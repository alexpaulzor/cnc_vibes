#!/usr/bin/env python3
"""Display stand for a 290mm ring puzzle cut from 300mm square stock
(RING_SPEC.md §13.15): the leftover frame (300 square minus the 290 disc)
is glued to a 300x300 MDF back to make a tray the puzzle sits back into,
and FEET cut from the four corner offcuts hold that tray tilted back for
display.

Layout, in the puzzle G-code's own machine frame (disc bounding box at
0..290, so the stock is -5..295 and the job runs without moving the sheet):

  * frame outline: a circle of radius 145 + frame_w_mm, clipped by the
    stock edges -- the frame keeps a frame_w_mm ring round the puzzle (5mm
    at the four flats where the circle runs off the stock) and the four
    corners come away identically (4-fold symmetric, so the frame reads as
    a deliberate round frame, not a damaged square);
  * one foot per corner offcut (4 identical; 2 are enough, 4 is steadier).
    Each foot is the corner crescent stood on its two trimmed tips like an
    arch, apex up: its two straight stock edges form the roof, the frame
    circle its underside. A slot for the tray's bottom edge runs up into it
    at the display angle from in front of the apex, so the tray's back
    face rests on the slot's back wall all the way up to the apex (a long
    back rest, not just a notch) and the front wall stops it sliding off.

The foot's roof is the stock's own edge and its underside is the frame
cut, so neither is cut twice; only the slot, the trimmed tips and the lip
are foot-only cuts. Feet are cut first, the frame outline last (once the
corners are free nothing else can be cut on them).

Stability is checked, not assumed: the tray's centre of mass (it is a
uniform 300x300 plate, puzzle in) must project inside the feet's footprint
with margin; see stability(). Display angle = degrees from HORIZONTAL
(60 = leaning back 30 degrees from vertical).

    PYTHONPATH=. python3 scripts/display_stand.py OUTDIR [--angle 60]
"""
import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "quickcut"))

from shapely import affinity  # noqa: E402
from shapely.geometry import LineString, MultiLineString, Point, Polygon, box  # noqa: E402
from shapely.ops import linemerge, unary_union  # noqa: E402

STOCK_MM = 300.0
DISC_MM = 290.0


def _parts(g):
    if g.is_empty:
        return []
    return list(g.geoms) if hasattr(g, "geoms") else [g]


def foot_profile(angle_deg=60.0, frame_w_mm=7.0, tray_t_mm=6.0, slot_clear_mm=0.2,
                 wall_mm=5.0, tip_mm=6.0):
    """One foot in its standing frame: x = front(-) .. back(+), y = up, ground
    at y = 0. Returns (polygon, info). The crescent is the corner offcut
    rotated apex-up (stock centre at (0, -y_ground)); the slot's back wall
    is the display-angle line through the apex."""
    R_o = DISC_MM / 2 + frame_w_mm
    h = STOCK_MM / 2
    apex_y = h * math.sqrt(2)  # stock corner, rotated onto the +y axis
    roof = Polygon([(0, apex_y), (apex_y, 0), (0, -apex_y), (-apex_y, 0)])  # the stock square
    cres = roof.difference(Point(0, 0).buffer(R_o, quad_segs=256))
    cres = max(_parts(cres), key=lambda g: g.centroid.y)  # the corner on +y
    # trim the tips where the crescent gets thinner than tip_mm
    xt = next(x / 10 for x in range(0, int(apex_y * 10))
              if (apex_y - x / 10) - math.sqrt(max(0.0, R_o ** 2 - (x / 10) ** 2)) < tip_mm)
    y_ground = math.sqrt(R_o ** 2 - xt ** 2)
    foot = cres.intersection(box(-apex_y, y_ground, apex_y, apex_y + 1))
    foot = affinity.translate(foot, 0, -y_ground)
    H = apex_y - y_ground
    th = math.radians(angle_deg)
    u = (math.cos(th), math.sin(th))  # up the tray, toward the back
    nf = (-math.sin(th), math.cos(th))  # tray normal, toward the front
    w = tray_t_mm + slot_clear_mm
    A = (0.0, H)
    # deepest slot whose bottom corners keep wall_mm of foot below/around them
    best = None
    for k in range(1, 4000):
        t = k * 0.1
        B0 = (A[0] - t * u[0], A[1] - t * u[1])
        F0 = (B0[0] + w * nf[0], B0[1] + w * nf[1])
        ok = all(foot.contains(Point(p)) and foot.exterior.distance(Point(p)) >= wall_mm
                 for p in (B0, F0))
        if ok:
            best = (B0, F0)
        elif best is not None:
            break
    if best is None:
        raise SystemExit("no slot fits this foot -- larger corner (smaller frame_w_mm) or angle")
    B0, F0 = best
    far = 400.0
    slot = Polygon([B0, F0, (F0[0] + far * u[0], F0[1] + far * u[1]),
                    (B0[0] + far * u[0], B0[1] + far * u[1])])
    f = foot.difference(slot)
    # drop the knife-edge of the lip (between the 45deg roof and the slot)
    r = wall_mm / 2
    f = unary_union([f.buffer(-r, join_style=1).buffer(r, join_style=1).intersection(f),
                     f.intersection(box(-H * 2, -1, H * 2, tip_mm + 2))])  # keep the pads square
    f = max(_parts(f), key=lambda g: g.area)
    info = dict(R_o=R_o, y_ground=y_ground, H=H, B0=B0, F0=F0, u=u, nf=nf, w=w,
                angle_deg=angle_deg, tray_t_mm=tray_t_mm, wall_mm=wall_mm)
    return f, info


