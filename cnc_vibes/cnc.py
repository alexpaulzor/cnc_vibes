#!/usr/bin/env python3
"""cnc — task runner for the cnc_vibes pipeline.

Cross-platform replacement for a Makefile. Same entry point on Windows 11,
macOS, and Linux. Run `python cnc.py --help` for the list of subcommands.

Environment overrides:
  OPENSCAD     path to openscad executable (default: auto-detect)
  FREECAD_CMD  path to FreeCADCmd executable (default: auto-detect)
  PROFILE      machine profile YAML (default: profiles/anolex_4030_evo_ultra2.yaml)
  TOOLS        tool table YAML       (default: profiles/tools.yaml)
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXAMPLES = ROOT / "examples"
PROFILES = ROOT / "profiles"

sys.path.insert(0, str(ROOT / "scripts"))
from gcode_validate import detect_head  # noqa: E402
from help_topics import render_index, render_topic, search  # noqa: E402
from job_params import (  # noqa: E402
    LASER_PREFLIGHT_CHECKLIST,
    PREFLIGHT_CHECKLIST,
    compute_derived,
    find_by_id,
    format_report,
    laser_job_from_yaml,
    load_job,
    load_job_yaml,
    load_yaml,
)


def _find_executable(name: str, env_var: str, fallbacks: list[str]) -> str | None:
    if env_path := os.environ.get(env_var):
        return env_path
    if found := shutil.which(name):
        return found
    for fb in fallbacks:
        if Path(fb).exists():
            return fb
    return None


def openscad_path() -> str | None:
    return _find_executable(
        "openscad",
        "OPENSCAD",
        [
            r"C:\Program Files\OpenSCAD\openscad.exe",
            r"C:\Program Files (x86)\OpenSCAD\openscad.exe",
            "/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD",
        ],
    )


def freecad_cmd_path() -> str | None:
    return _find_executable(
        "FreeCADCmd",
        "FREECAD_CMD",
        [
            r"C:\Program Files\FreeCAD 1.0\bin\FreeCADCmd.exe",
            r"C:\Program Files\FreeCAD\bin\FreeCADCmd.exe",
            "/Applications/FreeCAD.app/Contents/MacOS/FreeCADCmd",
        ],
    )


def cmd_build(args: argparse.Namespace) -> None:
    name = args.name
    example_dir = EXAMPLES / name
    src = example_dir / f"{name}.scad"
    if not src.exists():
        sys.exit(f"error: no SCAD source at {src}")

    scad = openscad_path()
    if not scad:
        sys.exit(
            "error: openscad not found. Install OpenSCAD or set OPENSCAD env var.\n"
            "  Windows: winget install OpenSCAD.OpenSCAD\n"
            "  macOS:   brew install --cask openscad"
        )

    build_dir = example_dir / "build"
    build_dir.mkdir(exist_ok=True)

    # CSG is the primary intermediate: it preserves the OpenSCAD CSG tree,
    # which FreeCAD's OpenSCAD workbench re-builds into a real B-rep solid
    # (faces and edges selectable by name). STL stays available for visual
    # QC / slicer preview but is no longer the CAM-feeding artifact.
    formats = [args.format] if args.format else ["csg"]
    for fmt in formats:
        out = build_dir / f"{name}.{fmt}"
        # Pass absolute paths to dodge OpenSCAD's relative-path quirks
        # on macOS. The mode=-D variable is only meaningful for legacy
        # .scad files that branch on it; CSG/STL exports of the default
        # branch both produce the 3D solid.
        cmd = [scad, "-o", str(out.resolve()), str(src.resolve())]
        print("->", " ".join(cmd))
        subprocess.run(cmd, check=True)


def cmd_validate(args: argparse.Namespace) -> None:
    gcode = Path(args.gcode)
    if not gcode.exists():
        sys.exit(f"error: gcode not found: {gcode}")
    profile = os.environ.get(
        "PROFILE", str(ROOT / "profiles" / "anolex_4030_evo_ultra2.yaml")
    )
    tools = os.environ.get("TOOLS", str(ROOT / "profiles" / "tools.yaml"))
    rc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "gcode_validate.py"),
            "--profile",
            profile,
            "--tools",
            tools,
            "--gcode",
            str(gcode),
        ]
    ).returncode
    sys.exit(rc)


def cmd_test(args: argparse.Namespace) -> None:
    rc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            str(ROOT / "tests"),
            str(ROOT / "lessons"),
        ]
    ).returncode
    sys.exit(rc)


def cmd_clean(args: argparse.Namespace) -> None:
    for build_dir in EXAMPLES.glob("*/build"):
        print(f"→ removing {build_dir}")
        shutil.rmtree(build_dir)


def cmd_post(args: argparse.Namespace) -> None:
    # Placeholder for the FreeCAD CLI post-process flow. Wiring this up
    # requires a real .FCStd to drive — see cnc_for_the_scad.md §8.
    if not freecad_cmd_path():
        sys.exit(
            "error: FreeCADCmd not found. Install FreeCAD or set FREECAD_CMD env var.\n"
            "  Windows: winget install FreeCAD.FreeCAD\n"
            "  macOS:   brew install --cask freecad"
        )
    sys.exit(
        "error: `cnc post` is not implemented yet.\n"
        "For now, post via FreeCAD GUI: right-click the Job → Post Process,\n"
        "saving to examples/<name>/build/<name>.gcode."
    )


def cmd_doctor(args: argparse.Namespace) -> None:
    print(f"platform:    {platform.system()} {platform.release()}")
    print(f"python:      {sys.version.split()[0]}  ({sys.executable})")
    print(f"openscad:    {openscad_path() or 'MISSING (required for `build`)'}")
    print(f"FreeCADCmd:  {freecad_cmd_path() or 'missing (only needed for `post`)'}")
    try:
        import yaml

        print(f"pyyaml:      {yaml.__version__}")
    except ImportError:
        print("pyyaml:      MISSING — run `python -m pip install -r requirements.txt`")
    try:
        import pytest

        print(f"pytest:      {pytest.__version__}")
    except ImportError:
        print("pytest:      MISSING — run `python -m pip install -r requirements.txt`")


def _load_job_context(name: str):
    """Load job spec + machine + tool + material for a given example name."""
    job_dir = EXAMPLES / name
    job = load_job(job_dir)
    machine = load_yaml(PROFILES / "anolex_4030_evo_ultra2.yaml")
    tools = load_yaml(PROFILES / "tools.yaml")
    materials = load_yaml(PROFILES / "materials.yaml")
    tool = find_by_id(tools, job.tool, "tool")
    material = find_by_id(materials, job.material, "material")
    derived = compute_derived(machine, material, tool, job.spindle_rpm)
    return job, machine, material, tool, derived


def cmd_params(args: argparse.Namespace) -> None:
    job, machine, material, tool, derived = _load_job_context(args.name)
    print(format_report(job, machine, material, tool, derived))
    # Non-zero exit if any safety check failed, so this fits in CI/scripts.
    if any(not c["ok"] for c in derived["checks"]):
        sys.exit(1)


def _walk_checklist(
    checklist: list[tuple[str, str]],
    bindings: dict,
    print_only: bool,
    arg_for_rerun: str,
) -> None:
    """Walk a checklist either interactively or in print-only mode.

    `bindings` is the dict passed to .format() for each prompt template.
    Exits non-zero if any item is unconfirmed during an interactive walk.
    """
    if print_only:
        for _, prompt_tpl in checklist:
            print(f"  [ ] {prompt_tpl.format(**bindings)}")
        print("\n(--print-only: not interactive. Tick boxes mentally.)")
        return

    failed = []
    for key, prompt_tpl in checklist:
        prompt = prompt_tpl.format(**bindings)
        try:
            ans = input(f"  {prompt}  [y/n/q]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            sys.exit("\nABORT: preflight interrupted.")
        if ans == "q":
            sys.exit("\nABORT: preflight quit by user.")
        if ans not in ("y", "yes"):
            failed.append(key)
            print("      -> NOT CONFIRMED")
        else:
            print("      -> ok")

    print()
    if failed:
        sys.exit(
            f"ABORT: {len(failed)} item(s) not confirmed: {', '.join(failed)}.\n"
            f"Resolve each one and re-run `cnc.py preflight {arg_for_rerun}`."
        )
    print("All preflight items confirmed. Cleared to start the cut.")


def _looks_like_yaml_path(s: str) -> bool:
    """Heuristic: ends in .yaml/.yml AND points to an existing file."""
    p = Path(s)
    return p.suffix in (".yaml", ".yml") and p.exists() and p.is_file()


def _looks_like_gcode_path(s: str) -> bool:
    """Heuristic: if it ends in .gcode/.nc/.cnc OR is an existing file path."""
    if s.endswith((".gcode", ".nc", ".cnc", ".tap")):
        return True
    return Path(s).exists() and Path(s).is_file()


def cmd_preflight(args: argparse.Namespace) -> None:
    # Three modes: standalone job.yaml file (head: laser|spindle), raw
    # gcode path (head detected from ;HEAD: marker), or example-name
    # (directory under examples/, spindle, dir-based job.yaml).
    if _looks_like_yaml_path(args.name):
        _preflight_from_yaml(Path(args.name), args.print_only)
    elif _looks_like_gcode_path(args.name):
        _preflight_from_gcode(Path(args.name), args.print_only)
    else:
        _preflight_from_example(args.name, args.print_only)


def _preflight_from_example(name: str, print_only: bool) -> None:
    """Spindle preflight: load job.yaml + machine + tool, show params, walk."""
    job, machine, material, tool, derived = _load_job_context(name)
    print(format_report(job, machine, material, tool, derived))

    if any(not c["ok"] for c in derived["checks"]):
        sys.exit(
            "\nABORT: one or more safety checks failed above. "
            "Adjust the material, tool, or spindle_rpm in job.yaml and re-run."
        )

    print()
    print("=" * 60)
    print("Pre-cut checklist — confirm each item before starting the spindle.")
    print("=" * 60)
    print()

    _walk_checklist(
        PREFLIGHT_CHECKLIST,
        {
            "tool_id": tool["id"],
            "tool_diameter": tool["diameter_mm"],
            "gcode": job.gcode,
        },
        print_only=print_only,
        arg_for_rerun=name,
    )


def _preflight_from_gcode(gcode_path: Path, print_only: bool) -> None:
    """Detect head from the GCode file, then walk the matching checklist."""
    if not gcode_path.exists():
        sys.exit(f"error: gcode file not found: {gcode_path}")

    text = gcode_path.read_text()
    head = detect_head(text)
    material = _extract_material_comment(text) or "(unspecified)"

    print(f"File:     {gcode_path}")
    print(f"Head:     {head}")
    print(f"Material: {material}")
    print()
    print("Reminder: run `cnc.py validate` on this file first if you haven't.")
    print()

    if head == "laser":
        checklist = LASER_PREFLIGHT_CHECKLIST
        banner = "Pre-burn checklist — confirm each item before firing the laser."
    else:
        # Spindle-style preflight from a raw gcode path: we don't have a
        # job.yaml so the params report is skipped. Walk the spindle
        # checklist with placeholder bindings.
        checklist = PREFLIGHT_CHECKLIST
        banner = "Pre-cut checklist — confirm each item before starting the spindle."

    print("=" * 60)
    print(banner)
    print("=" * 60)
    print()

    _walk_checklist(
        checklist,
        {
            "tool_id": "(see gcode header)",
            "tool_diameter": "(see gcode header)",
            "material": material,
            "gcode": str(gcode_path),
        },
        print_only=print_only,
        arg_for_rerun=str(gcode_path),
    )


def _extract_material_comment(gcode_text: str) -> str | None:
    """Extract ;MATERIAL: <id> from the GCode header, if present."""
    for line in gcode_text.splitlines()[:30]:
        m = re.search(r";\s*MATERIAL:\s*(\S+)", line)
        if m:
            return m.group(1)
    return None


def _preflight_from_yaml(yaml_path: Path, print_only: bool) -> None:
    """Walk preflight against a standalone job.yaml file.

    Routes to the laser or spindle checklist based on `head:` in the
    yaml. For laser jobs we skip the spindle params report (different
    schema — no tool/rpm) and go straight to the checklist.
    """
    data = load_job_yaml(yaml_path)
    head = data.get("head", "spindle")

    print(f"Job:      {yaml_path}")
    print(f"Head:     {head}")
    print(f"Material: {data['material']}")
    print(f"GCode:    {data['gcode']}")
    print()
    print("Reminder: run `cnc.py validate` on the GCode first if you haven't.")
    print()

    if head == "laser":
        checklist = LASER_PREFLIGHT_CHECKLIST
        banner = "Pre-burn checklist — confirm each item before firing the laser."
        bindings = {
            "tool_id": "(n/a — laser)",
            "tool_diameter": "(n/a — laser)",
            "material": data["material"],
            "gcode": data["gcode"],
        }
    else:
        checklist = PREFLIGHT_CHECKLIST
        banner = "Pre-cut checklist — confirm each item before starting the spindle."
        bindings = {
            "tool_id": data.get("tool", "(see job.yaml)"),
            "tool_diameter": "(see profiles/tools.yaml)",
            "material": data["material"],
            "gcode": data["gcode"],
        }

    print("=" * 60)
    print(banner)
    print("=" * 60)
    print()

    _walk_checklist(
        checklist,
        bindings,
        print_only=print_only,
        arg_for_rerun=str(yaml_path),
    )


def cmd_jigsaw(args: argparse.Namespace) -> None:
    raise SystemExit(
        "cnc.py jigsaw was removed: the jigsaw project moved to its own repo "
        "(~/src/vibes/jigsawzall). Run jigsaw.py there directly."
    )


def cmd_find_machine(args: argparse.Namespace) -> None:
    """Discover Grbl_ESP32 controllers on the LAN via mDNS + SSDP."""
    rc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "find_cnc.py"),
            "--timeout",
            str(args.timeout),
            *(["--first"] if args.first else []),
            *(["--no-probe"] if args.no_probe else []),
            *(["--cache"] if args.cache else []),
        ]
    ).returncode
    sys.exit(rc)


def cmd_ip(args: argparse.Namespace) -> None:
    """Print the controller's IP. Prefers cached state; falls back to mDNS scan."""
    from cnc_state import (  # local import — keeps cnc.py import-light
        DEFAULT_FRESHNESS_SEC,
        format_age,
        get_machine,
        is_fresh,
    )

    max_age = (
        args.max_age_sec if args.max_age_sec is not None else DEFAULT_FRESHNESS_SEC
    )
    record = get_machine()
    if record and is_fresh(record, max_age_sec=max_age):
        age = record.age_seconds() or 0
        if args.verbose:
            print(
                f"{record.ip}  (cached, last seen {format_age(age)}"
                + (f", MAC {record.mac}" if record.mac else "")
                + ")",
                file=sys.stderr,
            )
        print(record.ip)
        return
    if record and not args.no_cache_fallback:
        # Stale cache. Try a discovery scan; if it works, use that.
        # If discovery fails, we'll fall back to the stale cache with a
        # warning so the user has SOMETHING to try.
        pass
    if not args.no_discover:
        if args.verbose:
            print(
                f"no fresh cache; scanning network (timeout {args.discover_timeout}s)...",
                file=sys.stderr,
            )
        rc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "find_cnc.py"),
                "--first",
                "--cache",
                "--timeout",
                str(args.discover_timeout),
            ]
        ).returncode
        if rc == 0:
            # find_cnc.py just wrote to the cache; read it back
            record = get_machine()
            if record:
                print(record.ip)
                return
    if record:
        # Stale cache fallback
        age = record.age_seconds() or 0
        print(
            f"warning: using stale cache (last seen {format_age(age)}); "
            f"machine may have changed IP",
            file=sys.stderr,
        )
        print(record.ip)
        return
    print(
        "error: no machine in cache and discovery found none. "
        "Try `cnc.py inspect` over USB to populate the cache.",
        file=sys.stderr,
    )
    sys.exit(1)


