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


CURVES = (0.0, 2.0, 1.2, 0.8)  # straight, then arcs (radius / line length)


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
            cand = []
            for curv in CURVES:
                lo, hi = 3.0, 60.0
                for _ in range(12):  # largest center cap that fits
                    mid = (lo + hi) / 2
                    wu, _l, _p = _center_block(ref[0], ref[1], mid, word_gap, C, ppm, t_px, curv)
                    if allowed.contains(unary_union(wu)):
                        lo = mid
                    else:
                        hi = mid
                if lo < rp.center_text_cap_min_mm:
                    continue
                cw, cl, cp = _center_block(ref[0], ref[1], lo, word_gap, C, ppm, t_px, curv)
                area = area_o + sum(g.area for ln in cl for g in ln)
                cand.append((area, L, world, cw, cl, cp, lo, curv))
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


def _column(letters, path, s_, t, ppm):
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

    if min(angle(outer), angle(inner)) < 55:
        return None
    side = 1.8 * t
    for p0, sgn in ((outer, 1), (inner, -1)):
        beyond = LineString([(p0[0] + sgn * nx * 0.5 * t, p0[1] + sgn * ny * 0.5 * t),
                             (p0[0] + sgn * nx * big, p0[1] + sgn * ny * big)]).buffer(side, cap_style=2)
        if not g.intersection(beyond).is_empty:
            return None
    return outer, inner, (px, py), (nx, ny)


