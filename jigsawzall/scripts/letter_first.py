"""Letter-first ring layout: place every letter first, then derive the puzzle.

1. Letters: the outer words go around the disc (fit_ring) with `group_gap_mm`
   between the rim and the letter tops and between neighbouring letters; the
   center words (two lines) are scaled up as far as they fit while staying
   `group_gap_mm` from the outer letters and from each other. The (outer cap,
   center cap) pair is chosen to maximise the total letter area.
2. Edges: a generalized Voronoi diagram of the letters -- each outer letter
   (or ornament) and each whole center word is one site; the seams run along
   the medial lines halfway between neighbouring sites, so every site gets
   its own region of wood.
3. Pieces: each region is split by cuts radiating from its letter/word out
   to the region's boundary -- at least two (no piece wraps all the way round
   a letter) and more for big regions -- then tabs, QA and everything
   downstream are the ring generator's (assemble_qa).

Selected with ring_prototype.py --center-style letterfirst.
"""
from __future__ import annotations

import math
import os
import random
from dataclasses import replace

import shapely
from shapely import affinity
from shapely.geometry import LineString, MultiPoint, MultiPolygon, Point
from shapely.ops import linemerge, nearest_points, unary_union

import ring_prototype as R

GROUP_GAP_MM = 15.0  # tab space: rim <-> outer letters <-> center words


def _ref_glyphs(rp, ppm, words, ref_cap_mm=40.0):
    """Center-word glyphs at a reference size (rasterised once); later sizes
    are geometric scalings of these -- fast enough to search many sizes."""
    font = R._center_font(rp, ref_cap_mm, ppm)
    out = []
    for w in words:
        glyphs, xs, total = R._word_advances(w, font, 0.0)
        out.append((glyphs, list(xs)))
    return out, ref_cap_mm


def _word_on_path(glyphs, pos, path):
    """Place glyphs (local: centred on x=0, baseline y=0, letter in -y) with
    their baseline centres at arc-length positions `pos` along `path`."""
    out = []
    for g, s_ in zip(glyphs, pos):
        (px, py), (nx, ny) = path_point(path, s_)
        a = math.atan2(nx, -ny)  # rotation taking local "up" (0,-1) to n
        out.append(affinity.translate(affinity.rotate(g, a, origin=(0, 0), use_radians=True), px, py))
    return out


def path_point(path, s_):
    """(point, outward unit normal) at arc length s_ along a word path.
    path: ("line", y, up) or ("arc", cx, cy, rho, up). `up` = +1 when the
    letters stand on the path pointing away from the other word (top word),
    -1 for the bottom word."""
    if path[0] == "line":
        _, x0, y, up = path
        return (x0 + s_, y), (0.0, -1.0 * up)
    _, cx, cy, rho, up = path
    phi = s_ / rho
    if up > 0:  # frown: circle centre below the word, letters point outward
        return (cx + rho * math.sin(phi), cy - rho * math.cos(phi)), (math.sin(phi), -math.cos(phi))
    # smile: centre above, letters point toward the centre (upright to read)
    return (cx + rho * math.sin(phi), cy + rho * math.cos(phi)), (-math.sin(phi), -math.cos(phi))


