"""Rib geometry for orpot — the single-piece expanding disc.

The disc expands into a cone (height rises with radius). N separate radial ribs
lock it open. Each rib is a flat MDF wedge standing in a radial plane, spanning
from the hub (inner, low) up the cone slant to the rim (outer, high):

  - a tab (<= rib_tab_len) at the INNER-bottom end plugs into a slot in the HUB
  - a tab (<= rib_tab_len) at the OUTER-top end plugs into a slot in the RIM RING,
    always the same distance from the outer edge (the ring center)
  - a ~rib_notch_depth shelf/notch wherever a spiral arm crosses, so the arm
    rests in it (the arms themselves are NOT slotted)

Ribs are built in the (s, z) plane (s = radius from the axis, z = assembled
height). `rib_3d` maps a rib into the assembled 3D view. `hub_slots`/`ring_slots`
return the holes to subtract from the single disc.
"""

from __future__ import annotations

import math

import numpy as np
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

from spiral import ASSEMBLY_PHASE_RAD, SpiralConfig, _part_polar_params, disc_radii


def rib_azimuths(cfg: SpiralConfig) -> list[float]:
    """Physical azimuths (radians) of the ribs."""
    off = math.radians(cfg.rib_offset_deg)
    return [off + i * 2.0 * math.pi / cfg.n_ribs for i in range(cfg.n_ribs)]


def _disc_geom(cfg: SpiralConfig):
    r_hub, r_rim_in, r_outer = disc_radii(cfg)
    ring_center = r_outer - cfg.top_ring_w_mm / 2.0  # fixed dist from outer edge
    rise = cfg.rise_per_rev_mm * cfg.turns  # total assembled height
    return r_hub, r_rim_in, r_outer, ring_center, rise


def rib_crossings(
    cfg: SpiralConfig, azimuth_rad: float
) -> list[tuple[str, float, float]]:
    """(name, r, z) for every point where a spiral arm crosses this rib's plane.
    A one-rev arm crosses a given azimuth once per revolution; two arms 180 deg
    apart give the staircase of crossings the rib must support."""
    out = []
    span = cfg.turns * 2.0 * math.pi
    for name in ("bottom", "top"):
        r0, pitch, _ = _part_polar_params(name, cfg)
        base = (azimuth_rad - ASSEMBLY_PHASE_RAD[name]) % (2.0 * math.pi)
        k = 0
        while base + k * 2.0 * math.pi <= span + 1e-6:
            theta = base + k * 2.0 * math.pi
            r = r0 + (pitch / (2.0 * math.pi)) * theta
            out.append((name, r, cfg.z_at_r(r)))
            k += 1
    return out


def _cubic(p0, p1, p2, p3, t):
    mt = 1.0 - t
    x = mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t**2 * p2[0] + t**3 * p3[0]
    y = mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t**2 * p2[1] + t**3 * p3[1]
    return (x, y)


def build_rib(cfg: SpiralConfig, azimuth_rad: float) -> Polygon:
    """Flat outline of one rib in the (s, z) plane — a constant-width S-curve
    strut. Its centerline sweeps as an ogee from the rim ring (top, outer) down
    and INWARD, tucking under the spiral's slope, to the hub (bottom, inner), so
    the rib sits mostly on the inside and reads as a graceful S rather than a
    block. A <=5mm tab at each end plugs into a rim-ring slot (top) and a hub
    slot (bottom). Same shape for every rib."""
    r_hub, r_rim_in, r_outer, ring_center, rise = _disc_geom(cfg)
    tab = min(cfg.rib_tab_len_mm, 5.0)
    hw = cfg.rib_band_mm / 2.0  # strut half-width
    btw = cfg.rib_bot_tab_w_mm
    ttw = cfg.rib_top_tab_w_mm / 2.0

    top = (ring_center, rise - hw)  # top end (round cap reaches the rim, z=rise)
    bot = (r_hub, hw)  # bottom end (round cap reaches the floor, z=0)
    # Ogee centerline: leave the rim heading straight down (outer), sweep inward
    # across the middle, then drop into the hub (inner) — an S.
    p1 = (ring_center, rise * 0.55)
    p2 = (r_hub, rise * 0.45)
    cl = [_cubic(top, p1, p2, bot, t) for t in np.linspace(0.0, 1.0, 80)]
    strut = LineString(cl).buffer(hw, cap_style="round", join_style="round")

    # Tabs at each end (<=5mm protrusion beyond the rim / below the hub floor).
    top_tab = box(ring_center - ttw, rise - hw, ring_center + ttw, rise + tab)
    bot_tab = box(r_hub - btw, -tab, r_hub + hw, hw)
    outline = unary_union([strut, top_tab, bot_tab])

    if not isinstance(outline, Polygon):
        outline = max(outline.geoms, key=lambda g: g.area)
    return outline


def build_all_ribs(cfg: SpiralConfig) -> list[Polygon]:
    return [build_rib(cfg, a) for a in rib_azimuths(cfg)]


def _radial_slots(cfg: SpiralConfig, r0: float, r1: float) -> list[Polygon]:
    """One material-thick radial slot per rib azimuth, spanning r0..r1."""
    hw = (cfg.material_th_mm + cfg.slot_fit_mm) / 2.0  # half slot width (tangential)
    slots = []
    for a in rib_azimuths(cfg):
        ca, sa = math.cos(a), math.sin(a)
        corners = [(r0, -hw), (r1, -hw), (r1, hw), (r0, hw)]
        slots.append(Polygon([(x * ca - y * sa, x * sa + y * ca) for x, y in corners]))
    return slots


def hub_slots(cfg: SpiralConfig) -> list[Polygon]:
    """Slots in the solid hub (r < r_hub) for the rib bottom tabs."""
    r_hub, *_ = _disc_geom(cfg)
    return _radial_slots(cfg, r_hub - cfg.rib_bot_tab_w_mm, r_hub)


def ring_slots(cfg: SpiralConfig) -> list[Polygon]:
    """Slots in the rim ring for the rib top tabs (at the ring center)."""
    _, _, _, ring_center, _ = _disc_geom(cfg)
    ttw = cfg.rib_top_tab_w_mm / 2.0
    return _radial_slots(cfg, ring_center - ttw, ring_center + ttw)


def rib_3d(cfg: SpiralConfig, azimuth_rad: float) -> dict:
    """Map a rib's outline (and any holes) into the assembled 3D view: each
    (s, z) becomes (s*cos, s*sin, z) at this azimuth."""
    poly = build_rib(cfg, azimuth_rad)
    ca, sa = math.cos(azimuth_rad), math.sin(azimuth_rad)

    def lift(ring) -> np.ndarray:
        return np.array([(s * ca, s * sa, z) for s, z in ring.coords])

    return {"exterior": lift(poly.exterior), "holes": [lift(r) for r in poly.interiors]}
