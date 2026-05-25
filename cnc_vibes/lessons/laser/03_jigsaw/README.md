# Lesson 3c — Wooden jigsaw with name-preserving cuts

Generate cuttable GCode for a wooden jigsaw puzzle that embeds a name into the cut pattern. Letters become intact pieces that nest into pockets carved from the surrounding cells. Optional photo raster engraving overlays a continuous image across the assembled puzzle.

Loose-fit puzzle: centerline cuts, kerf becomes the natural clearance between pieces.

## How to use

Single CLI: `jigsaw.py` at the lesson root. Four subcommands.

```bash
# Verification diagram (no GCode, just an image)
python lessons/laser/03_jigsaw/jigsaw.py preview --size full --word NORA --seed 7
python lessons/laser/03_jigsaw/jigsaw.py preview --size small --word N

# Cut GCode
python lessons/laser/03_jigsaw/jigsaw.py cut --size full --word NORA --material mdf_3mm
python lessons/laser/03_jigsaw/jigsaw.py cut --size small --word N --material mdf_3mm

# Photo raster + cut (three output files: raster, cut, combined)
python lessons/laser/03_jigsaw/jigsaw.py raster --image kitten.jpg --size small --mode halftone
python lessons/laser/03_jigsaw/jigsaw.py raster --image kitten.jpg --size full --mode halftone

# Mockup: visual halftone vs grayscale comparison on a wood-color mockup
python lessons/laser/03_jigsaw/jigsaw.py mockup --image kitten.jpg --word NORA

# Validate the GCode
python cnc.py validate lessons/laser/03_jigsaw/build/cut_full_nora_seed7.gcode
```

### Sizes

| `--size` | Panel | Cells | Default `--word` | Pieces (typical) | When to use |
|---|---|---|---|---|---|
| `small` | 80×80mm | 2×2 @ 40mm | `N` | 5 (4 cell + 1 letter) | Calibration / test cuts; many fit on one piece of stock |
| `full` | 300×300mm | 6×6 @ 50mm | `NORA` | 44 (40 cell + 4 letter) | The actual deliverable puzzle |

### Cut emission strategy

- **Small**: per-polygon cut, letter-then-cells ordering. Shared edges cut twice; acceptable at low piece counts.
- **Full**: edge dedup via shapely `unary_union` + `linemerge` (shared cell-to-cell boundaries cut exactly once), containment-aware ordering (letter perimeters → interior → panel border last so stock stays attached until the final cut), greedy nearest-neighbor within each tier to reduce rapid travel. ~7m total cuts, ~24k GCode lines for the full NORA default.

### Raster modes

- **halftone** (default): PIL Floyd-Steinberg dither to 1-bit, fires at one fixed laser power. Calibration-tolerant. Newsprint-style stipple up close, photographic at arm's length.
- **grayscale**: posterize to N levels (default 16), per-pixel laser power scaled to darkness. Smoother gradients but needs a calibrated power-vs-darkness LUT (on the roadmap) for accurate tones.

Both emit three files: `<base>_raster.gcode` (engrave only), `<base>_cut.gcode` (pieces only), `<base>_full.gcode` (engrave then cut). Run the separate files if you want to verify the engrave before committing to the cut.

## Module layout

```
lessons/laser/03_jigsaw/
├── jigsaw.py        ← CLI entry point (subcommands)
├── geometry.py      ← parametric polygon math (PuzzleConfig + pure functions)
├── encoder.py       ← image preprocessing + halftone/grayscale encoders
├── emitter.py       ← cut GCode (simple + dedup/toposort), raster GCode, combined output
├── tests/
│   ├── test_geometry.py    ← regression locks vs scratch/phaseN
│   └── test_emitter.py     ← validator-contract + dedup + classification
├── figs/            ← preview + mockup PNGs (rendered)
├── build/           ← generated GCode (gitignored)
├── scratch/         ← historical phase-by-phase development; superseded by the modules above
└── SPEC.md          ← original design history
```

The algorithm geometry is parametric over a `PuzzleConfig` dataclass — multiple puzzle sizes coexist in one process. The previous scratch/ design had `phase6_small` mutating `phase2`'s module-level constants which made phase6 + phase8 mutually-exclusive imports; that's gone now.

## Algorithm summary

1. **Cell grid** with lollipop tabs (thin stem + circular bulb) for mechanical undercut grip.
2. **Letter rasterization** via PIL, contour-traced via OpenCV `findContours RETR_CCOMP` so letter counters (O's hole, R's bowl, A's triangle) are properly nested.
3. **Tab shifting**: tabs that would slice a letter outline shift along their edge to a clear position. If no clear position exists, the tab is dropped (that edge becomes a straight cut).
4. **Pocket carving**: subtract the letter union from each cell; what's left is the cell fragments around the letter.
5. **Sliver merging**: thin fragments (less than one tab-radius wide or smaller than 10% of a cell) absorb into their largest adjacent neighbor. Letter counters (no adjacent fragment) are correctly left alone.
6. **Letters as pieces**: each letter's polygon becomes its own piece that drops into the pocket carved for it.

## Dependencies

- `shapely` — polygon Boolean ops, containment, edge merging
- `opencv-python-headless` — letter contour tracing
- `Pillow` — letter rasterization, image loading, mockup rendering
- `numpy` — installed as cv2 dependency
- `pyyaml` — material profile loading

Install via the repo's standard `pip install -r requirements.txt`.

## Status

Productionized 2026-05. The `scratch/` directory is kept alive for reference but is no longer the canonical implementation — please use `jigsaw.py` for any new work. The `scratch/` phase scripts will be removed in a follow-up commit after the productionized code has been verified in actual cuts.

Pending on the roadmap:
- **job.yaml integration** — declarative config like `lessons/mill/01_spacer/`. Would let `cnc.py preflight` walk the laser-cut checklist before firing.
- **Empirical gamma LUT for grayscale raster** — bake the power-vs-darkness relationship for plywood/MDF into a lookup table for accurate tonal reproduction. Uses Int-04's `--mode engrave` for the raw calibration data.
- **Red-team testing** — once NORA is verified physical-cut-correct, user provides novel words/photos to surface corner cases the canonical case can't.
