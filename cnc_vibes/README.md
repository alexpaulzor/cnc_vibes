# cnc_vibes

**A code-first toolchain for a GRBL CNC.** Generate GCode parametrically in Python (or via OpenSCAD → FreeCAD), validate it against your machine's envelope, walk a safety checklist, then cut. Targets the Anolex 4030-Evo Ultra 2 + LaserTree 10W laser head, but the architecture (machine-as-YAML, head-aware validator) is portable to any GRBL 1.1+ router or diode laser.

**Primary platform**: Windows 11. macOS is supported as a development convenience.

## Who this is for

You want CNC parts to be **scripts**, not click-trails. You're OK reading Python and would rather edit a config file than click through a dialog. You may also want a visual sanity check (CAMotics) before committing to a real cut. The repo has both code-first and GUI-driven workflows; pick per-job.

## Two workflows, pick per part

**A — Pure Python (no FreeCAD).** Best for parametric 2.5D parts (puzzles, spacers, hole grids, pockets, profile cuts). Define the shape as a `shapely` polygon or call a pre-built lesson script; emit GCode directly; preview in CAMotics; validate; cut. No GUI except CAMotics for inspection.

```bash
# Example: full NORA jigsaw puzzle, MDF 3mm
python lessons/laser/03_jigsaw/jigsaw.py cut --size full --word NORA --material mdf_3mm
python cnc.py validate lessons/laser/03_jigsaw/build/cut_full_nora_seed7.gcode
python cnc.py preview  lessons/laser/03_jigsaw/build/cut_full_nora_seed7.gcode  # CAMotics
python cnc.py preflight lessons/laser/03_jigsaw/build/cut_full_nora_seed7.gcode
# ...load in your sender, cut...
```

**B — OpenSCAD + FreeCAD.** Best for complex 3D parts or anything that doesn't fit a 2.5D model. Design in OpenSCAD, import the CSG into FreeCAD, run the CAM workbench, post-process to GCode. The conceptual guide (`cnc_for_the_scad.md`) is a click-by-click walkthrough; `examples/hole_in_sheet/` is a worked sample. Slower to set up, more flexible.

Both paths share the same downstream tooling (`validate`, `preflight`, `preview`, `params`) and the same machine/tool/material profiles.

## Where to start (new reader)

Pick the lesson that matches what you actually want to cut. Each link is a self-contained README with usage, dependencies, and "what's next" pointers.

| Your situation | Start here | Why |
|---|---|---|
| **First time, want fastest path to a cut** | [3a laser spacer](lessons/laser/01_spacer/) | Smallest end-to-end lesson. Pure Python → laser GCode. Establishes the parametric pattern. Cuts in <2 minutes. |
| **Have a CNC router (not laser) and want a 2.5D part** | [4e generic CAM](lessons/mill/05_generic_cam/) | Worked example: composes `profile_cut` + `pocket_mill` + `drill_array` into one mounting plate. The reference for the code-first router workflow. |
| **Want to engrave a photo / make a jigsaw** | [3c jigsaw](lessons/laser/03_jigsaw/) | The productionized end-to-end example. Halftone + grayscale photo raster, multi-line per-letter fonts, optional wavy edges. |
| **Need to characterize an unknown laser** | [3b laser calibration](lessons/laser/02_calibration/) and then [Int-04 interactive cal](lessons/integration/04_interactive_laser_cal/) | Static matrix first, interactive iteration for the focus/power sweet spot. |
| **Want a 3D part you can't express as 2.5D shapely** | `cnc_for_the_scad.md` (Workflow B deep-dive) + [4a router spacer](lessons/mill/01_spacer/) (frustum case) | FreeCAD path. Slower setup, handles arbitrary 3D. |
| **Cutting metal** | [4c steel center-punch](lessons/mill/02_steel_center_punch/), then [4d aluminum trochoidal](lessons/mill/03_aluminum/) | Sanity-check spindle on metal with no cutting first; then graduate to actual material removal. |
| **Need to talk to the controller** (verify state, find IP, probe corners) | [Int-01 inspect](lessons/integration/01_inspect/), then [Int-03 probe-corner](lessons/integration/03_probe_corner/) | Same serial pattern, different scope. |

