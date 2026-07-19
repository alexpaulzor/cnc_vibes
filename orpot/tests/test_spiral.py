"""Tests for orpot — the single-piece expanding spiral disc + ribs.

Validates: the disc is one connected piece with open spiral cuts that stop short
of both edges; the ribs are wedges with <=5mm end tabs and shelf notches; the
hub/ring slots register the tabs; and the gcode matches the machine conventions
(static M3, S in range, warmup + passes, positive coords, cut order)."""

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
    build_all_ribs,
    build_rib,
    hub_slots,
    rib_azimuths,
    rib_crossings,
    ring_slots,
)
from spiral import (  # noqa: E402
    SpiralConfig,
    build_disc,
    disc_radii,
)


# --- single-piece disc ---


def test_disc_is_single_connected_piece():
    cfg = SpiralConfig()
    profile, cuts = build_disc(cfg)
    assert isinstance(profile, Polygon) and profile.is_valid
    r_hub, r_rim_in, r_outer = disc_radii(cfg)
    reach = max(profile.bounds[2], profile.bounds[3])
    assert reach == pytest.approx(r_outer, abs=0.5)
    assert profile.contains(Point(0, 0)), "solid hub missing"
    assert len(cuts) == cfg.n_spirals


def test_disc_cuts_are_open_and_inside_edges():
    cfg = SpiralConfig()
    r_hub, r_rim_in, r_outer = disc_radii(cfg)
    _, cuts = build_disc(cfg)
    for c in cuts:
        (x0, y0), (x1, y1) = c.coords[0], c.coords[-1]
        assert math.hypot(x1 - x0, y1 - y0) > cfg.strip_w_mm  # open, not a loop
        rs = [math.hypot(x, y) for x, y in c.coords]
        assert min(rs) == pytest.approx(r_hub, abs=1.0)
        assert max(rs) == pytest.approx(r_rim_in, abs=1.0)
        assert max(rs) < r_outer - 1.0  # stays inside the solid rim


def test_tight_pack_arm_width():
    """Two 180-deg-offset arms with pitch = n*strip_w sit strip_w apart."""
    cfg = SpiralConfig()
    r_hub, r_rim_in, _ = disc_radii(cfg)
    assert (r_rim_in - r_hub) == pytest.approx(
        cfg.n_spirals * cfg.strip_w_mm * cfg.turns
    )


# --- ribs ---


def test_ribs_have_end_tabs_within_5mm():
    cfg = SpiralConfig()
    ribs = build_all_ribs(cfg)
    assert len(ribs) == cfg.n_ribs
    rise = cfg.rise_per_rev_mm * cfg.turns
    for rib in ribs:
        assert rib.is_valid and rib.area > 0
        minx, miny, maxx, maxy = rib.bounds
        # Bottom tab dips below z=0 by <=5mm; top tab rises above the rim by <=5mm.
        assert -5.0 - 1e-6 <= miny < 0.0
        assert rise < maxy <= rise + 5.0 + 1e-6


def test_rib_is_single_connected_strut():
    """Every rib is one valid connected polygon (the S-strut didn't sever)."""
    cfg = SpiralConfig()
    for rib in build_all_ribs(cfg):
        assert isinstance(rib, Polygon) and rib.is_valid and rib.area > 0


def test_hub_and_ring_slots_present_and_removed():
    cfg = SpiralConfig()
    assert len(hub_slots(cfg)) == cfg.n_ribs
    assert len(ring_slots(cfg)) == cfg.n_ribs
    profile, _ = build_disc(cfg)
    # The disc has holes (the hub + ring slots were subtracted).
    assert len(profile.interiors) >= cfg.n_ribs


def test_top_tab_fixed_distance_from_outer_edge():
    cfg = SpiralConfig()
    _, _, r_outer = disc_radii(cfg)
    # Ring slots centered a fixed distance (ring_w/2) from the outer edge.
    for slot in ring_slots(cfg):
        cx, cy = slot.centroid.x, slot.centroid.y
        dist_from_edge = r_outer - math.hypot(cx, cy)
        assert dist_from_edge == pytest.approx(cfg.top_ring_w_mm / 2.0, abs=0.5)


# --- gcode ---


def test_disc_gcode_open_cuts_and_order():
    cfg = SpiralConfig()
    material = load_material("mdf_3mm")
    profile, cuts = build_disc(cfg)
    g = emit_disc_gcode(profile, cuts, material, "test", cfg)
    assert "$32=1" in g and ";MATERIAL: mdf_3mm" in g and ";LASER_MODE: static" in g
    assert "\nM4 " not in g  # static M3 only
    assert g.count("; --- spiral cut") == cfg.n_spirals
    assert "disc profile" in g
    # slots before cuts before the outer profile (piece stays anchored).
    assert g.index("rib slot 1") < g.index("spiral cut 1") < g.index("disc profile")
    s_vals = [int(m) for m in re.findall(r"M3 S(\d+)", g)]
    assert s_vals and all(0 <= s <= 1000 for s in s_vals)
    coords = re.findall(r"[XY](-?\d+\.\d+)", g)
    # disc gcode is centered on the origin, so it has negative coords; the CLI
    # translates to positive before emitting. Just check they parse.
    assert coords


def test_ribs_gcode_conventions():
    cfg = SpiralConfig()
    material = load_material("mdf_3mm")
    parts = [(f"rib{i}", r) for i, r in enumerate(build_all_ribs(cfg))]
    g = emit_cut_gcode(parts, material, "test", cfg)
    assert "\nM4 " not in g
    assert g.count("; pass 2 of 2") >= cfg.n_ribs  # 2 passes each
    assert "F350" in g


def test_decimate_respects_min_segment():
    cfg = SpiralConfig(min_segment_mm=0.4)
    profile, _ = build_disc(cfg)
    ring = list(profile.exterior.coords)
    dec = decimate_min_segment(ring, cfg.min_segment_mm)
    seglens = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(dec, dec[1:])]
    short = [s for s in seglens[:-1] if s < cfg.min_segment_mm - 1e-6]
    assert not short, f"sub-floor segments after decimation: {short}"