def stability(foot, info, tray_mm=STOCK_MM):
    """The tray's centre of mass vs the feet's ground contact. The tray's
    bottom face sits on the slot bottom, its back face on the back wall;
    its COM is tray_mm/2 up its mid-plane. Returns (com, x_front, x_back,
    margin_back): margin_back = how far behind the COM the rear ground
    contact is (must be well > 0 -- it is what stops it tipping back)."""
    B0, u, nf, t = info["B0"], info["u"], info["nf"], info["tray_t_mm"]
    M = (B0[0] + t / 2 * nf[0], B0[1] + t / 2 * nf[1])
    com = (M[0] + tray_mm / 2 * u[0], M[1] + tray_mm / 2 * u[1])
    ground = [x for x, y in foot.exterior.coords if y < 0.5]
    return com, min(ground), max(ground), max(ground) - com[0]


def stand_layout(foot, info):
    """Feet + frame outline in the puzzle's machine frame (mm, y up; disc
    centre at (145, 145), stock -5..295). Returns (feet polygons, frame
    polygon, corner regions)."""
    c = DISC_MM / 2
    h = STOCK_MM / 2
    stock = box(c - h, c - h, c + h, c + h)
    feet = []
    for k in range(4):
        g = affinity.translate(foot, 0, info["y_ground"])  # back to stock-centre origin
        g = affinity.rotate(g, -45 + 90 * k, origin=(0, 0))  # apex onto a corner
        feet.append(affinity.translate(g, c, c))
    frame_out = stock.intersection(Point(c, c).buffer(info["R_o"], quad_segs=256))
    corners = _parts(stock.difference(frame_out))
    return feet, frame_out, stock, corners


def cut_paths(feet, frame_out, stock):
    """Polylines to cut (mm): each foot's outline minus what the stock edge
    and the frame cut already give it, then the frame outline arcs (run
    0.5mm past the stock edge so the corners come away cleanly)."""
    edge = stock.exterior.buffer(0.05)
    arc_line = frame_out.exterior.difference(stock.exterior.buffer(0.05))
    arc_zone = arc_line.buffer(0.05)
    paths = []
    for f in feet:
        ln = LineString(f.exterior.coords).difference(edge).difference(arc_zone)
        paths += [list(g.coords) for g in _parts(linemerge(ln) if ln.geom_type == "MultiLineString" else ln)
                  if g.geom_type == "LineString" and g.length > 0.5]
    arcs = _parts(linemerge(arc_line) if arc_line.geom_type == "MultiLineString" else arc_line)
    for a in arcs:
        pts = list(a.coords)
        for end in (0, -1):
            p, q = pts[end], pts[1 if end == 0 else -2]
            d = math.dist(p, q) or 1.0
            ext = (p[0] + (p[0] - q[0]) / d * 0.5, p[1] + (p[1] - q[1]) / d * 0.5)
            if end == 0:
                pts.insert(0, ext)
            else:
                pts.append(ext)
        paths.append(pts)
    n_feet_paths = len(paths) - len(arcs)
    return paths, n_feet_paths


