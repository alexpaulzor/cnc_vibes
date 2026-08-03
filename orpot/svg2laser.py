#!/usr/bin/env python3
"""svg2laser.py — turn an SVG of cut paths into GRBL laser G-code for a WEAK
DIODE laser (static M3 + warmup lead-in).

Most SVG->G-code tools assume an instant-on CO2 laser: they emit M4 dynamic
power and start cutting the instant the beam fires. A weak diode laser needs the
opposite:

  * STATIC M3 constant power (M4 dynamic under-fires this diode), and
  * a front-loaded WARMUP lead-in: before each real cut the head traces back and
    forth over the START of that path so the beam reaches full optical power by
    the time it returns to the start point. Then the real cut runs full-power
    over every mm, including the start zone. (GRBL laser mode only fires while
    moving, so you can't just dwell.)

This tool does that. Input is an SVG of cut paths — polygonal SVGs (e.g.
OpenSCAD 2D export) are ideal; straight segments (M/L/H/V/Z) are exact and
curves (C/S/Q/T/A) are flattened to segments. Output is validator-clean GRBL
laser G-code ($32=1, G21 mm, G90 abs, static M3, S in [0,1000]).

Cut order: interior loops first (smallest area first), the outer boundary last,
so the workpiece stays attached to the sheet until the final pass.

Examples:
  svg2laser.py cut.svg -o cut.gcode --material mdf_3mm
  svg2laser.py cut.svg --feed 350 --power 100 --passes 2 --warmup-ms 1000
  svg2laser.py cut.svg --material mdf_3mm --origin center

Material profiles are read from the shared ../material_profiles/laser_materials.yaml
at the repo root (same format as the jigsawzall/orpot tools). --feed/--power/--passes
override the profile, or let you skip it entirely.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

# Diode cold-start ramp (ms) to reach full optical power after the beam fires.
DEFAULT_WARMUP_MS = 1000.0

# Shared motion + material helpers live in the sibling quickcut package.
sys.path.insert(0, str(SCRIPT_DIR.parent / "quickcut"))
from materials import load_material  # noqa: E402
from motion import (  # noqa: E402
    decimate,
    follow_through,
    path_length,
    signed_area,
    warmup_wiggle,
)

# ---------------------------------------------------------------------------
# SVG parsing  ->  list of (points, closed) polylines in SVG user units
# ---------------------------------------------------------------------------

_NUM = r"[-+]?(?:[0-9]*\.[0-9]+|[0-9]+\.?)(?:[eE][-+]?[0-9]+)?"
_TOKEN = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|" + _NUM)
_CURVE_STEPS = 24  # samples per flattened curve/arc


def _nums(s: str) -> list[float]:
    return [float(x) for x in re.findall(_NUM, s)]


def _cubic(p0, p1, p2, p3, n=_CURVE_STEPS):
    out = []
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        out.append(
            (
                u * u * u * p0[0]
                + 3 * u * u * t * p1[0]
                + 3 * u * t * t * p2[0]
                + t * t * t * p3[0],
                u * u * u * p0[1]
                + 3 * u * u * t * p1[1]
                + 3 * u * t * t * p2[1]
                + t * t * t * p3[1],
            )
        )
    return out


def _quad(p0, p1, p2, n=_CURVE_STEPS):
    out = []
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        out.append(
            (
                u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
                u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1],
            )
        )
    return out


def _arc(p0, rx, ry, phi_deg, large, sweep, p1, n=_CURVE_STEPS):
    # SVG endpoint arc -> center parameterization (per SVG spec F.6).
    if rx == 0 or ry == 0 or p0 == p1:
        return [p1]
    phi = math.radians(phi_deg)
    cosp, sinp = math.cos(phi), math.sin(phi)
    dx, dy = (p0[0] - p1[0]) / 2, (p0[1] - p1[1]) / 2
    x1p = cosp * dx + sinp * dy
    y1p = -sinp * dx + cosp * dy
    rx, ry = abs(rx), abs(ry)
    lam = x1p * x1p / (rx * rx) + y1p * y1p / (ry * ry)
    if lam > 1:
        s = math.sqrt(lam)
        rx, ry = rx * s, ry * s
    num = max(rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p, 0)
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    co = (math.sqrt(num / den) if den else 0) * (-1 if large == sweep else 1)
    cxp, cyp = co * rx * y1p / ry, -co * ry * x1p / rx
    cx = cosp * cxp - sinp * cyp + (p0[0] + p1[0]) / 2
    cy = sinp * cxp + cosp * cyp + (p0[1] + p1[1]) / 2

    def ang(ux, uy, vx, vy):
        d = math.hypot(ux, uy) * math.hypot(vx, vy)
        c = max(-1, min(1, (ux * vx + uy * vy) / d)) if d else 1
        a = math.acos(c)
        return -a if (ux * vy - uy * vx) < 0 else a

    th0 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dth = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dth > 0:
        dth -= 2 * math.pi
    elif sweep and dth < 0:
        dth += 2 * math.pi
    out = []
    for i in range(1, n + 1):
        th = th0 + dth * i / n
        x = cosp * rx * math.cos(th) - sinp * ry * math.sin(th) + cx
        y = sinp * rx * math.cos(th) + cosp * ry * math.sin(th) + cy
        out.append((x, y))
    return out


def _parse_path(d: str) -> list[tuple[list, bool]]:
    """Parse an SVG path 'd' into a list of (points, closed) subpaths."""
    toks = _TOKEN.findall(d)
    subs, cur, cx, cy, sx, sy = [], [], 0.0, 0.0, 0.0, 0.0
    cmd = None
    prev_cubic = prev_quad = None
    i = 0

    def num():
        nonlocal i
        v = float(toks[i])
        i += 1
        return v

    while i < len(toks):
        t = toks[i]
        if re.match(r"[A-Za-z]", t):
            cmd = t
            i += 1
        rel = cmd.islower()
        C = cmd.upper()
        if C == "M":
            if cur:
                subs.append((cur, False))
            x = num() + (cx if rel else 0)
            y = num() + (cy if rel else 0)
            cx, cy, sx, sy = x, y, x, y
            cur = [(cx, cy)]
            cmd = "l" if rel else "L"  # subsequent pairs are implicit lineto
            prev_cubic = prev_quad = None
        elif C == "L":
            cx = num() + (cx if rel else 0)
            cy = num() + (cy if rel else 0)
            cur.append((cx, cy))
            prev_cubic = prev_quad = None
        elif C == "H":
            cx = num() + (cx if rel else 0)
            cur.append((cx, cy))
            prev_cubic = prev_quad = None
        elif C == "V":
            cy = num() + (cy if rel else 0)
            cur.append((cx, cy))
            prev_cubic = prev_quad = None
        elif C in ("C", "S"):
            if C == "C":
                x1 = num() + (cx if rel else 0)
                y1 = num() + (cy if rel else 0)
            else:
                x1 = 2 * cx - prev_cubic[0] if prev_cubic else cx
                y1 = 2 * cy - prev_cubic[1] if prev_cubic else cy
            x2 = num() + (cx if rel else 0)
            y2 = num() + (cy if rel else 0)
            x = num() + (cx if rel else 0)
            y = num() + (cy if rel else 0)
            cur += _cubic((cx, cy), (x1, y1), (x2, y2), (x, y))
            prev_cubic = (x2, y2)
            prev_quad = None
            cx, cy = x, y
        elif C in ("Q", "T"):
            if C == "Q":
                x1 = num() + (cx if rel else 0)
                y1 = num() + (cy if rel else 0)
            else:
                x1 = 2 * cx - prev_quad[0] if prev_quad else cx
                y1 = 2 * cy - prev_quad[1] if prev_quad else cy
            x = num() + (cx if rel else 0)
            y = num() + (cy if rel else 0)
            cur += _quad((cx, cy), (x1, y1), (x, y))
            prev_quad = (x1, y1)
            prev_cubic = None
            cx, cy = x, y
        elif C == "A":
            rx = num()
            ry = num()
            rot = num()
            large = num()
            sweep = num()
            x = num() + (cx if rel else 0)
            y = num() + (cy if rel else 0)
            cur += _arc((cx, cy), rx, ry, rot, int(large), int(sweep), (x, y))
            cx, cy = x, y
            prev_cubic = prev_quad = None
        elif C == "Z":
            if cur:
                subs.append((cur, True))
            cur = []
            cx, cy = sx, sy
            prev_cubic = prev_quad = None
        else:
            i += 1
    if cur:
        subs.append((cur, False))
    return subs


def _length_to_mm(s: str) -> float | None:
    m = re.match(r"\s*(" + _NUM + r")\s*([a-z%]*)\s*$", s or "")
    if not m:
        return None
    v = float(m.group(1))
    unit = m.group(2)
    return v * {
        "": 1,
        "px": 25.4 / 96,
        "mm": 1,
        "cm": 10,
        "in": 25.4,
        "pt": 25.4 / 72,
        "pc": 25.4 / 6,
    }.get(unit, 1)


def svg_subpaths(svg: str) -> tuple[list, float]:
    """Return (subpaths, scale). subpaths are (points, closed) in SVG user units;
    scale converts user units to mm (from width/height vs viewBox)."""
    vb = re.search(r'viewBox\s*=\s*"([^"]+)"', svg)
    w = re.search(r'\bwidth\s*=\s*"([^"]+)"', svg)
    h = re.search(r'\bheight\s*=\s*"([^"]+)"', svg)
    scale = 1.0
    if vb and w:
        _, _, vbw, _vbh = _nums(vb.group(1))[:4]
        wmm = _length_to_mm(w.group(1))
        if wmm and vbw:
            scale = wmm / vbw
    subs = []
    for d in re.findall(r'<path[^>]*\bd\s*=\s*"([^"]*)"', svg, re.S):
        subs += _parse_path(d)
    for pts in re.findall(r'<polygon[^>]*\bpoints\s*=\s*"([^"]*)"', svg):
        n = _nums(pts)
        subs.append(([(n[i], n[i + 1]) for i in range(0, len(n) - 1, 2)], True))
    for pts in re.findall(r'<polyline[^>]*\bpoints\s*=\s*"([^"]*)"', svg):
        n = _nums(pts)
        subs.append(([(n[i], n[i + 1]) for i in range(0, len(n) - 1, 2)], False))
    for ln in re.findall(r"<line\b[^>]*>", svg):
        a = dict(re.findall(r'\b(x1|y1|x2|y2)\s*=\s*"([^"]*)"', ln))
        if len(a) == 4:
            subs.append(
                (
                    [
                        (float(a["x1"]), float(a["y1"])),
                        (float(a["x2"]), float(a["y2"])),
                    ],
                    False,
                )
            )
    for rc in re.findall(r"<rect\b[^>]*>", svg):
        a = dict(re.findall(r'\b(x|y|width|height)\s*=\s*"([^"]*)"', rc))
        if "width" in a and "height" in a:
            x, y = float(a.get("x", 0)), float(a.get("y", 0))
            w2, h2 = float(a["width"]), float(a["height"])
            subs.append(([(x, y), (x + w2, y), (x + w2, y + h2), (x, y + h2)], True))
    for ci in re.findall(r"<circle\b[^>]*>", svg):
        a = dict(re.findall(r'\b(cx|cy|r)\s*=\s*"([^"]*)"', ci))
        if "r" in a:
            cx, cy, r = float(a.get("cx", 0)), float(a.get("cy", 0)), float(a["r"])
            subs.append(
                (
                    [
                        (
                            cx + r * math.cos(2 * math.pi * k / 64),
                            cy + r * math.sin(2 * math.pi * k / 64),
                        )
                        for k in range(65)
                    ],
                    True,
                )
            )
    return subs, scale


# ---------------------------------------------------------------------------
# Geometry + motion helpers
# ---------------------------------------------------------------------------
# signed_area, decimate, warmup_wiggle and follow_through are imported from
# quickcut/motion.py (the diode warmup/follow-through know-how lives there so it
# is shared with every vibes emitter).


# ---------------------------------------------------------------------------
# Emit
# ---------------------------------------------------------------------------


def emit(loops, feed, power_pct, passes, warmup_ms, min_seg, material_id):
    power_s = int(round(power_pct * 10))
    warmup_mm = warmup_ms / 60000.0 * feed
    L = [
        "; svg2laser: GRBL diode-laser cut (static M3 + warmup lead-in)",
        f"; feed={feed}mm/min power={power_pct}% passes={passes} "
        f"warmup={warmup_ms:.0f}ms ({warmup_mm:.1f}mm at F{feed})",
        "; closed loops: cut the loop then follow through {:.1f}mm past the start".format(
            warmup_mm
        ),
        "; open paths: out-and-back warmup wiggle over the start",
        "; order: interior loops first, outer boundary last",
        ";",
        ";HEAD: laser",
        f";MATERIAL: {material_id}",
        ";LASER_MODE: static  (M3 — constant power; assumes Z already at focus)",
        "",
        "$32=1   ; GRBL laser mode",
        "G21     ; mm",
        "G90     ; absolute",
        "M5      ; laser off",
        "G0 X0 Y0",
        "",
    ]
    for name, closed, pts in loops:
        pts = decimate(pts, min_seg)
        if len(pts) < 2:
            continue
        x0, y0 = pts[0]
        L.append(f"; --- {name} ---")
        L.append(f"G0 X{x0:.3f} Y{y0:.3f}")
        L.append(f"M3 S{power_s}")
        L.append(f"F{feed}")
        if closed:
            # Closed loop: trace it `passes` times in ONE direction — no ping-pong.
            # A loop ends where the next pass begins (its own start), so there's no
            # laser-on return chord to avoid; the head just keeps circling. After
            # the final pass returns to the start, follow through past it for
            # warmup_mm so the start region (cut cold on lap 1) is re-cut at full
            # power and the beam turns off in already-severed material — no snag.
            for p in range(passes):
                if passes > 1:
                    L.append(f"; pass {p + 1} of {passes}")
                for x, y in pts[1:]:
                    L.append(f"G1 X{x:.3f} Y{y:.3f}")
            if warmup_mm > 0:
                lead = follow_through(pts, warmup_mm)
                if lead:
                    L.append(
                        f"; follow-through {warmup_mm:.1f}mm past start (clean separation)"
                    )
                    for x, y in lead:
                        L.append(f"G1 X{x:.3f} Y{y:.3f}")
        else:
            # Open path: can't loop back through its own start, so (1) warm up with
            # an out-and-back wiggle over the start, then (2) ping-pong the passes —
            # cut forward, reverse back over the SAME path, alternating. The head is
            # already at the far end after each pass, so we never do a laser-on move
            # back to the start (which would slice a chord across the workpiece).
            for wx, wy in warmup_wiggle(pts, warmup_mm):
                L.append(f"G1 X{wx:.3f} Y{wy:.3f}")
            for p in range(passes):
                if passes > 1:
                    direction = "forward" if p % 2 == 0 else "reverse"
                    L.append(f"; pass {p + 1} of {passes} ({direction})")
                seq = pts[1:] if p % 2 == 0 else pts[-2::-1]
                for x, y in seq:
                    L.append(f"G1 X{x:.3f} Y{y:.3f}")
        L.append("M5")
        L.append("")
    L += ["G0 X0 Y0", ""]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="svg2laser",
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("svg", type=Path)
    ap.add_argument(
        "-o", "--out", type=Path, help="output .gcode (default: <svg>.gcode)"
    )
    ap.add_argument("--material", help="material id in the profile yaml")
    ap.add_argument(
        "--profile",
        type=Path,
        default=SCRIPT_DIR.parent / "material_profiles" / "laser_materials.yaml",
    )
    ap.add_argument("--feed", type=int, help="feed mm/min (overrides profile)")
    ap.add_argument(
        "--power", type=float, help="power percent 0-100 (overrides profile)"
    )
    ap.add_argument("--passes", type=int, help="cut passes (overrides profile)")
    ap.add_argument(
        "--warmup-ms",
        type=float,
        default=DEFAULT_WARMUP_MS,
        help=f"diode warmup lead-in (default {DEFAULT_WARMUP_MS:.0f}ms; 0 to disable)",
    )
    ap.add_argument(
        "--min-seg",
        type=float,
        default=0.3,
        help="decimate segments shorter than this mm",
    )
    ap.add_argument(
        "--margin",
        type=float,
        default=5.0,
        help="mm inset from origin (positive work area)",
    )
    ap.add_argument(
        "--origin",
        choices=["corner", "center"],
        default="corner",
        help="place min-corner at margin (corner) or center the work on X0Y0",
    )
    args = ap.parse_args()

    feed = args.feed
    power = args.power
    passes = args.passes
    mat_id = args.material or "custom"
    if args.material:
        mat = load_material(args.material, args.profile)
        feed = feed if feed is not None else mat["feed_mm_per_min"]
        power = power if power is not None else mat["power_percent"]
        passes = passes if passes is not None else mat["passes"]
    feed = feed or 350
    power = 100.0 if power is None else power
    passes = passes or 1
    if not 0 <= power <= 100:
        raise SystemExit("power must be 0-100")

    subs, scale = svg_subpaths(args.svg.read_text())
    if not subs:
        raise SystemExit("no cut paths found in SVG")

    # scale to mm, flip Y (SVG y-down -> machine y-up), shift to positive/centre.
    # machine = (x*scale + tx, ty - y*scale)
    all_pts = [(x * scale, y * scale) for pts, _ in subs for x, y in pts]
    minx = min(p[0] for p in all_pts)
    maxx = max(p[0] for p in all_pts)
    miny = min(p[1] for p in all_pts)
    maxy = max(p[1] for p in all_pts)
    if args.origin == "center":
        tx = -(minx + maxx) / 2  # work centred on X0 Y0
        ty = (miny + maxy) / 2
    else:
        tx = args.margin - minx  # min-corner at (margin, margin)
        ty = args.margin + maxy

    def xf(pts):
        return [(x * scale + tx, ty - y * scale) for x, y in pts]

    loops = []
    for idx, (pts, closed) in enumerate(subs):
        mpts = xf(pts)
        # close closed loops so the final edge is cut (SVG marks a loop closed
        # without repeating the first point -> otherwise the last side is skipped)
        if closed and len(mpts) >= 3 and mpts[0] != mpts[-1]:
            mpts = mpts + [mpts[0]]
        area = abs(signed_area(mpts)) if closed and len(mpts) >= 3 else 0.0
        loops.append((area, closed, mpts, idx))
    # interior/small loops first, outer boundary (largest area) last; open paths first
    loops.sort(key=lambda t: (t[0], t[3]))
    named = [
        (
            f"loop {i + 1} (area {a:.0f}mm^2)" if c else f"open path {i + 1}",
            c,
            p,
        )
        for i, (a, c, p, _) in enumerate(loops)
    ]

    gcode = emit(named, feed, power, passes, args.warmup_ms, args.min_seg, mat_id)
    out = args.out or args.svg.with_suffix(".gcode")
    out.write_text(gcode)
    total = sum(path_length(p) for _, _, p in named)
    print(f"-> {out}")
    print(
        f"   {len(named)} paths, {len(gcode.splitlines())} lines, "
        f"~{total / 1000:.1f}m cut, feed F{feed} x{passes} pass(es), "
        f"warmup {args.warmup_ms:.0f}ms"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
