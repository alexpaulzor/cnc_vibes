#!/usr/bin/env python3
"""Perspective renders of a finished puzzle standing on its keyed feet
(RING_SPEC.md §13.18): MDF back, notched veneer frame, the puzzle's pieces
in the recess, two feet keyed into the frame's bottom notches.

    PYTHONPATH=. python3 scripts/stand_views.py OUTDIR [PIECES.pkl]

PIECES.pkl is a planet290.py `sN_pieces.pkl` (the puzzle face is drawn
from its pieces); without it the recess shows a plain disc.
"""
import math
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import shapely  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from shapely.geometry import Point, Polygon, box  # noqa: E402

import stand_feet_sheet as SF  # noqa: E402

LIGHT = (-0.35, -0.75, 0.55)  # from the front-left, above
_ln = math.sqrt(sum(c * c for c in LIGHT))
LIGHT = tuple(c / _ln for c in LIGHT)


def _tris(poly):
    """Constrained triangulation of a polygon (holes respected)."""
    tri = shapely.constrained_delaunay_triangles(poly)
    return [list(t.exterior.coords)[:3] for t in tri.geoms]


def _shade(face, rgb):
    (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) = face[:3]
    ux, uy, uz = x1 - x0, y1 - y0, z1 - z0
    vx, vy, vz = x2 - x0, y2 - y0, z2 - z0
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    n = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    k = abs((nx * LIGHT[0] + ny * LIGHT[1] + nz * LIGHT[2]) / n)
    f = 0.5 + 0.5 * k
    return tuple(min(1.0, c * f) for c in rgb)


class Scene:
    def __init__(self):
        self.faces, self.colors, self.lines, self.line_colors = [], [], [], []

    def add(self, face, rgb):
        self.faces.append(face)
        self.colors.append(_shade(face, rgb))

    def slab(self, poly, to3d, n0, n1, rgb, sides=True):
        """Extrude a 2D polygon between two offsets; to3d(a, b, n) -> xyz."""
        for t in _tris(poly):
            for n in (n0, n1):
                self.add([to3d(a, b, n) for a, b in t], rgb)
        if sides:
            for ring in [poly.exterior] + list(poly.interiors):
                c = list(ring.coords)
                for (a0, b0), (a1, b1) in zip(c, c[1:]):
                    self.add([to3d(a0, b0, n0), to3d(a1, b1, n0), to3d(a1, b1, n1),
                              to3d(a0, b0, n1)], rgb)


def build(pieces_pkl=None, angle=75.0):
    g, info = SF.best_foot(angle, 32)[:2]
    B0, u, nf = info["B0"], info["u"], info["nf"]
    half = SF.TRAY_MM / 2
    stance = 200.0
    notch_d, notch_w = info["notch_depth"], 3.2

    def tray(s, t, n):  # s across the tray, t up it from the bottom edge, n out of the back
        return (s, B0[0] + t * u[0] + n * nf[0], B0[1] + t * u[1] + n * nf[1])

    sc = Scene()
    # MDF back, split into tiles so the depth sort behaves
    for i in range(6):
        for j in range(6):
            tile = box(-half + i * 50, j * 50, -half + (i + 1) * 50, (j + 1) * 50)
            sc.slab(tile, lambda a, b, n: tray(a, b, n), 0.0, 3.0, (0.55, 0.42, 0.30),
                    sides=(i in (0, 5) or j in (0, 5)))
    # veneer frame: square minus the disc minus the notches (all four edges)
    frame = box(-half, 0, half, 2 * half).difference(Point(0, half).buffer(145, quad_segs=48))
    for k in range(4):
        for sx in (-stance / 2, stance / 2):
            nb = box(sx - notch_w / 2, -1, sx + notch_w / 2, notch_d)
            frame = frame.difference(shapely.affinity.rotate(nb, 90 * k, origin=(0, half)))
    for q in SF._parts(frame):
        sc.slab(q, lambda a, b, n: tray(a, b, n), 3.0, 6.0, (0.86, 0.75, 0.56))
    # puzzle face in the recess
    if pieces_pkl:
        pieces, cfg, _L, _st, _panel, C = pickle.loads(Path(pieces_pkl).read_bytes())
        ppm = cfg.px_per_mm
        cols = {"land": (0.55, 0.66, 0.38), "letter": (0.78, 0.33, 0.33), "cell": (0.62, 0.80, 0.86)}

        def pz(a, b, n):
            return tray((a - C[0]) / ppm, half - (b - C[1]) / ppm, n)

        for p in pieces:
            for q in SF._parts(p["polygon"]):
                q = q.simplify(0.4 * ppm)
                if not isinstance(q, Polygon) or q.is_empty:
                    continue
                for t in _tris(q):
                    sc.add([pz(a, b, 6.0) for a, b in t], cols.get(p["kind"], (0.8, 0.8, 0.8)))
                for ring in [q.exterior] + list(q.interiors):
                    c = [pz(a, b, 6.08) for a, b in ring.coords]
                    sc.lines += list(zip(c, c[1:]))
    else:
        disc = Point(0, half).buffer(145, quad_segs=48)
        for t in _tris(disc):
            sc.add([tray(a, b, 6.0) for a, b in t], (0.85, 0.78, 0.62))
    # the two feet, keyed into the bottom notches
    for sx in (-stance / 2, stance / 2):
        for q in SF._parts(g):
            sc.slab(q, lambda a, b, n, sx=sx: (sx + n, a, b), -1.5, 1.5, (0.42, 0.62, 0.40))
    # a bit of table
    return sc, info


