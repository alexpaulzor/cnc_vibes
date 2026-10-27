#!/usr/bin/env python3
"""Display-stand feet for 290mm ring puzzles, nested many-up on their OWN
300mm sheet (RING_SPEC.md §13.17-13.18).

The tray = the leftover 300mm square veneer frame (puzzle cut out of it)
glued to an UNCUT 3mm MDF back, puzzle sitting in the recess. It stands
on its bottom edge in two feet, leaning back at the display angle
(degrees from horizontal; Alex's tilt test: the puzzle stays in to >=88).

v3 "keyed" foot (default): the frame gets NOTCHES at its bottom edge
(frame_notch_gcode(), cut with the puzzle -- that sheet is CNC'd anyway),
the MDF gets nothing. Each foot's slot is a STAIR STEP: a 3.2mm channel for
the MDF alone, and in front of it a lip that rises up into the frame's
notch. So
  * the MDF back is never cut (no extra setup, no batch of backplates);
  * the notch walls grip the foot's own 3mm thickness ~notch_depth up the
    tray, so a foot can't lean sideways or slide along the edge -- the
    weak point of a plain slotted foot -- and the notches set the stance
    (feet ~2/3 of the tray width apart, where the frame is deep);
  * notches go along the bottom (X-axis) edge only, cut from y = -10.

Side view of a foot (front at left), tray leaning back into it:

          lip in notch   back rest
     toe    ┌┐ ╱╱ ╲
    ___┌────┘│╱╱    ╲___________
    |__|_____|╱_________________|   <- table

Stability (computed, not assumed): the base reaches as far IN FRONT of the
tray's centre of mass as BEHIND it, so it tolerates the same knock either
way; the foot length is the largest that still nests >= min_sets x 2 feet
on one 300mm sheet. Durability: >= wall_mm of wood round the slot and the
decorative round cut-outs, a solid ground bar, 1mm rounded outside corners.

    PYTHONPATH=. python3 scripts/stand_feet_sheet.py OUTDIR [--angle 75] [--min-sets 16]
"""
import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "quickcut"))

from shapely import affinity  # noqa: E402
from shapely.geometry import LineString, Point, Polygon, box  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

TRAY_MM = 300.0
STOCK_MM = 300.0
DISC_MM = 290.0


def _parts(g):
    if g.is_empty:
        return []
    return list(g.geoms) if hasattr(g, "geoms") else [g]


