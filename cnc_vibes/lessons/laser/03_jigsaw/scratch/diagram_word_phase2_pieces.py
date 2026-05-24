#!/usr/bin/env python3
"""Phase 2b — clean per-piece view of the would-be final puzzle.

Same analysis as diagram_word_phase2.py but renders for clarity:
each sub-piece gets a unique pastel color + serial number, no
overlapping wireframes from the original cut pattern. Letter outline
shown subtly in the background so you can see where it lies relative
to pieces.

Use this view for "color by numbers" — tell me which serial-numbered
pieces should be merged under your algorithm.

Tab markers (X for removed, blue dot for proposed letter-perimeter)
are overlaid lightly so you can still see the algorithm's decisions.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Import analysis from Phase 2 (same directory)
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
    build_pieces_and_tabs,
    count_tabs_per_subpiece,
    extract_subpieces,
    find_font,
    find_interfered_tabs,
    propose_letter_tabs,
    render_letter_polygons,
    shapely_to_pil_points,
)

OUT_DIR = Path(__file__).resolve().parent.parent / "figs"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ---- visual params for this view ----
BG = (255, 255, 255)
LETTER_OUTLINE_SUBTLE = (200, 30, 30, 130)  # translucent red for context
TAB_REMOVE_COLOR = (200, 30, 30)
LETTER_TAB_PROPOSE_COLOR = (60, 90, 200)
TAB_KEEP_DOT = (90, 90, 90, 110)  # translucent


def pastel(i: int, total: int) -> tuple[int, int, int]:
    """Generate a distinct pastel color spaced by hue + slight V/S jitter
    so adjacent IDs are easy to tell apart even with similar hues.
    """
    h = (i * 360 / max(total, 1)) % 360
    # Alternate saturation/value per index for adjacent contrast
    s = 0.35 if i % 2 == 0 else 0.45
    v = 0.96 if i % 3 == 0 else 0.92
    c = v * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = v - c
    if h < 60:
        r, g, b = c, x, 0
    elif h < 120:
        r, g, b = x, c, 0
    elif h < 180:
        r, g, b = 0, c, x
    elif h < 240:
        r, g, b = 0, x, c
    elif h < 300:
        r, g, b = x, 0, c
    else:
        r, g, b = c, 0, x
    return (int((r + m) * 255), int((g + m) * 255), int((b + m) * 255))


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

    print("running analysis...", file=sys.stderr)
    piece_polys, tabs = build_pieces_and_tabs(args.seed)
    letter_union, text_x, text_y, font = render_letter_polygons(
        word, img_w, img_h, px, py, puzzle_w, puzzle_h
    )
    subpieces = extract_subpieces(piece_polys, letter_union)
    count_tabs_per_subpiece(subpieces, tabs)
    interfered_tab_ids = find_interfered_tabs(tabs, letter_union)
    proposed_letter_tabs = propose_letter_tabs(subpieces, letter_union)

    # Assign serial IDs and colors. Sort sub-pieces by (parent (c, r), then
    # region) for stable numbering across runs.
    subpieces.sort(key=lambda sp: (sp["parent"], sp["region"]))
    for i, sp in enumerate(subpieces, start=1):
        sp["serial"] = i

    n_total = len(subpieces)
    print(f"  {n_total} sub-pieces total", file=sys.stderr)

    # ---- Render ----
    img = Image.new("RGB", (img_w, img_h), BG)
    draw = ImageDraw.Draw(img)

    title_font = find_font(34)
    draw.text(
        (px, 30),
        f"{word} — would-be final pieces ({n_total} pieces, each uniquely numbered)",
        fill=LABEL_TEXT,
        font=title_font,
    )

    # Fill each sub-piece with its unique color, outline in dark grey.
    # NO original-piece cut lines drawn — they're subsumed by the
    # sub-piece boundaries.
    for sp in subpieces:
        color = pastel(sp["serial"], n_total)
        pts = shapely_to_pil_points(sp["polygon"])
        if pts:
            draw.polygon(pts, fill=color, outline=CUT, width=2)

    # Outer panel border (cleaner)
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

    # Letter outline subtly (so you can see the seam where pieces split)
    if letter_union is not None:
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

    # Light tab-marker overlay for context (smaller / more transparent)
    overlay = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    ol = ImageDraw.Draw(overlay)
    for tab in tabs:
        if tab["id"] in interfered_tab_ids:
            x, y = tab["midpoint"]
            s = 7
            ol.line([(x - s, y - s), (x + s, y + s)], fill=TAB_REMOVE_COLOR, width=3)
            ol.line([(x - s, y + s), (x + s, y - s)], fill=TAB_REMOVE_COLOR, width=3)
        else:
            x, y = tab["midpoint"]
            ol.ellipse([x - 3, y - 3, x + 3, y + 3], fill=TAB_KEEP_DOT)
    for prop in proposed_letter_tabs:
        x, y = prop["point"]
        r = 8
        ol.ellipse(
            [x - r, y - r, x + r, y + r],
            fill=LETTER_TAB_PROPOSE_COLOR,
            outline=(255, 255, 255),
            width=2,
        )
    img.paste(overlay, (0, 0), overlay)

    # Piece grid labels (A0..F5) in tiny grey at corners, for cross-reference
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

    # Serial number labels at each sub-piece centroid
    serial_font = find_font(26)
    for sp in subpieces:
        cent = sp["polygon"].centroid
        cx, cy = cent.x, cent.y
        label = str(sp["serial"])
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

    # Legend
    legend_y = py + puzzle_h + TAB_HEIGHT + 35
    lf = find_font(20)
    draw.text(
        (px, legend_y),
        f"Panel: {PANEL_MM}x{PANEL_MM} mm  |  Grid: {COLS}x{ROWS}={COLS * ROWS} base pieces, "
        f"{n_total} after letter cuts  |  Piece size: {PIECE_MM} mm",
        fill=LABEL_TEXT,
        font=lf,
    )
    draw.text(
        (px, legend_y + 28),
        f"Each numbered region = one would-be physical puzzle piece. "
        f"Use the numbers to tell me which to merge.",
        fill=LABEL_TEXT,
        font=lf,
    )

    y = legend_y + 65
    # Letter outline swatch
    draw.line(
        [(px, y + 12), (px + 40, y + 12)],
        fill=(200, 30, 30),
        width=LETTER_OUTLINE_WIDTH,
    )
    draw.text(
        (px + 52, y), f"letter outline (subtle, for context)", fill=LABEL_TEXT, font=lf
    )
    y += 30
    # X marker
    draw.line([(px + 6, y + 6), (px + 24, y + 24)], fill=TAB_REMOVE_COLOR, width=3)
    draw.line([(px + 6, y + 24), (px + 24, y + 6)], fill=TAB_REMOVE_COLOR, width=3)
    draw.text(
        (px + 36, y + 5),
        f"existing tab to be REMOVED ({len(interfered_tab_ids)})",
        fill=LABEL_TEXT,
        font=lf,
    )
    y += 30
    # Blue dot marker
    draw.ellipse(
        [px + 5, y + 5, px + 25, y + 25],
        fill=LETTER_TAB_PROPOSE_COLOR,
        outline=(255, 255, 255),
        width=2,
    )
    draw.text(
        (px + 36, y + 5),
        f"proposed letter-perimeter tab ({len(proposed_letter_tabs)})",
        fill=LABEL_TEXT,
        font=lf,
    )
    y += 30
    # Grey dot
    draw.ellipse([px + 11, y + 11, px + 19, y + 19], fill=(90, 90, 90))
    draw.text(
        (px + 36, y + 5),
        f"existing tab kept ({len(tabs) - len(interfered_tab_ids)})",
        fill=LABEL_TEXT,
        font=lf,
    )

    out_path = OUT_DIR / f"{word.lower()}_phase2_pieces.png"
    img.save(out_path, "PNG", optimize=True)
    print(f"-> wrote {out_path}  ({img_w}x{img_h})  {n_total} pieces")


if __name__ == "__main__":
    main()
