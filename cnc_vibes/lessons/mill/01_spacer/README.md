# Lesson 4a — Parametric router-cut spacer

> Hybrid toolchain. Cylindrical case is fully automated (pure Python → GCode). Frustum case generates the SCAD/CSG and hands off to FreeCAD CAM for the sloped-wall finishing.
>
> Companion design doc: [SPEC.md](SPEC.md).

## Goal

Generate a router-cut spacer with potentially varying inner and outer diameters between top and bottom faces. When the geometry collapses to a plain cylindrical washer, skip the GUI entirely and emit GCode directly. When it's a real 3D part (sloped walls), generate the geometry and walk you through the FreeCAD CAM setup.

This is the first **spindle-side** lesson — the first that actually drives the router instead of the laser.

## What you'll learn

- The **hybrid-toolchain pattern**: one script, two execution paths, chosen automatically based on geometry.
- **Helical bore** vs **peck drill** as strategies for making a hole bigger than the tool. The script picks based on the `id / tool_diameter` ratio.
- How to compose the existing infrastructure (`compute_derived` for feeds, the spindle validator rules, the spindle preflight) without writing anything new in `scripts/` or `profiles/`.

## Prerequisites

- Read main guide [§4 (machine-as-profile)](../../../cnc_for_the_scad.md) and [§5 (FreeCAD CAM workbench)](../../../cnc_for_the_scad.md).
- Have a working spindle setup: bit installed, touch plate working, dust collection.
- For the frustum path: FreeCAD installed and `cnc.py doctor` shows `FreeCADCmd` resolved.

## Usage

```
# Cylindrical (the easy case, fully automated)
python lessons/mill/01_spacer/mill_spacer.py \
    --height 6 --od 8 --id 3.2

# Frustum (sloped outer wall)
python lessons/mill/01_spacer/mill_spacer.py \
    --height 12 \
    --top-od 10 --bottom-od 14 \
    --top-id 3.2 --bottom-id 3.2
```

All arguments optional; defaults produce a 6 mm tall, 8 mm OD, 3.2 mm ID (M3 clearance) cylindrical spacer in 6 mm plywood.

| Flag | Default | Meaning |
|---|---|---|
| `--height FLOAT` | 6.0 | Z dimension of the part. Should match material thickness. |
| `--od FLOAT` | 8.0 | Outer diameter. Sets both `top_od` and `bottom_od`. |
| `--id FLOAT` | 3.2 | Inner diameter (hole). Sets both `top_id` and `bottom_id`. M3 clearance. |
| `--top-od FLOAT` | (= `--od`) | Override top outer diameter — triggers 3D path if differs from bottom. |
| `--bottom-od FLOAT` | (= `--od`) | Override bottom outer. |
| `--top-id FLOAT` | (= `--id`) | Override top inner. |
| `--bottom-id FLOAT` | (= `--id`) | Override bottom inner. |
| `--material ID` | `plywood_baltic_birch_6mm` | From `profiles/materials.yaml`. |
| `--tool ID` | `flat_3.175mm_2flute` | From `profiles/tools.yaml`. |
| `--spindle-rpm INT` | 18000 | Used to derive feed rate via `chipload × flutes × rpm`. |
| `--out PATH` | derived | Output `.gcode` path (cylindrical case only). |

## What the script does

```
            ┌──────────────────────────────────────┐
            │ Is geometry plain cylindrical?        │
            │ (top_od == bottom_od &&               │
            │  top_id == bottom_id)                 │
            └────────┬──────────────────────┬───────┘
                     yes                    no
                     │                      │
                     ▼                      ▼
       ┌─────────────────────────┐  ┌─────────────────────────────┐
       │ Pure-Python path        │  │ FreeCAD-handoff path         │
       │   .scad   (always)      │  │   .scad   (always)           │
       │ + .gcode (auto)         │  │ + .csg    (if openscad on    │
       │                          │  │            PATH)             │
       │ Hole strategy:          │  │                              │
       │  helical bore if         │  │ Prints next-steps for       │
       │   id > 2.5 × tool_dia    │  │  FreeCAD CAM (Job setup,    │
       │  peck drill otherwise   │  │  ball-end finishing pass).  │
       │ + profile cut perimeter │  │                              │
       └─────────────────────────┘  └─────────────────────────────┘
```

The `.scad` is always written so you can visualize the part in OpenSCAD before cutting, regardless of which path was taken.

## End-to-end run (cylindrical case)

```
# 1. Generate
python lessons/mill/01_spacer/mill_spacer.py --height 6 --od 8 --id 3.2

# 2. Validate (auto-detects spindle, applies all spindle rules)
python cnc.py validate lessons/mill/01_spacer/build/spacer_h6.0_to8.0_bo8.0_ti3.2_bi3.2.gcode

# 3. Preflight (spindle checklist)
python cnc.py preflight lessons/mill/01_spacer/build/spacer_h6.0_to8.0_bo8.0_ti3.2_bi3.2.gcode

# 4. Load in gSender, install the bit, probe Z, run.
```

## End-to-end run (frustum case)

```
# 1. Generate
python lessons/mill/01_spacer/mill_spacer.py \
    --height 12 --top-od 10 --bottom-od 14 --top-id 3.2 --bottom-id 3.2

# -> writes .scad and .csg; prints FreeCAD next-steps to stdout.

# 2. Open the .csg in FreeCAD (OpenSCAD workbench -> import)
# 3. Switch to CAM workbench, create Job with the imported solid as Base
# 4. Set up roughing (flat endmill) and finishing (ball-end) operations
# 5. Post to GCode
# 6. cnc.py validate <gcode>; cnc.py preflight <gcode>; run.
```

The §6 click-through in the main guide covers the FreeCAD setup steps in detail.

## Known limitations (called out in the GCode)

- **Tabs are not yet implemented.** The generated perimeter cut goes through to spoilboard depth on the final pass, which means the part will release before the cut completes. The GCode contains a `TODO` comment about this. Workarounds: clamp the part from below (sacrificial fixture), or hand-edit the GCode to add an `M0` pause before the last pass so you can re-clamp.
- **Tool change is not handled.** If your helical bore needs a different tool than your perimeter profile, run the script twice with different `--tool` values and combine the GCode files manually (with an `M0` pause + tool-change instructions between).
- **Material thickness vs `--height` is not enforced.** If you pass `--material plywood_baltic_birch_3mm` and `--height 6`, the script happily cuts to Z=-6.2 even though the material is 3mm thick. Sanity-check yourself.

These are in the SPEC's extensions list and are reasonable next iterations.

## Extensions

- **Tabs**: split the perimeter into arcs that step up over tab locations.
- **Multi-tool sequencing**: generate two GCode files (hole + perimeter) when the optimal tools differ, plus a "swap-tool-and-probe" macro between.
- **Aluminum support**: the chipload tables already cover `aluminum_6061_3mm`. Run `--material aluminum_6061_3mm` and the feeds derive correctly — but you'll want to validate that trochoidal/adaptive clearing isn't necessary at the proposed DOC. That's lesson 4d's job.
- **Roughing + finishing path** for the cylindrical case: take a roughing pass leaving 0.3 mm, then a single-pass finishing cut at full depth. Better surface finish on the perimeter.

## Status

Implemented and tested. `python cnc.py test` runs the mill_spacer tests alongside the rest.
