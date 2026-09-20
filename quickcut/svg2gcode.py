#!/usr/bin/env python3
"""svg2gcode — turn an SVG of cut paths into GRBL G-code for a weak diode laser.

A weak (~10W) diode needs the opposite of the CO2 assumptions most SVG->G-code
tools bake in. This one bakes in the techniques that actually cut clean on one:

  * STATIC M3 constant power. M4 scales power with feedrate, and a weak diode
    never reaches cutting threshold that way — so we hold S constant.
  * A warmup lead-in to cover the diode's ~1s cold-start ramp (GRBL laser mode
    only fires while moving, so you can't just dwell):
      - closed loops trace the loop, then FOLLOW THROUGH past the start so the
        cold-cut start region is re-cut hot and the part releases with no snag;
      - open paths trace an out-and-back WIGGLE over the start before cutting.
  * Ping-pong passes on open paths (forward, then reverse over the same points)
    so a multi-pass cut never fires the laser on a move back to the start.
  * Cut ordering: interior loops first (smallest area first), outer boundary
    last, so the workpiece stays attached to the sheet until the final pass.

Power is a percentage of an explicit full-power S value (--s-full-power, default
1000) that must match your controller's GRBL $30 setting. Check yours with `$$`.

The whole pipeline, top to bottom (read svg_to_gcode() first — everything else
is a named subroutine it calls):

    svg text  --parse_svg-------->  polylines in SVG units
              --place_on_bed----->  polylines in machine mm (Y-up, positive)
              --order_cuts------->  interior loops first, boundary last
              --emit_gcode------->  validator-clean GRBL text

Input is an SVG of cut paths; an OpenSCAD 2D export is ideal. Straight segments
(M/L/H/V/Z) are exact; curves (C/S/Q/T/A) are flattened to short segments.

Examples:
    svg2gcode.py cut.svg -o cut.gcode --material mdf_3mm
    svg2gcode.py cut.svg --feed 350 --power 100 --passes 2 --warmup-ms 1000
    svg2gcode.py cut.svg --material mdf_3mm --origin center --s-full-power 1000
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from materials import SHARED_MATERIALS, load_material  # noqa: E402
from motion import (  # noqa: E402
    decimate,
    follow_through,
    path_length,
    points_along,
    signed_area,
    warmup_wiggle,
)

# The diode's cold-start ramp: time from the beam firing to full optical power.
DEFAULT_WARMUP_MS = 1000.0


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Polyline:
    """A single cut path: a list of (x, y) points, and whether it is a closed
    loop (last point joins back to the first) or an open path."""

    points: list[tuple[float, float]]
    closed: bool


@dataclass
class CutJob:
    """Everything the emitter needs that is NOT geometry: the cutting recipe."""

    feed_mm_per_min: int
    power_percent: float
    passes: int
    warmup_ms: float = DEFAULT_WARMUP_MS
    min_segment_mm: float = 0.3
    material_id: str = "custom"
    # The S value that means 100% power. Must equal your controller's GRBL $30
    # (S range 0..$30). Most GRBL laser setups use 1000, so S1000 = full power.
    s_full_power: int = 1000

    @property
    def power_s(self) -> int:
        """The S word to command, scaled from percent to the full-power ceiling."""
        return round(self.power_percent / 100.0 * self.s_full_power)

    @property
    def warmup_mm(self) -> float:
        """Warmup expressed as travel distance (the beam ramps over time, and the
        head is moving at the feedrate, so time * feed = distance)."""
        return self.warmup_ms / 60_000.0 * self.feed_mm_per_min


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------


def svg_to_gcode(
    svg_text: str,
    job: CutJob,
    origin: str = "corner",
    margin_mm: float = 5.0,
) -> str:
    """SVG text in, GRBL G-code text out. This is the whole program in four steps."""
    polylines, units_to_mm = parse_svg(svg_text)
    if not polylines:
        raise ValueError("no cut paths found in SVG")
    on_bed = place_on_bed(polylines, units_to_mm, origin, margin_mm)
    ordered = order_cuts(on_bed)
    return emit_gcode(ordered, job)


def place_on_bed(
    polylines: list[Polyline],
    units_to_mm: float,
    origin: str,
    margin_mm: float,
) -> list[Polyline]:
    """Scale SVG units to mm, flip Y (SVG is y-down, the machine is y-up), and
    shift the whole drawing into positive machine coordinates."""
    scaled = [
        [(x * units_to_mm, y * units_to_mm) for x, y in line.points]
        for line in polylines
    ]
    every_point = [point for line in scaled for point in line]
    min_x = min(x for x, _ in every_point)
    max_x = max(x for x, _ in every_point)
    min_y = min(y for _, y in every_point)
    max_y = max(y for _, y in every_point)

    if origin == "center":
        shift_x = -(min_x + max_x) / 2  # drawing centered on X0 Y0
        flip_about_y = (min_y + max_y) / 2
    else:  # "corner": min-corner sits at (margin, margin)
        shift_x = margin_mm - min_x
        flip_about_y = margin_mm + max_y

    def to_machine(point: tuple[float, float]) -> tuple[float, float]:
        x, y = point
        return (x + shift_x, flip_about_y - y)

    placed = []
    for scaled_points, source in zip(scaled, polylines):
        machine_points = [to_machine(point) for point in scaled_points]
        # An SVG closed loop marks itself closed WITHOUT repeating the first point;
        # repeat it so the final edge actually gets cut.
        if (
            source.closed
            and len(machine_points) >= 3
            and machine_points[0] != machine_points[-1]
        ):
            machine_points.append(machine_points[0])
        placed.append(Polyline(machine_points, source.closed))
    return placed


def order_cuts(polylines: list[Polyline]) -> list[Polyline]:
    """Interior loops (smallest area) first, outer boundary last, so the part
    stays attached to the sheet until the very end. Open paths (area 0) lead.
    Sort is stable, so equal-area paths keep their original SVG order."""
    return sorted(polylines, key=loop_area)


def emit_gcode(cuts: list[Polyline], job: CutJob) -> str:
    """Assemble the full G-code file: one preamble, one motion block per cut,
    one footer."""
    lines = _preamble(job)
    for index, cut in enumerate(cuts, start=1):
        lines += _emit_cut(cut, index, len(cuts), job)
    lines += _footer()
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Emitting one cut
# ---------------------------------------------------------------------------


def _emit_cut(cut: Polyline, index: int, total: int, job: CutJob) -> list[str]:
    """Move to the start, turn the beam on, cut (closed vs open differ), turn off."""
    points = decimate(cut.points, job.min_segment_mm)
    if len(points) < 2:
        return []
    start_x, start_y = points[0]
    kind = "loop" if cut.closed else "open path"
    motion = (
        _closed_loop_motion(points, job)
        if cut.closed
        else _open_path_motion(points, job)
    )
    return [
        f"; --- cut {index}/{total} ({kind}, {len(points)} pts) ---",
        f"G0 X{start_x:.3f} Y{start_y:.3f}",
        f"M3 S{job.power_s}",
        f"F{job.feed_mm_per_min}",
        *motion,
        "M5",
        "",
    ]


def _closed_loop_motion(points: list[tuple[float, float]], job: CutJob) -> list[str]:
    """Trace the loop `passes` times in ONE direction (a loop returns to its own
    start, so there is no laser-on return chord to avoid — no ping-pong needed),
    then follow through past the start to re-cut the cold-start region hot."""
    lines: list[str] = []
    for pass_number in range(1, job.passes + 1):
        if job.passes > 1:
            lines.append(f"; pass {pass_number}/{job.passes}")
        lines += _cut_through(points[1:])
    lead = follow_through(points, job.warmup_mm)
    if lead:
        lines.append(
            f"; follow-through {job.warmup_mm:.1f}mm past start (clean release)"
        )
        lines += _cut_through(lead)
    return lines


def _open_path_motion(points: list[tuple[float, float]], job: CutJob) -> list[str]:
    """Warm the beam up with an out-and-back wiggle over the start (an open path
    can't loop back through it), then ping-pong the passes so we never fire the
    laser on a move back to the start."""
    lines = _cut_through(warmup_wiggle(points, job.warmup_mm))
    for pass_number in range(1, job.passes + 1):
        going_forward = pass_number % 2 == 1
        if job.passes > 1:
            direction = "forward" if going_forward else "reverse"
            lines.append(f"; pass {pass_number}/{job.passes} ({direction})")
        sequence = points[1:] if going_forward else points[-2::-1]
        lines += _cut_through(sequence)
    return lines


def _cut_through(points: list[tuple[float, float]]) -> list[str]:
    """Beam-on linear moves through a sequence of points."""
    return [f"G1 X{x:.3f} Y{y:.3f}" for x, y in points]


def _preamble(job: CutJob) -> list[str]:
    return [
        "; svg2gcode: GRBL diode-laser cut (static M3 + warmup lead-in)",
        f"; feed={job.feed_mm_per_min}mm/min power={job.power_percent}% "
        f"(S{job.power_s} of {job.s_full_power}) passes={job.passes}",
        f"; warmup={job.warmup_ms:.0f}ms = {job.warmup_mm:.1f}mm at this feed",
        "; closed loops: N same-direction laps, then follow-through past the start",
        "; open paths: out-and-back warmup wiggle, then ping-pong passes",
        "; order: interior loops first, outer boundary last",
        ";",
        ";HEAD: laser",
        f";MATERIAL: {job.material_id}",
        ";LASER_MODE: static  (M3 — constant power; assumes Z already at focus)",
        "",
        f"$32=1   ; GRBL laser mode (assumes firmware $30={job.s_full_power}; set once, not here)",
        "G21     ; mm",
        "G90     ; absolute",
        "M5      ; laser off",
        "G0 X0 Y0",
        "",
    ]


def _footer() -> list[str]:
    return ["G0 X0 Y0", ""]


# ---------------------------------------------------------------------------
# Cut ordering
# ---------------------------------------------------------------------------


def loop_area(line: Polyline) -> float:
    """Enclosed area of a closed loop (0 for open paths), used for cut ordering."""
    if line.closed and len(line.points) >= 3:
        return abs(signed_area(line.points))
    return 0.0


# ---------------------------------------------------------------------------
# SVG parsing  ->  polylines in SVG user units
# ---------------------------------------------------------------------------

_NUMBER = r"[-+]?(?:[0-9]*\.[0-9]+|[0-9]+\.?)(?:[eE][-+]?[0-9]+)?"
_CURVE_STEPS = 24  # samples per flattened bezier/arc


def parse_svg(svg_text: str) -> tuple[list[Polyline], float]:
    """Return (polylines in SVG user units, units_to_mm scale). Handles <path>,
    <polygon>, and <polyline>; those are what OpenSCAD 2D export and most cut
    files emit. <path> supports lines and curves; the rest are already polygonal."""
    polylines: list[Polyline] = []
    for d_attribute in re.findall(r'<path[^>]*\bd\s*=\s*"([^"]*)"', svg_text, re.S):
        polylines += _parse_path_data(d_attribute)
    for pts in re.findall(r'<polygon[^>]*\bpoints\s*=\s*"([^"]*)"', svg_text):
        polylines.append(Polyline(_point_pairs(pts), closed=True))
    for pts in re.findall(r'<polyline[^>]*\bpoints\s*=\s*"([^"]*)"', svg_text):
        polylines.append(Polyline(_point_pairs(pts), closed=False))
    return polylines, _units_to_mm(svg_text)


def _point_pairs(raw: str) -> list[tuple[float, float]]:
    numbers = [float(n) for n in re.findall(_NUMBER, raw)]
    return [(numbers[i], numbers[i + 1]) for i in range(0, len(numbers) - 1, 2)]


def _units_to_mm(svg_text: str) -> float:
    """Scale from SVG user units to mm, from width= vs viewBox width. Defaults to
    1 (OpenSCAD exports user units == mm)."""
    view_box = re.search(r'viewBox\s*=\s*"([^"]+)"', svg_text)
    width = re.search(r'\bwidth\s*=\s*"([^"]+)"', svg_text)
    if not (view_box and width):
        return 1.0
    view_box_width = [float(n) for n in re.findall(_NUMBER, view_box.group(1))][2]
    width_mm = _length_to_mm(width.group(1))
    if width_mm and view_box_width:
        return width_mm / view_box_width
    return 1.0


def _length_to_mm(length: str) -> float | None:
    match = re.match(r"\s*(" + _NUMBER + r")\s*([a-z%]*)\s*$", length or "")
    if not match:
        return None
    unit_to_mm = {
        "": 1,
        "px": 25.4 / 96,
        "mm": 1,
        "cm": 10,
        "in": 25.4,
        "pt": 25.4 / 72,
        "pc": 25.4 / 6,
    }
    return float(match.group(1)) * unit_to_mm.get(match.group(2), 1)


def _parse_path_data(d: str) -> list[Polyline]:
    """Parse an SVG path 'd' string into one or more Polylines. Curves are
    flattened to short line segments; Z closes the current subpath."""
    tokens = re.findall(r"[MmLlHhVvCcSsQqTtAaZz]|" + _NUMBER, d)
    index = 0

    def next_number() -> float:
        nonlocal index
        value = float(tokens[index])
        index += 1
        return value

    subpaths: list[Polyline] = []
    current: list[tuple[float, float]] = []
    x = y = start_x = start_y = 0.0
    command = None
    last_cubic_control = last_quad_control = None

    while index < len(tokens):
        if re.match(r"[A-Za-z]", tokens[index]):
            command = tokens[index]
            index += 1
        relative = command.islower()
        base = command.upper()
        origin_x, origin_y = (x, y) if relative else (0.0, 0.0)

        if base == "M":
            if current:
                subpaths.append(Polyline(current, closed=False))
            x, y = next_number() + origin_x, next_number() + origin_y
            start_x, start_y = x, y
            current = [(x, y)]
            command = "l" if relative else "L"  # extra pairs after M are lineto
            last_cubic_control = last_quad_control = None
        elif base == "L":
            x, y = next_number() + origin_x, next_number() + origin_y
            current.append((x, y))
            last_cubic_control = last_quad_control = None
        elif base == "H":
            x = next_number() + origin_x
            current.append((x, y))
            last_cubic_control = last_quad_control = None
        elif base == "V":
            y = next_number() + origin_y
            current.append((x, y))
            last_cubic_control = last_quad_control = None
        elif base in ("C", "S"):
            if base == "C":
                control1 = (next_number() + origin_x, next_number() + origin_y)
            else:  # smooth: reflect the previous cubic control point
                control1 = _reflect(last_cubic_control, (x, y))
            control2 = (next_number() + origin_x, next_number() + origin_y)
            end = (next_number() + origin_x, next_number() + origin_y)
            current += _flatten_cubic((x, y), control1, control2, end)
            last_cubic_control, last_quad_control = control2, None
            x, y = end
        elif base in ("Q", "T"):
            if base == "Q":
                control = (next_number() + origin_x, next_number() + origin_y)
            else:  # smooth: reflect the previous quadratic control point
                control = _reflect(last_quad_control, (x, y))
            end = (next_number() + origin_x, next_number() + origin_y)
            current += _flatten_quadratic((x, y), control, end)
            last_quad_control, last_cubic_control = control, None
            x, y = end
        elif base == "A":
            radius_x, radius_y = next_number(), next_number()
            rotation = next_number()
            large_arc, sweep = int(next_number()), int(next_number())
            end = (next_number() + origin_x, next_number() + origin_y)
            current += _flatten_arc(
                (x, y), radius_x, radius_y, rotation, large_arc, sweep, end
            )
            x, y = end
            last_cubic_control = last_quad_control = None
        elif base == "Z":
            if current:
                subpaths.append(Polyline(current, closed=True))
            current = []
            x, y = start_x, start_y
            last_cubic_control = last_quad_control = None
        else:
            index += 1

    if current:
        subpaths.append(Polyline(current, closed=False))
    return subpaths


def _reflect(control, about):
    """Reflect a control point across `about` (for smooth S/T commands)."""
    if control is None:
        return about
    return (2 * about[0] - control[0], 2 * about[1] - control[1])


def _flatten_cubic(p0, p1, p2, p3, steps=_CURVE_STEPS):
    result = []
    for step in range(1, steps + 1):
        t = step / steps
        u = 1 - t
        result.append(
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
    return result


def _flatten_quadratic(p0, p1, p2, steps=_CURVE_STEPS):
    result = []
    for step in range(1, steps + 1):
        t = step / steps
        u = 1 - t
        result.append(
            (
                u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
                u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1],
            )
        )
    return result


def _flatten_arc(
    p0, radius_x, radius_y, rotation_deg, large_arc, sweep, p1, steps=_CURVE_STEPS
):
    """SVG endpoint-parameterized elliptical arc, flattened to segments (SVG spec
    appendix F.6)."""
    if radius_x == 0 or radius_y == 0 or p0 == p1:
        return [p1]
    phi = math.radians(rotation_deg)
    cos_phi, sin_phi = math.cos(phi), math.sin(phi)
    half_dx, half_dy = (p0[0] - p1[0]) / 2, (p0[1] - p1[1]) / 2
    x1p = cos_phi * half_dx + sin_phi * half_dy
    y1p = -sin_phi * half_dx + cos_phi * half_dy
    radius_x, radius_y = abs(radius_x), abs(radius_y)
    oversize = x1p * x1p / (radius_x * radius_x) + y1p * y1p / (radius_y * radius_y)
    if oversize > 1:
        scale = math.sqrt(oversize)
        radius_x, radius_y = radius_x * scale, radius_y * scale
    numerator = max(
        radius_x * radius_x * radius_y * radius_y
        - radius_x * radius_x * y1p * y1p
        - radius_y * radius_y * x1p * x1p,
        0,
    )
    denominator = radius_x * radius_x * y1p * y1p + radius_y * radius_y * x1p * x1p
    coefficient = (math.sqrt(numerator / denominator) if denominator else 0) * (
        -1 if large_arc == sweep else 1
    )
    center_xp = coefficient * radius_x * y1p / radius_y
    center_yp = -coefficient * radius_y * x1p / radius_x
    center_x = cos_phi * center_xp - sin_phi * center_yp + (p0[0] + p1[0]) / 2
    center_y = sin_phi * center_xp + cos_phi * center_yp + (p0[1] + p1[1]) / 2

    def angle(ux, uy, vx, vy):
        magnitude = math.hypot(ux, uy) * math.hypot(vx, vy)
        cosine = max(-1, min(1, (ux * vx + uy * vy) / magnitude)) if magnitude else 1
        theta = math.acos(cosine)
        return -theta if (ux * vy - uy * vx) < 0 else theta

    theta0 = angle(1, 0, (x1p - center_xp) / radius_x, (y1p - center_yp) / radius_y)
    sweep_angle = angle(
        (x1p - center_xp) / radius_x,
        (y1p - center_yp) / radius_y,
        (-x1p - center_xp) / radius_x,
        (-y1p - center_yp) / radius_y,
    )
    if not sweep and sweep_angle > 0:
        sweep_angle -= 2 * math.pi
    elif sweep and sweep_angle < 0:
        sweep_angle += 2 * math.pi

    result = []
    for step in range(1, steps + 1):
        theta = theta0 + sweep_angle * step / steps
        result.append(
            (
                cos_phi * radius_x * math.cos(theta)
                - sin_phi * radius_y * math.sin(theta)
                + center_x,
                sin_phi * radius_x * math.cos(theta)
                + cos_phi * radius_y * math.sin(theta)
                + center_y,
            )
        )
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="svg2gcode",
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("svg", type=Path)
    parser.add_argument(
        "-o", "--out", type=Path, help="output .gcode (default: <svg>.gcode)"
    )
    parser.add_argument("--material", help="material id in the profile yaml")
    parser.add_argument("--profile", type=Path, default=SHARED_MATERIALS)
    parser.add_argument("--feed", type=int, help="feed mm/min (overrides profile)")
    parser.add_argument(
        "--power", type=float, help="power percent 0-100 (overrides profile)"
    )
    parser.add_argument("--passes", type=int, help="cut passes (overrides profile)")
    parser.add_argument(
        "--s-full-power",
        type=int,
        default=1000,
        help="S value that means 100%% power; must match GRBL $30 (default 1000)",
    )
    parser.add_argument(
        "--warmup-ms",
        type=float,
        default=DEFAULT_WARMUP_MS,
        help=f"diode warmup lead-in (default {DEFAULT_WARMUP_MS:.0f}ms; 0 to disable)",
    )
    parser.add_argument(
        "--min-seg",
        type=float,
        default=0.3,
        help="decimate segments shorter than this mm",
    )
    parser.add_argument(
        "--margin",
        type=float,
        default=5.0,
        help="mm inset from origin (corner mode)",
    )
    parser.add_argument(
        "--origin",
        choices=["corner", "center"],
        default="corner",
        help="min-corner at margin (corner) or center the work on X0 Y0",
    )
    args = parser.parse_args(argv)

    feed, power, passes = args.feed, args.power, args.passes
    material_id = args.material or "custom"
    if args.material:
        recipe = load_material(args.material, args.profile)
        feed = feed if feed is not None else recipe["feed_mm_per_min"]
        power = power if power is not None else recipe["power_percent"]
        passes = passes if passes is not None else recipe.get("passes", 1)
    feed = feed or 350
    power = 100.0 if power is None else power
    passes = passes or 1
    if not 0 <= power <= 100:
        raise SystemExit("power must be 0-100")

    job = CutJob(
        feed_mm_per_min=feed,
        power_percent=power,
        passes=passes,
        warmup_ms=args.warmup_ms,
        min_segment_mm=args.min_seg,
        material_id=material_id,
        s_full_power=args.s_full_power,
    )

    gcode = svg_to_gcode(
        args.svg.read_text(), job, origin=args.origin, margin_mm=args.margin
    )
    out_path = args.out or args.svg.with_suffix(".gcode")
    out_path.write_text(gcode)

    print(f"-> {out_path}")
    print(
        f"   {len(gcode.splitlines())} lines, feed F{feed} x{passes} pass(es), "
        f"power {power:.0f}% (S{job.power_s}/{job.s_full_power}), warmup {args.warmup_ms:.0f}ms"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
