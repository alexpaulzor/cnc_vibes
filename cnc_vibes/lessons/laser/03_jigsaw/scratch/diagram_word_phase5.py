#!/usr/bin/env python3
"""Phase 5 — shift tabs away from letters instead of removing them.

For each tab on a cell edge, check if its bulb overlaps (or comes too
close to) the letter outline. If it does, shift the tab along the edge
to a clear position. If no clear position exists, drop the tab and
leave that edge as a straight cut.

Then continue with Phase 4's approach: subtract letter shapes from
cells to form pockets, insert letters as their own pieces.

Progression:
  v01: starting state — Phase 4's normal tabs, centered, no shifting
       (lots of tabs are too close to or sliced by letters)
  v02: tabs shifted away from letters (or dropped if no clear spot)
  v03: cells get letter-pocket indents
  v04: final — letters inserted as intact pieces
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.ops import unary_union

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from diagram_word_phase2 import (  # noqa: E402
    CELL_H,
    CELL_W,
    COLS,
    CUT,
    LEGEND_H,
    LETTER_OUTLINE_WIDTH,
    MARGIN,
    PANEL_MM,
    PIECE_LABEL_TEXT,
    PIECE_MM,
    ROWS,
    SEED,
    TAB_CIRCLE_R,
    TAB_HEIGHT,
    TAB_LEN,
    find_font,
    render_letter_polygons,
    tab_outline,
)
from diagram_word_phase2_pieces import pastel  # noqa: E402
from diagram_word_phase4 import (  # noqa: E402
    BG,
    CUT_THICK,
    LETTER_FILL_PHASE,
    render_diagram,
)

OUT_DIR = Path(__file__).resolve().parent.parent / "figs"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# Clearance between a tab cavity and any other edge (one tab radius).
# The bulb in piece A's territory = the cavity in A, so checking the bulb's
# distance against the letter union enforces this on both sides of the edge.
LETTER_CLEARANCE_PX = TAB_CIRCLE_R
# How many shift steps to try in each direction
SHIFT_STEPS = 12
# Step size as fraction of TAB_LEN
SHIFT_STEP_FRAC = 0.2

# After carving letter pockets, merge any fragment thinner than this (i.e.,
# negative-buffer-empty at half this width) or smaller than the area cutoff
# into its largest adjacent neighbor. Both thresholds derive from TAB_CIRCLE_R
# so the rule scales with tab geometry.
MIN_FRAGMENT_THICKNESS_PX = TAB_CIRCLE_R
MIN_FRAGMENT_AREA_PX = 0.10 * CELL_W * CELL_H


# ---------------------------------------------------------------------------
# Tab placement with optional offset along the edge
# ---------------------------------------------------------------------------


def place_tab_at_offset(edge_start, edge_dir, edge_length, direction, offset_u):
    """Generate tab outline points in world coords. offset_u = start of tab
    along the edge (0 = at edge_start, edge_length - TAB_LEN = at far end).
    """
    out = (edge_dir[1], -edge_dir[0])
    local = tab_outline(direction)
    world = []
    for u, v in local:
        u_world = offset_u + u * TAB_LEN
        x = edge_start[0] + u_world * edge_dir[0] + v * TAB_HEIGHT * out[0]
        y = edge_start[1] + u_world * edge_dir[1] + v * TAB_HEIGHT * out[1]
        world.append((x, y))
    return world


def tab_bulb_polygon(edge_start, edge_dir, edge_length, direction, offset_u):
    """Build a closed Polygon for the tab's bulb shape at the given offset."""
    pts = place_tab_at_offset(edge_start, edge_dir, edge_length, direction, offset_u)
    if len(pts) < 3:
        return None
    try:
        poly = Polygon(pts)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if hasattr(poly, "area") and poly.area > 5:
            return poly
    except Exception:
        return None
    return None


