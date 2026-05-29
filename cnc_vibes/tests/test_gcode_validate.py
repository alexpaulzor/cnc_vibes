"""Tests for scripts/gcode_validate.py — the interface boundary between
generated GCode and the machine. Each test pairs a small synthetic GCode
fragment with the violations it should (or should not) trigger.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from gcode_validate import validate  # noqa: E402


PROFILE = {
    "name": "test",
    "envelope_mm": {"x": 400, "y": 300, "z": 100},
    "max_feed_mm_per_min": {"xy": 3000, "z": 1000},
    "default_safe_z_mm": 5.0,
}
TOOLS = [
    {
        "id": "flat_3mm",
        "type": "flat_endmill",
        "diameter_mm": 3.0,
        "max_rpm": 24000,
        "max_plunge_mm_per_min": 300,
    },
]


def _rules(violations):
    return sorted({v.rule for v in violations})


def test_clean_job_has_no_violations():
    gcode = """
        ;TOOL: flat_3mm
        G90
        G0 Z5
        M3 S12000
        G0 X10 Y10
        G1 Z-2 F200
        G1 X20 Y10 F1000
        G0 Z5
        M5
    """
    assert validate(gcode, PROFILE, TOOLS) == []


def test_out_of_envelope_x_flagged():
    gcode = "G90\nM3 S12000\nG0 Z5\nG0 X500 Y10\nG1 Z-1 F200\n"
    assert "bounds" in _rules(validate(gcode, PROFILE, TOOLS))


def test_out_of_envelope_z_too_deep():
    gcode = "G90\nM3 S12000\nG0 Z5\nG0 X10 Y10\nG1 Z-150 F200\n"
    assert "bounds" in _rules(validate(gcode, PROFILE, TOOLS))


def test_xy_feed_too_fast_flagged():
    gcode = "G90\nM3 S12000\nG0 Z5\nG0 X10 Y10\nG1 Z-1 F200\nG1 X20 F9000\n"
    assert "max_feed" in _rules(validate(gcode, PROFILE, TOOLS))


def test_plunge_too_fast_flagged_when_tool_declared():
    gcode = ";TOOL: flat_3mm\nG90\nM3 S12000\nG0 Z5\nG0 X10 Y10\nG1 Z-2 F800\n"
    rules = _rules(validate(gcode, PROFILE, TOOLS))
    assert "max_plunge" in rules


def test_plunge_check_skipped_without_tool_declaration():
    # Without a ;TOOL: comment, max_plunge is not enforced.
    gcode = "G90\nM3 S12000\nG0 Z5\nG0 X10 Y10\nG1 Z-2 F800\n"
    rules = _rules(validate(gcode, PROFILE, TOOLS))
    # F800 is below XY cap and we have no tool reference, so max_feed +
    # max_plunge both quiet. (Z cap is 1000.)
    assert "max_plunge" not in rules


def test_rapid_below_safe_z_with_xy_change_flagged():
    # G0 traverse at Z=-1 (below safe_z=5) with XY change is unsafe.
    gcode = "G90\nM3 S12000\nG0 Z-1\nG0 X10 Y10\n"
    assert "safe_z_rapid" in _rules(validate(gcode, PROFILE, TOOLS))


def test_pure_z_rapid_below_safe_z_ok():
    # After retracting to safe_z, an XY traverse is fine; the subsequent
    # pure-Z plunge to cutting depth (no XY component) is also fine.
    gcode = "G90\nM3 S12000\nG0 Z5\nG0 X10 Y10\nG0 Z-1\nG1 X20 F500\n"
    rules = _rules(validate(gcode, PROFILE, TOOLS))
    assert "safe_z_rapid" not in rules


def test_spindle_off_during_cut_flagged():
    gcode = "G90\nG0 Z5\nG0 X10 Y10\nG1 Z-2 F200\n"
    assert "spindle_on" in _rules(validate(gcode, PROFILE, TOOLS))


def test_spindle_s_zero_during_cut_flagged():
    gcode = "G90\nM3 S0\nG0 Z5\nG0 X10 Y10\nG1 Z-2 F200\n"
    assert "spindle_on" in _rules(validate(gcode, PROFILE, TOOLS))


def test_comments_and_parens_are_ignored():
    gcode = """
        ;TOOL: flat_3mm
        ; preamble
        (header from FreeCAD post)
        G90
        M3 S12000  ; spindle
        G0 Z5  (safe)
        G0 X10 Y10
        G1 Z-2 F200
        G1 X20 F1000
        M5
    """
    assert validate(gcode, PROFILE, TOOLS) == []


# ---------------------------------------------------------------------------
# Laser-mode rules (gated by ;HEAD: laser comment in the first 20 lines)
# ---------------------------------------------------------------------------

LASER_HEADER = ";HEAD: laser\n$32=1\nG21\nG90\nM5\n"


def test_laser_job_with_m4_and_in_range_s_is_clean():
    gcode = LASER_HEADER + (
        "G0 X10 Y10\nM4 S1000\nF400\nG3 X10 Y10 I-3 J0\nM5\nG0 X0 Y0\n"
    )
    assert validate(gcode, PROFILE, TOOLS) == []


def test_laser_job_missing_dollar_32_flagged():
    gcode = ";HEAD: laser\nG21\nG90\nM4 S1000\nF400\nG3 X10 Y10 I-3 J0\nM5\n"
    assert "laser_mode" in _rules(validate(gcode, PROFILE, TOOLS))


def test_laser_job_using_m3_flagged():
    gcode = LASER_HEADER + "G0 X10 Y10\nM3 S1000\nF400\nG3 X10 Y10 I-3 J0\nM5\n"
    assert "laser_m4_required" in _rules(validate(gcode, PROFILE, TOOLS))


def test_laser_m3_allowed_when_laser_mode_static_declared():
    gcode = (
        ";HEAD: laser\n;LASER_MODE: static\n$32=1\nG21\nG90\nM5\n"
        "G0 X10 Y10\nM3 S1000\nF400\nG1 X20 Y10\nM5\n"
    )
    assert "laser_m4_required" not in _rules(validate(gcode, PROFILE, TOOLS))


def test_laser_s_value_above_1000_flagged():
    gcode = LASER_HEADER + "G0 X10 Y10\nM4 S2000\nF400\nG3 X10 Y10 I-3 J0\nM5\n"
    assert "laser_power_range" in _rules(validate(gcode, PROFILE, TOOLS))


def test_laser_job_skips_spindle_on_rule():
    # No M3 ever — but the spindle_on rule must NOT fire for laser jobs.
    gcode = LASER_HEADER + ("G0 X10 Y10\nM4 S1000\nF400\nG3 X10 Y10 I-3 J0\nM5\n")
    assert "spindle_on" not in _rules(validate(gcode, PROFILE, TOOLS))


def test_laser_job_skips_safe_z_rapid_rule():
    # Laser jobs keep Z at 0 throughout. State.z=0 < safe_z=5, but the
    # safe_z_rapid rule should not fire for laser-tagged GCode.
    gcode = LASER_HEADER + (
        "G0 X10 Y10\nM4 S1000\nF400\nG3 X10 Y10 I-3 J0\nM5\nG0 X20 Y20\n"
    )
    assert "safe_z_rapid" not in _rules(validate(gcode, PROFILE, TOOLS))


def test_laser_job_still_enforces_bounds():
    # X=500 exceeds envelope.x=400 even for laser jobs.
    gcode = LASER_HEADER + "G0 X500 Y10\nM4 S1000\nF400\nG3 X500 Y10 I-3 J0\nM5\n"
    assert "bounds" in _rules(validate(gcode, PROFILE, TOOLS))


def test_laser_job_still_enforces_max_feed():
    gcode = LASER_HEADER + "G0 X10 Y10\nM4 S1000\nF9000\nG3 X10 Y10 I-3 J0\nM5\n"
    assert "max_feed" in _rules(validate(gcode, PROFILE, TOOLS))


def test_spindle_default_when_no_head_marker():
    # No ;HEAD: comment -> spindle rules apply (existing behavior).
    gcode = "G90\nG0 Z5\nG0 X10 Y10\nG1 Z-2 F200\n"
    rules = _rules(validate(gcode, PROFILE, TOOLS))
    assert "spindle_on" in rules
    # And no laser rules should fire.
    assert "laser_mode" not in rules
    assert "laser_m4_required" not in rules
