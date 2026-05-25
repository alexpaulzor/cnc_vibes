#!/usr/bin/env python3
"""Red-team experiment: multi-line text + per-letter unique fonts.

Three lines: NORA / ♥ / AYANA. Each glyph in a different font.
Generates a wood-color halftone mockup with the pomsky photo, same
visual style as mockup_photo_puzzle.py. Surfaces corner cases we
haven't hit with the single-word case.

This is exploration code; if any of the discovered behaviors warrant
production support, they'll get extracted into geometry.py /
encoder.py / emitter.py / jigsaw.py.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.ops import unary_union

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))  # lesson root

from geometry import (  # noqa: E402
    PuzzleConfig,
    _trace_mask_polygons,
    build_pieces_with_shifted_tabs,
    carve_letter_pockets,
    full_puzzle_config,
    merge_small_fragments,
)

FIG_DIR = SCRIPT_DIR.parent / "figs"
FIG_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Multi-line, per-letter-font rendering
# ---------------------------------------------------------------------------


def _load(path: str, size: int) -> ImageFont.ImageFont:
    return ImageFont.truetype(path, size)


# Per-letter font assignment for "NORA ♥ AYANA". Chosen to maximize
# visual variety — serif, sans, mono, italic, display, condensed, script.
LINE_FONT_RECIPES = [
    # Line 1: NORA
    [
        ("N", "/System/Library/Fonts/Supplemental/Baskerville.ttc"),  # serif
        ("O", "/System/Library/Fonts/Helvetica.ttc"),  # sans
        ("R", "/System/Library/Fonts/Supplemental/Bodoni 72.ttc"),  # display
        (
            "A",
            "/System/Library/Fonts/Supplemental/AmericanTypewriter.ttc",
        ),  # slab serif
    ],
    # Line 2: ♥
    [
        ("♥", "/System/Library/Fonts/Apple Symbols.ttf"),
    ],
    # Line 3: AYANA
    [
        ("A", "/System/Library/Fonts/Supplemental/Brush Script.ttf"),  # script
        ("Y", "/System/Library/Fonts/Supplemental/Chalkduster.ttf"),  # display
        ("A", "/System/Library/Fonts/Supplemental/Comic Sans MS Bold.ttf"),  # casual
        ("N", "/System/Library/Fonts/Supplemental/BigCaslon.ttf"),  # serif large
        ("A", "/System/Library/Fonts/Supplemental/Arial Black.ttf"),  # heavy sans
    ],
]


def render_multiline_polygons(line_recipes, cfg: PuzzleConfig, font_size: int):
    """Render multi-line text with per-glyph fonts. Returns the shapely
    union of all letter shapes (compatible with build_pieces_with_shifted_tabs
    et al)."""
    img_w, img_h = cfg.canvas_w_px, cfg.canvas_h_px
    px, py = cfg.margin_px, cfg.margin_px
    puzzle_w, puzzle_h = cfg.puzzle_w_px, cfg.puzzle_h_px

    n_lines = len(line_recipes)
    line_pitch = puzzle_h // (n_lines + 1)  # space lines evenly with gaps

    mask = Image.new("L", (img_w, img_h), 0)
    draw = ImageDraw.Draw(mask)

    for line_idx, glyphs in enumerate(line_recipes):
        # Pre-measure each glyph at the chosen font size
        loaded = [(char, _load(path, font_size)) for char, path in glyphs]
        widths, heights, bboxes = [], [], []
        for char, font in loaded:
            bbox = draw.textbbox((0, 0), char, font=font)
            widths.append(bbox[2] - bbox[0])
            heights.append(bbox[3] - bbox[1])
            bboxes.append(bbox)
        kerning = font_size // 8
        total_w = sum(widths) + kerning * (len(loaded) - 1)
        max_h = max(heights) if heights else 1

        # Auto-scale this line down if too wide
        max_line_w = int(puzzle_w * 0.92)
        line_scale = 1.0
        if total_w > max_line_w:
            line_scale = max_line_w / total_w
            # Re-render with scaled fonts. Since PIL fonts can't be live-scaled,
            # reload each at the new size.
            scaled_size = max(8, int(font_size * line_scale))
            loaded = [(char, _load(path, scaled_size)) for char, path in glyphs]
            widths, heights, bboxes = [], [], []
            for char, font in loaded:
                bbox = draw.textbbox((0, 0), char, font=font)
                widths.append(bbox[2] - bbox[0])
                heights.append(bbox[3] - bbox[1])
                bboxes.append(bbox)
            kerning = scaled_size // 8
            total_w = sum(widths) + kerning * (len(loaded) - 1)
            max_h = max(heights) if heights else 1

        # Position the line
        line_y_center = py + line_pitch * (line_idx + 1)
        x_cursor = px + (puzzle_w - total_w) // 2

        for (char, font), w, h, bbox in zip(loaded, widths, heights, bboxes):
            # Vertical alignment: center this glyph's bbox on line_y_center
            y_draw = line_y_center - h // 2 - bbox[1]
            x_draw = x_cursor - bbox[0]
            draw.text((x_draw, y_draw), char, fill=255, font=font)
            x_cursor += w + kerning

    return _trace_mask_polygons(mask)


# ---------------------------------------------------------------------------
# Mockup composition (reusing mockup_photo_puzzle's wood-color rendering)
# ---------------------------------------------------------------------------

WOOD_LIGHT = (228, 204, 168)
WOOD_DARK = (78, 50, 28)
CUT_LINE = (40, 30, 20)
LETTER_HIGHLIGHT = (160, 60, 60)


def halftone_encode(img):
    return img.convert("1").convert("L")


def wood_mockup_from_gray(gray: Image.Image) -> Image.Image:
    rgb = Image.new("RGB", gray.size)
    pi, po = gray.load(), rgb.load()
    for y in range(gray.height):
        for x in range(gray.width):
            v = pi[x, y]
            t = v / 255.0
            po[x, y] = (
                int(WOOD_DARK[0] + (WOOD_LIGHT[0] - WOOD_DARK[0]) * t),
                int(WOOD_DARK[1] + (WOOD_LIGHT[1] - WOOD_DARK[1]) * t),
                int(WOOD_DARK[2] + (WOOD_LIGHT[2] - WOOD_DARK[2]) * t),
            )
    return rgb


def draw_cut_pattern(img, pieces, cfg, px_per_mm):
    d = ImageDraw.Draw(img)
    scale = px_per_mm / cfg.px_per_mm

    def to_mockup(x_phase, y_phase):
        return ((x_phase - cfg.margin_px) * scale, (y_phase - cfg.margin_px) * scale)

    for piece in pieces:
        poly = piece["polygon"]
        polys = list(poly.geoms) if isinstance(poly, MultiPolygon) else [poly]
        for p in polys:
            pts = [to_mockup(x, y) for x, y in p.exterior.coords]
            d.line(pts, fill=CUT_LINE, width=2)
            for interior in p.interiors:
                ipts = [to_mockup(x, y) for x, y in interior.coords]
                d.line(ipts, fill=CUT_LINE, width=2)
    for piece in pieces:
        if piece.get("kind") != "letter":
            continue
        poly = piece["polygon"]
        polys = list(poly.geoms) if isinstance(poly, MultiPolygon) else [poly]
        for p in polys:
            pts = [to_mockup(x, y) for x, y in p.exterior.coords]
            d.line(pts, fill=LETTER_HIGHLIGHT, width=3)


def _load_label_font(size):
    try:
        return ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size)
    except (OSError, IOError):
        return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--image", type=Path, default=Path("/tmp/cnc_mockup/pomsky.jpg"))
    ap.add_argument("--font-size", type=int, default=180)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--px-per-mm", type=float, default=4.0)
    args = ap.parse_args()

    cfg = full_puzzle_config()

    print("rendering multi-line text...")
    letter_union = render_multiline_polygons(LINE_FONT_RECIPES, cfg, args.font_size)
    if letter_union is None:
        sys.exit(
            "error: letter rendering produced empty polygon — fonts may be missing"
        )
    if isinstance(letter_union, MultiPolygon):
        n_letter_shapes = len(letter_union.geoms)
    else:
        n_letter_shapes = 1
    print(f"  letter union: {n_letter_shapes} disjoint shapes")

    print("building cell pieces (this also shifts tabs to clear letters)...")
    piece_polys, stats = build_pieces_with_shifted_tabs(args.seed, letter_union, cfg)
    print(f"  tabs: {stats}")

    fragments = merge_small_fragments(
        carve_letter_pockets(piece_polys, letter_union), cfg
    )
    print(f"  cell fragments after merge: {len(fragments)}")

    letter_polys = (
        [g for g in letter_union.geoms if g.area > 100]
        if isinstance(letter_union, MultiPolygon)
        else [letter_union]
    )
    pieces = list(fragments) + [
        {"parent": None, "polygon": lp, "kind": "letter"} for lp in letter_polys
    ]
    for i, p in enumerate(pieces, start=1):
        p["serial"] = i
    n_cells = sum(1 for p in pieces if p["kind"] == "cell")
    n_letters = len(pieces) - n_cells
    print(f"  total pieces: {len(pieces)} ({n_cells} cells + {n_letters} letters)")

    print("loading + halftoning photo...")
    src = Image.open(args.image).convert("L")
    w, h = src.size
    side = min(w, h)
    src = src.crop(
        (
            (w - side) // 2,
            (h - side) // 2,
            (w - side) // 2 + side,
            (h - side) // 2 + side,
        )
    )
    panel_px = int(cfg.panel_mm * args.px_per_mm)
    src = src.resize((panel_px, panel_px), Image.LANCZOS)
    ht = halftone_encode(src)
    mockup = wood_mockup_from_gray(ht)
    draw_cut_pattern(mockup, pieces, cfg, args.px_per_mm)

    # Compose final image with caption
    label_h = 100
    out = Image.new("RGB", (panel_px, panel_px + label_h), (250, 250, 250))
    out.paste(mockup, (0, 0))
    d = ImageDraw.Draw(out)
    title_font = _load_label_font(22)
    line1 = f"Multi-line red-team: NORA / heart / AYANA, per-letter fonts, halftone + pomsky"
    line2 = (
        f"{len(pieces)} pieces ({n_cells} cells + {n_letters} letters), "
        f"tabs centered/shifted/dropped: {stats['centered']}/{stats['shifted']}/{stats['dropped']}"
    )
    d.text((10, panel_px + 10), line1, fill=(20, 20, 20), font=title_font)
    d.text((10, panel_px + 50), line2, fill=(60, 60, 60), font=title_font)

    out_path = FIG_DIR / "redteam_multiline_pomsky.png"
    out.save(out_path, "PNG", optimize=True)
    print(f"-> {out_path}")


if __name__ == "__main__":
    main()
