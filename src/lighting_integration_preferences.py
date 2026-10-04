#!/usr/bin/env python3
"""Persisted EDL lighting-integration preferences.

This module stores operator choices only. It does not discover devices, start
services, route targets, acquire hardware, or render lighting.
"""

from __future__ import annotations

from dataclasses import dataclass


OPENRGB_ENABLED_KEY = "integrations/openrgb_enabled"
CHROMA_ENABLED_KEY = "integrations/chroma_enabled"
GOVEE_H61C3_ENABLED_KEY = "integrations/govee_h61c3_enabled"
INTEGRATIONS_CONFIGURED_KEY = "integrations/configured"
DISCOVERY_ORDER_KEY = "integrations/discovery_order"

SOURCE_OPENRGB = "OpenRGB"
SOURCE_CHROMA = "Razer Chroma"
SOURCE_GOVEE = "Enhanced Govee H61C3"
ALL_DISCOVERY_SOURCES = (SOURCE_OPENRGB, SOURCE_CHROMA, SOURCE_GOVEE)


@dataclass(frozen=True)
class IntegrationPreferences:
    openrgb_enabled: bool = True
    chroma_enabled: bool = False
    govee_h61c3_enabled: bool = False


def _bool_setting(settings, key: str, default: bool) -> bool:
    value = settings.value(key, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}


def integration_preferences_configured(settings) -> bool:
    return _bool_setting(settings, INTEGRATIONS_CONFIGURED_KEY, False)


def load_integration_preferences(settings) -> IntegrationPreferences:
    return IntegrationPreferences(
        openrgb_enabled=_bool_setting(settings, OPENRGB_ENABLED_KEY, True),
        chroma_enabled=_bool_setting(settings, CHROMA_ENABLED_KEY, False),
        govee_h61c3_enabled=_bool_setting(settings, GOVEE_H61C3_ENABLED_KEY, False),
    )


def save_integration_preferences(settings, preferences: IntegrationPreferences) -> None:
    settings.setValue(OPENRGB_ENABLED_KEY, bool(preferences.openrgb_enabled))
    settings.setValue(CHROMA_ENABLED_KEY, bool(preferences.chroma_enabled))
    settings.setValue(GOVEE_H61C3_ENABLED_KEY, bool(preferences.govee_h61c3_enabled))
    settings.setValue(INTEGRATIONS_CONFIGURED_KEY, True)
    settings.sync()



def enabled_sources(preferences: IntegrationPreferences) -> tuple[str, ...]:
    result = []
    if preferences.openrgb_enabled:
        result.append(SOURCE_OPENRGB)
    if preferences.chroma_enabled:
        result.append(SOURCE_CHROMA)
    if preferences.govee_h61c3_enabled:
        result.append(SOURCE_GOVEE)
    return tuple(result)


def load_discovery_order(
    settings,
    preferences: IntegrationPreferences | None = None,
) -> tuple[str, ...]:
    preferences = preferences or load_integration_preferences(settings)
    enabled = enabled_sources(preferences)

    raw = settings.value(DISCOVERY_ORDER_KEY, "")
    if isinstance(raw, str):
        stored = tuple(
            value.strip()
            for value in raw.split("|")
            if value.strip() in ALL_DISCOVERY_SOURCES
        )
    elif isinstance(raw, (list, tuple)):
        stored = tuple(
            str(value)
            for value in raw
            if str(value) in ALL_DISCOVERY_SOURCES
        )
    else:
        stored = ()

    ordered = [source for source in stored if source in enabled]
    ordered.extend(source for source in enabled if source not in ordered)
    return tuple(ordered)


def save_discovery_order(settings, order: tuple[str, ...] | list[str]) -> None:
    normalized = []
    for source in order:
        if source in ALL_DISCOVERY_SOURCES and source not in normalized:
            normalized.append(source)
    settings.setValue(DISCOVERY_ORDER_KEY, "|".join(normalized))
    settings.sync()
