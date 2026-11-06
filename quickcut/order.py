"""Cut ordering: middle-out, with as little laser-off travel as possible.

Middle-out (non-negotiable): a cut must come before every closed loop that contains it -- holes and
inner details before the outline around them -- so a part never comes loose while it still has cuts
to go (it would shift, and the rest of its cuts would land in the wrong place).

Within that rule, cuts are chained to keep travel short:
  1. a precedence graph from a containment test (shapely, STRtree-accelerated);
  2. greedy nearest-next among the cuts whose containers' contents are all done, entering each
     closed loop at its vertex nearest the head and each open path from its nearer end;
  3. a precedence-safe improvement pass (or-opt: move a cut elsewhere if that shortens the tour and
     no containment rule breaks).

    from order import order_middle_out
    cuts = order_middle_out(cuts, start=(0, 0))       # list[Polyline] -> list[Polyline]

`order_by_area` keeps the old rule (smallest area first): also middle-out, but travel-blind.
"""
from __future__ import annotations

import math

from shapely.geometry import LineString, Point, Polygon
from shapely.strtree import STRtree

from motion import signed_area


def _closed(pl) -> bool:
    return pl.closed and len(pl.points) >= 4


def order_by_area(polylines):
    """the old rule: open paths, then loops smallest first (stable)"""
    def area(pl):
        return abs(signed_area(pl.points)) if _closed(pl) else 0.0
    return sorted(polylines, key=area)


def precedence(polylines):
    """before[i] = set of cut indices that must come before cut i (i.e. that i contains)"""
    n = len(polylines)
    polys = {i: Polygon(pl.points).buffer(0) for i, pl in enumerate(polylines) if _closed(pl)}
    geoms = [LineString(pl.points) if len(pl.points) > 1 else Point(pl.points[0]) for pl in polylines]
    before = [set() for _ in range(n)]
    if not polys:
        return before
    keys = list(polys)
    tree = STRtree([polys[k] for k in keys])
    for i, g in enumerate(geoms):
        for hit in tree.query(g):
            j = keys[int(hit)]
            if j == i:
                continue
            pj = polys[j]
            if pj.area <= (polys[i].area if i in polys else 0.0):
                continue                       # a loop can't be inside a smaller one
            if pj.buffer(1e-6).contains(g):
                before[j].add(i)               # i lies inside loop j: i first
    return before


def _entry(pl, pos):
    """(distance to start, oriented points) for entering `pl` from `pos`"""
    pts = pl.points
    if _closed(pl):
        ring = pts[:-1] if pts[0] == pts[-1] else pts
        k = min(range(len(ring)), key=lambda t: math.dist(ring[t], pos))
        rot = ring[k:] + ring[:k]
        return math.dist(ring[k], pos), rot + [rot[0]]
    a, b = math.dist(pts[0], pos), math.dist(pts[-1], pos)
    return (a, pts) if a <= b else (b, pts[::-1])


def travel(polylines, start=(0.0, 0.0)):
    """laser-off travel of cutting them in this order, as given (mm)"""
    pos, total = start, 0.0
    for pl in polylines:
        total += math.dist(pos, pl.points[0])
        pos = pl.points[-1]
    return total


def order_middle_out(polylines, start=(0.0, 0.0), improve=True, rotate=True):
    """middle-out order with short travel; returns new Polyline objects (same class)"""
    cls = type(polylines[0]) if polylines else None
    n = len(polylines)
    if n < 2:
        return list(polylines)
    before = precedence(polylines)
    done, out, pos = set(), [], start
    while len(done) < n:
        ready = [i for i in range(n) if i not in done and before[i] <= done]
        best = None
        for i in ready:
            d, pts = _entry(polylines[i], pos) if rotate else (math.dist(polylines[i].points[0], pos), polylines[i].points)
            if best is None or d < best[0]:
                best = (d, i, pts)
        _, i, pts = best
        done.add(i)
        out.append((i, pts))
        pos = pts[-1]
    if improve:
        out = _or_opt(out, before, start)
    return [cls(pts, polylines[i].closed) for i, pts in out]


def _tour(seq, start):
    pos, total = start, 0.0
    for _, pts in seq:
        total += math.dist(pos, pts[0])
        pos = pts[-1]
    return total


def _valid(seq, before):
    seen = set()
    for i, _ in seq:
        if not before[i] <= seen:
            return False
        seen.add(i)
    return True


def _or_opt(seq, before, start, rounds=3):
    """move single cuts to a better slot while it shortens the tour and keeps middle-out"""
    best = _tour(seq, start)
    for _ in range(rounds):
        improved = False
        for a in range(len(seq)):
            item = seq[a]
            rest = seq[:a] + seq[a + 1:]
            for b in range(len(rest) + 1):
                if b == a:
                    continue
                cand = rest[:b] + [item] + rest[b:]
                t = _tour(cand, start)
                if t + 1e-6 < best and _valid(cand, before):
                    seq, best, improved = cand, t, True
                    break
        if not improved:
            break
    return seq
