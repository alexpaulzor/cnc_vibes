"""Tests for mill_spacer.py — hybrid dispatch, hole strategy, GCode structure."""

import re
import sys
from pathlib import Path

import pytest

LESSON_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LESSON_DIR))

from mill_spacer import (  # noqa: E402
    _is_cylindrical,
    generate_cylindrical_gcode,
    generate_scad,
)

REPO_ROOT = LESSON_DIR.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from job_params import load_yaml  # noqa: E402


PROFILES = REPO_ROOT / "profiles"


@pytest.fixture(scope="module")
def machine():
    return load_yaml(PROFILES / "default.yaml")


@pytest.fixture(scope="module")
def material():
    mats = load_yaml(PROFILES / "materials.yaml")
    return next(m for m in mats if m["id"] == "plywood_baltic_birch_6mm")


@pytest.fixture(scope="module")
def tool_3175():
    tools = load_yaml(PROFILES / "tools.yaml")
    return next(t for t in tools if t["id"] == "flat_3.175mm_2flute")


# ---------------------------------------------------------------------------
# Geometry classification
# ---------------------------------------------------------------------------


def test_all_equal_diameters_is_cylindrical():
    assert _is_cylindrical(8, 8, 3.2, 3.2) is True


def test_different_top_od_is_frustum():
    assert _is_cylindrical(10, 8, 3.2, 3.2) is False


def test_different_top_id_is_frustum():
    assert _is_cylindrical(8, 8, 4.0, 3.2) is False


def test_tiny_float_difference_still_cylindrical():
    # Within absolute tolerance — should classify as cylindrical
    assert _is_cylindrical(8.0, 8.0000005, 3.2, 3.2) is True


# ---------------------------------------------------------------------------
# SCAD generation
# ---------------------------------------------------------------------------


def test_scad_uses_difference_of_two_cylinders():
    scad = generate_scad(top_od=8, bottom_od=12, top_id=3.2, bottom_id=3.2, height=6)
    assert "difference()" in scad
    assert scad.count("cylinder(") == 2


def test_scad_outer_has_correct_radii():
    scad = generate_scad(top_od=10, bottom_od=14, top_id=3.2, bottom_id=3.2, height=6)
    assert "r1=7.0" in scad  # bottom_od/2
    assert "r2=5.0" in scad  # top_od/2


def test_scad_inner_offset_by_minus_0_1():
    scad = generate_scad(top_od=8, bottom_od=8, top_id=3.2, bottom_id=3.2, height=6)
    assert "translate([0, 0, -0.1])" in scad


# ---------------------------------------------------------------------------
# Cylindrical GCode generation
# ---------------------------------------------------------------------------


def test_cylindrical_gcode_contains_tool_and_material_markers(
    machine, material, tool_3175
):
    out = generate_cylindrical_gcode(
        od=8,
        id_mm=3.2,
        height=6,
        machine=machine,
        material=material,
        tool=tool_3175,
        spindle_rpm=18000,
    )
    assert f";TOOL: {tool_3175['id']}" in out
    assert f";MATERIAL: {material['id']}" in out


def test_cylindrical_gcode_uses_m3_not_m4(machine, material, tool_3175):
    out = generate_cylindrical_gcode(
        od=8,
        id_mm=3.2,
        height=6,
        machine=machine,
        material=material,
        tool=tool_3175,
        spindle_rpm=18000,
    )
    assert re.search(r"^M3\b", out, re.MULTILINE), "spindle job should use M3"
    assert not re.search(r"^M4\b", out, re.MULTILINE), "no M4 in spindle GCode"


def test_cylindrical_gcode_explicitly_disables_laser_mode(machine, material, tool_3175):
    out = generate_cylindrical_gcode(
        od=8,
        id_mm=3.2,
        height=6,
        machine=machine,
        material=material,
        tool=tool_3175,
        spindle_rpm=18000,
    )
    assert "$32=0" in out


def test_helical_bore_used_when_id_large(machine, material, tool_3175):
    # id=10 > 2.5 * 3.175 => helical
    out = generate_cylindrical_gcode(
        od=16,
        id_mm=10,
        height=6,
        machine=machine,
        material=material,
        tool=tool_3175,
        spindle_rpm=18000,
    )
    assert "helical bore" in out
    assert "peck drill" not in out


def test_peck_drill_used_when_id_small(machine, material, tool_3175):
    # id=3.2 < 2.5 * 3.175 => peck drill
    out = generate_cylindrical_gcode(
        od=8,
        id_mm=3.2,
        height=6,
        machine=machine,
        material=material,
        tool=tool_3175,
        spindle_rpm=18000,
    )
    assert "peck drill" in out
    assert "helical bore" not in out


def test_id_smaller_than_tool_errors(machine, material, tool_3175):
    # tool dia is 3.175; id 2.0 is too small
    with pytest.raises(SystemExit, match="smaller than tool diameter"):
        generate_cylindrical_gcode(
            od=8,
            id_mm=2.0,
            height=6,
            machine=machine,
            material=material,
            tool=tool_3175,
            spindle_rpm=18000,
        )


def test_perimeter_pass_count_scales_with_height(machine, material, tool_3175):
    # height 12 means more passes than height 6 at the same DOC
    out_6 = generate_cylindrical_gcode(
        od=8,
        id_mm=3.2,
        height=6,
        machine=machine,
        material=material,
        tool=tool_3175,
        spindle_rpm=18000,
    )
    out_12 = generate_cylindrical_gcode(
        od=8,
        id_mm=3.2,
        height=12,
        machine=machine,
        material=material,
        tool=tool_3175,
        spindle_rpm=18000,
    )
    n6 = len(re.findall(r"perimeter pass \d+/", out_6))
    n12 = len(re.findall(r"perimeter pass \d+/", out_12))
    assert n12 > n6


def test_part_centered_in_positive_quadrant(machine, material, tool_3175):
    out = generate_cylindrical_gcode(
        od=20,
        id_mm=8,
        height=6,
        machine=machine,
        material=material,
        tool=tool_3175,
        spindle_rpm=18000,
    )
    xy_coords = re.findall(r"[XY](-?[0-9.]+)", out)
    nums = [float(c) for c in xy_coords]
    assert min(nums) >= 0, "spacer geometry should not extend into negative XY"


def test_perimeter_through_cut_overcuts_by_spoilboard(machine, material, tool_3175):
    out = generate_cylindrical_gcode(
        od=8,
        id_mm=3.2,
        height=6,
        machine=machine,
        material=material,
        tool=tool_3175,
        spindle_rpm=18000,
    )
    # Final depth should be -6.2 (height + 0.2 mm overcut)
    z_values = [float(m) for m in re.findall(r"Z(-?\d+\.\d+)", out)]
    assert min(z_values) == pytest.approx(-6.2)


def test_gcode_includes_safe_z_retract_first(machine, material, tool_3175):
    out = generate_cylindrical_gcode(
        od=8,
        id_mm=3.2,
        height=6,
        machine=machine,
        material=material,
        tool=tool_3175,
        spindle_rpm=18000,
    )
    # First G0 after $32=0 / units / mode should be a Z retract, not an XY traverse
    lines = [l for l in out.splitlines() if l.startswith("G0")]
    assert lines[0].startswith("G0 Z"), (
        f"first G0 should retract to safe Z; got: {lines[0]}"
    )
