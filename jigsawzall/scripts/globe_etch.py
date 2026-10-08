#!/usr/bin/env python3
"""Orthographic-projected globe line art: coastlines + lat/lon graticule,
clipped to the visible hemisphere, for etching as a background pattern on
the ring puzzle (see RING_SPEC.md). Not wired into ring_prototype.py yet --
this is the standalone prototype for checking projection/visual quality.

Coastline source: GSHHS crude resolution, bundled by the Debian package
python-cartopy-data at /usr/share/cartopy/data/shapefiles/gshhs/c/. That's
a real dependency for regenerating this data --  the traced polylines
themselves, once generated, don't need it again.
"""
import json
import math
import sys
from pathlib import Path

GSHHS_PATH = "/usr/share/cartopy/data/shapefiles/gshhs/c/GSHHS_c_L1.dbf"
CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "gshhs_coastlines_crude.json"


def load_coastlines():
    """Returns a list of rings, each a list of (lon, lat) in degrees.

    Prefers the bundled cache (data/gshhs_coastlines_crude.json, ~140KB,
    committed to the repo) so this doesn't need geopandas + the
    python-cartopy-data apt package installed every time -- those are only
    needed to regenerate the cache itself (see __main__ below, `--refresh-
    cache`), e.g. if a finer resolution or a different source is wanted
    later."""
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text())
    import geopandas as gpd

    gdf = gpd.read_file(GSHHS_PATH, on_invalid="fix")
    rings = []
    for geom in gdf.geometry:
        polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        for p in polys:
            rings.append(list(p.exterior.coords))
    return rings


def refresh_cache():
    """Regenerate data/gshhs_coastlines_crude.json from the system GSHHS
    shapefile (apt install python-cartopy-data; pip install geopandas)."""
    import geopandas as gpd

    gdf = gpd.read_file(GSHHS_PATH, on_invalid="fix")
    rings = []
    for geom in gdf.geometry:
        polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        for p in polys:
            rings.append([[round(x, 3), round(y, 3)] for x, y in p.exterior.coords])
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(rings))
    print(f"wrote {CACHE_PATH}, {len(rings)} rings")


def _ortho(lon, lat, lon0, lat0):
    """Orthographic projection, unit sphere. Returns (x, y, cosc) -- x,y in
    [-1,1], cosc = cos(angular distance from view center); cosc<0 = far side
    (not visible)."""
    lon, lat, lon0, lat0 = map(math.radians, (lon, lat, lon0, lat0))
    cosc = math.sin(lat0) * math.sin(lat) + math.cos(lat0) * math.cos(lat) * math.cos(lon - lon0)
    x = math.cos(lat) * math.sin(lon - lon0)
    y = math.cos(lat0) * math.sin(lat) - math.sin(lat0) * math.cos(lat) * math.cos(lon - lon0)
    return x, y, cosc


def _densify(ring, max_deg=2.0):
    """Insert extra points along long lon/lat spans so horizon clipping
    doesn't skip a visibility flip between two far-apart vertices."""
    out = []
    for i in range(len(ring) - 1):
        a, b = ring[i], ring[i + 1]
        d = max(abs(b[0] - a[0]), abs(b[1] - a[1]))
        n = max(1, int(d / max_deg))
        for k in range(n):
            t = k / n
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    out.append(ring[-1])
    return out


def project_ring(ring, lon0, lat0, R, cx, cy, densify_deg=2.0):
    """One coastline ring -> a list of visible-hemisphere strokes (each a
    list of (x,y) in the SAME px frame as cx,cy,R), split wherever the ring
    crosses the horizon."""
    pts = _densify(ring, densify_deg)
    strokes = []
    cur = []
    for lon, lat in pts:
        x, y, cosc = _ortho(lon, lat, lon0, lat0)
        if cosc >= 0:
            cur.append((cx + x * R, cy - y * R))
        else:
            if len(cur) >= 2:
                strokes.append(cur)
            cur = []
    if len(cur) >= 2:
        strokes.append(cur)
    return strokes


def graticule(lon0, lat0, R, cx, cy, lon_step=30, lat_step=30, lat_max=80):
    """Meridian + parallel arcs as (x,y) strokes, same convention as
    project_ring. Includes the horizon (limb) circle itself."""
    strokes = []
    for lon in range(-180, 180, lon_step):
        ring = [(lon, lat) for lat in range(-90, 91, 2)]
        strokes += project_ring(ring, lon0, lat0, R, cx, cy, densify_deg=4)
    for lat in range(-lat_max, lat_max + 1, lat_step):
        ring = [(lon, lat) for lon in range(-180, 181, 2)]
        strokes += project_ring(ring, lon0, lat0, R, cx, cy, densify_deg=4)
    # limb (horizon circle)
    limb = [(cx + R * math.cos(t), cy + R * math.sin(t)) for t in
            [2 * math.pi * k / 180 for k in range(181)]]
    strokes.append(limb)
    return strokes


def build_globe(lon0, lat0, R, cx, cy, coastlines=None):
    coastlines = coastlines if coastlines is not None else load_coastlines()
    strokes = []
    for ring in coastlines:
        strokes += project_ring(ring, lon0, lat0, R, cx, cy)
    strokes += graticule(lon0, lat0, R, cx, cy)
    return strokes


def ensure_full_coverage(pieces, strokes):
    """The whole point of an etched design as an orientation mark is that
    EVERY piece carries some trace of it -- a sparse line-art pattern (a
    world map's graticule + coastlines, or any other design) can still miss
    an occasional piece by chance, landing entirely in a gap between lines.
    Returns extra strokes: one short tick through the centroid of any
    piece, along its own longest axis (so it reads as a plausible
    continuation of nearby line-work, not a random mark), for every piece
    the given strokes don't already cross. Generic -- not globe-specific."""
    import math
    import warnings

    from shapely.geometry import LineString
    from shapely.ops import unary_union

    stroke_lines = [LineString(s) for s in strokes if len(s) >= 2]
    covered = unary_union(stroke_lines) if stroke_lines else None
    extra = []
    for p in pieces:
        poly = p["polygon"]
        if covered is not None and poly.intersects(covered):
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            mrr = poly.minimum_rotated_rectangle
        xs, ys = mrr.exterior.coords.xy
        edges = [
            (math.hypot(xs[i + 1] - xs[i], ys[i + 1] - ys[i]), i) for i in range(2)
        ]
        length, i = max(edges)
        ang = math.atan2(ys[i + 1] - ys[i], xs[i + 1] - xs[i])
        c = poly.centroid
        half = 0.3 * length
        extra.append([
            (c.x - half * math.cos(ang), c.y - half * math.sin(ang)),
            (c.x + half * math.cos(ang), c.y + half * math.sin(ang)),
        ])
    return extra


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--refresh-cache":
        refresh_cache()
        sys.exit(0)

    from PIL import Image, ImageDraw

    lon0 = float(sys.argv[1]) if len(sys.argv) > 1 else -122.4  # San Francisco
    lat0 = float(sys.argv[2]) if len(sys.argv) > 2 else 37.7
    out = sys.argv[3] if len(sys.argv) > 3 else "/tmp/globe_test.png"

    coastlines = load_coastlines()
    W = H = 1000
    cx = cy = W / 2
    R = W * 0.46
    strokes = build_globe(lon0, lat0, R, cx, cy, coastlines)
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    for s in strokes:
        if len(s) >= 2:
            d.line(s, fill="black", width=1)
    img.save(out)
    print(f"saved {out}, {len(strokes)} strokes, centered lon={lon0} lat={lat0}")
