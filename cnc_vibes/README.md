# cnc_vibes

A code-first toolchain for taking OpenSCAD designs to CNC GCode on a GRBL-class router. Primary platform is **Windows 11**; macOS works as a development convenience.

The conceptual guide — what CAM is, why CNC is harder than 3D printing, the FreeCAD object model, the worked example with full click-through — lives in **[cnc_for_the_scad.md](cnc_for_the_scad.md)**. Read that first. This README is the day-to-day reference once you've internalized the pipeline.

---

## Install

### Windows 11

```
winget install Python.Python.3.12
winget install OpenSCAD.OpenSCAD
winget install FreeCAD.FreeCAD
```

Close and reopen PowerShell, then from the repo root:

```
python -m pip install -r requirements.txt
python cnc.py doctor
```

`doctor` prints the toolchain it resolved. If anything shows `MISSING`, either reinstall via winget or set the path explicitly:

```
$env:OPENSCAD = "C:\Program Files\OpenSCAD\openscad.exe"
$env:FREECAD_CMD = "C:\Program Files\FreeCAD 1.0\bin\FreeCADCmd.exe"
```

Add those lines to your PowerShell profile (`notepad $PROFILE`) to persist them.

### macOS (bonus)

```
brew install python openscad
brew install --cask freecad
python3 -m pip install -r requirements.txt
python3 cnc.py doctor
```

Use `python3` instead of `python` on macOS (or alias it).

---

## Commands

All driven through `python cnc.py <subcommand>`. Identical on Windows and macOS.

| Command | What it does |
|---|---|
| `cnc.py help` | Browse the toolchain reference manpage-style. `cnc.py help <topic>` for detail; `cnc.py help --search KEYWORD` to find topics. Start here when you forget a flag or want to know what's available. |
| `cnc.py doctor` | Print the resolved toolchain (Python, OpenSCAD, FreeCADCmd, deps). Run this first. |
| `cnc.py build <example>` | OpenSCAD → CSG (default) into `examples/<example>/build/`. Add `--format stl` for an STL sidecar (slicer preview / 3D print). |
| `cnc.py params <example>` | Print the lookup tables (machine, material, tool) and the derived feed/DOC/depth numbers for the job, with safety checks. |
| `cnc.py preflight <example>` | Print params, then walk an interactive pre-cut safety checklist. Aborts with a non-zero exit if anything is unconfirmed. |
| `cnc.py preflight <example> --print-only` | Same checklist, non-interactive. Useful for review or printing. |
| `cnc.py validate <gcode>` | Run the machine-aware GCode validator (bounds, max feed, plunge, safe-Z, spindle-on). |
| `cnc.py test` | Run the pytest suite. |
| `cnc.py clean` | Delete all `examples/*/build/` directories. |
| `cnc.py post <fcstd> <gcode>` | (Not yet implemented — for now, post-process from inside the FreeCAD GUI.) |

Run `python cnc.py --help` or `python cnc.py <subcommand> --help` for full usage.

---

## Per-job workflow

Each example under `examples/` represents one part you can cut. The end-to-end flow for an existing example:

1. **Edit the design.** Open `examples/<name>/<name>.scad` in OpenSCAD (or any text editor) and tune parameters.
2. **Regenerate geometry.** `python cnc.py build <name>` runs OpenSCAD with `--export-format csg` and writes `examples/<name>/build/<name>.csg`. The CSG file is text describing OpenSCAD's evaluated CSG tree; FreeCAD's OpenSCAD workbench parses it and rebuilds the geometry as real B-rep solids with selectable faces and edges.
3. **CAM in FreeCAD** *(first time only)*. Open `examples/<name>/<name>.FCStd` in FreeCAD. Switch to the **OpenSCAD** workbench, import the `.csg` (gives you a Part Solid). Switch to **CAM**, create a Job with the imported Solid as the Model, configure Stock (Box from base bounding box + 10 mm extra X/Y), set the WCS origin at stock front-left-top, attach a Tool Controller matching `profiles/tools.yaml`, add Profile operations (Inside for holes, Outside for the perimeter — `Side` is per-op, not per-edge), drop a Tabs Dressup on the perimeter Profile, configure the grbl post-processor, save the `.FCStd`. Detailed click-through in `cnc_for_the_scad.md` §6.
4. **Post to GCode.** Right-click the Job in FreeCAD → Post Process. Save into `examples/<name>/build/<name>.gcode`.
5. **Validate.** `python cnc.py validate examples/<name>/build/<name>.gcode`. Aborts on any rule violation (bounds, max feed, missing spindle-on, unsafe rapid, etc.).
6. **Preflight.** `python cnc.py preflight <name>`. Prints the params (so you can sanity-check what's about to happen against the GCode that's about to run), then walks the safety checklist interactively. You must answer `y` to every item; anything else triggers an abort.
7. **Cut.** With preflight passed and your sender loaded with the GCode, run the job. Stay near the e-stop.

