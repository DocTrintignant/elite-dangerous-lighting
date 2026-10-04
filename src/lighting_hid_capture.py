#!/usr/bin/env python3
"""Windows Raw Input HID discovery and one-shot binding helpers for the UI.

This is the production-facing extraction of the already physically accepted
Raw Input/HidP mechanics used by the project probes. It deliberately owns only
device discovery and bounded operator binding capture; rule semantics remain in
``lighting_rules.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import time
from typing import Iterable

KNOWN_VENDORS = {
    0x231D: "VKB",
    0x4098: "WINCTRL / WinWing",
    0x3344: "VIRPIL / VPC",
    0x1234: "vJoy",
}
KNOWN_PRODUCTS = {
    (0x3344, 0x025A): "VIRPIL VPC Panel #2",
}
GENERIC_DESKTOP_PAGE = 0x01
CONTROLLER_USAGES = {
    0x04: "Joystick",
    0x05: "Game Pad",
    0x08: "Multi-axis Controller",
}


@dataclass(frozen=True)
class HidDevice:
    device_id: str
    label: str
    vendor_id: int
    product_id: int
    usage_page: int
    usage: int


def make_device_label(
    vendor_id: int,
    product_id: int,
    usage: int,
    *,
    ordinal: int | None = None,
) -> str:
    product = KNOWN_PRODUCTS.get((vendor_id, product_id))
    if product:
        base = product
    else:
        vendor = KNOWN_VENDORS.get(vendor_id, f"VID {vendor_id:04X}")
        usage_label = CONTROLLER_USAGES.get(usage, f"usage {usage:04X}")
        base = f"{vendor} — PID {product_id:04X} — {usage_label}"
    if ordinal is not None:
        return f"{base} #{ordinal}"
    return base


if os.name == "nt":
    import ctypes
    import struct
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    hid = ctypes.WinDLL("hid", use_last_error=True)

    RIM_TYPEHID = 2
    RID_INPUT = 0x10000003
    RIDI_DEVICENAME = 0x20000007
    RIDI_DEVICEINFO = 0x2000000B
    RIDI_PREPARSEDDATA = 0x20000005
    WM_INPUT = 0x00FF
    PM_REMOVE = 0x0001
    RIDEV_INPUTSINK = 0x00000100
    HIDP_INPUT = 0
    BUTTON_USAGE_PAGE = 0x09

    class RAWINPUTDEVICELIST(ctypes.Structure):
        _fields_ = [
            ("hDevice", wintypes.HANDLE),
            ("dwType", wintypes.DWORD),
        ]

    class RID_DEVICE_INFO_MOUSE(ctypes.Structure):
        _fields_ = [
            ("dwId", wintypes.DWORD),
            ("dwNumberOfButtons", wintypes.DWORD),
            ("dwSampleRate", wintypes.DWORD),
            ("fHasHorizontalWheel", wintypes.BOOL),
        ]

    class RID_DEVICE_INFO_KEYBOARD(ctypes.Structure):
        _fields_ = [
            ("dwType", wintypes.DWORD),
            ("dwSubType", wintypes.DWORD),
            ("dwKeyboardMode", wintypes.DWORD),
            ("dwNumberOfFunctionKeys", wintypes.DWORD),
            ("dwNumberOfIndicators", wintypes.DWORD),
            ("dwNumberOfKeysTotal", wintypes.DWORD),
        ]

    class RID_DEVICE_INFO_HID(ctypes.Structure):
        _fields_ = [
            ("dwVendorId", wintypes.DWORD),
            ("dwProductId", wintypes.DWORD),
            ("dwVersionNumber", wintypes.DWORD),
            ("usUsagePage", wintypes.USHORT),
            ("usUsage", wintypes.USHORT),
        ]

    class RID_DEVICE_INFO_UNION(ctypes.Union):
        _fields_ = [
            ("mouse", RID_DEVICE_INFO_MOUSE),
            ("keyboard", RID_DEVICE_INFO_KEYBOARD),
            ("hid", RID_DEVICE_INFO_HID),
        ]

    class RID_DEVICE_INFO(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("dwType", wintypes.DWORD),
            ("u", RID_DEVICE_INFO_UNION),
        ]

    class RAWINPUTDEVICE(ctypes.Structure):
        _fields_ = [
            ("usUsagePage", wintypes.USHORT),
            ("usUsage", wintypes.USHORT),
            ("dwFlags", wintypes.DWORD),
            ("hwndTarget", wintypes.HWND),
        ]

    class RAWINPUTHEADER(ctypes.Structure):
        _fields_ = [
            ("dwType", wintypes.DWORD),
            ("dwSize", wintypes.DWORD),
            ("hDevice", wintypes.HANDLE),
            ("wParam", wintypes.WPARAM),
        ]

    user32.GetRawInputDeviceList.argtypes = [
        ctypes.POINTER(RAWINPUTDEVICELIST),
        ctypes.POINTER(wintypes.UINT),
        wintypes.UINT,
    ]
    user32.GetRawInputDeviceList.restype = wintypes.UINT
    user32.GetRawInputDeviceInfoW.argtypes = [
        wintypes.HANDLE,
        wintypes.UINT,
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.UINT),
    ]
    user32.GetRawInputDeviceInfoW.restype = wintypes.UINT
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL
    user32.RegisterRawInputDevices.argtypes = [
        ctypes.POINTER(RAWINPUTDEVICE),
        wintypes.UINT,
        wintypes.UINT,
    ]
    user32.RegisterRawInputDevices.restype = wintypes.BOOL
    user32.GetRawInputData.argtypes = [
        wintypes.HANDLE,
        wintypes.UINT,
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.UINT),
        wintypes.UINT,
    ]
    user32.GetRawInputData.restype = wintypes.UINT
    user32.PeekMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
        wintypes.UINT,
    ]
    user32.PeekMessageW.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.TranslateMessage.restype = wintypes.BOOL
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = wintypes.LPARAM

    hid.HidP_MaxUsageListLength.argtypes = [
        ctypes.c_int,
        wintypes.USHORT,
        ctypes.c_void_p,
    ]
    hid.HidP_MaxUsageListLength.restype = wintypes.ULONG
    hid.HidP_GetUsages.argtypes = [
        ctypes.c_int,
        wintypes.USHORT,
        wintypes.USHORT,
        ctypes.POINTER(wintypes.USHORT),
        ctypes.POINTER(wintypes.ULONG),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_char),
        wintypes.ULONG,
    ]
    hid.HidP_GetUsages.restype = ctypes.c_long


def _require_windows() -> None:
    if os.name != "nt":
        raise RuntimeError("Raw Input HID discovery requires Windows.")


def _handle_value(handle: object) -> int:
    _require_windows()
    if handle is None:
        return 0
    if isinstance(handle, int):
        return handle
    value = ctypes.cast(handle, ctypes.c_void_p).value
    return int(value or 0)


def _win_error(label: str) -> OSError:
    _require_windows()
    return ctypes.WinError(ctypes.get_last_error(), label)


def _device_name(handle) -> str:
    chars = wintypes.UINT(0)
    result = user32.GetRawInputDeviceInfoW(
        handle, RIDI_DEVICENAME, None, ctypes.byref(chars)
    )
    if result == 0xFFFFFFFF:
        raise _win_error("GetRawInputDeviceInfoW(RIDI_DEVICENAME size)")
    if chars.value == 0:
        return ""
    buffer = ctypes.create_unicode_buffer(chars.value + 1)
    capacity = wintypes.UINT(len(buffer))
    result = user32.GetRawInputDeviceInfoW(
        handle, RIDI_DEVICENAME, buffer, ctypes.byref(capacity)
    )
    if result == 0xFFFFFFFF:
        raise _win_error("GetRawInputDeviceInfoW(RIDI_DEVICENAME data)")
    return buffer.value


def _device_info(handle):
    info = RID_DEVICE_INFO()
    info.cbSize = ctypes.sizeof(RID_DEVICE_INFO)
    size = wintypes.UINT(ctypes.sizeof(RID_DEVICE_INFO))
    result = user32.GetRawInputDeviceInfoW(
        handle, RIDI_DEVICEINFO, ctypes.byref(info), ctypes.byref(size)
    )
    if result == 0xFFFFFFFF:
        raise _win_error("GetRawInputDeviceInfoW(RIDI_DEVICEINFO)")
    return info


def _raw_rows():
    _require_windows()
    count = wintypes.UINT(0)
    result = user32.GetRawInputDeviceList(
        None, ctypes.byref(count), ctypes.sizeof(RAWINPUTDEVICELIST)
    )
    if result == 0xFFFFFFFF:
        raise _win_error("GetRawInputDeviceList(size)")
    if count.value == 0:
        return []

    devices = (RAWINPUTDEVICELIST * count.value)()
    actual = wintypes.UINT(count.value)
    result = user32.GetRawInputDeviceList(
        devices, ctypes.byref(actual), ctypes.sizeof(RAWINPUTDEVICELIST)
    )
    if result == 0xFFFFFFFF:
        raise _win_error("GetRawInputDeviceList(data)")

    rows = []
    for item in devices[: actual.value]:
        if item.dwType != RIM_TYPEHID:
            continue
        try:
            info = _device_info(item.hDevice)
            name = _device_name(item.hDevice)
        except OSError:
            continue
        hid_info = info.hid
        if (
            int(hid_info.dwVendorId) not in KNOWN_VENDORS
            and not (
                int(hid_info.usUsagePage) == GENERIC_DESKTOP_PAGE
                and int(hid_info.usUsage) in CONTROLLER_USAGES
            )
        ):
            continue
        rows.append((item, info, name))
    return rows


def enumerate_hid_devices() -> tuple[HidDevice, ...]:
    """Return stable Raw Input device-path identifiers with readable labels."""
    if os.name != "nt":
        return ()

    raw = _raw_rows()
    counts: dict[tuple[int, int, int], int] = {}
    for _item, info, _name in raw:
        key = (
            int(info.hid.dwVendorId),
            int(info.hid.dwProductId),
            int(info.hid.usUsage),
        )
        counts[key] = counts.get(key, 0) + 1

    seen: dict[tuple[int, int, int], int] = {}
    result: list[HidDevice] = []
    for _item, info, name in raw:
        vendor = int(info.hid.dwVendorId)
        product = int(info.hid.dwProductId)
        usage_page = int(info.hid.usUsagePage)
        usage = int(info.hid.usUsage)
        key = (vendor, product, usage)
        seen[key] = seen.get(key, 0) + 1
        ordinal = seen[key] if counts[key] > 1 else None
        result.append(
            HidDevice(
                device_id=name,
                label=make_device_label(vendor, product, usage, ordinal=ordinal),
                vendor_id=vendor,
                product_id=product,
                usage_page=usage_page,
                usage=usage,
            )
        )
    return tuple(result)


def _current_device_map() -> dict[int, HidDevice]:
    result: dict[int, HidDevice] = {}
    devices_by_id = {device.device_id: device for device in enumerate_hid_devices()}
    for item, _info, name in _raw_rows():
        device = devices_by_id.get(name)
        if device is not None:
            result[_handle_value(item.hDevice)] = device
    return result


def _create_sink_window():
    hwnd = user32.CreateWindowExW(
        0,
        "STATIC",
        "Elite Dangerous Lighting Raw Input Bind",
        0,
        0,
        0,
        0,
        0,
        None,
        None,
        None,
        None,
    )
    if not hwnd:
        raise _win_error("CreateWindowExW")
    return hwnd


def _register_controller_raw_input(hwnd) -> None:
    usages = tuple(CONTROLLER_USAGES)
    registrations = (RAWINPUTDEVICE * len(usages))(
        *(
            RAWINPUTDEVICE(
                GENERIC_DESKTOP_PAGE,
                usage,
                RIDEV_INPUTSINK,
                hwnd,
            )
            for usage in usages
        )
    )
    ok = user32.RegisterRawInputDevices(
        registrations, len(usages), ctypes.sizeof(RAWINPUTDEVICE)
    )
    if not ok:
        raise _win_error("RegisterRawInputDevices")


def _raw_input_bytes(hrawinput) -> bytes | None:
    size = wintypes.UINT(0)
    result = user32.GetRawInputData(
        hrawinput,
        RID_INPUT,
        None,
        ctypes.byref(size),
        ctypes.sizeof(RAWINPUTHEADER),
    )
    if result == 0xFFFFFFFF:
        raise _win_error("GetRawInputData(size)")
    if size.value == 0:
        return None
    buffer = ctypes.create_string_buffer(size.value)
    result = user32.GetRawInputData(
        hrawinput,
        RID_INPUT,
        buffer,
        ctypes.byref(size),
        ctypes.sizeof(RAWINPUTHEADER),
    )
    if result == 0xFFFFFFFF:
        raise _win_error("GetRawInputData(data)")
    return bytes(buffer.raw[: size.value])


def _decode_hid_packet(packet: bytes) -> tuple[int, int, int, bytes] | None:
    header_size = ctypes.sizeof(RAWINPUTHEADER)
    if len(packet) < header_size + 8:
        return None
    header = RAWINPUTHEADER.from_buffer_copy(packet[:header_size])
    if header.dwType != RIM_TYPEHID:
        return None
    report_size, report_count = struct.unpack_from("<II", packet, header_size)
    payload_start = header_size + 8
    payload_size = report_size * report_count
    payload = packet[payload_start : payload_start + payload_size]
    return _handle_value(header.hDevice), report_size, report_count, payload


def _preparsed_data(handle_value: int):
    size = wintypes.UINT(0)
    result = user32.GetRawInputDeviceInfoW(
        wintypes.HANDLE(handle_value),
        RIDI_PREPARSEDDATA,
        None,
        ctypes.byref(size),
    )
    if result == 0xFFFFFFFF:
        raise _win_error("GetRawInputDeviceInfoW(RIDI_PREPARSEDDATA size)")
    if size.value == 0:
        raise RuntimeError("Raw Input returned zero preparsed-data bytes")
    buffer = ctypes.create_string_buffer(size.value)
    capacity = wintypes.UINT(size.value)
    result = user32.GetRawInputDeviceInfoW(
        wintypes.HANDLE(handle_value),
        RIDI_PREPARSEDDATA,
        buffer,
        ctypes.byref(capacity),
    )
    if result == 0xFFFFFFFF:
        raise _win_error("GetRawInputDeviceInfoW(RIDI_PREPARSEDDATA data)")
    return buffer


def _active_button_usages(preparsed, report: bytes) -> set[int]:
    preparsed_ptr = ctypes.cast(preparsed, ctypes.c_void_p)
    maximum = int(
        hid.HidP_MaxUsageListLength(
            HIDP_INPUT,
            BUTTON_USAGE_PAGE,
            preparsed_ptr,
        )
    )
    if maximum <= 0:
        return set()

    usages = (wintypes.USHORT * maximum)()
    usage_count = wintypes.ULONG(maximum)
    report_buffer = ctypes.create_string_buffer(report, len(report))
    status = hid.HidP_GetUsages(
        HIDP_INPUT,
        BUTTON_USAGE_PAGE,
        0,
        usages,
        ctypes.byref(usage_count),
        preparsed_ptr,
        report_buffer,
        len(report),
    )
    if status < 0:
        raise RuntimeError(
            f"HidP_GetUsages failed with NTSTATUS 0x{status & 0xFFFFFFFF:08X}"
        )
    return {int(usages[index]) for index in range(int(usage_count.value))}


def capture_next_button(
    *,
    timeout_seconds: float = 10.0,
    preferred_device_id: str | None = None,
) -> tuple[HidDevice, int]:
    """Wait for one physical button-down transition and return device + HID usage."""
    _require_windows()
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")

    device_map = _current_device_map()
    if preferred_device_id is not None:
        device_map = {
            handle: device
            for handle, device in device_map.items()
            if device.device_id == preferred_device_id
        }
    if not device_map:
        raise RuntimeError("No matching controller-class Raw Input HID device is available.")

    preparsed: dict[int, object] = {}
    previous: dict[int, set[int]] = {}
    hwnd = _create_sink_window()
    try:
        _register_controller_raw_input(hwnd)
        deadline = time.perf_counter() + timeout_seconds
        message = wintypes.MSG()

        while time.perf_counter() < deadline:
            observed_message = False
            while user32.PeekMessageW(
                ctypes.byref(message), hwnd, 0, 0, PM_REMOVE
            ):
                observed_message = True
                if message.message == WM_INPUT:
                    packet = _raw_input_bytes(message.lParam)
                    if packet is not None:
                        decoded = _decode_hid_packet(packet)
                        if decoded is not None:
                            handle, report_size, report_count, payload = decoded
                            device = device_map.get(handle)
                            if device is not None:
                                if handle not in preparsed:
                                    preparsed[handle] = _preparsed_data(handle)
                                for index in range(report_count):
                                    start = index * report_size
                                    report = payload[start : start + report_size]
                                    active = _active_button_usages(preparsed[handle], report)
                                    prior = previous.get(handle)
                                    if prior is None:
                                        # On devices that only report on change, a first
                                        # unambiguous active usage is the operator's press.
                                        if len(active) == 1:
                                            return device, next(iter(active))
                                        previous[handle] = active
                                        continue
                                    newly_pressed = sorted(active - prior)
                                    previous[handle] = active
                                    if newly_pressed:
                                        return device, newly_pressed[0]

                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))

            if not observed_message:
                time.sleep(0.005)

        raise TimeoutError("No button press was detected before the binding timeout.")
    finally:
        user32.DestroyWindow(hwnd)
