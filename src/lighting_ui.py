#!/usr/bin/env python3
"""Product UI composition helper for the canonical lighting_ui_main entry point."""

from __future__ import annotations

import lighting_chroma_zone_ui as chroma_zone_ui
import lighting_covas_ui as covas_ui
import lighting_mode_ui as mode_ui
import lighting_ui_main as ui_main
import lighting_ui_header_disposition as header_ui
from lighting_authority_runtime import apply_runtime_authority
from lighting_axis_modulation_ui import apply_axis_modulation_ui
from lighting_chroma_zone_ui import apply_chroma_presentation, apply_chroma_zone_rule_ui, apply_chroma_zone_ui
from lighting_chromalink_ui import apply_chromalink_cell_ui
from lighting_covas_mode_bridge import CovasModeBridge
from lighting_covas_ui import apply_covas_integration
from lighting_effect_target_ui import apply_effect_target_ui, apply_effect_ui_promotion
from lighting_govee_rule_ui import apply_govee_all_ui, apply_govee_rule_ui
from lighting_govee_runtime_orientation import apply_govee_runtime_orientation
from lighting_govee_ui import apply_govee_ui
from lighting_govee_ui_mapper import apply_composed_govee_mapper, apply_govee_editor_lifecycle
from lighting_independent_outputs_ui import apply_independent_outputs_ui
from lighting_live_profile_lock import apply_live_button_focus_policy, apply_live_profile_lock
from lighting_mode_ui import (
    apply_live_mode_catalog,
    apply_mode_final_guardrails,
    apply_mode_live_ui,
    apply_mode_media_ui,
    apply_mode_ui,
    apply_mode_ui_copy,
)
from lighting_profile_device_guard import apply_profile_device_guard
from lighting_openrgb_devices_ui import apply_lighting_devices_ui, apply_openrgb_rule_ui, apply_startup_compatibility_notice
from lighting_scene_runtime import apply_scene_runtime
from lighting_release_update_ui import apply_release_update_notification
from lighting_shared_preview_ui import apply_shared_preview_ui
from lighting_shared_target_ui import apply_shared_target_ui, apply_target_alias_integrity
from lighting_ui_help import apply_guided_tour, apply_human_tooltips, install_section_help
from lighting_ui_density import apply_compact_outputs_ui, apply_compact_rule_editor, apply_compact_source_ui
from lighting_ui_header_disposition import (
    apply_current_ui_integration,
    apply_header_disposition,
    apply_ui_normalization,
)
from lighting_appearance import apply_appearance_ui
from lighting_ui_readiness import apply_readiness_presentation
from lighting_workspace_ui import apply_profile_startup_ui, apply_workspace_ui


# Install one canonical section-help mechanism before any product window is built.
install_section_help(ui_main.base)

# The current header/render-rate compatibility layer is a product-composition
# concern, not an Argument-catalogue side effect. Keep the accepted mechanism
# and installation order explicit here before later UI adapters wrap it.
apply_current_ui_integration(ui_main)

