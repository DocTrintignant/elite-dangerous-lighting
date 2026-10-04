#!/usr/bin/env python3
"""Named user-Mode extension of the current COVAS effect bridge.

Modes are the only public scripted-lighting command surface. The bridge resolves
a requested name against the immutable live ModeLibrary and installs its phases
into the generic temporary-scene authority used by the renderer compositor.
No fixed named-scene API exists here.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import lighting_covas_effect_bridge as previous
from lighting_authority import lighting_authority
from lighting_mode_catalog import mode_catalog
from lighting_mode_runtime import start_lighting_mode
from lighting_scene_authority import SceneAlreadyActiveError, scene_authority


base = previous.base
_MISSING = object()

_COVAS_RUNTIME_TTL_SECONDS = 25.0
MIN_CHROMAS_NEXT_RUNTIME_VERSION = "0.6.12"
MIN_COVASIFY_RUNTIME_VERSION = "4.2.0"
_COVAS_RUNTIME_LOCK = threading.Lock()
_covas_runtime_reports: dict[str, dict[str, object]] = {}
_COVAS_RUNTIME_FIELDS = frozenset(
    {
        "plugin_version",
        "covasify_bridge_enabled",
        "mode_action_registered",
        "media_listener_registered",
        "spotify_connected",
    }
)


def _record_covas_runtime(
    component: str,
    *,
    online: bool,
    fields: dict[str, object] | None = None,
    now: float | None = None,
) -> None:
    if component not in {"chromas_next", "covasify"}:
        raise ValueError("unsupported COVAS runtime component")
    timestamp = time.monotonic() if now is None else float(now)
    payload: dict[str, object] = {
        "online": bool(online),
        "updated_monotonic": timestamp,
    }
    if fields:
        for key, value in fields.items():
            if key in _COVAS_RUNTIME_FIELDS:
                payload[key] = value
    with _COVAS_RUNTIME_LOCK:
        _covas_runtime_reports[component] = payload


def clear_covas_runtime_reports() -> None:
    with _COVAS_RUNTIME_LOCK:
        _covas_runtime_reports.clear()


def covas_runtime_snapshot(*, now: float | None = None) -> dict[str, dict[str, object]]:
    current = time.monotonic() if now is None else float(now)
    with _COVAS_RUNTIME_LOCK:
        source = {name: dict(value) for name, value in _covas_runtime_reports.items()}

    result: dict[str, dict[str, object]] = {}
    for name in ("chromas_next", "covasify"):
        payload = source.get(name, {})
        updated = payload.get("updated_monotonic")
        try:
            age = max(0.0, current - float(updated))
        except (TypeError, ValueError):
            age = float("inf")
        fresh = bool(payload.get("online")) and age <= _COVAS_RUNTIME_TTL_SECONDS
        item = dict(payload)
        item["fresh"] = fresh
        item["age_seconds"] = age
        result[name] = item
    return result


def _version_tuple(value: object) -> tuple[int, ...] | None:
    text = str(value or "").strip()
    if not text:
        return None
    parts = text.split(".")
    if not parts or any(not part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def _runtime_version_supported(value: object, minimum_value: str) -> bool:
    observed = _version_tuple(value)
    minimum = _version_tuple(minimum_value)
    if observed is None or minimum is None:
        return False
    width = max(len(observed), len(minimum))
    observed += (0,) * (width - len(observed))
    minimum += (0,) * (width - len(minimum))
    return observed >= minimum


def chromas_runtime_version_supported(value: object) -> bool:
    return _runtime_version_supported(value, MIN_CHROMAS_NEXT_RUNTIME_VERSION)


def covasify_runtime_version_supported(value: object) -> bool:
    return _runtime_version_supported(value, MIN_COVASIFY_RUNTIME_VERSION)


@dataclass(frozen=True)
class CovasRuntimeAdmission:
    ready: bool
    state: str
    message: str
    requires_covasify: bool = False


def covas_runtime_admission(
    command: str,
    *,
    now: float | None = None,
) -> CovasRuntimeAdmission:
    """Fail-closed admission for state-changing COVAS commands.

    Chromas Next must always be fresh, new enough to implement the health
    contract and report the public Mode action. If the operator enabled the
    optional Covasify audio bridge, run_mode additionally requires a fresh
    Covasify runtime with its media listener registered. Spotify readiness is
    deliberately not an admission requirement: accepted media failures remain
    non-authoritative over lighting.
    """
    snapshot = covas_runtime_snapshot(now=now)
    chromas = snapshot["chromas_next"]

    if not chromas.get("fresh"):
        return CovasRuntimeAdmission(
            False,
            "chromas_runtime_missing",
            "Chromas Next runtime handshake is not fresh.",
        )

    version = chromas.get("plugin_version")
    if not chromas_runtime_version_supported(version):
        observed = str(version or "unknown")
        return CovasRuntimeAdmission(
            False,
            "chromas_version_unsupported",
            (
                f"Chromas Next runtime {observed} is not compatible with the "
                f"required handshake. Update to {MIN_CHROMAS_NEXT_RUNTIME_VERSION} or newer."
            ),
        )

    if not chromas.get("mode_action_registered"):
        return CovasRuntimeAdmission(
            False,
            "chromas_mode_action_missing",
            "Chromas Next did not report the required EDL Mode action.",
        )

    requires_covasify = bool(
        command == "run_mode" and chromas.get("covasify_bridge_enabled")
    )
    if not requires_covasify:
        return CovasRuntimeAdmission(
            True,
            "ready",
            "Chromas Next runtime handshake is healthy.",
        )

    covasify = snapshot["covasify"]
    if not covasify.get("fresh"):
        return CovasRuntimeAdmission(
            False,
            "covasify_runtime_missing",
            "Covasify audio bridge is enabled, but the Covasify runtime handshake is not fresh.",
            requires_covasify=True,
        )

    covasify_version = covasify.get("plugin_version")
    if not covasify_runtime_version_supported(covasify_version):
        observed = str(covasify_version or "unknown")
        return CovasRuntimeAdmission(
            False,
            "covasify_version_unsupported",
            (
                f"Covasify runtime {observed} is not compatible with the required "
                f"EDL integration. Update to {MIN_COVASIFY_RUNTIME_VERSION} or newer."
            ),
            requires_covasify=True,
        )

    if not covasify.get("media_listener_registered"):
        return CovasRuntimeAdmission(
            False,
            "covasify_listener_missing",
            "Covasify is present, but the EDL media listener was not reported.",
            requires_covasify=True,
        )

    return CovasRuntimeAdmission(
        True,
        "ready_with_covasify",
        "Chromas Next and Covasify runtime handshakes are healthy.",
        requires_covasify=True,
    )


class _ModeStatusHandler(previous._EffectStatusHandler):
    """Current effect handler plus generic user-authored Mode dispatch."""

    def _read_command_json(self):
        cached = getattr(self, "_edl_mode_cached_payload", _MISSING)
        if cached is not _MISSING:
            delattr(self, "_edl_mode_cached_payload")
            return cached
        cached = getattr(self, "_edl_effect_cached_payload", _MISSING)
        if cached is not _MISSING:
            delattr(self, "_edl_effect_cached_payload")
            return cached
        return super()._read_command_json()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self.path != "/api/v1/commands":
            super().do_POST()
            return

        payload = self._read_command_json()
        if payload is None:
            return

        command = payload.get("command")

        if command == "report_covas_runtime":
            self._report_covas_runtime(payload)
            return

        # Stand Down remains available as a safe release/recovery action even if
        # an external runtime disappears after taking authority. Every other
        # state-changing COVAS command fails closed until the automatic runtime
        # handshake is healthy.
        if command != "stand_down":
            admission = covas_runtime_admission(str(command or ""))
            if not admission.ready:
                self._write_json(
                    503,
                    {
                        "ok": False,
                        "api_version": base.API_VERSION,
                        "service": "elite-dangerous-lighting",
                        "error": "covas_runtime_unavailable",
                        "health_state": admission.state,
                        "message": admission.message,
                    },
                )
                return

        if command == "run_mode":
            self._run_mode(payload)
            return

        if command == "stand_down":
            scene_authority.stop()

        # The effect handler owns direct commands and Stand Down. Hand it the
        # exact already-decoded payload without a second socket read.
        self._edl_effect_cached_payload = payload
        super().do_POST()

    def _report_covas_runtime(self, payload: dict[str, object]) -> None:
        component = payload.get("component")
        online = payload.get("online")
        if component not in {"chromas_next", "covasify"} or not isinstance(online, bool):
            self._invalid(
                "report_covas_runtime requires component chromas_next/covasify and boolean online."
            )
            return

        fields = {
            key: payload[key]
            for key in _COVAS_RUNTIME_FIELDS
            if key in payload
        }
        _record_covas_runtime(str(component), online=online, fields=fields)
        self._write_json(
            200,
            {
                "ok": True,
                "api_version": base.API_VERSION,
                "service": "elite-dangerous-lighting",
                "command": "report_covas_runtime",
                "component": component,
            },
        )

    def _run_mode(self, payload: dict[str, object]) -> None:
        mode_value = payload.get("mode")
        if not isinstance(mode_value, str) or not mode_value.strip():
            self._invalid("run_mode requires a non-empty string mode field.")
            return

        active_targets = lighting_authority.active_targets
        if not active_targets:
            self._write_json(
                409,
                {
                    "ok": False,
                    "api_version": base.API_VERSION,
                    "service": "elite-dangerous-lighting",
                    "error": "lighting_not_running",
                    "message": "Start live lighting in EDL before starting a Mode.",
                },
            )
            return

        try:
            mode = mode_catalog.mode(mode_value)
        except ValueError as exc:
            self._invalid(str(exc))
            return
        if mode is None:
            self._write_json(
                400,
                {
                    "ok": False,
                    "api_version": base.API_VERSION,
                    "service": "elite-dangerous-lighting",
                    "error": "mode_not_found",
                    "message": f"No applied live Mode named {mode_value!r}.",
                    "available_modes": list(mode_catalog.names()),
                },
            )
            return

        try:
            run = start_lighting_mode(mode)
        except SceneAlreadyActiveError as exc:
            self._write_json(
                409,
                {
                    "ok": False,
                    "api_version": base.API_VERSION,
                    "service": "elite-dangerous-lighting",
                    "error": "mode_active",
                    "message": (
                        f"{exc.active_scene} is already running. "
                        "Stand Down or let it finish before starting another Mode."
                    ),
                    "active_mode": exc.active_scene,
                },
            )
            return
        except RuntimeError as exc:
            self._write_json(
                409,
                {
                    "ok": False,
                    "api_version": base.API_VERSION,
                    "service": "elite-dangerous-lighting",
                    "error": "target_not_active",
                    "message": str(exc),
                    "active_control_targets": list(lighting_authority.active_targets),
                },
            )
            return
        except ValueError as exc:
            self._invalid(str(exc))
            return

        response = {
            "ok": True,
            "api_version": base.API_VERSION,
            "service": "elite-dangerous-lighting",
            "command": "run_mode",
            "mode": run.name,
            "display_name": mode.name,
            "targets": list(run.targets),
            "duration_seconds": run.duration_seconds,
            "authority": "temporary_mode",
        }
        if mode.media_cue is not None:
            response["media_cue"] = mode.media_cue
        self._record_command_diagnostic(response)
        self._write_json(200, response)


class CovasModeBridge(previous.CovasEffectBridge):
    """Current bridge lifecycle plus generic data-driven Mode dispatch."""

    def start(self) -> None:
        if self.is_running:
            return
        # A newly started listener is a new observation window. Do not carry a
        # still-young heartbeat across a bridge disable/re-enable cycle.
        clear_covas_runtime_reports()
        server = self._configure_server(
            base.ThreadingHTTPServer((self.host, self.port), _ModeStatusHandler)
        )
        thread = threading.Thread(
            target=server.serve_forever,
            name="edl-covas-mode-bridge",
            daemon=True,
        )
        self._server = server
        self._thread = thread
        thread.start()
