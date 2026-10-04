#!/usr/bin/env python3
"""Continuous and one-shot Windows Raw Input state helpers.

This module reuses the already accepted Raw Input/HidP transport and button
parser from ``lighting_hid_capture``. It owns two small runtime/UI boundaries:

- maintain current Button/Axis states for the addresses used by an applied profile;
- identify one deliberately moved axis on a selected controller for UI binding.

Button values remain unknown until a report for that device establishes state.
Axis values are normalized to 0..100 using the HID logical range, matching the
physically accepted axis probe and VIRPIL profile scale.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterable

import lighting_hid_capture as raw

ButtonAddress = tuple[str, int]
AxisAddress = tuple[str, int]

HID_TOPOLOGY_REFRESH_SECONDS = 1.0

AXIS_USAGE_NAMES = {
    0x30: "X axis",
    0x31: "Y axis",
    0x32: "Z axis",
    0x33: "X rotation",
    0x34: "Y rotation",
    0x35: "Z rotation",
    0x36: "Slider",
    0x37: "Dial",
    0x38: "Wheel",
}


def axis_usage_name(usage: int) -> str:
    """Return a HOTAS-readable name for a HID Generic Desktop axis usage."""
    return AXIS_USAGE_NAMES.get(int(usage), f"Axis usage 0x{int(usage):02X}")


def axis_position_display(value: float) -> tuple[str, int]:
    """Format a normalized axis value for the editor without hardware assumptions."""
    clamped = max(0.0, min(100.0, float(value)))
    percent = max(0, min(100, round(clamped)))
    return f"{clamped:.0f}%", percent


def axis_report_matches(report_id: int, report: bytes) -> bool:
    """Return whether a HID input report can contain a value for ``report_id``."""
    report_id = int(report_id)
    return report_id == 0 or (bool(report) and report[0] == report_id)


def prune_disconnected_states(
    available_device_ids: Iterable[str],
    button_states: dict[ButtonAddress, bool],
    axis_values: dict[AxisAddress, float],
) -> None:
    """Drop stale samples for controllers that are no longer enumerated.

    Connected-device state semantics are unchanged.  This only prevents a
    physically removed controller from leaving its last Pressed/axis value
    latched indefinitely in the live rule calculation.
    """
    available = frozenset(str(device_id) for device_id in available_device_ids)
    for address in tuple(button_states):
        if address[0] not in available:
            del button_states[address]
    for address in tuple(axis_values):
        if address[0] not in available:
            del axis_values[address]


class LatestAxisValue:
    """Thread-safe latest-value cell: updates replace state instead of forming a queue."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value: float | None = None
        self._revision = 0

    def update(self, value: float) -> None:
        with self._lock:
            self._value = float(value)
            self._revision += 1

    def snapshot(self) -> tuple[float | None, int]:
        with self._lock:
            return self._value, self._revision


