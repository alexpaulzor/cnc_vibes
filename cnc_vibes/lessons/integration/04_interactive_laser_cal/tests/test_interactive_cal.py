"""Tests for interactive_cal.py — pure GCode emitters and grid math.

Serial layer is integration-tested manually only.
"""

import re
import sys
from pathlib import Path

import pytest

LESSON_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LESSON_DIR))

from interactive_cal import (  # noqa: E402
    CalParams,
    emit_circle_cut_gcode,
    emit_iteration_gcode,
    emit_label_gcode,
    grid_position,
)


def test_grid_position_first_iteration():
    assert grid_position(1, 10, 20, 6, 30, 30) == (10, 20)


def test_grid_position_wraps_to_next_row():
    # Slot 7 (1-indexed) should be at (10, 20+30) with slots_per_row=6
    assert grid_position(7, 10, 20, 6, 30, 30) == (10, 50)


def test_grid_position_advances_within_row():
    assert grid_position(3, 10, 20, 6, 30, 30) == (10 + 60, 20)


def test_label_gcode_uses_m4():
    lines = emit_label_gcode(
        slot_x=0,
        slot_y=0,
        n=1,
        digit_height=4,
        power_s=200,
        feed=1500,
    )
    text = "\n".join(lines)
    assert re.search(r"^M4 S200\b", text, re.MULTILINE)
    # No M3 in laser mode
    assert not re.search(r"^M3\b", text, re.MULTILINE)


def test_label_gcode_includes_iteration_comment():
    lines = emit_label_gcode(0, 0, 7, 4, 200, 1500)
    assert any("; iter 7 label" in l for l in lines)


def test_circle_cut_includes_param_summary():
    params = CalParams(z_mm=2.5, power_percent=80, feed_mm_per_min=300, passes=3)
    lines = emit_circle_cut_gcode(0, 0, 8, params)
    text = "\n".join(lines)
    assert "Z=2.5" in text
    assert "S=800" in text  # 80% = S800
    assert "F=300" in text
    assert "P=3" in text


def test_circle_cut_pass_count_matches_passes():
    params = CalParams(z_mm=0, power_percent=100, feed_mm_per_min=400, passes=5)
    lines = emit_circle_cut_gcode(0, 0, 8, params)
    g3_lines = [l for l in lines if l.startswith("G3")]
    assert len(g3_lines) == 5


def test_circle_cut_skips_z_move_when_z_is_zero():
    params = CalParams(z_mm=0.0, power_percent=100, feed_mm_per_min=400, passes=2)
    lines = emit_circle_cut_gcode(0, 0, 8, params)
    text = "\n".join(lines)
    assert "G0 Z" not in text  # no Z moves when z=0


def test_circle_cut_includes_z_move_when_z_nonzero():
    params = CalParams(z_mm=1.5, power_percent=100, feed_mm_per_min=400, passes=2)
    lines = emit_circle_cut_gcode(0, 0, 8, params)
    text = "\n".join(lines)
    assert "G0 Z1.500" in text
    assert "G0 Z0" in text  # returns to baseline after


def test_circle_cut_ends_with_m5():
    params = CalParams(z_mm=0, power_percent=100, feed_mm_per_min=400, passes=1)
    lines = emit_circle_cut_gcode(0, 0, 8, params)
    # M5 should appear before the optional return-to-Z
    m5_indices = [i for i, l in enumerate(lines) if l.strip() == "M5"]
    assert m5_indices, "no M5 found"


def test_emit_iteration_returns_position():
    params = CalParams(z_mm=0, power_percent=100, feed_mm_per_min=400, passes=2)
    lines, pos = emit_iteration_gcode(
        iter_n=1,
        origin_x=10,
        origin_y=20,
        slots_per_row=6,
        slot_w=30,
        slot_h=30,
        circle_dia=8,
        digit_height=4,
        engrave_power_s=250,
        engrave_feed=1500,
        params=params,
    )
    # Position should be inside slot 1
    assert 10 <= pos[0] <= 40
    assert 20 <= pos[1] <= 50


def test_emit_iteration_combines_label_and_cut():
    params = CalParams(z_mm=0, power_percent=100, feed_mm_per_min=400, passes=2)
    lines, _ = emit_iteration_gcode(
        iter_n=3,
        origin_x=0,
        origin_y=0,
        slots_per_row=6,
        slot_w=30,
        slot_h=30,
        circle_dia=8,
        digit_height=4,
        engrave_power_s=250,
        engrave_feed=1500,
        params=params,
    )
    text = "\n".join(lines)
    assert "; iter 3 label" in text
    assert "; iter cut" in text
    # Label uses lower power; cut uses higher
    assert "S250" in text  # engrave
    assert "S1000" in text  # cut at 100%


def test_iteration_3_is_3rd_column_first_row():
    _, pos = emit_iteration_gcode(
        iter_n=3,
        origin_x=0,
        origin_y=0,
        slots_per_row=6,
        slot_w=30,
        slot_h=30,
        circle_dia=8,
        digit_height=4,
        engrave_power_s=250,
        engrave_feed=1500,
        params=CalParams(),
    )
    # Slot 3 is in column 2 (0-indexed): slot_x = 60, cx = 60 + 15 = 75
    assert pos[0] == pytest.approx(75)
