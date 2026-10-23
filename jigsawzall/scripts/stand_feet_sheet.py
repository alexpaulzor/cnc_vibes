#!/usr/bin/env python3
"""Display-stand feet for 290mm ring puzzles, nested many-up on their OWN
300mm sheet (RING_SPEC.md §13.17). Replaces the corner-offcut feet of
§13.15 (display_stand.py) when a third sheet is fine: small feet, many
stands per sheet.

The tray (leftover 300 square frame + 3mm MDF back, puzzle in it) stands
on its straight bottom edge in a slot in each foot, leaning back at the
display angle (degrees from HORIZONTAL; Alex's tilt test: the puzzle stays
in the frame to at least 88). Foot profile, side view (front at left):

        lip  slot  back rest
         _   / /\\
        | | / /   \\___________
        |_|/_/_______________|   <- table
         ^ front              ^ rear contact

The slot's back wall carries the tray's back face up to the back rest's
top; the lip stops it sliding forward. The foot reaches back behind the
tray's centre of mass by `margin_mm` -- the steeper the angle, the less
reach (the COM sits 150*cos(angle) behind the slot), so the smaller the
foot. Two feet per stand hold it; three or four steady a big name ring.

    PYTHONPATH=. python3 scripts/stand_feet_sheet.py OUTDIR [--angle 75]
"""
import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "quickcut"))

from shapely import affinity  # noqa: E402
from shapely.geometry import Polygon, box  # noqa: E402

import display_stand as DS  # noqa: E402

TRAY_MM = 300.0


def foot(angle_deg=75.0, tray_t_mm=6.0, slot_clear_mm=0.2, base_mm=6.0, lip_mm=6.0,
         lip_h_mm=8.0, rest_mm=25.0, margin_mm=25.0, tail_h_mm=6.0):
    """Foot polygon (x back, y up, front-bottom corner at the origin) and
    the info dict display_stand.stability() reads. base_mm: wood under the
    slot; lip_mm: lip thickness; lip_h_mm: how far up the tray's front face
    the lip reaches (along the tray); rest_mm: back-wall contact length up
    the tray; margin_mm: rear ground contact behind the tray's COM."""
    th = math.radians(angle_deg)
    u = (math.cos(th), math.sin(th))
    nf = (-math.sin(th), math.cos(th))  # tray normal, toward the front
    w = tray_t_mm + slot_clear_mm
    # slot bottom: front corner F0 is lip_mm behind the front face
    F0 = (lip_mm + 0.0, base_mm)
    B0 = (F0[0] - w * nf[0], F0[1] - w * nf[1])  # back corner, same height-ish
    if B0[1] < base_mm:  # keep base_mm of wood under the lower corner
        dy = base_mm - B0[1]
        F0, B0 = (F0[0], F0[1] + dy), (B0[0], B0[1] + dy)
    F1 = (F0[0] + lip_h_mm * u[0], F0[1] + lip_h_mm * u[1])  # lip top
    A = (B0[0] + rest_mm * u[0], B0[1] + rest_mm * u[1])  # back-rest top
    info = dict(B0=B0, F0=F0, u=u, nf=nf, w=w, angle_deg=angle_deg, tray_t_mm=tray_t_mm,
                y_ground=0.0, R_o=0.0, H=A[1])
    com, _xf, _xb, _m = DS.stability(Polygon([(0, 0), (1, 0), (0, 1)]), info, tray_mm=TRAY_MM)
    x_back = com[0] + margin_mm
    pts = [(0.0, 0.0), (x_back, 0.0), (x_back, tail_h_mm), A, B0, F0, F1,
           (F1[0] - lip_mm * 0.5, F1[1]), (0.0, F1[1])]
    g = Polygon(pts).buffer(0)
    # round the outside corners a little (1mm) -- no sharp tips to snap off
    g = g.buffer(-1.0, join_style=1).buffer(1.0, join_style=1).union(
        g.intersection(box(-1, -1, x_back + 1, 0.5)))  # keep the ground edge flat
    return g, info