class AxisLiveMonitor:
    """Continuously drain Raw Input off the Qt thread and expose only newest axis state.

    This is an editor-feedback adapter, not a replacement for ``HidStateAdapter``.
    The accepted runtime snapshot path remains unchanged. The monitor owns exactly
    one Raw Input sink window on one worker thread for its complete lifetime.
    """

    def __init__(self, device_id: str, axis_index: int) -> None:
        raw._require_windows()
        if not isinstance(device_id, str) or not device_id:
            raise ValueError("device_id must identify a selected controller")
        if int(axis_index) < 0:
            raise ValueError("axis_index must be non-negative")
        self.device_id = device_id
        self.axis_index = int(axis_index)
        self._latest = LatestAxisValue()
        self._meta_lock = threading.Lock()
        self._axis_name: str | None = None
        self._error: str | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="EDL Axis Live Monitor",
            daemon=True,
        )
        self._thread.start()

    def _set_axis_name(self, name: str) -> None:
        with self._meta_lock:
            self._axis_name = name

    def _set_error(self, message: str) -> None:
        with self._meta_lock:
            self._error = message

    def axis_name(self) -> str | None:
        with self._meta_lock:
            return self._axis_name

    def error(self) -> str | None:
        with self._meta_lock:
            return self._error

    def latest_snapshot(self) -> tuple[float | None, int]:
        """Return the newest sample and monotonic revision without consuming it."""
        return self._latest.snapshot()

    def latest_value(self) -> float | None:
        value, _revision = self.latest_snapshot()
        return value

    def _run(self) -> None:
        hwnd = None
        try:
            device_map = {
                handle: device
                for handle, device in raw._current_device_map().items()
                if device.device_id == self.device_id
            }
            if not device_map:
                raise RuntimeError("The selected controller is not currently available.")
            if self._stop.is_set():
                return

            hwnd = raw._create_sink_window()
            if self._stop.is_set():
                return
            raw._register_controller_raw_input(hwnd)
            preparsed: dict[int, object] = {}
            axes_by_handle: dict[int, list] = {}
            message = raw.wintypes.MSG()

            while not self._stop.is_set():
                saw_message = False
                while not self._stop.is_set() and raw.user32.PeekMessageW(
                    raw.ctypes.byref(message), hwnd, 0, 0, raw.PM_REMOVE
                ):
                    saw_message = True
                    if message.message == raw.WM_INPUT:
                        packet = raw._raw_input_bytes(message.lParam)
                        if packet is not None:
                            decoded = raw._decode_hid_packet(packet)
                            if decoded is not None:
                                handle, report_size, report_count, payload = decoded
                                if handle in device_map:
                                    if handle not in preparsed:
                                        preparsed[handle] = raw._preparsed_data(handle)
                                        axes_by_handle[handle] = _axis_caps(preparsed[handle])
                                    axes = axes_by_handle[handle]
                                    if 0 <= self.axis_index < len(axes):
                                        cap, usage = axes[self.axis_index]
                                        self._set_axis_name(axis_usage_name(usage))
                                        latest_report = None
                                        for report_index in range(report_count):
                                            start = report_index * report_size
                                            report = payload[start : start + report_size]
                                            if axis_report_matches(int(cap.ReportID), report):
                                                latest_report = report
                                        if latest_report is not None:
                                            value = _read_axis(
                                                preparsed[handle], cap, usage, latest_report
                                            )
                                            if value is not None:
                                                self._latest.update(value)

                    raw.user32.TranslateMessage(raw.ctypes.byref(message))
                    raw.user32.DispatchMessageW(raw.ctypes.byref(message))

                if not saw_message:
                    self._stop.wait(0.001)
        except Exception as exc:
            self._set_error(str(exc))
        finally:
            if hwnd:
                raw.user32.DestroyWindow(hwnd)

    def close(self) -> None:
        """Stop the worker and do not return while its Raw Input sink can still exist."""
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    hid = ctypes.WinDLL("hid", use_last_error=True)

    HIDP_INPUT = 0
    GENERIC_DESKTOP_PAGE = 0x01
    AXIS_USAGES = frozenset(range(0x30, 0x39))

    class HIDP_CAPS(ctypes.Structure):
        _fields_ = [
            ("Usage", wintypes.USHORT),
            ("UsagePage", wintypes.USHORT),
            ("InputReportByteLength", wintypes.USHORT),
            ("OutputReportByteLength", wintypes.USHORT),
            ("FeatureReportByteLength", wintypes.USHORT),
            ("Reserved", wintypes.USHORT * 17),
            ("NumberLinkCollectionNodes", wintypes.USHORT),
            ("NumberInputButtonCaps", wintypes.USHORT),
            ("NumberInputValueCaps", wintypes.USHORT),
            ("NumberInputDataIndices", wintypes.USHORT),
            ("NumberOutputButtonCaps", wintypes.USHORT),
            ("NumberOutputValueCaps", wintypes.USHORT),
            ("NumberOutputDataIndices", wintypes.USHORT),
            ("NumberFeatureButtonCaps", wintypes.USHORT),
            ("NumberFeatureValueCaps", wintypes.USHORT),
            ("NumberFeatureDataIndices", wintypes.USHORT),
        ]

    class HIDP_VALUE_CAPS_RANGE(ctypes.Structure):
        _fields_ = [
            ("UsageMin", wintypes.USHORT),
            ("UsageMax", wintypes.USHORT),
            ("StringMin", wintypes.USHORT),
            ("StringMax", wintypes.USHORT),
            ("DesignatorMin", wintypes.USHORT),
            ("DesignatorMax", wintypes.USHORT),
            ("DataIndexMin", wintypes.USHORT),
            ("DataIndexMax", wintypes.USHORT),
        ]

    class HIDP_VALUE_CAPS_NOT_RANGE(ctypes.Structure):
        _fields_ = [
            ("Usage", wintypes.USHORT),
            ("Reserved1", wintypes.USHORT),
            ("StringIndex", wintypes.USHORT),
            ("Reserved2", wintypes.USHORT),
            ("DesignatorIndex", wintypes.USHORT),
            ("Reserved3", wintypes.USHORT),
            ("DataIndex", wintypes.USHORT),
            ("Reserved4", wintypes.USHORT),
        ]

    class HIDP_VALUE_CAPS_UNION(ctypes.Union):
        _fields_ = [
            ("Range", HIDP_VALUE_CAPS_RANGE),
            ("NotRange", HIDP_VALUE_CAPS_NOT_RANGE),
        ]

    class HIDP_VALUE_CAPS(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [
            ("UsagePage", wintypes.USHORT),
            ("ReportID", ctypes.c_ubyte),
            ("IsAlias", ctypes.c_ubyte),
            ("BitField", wintypes.USHORT),
            ("LinkCollection", wintypes.USHORT),
            ("LinkUsage", wintypes.USHORT),
            ("LinkUsagePage", wintypes.USHORT),
            ("IsRange", ctypes.c_ubyte),
            ("IsStringRange", ctypes.c_ubyte),
            ("IsDesignatorRange", ctypes.c_ubyte),
            ("IsAbsolute", ctypes.c_ubyte),
            ("HasNull", ctypes.c_ubyte),
            ("Reserved", ctypes.c_ubyte),
            ("BitSize", wintypes.USHORT),
            ("ReportCount", wintypes.USHORT),
            ("Reserved2", wintypes.USHORT * 5),
            ("UnitsExp", wintypes.ULONG),
            ("Units", wintypes.ULONG),
            ("LogicalMin", wintypes.LONG),
            ("LogicalMax", wintypes.LONG),
            ("PhysicalMin", wintypes.LONG),
            ("PhysicalMax", wintypes.LONG),
            ("u", HIDP_VALUE_CAPS_UNION),
        ]

    hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
    hid.HidP_GetCaps.restype = ctypes.c_long
    hid.HidP_GetValueCaps.argtypes = [
        ctypes.c_int,
        ctypes.POINTER(HIDP_VALUE_CAPS),
        ctypes.POINTER(wintypes.USHORT),
        ctypes.c_void_p,
    ]
    hid.HidP_GetValueCaps.restype = ctypes.c_long
    hid.HidP_GetUsageValue.argtypes = [
        ctypes.c_int,
        wintypes.USHORT,
        wintypes.USHORT,
        wintypes.USHORT,
        ctypes.POINTER(wintypes.ULONG),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_char),
        wintypes.ULONG,
    ]
    hid.HidP_GetUsageValue.restype = ctypes.c_long


