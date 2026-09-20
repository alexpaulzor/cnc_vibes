"""Behavioral tests for svg2gcode — the diode-laser techniques that matter.

Run:  uv run --with pytest python -m pytest quickcut/tests -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from svg2gcode import (  # noqa: E402
    CutJob,
    Polyline,
    emit_gcode,
    follow_through,
    order_cuts,
    parse_svg,
    place_on_bed,
    warmup_wiggle,
)

SQUARE = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]


def _job(**overrides):
    base = dict(feed_mm_per_min=500, power_percent=100, passes=2, warmup_ms=1000)
    base.update(overrides)
    return CutJob(**base)


# --- power / S scaling -------------------------------------------------------


def test_power_s_respects_full_power_ceiling():
    assert _job(power_percent=100, s_full_power=1000).power_s == 1000
    assert _job(power_percent=50, s_full_power=1000).power_s == 500
    # A controller with $30=24000 would need the ceiling raised to hit full power.
    assert _job(power_percent=100, s_full_power=24000).power_s == 24000


def test_warmup_distance_is_time_times_feed():
    # 1000ms at F600 = 10mm of travel.
    assert _job(feed_mm_per_min=600, warmup_ms=1000).warmup_mm == 10.0


# --- closed loops: same-direction laps + follow-through, NO ping-pong --------


def _moves_between(gcode: str, start_marker: str, end_marker: str) -> list[str]:
    """The G1 moves between the line containing start_marker and the next line
    containing end_marker."""
    lines = gcode.splitlines()
    start = next(i for i, line in enumerate(lines) if start_marker in line)
    end = next(i for i in range(start + 1, len(lines)) if end_marker in lines[i])
    return [line for line in lines[start:end] if line.startswith("G1 ")]


def _has_follow_through(gcode: str) -> bool:
    return any(line.startswith("; follow-through") for line in gcode.splitlines())


def test_closed_loop_passes_go_same_direction():
    gcode = emit_gcode([Polyline(SQUARE, closed=True)], _job(passes=2))
    lap1 = _moves_between(gcode, "pass 1/2", "pass 2/2")
    lap2 = _moves_between(gcode, "pass 2/2", "follow-through")
    # both laps trace identical points in the same order (no reversal / ping-pong)
    assert lap1 == lap2
    assert "reverse" not in gcode


def test_closed_loop_has_follow_through_tail():
    gcode = emit_gcode([Polyline(SQUARE, closed=True)], _job(passes=1))
    assert _has_follow_through(gcode)
    # the beam turns off AFTER the follow-through, i.e. not on the start seam
    lines = gcode.splitlines()
    last_move = [line for line in lines if line.startswith("G1 ")][-1]
    assert last_move != "G1 X10.000 Y10.000"  # would be the start seam of this loop


# --- open paths: warmup wiggle + ping-pong ----------------------------------


def test_open_path_pingpongs_and_wiggles():
    line = [(0, 0), (20, 0)]
    gcode = emit_gcode([Polyline(line, closed=False)], _job(passes=2))
    assert "forward" in gcode and "reverse" in gcode
    assert not _has_follow_through(gcode)


def test_warmup_wiggle_starts_and_ends_near_start():
    line = [(0, 0), (20, 0)]
    wiggle = warmup_wiggle(line, warmup_mm=8.0)
    assert wiggle, "expected a wiggle"
    assert wiggle[-1] == (0, 0)  # returns to the start, ready for the real cut


def test_follow_through_capped_at_one_lap():
    # lead longer than the loop should never exceed the loop's own length.
    lead = follow_through(SQUARE, lead_mm=1000)
    assert lead
    length = sum(
        ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
        for a, b in zip([SQUARE[0]] + lead, lead)
    )
    assert length <= 40.0 + 1e-6  # perimeter of the 10mm square


# --- cut ordering ------------------------------------------------------------


def test_interior_loops_cut_before_boundary():
    boundary = Polyline([(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)], closed=True)
    hole = Polyline([(40, 40), (60, 40), (60, 60), (40, 60), (40, 40)], closed=True)
    ordered = order_cuts([boundary, hole])
    assert ordered[0] is hole and ordered[-1] is boundary


# --- SVG parsing + bed placement --------------------------------------------


def test_parse_svg_path_closed_and_placed_positive():
    svg = (
        '<svg width="10mm" height="10mm" viewBox="0 0 10 10">'
        '<path d="M0,0 L10,0 L10,10 L0,10 Z"/></svg>'
    )
    polylines, scale = parse_svg(svg)
    assert scale == 1.0
    assert len(polylines) == 1 and polylines[0].closed
    placed = place_on_bed(polylines, scale, origin="corner", margin_mm=5.0)
    xs = [x for x, _ in placed[0].points]
    ys = [y for _, y in placed[0].points]
    assert min(xs) >= 5.0 - 1e-9 and min(ys) >= 5.0 - 1e-9  # inside positive margin
    assert placed[0].points[0] == placed[0].points[-1]  # closed ring
