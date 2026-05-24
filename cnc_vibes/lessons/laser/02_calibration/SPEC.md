# Lesson 3b — Laser calibration pattern

> **Status: SPEC.** Captures the design intent before code lands.
>
> Inherits the toolchain pattern from [3a](../01_spacer/SPEC.md): pure Python → GRBL laser-mode GCode → existing `cnc.py validate` (laser rules) → existing `cnc.py preflight` (laser checklist). No new infrastructure.

## Goal

Find the right combination of **(power, passes, feed)** that produces a clean **cut-through** on a given material. The output, after burning, is a 2D matrix of small cut shapes labeled by their parameters. By inspecting which slugs fell cleanly out, which barely held on, and which didn't cut through, the user can read off the right settings and write them back into `profiles/laser_materials.yaml`.

The calibration is **cut-through-oriented**, not engrave-oriented. Each "sample" in the matrix is a small closed shape that the laser tries to cut all the way through. The labels around the grid tell you which power, pass count, and feed produced which sample.

## What you'll learn

- How to lay out a multi-dimensional parameter sweep as a GCode artifact you can read with your eyes.
- The role of cut-through testing: published numbers are starting points; only your machine + your lens condition + your material batch tells you the real answer.
- How to render block-font numerals without depending on a font library (7-segment digits drawn as line segments — about 40 lines of Python).
- Why **M4 dynamic power** can mask cell-to-cell power differences (it scales with momentary feed). The mitigation: cells are large enough that acceleration ramps are a small fraction of stroke time.

## Prerequisites

