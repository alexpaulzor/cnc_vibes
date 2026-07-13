#!/usr/bin/env python3
"""Vertex-grid tiling for name-banner puzzles (opt-in, `jigsaw.py vgrid`).

Letters are cut out as their own pieces; the background around them is tiled into
interlocking pieces that ENCASE each letter. Seam model (anchored perpendicular-
launch):
  * every seam vertex sits ON a letter or the outer border — never on another seam,
  * seams connect letter->letter or letter->border; no seam-to-seam junctions,
  * seams meet letters ~perpendicular (obtuse at convex corners),
  * seams meet the outer border within ~30deg of their reference axis,
  * CAP seams run vertical from a letter's top/bottom to the border,
  * GAP seams run letter->letter as: orthogonal launch -> S-curve -> STRAIGHT flat
    (tab-bulb wide, carries the one knob) -> S-curve -> orthogonal landing,
  * one REAL jigsaw knob per shared edge (narrow neck, wider bulb — pieces lock),
  * durability first: no material bridge < wall_mm anywhere,
  * fits within 300 x 150 mm (font/gap auto-shrink for long words).

Counters (O/A/D holes) are cut as their own loose pieces. Geometry is shapely
polygons in pixel space with `px_per_mm` so the emitter can scale to mm.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import shapely.geometry as sg
from PIL import Image, ImageDraw, ImageFont
from shapely.ops import linemerge, polygonize, unary_union

FONT_PATHS = [
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
_PAL = [
    (245, 224, 150),
    (176, 216, 178),
    (158, 202, 234),
    (208, 178, 226),
    (243, 197, 152),
    (176, 222, 212),
    (223, 187, 208),
    (203, 213, 162),
    (232, 205, 176),
    (188, 200, 232),
    (224, 222, 170),
    (170, 210, 205),
    (230, 190, 175),
    (198, 190, 226),
    (210, 224, 180),
    (180, 214, 222),
    (240, 215, 190),
    (200, 225, 200),
    (215, 200, 235),
    (235, 225, 165),
]

MAX_W_MM, MAX_H_MM = 300.0, 150.0  # stock envelope
WARMUP_MS = 1000.0  # machine constant (matches emitter.WARMUP_MS)


@dataclass
class VGParams:
    px_per_mm: float = 6.0
    gap_mm: float = 26.0  # nominal inter-letter gap (may shrink to fit width)
    margin_mm: float = 15.0  # left/right side margin
    strip_mm: float = 32.0  # top/bottom background strip height
    wall_mm: float = 4.0  # min material bridge (durability floor)
    min_side_mm: float = 18.0  # merge pieces thinner than this
    font_size_mm: float = 42.0
    corner_radius_mm: float = 5.0  # rounded plaque corners (banner standard)
    simplify_mm: float = 0.15  # decimate cut segments below this (anti-stutter)
    neck_mm: float = 5.0  # tab neck width  (< bulb -> pieces interlock)
    bulb_mm: float = 9.0  # tab bulb diameter
    reach_mm: float = 6.5  # edge -> bulb-center distance


@dataclass
class VGResult:
    pieces: list  # background pieces (shapely Polygons, px space)
    letters: list  # filled letter outer polygons (cut-outs)
    counters: list  # counter cut-outs (shapely Polygons)
    outer_contours: list  # raw cv2 contours for letters (px)
    counter_contours: list
    tabs: tuple  # (full, circle, skipped)
    durable: bool
    gap_mm: float
    px_per_mm: float
    w_px: int
    h_px: int
    plaque: object = None  # rounded-corner plaque polygon (px space)
    meta: dict = field(default_factory=dict)

    @property
    def w_mm(self):
        return self.w_px / self.px_per_mm

    @property
    def h_mm(self):
        return self.h_px / self.px_per_mm


def _load_font(size):
    for p in FONT_PATHS:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _sarea(poly):
    x, y = poly[:, 0], poly[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def _polys(g):
    if g.is_empty:
        return []
    if g.geom_type == "Polygon":
        return [g]
    return [
        p for p in getattr(g, "geoms", []) if p.geom_type == "Polygon" and p.area > 1
    ]


def _convex_anchors(cnt, eps_frac=0.02):
    """Convex (outward) polygon corners + the 4 curve extrema of a contour."""
    peri = cv2.arcLength(cnt, True)
    ap = cv2.approxPolyDP(cnt, eps_frac * peri, True).reshape(-1, 2).astype(float)
    n = len(ap)
    orient = np.sign(_sarea(ap)) or 1.0
    pts = []
    for i in range(n):
        p0, p1, p2 = ap[i - 1], ap[i], ap[(i + 1) % n]
        cr = (p1[0] - p0[0]) * (p2[1] - p1[1]) - (p1[1] - p0[1]) * (p2[0] - p1[0])
        if np.sign(cr) == orient:
            pts.append((float(p1[0]), float(p1[1])))
    cc = cnt.reshape(-1, 2)
    for idx in (
        cc[:, 0].argmin(),
        cc[:, 0].argmax(),
        cc[:, 1].argmin(),
        cc[:, 1].argmax(),
    ):
        pts.append((float(cc[idx][0]), float(cc[idx][1])))
    return pts


# ------------------------------------------------------------------ geometry ---
def knob(mid, tangent, side, neck_w, bulb_d, reach):
    """A real jigsaw knob (narrow neck flaring into a wider bulb) as a Polygon.

    mid=(x,y) on the edge; tangent=unit dir along the edge; side=+/-1 (which side
    the knob protrudes); neck_w<bulb_d so neighbouring pieces mechanically lock.
    """
    mid = np.asarray(mid, float)
    t = np.asarray(tangent, float)
    t = t / (np.hypot(t[0], t[1]) + 1e-12)
    n = np.array([-t[1], t[0]]) * float(np.sign(side) or 1.0)
    rb, hn = bulb_d / 2.0, min(neck_w / 2.0, 0.85 * bulb_d / 2.0)

    def P(u, v):
        p = mid + u * t + v * n
        return (float(p[0]), float(p[1]))

    over = rb + reach
    neck = sg.Polygon([P(-hn, -over), P(hn, -over), P(hn, reach), P(-hn, reach)])
    bulb = sg.Point(P(0.0, reach)).buffer(rb, quad_segs=48)
    raw = unary_union([neck, bulb])
    rf = max(0.35 * neck_w, 0.55 * (rb - hn), 0.1)  # fillet the neck<->bulb waist
    smooth = raw.buffer(rf, join_style=1).buffer(-rf, join_style=1)
    big = 10.0 * (reach + bulb_d + neck_w) + 1000.0
    half = sg.Polygon([P(-big, 0.0), P(big, 0.0), P(big, big), P(-big, big)])
    out = smooth.intersection(half)
    if out.geom_type != "Polygon":
        parts = [g for g in getattr(out, "geoms", []) if g.geom_type == "Polygon"]
        out = max(parts, key=lambda g: g.area) if parts else raw
    return out.buffer(0)


def _s_curve(p_letter, launch, p_flat, flat_dir, samples=22):
    """Bezier from a letter (tangent=launch, perpendicular) to a flat endpoint
    (tangent=flat_dir) so it meets the straight tab segment smoothly."""
    p0, p3 = np.array(p_letter, float), np.array(p_flat, float)
    lu = np.array(launch, float)
    lu /= np.hypot(*lu) + 1e-9
    fu = np.array(flat_dir, float)
    fu /= np.hypot(*fu) + 1e-9
    h = 0.4 * np.hypot(*(p3 - p0))
    c1, c2 = p0 + h * lu, p3 - h * fu
    out = []
    for t in np.linspace(0, 1, samples):
        mt = 1 - t
        p = mt**3 * p0 + 3 * mt**2 * t * c1 + 3 * mt * t**2 * c2 + t**3 * p3
        out.append((float(p[0]), float(p[1])))
    return out


def _render_letters(word, prm):
    """Raster the word (Arial Black); return (outer_cnts, counter_cnts, W, H)."""
    ppm = prm.px_per_mm
    font = _load_font(int(round(prm.font_size_mm * ppm)))
    imgs = []
    for ch in word:
        bb = ImageDraw.Draw(Image.new("L", (10, 10))).textbbox((0, 0), ch, font=font)
        g = Image.new("L", (max(1, bb[2] - bb[0]), max(1, bb[3] - bb[1])), 0)
        ImageDraw.Draw(g).text((-bb[0], -bb[1]), ch, font=font, fill=255)
        imgs.append(g)
    gap = prm.gap_mm * ppm
    side = prm.margin_mm * ppm
    strip = prm.strip_mm * ppm
    gh = max(g.height for g in imgs)
    lettersW = sum(g.width for g in imgs) + gap * (len(imgs) - 1)
    W = int(lettersW + 2 * side)
    H = int(min(MAX_H_MM * ppm, gh + 2 * strip))
    mask = Image.new("L", (W, H), 0)
    x = side
    for g in imgs:
        mask.paste(g, (int(x), int((H - gh) / 2)))
        x += g.width + gap
    arr = np.where(np.array(mask) > 127, 255, 0).astype(np.uint8)
    cont, hier = cv2.findContours(arr, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    outer, counters = [], []
    if hier is not None:
        for c, h in zip(cont, hier[0]):
            if cv2.contourArea(c) < 200:
                continue
            (outer if h[3] < 0 else counters).append(c)
    outer.sort(key=lambda c: cv2.boundingRect(c)[0])
    return outer, counters, W, H


def _build_seams(outer_cnts, letters, W, H, prm, rng):
    """Return (seams, gap_sites). gap_sites = [((cx,cy),(dx,dy)), ...]."""
    ppm = prm.px_per_mm
    seams, gap_sites = [], []
    lb = [lt.bounds for lt in letters]
    cens = [lt.centroid for lt in letters]

    # CAP seams: vertical, from top/bottom convex corners up/down to the border.
    for i in range(len(letters)):
        anchors = np.array(_convex_anchors(outer_cnts[i]))
        minx, miny, maxx, maxy = lb[i]
        cx = (minx + maxx) / 2
        top = anchors[anchors[:, 1] < cens[i].y]
        bot = anchors[anchors[:, 1] > cens[i].y]
        wide = (maxx - minx) > 30 * ppm
        for grp, ydir in ((top, -1), (bot, 1)):
            if len(grp) == 0:
                continue
            left, right = grp[grp[:, 0] <= cx], grp[grp[:, 0] > cx]
            chosen = []
            if len(left):
                chosen.append(
                    left[np.argmin(left[:, 1]) if ydir < 0 else np.argmax(left[:, 1])]
                )
            if len(right):
                chosen.append(
                    right[
                        np.argmin(right[:, 1]) if ydir < 0 else np.argmax(right[:, 1])
                    ]
                )
            if wide:
                chosen.append(grp[np.argmin(np.abs(grp[:, 0] - cx))])
            for a in chosen:
                y_to = -20 if ydir < 0 else H + 20
                seams.append(sg.LineString([(a[0], a[1]), (a[0], y_to)]))

    # GAP seams: launch -> S -> straight flat (tab) -> S -> landing.
    bulb = prm.bulb_mm * ppm
    flat_len = bulb * 1.35
    for i in range(len(letters) - 1):
        anc_i = np.array(_convex_anchors(outer_cnts[i]))
        anc_j = np.array(_convex_anchors(outer_cnts[i + 1]))
        cx_i = (lb[i][0] + lb[i][2]) / 2
        cx_j = (lb[i + 1][0] + lb[i + 1][2]) / 2
        mx = (lb[i][2] + lb[i + 1][0]) / 2
        my = (cens[i].y + cens[i + 1].y) / 2 + rng.uniform(-6, 6) * ppm
        theta = np.radians(rng.uniform(-22, 22))  # seed rotates the flat
        dirv = np.array([np.cos(theta), np.sin(theta)])
        ctr = np.array([mx, my])
        A, B = ctr - dirv * flat_len / 2, ctr + dirv * flat_len / 2
        rside = anc_i[anc_i[:, 0] > cx_i]
        lside = anc_j[anc_j[:, 0] < cx_j]
        rside = rside if len(rside) else anc_i
        lside = lside if len(lside) else anc_j
        lv = rside[np.argmin(np.hypot(rside[:, 0] - A[0], rside[:, 1] - A[1]))]
        rv = lside[np.argmin(np.hypot(lside[:, 0] - B[0], lside[:, 1] - B[1]))]
        leftS = _s_curve((lv[0], lv[1]), (1, 0), (A[0], A[1]), dirv)
        rightS = _s_curve((rv[0], rv[1]), (-1, 0), (B[0], B[1]), -dirv)
        seams.append(sg.LineString(leftS + list(reversed(rightS))))
        gap_sites.append(((mx, my), (float(dirv[0]), float(dirv[1]))))

    # END seams: outer letters -> L/R border (any angle within ~30deg of axis).
    anc0 = np.array(_convex_anchors(outer_cnts[0]))
    l0 = anc0[np.argmin(anc0[:, 0])]
    seams.append(sg.LineString([(l0[0], l0[1]), (-20, l0[1])]))
    ancN = np.array(_convex_anchors(outer_cnts[-1]))
    rN = ancN[np.argmax(ancN[:, 0])]
    seams.append(sg.LineString([(rN[0], rN[1]), (W + 20, rN[1])]))
    return seams, gap_sites


def _make_pieces(W, H, letters_solid, seams, prm):
    ppm = prm.px_per_mm
    cr = prm.corner_radius_mm * ppm
    plaque = sg.box(cr, cr, W - cr, H - cr).buffer(cr, join_style=1)
    background = plaque.difference(letters_solid)
    clipped = []
    for s in seams:
        g = s.intersection(background)
        for part in getattr(g, "geoms", [g]):
            if part.geom_type == "LineString" and part.length > 3 * ppm:
                clipped.append(part)
    net = unary_union([plaque.boundary, letters_solid.boundary] + clipped)
    faces = [
        f
        for f in polygonize(net)
        if background.contains(f.representative_point()) and f.area > (5 * ppm) ** 2
    ]
    return plaque, background, faces


def _merge_small(pieces, prm):
    """Merge undersized/thin faces into the neighbor sharing the longest edge."""
    ms = prm.min_side_mm * prm.px_per_mm
    ma = (prm.min_side_mm * 1.15 * prm.px_per_mm) ** 2
    changed = True
    while changed:
        changed = False
        pieces.sort(key=lambda p: p.area)
        for i, a in enumerate(pieces):
            bx = a.bounds
            if not (min(bx[2] - bx[0], bx[3] - bx[1]) < ms or a.area < ma):
                continue
            best = None
            for j, b in enumerate(pieces):
                if i == j:
                    continue
                sh = a.intersection(b)
                L = sh.length if "Line" in sh.geom_type else 0
                if L <= 0:
                    continue
                u = unary_union([a, b])
                if u.geom_type == "Polygon" and (best is None or L > best[1]):
                    best = (j, L, u)
            if best:
                pieces[best[0]] = best[2]
                pieces.pop(i)
                changed = True
                break
    return pieces


def _split_pinched(pieces, prm):
    """Split a piece that necks below the wall thickness at the pinch (a topological
    pinch is a fragile bridge)."""
    w = prm.wall_mm * prm.px_per_mm
    out = []
    for p in pieces:
        comps = _polys(p.buffer(-w / 2))
        if len(comps) <= 1:
            out.append(p)
            continue
        subs = [s for c in comps for s in _polys(c.buffer(w / 2).intersection(p))]
        leftover = p.difference(unary_union(subs)) if subs else p
        for frag in _polys(leftover):
            if not subs:
                subs = [frag]
                continue
            k = min(range(len(subs)), key=lambda i: subs[i].distance(frag))
            subs[k] = unary_union([subs[k], frag]).buffer(0)
        out.extend(_polys(unary_union(subs)) if subs else [p])
    return out


def _durable(pieces, prm):
    w = prm.wall_mm * prm.px_per_mm
    bad = [
        k
        for k, p in enumerate(pieces)
        if p.buffer(-w / 2).is_empty or len(_polys(p.buffer(-w / 2))) != 1
    ]
    return len(bad) == 0, bad


def _place_knob(pieces, mid, tv, rng, knobs, letters_solid, prm):
    ppm = prm.px_per_mm
    neck, bulb, reach = prm.neck_mm * ppm, prm.bulb_mm * ppm, prm.reach_mm * ppm
    wall = prm.wall_mm * ppm

    def piece_at(pt):
        cand = [k for k, p in enumerate(pieces) if p.distance(sg.Point(pt)) < 1.5]
        return cand[0] if cand else None

    for side in (1, -1) if rng.random() < 0.5 else (-1, 1):
        kb = knob((mid[0], mid[1]), tv, side, neck, bulb, reach)
        if kb.distance(letters_solid) < wall * 0.5:
            continue
        if any(kb.intersects(pk) for pk in knobs):
            continue
        nx, ny = -tv[1] * side, tv[0] * side
        recv = piece_at((mid[0] + nx * 3, mid[1] + ny * 3))
        donor = piece_at((mid[0] - nx * 3, mid[1] - ny * 3))
        if recv is None or donor is None or recv == donor:
            continue
        try:
            pieces[recv] = unary_union([pieces[recv], kb]).buffer(0)
            d = pieces[donor].difference(kb)
            dp = _polys(d)
            pieces[donor] = max(dp, key=lambda p: p.area) if dp else pieces[donor]
            knobs.append(kb)
            return True
        except Exception:
            return False
    return False


def _add_tabs(pieces, letters_solid, gap_sites, prm, seed):
    ppm = prm.px_per_mm
    rng = np.random.default_rng(seed)
    neck, bulb = prm.neck_mm * ppm, prm.bulb_mm * ppm
    knobs = []
    # 1) each gap seam's flat spot gets its one knob first
    for (cx, cy), (dx, dy) in gap_sites:
        _place_knob(
            pieces, (cx, cy), np.array([dx, dy]), rng, knobs, letters_solid, prm
        )
    # 2) every other shared edge (caps, ends) gets one knob; skip already-tabbed
    edges = []
    for i in range(len(pieces)):
        for j in range(i + 1, len(pieces)):
            sh = pieces[i].intersection(pieces[j])
            if sh.geom_type == "MultiLineString":
                sh = max(sh.geoms, key=lambda s: s.length)
            if sh.geom_type == "LineString" and sh.length > neck * 1.6:
                edges.append(sh)
    for sh in edges:
        if any(sh.distance(pk.centroid) < bulb for pk in knobs):
            continue
        for frac in (0.5, 0.42, 0.58, 0.35, 0.65):
            mid = sh.interpolate(frac, normalized=True)
            a = sh.interpolate(max(0, frac - 0.05), normalized=True)
            b = sh.interpolate(min(1, frac + 0.05), normalized=True)
            tv = np.array([b.x - a.x, b.y - a.y])
            n = np.hypot(*tv)
            if n < 1e-6:
                continue
            tv /= n
            if _place_knob(pieces, (mid.x, mid.y), tv, rng, knobs, letters_solid, prm):
                break
    return pieces, knobs


def _build_one(word, seed, prm):
    """Build the tiling at the given params. Returns a VGResult."""
    rng = np.random.default_rng(seed)
    outer, counters, W, H = _render_letters(word, prm)
    letters = [sg.Polygon(c.reshape(-1, 2)).buffer(0) for c in outer]
    counter_polys = [sg.Polygon(c.reshape(-1, 2)).buffer(0) for c in counters]
    letters_solid = unary_union(letters) if letters else sg.Point(-9, -9)
    seams, gap_sites = _build_seams(outer, letters, W, H, prm, rng)
    plaque, background, faces = _make_pieces(W, H, letters_solid, seams, prm)
    pieces = _merge_small(list(faces), prm)
    pieces = _split_pinched(pieces, prm)
    pieces = _merge_small(pieces, prm)
    pieces, knobs = _add_tabs(pieces, letters_solid, gap_sites, prm, seed)
    ok, _bad = _durable(pieces, prm)
    return VGResult(
        pieces=pieces,
        letters=letters,
        counters=counter_polys,
        outer_contours=outer,
        counter_contours=counters,
        tabs=(len(knobs), 0, 0),
        durable=ok,
        gap_mm=prm.gap_mm,
        px_per_mm=prm.px_per_mm,
        w_px=W,
        h_px=H,
        plaque=plaque,
    )


def build(word, seed=7, params=None, auto_gap=True):
    """Build a vertex-grid puzzle. `auto_gap` (kept for API compat) enables the
    width auto-fit: font/gap/margin shrink together until the panel fits 300mm."""
    word = word.upper()
    prm = params or VGParams()
    tries = []
    res = _build_one(word, seed, prm)
    tries.append(prm.gap_mm)
    if auto_gap:
        for _ in range(5):
            if res.w_mm <= MAX_W_MM:
                break
            scale = (MAX_W_MM / res.w_mm) * 0.98
            prm = VGParams(**{**prm.__dict__,
                              "font_size_mm": prm.font_size_mm * scale,
                              "gap_mm": prm.gap_mm * scale,
                              "margin_mm": prm.margin_mm * scale})
            res = _build_one(word, seed, prm)
            tries.append(prm.gap_mm)
    res.meta["word"] = word
    res.meta["gap_tries"] = tries
    res.meta["fits_envelope"] = (res.w_mm <= MAX_W_MM and res.h_mm <= MAX_H_MM)
    if not res.durable:
        res.meta["durable_failed"] = True
    return res


# -------------------------------------------------------------------- gcode ---
def _mm_ring(coords, ppm, h_px):
    # px (Y-down, origin top-left) -> mm (Y-up, WCS origin at plaque bottom-left)
    return [(x / ppm, (h_px - y) / ppm) for (x, y) in coords]


def _advance(pts, dist):
    """Point `dist` mm along polyline pts from its start (warmup lead-in)."""
    acc = 0.0
    for a, b in zip(pts, pts[1:]):
        seg = ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5
        if acc + seg >= dist:
            t = (dist - acc) / seg if seg else 0
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        acc += seg
    return pts[-1]


def emit_gcode(res: VGResult, feed_mm_min=600, power_percent=100.0):
    """Emit validator-shaped GRBL laser GCode for a vertex-grid puzzle.

    Static M3 @ power_percent, WCS origin bottom-left, INTERIOR-FIRST cut order
    (all seams + letters + counters, then the plaque border last so the panel
    stays held), each continuous-cut chain preceded by a ~1s out-and-back warmup.

    VERIFY feed/warmup with a hardware test-cut before trusting on real stock.
    """
    ppm, H = res.px_per_mm, res.h_px
    S = int(round(power_percent * 10))
    plaque = res.plaque if res.plaque is not None else sg.box(0, 0, res.w_px, res.h_px)
    tol = res.px_per_mm * 0.15  # decimate per-pixel contour noise (anti-stutter)
    net = unary_union(
        [p.boundary for p in res.pieces]
        + [ln.boundary for ln in res.letters]
        + [c.boundary for c in res.counters]
    )
    border = net.intersection(plaque.exterior.buffer(1.0))
    interior = net.difference(border)

    def paths(geom):
        m = linemerge(geom) if geom.geom_type == "MultiLineString" else geom
        gs = list(m.geoms) if m.geom_type == "MultiLineString" else [m]
        return [g for g in gs if g.geom_type == "LineString" and g.length > 1]

    interior_paths = paths(interior)

    def chain(segs):
        from collections import defaultdict

        coords = [list(s.coords) for s in segs]

        def k(p):
            return (round(p[0], 1), round(p[1], 1))

        ends = defaultdict(list)
        for i, c in enumerate(coords):
            ends[k(c[0])].append(i)
            ends[k(c[-1])].append(i)
        used = [False] * len(coords)
        chains = []
        for start in range(len(coords)):
            if used[start]:
                continue
            used[start] = True
            ch = list(coords[start])
            while True:
                kk = k(ch[-1])
                nxt = next((i for i in ends[kk] if not used[i]), None)
                if nxt is None:
                    break
                used[nxt] = True
                c = coords[nxt]
                ch += c[1:] if k(c[0]) == kk else c[-2::-1]
            while True:
                kk = k(ch[0])
                nxt = next((i for i in ends[kk] if not used[i]), None)
                if nxt is None:
                    break
                used[nxt] = True
                c = coords[nxt]
                ch = (c[:-1] if k(c[-1]) == kk else c[:0:-1]) + ch
            chains.append(ch)
        return chains

    def order_nn(chains):
        remaining = list(chains)
        cur, out = (0.0, 0.0), []
        while remaining:
            i = min(
                range(len(remaining)),
                key=lambda i: (
                    (remaining[i][0][0] - cur[0]) ** 2
                    + (remaining[i][0][1] - cur[1]) ** 2
                ),
            )
            g = remaining.pop(i)
            out.append(g)
            cur = g[-1]
        return out

    ordered = order_nn(chain(interior_paths)) + chain(paths(plaque.exterior))
    warm_mm = feed_mm_min * (WARMUP_MS / 1000.0) / 60.0

    lines = [
        "; vertex-grid puzzle — GRBL laser, static M3, WCS origin bottom-left",
        f"; {res.meta.get('word', '')} {res.w_mm:.0f}x{res.h_mm:.0f}mm  "
        f"feed={feed_mm_min}mm/min  power={power_percent:.0f}%  passes=1  "
        f"pieces={len(res.pieces)} letters={len(res.letters)} "
        f"counters={len(res.counters)}",
        "; cut order: interior seams + letters + counters FIRST, plaque border LAST.",
        "; continuous-cut chains; front-loaded out-and-back warmup (~1s) per chain.",
        "; VERIFY feed/warmup with a hardware test-cut before real stock.",
        "$32=1",
        "G21",
        "G90",
        "M5",
        "G0 X0.000 Y0.000",
        "",
    ]
    for ch in ordered:
        if len(ch) >= 2:
            simp = sg.LineString(ch).simplify(tol, preserve_topology=False)
            if simp.geom_type == "LineString" and len(simp.coords) >= 2:
                ch = list(simp.coords)
        pm = _mm_ring(ch, ppm, H)
        if len(pm) < 2:
            continue
        sx, sy = pm[0]
        wpt = _advance(pm, warm_mm / 2.0)
        lines += [
            f"G0 X{sx:.3f} Y{sy:.3f}",
            f"M3 S{S}",
            f"F{feed_mm_min}",
            "; warmup out-and-back (~1s)",
            f"G1 X{wpt[0]:.3f} Y{wpt[1]:.3f}",
            f"G1 X{sx:.3f} Y{sy:.3f}",
        ]
        for x, y in pm[1:]:
            lines.append(f"G1 X{x:.3f} Y{y:.3f}")
        lines += ["M5", ""]
    lines += ["G0 X0.000 Y0.000", ""]
    return "\n".join(lines)


# ------------------------------------------------------------------ preview ---
def render_preview(res: VGResult, out_path: Path, title=None):
    """Render a colored preview PNG (pieces + cut-out letters + counters) with a
    white title band on top (title may contain newlines)."""
    fnt = _load_font(max(13, int(2.6 * res.px_per_mm)))
    n_lines = (title.count("\n") + 1) if title else 0
    line_h = int(fnt.size * 1.35)
    header = (n_lines * line_h + 14) if title else 0
    img = Image.new("RGB", (res.w_px, res.h_px + header), "white")
    d = ImageDraw.Draw(img, "RGBA")
    if title:
        d.multiline_text((10, 7), title, fill=(0, 0, 0), font=fnt, spacing=6)
        d.line([0, header - 1, res.w_px, header - 1], fill=(210, 210, 210), width=1)

    def shift(coords):
        return [(x, y + header) for x, y in coords]

    for i, c in enumerate(res.pieces):
        for poly in _polys(c):
            d.polygon(shift(poly.exterior.coords),
                      fill=_PAL[i % len(_PAL)] + (180,), outline=(50, 50, 50))
    for c in res.outer_contours:
        d.polygon(
            shift([tuple(map(int, q)) for q in c.reshape(-1, 2)]),
            fill=(158, 46, 46, 235),
            outline=(90, 20, 20),
        )
    for c in res.counter_contours:
        d.polygon(
            shift([tuple(map(int, q)) for q in c.reshape(-1, 2)]),
            fill=(250, 250, 250, 255),
            outline=(120, 90, 90),
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    return out_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="vertex_grid", description="Vertex-grid name-banner puzzle preview."
    )
    ap.add_argument("--word", default="WOJO")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--gap-mm", type=float, default=26.0)
    ap.add_argument("--px-per-mm", type=float, default=6.0)
    ap.add_argument("--no-auto-gap", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    prm = VGParams(px_per_mm=args.px_per_mm, gap_mm=args.gap_mm)
    res = build(args.word, args.seed, prm, auto_gap=not args.no_auto_gap)
    out = args.out or (
        Path(__file__).resolve().parent / "build"
        / f"vertex_grid_{args.word.lower()}_seed{args.seed}.png"
    )
    title = (
        f"{args.word} seed{args.seed}: {len(res.pieces)} pieces + "
        f"{len(res.letters)} letters + {len(res.counters)} counters | "
        f"{res.tabs[0]} tabs | durable={'YES' if res.durable else 'NO'} | "
        f"{res.w_mm:.0f}x{res.h_mm:.0f}mm"
    )
    render_preview(res, out, title)
    print(f"{args.word} seed{args.seed}: pieces {len(res.pieces)} letters "
          f"{len(res.letters)} counters {len(res.counters)} tabs {res.tabs} "
          f"durable {res.durable} {res.w_mm:.0f}x{res.h_mm:.0f}mm")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