def foot(angle_deg=75.0, length_mm=100.0, mdf_t=3.0, frame_t=3.0, clear=0.2,
         notch_depth=12.0, base_mm=6.0, toe_h=8.0, tail_h=8.0, rest_mm=30.0,
         lip_proud=3.0, wall_mm=5.0, keyed=True, hole=True):
    """Foot polygon (x back, y up, ground y=0) and info for stability().
    keyed: stair-step slot (MDF channel + lip into the frame notch);
    else a plain slot for the whole 6mm tray with a lip in front of it.
    length_mm: front contact to rear contact; split evenly about the
    tray's centre of mass."""
    th = math.radians(angle_deg)
    u = (math.cos(th), math.sin(th))
    nf = (-math.sin(th), math.cos(th))  # tray normal, toward the front
    tray_t = mdf_t + frame_t
    w = (mdf_t if keyed else tray_t) + clear  # slot (channel) width
    # slot bottom back corner B0 (MDF back face meets the slot bottom)
    B0 = (0.0, base_mm)
    F0 = (B0[0] + w * nf[0], B0[1] + w * nf[1])
    if F0[1] < base_mm:  # keep base_mm of wood under the lower corner
        dy = base_mm - F0[1]
        B0, F0 = (B0[0], B0[1] + dy), (F0[0], F0[1] + dy)
    lip_h = (notch_depth - 1.0) if keyed else 8.0  # 1mm clear of the notch top
    lip_t = (frame_t + lip_proud) if keyed else 6.0  # in the notch + proud of the face
    L1 = (F0[0] + lip_h * u[0], F0[1] + lip_h * u[1])  # lip top, back edge
    L2 = (L1[0] + lip_t * nf[0], L1[1] + lip_t * nf[1])  # lip top, front edge
    A = (B0[0] + rest_mm * u[0], B0[1] + rest_mm * u[1])  # back-rest top
    # tray COM: mid-plane tray_t/2 in front of the back face, TRAY_MM/2 up
    M = (B0[0] + tray_t / 2 * nf[0], B0[1] + tray_t / 2 * nf[1])
    com = (M[0] + TRAY_MM / 2 * u[0], M[1] + TRAY_MM / 2 * u[1])
    x_front, x_back = com[0] - length_mm / 2, com[0] + length_mm / 2
    # lip front face runs down (along -u) to the toe height
    t_down = (L2[1] - toe_h) / u[1]
    L3 = (L2[0] - t_down * u[0], toe_h)
    if L3[0] < x_front:  # the base can't stop short of the lip: give the rest to the back
        x_front = L3[0]
        x_back = x_front + length_mm
    x_back = max(x_back, A[0] + 5.0)
    pts = [(x_front, 0.0), (x_back, 0.0), (x_back, tail_h), A, B0, F0, L1, L2, L3,
           (x_front, toe_h)]
    g = Polygon(pts).buffer(0)
    g = max(_parts(g), key=lambda q: q.area)
    # convex NOSE instead of a free-standing lip finger (Alex: the hook
    # looked flimsy): a semicircle as tall as the lip top, flat side on the
    # tray's front face, hulled with the lip so the whole top-front is one
    # convex face -- trimmed only where the MDF channel and the frame (above
    # its notch) must stay clear
    R = L2[1] / 2
    far = 400.0
    P6 = (B0[0] + (w + frame_t) * nf[0], B0[1] + (w + frame_t) * nf[1])  # front-face line
    # a SEMIcircle: flat side upright on the tray's front face at half the
    # lip-top height, round side forward (a full circle stuck out too far)
    sgo = (R - P6[1]) / u[1]
    xd = P6[0] + sgo * u[0]
    circle = Point(xd, R).buffer(R, quad_segs=32).intersection(box(xd - R - 1, -1, xd, 2 * R + 1))
    cx = xd - R  # its front-most point
    lip = Polygon([F0, L1, L2, L3, (L3[0], 0.0), (F0[0], 0.0)]).buffer(0)
    channel = Polygon([B0, F0, (F0[0] + far * u[0], F0[1] + far * u[1]),
                       (B0[0] + far * u[0], B0[1] + far * u[1])])
    N0 = (F0[0] + (notch_depth - 0.5 if keyed else 0.0) * u[0],
          F0[1] + (notch_depth - 0.5 if keyed else 0.0) * u[1])  # frame starts here
    frame_zone = Polygon([N0, (N0[0] + (frame_t + clear) * nf[0], N0[1] + (frame_t + clear) * nf[1]),
                          (N0[0] + (frame_t + clear) * nf[0] + far * u[0],
                           N0[1] + (frame_t + clear) * nf[1] + far * u[1]),
                          (N0[0] + far * u[0], N0[1] + far * u[1])])
    toe = box(min(x_front, xd - R), 0.0, xd, toe_h)  # the hull sweeps down to the toe
    nose = unary_union([circle, lip, toe]).convex_hull.intersection(box(-far, 0, far, far))
    nose = nose.difference(channel).difference(frame_zone)
    g = max(_parts(unary_union([g, nose])), key=lambda q: q.area)
    ground_front = min(x for x, y in nose.exterior.coords if y < 0.5) if not nose.is_empty else x_front
    if ground_front < x_front:  # the nose's ground edge is the new front contact
        x_front = ground_front
    flat = g.intersection(box(x_front - 1, -1, x_back + 1, 0.6))
    g = unary_union([g.buffer(-1.0, join_style=1).buffer(1.0, join_style=1), flat])
    g = max(_parts(g), key=lambda q: q.area)
    info = dict(B0=B0, F0=F0, u=u, nf=nf, w=w, angle_deg=angle_deg, tray_t=tray_t, rest_mm=rest_mm,
                com=com, x_front=x_front, x_back=x_back, keyed=keyed,
                notch_depth=notch_depth, lip_h=lip_h, A=A, L1=L1)
    if hole:
        g, info["hole"] = _add_hole(g, info, wall_mm)
    return g, info


