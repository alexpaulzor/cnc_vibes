#!/usr/bin/env python3
"""Phase 3 — actually apply the algorithm.

Emits a sequence of numbered diagrams showing the progression:

  v01: starting state (same as Phase 2b)
  v02: after removing tabs interfered by letter outline
  v03: after adding letter-perimeter tabs at proposed locations
  v04: after re-analyzing and highlighting any remaining issues
  v05: final clean result

Each image is saved as nora_phase3_v0N_<label>.png so you can watch
the directory and see progression.
"""

from __future__ import annotations

import argparse
import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import unary_union

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from diagram_word_phase2 import (  # noqa: E402
    CELL_H,
    CELL_W,
    COLS,
    CUT,
    LABEL_BG,
    LABEL_TEXT,
    LEGEND_H,
    LETTER_OUTLINE,
    LETTER_OUTLINE_WIDTH,
    MARGIN,
    PIECE_LABEL_TEXT,
    PIECE_MM,
    PANEL_MM,
    ROWS,
    SEED,
    TAB_HEIGHT,
    TAB_LEN,
    bezier_pt,
    count_tabs_per_subpiece,
    extract_subpieces,
    find_font,
    find_interfered_tabs,
    place_tab,
    propose_letter_tabs,
    render_letter_polygons,
    shapely_to_pil_points,
    tab_outline,
)
from diagram_word_phase2_pieces import pastel  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "figs"
OUT_DIR.mkdir(parents=True, exist_ok=True)

BG = (255, 255, 255)
LETTER_OUTLINE_SUBTLE = (200, 30, 30, 100)


def build_pieces_with_skips(seed: int, skip_edges: set):
    """Build piece polygons + tab catalog, omitting tabs on `skip_edges`.

    skip_edges is a set of frozenset({(c1, r1), (c2, r2)}) — pairs of piece
    coordinates whose shared edge should be a straight cut (no tab).
    """
    random.seed(seed)
    vertical_tabs = {
        (c, r): random.random() > 0.5 for c in range(COLS - 1) for r in range(ROWS)
    }
    horizontal_tabs = {
        (c, r): random.random() > 0.5 for c in range(COLS) for r in range(ROWS - 1)
    }

    def piece_polygon(col, row, ox, oy):
        x0 = ox + col * CELL_W
        y0 = oy + row * CELL_H
        x1 = x0 + CELL_W
        y1 = y0 + CELL_H
        pts = [(x0, y0)]
        if row > 0:
            edge_key = frozenset({(col, row - 1), (col, row)})
            if edge_key not in skip_edges:
                bulges_down = horizontal_tabs[(col, row - 1)]
                d = -1 if bulges_down else +1
                pts.extend(place_tab((x0, y0), (1, 0), CELL_W, d)[1:-1])
        pts.append((x1, y0))
        if col < COLS - 1:
            edge_key = frozenset({(col, row), (col + 1, row)})
            if edge_key not in skip_edges:
                bulges_right = vertical_tabs[(col, row)]
                d = +1 if bulges_right else -1
                pts.extend(place_tab((x1, y0), (0, 1), CELL_H, d)[1:-1])
        pts.append((x1, y1))
        if row < ROWS - 1:
            edge_key = frozenset({(col, row), (col, row + 1)})
            if edge_key not in skip_edges:
                bulges_down = horizontal_tabs[(col, row)]
                d = +1 if bulges_down else -1
                pts.extend(place_tab((x1, y1), (-1, 0), CELL_W, d)[1:-1])
        pts.append((x0, y1))
        if col > 0:
            edge_key = frozenset({(col - 1, row), (col, row)})
            if edge_key not in skip_edges:
                bulges_right = vertical_tabs[(col - 1, row)]
                d = -1 if bulges_right else +1
                pts.extend(place_tab((x0, y1), (0, -1), CELL_H, d)[1:-1])
        return pts

    px = MARGIN
    py = MARGIN
    piece_polys = {
        (c, r): piece_polygon(c, r, px, py) for c in range(COLS) for r in range(ROWS)
    }

    tabs = []
    tab_id = 0
    for c in range(COLS - 1):
        for r in range(ROWS):
            edge_key = frozenset({(c, r), (c + 1, r)})
            if edge_key in skip_edges:
                continue
            tab_id += 1
            x_e = MARGIN + (c + 1) * CELL_W
            y_top = MARGIN + r * CELL_H
            tabs.append(
                {
                    "id": tab_id,
                    "type": "vertical",
                    "between": ((c, r), (c + 1, r)),
                    "midpoint": (x_e, y_top + CELL_H / 2),
                }
            )
    for c in range(COLS):
        for r in range(ROWS - 1):
            edge_key = frozenset({(c, r), (c, r + 1)})
            if edge_key in skip_edges:
                continue
            tab_id += 1
            x_left = MARGIN + c * CELL_W
            y_e = MARGIN + (r + 1) * CELL_H
            tabs.append(
                {
                    "id": tab_id,
                    "type": "horizontal",
                    "between": ((c, r), (c, r + 1)),
                    "midpoint": (x_left + CELL_W / 2, y_e),
                }
            )

    return piece_polys, tabs


