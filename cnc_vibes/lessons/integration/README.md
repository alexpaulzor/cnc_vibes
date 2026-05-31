# lessons/integration — talk to the machine, see what's happening

Standalone Python tools that talk to the GRBL controller or watch the machine. Each is a regular CLI with `--help` — invokable from your shell, from a script, or from anywhere else. No LLM in the loop.

The rest of the repo treats the machine as a write-only target: generate `.gcode` files, hand them to a sender. These tools close the loop:

- **Verify reality matches plan.** `inspect` queries GRBL state (alarm, settings, WCS, version, WiFi IP) so the preflight checklist can check, not just ask.
- **Capture setup state for the record.** `snapshot` grabs webcam stills attached to the run.
- **Automate the touch-off ritual.** `probe-corner` drives the spindle through a touch-plate sequence and sets the WCS automatically.
- **Iterate laser parameters by feel.** `interactive_cal` cuts one test per param-tweak cycle until you converge on optimal Z / power / feed / passes.

## Sub-lessons

| # | Tool | Status | Purpose |
|---|---|---|---|
| Int-01 | [`inspect`](01_inspect/) | ✅ | Read GRBL state via serial (`?`, `$$`, `$#`, `$I`). Parses Grbl_ESP32's WiFi info from `$I`; `--ip-only` extracts IP for shell scripting. |
| Int-02 | [`snapshot`](02_snapshot/) | ✅ | One-shot webcam image, written to disk with a timestamp. Useful for before/after-cut records attached to a job log. |
| Int-03 | [`probe-corner`](03_probe_corner/) | ✅ | Drive the spindle through a touch-plate routine to find the front-left corner of stock and set the WCS. Saves 2-3 minutes per job. |
| Int-04 | [interactive laser cal](04_interactive_laser_cal/) | ✅ | Drives the laser to cut one test target per iteration; operator evaluates and adjusts params per cycle. Supports `--mode cut` (circle for kerf/cut tuning), `--mode engrave` (raster patch for grayscale-engrave power tuning), and `--telnet` for raw-TCP transport on Grbl_ESP32. |
| Int-05 | [`jog`](05_jog/) | ✅ | Xbox controller + keyboard jogger with inline Z-probe. Default Z-probe travel is 250mm (Candle errors past 50mm); A button (or `P` key) starts probe, B (or `Esc`) cancels any motion. Reuses the `interactive_cal` transport layer. |

## Design constraints (carried forward)

- **Standalone, not Claude-dependent.** Each tool is a regular Python CLI. Claude can invoke them just like the user does; they don't import or know about an LLM.
- **Read-mostly default.** The tools that talk to the controller default to read-only queries. The ones that issue motion (probing, laser cal) are opt-in per-invocation and prompt before firing.
- **Safety is on the operator.** The probing + laser cal tools require the user to be at the machine with an e-stop reachable. Each prints what it's about to do and waits for confirmation. ALARM states block motion-issuing commands.
- **Cross-platform.** Windows + macOS + Linux. Serial via `pyserial`; mDNS via `zeroconf`; raw TCP via `socket`.

## Integration with `cnc.py preflight`

The vision was: `cnc.py preflight` auto-verifies the checkable items via `inspect`, attaches a `snapshot` to the run log, and only prompts for the truly-physical items.

Today, each Integration tool runs standalone. Wiring them into `preflight` is a follow-up. The pattern is established (each tool has a clean Python API alongside its CLI surface, importable from `preflight`).

## What this workstream is NOT

- **Not a sender.** Real-time GCode streaming stays with gSender (or your sender of choice). These tools don't try to compete.
- **Not a CV-based safety system.** Vision-based watchdogs (catch tool break, catch fire) need a dedicated process running locally with millisecond latency. `snapshot` is for pre/post-job artifacts, not mid-job monitors.
- **Not autonomous machining.** Probing writes the WCS but doesn't decide what to do next. The operator still drives the job.

## Dependencies these tools add (in `requirements.txt`)

- **`pyserial`** — `inspect`, `probe-corner`, `interactive_cal`, `jog` for talking to GRBL over USB.
- **`zeroconf`** — `cnc.py find-machine` for mDNS discovery on Grbl_ESP32.
- **`opencv-python-headless`** — `snapshot` for webcam capture (also pulled in by 3c jigsaw for letter contour tracing).
- **`Pillow`** — `snapshot` image writing.
- **`pygame`** — `jog` for xbox controller input via SDL.

All installed by the standard `pip install -r requirements.txt`.
