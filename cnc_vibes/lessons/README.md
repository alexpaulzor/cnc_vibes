# Lessons

Progressive CNC and laser projects, each demonstrating a technique you can lift into your own jobs. Assumes you've run `python cnc.py doctor` and read at least the top-level [README](../README.md). The conceptual guide [cnc_for_the_scad.md](../cnc_for_the_scad.md) is the deeper dive on the FreeCAD-based workflow.

Each lesson directory has its own `README.md` (user-facing: goal, prereqs, usage, extensions) and optionally a `SPEC.md` (design rationale for future maintainers).

## Laser

| # | Lesson | Status | Toolchain |
|---|---|---|---|
| 3a | [Parametric laser-cut PCB spacer](laser/01_spacer/) | ✅ | Fully automated. Pure Python → GCode. |
| 3b | [Laser calibration pattern (power × passes × speed)](laser/02_calibration/) | ✅ | Fully automated. Pure Python → GCode with engraved labels. |
| 3c | [Wooden jigsaw with name-preserving cuts + photo raster](laser/03_jigsaw/) | ✅ | Productionized: `jigsaw.py` CLI (preview / cut / raster / mockup). Lollipop tabs, tab-shifting, sliver merging, Floyd-Steinberg or grayscale photo engrave, multi-line per-letter fonts, optional wavy edges. |
| 3d | [Laser-cut spoilboard with M6 hole grid](laser/04_spoilboard/) | ✅ | Parametric grid + auto-tiling for designs larger than stock. Pure Python → GCode. |

## Mill

| # | Lesson | Status | Toolchain |
|---|---|---|---|
| 4a | [Parametric router-cut spacer](mill/01_spacer/) | ✅ | Hybrid: cylindrical case fully automated; frustum case via FreeCAD CAM. |
| 4b | [PCB engraving on copper-clad blanks](mill/04_pcb/) | ✅ (drill side) | KiCAD → FlatCAM (isolation) + `excellon_to_gcode.py` (drilling) → `cnc.py validate`. No chemicals. |
| 4c | [Steel center-punch divets](mill/02_steel_center_punch/) | ✅ | Fully automated. Pure Python → GCode. CSV/YAML/grid input. Precision marking for follow-up drilling. |
| 4d | [Aluminum trochoidal slot](mill/03_aluminum/) | ✅ | Low-engagement clearing for the 500W spindle. WD-40 / kerosene + chip evacuation are first-class concerns. |
| 4e | [Generic 2.5D CAM — no FreeCAD](mill/05_generic_cam/) | ✅ | Worked example composing `profile_cut` + `pocket_mill` + `drill_array` from `scripts/cam.py` into one mounting-plate part. The reference for the code-first CAM workflow. |

## Integration (talk to the machine, see what's happening)

Standalone Python tools — any human (or other CLI) can run them without an LLM in the loop.

| # | Tool | Status | Purpose |
|---|---|---|---|
| Int-01 | [`inspect`](integration/01_inspect/) | ✅ | Read GRBL state via serial. Catches "not homed", "$32 in wrong mode", "WCS not where you think it is". `--ip-only` extracts IP from Grbl_ESP32's `$I` for shell scripting. |
| Int-02 | [`snapshot`](integration/02_snapshot/) | ✅ | One-shot webcam captures for before/after-cut setup verification. |
| Int-03 | [`probe-corner`](integration/03_probe_corner/) | ✅ | Automated touch-plate probing to set the front-left-top WCS. Saves 2-3 min per job. |
| Int-04 | [interactive laser cal](integration/04_interactive_laser_cal/) | ✅ | Drives the laser to cut one test target per iteration; operator evaluates between firings. `--mode cut` (circle) or `--mode engrave` (raster patch) for cut or grayscale-engrave tuning. `--telnet` for raw-TCP transport on Grbl_ESP32. |

## Library — `scripts/cam.py`

Not a lesson per se, but the foundation for code-first 2.5D milling. Pure-function library: shapely shape + tool + material → validator-clean GCode, no FreeCAD GUI. Ships `profile_cut`, `pocket_mill`, `drill_array` (51 tests); `engrave_text` still on the roadmap. Each op emits clear warnings for default-pick / op-tool mismatch and supports `CamConfig(strict=True)` to escalate warnings to errors. The worked example at **[4e generic CAM](mill/05_generic_cam/)** composes all three ops into one part — read its README for the full pipeline (generate → validate → CAMotics preview → preflight → cut).

```python
from cam import profile_cut, pocket_mill, drill_array, load_tool, load_material
out_cut = profile_cut(my_polygon, depth_mm=6.0,
                      tool=load_tool("flat_6mm_2flute"),
                      material=load_material("plywood_baltic_birch_6mm"))
```

## Plasma (future)

[`plasma/`](plasma/) — the ArcFony Cut53M Pro as a third tool head. **Not for this iteration; requires mechanical fabrication** (outrigger mount, opto-isolated electrical interface). SPEC captures the full workstream design.

## Suggested reading order

Lessons build on each other. Recommended sequence for a new reader:

1. **3a (laser spacer)** — establishes the Python → GCode pattern, the laser-mode preflight, the CAM-as-code idea.
2. **3b (laser calibration)** — uses the same pattern to characterize your laser; values feed back into `profiles/laser_materials.yaml`.
3. **4a (router spacer)** — same parametric-part idea with spindle + Z motion.
4. **4c (steel center-punch)** — natural extension of 4a; confirms spindle path works on metal even if only superficially.
5. **4d (aluminum trochoidal)** — hardest 3-axis lesson; don't attempt before 4a is solid.
6. **4b (PCB drill)** — combines prior lessons + FlatCAM for isolation routing.
7. **4e (generic CAM)** — composes `profile_cut` + `pocket_mill` + `drill_array` from `scripts/cam.py` into one part. The canonical example for the code-first CAM workflow (no FreeCAD).
8. **Int-01 (inspect)** — first machine-talking tool; small scope, big preflight win. Builds the serial pattern Int-03 + Int-04 depend on.
9. **Int-03 (probe-corner)** — automates the per-job WCS ritual.
10. **Int-04 (interactive laser cal)** — interactive iteration for focus/power/feed dialing.
11. **3c (jigsaw)** — the productionized end-to-end example: text + photo + cut, full lesson layout to mirror.
12. **3d (spoilboard)** — generic auto-tiling pattern for designs larger than stock.
13. **Int-02 (snapshot)** — lower priority; useful once a camera bracket exists.
14. **5 (plasma)** — separate workstream, hardware-blocked.

## Adding a new lesson

```
lessons/<category>/NN_<short_name>/
├── README.md     ← user-facing (goal, prereqs, usage, extensions)
├── SPEC.md       ← optional: design rationale for future maintainers
├── <name>.py     ← the script(s) the lesson produces or uses
├── tests/        ← unit tests at lesson root
└── build/        ← gitignored output directory
```

Lessons are picked up by `python cnc.py test` automatically. Add a help topic in `scripts/help_topics.py` so the lesson surfaces via `cnc.py help`. Cross-reference in this index + [ROADMAP.md](../ROADMAP.md).
