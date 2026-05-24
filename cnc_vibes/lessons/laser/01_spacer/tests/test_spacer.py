"""Tests for lessons/laser/01_spacer/spacer.py.

Interface tests on the generated GCode: marker comments present,
correct laser-mode commands, expected number of arcs per pass, S values
in range, no Z motion. Plus argparse-level error paths.
"""

import re
import sys
from pathlib import Path

import pytest

LESSON_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LESSON_DIR))

from spacer import generate_gcode, load_material  # noqa: E402


MATERIAL = {
    "id": "test_material",
    "family": "wood",
    "thickness_mm": 3.0,
    "laser": {
        "power_percent": 100,
        "feed_mm_per_min": 400,
        "passes": 2,
    },
}


def _count_g3_arcs(gcode: str) -> int:
    return len(re.findall(r"^G3\b", gcode, flags=re.MULTILINE))


def _s_values(gcode: str) -> list[int]:
    return [int(m) for m in re.findall(r"\bS(\d+)", gcode)]


def test_output_contains_laser_head_marker():
    out = generate_gcode(6.0, 3.2, MATERIAL)
    assert ";HEAD: laser" in out


def test_output_sets_grbl_laser_mode():
    out = generate_gcode(6.0, 3.2, MATERIAL)
    assert "$32=1" in out


def test_uses_dynamic_power_m4_not_static_m3():
    out = generate_gcode(6.0, 3.2, MATERIAL)
    assert re.search(r"^M4\b", out, flags=re.MULTILINE)
    assert not re.search(r"^M3\b", out, flags=re.MULTILINE)


def test_correct_arc_count_for_two_passes_two_circles():
    out = generate_gcode(6.0, 3.2, MATERIAL)
    # 2 passes * 2 circles (inner, outer) = 4 G3 arcs
    assert _count_g3_arcs(out) == 4


def test_arc_count_scales_with_passes():
    mat = {**MATERIAL, "laser": {**MATERIAL["laser"], "passes": 5}}
    out = generate_gcode(6.0, 3.2, mat)
    assert _count_g3_arcs(out) == 10


def test_s_values_in_grbl_range_0_to_1000():
    out = generate_gcode(6.0, 3.2, MATERIAL)
    s_values = _s_values(out)
    assert s_values, "expected at least one S value"
    for s in s_values:
        assert 0 <= s <= 1000, f"S{s} out of GRBL range"


def test_power_percent_translates_to_s_correctly():
    mat = {**MATERIAL, "laser": {**MATERIAL["laser"], "power_percent": 60}}
    out = generate_gcode(6.0, 3.2, mat)
    # 60% -> S600
    assert "S600" in out


def test_no_z_motion_anywhere():
    out = generate_gcode(6.0, 3.2, MATERIAL)
    # Laser jobs keep Z constant (user pre-focuses). No G word should
    # carry a Z coordinate.
    assert not re.search(r"\bZ-?\d", out), "laser spacer GCode must not contain Z moves"


def test_laser_off_m5_before_and_after():
    out = generate_gcode(6.0, 3.2, MATERIAL)
    m5_count = len(re.findall(r"^M5\b", out, flags=re.MULTILINE))
    # M5 appears at: header, between inner and outer, at end => 3
    assert m5_count == 3


def test_feed_rate_matches_material_profile():
    mat = {**MATERIAL, "laser": {**MATERIAL["laser"], "feed_mm_per_min": 250}}
    out = generate_gcode(6.0, 3.2, mat)
    assert "\nF250\n" in out


def test_material_id_appears_in_comment_for_traceability():
    out = generate_gcode(6.0, 3.2, MATERIAL)
    assert ";MATERIAL: test_material" in out


def test_id_must_be_smaller_than_od():
    with pytest.raises(SystemExit, match="must be smaller"):
        generate_gcode(6.0, 6.0, MATERIAL)
    with pytest.raises(SystemExit, match="must be smaller"):
        generate_gcode(3.0, 5.0, MATERIAL)


def test_negative_dimensions_rejected():
    with pytest.raises(SystemExit, match="must be positive"):
        generate_gcode(-6.0, 3.2, MATERIAL)
    with pytest.raises(SystemExit, match="must be positive"):
        generate_gcode(6.0, -3.2, MATERIAL)


def test_load_material_finds_real_entry():
    # Smoke-test against the actual profiles file shipped in the repo.
    m = load_material("plywood_baltic_birch_3mm")
    assert m["id"] == "plywood_baltic_birch_3mm"
    assert m["laser"]["passes"] >= 1


def test_load_material_unknown_id_exits():
    with pytest.raises(SystemExit, match="unknown material"):
        load_material("not_a_real_material_xyz")


def test_bounds_stay_in_positive_quadrant():
    out = generate_gcode(50.0, 10.0, MATERIAL)
    # Extract all X and Y coords; min should be >= 0 (we add a 2mm margin
    # from origin to the nearest edge of the cut).
    coords = re.findall(r"[XY]([-\d.]+)", out)
    nums = [float(c) for c in coords]
    assert min(nums) >= 0, "spacer geometry should not extend into negative XY"
