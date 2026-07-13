# Lesson 3d — Laser-cut spoilboard with M6 hole grid

Generate cuttable GCode for a fresh spoilboard with a regular M6 mounting-hole grid, automatically tiled to fit your stock and machine envelope.

The Anolex 4030's bed has a 9×10 grid of M6 mounting holes on 45mm centers, spanning 400×500mm. The default config matches that bed; flags let you target other machines and grids.

## Why this is a lesson worth teaching

Three things compound in one short script:

1. **Parametric grid generation** with margin-auto-center math.
2. **Tiling logic** — when the design exceeds your stock, you need joints that don't bisect features. Here the joints fall between hole rows/columns, never through a hole.
3. **Innermost-cuts-first ordering** — holes cut before the perimeter so the tile doesn't release before its features are made.

Reusable across any application where you need a regular feature grid bigger than your stock.

## Usage

```bash
# Anolex defaults (400x500mm panel, 9x10 hole grid, 6.5mm holes, 300x300 stock, 3mm MDF)
python lessons/laser/04_spoilboard/spoilboard.py

# Different machine bed
python lessons/laser/04_spoilboard/spoilboard.py \
    --panel-w 300 --panel-h 200 --hole-cols 6 --hole-rows 4 --hole-spacing 50

# Different material
python lessons/laser/04_spoilboard/spoilboard.py --material plywood_baltic_birch_3mm

# Layout image only, skip GCode
python lessons/laser/04_spoilboard/spoilboard.py --no-gcode

# Validate
python cnc.py validate lessons/laser/04_spoilboard/build/spoilboard_tile_1.gcode
```

| Flag | Default | Meaning |
|---|---|---|
| `--panel-w` / `--panel-h` | 400, 500 | Full spoilboard size (mm) |
| `--hole-cols` / `--hole-rows` | 9, 10 | Number of holes per axis |
| `--hole-spacing` | 45 | Grid spacing (mm) |
| `--hole-dia` | 6.5 | Nominal hole diameter; kerf widens by ~0.2mm |
| `--margin-x` / `--margin-y` | auto | Distance from panel edge to first hole; defaults centered |
| `--stock-w` / `--stock-h` | 300, 300 | Available stock size; binds tile size |
| `--material` | `mdf_3mm` | Material profile id from `profiles/laser_materials.yaml` |
| `--no-gcode` | off | Render layout image only (preview without committing) |

## Output

- `figs/spoilboard_layout.png` — verification image with all tiles, hole positions, dimensions
- `build/spoilboard_tile_N.gcode` — one file per tile (N tiles for the default 4)

The default Anolex config produces 4 tiles:

| Tile | Size (mm) | Holes | Origin (panel coords) |
|---|---|---|---|
| 1 | 267.5 × 295 | 36 | (0, 0) — lower-left |
| 2 | 132.5 × 295 | 18 | (267.5, 0) — lower-right |
| 3 | 267.5 × 205 | 24 | (0, 295) — upper-left |
| 4 | 132.5 × 205 | 12 | (267.5, 295) — upper-right |

Total: 90 holes (= 9 × 10). Tile area sums to 200000mm² (= 400 × 500). All four fit in 300×300mm stock.

## Cut workflow per tile

For each tile:

1. Clamp stock on the machine; set WCS origin (X=0, Y=0) at the LOWER-LEFT corner of the stock.
2. Set Z to focal height (touch off + jog up; same procedure as Int-04).
3. Load `spoilboard_tile_N.gcode` in your sender.
4. **Walk the preflight checklist** — `python cnc.py preflight lessons/laser/04_spoilboard/build/spoilboard_tile_N.gcode`.
5. Cut. Holes are cut first (small G1 circle approximations, 36 segments each), then perimeter releases the tile.

## Assembly

The tiles butt-joint. The M6 bolts going through (spoilboard ∪ tile boundary) into the machine bed slots align everything when you torque them down. A ~0.5mm gap at the joints is invisible and immaterial.

## Why circle-approximation polygons instead of `G2`/`G3` arcs?

Per-segment `G1` lines give consistent kerf width regardless of how your sender / GRBL build interpolates arcs. For a 6.5mm hole, 36 segments → chord error of ~0.025mm, well below the laser's kerf. If you want tighter approximation, pass it through — the function accepts `n_segments`.

## What about CNC routing?

A spindle-routed version is intentionally NOT in this lesson. Routing M6 holes requires helical milling (circle path + Z descent through material), which needs a tool selection, RPM, feed/DOC math — best handled by the existing FreeCAD CAM workflow rather than a Python emitter. If you want a CNC spoilboard later, the geometry functions in `spoilboard.py` (`compute_hole_positions`, `compute_tiles`) are reusable — call them from a FreeCAD macro to build a Job with a Drill operation per hole.

## Status

Implemented and tested. 27 unit tests cover hole-position math, axis-splits-between-holes, tile-area conservation, hole-assignment-once-only, and the GCode shape (M4 not M3, S in range, holes-before-perimeter, coords within tile envelope, pass count matches material).

GCode validates clean against `profiles/default.yaml` (`cnc.py validate`).
