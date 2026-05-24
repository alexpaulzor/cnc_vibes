"""Reference text for `cnc.py help`.

Each topic is a (title, body) tuple in TOPICS. CATEGORIES groups topic
names for the index page. To add a topic, append it to TOPICS and to a
category in CATEGORIES; the help command and its tests pick it up.

The PREFLIGHT_CHECKLIST and validator rules are rendered dynamically
from their source modules so they don't drift from the actual behavior.
"""

from __future__ import annotations

from job_params import LASER_PREFLIGHT_CHECKLIST, PREFLIGHT_CHECKLIST


# Topic name (stable, kebab-case) -> (one-line title, body)
TOPICS: dict[str, tuple[str, str]] = {
    "topics": (
        "Help topics index",
        # Body rendered dynamically in render_topic("topics"); kept here so the
        # topic is discoverable via search and listing.
        "",
    ),
    # ---- Subcommands ----
    "build": (
        "cnc.py build — OpenSCAD → CSG (or STL)",
        """
Usage:  cnc.py build <example> [--format csg|stl]

Regenerates geometry from an example's .scad source.

Inputs:
  examples/<name>/<name>.scad

Outputs (default: csg only):
  examples/<name>/build/<name>.csg   (OpenSCAD CSG tree, the CAM-feeding
                                      artifact — FreeCAD's OpenSCAD
                                      workbench imports this as a real
                                      B-rep Solid with selectable faces
                                      and edges)
  examples/<name>/build/<name>.stl   (optional, with --format stl —
                                      handy for slicer preview or 3D
                                      printing the part)

CSG is preferred over DXF/STL because the same import works for both
2.5D (pick top-face edges) and 3D contour (use the solid as the model),
eliminating the historical 2D-vs-3D export fork.

Options:
  --format csg    only produce the CSG (default behavior anyway)
  --format stl    only produce the STL

Environment:
  OPENSCAD        path to openscad executable (default: auto-detect)

Examples:
  cnc.py build hole_in_sheet
  cnc.py build hole_in_sheet --format stl

See also: doctor, workflow, pipeline
""",
    ),
    "validate": (
        "cnc.py validate — GCode safety checks",
        """
Usage:  cnc.py validate <gcode-path>

Runs the machine-aware GCode validator against the given file.
Aborts with exit code 1 on any rule violation.

Checks performed:
  - bounds         coordinates within machine envelope
  - max_feed       F values within machine max feed
  - max_plunge     pure-Z-down moves within tool max_plunge
  - safe_z_rapid   no G0 traversing XY below safe Z
  - spindle_on     spindle running with S > 0 before first cut

See `cnc.py help validator-rules` for the rule details and
configuration sources.

Exit codes: 0 = clean, 1 = violations, 2 = bad inputs.

Examples:
  cnc.py validate examples/hole_in_sheet/build/hole_in_sheet.gcode

See also: validator-rules, machine-profile, tools
""",
    ),
    "params": (
        "cnc.py params — show lookup tables and derived numbers",
        """
Usage:  cnc.py params <example>

Reads examples/<name>/job.yaml and joins it with the machine, tool, and
material profiles. Prints the lookup values, the derived parameters
(feed rate, depths of cut, through-cut depth, pass count) with the
formula spelled out, and the pass/fail safety checks.

Exits non-zero if any safety check fails.

Derived parameters:
  feed (XY)         = chipload × flutes × spindle_rpm
  DOC roughing      = doc_fraction × tool.diameter_mm
  DOC finishing     = doc_fraction_finish × tool.diameter_mm
  through-cut depth = -material.thickness_mm - 0.2 (spoilboard overcut)
  passes            = ceil(|through-cut| / DOC roughing)

Safety checks:
  - spindle RPM within machine range
  - spindle RPM within tool max
  - feed within machine XY max
  - plunge feed within machine Z max

Examples:
  cnc.py params hole_in_sheet

See also: preflight, job, machine-profile, materials, tools
""",
    ),
    "preflight": (
        "cnc.py preflight — params + interactive safety checklist",
        """
Usage:  cnc.py preflight <example> [--print-only]

Prints params (see `cnc.py help params`), then walks an interactive
pre-cut safety checklist. Aborts non-zero if any safety check fails
upfront, or if any checklist item is not confirmed during the walk.

Options:
  --print-only    skip the prompts; just print the checklist

Answers during the walk:
  y, yes          confirmed
  n, no, anything else   marked NOT CONFIRMED
  q               quit immediately

See `cnc.py help checklist` for the full checklist content.

Examples:
  cnc.py preflight hole_in_sheet
  cnc.py preflight hole_in_sheet --print-only

See also: checklist, params, validate
""",
    ),
    "doctor": (
        "cnc.py doctor — show resolved toolchain",
        """
Usage:  cnc.py doctor

Prints the platform, Python version + path, and the resolved paths to
openscad and FreeCADCmd (or MISSING if not found). Also reports the
installed versions of pyyaml and pytest.

If a tool shows MISSING:
  openscad        winget install OpenSCAD.OpenSCAD (Windows)
                  brew install --cask openscad (macOS)
                  or set OPENSCAD env var
  FreeCADCmd      winget install FreeCAD.FreeCAD (Windows)
                  brew install --cask freecad (macOS)
                  or set FREECAD_CMD env var
  pyyaml/pytest   python -m pip install -r requirements.txt

Examples:
  cnc.py doctor
""",
    ),
    "test": (
        "cnc.py test — run pytest suite",
        """
Usage:  cnc.py test

Runs the full pytest suite under tests/. Takes no arguments. Wraps
`python -m pytest -q tests/` for convenience and cross-platform
consistency.

Examples:
  cnc.py test
""",
    ),
    "clean": (
        "cnc.py clean — remove build artifacts",
        """
Usage:  cnc.py clean

Deletes every examples/*/build/ directory. These hold generated CSG /
STL / GCode and are gitignored; this command just clears local cache
when you want a fully fresh build.

Examples:
  cnc.py clean
""",
    ),
    "post": (
        "cnc.py post — FreeCAD post-process (not implemented)",
        """
Usage:  cnc.py post <fcstd> <gcode>

NOT YET IMPLEMENTED. Will eventually use FreeCADCmd to open a saved
.FCStd CAM project, run its post-processor, and write the result to
the given .gcode path — so GCode regeneration becomes a cnc.py command
rather than a GUI ritual.

For now, post-process from inside the FreeCAD GUI:
  Right-click the Job in the tree → Post Process
  Save to examples/<name>/build/<name>.gcode
""",
    ),
    "help": (
        "cnc.py help — this command",
        """
Usage:  cnc.py help [topic] [--search KEYWORD]

Browse the toolchain reference, manpage-style.

  cnc.py help                  show the topic index (this is what you
                               see now)
  cnc.py help <topic>          show detailed help on a topic
  cnc.py help --search foo     list topics whose title or body mentions
                               'foo' (case-insensitive)

For argparse-style usage of any subcommand, `cnc.py <subcommand> --help`
also works (e.g. `cnc.py preflight --help`).

Examples:
  cnc.py help
  cnc.py help preflight
  cnc.py help --search chipload
""",
    ),
    # ---- Configuration ----
    "machine-profile": (
        "profiles/anolex_4030_evo_ultra2.yaml — machine profile",
        """
Defines the GRBL-class machine's envelope, feed limits, spindle range,
and probe. Consumed by validate, params, and preflight to enforce
safety checks.

Required keys:
  name                        string, human-readable
  controller.dialect          "grbl-1.1+"
  envelope_mm.{x,y,z}         machine travel in mm
  max_feed_mm_per_min.{xy,z}  per-axis max feed (mm/min)
  spindle.{rpm_min,rpm_max}   spindle speed range
  default_safe_z_mm           rapid-traverse height above stock (mm)

To target a different machine, copy this file and edit the values.
See cnc_for_the_scad.md §4 for the parameterization principle.

See also: tools, materials, job
""",
    ),
    "tools": (
        "profiles/tools.yaml — tool table",
        """
List of available endmills, ball-ends, and V-bits with their geometric
and operational limits.

Per-tool fields:
  id                       stable string id (referenced from job.yaml
                           and from materials.yaml chipload entries)
  type                     "flat_endmill" | "ball_endmill" | "v_bit"
  diameter_mm              tool diameter in mm
  flutes                   number of cutting edges
  max_rpm                  do not spin faster than this
  max_plunge_mm_per_min    do not plunge faster than this
  flute_length_mm          (optional) length of cutting region
  shank_mm                 (optional) shank diameter

Add new tools by appending entries. Tool ids are stable contracts —
materials.yaml chipload tables and job.yaml files refer to them.

See also: materials, job, machine-profile
""",
    ),
    "materials": (
        "profiles/materials.yaml — material chipload + DOC tables",
        """
List of stock materials with chipload tables per tool and recommended
depth-of-cut fractions.

Per-material fields:
  id                       stable string id (referenced from job.yaml)
  family                   "wood" | "aluminum" | "plastic" | ...
  thickness_mm             sheet thickness
  chipload                 dict mapping tool id -> mm/tooth chipload
  doc_fraction             recommended DOC as fraction of tool diameter
  doc_fraction_finish      (optional) finishing-pass DOC fraction
  notes                    (optional) free-form text

Derived numbers from these values:
  feed = chipload × flutes × rpm
  DOC  = doc_fraction × tool.diameter_mm

These are starting points, not gospel. Measure chips, listen, adjust.

See also: tools, job
""",
    ),
    "job": (
        "examples/<name>/job.yaml — per-example job spec",
        """
Per-example configuration tying the example to its material, tool,
and spindle speed. Read by params and preflight.

Required keys:
  material      id from profiles/materials.yaml
  tool          id from profiles/tools.yaml
  spindle_rpm   integer rpm within machine and tool RPM ranges
  gcode         path (relative to repo root) to the expected .gcode

Example:
  material: plywood_baltic_birch_3mm
  tool: flat_3.175mm_2flute
  spindle_rpm: 18000
  gcode: examples/hole_in_sheet/build/hole_in_sheet.gcode

Changing spindle_rpm changes the derived feed. Changing material
swaps to a different chipload table. The .FCStd CAM project is the
source of truth for what FreeCAD emits; job.yaml is what cnc.py uses
to tell you what the GCode *should* match.

See also: materials, tools, params, preflight
""",
    ),
    # ---- Concepts (pointers into the guide) ----
    "concepts": (
        "concepts — what CAM is, vocabulary, why it's not slicing",
        """
The conceptual material lives in cnc_for_the_scad.md.

Key sections to read:
  §1  The shift you're making (CAM vs slicing)
  §2  Industry vocabulary (stock, WCS, chipload, DOC, climb vs
      conventional, operations, tabs, post-processor, ...)
  §3  Pipeline diagram with branches for spindle/laser/FDM
  §4  Machine-as-profile principle
  §5  CAM tool choice + FreeCAD object model class diagram
  §6  Worked example with FreeCAD click-through

See also: pipeline, freecad, workflow
""",
    ),
    "pipeline": (
        "pipeline — the .scad-to-finished-part flow",
        """
The high-level pipeline (full mermaid diagram in cnc_for_the_scad.md §3):

  .scad source
    -> openscad CLI               -> CSG (text CSG tree)
    -> FreeCAD OpenSCAD workbench -> imports as real B-rep Solid
    -> FreeCAD CAM (GUI)          -> .FCStd CAM project
    -> FreeCAD post-processor     -> .gcode (GRBL dialect)
    -> cnc.py validate            (machine + tool safety)
    -> cnc.py preflight           (params + interactive checklist)
    -> sender (gSender)           -> machine -> finished part

Configuration (profiles/*.yaml + job.yaml) feeds CAM, validate,
params, and preflight at multiple stages.

See also: concepts, freecad, workflow, build, validate, preflight
""",
    ),
    "freecad": (
        "freecad — CAM workbench and object model",
        """
FreeCAD CAM workbench is the only step in the pipeline that requires
a GUI. The detailed reasoning, object model class diagram, and a
step-by-step click-through live in cnc_for_the_scad.md:

  §5    Why FreeCAD CAM was chosen
  §5.1  Class diagram of the relevant FreeCAD types (Document, Job,
        Stock, ToolController, Operation, Profile, TabsDressup, ...)
  §6    Worked example with full click-through (collapsible block)

Tool: open `<name>.FCStd` from the example directory. Workbench
selector → CAM (top-left).

See also: concepts, workflow
""",
    ),
    "workflow": (
        "workflow — per-job 7-step procedure",
        """
Per-job workflow (full version in README.md):

  1. Edit examples/<name>/<name>.scad
  2. cnc.py build <name>
  3. (first time only) Open <name>.FCStd in FreeCAD, set up CAM job
  4. Right-click Job in FreeCAD → Post Process → save build/<name>.gcode
  5. cnc.py validate examples/<name>/build/<name>.gcode
  6. cnc.py preflight <name>
  7. Load .gcode in sender (gSender), cut

After parameter tweaks (step 1), the loop shortens to: cnc.py build →
reopen .FCStd (geometry refreshes via "From Base shape" stock) →
right-click Job → Post Process → validate → preflight → cut.

See also: pipeline, build, validate, preflight
""",
    ),
    # ---- Reference (dynamic content) ----
    "checklist": (
        "checklist — full preflight safety checklist",
        "",  # rendered dynamically from PREFLIGHT_CHECKLIST
    ),
    "laser-checklist": (
        "laser-checklist — full pre-burn safety checklist",
        "",  # rendered dynamically from LASER_PREFLIGHT_CHECKLIST
    ),
    "laser-materials": (
        "profiles/laser_materials.yaml — per-material laser params",
        """
Per-material laser settings for diode-laser cutting/engraving. Read by
the lesson scripts (e.g. lessons/laser/01_spacer/spacer.py) to translate
material id into power/feed/passes.

Per-material fields:
  id                  stable string id
  family              "wood" | "acrylic" | "paper" | ...
  thickness_mm        sheet thickness
  laser:
    power_percent     S value as percent of 1000
    feed_mm_per_min   cut feedrate (M4 dynamic mode scales power with speed)
    passes            cut-through passes
    notes             optional free-form safety/quality notes

Values are STARTING POINTS for a 10W diode laser. Lesson 3b
(calibration) refines them empirically per machine.

NEVER laser-cut: PVC, polycarbonate, ABS, vinyl, anything chlorinated,
galvanized metal. The file's tail comment lists these.

See also: lesson-spacer, laser-checklist
""",
    ),
    "lesson-spacer": (
        "lesson 3a — parametric laser-cut PCB spacer",
        """
Location: lessons/laser/01_spacer/

First fully-automated toolchain in the repo. No FreeCAD, no CAM project.
Pure Python: parameters in -> GRBL laser-mode GCode out -> validator
passes -> cut. Demonstrates the CAM-as-code pattern for simple
parametric parts.

Usage:
  python lessons/laser/01_spacer/spacer.py \\
      --od 6.0 --id 3.2 \\
      --material plywood_baltic_birch_3mm \\
      --out lessons/laser/01_spacer/build/spacer.gcode

Defaults:
  --od         6.0 mm (small PCB-standoff footprint)
  --id         3.2 mm (M3 screw clearance)
  --material   plywood_baltic_birch_3mm
  --out        build/spacer_<od>_<id>.gcode under the lesson dir

Notes:
  * Toolpath dimensions are NOT kerf-compensated. Finished hole is
    ~kerf-width larger than --id; finished OD is ~kerf-width smaller
    than --od. Add the kerf to --id and subtract from --od if a precise
    fit matters.
  * Output GCode uses M4 dynamic power and $32=1 laser mode.
  * Validate with `cnc.py validate <gcode>` and walk
    `cnc.py preflight <gcode>` (auto-detects head=laser).

See also: laser-materials, laser-checklist, validate, preflight,
          lesson-calibration
""",
    ),
    "lesson-calibration": (
        "lesson 3b — laser calibration pattern",
        """
Location: lessons/laser/02_calibration/

Generates a labeled matrix of small cut-through test squares at varying
(power, passes, feed) combinations. After burning, you inspect which
cells cut through cleanly and write the calibrated numbers back into
profiles/laser_materials.yaml.

Usage:
  python lessons/laser/02_calibration/calibration.py \\
      --material plywood_baltic_birch_3mm \\
      --max-passes 5 \\
      --powers 100,75,50,25 \\
      --speeds 200,400,600

Defaults:
  --max-passes          5
  --powers              100,75,50,25
  --speeds              (empty - use material's default feed)
  --cell-pitch          18.0 mm (cut square is 8mm centered)
  --label-digit-height  5.0 mm

Layout:
  One panel per speed, stacked vertically.
  Each panel: row labels = power %, col labels = pass count.
  Each cell: 8mm square cut N times at the configured power and feed.
  Panel headers show the feed rate.

Notes:
  * Default (1 speed) fits comfortably on a 300 mm Y bed. Practical
    limit is ~3 speeds before stacking exceeds the envelope; the
    validator's bounds rule catches overflow.
  * Glyphs supported: digits 0-9 only (no letters needed - context
    tells you which axis is which).
  * Uses M4 dynamic power. Absolute power numbers are upper bounds;
    cell-to-cell relative comparison is honest.

See also: laser-materials, laser-checklist, validate, preflight,
          lesson-spacer
""",
    ),
    "lesson-mill-spacer": (
        "lesson 4a — parametric router-cut spacer",
        """
Location: lessons/mill/01_spacer/

First spindle-side lesson. Hybrid toolchain: if the geometry is a
plain cylindrical washer (all four diameters equal) the script emits
GCode directly. If any face has different OD/ID than the opposite
face (frustum), it generates the .scad/.csg and hands off to FreeCAD
CAM.

Usage:
  # Cylindrical (fully automated)
  python lessons/mill/01_spacer/mill_spacer.py \\
      --height 6 --od 8 --id 3.2

  # Frustum (FreeCAD CAM handoff)
  python lessons/mill/01_spacer/mill_spacer.py \\
      --height 12 --top-od 10 --bottom-od 14 \\
      --top-id 3.2 --bottom-id 3.2

Defaults:
  --height       6.0 mm
  --od           8.0 mm (sets top + bottom unless overridden)
  --id           3.2 mm (M3 clearance; sets top + bottom)
  --material     plywood_baltic_birch_6mm
  --tool         flat_3.175mm_2flute
  --spindle-rpm  18000

Hole strategy is auto-picked:
  * helical bore when id > 2.5 x tool_diameter
  * peck drill otherwise
  * errors out if id < tool_diameter (pick a smaller tool)

Known limitations:
  * Tabs not yet implemented - part releases on the final pass.
    Hand-add M0 pause or clamp from below.
  * Tool change not handled - run twice for different hole + perimeter
    tools and combine manually.

See also: machine-profile, tools, materials, validate, preflight,
          checklist, lesson-spacer
""",
    ),
    "lesson-center-punch": (
        "lesson 4c — steel center-punch divets",
        """
Location: lessons/mill/02_steel_center_punch/

Fully automated. Generates GCode that plunges the spindle to a small
depth (default 0.4 mm) at a list of (x, y) points. Use an engraver /
V-bit to make precisely-located marks in mild steel for follow-up
drilling.

This script does NOT cut steel - the 500W spindle on this class of
router is too underpowered. It only deforms the surface enough to
register a drill bit.

Three ways to specify points:
  --points "x1,y1,x2,y2,..."     inline CSV (typed by hand)
  --points-file my_holes.yaml     YAML list of [x, y] pairs
  --grid 5x4 --pitch 12 \\
         --origin 10,10           parametric grid

Other flags:
  --depth         (default 0.4 mm; capped at 2.0 mm)
  --plunge-feed   (default 80 mm/min - slow for hard material)
  --tool          (default vbit_60deg_6mm)
  --spindle-rpm   (default 12000)

Validation gates in the script:
  * depth must be > 0 and <= 2.0 mm
  * spindle_rpm <= tool max_rpm
  * plunge_feed <= tool max_plunge_mm_per_min
  * every point in the machine envelope

See also: tools, machine-profile, validate, preflight, checklist
""",
    ),
    "lesson-aluminum-slot": (
        "lesson 4d — aluminum trochoidal slot",
        """
Location: lessons/mill/03_aluminum/

Generates GCode for a single straight slot in aluminum using trochoidal
(low-engagement) motion. The tool moves in tight circles while
advancing along the slot, keeping cutting forces low enough for the
500W spindle to handle.

For plain cylindrical aluminum spacers, use lesson 4a directly with
--material aluminum_6061_3mm; the material profile gives safe feeds.

Usage:
  python lessons/mill/03_aluminum/trochoidal_slot.py \\
      --x0 10 --y0 10 \\
      --length 30 --width 6 \\
      --depth 3

Flags:
  --x0, --y0          slot start (required)
  --length, --width   slot dimensions (required; width > tool diameter)
  --depth             negative-Z reached (required)
  --tool              default flat_3.175mm_2flute
  --material          default aluminum_6061_3mm
  --spindle-rpm       default 18000
  --trochoidal-radius-frac  default 0.4 (loop r as fraction of tool dia)
  --trochoidal-step-frac    default 0.15 (per-loop X advance as fraction)

Validation:
  * width > tool_diameter (else use a profile cut)
  * length > tool_dia + 2 * loop_radius (else slot too short)
  * spindle_rpm <= tool max_rpm
  * dimensions all > 0

Critical operator responsibility:
  * Apply WD-40 / kerosene every 30-60 seconds during the cut.
    Aluminum chips fuse to the tool without lubrication.
  * Watch for chatter; hit e-stop if you hear high-pitched squealing.

See also: tools, materials, machine-profile, validate, preflight,
          lesson-mill-spacer
""",
    ),
    "validator-rules": (
        "validator-rules — every rule gcode_validate enforces",
        """
The validator (scripts/gcode_validate.py) parses GCode line-by-line
and emits one violation per rule per offending line.

  bounds        coord magnitude exceeds envelope on any axis
                (|x| > envelope.x, etc.) — machine-coord-agnostic rule
                that catches "200mm Z plunge on a 100mm-Z machine" but
                permits positive Z safe-traverse moves.

  max_feed      F value on a G1/G2/G3 exceeds the machine's per-axis
                max feed. XY moves use machine.max_feed_mm_per_min.xy;
                pure-Z moves use .z.

  max_plunge    pure-Z-down G1 with F exceeding the declared tool's
                max_plunge_mm_per_min. Skipped if no tool is declared
                via ";TOOL: <id>" comment in the GCode.

  safe_z_rapid  G0 (rapid) with XY change while Z is below
                machine.default_safe_z_mm — would crash through stock
                or clamps.

  spindle_on    first G1/G2/G3 move below safe_z arrives without an
                M3 with S > 0 — would cut with the spindle off.

Configuration sources:
  --profile     machine profile YAML (default: anolex_4030_evo_ultra2)
  --tools       tools.yaml
  --gcode       the GCode file to check
  ;TOOL: <id>   in-band tool declaration (optional, enables max_plunge)

See also: validate, machine-profile, tools
""",
    ),
    "failures": (
        "failures — common error messages and what they mean",
        """
Symptom                                        Cause and fix
-------                                        -------------
doctor: openscad MISSING                       Not installed or off
                                               PATH. Install via
                                               winget/brew or set
                                               OPENSCAD env var.

params: "no chipload entry"                    The job's material has
                                               no chipload for the
                                               job's tool. Add the
                                               pair to materials.yaml.

params: safety check fails                     Spindle RPM or derived
                                               feed exceeds a limit.
                                               Lower spindle_rpm in
                                               job.yaml or pick a
                                               different tool.

validate: "bounds"                             GCode would drive the
                                               spindle outside the
                                               machine envelope. Check
                                               WCS origin in FreeCAD;
                                               stock placement is
                                               usually off.

validate: "spindle_on"                         FreeCAD's grbl post
                                               isn't emitting M3.
                                               Job-Edit → Output tab
                                               → enable spindle
                                               output.

validate: "safe_z_rapid"                       A G0 traverses XY below
                                               default_safe_z_mm.
                                               Raise the Profile op's
                                               Safe Height, or lower
                                               default_safe_z_mm in
                                               the machine profile.

preflight refuses to start                     A safety check failed
                                               before the checklist.
                                               Fix the params issue
                                               first.

See also: doctor, params, validate, preflight
""",
    ),
}