def emit_gcode(paths, material, title, notes=None):
    from emitter import WARMUP_MS, _header
    from motion import decimate, warmup_wiggle

    laser = material["laser"]
    feed = laser["feed_mm_per_min"]
    s = int(round(laser["power_percent"] * 10))
    lead = WARMUP_MS / 1000 * feed / 60
    lines = _header(title=title, material_id=material["id"], extra=(notes or [
        "display stand: 4 feet from the corner offcuts, then the frame outline (last)",
        "SAME machine frame as the puzzle cut (disc bbox 0..290): run it on the same "
        "sheet without moving it -- coordinates reach -5.5 / 295.5 (the stock edges)",
    ]) + [f"warmup: {WARMUP_MS:.0f}ms = {lead:.1f}mm fwd half / back to start"])
    for i, pts in enumerate(paths, 1):
        pts = decimate(pts, 0.3)
        if len(pts) < 2:
            continue
        warm = warmup_wiggle(pts, lead)
        lines += [f"; --- stand path {i}/{len(paths)} ---", f"G0 X{pts[0][0]:.3f} Y{pts[0][1]:.3f}",
                  f"M3 S{s}", f"F{feed}"]
        for _p in range(laser["passes"]):
            lines += [f"G1 X{x:.3f} Y{y:.3f}" for x, y in warm + pts[1:]]
            warm = []
            pts = pts[::-1]
        lines += ["M5", ""]
    lines += ["G0 X0 Y0", ""]
    return "\n".join(lines)


