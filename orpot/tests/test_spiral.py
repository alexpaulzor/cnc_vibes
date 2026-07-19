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

from emit import (  # noqa: E402
    decimate_min_segment,
    emit_cut_gcode,
    emit_disc_gcode,
    load_material,
)
from ribs import (  # noqa: E402
    base_disc_slots,
    build_all_ribs,
    rib_azimuths,
    rib_crossings,
    ring_slots,
)
from spiral import (  # noqa: E402
    SpiralConfig,
    build_bottom_spiral,
    build_disc,
    build_part,
    build_top_spiral,
    disc_radii,
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
    """Width of the inward ribbon on the -x axis (theta=pi). Walk outward from
    the centerline until material ends, so the new outer rim ring doesn't skew
    the measurement."""
    from spiral import _part_polar_params

    cfg = SpiralConfig()
    top = build_top_spiral(cfg)
    r0, pitch, _ = _part_polar_params("top", cfg)
    r_mid = r0 + pitch * 0.5  # centerline radius at theta=pi
    step = 0.05

    def edge(direction):
        r = r_mid
        while top.contains(Point(-r, 0.0)):
            r += direction * step
        return r

    width = edge(+1) - edge(-1)
    assert width == pytest.approx(cfg.strip_w_mm, abs=0.6)


def test_bottom_contains_base_disc():
    cfg = SpiralConfig()
    bot = build_bottom_spiral(cfg)
    disc = Point(0, 0).buffer(cfg.base_r * 0.98)
    assert bot.contains(disc), "base disc footprint missing from bottom spiral"
    # The ramp reaches at least the rim ring's inner edge (the free-end boss may
    # extend a bit past it).
    minx, miny, maxx, maxy = bot.bounds
    reach = max(maxx, maxy, -minx, -miny)
    assert reach >= cfg.ring_inner_r - 1.0


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


def test_free_end_slots_present():
    """Each spiral has a rib slot carved at its free end (bottom: outer end;
    top: inner end), both on the +x seam — the slot center is cut away."""
    cfg = SpiralConfig()
    bot = build_bottom_spiral(cfg)
    top = build_top_spiral(cfg)
    assert not bot.contains(Point(cfg.span_r_hi, 0.0)), "bottom free-end slot missing"
    assert not top.contains(Point(cfg.span_r_lo, 0.0)), "top free-end slot missing"
    cfg = SpiralConfig(n_ribs=6)
    ribs = build_all_ribs(cfg)
    assert len(ribs) == 6
    for rib in ribs:
        assert rib.is_valid and rib.area > 0
        minx, miny, maxx, maxy = rib.bounds
        # Base tab drops below the floor; top tab rises above the rim.
        assert miny <= -cfg.rib_tab_depth_mm + 0.5
        assert maxy >= cfg.rise_per_rev_mm + cfg.rib_top_tab_up_mm - 0.5
    # One base-disc slot and one rim-ring slot per rib.
    assert len(base_disc_slots(cfg)) == 6
    assert len(ring_slots(cfg)) == 6


def test_rib_notches_at_crossings():
    """Each MID spiral crossing carves an open notch (>= notch depth) from the
    rib's top edge: material is gone at the crossing but present below."""
    cfg = SpiralConfig(n_ribs=6)
    rise, nd, h_min = cfg.rise_per_rev_mm, cfg.rib_notch_depth_mm, cfg.rib_band_mm
    for a, rib in zip(rib_azimuths(cfg), build_all_ribs(cfg)):
        for name, r, z in rib_crossings(cfg, a):
            if not (h_min < z < rise - h_min):  # only mid crossings are notched
                continue
            assert not rib.contains(Point(r, z - 0.5)), (
                f"expected notch at r={r:.1f} z={z:.1f}"
            )
            below = z - nd - 1.0
            if below > -cfg.rib_band_mm:  # still within the strut band
                assert rib.contains(Point(r, below))


def test_top_ring_has_slots_removed():
    """The top piece with rim-ring slots has less area than the plain top."""
    from shapely.ops import unary_union

    cfg = SpiralConfig(n_ribs=6)
    plain = build_top_spiral(cfg)
    slotted = plain.difference(unary_union(ring_slots(cfg)))
    assert slotted.area < plain.area


def test_base_disc_slots_removed_from_bottom():
    """The bottom piece with rib slots has less area than the plain disc+ramp."""
    from shapely.ops import unary_union

    cfg = SpiralConfig(n_ribs=6)
    plain = build_bottom_spiral(cfg)
    slotted = plain.difference(unary_union(base_disc_slots(cfg)))
    assert slotted.area < plain.area


# --- single-piece disc (the real fabrication model) ---


def test_disc_is_single_connected_piece():
    cfg = SpiralConfig()
    profile, cuts = build_disc(cfg)
    assert isinstance(profile, Polygon) and profile.is_valid
    r_hub, r_rim_in, r_outer = disc_radii(cfg)
    # Outer radius matches; solid hub present (center is material).
    reach = max(profile.bounds[2], profile.bounds[3])
    assert reach == pytest.approx(r_outer, abs=0.5)
    assert profile.contains(Point(0, 0)), "solid hub missing"
    # n_spirals open cuts, each spanning hub->rim (endpoints differ = open).
    assert len(cuts) == cfg.n_spirals
    for c in cuts:
        (x0, y0), (x1, y1) = c.coords[0], c.coords[-1]
        assert math.hypot(x1 - x0, y1 - y0) > cfg.strip_w_mm  # not a closed loop
        rs = [math.hypot(x, y) for x, y in c.coords]
        assert min(rs) == pytest.approx(r_hub, abs=1.0)
        assert max(rs) == pytest.approx(r_rim_in, abs=1.0)


def test_disc_cuts_do_not_reach_edges():
    """Cuts must stop inside the solid rim and outside the solid hub so the disc
    stays one connected piece."""
    cfg = SpiralConfig()
    _, _, r_outer = disc_radii(cfg)
    _, cuts = build_disc(cfg)
    for c in cuts:
        rs = [math.hypot(x, y) for x, y in c.coords]
        assert max(rs) < r_outer - 1.0  # stays inside the rim band
        assert min(rs) >= cfg.base_r - 1e-6  # starts at/after the hub edge


def test_disc_gcode_open_cuts_and_order():
    cfg = SpiralConfig()
    material = load_material("mdf_3mm")
    profile, cuts = build_disc(cfg)
    g = emit_disc_gcode(profile, cuts, material, "test", cfg)
    assert "$32=1" in g and ";MATERIAL: mdf_3mm" in g and ";LASER_MODE: static" in g
    assert "\nM4 " not in g
    assert g.count("; --- spiral cut") == cfg.n_spirals
    assert "disc profile" in g
    # rib slots come before the profile; profile is the last cut section.
    assert g.index("rib slot 1") < g.index("spiral cut 1") < g.index("disc profile")
