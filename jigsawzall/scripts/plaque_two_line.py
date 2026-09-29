#!/usr/bin/env python3
"""Two-line rectangular desk plaque: ONE genuinely interlocking jigsaw
puzzle spanning two stacked lines of text (e.g. a title over a surname),
set into a separable outer frame that glues onto a plain backboard of the
identical profile.

History (see RING_SPEC.md for the full narrative): a first attempt used
the plain uniform-grid engine (build_pieces_with_shifted_tabs) so both
lines shared one letter_union and one grid -- genuinely one puzzle, tabs
crossing the row boundary, no dropped seams. Rejected: "boring square
grid... edges should connect to letters not surround letters" -- a plain
grid's cells fully enclose each letter with background on every side; the
seam (and its tab) never actually touches the letter. What was wanted is
the OTHER family of engine already used elsewhere in this project for
single-line names (geometry.py's vertex_grid/wave_grid): curved seams
launched FROM a letter's own silhouette edge to the next attach point
(another letter, or the border) -- but neither engine has any concept of
TWO rows of text; both sort every glyph into one left-to-right sequence.

This module is a from-scratch two-row adaptation of that curved-seam
machinery, reusing geometry.py's lower-level primitives directly
(_letter_edge_point, _vg_curve, _vg_assemble, _vg_bow/_vg_deflection)
rather than either grid engine wholesale:

  * n vertical dividers, one per (row0 letter, row1 letter) pair (both
    words happen to be 9 letters -- a 1:1 index pairing): a curved seam
    from the panel's top border to row0 letter i's own top edge, THROUGH
    that letter, a curved seam from its bottom edge down to row1 letter
    i's top edge (the cross-row connector -- this is the part neither
    stock engine has), through that letter, and a curved seam from its
    bottom edge down to the panel's bottom border.
  * within each row, ordinary letter-to-letter gap seams (same technique
    single-line vertex_grid uses) plus end seams to the L/R border.

Getting this to actually place tabs (not drop every seam) needed two
real fixes over the first draft: the border-node OUTWARD normals were
backwards (should point INTO the panel, matching _vg_curve's arrival-
direction assumption -- with them backwards almost every border-attached
seam failed its curve construction); and margins/row-gap need to be the
same ballpark geometry.py's own production single-line recipe uses
(~24mm top/bottom margin, ~30mm between the two letter rows) for
_vg_tab_candidates' border-clearance and minimum-tab-run-length checks to
have room to succeed -- tighter margins looked more compact on paper but
mostly dropped to straight cuts. Also: PuzzleConfig.puzzle_h_px silently
FLOORS panel_h_mm to a whole number of piece_h_mm-tall grid rows (a
149mm panel_h_mm at the 50mm default piece_h_mm becomes 100mm!) even
though this module doesn't use the grid at all -- bypassed with the
panel_w_px_fit/panel_h_px_fit override fields.

    PYTHONPATH=. python3 scripts/plaque_two_line.py
"""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw  # noqa: E402
from shapely.geometry import LineString, MultiPolygon, Polygon, box  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

import geometry as G  # noqa: E402

BUILD_DIR = Path(__file__).resolve().parent.parent / "build" / "plaque_two_line"


def _frange(lo, hi, d):
    out, t = [], lo
    while t <= hi:
        out.append(t)
        t += d
    return out


