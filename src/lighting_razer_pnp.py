#!/usr/bin/env python3
"""Read-only Windows PnP identity discovery for physical Razer devices.

This module does not open HID devices or acquire lighting control. It asks
Windows for already-present PnP metadata so EDL can give Chroma/OpenRGB routes
one stable physical-device identity.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import subprocess
from typing import Iterable, Mapping


_RAZER_VID = "1532"
_VID_PID_RE = re.compile(r"VID_([0-9A-F]{4})&PID_([0-9A-F]{4})", re.IGNORECASE)


class RazerPnPDiscoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class RazerPhysicalDevice:
    container_id: str
    name: str
    device_type: str
    vendor_id: str
    product_id: str
    instance_ids: tuple[str, ...]

    @property
    def identity(self) -> str:
        return f"windows-razer|{self.container_id.casefold()}"


def _clean(value) -> str:
    return " ".join(str(value or "").strip().split())


def normalize_razer_product_name(name: str) -> str:
    """Normalize benign transport suffixes for cross-source correlation only."""
    value = _clean(name)
    # OpenRGB commonly appends transport qualifiers such as "(Wireless)" while
    # Windows reports the product model without them. Do not alter display text.
    value = re.sub(
        r"\s*\((?:wireless|wired|bluetooth|2\.4\s*ghz|2\.4ghz)\)\s*$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    return value.casefold()


def _record_value(record: Mapping[str, object], *names: str) -> str:
    for name in names:
        if name in record:
            return _clean(record[name])
    lowered = {str(key).casefold(): value for key, value in record.items()}
    for name in names:
        value = lowered.get(name.casefold())
        if value is not None:
            return _clean(value)
    return ""


def parse_razer_pnp_records(
    records: Iterable[Mapping[str, object]],
) -> tuple[RazerPhysicalDevice, ...]:
    """Collapse named Windows interface rows into physical ContainerId devices."""
    groups: dict[str, list[dict[str, str]]] = {}

    for record in records:
        name = _record_value(record, "FriendlyName")
        class_name = _record_value(record, "Class")
        container_id = _record_value(record, "ContainerId")
        instance_id = _record_value(record, "InstanceId")

        if not name.casefold().startswith("razer "):
            continue
        if class_name.casefold() not in {"keyboard", "mouse", "hidclass"}:
            continue
        if not container_id or not instance_id:
            continue

        match = _VID_PID_RE.search(instance_id)
        if not match or match.group(1).upper() != _RAZER_VID:
            continue

        groups.setdefault(container_id.casefold(), []).append(
            {
                "name": name,
                "class": class_name,
                "container_id": container_id,
                "instance_id": instance_id,
                "vendor_id": match.group(1).upper(),
                "product_id": match.group(2).upper(),
            }
        )

    result = []
    for rows in groups.values():
        classes = {row["class"].casefold() for row in rows}
        if "keyboard" in classes:
            device_type = "Keyboard"
        elif "mouse" in classes:
            device_type = "Mouse"
        else:
            # A named HIDClass-only row does not prove which supported Chroma
            # endpoint class it belongs to, so do not guess.
            continue

        names = [row["name"] for row in rows]
        name = max(
            sorted(set(names), key=str.casefold),
            key=lambda candidate: (
                names.count(candidate),
                len(candidate),
                candidate.casefold(),
            ),
        )
        first = rows[0]
        result.append(
            RazerPhysicalDevice(
                container_id=first["container_id"],
                name=name,
                device_type=device_type,
                vendor_id=first["vendor_id"],
                product_id=first["product_id"],
                instance_ids=tuple(
                    sorted({row["instance_id"] for row in rows}, key=str.casefold)
                ),
            )
        )

    return tuple(
        sorted(
            result,
            key=lambda device: (
                device.name.casefold(),
                device.device_type.casefold(),
                device.container_id.casefold(),
            ),
        )
    )


_POWERSHELL_SCRIPT = r"""
$ErrorActionPreference = 'Stop'

$devices = @(
    Get-PnpDevice -PresentOnly |
        Where-Object {
            $_.InstanceId -match 'VID_1532&PID_' -and
            $_.FriendlyName -match '^Razer ' -and
            ($_.Class -eq 'Keyboard' -or $_.Class -eq 'Mouse' -or $_.Class -eq 'HIDClass')
        }
)

$result = @(
    foreach ($device in $devices) {
        $container = $null
        try {
            $container = (Get-PnpDeviceProperty -InstanceId $device.InstanceId -KeyName 'DEVPKEY_Device_ContainerId' -ErrorAction Stop).Data
        } catch {
            $container = $null
        }

        if ($null -ne $container) {
            [PSCustomObject]@{
                FriendlyName = $device.FriendlyName
                Class        = $device.Class
                ContainerId  = $container.ToString()
                InstanceId   = $device.InstanceId
            }
        }
    }
)

if ($result.Count -eq 0) {
    Write-Output '[]'
} else {
    $result | ConvertTo-Json -Compress
}
""".strip()


def discover_razer_physical_devices(
    timeout: float = 6.0,
) -> tuple[RazerPhysicalDevice, ...]:
    """Read currently-present Razer model/container metadata from Windows."""
    if os.name != "nt":
        return ()

    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                _POWERSHELL_SCRIPT,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=float(timeout),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RazerPnPDiscoveryError(
            "Windows Razer device identity lookup did not complete."
        ) from exc

    if completed.returncode != 0:
        detail = _clean(completed.stderr) or "PowerShell returned an error."
        raise RazerPnPDiscoveryError(
            f"Windows Razer device identity lookup failed: {detail}"
        )

    payload = completed.stdout.strip() or "[]"
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RazerPnPDiscoveryError(
            "Windows returned unreadable Razer device identity data."
        ) from exc

    if isinstance(decoded, dict):
        decoded = [decoded]
    if not isinstance(decoded, list):
        raise RazerPnPDiscoveryError(
            "Windows returned an unexpected Razer device identity format."
        )

    return parse_razer_pnp_records(
        record for record in decoded if isinstance(record, dict)
    )
