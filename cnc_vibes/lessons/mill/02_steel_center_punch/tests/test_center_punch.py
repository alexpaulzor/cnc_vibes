"""Tests for center_punch.py — point sources and GCode generation."""

import sys
from pathlib import Path

import pytest
import yaml

LESSON_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LESSON_DIR))

from center_punch import (  # noqa: E402
    generate_gcode,
    generate_grid,
    load_points_file,
    parse_grid_spec,
    parse_points_csv,
)


MACHINE = {
    "envelope_mm": {"x": 400, "y": 300, "z": 100},
}
TOOL = {
    "id": "vbit_test",
    "type": "v_bit",
    "diameter_mm": 6.0,
    "max_rpm": 24000,
    "max_plunge_mm_per_min": 200,
}


# ---------------------------------------------------------------------------
# Point parsing
# ---------------------------------------------------------------------------


def test_parse_points_csv_basic():
    assert parse_points_csv("10,20,30,40") == [(10.0, 20.0), (30.0, 40.0)]


def test_parse_points_csv_floats():
    assert parse_points_csv("1.5,2.5") == [(1.5, 2.5)]


def test_parse_points_csv_odd_count_rejected():
    with pytest.raises(SystemExit, match="even number of values"):
        parse_points_csv("1,2,3")


def test_parse_grid_spec():
    assert parse_grid_spec("5x4") == (5, 4)


def test_parse_grid_spec_invalid():
    with pytest.raises(SystemExit, match="AxB"):
        parse_grid_spec("5_4")


def test_generate_grid_dimensions():
    pts = generate_grid(cols=3, rows=2, pitch=10)
    assert len(pts) == 6


def test_generate_grid_spacing():
    pts = generate_grid(cols=2, rows=2, pitch=5, origin_x=1, origin_y=2)
    assert (1, 2) in pts
    assert (6, 2) in pts
    assert (1, 7) in pts
    assert (6, 7) in pts


def test_generate_grid_invalid_dimensions():
    with pytest.raises(SystemExit, match=">= 1"):
        generate_grid(cols=0, rows=4, pitch=10)


def test_generate_grid_invalid_pitch():
    with pytest.raises(SystemExit, match="pitch must be > 0"):
        generate_grid(cols=2, rows=2, pitch=0)


def test_load_points_file_happy_path(tmp_path):
    p = tmp_path / "points.yaml"
    p.write_text(yaml.safe_dump([[1.0, 2.0], [3.5, 4.5]]))
    assert load_points_file(p) == [(1.0, 2.0), (3.5, 4.5)]


def test_load_points_file_missing(tmp_path):
    with pytest.raises(SystemExit, match="not found"):
        load_points_file(tmp_path / "missing.yaml")


def test_load_points_file_not_a_list(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump({"x": 1}))
    with pytest.raises(SystemExit, match="list of"):
        load_points_file(p)


def test_load_points_file_bad_pair(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump([[1, 2], [3]]))
    with pytest.raises(SystemExit, match="not a"):
        load_points_file(p)


# ---------------------------------------------------------------------------
# GCode generation
# ---------------------------------------------------------------------------


def test_gcode_contains_tool_marker():
    out = generate_gcode(
        points=[(10, 10), (20, 20)],
        depth_mm=0.4,
        plunge_feed_mm_per_min=80,
        tool=TOOL,
        spindle_rpm=12000,
        machine=MACHINE,
    )
    assert ";TOOL: vbit_test" in out


def test_gcode_uses_m3_not_m4():
    out = generate_gcode(
        points=[(10, 10)],
        depth_mm=0.4,
        plunge_feed_mm_per_min=80,
        tool=TOOL,
        spindle_rpm=12000,
        machine=MACHINE,
    )
    import re

    assert re.search(r"^M3\b", out, re.MULTILINE)
    assert not re.search(r"^M4\b", out, re.MULTILINE)


def test_gcode_disables_laser_mode():
    out = generate_gcode(
        points=[(10, 10)],
        depth_mm=0.4,
        plunge_feed_mm_per_min=80,
        tool=TOOL,
        spindle_rpm=12000,
        machine=MACHINE,
    )
    assert "$32=0" in out


def test_gcode_point_count_matches():
    points = [(10, 10), (20, 20), (30, 30), (40, 40)]
    out = generate_gcode(
        points=points,
        depth_mm=0.4,
        plunge_feed_mm_per_min=80,
        tool=TOOL,
        spindle_rpm=12000,
        machine=MACHINE,
    )
    import re

    plunge_lines = re.findall(r"^G1 Z-0\.400 F80\b", out, re.MULTILINE)
    assert len(plunge_lines) == len(points)


def test_gcode_no_points_rejected():
    with pytest.raises(SystemExit, match="no points"):
        generate_gcode(
            points=[],
            depth_mm=0.4,
            plunge_feed_mm_per_min=80,
            tool=TOOL,
            spindle_rpm=12000,
            machine=MACHINE,
        )


def test_gcode_excessive_depth_rejected():
    with pytest.raises(SystemExit, match="too aggressive"):
        generate_gcode(
            points=[(10, 10)],
            depth_mm=5.0,
            plunge_feed_mm_per_min=80,
            tool=TOOL,
            spindle_rpm=12000,
            machine=MACHINE,
        )


def test_gcode_negative_depth_rejected():
    with pytest.raises(SystemExit, match="must be > 0"):
        generate_gcode(
            points=[(10, 10)],
            depth_mm=-0.4,
            plunge_feed_mm_per_min=80,
            tool=TOOL,
            spindle_rpm=12000,
            machine=MACHINE,
        )


def test_gcode_excessive_rpm_rejected():
    with pytest.raises(SystemExit, match="exceeds tool max_rpm"):
        generate_gcode(
            points=[(10, 10)],
            depth_mm=0.4,
            plunge_feed_mm_per_min=80,
            tool=TOOL,
            spindle_rpm=30000,
            machine=MACHINE,
        )


def test_gcode_excessive_plunge_rejected():
    with pytest.raises(SystemExit, match="exceeds tool max_plunge"):
        generate_gcode(
            points=[(10, 10)],
            depth_mm=0.4,
            plunge_feed_mm_per_min=500,
            tool=TOOL,
            spindle_rpm=12000,
            machine=MACHINE,
        )


def test_gcode_out_of_envelope_point_rejected():
    with pytest.raises(SystemExit, match="outside machine X envelope"):
        generate_gcode(
            points=[(500, 10)],
            depth_mm=0.4,
            plunge_feed_mm_per_min=80,
            tool=TOOL,
            spindle_rpm=12000,
            machine=MACHINE,
        )


def test_gcode_safe_z_retract_first():
    out = generate_gcode(
        points=[(10, 10)],
        depth_mm=0.4,
        plunge_feed_mm_per_min=80,
        tool=TOOL,
        spindle_rpm=12000,
        machine=MACHINE,
    )
    g0_lines = [l for l in out.splitlines() if l.startswith("G0")]
    # First G0 must be a Z retract (state.z starts at 0 < safe_z=5)
    assert g0_lines[0].startswith("G0 Z")
