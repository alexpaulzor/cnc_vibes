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
from shapely.geometry import LineString, Polygon, box
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
    """Flat cut outline of one rib (in the s-z plane), with capture-slot holes
    and a base tab. z=0 is the floor; the tab drops to negative z.

    cfg.rib_style: "panel" = solid rectangular fin; "spine" (default) = a narrow
    strut skeleton — a small collar of material around each capture slot, joined
    by thin struts down to the base tab (minimal material, most open)."""
    crossings = rib_crossings(cfg, azimuth_rad)
    # The bottom spiral's inner start (z~0, r~base_r) is fused into the base
    # disc — it's the anchor, not a free ribbon, so it needs no capture slot.
    crossings = [
        c
        for c in crossings
        if not (c[0] == "bottom" and c[2] < 1e-6 and abs(c[1] - cfg.base_r) < 1e-3)
    ]
    if not crossings:
        raise ValueError("rib azimuth crosses no ramp")

    half_w = cfg.strip_w_mm / 2.0
    fit = cfg.slot_fit_mm
    sh = (cfg.material_th_mm + fit) / 2.0  # half slot height (z)
    sw = (cfg.strip_w_mm + fit) / 2.0  # half slot length (radial)

    # Base tab (plugs down into the base disc).
    t0, t1 = cfg.rib_tab_span_mm
    tab = box(t0, -cfg.rib_tab_depth_mm, t1, 0.0)
    tab_top = ((t0 + t1) / 2.0, 0.0)

    if cfg.rib_style == "panel":
        s_out = max(r for _, r, _ in crossings) + half_w + cfg.rib_edge_margin_mm
        s_in = min(cfg.rib_inner_r_mm, min(r for _, r, _ in crossings) - half_w)
        z_top = max(z for _, _, z in crossings) + cfg.rib_top_lip_mm
        outline = unary_union([box(s_in, 0.0, s_out, z_top), tab])
    else:  # spine
        m = cfg.rib_collar_margin_mm
        hw = cfg.rib_strut_w_mm / 2.0
        pieces = [tab]
        # A collar (small rounded-corner box) of material around each slot.
        nodes = []  # collar centers, to route struts through
        for _, r, z in crossings:
            pieces.append(box(r - sw - m, z - sh - m, r + sw + m, z + sh + m))
            nodes.append((r, z))
        # Route struts: base tab -> nodes ordered by height (low to high).
        route = [tab_top] + sorted(nodes, key=lambda p: p[1])
        for a, b in zip(route, route[1:]):
            pieces.append(LineString([a, b]).buffer(hw, cap_style="round"))
        outline = unary_union(pieces)

    # Clamp the body to z>=0 (a floor-level end slot becomes an open-bottom
    # notch rather than material poking below the table), then add the tab back.
    outline = outline.intersection(box(-1e4, 0.0, 1e4, 1e4)).union(tab)

    # Capture slots: material-thickness tall x strip-width long, + slip fit.
    for _, r, z in crossings:
        outline = outline.difference(box(r - sw, z - sh, r + sw, z + sh))

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
