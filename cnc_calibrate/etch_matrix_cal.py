#!/usr/bin/env python3
"""Concentric-ring ETCH calibration -- a variant of spiral_cal.py's cut
calibration pattern, for a shallow non-cutting score pass instead of a
through-cut.

One plate maps two variables:

  * FEED  -- concentric rings, inner=slow -> outer=fast (up to the
             machine's rated max, e.g. F6000).
  * POWER -- each ring is split into equal-angle SECTORS, one per test
             power. A ring is cut as ONE continuous laser-on path (the
             diode is already lasing at a sector boundary, so an S-value
             change there does NOT re-trigger a cold-start ramp -- only
             the ring's own first point, where the laser turns on from
             cold, gets a warmup wiggle).

Each (ring, sector) = (feed, power) cell gets TWO comparison marks, both
using the SAME warmup model as emitter.emit_etch_gcode() (ramp_ms =
WARMUP_MS * power_percent/100, lead_in_mm = ramp_ms/1000 * feed/60):

  1. The ring's own arc through that sector -- a LONG continuous stroke,
     wiggled once at the ring's start (far from most sectors), so most of
     a sector's arc is read at steady-state power with no double-dose.
  2. A cluster of short radial tick marks just outside the ring, in that
     sector's angular band -- SHORT disconnected strokes, each with its
     OWN individual wiggle, matching how real etch strokes (e.g. globe
     coastline detail) get wiggled once per stroke. This is where the
     "first few mm get ~2x dose from the wiggle's forward+back overlap"
     effect shows up hardest, since the overlap is a much bigger fraction
     of a short stroke's total length.

Comparing mark 1 vs mark 2 at the same cell is the actual test: a good
power/feed setting is one where neither is badly off from the other --
long lines readable, short ticks not blown out.

Tick clusters are cut in FARTHEST-FIRST order (greedy nearest-remaining-
is-excluded, next-cut is the remaining cell farthest from the one just
cut) so consecutive laser-off/laser-on events are never physically
adjacent -- avoids one swatch's residual heat biasing its neighbor's
read, per the "start the next cut a few cm away" concern.

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

DEFAULT_FEEDS = [1500, 2500, 4000, 6000]  # rings, inner -> outer, mm/min
DEFAULT_POWERS = [25, 50, 70, 100]  # sectors per ring, percent
ENGRAVE_POWER_PCT = 15.0
ENGRAVE_FEED = 3000


def _farthest_order(cells, center_of):
    """Greedy farthest-neighbor traversal: start at cells[0], then repeatedly
    jump to whichever remaining cell is farthest from the one just visited.
    Not a globally optimal max-separation tour, just a cheap way to keep
    consecutive laser-off/on events from landing next to each other."""
    remaining = list(cells)
    order = [remaining.pop(0)]
    while remaining:
        last = center_of(order[-1])
        best_i, best_d = 0, -1.0
        for i, c in enumerate(remaining):
            p = center_of(c)
            d = (p[0] - last[0]) ** 2 + (p[1] - last[1]) ** 2
            if d > best_d:
                best_d, best_i = d, i
        order.append(remaining.pop(best_i))
    return order


def generate(
    feeds=DEFAULT_FEEDS,
    powers=DEFAULT_POWERS,
    min_r=15.0,
    max_r=45.0,
    tick_len_mm=4.0,
    ticks_per_sector=3,
    ring_step_deg=2.0,
    engrave_power=ENGRAVE_POWER_PCT,
    engrave_feed=ENGRAVE_FEED,
):
    """Build the etch calibration gcode. Returns (lines, meta)."""
    n_rings, n_sectors = len(feeds), len(powers)
    radii = [
        min_r + (max_r - min_r) * i / max(1, n_rings - 1) for i in range(n_rings)
    ]
    sec = 360.0 / n_sectors
    p0 = powers[0]  # power the ring itself starts cold on (sector 0)

    lines = [
        "; ETCH calibration -- concentric rings (feed) x sectors (power)",
        f"; rings (feed mm/min, inner->outer): {feeds}",
        f"; sectors (power %%, per ring, CCW from angle 0): {powers}",
        f"; warmup model: ramp_ms = WARMUP_MS({WARMUP_MS:.0f}) * power_pct/100 "
        "-- SAME formula as emitter.emit_etch_gcode(), not independently "
        "re-measured here; this plate is what re-measures it.",
        ";",
        "; Each (ring, sector) cell has TWO marks to compare:",
        ";  - the ring's own arc through that sector: a LONG stroke, wiggled",
        ";    only once (at the ring's start, sector 0) -- steady-state read.",
        ";  - a cluster of short radial ticks just outside the ring in that",
        ";    sector's band: SHORT strokes, each wiggled individually --",
        ";    worst-case read for the wiggle-overlap double-dose effect.",
        "; A good setting is one where the long arc and the short ticks in",
        "; the same cell don't look very different from each other.",
        ";",
        "; Short-tick clusters are cut in farthest-first order -- consecutive",
        "; laser-off/on events land far apart on the plate, so residual heat",
        "; from one cluster doesn't bias its neighbor's read.",
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

    # ---- engraved labels: feed per ring, power per sector ----
    # Ring labels sit AT each ring's own radius, tangentially oriented (running
    # along the circumference, not radially outward) so a thin annulus band
    # holds one ring's number without smearing into its neighbors. Fixed angle
    # -90 (south) keeps them clear of the sector-0/wiggle region at angle 0.
    label_strokes = []
    ring_label_th = -90.0
    ring_tang = ring_label_th + 90.0
    for i, (r, feed) in enumerate(zip(radii, feeds)):
        x, y = ellipse_pt(r, r, ring_label_th)
        label_strokes += _label_strokes(str(feed), x, y, 2.0, angle_deg=ring_tang)
    label_r = max_r + tick_len_mm * (ticks_per_sector + 2.5)
    for k, power in enumerate(powers):
        th = (k + 0.5) * sec
        x, y = ellipse_pt(label_r, label_r, th)
        tang = th + 90.0
        if math.cos(math.radians(tang)) < 0:
            tang += 180
        label_strokes += _label_strokes(f"{power}", x, y, 3.0, angle_deg=tang)
    if label_strokes:
        ordered = _order_strokes(label_strokes)
        lines.append("; --- labels: ring=feed (near angle 0), sector=power (rim) ---")
        pts = [(ordered[0][0], ordered[0][1])]
        for x1, y1, x2, y2 in ordered:
            if (x1, y1) != pts[-1]:
                pts.append((x1, y1))
            pts.append((x2, y2))
        eng_s = int(round(engrave_power * 10))
        eng_warm = engrave_feed * (WARMUP_MS / 60000.0)
        lines.append(f"G0 X{pts[0][0]:.3f} Y{pts[0][1]:.3f}")
        lines.append(f"M3 S{eng_s}")
        lines.append(f"F{engrave_feed}")
        for x, y in _warmup_points(pts, eng_warm):
            lines.append(f"G1 X{x:.3f} Y{y:.3f}")
        for x, y in pts[1:]:
            lines.append(f"G1 X{x:.3f} Y{y:.3f}")
        lines.append("M5")
        lines.append("")

    # ---- rings: one continuous laser-on path per ring, S changes at sector
    # boundaries (no re-ramp -- diode stays lit), wiggle only at the very start ----
    for i, (r, feed) in enumerate(zip(radii, feeds)):
        ramp_ms = WARMUP_MS * (p0 / 100.0)
        lead_in_mm = ramp_ms / 1000.0 * feed / 60.0
        # Sample only the arc the warmup wiggle actually needs (it walks
        # forward from angle 0 until it's covered lead_in_mm, then reverses)
        # -- a full-circle point list here just wastes points on comparisons
        # the wiggle never reaches.
        warmup_span_deg = min(360.0, 360.0 * lead_in_mm / max(1e-6, 2 * math.pi * r))
        n_warm_pts = max(4, int(round(warmup_span_deg / ring_step_deg)) + 1)
        warm_pts = [
            ellipse_pt(r, r, k * warmup_span_deg / max(1, n_warm_pts - 1))
            for k in range(n_warm_pts)
        ]
        lines.append(
            f"; --- ring #{i + 1} feed={feed}mm/min r={r:.1f}mm "
            f"(cold-start sector power={p0}%, wiggle {ramp_ms:.0f}ms = "
            f"{lead_in_mm:.2f}mm) ---"
        )
        lines.append(f"G0 X{warm_pts[0][0]:.3f} Y{warm_pts[0][1]:.3f}")
        lines.append(f"M3 S{int(round(p0 * 10))}")
        lines.append(f"F{feed}")
        for x, y in _warmup_points(warm_pts, lead_in_mm):
            lines.append(f"G1 X{x:.3f} Y{y:.3f}")
        for k, power in enumerate(powers):
            th0, th1 = k * sec, (k + 1) * sec
            n_steps = max(1, int(round((th1 - th0) / ring_step_deg)))
            lines.append(f"; sector {k + 1}/{n_sectors} power={power}%")
            lines.append(f"M3 S{int(round(power * 10))}")
            for j in range(1, n_steps + 1):
                th = th0 + (th1 - th0) * j / n_steps
                x, y = ellipse_pt(r, r, th)
                lines.append(f"G1 X{x:.3f} Y{y:.3f}")
        lines.append("M5")
        lines.append("")

    # ---- short-tick clusters (the short-segment worst case), farthest-first ----
    cells = [(i, k) for i in range(n_rings) for k in range(n_sectors)]

    def cell_center(c):
        i, k = c
        r = radii[i] + tick_len_mm * (ticks_per_sector / 2.0 + 1)
        th = (k + 0.5) * sec
        return ellipse_pt(r, r, th)

    order = _farthest_order(cells, cell_center)
    lines.append(
        f"; --- {len(cells)} short-tick clusters (feed x power), farthest-first ---"
    )
    for i, k in order:
        r, feed, power = radii[i], feeds[i], powers[k]
        ramp_ms = WARMUP_MS * (power / 100.0)
        lead_in_mm = ramp_ms / 1000.0 * feed / 60.0
        power_s = int(round(power * 10))
        lines.append(
            f"; cluster ring#{i + 1} sector#{k + 1} feed={feed} power={power}% "
            f"wiggle {ramp_ms:.0f}ms = {lead_in_mm:.2f}mm"
        )
        for t in range(ticks_per_sector):
            th = k * sec + (t + 0.5) / ticks_per_sector * sec
            r0 = r + 1.0 + t * tick_len_mm * 1.3
            r1 = r0 + tick_len_mm
            p0pt = ellipse_pt(r0, r0, th)
            p1pt = ellipse_pt(r1, r1, th)
            pts = [p0pt, p1pt]
            lines.append(f"G0 X{p0pt[0]:.3f} Y{p0pt[1]:.3f}")
            lines.append(f"M3 S{power_s}")
            lines.append(f"F{feed}")
            for x, y in _warmup_points(pts, lead_in_mm):
                lines.append(f"G1 X{x:.3f} Y{y:.3f}")
            lines.append(f"G1 X{p1pt[0]:.3f} Y{p1pt[1]:.3f}")
            lines.append("M5")

    lines += ["", "G0 X0 Y0", ""]
    meta = {
        "feeds": feeds,
        "powers": powers,
        "radii": radii,
        "n_ticks": len(cells) * ticks_per_sector,
    }
    return lines, meta


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="etch_matrix_cal.py",
        description="Concentric-ring feed x power ETCH calibration.",
    )
    p.add_argument(
        "--feeds",
        type=lambda s: [int(x) for x in s.split(",")],
        default=DEFAULT_FEEDS,
        help=f"comma-separated feeds mm/min, inner->outer rings "
        f"(default {DEFAULT_FEEDS})",
    )
    p.add_argument(
        "--powers",
        type=lambda s: [float(x) for x in s.split(",")],
        default=DEFAULT_POWERS,
        help=f"comma-separated power percents, per-ring sectors "
        f"(default {DEFAULT_POWERS})",
    )
    p.add_argument("--min-r", dest="min_r", type=float, default=15.0)
    p.add_argument("--max-r", dest="max_r", type=float, default=45.0)
    p.add_argument("--tick-len", dest="tick_len", type=float, default=4.0)
    p.add_argument("--ticks-per-sector", dest="tps", type=int, default=3)
    args = p.parse_args(argv)

    lines, meta = generate(
        feeds=args.feeds,
        powers=args.powers,
        min_r=args.min_r,
        max_r=args.max_r,
        tick_len_mm=args.tick_len,
        ticks_per_sector=args.tps,
    )
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    out = BUILD_DIR / "etch_matrix_cal.gcode"
    out.write_text("\n".join(lines))
    png_path = _render_gcode_png(lines, BUILD_DIR / "etch_matrix_cal.png")
    print(f"feeds (rings): {meta['feeds']}")
    print(f"powers (sectors): {meta['powers']}")
    print(f"radii mm: {[round(r, 1) for r in meta['radii']]}")
    print(f"{meta['n_ticks']} short ticks, farthest-first ordered")
    print(f"-> {out}")
    print(f"-> {png_path}  (toolpath preview)")
    print(
        "read: for each (ring=feed, sector=power) cell, compare the ring's "
        "own arc (long-line read) against its tick cluster (short-segment "
        "read, worst case for wiggle-overlap darkening). Pick the cell "
        "where they're closest to each other AND both look right."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
