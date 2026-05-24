#!/usr/bin/env python3
"""Generate a 6x4 jigsaw diagram with curved Bezier tabs and a letter A
overlay. Each enclosed region (bounded by puzzle cuts and/or letter
outline) gets a serial number so the user can do "color by numbers" to
indicate which regions their algorithm should merge.

Curved tabs: random per-edge direction (seeded for reproducibility),
cubic Bezier curve approximation of a classic "ball and stem" tab.

Region numbering: after all cuts are drawn, flood-fill identifies each
connected non-cut region; each gets a serial number labeled at its
centroid. Colors are light pastels for visual separation; the NUMBERS
are what matter.
"""

from __future__ import annotations

import math
import random
import sys
from collections import deque
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path(__file__).resolve().parent.parent / "figs"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_PATH = OUT_DIR / "puzzle_6x4_with_letter_A_numbered.png"

# ---- layout ----
COLS = 6
ROWS = 4
CELL_W = 240
CELL_H = 200
TAB_LEN = 90  # length along the edge
TAB_HEIGHT = 38  # depth (perpendicular bulb radius-ish)
MARGIN = 80
LEGEND_H = 120
SEED = 42
CUT_WIDTH = 3
LETTER_OUTLINE_WIDTH = 5

random.seed(SEED)

# ---- colors ----
BG = (255, 255, 255)
CUT = (40, 40, 40)
LETTER_OUTLINE = (180, 30, 30)
LABEL_BG = (255, 255, 255)
LABEL_TEXT = (10, 10, 10)
PIECE_LABEL_TEXT = (40, 40, 40)


# ---------------------------------------------------------------------------
# Curved tab geometry
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
    """Sample a classic ball-and-stem tab curve.

    Local coords: u along the edge in [0, 1], v perpendicular.
    v > 0 = outward (convex). direction +1 keeps that; -1 mirrors
    (concave notch dipping into the piece).
    """
    pts: list[tuple[float, float]] = [(0.0, 0.0), (0.30, 0.0)]
    d = direction
    # Left half of bulb: cubic Bezier from neck base to top of bulb
    p0, p1, p2, p3 = (0.30, 0.0), (0.16, 0.50 * d), (0.16, 1.10 * d), (0.50, 1.10 * d)
    for i in range(1, n + 1):
        pts.append(bezier_pt(p0, p1, p2, p3, i / n))
    # Right half of bulb: cubic Bezier from top of bulb back to neck base
    p0, p1, p2, p3 = (0.50, 1.10 * d), (0.84, 1.10 * d), (0.84, 0.50 * d), (0.70, 0.0)
    for i in range(1, n + 1):
        pts.append(bezier_pt(p0, p1, p2, p3, i / n))
    pts.append((1.0, 0.0))
    return pts


def place_tab(
    edge_start: tuple[float, float],
    edge_dir: tuple[float, float],
    edge_length: float,
    direction: int,
) -> list[tuple[float, float]]:
    """Transform a tab outline into world coords for a particular edge."""
    # Outward from the piece interior. For a CW perimeter walk in screen
    # coords (y increases downward), the outward direction is the 90° CW
    # rotation of edge_dir: (dx, dy) -> (dy, -dx).
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
# Piece polygon construction (with randomized tab directions)
# ---------------------------------------------------------------------------


# vertical_tabs[(col, row)] = True iff tab bulges to the right (left piece
# owns the convex side, right piece has the concave notch).
vertical_tabs = {
    (c, r): random.random() > 0.5 for c in range(COLS - 1) for r in range(ROWS)
}
# horizontal_tabs[(col, row)] = True iff tab bulges downward (upper piece
# owns the convex side, lower piece has the concave notch).
horizontal_tabs = {
    (c, r): random.random() > 0.5 for c in range(COLS) for r in range(ROWS - 1)
}


