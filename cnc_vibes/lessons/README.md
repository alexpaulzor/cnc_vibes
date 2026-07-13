# Lessons

A practical (and safe-enough) choose-your-own-adventure cheat-sheet for deterministic, headless CAM: turn ideas and code into good physical parts, navigate the gotchas per task/material/operation, and automate the boring conversions, table lookups, and calibration. Each entry is a self-contained recipe you can lift into your own jobs — pick by what you're cutting, not by order. Aimed at busy makers who'd rather run a command than click through dialogs. Assumes you've run `python cnc.py doctor` and skimmed the top-level [README](../README.md) (its [Where to start](../README.md#where-to-start-pick-by-task) table is the primary situation → start-here map). The conceptual guide [cnc_for_the_scad.md](../cnc_for_the_scad.md) is the deeper dive on the FreeCAD-based workflow.

Each directory has its own `README.md` (user-facing: goal, prereqs, usage, extensions) and optionally a `SPEC.md` (design rationale for future maintainers).

## Laser

| # | Lesson | Status | Toolchain |
|---|---|---|---|
| 3a | [Parametric laser-cut PCB spacer](laser/01_spacer/) | ✅ | Fully automated. Pure Python → GCode. |
| 3b | [Laser calibration pattern (power × passes × speed)](laser/02_calibration/) | ✅ | Fully automated. Pure Python → GCode with engraved labels. |
| 3c | Wooden jigsaw with name-preserving cuts + photo raster — **moved to `~/src/vibes/jigsawzall`** | ➡️ | Now its own repo. |
| 3d | [Laser-cut spoilboard with M6 hole grid](laser/04_spoilboard/) | ✅ | Parametric grid + auto-tiling for designs larger than stock. Pure Python → GCode. |
| — | **Concentric spiral laser calibration** — `cnc.py cal-laser` (`scripts/spiral_cal.py`) | ✅ | Recommended laser calibration. One small disc of concentric rings cut inner→outer at feeds = circumference/loop-time; stop when a ring stops falling free = max clean single-pass feed. Each ring has a spiral warmup lead-in recording the cold-start ramp. Center origin; outputs gcode + toolpath PNG + a self-explanatory key PNG. Supersedes the old square test card + hex-spiral cal. |

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

Not a lesson per se, but the foundation for code-first 2.5D milling. Pure-function library: shapely shape + tool + material → validator-clean GCode, no FreeCAD GUI. Ships eight ops: `profile_cut`, `pocket_mill`, `drill_array`, `engrave_text` (constant-depth outline trace of glyph contours; not V-carve), `chamfer_edge` (V-bit perimeter chamfer), `profile_cut_with_tabs` (profile cut leaving N small bridges to stock), `slot_mill` (stadium-shape adjustable-mount slots), and `face_mill` (zig-zag raster stock surfacing). Each op emits clear warnings for default-pick / op-tool mismatch and supports `CamConfig(strict=True)` to escalate warnings to errors. The worked example at **[4e generic CAM](mill/05_generic_cam/)** composes the cut/pocket/drill ops into one part — read its README for the full pipeline (generate → validate → CAMotics preview → preflight → cut); the other five ops compose the same way.

```python
from cam import profile_cut, pocket_mill, drill_array, engrave_text, load_tool, load_material
out_cut = profile_cut(my_polygon, depth_mm=6.0,
                      tool=load_tool("flat_6mm_2flute"),
                      material=load_material("plywood_baltic_birch_6mm"))
```

For one-off shell-driven cuts (rrect + holes, circle pocket, label engrave), use `cnc.py cam <op>` — a thin CLI/TUI shim over the library that handles common shape primitives (rect, rrect, circle, ellipse, polygon, svg, scad) and hole patterns (grid, bolt-circle, linear, explicit) without writing Python. Supports both `--head spindle` and `--head laser`. Run `cnc.py cam` with no arguments for an interactive prompt_toolkit wizard, or `cnc.py help cam-cli` for the full flag catalog.

## Plasma (future)

The **ArcFony Cut53M Pro** as a third tool head. **Not for this iteration; requires mechanical fabrication** (outrigger mount, opto-isolated electrical interface). Lesson removed; machine noted here for context.

## Pick by task (not in order)

These recipes don't form a curriculum — grab whichever matches the job in front of
you. The [Where to start](../README.md#where-to-start-pick-by-task) table in the
top-level README maps situation → start-here → why, and the tables above are the
full capability matrix. If you want a sense of what leans on what: the
machine-talking tools (Int-03 probe-corner, Int-04 laser cal) reuse the serial
pattern from Int-01 inspect, and the harder metal work (4d aluminum trochoidal)
assumes you're comfortable with the parametric spindle path from 4a. New to the
parametric Python → GCode idea? 3a (laser spacer) is the smallest end-to-end
example. Everything else stands alone.

```
lessons/<category>/NN_<short_name>/
├── README.md     ← user-facing (goal, prereqs, usage, extensions)
├── SPEC.md       ← optional: design rationale for future maintainers
├── <name>.py     ← the script(s) the lesson produces or uses
├── tests/        ← unit tests at lesson root
└── build/        ← gitignored output directory
```

Lessons are picked up by `python cnc.py test` automatically. Add a help topic in `scripts/help_topics.py` so the lesson surfaces via `cnc.py help`. Cross-reference in this index + [ROADMAP.md](../ROADMAP.md).
