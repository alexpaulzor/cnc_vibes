#!/usr/bin/env python3
"""REFERENCE PROTOTYPE (not wired into jigsaw.py): circular "ring" name puzzle.

Letters sit upright on a ring (tops outward, reading clockwise from 12 o'clock),
and the background is tiled by a POLAR wave-grid: radial caps off each letter
to the rim and hub, ring seams between neighbouring letters, rim / mid / hub
subdivisions that T-junction onto those seams, a hub circle split into arcs,
and a hub disc split into pinwheel spokes. Reuses geometry.py's curve, tab,
splice and validity machinery unchanged.

See RING_SPEC.md for the algorithm and the plan for productionizing this into
geometry.py / jigsaw.py. Render a sketch:

    PYTHONPATH=. python3 scripts/ring_prototype.py JONATHAN
    PYTHONPATH=. python3 scripts/ring_prototype.py JONATHAN --shape square --debug
"""

import argparse
import contextlib
import math
import random
import sys
import warnings
from dataclasses import dataclass, replace
from pathlib import Path

warnings.simplefilter("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shapely  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from shapely import affinity  # noqa: E402
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box  # noqa: E402
from shapely.ops import nearest_points, polygonize, unary_union  # noqa: E402

import geometry as G  # noqa: E402


@dataclass
class RingParams:
    diameter_mm: float = 300.0  # disc diameter (or square side)
    shape: str = "disc"  # "disc" | "square"
    rim_mm: float = 24.0  # letter tops -> rim (== banner_margin_mm)
    corner_ring_mm: float = 20.0  # square only: corner arc radius = inscribed r + this
    cap_h_max_mm: float = 46.0  # == banner_letter_h_mm (upper bound)
    cap_h_min_mm: float = 18.0
    min_gap_mm: float = 14.0  # tab fit (tab_height + 2R = 11) + letter_gap_extra 3
    hub_arc_mm: float = 30.0  # min hub-circle arc per hub-ring piece
    hub_ring_min_mm: float = 26.0  # min radial thickness of the hub ring
    hub_r_min_mm: float = 22.0
    target_w_mm: float = 46.0  # target arc width of rim / hub-ring pieces
    hub_piece_mm: float = 62.0  # max chord of a hub-disc wedge
    rows: int = 3  # rings of background across the letter band (2 or 3)
    levels3: tuple = (
        0.18,
        0.82,
    )  # rows=3 ring-seam heights (frac of cap h), banner-equivalent
    ornament: str | None = "heart"  # None | "dot" | "heart" | "star"
    variants: int = 12
    font: str | None = None  # geometry.find_font path/alias; None = repo default
    letter_round_mm: float = 0.0  # fillet glyph corners (inside + out), like --letter-round-mm
    outline_smooth_px: float = 1.2
    # Tab / clearance sizing (defaults = the name-plate tabs). Small discs
    # (~140mm, 4-up on a 300mm panel) need scaled-down tabs: every seam must
    # hold tab_len + 2 * clearance, and 140mm-class seams are only 13-20mm long.
    tab_r_px: int = 15
    tab_stem_px: float = 30.0
    border_floor_mm: float = 7.0
    letter_clearance_mm: float = 4.0
    # Solid outer frame: a continuous annulus this wide (mm) around the puzzle,
    # never cut radially. Rim seams stop on its inner circle (T-junctions) and the
    # circle itself is split into a few tabbed arcs so the pieces lock into it.
    # 0 = no frame (rim pieces run to the panel edge).
    frame_mm: float = 0.0
    frame_arcs: int = 4
    # Keep letter caps/bridges as plain (untabbed) cuts when no tab fits,
    # instead of dropping them and merging their pieces. Needed with a frame:
    # letter-top -> frame caps are too short for any tab.
    plain_caps: bool = False  # Douglas-Peucker on glyph outlines (px) to kill pixel stairs


# --------------------------------------------------------------------------
# glyph rendering in a LOCAL frame: x = 0 at ink centre, y = 0 at baseline,
# y DOWN (image convention, so geometry.py's letter helpers work unchanged).
# --------------------------------------------------------------------------


def glyph_local(ch, font):
    gl, gt, gr, gb = font.getbbox(ch)
    pad = 6
    w, h = int(gr - gl) + 2 * pad, int(gb - gt) + 2 * pad
    img = Image.new("L", (w, h), 0)
    ImageDraw.Draw(img).text((-gl + pad, -gt + pad), ch, fill=255, font=font)
    poly = G._trace_mask_polygons(img)
    ascent = font.getmetrics()[0]
    base_y = ascent - gt + pad
    cx = (gl + gr) / 2 - gl + pad
    return affinity.translate(poly, -cx, -base_y)


def ornament_local(kind, cap_h):
    """Separator piece at the join (end -> start of the name), in the same
    local frame, centred on the letter band."""
    cy = -cap_h / 2
    if kind == "dot":
        return Point(0, cy).buffer(0.22 * cap_h, quad_segs=32)
    if kind == "star":
        pts = []
        for k in range(10):
            r = 0.30 * cap_h if k % 2 == 0 else 0.14 * cap_h
            a = -math.pi / 2 + k * math.pi / 5
            pts.append((r * math.cos(a), cy + r * math.sin(a)))
        return Polygon(pts).buffer(0.03 * cap_h).buffer(-0.03 * cap_h)
    # heart (point toward the hub)
    pts = []
    for k in range(120):
        t = 2 * math.pi * k / 120
        x = 16 * math.sin(t) ** 3
        y = (
            13 * math.cos(t)
            - 5 * math.cos(2 * t)
            - 2 * math.cos(3 * t)
            - math.cos(4 * t)
        )
        pts.append((x, -y))
    p = Polygon(pts)
    s = 0.55 * cap_h / (p.bounds[3] - p.bounds[1])
    p = affinity.scale(p, s, s, origin=(0, 0))
    b = p.bounds
    return affinity.translate(p, -(b[0] + b[2]) / 2, cy - (b[1] + b[3]) / 2)


def solid_of(g):
    if isinstance(g, MultiPolygon):
        g = max(g.geoms, key=lambda q: q.area)
    return Polygon(g.exterior)


# --------------------------------------------------------------------------
# polar placement
# --------------------------------------------------------------------------


def out_vec(th):  # outward unit vector at clockwise-from-top angle th (y down)
    return (math.sin(th), -math.cos(th))


def place(poly, th, r_base, C):
    c, s = math.cos(th), math.sin(th)
    return affinity.affine_transform(
        poly, [c, -s, s, c, C[0] + r_base * s, C[1] - r_base * c]
    )


def xf_pt(p, th, r_base, C):
    c, s = math.cos(th), math.sin(th)
    x, y = p
    return (c * x - s * y + C[0] + r_base * s, s * x + c * y + C[1] - r_base * c)


def xf_vec(v, th):
    c, s = math.cos(th), math.sin(th)
    return (c * v[0] - s * v[1], s * v[0] + c * v[1])


def ang_of(p, C):
    return math.atan2(p[0] - C[0], -(p[1] - C[1])) % (2 * math.pi)


def ang_extent(poly, r_base):
    """(left, right) angular half-extents of a local glyph placed at r_base."""
    xs = poly.exterior.coords
    a = [math.atan2(x, r_base - y) for x, y in xs]
    return -min(a), max(a)


def fit_ring(word, rp: RingParams, ppm):
    """Pick the largest cap height (<= cap_h_max) whose ring layout keeps
    every adjacent letter pair >= min_gap apart (the tight side is the INNER,
    converging side) and leaves a hub ring + hub disc. Returns a layout dict."""
    chars = [c for c in word.upper() if not c.isspace()]
    Rp = rp.diameter_mm / 2 * ppm
    Ro = Rp - (rp.frame_mm + rp.rim_mm) * ppm
    h_mm = rp.cap_h_max_mm
    ref = G.find_font(1000, rp.font)
    cap_ratio = (ref.getbbox("H")[3] - ref.getbbox("H")[1]) / 1000.0
    while True:
        cap = h_mm * ppm
        font = G.find_font(max(10, int(round(cap / cap_ratio))), rp.font)
        cap = font.getbbox("H")[3] - font.getbbox("H")[1]
        Rin = Ro - cap
        # Pixel-traced outlines are 1px staircases. Upright (banner) they're
        # collinear runs the emitter merges, but ROTATED onto the ring every
        # stair becomes a 0.1-0.2mm zig-zag move: thousands of micro-moves and
        # near-reversals that stall GRBL and flicker the laser. Simplify in the
        # local (unrotated) frame first so rotated edges are clean lines.
        locs = [glyph_local(c, font).simplify(rp.outline_smooth_px) for c in chars]
        if rp.letter_round_mm > 0:
            r = rp.letter_round_mm * ppm
            locs = [
                g.buffer(r, join_style=1)
                .buffer(-2 * r, join_style=1)
                .buffer(r, join_style=1)
                .simplify(0.25)
                for g in locs
            ]
        labels = list(chars)
        if rp.ornament:
            locs.append(ornament_local(rp.ornament, cap))
            labels.append("*")
        n = len(locs)
        ext = [ang_extent(solid_of(g), Rin) for g in locs]
        span = sum(l + r for l, r in ext)
        beta = (2 * math.pi - span) / n  # equal angular gap
        # hub sizing
        r_h = max(rp.hub_r_min_mm * ppm, n * rp.hub_arc_mm * ppm / (2 * math.pi))
        r_h = min(r_h, Rin - rp.hub_ring_min_mm * ppm)
        ths, t = [], 0.0
        for l, r in ext:
            t += l
            ths.append(t)
            t += r + beta
        # rotate so the ornament (or the join) is at the bottom, text centred on top
        join = ths[-1] if rp.ornament else ths[-1] + ext[-1][1] + beta / 2
        ths = [(x - join + math.pi) % (2 * math.pi) for x in ths]
        C = (0.0, 0.0)
        world = [place(g, th, Rin, C) for g, th in zip(locs, ths)]
        gaps = [
            solid_of(world[i]).distance(solid_of(world[(i + 1) % n])) for i in range(n)
        ]
        ok = (
            beta > 0
            and min(gaps) >= rp.min_gap_mm * ppm
            and r_h >= rp.hub_r_min_mm * ppm
        )
        if ok or h_mm <= rp.cap_h_min_mm:
            return dict(
                labels=labels,
                locs=locs,
                ths=ths,
                Rp=Rp,
                Ro=Ro,
                Rin=Rin,
                cap=cap,
                cap_mm=cap / ppm,
                r_h=r_h,
                min_gap_mm=min(gaps) / ppm,
                ok=ok,
                font_size=font.size,
            )
        h_mm -= 1.0


# --------------------------------------------------------------------------
# seam network
# --------------------------------------------------------------------------


def arc_pts(C, r, a0, a1, step_px):
    d = (a1 - a0) % (2 * math.pi)
    n = max(4, int(r * d / step_px))
    return [
        (C[0] + r * math.sin(a0 + d * k / n), C[1] - r * math.cos(a0 + d * k / n))
        for k in range(n + 1)
    ]


def closest_on(pts, th, C):
    """Index of the curve sample whose polar angle is nearest th."""
    return min(
        range(1, len(pts) - 1),
        key=lambda i: abs(
            ((ang_of(pts[i], C) - th + math.pi) % (2 * math.pi)) - math.pi
        ),
    )


def boundary_hit(panel, C, th, far):
    ray = LineString([C, (C[0] + far * math.sin(th), C[1] - far * math.cos(th))])
    hit = ray.intersection(panel.exterior)
    pts = G._coords_of(hit)
    return max(pts, key=lambda p: math.hypot(p[0] - C[0], p[1] - C[1]))


def inward_normal(panel, p, C):
    ring = panel.exterior
    d = ring.project(Point(p))
    a, b = ring.interpolate(d - 3), ring.interpolate(d + 3)
    tx, ty = b.x - a.x, b.y - a.y
    tn = math.hypot(tx, ty) or 1.0
    nx, ny = -ty / tn, tx / tn
    if nx * (C[0] - p[0]) + ny * (C[1] - p[1]) < 0:
        nx, ny = -nx, -ny
    return (nx, ny)


def n_sub(length_px, rp, ppm):
    """How many subdivision seams split a span of this length: enough that no
    piece exceeds ~1.3x the target width."""
    return max(0, math.ceil(length_px / (1.3 * rp.target_w_mm * ppm)) - 1)


def build_ring(word, seed, rp: RingParams, cfg):
    ppm = cfg.px_per_mm
    L = fit_ring(word, rp, ppm)
    D = rp.diameter_mm * ppm
    m = cfg.margin_px
    C = (m + D / 2, m + D / 2)
    cfg = replace(
        cfg,
        panel_w_px_fit=int(D),
        panel_h_px_fit=int(D),
        panel_shape="disc" if rp.shape == "disc" else "rect",
    )
    if rp.shape == "disc":
        panel = Point(C).buffer(D / 2, quad_segs=96)
    else:
        panel = box(m, m, m + D, m + D)
    Rin, Rp, r_h = L["Rin"], L["Rp"], L["r_h"]
    ths, locs = L["ths"], L["locs"]
    n = len(locs)
    world = [place(g, th, Rin, C) for g, th in zip(locs, ths)]
    letter_union = unary_union(world)
    solids_l = [solid_of(g) for g in locs]
    solids = [solid_of(w) for w in world]
    letters_solid = unary_union(solids)
    background = panel.difference(letter_union)
    far = 2 * D
    # Square stock: a circle of radius Rc (inscribed + corner_ring_mm) clipped by
    # the square fences off the 4 deep corners; radial rim seams stop on it (T)
    # where it's nearer than the square edge. Disc: Rc = inf (never hit).
    Rc = Rp + rp.corner_ring_mm * ppm if rp.shape == "square" else float("inf")
    if rp.frame_mm > 0:
        # The frame's inner circle plays the corner arc's role everywhere: every
        # radial rim seam lands on it (T) instead of on the panel edge.
        Rc = Rp - rp.frame_mm * ppm

    frame_ends = []  # polar angles where rim seams land on the frame circle

    def rim_target(phi):
        b = boundary_hit(panel, C, phi, far)
        if math.hypot(b[0] - C[0], b[1] - C[1]) > Rc:
            b = (C[0] + Rc * math.sin(phi), C[1] - Rc * math.cos(phi))
            if rp.frame_mm > 0:
                # Frame: the circle is split into arcs AT every landing, so each
                # piece touching the frame owns one arc (and gets its own tab).
                frame_ends.append(phi % (2 * math.pi))
                return b, out_vec(phi + math.pi), "J"
            return b, out_vec(phi + math.pi), "T"
        return b, inward_normal(panel, b, C), "B"

    def obstacles(attach):
        return [(s, attach.get(k)) for k, s in enumerate(solids)]

    def curved(pts):
        if pts is None:
            return None
        if G._vg_deflection(pts) < 2.5 * ppm:
            pts = G._vg_bow(pts, letters_solid, cfg)
        return pts

    best = None
    for variant in range(rp.variants):
        rng = random.Random(seed * 131 + variant * 9973)
        frame_ends.clear()
        seams = []  # dicts: pts, ends=(type_a, type_b)
        hub_ends = []  # polar angles where seams land on the hub circle
        # --- per-slot caps -------------------------------------------------
        top_caps = {}  # slot -> list of rim angles
        for i in range(n):
            tp, bp, bridges = G._letter_caps(solids_l[i], rng, ppm)
            # A bridged open side (H/N/A feet, U/H tops) is already sealed by its
            # bridge seam, so ONE cap per side is enough; per-prong caps crowd the
            # hub circle (19mm arcs, no room for tabs) and slice the rim thin.
            if len(bp) > 1 and any(sd == "bottom" for _a, _b, sd in bridges):
                bp = [min(bp, key=lambda p: abs(p[0]))]
            if len(tp) > 1 and any(sd == "top" for _a, _b, sd in bridges):
                tp = [min(tp, key=lambda p: abs(p[0]))]
            th = ths[i]
            for p in tp:
                a = xf_pt(p, th, Rin, C)
                na = xf_vec((0.0, -1.0), th)
                phi = ang_of(a, C) + math.radians(rng.uniform(-4, 4))
                b, nb, eb = rim_target(phi)
                pts = G._vg_curve(a, na, b, nb, obstacles({i: "a"}), ppm) or [a, b]
                seams.append(dict(pts=curved(pts), kind="topcap", plain_ok=rp.plain_caps, ends=("L", eb)))
                top_caps.setdefault(i, []).append(ang_of(b, C))
            for p in bp:
                a = xf_pt(p, th, Rin, C)
                na = xf_vec((0.0, 1.0), th)
                phi = ang_of(a, C) + math.radians(rng.uniform(-6, 6))
                b = (C[0] + r_h * math.sin(phi), C[1] - r_h * math.cos(phi))
                nb = out_vec(phi)
                pts = G._vg_curve(a, na, b, nb, obstacles({i: "a"}), ppm) or [a, b]
                seams.append(dict(pts=curved(pts), kind="botcap", plain_ok=rp.plain_caps, ends=("L", "T")))
                hub_ends.append(phi % (2 * math.pi))
            for a, b, _side in bridges:
                aw, bw = xf_pt(a, th, Rin, C), xf_pt(b, th, Rin, C)
                na = xf_vec((1.0, 0.0) if b[0] >= a[0] else (-1.0, 0.0), th)
                nb = (-na[0], -na[1])
                pts = G._vg_curve(aw, na, bw, nb, obstacles({i: "ab"}), ppm) or [aw, bw]
                seams.append(dict(pts=curved(pts), kind="bridge", plain_ok=rp.plain_caps, ends=("L", "L")))
        # --- ring seams between neighbouring slots ---------------------------
        levels = [0.5] if rp.rows == 2 else list(rp.levels3)  # frac of cap h
        H = []
        for i in range(n):
            miny, maxy = solids_l[i].bounds[1], solids_l[i].bounds[3]
            lo, hi = miny + 4 * ppm, maxy - 4 * ppm
            row = []
            for f in levels:
                y = -f * L["cap"] + rng.uniform(-0.07, 0.07) * L["cap"]
                row.append(min(max(y, lo), hi) if hi > lo else (miny + maxy) / 2)
            H.append(row)
        ring_seams = {}  # (gap, level) -> pts
        for g in range(n):
            i, j = g, (g + 1) % n
            for lv in range(len(levels)):
                ea = G._letter_edge_point(solids_l[i], "right", H[i][lv], ppm)
                eb = G._letter_edge_point(solids_l[j], "left", H[j][lv], ppm)
                if ea is None or eb is None:
                    continue
                a, na = xf_pt(ea[0], ths[i], Rin, C), xf_vec(ea[1], ths[i])
                b, nb = xf_pt(eb[0], ths[j], Rin, C), xf_vec(eb[1], ths[j])
                pts = G._vg_curve(a, na, b, nb, obstacles({i: "a", j: "b"}), ppm) or [
                    a,
                    b,
                ]
                pts = curved(pts)
                ring_seams[(g, lv)] = pts
                seams.append(dict(pts=pts, kind="ring", ends=("L", "L")))
        # --- rim subdivisions: T off the outermost ring seam up to the rim ----
        top_lv = len(levels) - 1
        for g in range(n):
            i, j = g, (g + 1) % n
            host = ring_seams.get((g, top_lv))
            if host is None or i not in top_caps or j not in top_caps:
                continue
            a0 = max(
                top_caps[i],
                key=lambda a: (
                    (a - ths[i]) % (2 * math.pi)
                    if (a - ths[i]) % (2 * math.pi) < math.pi
                    else -1
                ),
            )
            a1 = min(top_caps[j], key=lambda a: (a - ths[j]) % (2 * math.pi))
            d = (a1 - a0) % (2 * math.pi)
            # piece budget by AREA of the rim region in this angular window, so
            # a square panel's deep corners get more (narrower) pieces
            wedge = Polygon(
                [C]
                + [
                    (
                        C[0] + far * math.sin(a0 + d * t / 16),
                        C[1] - far * math.cos(a0 + d * t / 16),
                    )
                    for t in range(17)
                ]
            )
            area = (
                panel.intersection(wedge)
                .intersection(Point(C).buffer(min(Rc, far), quad_segs=64))
                .difference(Point(C).buffer(L["Ro"], quad_segs=64))
                .area
            )
            k = n_sub(area / (Rp - L["Ro"]), rp, ppm)
            for s in range(1, k + 1):
                phi = a0 + d * s / (k + 1) + math.radians(rng.uniform(-2, 2))
                idx = closest_on(host, phi, C)
                a = host[idx]
                b, nb, eb = rim_target(ang_of(a, C) + math.radians(rng.uniform(-3, 3)))
                na = out_vec(ang_of(a, C))
                pts = G._vg_curve(a, na, b, nb, obstacles({}), ppm) or [a, b]
                seams.append(dict(pts=curved(pts), kind="rimsub", ends=("T", eb)))
        # --- middle-band subdivisions (wide gaps on short names) ---------------
        if len(levels) == 2:
            for g in range(n):
                lo_h, up_h = ring_seams.get((g, 0)), ring_seams.get((g, 1))
                if lo_h is None or up_h is None:
                    continue
                w = (LineString(lo_h).length + LineString(up_h).length) / 2
                k = n_sub(w, rp, ppm)
                a_lo, a_hi = ang_of(lo_h[0], C), ang_of(lo_h[-1], C)
                d = (a_hi - a_lo) % (2 * math.pi)
                for s_ in range(1, k + 1):
                    phi = a_lo + d * s_ / (k + 1) + math.radians(rng.uniform(-2, 2))
                    a = lo_h[closest_on(lo_h, phi, C)]
                    b = up_h[closest_on(up_h, phi, C)]
                    na, nb = out_vec(ang_of(a, C)), out_vec(ang_of(b, C) + math.pi)
                    pts = G._vg_curve(a, na, b, nb, obstacles({}), ppm) or [a, b]
                    seams.append(dict(pts=curved(pts), kind="midsub", ends=("T", "T")))
        # --- hub-ring subdivisions: T off the innermost ring seam to the hub ---
        hub_sorted = sorted(hub_ends)
        r_hm = (Rin + r_h) / 2
        for g in range(n):
            host = ring_seams.get((g, 0))
            if host is None:
                continue
            ga = ang_of(host[len(host) // 2], C)
            # hub-cap angles bracketing this gap
            prev = max(
                [a for a in hub_sorted if (ga - a) % (2 * math.pi) < math.pi]
                or hub_sorted,
                key=lambda a: -((ga - a) % (2 * math.pi)),
            )
            nxt = min(hub_sorted, key=lambda a: (a - ga) % (2 * math.pi))
            d = (nxt - prev) % (2 * math.pi)
            k = n_sub(r_hm * d, rp, ppm)
            for s in range(1, k + 1):
                phi = prev + d * s / (k + 1)
                idx = closest_on(host, phi, C)
                a = host[idx]
                phb = ang_of(a, C) + math.radians(rng.uniform(-5, 5))
                b = (C[0] + r_h * math.sin(phb), C[1] - r_h * math.cos(phb))
                pts = G._vg_curve(
                    a,
                    out_vec(ang_of(a, C) + math.pi),
                    b,
                    out_vec(phb),
                    obstacles({}),
                    ppm,
                ) or [a, b]
                seams.append(dict(pts=curved(pts), kind="hubsub", ends=("T", "T")))
        # --- hub disc spokes (pinwheel), landing on the hub circle -------------
        k_hub = 1
        while (
            2 * r_h * (math.sin(math.pi / k_hub) if k_hub > 1 else 1.0)
            > rp.hub_piece_mm * ppm
        ):
            k_hub += 1
        a0 = rng.uniform(0, 2 * math.pi)
        split = []
        if k_hub > 1:
            twist = rng.choice((-1, 1))
            for s_ in range(k_hub):
                phi = a0 + 2 * math.pi * s_ / k_hub
                b = (C[0] + r_h * math.sin(phi), C[1] - r_h * math.cos(phi))
                # pinwheel: leave the centre along a rotated direction
                na = out_vec(phi + twist * math.radians(50))
                pts = G._vg_bez(C, na, b, out_vec(phi + math.pi), 0.45 * r_h)
                seams.append(dict(pts=pts, kind="spoke", ends=("J", "J")))
                split.append(phi % (2 * math.pi))
        else:
            split = [(a0 + 2 * math.pi * s_ / 3) % (2 * math.pi) for s_ in range(3)]
        # --- solid frame: its inner circle, split into a few host arcs ---------
        if rp.frame_mm > 0:
            if frame_ends:
                fa = sorted(frame_ends)
                fa = [a for k, a in enumerate(fa) if k == 0 or a - fa[k - 1] > 1e-6]
            else:
                f0 = rng.uniform(0, 2 * math.pi)
                fa = sorted(
                    (f0 + 2 * math.pi * q / rp.frame_arcs) % (2 * math.pi)
                    for q in range(rp.frame_arcs)
                )
            for q in range(len(fa)):
                seams.insert(
                    0,
                    dict(
                        pts=arc_pts(C, Rc, fa[q], fa[(q + 1) % len(fa)], 2 * ppm),
                        kind="framearc",
                        ends=("J", "J"),
                    ),
                )
        # --- square corners: arc on Rc across each corner + a diagonal split ---
        if rp.shape == "square" and rp.frame_mm <= 0:
            half = D / 2
            span = math.acos(
                min(1.0, half / Rc)
            )  # angle from the edge normal to the arc end
            for q in range(4):
                mid = math.pi / 4 + q * math.pi / 2  # corner direction
                a0 = mid - (math.pi / 4 - span) - math.radians(1.0)
                a1 = mid + (math.pi / 4 - span) + math.radians(1.0)
                seams.insert(
                    0,
                    dict(
                        pts=arc_pts(
                            C, Rc, a0 % (2 * math.pi), a1 % (2 * math.pi), 2 * ppm
                        ),
                        kind="cornerarc",
                        ends=("B", "B"),
                    ),
                )
                phi = mid + math.radians(rng.uniform(-8, 8))
                a = (C[0] + Rc * math.sin(phi), C[1] - Rc * math.cos(phi))
                b = boundary_hit(panel, C, phi, far)
                seams.append(
                    dict(pts=curved([a, b]), kind="cornersub", ends=("T", "B"))
                )
        # --- hub circle: arcs between spoke landings (caps T onto it) ---------
        hs = sorted(split)
        for q in range(len(hs)):
            b0, b1 = hs[q], hs[(q + 1) % len(hs)]
            seams.insert(
                0,
                dict(
                    pts=arc_pts(C, r_h, b0, b1, 2 * ppm), kind="hubarc", ends=("J", "J")
                ),
            )

        with _oriented_oversized():
            surround, counters, st = assemble(
                seams, letter_union, letters_solid, background, panel, cfg, C
            )
        sc = score(surround, panel, cfg) + (st["dropped"],)
        if best is None or sc < best[0]:
            best = (sc, surround, counters, st, seams)
    sc, surround, counters, st, seams = best
    st["score"] = sc
    pieces = {(k, 0): p for k, p in enumerate(surround + counters)}
    st["seams"] = seams
    return pieces, letter_union, cfg, L, st, panel, C


# --------------------------------------------------------------------------
# assembly with junction support (generalised _vg_assemble)
# --------------------------------------------------------------------------


def _extend(pts, end, d):
    if end == 0:
        (x0, y0), (x1, y1) = pts[1], pts[0]
    else:
        (x0, y0), (x1, y1) = pts[-2], pts[-1]
    L = math.hypot(x1 - x0, y1 - y0) or 1.0
    q = (x1 + (x1 - x0) / L * d, y1 + (y1 - y0) / L * d)
    return [q] + pts if end == 0 else pts + [q]


def assemble(seams, letter_union, letters_solid, background, panel, cfg, C):
    ppm = cfg.px_per_mm
    st = {"total": 0, "centered": 0, "shifted": 0, "flipped": 0, "dropped": 0}
    min_gap = cfg.letter_clearance_px
    jr = 7 * ppm  # tab bulbs keep this far from any junction point
    # junction points = every non-letter/non-border endpoint (and T hosts' feet)
    junctions = []
    for s in seams:
        for e, p in ((0, s["pts"][0]), (1, s["pts"][-1])):
            if s["ends"][e] in ("T", "J"):
                junctions.append(Point(p))
    keep = [j.buffer(jr) for j in junctions]
    jzone = (
        unary_union([j.buffer(min_gap + 3 * ppm) for j in junctions])
        if junctions
        else None
    )
    placed = list(keep)
    accepted, tabbed = [], []

    def conflict(cand):
        core = cand if jzone is None else cand.difference(jzone)
        if core.is_empty:
            return False
        for a in accepted:
            if core.distance(a) >= min_gap:
                continue
            q1, q2 = nearest_points(core, a)
            mid = Point((q1.x + q2.x) / 2, (q1.y + q2.y) / 2)
            if letters_solid.distance(mid) > min_gap:
                return True
        return False

    for s in seams:
        pts = [tuple(p) for p in s["pts"]]
        ea, eb = s["ends"]
        # Overshoot every letter/border/T end by 2px so the union NODES the
        # crossing (an endpoint merely touching a line is float-fragile);
        # polygonize discards the dangling stubs.
        if ea in ("L", "T", "B"):
            pts = _extend(pts, 0, 2)
        if eb in ("L", "T", "B"):
            pts = _extend(pts, 1, 2)
        ls0 = LineString(pts)
        if ls0.intersection(background).length < 3 * ppm:
            s["status"] = "short"
            continue
        n = max(6, int(ls0.length / (2 * ppm)))
        rs = [ls0.interpolate(t / n, normalized=True) for t in range(n + 1)]
        rs = [(p.x, p.y) for p in rs]
        st["total"] += 1
        chosen = None
        s["ncand"] = 0
        s["nconf"] = 0
        for scale in (1.0, 0.85, 0.7):
            c2 = (
                cfg
                if scale >= 0.999
                else replace(
                    cfg,
                    tab_circle_r_px=max(6, int(round(cfg.tab_circle_r_px * scale))),
                    tab_stem_w_px=(
                        cfg.tab_stem_w_px * scale if cfg.tab_stem_w_px else None
                    ),
                )
            )
            for pi, ps in G._vg_tab_candidates(rs, letters_solid, c2, panel, placed):
                res = G._vg_splice(rs, pi, ps, c2)
                if res is None:
                    continue
                spliced, bulb = res
                cand = LineString(spliced)
                s["ncand"] += 1
                if conflict(cand):
                    s["nconf"] += 1
                    continue
                chosen = (spliced, bulb, cand)
                break
            if chosen:
                break
        if chosen:
            tabbed.append(chosen[0])
            placed.append(chosen[1])
            accepted.append(chosen[2])
            st["centered"] += 1
            s["status"] = "ok"
        else:
            s["status"] = "drop"
            # keep the seam untabbed only if it's needed for structure (hub
            # arcs, spokes); otherwise drop it like wave-grid does.
            cand = LineString(rs)
            if ((ea, eb) == ("J", "J") or s.get("plain_ok")) and not conflict(cand):
                tabbed.append(rs)
                accepted.append(cand)
                s["status"] = "plain"
            st["dropped"] += 1

    net = shapely.union_all(
        [panel.boundary, letter_union.boundary] + [LineString(t) for t in tabbed],
        grid_size=0.1,
    )
    faces = [
        f
        for f in polygonize(net)
        if background.contains(f.representative_point()) and f.area > ppm**2
    ]
    surround = [
        f for f in faces if not letters_solid.contains(f.representative_point())
    ]
    counters = [f for f in faces if letters_solid.contains(f.representative_point())]
    surround = G._vg_absorb(surround, panel, cfg)
    return surround, counters, st


def oversized_oriented(poly, cfg):
    """Rotation-invariant _vg_oversized: ring pieces sit at every angle, so the
    axis-aligned bbox test would flag diagonal pieces falsely."""
    ppm = cfg.px_per_mm
    if poly.area <= (50 * ppm) ** 2:
        return False
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mrr = poly.minimum_rotated_rectangle
    xs, ys = mrr.exterior.coords.xy
    e = sorted(math.hypot(xs[i + 1] - xs[i], ys[i + 1] - ys[i]) for i in range(2))
    return e[0] > 34 * ppm or e[1] > 64 * ppm


@contextlib.contextmanager
def _oriented_oversized():
    """_vg_absorb calls geometry._vg_oversized internally; swap in the
    rotation-invariant test for the duration of a ring build only. (In the
    real implementation, give _vg_oversized an `oriented` flag instead.)"""
    orig = G._vg_oversized
    G._vg_oversized = oversized_oriented
    try:
        yield
    finally:
        G._vg_oversized = orig


def score(surround, panel, cfg):
    thin = sum(1 for p in surround if G._vg_thin_bridge(p, cfg))
    over = sum(1 for p in surround if oversized_oriented(p, cfg))
    slv = sum(1 for p in surround if G._vg_sliver(p, cfg))
    nub = sum(1 for p in surround if G._vg_border_nub(p, panel, cfg))
    return (thin, over, slv, nub)


def make_cfg(rp):
    """Name-plate tab settings (banner_puzzle_config's fat capsule tabs, 4mm
    letter clearance) on a square canvas. Both border floors are 7mm: a ring has
    no 'short dimension', every tab near the rim gets the generous floor."""
    return G.PuzzleConfig(
        panel_mm=rp.diameter_mm,
        panel_h_mm=rp.diameter_mm,
        piece_mm=25,
        tab_circle_r_px=rp.tab_r_px,
        tab_stem_w_px=rp.tab_stem_px,
        letter_clearance_mm=rp.letter_clearance_mm,
        corner_radius_mm=0.0,
        tab_border_floor_v_mm=rp.border_floor_mm,
        tab_border_floor_h_mm=rp.border_floor_mm,
        font_path=rp.font,
    )


def generate(word, seed, rp):
    """Same (pieces, ...) shape as geometry.generate_pieces: carve pockets,
    fuse counters, append letters, absorb letter slivers."""
    cfg = make_cfg(rp)
    if rp.shape == "square":
        cfg = replace(cfg, corner_radius_mm=5.0)
    piece_polys, letter_union, cfg, L, st, panel, C = build_ring(word, seed, rp, cfg)
    frags = G.carve_letter_pockets(piece_polys, letter_union)
    frags = G.fuse_counter_fragments(frags, letter_union, cfg)
    if rp.shape == "square":
        frags = G.round_panel_corners(frags, cfg)
    lp = [
        g
        for g in (
            letter_union.geoms
            if isinstance(letter_union, MultiPolygon)
            else [letter_union]
        )
        if g.area > 100
    ]
    pieces = list(frags) + [
        {"parent": None, "polygon": g, "kind": "letter"} for g in lp
    ]
    pieces = G.absorb_letter_slivers(pieces, letter_union, cfg)
    # Snap every outline to a shared 1px (0.2mm, ~kerf) grid. The float
    # rotations + carving leave near-duplicate vertices where pockets meet seams;
    # after dedup those become 0.00-0.04mm stub edges, i.e. isolated micro cut
    # paths. Shared boundaries snap identically, so pieces still tile exactly.
    for p in pieces:
        p["polygon"] = shapely.set_precision(p["polygon"], 1.0)
    for i, p in enumerate(pieces, 1):
        p["serial"] = i
    return pieces, cfg, L, st, panel, C


def render_debug_overlay(png_path, seams):
    """Draw every raw seam over a rendered preview: green = tabbed, blue =
    kept untabbed (hub arcs / spokes), red = dropped (its two faces merged)."""
    img = Image.open(png_path).convert("RGB")
    d = ImageDraw.Draw(img)
    col = {"ok": (0, 150, 0), "drop": (230, 0, 0), "plain": (0, 0, 230)}
    for s in seams:
        st = s.get("status")
        d.line(
            [tuple(p) for p in s["pts"]],
            fill=col.get(st, (255, 140, 0)),
            width=5 if st == "drop" else 2,
        )
        if st == "drop":
            d.text(tuple(s["pts"][len(s["pts"]) // 2]), s["kind"], fill=(200, 0, 0))
    img.save(png_path)


def main():
    import jigsaw as J

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("word")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--variants", type=int, default=12)
    ap.add_argument("--shape", choices=("disc", "square"), default="disc")
    ap.add_argument("--diameter-mm", type=float, default=300.0)
    ap.add_argument("--rows", type=int, choices=(2, 3), default=3)
    ap.add_argument("--ornament", default="heart", help="heart | dot | star | none")
    ap.add_argument("--font", default=None)
    ap.add_argument("--letter-round-mm", type=float, default=0.0)
    ap.add_argument("--debug", action="store_true", help="overlay seam status")
    ap.add_argument("--gcode", default=None, help="also emit cut GCode to this path")
    ap.add_argument("--material", default="plywood_baltic_birch_3mm")
    ap.add_argument("--feed", type=int, default=None)
    ap.add_argument("--passes", type=int, default=None)
    ap.add_argument(
        "--max-backtrack-ms", type=float, default=5000.0,
        help="re-trace already-cut line up to this many ms to avoid a restart+warmup",
    )
    ap.add_argument(
        "--min-segment-mm", type=float, default=0.3,
        help="drop G1 chords shorter than this (mm) so GRBL never stalls on micro-moves",
    )
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    rp = RingParams(
        diameter_mm=a.diameter_mm,
        shape=a.shape,
        rows=a.rows,
        variants=a.variants,
        ornament=None if a.ornament == "none" else a.ornament,
        font=a.font,
    )
    word = a.word.upper()
    pieces, cfg, L, st, _panel, _C = generate(word, a.seed, rp)
    out = Path(
        a.out
        or Path(__file__).resolve().parent.parent
        / "figs"
        / f"ring_{word}_{a.shape}.png"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    title = f"{word} ring {a.shape} seed {a.seed}  cap {L['cap_mm']:.0f}mm  {len(pieces)}pc  score {st['score']}"
    J.render_preview(pieces, cfg, title, out)
    if a.debug:
        render_debug_overlay(out, st["seams"])
    if a.gcode:
        from emitter import load_material

        material = load_material(a.material)
        if a.passes is not None:
            material = {**material, "laser": {**material["laser"], "passes": a.passes}}
        gcode = J._emit_cut_for(
            pieces, material, cfg, word, "ring", mode="static",
            feed_override=a.feed, power_percent=100.0,
            min_segment_mm=a.min_segment_mm,
            max_backtrack_ms=a.max_backtrack_ms,
        )
        Path(a.gcode).write_text(gcode)
        png, _svg = J.render_gcode_previews(
            gcode, cfg, Path(a.gcode).with_suffix(""), title=f"{word} ring cut"
        )
        print(f"-> {a.gcode}  ({len(gcode.splitlines())} lines), toolpath {png}")
    print(
        f"{word}: cap {L['cap_mm']:.1f}mm, min letter gap {L['min_gap_mm']:.1f}mm, "
        f"hub r {L['r_h'] / cfg.px_per_mm:.0f}mm, {len(pieces)} pieces, "
        f"score (thin, oversized, sliver, nub, dropped) = {st['score']} -> {out}"
    )


if __name__ == "__main__":
    main()
