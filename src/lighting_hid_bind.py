#!/usr/bin/env python3
"""Persistent operator binding capture for cockpit buttons and axes.

The production UI needs a different interaction from a one-shot HID probe:
open one Raw Input session, establish the current physical baseline, then watch
what the operator deliberately moves.  Keeping that session alive is important
for latching cockpit switches because an input that was already ON before the
operator armed detection must not be mistaken for a new press.

This module reuses the already accepted Windows Raw Input/HidP parser.  It adds
no rule semantics and no alternate HID transport.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from threading import Event

import lighting_hid_capture as raw
import lighting_hid_state as axis_raw


@dataclass
class ButtonTransitionTracker:
    """Pure transition tracker used by the Raw Input capture and unit tests."""

    previous: dict[int, set[int]] = field(default_factory=dict)

    def observe(self, handle: int, active: set[int], *, armed: bool) -> tuple[int, ...]:
        current = set(active)
        prior = self.previous.get(handle)
        self.previous[handle] = current
        if prior is None or not armed:
            return ()
        return tuple(sorted(current - prior))


def _selected_devices(preferred_device_id: str | None):
    device_map = raw._current_device_map()
    if preferred_device_id is not None:
        device_map = {
            handle: device
            for handle, device in device_map.items()
            if device.device_id == preferred_device_id
        }
    if not device_map:
        if preferred_device_id is None:
            raise RuntimeError("No controller devices are currently available.")
        raise RuntimeError("The selected controller is not currently available.")
    return device_map


def iter_button_presses(
    stop_event: Event,
    *,
    preferred_device_id: str | None = None,
    baseline_seconds: float = 0.8,
) -> Iterator[tuple[raw.HidDevice, int]]:
    """Yield deliberate button-down transitions from one persistent Raw Input session.

    All matching controllers may participate when ``preferred_device_id`` is None.
    A short baseline phase is always completed before transitions are emitted.
    Devices that do not report during that phase are still safe: their first later
    report establishes state and is never emitted as a press.
    """

    raw._require_windows()
    if not isinstance(stop_event, Event):
        raise ValueError("stop_event must be threading.Event")
    if baseline_seconds < 0:
        raise ValueError("baseline_seconds must not be negative")

    device_map = _selected_devices(preferred_device_id)
    preparsed: dict[int, object] = {}
    tracker = ButtonTransitionTracker()
    hwnd = raw._create_sink_window()

    def pump(*, armed: bool):
        message = raw.wintypes.MSG()
        emitted: list[tuple[raw.HidDevice, int]] = []
        saw_message = False
        while raw.user32.PeekMessageW(
            raw.ctypes.byref(message), hwnd, 0, 0, raw.PM_REMOVE
        ):
            saw_message = True
            if message.message == raw.WM_INPUT:
                packet = raw._raw_input_bytes(message.lParam)
                if packet is not None:
                    decoded = raw._decode_hid_packet(packet)
                    if decoded is not None:
                        handle, report_size, report_count, payload = decoded
                        device = device_map.get(handle)
                        if device is not None:
                            if handle not in preparsed:
                                preparsed[handle] = raw._preparsed_data(handle)
                            for report_index in range(report_count):
                                start = report_index * report_size
                                report = payload[start : start + report_size]
                                active = raw._active_button_usages(preparsed[handle], report)
                                for button in tracker.observe(handle, active, armed=armed):
                                    emitted.append((device, button))
            raw.user32.TranslateMessage(raw.ctypes.byref(message))
            raw.user32.DispatchMessageW(raw.ctypes.byref(message))
        return saw_message, emitted

    try:
        raw._register_controller_raw_input(hwnd)

        baseline_deadline = time.perf_counter() + baseline_seconds
        while not stop_event.is_set() and time.perf_counter() < baseline_deadline:
            saw, _ = pump(armed=False)
            if not saw:
                stop_event.wait(0.005)

        while not stop_event.is_set():
            saw, emitted = pump(armed=True)
            for candidate in emitted:
                yield candidate
            if not saw:
                stop_event.wait(0.005)
    finally:
        raw.user32.DestroyWindow(hwnd)


def capture_moved_axis(
    *,
    timeout_seconds: float = 12.0,
    movement_threshold: float = 15.0,
    preferred_device_id: str | None = None,
):
    """Return the first clearly moved analogue axis across available controllers.

    Every observed axis gets a baseline before it can win detection.  Small jitter is
    ignored.  Supplying ``preferred_device_id`` remains available for diagnostics,
    while the normal UI can simply ask the operator to move the intended control.
    """

    raw._require_windows()
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")
    if movement_threshold <= 0:
        raise ValueError("movement_threshold must be greater than zero")

    device_map = _selected_devices(preferred_device_id)
    hwnd = raw._create_sink_window()
    preparsed: dict[int, object] = {}
    axes_by_handle: dict[int, list] = {}
    baseline: dict[tuple[int, int], float] = {}

    try:
        raw._register_controller_raw_input(hwnd)
        deadline = time.perf_counter() + timeout_seconds
        message = raw.wintypes.MSG()

        while time.perf_counter() < deadline:
            saw_message = False
            while raw.user32.PeekMessageW(
                raw.ctypes.byref(message), hwnd, 0, 0, raw.PM_REMOVE
            ):
                saw_message = True
                if message.message == raw.WM_INPUT:
                    packet = raw._raw_input_bytes(message.lParam)
                    if packet is not None:
                        decoded = raw._decode_hid_packet(packet)
                        if decoded is not None:
                            handle, report_size, report_count, payload = decoded
                            device = device_map.get(handle)
                            if device is not None:
                                if handle not in preparsed:
                                    preparsed[handle] = raw._preparsed_data(handle)
                                    axes_by_handle[handle] = axis_raw._axis_caps(preparsed[handle])
                                axes = axes_by_handle[handle]
                                for report_index in range(report_count):
                                    start = report_index * report_size
                                    report = payload[start : start + report_size]
                                    for axis_index, (cap, usage) in enumerate(axes):
                                        value = axis_raw._read_axis(
                                            preparsed[handle], cap, usage, report
                                        )
                                        if value is None:
                                            continue
                                        key = (handle, axis_index)
                                        start_value = baseline.get(key)
                                        if start_value is None:
                                            baseline[key] = value
                                            continue
                                        if abs(value - start_value) >= movement_threshold:
                                            return device, axis_index, axis_raw.axis_usage_name(usage), value

                raw.user32.TranslateMessage(raw.ctypes.byref(message))
                raw.user32.DispatchMessageW(raw.ctypes.byref(message))

            if not saw_message:
                time.sleep(0.005)

        if not baseline:
            raise TimeoutError(
                "No axis data arrived. Move one stick, throttle, slider or rotary and try again."
            )
        raise TimeoutError(
            "No axis moved far enough to identify it. Move one control through a larger part of its travel."
        )
    finally:
        raw.user32.DestroyWindow(hwnd)