def piece_polygon(col: int, row: int, ox: int, oy: int) -> list[tuple[float, float]]:
    x0 = ox + col * CELL_W
    y0 = oy + row * CELL_H
    x1 = x0 + CELL_W
    y1 = y0 + CELL_H

    pts: list[tuple[float, float]] = [(x0, y0)]

    # Top edge L→R. If not top row, tab present.
    if row > 0:
        bulges_down = horizontal_tabs[(col, row - 1)]
        # From this piece's POV (it's the lower side of the edge),
        # convex bulge going outward (= UP from this piece) means the
        # tab bulges UP, i.e. NOT bulges_down.
        direction = -1 if bulges_down else +1
        tab_world = place_tab(
            edge_start=(x0, y0),
            edge_dir=(1, 0),
            edge_length=CELL_W,
            direction=direction,
        )
        pts.extend(tab_world[1:-1])
    pts.append((x1, y0))

    # Right edge T→B
    if col < COLS - 1:
        bulges_right = vertical_tabs[(col, row)]
        # This piece is the LEFT side of the edge. bulges_right == True
        # means the convex tab points right (outward from this piece).
        direction = +1 if bulges_right else -1
        tab_world = place_tab(
            edge_start=(x1, y0),
            edge_dir=(0, 1),
            edge_length=CELL_H,
            direction=direction,
        )
        pts.extend(tab_world[1:-1])
    pts.append((x1, y1))

    # Bottom edge R→L
    if row < ROWS - 1:
        bulges_down = horizontal_tabs[(col, row)]
        # This piece is the UPPER side. bulges_down means convex tab
        # points down (outward from this piece).
        direction = +1 if bulges_down else -1
        tab_world = place_tab(
            edge_start=(x1, y1),
            edge_dir=(-1, 0),
            edge_length=CELL_W,
            direction=direction,
        )
        pts.extend(tab_world[1:-1])
    pts.append((x0, y1))

    # Left edge B→T
    if col > 0:
        bulges_right = vertical_tabs[(col - 1, row)]
        # This piece is the RIGHT side. bulges_right True means the convex
        # tab from the left bulges INTO us (concave from our POV).
        direction = -1 if bulges_right else +1
        tab_world = place_tab(
            edge_start=(x0, y1),
            edge_dir=(0, -1),
            edge_length=CELL_H,
            direction=direction,
        )
        pts.extend(tab_world[1:-1])

    return pts


# ---------------------------------------------------------------------------
# Font helpers
# ---------------------------------------------------------------------------