def _center_block(ref, ref_cap, cap_mm, gap_px, C, ppm, t_px, curv):
    """Both center lines at cap_mm. curv = 0 (straight) or the arc radius as a
    multiple of the line length. Letters keep >= t of wood inside a word;
    the lines sit gap_px apart (real glyph distance), centred on C.
    Returns (word unions, letters per word, path per word)."""
    k = cap_mm / ref_cap
    words, paths = [], []
    for wi, (glyphs, xs) in enumerate(ref):
        gs = [affinity.scale(g, k, k, origin=(0, 0)) for g in glyphs]
        pos = [x * k for x in xs]
        span = (pos[-1] - pos[0]) + 1e-6
        up = 1 if wi == 0 else -1
        if curv:
            rho = curv * span
            path = ("arc", 0.0, rho if up > 0 else -rho, rho, up)
        else:
            path = ("line", 0.0, 0.0, up)
        extra = 0.0
        for _ in range(60):  # widen tracking until letters keep t of wood
            p_ = [x + extra * (i - (len(pos) - 1) / 2) for i, x in enumerate(pos)]
            placed = _word_on_path(gs, p_, path)
            gaps = [placed[i].distance(placed[i + 1]) for i in range(len(placed) - 1)]
            if not gaps or min(gaps) >= t_px:
                break
            extra += 0.25 * ppm
        words.append(placed)
        paths.append(path)
    def shift(wi, dx, dy):
        words[wi] = [affinity.translate(g, dx, dy) for g in words[wi]]
        pth = paths[wi]
        paths[wi] = (("line", pth[1] + dx, pth[2] + dy, pth[3]) if pth[0] == "line"
                     else ("arc", pth[1] + dx, pth[2] + dy, pth[3], pth[4]))
    if len(words) > 1:
        u1, u2 = unary_union(words[0]), unary_union(words[1])
        dy = (u1.bounds[3] - u2.bounds[1]) + gap_px
        for _ in range(8):
            d = u1.distance(affinity.translate(u2, 0, dy))
            dy += gap_px - d
        shift(1, 0, dy)
    both = unary_union([g for w in words for g in w])
    cx, cy = (both.bounds[0] + both.bounds[2]) / 2, (both.bounds[1] + both.bounds[3]) / 2
    for wi in range(len(words)):
        shift(wi, C[0] - cx, C[1] - cy)
    return [unary_union(w) for w in words], words, paths


def _ring_block(ref, ref_cap, cap_mm, r_top, C, ppm, t_px):
    """Center words on one inner ring concentric with the disc: word 1 over
    the top with its letters pointing outward (baseline radius r_top), word 2
    along the bottom pointing inward so it reads upright (baseline radius
    r_top + cap) -- both in the same band of radius, like a second ring of
    letters. Letters keep >= t of wood inside a word.
    Returns (word unions, letters per word, path per word)."""
    k = cap_mm / ref_cap
    cap_px = cap_mm * ppm
    words, paths = [], []
    for wi, (glyphs, xs) in enumerate(ref):
        gs = [affinity.scale(g, k, k, origin=(0, 0)) for g in glyphs]
        pos = [x * k for x in xs]
        up = 1 if wi == 0 else -1
        path = ("arc", C[0], C[1], r_top if up > 0 else r_top + cap_px, up)
        extra = 0.0
        for _ in range(60):
            p_ = [x + extra * (i - (len(pos) - 1) / 2) for i, x in enumerate(pos)]
            placed = _word_on_path(gs, p_, path)
            gaps = [placed[i].distance(placed[i + 1]) for i in range(len(placed) - 1)]
            if not gaps or min(gaps) >= t_px:
                break
            extra += 0.25 * ppm
        words.append(placed)
        paths.append(path)
    return [unary_union(w) for w in words], words, paths


