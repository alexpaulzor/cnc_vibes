# Lesson 4a — Parametric router-cut spacer

> **Status: SPEC.** Captures my interpretation of the design intent. Open question at the bottom about the geometry; correct me before code lands.

## Goal

Generate a router-cut spacer with potentially varying inner and outer diameters between the top and bottom faces. **Hybrid toolchain**: when the geometry is a plain cylindrical washer, emit GCode directly (fully automated). When it's a real 3D frustum, generate the CSG and hand off to FreeCAD CAM (semi-automated, GUI for setup).

This is the first **spindle**-side lesson and the first that pivots between the two execution paths in one script.

## What you'll learn

- The **hybrid toolchain pattern**: a single script auto-detects whether geometry is simple enough for pure-Python GCode generation, or whether it requires FreeCAD CAM. Saves manual CAM setup for the simple cases.
- **Helical bore** vs **drill / peck drill** vs **pocket** as strategies for making a hole bigger than the tool. The script picks based on the ratio (hole_dia / tool_dia).
- **Tabs and through-cuts** for profile cutting — same idea as the §6 hole_in_sheet example, now baked into the script.
- The role of **stock-to-leave** when you'll come back with a finishing pass on a 3D surface.

## Geometry *(settled 2026-05-24)*

```scad
difference() {
    cylinder(r1=bottom_od/2, r2=top_od/2, h=height);
    translate([0, 0, -0.1])
        cylinder(r1=bottom_id/2, r2=top_id/2, h=height + 0.2);
}
```

Four independent diameters plus a height. When all four diameters are equal, the part is a plain cylindrical washer; otherwise the outer wall and/or hole is a frustum.

The geometric difference between "cylindrical" and "frustum" is **immaterial from the design side** — the SCAD is the same. The distinction only matters on the CAM side, where pure-Python GCode generation works for the degenerate cylindrical case and FreeCAD CAM (with potential ball-end finishing) is needed for sloped walls. The script auto-detects and picks the faster path silently.

## CLI (assuming the 4-diameter interpretation)

```
python lessons/mill/01_spacer/mill_spacer.py \
    --height 6.0 \
    --od 8.0 \
    --id 3.2 \
    --material plywood_baltic_birch_6mm \
    --tool flat_3.175mm_2flute \
    --spindle-rpm 18000
```

| Flag | Default | Meaning |
|---|---|---|
| `--height FLOAT` | 6.0 | Z dimension of the part, mm. Must match the material's thickness. |
| `--od FLOAT` | 8.0 | Outer diameter. Sets both `top_od` and `bottom_od` unless one is overridden. |
| `--id FLOAT` | 3.2 | Inner diameter (hole). Sets both `top_id` and `bottom_id` unless one is overridden. |
| `--top-od FLOAT` | (= `--od`) | Override the top outer diameter. If differs from `--bottom-od`, switches to 3D mode. |
| `--bottom-od FLOAT` | (= `--od`) | Override the bottom outer diameter. |
| `--top-id FLOAT` | (= `--id`) | Override the top inner diameter. |
| `--bottom-id FLOAT` | (= `--id`) | Override the bottom inner diameter. |
| `--material ID` | `plywood_baltic_birch_6mm` | From `profiles/materials.yaml`. Used for chipload, doc_fraction, and to confirm `--height` matches `thickness_mm`. |
| `--tool ID` | `flat_3.175mm_2flute` | From `profiles/tools.yaml`. Used for diameter, max_rpm, max_plunge. |
| `--spindle-rpm INT` | 18000 | Used for feed derivation (`chipload × flutes × rpm`). |
| `--out PATH` | derived | Output path. Simple case: `.gcode`. 3D case: `.scad` (and `.csg` after running openscad). |

## Hybrid toolchain decision tree

```
                ┌───────────────────────────────────┐
                │  Is geometry "simple" (cylindrical)?  │
                │  top_od == bottom_od AND              │
                │  top_id == bottom_id                  │
                └───────┬───────────────────┬───────────┘
                        yes                 no
                        │                   │
                        ▼                   ▼
   ┌────────────────────────────┐   ┌──────────────────────────────────┐
   │ Simple case — pure Python   │   │ 3D case — hand off to FreeCAD    │
   │ • Helical bore for hole     │   │ • Generate .scad with cylinder() │
   │ • Profile cut + tabs        │   │   (h, r1=bot_od/2, r2=top_od/2)  │
   │ • Write .gcode directly     │   │ • openscad → .csg                │
   │ • cnc.py validate           │   │ • Print next-steps:              │
   │ • cnc.py preflight          │   │   "open in FreeCAD,              │
   │ • Run                        │   │    CAM workbench, set up Job,    │
   │                              │   │    use a ball-end mill for       │
   │                              │   │    finishing the sloped walls"   │
   └────────────────────────────┘   └──────────────────────────────────┘
```

The script always generates the `.scad` regardless (so you can visualize either case in OpenSCAD). For the simple case it *additionally* writes the `.gcode`. For the 3D case it stops at the `.csg` and tells you to take it into FreeCAD.

## What the simple-case GCode does

Three operations in this order:

