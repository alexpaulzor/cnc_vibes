#!/usr/bin/env python3
"""orpot_gcode.py — one-command laser G-code for the orchid pot (single-kerf).

The spiral cuts must be single-kerf (traced once down the centerline), but
OpenSCAD 2D export can only produce filled areas — a thin spiral slot would be
traced on BOTH sides. So this wrapper:

  1. runs OpenSCAD in MODE="frame" to export the disc WITHOUT the spiral slots
     (just the outer circle + rib slots) plus the 4 ribs, as SVG outlines, and
  2. captures the spiral CENTERLINES that the .scad echoes,
  3. injects the centerlines into that SVG as open <polyline>s, and
  4. calls svg2laser.py, which cuts open polylines once (single kerf) and closed
     outlines once around — all with the diode static-M3 + warmup workaround.

Everything is driven by orpot.scad (single source of truth); the spiral echo
uses the same parameters as the cut, so they can't drift.

Usage:
  ./orpot_gcode.py --material mdf_3mm            # -> build/orpot.gcode
  ./orpot_gcode.py --material mdf_3mm --warmup-ms 1200 --origin center
  ./orpot_gcode.py --scad orpot.scad -o build/orpot.gcode -- --feed 300 --power 90

Anything after `--` (or any unrecognized flag) is passed through to svg2laser;
see `svg2laser.py -h`.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent
_NUM = r"[-+]?(?:[0-9]*\.[0-9]+|[0-9]+\.?)(?:[eE][-+]?[0-9]+)?"


def find_openscad(explicit: str | None) -> str:
    for c in ([explicit] if explicit else []) + [
        "openscad",
        "/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD",
        "/usr/bin/openscad",
        "/usr/local/bin/openscad",
    ]:
        if c and (shutil.which(c) or Path(c).exists()):
            return c
    raise SystemExit("openscad not found; pass --openscad /path/to/OpenSCAD")


def parse_spirals(stderr: str) -> list[list[tuple[float, float]]]:
    """Pull the echoed 'SPIRAL', [[x,y],...] point lists out of OpenSCAD output."""
    out = []
    for line in stderr.splitlines():
        if '"SPIRAL"' not in line:
            continue
        nums = [float(n) for n in re.findall(_NUM, line.split('"SPIRAL"', 1)[1])]
        out.append([(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)])
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="orpot_gcode",
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--scad", type=Path, default=DIR / "orpot.scad")
    ap.add_argument("--openscad", help="path to the OpenSCAD binary")
    ap.add_argument("-o", "--out", type=Path, default=DIR / "build" / "orpot.gcode")
    ap.add_argument(
        "--help-svg2laser",
        action="store_true",
        help="show svg2laser's own options (feed/power/passes/warmup/…)",
    )
    args, fwd = ap.parse_known_args()
    if args.help_svg2laser:
        return subprocess.run(
            [sys.executable, str(DIR / "svg2laser.py"), "-h"]
        ).returncode

    osc = find_openscad(args.openscad)
    build = args.out.parent
    build.mkdir(parents=True, exist_ok=True)
    frame_svg = build / "orpot_frame.svg"
    combined = build / "orpot_combined.svg"

    # 1+2: export the frame SVG and capture the spiral-centerline echo
    proc = subprocess.run(
        [osc, "-o", str(frame_svg), "-D", 'MODE="frame"', str(args.scad)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit("OpenSCAD frame export failed")
    spirals = parse_spirals(proc.stderr)
    if not spirals:
        raise SystemExit(
            'no spiral centerlines echoed — is MODE="frame" wired in orpot.scad?'
        )

    # 3: inject the spiral centerlines as open polylines. OpenSCAD's SVG export
    # negates Y (y-down); the echo is in OpenSCAD Y-up coords, so negate Y here
    # to land the spirals in the same frame as the SVG paths.
    svg = frame_svg.read_text()
    polylines = "\n".join(
        '<polyline fill="none" stroke="black" stroke-width="0.1" points="'
        + " ".join(f"{x:.4f},{-y:.4f}" for x, y in pts)
        + '"/>'
        for pts in spirals
    )
    combined.write_text(svg.replace("</svg>", polylines + "\n</svg>"))

    # guard: if parts overlap in the layout they fuse into one outline (a rib
    # merges into the disc), so a too-small layout_side can't silently ruin a cut.
    # A merge makes the largest closed loop = disc + rib area, well over the disc.
    m = re.search(r'"DISC_AREA",\s*([0-9.]+)', proc.stderr)
    if m:
        sys.path.insert(0, str(DIR))
        import svg2laser as _s

        disc_area = float(m.group(1))
        areas = [
            abs(_s.signed_area(p))
            for p, c in _s.svg_subpaths(combined.read_text())[0]
            if c
        ]
        biggest = max(areas) if areas else 0
        if biggest > disc_area * 1.05:
            raise SystemExit(
                f"parts overlap: a closed outline is {biggest:.0f}mm^2 vs the disc's "
                f"{disc_area:.0f}mm^2 — a rib is fused into the disc. Increase "
                "layout_side in orpot.scad and re-run."
            )

    # 4: hand off to svg2laser (open polylines -> single-kerf; closed -> outline)
    cmd = [
        sys.executable,
        str(DIR / "svg2laser.py"),
        str(combined),
        "-o",
        str(args.out),
    ] + [a for a in fwd if a != "--"]
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd).returncode


if __name__ == "__main__":
    sys.exit(main())
