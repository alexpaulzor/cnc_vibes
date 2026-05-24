# Lessons

Progressive, increasingly-sophisticated CNC and laser projects, each demonstrating a technique you can reuse. Assumes you've read the main [cnc_for_the_scad.md](../cnc_for_the_scad.md) guide and have `cnc.py doctor` happy.

Each lesson directory has its own README with goal, prerequisites, the actual work, and extensions. SPEC.md files (where present) capture the design decisions behind each lesson — useful when adapting a lesson into your own project.

## Laser

| # | Lesson | Status | Toolchain |
|---|---|---|---|
| 3a | [Parametric laser-cut PCB spacer](laser/01_spacer/) | ✅ implemented | Fully automated. Pure Python → GCode. |
| 3b | [Laser calibration pattern (power × passes × speed)](laser/02_calibration/) | ✅ implemented | Fully automated. Pure Python → GCode with engraved labels. |
| 3c | [Photo-engraved wooden jigsaw with name-preserving cuts](laser/03_jigsaw/) | spec only (aspirational) | Multi-stage. Raster engrave + tessellation + name-preserving cut algorithm. |

## Mill

| # | Lesson | Status | Toolchain |
|---|---|---|---|
| 4a | [Parametric router-cut spacer](mill/01_spacer/) | ✅ implemented | Hybrid: cylindrical case fully automated; frustum case via FreeCAD CAM. |
| 4b | [PCB engraving on copper-clad blanks](mill/04_pcb/) — no chemicals. Excellon drill file → cnc.py drill GCode (this lesson). Isolation routing delegated to FlatCAM. | ✅ implemented (drill side) | KiCAD → FlatCAM (isolation) + excellon_to_gcode.py (drilling) → cnc.py validate. |
| 4c | [Steel center-punch divets](mill/02_steel_center_punch/) — precisely-located marks for follow-up drilling. Engraver tip, single-point Z plunge per location, no cutting. ~1/8" mild steel. | ✅ implemented | Fully automated. Pure Python → GCode. CSV/YAML/grid input. |
| 4d | [Aluminum milling](mill/03_aluminum/) — small parts, very conservative feeds/DOC, trochoidal clearing to keep tool engagement low. Lubrication (WD-40 / kerosene) and chip evacuation are first-class concerns. | ✅ implemented | Mix: 4a handles spacers; trochoidal_slot.py demonstrates low-engagement slotting. |

## Integration (machine state + camera)

Standalone Python tools (no LLM dependency) that talk to the machine or watch it. See [integration/README.md](integration/) for the workstream overview.

| # | Tool | Status | Purpose |
|---|---|---|---|
| Int-01 | [`inspect`](integration/01_inspect/) | ✅ implemented | Read GRBL state via serial; verify `$32`, WCS offsets, alarms before cut. |
| Int-02 | [`snapshot`](integration/02_snapshot/) | ✅ implemented | One-shot webcam stills for before/after-cut setup verification. |
| Int-03 | [`probe-corner`](integration/03_probe_corner/) | ✅ implemented | Automated WCS-finding routine via touch plate. Saves 2-3 min per job. |
| Int-04 | [interactive laser cal](integration/04_interactive_laser_cal/) | ✅ implemented | Drives the laser to cut one test circle at a time; operator evaluates and adjusts params per iteration. Dial in Z focus + power + feed + passes by feel. |

## Plasma (future)

See [lessons/plasma/](plasma/) — the ArcFony Cut53M Pro plasma cutter as a third tool head. **Not for this iteration; requires mechanical fabrication.** The SPEC captures the workstream design (outrigger mount, opto-isolated electrical interface, software integration phases) so it survives the months between now and when you tackle it.

## How to read this section

The lessons aren't independent — they build on each other. Suggested order:
1. **3a (spacer)** — establishes the laser GCode generation pattern, the laser-mode preflight, and the CAM-as-code idea.
2. **3b (calibration)** — uses the same pattern to characterize your laser, producing the numbers that improve 3a's `profiles/laser_materials.yaml`.
3. **4a (router spacer)** — same parametric-part idea, now with a spindle and Z motion.
4. **4c (steel center-punch)** — natural extension of 4a's "spindle does a parametric thing" — just an array of dots instead of profile cuts. Confirms the spindle path works on metal (even if only superficially).
5. **4d (aluminum milling)** — the hardest 3-axis lesson. Don't attempt before 4a is solid.
6. **4b (PCB engraving)** — combines parts of all prior lessons plus the FreeCAD CAM workflow.
7. **Int-01 (inspect)** — first machine-talking tool; small scope, big preflight win. Builds the serial pattern Int-03 depends on.
8. **Int-03 (probe-corner)** — automates the per-job WCS ritual. Depends on Int-01.
9. **3c (jigsaw)** — aspirational; will need its own sub-roadmap.
10. **Int-02 (snapshot)** — useful but lower priority; defer until a camera bracket physically exists.
11. **5 (plasma)** — separate workstream, requires mechanical fabrication before any code.

## Adding a new lesson

```
lessons/<category>/NN_<short_name>/
├── README.md     ← user-facing lesson (goal, prereqs, usage, extensions)
├── SPEC.md       ← optional: design decisions for future maintainers
├── <name>.py     ← script(s) the lesson produces or uses
├── tests/        ← unit tests for the script(s)
└── build/        ← gitignored output directory
```

Lessons are picked up by `python cnc.py test` automatically. Add corresponding help topics in `scripts/help_topics.py` so the lesson is discoverable via `cnc.py help`.
