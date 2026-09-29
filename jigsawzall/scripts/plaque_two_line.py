#!/usr/bin/env python3
"""Two-line rectangular desk plaque: ONE genuinely interlocking jigsaw
puzzle spanning two stacked lines of text (e.g. a title over a surname),
set into a separable outer frame that glues onto a plain backboard of the
identical profile.

Unlike the ring puzzles (scripts/ring_prototype.py) or the single-line
name-banner (geometry.py's wave_grid/vertex_grid/letter_aligned_grid
paths), this reuses the OLDEST, plainest grid engine
(build_pieces_with_shifted_tabs -- a uniform cols x rows cell grid with
tabs shifted to dodge letters) because it is letter-arrangement-agnostic:
it doesn't care how many lines of text are baked into the letter_union it's
given, so feeding it a union that already contains TWO lines of text
produces ONE continuously-tabbed puzzle -- including real tabs crossing
the seam between the two lines -- for free. The wave/vertex/letter-aligned
engines all assume a single horizontal sequence of glyphs and would need
real new engineering to do this; the plain grid needed only a custom
two-line letter_union builder (build_two_line_union below) and some
parameter tuning (piece size, letter tracking, clearance) to get every
tab to place cleanly instead of mostly dropping to straight cuts.

Assembly (per Alex, the commissioner): cut the PUZZLE panel (this script's
main output) and a plain BACKBOARD of the identical outer profile (no
cuts). Pop the puzzle's thin outer FRAME ring off, glue it onto the
backboard (same profile, sits flush all the way around), then set the
loose letter/background pieces into the resulting shallow tray -- the
frame's inner edge holds them in place once assembled. Two slotted foot
pieces (build_foot in this same script) let the glued assembly stand at
an angle like a desk nameplate.

    PYTHONPATH=. python3 scripts/plaque_two_line.py
"""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw  # noqa: E402
from shapely.geometry import MultiPolygon, Polygon, box  # noqa: E402

import geometry as G  # noqa: E402

BUILD_DIR = Path(__file__).resolve().parent.parent / "build" / "plaque_two_line"


# ---------------------------------------------------------------------------
# Two-line letter layout + single-grid puzzle generation
# ---------------------------------------------------------------------------
def _draw_tracked_line(d, word, font, cx, cy, extra_track_px):
    """Draw `word` centered at (cx, cy) with extra_track_px added between
    each letter's natural advance -- default kerning on a bold face packs
    glyphs almost flush, leaving the grid's vertical tabs no background to
    shift into. Real tracking is what got the drop rate from ~80% to 0%."""
    widths = [d.textlength(c, font=font) for c in word]
    total = sum(widths) + extra_track_px * (len(word) - 1)
    bbox = d.textbbox((0, 0), word, font=font)
    th = bbox[3] - bbox[1]
    x = cx - total / 2
    y = cy - th / 2 - bbox[1]
    for c, w in zip(word, widths):
        d.text((x, y), c, fill=255, font=font)
        x += w + extra_track_px
    return total, th


def build_two_line_union(cfg, line1, line2, row_h_frac, gap_h_frac, extra_track_mm):
    """Render both lines onto ONE mask and trace it ONCE, so the resulting
    letter_union is a single opaque shape spanning the whole panel --
    build_pieces_with_shifted_tabs then tiles ONE uniform grid over it."""
    img_w, img_h = cfg.canvas_w_px, cfg.canvas_h_px
    px, py = cfg.margin_px, cfg.margin_px
    pw, ph = cfg.puzzle_w_px, cfg.puzzle_h_px
    ppm = cfg.px_per_mm

    row_h = ph * row_h_frac
    gap_h = ph * gap_h_frac
    margin_h = (ph - 2 * row_h - gap_h) / 2.0
    extra_track_px = extra_track_mm * ppm

    mask = Image.new("L", (img_w, img_h), 0)
    d = ImageDraw.Draw(mask)
    max_text_w = pw * 0.90
    centers_y = [py + margin_h + row_h / 2, py + margin_h + row_h + gap_h + row_h / 2]
    for word, cy in zip((line1, line2), centers_y):
        font_size = int(row_h * 1.35)
        font = G.find_font(font_size, cfg.font_path)
        while font_size > 6:
            font = G.find_font(font_size, cfg.font_path)
            widths = [d.textlength(c, font=font) for c in word]
            total = sum(widths) + extra_track_px * (len(word) - 1)
            if total <= max_text_w:
                break
            font_size -= 2
        _draw_tracked_line(d, word, font, px + pw / 2, cy, extra_track_px)
    return G._trace_mask_polygons(mask)


