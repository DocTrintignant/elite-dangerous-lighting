#!/usr/bin/env python3
"""One persistent multi-device Chroma session for live applied profiles.

This is the same accepted REST lifecycle used by the UI preview, generalized to
Keyboard + Mouse + ChromaLink in one session. It does not introduce a second
transport model.
"""

from __future__ import annotations

import threading

from lighting_chroma_preview import (
    BASE_URI,
    HEARTBEAT_SECONDS,
    _normalize_session_uri,
    _request_json,
    _require_success,
    _rgb_payload_for_target,
)
from lighting_rules import rgb_to_chroma_colorref

SUPPORTED_TARGETS = ("KEYBOARD", "MOUSE", "CHROMALINK")


class LiveChromaSession:
    """One persistent Chroma REST session shared by all active live targets."""

    def __init__(self, targets: tuple[str, ...]) -> None:
        normalized = tuple(dict.fromkeys(target.upper() for target in targets))
        if not normalized:
            raise ValueError("live Chroma session requires at least one target")
        unsupported = [target for target in normalized if target not in SUPPORTED_TARGETS]
        if unsupported:
            raise ValueError(f"unsupported live Chroma target(s): {unsupported}")
        self.targets = normalized
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
            "title": "Elite Dangerous Lighting",
            "description": "Live applied Elite Dangerous Lighting profile",
            "author": {
                "name": "Elite Dangerous Lighting",
                "contact": "local application",
            },
            "device_supported": [target.lower() for target in self.targets],
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
            name="chroma-live-heartbeat",
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
                _require_success("live heartbeat", response)
            except Exception as exc:
                self._heartbeat_error = str(exc)
                return

    def _require_target(self, target: str) -> str:
        normalized = target.upper()
        if normalized not in self.targets:
            raise ValueError(f"target {normalized!r} is not part of this live session")
        if self.uri is None:
            raise RuntimeError("No active live Chroma session")
        if self._heartbeat_error:
            raise RuntimeError(f"Chroma live heartbeat failed: {self._heartbeat_error}")
        return normalized

    def render_rgb(self, target: str, rgb: tuple[int, int, int]) -> None:
        target = self._require_target(target)
        payload = _rgb_payload_for_target(target, rgb)
        response = _request_json("PUT", f"{self.uri}/{target.lower()}", payload)
        _require_success(f"{target.lower()} live render", response)

    def render_custom_matrix(
        self,
        target: str,
        matrix: tuple[tuple[tuple[int, int, int], ...], ...],
    ) -> None:
        """Render one generic Chroma address matrix on the accepted session.

        Keyboard uses Razer's proven 6x22 CHROMA_CUSTOM payload. Mouse uses the
        documented 9x7 CHROMA_CUSTOM2 virtual grid. The caller owns geometry and
        validates the matrix dimensions against EDL's generic surface data.
        """
        target = self._require_target(target)
        if target == "KEYBOARD":
            effect = "CHROMA_CUSTOM"
        elif target == "MOUSE":
            effect = "CHROMA_CUSTOM2"
        else:
            raise ValueError("custom matrix rendering is supported for Keyboard or Mouse")
        payload = {
            "effect": effect,
            "param": [
                [rgb_to_chroma_colorref(rgb) for rgb in row]
                for row in matrix
            ],
        }
        response = _request_json("PUT", f"{self.uri}/{target.lower()}", payload)
        _require_success(f"{target.lower()} custom live render", response)

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
            _require_success("live uninitialize", response)
        finally:
            self.uri = None
            self._heartbeat_thread = None
            self._heartbeat_error = None
