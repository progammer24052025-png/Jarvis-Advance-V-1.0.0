# Jarvis pet: a cute desktop companion, plus new icons

## 1. Goal

Turn Jarvis's floating orb into a small, very cute **pet** that lives on your desktop.

- It shows what Jarvis is doing (listening, thinking, working, speaking) at a glance, without opening the
  window.
- It reacts to you, so Jarvis feels alive.
- Its face becomes Jarvis's icon everywhere: app, tray, Start menu, installer, notifications, dashboard and
  the phone app.

It is an **original character**: it shares the idea of a desktop companion, but it does not copy any other
app's pets.

**Today:** `ui.FloatingLauncher` is a 90 px always-on-top orb with rings and particles, plus click, drag
and a menu. The pet replaces it and keeps all of its signals and menu actions, so nothing else breaks.

## 2. The character (meet "Orbi")

A round, chubby little glow-creature: Jarvis's orb grown a face.

| Part | Design | Why it's cute |
|---|---|---|
| Body | A soft, slightly squashed sphere with a gentle inner glow, in Jarvis's accent colour (changes live with Customise) | Round + soft = friendly; it's still "the orb" |
| Eyes | Two big, glossy, rounded eyes with a white sparkle highlight; they blink every few seconds and follow your mouse | Big eyes with highlights are the core of "cute" |
| Cheeks | Two small pink blush dots | Instantly reads as "sweet" |
| Mouth | Tiny; a small smile at rest, it moves with Jarvis's voice when speaking | Expressive without being busy |
| Antenna | A short antenna with a tiny glowing bulb that pulses | Says "smart helper", and gives a place to show status |
| Hands | Two little floating nubs (no arms) that wave, hold things and cover its mouth | Lets it act out states |
| Shadow | A soft oval shadow under it that grows and shrinks as it bobs | Makes it feel like it's floating in your space |

**Size:** 64 / 88 / 112 px (Small / Medium / Large). It is drawn with code, not images, so it stays crisp
at any size and any colour, and works in light and dark.

**Style rules:**
- Proportions: head-heavy, eyes about ⅓ of the face, everything rounded, no sharp corners.
- Easing: every move is springy, with squash and stretch on bounce.
- Never creepy: no realistic details and no stare when you're not interacting. Idle glances drift away
  naturally.

## 3. Moods (one per Jarvis state)

| Jarvis state | What Orbi does |
|---|---|
| Idle / ready | Gentle bob, slow blinks, eyes follow the cursor; sometimes stretches or looks around |
| Listening | Antenna bulb glows bright, eyes go wide, soft sound ripples around it, leans toward you |
| Thinking | Eyes look up and to the side, a small thought bubble with three bouncing dots, antenna bulb spins |
| Working (running a task) | Holds a tiny glowing progress ring, focused squint, little "busy" wiggle |
| Speaking | Mouth opens and closes with the voice level, small happy bounces |
| Done / success | A hop with a sparkle burst, then back to idle |
| Muted (mic off) | Covers its mouth with both hands (or a tiny zipper), a small "mic off" badge |
| Silent / "don't talk" rule | Wears tiny headphones, shows a soft envelope badge when it texts you instead |
| Focus mode | Closes its eyes with a calm smile, very dim, almost still; only wakes for something important |
| Quiet hours / sleep | Curls up and sleeps with small "z z z" |
| Safety hold (needs your OK) | Raises one hand with a "!" bubble and waits; this one always shows |
| Error | Brief dizzy swirl eyes, then a sheepish smile |
| New message / reminder | Waves and shows a speech bubble with a short line |

## 4. How it behaves

- **Always there, never in the way.**
  - It stays on top and you can drag it anywhere.
  - It snaps softly to screen edges and the top of the taskbar, and remembers its spot on each monitor.
  - It hides automatically while a full-screen app, game or presentation is running, and returns afterwards.
- **Click:** a giggle and a little bounce.
- **Double-click:** opens Jarvis.
- **Right-click:** the current launcher menu (mute, open, settings and so on).
- **Hover:** a small card with what Jarvis is doing now ("Researching flights to Goa…"), with a progress
  ring for long tasks.
- **Speech bubbles:** short lines only (one sentence, about 4 seconds), and they follow the Output Router:
  - in silent mode, a badge instead of a bubble;
  - in focus mode, nothing unless it's important;
  - safety holds always show.
