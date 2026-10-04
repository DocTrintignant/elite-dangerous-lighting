#!/usr/bin/env python3
"""Canonical product version identity for Elite Dangerous Lighting.

This module is intentionally pure and dependency-free. Product surfaces such
as About, diagnostics, packaging and update checks should consume EDL_VERSION
rather than defining independent version strings.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


EDL_VERSION = "1.0.0"

_STABLE_VERSION_RE = re.compile(
    r"^(?:v)?"
    r"(?P<major>0|[1-9][0-9]*)\."
    r"(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)$"
)


@dataclass(frozen=True, order=True)
class SemanticVersion:
    """A stable MAJOR.MINOR.PATCH version suitable for release comparison."""

    major: int
    minor: int
    patch: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def parse_stable_version(value: str) -> SemanticVersion:
    """Parse a stable semantic version, accepting an optional lowercase v tag.

    Prerelease and build suffixes are intentionally rejected here. EDL's V1
    update channel consumes published stable GitHub Releases only.
    """
    if not isinstance(value, str):
        raise TypeError("version must be a string")

    text = value.strip()
    match = _STABLE_VERSION_RE.fullmatch(text)
    if match is None:
        raise ValueError(
            f"invalid stable version {value!r}; expected MAJOR.MINOR.PATCH"
        )

    return SemanticVersion(
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
    )


CURRENT_VERSION = parse_stable_version(EDL_VERSION)
