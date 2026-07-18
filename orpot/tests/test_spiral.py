"""Tests for orpot spiral geometry + gcode emission.

Validates the phase-1 contract: two single-polygon spiral ribbons with the
right radii/width, and static-M3 GRBL gcode that matches the machine
conventions (M3 not M4, S in range, warmup + passes, positive coords, no
sub-floor segments)."""

import math
import re
import sys
from pathlib import Path

import pytest
from shapely.geometry import Point, Polygon

PKG_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_DIR))

from emit import emit_cut_gcode, load_material  # noqa: E402
from spiral import (  # noqa: E402
    SpiralConfig,
    build_bottom_spiral,
    build_part,
    build_top_spiral,
)


def test_parts_are_single_valid_polygons():
    cfg = SpiralConfig()
    top = build_top_spiral(cfg)
    bot = build_bottom_spiral(cfg)
    for p in (top, bot):
        assert isinstance(p, Polygon)
        assert p.is_valid
        assert p.area > 0


def test_top_spiral_radii_and_width():
    cfg = SpiralConfig()
    top = build_top_spiral(cfg)  # centered at origin
    # Widest extent of the rim = top_outer_r (within buffer tolerance).
    minx, miny, maxx, maxy = top.bounds
    reach = max(maxx, maxy, -minx, -miny)
    assert reach == pytest.approx(cfg.top_outer_r, abs=0.6)
    # A one-rev ribbon of width strip_w: its area is roughly width * arc length,
    # well above a degenerate sliver. Sanity floor:
    assert top.area > cfg.strip_w_mm * cfg.top_inner_r


def test_top_ribbon_width_via_radial_probe():
    """Probe the ribbon width on the -x axis (theta=pi), where a single turn
    crosses cleanly, away from the end caps and the nesting seam at theta=0."""
    cfg = SpiralConfig()
    top = build_top_spiral(cfg)
    # At theta=pi the top centerline sits at radius r_start - pitch/2.
    r_mid = (cfg.top_outer_r - cfg.strip_w_mm / 2.0) - cfg.top_pitch_mm / 2.0
    lo = int((r_mid + cfg.strip_w_mm) * 10) + 40
    inside = [
        -x / 10.0
        for x in range(int((r_mid - cfg.strip_w_mm) * 10) - 40, lo)
        if top.contains(Point(-x / 10.0, 0.0))
    ]
    assert inside, "no material found along -x probe"
    span = max(inside) - min(inside)
    assert span == pytest.approx(cfg.strip_w_mm, abs=0.6)


def test_bottom_contains_base_disc():
    cfg = SpiralConfig()
    bot = build_bottom_spiral(cfg)
    disc = Point(0, 0).buffer(cfg.base_r * 0.98)
    assert bot.contains(disc), "base disc footprint missing from bottom spiral"
    # And it reaches out to ~top_outer_r.
    minx, miny, maxx, maxy = bot.bounds
    reach = max(maxx, maxy, -minx, -miny)
    assert reach == pytest.approx(cfg.top_outer_r, abs=1.0)


def test_placed_part_is_positive():
    cfg = SpiralConfig(margin_mm=8.0)
    for name in ("top", "bottom"):
        poly = build_part(name, cfg)
        minx, miny, _, _ = poly.bounds
        assert minx == pytest.approx(cfg.margin_mm, abs=1e-6)
        assert miny == pytest.approx(cfg.margin_mm, abs=1e-6)


def test_gcode_conventions_and_bounds():
    cfg = SpiralConfig()
    material = load_material("mdf_3mm")
    parts = [(n, build_part(n, cfg)) for n in ("top", "bottom")]
    gcode = emit_cut_gcode(parts, material, "test", cfg)

    assert "$32=1" in gcode
    assert ";MATERIAL: mdf_3mm" in gcode
    assert ";LASER_MODE: static" in gcode
    assert "\nM4 " not in gcode  # static M3 only, never M4 dynamic
    assert gcode.count("\nM3 ") == 2  # one cut per part
    assert gcode.count("\nM5") >= 2

    # S value in [0, 1000]; feed present.
    s_vals = [int(m) for m in re.findall(r"M3 S(\d+)", gcode)]
    assert s_vals and all(0 <= s <= 1000 for s in s_vals)
    assert s_vals == [1000, 1000]  # 100% power
    assert "F350" in gcode  # mdf_3mm feed

    # 2 passes per part (mdf_3mm).
    assert gcode.count("; pass 2 of 2") == 2

    # All coordinates positive (within the machine work area, origin corner).
    coords = re.findall(r"[XY](-?\d+\.\d+)", gcode)
    assert coords and all(float(c) >= 0.0 for c in coords)


def test_gcode_respects_min_segment():
    cfg = SpiralConfig(min_segment_mm=0.4)
    material = load_material("mdf_3mm")
    parts = [("top", build_part("top", cfg))]
    gcode = emit_cut_gcode(parts, material, "test", cfg)
    # Reconstruct consecutive G1 points and check segment lengths. The warmup
    # wiggle can create short pivots at the turnaround, so only check the main
    # cut ring: segments should be >= floor minus a small numeric epsilon.
    pts = []
    for ln in gcode.splitlines():
        m = re.match(r"G1 X(-?\d+\.\d+) Y(-?\d+\.\d+)", ln)
        if m:
            pts.append((float(m.group(1)), float(m.group(2))))
    # Count how many segments fall below floor; allow a tiny few from wiggle
    # turnarounds / the closing chord, but the vast majority must comply.
    short = sum(
        1
        for a, b in zip(pts, pts[1:])
        if math.hypot(b[0] - a[0], b[1] - a[1]) < cfg.min_segment_mm - 1e-6
    )
    assert short <= 3, f"too many sub-floor segments: {short}"
