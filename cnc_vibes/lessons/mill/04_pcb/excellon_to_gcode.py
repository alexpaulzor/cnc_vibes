#!/usr/bin/env python3
"""Convert a KiCAD-style Excellon drill file (.drl) into peck-drill GCode.

Excellon is the standard PCB drill file format — KiCAD, Eagle, all the
PCB CAD tools export it. This script reads one and emits cnc.py-
compatible drill GCode for the cnc_vibes pipeline.

For PCB **isolation routing** (cutting traces around copper islands)
use FlatCAM or pcb2gcode — that's a much harder problem and they do
it well. This tool covers only the drill side, which is structurally
identical to lesson 4c's center-punch except with multiple tool
diameters and full through-holes (peck drilling) instead of divets.

Usage:
  python excellon_to_gcode.py my_board.drl \\
      --copper-thickness 1.6 \\
      --spindle-rpm 12000

The script groups holes by tool diameter and emits a peck-drill
sequence per tool, with M0 pauses between tools so the operator can
swap drill bits.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

LESSON_DIR = Path(__file__).resolve().parent
REPO_ROOT = LESSON_DIR.parent.parent.parent
PROFILES = REPO_ROOT / "profiles"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
from job_params import load_yaml  # noqa: E402


SAFE_Z = 5.0
APPROACH_Z = 1.0


# ---------------------------------------------------------------------------
# Excellon parser — pure, testable.
# ---------------------------------------------------------------------------


@dataclass
class ExcellonTool:
    number: int
    diameter_mm: float
    holes: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class ExcellonFile:
    units: str = "mm"  # "mm" or "inch"
    tools: dict[int, ExcellonTool] = field(default_factory=dict)


def parse_excellon(text: str) -> ExcellonFile:
    """Parse a KiCAD-style Excellon drill file.

    Handles the common KiCAD output: M48 header, METRIC/INCH units,
    T<n>C<dia> tool definitions, % start-of-body, T<n> tool selects,
    X<n>Y<n> coordinates, M30 footer.
    """
    out = ExcellonFile()
    current_tool: int | None = None
    in_body = False

    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith(";"):
            continue
        if s == "M48":
            in_body = False
            continue
        if s in ("METRIC", "INCH"):
            out.units = "mm" if s == "METRIC" else "inch"
            continue
        if s == "%":
            in_body = True
            continue
        if s.startswith("M30") or s.startswith("M00"):
            break

        # Tool definition in header: T1C0.8 or T01C0.800
        m = re.match(r"^T(\d+)C([\d.]+)", s)
        if m and not in_body:
            num = int(m.group(1))
            dia = float(m.group(2))
            if out.units == "inch":
                dia *= 25.4
            out.tools[num] = ExcellonTool(number=num, diameter_mm=dia)
            continue

        # Tool select in body: T1
        m = re.match(r"^T(\d+)\s*$", s)
        if m and in_body:
            current_tool = int(m.group(1))
            continue

        # Coordinate line: X1.5Y2.5 or X+0001500Y+0002500 etc.
        m = re.match(r"^X(-?[\d.]+)Y(-?[\d.]+)\s*$", s)
        if m and in_body and current_tool is not None:
            x = float(m.group(1))
            y = float(m.group(2))
            if out.units == "inch":
                x *= 25.4
                y *= 25.4
            out.tools[current_tool].holes.append((x, y))
            continue

    return out


# ---------------------------------------------------------------------------
# GCode generation — peck drill per (x, y) per tool.
# ---------------------------------------------------------------------------


def generate_drill_gcode(
    drl: ExcellonFile,
    copper_thickness_mm: float,
    spindle_rpm: int,
    plunge_feed_mm_per_min: int,
    peck_depth_mm: float,
    machine: dict,
) -> str:
    """Generate GCode that drills every hole in `drl`.

    Holes are grouped by tool. Between tools the GCode emits M0 (pause)
    so the operator can swap drill bits.
    """
    if not drl.tools:
        sys.exit("error: no tools found in drill file")

    tools_with_holes = [t for t in drl.tools.values() if t.holes]
    if not tools_with_holes:
        sys.exit("error: no holes found in any tool")

    # Drill depth: copper + small overcut into sacrificial backer board
    final_z = -(copper_thickness_mm + 0.3)
    env = machine["envelope_mm"]

    header = [
        "; excellon_to_gcode.py — PCB drill GCode",
        f"; from excellon file with {len(drl.tools)} tool(s), "
        f"{sum(len(t.holes) for t in tools_with_holes)} total holes",
        f"; copper_thickness={copper_thickness_mm}mm  drill_to_Z={final_z:.2f}mm",
        ";",
        "$32=0",
        "G21",
        "G90",
        "M5",
        f"G0 Z{SAFE_Z:.3f}",
        "G0 X0 Y0",
        "",
    ]

    body = []
    for ti, tool in enumerate(sorted(tools_with_holes, key=lambda t: t.diameter_mm)):
        # Validate hole positions for this tool.
        for x, y in tool.holes:
            if not (0 <= x <= env["x"]):
                sys.exit(
                    f"error: tool T{tool.number} has hole at X={x} outside envelope"
                )
            if not (0 <= y <= env["y"]):
                sys.exit(
                    f"error: tool T{tool.number} has hole at Y={y} outside envelope"
                )

        body.append(
            f"; ==== tool T{tool.number}: {tool.diameter_mm}mm "
            f"({len(tool.holes)} holes) ===="
        )
        body.append(f";TOOL: drill_{tool.diameter_mm}mm")
        if ti > 0:
            body.append(f"M5  ; spindle off")
            body.append(
                f"M0  ; PAUSE: swap to a {tool.diameter_mm}mm drill bit, "
                f"re-probe Z, then press CYCLE START"
            )
        body.append(f"M3 S{spindle_rpm}")
        body.append("")

        for i, (x, y) in enumerate(tool.holes, start=1):
            body.append(
                f"; T{tool.number} hole {i}/{len(tool.holes)}: ({x:.3f}, {y:.3f})"
            )
            body.append(f"G0 X{x:.3f} Y{y:.3f}")
            body.append(f"G0 Z{APPROACH_Z:.3f}")
            # Peck drill: plunge to peck_depth, retract; plunge deeper, retract;
            # until we reach final_z.
            z = 0.0
            while z > final_z + 1e-6:
                z = max(final_z, z - peck_depth_mm)
                body.append(f"G1 Z{z:.3f} F{plunge_feed_mm_per_min}")
                body.append(f"G0 Z{APPROACH_Z:.3f}")
            body.append(f"G0 Z{SAFE_Z:.3f}")
            body.append("")

    footer = ["M5", "G0 X0 Y0"]
    return "\n".join(header + body + footer) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("drill_file", type=Path, help="path to Excellon .drl file")
    p.add_argument(
        "--copper-thickness",
        type=float,
        default=1.6,
        help="board thickness in mm (default 1.6 for standard FR4)",
    )
    p.add_argument("--spindle-rpm", type=int, default=12000)
    p.add_argument(
        "--plunge-feed",
        type=int,
        default=80,
        help="plunge feed rate, mm/min (default 80 — slow for FR4)",
    )
    p.add_argument(
        "--peck-depth",
        type=float,
        default=0.5,
        help="peck cycle depth, mm (default 0.5)",
    )
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()

    if not args.drill_file.exists():
        sys.exit(f"error: drill file not found: {args.drill_file}")

    text = args.drill_file.read_text()
    drl = parse_excellon(text)
    machine = load_yaml(PROFILES / "anolex_4030_evo_ultra2.yaml")

    gcode = generate_drill_gcode(
        drl=drl,
        copper_thickness_mm=args.copper_thickness,
        spindle_rpm=args.spindle_rpm,
        plunge_feed_mm_per_min=args.plunge_feed,
        peck_depth_mm=args.peck_depth,
        machine=machine,
    )

    if args.out is None:
        build_dir = LESSON_DIR / "build"
        build_dir.mkdir(exist_ok=True)
        args.out = build_dir / f"{args.drill_file.stem}.gcode"
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)

    args.out.write_text(gcode)
    n_holes = sum(len(t.holes) for t in drl.tools.values())
    n_tools = len([t for t in drl.tools.values() if t.holes])
    print(f"-> wrote {args.out}")
    print(f"   {n_holes} holes across {n_tools} drill bit(s)")
    if n_tools > 1:
        print(f"   GCode includes M0 pauses for tool changes")


if __name__ == "__main__":
    main()
