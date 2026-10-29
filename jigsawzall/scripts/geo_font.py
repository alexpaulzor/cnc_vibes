#!/usr/bin/env python3
"""Geometric stroke font for the ring puzzles (`--font geo`).

Every capital is built from straight strokes and circular arcs on a
cap-height grid (no TrueType outline): each stroke's centre line is
buffered to a constant stroke width, the letter is clipped flat at the
baseline and cap line, and every FREE stroke end (one that doesn't run into
another stroke) gets semicircle serifs -- the stroke end flares wider than
its neck, so in the cut puzzle each end locks into the surround like a tab
(Alex: "the end of each letter effectively becomes a puzzle tab").
Symmetric letters come out exactly symmetric because their strokes are.

Serif: at each free end, half a circle of diameter `serif_mm` on each side
of the stroke, flat side against the stroke's side edge from the end
corner back, bulging outward -- e.g. the I's foot flares into a round bulb
on a stroke-wide neck, the shape of the puzzle's own tabs.

Units below are cap heights (H = 1, y up, baseline 0). GeoFont mimics the
bits of PIL's FreeTypeFont that ring_prototype reads (getbbox("H") for the
cap height, getlength for the advance) plus glyph() for the outline in
glyph_local's frame (x centred, baseline y=0, y down).

    PYTHONPATH=. python3 scripts/geo_font.py OUT.png   # specimen sheet
"""
import math
import sys

from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

STROKE = 0.17  # stroke width / cap height (3.1mm at an 18mm cap)
CAP_RATIO = 0.72  # cap height / "font size", like a TrueType face
SIDE = 0.10  # side bearing each side / cap height
SERIF_MM = 2.0  # serif lobe diameter (Alex picked 2mm over 3 and 1.5)
FILLET_MM = 0.75  # every corner of the outline rounded to this radius
NOTCH_MIN_MM = 3.0  # a serif bump keeps this much wood to a stroke straight ahead of it


def _arc(cx, cy, r, a0, a1, n=None):
    """Circular arc, angles in degrees CCW from +x, a0 -> a1."""
    n = n or max(8, int(abs(a1 - a0) / 4))
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * k / n)),
             cy + r * math.sin(math.radians(a0 + (a1 - a0) * k / n))) for k in range(n + 1)]


