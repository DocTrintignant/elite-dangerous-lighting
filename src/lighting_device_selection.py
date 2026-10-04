#!/usr/bin/env python3
"""Persisted selection state for discovered lighting devices.

This module stores only operator selection/review state. It does not discover
hardware, route targets, resize zones, or render lighting.
"""

from __future__ import annotations

import json
from typing import Iterable


DEVICE_SELECTED_IDS_KEY = "devices/selected_ids"
DEVICE_KNOWN_IDS_KEY = "devices/known_ids"
OPENRGB_SELECTED_IDS_KEY = "devices/openrgb_selected_ids"
OPENRGB_KNOWN_IDS_KEY = "devices/openrgb_known_ids"
SHOW_DEVICE_SELECTOR_AT_STARTUP_KEY = "devices/show_selector_at_startup"
DEVICE_CONTROL_ROUTES_KEY = "devices/control_routes"


def _bool_setting(settings, key: str, default: bool) -> bool:
    value = settings.value(key, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}


def show_device_selector_at_startup(settings) -> bool:
    return _bool_setting(settings, SHOW_DEVICE_SELECTOR_AT_STARTUP_KEY, True)


def set_show_device_selector_at_startup(settings, enabled: bool) -> None:
    settings.setValue(SHOW_DEVICE_SELECTOR_AT_STARTUP_KEY, bool(enabled))
    settings.sync()


def _load_id_set(settings, key: str) -> set[str]:
    value = settings.value(key, "")
    if isinstance(value, (list, tuple)):
        return {str(item) for item in value if str(item).strip()}
    if not isinstance(value, str) or not value.strip():
        return set()
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return set()
    if not isinstance(decoded, list):
        return set()
    return {str(item) for item in decoded if str(item).strip()}


def _save_id_set(settings, key: str, values: Iterable[str]) -> None:
    settings.setValue(key, json.dumps(sorted(set(values)), separators=(",", ":")))


def selected_openrgb_device_ids(settings) -> set[str]:
    return _load_id_set(settings, OPENRGB_SELECTED_IDS_KEY)


def known_openrgb_device_ids(settings) -> set[str]:
    return _load_id_set(settings, OPENRGB_KNOWN_IDS_KEY)


def openrgb_device_identity(device) -> str:
    """Build a stable-enough identity from OpenRGB metadata, never list index."""
    vendor = str(getattr(device, "vendor", "") or "").strip()
    name = str(getattr(device, "name", "") or "").strip()
    device_type = str(getattr(device, "device_type", "") or "").strip()
    serial = str(getattr(device, "serial", "") or "").strip()
    location = str(getattr(device, "location", "") or "").strip()
    description = str(getattr(device, "description", "") or "").strip()
    version = str(getattr(device, "version", "") or "").strip()

    if serial:
        anchor = f"serial={serial}"
    elif location:
        anchor = f"location={location}"
    else:
        anchor = f"description={description}|version={version}"

    return f"openrgb|{vendor}|{name}|{device_type}|{anchor}"


def save_openrgb_device_review(
    settings,
    discovered_ids: Iterable[str],
    selected_discovered_ids: Iterable[str],
) -> None:
    """Save reviewed selections while preserving absent previously selected devices."""
    discovered = set(discovered_ids)
    selected_now = set(selected_discovered_ids)
    previous_selected = selected_openrgb_device_ids(settings)
    previous_known = known_openrgb_device_ids(settings)

    retained_absent = previous_selected - discovered
    final_selected = retained_absent | selected_now
    final_known = previous_known | discovered

    _save_id_set(settings, OPENRGB_SELECTED_IDS_KEY, final_selected)
    _save_id_set(settings, OPENRGB_KNOWN_IDS_KEY, final_known)
    settings.sync()



def save_openrgb_physical_review(
    settings,
    physical_to_member_ids: dict[str, Iterable[str]],
    selected_physical_ids: Iterable[str],
) -> None:
    """Persist physical-device review and retire represented legacy member IDs."""
    mapping = {
        str(physical_id): {str(member) for member in members}
        for physical_id, members in physical_to_member_ids.items()
    }
    discovered_physical = set(mapping)
    represented_members = set().union(*mapping.values()) if mapping else set()
    selected_now = set(selected_physical_ids)

    previous_selected = selected_openrgb_device_ids(settings)
    previous_known = known_openrgb_device_ids(settings)

    retained_absent = previous_selected - discovered_physical - represented_members
    final_selected = retained_absent | selected_now
    final_known = (previous_known - represented_members) | discovered_physical

    _save_id_set(settings, OPENRGB_SELECTED_IDS_KEY, final_selected)
    _save_id_set(settings, OPENRGB_KNOWN_IDS_KEY, final_known)
    settings.sync()



