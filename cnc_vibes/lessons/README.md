# Lessons

Progressive, increasingly-sophisticated CNC and laser projects, each demonstrating a technique you can reuse. Assumes you've read the main [cnc_for_the_scad.md](../cnc_for_the_scad.md) guide and have `cnc.py doctor` happy.

Each lesson directory has its own README with goal, prerequisites, the actual work, and extensions. SPEC.md files (where present) capture the design decisions behind each lesson — useful when adapting a lesson into your own project.

## Laser

| # | Lesson | Status | Toolchain |
|---|---|---|---|
| 3a | [Parametric laser-cut PCB spacer](laser/01_spacer/) | ✅ implemented | Fully automated. Pure Python → GCode. |
| 3b | [Laser calibration pattern (power × passes × speed)](laser/02_calibration/) | ✅ implemented | Fully automated. Pure Python → GCode with engraved labels. |
| 3c | Photo-engraved wooden jigsaw with name-preserving cuts | far future | Multi-stage. Aspirational. |

## Mill

| # | Lesson | Status | Toolchain |
|---|---|---|---|
| 4a | [Parametric router-cut spacer](mill/01_spacer/) | ✅ implemented | Hybrid: cylindrical case fully automated; frustum case via FreeCAD CAM. |
| 4b | Perfboard-style PCB engraving on copper-clad blanks (no chemicals) | not yet | KiCAD → Gerber → FlatCAM/pcb2gcode → cnc.py validate. |
| 4c | Steel center-punch divets — precisely-located marks for follow-up drilling. Engraver tip, single-point Z plunge per location, no cutting. ~1/8" mild steel. | not yet | Fully automatable. Pure Python → GCode given a list of (x, y) points. |
| 4d | Aluminum milling — small parts, very conservative feeds/DOC, trochoidal/adaptive clearing to keep tool engagement low. Lubrication (WD-40 / kerosene) and chip evacuation are first-class concerns. | not yet | Semi-automated via FreeCAD CAM with hand-tuned ops. |

## Integration (machine state + camera)

Standalone Python tools (no LLM dependency) that talk to the machine or watch it. See [integration/README.md](integration/) for the workstream overview.

| # | Tool | Status | Purpose |
|---|---|---|---|
| Int-01 | [`inspect`](integration/01_inspect/SPEC.md) | spec only | Read GRBL state via serial; verify `$32`, WCS offsets, alarms before cut. |
| Int-02 | [`snapshot`](integration/02_snapshot/SPEC.md) | spec only (future) | One-shot webcam stills for before/after-cut setup verification. |
| Int-03 | [`probe-corner`](integration/03_probe_corner/SPEC.md) | spec only | Automated WCS-finding routine via touch plate. Saves 2-3 min per job. |

## Plasma (future)

The ArcFony Cut53M Pro plasma cutter has CNC control ports on the rear but needs serious mechanical and electrical integration before the Anolex can drive it. **Not for this iteration.**

Outline of what plasma will require, captured here so it's not lost:

- **5a — Plasma torch outrigger + wiring.** Mount the torch on an arm that puts the cut zone off the machine bed (over a water table or sacrificial surface). Adapt the GRBL spindle PWM / coolant pins to drive the plasma's torch-on relay. Read back the arc-OK signal so motion only starts after the arc establishes.
- **5b — Plasma sheet-metal cutout** (parametric bracket or similar). Pierce delay handling (0.5–2 s before motion). Conservative feeds (plasma cuts very fast — 1000–3000 mm/min for thin steel, but the bottleneck is acceleration). Torch-height-control is open: either fixed Z (simple, OK for flat sheet) or floating-head probe (better cut quality, more mechanism).
- **Safety:** plasma is the most hazardous head — UV, sparks, hot metal, EMI noise that can corrupt USB serial. Galvanic isolation between the plasma controller and the GRBL controller is mandatory.

The plasma section will get its own SPEC and lessons when we're ready to tackle it.

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
