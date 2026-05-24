#!/usr/bin/env python3
"""Phase 2 of the NORA jigsaw diagram.

Builds on Phase 1's geometry: same piece polygons with curved tabs, same
letter outline. Adds tab inventory per sub-piece:

  1. Each internal puzzle edge has a tab. We track the tab's midpoint
     (where it meets the cell grid).
  2. The letter outline subdivides some pieces into multiple sub-pieces.
  3. For each sub-piece, count how many original tabs lie on its
     boundary (a tab interface point is "on the boundary" if it's
     within a small proximity threshold).
  4. Classify each sub-piece:
       0 tabs   -> ORPHAN     (red)    — would float free
       1 tab    -> UNDER       (orange) — single attachment, weak
       2+ tabs  -> SUFFICIENT  (green)  — normal puzzle piece

  5. For each background tab whose midpoint is within proximity of the
     letter outline, mark it as "interfered with by letter" (these are
     the tabs Phase 3 will remove).

  6. For each sub-piece that's under-tabbed, mark a candidate location
     on the letter outline where Phase 3 will add a compensating tab.

This script DOES NOT modify the actual cut geometry. It identifies and
visualizes what Phase 3 will change.

Usage:
  python diagram_word_phase2.py [--word NORA] [--seed 7]
"""

from __future__ import annotations

import argparse
import math
import random
import sys
from collections import deque
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import unary_union

OUT_DIR = Path(__file__).resolve().parent.parent / "figs"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ---- physical panel + render scale (same as phase 1) ----
PANEL_MM = 300
PIECE_MM = 50
PX_PER_MM = 5
COLS = PANEL_MM // PIECE_MM
ROWS = PANEL_MM // PIECE_MM
CELL_W = PIECE_MM * PX_PER_MM
CELL_H = PIECE_MM * PX_PER_MM

TAB_LEN = int(0.40 * CELL_W)
TAB_HEIGHT = int(0.18 * CELL_W)
MARGIN = 120
LEGEND_H = 240
SEED = 7
CUT_WIDTH = 3
LETTER_OUTLINE_WIDTH = 4

# ---- Phase 2 algorithm params ----
TAB_PROXIMITY_PX = max(
    8, TAB_HEIGHT // 3
)  # how close a tab midpoint must be to a sub-piece boundary
LETTER_TAB_INTERFERE_PX = (
    TAB_HEIGHT * 2
)  # tabs within this distance of letter outline = interfered

# ---- colors ----
BG = (255, 255, 255)
CUT = (40, 40, 40)
LETTER_OUTLINE = (180, 30, 30)
PIECE_LABEL_TEXT = (70, 70, 70)
LABEL_TEXT = (10, 10, 10)
LABEL_BG = (255, 255, 255)

ORPHAN_COLOR = (240, 130, 130)  # red — 0 tabs
UNDER_COLOR = (250, 195, 110)  # orange — 1 tab
SUFFICIENT_COLOR = (170, 220, 175)  # green — 2+ tabs

CANDIDATE_TAB_COLOR = (60, 90, 200)  # blue dots — proposed letter-perimeter tabs
INTERFERED_TAB_COLOR = (200, 30, 30)  # red X marks — tabs proposed for removal
TAB_DOT_COLOR = (90, 90, 90)  # small dot per existing tab midpoint


# ---------------------------------------------------------------------------
# Curved tab geometry (same as phase 1)
# ---------------------------------------------------------------------------


def bezier_pt(p0, p1, p2, p3, t):
    a = 1 - t
    return (
        a * a * a * p0[0]
        + 3 * a * a * t * p1[0]
        + 3 * a * t * t * p2[0]
        + t * t * t * p3[0],
        a * a * a * p0[1]
        + 3 * a * a * t * p1[1]
        + 3 * a * t * t * p2[1]
        + t * t * t * p3[1],
    )


def tab_outline(direction, n=24):
    pts = [(0.0, 0.0), (0.25, 0.0)]
    d = direction
    p0, p1, p2, p3 = (0.25, 0.0), (0.38, 0.15 * d), (0.10, 1.05 * d), (0.50, 1.05 * d)
    for i in range(1, n + 1):
        pts.append(bezier_pt(p0, p1, p2, p3, i / n))
    p0, p1, p2, p3 = (0.50, 1.05 * d), (0.90, 1.05 * d), (0.62, 0.15 * d), (0.75, 0.0)
    for i in range(1, n + 1):
        pts.append(bezier_pt(p0, p1, p2, p3, i / n))
    pts.append((1.0, 0.0))
    return pts