def layout(words, center, rp, ppm, C, panel):
    """Pick the outer cap, center cap and center curvature that maximise the
    total letter area."""
    gap = rp.group_gap_mm * ppm
    t_px = rp.min_feature_mm * ppm
    # between the two center words: room for the biggest tab on either side
    word_gap = max(gap, 2 * (3 * max(R.TAB_SIZE_CLASSES) * t_px + t_px))
    ref = _ref_glyphs(rp, ppm, center) if center else None
    best = None
    h = rp.cap_h_max_mm
    while h >= rp.cap_h_min_mm:
        rpo = replace(rp, cap_h_max_mm=h, cap_h_min_mm=h, rim_mm=rp.group_gap_mm,
                      min_gap_mm=rp.group_gap_mm, hub_r_min_mm=1.0, hub_ring_min_mm=0.0,
                      center_text=None)
        L = R.fit_ring(words, rpo, ppm)
        h -= 2.0
        if not L["ok"]:
            continue
        world = [R.place(g, th, L["Rin"], (0.0, 0.0)) for g, th in zip(L["locs"], L["ths"])]
        world = [affinity.translate(g, C[0], C[1]) for g in world]
        outer = unary_union([R.solid_of(g) for g in world])
        area_o = sum(g.area for g in world)
        if not center:
            cand = [(area_o, L, world, [], [], [], None, 0.0)]
        else:
            allowed = panel.difference(outer.buffer(gap))
            allowed = next((g for g in getattr(allowed, "geoms", [allowed])
                            if g.contains(Point(C))), None)
            if allowed is None:
                continue
            # the band the center ring may use: out to the inscribed circle of
            # the allowed region; letters' outer edge sits on it
            r_out = allowed.exterior.distance(Point(C))
            cand = []
            lo, hi = 3.0, 60.0
            for _ in range(14):  # largest center cap whose ring fits
                mid = (lo + hi) / 2
                r_top = r_out - mid * ppm
                ok = r_top > 0
                if ok:
                    wu, _l, _p = _ring_block(ref[0], ref[1], mid, r_top, C, ppm, t_px)
                    ok = allowed.contains(unary_union(wu)) and (
                        len(wu) < 2 or wu[0].distance(wu[1]) >= word_gap)
                if ok:
                    lo = mid
                else:
                    hi = mid
            if lo >= rp.center_text_cap_min_mm:
                cw, cl, cp = _ring_block(ref[0], ref[1], lo, r_out - lo * ppm, C, ppm, t_px)
                area = area_o + sum(g.area for ln in cl for g in ln)
                cand.append((area, L, world, cw, cl, cp, lo, r_out / ppm))
        for c_ in cand:
            if best is None or c_[0] > best[0]:
                best = c_
    if best is None:
        raise SystemExit("letterfirst: no layout fits")
    return best[1:]


def voronoi_regions(sites, panel, ppm, step_mm=1.0):
    """Generalized Voronoi: one region of the panel per site (polygon),
    from a point Voronoi of points sampled along every site's outline."""
    pts, owner = [], []
    for i, s in enumerate(sites):
        rings = []
        for g in getattr(s, "geoms", [s]):
            rings.append(g.exterior)
        for ring in rings:
            n = max(8, int(ring.length / (step_mm * ppm)))
            for k in range(n):
                q = ring.interpolate(k / n, normalized=True)
                pts.append(q)
                owner.append(i)
    env = panel.envelope.buffer(panel.length)
    cells = shapely.voronoi_polygons(MultiPoint(pts), extend_to=env)
    # voronoi_polygons returns cells in input order only with ordered=True
    try:
        cells = shapely.voronoi_polygons(MultiPoint(pts), extend_to=env, ordered=True)
        cell_list = list(cells.geoms)
    except TypeError:
        cell_list = [next(c for c in cells.geoms if c.contains(p) or c.touches(p)) for p in pts]
    groups = {}
    for c, i in zip(cell_list, owner):
        groups.setdefault(i, []).append(c)
    regions = []
    for i in range(len(sites)):
        reg = unary_union(groups.get(i, [])).intersection(panel)
        regions.append(reg.buffer(0))
    return regions


def _ray_to(p0, d, reg, panel, t, far):
    """First point on region `reg`'s boundary along the ray p0 + d*s (s>2t).
    Returns (point, lands_on_disc_edge) or (None, False)."""
    ray = LineString([p0, (p0[0] + d[0] * far, p0[1] + d[1] * far)])
    hit = ray.intersection(reg.exterior)
    cs = [c for g in getattr(hit, "geoms", [hit]) for c in getattr(g, "coords", [])]
    cs = [c for c in cs if math.dist(c, p0) > 2 * t]
    if not cs:
        return None, False
    end = min(cs, key=lambda c: math.dist(c, p0))
    return end, panel.exterior.distance(Point(end)) < 1.0


