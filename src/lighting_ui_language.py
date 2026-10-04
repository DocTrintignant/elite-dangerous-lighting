#!/usr/bin/env python3
"""Canonical operator-facing language for the EDL desktop UI.

This module owns presentation vocabulary only. Domain classes, persisted field
names and runtime API names intentionally remain unchanged.
"""

from __future__ import annotations


# Top-level workspaces.
RULES_TAB = "Rules"
SCRIPTED_MODES_TAB = "Scripted modes"

RULES_TAB_HELP = (
    "Create condition-driven lighting rules that react to Elite state, keyboard, "
    "buttons or axes."
)
SCRIPTED_MODES_TAB_HELP = (
    "Create named timed lighting modes for COVAS:NEXT voice commands. "
    "Voice operation requires the Chromas Next plugin and the COVASify integration. "
    "Use Preview to play the current Mode locally on the lighting hardware."
)

# Shared source/target/effect concepts.
TARGET_SUBTITLE = "where the lights are sent"
TARGET_HELP = (
    "<b>Target</b><br><br>"
    "Target defines where the selected output sends its lighting.<br><br>"
    "Use <b>Lights</b> to choose the destination for that output. Each output "
    "keeps its own target, effect, colours and effect settings."
)

EFFECT_SUBTITLE = "what the lights should do"
EFFECT_HELP = (
    "<b>Effect</b><br><br>"
    "Effect defines what the selected output does while it is active.<br><br>"
    "<b>STATIC</b> holds one colour.<br>"
    "<b>FLASH</b> steps through colours.<br>"
    "<b>PULSE</b> raises and lowers brightness.<br>"
    "<b>BREATH</b> moves smoothly through its configured colours.<br>"
    "<b>SPECTRUM</b> cycles through the rainbow.<br>"
    "<b>WAVE</b> moves a colour pattern across an addressable surface.<br>"
    "<b>STARLIGHT</b> produces distributed twinkles.<br>"
    "<b>FIRE</b> produces a changing fire-like pattern.<br><br>"
    "<b>REACTIVE</b> and <b>RIPPLE</b> begin from a source transition and are "
    "configured in Rules."
)
RULE_EFFECT_HELP = EFFECT_HELP
SCENE_EFFECT_HELP = EFFECT_HELP


# Rule draft actions.
APPLY_RULE_CHANGES = "Apply changes"
CANCEL_RULE_CHANGES = "Cancel changes"

# Scripted-mode authoring.
MODES_HEADING = "Modes"
MODE_EDITOR_TITLE = "Mode editor"
MODE_SECTION_TITLE = "MODE"
MODE_SECTION_SUBTITLE = "named timed lighting sequence"
MODE_HELP = (
    "<b>Scripted mode</b><br><br>"
    "A named timed lighting sequence for COVAS:NEXT voice commands. "
    "A mode runs its phases from top to bottom.<br><br>"
    "In v1, voice operation requires the <b>Chromas Next plugin</b> and the "
    "<b>COVASify integration</b>. EDL still owns the Mode, its phases, lighting "
    "effects, targets and restoration.<br><br>"
    "Use <b>Preview mode</b> to play the current editor Mode locally on the "
    "lighting hardware. Local Preview does not invoke COVASify media playback."
)

MODE_PREVIEW_HELP = (
    "<b>Preview mode</b><br><br>"
    "Play the current Mode editor state on the real lighting hardware without "
    "applying the Mode or starting the full profile.<br><br>"
    "Preview uses the same device ownership, renderer and restoration paths as "
    "normal EDL lighting. Choose <b>Stop preview</b> to end it early.<br><br>"
    "COVASify media playback is part of the voice integration and is not started "
    "by local Preview."
)

PHASE_SECTION_TITLE = "PHASE"
PHASE_SECTION_SUBTITLE = "one timed step in the mode"
PHASE_HELP = (
    "<b>Phase</b><br><br>"
    "One timed step in the scripted mode. All enabled outputs in the phase run "
    "at the same time. When its duration ends, EDL moves to the next phase."
)

MEDIA_SECTION_TITLE = "MEDIA"
MEDIA_SECTION_SUBTITLE = "optional media request for this scripted mode"
MEDIA_HELP = (
    "<b>Media</b><br><br>"
    "Optional media request associated with this scripted mode. EDL stores the "
    "request but does not discover or play media itself.<br><br>"
    "When the mode is invoked through the supported COVASify integration, COVASify "
    "can use the request to resolve and play the track. Leave both fields blank for "
    "a lighting-only scene."
)

ADD_MODE = "+ Add mode"
RUN_MODE = "Preview mode"
STOP_MODE = "Stop preview"
UNSAVED_MODE_CHANGES = "Unsaved mode changes"
