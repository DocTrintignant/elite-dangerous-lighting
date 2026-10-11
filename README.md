# Elite Dangerous Lighting

> **Platform support:** This software is developed and tested on Windows. It has not been tested on Linux, and compatibility or correct operation on Linux is not guaranteed.

**Make your cockpit lighting react to Elite Dangerous.**

Elite Dangerous Lighting — **EDL** — is a Windows application that connects Elite Dangerous to compatible RGB lighting.

Your keyboard, mouse, cockpit lighting, addressable RGB and supported smart lights can react automatically as you play.

For example:

```text
Landing gear goes down
        ↓
Keyboard turns orange
```

or:

```text
FSD starts charging
        ↓
Cockpit lighting begins pulsing blue
```

EDL can react to:

- Elite Dangerous ship state
- keyboard combinations
- joystick, throttle and button-box controls
- analogue controls such as throttle levers, joystick axes, rotary dials and sliders
- timed lighting sequences you create yourself

EDL works on its own. Optional voice control and Spotify features can be added through [**COVAS:NEXT**](https://ratherrude.github.io/Elite-Dangerous-AI-Integration/), a separate voice and AI assistant platform for Elite Dangerous. That integration is explained later in this guide.

---

## Development Transparency and AI use

The projects in this repository is developed with substantial generative-AI assistance under human direction.

Project goals, requirements, architecture, design decisions, testing, physical validation where applicable, acceptance criteria, maintenance direction, and publication are directed by **DocTrintignant**. AI-generated code is reviewed and tested before being accepted, but users should understand that these are non-commercial passion projects maintained by a non-professional and may still contain defects or inefficiencies. Download and use at your own discretion.

---

## What you need

EDL is developed and tested on **Windows**. Linux has not been tested and is not currently a supported platform.

For normal use you need:

- Windows
- Elite Dangerous
- compatible lighting hardware

EDL can currently control supported hardware through:

- **Razer Chroma** — Razer's RGB lighting system and compatible devices exposed through Chroma. Razer Synapse/Chroma must be installed and available.
- **OpenRGB** — an independent application that can expose supported PC lighting hardware to EDL. For OpenRGB-controlled devices, run the OpenRGB SDK server in the logged-in Windows session before starting EDL lighting.
- **Enhanced Govee** — EDL's direct integration for supported Govee devices, including finer zone control (**currently only the Govee H61C3 is supported through this direct integration**). The device must be reachable on the local network, and the same H61C3 must not simultaneously be assigned to Govee Desktop's Razer/Chroma control while EDL owns it directly.

EDL presents detected lighting hardware in one place, regardless of which integration is used underneath.

---

## Installation

### Windows installer

Download the latest stable Windows installer from the [GitHub Releases page](https://github.com/DocTrintignant/elite-dangerous-lighting/releases/latest).

For V1, download:

```text
Elite.Dangerous.Lighting.Setup.exe
```

Run the installer, then launch **Elite Dangerous Lighting** from the Start Menu.

The installer is per-user and does not require administrator rights. EDL stores profiles, settings and logs outside the application directory so normal application updates do not replace your user data.

### Run from source

Python 3.12 is the validated development/runtime baseline for V1.

From a clean checkout:

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python src\lighting_ui_main.py
```

The Windows installer remains the recommended path for normal users.

---

# Getting started

The first time EDL starts, a short guided tour introduces the main controls.

The examples shown during the tour are temporary. They are not added to your profile.

You can run the tour again later from:

**Help → Take the tour**

On the first packaged launch, EDL also explains its local COVAS:NEXT connection before starting that listener. Windows may ask whether Elite Dangerous Lighting is allowed to communicate on the network when this connection starts.

EDL's COVAS:NEXT listener is bound only to `127.0.0.1` (this PC), so it is reachable only through the local loopback interface. EDL does not bind this listener to your LAN or Internet-facing network interfaces.

A normal EDL workflow is:

```text
1. Check your lighting devices
2. Create or open a profile
3. Add Rules and/or Scripted Modes
4. Preview what you created
5. Apply your changes
6. Save the profile
7. Start lighting
```

A **profile** is your saved EDL lighting setup. It contains the Rules and Scripted Modes you create.

The two main ways of controlling your lighting are:

```text
RULE
React automatically to something that is currently true

SCRIPTED MODE
Play a timed lighting sequence
```

---

# Lighting devices

Open:

**Setup → Lighting devices**

to see the hardware EDL has detected.

Some physical devices can be reached through more than one lighting integration. For example, the same device might be visible through both Razer Chroma and OpenRGB.

In that case, **Control via** tells EDL which integration should control that physical device.

For example:

```text
Keyboard
Control via: Razer Chroma
```

EDL does not silently switch to another integration if the selected one becomes unavailable.

---

# Zones

A **zone** is a smaller controllable part of a lighting device.

Instead of controlling one complete light:

```text
Desk light
```

you might divide it into:

```text
Left console
Centre
Right console
```

Rules and Scripted Modes can then control those areas separately.

Zone configuration is available under:

**Setup → Zones → Chroma zones**

and:

**Setup → Zones → Govee zones**

OpenRGB-reported zones are discovered from the device topology rather than configured from this menu.

Once a configured zone is available, it appears as a normal lighting target in the Rule and Mode editors.

---

# Rules

Rules make your lighting react automatically to Elite Dangerous or to your controls.

Every Rule has three main parts:

**Source** — when the Rule should activate  
**Target** — which light or zone it controls  
**Effect** — what that light should do

For example:

```text
Source
LandingGearDown = True

Target
Keyboard

Effect
Static orange
```

---

## Source — when should the Rule activate?

A Rule can react to:

- Elite Dangerous state
- a keyboard combination
- a button or switch
- an analogue control

### Elite Dangerous state

Elite Dangerous writes its current ship state to a file called `Status.json`. EDL reads that file directly.

It contains information such as whether the landing gear is down, whether the ship is docked, whether the FSD is charging, whether the ship is overheating, whether you are in Supercruise, and many other current states and values.

To see the complete set of Elite Dangerous state values that EDL currently knows about, open:

**Tools → Troubleshooting → Elite status**

For example:

```text
LandingGearDown = True
```

means that the Rule is active while Elite Dangerous reports the landing gear as down.

---

## More than one condition

A Rule can contain several Elite Dangerous conditions.

When it does, **all of them must be true at the same time**.

For example:

```text
InMainShip = True

AND

LandingGearDown = True
```

This Rule only activates while you are in your main ship **and** the landing gear is down.

---

## Keyboard, buttons and switches

A Rule can react to a keyboard combination such as:

```text
CTRL + SHIFT + F10
```

It can also react to buttons and switches on compatible joysticks, throttles, button boxes and other controllers.

A button Rule can react when the control is **Pressed** or **Released**.

---

## Analogue controls

An **analogue control** has a changing position rather than simply being On or Off.

Examples include:

- a throttle lever
- a joystick axis
- a rotary dial
- a slider

A Rule can react to its value.

For example:

```text
Throttle above 75%
```

or:

```text
Throttle between 25% and 50%
```

EDL can also use an analogue control to vary lighting continuously where the selected lighting behaviour supports it.

---

# Rule order matters

EDL evaluates Rules **from top to bottom**.

If two active Rules control the same lighting output, the matching Rule lower in the list takes precedence for that output.

For example:

```text
Rule 1
InMainShip = True
→ Keyboard blue
```

followed by:

```text
Rule 2
LandingGearDown = True
→ Keyboard orange
```

With the landing gear up, only Rule 1 matches:

```text
Keyboard = blue
```

When the landing gear goes down, both Rules match.

Because Rule 2 is lower in the list and controls the same keyboard:

```text
Keyboard = orange
```

Moving Rules up or down therefore changes the result when their outputs overlap.

This makes it possible to create broad general Rules first and place more specific exceptions underneath them.

---

# Targets

The **Target** is the light controlled by a Rule.

Depending on your hardware and setup, a Target might be:

- an entire keyboard
- a mouse
- part of a keyboard
- a configured Chroma zone
- an OpenRGB lighting zone
- a Govee zone
- another lighting output exposed by EDL

A Rule can also contain more than one independently configured lighting output.

---

# Effects

The **Effect** determines what the selected lights do.

Available effects include:

- Static
- Flash
- Pulse
- Breath
- Spectrum
- Wave
- Starlight
- Fire
- Reactive
- Ripple

Different effects have different controls.

For example, a Flash effect needs timing settings, while a Wave can also use direction.

EDL shows the controls relevant to the selected effect.

Lighting hardware differs, so not every physical device can reproduce every effect in exactly the same way.

---

# Preview and testing

You can inspect or test what you are editing without starting the complete profile.

**Preview effect** shows the selected effect, colours and parameters in the editor. It is software-only: it does not acquire lighting hardware and does not evaluate the Rule source.

For a Rule, **Test rule** runs only the Rule currently open in the editor on its real selected lighting targets. It uses the current Rule source or activation condition and the normal device ownership, renderer and restoration paths.

For a Scripted Mode, **Preview mode** plays the current Mode sequence on its selected lighting targets through the normal renderer and restoration paths.

These tools use the current editor state, so you can check changes before applying them.

A normal editing flow is:

```text
Edit
 ↓
Preview effect / Test rule / Preview mode
 ↓
Apply
 ↓
Save
```

---

# Scripted Modes

A **Scripted Mode** is a timed lighting sequence.

Rules describe what your lighting should do while a condition is true. A Mode is for a temporary sequence that should run from beginning to end.

The name you give a Mode in EDL is also the name you use if you later start that Mode through COVAS:NEXT.

For example:

```text
RED ALERT

Phase 1 — Alert — 2 seconds
Keyboard → flashing red
Cockpit strip → flashing red

Phase 2 — Hold — 8 seconds
Keyboard → dark red
Cockpit strip → pulsing red
```

---

## Phases

A Mode is divided into **phases**.

A phase is one timed step in the sequence.

Each phase has:

- a name
- a duration
- one or more lighting outputs

EDL plays the phases from top to bottom.

For example:

```text
1. Warning    2 seconds
2. Hold       8 seconds
3. Recovery   2 seconds
```

---

## Several lights in one phase

A single phase can control several outputs at the same time.

For example:

```text
Phase: Warning

Keyboard
→ flashing red

Mouse
→ solid red

Cockpit strip
→ red wave
```

Each output can have its own Target and Effect.

---

## What happens when a Mode ends?

A Mode is temporary.

While it runs, the normal Rule system continues to follow the current Elite Dangerous state underneath it.

When the Mode ends, EDL returns to the lighting that is appropriate **at that moment**.

For example, if the landing gear was raised while a Mode was running, EDL returns to the Rule state for landing gear up rather than restoring an old lighting state from before the Mode began.

---

# Profiles

A profile contains your saved:

- Rules
- Scripted Modes

Hardware selection, **Control via** choices and zone setup are kept separately from the profile.

Use:

**Open profile**

and:

**Save profile**

for normal profile management.

You can keep one general profile or create several for different ships, cockpit layouts or lighting styles.

EDL can also load your last profile automatically when it starts.

---

# Importing a VIRPIL lighting profile

If you already use the **VIRPIL VPC Link Tool**, EDL can import its `.led.json` lighting profiles.

The importer preserves the original Rule structure rather than reducing it to a few approximate colours.

Imported information can include:

- Rule order
- conditions
- comparison operators
- buttons
- axes
- keyboard inputs
- device and LED targets
- colours
- steady or flashing behaviour
- enabled state
- comments

Some imported targets may not have an equivalent on your current lighting hardware. EDL can preserve that information even when it cannot currently render that target.

---

# Starting and stopping lighting

Once your profile and devices are ready, choose:

**Start lighting**

EDL then begins applying the current profile.

While lighting is running:

- Elite Dangerous state continues to update
- Rules are continually recalculated
- effects continue to animate
- Scripted Modes can temporarily take control when started

Choose:

**Stop lighting**

to end the EDL lighting session and release the devices back to their normal lighting software.

---

# Integration with COVAS:NEXT

Everything described so far works without COVAS:NEXT.

[**COVAS:NEXT**](https://ratherrude.github.io/Elite-Dangerous-AI-Integration/) is a separate voice and AI assistant platform for Elite Dangerous. If you use it, EDL can optionally be connected to it for voice-controlled lighting and, if desired, Spotify music attached to Scripted Modes.

Two separate COVAS:NEXT plugins are involved:

**[Chromas Next](https://github.com/DocTrintignant/covas-next-plugins/tree/main/ChromasNext)** connects COVAS:NEXT to EDL.  
**[Covasify](https://github.com/DocTrintignant/covas-next-plugins/tree/main/Covasify)** adds optional Spotify control.

You need Chromas Next for COVAS:NEXT to control EDL. You only need Covasify if you also want Spotify functionality.

Install COVAS:NEXT plugins under:

```text
%APPDATA%\com.covas-next.ui\plugins\
```

Copy the published `ChromasNext` and, if wanted, `Covasify` folders there, then restart COVAS:NEXT. Each plugin's own README contains its setup instructions.

For COVAS:NEXT lighting commands and saved Mode launches, **Start lighting** must already be running in EDL. Connection/status checks remain separate from that live-lighting requirement.

## Voice control with Chromas Next

**[Chromas Next](https://github.com/DocTrintignant/covas-next-plugins/tree/main/ChromasNext)** is the bridge between COVAS:NEXT and EDL.

It does not control RGB hardware itself. EDL remains responsible for your lighting; Chromas Next simply passes requests from COVAS:NEXT to EDL.

```text
You speak
    ↓
COVAS:NEXT
    ↓
Chromas Next
    ↓
EDL
    ↓
Your lights
```

You can ask for things such as:

```text
"Is Elite Dangerous Lighting connected?"

"Make the keyboard red."

"Start Red Alert."

"Stand Down."
```

To start a saved EDL Mode through COVAS:NEXT, ask for the Mode using the name you gave it in EDL. For example, if the Mode is named `RED ALERT`, say **“Start RED ALERT.”**

You can check the connection from:

**Setup → COVAS:NEXT connection**

## Optional Spotify music with Covasify

**[Covasify](https://github.com/DocTrintignant/covas-next-plugins/tree/main/Covasify)** is a separate COVAS:NEXT plugin that controls Spotify.

It is not required for EDL and it is not required for voice control of EDL.

An EDL Scripted Mode can optionally contain a music cue. If the **Covasify bridge for Mode audio** is enabled in Chromas Next, starting that Mode can also pass its music request to Covasify.

The two paths remain separate:

```text
COVAS:NEXT
    ↓
Chromas Next
    ↓
EDL
    ↓
Lighting
```

and, when Mode music is enabled:

```text
Chromas Next
    ↓
Covasify
    ↓
Spotify
```

A Spotify problem does not take control away from EDL or cancel lighting that has already started.

In short:

**Chromas Next connects COVAS:NEXT to EDL. Covasify adds optional Spotify playback.**

---

# Troubleshooting

## A lighting device does not appear

Open:

**Setup → Lighting devices**

and check whether EDL detects it.

If the same device is available through more than one integration, also check its **Control via** selection.

---

## A device is listed but unavailable

The selected lighting integration may not currently be reachable.

Check the software or connection used by that device, then reopen **Lighting devices** to confirm its status.

---

## A Rule does not activate

Check:

- the Rule is enabled
- its Source matches the current state
- every condition in a multi-condition Rule is true
- the Target is available
- another matching Rule lower in the list is not overriding the same output

Useful diagnostic tools are available under **Tools**.

**Tools → Troubleshooting → Elite status** shows the current Elite Dangerous state EDL can see.

**Tools → Test rules** lets you inspect Rule behaviour by temporarily pretending state or input values instead of waiting for the situation to occur in game.

---

## A Scripted Mode does not run

Check that:

- the Mode exists in the current profile
- its changes have been applied
- its lighting Targets are available

For voice operation, also check:

**Setup → COVAS:NEXT connection**

---

## Mode lighting works but Spotify does not

Check that:

- Covasify is installed
- Covasify is connected to Spotify
- the Chromas Next option for Mode audio is enabled

The lighting and Spotify parts are separate, so a Spotify problem does not cancel an EDL lighting Mode that has already started.

---

# Updates

EDL performs a non-blocking check for a newer stable release shortly after startup, once the startup dialogs are clear.

If a newer version is available, EDL shows an update message. Choosing **Download** opens the official GitHub release page.

If the check cannot complete because EDL is offline or the release service is unavailable, startup continues normally and no update error blocks the application.

---

# Cockpit gameplay lighting — design checkpoint (11 October 2026)

> **Design ledger, not a shipped gameplay preset.** This checkpoint records operator-approved concepts and remaining validation. It does not imply that the new cabin-state resolver or the sequences below are already implemented or physically accepted. Exact RGB, brightness, speed and timing remain open unless noted.

## Ownership and method

EDL stays standalone and deterministic; preserve accepted renderers, transport ownership, profile formats, Status.json state authority and startup choreography. Physical cockpit behavior outranks software-only verification.

- **VIRPIL** — discrete statuses, instrumentation, controls and annunciators.
- **Enhanced Govee H61C3** — cabin/environment: the operating situation the commander occupies.
- **ASUS/OpenRGB** — machinery: power, propulsion, charge, load, thermal stress and recovery. Gameplay semantics still need their own pass.
- Razer keyboard/mouse and ASUS monitor remain excluded from Elite gameplay lighting in this cockpit composition.

The design pipeline is **Elite state → semantic role → resolved cabin state → color/effect intent → rendering**. Classify flags as **DIRECT VISUAL STATE**, **MODIFIER**, **GUARD / CONDITION**, or **NO GOVEE EFFECT**. Multiple flags can resolve to one meaningful cabin state. Keep the aesthetic dark, technical and restrained. Black/off is a valid output; avoid one flag = one color. The uploaded VIRPIL V2.6 profile is an aesthetic/historical reference, not a palette to duplicate.

## Govee gameplay vocabulary

The following are accepted *concepts*, not final physical calibration.

| Situation | Color family | Intended effect |
| --- | --- | --- |
| Docked / Landed | Muted deep green | **STATIC** — shared secured state |
| Normal open-space flight | Deep burnt amber / ember orange | **STATIC** |
| Planetary flight / vicinity | Dark bronze / sand-gold | **STATIC**; exact HasLatLong-based resolver remains open |
| Supercruise | Deep cobalt / ultramarine with visibly contrasting endpoints | **WAVE**; a physically tried 4-second WAVE already felt fairly slow |
| Glide | Cold or icy white | Rapid/dense **STARLIGHT** |
| Fighter | Graphite / near-black neutral | **STATIC**; a separate cabin |
| SRV | Muted olive / sulphur-khaki | **STATIC** |
| SilentRunning | Deep violet | Slow **BREATH**; not an alarm |
| BeingInterdicted | Supercruise cobalt invaded by hot magenta/purple | Hard **FLASH** |
| IsInDanger | Deep red ↔ strong red | Red-on-red **FLASH**, not red-black |
| OverHeating | Predominantly thermal red | **FIRE**, not decorative orange/yellow; initially assessed around 0.10-second noise cadence |
| Battle Ready | Dim submarine / naval-control-room red | **STATIC**, deliberate tactical posture rather than danger |

**Battle Ready** requires **HardpointsDeployed = 1 AND HudInAnalysisMode = 0** (Combat HUD), guarded by **InMainShip**. Hardpoints alone could also be mining equipment; HUD mode alone is not a combat posture. Analysis mode with deployed hardpoints does not create a Mining Mode. This two-condition resolver awaits a real-game test. Generic danger and overheating remain separate, stronger semantic conditions.

### Four-state FSD environmental language

VIRPIL's physical T7/T8 protective covers are arming/annunciator controls only; merely opening a cover must not change Govee or ASUS.

| FSD phase | Color family | Effect |
| --- | --- | --- |
| Supercruise charging | Dark heated amber → pale warm white, not saturated gold | **PULSE** |
| Established Supercruise | Cobalt / ultramarine | **WAVE** |
| Hyperspace charging | Electric violet → cold/pale white (more energetic than Silent Running) | **BREATH** |
| Hyperspace / witchspace tunnel | Cold-white dominant, restrained pale cyan/ice-blue | **FIRE** as turbulent spatial noise, not literal flame |

The current decoder in **src/elite_status.py** provides **FsdCharging**, **FsdHyperdriveCharging**, **FsdJump** and **Supercruise**. Proposed Status-only interpretation: FsdHyperdriveCharging identifies a hyperspace cycle, while FsdJump rising/falling *within that known cycle* may bracket the witchspace tunnel. FsdJump is **not** hyperspace-exclusive; it also occurs around Supercruise entry. Correlate all four flags against a real charge/countdown/tunnel/arrival trace before accepting the state machine. Do not substitute a fixed-duration tunnel timer for unproven telemetry. On exit reveal the **current** resolved cabin state, normally Supercruise.

**FsdCooldown** is **NO GOVEE EFFECT**, applies after both Supercruise and hyperspace transitions, and may later inform ASUS recovery. **FsdMassLocked** is VIRPIL instrumentation and **NO GOVEE EFFECT**; it might be a future guard, not a cabin scene.

### Sustained landing preparation and arrival

**LandingGearDown is not itself a cabin mode.** The new landing-preparation concept is sustained for a *qualified landing approach* and works for both planetary landing and station docking.

1. **Enter guidance** when landing gear is down **and** a valid planetary/station approach is established. Lowering gear randomly in empty space must **not** activate it. Qualification after gear was already lowered should also be considered; exact trigger/resolver logic is not frozen.
2. **While approaching**, run a **continuous, repeating** landing-marshalling environment. This does **not** expire after a fixed number of seconds; it lasts while the qualifying approach remains in progress.
3. **Approved color family:** **marshaller red + soft, slightly warm signal white**, inspired by naval flight-deck landing guidance. Avoid emergency-strobe intensity. Exact RGB values are **not** yet accepted.
4. **First effect to test on real hardware:** existing **multi-entry WAVE** with repeated red and white palette entries. Current **src/lighting_effect_frames.py** WAVE smoothly interpolates adjacent palette colors; repeated entries can produce flatter moving bands, **not mathematically sharp red/white boundaries**. Validate movement, perceptual separation, brightness, period and the installed H61C3 orientation before locking this effect. Do not modify the accepted general-purpose WAVE engine pre-emptively.
5. **Complete** at **Landed false→true OR Docked false→true**: stop guidance, present bright-green **one-shot arrival punctuation**, then muted deep-green **STATIC**. The previously accepted planetary touchdown punctuation is green trigger-scoped **RIPPLE**. The exact station-docking punctuation, informally described as a green flash, is still to be resolved without silently changing the accepted touchdown mechanism.
6. **Abort/interrupt:** gear retraction or loss of qualified approach cancels guidance and reveals the **current** underlying cabin state. Established operator/scripted authority and higher-significance hazard behavior remain intact; underlying Elite telemetry continues to update.

**First unproven telemetry boundary:** Status.json exposes LandingGearDown, Landed, Docked, HasLatLong and related planetary position/altitude values, but does not reliably report an authorized **station** approach. A narrowly scoped future **Elite Dangerous Journal listener**, possibly observing **DockingGranted** plus appropriate cancellation, revocation and termination events, **may** be needed for a safe station qualifier. This is only a **candidate to investigate**, *not* an approved or implemented listener/state machine. Do not invent a Status-only proxy such as mass lock = docking clearance. Status.json remains the canonical state authority. Planetary approach qualification also requires real telemetry testing.

### Other classified Status fields

- **NO GOVEE EFFECT:** LowFuel, ScoopingFuel, ShieldsUp (including shields-down condition), FlightAssistOff, CargoScoopDeployed, LightsOn, NightVision and SupercruiseAssistActive. These retain the current cabin; instrumentation belongs primarily to VIRPIL.
- **GUARD / CONDITION:** HudInAnalysisMode participates in Battle Ready only; no independent color. HardpointsDeployed alone does not change the cabin; it only qualifies Battle Ready with Combat HUD. LandingGearDown alone does not activate landing guidance in empty space.
- **BLACK / OFF guard:** OnFoot, InTaxi, social spaces, concourse and other situations outside the commander's own cockpit.
- **OPEN:** SupercruiseOverdriveActive / SCO. Cold FIRE now belongs to witchspace; establish a separate environmental concept rather than reusing it by convenience.

### Preserved startup and next work

Startup structure remains frozen: card-insertion **one-shot RIPPLE** travels physically from right-side card reader toward left, then darkness; WinWing MFD's green ignition punch owns GO; ASUS follows with one continuous bottom-to-top, overlapping PSU → GPU → motherboard → front/AIO power-up. At the final authority transfer Govee fades into the **current live** cabin and ASUS settles into the **current live** machinery state. Never restore stale captured status.

The installed H61C3 has shown apparent LEFT/RIGHT inversion. Investigate saved orientation/runtime mapping rather than reversing shared effect semantics; describe movement in **physical cockpit** terms.

After completing Govee semantics and resolving SCO, define ASUS machinery semantics, derive startup colors from those gameplay vocabularies, and physically tune colors, brightness, timing, effect widths and overlaps. Any later keyboard-only Govee physical-test profile must use the then-current native profile schema and actual configured H61C3 target ID.

---

# Planned development

A future version is planned to support selected **Elite Dangerous Journal events** as triggers for temporary lighting behaviour.

`Status.json` will remain the source for current-state Rules.

The distinction is:

```text
Current state
Status.json
    ↓
Rules
```

and, in a future version:

```text
Something happens
Elite Dangerous Journal
    ↓
Saved Mode or temporary action
```

Journal support is intended to extend the existing system, not replace it.

A separate potential Journal use is to qualify **sustained Govee landing preparation** during authorized station approaches (for example, via DockingGranted). That possibility is documented above but has **not been implemented or approved as a new telemetry source**; Status.json continues to own current-state Rules.

---

# Licence

Elite Dangerous Lighting is made available under the [PolyForm Noncommercial License 1.0.0](LICENSE).

Non-commercial use, modification and redistribution are permitted under the licence. Commercial use requires a separate licence from the copyright holder.

Forks and derivative projects are welcome for non-commercial use. If you plan to publish or maintain a derivative project, please contact the maintainer first. This is a courtesy request, not an additional condition of the licence.
