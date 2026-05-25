# Roadmap

What's done, what's in flight, what's next. Maintained alongside the lessons. The full table of contents with descriptions lives in [lessons/README.md](lessons/README.md); this file is the at-a-glance status view.

## Status legend
- ✅ implemented and tested
- 🔨 in progress
- 📋 spec only / aspirational
- ⛔ blocked (typically on hardware)

## Laser

| # | Lesson | Status |
|---|---|---|
| 3a | [Parametric laser-cut PCB spacer](lessons/laser/01_spacer/) | ✅ |
| 3b | [Laser calibration pattern (power × passes × speed)](lessons/laser/02_calibration/) | ✅ |
| 3c | [Photo-engraved wooden jigsaw with name-preserving cuts](lessons/laser/03_jigsaw/) | 🔨 Algorithm + small-puzzle GCode + full-puzzle GCode + photo raster all working in `scratch/` (Phases 1-8). Productionization to canonical lesson layout still pending. |
| 3d | [Laser-cut spoilboard with M6 hole grid](lessons/laser/04_spoilboard/) | ✅ Parametric grid + auto-tiling for stock larger than design. Default matches Anolex 4030 bed (400×500mm, 9×10 holes @ 45mm). |

## Mill

| # | Lesson | Status |
|---|---|---|
| 4a | [Parametric router-cut spacer](lessons/mill/01_spacer/) | ✅ |
| 4b | [PCB engraving (Excellon drill side)](lessons/mill/04_pcb/) | ✅ |
| 4c | [Steel center-punch divets](lessons/mill/02_steel_center_punch/) | ✅ |
| 4d | [Aluminum trochoidal slot](lessons/mill/03_aluminum/) | ✅ |

## Integration (machine state + camera)

| # | Tool | Status |
|---|---|---|
| Int-01 | [`inspect`](lessons/integration/01_inspect/) — read GRBL state via serial | ✅ |
| Int-02 | [`snapshot`](lessons/integration/02_snapshot/) — webcam stills for setup verification | ✅ |
| Int-03 | [`probe-corner`](lessons/integration/03_probe_corner/) — automated WCS-finding via touch plate | ✅ |
| Int-04 | [interactive laser cal](lessons/integration/04_interactive_laser_cal/) — iterative Z/power/feed/passes tuning | ✅ (cut mode + engrave mode for grayscale calibration) |

## Plasma

| # | Lesson | Status |
|---|---|---|
| 5 | [ArcFony Cut53M Pro as third tool head](lessons/plasma/) | ⛔ blocked — requires mechanical fabrication (outrigger mount, opto-isolator) |

## Suggested learning order

The lessons build on each other. Recommended sequence:
1. **3a** (laser spacer) — establishes the parametric Python→GCode pattern + laser preflight
2. **3b** (laser calibration) — characterize your laser; values feed back into `profiles/laser_materials.yaml`
3. **4a** (router spacer) — same parametric idea with spindle + Z motion
4. **4c** (center-punch) — confirm spindle works on metal
5. **4d** (aluminum) — hardest 3-axis; don't attempt before 4a is solid
6. **4b** (PCB) — combines all prior lessons + FreeCAD CAM workflow
7. **Int-01** (inspect) — first machine-talking tool; small scope, big preflight win
8. **Int-03** (probe-corner) — automates per-job WCS ritual; depends on Int-01
9. **Int-04** (laser cal) — interactive iteration; depends on Int-01
10. **3c** (jigsaw) — aspirational; sub-roadmap below
11. **Int-02** (snapshot) — useful but lower priority; defer until camera bracket exists
12. **5** (plasma) — separate workstream

## Active work — Lesson 3c jigsaw sub-roadmap

The jigsaw lesson is the only one in flight. Current state in `lessons/laser/03_jigsaw/scratch/`:

- ✅ Phase 1: cell-grid puzzle with Bezier knob tabs
- ✅ Phase 2: sub-piece detection + tab-coverage analysis
- ✅ Phase 4: letters as intact polygons, carved from cell pockets (Phase 3 abandoned)
- ✅ Phase 5: tab shifting away from letters + sliver merging
- ✅ Lollipop tab geometry (stem + circle, mechanical undercut)
- ✅ One-tab-radius clearance enforcement between tab cavities and letter edges
- ✅ Phase 6: small puzzle test variant (4 pieces + 1 letter, ~80×80mm)
- ✅ GCode emission from polygon set (centerline cuts, validator-clean)
- ✅ Simple inside-out cut ordering (letters first, then cells)
- ✅ Phase 7: photo raster engraving (halftone via Floyd-Steinberg, grayscale via per-pixel power modulation); emits raster-only, cut-only, and combined GCode
- ✅ Phase 8: full NORA-scale (300×300mm, 44-piece) GCode emitter with edge dedup (`unary_union` + `linemerge`) + containment-aware ordering (letter → interior → panel border) + greedy nearest-neighbor travel reduction
- 📋 Productionize: move from `scratch/` to canonical lesson layout (README, CLI, tests, profile integration)
- 📋 Photo engraving on the full puzzle — needs phase7's raster pipeline decoupled from its phase6_small dependency so it can pair with phase8's full-puzzle cut
- 📋 Empirical gamma curve for phase7's grayscale mode (bake the power-vs-darkness relationship for plywood into a lookup table for accurate tonal reproduction)

## Next session candidates

Software-side, all unblocked (the bed is on the way but no software work depends on it):

- **Productionize jigsaw out of `scratch/`** — move to canonical lesson layout: `jigsaw.py` (single CLI selecting small/full/raster modes), `tests/` at lesson root, README + SPEC updated, profile-integration via `job.yaml`. Consolidates phases 5/6/7/8 into one coherent script and decouples phase7's raster pipeline from the small-puzzle config so it can pair with phase8.
- **Empirical gamma curve for grayscale raster** — bake the power-vs-darkness relationship for plywood/MDF into a lookup table so phase7's grayscale mode produces accurate tonal reproduction. Uses calibration patches from Int-04's `--mode engrave` as raw data.

Hardware-side (waiting on bed arrival):
- First-corner Z-focus measurement after the bed is installed.
- Run Int-04 cal on a real piece of stock to dial in cutting params.
- Cut the small puzzle test (phase6_small.gcode) on the dialed-in params.
- After confirming fit looks right at small scale, cut the full NORA puzzle (phase8 output).
