#!/usr/bin/env python3
"""Globe LAND as cut pieces (RING_SPEC.md §13.13): countries projected onto
the same orthographic globe as globe_etch.py, clipped to the visible
hemisphere, then cleaned into pieces that survive 3mm veneer -- no tabs,
they drop into pockets in the ocean pieces the way letters do.

Country source: Natural Earth 1:110m admin-0 countries (public domain),
cached as data/ne_110m_countries.json so nothing needs downloading again.
Regenerate the cache with `globe_land.py --refresh-cache GEOJSON` from
ne_110m_admin_0_countries.geojson (github.com/nvkelso/natural-earth-vector).

Pipeline (land_pieces):
  1. project every country polygon, clip to the globe disc, subtract the
     letters (they cut their own pockets into the land, like into wood);
  2. morphological clean-up of the WHOLE land mass against the letters:
     close (narrow straits / land-letter gaps under min_feature_mm become
     land) and open (land necks / spits under min_feature_mm become ocean);
  3. split the cleaned land back into countries, fold sub-min_feature bits
     and too-small countries into their neighbour with the longest border;
  4. split anything oversized along a projected meridian / parallel through
     it (snapped to a 5-degree graticule line when one balances the split);
  5. drop islands too small to cut; flag pieces under bridge_mm for
     breakaway bridges (done by the caller).
"""
import json
import math
import sys
import warnings
from pathlib import Path

from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import split, unary_union

CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "ne_110m_countries.json"


