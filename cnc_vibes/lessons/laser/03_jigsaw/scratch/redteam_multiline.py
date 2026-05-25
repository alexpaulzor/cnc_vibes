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


# Cross-platform font search roots, tried in order. _find_font_path
# resolves a font name to an actual file by checking each root.
FONT_SEARCH_ROOTS = [
    "/System/Library/Fonts",
    "/System/Library/Fonts/Supplemental",
    "/Library/Fonts",
    "/usr/share/fonts",
    "/usr/share/fonts/truetype",
    "/usr/share/fonts/truetype/dejavu",
    "C:\\Windows\\Fonts",
]


def _find_font_path(filename: str) -> str:
    """Locate a font file by name across platform-typical font directories.
    Returns the first hit; raises FileNotFoundError if no root contains it."""
    import os

    for root in FONT_SEARCH_ROOTS:
        candidate = os.path.join(root, filename)
        if os.path.exists(candidate):
            return candidate
    raise FileNotFoundError(
        f"font {filename!r} not found in any of: {FONT_SEARCH_ROOTS}"
    )


def _cap_height_at_size(font: ImageFont.ImageFont, char: str) -> int:
    """Pixel height of a single capital glyph at the font's current size.
    Used for per-glyph auto-scaling so different fonts all render at a
    consistent visual height."""
    bbox = font.getbbox(char)
    return bbox[3] - bbox[1]


def _load_at_target_cap_height(
    path: str, char: str, target_h: int
) -> ImageFont.ImageFont:
    """Load `path` at whatever font-size makes `char`'s cap height ≈ target_h.
    Uses one measurement at size 200 to derive the scale factor, then a
    refining measurement at the candidate size."""
    probe = _load(path, 200)
    probe_h = _cap_height_at_size(probe, char) or 1
    scale = target_h / probe_h
    size = max(8, int(round(200 * scale)))
    # One refinement pass: actual cap height at the chosen size may
    # differ slightly from the linear projection, so adjust once.
    final = _load(path, size)
    actual = _cap_height_at_size(final, char) or 1
    if abs(actual - target_h) > target_h * 0.05:
        size = max(8, int(round(size * target_h / actual)))
        final = _load(path, size)
    return final


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


def render_multiline_polygons(
    line_recipes,
    cfg: PuzzleConfig,
    target_cap_height_px: int | None = None,
    line_height_px: int | None = None,
):
    """Render multi-line text with per-glyph fonts, each glyph auto-scaled
    so its cap-height matches target_cap_height_px (defaults to cfg.cell_h_px
    — letters end up at least as tall as a standard puzzle piece). Different
    fonts have wildly different size→cap-height relationships; per-glyph
    scaling makes the line look visually uniform regardless of font choice.

    Baseline alignment: each glyph drawn so its font baseline sits on the
    line baseline (not bbox center). This fixes the "letters bouncing" look
    you get from naive bbox-center alignment.

    Returns the shapely union of all letter shapes."""
    img_w, img_h = cfg.canvas_w_px, cfg.canvas_h_px
    px, py = cfg.margin_px, cfg.margin_px
    puzzle_w, puzzle_h = cfg.puzzle_w_px, cfg.puzzle_h_px

    if target_cap_height_px is None:
        target_cap_height_px = cfg.cell_h_px

    n_lines = len(line_recipes)
    if line_height_px is None:
        # Pack lines: cap-height + small gap; positioned within the panel
        gap = max(20, cfg.cell_h_px // 6)
        line_height_px = target_cap_height_px + gap
    # Anchor lines around vertical center of the panel.
    total_text_h = line_height_px * n_lines
    block_top = py + (puzzle_h - total_text_h) // 2

    mask = Image.new("L", (img_w, img_h), 0)
    draw = ImageDraw.Draw(mask)

    for line_idx, glyphs in enumerate(line_recipes):
        # Per-glyph load at the right size to hit target_cap_height_px
        loaded = []
        widths = []
        bboxes = []
        for char, path in glyphs:
            font = _load_at_target_cap_height(path, char, target_cap_height_px)
            bbox = draw.textbbox((0, 0), char, font=font)
            loaded.append((char, font))
            widths.append(bbox[2] - bbox[0])
            bboxes.append(bbox)

        kerning = max(8, target_cap_height_px // 16)
        total_w = sum(widths) + kerning * max(0, len(loaded) - 1)

        # If too wide, scale all line glyphs down uniformly until it fits
        max_line_w = int(puzzle_w * 0.92)
        if total_w > max_line_w:
            shrink = max_line_w / total_w
            new_target = max(20, int(target_cap_height_px * shrink))
            loaded = []
            widths = []
            bboxes = []
            for char, path in glyphs:
                font = _load_at_target_cap_height(path, char, new_target)
                bbox = draw.textbbox((0, 0), char, font=font)
                loaded.append((char, font))
                widths.append(bbox[2] - bbox[0])
                bboxes.append(bbox)
            kerning = max(8, new_target // 16)
            total_w = sum(widths) + kerning * max(0, len(loaded) - 1)
            print(
                f"  line {line_idx}: too wide at target cap-height; "
                f"shrunk to {new_target}px"
            )

        # Line baseline = bottom of the cap region for this line.
        # block_top + per-line offset + cap_height = baseline
        line_baseline_y = (
            block_top
            + line_height_px * (line_idx + 1)
            - max(8, target_cap_height_px // 6)
        )
        x_cursor = px + (puzzle_w - total_w) // 2

        for (char, font), w, bbox in zip(loaded, widths, bboxes):
            # font.getmetrics() -> (ascent, descent). The baseline sits at
            # `ascent` pixels from the TOP of the font's drawing area, so
            # to put baseline at line_baseline_y, draw the text starting
            # at y_top = line_baseline_y - ascent.
            ascent, _descent = font.getmetrics()
            y_top = line_baseline_y - ascent
            x_draw = x_cursor - bbox[0]
            draw.text((x_draw, y_top), char, fill=255, font=font)
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
    ap.add_argument(
        "--cap-height-mm",
        type=float,
        default=None,
        help="target cap-height per letter in mm (default: one cell tall)",
    )
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--px-per-mm", type=float, default=4.0)
    args = ap.parse_args()

    cfg = full_puzzle_config()

    target_cap_height_px = None
    if args.cap_height_mm is not None:
        target_cap_height_px = int(args.cap_height_mm * cfg.px_per_mm)

    print("rendering multi-line text (cap-height auto-targeted per glyph)...")
    letter_union = render_multiline_polygons(
        LINE_FONT_RECIPES, cfg, target_cap_height_px=target_cap_height_px
    )
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
