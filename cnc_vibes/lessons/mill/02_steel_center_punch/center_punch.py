#!/usr/bin/env python3
"""Generate GCode that punches center-marks (small Z-plunge divets) at a
list of (x, y) points, using an engraver tip / V-bit. Intended for
marking mild steel before follow-up drilling — the 500W spindle on this
class of router can't CUT steel but can deform it superficially.

Three ways to specify the points:
  --points "x1,y1,x2,y2,..."     comma-separated, any number of pairs
  --points-file PATH              YAML file: list of [x, y] pairs
  --grid AxB --pitch P [--origin X,Y]
                                  generate an A-column by B-row grid

Usage:
  python center_punch.py --points "10,10,20,20,30,30"
  python center_punch.py --points-file my_holes.yaml --depth 0.4
  python center_punch.py --grid 5x4 --pitch 12 --origin 10,10
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

LESSON_DIR = Path(__file__).resolve().parent
REPO_ROOT = LESSON_DIR.parent.parent.parent
PROFILES = REPO_ROOT / "profiles"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
from job_params import find_by_id, load_yaml  # noqa: E402


SAFE_Z = 5.0
APPROACH_Z = 1.0  # mm above stock top at which to slow to plunge feed
DWELL_S = 0.1  # brief dwell at the bottom of each divet


# ---------------------------------------------------------------------------
# Point list helpers
# ---------------------------------------------------------------------------


def parse_points_csv(s: str) -> list[tuple[float, float]]:
    """Parse 'x1,y1,x2,y2,...' into [(x1,y1), (x2,y2), ...]."""
    vals = [float(v) for v in s.split(",") if v.strip()]
    if len(vals) % 2 != 0:
        sys.exit(f"error: --points needs an even number of values; got {len(vals)}")
    return [(vals[i], vals[i + 1]) for i in range(0, len(vals), 2)]


def load_points_file(path: Path) -> list[tuple[float, float]]:
    """Load points from a YAML file containing a list of [x, y] pairs."""
    if not path.exists():
        sys.exit(f"error: points file not found: {path}")
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, list):
        sys.exit(f"error: {path} must be a YAML list of [x, y] pairs")
    out = []
    for i, p in enumerate(data):
        if not (isinstance(p, list) and len(p) == 2):
            sys.exit(f"error: {path}[{i}] is not a [x, y] pair: {p!r}")
        out.append((float(p[0]), float(p[1])))
    return out


def generate_grid(
    cols: int, rows: int, pitch: float, origin_x: float = 0.0, origin_y: float = 0.0
) -> list[tuple[float, float]]:
    """Generate a cols x rows grid of points, spaced by pitch mm."""
    if cols < 1 or rows < 1:
        sys.exit("error: grid dimensions must be >= 1")
    if pitch <= 0:
        sys.exit("error: pitch must be > 0")
    return [
        (origin_x + c * pitch, origin_y + r * pitch)
        for r in range(rows)
        for c in range(cols)
    ]


def parse_grid_spec(s: str) -> tuple[int, int]:
    """Parse 'AxB' into (A, B)."""
    if "x" not in s:
        sys.exit(f"error: --grid expects format 'AxB' (got {s!r})")
    a, b = s.split("x", 1)
    return int(a), int(b)


# ---------------------------------------------------------------------------
# GCode generation
# ---------------------------------------------------------------------------


def generate_gcode(
    points: list[tuple[float, float]],
    depth_mm: float,
    plunge_feed_mm_per_min: int,
    tool: dict,
    spindle_rpm: int,
    machine: dict,
) -> str:
    """Generate the full GCode for a center-punch job."""
    if not points:
        sys.exit("error: no points provided")
    if depth_mm <= 0:
        sys.exit(f"error: --depth must be > 0 (got {depth_mm})")
    if depth_mm > 2.0:
        sys.exit(
            f"error: --depth {depth_mm} is too aggressive for a center punch "
            f"(typical 0.3-0.5 mm). If you really want this, edit the source."
        )
    if spindle_rpm > tool["max_rpm"]:
        sys.exit(
            f"error: spindle_rpm {spindle_rpm} exceeds tool max_rpm ({tool['max_rpm']})"
        )
    if plunge_feed_mm_per_min > tool["max_plunge_mm_per_min"]:
        sys.exit(
            f"error: plunge feed {plunge_feed_mm_per_min} exceeds tool "
            f"max_plunge_mm_per_min ({tool['max_plunge_mm_per_min']})"
        )

    # Verify all points are within machine envelope (positive quadrant).
    env = machine["envelope_mm"]
    for x, y in points:
        if not (0 <= x <= env["x"]):
            sys.exit(f"error: point X={x} outside machine X envelope [0, {env['x']}]")
        if not (0 <= y <= env["y"]):
            sys.exit(f"error: point Y={y} outside machine Y envelope [0, {env['y']}]")

    final_z = -depth_mm

    header = [
        "; center_punch.py — steel center-punch divets",
        f"; tool={tool['id']}  rpm={spindle_rpm}  depth={depth_mm}  "
        f"plunge_feed={plunge_feed_mm_per_min}  n_points={len(points)}",
        ";",
        f";TOOL: {tool['id']}",
        "",
        "$32=0   ; ensure GRBL laser mode is OFF (spindle job)",
        "G21     ; mm",
        "G90     ; absolute coordinates",
        "M5      ; spindle off (start clean)",
        f"G0 Z{SAFE_Z:.3f}  ; retract to safe Z before any XY motion",
        "G0 X0 Y0",
        f"M3 S{spindle_rpm}",
        "",
    ]

    body = []
    for i, (x, y) in enumerate(points, start=1):
        body.append(f"; --- point {i}/{len(points)}: ({x:.3f}, {y:.3f}) ---")
        body.append(f"G0 X{x:.3f} Y{y:.3f}")
        body.append(f"G0 Z{APPROACH_Z:.3f}  ; approach")
        body.append(f"G1 Z{final_z:.3f} F{plunge_feed_mm_per_min}")
        if DWELL_S > 0:
            body.append(f"G4 P{DWELL_S}  ; dwell to stabilize the mark")
        body.append(f"G0 Z{SAFE_Z:.3f}  ; retract")
        body.append("")

    footer = [
        "M5",
        "G0 X0 Y0",
    ]
    return "\n".join(header + body + footer) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_points(args) -> list[tuple[float, float]]:
    sources = sum(1 for x in (args.points, args.points_file, args.grid) if x)
    if sources == 0:
        sys.exit("error: provide one of --points, --points-file, or --grid")
    if sources > 1:
        sys.exit("error: --points / --points-file / --grid are mutually exclusive")

    if args.points:
        return parse_points_csv(args.points)
    if args.points_file:
        return load_points_file(Path(args.points_file))
    cols, rows = parse_grid_spec(args.grid)
    ox, oy = (0.0, 0.0)
    if args.origin:
        try:
            ox_str, oy_str = args.origin.split(",", 1)
            ox, oy = float(ox_str), float(oy_str)
        except ValueError:
            sys.exit(f"error: --origin expects 'X,Y' (got {args.origin!r})")
    return generate_grid(cols, rows, args.pitch, ox, oy)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])

    src = p.add_argument_group("point source (pick one)")
    src.add_argument("--points", default=None, help='"x1,y1,x2,y2,..." inline list')
    src.add_argument(
        "--points-file", default=None, help="YAML file with a list of [x, y] pairs"
    )
    src.add_argument("--grid", default=None, help="generate a 'AxB' grid")
    src.add_argument(
        "--pitch", type=float, default=20.0, help="grid spacing in mm (default 20)"
    )
    src.add_argument(
        "--origin", default=None, help="grid origin as 'X,Y' (default 0,0)"
    )

    p.add_argument(
        "--depth", type=float, default=0.4, help="divet depth, mm (default 0.4)"
    )
    p.add_argument(
        "--plunge-feed", type=int, default=80, help="plunge feed mm/min (default 80)"
    )
    p.add_argument(
        "--tool", default="vbit_60deg_6mm", help="tool id from profiles/tools.yaml"
    )
    p.add_argument("--spindle-rpm", type=int, default=12000)
    p.add_argument("--out", type=Path, default=None, help="output gcode path")
    args = p.parse_args()

    points = _build_points(args)
    machine = load_yaml(PROFILES / "anolex_4030_evo_ultra2.yaml")
    tools = load_yaml(PROFILES / "tools.yaml")
    tool = find_by_id(tools, args.tool, "tool")

    gcode = generate_gcode(
        points=points,
        depth_mm=args.depth,
        plunge_feed_mm_per_min=args.plunge_feed,
        tool=tool,
        spindle_rpm=args.spindle_rpm,
        machine=machine,
    )

    if args.out is None:
        build_dir = LESSON_DIR / "build"
        build_dir.mkdir(exist_ok=True)
        args.out = build_dir / f"center_punch_n{len(points)}.gcode"
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)

    args.out.write_text(gcode)
    print(f"-> wrote {args.out}  ({len(points)} points)")


if __name__ == "__main__":
    main()