def refresh_cache(geojson_path):
    """Natural Earth admin-0 GeoJSON -> data/ne_110m_countries.json:
    [{"name", "a3", "polys": [[exterior, hole, ...], ...]}], lon/lat rounded
    to 0.01 degree (~25 microns on a 290mm globe -- far below the kerf)."""
    d = json.loads(Path(geojson_path).read_text())
    out = []
    for f in d["features"]:
        g, p = f["geometry"], f["properties"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        out.append({
            "name": p["NAME"],
            "a3": p["ADM0_A3"],
            "polys": [[[[round(x, 2), round(y, 2)] for x, y in ring] for ring in poly]
                      for poly in polys],
        })
    CACHE_PATH.write_text(json.dumps(out, separators=(",", ":")))
    print(f"wrote {CACHE_PATH}, {len(out)} countries")


def load_countries():
    return json.loads(CACHE_PATH.read_text())


# ---------------------------------------------------------------------------
# projection: clip on the sphere via the azimuthal-equidistant plane (the
# visible hemisphere is exactly the disc rho <= pi/2 there, and the map is
# continuous up to the antipode), then squash rho -> sin(rho) = orthographic.
# ---------------------------------------------------------------------------


def _aeqd(lon, lat, lon0, lat0):
    lon, lat, lon0, lat0 = map(math.radians, (lon, lat, lon0, lat0))
    cosc = math.sin(lat0) * math.sin(lat) + math.cos(lat0) * math.cos(lat) * math.cos(lon - lon0)
    x = math.cos(lat) * math.sin(lon - lon0)
    y = math.cos(lat0) * math.sin(lat) - math.sin(lat0) * math.cos(lat) * math.cos(lon - lon0)
    c = math.acos(max(-1.0, min(1.0, cosc)))
    s = math.hypot(x, y)
    k = c / s if s > 1e-12 else 1.0
    return x * k, y * k


def _densify_ll(ring, max_deg=1.0):
    out = []
    for a, b in zip(ring, ring[1:]):
        n = max(1, int(max(abs(b[0] - a[0]), abs(b[1] - a[1])) / max_deg))
        out += [(a[0] + (b[0] - a[0]) * t / n, a[1] + (b[1] - a[1]) * t / n) for t in range(n)]
    out.append(tuple(ring[-1]))
    return out


_HEMI = Point(0, 0).buffer(math.pi / 2, quad_segs=180)


def project_country(polys, lon0, lat0, R, cx, cy):
    """A country's lon/lat polygons -> its visible part in image px (same
    frame as globe_etch: x right, y DOWN, globe radius R about (cx, cy)), or
    None if it's all on the far side."""
    anti = Point((lon0 + 360.0) % 360.0 - 180.0, -lat0)
    parts = []
    for poly in polys:
        if len(poly[0]) >= 4 and Polygon(poly[0]).buffer(0).contains(anti):
            # the antipode is the AEQD plane's boundary circle: a polygon
            # around it would turn inside out
            raise ValueError("a country covers the view's antipode -- pick another centre")
        rings = [[_aeqd(lon, lat, lon0, lat0) for lon, lat in _densify_ll(r)] for r in poly]
        if len(rings[0]) < 4:
            continue
        g = Polygon(rings[0], [r for r in rings[1:] if len(r) >= 4]).buffer(0)
        if g.is_empty:
            continue
        g = g.intersection(_HEMI)
        if not g.is_empty:
            parts.append(g)
    if not parts:
        return None
    g = unary_union(parts)

    def squash(x, y, z=None):
        rho = math.hypot(x, y)
        k = math.sin(rho) / rho if rho > 1e-12 else 1.0
        return cx + x * k * R, cy - y * k * R

    from shapely.ops import transform
    out = transform(lambda xs, ys, zs=None: tuple(zip(*[squash(x, y) for x, y in zip(xs, ys)])), g)
    return out.buffer(0)


def inverse_ortho(px, py, lon0, lat0, R, cx, cy):
    """Image px -> (lon, lat) degrees on the visible hemisphere."""
    x, y = (px - cx) / R, -(py - cy) / R
    rho = math.hypot(x, y)
    if rho < 1e-12:
        return lon0, lat0
    rho = min(rho, 1.0)
    c = math.asin(rho)
    p0 = math.radians(lat0)
    lat = math.asin(math.cos(c) * math.sin(p0) + y * math.sin(c) * math.cos(p0) / rho)
    lon = math.radians(lon0) + math.atan2(x * math.sin(c), rho * math.cos(c) * math.cos(p0)
                                          - y * math.sin(c) * math.sin(p0))
    return math.degrees(lon), math.degrees(lat)


def project_line(lonlat, lon0, lat0, R, cx, cy):
    """Visible part of a lon/lat polyline as a list of px point lists."""
    import globe_etch as GE
    return GE.project_ring(lonlat, lon0, lat0, R, cx, cy, densify_deg=1.0)


# ---------------------------------------------------------------------------
# cleaning
# ---------------------------------------------------------------------------


def _parts(g):
    if g is None or g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    return [q for q in getattr(g, "geoms", []) if isinstance(q, Polygon) and not q.is_empty]


def _lines(g):
    from shapely.ops import linemerge
    if g.is_empty:
        return []
    if g.geom_type == "LineString":
        return [g]
    ls = [q for q in getattr(g, "geoms", []) if q.geom_type == "LineString"]
    m = linemerge(ls) if ls else None
    return [] if m is None else ([m] if m.geom_type == "LineString" else list(m.geoms))


def chop_seams(seams, land, ppm, min_mm=3.0):
    """Seams run UNDER the land: each seam is cut where it crosses land, and
    every remaining stretch of at least min_mm becomes its own seam ending
    on the coast ("L", like a seam ending on a letter). Stretches touching
    an original end keep that end's type; structural ("J", "J") seams stay
    cuttable plain (never force-dropped) after the chop."""
    out = []
    for s in seams:
        pts = [tuple(p) for p in s["pts"]]
        if len(pts) < 2:
            out.append(s)
            continue
        ls = LineString(pts)
        if not ls.intersects(land):
            out.append(s)
            continue
        p0, p1 = Point(pts[0]), Point(pts[-1])
        for g in _lines(ls.difference(land)):
            if g.length < min_mm * ppm:
                continue
            q = list(g.coords)
            if Point(q[0]).distance(p1) < Point(q[0]).distance(p0) and Point(q[-1]).distance(p0) < 0.5:
                q = q[::-1]
            ea = s["ends"][0] if Point(q[0]).distance(p0) < 0.5 else "L"
            eb = s["ends"][1] if Point(q[-1]).distance(p1) < 0.5 else "L"
            ns = dict(s, pts=q, ends=(ea, eb), land_chopped=True)
            if s["ends"] == ("J", "J"):
                ns["plain_ok"] = True
            out.append(ns)
    return out


def _mrr_sides(poly):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mrr = poly.minimum_rotated_rectangle
    if mrr.geom_type != "Polygon":
        return 0.0, 0.0
    xs, ys = mrr.exterior.coords.xy
    return tuple(sorted(math.hypot(xs[i + 1] - xs[i], ys[i + 1] - ys[i]) for i in range(2)))


def _shared(a, b, eps):
    """Approximate shared-border length of two touching polygons."""
    inter = a.buffer(eps).intersection(b.buffer(eps))
    return inter.area / (2 * eps) if not inter.is_empty else 0.0


def _open(g, r):
    return g.buffer(-r, quad_segs=6).buffer(r, quad_segs=6).intersection(g)


def _clean_mass(land, obstacles, r, rounds=3):
    """Close then open the whole land mass, with the letters (obstacles)
    counting as solid for the closing, so neither land nor the ocean left
    between land and letters keeps a feature narrower than 2r."""
    for _ in range(rounds):
        solid = unary_union([land, obstacles])
        closed = solid.buffer(r, quad_segs=6).buffer(-r, quad_segs=6)
        land = closed.difference(obstacles).buffer(0)
        land = _open(land, r).buffer(0)
    return land


def land_pieces(countries, lon0, lat0, R, C, disc, obstacles, ppm, oversized,
                min_feature_mm=3.0, min_piece_mm=7.0, island_min_mm=4.0,
                merge_area_mm2=120.0, max_len_mm=64.0):
    """Returns (pieces, stats): pieces = [{"polygon", "names"}] in image px.
    `obstacles` = the letters' solid union (land never overlaps a letter);
    `disc` = where land may exist (the globe disc minus its ocean margin);
    `oversized(poly) -> bool` = the ring's own piece-size limit; land is also
    split when its long side exceeds max_len_mm (a thin Russia-at-the-limb
    strip passes the area-based limit but still reads as one huge piece)."""
    r = (min_feature_mm / 2 + 0.2) * ppm
    raw = []
    for c in countries:
        g = project_country(c["polys"], lon0, lat0, R, C[0], C[1])
        if g is None:
            continue
        g = g.intersection(disc).difference(obstacles)
        if not g.is_empty:
            raw.append((c["name"], g))
    land = _clean_mass(unary_union([g for _n, g in raw]), obstacles, r)
    st = {"countries": len(raw), "merged": 0, "split": 0, "dropped_islands": 0}

    def big_enough(q):
        sides = _mrr_sides(q)
        return sides[0] >= min_piece_mm * ppm and q.area >= merge_area_mm2 * ppm ** 2

    pcs = []
    for comp in _parts(land):
        # countries that hold up on their own (after their own 3mm opening:
        # a border can pinch a country thinner than the coast-level clean-up)
        kept = []
        for name, g in raw:
            if not g.intersects(comp):
                continue
            for q in _parts(_open(g.intersection(comp), r)):
                if big_enough(q):
                    kept.append({"polygon": q, "names": [name]})
        if not kept:
            # an island (or a cluster of small countries): one piece, if it
            # is big enough to cut at all
            if _mrr_sides(comp)[0] < island_min_mm * ppm:
                st["dropped_islands"] += 1
                continue
            names = [n for n, g in raw if g.intersection(comp).area > 0.2 * comp.area]
            pcs.append({"polygon": comp, "names": names})
            continue
        # everything else in the component (small countries, enclaves, thin
        # border strips, closed-up straits) joins the kept piece it shares
        # the most border with
        rest = comp.difference(unary_union([k["polygon"] for k in kept]))
        add = [[] for _ in kept]
        for q in _parts(rest):
            sh = [(_shared(q, k["polygon"], 0.1 * ppm), -q.distance(k["polygon"]), n)
                  for n, k in enumerate(kept)]
            add[max(sh)[2]].append(q)
            st["merged"] += 1
        for k, extra in zip(kept, add):
            if extra:
                u = unary_union([k["polygon"]] + extra)
                parts = sorted(_parts(u), key=lambda q: -q.area)
                k["polygon"] = parts[0]
                for q in parts[1:]:  # touched only at a corner: its own bit
                    if q.area > 0.5 * ppm ** 2:
                        pcs.append({"polygon": q, "names": list(k["names"])})
        pcs += kept

    # split oversized pieces along a meridian / parallel
    def too_big(g):
        return oversized(g) or _mrr_sides(g)[1] > max_len_mm * ppm

    stuck = []
    for _round in range(60):
        k = next((k for k, p in enumerate(pcs) if too_big(p["polygon"])), None)
        if k is None:
            break
        p = pcs.pop(k)
        parts = _graticule_split(p["polygon"], lon0, lat0, R, C, ppm, min_part_mm=min_piece_mm)
        if not parts:
            st.setdefault("unsplit_names", []).append("/".join(p["names"]))
            stuck.append(p)
            continue
        pcs += [{"polygon": q, "names": list(p["names"])} for q in parts]
        st["split"] += 1
    pcs += stuck
    st["unsplittable"] = len(stuck)
    for p in pcs:
        p["polygon"] = p["polygon"].buffer(0)
        # borders of the countries merged into this piece: etch, not cut
        inner = p["polygon"].buffer(-1.0 * ppm)
        lines = [g.boundary.intersection(inner) for n, g in raw
                 if n in p["names"] and len(p["names"]) > 1 and g.intersects(inner)]
        p["borders"] = [list(q.coords) for ln in lines for q in _lines(ln)
                        if q.length > 2.0 * ppm]
    st["pieces"] = len(pcs)
    return pcs, st


def _graticule_split(poly, lon0, lat0, R, C, ppm, snap_deg=5.0, min_part_mm=7.0):
    """Split poly in two along the projected meridian or parallel through it
    that best balances the halves (preferring a 5-degree graticule line), cut
    across its long axis. Returns the parts, or None."""
    c = poly.representative_point() if not poly.contains(poly.centroid) else poly.centroid
    lon_c, lat_c = inverse_ortho(c.x, c.y, lon0, lat0, R, C[0], C[1])
    cands = []
    for kind in ("lon", "lat"):
        v0 = lon_c if kind == "lon" else lat_c
        vals = {v0}
        for k in range(-6, 7):
            vals.add(round(v0 / snap_deg) * snap_deg + k * snap_deg)
        for v in vals:
            if kind == "lon":
                ll = [(v, la) for la in range(-90, 91)]
            else:
                if abs(v) >= 89:
                    continue
                ll = [(lo, v) for lo in range(-180, 181)]
            for s in project_line(ll, lon0, lat0, R, C[0], C[1]):
                if len(s) < 2:
                    continue
                line = LineString(s)
                if not line.intersects(poly):
                    continue
                try:
                    res = [q for q in split(poly, line).geoms if q.area > 0]
                except Exception:
                    continue
                if len(res) < 2:
                    continue
                res = sorted(res, key=lambda q: -q.area)
                if any(_mrr_sides(q)[0] < min_part_mm * ppm for q in res):
                    continue
                cut_len = line.intersection(poly).length
                bal = res[1].area / poly.area if len(res) == 2 else res[-1].area / poly.area
                snapped = abs(v / snap_deg - round(v / snap_deg)) < 1e-6
                # balanced first, then short cut, then a graticule-line bonus
                cost = (abs(0.5 - bal) * 4) + cut_len / (poly.length / 2) - (0.15 if snapped else 0)
                cands.append((cost, res))
    if not cands:
        return _straight_split(poly, ppm, min_part_mm)
    return min(cands, key=lambda t: t[0])[1]


def _straight_split(poly, ppm, min_part_mm):
    """Fallback for a strip the graticule only runs along (land hugging the
    limb): a straight cut square to its long axis, through its middle."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mrr = poly.minimum_rotated_rectangle
    xs, ys = mrr.exterior.coords.xy
    e = max((((xs[k], ys[k]), (xs[k + 1], ys[k + 1])) for k in range(2)),
            key=lambda q: math.dist(*q))
    L_ = math.dist(*e)
    ux, uy = (e[1][0] - e[0][0]) / L_, (e[1][1] - e[0][1]) / L_
    m = mrr.centroid
    for f in (0.0, 0.05, -0.05, 0.1, -0.1, 0.15, -0.15, 0.2, -0.2, 0.25, -0.25, 0.3, -0.3):
        c = (m.x + ux * f * L_, m.y + uy * f * L_)
        line = LineString([(c[0] - uy * 2 * L_, c[1] + ux * 2 * L_),
                           (c[0] + uy * 2 * L_, c[1] - ux * 2 * L_)])
        res = [q for q in split(poly, line).geoms if q.area > 0]
        if len(res) >= 2 and all(_mrr_sides(q)[0] >= min_part_mm * ppm for q in res):
            return res
    return None


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--refresh-cache":
        refresh_cache(sys.argv[2])
