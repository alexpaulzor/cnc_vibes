#!/usr/bin/env python3
"""holders.py -- slot-together orchid pot holders, two styles x two pot sizes, sized from the material profile.

Styles (both first cut from hand-written OpenSCAD, or_brace.scad / or_brace2.scad):

  stand   two crossed triangular arms (one long leg with round holes, one short post) cradling the pot,
          half-lapped where they cross under the pot. A lock disc with a + slot drops over the two
          tongues at the crossing: it keeps the arms square and the pot sits on it.
  flower  four "flowercube" fins in a # around the pot (half-lapped at the feet, the pot sits on
          their floors) under a flower top plate. Eight fin tabs go through closed slots in the plate.

Pots: `square` (the 47 -> 65 mm, 85 mm tall square pot, or_brace2.scad) and `net3` (a 3" net cup).

Why the first cuts wobbled or cracked: the .scad files drew every slot at a guessed thickness (3.0 mm for
the stand, 3.3 mm for the flowercube), but the ply3 sheet is 2.63 mm. Here every slot, tongue and tab
width comes from `gcode.fit(material)`: slot = thickness + slot_extra, and the toolpath is kerf-compensated
(every part grown by kerf/2, so slots come out at that width, not kerf wider -- the mdf3old fit that
verified tight and snug was made this way). Joints are deeper too: the half-laps engage 10 mm (the stand's were 5 mm), the
plate is held by closed slots instead of shallow notches cut into its inner edge, and inside corners
are filleted.

Usage:
  python holders.py                          # all four holders, ply3 -> out/*.gcode + out/*.png
  python holders.py --style stand --pot net3 --material ply3
  python holders.py --list-pots
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

from shapely import affinity
from shapely.geometry import MultiPolygon, Point, Polygon, box
from shapely.ops import polylabel, unary_union

DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DIR.parent / "quickcut"))
import gcode  # noqa: E402

BED = (390.0, 290.0)      # usable cut area inside the 400 x 300 envelope (jigsawzall keeps Y under ~293)
GAP = 4.0                 # between parts on the sheet
MARGIN = 3.0              # from X0 Y0
MIN_FEATURE = 3.0         # thinnest wood left anywhere (ply3 min_feature_mm)
SEAT = 0.3                # extra depth on each half-lap slot so both parts reach the table


# ---------------------------------------------------------------- pots
@dataclass(frozen=True)
class Pot:
    """A tapered pot, measured outside: widths at the bottom and at the rim, height bottom -> rim top.
    Square pots: face-to-face width, corner radius r_*. Round pots: diameters; an optional lip
    (lip_d wide, lip_h tall) at the top that the holder stays clear of."""
    name: str
    shape: str            # "square" | "round"
    bottom: float
    top: float
    height: float
    r_bottom: float = 0.0
    r_top: float = 0.0
    lip_d: float = 0.0
    lip_h: float = 0.0
    source: str = ""

    def width(self, z: float) -> float:
        """outside width (or diameter) at height z above the pot's bottom; constant above the rim"""
        f = min(max(z / self.height, 0.0), 1.0)
        return self.bottom + (self.top - self.bottom) * f

    def corner(self, z: float) -> float:
        f = min(max(z / self.height, 0.0), 1.0)
        return self.r_bottom + (self.r_top - self.r_bottom) * f

    def half_chord(self, z: float, offset: float) -> float:
        """half the pot's width at height z, in a vertical plane `offset` from its axis (parallel to a
        face for a square pot)"""
        h = self.width(z) / 2
        if self.shape == "round":
            return math.sqrt(max(h * h - offset * offset, 0.0))
        r = self.corner(z)
        flat = h - r
        if abs(offset) <= flat:
            return h
        d = abs(offset) - flat
        return flat + math.sqrt(max(r * r - d * d, 0.0))

    def footprint(self, z: float, grow: float = 0.0) -> Polygon:
        """horizontal section at height z, grown by `grow`"""
        w = self.width(z)
        if self.shape == "round":
            return Point(0, 0).buffer(w / 2 + grow, quad_segs=32)
        r = self.corner(z)
        core = w / 2 - r
        return box(-core, -core, core, core).buffer(r + grow, quad_segs=16)


