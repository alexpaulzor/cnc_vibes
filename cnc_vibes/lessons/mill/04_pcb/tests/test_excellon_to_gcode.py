"""Tests for excellon_to_gcode.py — Excellon parsing and drill GCode generation."""

import sys
from pathlib import Path

import pytest

LESSON_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LESSON_DIR))

from excellon_to_gcode import (  # noqa: E402
    ExcellonFile,
    ExcellonTool,
    generate_drill_gcode,
    parse_excellon,
)


SAMPLE_METRIC = """\
M48
METRIC
T1C0.800
T2C1.000
%
T1
X1.500Y2.500
X3.500Y2.500
X1.500Y4.500
T2
X10.000Y10.000
M30
"""

SAMPLE_INCH = """\
M48
INCH
T1C0.0315
%
T1
X1.0000Y1.0000
M30
"""

MACHINE = {"envelope_mm": {"x": 400, "y": 300, "z": 100}}


# ---------------------------------------------------------------------------
# Excellon parser
# ---------------------------------------------------------------------------


def test_parse_metric_recognizes_units():
    drl = parse_excellon(SAMPLE_METRIC)
    assert drl.units == "mm"


def test_parse_inch_recognizes_units_and_converts():
    drl = parse_excellon(SAMPLE_INCH)
    assert drl.units == "inch"
    # 0.0315" * 25.4 = 0.8001 mm
    assert drl.tools[1].diameter_mm == pytest.approx(0.0315 * 25.4)
    # 1.0" * 25.4 = 25.4 mm
    assert drl.tools[1].holes[0] == (pytest.approx(25.4), pytest.approx(25.4))


def test_parse_tool_definitions():
    drl = parse_excellon(SAMPLE_METRIC)
    assert 1 in drl.tools
    assert 2 in drl.tools
    assert drl.tools[1].diameter_mm == 0.800
    assert drl.tools[2].diameter_mm == 1.000


def test_parse_holes_grouped_by_tool():
    drl = parse_excellon(SAMPLE_METRIC)
    assert len(drl.tools[1].holes) == 3
    assert len(drl.tools[2].holes) == 1


def test_parse_hole_coordinates():
    drl = parse_excellon(SAMPLE_METRIC)
    assert drl.tools[1].holes[0] == (1.5, 2.5)
    assert drl.tools[1].holes[2] == (1.5, 4.5)


def test_parse_ignores_comments_and_blank_lines():
    text = "; this is a comment\n\nM48\nMETRIC\n; another\nT1C0.5\n%\nT1\nX1Y2\nM30\n"
    drl = parse_excellon(text)
    assert drl.tools[1].diameter_mm == 0.5
    assert drl.tools[1].holes == [(1.0, 2.0)]


def test_parse_empty_file():
    drl = parse_excellon("")
    assert drl.tools == {}


def test_parse_handles_m00_footer():
    text = "M48\nMETRIC\nT1C0.5\n%\nT1\nX1Y2\nM00\n"
    drl = parse_excellon(text)
    assert drl.tools[1].holes == [(1.0, 2.0)]


# ---------------------------------------------------------------------------
# GCode generation
# ---------------------------------------------------------------------------


def test_gcode_includes_required_markers():
    drl = parse_excellon(SAMPLE_METRIC)
    out = generate_drill_gcode(
        drl=drl,
        copper_thickness_mm=1.6,
        spindle_rpm=12000,
        plunge_feed_mm_per_min=80,
        peck_depth_mm=0.5,
        machine=MACHINE,
    )
    assert "$32=0" in out
    assert "M3 S12000" in out


def test_gcode_emits_tool_change_pause_between_tools():
    drl = parse_excellon(SAMPLE_METRIC)
    out = generate_drill_gcode(
        drl=drl,
        copper_thickness_mm=1.6,
        spindle_rpm=12000,
        plunge_feed_mm_per_min=80,
        peck_depth_mm=0.5,
        machine=MACHINE,
    )
    import re

    m0_lines = re.findall(r"^M0\b", out, re.MULTILINE)
    # 2 tools => 1 M0 pause between them
    assert len(m0_lines) == 1


def test_gcode_no_pause_for_single_tool():
    drl = parse_excellon(SAMPLE_INCH)
    out = generate_drill_gcode(
        drl=drl,
        copper_thickness_mm=1.6,
        spindle_rpm=12000,
        plunge_feed_mm_per_min=80,
        peck_depth_mm=0.5,
        machine=MACHINE,
    )
    import re

    assert not re.search(r"^M0\b", out, re.MULTILINE)


def test_gcode_drill_count_matches_holes():
    drl = parse_excellon(SAMPLE_METRIC)
    out = generate_drill_gcode(
        drl=drl,
        copper_thickness_mm=1.6,
        spindle_rpm=12000,
        plunge_feed_mm_per_min=80,
        peck_depth_mm=0.5,
        machine=MACHINE,
    )
    import re

    # Each hole has a leading "; T<n> hole" comment
    holes = re.findall(r"^; T\d+ hole \d+/", out, re.MULTILINE)
    assert len(holes) == 4  # 3 from T1, 1 from T2


def test_gcode_rejects_no_tools():
    drl = ExcellonFile()
    with pytest.raises(SystemExit, match="no tools"):
        generate_drill_gcode(
            drl=drl,
            copper_thickness_mm=1.6,
            spindle_rpm=12000,
            plunge_feed_mm_per_min=80,
            peck_depth_mm=0.5,
            machine=MACHINE,
        )


def test_gcode_rejects_no_holes():
    drl = ExcellonFile()
    drl.tools[1] = ExcellonTool(number=1, diameter_mm=0.5)
    with pytest.raises(SystemExit, match="no holes"):
        generate_drill_gcode(
            drl=drl,
            copper_thickness_mm=1.6,
            spindle_rpm=12000,
            plunge_feed_mm_per_min=80,
            peck_depth_mm=0.5,
            machine=MACHINE,
        )


def test_gcode_rejects_hole_outside_envelope():
    drl = ExcellonFile()
    drl.tools[1] = ExcellonTool(number=1, diameter_mm=0.5, holes=[(500, 10)])
    with pytest.raises(SystemExit, match="outside envelope"):
        generate_drill_gcode(
            drl=drl,
            copper_thickness_mm=1.6,
            spindle_rpm=12000,
            plunge_feed_mm_per_min=80,
            peck_depth_mm=0.5,
            machine=MACHINE,
        )


def test_gcode_drills_to_correct_depth():
    drl = parse_excellon(SAMPLE_INCH)
    out = generate_drill_gcode(
        drl=drl,
        copper_thickness_mm=1.6,
        spindle_rpm=12000,
        plunge_feed_mm_per_min=80,
        peck_depth_mm=0.5,
        machine=MACHINE,
    )
    import re

    z_values = [float(m) for m in re.findall(r"Z(-?\d+\.\d+)", out)]
    # final_z = -(1.6 + 0.3) = -1.9
    assert min(z_values) == pytest.approx(-1.9)


def test_gcode_safe_z_retract_first():
    drl = parse_excellon(SAMPLE_INCH)
    out = generate_drill_gcode(
        drl=drl,
        copper_thickness_mm=1.6,
        spindle_rpm=12000,
        plunge_feed_mm_per_min=80,
        peck_depth_mm=0.5,
        machine=MACHINE,
    )
    g0_lines = [l for l in out.splitlines() if l.startswith("G0")]
    assert g0_lines[0].startswith("G0 Z")