CATEGORIES: dict[str, list[str]] = {
    "Subcommands": [
        "build",
        "validate",
        "params",
        "preflight",
        "doctor",
        "test",
        "clean",
        "post",
        "help",
    ],
    "Configuration": [
        "machine-profile",
        "tools",
        "materials",
        "laser-materials",
        "job",
    ],
    "Concepts": ["concepts", "pipeline", "freecad", "workflow"],
    "Reference": ["checklist", "laser-checklist", "validator-rules", "failures"],
    "Lessons": [
        "lesson-spacer",
        "lesson-calibration",
        "lesson-mill-spacer",
        "lesson-center-punch",
        "lesson-aluminum-slot",
    ],
}


def _render_checklist_body(
    checklist: list[tuple[str, str]],
    intro: str,
    placeholders_note: str,
    see_also: str = "preflight",
) -> str:
    lines = [intro, ""]
    for i, (key, prompt) in enumerate(checklist, start=1):
        # Strip the format placeholders for the static help view; show the
        # template literally so the user knows what to expect.
        lines.append(f"  {i:2d}. [{key}] {prompt}")
    lines += [
        "",
        placeholders_note,
        "",
        f"See also: {see_also}",
    ]
    return "\n".join(lines)


def _render_index() -> str:
    lines = [
        "cnc — task runner for the cnc_vibes pipeline",
        "",
        "Usage: cnc.py help <topic>   for detailed help on a topic",
        "       cnc.py help --search KEYWORD",
        "",
    ]
    for category, names in CATEGORIES.items():
        lines.append(f"{category}:")
        for name in names:
            title = TOPICS[name][0]
            # Drop the "cnc.py X — " prefix for a tighter index, if present.
            short = title.split(" — ", 1)[-1] if " — " in title else title
            lines.append(f"  {name:<18} {short}")
        lines.append("")
    return "\n".join(lines).rstrip()