header_ui.HEADER_ACTIONS["Govee zones…"] = (
    "Open Govee setup",
    "Find and configure Govee lights for native zone control. EDL uses verified model layouts when available and can guide an experimental physical calibration for other discovered devices. Other lighting devices do not need this Govee setup.",
)
header_ui.GOVEE_SETUP_TOOLTIP = (
    "Find and configure Govee lights that you want EDL to control as independent named zones. "
    "Verified layouts work immediately; other discovered Govee devices can be physically calibrated when EDL has candidate model evidence. "
    "Other lighting devices do not need Govee zone setup here."
)
# Compose position editing, guided discovery/calibration, evidence gating,
# multi-device staging, prototype normalization and
# per-section logical orientation into the final Govee setup mapper. Transport,
# discovery/calibration and native segment addressing remain unchanged.
apply_composed_govee_mapper()
apply_target_alias_integrity(ui_main)
apply_chromalink_cell_ui(ui_main)
apply_govee_ui(ui_main)
apply_govee_rule_ui(ui_main)
apply_govee_editor_lifecycle(ui_main)
apply_chroma_zone_rule_ui(ui_main)
apply_chroma_zone_ui(ui_main)
apply_human_tooltips(ui_main)
apply_effect_ui_promotion(ui_main)
# Final presentation pass only: layout/compactness/help text. Runtime semantics
# remain owned by the already-applied modules above.
apply_chroma_presentation(ui_main, chroma_zone_ui)
# Device-address correction: native Govee gets the same explicit ALL concept as
# every other device, backed by a real whole-device target.
apply_govee_all_ui(ui_main)
# Output-level analogue modulation is not an effect: it lets one HID axis vary
# brightness, STATIC colour, or both while keeping fixed output behavior unchanged.
# Install it before independent outputs so each selected RuleOutput preserves its
# own binding through the existing editor workflow.
apply_axis_modulation_ui(ui_main)
# Independent-output authoring deliberately wraps the complete accepted
# target/effect adapter stack above. The existing controls become the editor for
# one selected RuleOutput at a time; source authoring remains singular.
apply_independent_outputs_ui(ui_main)
# Spatial controls depend on the selected output target. Keep Govee strip-only
# directions and REACTIVE zone Origin presentation above all target adapters.
apply_effect_target_ui(ui_main)
# SOURCE now follows the same visible-controls-only discipline as Modes: hidden
# source pages do not participate in layout, while the active page keeps the
# established Argument/Keyboard/Button/Axis semantics.
apply_compact_source_ui(ui_main)
# Keyboard-trigger guardrail: after clicking Start lighting, SPACE must remain a
# usable rule source rather than activating the focused QPushButton again.
apply_live_button_focus_policy(ui_main)
# Start lighting owns an immutable profile/configuration snapshot. Keep the UI
# from diverging from that running snapshot; edit only after normal Stop/release.
apply_live_profile_lock(ui_main)
# Direct operator authority is an EDL-owned final live-render boundary. STATIC
# and all accepted direct effects reuse existing renderer paths while
# rules/trigger state continue calculating underneath, so Stand Down reveals the
# current state.
apply_runtime_authority()
# Spatial native-Govee effects use the same per-device left/right installation
# orientation already physically calibrated by the setup UI. Native segment
# addresses stay unchanged; only effect coordinates are aligned to the cockpit.
apply_govee_runtime_orientation()
# Scripted scenes are a separate bounded authority layer below direct/operator
# control. This wrapper is installed outside the accepted direct wrapper: scene
# output is produced first, then the already-installed direct wrapper gets final
# authority before the unchanged renderer/transport.
apply_scene_runtime()
# Data-driven COVAS:NEXT mode authoring reuses the established two-pane grammar
# while keeping timed modes separate from condition-driven LightingRule data.
apply_mode_ui(ui_main)
# Copy the normal EDL list/editor workflow: Modes + ordered Phases on the left,
# explicit draft Apply/Cancel on the right, one selected output at a time and
# ordinary continuous effects only.
apply_mode_ui_copy(ui_main)
# Mode live integration is a separate lifecycle seam: the immutable runtime
# snapshot acquires all applied mode targets, and Run/Stop is scoped to the exact
# SceneRun started by the mode workspace.
apply_mode_live_ui(ui_main)
# Final adversarial guardrails add ChromaLink cell target coverage, keep target-
# aware effect controls synchronized and prevent stale modes from surviving a
# VIRPIL working-profile replacement.
apply_mode_final_guardrails(ui_main)
# Rules and Modes now share the same compact target selector class. Existing
# target IDs, device-specific adapters and independent-output semantics remain
# authoritative underneath the common popup presentation.
apply_shared_target_ui(ui_main, mode_ui)
# Expose the already-supported optional Mode-level media_cue through the accepted
# Mode draft/apply workflow. This is authoring metadata only; media discovery and
# playback remain owned by external integrations such as Covasify.
apply_mode_media_ui(ui_main)
# Normal EDL Rules should use the same vertical discipline already proven useful
# in Modes: RULE/SOURCE/TARGET consume natural content height and irrelevant
# ChromaLink address controls disappear until ChromaLink is actually selected.
apply_compact_rule_editor(ui_main)
# The independent-output list should reserve only the rows it actually shows:
# one output = one row, two outputs = two rows, three or more = three-row viewport.
apply_compact_outputs_ui(ui_main)
# Persistence now uses an explicit outer workspace envelope. Restore the strict
# native profile parser and unpack only that new envelope at the combined UI
# boundary; plain native v1-v5 profiles keep their accepted load path unchanged.
apply_workspace_ui(ui_main)
# Publish the exact clean ModeLibrary only after Start lighting succeeds. COVAS
# therefore sees the same immutable Mode set whose physical targets were acquired
# for this live session, never mutable editor state.
apply_live_mode_catalog(ui_main)
# Extend the accepted localhost scene bridge with generic named Mode dispatch.
# Mode data still executes only through lighting_mode_runtime -> scene authority.
covas_ui.CovasStatusBridge = CovasModeBridge
# COVAS:NEXT is deliberately the final additive UI/lifecycle boundary. It must
# see the complete accepted product window rather than bypassing any device,
# output, effect, scene or presentation adapters above.
apply_covas_integration(ui_main)
# Startup profile selection is an outer UI preference only. It remembers the
# last successful native load/save and reuses the existing loader when the user
# explicitly enables automatic loading; it never starts lighting automatically.
apply_profile_startup_ui(ui_main)
# Lighting devices is the single operator-facing surface for discovery-source
# preferences, device selection and Control via routing. The underlying
# integration preference model remains unchanged.
# Read-only device inventory. This requests OpenRGB metadata only when the
# operator opens Lighting devices; it performs no lighting writes or zone resize.
apply_lighting_devices_ui(ui_main)
# Present stable OpenRGB leaves in the ordinary rule editor only. The final
# availability guard below still enforces discovered + selected + routable.
apply_openrgb_rule_ui(ui_main)
# Informational startup notice only. No discovery, routing or hardware changes.
apply_startup_compatibility_notice(ui_main)
# Final device-availability guard. New authoring choices are restricted to
# currently discovered + selected devices; saved profile references remain
# intact and are validated only after startup discovery/selection has finished.
apply_profile_device_guard(ui_main)
# Rules and Scripted Modes share one local Preview lifecycle over the normal
# renderer/device-router stack. Install this after final availability/owner
# admission so Preview cannot bypass Control via or current route health.
apply_shared_preview_ui(ui_main)
# One read-only readiness projection above the accepted device-availability guard.
# Header, Start/Stop and status presentation reuse this same truth surface.
apply_readiness_presentation(ui_main)
# Final UI-fixes disposition pass only. All product controls already exist at
# this point; this layer changes where they are presented without replacing
# their callbacks, state logic, dialogs or runtime mechanisms.
apply_header_disposition(ui_main)
# System / Light / Dark is application presentation only. Install it after the
# final Setup menu exists; runtime, profiles and lighting ownership remain untouched.
apply_appearance_ui(ui_main)
# First-run onboarding is a read-only presentation layer over the final product
# hierarchy. It defers the existing compatibility/device startup flow until the
# tour is finished or skipped, then hands control back unchanged.
apply_guided_tour(ui_main)
# Final presentation contract: canonical vocabulary/help and status-bar behavior
# after every additive UI adapter has finished composing the product window.
apply_ui_normalization(ui_main)
# Passive release awareness is deliberately last and additive. It starts only
# after the product window is usable, never blocks startup, and owns no updater.
apply_release_update_notification(ui_main)
# Compatibility alias for code that imports this composition module.  The only
# command-line entry point is src/lighting_ui_main.py.
main = ui_main.main
