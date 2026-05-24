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
# Real run (requires --port or CNC_PORT env)
python lessons/integration/04_interactive_laser_cal/interactive_cal.py \
    --port /dev/ttyUSB0 \
    --origin-x 10 --origin-y 10 \
    --start-z 0 --start-power 100 --start-feed 400 --start-passes 2

# Dry run (no serial; prints all GCode and uses defaults for all iterations)
python lessons/integration/04_interactive_laser_cal/interactive_cal.py \
    --dry-run --max-iterations 4
```

| Flag | Default | Meaning |
|---|---|---|
| `--port` | `$CNC_PORT` | Serial port (required unless --dry-run). |
| `--baud` | 115200 | GRBL standard. |
| `--origin-x` / `--origin-y` | 10, 10 mm | Lower-left of first iteration slot. |
| `--slot-w` / `--slot-h` | 30, 30 mm | Slot size per iteration. |
| `--slots-per-row` | 6 | Wraps to next row after this many. |
| `--circle-dia` | 8 mm | Test cut diameter. |
| `--engrave-height` | 4 mm | Iteration-number digit height. |
| `--engrave-power-percent` | 25 | Low power so the label doesn't cut through. |
| `--start-z` / `--start-power` / `--start-feed` / `--start-passes` | 0, 100, 400, 2 | Initial params. |
| `--max-iterations` | 24 | Hard safety stop. |
| `--dry-run` | off | Print GCode without opening port. |

## Interactive flow per iteration

```
=== Iteration N ===
  params: Z=2.0  S=100%  F=400  P=2
  position: (25.0, 21.0) mm
  Press ENTER to fire (or 'q' to quit):

[machine engraves the label, cuts the circle, returns to safe Z, M5]

--- Evaluate the cut ---
  Outcome [clean / incomplete / burnt / kerf-wide / kerf-narrow / abort / done]:
  Notes (free-form, optional):

--- Adjust params for next iteration ---
  Press ENTER to keep current value. Type 'done' on any line to finish.
  Z (mm) (current 2.0):
  Power % (current 100):
  Feed mm/min (current 400):
  Passes (current 2):
```

## Manifest

Each run writes `runs/cal_<timestamp>.json` with every iteration's params, position, outcome, and notes. Use it to look up "what gave the cleanest result?" after the session.

The `runs/` directory is gitignored — per-session, not source.

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

## Status

Implemented and tested. 13 unit tests cover the pure GCode emitters (label engraves, circle cuts, grid positioning, Z-move conditional logic). Serial driver is manually-tested only.
