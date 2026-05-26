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
