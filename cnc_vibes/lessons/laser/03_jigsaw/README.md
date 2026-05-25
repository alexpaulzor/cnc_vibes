# Lesson 3c — Jigsaw puzzle

> Algorithm-complete in `scratch/`; productionization to canonical lesson layout still pending. Cuttable GCode emits today from `scratch/phase6_small.py`.

## What this lesson is

A wooden jigsaw puzzle that:

1. Embeds a name (default "NORA") into the cut pattern.
2. Keeps each letter as an intact piece nested inside a pocket carved from the surrounding cells.
3. Cuts as classic interlocking puzzle pieces between the cells, with the tab geometry shifted away from letter outlines so tab cavities don't end up touching the letter edges.

The original spec called for a photo-engraved jigsaw with name-preserving cuts. The name-preserving cut algorithm is the substance of what's been built; raster image engraving is deferred — name-only is the current target.

## Where the code lives

Everything is in `scratch/` while the algorithm stabilizes.

| File | Purpose |
|---|---|
| `scratch/diagram_word_phase2.py` | Cell grid + Bezier→**lollipop** tab geometry + letter polygon rendering (contour-traced via OpenCV). The geometric foundation. |
| `scratch/diagram_word_phase4.py` | Letters as intact pieces carved into cell pockets. Polygon-with-hole rendering. |
| `scratch/diagram_word_phase5.py` | Tab shifting (move tabs along the edge to clear letter outlines), sliver merging (absorb thin fragments into larger neighbors), one-tab-radius clearance enforcement. The current "canonical" algorithm. |
| `scratch/phase6_small.py` | Small (80×80mm) puzzle generator that overrides phase2's module constants and emits a cuttable `.gcode` file. Validator-clean. Exposes `generate_pieces()` for reuse. |
| `scratch/phase7_raster.py` | Photo raster engraving on top of the small puzzle. Halftone (Floyd-Steinberg) or grayscale (per-pixel power) modes; emits three GCode files (raster-only, cut-only, combined). |
| `scratch/phase8_full_puzzle.py` | Full NORA-scale (300×300mm, 44-piece) GCode emitter. Edge dedup via `unary_union` + `linemerge`; containment-aware ordering (letter perimeters → interior cell-to-cell → panel border); greedy nearest-neighbor within each tier. |
| `scratch/tests/test_phase6_small.py` | 12 unit tests for the small-puzzle GCode emitter. |
| `scratch/tests/test_phase7_raster.py` | 33 unit tests for image preprocessing, encoders, run extraction, raster GCode shape, and the three output forms. |
| `scratch/tests/test_phase8_full_puzzle.py` | 15 unit tests for edge dedup, classification, greedy ordering, and full-puzzle GCode shape. |

Phases 1 and 3 are dead ends preserved for history; do not import them.

## How to use it today

Generate the small test puzzle (one letter, 4 pieces, ~80×80mm — many trials fit on one piece of stock):

```bash
python lessons/laser/03_jigsaw/scratch/phase6_small.py --word N --seed 7
python cnc.py validate lessons/laser/03_jigsaw/build/small_puzzle_n.gcode
```

Outputs:
- `figs/small_puzzle_n.png` — verification diagram
- `build/small_puzzle_n.gcode` — cuttable GCode (loose-fit: kerf becomes the natural clearance)

Engrave a photo on top of that puzzle, then cut:

```bash
# Halftone (default): Floyd-Steinberg dither at fixed power. Calibration-tolerant.
python lessons/laser/03_jigsaw/scratch/phase7_raster.py --image baby.jpg

# Grayscale: per-pixel power modulation. Smoother gradients; needs a calibrated power curve.
python lessons/laser/03_jigsaw/scratch/phase7_raster.py --image baby.jpg --mode grayscale

# No image handy? Test with a built-in gradient + disc pattern.
python lessons/laser/03_jigsaw/scratch/phase7_raster.py --test-pattern
```

Emits three GCode files per run: `<stem>_raster.gcode` (engrave only), `<stem>_cut.gcode` (pieces only), `<stem>_full.gcode` (engrave then cut). Use the separate files if you want to verify the engrave before committing to the cut; use the combined file for one-shot jobs.

For the full NORA-sized puzzle (300×300mm, 44 pieces):

```bash
python lessons/laser/03_jigsaw/scratch/phase8_full_puzzle.py --word NORA --seed 7
python cnc.py validate lessons/laser/03_jigsaw/build/full_puzzle_nora.gcode
```

Emits `build/full_puzzle_nora.gcode` (~24k lines, ~7.2m of cuts at the default plywood feed). Shared cell-to-cell boundaries are cut exactly once (edge dedup via `unary_union` + `linemerge`); cut order is letter perimeters → interior → panel border (so the stock stays attached until the very last pass).

> **Note on running phase6/7 and phase8 together**: they want different `phase2` constants (small puzzle vs. full puzzle). Don't import them in the same Python process. `phase8.generate_pieces()` will raise `RuntimeError` if it detects phase6 has been loaded. Run each from its own `python` invocation.

Photo raster engraving for the full puzzle is intentionally deferred — combining phase7's raster pipeline with phase8's full-puzzle cuts needs phase7 decoupled from its phase6_small dependency. See ROADMAP.

## What the algorithm does

1. Generate a regular cell grid (e.g., 6×6 cells of 50mm each).
2. Add interlocking tabs on each internal edge — **lollipop geometry**: a thin stem rising into a circular bulb, mechanical undercut on both sides for grip.
3. Rasterize the word at the panel's center; contour-trace it (OpenCV `findContours` with `RETR_CCOMP` so letter counters like O's hole are correctly nested).
4. For each tab, check whether its bulb sits within one tab-radius of any letter outline. If yes, shift the tab along its edge to a clear position; if no clear position exists, drop the tab entirely (that edge becomes a straight cut).
5. Subtract the letter shapes from the cells to form letter-shaped pockets in the cell pieces.
6. Merge sliver fragments (cells split into thin pieces by the letter intrusion) into their largest adjacent neighbor. Letter counters (surrounded by the empty pocket, no adjacent fragment) are correctly left alone.
7. Each letter becomes its own intact piece that drops into the pocket.

## Cutting strategy

**Loose-fit puzzle**: cut on the centerline — the laser kerf (typically 0.15-0.3mm) becomes the natural clearance between adjacent pieces. No kerf compensation is applied. For a tighter fit you'd offset bulb sides outward and cavity sides inward, but the user's target is "loose fit is fine."

**Cut order** (phase6_small): letters first (they're surrounded by material that holds them during the cut), then cells (any order — duplicate cuts on shared edges are acceptable for 5-piece test puzzles; full toposort needed for the NORA-scale puzzle).

## Dependencies

- `shapely` — polygon Boolean ops, MultiPolygon, containment
- `opencv-python-headless` — letter contour tracing
- `Pillow` — letter rasterization + verification image rendering
- `numpy` — installed as cv2 dependency

Install via the standard `pip install -r requirements.txt`.

## Pending — see [ROADMAP.md](../../../ROADMAP.md) for status

- Full-panel GCode emission (containment toposort, travel optimization, edge dedup)
- Productionize: move out of `scratch/` to a canonical lesson layout (jigsaw.py + tests/ + CLI)
- Photo engraving overlay (original spec; deferred)

## SPEC

[SPEC.md](SPEC.md) captures the original phasing/design (3c-1 through 3c-4). The algorithm shipped does not strictly match that phasing — see SPEC.md's "Implementation note" section (forthcoming).
