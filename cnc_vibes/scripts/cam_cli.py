"""`cnc.py cam` — thin CLI + interactive shim over scripts/cam.py.

Two modes:

  Non-interactive (shell):
    cnc.py cam profile  --shape rrect --width 60 --height 40 --radius 5 \\
        --depth 6 --material mdf_12mm --tool flat_6mm_2flute --side outside
    cnc.py cam drill    --pattern grid --cols 3 --rows 3 --spacing 25 \\
        --depth 5 --material mdf_12mm --tool drill_3.2mm_m4_clearance
    cnc.py cam engrave  --text "BIN A" --x 10 --y 10 --height 6 \\
        --depth 0.3 --material mdf_12mm --tool vbit_60deg_6mm
    cnc.py cam profile  --head laser --shape circle --diameter 30 \\
        --material cardboard_thin_1mm

  Interactive (TUI):
    cnc.py cam
    -> prompts for head / material / tool / op / shape / params,
       prints the equivalent shell command, confirms, emits, validates.

Output: writes .gcode to --out (default build/<op>_<shape>_<ts>.gcode),
then auto-runs cnc.py validate. Non-zero exit on validator failure.
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from shapely.affinity import translate
from shapely.geometry import Point, Polygon, box

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
PROFILES_DIR = REPO_ROOT / "profiles"
DEFAULT_BUILD = REPO_ROOT / "build" / "cam_cli"

sys.path.insert(0, str(SCRIPT_DIR))
import cam  # noqa: E402
import laser_cam  # noqa: E402
from openscad_loader import openscad_to_polygons, svg_to_polygons  # noqa: E402


# ---------------------------------------------------------------------------
# Shape factory
# ---------------------------------------------------------------------------


def _parse_xy(s: str) -> tuple[float, float]:
    parts = [p.strip() for p in s.split(",")]
    if len(parts) != 2:
        raise SystemExit(f"expected 'x,y', got {s!r}")
    return (float(parts[0]), float(parts[1]))


def _parse_points(s: str) -> list[tuple[float, float]]:
    return [_parse_xy(pt) for pt in s.split() if pt.strip()]


def _rrect(w: float, h: float, r: float) -> Polygon:
    if r <= 0:
        return box(-w / 2, -h / 2, w / 2, h / 2)
    if r > min(w, h) / 2 + 1e-9:
        raise SystemExit(
            f"rrect radius {r} too large for {w}x{h} (max {min(w, h) / 2})"
        )
    # Inset rectangle, then buffer by r with rounded joins.
    inset = box(-w / 2 + r, -h / 2 + r, w / 2 - r, h / 2 - r)
    return inset.buffer(r, resolution=32, join_style=1, cap_style=1)


def _ellipse(w: float, h: float, segments: int = 96) -> Polygon:
    pts = []
    for i in range(segments):
        a = 2 * math.pi * i / segments
        pts.append((w / 2 * math.cos(a), h / 2 * math.sin(a)))
    return Polygon(pts)


def build_shape(args: argparse.Namespace) -> Polygon:
    shape = args.shape
    if shape == "rect":
        if args.width is None or args.height is None:
            raise SystemExit("--shape rect requires --width and --height")
        geom = box(-args.width / 2, -args.height / 2, args.width / 2, args.height / 2)
    elif shape == "rrect":
        if args.width is None or args.height is None or args.radius is None:
            raise SystemExit("--shape rrect requires --width, --height, --radius")
        geom = _rrect(args.width, args.height, args.radius)
    elif shape == "circle":
        if args.diameter is None:
            raise SystemExit("--shape circle requires --diameter")
        geom = Point(0, 0).buffer(args.diameter / 2, resolution=48)
    elif shape == "ellipse":
        if args.width is None or args.height is None:
            raise SystemExit("--shape ellipse requires --width and --height")
        geom = _ellipse(args.width, args.height)
    elif shape == "polygon":
        if not args.points:
            raise SystemExit('--shape polygon requires --points "x1,y1 x2,y2 ..."')
        pts = _parse_points(args.points)
        if len(pts) < 3:
            raise SystemExit("polygon needs at least 3 points")
        geom = Polygon(pts)
    elif shape == "svg":
        if not args.svg_file:
            raise SystemExit("--shape svg requires --svg-file")
        polys = svg_to_polygons(Path(args.svg_file))
        if not polys:
            raise SystemExit(f"no polygons found in {args.svg_file}")
        if len(polys) > 1:
            print(
                f"warning: {args.svg_file} has {len(polys)} polygons; using "
                f"the largest by area. Use --scad-file for explicit single-shape output.",
                file=sys.stderr,
            )
        geom = max(polys, key=lambda p: p.area)
    elif shape == "scad":
        if not args.scad_file:
            raise SystemExit("--shape scad requires --scad-file")
        polys = openscad_to_polygons(Path(args.scad_file))
        if not polys:
            raise SystemExit(f"no polygons produced from {args.scad_file}")
        if len(polys) > 1:
            print(
                f"warning: {args.scad_file} produced {len(polys)} polygons; using the largest",
                file=sys.stderr,
            )
        geom = max(polys, key=lambda p: p.area)
    else:
        raise SystemExit(f"unknown --shape {shape!r}")

    if args.center:
        cx, cy = _parse_xy(args.center)
        geom = translate(geom, xoff=cx, yoff=cy)
    return geom


def add_shape_args(p: argparse.ArgumentParser, required: bool = True) -> None:
    p.add_argument(
        "--shape",
        required=required,
        choices=("rect", "rrect", "circle", "ellipse", "polygon", "svg", "scad"),
    )
    p.add_argument("--width", type=float)
    p.add_argument("--height", type=float)
    p.add_argument("--radius", type=float, help="corner radius for rrect")
    p.add_argument("--diameter", type=float, help="for circle")
    p.add_argument("--points", help='"x1,y1 x2,y2 ..." for polygon')
    p.add_argument("--svg-file", help="path to .svg for --shape svg")
    p.add_argument("--scad-file", help="path to .scad for --shape scad")
    p.add_argument(
        "--center", help='shift shape by "x,y" mm (default: shape at origin)'
    )


# ---------------------------------------------------------------------------
# Hole pattern factory
# ---------------------------------------------------------------------------


def build_holes(args: argparse.Namespace) -> list[tuple[float, float]]:
    pattern = args.pattern
    ox, oy = _parse_xy(args.origin) if args.origin else (0.0, 0.0)
    if pattern == "grid":
        for f in ("cols", "rows", "spacing"):
            if getattr(args, f) is None:
                raise SystemExit(f"--pattern grid requires --{f}")
        cols, rows, sp = args.cols, args.rows, args.spacing
        x0 = ox - sp * (cols - 1) / 2
        y0 = oy - sp * (rows - 1) / 2
        return [(x0 + i * sp, y0 + j * sp) for j in range(rows) for i in range(cols)]
    if pattern == "bolt-circle":
        if args.count is None or args.radius is None:
            raise SystemExit("--pattern bolt-circle requires --count and --radius")
        n, r = args.count, args.radius
        return [
            (
                ox + r * math.cos(2 * math.pi * i / n),
                oy + r * math.sin(2 * math.pi * i / n),
            )
            for i in range(n)
        ]
    if pattern == "linear":
        if args.count is None or args.spacing is None:
            raise SystemExit("--pattern linear requires --count and --spacing")
        n, sp = args.count, args.spacing
        angle = math.radians(args.angle or 0.0)
        dx, dy = math.cos(angle) * sp, math.sin(angle) * sp
        x0 = ox - dx * (n - 1) / 2
        y0 = oy - dy * (n - 1) / 2
        return [(x0 + i * dx, y0 + i * dy) for i in range(n)]
    if pattern == "explicit":
        if not args.points:
            raise SystemExit('--pattern explicit requires --points "x1,y1 ..."')
        pts = _parse_points(args.points)
        return [(ox + x, oy + y) for x, y in pts]
    raise SystemExit(f"unknown --pattern {pattern!r}")


def add_hole_args(p: argparse.ArgumentParser, required: bool = True) -> None:
    p.add_argument(
        "--pattern",
        required=required,
        choices=("grid", "bolt-circle", "linear", "explicit"),
    )
    p.add_argument("--cols", type=int)
    p.add_argument("--rows", type=int)
    p.add_argument("--spacing", type=float, help="grid pitch or linear pitch (mm)")
    p.add_argument("--count", type=int, help="for bolt-circle or linear")
    p.add_argument("--radius", type=float, help="for bolt-circle (mm)")
    p.add_argument("--angle", type=float, help="for linear (degrees, default 0)")
    p.add_argument("--points", help='"x1,y1 ..." for explicit')
    p.add_argument("--origin", help='shift pattern by "x,y" (default 0,0)')


# ---------------------------------------------------------------------------
# Op execution
# ---------------------------------------------------------------------------

_LASER_REFUSALS = {
    "pocket": "laser can't pocket — it can only cut through. For interior removal, "
    "cut a profile around what you want REMOVED.",
    "drill": "laser doesn't drill. Use `profile --shape circle --diameter <d>` instead.",
    "chamfer": "chamfer needs a V-bit + controlled Z; laser is constant-depth.",
    "profile-tabs": "tabs use Z lift on the final pass; laser has no Z motion.",
    "face": "face-milling needs a flat endmill skimming Z; laser is constant-depth.",
}


def _resolve_tool_and_material_spindle(args):
    tool = cam.load_tool(args.tool) if args.tool else None
    material = cam.load_material(args.material) if args.material else None
    return tool, material


def _emit_spindle(args: argparse.Namespace) -> str:
    tool, material = _resolve_tool_and_material_spindle(args)
    cfg = cam.CamConfig(strict=args.strict)
    if args.safe_z is not None:
        cfg.safe_z_mm = args.safe_z

    op = args.op
    if op in (
        "profile",
        "pocket",
        "drill",
        "chamfer",
        "profile-tabs",
        "slot",
        "face",
        "text-profile",
    ):
        if getattr(args, "depth", None) is None:
            raise SystemExit(f"--depth is required for {op} (spindle)")
    if op == "profile":
        out = cam.profile_cut(
            build_shape(args),
            args.depth,
            tool=tool,
            material=material,
            side=args.side,
            cfg=cfg,
        )
    elif op == "pocket":
        out = cam.pocket_mill(
            build_shape(args),
            args.depth,
            tool=tool,
            material=material,
            stepover_factor=args.stepover,
            cfg=cfg,
        )
    elif op == "drill":
        out = cam.drill_array(
            build_holes(args),
            args.depth,
            tool=tool,
            material=material,
            peck_depth_mm=args.peck,
            cfg=cfg,
        )
    elif op == "engrave":
        out = cam.engrave_text(
            args.text,
            (args.x, args.y),
            args.height,
            args.depth,
            tool=tool,
            material=material,
            font_path=args.font,
            cfg=cfg,
        )
    elif op == "text-profile":
        out = cam.text_profile(
            args.text,
            (args.x, args.y),
            args.height,
            args.depth,
            tool=tool,
            material=material,
            font_path=args.font,
            side=args.side,
            cfg=cfg,
        )
    elif op == "chamfer":
        out = cam.chamfer_edge(
            build_shape(args),
            args.depth,
            tool=tool,
            material=material,
            cfg=cfg,
        )
    elif op == "profile-tabs":
        out = cam.profile_cut_with_tabs(
            build_shape(args),
            args.depth,
            tab_count=args.tab_count,
            tab_width_mm=args.tab_width,
            tab_height_mm=args.tab_height,
            tool=tool,
            material=material,
            side=args.side,
            cfg=cfg,
        )
    elif op == "slot":
        out = cam.slot_mill(
            _parse_xy(args.p1),
            _parse_xy(args.p2),
            args.width,
            args.depth,
            tool=tool,
            material=material,
            cfg=cfg,
        )
    elif op == "face":
        out = cam.face_mill(
            build_shape(args),
            args.depth,
            tool=tool,
            material=material,
            stepover_factor=args.stepover,
            cfg=cfg,
        )
    else:
        raise SystemExit(f"unknown op {op!r}")
    return out.text


def _emit_laser(args: argparse.Namespace) -> str:
    op = args.op
    if op in _LASER_REFUSALS:
        raise SystemExit(f"refusing: {_LASER_REFUSALS[op]}")
    mode = getattr(args, "laser_mode", None) or "dynamic"
    simplify = getattr(args, "simplify_mm", None)
    if simplify is None:
        simplify = 0.05
    if op == "slot":
        # Build the stadium polygon ourselves and laser_profile it.
        from shapely.geometry import LineString

        p1, p2 = _parse_xy(args.p1), _parse_xy(args.p2)
        stadium = LineString([p1, p2]).buffer(
            args.width / 2, resolution=32, cap_style=1
        )
        material = laser_cam.load_laser_material(args.material)
        out = laser_cam.laser_profile(
            stadium,
            material,
            mode=mode,
            simplify_tolerance_mm=simplify,
            cfg=cam.CamConfig(strict=args.strict),
        )
        return out.text
    if op == "profile":
        material = laser_cam.load_laser_material(args.material)
        out = laser_cam.laser_profile(
            build_shape(args),
            material,
            mode=mode,
            simplify_tolerance_mm=simplify,
            cfg=cam.CamConfig(strict=args.strict),
        )
        return out.text
    if op == "engrave":
        material = laser_cam.load_laser_material(args.material)
        out = laser_cam.laser_engrave(
            args.text,
            (args.x, args.y),
            args.height,
            material,
            font_path=args.font,
            mode=mode,
            simplify_tolerance_mm=simplify,
            cfg=cam.CamConfig(strict=args.strict),
        )
        return out.text
    if op == "text-profile":
        material = laser_cam.load_laser_material(args.material)
        out = laser_cam.text_profile(
            args.text,
            (args.x, args.y),
            args.height,
            material,
            font_path=args.font,
            mode=mode,
            simplify_tolerance_mm=simplify,
            cfg=cam.CamConfig(strict=args.strict),
        )
        return out.text
    raise SystemExit(f"unknown op {op!r} for --head laser")


def _emit(args: argparse.Namespace) -> str:
    return _emit_laser(args) if args.head == "laser" else _emit_spindle(args)


def _default_out_path(args: argparse.Namespace) -> Path:
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    head = args.head
    op = args.op
    if op == "drill":
        tag = getattr(args, "pattern", "drill")
    elif op == "engrave":
        tag = "engrave"
    elif op == "text-profile":
        tag = "textprofile"
    elif op == "slot":
        tag = "slot"
    else:
        tag = getattr(args, "shape", op)
    return DEFAULT_BUILD / f"{head}_{op}_{tag}_{ts}.gcode"


def _run_validator(path: Path) -> int:
    r = subprocess.run(
        [sys.executable, str(REPO_ROOT / "cnc.py"), "validate", str(path)],
        capture_output=False,
    )
    return r.returncode


# ---------------------------------------------------------------------------
# Argparse setup (shared between standalone and `cnc.py cam` entry)
# ---------------------------------------------------------------------------


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--head",
        default="spindle",
        choices=("spindle", "laser"),
        help="default: spindle",
    )
    p.add_argument(
        "--material", help="id from profiles/materials.yaml or laser_materials.yaml"
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"output .gcode path (default: {DEFAULT_BUILD}/<head>_<op>_<shape>_<ts>.gcode)",
    )
    p.add_argument("--strict", action="store_true", help="warnings become errors")
    p.add_argument("--no-validate", action="store_true", help="skip auto-validation")
    p.add_argument(
        "--laser-mode",
        choices=("dynamic", "static"),
        default="dynamic",
        help="laser ops only: dynamic=M4 (default, corner-safe but starves "
        "on very short segments); static=M3 (constant power; emits "
        ";LASER_MODE: static so the validator accepts M3)",
    )
    p.add_argument(
        "--simplify-mm",
        type=float,
        default=None,
        help="laser ops only: Douglas-Peucker tolerance for shape/glyph "
        "simplification (default 0.05mm; 0 disables). Drop sub-pixel "
        "vertices so M4 dynamic-power doesn't starve on micro-segments.",
    )


def _add_spindle_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--tool", help="id from profiles/tools.yaml")
    p.add_argument("--safe-z", type=float)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cnc.py cam",
        description="Thin CLI over scripts/cam.py + scripts/laser_cam.py",
    )
    subs = p.add_subparsers(dest="op")

    pr = subs.add_parser("profile", help="cut around a polygon's perimeter")
    _add_common(pr)
    _add_spindle_common(pr)
    add_shape_args(pr)
    pr.add_argument("--depth", type=float, help="(spindle only)")
    pr.add_argument("--side", default="outside", choices=("outside", "inside", "on"))

    po = subs.add_parser("pocket", help="clear polygon interior")
    _add_common(po)
    _add_spindle_common(po)
    add_shape_args(po, required=False)
    po.add_argument("--depth", type=float, help="(spindle only)")
    po.add_argument("--stepover", type=float, default=0.5)

    dr = subs.add_parser("drill", help="drill a hole pattern")
    _add_common(dr)
    _add_spindle_common(dr)
    add_hole_args(dr, required=False)
    dr.add_argument("--depth", type=float, help="(spindle only)")
    dr.add_argument("--peck", type=float, default=None, help="peck depth (mm)")

    en = subs.add_parser("engrave", help="outline-trace text")
    _add_common(en)
    _add_spindle_common(en)
    en.add_argument("--text", required=True)
    en.add_argument("--x", type=float, default=0.0)
    en.add_argument("--y", type=float, default=0.0)
    en.add_argument("--height", type=float, required=True, help="cap height (mm)")
    en.add_argument("--depth", type=float, default=0.3, help="(spindle only)")
    en.add_argument("--font", help="path to .ttf/.otf (default: system font)")

    tp = subs.add_parser(
        "text-profile",
        help="cut each glyph's silhouette out of stock (counters of O/A "
        "preserved as holes)",
    )
    _add_common(tp)
    _add_spindle_common(tp)
    tp.add_argument("--text", required=True)
    tp.add_argument("--x", type=float, default=0.0)
    tp.add_argument("--y", type=float, default=0.0)
    tp.add_argument("--height", type=float, required=True, help="cap height (mm)")
    tp.add_argument("--depth", type=float, help="(spindle only) cut depth")
    tp.add_argument("--font", help="path to .ttf/.otf (default: system font)")
    tp.add_argument(
        "--side",
        default="outside",
        choices=("outside", "inside", "on"),
        help="spindle only — outside (default) cuts the letter OUT of stock; "
        "inside cuts a letter-shaped hole; on is centerline.",
    )

    ch = subs.add_parser("chamfer", help="V-bit chamfer around perimeter")
    _add_common(ch)
    _add_spindle_common(ch)
    add_shape_args(ch, required=False)
    ch.add_argument("--depth", type=float, help="(spindle only)")

    pt = subs.add_parser("profile-tabs", help="profile with N holding tabs")
    _add_common(pt)
    _add_spindle_common(pt)
    add_shape_args(pt, required=False)
    pt.add_argument("--depth", type=float, help="(spindle only)")
    pt.add_argument("--side", default="outside", choices=("outside", "inside"))
    pt.add_argument("--tab-count", type=int, default=4)
    pt.add_argument("--tab-width", type=float, default=4.0)
    pt.add_argument("--tab-height", type=float, default=1.0)

    sl = subs.add_parser("slot", help="elongated slot from p1 to p2")
    _add_common(sl)
    _add_spindle_common(sl)
    sl.add_argument("--p1", required=True, help='"x,y"')
    sl.add_argument("--p2", required=True, help='"x,y"')
    sl.add_argument("--width", type=float, required=True)
    sl.add_argument("--depth", type=float, help="(spindle only)")

    fa = subs.add_parser("face", help="face-mill a region flat")
    _add_common(fa)
    _add_spindle_common(fa)
    add_shape_args(fa, required=False)
    fa.add_argument("--depth", type=float, help="(spindle only)")
    fa.add_argument("--stepover", type=float, default=0.7)

    return p


# ---------------------------------------------------------------------------
# Interactive (prompt_toolkit) wizard
# ---------------------------------------------------------------------------


def _list_yaml_ids(path: Path) -> list[str]:
    if not path.exists():
        return []
    with path.open() as f:
        items = yaml.safe_load(f) or []
    return [it["id"] for it in items if isinstance(it, dict) and "id" in it]


def _pick(label: str, choices: list[tuple[str, str]]) -> str:
    """Arrow-key picker (radiolist_dialog) returning the chosen value."""
    from prompt_toolkit.shortcuts import radiolist_dialog

    result = radiolist_dialog(
        title=label,
        values=choices,
    ).run()
    if result is None:
        sys.exit("cancelled")
    return result


def _ask(label: str, default: str | None = None, validator=None) -> str:
    from prompt_toolkit import prompt

    while True:
        s = prompt(f"{label}{f' [{default}]' if default else ''}: ").strip()
        if not s and default is not None:
            s = default
        if not s:
            print("(required)")
            continue
        if validator:
            try:
                validator(s)
            except Exception as e:
                print(f"  invalid: {e}")
                continue
        return s


def _ask_float(label: str, default: float | None = None) -> float:
    s = _ask(label, str(default) if default is not None else None, float)
    return float(s)


def _ask_int(label: str, default: int | None = None) -> int:
    s = _ask(label, str(default) if default is not None else None, int)
    return int(s)


_SHAPE_PROMPTS: dict[str, list[str]] = {
    "rect": ["width", "height"],
    "rrect": ["width", "height", "radius"],
    "circle": ["diameter"],
    "ellipse": ["width", "height"],
    "polygon": ["points"],
    "svg": ["svg_file"],
    "scad": ["scad_file"],
}


def _ask_shape(args: argparse.Namespace) -> None:
    args.shape = _pick(
        "Shape",
        [(s, s) for s in _SHAPE_PROMPTS.keys()],
    )
    for f in _SHAPE_PROMPTS[args.shape]:
        if f in ("svg_file", "scad_file", "points"):
            setattr(
                args,
                f if f != "svg_file" and f != "scad_file" else f.replace("_", "_"),
                _ask(f.replace("_", " ")),
            )
        else:
            setattr(args, f, _ask_float(f))


_OPS_FOR_HEAD = {
    "spindle": [
        "profile",
        "pocket",
        "drill",
        "engrave",
        "text-profile",
        "chamfer",
        "profile-tabs",
        "slot",
        "face",
    ],
    "laser": ["profile", "engrave", "text-profile", "slot"],
}


def interactive(argv_out: list[str] | None = None) -> argparse.Namespace:
    """Walk the user through head → material → tool → op → shape → params.
    Returns a Namespace as if argparse had parsed an equivalent command line,
    and (if argv_out is provided) appends the equivalent shell tokens to it.
    """
    args = argparse.Namespace()
    args.head = _pick("Head", [("spindle", "spindle"), ("laser", "laser")])

    if args.head == "spindle":
        materials = _list_yaml_ids(PROFILES_DIR / "materials.yaml")
        tools = _list_yaml_ids(PROFILES_DIR / "tools.yaml")
    else:
        materials = _list_yaml_ids(PROFILES_DIR / "laser_materials.yaml")
        tools = []

    args.material = _pick("Material", [(m, m) for m in materials])
    args.tool = _pick("Tool", [(t, t) for t in tools]) if tools else None
    args.op = _pick(
        f"Op ({args.head})",
        [(o, o) for o in _OPS_FOR_HEAD[args.head]],
    )

    # Per-op param prompts. We populate the same attributes argparse would.
    for attr in (
        "shape",
        "width",
        "height",
        "radius",
        "diameter",
        "points",
        "svg_file",
        "scad_file",
        "center",
        "pattern",
        "cols",
        "rows",
        "spacing",
        "count",
        "angle",
        "origin",
        "depth",
        "side",
        "stepover",
        "peck",
        "text",
        "x",
        "y",
        "font",
        "tab_count",
        "tab_width",
        "tab_height",
        "p1",
        "p2",
        "safe_z",
    ):
        if not hasattr(args, attr):
            setattr(args, attr, None)
    args.strict = False
    args.no_validate = False
    args.out = None
    args.laser_mode = "dynamic"
    args.simplify_mm = None

    if args.head == "laser":
        args.laser_mode = _pick(
            "Laser power mode",
            [
                ("dynamic", "dynamic — M4 (default, corner-safe)"),
                (
                    "static",
                    "static — M3 (constant S; use if M4 starves on short segments)",
                ),
            ],
        )

    if args.op == "drill":
        args.pattern = _pick(
            "Hole pattern",
            [(p, p) for p in ("grid", "bolt-circle", "linear", "explicit")],
        )
        if args.pattern == "grid":
            args.cols = _ask_int("cols", 3)
            args.rows = _ask_int("rows", 3)
            args.spacing = _ask_float("spacing mm", 20.0)
        elif args.pattern == "bolt-circle":
            args.count = _ask_int("count", 6)
            args.radius = _ask_float("radius mm", 25.0)
        elif args.pattern == "linear":
            args.count = _ask_int("count", 4)
            args.spacing = _ask_float("spacing mm", 20.0)
            args.angle = _ask_float("angle deg", 0.0)
        else:
            args.points = _ask('points "x1,y1 x2,y2 ..."')
        args.depth = _ask_float("depth mm")
        if args.head == "spindle":
            peck = _ask("peck mm (blank for none)", "")
            args.peck = float(peck) if peck else None
    elif args.op == "engrave":
        args.text = _ask("text")
        args.x = _ask_float("x mm", 0.0)
        args.y = _ask_float("y mm", 0.0)
        args.height = _ask_float("height mm", 6.0)
        if args.head == "spindle":
            args.depth = _ask_float("depth mm", 0.3)
    elif args.op == "text-profile":
        args.text = _ask("text to cut out")
        args.x = _ask_float("x mm", 0.0)
        args.y = _ask_float("y mm", 0.0)
        args.height = _ask_float("cap height mm", 25.0)
        args.depth = _ask_float("depth mm (full thickness for cut-through)")
        if args.head == "spindle":
            args.side = _pick(
                "Side (spindle)",
                [
                    ("outside", "outside — cut the letter OUT (default)"),
                    ("inside", "inside — cut a letter-shaped HOLE"),
                    ("on", "on — centerline (laser-like)"),
                ],
            )
    elif args.op == "slot":
        args.p1 = _ask('p1 "x,y"', "0,0")
        args.p2 = _ask('p2 "x,y"', "20,0")
        args.width = _ask_float("slot width mm", 6.0)
        if args.head == "spindle":
            args.depth = _ask_float("depth mm")
    else:
        _ask_shape(args)
        if args.op != "face":
            args.depth = _ask_float("depth mm")
        else:
            args.depth = _ask_float("skim depth mm", 0.5)
        if args.op in ("profile", "profile-tabs"):
            args.side = _pick(
                "Side",
                [("outside", "outside"), ("inside", "inside"), ("on", "on")]
                if args.op == "profile"
                else [("outside", "outside"), ("inside", "inside")],
            )
        if args.op == "pocket":
            args.stepover = _ask_float("stepover fraction", 0.5)
        if args.op == "face":
            args.stepover = _ask_float("stepover fraction", 0.7)
        if args.op == "profile-tabs":
            args.tab_count = _ask_int("tab count", 4)
            args.tab_width = _ask_float("tab width mm", 4.0)
            args.tab_height = _ask_float("tab height mm", 1.0)

    return args


def _argv_for(args: argparse.Namespace) -> list[str]:
    """Reconstruct the equivalent shell command for printing back to the user."""
    out: list[str] = ["cnc.py", "cam", args.op]
    if args.head != "spindle":
        out += ["--head", args.head]
    if args.material:
        out += ["--material", args.material]
    if getattr(args, "tool", None):
        out += ["--tool", args.tool]
    for k in (
        "shape",
        "width",
        "height",
        "radius",
        "diameter",
        "points",
        "svg_file",
        "scad_file",
        "center",
        "pattern",
        "cols",
        "rows",
        "spacing",
        "count",
        "angle",
        "origin",
        "depth",
        "side",
        "stepover",
        "peck",
        "text",
        "x",
        "y",
        "font",
        "tab_count",
        "tab_width",
        "tab_height",
        "p1",
        "p2",
        "laser_mode",
        "simplify_mm",
    ):
        v = getattr(args, k, None)
        if v is None or v is False:
            continue
        flag = "--" + k.replace("_", "-")
        if isinstance(v, str) and " " in v:
            out += [flag, f'"{v}"']
        else:
            out += [flag, str(v)]
    if args.strict:
        out.append("--strict")
    if args.no_validate:
        out.append("--no-validate")
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.op is None:
        # Interactive mode
        args = interactive()
        print("\nEquivalent command:")
        print("  " + " ".join(_argv_for(args)))
        from prompt_toolkit.shortcuts import confirm

        if not confirm("Emit GCode now?"):
            print("cancelled")
            return 1

    out_text = _emit(args)

    out_path: Path = args.out or _default_out_path(args)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(out_text)
    print(f"-> wrote {out_path}  ({len(out_text.splitlines())} lines)")

    if args.no_validate:
        return 0
    rc = _run_validator(out_path)
    return rc


if __name__ == "__main__":
    sys.exit(main())
