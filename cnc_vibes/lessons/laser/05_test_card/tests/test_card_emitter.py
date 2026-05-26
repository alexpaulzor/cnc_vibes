"""Smoke tests for lessons/laser/05_test_card/test_card.py."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

LESSON_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = LESSON_DIR.parent.parent.parent

sys.path.insert(0, str(LESSON_DIR))
import test_card  # noqa: E402


def _material(power=50, feed=2500, passes=1):
    return {
        "id": "cardboard_thin_1mm",
        "laser": {
            "power_percent": power,
            "feed_mm_per_min": feed,
            "passes": passes,
        },
    }


def test_default_geometry_centered_on_origin():
    g = test_card.generate_gcode(50.0, 30.0, _material())
    # Outer cuts should reach +/- 25
    assert "X25.000" in g and "X-25.000" in g
    assert "Y25.000" in g and "Y-25.000" in g
    # Inner cuts reach +/- 15
    assert "X15.000" in g and "X-15.000" in g
    assert "Y15.000" in g and "Y-15.000" in g


def test_emits_required_headers():
    g = test_card.generate_gcode(50.0, 30.0, _material())
    assert ";HEAD: laser" in g
    assert ";MATERIAL: cardboard_thin_1mm" in g
    assert "$32=1" in g
    assert "M4 S500" in g  # 50% -> S500
    assert "F2500" in g


def test_inner_cut_before_outer():
    g = test_card.generate_gcode(50.0, 30.0, _material())
    inner_pos = g.index("--- inner")
    outer_pos = g.index("--- outer")
    assert inner_pos < outer_pos, "inner must be cut before outer"


def test_rejects_inverted_sizes():
    with pytest.raises(SystemExit):
        test_card.generate_gcode(20.0, 30.0, _material())


def test_passes_validator():
    """End-to-end: real cardboard material, validator-clean."""
    out = LESSON_DIR / "build" / "test_card_smoke.gcode"
    out.parent.mkdir(exist_ok=True)
    r = subprocess.run(
        [
            sys.executable,
            str(LESSON_DIR / "test_card.py"),
            "--material",
            "cardboard_thin_1mm",
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.exists(), r.stderr

    v = subprocess.run(
        [sys.executable, str(REPO_ROOT / "cnc.py"), "validate", str(out)],
        capture_output=True,
        text=True,
    )
    assert v.returncode == 0, v.stdout + v.stderr
    assert re.search(r"\bok\b", v.stdout)
