"""Polyline -> G2/G3 arcs + G1 lines within a chord tolerance (ArcWelder-style).

No maintained Python package does this (ArcWelder itself is a C++ OctoPrint
plugin; pygcode/gcodeparser only parse; vpype only simplifies lines), so this
is a small greedy fitter on the same idea:

  from each point, grow a run of polyline vertices as long as ONE circular arc
  (through the run's first and last point, so the G2/G3 endpoint is exact and
  GRBL's start/end radius check passes) stays within `tol_mm` of every vertex
  and every segment midpoint, sweeps one way, and stays under `max_radius_mm`.
  Runs that can't form an arc of >= `min_arc_points` vertices are merged into
  straight G1s with the same tolerance (Douglas-Peucker on that run).

Every primitive starts where the previous one ended, so `primitives_to_points`
(dense re-sampling) passes within tol_mm of every input vertex -- the tests
check exactly that.
"""

from __future__ import annotations

import math

from shapely.geometry import LineString

Point = tuple[float, float]


def _circle_through(a, b, c):
    ax, ay = a
    bx, by = b
    cx, cy = c
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return None
    a2, b2, c2 = ax * ax + ay * ay, bx * bx + by * by, cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    return ux, uy


def _cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _max_dev(pts, i, j, cx, cy, r):
    return max(abs(math.hypot(pts[k][0] - cx, pts[k][1] - cy) - r) for k in range(i, j + 1))