def _axis_caps(preparsed):
    pp = ctypes.cast(preparsed, ctypes.c_void_p)
    caps = HIDP_CAPS()
    status = hid.HidP_GetCaps(pp, ctypes.byref(caps))
    if status < 0:
        raise RuntimeError(f"HidP_GetCaps failed: 0x{status & 0xFFFFFFFF:08X}")

    count = int(caps.NumberInputValueCaps)
    if count <= 0:
        return []

    values = (HIDP_VALUE_CAPS * count)()
    length = wintypes.USHORT(count)
    status = hid.HidP_GetValueCaps(HIDP_INPUT, values, ctypes.byref(length), pp)
    if status < 0:
        raise RuntimeError(f"HidP_GetValueCaps failed: 0x{status & 0xFFFFFFFF:08X}")

    found = []
    for cap in values[: int(length.value)]:
        if int(cap.UsagePage) != GENERIC_DESKTOP_PAGE:
            continue
        if cap.IsRange:
            usages = range(int(cap.Range.UsageMin), int(cap.Range.UsageMax) + 1)
        else:
            usages = (int(cap.NotRange.Usage),)
        for usage in usages:
            if usage in AXIS_USAGES:
                found.append((cap, usage))
    return found


def _signed_if_needed(value: int, cap) -> int:
    if int(cap.LogicalMin) >= 0:
        return value
    bits = int(cap.BitSize)
    if bits <= 0 or bits >= 32:
        return ctypes.c_long(value).value
    sign = 1 << (bits - 1)
    mask = (1 << bits) - 1
    value &= mask
    return value - (1 << bits) if value & sign else value