def cmd_preview(args: argparse.Namespace) -> None:
    """Open the given GCode file in CAMotics for 3D toolpath inspection.

    CAMotics is a separate install (https://camotics.org). On macOS we
    launch the bundled .app via `open -a CAMotics`; on Linux/Windows we
    invoke the `camotics` binary if it's on PATH. The app stays open for
    interactive inspection; close it when done. No headless render today —
    the prebuilt CAMotics CLI tools (camsim, gcodetool) link against an
    Intel libcairo that doesn't load cleanly on Apple Silicon, so we skip
    them and rely on the GUI launcher.
    """
    gcode = Path(args.gcode).resolve()
    if not gcode.exists():
        sys.exit(f"error: gcode file not found: {gcode}")

    import platform as _platform

    system = _platform.system()
    if system == "Darwin":
        app = "/Applications/CAMotics.app"
        if not Path(app).exists():
            sys.exit(
                "error: CAMotics not installed in /Applications. "
                "Download from https://camotics.org and drag to /Applications."
            )
        # `open -a` returns immediately; CAMotics stays running for the user
        rc = subprocess.run(["open", "-a", "CAMotics", str(gcode)]).returncode
        if rc != 0:
            sys.exit(rc)
        print(f"-> opened {gcode.name} in CAMotics")
        return

    # Linux / Windows: assume `camotics` is on PATH
    binary = "camotics"
    try:
        subprocess.Popen([binary, str(gcode)])
        print(f"-> opened {gcode.name} in CAMotics")
    except FileNotFoundError:
        sys.exit(
            f"error: `camotics` not on PATH. Install from https://camotics.org "
            f"or apt install camotics on Debian/Ubuntu."
        )


