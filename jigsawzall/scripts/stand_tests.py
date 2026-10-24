#!/usr/bin/env python3
"""Test cuts for the open unknowns behind the display stand and MDF batch
cutting (RING_SPEC.md §13.18). Each writes one small G-code file; all stay
inside 5..200mm, M3 static, one $32=1, warmup wiggle per path like every
other cut here.

  fit_comb.gcode      3mm veneer. Two combs:
                      top strip = FRAME NOTCH widths 2.9..3.5mm (12mm deep):
                        slide a cut foot (or any 3mm veneer offcut edge) in --
                        pick the narrowest that seats without forcing;
                      bottom strip = MDF CHANNEL widths 3.0..3.6mm (15mm deep):
                        slide your 3mm MDF in -- pick the narrowest that drops in.
                      Widths run left -> right; the chamfered corner is the
                      narrow end. Feeds those numbers back as
                      stand_feet_sheet.py --notch-w / --mdf-clear.
  mdf_grid.gcode      3mm MDF. 12mm squares, rows = feed 200/300/400/500/600/800
                      mm/min (bottom -> top), columns = passes 1/2/3/4/6/8
                      (left -> right), 100% M3; chamfered corner = row 200,
                      column 1. A square that DROPS OUT cut through. Run it on
                      1 sheet, then on a stack of 2 (and 3) taped together --
                      check which squares fall out of the BOTTOM sheet -- and
                      repeat with each acrylic focus block, to find the cutting
                      focus and how deep the beam still cuts.
  focus_ramp.gcode    3mm MDF scrap >= 200 x 60mm, tilted: the far end (x=190)
                      raised by a 10mm block, near end flat. Three 180mm lines
                      along X at F300 / F500 / F800 x1 pass, plus one low-power
                      mark line (S200 F3000). Along each line the focal offset
                      goes 0 -> 10mm: where the mark line is thinnest = focus;
                      how far along the F300 line still cuts through = the
                      usable depth of the beam (the "10mm" claim, measured).

    PYTHONPATH=. python3 scripts/stand_tests.py OUTDIR
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "quickcut"))

from shapely.geometry import Polygon, box  # noqa: E402


def _emit(title, material_id, jobs, extra=()):
    """jobs: [(points_mm, feed, power_pct, passes)] -> G-code."""
    from emitter import WARMUP_MS, _header
    from motion import decimate, warmup_wiggle

    lines = _header(title=title, material_id=material_id, extra=list(extra))
    total_s = 0.0
    for i, (pts, feed, pct, passes) in enumerate(jobs, 1):
        pts = decimate(pts, 0.3)
        lead = WARMUP_MS * pct / 100 / 1000 * feed / 60
        warm = warmup_wiggle(pts, lead)
        lines += [f"; --- path {i}/{len(jobs)}: F{feed} {pct:.0f}% x{passes} ---",
                  f"G0 X{pts[0][0]:.3f} Y{pts[0][1]:.3f}", f"M3 S{int(round(pct * 10))}", f"F{feed}"]
        seq = list(pts)
        moves = list(warm)
        for _p in range(passes):
            moves += seq[1:]
            seq = seq[::-1]
        lines += [f"G1 X{x:.3f} Y{y:.3f}" for x, y in moves]
        lines += ["M5", ""]
        L = sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
        total_s += (L * passes + lead) / (feed / 60)
    lines += ["G0 X0 Y0", ""]
    return "\n".join(lines), total_s


def _chamfered(x0, y0, x1, y1, c=3.0):
    return Polygon([(x0 + c, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0 + c)])


def fit_comb():
    notch = [2.9 + 0.1 * k for k in range(7)]
    chan = [3.0 + 0.1 * k for k in range(7)]
    jobs = []
    for row, (widths, depth, y0) in enumerate(((notch, 12.0, 45.0), (chan, 15.0, 10.0))):
        strip = _chamfered(10.0, y0, 10.0 + 14.0 * len(widths) + 8.0, y0 + depth + 10.0)
        for k, w in enumerate(widths):
            cx = 10.0 + 8.0 + 14.0 * k + 3.0
            strip = strip.difference(box(cx - w / 2, y0 - 1.0, cx + w / 2, y0 + depth))
        jobs.append((list(strip.exterior.coords), 350, 100.0, 1))
    return _emit("fit comb: frame-notch widths (top) + MDF-channel widths (bottom)",
                 "plywood_veneer_3ply_3mm", jobs,
                 ["top strip: notch widths " + " ".join(f"{w:.1f}" for w in notch)
                  + " (12mm deep), chamfer = narrow end",
                  "bottom strip: MDF channel widths " + " ".join(f"{w:.1f}" for w in chan)
                  + " (15mm deep), chamfer = narrow end",
                  "toolpath widths: the kerf (~0.15mm) adds to every one"])


def mdf_grid():
    feeds = [200, 300, 400, 500, 600, 800]
    passes = [1, 2, 3, 4, 6, 8]
    jobs = []
    for r, f in enumerate(feeds):
        for c, p in enumerate(passes):
            x0, y0 = 15.0 + 18.0 * c, 15.0 + 18.0 * r
            sq = (_chamfered(x0, y0, x0 + 12, y0 + 12) if (r, c) == (0, 0)
                  else box(x0, y0, x0 + 12, y0 + 12))
            jobs.append((list(sq.exterior.coords), f, 100.0, p))
    return _emit("MDF cut-through grid: feed (rows) x passes (columns)", "mdf_3mm", jobs,
                 ["rows bottom->top: F" + "/".join(map(str, feeds)),
                  "columns left->right: passes " + "/".join(map(str, passes)),
                  "chamfered square = F200 x1; a square that drops out cut through",
                  "run on 1 sheet, then a taped stack of 2 (and 3); repeat per focus block"])


def focus_ramp():
    jobs = []
    for y, f, pct in ((15.0, 300, 100.0), (30.0, 500, 100.0), (45.0, 800, 100.0), (60.0, 3000, 20.0)):
        jobs.append(([(10.0, y), (190.0, y)], f, pct, 1))
    return _emit("focus ramp: tilt the scrap, far end (x=190) up 10mm", "mdf_3mm", jobs,
                 ["lines along X at y=15 F300, y=30 F500, y=45 F800 (100%), y=60 F3000 20% mark",
                  "focal offset runs 0 -> 10mm along each line: thinnest mark = focus,",
                  "how far the F300 line still cuts through = usable beam depth"])


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "stand_tests")
    out.mkdir(parents=True, exist_ok=True)
    import re

    for name, fn in (("fit_comb", fit_comb), ("mdf_grid", mdf_grid), ("focus_ramp", focus_ramp)):
        g, t = fn()
        xs = [float(v) for v in re.findall(r"^G[01] .*?X(-?[\d.]+)", g, re.M)]
        ys = [float(v) for v in re.findall(r"^G[01] .*?Y(-?[\d.]+)", g, re.M)]
        (out / f"{name}.gcode").write_text(g)
        print(f"{name}.gcode: ~{t / 60:.1f} min of laser time, X {min(xs):.1f}..{max(xs):.1f} "
              f"Y {min(ys):.1f}..{max(ys):.1f}")


if __name__ == "__main__":
    main()