def render(out_dir, foot, info, feet, frame_out, stock, paths, stab):
    from PIL import Image, ImageDraw, ImageFont

    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 18)
    except OSError:
        font = ImageFont.load_default()
    # --- sheet layout (top view) -------------------------------------------
    k = 3.0
    W = int(320 * k)

    def P(x, y):
        return ((x + 15) * k, W - (y + 15) * k)

    img = Image.new("RGB", (W, W), "white")
    d = ImageDraw.Draw(img)
    d.polygon([P(*p) for p in stock.exterior.coords], fill=(235, 220, 190))
    d.polygon([P(*p) for p in frame_out.exterior.coords], fill=(214, 190, 150))
    c = DISC_MM / 2
    disc = Point(c, c).buffer(c, quad_segs=128)
    d.polygon([P(*p) for p in disc.exterior.coords], fill=(245, 245, 245), outline=(150, 150, 150))
    for f in feet:
        d.polygon([P(*p) for p in f.exterior.coords], fill=(120, 160, 110))
    for pts in paths:
        d.line([P(*p) for p in pts], fill=(220, 0, 0), width=2)
    d.text((10, 10), "display stand: 4 feet (green) from the corners; red = cut; "
           "frame keeps a ring round the disc", fill=(0, 0, 0), font=font)
    img.save(out_dir / "stand_layout.png")
    # --- side view -------------------------------------------------------------
    k = 3.0
    com, xf, xb, margin = stab
    W, Hh = int(330 * k), int(300 * k)

    def Q(x, y):
        return ((x + 110) * k, Hh - (y + 20) * k)

    img = Image.new("RGB", (W, Hh), "white")
    d = ImageDraw.Draw(img)
    d.line([Q(-110, 0), Q(220, 0)], fill=(0, 0, 0), width=2)
    d.polygon([Q(*p) for p in foot.exterior.coords], fill=(120, 160, 110), outline=(0, 90, 0))
    B0, F0, u, t = info["B0"], info["F0"], info["u"], info["tray_t_mm"]
    L = STOCK_MM
    tray = [B0, (B0[0] + L * u[0], B0[1] + L * u[1]),
            (F0[0] + L * u[0], F0[1] + L * u[1]), F0]
    nf = info["nf"]
    tray = [B0, (B0[0] + L * u[0], B0[1] + L * u[1]),
            (B0[0] + L * u[0] + t * nf[0], B0[1] + L * u[1] + t * nf[1]),
            (B0[0] + t * nf[0], B0[1] + t * nf[1])]
    d.polygon([Q(*p) for p in tray], fill=(214, 190, 150), outline=(120, 90, 50))
    d.ellipse([Q(com[0], com[1])[0] - 6, Q(com[0], com[1])[1] - 6,
               Q(com[0], com[1])[0] + 6, Q(com[0], com[1])[1] + 6], fill=(200, 0, 0))
    d.line([Q(com[0], com[1]), Q(com[0], 0)], fill=(200, 0, 0), width=1)
    d.text((10, 10), f"side view, tray at {info['angle_deg']:.0f} deg from horizontal; "
           f"COM (red) {margin:.0f}mm in front of the rear foot contact",
           fill=(0, 0, 0), font=font)
    img.save(out_dir / "stand_side.png")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--angle", type=float, default=60.0, help="display angle, degrees from horizontal")
    ap.add_argument("--frame-w-mm", type=float, default=7.0,
                    help="frame ring width round the disc (corners beyond it become feet)")
    ap.add_argument("--tray-t-mm", type=float, default=6.0, help="tray thickness: 3mm frame + 3mm MDF")
    ap.add_argument("--slot-clear-mm", type=float, default=0.2,
                    help="slot width = tray + this (the kerf adds ~0.15mm more)")
    ap.add_argument("--material", default="plywood_veneer_3ply_3mm")
    a = ap.parse_args()
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    foot, info = foot_profile(a.angle, a.frame_w_mm, a.tray_t_mm, a.slot_clear_mm)
    stab = stability(foot, info)
    com, xf, xb, margin = stab
    feet, frame_out, stock, _corners = stand_layout(foot, info)
    paths, n_fp = cut_paths(feet, frame_out, stock)
    from emitter import load_material

    g = emit_gcode(paths, load_material(a.material), f"display stand, {a.angle:.0f} deg")
    (out / "stand.gcode").write_text(g)
    render(out, foot, info, feet, frame_out, stock, paths, stab)
    xs = [float(t[1:]) for ln in g.splitlines() if ln.startswith(("G0", "G1"))
          for t in ln.split() if t.startswith("X")]
    ys = [float(t[1:]) for ln in g.splitlines() if ln.startswith(("G0", "G1"))
          for t in ln.split() if t.startswith("Y")]
    bx = foot.bounds
    print(f"foot: {bx[2] - bx[0]:.1f} x {bx[3] - bx[1]:.1f}mm (footprint {xb - xf:.1f}mm), "
          f"slot {info['w']:.1f}mm wide at {a.angle:.0f} deg, slot bottom "
          f"{min(info['B0'][1], info['F0'][1]):.1f}mm up; frame ring {a.frame_w_mm}mm "
          f"(R {info['R_o']:.0f}mm)")
    print(f"tray COM at x={com[0]:.1f}, y={com[1]:.1f}mm; ground contact x {xf:.1f}..{xb:.1f}; "
          f"COM is {margin:.1f}mm in front of the rear contact")
    print(f"{len(paths)} cut paths ({n_fp} feet, {len(paths) - n_fp} frame arcs); "
          f"G-code X {min(xs):.1f}..{max(xs):.1f}, Y {min(ys):.1f}..{max(ys):.1f} -> {out}/stand.gcode")
    if margin < 15:
        print("WARNING: less than 15mm tipping margin -- lower the angle or widen the corners")


if __name__ == "__main__":
    main()
