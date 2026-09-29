#!/usr/bin/env python3
"""Concentric-ring ETCH calibration -- a variant of spiral_cal.py's cut
calibration pattern, for a shallow non-cutting score pass instead of a
through-cut.

One plate maps two variables:

  * FEED  -- concentric ring PAIRS, inner=slow -> outer=fast (default up
             to F5000 -- not pushing the machine's rated max, just the
             practical ceiling once a heavier laser mount eats into it).
  * POWER -- each ring is split into equal-angle SECTORS, one per test
             power.

Each feed gets TWO nested rings, one power-sector apart in radius, so a
single photo straight down shows both at once:

  * the OUTER ring of the pair: ONE continuous laser-on path all the way
    around -- only its very first point (sector 0) is a genuine cold
    start: sector boundaries are just an S-value change while the diode
    stays lit, no re-ramp. Steady-state read.
  * the INNER ring of the pair: laser-off BETWEEN every sector, so each
    sector is its own fresh cold start at its own power -- worst case for
    the wiggle's cold-start artifact, one clean arc per cell instead of a
    scatter of ticks too small to read from a phone photo.

Both rings use the SAME warmup formula as emitter.emit_etch_gcode():
ramp_ms = WARMUP_MS * power_percent/100, lead_in_mm = ramp_ms/1000 *
feed/60. Comparing the two rings at the same radius band (same feed) is
the actual test: a good setting is one where the cold-start ring isn't
visibly worse than the continuous one.

Each sector's arc is not a single pass but a tight ZIGZAG of REPEAT COVERAGE
(see `_zigzag_arc_points`): the beam sweeps forward across the sector's full
angular span, then reverses and sweeps back, ~3 laps total (boustrophedon/
ping-pong), so it dwells several times longer in that one sector -- long
enough for the warmup and steady-state behavior to both be fully evident.
Each lap gets a tiny (cosmetic, ~0.25mm) radial nudge purely so the laps are
visually distinguishable as separate passes; it is NOT a deliberate radial
wobble, and it must not turn the sector's silhouette into a star -- a radial
oscillation shape was tried and rejected here because it has no angle/arc-
length to measure against the known feed rate, so timing can't be read off a
photo. Motion stays predominantly ANGULAR/diagonal: each sector should read
in a photo as one band traced several times, not a star. The zigzag rides on
top of each ring's existing cold-start wiggle (the continuous ring's one
real wiggle at its true start; the cold-start ring's fresh wiggle at the
head of every sector) -- it doesn't replace it.

The whole plate is centered on (0, 0) (`ellipse_pt` is polar about the
origin; the job opens and closes at G0 X0 Y0), and toolpath order is
strictly INSIDE-OUT: ring pairs are cut innermost-feed-first, each ring
pair's own feed label is engraved immediately alongside that same ring
pair (not in one upfront block), and only the shared sector/power rim
labels -- which sit at the outermost radius since they're common to every
ring -- are engraved in a final block after all ring pairs are done. This
means an aborted job (e.g. run off the edge of a scrap piece) still leaves
everything already cut as complete, valid, centered data. If the job is
stopped before that final rim-label block, the sector power values are
still knowable without the physical label: sectors are always in the same
fixed CCW order from the same starting angle on every ring, and each
sector's power (and each ring pair's feed) is also recorded directly in
the gcode's own comments.

Invoked standalone (see main() below). Outputs gcode + toolpath PNG to
build/etch_matrix_cal/.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DIR))
BUILD_DIR = DIR / "build" / "etch_matrix_cal"

from spiral_cal import (  # noqa: E402
    WARMUP_MS,
    _label_strokes,
    _order_strokes,
    _render_gcode_png,
    _warmup_points,
    ellipse_pt,
)

DEFAULT_FEEDS = [1250, 2000, 3200, 5000]  # ring pairs, inner -> outer, mm/min
DEFAULT_POWERS = [25, 50, 70, 100]  # sectors per ring, percent
ENGRAVE_POWER_PCT = 15.0
ENGRAVE_FEED = 3000
ZIGZAG_N_LAPS = 3  # forward/reverse sweeps of the sector's full angular span
ZIGZAG_LAP_STEP_MM = 0.25  # tiny cosmetic radial nudge between laps, NOT a wobble


def _zigzag_arc_points(r_center, th0, th1, n_laps, lap_step_mm, step_deg):
    """Dense point list covering the SAME sector (th0 to th1 around r_center)
    repeatedly: sweep forward th0->th1, reverse th1->th0, sweep forward again,
    etc., n_laps laps total (a boustrophedon/ping-pong repeat-coverage pass,
    not a single sweep) -- so the beam dwells several times longer in this one
    sector, long enough for warmup and steady-state to both show up in a
    photo. Motion stays predominantly ANGULAR (diagonal): each lap gets only a
    tiny, purely cosmetic radial nudge of lap_step_mm so successive laps are
    visually distinguishable, centered on r_center so the sector reads as one
    repeated band, NOT a star -- this is deliberately NOT a radial-oscillation
    shape (that has no angle/arc-length to measure timing against the known
    feed rate from a photo)."""
    span = th1 - th0
    n_steps = max(2, int(round(abs(span) / step_deg)))
    n_full_laps = max(1, int(math.floor(n_laps)))
    frac_last = n_laps - n_full_laps

    def lap_radius(lap_idx):
        return r_center + lap_step_mm * (lap_idx - (n_laps - 1) / 2.0)

    pts = [ellipse_pt(r_center, r_center, th0)]
    for lap_idx in range(n_full_laps):
        forward = lap_idx % 2 == 0
        r = lap_radius(lap_idx)
        for j in range(1, n_steps + 1):
            frac = j / n_steps
            th = th0 + span * (frac if forward else (1 - frac))
            pts.append(ellipse_pt(r, r, th))
    if frac_last > 1e-9:
        forward = n_full_laps % 2 == 0
        r = lap_radius(n_full_laps)
        n_partial = max(1, int(round(n_steps * frac_last)))
        for j in range(1, n_partial + 1):
            frac = frac_last * j / n_partial
            th = th0 + span * (frac if forward else (1 - frac))
            pts.append(ellipse_pt(r, r, th))
    return pts


def generate(
    feeds=DEFAULT_FEEDS,
    powers=DEFAULT_POWERS,
    min_r=13.0,
    max_r=36.5,
    ring_gap_mm=4.0,
    ring_step_deg=2.0,
    zigzag_n_laps=ZIGZAG_N_LAPS,
    zigzag_lap_step_mm=ZIGZAG_LAP_STEP_MM,
    engrave_power=ENGRAVE_POWER_PCT,
    engrave_feed=ENGRAVE_FEED,
):
    """Build the etch calibration gcode. Returns (lines, meta)."""
    n_rings, n_sectors = len(feeds), len(powers)
    centers = [
        min_r + (max_r - min_r) * i / max(1, n_rings - 1) for i in range(n_rings)
    ]
    sec = 360.0 / n_sectors
    p0 = powers[0]  # power the continuous ring itself starts cold on
    power_s = {power: int(round(power * 10)) for power in powers}
    eng_s = int(round(engrave_power * 10))
    eng_warm = engrave_feed * (WARMUP_MS / 60000.0)

    lines = [
        "; ETCH calibration -- concentric ring PAIRS (feed) x sectors (power)",
        f"; feeds (mm/min, inner->outer pair): {feeds}",
        f"; sectors (power %%, per ring, CCW from angle 0): {powers}",
        f"; warmup model: ramp_ms = WARMUP_MS({WARMUP_MS:.0f}) * power_pct/100 "
        "-- SAME formula as emitter.emit_etch_gcode(), not independently "
        "re-measured here; this plate is what re-measures it.",
        ";",
        "; Each feed gets a nested ring PAIR:",
        ";  - outer ring: ONE continuous laser-on path, only sector 0 is a",
        ";    genuine cold start (S changes at boundaries, diode stays lit) --",
        ";    steady-state read.",
        ";  - inner ring: laser OFF between every sector, so each sector is",
        ";    its own fresh cold start at its own power -- worst-case wiggle",
        ";    read, as one clean arc per cell (not a tick too small to photo).",
        "; Compare inner vs outer at the same radius band (= same feed): a",
        "; good setting is one where the cold-start ring isn't visibly worse.",
        ";",
        ";HEAD: laser",
        ";MATERIAL: plywood_baltic_birch_3mm (scrap)",
        ";LASER_MODE: static",
        "",
        "$32=1   ; GRBL laser mode",
        "G21     ; mm",
        "G90     ; absolute",
        "M5      ; laser off",
        "G0 X0 Y0",
        "",
    ]

    label_r = max_r + ring_gap_mm * 2.5
    label_th = -90.0  # feed labels sit at a fixed angle (south), tangentially

    # ---- ring pairs: STRICT INSIDE-OUT. Each ring pair's own feed label is
    # engraved immediately alongside that pair (not in one upfront block that
    # would reach straight to the outer radius) -- so an abort partway through
    # still leaves complete, valid, centered data behind it. Only the shared
    # sector/power rim labels go in a final block after every pair is cut;
    # see the note above that block for why an abort before it still leaves
    # the sector powers knowable. ----
    for i, (c, feed) in enumerate(zip(centers, feeds)):
        r_outer = c + ring_gap_mm / 2.0
        r_inner = c - ring_gap_mm / 2.0

        # -- this ring pair's own feed label, at its own radius band --
        x, y = ellipse_pt(c, c, label_th)
        feed_strokes = _label_strokes(str(feed), x, y, 1.6, angle_deg=label_th + 90.0)
        ordered = _order_strokes(feed_strokes)
        lines.append(f"; --- feed={feed}mm/min ring-pair label (r={c:.1f}mm) ---")
        pts = [(ordered[0][0], ordered[0][1])]
        for x1, y1, x2, y2 in ordered:
            if (x1, y1) != pts[-1]:
                pts.append((x1, y1))
            pts.append((x2, y2))
        lines.append(f"G0 X{pts[0][0]:.3f} Y{pts[0][1]:.3f}")
        lines.append(f"M3 S{eng_s}")
        lines.append(f"F{engrave_feed}")
        for x, y in _warmup_points(pts, eng_warm):
            lines.append(f"G1 X{x:.3f} Y{y:.3f}")
        for x, y in pts[1:]:
            lines.append(f"G1 X{x:.3f} Y{y:.3f}")
        lines.append("M5")
        lines.append("")

        # -- outer ring: ONE continuous path, wiggle only at the very start,
        # then each sector zigzags forward/reverse (repeat coverage) --
        ramp_ms0 = WARMUP_MS * (p0 / 100.0)
        lead_in_mm0 = ramp_ms0 / 1000.0 * feed / 60.0
        warmup_span_deg = min(
            360.0, 360.0 * lead_in_mm0 / max(1e-6, 2 * math.pi * r_outer)
        )
        n_warm_pts = max(4, int(round(warmup_span_deg / ring_step_deg)) + 1)
        warm_pts = [
            ellipse_pt(r_outer, r_outer, k * warmup_span_deg / max(1, n_warm_pts - 1))
            for k in range(n_warm_pts)
        ]
        lines.append(
            f"; --- feed={feed}mm/min outer ring r={r_outer:.1f}mm CONTINUOUS "
            f"(cold-start sector power={p0}%, wiggle {ramp_ms0:.0f}ms = "
            f"{lead_in_mm0:.2f}mm) ---"
        )
        lines.append(f"G0 X{warm_pts[0][0]:.3f} Y{warm_pts[0][1]:.3f}")
        lines.append(f"M3 S{power_s[p0]}")
        lines.append(f"F{feed}")
        for x, y in _warmup_points(warm_pts, lead_in_mm0):
            lines.append(f"G1 X{x:.3f} Y{y:.3f}")
        for k, power in enumerate(powers):
            th0, th1 = k * sec, (k + 1) * sec
            lines.append(f"; sector {k + 1}/{n_sectors} power={power}%")
            lines.append(f"M3 S{power_s[power]}")
            zpts = _zigzag_arc_points(
                r_outer, th0, th1, zigzag_n_laps, zigzag_lap_step_mm, ring_step_deg
            )
            for x, y in zpts[1:]:
                lines.append(f"G1 X{x:.3f} Y{y:.3f}")
        lines.append("M5")
        lines.append("")

        # -- inner ring: laser OFF between sectors, each its own cold start,
        # each sector zigzagging forward/reverse (repeat coverage) --
        lines.append(
            f"; --- feed={feed}mm/min inner ring r={r_inner:.1f}mm COLD-START "
            "PER SECTOR ---"
        )
        for k, power in enumerate(powers):
            th0, th1 = k * sec, (k + 1) * sec
            pts = _zigzag_arc_points(
                r_inner, th0, th1, zigzag_n_laps, zigzag_lap_step_mm, ring_step_deg
            )
            ramp_ms = WARMUP_MS * (power / 100.0)
            lead_in_mm = ramp_ms / 1000.0 * feed / 60.0
            lines.append(
                f"; sector {k + 1}/{n_sectors} power={power}% wiggle "
                f"{ramp_ms:.0f}ms = {lead_in_mm:.2f}mm"
            )
            lines.append(f"G0 X{pts[0][0]:.3f} Y{pts[0][1]:.3f}")
            lines.append(f"M3 S{power_s[power]}")
            lines.append(f"F{feed}")
            for x, y in _warmup_points(pts, lead_in_mm):
                lines.append(f"G1 X{x:.3f} Y{y:.3f}")
            for x, y in pts[1:]:
                lines.append(f"G1 X{x:.3f} Y{y:.3f}")
            lines.append("M5")
        lines.append("")

    # ---- shared sector/power rim labels: LAST, at the outermost radius,
    # after every ring pair is already cut, to keep the whole toolpath
    # strictly inside-out. If the job is aborted before this block, each
    # sector's power is still knowable without it: sectors are always in
    # the same fixed CCW order from the same starting angle on every ring,
    # and every sector's power (and every ring pair's feed) is also right
    # there in the gcode's own comments above, regardless. ----
    label_strokes = []
    for k, power in enumerate(powers):
        th = (k + 0.5) * sec
        x, y = ellipse_pt(label_r, label_r, th)
        tang = th + 90.0
        if math.cos(math.radians(tang)) < 0:
            tang += 180
        label_strokes += _label_strokes(f"{power}", x, y, 2.5, angle_deg=tang)
    if label_strokes:
        ordered = _order_strokes(label_strokes)
        lines.append("; --- labels: power (rim, shared across all rings) ---")
        pts = [(ordered[0][0], ordered[0][1])]
        for x1, y1, x2, y2 in ordered:
            if (x1, y1) != pts[-1]:
                pts.append((x1, y1))
            pts.append((x2, y2))
        lines.append(f"G0 X{pts[0][0]:.3f} Y{pts[0][1]:.3f}")
        lines.append(f"M3 S{eng_s}")
        lines.append(f"F{engrave_feed}")
        for x, y in _warmup_points(pts, eng_warm):
            lines.append(f"G1 X{x:.3f} Y{y:.3f}")
        for x, y in pts[1:]:
            lines.append(f"G1 X{x:.3f} Y{y:.3f}")
        lines.append("M5")
        lines.append("")

    lines += ["G0 X0 Y0", ""]
    meta = {"feeds": feeds, "powers": powers, "centers": centers}
    return lines, meta


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="etch_matrix_cal.py",
        description="Concentric ring-pair feed x power ETCH calibration "
        "(continuous vs cold-start-per-sector).",
    )
    p.add_argument(
        "--feeds",
        type=lambda s: [int(x) for x in s.split(",")],
        default=DEFAULT_FEEDS,
        help=f"comma-separated feeds mm/min, inner->outer ring pairs "
        f"(default {DEFAULT_FEEDS})",
    )
    p.add_argument(
        "--powers",
        type=lambda s: [float(x) for x in s.split(",")],
        default=DEFAULT_POWERS,
        help=f"comma-separated power percents, per-ring sectors "
        f"(default {DEFAULT_POWERS})",
    )
    p.add_argument("--min-r", dest="min_r", type=float, default=13.0)
    p.add_argument("--max-r", dest="max_r", type=float, default=36.5)
    p.add_argument("--ring-gap", dest="ring_gap", type=float, default=4.0)
    args = p.parse_args(argv)

    lines, meta = generate(
        feeds=args.feeds,
        powers=args.powers,
        min_r=args.min_r,
        max_r=args.max_r,
        ring_gap_mm=args.ring_gap,
    )
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    out = BUILD_DIR / "etch_matrix_cal.gcode"
    out.write_text("\n".join(lines))
    png_path = _render_gcode_png(lines, BUILD_DIR / "etch_matrix_cal.png")
    print(f"feeds (ring pairs): {meta['feeds']}")
    print(f"powers (sectors): {meta['powers']}")
    print(f"ring-pair center radii mm: {[round(r, 1) for r in meta['centers']]}")
    print(f"-> {out}")
    print(f"-> {png_path}  (toolpath preview)")
    print(
        "read: for each feed (ring pair), compare the outer ring (continuous, "
        "steady-state) against the inner ring (cold-start every sector) at "
        "the same power. Pick the feed/power where the cold-start ring isn't "
        "visibly worse than the continuous one."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