def _camera(eye, target, fov_deg, W, H):
    eye, target = np.array(eye, float), np.array(target, float)
    f = target - eye
    f /= np.linalg.norm(f)
    r = np.cross(f, (0, 0, 1.0))
    r /= np.linalg.norm(r)
    up = np.cross(r, f)
    foc = (W / 2) / math.tan(math.radians(fov_deg) / 2)

    def proj(P):  # (N,3) -> screen x, y, depth
        d = np.asarray(P, float) - eye
        cx, cy, cz = d @ r, d @ up, d @ f
        return W / 2 + foc * cx / cz, H / 2 - foc * cy / cz, cz

    return proj


def render(sc, out, eye, target, title, fov=32.0, lines=True, W=1400, H=1100, ss=2):
    """Z-buffered flat-shaded raster of the scene (ss x supersampled)."""
    Ws, Hs = W * ss, H * ss
    proj = _camera(eye, target, fov, Ws, Hs)
    zbuf = np.full((Hs, Ws), np.inf)
    img = np.ones((Hs, Ws, 3))
    ground = np.array([[x, y, 0.0] for x in (-400, 400) for y in (-300, 500)])
    for face, col in zip(sc.faces, sc.colors):
        P = np.array(face, float)
        X, Y, Z = proj(P)
        if (Z <= 1).any():
            continue
        for k in range(1, len(P) - 1):  # fan-triangulate quads
            xs, ys, zs = X[[0, k, k + 1]], Y[[0, k, k + 1]], Z[[0, k, k + 1]]
            x0, x1 = int(max(0, math.floor(xs.min()))), int(min(Ws - 1, math.ceil(xs.max())))
            y0, y1 = int(max(0, math.floor(ys.min()))), int(min(Hs - 1, math.ceil(ys.max())))
            if x1 < x0 or y1 < y0:
                continue
            gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
            (ax_, bx_, cx_), (ay_, by_, cy_) = xs, ys
            den = (by_ - cy_) * (ax_ - cx_) + (cx_ - bx_) * (ay_ - cy_)
            if abs(den) < 1e-9:
                continue
            w0 = ((by_ - cy_) * (gx - cx_) + (cx_ - bx_) * (gy - cy_)) / den
            w1 = ((cy_ - ay_) * (gx - cx_) + (ax_ - cx_) * (gy - cy_)) / den
            w2 = 1 - w0 - w1
            m = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
            if not m.any():
                continue
            iz = w0 / zs[0] + w1 / zs[1] + w2 / zs[2]  # perspective-correct depth
            z = 1 / np.where(iz > 0, iz, 1e-12)
            sub = zbuf[y0:y1 + 1, x0:x1 + 1]
            upd = m & (z < sub)
            sub[upd] = z[upd]
            img[y0:y1 + 1, x0:x1 + 1][upd] = col
    if lines:
        for (p0, p1) in sc.lines:
            n = max(2, int(math.dist(p0, p1) * ss * 1.5))
            P = np.linspace(p0, p1, n)
            X, Y, Z = proj(P)
            xi, yi = X.astype(int), Y.astype(int)
            ok = (xi >= 0) & (xi < Ws) & (yi >= 0) & (yi < Hs) & (Z > 1)
            xi, yi, Z = xi[ok], yi[ok], Z[ok]
            vis = Z <= zbuf[yi, xi] + 1.5
            for dx in range(ss):
                xx = np.clip(xi[vis] + dx, 0, Ws - 1)
                img[yi[vis], xx] = (0.22, 0.12, 0.10)
    # soft ground: tint pixels nothing covered below the horizon
    pic = Image.fromarray((img * 255).astype(np.uint8)).resize((W, H), Image.LANCZOS)
    d = ImageDraw.Draw(pic)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 18)
    except OSError:
        font = ImageFont.load_default()
    d.text((14, 12), title, fill=(30, 30, 30), font=font)
    pic.save(out)


def _table(sc):
    """A table top under the stand, tiled for clean depth tests."""
    for i in range(-5, 5):
        for j in range(-3, 6):
            x0, y0 = i * 40, j * 40
            col = (0.80, 0.80, 0.78) if (i + j) % 2 == 0 else (0.76, 0.76, 0.74)
            sc.faces.append([(x0, y0, -0.2), (x0 + 40, y0, -0.2), (x0 + 40, y0 + 40, -0.2), (x0, y0 + 40, -0.2)])
            sc.colors.append(col)


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    pk = sys.argv[2] if len(sys.argv) > 2 else None
    sc, info = build(pk)
    _table(sc)
    a = info["angle_deg"]
    mid = (0, 50, 135)
    render(sc, out / "view_front34.png", (-560, -700, 380), mid,
           f"front three-quarter: tray at {a:.0f} deg on two keyed feet", fov=34)
    render(sc, out / "view_side.png", (-1800, 40, 140), (0, 40, 138),
           "side (long lens): the lean, and the foot's reach in front of and behind the tray", fov=14)
    render(sc, out / "view_rear34.png", (620, 760, 420), mid,
           "rear three-quarter: both feet, MDF back uncut", lines=False, fov=34)
    render(sc, out / "view_front_low.png", (-150, -620, 60), (0, 30, 110),
           "front, low: two feet 200mm apart, lips keyed into the frame notches", fov=40)
    render(sc, out / "view_foot_closeup.png", (215, -95, 70), (100, 15, 12),
           "close-up: the lip keyed up into the frame notch, the MDF in its own channel", fov=34)
    print("->", out)


if __name__ == "__main__":
    main()