def find_font(size: int):
    for path in (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Flood-fill region detection
# ---------------------------------------------------------------------------


def find_regions(cut_mask: Image.Image, min_size: int = 200) -> list[dict]:
    """Find each connected region of non-cut pixels.

    Returns list of dicts: {id, pixels (count), centroid (cx, cy)}.
    """
    width, height = cut_mask.size
    cut_bytes = cut_mask.tobytes()  # 'L' mode = 1 byte per pixel

    # `cut_bytes[idx] != 0` means cut line; 0 means open
    # Use bytearray for visited tracking
    visited = bytearray(width * height)

    regions = []
    region_id = 0

    for sidx in range(width * height):
        if cut_bytes[sidx] != 0 or visited[sidx]:
            continue
        # BFS flood-fill from this seed
        region_id += 1
        sx_seed = sidx % width
        sy_seed = sidx // width
        sum_x = 0
        sum_y = 0
        count = 0
        queue = deque([(sx_seed, sy_seed)])
        visited[sidx] = 1
        while queue:
            x, y = queue.popleft()
            sum_x += x
            sum_y += y
            count += 1
            # 4-connected neighbors
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


# ---------------------------------------------------------------------------
# Pastel color generator
# ---------------------------------------------------------------------------


def pastel(i: int, total: int) -> tuple[int, int, int]:
    """Generate a light pastel color spaced by hue."""
    h = (i * 360 / max(total, 1)) % 360
    # HSV with low saturation and high value -> pastel
    s = 0.30
    v = 0.97
    return _hsv_to_rgb(h, s, v)


def _hsv_to_rgb(h: float, s: float, v: float) -> tuple[int, int, int]:
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    puzzle_w = COLS * CELL_W
    puzzle_h = ROWS * CELL_H
    img_w = puzzle_w + 2 * MARGIN + TAB_HEIGHT
    img_h = puzzle_h + 2 * MARGIN + TAB_HEIGHT + LEGEND_H

    px = MARGIN
    py = MARGIN

    # Pre-compute piece polygons
    piece_polys = {}
    for col in range(COLS):
        for row in range(ROWS):
            piece_polys[(col, row)] = piece_polygon(col, row, px, py)

    # Build cut mask: draw all puzzle cut lines + letter outline in WHITE
    # on BLACK 'L' image. Then "cut" = white = 255, "open" = black = 0.
    # Actually for flood-fill it's easier to have "cut" = 255, "open" = 0
    # so we can check `byte != 0` for cut.
    cut_mask = Image.new("L", (img_w, img_h), 0)
    cm_draw = ImageDraw.Draw(cut_mask)

    # Draw the outer puzzle perimeter
    outer = [
        (px, py),
        (px + puzzle_w, py),
        (px + puzzle_w, py + puzzle_h),
        (px, py + puzzle_h),
    ]
    cm_draw.line([*outer, outer[0]], fill=255, width=CUT_WIDTH)

    # Draw every internal cut. Each internal edge is shared by two pieces;
    # we draw it once (the right and bottom edges of each piece, except
    # the rightmost/bottom row which are the outer perimeter).
    for col in range(COLS):
        for row in range(ROWS):
            poly = piece_polys[(col, row)]
            # Draw the polygon outline; this draws all 4 edges. Duplicates
            # are fine — same pixels get painted again.
            cm_draw.line([*poly, poly[0]], fill=255, width=CUT_WIDTH)

    # Render letter A as a black-on-white shape, then extract its outline.
    letter_size = int(puzzle_h * 1.4)
    letter_font = find_font(letter_size)
    letter_fill = Image.new("L", (img_w, img_h), 0)
    lf_draw = ImageDraw.Draw(letter_fill)
    bbox = lf_draw.textbbox((0, 0), "A", font=letter_font)
    lw = bbox[2] - bbox[0]
    lh = bbox[3] - bbox[1]
    letter_x = px + (puzzle_w - lw) // 2 - bbox[0]
    letter_y = py + (puzzle_h - lh) // 2 - bbox[1]
    lf_draw.text((letter_x, letter_y), "A", fill=255, font=letter_font)

    # Letter outline = boundary of the filled letter. Draw the letter
    # with stroke and no fill onto the cut_mask so its boundary becomes
    # additional cut pixels.
    cm_draw.text(
        (letter_x, letter_y),
        "A",
        fill=0,
        font=letter_font,
        stroke_width=LETTER_OUTLINE_WIDTH,
        stroke_fill=255,
    )

    # ---- Find connected regions via flood-fill ----
    print("flood-filling regions...", file=sys.stderr)
    regions = find_regions(cut_mask, min_size=500)
    print(f"found {len(regions)} regions", file=sys.stderr)

    # ---- Render the final image ----
    img = Image.new("RGB", (img_w, img_h), BG)
    draw = ImageDraw.Draw(img)

    # Title
    title_font = find_font(28)
    draw.text(
        (px, 25),
        f"6x4 jigsaw with letter A and curved tabs — {len(regions)} numbered regions",
        fill=LABEL_TEXT,
        font=title_font,
    )

    # For each region, paint a pastel fill. We do this by flood-filling
    # the final image at each region's centroid (which we know is inside
    # the region because we computed it from the region's pixels).
    # NOTE: a centroid CAN fall outside a non-convex region. For our
    # rectangular-ish pieces this is rare; if it happens we fall back to
    # using one of the region's actual pixels.
    for i, region in enumerate(regions):
        color = pastel(i, len(regions))
        cx, cy = region["centroid"]
        # Use a known interior pixel as the seed. Centroid is approximate;
        # find a pixel near the centroid that's actually open.
        seed = _find_open_seed_near(cut_mask, int(cx), int(cy))
        if seed is None:
            continue
        ImageDraw.floodfill(img, seed, color, thresh=10)

    # Re-draw all cut lines on top of the fills so they're crisp
    # (flood-fill stops at the boundary but anti-aliased pixels get
    # painted with the fill color, blurring the line).
    draw.line([*outer, outer[0]], fill=CUT, width=CUT_WIDTH)
    for col in range(COLS):
        for row in range(ROWS):
            poly = piece_polys[(col, row)]
            draw.line([*poly, poly[0]], fill=CUT, width=CUT_WIDTH)

    # Letter outline in red
    letter_layer = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    ll_draw = ImageDraw.Draw(letter_layer)
    ll_draw.text(
        (letter_x, letter_y),
        "A",
        fill=(0, 0, 0, 0),
        font=letter_font,
        stroke_width=LETTER_OUTLINE_WIDTH,
        stroke_fill=LETTER_OUTLINE,
    )
    img.paste(letter_layer, (0, 0), letter_layer)

    # Per-piece labels (A0..F3) — small, near the top-left of each cell
    piece_label_font = find_font(20)
    for col in range(COLS):
        for row in range(ROWS):
            label = f"{chr(ord('A') + col)}{row}"
            tx = px + col * CELL_W + 8
            ty = py + row * CELL_H + 6
            draw.text((tx, ty), label, fill=PIECE_LABEL_TEXT, font=piece_label_font)

    # Region serial number labels at centroids
    region_font = find_font(26)
    for region in regions:
        cx, cy = region["centroid"]
        label = str(region["id"])
        bbox = draw.textbbox((0, 0), label, font=region_font)
        lw = bbox[2] - bbox[0]
        lh = bbox[3] - bbox[1]
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
            (cx - lw // 2 - bbox[0], cy - lh // 2 - bbox[1]),
            label,
            fill=LABEL_TEXT,
            font=region_font,
        )

    # Legend
    legend_y = py + puzzle_h + TAB_HEIGHT + 30
    legend_font = find_font(20)
    draw.text(
        (px, legend_y),
        f"{len(regions)} regions numbered 1..{len(regions)} at their centroids.",
        fill=LABEL_TEXT,
        font=legend_font,
    )
    draw.text(
        (px, legend_y + 28),
        "Piece labels (A0..F3) are at the top-left of each grid cell;",
        fill=LABEL_TEXT,
        font=legend_font,
    )
    draw.text(
        (px, legend_y + 52),
        "they identify which 'natural' cell a region belongs to (may be split by the letter outline).",
        fill=LABEL_TEXT,
        font=legend_font,
    )
    draw.line(
        [(px, legend_y + 90), (px + 50, legend_y + 90)],
        fill=LETTER_OUTLINE,
        width=LETTER_OUTLINE_WIDTH,
    )
    draw.text(
        (px + 60, legend_y + 78),
        "= letter A outline (additional cuts subdividing the pieces it crosses)",
        fill=LABEL_TEXT,
        font=legend_font,
    )

    img.save(OUT_PATH, "PNG", optimize=True)
    print(f"-> wrote {OUT_PATH} ({img_w}x{img_h}), {len(regions)} regions")


def _find_open_seed_near(mask: Image.Image, cx: int, cy: int, radius: int = 30):
    """Find a non-cut pixel near (cx, cy). Returns (x, y) or None."""
    w, h = mask.size
    bytes_ = mask.tobytes()
    # Spiral out from (cx, cy)
    for r in range(radius):
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if max(abs(dx), abs(dy)) != r and r > 0:
                    continue
                nx, ny = cx + dx, cy + dy
                if 0 <= nx < w and 0 <= ny < h:
                    if bytes_[ny * w + nx] == 0:
                        return (nx, ny)
    return None


if __name__ == "__main__":
    main()
