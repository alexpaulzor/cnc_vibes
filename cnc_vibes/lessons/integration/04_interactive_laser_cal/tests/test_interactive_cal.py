"""Tests for interactive_cal.py — pure GCode emitters, grid math, and
the safety/envelope checks. Serial layer is integration-tested manually only.
"""

import re
import sys
from pathlib import Path

import pytest

LESSON_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LESSON_DIR))

from interactive_cal import (  # noqa: E402
    CalParams,
    check_layout_within_envelope,
    check_z_bounds,
    emit_circle_cut_gcode,
    emit_iteration_gcode,
    emit_label_gcode,
    grid_position,
    load_machine_envelope,
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


# ---------------------------------------------------------------------------
# C1 regression: label and cut must honor custom slot_w/slot_h, not the
# DEFAULT_SLOT_W/H constants. Earlier bug: misalignment for non-default slots.
# ---------------------------------------------------------------------------


def test_label_centers_on_custom_slot_width():
    # Slot 50 wide at origin 0: label should center at x=25, not x=15 (default).
    lines = emit_label_gcode(
        slot_x=0,
        slot_y=0,
        n=8,
        digit_height=4,
        power_s=200,
        feed=1500,
        slot_w=50,
        slot_h=50,
    )
    # 7-seg "8" is symmetric; all G0/G1 x-coords should bracket x=25.
    xs = []
    for line in lines:
        m = re.search(r"[GMmg][01] X([0-9.]+)", line)
        if m:
            xs.append(float(m.group(1)))
    if xs:
        center = (min(xs) + max(xs)) / 2
        assert center == pytest.approx(25, abs=2), (
            f"label centered at {center}, expected ~25 for slot_w=50"
        )


def test_circle_cut_centers_on_custom_slot_width():
    params = CalParams(z_mm=0, power_percent=100, feed_mm_per_min=400, passes=1)
    lines = emit_circle_cut_gcode(
        slot_x=0, slot_y=0, circle_dia=8, params=params, slot_w=50, slot_h=50
    )
    # First G0 is to cx+r, so cx = X-coord - 4. Should be 25, not 15.
    g0 = next(l for l in lines if l.startswith("G0 X"))
    x = float(re.search(r"G0 X([0-9.]+)", g0).group(1))
    assert x == pytest.approx(29, abs=0.01), f"expected cx=25 + r=4 = 29, got {x}"


def test_emit_iteration_propagates_custom_slot_dims():
    """Whole composed iteration: position and cut must match for custom slots."""
    params = CalParams(z_mm=0, power_percent=100, feed_mm_per_min=400, passes=1)
    lines, pos = emit_iteration_gcode(
        iter_n=1,
        origin_x=10,
        origin_y=10,
        slots_per_row=4,
        slot_w=50,
        slot_h=50,
        circle_dia=8,
        digit_height=4,
        engrave_power_s=250,
        engrave_feed=1500,
        params=params,
    )
    # Reported position should equal where the actual cut happens.
    cut_g0 = next(l for l in lines if l.startswith("G0 X") and "; iter cut" not in l)
    # The cut's first G0 is at cx+r; find any line referencing the cut center cx
    # by searching for the "iter cut" comment which embeds the centerpoint.
    comment = next(l for l in lines if "iter cut" in l)
    m = re.search(r"at \(([0-9.]+),\s*([0-9.]+)\)", comment)
    cx, cy = float(m.group(1)), float(m.group(2))
    assert (cx, cy) == pytest.approx(pos, abs=0.01)


# ---------------------------------------------------------------------------
# Envelope and Z safety checks
# ---------------------------------------------------------------------------


def test_layout_fits_default_envelope():
    envelope = {"x": 400, "y": 300, "z": 100}
    problems = check_layout_within_envelope(10, 10, 30, 30, 6, 24, envelope)
    assert problems == []


def test_layout_too_wide_for_envelope():
    envelope = {"x": 400, "y": 300, "z": 100}
    problems = check_layout_within_envelope(10, 10, 200, 30, 4, 4, envelope)
    assert any("X=" in p for p in problems)


def test_layout_too_tall_for_envelope():
    envelope = {"x": 400, "y": 300, "z": 100}
    # 4 rows of 100mm slots starting at Y=10 => max Y = 410 > 300
    problems = check_layout_within_envelope(10, 10, 30, 100, 6, 24, envelope)
    assert any("Y=" in p for p in problems)


def test_layout_rejects_negative_origin():
    envelope = {"x": 400, "y": 300, "z": 100}
    problems = check_layout_within_envelope(-5, 10, 30, 30, 6, 4, envelope)
    assert any("negative" in p for p in problems)


def test_z_bounds_accepts_within_range():
    assert check_z_bounds(0, 10) is None
    assert check_z_bounds(5, 10) is None
    assert check_z_bounds(-9.99, 10) is None


def test_z_bounds_rejects_out_of_range():
    msg = check_z_bounds(25, 10)
    assert msg is not None and "Z=25" in msg
    msg = check_z_bounds(-15, 10)
    assert msg is not None and "-15" in msg


def test_load_envelope_falls_back_when_missing():
    env = load_machine_envelope(Path("/nonexistent/profile.yaml"))
    assert env["x"] > 0 and env["y"] > 0 and env["z"] > 0


def test_load_envelope_reads_real_profile():
    profile = (
        LESSON_DIR.parent.parent.parent / "profiles" / "anolex_4030_evo_ultra2.yaml"
    )
    if not profile.exists():
        pytest.skip("machine profile not in this checkout")
    env = load_machine_envelope(profile)
    assert env["x"] == 400.0
    assert env["y"] == 300.0