def generate_two_line_puzzle(
    line1, line2, seed, cfg, row_h_frac=0.24, gap_h_frac=0.16, extra_track_mm=8.0
):
    """Same post-processing pipeline as geometry.generate_pieces()'s plain
    (non letter-aligned/vertex/wave) branch, just fed a two-line union
    instead of a single render_letter_polygons() call."""
    letter_union = build_two_line_union(
        cfg, line1, line2, row_h_frac, gap_h_frac, extra_track_mm
    )
    letter_union = G.soften_letters(letter_union, cfg)
    piece_polys, stats = G.build_pieces_with_shifted_tabs(seed, letter_union, cfg)
    cell_fragments = G.carve_letter_pockets(piece_polys, letter_union)
    cell_fragments = G.merge_small_fragments(cell_fragments, cfg)
    cell_fragments = G.fuse_counter_fragments(cell_fragments, letter_union, cfg)
    cell_fragments = G.round_panel_corners(cell_fragments, cfg)
    letter_polys = []
    if letter_union is not None:
        if isinstance(letter_union, MultiPolygon):
            letter_polys = [g for g in letter_union.geoms if g.area > 100]
        elif isinstance(letter_union, Polygon):
            letter_polys = [letter_union]
    pieces = list(cell_fragments) + [
        {"parent": None, "polygon": lp, "kind": "letter"} for lp in letter_polys
    ]
    pieces = G.absorb_letter_slivers(pieces, letter_union, cfg)
    for i, p in enumerate(pieces, start=1):
        p["serial"] = i
    return pieces, stats


# ---------------------------------------------------------------------------
# Frame + backboard
# ---------------------------------------------------------------------------
def _rounded_rect(x0, y0, x1, y1, r, n=16):
    pts = []
    corners = [
        (x1 - r, y0 + r, -90, 0),
        (x1 - r, y1 - r, 0, 90),
        (x0 + r, y1 - r, 90, 180),
        (x0 + r, y0 + r, 180, 270),
    ]
    for cx, cy, a0, a1 in corners:
        for k in range(n + 1):
            a = math.radians(a0 + (a1 - a0) * k / n)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return Polygon(pts)


def build_frame_and_backboard(content_w_mm, content_h_mm, border_mm, corner_mm):
    """Frame = a separable ring: outer profile (rounded rect) minus the
    content rectangle. Backboard = the SAME outer profile, solid -- Alex
    glues the frame onto it (identical profiles, flush all around), then
    the loose letter/background pieces set into the resulting tray."""
    outer = _rounded_rect(
        -border_mm, -border_mm, content_w_mm + border_mm, content_h_mm + border_mm,
        corner_mm,
    )
    inner = box(0, 0, content_w_mm, content_h_mm)
    frame = outer.difference(inner)
    return frame, outer  # outer == backboard outline


# ---------------------------------------------------------------------------
# Slotted foot (stand)
# ---------------------------------------------------------------------------
def build_foot(
    stock_mm=3.0,
    slot_clearance_mm=1.0,
    lean_deg=52.0,
    slot_depth_mm=24.0,
    base_depth_mm=55.0,
    back_h_mm=42.0,
    front_h_mm=14.0,
    slot_entry_frac=0.62,
):
    """Side-profile of one foot: a wedge standing on the desk (flat
    bottom), with a slot notched into its sloped top edge at `lean_deg`
    from horizontal, sized for TWO layers of `stock_mm` (the glued
    frame+backboard edge) plus clearance. The slot's angle IS the
    plaque's resting angle once its bottom edge is seated in it.
    Unverified -- like every other physical fit in this project, wants a
    scrap test (slot width/depth/angle are all easy to retune here)."""
    slot_w = 2 * stock_mm + slot_clearance_mm
    outline = Polygon(
        [(0, 0), (base_depth_mm, 0), (base_depth_mm, front_h_mm), (0, back_h_mm)]
    )
    top_back = (0, back_h_mm)
    top_front = (base_depth_mm, front_h_mm)
    ex = top_back[0] + (top_front[0] - top_back[0]) * slot_entry_frac
    ey = top_back[1] + (top_front[1] - top_back[1]) * slot_entry_frac
    dx = -math.cos(math.radians(lean_deg))
    dy = -math.sin(math.radians(lean_deg))
    bx, by = ex + dx * slot_depth_mm, ey + dy * slot_depth_mm
    px, py = -dy, dx
    hw = slot_w / 2
    slot = Polygon(
        [
            (ex + px * hw, ey + py * hw),
            (bx + px * hw, by + py * hw),
            (bx - px * hw, by - py * hw),
            (ex - px * hw, ey - py * hw),
        ]
    ).buffer(0.5, join_style=2)
    return outline.difference(slot)


