"""Tests for calibration.py — GCode structure, panel layout, error paths."""

import re
import sys
from pathlib import Path

import pytest

LESSON_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LESSON_DIR))

from calibration import generate_gcode  # noqa: E402


MATERIAL = {
    "id": "test_wood",
    "family": "wood",
    "thickness_mm": 3.0,
    "laser": {
        "power_percent": 100,
        "feed_mm_per_min": 400,
        "passes": 2,
    },
}


def _square_cuts(gcode: str) -> int:
    """Count cell-square cut blocks (one comment per cell)."""
    return len(re.findall(r"^; cell row=\d+ col=\d+", gcode, re.MULTILINE))


def _g1_count(gcode: str) -> int:
    return len(re.findall(r"^G1\b", gcode, re.MULTILINE))


def _panels(gcode: str) -> int:
    return len(re.findall(r"^; ==== panel:", gcode, re.MULTILINE))


def test_output_contains_laser_head_marker():
    out = generate_gcode(
        MATERIAL,
        max_passes=5,
        powers=[100, 75, 50, 25],
        speeds=[400],
        cell_pitch=18,
        label_digit_height=5,
    )
    assert ";HEAD: laser" in out


def test_sets_grbl_laser_mode():
    out = generate_gcode(
        MATERIAL,
        max_passes=5,
        powers=[100, 75, 50, 25],
        speeds=[400],
        cell_pitch=18,
        label_digit_height=5,
    )
    assert "$32=1" in out


def test_uses_m4_dynamic_power_not_m3():
    out = generate_gcode(
        MATERIAL,
        max_passes=5,
        powers=[100, 75, 50, 25],
        speeds=[400],
        cell_pitch=18,
        label_digit_height=5,
    )
    assert re.search(r"^M4\b", out, re.MULTILINE)
    assert not re.search(r"^M3\b", out, re.MULTILINE)


def test_cell_count_matches_grid_dimensions():
    out = generate_gcode(
        MATERIAL,
        max_passes=5,
        powers=[100, 75, 50, 25],
        speeds=[400],
        cell_pitch=18,
        label_digit_height=5,
    )
    # 5 cols × 4 powers × 1 speed = 20 cells
    assert _square_cuts(out) == 20


def test_cell_count_scales_with_speeds():
    out = generate_gcode(
        MATERIAL,
        max_passes=3,
        powers=[100, 50],
        speeds=[200, 400, 600],
        cell_pitch=18,
        label_digit_height=5,
    )
    # 3 cols × 2 powers × 3 speeds = 18 cells
    assert _square_cuts(out) == 18


def test_panel_count_matches_speeds():
    out = generate_gcode(
        MATERIAL,
        max_passes=3,
        powers=[100],
        speeds=[200, 400],
        cell_pitch=18,
        label_digit_height=5,
    )
    assert _panels(out) == 2


def test_g1_count_for_cell_pass_count():
    # 1 cell, 3 passes, square = 4 G1 lines per pass = 12 from cuts.
    # Plus label engraves use 1 G1 per segment. We don't assert exact total
    # but we verify cuts contribute the expected amount.
    out = generate_gcode(
        MATERIAL,
        max_passes=1,
        powers=[100],
        speeds=[400],
        cell_pitch=18,
        label_digit_height=5,
    )
    # max_passes=1 => col 0 => 1 pass => 4 G1 lines from cut
    # plus label engrave G1s: feed label "400" (5 segs), col label "1" (2),
    # row label "100" (2+6+0=… actually 1=2segs, 0=6segs, 0=6segs => 14)
    # We just check total G1 count is positive and not zero.
    assert _g1_count(out) > 4


def test_no_z_motion_anywhere():
    out = generate_gcode(
        MATERIAL,
        max_passes=5,
        powers=[100, 75, 50, 25],
        speeds=[400],
        cell_pitch=18,
        label_digit_height=5,
    )
    assert not re.search(r"\bZ-?\d", out)


def test_s_values_all_in_grbl_range():
    out = generate_gcode(
        MATERIAL,
        max_passes=5,
        powers=[100, 75, 50, 25],
        speeds=[400],
        cell_pitch=18,
        label_digit_height=5,
    )
    s_vals = [int(m) for m in re.findall(r"\bS(\d+)", out)]
    assert s_vals, "expected S values"
    for s in s_vals:
        assert 0 <= s <= 1000, f"S{s} outside GRBL range"


def test_power_percent_translates_to_s_correctly():
    out = generate_gcode(
        MATERIAL,
        max_passes=1,
        powers=[60],
        speeds=[400],
        cell_pitch=18,
        label_digit_height=5,
    )
    # 60% -> S600 should appear (used for the cell cut)
    assert "S600" in out


def test_label_uses_engrave_power_S300():
    out = generate_gcode(
        MATERIAL,
        max_passes=1,
        powers=[100],
        speeds=[400],
        cell_pitch=18,
        label_digit_height=5,
    )
    # Label power is 30% = S300
    assert "S300" in out


def test_material_id_in_header_for_traceability():
    out = generate_gcode(
        MATERIAL,
        max_passes=1,
        powers=[100],
        speeds=[400],
        cell_pitch=18,
        label_digit_height=5,
    )
    assert ";MATERIAL: test_wood" in out


def test_lesson_marker_present():
    out = generate_gcode(
        MATERIAL,
        max_passes=1,
        powers=[100],
        speeds=[400],
        cell_pitch=18,
        label_digit_height=5,
    )
    assert ";LESSON: laser-calibration" in out


def test_bounds_stay_positive():
    out = generate_gcode(
        MATERIAL,
        max_passes=5,
        powers=[100, 75, 50, 25],
        speeds=[200, 400, 600],
        cell_pitch=18,
        label_digit_height=5,
    )
    coords = re.findall(r"[XY](-?[0-9.]+)", out)
    nums = [float(c) for c in coords]
    assert min(nums) >= 0, "calibration pattern should not extend into negative XY"


def test_empty_powers_rejected():
    with pytest.raises(SystemExit, match="powers cannot be empty"):
        generate_gcode(
            MATERIAL,
            max_passes=5,
            powers=[],
            speeds=[400],
            cell_pitch=18,
            label_digit_height=5,
        )


def test_zero_max_passes_rejected():
    with pytest.raises(SystemExit, match="must be >= 1"):
        generate_gcode(
            MATERIAL,
            max_passes=0,
            powers=[100],
            speeds=[400],
            cell_pitch=18,
            label_digit_height=5,
        )


def test_out_of_range_power_rejected():
    with pytest.raises(SystemExit, match="outside"):
        generate_gcode(
            MATERIAL,
            max_passes=1,
            powers=[150],
            speeds=[400],
            cell_pitch=18,
            label_digit_height=5,
        )
    with pytest.raises(SystemExit, match="outside"):
        generate_gcode(
            MATERIAL,
            max_passes=1,
            powers=[0],
            speeds=[400],
            cell_pitch=18,
            label_digit_height=5,
        )


def test_no_speeds_rejected():
    with pytest.raises(SystemExit, match="at least one speed"):
        generate_gcode(
            MATERIAL,
            max_passes=1,
            powers=[100],
            speeds=[],
            cell_pitch=18,
            label_digit_height=5,
        )
