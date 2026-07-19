"""Spiral geometry for orpot — the single-piece expanding spiral disc.

The pot is ONE solid MDF disc with two interleaved thin spiral CUTS (open curves,
nothing removed). The cuts stop short of both the center and the edge, so the
disc stays connected: a solid central HUB, a solid outer RIM ring, and two spiral
ARMS joining them. Lifting the hub relative to the rim expands the arms into a
two-start helical wall (the classic laser-cut expanding basket). Separate radial
ribs slot into the hub and rim ring to lock the expanded height.

Key entry points:
  disc_radii(cfg)        — (r_hub, r_rim_in, r_outer)
  build_cut_spirals(cfg) — the n_spirals open spiral cut LineStrings
  build_disc(cfg)        — (profile polygon with hub/ring slots, cut lines)
  part_helix(name, cfg)  — 3D rails of an arm in the assembled cone (for views)

Tight pack: the ramp span is derived from strip width so the two 180-deg-offset
arms sit edge-to-edge (pitch = n_spirals*strip_w). Everything is in machine mm
(Y-up), built centered on the origin; `place()` shifts to positive coords.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from shapely.affinity import translate
from shapely.geometry import LineString, Point, Polygon

MM_PER_INCH = 25.4


@dataclass
class SpiralConfig:
    """Geometry for the single-piece spiral disc + ribs.

    TIGHT PACK: the arm radial span is derived from strip width so the two
    180°-offset arms sit edge-to-edge with no gap (pitch = n_spirals*strip_w)."""

    strip_w_mm: float = 0.5 * MM_PER_INCH  # arm/ribbon width = 12.7 (1/2 in)
    base_dia_mm: float = 2.0 * MM_PER_INCH  # center hub Ø = 50.8
    turns: float = 1.0  # revolutions per spiral
    n_spirals: int = 2  # interleaved arms (2 for now; 4 is a someday variant)
    top_pitch_mm: float | None = None  # per-rev advance override; None => span
    bottom_pitch_mm: float | None = None  # per-rev advance override; None => span
    top_ring_w_mm: float = 0.5 * MM_PER_INCH  # outer rim ring width = 12.7 (1/2 in)
    seg_mm: float = 0.5  # spiral point spacing (curve smoothness)
    min_segment_mm: float = 0.3  # gcode decimation floor (see emit.py)
    margin_mm: float = 8.0  # inset from origin so coords stay positive
    buffer_resolution: int = 16  # shapely quad_segs (round-join segs per quarter)
    rise_per_rev_mm: float = 40.0  # 3D lift per full revolution (flex limit)

    # --- vertical ribs (radial fins that lock the expanded disc) ---
    n_ribs: int = 4  # number of radial ribs
    rib_offset_deg: float = 0.0  # first rib azimuth
    material_th_mm: float = 3.0  # MDF thickness -> slot width & rib thickness
    slot_fit_mm: float = 0.1  # added to slot size for a slip fit (kerf gives clearance)
    rib_band_mm: float = 0.5 * MM_PER_INCH  # rib body height at the inner plateau
    rib_notch_depth_mm: float = 5.0  # ~5mm shelf/notch where a spiral rests
    rib_tab_len_mm: float = 5.0  # tab protrusion at each rib end (<= 5mm)
    rib_bot_tab_w_mm: float = 8.0  # radial width of the bottom (hub) tab
    rib_top_tab_w_mm: float = 8.0  # radial width of the top (rim-ring) tab

    # --- derived radii ---
    @property
    def base_r(self) -> float:
        return self.base_dia_mm / 2.0

    @property
    def span_r_lo(self) -> float:
        """Inner centerline radius of the ramps (on the base-disc edge)."""
        return self.base_r

    @property
    def span_r_hi(self) -> float:
        """Outer centerline radius of the arms (where the cuts end at the rim
        inner edge). Tight pack: pitch = n_spirals*strip_w so adjacent turns
        touch, giving span n_spirals*strip_w*turns over `turns` revolutions."""
        return self.span_r_lo + self.n_spirals * self.strip_w_mm * self.turns

    @property
    def ring_inner_r(self) -> float:
        """Rim ring inner edge = where the arms/cuts end."""
        return self.span_r_hi

    @property
    def top_outer_r(self) -> float:
        """Widest radius of the whole disc (rim ring outer edge)."""
        return self.ring_inner_r + self.top_ring_w_mm

    @property
    def ring_center_r(self) -> float:
        """Mid-radius of the rim ring (fixed distance from the outer edge; where
        the rib top tabs plug in)."""
        return self.top_outer_r - self.top_ring_w_mm / 2.0

    def z_at_r(self, r):
        """Assembled height vs centerline radius: the pot is a cone with the hub
        low at the center and the rim high at the outside, so z rises with
        radius (keeps both arms right-side up)."""
        lo, hi = self.span_r_lo, self.span_r_hi
        return self.rise_per_rev_mm * (r - lo) / (hi - lo)


def _spiral_polar(
    r0: float, pitch: float, turns: float, seg_mm: float
) -> tuple[np.ndarray, np.ndarray]:
    """Sampled (theta, r) for an Archimedean spiral r(theta) = r0 + (pitch/2pi)
    * theta over theta in [0, turns*2pi]. `pitch` is the signed radial advance
    per revolution (negative spirals inward). Points are spaced ~seg_mm apart
    along the (planar) arc length."""
    theta_max = turns * 2.0 * math.pi
    b = pitch / (2.0 * math.pi)  # dr/dtheta

    # Estimate total arc length to pick a point count giving ~seg_mm spacing.
    # Sum the chord lengths of a fine polyline (version-proof; avoids np.trapz,
    # which numpy 2.x renamed to np.trapezoid).
    coarse = np.linspace(0.0, theta_max, 2000)
    r_coarse = r0 + b * coarse
    xc = r_coarse * np.cos(coarse)
    yc = r_coarse * np.sin(coarse)
    arc_len = float(np.hypot(np.diff(xc), np.diff(yc)).sum())
    n = max(8, int(round(arc_len / max(seg_mm, 1e-3))) + 1)

    theta = np.linspace(0.0, theta_max, n)
    r = r0 + b * theta
    return theta, r


def _part_polar_params(name: str, cfg: SpiralConfig) -> tuple[float, float, float]:
    """Shared (r0, pitch, turns) for an arm centerline, so the flat cut and the
    3D helix agree. Both arms wind OUTWARD from r_lo to r_hi identically; in
    assembly the second is offset 180 deg (a two-start helix)."""
    r_lo = cfg.span_r_lo
    r_hi = cfg.span_r_hi
    span_pitch = (r_hi - r_lo) / cfg.turns  # radial advance per rev to span once
    override = cfg.top_pitch_mm if name == "top" else cfg.bottom_pitch_mm
    pitch = abs(override) if override is not None else span_pitch  # outward (+)
    return r_lo, pitch, cfg.turns


# ---------------------------------------------------------------------------
# Single-piece disc (the fabrication model)
# ---------------------------------------------------------------------------
#
# The pot is ONE solid MDF disc with two interleaved thin spiral CUTS (open
# curves) that leave it connected: a solid central hub, a solid outer rim, and
# two spiral arms joining them. Lifting the hub relative to the rim expands the
# arms into a two-start helical wall. Separate ribs slot into the hub and rim
# ring (NOT the arms) and cradle the arms in shelf notches.
#
# Phases: the two ARM centerlines sit at 0 and pi (reused by the ribs and the
# 3D view); the two CUTS sit halfway between, at pi/2 and 3pi/2, so each arm is
# strip_w wide.


def disc_radii(cfg: SpiralConfig) -> tuple[float, float, float]:
    """(r_hub, r_rim_in, r_outer): solid hub radius, inner edge of the solid rim
    band (where cuts end), and the disc outer radius."""
    r_hub = cfg.base_r
    span = cfg.n_spirals * cfg.strip_w_mm * cfg.turns  # cuts advance n*w per rev
    r_rim_in = r_hub + span
    r_outer = r_rim_in + cfg.top_ring_w_mm
    return r_hub, r_rim_in, r_outer


def build_cut_spirals(cfg: SpiralConfig) -> list[LineString]:
    """The n_spirals open spiral CUT paths, from the hub edge out to the rim
    inner edge, evenly phased on the boundaries between arms."""
    r_hub, r_rim_in, _ = disc_radii(cfg)
    pitch = cfg.n_spirals * cfg.strip_w_mm  # radial advance per full revolution
    cuts = []
    for k in range(cfg.n_spirals):
        phase = (2 * k + 1) * math.pi / cfg.n_spirals  # between arm centerlines
        theta, r = _spiral_polar(r_hub, pitch, cfg.turns, cfg.seg_mm)
        xs = r * np.cos(theta + phase)
        ys = r * np.sin(theta + phase)
        cuts.append(LineString(np.column_stack([xs, ys])))
    return cuts


def build_disc(cfg: SpiralConfig):
    """The single cut piece. Returns (profile, cuts):
    - profile: the outer disc Polygon with rib slots cut in the HUB and RIM RING
      (not the arms)
    - cuts: the open spiral cut LineStrings
    Everything centered on the origin."""
    from ribs import hub_slots, ring_slots  # local import avoids a cycle

    _, _, r_outer = disc_radii(cfg)
    profile = Point(0.0, 0.0).buffer(r_outer, quad_segs=cfg.buffer_resolution * 2)
    if cfg.n_ribs > 0:
        for slot in hub_slots(cfg) + ring_slots(cfg):
            profile = profile.difference(slot)
    if not isinstance(profile, Polygon):
        profile = max(profile.geoms, key=lambda g: g.area)
    return profile, build_cut_spirals(cfg)


def place(poly: Polygon, margin: float) -> Polygon:
    """Translate so the part's lower-left bbox corner sits at (margin, margin),
    keeping every coordinate positive for the machine work area."""
    minx, miny, _, _ = poly.bounds
    return translate(poly, xoff=margin - minx, yoff=margin - miny)


# Assembly phase of each arm (two-start helix: second arm offset 180 deg).
ASSEMBLY_PHASE_RAD = {"bottom": 0.0, "top": math.pi}


# ---------------------------------------------------------------------------
# 3D assembled form (for visualization)
# ---------------------------------------------------------------------------
#
# Lifting the disc's hub raises the arms into a cone: height rises linearly with
# radius (z_at_r). part_helix lofts an arm centerline into 3D rails for the
# wireframe/OpenSCAD views. The two arms are offset 180 deg (phase_rad).


def part_helix(name: str, cfg: SpiralConfig, phase_rad: float = 0.0) -> dict:
    """3D rails for one arm in its ASSEMBLED position (a cone whose height rises
    with radius). Returns dict of numpy (x,y,z) polylines 'center', 'inner',
    'outer' (edges offset RADIALLY by +/- strip_w/2), plus 'theta' and 'z_base'.
    phase_rad rotates the arm's azimuth for two-start interleaving."""
    r0, pitch, turns = _part_polar_params(name, cfg)
    theta, r = _spiral_polar(r0, pitch, turns, cfg.seg_mm)
    z = cfg.z_at_r(r)  # height follows radius (outer=rim high, inner=hub low)
    az = theta + phase_rad

    def rail(r_off: float) -> np.ndarray:
        rr = r + r_off
        return np.column_stack([rr * np.cos(az), rr * np.sin(az), z])

    return {
        "theta": theta,
        "center": rail(0.0),
        "inner": rail(-cfg.strip_w_mm / 2.0),
        "outer": rail(+cfg.strip_w_mm / 2.0),
        "z_base": 0.0,
    }