def tangent_at_letter_point(letter_union, point: Point, sample_dist: float = 5.0):
    boundary = letter_union.boundary
    proj_dist = boundary.project(point)
    p1 = boundary.interpolate(max(0, proj_dist - sample_dist))
    p2 = boundary.interpolate(proj_dist + sample_dist)
    dx = p2.x - p1.x
    dy = p2.y - p1.y
    mag = math.hypot(dx, dy)
    if mag < 1e-6:
        return (1.0, 0.0)
    return (dx / mag, dy / mag)


def make_letter_tab_bulb(point, tangent, direction, tab_len=None, tab_height=None):
    if tab_len is None:
        tab_len = TAB_LEN * 0.6  # smaller letter-perimeter tabs
    if tab_height is None:
        tab_height = TAB_HEIGHT * 0.6
    normal = (tangent[1], -tangent[0])
    local = tab_outline(direction)
    pts = []
    for u, v in local:
        u_world = (u - 0.5) * tab_len
        x = point[0] + u_world * tangent[0] + v * tab_height * normal[0]
        y = point[1] + u_world * tangent[1] + v * tab_height * normal[1]
        pts.append((x, y))
    if len(pts) < 3:
        return None
    try:
        poly = Polygon(pts)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if hasattr(poly, "area") and poly.area < 10:
            return None
        return poly
    except Exception:
        return None


def apply_letter_tabs(subpieces, proposed_tabs, letter_union):
    """Modify sub-piece polygons to include letter-perimeter tab bulbs."""
    if letter_union is None:
        return subpieces
    sps = [{**sp, "polygon": sp["polygon"]} for sp in subpieces]
    for prop in proposed_tabs:
        pt = Point(*prop["point"])
        tan = tangent_at_letter_point(letter_union, pt)
        direction = +1 if (hash(prop["point"]) % 2 == 0) else -1
        bulb = make_letter_tab_bulb(prop["point"], tan, direction)
        if bulb is None:
            continue
        bulb_centroid = bulb.centroid
        bulb_inside = letter_union.contains(bulb_centroid)
        for sp in sps:
            if sp["polygon"].distance(pt) > TAB_HEIGHT * 2.0:
                continue
            sp_inside = letter_union.contains(sp["polygon"].centroid)
            try:
                if sp_inside == bulb_inside:
                    new_poly = sp["polygon"].union(bulb)
                else:
                    new_poly = sp["polygon"].difference(bulb)
                if new_poly.is_empty:
                    continue
                if isinstance(new_poly, MultiPolygon):
                    new_poly = max(new_poly.geoms, key=lambda g: g.area)
                if new_poly.is_valid and new_poly.area > 50:
                    sp["polygon"] = new_poly
            except Exception:
                continue
    return sps


