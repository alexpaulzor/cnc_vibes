"""Path clean-up between geometry and emission: fusing, bridges, re-cut trimming.

Chains are plain lists of (x, y) points in machine mm (open, or closed with first == last).
gcode.py re-exports what an emitter needs; nothing here writes G-code.

  fuse_touching(chains, tol)        consecutive chains whose end meets the next start become one
                                    (free continuity: no lift / re-fire at the same spot)
  bridge_chains(chains, pts, gap)   leave `gap` of uncut line centred on each bridge point
  CutIndex / trim_recut_ends        drop leading/trailing segments an earlier path already cut
"""
from __future__ import annotations

import math

from shapely.geometry import LineString
from shapely.ops import substring, unary_union
from shapely.strtree import STRtree


def fuse_touching(chains, tol: float = 0.1):
    """fuse consecutive chains whose join coincides (end of one == start of the next, within tol);
    chains of fewer than 2 points are dropped"""
    out = []
    for ch in chains:
        ch = list(ch)
        if len(ch) < 2:
            continue
        if out and math.hypot(ch[0][0] - out[-1][-1][0], ch[0][1] - out[-1][-1][1]) <= tol:
            out[-1] = out[-1] + ch[1:]
        else:
            out.append(ch)
    return out


def bridge_chains(chains, bridge_points, gap_mm: float, tol_mm: float = 0.3):
    """Leave an uncut `gap_mm` of cut line centred on each bridge point.
    Every pass of a chain within tol_mm of a point is split there (a re-trace
    over the same spot is gapped too). Closed chains split into one open path
    that starts and ends at the gaps."""
    out = []
    for ch in chains:
        ch = [tuple(p) for p in ch]
        cum = [0.0]
        for a, b in zip(ch, ch[1:]):
            cum.append(cum[-1] + math.dist(a, b))
        total = cum[-1]
        cuts = []
        for bx, by in bridge_points:
            best = None  # (distance, arc position) of each separate pass
            for k, (a, b) in enumerate(zip(ch, ch[1:])):
                seg = math.dist(a, b)
                if seg == 0:
                    continue
                t = max(0.0, min(1.0, ((bx - a[0]) * (b[0] - a[0]) + (by - a[1]) * (b[1] - a[1])) / seg ** 2))
                d = math.dist((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])), (bx, by))
                s_pos = cum[k] + t * seg
                if d < tol_mm:
                    if best is not None and s_pos - best[1] <= gap_mm:
                        if d < best[0]:
                            best = (d, s_pos)
                        continue
                    if best is not None:
                        cuts.append((best[1] - gap_mm / 2, best[1] + gap_mm / 2))
                    best = (d, s_pos)
            if best is not None:
                cuts.append((best[1] - gap_mm / 2, best[1] + gap_mm / 2))
        if not cuts:
            out.append(ch)
            continue
        cuts.sort()
        line = LineString(ch)
        keep, pos = [], 0.0
        for lo, hi in cuts:
            if lo > pos:
                keep.append((pos, lo))
            pos = max(pos, hi)
        if pos < total:
            keep.append((pos, total))
        parts = [list(substring(line, lo, hi).coords) for lo, hi in keep if hi - lo > 1e-6]
        closed = math.dist(ch[0], ch[-1]) < 1e-6
        if closed and len(parts) > 1 and keep[0][0] == 0.0 and keep[-1][1] == total:
            parts = [parts[-1] + parts[0][1:]] + parts[1:-1]
        out.extend(p for p in parts if len(p) >= 2)
    return out


class CutIndex:
    """Already-cut line, for spotting re-cut segments geometrically (vertex keys miss them: the
    same edge decimates to different points in different chains)."""

    def __init__(self, tol_mm=0.05):
        self.tol = tol_mm
        self.segs = []
        self.tree = None
        self.dirty = False

    def add(self, coords):
        self.segs += [LineString([a, b]) for a, b in zip(coords, coords[1:]) if a != b]
        self.dirty = True

    def covered(self, a, b):
        if not self.segs:
            return False
        if self.dirty:
            self.tree, self.dirty = STRtree(self.segs), False
        seg = LineString([a, b])
        near = [self.segs[i] for i in self.tree.query(seg.buffer(self.tol))]
        if not near:
            return False
        return seg.difference(unary_union(near).buffer(self.tol)).length < self.tol


def trim_recut_ends(coords, cut: CutIndex):
    """Drop leading/trailing segments an earlier path already cut (a re-trace only earns its keep
    BETWEEN two new cuts; at a path's end the laser restarts anyway)."""
    i, j = 0, len(coords) - 1
    while i < j and cut.covered(coords[i], coords[i + 1]):
        i += 1
    while j > i and cut.covered(coords[j - 1], coords[j]):
        j -= 1
    return list(coords[i:j + 1])
