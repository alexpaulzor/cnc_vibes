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
| 3c | [Photo-engraved wooden jigsaw with name-preserving cuts](lessons/laser/03_jigsaw/) | 🔨 Algorithm + small-puzzle GCode + photo raster all working in `scratch/` (Phases 1-7). Full NORA-scale GCode emission and productionization to canonical lesson layout still pending. |

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
| Int-04 | [interactive laser cal](lessons/integration/04_interactive_laser_cal/) — iterative Z/power/feed/passes tuning | ✅ (cutting mode; grayscale mode pending — see lesson README) |

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
- 📋 Full containment toposort for the NORA-scale puzzle (44 pieces)
- 📋 Productionize: move from `scratch/` to canonical lesson layout (README, CLI, tests, profile integration)
- 📋 Photo engraving overlay — implemented in phase7 (halftone + grayscale); future: gamma-calibrated grayscale power curve for "photo-realistic" rendering

## Next session candidates

Software-side, all unblocked (the bed is on the way but no software work depends on it):

- **Full NORA-scale GCode emission** — extend phase7's emitter to the 300×300mm, 44-piece full puzzle. Needs containment toposort (letter counters → letter perimeters → cell-to-cell boundaries → panel perimeter) and edge dedup so shared boundaries are cut once.
- **Productionize jigsaw out of `scratch/`** — move to canonical lesson layout: `jigsaw.py` (single CLI), `tests/` at lesson root, README + SPEC updated, profile-integration via `job.yaml`.
- **Add grayscale-engrave mode to Int-04** — currently cutting-only; raster patches at varying power for grayscale calibration.
- **Empirical gamma curve for grayscale raster** — bake the power-vs-darkness relationship for plywood into a lookup table so phase7's grayscale mode produces accurate tonal reproduction.

Hardware-side (waiting on bed arrival):
- First-corner Z-focus measurement after the bed is installed.
- Run Int-04 cal on a real piece of stock to dial in cutting params.
- Cut the small puzzle test (phase6_small.gcode) on the dialed-in params.