def _letters():
    """{ch: (width, [stroke polylines])} in cap units, centre lines."""
    s = STROKE
    h = s / 2
    top, bot = 1 - h, h
    L = {}
    w = 0.86
    # crossbar ends on the legs' centre lines (the feet are set in by the
    # legs' half-width, see glyph_units), so it sits inside them: no bumps
    hw = h / (1 / math.hypot(1, w / 2))
    ya = 0.33
    xa = hw + (w / 2 - hw) * ya
    L["A"] = (w, [[(0, 0), (w / 2, 1)], [(w / 2, 1), (w, 0)], [(xa, ya), (w - xa, ya)]])
    # B: stem + two bowls, the bottom one a little bigger
    m = 0.54
    rt, rb = (top - m) / 2, (m - bot) / 2
    at, ab = h + 0.24, h + 0.26
    L["B"] = (ab + rb + h, [[(h, 0), (h, 1)],
                            [(h, top), (at, top)] + _arc(at, m + rt, rt, 90, -90) + [(h, m)],
                            [(h, m), (ab, m)] + _arc(ab, bot + rb, rb, 90, -90) + [(h, bot)]])
    R = 0.5 - h
    L["C"] = (1.0, [_arc(0.5, 0.5, R, 42, 318)])
    a = h + 0.16
    L["D"] = (a + R + h, [[(h, 0), (h, 1)], [(h, top), (a, top)] + _arc(a, 0.5, R, 90, -90) + [(h, bot)]])
    w = 0.80
    # middle bar short of the others: the top/bottom bar ends' serif bumps
    # then keep NOTCH_MIN_MM of wood to it and stay
    mb = w - h - 0.35
    L["E"] = (w, [[(h, 0), (h, 1)], [(h, top), (w - h, top)], [(h, 0.5), (mb, 0.5)],
                  [(h, bot), (w - h, bot)]])  # middle bar centred: same room above and below
    L["F"] = (w, [[(h, 0), (h, 1)], [(h, top), (w - h, top)], [(h, 0.5), (mb, 0.5)]])
    # terminal at 48deg, crossbar at 0.45: the terminal's inner bump keeps
    # 3.2mm to the bar. The arc ends exactly ON the crossbar's centre line,
    # so the two meet in one rounded joint (ending it at 0.5 left a step)
    ge = 360 + math.degrees(math.asin((0.45 - 0.5) / R))
    gx = 0.5 + R * math.cos(math.radians(ge))
    L["G"] = (1.0, [_arc(0.5, 0.5, R, 48, ge), [(gx, 0.45), (0.56, 0.45)]])
    w = 0.80
    L["H"] = (w, [[(h, 0), (h, 1)], [(w - h, 0), (w - h, 1)], [(h, 0.5), (w - h, 0.5)]])  # top = bottom
    L["I"] = (s, [[(h, 0), (h, 1)]])
    w = 0.64
    rj = (w - s) / 2
    L["J"] = (w, [[(w - h, 1), (w - h, h + rj)] + _arc(h + rj, h + rj, rj, 0, -180) + [(h, h + rj + 0.08)]])
    w = 0.80
    kx = 0.30  # lower leg starts ON the upper one (no lump at the joint)
    L["K"] = (w, [[(h, 0), (h, 1)], [(h, 0.42), (w, 1)],
                  [(kx, 0.42 + (kx - h) / (w - h) * 0.58), (w, 0)]])
    w = 0.66
    L["L"] = (w, [[(h, 1), (h, 0)], [(h, bot), (w - h, bot)]])
    w = 1.0
    L["M"] = (w, [[(h, 0), (h, 1)], [(h, 1), (w / 2, 0.28)], [(w / 2, 0.28), (w - h, 1)], [(w - h, 1), (w - h, 0)]])
    w = 0.82
    L["N"] = (w, [[(h, 0), (h, 1)], [(h, 1), (w - h, 0)], [(w - h, 0), (w - h, 1)]])
    L["O"] = (1.0, [_arc(0.5, 0.5, R, 0, 360)])
    rp = (top - 0.44) / 2
    ap = h + 0.30
    L["P"] = (ap + rp + h, [[(h, 0), (h, 1)], [(h, top), (ap, top)] + _arc(ap, 0.44 + rp, rp, 90, -90) + [(h, 0.44)]])
    # tail: on the ring's own radius at -45deg -- square across the ring
    # (Alex) -- from inside the bowl out to the baseline
    q0 = (0.5 + 0.25 * math.cos(math.radians(-45)), 0.5 + 0.25 * math.sin(math.radians(-45)))
    q1 = (q0[0] + q0[1], 0.0)
    L["Q"] = (q1[0] + 0.05, [_arc(0.5, 0.5, R, 0, 360), [q0, q1]])
    L["R"] = (ap + rp + h, [[(h, 0), (h, 1)], [(h, top), (ap, top)] + _arc(ap, 0.44 + rp, rp, 90, -90) + [(h, 0.44)],
                            [(ap - 0.02, 0.44), (ap + rp + h, 0)]])
    # S: two 3/4 circles meeting at the centre, 180deg-symmetric
    rs = (1 - s) / 4
    w = 2 * rs + s + 0.06
    cx = w / 2
    L["S"] = (w, [_arc(cx, top - rs, rs, 25, 270), _arc(cx, bot + rs, rs, 90, -155)])
    w = 0.80
    L["T"] = (w, [[(h, top), (w - h, top)], [(w / 2, top), (w / 2, 0)]])
    w = 0.80
    ru = (w - s) / 2
    L["U"] = (w, [[(h, 1), (h, h + ru)] + _arc(w / 2, h + ru, ru, 180, 360) + [(w - h, 1)]])
    w = 0.86
    L["V"] = (w, [[(0, 1), (w / 2, 0)], [(w / 2, 0), (w, 1)]])
    w = 1.24
    L["W"] = (w, [[(0, 1), (0.25 * w, 0)], [(0.25 * w, 0), (0.5 * w, 0.72)], [(0.5 * w, 0.72), (0.75 * w, 0)],
                  [(0.75 * w, 0), (w, 1)]])
    w = 0.84
    L["X"] = (w, [[(0, 0), (w, 1)], [(0, 1), (w, 0)]])
    w = 0.84
    L["Y"] = (w, [[(0, 1), (w / 2, 0.46)], [(w, 1), (w / 2, 0.46)], [(w / 2, 0.46), (w / 2, 0)]])
    w = 0.78
    L["Z"] = (w, [[(h, top), (w - h, top)], [(w - h, top), (h, bot)], [(h, bot), (w - h, bot)]])
    return L