def _read_axis(preparsed, cap, usage: int, report: bytes) -> float | None:
    value = wintypes.ULONG(0)
    report_buffer = ctypes.create_string_buffer(report, len(report))
    status = hid.HidP_GetUsageValue(
        HIDP_INPUT,
        int(cap.UsagePage),
        int(cap.LinkCollection),
        usage,
        ctypes.byref(value),
        ctypes.cast(preparsed, ctypes.c_void_p),
        report_buffer,
        len(report),
    )
    if status < 0:
        return None
    logical = _signed_if_needed(int(value.value), cap)
    low = int(cap.LogicalMin)
    high = int(cap.LogicalMax)
    if high == low:
        return None
    return (logical - low) * 100.0 / (high - low)


def capture_moved_axis(
    *,
    preferred_device_id: str,
    timeout_seconds: float = 12.0,
    movement_threshold: float = 15.0,
):
    """Return ``(device, axis_index, value_percent)`` for one deliberate movement.

    Only the selected controller participates. The first axis that moves by at
    least ``movement_threshold`` percentage points from its observed baseline is
    returned. Small analogue noise therefore does not bind an unrelated axis.
    """
    raw._require_windows()
    if not isinstance(preferred_device_id, str) or not preferred_device_id:
        raise ValueError("preferred_device_id must be a selected controller")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")
    if movement_threshold <= 0:
        raise ValueError("movement_threshold must be greater than zero")

    device_map = {
        handle: device
        for handle, device in raw._current_device_map().items()
        if device.device_id == preferred_device_id
    }
    if not device_map:
        raise RuntimeError("The selected controller is not currently available.")

    hwnd = raw._create_sink_window()
    preparsed: dict[int, object] = {}
    axes_by_handle: dict[int, list] = {}
    baseline: dict[tuple[int, int], float] = {}
    latest: dict[tuple[int, int], float] = {}

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
                                    axes_by_handle[handle] = _axis_caps(preparsed[handle])
                                axes = axes_by_handle[handle]
                                for report_index in range(report_count):
                                    start = report_index * report_size
                                    report = payload[start : start + report_size]
                                    for axis_index, (cap, usage) in enumerate(axes):
                                        value = _read_axis(preparsed[handle], cap, usage, report)
                                        if value is None:
                                            continue
                                        key = (handle, axis_index)
                                        latest[key] = value
                                        if key not in baseline:
                                            baseline[key] = value
                                            continue
                                        if abs(value - baseline[key]) >= movement_threshold:
                                            return device, axis_index, value

                raw.user32.TranslateMessage(raw.ctypes.byref(message))
                raw.user32.DispatchMessageW(raw.ctypes.byref(message))

            if not saw_message:
                time.sleep(0.005)

        if not baseline:
            raise TimeoutError(
                "No axis data arrived from the selected controller. Move an analogue control and try again."
            )
        raise TimeoutError(
            "No axis moved far enough to identify it. Move one stick, throttle, slider or rotary through a larger part of its travel."
        )
    finally:
        raw.user32.DestroyWindow(hwnd)


