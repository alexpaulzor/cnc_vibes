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
NOTCH_MIN_MM = 4.5  # a serif bump keeps this far from the letter's other strokes


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
    w = 0.70
    # middle bar short of the others: the top/bottom bar ends' serif bumps
    # then clear it and stay (a bump facing another bar head-on is dropped)
    mb = w - h - 0.27
    L["E"] = (w, [[(h, 0), (h, 1)], [(h, top), (w - h, top)], [(h, 0.52), (mb, 0.52)],
                  [(h, bot), (w - h, bot)]])
    L["F"] = (w, [[(h, 0), (h, 1)], [(h, top), (w - h, top)], [(h, 0.52), (mb, 0.52)]])
    L["G"] = (1.0, [_arc(0.5, 0.5, R, 42, 360), [(0.5 + R, 0.5), (0.56, 0.5)]])
    w = 0.80
    L["H"] = (w, [[(h, 0), (h, 1)], [(w - h, 0), (w - h, 1)], [(h, 0.52), (w - h, 0.52)]])
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
    L["Q"] = (1.0, [_arc(0.5, 0.5, R, 0, 360), [(0.62, 0.30), (0.98, 0)]])
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


def glyph_units(ch, serif_mm=3.0, cap_mm=18.0, stroke=STROKE):
    """Letter outline in cap units (y up, baseline 0), and its width."""
    w, strokes = LETTERS[ch]
    s = stroke
    r = serif_mm / cap_mm / 2  # serif semicircle radius, cap units
    # a slanted stroke ending at the letter's side (A, V, W, X, Y feet and
    # tops): move its end in so the stroke's OUTER edge, not its centre
    # line, lands on the side
    fixed = []
    for pl in strokes:
        pl = list(pl)
        for k, nb in ((0, 1), (-1, -2)):
            (x, y), (x2, y2) = pl[k], pl[nb]
            if (y <= 1e-6 or y >= 1 - 1e-6) and (x <= 1e-6 or x >= w - 1e-6) and abs(x2 - x) > 1e-6:
                sin = abs(y2 - y) / math.hypot(x2 - x, y2 - y)
                hw = (s / 2) / max(sin, 0.3)
                pl[k] = (hw if x <= 1e-6 else w - hw, y)
        fixed.append(pl)
    strokes = fixed
    lines = [LineString(p) for p in strokes]
    ends = []  # (point, outward direction, free?)
    ext = []
    for i, pl in enumerate(strokes):
        others = unary_union([ln for j, ln in enumerate(lines) if j != i]) if len(lines) > 1 else None
        for k in (0, -1):
            p = pl[k]
            q = pl[1] if k == 0 else pl[-2]
            d = (p[0] - q[0], p[1] - q[1])
            n = math.hypot(*d) or 1.0
            d = (d[0] / n, d[1] / n)
            closed = math.dist(pl[0], pl[-1]) < 1e-6  # O, Q's ring: no ends at all
            free = not closed and (others is None or others.distance(Point(p)) > 0.6 * s)
            ends.append((p, d, free, len(pl) > 3))  # curved stroke (an arc)
        # ends on the baseline / cap line: extend past it, then clip flat
        q = list(pl)
        for k in (0, -1):
            if q[k][1] <= 1e-6 or q[k][1] >= 1 - 1e-6:
                q = _extend(q, k, 2 * s)
        ext.append(LineString(q))
    body = unary_union([ln.buffer(s / 2, cap_style="flat", join_style="round") for ln in ext])
    # round joins only where strokes meet: give joined (non-free) ends a round cap
    # round over a joint only where two stroke ENDS meet (W's peak, M's
    # middle): an end landing part-way along another stroke (A's crossbar)
    # would poke a bump out past it
    tips = [p for p, _d, _f, _c in ends]
    joints = [Point(p).buffer(s / 2, quad_segs=16) for p, _d, free, _c in ends
              if not free and 1e-6 < p[1] < 1 - 1e-6
              and sum(1 for q in tips if math.dist(p, q) < 0.6 * s) >= 2]
    # clip to the letter's own box: no spur where N's / M's diagonal meets
    # a stem at the cap line or baseline
    body = unary_union([body] + joints).intersection(box(0, 0, w, 1))
    serifs = []
    notch = NOTCH_MIN_MM / cap_mm  # serif must leave this much room to other strokes
    stroke_bodies = [ln.buffer(s / 2, cap_style="flat") for ln in ext]
    for p, d, free, curved in ends:
        if not free:
            continue
        own = min(range(len(ext)), key=lambda i: ext[i].distance(Point(p)))
        others = unary_union([b for i, b in enumerate(stroke_bodies) if i != own])
        if curved and 1e-6 < p[1] < 1 - 1e-6:
            # a curve's mid-air end (C, G, S, J): a ball terminal as wide as
            # a stem's serif bulb (stroke + 2 x serif radius)
            rb = s / 2 + r
            serifs.append(Point(p[0] - d[0] * r, p[1] - d[1] * r).buffer(rb, quad_segs=24))
            continue
        # the end's two corners: on the baseline / cap line where the stroke
        # is clipped flat, else square across the stroke end
        if p[1] <= 1e-6 or p[1] >= 1 - 1e-6:
            y = 0.0 if p[1] <= 1e-6 else 1.0
            seg = body.intersection(LineString([(p[0] - 3 * s, y), (p[0] + 3 * s, y)]))
            xa, _y0, xb, _y1 = seg.bounds
            corners = [(xa, y), (xb, y)]
        else:
            nx, ny = -d[1], d[0]
            corners = [(p[0] + nx * s / 2, p[1] + ny * s / 2), (p[0] - nx * s / 2, p[1] - ny * s / 2)]
        mid = ((corners[0][0] + corners[1][0]) / 2, (corners[0][1] + corners[1][1]) / 2)
        n_before = len(serifs)
        for c in corners:
            # half a circle of diameter serif_mm, flat side ON the stroke's
            # side edge, starting at the corner and running back along the
            # stroke, bulging outward: with its mirror on the other side the
            # stroke end becomes a puzzle-tab bulb on a stroke-wide neck
            o = (c[0] - mid[0], c[1] - mid[1])
            on = math.hypot(*o) or 1.0
            o = (o[0] / on, o[1] / on)
            cc = (c[0] - d[0] * r, c[1] - d[1] * r)
            disc = Point(cc).buffer(r, quad_segs=24)
            half = Polygon([(cc[0] + d[0] * 2 * r, cc[1] + d[1] * 2 * r), (cc[0] - d[0] * 2 * r, cc[1] - d[1] * 2 * r),
                            (cc[0] - d[0] * 2 * r + o[0] * 2 * r, cc[1] - d[1] * 2 * r + o[1] * 2 * r),
                            (cc[0] + d[0] * 2 * r + o[0] * 2 * r, cc[1] + d[1] * 2 * r + o[1] * 2 * r)])
            bump = disc.intersection(half)
            # inside a narrow opening (between E's arms, F's) a bulge would
            # pinch the surround's tongue of wood to a breakable neck: skip it
            # -- only when another stroke lies straight AHEAD of the bulge
            # (L's and T's bar ends sit near their stem sideways, and keep it)
            ahead = unary_union([bump, affinity.translate(bump, o[0] * notch, o[1] * notch)]).convex_hull
            if not others.is_empty and ahead.intersects(others):
                continue
            serifs.append(bump)
        if len(serifs) == n_before and 1e-6 < p[1] < 1 - 1e-6:
            # both bumps dropped (Q's tail inside the bowl): round the end
            # off rather than leave it square
            serifs.append(Point(p).buffer(s / 2, quad_segs=16))
    g = unary_union([body] + serifs).intersection(box(-1, 0, w + 1, 1)).buffer(0)
    return g, w


class GeoFont:
    """Stand-in for a PIL FreeTypeFont at `size` px: cap height size*CAP_RATIO."""

    def __init__(self, size, serif_mm=3.0, ppm=5.0):
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
