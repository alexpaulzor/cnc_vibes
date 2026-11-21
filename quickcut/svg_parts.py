#!/usr/bin/env python3
"""svg_parts.py -- a colour-coded cut file (bought designs, Illustrator/Inkscape SVG) -> etch + cut plates.

Stroke colour says what a line is: cut (closed outlines and their holes), etch (score lines), or hatch
(closed shapes filled with etch lines, e.g. eyes). Every other colour -- the "1 inch / delete before
cutting" size square, guides -- is dropped and listed. Cut outlines become parts (an outline with the
holes and etch lines inside it), numbered left-to-right, top-to-bottom as drawn. Parts are scaled,
nested onto your stock (quickcut/nest.py, any angle a long part needs) and written through
gcode.write: each plate etches first, then cuts (holes before outlines), warmup on every path.

    python quickcut/svg_parts.py design.svg --list                   # parts, sizes, colours -> preview
    python quickcut/svg_parts.py design.svg -o out/ --name unicorn   # all parts onto plates
    python quickcut/svg_parts.py design.svg --parts 1,4 --each       # a la carte: one file per part
    python quickcut/svg_parts.py design.svg --scale 0.8 --stock 300 300 --material ply3

Units come from the SVG's width/height (mm, in, pt...) over its viewBox; --scale multiplies.
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path

from shapely import affinity
from shapely.geometry import LineString, MultiLineString, Polygon
from shapely.ops import unary_union

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gcode  # noqa: E402
import nest as qnest  # noqa: E402
from svg2gcode import _parse_path_data, _point_pairs, _units_to_mm  # noqa: E402

MACHINE = (390.0, 292.5)       # max X / Y the head reaches (jigsawzall HANDOFF: Y limit trips at 294.45)
DEFAULT_CUT = "#df1f26,#ff0000,#e30613,#ed1c24"
DEFAULT_ETCH = "#00a651,#0000ff,#2e3192,#00ff00"
DEFAULT_HATCH = "#231f20,#000000"


# ---------------------------------------------------------------- reading
def _attr(tag_attrs: str, name: str):
    m = re.search(rf'(?<![\w-]){name}="([^"]*)"', tag_attrs)
    if m:
        return m.group(1).strip().lower()
    m = re.search(rf"(?<![\w-]){name}\s*:\s*([^;\"]+)", tag_attrs)       # style="stroke:#..."
    return m.group(1).strip().lower() if m else None


def read_svg(text: str):
    """[(stroke colour or None, fill or None, [Polyline in mm, Y up])] for every path/polygon/polyline"""
    if re.search(r"<\w+[^>]*\stransform=", text):
        raise SystemExit("this SVG uses transform= attributes; flatten them first (Inkscape: Path > Object to Path)")
    k = _units_to_mm(text)
    out, skipped = [], {}
    for tag, attrs in re.findall(r"<(\w+)\b([^>]*)>", text):
        if tag == "path":
            d = re.search(r'\sd="([^"]+)"', attrs)
            pls = _parse_path_data(d.group(1)) if d else []
        elif tag in ("polygon", "polyline"):
            pts = _point_pairs(re.search(r'points="([^"]+)"', attrs).group(1))
            pls = [gcode.Polyline(pts, tag == "polygon")]
        else:
            if tag in ("rect", "circle", "ellipse", "line"):
                skipped[tag] = skipped.get(tag, 0) + 1
            continue
        pls = [gcode.Polyline([(x * k, -y * k) for x, y in p.points], p.closed) for p in pls if len(p.points) > 1]
        out.append((_attr(attrs, "stroke"), _attr(attrs, "fill"), pls))
    if skipped:
        print(f"  NOT READ (convert to paths if they matter): {skipped}")
    return out


class Part:
    def __init__(self, outline: Polygon):
        self.poly = outline              # outline with holes as interiors
        self.etch: list = []             # open/closed etch lines (LineString)
        self.hatch: list = []            # closed shapes to fill (Polygon)
        self.number = 0

    @property
    def size(self):
        x0, y0, x1, y1 = self.poly.bounds
        return x1 - x0, y1 - y0

    def transformed(self, f):
        p = Part(f(self.poly))
        p.etch, p.hatch, p.number = [f(g) for g in self.etch], [f(g) for g in self.hatch], self.number
        return p


def build_parts(items, cut, etch, hatch, scale=1.0):
    """group by colour -> parts. Returns (parts, dropped {colour: count})"""
    loops, etches, hatches, dropped = [], [], [], {}
    for stroke, fill, pls in items:
        role = "cut" if stroke in cut else "etch" if stroke in etch else "hatch" if stroke in hatch else None
        if role is None:
            key = f"stroke {stroke}" if stroke not in (None, "none") else f"fill {fill}"
            dropped[key] = dropped.get(key, 0) + len(pls)
            continue
        for p in pls:
            if role == "cut":
                if not p.closed:
                    raise SystemExit(f"an open cut path ({len(p.points)} pts) -- close it in the design first")
                loops.append(Polygon(p.points).buffer(0))
            elif role == "etch":
                etches.append(LineString(p.points + ([p.points[0]] if p.closed else [])))
            else:
                hatches.append(Polygon(p.points).buffer(0))
    if scale != 1.0:
        f = lambda g: affinity.scale(g, scale, scale, origin=(0, 0))      # noqa: E731
        loops, etches, hatches = [f(g) for g in loops], [f(g) for g in etches], [f(g) for g in hatches]
    loops.sort(key=lambda g: -g.area)
    parts = []
    for g in loops:                       # an outline contained in a bigger one is a hole (one level deep)
        host = next((p for p in parts if p.poly.contains(g)), None)
        if host is None:
            parts.append(Part(Polygon(g.exterior)))
        else:
            host.poly = host.poly.difference(g)
    for line in etches:
        host = max(parts, key=lambda p: p.poly.buffer(0.5).intersection(line).length)
        clipped = line.intersection(host.poly)
        host.etch += [g for g in getattr(clipped, "geoms", [clipped]) if g.length > 0.2]
    for g in hatches:
        host = max(parts, key=lambda p: p.poly.buffer(0.5).intersection(g).area)
        host.hatch.append(g.intersection(host.poly))
    parts.sort(key=lambda p: (-round(p.poly.bounds[3] / 20), p.poly.bounds[0]))   # rows top-down, then left-right
    for n, p in enumerate(parts, 1):
        p.number = n
    return parts, dropped


# ---------------------------------------------------------------- hatching
def hatch_lines(poly: Polygon, spacing: float, angle: float = 45.0):
    """fill lines `spacing` apart across poly, joined boustrophedon wherever the join stays inside"""
    if poly.is_empty:
        return []
    c = poly.centroid
    rot = affinity.rotate(poly, -angle, origin=c)
    x0, y0, x1, y1 = rot.bounds
    rows, y, flip = [], y0 + spacing / 2, False
    while y < y1:
        seg = rot.intersection(LineString([(x0 - 1, y), (x1 + 1, y)]))
        segs = sorted((g for g in getattr(seg, "geoms", [seg]) if g.length > 0.05), key=lambda g: g.bounds[0])
        for g in (reversed(segs) if flip else segs):
            pts = list(g.coords)
            rows.append(pts[::-1] if flip else pts)
        flip = not flip
        y += spacing
    inside = rot.buffer(spacing * 0.6)
    chains = []
    for pts in rows:
        if chains and inside.contains(LineString([chains[-1][-1], pts[0]])):
            chains[-1] += pts
        else:
            chains.append(list(pts))
    return [list(affinity.rotate(LineString(ch), angle, origin=c).coords) for ch in chains]


# ---------------------------------------------------------------- G-code
def _lines(g):
    if isinstance(g, (LineString,)):
        return [list(g.coords)]
    if isinstance(g, MultiLineString) or hasattr(g, "geoms"):
        return [list(x.coords) for x in g.geoms if isinstance(x, LineString)]
    return []


def plate_gcode(parts, cut_job, etch_job, title, notes, hatch_mm):
    strokes = []
    for p in parts:
        for g in p.etch:
            for pts in _lines(g):
                closed = len(pts) > 2 and math.dist(pts[0], pts[-1]) < 1e-6
                strokes.append(gcode.Polyline(pts[:-1] if closed else pts, closed))
        for h in p.hatch:
            strokes.append(gcode.Polyline(list(h.exterior.coords)[:-1], True))      # crisp edge
            strokes += [gcode.Polyline(ch, False) for ch in hatch_lines(h, hatch_mm)]
    cuts = []
    for p in parts:
        for r in [p.poly.exterior, *p.poly.interiors]:
            cuts.append(gcode.Polyline([(round(x, 3), round(y, 3)) for x, y in r.simplify(0.02).coords][:-1], True))
    lines = gcode.preamble(cut_job, title=title, notes=notes, etch=etch_job if strokes else None)
    if strokes:
        chains = gcode.nearest_chains([gcode.prepare(s.points + ([s.points[0]] if s.closed else []),
                                                     simplify_mm=0.05, min_segment_mm=0.3) for s in strokes])
        lines.append(f"; ===== etch: {len(chains)} strokes (before any cut, the sheet still whole) =====")
        for k, pts in enumerate(chains, 1):
            closed = len(pts) > 2 and math.dist(pts[0], pts[-1]) < 1e-6
            lines += gcode.etch(gcode.Polyline(pts, closed), etch_job, k, len(chains))
    ordered = gcode.order(cuts)
    lines.append(f"; ===== cut: {len(ordered)} loops (holes before their outline) =====")
    for k, pl in enumerate(ordered, 1):
        lines += gcode.cut(pl, cut_job, k, len(ordered))
    return lines


def write_checked(path, lines, etch_job):
    """gcode.write with every validator rule, except that the etch recipe's feed may pass the
    profile's XY cap: the calibrated ply3 etch is F5000 and GRBL clamps it to $110 (3000 on the
    AnoleX), which is how it was calibrated. Anything else still refuses to write."""
    text = gcode.render(lines, validate=False)
    ok_f = f"F={float(etch_job.feed_mm_per_min)}" if etch_job else None
    bad = [v for v in gcode.check(text) if not (v.rule == "max_feed" and ok_f and v.message.startswith(ok_f))]
    if bad:
        raise gcode.GcodeError("G-code fails validation:\n  " + "\n  ".join(str(v) for v in bad[:20]))
    return gcode.write(path, lines, validate=False)


def laser_minutes(parts, cut_job, etch_job, hatch_mm):
    per = sum(p.poly.exterior.length + sum(i.length for i in p.poly.interiors) for p in parts)
    n = sum(1 + len(p.poly.interiors) for p in parts)
    t = (per * cut_job.passes + n * 2 * cut_job.warmup_mm) / cut_job.feed_mm_per_min
    if etch_job:
        e = sum(g.length for p in parts for g in p.etch)
        e += sum(h.area / hatch_mm + h.length for p in parts for h in p.hatch)
        t += e * etch_job.passes / etch_job.feed_mm_per_min
    return t


def preview(sheets, stock, path: Path, title: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    n = len(sheets)
    fig, axes = plt.subplots(1, n, figsize=(6.5 * n, 6.5), squeeze=False)
    for ax, parts in zip(axes[0], sheets):
        ax.add_patch(plt.Rectangle((0, 0), *stock, fill=False, ls="--", lw=0.6, color="0.6"))
        for p in parts:
            for r in [p.poly.exterior, *p.poly.interiors]:
                ax.plot(*r.xy, color="tab:red", lw=0.6)
            for g in p.etch:
                for pts in _lines(g):
                    ax.plot(*zip(*pts), color="tab:green", lw=0.4)
            for h in p.hatch:
                ax.fill(*h.exterior.xy, color="k")
            c = p.poly.representative_point()
            ax.text(c.x, c.y, str(p.number), fontsize=11, color="tab:blue", ha="center", va="center", weight="bold")
        ax.set_aspect("equal")
        ax.set_xlim(-5, stock[0] + 5)
        ax.set_ylim(-5, stock[1] + 5)
        ax.grid(True, lw=0.3)
    fig.suptitle(title + "   (red cut, green etch, black hatch, blue = part number)", fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=100)
    plt.close(fig)


# ---------------------------------------------------------------- main
def _colours(s: str):
    return {c.strip().lower() for c in s.split(",") if c.strip()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], epilog=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("svg", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=Path("build"))
    ap.add_argument("--name", help="file name stem (default: the SVG's)")
    ap.add_argument("--material", default="ply3")
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--stock", type=float, nargs=2, default=[300.0, 300.0], metavar=("W", "H"))
    ap.add_argument("--margin", type=float, default=2.0, help="from the stock edges, mm")
    ap.add_argument("--gap", type=float, default=4.0, help="between parts, mm")
    ap.add_argument("--parts", help="only these part numbers (see --list), e.g. 1,3-5")
    ap.add_argument("--each", action="store_true", help="one file per part instead of plates")
    ap.add_argument("--no-etch", action="store_true", help="cut only")
    ap.add_argument("--hatch-mm", type=float, default=0.3, help="hatch line spacing for filled shapes")
    ap.add_argument("--cut", default=DEFAULT_CUT, help="stroke colours that cut")
    ap.add_argument("--etch", default=DEFAULT_ETCH, help="stroke colours that etch (score)")
    ap.add_argument("--hatch", default=DEFAULT_HATCH, help="stroke colours whose closed shapes are etched solid")
    ap.add_argument("--list", action="store_true", help="list the parts and write a numbered preview only")
    a = ap.parse_args(argv)

    items = read_svg(a.svg.read_text())
    colours = sorted({s for s, _, _ in items if s} | {f"fill {f}" for s, f, _ in items if not s and f})
    print(f"{a.svg.name}: colours {colours}")
    parts, dropped = build_parts(items, _colours(a.cut), _colours(a.etch), _colours(a.hatch), a.scale)
    if dropped:
        print(f"  dropped (not cut/etch/hatch colours): {dropped}")
    if not parts:
        raise SystemExit("no closed cut outlines -- check --cut colours against the list above")
    if a.parts:
        want = set()
        for tok in a.parts.split(","):
            lo, _, hi = tok.partition("-")
            want |= set(range(int(lo), int(hi or lo) + 1))
        parts = [p for p in parts if p.number in want]
    if a.no_etch:
        for p in parts:
            p.etch, p.hatch = [], []
    stem = a.name or re.sub(r"\W+", "_", a.svg.stem).strip("_")
    a.out.mkdir(parents=True, exist_ok=True)
    for p in parts:
        w, h = p.size
        print(f"  part {p.number}: {w:.1f} x {h:.1f} mm, {len(p.poly.interiors)} holes, "
              f"{len(p.etch)} etch lines, {len(p.hatch)} hatched shapes")
    if a.list:
        x0, y0, x1, y1 = unary_union([p.poly for p in parts]).bounds
        moved = [p.transformed(lambda g: affinity.translate(g, -x0, -y0)) for p in parts]
        preview([moved], (x1 - x0, y1 - y0), a.out / f"{stem}_parts.png", f"{a.svg.name} at scale {a.scale:g}")
        print(f"  -> {a.out / (stem + '_parts.png')}")
        return 0

    bed = qnest.Bed(min(a.stock[0] - a.margin, MACHINE[0]), min(a.stock[1] - a.margin, MACHINE[1]), a.margin)
    cut_job = gcode.job(a.material)
    etch_job = None if a.no_etch else gcode.job(a.material, "etch")
    groups = [[p] for p in parts] if a.each else [parts]
    for group in groups:
        sheets = qnest.nest([(str(p.number), p.poly) for p in group], bed, gap=a.gap)
        by_no = {str(p.number): p for p in group}
        placed = [[by_no[pl.name].transformed(pl.apply) for pl in sheet] for sheet in sheets]
        for k, sheet in enumerate(placed, 1):
            tag = f"_part{sheet[0].number}" if a.each else (f"_plate{k}" if len(placed) > 1 else "")
            nums = ", ".join(str(p.number) for p in sheet)
            title = f"{a.svg.name}{tag.replace('_', ' ')}: parts {nums}, scale {a.scale:g}"
            notes = [f"material {a.material}; stock {a.stock[0]:g} x {a.stock[1]:g}; X0 Y0 = bottom-left corner",
                     "etch first (sheet still whole), then cut: holes before their outline"]
            lines = plate_gcode(sheet, cut_job, etch_job if any(p.etch or p.hatch for p in sheet) else None,
                                title, notes, a.hatch_mm)
            path = write_checked(gcode.std_path(a.out, f"{stem}{tag}", lines, a.material), lines, etch_job)
            preview([sheet], a.stock, path.with_suffix(".png"), path.stem)
            mins = laser_minutes(sheet, cut_job, etch_job, a.hatch_mm)
            print(f"  {path}  parts {nums}  ~{mins:.0f} min laser-on (estimate)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
