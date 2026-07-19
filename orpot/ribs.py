"""Vertical rib geometry for orpot.

The two flat spiral ramps are held at their graduated heights by N radial ribs
(default 6, at 60 deg spacing). Each rib is a flat MDF fin standing vertically
in a radial plane; where a ramp crosses that plane the rib has a horizontal
CAPTURE SLOT (material thickness tall x strip width long) that the flexible
ribbon threads through, holding it top and bottom without glue. Each rib also
has a tab at its inner-bottom that plugs into a slot in the base disc.

Because the two ramps are a two-start helix (top offset 180 deg), a rib at a
given azimuth meets the two ramps at two different heights -> two slots per rib,
distributed around the pot like a spiral staircase.

Ribs are built in the (s, z) plane (s = radius from the pot axis, z = height);
`rib_3d` maps a rib into the assembled 3D view, and `base_disc_slots` returns
the holes to subtract from the base disc.
"""

from __future__ import annotations

import math

import numpy as np
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from spiral import ASSEMBLY_PHASE_RAD, SpiralConfig, _part_polar_params


def rib_azimuths(cfg: SpiralConfig) -> list[float]:
    """Physical azimuths (radians) of the ribs."""
    off = math.radians(cfg.rib_offset_deg)
    return [off + i * 2.0 * math.pi / cfg.n_ribs for i in range(cfg.n_ribs)]


def rib_crossings(
    cfg: SpiralConfig, azimuth_rad: float
) -> list[tuple[str, float, float]]:
    """(name, r_center, z) for every point where a ramp crosses this rib's plane.

    A one-revolution spiral starts AND ends at the same azimuth, so a rib on an
    end seam (default: az 0 for the bottom, az 180 for the top) sees that ramp
    twice — at its inner and outer endpoints. That is exactly how the free ends
    get captured/locked into the ribs."""
    out = []
    span = cfg.turns * 2.0 * math.pi
    for name in ("bottom", "top"):
        r0, pitch, _ = _part_polar_params(name, cfg)
        base = (azimuth_rad - ASSEMBLY_PHASE_RAD[name]) % (2.0 * math.pi)
        k = 0
        while base + k * 2.0 * math.pi <= span + 1e-6:
            theta = base + k * 2.0 * math.pi
            r = r0 + (pitch / (2.0 * math.pi)) * theta
            z = cfg.z_at_r(r)  # height follows radius (rim high, base low)
            out.append((name, r, z))
            k += 1
    return out


def build_rib(cfg: SpiralConfig, azimuth_rad: float) -> Polygon:
    """Flat cut outline of one rib (in the s-z plane).

    The pot is a cone whose height rises linearly with radius, so its slant is a
    straight line from the base disc (r_lo, 0) up to the rim (r_hi, rise). Every
    rib is the same solid wedge: an inner plateau (min height for the base tab), a
    slant up to the rim height where the spirals cross, then a flat top running
    out to the rim ring where a top tab plugs in. Wherever a MID spiral crossing
    lands on the slant, an open notch (>= rib_notch_depth) is carved from the top
    edge so the ramp rests in it — solid below, so notches never sever it; only
    the notch positions differ between ribs, so they're ~identical. End crossings
    (at the base or rim) are anchored by the disc/ring, not notched."""
    rise = cfg.rise_per_rev_mm
    nd = cfg.rib_notch_depth_mm
    h_min = cfg.rib_band_mm  # minimum (inner) rib height
    # Notch only the clearly mid-height crossings; near-base (z<=h_min) and
    # near-rim (z>=rise-h_min) crossings are supported by the base disc / rim
    # ring's flat top, so notching them would collide with the tabs.
    crossings = [
        c for c in rib_crossings(cfg, azimuth_rad) if h_min < c[2] < rise - h_min
    ]

    r_lo, r_hi = cfg.span_r_lo, cfg.span_r_hi
    r_ring = cfg.ring_center_r  # rib reaches out to the ring to meet the top tab
    r_flat = r_lo + h_min * (r_hi - r_lo) / rise  # radius where the slant hits h_min
    t0, t1 = cfg.rib_tab_span_mm
    ttw = cfg.rib_top_tab_w_mm / 2.0

    wedge = Polygon(
        [
            (t0, 0.0),  # inner floor (reaches the base tab)
            (r_ring, 0.0),  # outer floor (out to the ring)
            (r_ring, rise),  # outer top at the rim ring
            (r_hi, rise),  # flat top meets the slant at the spiral's outer end
            (r_flat, h_min),  # slant down to the min-height plateau
            (t0, h_min),  # inner plateau
        ]
    )
    base_tab = box(t0, -cfg.rib_tab_depth_mm, t1, h_min)
    top_tab = box(
        r_ring - ttw, rise - h_min, r_ring + ttw, rise + cfg.rib_top_tab_up_mm
    )
    outline = unary_union([wedge, base_tab, top_tab])

    # Open-top notches (vertical U-slots) where each mid ramp crossing rests. The
    # ramp lies flat, so the notch is a plain column open to the top; the solid
    # wedge remains below it, so it can't sever the rib.
    sw = (cfg.strip_w_mm + cfg.slot_fit_mm) / 2.0  # half notch width (radial)
    for _, r, z in crossings:
        outline = outline.difference(box(r - sw, z - nd, r + sw, 1e4))

    if not isinstance(outline, Polygon):
        outline = max(outline.geoms, key=lambda g: g.area)
    return outline


def build_all_ribs(cfg: SpiralConfig) -> list[Polygon]:
    return [build_rib(cfg, a) for a in rib_azimuths(cfg)]


def _radial_slots(cfg: SpiralConfig, r0: float, r1: float) -> list[Polygon]:
    """Radial through-slots (material-thick wide) at each rib azimuth, spanning
    r0..r1 — used both for the base-disc tabs and the rim-ring tabs."""
    hw = (cfg.material_th_mm + cfg.slot_fit_mm) / 2.0  # half slot width (tangential)
    slots = []
    for a in rib_azimuths(cfg):
        ca, sa = math.cos(a), math.sin(a)
        corners = [(r0, -hw), (r1, -hw), (r1, hw), (r0, hw)]
        slots.append(Polygon([(x * ca - y * sa, x * sa + y * ca) for x, y in corners]))
    return slots


def base_disc_slots(cfg: SpiralConfig) -> list[Polygon]:
    """Slots in the base disc for the rib base tabs."""
    t0, t1 = cfg.rib_tab_span_mm
    return _radial_slots(cfg, t0, t1)


def ring_slots(cfg: SpiralConfig) -> list[Polygon]:
    """Slots in the top rim ring for the rib top tabs (centered at the ring)."""
    ttw = cfg.rib_top_tab_w_mm / 2.0
    return _radial_slots(cfg, cfg.ring_center_r - ttw, cfg.ring_center_r + ttw)


def rib_3d(cfg: SpiralConfig, azimuth_rad: float) -> dict:
    """Map a rib's outline (and slot holes) into the assembled 3D view: each
    (s, z) becomes (s*cos, s*sin, z) at this azimuth. Returns exterior + holes
    as lists of Nx3 arrays for the wireframe sketch."""
    poly = build_rib(cfg, azimuth_rad)
    ca, sa = math.cos(azimuth_rad), math.sin(azimuth_rad)

    def lift(ring) -> np.ndarray:
        pts = [(s * ca, s * sa, z) for s, z in ring.coords]
        return np.array(pts)

    return {
        "exterior": lift(poly.exterior),
        "holes": [lift(r) for r in poly.interiors],
    }
