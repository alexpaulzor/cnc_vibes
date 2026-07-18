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

from emit import decimate_min_segment, emit_cut_gcode, load_material  # noqa: E402
from ribs import (  # noqa: E402
    base_disc_slots,
    build_all_ribs,
    rib_azimuths,
    rib_crossings,
)
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
    crosses cleanly, away from the end caps and the seam at theta=0."""
    from spiral import _part_polar_params

    cfg = SpiralConfig()
    top = build_top_spiral(cfg)
    r0, pitch, _ = _part_polar_params("top", cfg)
    r_mid = r0 + pitch * 0.5  # centerline radius at theta=pi
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

    # One cut ring per exterior + per interior hole (holes cut first, profile
    # last).
    rings = sum(1 + len(poly.interiors) for _, poly in parts)
    assert rings >= 2  # at least one profile per part

    assert "$32=1" in gcode
    assert ";MATERIAL: mdf_3mm" in gcode
    assert ";LASER_MODE: static" in gcode
    assert "\nM4 " not in gcode  # static M3 only, never M4 dynamic
    assert gcode.count("\nM3 ") == rings  # one laser-on per ring
    assert gcode.count("\nM5") >= rings

    # S value in [0, 1000]; feed present.
    s_vals = [int(m) for m in re.findall(r"M3 S(\d+)", gcode)]
    assert s_vals and all(0 <= s <= 1000 for s in s_vals)
    assert all(s == 1000 for s in s_vals)  # 100% power
    assert "F350" in gcode  # mdf_3mm feed

    # 2 passes per ring (mdf_3mm).
    assert gcode.count("; pass 2 of 2") == rings

    # All coordinates positive (within the machine work area, origin corner).
    coords = re.findall(r"[XY](-?\d+\.\d+)", gcode)
    assert coords and all(float(c) >= 0.0 for c in coords)


def test_decimate_respects_min_segment():
    """The decimation guarantee is on the ring geometry: every emitted segment
    of a decimated ring is >= the floor (endpoints preserved). (The warmup
    wiggle is generated separately and may pivot short at turnarounds, so we
    test the decimator directly rather than parsing gcode.)"""
    cfg = SpiralConfig(min_segment_mm=0.4)
    ring = list(build_part("top", cfg).exterior.coords)
    dec = decimate_min_segment(ring, cfg.min_segment_mm)
    seglens = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(dec, dec[1:])]
    # Every interior segment complies; only the forced closing chord may be
    # shorter (endpoints are always preserved).
    short = [s for s in seglens[:-1] if s < cfg.min_segment_mm - 1e-6]
    assert not short, f"sub-floor segments after decimation: {short}"


def test_ribs_count_and_slots():
    cfg = SpiralConfig(n_ribs=6)
    ribs = build_all_ribs(cfg)
    assert len(ribs) == 6
    for rib in ribs:
        assert rib.is_valid and rib.area > 0
        # Each rib captures both ramps -> 2 slot holes.
        assert len(rib.interiors) == 2
    # One base-disc slot per rib.
    assert len(base_disc_slots(cfg)) == 6


def test_rib_slots_sit_at_ramp_crossings():
    """Every capture-slot hole should be centered on a ramp crossing (r, z)."""
    cfg = SpiralConfig(n_ribs=6)
    for a, rib in zip(rib_azimuths(cfg), build_all_ribs(cfg)):
        crossings = rib_crossings(cfg, a)
        hole_centers = [Polygon(r).centroid for r in rib.interiors]
        for _, r, z in crossings:
            near = min(
                math.hypot(c.x - r, c.y - z) for c in hole_centers
            )
            assert near < 1.0, f"no slot near crossing r={r:.1f} z={z:.1f}"


def test_base_disc_slots_removed_from_bottom():
    """The bottom piece with rib slots has less area than the plain disc+ramp."""
    from shapely.ops import unary_union

    cfg = SpiralConfig(n_ribs=6)
    plain = build_bottom_spiral(cfg)
    slotted = plain.difference(unary_union(base_disc_slots(cfg)))
    assert slotted.area < plain.area
