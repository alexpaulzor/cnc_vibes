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


# ---------------------------------------------------------------------------
# Smoothing fit: tangent-continuous arcs within a max shape change
# ---------------------------------------------------------------------------
#
# fit_arcs() above keeps every endpoint ON an input vertex, so on a noisy
# traced outline each arc meets the next at a small kink -- and GRBL slows at
# every kink ($11 junction deviation). fit_smooth_arcs() instead lets the path
# move off the traced outline by up to `max_dev_mm` (the approved budget:
# less than the laser line width) and makes each new arc START TANGENT to the
# previous one, so consecutive moves join without a corner. Where no tangent
# arc fits (a genuine corner sharper than max_dev_mm can round off, or a
# reversal) it starts a fresh, untangented primitive -- a real corner stays a
# corner.


def _pt_seg_dist(P, A, B):
    """Distances from points P (m,2) to segments A->B (k,2): (m,) minimum."""
    import numpy as np

    AB = B - A
    L2 = np.maximum((AB * AB).sum(1), 1e-18)
    AP = P[:, None, :] - A[None, :, :]
    t = np.clip((AP * AB[None]).sum(2) / L2[None], 0.0, 1.0)
    D = AP - t[..., None] * AB[None]
    return np.sqrt((D * D).sum(2)).min(1)


def _sample(P, prim, step):
    return primitives_to_points(P, [prim], step_mm=step)


def _end_tangent(P, prim):
    if prim[0] == "G1":
        dx, dy = prim[1][0] - P[0], prim[1][1] - P[1]
        n = math.hypot(dx, dy)
        return (dx / n, dy / n) if n > 1e-12 else None
    cx, cy = P[0] + prim[2][0], P[1] + prim[2][1]
    ex, ey = prim[1]
    rx, ry = ex - cx, ey - cy
    r = math.hypot(rx, ry)
    return (-ry / r, rx / r) if prim[0] == "G3" else (ry / r, -rx / r)


def _tangent_prim(P, T, pts, i, j, max_r):
    """Arc (or line) leaving P along unit tangent T, curvature least-squares
    fitted to pts[i+1..j], ending at pts[j]'s projection onto it."""
    tx, ty = T
    nx, ny = -ty, tx  # left normal
    num = den = 0.0
    for k in range(i + 1, j + 1):
        dx, dy = pts[k][0] - P[0], pts[k][1] - P[1]
        t, n = dx * tx + dy * ty, dx * nx + dy * ny
        s = t * t + n * n
        num += n * s
        den += s * s
    if den < 1e-18:
        return None
    kappa = 2 * num / den  # signed, left-positive
    qx, qy = pts[j]
    if abs(kappa) < 1.0 / max_r:
        t = (qx - P[0]) * tx + (qy - P[1]) * ty
        if t <= 1e-6:
            return None
        return ("G1", (P[0] + t * tx, P[1] + t * ty))
    R = 1.0 / kappa  # signed
    cx, cy = P[0] + nx * R, P[1] + ny * R
    vx, vy = qx - cx, qy - cy
    vn = math.hypot(vx, vy)
    if vn < 1e-9:
        return None
    r = abs(R)
    ex, ey = cx + vx / vn * r, cy + vy / vn * r
    if math.dist((ex, ey), P) < 1e-6:
        return None
    return ("G3" if kappa > 0 else "G2", (ex, ey), (cx - P[0], cy - P[1]))