def build_letter_first(words, seed, rp, cfg):
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
    sites = outer_solids + [unary_union([R.solid_of(g) for g in ln]) for ln in clines]
    site_letters = [[g] for g in outer_solids] + [[R.solid_of(g) for g in ln] for ln in clines]
    tab_solids = unary_union(sites)
    background = panel.difference(letter_union)
    regions = voronoi_regions(sites, panel, ppm)
    # medial edges: every region boundary that isn't the disc's own edge
    edges = unary_union([r.boundary for r in regions]).difference(panel.exterior.buffer(0.5))
    edges = linemerge([g for g in getattr(edges, "geoms", [edges]) if g.geom_type == "LineString"])
    edges = [g for g in getattr(edges, "geoms", [edges]) if g.length > 2 * ppm]
    edges = [g.simplify(0.4 * ppm) for g in edges]
    edge_net = unary_union(edges + [panel.exterior])
    target = 0.45 * (rp.target_w_mm * ppm) ** 2
    col = 0.75 * rp.target_w_mm * ppm  # ~35mm between center-word cuts

    def curved(pts):
        if G_deflect(pts) < 2.5 * ppm:
            pts = R.G._vg_bow(pts, tab_solids, cfg)
        return pts

    def G_deflect(pts):
        return R.G._vg_deflection(pts)

    best = None
    for variant in range(rp.variants):
        rng = random.Random(seed * 131 + variant * 9973)
        seams = []
        contacts = []  # points where cuts land on medial edges
        n_outer = len(outer_solids)
        for si, (site, reg) in enumerate(zip(sites, regions)):
            if si >= n_outer:
                continue  # center words: letter-aligned cuts below
            wood = reg.difference(site)
            n = max(2, round(wood.area / target))
            cx, cy = site.centroid.x, site.centroid.y
            phi0 = math.atan2(cy - C[1], cx - C[0]) + math.radians(rng.uniform(-12, 12))
            far = 2 * D
            for k_ in range(n):
                phi = phi0 + 2 * math.pi * k_ / n
                ray = LineString([(cx, cy), (cx + far * math.cos(phi), cy + far * math.sin(phi))])
                hit = ray.intersection(site)
                cs = [c for g in getattr(hit, "geoms", [hit]) for c in getattr(g, "coords", [])]
                start = max(cs, key=lambda c: math.dist(c, (cx, cy))) if cs else \
                    (lambda q: (q.x, q.y))(nearest_points(site, Point(cx, cy))[0])
                end, on_rim = _ray_to(start, (math.cos(phi), math.sin(phi)), reg, panel, t, D)
                if end is None:
                    continue
                pts = R._wobble([start, end], min(0.8 * t, math.dist(start, end) / 6), 0, 0,
                                flat_ends=True)
                seams.append(dict(pts=pts, kind="lfcut", plain_ok=True, structural=True,
                                  ends=("L", "B" if on_rim else "J")))
                if not on_rim:
                    contacts.append(end)
        # center words: cuts square to the word's line through single letters
        # -- outward from the letter to its region's edge, and inward from
        # the letter's far side to the edge between the two words. Bottom-word
        # cuts meet that edge at the top word's junction or keep 4t clear.
        mid_s = []
        for wi, (letters_w, path) in enumerate(zip(clines, cpaths)):
            si = n_outer + wi
            reg = regions[si]
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
                cand = [(s_, c_) for s_, c_ in goods
                        if all(abs(s_ - u) > 0.5 * col for u, _ in chosen)
                        and all(abs(c_[2][0] - m_[0]) < 1.0 * ppm or abs(c_[2][0] - m_[0]) >= 4 * t
                                for m_ in mid_s)]
                if cand:
                    pick = min(cand, key=lambda sc: abs(sc[0] - want))
                    # prefer landing on the other word's junction outright
                    shared = [sc for sc in cand if any(abs(sc[1][2][0] - m_[0]) < 1.0 * ppm
                                                       for m_ in mid_s)
                              and abs(sc[0] - want) < 0.5 * col]
                    if shared:
                        pick = min(shared, key=lambda sc: abs(sc[0] - want))
                    if abs(pick[0] - want) < 0.5 * col:
                        chosen.append(pick)
            for s_, (outer_pt, inner_pt, _foot, n_) in chosen:
                end, on_rim = _ray_to(outer_pt, n_, reg, panel, t, D)
                if end is not None:
                    pts = R._wobble([outer_pt, end], min(0.8 * t, math.dist(outer_pt, end) / 6),
                                    0, 0, flat_ends=True)
                    seams.append(dict(pts=pts, kind="lfcut", plain_ok=True, structural=True,
                                      ends=("L", "B" if on_rim else "J")))
                    if not on_rim:
                        contacts.append(end)
                if len(clines) > 1:
                    inward = (-n_[0], -n_[1])
                    end2, on_rim2 = _ray_to(inner_pt, inward, reg, panel, t, D)
                    if end2 is not None:
                        snap = min(mid_s, key=lambda m_: math.dist(m_, end2), default=None)
                        if snap is not None and math.dist(snap, end2) < 2 * t:
                            end2 = snap
                        pts = R._wobble([inner_pt, end2], min(0.8 * t, math.dist(inner_pt, end2) / 6),
                                        0, 0, flat_ends=True)
                        seams.append(dict(pts=pts, kind="lfcut", plain_ok=True, structural=True,
                                          ends=("L", "J")))
                        contacts.append(end2)
                        mid_s.append(end2)
        # medial edges split at every cut landing so each stretch gets a tab
        for e in edges:
            cuts = sorted({round(e.project(Point(q)), 3) for q in contacts
                           if e.distance(Point(q)) < 1.0 and 0.5 < e.project(Point(q)) < e.length - 0.5})
            stops = [0.0] + cuts + [e.length]
            for k_ in range(len(stops) - 1):
                a, b = stops[k_], stops[k_ + 1]
                if b - a < 0.5:
                    continue
                nseg = max(2, int((b - a) / (2 * ppm)))
                seg = [e.interpolate(a + (b - a) * i / nseg) for i in range(nseg + 1)]
                seg = [(q.x, q.y) for q in seg]
                if k_ > 0:
                    seg[0] = min(contacts, key=lambda c: math.dist(c, seg[0]))
                if k_ < len(stops) - 2:
                    seg[-1] = min(contacts, key=lambda c: math.dist(c, seg[-1]))
                ends = []
                for q in (seg[0], seg[-1]):
                    ends.append("B" if panel.exterior.distance(Point(q)) < 1.0 else "J")
                if ends == ["J", "J"] and len(seg) > 2:
                    seg = curved(seg)
                seams.append(dict(pts=seg, kind="lfedge", plain_ok=True, structural=True,
                                  ends=tuple(ends)))
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