def nest(g, sheet_mm=300.0, margin_mm=5.0, gap_mm=2.0):
    """Feet in rows across the sheet as head-to-tail PAIRS (a foot is a
    wedge: one turned 180deg slides into the other's empty corner), then a
    lone foot where a row has room for one more. Returns placed polygons
    (sheet frame: 0..sheet_mm)."""
    g = affinity.translate(g, -g.bounds[0], -g.bounds[1])
    r = affinity.rotate(g, 180, origin=(0, 0))
    r = affinity.translate(r, -r.bounds[0], -r.bounds[1])
    W, H = g.bounds[2], g.bounds[3]
    # the rotated copy slides left / down until it is gap_mm from the first
    best = None
    for dy in [k * 0.5 for k in range(int(H * 2) + 1)]:
        for k in range(int(W * 10) + 1):
            dx = W + gap_mm - k * 0.1
            if affinity.translate(r, dx, -dy).distance(g) < gap_mm:
                break
        cand = (dx + 0.1, -dy)
        pair = [g, affinity.translate(r, *cand)]
        bx = min(q.bounds[0] for q in pair), min(q.bounds[1] for q in pair), \
            max(q.bounds[2] for q in pair), max(q.bounds[3] for q in pair)
        area = (bx[2] - bx[0]) * (bx[3] - bx[1])
        if best is None or area < best[0]:
            best = (area, pair, bx)
    _a, pair, bx = best
    pair = [affinity.translate(q, -bx[0], -bx[1]) for q in pair]
    PW, PH = bx[2] - bx[0], bx[3] - bx[1]
    placed = []
    y = margin_mm
    while y + PH <= sheet_mm - margin_mm + 1e-9:
        x = margin_mm
        while x + PW <= sheet_mm - margin_mm + 1e-9:
            placed += [affinity.translate(q, x, y) for q in pair]
            x += PW + gap_mm
        if x + W <= sheet_mm - margin_mm:  # room for one more on its own
            placed.append(affinity.translate(g, x, y))
        y += PH + gap_mm
    # a last row of single feet if a pair row doesn't fit but a foot does
    if y + H <= sheet_mm - margin_mm:
        x = margin_mm
        while x + W <= sheet_mm - margin_mm:
            placed.append(affinity.translate(g, x, y))
            x += W + gap_mm
    return placed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--angle", type=float, default=75.0, help="display angle, degrees from horizontal")
    ap.add_argument("--tray-t-mm", type=float, default=6.0)
    ap.add_argument("--slot-clear-mm", type=float, default=0.2)
    ap.add_argument("--margin-mm", type=float, default=25.0,
                    help="rear contact behind the tray COM (25mm: ~10deg knock back before it tips)")
    ap.add_argument("--feet-per-stand", type=int, default=2)
    ap.add_argument("--material", default="plywood_veneer_3ply_3mm")
    a = ap.parse_args()
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    g, info = foot(a.angle, a.tray_t_mm, a.slot_clear_mm, margin_mm=a.margin_mm)
    com, xf, xb, margin = DS.stability(g, info, tray_mm=TRAY_MM)
    feet = nest(g)
    paths = [list(f.exterior.coords) for f in feet]
    from emitter import load_material

    gc = DS.emit_gcode(paths, load_material(a.material),
                       f"stand feet, {len(feet)} up, {a.angle:.0f} deg")
    gc = gc.replace("SAME machine frame as the puzzle cut (disc bbox 0..290): run it on the same "
                    "sheet without moving it -- coordinates reach -5.5 / 295.5 (the stock edges)",
                    "its OWN 300mm sheet, origin at the sheet corner (0..300)")
    gc = gc.replace("display stand: 4 feet from the corner offcuts, then the frame outline (last)",
                    f"display stand feet: {len(feet)} up, {a.feet_per_stand} per stand, "
                    f"tray at {a.angle:.0f} deg from horizontal")
    (out / "stand_feet.gcode").write_text(gc)
    _render(out, g, info, feet, com, margin)
    b = g.bounds
    print(f"foot {b[2] - b[0]:.1f} x {b[3] - b[1]:.1f}mm, slot {info['w']:.1f}mm at {a.angle:.0f} deg; "
          f"tray COM {margin:.1f}mm in front of the rear contact")
    print(f"{len(feet)} feet on one 300mm sheet = {len(feet) // a.feet_per_stand} stands "
          f"at {a.feet_per_stand} feet each -> {out}/stand_feet.gcode")


def _render(out, g, info, feet, com, margin):
    from PIL import Image, ImageDraw

    k = 3.0
    img = Image.new("RGB", (int(300 * k), int(300 * k)), (235, 220, 190))
    d = ImageDraw.Draw(img)
    for f in feet:
        d.polygon([(x * k, (300 - y) * k) for x, y in f.exterior.coords], fill=(120, 160, 110),
                  outline=(200, 0, 0))
    d.text((8, 8), f"{len(feet)} feet on a 300mm sheet", fill=(0, 0, 0))
    img.save(out / "stand_feet_sheet.png")
    # side view
    W, Hh = 900, 700

    def Q(x, y):
        return ((x + 40) * k, Hh - (y + 20) * k)

    img = Image.new("RGB", (W, Hh), "white")
    d = ImageDraw.Draw(img)
    d.line([Q(-40, 0), Q(260, 0)], fill=(0, 0, 0), width=2)
    d.polygon([Q(*p) for p in g.exterior.coords], fill=(120, 160, 110), outline=(0, 90, 0))
    B0, u, nf, t = info["B0"], info["u"], info["nf"], info["tray_t_mm"]
    L = 220  # draw the lower part of the tray only
    tray = [B0, (B0[0] + L * u[0], B0[1] + L * u[1]),
            (B0[0] + L * u[0] + t * nf[0], B0[1] + L * u[1] + t * nf[1]),
            (B0[0] + t * nf[0], B0[1] + t * nf[1])]
    d.polygon([Q(*p) for p in tray], fill=(214, 190, 150), outline=(120, 90, 50))
    d.line([Q(com[0], 0), Q(com[0], 200)], fill=(200, 0, 0), width=1)
    d.text((8, 8), f"{info['angle_deg']:.0f} deg; red = under the tray's centre of mass "
           f"({margin:.0f}mm in front of the rear contact)", fill=(0, 0, 0))
    img.save(out / "stand_feet_side.png")


if __name__ == "__main__":
    main()