def _try_arc(pts, i, j, tol, max_r, sag_tol=None):
    """Best arc through pts[i] and pts[j] (center on their perpendicular
    bisector, so the G2/G3 endpoint is exact): golden-section search on the
    bisector offset minimising the worst vertex's radial error, seeded by the
    circle through the start, middle and end vertices. Returns
    (cx, cy, r, ccw) if every vertex in i..j is within `tol` and the run
    sweeps one way by less than a full turn, else None."""
    if j - i < 2:
        return None
    a, b = pts[i], pts[j]
    chord = math.dist(a, b)
    if chord < 1e-9:  # closed run: let a shorter run handle it
        return None
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    nx, ny = -(b[1] - a[1]) / chord, (b[0] - a[0]) / chord
    cen = _circle_through(a, pts[(i + j) // 2], b)
    if cen is None:
        return None
    t0 = (cen[0] - mx) * nx + (cen[1] - my) * ny

    def dev(t):
        cx, cy = mx + t * nx, my + t * ny
        return _max_dev(pts, i, j, cx, cy, math.hypot(a[0] - cx, a[1] - cy))

    half = max(abs(t0) * 0.5, chord)
    lo, hi = t0 - half, t0 + half
    g = (math.sqrt(5) - 1) / 2
    c1, c2 = hi - g * (hi - lo), lo + g * (hi - lo)
    f1, f2 = dev(c1), dev(c2)
    for _ in range(40):
        if f1 < f2:
            hi, c2, f2 = c2, c1, f1
            c1 = hi - g * (hi - lo)
            f1 = dev(c1)
        else:
            lo, c1, f1 = c1, c2, f2
            c2 = lo + g * (hi - lo)
            f2 = dev(c2)
        if hi - lo < 1e-5:
            break
    t = (lo + hi) / 2
    if dev(t) > tol:
        return None
    cx, cy = mx + t * nx, my + t * ny
    r = math.hypot(a[0] - cx, a[1] - cy)
    if r > max_r:
        return None
    # between vertices the arc bulges away from each chord by its sagitta;
    # a long chord is a genuinely straight edge (a circle through a
    # rectangle's 4 corners fits every VERTEX exactly), so cap that bulge
    sag_tol = tol if sag_tol is None else sag_tol
    for k in range(i, j):
        half = math.dist(pts[k], pts[k + 1]) / 2
        if r - math.sqrt(max(0.0, r * r - half * half)) > sag_tol:
            return None
    # one rotation direction: every vertex's swept angle increases
    a0 = math.atan2(a[1] - cy, a[0] - cx)
    turn = sum(_cross((cx, cy), pts[k], pts[k + 1]) for k in range(i, j))
    sgn = 1 if turn > 0 else -1
    prev = 0.0
    for k in range(i + 1, j + 1):
        p = pts[k]
        sweep = (sgn * (math.atan2(p[1] - cy, p[0] - cx) - a0)) % (2 * math.pi)
        if sweep + 1e-9 < prev:
            return None
        prev = sweep
    if prev > 2 * math.pi - 1e-6:
        return None
    return cx, cy, r, sgn > 0


def fit_arcs(points: list[Point], tol_mm: float = 0.02, min_arc_points: int = 4,
             max_radius_mm: float = 500.0, source_tol_mm: float = 0.05) -> list[tuple]:
    """Primitives covering `points`.

    tol_mm: max distance from any input vertex to the arc.
    source_tol_mm: how far the input chords may already be from the true
      curve (the emitter simplifies outlines at 0.05mm): an arc may bulge
      up to tol_mm + source_tol_mm from a chord, never more.

    Returns ("G1", (x, y)) and ("G2"|"G3", (x, y), (i, j)) with I/J relative
    to the primitive's own start point, as GRBL wants."""
    sag_tol = tol_mm + source_tol_mm
    pts = [tuple(p) for p in points]
    n = len(pts)
    out = []
    i = 0
    pending_line = [pts[0]] if pts else []
    while i < n - 1:
        best_j, best = None, None
        j = i + 2
        # grow while it fits; allow a couple of misses (a vertex set that
        # fits at j+2 can fail at j+1 on a near-tangent 3-point fit)
        misses = 0
        while j < n and misses <= 2:
            arc = _try_arc(pts, i, j, tol_mm, max_radius_mm, sag_tol)
            if arc:
                best_j, best, misses = j, arc, 0
            else:
                misses += 1
            j += 1
        if best is not None and best_j - i + 1 >= min_arc_points:
            _flush_lines(out, pending_line, tol_mm)
            cx, cy, _r, ccw = best
            x0, y0 = pts[i]
            out.append(("G3" if ccw else "G2", pts[best_j], (cx - x0, cy - y0)))
            i = best_j
            pending_line = [pts[i]]
        else:
            pending_line.append(pts[i + 1])
            i += 1
    _flush_lines(out, pending_line, tol_mm)
    return out


def _flush_lines(out, run, tol):
    if len(run) < 2:
        return
    simp = list(LineString(run).simplify(tol, preserve_topology=False).coords)
    for p in simp[1:]:
        out.append(("G1", (p[0], p[1])))


def primitives_to_points(start: Point, prims, step_mm: float = 0.05) -> list[Point]:
    """Densely re-sample primitives (for renders, lint and tolerance tests)."""
    pts = [start]
    x, y = start
    for pr in prims:
        if pr[0] == "G1":
            pts.append(pr[1])
        else:
            (ex, ey), (ci, cj) = pr[1], pr[2]
            cx, cy = x + ci, y + cj
            r = math.hypot(ci, cj)
            a0 = math.atan2(y - cy, x - cx)
            a1 = math.atan2(ey - cy, ex - cx)
            if pr[0] == "G3":
                while a1 <= a0 + 1e-12:
                    a1 += 2 * math.pi
            else:
                while a1 >= a0 - 1e-12:
                    a1 -= 2 * math.pi
            n = max(2, int(abs(a1 - a0) * r / step_mm))
            for k in range(1, n):
                a = a0 + (a1 - a0) * k / n
                pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
            pts.append((ex, ey))
        x, y = pts[-1]
    return pts


def prims_to_gcode(prims, s_words=None, fmt="{:.3f}") -> list[str]:
    """G-code lines for primitives; `s_words[k]` (optional) appends S to line k."""
    out = []
    for k, pr in enumerate(prims):
        x, y = pr[1]
        line = f"{pr[0]} X{fmt.format(x)} Y{fmt.format(y)}"
        if pr[0] != "G1":
            line += f" I{fmt.format(pr[2][0])} J{fmt.format(pr[2][1])}"
        if s_words is not None and s_words[k] is not None:
            line += f" S{s_words[k]}"
        out.append(line)
    return out


def simplify_lines(points: list[Point], tol_mm: float) -> list[Point]:
    """Tolerance-based downsampling baseline (Douglas-Peucker)."""
    return list(LineString(points).simplify(tol_mm, preserve_topology=False).coords)
