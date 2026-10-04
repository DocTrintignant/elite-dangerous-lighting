#!/usr/bin/env python3
"""Native direct-effect extension of the accepted loopback COVAS bridge.

The base bridge remains read-only status/catalog infrastructure. This layer adds
EDL's current native direct-effect command path plus Stand Down, using the existing
engine-defined target/zone addressing and renderer authority rather than a
parallel STATIC override mechanism.
"""

from __future__ import annotations

import copy
import threading
from dataclasses import fields
from typing import Any, Mapping

import lighting_covas_bridge as base
from lighting_authority import lighting_authority, parse_static_colour
from lighting_covas_catalog import build_lighting_catalog
from lighting_direct_effects import DIRECT_EFFECTS, direct_effects
from lighting_effect_config import SPECTRUM, EffectParameters
from lighting_session_profile import physical_owner_target


_PARAMETER_FIELDS = tuple(item.name for item in fields(EffectParameters))
_PARAMETER_FIELD_SET = set(_PARAMETER_FIELDS)
_LAST_COMMAND_LOCK = threading.Lock()
_last_command_payload: dict[str, object] | None = None
_DIAGNOSTIC_FIELDS = frozenset(
    {
        "command",
        "target",
        "targets",
        "color",
        "effect",
        "colors",
        "parameters",
        "step_seconds",
        "scene",
        "mode",
        "display_name",
        "duration_seconds",
        "authority",
        "media_cue",
        "cleared_overrides",
    }
)


def _record_last_command(response: Mapping[str, object]) -> None:
    """Keep one sanitized localhost diagnostic for a successfully accepted command."""
    global _last_command_payload
    snapshot = {
        key: copy.deepcopy(value)
        for key, value in response.items()
        if key in _DIAGNOSTIC_FIELDS
    }
    with _LAST_COMMAND_LOCK:
        _last_command_payload = snapshot


def _last_command_snapshot() -> dict[str, object] | None:
    with _LAST_COMMAND_LOCK:
        return copy.deepcopy(_last_command_payload)


def _catalog_with_effect_state(
    catalog: Mapping[str, Any],
    *,
    availability: Any | None = None,
) -> dict[str, Any]:
    result = base._catalog_with_runtime_state(catalog, availability=availability)
    effect_targets = set(direct_effects.direct_override_targets)
    overridden = effect_targets
    result["direct_override_targets"] = sorted(overridden)

    devices = result.get("devices")
    if isinstance(devices, list):
        for device in devices:
            if not isinstance(device, dict):
                continue
            targets = device.get("targets")
            if not isinstance(targets, list):
                continue
            for target in targets:
                if not isinstance(target, dict):
                    continue
                target_id = target.get("id")
                if isinstance(target_id, str):
                    target["direct_override"] = target_id in overridden
    return result


def _parameter_payload(parameters: EffectParameters) -> dict[str, object]:
    result: dict[str, object] = {}
    for item in fields(EffectParameters):
        value = getattr(parameters, item.name)
        if value is not None:
            result[item.name] = value
    return result


def _parse_effect_colour(value: object) -> tuple[int, int, int]:
    if isinstance(value, str):
        return parse_static_colour(value)
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(
            "each color must be an RGB [R,G,B] triplet or a #RRGGBB/name compatibility string"
        )
    if any(isinstance(component, bool) or not isinstance(component, int) for component in value):
        raise ValueError("RGB components must be integers")
    if any(component < 0 or component > 255 for component in value):
        raise ValueError("RGB components must be between 0 and 255")
    return value[0], value[1], value[2]


def _whole_active_targets() -> tuple[str, ...]:
    """Collapse ALL through the current physical-owner mapping.

    This deliberately contains no provider whitelist. Chroma/Govee leaves collapse
    to their owning surface/device; OpenRGB leaves remain their concrete renderable
    targets unless a backend later defines a broader owner mapping.
    """
    return tuple(
        dict.fromkeys(
            physical_owner_target(target)
            for target in lighting_authority.active_targets
        )
    )


