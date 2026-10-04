#!/usr/bin/env python3
"""Pure GitHub Release interpretation for the future EDL update notifier.

There is deliberately no network access in this module. A later transport/UI
boundary may fetch GitHub JSON and pass the decoded object here. Keeping fetch,
interpretation and presentation separate makes offline/error behavior easy to
test and keeps update awareness non-critical to application startup.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from lighting_version import CURRENT_VERSION, SemanticVersion, parse_stable_version


@dataclass(frozen=True)
class StableRelease:
    """The small published-release subset EDL needs for update awareness."""

    tag_name: str
    version: SemanticVersion
    name: str
    html_url: str
    published_at: str


@dataclass(frozen=True)
class UpdateAssessment:
    """Comparison result after interpreting one GitHub Release payload."""

    current_version: SemanticVersion
    release: StableRelease | None

    @property
    def update_available(self) -> bool:
        return (
            self.release is not None
            and self.release.version > self.current_version
        )


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"GitHub Release field {key!r} must be a non-empty string")
    return value.strip()


def parse_stable_github_release(
    payload: Mapping[str, Any],
) -> StableRelease | None:
    """Interpret one decoded GitHub Release object.

    Drafts and releases explicitly marked as prereleases are outside EDL's V1
    stable channel and therefore return None. Malformed purportedly-stable
    release data raises ValueError so a future fetch boundary can fail silently
    without accidentally treating bad metadata as an update.
    """
    if not isinstance(payload, Mapping):
        raise TypeError("GitHub Release payload must be a mapping")

    draft = payload.get("draft")
    prerelease = payload.get("prerelease")
    if not isinstance(draft, bool):
        raise ValueError("GitHub Release field 'draft' must be a boolean")
    if not isinstance(prerelease, bool):
        raise ValueError("GitHub Release field 'prerelease' must be a boolean")

    if draft or prerelease:
        return None

    tag_name = _required_string(payload, "tag_name")
    version = parse_stable_version(tag_name)
    name_value = payload.get("name")
    if name_value is None:
        name = tag_name
    elif isinstance(name_value, str):
        name = name_value.strip() or tag_name
    else:
        raise ValueError("GitHub Release field 'name' must be a string or null")

    html_url = _required_string(payload, "html_url")
    if not html_url.startswith("https://"):
        raise ValueError("GitHub Release html_url must use HTTPS")

    published_at = _required_string(payload, "published_at")

    return StableRelease(
        tag_name=tag_name,
        version=version,
        name=name,
        html_url=html_url,
        published_at=published_at,
    )


def assess_github_release(
    payload: Mapping[str, Any],
    *,
    current_version: SemanticVersion = CURRENT_VERSION,
) -> UpdateAssessment:
    """Return the pure decision a future update UI will consume."""
    if not isinstance(current_version, SemanticVersion):
        raise TypeError("current_version must be SemanticVersion")
    return UpdateAssessment(
        current_version=current_version,
        release=parse_stable_github_release(payload),
    )
