#!/usr/bin/env python3
"""Canonical Elite Dangerous Status.json decoding.

This module is intentionally pure: it decodes one already-loaded Status.json
object and does not watch files, talk to Chroma, import Virpil profiles, or
depend on the cockpit-startup repository.

Canonical source:
https://elite-journal.readthedocs.io/en/latest/Status%20File.html

Frontier's older Journal Manual v34 remains a useful historical cross-check:
https://hosting.zaonce.net/community/journal/v34/Journal_Manual_v34.pdf
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

STATUS_SOURCE_URL = (
    "https://elite-journal.readthedocs.io/en/latest/Status%20File.html"
)

# Canonical names preserve Frontier's documented meaning while removing spaces
# and punctuation so they are stable configuration keys.
FLAG_BITS: dict[str, int] = {
    "Docked": 0,
    "Landed": 1,
    "LandingGearDown": 2,
    "ShieldsUp": 3,
    "Supercruise": 4,
    "FlightAssistOff": 5,
    "HardpointsDeployed": 6,
    "InWing": 7,
    "LightsOn": 8,
    "CargoScoopDeployed": 9,
    "SilentRunning": 10,
    "ScoopingFuel": 11,
    "SrvHandbrake": 12,
    "SrvUsingTurretView": 13,
    "SrvTurretRetracted": 14,
    "SrvDriveAssist": 15,
    "FsdMassLocked": 16,
    "FsdCharging": 17,
    "FsdCooldown": 18,
    "LowFuel": 19,
    "OverHeating": 20,
    "HasLatLong": 21,
    "IsInDanger": 22,
    "BeingInterdicted": 23,
    "InMainShip": 24,
    "InFighter": 25,
    "InSRV": 26,
    "HudInAnalysisMode": 27,
    "NightVision": 28,
    "AltitudeFromAverageRadius": 29,
    "FsdJump": 30,
    "SrvHighBeam": 31,
}

FLAG2_BITS: dict[str, int] = {
    "OnFoot": 0,
    "InTaxi": 1,
    "InMulticrew": 2,
    "OnFootInStation": 3,
    "OnFootOnPlanet": 4,
    "AimDownSight": 5,
    "LowOxygen": 6,
    "LowHealth": 7,
    "Cold": 8,
    "Hot": 9,
    "VeryCold": 10,
    "VeryHot": 11,
    "GlideMode": 12,
    "OnFootInHangar": 13,
    "OnFootSocialSpace": 14,
    "OnFootExterior": 15,
    "BreathableAtmosphere": 16,
    "TelepresenceMulticrew": 17,
    "PhysicalMulticrew": 18,
    "FsdHyperdriveCharging": 19,
    "SupercruiseOverdriveActive": 20,
    "SupercruiseAssistActive": 21,
}

# Backward-compatible public aliases retained for existing project importers.
# They reference the same canonical maps and do not duplicate state authority.
FLAGS = FLAG_BITS
FLAGS2 = FLAG2_BITS

# Scalar Status.json values that are useful to rule evaluation now or later.
# Missing fields are represented as None instead of inventing a state.
SCALAR_KEYS: tuple[str, ...] = (
    "Timestamp",
    "Event",
    "Pips",
    "FireGroup",
    "GuiFocus",
    "FuelMain",
    "FuelReservoir",
    "Cargo",
    "LegalState",
    "Latitude",
    "Altitude",
    "Longitude",
    "Heading",
    "BodyName",
    "PlanetRadius",
    "Balance",
    "DestinationSystem",
    "DestinationBody",
    "DestinationName",
    "Oxygen",
    "Health",
    "Temperature",
    "SelectedWeapon",
    "Gravity",
)


def _require_bitfield(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer bitfield")
    if value < 0:
        raise ValueError(f"{key} must not be negative")
    return value


def decode_bitfield(value: int, definitions: Mapping[str, int]) -> dict[str, bool]:
    """Decode a non-negative integer bitfield using the supplied name->bit map."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("bitfield value must be an integer")
    if value < 0:
        raise ValueError("bitfield value must not be negative")

    return {
        name: bool(value & (1 << bit))
        for name, bit in definitions.items()
    }


def decode_status(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return one flat canonical state dictionary from a Status.json object.

    Flags and Flags2 are expanded to named booleans. Documented scalar values
    are copied without interpretation. Virpil-specific aliases and derived
    states are deliberately excluded from this canonical layer.
    """
    if not isinstance(payload, Mapping):
        raise TypeError("Status payload must be a mapping")

    flags = _require_bitfield(payload, "Flags")
    flags2 = _require_bitfield(payload, "Flags2")

    state: dict[str, Any] = {
        "RawFlags": flags,
        "RawFlags2": flags2,
    }
    state.update(decode_bitfield(flags, FLAG_BITS))
    state.update(decode_bitfield(flags2, FLAG2_BITS))

    fuel = payload.get("Fuel")
    if isinstance(fuel, Mapping):
        fuel_main = fuel.get("FuelMain")
        fuel_reservoir = fuel.get("FuelReservoir")
    else:
        fuel_main = None
        fuel_reservoir = None

    destination = payload.get("Destination")
    if isinstance(destination, Mapping):
        destination_system = destination.get("System")
        destination_body = destination.get("Body")
        destination_name = destination.get("Name")
    else:
        destination_system = None
        destination_body = None
        destination_name = None

    scalar_values = {
        "Timestamp": payload.get("timestamp"),
        "Event": payload.get("event"),
        "Pips": payload.get("Pips"),
        "FireGroup": payload.get("FireGroup"),
        "GuiFocus": payload.get("GuiFocus"),
        "FuelMain": fuel_main,
        "FuelReservoir": fuel_reservoir,
        "Cargo": payload.get("Cargo"),
        "LegalState": payload.get("LegalState"),
        "Latitude": payload.get("Latitude"),
        "Altitude": payload.get("Altitude"),
        "Longitude": payload.get("Longitude"),
        "Heading": payload.get("Heading"),
        "BodyName": payload.get("BodyName"),
        "PlanetRadius": payload.get("PlanetRadius"),
        "Balance": payload.get("Balance"),
        "DestinationSystem": destination_system,
        "DestinationBody": destination_body,
        "DestinationName": destination_name,
        "Oxygen": payload.get("Oxygen"),
        "Health": payload.get("Health"),
        "Temperature": payload.get("Temperature"),
        "SelectedWeapon": payload.get("SelectedWeapon"),
        "Gravity": payload.get("Gravity"),
    }
    state.update(scalar_values)

    return state
