"""GCode emission + preview rendering for orpot.

Every program is built and finished by quickcut.gcode (the one laser G-code core), so the machine
behaviour is the shop's:

  - $32=1 laser mode, G21 mm, G90 absolute
  - STATIC M3 constant power at 100% (this weak diode under-fires on M4 dynamic)
  - a front-loaded warmup wiggle at the START of every cut so the beam reaches
    full power before it bites (diode cold-start ~1s ramp)
  - point decimation so no emitted segment is shorter than a floor

Coordinates arrive already in machine mm (Y-up, positive) from spiral.py, so
there is no image-pixel flip here (unlike jigsaw).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "quickcut"))
import gcode  # noqa: E402  (THE G-code core: preamble, cut, frame, park, validation)

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import Polygon

REPO_ROOT = Path(__file__).resolve().parent


def load_material(material_id: str) -> dict:
    """the full material record (quickcut.gcode.material)"""
    return gcode.material(material_id)


# ---------------------------------------------------------------------------
# Cut GCode
# ---------------------------------------------------------------------------


def _exterior_coords(poly: Polygon) -> list[tuple[float, float]]:
    return [(float(x), float(y)) for x, y in poly.exterior.coords]


def _ring_coords(ring) -> list[tuple[float, float]]:
    return [(float(x), float(y)) for x, y in ring.coords]


def _job(material: dict, cfg, feed_override=None, power_percent=None):
    over = {"min_segment_mm": cfg.min_segment_mm}
    if feed_override is not None:
        over["feed_mm_per_min"] = feed_override
    if power_percent is not None:
        over["power_percent"] = power_percent
    return gcode.job(material["id"], **over)


def emit_cut_gcode(
    parts: list[tuple[str, Polygon]],
    material: dict,
    title: str,
    cfg,
    feed_override: int | None = None,
    power_percent: float | None = None,
) -> str:
    """Cut G-code for one or more parts through quickcut.gcode: each part's interior holes (slots)
    first, its outer profile last (so the piece stays anchored), the shop's standard cut (warmup on
    every path, recipe passes, loop overlap), frame + park. Coordinates are already machine mm.
    Not validated here (callers may pass centred test geometry): orpot.py writes with gcode.write."""
    j = _job(material, cfg, feed_override, power_percent)
    lines = gcode.preamble(j, title=title, notes=[
        "orpot: two flat spiral ramps + radial ribs; assemble then lift/coil",
        "holes (slots) cut first, outer profile last; kerf becomes clearance",
        "ASSUMES Z already at focal height in your WCS"])
    rings = []
    for name, poly in parts:
        rings += [(f"{name} slot {i + 1}", _ring_coords(r)) for i, r in enumerate(poly.interiors)]
        rings.append((f"{name} profile", _exterior_coords(poly)))
    for k, (label, pts) in enumerate(rings, 1):
        lines += gcode.cut(gcode.Polyline(pts, True), j, k, len(rings), label=f"{label}:")
    return gcode.render(lines, validate=False)


def emit_disc_gcode(
    profile: Polygon,
    cuts: list,
    material: dict,
    title: str,
    cfg,
    feed_override: int | None = None,
    power_percent: float | None = None,
) -> str:
    """The SINGLE-PIECE disc through quickcut.gcode: rib-slot holes first, then the open spiral cut
    paths, then the outer profile last (the piece stays anchored until the final cut)."""
    j = _job(material, cfg, feed_override, power_percent)
    lines = gcode.preamble(j, title=title, notes=[
        "orpot single-piece disc: 2 interleaved spiral cuts, expands into a pot",
        "order: rib slots, then spiral cuts, then outer profile last",
        "spiral cuts are OPEN paths (kerf gap); the disc stays one piece",
        "ASSUMES Z already at focal height in your WCS"])
    paths = [(f"rib slot {i + 1}", _ring_coords(r), True) for i, r in enumerate(profile.interiors)]
    paths += [(f"spiral cut {i + 1}", [(float(x), float(y)) for x, y in ln.coords], False) for i, ln in enumerate(cuts)]
    paths.append(("disc profile", _exterior_coords(profile), True))
    for k, (label, pts, closed) in enumerate(paths, 1):
        lines += gcode.cut(gcode.Polyline(pts, closed), j, k, len(paths), label=f"{label}:")
    return gcode.render(lines, validate=False)


# ---------------------------------------------------------------------------
# Preview (PIL raster; SVG opt-in). Mirrors jigsawzall's render approach.
# ---------------------------------------------------------------------------


def _load_font(size: int):
    for name in ("Arial.ttf", "DejaVuSans.ttf", "Helvetica.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _poly_rings(poly: Polygon) -> list[list[tuple[float, float]]]:
    rings = [[(float(x), float(y)) for x, y in poly.exterior.coords]]
    for interior in poly.interiors:
        rings.append([(float(x), float(y)) for x, y in interior.coords])
    return rings


def render_disc(
    profile: Polygon,
    cuts: list,
    out_stem: Path,
    title: str,
    write_svg: bool = False,
) -> tuple[Path, "Path | None"]:
    """Render the single-piece disc: outer profile + rib-slot holes (dark) and
    the open spiral cut paths (distinct colors). Returns (png, svg_or_None)."""
    rings = _poly_rings(profile)  # [exterior, *holes]
    cut_rings = [[(float(x), float(y)) for x, y in c.coords] for c in cuts]
    xs = [x for r in rings for x, _ in r]
    ys = [y for r in rings for _, y in r]
    pad = 10.0
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    vw, vh = (maxx - minx) + 2 * pad, (maxy - miny) + 2 * pad
    scale = max(4, int(900 / max(vw, vh)))
    W, H = int(vw * scale), int(vh * scale) + 24

    cut_colors = [(40, 90, 200), (200, 90, 40), (40, 150, 90), (150, 60, 160)]

    def px(x, y):
        return ((x - minx + pad) * scale, 24 + (maxy - y + pad) * scale)

    png_path = out_stem.with_suffix(".png")
    svg_path = out_stem.with_suffix(".svg")

    if write_svg:

        def fy(y):
            return maxy - y + pad

        svg = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{vw}mm" '
            f'height="{vh}mm" viewBox="0 0 {vw} {vh}">',
            f'<rect x="0" y="0" width="{vw}" height="{vh}" fill="white"/>',
            f"<title>{title}</title>",
        ]
        for r in rings:
            pts = " ".join(f"{x - minx + pad:.3f},{fy(y):.3f}" for x, y in r)
            svg.append(
                f'<polyline points="{pts}" fill="none" stroke="#333" stroke-width="0.3"/>'
            )
        for i, r in enumerate(cut_rings):
            col = cut_colors[i % len(cut_colors)]
            pts = " ".join(f"{x - minx + pad:.3f},{fy(y):.3f}" for x, y in r)
            svg.append(
                f'<polyline points="{pts}" fill="none" '
                f'stroke="rgb{col}" stroke-width="0.4"/>'
            )
        svg.append("</svg>")
        svg_path.write_text("\n".join(svg))

    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    for r in rings:
        d.line([px(x, y) for x, y in r], fill=(60, 60, 60), width=2)
    for i, r in enumerate(cut_rings):
        d.line([px(x, y) for x, y in r], fill=cut_colors[i % len(cut_colors)], width=2)
    d.text((pad, 6), title, fill=(20, 20, 20), font=_load_font(14))
    img.save(png_path, "PNG", optimize=True)
    return png_path, (svg_path if write_svg else None)


def render_preview(
    parts: list[tuple[str, Polygon]],
    out_stem: Path,
    title: str,
    write_svg: bool = False,
) -> tuple[Path, Path | None]:
    """Write a PNG (and optionally SVG) of the part outlines. Returns
    (png_path, svg_path_or_None). Y is flipped so up is +Y like the machine."""
    pad = 10.0
    all_rings = [ring for _, p in parts for ring in _poly_rings(p)]
    xs = [x for ring in all_rings for x, _ in ring]
    ys = [y for ring in all_rings for _, y in ring]
    maxx, maxy = max(xs), max(ys)
    vw = maxx + 2 * pad
    vh = maxy + 2 * pad

    png_path = out_stem.with_suffix(".png")
    svg_path = out_stem.with_suffix(".svg")

    def fy(y: float) -> float:
        return maxy - y + pad

    if write_svg:
        svg = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{vw}mm" '
            f'height="{vh}mm" viewBox="0 0 {vw} {vh}">',
            f'<rect x="0" y="0" width="{vw}" height="{vh}" fill="white"/>',
            f"<title>{title}</title>",
        ]
        for ring in all_rings:
            pts = " ".join(f"{x + pad:.3f},{fy(y):.3f}" for x, y in ring)
            svg.append(
                f'<polyline points="{pts}" fill="none" stroke="#b03020" '
                f'stroke-width="0.3"/>'
            )
        svg.append("</svg>")
        svg_path.write_text("\n".join(svg))

    scale = max(4, int(900 / max(vw, vh)))
    W, H = int(vw * scale), int(vh * scale)
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)

    def px(x: float, y: float) -> tuple[float, float]:
        return ((x + pad) * scale, (maxy - y + pad) * scale)

    for ring in all_rings:
        d.line([px(x, y) for x, y in ring], fill=(176, 48, 32), width=2)
    d.text(
        (pad * scale, 2), title, fill=(20, 20, 20), font=_load_font(max(12, scale * 3))
    )
    img.save(png_path, "PNG", optimize=True)
    return png_path, (svg_path if write_svg else None)


# ---------------------------------------------------------------------------
# 3D assembly sketch (orthographic wireframe; no extra deps)
# ---------------------------------------------------------------------------


def _project(pts: np.ndarray, az_deg: float, el_deg: float) -> np.ndarray:
    """Rotate 3D points about Z by az, then about X by el, and orthographically
    keep (x, z) as screen (u, v up). Returns Nx3 array [u, v, depth]; depth is
    the post-rotation Y (larger = further from camera)."""
    az, el = math.radians(az_deg), math.radians(el_deg)
    x, y, z = pts[:, 0], pts[:, 1], pts[:, 2]
    x1 = x * math.cos(az) - y * math.sin(az)
    y1 = x * math.sin(az) + y * math.cos(az)
    z1 = z
    y2 = y1 * math.cos(el) - z1 * math.sin(el)
    z2 = y1 * math.sin(el) + z1 * math.cos(el)
    return np.column_stack([x1, z2, y2])


def render_assembly_sketch(
    helices: list[tuple[str, dict]],
    cfg,
    out_stem: Path,
    title: str,
    base_r: float,
    az_deg: float = 32.0,
    el_deg: float = 22.0,
    ribs: list[dict] | None = None,
) -> Path:
    """Quick 3D wireframe sketch of the assembled pot: each spiral's inner/outer
    rails plus ribbon rungs, the base disc, and (optionally) the vertical ribs
    with their capture slots. Depth-sorted painter's order; colors distinguish
    the two spirals and the ribs."""
    palette = {
        "bottom": (184, 118, 58),  # warm ochre
        "top": (176, 48, 32),  # brick red
    }
    rib_color = (70, 100, 150)  # steel blue
    rung_fade = 0.55  # rungs drawn lighter than rails

    # Collect drawable segments as (depth, (u1,v1), (u2,v2), color, width).
    segs: list[tuple] = []

    def add_polyline(p3: np.ndarray, color, width, step=1):
        pr = _project(p3, az_deg, el_deg)
        for i in range(0, len(pr) - step, step):
            a, b = pr[i], pr[i + step]
            segs.append(
                (
                    (a[2] + b[2]) / 2.0,
                    (a[0], a[1]),
                    (b[0], b[1]),
                    color,
                    width,
                )
            )

    def lighten(c, f):
        return tuple(int(v + (255 - v) * f) for v in c)

    # Base disc outline (at z=0).
    ang = np.linspace(0, 2 * np.pi, 128)
    disc = np.column_stack(
        [base_r * np.cos(ang), base_r * np.sin(ang), np.zeros_like(ang)]
    )
    add_polyline(disc, (120, 120, 120), 2)

    # Rim ring (full circle, outer + inner) at the top height.
    z_rim = cfg.rise_per_rev_mm * cfg.turns
    for rr in (cfg.top_outer_r, cfg.top_outer_r - cfg.top_ring_w_mm):
        ring = np.column_stack(
            [rr * np.cos(ang), rr * np.sin(ang), np.full_like(ang, z_rim)]
        )
        add_polyline(ring, (120, 120, 120), 2)

    for name, hx in helices:
        color = palette.get(name, (80, 80, 80))
        inner, outer = hx["inner"], hx["outer"]
        # Ribbon rungs (inner->outer) every ~8mm of travel to read as a surface.
        n = len(inner)
        stride = max(1, n // 48)
        for i in range(0, n, stride):
            rung = np.vstack([inner[i], outer[i]])
            add_polyline(rung, lighten(color, rung_fade), 1)
        # Rails on top.
        add_polyline(inner, color, 2)
        add_polyline(outer, color, 2)

    # Vertical ribs (exterior outline + capture-slot holes).
    for rib in ribs or []:
        add_polyline(rib["exterior"], rib_color, 2)
        for hole in rib["holes"]:
            add_polyline(hole, lighten(rib_color, 0.3), 1)

    # Screen transform: fit all segment endpoints into a padded canvas.
    us = [u for _, (u, _), _, _, _ in segs] + [u for _, _, (u, _), _, _ in segs]
    vs = [v for _, (_, v), _, _, _ in segs] + [v for _, _, (_, v), _, _ in segs]
    minu, maxu, minv, maxv = min(us), max(us), min(vs), max(vs)
    pad = 30.0
    span = max(maxu - minu, maxv - minv)
    scale = (900 - 2 * pad) / span
    W = int((maxu - minu) * scale + 2 * pad)
    H = int((maxv - minv) * scale + 2 * pad) + 24  # room for the title

    def to_px(u, v):
        # v is up in world -> flip for image y.
        return (pad + (u - minu) * scale, 24 + pad + (maxv - v) * scale)

    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    # Painter's order: far (large depth) first.
    for _, p1, p2, color, width in sorted(segs, key=lambda s: -s[0]):
        d.line([to_px(*p1), to_px(*p2)], fill=color, width=width)
    d.text((pad, 6), title, fill=(20, 20, 20), font=_load_font(15))

    png_path = out_stem.with_suffix(".png")
    img.save(png_path, "PNG", optimize=True)
    return png_path
