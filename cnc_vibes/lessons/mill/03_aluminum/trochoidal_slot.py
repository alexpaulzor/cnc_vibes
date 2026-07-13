#!/usr/bin/env python3
"""Generate GCode for a single straight slot in aluminum using trochoidal motion.

Trochoidal = the tool moves in a tight circle while advancing slowly
along the slot. At any moment only a small arc of the tool is engaged
with the material, so the cutting force stays low. This is essential
on a 500W spindle that would otherwise stall trying to slot at full
tool engagement.

The slot runs from (x0, y0) along +X for `length` mm, with width
`width` mm centered on the y0 line. Tool diameter must be less than
slot width (otherwise it's not a trochoidal motion, just a straight
slot).

Usage:
  python trochoidal_slot.py --x0 10 --y0 10 --length 30 --width 6 --depth 3
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

LESSON_DIR = Path(__file__).resolve().parent
REPO_ROOT = LESSON_DIR.parent.parent.parent
PROFILES = REPO_ROOT / "profiles"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
from job_params import compute_derived, find_by_id, load_yaml  # noqa: E402


SAFE_Z = 5.0


def generate_trochoidal_slot(
    x0: float,
    y0: float,
    length: float,
    width: float,
    depth: float,
    tool: dict,
    material: dict,
    machine: dict,
    spindle_rpm: int,
    trochoidal_radius_frac: float = 0.4,
    trochoidal_step_frac: float = 0.15,
) -> str:
    """Render the trochoidal-slot GCode.

    Algorithm: at each layer (Z stepping down by DOC):
      - Position at the slot start.
      - At each X step, do a small full circle (trochoidal motion)
        centered on the slot's Y centerline. The circle's radius lets
        the tool's *side* reach the slot walls; the X advance keeps
        the tool engaged on only a small arc at any moment.
      - Step Z down by DOC and repeat.
    """
    tool_dia = tool["diameter_mm"]
    if width <= tool_dia:
        sys.exit(
            f"error: --width ({width}) must be > tool diameter ({tool_dia}). "
            f"For a slot exactly tool-wide, use a profile cut instead."
        )
    if depth <= 0 or length <= 0 or width <= 0:
        sys.exit("error: length, width, depth must all be > 0")
    if spindle_rpm > tool["max_rpm"]:
        sys.exit(f"error: spindle_rpm {spindle_rpm} > tool max_rpm {tool['max_rpm']}")

    derived = compute_derived(machine, material, tool, spindle_rpm)
    feed = int(round(derived["values"]["feed_xy_mm_per_min"]))
    plunge_feed = int(derived["values"]["plunge_feed_mm_per_min"])
    doc = derived["values"]["doc_rough_mm"]

    # Trochoidal geometry.
    # The loop radius is chosen so the tool's edge reaches the slot wall:
    #   loop_radius = (width - tool_dia) / 2
    # but we cap it at trochoidal_radius_frac * tool_dia for safety on
    # underpowered spindles.
    max_loop_r = (width - tool_dia) / 2
    target_loop_r = trochoidal_radius_frac * tool_dia
    loop_radius = min(max_loop_r, target_loop_r)
    step_x = trochoidal_step_frac * tool_dia

    cy = y0 + width / 2  # slot centerline
    # X span the tool center can sweep through:
    x_start = x0 + tool_dia / 2 + loop_radius
    x_end = x0 + length - tool_dia / 2 - loop_radius
    if x_end <= x_start:
        sys.exit(
            f"error: slot is too short for trochoidal motion at this tool/width. "
            f"length must be > {(tool_dia + 2 * loop_radius):.2f} mm"
        )

    n_x_steps = max(1, math.ceil((x_end - x_start) / step_x))
    n_z_layers = math.ceil(depth / doc)

    header = [
        "; trochoidal_slot.py — aluminum-safe slot",
        f"; slot at ({x0}, {y0}) length={length} width={width} depth={depth}",
        f"; tool={tool['id']}  material={material['id']}  rpm={spindle_rpm}",
        f"; derived: feed={feed} plunge={plunge_feed} doc={doc:.3f}",
        f"; trochoidal: loop_r={loop_radius:.3f} step_x={step_x:.3f}",
        f"; layers: {n_z_layers}  x_steps_per_layer: {n_x_steps}",
        ";",
        f";TOOL: {tool['id']}",
        f";MATERIAL: {material['id']}",
        "",
        "$32=0   ; ensure laser mode OFF",
        "G21",
        "G90",
        "M5",
        f"G0 Z{SAFE_Z:.3f}",
        "G0 X0 Y0",
        f"M3 S{spindle_rpm}",
        "",
    ]

    body = []
    for layer in range(n_z_layers):
        z_target = max(-depth, -(layer + 1) * doc)
        body.append(f"; ---- layer {layer + 1}/{n_z_layers}: Z={z_target:.3f} ----")
        # Move to first loop position, descend.
        body.append(f"G0 X{x_start:.3f} Y{cy:.3f}")
        body.append(f"G1 Z{z_target:.3f} F{plunge_feed}")
        # Trochoidal loops, advancing in +X.
        for step in range(n_x_steps + 1):
            cx = min(x_end, x_start + step * step_x)
            # Move tool to start-of-loop position (rightmost of the circle).
            body.append(f"G1 X{cx + loop_radius:.3f} Y{cy:.3f} F{feed}")
            # Full CCW circle around (cx, cy).
            body.append(
                f"G3 X{cx + loop_radius:.3f} Y{cy:.3f} I{-loop_radius:.3f} J0 F{feed}"
            )
        # Retract before next layer.
        body.append(f"G0 Z{SAFE_Z:.3f}")
        body.append("")

    footer = ["M5", "G0 X0 Y0"]
    return "\n".join(header + body + footer) + "\n"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--x0", type=float, required=True)
    p.add_argument("--y0", type=float, required=True)
    p.add_argument("--length", type=float, required=True)
    p.add_argument("--width", type=float, required=True)
    p.add_argument("--depth", type=float, required=True)
    p.add_argument("--tool", default="flat_3.175mm_2flute")
    p.add_argument("--material", default="aluminum_6061_3mm")
    p.add_argument("--spindle-rpm", type=int, default=18000)
    p.add_argument("--trochoidal-radius-frac", type=float, default=0.4)
    p.add_argument("--trochoidal-step-frac", type=float, default=0.15)
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()

    machine = load_yaml(PROFILES / "default.yaml")
    tool = find_by_id(load_yaml(PROFILES / "tools.yaml"), args.tool, "tool")
    material = find_by_id(
        load_yaml(PROFILES / "materials.yaml"), args.material, "material"
    )

    gcode = generate_trochoidal_slot(
        x0=args.x0,
        y0=args.y0,
        length=args.length,
        width=args.width,
        depth=args.depth,
        tool=tool,
        material=material,
        machine=machine,
        spindle_rpm=args.spindle_rpm,
        trochoidal_radius_frac=args.trochoidal_radius_frac,
        trochoidal_step_frac=args.trochoidal_step_frac,
    )

    if args.out is None:
        build_dir = LESSON_DIR / "build"
        build_dir.mkdir(exist_ok=True)
        args.out = (
            build_dir
            / f"trochoidal_slot_L{args.length}_W{args.width}_D{args.depth}.gcode"
        )
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)

    args.out.write_text(gcode)
    print(f"-> wrote {args.out}")


if __name__ == "__main__":
    main()