def _add_hole(g, info, wall_mm, min_d_mm=3.0, max_d_mm=16.0):
    """A row of round cut-outs down the foot's taper: the first is the
    biggest the body allows (wall_mm of wood to every edge, clear of the
    slot and the back rest's load path), and each one after it, wall_mm on,
    is as big as the body there allows but never bigger than the one before
    -- so they shrink steadily toward the thin tail. Returns (foot, [(x, y,
    r)] or None)."""
    keep_out = Polygon([info["B0"], info["F0"], info["L1"], info["A"]]).buffer(wall_mm)
    edge = g.exterior

    def fit(x):
        col = g.intersection(box(x - 0.05, -1, x + 0.05, 200))
        if col.is_empty:
            return None
        c = Point(x, (col.bounds[1] + col.bounds[3]) / 2)
        r = min(edge.distance(c) - wall_mm, keep_out.distance(c), max_d_mm / 2)
        return c, r

    x0, x1 = keep_out.bounds[0], g.bounds[2]
    scan = [f for f in (fit(x0 + 0.25 * k) for k in range(int((x1 - x0) / 0.25))) if f]
    if not scan:
        return g, None
    c, r = max(scan, key=lambda t: t[1])
    if 2 * r < min_d_mm:
        return g, None
    holes = [(c, r)]
    while True:
        last_c, last_r = holes[-1]
        best = None
        x = last_c.x + last_r + wall_mm
        while x < min(x1, last_c.x + 3 * last_r + wall_mm + 0.5):
            f = fit(x)
            if f:
                cc, rr = f
                rr = min(rr, last_r, cc.distance(last_c) - last_r - wall_mm)
                if best is None or rr > best[1] + 1e-6:
                    best = (cc, rr)
            x += 0.25
        if best is None or 2 * best[1] < min_d_mm:
            break
        holes.append(best)
    cuts = [cc.buffer(rr, quad_segs=32) for cc, rr in holes]
    return g.difference(unary_union(cuts)), [(cc.x, cc.y, rr) for cc, rr in holes]


def stability(info):
    """Tip-over tolerance (degrees) forward and back: how far the stand can
    be rocked before the tray's centre of mass passes a ground contact."""
    com = info["com"]
    back = math.degrees(math.atan2(info["x_back"] - com[0], com[1]))
    fwd = math.degrees(math.atan2(com[0] - info["x_front"], com[1]))
    return fwd, back


def nest(g, sheet_mm=STOCK_MM, margin_mm=5.0, gap_mm=2.0):
    """Feet in rows as head-to-tail PAIRS (a foot is roughly a wedge: one
    turned 180deg slides into the other's empty corner), plus a lone foot
    where a row has room. Returns placed polygons (sheet frame 0..sheet_mm)."""
    g = affinity.translate(g, -g.bounds[0], -g.bounds[1])
    r = affinity.rotate(g, 180, origin=(0, 0))
    r = affinity.translate(r, -r.bounds[0], -r.bounds[1])
    W, H = g.bounds[2], g.bounds[3]
    best = None
    for dy in [k * 0.5 for k in range(int(H * 2) + 1)]:
        lo, hi = 0.0, W + gap_mm  # bisect the slide-in distance
        for _ in range(30):
            mid = (lo + hi) / 2
            if affinity.translate(r, mid, -dy).distance(g) < gap_mm:
                lo = mid
            else:
                hi = mid
        pair = [g, affinity.translate(r, hi, -dy)]
        bx = (min(q.bounds[0] for q in pair), min(q.bounds[1] for q in pair),
              max(q.bounds[2] for q in pair), max(q.bounds[3] for q in pair))
        area = (bx[2] - bx[0]) * (bx[3] - bx[1])
        if best is None or area < best[0]:
            best = (area, pair, bx)
    _a, pair, bx = best
    pair = [affinity.translate(q, -bx[0], -bx[1]) for q in pair]
    PW, PH = bx[2] - bx[0], bx[3] - bx[1]
    usable = sheet_mm - margin_mm
    placed = []
    y = margin_mm
    while y + PH <= usable + 1e-9:
        x = margin_mm
        while x + PW <= usable + 1e-9:
            placed += [affinity.translate(q, x, y) for q in pair]
            x += PW + gap_mm
        if x + W <= usable:
            placed.append(affinity.translate(g, x, y))
        y += PH + gap_mm
    if y + H <= usable:
        x = margin_mm
        while x + W <= usable:
            placed.append(affinity.translate(g, x, y))
            x += W + gap_mm
    return placed