If you don't know which workflow yet, **default to Workflow A** (code-first). The lessons under `lessons/laser/` and `lessons/mill/` mostly fit there, and you skip the FreeCAD learning curve until you genuinely need it.

## Install

### Windows 11

```powershell
winget install Python.Python.3.12
winget install OpenSCAD.OpenSCAD            # only if using workflow B
winget install FreeCAD.FreeCAD              # only if using workflow B
python -m pip install -r requirements.txt
python cnc.py doctor
```

Then optionally for visual preview:
- [CAMotics](https://camotics.org) (free, open-source 3D simulator) — install from their site

### macOS

```bash
brew install python openscad
brew install --cask freecad camotics
python3 -m pip install -r requirements.txt
python3 cnc.py doctor
```

Use `python3` instead of `python` on macOS (or alias it). The same `cnc.py` commands work on both platforms.

### Raspberry Pi (sender or full workstation)

See [RASPBERRY_PI.md](RASPBERRY_PI.md) — Pi 4/5 with 64-bit Pi OS Lite runs the whole toolchain. RAM tiers, sender choice (bCNC vs FluidNC WebUI vs CNCjs caveats), CAMotics-doesn't-run-on-ARM workaround, and OS recommendation.

### Python deps (auto-installed via `requirements.txt`)

`pyyaml`, `pytest`, `shapely`, `opencv-python-headless` (jigsaw letter contour tracing), `Pillow` (image + font rendering), `pyserial` (Int-01/03/04 talking to GRBL), `zeroconf` (mDNS discovery for `cnc.py find-machine`).

## Commands at a glance

All driven through `python cnc.py <subcommand>`. Identical on Windows and macOS.

| Command | What it does |
|---|---|
| `cnc.py doctor` | Print the resolved toolchain (Python, OpenSCAD, deps). Run first. |
| `cnc.py help` | Browse the reference manpage-style. `help <topic>` for detail, `help --search KEYWORD` to find topics. |
| `cnc.py build <example>` | OpenSCAD → CSG (default) into `examples/<example>/build/`. Add `--format stl` for an STL sidecar. *(Workflow B only.)* |
| `cnc.py params <example>` | Print machine/material/tool lookups + derived feed/DOC/depth for the job, with safety checks. |
| `cnc.py validate <gcode>` | Machine-aware GCode lint (envelope, max feed, plunge, safe-Z, head-mode). |
| `cnc.py preview <gcode>` | Open the file in CAMotics for 3D toolpath + material simulation. Inspection only — no GUI for CAM. |
| `cnc.py preflight <example\|gcode>` | Print params, walk the interactive safety checklist. Non-zero exit if anything's unconfirmed. `--print-only` for non-interactive review. |
| `cnc.py find-machine` | mDNS/SSDP discovery of Grbl_ESP32 controllers on the LAN. |
| `cnc.py ip` | Print the controller's IP (cached if fresh, else mDNS scan). Composes in shell: `IP=$(cnc.py ip)`. |
| `cnc.py test` | Run the pytest suite. |
| `cnc.py clean` | Delete all `examples/*/build/` directories. |
| `cnc.py post <fcstd> <gcode>` | Not yet implemented — post-process from inside FreeCAD GUI for now. |

`python cnc.py --help` or `python cnc.py <subcommand> --help` for full usage.

## Repo layout

```
cnc_vibes/
├── README.md                          ← this file
├── ROADMAP.md                         ← at-a-glance lesson + work status
├── cnc_for_the_scad.md                ← conceptual guide (read first if new to CAM)
├── cnc.py                             ← task runner CLI (cross-platform)
├── requirements.txt
├── profiles/                          ← machine-as-config (swap to retarget)
│   ├── anolex_4030_evo_ultra2.yaml   ← machine envelope + feed limits
│   ├── tools.yaml                     ← endmills, ball-ends, V-bits, drills
│   ├── materials.yaml                 ← spindle chipload + DOC tables
│   └── laser_materials.yaml           ← per-material laser power/feed/passes
├── examples/                          ← per-job parts (Workflow B)
│   └── hole_in_sheet/                 ← reference example with worked CAM
├── lessons/                           ← progressive tutorials — see lessons/README.md
│   ├── laser/    {spacer, calibration, jigsaw, spoilboard}
│   ├── mill/     {spacer, steel-center-punch, aluminum-trochoidal, pcb-drill}
│   ├── integration/  {inspect, snapshot, probe-corner, interactive-laser-cal}
│   └── plasma/                         ← future tool head (specced, hardware-blocked)
├── scripts/
│   ├── cam.py                          ← parametric 2.5D CAM library (profile/pocket/drill/engrave)
│   ├── openscad_loader.py              ← OpenSCAD .scad/.svg → shapely Polygons (feeds cam.py)
│   ├── gcode_validate.py               ← per-line GCode rules (spindle + laser)
│   ├── job_params.py                   ← loaders, derived math, preflight checklists
│   ├── help_topics.py                  ← `cnc.py help` reference content
│   ├── find_cnc.py                     ← mDNS/SSDP controller discovery
│   └── cnc_state.py                    ← persisted last-seen-IP cache
└── tests/                              ← repo-wide pytest suite (currently ~490 passing)
```

## Lessons

Progressive tutorials, each demonstrating a technique you can reuse on your own jobs. Full index + suggested reading order in [lessons/README.md](lessons/README.md); [ROADMAP.md](ROADMAP.md) is the at-a-glance status view.

**Implemented and tested**:
- **Laser**: [3a spacer](lessons/laser/01_spacer/), [3b calibration matrix](lessons/laser/02_calibration/), [3c jigsaw](lessons/laser/03_jigsaw/) (productionized), [3d spoilboard](lessons/laser/04_spoilboard/)
- **Mill**: [4a router spacer](lessons/mill/01_spacer/), [4b PCB drill](lessons/mill/04_pcb/), [4c steel center-punch](lessons/mill/02_steel_center_punch/), [4d aluminum trochoidal](lessons/mill/03_aluminum/), [4e generic 2.5D CAM](lessons/mill/05_generic_cam/)
- **Integration** (talk to the machine): [Int-01 inspect](lessons/integration/01_inspect/), [Int-02 snapshot](lessons/integration/02_snapshot/), [Int-03 probe-corner](lessons/integration/03_probe_corner/), [Int-04 interactive laser cal](lessons/integration/04_interactive_laser_cal/)

**Specced for future**:
- **5 plasma** — ArcFony Cut53M Pro as third tool head, gated on mechanical fabrication

## The profile concept (machine-as-YAML)

Three YAMLs hold everything machine-, tool-, or material-specific:

- `profiles/<machine>.yaml` — envelope, feed limits, GRBL flavor, head conventions
- `profiles/tools.yaml` — endmills, ball-ends, V-bits, drills with per-tool RPM + plunge limits
- `profiles/materials.yaml` — chipload tables keyed by tool id, DOC fractions
- `profiles/laser_materials.yaml` — per-material laser power/feed/passes

Code in `scripts/` and `lessons/` reads from these. To retarget a different GRBL router: copy the machine YAML, edit its envelope + feeds + spindle range, point `cnc.py` at it via the `PROFILE` env var. The principle is laid out in `cnc_for_the_scad.md` §4.

## Adding a new parametric part (Workflow A)

Two ways to feed shapes into the cam.py library — define them in Python directly with `shapely`, OR author in OpenSCAD and load via the loader.

```python
# Option 1 — pure Python
from cam import profile_cut, load_tool, load_material, CamConfig
from shapely.geometry import Polygon

part = Polygon([(0, 0), (80, 0), (80, 40), (0, 40)])  # 80x40mm rectangle
gcode = profile_cut(
    part, depth_mm=6.0,
    tool=load_tool("flat_6mm_2flute"),
    material=load_material("plywood_baltic_birch_6mm"),
    cfg=CamConfig(spindle_rpm=18000, safe_z_mm=5.0),
)
print(gcode.text)
```

```python
# Option 2 — design in OpenSCAD, CAM in Python
from openscad_loader import openscad_to_polygons
from cam import profile_cut, load_tool, load_material

# my_part.scad contains: difference() { square([80, 40]); ...holes... }
polys = openscad_to_polygons("my_part.scad")
gcode = profile_cut(polys[0], depth_mm=6.0,
                    tool=load_tool("flat_6mm_2flute"),
                    material=load_material("plywood_baltic_birch_6mm"))
```

Then `python my_script.py > build/my_part.gcode`, validate, preview, preflight, cut. Same downstream pipeline as any lesson.

## Adding a new example with FreeCAD (Workflow B)

```bash
mkdir examples/my_new_part
cp examples/hole_in_sheet/hole_in_sheet.scad examples/my_new_part/my_new_part.scad
cp examples/hole_in_sheet/job.yaml examples/my_new_part/job.yaml
# Edit the .scad for geometry; tune job.yaml for material + tool + RPM
python cnc.py build my_new_part        # OpenSCAD → CSG
# Open my_new_part.FCStd in FreeCAD; set up Job; post to .gcode
python cnc.py validate examples/my_new_part/build/my_new_part.gcode
python cnc.py preview  examples/my_new_part/build/my_new_part.gcode
python cnc.py preflight my_new_part
```

The conceptual guide (`cnc_for_the_scad.md` §6) is the click-by-click walkthrough of the FreeCAD CAM setup.

## Customizing for a different machine

The repo treats your machine as configuration. To retarget:

1. Copy `profiles/anolex_4030_evo_ultra2.yaml` to `profiles/<your_machine>.yaml`.
2. Update envelope, max feed, spindle/laser range.
3. Either edit `cnc.py` + `scripts/gcode_validate.py` to point at the new file (one constant in each), or set the `PROFILE` env var.

The validator + preflight then work against your machine without any other code changes. The principle is discussed in `cnc_for_the_scad.md` §4.

## When something goes wrong

| Symptom | Likely cause | Where to look |
|---|---|---|
| `cnc.py doctor` shows `openscad: MISSING` | OpenSCAD not on PATH | `winget install OpenSCAD.OpenSCAD` or set `$env:OPENSCAD` |
| `cnc.py params` says "no chipload entry" | Material has no chipload for the chosen tool | Add the pair to `profiles/materials.yaml` |
| `cnc.py validate` flags `bounds` | GCode would drive outside the envelope | Check WCS origin in the Job — usually stock-placement issue |
| `cnc.py validate` flags `safe_z_rapid` | A G0 traverses XY below safe Z | Raise Safe Height in the Profile op, or `default_safe_z_mm` in machine YAML |
| `cnc.py validate` flags `laser_m4_required` | Job uses M3 instead of M4 dynamic | Switch to laser-mode emitter that uses M4 (lessons all do this correctly) |
| `cnc.py preview` says CAMotics not found | CAMotics not installed at expected location | `brew install --cask camotics` or download from https://camotics.org |
| Preflight refuses to start | Safety check failed before checklist | Fix the params issue first, re-run preflight |
| Int-04 says `ALARM:8 Homing fail` | `$27` pull-off too small | `$27=5` in your sender, then `$X` to unlock |

For everything else, the conceptual guide (`cnc_for_the_scad.md`) explains the *why* behind each tool and decision.

## Further reading

- **[cnc_for_the_scad.md](cnc_for_the_scad.md)** — conceptual guide. What CAM is, why CNC is harder than 3D printing, the FreeCAD object model. Read this first if you're new to CAM.
- **[ROADMAP.md](ROADMAP.md)** — at-a-glance status of every lesson + what's pending.
- **[lessons/README.md](lessons/README.md)** — lesson index with suggested reading order.
- **[lessons/JOURNAL.md](lessons/JOURNAL.md)** — session-by-session decision log.
