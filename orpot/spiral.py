"""Spiral geometry for orpot — the laser-cut orchid pot.

Phase 1 builds ONLY the two flat spiral ribbons. The physical trick: 3mm MDF is
cut flat here, then you lift one end and the ribbon flexes/twists up into a 3D
coil (like a lifted paper party-spiral). How tall it rises before cracking is an
empirical limit found by hand — so we cut plain spirals for flex-testing and
defer the vertical ribs, the interlocking end-joint, and any kerf-bending.

Two parts, both one revolution by default:

  top spiral    — a constant-width ribbon, the pot RIM. Its outer edge starts at
                  the widest radius and spirals INWARD one turn (a "washer that
                  winds in"). Default pitch = strip width, so turns nest.

  bottom spiral — a solid base disc (the footprint) unioned with a ribbon that
                  spirals OUTWARD one turn from the disc edge to the top spiral's
                  outer radius. The open spiral gap is the drainage/airflow.

Everything is in machine mm (Y-up). Parts are built centered on the origin;
`place()` shifts a part so all coordinates are >= margin (GRBL positive work
area). A one-revolution offset ribbon leaves a radial slit, so each part is a
single simply-connected polygon with no interior hole — cutting its exterior
ring frees the piece.
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
    """Geometry for both spirals. Defaults follow the user's rough numbers
    (4in inner rim Ø, 15mm strip, 2in base Ø); all are CLI-overridable."""

    inner_dia_mm: float = 4.0 * MM_PER_INCH  # top spiral inner Ø (rim opening) = 101.6
    strip_w_mm: float = 15.0  # ribbon width for both spirals
    base_dia_mm: float = 2.0 * MM_PER_INCH  # bottom spiral base disc Ø = 50.8
    turns: float = 1.0  # revolutions per spiral
    top_pitch_mm: float | None = None  # None => symmetric span (winds in to base_r)
    bottom_pitch_mm: float | None = None  # None => symmetric span (winds out to R_max)
    top_ring_w_mm: float = 12.0  # radial width of the top spiral's full outer rim ring
    seg_mm: float = 0.5  # spiral point spacing (curve smoothness)
    min_segment_mm: float = 0.3  # gcode decimation floor (see emit.py)
    margin_mm: float = 8.0  # inset from origin so coords stay positive
    buffer_resolution: int = 16  # shapely quad_segs (round-join segs per quarter)
    rise_per_rev_mm: float = 40.0  # 3D lift per full revolution (flex limit)

    # --- vertical ribs (radial fins that capture both ramps) ---
    n_ribs: int = 6  # number of radial ribs
    rib_offset_deg: float = 0.0  # first rib azimuth; 0 puts ribs on the end seams
    material_th_mm: float = 3.0  # MDF thickness -> notch/slot width & rib thickness
    slot_fit_mm: float = 0.1  # added to slot size for a slip fit (kerf gives clearance)
    rib_band_mm: float = 10.0  # width of the diagonal rib strut (below the cone slant)
    rib_notch_depth_mm: float = 3.5  # open notch depth where a spiral rests (>= 3mm)
    rib_tab_span_mm: tuple = (12.0, 22.0)  # radial [inner, outer] of the base-disc tab
    rib_tab_depth_mm: float = (
        4.0  # how far the base tab drops below z=0 (into the disc)
    )
    rib_top_tab_w_mm: float = 10.0  # width of the top tab that enters the rim ring
    rib_top_tab_up_mm: float = 4.0  # how far the top tab rises through the rim ring

    # --- derived radii ---
    @property
    def top_inner_r(self) -> float:
        return self.inner_dia_mm / 2.0

    @property
    def top_outer_r(self) -> float:
        """Widest radius of the whole pot (rim outside) = the shared R_max."""
        return self.top_inner_r + self.strip_w_mm

    @property
    def base_r(self) -> float:
        return self.base_dia_mm / 2.0

    # Shared centerline radial span of both spirals (bottom out, top in).
    @property
    def span_r_lo(self) -> float:
        return self.base_r

    @property
    def span_r_hi(self) -> float:
        # Spiral runs a full turn whose outer end sits at the rim ring's INNER
        # edge, overlapping half a strip into the ring so the ribbon merges with
        # it (a clean blend, and a full turn is visible inside the ring).
        return self.top_outer_r - self.top_ring_w_mm

    @property
    def ring_center_r(self) -> float:
        """Mid-radius of the rim ring (where rib top tabs plug in)."""
        return self.top_outer_r - self.top_ring_w_mm / 2.0

    def z_at_r(self, r):
        """Assembled height as a function of centerline radius: the pot is a cone
        with the base disc low at the center and the rim ring high at the outside,
        so z rises with radius (this is what keeps BOTH spirals right-side up)."""
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


def _spiral_centerline(
    r0: float, pitch: float, turns: float, seg_mm: float
) -> LineString:
    """Planar centerline polyline for the flat cut."""
    theta, r = _spiral_polar(r0, pitch, turns, seg_mm)
    xs = r * np.cos(theta)
    ys = r * np.sin(theta)
    return LineString(np.column_stack([xs, ys]))


def _ribbon(centerline: LineString, width: float, quad_segs: int) -> Polygon:
    """Constant-width ribbon around a centerline. Flat end caps give clean
    mating faces for the (future) interlocking joint; round joins keep the
    inner/outer edges smooth."""
    poly = centerline.buffer(
        width / 2.0,
        cap_style="flat",
        join_style="round",
        quad_segs=quad_segs,
    )
    if not isinstance(poly, Polygon):
        # Degenerate configs shouldn't split the ribbon, but guard anyway:
        # keep the largest piece.
        poly = max(poly.geoms, key=lambda g: g.area)
    return poly


def _part_polar_params(name: str, cfg: SpiralConfig) -> tuple[float, float, float]:
    """Shared (r0, pitch, turns) for a part's centerline, so the flat cut and
    the 3D helix are guaranteed to agree. pitch is signed (negative = inward).

    Both spirals span the SAME centerline radial range [r_lo, r_hi], in opposite
    directions, so each one's start radius equals the other's end radius (needed
    for the interlocking end-joint):
      bottom: r_lo -> r_hi (winds out)     top: r_hi -> r_lo (winds in)
    where r_lo = base_r and r_hi = top_outer_r - strip_w/2 (outer edge on R_max).
    The optional *_pitch_mm overrides break the symmetry if you really want it."""
    r_lo = cfg.span_r_lo
    r_hi = cfg.span_r_hi
    span_pitch = (r_hi - r_lo) / cfg.turns  # radial advance per rev to span it once
    if name == "top":
        r0 = r_hi
        pitch = -(abs(cfg.top_pitch_mm) if cfg.top_pitch_mm is not None else span_pitch)
    elif name == "bottom":
        r0 = r_lo
        pitch = (
            abs(cfg.bottom_pitch_mm) if cfg.bottom_pitch_mm is not None else span_pitch
        )
    else:
        raise ValueError(f"unknown part: {name!r} (expected 'top' or 'bottom')")
    return r0, pitch, cfg.turns


def build_top_spiral(cfg: SpiralConfig) -> Polygon:
    """Top piece: a full outer RIM RING (a complete circle at the outer radius)
    that blends into a ribbon winding INWARD one turn. The ring is the top
    piece's anchor (mirror of the bottom's center base disc); the ribbon's outer
    start merges smoothly into it."""
    r0, pitch, turns = _part_polar_params("top", cfg)
    line = _spiral_centerline(r0=r0, pitch=pitch, turns=turns, seg_mm=cfg.seg_mm)
    ribbon = _ribbon(line, cfg.strip_w_mm, cfg.buffer_resolution)
    # Full outer rim ring: annulus from (top_outer_r - ring_w) to top_outer_r.
    outer = Point(0.0, 0.0).buffer(cfg.top_outer_r, quad_segs=cfg.buffer_resolution * 2)
    inner = Point(0.0, 0.0).buffer(
        cfg.top_outer_r - cfg.top_ring_w_mm, quad_segs=cfg.buffer_resolution * 2
    )
    ring = outer.difference(inner)
    part = ribbon.union(ring)
    if not isinstance(part, Polygon):
        part = max(part.geoms, key=lambda g: g.area)
    return part


def build_bottom_spiral(cfg: SpiralConfig) -> Polygon:
    """Base disc (footprint) unioned with a ribbon spiralling OUTWARD one turn
    from the disc edge to the pot's max radius. The centerline starts ON the
    disc perimeter so the ribbon straddles the edge and merges into one solid
    piece (a tail emerging from the disc). Pitch is sized so the ribbon's OUTER
    EDGE — not its centerline — reaches top_outer_r, matching the top spiral's
    widest radius. Override with bottom_pitch_mm."""
    r0, pitch, turns = _part_polar_params("bottom", cfg)
    line = _spiral_centerline(r0=r0, pitch=pitch, turns=turns, seg_mm=cfg.seg_mm)
    ribbon = _ribbon(line, cfg.strip_w_mm, cfg.buffer_resolution)
    disc = Point(0.0, 0.0).buffer(cfg.base_r, quad_segs=cfg.buffer_resolution * 2)
    part = ribbon.union(disc)
    if not isinstance(part, Polygon):
        # Should not happen now the ribbon straddles the disc edge, but if a
        # custom pitch/width detaches them, keep the largest and warn loudly.
        import warnings

        warnings.warn(
            "bottom spiral split into disjoint pieces (base disc not connected "
            "to ribbon) — check strip_w / base_dia / bottom_pitch",
            stacklevel=2,
        )
        part = max(part.geoms, key=lambda g: g.area)
    return part


def place(poly: Polygon, margin: float) -> Polygon:
    """Translate so the part's lower-left bbox corner sits at (margin, margin),
    keeping every coordinate positive for the machine work area."""
    minx, miny, _, _ = poly.bounds
    return translate(poly, xoff=margin - minx, yoff=margin - miny)


def build_part(name: str, cfg: SpiralConfig) -> Polygon:
    """Build one placed part by name ('top' or 'bottom')."""
    if name == "top":
        poly = build_top_spiral(cfg)
    elif name == "bottom":
        poly = build_bottom_spiral(cfg)
    else:
        raise ValueError(f"unknown part: {name!r} (expected 'top' or 'bottom')")
    return place(poly, cfg.margin_mm)


# Assembly phase of each spiral (two-start helix: top offset 180 deg).
ASSEMBLY_PHASE_RAD = {"bottom": 0.0, "top": math.pi}


def crossing_rz(name: str, cfg: SpiralConfig, azimuth_rad: float):
    """Where spiral `name` crosses a radial plane at the given physical azimuth,
    in the assembled pot. Returns (r_center, z) for the ribbon centerline, or
    None if the (one-revolution) spiral does not reach that azimuth. Used to
    place capture slots in the ribs."""
    r0, pitch, turns = _part_polar_params(name, cfg)
    phase = ASSEMBLY_PHASE_RAD[name]
    span = turns * 2.0 * math.pi
    # winding angle theta such that theta + phase == azimuth (mod 2pi)
    theta = (azimuth_rad - phase) % (2.0 * math.pi)
    if theta > span + 1e-9:
        return None
    r = r0 + (pitch / (2.0 * math.pi)) * theta
    z = cfg.z_at_r(r)
    return (r, z)


# ---------------------------------------------------------------------------
# 3D assembled form (for visualization + future rib/joint design)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
#
# Model: the flat ribbon stays FLAT relative to the floor (horizontal). Lifting
# it into a coil keeps each point's (r, theta) and its horizontal orientation,
# and only adds height z(theta) = rise_per_rev * theta / 2pi. The ribbon is a
# gently climbing horizontal ramp, NOT a banked/twisted wall.
#
# The two spirals are interleaved (a two-start helix), BOTH occupying z in
# [0, rise] over one revolution -- they do not stack. The bottom winds OUT from
# the 2in base disc; the top winds IN from the rim. They cross radially over the
# turn and their ends interlock. Total height ~= rise_per_rev (one revolution).


def part_helix(name: str, cfg: SpiralConfig, phase_rad: float = 0.0) -> dict:
    """3D rails for one spiral in its ASSEMBLED position: a flat (horizontal)
    ribbon climbing rise_per_rev over one revolution. Returns dict of numpy
    (x,y,z) polylines 'center', 'inner', 'outer' (edges offset RADIALLY by
    +/- strip_w/2, same height), plus 'theta' and 'z_base'.

    phase_rad rotates the ribbon's AZIMUTH only (not its height/radius), so the
    two spirals can be interleaved as a two-start helix with their starts/ends
    offset (pass pi for a 180-degree offset)."""
    r0, pitch, turns = _part_polar_params(name, cfg)
    theta, r = _spiral_polar(r0, pitch, turns, cfg.seg_mm)
    z = cfg.z_at_r(r)  # height follows radius (outer=rim high, inner=base low)
    az = theta + phase_rad  # azimuth offset for two-start interleaving

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