POTS = {
    "square": Pot("square", "square", bottom=47, top=65, height=85, r_bottom=7, r_top=10,
                  source="or_brace2.scad (bottom_w 47, top_w 65, top_h 85, top_or 10); bottom corner r 7 assumed"),
    "net3": Pot("net3", "round", bottom=55, top=72, height=62.5, lip_d=80, lip_h=3,
                source='3" net cup: lip 80, body 72, bottom 55 TYPICAL (not measured); height 62.5 = the '
                       "75 first assumed less 12.5 (the stand cut at 75 came out 1-1.5 cm too tall)"),
}


# ---------------------------------------------------------------- geometry helpers
def rect(x0, y0, x1, y1) -> Polygon:
    return box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def round_concave(g, r: float):
    """fillet inside corners (closing): stops cracks starting at sharp inside corners"""
    return g.buffer(r, quad_segs=8).buffer(-r, quad_segs=8)


def drop_slivers(g, w: float = MIN_FEATURE):
    """remove material narrower than w (opening) -- also rounds outside corners by w/2"""
    return g.buffer(-w / 2, quad_segs=8).buffer(w / 2, quad_segs=8)


def largest(g) -> Polygon:
    if isinstance(g, MultiPolygon):
        g = max(g.geoms, key=lambda p: p.area)
    return g


def drop_small_holes(p: Polygon, min_area: float = 12.0) -> Polygon:
    return Polygon(p.exterior, [i for i in p.interiors if Polygon(i).area >= min_area])


def section(pot: Pot, offset: float, z_bottom: float, z_top: float, grow: float, steps: int = 24) -> Polygon:
    """the pot's cut-out in a vertical plane `offset` from its axis, pot bottom at z_bottom, grown
    `grow` each side, open past z_top"""
    zs = [z_bottom + (z_top - z_bottom) * k / steps for k in range(steps + 1)]
    right = [(pot.half_chord(z - z_bottom, offset) + grow, z) for z in zs]
    right.append((right[-1][0], z_top + 30))
    left = [(-x, z) for x, z in reversed(right)]
    return Polygon(right + left)


