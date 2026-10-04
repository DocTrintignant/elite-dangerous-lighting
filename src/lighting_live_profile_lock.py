#!/usr/bin/env python3
"""Keep the editor/profile state identical to the profile owned by Start lighting.

``run_profile`` deliberately owns one immutable NativeLightingProfile snapshot for
an entire live session. Editing profile or topology state while that worker is
running would make the operator-visible configuration diverge from the profile
and device maps that actually own Chroma/native-Govee transports.

This final UI lifecycle adapter therefore makes the snapshot boundary explicit:
while live lighting is active, rule/profile/topology mutations are unavailable.
Stop lighting first, edit/apply, then Start lighting again so transport ownership
is acquired from exactly that applied configuration.

The renderer, rule engine, input adapters and transport lifecycles are untouched.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton


LOCK_MESSAGE = "Lighting is running. Stop lighting to change rules, targets, or profiles."
LOCK_LABEL = "Lighting running — stop lighting to edit this profile."


def apply_live_profile_lock(ui_module: Any) -> None:
    """Install the live-profile snapshot guardrail on the final MainWindow."""
    window_class = ui_module.MainWindow
    if getattr(window_class, "_edl_live_profile_lock_applied", False):
        return

    previous_init = window_class.__init__
    previous_set_editor_enabled = window_class._set_editor_enabled
    previous_update_profile_buttons = window_class._update_profile_buttons
    previous_toggle_live_lighting = window_class._toggle_live_lighting
    previous_live_finished = window_class._live_finished
    previous_set_profile_rules = window_class._set_profile_rules
    previous_open_profile_dialog = window_class.open_profile_dialog
    previous_load_profile = window_class.load_profile
    previous_import_virpil = getattr(window_class, "import_virpil_profile_dialog", None)

    guarded_method_names = (
        "add_rule",
        "duplicate_rules",
        "request_delete_rules",
        "confirm_delete_rules",
        "cut_rules",
        "paste_rules",
        "move_selected_rules",
        "drag_move_rule",
        "apply_rule",
    )
    guarded_methods: dict[str, Callable[..., Any]] = {
        name: getattr(window_class, name) for name in guarded_method_names
    }

    def reject_live_edit(self) -> None:
        self.statusBar().showMessage(LOCK_MESSAGE, 6500)

    def running(self) -> bool:
        return bool(self._live_running())

    def restore_draft_label(self) -> None:
        if not hasattr(self, "draft_state"):
            return
        if self._editor_dirty:
            self.draft_state.setText("Unsaved rule changes")
        elif self._new_rule_draft:
            self.draft_state.setText("New rule draft")
        else:
            self.draft_state.setText("")

    def sync_live_profile_lock(self) -> None:
        is_running = running(self)

        if hasattr(self, "add_rule_button"):
            self.add_rule_button.setEnabled(not is_running)

        if hasattr(self, "table"):
            self.table.setDragEnabled(not is_running)

        if is_running:
            for name in (
                "duplicate_button",
                "delete_button",
                "move_up_button",
                "move_down_button",
            ):
                control = getattr(self, name, None)
                if control is not None:
                    control.setEnabled(False)
            for name in ("cut_action", "paste_action"):
                action = getattr(self, name, None)
                if action is not None:
                    action.setEnabled(False)
            for name in ("cut_button", "paste_button"):
                button = getattr(self, name, None)
                if button is not None:
                    button.setEnabled(False)

            if hasattr(self, "delete_confirm"):
                self.delete_confirm.setVisible(False)
            previous_set_editor_enabled(self, False)
            if hasattr(self, "draft_state"):
                self.draft_state.setText(LOCK_LABEL)
        else:
            if hasattr(self, "_update_rule_action_state"):
                self._update_rule_action_state()
            active_editor = bool(
                getattr(self, "_new_rule_draft", False)
                or getattr(self, "_editing_row", None) is not None
            )
            previous_set_editor_enabled(self, active_editor)
            if hasattr(self, "_update_test_output_state"):
                self._update_test_output_state()
            if getattr(self, "draft_state", None) is not None and self.draft_state.text() == LOCK_LABEL:
                restore_draft_label(self)

        # Open/import and topology setup can replace configuration snapshots
        # without going through _set_profile_rules, so lock those entry points too.
        for button in self.findChildren(QPushButton):
            label = button.text()
            lower = label.casefold()
            if (
                label in {"Open profile", "Open profile…", "Import VIRPIL profile", "Import VIRPIL…"}
                or "zone setup" in lower
                or "chroma zones" in lower
            ):
                button.setEnabled(not is_running)

    def window_init(self, *args, **kwargs) -> None:
        previous_init(self, *args, **kwargs)
        sync_live_profile_lock(self)

    def set_editor_enabled(self, enabled: bool) -> None:
        previous_set_editor_enabled(self, bool(enabled) and not running(self))

    def update_profile_buttons(self) -> None:
        previous_update_profile_buttons(self)
        sync_live_profile_lock(self)

    def toggle_live_lighting(self) -> None:
        previous_toggle_live_lighting(self)
        sync_live_profile_lock(self)

    def live_finished(self, error: object) -> None:
        previous_live_finished(self, error)
        sync_live_profile_lock(self)

    def set_profile_rules(self, rules) -> None:
        if running(self):
            raise RuntimeError(LOCK_MESSAGE)
        previous_set_profile_rules(self, rules)

    def open_profile_dialog(self) -> None:
        if running(self):
            reject_live_edit(self)
            return
        previous_open_profile_dialog(self)

    def load_profile(self, path) -> None:
        if running(self):
            reject_live_edit(self)
            return
        previous_load_profile(self, path)

    def guarded(name: str, previous: Callable[..., Any]):
        def method(self, *args, **kwargs):
            if running(self):
                reject_live_edit(self)
                return None
            return previous(self, *args, **kwargs)

        method.__name__ = name
        return method

    window_class.__init__ = window_init
    window_class._set_editor_enabled = set_editor_enabled
    window_class._update_profile_buttons = update_profile_buttons
    window_class._toggle_live_lighting = toggle_live_lighting
    window_class._live_finished = live_finished
    window_class._set_profile_rules = set_profile_rules
    window_class.open_profile_dialog = open_profile_dialog
    window_class.load_profile = load_profile

    if previous_import_virpil is not None:
        def import_virpil_profile_dialog(self, *args, **kwargs):
            if running(self):
                reject_live_edit(self)
                return None
            return previous_import_virpil(self, *args, **kwargs)

        window_class.import_virpil_profile_dialog = import_virpil_profile_dialog

    for name, previous in guarded_methods.items():
        setattr(window_class, name, guarded(name, previous))

    window_class._edl_live_profile_lock_applied = True


# Co-located keyboard-focus guard for the live lighting action.
def apply_live_button_focus_policy(ui_module) -> None:
    """Keep Start/Stop lighting out of the keyboard focus chain."""
    window_class = ui_module.MainWindow
    if getattr(window_class, "_edl_live_button_no_focus_applied", False):
        return

    previous_init = window_class.__init__

    def patched_init(self, *args, **kwargs):
        previous_init(self, *args, **kwargs)
        self.start_lighting_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    window_class.__init__ = patched_init
    window_class._edl_live_button_no_focus_applied = True

