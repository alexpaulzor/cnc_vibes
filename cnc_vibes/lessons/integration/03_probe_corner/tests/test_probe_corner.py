"""Tests for probe_corner.py — pure GCode generation and PRB parsing."""

import sys
from pathlib import Path

import pytest

LESSON_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LESSON_DIR))

from probe_corner import (  # noqa: E402
    ProbeConfig,
    _parse_prb,
    generate_probe_sequence,
)


def _cfg(**kwargs):
    base = dict(
        plate_thickness_mm=12.0,
        plate_x_offset_mm=25.0,
        plate_y_offset_mm=25.0,
        tool_diameter_mm=3.175,
    )
    base.update(kwargs)
    return ProbeConfig(**base)


# ---------------------------------------------------------------------------
# generate_probe_sequence
# ---------------------------------------------------------------------------


def test_sequence_starts_with_units_and_absolute():
    lines = generate_probe_sequence(_cfg())
    code_lines = [l for l in lines if l and not l.startswith(";")]
    assert "G21" in code_lines[0] or "G21" in code_lines[1]
    assert any("G90" in l for l in code_lines[:5])


def test_sequence_explicitly_disables_laser_mode():
    lines = generate_probe_sequence(_cfg())
    assert any("$32=0" in l for l in lines)


def test_sequence_has_three_probes_one_per_axis():
    lines = generate_probe_sequence(_cfg())
    probe_lines = [l for l in lines if l.startswith("G38.2")]
    assert len(probe_lines) == 3
    # First is Z, then X, then Y in our canonical order.
    assert "Z-" in probe_lines[0]
    assert "X-" in probe_lines[1]
    assert "Y-" in probe_lines[2]


def test_sequence_writes_g54_for_each_axis():
    lines = generate_probe_sequence(_cfg())
    g10_lines = [l for l in lines if l.startswith("G10 L20 P1")]
    assert len(g10_lines) == 3
    assert any("Z" in l for l in g10_lines)
    assert any("X" in l for l in g10_lines)
    assert any("Y" in l for l in g10_lines)


def test_z_offset_equals_plate_thickness():
    lines = generate_probe_sequence(_cfg(plate_thickness_mm=10.0))
    z_line = next(l for l in lines if l.startswith("G10 L20 P1 Z"))
    assert "Z10.0" in z_line


def test_xy_offset_equals_tool_radius():
    lines = generate_probe_sequence(_cfg(tool_diameter_mm=6.0))
    x_line = next(l for l in lines if l.startswith("G10 L20 P1 X"))
    y_line = next(l for l in lines if l.startswith("G10 L20 P1 Y"))
    assert "X3.0" in x_line  # tool_radius = 6/2 = 3
    assert "Y3.0" in y_line


def test_feed_rate_used_for_probes():
    lines = generate_probe_sequence(_cfg(feed_mm_per_min=75))
    for l in lines:
        if l.startswith("G38.2"):
            assert "F75" in l


def test_max_probe_distance_used_in_probe_command():
    lines = generate_probe_sequence(_cfg(max_probe_distance_mm=20.0))
    probe_lines = [l for l in lines if l.startswith("G38.2")]
    assert all("20.0" in l or "20" in l for l in probe_lines)


def test_sequence_ends_at_wcs_offset_position():
    lines = generate_probe_sequence(_cfg(plate_x_offset_mm=30, plate_y_offset_mm=40))
    g0_lines = [l for l in lines if l.startswith("G0 X") and "Y" in l]
    # The final return-to-origin G0 should reference the offsets.
    final = g0_lines[-1]
    assert "X30" in final
    assert "Y40" in final


# ---------------------------------------------------------------------------
# _parse_prb
# ---------------------------------------------------------------------------


def test_parse_prb_success():
    lines = ["[PRB:5.123,6.789,-2.000:1]", "ok"]
    assert _parse_prb(lines) == (5.123, 6.789, -2.0, True)


def test_parse_prb_failure():
    lines = ["[PRB:0.000,0.000,0.000:0]", "ok"]
    assert _parse_prb(lines) == (0.0, 0.0, 0.0, False)


def test_parse_prb_missing_returns_none():
    assert _parse_prb(["ok"]) is None


def test_parse_prb_skips_other_brackets():
    lines = ["[G54:0,0,0]", "[PRB:1.0,2.0,3.0:1]", "ok"]
    assert _parse_prb(lines) == (1.0, 2.0, 3.0, True)