class _EffectStatusHandler(base._StatusHandler):
    """Accepted bridge handler plus generic native direct effects."""

    def _record_command_diagnostic(self, response: Mapping[str, object]) -> None:
        _record_last_command(response)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self.path == "/api/v1/diagnostics/last-command":
            self._write_json(
                200,
                {
                    "ok": True,
                    "api_version": base.API_VERSION,
                    "service": "elite-dangerous-lighting",
                    "last_command": _last_command_snapshot(),
                },
            )
            return

        if self.path == "/api/v1/status":
            active_targets = lighting_authority.active_targets
            overridden = sorted(direct_effects.direct_override_targets)
            self._write_json(
                200,
                {
                    "ok": True,
                    "api_version": base.API_VERSION,
                    "service": "elite-dangerous-lighting",
                    "bridge": "ready",
                    "capabilities": [
                        "status",
                        "catalog",
                        "direct_effects",
                        "engine_defined_zones",
                        "exact_rgb",
                        "rgb_triplet_palette",
                        "last_command_diagnostic",
                        "stand_down",
                    ],
                    "effects": list(DIRECT_EFFECTS),
                    "lighting_control_available": bool(active_targets),
                    "active_control_targets": list(active_targets),
                    "direct_override_targets": overridden,
                },
            )
            return

        if self.path == "/api/v1/catalog":
            try:
                catalog = _catalog_with_effect_state(
                    build_lighting_catalog(),
                    availability=getattr(self.server, "runtime_availability", None),
                )
            except (OSError, ValueError) as exc:
                self._write_json(
                    500,
                    {
                        "ok": False,
                        "api_version": base.API_VERSION,
                        "service": "elite-dangerous-lighting",
                        "error": "catalog_unavailable",
                        "message": str(exc),
                    },
                )
                return
            self._write_json(
                200,
                {
                    "ok": True,
                    "api_version": base.API_VERSION,
                    "service": "elite-dangerous-lighting",
                    **catalog,
                },
            )
            return

        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self.path != "/api/v1/commands":
            self._write_json(
                405,
                {
                    "ok": False,
                    "api_version": base.API_VERSION,
                    "error": "method_not_allowed",
                },
            )
            return

        payload = self._read_command_json()
        if payload is None:
            return

        command = payload.get("command")
        if command == "set_effect":
            self._set_effect(payload)
            return
        if command == "stand_down":
            overridden_before = set(direct_effects.direct_override_targets)
            direct_effects.clear_direct_overrides()
            response = {
                "ok": True,
                "api_version": base.API_VERSION,
                "service": "elite-dangerous-lighting",
                "command": "stand_down",
                "cleared_overrides": len(overridden_before),
            }
            self._record_command_diagnostic(response)
            self._write_json(200, response)
            return

        self._write_json(
            400,
            {
                "ok": False,
                "api_version": base.API_VERSION,
                "service": "elite-dangerous-lighting",
                "error": "unsupported_command",
                "message": (
                    "Supported commands are set_effect and stand_down."
                ),
            },
        )

    @staticmethod
    def _optional_number(payload: Mapping[str, object], name: str):
        value = payload.get(name)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be numeric when supplied")
        return float(value)

    @staticmethod
    def _optional_int(payload: Mapping[str, object], name: str):
        value = payload.get(name)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} must be an integer when supplied")
        return value

    @staticmethod
    def _parameter_source(payload: dict[str, object]) -> Mapping[str, object]:
        nested = payload.get("parameters")
        if nested is None:
            return payload
        if not isinstance(nested, dict):
            raise ValueError("parameters must be an object when supplied")

        unknown = sorted(str(name) for name in nested if name not in _PARAMETER_FIELD_SET)
        if unknown:
            raise ValueError("unknown effect parameter(s): " + ", ".join(unknown))

        merged: dict[str, object] = dict(nested)
        conflicts: list[str] = []
        for name in _PARAMETER_FIELDS:
            top_level = payload.get(name)
            if top_level is None:
                continue
            if name in merged and merged[name] != top_level:
                conflicts.append(name)
            elif name not in merged:
                merged[name] = top_level
        if conflicts:
            raise ValueError(
                "parameter supplied with conflicting nested/top-level values: "
                + ", ".join(sorted(conflicts))
            )
        return merged

    def _effect_parameters(self, payload: dict[str, object]) -> EffectParameters:
        source = self._parameter_source(payload)
        direction = source.get("direction")
        if direction is not None and not isinstance(direction, str):
            raise ValueError("direction must be a string when supplied")
        brightness = self._optional_number(source, "brightness")
        return EffectParameters(
            brightness=1.0 if brightness is None else brightness,
            step_seconds=self._optional_number(source, "step_seconds"),
            cycle_seconds=self._optional_number(source, "cycle_seconds"),
            minimum_brightness=self._optional_number(source, "minimum_brightness"),
            maximum_brightness=self._optional_number(source, "maximum_brightness"),
            direction=direction,
            density=self._optional_number(source, "density"),
            speed=self._optional_number(source, "speed"),
            width=self._optional_number(source, "width"),
            duration_seconds=self._optional_number(source, "duration_seconds"),
            response_size=self._optional_int(source, "response_size"),
        )

    def _set_effect(
        self,
        payload: dict[str, object],
        *,
        response_command: str = "set_effect",
    ) -> None:
        target_value = payload.get("target")
        effect_value = payload.get("effect")
        colours_value = payload.get("colors")

        if not isinstance(target_value, str):
            self._invalid("set_effect requires a string target field.")
            return
        if not isinstance(effect_value, str):
            self._invalid("set_effect requires a string effect field.")
            return
        effect = effect_value.strip().upper()
        if effect not in DIRECT_EFFECTS:
            self._invalid(
                f"Unsupported effect {effect!r}. EDL currently exposes: {', '.join(DIRECT_EFFECTS)}."
            )
            return

        if effect == SPECTRUM and (colours_value is None or colours_value == []):
            # Persisted SPECTRUM carries one ignored compatibility colour. Direct
            # callers should not have to invent one, so keep it inside the bridge.
            colours = ((0, 0, 0),)
            response_colours: list[list[int]] = []
        else:
            if not isinstance(colours_value, list):
                self._invalid(
                    "colors must be an ordered array of RGB [R,G,B] triplets or #RRGGBB/name compatibility strings."
                )
                return
            try:
                colours = tuple(_parse_effect_colour(value) for value in colours_value)
            except ValueError as exc:
                self._invalid(str(exc))
                return
            response_colours = [list(colour) for colour in colours]

        try:
            parameters = self._effect_parameters(payload)
        except ValueError as exc:
            self._invalid(str(exc))
            return

        active_targets = lighting_authority.active_targets
        if not active_targets:
            self._write_json(
                409,
                {
                    "ok": False,
                    "api_version": base.API_VERSION,
                    "service": "elite-dangerous-lighting",
                    "error": "lighting_not_running",
                    "message": (
                        "Start live lighting in EDL before sending a lighting command."
                    ),
                },
            )
            return

        if base._is_all_target_request(target_value):
            targets = _whole_active_targets()
            if not targets:
                targets = active_targets
        else:
            try:
                runtime_catalog = base._catalog_with_runtime_state(
                    build_lighting_catalog(),
                    availability=getattr(self.server, "runtime_availability", None),
                )
                target = base.resolve_control_target(
                    target_value,
                    catalog=runtime_catalog,
                )
            except (OSError, ValueError) as exc:
                self._invalid(str(exc))
                return
            if target not in active_targets:
                self._write_json(
                    409,
                    {
                        "ok": False,
                        "api_version": base.API_VERSION,
                        "service": "elite-dangerous-lighting",
                        "error": "target_not_active",
                        "message": (
                            f"{target} is configured but is not owned by the "
                            "current live lighting session."
                        ),
                        "active_control_targets": list(active_targets),
                    },
                )
                return
            targets = (target,)

        try:
            applied = direct_effects.set_effect_overrides(
                targets,
                effect,
                colours,
                parameters=parameters,
            )
        except RuntimeError as exc:
            self._write_json(
                409,
                {
                    "ok": False,
                    "api_version": base.API_VERSION,
                    "service": "elite-dangerous-lighting",
                    "error": "target_not_active",
                    "message": str(exc),
                    "active_control_targets": list(
                        lighting_authority.active_targets
                    ),
                },
            )
            return
        except ValueError as exc:
            self._invalid(str(exc))
            return

        first_override = direct_effects.override_for(applied[0])
        assert first_override is not None
        response: dict[str, object] = {
            "ok": True,
            "api_version": base.API_VERSION,
            "service": "elite-dangerous-lighting",
            "command": response_command,
            "effect": effect,
            "targets": list(applied),
            "colors": response_colours,
            "parameters": _parameter_payload(first_override.parameters),
            "authority": "direct_operator_override",
        }
        # Preserve first-slice response fields for existing FLASH clients/tests.
        if effect == "FLASH":
            response["step_seconds"] = first_override.parameters.step_seconds
        if len(applied) == 1:
            response["target"] = applied[0]
        self._record_command_diagnostic(response)
        self._write_json(200, response)

    def _invalid(self, message: str) -> None:
        self._write_json(
            400,
            {
                "ok": False,
                "api_version": base.API_VERSION,
                "service": "elite-dangerous-lighting",
                "error": "invalid_request",
                "message": message,
            },
        )


class CovasEffectBridge(base.CovasStatusBridge):
    """Same loopback lifecycle as the accepted bridge, with native effects."""

    def start(self) -> None:
        if self.is_running:
            return
        server = self._configure_server(
            base.ThreadingHTTPServer((self.host, self.port), _EffectStatusHandler)
        )
        thread = threading.Thread(
            target=server.serve_forever,
            name="edl-covas-effect-bridge",
            daemon=True,
        )
        self._server = server
        self._thread = thread
        thread.start()