After a parameter tweak to the `.scad` (different hole size, different sheet dimensions, etc.), the loop shortens to: `cnc.py build` → reopen the FCStd (geometry refreshes automatically because Stock was "From Base shape") → right-click Job → Post Process → `cnc.py validate` → `cnc.py preflight` → cut.

---

## Per-job configuration: `job.yaml`

`params` and `preflight` read a `job.yaml` next to the `.scad` to know which material, tool, and spindle speed the job is targeting. Example (`examples/hole_in_sheet/job.yaml`):

```yaml
material: plywood_baltic_birch_3mm    # id from profiles/materials.yaml
tool: flat_3.175mm_2flute             # id from profiles/tools.yaml
spindle_rpm: 18000                    # within machine + tool RPM range
gcode: examples/hole_in_sheet/build/hole_in_sheet.gcode
```

Changing `spindle_rpm` here changes the derived feed rate (chipload × flutes × rpm). Changing `material` swaps to a different chipload table. The CAM project file (`.FCStd`) is the source of truth for what FreeCAD actually emits — `job.yaml` is what cnc.py uses to tell you what the GCode *should* match.

If `params` says your derived feed is 1440 mm/min and the GCode coming out of FreeCAD shows F900, that's a discrepancy worth investigating (probably stale CAM project after a `job.yaml` change).

---

## Repo layout

```
cnc_vibes/
├── README.md                          ← this file
├── ROADMAP.md                         ← at-a-glance lesson status
├── cnc_for_the_scad.md                ← conceptual guide (read first)
├── cnc.py                             ← task runner (cross-platform)
├── requirements.txt                   ← pyyaml, pytest, shapely, opencv, Pillow
├── profiles/
│   ├── anolex_4030_evo_ultra2.yaml   ← machine envelope + feeds + spindle range
│   ├── tools.yaml                     ← endmills, ball-ends, V-bits with limits
│   ├── materials.yaml                 ← spindle chipload tables, DOC fractions
│   └── laser_materials.yaml           ← per-material laser power/feed/passes
├── examples/
│   └── hole_in_sheet/
│       ├── hole_in_sheet.scad         ← parametric source (CSG output)
│       ├── job.yaml                   ← material + tool + spindle_rpm for this job
│       ├── hole_in_sheet.FCStd        ← FreeCAD CAM project (you create in step 3)
│       └── build/                     ← generated CSG / STL / GCode (gitignored)
├── lessons/                           ← see lessons/README.md for full index
│   ├── laser/    {01_spacer, 02_calibration, 03_jigsaw}
│   ├── mill/     {01_spacer, 02_steel_center_punch, 03_aluminum, 04_pcb}
│   ├── integration/  {01_inspect, 02_snapshot, 03_probe_corner, 04_interactive_laser_cal}
│   └── plasma/   (specced, not implemented)
├── scripts/
│   ├── gcode_validate.py              ← per-line GCode rules (spindle + laser)
│   ├── job_params.py                  ← loaders, derived math, preflight checklists
│   └── help_topics.py                 ← `cnc.py help` reference content
└── tests/
    ├── test_profiles.py               ← profile YAML schema sanity
    ├── test_gcode_validate.py         ← validator rules (spindle + laser)
    ├── test_job_params.py             ← math + safety checks + load_job error paths
    └── test_help_topics.py            ← help structure + dynamic content sync
```

---

## Lessons

Progressive tutorials, each demonstrating a technique you can reuse. See [lessons/README.md](lessons/README.md) for the full index and the suggested reading order; [ROADMAP.md](ROADMAP.md) is the at-a-glance status view.

**Implemented lessons** (have working code + tests):