# ---------------------------------------------------------------------------
# Two-row letter layout
# ---------------------------------------------------------------------------
def build_two_row_letters(
    cfg, line1, line2, row_h_mm, row_gap_mm, track_mm,
    margin_top_mm=24.0, margin_bottom_mm=24.0,
):
    """Two rows of per-glyph solids (each row sorted left-to-right), each
    row's own font size shrunk to fit the panel width with explicit
    per-letter tracking (default kerning on a bold face packs glyphs too
    tight for a tab to shift into -- the same fix the flat-grid draft
    needed). margin_top_mm/margin_bottom_mm and row_gap_mm are real mm,
    not fractions of the panel -- see the module docstring on why they
    can't be pared down much from geometry.py's own single-line values."""
    img_w, img_h = cfg.canvas_w_px, cfg.canvas_h_px
    px, py = cfg.margin_px, cfg.margin_px
    pw, ph = cfg.puzzle_w_px, cfg.puzzle_h_px
    ppm = cfg.px_per_mm
    row_h = row_h_mm * ppm
    row_gap = row_gap_mm * ppm
    top = py + margin_top_mm * ppm
    centers_y = [top + row_h / 2, top + row_h + row_gap + row_h / 2]

    tmp = Image.new("L", (img_w, img_h), 0)
    td = ImageDraw.Draw(tmp)
    max_text_w = pw * 0.92
    extra_track_px = track_mm * ppm
    rows_solids = []
    for word, cy in zip((line1, line2), centers_y):
        font_size = int(row_h * 1.35)
        font = G.find_font(font_size, cfg.font_path)
        while font_size > 6:
            font = G.find_font(font_size, cfg.font_path)
            widths = [td.textlength(c, font=font) for c in word]
            total = sum(widths) + extra_track_px * (len(word) - 1)
            if total <= max_text_w:
                break
            font_size -= 2
        widths = [td.textlength(c, font=font) for c in word]
        total = sum(widths) + extra_track_px * (len(word) - 1)
        bbox = td.textbbox((0, 0), word, font=font)
        th = bbox[3] - bbox[1]
        x = px + pw / 2 - total / 2
        y = cy - th / 2 - bbox[1]
        solids = []
        for c, w in zip(word, widths):
            mask = Image.new("L", (img_w, img_h), 0)
            ImageDraw.Draw(mask).text((x, y), c, fill=255, font=font)
            poly = G._trace_mask_polygons(mask)
            geoms = poly.geoms if poly.geom_type == "MultiPolygon" else [poly]
            sol = max(geoms, key=lambda g: g.area)
            solids.append(Polygon(sol.exterior))
            x += w + extra_track_px
        rows_solids.append(solids)
    return rows_solids[0], rows_solids[1]


