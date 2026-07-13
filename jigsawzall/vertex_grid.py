#!/usr/bin/env python3
"""Vertex-grid tiling for name-banner puzzles (opt-in, `--vertex-grid`).

Letters are cut out as their own pieces; the background around them is tiled into
interlocking pieces that ENCASE each letter. Cardinal rules:
  * no seam ever crosses a letter or a counter,
  * seam edges run vertex-to-vertex (Delaunay over letter convex vertices),
  * one tab per edge (flip / shrink-to-circle / skip),
  * durability first: no material bridge < WALL_MM anywhere,
  * size consistency next (region-grow to a target piece size).

Pipeline: render letters -> outer + counter contours -> Delaunay over convex
vertices + border ring -> keep background triangles -> BFS region-grow into
~uniform pieces -> de-spike + coverage-fill + targeted durability merge ->
one tab per shared edge. Counters become their own cut-out pieces.

Geometry is returned as shapely polygons in pixel space with `px_per_mm` so
callers can scale to mm (GCode emission is a future step; this module currently
powers the preview).
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import shapely.geometry as sg
from PIL import Image, ImageDraw, ImageFont
from scipy.spatial import Delaunay
from shapely.ops import unary_union

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


@dataclass
class VGParams:
    px_per_mm: float = 6.0
    gap_mm: float = 22.0
    margin_mm: float = 18.0
    wall_mm: float = 4.0
    node_min_mm: float = 11.0
    piece_mm: float = 30.0
    min_side_mm: float = 15.0
    tab_depth_mm: float = 5.0
    tab_width_mm: float = 8.0
    font_size_mm: float = 44.0


@dataclass
class VGResult:
    pieces: list  # background pieces (shapely Polygons, px space)
    letters: list  # letter ring cut-outs (shapely Polygons)
    counters: list  # counter cut-outs (shapely Polygons)
    outer_contours: list  # raw cv2 contours for letters (px)
    counter_contours: list
    tabs: tuple  # (full, circle, skipped)
    durable: bool
    gap_mm: float
    px_per_mm: float
    w_px: int
    h_px: int
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


def _decimate(pts, mind):
    out = []
    for p in pts:
        if all((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 > mind * mind for q in out):
            out.append(p)
    return out


def _polys(g):
    if g.is_empty:
        return []
    if g.geom_type == "Polygon":
        return [g]
    return [
        p for p in getattr(g, "geoms", []) if p.geom_type == "Polygon" and p.area > 1
    ]


def _render_letters(word, prm):
    """Raster the word (Arial Black) with uniform gaps; return (mask_arr, W, H)."""
    ppm = prm.px_per_mm
    gap = prm.gap_mm * ppm
    margin = prm.margin_mm * ppm
    font = _load_font(int(round(prm.font_size_mm * ppm)))
    glyphs = []
    for ch in word:
        bb = ImageDraw.Draw(Image.new("L", (10, 10))).textbbox((0, 0), ch, font=font)
        g = Image.new("L", (max(1, bb[2] - bb[0]), max(1, bb[3] - bb[1])), 0)
        ImageDraw.Draw(g).text((-bb[0], -bb[1]), ch, font=font, fill=255)
        glyphs.append(g)
    gh = max(g.height for g in glyphs)
    W = int(sum(g.width for g in glyphs) + gap * (len(glyphs) - 1) + 2 * margin)
    Hh = int(gh + 2 * margin)
    mask = Image.new("L", (W, Hh), 0)
    x = margin
    for g in glyphs:
        mask.paste(g, (int(x), int(margin + (gh - g.height) / 2)))
        x += g.width + gap
    return np.array(mask), W, Hh


def _build_one(word, seed, prm):
    """Build the tiling for a single gap value. Returns a VGResult."""
    rng = np.random.default_rng(seed)
    ppm = prm.px_per_mm
    wall = prm.wall_mm * ppm
    margin = prm.margin_mm * ppm
    arr, W, Hh = _render_letters(word, prm)

    cont, hier = cv2.findContours(
        np.where(arr > 127, 255, 0).astype(np.uint8),
        cv2.RETR_CCOMP,
        cv2.CHAIN_APPROX_NONE,
    )
    outer, counters = [], []
    if hier is not None:
        for c, h in zip(cont, hier[0]):
            if cv2.contourArea(c) < 200:
                continue
            (outer if h[3] < 0 else counters).append(c)
    outer = sorted(outer, key=lambda c: cv2.boundingRect(c)[0])
    letters_solid = unary_union([sg.Polygon(c.reshape(-1, 2)).buffer(0) for c in outer])
    counter_polys = [sg.Polygon(c.reshape(-1, 2)).buffer(0) for c in counters]
    counters_u = unary_union(counter_polys) if counter_polys else sg.Point(-9, -9)
    letters_ring = letters_solid.difference(counters_u)
    plaque = sg.box(0, 0, W, Hh)
    background = plaque.difference(letters_solid)

    # nodes: decimated convex vertices + border ring
    nodes = []
    for c in outer:
        nodes += _convex_anchors(c)
    nodes = _decimate(nodes, prm.node_min_mm * ppm)
    ring = []
    st = prm.node_min_mm * 1.6 * ppm
    ring += [(xx, 0.0) for xx in np.arange(0, W + 1, st)]
    ring += [(xx, float(Hh)) for xx in np.arange(0, W + 1, st)]
    ring += [(0.0, yy) for yy in np.arange(0, Hh + 1, st)]
    ring += [(float(W), yy) for yy in np.arange(0, Hh + 1, st)]
    # coarse background lattice -> regularizes the triangulation into blockier,
    # more grid-like pieces (less arbitrary angles). Only points clear of letters.
    lattice = []
    gstep = prm.piece_mm * ppm
    keepout = letters_solid.buffer(prm.wall_mm * ppm)
    gx0 = margin + gstep / 2
    for lx in np.arange(gx0, W - margin, gstep):
        for ly in np.arange(margin + gstep / 2, Hh - margin, gstep):
            pt = sg.Point(float(lx), float(ly))
            if background.contains(pt) and not keepout.contains(pt):
                lattice.append((float(lx), float(ly)))
    pts = np.array(
        nodes + ring + lattice + [(0, 0), (W, 0), (0, Hh), (W, Hh)], dtype=float
    )

    tri = Delaunay(pts)
    simp, neigh = tri.simplices, tri.neighbors
    tri_poly = [sg.Polygon(pts[s]).buffer(0) for s in simp]
    tri_area = np.array([tp.area for tp in tri_poly])
    is_bg = np.array(
        [
            (tp.area > 2)
            and background.contains(tp.representative_point())
            and tp.intersection(letters_ring).area < 0.25 * tp.area
            for tp in tri_poly
        ]
    )

    # BFS region-grow into ~uniform pieces
    target = (prm.piece_mm * ppm) ** 2
    assigned = -np.ones(len(simp), dtype=int)
    pid = 0
    order = sorted(
        [i for i in range(len(simp)) if is_bg[i]],
        key=lambda i: (pts[simp[i]][:, 1].mean(), pts[simp[i]][:, 0].mean()),
    )
    for s in order:
        if assigned[s] >= 0:
            continue
        area, q = 0.0, deque([s])
        assigned[s] = pid
        while q and area < target:
            cur = q.popleft()
            area += tri_area[cur]
            for nb in neigh[cur]:
                if nb != -1 and is_bg[nb] and assigned[nb] < 0:
                    assigned[nb] = pid
                    q.append(nb)
        pid += 1
    pieces = []
    for k in range(pid):
        members = [tri_poly[i] for i in range(len(simp)) if assigned[i] == k]
        if members:
            pieces.extend(_polys(unary_union(members)))

    def thin(g):
        er = g.buffer(-wall / 2)
        return (
            er.is_empty
            or er.area < 0.5 * g.area
            or min(g.bounds[2] - g.bounds[0], g.bounds[3] - g.bounds[1])
            < prm.min_side_mm * ppm
        )

    def despike(pcs):
        changed = True
        while changed:
            changed = False
            pcs.sort(key=lambda g: g.area)
            for i, a in enumerate(pcs):
                if not thin(a):
                    continue
                best = None
                for j, b in enumerate(pcs):
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
                    pcs[best[0]] = best[2]
                    pcs.pop(i)
                    changed = True
                    break
        return pcs

    pieces = despike(pieces)

    # coverage fill: absorb uncovered background into the longest-edge neighbor
    for _ in range(3):
        covered = unary_union(pieces) if pieces else sg.Point(-9, -9)
        frags = [f for f in _polys(background.difference(covered)) if f.area > 1.0]
        if not frags:
            break
        for frag in frags:
            best = None
            for j, b in enumerate(pieces):
                sh = frag.intersection(b)
                L = sh.length if "Line" in sh.geom_type else 0
                if L > 0 and (best is None or L > best[1]):
                    best = (j, L)
            if best:
                u = unary_union([pieces[best[0]], frag])
                pieces[best[0]] = (
                    u
                    if u.geom_type == "Polygon"
                    else max(_polys(u), key=lambda p: p.area)
                )
            else:
                pieces.append(frag)
    pieces = [
        pc
        for p in pieces
        for pc in _polys(p.intersection(background))
        if pc.area > (6 * ppm) ** 2
    ]
    pieces = despike(pieces)

    def durable(g):
        er = g.buffer(-wall / 2)
        return (not er.is_empty) and len(_polys(er)) == 1

    # targeted durability merge (durable-only unions -> no cascade)
    progress, guard = True, 0
    while progress and guard < 200:
        guard += 1
        progress = False
        for i, g in enumerate(pieces):
            if durable(g):
                continue
            best = None
            for j, b in enumerate(pieces):
                if i == j:
                    continue
                sh = g.intersection(b)
                L = sh.length if "Line" in sh.geom_type else 0
                if L <= 0:
                    continue
                u = unary_union([g, b])
                if (
                    u.geom_type == "Polygon"
                    and durable(u)
                    and (best is None or L > best[1])
                ):
                    best = (j, L, u)
            if best:
                pieces[best[0]] = best[2]
                pieces.pop(i)
                progress = True
                break
    min_ok = all(durable(g) for g in pieces)

    # ONE tab per shared edge
    tally = [0, 0, 0]

    def add_tab(i, j, edge):
        # scan several positions along the edge so a fitting spot is almost always
        # found (one tab per edge, but never omit it — esp. letter-adjacent edges).
        for frac in (0.5, 0.4, 0.6, 0.32, 0.68, 0.25, 0.75):
            mid = edge.interpolate(frac, normalized=True)
            a = edge.interpolate(max(0.0, frac - 0.06), normalized=True)
            b = edge.interpolate(min(1.0, frac + 0.06), normalized=True)
            t = np.array([b.x - a.x, b.y - a.y])
            t = t / (np.hypot(*t) + 1e-9)
            nx, ny = -t[1], t[0]
            pt = (mid.x, mid.y)
            for dep, wid, circ in (
                (prm.tab_depth_mm * ppm, prm.tab_width_mm * ppm, False),
                (prm.tab_width_mm * ppm / 2, prm.tab_width_mm * ppm / 2, True),
            ):
                for d in (1, -1) if rng.random() < 0.5 else (-1, 1):
                    tip = (pt[0] + nx * dep * d, pt[1] + ny * dep * d)
                    if circ:
                        tab = sg.Point(pt).buffer(dep, quad_segs=16)
                    else:
                        tab = (
                            sg.LineString([pt, tip])
                            .buffer(wid / 2, cap_style=2)
                            .union(sg.Point(tip).buffer(wid / 2, quad_segs=16))
                        )
                    if tab.distance(letters_ring) < wall or (
                        not counters_u.is_empty and tab.distance(counters_u) < wall
                    ):
                        continue
                    tp = sg.Point(tip)
                    donor, recv = (
                        (j, i)
                        if pieces[j].distance(tp) <= pieces[i].distance(tp)
                        else (i, j)
                    )
                    pieces[recv] = unary_union([pieces[recv], tab])
                    pieces[donor] = pieces[donor].difference(tab)
                    tally[0 if not circ else 1] += 1
                    return True
        tally[2] += 1
        return False

    # tab every shared edge; letter-adjacent edges first (must never be omitted)
    letters_bnd = letters_ring.boundary
    edges = []
    for i in range(len(pieces)):
        for j in range(i + 1, len(pieces)):
            sh = pieces[i].intersection(pieces[j])
            if sh.geom_type == "MultiLineString":
                sh = max(sh.geoms, key=lambda s: s.length)
            if sh.geom_type != "LineString" or sh.length < prm.tab_width_mm * ppm * 1.2:
                continue
            touches_letter = sh.distance(letters_bnd) < wall
            edges.append((0 if touches_letter else 1, i, j, sh))
    edges.sort(key=lambda e: e[0])  # letter-adjacent edges get first pick of room
    for _, i, j, sh in edges:
        add_tab(i, j, sh)

    letter_polys = _polys(letters_ring)
    return VGResult(
        pieces=pieces,
        letters=letter_polys,
        counters=counter_polys,
        outer_contours=outer,
        counter_contours=counters,
        tabs=tuple(tally),
        durable=min_ok,
        gap_mm=prm.gap_mm,
        px_per_mm=ppm,
        w_px=W,
        h_px=Hh,
    )


def build(word, seed=7, params=None, auto_gap=True):
    """Build a vertex-grid puzzle. If auto_gap, widen the gap (up to +12mm) until
    a durable result is found (crowded words need more room)."""
    word = word.upper()
    prm = params or VGParams()
    tried = []
    gaps = (
        [prm.gap_mm, prm.gap_mm + 4, prm.gap_mm + 8, prm.gap_mm + 12]
        if auto_gap
        else [prm.gap_mm]
    )
    for g in gaps:
        p = VGParams(**{**prm.__dict__, "gap_mm": g})
        res = _build_one(word, seed, p)
        tried.append(res)
        if res.durable:
            res.meta["gap_tries"] = [t.gap_mm for t in tried]
            return res
    best = max(tried, key=lambda r: sum(1 for pc in r.pieces))  # fall back to last/most
    best.meta["gap_tries"] = [t.gap_mm for t in tried]
    best.meta["durable_failed"] = True
    return best


def render_preview(res: VGResult, out_path: Path, title=None):
    """Render a colored preview PNG (pieces + cut-out letters + counters)."""
    img = Image.new("RGB", (res.w_px, res.h_px), "white")
    d = ImageDraw.Draw(img, "RGBA")
    for i, c in enumerate(res.pieces):
        for poly in _polys(c):
            d.polygon(
                list(poly.exterior.coords),
                fill=_PAL[i % len(_PAL)] + (180,),
                outline=(50, 50, 50),
            )
    for c in res.outer_contours:
        d.polygon(
            [tuple(map(int, q)) for q in c.reshape(-1, 2)],
            fill=(158, 46, 46, 235),
            outline=(90, 20, 20),
        )
    for c in res.counter_contours:
        d.polygon(
            [tuple(map(int, q)) for q in c.reshape(-1, 2)],
            fill=(250, 250, 250, 255),
            outline=(120, 90, 90),
        )
    if title:
        d.text(
            (10, 6), title, fill=(0, 0, 0), font=_load_font(int(3.2 * res.px_per_mm))
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
    ap.add_argument("--gap-mm", type=float, default=22.0)
    ap.add_argument("--px-per-mm", type=float, default=6.0)
    ap.add_argument("--no-auto-gap", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    prm = VGParams(px_per_mm=args.px_per_mm, gap_mm=args.gap_mm)
    res = build(args.word, args.seed, prm, auto_gap=not args.no_auto_gap)
    out = args.out or (
        Path(__file__).resolve().parent
        / "build"
        / f"vertex_grid_{args.word.lower()}_seed{args.seed}.png"
    )
    title = (
        f"{args.word} seed{args.seed}: {len(res.pieces)} pieces + "
        f"{len(res.letters)} letters + {len(res.counters)} counters | "
        f"1 tab/edge {res.tabs[0]}f/{res.tabs[1]}c/{res.tabs[2]}skip | "
        f"durable={'YES' if res.durable else 'NO'} | "
        f"{res.w_mm:.0f}x{res.h_mm:.0f}mm gap={res.gap_mm:.0f}"
    )
    render_preview(res, out, title)
    print(
        f"{args.word} seed{args.seed}: pieces {len(res.pieces)} letters "
        f"{len(res.letters)} counters {len(res.counters)} tabs {res.tabs} "
        f"durable {res.durable} gap {res.gap_mm:.0f}mm"
    )
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