def place_tab(edge_start, edge_dir, edge_length, direction):
    out = (edge_dir[1], -edge_dir[0])
    tab_start_u = (edge_length - TAB_LEN) / 2
    local = tab_outline(direction)
    world = []
    for u, v in local:
        u_world = tab_start_u + u * TAB_LEN
        x = edge_start[0] + u_world * edge_dir[0] + v * TAB_HEIGHT * out[0]
        y = edge_start[1] + u_world * edge_dir[1] + v * TAB_HEIGHT * out[1]
        world.append((x, y))
    return world


# ---------------------------------------------------------------------------
# Tab inventory: track each edge's tab midpoint (independent of bulb direction)
# ---------------------------------------------------------------------------


def edge_tab_midpoint(edge_start, edge_dir, edge_length):
    """Return the (x, y) midpoint of the edge — the tab's "attachment point"."""
    return (
        edge_start[0] + (edge_length / 2) * edge_dir[0],
        edge_start[1] + (edge_length / 2) * edge_dir[1],
    )


# ---------------------------------------------------------------------------
# Build piece polygons + tab catalog
# ---------------------------------------------------------------------------


def build_pieces_and_tabs(seed: int):
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
            bulges_down = horizontal_tabs[(col, row - 1)]
            d = -1 if bulges_down else +1
            tab_world = place_tab((x0, y0), (1, 0), CELL_W, d)
            pts.extend(tab_world[1:-1])
        pts.append((x1, y0))
        if col < COLS - 1:
            bulges_right = vertical_tabs[(col, row)]
            d = +1 if bulges_right else -1
            tab_world = place_tab((x1, y0), (0, 1), CELL_H, d)
            pts.extend(tab_world[1:-1])
        pts.append((x1, y1))
        if row < ROWS - 1:
            bulges_down = horizontal_tabs[(col, row)]
            d = +1 if bulges_down else -1
            tab_world = place_tab((x1, y1), (-1, 0), CELL_W, d)
            pts.extend(tab_world[1:-1])
        pts.append((x0, y1))
        if col > 0:
            bulges_right = vertical_tabs[(col - 1, row)]
            d = -1 if bulges_right else +1
            tab_world = place_tab((x0, y1), (0, -1), CELL_H, d)
            pts.extend(tab_world[1:-1])
        return pts

    px = MARGIN
    py = MARGIN

    piece_polys = {
        (c, r): piece_polygon(c, r, px, py) for c in range(COLS) for r in range(ROWS)
    }

    # Catalog of tab midpoints: one per internal edge.
    tabs: list[dict] = []
    tab_id = 0
    # Vertical edges (between col c and c+1, in row r)
    for c in range(COLS - 1):
        for r in range(ROWS):
            x_e = px + (c + 1) * CELL_W
            y_top = py + r * CELL_H
            y_bot = y_top + CELL_H
            mp = (x_e, (y_top + y_bot) / 2)
            tab_id += 1
            tabs.append(
                {
                    "id": tab_id,
                    "type": "vertical",
                    "between": ((c, r), (c + 1, r)),
                    "midpoint": mp,
                }
            )
    # Horizontal edges (between row r and r+1, in col c)
    for c in range(COLS):
        for r in range(ROWS - 1):
            x_left = px + c * CELL_W
            x_right = x_left + CELL_W
            y_e = py + (r + 1) * CELL_H
            mp = ((x_left + x_right) / 2, y_e)
            tab_id += 1
            tabs.append(
                {
                    "id": tab_id,
                    "type": "horizontal",
                    "between": ((c, r), (c, r + 1)),
                    "midpoint": mp,
                }
            )

    return piece_polys, tabs


# ---------------------------------------------------------------------------
# Letter outline via Pillow + tracing
# ---------------------------------------------------------------------------