- **3a** — [Parametric laser-cut PCB spacer](lessons/laser/01_spacer/) (fully automated, Python → GCode)
- **3b** — [Laser calibration pattern](lessons/laser/02_calibration/) (power × passes × speed matrix)
- **4a** — [Parametric router-cut spacer](lessons/mill/01_spacer/) (hybrid: cylindrical fully automated, frustum via FreeCAD)
- **4b** — [PCB Excellon-to-GCode drill converter](lessons/mill/04_pcb/) (delegates isolation routing to FlatCAM)
- **4c** — [Steel center-punch divets](lessons/mill/02_steel_center_punch/) (precision marking for follow-up drilling)
- **4d** — [Aluminum trochoidal slot](lessons/mill/03_aluminum/) (low-engagement clearing for the 500W spindle)

**Integration tools** (talk to the machine + watch it):

- **Int-01** — [`inspect`](lessons/integration/01_inspect/) — read GRBL state via serial
- **Int-02** — [`snapshot`](lessons/integration/02_snapshot/) — webcam stills for setup verification
- **Int-03** — [`probe-corner`](lessons/integration/03_probe_corner/) — automated WCS-finding via touch plate
- **Int-04** — [interactive laser cal](lessons/integration/04_interactive_laser_cal/) — iterative Z/power/feed/passes tuning via serial

**In progress**:

- **3c** — [Photo-engraved jigsaw with name-preserving cuts](lessons/laser/03_jigsaw/) (algorithm complete in `scratch/`; productionization pending)

**Specced for future work**:

- **5** — [Plasma cutting](lessons/plasma/) (requires mechanical fabrication first)

The full status view is in [ROADMAP.md](ROADMAP.md); the session journal at [lessons/JOURNAL.md](lessons/JOURNAL.md) captures decisions and progress history.

---

## Adding a new example

```
mkdir examples/my_new_part
cp examples/hole_in_sheet/hole_in_sheet.scad examples/my_new_part/my_new_part.scad
cp examples/hole_in_sheet/job.yaml examples/my_new_part/job.yaml
```

Edit the `.scad` for the geometry you want. Update `job.yaml` to match the material thickness and tool you'll use. Then `python cnc.py build my_new_part` to verify the geometry exports cleanly. Open `my_new_part.FCStd` (you'll create it on first run) in FreeCAD to set up the CAM job.

---

## Customizing for a different machine

The repo is designed so that swapping to a different GRBL router means editing one file. Copy `profiles/anolex_4030_evo_ultra2.yaml` to `profiles/<your_machine>.yaml`, update the envelope, feeds, and spindle range, then either:

- Edit `cnc.py` and `scripts/gcode_validate.py` to point at the new file (one constant in each), or
- Set the `PROFILE` env var: `$env:PROFILE = "profiles/<your_machine>.yaml"`.

The principle (machine as configuration, not hardcoded) is discussed in `cnc_for_the_scad.md` §4.

---

## When something goes wrong

| Symptom | Likely cause | Where to look |
|---|---|---|
| `cnc.py doctor` shows `openscad: MISSING` | OpenSCAD not installed, or in a non-standard directory | `winget install OpenSCAD.OpenSCAD`, or set `$env:OPENSCAD` |
| `cnc.py params` says "no chipload entry" | The job's material doesn't have a chipload value for the job's tool | Add the pair to `profiles/materials.yaml` |
| `cnc.py params` exits with a failed safety check | Spindle RPM or feed exceeds machine/tool limits | Lower `spindle_rpm` in `job.yaml`, or use a different tool |
| `cnc.py validate` flags `bounds` | The GCode would drive the spindle outside the machine envelope | Check the WCS origin in the FreeCAD Job — it usually means stock placement is off |
| `cnc.py validate` flags `spindle_on` | The grbl post-processor isn't emitting M3 commands | In FreeCAD, Job-Edit → Output tab → enable spindle output |
| `cnc.py validate` flags `safe_z_rapid` | A rapid (G0) traverses XY below the configured safe Z | Raise "Safe Height" in the Profile op, or lower `default_safe_z_mm` in the machine profile |
| Preflight refuses to start | Safety check failed before the checklist | Fix the params issue first, then re-run preflight |

For everything else, the conceptual guide (`cnc_for_the_scad.md`) explains the why behind each tool and decision.