def best_foot(angle_deg, min_feet, rests=(18.0, 22.0, 26.0, 30.0), **kw):
    """Most stable foot that still nests min_feet on one sheet: searches
    foot length and back-rest height (the rest sets the foot's height, and
    so how many rows fit) for the largest worst-case tip tolerance
    (min of forward / back). Returns (foot, info, placed)."""
    best = None
    for rest in rests:
        for L10 in range(500, 2001, 25):
            L = L10 / 10
            g, info = foot(angle_deg, L, rest_mm=rest, **kw)
            placed = nest(g)
            if len(placed) < min_feet:
                continue
            score = (round(min(stability(info)), 1), rest, len(placed))
            if best is None or score > best[0]:
                best = (score, g, info, placed)
    return None if best is None else best[1:]


# ---------------------------------------------------------------------------
# frame notches (cut with the puzzle, in ITS machine frame: disc bbox 0..290,
# stock -5..295) -- the MDF back is never cut
# ---------------------------------------------------------------------------


def notch_positions(stance_mm=200.0):
    """Notch centres along an edge, measured from the edge's middle."""
    return (-stance_mm / 2, stance_mm / 2)


# Machine reach in the puzzle job's frame (disc bbox 0..290, stock -5..295):
# Alex confirmed -10mm X/Y is reachable; a 300mm job hit the Y hard stop at
# ~297mm, so nothing goes past 295.5.
REACH_MIN_MM, REACH_MAX_MM = -10.0, 295.5


def frame_notch_paths(depth_mm=12.0, width_mm=3.2, stance_mm=200.0, overrun_mm=5.0,
                      edges=(0,)):
    """U-shaped cut paths for notches on the stock's bottom edge (the
    X-axis edge, y = -5mm in the puzzle job's frame; edges 1/2/3 = right /
    top / left are available but off by default -- Alex: notches along X
    only). Each notch is width_mm wide (the foot's 3mm + fit), depth_mm
    deep, and its cut starts overrun_mm past the stock edge (y = -10 with
    the default 5mm, inside REACH_MIN_MM)."""
    c = DISC_MM / 2
    h = STOCK_MM / 2
    paths = []
    for k in edges:  # the bottom edge, rotated onto each requested edge
        for s in notch_positions(stance_mm):
            # bottom edge, then rotate by k*90 about the disc centre
            x0, x1 = c + s - width_mm / 2, c + s + width_mm / 2
            ye = c - h  # stock edge
            pts = [(x0, ye - overrun_mm), (x0, ye + depth_mm), (x1, ye + depth_mm),
                   (x1, ye - overrun_mm)]
            ln = affinity.rotate(LineString(pts), 90 * k, origin=(c, c))
            paths.append(list(ln.coords))
    # the frame band must stay deep enough above every notch
    band = h - math.sqrt(max(0.0, c ** 2 - (stance_mm / 2 + width_mm / 2) ** 2))
    return paths, band