LETTERS = _letters()


def _extend(pl, k, d):
    """Extend polyline pl's end k (0 or -1) by d along its end direction."""
    (x0, y0), (x1, y1) = (pl[1], pl[0]) if k == 0 else (pl[-2], pl[-1])
    n = math.hypot(x1 - x0, y1 - y0) or 1.0
    e = (x1 + (x1 - x0) / n * d, y1 + (y1 - y0) / n * d)
    return [e] + pl if k == 0 else pl + [e]


def glyph_units(ch, serif_mm=None, cap_mm=18.0, stroke=STROKE, fillet_mm=None, log=None):
    """Letter outline in cap units (y up, baseline 0), and its width. `log`
    (a list) collects each serif bump's (end point, side, kept?, mm ahead)."""
    w, strokes = LETTERS[ch]
    s = stroke
    serif_mm = SERIF_MM if serif_mm is None else serif_mm
    r = serif_mm / cap_mm / 2  # serif semicircle radius, cap units
    f = (FILLET_MM if fillet_mm is None else fillet_mm) / cap_mm

    def closing(gg):  # fills inside corners, as the final rounding will
        return gg.buffer(f, quad_segs=12).buffer(-f, quad_segs=12) if f > 0 else gg
    notch = NOTCH_MIN_MM / cap_mm  # a lobe keeps this much wood to a stroke ahead of it
    strokes = [list(pl) for pl in strokes]
    lines = [LineString(pl) for pl in strokes]

    def unit(v):
        n_ = math.hypot(*v) or 1.0
        return (v[0] / n_, v[1] / n_)

    # every stroke end: free (gets the tab lobes) or a joint
    ends = []  # (stroke i, k, point, outward direction d, free?, on the baseline/cap line?)
    for i, pl in enumerate(strokes):
        others = unary_union([ln for j, ln in enumerate(lines) if j != i]) if len(lines) > 1 else None
        closed = math.dist(pl[0], pl[-1]) < 1e-6  # O, Q's ring: no ends at all
        for k in (0, -1):
            p = pl[k]
            q = pl[1] if k == 0 else pl[-2]
            d = unit((p[0] - q[0], p[1] - q[1]))
            free = not closed and (others is None or others.distance(Point(p)) > 0.6 * s)
            on_line = p[1] <= 1e-6 or p[1] >= 1 - 1e-6
            ends.append((i, k, p, d, free, on_line))

    def edge_points(pl, k, P, d):
        """The two points on the stroke's side EDGES (offset curves of its
        centre line, s/2 either side) one serif radius back from end k,
        measured along each edge -- on a curve the tangent would put the
        inner and outer circle in unequal spots."""
        nrm = (-d[1], d[0])
        out = []
        for sg in (1, -1):
            corner = Point(P[0] + nrm[0] * sg * s / 2, P[1] + nrm[1] * sg * s / 2)
            # offset_curve's + side is LEFT of the line's own direction; at a
            # stroke's start (k=0) that direction points into the stroke, so
            # left/right swap relative to the outward normal
            side = sg if k == -1 else -sg
            edge = LineString(pl).offset_curve(side * s / 2, join_style="round")
            if edge.is_empty or edge.length < 2 * r:
                out.append(None)
                continue
            a_, b_ = Point(edge.coords[0]), Point(edge.coords[-1])
            t = r if a_.distance(corner) < b_.distance(corner) else edge.length - r
            q = edge.interpolate(t)
            out.append(((q.x, q.y), (nrm[0] * sg, nrm[1] * sg)))
        return out

    def lobes(P, d, pl=None, k=None):
        """The tab at a stroke end whose square end-line is centred on P
        (outward direction d): a circle of radius r on EACH side edge, level
        with the end line less r, bulging outward -- a mirror pair about the
        stroke's own axis, whatever its angle. Returns [(circle, normal)]."""
        out = []
        on_edge = edge_points(pl, k, P, d) if pl is not None else [None, None]
        for sg, ep in zip((1, -1), on_edge):
            nrm = (-d[1] * sg, d[0] * sg)
            c = ep[0] if ep else (P[0] + nrm[0] * s / 2 - d[0] * r, P[1] + nrm[1] * s / 2 - d[1] * r)
            # a FULL circle centred on the edge: on a straight stroke its inner
            # half hides inside the stroke (same as a half circle); on a curve
            # it blends into the bending edge instead of leaving a notch where
            # a flat half-circle side ran off along the tangent (C, S)
            out.append((Point(c).buffer(r, quad_segs=24), nrm))
        return out

    def end_square(P, d):
        nrm = (-d[1], d[0])
        return Polygon([(P[0] + nrm[0] * s / 2, P[1] + nrm[1] * s / 2), (P[0] - nrm[0] * s / 2, P[1] - nrm[1] * s / 2),
                        (P[0] - nrm[0] * s / 2 - d[0] * 2 * r, P[1] - nrm[1] * s / 2 - d[1] * 2 * r),
                        (P[0] + nrm[0] * s / 2 - d[0] * 2 * r, P[1] + nrm[1] * s / 2 - d[1] * 2 * r)])

    # a FREE end on the baseline / cap line ends square across its stroke
    # (never cut along the line: a slanted foot cut flat gave A and C unequal
    # inner and outer lobes) and is pulled back along the stroke until its
    # lowest (highest) point just touches the line
    tab_at = {}
    for i, k, p, d, free, on_line in ends:
        if not (free and on_line):
            continue
        shape = unary_union([end_square(p, d)] + [lb for lb, _n in lobes(p, d)])
        over = -shape.bounds[1] if p[1] <= 1e-6 else shape.bounds[3] - 1.0
        t = max(0.0, over) / max(abs(d[1]), 1e-6)
        P = (p[0] - d[0] * t, p[1] - d[1] * t)
        strokes[i][k] = P
        tab_at[(i, k)] = P
    # joints on the baseline / cap line (V's point, N's corners): run the
    # stroke past the line and cut it flat there, inside the letter's box
    # (no spur beside N's / M's stem)
    pieces, extensions = [], []
    for i, pl in enumerate(strokes):
        pieces.append(LineString(pl).buffer(s / 2, cap_style="flat", join_style="round"))
        for ii, k, p, d, free, on_line in ends:
            if ii == i and on_line and not free:
                e = (p[0] + d[0] * 2 * s, p[1] + d[1] * 2 * s)
                extensions.append(LineString([p, e]).buffer(s / 2, cap_style="flat"))
    body = unary_union(pieces + [x.intersection(box(0, 0, w, 1)) for x in extensions])
    # round over a joint only where two stroke ENDS meet (W's peak, M's
    # middle): an end landing part-way along another stroke (A's crossbar)
    # would poke a bump out past it
    tips = [p for _i, _k, p, _d, _f, _o in ends]
    joints = [Point(p).buffer(s / 2, quad_segs=16) for _i, _k, p, _d, free, _o in ends
              if not free and 1e-6 < p[1] < 1 - 1e-6
              and sum(1 for q in tips if math.dist(p, q) < 0.6 * s) >= 2]
    body = unary_union([body] + joints).intersection(box(-1, 0, w + 1, 1))
    serifs = []
    for i, k, p, d, free, on_line in ends:
        if not free:
            continue
        P = tab_at.get((i, k), p)
        # a closed ring (Q's bowl) doesn't count: the tail's lobes may touch it
        others = unary_union([pc for j, pc in enumerate(pieces)
                              if j != i and math.dist(strokes[j][0], strokes[j][-1]) > 1e-6])
        pair = []
        for bump, o in lobes(P, d, strokes[i], k):
            # a lobe with another stroke within NOTCH_MIN_MM straight AHEAD of
            # it would pinch the surround's wood between them to a neck (E's
            # middle bar, under its top bar): skip it
            ahead = unary_union([bump, affinity.translate(bump, o[0] * notch, o[1] * notch)]).convex_hull
            dropped = not others.is_empty and ahead.intersects(others)
            if log is not None:
                log.append((P, o, not dropped, bump.distance(others) * cap_mm if not others.is_empty else None))
            if not dropped:
                pair.append(bump)
        # both lobes or neither: a lone lobe on one side of an end looks lopsided
        if len(pair) == 2:
            # on a curve the edge bends toward one circle and away from the
            # other, so equal circles SHOW unequal amounts (S: 80%): push the
            # smaller-showing one out along its normal until they match
            # measured AFTER the corner rounding, which fills a lobe's neck
            # more where the stroke curves back toward it (G's terminal, J's
            # hook): balanced before rounding, they still LOOKED unequal
            base = closing(body)

            def shows(b):
                return closing(unary_union([body, b])).difference(base).area

            vis = [shows(b) for b in pair]
            lo = 0 if vis[0] < vis[1] else 1
            if vis[lo] < 0.99 * vis[1 - lo]:
                nrm = lobes(P, d, strokes[i], k)[lo][1]
                a_, b_ = 0.0, r
                for _ in range(16):
                    m = (a_ + b_) / 2
                    t = affinity.translate(pair[lo], nrm[0] * m, nrm[1] * m)
                    if shows(t) < vis[1 - lo]:
                        a_ = m
                    else:
                        b_ = m
                pair[lo] = affinity.translate(pair[lo], nrm[0] * b_, nrm[1] * b_)
            serifs.extend(pair)
        elif log is not None and pair:
            log.append((P, None, False, None))  # its partner, dropped with it
    g = unary_union([body] + serifs).intersection(box(-1, 0, w + 1, 1)).buffer(0)
    # round EVERY corner, inside and out (Alex: square corners next to the
    # round serifs "just look weird"): close (fills inside corners, e.g.
    # where a half-circle meets its stroke) then open (rounds outside ones)
    if f > 0:
        g = g.buffer(f, quad_segs=12).buffer(-2 * f, quad_segs=12).buffer(f, quad_segs=12)
    return g, w