- [Lesson 3a](../01_spacer/) understood (you've ideally cut at least one spacer).
- Laser preflight comfort.
- Material to sacrifice for the calibration burn — typically a full sheet of the material you want to calibrate.

## Usage

```
python lessons/laser/02_calibration/calibration.py \
    --material plywood_baltic_birch_3mm \
    --max-passes 5 \
    --powers 100,75,50,25 \
    --speeds 200,400,600 \
    --out lessons/laser/02_calibration/build/cal_plywood_3mm.gcode
```

All arguments optional; defaults produce a sensible first calibration. **All requested speeds are packed into a single file** as vertically-stacked panels.

| Flag | Default | Meaning |
|---|---|---|
| `--material ID` | `plywood_baltic_birch_3mm` | Material id from `profiles/laser_materials.yaml`. Default `feed_mm_per_min` from the profile is used if `--speeds` is empty. |
| `--max-passes N` | 5 | X axis of each panel: columns from 1 pass through N passes. |
| `--powers LIST` | `100,75,50,25` | Y axis of each panel: rows at each power percentage, top-down. |
| `--speeds LIST` | (empty — use material default) | Z axis: one panel per speed, stacked vertically in the same file. |
| `--cell-pitch FLOAT` | 18.0 | Cell-to-cell pitch, mm. Cut shape is ~8mm centered in each cell. |
| `--label-digit-height FLOAT` | 5.0 | Height of the block-font digits used for labels, mm. |
| `--out PATH` | derived | Output `.gcode` path. |

## What the output looks like

Per panel (one panel per speed):

```
        F<feed>          <- panel header: the speed for this panel
      1   2   3   4   5  <- column header: pass count
  100 □   □   □   □   □
   75 □   □   □   □   □
   50 □   □   □   □   □
   25 □   □   □   □   □
   ^             ^
   |             |
   row labels    each □ is a small square cut N times at power P and feed F
   (power %)
```

Multiple panels stack vertically with a gap and a new feed label between them. Total Y extent is checked against the machine envelope; the script warns (and the validator catches it) if you ask for more panels than fit.

After burning, inspect:
- **Best cell per row**: the smallest pass count where the slug fell out cleanly at that power.
- **Best cell per column**: the lowest power that cut through at that pass count.
- **Best panel**: the fastest feed where you still got clean cuts at acceptable power.

Write the chosen `(power_percent, feed_mm_per_min, passes)` back into the material's entry in `profiles/laser_materials.yaml`.

## Labeling strategy

Labels are **per-axis**, not per-cell, to keep the burn time and GCode size manageable.

- **Row labels** (left of each row): power percentage. E.g. "100", "75", "50", "25".
- **Column labels** (above each column): pass count. E.g. "1", "2", "3", "4", "5".
- **Panel labels** (top of each panel): feed rate. E.g. "200", "400", "600".

All labels are engraved (single-pass, low power) in a single dedicated header pass before the cuts begin. The labels are not affected by the cut burns because they live outside the grid cells.

Glyphs supported: digits 0–9. No letters — the user knows what each axis is from this README.

## Toolchain

Same shape as 3a — pure Python emits GRBL laser-mode GCode, validator and preflight already handle laser jobs:

```
calibration.py  →  .gcode  →  cnc.py validate  →  cnc.py preflight  →  sender
```

No FreeCAD. No CAM.

## How the digits are rendered

7-segment block font. Each digit drawn as straight line segments in a `W × H` box where `W = H / 2`. The renderer takes a string, a starting point, and emits a list of `(x1, y1, x2, y2)` segments that the GCode generator converts to G0/G1 + M4/M5 sequences.

Why not a real font library: brings in `freetype-py` or similar as a dependency for ~10 glyphs. The 7-segment approach is ~40 lines and stays in stdlib.

Why not OpenSCAD `text()` then projection: OpenSCAD's text produces filled glyph outlines, which would render as outlines around each glyph rather than single-stroke characters. Single-stroke is what we want (one visible line per stroke per pass).

## M4 vs M3 caveat

For real cuts we always use **M4 dynamic power**. For calibration, M4 is slightly dishonest: the actual delivered power scales with momentary feed rate. The shorter the segment, the more time the laser spends accelerating from rest, lowering the average effective power.

**Mitigation:** cell pitch defaults to 18mm and cut shapes are 8mm squares — each square edge is 8mm of constant-feed travel. With M4 at 400 mm/min, acceleration takes a tiny fraction of that. Cell-to-cell *relative* comparison is honest; absolute power should be taken as upper bounds.

## Files this lesson creates

```
lessons/laser/02_calibration/
  SPEC.md                       ← this file (design history)
  README.md                     ← user-facing lesson
  calibration.py                ← argparse CLI, generator
  font_7seg.py                  ← block-font digit rendering (importable, testable)
  tests/
    test_font_7seg.py           ← every supported glyph has correct segments
    test_calibration.py         ← GCode structure: cell count, M4, S in range, no Z, labels present
```

## Validation that the lesson works end-to-end

1. `python lessons/laser/02_calibration/calibration.py --material plywood_baltic_birch_3mm` produces a `.gcode` in `build/`.
2. `python cnc.py validate <gcode>` passes with laser-aware rules.
3. `python cnc.py preflight <gcode>` walks the laser checklist.
4. Run on the machine. The result is a labeled matrix of cut squares plus engraved labels around the edges.
5. Inspect which cells cut through cleanly. Pick `(power, feed, passes)`. Write back into `profiles/laser_materials.yaml`.

## My critique of this design

- **Engraved labels assume the label power doesn't burn through.** If you're calibrating thin material (cardstock at 30% power = full cut), the labels might cut through and fall out, taking the readability with them. v1 uses a fixed label power (30%) and feed (1500mm/min); thin-material calibration may need lower label power.
- **No focus axis.** Real diode lasers are sensitive to Z-focus distance. Focus calibration is a separate pattern; not in v1.
- **No air-assist axis.** Air assist is on/off; a calibration comparing with-air vs no-air would help but requires the operator to flip the air switch mid-burn.
- **No kerf measurement.** If you want kerf comp later, you'd measure cut widths from this calibration. Not in v1 (no kerf comp in laser lessons period).
- **Bed-size limit.** A 5-pass × 4-power × 4-speed calibration takes ~5×18 × 4×18 × (4 panels × 80mm + gaps) = 90 × 72 × ~360mm which exceeds the 300mm Y envelope. Script warns; user reduces axes or splits across multiple invocations.

## Settled design decisions

### Group A — Defaults *(settled 2026-05-24)*

1. `--max-passes` = **5**.
2. `--powers` = **`100,75,50,25`**.
3. `--label-digit-height` = **5.0 mm** (small, readable, doesn't dominate cell footprint).
4. `--cell-pitch` = **18.0 mm**, cut shape = **8 mm square** (fixed in script).

### Group B — Scope of v1 *(settled 2026-05-24)*

5. **Cut-through is the primary goal**, not engrave. Each cell cuts a small closed shape (8mm square). The original "engraved Nx text per cell" design was wrong — the user clarified the calibration is for finding cut settings, with labels around the grid identifying parameters.
6. **All speeds in one .gcode file** (stacked panels). User explicitly requested this over per-file output.

### Group C — Font shipping *(settled 2026-05-24)*

7. `font_7seg.py` lives **inside the lesson directory** for v1. Promote to `scripts/` if lesson 3c (jigsaw) or others need text rendering.

