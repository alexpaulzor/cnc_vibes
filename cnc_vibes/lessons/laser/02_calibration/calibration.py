#!/usr/bin/env python3
"""Generate a laser calibration burn pattern.

Produces a 2D matrix of small square cuts at varying (power, passes,
feed) parameter combinations, labeled around the edges so you can read
off which cell corresponded to which settings after the burn.

Layout per panel (one panel per requested feed):

         <feed-rate label>
       1   2   3   4   5     <- pass count (X axis)
   100 []  []  []  []  []
    75 []  []  []  []  []     <- power% (Y axis)
    50 []  []  []  []  []
    25 []  []  []  []  []

Multiple --speeds produce multiple panels stacked vertically in the
same file. Read off which slugs fell out cleanly to determine cut
settings; write back into profiles/laser_materials.yaml.

Usage:
  python calibration.py --material plywood_baltic_birch_3mm \\
                        --max-passes 5 \\
                        --powers 100,75,50,25 \\
                        --speeds 200,400,600

Outputs GRBL laser-mode GCode (uses M4 dynamic power, $32=1, no Z).
Validate with `cnc.py validate <gcode>` and walk
`cnc.py preflight <gcode>` before running.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

LESSON_DIR = Path(__file__).resolve().parent
REPO_ROOT = LESSON_DIR.parent.parent.parent
PROFILES = REPO_ROOT / "profiles"

sys.path.insert(0, str(LESSON_DIR))
from font_7seg import render_text, text_width  # noqa: E402


# ---- layout constants (tunable, but fixed for v1) -------------------------

CELL_SHAPE_SIZE = 8.0  # mm; cut square side
GRID_PAD_LEFT = 18.0  # mm; horizontal space reserved for row (power) labels
GRID_PAD_TOP = 8.0  # mm; vertical gap between column labels and grid
PANEL_LABEL_GAP = 4.0  # mm; gap between speed-label and column labels
PANEL_GAP = 8.0  # mm; vertical gap between panels
EDGE_MARGIN = 3.0  # mm; offset from WCS X=0, Y=0 to first label

LABEL_POWER_PERCENT = 30  # engrave-only power for labels
LABEL_FEED_MM_PER_MIN = 1500
LABEL_DIGIT_SPACING = 1.0


# ---- helpers --------------------------------------------------------------


def load_material(material_id: str) -> dict:
    with (PROFILES / "laser_materials.yaml").open() as f:
        materials = yaml.safe_load(f)
    for m in materials:
        if m.get("id") == material_id:
            if "laser" not in m:
                sys.exit(f"error: material '{material_id}' has no laser params")
            return m
    available = ", ".join(sorted(m.get("id", "?") for m in materials))
    sys.exit(f"error: unknown material '{material_id}'. Available: {available}")


def _parse_csv_floats(s: str) -> list[float]:
    return [float(x) for x in s.split(",") if x.strip()]


def _parse_csv_ints(s: str) -> list[int]:
    return [int(x) for x in s.split(",") if x.strip()]


# ---- GCode primitives -----------------------------------------------------


def _emit_engrave_segments(
    segments: list[tuple[float, float, float, float]],
    power_s: int,
    feed: int,
) -> list[str]:
    """Emit GCode that traces each line segment as a single-pass engrave."""
    lines = []
    for x1, y1, x2, y2 in segments:
        lines.append(f"G0 X{x1:.3f} Y{y1:.3f}")
        lines.append(f"M4 S{power_s}")
        lines.append(f"G1 X{x2:.3f} Y{y2:.3f} F{feed}")
        lines.append("M5")
    return lines


def _emit_cut_square(
    cx: float, cy: float, size: float, power_s: int, feed: int, passes: int
) -> list[str]:
    """Cut a square centered on (cx, cy), passes times at the given power/feed."""
    half = size / 2
    x0 = cx - half
    y0 = cy - half
    x1 = cx + half
    y1 = cy + half
    lines = [f"G0 X{x0:.3f} Y{y0:.3f}", f"M4 S{power_s}"]
    for p in range(passes):
        lines.append(f"; pass {p + 1}/{passes}")
        lines.append(f"G1 X{x1:.3f} Y{y0:.3f} F{feed}")
        lines.append(f"G1 X{x1:.3f} Y{y1:.3f} F{feed}")
        lines.append(f"G1 X{x0:.3f} Y{y1:.3f} F{feed}")
        lines.append(f"G1 X{x0:.3f} Y{y0:.3f} F{feed}")
    lines.append("M5")
    return lines


# ---- panel layout ---------------------------------------------------------


def _panel_dimensions(
    max_passes: int, powers: list[float], cell_pitch: float, digit_height: float
) -> tuple[float, float]:
    """Return (panel_width, panel_height) in mm, including label margins."""
    grid_w = max_passes * cell_pitch
    grid_h = len(powers) * cell_pitch
    panel_w = GRID_PAD_LEFT + grid_w
    panel_h = digit_height + PANEL_LABEL_GAP + GRID_PAD_TOP + grid_h
    return panel_w, panel_h


def _emit_panel(
    panel_origin_x: float,
    panel_origin_y: float,
    feed: int,
    max_passes: int,
    powers: list[float],
    cell_pitch: float,
    digit_height: float,
    label_power_s: int,
) -> list[str]:
    """Emit the GCode for one panel (labels + cell cuts at one feed)."""
    out = []
    grid_origin_x = panel_origin_x + GRID_PAD_LEFT
    grid_origin_y = panel_origin_y + digit_height + PANEL_LABEL_GAP + GRID_PAD_TOP

    # ---- panel header label (the feed rate) ----
    out.append(f"")
    out.append(f"; ==== panel: feed={feed} mm/min ====")
    feed_text = str(feed)
    feed_segments = render_text(
        feed_text,
        origin_x=grid_origin_x,
        origin_y=panel_origin_y,
        height=digit_height,
        spacing=LABEL_DIGIT_SPACING,
    )
    out.extend(
        _emit_engrave_segments(feed_segments, label_power_s, LABEL_FEED_MM_PER_MIN)
    )

    # ---- column labels (pass counts) above the grid ----
    col_label_y = grid_origin_y - GRID_PAD_TOP - digit_height
    for col in range(max_passes):
        text = str(col + 1)
        # Center the label horizontally within the cell
        label_w = text_width(text, digit_height, LABEL_DIGIT_SPACING)
        cell_center_x = grid_origin_x + col * cell_pitch + cell_pitch / 2
        label_origin_x = cell_center_x - label_w / 2
        segs = render_text(
            text, label_origin_x, col_label_y, digit_height, LABEL_DIGIT_SPACING
        )
        out.extend(_emit_engrave_segments(segs, label_power_s, LABEL_FEED_MM_PER_MIN))

    # ---- row labels (power percentages) left of the grid ----
    for row, power in enumerate(powers):
        text = str(int(power))
        label_w = text_width(text, digit_height, LABEL_DIGIT_SPACING)
        # Right-align labels in the GRID_PAD_LEFT margin, with 2mm of padding
        label_origin_x = grid_origin_x - 2.0 - label_w
        cell_center_y = grid_origin_y + row * cell_pitch + cell_pitch / 2
        label_origin_y = cell_center_y - digit_height / 2
        segs = render_text(
            text, label_origin_x, label_origin_y, digit_height, LABEL_DIGIT_SPACING
        )
        out.extend(_emit_engrave_segments(segs, label_power_s, LABEL_FEED_MM_PER_MIN))

    # ---- the actual cuts: one square per (row=power, col=passes) cell ----
    out.append("")
    out.append("; ---- cut grid ----")
    for row, power_pct in enumerate(powers):
        cell_power_s = int(round(power_pct * 10))
        for col in range(max_passes):
            passes = col + 1
            cx = grid_origin_x + col * cell_pitch + cell_pitch / 2
            cy = grid_origin_y + row * cell_pitch + cell_pitch / 2
            out.append("")
            out.append(f"; cell row={row} col={col} power={power_pct}% passes={passes}")
            out.extend(
                _emit_cut_square(cx, cy, CELL_SHAPE_SIZE, cell_power_s, feed, passes)
            )

    return out


# ---- top-level generator --------------------------------------------------


def generate_gcode(
    material: dict,
    max_passes: int,
    powers: list[float],
    speeds: list[int],
    cell_pitch: float,
    label_digit_height: float,
) -> str:
    """Render the full multi-panel calibration GCode."""
    if max_passes < 1:
        sys.exit("error: --max-passes must be >= 1")
    if not powers:
        sys.exit("error: --powers cannot be empty")
    for p in powers:
        if not (0 < p <= 100):
            sys.exit(f"error: power {p} outside (0, 100]")
    if not speeds:
        sys.exit("error: at least one speed is required (or material default)")
    label_power_s = int(round(LABEL_POWER_PERCENT * 10))

    panel_w, panel_h = _panel_dimensions(
        max_passes, powers, cell_pitch, label_digit_height
    )
    total_h = EDGE_MARGIN + len(speeds) * panel_h + max(0, len(speeds) - 1) * PANEL_GAP
    total_w = EDGE_MARGIN + panel_w

    header = [
        "; calibration.py — laser cut-through calibration pattern",
        f"; generated by lessons/laser/02_calibration/calibration.py",
        f"; material={material['id']}",
        f"; max_passes={max_passes}  powers={powers}  speeds={speeds}",
        f"; layout: {len(speeds)} panel(s), each {panel_w:.1f}x{panel_h:.1f} mm",
        f"; total extent: {total_w:.1f}x{total_h:.1f} mm",
        ";",
        ";HEAD: laser",
        f";MATERIAL: {material['id']}",
        ";LESSON: laser-calibration",
        "",
        "$32=1   ; enable GRBL laser mode (sticky)",
        "G21     ; mm",
        "G90     ; absolute coordinates",
        "M5      ; laser off",
        "G0 X0 Y0",
        "",
    ]

    body = []
    for i, feed in enumerate(speeds):
        panel_origin_x = EDGE_MARGIN
        panel_origin_y = EDGE_MARGIN + i * (panel_h + PANEL_GAP)
        body.extend(
            _emit_panel(
                panel_origin_x=panel_origin_x,
                panel_origin_y=panel_origin_y,
                feed=feed,
                max_passes=max_passes,
                powers=powers,
                cell_pitch=cell_pitch,
                digit_height=label_digit_height,
                label_power_s=label_power_s,
            )
        )

    footer = ["", "M5", "G0 X0 Y0  ; park"]
    return "\n".join(header + body + footer) + "\n"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--material", default="plywood_baltic_birch_3mm")
    p.add_argument("--max-passes", type=int, default=5)
    p.add_argument(
        "--powers",
        default="100,75,50,25",
        help="comma-separated power percentages, top-down (default: 100,75,50,25)",
    )
    p.add_argument(
        "--speeds",
        default="",
        help="comma-separated feeds in mm/min. Empty = use material default.",
    )
    p.add_argument("--cell-pitch", type=float, default=18.0)
    p.add_argument("--label-digit-height", type=float, default=5.0)
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()

    material = load_material(args.material)
    powers = _parse_csv_floats(args.powers)
    speeds = (
        _parse_csv_ints(args.speeds)
        if args.speeds
        else [int(material["laser"]["feed_mm_per_min"])]
    )

    if args.out is None:
        build_dir = LESSON_DIR / "build"
        build_dir.mkdir(exist_ok=True)
        speed_tag = "_".join(str(s) for s in speeds)
        args.out = build_dir / f"cal_{args.material}_F{speed_tag}.gcode"
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)

    gcode = generate_gcode(
        material=material,
        max_passes=args.max_passes,
        powers=powers,
        speeds=speeds,
        cell_pitch=args.cell_pitch,
        label_digit_height=args.label_digit_height,
    )
    args.out.write_text(gcode)
    print(f"-> wrote {args.out}")


if __name__ == "__main__":
    main()