1. **Helical bore for the hole.** Spiral path that descends from Z=0 to Z=-(height + 0.2 mm overcut). Pitch chosen so each revolution removes a depth equal to `doc_fraction × diameter / 2`. Only used when `id > 2.5 × tool_diameter` (otherwise the spiral is impractically tight; use drill instead).
   - For id ≤ 2.5 × tool_diameter: **drill / peck-drill** instead — plunge, retract, plunge deeper, etc.
   - For id < tool_diameter: can't cut a hole smaller than the tool. Script errors out and suggests a smaller tool.
2. **Profile cut for the outer perimeter.** Stepped Z passes (DOC = `doc_fraction × tool.diameter_mm`). Climb-mill direction. **Tabs** added (4 evenly distributed, 5mm wide × 1.5mm tall by default) so the part doesn't release and get hit by the spinning tool.
3. **Park.** Retract to safe Z, move to (0, 0), spindle off (M5).

Header includes `;TOOL: <tool_id>` so the validator's max_plunge rule fires. Header includes `$32=0` to ensure laser mode is off (idempotent; safe to emit on every spindle job).

## What the 3D case produces

A `.scad` file with the canonical shape:

```scad
$fn = 128;
difference() {
    cylinder(h = HEIGHT, r1 = BOT_OD/2, r2 = TOP_OD/2);
    translate([0, 0, -0.1])
        cylinder(h = HEIGHT + 0.2, r1 = BOT_ID/2, r2 = TOP_ID/2);
}
```

Then `cnc.py build mill_spacer` (extended to handle lessons, or we just call openscad directly) produces the `.csg`. The user opens `.csg` in FreeCAD via OpenSCAD workbench, sets up a Job in CAM workbench, picks a ball-end mill for finishing, configures roughing+finishing passes.

The script prints those next steps to stdout when it writes the `.scad`, so the user doesn't have to remember.

## Files this lesson creates

```
lessons/mill/01_spacer/
  SPEC.md                       ← this file
  README.md                     ← user-facing once implemented
  mill_spacer.py                ← argparse CLI, dispatches simple vs 3D
  tests/
    test_mill_spacer.py         ← tests on both code paths and the dispatch decision
```

## Toolchain reuse

This lesson **adds nothing new** to the supporting infrastructure — it composes existing tools:

- `profiles/materials.yaml` (spindle materials, chipload tables): used as-is.
- `profiles/tools.yaml`: used as-is.
- `profiles/default.yaml`: used as-is.
- `scripts/job_params.py`: `compute_derived()` already does feed = chipload × flutes × rpm. Reused.
- `scripts/gcode_validate.py`: spindle rules already cover everything. No changes.
- `scripts/job_params.py` PREFLIGHT_CHECKLIST: covers spindle prep. No changes.
- `cnc.py preflight`: works as-is.

The hybrid-toolchain pattern is the only new concept; the implementing code is small.

## My critique of this design

- **Helical bore feed/pitch math is per-tool.** The script computes pitch from doc_fraction but doesn't measure whether the resulting helical-engagement angle is safe for the tool. For a 6mm tool boring a 10mm hole, the engagement is ~80% diameter — that's fine for plywood, brutal for aluminum. For 4d (aluminum) the helical bore would need trochoidal-style logic with reduced engagement.
- **No through-tabs strategy choice.** Tabs are placed at the same Z (0.5mm above stock bottom) regardless of part thickness. For thick stock with tall parts you'd want tabs midway. Extension.
- **No support for non-axisymmetric geometry.** A spacer with a flat on one side, or an oval hole, or any non-rotational feature falls outside this script entirely. Different lesson.
- **Tool change not handled.** If the simple-case helical bore needs a smaller tool than the perimeter profile, the user has to break the job into two GCode files manually. Could be auto-split (with an M0 pause between), but adds complexity.
- **Material thickness mismatch isn't enforced.** If `--height 6.0` but `--material plywood_baltic_birch_3mm`, the script just emits the cut depths as if the material were 6mm. Could warn or error; deferred.

## Validation that the lesson works end-to-end

**Simple case:**

```
python lessons/mill/01_spacer/mill_spacer.py --height 6 --od 8 --id 3.2
python cnc.py validate lessons/mill/01_spacer/build/spacer_simple.gcode
python cnc.py preflight lessons/mill/01_spacer/build/spacer_simple.gcode
# load in gSender, cut
```

**3D case:**

```
python lessons/mill/01_spacer/mill_spacer.py --height 12 \
    --top-od 10 --bottom-od 14 \
    --top-id 3.2 --bottom-id 3.2
# -> writes spacer_3d.scad
openscad -o lessons/mill/01_spacer/build/spacer_3d.csg lessons/mill/01_spacer/build/spacer_3d.scad
# Open spacer_3d.csg in FreeCAD, set up Job in CAM workbench, post -> .gcode
python cnc.py validate <that.gcode>
python cnc.py preflight <that.gcode>
# load in gSender, cut
```

## Settled design decisions

### Group B — Simple-case hole strategy *(settled 2026-05-24)*

1. **Auto-pick**: helical bore when `id > 2.5 × tool_diameter`, peck drill otherwise.
2. **Error out** if `id < tool_diameter`. Suggest a smaller tool in the error message.

### Group C — Visualization *(settled 2026-05-24)*

3. **Always emit the `.scad`** alongside any GCode output. Lets the user visualize what they're about to cut in OpenSCAD regardless of which CAM path was taken.
