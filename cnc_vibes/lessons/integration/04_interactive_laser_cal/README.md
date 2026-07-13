# Integration 04 — interactive laser calibration

> Standalone Python tool. Drives the laser via serial. Each iteration: engraves an iteration number, cuts a small test circle at the current params, prompts the operator to evaluate and adjust before the next cut. Saves a per-run JSON manifest.
>
> Built specifically for **Z offset / focus calibration** where standard static patterns don't give enough resolution — but useful for power, feed, and pass-count tuning too.

## Why this exists

You can characterize a laser two ways:

1. **Static pattern** (lesson 3b's calibration): cut the entire matrix in one run, inspect afterward. Good when you know roughly the right range and want to sample across it.
2. **Interactive iteration** (this lesson): cut one test, look at it, adjust, cut next. Better when you're in unknown territory and want to converge by feel — particularly for Z focus where the right value depends on your specific lens/material/jig and the answer might be ±0.2mm of optimum.

No open-source tool I'm aware of does the interactive flow. LightBurn (proprietary) has a material library UI; LaserGRBL has static test cards; that's the published landscape.

## Usage

```
# Real run via USB serial
python lessons/integration/04_interactive_laser_cal/interactive_cal.py \
    --port /dev/ttyUSB0 \
    --origin-x 10 --origin-y 10 \
    --start-z 0 --start-power 100 --start-feed 400 --start-passes 2

# Real run via raw TCP (Grbl_ESP32 listens on port 23). Avoids the ~5s
# boot-banner reset that happens every time you open the USB port.
python lessons/integration/04_interactive_laser_cal/interactive_cal.py \
    --telnet 192.168.4.116

# Dry run (no serial; prints all GCode and uses defaults for all iterations)
python lessons/integration/04_interactive_laser_cal/interactive_cal.py \
    --dry-run --max-iterations 4
```

| Flag | Default | Meaning |
|---|---|---|
| `--port` | `$CNC_PORT` | Serial port (mutually exclusive with `--telnet`). |
| `--baud` | 115200 | GRBL standard. |
| `--telnet` | none | `host[:port]` for raw-TCP transport (default port 23). Avoids the boot-banner reset that happens on every USB port open with the Grbl_ESP32 build. Mutually exclusive with `--port`. |
| `--origin-x` / `--origin-y` | 10, 10 mm | Lower-left of first iteration slot. |
| `--slot-w` / `--slot-h` | 30, 30 mm | Slot size per iteration. |
| `--slots-per-row` | 6 | Wraps to next row after this many. |
| `--circle-dia` | 8 mm | Test cut diameter. |
| `--engrave-height` | 4 mm | Iteration-number digit height. |
| `--engrave-power-percent` | 25 | Low power so the label doesn't cut through. |
| `--start-z` / `--start-power` / `--start-feed` / `--start-passes` | 0, 50, 800, 1 | Initial params. **Conservative for Stage 1 (focus)** — lower than the material profile so an unknown-focus first run can't immediately combust. Override explicitly when you reach Stages 2-4 (typically `--start-power 100 --start-feed 400 --start-passes 2` to match the material profile). |
| `--mode` | `cut` | `cut` (default) emits a circle-cut test for kerf/cut calibration. `engrave` emits a raster-filled square patch at the iteration's params instead — for grayscale-engrave power calibration. Operator visually evaluates the darkness produced per S value; the manifest captures power → outcome. |
| `--patch-size` | 6 mm | Square patch size in engrave mode. |
| `--patch-line-spacing` | 0.20 mm | Raster line spacing in engrave mode (matches phase7_raster's default so cal mirrors real engrave behavior). |
| `--max-iterations` | 24 | Hard safety stop. |
| `--max-z-offset` | 10 mm | Reject Z values whose absolute value exceeds this. Bounds the blast radius of a typo at the prompt. |
| `--machine-profile` | `profiles/default.yaml` | Profile read for the envelope check. |
| `--skip-envelope-check` | off | Bypass the layout-vs-envelope check (use only if your profile is unrepresentative). |
| `--dry-run` | off | Print GCode without opening port. |

## Procedure for a novice operator

Follow this end-to-end the first time. Subsequent sessions can skip the setup steps you've already done.

### Stage 0 — Bench prep (no laser yet)

1. Choose a sacrificial scrap of the material you intend to cut for real. Size doesn't matter — 60×60mm is enough for one run with default slot size.
2. Tape or clamp the scrap onto the honeycomb so it can't shift. Any movement during a cut ruins all the readings after.
3. Run the script in `--dry-run` mode and read the GCode it prints. You should recognize the M4/G1/M5 pattern. This validates your install and surfaces config issues (envelope, Z bounds) before connecting the machine.

   ```
   python lessons/integration/04_interactive_laser_cal/interactive_cal.py --dry-run --max-iterations 4
   ```

   **Pitfall:** If you see `error: planned grid does not fit machine envelope`, fix your `--origin-*` / `--slot-*` flags first. The script refuses to send motion that would crash the head.

### Stage 1 — Z-offset / focus calibration

This is the hardest of the four parameters. The diode laser has a tight focal depth (~±0.5mm) and the right Z depends on your lens, your jig height, and the material thickness. Do this **first** — if focus is wrong, no amount of power/feed tuning fixes it.

**What "Z=0" means in this script**: an *absolute* Z position. The script does `G0 Z<value>` directly. Whatever Z you call zero in your work-coordinate system is your reference plane. The typical convention:

- Manually jog the head to the focal distance your lens manufacturer specifies (often ~50mm above material; for some lenses it's ~6-8mm).
- Zero the WCS at that position (`G10 L20 P1 Z0` or equivalent in your sender).
- Start the script with `--start-z 0`. Iterations test small offsets around that zero.

**Procedure:**

1. Sender + machine prep:
   - Power-on the machine.
   - Connect via your sender (UGS, Candle, etc.) once. Send `$X` if homing alarm is active.
   - Set the spindle/laser hardware switch to LASER. Confirm `$32=1`.
   - Jog to the focal height and zero Z. Jog to the lower-left of where you want the grid (default origin 10,10). Zero X and Y.
   - **Disconnect** from your sender (only one program can own the serial port).

2. Put on laser glasses appropriate for the wavelength (450nm for a blue diode). Turn on air assist if equipped. Have something to smother a fire within arm's reach.

3. Start the script:
   ```
   python lessons/integration/04_interactive_laser_cal/interactive_cal.py \
       --port COM3 --start-z 0
   ```
   (substitute your port; on Linux/macOS that's `/dev/ttyUSB0` or `/dev/tty.usbserial-*`)

   For Stage 1 (focus), the defaults are deliberately conservative — 50% power, 800 mm/min, 1 pass. Cuts may not go through; that's fine. You're measuring kerf width to find focus, not making a usable part.

4. The script checks: GRBL state is not ALARM, layout fits envelope, Z is within ±10mm. If any check fails it aborts before any motion. Read errors carefully.

5. The script issues `M5`, `$32=1`, `G21`, `G90` and then prompts you to confirm the initial params. Accept defaults for the first iteration.

6. At each iteration, **look at where the laser head will be** before pressing ENTER. The script prints `position: (x, y)` — verify it's over your material.

7. After the cut: rate the result. Outcomes the script understands:
   - `clean` — through cut, narrow kerf, minimal char
   - `incomplete` — didn't cut through; need more passes / more power / lower feed / better focus
   - `burnt` — through cut but charred edges; reduce power or increase feed
   - `kerf-wide` / `kerf-narrow` — fit feedback (don't worry about this in Z calibration; just track it)

8. Adjust **only Z** between iterations during Z calibration. Try a sequence like: 0, +0.5, +1.0, -0.5, -1.0, +0.25, -0.25. Find the Z that gives the narrowest, cleanest kerf. Stop when adjacent iterations look identical (you've found the focal range, ±0.25mm).

9. Type `done` at any prompt to stop. The manifest at `runs/cal_*.json` records what you tried.

**Hazards at this stage:**

- **Head crash from typo at Z prompt.** You enter `25` thinking 25%, but Z is mm — the script now rejects abs(Z)>10 by default. If you really need a larger move, override with `--max-z-offset 50` AND have a plan.
- **Focal collapse fire.** A perfectly-focused beam on dark material can ignite it in seconds. Don't walk away while the laser is firing.
- **Wrong Z reference.** If you zero Z at the *material surface* instead of at the *focal height*, Z=0 means head touching material. The "Ready?" prompt explicitly reminds you. Re-read it each session.

### Stage 2 — Power calibration

Once Z is dialed in, find the lowest power that reliably cuts through. Lower power = narrower kerf, less char, less laser wear.

**Procedure:**

1. Continue from the Z calibration (or restart with `--start-z <chosen>` and `--start-power 60 --start-passes 2`).
2. Start at 60% power. If first cut doesn't go through, raise to 80%. If it does, lower to 40%.
3. Bisect: 60 → (80 or 40) → halfway again. Goal is the lowest power where outcome is `clean` (not `incomplete`).
4. Keep `passes` at your starting value (typically 2 for 3mm plywood). Don't vary feed yet.

**Hazards:**

- **Flameout.** 100% power for many passes on plywood can ignite. Watch for sustained flame (>1 sec). M5 + close the port + extinguish if needed.
- **Forgetting to update `start-passes`.** With 4 passes at 100% power you'll burn through anything; that's not useful data.

### Stage 3 — Feed (speed) calibration

In M4 dynamic mode, *feed is the dominant heat control*. Slower = more heat per mm = wider kerf, more char. Faster = less heat = cleaner but might not cut through.

**Procedure:**

1. Hold power and Z constant at your Stage 1+2 winners.
2. Start at your `start-feed` (e.g., 400 mm/min). Try halving (200) and doubling (800).
3. Goal: the fastest feed that still gives a clean through-cut at your chosen power. Faster = less char and a more usable edge for puzzle pieces.

**Hazards:**

- Same as power — runaway combustion if you slow down too much.
- Don't reduce feed AND raise power simultaneously — vary one axis at a time so you can read which one moved the result.

### Stage 4 — Pass count

The variable you've held at 2 throughout. After Z + power + feed are dialed, see if you can drop to 1 pass.

**Procedure:**

1. Set `passes=1` and cut.
2. If `clean`, you're done.
3. If `incomplete`, you're at 2 passes. Don't go higher unless you've also tried lowering feed.

**Hazards:** Extra passes widen the kerf because the second pass burns slightly oversize. For tight-fit puzzles, fewer passes = better tolerance.

### Stage 5 — Write the numbers back

Take your winning combo (Z, S, F, P) from the manifest and update `profiles/laser_materials.yaml` for the material you tested. Future jobs cutting that same material will use these values.

The script does **not** auto-update the profile. The values are written by hand to preserve operator review.

---

## Grayscale rastering calibration

Run the script with `--mode engrave` instead of the default `--mode cut`. The iteration target becomes a raster-filled square patch (default 6×6 mm) at the iteration's params, scanned at 0.20 mm line spacing — same scan pattern phase7_raster uses for real engrave jobs, so cal mirrors production behavior.

Workflow:

```bash
python lessons/integration/04_interactive_laser_cal/interactive_cal.py \
    --telnet 192.168.4.116 --mode engrave \
    --start-power 10 --start-feed 3000 --start-passes 1
```

Then iterate the **power** field across the range you care about (typically 5-30% for wood). The script labels each iteration (1, 2, 3, ...) so you can match patch darkness to manifest entries afterward.

Typical starting parameters for diode + MDF:
- **Power**: 5-30% range. Below 5% you get nothing; above 30% you burn through.
- **Feed**: 3000-6000 mm/min (much faster than cutting).
- **Line spacing**: 0.20 mm default (matches phase7_raster's halftone / grayscale spacing).
- **Passes**: 1 (anything more just deepens the burn unnecessarily).

After the session, the manifest (`runs/cal_<timestamp>.json`) maps each iteration number to its params. Combined with photographs of the patches, you have the raw data to build a power→darkness LUT for phase7_raster's grayscale mode. **The LUT bake itself is still pending work** (see ROADMAP) — this script just produces the calibration patches.

---

## Safety summary — what the script does for you

The script now performs these checks before any motion:

1. **Envelope check** — reads `profiles/<machine>.yaml` and verifies the full iteration grid fits within the machine's X/Y envelope. Fails fast in `--dry-run` too, so you catch bad CLI args before plugging in.
2. **ALARM check** — queries GRBL status on startup. If it's in ALARM, refuses to send any motion until you resolve it via `$X` or `$H` in your sender.
3. **Setup-command response check** — `M5`, `$32=1`, `G21`, `G90` are sent with `_send_line_checked`. If GRBL rejects any of them (e.g., `error:9` for "command in locked state"), the script aborts before any cut.
4. **Z safety bound** — both `--start-z` and any value entered at the eval prompt are rejected if absolute value exceeds `--max-z-offset` (default 10mm). Bounds the blast radius of a typo.
5. **Per-iteration confirmation** — explicit "press ENTER to fire" before any laser firing, abortable with 'q' or Ctrl-C.
6. **Final M5 in `finally`** — laser is always turned off when the script exits, even on exception or Ctrl-C.

## Setup safety

This script **issues motion AND fires the laser**. Before each iteration there's an explicit "press ENTER to fire" prompt — you can abort with 'q' or Ctrl-C at any time. But the usual laser safety still applies:

- PPE on (laser glasses).
- Air assist running.
- Material clamped flat.
- Fire extinguisher reachable.
- `$32=1` (laser mode) is set automatically at startup.
- Spindle motor unpowered.
- Hardware switch on rear set to LASER.

The standard `LASER_PREFLIGHT_CHECKLIST` from `scripts/job_params.py` covers all of these — walk it once before starting.

## What it does NOT do

- Does not auto-evaluate cuts (no vision; you evaluate by eye).
- Does not optimize automatically across all axes (your judgment drives the adjustments).
- Does not modify the laser_materials.yaml — you write back the chosen values yourself.
- Does not yet support grayscale-raster calibration (see above section).

## Status

Implemented and tested. 24 unit tests cover the pure GCode emitters (label engraves, circle cuts, grid positioning, Z-move conditional logic, custom slot dimensions) plus the envelope and Z safety checks. Serial driver is manually-tested only.
