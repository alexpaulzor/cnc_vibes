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
    """Geometry for the two interleaved spiral ramps + ribs.

    Default is a TIGHT PACK: the ramp radial span is derived from strip width so
    the two 180°-offset ramps sit edge-to-edge with no gap (pitch = 2*strip_w).
    Set tight_pack=False to size the rim from inner_dia_mm the legacy way."""

    strip_w_mm: float = 0.5 * MM_PER_INCH  # ramp/ribbon width = 12.7 (1/2 in)
    base_dia_mm: float = 2.0 * MM_PER_INCH  # center base disc Ø = 50.8
    turns: float = 1.0  # revolutions per spiral
    n_spirals: int = 2  # interleaved ramps (2 for now; 4 is a someday variant)
    tight_pack: bool = True  # derive span from strip_w so ramps touch (no gap)
    inner_dia_mm: float = 4.0 * MM_PER_INCH  # legacy rim opening Ø (tight_pack=False)
    top_pitch_mm: float | None = None  # per-rev advance override; None => span
    bottom_pitch_mm: float | None = None  # per-rev advance override; None => span
    top_ring_w_mm: float = 0.5 * MM_PER_INCH  # outer rim ring width = 12.7 (1/2 in)
    corner_fillet_mm: float = 3.0  # smooth concave corners where ramps meet anchors
    seg_mm: float = 0.5  # spiral point spacing (curve smoothness)
    min_segment_mm: float = 0.3  # gcode decimation floor (see emit.py)
    margin_mm: float = 8.0  # inset from origin so coords stay positive
    buffer_resolution: int = 16  # shapely quad_segs (round-join segs per quarter)
    rise_per_rev_mm: float = 40.0  # 3D lift per full revolution (flex limit)

    # --- vertical ribs (radial fins that capture both ramps) ---
    n_ribs: int = 4  # number of radial ribs
    rib_offset_deg: float = 0.0  # first rib azimuth; 0 puts ribs on the end seams
    material_th_mm: float = 3.0  # MDF thickness -> notch/slot width & rib thickness
    slot_fit_mm: float = 0.1  # added to slot size for a slip fit (kerf gives clearance)
    rib_band_mm: float = 0.5 * MM_PER_INCH  # rib width (excl. tabs) = 12.7 (1/2 in)
    rib_notch_depth_mm: float = 3.5  # open notch depth where a spiral rests (>= 3mm)
    rib_tab_span_mm: tuple = (12.0, 22.0)  # radial [inner, outer] of the base-disc tab
    rib_tab_depth_mm: float = (
        4.0  # how far the base tab drops below z=0 (into the disc)
    )
    rib_top_tab_w_mm: float = 10.0  # width of the top tab that enters the rim ring
    rib_top_tab_up_mm: float = 4.0  # how far the top tab rises through the rim ring
    free_end_slot_mm: float = (
        12.0  # radial length of the rib slot at a spiral's free end
    )
    end_slot_margin_mm: float = 4.0  # min material around the free-end slot (strength)
    disc_slot_len_mm: float = 10.0  # radial length of a rib mortise in the single disc

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
        """Outer centerline radius of the ramps. Tight pack: pitch = n_spirals *
        strip_w (adjacent turns touch), so the span over `turns` revs is
        n_spirals*strip_w*turns. Legacy: derive from the rim inner edge."""
        if self.tight_pack:
            return self.span_r_lo + self.n_spirals * self.strip_w_mm * self.turns
        return (self.inner_dia_mm / 2.0 + self.strip_w_mm) - self.top_ring_w_mm

    @property
    def ring_inner_r(self) -> float:
        """Rim ring inner edge = the ramps' outer edge (they blend)."""
        return self.span_r_hi + self.strip_w_mm / 2.0

    @property
    def top_outer_r(self) -> float:
        """Widest radius of the whole pot (rim ring outer edge)."""
        return self.ring_inner_r + self.top_ring_w_mm

    @property
    def top_inner_r(self) -> float:
        """Rim opening radius (informational)."""
        return self.top_outer_r - self.strip_w_mm

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
    the 3D helix are guaranteed to agree.

    Both spirals are the SAME ramp — they wind OUTWARD from r_lo to r_hi the same
    way — and in assembly the top is offset 180 deg (a true two-start helix that
    never self-intersects). They differ only in their anchor: the bottom carries
    the center base disc at its inner (start) end, the top carries the rim ring at
    its outer (end) end. r_lo = base_r; r_hi = the rim ring's inner edge. The
    optional *_pitch_mm overrides the per-rev advance."""
    r_lo = cfg.span_r_lo
    r_hi = cfg.span_r_hi
    span_pitch = (r_hi - r_lo) / cfg.turns  # radial advance per rev to span it once
    override = cfg.top_pitch_mm if name == "top" else cfg.bottom_pitch_mm
    pitch = abs(override) if override is not None else span_pitch  # outward (+)
    return r_lo, pitch, cfg.turns


def _fillet_concave(poly: Polygon, r: float) -> Polygon:
    """Round concave (reflex) corners with radius r via a morphological close
    (dilate then erode). Smooths the sharp corners where a ramp diverges from its
    anchor circle; leaves convex corners ~unchanged. Apply BEFORE cutting slots
    (a close would otherwise fill thin slots)."""
    if r <= 0:
        return poly
    out = poly.buffer(r, join_style="round").buffer(-r, join_style="round")
    if not isinstance(out, Polygon):
        out = max(out.geoms, key=lambda g: g.area)
    return out


def _spiral_terminus(cfg: SpiralConfig, name: str, which: str) -> tuple[float, float]:
    """(radius, azimuth_rad) of a spiral centerline terminus in the flat part.
    which: 'inner' (theta=0) or 'outer' (theta=turns*2pi)."""
    r0, pitch, turns = _part_polar_params(name, cfg)
    theta, r = _spiral_polar(r0, pitch, turns, cfg.seg_mm)
    i = 0 if which == "inner" else -1
    return float(r[i]), float(theta[i])


def _add_reinforced_end_slot(
    cfg: SpiralConfig, part: Polygon, name: str, which: str, direction: int
) -> Polygon:
    """Extend a ramp's free end into a rounded boss with >= end_slot_margin_mm of
    material around a rib slot, then cut the slot. The boss guarantees the free
    end is strong enough. direction: +1 extends the tip to larger radius (outer
    free end), -1 toward the center (inner free end)."""
    from shapely.affinity import rotate
    from shapely.geometry import LineString, box

    r_tip, az = _spiral_terminus(cfg, name, which)
    hw = (cfg.material_th_mm + cfg.slot_fit_mm) / 2.0  # half slot width (tangential)
    m = cfg.end_slot_margin_mm
    length = cfg.free_end_slot_mm
    r_new = r_tip + direction * (length + m)  # extend tip past the slot by margin
    # Rounded-cap boss along the radial axis (built on +x, then rotated to az).
    boss = LineString([(r_tip, 0.0), (r_new, 0.0)]).buffer(hw + m, cap_style="round")
    far = r_new - direction * m  # slot far end sits `m` inside the boss tip
    near = far - direction * length
    lo, hi = sorted((near, far))
    slot = box(lo, -hw, hi, hw)
    boss = rotate(boss, az, origin=(0, 0), use_radians=True)
    slot = rotate(slot, az, origin=(0, 0), use_radians=True)
    part = part.union(boss).difference(slot)
    if not isinstance(part, Polygon):
        part = max(part.geoms, key=lambda g: g.area)
    return part


def build_top_spiral(cfg: SpiralConfig) -> Polygon:
    """Top piece: a ribbon that winds OUTWARD one turn from near the center to a
    full outer RIM RING it blends into at the outer end. The ring is the top
    piece's anchor (mirror of the bottom's center base disc). Same ramp shape as
    the bottom; in assembly it's offset 180 deg. Its free (inner) end gets a
    reinforced rib slot; the divergence corners are filleted."""
    r0, pitch, turns = _part_polar_params("top", cfg)
    line = _spiral_centerline(r0=r0, pitch=pitch, turns=turns, seg_mm=cfg.seg_mm)
    ribbon = _ribbon(line, cfg.strip_w_mm, cfg.buffer_resolution)
    outer = Point(0.0, 0.0).buffer(cfg.top_outer_r, quad_segs=cfg.buffer_resolution * 2)
    # Pull the ring inner edge in ~1mm past the ramp's outer edge so the ribbon
    # overlaps the ring and merges into one solid piece (they'd only touch
    # tangentially otherwise, splitting the union).
    inner = Point(0.0, 0.0).buffer(
        cfg.ring_inner_r - 1.0, quad_segs=cfg.buffer_resolution * 2
    )
    part = ribbon.union(outer.difference(inner))
    if not isinstance(part, Polygon):
        part = max(part.geoms, key=lambda g: g.area)
    part = _fillet_concave(part, cfg.corner_fillet_mm)
    part = _add_reinforced_end_slot(cfg, part, "top", "inner", direction=-1)
    return part


def build_bottom_spiral(cfg: SpiralConfig) -> Polygon:
    """Bottom piece: a ribbon that winds OUTWARD one turn from the center base
    disc (its anchor) to the rim ring's inner edge. The centerline starts ON the
    disc perimeter so the ribbon merges into one solid piece. Its free (outer)
    end gets a reinforced rib slot; the divergence corners are filleted."""
    r0, pitch, turns = _part_polar_params("bottom", cfg)
    line = _spiral_centerline(r0=r0, pitch=pitch, turns=turns, seg_mm=cfg.seg_mm)
    ribbon = _ribbon(line, cfg.strip_w_mm, cfg.buffer_resolution)
    disc = Point(0.0, 0.0).buffer(cfg.base_r, quad_segs=cfg.buffer_resolution * 2)
    part = ribbon.union(disc)
    if not isinstance(part, Polygon):
        import warnings

        warnings.warn(
            "bottom spiral split into disjoint pieces (base disc not connected "
            "to ribbon) — check strip_w / base_dia / bottom_pitch",
            stacklevel=2,
        )
        part = max(part.geoms, key=lambda g: g.area)
    part = _fillet_concave(part, cfg.corner_fillet_mm)
    part = _add_reinforced_end_slot(cfg, part, "bottom", "outer", direction=1)
    return part


# ---------------------------------------------------------------------------
# Single-piece disc (the real fabrication model)
# ---------------------------------------------------------------------------
#
# The pot is ONE solid MDF disc with two interleaved thin spiral CUTS (open
# curves) that leave it connected: a solid central hub, a solid outer rim, and
# two spiral arms joining them. Lifting the hub relative to the rim expands the
# arms into a two-start helical wall. Separate ribs slot into holes cut in the
# arms to lock the expanded height. Ramp (arm) width = spacing between cuts.
#
# Phases: the two ARM centerlines sit at 0 and pi (reused by the ribs and the
# 3D view); the two CUTS sit halfway between them, at pi/2 and 3pi/2, so each arm
# is strip_w wide.


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
    - profile: the outer disc Polygon with rib-slot holes cut in the arms
    - cuts: the open spiral cut LineStrings
    Everything centered on the origin."""
    from ribs import disc_rib_slots  # local import avoids a cycle

    _, _, r_outer = disc_radii(cfg)
    profile = Point(0.0, 0.0).buffer(r_outer, quad_segs=cfg.buffer_resolution * 2)
    if cfg.n_ribs > 0:
        for slot in disc_rib_slots(cfg):
            profile = profile.difference(slot)
    if not isinstance(profile, Polygon):
        profile = max(profile.geoms, key=lambda g: g.area)
    return profile, build_cut_spirals(cfg)


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
