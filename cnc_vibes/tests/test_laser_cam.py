"""Unit tests for scripts/laser_cam.py.

This repo cuts with a weak ~10W diode: STATIC M3 constant power is the
diode-correct default, and CLOSED rings are traced N same-direction laps
then followed through past the start (never ping-ponged).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from shapely.geometry import Point, box

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import gcode_validate  # noqa: E402
import laser_cam  # noqa: E402


def _mat():
    return laser_cam.LaserMaterial(
        id="cardboard_thin_1mm",
        family="paper",
        thickness_mm=1.0,
        power_percent=50.0,
        feed_mm_per_min=2500,
        passes=1,
    )


def test_load_laser_material_real():
    m = laser_cam.load_laser_material("cardboard_thin_1mm")
    assert m.id == "cardboard_thin_1mm"
    assert 0 < m.power_percent <= 100
    assert m.feed_mm_per_min > 0


def test_load_laser_material_unknown():
    with pytest.raises(SystemExit):
        laser_cam.load_laser_material("definitely_not_real")


# ---------------------------------------------------------------------------
# Default = static (M3). M4 is the opt-in that under-fires a weak diode.
# ---------------------------------------------------------------------------


def test_laser_profile_required_headers_default_static():
    out = laser_cam.laser_profile(box(-10, -10, 10, 10), _mat())
    text = out.text
    assert ";HEAD: laser" in text
    assert ";MATERIAL: cardboard_thin_1mm" in text
    assert "$32=1" in text
    assert "M3 S500" in text  # 50% -> S500, static M3 by default
    assert "F2500" in text
    # No Z motion ever
    assert not re.search(r"\bZ-?\d", text)
    # Static is the diode default: no M4, no dynamic-mode header needed.
    assert "M4" not in text
    assert ";LASER_MODE: dynamic" not in text


def test_laser_profile_dynamic_emits_m4_and_header():
    out = laser_cam.laser_profile(box(-10, -10, 10, 10), _mat(), mode="dynamic")
    text = out.text
    assert ";LASER_MODE: dynamic" in text
    assert "M4 S500" in text
    assert "M3" not in text


def test_laser_engrave_default_static_m3():
    out = laser_cam.laser_engrave("OK", (0, 0), 8, _mat())
    text = out.text
    assert ";HEAD: laser" in text
    assert "M3 S500" in text
    assert "M4" not in text
    # OK = 2 glyphs, O has 2 contours, K has 1 -> at least 3 contour blocks
    assert text.count("--- contour ") >= 3


def test_laser_engrave_dynamic_emits_m4_and_header():
    out = laser_cam.laser_engrave("OK", (0, 0), 8, _mat(), mode="dynamic")
    text = out.text
    assert ";LASER_MODE: dynamic" in text
    assert "M4 S500" in text
    assert "M3" not in text


def test_laser_engrave_empty_text_warns():
    out = laser_cam.laser_engrave("", (0, 0), 6, _mat())
    assert out.lines == []
    assert out.warnings


# ---------------------------------------------------------------------------
# $30-aware S scaling (mirrors quickcut CutJob.power_s)
# ---------------------------------------------------------------------------


def test_s_scaling_default_full_power_1000():
    out = laser_cam.laser_profile(box(-10, -10, 10, 10), _mat())
    assert "M3 S500" in out.text  # 50% of 1000


def test_s_scaling_respects_s_full_power():
    out = laser_cam.laser_profile(box(-10, -10, 10, 10), _mat(), s_full_power=24000)
    # 50% of 24000 -> S12000
    assert "M3 S12000" in out.text
    assert "S500" not in out.text


# ---------------------------------------------------------------------------
# Closed-loop motion: NO ping-pong, and a follow-through past the start.
# ---------------------------------------------------------------------------


def test_closed_ring_does_not_ping_pong():
    out = laser_cam.laser_profile(box(-5, -5, 5, 5), _mat())
    text = out.text
    assert "reverse" not in text
    assert "forward" not in text


def test_closed_ring_has_follow_through():
    out = laser_cam.laser_profile(box(-10, -10, 10, 10), _mat())
    assert "follow-through" in out.text


def test_laser_profile_passes_propagate_same_direction():
    m = _mat()
    m.passes = 3
    out = laser_cam.laser_profile(box(-5, -5, 5, 5), m)
    text = out.text
    assert "pass 1/3" in text and "pass 2/3" in text and "pass 3/3" in text
    assert "reverse" not in text


def test_laser_profile_handles_circle():
    geom = Point(0, 0).buffer(10, resolution=32)
    out = laser_cam.laser_profile(geom, _mat())
    text = out.text
    # A buffered point produces many points; we want G1 cuts after M3
    g1_count = text.count("G1 X")
    assert g1_count > 30


# ---------------------------------------------------------------------------
# Simplification / decimation: short-segment thinning
# ---------------------------------------------------------------------------


def test_engrave_simplification_drops_point_count():
    """simplify_tolerance_mm collapses near-collinear vertices before decimation.
    At the 0.05mm default we expect far fewer G1 lines than with simplify=0."""
    default = laser_cam.laser_engrave("SAMPLE", (0, 0), 8, _mat()).lines
    raw = laser_cam.laser_engrave(
        "SAMPLE", (0, 0), 8, _mat(), simplify_tolerance_mm=0
    ).lines
    g1_default = sum(1 for l in default if l.startswith("G1 "))
    g1_raw = sum(1 for l in raw if l.startswith("G1 "))
    assert g1_default < g1_raw, (
        f"simplification underwhelmed: default={g1_default} raw={g1_raw}"
    )


def test_profile_simplification_drops_circle_vertices():
    geom = Point(0, 0).buffer(20, resolution=128)  # very over-sampled circle
    default = laser_cam.laser_profile(geom, _mat()).lines
    raw = laser_cam.laser_profile(geom, _mat(), simplify_tolerance_mm=0).lines
    g1_default = sum(1 for l in default if l.startswith("G1 "))
    g1_raw = sum(1 for l in raw if l.startswith("G1 "))
    assert g1_default < g1_raw


def test_decimate_thins_dense_segments():
    """min_segment_mm drops points closer than the threshold to the previous."""
    geom = Point(0, 0).buffer(20, resolution=128)
    fine = laser_cam.laser_profile(
        geom, _mat(), simplify_tolerance_mm=0, min_segment_mm=0.05
    ).lines
    coarse = laser_cam.laser_profile(
        geom, _mat(), simplify_tolerance_mm=0, min_segment_mm=2.0
    ).lines
    g1_fine = sum(1 for l in fine if l.startswith("G1 "))
    g1_coarse = sum(1 for l in coarse if l.startswith("G1 "))
    assert g1_coarse < g1_fine


# ---------------------------------------------------------------------------
# text_profile (cut glyph silhouettes out of stock)
# ---------------------------------------------------------------------------


def test_text_profile_emits_ring_per_glyph_and_counter():
    """'OAK' = O (outer+counter) + A (outer+counter) + K (outer only) = 5 rings."""
    out = laser_cam.text_profile("OAK", (0, 0), 25, _mat())
    rings = sum(1 for l in out.lines if l.startswith("; --- ring "))
    assert rings == 5, f"expected 5 rings for OAK, got {rings}\n{out.text}"


def test_text_profile_default_static_m3():
    out = laser_cam.text_profile("OK", (0, 0), 20, _mat())
    text = out.text
    assert "M3 S500" in text
    assert "M4" not in text
    assert ";LASER_MODE: dynamic" not in text


def test_text_profile_dynamic_mode_header_and_m4():
    out = laser_cam.text_profile("OK", (0, 0), 20, _mat(), mode="dynamic")
    text = out.text
    assert ";LASER_MODE: dynamic" in text
    assert "M4 S500" in text
    assert "M3" not in text


def test_text_profile_empty_text_warns():
    out = laser_cam.text_profile("", (0, 0), 20, _mat())
    assert out.lines == []
    assert out.warnings


def test_text_profile_position_offsets_coords():
    out = laser_cam.text_profile("I", (50, 50), 20, _mat())
    # All G1 cuts should be in the +X +Y quadrant near (50,50)
    g1_lines = [l for l in out.lines if l.startswith("G1 X")]
    for line in g1_lines:
        x = float(line.split("X")[1].split()[0])
        assert x > 30, f"text_profile didn't translate by position: {line}"


# ---------------------------------------------------------------------------
# No dwell: GRBL laser mode ($32=1) only fires while moving, so a G4 dwell
# produces no beam. No laser op should emit one.
# ---------------------------------------------------------------------------


def test_laser_profile_emits_no_dwell():
    out = laser_cam.laser_profile(box(-10, -10, 10, 10), _mat())
    assert "G4 " not in out.text


def test_text_profile_emits_no_dwell():
    out = laser_cam.text_profile("OAK", (0, 0), 25, _mat())
    assert "G4 " not in out.text


# ---------------------------------------------------------------------------
# The default (static) output validates clean against gcode_validate.
# ---------------------------------------------------------------------------


def _profile():
    return {
        "envelope_mm": {"x": 400, "y": 300, "z": 100},
        "max_feed_mm_per_min": {"xy": 3000, "z": 1000},
        "default_safe_z_mm": 5.0,
    }


def test_default_laser_profile_validates_clean():
    out = laser_cam.laser_profile(box(-10, -10, 10, 10), _mat())
    violations = gcode_validate.validate(out.text, _profile(), tools=[])
    assert violations == [], "\n".join(str(v) for v in violations)


def test_dynamic_laser_profile_validates_clean():
    out = laser_cam.laser_profile(box(-10, -10, 10, 10), _mat(), mode="dynamic")
    violations = gcode_validate.validate(out.text, _profile(), tools=[])
    assert violations == [], "\n".join(str(v) for v in violations)


def test_s_full_power_out_of_range_flagged_by_validator():
    # s_full_power=24000 -> S12000, which exceeds the default $30=1000 range.
    out = laser_cam.laser_profile(box(-10, -10, 10, 10), _mat(), s_full_power=24000)
    violations = gcode_validate.validate(out.text, _profile(), tools=[])
    assert any(v.rule == "laser_power_range" for v in violations)
