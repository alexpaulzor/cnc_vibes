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
    t_px = rp.min_feature_mm * ppm
    out = []
    for w in words:
        glyphs, xs, total = R._word_advances(w, font, 0.0)
        out.append((glyphs, [x for x in xs]))
    return out, ref_cap_mm, t_px


def _center_block(ref, ref_cap, cap_mm, gap_px, C, ppm, t_px):
    """Both center lines at cap_mm, letters at least t apart within a word,
    the lines gap_px apart (real glyph distance), centred on C. Returns
    (list of word unions, list of letter polygons per word)."""
    k = cap_mm / ref_cap
    lines = []
    for glyphs, xs in ref:
        gs = [affinity.scale(g, k, k, origin=(0, 0)) for g in glyphs]
        pos = [x * k for x in xs]
        # widen tracking until neighbouring letters keep t of wood
        extra = 0.0
        for _ in range(40):
            placed = [affinity.translate(g, p + extra * (i - (len(gs) - 1) / 2), 0)
                      for i, (g, p) in enumerate(zip(gs, pos))]
            gaps = [placed[i].distance(placed[i + 1]) for i in range(len(placed) - 1)]
            if not gaps or min(gaps) >= t_px:
                break
            extra += 0.25 * ppm
        lines.append(placed)
    if len(lines) == 1:
        u = unary_union(lines[0])
        dx, dy = C[0] - u.centroid.x, C[1] - (u.bounds[1] + u.bounds[3]) / 2
        lines = [[affinity.translate(g, dx, dy) for g in lines[0]]]
    else:
        u1, u2 = unary_union(lines[0]), unary_union(lines[1])
        # put line 2 below line 1 with exactly gap_px of clearance
        dy = (u1.bounds[3] - u2.bounds[1]) + gap_px
        for _ in range(6):
            d = u1.distance(affinity.translate(u2, 0, dy))
            dy += gap_px - d
        lines[1] = [affinity.translate(g, 0, dy) for g in lines[1]]
        both = unary_union(lines[0] + lines[1])
        cx = (both.bounds[0] + both.bounds[2]) / 2
        cy = (both.bounds[1] + both.bounds[3]) / 2
        lines = [[affinity.translate(g, C[0] - cx, C[1] - cy) for g in ln] for ln in lines]
    return [unary_union(ln) for ln in lines], lines


def layout(words, center, rp, ppm, C, panel):
    """Pick the outer cap and center cap (maximising total letter area)."""
    gap = rp.group_gap_mm * ppm
    t_px = rp.min_feature_mm * ppm
    ref = _ref_glyphs(rp, ppm, center) if center else None
    best = None
    h = rp.cap_h_max_mm
    while h >= rp.cap_h_min_mm:
        rpo = replace(rp, cap_h_max_mm=h, cap_h_min_mm=h, rim_mm=rp.group_gap_mm,
                      min_gap_mm=rp.group_gap_mm, hub_r_min_mm=1.0, hub_ring_min_mm=0.0,
                      center_text=None)
        L = R.fit_ring(words, rpo, ppm)
        h -= 1.0
        if not L["ok"]:
            continue
        Cl = (0.0, 0.0)
        world = [R.place(g, th, L["Rin"], Cl) for g, th in zip(L["locs"], L["ths"])]
        world = [affinity.translate(g, C[0], C[1]) for g in world]
        outer = unary_union([R.solid_of(g) for g in world])
        area_o = sum(g.area for g in world)
        cw, cl, ccap = [], [], None
        if center:
            allowed = panel.difference(outer.buffer(gap))
            allowed = next((g for g in getattr(allowed, "geoms", [allowed])
                            if g.contains(Point(C))), None)
            if allowed is None:
                continue
            lo, hi = 3.0, 60.0
            for _ in range(14):  # binary search the largest center cap that fits
                mid = (lo + hi) / 2
                wu, _ln = _center_block(ref[0], ref[1], mid, gap, C, ppm, t_px)
                if allowed.contains(unary_union(wu)):
                    lo = mid
                else:
                    hi = mid
            ccap = lo
            cw, cl = _center_block(ref[0], ref[1], ccap, gap, C, ppm, t_px)
            if ccap < rp.center_text_cap_min_mm:
                continue
        area = area_o + sum(g.area for ln in cl for g in ln)
        if best is None or area > best[0]:
            best = (area, L, world, cw, cl, ccap)
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


def build_letter_first(words, seed, rp, cfg):
    ppm = cfg.px_per_mm
    D = rp.diameter_mm * ppm
    m = cfg.margin_px
    C = (m + D / 2, m + D / 2)
    cfg = replace(cfg, panel_w_px_fit=int(D), panel_h_px_fit=int(D), panel_shape="disc")
    panel = Point(C).buffer(D / 2, quad_segs=96)
    t = rp.min_feature_mm * ppm
    center = list(rp.center_text) if rp.center_text else []
    L, world, cwords, clines, ccap = layout(words, center, rp, ppm, C, panel)
    L["center_cap_mm"] = round(ccap, 1) if ccap else None
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
        for si, (site, reg) in enumerate(zip(sites, regions)):
            wood = reg.difference(site)
            n = max(2, round(wood.area / target))
            cx, cy = site.centroid.x, site.centroid.y
            if si < len(outer_solids):
                phi0 = math.atan2(cy - C[1], cx - C[0])  # radially outward
            else:
                phi0 = -math.pi / 2  # up
            phi0 += math.radians(rng.uniform(-12, 12))
            far = 2 * D
            for k_ in range(n):
                phi = phi0 + 2 * math.pi * k_ / n
                ray = LineString([(cx, cy), (cx + far * math.cos(phi), cy + far * math.sin(phi))])
                # start where the ray finally leaves the letters of this site
                hit = ray.intersection(unary_union(site_letters[si]))
                cs = [c for g in getattr(hit, "geoms", [hit]) for c in getattr(g, "coords", [])]
                if not cs:
                    near = nearest_points(site, Point(cx, cy))[0]
                    start = (near.x, near.y)
                else:
                    start = max(cs, key=lambda c: math.dist(c, (cx, cy)))
                rest = LineString([start, ray.coords[1]])
                hit2 = rest.intersection(reg.exterior)
                cs2 = [c for g in getattr(hit2, "geoms", [hit2]) for c in getattr(g, "coords", [])]
                cs2 = [c for c in cs2 if math.dist(c, start) > 2 * t]
                if not cs2:
                    continue
                end = min(cs2, key=lambda c: math.dist(c, start))
                on_rim = panel.exterior.distance(Point(end)) < 1.0
                pts = [start, end]
                pts = R._wobble(pts, min(0.8 * t, math.dist(start, end) / 6), 0, 0, flat_ends=True)
                seams.append(dict(pts=pts, kind="lfcut", plain_ok=True, structural=True,
                                  ends=("L", "B" if on_rim else "J")))
                if not on_rim:
                    contacts.append(end)
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
