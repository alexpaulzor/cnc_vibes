"""Tests for trochoidal_slot.py — geometry math and GCode structure."""

import re
import sys
from pathlib import Path

import pytest

LESSON_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LESSON_DIR))

from trochoidal_slot import generate_trochoidal_slot  # noqa: E402


MACHINE = {
    "envelope_mm": {"x": 400, "y": 300, "z": 100},
    "max_feed_mm_per_min": {"xy": 3000, "z": 1000},
    "spindle": {"rpm_min": 8000, "rpm_max": 24000},
}
TOOL = {
    "id": "flat_3.175mm_test",
    "diameter_mm": 3.175,
    "flutes": 2,
    "max_rpm": 24000,
    "max_plunge_mm_per_min": 300,
}
MATERIAL = {
    "id": "aluminum_test",
    "family": "aluminum",
    "thickness_mm": 3.0,
    "chipload": {"flat_3.175mm_test": 0.015},
    "doc_fraction": 0.15,
}


def _gen(**overrides):
    base = dict(
        x0=10,
        y0=10,
        length=30,
        width=6,
        depth=3,
        tool=TOOL,
        material=MATERIAL,
        machine=MACHINE,
        spindle_rpm=18000,
    )
    base.update(overrides)
    return generate_trochoidal_slot(**base)


# ---------------------------------------------------------------------------
# Headers and markers
# ---------------------------------------------------------------------------


def test_includes_tool_and_material_markers():
    out = _gen()
    assert ";TOOL: flat_3.175mm_test" in out
    assert ";MATERIAL: aluminum_test" in out


def test_disables_laser_mode():
    out = _gen()
    assert "$32=0" in out


def test_uses_m3_not_m4():
    out = _gen()
    assert re.search(r"^M3\b", out, re.MULTILINE)
    assert not re.search(r"^M4\b", out, re.MULTILINE)


# ---------------------------------------------------------------------------
# Trochoidal geometry
# ---------------------------------------------------------------------------


def test_emits_full_circle_per_loop_step():
    out = _gen()
    g3_lines = re.findall(r"^G3\b", out, re.MULTILINE)
    # At least many trochoidal circles
    assert len(g3_lines) > 5


def test_layer_count_scales_with_depth():
    shallow = _gen(depth=1)
    deep = _gen(depth=6)
    shallow_layers = len(re.findall(r"; ---- layer", shallow))
    deep_layers = len(re.findall(r"; ---- layer", deep))
    assert deep_layers > shallow_layers


def test_width_must_exceed_tool_diameter():
    with pytest.raises(SystemExit, match="must be >"):
        _gen(width=3.0)  # tool is 3.175


def test_slot_too_short_for_loops_rejected():
    # If length < tool_dia + 2 * loop_radius, can't even fit one trochoidal step
    with pytest.raises(SystemExit, match="too short"):
        _gen(length=2, width=6)


def test_negative_dimensions_rejected():
    with pytest.raises(SystemExit, match=">.*0"):
        _gen(depth=-1)
    with pytest.raises(SystemExit, match=">.*0"):
        _gen(length=0)


def test_rpm_over_tool_max_rejected():
    with pytest.raises(SystemExit, match="> tool max_rpm"):
        _gen(spindle_rpm=30000)


def test_bounds_stay_positive():
    out = _gen()
    nums = [float(c) for c in re.findall(r"[XY](-?[0-9.]+)", out)]
    assert min(nums) >= 0


def test_z_reaches_full_depth():
    out = _gen(depth=3)
    z_values = [float(m) for m in re.findall(r"Z(-?\d+\.\d+)", out)]
    # Should reach -3 on the final layer (within DOC step)
    assert min(z_values) == pytest.approx(-3.0, abs=0.01)


def test_safe_z_retract_first():
    out = _gen()
    g0_lines = [l for l in out.splitlines() if l.startswith("G0")]
    assert g0_lines[0].startswith("G0 Z"), f"first G0 must retract; got {g0_lines[0]}"


def test_loop_radius_respects_width():
    # If width is very tight, loop_radius is capped by (width - tool_dia)/2
    out = _gen(width=4.0)  # tool is 3.175; max loop_r = (4 - 3.175)/2 = 0.4125
    # The trochoidal step (in --trochoidal-radius-frac default = 0.4 * 3.175 = 1.27)
    # would normally be 1.27, but should be capped to 0.4125.
    header_loop_r = re.search(r"loop_r=([0-9.]+)", out)
    assert header_loop_r
    loop_r = float(header_loop_r.group(1))
    assert loop_r <= 0.413