# ---------------------------------------------------------------------------
# Two-row curved-seam puzzle (the reused vertex-grid primitives)
# ---------------------------------------------------------------------------
def build_two_row_pieces(
    cfg, line1, line2, row_h_mm=34.0, row_gap_mm=30.0, track_mm=6.0,
    margin_top_mm=24.0, margin_bottom_mm=24.0,
):
    """Returns (pieces {(idx,0): Polygon}, stats, letter_union). `pieces`
    already includes the individual letter pieces (not just the
    background cells), matching geometry.generate_pieces()'s contract."""
    ppm = cfg.px_per_mm
    px, py = cfg.margin_px, cfg.margin_px
    pw, ph = cfg.puzzle_w_px, cfg.puzzle_h_px
    panel = box(px, py, px + pw, py + ph)

    solids0, solids1 = build_two_row_letters(
        cfg, line1, line2, row_h_mm, row_gap_mm, track_mm, margin_top_mm, margin_bottom_mm
    )
    n0, n1 = len(solids0), len(solids1)
    solids = solids0 + solids1
    letter_union = unary_union([Polygon(s.exterior) for s in solids])
    letter_union = G.soften_letters(letter_union, cfg)
    letters_solid = unary_union(solids)
    background = panel.difference(letter_union)

    cr = cfg.corner_radius_mm * ppm
    step = 10 * ppm
    # Border-node normals point INTO the panel (matches _vg_curve's
    # arrival-direction assumption) -- getting this backwards was the
    # single biggest reason the first draft dropped almost every seam.
    bv_top = [((x, py), (0.0, 1.0)) for x in _frange(px + cr, px + pw - cr, step)]
    bv_bot = [((x, py + ph), (0.0, -1.0)) for x in _frange(px + cr, px + pw - cr, step)]
    bv_left = [((px, y), (1.0, 0.0)) for y in _frange(py + cr, py + ph - cr, step)]
    bv_right = [((px + pw, y), (-1.0, 0.0)) for y in _frange(py + cr, py + ph - cr, step)]

    def obstacles(attach):
        return [(sol, attach.get(k)) for k, sol in enumerate(solids)]

    def curved(pts):
        if pts is None:
            return None
        if G._vg_deflection(pts) < 2.5 * ppm:
            return G._vg_bow(pts, letters_solid, cfg)
        return pts

    def border_seam(a, na, bvs, attach):
        allow = []
        for b, nb in bvs:
            dxb, dyb = b[0] - a[0], b[1] - a[1]
            dn = math.hypot(dxb, dyb) or 1.0
            if (na[0] * dxb + na[1] * dyb) / dn <= 0.2:
                continue
            pts = G._vg_curve(a, na, b, nb, obstacles(attach), ppm)
            if pts is None or LineString(pts).length < 1.5 * cfg.tab_len_px:
                continue
            allow.append((b, pts))
        if not allow:
            return None
        allow.sort(key=lambda e: math.hypot(e[0][0] - a[0], e[0][1] - a[1]))
        return allow[0][1]

    seams = []
    for i, sol in enumerate(solids0):
        ep = G._letter_edge_point(sol, "top", sol.centroid.x, ppm)
        if ep is None:
            continue
        a, na = ep
        cand = [(b, nb) for b, nb in bv_top if abs(b[0] - a[0]) < 60 * ppm]
        pts = border_seam(a, na, cand, {i: "a"}) or [(a[0], a[1]), (a[0], py - 20)]
        seams.append(curved(pts))
    for j, sol in enumerate(solids1):
        idx = n0 + j
        ep = G._letter_edge_point(sol, "bottom", sol.centroid.x, ppm)
        if ep is None:
            continue
        a, na = ep
        cand = [(b, nb) for b, nb in bv_bot if abs(b[0] - a[0]) < 60 * ppm]
        pts = border_seam(a, na, cand, {idx: "a"}) or [(a[0], a[1]), (a[0], py + ph + 20)]
        seams.append(curved(pts))
    # Cross-row connectors: row0[i] bottom -> row1[i] top. This IS the
    # part with no stock equivalent -- both real engines only ever
    # connect a letter to the border or to its SAME-ROW neighbour.
    n = min(n0, n1)
    for i in range(n):
        a_sol, b_sol = solids0[i], solids1[i]
        ea = G._letter_edge_point(a_sol, "bottom", a_sol.centroid.x, ppm)
        eb = G._letter_edge_point(b_sol, "top", b_sol.centroid.x, ppm)
        if ea is None or eb is None:
            continue
        (a, na), (b, nb) = ea, eb
        pts = G._vg_curve(a, na, b, nb, obstacles({i: "a", n0 + i: "b"}), ppm)
        pts = pts or [(a[0], a[1]), (b[0], b[1])]
        seams.append(curved(pts))
    for solids_row, base_idx in ((solids0, 0), (solids1, n0)):
        m = len(solids_row)
        ep = G._letter_edge_point(solids_row[0], "left", solids_row[0].centroid.y, ppm)
        if ep is not None:
            a, na = ep
            cand = [(b, nb) for b, nb in bv_left if abs(b[1] - a[1]) < 60 * ppm]
            pts = border_seam(a, na, cand, {base_idx: "a"}) or [(a[0], a[1]), (px - 20, a[1])]
            seams.append(curved(pts))
        for i in range(m - 1):
            gi, gj = base_idx + i, base_idx + i + 1
            si, sj = solids_row[i], solids_row[i + 1]
            lo = max(si.bounds[1], sj.bounds[1]) + 3 * ppm
            hi = min(si.bounds[3], sj.bounds[3]) - 3 * ppm
            hmid = (lo + hi) / 2 if hi > lo else (si.centroid.y + sj.centroid.y) / 2
            ea = G._letter_edge_point(si, "right", hmid, ppm)
            eb = G._letter_edge_point(sj, "left", hmid, ppm)
            if ea is None or eb is None:
                continue
            (a, na), (b, nb) = ea, eb
            pts = G._vg_curve(a, na, b, nb, obstacles({gi: "a", gj: "b"}), ppm) or [
                (a[0], a[1]), (b[0], b[1])
            ]
            seams.append(curved(pts))
        ep = G._letter_edge_point(solids_row[-1], "right", solids_row[-1].centroid.y, ppm)
        if ep is not None:
            a, na = ep
            cand = [(b, nb) for b, nb in bv_right if abs(b[1] - a[1]) < 60 * ppm]
            pts = border_seam(a, na, cand, {base_idx + m - 1: "a"}) or [
                (a[0], a[1]), (px + pw + 20, a[1])
            ]
            seams.append(curved(pts))

    seams = [s for s in seams if s is not None]
    surround, counters, stats = G._vg_assemble(seams, letter_union, letters_solid, background, panel, cfg)
    letter_pieces = []
    if letter_union is not None:
        geoms = letter_union.geoms if isinstance(letter_union, MultiPolygon) else [letter_union]
        letter_pieces = [g for g in geoms if g.area > 100]
    pieces = {}
    n_cells = len(surround) + len(counters)
    for idx, poly in enumerate(surround + counters + letter_pieces):
        pieces[(idx, 0)] = poly
    stats["n_letters"] = len(letter_pieces)
    stats["n_cells"] = n_cells
    return pieces, stats, letter_union


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
    content rectangle. Backboard = the SAME outer profile, solid -- glue
    the frame onto it (identical profiles, flush all around), then set
    the loose letter/background pieces into the resulting tray."""
    outer = _rounded_rect(
        -border_mm, -border_mm, content_w_mm + border_mm, content_h_mm + border_mm, corner_mm
    )
    inner = box(0, 0, content_w_mm, content_h_mm)
    return outer.difference(inner), outer


# ---------------------------------------------------------------------------
# Slotted foot (stand)
# ---------------------------------------------------------------------------
def build_foot(
    stock_mm=3.0, slot_clearance_mm=1.0, lean_deg=52.0, slot_depth_mm=24.0,
    base_depth_mm=55.0, back_h_mm=42.0, front_h_mm=14.0, slot_entry_frac=0.62,
):
    """Side-profile of one foot: a wedge standing on the desk (flat
    bottom), with a slot notched into its sloped top edge at `lean_deg`
    from horizontal, sized for TWO layers of `stock_mm` (the glued
    frame+backboard edge) plus clearance. Unverified -- like every other
    physical fit in this project, wants a scrap test."""
    slot_w = 2 * stock_mm + slot_clearance_mm
    outline = Polygon([(0, 0), (base_depth_mm, 0), (base_depth_mm, front_h_mm), (0, back_h_mm)])
    top_back, top_front = (0, back_h_mm), (base_depth_mm, front_h_mm)
    ex = top_back[0] + (top_front[0] - top_back[0]) * slot_entry_frac
    ey = top_back[1] + (top_front[1] - top_back[1]) * slot_entry_frac
    dx, dy = -math.cos(math.radians(lean_deg)), -math.sin(math.radians(lean_deg))
    bx, by = ex + dx * slot_depth_mm, ey + dy * slot_depth_mm
    px, py = -dy, dx
    hw = slot_w / 2
    slot = Polygon(
        [(ex + px * hw, ey + py * hw), (bx + px * hw, by + py * hw),
         (bx - px * hw, by - py * hw), (ex - px * hw, ey - py * hw)]
    ).buffer(0.5, join_style=2)
    return outline.difference(slot)


# ---------------------------------------------------------------------------
# Rendering + CLI
# ---------------------------------------------------------------------------
def _render_pieces_mm(pieces, cfg, path, extra_polys=(), px_per_mm=5):
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
    polys = list(pieces.values()) if isinstance(pieces, dict) else pieces
    palette = [
        (random.randint(120, 230), random.randint(120, 230), random.randint(120, 230))
        for _ in range(len(polys))
    ]
    for i, poly in enumerate(polys):
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
    row_h_mm, row_gap_mm, margin_top, margin_bottom = 34.0, 30.0, 24.0, 24.0
    panel_w_mm = 280.0
    panel_h_mm = margin_top + row_h_mm + row_gap_mm + row_h_mm + margin_bottom
    ppm = 5
    cfg = G.PuzzleConfig(
        panel_mm=panel_w_mm, panel_h_mm=panel_h_mm,
        tab_circle_r_px=15, tab_stem_w_px=30.0,
        letter_clearance_mm=4.0, corner_radius_mm=0.0,
        panel_w_px_fit=int(panel_w_mm * ppm), panel_h_px_fit=int(panel_h_mm * ppm),
    )
    pieces, stats = build_two_row_pieces(
        cfg, line1, line2, row_h_mm=row_h_mm, row_gap_mm=row_gap_mm, track_mm=6.0,
        margin_top_mm=margin_top, margin_bottom_mm=margin_bottom,
    )[:2]
    content_w, content_h = panel_w_mm, panel_h_mm
    border_mm, corner_mm = 10.0, 5.0
    frame, backboard = build_frame_and_backboard(content_w, content_h, border_mm, corner_mm)
    total_w, total_h = content_w + 2 * border_mm, content_h + 2 * border_mm

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    _render_pieces_mm(pieces, cfg, BUILD_DIR / "plaque.png", extra_polys=[(frame, (222, 196, 150))])
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

    print(f"content {content_w:.1f}x{content_h:.1f}mm, {len(pieces)} pieces "
          f"({stats['n_letters']} letters, {stats['n_cells']} background), seams={stats}")
    print(f"total (with {border_mm:g}mm border) {total_w:.1f}x{total_h:.1f}mm")
    print(f"-> {BUILD_DIR / 'plaque.png'}")
    print(f"-> {BUILD_DIR / 'foot.png'}")


if __name__ == "__main__":
    main()
