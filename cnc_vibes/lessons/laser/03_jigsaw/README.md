# Lesson 3c — Wooden jigsaw with name-preserving cuts

Generate cuttable GCode for a wooden jigsaw puzzle that embeds a name into the cut pattern. Letters become intact pieces that nest into pockets carved from the surrounding cells. Optional photo raster engraving overlays a continuous image across the assembled puzzle.

Loose-fit puzzle: centerline cuts, kerf becomes the natural clearance between pieces.

See [ALGORITHMS.md](ALGORITHMS.md) for the accumulated design rules, the geometry/cut-routing algorithms (shared edges, Eulerian/Chinese-Postman continuous cuts, lead-in warmup), laser/material rules, and open TODOs.

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
| `mini` | 100×100mm | 4×4 @ 25mm | `NORA` | 23 (20 cell + 3 letter) | Mini NORA test cut on a small scrap (e.g. cardboard) |
| `micro` | 150×150mm | 3×3 @ 50mm | `NORA` | varies | Tram / tolerance test cuts |
| `banner` | 150×75mm | letter-aligned, 2 rows | `NORA` | ~17–23 | Nameplate: grid lines derived from the letters (R12) |
| `full` | 300×300mm | 6×6 @ 50mm | `NORA` | 44 (40 cell + 4 letter) | The actual deliverable puzzle |

The **`banner`** preset uses the *letter-aligned grid*: vertical cut lines pass through each glyph's automatic origin (its dominant stroke) and the middle row boundary bends through those origins, so background pieces frame the letters cleanly instead of the slivers a uniform grid leaves. See ALGORITHMS.md R12/A12/A13.

Review tools:

```bash
# Contact sheet of every glyph's automatic grid-origin (red crosshair)
python lessons/laser/03_jigsaw/jigsaw.py glyphs

# The 7 banner name demos (NORA + alt names), archived to a git-ignored
# history folder on each change; --origins overlays the origin crosshair
python lessons/laser/03_jigsaw/jigsaw.py bannerdemos
python lessons/laser/03_jigsaw/jigsaw.py bannerdemos --origins
```

### Cold-start fade (`cut`)

Diode lasers fade in from cold, so the first few mm of each cut path can under-burn. With GRBL laser mode (`$32=1`) the beam fires *only while moving*, so a `G4` dwell does nothing — warmup has to happen through motion. Two flags matter:

- `--laser-mode dynamic|static` — `dynamic` (default) emits M4 (power scales with feed); `static` emits M3 constant power with a `;LASER_MODE: static` header. Static is easier to reason about on thin stock.
- `--lead-in-mm N` — after a closed cut loop finishes (laser now warm), re-trace its first `N` mm to clean up the cold under-cut start (default 10). This is the real cold-start remedy; there are no warmup-dwell flags.

Measure the ramp distance with `build/warmup_ramp_test.gcode` (cut it, see how far into each line the cut starts going through) and set `--lead-in-mm` to that + ~2mm.

### Cut emission strategy

- **Small**: per-polygon cut, letter-then-cells ordering. Shared edges cut twice; acceptable at low piece counts.
- **Full**: edge dedup via shapely `unary_union` + `linemerge` (shared cell-to-cell boundaries cut exactly once), containment-aware ordering (letter perimeters → interior → panel border last so stock stays attached until the final cut), greedy nearest-neighbor within each tier to reduce rapid travel. ~7m total cuts, ~24k GCode lines for the full NORA default.

### Raster modes

- **halftone** (default): PIL Floyd-Steinberg dither to 1-bit, fires at one fixed laser power. Calibration-tolerant. Newsprint-style stipple up close, photographic at arm's length.
- **grayscale**: posterize to N levels (default 16), per-pixel laser power scaled to darkness. Smoother gradients but needs a calibrated power-vs-darkness LUT (on the roadmap) for accurate tones.

Both emit three files: `<base>_raster.gcode` (engrave only), `<base>_cut.gcode` (pieces only), `<base>_full.gcode` (engrave then cut). Run the separate files if you want to verify the engrave before committing to the cut.

## Declarative jobs via `job.yaml`

For a curated invocation that survives outside your shell history, drop a `job.yaml` into [`examples/`](examples/) and dispatch via `cnc.py`. Three samples ship with the lesson:

| File | What it cuts | Use when |
|---|---|---|
| [`examples/small_n.yaml`](examples/small_n.yaml) | 80x80mm N, cut only | Calibration / first cut on a new material |
| [`examples/nora_mini_100.yaml`](examples/nora_mini_100.yaml) | 100x100mm NORA on 3mm corrugated, static M3 + 10mm lead-in | Mini test cut on a small scrap |
| [`examples/nora_300.yaml`](examples/nora_300.yaml) | Full 300x300mm NORA, cut only | The canonical deliverable |
| [`examples/nora_with_photo.yaml`](examples/nora_with_photo.yaml) | Full NORA + halftone photo raster | When you want the kitten-on-NORA effect |

```bash
# Generate GCode declared by the yaml (dispatches to jigsaw.py with the right flags)
python cnc.py jigsaw lessons/laser/03_jigsaw/examples/small_n.yaml

# Walk the laser preflight checklist against the yaml
python cnc.py preflight lessons/laser/03_jigsaw/examples/small_n.yaml
```

`head: laser` in the yaml routes `cnc.py preflight` to the laser checklist (PPE, air assist, fire-safety, laser-safe material) instead of the spindle one. Schema lives in [`job_yaml.py`](job_yaml.py); see the example files for the field shapes.



```
lessons/laser/03_jigsaw/
├── jigsaw.py        ← CLI entry point (subcommands)
├── job_yaml.py      ← job.yaml schema + argv derivation for `cnc.py jigsaw`
├── geometry.py      ← parametric polygon math (PuzzleConfig + pure functions)
├── encoder.py       ← image preprocessing + halftone/grayscale encoders
├── emitter.py       ← cut GCode (simple + dedup/toposort), raster GCode, combined output
├── examples/        ← sample job.yaml files (small_n, nora_300, nora_with_photo)
├── tests/
│   ├── test_geometry.py          ← regression locks vs scratch/phaseN
│   ├── test_emitter.py           ← validator-contract + dedup + classification
│   └── test_jigsaw_job_yaml.py   ← schema + argv round-trip + preflight routing
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
- **Empirical gamma LUT for grayscale raster** — bake the power-vs-darkness relationship for plywood/MDF into a lookup table for accurate tonal reproduction. Uses Int-04's `--mode engrave` for the raw calibration data.
- **Red-team testing** — once NORA is verified physical-cut-correct, user provides novel words/photos to surface corner cases the canonical case can't.
