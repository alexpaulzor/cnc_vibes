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

This module is a from-scratch two-row adaptation of the SAME curved-seam
machinery geometry.py's build_pieces_vertex_grid uses for single-line
names (KARSON, RYAN): candidate seam-anchor vertices per letter
(_vg_anchors), true local outward normals (_vg_normal), line-of-sight +
dual-facing-angle-filtered pairing, seeded density/variant iterate-
until-valid search (_vg_score), and final splice/polygonize
(_vg_assemble) -- NOT the simpler _letter_edge_point single-attach-point
method an earlier draft used, which does not pick from multiple
candidate vertices per letter and was why some letters ended up with no
seam touching them at all.

  * within each row: ordinary letter-to-letter GAP seams (identical
    technique to single-line vertex_grid) plus END seams to the L/R
    border for each row's own outermost letter.
  * CAP seams: row0's TOP only, row1's BOTTOM only (their other side
    connects cross-row instead of to a border).
  * CROSS-row seams: row0[i]'s downward-facing vertices to row1[i]'s
    upward-facing vertices -- the one seam category with no single-line
    equivalent, filtered with the identical line-of-sight + dual-facing-
    angle test as an ordinary gap seam.

Two real fixes were needed to get any tabs placed at all: the border-node
OUTWARD normals were backwards (should point INTO the panel, matching
_vg_curve's arrival-direction assumption); and PuzzleConfig.puzzle_h_px
silently FLOORS panel_h_mm to a whole number of piece_h_mm-tall grid rows
even though this module doesn't use the grid -- bypassed with the
panel_w_px_fit/panel_h_px_fit override fields. A third, found later:
build_two_row_letters() and the letter_union construction were both
collapsing each glyph to Polygon(exterior), silently discarding letter
counters (O's hole, P's bowl, A's triangle) -- fixed by keeping the
traced glyph (with interior rings) as the letter_union source and only
stripping to a filled Polygon(exterior) for the separate
letters_solid/obstacle representation, exactly mirroring how
build_pieces_vertex_grid itself keeps those two representations distinct.

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
    """Two rows of per-glyph GLYPHS (each row sorted left-to-right), each
    row's own font size shrunk to fit the panel width with explicit
    per-letter tracking (default kerning on a bold face packs glyphs too
    tight for a tab to shift into -- the same fix the flat-grid draft
    needed). margin_top_mm/margin_bottom_mm and row_gap_mm are real mm,
    not fractions of the panel -- see the module docstring on why they
    can't be pared down much from geometry.py's own single-line values.

    Each returned polygon keeps its interior rings (O's hole, P's bowl,
    A's triangle etc.) intact -- these are the letter "counters" that
    must survive as their own separate piece; do not collapse to
    Polygon(exterior) here the way build_two_row_pieces()'s obstacle/
    letters_solid representation deliberately does further down."""
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
            solids.append(sol)  # keep interiors (counters) intact
            x += w + extra_track_px
        rows_solids.append(solids)
    return rows_solids[0], rows_solids[1]


# ---------------------------------------------------------------------------
# Two-row curved-seam puzzle -- vertex_grid primitives (candidate-vertex +
# true-normal + line-of-sight + dual-facing-angle filtering + seeded
# density/variant search), the same machinery build_pieces_vertex_grid
# uses for single-line names (KARSON, RYAN) -- NOT the simpler
# _letter_edge_point single-attach-point approach an earlier draft used.
# Adds one seam category with no single-line equivalent: cross-row gap
# seams between row0[i]'s downward-facing vertices and row1[i]'s
# upward-facing vertices, filtered identically to an ordinary gap seam.
# ---------------------------------------------------------------------------
def _gap_allowed(gi, gj, verts, norms, solids, obstacles, cfg, ppm):
    allowed = []
    for ia, a in enumerate(verts[gi]):
        na = norms[gi][ia]
        for ib, b in enumerate(verts[gj]):
            nb = norms[gj][ib]
            chord = LineString([a, b])
            if (chord.intersection(solids[gi]).length > 1 or
                    chord.intersection(solids[gj]).length > 1):
                continue
            dxb, dyb = b[0] - a[0], b[1] - a[1]
            dn = math.hypot(dxb, dyb) or 1.0
            if (na[0] * dxb + na[1] * dyb) / dn <= 0.2:
                continue
            if (nb[0] * -dxb + nb[1] * -dyb) / dn <= 0.2:
                continue
            pts = G._vg_curve(a, na, b, nb, obstacles({gi: "a", gj: "b"}), ppm)
            if pts is None or LineString(pts).length < 1.5 * cfg.tab_len_px:
                continue
            allowed.append(((a[1] + b[1]) / 2, pts, ia, ib))
    return allowed


def build_two_row_pieces(
    cfg, line1, line2, row_h_mm=26.0, row_gap_mm=30.0, track_mm=None,
    margin_top_mm=24.0, margin_bottom_mm=24.0, seed=1, densities=(1, 2, 3, 4), variants=8,
):
    """Returns (pieces {(idx,0): Polygon}, stats, letter_union). `pieces`
    already includes the individual letter pieces (not just the
    background cells), matching geometry.generate_pieces()'s contract.

    track_mm=None auto-searches (10.0, 20.0)mm inter-letter tracking, the
    same two values build_pieces_vertex_grid's own vg_spacing_search tries
    for single-line names: some adjacent-letter pairs (e.g. a narrow "I"
    between wider letters) don't leave enough room at 10mm for a gap seam
    to reach the required minimum tab-carrying length, so the search
    widens to 20mm only where needed rather than widening every pair
    up front. Pass an explicit mm value to pin the tracking instead."""
    gaps = (10.0, 20.0) if track_mm is None else (track_mm,)
    ppm = cfg.px_per_mm
    px, py = cfg.margin_px, cfg.margin_px
    pw, ph = cfg.puzzle_w_px, cfg.puzzle_h_px
    panel = box(px, py, px + pw, py + ph)

    def _try(track_mm_val):
        glyphs0, glyphs1 = build_two_row_letters(
            cfg, line1, line2, row_h_mm, row_gap_mm, track_mm_val, margin_top_mm, margin_bottom_mm
        )
        n0, n1 = len(glyphs0), len(glyphs1)
        glyphs = glyphs0 + glyphs1
        # letter_union keeps interiors (counters) -- it defines the actual
        # piece boundary. `solids`/letters_solid are the FILLED (no-hole)
        # bodies used only for obstacle avoidance and counter classification,
        # matching build_pieces_vertex_grid's own convention exactly.
        letter_union = unary_union(glyphs)
        letter_union = G.soften_letters(letter_union, cfg)
        solids = [Polygon(g.exterior) for g in glyphs]
        letters_solid = unary_union(solids)
        background = panel.difference(letter_union)

        verts = [G._vg_anchors(g, ppm) for g in glyphs]
        norms = [[G._vg_normal(g, v, ppm) for v in vs] for g, vs in zip(glyphs, verts)]

        def obstacles(attach):
            return [(sol, attach.get(k)) for k, sol in enumerate(solids)]

        cr = cfg.corner_radius_mm * ppm
        step = 10 * ppm
        # Border-node normals point INTO the panel (matches _vg_curve's
        # arrival-direction assumption) -- getting this backwards was the
        # single biggest reason the first draft dropped almost every seam.
        bv_top = [((x, py), (0.0, 1.0)) for x in _frange(px + cr, px + pw - cr, step)]
        bv_bot = [((x, py + ph), (0.0, -1.0)) for x in _frange(px + cr, px + pw - cr, step)]
        bv_left = [((px, y), (1.0, 0.0)) for y in _frange(py + cr, py + ph - cr, step)]
        bv_right = [((px + pw, y), (-1.0, 0.0)) for y in _frange(py + cr, py + ph - cr, step)]

        row0_gap_allowed = [
            _gap_allowed(i, i + 1, verts, norms, solids, obstacles, cfg, ppm)
            for i in range(n0 - 1)
        ]
        row1_gap_allowed = [
            _gap_allowed(n0 + j, n0 + j + 1, verts, norms, solids, obstacles, cfg, ppm)
            for j in range(n1 - 1)
        ]
        # Cross-row gap-allowed pairs: row0[i] <-> row1[i], filtered to
        # vertices actually facing the other row (down for row0, up for
        # row1) so a seam never launches sideways across the gap.
        m = min(n0, n1)
        cross_allowed = []
        for i in range(m):
            gi, gj = i, n0 + i
            allowed = []
            for ia, a in enumerate(verts[gi]):
                na = norms[gi][ia]
                if na[1] < 0.4:  # must face DOWN (toward row1)
                    continue
                for ib, b in enumerate(verts[gj]):
                    nb = norms[gj][ib]
                    if nb[1] > -0.4:  # must face UP (toward row0)
                        continue
                    chord = LineString([a, b])
                    if (chord.intersection(solids[gi]).length > 1 or
                            chord.intersection(solids[gj]).length > 1):
                        continue
                    dxb, dyb = b[0] - a[0], b[1] - a[1]
                    dn = math.hypot(dxb, dyb) or 1.0
                    if (na[0] * dxb + na[1] * dyb) / dn <= 0.2:
                        continue
                    if (nb[0] * -dxb + nb[1] * -dyb) / dn <= 0.2:
                        continue
                    pts = G._vg_curve(a, na, b, nb, obstacles({gi: "a", gj: "b"}), ppm)
                    if pts is None or LineString(pts).length < 1.5 * cfg.tab_len_px:
                        continue
                    allowed.append(((a[0] + b[0]) / 2, pts, ia, ib))
            cross_allowed.append(allowed)

        def _border_seam(gi, a, na, bvs, jitter_span, rng):
            allow = []
            for b, nb in bvs:
                dxb, dyb = b[0] - a[0], b[1] - a[1]
                dn = math.hypot(dxb, dyb) or 1.0
                if (na[0] * dxb + na[1] * dyb) / dn <= 0.2:
                    continue
                pts = G._vg_curve(a, na, b, nb, obstacles({gi: "a"}), ppm)
                if pts is None or LineString(pts).length < 1.5 * cfg.tab_len_px:
                    continue
                allow.append((b, pts))
            if not allow:
                return None
            tgt = a[0] if jitter_span == "x" else a[1]
            key = 0 if jitter_span == "x" else 1
            tgt += rng.uniform(-15, 15) * ppm
            allow.sort(key=lambda e: abs(e[0][key] - tgt))
            return allow[0][1]

        def _pick_from_allowed(allowed_list, gi_of, gj_of, density, span, rng, used, out):
            for pair_idx, allowed in enumerate(allowed_list):
                gi, gj = gi_of(pair_idx), gj_of(pair_idx)
                if not allowed:
                    continue
                targets = [span * (mm + 0.5) / density for mm in range(density)]
                jit = rng.uniform(-0.08, 0.08) * span
                chosen = []
                min_sep = 1.6 * cfg.tab_len_px
                for tgt in targets:
                    t = tgt + jit
                    for e in sorted(allowed, key=lambda e: abs(e[0] - t)):
                        if (gi, e[2]) in used or (gj, e[3]) in used:
                            continue
                        if any(abs(e[0] - h) < min_sep for h in chosen):
                            continue
                        used.add((gi, e[2]))
                        used.add((gj, e[3]))
                        chosen.append(e[0])
                        out.append(e[1])
                        break
                if not chosen:
                    e = min(allowed, key=lambda e: abs(e[0] - span / 2))
                    used.add((gi, e[2]))
                    used.add((gj, e[3]))
                    out.append(e[1])

        def gen_seams(density, variant):
            rng = random.Random(seed * 131 + density * 17 + variant * 9973)
            seams = []
            used = set()

            _pick_from_allowed(row0_gap_allowed, lambda k: k, lambda k: k + 1, density, ph, rng, used, seams)
            _pick_from_allowed(row1_gap_allowed, lambda k: n0 + k, lambda k: n0 + k + 1, density, ph, rng, used, seams)
            _pick_from_allowed(cross_allowed, lambda k: k, lambda k: n0 + k, density, pw, rng, used, seams)

            # CAP seams: row0 TOP only, row1 BOTTOM only (their other side
            # now connects cross-row instead of to a border).
            primary_caps, extra_caps = [], []
            cap_specs = [(i, True, bv_top) for i in range(n0)] + [
                (n0 + j, False, bv_bot) for j in range(n1)
            ]
            for gi, is_top, bvs_all in cap_specs:
                anchors, nrm = verts[gi], norms[gi]
                g = solids[gi]
                minx, _mn, maxx, _mx = g.bounds
                cx = (minx + maxx) / 2
                grp = [iv for iv in range(len(anchors)) if (nrm[iv][1] < -0.4 if is_top else nrm[iv][1] > 0.4)]
                if not grp:
                    continue
                ncap = max(1, min(density, len(grp)))
                span = max(1.0, maxx - minx)
                xtargets = sorted(
                    [minx + span * (mm + 0.5) / ncap for mm in range(ncap)], key=lambda x: abs(x - cx)
                )
                first = True
                for xt in xtargets:
                    avail = [iv for iv in grp if (gi, iv) not in used]
                    if not avail:
                        break
                    iv = min(avail, key=lambda k: abs(anchors[k][0] - xt))
                    used.add((gi, iv))
                    a, na = anchors[iv], nrm[iv]
                    bvs = [(b, nb) for b, nb in bvs_all if abs(b[0] - a[0]) < 45 * ppm]
                    pts = _border_seam(gi, a, na, bvs, "x", rng)
                    if pts is None:
                        pts = [(a[0], a[1]), (a[0], py - 20 if is_top else py + ph + 20)]
                    (primary_caps if first else extra_caps).append(pts)
                    first = False

            # END seams: L/R border for EACH row's own leftmost/rightmost letter.
            end_seams = []
            for gi_end, side_bvs, want_left in (
                (0, bv_left, True), (n0 - 1, bv_right, False),
                (n0, bv_left, True), (n0 + n1 - 1, bv_right, False),
            ):
                vs, ns = verts[gi_end], norms[gi_end]
                face = [k for k in range(len(vs)) if (ns[k][0] < -0.2 if want_left else ns[k][0] > 0.2)]
                if not face:
                    face = [min(range(len(vs)), key=lambda k: vs[k][0]) if want_left
                            else max(range(len(vs)), key=lambda k: vs[k][0])]
                miny, maxy = min(vs[k][1] for k in face), max(vs[k][1] for k in face)
                spanY = max(1.0, maxy - miny)
                nend = max(1, min(density, len(face)))
                ytargets = [miny + spanY * (mm + 0.5) / nend for mm in range(nend)]
                placed_any = False
                for yt in ytargets:
                    avail = [k for k in face if (gi_end, k) not in used]
                    if not avail:
                        break
                    k = min(avail, key=lambda k: abs(vs[k][1] - yt))
                    used.add((gi_end, k))
                    pts = _border_seam(gi_end, vs[k], ns[k], side_bvs, "y", rng)
                    if pts is not None:
                        end_seams.append(pts)
                        placed_any = True
                if not placed_any:
                    k = (min(range(len(vs)), key=lambda k: vs[k][0]) if want_left
                         else max(range(len(vs)), key=lambda k: vs[k][0]))
                    a = vs[k]
                    xto = px - 20 if want_left else px + pw + 20
                    end_seams.append([(a[0], a[1]), (xto, a[1])])

            return seams + end_seams + primary_caps + extra_caps

        best_here = None
        done_here = False
        for density in densities:
            best_d = None
            for variant in range(variants):
                seams_v = gen_seams(density, variant)
                surround, counters, st = G._vg_assemble(
                    seams_v, letter_union, letters_solid, background, panel, cfg
                )
                sc = G._vg_score(surround, panel, cfg)
                if best_d is None or sc < best_d[0]:
                    best_d = (sc, surround, counters, st, density)
                if sc[0] == 0 and sc[1] == 0 and sc[2] == 0 and sc[3] == 0:
                    done_here = True
                    break
            if best_here is None or best_d[0] < best_here[0]:
                best_here = best_d
            if done_here:
                break
            # Structural faults (thin bridge / un-split blob) cleared at
            # this density -> don't climb further: higher density only
            # worsens slivers/nubs, matching build_pieces_vertex_grid's rule.
            if best_d[0][0] == 0 and best_d[0][1] == 0:
                break
        return best_here + (letter_union,)

    best = None
    for track_mm_val in gaps:
        result = _try(track_mm_val)
        sc = result[0]
        if best is None or sc < best[0]:
            best = result
        if sc[0] == 0 and sc[1] == 0 and sc[2] == 0 and sc[3] == 0:
            break
        # Structural faults cleared -> a wider gap can only make pieces
        # sparser/slivery, not fix anything further; don't widen more.
        if sc[0] == 0 and sc[1] == 0:
            break
    sc, surround, counters, stats, density, letter_union = best
    stats["density"] = density
    stats["thin"], stats["oversized"], stats["sliver"], stats["nub"] = sc[0], sc[1], sc[2], sc[3]
    stats["score"] = sc

    letter_pieces = []
    if letter_union is not None:
        geoms = letter_union.geoms if isinstance(letter_union, MultiPolygon) else [letter_union]
        letter_pieces = [g for g in geoms if g.area > 100]
    pieces = {}
    for idx, poly in enumerate(surround + counters + letter_pieces):
        pieces[(idx, 0)] = poly
    stats["n_letters"] = len(letter_pieces)
    stats["n_cells"] = len(surround) + len(counters)
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
    row_h_mm, row_gap_mm, margin_top, margin_bottom = 26.0, 30.0, 24.0, 24.0
    panel_w_mm = 280.0
    panel_h_mm = margin_top + row_h_mm + row_gap_mm + row_h_mm + margin_bottom
    ppm = 5
    cfg = G.PuzzleConfig(
        panel_mm=panel_w_mm, panel_h_mm=panel_h_mm,
        piece_mm=25.0, tab_circle_r_px=15, tab_stem_w_px=30.0,
        letter_clearance_mm=4.0, corner_radius_mm=0.0,
        panel_w_px_fit=int(panel_w_mm * ppm), panel_h_px_fit=int(panel_h_mm * ppm),
    )
    pieces, stats, _letter_union = build_two_row_pieces(
        cfg, line1, line2, row_h_mm=row_h_mm, row_gap_mm=row_gap_mm,
        margin_top_mm=margin_top, margin_bottom_mm=margin_bottom,
    )
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
