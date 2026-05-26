"""Load 2D OpenSCAD designs into shapely polygons for use with cam.py ops.

Workflow:
  1. Author the shape in OpenSCAD (2D primitives, or projection() of 3D)
  2. Either pass the .scad file (we'll run OpenSCAD for you) OR pre-export
     an .svg yourself
  3. polygons = openscad_to_polygons("part.scad")  # or path/to.svg
  4. profile_cut(polygons[0], depth_mm=..., ...)   # pipe to cam.py

OpenSCAD's SVG export (`--export-format svg`) writes 2D primitives — or
the result of `projection(cut=true) { ... }` on 3D solids — as a single
<path> with subpaths. Each subpath ("M ... z" block) is one contour:
the first is the outer ring, subsequent are holes (e.g. for
`difference() { square(); circle(); }` you get outer-square plus
inner-circle).

The Y axis in OpenSCAD's SVG is flipped (negative Y is the SVG "down"
direction, but OpenSCAD uses standard math Y-up). We flip it back so
the returned shapely polygons are in OpenSCAD's native +Y-up
coordinate system — ready to feed into cam.py without further
transformation.

Doesn't handle 3D solids that haven't been projected. If you have a
3D model, wrap it in `projection(cut=true)` first, OR slice it at a
specific Z and project the slice. See OpenSCAD docs.

Subprocess to OpenSCAD respects the same OPENSCAD env var that
cnc.py doctor uses, with a macOS-app-bundle fallback.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry


# Resolution order matches cnc.py's _find_openscad
_OPENSCAD_FALLBACKS = [
    "/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD",
    "/usr/bin/openscad",
    "/usr/local/bin/openscad",
    "C:\\Program Files\\OpenSCAD\\openscad.exe",
]


def _find_openscad() -> str:
    """Return the path to the openscad executable, or raise SystemExit."""
    if env := os.environ.get("OPENSCAD"):
        if Path(env).exists():
            return env
    # Try PATH
    from shutil import which

    found = which("openscad")
    if found:
        return found
    # Platform-specific fallbacks
    for path in _OPENSCAD_FALLBACKS:
        if Path(path).exists():
            return path
    raise SystemExit(
        "openscad not found. Install via "
        "`brew install --cask openscad` (macOS) / "
        "`winget install OpenSCAD.OpenSCAD` (Windows) / "
        "your distro's package manager, or set the OPENSCAD env var to "
        "the binary path."
    )


def scad_to_svg(scad_path: Path | str, svg_path: Path | str | None = None) -> Path:
    """Run OpenSCAD to export `scad_path` to an SVG. If `svg_path` is None,
    writes to a tempfile and returns that path. Raises SystemExit on
    OpenSCAD failure."""
    scad_path = Path(scad_path)
    if not scad_path.exists():
        raise SystemExit(f"scad file not found: {scad_path}")
    if svg_path is None:
        # tempfile.NamedTemporaryFile cleanup leaks across Windows + the file
        # has to outlive this call so the caller can read it. mktemp the path
        # and rely on temp-dir cleanup.
        svg_path = Path(tempfile.mkstemp(suffix=".svg")[1])
    else:
        svg_path = Path(svg_path)
    openscad = _find_openscad()
    result = subprocess.run(
        [openscad, "--export-format", "svg", "-o", str(svg_path), str(scad_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"openscad failed (exit {result.returncode}):\n"
            f"  stderr: {result.stderr.strip()}\n"
            f"  stdout: {result.stdout.strip()}"
        )
    if not svg_path.exists():
        raise SystemExit(f"openscad ran but produced no SVG at {svg_path}")
    return svg_path


def svg_to_polygons(
    svg_path: Path | str, flip_y: bool = False, points_per_curve: int = 24
) -> list[Polygon]:
    """Parse an SVG file into shapely Polygons.

    Handles OpenSCAD's typical output (single <path> with multiple
    M..z subpaths). Each subpath becomes a ring; rings with their
    polygon center inside another ring become that ring's hole.
    Curves (C, Q, A) are tessellated to `points_per_curve` segments.

    flip_y (default False): leave coordinates as svgelements gives them.
    For OpenSCAD-exported SVGs, the viewBox has negative-min-Y to encode
    "math Y is up", and svgelements normalizes accordingly — you get
    polygons in the same +Y-up coords as your .scad source. Set
    flip_y=True only if loading a third-party SVG that's still in
    SVG-Y-down and you want the result in math-Y-up.
    """
    from svgelements import SVG, Path as SvgPath, Polygon as SvgPolygon
    from svgelements import Polyline as SvgPolyline, Rect as SvgRect
    from svgelements import Circle as SvgCircle, Ellipse as SvgEllipse

    svg_path = Path(svg_path)
    if not svg_path.exists():
        raise SystemExit(f"svg file not found: {svg_path}")
    # ppi=25.4 makes svgelements treat 1 user unit = 1 mm (instead of
    # the default 96 DPI scaling that would turn "60mm" → 226.77 px).
    # OpenSCAD's SVG export uses mm in viewBox + path data, so this keeps
    # the returned shapely polygons in millimeters — same units as cam.py.
    svg = SVG.parse(str(svg_path), ppi=25.4)

    # Gather all sub-rings across every element; each ring is a list[(x,y)]
    rings: list[list[tuple[float, float]]] = []

    def y(v):
        return -v if flip_y else v

    for el in svg.elements():
        if isinstance(el, SvgPath):
            # Split on subpaths. svgelements' Path is iterable over segments;
            # a Move starts a new subpath. We use as_subpaths() to split.
            for sub in el.as_subpaths():
                pts = _sample_path(sub, points_per_curve)
                if len(pts) >= 3:
                    rings.append([(p[0], y(p[1])) for p in pts])
        elif isinstance(el, (SvgPolygon, SvgPolyline)):
            pts = [(float(p.x), y(float(p.y))) for p in el.points]
            if len(pts) >= 3:
                rings.append(pts)
        elif isinstance(el, SvgRect):
            x, y0 = float(el.x), float(el.y)
            w, h = float(el.width), float(el.height)
            rings.append(
                [
                    (x, y(y0)),
                    (x + w, y(y0)),
                    (x + w, y(y0 + h)),
                    (x, y(y0 + h)),
                ]
            )
        elif isinstance(el, (SvgCircle, SvgEllipse)):
            cx, cy = float(el.cx), float(el.cy)
            rx = float(getattr(el, "rx", el.r if hasattr(el, "r") else 0))
            ry = float(getattr(el, "ry", el.r if hasattr(el, "r") else 0))
            pts = _sample_ellipse(cx, cy, rx, ry, points_per_curve * 2)
            rings.append([(px, y(py)) for px, py in pts])
        # Other element types (text, image, etc.) are ignored.

    if not rings:
        return []
    return _rings_to_polygons(rings)


def _sample_path(subpath, n_per_curve: int) -> list[tuple[float, float]]:
    """Walk an svgelements subpath, sampling curve segments to a fixed
    point count. Linear segments contribute their endpoints; curves
    contribute N+1 points."""
    from svgelements import Line, QuadraticBezier, CubicBezier, Arc, Move, Close

    pts: list[tuple[float, float]] = []
    for seg in subpath:
        if isinstance(seg, Move):
            pts.append((float(seg.end.x), float(seg.end.y)))
        elif isinstance(seg, (Line, Close)):
            pts.append((float(seg.end.x), float(seg.end.y)))
        elif isinstance(seg, (QuadraticBezier, CubicBezier, Arc)):
            for i in range(1, n_per_curve + 1):
                t = i / n_per_curve
                p = seg.point(t)
                pts.append((float(p.x), float(p.y)))
    # Deduplicate trailing repeated point (svgelements often closes with a
    # Line back to start which equals the first Move).
    if len(pts) > 1 and pts[-1] == pts[0]:
        pts = pts[:-1]
    return pts


def _sample_ellipse(cx: float, cy: float, rx: float, ry: float, n: int):
    import math

    return [
        (
            cx + rx * math.cos(2 * math.pi * i / n),
            cy + ry * math.sin(2 * math.pi * i / n),
        )
        for i in range(n)
    ]


def _rings_to_polygons(rings: list[list[tuple[float, float]]]) -> list[Polygon]:
    """Convert a flat list of rings into shapely Polygons with holes.

    For each pair (A, B), if B's representative interior point lies inside
    A's polygon AND A's area > B's area, B is a hole of A. Builds a
    parent→children mapping.
    """
    if not rings:
        return []
    # Make raw shapely polygons (without holes) first to test containment
    raws = []
    for r in rings:
        try:
            poly = Polygon(r)
            if not poly.is_valid:
                poly = poly.buffer(0)
            raws.append(poly)
        except Exception:
            raws.append(None)

    n = len(raws)
    # parents[i] = j means ring i is a hole of ring j (or None if outer)
    parents: list[int | None] = [None] * n
    for i in range(n):
        if raws[i] is None or raws[i].is_empty:
            continue
        ipt = raws[i].representative_point()
        best_parent = None
        best_area = float("inf")
        for j in range(n):
            if i == j or raws[j] is None or raws[j].is_empty:
                continue
            if raws[j].area <= raws[i].area:
                continue
            # j is bigger; does it contain i's representative point?
            if raws[j].contains(ipt):
                if raws[j].area < best_area:
                    best_area = raws[j].area
                    best_parent = j
        parents[i] = best_parent

    # Outer rings are those with parents[i] is None and depth even (0).
    # Holes are those at depth 1. Holes-inside-holes (depth 2) become outer
    # rings again — common in OpenSCAD differences with nested cuts.
    def depth(i):
        d = 0
        cur = parents[i]
        while cur is not None:
            d += 1
            cur = parents[cur]
        return d

    polygons: list[Polygon] = []
    for i, p in enumerate(raws):
        if p is None or p.is_empty:
            continue
        if depth(i) % 2 == 1:
            continue  # skip holes; they're attached to their parent
        # Collect immediate children as holes
        holes = []
        for j, par in enumerate(parents):
            if par == i and raws[j] is not None and not raws[j].is_empty:
                holes.append(list(raws[j].exterior.coords))
        try:
            poly = Polygon(list(p.exterior.coords), holes=holes)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if not poly.is_empty:
                polygons.append(poly)
        except Exception:
            continue
    return polygons


def openscad_to_polygons(
    scad_or_svg_path: Path | str,
    flip_y: bool = False,
    points_per_curve: int = 24,
) -> list[Polygon]:
    """Top-level: if input is .scad, run OpenSCAD to get SVG first, then
    parse. If input is .svg, parse directly."""
    p = Path(scad_or_svg_path)
    if p.suffix.lower() == ".scad":
        svg = scad_to_svg(p)
    elif p.suffix.lower() == ".svg":
        svg = p
    else:
        raise SystemExit(
            f"openscad_to_polygons: expected .scad or .svg, got {p.suffix!r}"
        )
    return svg_to_polygons(svg, flip_y=flip_y, points_per_curve=points_per_curve)


def _demo() -> int:
    """Generate a sample .scad → polygons round-trip."""
    scad_content = """
difference() {
    square([60, 40]);
    translate([15, 12]) circle(r=3, $fn=32);
    translate([45, 12]) circle(r=3, $fn=32);
    translate([30, 28]) circle(r=8, $fn=32);
}
"""
    tmp = Path(tempfile.mkstemp(suffix=".scad")[1])
    tmp.write_text(scad_content)
    polys = openscad_to_polygons(tmp)
    tmp.unlink()
    print(f"loaded {len(polys)} polygon(s) from sample .scad")
    for i, p in enumerate(polys):
        print(
            f"  {i}: bounds={tuple(round(x, 2) for x in p.bounds)}, "
            f"area={p.area:.2f}, holes={len(p.interiors)}"
        )
    return 0


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["demo"], nargs="?", default="demo")
    args = ap.parse_args()
    sys.exit({"demo": _demo}[args.command]())