- **Show or hide it:**
  - say *"show the pet"* / *"hide the pet"*;
  - tray menu → Pet;
  - Customise → Pet;
  - a hotkey (Ctrl+Alt+P).
- **Customise → Pet:**
  - on/off and size;
  - lively or calm animation, and reduce motion (which follows the Windows setting);
  - sound effects (off by default);
  - the pet's name (default "Orbi").

## 5. Privacy and performance

- **Privacy:** while the PC is locked, or the screen is being shared or recorded (when Windows tells us),
  bubbles and hover cards show only "Jarvis is working": never task content. The pet never shows keys,
  message text or notification bodies.
- **Performance:**
  - 30 fps while it moves, about 4 fps while idle, and fully paused when hidden, locked or full-screen.
  - Target: under 1% CPU on an average laptop. A test checks that the idle timer slows down.
- **Accessibility:** the pet has an accessible name and status, so screen readers announce "Jarvis:
  thinking".

## 6. New icons from the pet

One script, `tools/make_icons.py`, draws every icon from the same pet drawing code, so all icons always
match:

| Where | What |
|---|---|
| App / window / taskbar / Start menu / Apps & Features / installer / uninstaller | Multi-size `.ico` (16, 20, 24, 32, 40, 48, 64, 128, 256) |
| Tray | One icon per state: idle, listening, muted, alert. At 16 px it's a simplified face: glowing circle + two eyes. |
| Notifications | 256 px PNG of the happy face |
| Setup wizard, uninstall window, About | Large PNG / live pet |
| Dashboard (web) | Favicon, app logo, login page |
| Android companion app | Launcher icons for every density (mdpi to xxxhdpi), adaptive icon foreground + background, notification icon |
| README, GitHub | Logo, social preview image, and new setup screenshots showing the pet |

- The old `Jarvis_Lite_Logo.*` names are kept as copies, so nothing that already points at them breaks.
- New names (`jarvis_pet_*.png/.ico`) are used from now on.
- The leak scanner and the clean-room check already cover `assets/`.

## 7. Phases

Status:
- **Phase 1: done.** The owner chose **A · Orbi Classic** (preview in `experiments/pet_preview`).
- **Phase 2: done.**
  - `gui/pet.py` replaces the floating orb in `ui.py`; the old orb is kept as a fallback.
  - `tests/test_pet.py` covers it.
  - It measured about 0.5% CPU.
- **Phase 3: done.**
  - Hover card, speech bubbles via the Output Router (focus, quiet-hours and lock rules), and a Customise → Pet row.
  - Tray Show/Hide Pet, Ctrl+Alt+P, and voice "show/hide the pet / <name>".
  - Edge snap, multi-monitor, full-screen hiding, and calm/lively plus Windows reduce-motion.
  - Settings live in `config/pet.json`.
- **Phase 4: done.**
  - `gui/pet_icons.py` and `tools/make_icons.py` make all the icons: app .ico (16–256), large logo,
    per-state tray icons, dashboard favicon and logo, Android launcher icons, README logo and social preview.
  - Old icons are kept in `build/old_icons`.
- **Phase 5: not started.**

| Phase | Work | Done when |
|---|---|---|
| 1 | **Design sheet:** 3 variations of Orbi (rounder / with ears / with a tiny visor), each shown in all moods, at 256 px and 32 px | You pick one (or a mix); nothing in the app changes yet |
| 2 | `gui/pet.py`: the chosen design, drawn and animated in code; replaces `FloatingLauncher` with the same signals and menu | Pet works on the desktop with all moods wired to Jarvis's real states |
| 3 | Behaviours: drag/snap, cursor-following eyes, hover card, speech bubbles via the Output Router, full-screen hiding, focus/silent/sleep looks, settings, voice and hotkey | Every row of §3 and §4 works; screenshots for review |
| 4 | Icons: `tools/make_icons.py` generates every icon in §6 and the app switches to them (app, tray per state, notifications, MSI, uninstaller, dashboard, Android) | All icons match; tests pass; leak scan clean |
| 5 | Tests, performance check, README and screenshots, build `Jarvis-1.0.2.msi`, GitHub release | Published |

**Tests:**
- every Jarvis state maps to a mood;
- the idle frame rate drops and the pet pauses when hidden;
- bubbles respect silent, focus and lock rules;
- icons exist at every required size.
