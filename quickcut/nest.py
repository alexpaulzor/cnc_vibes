"""quickcut.nest -- put part outlines onto stock sheets (bottom-left fill on the real shapes).

Each part (biggest first) goes, turned by one of `angles`, at the spot with the lowest top edge (then
lowest, then leftmost) where it keeps `gap` from everything already on the sheet, so parts tuck into
each other's open cut-outs and around round parts. A part that fits no listed angle on an empty sheet
is tried every `fine_step` degrees (a long bar goes corner to corner). Parts that don't fit go on the
next sheet.

    sheets = nest([(name, polygon), ...], Bed(298, 292.5, margin=2))
    for sheet in sheets:
        for p in sheet:          # Placement(name, geom, angle, dx, dy)
            moved = p.apply(other_geometry_in_the_same_frame)
"""
from __future__ import annotations

from dataclasses import dataclass

from shapely import affinity
from shapely.ops import unary_union
from shapely.prepared import prep


@dataclass(frozen=True)
class Bed:
    x_max: float           # furthest X a cut may reach (stock width less the edge margin, within machine reach)
    y_max: float
    margin: float = 3.0    # X/Y of the first usable corner


@dataclass
class Placement:
    name: str
    geom: object           # the placed polygon
    angle: float           # degrees CCW about (0, 0), then...
    dx: float              # ...this translation
    dy: float

    def apply(self, g):
        return affinity.translate(affinity.rotate(g, self.angle, origin=(0, 0)), self.dx, self.dy)


def _fit(g, angle, bed, occ, coll_tol, gap, step, best):
    r = affinity.rotate(g, angle, origin=(0, 0))
    x0, y0, x1, y1 = r.bounds
    w, h = x1 - x0, y1 - y0
    if bed.margin + w > bed.x_max or bed.margin + h > bed.y_max:
        return best
    coll = affinity.translate(r, -x0, -y0).simplify(coll_tol).buffer(gap / 2, quad_segs=4)
    y = bed.margin
    while y + h <= bed.y_max and (best is None or y + h <= best[0][0]):
        x = bed.margin
        while x + w <= bed.x_max:
            if occ is None or not occ.intersects(affinity.translate(coll, x, y)):
                key = (y + h, y, x)
                if best is None or key < best[0]:
                    best = (key, angle, x - x0, y - y0)
                return best
            x += step
        y += step
    return best


def nest(items, bed: Bed, gap: float = 4.0, step: float = 2.0, angles=(0, 90, 180, 270),
         fine_step: float = 5.0, coll_tol: float = 0.3):
    """[(name, polygon), ...] -> sheets: [[Placement, ...], ...]; raises ValueError if a part fits no sheet"""
    todo = sorted(items, key=lambda it: -it[1].area)
    sheets = []
    while todo:
        placed, occ, left = [], None, []
        for name, g in todo:
            best = None
            for a in angles:
                best = _fit(g, a, bed, occ, coll_tol, gap, step, best)
            if best is None and occ is None:          # fits no right angle even alone: try every few degrees
                a = 0.0
                while a < 180:
                    best = _fit(g, a, bed, occ, coll_tol, gap, step, best)
                    a += fine_step
            if best is None:
                left.append((name, g))
                continue
            _, a, dx, dy = best
            p = Placement(name, None, a, dx, dy)
            p.geom = p.apply(g)
            placed.append(p)
            occ = prep(unary_union([q.geom.buffer(gap / 2, quad_segs=4) for q in placed]))
        if not placed:
            names = ", ".join(n for n, _ in left)
            raise ValueError(f"does not fit a {bed.x_max:g} x {bed.y_max:g} bed at any angle: {names}")
        sheets.append(placed)
        todo = left
    return sheets