def render_topic(name: str) -> str:
    """Render a single topic (full title + body). Raises KeyError if unknown."""
    if name == "topics":
        return _render_index()
    if name == "checklist":
        title, _ = TOPICS["checklist"]
        body = _render_checklist_body(
            PREFLIGHT_CHECKLIST,
            "The interactive `cnc.py preflight <example>` walks these items"
            " in order. Each requires y/yes to confirm.",
            "Placeholders like {tool_id} and {gcode} are filled in at runtime"
            " from the job's tool and gcode path.",
            see_also="preflight, laser-checklist",
        )
        return f"{title}\n{'=' * len(title)}\n\n{body}"
    if name == "laser-checklist":
        title, _ = TOPICS["laser-checklist"]
        body = _render_checklist_body(
            LASER_PREFLIGHT_CHECKLIST,
            "The interactive `cnc.py preflight <gcode>` walks these items"
            " when the GCode contains `;HEAD: laser`. Each requires y/yes"
            " to confirm.",
            "Placeholders like {material} and {gcode} are filled in at runtime"
            " from the GCode file's header comments.",
            see_also="preflight, checklist, laser-materials",
        )
        return f"{title}\n{'=' * len(title)}\n\n{body}"
    if name not in TOPICS:
        raise KeyError(name)
    title, body = TOPICS[name]
    return f"{title}\n{'=' * len(title)}\n{body.rstrip()}"


def render_index() -> str:
    """Render the topic index page."""
    return _render_index()


def search(keyword: str) -> list[str]:
    """Return topic names whose title or body contains the keyword (ci)."""
    needle = keyword.lower()
    hits = []
    for name, (title, body) in TOPICS.items():
        haystack = (title + " " + body).lower()
        if needle in haystack or needle in name.lower():
            hits.append(name)
    # Special-case the dynamically-rendered checklist topics so search
    # can find content that only exists at render time.
    for topic_name, checklist in (
        ("checklist", PREFLIGHT_CHECKLIST),
        ("laser-checklist", LASER_PREFLIGHT_CHECKLIST),
    ):
        rendered = " ".join(prompt for _, prompt in checklist).lower()
        if needle in rendered and topic_name not in hits:
            hits.append(topic_name)
    return sorted(hits)