def flowercube(cx, cy, dx, dy, num_ovals=6, scale=1 / 3, width=13.0, start_r0=1, start_offs=1, n=96):
    """or_brace2.scad flowercube(), 2D: overlapping elliptical rings (num_ovals/2 orientations)"""
    max_r = math.hypot(dx, dy) / 2
    a_in, b_in = max_r - width, max_r * scale - width
    dr = 360 / num_ovals / 2
    r0 = dr / 2 if (num_ovals % 2) == start_r0 else 0
    rings, ang = [], -90 + dr * start_offs + r0
    while ang <= 90 - dr * start_offs + r0 + 1e-9:
        outer = affinity.scale(Point(0, 0).buffer(1, quad_segs=n // 4), max_r, max_r * scale)
        ring = outer
        if b_in > 0:
            ring = outer.difference(affinity.scale(Point(0, 0).buffer(1, quad_segs=n // 4), a_in, b_in))
        rings.append(affinity.rotate(ring, ang, origin=(0, 0)))
        ang += dr
    return affinity.translate(unary_union(rings), cx, cy), max_r


def place_circles(avail, radii, margin):
    """greedy round holes: the biggest circle that fits in `avail` (shrunk by margin), up to each radius"""
    out = []
    for rmax in radii:
        a = avail.buffer(-margin)
        if a.is_empty:
            break
        a = largest(a)
        p = polylabel(a, tolerance=0.2)
        r = min(a.exterior.distance(p), *(i.distance(p) for i in a.interiors), rmax) if a.interiors \
            else min(a.exterior.distance(p), rmax)
        if r < 4:
            break
        c = p.buffer(r, quad_segs=24)
        out.append(c)
        avail = avail.difference(c.buffer(margin))
    return out


# ---------------------------------------------------------------- parts
@dataclass
class Part:
    name: str
    shape: Polygon          # in mm, any position
    note: str = ""


@dataclass
class Fit:
    material: str
    t: float                # sheet thickness
    kerf: float
    slot: float             # drawn slot width = t + slot_extra


def fit_for(material_id: str) -> Fit:
    t, kerf, extra = gcode.fit(material_id)
    return Fit(material_id, t, kerf, t + extra)


def clearance(pot: Pot) -> float:
    """radial gap between pot and wood: a little more for the unmeasured net cup"""
    return 0.75 if pot.shape == "square" else 1.0


def stand_parts(pot: Pot, f: Fit, lift: float = 18.0, post_w: float = 12.0):
    """two arms + a lock disc. Arm coords: u along the arm (pot axis at u=0), z up (table z=0)."""
    t, sw, clr = f.t, f.slot, clearance(pot)
    z_pot = lift + t                              # pot bottom sits on the disc
    top = z_pot + (pot.height - pot.lip_h - 1.0 if pot.lip_h else pot.height - 8.0)
    hw = lambda z: pot.half_chord(z - z_pot, 0) + clr        # noqa: E731
    hw_top = hw(top)
    reach = hw_top + post_w + 0.95 * top           # long leg tip
    foot = hw_top + post_w + 0.30 * top            # short post foot
    outer = Polygon([(-foot, 0), (reach, 0), (hw_top + post_w, top), (-(hw_top + post_w), top)])
    cut = section(pot, 0, z_pot, top, clr)
    cut = cut.union(rect(-hw(z_pot), lift, hw(z_pot), z_pot + 0.01))           # floor under the disc
    cut = cut.buffer(-3, quad_segs=8).buffer(3, quad_segs=8)                    # 3 mm fillets at the floor
    disc_d = 2 * (hw(z_pot) - 0.75)                                             # fits inside the cradle
    tongue = min(0.5 * disc_d, 30.0)
    arm = drop_slivers(outer.difference(cut)).union(rect(-tongue / 2, lift - 0.01, tongue / 2, z_pot))
    # holes in the long leg, clear of the cradle, the base and the edges
    avail = outer.difference(cut.buffer(8)).difference(rect(-1e3, -1, hw_top + post_w + 2, 1e3))
    holes = place_circles(avail, (0.20 * top, 0.10 * top, 0.10 * top), margin=5.0)
    for c in holes:
        arm = arm.difference(c)
    mid = z_pot / 2
    arm_a = arm.difference(rect(-sw / 2, mid - SEAT, sw / 2, z_pot + 5))       # slotted from the top
    arm_b = arm.difference(rect(-sw / 2, -5, sw / 2, mid + SEAT))              # slotted from the bottom
    disc = Point(0, 0).buffer(disc_d / 2, quad_segs=48)
    plus = unary_union([rect(-tongue / 2 - 0.3, -sw / 2, tongue / 2 + 0.3, sw / 2),
                        rect(-sw / 2, -tongue / 2 - 0.3, sw / 2, tongue / 2 + 0.3)])
    disc = disc.difference(plus)
    info = dict(lift=lift, z_pot=z_pot, top=top, reach=reach, foot=foot, disc_d=disc_d, tongue=tongue,
                halflap=mid, holes=[round(c.bounds[2] - c.bounds[0], 1) for c in holes])
    return [Part("arm A (slot from top)", largest(arm_a)), Part("arm B (slot from bottom)", largest(arm_b)),
            Part("lock disc", disc, "drops over the crossing; pot sits on it")], info


def flower_parts(pot: Pot, f: Fit, lift: float = 20.0, gap: float = 4.0, tab: float = 8.0):
    """four fins + top plate. Fin coords: u along the fin, z up; fins A in planes x=+-c, fins B in y=+-c."""
    t, sw, clr = f.t, f.slot, clearance(pot)
    c = 15.0 if pot.shape == "square" else 16.0
    z_pot = lift
    plate_top = z_pot + (pot.height - pot.lip_h - 1.0 if pot.lip_h else pot.height - 22.0)
    zp = plate_top - t                             # plate underside = fin tops
    h = pot.half_chord(zp - z_pot, c) + clr        # pot's half width in a fin's plane at the plate
    flower, _ = flowercube(0, zp / 2, 2 * pot.bottom, zp)
    base = rect(-(c + 12), 0, c + 12, lift)
    shoulders = [rect(s * h, zp - 10, s * (h + gap + tab + 4), zp) for s in (-1, 1)]
    body = unary_union([flower, base, *shoulders]).intersection(rect(-1e3, 0, 1e3, zp))
    cut = section(pot, c, z_pot, zp, clr)
    hb = pot.half_chord(0, c) + clr                # relief holes in the floor corners (no sharp corner to crack)
    cut = cut.union(unary_union([Point(s * hb, z_pot).buffer(1.5, quad_segs=8) for s in (-1, 1)]))
    body = drop_slivers(round_concave(body.difference(cut), 1.0))
    body = drop_small_holes(largest(body))
    tabs = [rect(s * (h + gap), zp - 0.01, s * (h + gap + tab), plate_top) for s in (-1, 1)]
    body = body.union(unary_union(tabs))
    mid = lift / 2
    fin_a = body.difference(unary_union([rect(s * c - sw / 2, mid - SEAT, s * c + sw / 2, lift + 30)
                                         for s in (-1, 1)]))
    fin_b = body.difference(unary_union([rect(s * c - sw / 2, -5, s * c + sw / 2, mid + SEAT)
                                         for s in (-1, 1)]))
    # plate (x, y): hole at the plate's top surface, slots under the fin tabs
    hole = pot.footprint(plate_top - z_pot, clr)
    slots = []
    for s in (-1, 1):
        for q in (-1, 1):
            y0, y1 = q * (h + gap - 0.2), q * (h + gap + tab + 0.2)
            slots.append(rect(s * c - sw / 2, y0, s * c + sw / 2, y1))      # fins A (x = +-c)
            slots.append(rect(y0, s * c - sw / 2, y1, s * c + sw / 2))      # fins B (y = +-c)
    slots = unary_union(slots)
    pw = pot.width(plate_top - z_pot)
    pflower, _ = flowercube(0, 0, 1.7 * pw, 1.7 * pw, start_r0=0, start_offs=0)
    ring = hole.buffer(gap + tab + 7, quad_segs=16)
    plate = unary_union([pflower, ring, slots.buffer(4)]).difference(hole)
    plate = drop_small_holes(largest(drop_slivers(round_concave(plate, 1.0))))
    plate = plate.difference(slots)
    info = dict(lift=lift, c=c, plate_top=plate_top, fin_top=zp, h=h, tab=tab, gap=gap, halflap=mid,
                hole=round(pw + 2 * clr, 2), plate_d=round(plate.bounds[2] - plate.bounds[0], 1))
    return [Part("fin A1 (slots from top)", largest(fin_a)), Part("fin A2 (slots from top)", largest(fin_a)),
            Part("fin B1 (slots from bottom)", largest(fin_b)), Part("fin B2 (slots from bottom)", largest(fin_b)),
            Part("top plate", plate)], info


STYLES = {"stand": stand_parts, "flower": flower_parts}


# ---------------------------------------------------------------- layout + G-code
def _shelves(items, bed):
    """shelf-pack (name, polygon) items, tallest first; returns sheets of (name, placed polygon)"""
    items = sorted(items, key=lambda it: -(it[1].bounds[3] - it[1].bounds[1]))
    sheets, cur, x, y, row_h = [], [], MARGIN, MARGIN, 0.0
    for name, g in items:
        x0, y0, x1, y1 = g.bounds
        w, hh = x1 - x0, y1 - y0
        if w > bed[0] - MARGIN or hh > bed[1] - MARGIN:
            return None
        if x + w > bed[0]:
            x, y, row_h = MARGIN, y + row_h + GAP, 0.0
        if y + hh > bed[1]:
            sheets.append(cur)
            cur, x, y, row_h = [], MARGIN, MARGIN, 0.0
        cur.append((name, affinity.translate(g, x - x0, y - y0)))
        x += w + GAP
        row_h = max(row_h, hh)
    return sheets + ([cur] if cur else [])


def layout(parts, bed=BED):
    """parts onto as few sheets as possible: every as-is / turned-90 combination is shelf-packed and the
    one with the fewest sheets, then the smallest used area, wins"""
    best = None
    for mask in range(1 << len(parts)):
        items = [(p.name, affinity.rotate(p.shape, 90, origin=(0, 0)) if mask >> k & 1 else p.shape)
                 for k, p in enumerate(parts)]
        sheets = _shelves(items, bed)
        if sheets is None:
            continue
        ext = unary_union([g for s in sheets for _, g in s]).bounds
        score = (len(sheets), ext[2] * ext[3])
        if best is None or score < best[0]:
            best = (score, sheets)
    if best is None:
        raise SystemExit(f"a part is bigger than the {bed[0]:.0f} x {bed[1]:.0f} bed")
    return best[1]


def loops(poly: Polygon):
    rings = [poly.exterior, *poly.interiors]
    out = []
    for r in rings:
        pts = [(round(px, 3), round(py, 3)) for px, py in r.simplify(0.02).coords]
        out.append(gcode.Polyline(pts, True))
    return out


def sheet_gcode(placed, f: Fit, title: str, notes) -> list[str]:
    cuts = [pl for _, g in placed for pl in loops(g)]
    ordered = gcode.order(cuts)
    j = gcode.job(f.material)
    lines = gcode.preamble(j, title=title, notes=notes)
    for k, pl in enumerate(ordered, 1):
        lines += gcode.cut(pl, j, k, len(ordered))
    return lines


def cut_minutes(placed, f: Fit) -> float:
    """laser-on time: perimeter x passes + one warmup and one overlap per loop, at the recipe feed"""
    j = gcode.job(f.material)
    n = sum(1 + len(g.interiors) for _, g in placed)
    per = sum(g.exterior.length + sum(i.length for i in g.interiors) for _, g in placed)
    return (per * j.passes + n * 2 * j.warmup_mm) / j.feed_mm_per_min


def preview(placed_sheets, path: Path, title: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    n = len(placed_sheets)
    fig, axes = plt.subplots(1, n, figsize=(7.5 * n, 6), squeeze=False)
    for ax, placed in zip(axes[0], placed_sheets):
        ax.add_patch(plt.Rectangle((0, 0), *BED, fill=False, ls="--", lw=0.6, color="0.6"))
        for name, g in placed:
            for r in [g.exterior, *g.interiors]:
                xs, ys = r.xy
                ax.plot(xs, ys, "k-", lw=0.6)
            cx, cy = g.representative_point().coords[0]
            ax.text(cx, cy, name, fontsize=6, ha="center", color="tab:blue")
        ax.set_aspect("equal")
        ax.set_xlim(-5, BED[0] + 5)
        ax.set_ylim(-5, BED[1] + 5)
        ax.grid(True, lw=0.3)
    fig.suptitle(title, fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def assembly(style: str, pot: Pot, f: Fit, parts, info, path: Path):
    """3D wireframe of the assembled holder with the pot in place (an assembly picture, not a check)"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig = plt.figure(figsize=(6, 6))
    ax = fig.add_subplot(projection="3d")

    def draw(g, to3d, color, z_off=(0.0,)):
        for r in [g.exterior, *g.interiors]:
            for dz in z_off:
                pts = [to3d(u, v, dz) for u, v in r.coords]
                ax.plot(*zip(*pts), color=color, lw=0.6)

    t = f.t
    if style == "stand":
        a, b, disc = (p.shape for p in parts)
        draw(a, lambda u, z, d: (u, d - t / 2, z), "tab:brown", (0, t))
        draw(b, lambda u, z, d: (d - t / 2, u, z), "peru", (0, t))
        draw(disc, lambda x, y, d: (x, y, info["lift"] + d), "goldenrod", (0, t))
        z0 = info["z_pot"]
    else:
        fa, _, fb, _, plate = (p.shape for p in parts)
        c = info["c"]
        for s in (-1, 1):
            draw(fa, lambda u, z, d, s=s: (s * c + d - t / 2, u, z), "tab:brown", (0, t))
            draw(fb, lambda u, z, d, s=s: (u, s * c + d - t / 2, z), "peru", (0, t))
        draw(plate, lambda x, y, d: (x, y, info["fin_top"] + d), "goldenrod", (0, t))
        z0 = info["lift"]
    for zr in (0, pot.height):
        fp = pot.footprint(zr)
        ax.plot(*zip(*[(x, y, z0 + zr) for x, y in fp.exterior.coords]), color="tab:green", lw=1)
    ax.set_box_aspect((1, 1, 0.6))
    lim = max(abs(v) for p in parts[:1] for v in p.shape.bounds[::2])
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-lim, lim)
    ax.set_zlim(0, 1.2 * lim)
    ax.view_init(elev=22, azim=-60)
    ax.set_title(f"orpot {style} / {pot.name} assembled (green = pot)", fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def toolpath(shape: Polygon, kerf: float) -> Polygon:
    """kerf compensation: the beam's centreline runs kerf/2 outside the part (inside holes and slots),
    so the finished part is the drawn one. Mitre joins keep slot corners square."""
    return shape.buffer(kerf / 2, join_style="mitre", mitre_limit=4.0)


def build(style: str, pot: Pot, f: Fit, out_dir: Path, png: bool = True):
    parts, info = STYLES[style](pot, f)
    sheets = layout([Part(p.name, toolpath(p.shape, f.kerf), p.note) for p in parts])
    written = []
    for k, placed in enumerate(sheets, 1):
        tag = f"_s{k}" if len(sheets) > 1 else ""
        title = f"orpot {style} holder for the {pot.name} pot{tag.replace('_', ' ')} ({', '.join(n for n, _ in placed)})"
        notes = [f"pot: {pot.source}",
                 f"material {f.material}: t {f.t} mm, slots {f.slot:.2f} mm finished, kerf {f.kerf} mm compensated (path kerf/2 outside each part)",
                 "geometry (mm): " + ", ".join(f"{k2} {round(v, 2) if isinstance(v, float) else v}"
                                               for k2, v in info.items()),
                 "X0 Y0 = bottom-left corner of the stock"]
        lines = sheet_gcode(placed, f, title, notes)
        base = f"orpot_{style}_{pot.name}{tag}"
        path = gcode.write(gcode.std_path(out_dir, base, lines, f.material), lines)
        written.append((path, placed))
    if png:
        assembly(style, pot, f, parts, info, out_dir / f"orpot_{style}_{pot.name}_{f.material}_3d.png")
        preview([p for _, p in written], out_dir / f"orpot_{style}_{pot.name}_{f.material}.png",
                f"orpot {style} / {pot.name} / {f.material} (t {f.t} mm, slot {f.slot:.2f} mm)")
    return written, info


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], epilog=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--style", choices=[*STYLES, "all"], default="all")
    ap.add_argument("--pot", choices=[*POTS, "all"], default="all")
    ap.add_argument("--material", default="ply3")
    ap.add_argument("-o", "--out", type=Path, default=DIR / "out")
    ap.add_argument("--net-lip", type=float, help="net cup: lip diameter, mm")
    ap.add_argument("--net-top", type=float, help="net cup: body diameter just under the lip, mm")
    ap.add_argument("--net-bottom", type=float, help="net cup: bottom diameter, mm")
    ap.add_argument("--net-height", type=float, help="net cup: height, bottom to the top of the lip, mm")
    ap.add_argument("--list-pots", action="store_true")
    a = ap.parse_args(argv)
    net = POTS["net3"]
    over = {k: v for k, v in dict(lip_d=a.net_lip, top=a.net_top, bottom=a.net_bottom, height=a.net_height).items()
            if v is not None}
    if over:
        POTS["net3"] = Pot(**{**net.__dict__, **over, "source": "net cup, measured: " +
                              ", ".join(f"{k} {v}" for k, v in over.items())})
    if a.list_pots:
        for p in POTS.values():
            print(f"{p.name}: {p.source}")
        return 0
    f = fit_for(a.material)
    print(f"{a.material}: thickness {f.t} mm, slot {f.slot:.2f} mm finished, kerf {f.kerf} mm compensated")
    for style in (STYLES if a.style == "all" else [a.style]):
        for pname in (POTS if a.pot == "all" else [a.pot]):
            written, info = build(style, POTS[pname], f, a.out)
            for path, placed in written:
                print(f"  {path.relative_to(DIR)}  ({len(placed)} parts, ~{cut_minutes(placed, f):.0f} min laser-on)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