def frame_notch_sections(depth_mm=12.0, width_mm=3.2, stance_mm=200.0, overrun_mm=5.0,
                         edge=3, section_mm=2.0):
    """The same notches as frame_notch_paths, cut in section_mm slices for
    running LAST in the puzzle job: deepest slice first, alternating between
    the two slots, working out to (and overrun_mm past) the stock edge. If
    the machine hits an end stop near the edge and aborts, only the outer
    slices are left, to finish with a file, saw or knife. Each slice is one
    path: down one side, across (chopping the waste plug), up the other; the
    first also closes the slot's end, and slices reaching the stock edge or
    beyond (the overrun, there for an imprecisely placed sheet) cut their
    two sides only -- a cross cut there would only burn the bed. edge: 0 bottom, 1 right, 2 top, 3 left (Alex's laser
    sits ~80mm -Y / ~20mm -X of the spindle, so the -X edge is the one it
    reaches)."""
    c = DISC_MM / 2
    ye = c - STOCK_MM / 2  # stock edge
    top, bottom = ye + depth_mm, ye - overrun_mm
    n = math.ceil((top - bottom) / section_mm - 1e-9)
    paths = []
    for k in range(n):
        hi = top - k * section_mm
        lo = max(bottom, hi - section_mm)
        for sx in notch_positions(stance_mm):
            x0, x1 = c + sx - width_mm / 2, c + sx + width_mm / 2
            if k == 0:
                runs = [[(x1, hi), (x0, hi), (x0, lo), (x1, lo), (x1, hi)]]
            elif lo > ye + 1e-6:
                runs = [[(x0, hi), (x0, lo), (x1, lo), (x1, hi)]]
            else:  # at/past the stock edge: no cross cut into the bed
                runs = [[(x0, hi), (x0, lo)], [(x1, lo), (x1, hi)]]
            for pts in runs:
                ln = affinity.rotate(LineString(pts), 90 * edge, origin=(c, c))
                paths.append([(round(x, 4), round(y, 4)) for x, y in ln.coords])
    return paths


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--angle", type=float, default=75.0, help="display angle, degrees from horizontal")
    ap.add_argument("--min-sets", type=int, default=16, help="stands per sheet, 2 feet each")
    ap.add_argument("--plain", action="store_true", help="v2 plain 6mm slot (no frame notches)")
    ap.add_argument("--notch-depth", type=float, default=12.0)
    ap.add_argument("--notch-w", type=float, default=3.2, help="frame notch width (foot 3mm + fit)")
    ap.add_argument("--stance", type=float, default=200.0, help="foot spacing along the tray edge")
    ap.add_argument("--mdf-clear", type=float, default=0.2, help="slot = MDF 3mm + this")
    ap.add_argument("--no-hole", action="store_true")
    ap.add_argument("--material", default="plywood_veneer_3ply_3mm")
    a = ap.parse_args()
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    import display_stand as DS
    from emitter import load_material

    material = load_material(a.material)
    kw = dict(keyed=not a.plain, notch_depth=a.notch_depth, clear=a.mdf_clear,
              hole=not a.no_hole)
    res = best_foot(a.angle, 2 * a.min_sets, **kw)
    if res is None:
        raise SystemExit("no foot nests that many -- lower --min-sets")
    g, info, feet = res
    fwd, back = stability(info)
    paths = []
    for f in feet:  # hole first, then outline (the part stays put while its hole is cut)
        paths += [list(r.coords) for r in f.interiors]
        paths.append(list(f.exterior.coords))
    gc = DS.emit_gcode(paths, material, f"stand feet, {len(feet)} up, {a.angle:.0f} deg")
    gc = gc.replace("display stand: 4 feet from the corner offcuts, then the frame outline (last)",
                    f"stand feet: {len(feet)} up ({len(feet) // 2} stands), "
                    f"tray at {a.angle:.0f} deg from horizontal, "
                    + ("keyed (frame-notch) slot" if info["keyed"] else "plain 6mm slot"))
    gc = gc.replace("SAME machine frame as the puzzle cut (disc bbox 0..290): run it on the same "
                    "sheet without moving it -- coordinates reach -5.5 / 295.5 (the stock edges)",
                    "its OWN 300mm sheet, origin at the sheet corner (0..300)")
    (out / "stand_feet.gcode").write_text(gc)
    if info["keyed"]:
        npaths, band = frame_notch_paths(a.notch_depth, a.notch_w, a.stance)
        ng = DS.emit_gcode(npaths, material, "frame notches for keyed stand feet")
        ng = ng.replace("display stand: 4 feet from the corner offcuts, then the frame outline (last)",
                        f"frame notches: 2 ({a.notch_w}mm wide, {a.notch_depth}mm deep, "
                        f"{a.stance:.0f}mm apart) along the bottom (X) stock edge, puzzle-job "
                        "frame (disc bbox 0..290, cut from y=-10) -- the MDF back is not cut")
        (out / "frame_notches.gcode").write_text(ng)
    _render(out, g, info, feet)
    b = g.bounds
    print(f"foot {b[2] - b[0]:.1f} x {b[3] - b[1]:.1f}mm "
          f"({'keyed: 3.2mm MDF channel + lip in a frame notch' if info['keyed'] else 'plain 6.2mm slot'}), "
          f"back rest {info['rest_mm']:.0f}mm up the tray, "
          f"{len(info.get('hole') or [])} round cut-outs "
          f"(diameters {', '.join(f'{2 * r:.1f}' for _x, _y, r in info.get('hole') or [])}mm)")
    print(f"tip tolerance at {a.angle:.0f} deg: {fwd:.1f} deg forward, {back:.1f} deg back")
    print(f"{len(feet)} feet on one 300mm sheet = {len(feet) // 2} stands -> {out}/stand_feet.gcode")
    if info["keyed"]:
        print(f"frame notches: {a.notch_w}mm x {a.notch_depth}mm, {a.stance:.0f}mm apart, bottom (X) edge only, cut from y=-10; "
              f"frame band there {band:.0f}mm deep -> {out}/frame_notches.gcode")