def find_clear_tab_offset(
    edge_start,
    edge_dir,
    edge_length,
    letter_union,
    direction,
    clearance: float = LETTER_CLEARANCE_PX,
):
    """Walk through candidate offsets along the edge, return the first one
    where the tab bulb has clearance from letter_union. None if no clear spot.
    """
    max_offset = edge_length - TAB_LEN
    if max_offset <= 0:
        return None
    center = max_offset / 2
    step = TAB_LEN * SHIFT_STEP_FRAC

    # Build candidate list: center first, then alternating left/right
    candidates = [center]
    for i in range(1, SHIFT_STEPS + 1):
        for sign in (-1, +1):
            o = center + sign * i * step
            if 0 <= o <= max_offset:
                candidates.append(o)

    if letter_union is None:
        return center

    for offset_u in candidates:
        bulb = tab_bulb_polygon(edge_start, edge_dir, edge_length, direction, offset_u)
        if bulb is None:
            continue
        try:
            if bulb.distance(letter_union) >= clearance:
                return offset_u
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Build pieces with letter-aware tab placement
# ---------------------------------------------------------------------------


def build_pieces_with_shifted_tabs(seed: int, letter_union):
    """Build piece polygons, shifting tabs away from letters or dropping
    them if no clear position exists.
    """
    random.seed(seed)
    vertical_tabs = {
        (c, r): random.random() > 0.5 for c in range(COLS - 1) for r in range(ROWS)
    }
    horizontal_tabs = {
        (c, r): random.random() > 0.5 for c in range(COLS) for r in range(ROWS - 1)
    }

    # Stats
    stats = {"total": 0, "centered": 0, "shifted": 0, "dropped": 0}

    def add_tab(pts, edge_start, edge_dir, edge_length, direction):
        stats["total"] += 1
        max_offset = edge_length - TAB_LEN
        if max_offset <= 0:
            stats["dropped"] += 1
            return
        center = max_offset / 2
        offset = find_clear_tab_offset(
            edge_start, edge_dir, edge_length, letter_union, direction
        )
        if offset is None:
            stats["dropped"] += 1
            return
        if abs(offset - center) < 1.0:
            stats["centered"] += 1
        else:
            stats["shifted"] += 1
        pts.extend(
            place_tab_at_offset(edge_start, edge_dir, edge_length, direction, offset)[
                1:-1
            ]
        )

    def piece_polygon(col, row, ox, oy):
        x0 = ox + col * CELL_W
        y0 = oy + row * CELL_H
        x1 = x0 + CELL_W
        y1 = y0 + CELL_H
        pts = [(x0, y0)]
        if row > 0:
            bulges_down = horizontal_tabs[(col, row - 1)]
            d = -1 if bulges_down else +1
            add_tab(pts, (x0, y0), (1, 0), CELL_W, d)
        pts.append((x1, y0))
        if col < COLS - 1:
            bulges_right = vertical_tabs[(col, row)]
            d = +1 if bulges_right else -1
            add_tab(pts, (x1, y0), (0, 1), CELL_H, d)
        pts.append((x1, y1))
        if row < ROWS - 1:
            bulges_down = horizontal_tabs[(col, row)]
            d = +1 if bulges_down else -1
            add_tab(pts, (x1, y1), (-1, 0), CELL_W, d)
        pts.append((x0, y1))
        if col > 0:
            bulges_right = vertical_tabs[(col - 1, row)]
            d = -1 if bulges_right else +1
            add_tab(pts, (x0, y1), (0, -1), CELL_H, d)
        return pts

    px = MARGIN
    py = MARGIN
    pieces = {}
    for c in range(COLS):
        for r in range(ROWS):
            pts = piece_polygon(c, r, px, py)
            try:
                poly = Polygon(pts)
                if not poly.is_valid:
                    poly = poly.buffer(0)
                pieces[(c, r)] = poly
            except Exception:
                continue
    return pieces, stats


# ---------------------------------------------------------------------------
# Same as Phase 4's build (centered tabs, no shifting) for v01 baseline
# ---------------------------------------------------------------------------


def build_pieces_centered(seed: int):
    """Build pieces with tabs always centered (no shifting). For comparison."""
    return build_pieces_with_shifted_tabs(seed, None)


