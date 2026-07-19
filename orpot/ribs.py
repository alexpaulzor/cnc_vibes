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
from shapely.geometry import Polygon, box
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


def build_rib(cfg: SpiralConfig, azimuth_rad: float) -> Polygon:
    """Flat outline of one rib in the (s, z) plane.

    A solid wedge from the hub up the cone slant to the rim, with a <=5mm tab at
    each end (bottom -> hub slot, top -> rim-ring slot) and a ~notch_depth open
    shelf where each arm crosses so the arm rests in it. Solid below the notches,
    so they can't sever the rib; ribs differ only by notch positions."""
    r_hub, r_rim_in, r_outer, ring_center, rise = _disc_geom(cfg)
    nd = cfg.rib_notch_depth_mm
    h_min = cfg.rib_band_mm  # inner-plateau height (gives the bottom tab material)
    tab = min(cfg.rib_tab_len_mm, 5.0)
    r_flat = r_hub + h_min * (r_rim_in - r_hub) / rise  # slant reaches h_min here

    wedge = Polygon(
        [
            (r_hub, 0.0),  # inner floor
            (r_outer, 0.0),  # outer floor (out under the ring)
            (r_outer, rise),  # outer top at the rim
            (r_rim_in, rise),  # flat top meets the slant where arms end
            (r_flat, h_min),  # slant down to the inner plateau
            (r_hub, h_min),  # inner plateau
        ]
    )
    # Bottom tab: at the inner end, reaching INTO the solid hub (r < r_hub) and
    # dropping below z=0 into the hub slot.
    btw = cfg.rib_bot_tab_w_mm
    bot_tab = box(r_hub - btw, -tab, r_hub, h_min)
    # Top tab: at the ring center, rising above the rim into the ring slot.
    ttw = cfg.rib_top_tab_w_mm / 2.0
    top_tab = box(ring_center - ttw, rise - h_min, ring_center + ttw, rise + tab)
    outline = unary_union([wedge, bot_tab, top_tab])

    # Open shelf-notches where each arm crosses (skip the hub/rim ends, handled by
    # the tabs). Each removes a ~nd-deep bite from the top edge; the arm rests on
    # the notch floor.
    sw = (cfg.strip_w_mm + cfg.slot_fit_mm) / 2.0  # half notch width (radial)
    eps = 1.0
    for _, r, z in rib_crossings(cfg, azimuth_rad):
        if not (eps < z < rise - eps):
            continue
        outline = outline.difference(box(r - sw, z - nd, r + sw, 1e4))

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