def find_font(size):
    for path in (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def render_letter_polygons(
    word: str, img_w: int, img_h: int, px: int, py: int, puzzle_w: int, puzzle_h: int
):
    """Return (shapely_polygon_union, text_x, text_y, font) for the rendered word.

    The polygon is built by tracing the rasterized letter shape's contour.
    """
    target_letter_h = int(puzzle_h * 0.70)
    font_size = int(target_letter_h * 1.4)
    font = find_font(font_size)

    tmp = Image.new("L", (img_w, img_h), 0)
    td = ImageDraw.Draw(tmp)
    bbox = td.textbbox((0, 0), word, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    max_text_w = int(puzzle_w * 0.88)
    if tw > max_text_w:
        scale = max_text_w / tw
        font_size = int(font_size * scale)
        font = find_font(font_size)
        bbox = ImageDraw.Draw(tmp).textbbox((0, 0), word, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]

    text_x = px + (puzzle_w - tw) // 2 - bbox[0]
    text_y = py + (puzzle_h - th) // 2 - bbox[1]

    # Render letter filled shape to a mask
    letter_mask = Image.new("L", (img_w, img_h), 0)
    ImageDraw.Draw(letter_mask).text(
        (text_x, text_y),
        word,
        fill=255,
        font=font,
    )

    # Trace contours from the mask using a simple marching-squares-like
    # approach: find boundary points and assemble into polygons.
    # For simplicity, just sample the mask and build a rough polygon
    # per connected component.
    letter_polys = _trace_mask_polygons(letter_mask)

    return letter_polys, text_x, text_y, font


def _trace_mask_polygons(mask: Image.Image, sample_step: int = 2):
    """Trace letter outlines from a binary mask using cv2.findContours.

    Uses RETR_CCOMP so we get a two-level hierarchy: outer contours
    (the letter strokes) + inner contours (the holes in O, A, R bowl,
    etc.). Each letter becomes one shapely Polygon with optional holes.
    Returns the union of all letters.
    """
    import cv2
    import numpy as np

    arr = np.array(mask)
    contours, hierarchy = cv2.findContours(arr, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if not contours or hierarchy is None:
        return None

    h = hierarchy[0]  # shape (n_contours, 4): [next, prev, first_child, parent]
    polygons = []
    for i, contour in enumerate(contours):
        if h[i][3] != -1:
            # has a parent → this is a hole; handled when we process its parent
            continue
        outer = [(int(p[0][0]), int(p[0][1])) for p in contour]
        if len(outer) < 3:
            continue
        # Collect holes (children of this outer contour)
        holes = []
        child_idx = h[i][2]
        while child_idx != -1:
            child = contours[child_idx]
            hole_pts = [(int(p[0][0]), int(p[0][1])) for p in child]
            if len(hole_pts) >= 3:
                holes.append(hole_pts)
            child_idx = h[child_idx][0]  # next sibling
        try:
            poly = Polygon(outer, holes=holes)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if hasattr(poly, "area") and poly.area > 100:
                polygons.append(poly)
        except Exception:
            continue

    if not polygons:
        return None
    return unary_union(polygons)


# ---------------------------------------------------------------------------
# Sub-piece extraction + tab counting
# ---------------------------------------------------------------------------


def extract_subpieces(piece_polys, letter_union):
    """For each piece, compute its sub-pieces (intersect/difference with letter).

    Returns list of dicts: {parent: (c, r), polygon: shapely.Polygon,
                            region: 'inside_letter' or 'outside_letter'}
    """
    subpieces = []
    for (c, r), pts in piece_polys.items():
        try:
            piece = Polygon(pts)
            if not piece.is_valid:
                piece = piece.buffer(0)
        except Exception:
            continue
        if letter_union is None:
            subpieces.append(
                {"parent": (c, r), "polygon": piece, "region": "no_letter"}
            )
            continue
        try:
            inside = piece.intersection(letter_union)
            outside = piece.difference(letter_union)
        except Exception:
            subpieces.append({"parent": (c, r), "polygon": piece, "region": "error"})
            continue
        for region_label, geom in (
            ("inside_letter", inside),
            ("outside_letter", outside),
        ):
            if geom.is_empty:
                continue
            if isinstance(geom, MultiPolygon):
                for sub in geom.geoms:
                    if sub.area > 100:
                        subpieces.append(
                            {
                                "parent": (c, r),
                                "polygon": sub,
                                "region": region_label,
                            }
                        )
            elif isinstance(geom, Polygon):
                if geom.area > 100:
                    subpieces.append(
                        {
                            "parent": (c, r),
                            "polygon": geom,
                            "region": region_label,
                        }
                    )
    return subpieces


def count_tabs_per_subpiece(subpieces, tabs):
    """For each sub-piece, count how many tab midpoints lie near its boundary."""
    for sp in subpieces:
        count = 0
        relevant_tab_ids = []
        poly = sp["polygon"]
        if poly is None or poly.is_empty:
            sp["tab_count"] = 0
            sp["tab_ids"] = []
            continue
        boundary = poly.boundary
        if boundary is None or boundary.is_empty:
            sp["tab_count"] = 0
            sp["tab_ids"] = []
            continue
        for tab in tabs:
            tab_pt = Point(*tab["midpoint"])
            if boundary.distance(tab_pt) < TAB_PROXIMITY_PX:
                # Also require: the tab is between this sub-piece's parent and another piece
                if sp["parent"] in tab["between"]:
                    count += 1
                    relevant_tab_ids.append(tab["id"])
        sp["tab_count"] = count
        sp["tab_ids"] = relevant_tab_ids
        sp["tab_ids"] = relevant_tab_ids


def find_interfered_tabs(tabs, letter_union):
    """Find tabs whose midpoint is too close to the letter outline."""
    if letter_union is None:
        return set()
    letter_boundary = letter_union.boundary
    interfered = set()
    for tab in tabs:
        if letter_boundary.distance(Point(*tab["midpoint"])) < LETTER_TAB_INTERFERE_PX:
            interfered.add(tab["id"])
    return interfered


def propose_letter_tabs(subpieces, letter_union):
    """For each under-tabbed sub-piece (< 2 tabs), propose a letter-perimeter
    tab location on the longest segment of its boundary that lies on the
    letter outline.
    """
    if letter_union is None:
        return []
    proposed = []
    letter_boundary = letter_union.boundary
    for sp in subpieces:
        if sp.get("tab_count", 0) >= 2:
            continue
        # Get the part of this sub-piece's boundary that lies on the letter
        try:
            shared = sp["polygon"].boundary.intersection(letter_boundary)
        except Exception:
            continue
        if shared.is_empty:
            continue
        # Pick a representative point on the shared boundary
        # (use the centroid of the longest LineString component if MultiLineString)
        if hasattr(shared, "geoms"):
            longest = max(
                shared.geoms, key=lambda g: g.length if hasattr(g, "length") else 0
            )
        else:
            longest = shared
        if hasattr(longest, "interpolate"):
            mid = longest.interpolate(0.5, normalized=True)
            proposed.append(
                {
                    "for_subpiece_parent": sp["parent"],
                    "for_subpiece_region": sp["region"],
                    "point": (mid.x, mid.y),
                    "tab_count_was": sp["tab_count"],
                }
            )
    return proposed


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def shapely_to_pil_points(geom):
    if isinstance(geom, Polygon):
        return list(geom.exterior.coords)
    return None


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

    print("building piece polygons + tab catalog...", file=sys.stderr)
    piece_polys, tabs = build_pieces_and_tabs(args.seed)
    print(f"  {len(piece_polys)} pieces, {len(tabs)} tabs", file=sys.stderr)

    print("rendering letter polygons...", file=sys.stderr)
    letter_union, text_x, text_y, font = render_letter_polygons(
        word, img_w, img_h, px, py, puzzle_w, puzzle_h
    )

    print("extracting sub-pieces (shapely intersect/diff)...", file=sys.stderr)
    subpieces = extract_subpieces(piece_polys, letter_union)
    print(f"  {len(subpieces)} sub-pieces", file=sys.stderr)

    print("counting tabs per sub-piece...", file=sys.stderr)
    count_tabs_per_subpiece(subpieces, tabs)

    print("identifying interfered tabs...", file=sys.stderr)
    interfered_tab_ids = find_interfered_tabs(tabs, letter_union)
    print(f"  {len(interfered_tab_ids)} tabs interfered by letter", file=sys.stderr)

    print("proposing letter-perimeter tabs...", file=sys.stderr)
    proposed_letter_tabs = propose_letter_tabs(subpieces, letter_union)
    print(f"  {len(proposed_letter_tabs)} proposed letter tabs", file=sys.stderr)

    # Tally classifications
    n_orphan = sum(1 for sp in subpieces if sp.get("tab_count", 0) == 0)
    n_under = sum(1 for sp in subpieces if sp.get("tab_count", 0) == 1)
    n_sufficient = sum(1 for sp in subpieces if sp.get("tab_count", 0) >= 2)

    # ---- Render ----
    img = Image.new("RGB", (img_w, img_h), BG)
    draw = ImageDraw.Draw(img)

    title_font = find_font(34)
    draw.text(
        (px, 30),
        f"{word} jigsaw — Phase 2: tab inventory (orphan={n_orphan}, "
        f"under={n_under}, sufficient={n_sufficient})",
        fill=LABEL_TEXT,
        font=title_font,
    )

    # Draw each sub-piece with classification color
    for sp in subpieces:
        count = sp.get("tab_count", 0)
        if count == 0:
            color = ORPHAN_COLOR
        elif count == 1:
            color = UNDER_COLOR
        else:
            color = SUFFICIENT_COLOR
        pts = shapely_to_pil_points(sp["polygon"])
        if pts:
            draw.polygon(pts, fill=color, outline=CUT, width=2)

    # Draw original piece cut lines on top so they're crisp
    for poly_pts in piece_polys.values():
        draw.line([*poly_pts, poly_pts[0]], fill=CUT, width=CUT_WIDTH)

    # Outer border
    draw.line(
        [
            (px, py),
            (px + puzzle_w, py),
            (px + puzzle_w, py + puzzle_h),
            (px, py + puzzle_h),
            (px, py),
        ],
        fill=CUT,
        width=CUT_WIDTH,
    )

    # Letter outline in red
    if letter_union is not None:
        letter_layer = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
        ll = ImageDraw.Draw(letter_layer)
        ll.text(
            (text_x, text_y),
            word,
            fill=(0, 0, 0, 0),
            font=font,
            stroke_width=LETTER_OUTLINE_WIDTH,
            stroke_fill=LETTER_OUTLINE,
        )
        img.paste(letter_layer, (0, 0), letter_layer)

    # Mark each tab midpoint with a small grey dot
    for tab in tabs:
        if tab["id"] in interfered_tab_ids:
            continue  # drawn differently below
        x, y = tab["midpoint"]
        draw.ellipse([x - 4, y - 4, x + 4, y + 4], fill=TAB_DOT_COLOR)

    # Mark interfered (to-be-removed) tabs with red X
    for tab in tabs:
        if tab["id"] not in interfered_tab_ids:
            continue
        x, y = tab["midpoint"]
        s = 9
        draw.line([(x - s, y - s), (x + s, y + s)], fill=INTERFERED_TAB_COLOR, width=4)
        draw.line([(x - s, y + s), (x + s, y - s)], fill=INTERFERED_TAB_COLOR, width=4)

    # Mark proposed letter-perimeter tab locations with blue filled circles
    for prop in proposed_letter_tabs:
        x, y = prop["point"]
        r = 10
        draw.ellipse(
            [x - r, y - r, x + r, y + r],
            fill=CANDIDATE_TAB_COLOR,
            outline=(255, 255, 255),
            width=2,
        )

    # Piece grid labels
    plf = find_font(20)
    for c in range(COLS):
        for r in range(ROWS):
            label = f"{chr(ord('A') + c)}{r}"
            draw.text(
                (px + c * CELL_W + 8, py + r * CELL_H + 6),
                label,
                fill=PIECE_LABEL_TEXT,
                font=plf,
            )

    # Legend
    legend_y = py + puzzle_h + TAB_HEIGHT + 35
    lf = find_font(22)

    def swatch(y, color, text):
        s = 32
        draw.rectangle([px, y, px + s, y + s], fill=color, outline=CUT, width=2)
        draw.text((px + s + 14, y + 4), text, fill=LABEL_TEXT, font=lf)

    swatch(
        legend_y,
        ORPHAN_COLOR,
        f"orphan sub-piece (0 tabs) — would float free  [{n_orphan} found]",
    )
    swatch(
        legend_y + 42,
        UNDER_COLOR,
        f"under-tabbed (1 tab) — weakly attached  [{n_under} found]",
    )
    swatch(
        legend_y + 84, SUFFICIENT_COLOR, f"sufficient (2+ tabs)  [{n_sufficient} found]"
    )

    # Tab markers in legend
    y = legend_y + 130
    draw.ellipse([px + 10, y + 10, px + 22, y + 22], fill=TAB_DOT_COLOR)
    draw.text(
        (px + 36, y + 7),
        f"existing tab midpoint  [{len(tabs) - len(interfered_tab_ids)} kept]",
        fill=LABEL_TEXT,
        font=lf,
    )

    y += 32
    draw.line([(px + 8, y + 8), (px + 24, y + 24)], fill=INTERFERED_TAB_COLOR, width=4)
    draw.line([(px + 8, y + 24), (px + 24, y + 8)], fill=INTERFERED_TAB_COLOR, width=4)
    draw.text(
        (px + 36, y + 7),
        f"tab interfered by letter — Phase 3 will remove  [{len(interfered_tab_ids)} marked]",
        fill=LABEL_TEXT,
        font=lf,
    )

    y += 32
    draw.ellipse(
        [px + 6, y + 6, px + 26, y + 26],
        fill=CANDIDATE_TAB_COLOR,
        outline=(255, 255, 255),
        width=2,
    )
    draw.text(
        (px + 36, y + 7),
        f"proposed letter-perimeter tab — Phase 3 will add  "
        f"[{len(proposed_letter_tabs)} proposed]",
        fill=LABEL_TEXT,
        font=lf,
    )

    out_path = OUT_DIR / f"{word.lower()}_phase2_tab_inventory.png"
    img.save(out_path, "PNG", optimize=True)
    print(f"-> wrote {out_path}  ({img_w}x{img_h})")


if __name__ == "__main__":
    main()
