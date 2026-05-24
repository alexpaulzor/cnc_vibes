# Lesson 3b — Laser calibration pattern

> Pure-Python calibration generator. Produces a labeled matrix of small cut squares at varying (power, passes, feed) combinations so you can find the right cut-through settings for a material empirically.
>
> Companion design doc: [SPEC.md](SPEC.md).

## Goal

Find the right combination of **(power, passes, feed)** that produces a clean **cut-through** on a given material. The output is a 2D matrix of cut squares with engraved labels around the edges identifying the settings used for each cell.

After burning, you inspect which slugs fell out cleanly, which barely held on, and which didn't cut through. Pick your cell, write the numbers back into `profiles/laser_materials.yaml`, and 3a's spacer cuts get more reliable.

## What you'll learn

- How to lay out a multi-dimensional parameter sweep as a GCode artifact you can read with your eyes.
- The role of cut-through testing — published numbers are starting points; only your machine + your lens + your material batch tells you the real answer.
- Block-font numeral rendering without a font library (7-segment digits as line segments — see [`font_7seg.py`](font_7seg.py)).
- Why M4 dynamic power slightly skews calibration (it scales with momentary feed) and the mitigation (cells big enough that ramp-up is a small fraction of the burn).

## Prerequisites

- [Lesson 3a](../01_spacer/) — establishes the laser GCode pattern this lesson reuses.
- Laser preflight comfort.
- A sacrificial piece of the material you want to calibrate. The default pattern (5 passes × 4 powers × 1 speed) is ~106 × 100 mm. Multi-speed patterns scale taller — see "Bed-size limits" below.

## Usage

```
python lessons/laser/02_calibration/calibration.py \
    --material plywood_baltic_birch_3mm \
    --max-passes 5 \
    --powers 100,75,50,25 \
    --speeds 200,400,600
```

All arguments optional; defaults produce a single-panel calibration at the material's default feed rate.

| Flag | Default | Meaning |
|---|---|---|
| `--material ID` | `plywood_baltic_birch_3mm` | Material id from `profiles/laser_materials.yaml`. |
| `--max-passes N` | 5 | X axis: 1 through N pass count. |
| `--powers LIST` | `100,75,50,25` | Y axis: comma-separated power percentages, top row first. |
| `--speeds LIST` | (empty — use material default) | Z axis: comma-separated feed rates in mm/min. Each speed becomes a vertically-stacked panel in the same file. |
| `--cell-pitch FLOAT` | 18.0 | Cell-to-cell pitch in mm; cut square is 8mm centered in each cell. |
| `--label-digit-height FLOAT` | 5.0 | Block-font label height in mm. |
| `--out PATH` | derived | Output `.gcode` path. |

## What the output looks like

Per panel (one per requested speed):

```
        <feed-rate>          <- panel header
      1   2   3   4   5      <- column header: pass count
  100 □   □   □   □   □
   75 □   □   □   □   □      <- row labels: power %
   50 □   □   □   □   □
   25 □   □   □   □   □
```

Each `□` is an 8mm square cut N times at power P% and feed F (where N, P, F come from the row/column/panel position).

Multiple panels stack vertically. The first panel sits at the WCS origin (with a 3 mm margin); subsequent panels stack +Y with an 8 mm gap and a fresh feed-rate label.

## End-to-end run

```
# 1. Generate (single speed, defaults)
python lessons/laser/02_calibration/calibration.py

# 2. Validate (auto-detects head=laser)
python cnc.py validate lessons/laser/02_calibration/build/cal_plywood_baltic_birch_3mm_F400.gcode

# 3. Preflight (auto-picks laser checklist)
python cnc.py preflight lessons/laser/02_calibration/build/cal_plywood_baltic_birch_3mm_F400.gcode

# 4. Load in gSender, run, inspect the result, update profiles/laser_materials.yaml.
```

## Reading the result

After the burn:

- **Walk each row** (constant power): find the smallest pass count where the slug fell out cleanly. That's your "minimum passes at this power."
- **Walk each column** (constant passes): find the lowest power that still cut through. That's your "minimum power at this pass count."
- **Walk each panel** (constant feed): the fastest feed where any reasonable (passes, power) combo still cuts through. Faster = quicker jobs.

Write the chosen `(power_percent, feed_mm_per_min, passes)` back into the material's `laser:` block in `profiles/laser_materials.yaml`. 3a's spacer cuts will use the calibrated values automatically.

## Bed-size limits

Default config (5 × 4 grid, 18 mm cell pitch) is ~106 × 100 mm. Stacking N panels adds ~80 mm per panel + 8 mm gap. The 4030 has a 300 mm Y envelope. Practical limits:

- 1 speed: well within bed.
- 2 speeds: ~200 mm Y.
- 3 speeds: ~280 mm Y (tight but fits).
- 4+ speeds: exceeds 300 mm — validator will catch this via the bounds rule. Split across multiple invocations or shrink the grid (`--max-passes 3 --powers 100,75,50`).

## Extensions to explore

- **Cut-shape variants.** Replace the fixed 8mm square with a `--cell-shape` flag for circles, ellipses, or specific test profiles.
- **Static-power calibration mode.** Add `--mode static` that emits M3 (constant power) instead of M4 (dynamic). Would need to teach the validator a `laser_calibration_mode` exemption from the `laser_m4_required` rule. Gives more honest absolute power numbers at the cost of corner over-burn.
- **Auto-tile when overflowing.** If the requested speeds × grid exceeds the bed, automatically split across columns or files instead of just failing validation.
- **Result entry script.** Write a small `calibrate.py update --material ... --power ... --feed ... --passes ...` that updates `profiles/laser_materials.yaml` in place after you've picked your cell.

## Status

Implemented and tested. `python cnc.py test` runs the font + calibration test suites along with the rest.
