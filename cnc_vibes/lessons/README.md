# Lessons

Progressive, increasingly-sophisticated CNC and laser projects, each demonstrating a technique you can reuse. Assumes you've read the main [cnc_for_the_scad.md](../cnc_for_the_scad.md) guide and have `cnc.py doctor` happy.

Each lesson directory has its own README with goal, prerequisites, the actual work, and extensions. SPEC.md files (where present) capture the design decisions behind each lesson — useful when adapting a lesson into your own project.

## Laser

| # | Lesson | Status | Toolchain |
|---|---|---|---|
| 3a | [Parametric laser-cut PCB spacer](laser/01_spacer/) | ✅ implemented | Fully automated. Pure Python → GCode. |
| 3b | Laser calibration pattern (power × passes × speed) | not yet | Will be fully automated, Python-only. |
| 3c | Photo-engraved wooden jigsaw with name-preserving cuts | far future | Multi-stage. Aspirational. |

## Mill

| # | Lesson | Status | Toolchain |
|---|---|---|---|
| 4a | Parametric router-cut spacer (degenerate-cylindrical + frustum variants) | not yet | Hybrid: simple case fully automated; 3D case via FreeCAD CAM. |
| 4b | Perfboard-style PCB engraving on copper-clad blanks (no chemicals) | not yet | KiCAD → Gerber → FlatCAM/pcb2gcode → cnc.py validate. |

## How to read this section

The lessons aren't independent — they build on each other. Suggested order:
1. **3a (spacer)** — establishes the laser GCode generation pattern, the laser-mode preflight, and the CAM-as-code idea.
2. **3b (calibration)** — uses the same pattern to characterize your laser, producing the numbers that improve 3a's `profiles/laser_materials.yaml`.
3. **4a (router spacer)** — same parametric-part idea, now with a spindle and Z motion.
4. **4b (PCB engraving)** — combines parts of all prior lessons plus the FreeCAD CAM workflow.
5. **3c (jigsaw)** — aspirational; will need its own sub-roadmap.

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