def _path_span(letters, path):
    """Arc-length range covered by a word's letters along its path."""
    ss = []
    for g in letters:
        c = g.centroid
        if path[0] == "line":
            ss.append(c.x - path[1])
        else:
            _, cx, cy, rho, up = path
            ang = math.atan2(c.x - cx, (cy - c.y) if up > 0 else (c.y - cy))
            ss.append(ang * rho)
    w = max(g.bounds[2] - g.bounds[0] for g in letters) / 2
    return min(ss) - w, max(ss) + w


def _column(letters, path, s_, t, ppm, need=("outer", "inner")):
    """A cut square to the word's line at arc length s_, through exactly one
    letter. Returns (outer exit point, inner exit point, foot point on the
    path, outward normal) -- or None when the cut would meet a letter edge
    at a shallow angle (< 55 deg) or run alongside another part of the same
    letter within t + wobble (a P's stem beside a cut from its bowl)."""
    (px, py), (nx, ny) = path_point(path, s_)
    big = 200 * ppm
    line = LineString([(px - nx * big, py - ny * big), (px + nx * big, py + ny * big)])
    hits = [(g, line.intersection(g)) for g in letters]
    hits = [(g, h) for g, h in hits if not h.is_empty]
    if len(hits) != 1:
        return None
    g, h = hits[0]
    cs = [c for k in getattr(h, "geoms", [h]) for c in getattr(k, "coords", [])]
    proj = lambda c: (c[0] - px) * nx + (c[1] - py) * ny  # noqa: E731
    outer = max(cs, key=proj)
    inner = min(cs, key=proj)

    def angle(pt):
        ring = min([g.exterior] + list(g.interiors), key=lambda r: r.distance(Point(pt)))
        d = ring.project(Point(pt))
        a, b = ring.interpolate(d - 0.8 * ppm), ring.interpolate(d + 0.8 * ppm)
        tx, ty = b.x - a.x, b.y - a.y
        m = math.hypot(tx, ty) or 1.0
        return math.degrees(math.acos(min(1.0, abs((tx * nx + ty * ny) / m))))

    ends = [(p0, sgn) for p0, sgn, nm in ((outer, 1, "outer"), (inner, -1, "inner")) if nm in need]
    if min(angle(p0) for p0, _ in ends) < 55:
        return None
    side = 1.8 * t
    for p0, sgn in ends:
        beyond = LineString([(p0[0] + sgn * nx * 0.5 * t, p0[1] + sgn * ny * 0.5 * t),
                             (p0[0] + sgn * nx * big, p0[1] + sgn * ny * big)]).buffer(side, cap_style=2)
        if not g.intersection(beyond).is_empty:
            return None
    return outer, inner, (px, py), (nx, ny)


def _ang(p, C):
    """Clockwise-from-top angle of p about C (y down), as R.out_vec uses."""
    return math.atan2(p[0] - C[0], C[1] - p[1])


def _at(C, phi, r):
    v = R.out_vec(phi)
    return (C[0] + v[0] * r, C[1] + v[1] * r)


def _ang_span(g, C):
    """(lo, hi) angular extent of polygon g about C, unwrapped around its
    centroid's angle (hi may exceed pi)."""
    a0 = _ang((g.centroid.x, g.centroid.y), C)
    rings = [p.exterior for p in getattr(g, "geoms", [g])]
    ds = [math.remainder(_ang(c, C) - a0, 2 * math.pi) for r in rings for c in r.coords]
    return a0 + min(ds), a0 + max(ds)