class HidStateAdapter:
    """Pump Raw Input and expose current configured Button/Axis values."""

    def __init__(
        self,
        *,
        watched_buttons: Iterable[ButtonAddress] = (),
        watched_axes: Iterable[AxisAddress] = (),
    ) -> None:
        raw._require_windows()
        self._watched_buttons = frozenset((str(device), int(button)) for device, button in watched_buttons)
        self._watched_axes = frozenset((str(device), int(axis)) for device, axis in watched_axes)
        self._buttons_by_device: dict[str, set[int]] = {}
        self._axes_by_device: dict[str, set[int]] = {}
        for device, button in self._watched_buttons:
            self._buttons_by_device.setdefault(device, set()).add(button)
        for device, axis in self._watched_axes:
            self._axes_by_device.setdefault(device, set()).add(axis)

        self._device_map = raw._current_device_map()
        self._last_topology_refresh = time.perf_counter()
        self._preparsed: dict[int, object] = {}
        self._axis_lists: dict[int, list] = {}
        self._button_states: dict[ButtonAddress, bool] = {}
        self._axis_values: dict[AxisAddress, float] = {}
        self._hwnd = raw._create_sink_window()
        raw._register_controller_raw_input(self._hwnd)

    def _prepare(self, handle: int):
        if handle not in self._preparsed:
            self._preparsed[handle] = raw._preparsed_data(handle)
        return self._preparsed[handle]

    def _axes(self, handle: int):
        if handle not in self._axis_lists:
            self._axis_lists[handle] = _axis_caps(self._prepare(handle))
        return self._axis_lists[handle]

    def _refresh_topology(self, *, force: bool = False) -> None:
        now = time.perf_counter()
        if not force and now - self._last_topology_refresh < HID_TOPOLOGY_REFRESH_SECONDS:
            return
        current = raw._current_device_map()
        self._last_topology_refresh = now
        if current == self._device_map:
            return

        current_handles = set(current)
        for handle in tuple(self._preparsed):
            if handle not in current_handles:
                self._preparsed.pop(handle, None)
                self._axis_lists.pop(handle, None)
        self._device_map = current
        prune_disconnected_states(
            (device.device_id for device in current.values()),
            self._button_states,
            self._axis_values,
        )

    def poll(self) -> None:
        self._refresh_topology()
        message = raw.wintypes.MSG()
        while raw.user32.PeekMessageW(
            raw.ctypes.byref(message), self._hwnd, 0, 0, raw.PM_REMOVE
        ):
            if message.message == raw.WM_INPUT:
                packet = raw._raw_input_bytes(message.lParam)
                if packet is not None:
                    decoded = raw._decode_hid_packet(packet)
                    if decoded is not None:
                        handle, report_size, report_count, payload = decoded
                        device = self._device_map.get(handle)
                        if device is None:
                            # A reconnected controller normally receives a new Raw
                            # Input handle.  Refresh only at this topology boundary.
                            self._refresh_topology(force=True)
                            device = self._device_map.get(handle)
                        if device is not None:
                            wanted_buttons = self._buttons_by_device.get(device.device_id, set())
                            wanted_axes = self._axes_by_device.get(device.device_id, set())
                            if wanted_buttons or wanted_axes:
                                preparsed = self._prepare(handle)
                                axes = self._axes(handle) if wanted_axes else ()
                                for report_index in range(report_count):
                                    start = report_index * report_size
                                    report = payload[start : start + report_size]
                                    if wanted_buttons:
                                        active = raw._active_button_usages(preparsed, report)
                                        for button in wanted_buttons:
                                            self._button_states[(device.device_id, button)] = button in active
                                    for axis_index in wanted_axes:
                                        if 0 <= axis_index < len(axes):
                                            cap, usage = axes[axis_index]
                                            value = _read_axis(preparsed, cap, usage, report)
                                            if value is not None:
                                                self._axis_values[(device.device_id, axis_index)] = value

            raw.user32.TranslateMessage(raw.ctypes.byref(message))
            raw.user32.DispatchMessageW(raw.ctypes.byref(message))

    def axis_name(self, device_id: str, axis_index: int) -> str | None:
        """Resolve the selected stored axis index back to its HID semantic name."""
        for handle, device in self._device_map.items():
            if device.device_id != device_id:
                continue
            axes = self._axes(handle)
            if 0 <= axis_index < len(axes):
                _cap, usage = axes[axis_index]
                return axis_usage_name(usage)
        return None

    def snapshot(self) -> tuple[dict[ButtonAddress, bool], dict[AxisAddress, float]]:
        self.poll()
        return dict(self._button_states), dict(self._axis_values)

    def close(self) -> None:
        hwnd = self._hwnd
        if hwnd:
            raw.user32.DestroyWindow(hwnd)
            self._hwnd = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
