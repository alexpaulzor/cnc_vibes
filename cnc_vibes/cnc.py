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
    load_job,
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


def _looks_like_gcode_path(s: str) -> bool:
    """Heuristic: if it ends in .gcode/.nc/.cnc OR is an existing file path."""
    if s.endswith((".gcode", ".nc", ".cnc", ".tap")):
        return True
    return Path(s).exists() and Path(s).is_file()


def cmd_preflight(args: argparse.Namespace) -> None:
    # Two modes: example-name (spindle, job.yaml-driven) or raw gcode path
    # (head detected from the file's ;HEAD: marker; laser uses the laser
    # checklist).
    if _looks_like_gcode_path(args.name):
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

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