def selected_device_ids(settings) -> set[str]:
    """Return current general selections, falling back to legacy OpenRGB storage."""
    current = _load_id_set(settings, DEVICE_SELECTED_IDS_KEY)
    if current:
        return current
    return selected_openrgb_device_ids(settings)


def known_device_ids(settings) -> set[str]:
    """Return current general known IDs, falling back to legacy OpenRGB storage."""
    current = _load_id_set(settings, DEVICE_KNOWN_IDS_KEY)
    if current:
        return current
    return known_openrgb_device_ids(settings)


def save_device_review(
    settings,
    discovered_to_aliases: dict[str, Iterable[str]],
    selected_discovered_ids: Iterable[str],
    explicitly_unselected_ids: Iterable[str] = (),
) -> None:
    """Persist selected physical/candidate IDs across all discovery sources.

    Aliases are legacy/raw controller IDs represented by one current candidate.
    They are retired when the candidate is reviewed so selections migrate cleanly.
    """
    mapping = {
        str(identity): {str(alias) for alias in aliases}
        for identity, aliases in discovered_to_aliases.items()
    }
    discovered = set(mapping)
    represented_aliases = set().union(*mapping.values()) if mapping else set()
    selected_now = set(selected_discovered_ids)
    explicitly_unselected = set(explicitly_unselected_ids)

    previous_selected = selected_device_ids(settings)
    previous_known = known_device_ids(settings)

    retained_absent = (
        previous_selected
        - discovered
        - represented_aliases
        - explicitly_unselected
    )
    final_selected = (retained_absent | selected_now) - explicitly_unselected
    final_known = (previous_known - represented_aliases) | discovered

    _save_id_set(settings, DEVICE_SELECTED_IDS_KEY, final_selected)
    _save_id_set(settings, DEVICE_KNOWN_IDS_KEY, final_known)

    # Once the general catalogue has been saved, clear legacy OpenRGB-only keys.
    settings.remove(OPENRGB_SELECTED_IDS_KEY)
    settings.remove(OPENRGB_KNOWN_IDS_KEY)
    settings.sync()



def device_control_routes(settings) -> dict[str, str]:
    """Return persisted physical-device renderer ownership choices."""
    value = settings.value(DEVICE_CONTROL_ROUTES_KEY, "")
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return {}
    if not isinstance(decoded, dict):
        return {}
    return {
        str(identity): str(route)
        for identity, route in decoded.items()
        if str(identity).strip() and str(route).strip()
    }


def save_device_control_route_review(
    settings,
    discovered_to_aliases: dict[str, Iterable[str]],
    reviewed_overlap_routes: dict[str, str],
) -> None:
    """Persist confirmed renderer ownership for currently overlapping devices.

    Current physical identities retire represented legacy/raw aliases. Existing
    confirmed ownership survives later scans where only one route is currently
    reachable; a temporary backend outage must never erase ownership or silently
    promote the remaining route. A single-route device with no prior ownership
    still creates no preference of its own.
    """
    mapping = {
        str(identity): {str(alias) for alias in aliases}
        for identity, aliases in discovered_to_aliases.items()
    }
    discovered = set(mapping)
    represented_aliases = set().union(*mapping.values()) if mapping else set()

    previous = device_control_routes(settings)
    retained = {
        identity: route
        for identity, route in previous.items()
        if identity not in discovered and identity not in represented_aliases
    }

    # Preserve/migrate an already-confirmed owner for every currently discovered
    # physical device even when only one backend is reachable in this scan.
    for identity, aliases in mapping.items():
        route = previous.get(identity)
        if not route:
            route = next(
                (previous[alias] for alias in aliases if alias in previous),
                None,
            )
        if route:
            retained[identity] = route

    # An overlap row reviewed in this dialog is the only thing allowed to change
    # an existing owner or create a new one.
    for identity, route in reviewed_overlap_routes.items():
        identity = str(identity)
        route = str(route).strip()
        if identity in discovered and route:
            retained[identity] = route

    settings.setValue(
        DEVICE_CONTROL_ROUTES_KEY,
        json.dumps(dict(sorted(retained.items())), separators=(",", ":")),
    )
    settings.sync()