def _ring_radii(r0, ppm):
    """Concentric circles inside radius r0 (px): equal-width rings, then a
    core small enough for a few pie slices -- every ring and slice inside
    the oversized limits. Returns (ring radii outermost first incl. r0, core
    radius, slices in the core)."""
    short = 0.9 * R.OVERSIZE_SHORT_MM * ppm
    n = max(1, math.ceil(r0 / short))
    w = r0 / n
    radii = [r0 - i * w for i in range(n)]
    rc = r0 - (n - 1) * w
    k = 3
    while 2 * rc * math.sin(math.pi / k) > short:
        k += 1
    return radii, rc, k


def build_letter_first(words, seed, rp, cfg):
    """Concentric-ring edges around the letter-first layout:

      * circle c1 midway between the center words and the outer letters;
        circle c0 a little inside the center words; equal rings inside c0
        down to a core cut into curvy pie slices;
      * radial cuts: through each outer letter and through every gap between
        outer letters (c1 -> rim); through center-word letters square to
        their arc (c0 -> c1) and through the empty stretches of that band;
        evenly round each inner ring, lined up with the cuts landing on its
        outer circle so verticals continue across rings where they can.
    Each circle is split where cuts land so every stretch gets its own tab."""
    ppm = cfg.px_per_mm
    D = rp.diameter_mm * ppm
    m = cfg.margin_px
    C = (m + D / 2, m + D / 2)
    cfg = replace(cfg, panel_w_px_fit=int(D), panel_h_px_fit=int(D), panel_shape="disc")
    panel = Point(C).buffer(D / 2, quad_segs=96)
    t = rp.min_feature_mm * ppm
    center = list(rp.center_text) if rp.center_text else []
    L, world, cwords, clines, cpaths, ccap, curv = layout(words, center, rp, ppm, C, panel)
    L["center_cap_mm"] = round(ccap, 1) if ccap else None
    L["center_curve"] = curv
    L["r_h"] = 0.0
    center_polys = [g for ln in clines for g in ln]
    letter_union = unary_union(world + center_polys)
    outer_solids = [R.solid_of(g) for g in world]
    word_solids = [unary_union([R.solid_of(g) for g in ln]) for ln in clines]
    tab_solids = unary_union(outer_solids + word_solids)
    background = panel.difference(letter_union)
    col = 0.75 * rp.target_w_mm * ppm  # ~35mm between radial cuts
    band_max = 0.9 * R.OVERSIZE_SHORT_MM * ppm
    Pc = Point(C)

    # --- circles
    r_o_in = min(Pc.distance(g) for g in outer_solids)
    if word_solids:
        r_c_out = max(math.dist(C, c) for g in word_solids
                      for p in getattr(g, "geoms", [g]) for c in p.exterior.coords)
        r_c_in = min(Pc.distance(g) for g in word_solids)
        r1 = (r_c_out + r_o_in) / 2
        # c0: under the words by a tab's width where the band allows it
        r0 = max(r1 - band_max, r_c_in - (3 * max(R.TAB_SIZE_CLASSES) * t + t))
        r0 = min(r0, r_c_in - 2 * t)
    else:
        r1 = r_o_in - rp.group_gap_mm * ppm / 2
        r0 = r1
    inner_radii, rc, k_core = _ring_radii(r0, ppm)
    circles = ([r1] if word_solids else []) + inner_radii  # outermost first
    if os.environ.get("LF_DEBUG"):
        print("circles mm", [round(r / ppm, 1) for r in circles], "core", round(rc / ppm, 1),
              "slices", k_core, flush=True)

    best = None
    for variant in range(rp.variants):
        rng = random.Random(seed * 131 + variant * 9973)
        seams = []
        contacts = {i: [] for i in range(len(circles))}

        def land(ci, q):
            # a landing within 2t of an earlier one on the same circle shares
            # it (crossing): two contacts a hair apart leave a sliver of arc
            near = min(contacts[ci], key=lambda c: math.dist(c, q), default=None)
            if near is not None and math.dist(near, q) < 2 * t:
                return near
            contacts[ci].append(q)
            return q

        def radial(a, b, ends):
            pts = R._wobble([a, b], min(0.8 * t, math.dist(a, b) / 6), 0, 0, flat_ends=True)
            seams.append(dict(pts=pts, kind="lfcut", plain_ok=True, structural=True, ends=ends))

        rim = D / 2
        # --- outer ring: through each letter, and through each gap
        spans = []
        for g in outer_solids:
            lo, hi = _ang_span(g, C)
            spans.append((lo, hi, g))
            th = _ang((g.centroid.x, g.centroid.y), C)
            rho = Pc.distance(g.centroid)
            path = ("arc", C[0], C[1], rho, 1)
            # outward and inward cuts pick their angles separately (a heart's
            # notch spoils the straight line through it, its point doesn't)
            for side in ("outer", "inner"):
                goods = []
                phi = lo
                while phi < hi:
                    c_ = _column([g], path, phi * rho, t, ppm, need=(side,))
                    if c_ is not None:
                        goods.append((phi, c_))
                    phi += 0.5 * ppm / rho
                if goods:
                    phi, (p_out, p_in, _f, _n) = min(goods, key=lambda pc: abs(pc[0] - th))
                else:
                    # no clean crossing (a heart's point): leave from the
                    # letter's extreme point on that side -- it bisects a tip
                    cs = [c for p_ in getattr(g, "geoms", [g]) for c in p_.exterior.coords]
                    p_out = max(cs, key=lambda c: math.dist(c, C))
                    p_in = min(cs, key=lambda c: math.dist(c, C))
                if side == "outer":
                    radial(p_out, _at(C, _ang(p_out, C), rim), ("L", "B"))
                elif word_solids:
                    radial(p_in, land(0, _at(C, _ang(p_in, C), r1)), ("L", "J"))
        spans.sort(key=lambda s: s[0])
        for i, (lo, hi, _g) in enumerate(spans):
            lo2 = spans[(i + 1) % len(spans)][0] + (2 * math.pi if i + 1 == len(spans) else 0)
            mid = (hi + lo2) / 2
            a = land(0, _at(C, mid, r1)) if word_solids else _at(C, mid, r1)
            radial(a, _at(C, mid, rim), ("J" if word_solids else "B", "B"))

        # --- center band (c0..c1): columns through single letters
        band_angles = []
        for letters_w, path in zip(clines, cpaths):
            span = _path_span(letters_w, path)
            n_cols = max(1, round((span[1] - span[0]) / col) - 1)
            goods = []
            s_ = span[0]
            while s_ < span[1]:
                c_ = _column(letters_w, path, s_, t, ppm)
                if c_ is not None:
                    goods.append((s_, c_))
                s_ += 0.5 * ppm
            chosen = []
            for j in range(1, n_cols + 1):
                want = span[0] + (span[1] - span[0]) * j / (n_cols + 1)
                cand = [sc for sc in goods if all(abs(sc[0] - u) > 0.5 * col for u, _ in chosen)]
                if cand:
                    pick = min(cand, key=lambda sc: abs(sc[0] - want))
                    if abs(pick[0] - want) < 0.5 * col:
                        chosen.append(pick)
            for _s, (p_hi, p_lo, foot, _n) in chosen:
                far, near_ = sorted((p_hi, p_lo), key=lambda q: -math.dist(q, C))
                phi = _ang(foot, C)
                radial(far, land(0, _at(C, phi, r1)), ("L", "J"))
                radial(near_, land(1, _at(C, phi, r0)), ("L", "J"))
                band_angles.append(phi)
        if word_solids:
            # empty stretches of the band (the word ends): straight c0 -> c1
            # cuts clear of the letters, ~col apart
            words_u = unary_union(word_solids)
            rm = (r0 + r1) / 2
            angs = sorted(a % (2 * math.pi) for a in band_angles)
            if not angs:
                angs = [0.0]
            for i, a in enumerate(angs):
                b = angs[(i + 1) % len(angs)] + (2 * math.pi if i + 1 == len(angs) else 0)
                if len(angs) == 1:
                    b = a + 2 * math.pi
                n_add = round((b - a) * rm / col) - 1
                for j in range(1, n_add + 1):
                    want = a + (b - a) * j / (n_add + 1)
                    for d in sorted(range(-40, 41), key=abs):
                        phi = want + d * 0.5 * ppm / rm
                        if not (a < phi < b):
                            continue
                        seg = LineString([_at(C, phi, r0), _at(C, phi, r1)])
                        if seg.distance(words_u) >= 2 * t:
                            radial(land(1, _at(C, phi, r0)), land(0, _at(C, phi, r1)),
                                   ("J", "J"))
                            break

        # --- inner rings and the core's pie slices, lined up with the cuts
        # already landing on each ring's outer circle
        def aligned(ci, r, n):
            have = sorted(_ang(c, C) for c in contacts[ci])
            step = 2 * math.pi / n
            best_off, best_hit = rng.uniform(0, step), -1
            for off in have:
                hit = sum(1 for k_ in range(n)
                          if any(abs(math.remainder(off + k_ * step - h, 2 * math.pi)) < 0.25 * step
                                 for h in have))
                if hit > best_hit:
                    best_off, best_hit = off, hit
            out = []
            for k_ in range(n):
                a = best_off + k_ * step
                h = min(have, key=lambda h: abs(math.remainder(a - h, 2 * math.pi)), default=None)
                if h is not None and abs(math.remainder(a - h, 2 * math.pi)) < 0.25 * step:
                    a = h
                out.append(a)
            return out

        first_inner = 1 if word_solids else 0
        for ci in range(first_inner, len(circles) - 1):
            ro, ri = circles[ci], circles[ci + 1]
            n = max(3, round(2 * math.pi * (ro + ri) / 2 / col))
            for a in aligned(ci, ro, n):
                radial(land(ci, _at(C, a, ro)), land(ci + 1, _at(C, a, ri)), ("J", "J"))
        ci = len(circles) - 1
        twist = rng.choice((-1, 1))
        for a in aligned(ci, rc, k_core):
            b = land(ci, _at(C, a, rc))
            pts = R._pinwheel_spoke(C, _ang(b, C), b, twist, 50, rc)
            seams.append(dict(pts=pts, kind="lfcut", plain_ok=True, structural=True,
                              ends=("J", "J")))

        # --- circles, split at every landing so each stretch gets a tab
        for ci, r in enumerate(circles):
            angs = sorted({round(_ang(c, C) % (2 * math.pi), 6): c for c in contacts[ci]}.items())
            if not angs:
                continue
            for i, (a, pa) in enumerate(angs):
                b, pb = angs[(i + 1) % len(angs)]
                if i + 1 == len(angs):
                    b += 2 * math.pi
                if b - a < 1e-6:
                    continue
                nseg = max(4, int((b - a) * r / (2 * ppm)))
                pts = [pa] + [_at(C, a + (b - a) * j / nseg, r) for j in range(1, nseg)] + [pb]
                pts = R._wobble(pts, min(0.6 * t, LineString(pts).length / 8), 0, 0,
                                flat_ends=True)
                seams.append(dict(pts=pts, kind="lfedge", plain_ok=True, structural=True,
                                  ends=("J", "J")))

        with R._oriented_oversized():
            surround, counters, st = R.assemble_qa(
                seams, letter_union, tab_solids, background, panel, cfg, C,
                distinct_tabs=rp.distinct_tabs)
        sc = (len(st["defects"]),) + R.score(surround, panel, cfg) + (st["dropped"],)
        if best is None or sc < best[0]:
            best = (sc, surround, counters, st, seams)
    sc, surround, counters, st, seams = best
    st["score"] = sc
    st["seams"] = seams
    st["center_medallion"] = None
    pieces = {(k, 0): p for k, p in enumerate(surround + counters)}
    return pieces, letter_union, cfg, L, st, panel, C
