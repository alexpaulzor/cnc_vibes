# lessons/integration — talk to the machine, see what's happening

> **Status: planning only — no code yet.** This directory captures the design for a workstream that crosses out of the "generate GCode files" lane into "actually communicate with the machine and its surroundings." Three sub-lessons are speced; each will be implemented as a **standalone Python script** that any human (or other tool) can run without Claude or any LLM in the loop.

## Motivation

The rest of the repo treats the machine as a write-only target: we generate `.gcode` files, you load them into a sender, the sender streams them to the controller. That works fine for the cuts themselves but leaves a gap:

- **The preflight checklist asks the user "is X true?" but can't verify X itself.** If you swear you set the WCS to the stock corner but actually didn't, the checklist won't catch it.
- **Setup mistakes are visible but ephemeral.** Wrong tool in the spindle, clamp in the toolpath, stock shifted half a millimeter — these are obvious in person but lost the moment you turn around.
- **Manual zeroing burns iteration time.** Every job starts with the same touch-off ritual (find front-left corner, probe Z). A macro that drives the machine to do it would save minutes per job.

The integration lessons close those gaps with three standalone tools that any user can run from a terminal.

## Constraints

- **Standalone, not Claude-dependent.** Each tool is a Python script with `--help`. Nothing in the design requires an LLM in the loop. Claude (or any other automation layer) can *invoke* them just like the user does — they're regular CLI tools.
- **Read-mostly default.** The tools that talk to the controller default to read-only queries (`?`, `$$`, `$#`). The one tool that issues motion (probing) is opt-in per-invocation and produces a small, reviewable GCode sequence rather than running freely.
- **Safety is on the operator.** Even the probing tool requires the user to be at the machine with an e-stop reachable. The tool prints what it's about to do and waits for confirmation.
- **Windows-first.** Like the rest of the repo, all tools must work on Windows 11 (the deployment target).

## Sub-lessons

| # | Tool | Status | Purpose |
|---|---|---|---|
| 01 | [`inspect`](01_inspect/SPEC.md) | planned | Read GRBL state via serial — alarm state, settings, WCS offsets, version. Catches "machine not homed", "$32 in wrong mode", "WCS not where you think it is." |
| 02 | [`snapshot`](02_snapshot/SPEC.md) | planned (future) | Capture one-shot images from a USB webcam pointed at the bed. Attach to the preflight log as "before-cut" / "after-cut" records. Verifies physical setup matches plan. |
| 03 | [`probe-corner`](03_probe_corner/SPEC.md) | planned | Drive the spindle through a touch-plate probing routine to find the front-left corner of stock and set the WCS automatically. Saves 2–3 minutes per job. |

## Suggested implementation order

1. **01_inspect first.** Smallest scope, zero motion, biggest checklist-quality win. Validates the serial-port-talking-to-GRBL pattern that 03 depends on.
2. **03_probe-corner second.** Builds on 01's serial pattern and the existing touch plate the user already owns.
3. **02_snapshot last.** Furthest from the existing toolchain, depends on having a camera physically mounted and lit. Punted to "future" until the cabling + bracket exist.

## Dependencies these will add

Captured upfront so it's not a surprise when implementation starts:

- **`pyserial`** for `01_inspect` and `03_probe-corner`. Standard library elsewhere; this is the only new runtime dep for the machine-talking tools.
- **`opencv-python`** or **`Pillow` + `imageio[ffmpeg]`** for `02_snapshot`. Heavier dependency; could be made optional (snapshot only loads cv2 when you actually use it).

`requirements.txt` would add these conditionally — perhaps split into `requirements-base.txt` (always needed) and `requirements-integration.txt` (only if you want these tools). The split avoids forcing 50MB of OpenCV on anyone who just wants the GCode generators.

## How these integrate with cnc.py preflight

The preflight checklist today asks the user yes/no questions. With these tools, several items can be *verified* rather than asked:

| Today's checklist item | What `inspect` could verify | What `snapshot` could verify |
|---|---|---|
| GRBL laser mode active ($32=1)? | yes — exact value of `$32` from `$$` output | n/a |
| Spindle motor unpowered? | partial — no spindle status pin in stock GRBL | yes (visual confirmation) |
| Material flat against bed? | n/a | partial (visual; no depth sensing) |
| Air assist on? | n/a | partial (look for mist / hear) |
| X/Y origin set correctly? | yes — `$#` returns current WCS offsets | partial (visual relative to fiducials) |
| Probe stowed clear of spindle? | n/a | yes |
| Area clear of flammables? | n/a | yes |
| Correct tool installed? | n/a | partial (visual, hard to read tool engraving) |
| GCode loaded in sender? | n/a | n/a (sender's own UI) |

The endgame: `cnc.py preflight <gcode>` auto-verifies the checkable items via `inspect`, attaches a `snapshot` to the run log, and only prompts for the truly-physical items the operator must confirm by looking.

This integration work is separate from the standalone tools — the tools come first, then `cnc.py preflight` learns to call them.

## What this workstream is NOT

- **Not a sender.** Real-time GCode streaming stays with gSender (or your sender of choice). Claude has no business in the streaming loop, and neither do these tools.
- **Not a CV-based safety system.** Vision-based watchdogs (catch tool break, catch fire) need a dedicated process running locally with millisecond-class latency. These snapshots are pre/post-job artifacts, not mid-job monitors.
- **Not autonomous machining.** The probing routine writes the WCS but doesn't decide what to do next. The user still drives the job.