# ---------------------------------------------------------------------------
# Rendering + CLI
# ---------------------------------------------------------------------------
def _render_pieces_mm(pieces, cfg, path, extra_polys=(), px_per_mm=5):
    """extra_polys: [(polygon_in_mm, fill_rgb)] drawn first (e.g. the
    frame), UNDER the piece polygons (which are in image-px, margin_px
    origin -- converted to the same mm frame as extra_polys, content
    top-left = (0,0))."""
    ox_px, oy_px = cfg.margin_px, cfg.margin_px
    ppm = cfg.px_per_mm
    xs = [p[0].bounds[0] for p in extra_polys] + [0]
    ys = [p[0].bounds[1] for p in extra_polys] + [0]
    x1s = [p[0].bounds[2] for p in extra_polys] + [cfg.puzzle_w_px / ppm]
    y1s = [p[0].bounds[3] for p in extra_polys] + [cfg.puzzle_h_px / ppm]
    minx, miny, maxx, maxy = min(xs), min(ys), max(x1s), max(y1s)
    pad = 20
    W = int((maxx - minx) * px_per_mm) + 2 * pad
    H = int((maxy - miny) * px_per_mm) + 2 * pad
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)

    def to_px(x, y):
        return (pad + (x - minx) * px_per_mm, pad + (y - miny) * px_per_mm)

    for poly, fill in extra_polys:
        geoms = poly.geoms if poly.geom_type == "MultiPolygon" else [poly]
        for g in geoms:
            d.polygon([to_px(x, y) for x, y in g.exterior.coords], fill=fill, outline=(0, 0, 0))
            for ring in g.interiors:
                d.polygon([to_px(x, y) for x, y in ring.coords], fill="white", outline=(0, 0, 0))

    random.seed(4)
    palette = [
        (random.randint(120, 230), random.randint(120, 230), random.randint(120, 230))
        for _ in range(len(pieces))
    ]
    for i, p in enumerate(pieces):
        poly = p["polygon"]
        geoms = poly.geoms if poly.geom_type == "MultiPolygon" else [poly]
        for g in geoms:
            pts = [to_px((x - ox_px) / ppm, (y - oy_px) / ppm) for x, y in g.exterior.coords]
            d.polygon(pts, fill=palette[i], outline=(30, 30, 30))
            for ring in g.interiors:
                hpts = [to_px((x - ox_px) / ppm, (y - oy_px) / ppm) for x, y in ring.coords]
                d.polygon(hpts, fill="white", outline=(30, 30, 30))
    img.save(path)


def main():
    line1, line2 = "PRINCIPAL", "CAVAGNOLO"
    cfg = G.PuzzleConfig(
        panel_mm=280, panel_h_mm=110, piece_mm=30, piece_h_mm=55,
        tab_circle_r_px=15, tab_stem_w_px=30.0,
        letter_clearance_mm=2.0, corner_radius_mm=0.0,
        snap_letters_to_grid=False,
    )
    pieces, stats = generate_two_line_puzzle(line1, line2, seed=1, cfg=cfg)
    content_w = cfg.puzzle_w_px / cfg.px_per_mm
    content_h = cfg.puzzle_h_px / cfg.px_per_mm
    border_mm, corner_mm = 10.0, 5.0
    frame, backboard = build_frame_and_backboard(content_w, content_h, border_mm, corner_mm)
    total_w = content_w + 2 * border_mm
    total_h = content_h + 2 * border_mm

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    _render_pieces_mm(
        pieces, cfg, BUILD_DIR / "plaque.png",
        extra_polys=[(frame, (222, 196, 150))],
    )
    foot = build_foot()
    fb = foot.bounds
    foot_img = Image.new("RGB", (int((fb[2] - fb[0]) * 8) + 40, int((fb[3] - fb[1]) * 8) + 40), "white")
    fd = ImageDraw.Draw(foot_img)
    def fpx(x, y):
        return (20 + (x - fb[0]) * 8, foot_img.height - 20 - (y - fb[1]) * 8)
    geoms = foot.geoms if foot.geom_type == "MultiPolygon" else [foot]
    for g in geoms:
        fd.polygon([fpx(x, y) for x, y in g.exterior.coords], fill=(222, 196, 150), outline=(0, 0, 0), width=2)
        for ring in g.interiors:
            fd.polygon([fpx(x, y) for x, y in ring.coords], fill="white", outline=(0, 0, 0), width=2)
    foot_img.save(BUILD_DIR / "foot.png")

    print(f"content {content_w:.1f}x{content_h:.1f}mm, {len(pieces)} pieces, seams={stats}")
    print(f"total (with {border_mm:g}mm border) {total_w:.1f}x{total_h:.1f}mm")
    print(f"-> {BUILD_DIR / 'plaque.png'}")
    print(f"-> {BUILD_DIR / 'foot.png'}")


if __name__ == "__main__":
    main()
