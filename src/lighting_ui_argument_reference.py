#!/usr/bin/env python3
"""Human-facing Elite Argument catalogue for the desktop rule editor.

This module is deliberately limited to the UI/reference boundary. It does not
change Status decoding, rule evaluation, Virpil import, effects, runtime state,
or Chroma transport.

The catalogue gives each stable EDL Argument identifier a readable display name
and a teaching-first tooltip. RawFlags/RawFlags2 are intentionally omitted from
normal rule creation; they remain decoder/diagnostic data for the later Rule
Simulator.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox

WIP_OPENING = (
    "In development — EDL can read this Elite Dangerous value, but it is not "
    "supported or validated as a Rule input in V1. Do not use it for active "
    "lighting rules."
)

WIP_ARGUMENTS = {
    "Balance",
    "BodyName",
    "DestinationBody",
    "DestinationName",
    "DestinationSystem",
    "Event",
    "FireGroup",
    "Heading",
    "Pips",
    "SelectedWeapon",
    "Timestamp",
}

VIRPIL_ALIASES = {
    "FsdCharging": "FSD_Charging",
    "FsdJump": "FSD_Jumping",
    "HasLatLong": "HasLat_Long",
    "FuelMain": "Fuel_Main",
    "FuelReservoir": "Fuel_Reservoir",
    "IsInDanger": "InDanger",
    "LandingGearDown": "GearDown",
}

DISPLAY_NAMES = {
    "AimDownSight": "Aim down sights",
    "Altitude": "Altitude",
    "AltitudeFromAverageRadius": "Altitude from average radius",
    "Balance": "Balance",
    "BeingInterdicted": "Being interdicted",
    "BodyName": "Body name",
    "BreathableAtmosphere": "Breathable atmosphere",
    "Cargo": "Cargo",
    "CargoScoopDeployed": "Cargo scoop deployed",
    "Cold": "Cold",
    "DestinationBody": "Destination body",
    "DestinationName": "Destination name",
    "DestinationSystem": "Destination system",
    "Docked": "Docked",
    "Event": "Event",
    "FireGroup": "Fire group",
    "FlightAssistOff": "Flight assist off",
    "FsdCharging": "FSD charging",
    "FsdCooldown": "FSD cooldown",
    "FsdHyperdriveCharging": "FSD hyperdrive charging",
    "FsdJump": "FSD jump",
    "FsdMassLocked": "FSD mass locked",
    "FuelMain": "Main fuel",
    "FuelReservoir": "Reservoir fuel",
    "GlideMode": "Glide mode",
    "Gravity": "Gravity",
    "GuiFocus": "GUI focus",
    "HardpointsDeployed": "Hardpoints deployed",
    "HasLatLong": "Has latitude/longitude",
    "Heading": "Heading",
    "Health": "Health",
    "Hot": "Hot",
    "HudInAnalysisMode": "HUD analysis mode",
    "InFighter": "In fighter",
    "InMainShip": "In main ship",
    "InMulticrew": "In multicrew",
    "InSRV": "In SRV",
    "InTaxi": "In taxi",
    "InWing": "In wing",
    "IsInDanger": "In danger",
    "Landed": "Landed",
    "LandingGearDown": "Gear down",
    "Latitude": "Latitude",
    "LegalState": "Legal state",
    "LightsOn": "Lights on",
    "Longitude": "Longitude",
    "LowFuel": "Low fuel",
    "LowHealth": "Low health",
    "LowOxygen": "Low oxygen",
    "NightVision": "Night vision",
    "OnFoot": "On foot",
    "OnFootExterior": "On foot outside",
    "OnFootInHangar": "On foot in hangar",
    "OnFootInStation": "On foot in station",
    "OnFootOnPlanet": "On foot on planet",
    "OnFootSocialSpace": "On foot in social space",
    "OverHeating": "Overheating",
    "Oxygen": "Oxygen",
    "PhysicalMulticrew": "Physical multicrew",
    "Pips": "Pips",
    "PlanetRadius": "Planet radius",
    "ScoopingFuel": "Scooping fuel",
    "SelectedWeapon": "Selected weapon",
    "ShieldsUp": "Shields up",
    "SilentRunning": "Silent running",
    "SrvDriveAssist": "SRV drive assist",
    "SrvHandbrake": "SRV handbrake",
    "SrvHighBeam": "SRV high beam",
    "SrvTurretRetracted": "SRV turret retracted",
    "SrvUsingTurretView": "SRV turret view",
    "Supercruise": "Supercruise",
    "SupercruiseAssistActive": "Supercruise assist active",
    "SupercruiseOverdriveActive": "Supercruise overdrive active",
    "TelepresenceMulticrew": "Telepresence multicrew",
    "Temperature": "Temperature",
    "Timestamp": "Timestamp",
    "VeryCold": "Very cold",
    "VeryHot": "Very hot",
}

# Frontier's published wording is retained only in the technical footer. Bit
# numbers are deliberately not shown in normal tooltips: they identify storage,
# not meaning, and made the UI harder rather than easier to understand.
FLAG_LABELS = {
    "Docked": ("Flags", "Docked"),
    "Landed": ("Flags", "Landed"),
    "LandingGearDown": ("Flags", "Landing Gear Down"),
    "ShieldsUp": ("Flags", "Shields Up"),
    "Supercruise": ("Flags", "Supercruise"),
    "FlightAssistOff": ("Flags", "FlightAssist Off"),
    "HardpointsDeployed": ("Flags", "Hardpoints Deployed"),
    "InWing": ("Flags", "In Wing"),
    "LightsOn": ("Flags", "LightsOn"),
    "CargoScoopDeployed": ("Flags", "Cargo Scoop Deployed"),
    "SilentRunning": ("Flags", "Silent Running"),
    "ScoopingFuel": ("Flags", "Scooping Fuel"),
    "SrvHandbrake": ("Flags", "Srv Handbrake"),
    "SrvUsingTurretView": ("Flags", "Srv using Turret view"),
    "SrvTurretRetracted": ("Flags", "Srv Turret retracted (close to ship)"),
    "SrvDriveAssist": ("Flags", "Srv DriveAssist"),
    "FsdMassLocked": ("Flags", "Fsd MassLocked"),
    "FsdCharging": ("Flags", "Fsd Charging"),
    "FsdCooldown": ("Flags", "Fsd Cooldown"),
    "LowFuel": ("Flags", "Low Fuel"),
    "OverHeating": ("Flags", "Over Heating"),
    "HasLatLong": ("Flags", "Has Lat Long"),
    "IsInDanger": ("Flags", "IsInDanger"),
    "BeingInterdicted": ("Flags", "Being Interdicted"),
    "InMainShip": ("Flags", "In MainShip"),
    "InFighter": ("Flags", "In Fighter"),
    "InSRV": ("Flags", "In SRV"),
    "HudInAnalysisMode": ("Flags", "Hud in Analysis mode"),
    "NightVision": ("Flags", "Night Vision"),
    "AltitudeFromAverageRadius": ("Flags", "Altitude from Average radius"),
    "FsdJump": ("Flags", "fsdJump"),
    "SrvHighBeam": ("Flags", "srvHighBeam"),
    "OnFoot": ("Flags2", "OnFoot"),
    "InTaxi": ("Flags2", "InTaxi (or dropship/shuttle)"),
    "InMulticrew": ("Flags2", "InMulticrew (ie in someone else's ship)"),
    "OnFootInStation": ("Flags2", "OnFootInStation"),
    "OnFootOnPlanet": ("Flags2", "OnFootOnPlanet"),
    "AimDownSight": ("Flags2", "AimDownSight"),
    "LowOxygen": ("Flags2", "LowOxygen"),
    "LowHealth": ("Flags2", "LowHealth"),
    "Cold": ("Flags2", "Cold"),
    "Hot": ("Flags2", "Hot"),
    "VeryCold": ("Flags2", "VeryCold"),
    "VeryHot": ("Flags2", "VeryHot"),
    "GlideMode": ("Flags2", "Glide Mode"),
    "OnFootInHangar": ("Flags2", "OnFootInHangar"),
    "OnFootSocialSpace": ("Flags2", "OnFootSocialSpace"),
    "OnFootExterior": ("Flags2", "OnFootExterior"),
    "BreathableAtmosphere": ("Flags2", "BreathableAtmosphere"),
    "TelepresenceMulticrew": ("Flags2", "Telepresence Multicrew"),
    "PhysicalMulticrew": ("Flags2", "Physical Multicrew"),
    "FsdHyperdriveCharging": ("Flags2", "Fsd hyperdrive charging"),
    "SupercruiseOverdriveActive": ("Flags2", "Supercruise Overdrive (SCO) Active"),
    "SupercruiseAssistActive": ("Flags2", "Supercruise Assist Active"),
}

BOOLEAN_MEANINGS = {
    "AimDownSight": (
        "True while you are on foot and aiming a weapon down its sights. "
        "Useful for changing lighting while you are actively aiming."
    ),
    "AltitudeFromAverageRadius": (
        "Tells you how Elite is calculating the Altitude number. At higher altitudes Elite can measure height from the planet's average spherical radius instead of the terrain directly below you. "
        "True means the average-radius method is being used; False means Elite is measuring to the actual surface below the ship or SRV."
    ),
    "BeingInterdicted": (
        "True while another ship is actively pulling you out of Supercruise in an interdiction. "
        "Useful for an immediate interdiction warning effect."
    ),
    "BreathableAtmosphere": (
        "True when Elite says the atmosphere around you is breathable while on foot. "
        "Useful for suit or environmental lighting that distinguishes safe air from suit-dependent environments."
    ),
    "CargoScoopDeployed": "True while the ship's cargo scoop is deployed.",
    "Cold": (
        "True when Elite classifies your current on-foot thermal condition as Cold. "
        "Frontier does not publish the exact temperature threshold for this state, so let Elite decide when it applies."
    ),
    "Docked": "True while your ship is docked on a landing pad.",
    "FlightAssistOff": "True while Flight Assist is switched off.",
    "FsdCharging": (
        "True while Elite's general FSD charging state is active. "
        "Use this for lighting that should react whenever the Frame Shift Drive is charging."
    ),
    "FsdCooldown": "True while the Frame Shift Drive is cooling down and cannot yet be charged again.",
    "FsdHyperdriveCharging": (
        "True while the FSD is charging specifically for a hyperspace jump to another star system. "
        "This is more specific than FSD charging, which is the broader charging state."
    ),
    "FsdJump": (
        "True during Elite's FSD-jump state. "
        "Use it when you want lighting tied to the jump transition itself rather than only to the charging period."
    ),
    "FsdMassLocked": (
        "True while nearby mass is preventing the FSD from being used. "
        "Typical causes include being too close to a station, planet, or other large mass."
    ),
    "GlideMode": "True while the ship is in planetary glide during a high-speed approach toward a surface.",
    "HardpointsDeployed": "True while the ship's hardpoints are deployed.",
    "HasLatLong": (
        "True when Elite is providing usable latitude and longitude for your current position. "
        "Use this as a guard if a rule depends on Latitude or Longitude."
    ),
    "Hot": (
        "True when Elite classifies your current on-foot thermal condition as Hot. "
        "Frontier does not publish the exact temperature threshold for this state."
    ),
    "HudInAnalysisMode": "True while the ship HUD is in Analysis mode rather than Combat mode.",
    "InFighter": "True while you are controlling a ship-launched fighter rather than your main ship.",
    "InMainShip": "True while you are in your main ship.",
    "InMulticrew": "True while you are participating in multicrew. Frontier describes this general state as being in someone else's ship.",
    "InSRV": "True while you are in an SRV on a planetary surface.",
    "InTaxi": "True while you are travelling in an Apex-style taxi, dropship, or shuttle.",
    "InWing": "True while you are currently in a wing.",
    "IsInDanger": (
        "Elite provides a True/False state called Is In Danger. When it is True, the game has flagged your current situation as dangerous. "
        "Frontier does not document exactly which situations turn this state on or off, so do not assume it means only combat, interdiction, low shields, or any other single condition. "
        "Use this if you want your lighting to follow Elite's own danger flag."
    ),
    "Landed": "True while the ship is landed on a planetary surface.",
    "LandingGearDown": "True while the landing gear is deployed.",
    "LightsOn": "True while the ship or vehicle lights are switched on.",
    "LowFuel": (
        "Elite's own low-fuel warning. It becomes True when fuel falls below 25%. "
        "This is a warning state, not a fuel quantity in tons; use Main fuel if you want your own numeric threshold."
    ),
    "LowHealth": (
        "True when Elite considers your on-foot health to be low. "
        "Frontier does not publish the exact Health value that switches this warning on."
    ),
    "LowOxygen": (
        "True when Elite considers your on-foot oxygen to be low. "
        "Frontier does not publish the exact Oxygen value that switches this warning on."
    ),
    "NightVision": "True while night vision is switched on.",
    "OnFoot": "True while your commander is on foot rather than inside a ship, SRV, or taxi.",
    "OnFootExterior": "True while you are on foot outside an interior or social space.",
    "OnFootInHangar": "True while you are on foot inside a hangar.",
    "OnFootInStation": "True while you are on foot inside a station.",
    "OnFootOnPlanet": "True while you are on foot on a planetary surface.",
    "OnFootSocialSpace": "True while you are on foot in a station or settlement social space.",
    "OverHeating": "Elite's ship overheating warning. Frontier defines this state as True above 100% heat.",
    "PhysicalMulticrew": "True while you are physically present as part of a multicrew crew rather than joining by telepresence.",
    "ScoopingFuel": "True while the ship is actively scooping fuel from a star.",
    "ShieldsUp": "True while the ship's shields are up.",
    "SilentRunning": "True while Silent Running is active.",
    "SrvDriveAssist": "True while SRV Drive Assist is switched on.",
    "SrvHandbrake": "True while the SRV handbrake is engaged.",
    "SrvHighBeam": "True while the SRV high-beam lights are active.",
    "SrvTurretRetracted": "True while the SRV turret is retracted. Frontier notes this state in the context of being close to the ship.",
    "SrvUsingTurretView": "True while you are using the SRV turret view.",
    "Supercruise": "True while the ship is travelling in Supercruise.",
    "SupercruiseAssistActive": (
        "True when Supercruise Assist is not merely enabled but actively controlling the ship. "
        "Frontier specifies that the ship must be aligned and the throttle must be in the blue zone."
    ),
    "SupercruiseOverdriveActive": "True while Supercruise Overdrive (SCO) is active.",
    "TelepresenceMulticrew": "True while you are participating in multicrew through telepresence rather than being physically present aboard the ship.",
    "VeryCold": (
        "True when Elite classifies your current on-foot thermal condition as Very cold. "
        "Frontier does not publish the exact temperature threshold for this state."
    ),
    "VeryHot": (
        "True when Elite classifies your current on-foot thermal condition as Very hot. "
        "Frontier does not publish the exact temperature threshold for this state."
    ),
}


@dataclass(frozen=True)
class ScalarReference:
    frontier_json: str
    meaning: str
    value: str
    extra: str = ""
    wip_reason: str = ""


SCALAR_REFERENCES = {
    "Altitude": ScalarReference(
        "Altitude",
        (
            "How high your ship or SRV is above the planetary surface. Normally Elite measures this against the terrain directly beneath you. "
            "At higher altitudes it can instead measure height from the planet's average radius; Altitude from average radius tells you when that alternative calculation is being used."
        ),
        "Number",
        "Frontier's Status reference does not state the unit, so EDL does not invent one in the editor.",
    ),
    "Balance": ScalarReference(
        "Balance",
        "Elite supplies a number called Balance in Status.json, but Frontier's Status reference does not explain what that number represents.",
        "Number",
        wip_reason="Until we verify what Balance means in live game data, a user cannot choose a deliberate lighting threshold with confidence.",
    ),
    "BodyName": ScalarReference(
        "BodyName",
        "The name Elite reports for the planetary or stellar body associated with your current location.",
        "Text",
        wip_reason="The meaning is clear, but we still need to verify the exact emitted text and freeze EDL's text-matching/case behaviour before this becomes a reliable rule input.",
    ),
    "Cargo": ScalarReference(
        "Cargo",
        "How much cargo mass your ship is currently carrying.",
        "Number in tons",
        "Example: 32 means 32 tons of cargo.",
    ),
    "DestinationBody": ScalarReference(
        "Destination.Body",
        "The body component of Elite's current navigation destination.",
        "Not yet verified",
        wip_reason="Frontier lists Destination.Body but does not document its value type or format. We need a live local capture before deciding what the user should enter.",
    ),
    "DestinationName": ScalarReference(
        "Destination.Name",
        "The name component of Elite's current navigation destination.",
        "Text-like value; exact contract not yet verified",
        wip_reason="We need live examples and a defined text-matching contract before this can be used deliberately and repeatably in rules.",
    ),
    "DestinationSystem": ScalarReference(
        "Destination.System",
        "The star-system component of Elite's current navigation destination.",
        "Not yet verified",
        wip_reason="Frontier lists Destination.System but does not document its value type or format. We must verify live output before deciding whether the rule input is a name, numeric address, or something else.",
    ),
    "Event": ScalarReference(
        "event",
        "The JSON record type. In Status.json Elite normally writes the value Status, identifying the file as a Status record rather than describing an in-game condition.",
        "Text",
        wip_reason="We do not yet have a useful lighting-rule purpose for this field. A rule that only tests whether Status.json says 'Status' adds no meaningful game-state distinction.",
    ),
    "FireGroup": ScalarReference(
        "FireGroup",
        "The number Elite reports for the currently selected ship fire group.",
        "Integer",
        wip_reason="We still need to verify the numbering/indexing behaviour and how to present fire groups so a user can intentionally choose the group they mean.",
    ),
    "FuelMain": ScalarReference(
        "Fuel.FuelMain",
        "How much fuel is currently in the ship's main fuel tank.",
        "Number in tons",
        "Use this when you want your own fuel threshold in tons. Low fuel is a separate Frontier warning state that becomes True below 25%.",
    ),
    "FuelReservoir": ScalarReference(
        "Fuel.FuelReservoir",
        "How much fuel Elite reports in the ship's fuel reservoir.",
        "Number in tons",
    ),
    "Gravity": ScalarReference(
        "Gravity",
        "Strength of gravity at your current on-foot location, compared with Earth gravity.",
        "Number relative to Earth gravity",
        "Examples: 1.0 = Earth-like gravity; 0.5 = half Earth gravity; 2.0 = twice Earth gravity.",
    ),
    "GuiFocus": ScalarReference(
        "GuiFocus",
        "Which major cockpit or game interface currently has focus.",
        "Enumeration 0–11",
        "Frontier values: 0 No focus; 1 Internal panel (right); 2 External panel (left); 3 Comms panel; 4 Role panel; 5 Station services; 6 Galaxy map; 7 System map; 8 Orrery; 9 FSS mode; 10 SAA mode; 11 Codex.",
    ),
    "Heading": ScalarReference(
        "Heading",
        "The direction your ship or SRV is facing around the local horizon while near a planetary body.",
        "Number",
        wip_reason="Frontier's Status reference does not define the unit, range, or reference convention for Heading. We need to verify those mechanics before asking the user to choose numeric thresholds.",
    ),
    "Health": ScalarReference(
        "Health",
        "Your current on-foot health level.",
        "Number from 0.0 to 1.0",
        "1.0 means full health; 0.5 means half; 0.0 means depleted. Low health is a separate Elite warning state with an undocumented trigger threshold.",
    ),
    "Latitude": ScalarReference(
        "Latitude",
        "Your north/south position on a planetary body when Elite has latitude/longitude data available.",
        "Degrees",
        "Frontier says Status.json updates after a latitude/longitude change of about 0.02° while flying or 0.0005° in an SRV. Has latitude/longitude tells you when these coordinates are available.",
    ),
    "LegalState": ScalarReference(
        "LegalState",
        "Elite's current legal-state label for you in the present context.",
        "Named state",
        "Frontier values: Clean, IllegalCargo, Speeding, Wanted, Hostile, PassengerWanted, Warrant, Allied, Thargoid.",
    ),
    "Longitude": ScalarReference(
        "Longitude",
        "Your east/west position on a planetary body when Elite has latitude/longitude data available.",
        "Degrees",
        "Frontier says Status.json updates after a latitude/longitude change of about 0.02° while flying or 0.0005° in an SRV. Has latitude/longitude tells you when these coordinates are available.",
    ),
    "Oxygen": ScalarReference(
        "Oxygen",
        "Your current on-foot oxygen level.",
        "Number from 0.0 to 1.0",
        "1.0 means full; 0.5 means half; 0.0 means depleted. Low oxygen is a separate Elite warning state with an undocumented trigger threshold.",
    ),
    "Pips": ScalarReference(
        "Pips",
        "Your power-distributor allocation across SYS, ENG, and WEP.",
        "Three values stored together",
        "Elite reports the values in half-pips. Example: [2, 8, 2] means 1 SYS pip, 4 ENG pips, and 1 WEP pip.",
        wip_reason="EDL reads the three values, but the normal rule editor does not yet have a deliberate way to choose SYS, ENG, or WEP as the component to compare.",
    ),
    "PlanetRadius": ScalarReference(
        "PlanetRadius",
        "The radius/size value Elite reports for the current planetary body.",
        "Number",
        "Frontier's Status reference does not state the unit, so EDL does not label one here.",
    ),
    "SelectedWeapon": ScalarReference(
        "SelectedWeapon",
        "The weapon Elite reports as currently selected while you are on foot.",
        "Text",
        wip_reason="We still need live examples of the exact weapon strings and a frozen text-matching/case policy before users can build reliable rules from them.",
    ),
    "Temperature": ScalarReference(
        "Temperature",
        "The environmental temperature Elite reports while you are on foot.",
        "Kelvin",
        "For orientation: 273.15 K = 0°C and about 293 K = 20°C. Elite separately exposes Cold, Hot, Very cold, and Very hot states; Frontier does not publish their exact temperature thresholds.",
    ),
    "Timestamp": ScalarReference(
        "timestamp",
        "The time at which Elite wrote the current Status.json snapshot.",
        "ISO-8601 time in GMT/UTC form",
        wip_reason="EDL can read the timestamp, but useful rule semantics such as before/after, elapsed time, or age have not been defined. Raw text comparison would not be a sensible lighting control.",
    ),
}


@dataclass(frozen=True)
class ArgumentReference:
    edl_name: str
    display_name: str
    tooltip_html: str
    wip: bool


def _technical_footer(edl_name: str, frontier_source: str, frontier_label: str | None = None) -> str:
    source = escape(frontier_source)
    if frontier_label:
        source += f" — {escape(frontier_label)}"
    parts = [
        f"<b>Frontier source:</b> <code>{source}</code>",
        f"<b>EDL name:</b> <code>{escape(edl_name)}</code>",
    ]
    alias = VIRPIL_ALIASES.get(edl_name)
    if alias:
        parts.append(f"<b>VIRPIL name:</b> <code>{escape(alias)}</code>")
    return "<br>".join(parts)


def _boolean_tooltip(edl_name: str) -> str:
    display_name = DISPLAY_NAMES[edl_name]
    source, label = FLAG_LABELS[edl_name]
    meaning = BOOLEAN_MEANINGS[edl_name]
    return (
        "<qt>"
        f"<b>{escape(display_name)}</b><br><br>"
        f"{escape(meaning)}<br><br>"
        "<b>Value:</b> True / False"
        "<br><br><hr>"
        f"{_technical_footer(edl_name, source, label)}"
        "</qt>"
    )


def _scalar_tooltip(edl_name: str, reference: ScalarReference) -> str:
    display_name = DISPLAY_NAMES[edl_name]
    pieces = [
        "<qt>",
        f"<b>{escape(display_name)}</b><br><br>",
        escape(reference.meaning),
        "<br><br>",
        f"<b>Value:</b> {escape(reference.value)}",
    ]
    if reference.extra:
        pieces.extend(("<br><br>", escape(reference.extra)))
    if reference.wip_reason:
        pieces.extend(
            (
                "<br><br>",
                f"<span style='color:#E06C75'><b>{escape(WIP_OPENING)}</b></span>",
                "<br>",
                escape(reference.wip_reason),
            )
        )
    pieces.extend(
        (
            "<br><br><hr>",
            _technical_footer(edl_name, f"JSON {reference.frontier_json}"),
            "</qt>",
        )
    )
    return "".join(pieces)


def build_catalogue() -> dict[str, ArgumentReference]:
    catalogue: dict[str, ArgumentReference] = {}
    for edl_name in FLAG_LABELS:
        catalogue[edl_name] = ArgumentReference(
            edl_name=edl_name,
            display_name=DISPLAY_NAMES[edl_name],
            tooltip_html=_boolean_tooltip(edl_name),
            wip=edl_name in WIP_ARGUMENTS,
        )
    for edl_name, reference in SCALAR_REFERENCES.items():
        catalogue[edl_name] = ArgumentReference(
            edl_name=edl_name,
            display_name=DISPLAY_NAMES[edl_name],
            tooltip_html=_scalar_tooltip(edl_name, reference),
            wip=edl_name in WIP_ARGUMENTS,
        )
    return catalogue


ARGUMENT_REFERENCES = build_catalogue()
DISPLAY_TO_EDL = {
    reference.display_name.casefold(): edl_name
    for edl_name, reference in ARGUMENT_REFERENCES.items()
}
EDL_FOLDED = {edl_name.casefold(): edl_name for edl_name in ARGUMENT_REFERENCES}
VIRPIL_FOLDED = {alias.casefold(): edl_name for edl_name, alias in VIRPIL_ALIASES.items()}


def display_name_for(edl_name: str) -> str:
    reference = ARGUMENT_REFERENCES.get(edl_name)
    return reference.display_name if reference else edl_name


def resolve_edl_name(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return stripped
    folded = stripped.casefold()
    return (
        DISPLAY_TO_EDL.get(folded)
        or EDL_FOLDED.get(folded)
        or VIRPIL_FOLDED.get(folded)
        or stripped
    )


def _kind_for(base: Any, text: str) -> str:
    edl_name = resolve_edl_name(text)
    if edl_name in base.BOOLEAN_ARGUMENTS:
        return "bool"
    if edl_name in base.NUMERIC_ARGUMENTS:
        return "numeric"
    return "text"


def apply_argument_reference_ui(app_module: Any) -> None:
    """Apply the catalogue to the existing refined UI without touching engine code."""

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPalette

    base = app_module.base
    row_class = app_module.HumanArgumentConditionRow
    argument_condition = app_module.ArgumentCondition

    expected = set(base.NORMAL_ARGUMENTS) - {"RawFlags", "RawFlags2"}
    missing = expected - set(ARGUMENT_REFERENCES)
    extra = set(ARGUMENT_REFERENCES) - expected
    if missing or extra:
        raise RuntimeError(
            "Argument reference catalogue does not match the canonical UI source set: "
            f"missing={sorted(missing)!r}, extra={sorted(extra)!r}"
        )

    def populate_sources(self) -> None:
        self.source.clear()
        names = [
            name
            for name in base.NORMAL_ARGUMENTS
            if name not in {"RawFlags", "RawFlags2"}
        ]
        names.sort(key=lambda name: display_name_for(name).casefold())
        for edl_name in names:
            reference = ARGUMENT_REFERENCES.get(edl_name)
            self.source.addItem(display_name_for(edl_name), edl_name)
            index = self.source.count() - 1
            if reference is not None:
                self.source.setItemData(index, reference.tooltip_html, Qt.ItemDataRole.ToolTipRole)
                if reference.wip:
                    self.source.setItemData(index, QColor("#E06C75"), Qt.ItemDataRole.ForegroundRole)

    def source_kind(name: str) -> str:
        return _kind_for(base, name)

    def current_edl_name(self, visible_text: str | None = None) -> str:
        text = self.source.currentText() if visible_text is None else visible_text
        index = self.source.currentIndex()
        if index >= 0 and self.source.currentText().strip() == text.strip():
            data = self.source.itemData(index, Qt.ItemDataRole.UserRole)
            if isinstance(data, str) and data:
                return data
        return resolve_edl_name(text)

    def update_visible_reference(self, edl_name: str) -> None:
        reference = ARGUMENT_REFERENCES.get(edl_name)
        if reference is None:
            self.source.setToolTip(
                f"Uncatalogued Argument: {edl_name}. The value is preserved, but no user reference has been written yet."
            )
        else:
            self.source.setToolTip(reference.tooltip_html)

        line_edit = self.source.lineEdit()
        if line_edit is None:
            return
        if not hasattr(self, "_reference_normal_text_colour"):
            self._reference_normal_text_colour = line_edit.palette().color(QPalette.ColorRole.Text)
        palette = line_edit.palette()
        colour = QColor("#E06C75") if reference is not None and reference.wip else self._reference_normal_text_colour
        palette.setColor(QPalette.ColorRole.Text, colour)
        line_edit.setPalette(palette)

    def source_changed(self, visible_name: str) -> None:
        was_loading = self._loading
        self._loading = True
        try:
            edl_name = current_edl_name(self, visible_name)
            kind = _kind_for(base, edl_name)
            previous = self.operator.currentText()
            self.operator.clear()
            if kind == "numeric":
                self.operator.addItems(base.SUPPORTED_OPERATORS)
            elif kind == "text":
                self.operator.addItems(("Equal", "Not Equal"))

            if kind != "bool":
                index = self.operator.findText(previous)
                if index >= 0:
                    self.operator.setCurrentIndex(index)

            self.bool_relation.setVisible(kind == "bool")
            self.operator.setVisible(kind != "bool")
            self.value_stack.setCurrentIndex(0 if kind == "bool" else 1 if kind == "numeric" else 2)
            update_visible_reference(self, edl_name)
        finally:
            self._loading = was_loading
        if not was_loading:
            self._emit_changed()

    def set_condition(self, condition) -> None:
        self._loading = True
        try:
            wanted = condition.source
            index = -1
            for candidate in range(self.source.count()):
                if self.source.itemData(candidate, Qt.ItemDataRole.UserRole) == wanted:
                    index = candidate
                    break
            if index < 0:
                self.source.addItem(display_name_for(wanted), wanted)
                index = self.source.count() - 1
            self.source.setCurrentIndex(index)
            source_changed(self, self.source.currentText())

            kind = _kind_for(base, wanted)
            if kind == "bool":
                value = condition.value is True
                if condition.operator == "Not Equal":
                    value = not value
                self._stored_bool_operator = "Equal"
                self.bool_value.setCurrentText("True" if value else "False")
            else:
                op_index = self.operator.findText(condition.operator)
                if op_index < 0:
                    self.operator.addItem(condition.operator)
                    op_index = self.operator.findText(condition.operator)
                self.operator.setCurrentIndex(op_index)
                if kind == "numeric":
                    self.numeric_value.setText(str(condition.value))
                else:
                    self.text_value.setText("" if condition.value is None else str(condition.value))
            update_visible_reference(self, wanted)
        finally:
            self._loading = False

    def condition(self):
        source = current_edl_name(self)
        kind = _kind_for(base, source)
        if not source:
            raise ValueError("Argument condition requires a field")

        if kind == "bool":
            return argument_condition(source, "Equal", self.bool_value.currentText() == "True")

        operator = self.operator.currentText().strip()
        if kind == "numeric":
            text = self.numeric_value.text().strip()
            if not text:
                raise ValueError(f"{display_name_for(source)} requires a numeric value")
            try:
                value: object = int(text, 10)
            except ValueError:
                value = float(text)
        else:
            value = self.text_value.text()
        return argument_condition(source, operator, value)

    row_class._populate_sources = populate_sources
    row_class._source_kind = staticmethod(source_kind)
    row_class._source_changed = source_changed
    row_class.set_condition = set_condition
    row_class.condition = condition


# Named categorical Argument editing belongs to the same source-reference owner.
GUI_FOCUS_VALUES = (
    ("No focus", 0),
    ("Internal panel (right)", 1),
    ("External panel (left)", 2),
    ("Comms panel", 3),
    ("Role panel", 4),
    ("Station services", 5),
    ("Galaxy map", 6),
    ("System map", 7),
    ("Orrery", 8),
    ("FSS mode", 9),
    ("SAA mode", 10),
    ("Codex", 11),
)

LEGAL_STATE_VALUES = (
    ("Clean", "Clean"),
    ("Illegal cargo", "IllegalCargo"),
    ("Speeding", "Speeding"),
    ("Wanted", "Wanted"),
    ("Hostile", "Hostile"),
    ("Passenger wanted", "PassengerWanted"),
    ("Warrant", "Warrant"),
    ("Allied", "Allied"),
    ("Thargoid", "Thargoid"),
)

CATEGORICAL_VALUES = {
    "GuiFocus": GUI_FOCUS_VALUES,
    "LegalState": LEGAL_STATE_VALUES,
}

DISPLAY_TO_NATIVE_OPERATOR = {
    "is": "Equal",
    "is not": "Not Equal",
}
NATIVE_TO_DISPLAY_OPERATOR = {
    native: display for display, native in DISPLAY_TO_NATIVE_OPERATOR.items()
}


def _validate_catalogue() -> None:
    gui_values = tuple(value for _label, value in GUI_FOCUS_VALUES)
    if gui_values != tuple(range(12)):
        raise RuntimeError("GUI focus catalogue must preserve Frontier values 0..11")
    for source, choices in CATEGORICAL_VALUES.items():
        stored = tuple(value for _label, value in choices)
        if len(stored) != len(set(stored)):
            raise RuntimeError(f"Duplicate stored value in {source} categorical catalogue")


_validate_catalogue()


def apply_categorical_argument_inputs(app_module: Any) -> None:
    """Give known categorical Arguments named selectors without changing rules."""

    row_class = app_module.HumanArgumentConditionRow
    argument_condition = app_module.ArgumentCondition

    previous_init = row_class.__init__
    previous_source_kind = row_class._source_kind
    previous_source_changed = row_class._source_changed
    previous_set_condition = row_class.set_condition
    previous_condition = row_class.condition

    def current_edl_name(self, visible_text: str | None = None) -> str:
        text = self.source.currentText() if visible_text is None else visible_text
        index = self.source.currentIndex()
        if index >= 0 and self.source.currentText().strip() == text.strip():
            data = self.source.itemData(index, Qt.ItemDataRole.UserRole)
            if isinstance(data, str) and data:
                return data
        return resolve_edl_name(text)

    def source_kind(name: str) -> str:
        edl_name = resolve_edl_name(name)
        if edl_name in CATEGORICAL_VALUES:
            return "categorical"
        return previous_source_kind(name)

    def populate_categorical(self, edl_name: str, wanted: object = None) -> None:
        choices = CATEGORICAL_VALUES[edl_name]
        previous = self.categorical_value.currentData()
        self.categorical_value.blockSignals(True)
        try:
            self.categorical_value.clear()
            for label, stored_value in choices:
                self.categorical_value.addItem(label, stored_value)

            target = wanted if wanted is not None else previous
            index = self.categorical_value.findData(target)
            if index < 0 and wanted is not None:
                # Preserve a value from an existing profile even if Frontier later
                # extends an enumeration before EDL's display catalogue is updated.
                self.categorical_value.addItem(f"Unknown value: {wanted}", wanted)
                index = self.categorical_value.count() - 1
            if index < 0 and self.categorical_value.count():
                index = 0
            if index >= 0:
                self.categorical_value.setCurrentIndex(index)
        finally:
            self.categorical_value.blockSignals(False)

    def set_relation(self, native_operator: str) -> None:
        display = NATIVE_TO_DISPLAY_OPERATOR.get(native_operator, "is")
        index = self.operator.findText(display)
        if index >= 0:
            self.operator.setCurrentIndex(index)

    def source_changed(self, visible_name: str) -> None:
        edl_name = current_edl_name(self, visible_name)
        legacy_source = getattr(self, "_categorical_legacy_numeric_source", None)
        if legacy_source is not None and legacy_source != edl_name:
            self._categorical_legacy_numeric_source = None
            legacy_source = None

        if not hasattr(self, "categorical_value") or edl_name not in CATEGORICAL_VALUES:
            previous_source_changed(self, visible_name)
            return

        # Preserve an already-existing GuiFocus range/comparison rule instead of
        # silently rewriting it. Newly created GuiFocus rules use the named list.
        if legacy_source == "GuiFocus":
            previous_source_changed(self, visible_name)
            return

        was_loading = self._loading
        self._loading = True
        try:
            # Let the reference layer keep the readable Argument name, WIP colour,
            # and deliberate help text in sync, then replace only the value editor.
            previous_source_changed(self, visible_name)
            previous_native = DISPLAY_TO_NATIVE_OPERATOR.get(
                self.operator.currentText(), self.operator.currentText()
            )
            self.operator.clear()
            self.operator.addItems(("is", "is not"))
            set_relation(self, previous_native)
            self.bool_relation.setVisible(False)
            self.operator.setVisible(True)
            populate_categorical(self, edl_name)
            self.value_stack.setCurrentWidget(self.categorical_value)
        finally:
            self._loading = was_loading
        if not was_loading:
            self._emit_changed()

    def set_condition(self, condition) -> None:
        wanted = condition.source
        is_category = wanted in CATEGORICAL_VALUES
        legacy_numeric = (
            wanted == "GuiFocus"
            and condition.operator not in {"Equal", "Not Equal"}
        )
        self._categorical_legacy_numeric_source = "GuiFocus" if legacy_numeric else None

        # During the original row constructor the categorical widget does not
        # exist yet. Load normally, then the wrapped constructor re-applies the
        # condition once the named selector has been installed.
        if not hasattr(self, "categorical_value") or not is_category or legacy_numeric:
            previous_set_condition(self, condition)
            return

        previous_set_condition(self, condition)
        source_changed(self, self.source.currentText())
        self._loading = True
        try:
            set_relation(self, condition.operator)
            populate_categorical(self, wanted, condition.value)
            self.value_stack.setCurrentWidget(self.categorical_value)
        finally:
            self._loading = False

    def condition(self):
        source = current_edl_name(self)
        if (
            source == "GuiFocus"
            and getattr(self, "_categorical_legacy_numeric_source", None) == "GuiFocus"
        ):
            return previous_condition(self)

        if source in CATEGORICAL_VALUES and hasattr(self, "categorical_value"):
            value = self.categorical_value.currentData()
            if value is None:
                raise ValueError(f"{display_name_for(source)} requires a selected value")
            native_operator = DISPLAY_TO_NATIVE_OPERATOR.get(self.operator.currentText())
            if native_operator is None:
                raise ValueError(
                    f"{display_name_for(source)} requires 'is' or 'is not'"
                )
            return argument_condition(source, native_operator, value)

        return previous_condition(self)

    def row_init(self, *args, **kwargs) -> None:
        condition_arg = args[0] if args else kwargs.get("condition")
        previous_init(self, *args, **kwargs)

        self.categorical_value = QComboBox()
        self.categorical_value.setMinimumWidth(180)
        self.categorical_value.currentIndexChanged.connect(
            lambda _index: self._emit_changed()
        )
        self.value_stack.addWidget(self.categorical_value)

        if condition_arg is not None:
            self.set_condition(condition_arg)
        else:
            source_changed(self, self.source.currentText())

    row_class.__init__ = row_init
    row_class._source_kind = staticmethod(source_kind)
    row_class._source_changed = source_changed
    row_class.set_condition = set_condition
    row_class.condition = condition
