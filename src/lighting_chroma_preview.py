#!/usr/bin/env python3
"""Bounded hardware preview for one current lighting intent.

This module is deliberately smaller than the full application runtime. It owns
only the temporary UI preview boundary:

    one already-built LightingIntent
        -> existing hardware-neutral runtime sampling
        -> one selected Chroma device
        -> continuous desired-state rendering on one persistent REST session

The accepted M1 transport lessons are preserved:
- one persistent Chroma REST session
- continuous desired-state rendering at the selected application rate
- 30 FPS remains the default/reference cadence
- no fixed readiness delay
- no BLACK priming frame
- no heartbeat-success readiness gate
- DELETE on stop returns authority to Synapse
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from lighting_intent import LightingIntent
from lighting_render_rate import DEFAULT_RENDER_FPS, frame_period_seconds
from lighting_rules import rgb_to_chroma_colorref
from lighting_runtime import RuntimeIntentSample, render_runtime_tick

BASE_URI = "http://localhost:54235/razer/chromasdk"
HTTP_TIMEOUT_SECONDS = 2.0
MAX_CHROMA_RESPONSE_BYTES = 64 * 1024
CHROMA_SESSION_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
CHROMA_SESSION_PATH = "/chromasdk"
HEARTBEAT_SECONDS = 1.0
# Backward-compatible constant retained for probes that intentionally use the
# historical fixed 30 FPS acceptance fixture.  The desktop application preview
# itself reads the live application-wide render-rate setting below.
RENDER_FPS = DEFAULT_RENDER_FPS
SUPPORTED_PREVIEW_TARGETS = ("KEYBOARD", "MOUSE", "CHROMALINK")
CHROMALINK_CELL_COUNT = 5


def _read_bounded_response(stream, *, label: str) -> bytes:
    raw = stream.read(MAX_CHROMA_RESPONSE_BYTES + 1)
    if len(raw) > MAX_CHROMA_RESPONSE_BYTES:
        raise RuntimeError(
            f"{label} exceeded the {MAX_CHROMA_RESPONSE_BYTES}-byte response limit"
        )
    return raw


def _normalize_session_uri(value: object) -> str:
    """Accept only the local per-session URI contract returned by Chroma REST."""
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"Chroma SDK returned an invalid session URI: {value!r}")
    candidate = value.strip().rstrip("/")
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError(
            f"Chroma SDK returned an invalid session URI: {value!r}"
        ) from exc

    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() != "http"
        or host not in CHROMA_SESSION_HOSTS
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != CHROMA_SESSION_PATH
    ):
        raise RuntimeError(
            "Chroma SDK returned a session URI outside the accepted local "
            f"REST authority/path: {value!r}"
        )
    return candidate


def _request_json(method: str, url: str, payload: object | None = None) -> dict:
    body = None
    headers = {"Accept": "application/json"}

    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            raw = _read_bounded_response(
                response,
                label="Chroma SDK response",
            ).decode("utf-8").strip()
    except urllib.error.HTTPError as exc:
        detail_bytes = _read_bounded_response(
            exc,
            label="Chroma SDK error response",
        )
        detail = detail_bytes.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"HTTP {exc.code} from Chroma SDK at {url}"
            + (f": {detail}" if detail else "")
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            "Cannot reach the Razer Chroma SDK REST service at "
            f"{BASE_URI}. Is Synapse running with Chroma Apps enabled?"
        ) from exc

    if not raw:
        return {}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Non-JSON response from Chroma SDK: {raw!r}") from exc

    if not isinstance(parsed, dict):
        raise RuntimeError(f"Unexpected Chroma SDK response: {parsed!r}")
    return parsed


def _require_success(label: str, response: dict) -> None:
    result = response.get("result")
    if result is None:
        return
    if result != 0:
        raise RuntimeError(f"{label} failed: Chroma SDK result={result}, response={response}")


def _device_name(target: str) -> str:
    if target not in SUPPORTED_PREVIEW_TARGETS:
        raise ValueError(
            f"unsupported preview target {target!r}; expected one of {SUPPORTED_PREVIEW_TARGETS}"
        )
    return target.lower()


def _rgb_payload_for_target(target: str, rgb: tuple[int, int, int]) -> dict:
    """Adapt one uniform engine RGB value to the proven Chroma target surface."""
    color = rgb_to_chroma_colorref(rgb)
    if target.upper() == "CHROMALINK":
        # The downstream ChromaLink path was physically accepted with exactly
        # five logical CL1..CL5 cells driven through CHROMA_CUSTOM. Keep that
        # proven mechanism rather than assuming CHROMA_STATIC is equivalent.
        return {
            "effect": "CHROMA_CUSTOM",
            "param": [color] * CHROMALINK_CELL_COUNT,
        }
    return {
        "effect": "CHROMA_STATIC",
        "param": {"color": color},
    }


class PreviewChromaSession:
    """One persistent Chroma REST session scoped to one preview device."""

    def __init__(self, target: str) -> None:
        self.target = target
        self.device = _device_name(target)
        self.uri: str | None = None
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._heartbeat_error: str | None = None

    @property
    def active(self) -> bool:
        return self.uri is not None

    def start(self) -> None:
        if self.active:
            return

        app_info = {
            "title": "Elite Dangerous Lighting Preview",
            "description": "Temporary effect preview from Elite Dangerous Lighting",
            "author": {
                "name": "Elite Dangerous Lighting",
                "contact": "local application",
            },
            "device_supported": [self.device],
            "category": "application",
        }

        response = _request_json("POST", BASE_URI, app_info)
        try:
            uri = _normalize_session_uri(response.get("uri"))
        except RuntimeError as exc:
            raise RuntimeError(
                f"Chroma SDK initialization failed: {response}"
            ) from exc

        self.uri = uri
        self._heartbeat_error = None
        self._heartbeat_stop.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="chroma-preview-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def _heartbeat_loop(self) -> None:
        while not self._heartbeat_stop.wait(HEARTBEAT_SECONDS):
            uri = self.uri
            if uri is None:
                return
            try:
                response = _request_json("PUT", f"{uri}/heartbeat")
                _require_success("preview heartbeat", response)
            except Exception as exc:
                self._heartbeat_error = str(exc)
                return

    def render_rgb(self, rgb: tuple[int, int, int]) -> None:
        if self.uri is None:
            raise RuntimeError("No active Chroma preview session.")
        if self._heartbeat_error:
            raise RuntimeError(f"Chroma preview heartbeat failed: {self._heartbeat_error}")

        payload = _rgb_payload_for_target(self.target, rgb)
        response = _request_json("PUT", f"{self.uri}/{self.device}", payload)
        _require_success(f"{self.device} preview render", response)

    def release(self) -> None:
        uri = self.uri
        if uri is None:
            return

        self._heartbeat_stop.set()
        thread = self._heartbeat_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.5)

        try:
            response = _request_json("DELETE", uri)
            _require_success("preview uninitialize", response)
        finally:
            self.uri = None
            self._heartbeat_thread = None
            self._heartbeat_error = None


def _runtime_sample(intent: LightingIntent, elapsed_seconds: float) -> RuntimeIntentSample:
    if intent.effect == "STATIC":
        return RuntimeIntentSample(intent)
    if intent.effect in {"FLASH", "PULSE", "BREATH"}:
        return RuntimeIntentSample(intent, elapsed_seconds=elapsed_seconds)
    raise ValueError(f"effect {intent.effect!r} is not wired for UI hardware preview")


def run_effect_preview(
    intent: LightingIntent,
    target: str,
    stop_event: threading.Event,
) -> None:
    """Continuously preview one immutable intent snapshot until stopped."""
    if not isinstance(intent, LightingIntent):
        raise ValueError("preview intent must be a LightingIntent")
    if not isinstance(stop_event, threading.Event):
        raise ValueError("stop_event must be threading.Event")
    if intent.target != target:
        raise ValueError(
            f"preview intent target {intent.target!r} does not match selected target {target!r}"
        )

    # Fail before acquiring Chroma when a dynamic effect lacks real timing.
    render_runtime_tick((_runtime_sample(intent, 0.0),))

    session = PreviewChromaSession(target)

    try:
        session.start()
        started = time.perf_counter()
        next_frame = started

        while not stop_event.is_set():
            now = time.perf_counter()
            elapsed = now - started
            frame = render_runtime_tick((_runtime_sample(intent, elapsed),))
            session.render_rgb(frame[target])

            # Read the shared application setting every frame.  A user can change
            # the update rate while previewing without recreating the Chroma session.
            next_frame += frame_period_seconds()
            remaining = next_frame - time.perf_counter()
            if remaining > 0:
                stop_event.wait(remaining)
            else:
                next_frame = time.perf_counter()
    finally:
        if session.active:
            session.release()
