#!/usr/bin/env python3
"""Phase 4 — new algorithm: letters as inserted pieces, no slicing.

Approach (much simpler than the abandoned letter-tab algorithm):

  1. Generate the normal 6x6 puzzle tessellation with curved tabs.
  2. For each puzzle piece that overlaps a letter, SUBTRACT the letter
     shape from the piece. Result: that piece now has a letter-shaped
     indent on the side facing the letter.
  3. Each letter becomes its OWN piece, a single intact polygon.
  4. Letters stay in place by being nestled in the indents of the
     surrounding tabbed pieces. No internal letter tabs needed.

Emits a progression:
  v01: starting state — normal puzzle + letter overlay (context only)
  v02: subtract letters from cells (indents appear)
  v03: add letter pieces (each letter as its own polygon)
  v04: final clean view — no markers, just pieces

Also fixes the rendering bugs identified in Phase 3 analysis:
  - MultiPolygon sub-pieces are now drawn properly (each part rendered)
  - Polygon holes are punched out with background color
  - Cut outlines are thicker for better contrast
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon

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
    PANEL_MM,
    PIECE_LABEL_TEXT,
    PIECE_MM,
    ROWS,
    SEED,
    TAB_HEIGHT,
    find_font,
    place_tab,
    render_letter_polygons,
)
from diagram_word_phase2_pieces import pastel  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "figs"
OUT_DIR.mkdir(parents=True, exist_ok=True)

BG = (255, 255, 255)
LETTER_FILL_PHASE = (180, 200, 240)  # distinct fill for letter pieces in v03+
CUT_THICK = 3


# ---------------------------------------------------------------------------
# Piece polygon generation (same as Phase 1, no skips)
# ---------------------------------------------------------------------------


def build_pieces(seed: int):
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
            pts.extend(place_tab((x0, y0), (1, 0), CELL_W, d)[1:-1])
        pts.append((x1, y0))
        if col < COLS - 1:
            bulges_right = vertical_tabs[(col, row)]
            d = +1 if bulges_right else -1
            pts.extend(place_tab((x1, y0), (0, 1), CELL_H, d)[1:-1])
        pts.append((x1, y1))
        if row < ROWS - 1:
            bulges_down = horizontal_tabs[(col, row)]
            d = +1 if bulges_down else -1
            pts.extend(place_tab((x1, y1), (-1, 0), CELL_W, d)[1:-1])
        pts.append((x0, y1))
        if col > 0:
            bulges_right = vertical_tabs[(col - 1, row)]
            d = -1 if bulges_right else +1
            pts.extend(place_tab((x0, y1), (0, -1), CELL_H, d)[1:-1])
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
    return pieces


# ---------------------------------------------------------------------------
# Rendering — handles Polygon, MultiPolygon, and polygons with holes
# ---------------------------------------------------------------------------


def render_geom(
    draw: ImageDraw.ImageDraw, geom, fill, outline, width: int, background_color
):
    """Draw a shapely geom (Polygon or MultiPolygon) with fill, outline,
    and proper hole handling.

    Holes are filled with `background_color` so the polygon visually
    has cutouts. Outline is drawn for both exterior and interior rings.
    """
    if geom.is_empty:
        return
    if isinstance(geom, Polygon):
        ext_pts = list(geom.exterior.coords)
        if len(ext_pts) < 3:
            return
        draw.polygon(ext_pts, fill=fill)
        for interior in geom.interiors:
            int_pts = list(interior.coords)
            if len(int_pts) >= 3:
                draw.polygon(int_pts, fill=background_color)
        # Draw outlines
        draw.line([*ext_pts, ext_pts[0]], fill=outline, width=width)
        for interior in geom.interiors:
            int_pts = list(interior.coords)
            if len(int_pts) >= 3:
                draw.line([*int_pts, int_pts[0]], fill=outline, width=width)
    elif isinstance(geom, MultiPolygon):
        for sub in geom.geoms:
            render_geom(draw, sub, fill, outline, width, background_color)
    elif isinstance(geom, GeometryCollection):
        for sub in geom.geoms:
            if isinstance(sub, (Polygon, MultiPolygon)):
                render_geom(draw, sub, fill, outline, width, background_color)


def safe_centroid(geom):
    """Get a representative point for label placement, robust to multi-parts."""
    try:
        # Use representative_point which is guaranteed to be inside the polygon
        pt = geom.representative_point()
        return (pt.x, pt.y)
    except Exception:
        c = geom.centroid
        return (c.x, c.y)


def render_diagram(
    pieces: list[dict],
    img_w,
    img_h,
    px,
    py,
    puzzle_w,
    puzzle_h,
    title: str,
    out_path: Path,
    letter_union=None,
    text_x=0,
    text_y=0,
    font=None,
    word="",
    show_letter_marker: bool = False,
    highlight_letters: bool = False,
):
    """Render the diagram. `pieces` is a list of dicts with 'polygon', 'serial', 'kind'."""
    n_total = len(pieces)
    img = Image.new("RGB", (img_w, img_h), BG)
    draw = ImageDraw.Draw(img)

    title_font = find_font(34)
    draw.text((px, 30), title, fill=LABEL_TEXT, font=title_font)

    for p in pieces:
        if highlight_letters and p.get("kind") == "letter":
            color = LETTER_FILL_PHASE
        else:
            color = pastel(p.get("serial", 1), n_total)
        render_geom(draw, p["polygon"], color, CUT, CUT_THICK, BG)

    # Outer panel border
    draw.line(
        [
            (px, py),
            (px + puzzle_w, py),
            (px + puzzle_w, py + puzzle_h),
            (px, py + puzzle_h),
            (px, py),
        ],
        fill=CUT,
        width=CUT_THICK,
    )

    # Letter outline overlay (for context only — won't be in v04)
    if show_letter_marker and letter_union is not None and font is not None:
        letter_layer = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
        ll = ImageDraw.Draw(letter_layer)
        ll.text(
            (text_x, text_y),
            word,
            fill=(0, 0, 0, 0),
            font=font,
            stroke_width=LETTER_OUTLINE_WIDTH,
            stroke_fill=(200, 30, 30, 130),
        )
        img.paste(letter_layer, (0, 0), letter_layer)

    # Piece grid labels A0..F5
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

    # Serial number labels
    serial_font = find_font(24)
    for p in pieces:
        cx, cy = safe_centroid(p["polygon"])
        label = str(p.get("serial", "?"))
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

    base = f"{word.lower()}_phase4"

    # ===== v01: starting state =====
    print("\n=== v01: starting state ===", file=sys.stderr)
    piece_polys = build_pieces(args.seed)
    letter_union, text_x, text_y, font = render_letter_polygons(
        word, img_w, img_h, px, py, puzzle_w, puzzle_h
    )

    pieces_v01 = []
    for (c, r), poly in sorted(piece_polys.items()):
        pieces_v01.append({"parent": (c, r), "polygon": poly, "kind": "cell"})
    for i, p in enumerate(pieces_v01, start=1):
        p["serial"] = i

    render_diagram(
        pieces_v01,
        img_w,
        img_h,
        px,
        py,
        puzzle_w,
        puzzle_h,
        title=f"{word} Phase 4 v01 — normal 6x6 puzzle + letter overlay "
        f"({len(pieces_v01)} pieces, letters not yet inserted)",
        out_path=OUT_DIR / f"{base}_v01_start.png",
        letter_union=letter_union,
        text_x=text_x,
        text_y=text_y,
        font=font,
        word=word,
        show_letter_marker=True,
    )

    # ===== v02: subtract letters from cells =====
    print("\n=== v02: subtract letter pockets from cells ===", file=sys.stderr)
    pieces_v02 = []
    for (c, r), piece in sorted(piece_polys.items()):
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
                    pieces_v02.append(
                        {"parent": (c, r), "polygon": geom, "kind": "cell"}
                    )
        elif isinstance(remaining, Polygon):
            if remaining.area > 100:
                pieces_v02.append(
                    {"parent": (c, r), "polygon": remaining, "kind": "cell"}
                )

    for i, p in enumerate(pieces_v02, start=1):
        p["serial"] = i

    render_diagram(
        pieces_v02,
        img_w,
        img_h,
        px,
        py,
        puzzle_w,
        puzzle_h,
        title=f"{word} Phase 4 v02 — letter pockets carved from cells "
        f"({len(pieces_v02)} cell fragments, letters not yet inserted)",
        out_path=OUT_DIR / f"{base}_v02_pockets.png",
        letter_union=letter_union,
        text_x=text_x,
        text_y=text_y,
        font=font,
        word=word,
        show_letter_marker=True,
    )

    # ===== v03: add letters as their own pieces =====
    print("\n=== v03: insert letters as intact pieces ===", file=sys.stderr)
    pieces_v03 = list(pieces_v02)
    letter_polys = []
    if letter_union is not None:
        if isinstance(letter_union, MultiPolygon):
            letter_polys = [g for g in letter_union.geoms if g.area > 100]
        elif isinstance(letter_union, Polygon):
            letter_polys = [letter_union]

    for lp in letter_polys:
        pieces_v03.append({"parent": None, "polygon": lp, "kind": "letter"})

    for i, p in enumerate(pieces_v03, start=1):
        p["serial"] = i

    render_diagram(
        pieces_v03,
        img_w,
        img_h,
        px,
        py,
        puzzle_w,
        puzzle_h,
        title=f"{word} Phase 4 v03 — letters inserted as intact pieces "
        f"({len(pieces_v03)} total: {len(pieces_v02)} cell fragments "
        f"+ {len(letter_polys)} letter pieces)",
        out_path=OUT_DIR / f"{base}_v03_letters_inserted.png",
        letter_union=letter_union,
        text_x=text_x,
        text_y=text_y,
        font=font,
        word=word,
        show_letter_marker=False,
        highlight_letters=True,
    )

    # ===== v04: clean final =====
    print("\n=== v04: clean final ===", file=sys.stderr)
    render_diagram(
        pieces_v03,
        img_w,
        img_h,
        px,
        py,
        puzzle_w,
        puzzle_h,
        title=f"{word} Phase 4 v04 — FINAL ({len(pieces_v03)} pieces, "
        f"letters held by nesting in surrounding tabbed pieces)",
        out_path=OUT_DIR / f"{base}_v04_final.png",
        show_letter_marker=False,
        highlight_letters=False,
    )

    print("\nDone.", file=sys.stderr)


if __name__ == "__main__":
    main()