class GeoFont:
    """Stand-in for a PIL FreeTypeFont at `size` px: cap height size*CAP_RATIO."""

    def __init__(self, size, serif_mm=None, ppm=5.0):
        self.size = size
        self.cap = size * CAP_RATIO
        self.serif_mm, self.ppm = serif_mm, ppm
        self._cache = {}

    def getbbox(self, text):
        return (0, -self.cap, self.getlength(text), 0)

    def getlength(self, text):
        return sum((self.glyph(c).bounds[2] - self.glyph(c).bounds[0]) + 2 * SIDE * self.cap
                   if c in LETTERS else 0.4 * self.cap for c in text)

    def glyph(self, ch):
        """Outline in glyph_local's frame: x centred on the letter's outline
        (serifs included), baseline y=0, y DOWN (cap line at -cap)."""
        if ch not in self._cache:
            g, _w = glyph_units(ch, self.serif_mm, self.cap / self.ppm)
            b = g.bounds
            g = affinity.translate(g, -(b[0] + b[2]) / 2, 0)
            self._cache[ch] = affinity.scale(g, self.cap, -self.cap, origin=(0, 0))
        return self._cache[ch]


def specimen(out, text="ABCDEFGHIJKLMNOPQRSTUVWXYZ", cap_mm=18.0):
    from PIL import Image, ImageDraw

    k = 8.0  # px per mm
    rows = [text[:13], text[13:]]
    W, Hh = int(19 * cap_mm * k), int((len(rows) * 1.7 + 0.6) * cap_mm * k)
    img = Image.new("RGB", (W, Hh), "white")
    d = ImageDraw.Draw(img)
    f = GeoFont(cap_mm * 5.0 / CAP_RATIO, ppm=5.0)
    for ri, row in enumerate(rows):
        x = 0.5 * cap_mm * k
        base = (ri * 1.7 + 1.4) * cap_mm * k
        d.text((6, base - 1.3 * cap_mm * k), f"3mm half-circle tab serifs, {cap_mm:.0f}mm cap, stroke "
               f"{STROKE * cap_mm:.1f}mm", fill=(0, 0, 0))
        for ch in row:
            g = f.glyph(ch)
            adv = f.getlength(ch) / 5.0 * k
            for poly in (g.geoms if g.geom_type == "MultiPolygon" else [g]):
                pts = [(x + adv / 2 + px / 5.0 * k, base + py / 5.0 * k) for px, py in poly.exterior.coords]
                d.polygon(pts, fill=(200, 70, 70))
                for hole in poly.interiors:
                    d.polygon([(x + adv / 2 + px / 5.0 * k, base + py / 5.0 * k) for px, py in hole.coords],
                              fill="white")
            x += adv
    img.save(out)


if __name__ == "__main__":
    specimen(sys.argv[1] if len(sys.argv) > 1 else "geo_specimen.png")
