# Lesson 4e — Generic 2.5D CAM, no FreeCAD

Compose [`scripts/cam.py`](../../../scripts/cam.py)'s three operations (profile_cut + pocket_mill + drill_array) into one part, no GUI for CAM. Demonstrates the **code-first CNC workflow**: shapely shapes piped through cam.py → validator → CAMotics preview → preflight → cut.

## Why this exists

The other mill lessons each demonstrate ONE op (4a profile, 4c divet array, 4d trochoidal slot). Real parts usually need multiple ops in one job — corner holes drilled, a center pocket cleared, the outer perimeter profile-cut. This lesson is that case, as a single composable Python file. Future readers can copy it as a starting point for their own multi-op parts.

## The part

A 60×40mm rectangular mounting plate in 6mm Baltic birch plywood with:

- **4 corner M4 clearance holes** (3.2mm diameter), inset 8mm from each corner, drilled through with peck cycle
- **1 central 20×10mm pocket**, 3mm deep, milled with offset-spiral
- **Outer perimeter cut** through 6mm

Three operations, one GCode file. Cut order: holes → pocket → perimeter (so the part stays anchored to the stock until the very last operation, when the perimeter cut releases it).

## Run

```bash
# Generate GCode with defaults
python lessons/mill/05_generic_cam/mounting_plate.py

# Or override any param via CLI
python lessons/mill/05_generic_cam/mounting_plate.py \
    --plate-w-mm 80 --plate-h-mm 50 \
    --pocket-w-mm 30 --pocket-h-mm 15 \
    --plate-thickness-mm 12 \
    --material plywood_baltic_birch_6mm

# CI-safe: fail loud on any sketchy default-pick
python lessons/mill/05_generic_cam/mounting_plate.py --strict
```

## The full pipeline (no FreeCAD)

```bash
# 1. Generate
python lessons/mill/05_generic_cam/mounting_plate.py

# 2. Lint the GCode against the machine envelope + tool limits
python cnc.py validate lessons/mill/05_generic_cam/build/mounting_plate.gcode

# 3. Visual sanity check in CAMotics (3D toolpath + material simulation)
python cnc.py preview  lessons/mill/05_generic_cam/build/mounting_plate.gcode

# 4. Interactive pre-cut safety checklist
python cnc.py preflight lessons/mill/05_generic_cam/build/mounting_plate.gcode

# 5. Load in your sender and run, swapping tools at the ;TOOL line breaks
#    (the script pauses GCode flow naturally between sections)
```

## Tool changes between sections

The output is one GCode file with three op sections (drill → pocket → profile), each preceded by a `; ===== <label> =====` marker AND with its own `;TOOL: <id>` header. **Each section needs a different physical tool** in the spindle:

| Section | Tool | Physical change required |
|---|---|---|
| 1 — corner mount holes | `drill_3.2mm_m4_clearance` | Install 3.2mm drill bit |
| 2 — center pocket | `flat_3.175mm_2flute` | Swap to 1/8" flat endmill |
| 3 — outer perimeter | `flat_3.175mm_2flute` | (same tool as section 2, no swap) |

If you have a tool-change macro in your sender, the section breaks make natural pause points. Otherwise, pause manually at the `; ===== <next section> =====` marker, swap tools, re-zero Z if needed, resume.

## What the demo proves

- **The cam.py library composes naturally**: three different ops in one file, sharing CamConfig (safe_z, spindle_rpm, strict mode).
- **Cut order is operator-controlled** (here: inside-features-first so the perimeter cut comes last and releases the part).
- **Per-op warnings still fire correctly when composed** — try changing `--drill-tool flat_3.175mm_2flute` to see the flat-endmill-for-drilling warning surface in section 1.
- **Validator-clean output** — `cnc.py validate` checks the whole composed file for envelope violations, max feed/plunge, spindle-mode correctness.
- **CAMotics preview works** end-to-end — `cnc.py preview` opens the simulation and you can SEE the part shape before committing to a cut.

## Extending this lesson

The pattern in `mounting_plate.py` generalizes. Common variations:

- **Different shapes**: any shapely Polygon works as input to `profile_cut` or `pocket_mill`. Generate via `shapely.geometry.box`, ellipses, or assembled from points.
- **Different hole patterns**: pass any list of `(x, y)` tuples to `drill_array`.
- **Different materials**: any id in `profiles/materials.yaml`. Cam.py reads chipload + DOC fractions automatically.
- **Different tool selection per op**: each cam.py function takes a `tool=` parameter independently.
- **Tabs on the profile cut**: `profile_cut` doesn't currently support tabs; add as a follow-up if needed (the existing jigsaw cut emitter has a related sliver-bridging concept that could be adapted).

## Status

Implemented. Tests in `tests/test_mounting_plate.py` check that the default invocation produces a validator-clean GCode file with the expected three sections + correct hole count.

The cam.py library itself has 51 tests covering profile_cut + pocket_mill + drill_array. See `tests/test_cam.py`.
