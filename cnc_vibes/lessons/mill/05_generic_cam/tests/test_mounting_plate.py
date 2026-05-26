"""Tests for lessons/mill/05_generic_cam/mounting_plate.py — the
worked-example part. Smoke-level only; the cam.py library itself has
deep tests at tests/test_cam.py."""

import re
import sys
from pathlib import Path

import pytest

LESSON_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = LESSON_DIR.parent.parent.parent
sys.path.insert(0, str(LESSON_DIR))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from mounting_plate import make_mounting_plate_gcode  # noqa: E402


def test_default_invocation_produces_nonempty_gcode():
    out = make_mounting_plate_gcode()
    assert out.lines, "expected non-empty GCode output"


def test_default_invocation_has_no_warnings():
    """All defaults pass explicit tool/material so the warning-suppressing
    'explicit tool' path should fire — output should have zero warnings."""
    out = make_mounting_plate_gcode()
    assert out.warnings == [], (
        f"expected no warnings with all-explicit defaults; got: {out.warnings}"
    )


def test_output_has_three_op_sections():
    out = make_mounting_plate_gcode()
    section_markers = [l for l in out.lines if l.startswith("; =====")]
    # Three sections: drill, pocket, profile
    assert len(section_markers) == 3
    # Order check: drill → pocket → profile (inside-out so part stays
    # anchored until the perimeter cut)
    assert "drill" in section_markers[0].lower()
    assert "pocket" in section_markers[1].lower()
    assert "profile" in section_markers[2].lower()


def test_output_emits_four_hole_sections():
    out = make_mounting_plate_gcode()
    n_hole_comments = sum(1 for l in out.lines if l.startswith("; --- hole"))
    assert n_hole_comments == 4


def test_strict_mode_passes_with_all_defaults_explicit():
    """Defaults specify tool+material explicitly, so strict mode shouldn't
    raise. This locks the 'safe defaults' contract."""
    # No exception expected
    make_mounting_plate_gcode(strict=True)


def test_strict_mode_fails_with_bad_tool_for_drill_section():
    """If we pick a ball-end for the drill section, strict mode catches it."""
    with pytest.raises(SystemExit, match="ball_endmill"):
        make_mounting_plate_gcode(drill_tool_id="ball_3mm_2flute", strict=True)


def test_output_coords_within_plate_envelope():
    """All cut moves should stay within (0, plate_w) x (0, plate_h) plus
    tool-radius slack for the profile cut."""
    out = make_mounting_plate_gcode(plate_w_mm=60, plate_h_mm=40)
    text = "\n".join(out.lines)
    for m in re.finditer(r"^G[01]\s+X([-\d.]+)\s+Y([-\d.]+)", text, re.MULTILINE):
        x, y = float(m.group(1)), float(m.group(2))
        if x == 0 and y == 0:
            continue  # park position
        # profile_cut adds tool_radius (1.6mm) of slack on the outside;
        # use a 3mm tolerance to absorb that without false positives
        assert -3 <= x <= 63, f"X={x} far outside plate envelope"
        assert -3 <= y <= 43, f"Y={y} far outside plate envelope"


def test_each_section_has_validator_headers():
    out = make_mounting_plate_gcode()
    text = "\n".join(out.lines)
    # Three ;HEAD: spindle headers, one per section
    assert text.count(";HEAD: spindle") == 3
    # Three ;TOOL lines (one per section, though two sections may name the
    # same tool — flat_3.175mm_2flute for pocket and profile by default)
    assert text.count(";TOOL: ") == 3
