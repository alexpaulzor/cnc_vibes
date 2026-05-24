#!/usr/bin/env python3
"""NORA jigsaw diagram — Phase 1.

Renders a 30 cm x 30 cm jigsaw panel with the word NORA overlaid as
additional cut lines. 6 x 6 grid (50 mm pieces). Numbered regions
exactly as in diagram_letter_A_curved.py — this is just a scaled-up
NORA version before we layer on the tab-aware algorithm.

Phase 2 will add: tab inventory per sub-piece, letter-perimeter tab
insertion where a sub-piece has < 2 tabs, removal of background-puzzle
tabs that are visually crowded by the letter outline.
"""

from __future__ import annotations

import random
import sys
from collections import deque
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path(__file__).resolve().parent.parent / "figs"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_PATH = OUT_DIR / "nora_phase1_standard_jigsaw.png"

# ---- physical panel + render scale ----
PANEL_MM = 300  # 30 cm
PIECE_MM = 50  # 5 cm per piece -> 6x6 grid
PX_PER_MM = 5
COLS = PANEL_MM // PIECE_MM  # 6
ROWS = PANEL_MM // PIECE_MM  # 6
CELL_W = PIECE_MM * PX_PER_MM  # 250 px
CELL_H = PIECE_MM * PX_PER_MM  # 250 px

# ---- tab geometry ----
TAB_LEN = int(0.40 * CELL_W)  # ~100 px (20 mm) along edge
TAB_HEIGHT = int(0.18 * CELL_W)  # ~45 px (9 mm) deep
MARGIN = 120
LEGEND_H = 200
SEED = 7
CUT_WIDTH = 3
LETTER_OUTLINE_WIDTH = 4

# ---- text ----
TEXT = "NORA"

# ---- colors ----
BG = (255, 255, 255)
CUT = (40, 40, 40)
LETTER_OUTLINE = (180, 30, 30)
LABEL_BG = (255, 255, 255)
LABEL_TEXT = (10, 10, 10)
PIECE_LABEL_TEXT = (70, 70, 70)


random.seed(SEED)


# ---------------------------------------------------------------------------
# Curved Bezier tabs (same as diagram_letter_A_curved.py)
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


def tab_outline(direction: int, n: int = 24) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = [(0.0, 0.0), (0.30, 0.0)]
    d = direction
    p0, p1, p2, p3 = (0.30, 0.0), (0.16, 0.50 * d), (0.16, 1.10 * d), (0.50, 1.10 * d)
    for i in range(1, n + 1):
        pts.append(bezier_pt(p0, p1, p2, p3, i / n))
    p0, p1, p2, p3 = (0.50, 1.10 * d), (0.84, 1.10 * d), (0.84, 0.50 * d), (0.70, 0.0)
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
# Piece polygon (with random tab directions)
# ---------------------------------------------------------------------------


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
        direction = -1 if bulges_down else +1
        tab_world = place_tab((x0, y0), (1, 0), CELL_W, direction)
        pts.extend(tab_world[1:-1])
    pts.append((x1, y0))

    if col < COLS - 1:
        bulges_right = vertical_tabs[(col, row)]
        direction = +1 if bulges_right else -1
        tab_world = place_tab((x1, y0), (0, 1), CELL_H, direction)
        pts.extend(tab_world[1:-1])
    pts.append((x1, y1))

    if row < ROWS - 1:
        bulges_down = horizontal_tabs[(col, row)]
        direction = +1 if bulges_down else -1
        tab_world = place_tab((x1, y1), (-1, 0), CELL_W, direction)
        pts.extend(tab_world[1:-1])
    pts.append((x0, y1))

    if col > 0:
        bulges_right = vertical_tabs[(col - 1, row)]
        direction = -1 if bulges_right else +1
        tab_world = place_tab((x0, y1), (0, -1), CELL_H, direction)
        pts.extend(tab_world[1:-1])

    return pts


# ---------------------------------------------------------------------------
# Font + region detection (shared with previous diagram)
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


