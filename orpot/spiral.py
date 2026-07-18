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
    top_pitch_mm: float = 15.0  # top ribbon radial advance per rev (inward)
    bottom_pitch_mm: float | None = None  # None => auto: reach r_max in `turns`
    seg_mm: float = 0.5  # spiral point spacing (curve smoothness)
    min_segment_mm: float = 0.3  # gcode decimation floor (see emit.py)
    margin_mm: float = 8.0  # inset from origin so coords stay positive
    buffer_resolution: int = 16  # shapely quad_segs (round-join segs per quarter)

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


def _spiral_centerline(
    r0: float, pitch: float, turns: float, seg_mm: float
) -> LineString:
    """Archimedean centerline r(theta) = r0 + (pitch / 2pi) * theta over
    theta in [0, turns * 2pi]. `pitch` is the signed radial advance per full
    revolution (negative spirals inward). Points are spaced ~seg_mm apart along
    the (approximate) arc length so the buffered ribbon stays smooth."""
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


def build_top_spiral(cfg: SpiralConfig) -> Polygon:
    """Rim ribbon: outer edge starts at top_outer_r and winds INWARD one turn.
    The centerline sits half a strip-width inside the outer edge and spirals
    in by top_pitch_mm per revolution."""
    r_start = cfg.top_outer_r - cfg.strip_w_mm / 2.0
    line = _spiral_centerline(
        r0=r_start,
        pitch=-abs(cfg.top_pitch_mm),  # inward
        turns=cfg.turns,
        seg_mm=cfg.seg_mm,
    )
    return _ribbon(line, cfg.strip_w_mm, cfg.buffer_resolution)


def build_bottom_spiral(cfg: SpiralConfig) -> Polygon:
    """Base disc (footprint) unioned with a ribbon spiralling OUTWARD one turn
    from the disc edge to the pot's max radius. The centerline starts ON the
    disc perimeter so the ribbon straddles the edge and merges into one solid
    piece (a tail emerging from the disc). Pitch is sized so the ribbon's OUTER
    EDGE — not its centerline — reaches top_outer_r, matching the top spiral's
    widest radius. Override with bottom_pitch_mm."""
    r_start = cfg.base_r  # centerline on the disc edge -> guaranteed solid union
    r_end = cfg.top_outer_r - cfg.strip_w_mm / 2.0  # outer edge lands on R_max
    span = r_end - r_start
    pitch = cfg.bottom_pitch_mm if cfg.bottom_pitch_mm is not None else span / cfg.turns
    line = _spiral_centerline(
        r0=r_start,
        pitch=abs(pitch),  # outward
        turns=cfg.turns,
        seg_mm=cfg.seg_mm,
    )
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
