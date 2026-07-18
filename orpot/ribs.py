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

from spiral import SpiralConfig, crossing_rz


def rib_azimuths(cfg: SpiralConfig) -> list[float]:
    """Physical azimuths (radians) of the ribs."""
    off = math.radians(cfg.rib_offset_deg)
    return [off + i * 2.0 * math.pi / cfg.n_ribs for i in range(cfg.n_ribs)]


def rib_crossings(
    cfg: SpiralConfig, azimuth_rad: float
) -> list[tuple[str, float, float]]:
    """(name, r_center, z) for each ramp that crosses this rib's plane."""
    out = []
    for name in ("bottom", "top"):
        rz = crossing_rz(name, cfg, azimuth_rad)
        if rz is not None:
            out.append((name, rz[0], rz[1]))
    return out


def build_rib(cfg: SpiralConfig, azimuth_rad: float) -> Polygon:
    """Flat cut outline of one rib (in the s-z plane), with capture-slot holes
    and a base tab. z=0 is the floor; the tab drops to negative z."""
    crossings = rib_crossings(cfg, azimuth_rad)
    if not crossings:
        raise ValueError("rib azimuth crosses no ramp")

    half_w = cfg.strip_w_mm / 2.0
    s_out = max(r for _, r, _ in crossings) + half_w + cfg.rib_edge_margin_mm
    s_in = min(cfg.rib_inner_r_mm, min(r for _, r, _ in crossings) - half_w)
    z_top = max(z for _, _, z in crossings) + cfg.rib_top_lip_mm

    body = box(s_in, 0.0, s_out, z_top)

    # Base tab (plugs down into the base disc).
    t0, t1 = cfg.rib_tab_span_mm
    t0 = max(t0, s_in)
    t1 = min(t1, s_out)
    tab = box(t0, -cfg.rib_tab_depth_mm, t1, 0.0)
    outline = unary_union([body, tab])

    # Capture slots: material-thickness tall x strip-width long, + slip fit.
    fit = cfg.slot_fit_mm
    sh = (cfg.material_th_mm + fit) / 2.0  # half slot height
    sw = (cfg.strip_w_mm + fit) / 2.0  # half slot length (radial)
    for _, r, z in crossings:
        slot = box(r - sw, z - sh, r + sw, z + sh)
        outline = outline.difference(slot)

    if not isinstance(outline, Polygon):
        outline = max(outline.geoms, key=lambda g: g.area)
    return outline


def build_all_ribs(cfg: SpiralConfig) -> list[Polygon]:
    return [build_rib(cfg, a) for a in rib_azimuths(cfg)]


def base_disc_slots(cfg: SpiralConfig) -> list[Polygon]:
    """Rectangular through-slots to subtract from the base disc so the rib tabs
    plug in. Each is at a rib azimuth, radial [tab span], material-thick wide."""
    t0, t1 = cfg.rib_tab_span_mm
    hw = (cfg.material_th_mm + cfg.slot_fit_mm) / 2.0  # half slot width (tangential)
    slots = []
    for a in rib_azimuths(cfg):
        # A slot centered on the radial line at azimuth a, from r=t0..t1.
        ca, sa = math.cos(a), math.sin(a)
        # local rectangle [t0,t1] x [-hw,hw] rotated by a
        corners = [(t0, -hw), (t1, -hw), (t1, hw), (t0, hw)]
        pts = [(x * ca - y * sa, x * sa + y * ca) for x, y in corners]
        slots.append(Polygon(pts))
    return slots


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
