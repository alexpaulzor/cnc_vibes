#!/usr/bin/env python3
"""Flush 3mm hold-down kit for a honeycomb laser bed (no magnets, no clips).

Every part is cut from the SAME 3mm stock as the job and lies FLAT on the
honeycomb, so its top is flush with the workpiece top: nothing stands above
the work for the head (~4mm focus gap) to hit, anywhere it travels.

Parts:
  * KEY    -- a small upright tab: a 10x3mm top bar (sits in a part's slot,
              flush) over a 4mm-wide peg that drops ~6mm into one honeycomb
              cell; the 3mm shoulders rest on the cell walls.
  * FENCE  -- a flat bar with two LONG slots. A key drops through each slot
              into whatever cell lies under it, so the fence is locked
              sideways (perpendicular to its length) wherever it's placed --
              the long slots make it independent of the exact cell pitch and
              honeycomb orientation.
  * CORNER -- a flat L square with a long slot in each arm, same key system;
              the work's corner seats in its inside corner (with a relief
              notch for a chipped/fuzzy corner). Doubles as a repeatable
              zero reference.
  * WEDGE  -- a flat, shallow (self-locking) taper pushed between a fence and
              the work edge to clamp the work against the corner.

The only honeycomb dimension that matters is that a 4x3mm peg fits inside a
cell (cell >~6mm); `--pitch` only sizes the slot length (a key must always
find a cell within the slot), so a rough pitch is fine.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from shapely import affinity
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

T = 3.0  # stock thickness (mm)


def key(peg_w=4.0, peg_depth=6.0, bar_w=10.0):
    """Side profile, cut flat: bar on top (y in [0,T]), peg below."""
    bar = box(-bar_w / 2, 0, bar_w / 2, T)
    peg = box(-peg_w / 2, -peg_depth, peg_w / 2, 0)
    tip = 0.8  # chamfer the peg tip so it finds the cell
    peg = peg.difference(Polygon([(-peg_w / 2, -peg_depth), (-peg_w / 2 + tip, -peg_depth),
                                  (-peg_w / 2, -peg_depth + tip)]))
    peg = peg.difference(Polygon([(peg_w / 2, -peg_depth), (peg_w / 2 - tip, -peg_depth),
                                  (peg_w / 2, -peg_depth + tip)]))
    return unary_union([bar, peg])


def slot(cx, cy, length, along_x=True, width=T):
    s = box(-length / 2, -width / 2, length / 2, width / 2)
    s = s.union(Point(-length / 2, 0).buffer(width / 2)).union(Point(length / 2, 0).buffer(width / 2))
    if not along_x:
        s = affinity.rotate(s, 90, origin=(0, 0))
    return affinity.translate(s, cx, cy)


def fence(length=120.0, width=20.0, slot_len=22.0):
    body = box(0, 0, length, width)
    for cx in (length * 0.22, length * 0.78):
        body = body.difference(slot(cx, width / 2, slot_len))
    return body


def corner(arm=80.0, width=20.0, slot_len=22.0, relief_r=2.0):
    body = unary_union([box(0, 0, arm, width), box(0, 0, width, arm)])
    # work seats against the inside faces x=width and y=width
    body = body.difference(Point(width, width).buffer(relief_r))
    body = body.difference(slot(arm * 0.62, width / 2, slot_len))
    body = body.difference(slot(width / 2, arm * 0.62, slot_len, along_x=False))
    return body


def wedge(length=60.0, w0=5.0, w1=12.0, grip=10.0):
    # taper ~6.7 deg: shallow enough to self-lock under friction
    taper = Polygon([(0, 0), (length, 0), (length, w1), (0, w0)])
    tab = box(length, 0, length + grip, w1)  # square grip end to push/pull
    return unary_union([taper, tab])


def layout(n_fence=4, n_corner=2, n_wedge=6, n_key=12, gap=4.0, pitch=10.0):
    slot_len = pitch + 10.0 + 2.0  # key bar (10) can always reach a cell
    parts = []
    x0, y = gap, gap
    for i in range(n_corner):
        parts.append(("corner", affinity.translate(corner(slot_len=slot_len), x0 + i * (80 + gap), y)))
    y = gap + 80 + gap
    for i in range(n_fence):
        parts.append(("fence", affinity.translate(fence(slot_len=slot_len), gap, y + i * (20 + gap))))
    y_w = y + n_fence * (20 + gap)
    for i in range(n_wedge):
        r, c = divmod(i, 2)
        parts.append(("wedge", affinity.translate(wedge(), gap + c * (70 + gap), y_w + r * (12 + gap))))
    # keys in the free column right of the fences
    kx0 = gap + 120 + gap + 5
    for i in range(n_key):
        r, c = divmod(i, 3)
        parts.append(("key", affinity.translate(key(), kx0 + c * (10 + gap), y + 6 + r * (9 + gap))))
    W = max(p.bounds[2] for _n, p in parts) + gap
    H = max(p.bounds[3] for _n, p in parts) + gap
    return parts, (math.ceil(W), math.ceil(H))


def rings(poly):
    geoms = getattr(poly, "geoms", [poly])
    for g in geoms:
        yield list(g.exterior.coords)
        for r in g.interiors:
            yield list(r.coords)


def write_svg(parts, W, H, path):
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}mm" height="{H}mm" '
           f'viewBox="0 0 {W} {H}">']
    for _name, p in parts:
        for ring in rings(p):
            d = "M " + " L ".join(f"{x:.3f},{H - y:.3f}" for x, y in ring) + " Z"
            out.append(f'<path d="{d}" fill="none" stroke="#e60000" stroke-width="0.2"/>')
    out.append("</svg>")
    Path(path).write_text("\n".join(out))


def render(parts, W, H, path, pitch):
    s = 4  # px per mm
    side_h = 70
    img = Image.new("RGB", (int(W * s) + 40, int(H * s) + 40 + side_h * s), "white")
    d = ImageDraw.Draw(img)
    try:
        f = ImageFont.truetype("DejaVuSans.ttf", 14)
    except OSError:
        f = ImageFont.load_default()

    def P(x, y, ox=20, oy=20, hh=H):
        return (ox + x * s, oy + (hh - y) * s)

    fills = {"corner": (250, 225, 190), "fence": (230, 210, 170), "wedge": (215, 195, 160), "key": (200, 175, 140)}
    for name, p in parts:
        for g in getattr(p, "geoms", [p]):
            d.polygon([P(x, y) for x, y in g.exterior.coords], fill=fills[name], outline=(230, 0, 0))
            for r in g.interiors:
                d.polygon([P(x, y) for x, y in r.coords], fill="white", outline=(230, 0, 0))
    d.text((20, 2), f"Honeycomb hold-down kit, 3mm, flat-cut  {W}x{H}mm  (slots sized for ~{pitch:g}mm pitch)",
           fill="black", font=f)

    # side-view diagram: honeycomb, work, fence+key, wedge -- all flush
    oy = 40 + H * s
    sx = 5  # px/mm for the diagram
    X = lambda x: 30 + x * sx  # noqa: E731
    Y = lambda z: oy + 40 * sx / 4 * 0 + (12 - z) * sx  # noqa: E731
    d.text((20, oy - 4), "Side view (not to scale horizontally): everything flush with the 3mm work", fill="black", font=f)
    for c in range(0, 14):  # honeycomb cells
        d.rectangle([X(c * pitch), Y(0), X(c * pitch + pitch - 0.4), Y(-9)], outline=(90, 90, 90))
    d.rectangle([X(0), Y(T), X(60), Y(0)], fill=(240, 220, 180), outline="black")
    d.text((X(18), Y(T) + 2), "WORK", fill="black", font=f)
    d.rectangle([X(66), Y(T), X(76), Y(0)], fill=(215, 195, 160), outline="black")
    d.text((X(64), Y(T) - 18), "wedge", fill="black", font=f)
    d.rectangle([X(80), Y(T), X(120), Y(0)], fill=(230, 210, 170), outline="black")
    d.text((X(88), Y(T) - 18), "fence", fill="black", font=f)
    kx = 100
    d.rectangle([X(kx - 5), Y(T), X(kx + 5), Y(0)], fill=(200, 175, 140), outline="black")
    d.rectangle([X(kx - 2), Y(0), X(kx + 2), Y(-6)], fill=(200, 175, 140), outline="black")
    d.text((X(kx + 6), Y(-4)), "key peg in cell", fill="black", font=f)
    d.line([X(-2), Y(T + 4), X(125), Y(T + 4)], fill=(0, 120, 255), width=2)
    d.text((X(2), Y(T + 4) - 16), "nozzle ~4mm above work: nothing up here", fill=(0, 90, 200), font=f)
    img.save(path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pitch", type=float, default=10.0, help="honeycomb cell pitch (mm, rough is fine)")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "build" / "honeycomb_holddowns"))
    a = ap.parse_args(argv)
    parts, (W, H) = layout(pitch=a.pitch)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_svg(parts, W, H, out.with_suffix(".svg"))
    render(parts, W, H, out.with_suffix(".png"), a.pitch)
    counts = {}
    for n, _p in parts:
        counts[n] = counts.get(n, 0) + 1
    print(f"sheet {W}x{H}mm, parts {counts}")
    print(f"-> {out.with_suffix('.svg')}\n-> {out.with_suffix('.png')}")
    print("gcode (after measuring pitch): python quickcut/svg2gcode.py "
          f"{out.with_suffix('.svg')} --material plywood_veneer_3ply_3mm")


if __name__ == "__main__":
    main()
