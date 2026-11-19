#!/usr/bin/env python3
"""Aluminium calibration for the trim router: feed (slots) and size (a part cut the real way).

  --feeds : a row of 20 mm slots, each one chipload step faster, all 3 x step_down deep, ramped in (no plunge).
            Slot k has k dimples to its left (1 = slowest). Pick the fastest slot with clean, curled chips, no squeal
            and no aluminium welded on the cutter; measure its width (= the cutter's real slotting width).
  --size  : a 20 x 20 square with a 5.3 hole in its middle, cut exactly as the pallets will be: file 1 mills the hole,
            you screw the square down through it, file 2 cuts the square free (widened roughing + finish). Measure:
            square vs 20.00 and hole vs 5.30 -> the cutter's effective diameter (deflection included).

Z0 = top of the plate, X0 Y0 = its front-left corner (probe-corner). Set the router DIAL to --rpm.
    python lessons/mill/03_aluminum/alu_cal.py --feeds                   # slots at x 20.., y 150..
    python lessons/mill/03_aluminum/alu_cal.py --size --chipload 0.02    # square at x 75..95, y 150..170
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts"))
sys.path.insert(0, str(HERE))
from shapely.geometry import Point, box  # noqa: E402

import alu_ops as A  # noqa: E402
from cam import load_material, load_tool  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--feeds", action="store_true")
    ap.add_argument("--size", action="store_true")
    ap.add_argument("--tool", default="flat_3.175mm_1flute_alu")
    ap.add_argument("--material", default="alu6061_6.35")
    ap.add_argument("--rpm", type=int, default=24000, help="the router dial's setting (6 = 24000)")
    ap.add_argument("--chiploads", type=float, nargs="+", default=[0.010, 0.015, 0.020, 0.025, 0.030])
    ap.add_argument("--chipload", type=float, help="--size: the chipload picked from --feeds (default: the material's)")
    ap.add_argument("--x0", type=float, help="front-left of the test (default: feeds 20, size 75: side by side)")
    ap.add_argument("--y0", type=float, default=150.0)
    ap.add_argument("-o", "--out", type=Path, default=HERE / "build")
    a = ap.parse_args(argv)
    tool, mat = load_tool(a.tool), load_material(a.material)
    sd = round(mat.doc_fraction * tool.diameter_mm, 3)
    cl = a.chipload or mat.chipload_for(tool.id)
    job = A.AluJob(tool, a.rpm, cl, mat.thickness_mm, sd)
    out = []
    x_feeds = a.x0 if a.x0 is not None else 20.0
    x_size = a.x0 if a.x0 is not None else 75.0
    if a.feeds:
        body, depth, pitch = [], 3 * sd, 8.0
        rows = []
        for k, c in enumerate(a.chiploads, 1):
            y = a.y0 + (k - 1) * pitch
            f = int(round(c * tool.flutes * a.rpm))
            body += ["", f"; slot {k}: chipload {c:g} -> F{f}"] + A.dimples(job, [(x_feeds + 2.0 * i, y) for i in range(k)])
            body += A.ramp_slot(job, (x_feeds + 13, y), (x_feeds + 33, y), depth, f)
            rows.append(f"slot {k} ({k} dimples): chipload {c:g} -> F{f}")
        lines = A.program(job, f"alu feed test: {len(rows)} slots, {depth:.3f} deep in {sd:g} ramps", rows, body)
        out.append(A.write(a.out, "alu_cal_feeds", mat.id, lines))
    if a.size:
        sq = box(x_size, a.y0, x_size + 20, a.y0 + 20).difference(Point(x_size + 10, a.y0 + 10).buffer(2.65, 64))
        progs = A.holes_then_outline(job, "alu size test: 20 x 20 square, 5.3 hole", [("square", sq)],
                                     ["measure: square vs 20.00, hole vs 5.30"])
        for k, lines in progs.items():
            out.append(A.write(a.out, f"alu_cal_size_{k}", mat.id, lines))
    for p in out:
        print(p)
    return out


if __name__ == "__main__":
    main()
