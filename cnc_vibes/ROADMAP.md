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
| 3c | [Photo-engraved wooden jigsaw with name-preserving cuts](lessons/laser/03_jigsaw/) | 🔨 Phase 5 working in `scratch/`; GCode emission + cut-ordering not yet built |

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
- 🔨 Small puzzle test variant (4 pieces + 1 letter, <10×10cm) — pending
- 🔨 GCode emission from polygon set — pending
- 🔨 Cut ordering via containment toposort — pending
- 📋 Productionize: move from `scratch/` to canonical lesson layout (README, CLI, tests, profile integration)
- 📋 Photo engraving overlay (per original spec; user's current target is name-only)

## Next session candidates

- Run the cal script for real (user is queued up for this)
- Build the small-puzzle test variant + GCode emitter
- Productionize the jigsaw lesson out of `scratch/`
- Add grayscale-engrave mode to Int-04
