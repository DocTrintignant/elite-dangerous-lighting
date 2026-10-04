#!/usr/bin/env python3
"""UI guardrails for discovered+selected lighting targets and profile validation.

This layer is intentionally outside discovery and rendering. It consumes the
latest read-only discovery result plus persisted operator selection, filters only
new authoring choices, preserves stored profile targets, and builds an ephemeral
runtime snapshot with unavailable outputs disabled.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton

from lighting_chroma_zone_config import ChromaZoneConfiguration, load_chroma_zone_configuration
from lighting_device_availability import (
    DeviceAvailabilitySnapshot,
    ProfileTargetIssue,
    filter_profile_for_runtime,
    profile_acquisition_targets,
    profile_target_issues,
)
from lighting_device_selection import device_control_routes, selected_device_ids
from lighting_discovery_catalog import LightingDiscoveryResult
from lighting_govee_config import GoveeConfiguration, load_govee_configuration
from lighting_openrgb_targets import parse_openrgb_target
from lighting_profiles import NativeLightingProfile
from lighting_ui_tokens import STATUS_OK, STATUS_WARNING, TEXT_SECONDARY


@dataclass(frozen=True)
class ProposedSession:
    runtime_profile: NativeLightingProfile
    requested_targets: tuple[str, ...]
    available_targets: tuple[str, ...]
    unavailable_targets: tuple[str, ...]
    mode_targets: tuple[str, ...]


_UNAVAILABLE_SUFFIX = " — unavailable"


def _unavailable_suffix(target: str, reason: str) -> str:
    if reason != "ROUTE_UNAVAILABLE":
        return _UNAVAILABLE_SUFFIX
    value = str(target or "").strip()
    normalized = value.upper()
    if parse_openrgb_target(value) is not None:
        return " — OpenRGB unavailable"
    if (
        normalized in {"KEYBOARD", "MOUSE", "CHROMALINK"}
        or normalized.startswith("CHROMALINK::")
        or normalized.startswith("CHROMA_ZONE::")
    ):
        return " — Chroma unavailable"
    return _UNAVAILABLE_SUFFIX


def _status_suffix(status) -> str:
    if status.reason == "OWNER_MISMATCH":
        detail = str(status.detail or "").rstrip(".")
        if detail.startswith("Control via is set to "):
            owner = detail[len("Control via is set to "):]
            return f" — Control via {owner}"
        return f" — {detail}" if detail else _UNAVAILABLE_SUFFIX
    if status.reason == "ROUTE_DISABLED":
        detail = str(status.detail or "").rstrip(".")
        return f" [{detail}]" if detail else _UNAVAILABLE_SUFFIX
    return _unavailable_suffix(status.target, status.reason)


def _load_snapshot(window) -> DeviceAvailabilitySnapshot:
    result = getattr(window, "_last_lighting_discovery", None)
    if not isinstance(result, LightingDiscoveryResult):
        result = LightingDiscoveryResult(())

    govee = getattr(window, "_govee_configuration", None)
    if not isinstance(govee, GoveeConfiguration):
        try:
            govee = load_govee_configuration()
        except Exception:
            govee = GoveeConfiguration()

    try:
        chroma = load_chroma_zone_configuration()
    except Exception:
        chroma = ChromaZoneConfiguration()

    return DeviceAvailabilitySnapshot(
        discovery=result,
        selected_ids=frozenset(selected_device_ids(window._settings)),
        govee_configuration=govee,
        chroma_zone_configuration=chroma,
        control_routes=device_control_routes(window._settings),
    )


def _base_box_text(box) -> str:
    stored = box.property("edl_availability_base_text")
    if isinstance(stored, str) and stored:
        return stored
    text = box.text()
    if text.endswith(_UNAVAILABLE_SUFFIX):
        text = text[: -len(_UNAVAILABLE_SUFFIX)]
    return text


def _restore_box_label(box) -> None:
    stored = box.property("edl_availability_base_text")
    if isinstance(stored, str) and stored:
        box.setText(stored)
    box.setProperty("edl_availability_base_text", None)
    previous_tip = box.property("edl_availability_base_tooltip")
    if isinstance(previous_tip, str):
        box.setToolTip(previous_tip)
    box.setProperty("edl_availability_base_tooltip", None)


def _mark_selected_unavailable(box, status) -> None:
    base = _base_box_text(box)
    if not box.property("edl_availability_base_text"):
        box.setProperty("edl_availability_base_text", base)
        box.setProperty("edl_availability_base_tooltip", box.toolTip())
    # Target identity stays stable. Runtime/backend state belongs in the
    # separate availability presentation, not inside the selected value.
    box.setText(base)
    box.setToolTip(status.detail)


def _set_selector_availability(selector, snapshot: DeviceAvailabilitySnapshot) -> None:
    label = getattr(selector, "_edl_availability_label", None)
    if label is None:
        return

    targets = tuple(getattr(selector, "targets", lambda: ())())
    if not targets:
        label.setText("Choose one or more lights.")
        label.setProperty("edlState", "")
    else:
        statuses = tuple(snapshot.status(target) for target in targets)
        unavailable = tuple(status for status in statuses if not status.available)
        if not unavailable:
            label.setText(
                "Available now"
                if len(statuses) == 1
                else f"{len(statuses)} selected · all available"
            )
            label.setProperty("edlState", "ok")
        else:
            first = unavailable[0]
            detail = str(first.detail or "").strip()
            if len(statuses) == 1:
                label.setText(detail or "Selected light is currently unavailable.")
            else:
                label.setText(
                    f"{len(statuses) - len(unavailable)}/{len(statuses)} available"
                    + (f" · {detail}" if detail else "")
                )
            label.setProperty("edlState", "warning")

    style = label.style()
    style.unpolish(label)
    style.polish(label)
    label.update()


def _sync_empty_selector_hint(selector, snapshot: DeviceAvailabilitySnapshot) -> None:
    """Keep the Rule target popup informative when no authorable targets exist."""
    window = selector.window()
    if getattr(window, "target_combo", None) is not selector:
        return

    boxes = getattr(selector, "_boxes", {})
    visible_targets = tuple(
        target
        for target, box in boxes.items()
        if target != "GLOBAL" and not box.isHidden()
    )
    empty = not visible_targets

    label = getattr(selector, "_edl_empty_target_label", None)
    button = getattr(selector, "_edl_empty_target_button", None)
    if label is None or button is None:
        layout = getattr(selector, "_menu_layout", None)
        if layout is None:
            return

        label = QLabel(selector._menu_host)
        label.setWordWrap(True)
        label.setStyleSheet(f"color:{TEXT_SECONDARY}; padding:4px 2px;")
        button = QPushButton("Lighting devices…", selector._menu_host)

        def open_devices() -> None:
            menu = getattr(selector, "menu", None)
            if menu is not None:
                menu.close()
            opener = getattr(window, "show_lighting_devices", None)
            if callable(opener):
                QTimer.singleShot(0, opener)

        button.clicked.connect(open_devices)
        layout.addWidget(label)
        layout.addWidget(button)
        selector._edl_empty_target_label = label
        selector._edl_empty_target_button = button

    if empty:
        if snapshot.selected_ids:
            message = (
                "No selected lighting target is currently available. "
                "Review device detection and selection."
            )
        else:
            message = (
                "No lighting devices are selected. Choose the devices EDL may use first."
            )
        label.setText(message)
        label.show()
        button.show()
        selector.button.setToolTip(message)
    else:
        label.hide()
        button.hide()


def _apply_runtime_target_label(box, target: str, status) -> None:
    normalized = str(target or "").strip().upper()
    if (
        normalized in {"KEYBOARD", "MOUSE", "CHROMALINK"}
        or normalized.startswith("CHROMA_ZONE::")
    ):
        label = str(getattr(status, "label", "") or "").strip()
        if label:
            box.setText(label)


def _sync_selector(selector) -> None:
    window = selector.window()
    snapshot_method = getattr(window, "_edl_availability_snapshot", None)
    if not callable(snapshot_method):
        return
    snapshot = snapshot_method()

    boxes = getattr(selector, "_boxes", {})
    for target, box in boxes.items():
        status = snapshot.status(target)
        selected = box.isChecked()
        hidden_by_guard = bool(box.property("edl_hidden_unavailable"))

        if status.available:
            if hidden_by_guard:
                box.setVisible(True)
                box.setProperty("edl_hidden_unavailable", False)
            _restore_box_label(box)
            _apply_runtime_target_label(box, target, status)
            continue

        if selected:
            # Existing saved/draft references remain visible so the operator can
            # see what the rule contains. They are never silently rewritten.
            if hidden_by_guard:
                box.setVisible(True)
                box.setProperty("edl_hidden_unavailable", False)
            _restore_box_label(box)
            _apply_runtime_target_label(box, target, status)
            _mark_selected_unavailable(box, status)
            continue

        # Hide only choices this guard owns. Other adapters may intentionally hide
        # implementation leaves (for example native Govee zone addresses).
        if not box.isHidden() or hidden_by_guard:
            box.setVisible(False)
            box.setProperty("edl_hidden_unavailable", True)
        _restore_box_label(box)
        _apply_runtime_target_label(box, target, status)

    refresh = getattr(selector, "_refresh_button", None)
    if callable(refresh):
        refresh()
    _set_selector_availability(selector, snapshot)
    _sync_empty_selector_hint(selector, snapshot)


def _first_available_selector_target(window) -> str | None:
    selector = getattr(window, "target_combo", None)
    if selector is None:
        return None
    snapshot = window._edl_availability_snapshot()
    for target in getattr(selector, "_order", ()):
        if target == "GLOBAL":
            continue
        box = getattr(selector, "_boxes", {}).get(target)
        if box is None:
            continue
        if box.isHidden() and not bool(box.property("edl_hidden_unavailable")):
            continue
        if snapshot.status(target).available:
            return target
    return None


def _issue_reason(issue: ProfileTargetIssue) -> str:
    if issue.reason in {"ROUTE_UNAVAILABLE", "ROUTE_DISABLED", "OWNER_MISMATCH"}:
        if issue.detail:
            return issue.detail.rstrip(".")
    labels = {
        "NOT_DETECTED": "not currently detected",
        "NOT_SELECTED": "detected but not selected",
        "ROUTE_UNAVAILABLE": "control route unavailable",
        "ROUTE_DISABLED": "control route disabled in EDL",
        "OWNER_MISMATCH": "controlled through another backend",
        "UNKNOWN_TARGET": "target cannot be resolved",
    }
    return labels.get(issue.reason, issue.reason.replace("_", " ").lower())


def _issues_html(profile_name: str, issues: tuple[ProfileTargetIssue, ...]) -> str:
    grouped: dict[int, list[ProfileTargetIssue]] = {}
    for issue in issues:
        grouped.setdefault(issue.rule_index, []).append(issue)

    parts = [
        f"<p><b>{html.escape(profile_name)}</b> was loaded, but some saved lighting "
        "outputs are not currently available.</p>",
        "<p>The profile has not been changed. Available outputs can still run; "
        "unavailable outputs will be skipped for this live session.</p>",
    ]
    for rule_index in sorted(grouped):
        entries = grouped[rule_index]
        rule_name = entries[0].rule_name
        parts.append(
            f"<p><b>Rule {rule_index + 1} — {html.escape(rule_name)}</b><br>"
        )
        lines = []
        for issue in entries:
            disabled = " (saved output is disabled)" if not issue.output_enabled else ""
            lines.append(
                f"Output {issue.output_index + 1}: "
                f"<b>{html.escape(issue.label)}</b> — "
                f"{html.escape(_issue_reason(issue))}{html.escape(disabled)}"
            )
        parts.append("<br>".join(lines) + "</p>")
    return "".join(parts)


def apply_profile_device_guard(app_module: Any) -> None:
    """Install discovered+selected authoring and non-destructive profile checks."""
    window_class = app_module.MainWindow
    if getattr(window_class, "_edl_profile_device_guard_applied", False):
        return

    selector_class = app_module.MultiTargetSelector
    previous_selector_init = selector_class.__init__
    previous_selector_set_targets = selector_class.set_targets

    def selector_init(self, *args, **kwargs) -> None:
        previous_selector_init(self, *args, **kwargs)
        menu = getattr(self, "menu", None)
        if menu is not None:
            menu.aboutToShow.connect(lambda: _sync_selector(self))

    def selector_set_targets(self, targets, *, emit: bool = True) -> None:
        previous_selector_set_targets(self, targets, emit=emit)
        _sync_selector(self)

    selector_class.__init__ = selector_init
    selector_class.set_targets = selector_set_targets

    previous_init = window_class.__init__
    previous_refresh_devices = window_class.refresh_lighting_devices
    previous_load_profile = window_class.load_profile
    previous_import_virpil = window_class.import_virpil_profile
    previous_add_rule = window_class.add_rule
    previous_toggle_live = window_class._toggle_live_lighting
    previous_runnable_targets = window_class._runnable_targets
    previous_update_test_output_state = window_class._update_test_output_state
    previous_toggle_preview = window_class._toggle_preview

    def availability_snapshot(self) -> DeviceAvailabilitySnapshot:
        return _load_snapshot(self)

    def proposed_session(self) -> ProposedSession | None:
        profile = getattr(self, "_profile", None)
        if not isinstance(profile, NativeLightingProfile):
            return None

        snapshot = self._edl_availability_snapshot()
        requested = profile_acquisition_targets(profile)
        available = tuple(
            target for target in requested if snapshot.status(target).available
        )
        unavailable = tuple(
            target for target in requested if not snapshot.status(target).available
        )
        runtime_profile = filter_profile_for_runtime(profile, snapshot)
        return ProposedSession(
            runtime_profile=runtime_profile,
            requested_targets=requested,
            available_targets=available,
            unavailable_targets=unavailable,
            mode_targets=(),
        )

    def sync_target_availability(self) -> None:
        for selector in self.findChildren(selector_class):
            _sync_selector(selector)

    def show_pending_profile_device_issues(self) -> None:
        profile = getattr(self, "_edl_pending_profile_validation", None)
        self._edl_pending_profile_validation = None
        if profile is None or profile is not getattr(self, "_profile", None):
            return

        issues = profile_target_issues(profile, self._edl_availability_snapshot())
        self._edl_last_profile_target_issues = issues
        self._edl_sync_target_availability()
        if not issues:
            return

        box = QMessageBox(self)
        box.setWindowTitle("Profile device check")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(_issues_html(profile.name, issues))
        box.setInformativeText(
            "Select the missing device(s) in Lighting devices, reconnect them, "
            "or continue with the currently available outputs."
        )

        first_button = box.addButton(
            "Go to first affected rule",
            QMessageBox.ButtonRole.ActionRole,
        )
        devices_button = box.addButton(
            "Lighting devices…",
            QMessageBox.ButtonRole.ActionRole,
        )
        continue_button = box.addButton(
            "Continue",
            QMessageBox.ButtonRole.AcceptRole,
        )
        box.setDefaultButton(continue_button)
        box.exec()

        clicked = box.clickedButton()
        if clicked is first_button:
            first = issues[0]
            if (
                getattr(self, "_profile", None) is profile
                and 0 <= first.rule_index < len(profile.rules)
            ):
                self._select_rows((first.rule_index,))
                self._load_rule_into_editor(first.rule_index)
                item = self.table.item(first.rule_index, 0)
                if item is not None:
                    self.table.scrollToItem(item)
        elif clicked is devices_button:
            QTimer.singleShot(0, self.show_lighting_devices)

    def queue_profile_validation(self) -> None:
        profile = getattr(self, "_profile", None)
        if profile is None:
            return
        self._edl_pending_profile_validation = profile
        self._edl_sync_target_availability()
        if isinstance(
            getattr(self, "_last_lighting_discovery", None),
            LightingDiscoveryResult,
        ):
            QTimer.singleShot(0, self._edl_show_pending_profile_device_issues)

    def refresh_lighting_devices(self, *args, **kwargs):
        result = previous_refresh_devices(self, *args, **kwargs)
        self._edl_sync_target_availability()
        if getattr(self, "_edl_pending_profile_validation", None) is not None:
            # If this was startup discovery, this is deliberately the third modal:
            # compatibility -> device chooser -> profile device check.
            QTimer.singleShot(100, self._edl_show_pending_profile_device_issues)
        return result

    def load_profile(self, path: Path) -> None:
        candidate = Path(path)
        previous_load_profile(self, candidate)
        current = getattr(self, "_profile_path", None)
        if (
            current is not None
            and getattr(self, "_profile", None) is not None
            and Path(current).expanduser().resolve(strict=False)
            == candidate.expanduser().resolve(strict=False)
        ):
            self._edl_queue_profile_validation()

    def import_virpil_profile(self, path: Path) -> None:
        before = getattr(self, "_profile", None)
        previous_import_virpil(self, Path(path))
        after = getattr(self, "_profile", None)
        if after is not None and after is not before:
            self._edl_queue_profile_validation()

    def add_rule(self) -> None:
        previous_add_rule(self)
        if not getattr(self, "_new_rule_draft", False):
            return
        selector = getattr(self, "target_combo", None)
        if selector is None:
            return

        # LightingRule still needs a valid target while the draft object is being
        # constructed, so the base editor uses KEYBOARD internally. At the final
        # Rule-authoring boundary clear that inherited choice: the operator must
        # explicitly choose the new Rule's physical output. Saved/loaded/duplicated
        # rules do not pass through this method and therefore retain their targets.
        self._edl_sync_target_availability()
        selector.set_targets((), emit=True)
        self._edl_sync_target_availability()

        show_pending = getattr(self, "_show_pending_row", None)
        if callable(show_pending):
            show_pending()

    def runnable_targets(self) -> set[str]:
        result = set(previous_runnable_targets(self))
        profile = getattr(self, "_profile", None)
        if not isinstance(profile, NativeLightingProfile):
            return result
        snapshot = self._edl_availability_snapshot()
        result.update(
            default.target
            for default in profile.defaults
            if default.target != "GLOBAL"
            and snapshot.status(default.target).available
        )
        return result

    def update_test_output_state(self) -> None:
        previous_update_test_output_state(self)
        if bool(getattr(self, "_preview_running", lambda: False)()):
            return
        # Preserve more specific semantic blockers (for example trigger-only
        # effects or an incomplete ChromaLink cell selection). Availability
        # narrows Preview only when the existing effect/editor contract says it
        # would otherwise be eligible.
        if not self.preview_button.isEnabled():
            return
        targets = tuple(getattr(self, "target_combo", ()).targets())
        if len(targets) != 1:
            return
        status = self._edl_availability_snapshot().status(targets[0])
        if status.available:
            return
        self.preview_button.setEnabled(False)
        detail = status.detail or "This target is not currently available for Preview."
        self.preview_button.setToolTip(detail)
        preview_status = getattr(self, "preview_status", None)
        if isinstance(preview_status, QLabel):
            preview_status.setText("Preview unavailable")
            preview_status.setToolTip(detail)
            preview_status.setProperty("edlState", "warning")
            style = preview_status.style()
            style.unpolish(preview_status)
            style.polish(preview_status)
            preview_status.update()

    def toggle_preview(self) -> None:
        if bool(getattr(self, "_preview_running", lambda: False)()):
            previous_toggle_preview(self)
            return
        targets = tuple(getattr(self, "target_combo", ()).targets())
        if len(targets) == 1:
            status = self._edl_availability_snapshot().status(targets[0])
            if not status.available:
                self.statusBar().showMessage(
                    status.detail or "This target is not currently available for Preview.",
                    6500,
                )
                self._update_test_output_state()
                return
        previous_toggle_preview(self)

    def toggle_live_lighting(self) -> None:
        # Stop follows the accepted lifecycle unchanged.
        live_running = getattr(self, "_live_running", None)
        if callable(live_running) and live_running():
            previous_toggle_live(self)
            return

        profile = getattr(self, "_profile", None)
        if profile is None:
            previous_toggle_live(self)
            return

        proposal = self._edl_proposed_session()
        if proposal is None:
            previous_toggle_live(self)
            return

        # The accepted runner takes an immutable local profile snapshot. Expose
        # the complete augmented-then-filtered session only long enough for the
        # existing Mode/base wrappers to capture it. The marker prevents the
        # inner Mode wrapper from re-adding an owner that final admission removed.
        self._profile = proposal.runtime_profile
        self._edl_session_profile_finalized = True
        try:
            previous_toggle_live(self)
        finally:
            self._edl_session_profile_finalized = False
            self._profile = profile

    def init(self, *args, **kwargs) -> None:
        previous_init(self, *args, **kwargs)
        self._edl_pending_profile_validation = None
        self._edl_last_profile_target_issues = ()
        self._edl_session_profile_finalized = False
        QTimer.singleShot(0, self._edl_sync_target_availability)

    window_class.__init__ = init
    window_class.refresh_lighting_devices = refresh_lighting_devices
    window_class.load_profile = load_profile
    window_class.import_virpil_profile = import_virpil_profile
    window_class.add_rule = add_rule
    window_class._runnable_targets = runnable_targets
    window_class._update_test_output_state = update_test_output_state
    window_class._toggle_preview = toggle_preview
    window_class._toggle_live_lighting = toggle_live_lighting
    window_class._edl_availability_snapshot = availability_snapshot
    window_class._edl_proposed_session = proposed_session
    window_class._edl_sync_target_availability = sync_target_availability
    window_class._edl_queue_profile_validation = queue_profile_validation
    window_class._edl_show_pending_profile_device_issues = show_pending_profile_device_issues
    window_class._edl_profile_device_guard_applied = True
