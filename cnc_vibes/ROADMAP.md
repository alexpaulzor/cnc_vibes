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
| 3c | [Photo-engraved wooden jigsaw with name-preserving cuts](lessons/laser/03_jigsaw/) | ✅ Productionized 2026-05. Single CLI (`jigsaw.py`) with `preview` / `cut` / `raster` / `mockup` subcommands; parametric geometry / encoder / emitter modules at lesson root; tests + regression locks vs scratch. |
| 3d | [Laser-cut spoilboard with M6 hole grid](lessons/laser/04_spoilboard/) | ✅ Parametric grid + auto-tiling for stock larger than design. Default matches Anolex 4030 bed (400×500mm, 9×10 holes @ 45mm). |

## Mill

| # | Lesson | Status |
|---|---|---|
| 4a | [Parametric router-cut spacer](lessons/mill/01_spacer/) | ✅ |
| 4b | [PCB engraving (Excellon drill side)](lessons/mill/04_pcb/) | ✅ |
| 4c | [Steel center-punch divets](lessons/mill/02_steel_center_punch/) | ✅ |
| 4d | [Aluminum trochoidal slot](lessons/mill/03_aluminum/) | ✅ |
| 4e | [Generic 2.5D CAM — no FreeCAD](lessons/mill/05_generic_cam/) | ✅ Composes `profile_cut` + `pocket_mill` + `drill_array` from `scripts/cam.py` into one mounting-plate part. Demonstrates the code-first CAM workflow end-to-end. |

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

## Active library — `scripts/cam.py` (parametric 2.5D CAM, no FreeCAD)

A code-first CAM library that produces validator-clean GCode for the common 2.5D ops from shapely shapes. Pure-function ops, op-tool warning framework, `strict=True` mode escalates warnings to errors.

- ✅ `profile_cut` — inside/outside/on cut around a polygon's perimeter (multi-pass Z)
- ✅ `pocket_mill` — offset-spiral clearance of a polygon interior (multi-pass Z, configurable stepover)
- ✅ `drill_array` — peck or single-plunge drill cycle at each (x, y)
- ✅ `engrave_text` — constant-depth outline trace of glyph contours (PIL+cv2 rasterize → findContours → per-contour G-code). NOT V-carve; variable-depth medial-axis variant still on the roadmap.
- ✅ Worked example: `lessons/mill/05_generic_cam/` composes profile/pocket/drill into a mounting plate
- ✅ `scripts/openscad_loader.py` — load 2D OpenSCAD designs into shapely polygons via `--export-format svg` → svgelements → shapely. Closes the loop between OpenSCAD authoring and the cam.py CAM library.
- 📋 `cnc.py preview <gcode>` ✅ for CAMotics already; `cnc.py cam <op>` CLI shim is unstarted
- 📋 V-carve (medial-axis variable-depth) for `engrave_text` — bigger algorithmic problem; constant-depth handles most cases

## Suggested learning order

The lessons build on each other. Recommended sequence:
1. **3a** (laser spacer) — establishes the parametric Python→GCode pattern + laser preflight
2. **3b** (laser calibration) — characterize your laser; values feed back into `profiles/laser_materials.yaml`
3. **4a** (router spacer) — same parametric idea with spindle + Z motion
4. **4c** (center-punch) — confirm spindle works on metal
5. **4d** (aluminum) — hardest 3-axis; don't attempt before 4a is solid
6. **4b** (PCB) — combines all prior lessons + FreeCAD CAM workflow
7. **4e** (generic CAM) — code-first multi-op composition with the `scripts/cam.py` library; no FreeCAD
8. **Int-01** (inspect) — first machine-talking tool; small scope, big preflight win
9. **Int-03** (probe-corner) — automates per-job WCS ritual; depends on Int-01
10. **Int-04** (laser cal) — interactive iteration; depends on Int-01
11. **3c** (jigsaw) — the productionized end-to-end example: text + photo + cut
12. **3d** (spoilboard) — generic auto-tiling pattern for designs larger than stock
13. **Int-02** (snapshot) — useful but lower priority; defer until camera bracket exists
14. **5** (plasma) — separate workstream

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
- ✅ Productionized to canonical lesson layout: `jigsaw.py` CLI (preview/cut/raster/mockup), `geometry.py` + `encoder.py` + `emitter.py` modules, `tests/` at lesson root with regression locks against scratch
- 📋 Delete `scratch/*` after the productionized code has been verified in actual cuts
- ✅ `job.yaml` integration (declarative config; `cnc.py jigsaw <yaml>` dispatches + `cnc.py preflight <yaml>` walks the laser checklist). Three sample yamls in [`examples/`](lessons/laser/03_jigsaw/examples/).
- 📋 Empirical gamma LUT for grayscale raster — bake the power-vs-darkness relationship for plywood/MDF into a lookup table (uses Int-04 `--mode engrave` patches as raw data)

## Next session candidates

Software-side, all unblocked (the bed is on the way but no software work depends on it):

- **Delete `scratch/*`** after the user verifies the productionized jigsaw cuts cleanly. Small commit, just cleanup.
- **Empirical gamma curve for grayscale raster** — bake the power-vs-darkness relationship for plywood/MDF into a lookup table so jigsaw raster's grayscale mode produces accurate tonal reproduction. Uses Int-04 `--mode engrave` patches as raw data.
- **Red-team test workflow** — user provides novel words/photos to surface corner cases the NORA canonical case doesn't.

Hardware-side (waiting on bed arrival):
- First-corner Z-focus measurement after the bed is installed.
- Run Int-04 cal on a real piece of stock to dial in cutting params.
- Cut the small puzzle test (phase6_small.gcode) on the dialed-in params.
- After confirming fit looks right at small scale, cut the full NORA puzzle (phase8 output).