def cmd_cam(args: argparse.Namespace) -> None:
    """Dispatch to scripts/cam_cli.py. All remaining args pass through."""
    import cam_cli

    rc = cam_cli.main(args.rest or None)
    if rc:
        sys.exit(rc)


def cmd_cal_laser(args: argparse.Namespace) -> None:
    """Dispatch to lessons/laser/06_spiral_cal/spiral_cal.py."""
    sys.path.insert(0, str(ROOT / "lessons" / "laser" / "06_spiral_cal"))
    import spiral_cal  # noqa: E402

    rc = spiral_cal.main(args.rest or None)
    if rc:
        sys.exit(rc)


def cmd_jog(args: argparse.Namespace) -> None:
    """Dispatch to lessons/integration/05_jog/jog.py."""
    sys.path.insert(0, str(ROOT / "lessons" / "integration" / "05_jog"))
    import jog  # noqa: E402

    rc = jog.main(args.rest or None)
    if rc:
        sys.exit(rc)


def cmd_help(args: argparse.Namespace) -> None:
    if args.search:
        matches = search(args.search)
        if not matches:
            sys.exit(f"no topics matching '{args.search}'")
        print(f"Topics matching '{args.search}':")
        for name in matches:
            print(f"  {name}")
        return

    if not args.topic:
        print(render_index())
        return

    try:
        print(render_topic(args.topic))
    except KeyError:
        sys.exit(f"unknown topic '{args.topic}'. Run `cnc.py help` for the topic list.")


