#!/usr/bin/env python3
"""Spiral laser calibration card.

Sweeps one of (power, feed, passes) over a list of values, laying out
one test patch per value. Patches are 15mm circles with a double
Archimedean spiral inside them — when the patch cuts through, the
center disk falls out as several pie-slice pieces, giving you instant
visual confirmation of through-cut quality.

The patches are arranged in a hex spiral starting at WCS origin (0, 0),
so you can place a small chunk of scrap under the laser head, jog to a
free spot at its center, and run the sweep without wasting an entire
sheet of fresh material.

Usage:
  python spiral_cal.py --sweep power --values 30,40,50,60,70 \\
      --material cardboard_thin_1mm --laser-mode static

  python spiral_cal.py --sweep feed --values 1500,2000,2500,3000,3500 \\
      --material plywood_baltic_birch_3mm --power 80 \\
      --laser-mode static --laser-warmup-ms 250

  python spiral_cal.py --sweep passes --values 1,2,3,4 \\
      --material plywood_baltic_birch_6mm --power 100 --feed 200 \\
      --laser-mode static --laser-warmup-ms 300

  python spiral_cal.py interactive       # prompts walk you through the same

After cutting, look at each patch:
  - Did the center pieces fall out? (through-cut)
  - Edges clean or charred? (overpowered)
  - Top scored but back uncut? (underpowered or too fast)
  - Pick the leanest setting that cleanly drops the pieces.

Z (focus) is NOT swept here — the laser has no Z control. Change the
spacer manually between runs and re-cut a sweep if you want to compare
focus distances.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

LESSON_DIR = Path(__file__).resolve().parent
REPO_ROOT = LESSON_DIR.parent.parent.parent

sys.path.insert(0, str(REPO_ROOT / "scripts"))
from laser_cam import (  # noqa: E402
    LaserMaterial,
    _laser_footer,
    _laser_header,
    _power_code,
    _power_s,
    load_laser_material,
)

PATCH_OUTER_DIAMETER_MM = 15.0
PATCH_GAP_MM = 2.0
SPIRAL_TURNS = 2.5  # turns of EACH of the two interleaved spirals
SPIRAL_INNER_R_MM = 0.5  # spirals start at this radius (small center piece)
SPIRAL_POINTS = 180  # vertices per spiral arm


# ---------------------------------------------------------------------------
# Patch layout — hex-spiral of centers starting at origin
# ---------------------------------------------------------------------------


def patch_centers(
    n: int,
    patch_diameter_mm: float = PATCH_OUTER_DIAMETER_MM,
    gap_mm: float = PATCH_GAP_MM,
) -> list[tuple[float, float]]:
    """Return n patch centers laid out in concentric rings from origin.

    Ring 0: center.
    Ring K: 6K patches evenly spaced on a circle of radius K * pitch,
    where pitch = patch_diameter + gap. Tight enough that adjacent
    patch edges (within a ring AND across rings) sit ~gap apart.
    """
    if n <= 0:
        return []
    pitch = patch_diameter_mm + gap_mm
    centers: list[tuple[float, float]] = [(0.0, 0.0)]
    ring = 1
    while len(centers) < n:
        n_in_ring = 6 * ring
        r = ring * pitch
        for i in range(n_in_ring):
            angle = 2 * math.pi * i / n_in_ring
            centers.append((r * math.cos(angle), r * math.sin(angle)))
            if len(centers) >= n:
                break
        ring += 1
    return centers[:n]


# ---------------------------------------------------------------------------
# Patch geometry — outer circle + double Archimedean spiral
# ---------------------------------------------------------------------------


def _circle_points(
    cx: float, cy: float, r: float, segments: int = 64
) -> list[tuple[float, float]]:
    return [
        (
            cx + r * math.cos(2 * math.pi * i / segments),
            cy + r * math.sin(2 * math.pi * i / segments),
        )
        for i in range(segments + 1)
    ]


def _spiral_points(
    cx: float,
    cy: float,
    r_min: float,
    r_max: float,
    n_turns: float,
    offset_rad: float,
    n_points: int = SPIRAL_POINTS,
) -> list[tuple[float, float]]:
    """Archimedean spiral from r_min at θ=0 to r_max at θ=n_turns·2π,
    rotated by offset_rad. Returned center→outward (caller can reverse)."""
    theta_max = n_turns * 2 * math.pi
    pts = []
    for i in range(n_points + 1):
        t = i / n_points
        theta = t * theta_max + offset_rad
        r = r_min + t * (r_max - r_min)
        pts.append((cx + r * math.cos(theta), cy + r * math.sin(theta)))
    return pts


# ---------------------------------------------------------------------------
# Per-patch GCode emission
# ---------------------------------------------------------------------------


def _emit_patch(
    cx: float,
    cy: float,
    s: int,
    feed: int,
    passes: int,
    mode: str,
    warmup_ms: int,
    z_mm: float | None = None,
) -> list[str]:
    """Emit GCode for a single test patch at (cx, cy).

    z_mm: if not None, prepends a G0 Z<z_mm> rapid before any XY motion
    in this patch — for sweeping focal distance via the CNC's Z axis.
    """
    on = _power_code(mode)
    warmup_s = max(0, warmup_ms) / 1000.0
    r_outer = PATCH_OUTER_DIAMETER_MM / 2

    prologue: list[str] = []
    if z_mm is not None:
        prologue.append(f"G0 Z{z_mm:.3f}")

    def _trace(name: str, pts: list[tuple[float, float]]) -> list[str]:
        if len(pts) < 2:
            return []
        x0, y0 = pts[0]
        lines = [
            f"; -- {name} --",
            "M5",
            f"G0 X{x0:.3f} Y{y0:.3f}",
            f"{on} S{s}",
            f"F{feed}",
        ]
        if warmup_ms > 0:
            lines.append(f"G4 P{warmup_s:.3f}  ; warmup")
        for p in range(passes):
            if passes > 1:
                lines.append(f"; pass {p + 1}/{passes}")
            for x, y in pts[1:]:
                lines.append(f"G1 X{x:.3f} Y{y:.3f}")
        lines.append("M5")
        return lines

    spiral_r_max = r_outer - 0.5  # leave 0.5mm gap inside the boundary

    lines: list[str] = list(prologue)
    lines.extend(_trace("outer circle", _circle_points(cx, cy, r_outer)))
    lines.extend(
        _trace(
            "spiral A",
            _spiral_points(
                cx, cy, SPIRAL_INNER_R_MM, spiral_r_max, SPIRAL_TURNS, offset_rad=0.0
            ),
        )
    )
    lines.extend(
        _trace(
            "spiral B",
            _spiral_points(
                cx,
                cy,
                SPIRAL_INNER_R_MM,
                spiral_r_max,
                SPIRAL_TURNS,
                offset_rad=math.pi,
            ),
        )
    )
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Sweep orchestrator
# ---------------------------------------------------------------------------


@dataclass
class SweepResult:
    gcode: str
    layout: list[tuple[float, float, str]]  # (cx, cy, label) per patch


def _coerce(values: list, sweep_var: str) -> list:
    cast = float if sweep_var in ("power", "feed", "z") else int
    return [cast(v) for v in values]


def generate_sweep(
    material: LaserMaterial,
    sweep_var: str,
    values: list,
    mode: str = "dynamic",
    warmup_ms: int = 0,
    power_percent: float | None = None,
    feed_mm_per_min: int | None = None,
    passes: int | None = None,
    z_mm: float | None = None,
) -> SweepResult:
    """Emit one GCode file containing N patches, one per swept value.

    sweep_var="z" treats `values` as absolute Z coordinates in WCS mm
    and emits G0 Z<value> before each patch's first cut. Useful for
    finding the focal plane via the CNC's Z axis (the carriage moves;
    the laser spacer doesn't). Park at a known-safe Z first, then
    pick sweep values within reachable range — going too low CRASHES
    the laser head into the stock.

    z_mm: if set (and sweep_var != "z"), all patches use this absolute
    Z. Default None = leave Z wherever the user parked.
    """
    if sweep_var not in ("power", "feed", "passes", "z", "warmup"):
        raise SystemExit(
            f"--sweep must be power|feed|passes|z|warmup, got {sweep_var!r}"
        )
    if not values:
        raise SystemExit("--values must list at least one value")
    values = _coerce(values, sweep_var)

    base = {
        "power": power_percent if power_percent is not None else material.power_percent,
        "feed": feed_mm_per_min
        if feed_mm_per_min is not None
        else material.feed_mm_per_min,
        "passes": passes if passes is not None else material.passes,
    }

    centers = patch_centers(len(values))
    header = _laser_header("spiral_cal", material, mode)
    header.append(f"; sweep_var={sweep_var}  values={values}")
    header.append(
        f"; base: power={base['power']}%  feed={base['feed']}mm/min  passes={base['passes']}"
    )
    if sweep_var == "z":
        header.append(
            "; Z SWEEP: values are absolute WCS Z (mm). "
            "Park clear of stock before sending; crash risk if Z too low."
        )
    elif z_mm is not None:
        header.append(f"; All patches set Z={z_mm:.3f}mm before cutting.")
    header.append(
        f"; {len(values)} patch(es), {PATCH_OUTER_DIAMETER_MM}mm OD, "
        f"{PATCH_GAP_MM}mm gap, hex-spiral from origin"
    )
    if warmup_ms > 0:
        header.append(f"; warmup dwell {warmup_ms}ms per ring start")
    header.append("")
    header.append("; Patch layout (cut order):")
    layout: list[tuple[float, float, str]] = []
    for i, ((cx, cy), val) in enumerate(zip(centers, values), start=1):
        label = f"{sweep_var}={val}"
        header.append(f";   patch {i:2d}: ({cx:+7.2f}, {cy:+7.2f})  {label}")
        layout.append((cx, cy, label))
    header.append("")

    body: list[str] = []
    for i, ((cx, cy), val) in enumerate(zip(centers, values), start=1):
        params = dict(base)
        patch_z = z_mm
        patch_warmup = warmup_ms
        if sweep_var == "z":
            patch_z = float(val)
        elif sweep_var == "warmup":
            patch_warmup = int(val)
        else:
            params[sweep_var] = val
        s = _power_s(params["power"])
        body.append(
            f"; ===== patch {i}/{len(values)}: {sweep_var}={val} at ({cx:+.2f}, {cy:+.2f}) ====="
        )
        body.extend(
            _emit_patch(
                cx,
                cy,
                s=s,
                feed=int(params["feed"]),
                passes=max(1, int(params["passes"])),
                mode=mode,
                warmup_ms=patch_warmup,
                z_mm=patch_z,
            )
        )

    text = "\n".join(header + body + _laser_footer()) + "\n"
    return SweepResult(gcode=text, layout=layout)


# ---------------------------------------------------------------------------
# Interactive guided mode (asks the same questions the CLI would)
# ---------------------------------------------------------------------------


def _ask(prompt: str, default: str | None = None) -> str:
    sfx = f" [{default}]" if default is not None else ""
    while True:
        v = input(f"{prompt}{sfx}: ").strip()
        if not v and default is not None:
            return default
        if v:
            return v


def _ask_int(prompt: str, default: int | None = None) -> int:
    while True:
        try:
            return int(_ask(prompt, str(default) if default is not None else None))
        except ValueError:
            print("  not an integer")


def _ask_float(prompt: str, default: float | None = None) -> float:
    while True:
        try:
            return float(_ask(prompt, str(default) if default is not None else None))
        except ValueError:
            print("  not a number")


def interactive() -> argparse.Namespace:
    print("Spiral laser calibration — interactive mode\n")
    print("Park the laser over the CENTER of your scrap material before running.")
    print("All patches will spiral outward from WCS (0, 0).\n")

    args = argparse.Namespace()
    args.material = _ask(
        "Material id (from profiles/laser_materials.yaml)", "cardboard_thin_1mm"
    )
    args.sweep = _ask("Sweep what? (power|feed|passes|z|warmup)", "power")
    if args.sweep not in ("power", "feed", "passes", "z", "warmup"):
        sys.exit(f"invalid sweep {args.sweep!r}")

    units = {
        "power": "%",
        "feed": "mm/min",
        "passes": "passes",
        "z": "mm (WCS Z)",
        "warmup": "ms (G4 dwell)",
    }[args.sweep]
    print(f"Enter the {args.sweep} values to test in {units}, comma-separated.")
    if args.sweep == "power":
        ex = "30,40,50,60,70"
    elif args.sweep == "feed":
        ex = "1500,2000,2500,3000,3500"
    elif args.sweep == "z":
        ex = "-2,-1,0,1,2"
        print("  NOTE: values are ABSOLUTE WCS Z (mm). Park at a safe Z first.")
        print("  Going too low will crash the laser head into the stock.")
    elif args.sweep == "warmup":
        ex = "0,100,200,300,400"
        print("  Sweeps the cold-start dwell. Lower power so a too-short dwell")
        print("  visibly under-cuts; find the shortest dwell that cuts cleanly.")
    else:
        ex = "1,2,3,4"
    args.values = _ask(f"Values (e.g. {ex})")

    print("\nNon-swept params (defaults from material; press Enter to accept):")
    args.power = None if args.sweep == "power" else _ask_float("Power %", 50.0)
    args.feed = None if args.sweep == "feed" else _ask_int("Feed mm/min", 2500)
    args.passes = None if args.sweep == "passes" else _ask_int("Passes", 1)
    args.z = (
        None
        if args.sweep == "z"
        else _ask("Z mm (absolute WCS; blank = leave Z where parked)", "")
    )
    if args.z == "":
        args.z = None
    elif args.z is not None:
        args.z = float(args.z)

    args.laser_mode = _ask("Laser mode (dynamic|static)", "static")
    if args.sweep == "warmup":
        args.laser_warmup_ms = 0  # swept per-patch; base unused
    else:
        args.laser_warmup_ms = _ask_int(
            "Warmup dwell ms (200-300 for cold-start fade)", 250
        )

    args.out = None  # default path
    args.no_validate = False
    return args


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cnc.py cal-laser",
        description="Spiral laser calibration card — hex-spiral of double-spiral "
        "patches starting at WCS origin.",
    )
    p.add_argument("--material", help="id from profiles/laser_materials.yaml")
    p.add_argument(
        "--sweep",
        choices=("power", "feed", "passes", "z", "warmup"),
        help="which variable to vary across patches",
    )
    p.add_argument(
        "--values",
        help="comma-separated values for the swept variable. For --sweep z "
        "these are ABSOLUTE WCS Z (mm); going too low crashes the head. "
        "For --sweep warmup these are G4 dwell milliseconds per patch.",
    )
    p.add_argument(
        "--power", type=float, help="override power_percent (non-swept axis)"
    )
    p.add_argument("--feed", type=int, help="override feed_mm_per_min (non-swept axis)")
    p.add_argument("--passes", type=int, help="override passes (non-swept axis)")
    p.add_argument(
        "--z",
        type=float,
        default=None,
        help="absolute WCS Z (mm) for all patches when NOT sweeping z. "
        "Default: leave Z wherever the user parked. Ignored if --sweep z.",
    )
    p.add_argument(
        "--laser-mode",
        choices=("dynamic", "static"),
        default="static",
        help="M4 dynamic vs M3 static (default static — calibration "
        "results are easier to interpret with constant power)",
    )
    p.add_argument(
        "--laser-warmup-ms",
        type=int,
        default=250,
        help="G4 dwell after M3/M4 to defeat cold-start fade-in (default 250)",
    )
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--no-validate", action="store_true")

    sub = p.add_subparsers(dest="mode")
    sub.add_parser("interactive", help="prompt-driven setup (no flags needed)")
    return p


def _default_out_path(args) -> Path:
    sweep = args.sweep or "x"
    return LESSON_DIR / "build" / f"spiral_cal_{args.material}_{sweep}.gcode"


def _run_validator(path: Path) -> int:
    import subprocess

    r = subprocess.run(
        [sys.executable, str(REPO_ROOT / "cnc.py"), "validate", str(path)],
    )
    return r.returncode


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.mode == "interactive":
        args = interactive()

    if not args.material:
        sys.exit("error: --material is required (or use `interactive`)")
    if not args.sweep:
        sys.exit("error: --sweep is required (power|feed|passes|z|warmup)")
    if not args.values:
        sys.exit("error: --values is required (comma-separated)")

    material = load_laser_material(args.material)
    values = [v.strip() for v in args.values.split(",") if v.strip()]
    result = generate_sweep(
        material,
        args.sweep,
        values,
        mode=args.laser_mode,
        warmup_ms=args.laser_warmup_ms,
        power_percent=args.power,
        feed_mm_per_min=args.feed,
        passes=args.passes,
        z_mm=getattr(args, "z", None),
    )

    out = args.out or _default_out_path(args)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(result.gcode)
    print(f"-> wrote {out}  ({len(result.gcode.splitlines())} lines)")
    print(f"   {len(result.layout)} patch(es) laid out in a hex spiral from origin:")
    for i, (cx, cy, label) in enumerate(result.layout, start=1):
        print(f"     patch {i:2d}: ({cx:+7.2f}, {cy:+7.2f})  {label}")
    print()
    print("Park the laser at the CENTER of your scrap, then send this file.")

    if args.no_validate:
        return 0
    return _run_validator(out)


if __name__ == "__main__":
    sys.exit(main())
