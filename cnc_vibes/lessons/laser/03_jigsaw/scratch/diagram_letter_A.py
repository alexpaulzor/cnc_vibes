#!/usr/bin/env python3
"""Generate a diagram of a 6x4 jigsaw with a letter A overlaid.

Color-codes each piece by its relationship to the letter shape:
  * white       — piece is fully OUTSIDE the letter
  * blue        — piece is fully INSIDE the letter
  * orange      — piece STRADDLES the letter boundary (the interesting
                  case for the name-preserving cut algorithm)

Letter outline drawn in red over the top. Piece labels (A0..F3) in
each piece's body. Legend at the bottom.

Used as a visual aid for designing the name-preserving cut algorithm
in lesson 3c. Not part of any lesson's runtime code.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path(__file__).resolve().parent.parent / "figs"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_PATH = OUT_DIR / "puzzle_6x4_with_letter_A.png"


# ---- puzzle layout ----
COLS = 6
ROWS = 4
CELL_W = 220
CELL_H = 180
TAB_W = 80
TAB_D = 30
MARGIN = 80
LEGEND_H = 180

# ---- colors ----
BG = (255, 255, 255)
OUTSIDE_COLOR = (250, 250, 250)
INSIDE_COLOR = (140, 180, 230)
BOUNDARY_COLOR = (245, 175, 90)
GRID = (40, 40, 40)
TEXT = (10, 10, 10)
LETTER_OUTLINE = (200, 30, 30)
LETTER_FILL = (200, 30, 30, 35)


def piece_polygon(col: int, row: int, ox: int, oy: int) -> list[tuple[int, int]]:
    """Walk a piece's perimeter and return its polygon vertices.

    Tab convention: every internal vertical edge has a convex tab
    extending RIGHT from the left piece. Every internal horizontal
    edge has a convex tab extending DOWN from the upper piece.
    """
    x0 = ox + col * CELL_W
    y0 = oy + row * CELL_H
    x1 = x0 + CELL_W
    y1 = y0 + CELL_H

    tab_xa = x0 + (CELL_W - TAB_W) // 2
    tab_xb = x0 + (CELL_W + TAB_W) // 2
    tab_ya = y0 + (CELL_H - TAB_W) // 2
    tab_yb = y0 + (CELL_H + TAB_W) // 2

    pts: list[tuple[int, int]] = [(x0, y0)]

    # Top edge L→R; concave notch (going down INTO piece) if not top row.
    if row > 0:
        pts += [
            (tab_xa, y0),
            (tab_xa, y0 + TAB_D),
            (tab_xb, y0 + TAB_D),
            (tab_xb, y0),
        ]
    pts.append((x1, y0))

    # Right edge T→B; convex tab going OUT to the right if not rightmost col.
    if col < COLS - 1:
        pts += [
            (x1, tab_ya),
            (x1 + TAB_D, tab_ya),
            (x1 + TAB_D, tab_yb),
            (x1, tab_yb),
        ]
    pts.append((x1, y1))

    # Bottom edge R→L; convex tab going OUT downward if not bottom row.
    if row < ROWS - 1:
        pts += [
            (tab_xb, y1),
            (tab_xb, y1 + TAB_D),
            (tab_xa, y1 + TAB_D),
            (tab_xa, y1),
        ]
    pts.append((x0, y1))

    # Left edge B→T; concave notch (going right INTO piece) if not leftmost col.
    if col > 0:
        pts += [
            (x0, tab_yb),
            (x0 + TAB_D, tab_yb),
            (x0 + TAB_D, tab_ya),
            (x0, tab_ya),
        ]
    return pts


def find_font(size: int):
    """Try to locate a bold sans-serif font. Falls back to default."""
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
        "Arial Bold.ttf",
        "Arial.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    print("warning: no truetype font found; using default bitmap font", file=sys.stderr)
    return ImageFont.load_default()


def main() -> None:
    puzzle_w = COLS * CELL_W
    puzzle_h = ROWS * CELL_H
    img_w = puzzle_w + 2 * MARGIN + TAB_D
    img_h = puzzle_h + 2 * MARGIN + TAB_D + LEGEND_H

    img = Image.new("RGB", (img_w, img_h), BG)
    draw = ImageDraw.Draw(img)
    px = MARGIN
    py = MARGIN

    # Title
    title_font = find_font(28)
    draw.text((px, 20), "6x4 jigsaw with letter A overlay", fill=TEXT, font=title_font)

    # Render letter A to a black-and-white mask the size of the image.
    letter_size = int(puzzle_h * 1.35)
    letter_font = find_font(letter_size)
    letter_mask = Image.new("L", (img_w, img_h), 0)
    lm_draw = ImageDraw.Draw(letter_mask)
    bbox = lm_draw.textbbox((0, 0), "A", font=letter_font)
    letter_w_actual = bbox[2] - bbox[0]
    letter_h_actual = bbox[3] - bbox[1]
    letter_x = px + (puzzle_w - letter_w_actual) // 2 - bbox[0]
    letter_y = py + (puzzle_h - letter_h_actual) // 2 - bbox[1]
    lm_draw.text((letter_x, letter_y), "A", fill=255, font=letter_font)

    # Classify each piece by overlap with the letter mask.
    SAMPLE_STEP = 6
    pieces = {}
    for col in range(COLS):
        for row in range(ROWS):
            poly = piece_polygon(col, row, px, py)
            piece_mask = Image.new("L", (img_w, img_h), 0)
            ImageDraw.Draw(piece_mask).polygon(poly, fill=255)

            xs = [p[0] for p in poly]
            ys = [p[1] for p in poly]
            x0, x1 = min(xs), max(xs)
            y0, y1 = min(ys), max(ys)
            inside = outside = 0
            for sx in range(x0, x1, SAMPLE_STEP):
                for sy in range(y0, y1, SAMPLE_STEP):
                    if piece_mask.getpixel((sx, sy)) > 0:
                        if letter_mask.getpixel((sx, sy)) > 128:
                            inside += 1
                        else:
                            outside += 1
            if inside == 0:
                cat = "outside"
            elif outside == 0:
                cat = "inside"
            else:
                cat = "boundary"
            pieces[(col, row)] = (cat, poly)

    # Fill + outline each piece polygon
    for (col, row), (cat, poly) in pieces.items():
        color = {
            "outside": OUTSIDE_COLOR,
            "inside": INSIDE_COLOR,
            "boundary": BOUNDARY_COLOR,
        }[cat]
        draw.polygon(poly, fill=color, outline=GRID, width=3)

    # Letter overlay (translucent fill + bold outline) on an RGBA layer
    letter_layer = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    ll_draw = ImageDraw.Draw(letter_layer)
    ll_draw.text(
        (letter_x, letter_y),
        "A",
        fill=LETTER_FILL,
        font=letter_font,
        stroke_width=5,
        stroke_fill=LETTER_OUTLINE,
    )
    img.paste(letter_layer, (0, 0), letter_layer)

    # Piece labels with white background for legibility
    label_font = find_font(32)
    for (col, row), (cat, poly) in pieces.items():
        label = f"{chr(ord('A') + col)}{row}"
        cx = px + col * CELL_W + CELL_W // 2
        cy = py + row * CELL_H + CELL_H // 2
        lbbox = draw.textbbox((0, 0), label, font=label_font)
        lw = lbbox[2] - lbbox[0]
        lh = lbbox[3] - lbbox[1]
        pad = 8
        draw.rectangle(
            [
                cx - lw // 2 - pad,
                cy - lh // 2 - pad,
                cx + lw // 2 + pad,
                cy + lh // 2 + pad,
            ],
            fill=(255, 255, 255),
            outline=GRID,
            width=1,
        )
        draw.text(
            (cx - lw // 2 - lbbox[0], cy - lh // 2 - lbbox[1]),
            label,
            fill=TEXT,
            font=label_font,
        )

    # Legend
    legend_font = find_font(22)
    legend_y = py + puzzle_h + TAB_D + 35
    legend_x = px
    sw = 36
    spacing = sw + 18

    def swatch(y, color, text):
        draw.rectangle(
            [legend_x, y, legend_x + sw, y + sw], fill=color, outline=GRID, width=2
        )
        draw.text((legend_x + sw + 14, y + 5), text, fill=TEXT, font=legend_font)

    swatch(legend_y, OUTSIDE_COLOR, "outside the letter — regular puzzle pieces")
    swatch(
        legend_y + spacing,
        INSIDE_COLOR,
        "inside the letter — candidates for the letter's sub-puzzle",
    )
    swatch(
        legend_y + 2 * spacing,
        BOUNDARY_COLOR,
        "straddles the letter boundary — the interesting case",
    )

    # Letter outline swatch
    y = legend_y + 3 * spacing
    draw.line(
        [(legend_x, y + sw // 2), (legend_x + sw, y + sw // 2)],
        fill=LETTER_OUTLINE,
        width=5,
    )
    draw.text(
        (legend_x + sw + 14, y + 5),
        "letter outline (cuts that define the letter region)",
        fill=TEXT,
        font=legend_font,
    )

    img.save(OUT_PATH, "PNG", optimize=True)
    print(f"-> wrote {OUT_PATH}  ({img_w}x{img_h})")

    # Print a text summary too
    print("\nPiece classifications:")
    for cat in ("inside", "boundary", "outside"):
        names = [
            f"{chr(ord('A') + c)}{r}" for (c, r), (k, _) in pieces.items() if k == cat
        ]
        print(f"  {cat:>10}: {', '.join(sorted(names))}")


if __name__ == "__main__":
    main()