# ---------------------------------------------------------------------------
# Merge small or thin fragments left over after pocket carving
# ---------------------------------------------------------------------------


def _adjacency_length(poly_a, poly_b, eps: float = 0.5) -> float:
    """Approximate the length of the shared boundary between two polygons.
    Returns 0 if they only touch at a point or are disjoint."""
    try:
        inter = poly_a.buffer(eps).intersection(poly_b)
        if inter.is_empty:
            return 0.0
        return inter.area / (2 * eps)
    except Exception:
        return 0.0


def _too_thin_or_small(poly) -> bool:
    if poly.area < MIN_FRAGMENT_AREA_PX:
        return True
    try:
        eroded = poly.buffer(-MIN_FRAGMENT_THICKNESS_PX / 2)
        if eroded.is_empty or eroded.area < 1.0:
            return True
    except Exception:
        pass
    return False


def merge_small_fragments(pieces, min_shared: float = 5.0):
    """Iteratively merge any fragment thinner than MIN_FRAGMENT_THICKNESS_PX
    or smaller than MIN_FRAGMENT_AREA_PX into its largest neighbor that
    shares at least min_shared pixels of boundary. Fragments with no such
    neighbor (e.g. the O's counter before letters are inserted) are left
    alone. Returns a new list of pieces."""
    pieces = list(pieces)
    skip: set[int] = set()
    while True:
        small_idx = None
        for i, p in enumerate(pieces):
            if i in skip:
                continue
            if _too_thin_or_small(p["polygon"]):
                small_idx = i
                break
        if small_idx is None:
            return pieces
        best_idx = None
        best_area = -1.0
        for j, q in enumerate(pieces):
            if j == small_idx:
                continue
            shared = _adjacency_length(pieces[small_idx]["polygon"], q["polygon"])
            if shared >= min_shared and q["polygon"].area > best_area:
                best_area = q["polygon"].area
                best_idx = j
        if best_idx is None:
            skip.add(small_idx)
            continue
        merged = unary_union(
            [pieces[best_idx]["polygon"], pieces[small_idx]["polygon"]]
        )
        if isinstance(merged, MultiPolygon):
            merged = max(merged.geoms, key=lambda g: g.area)
        elif isinstance(merged, GeometryCollection):
            polys = [g for g in merged.geoms if isinstance(g, Polygon)]
            if not polys:
                skip.add(small_idx)
                continue
            merged = max(polys, key=lambda g: g.area)
        pieces[best_idx]["polygon"] = merged
        new_skip = set()
        for s in skip:
            if s < small_idx:
                new_skip.add(s)
            elif s > small_idx:
                new_skip.add(s - 1)
        skip = new_skip
        pieces.pop(small_idx)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


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

    base = f"{word.lower()}_phase5"

    letter_union, text_x, text_y, font = render_letter_polygons(
        word, img_w, img_h, px, py, puzzle_w, puzzle_h
    )

    # ===== v01: tabs all at centers (no shifting) =====
    print("\n=== v01: tabs at centers (baseline) ===", file=sys.stderr)
    piece_polys_v01, stats_v01 = build_pieces_centered(args.seed)
    pieces_v01 = []
    for (c, r), poly in sorted(piece_polys_v01.items()):
        pieces_v01.append({"parent": (c, r), "polygon": poly, "kind": "cell"})
    for i, p in enumerate(pieces_v01, start=1):
        p["serial"] = i
    print(f"  tabs: {stats_v01}", file=sys.stderr)
    render_diagram(
        pieces_v01,
        img_w,
        img_h,
        px,
        py,
        puzzle_w,
        puzzle_h,
        title=f"{word} Phase 5 v01 — tabs all centered (baseline; "
        f"{stats_v01['total']} tabs, none shifted)",
        out_path=OUT_DIR / f"{base}_v01_centered.png",
        letter_union=letter_union,
        text_x=text_x,
        text_y=text_y,
        font=font,
        word=word,
        show_letter_marker=True,
    )

    # ===== v02: shift tabs away from letters =====
    print("\n=== v02: shift tabs away from letters ===", file=sys.stderr)
    piece_polys_v02, stats_v02 = build_pieces_with_shifted_tabs(args.seed, letter_union)
    pieces_v02 = []
    for (c, r), poly in sorted(piece_polys_v02.items()):
        pieces_v02.append({"parent": (c, r), "polygon": poly, "kind": "cell"})
    for i, p in enumerate(pieces_v02, start=1):
        p["serial"] = i
    print(f"  tabs: {stats_v02}", file=sys.stderr)
    render_diagram(
        pieces_v02,
        img_w,
        img_h,
        px,
        py,
        puzzle_w,
        puzzle_h,
        title=f"{word} Phase 5 v02 — tabs shifted "
        f"({stats_v02['centered']} centered, {stats_v02['shifted']} shifted, "
        f"{stats_v02['dropped']} dropped of {stats_v02['total']} total)",
        out_path=OUT_DIR / f"{base}_v02_shifted.png",
        letter_union=letter_union,
        text_x=text_x,
        text_y=text_y,
        font=font,
        word=word,
        show_letter_marker=True,
    )

    # ===== v03: carve letter pockets =====
    print("\n=== v03: carve letter pockets from cells ===", file=sys.stderr)
    pieces_v03 = []
    for (c, r), piece in sorted(piece_polys_v02.items()):
        if letter_union is None:
            remaining = piece
        else:
            try:
                remaining = piece.difference(letter_union)
            except Exception:
                remaining = piece
        if remaining.is_empty:
            continue
        if isinstance(remaining, (MultiPolygon, GeometryCollection)):
            for geom in remaining.geoms:
                if isinstance(geom, Polygon) and geom.area > 100:
                    pieces_v03.append(
                        {"parent": (c, r), "polygon": geom, "kind": "cell"}
                    )
        elif isinstance(remaining, Polygon):
            if remaining.area > 100:
                pieces_v03.append(
                    {"parent": (c, r), "polygon": remaining, "kind": "cell"}
                )
    for i, p in enumerate(pieces_v03, start=1):
        p["serial"] = i
    n_before_merge = len(pieces_v03)
    pieces_v03 = merge_small_fragments(pieces_v03)
    for i, p in enumerate(pieces_v03, start=1):
        p["serial"] = i
    print(
        f"  merged {n_before_merge - len(pieces_v03)} sliver fragments into neighbors",
        file=sys.stderr,
    )
    render_diagram(
        pieces_v03,
        img_w,
        img_h,
        px,
        py,
        puzzle_w,
        puzzle_h,
        title=f"{word} Phase 5 v03 — pockets carved + slivers merged "
        f"({len(pieces_v03)} cell fragments, was {n_before_merge} pre-merge)",
        out_path=OUT_DIR / f"{base}_v03_pockets.png",
        letter_union=letter_union,
        text_x=text_x,
        text_y=text_y,
        font=font,
        word=word,
        show_letter_marker=True,
    )

    # ===== v04: insert letters as intact pieces =====
    print("\n=== v04: insert letters ===", file=sys.stderr)
    pieces_v04 = list(pieces_v03)
    letter_polys = []
    if letter_union is not None:
        if isinstance(letter_union, MultiPolygon):
            letter_polys = [g for g in letter_union.geoms if g.area > 100]
        elif isinstance(letter_union, Polygon):
            letter_polys = [letter_union]
    for lp in letter_polys:
        pieces_v04.append({"parent": None, "polygon": lp, "kind": "letter"})
    for i, p in enumerate(pieces_v04, start=1):
        p["serial"] = i
    render_diagram(
        pieces_v04,
        img_w,
        img_h,
        px,
        py,
        puzzle_w,
        puzzle_h,
        title=f"{word} Phase 5 v04 — FINAL ({len(pieces_v04)} pieces, "
        f"tabs shifted away from letters, letters inserted)",
        out_path=OUT_DIR / f"{base}_v04_final.png",
        show_letter_marker=False,
        highlight_letters=False,
    )

    print("\nDone.", file=sys.stderr)


if __name__ == "__main__":
    main()
