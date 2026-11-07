"""Motion primitives for weak-diode laser cutting — shared by every vibes tool.

Pure geometry on lists of ``(x, y)`` points: no SVG, no file I/O, no G-code
text. quickcut/gcode.py (the one G-code core) builds on these so the diode techniques live in one place.

The two that carry the diode know-how:

  * ``warmup_wiggle`` — for OPEN paths: an out-and-back over the start so the
    beam reaches full power before the real cut, ending back at the start.
  * ``follow_through`` — for CLOSED loops: keep going past the start so the
    cold-cut start region is re-cut hot and the part releases with no snag.
"""

from __future__ import annotations

import math

Point = tuple[float, float]


def signed_area(points: list[Point]) -> float:
    """Shoelace signed area of a closed ring (sign encodes winding direction)."""
    total = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1]):
        total += x0 * y1 - x1 * y0
    return total / 2


def path_length(points: list[Point]) -> float:
    """Total arc length of a polyline."""
    return sum(math.dist(start, end) for start, end in zip(points, points[1:]))


def decimate(points: list[Point], min_segment_mm: float) -> list[Point]:
    """Drop points closer than min_segment_mm to the previous kept point, so we
    don't emit a flood of sub-kerf micro-moves. The last point is always kept."""
    if min_segment_mm <= 0 or len(points) < 3:
        return points
    kept = [points[0]]
    for point in points[1:]:
        if math.dist(point, kept[-1]) >= min_segment_mm:
            kept.append(point)
    if kept[-1] != points[-1]:
        if len(kept) >= 2:
            kept.pop()
        kept.append(points[-1])
    return kept


def points_along(points: list[Point], distance_mm: float) -> list[Point]:
    """Walk `points` from the start and return the run covering distance_mm of
    arc length (interpolating the final segment to land exactly on distance_mm)."""
    walked = [points[0]]
    covered = 0.0
    for start, end in zip(points, points[1:]):
        segment = math.dist(start, end)
        if covered + segment >= distance_mm and segment > 1e-9:
            fraction = (distance_mm - covered) / segment
            walked.append(
                (
                    start[0] + fraction * (end[0] - start[0]),
                    start[1] + fraction * (end[1] - start[1]),
                )
            )
            return walked
        walked.append(end)
        covered += segment
    return walked


def warmup_wiggle(points: list[Point], warmup_mm: float, one_trip: bool = False) -> list[Point]:
    """For OPEN paths: an out-and-back run over the start (ending back at the
    start point) so the beam reaches full power before the real cut begins.
    A path shorter than half the warmup gets full out-and-back trips until warm,
    or just one with `one_trip` (no energy pile-up on a short stroke)."""
    if warmup_mm <= 0 or len(points) < 2:
        return []
    total = path_length(points)
    if total <= 1e-9:
        return []
    half = warmup_mm / 2
    if total >= half:
        out = points_along(points, half)
        return out[1:] + list(reversed(out))[1:]
    # Path shorter than the wiggle: run full out-and-back trips until warmed up.
    trips = 1 if one_trip else max(1, math.ceil(warmup_mm / (2 * total)))
    wiggle: list[Point] = []
    for _ in range(trips):
        wiggle += list(points[1:]) + list(reversed(points))[1:]
    return wiggle


def follow_through(points: list[Point], lead_mm: float, laps: bool = False) -> list[Point]:
    """For CLOSED loops: keep going PAST the start, tracing back into the loop for
    lead_mm of extra travel. The beam fired cold at the start, so the loop's first
    lead_mm was cut weak; carrying the now-hot beam that far past the start re-cuts
    it at full power, and the beam turns off mid-arc in already-severed material
    instead of dwelling on the burn seam. Capped at one full lap unless `laps`
    (etch: a short loop keeps circling until the lead is covered)."""
    if lead_mm <= 0 or len(points) < 3:
        return []
    total = path_length(points)
    if total <= 1e-9:
        return []
    if not laps:
        return points_along(points, min(lead_mm, total))[1:]
    ring = [p for i, p in enumerate(points) if i == 0 or math.dist(p, points[i - 1]) > 1e-9]
    if math.dist(ring[0], ring[-1]) > 1e-9:
        ring.append(ring[0])
    track = list(ring)
    while path_length(track) < lead_mm:
        track += ring[1:]
    return points_along(track, lead_mm)[1:]


def collapse_shuttles(points: list[Point], max_leg_mm: float = 1.0, eps: float = 1e-3) -> list[Point]:
    """Drop repeated back-and-forth trips over the same short edge.

    Continuous routing can re-trace one sub-mm edge several times in a row
    (A,B,A,B,A,...) when many trails meet at a tiny junction; the head then
    shuttles in place and the static beam flickers/over-burns. Collapse every
    extra A->B->A round trip, keeping one so the edge is still cut."""
    out = list(points)

    def same(p, q):
        return abs(p[0] - q[0]) <= eps and abs(p[1] - q[1]) <= eps

    i = 0
    while i + 4 < len(out):
        a, b = out[i], out[i + 1]
        if (math.hypot(b[0] - a[0], b[1] - a[1]) < max_leg_mm and same(out[i + 2], a)
                and same(out[i + 3], b) and same(out[i + 4], a)):
            del out[i + 1:i + 3]  # A,B,A,B,A -> A,B,A
            continue
        i += 1
    return out
