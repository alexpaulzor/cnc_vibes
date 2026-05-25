#!/usr/bin/env python3
"""Generate laser-cuttable spoilboard tiles with an M6 hole grid.

The Anolex 4030's bed has a 9x10 grid of M6 mounting holes on 45mm
centers (400x500mm overall). To cut a fresh spoilboard from 300x300mm
stock, the design is split into tiles whose joints fall BETWEEN hole
rows/columns (never through a hole). Each tile fits in stock AND in the
machine envelope; the tiles butt-joint and the M6 bolts themselves
align everything when the spoilboard is mounted.

Pure-function geometry (testable without hardware). Emits one .gcode
per tile + a verification image showing the assembled layout.

Usage:
  python spoilboard.py
  python spoilboard.py --stock-w 250 --stock-h 250
  python spoilboard.py --panel-w 400 --panel-h 300 --hole-rows 6
  python spoilboard.py --material plywood_baltic_birch_3mm
  python cnc.py validate lessons/laser/04_spoilboard/build/spoilboard_tile_1.gcode

Loose-fit holes: cut on centerline, kerf widens each hole by ~0.2mm
beyond the nominal --hole-dia. Default 6.5mm nominal becomes ~6.7mm
actual, an M6 clearance fit.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
BUILD_DIR = SCRIPT_DIR / "build"
FIG_DIR = SCRIPT_DIR / "figs"
BUILD_DIR.mkdir(parents=True, exist_ok=True)
FIG_DIR.mkdir(parents=True, exist_ok=True)

# Anolex 4030 defaults
DEFAULT_PANEL_W = 400.0
DEFAULT_PANEL_H = 500.0
DEFAULT_HOLE_COLS = 9
DEFAULT_HOLE_ROWS = 10
DEFAULT_HOLE_SPACING = 45.0
DEFAULT_HOLE_DIA = 6.5  # M6 clearance fit; kerf adds ~0.2mm in cut
DEFAULT_STOCK_W = 300.0
DEFAULT_STOCK_H = 300.0
DEFAULT_MATERIAL = "mdf_3mm"
DEFAULT_MARGIN_PX_PER_MM = 2  # for the verification image only


# ---------------------------------------------------------------------------
# Geometry — pure functions
# ---------------------------------------------------------------------------


@dataclass
class Tile:
    """One cuttable tile from the spoilboard."""

    index: int  # 1-indexed for human-readable file names
    x0: float  # tile's lower-left X in panel coords (mm)
    y0: float  # tile's lower-left Y in panel coords (mm)
    w: float  # tile width (mm)
    h: float  # tile height (mm)
    holes: list[tuple[float, float]]  # hole centers in PANEL coords (mm)

    @property
    def holes_tile_local(self) -> list[tuple[float, float]]:
        """Hole centers translated to tile-local coords (lower-left=0,0)."""
        return [(hx - self.x0, hy - self.y0) for hx, hy in self.holes]


def compute_hole_positions(
    panel_w: float,
    panel_h: float,
    cols: int,
    rows: int,
    spacing: float,
    margin_x: float | None = None,
    margin_y: float | None = None,
) -> list[tuple[float, float]]:
    """Return list of (x, y) hole centers in panel coords. If margin is
    None, holes are centered with auto-margin."""
    if margin_x is None:
        x_span = (cols - 1) * spacing
        margin_x = (panel_w - x_span) / 2
    if margin_y is None:
        y_span = (rows - 1) * spacing
        margin_y = (panel_h - y_span) / 2
    if margin_x < 0 or margin_y < 0:
        raise ValueError(
            f"hole grid doesn't fit in panel: "
            f"need at least {(cols - 1) * spacing}x{(rows - 1) * spacing}mm "
            f"for {cols}x{rows} holes at {spacing}mm spacing"
        )
    return [
        (margin_x + i * spacing, margin_y + j * spacing)
        for j in range(rows)
        for i in range(cols)
    ]


def compute_axis_splits(
    panel_extent: float, hole_axis_positions: list[float], stock_extent: float
) -> list[float]:
    """Greedy: pick the largest split position ≤ stock_extent where the
    split falls midway between consecutive hole positions. Repeat from
    the new origin until the remainder fits in stock.

    Returns the LIST OF SPLIT COORDINATES (not tile widths). A panel
    with no splits returns []; one split returns [s]; two returns [s1, s2].
    """
    unique_axis = sorted(set(hole_axis_positions))
    midpoints = [
        (a + b) / 2 for a, b in zip(unique_axis, unique_axis[1:])
    ]  # safe between-hole splits
    splits: list[float] = []
    cursor = 0.0
    while panel_extent - cursor > stock_extent:
        # Find the largest valid split position in (cursor, cursor + stock_extent]
        # that's also a between-hole midpoint
        best = None
        for m in midpoints:
            if cursor < m <= cursor + stock_extent:
                if best is None or m > best:
                    best = m
        if best is None:
            raise ValueError(
                f"can't find a between-holes split fitting in stock_extent={stock_extent} "
                f"starting at cursor={cursor}; panel_extent={panel_extent}; "
                f"midpoints={midpoints}. Stock is too small or hole spacing is too coarse."
            )
        splits.append(best)
        cursor = best
    return splits


def compute_tiles(
    panel_w: float,
    panel_h: float,
    holes: list[tuple[float, float]],
    stock_w: float,
    stock_h: float,
) -> list[Tile]:
    """Split panel into tiles fitting in stock; each hole belongs to
    exactly one tile."""
    x_splits = compute_axis_splits(panel_w, [h[0] for h in holes], stock_w)
    y_splits = compute_axis_splits(panel_h, [h[1] for h in holes], stock_h)

    x_bounds = [0.0] + x_splits + [panel_w]
    y_bounds = [0.0] + y_splits + [panel_h]

    tiles: list[Tile] = []
    idx = 1
    for j in range(len(y_bounds) - 1):
        y0, y1 = y_bounds[j], y_bounds[j + 1]
        for i in range(len(x_bounds) - 1):
            x0, x1 = x_bounds[i], x_bounds[i + 1]
            tile_holes = [(hx, hy) for hx, hy in holes if x0 < hx < x1 and y0 < hy < y1]
            tiles.append(
                Tile(
                    index=idx,
                    x0=x0,
                    y0=y0,
                    w=x1 - x0,
                    h=y1 - y0,
                    holes=tile_holes,
                )
            )
            idx += 1
    return tiles


# ---------------------------------------------------------------------------
# Visualization
# ---------------------------------------------------------------------------


def render_layout(
    panel_w: float,
    panel_h: float,
    holes: list[tuple[float, float]],
    tiles: list[Tile],
    hole_dia: float,
    out_path: Path,
    px_per_mm: int = DEFAULT_MARGIN_PX_PER_MM,
) -> None:
    """Verification image: panel outline + holes + tile split lines + tile labels."""
    margin_px = 40
    img_w = int(panel_w * px_per_mm) + 2 * margin_px
    img_h = int(panel_h * px_per_mm) + 2 * margin_px + 60
    img = Image.new("RGB", (img_w, img_h), (255, 255, 255))
    d = ImageDraw.Draw(img)

    def mm_to_px(x_mm, y_mm):
        # Image Y is down; flip so panel Y=0 is at bottom of image
        return (
            margin_px + x_mm * px_per_mm,
            margin_px + (panel_h - y_mm) * px_per_mm,
        )

    # Panel outline
    tl = mm_to_px(0, panel_h)
    br = mm_to_px(panel_w, 0)
    d.rectangle([tl, br], outline=(60, 60, 60), width=2)

    # Tile boundaries (dashed)
    pastel_colors = [
        (255, 230, 230),
        (230, 255, 230),
        (230, 230, 255),
        (255, 255, 220),
        (255, 230, 255),
        (230, 255, 255),
    ]
    for tile in tiles:
        tl_t = mm_to_px(tile.x0, tile.y0 + tile.h)
        br_t = mm_to_px(tile.x0 + tile.w, tile.y0)
        d.rectangle(
            [tl_t, br_t],
            fill=pastel_colors[(tile.index - 1) % len(pastel_colors)],
            outline=(180, 60, 60),
            width=2,
        )

    # Holes
    r_px = (hole_dia / 2) * px_per_mm
    for hx, hy in holes:
        cx, cy = mm_to_px(hx, hy)
        d.ellipse(
            [cx - r_px, cy - r_px, cx + r_px, cy + r_px],
            outline=(40, 40, 40),
            width=1,
        )

    # Tile labels
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
    except (OSError, IOError):
        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24
            )
        except (OSError, IOError):
            font = ImageFont.load_default()
    for tile in tiles:
        cx_mm = tile.x0 + tile.w / 2
        cy_mm = tile.y0 + tile.h / 2
        cx, cy = mm_to_px(cx_mm, cy_mm)
        label = (
            f"Tile {tile.index}\n{tile.w:.0f}×{tile.h:.0f}mm\n{len(tile.holes)} holes"
        )
        d.multiline_text((cx - 50, cy - 36), label, fill=(60, 0, 0), font=font)

    # Caption
    caption = (
        f"Spoilboard: {panel_w:.0f}×{panel_h:.0f}mm, "
        f"{len(holes)} holes ({hole_dia:.1f}mm), "
        f"{len(tiles)} tiles"
    )
    d.text((margin_px, img_h - 50), caption, fill=(20, 20, 20), font=font)

    img.save(out_path, "PNG", optimize=True)


# ---------------------------------------------------------------------------
# GCode emission (laser mode)
# ---------------------------------------------------------------------------


def load_material(material_id: str) -> dict:
    with (REPO_ROOT / "profiles" / "laser_materials.yaml").open() as f:
        materials = yaml.safe_load(f)
    for m in materials:
        if m.get("id") == material_id:
            return m
    raise SystemExit(f"unknown material: {material_id}")


def emit_circle_path(
    cx: float, cy: float, radius: float, n_segments: int = 36
) -> list[tuple[float, float]]:
    """Return a closed polyline approximating a circle. Used so all cuts
    are G1 line segments (consistent kerf vs G2/G3 which depend on
    interpolation accuracy)."""
    import math

    pts = []
    for i in range(n_segments + 1):
        theta = 2 * math.pi * i / n_segments
        pts.append((cx + radius * math.cos(theta), cy + radius * math.sin(theta)))
    return pts


def emit_tile_gcode(tile: Tile, hole_dia: float, material: dict) -> str:
    """Emit laser GCode for one tile: holes first (innermost), then perimeter
    (so the tile doesn't fall away before its holes are cut)."""
    laser = material["laser"]
    power_s = int(round(laser["power_percent"] * 10))
    feed = laser["feed_mm_per_min"]
    passes = laser["passes"]
    radius = hole_dia / 2

    lines = [
        f"; spoilboard tile {tile.index}: {tile.w:.1f}x{tile.h:.1f}mm, "
        f"{len(tile.holes)} holes",
        f"; generated by lessons/laser/04_spoilboard/spoilboard.py",
        f"; ASSUMES Z already at focal height in your WCS; X=0 Y=0 at tile lower-left",
        f"; cut order: holes (innermost) before tile perimeter (last)",
        f";",
        f";HEAD: laser",
        f";MATERIAL: {material['id']}",
        "",
        "$32=1   ; GRBL laser mode",
        "G21     ; mm",
        "G90     ; absolute",
        "M5      ; laser off",
        "G0 X0 Y0",
        "",
    ]

    def emit_closed_path(label: str, pts: list[tuple[float, float]]):
        lines.append(f"; --- {label} ---")
        x0, y0 = pts[0]
        lines.append(f"G0 X{x0:.3f} Y{y0:.3f}")
        lines.append(f"M4 S{power_s}")
        lines.append(f"F{feed}")
        for pass_n in range(passes):
            if passes > 1:
                lines.append(f"; pass {pass_n + 1} of {passes}")
            if pass_n > 0:
                # Multi-pass: return to start before re-tracing
                lines.append(f"G0 X{x0:.3f} Y{y0:.3f}")
                lines.append(f"M4 S{power_s}")
            for x, y in pts[1:]:
                lines.append(f"G1 X{x:.3f} Y{y:.3f}")
        lines.append("M5")
        lines.append("")

    # Holes first (in tile-local coords)
    for hi, (hx, hy) in enumerate(tile.holes_tile_local, start=1):
        pts = emit_circle_path(hx, hy, radius)
        emit_closed_path(f"hole {hi}/{len(tile.holes)} at ({hx:.1f}, {hy:.1f})", pts)

    # Perimeter last
    perim = [
        (0, 0),
        (tile.w, 0),
        (tile.w, tile.h),
        (0, tile.h),
        (0, 0),
    ]
    emit_closed_path("perimeter", perim)

    lines += ["G0 X0 Y0", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--panel-w", type=float, default=DEFAULT_PANEL_W)
    ap.add_argument("--panel-h", type=float, default=DEFAULT_PANEL_H)
    ap.add_argument("--hole-cols", type=int, default=DEFAULT_HOLE_COLS)
    ap.add_argument("--hole-rows", type=int, default=DEFAULT_HOLE_ROWS)
    ap.add_argument("--hole-spacing", type=float, default=DEFAULT_HOLE_SPACING)
    ap.add_argument(
        "--hole-dia",
        type=float,
        default=DEFAULT_HOLE_DIA,
        help="nominal hole diameter; kerf will widen by ~0.2mm in the cut",
    )
    ap.add_argument(
        "--margin-x",
        type=float,
        default=None,
        help="distance from panel left edge to first hole column (default: auto-center)",
    )
    ap.add_argument("--margin-y", type=float, default=None)
    ap.add_argument("--stock-w", type=float, default=DEFAULT_STOCK_W)
    ap.add_argument("--stock-h", type=float, default=DEFAULT_STOCK_H)
    ap.add_argument("--material", default=DEFAULT_MATERIAL)
    ap.add_argument(
        "--no-gcode",
        action="store_true",
        help="only render the layout image; skip GCode emission",
    )
    args = ap.parse_args()

    print(
        f"spoilboard: panel={args.panel_w}x{args.panel_h}mm "
        f"holes={args.hole_cols}x{args.hole_rows}@{args.hole_spacing}mm "
        f"hole_dia={args.hole_dia}mm stock={args.stock_w}x{args.stock_h}mm "
        f"material={args.material}"
    )

    holes = compute_hole_positions(
        args.panel_w,
        args.panel_h,
        args.hole_cols,
        args.hole_rows,
        args.hole_spacing,
        args.margin_x,
        args.margin_y,
    )
    tiles = compute_tiles(args.panel_w, args.panel_h, holes, args.stock_w, args.stock_h)
    print(f"  {len(holes)} holes, {len(tiles)} tiles:")
    for t in tiles:
        print(
            f"    tile {t.index}: x0={t.x0:.1f} y0={t.y0:.1f} "
            f"{t.w:.1f}x{t.h:.1f}mm ({len(t.holes)} holes)"
        )

    layout_path = FIG_DIR / "spoilboard_layout.png"
    render_layout(args.panel_w, args.panel_h, holes, tiles, args.hole_dia, layout_path)
    print(f"-> {layout_path}")

    if args.no_gcode:
        return

    material = load_material(args.material)
    for tile in tiles:
        gcode = emit_tile_gcode(tile, args.hole_dia, material)
        out_path = BUILD_DIR / f"spoilboard_tile_{tile.index}.gcode"
        out_path.write_text(gcode)
        print(f"-> {out_path}  ({len(gcode.splitlines())} lines)")

    print("\nValidate with:")
    for tile in tiles:
        rel = (BUILD_DIR / f"spoilboard_tile_{tile.index}.gcode").relative_to(REPO_ROOT)
        print(f"  python cnc.py validate {rel}")


if __name__ == "__main__":
    main()