def main() -> None:
    # Pre-dispatch: subcommands that take their own argparse (cam, cal-laser, jog)
    # bypass our top-level parser entirely, because argparse REMAINDER in
    # 3.12+ doesn't pass through leading-dash args cleanly to subparsers.
    if len(sys.argv) >= 2 and sys.argv[1] in ("cam", "cal-laser", "jog"):
        sub_argv = sys.argv[2:]
        if sys.argv[1] == "cam":
            import cam_cli

            sys.exit(cam_cli.main(sub_argv) or 0)
        if sys.argv[1] == "jog":
            sys.path.insert(0, str(ROOT / "lessons" / "integration" / "05_jog"))
            import jog  # noqa: E402

            sys.exit(jog.main(sub_argv) or 0)
        sys.path.insert(0, str(ROOT / "lessons" / "laser" / "06_spiral_cal"))
        import spiral_cal  # noqa: E402

        sys.exit(spiral_cal.main(sub_argv) or 0)

    p = argparse.ArgumentParser(prog="cnc", description=__doc__.splitlines()[0])
    subs = p.add_subparsers(dest="cmd", required=True)

    b = subs.add_parser("build", help="OpenSCAD -> CSG (or STL) for an example")
    b.add_argument("name", help="example name, e.g. hole_in_sheet")
    b.add_argument(
        "--format",
        choices=["csg", "stl"],
        help="generate only one format (default: csg)",
    )
    b.set_defaults(func=cmd_build)

    v = subs.add_parser("validate", help="run gcode_validate on a GCode file")
    v.add_argument("gcode", help="path to .gcode file")
    v.set_defaults(func=cmd_validate)

    t = subs.add_parser("test", help="run the pytest suite")
    t.set_defaults(func=cmd_test)

    c = subs.add_parser("clean", help="remove examples/*/build directories")
    c.set_defaults(func=cmd_clean)

    po = subs.add_parser("post", help="FreeCAD post-process (not yet implemented)")
    po.add_argument("fcstd", help="path to .FCStd project")
    po.add_argument("gcode", help="output path for .gcode")
    po.set_defaults(func=cmd_post)

    d = subs.add_parser("doctor", help="print resolved toolchain for diagnostics")
    d.set_defaults(func=cmd_doctor)

    pa = subs.add_parser(
        "params",
        help="print lookup tables + derived params for a job (reads job.yaml)",
    )
    pa.add_argument("name", help="example name, e.g. hole_in_sheet")
    pa.set_defaults(func=cmd_params)

    pf = subs.add_parser(
        "preflight",
        help="print params + walk the interactive pre-cut safety checklist",
    )
    pf.add_argument("name", help="example name, e.g. hole_in_sheet")
    pf.add_argument(
        "--print-only",
        action="store_true",
        help="print the checklist without prompting (for review/printing)",
    )
    pf.set_defaults(func=cmd_preflight)

    fm = subs.add_parser(
        "find-machine",
        help="discover Grbl_ESP32 controllers on the LAN (mDNS + SSDP)",
    )
    fm.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="scan duration in seconds (default 5)",
    )
    fm.add_argument(
        "--first",
        action="store_true",
        help="exit on first confirmed match (for scripting)",
    )
    fm.add_argument(
        "--no-probe",
        action="store_true",
        help="skip description.xml fingerprint (faster, noisier)",
    )
    fm.add_argument(
        "--cache",
        action="store_true",
        help="write first hit to ~/.cnc_state.json",
    )
    fm.set_defaults(func=cmd_find_machine)

    ip = subs.add_parser(
        "ip",
        help="print the controller's IP (cached if fresh, else mDNS scan)",
    )
    ip.add_argument(
        "--max-age-sec",
        type=float,
        default=None,
        help="cache freshness threshold in seconds (default 21600 = 6h)",
    )
    ip.add_argument(
        "--discover-timeout",
        type=float,
        default=5.0,
        help="mDNS scan timeout if cache is stale (seconds, default 5)",
    )
    ip.add_argument(
        "--no-discover",
        action="store_true",
        help="never scan; only use the cache (exit 1 if cache is missing or stale)",
    )
    ip.add_argument(
        "--no-cache-fallback",
        action="store_true",
        help="if a discovery scan fails, do NOT fall back to a stale cached IP",
    )
    ip.add_argument(
        "-v", "--verbose", action="store_true", help="print cache + scan info to stderr"
    )
    ip.set_defaults(func=cmd_ip)

    pv = subs.add_parser(
        "preview",
        help="open a GCode file in CAMotics for 3D toolpath inspection",
    )
    pv.add_argument("gcode", help="path to GCode file to visualize")
    pv.set_defaults(func=cmd_preview)

    h = subs.add_parser(
        "help",
        help="browse the toolchain reference (manpage-style)",
    )
    h.add_argument(
        "topic",
        nargs="?",
        help="topic name (omit for the topic index)",
    )
    h.add_argument(
        "--search",
        metavar="KEYWORD",
        help="list topics whose title or body contains KEYWORD",
    )
    h.set_defaults(func=cmd_help)

    cam = subs.add_parser(
        "cam",
        help="thin CLI + interactive shim over scripts/cam.py (run with --help for ops)",
        add_help=False,
    )
    cam.add_argument("rest", nargs=argparse.REMAINDER)
    cam.set_defaults(func=cmd_cam)

    cal = subs.add_parser(
        "cal-laser",
        help="spiral laser calibration card (hex spiral of double-spiral "
        "patches from origin); run with --help or 'interactive' for guided setup",
        add_help=False,
    )
    cal.add_argument("rest", nargs=argparse.REMAINDER)
    cal.set_defaults(func=cmd_cal_laser)

    jog_p = subs.add_parser(
        "jog",
        help="xbox + keyboard jogger with inline Z-probe (Anolex 4030); "
        "run with --print-map to see button mapping, --help for flags",
        add_help=False,
    )
    jog_p.add_argument("rest", nargs=argparse.REMAINDER)
    jog_p.set_defaults(func=cmd_jog)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
