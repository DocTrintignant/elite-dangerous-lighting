#!/usr/bin/env python3
"""Shared local testing and software-effect preview for Rules and Scripted Modes.

Two deliberately different operator tools live here:

- Preview effect: software-only, device-independent visualization of the selected
  effect/palette/parameters. It never acquires hardware or evaluates sources.
- Test rule / Preview mode: real hardware execution through the normal runner,
  ownership, input and restoration paths.

External COVAS Mode dispatch and normal Start Lighting remain separate.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton

import lighting_mode_ui as mode_ui
import lighting_mode_ui as mode_copy
from lighting_device_availability import filter_profile_for_runtime
from lighting_effect_editor import EFFECT_PREVIEW_HELP, EffectPreviewStrip
from lighting_preview_runtime import run_mode_preview, run_rule_test
from lighting_profiles import NativeLightingProfile, make_default_profile
from lighting_ui_tokens import TEXT_SECONDARY


PHYSICAL_PREVIEW_TARGETS = {"KEYBOARD", "MOUSE", "CHROMALINK"}


TEST_RULE_HELP = (
    "<b>Test rule</b><br><br>"
    "Test only the Rule currently open in the editor on your real lights. "
    "The rest of the profile does not run.<br><br>"
    "EDL waits for this Rule's conditions or controls exactly as it normally would. "
    "If the Rule uses <b>All connected devices</b>, the test uses the lighting devices "
    "that are selected and available now.<br><br>"
    "Choose <b>Stop test</b> when you are done."
)


def _refresh_label(label: QLabel, text: str, state: str = "", detail: str = "") -> None:
    label.setText(text)
    label.setToolTip(detail)
    label.setProperty("edlState", state)
    style = label.style()
    style.unpolish(label)
    style.polish(label)
    label.update()


def _layout_containing(layout, widget):
    if layout is None:
        return None
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item.widget() is widget:
            return layout
        nested = item.layout()
        if nested is not None:
            found = _layout_containing(nested, widget)
            if found is not None:
                return found
    return None


def _enabled_rule_targets(rule) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            output.target
            for output in rule.outputs
            if output.enabled
        )
    )


def _resolved_rule_test_targets(rule, snapshot) -> tuple[tuple[str, ...], bool]:
    targets = _enabled_rule_targets(rule)
    all_selected = targets == ("GLOBAL",)
    if all_selected:
        return tuple(sorted(snapshot.available_runtime_targets())), True
    return tuple(target for target in targets if target != "GLOBAL"), False


def apply_shared_preview_ui(ui_module: Any) -> None:
    window_class = ui_module.MainWindow
    surface_class = mode_ui.ModeEditorSurface
    if getattr(window_class, "_edl_shared_preview_applied", False):
        return

    previous_init = window_class.__init__
    previous_preview_finished = window_class._preview_finished
    previous_update_test_output_state = window_class._update_test_output_state
    previous_build_effect_section = window_class._build_effect_section
    previous_mode_build_editor_panel = surface_class._build_editor_panel
    previous_mode_load_output = surface_class._load_output

    def preview_intent(self):
        selector = self.target_combo
        if hasattr(selector, "targets"):
            targets = tuple(selector.targets())
            if len(targets) != 1:
                raise ValueError(
                    "Preview requires exactly one Keyboard, Mouse or ChromaLink target."
                )
            target = targets[0].strip().upper()
        else:
            target = selector.currentText().strip().upper()

        if target not in PHYSICAL_PREVIEW_TARGETS:
            raise ValueError(
                "Preview requires one Keyboard, Mouse or ChromaLink physical target."
            )

        palette = self.palette_editor.palette()
        parameters = self.effect_controls.parameters()
        effect = self.effect_combo.currentText()
        ui_module.base.validate_effect_configuration(effect, palette, parameters)
        preview_rule = ui_module.base.LightingRule(
            name="UI preview snapshot",
            conditions=(ui_module.base.ArgumentCondition("Docked", "Equal", True),),
            colour=palette[0],
            colours=palette,
            effect=effect,
            effect_parameters=parameters,
            target=target,
        )
        return ui_module.base.intent_from_rule(preview_rule)

    def preview_kind(self) -> str | None:
        return getattr(self, "_edl_preview_kind", None)

    def refresh_mode_preview(self) -> None:
        surface = getattr(self, "_covas_modes_workspace", None)
        refresh = getattr(surface, "_refresh_run_state", None)
        if callable(refresh):
            refresh()

    def start_shared_preview(
        self,
        kind: str,
        label: str,
        worker_call: Callable[[threading.Event], None],
        *,
        log_data: dict[str, object] | None = None,
    ) -> bool:
        if self._live_running():
            self.statusBar().showMessage(
                "Stop live lighting before starting a hardware test.",
                6500,
            )
            return False
        if self._preview_running():
            self.statusBar().showMessage(
                "Stop the current hardware test before starting another one.",
                6500,
            )
            return False

        stop_event = threading.Event()
        self._preview_stop_event = stop_event
        self._edl_preview_kind = kind
        self._edl_preview_label = label

        data = dict(log_data or {})
        data.update(kind=kind, label=label)
        self._logger.event("PREVIEW_START", **data)

        def worker() -> None:
            error: Exception | None = None
            try:
                worker_call(stop_event)
            except Exception as exc:
                error = exc
            self._preview_signals.finished.emit(error)

        self._preview_thread = threading.Thread(
            target=worker,
            name=f"edl-{kind}-preview",
            daemon=True,
        )
        self._preview_thread.start()
        self._update_test_output_state()
        refresh_mode_preview(self)
        self._update_profile_buttons()
        return True

    def stop_shared_preview(self, kind: str | None = None) -> bool:
        if not self._preview_running():
            return False
        active_kind = preview_kind(self)
        if kind is not None and active_kind != kind:
            return False
        self._stop_preview(request_log=True)
        return True

    # ----- device-independent Effect preview, shared by Rules and Modes -----

    def refresh_rule_effect_preview(self) -> None:
        strip = getattr(self, "effect_preview_strip", None)
        if strip is None or not strip.isVisible():
            return
        try:
            strip.set_configuration(
                self.effect_combo.currentText(),
                tuple(self.palette_editor.palette()),
                self.effect_controls.parameters(),
            )
        except Exception as exc:
            self.statusBar().showMessage(f"Cannot preview effect: {exc}", 5000)

    def toggle_rule_effect_preview(self) -> None:
        strip = getattr(self, "effect_preview_strip", None)
        button = getattr(self, "effect_preview_button", None)
        if strip is None or button is None:
            return
        if strip.isVisible():
            strip.hide()
            button.setText("Preview effect")
            return
        try:
            strip.set_configuration(
                self.effect_combo.currentText(),
                tuple(self.palette_editor.palette()),
                self.effect_controls.parameters(),
            )
        except Exception as exc:
            QMessageBox.warning(self, "Cannot preview effect", str(exc))
            return
        strip.show()
        button.setText("Hide preview")

    def build_rule_effect_section(self):
        section = previous_build_effect_section(self)
        row = _layout_containing(section.root, self.effect_combo)

        self.effect_preview_button = QPushButton("Preview effect", section)
        self.effect_preview_button.setObjectName("effectPreviewAction")
        self.effect_preview_button.clicked.connect(
            lambda: toggle_rule_effect_preview(self)
        )
        self.effect_preview_help = ui_module.HelpButton(
            EFFECT_PREVIEW_HELP,
            section,
        )

        if isinstance(row, QHBoxLayout):
            index = row.indexOf(self.effect_combo)
            row.insertWidget(index + 1, self.effect_preview_button)
            row.insertWidget(index + 2, self.effect_preview_help)

        self.effect_preview_strip = EffectPreviewStrip(section)
        controls_index = section.root.indexOf(self.effect_controls)
        section.root.insertWidget(
            controls_index if controls_index >= 0 else 1,
            self.effect_preview_strip,
        )
        self.effect_preview_strip.hide()

        self.effect_combo.currentTextChanged.connect(
            lambda *_args: refresh_rule_effect_preview(self)
        )
        self.effect_controls.parametersChanged.connect(
            lambda *_args: refresh_rule_effect_preview(self)
        )
        self.palette_editor.paletteChanged.connect(
            lambda *_args: refresh_rule_effect_preview(self)
        )
        return section

    def refresh_mode_effect_preview(self) -> None:
        strip = getattr(self, "effect_preview_strip", None)
        if strip is None or not strip.isVisible() or self._mode_index < 0:
            return
        try:
            strip.set_configuration(
                self.effect_combo.currentText(),
                tuple(self.palette_editor.palette()),
                self.effect_controls.parameters(),
            )
        except Exception as exc:
            self._window().statusBar().showMessage(
                f"Cannot preview effect: {exc}",
                5000,
            )

    def toggle_mode_effect_preview(self) -> None:
        strip = getattr(self, "effect_preview_strip", None)
        button = getattr(self, "effect_preview_button", None)
        if strip is None or button is None:
            return
        if strip.isVisible():
            strip.hide()
            button.setText("Preview effect")
            return
        if self._mode_index < 0:
            return
        try:
            strip.set_configuration(
                self.effect_combo.currentText(),
                tuple(self.palette_editor.palette()),
                self.effect_controls.parameters(),
            )
        except Exception as exc:
            QMessageBox.warning(self, "Cannot preview effect", str(exc))
            return
        strip.show()
        button.setText("Hide preview")

    def build_mode_editor_panel(self):
        panel = previous_mode_build_editor_panel(self)
        section = getattr(self, "_edl_compact_effect_section", None)
        if section is None:
            return panel
        row = _layout_containing(section.root, self.effect_combo)

        self.effect_preview_button = QPushButton("Preview effect", section)
        self.effect_preview_button.setObjectName("modeEffectPreviewAction")
        self.effect_preview_button.clicked.connect(
            lambda: toggle_mode_effect_preview(self)
        )
        self.effect_preview_help = self._ui.HelpButton(
            EFFECT_PREVIEW_HELP,
            section,
        )
        if isinstance(row, QHBoxLayout):
            index = row.indexOf(self.effect_combo)
            row.insertWidget(index + 1, self.effect_preview_button)
            row.insertWidget(index + 2, self.effect_preview_help)

        self.effect_preview_strip = EffectPreviewStrip(section)
        controls_index = section.root.indexOf(self.effect_controls)
        section.root.insertWidget(
            controls_index if controls_index >= 0 else 1,
            self.effect_preview_strip,
        )
        self.effect_preview_strip.hide()

        self.effect_combo.currentTextChanged.connect(
            lambda *_args: refresh_mode_effect_preview(self)
        )
        self.effect_controls.parametersChanged.connect(
            lambda *_args: refresh_mode_effect_preview(self)
        )
        self.palette_editor.paletteChanged.connect(
            lambda *_args: refresh_mode_effect_preview(self)
        )
        return panel

    def mode_load_output(self, index: int) -> None:
        previous_mode_load_output(self, index)
        refresh_mode_effect_preview(self)

    # ----- real single-Rule hardware test -----

    def update_rule_preview_state(self) -> None:
        # Preserve target/effect adapters that synchronize editor controls.
        previous_update_test_output_state(self)
        if not hasattr(self, "preview_button"):
            return

        refresh_rule_effect_preview(self)

        active_editor = bool(self._new_rule_draft or self._editing_row is not None)
        live = self._live_running()
        running = self._preview_running()
        kind = preview_kind(self)

        if hasattr(self, "preview_title"):
            self.preview_title.setText("Test")
        if hasattr(self, "preview_help"):
            self.preview_help.set_help(TEST_RULE_HELP)

        self.preview_button.setText(
            "Stop test" if running and kind == "rule" else "Test rule"
        )

        effect_preview = getattr(self, "effect_preview_button", None)
        if effect_preview is not None:
            effect_preview.setEnabled(active_editor)
            if not active_editor:
                strip = getattr(self, "effect_preview_strip", None)
                if strip is not None:
                    strip.hide()
                effect_preview.setText("Preview effect")

        if not active_editor:
            self.preview_button.setEnabled(False)
            detail = "Select or add a Rule before testing it."
            self.preview_button.setToolTip(detail)
            _refresh_label(self.preview_status, "Select a rule", detail=detail)
            return

        if live:
            detail = "Stop live lighting before testing this Rule by itself."
            self.preview_button.setEnabled(False)
            self.preview_button.setToolTip(detail)
            _refresh_label(self.preview_status, "Stop live lighting", "warning", detail)
            return

        if running:
            if kind == "rule":
                detail = (
                    "Only this Rule is running on real hardware through the normal "
                    "live source/input and renderer path."
                )
                self.preview_button.setEnabled(True)
                self.preview_button.setToolTip("Stop the current Rule test.")
                _refresh_label(self.preview_status, "Testing rule", "ok", detail)
            else:
                detail = "A Scripted Mode hardware preview is already running."
                self.preview_button.setEnabled(False)
                self.preview_button.setToolTip(detail)
                _refresh_label(self.preview_status, "Test unavailable", "warning", detail)
            return

        try:
            rule = self._build_rule_from_editor()
        except Exception as exc:
            detail = str(exc)
            self.preview_button.setEnabled(False)
            self.preview_button.setToolTip(detail)
            _refresh_label(self.preview_status, "Test unavailable", "warning", detail)
            return

        if not rule.enabled:
            detail = "Enable the Rule before testing its real runtime behavior."
            self.preview_button.setEnabled(False)
            self.preview_button.setToolTip(detail)
            _refresh_label(self.preview_status, "Rule disabled", "warning", detail)
            return

        snapshot = self._edl_availability_snapshot()
        targets, all_selected = _resolved_rule_test_targets(rule, snapshot)
        if not targets:
            if all_selected:
                detail = (
                    "Select at least one available device under "
                    "Setup → Lighting devices before testing this Rule."
                )
                status = "Select a lighting device"
            else:
                detail = "Choose and enable at least one lighting output for this Rule."
                status = "Choose lights"
            self.preview_button.setEnabled(False)
            self.preview_button.setToolTip(detail)
            _refresh_label(self.preview_status, status, "warning", detail)
            return

        unavailable = [
            snapshot.status(target)
            for target in targets
            if not snapshot.status(target).available
        ]
        if unavailable:
            first = unavailable[0]
            detail = first.detail or "One of this Rule's lighting targets is unavailable."
            self.preview_button.setEnabled(False)
            self.preview_button.setToolTip(detail)
            _refresh_label(
                self.preview_status,
                f"{first.label or 'Light'} unavailable",
                "warning",
                detail,
            )
            return

        if all_selected:
            detail = (
                "Test this Rule on every selected and available lighting device. "
                "EDL uses the Rule's real activation condition and live controller input."
            )
            status = "Ready — all selected lights"
        else:
            detail = (
                "Test only this Rule on its selected lights. EDL uses the Rule's "
                "real activation condition and live controller input."
            )
            status = "Ready to test"
        self.preview_button.setEnabled(True)
        self.preview_button.setToolTip(detail)
        _refresh_label(self.preview_status, status, "ok", detail)

    def toggle_rule_preview(self) -> None:
        if self._preview_running():
            if preview_kind(self) == "rule":
                stop_shared_preview(self, "rule")
            else:
                self.statusBar().showMessage(
                    "Stop the Scripted Mode Preview before testing a Rule.",
                    6500,
                )
            self._update_test_output_state()
            return

        if self._live_running():
            self.statusBar().showMessage(
                "Stop live lighting before testing a Rule by itself.",
                6500,
            )
            return

        try:
            rule = self._build_rule_from_editor()
        except Exception as exc:
            QMessageBox.warning(self, "Cannot test rule", str(exc))
            self._update_test_output_state()
            return

        if not rule.enabled:
            self.statusBar().showMessage("Enable the Rule before testing it.", 5000)
            self._update_test_output_state()
            return

        snapshot = self._edl_availability_snapshot()
        targets, all_selected = _resolved_rule_test_targets(rule, snapshot)
        if not targets:
            self.statusBar().showMessage(
                "Select an available lighting device first."
                if all_selected
                else "Choose and enable at least one lighting output first.",
                5000,
            )
            self._update_test_output_state()
            return

        unavailable = [
            snapshot.status(target)
            for target in targets
            if not snapshot.status(target).available
        ]
        if unavailable:
            self.statusBar().showMessage(
                unavailable[0].detail or "A Rule target is not currently available.",
                6500,
            )
            self._update_test_output_state()
            return

        status_path = self._elite_diagnostics.status_path
        if rule.conditions and (status_path is None or not status_path.is_file()):
            self.statusBar().showMessage(
                "This Argument Rule needs Elite Dangerous Status.json. "
                "Choose it under Setup → Elite Dangerous data first.",
                7000,
            )
            self._update_test_output_state()
            return

        label = rule.name or "Current rule"
        start_shared_preview(
            self,
            "rule",
            label,
            lambda stop: run_rule_test(
                rule,
                stop,
                status_path=status_path,
                ownership_targets=targets if all_selected else None,
                govee_configuration=snapshot.govee_configuration,
            ),
            log_data={
                "rule": rule.name,
                "targets": targets,
            },
        )

    def preview_finished(self, error: object) -> None:
        self._edl_preview_kind = None
        self._edl_preview_label = None
        previous_preview_finished(self, error)
        refresh_mode_preview(self)

    def window_init(self, *args, **kwargs) -> None:
        self._edl_preview_kind = None
        self._edl_preview_label = None
        previous_init(self, *args, **kwargs)
        if hasattr(self, "preview_title"):
            self.preview_title.setText("Test")
        if hasattr(self, "preview_help"):
            self.preview_help.set_help(TEST_RULE_HELP)

    # ----- Scripted Mode hardware Preview -----

    def mode_run_preview(self) -> None:
        window = self._window()
        if window._preview_running():
            if preview_kind(window) == "mode":
                stop_shared_preview(window, "mode")
            else:
                window.statusBar().showMessage(
                    "Stop the Rule test before previewing a Scripted Mode.",
                    6500,
                )
            self._refresh_run_state()
            return

        if window._live_running():
            window.statusBar().showMessage(
                "Stop live lighting before previewing a Scripted Mode.",
                6500,
            )
            self._refresh_run_state()
            return
        if self._mode_index < 0:
            return

        try:
            self._commit_current()
            mode = self._current_mode()
        except Exception as exc:
            QMessageBox.warning(self, "Cannot preview mode", str(exc))
            return

        if not mode.targets:
            QMessageBox.information(
                self,
                "Nothing to preview",
                "Enable at least one lighting output in this Mode.",
            )
            self._refresh_run_state()
            return

        snapshot = window._edl_availability_snapshot()
        unavailable = [
            snapshot.status(target)
            for target in mode.targets
            if not snapshot.status(target).available
        ]
        if unavailable:
            first = unavailable[0]
            window.statusBar().showMessage(
                first.detail or "A Mode target is not currently available for Preview.",
                6500,
            )
            self._refresh_run_state()
            return

        base_profile = (
            window._profile
            if isinstance(window._profile, NativeLightingProfile)
            else make_default_profile("Mode preview")
        )

        start_shared_preview(
            window,
            "mode",
            mode.name,
            lambda stop: run_mode_preview(
                base_profile,
                mode,
                stop,
                status_path=window._elite_diagnostics.status_path,
                profile_filter=lambda profile: filter_profile_for_runtime(
                    profile,
                    snapshot,
                ),
                govee_configuration=snapshot.govee_configuration,
            ),
            log_data={
                "mode": mode.name,
                "targets": mode.targets,
            },
        )

    def mode_stop_preview(self) -> None:
        window = self._window()
        stop_shared_preview(window, "mode")
        self._refresh_run_state()

    def refresh_mode_state(self) -> None:
        window = self._window()
        has_mode = self._mode_index >= 0
        live = window._live_running()
        running = window._preview_running()
        kind = preview_kind(window)

        effect_preview = getattr(self, "effect_preview_button", None)
        if effect_preview is not None:
            effect_preview.setEnabled(has_mode)
            if not has_mode:
                strip = getattr(self, "effect_preview_strip", None)
                if strip is not None:
                    strip.hide()
                effect_preview.setText("Preview effect")

        self.run_button.setText(
            "Stop preview" if running and kind == "mode" else "Preview mode"
        )
        self.stop_button.setEnabled(running and kind == "mode")

        if not has_mode:
            detail = "Create or select a Scripted Mode before using Preview."
            self.run_button.setEnabled(False)
            self.run_button.setToolTip(detail)
            _refresh_label(self.run_status, "No mode selected", detail=detail)
        elif live:
            detail = "Stop live lighting before previewing the current Scripted Mode."
            self.run_button.setEnabled(False)
            self.run_button.setToolTip(detail)
            _refresh_label(self.run_status, "Stop live lighting", "warning", detail)
        elif running:
            if kind == "mode":
                name = getattr(window, "_edl_preview_label", None) or self._current_mode().name
                detail = "The current Mode editor snapshot is playing through the normal lighting renderer path."
                self.run_button.setEnabled(True)
                self.run_button.setToolTip("Stop the current Mode preview.")
                _refresh_label(self.run_status, f"Previewing {name}", "ok", detail)
            else:
                detail = "A Rule hardware test is already using the temporary lighting session."
                self.run_button.setEnabled(False)
                self.run_button.setToolTip(detail)
                _refresh_label(self.run_status, "Preview unavailable", "warning", detail)
        else:
            mode = self._current_mode()
            if not mode.targets:
                detail = "Enable at least one lighting output in this Mode."
                self.run_button.setEnabled(False)
                self.run_button.setToolTip(detail)
                _refresh_label(self.run_status, "Preview unavailable", "warning", detail)
            else:
                snapshot = window._edl_availability_snapshot()
                unavailable = [
                    snapshot.status(target)
                    for target in mode.targets
                    if not snapshot.status(target).available
                ]
                if unavailable:
                    first = unavailable[0]
                    detail = first.detail or "A Mode target is not currently available for Preview."
                    self.run_button.setEnabled(False)
                    self.run_button.setToolTip(detail)
                    _refresh_label(self.run_status, "Preview unavailable", "warning", detail)
                else:
                    detail = (
                        "Preview the current Mode editor state without applying it or "
                        "starting the full profile."
                    )
                    self.run_button.setEnabled(True)
                    self.run_button.setToolTip(detail)
                    _refresh_label(self.run_status, "Ready", "ok", detail)

        refresh_mode_effect_preview(self)
        mode_copy._sync_live_authoring_lock(self)

    window_class._build_effect_section = build_rule_effect_section
    surface_class._build_editor_panel = build_mode_editor_panel
    surface_class._load_output = mode_load_output

    window_class.__init__ = window_init
    window_class._preview_intent = preview_intent
    window_class._edl_preview_kind_value = preview_kind
    window_class._edl_start_shared_preview = start_shared_preview
    window_class._edl_stop_shared_preview = stop_shared_preview
    window_class._update_test_output_state = update_rule_preview_state
    window_class._toggle_preview = toggle_rule_preview
    window_class._preview_finished = preview_finished

    surface_class.run_mode = mode_run_preview
    surface_class.stop_mode = mode_stop_preview
    surface_class._refresh_run_state = refresh_mode_state

    window_class._edl_shared_preview_applied = True