def _free_prims(P, pts, i, j, max_r):
    """Untangented candidates from P toward pts[j]: a line, and the arc
    through P, the middle vertex and pts[j]."""
    out = [("G1", tuple(pts[j]))]
    if j - i >= 2:
        m = pts[(i + 1 + j) // 2]
        cen = _circle_through(P, m, pts[j])
        if cen is not None and math.hypot(P[0] - cen[0], P[1] - cen[1]) <= max_r:
            ccw = _cross(P, m, pts[j]) > 0
            out.append(("G3" if ccw else "G2", tuple(pts[j]), (cen[0] - P[0], cen[1] - P[1])))
    return out


def _fits(P, prim, pts, i, j, A, B, max_dev, step):
    """Shape check, both ways: every vertex i+1..j within max_dev of the new
    move, every point of the new move within max_dev of the traced polyline
    near it, the move sweeps < 270deg, and it visits the vertices in order
    (so an out-and-back never collapses onto a one-way arc)."""
    import numpy as np

    if prim[0] != "G1":
        r = math.hypot(*prim[2])
        cx, cy = P[0] + prim[2][0], P[1] + prim[2][1]
        a0 = math.atan2(P[1] - cy, P[0] - cx)
        a1 = math.atan2(prim[1][1] - cy, prim[1][0] - cx)
        sweep = (a1 - a0) % (2 * math.pi) if prim[0] == "G3" else (a0 - a1) % (2 * math.pi)
        if sweep > 1.5 * math.pi:
            return False
    S = np.array(_sample(P, prim, step))
    V = np.array(pts[i + 1: j + 1])
    lo, hi = max(0, i - 1), min(len(pts) - 1, j + 1)
    if _pt_seg_dist(V, S[:-1], S[1:]).max() > max_dev:
        return False
    if _pt_seg_dist(S, A[lo:hi], B[lo:hi]).max() > max_dev:
        return False
    # order: each vertex's nearest sample index must not go backwards
    d2 = ((V[:, None, :] - S[None, :, :]) ** 2).sum(2)
    idx = d2.argmin(1)
    back = np.maximum.accumulate(idx) - idx
    return bool((back * step <= max_dev + step).all())


def _arc_to(P, T, E, max_r):
    """Arc leaving P along unit tangent T and ending exactly at E (a line if
    E is straight ahead)."""
    wx, wy = E[0] - P[0], E[1] - P[1]
    nx, ny = -T[1], T[0]
    wn = wx * nx + wy * ny
    ww = wx * wx + wy * wy
    if ww < 1e-18:
        return None
    if abs(wn) < 1e-12 or ww / (2 * abs(wn)) > max_r:
        if wx * T[0] + wy * T[1] <= 0:
            return None
        return ("G1", tuple(E))
    R = ww / (2 * wn)  # signed, left-positive
    return ("G3" if R > 0 else "G2", tuple(E), (nx * R, ny * R))


def _biarc(P0, T0, P1, T1, max_r):
    """Two tangent-continuous arcs from (P0, T0) to (P1, T1), equal-tangent-
    length joint (the classic biarc). None if it doesn't exist."""
    vx, vy = P1[0] - P0[0], P1[1] - P0[1]
    tx, ty = T0[0] + T1[0], T0[1] + T1[1]
    vt = vx * tx + vy * ty
    vv = vx * vx + vy * vy
    a = 2 * (1 - (T0[0] * T1[0] + T0[1] * T1[1]))
    if a < 1e-9:
        if abs(vt) < 1e-12:
            return None
        d = vv / (2 * vt)
    else:
        disc = vt * vt + a * vv
        d = (-vt + math.sqrt(disc)) / a
    if d <= 1e-9:
        return None
    q0 = (P0[0] + d * T0[0], P0[1] + d * T0[1])
    q1 = (P1[0] - d * T1[0], P1[1] - d * T1[1])
    J = ((q0[0] + q1[0]) / 2, (q0[1] + q1[1]) / 2)
    jx, jy = q1[0] - q0[0], q1[1] - q0[1]
    jn = math.hypot(jx, jy)
    if jn < 1e-12:
        return None
    a1 = _arc_to(P0, T0, J, max_r)
    a2 = _arc_to(J, (jx / jn, jy / jn), P1, max_r)
    if a1 is None or a2 is None:
        return None
    return [a1, a2]


def _tangents(pts):
    """Unit tangents of a densely sampled smooth polyline (central
    differences; one-sided at open ends, wrapped for a closed loop)."""
    n = len(pts)
    closed = n > 3 and math.dist(pts[0], pts[-1]) < 1e-9
    out = []
    for k in range(n):
        if closed:
            a = pts[k - 1] if k > 0 else pts[-2]
            b = pts[k + 1] if k < n - 1 else pts[1]
        else:
            a, b = pts[max(0, k - 1)], pts[min(n - 1, k + 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        d = math.hypot(dx, dy)
        out.append((dx / d, dy / d) if d > 1e-12 else (1.0, 0.0))
    return out


def fit_smooth_arcs(points: list[Point], max_dev_mm: float = 0.02,
                    max_radius_mm: float = 500.0, step_mm: float = 0.05,
                    max_misses: int = 4) -> list[tuple]:
    """Biarc fit of a densely sampled SMOOTH polyline (use smooth_polyline()
    first on traced data): from each fitted point, the longest biarc to a
    later point -- matching the curve's position and tangent at both ends,
    so consecutive moves join without a corner -- whose two-sided distance
    to the polyline stays within max_dev_mm. Where none fits (a real corner)
    a short G1 bridges to the next point. Same primitive format as
    fit_arcs()."""
    import numpy as np

    pts = [tuple(points[0])]
    for p in points[1:]:
        if math.dist(p, pts[-1]) > 1e-9:
            pts.append(tuple(p))
    n = len(pts)
    if n < 2:
        return []
    T = _tangents(pts)
    arr = np.array(pts)
    A, B = arr[:-1], arr[1:]
    out = []
    i = 0
    while i < n - 1:
        best = None
        misses = 0
        for j in range(i + 2, n):
            ba = _biarc(pts[i], T[i], pts[j], T[j], max_radius_mm)
            ok = False
            if ba is not None:
                S = _sample(pts[i], ba[0], step_mm) + _sample(ba[0][1], ba[1], step_mm)[1:]
                S = np.array(S)
                V = arr[i: j + 1]
                lo, hi = i, j
                ok = (_pt_seg_dist(V, S[:-1], S[1:]).max() <= max_dev_mm
                      and _pt_seg_dist(S, A[lo:hi], B[lo:hi]).max() <= max_dev_mm)
            if ok:
                best, misses = (j, ba), 0
            else:
                misses += 1
                if misses > max_misses:
                    break
        if best is None:
            out.append(("G1", pts[i + 1]))
            i += 1
            continue
        j, ba = best
        out += ba
        i = j
    return out


def _corner_indices(pts, corner_deg, closed):
    """Indices into pts of genuine corners: vertices of the 0.05mm
    Douglas-Peucker simplification (so a pixel staircase doesn't count)
    turning by more than corner_deg."""
    simp = list(LineString(pts).simplify(0.05).coords)
    if closed and len(simp) > 3:
        ring = simp[:-1]
        cand = range(len(ring))
    else:
        ring = simp
        cand = range(1, len(ring) - 1)
    out = []
    for k in cand:
        a, b, c = ring[k - 1], ring[k], ring[(k + 1) % len(ring)]
        v1 = (b[0] - a[0], b[1] - a[1])
        v2 = (c[0] - b[0], c[1] - b[1])
        n1, n2 = math.hypot(*v1), math.hypot(*v2)
        if n1 < 1e-9 or n2 < 1e-9:
            continue
        cosang = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)
        if math.degrees(math.acos(max(-1.0, min(1.0, cosang)))) > corner_deg:
            out.append(min(range(len(pts)), key=lambda i: math.dist(pts[i], b)))
    return sorted(set(out))


def _smooth_piece(points, max_dev_mm, closed, spacing_mm, iterations):
    import numpy as np
    from scipy.interpolate import splev, splprep

    line = LineString(points)
    total = line.length
    if total < 4 * spacing_mm or len(points) < 3:
        return [tuple(p) for p in points]
    n = max(8, int(total / spacing_mm))
    k_end = n if closed else n + 1
    Q = np.array([line.interpolate(k * total / n).coords[0] for k in range(k_end)])
    m = max(8, int(round(total / spacing_mm)))
    uu = np.linspace(0.0, 1.0, m + 1)
    w = np.ones(len(Q))
    if not closed:
        w[0] = w[-1] = 1000.0  # pin the ends (corners / stroke ends)
    k = 3 if len(Q) > 3 else len(Q) - 1

    def fit(sm):
        tck, _u = splprep(Q.T, w=w, s=sm, per=1 if closed else 0, k=k)
        S = np.array(splev(uu, tck)).T
        if not closed:
            S[0], S[-1] = Q[0], Q[-1]
        return S, LineString(S).hausdorff_distance(line)

    best, _ = fit(0.0)
    lo, hi = 0.0, len(Q) * max_dev_mm ** 2 * 4
    for _ in range(iterations):
        mid = (lo + hi) / 2
        S, d = fit(mid)
        if d <= max_dev_mm:
            lo, best = mid, S
        else:
            hi = mid
    out = [tuple(p) for p in best]
    if closed:
        out[-1] = out[0]
    return out


def smooth_polyline(points: list[Point], max_dev_mm: float = 0.08,
                    spacing_mm: float = 0.1, iterations: int = 25,
                    corner_deg: float = 50.0) -> list[Point]:
    """Smooth a traced (staircase-noisy) polyline without moving it more than
    max_dev_mm: a cubic smoothing spline (scipy splprep) through the
    chord-densified polyline, its smoothing factor bisected to the largest
    value whose Hausdorff distance to the input stays within max_dev_mm;
    resampled every ~spacing_mm. Genuine corners (turning more than
    corner_deg on the 0.05mm-simplified outline) are kept as corners: the
    stroke is split there and each piece smoothed with its ends pinned -- a
    spline across a sharp corner rings (wiggles) and slows the head down.
    Closed loops (first == last within 0.05mm) stay closed."""
    closed = len(points) > 3 and math.dist(points[0], points[-1]) <= 0.05
    pts = [tuple(p) for p in points]
    if closed and math.dist(pts[0], pts[-1]) > 0:
        pts.append(pts[0])
    if LineString(pts).length < 4 * spacing_mm or len(pts) < 4:
        return pts
    corners = _corner_indices(pts, corner_deg, closed)
    if closed:
        ring = pts[:-1]
        if not corners:
            return _smooth_piece(pts, max_dev_mm, True, spacing_mm, iterations)
        c0 = corners[0]
        ring = ring[c0:] + ring[:c0]  # start at a corner
        cs = sorted({(c - c0) % len(ring) for c in corners}) + [len(ring)]
        ring = ring + [ring[0]]
        bounds = cs
    else:
        ring = pts
        bounds = [0] + [c for c in corners if 0 < c < len(pts) - 1] + [len(pts) - 1]
    out = [ring[bounds[0]]]
    for a, b in zip(bounds, bounds[1:]):
        piece = ring[a: b + 1]
        sm = _smooth_piece(piece, max_dev_mm, False, spacing_mm, iterations)
        out += sm[1:]
    if closed:
        # rotated to start at a corner: same loop, different start point;
        # put the original start back so warmup/ordering are unchanged
        k = min(range(len(out)), key=lambda i: math.dist(out[i], pts[0]))
        loop = out[:-1]
        out = loop[k:] + loop[:k] + [loop[k]]
    return out
