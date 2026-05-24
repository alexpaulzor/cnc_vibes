# Lesson 4c — Steel center-punch divets

> Fully automated. Pure Python → GCode. Given a list of (x, y) points (CLI, file, or grid), generates GCode that plunges the spindle to a small depth at each location, making a divet to register a follow-up drill bit.

## Goal

Use the existing engraver / V-bit to make precisely-located center-punch marks in mild steel (or any softer metal) for follow-up drilling. The 500W spindle on this class of router **cannot cut steel** — but it can deform the surface superficially with a sharp V-bit at low feed, which is all you need to mark hole locations accurately.

## What you'll learn

- The pattern for "spindle does a parametric thing" without any real machining: header + safe-Z retract + spindle on + per-point (G0 to XY → G0 to approach Z → G1 plunge to depth → dwell → retract).
- How to pass point lists three different ways (inline CSV, YAML file, generated grid) without making the script ugly.
- Validation gates for tool-specific limits: spindle RPM, plunge feed.

## Prerequisites

- Mill setup with an engraver V-bit installed (default: `vbit_60deg_6mm` from `profiles/tools.yaml`).
- Mild steel or aluminum sheet, ~1/8" or 3 mm thick.
- The steel is **clamped securely** — center-punching applies sideways force on the bit; loose stock spins.
- Standard preflight: probe Z carefully, set WCS, dust collection ON (sparks are minimal but possible).

## Usage

```
# Inline points (a few divets)
python lessons/mill/02_steel_center_punch/center_punch.py \
    --points "10,10,30,10,50,10,10,30,30,30,50,30" \
    --depth 0.4

# YAML file of points
python lessons/mill/02_steel_center_punch/center_punch.py \
    --points-file my_holes.yaml \
    --depth 0.5 --tool vbit_60deg_6mm

# Generated grid (e.g. for a perforated panel layout)
python lessons/mill/02_steel_center_punch/center_punch.py \
    --grid 5x4 --pitch 12 --origin 10,10 \
    --depth 0.3
```

| Flag | Default | Meaning |
|---|---|---|
| `--points "x,y,x,y,..."` | — | inline CSV |
| `--points-file PATH` | — | YAML list of `[x, y]` pairs |
| `--grid AxB --pitch P --origin X,Y` | — | parametric grid |
| `--depth FLOAT` | 0.4 | divet depth, mm. Capped at 2.0 mm (refuses anything bigger). |
| `--plunge-feed INT` | 80 | mm/min. Slow for accuracy on hard material. |
| `--tool ID` | `vbit_60deg_6mm` | from `profiles/tools.yaml` |
| `--spindle-rpm INT` | 12000 | low end of the tool's RPM range. |
| `--out PATH` | derived | output gcode path |

The three point-source flags are mutually exclusive; pass exactly one.

## End-to-end run

```
# 1. Generate
python lessons/mill/02_steel_center_punch/center_punch.py --grid 5x4 --pitch 12 --origin 10,10

# 2. Validate
python cnc.py validate lessons/mill/02_steel_center_punch/build/center_punch_n20.gcode

# 3. Preflight (spindle checklist)
python cnc.py preflight lessons/mill/02_steel_center_punch/build/center_punch_n20.gcode

# 4. Optional but recommended: inspect machine state first
python lessons/integration/01_inspect/grbl_inspect.py --expect-head spindle

# 5. Load in gSender, install V-bit, probe Z carefully (V-bit point is fragile), set WCS, run.
# Then take it to a drill press with the divets registering your bit.
```

## Safety notes

- **Steel produces fragments, not chips.** Wear glasses. Plunge depth above 0.5 mm risks bit breakage on cold-rolled steel.
- **Don't try to "cut" steel** with this script. If you want a through-hole, mark with this and drill with a drill press.
- **V-bit tips are fragile.** A 0.4 mm divet is conservative for a fresh tip; a dull one needs more depth. Inspect your bit before AND after.
- **The script's depth cap (2 mm)** is a guard against typos. If you genuinely want deeper, edit the source.

## Extensions

- **Read points from a KiCAD drill file (`.drl`)** so a PCB layout's hole positions can mark a metal plate that will hold the PCB.
- **Add a peck cycle** option: plunge-retract-plunge for harder material that needs incremental depth.
- **Auto-probe before each row** to compensate for stock-thickness variation across the sheet. Would integrate with the `probe_corner` machinery.
- **Skip points already marked** by comparing with a previous run's manifest — useful when you're adding holes to an existing part.

## Status

Implemented and tested. 22 tests cover point parsing, grid generation, GCode structure, and error paths.
