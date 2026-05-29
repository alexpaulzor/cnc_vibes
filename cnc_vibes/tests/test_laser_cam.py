"""Unit tests for scripts/laser_cam.py."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from shapely.geometry import Point, box

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
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


def test_laser_profile_required_headers():
    out = laser_cam.laser_profile(box(-10, -10, 10, 10), _mat())
    text = out.text
    assert ";HEAD: laser" in text
    assert ";MATERIAL: cardboard_thin_1mm" in text
    assert "$32=1" in text
    assert "M4 S500" in text  # 50% -> S500
    assert "F2500" in text
    # No Z motion ever
    assert not re.search(r"\bZ-?\d", text)
    # No M3 (static-power), only M4
    assert "M3" not in text


def test_laser_profile_passes_propagate():
    m = _mat()
    m.passes = 3
    out = laser_cam.laser_profile(box(-5, -5, 5, 5), m)
    text = out.text
    assert "pass 1/3" in text and "pass 2/3" in text and "pass 3/3" in text


def test_laser_profile_handles_circle():
    geom = Point(0, 0).buffer(10, resolution=32)
    out = laser_cam.laser_profile(geom, _mat())
    text = out.text
    # A buffered point produces many points; we want G1 cuts after M4
    g1_count = text.count("G1 X")
    assert g1_count > 30


def test_laser_engrave_emits_per_contour():
    out = laser_cam.laser_engrave("OK", (0, 0), 8, _mat())
    text = out.text
    assert ";HEAD: laser" in text
    assert "M4 S500" in text
    # OK = 2 glyphs, O has 2 contours, K has 1 -> at least 3 contour blocks
    assert text.count("--- contour ") >= 3


def test_laser_engrave_empty_text_warns():
    out = laser_cam.laser_engrave("", (0, 0), 6, _mat())
    assert out.lines == []
    assert out.warnings


# ---------------------------------------------------------------------------
# Static (M3) mode
# ---------------------------------------------------------------------------


def test_laser_profile_static_emits_m3_and_header():
    out = laser_cam.laser_profile(box(-10, -10, 10, 10), _mat(), mode="static")
    text = out.text
    assert ";LASER_MODE: static" in text
    assert "M3 S500" in text
    assert "M4" not in text


def test_laser_engrave_static_emits_m3():
    out = laser_cam.laser_engrave("OK", (0, 0), 8, _mat(), mode="static")
    text = out.text
    assert ";LASER_MODE: static" in text
    assert "M3 S500" in text
    assert "M4" not in text


def test_default_dynamic_emits_m4_no_static_header():
    out = laser_cam.laser_profile(box(-10, -10, 10, 10), _mat())
    text = out.text
    assert ";LASER_MODE: static" not in text
    assert "M4 S500" in text
    assert "M3 " not in text


# ---------------------------------------------------------------------------
# Simplification: short-segment starvation mitigation
# ---------------------------------------------------------------------------


def test_engrave_simplification_drops_point_count():
    """The whole point of simplification — M4 dynamic mode starves on
    sub-mm segments. At 0.05mm tolerance (default) we expect orders-of-
    magnitude fewer G1 lines than at tolerance=0."""
    default = laser_cam.laser_engrave("SAMPLE", (0, 0), 8, _mat()).lines
    raw = laser_cam.laser_engrave(
        "SAMPLE", (0, 0), 8, _mat(), simplify_tolerance_mm=0
    ).lines
    g1_default = sum(1 for l in default if l.startswith("G1 "))
    g1_raw = sum(1 for l in raw if l.startswith("G1 "))
    assert g1_default * 10 < g1_raw, (
        f"simplification underwhelmed: default={g1_default} raw={g1_raw}"
    )


def test_profile_simplification_drops_circle_vertices():
    geom = Point(0, 0).buffer(20, resolution=128)  # very over-sampled circle
    default = laser_cam.laser_profile(geom, _mat()).lines
    raw = laser_cam.laser_profile(geom, _mat(), simplify_tolerance_mm=0).lines
    g1_default = sum(1 for l in default if l.startswith("G1 "))
    g1_raw = sum(1 for l in raw if l.startswith("G1 "))
    assert g1_default < g1_raw


# ---------------------------------------------------------------------------
# text_profile (cut glyph silhouettes out of stock)
# ---------------------------------------------------------------------------


def test_text_profile_emits_ring_per_glyph_and_counter():
    """'OAK' = O (outer+counter) + A (outer+counter) + K (outer only) = 5 rings."""
    out = laser_cam.text_profile("OAK", (0, 0), 25, _mat())
    rings = sum(1 for l in out.lines if l.startswith("; --- ring "))
    assert rings == 5, f"expected 5 rings for OAK, got {rings}\n{out.text}"


def test_text_profile_static_mode_header_and_m3():
    out = laser_cam.text_profile("OK", (0, 0), 20, _mat(), mode="static")
    text = out.text
    assert ";LASER_MODE: static" in text
    assert "M3 S500" in text
    assert "M4" not in text


def test_text_profile_empty_text_warns():
    out = laser_cam.text_profile("", (0, 0), 20, _mat())
    assert out.lines == []
    assert out.warnings


def test_text_profile_position_offsets_coords():
    out = laser_cam.text_profile("I", (50, 50), 20, _mat())
    text = out.text
    # All G1 cuts should be in the +X +Y quadrant near (50,50)
    g1_lines = [l for l in out.lines if l.startswith("G1 X")]
    for line in g1_lines:
        # parse "G1 X.. Y.."
        x = float(line.split("X")[1].split()[0])
        assert x > 30, f"text_profile didn't translate by position: {line}"


# ---------------------------------------------------------------------------
# Warmup dwell (laser fade-in mitigation)
# ---------------------------------------------------------------------------


def test_warmup_emits_g4_per_ring_in_laser_profile():
    out = laser_cam.laser_profile(box(-10, -10, 10, 10), _mat(), warmup_ms=300)
    g4_lines = [l for l in out.lines if l.startswith("G4 P0.300")]
    assert len(g4_lines) == 1


def test_warmup_emits_g4_per_contour_in_laser_engrave():
    out = laser_cam.laser_engrave("OK", (0, 0), 8, _mat(), warmup_ms=200)
    g4_lines = [l for l in out.lines if l.startswith("G4 P0.200")]
    # 'O' has 2 contours, 'K' has 1 -> 3 G4 dwells
    assert len(g4_lines) == 3


def test_warmup_threads_through_text_profile():
    out = laser_cam.text_profile("OAK", (0, 0), 25, _mat(), warmup_ms=150)
    g4_lines = [l for l in out.lines if l.startswith("G4 P0.150")]
    # OAK = 5 rings (O:2, A:2, K:1) -> 5 warmup dwells
    assert len(g4_lines) == 5


def test_warmup_zero_emits_no_dwell():
    out = laser_cam.laser_profile(box(-10, -10, 10, 10), _mat())
    assert "G4 " not in out.text


def test_warmup_negative_treated_as_zero():
    out = laser_cam.laser_profile(box(-10, -10, 10, 10), _mat(), warmup_ms=-100)
    assert "G4 " not in out.text
