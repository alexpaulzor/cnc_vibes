#!/usr/bin/env python3
"""Lesson 4e demo part — a generic mounting plate that composes all three
cam.py operations into a single GCode file. No FreeCAD; no GUI for CAM;
just shapely shapes piped through cam.py → validator → CAMotics preview
→ preflight → cut.

The part: a 60×40mm rectangular plate with
  - 4 M4 clearance holes (one per corner, inset 8mm)
  - 1 central 20×10mm rectangular pocket, 3mm deep
  - outer profile cut all the way through 6mm plywood

Default tool / material / params are reasonable for the Anolex
4030 + plywood_baltic_birch_6mm + a 1/8" flat endmill (corner holes
use a real 3.2mm drill bit). All are CLI-overridable.

Generates lessons/mill/05_generic_cam/build/mounting_plate.gcode.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from shapely.geometry import Polygon

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from cam import (  # noqa: E402
    CamConfig,
    GcodeOutput,
    drill_array,
    load_material,
    load_tool,
    pocket_mill,
    profile_cut,
)

BUILD_DIR = SCRIPT_DIR / "build"
BUILD_DIR.mkdir(parents=True, exist_ok=True)


def make_mounting_plate_gcode(
    plate_w_mm: float = 60.0,
    plate_h_mm: float = 40.0,
    hole_inset_mm: float = 8.0,
    pocket_w_mm: float = 20.0,
    pocket_h_mm: float = 10.0,
    pocket_depth_mm: float = 3.0,
    plate_thickness_mm: float = 6.0,
    profile_tool_id: str = "flat_3.175mm_2flute",
    pocket_tool_id: str = "flat_3.175mm_2flute",
    drill_tool_id: str = "drill_3.2mm_m4_clearance",
    material_id: str = "plywood_baltic_birch_6mm",
    spindle_rpm: int = 18000,
    strict: bool = False,
) -> GcodeOutput:
    """Compose profile_cut + pocket_mill + drill_array into a single GCode
    body. Each op uses the SAME spindle + cfg conventions so the output
    is one continuous tool path the controller streams without re-homing
    or tool-changing (assumes you swap tools between sections manually,
    pausing at each ;TOOL-CHANGE marker)."""
    material = load_material(material_id)

    cfg = CamConfig(safe_z_mm=5.0, spindle_rpm=spindle_rpm, strict=strict)

    # Shapes
    plate = Polygon(
        [(0, 0), (plate_w_mm, 0), (plate_w_mm, plate_h_mm), (0, plate_h_mm)]
    )
    pocket_x0 = (plate_w_mm - pocket_w_mm) / 2
    pocket_y0 = (plate_h_mm - pocket_h_mm) / 2
    pocket = Polygon(
        [
            (pocket_x0, pocket_y0),
            (pocket_x0 + pocket_w_mm, pocket_y0),
            (pocket_x0 + pocket_w_mm, pocket_y0 + pocket_h_mm),
            (pocket_x0, pocket_y0 + pocket_h_mm),
        ]
    )
    holes = [
        (hole_inset_mm, hole_inset_mm),
        (plate_w_mm - hole_inset_mm, hole_inset_mm),
        (hole_inset_mm, plate_h_mm - hole_inset_mm),
        (plate_w_mm - hole_inset_mm, plate_h_mm - hole_inset_mm),
    ]

    # Operations in cut-order: inside-features first, perimeter last
    # (so the part stays anchored to the stock until the very end).
    ops_in_order = [
        (
            "drill corner mount holes",
            drill_array(
                holes,
                depth_mm=plate_thickness_mm + 0.5,  # tiny breakthrough
                tool=load_tool(drill_tool_id),
                material=material,
                peck_depth_mm=2.0,  # peck so chips clear in deep wood holes
                cfg=cfg,
            ),
        ),
        (
            "mill center pocket",
            pocket_mill(
                pocket,
                depth_mm=pocket_depth_mm,
                tool=load_tool(pocket_tool_id),
                material=material,
                cfg=cfg,
            ),
        ),
        (
            "profile-cut outer perimeter",
            profile_cut(
                plate,
                depth_mm=plate_thickness_mm,
                tool=load_tool(profile_tool_id),
                material=material,
                side="outside",
                cfg=cfg,
            ),
        ),
    ]

    # Concatenate. Each cam.py op already emits a full standalone GCode
    # block (header + body + footer). For a single combined file we
    # keep all of them — between sections, the operator can pause to
    # swap tools at the ;TOOL line, then resume.
    combined_lines: list[str] = []
    combined_warnings: list[str] = []
    for label, out in ops_in_order:
        combined_lines.append(f"; ===== {label} =====")
        combined_lines.append("")
        combined_lines.extend(out.lines)
        combined_lines.append("")
        combined_warnings.extend(out.warnings)
    return GcodeOutput(lines=combined_lines, warnings=combined_warnings)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--plate-w-mm", type=float, default=60.0)
    ap.add_argument("--plate-h-mm", type=float, default=40.0)
    ap.add_argument("--plate-thickness-mm", type=float, default=6.0)
    ap.add_argument("--hole-inset-mm", type=float, default=8.0)
    ap.add_argument("--pocket-w-mm", type=float, default=20.0)
    ap.add_argument("--pocket-h-mm", type=float, default=10.0)
    ap.add_argument("--pocket-depth-mm", type=float, default=3.0)
    ap.add_argument("--material", default="plywood_baltic_birch_6mm")
    ap.add_argument("--drill-tool", default="drill_3.2mm_m4_clearance")
    ap.add_argument("--pocket-tool", default="flat_3.175mm_2flute")
    ap.add_argument("--profile-tool", default="flat_3.175mm_2flute")
    ap.add_argument("--spindle-rpm", type=int, default=18000)
    ap.add_argument(
        "--strict",
        action="store_true",
        help="upgrade all CAM warnings to fatal errors (use in CI)",
    )
    args = ap.parse_args()

    out = make_mounting_plate_gcode(
        plate_w_mm=args.plate_w_mm,
        plate_h_mm=args.plate_h_mm,
        hole_inset_mm=args.hole_inset_mm,
        pocket_w_mm=args.pocket_w_mm,
        pocket_h_mm=args.pocket_h_mm,
        pocket_depth_mm=args.pocket_depth_mm,
        plate_thickness_mm=args.plate_thickness_mm,
        profile_tool_id=args.profile_tool,
        pocket_tool_id=args.pocket_tool,
        drill_tool_id=args.drill_tool,
        material_id=args.material,
        spindle_rpm=args.spindle_rpm,
        strict=args.strict,
    )
    gcode_path = BUILD_DIR / "mounting_plate.gcode"
    gcode_path.write_text(out.text)
    print(f"-> {gcode_path}  ({len(out.lines)} lines)")
    print(f"   warnings: {len(out.warnings)}")
    for w in out.warnings:
        snippet = w if len(w) <= 100 else w[:97] + "..."
        print(f"     - {snippet}")
    rel = gcode_path.relative_to(REPO_ROOT)
    print(f"\nNext:")
    print(f"  python cnc.py validate {rel}")
    print(f"  python cnc.py preview  {rel}   # opens CAMotics")
    print(f"  python cnc.py preflight {rel}")


if __name__ == "__main__":
    main()