def find_regions(cut_mask, min_size=2000):
    width, height = cut_mask.size
    cut_bytes = cut_mask.tobytes()
    visited = bytearray(width * height)
    regions = []
    region_id = 0
    for sidx in range(width * height):
        if cut_bytes[sidx] != 0 or visited[sidx]:
            continue
        region_id += 1
        sx_seed, sy_seed = sidx % width, sidx // width
        sum_x = sum_y = count = 0
        queue = deque([(sx_seed, sy_seed)])
        visited[sidx] = 1
        while queue:
            x, y = queue.popleft()
            sum_x += x
            sum_y += y
            count += 1
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= nx < width and 0 <= ny < height:
                    nidx = ny * width + nx
                    if not visited[nidx] and cut_bytes[nidx] == 0:
                        visited[nidx] = 1
                        queue.append((nx, ny))
        if count >= min_size:
            regions.append(
                {
                    "id": region_id,
                    "pixels": count,
                    "centroid": (sum_x / count, sum_y / count),
                }
            )
    return regions


def pastel(i, total):
    h = (i * 360 / max(total, 1)) % 360
    s, v = 0.30, 0.97
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


def find_open_seed(mask, cx, cy, radius=80):
    w, h = mask.size
    b = mask.tobytes()
    for r in range(radius):
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if max(abs(dx), abs(dy)) != r and r > 0:
                    continue
                nx, ny = cx + dx, cy + dy
                if 0 <= nx < w and 0 <= ny < h and b[ny * w + nx] == 0:
                    return (nx, ny)
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    puzzle_w = COLS * CELL_W
    puzzle_h = ROWS * CELL_H
    img_w = puzzle_w + 2 * MARGIN + TAB_HEIGHT
    img_h = puzzle_h + 2 * MARGIN + TAB_HEIGHT + LEGEND_H

    px = MARGIN
    py = MARGIN

    piece_polys = {
        (c, r): piece_polygon(c, r, px, py) for c in range(COLS) for r in range(ROWS)
    }

    # Cut mask
    cut_mask = Image.new("L", (img_w, img_h), 0)
    cm_draw = ImageDraw.Draw(cut_mask)

    outer = [
        (px, py),
        (px + puzzle_w, py),
        (px + puzzle_w, py + puzzle_h),
        (px, py + puzzle_h),
    ]
    cm_draw.line([*outer, outer[0]], fill=255, width=CUT_WIDTH)
    for poly in piece_polys.values():
        cm_draw.line([*poly, poly[0]], fill=255, width=CUT_WIDTH)

    # Render NORA letters
    # Try to make letter height = ~70% of puzzle height for visual balance
    target_letter_h = int(puzzle_h * 0.70)
    font_size = int(target_letter_h * 1.4)  # font size includes ascenders etc
    font = find_font(font_size)

    # Render NORA to find its bounding box, then center it
    tmp = Image.new("L", (img_w, img_h), 0)
    td = ImageDraw.Draw(tmp)
    bbox = td.textbbox((0, 0), TEXT, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    # If NORA at this font size is too wide, scale down
    max_text_w = int(puzzle_w * 0.88)
    if tw > max_text_w:
        scale = max_text_w / tw
        font_size = int(font_size * scale)
        font = find_font(font_size)
        bbox = ImageDraw.Draw(tmp).textbbox((0, 0), TEXT, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]

    text_x = px + (puzzle_w - tw) // 2 - bbox[0]
    text_y = py + (puzzle_h - th) // 2 - bbox[1]

    # Draw the letter outline as additional cuts
    cm_draw.text(
        (text_x, text_y),
        TEXT,
        fill=0,
        font=font,
        stroke_width=LETTER_OUTLINE_WIDTH,
        stroke_fill=255,
    )

    print("flood-filling regions...", file=sys.stderr)
    regions = find_regions(cut_mask, min_size=2000)
    print(f"found {len(regions)} regions", file=sys.stderr)

    # Build final image
    img = Image.new("RGB", (img_w, img_h), BG)
    draw = ImageDraw.Draw(img)

    title_font = find_font(34)
    draw.text(
        (px, 40),
        f"NORA on 30x30cm jigsaw (6x6 pieces, 50mm each) — {len(regions)} regions, phase 1",
        fill=LABEL_TEXT,
        font=title_font,
    )

    # Pastel fill per region
    for i, region in enumerate(regions):
        color = pastel(i, len(regions))
        cx, cy = region["centroid"]
        seed = find_open_seed(cut_mask, int(cx), int(cy))
        if seed:
            ImageDraw.floodfill(img, seed, color, thresh=10)

    # Re-draw cut lines
    draw.line([*outer, outer[0]], fill=CUT, width=CUT_WIDTH)
    for poly in piece_polys.values():
        draw.line([*poly, poly[0]], fill=CUT, width=CUT_WIDTH)

    # Letter outline in red
    letter_layer = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    ll_draw = ImageDraw.Draw(letter_layer)
    ll_draw.text(
        (text_x, text_y),
        TEXT,
        fill=(0, 0, 0, 0),
        font=font,
        stroke_width=LETTER_OUTLINE_WIDTH,
        stroke_fill=LETTER_OUTLINE,
    )
    img.paste(letter_layer, (0, 0), letter_layer)

    # Piece grid labels (A0..F5)
    piece_label_font = find_font(22)
    for c in range(COLS):
        for r in range(ROWS):
            label = f"{chr(ord('A') + c)}{r}"
            draw.text(
                (px + c * CELL_W + 8, py + r * CELL_H + 6),
                label,
                fill=PIECE_LABEL_TEXT,
                font=piece_label_font,
            )

    # Region serial numbers at centroids
    region_font = find_font(28)
    for region in regions:
        cx, cy = region["centroid"]
        label = str(region["id"])
        b = draw.textbbox((0, 0), label, font=region_font)
        lw, lh = b[2] - b[0], b[3] - b[1]
        pad = 6
        draw.rectangle(
            [
                cx - lw // 2 - pad,
                cy - lh // 2 - pad,
                cx + lw // 2 + pad,
                cy + lh // 2 + pad,
            ],
            fill=LABEL_BG,
            outline=CUT,
            width=1,
        )
        draw.text(
            (cx - lw // 2 - b[0], cy - lh // 2 - b[1]),
            label,
            fill=LABEL_TEXT,
            font=region_font,
        )

    # Legend
    legend_y = py + puzzle_h + TAB_HEIGHT + 40
    lf = find_font(22)
    draw.text(
        (px, legend_y),
        f"Panel: 30cm x 30cm  |  Grid: {COLS}x{ROWS} = {COLS * ROWS} base pieces  "
        f"|  Piece size: {PIECE_MM}mm  |  {len(regions)} total regions after letter cuts",
        fill=LABEL_TEXT,
        font=lf,
    )
    draw.text(
        (px, legend_y + 32),
        "Phase 1 (this image): standard jigsaw + letter outline as additional cuts. "
        "No tab handling around the letter perimeter yet.",
        fill=LABEL_TEXT,
        font=lf,
    )
    draw.text(
        (px, legend_y + 64),
        "Phase 2 (next): detect sub-pieces with < 2 tabs, add tabs along the letter "
        "perimeter for them, remove background tabs that visually crowd the letter.",
        fill=LABEL_TEXT,
        font=lf,
    )
    draw.line(
        [(px, legend_y + 110), (px + 50, legend_y + 110)],
        fill=LETTER_OUTLINE,
        width=LETTER_OUTLINE_WIDTH,
    )
    draw.text(
        (px + 60, legend_y + 98),
        "= letter outline (additional cuts)",
        fill=LABEL_TEXT,
        font=lf,
    )

    img.save(OUT_PATH, "PNG", optimize=True)
    print(f"-> wrote {OUT_PATH}  ({img_w}x{img_h})  {len(regions)} regions")


if __name__ == "__main__":
    main()
