#!/usr/bin/env python3
"""One-file persistence for native EDL profiles plus COVAS:NEXT modes.

The accepted native profile and timed-mode domains remain separate. A new outer
EDL workspace envelope packages both without extending or weakening the strict
native profile v1-v6 schema.

Existing native v1-v6 profile files remain readable and simply produce an empty
mode library. Rules-only saves stay native v6; the explicit workspace envelope
is written only when timed modes actually exist.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from lighting_modes import ModeLibrary, lighting_mode_from_dict, lighting_mode_to_dict
from lighting_profiles import NativeLightingProfile, profile_from_dict, profile_to_dict


# Outer workspace-v1 envelope owned by this persistence module.
WORKSPACE_FORMAT = "elite-dangerous-lighting-workspace"
WORKSPACE_VERSION = 1
WORKSPACE_PROFILE_FIELD = "profile"
WORKSPACE_MODES_FIELD = "modes"
_WORKSPACE_FIELDS = {
    "format",
    "version",
    WORKSPACE_PROFILE_FIELD,
    WORKSPACE_MODES_FIELD,
}


def is_workspace_document(value: Any) -> bool:
    return isinstance(value, dict) and value.get("format") == WORKSPACE_FORMAT


def split_workspace_document(value: Any) -> tuple[dict[str, Any], list[Any]]:
    """Validate the outer envelope and return raw profile/mode payloads."""
    if not isinstance(value, dict):
        raise ValueError("EDL workspace must be a JSON object")
    if value.get("format") != WORKSPACE_FORMAT:
        raise ValueError(f"unsupported EDL workspace format: {value.get('format')!r}")
    if set(value) != _WORKSPACE_FIELDS:
        missing = _WORKSPACE_FIELDS - set(value)
        unknown = set(value) - _WORKSPACE_FIELDS
        details = []
        if missing:
            details.append(f"missing {sorted(missing)}")
        if unknown:
            details.append(f"unknown {sorted(unknown)}")
        raise ValueError(f"EDL workspace fields invalid: {'; '.join(details)}")

    version = value["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != WORKSPACE_VERSION:
        raise ValueError(f"unsupported EDL workspace version: {version!r}")

    profile = value[WORKSPACE_PROFILE_FIELD]
    modes = value[WORKSPACE_MODES_FIELD]
    if not isinstance(profile, dict):
        raise ValueError("EDL workspace profile must be a JSON object")
    if not isinstance(modes, list):
        raise ValueError("EDL workspace modes must be an array")
    return profile, modes


def unwrap_native_profile_document(value: Any) -> Any:
    """Expose the nested native profile to native-only consumers.

    Plain native v1-v6 documents pass through unchanged. This keeps the strict
    native profile parser untouched while allowing existing runners/loaders to
    consume the base profile from a one-file workspace.
    """
    if not is_workspace_document(value):
        return value
    profile, _modes = split_workspace_document(value)
    return profile


def profile_modes_to_dict(
    profile: NativeLightingProfile,
    modes: ModeLibrary,
) -> dict[str, Any]:
    """Package the independent domains inside one explicit workspace envelope."""
    if not isinstance(profile, NativeLightingProfile):
        raise ValueError("profile must be NativeLightingProfile")
    if not isinstance(modes, ModeLibrary):
        raise ValueError("modes must be ModeLibrary")
    return {
        "format": WORKSPACE_FORMAT,
        "version": WORKSPACE_VERSION,
        WORKSPACE_PROFILE_FIELD: profile_to_dict(profile),
        WORKSPACE_MODES_FIELD: [lighting_mode_to_dict(mode) for mode in modes.modes],
    }


def profile_modes_from_dict(value: Any) -> tuple[NativeLightingProfile, ModeLibrary]:
    """Parse a workspace or a legacy/plain native profile.

    A plain native v1-v6 document intentionally has no timed-mode payload.
    """
    if is_workspace_document(value):
        profile_data, raw_modes = split_workspace_document(value)
        profile = profile_from_dict(profile_data)
        modes = ModeLibrary(tuple(lighting_mode_from_dict(item) for item in raw_modes))
        return profile, modes

    profile = profile_from_dict(value)
    return profile, ModeLibrary(())


def load_profile_modes(
    path: str | os.PathLike[str],
) -> tuple[NativeLightingProfile, ModeLibrary]:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read EDL profile/workspace {source}: {exc}") from exc
    return profile_modes_from_dict(value)


def save_profile_modes(
    path: str | os.PathLike[str],
    profile: NativeLightingProfile,
    modes: ModeLibrary,
) -> None:
    if not isinstance(profile, NativeLightingProfile):
        raise ValueError("profile must be NativeLightingProfile")
    if not isinstance(modes, ModeLibrary):
        raise ValueError("modes must be ModeLibrary")

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    document = (
        profile_modes_to_dict(profile, modes)
        if modes.modes
        else profile_to_dict(profile)
    )
    payload = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    temporary = destination.with_name(f".{destination.name}.tmp")
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
