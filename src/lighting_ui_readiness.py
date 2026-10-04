#!/usr/bin/env python3
"""Read-only operator readiness projection for the EDL desktop UI.

This module creates no device discovery, state authority or renderer policy.
It projects facts already owned by the canonical UI, Status diagnostics and
DeviceAvailabilitySnapshot so header, Start/Stop and status-bar presentation
can share one vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from lighting_ui_tokens import (
    STATUS_OK,
    STATUS_WARNING,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)


@dataclass(frozen=True)
class ReadinessFact:
    key: str
    label: str
    value: str
    state: str
    detail: str = ""


@dataclass(frozen=True)
class UiReadinessSnapshot:
    facts: tuple[ReadinessFact, ...]
    can_start: bool
    live_running: bool
    preview_running: bool
    blocked_reason: str
    enabled_rule_count: int
    enabled_output_count: int
    available_output_count: int
    unavailable_output_count: int
    requested_target_count: int | None = None
    available_target_count: int | None = None
    unavailable_target_count: int | None = None

    @property
    def summary(self) -> str:
        if self.live_running:
            return "Lighting running"
        if self.preview_running:
            return "Preview active"
        if self.can_start:
            return "Ready"
        return "Needs attention"


def _running(window, method_name: str) -> bool:
    method = getattr(window, method_name, None)
    return bool(callable(method) and method())


def _availability(window):
    method = getattr(window, "_edl_availability_snapshot", None)
    return method() if callable(method) else None


def build_readiness(window) -> UiReadinessSnapshot:
    """Project current operator readiness without changing any underlying state."""
    profile = getattr(window, "_profile", None)
    profile_dirty = bool(getattr(window, "_profile_dirty", False))
    mode_surface = getattr(window, "_covas_modes_workspace", None)
    mode_draft_pending = bool(
        mode_surface is not None
        and getattr(mode_surface, "_mode_draft_dirty", False)
    )
    draft_pending = bool(
        getattr(window, "_editor_dirty", False)
        or getattr(window, "_new_rule_draft", False)
        or mode_draft_pending
    )
    live_running = _running(window, "_live_running")
    preview_running = _running(window, "_preview_running")

    diagnostics = getattr(window, "_elite_diagnostics", None)
    elite_error = getattr(window, "_elite_status_error", None)
    elite_state = diagnostics.state if diagnostics is not None else {}
    elite_configured = bool(
        diagnostics is not None and getattr(diagnostics, "configured", False)
    )
    elite_status_path = (
        getattr(diagnostics, "status_path", None) if diagnostics is not None else None
    )
    elite_path_available = bool(
        elite_status_path is not None and elite_status_path.is_file()
    )
    if elite_error:
        elite_fact = ReadinessFact(
            "elite",
            "Elite status",
            "Error",
            "warning",
            str(elite_error),
        )
    elif elite_state:
        elite_fact = ReadinessFact(
            "elite",
            "Elite status",
            "Live",
            "ok",
            "Canonical Status.json snapshot is updating.",
        )
    elif elite_configured and not elite_path_available:
        elite_fact = ReadinessFact(
            "elite",
            "Elite status",
            "File not found",
            "warning",
            f"Configured Status.json was not found: {elite_status_path}",
        )
    elif elite_configured:
        elite_fact = ReadinessFact(
            "elite",
            "Elite status",
            "Waiting",
            "neutral",
            "No canonical Status.json snapshot has been read yet.",
        )
    else:
        elite_fact = ReadinessFact(
            "elite",
            "Elite status",
            "Not configured",
            "warning",
            "No canonical Status.json path is configured.",
        )

    if profile is None:
        profile_fact = ReadinessFact(
            "profile",
            "Profile",
            "No profile loaded",
            "blocked",
            "Load a profile or add a rule to create a new one.",
        )
        rules = ()
    else:
        profile_value = profile.name + (" · unsaved changes" if profile_dirty else "")
        profile_fact = ReadinessFact(
            "profile",
            "Profile",
            profile_value,
            "warning" if profile_dirty else "ok",
            (
                "Applied profile changes have not been saved to disk."
                if profile_dirty
                else "Applied profile is saved."
            ),
        )
        rules = tuple(profile.rules)

    enabled_rules = tuple(rule for rule in rules if rule.enabled)
    uses_elite_status = any(bool(rule.conditions) for rule in enabled_rules)
    enabled_outputs = tuple(
        output
        for rule in enabled_rules
        for output in rule.outputs
        if output.enabled and output.target != "GLOBAL"
    )

    availability = _availability(window)
    ownership_targets: tuple[str, ...] = ()
    ownership_error = ""
    if availability is not None:
        try:
            ownership_targets = tuple(sorted(availability.available_runtime_targets()))
        except Exception as exc:
            ownership_error = str(exc)

    available_outputs = []
    unavailable_outputs = []
    if availability is not None:
        for output in enabled_outputs:
            status = availability.status(output.target)
            (available_outputs if status.available else unavailable_outputs).append(
                (output, status)
            )
    else:
        unavailable_outputs = [(output, None) for output in enabled_outputs]

    if profile is None:
        profile_fact = ReadinessFact(
            "profile",
            "Profile",
            "No profile · direct control only",
            "neutral",
            "A profile is optional. Start Lighting can expose selected devices directly to COVAS.",
        )

    if not enabled_outputs:
        rules_fact = ReadinessFact(
            "rules",
            "Rules",
            "No enabled outputs",
            "neutral",
            "Rules are optional. Selected devices can still be controlled by COVAS or a Mode.",
        )
    else:
        unavailable_count = len(unavailable_outputs)
        rules_fact = ReadinessFact(
            "rules",
            "Rules",
            (
                f"{len(enabled_rules)} enabled · "
                f"{len(available_outputs)} available output"
                f"{'s' if len(available_outputs) != 1 else ''}"
            ),
            "warning" if unavailable_count else "ok",
            (
                f"{unavailable_count} saved output"
                f"{'s are' if unavailable_count != 1 else ' is'} currently unavailable."
                if unavailable_count
                else "All enabled Rule outputs are currently available."
            ),
        )

    if availability is None:
        devices_fact = ReadinessFact(
            "devices",
            "Lighting devices",
            "Availability unavailable",
            "blocked",
            "The device-availability projection is not initialized.",
        )
    else:
        selected = len(availability.selected_ids)
        detected = len(availability.discovery.candidates)
        devices_fact = ReadinessFact(
            "devices",
            "Lighting devices",
            f"{selected} selected · {detected} detected",
            "ok" if ownership_targets else ("warning" if selected else "blocked"),
            (
                f"{len(ownership_targets)} live control target(s) are available."
                if ownership_targets
                else ownership_error
                or "No selected Available device can be acquired."
            ),
        )

    if live_running:
        lighting_fact = ReadinessFact(
            "lighting",
            "Lighting",
            "Running",
            "ok",
            "The applied runtime snapshot currently owns its selected renderers.",
        )
    elif preview_running:
        lighting_fact = ReadinessFact(
            "lighting",
            "Lighting",
            "Preview active",
            "warning",
            "Temporary Preview owns its selected renderer until stopped.",
        )
    else:
        lighting_fact = ReadinessFact(
            "lighting",
            "Lighting",
            "Stopped",
            "neutral",
            "No live lighting session is running.",
        )

    blocked_reason = ""
    if not live_running:
        if preview_running:
            blocked_reason = "Stop Preview before starting lighting."
        elif draft_pending:
            draft_kind = (
                "Mode"
                if mode_draft_pending
                and not (
                    getattr(window, "_editor_dirty", False)
                    or getattr(window, "_new_rule_draft", False)
                )
                else "Rule"
                if not mode_draft_pending
                else "Rule or Mode"
            )
            blocked_reason = (
                f"Apply or cancel the current {draft_kind} draft before starting lighting."
            )
        elif uses_elite_status and elite_error:
            blocked_reason = (
                "Elite Dangerous Status.json has a read error. "
                "Resolve it before starting Argument Rules."
            )
        elif uses_elite_status and not elite_path_available:
            blocked_reason = (
                "Argument Rules need Elite Dangerous Status.json. "
                "Choose it under Setup → Elite Dangerous data."
            )
        elif availability is None:
            blocked_reason = "Lighting device availability is not initialized."
        elif ownership_error:
            blocked_reason = ownership_error
        elif not ownership_targets:
            blocked_reason = (
                "Select at least one Available device in Lighting devices before starting lighting."
            )

    return UiReadinessSnapshot(
        facts=(elite_fact, profile_fact, rules_fact, devices_fact, lighting_fact),
        can_start=bool(not live_running and not blocked_reason),
        live_running=live_running,
        preview_running=preview_running,
        blocked_reason=blocked_reason,
        enabled_rule_count=len(enabled_rules),
        enabled_output_count=len(enabled_outputs),
        available_output_count=len(available_outputs),
        unavailable_output_count=len(unavailable_outputs),
        requested_target_count=len(ownership_targets),
        available_target_count=len(ownership_targets),
        unavailable_target_count=0,
    )


def _status_span(value: str, colour: str, *, weight: int = 400) -> str:
    return (
        f"<span style='color:{colour}; font-weight:{weight};'>"
        f"{escape(value)}</span>"
    )


def readiness_status_text(snapshot: UiReadinessSnapshot) -> str:
    """Compact, readable projection of the same established readiness facts."""
    facts = {fact.key: fact for fact in snapshot.facts}
    elite = facts.get("elite")
    profile = facts.get("profile")
    lighting = facts.get("lighting")

    elite_text = elite.value if elite is not None else "—"
    elite_colour = STATUS_OK if elite is not None and elite.state == "ok" else (
        STATUS_WARNING if elite is not None and elite.state in {"warning", "blocked"} else TEXT_PRIMARY
    )

    profile_text = profile.value if profile is not None else "—"
    profile_html = _status_span(profile_text, TEXT_PRIMARY, weight=600)
    suffix = " · unsaved changes"
    if profile_text.endswith(suffix):
        base = profile_text[:-len(suffix)]
        profile_html = (
            _status_span(base, TEXT_PRIMARY, weight=600)
            + _status_span(suffix, STATUS_WARNING, weight=600)
        )

    requested_targets = (
        snapshot.requested_target_count
        if snapshot.requested_target_count is not None
        else snapshot.enabled_output_count
    )
    available_targets = (
        snapshot.available_target_count
        if snapshot.available_target_count is not None
        else snapshot.available_output_count
    )
    unavailable_targets = (
        snapshot.unavailable_target_count
        if snapshot.unavailable_target_count is not None
        else snapshot.unavailable_output_count
    )
    if requested_targets:
        targets_text = f"{available_targets}/{requested_targets} available"
        targets_colour = STATUS_WARNING if unavailable_targets else TEXT_PRIMARY
    else:
        targets_text = "None"
        targets_colour = TEXT_PRIMARY

    lighting_text = lighting.value if lighting is not None else "—"
    if snapshot.live_running:
        lighting_colour = STATUS_OK
    elif snapshot.preview_running:
        lighting_colour = STATUS_WARNING
    else:
        lighting_colour = TEXT_PRIMARY

    label = lambda value: _status_span(value, TEXT_SECONDARY, weight=600)
    separator = _status_span("  ·  ", TEXT_SECONDARY)
    return (
        label("Elite:") + " " + _status_span(elite_text, elite_colour, weight=600)
        + separator
        + label("Profile:") + " " + profile_html
        + separator
        + label("Targets:") + " " + _status_span(targets_text, targets_colour, weight=600)
        + separator
        + label("Lighting:") + " " + _status_span(lighting_text, lighting_colour, weight=600)
    )


def apply_readiness_presentation(ui_module: Any) -> None:
    window_class = ui_module.MainWindow
    if getattr(window_class, "_edl_readiness_presentation_applied", False):
        return

    previous_init = window_class.__init__
    previous_update_profile_buttons = window_class._update_profile_buttons
    previous_poll_elite_status = window_class._poll_elite_status
    previous_refresh_lighting_devices = window_class.refresh_lighting_devices
    previous_update_test_output_state = window_class._update_test_output_state

    def readiness_snapshot(self):
        return build_readiness(self)

    def refresh_readiness(self) -> None:
        status_label = getattr(self, "readiness_status", None)
        if not isinstance(status_label, QLabel):
            return
        snapshot = self._edl_readiness_snapshot()
        status_label.setText(readiness_status_text(snapshot))

    def update_profile_buttons(self) -> None:
        previous_update_profile_buttons(self)
        self._edl_refresh_readiness()

    def poll_elite_status(self) -> None:
        diagnostics = getattr(self, "_elite_diagnostics", None)
        before = (
            bool(diagnostics.state) if diagnostics is not None else False,
            getattr(self, "_elite_status_error", None),
        )
        previous_poll_elite_status(self)
        diagnostics = getattr(self, "_elite_diagnostics", None)
        after = (
            bool(diagnostics.state) if diagnostics is not None else False,
            getattr(self, "_elite_status_error", None),
        )
        if after != before:
            self._edl_refresh_readiness()

    def refresh_lighting_devices(self, *args, **kwargs):
        result = previous_refresh_lighting_devices(self, *args, **kwargs)
        # Device selection/discovery changes Start/Stop admission, not only the
        # status text. Reuse the existing button-state path so a zero-Rule
        # session becomes startable immediately after selected Available devices
        # are confirmed.
        self._update_profile_buttons()
        return result

    def update_test_output_state(self) -> None:
        previous_update_test_output_state(self)
        self._edl_refresh_readiness()

    def init(self, *args, **kwargs) -> None:
        previous_init(self, *args, **kwargs)
        self.readiness_status = QLabel(self)
        self.readiness_status.setObjectName("readinessStatus")
        self.readiness_status.setProperty("edlTextRole", "status")
        self.readiness_status.setTextFormat(Qt.TextFormat.RichText)
        self.readiness_status.setStyleSheet("padding:0 6px;")
        self.statusBar().addPermanentWidget(self.readiness_status)
        self._edl_refresh_readiness()

    window_class.__init__ = init
    window_class._update_profile_buttons = update_profile_buttons
    window_class._poll_elite_status = poll_elite_status
    window_class.refresh_lighting_devices = refresh_lighting_devices
    window_class._update_test_output_state = update_test_output_state
    window_class._edl_readiness_snapshot = readiness_snapshot
    window_class._edl_refresh_readiness = refresh_readiness
    window_class._edl_readiness_presentation_applied = True