def _render(out, g, info, feet):
    from PIL import Image, ImageDraw

    k = 3.0
    img = Image.new("RGB", (int(300 * k), int(300 * k)), (235, 220, 190))
    d = ImageDraw.Draw(img)
    for f in feet:
        d.polygon([(x * k, (300 - y) * k) for x, y in f.exterior.coords], fill=(120, 160, 110),
                  outline=(200, 0, 0))
        for r in f.interiors:
            d.polygon([(x * k, (300 - y) * k) for x, y in r.coords], fill=(235, 220, 190),
                      outline=(200, 0, 0))
    d.text((8, 8), f"{len(feet)} feet ({len(feet) // 2} stands) on a 300mm sheet", fill=(0, 0, 0))
    img.save(out / "stand_feet_sheet.png")
    # side view, tray layers drawn separately (MDF back, frame front with the notch)
    k = 4.0
    x0 = info["x_front"] - 10
    W, Hh = int((info["x_back"] - x0 + 20) * k), int(130 * k)

    def Q(x, y):
        return ((x - x0) * k, Hh - (y + 10) * k)

    img = Image.new("RGB", (W, Hh), "white")
    d = ImageDraw.Draw(img)
    d.line([Q(x0, 0), Q(info["x_back"] + 20, 0)], fill=(0, 0, 0), width=2)
    d.polygon([Q(*p) for p in g.exterior.coords], fill=(120, 160, 110), outline=(0, 90, 0))
    for r in g.interiors:
        d.polygon([Q(*p) for p in r.coords], fill="white", outline=(0, 90, 0))
    B0, u, nf = info["B0"], info["u"], info["nf"]
    L = 120
    mdf = [B0, (B0[0] + L * u[0], B0[1] + L * u[1]),
           (B0[0] + L * u[0] + 3 * nf[0], B0[1] + L * u[1] + 3 * nf[1]),
           (B0[0] + 3 * nf[0], B0[1] + 3 * nf[1])]
    d.polygon([Q(*p) for p in mdf], fill=(150, 120, 90), outline=(90, 60, 30))
    z = info["notch_depth"] if info["keyed"] else 0.0  # frame starts above the notch here
    F = (B0[0] + 3 * nf[0], B0[1] + 3 * nf[1])
    frame = [(F[0] + z * u[0], F[1] + z * u[1]), (F[0] + L * u[0], F[1] + L * u[1]),
             (F[0] + L * u[0] + 3 * nf[0], F[1] + L * u[1] + 3 * nf[1]),
             (F[0] + z * u[0] + 3 * nf[0], F[1] + z * u[1] + 3 * nf[1])]
    d.polygon([Q(*p) for p in frame], fill=(214, 190, 150), outline=(120, 90, 50))
    com = info["com"]
    d.line([Q(com[0], 0), Q(com[0], 110)], fill=(200, 0, 0), width=1)
    fwd, back = stability(info)
    d.text((8, 8), f"{info['angle_deg']:.0f} deg: dark = MDF back (uncut), light = frame "
           f"({'notched over the lip' if info['keyed'] else 'plain'}); red = under the COM; "
           f"tips at {fwd:.0f} deg fwd / {back:.0f} deg back", fill=(0, 0, 0))
    img.save(out / "stand_feet_side.png")


if __name__ == "__main__":
    main()
