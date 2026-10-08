"""Etched outline strokes on both sides of every letter cut.

For each letter-kind piece, one line `inset_mm` INSIDE its cut edge (etched
on the letter itself) and one `outset_mm` OUTSIDE it (etched on whichever
background piece(s) the letter seats into). Both follow every ring of the
letter, so counters get them too (the inset grows the hole, the outset
lands inside the counter piece). Purpose: the letters read before painting,
and since the etch is only on the top face it shows which side is up when
assembling from a jumbled bag.

Strokes are open polylines in the same image-px frame as the pieces, ready
for emitter.emit_etch_gcode() and for drawing over render_preview's PNG.
"""
from __future__ import annotations

from PIL import Image, ImageDraw
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon
from shapely.ops import unary_union

_ROUND = 1  # shapely join/cap style: round


def _rings(geom):
    polys = geom.geoms if isinstance(geom, MultiPolygon) else [geom]
    for p in polys:
        if p.is_empty:
            continue
        yield p.exterior
        yield from p.interiors


def _lines(geom):
    if geom.is_empty:
        return []
    if isinstance(geom, LineString):
        return [geom]
    if isinstance(geom, MultiLineString):
        return list(geom.geoms)
    return [g for g in getattr(geom, "geoms", []) if isinstance(g, LineString)]


def letter_outline_strokes(letter_polys, panel, ppm, inset_mm=0.75, outset_mm=0.75):
    """Returns (strokes, warnings). strokes: list of [(x, y), ...] px
    polylines. A letter whose stroke is too thin for the inset (the inset
    collapses or splits a stroke apart) is reported in `warnings` rather
    than silently dropped or etched as fragments."""
    strokes, warns = [], []
    others_cache = unary_union(letter_polys)
    inner_panel = panel.buffer(-outset_mm * ppm)
    for i, g in enumerate(letter_polys):
        n_parts = len(g.geoms) if isinstance(g, MultiPolygon) else 1
        if inset_mm > 0:
            inn = g.buffer(-inset_mm * ppm, join_style=_ROUND)
            n_in = 0 if inn.is_empty else (len(inn.geoms) if isinstance(inn, MultiPolygon) else 1)
            if n_in != n_parts:
                c = g.centroid
                warns.append(
                    f"letter at ({c.x / ppm:.0f},{c.y / ppm:.0f})mm: {inset_mm}mm inset "
                    f"splits it into {n_in} part(s) -- stroke too thin"
                )
            for r in _rings(inn):
                strokes.append(list(r.coords))
        if outset_mm > 0:
            out = g.buffer(outset_mm * ppm, join_style=_ROUND)
            # stay on the panel and never run across a neighbouring letter
            others = others_cache.difference(g.buffer(1.0))
            keep = inner_panel.difference(others.buffer(outset_mm * ppm))
            for r in _rings(out):
                for ln in _lines(LineString(r.coords).intersection(keep)):
                    if ln.length > 2 * ppm:
                        strokes.append(list(ln.coords))
    return strokes, warns


def draw_strokes(png_path, strokes, color=(20, 20, 20), width=1):
    img = Image.open(png_path).convert("RGB")
    d = ImageDraw.Draw(img)
    for s in strokes:
        d.line([tuple(p) for p in s], fill=color, width=width)
    img.save(png_path)
