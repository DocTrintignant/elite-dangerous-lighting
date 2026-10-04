#!/usr/bin/env python3
"""Loopback-only COVAS:NEXT status/catalog bridge for Elite Dangerous Lighting.

This base layer owns the bounded loopback transport, live ownership projection,
plugin validation, and read-only status/catalog surface. State-changing lighting
commands are implemented only by the effect/Mode bridge extensions.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer as _StdlibThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import threading
import time
from typing import Any, Mapping

from lighting_authority import (
    lighting_authority,
    parse_static_colour,
    resolve_direct_static_target,
)
from lighting_covas_catalog import build_lighting_catalog
from lighting_chroma_surfaces import chroma_surface
from lighting_chroma_zone_config import load_chroma_zone_configuration
from lighting_chromalink_cells import CHROMALINK_CELL_TARGETS, is_chromalink_cell_target
from lighting_govee_config import load_govee_configuration, parse_all_target as parse_govee_all_target, parse_zone_target as parse_govee_zone_target
from lighting_openrgb_targets import (
    fallback_target_label as openrgb_fallback_target_label,
    openrgb_target_device_label,
    parse_openrgb_target,
)


API_VERSION = 1
BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 43871
PLUGIN_FOLDER_NAME = "ChromasNext"
PLUGIN_GUID = "6be9c771-15ca-4b74-a9ab-7cc36db8e3a1"
MAX_COMMAND_BYTES = 16 * 1024
REQUEST_TIMEOUT_SECONDS = 1.5
MAX_HANDLER_THREADS = 8
MAX_COMMANDS_PER_SECOND = 20
LOCAL_HTTP_HOSTS = frozenset({"127.0.0.1", "localhost"})
ALL_TARGET_ALIASES = frozenset(
    {
        "all",
        "all lights",
        "all lighting",
        "all edl lights",
        "all edl lighting",
        "everything",
    }
)



class ThreadingHTTPServer(_StdlibThreadingHTTPServer):
    """Bounded loopback HTTP server shared by every bridge capability layer."""

    # Wait for the bounded handler set on server_close(). Each connection has a
    # finite socket deadline, so bridge disable cannot leave detached command
    # handlers running after stop() returns.
    daemon_threads = False
    request_queue_size = MAX_HANDLER_THREADS

    def __init__(
        self,
        server_address,
        RequestHandlerClass,
        bind_and_activate: bool = True,
        *,
        max_handlers: int = MAX_HANDLER_THREADS,
        max_commands_per_second: int = MAX_COMMANDS_PER_SECOND,
    ) -> None:
        if isinstance(max_handlers, bool) or not isinstance(max_handlers, int) or max_handlers <= 0:
            raise ValueError("max_handlers must be a positive integer")
        if (
            isinstance(max_commands_per_second, bool)
            or not isinstance(max_commands_per_second, int)
            or max_commands_per_second <= 0
        ):
            raise ValueError("max_commands_per_second must be a positive integer")
        self.accepting_requests = True
        self._handler_slots = threading.BoundedSemaphore(max_handlers)
        self._handler_state_lock = threading.Lock()
        self._active_handler_count = 0
        self._max_handlers = max_handlers
        self._command_rate_lock = threading.Lock()
        self._command_times: deque[float] = deque()
        self._max_commands_per_second = max_commands_per_second
        super().__init__(server_address, RequestHandlerClass, bind_and_activate)

    @property
    def active_handler_count(self) -> int:
        with self._handler_state_lock:
            return self._active_handler_count

    def admit_command(self, *, now: float | None = None) -> bool:
        timestamp = time.monotonic() if now is None else float(now)
        cutoff = timestamp - 1.0
        with self._command_rate_lock:
            while self._command_times and self._command_times[0] <= cutoff:
                self._command_times.popleft()
            if len(self._command_times) >= self._max_commands_per_second:
                return False
            self._command_times.append(timestamp)
            return True

    def _busy_response(self, request) -> None:
        body = json.dumps(
            {
                "ok": False,
                "api_version": API_VERSION,
                "service": "elite-dangerous-lighting",
                "error": "server_busy",
                "message": "The local EDL bridge is handling the maximum number of concurrent requests.",
            },
            separators=(",", ":"),
        ).encode("utf-8")
        response = (
            b"HTTP/1.1 503 Service Unavailable\r\n"
            b"Content-Type: application/json; charset=utf-8\r\n"
            + f"Content-Length: {len(body)}\r\n".encode("ascii")
            + b"Cache-Control: no-store\r\n"
            + b"Connection: close\r\n\r\n"
            + body
        )
        try:
            request.sendall(response)
        except OSError:
            pass

    def process_request(self, request, client_address) -> None:
        if not self.accepting_requests or not self._handler_slots.acquire(blocking=False):
            self._busy_response(request)
            self.shutdown_request(request)
            return
        with self._handler_state_lock:
            self._active_handler_count += 1
        try:
            super().process_request(request, client_address)
        except BaseException:
            with self._handler_state_lock:
                self._active_handler_count -= 1
            self._handler_slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._handler_state_lock:
                self._active_handler_count -= 1
            self._handler_slots.release()


@dataclass(frozen=True)
class PluginInstallCheck:
    path: Path
    valid: bool
    message: str
    version: str | None = None
    entrypoint: str | None = None


def _normalize_target_words(value: str) -> str:
    return " ".join(
        value.strip().lower().replace("_", " ").replace("-", " ").replace("·", " ").split()
    )


def _is_all_target_request(value: str) -> bool:
    return isinstance(value, str) and _normalize_target_words(value) in ALL_TARGET_ALIASES


def _catalog_with_runtime_state(
    catalog: Mapping[str, Any],
    *,
    availability: Any | None = None,
) -> dict[str, Any]:
    """Project the current live control topology from active renderer ownership.

    The public catalog intentionally contains no configured-but-unowned devices.
    If EDL says a device is controllable now, it appears here; otherwise it does
    not. This keeps COVAS aligned with the same live ownership registry used by
    the renderers.
    """
    active = tuple(lighting_authority.active_targets)
    overridden: set[str] = set()
    result = dict(catalog)
    result["lighting_control_available"] = bool(active)
    result["active_control_targets"] = list(active)
    result["direct_override_targets"] = sorted(overridden)

    chroma_zones = (
        getattr(availability, "chroma_zone_configuration", None)
        if availability is not None
        else None
    )
    if chroma_zones is None:
        chroma_zones = load_chroma_zone_configuration()

    govee = (
        getattr(availability, "govee_configuration", None)
        if availability is not None
        else None
    )
    if govee is None:
        govee = load_govee_configuration()
    govee_map = govee.target_map()

    devices: dict[str, dict[str, Any]] = {}

    def ensure_device(key: str, label: str, provider: str) -> dict[str, Any]:
        return devices.setdefault(
            key,
            {
                "id": key,
                "label": label,
                "provider": provider,
                "kind": "live_device",
                "enabled": True,
                "active": True,
                "targets": [],
            },
        )

    for target in active:
        target_label = target
        target_type = "whole_device"
        device_key = target
        device_label = target
        provider = "edl"

        if target in {"KEYBOARD", "MOUSE", "CHROMALINK"}:
            surface = chroma_surface(target)
            device_key = f"chroma:{target.lower()}"
            device_label = surface.display_name
            if availability is not None:
                try:
                    status = availability.status(target)
                except Exception:
                    status = None
                live_label = str(getattr(status, "label", "") or "").strip()
                if live_label:
                    device_label = live_label
            provider = "razer_chroma"
            target_label = f"Whole {device_label}"
        elif target.startswith("CHROMA_ZONE::"):
            surface = target.split("::", 2)[1]
            device_key = f"chroma:{surface.lower()}"
            device_label = chroma_surface(surface).display_name
            if availability is not None:
                try:
                    surface_status = availability.status(surface)
                except Exception:
                    surface_status = None
                live_label = str(getattr(surface_status, "label", "") or "").strip()
                if live_label:
                    device_label = live_label
            provider = "razer_chroma"
            target_type = "zone"
            zone = next(
                (value for value in chroma_zones.for_surface(surface) if value.target == target),
                None,
            )
            target_label = zone.name if zone is not None else target
        elif is_chromalink_cell_target(target):
            device_key = "chroma:chromalink"
            device_label = chroma_surface("CHROMALINK").display_name
            if availability is not None:
                try:
                    status = availability.status("CHROMALINK")
                except Exception:
                    status = None
                live_label = str(getattr(status, "label", "") or "").strip()
                if live_label:
                    device_label = live_label
            provider = "razer_chroma"
            target_type = "zone"
            target_label = target.split("::", 1)[1]
        else:
            govee_entry = govee_map.get(target)
            if govee_entry is not None:
                device, zone = govee_entry
                device_key = f"govee:{device.device_id}"
                device_label = device.name
                provider = "govee_native"
                target_type = "whole_device" if zone is None else "zone"
                target_label = "Whole device" if zone is None else zone.name
            else:
                spec = parse_openrgb_target(target)
                if spec is not None:
                    device_key = f"openrgb:{spec.physical_identity}"
                    device_label = openrgb_target_device_label(target)
                    provider = "openrgb"
                    target_type = "whole_device" if spec.whole_device else "zone"
                    target_label = openrgb_fallback_target_label(target)

        device = ensure_device(device_key, device_label, provider)
        device["targets"].append(
            {
                "id": target,
                "label": target_label,
                "type": target_type,
                "active": True,
                "direct_override": target in overridden,
            }
        )

    result["devices"] = list(devices.values())
    return result


def resolve_control_target(
    value: str,
    *,
    catalog: Mapping[str, Any] | None = None,
) -> str:
    """Resolve a spoken/friendly EDL target into one canonical target ID.

    Exact canonical IDs and accepted Chroma aliases resolve first. Otherwise the
    current EDL catalog supplies device names and zone names. Ambiguous friendly
    names are rejected rather than guessed. If exactly one enabled native Govee
    device exists, generic phrases such as 'Govee strip' resolve to its whole
    device target.
    """
    try:
        return resolve_direct_static_target(value)
    except ValueError:
        pass

    if not isinstance(value, str) or not value.strip():
        raise ValueError("target must be a non-empty string")
    topology = (
        _catalog_with_runtime_state(build_lighting_catalog())
        if catalog is None
        else catalog
    )
    devices = topology.get("devices")
    if not isinstance(devices, list):
        raise ValueError("EDL lighting catalog is malformed")

    aliases: dict[str, set[str]] = {}
    enabled_govee_whole_targets: list[str] = []

    def add(alias: str, target: str) -> None:
        key = _normalize_target_words(alias)
        if key:
            aliases.setdefault(key, set()).add(target)

    for device in devices:
        if not isinstance(device, dict):
            continue
        if device.get("enabled") is False:
            continue
        device_label = str(device.get("label") or "").strip()
        provider = str(device.get("provider") or "")
        targets = device.get("targets")
        if not isinstance(targets, list):
            continue

        whole_target: str | None = None
        for target in targets:
            if not isinstance(target, dict):
                continue
            target_id = target.get("id")
            if not isinstance(target_id, str) or not target_id.strip():
                continue
            target_id = target_id.strip()
            target_label = str(target.get("label") or target_id).strip()
            add(target_id, target_id)
            add(target_label, target_id)
            if device_label:
                add(f"{device_label} {target_label}", target_id)
            if target.get("type") == "whole_device":
                whole_target = target_id

        if whole_target is not None and device_label:
            add(device_label, whole_target)
            add(f"whole {device_label}", whole_target)
        if provider == "govee_native" and whole_target is not None:
            enabled_govee_whole_targets.append(whole_target)

    if len(enabled_govee_whole_targets) == 1:
        govee_target = enabled_govee_whole_targets[0]
        for alias in (
            "govee",
            "govee strip",
            "govee stripe",
            "govee light",
            "govee light strip",
            "light strip",
            "strip",
            "stripe",
        ):
            add(alias, govee_target)

    matches = aliases.get(_normalize_target_words(value), set())
    if not matches:
        raise ValueError(f"No configured EDL lighting target matches {value!r}.")
    if len(matches) > 1:
        raise ValueError(
            f"Lighting target {value!r} is ambiguous; use the device or zone name from the EDL catalog."
        )
    return next(iter(matches))


def default_plugin_install_path(
    environment: Mapping[str, str] | None = None,
) -> Path:
    env = os.environ if environment is None else environment
    appdata = env.get("APPDATA")
    if appdata:
        return Path(appdata) / "com.covas-next.ui" / "plugins" / PLUGIN_FOLDER_NAME
    return Path.home() / "AppData" / "Roaming" / "com.covas-next.ui" / "plugins" / PLUGIN_FOLDER_NAME


def resolve_plugin_install_path(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    if (candidate / "manifest.json").is_file():
        return candidate
    nested = candidate / PLUGIN_FOLDER_NAME
    if (nested / "manifest.json").is_file():
        return nested
    return candidate


def validate_plugin_install(path: str | Path) -> PluginInstallCheck:
    candidate = resolve_plugin_install_path(path)
    manifest_path = candidate / "manifest.json"
    if not candidate.is_dir():
        return PluginInstallCheck(candidate, False, "Plugin folder does not exist.")
    if not manifest_path.is_file():
        return PluginInstallCheck(candidate, False, "manifest.json was not found.")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return PluginInstallCheck(candidate, False, f"manifest.json could not be read: {exc}")

    if not isinstance(manifest, dict):
        return PluginInstallCheck(candidate, False, "manifest.json must contain an object.")

    if manifest.get("guid") != PLUGIN_GUID:
        return PluginInstallCheck(candidate, False, "This folder is not the Chromas Next COVAS:NEXT plugin.")

    entrypoint = manifest.get("entrypoint")
    if not isinstance(entrypoint, str) or not entrypoint.lower().endswith(".py"):
        return PluginInstallCheck(candidate, False, "The plugin manifest has no valid Python entrypoint.")
    if not (candidate / entrypoint).is_file():
        return PluginInstallCheck(
            candidate,
            False,
            f"Plugin entrypoint {entrypoint!r} was not found.",
            entrypoint=entrypoint,
        )

    version = manifest.get("version")
    version_text = version if isinstance(version, str) else None
    return PluginInstallCheck(
        candidate,
        True,
        "Chromas Next COVAS:NEXT plugin found.",
        version=version_text,
        entrypoint=entrypoint,
    )


class _StatusHandler(BaseHTTPRequestHandler):
    server_version = "EDLCovasBridge/1"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(REQUEST_TIMEOUT_SECONDS)

    def _bridge_accepting(self) -> bool:
        return bool(getattr(self.server, "accepting_requests", True))

    def _request_host_is_local(self) -> bool:
        raw = self.headers.get("Host", "").strip().lower()
        if not raw:
            return False
        host = raw.split(":", 1)[0]
        return host in LOCAL_HTTP_HOSTS

    def parse_request(self) -> bool:
        if not super().parse_request():
            return False

        if not self._bridge_accepting():
            self._write_json(
                503,
                {
                    "ok": False,
                    "api_version": API_VERSION,
                    "error": "bridge_stopping",
                },
            )
            return False

        if not self._request_host_is_local():
            self._write_json(
                403,
                {
                    "ok": False,
                    "api_version": API_VERSION,
                    "error": "invalid_host",
                    "message": "The EDL bridge accepts only local loopback HTTP requests.",
                },
            )
            return False

        if self.headers.get("Origin"):
            self._write_json(
                403,
                {
                    "ok": False,
                    "api_version": API_VERSION,
                    "error": "origin_not_allowed",
                    "message": "Browser-origin requests are not part of the EDL bridge contract.",
                },
            )
            return False

        if self.command == "POST":
            if self.headers.get_content_type() != "application/json":
                self._write_json(
                    415,
                    {
                        "ok": False,
                        "api_version": API_VERSION,
                        "error": "unsupported_media_type",
                        "message": "POST requests must use application/json.",
                    },
                )
                return False
            admit = getattr(self.server, "admit_command", None)
            if callable(admit) and not admit():
                self._write_json(
                    429,
                    {
                        "ok": False,
                        "api_version": API_VERSION,
                        "error": "rate_limited",
                        "message": "Too many local bridge commands were submitted in one second.",
                    },
                )
                return False

        return True

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self.path == "/api/v1/status":
            active_targets = lighting_authority.active_targets
            self._write_json(
                200,
                {
                    "ok": True,
                    "api_version": API_VERSION,
                    "service": "elite-dangerous-lighting",
                    "bridge": "ready",
                    "capabilities": [
                        "status",
                        "catalog",
                    ],
                    "lighting_control_available": bool(active_targets),
                    "active_control_targets": list(active_targets),
                    "direct_override_targets": [],
                },
            )
            return

        if self.path == "/api/v1/catalog":
            try:
                catalog = _catalog_with_runtime_state(
                    build_lighting_catalog(),
                    availability=getattr(self.server, "runtime_availability", None),
                )
            except (OSError, ValueError) as exc:
                self._write_json(
                    500,
                    {
                        "ok": False,
                        "api_version": API_VERSION,
                        "service": "elite-dangerous-lighting",
                        "error": "catalog_unavailable",
                        "message": str(exc),
                    },
                )
                return
            self._write_json(
                200,
                {
                    "ok": True,
                    "api_version": API_VERSION,
                    "service": "elite-dangerous-lighting",
                    **catalog,
                },
            )
            return

        self._write_json(
            404,
            {
                "ok": False,
                "api_version": API_VERSION,
                "error": "not_found",
            },
        )

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self.path != "/api/v1/commands":
            self._write_json(
                405,
                {
                    "ok": False,
                    "api_version": API_VERSION,
                    "error": "method_not_allowed",
                },
            )
            return

        payload = self._read_command_json()
        if payload is None:
            return
        command = payload.get("command")
        self._write_json(
            400,
            {
                "ok": False,
                "api_version": API_VERSION,
                "service": "elite-dangerous-lighting",
                "error": "unsupported_command",
                "message": "No state-changing commands are implemented by the base status bridge.",
            },
        )


    def _record_command_diagnostic(self, response: Mapping[str, object]) -> None:
        """Optional accepted-command diagnostic hook for bridge extensions."""
        return

    def _read_command_json(self) -> dict[str, object] | None:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_COMMAND_BYTES:
            self._write_json(
                413,
                {
                    "ok": False,
                    "api_version": API_VERSION,
                    "error": "request_too_large",
                },
            )
            return None
        try:
            raw = self.rfile.read(length)
        except (socket.timeout, TimeoutError):
            self._write_json(
                408,
                {
                    "ok": False,
                    "api_version": API_VERSION,
                    "error": "request_timeout",
                    "message": "The local bridge command body was not received before the request deadline.",
                },
            )
            return None
        if not self._bridge_accepting():
            self._write_json(
                503,
                {
                    "ok": False,
                    "api_version": API_VERSION,
                    "error": "bridge_stopping",
                },
            )
            return None
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._write_json(
                400,
                {
                    "ok": False,
                    "api_version": API_VERSION,
                    "error": "invalid_json",
                },
            )
            return None
        if not isinstance(payload, dict):
            self._write_json(
                400,
                {
                    "ok": False,
                    "api_version": API_VERSION,
                    "error": "invalid_request",
                    "message": "Command payload must be a JSON object.",
                },
            )
            return None
        return payload

    def log_message(self, format: str, *args: object) -> None:
        return

    def _write_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)


class CovasStatusBridge:
    """Own the loopback bridge independently from the lighting transport lifecycle."""

    def __init__(self, host: str = BRIDGE_HOST, port: int = BRIDGE_PORT) -> None:
        if host != BRIDGE_HOST:
            raise ValueError("The COVAS bridge must bind to 127.0.0.1.")
        self.host = host
        self.port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._runtime_availability: Any | None = None

    def set_runtime_availability(self, availability: Any | None) -> None:
        """Publish the immutable live device snapshot used by EDL's UI/session."""
        self._runtime_availability = availability
        if self._server is not None:
            self._server.runtime_availability = availability

    def _configure_server(self, server: ThreadingHTTPServer) -> ThreadingHTTPServer:
        server.runtime_availability = self._runtime_availability
        return server

    @property
    def is_running(self) -> bool:
        return self._server is not None and self._thread is not None and self._thread.is_alive()

    @property
    def bound_port(self) -> int:
        if self._server is not None:
            return int(self._server.server_address[1])
        return self.port

    @property
    def endpoint(self) -> str:
        return f"http://{self.host}:{self.bound_port}/api/v1/status"

    @property
    def catalog_endpoint(self) -> str:
        return f"http://{self.host}:{self.bound_port}/api/v1/catalog"

    @property
    def commands_endpoint(self) -> str:
        return f"http://{self.host}:{self.bound_port}/api/v1/commands"

    def start(self) -> None:
        if self.is_running:
            return
        server = self._configure_server(
            ThreadingHTTPServer((self.host, self.port), _StatusHandler)
        )
        thread = threading.Thread(target=server.serve_forever, name="edl-covas-status-bridge", daemon=True)
        self._server = server
        self._thread = thread
        thread.start()

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is None:
            return
        server.accepting_requests = False
        server.shutdown()
        server.server_close()
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