def render_pieces(
    subpieces,
    tabs,
    interfered_tab_ids,
    proposed_letter_tabs,
    letter_union,
    text_x,
    text_y,
    font,
    word,
    img_w,
    img_h,
    px,
    py,
    puzzle_w,
    puzzle_h,
    title: str,
    out_path: Path,
    show_tab_markers: bool = True,
    show_letter_marker: bool = True,
):
    n_total = len(subpieces)
    img = Image.new("RGB", (img_w, img_h), BG)
    draw = ImageDraw.Draw(img)

    title_font = find_font(34)
    draw.text((px, 30), title, fill=LABEL_TEXT, font=title_font)

    for sp in subpieces:
        color = pastel(sp.get("serial", 1), n_total)
        pts = shapely_to_pil_points(sp["polygon"])
        if pts:
            draw.polygon(pts, fill=color, outline=CUT, width=2)

    draw.line(
        [
            (px, py),
            (px + puzzle_w, py),
            (px + puzzle_w, py + puzzle_h),
            (px, py + puzzle_h),
            (px, py),
        ],
        fill=CUT,
        width=3,
    )

    if letter_union is not None and show_letter_marker:
        letter_layer = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
        ll = ImageDraw.Draw(letter_layer)
        ll.text(
            (text_x, text_y),
            word,
            fill=(0, 0, 0, 0),
            font=font,
            stroke_width=LETTER_OUTLINE_WIDTH,
            stroke_fill=LETTER_OUTLINE_SUBTLE,
        )
        img.paste(letter_layer, (0, 0), letter_layer)

    if show_tab_markers:
        overlay = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
        ol = ImageDraw.Draw(overlay)
        for tab in tabs:
            if tab["id"] in interfered_tab_ids:
                x, y = tab["midpoint"]
                s = 7
                ol.line([(x - s, y - s), (x + s, y + s)], fill=(200, 30, 30), width=3)
                ol.line([(x - s, y + s), (x + s, y - s)], fill=(200, 30, 30), width=3)
            else:
                x, y = tab["midpoint"]
                ol.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(90, 90, 90, 130))
        for prop in proposed_letter_tabs:
            x, y = prop["point"]
            r = 8
            ol.ellipse(
                [x - r, y - r, x + r, y + r],
                fill=(60, 90, 200),
                outline=(255, 255, 255),
                width=2,
            )
        img.paste(overlay, (0, 0), overlay)

    plf = find_font(16)
    for c in range(COLS):
        for r in range(ROWS):
            label = f"{chr(ord('A') + c)}{r}"
            draw.text(
                (px + c * CELL_W + 4, py + r * CELL_H + 3),
                label,
                fill=PIECE_LABEL_TEXT,
                font=plf,
            )

    serial_font = find_font(24)
    for sp in subpieces:
        cent = sp["polygon"].centroid
        cx, cy = cent.x, cent.y
        label = str(sp.get("serial", "?"))
        bbox = draw.textbbox((0, 0), label, font=serial_font)
        lw, lh = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad = 5
        draw.rectangle(
            [
                cx - lw / 2 - pad,
                cy - lh / 2 - pad,
                cx + lw / 2 + pad,
                cy + lh / 2 + pad,
            ],
            fill=LABEL_BG,
            outline=CUT,
            width=1,
        )
        draw.text(
            (cx - lw / 2 - bbox[0], cy - lh / 2 - bbox[1]),
            label,
            fill=LABEL_TEXT,
            font=serial_font,
        )

    img.save(out_path, "PNG", optimize=True)
    print(f"-> {out_path.name}  ({n_total} pieces)", file=sys.stderr)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--word", default="NORA")
    p.add_argument("--seed", type=int, default=SEED)
    args = p.parse_args()
    word = args.word.upper()

    puzzle_w = COLS * CELL_W
    puzzle_h = ROWS * CELL_H
    img_w = puzzle_w + 2 * MARGIN + TAB_HEIGHT
    img_h = puzzle_h + 2 * MARGIN + TAB_HEIGHT + LEGEND_H
    px = MARGIN
    py = MARGIN

    base_name = f"{word.lower()}_phase3"

    # ===== v01: starting state =====
    print("\n=== v01: starting state ===", file=sys.stderr)
    piece_polys_v01, tabs_v01 = build_pieces_with_skips(args.seed, set())
    letter_union, text_x, text_y, font = render_letter_polygons(
        word, img_w, img_h, px, py, puzzle_w, puzzle_h
    )
    subpieces_v01 = extract_subpieces(piece_polys_v01, letter_union)
    count_tabs_per_subpiece(subpieces_v01, tabs_v01)
    interfered_v01 = find_interfered_tabs(tabs_v01, letter_union)
    proposed_v01 = propose_letter_tabs(subpieces_v01, letter_union)
    subpieces_v01.sort(key=lambda sp: (sp["parent"], sp["region"]))
    for i, sp in enumerate(subpieces_v01, start=1):
        sp["serial"] = i

    render_pieces(
        subpieces_v01,
        tabs_v01,
        interfered_v01,
        proposed_v01,
        letter_union,
        text_x,
        text_y,
        font,
        word,
        img_w,
        img_h,
        px,
        py,
        puzzle_w,
        puzzle_h,
        title=f"{word} Phase 3 v01 — start ({len(subpieces_v01)} sub-pieces, "
        f"{len(interfered_v01)} interfered, {len(proposed_v01)} proposed)",
        out_path=OUT_DIR / f"{base_name}_v01_start.png",
    )

    # ===== v02: remove interfered tabs =====
    print("\n=== v02: remove interfered tabs ===", file=sys.stderr)
    skip_edges = set()
    for tab in tabs_v01:
        if tab["id"] in interfered_v01:
            skip_edges.add(frozenset({tab["between"][0], tab["between"][1]}))

    piece_polys_v02, tabs_v02 = build_pieces_with_skips(args.seed, skip_edges)
    subpieces_v02 = extract_subpieces(piece_polys_v02, letter_union)
    count_tabs_per_subpiece(subpieces_v02, tabs_v02)
    proposed_v02 = propose_letter_tabs(subpieces_v02, letter_union)
    interfered_v02 = find_interfered_tabs(tabs_v02, letter_union)
    subpieces_v02.sort(key=lambda sp: (sp["parent"], sp["region"]))
    for i, sp in enumerate(subpieces_v02, start=1):
        sp["serial"] = i

    render_pieces(
        subpieces_v02,
        tabs_v02,
        interfered_v02,
        proposed_v02,
        letter_union,
        text_x,
        text_y,
        font,
        word,
        img_w,
        img_h,
        px,
        py,
        puzzle_w,
        puzzle_h,
        title=f"{word} Phase 3 v02 — tabs REMOVED "
        f"({len(subpieces_v02)} sub-pieces, "
        f"{len(tabs_v02)} tabs remaining)",
        out_path=OUT_DIR / f"{base_name}_v02_tabs_removed.png",
    )

    # ===== v03: add letter-perimeter tabs =====
    print("\n=== v03: add letter-perimeter tabs ===", file=sys.stderr)
    subpieces_v03 = apply_letter_tabs(subpieces_v02, proposed_v02, letter_union)
    subpieces_v03.sort(key=lambda sp: (sp["parent"], sp["region"]))
    for i, sp in enumerate(subpieces_v03, start=1):
        sp["serial"] = i
    count_tabs_per_subpiece(subpieces_v03, tabs_v02)

    render_pieces(
        subpieces_v03,
        tabs_v02,
        set(),
        proposed_v02,
        letter_union,
        text_x,
        text_y,
        font,
        word,
        img_w,
        img_h,
        px,
        py,
        puzzle_w,
        puzzle_h,
        title=f"{word} Phase 3 v03 — letter tabs ADDED "
        f"({len(subpieces_v03)} sub-pieces; bulbs in sub-piece outlines)",
        out_path=OUT_DIR / f"{base_name}_v03_letter_tabs_added.png",
    )

    # ===== v04: highlight remaining under-tabbed =====
    print("\n=== v04: highlight remaining under-tabbed ===", file=sys.stderr)
    under_v03 = [sp for sp in subpieces_v03 if sp.get("tab_count", 0) < 2]
    render_pieces(
        subpieces_v03,
        tabs_v02,
        set(),
        proposed_v02,
        letter_union,
        text_x,
        text_y,
        font,
        word,
        img_w,
        img_h,
        px,
        py,
        puzzle_w,
        puzzle_h,
        title=f"{word} Phase 3 v04 — {len(under_v03)} sub-pieces still "
        f"low-tab by ORIGINAL tabs (letter tabs not counted by metric)",
        out_path=OUT_DIR / f"{base_name}_v04_under_tabbed.png",
    )

    # ===== v05: final clean =====
    print("\n=== v05: final clean ===", file=sys.stderr)
    render_pieces(
        subpieces_v03,
        tabs_v02,
        set(),
        [],
        letter_union,
        text_x,
        text_y,
        font,
        word,
        img_w,
        img_h,
        px,
        py,
        puzzle_w,
        puzzle_h,
        title=f"{word} Phase 3 v05 — FINAL ({len(subpieces_v03)} pieces, "
        f"ready for cutting)",
        out_path=OUT_DIR / f"{base_name}_v05_final.png",
        show_tab_markers=False,
        show_letter_marker=False,
    )

    print("\nDone.", file=sys.stderr)


if __name__ == "__main__":
    main()
