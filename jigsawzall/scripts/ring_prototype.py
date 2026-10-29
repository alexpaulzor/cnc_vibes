#!/usr/bin/env python3
"""REFERENCE PROTOTYPE (not wired into jigsaw.py): circular "ring" name puzzle.

Letters sit upright on a ring (tops outward, reading clockwise from 12 o'clock),
and the background is tiled by a POLAR wave-grid: radial caps off each letter
to the rim and hub, ring seams between neighbouring letters, rim / mid / hub
subdivisions that T-junction onto those seams, a hub circle split into arcs,
and a hub disc split into pinwheel spokes. Reuses geometry.py's curve, tab,
splice and validity machinery unchanged.

See RING_SPEC.md for the algorithm and the plan for productionizing this into
geometry.py / jigsaw.py. Render a sketch:

    PYTHONPATH=. python3 scripts/ring_prototype.py JONATHAN
    PYTHONPATH=. python3 scripts/ring_prototype.py JONATHAN --shape square --debug
"""

import argparse
import contextlib
import hashlib
import math
import random
import sys
import warnings
from dataclasses import dataclass, replace
from pathlib import Path

warnings.simplefilter("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shapely  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from shapely import affinity  # noqa: E402
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box  # noqa: E402
from shapely.ops import nearest_points, polygonize, unary_union  # noqa: E402

import geometry as G  # noqa: E402

# ---------------------------------------------------------------------------
# Planet Labs logo: real letterforms + brand mark, traced from a photo of an
# actual sticker (Alex is a Planet Labs employee; this is for his own desk --
# see RING_SPEC.md for why traced-from-real beats guessing at a system font
# or approximating their trademark from memory). See scripts/trace_planet_
# logo.py for how data/planet_logo_glyphs.json was produced.
# ---------------------------------------------------------------------------

PLANET_LOGO_FONT = "planet_logo"  # RingParams.font sentinel
PLANET_LOGO_ORNAMENT = "planet_logo"  # RingParams.ornament sentinel
_planet_logo_glyphs_cache = None


def _load_planet_logo_glyphs():
    global _planet_logo_glyphs_cache
    if _planet_logo_glyphs_cache is None:
        import json

        from shapely import wkt as _wkt

        path = Path(__file__).resolve().parent.parent / "data" / "planet_logo_glyphs.json"
        data = json.loads(path.read_text())
        glyphs = {k: _smooth_traced(_wkt.loads(v)) for k, v in data["glyphs"].items()}
        _planet_logo_glyphs_cache = (glyphs, data["cap_ref_px"], data["ring"])
    return _planet_logo_glyphs_cache


# The logo glyphs were traced from a photo at 133px cap height: raw 1px
# staircase outlines (150-190 turns over 25deg per round letter) that cut as a
# visibly ragged edge on the real PLANET cut. Spline-smooth each ring (genuine
# corners kept) within this many SOURCE px -- 0.6px = 0.21mm at a 46mm cap.
PLANET_GLYPH_SMOOTH_PX = 0.6


def _smooth_traced(poly):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "quickcut"))
    import arcfit

    def sm(r):
        return arcfit.smooth_polyline(list(r.coords), max_dev_mm=PLANET_GLYPH_SMOOTH_PX,
                                      spacing_mm=0.5, corner_deg=60)

    polys = poly.geoms if isinstance(poly, MultiPolygon) else [poly]
    out = [Polygon(sm(p.exterior), [sm(r) for r in p.interiors]).buffer(0) for p in polys]
    return out[0] if len(out) == 1 else unary_union(out)


def planet_logo_ornament_artwork(cap_h):
    """Etch-only detail for the PLANET_LOGO_ORNAMENT disc: the real "p" +
    brand-ring artwork, scaled so the ring's OUTER radius matches the
    ornament disc's own radius (ornament_local's 0.30*cap_h) and positioned
    in the SAME local frame (cy = -cap_h/2, same as ornament_local) so a
    caller can union this directly with that disc's placement transform.
    Returns a list of local-frame LineStrings (unplaced) -- never cut, only
    etched (see the disc-not-a-thin-ring note in ornament_local)."""
    glyphs, _, ring = _load_planet_logo_glyphs()
    disc_r = 0.30 * cap_h
    scale = disc_r / ring["outer_r"]
    cy = -cap_h / 2
    # The disc's radius came from the RING's outer radius, so the ring -- not
    # "p" -- belongs at the disc's own center (0, cy); "p" sits at whatever
    # offset it has from the ring's center in the traced artwork (ring[cx]/
    # [cy] are p's local frame minus the ring's true center, i.e. the ring's
    # position relative to p -- so p's placement is the negation of that).
    p = affinity.scale(glyphs["p"], scale, scale, origin=(0, 0))
    p = affinity.translate(p, -ring["cx"] * scale, cy - ring["cy"] * scale)
    strokes = [p.exterior] + list(p.interiors)
    for r in (ring["outer_r"] * scale, ring["inner_r"] * scale):
        strokes.append(Point(0, cy).buffer(r, quad_segs=64).exterior)
    return [LineString(s) for s in strokes]


@dataclass
class RingParams:
    diameter_mm: float = 290.0  # disc diameter (or square side); 290 = 300mm stock minus 5mm each side (RING_SPEC)
    shape: str = "disc"  # "disc" | "square"
    rim_mm: float = 24.0  # letter tops -> rim (== banner_margin_mm)
    corner_ring_mm: float = 20.0  # square only: corner arc radius = inscribed r + this
    cap_h_max_mm: float = 46.0  # == banner_letter_h_mm (upper bound)
    cap_h_min_mm: float = 18.0
    min_gap_mm: float = 14.0  # tab fit (tab_height + 2R = 11) + letter_gap_extra 3
    # Letters inside a name keep (about) the minimum gap; all spare angle goes
    # to the gaps on either side of the ornaments instead of spreading every
    # letter out evenly.
    pack_words: bool = False
    keep_seams: bool = False  # cut seams plain rather than dropping them
    ornament_scale: float = 1.0  # heart/dot/star size relative to the default
    # After assembly, split any piece whose longest side exceeds this (mm)
    # with a tabbed cross seam (0 = off).
    split_mm: float = 0.0
    hub_arc_mm: float = 30.0  # min hub-circle arc per hub-ring piece
    hub_ring_min_mm: float = 26.0  # min radial thickness of the hub ring
    hub_r_min_mm: float = 22.0
    target_w_mm: float = 46.0  # target arc width of rim / hub-ring pieces
    hub_piece_mm: float = 62.0  # max chord of a hub-disc wedge
    rows: int = 3  # rings of background across the letter band (2 or 3)
    levels3: tuple = (
        0.18,
        0.82,
    )  # rows=3 ring-seam heights (frac of cap h), banner-equivalent
    # None | "dot" | "heart" | "star" | "globe" | "rocket" | "satellite" | ...
    # -- or a list/tuple of kinds, one per word in `words` (cycled if shorter),
    # for a different separator after each word instead of the same one
    # every time (e.g. ["rocket", "satellite"] for a two-word ring).
    ornament: str | list[str] | None = "heart"
    # Indices into `words` that should read upright "from the south" instead
    # of the default ring convention (every letter's cap radially OUTWARD,
    # baseline INWARD, uniformly -- correct reading at the top, but requires
    # physically flipping the piece 180 to read at the bottom, like a coin).
    # A mirrored word instead reads correctly left-to-right without flipping
    # anything: cap toward the CENTER, baseline toward the OUTSIDE/rim,
    # still following the ring's curve letter-by-letter (each letter its own
    # tabbed piece, same as any other word) -- like text arcing under the
    # bottom of a circular badge/seal, not a coin's inverted second line.
    mirror_words: tuple[int, ...] = ()
    # index of the word to centre at 12 o'clock (None: last ornament at 6 o'clock)
    center_word: int | None = None
    variants: int = 12
    font: str | None = None  # geometry.find_font path/alias; None = repo default
    outline_smooth_px: float = 1.2
    # Tab / clearance sizing (defaults = the name-plate tabs). Small discs
    # (~140mm, 4-up on a 300mm panel) need scaled-down tabs: every seam must
    # hold tab_len + 2 * clearance, and 140mm-class seams are only 13-20mm long.
    tab_r_px: int = 15
    tab_stem_px: float = 30.0
    border_floor_mm: float = 7.0
    letter_clearance_mm: float = 4.0
    # Solid outer frame: a continuous annulus this wide (mm) around the puzzle,
    # never cut radially. Rim seams stop on its inner circle (T-junctions) and the
    # circle itself is split into a few tabbed arcs so the pieces lock into it.
    # 0 = no frame (rim pieces run to the panel edge).
    frame_mm: float = 0.0
    frame_arcs: int = 4
    # Keep letter caps/bridges as plain (untabbed) cuts when no tab fits,
    # instead of dropping them and merging their pieces. Needed with a frame:
    # letter-top -> frame caps are too short for any tab.
    plain_caps: bool = False  # Douglas-Peucker on glyph outlines (px) to kill pixel stairs
    # Vary each tab's bulb/neck size by a small, deterministic-per-edge amount
    # (see TAB_SIZE_CLASSES) so loose pieces don't all interchange -- every
    # class still stays within lint_tab_hardware's proven-safe range.
    distinct_tabs: bool = True
    # Center medallion: two lines of plain (non-wrapped) text in the hub disc,
    # e.g. ("THE", "PAULS"). Exempt from the ring's own letter-gap rules --
    # each word is fused into ONE piece (letters + a support baseline bar) so
    # it holds together, like a single oversized "letter" of the outer name.
    # Forces the hub disc to stay undivided (no pinwheel spokes) so nothing
    # slices through the text. None = no center text (default, unchanged
    # pinwheel hub).
    center_text: tuple | None = None
    center_text_cap_mm: float = 13.0  # per-line cap height, shrinks to fit
    center_text_cap_min_mm: float = 6.0
    center_text_gap_mm: float = 3.0  # vertical gap between the two lines
    center_text_baseline_mm: float = 5.0  # support bar height, below each line
    center_text_fit_frac: float = 0.82  # block must fit within r_h * this
    # How center_text is cut:
    #   "medallion" -- the original: each word fused with a support bar,
    #                  hub disc left as one undivided piece around it.
    #   "flat"      -- two horizontal lines of loose individual letters (no
    #                  bar), cut straight out of the normal pinwheel-sliced
    #                  hub; spokes run wherever they run, letters just become
    #                  pockets in whichever wedge(s) they land in.
    #   "ring"      -- same loose letters, but arced around an inner circle:
    #                  word 1 across the top, word 2 across the bottom
    #                  flipped (reversed + rotated 180) so both read upright,
    #                  with both words' midlines on the same circle.
    center_style: str = "medallion"
    # Leave the hub circle (centre disc <-> ring) as a plain, tab-free circular
    # cut so the assembled centre disc turns freely: nobody's name is favoured
    # as "up". The disc's own spokes keep their tabs and hold it together.
    hub_rotates: bool = True
    center_track_mm: float = 2.0  # starting extra spacing between loose center letters
    # Wood between two adjacent loose center letters is a short finger
    # attached only at its ends -- must be at least this wide (Alex: 5mm
    # floor for short spans on the 3-ply veneer stock). Tracking widens until
    # it holds, then cap height shrinks if it can't fit.
    center_min_gap_mm: float = 5.0
    # Split hub for two center words (center_style "ring"): a tabbed divider
    # across the disc between them instead of a pinwheel (_center_split_seams)
    center_split: bool = False
    center_band_gap_mm: float = 6.0  # inner circle this far inside the letters
    center_row_mm: float = 30.0  # flat-text grid: target row height above/below the text
    center_end_seg_mm: float = 10.0  # flat-text grid: shortest chord end (circle to first seam)
    center_flat_band_gap_mm: float = 15.0  # flat-text grid: wood above/below the text (>14 sliver floor)
    center_arc_min_mm: float = 24.0  # min inner-circle arc between landings (room for a tab)
    center_cross_min_deg: float = 70.0  # inner-disc pieces at least this wide (else absorbed)
    # Loose center text (flat/ring) searches DOWN from this cap height in
    # 0.5mm steps and keeps the largest that fits -- i.e. it fills the hub
    # instead of stopping at center_text_cap_mm (the medallion's fixed start).
    center_loose_cap_max_mm: float = 30.0
    center_match_ring_cap: bool = True  # center letters at the ring's cap height
    # Print one line per variant (score + elapsed + ETA) while generating.
    progress: bool = False
    # Globe land as CUT pieces (RING_SPEC.md §13.14): (lon0, lat0) of an
    # orthographic globe view, or None. Countries become tabless drop-in
    # pieces like letters: seams stop at the coast, land is carved out of
    # the ocean pieces as pockets, and the letters cut their own pockets
    # into the land. The globe's limb sits globe_margin_mm inside the rim so
    # the ocean band outside the land is never thinner than a rim piece.
    globe_land: tuple | None = None
    globe_margin_mm: float = 24.0
    land_min_feature_mm: float = 3.0  # no land neck / strait narrower (3mm veneer floor)
    land_min_piece_mm: float = 7.0  # smaller countries fold into a neighbour
    land_island_min_mm: float = 4.0  # islands narrower than this aren't cut at all
    land_bridge_mm: float = 10.0  # land pieces within this square get breakaway bridges
    # False: no ring seams between letters -- the background is polar
    # sectors from the hub circle to the rim, each bending sector_twist_deg
    # off radial by the rim (growing with radius, like the hub pinwheel).
    ring_seams: bool = True
    sector_twist_deg: float = 12.0


# --------------------------------------------------------------------------
# glyph rendering in a LOCAL frame: x = 0 at ink centre, y = 0 at baseline,
# y DOWN (image convention, so geometry.py's letter helpers work unchanged).
# --------------------------------------------------------------------------


# Exact symmetry per letter, so a letter piece still drops into its pocket
# (and any same-size copy's pocket) when flipped or turned: "v" = left-right
# mirror, "h" = top-bottom mirror, "r" = 180-degree turn. Letters not listed
# have no natural symmetry and fit one way only.
GLYPH_SYMMETRY = {
    **{c: "v" for c in "AMTUVWY"},
    **{c: "h" for c in "BCDEK"},
    **{c: "vh" for c in "HIOX"},
    **{c: "r" for c in "NSZ"},
}
SYMMETRIC_GLYPHS = True


def _sym_copy(g, op, c):
    if op == "v":
        return affinity.scale(g, -1, 1, origin=(c[0], c[1]))
    if op == "h":
        return affinity.scale(g, 1, -1, origin=(c[0], c[1]))
    return affinity.rotate(g, 180, origin=(c[0], c[1]))


def _best_centre(g, op, steps=24):
    """Axis (v/h) or centre (r) that best matches g to its own mirror/turn:
    a coarse-to-fine search around the bbox centre."""
    x0, y0, x1, y1 = g.bounds
    c = [(x0 + x1) / 2, (y0 + y1) / 2]
    span = [0.08 * (x1 - x0), 0.08 * (y1 - y0)]
    axes = {"v": [0], "h": [1], "r": [0, 1]}[op]
    for _ in range(3):
        for ax in axes:
            best = None
            for k in range(-steps, steps + 1):
                t = list(c)
                t[ax] = c[ax] + span[ax] * k / steps
                e = g.symmetric_difference(_sym_copy(g, op, t)).area
                if best is None or e < best[0]:
                    best = (e, t[ax])
            c[ax] = best[1]
            span[ax] /= steps / 2
    return c


def symmetrize_glyph(ch, g):
    """Make g exactly symmetric under its letter's natural symmetry: find the
    best-matching axis/centre, then UNION g with its mirror/turn (union, not
    intersection, so no stroke gets thinner)."""
    for op in GLYPH_SYMMETRY.get(ch, ""):
        c = _best_centre(g, op)
        g = unary_union([g, _sym_copy(g, op, c)]).buffer(0)
    return g


def _find_font(size, name=None):
    """TrueType face (geometry.find_font), or the geometric stroke font for
    name "geo" (geo_font.py: straight strokes + arcs, tab-bulb serifs)."""
    if name == "geo":
        import geo_font

        return geo_font.GeoFont(size)
    return G.find_font(size, name)


def glyph_local(ch, font):
    if hasattr(font, "glyph"):  # geo_font.GeoFont: exact geometry, no tracing
        return font.glyph(ch)
    gl, gt, gr, gb = font.getbbox(ch)
    pad = 6
    w, h = int(gr - gl) + 2 * pad, int(gb - gt) + 2 * pad
    img = Image.new("L", (w, h), 0)
    ImageDraw.Draw(img).text((-gl + pad, -gt + pad), ch, fill=255, font=font)
    poly = G._trace_mask_polygons(img)
    ascent = font.getmetrics()[0]
    base_y = ascent - gt + pad
    cx = (gl + gr) / 2 - gl + pad
    if SYMMETRIC_GLYPHS and len(ch) == 1:
        poly = symmetrize_glyph(ch, poly)
    return affinity.translate(poly, -cx, -base_y)


HEART_CAP_FRAC = 1.0  # heart height / cap height


def ornament_local(kind, cap_h):
    """Separator piece at the join (end -> start of the name), in the same
    local frame, centred on the letter band."""
    cy = -cap_h / 2
    if kind == "dot":
        return Point(0, cy).buffer(0.22 * cap_h, quad_segs=32)
    if kind == "globe":
        # A plain filled disc -- deliberately NOT a copy of anyone's brand
        # mark, just a generic "planet" silhouette. The graticule/coastline
        # look comes from the etched background overlay (globe_etch.py)
        # crossing over it at its final placed position, not from special
        # ornament geometry here.
        return Point(0, cy).buffer(0.30 * cap_h, quad_segs=48)
    if kind == PLANET_LOGO_ORNAMENT:
        # The real Planet Labs brand ring, traced from a photo (see
        # trace_planet_logo.py) -- a SOLID disc as the physical cut piece
        # (their actual mark is a thin open ring, which at ornament scale
        # would be a fragile sliver; see planet_logo_ornament_artwork() for
        # the etched p + ring-outline detail that goes ON this disc, not
        # cut through it). Radius matches the "globe" ornament's so it
        # takes a comparable angular slice of the ring regardless of which
        # ornament kind is chosen.
        return Point(0, cy).buffer(0.30 * cap_h, quad_segs=48)
    if kind == "star":
        pts = []
        for k in range(10):
            r = 0.30 * cap_h if k % 2 == 0 else 0.14 * cap_h
            a = -math.pi / 2 + k * math.pi / 5
            pts.append((r * math.cos(a), cy + r * math.sin(a)))
        return Polygon(pts).buffer(0.03 * cap_h).buffer(-0.03 * cap_h)
    if kind == "rocket":
        # Nose pointing OUTWARD (local -y, same direction letter caps extend --
        # the direction place() maps to "away from the hub" at th=0), flared
        # fin skirt toward the hub. No window cutout -- an interior hole here
        # would be a separate ring at ornament scale, fragile for no reason
        # since the window is purely decorative; save it for an etched detail.
        half_w = 0.15 * cap_h
        nose_h = 0.16 * cap_h
        body_h = 0.22 * cap_h
        fin_h = 0.12 * cap_h
        fin_out = half_w + 0.10 * cap_h
        y1, y2, y3 = nose_h, nose_h + body_h, nose_h + body_h + fin_h
        pts = [
            (0, 0),
            (half_w, y1),
            (half_w, y2),
            (fin_out, y3),
            (-fin_out, y3),
            (-half_w, y2),
            (-half_w, y1),
        ]
        p = Polygon(pts).buffer(0.02 * cap_h).buffer(-0.02 * cap_h)
        b = p.bounds
        return affinity.translate(p, -(b[0] + b[2]) / 2, cy - (b[1] + b[3]) / 2)
    if kind == "satellite":
        # Body + two flanking solar-panel wings, all touching (one fused
        # piece, no gaps to bridge). Symmetric, so it reads the same
        # whichever way the ring rotation flips it.
        bw, bh = 0.16 * cap_h, 0.30 * cap_h
        ww, wh = 0.20 * cap_h, 0.42 * cap_h
        body = box(-bw / 2, -bh / 2, bw / 2, bh / 2)
        wing_r = box(bw / 2, -wh / 2, bw / 2 + ww, wh / 2)
        wing_l = box(-bw / 2 - ww, -wh / 2, -bw / 2, wh / 2)
        p = unary_union([body, wing_r, wing_l])
        b = p.bounds
        return affinity.translate(p, -(b[0] + b[2]) / 2, cy - (b[1] + b[3]) / 2)
    # heart (point toward the hub)
    pts = []
    for k in range(120):
        t = 2 * math.pi * k / 120
        x = 16 * math.sin(t) ** 3
        y = (
            13 * math.cos(t)
            - 5 * math.cos(2 * t)
            - 2 * math.cos(3 * t)
            - math.cos(4 * t)
        )
        pts.append((x, -y))
    p = Polygon(pts)
    # as tall as the letters it separates (Alex: 0.55 cap came out ~1cm,
    # "too tiny")
    s = HEART_CAP_FRAC * cap_h / (p.bounds[3] - p.bounds[1])
    p = affinity.scale(p, s, s, origin=(0, 0))
    b = p.bounds
    return affinity.translate(p, -(b[0] + b[2]) / 2, cy - (b[1] + b[3]) / 2)


def _center_text_word(word, font, baseline_mm, ppm):
    """One word as a single fused piece: glyph_local's usual trace (already
    handles multi-char strings, x=0 centred, y=0 baseline) unioned with a
    thin support bar just below the baseline, spanning the word's width, so
    letters that don't touch each other (most short words at a small size)
    still come out of the pocket as one piece instead of loose chips."""
    poly = glyph_local(word, font)
    minx, miny, maxx, maxy = poly.bounds
    # Overlap a couple px PAST the nominal baseline (maxy, the glyph's own
    # lowest point) rather than starting exactly at y=0: font metrics leave
    # a hairline gap between the rendered glyph bottom and the "baseline" y
    # coordinate, which left the bar touching nothing and every letter its
    # own disconnected piece instead of one fused word.
    bar = box(minx - 2 * ppm, maxy - 2, maxx + 2 * ppm, baseline_mm * ppm)
    return unary_union([poly, bar])


def center_text_block(rp, ppm):
    """Fit `rp.center_text` (two words, e.g. ("THE","PAULS")) into a single
    local-frame polygon (x=0, y=0 at its own vertical centre), shrinking cap
    height until it clears the caller's fit check. Returns None if it can't
    fit even at center_text_cap_min_mm."""
    cap_mm = rp.center_text_cap_mm
    w1, w2 = rp.center_text
    while cap_mm >= rp.center_text_cap_min_mm:
        ref = _find_font(1000, rp.font)
        cap_ratio = (ref.getbbox("H")[3] - ref.getbbox("H")[1]) / 1000.0
        font = _find_font(max(8, int(round(cap_mm * ppm / cap_ratio))), rp.font)
        b1 = _center_text_word(w1, font, rp.center_text_baseline_mm, ppm)
        b2 = _center_text_word(w2, font, rp.center_text_baseline_mm, ppm)
        gap = rp.center_text_gap_mm * ppm
        dy = b2.bounds[1] - gap - b1.bounds[3]
        b1s = affinity.translate(b1, 0, dy)
        block = unary_union([b1s, b2])
        minx, miny, maxx, maxy = block.bounds
        block = affinity.translate(block, -(minx + maxx) / 2, -(miny + maxy) / 2)
        r = max(math.hypot(x, y) for x, y in block.convex_hull.exterior.coords)
        yield cap_mm, r, block
        cap_mm -= 1


def _center_font(rp, cap_mm, ppm):
    ref = _find_font(1000, rp.font)
    cap_ratio = (ref.getbbox("H")[3] - ref.getbbox("H")[1]) / 1000.0
    return _find_font(max(8, int(round(cap_mm * ppm / cap_ratio))), rp.font)


def _word_advances(word, font, track_px):
    """Per-letter local glyphs plus each letter's centre offset along the
    word (x=0 at the word's centre), using the font's own advances plus
    `track_px` extra between letters."""
    glyphs = [glyph_local(ch, font) for ch in word]
    adv = [font.getlength(ch) for ch in word]
    pos, x = [], 0.0
    for a in adv:
        pos.append(x + a / 2)
        x += a + track_px
    total = x - track_px
    return glyphs, [p - total / 2 for p in pos], total


def center_letters(rp, ppm, r_h, C):
    """Loose individual center letters (center_style "flat" or "ring"),
    placed in world coords around hub centre C, largest cap that fits.
    Returns (list of letter polygons, cap_mm) or (None, None) if nothing
    fits down to center_text_cap_min_mm."""
    min_gap = rp.center_min_gap_mm * ppm
    cap_mm = rp.center_loose_cap_max_mm
    while cap_mm >= rp.center_text_cap_min_mm:
        track_mm = rp.center_track_mm
        while track_mm <= rp.center_track_mm + 10:
            out = _center_layout(rp, ppm, r_h, C, cap_mm, track_mm * ppm)
            if out is None:
                break  # doesn't fit at this cap -- widening won't help
            gaps = [
                min(a.distance(b) for j, b in enumerate(out) if j != i)
                for i, a in enumerate(out)
            ]
            if min(gaps) >= min_gap:
                return out, cap_mm
            track_mm += 0.5
        cap_mm -= 0.5
    return None, None


def _center_layout(rp, ppm, r_h, C, cap_mm, track):
    """One layout attempt at a given cap height and tracking; None if it
    doesn't fit the hub."""
    limit = r_h * rp.center_text_fit_frac
    font = _center_font(rp, cap_mm, ppm)
    cap = cap_mm * ppm
    out = []
    if rp.center_style == "flat" and len(rp.center_text) == 1:
        # one line (a long family name on its own), centred on the hub
        g1, x1, _t1 = _word_advances(rp.center_text[0], font, track)
        out = [affinity.translate(g, C[0] + x, C[1] + cap / 2) for g, x in zip(g1, x1)]
        r = max(
            math.hypot(px - C[0], py - C[1])
            for g in out for px, py in g.convex_hull.exterior.coords
        )
        return out if r <= limit else None
    if rp.center_style == "ring" and len(rp.center_text) == 1:
        # one word wrapped all the way round the hub, a heart between its
        # last and first letters, everything evenly spaced, feet to the
        # centre: a spinning disc with no "up" (RING_SPEC.md §16.3)
        r_m = limit - cap / 2
        word = rp.center_text[0]
        gl = [glyph_local(ch, font) for ch in word] + [ornament_local("heart", cap)]
        wd = [font.getlength(ch) for ch in word]
        wd.append(gl[-1].bounds[2] - gl[-1].bounds[0])
        gap = (2 * math.pi * r_m - sum(wd)) / len(gl)
        if gap < track or r_m - cap / 2 <= 0:
            return None
        # word centred on 12 o'clock, heart opposite it at 6
        x = -(sum(wd[:-1]) + gap * (len(word) - 1)) / 2
        for g, w in zip(gl, wd):
            out.append(place(g, (x + w / 2) / r_m, r_m - cap / 2, C))
            x += w + gap
        return out
    w1, w2 = rp.center_text
    if rp.center_style == "flat":
        # the strip between the two lines is wood too -- same floor applies
        gap = max(rp.center_text_gap_mm, rp.center_min_gap_mm) * ppm + 1
        g1, x1, _t1 = _word_advances(w1, font, track)
        g2, x2, _t2 = _word_advances(w2, font, track)
        # line 1 baseline above centre, line 2 baseline below
        y1 = -gap / 2
        y2 = gap / 2 + cap
        out = [affinity.translate(g, C[0] + x, C[1] + y1) for g, x in zip(g1, x1)]
        out += [affinity.translate(g, C[0] + x, C[1] + y2) for g, x in zip(g2, x2)]
        r = max(
            math.hypot(px - C[0], py - C[1])
            for g in out for px, py in g.convex_hull.exterior.coords
        )
        return out if r <= limit else None
    else:  # "ring"
        r_m = limit - cap / 2  # shared midline, as far out as fits
        ok = True
        for wi, word in enumerate((w1, w2)):
            # the bottom word is flipped to read upright -- except on a split
            # hub, where both words are laid out alike (feet to the centre,
            # reading clockwise): 180 deg copies of each other, so neither is
            # "up" until the disc is turned (RING_SPEC.md §16.2)
            flip = wi == 1 and not rp.center_split
            seq = word[::-1] if flip else word
            gl, xs, total = _word_advances(seq, font, track)
            if total / r_m > math.radians(160):
                ok = False
                break
            th0 = math.pi if wi == 1 else 0.0
            r_base = r_m - cap / 2 if not flip else r_m + cap / 2
            for g, x in zip(gl, xs):
                if flip:
                    g = affinity.rotate(g, 180, origin=(0, 0))
                out.append(place(g, th0 + x / r_m, r_base, C))
        return out if ok and r_m - cap / 2 > 0 else None


def _center_split_seams(letters, C, r_h, ppm, rp, seams, rng):
    """Seams for a split hub (see build_ring); appends to `seams`, returns
    the hub-circle landing angles (the hub arcs split there)."""
    pts = [p for g in letters for p in g.exterior.coords]
    r_in = min(math.hypot(x - C[0], y - C[1]) for x, y in pts)
    r_i = r_in - rp.center_band_gap_mm * ppm  # inner circle, band_gap inside the letters
    clear = rp.center_min_gap_mm * ppm

    def ang(p):
        return ang_of(p, C) % (2 * math.pi)

    # letters' angular extents (clockwise from 12 o'clock), sorted round
    ext = []
    for g in letters:
        c = ang(g.centroid.coords[0])
        rel = [math.remainder(ang(p) - c, 2 * math.pi) for p in g.exterior.coords]
        ext.append(((c + min(rel)) % (2 * math.pi), (c + max(rel)) % (2 * math.pi), c))
    ext.sort(key=lambda e: e[2])
    # every gap between neighbouring letters: (mid angle, width at the letters)
    r_mid = (r_in + r_h) / 2
    gaps = []
    for k in range(len(ext)):
        a1 = ext[k][1]
        a2 = ext[(k + 1) % len(ext)][0]
        wdt = (a2 - a1) % (2 * math.pi)
        gaps.append(((a1 + wdt / 2) % (2 * math.pi), wdt * r_in))
    # the divider runs through the two widest gaps nearest 3 and 9 o'clock
    def nearest(target):
        return min(gaps, key=lambda gp: (abs(math.remainder(gp[0] - target, 2 * math.pi)), -gp[1]))

    phR, phL = nearest(math.pi / 2)[0], nearest(3 * math.pi / 2)[0]
    def at(r, phi):
        return (C[0] + r * math.sin(phi), C[1] - r * math.cos(phi))

    def radial(phi):
        a, b = at(r_i, phi), at(r_h, phi)
        n = max(4, int(math.dist(a, b) / (2 * ppm)))
        return [(a[0] + (b[0] - a[0]) * t / n, a[1] + (b[1] - a[1]) * t / n) for t in range(n + 1)]

    # band radials: through any gap wide enough for a seam with `clear` of
    # wood each side -- and, since loose centre letters sit only ~5mm apart,
    # THROUGH letters too, like the outer ring's caps: the seam stops at the
    # letter's foot and starts again at its head (the letter piece joins the
    # two sides). Only letters the radial crosses as one solid stroke (no
    # bowl or mouth to leave a sliver in), spaced for normal piece widths.
    solids_c = [solid_of(g) for g in letters]
    cand = [(a, "gap") for a, wdt in gaps if wdt >= 2 * clear + 2 * ppm]
    # scan each letter's angular extent: a radial is usable where it crosses
    # the glyph as ONE stroke (no counter, no second arm) and passes no other
    # part of it closer than `clear` (a near miss leaves a sliver); the middle
    # of each usable run -- a stem's centre line -- becomes a candidate
    step = math.radians(0.5)
    # a one-word ring ends with its heart (_center_layout): no radial through
    # it -- through the middle it runs out of the notch between the lobes,
    # gets chopped there, and the band pieces either side merge
    heart = len(letters) - 1 if len(rp.center_text) == 1 else None
    for li, g in enumerate(letters):
        if li == heart:
            continue
        sg = solid_of(g)
        c = ang(g.centroid.coords[0])
        rel = [math.remainder(ang(p) - c, 2 * math.pi) for p in g.exterior.coords]
        run = []
        for k in range(int((max(rel) - min(rel)) / step) + 2):
            phi = (c + min(rel) + k * step) % (2 * math.pi)
            pts_r = radial(phi)
            ls = LineString(pts_r)
            hit, hs = ls.intersection(g), ls.intersection(sg)
            ok = (hit.geom_type == "LineString" and not hit.is_empty
                  and hs.geom_type == "LineString" and abs(hit.length - hs.length) < 0.5
                  and not spoke_near_misses(pts_r, [g], clear))
            if ok:
                run.append(phi)
            if run and (not ok or k == int((max(rel) - min(rel)) / step) + 1):
                if len(run) >= 2:
                    cand.append((run[len(run) // 2], "letter"))
                run = []
    radials = [phR, phL]
    # the inner circle's arcs between landing points host the band's tabs
    # (the band radials themselves are mostly too short, broken by letters):
    # keep every arc long enough for a tab clear of both 7mm junction zones
    arc_min = rp.center_arc_min_mm * ppm
    cross = 2 * r_i > 64 * ppm
    fixed = [phR, phL]
    cross_at = []

    def clear_inner(phi):
        return all(abs(math.remainder(phi - m, 2 * math.pi)) * r_i >= arc_min for m in fixed)

    amin = arc_min / r_i
    for start, stop in ((phR, phL), (phL, phR)):  # each half, clockwise
        # pick the radials that make the widest band piece as narrow as
        # possible, every landing >= amin from its neighbours and the cross
        # seam's marks (a DP over the candidates, sorted round the half)
        span = (stop - start) % (2 * math.pi)
        offs = [0.0] + sorted({(c - start) % (2 * math.pi) for c, _kind in cand
                               if amin <= (c - start) % (2 * math.pi) <= span - amin
                               and clear_inner(c % (2 * math.pi))}) + [span]
        n = len(offs)

        def dp(order):  # best[j] = (narrowest widest piece from order[0] to j, prev)
            best = {order[0]: (0.0, None)}
            for jj, j in enumerate(order[1:], 1):
                best[j] = (math.inf, None)
                for i in order[:jj]:
                    if abs(offs[j] - offs[i]) >= amin and best[i][0] < math.inf:
                        w = max(best[i][0], abs(offs[j] - offs[i]))
                        if w < best[j][0]:
                            best[j] = (w, i)
            return best

        fwd, bwd = dp(list(range(n))), dp(list(range(n - 1, -1, -1)))

        def path(best, j, stop):
            out = []
            while j is not None and j != stop:
                out.append(j)
                j = best[j][1]
            return out

        # the inner disc's cross seam lands on one of the chosen radials; it
        # must leave both inner pieces >= center_cross_min_deg wide (a narrow
        # one gets tabs biting in from every side and is absorbed as a sliver,
        # leaving one inner piece for the whole half), so the DP is forced
        # through such a radial when one exists
        lo = math.radians(rp.center_cross_min_deg)
        musts = [m for m in range(1, n - 1) if lo <= offs[m] <= span - lo] if cross else []
        if musts:
            m = min(musts, key=lambda m: (max(fwd[m][0], bwd[m][0]), abs(offs[m] - span / 2)))
            picked = path(fwd, m, 0) + path(bwd, bwd[m][1], n - 1)
            cross_at.append((start + offs[m]) % (2 * math.pi))
        else:
            picked = path(fwd, fwd[n - 1][1], 0)
            if picked and cross:
                cross_at.append((start + offs[min(picked, key=lambda k: abs(offs[k] - span / 2))])
                                % (2 * math.pi))
        radials += [(start + offs[k]) % (2 * math.pi) for k in picked]
    radials = sorted(set(radials))
    letter_union_c = unary_union(solids_c)
    for phi in radials:
        sm = dict(pts=radial(phi), kind="hubradial", ends=("J", "J"))
        if LineString(sm["pts"]).intersects(letter_union_c):
            import globe_land as GL_

            for part in GL_.chop_seams([sm], letter_union_c, ppm, min_mm=2.0, approach_mm=0.5):
                part["kind"] = "hubradial"
                seams.append(part)
        else:
            seams.append(sm)
    # divider through the inner disc (via the centre), plus the cross seam
    # square to it when the inner disc's halves would be oversized
    inner_marks = list(radials)
    for phi in (phR, phL):
        a = at(r_i, phi)
        n = max(4, int(r_i / (2 * ppm)))
        seams.append(dict(pts=[(a[0] + (C[0] - a[0]) * t / n, a[1] + (C[1] - a[1]) * t / n)
                               for t in range(n + 1)], kind="divider", ends=("J", "J")))
    if cross:
        for phi in cross_at:
            a = at(r_i, phi)
            n = max(4, int(r_i / (2 * ppm)))
            seams.append(dict(pts=[(a[0] + (C[0] - a[0]) * t / n, a[1] + (C[1] - a[1]) * t / n)
                                   for t in range(n + 1)], kind="divider", ends=("J", "J")))
            inner_marks.append(phi)
    marks = sorted(set(round(m, 9) for m in inner_marks))
    for q in range(len(marks)):
        seams.insert(0, dict(pts=arc_pts(C, r_i, marks[q], marks[(q + 1) % len(marks)], 2 * ppm),
                             kind="innerarc", ends=("J", "J")))
    return [p % (2 * math.pi) for p in radials]


def _center_flat_grid_seams(letters, C, r_h, ppm, rp, seams):
    """Seams for a split hub under FLAT center text (center_split with
    center_style "flat"): a brick-like grid, not a pinwheel. Out of a
    pinwheel's centre every spoke heading across a W or N runs through its
    open notches, so the wedge under them can never split (BROWN: one
    oversized piece on every seed). Instead:

      * a tabbed chord across the disc center_flat_band_gap_mm above the
        text and one the same distance below, and more chords splitting the caps
        above/below into rows about center_row_mm tall (Alex: one row each
        side made "long and thin" pieces);
      * each row gets its own vertical seams (bricks, staggered): through
        letter gaps or a single letter stem in the text band, anywhere in
        the other rows -- a DP per row keeps its largest piece as small as
        possible, with every chord segment >= center_arc_min_mm long (room
        for a tab) and a seam either meeting a neighbour row's seam exactly
        or landing that far from it.

    Appends to `seams`; returns the hub-circle landing angles."""
    import globe_land as GL_

    U = unary_union(letters)
    lx0, ly0, lx1, ly1 = U.bounds
    # wood above/below the text: a band piece is mostly letter pocket, so
    # with 6mm strips no part of it was 14mm thick -- _vg_absorb took it for
    # a sliver and merged it into the row next to it
    gap = rp.center_flat_band_gap_mm * ppm
    yt, yb = ly0 - gap, ly1 + gap  # image y runs down: yt above the text
    clear = rp.center_min_gap_mm * ppm
    seg_min = rp.center_arc_min_mm * ppm
    top_y, bot_y = C[1] - r_h, C[1] + r_h

    def half(y):  # chord half-length at height y
        return math.sqrt(max(0.0, r_h ** 2 - (y - C[1]) ** 2))

    # chord levels: rows ~center_row_mm tall above and below the text band
    row = rp.center_row_mm * ppm
    n_up = max(1, round((yt - top_y) / row))
    n_dn = max(1, round((bot_y - yb) / row))
    ups = [yt - k * (yt - top_y) / n_up for k in range(1, n_up)]  # moving up
    dns = [yb + k * (bot_y - yb) / n_dn for k in range(1, n_dn)]  # moving down
    chords = {y: [] for y in [yt, yb] + ups + dns}  # y -> seam x's landing on it

    def ok_band(x):
        ln = LineString([(x, yt), (x, yb)])
        hits = [g for g in letters if ln.intersects(g)]
        if not hits:  # a gap seam running alongside a letter leaves a strip
            return U.distance(ln) >= max(clear, 7.0 * ppm)
        if len(hits) > 1:
            return False
        g = hits[0]
        h, hs = ln.intersection(g), ln.intersection(solid_of(g))
        pts = [(x, yt + t * (yb - yt) / 30) for t in range(31)]
        if not (h.geom_type == "LineString" and hs.geom_type == "LineString"
                and abs(h.length - hs.length) < 0.5 and not spoke_near_misses(pts, letters, clear)):
            return False
        # and it must leave the letter steeply: crossing a serif bulb and then
        # running down beside a slanted leg (W) leaves a wedge-thin strip
        hy0, hy1 = h.bounds[1], h.bounds[3]
        for k in range(1, 21):
            t = k * 0.5 * ppm
            for yy in (hy0 - t, hy1 + t):
                if yt <= yy <= yb and g.distance(Point(x, yy)) < 0.6 * t:
                    return False
        return True

    def fits_chord(x, y):
        # the end bit, out to the (untabbed) hub circle, needn't carry a tab:
        # both its pieces are held by their other seams
        if abs(x - C[0]) > half(y) - rp.center_end_seg_mm * ppm:
            return False
        return all(abs(x - q) < 0.5 or abs(x - q) >= seg_min for q in chords[y])

    disc = Point(C).buffer(r_h, quad_segs=128)

    def do_row(ya, yb_, band=False):
        """One row between ya (above) and yb_ (below); a y that is not a
        chord is the hub circle. Picks its vertical seams, records them on
        the chords, returns [(x, y_from, y_to)]."""
        reg = disc.intersection(box(C[0] - r_h, ya, C[0] + r_h, yb_))
        on = [y for y in (ya, yb_) if y in chords]
        xr = max(half(y) for y in on)
        cands, run = [], []
        x = C[0] - xr
        while x <= C[0] + xr:
            ok = all(fits_chord(x, y) for y in on) and (not band or ok_band(x))
            if ok:
                run.append(x)
            if run and (not ok or x + 0.5 * ppm > C[0] + xr):
                if band:  # the middle of each clean run (a gap or a stem)
                    cands.append(run[len(run) // 2])
                else:  # open wood: anywhere, every 1mm
                    cands.extend(run[::2])
                run = []
            x += 0.5 * ppm
        if not band:  # carrying a neighbour row's seam straight on is allowed too
            cands = sorted(set(cands) | {q for y in on for q in chords[y]
                                         if all(fits_chord(q, y2) for y2 in on)})
        pts = [C[0] - xr] + cands + [C[0] + xr]
        n = len(pts)

        def area(a, b):
            # a piece's cost: its area, or its width squared (x0.8) when it is
            # long and flat -- area alone left 70 x 26mm planks (Alex: "so
            # long and thin")
            return max(reg.intersection(box(a, ya, b, yb_)).area, 0.8 * (b - a) ** 2)

        best = [(0.0, None)] + [(math.inf, None)] * (n - 1)
        for j in range(1, n):
            for i in range(j):
                if best[i][0] == math.inf:
                    continue
                if 0 < i and j < n - 1 and pts[j] - pts[i] < seg_min:
                    continue
                w = max(best[i][0], area(pts[i], pts[j]))
                if w < best[j][0]:
                    best[j] = (w, i)
        sel, j = [], best[n - 1][1]
        while j:
            sel.append(pts[j])
            j = best[j][1]
        out = []
        for x in sorted(sel):
            for y in on:
                if all(abs(x - q) >= 0.5 for q in chords[y]):
                    chords[y].append(x)
            y0 = ya if ya in chords else C[1] - half_x(x)
            y1 = yb_ if yb_ in chords else C[1] + half_x(x)
            out.append((x, y0, y1))
        return out

    def half_x(x):
        return math.sqrt(max(0.0, r_h ** 2 - (x - C[0]) ** 2))

    verts = [(v, True) for v in do_row(yt, yb, band=True)]
    levels_up = [yt] + ups + [top_y]
    for y_lo, y_hi in zip(levels_up, levels_up[1:]):  # rows above, inner first
        verts += [(v, False) for v in do_row(y_hi, y_lo)]
    levels_dn = [yb] + dns + [bot_y]
    for y_hi, y_lo in zip(levels_dn, levels_dn[1:]):  # rows below, inner first
        verts += [(v, False) for v in do_row(y_hi, y_lo)]

    def line(a, b):
        k = max(4, int(math.dist(a, b) / (2 * ppm)))
        return [(a[0] + (b[0] - a[0]) * t / k, a[1] + (b[1] - a[1]) * t / k) for t in range(k + 1)]

    landings = []
    for y, xs in chords.items():  # every chord, split where seams land (one tab each)
        xs = [C[0] - half(y)] + sorted(xs) + [C[0] + half(y)]
        for a, b in zip(xs, xs[1:]):
            seams.append(dict(pts=line((a, y), (b, y)), kind="divider", ends=("J", "J")))
        landings += [ang_of((xs[0], y), C) % (2 * math.pi), ang_of((xs[-1], y), C) % (2 * math.pi)]
    solids = unary_union([solid_of(g) for g in letters])
    for (x, y0, y1), in_band in verts:
        sm = dict(pts=line((x, y0), (x, y1)), kind="hubradial", ends=("J", "J"))
        if in_band and LineString(sm["pts"]).intersects(U):
            for part in GL_.chop_seams([sm], solids, ppm, min_mm=2.0, approach_mm=0.5):
                part["kind"] = "hubradial"
                seams.append(part)
        else:
            seams.append(sm)
        for y in (y0, y1):
            if y not in chords:
                landings.append(ang_of((x, y), C) % (2 * math.pi))
    return sorted(landings)


def spoke_near_misses(pts, letters, thresh_px):
    """Interior points where a cut passes a letter WITHOUT touching it,
    closer than thresh_px: a strict local minimum of distance-to-letter that
    is > 0. A clean crossing drives the distance to 0 instead, so it never
    registers -- only a near miss does, which is what leaves a sliver
    between the cut and the letter, or a pinched neck at a letter corner.
    Returns [(index, letter_index, distance_px)]."""
    out = []
    for li, g in enumerate(letters):
        d = [g.distance(Point(q)) for q in pts]
        for i in range(1, len(pts) - 1):
            if 0 < d[i] < thresh_px and d[i] <= d[i - 1] and d[i] < d[i + 1]:
                out.append((i, li, d[i]))
    return out


def _pinwheel_spoke(P, phi, b, twist, tw_deg, r_h):
    na = out_vec(phi + twist * math.radians(tw_deg))
    return G._vg_bez(P, na, b, out_vec(phi + math.pi), 0.45 * r_h)


def plan_center_pinwheel(letters, C, r_h, k_hub, ppm, min_gap_mm, rng,
                         jitter_deg=12, tw_options=(40, 50, 60)):
    """Hub spokes for loose center text. With the spokes converging at the
    hub's exact centre, flat two-line text puts that point in the gap
    between the lines, so every spoke threads along the gap and grazes
    letters (slivers, pinched necks, and wedge tips meeting in open wood) --
    no rotation of the pinwheel avoids that (measured: 0 of 720). Instead:

      * converge INSIDE a center letter near the middle -- each spoke then
        starts on that letter's edge (the part inside is cut away with it);
      * nudge each spoke's landing angle independently by up to jitter_deg
        so it doesn't pass any letter within min_gap_mm without touching it;
      * reject layouts whose spokes cross each other.

    Tries convergence letters nearest the centre first and a seeded order
    of rotations/twists; returns the first fully clean layout, else the
    one with the fewest near-misses. Returns (P, phis, twist, tw_deg).

    A spoke that runs through another letter's open notch (e.g. down between
    an H's legs) also counts as a miss -- it leaves finger-thin strips (seen
    as unrepairable QA failures with the symmetric glyphs). The convergence
    letter's own notches are handled by _snap_notch_ends instead."""
    thresh = min_gap_mm * ppm
    # open notches (between E/H/M legs, inside C/S/U mouths): a spoke running
    # through one leaves finger-thin strips either side, so it counts as a miss
    notches = [(g, g.convex_hull, g.convex_hull.difference(g.buffer(0.5 * ppm)))
               for g in letters]
    notches = [(g, h, n) for g, h, n in notches if not n.is_empty]

    def notch_hits(line, conv):
        # cheap hull test first; the exact notch overlap only where it can hit
        return sum(1 for g, h, n in notches
                   if g is not conv and line.intersects(h)
                   and line.intersection(n).length > 1.0 * ppm)

    import time as _t
    t_end = _t.monotonic() + 20.0  # search budget: keep the best layout found

    cands = sorted(
        (g for g in letters if g.distance(Point(C)) < 0.4 * r_h),
        key=lambda g: g.distance(Point(C)),
    ) or [min(letters, key=lambda g: g.distance(Point(C)))]
    combos = [(tw, twist, a) for tw in tw_options for twist in (-1, 1)
              for a in range(0, 360 // k_hub, 4)]
    rng.shuffle(combos)
    best = None
    for g in cands:
        rpt = g.representative_point()
        P = (rpt.x, rpt.y)
        for tw, twist, a_deg in combos:
            phis, bad = [], 0
            for s_ in range(k_hub):
                nom = math.radians(a_deg) + 2 * math.pi * s_ / k_hub
                pick, pick_n = nom, None
                for dd in sorted(range(-jitter_deg, jitter_deg + 1), key=abs):
                    phi = nom + math.radians(dd)
                    b = (C[0] + r_h * math.sin(phi), C[1] - r_h * math.cos(phi))
                    sp = _pinwheel_spoke(P, phi, b, twist, tw, r_h)
                    n = len(spoke_near_misses(sp, letters, thresh)) + notch_hits(LineString(sp), g)
                    if pick_n is None or n < pick_n:
                        pick, pick_n = phi, n
                    if n == 0:
                        break
                phis.append(pick)
                bad += pick_n
            lines = [
                LineString(_pinwheel_spoke(
                    P, ph, (C[0] + r_h * math.sin(ph), C[1] - r_h * math.cos(ph)), twist, tw, r_h))
                for ph in phis
            ]
            if any(lines[i].crosses(lines[j]) for i in range(k_hub) for j in range(i + 1, k_hub)):
                continue
            if best is None or bad < best[0]:
                best = (bad, (P, phis, twist, tw))
            if bad == 0:
                return best[1]
            if best is not None and _t.monotonic() > t_end:
                return best[1]
    return best[1]


def solid_of(g):
    if isinstance(g, MultiPolygon):
        g = max(g.geoms, key=lambda q: q.area)
    return Polygon(g.exterior)


# --------------------------------------------------------------------------
# polar placement
# --------------------------------------------------------------------------


def out_vec(th):  # outward unit vector at clockwise-from-top angle th (y down)
    return (math.sin(th), -math.cos(th))


def place(poly, th, r_base, C):
    c, s = math.cos(th), math.sin(th)
    return affinity.affine_transform(
        poly, [c, -s, s, c, C[0] + r_base * s, C[1] - r_base * c]
    )


def place_upright(poly, th, r_base, C, anchor_y):
    """Like place(), but TRANSLATION ONLY -- no rotation. For decoration
    that should stay upright wherever it lands on the ring, unlike a
    letter (which must rotate to keep reading around the circle): the
    planet_logo ornament's etched p+ring art is a brand mark, not a
    spelled-out letter, so it shouldn't flip upside-down just because the
    join happens to sit in the bottom half (§2). `anchor_y` is the local y
    of the point that should land exactly at the target world position
    (e.g. cy = -cap_h/2, ornament_local's own disc-center convention) --
    without it, translating by the placed local origin (0,0) instead of
    the piece's actual center would offset the art from the disc it's
    decorating."""
    wx, wy = place(Point(0, anchor_y), th, r_base, C).coords[0]
    return affinity.translate(poly, wx, wy - anchor_y)


def xf_pt(p, th, r_base, C):
    c, s = math.cos(th), math.sin(th)
    x, y = p
    return (c * x - s * y + C[0] + r_base * s, s * x + c * y + C[1] - r_base * c)


def xf_vec(v, th):
    c, s = math.cos(th), math.sin(th)
    return (c * v[0] - s * v[1], s * v[0] + c * v[1])


def ang_of(p, C):
    return math.atan2(p[0] - C[0], -(p[1] - C[1])) % (2 * math.pi)


def ang_extent(poly, r_base):
    """(left, right) angular half-extents of a local glyph placed at r_base."""
    xs = poly.exterior.coords
    a = [math.atan2(x, r_base - y) for x, y in xs]
    return -min(a), max(a)


def fit_ring(words, rp: RingParams, ppm):
    """Pick the largest cap height (<= cap_h_max) whose ring layout keeps
    every adjacent letter pair >= min_gap apart (the tight side is the INNER,
    converging side) and leaves a hub ring + hub disc. Returns a layout dict.

    `words` is a single word (str) or a list of words: each word's letters are
    followed by one ornament slot (the same rp.ornament, e.g. a heart), so
    multiple names read as WORD1 <3 WORD2 <3 WORD3 <3 (back to WORD1). A
    single-word `words` reproduces the original single-heart-at-the-join
    layout exactly (one word -> one trailing ornament)."""
    if isinstance(words, str):
        words = [words]
    words = [w.upper() for w in words]
    Rp = rp.diameter_mm / 2 * ppm
    Ro = Rp - (rp.frame_mm + rp.rim_mm) * ppm
    h_mm = rp.cap_h_max_mm
    is_logo_font = rp.font == PLANET_LOGO_FONT
    if is_logo_font:
        _logo_glyphs, _logo_cap_ref_px, _ = _load_planet_logo_glyphs()
    else:
        ref = _find_font(1000, rp.font)
        cap_ratio = (ref.getbbox("H")[3] - ref.getbbox("H")[1]) / 1000.0
    while True:
        cap = h_mm * ppm
        if is_logo_font:
            font = None  # no system font in play; nothing downstream reads
            # `font` when is_logo_font, except L["font_size"] below.
            logo_scale = cap / _logo_cap_ref_px
        else:
            font = _find_font(max(10, int(round(cap / cap_ratio))), rp.font)
            cap = font.getbbox("H")[3] - font.getbbox("H")[1]
        Rin = Ro - cap

        def letter_glyph(c):
            if is_logo_font:
                key = c.lower()
                if key not in _logo_glyphs:
                    raise SystemExit(
                        f"planet_logo font has no traced glyph for {c!r} -- "
                        "only p,l,a,n,e,t,. were traced (see "
                        "scripts/trace_planet_logo.py)"
                    )
                g = affinity.scale(
                    _logo_glyphs[key], logo_scale, logo_scale, origin=(0, 0)
                )
                return g.simplify(rp.outline_smooth_px)
            # Pixel-traced outlines are 1px staircases. Upright (banner)
            # they're collinear runs the emitter merges, but ROTATED onto the
            # ring every stair becomes a 0.1-0.2mm zig-zag move: thousands of
            # micro-moves and near-reversals that stall GRBL and flicker the
            # laser. Simplify in the local (unrotated) frame first so rotated
            # edges are clean lines.
            return glyph_local(c, font).simplify(rp.outline_smooth_px)

        orn_kinds = (
            list(rp.ornament)
            if isinstance(rp.ornament, (list, tuple))
            else [rp.ornament]
        )
        locs, labels, word_of = [], [], []
        for i, w in enumerate(words):
            mirrored = i in rp.mirror_words
            # Reversed iteration order + a 180 local pre-rotation together
            # (not either alone) is what keeps a mirrored word reading
            # correctly left-to-right: increasing th runs clockwise, which
            # is left-to-right across the TOP of the ring but right-to-left
            # across the BOTTOM, so un-reversing the char order compensates
            # for that side's direction -- while the 180 rotation is what
            # actually flips each letter's cap from outward to inward (see
            # RingParams.mirror_words). Baking the rotation into the glyph
            # here (rather than a separate placement function) keeps
            # ang_extent/place() downstream unchanged: they just see
            # whatever polygon is in `locs`.
            for c in (w[::-1] if mirrored else w):
                if c.isspace():
                    continue
                g = letter_glyph(c)
                if mirrored:
                    g = affinity.rotate(g, 180, origin=(0, 0))
                locs.append(g)
                labels.append(c)
                word_of.append(i)
            orn = orn_kinds[i % len(orn_kinds)]
            if orn:
                o_ = ornament_local(orn, cap)
                if rp.ornament_scale != 1.0:
                    o_ = affinity.scale(o_, rp.ornament_scale, rp.ornament_scale,
                                        origin=(0, -cap / 2))
                locs.append(o_)
                labels.append("*")
                word_of.append(None)
        n = len(locs)
        ext = [ang_extent(solid_of(g), Rin) for g in locs]
        span = sum(l + r for l, r in ext)
        beta = (2 * math.pi - span) / n  # equal angular gap
        gaps_ang = [beta] * n
        if rp.pack_words and rp.ornament:
            wide = [k for k in range(n) if labels[k] == "*" or labels[(k + 1) % n] == "*"]
            tight = 1.3 * rp.min_gap_mm * ppm / Rin
            spare = 2 * math.pi - span - tight * (n - len(wide))
            if wide and spare / len(wide) > tight:
                gaps_ang = [spare / len(wide) if k in wide else tight for k in range(n)]
        # hub sizing
        r_h = max(rp.hub_r_min_mm * ppm, n * rp.hub_arc_mm * ppm / (2 * math.pi))
        r_h = min(r_h, Rin - rp.hub_ring_min_mm * ppm)
        ths, t = [], 0.0
        for k, (l, r) in enumerate(ext):
            t += l
            ths.append(t)
            t += r + gaps_ang[k]
        # rotate so the ornament (or the join) is at the bottom, text centred on top
        join = ths[-1] if rp.ornament else ths[-1] + ext[-1][1] + beta / 2
        ths = [(x - join + math.pi) % (2 * math.pi) for x in ths]
        if rp.center_word is not None:
            # centre that word's angular span (first letter's leading edge to
            # last letter's trailing edge) on 12 o'clock
            ks = [k for k, w in enumerate(word_of) if w == rp.center_word]
            a0 = ths[ks[0]] - ext[ks[0]][0]
            a1 = a0 + ((ths[ks[-1]] + ext[ks[-1]][1]) - a0) % (2 * math.pi)
            mid = (a0 + a1) / 2
            ths = [(x - mid) % (2 * math.pi) for x in ths]
        C = (0.0, 0.0)
        world = [place(g, th, Rin, C) for g, th in zip(locs, ths)]
        gaps = [
            solid_of(world[i]).distance(solid_of(world[(i + 1) % n])) for i in range(n)
        ]
        ok = (
            beta > 0
            and min(gaps) >= rp.min_gap_mm * ppm
            and r_h >= rp.hub_r_min_mm * ppm
        )
        if ok or h_mm <= rp.cap_h_min_mm:
            return dict(
                labels=labels,
                locs=locs,
                ths=ths,
                Rp=Rp,
                Ro=Ro,
                Rin=Rin,
                cap=cap,
                cap_mm=cap / ppm,
                r_h=r_h,
                min_gap_mm=min(gaps) / ppm,
                ok=ok,
                font_size=(font.size if font is not None else None),
            )
        h_mm -= 1.0


# --------------------------------------------------------------------------
# seam network
# --------------------------------------------------------------------------


def arc_pts(C, r, a0, a1, step_px):
    d = (a1 - a0) % (2 * math.pi)
    n = max(4, int(r * d / step_px))
    return [
        (C[0] + r * math.sin(a0 + d * k / n), C[1] - r * math.cos(a0 + d * k / n))
        for k in range(n + 1)
    ]


def closest_on(pts, th, C):
    """Index of the curve sample whose polar angle is nearest th."""
    return min(
        range(1, len(pts) - 1),
        key=lambda i: abs(
            ((ang_of(pts[i], C) - th + math.pi) % (2 * math.pi)) - math.pi
        ),
    )


def boundary_hit(panel, C, th, far):
    ray = LineString([C, (C[0] + far * math.sin(th), C[1] - far * math.cos(th))])
    hit = ray.intersection(panel.exterior)
    pts = G._coords_of(hit)
    return max(pts, key=lambda p: math.hypot(p[0] - C[0], p[1] - C[1]))


def inward_normal(panel, p, C):
    ring = panel.exterior
    d = ring.project(Point(p))
    a, b = ring.interpolate(d - 3), ring.interpolate(d + 3)
    tx, ty = b.x - a.x, b.y - a.y
    tn = math.hypot(tx, ty) or 1.0
    nx, ny = -ty / tn, tx / tn
    if nx * (C[0] - p[0]) + ny * (C[1] - p[1]) < 0:
        nx, ny = -nx, -ny
    return (nx, ny)


def raw_cells(seams, letter_union, background, panel):
    """Background cells cut by the raw seam lines (no tabs yet)."""
    lines = []
    for sm in seams:
        pts = [tuple(p) for p in sm["pts"]]
        if len(pts) < 2:
            continue
        ea, eb = sm["ends"]
        if ea in ("L", "T", "B"):
            pts = _extend(pts, 0, 2)
        if eb in ("L", "T", "B"):
            pts = _extend(pts, 1, 2)
        lines.append(LineString(pts))
    net = shapely.union_all(lines + [panel.boundary, letter_union.boundary], grid_size=0.1)
    return [f for f in polygonize(net)
            if background.contains(f.representative_point()) and f.area > 1.0]


def split_seams(pieces, letter_union, panel, rp, ppm, curved):
    """A cross seam through the middle of every piece whose longest side
    exceeds rp.split_mm, square to that side; ends land on the piece's own
    boundary (another seam, a letter, or the rim)."""
    out = []
    lim = rp.split_mm * ppm
    for poly in pieces:
        if poly.is_empty:
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            mrr = poly.minimum_rotated_rectangle
        xs, ys = mrr.exterior.coords.xy
        e = [((xs[k], ys[k]), (xs[k + 1], ys[k + 1])) for k in range(2)]
        long_e = max(e, key=lambda q: math.dist(*q))
        if math.dist(*long_e) <= lim:
            continue
        ux = (long_e[1][0] - long_e[0][0]) / math.dist(*long_e)
        uy = (long_e[1][1] - long_e[0][1]) / math.dist(*long_e)
        c = poly.centroid
        if not poly.contains(c):
            c = poly.representative_point()
        far = 400 * ppm
        cut = LineString([(c.x + uy * far, c.y - ux * far), (c.x - uy * far, c.y + ux * far)])
        hit = cut.intersection(poly)
        segs = [g for g in getattr(hit, "geoms", [hit]) if g.geom_type == "LineString"]
        if not segs:
            continue
        seg = min(segs, key=lambda g: g.distance(c))
        a, b = seg.coords[0], seg.coords[-1]
        if math.dist(a, b) < 8 * ppm:
            continue
        halves = poly.difference(seg.buffer(0.5))
        parts = sorted(getattr(halves, "geoms", [halves]), key=lambda g: -g.area)
        if len(parts) < 2 or parts[1].area < (15 * ppm) ** 2:
            continue

        def kind(q):
            if panel.exterior.distance(Point(q)) < 1.0:
                return "B"
            if letter_union.distance(Point(q)) < 1.0:
                return "L"
            return "T"

        pts = [a, b]
        n = max(4, int(math.dist(a, b) / (2 * ppm)))
        pts = [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(n + 1)]
        out.append(dict(pts=curved(pts), kind="split", plain_ok=True, ends=(kind(a), kind(b))))
    return out


def n_sub(length_px, rp, ppm):
    """How many subdivision seams split a span of this length: enough that no
    piece exceeds ~1.3x the target width."""
    return max(0, math.ceil(length_px / (1.3 * rp.target_w_mm * ppm)) - 1)


# Set by build_ring() when rp.center_text fits: (Cx, Cy, r_h_px) of the hub
# circle, so oversized_oriented() can exempt the one big undivided medallion
# background piece that surrounds the center text, the same way it already
# exempts the solid outer frame (§11.2 in RING_SPEC.md).
_center_medallion = None


def build_ring(words, seed, rp: RingParams, cfg):
    ppm = cfg.px_per_mm
    L = fit_ring(words, rp, ppm)
    D = rp.diameter_mm * ppm
    m = cfg.margin_px
    C = (m + D / 2, m + D / 2)
    cfg = replace(
        cfg,
        panel_w_px_fit=int(D),
        panel_h_px_fit=int(D),
        panel_shape="disc" if rp.shape == "disc" else "rect",
    )
    if rp.shape == "disc":
        panel = Point(C).buffer(D / 2, quad_segs=96)
    else:
        panel = box(m, m, m + D, m + D)
    Rin, Rp, r_h = L["Rin"], L["Rp"], L["r_h"]
    ths, locs = L["ths"], L["locs"]
    n = len(locs)
    world = [place(g, th, Rin, C) for g, th in zip(locs, ths)]
    global _center_medallion
    _center_medallion = None
    center_solids = []
    center_polys = []
    L["center_cap_mm"] = None
    if rp.center_text and rp.center_style in ("flat", "ring"):
        # Loose letters cut straight out of the ordinary pinwheel hub: no
        # medallion, no exemption, spokes stay. They join letter_union (so
        # they become pockets/pieces like any letter) but NOT the outer
        # letters' seam-routing obstacles -- only tab clearance sees them.
        # center letters the SAME size as the ring's (Alex: interchangeable,
        # and less annoying to assemble) unless that doesn't fit the hub
        rp_c = (replace(rp, center_loose_cap_max_mm=min(rp.center_loose_cap_max_mm, L["cap_mm"]))
                if rp.center_match_ring_cap else rp)
        cl, ccap = center_letters(rp_c, ppm, r_h, C)
        if cl is None:
            warnings.warn(
                f"center_text {rp.center_text!r} ({rp.center_style}) doesn't fit "
                f"hub radius {r_h / ppm:.0f}mm -- skipping"
            )
        else:
            world.extend(cl)
            center_polys = list(cl)
            center_solids = [solid_of(g) for g in cl]
            L["center_cap_mm"] = ccap
    elif rp.center_text:
        found = None
        for cap_mm, r, block in center_text_block(rp, ppm):
            if r <= r_h * rp.center_text_fit_frac:
                found = block
                break
        if found is None:
            warnings.warn(
                f"center_text {rp.center_text!r} doesn't fit hub radius "
                f"{r_h / ppm:.0f}mm even at {rp.center_text_cap_min_mm}mm cap "
                "-- skipping, hub stays a plain pinwheel"
            )
        else:
            world_block = affinity.translate(found, C[0], C[1])
            world.append(world_block)
            _center_medallion = (C[0], C[1], r_h)
    letter_union = unary_union(world)
    solids_l = [solid_of(g) for g in locs]
    solids = [solid_of(w) for w in world[:n]]
    letters_solid = unary_union(solids)
    # tabs/face classification must also keep clear of loose center letters
    tab_solids = unary_union(solids + center_solids) if center_solids else letters_solid
    # Globe land (rp.globe_land): fixed pieces, computed once for all
    # variants. cut_union = everything the background is carved around.
    land = []
    land_union = None
    cut_union = letter_union
    letter_tab_solids = tab_solids
    if rp.globe_land is not None:
        import globe_land as GL

        lon0, lat0 = rp.globe_land
        R_g = D / 2 - rp.globe_margin_mm * ppm
        obst = unary_union(solids + center_solids + ([world[-1]] if _center_medallion else []))
        land, lst = GL.land_pieces(
            GL.load_countries(), lon0, lat0, R_g, C, Point(C).buffer(R_g, quad_segs=128),
            obst, ppm, lambda g: oversized_oriented(g, cfg),
            min_feature_mm=rp.land_min_feature_mm, min_piece_mm=rp.land_min_piece_mm,
            island_min_mm=rp.land_island_min_mm,
        )
        L["globe_r_px"] = R_g
        L["land_stats"] = lst
        if land:
            land_union = unary_union([p["polygon"] for p in land])
            cut_union = unary_union([letter_union, land_union])
            tab_solids = unary_union([tab_solids, land_union])
    background = panel.difference(cut_union)
    far = 2 * D
    # Square stock: a circle of radius Rc (inscribed + corner_ring_mm) clipped by
    # the square fences off the 4 deep corners; radial rim seams stop on it (T)
    # where it's nearer than the square edge. Disc: Rc = inf (never hit).
    Rc = Rp + rp.corner_ring_mm * ppm if rp.shape == "square" else float("inf")
    if rp.frame_mm > 0:
        # The frame's inner circle plays the corner arc's role everywhere: every
        # radial rim seam lands on it (T) instead of on the panel edge.
        Rc = Rp - rp.frame_mm * ppm

    frame_ends = []  # polar angles where rim seams land on the frame circle

    def rim_target(phi):
        b = boundary_hit(panel, C, phi, far)
        if math.hypot(b[0] - C[0], b[1] - C[1]) > Rc:
            b = (C[0] + Rc * math.sin(phi), C[1] - Rc * math.cos(phi))
            if rp.frame_mm > 0:
                # Frame: the circle is split into arcs AT every landing, so each
                # piece touching the frame owns one arc (and gets its own tab).
                frame_ends.append(phi % (2 * math.pi))
                return b, out_vec(phi + math.pi), "J"
            return b, out_vec(phi + math.pi), "T"
        return b, inward_normal(panel, b, C), "B"

    def obstacles(attach):
        return [(s, attach.get(k)) for k, s in enumerate(solids)]

    def curved(pts):
        if pts is None:
            return None
        if G._vg_deflection(pts) < 2.5 * ppm:
            pts = G._vg_bow(pts, letters_solid, cfg)
        return pts

    best = None
    import time as _time

    _t_start = _time.time()
    for variant in range(rp.variants):
        rng = random.Random(seed * 131 + variant * 9973)
        frame_ends.clear()
        seams = []  # dicts: pts, ends=(type_a, type_b)
        hub_ends = []  # polar angles where seams land on the hub circle
        # --- per-slot caps -------------------------------------------------
        top_caps = {}  # slot -> list of rim angles
        for i in range(n):
            tp, bp, bridges = G._letter_caps(solids_l[i], rng, ppm)
            # A bridged open side (H/N/A feet, U/H tops) is already sealed by its
            # bridge seam, so ONE cap per side is enough; per-prong caps crowd the
            # hub circle (19mm arcs, no room for tabs) and slice the rim thin.
            if len(bp) > 1 and any(sd == "bottom" for _a, _b, sd in bridges):
                bp = [min(bp, key=lambda p: abs(p[0]))]
            if len(tp) > 1 and any(sd == "top" for _a, _b, sd in bridges):
                tp = [min(tp, key=lambda p: abs(p[0]))]
            th = ths[i]
            for p in tp:
                a = xf_pt(p, th, Rin, C)
                na = xf_vec((0.0, -1.0), th)
                phi = ang_of(a, C) + math.radians(rng.uniform(-4, 4))
                b, nb, eb = rim_target(phi)
                pts = G._vg_curve(a, na, b, nb, obstacles({i: "a"}), ppm) or [a, b]
                seams.append(dict(pts=curved(pts), kind="topcap", plain_ok=rp.plain_caps, ends=("L", eb)))
                top_caps.setdefault(i, []).append(ang_of(b, C))
            for p in bp:
                a = xf_pt(p, th, Rin, C)
                na = xf_vec((0.0, 1.0), th)
                phi = ang_of(a, C) + math.radians(rng.uniform(-6, 6))
                b = (C[0] + r_h * math.sin(phi), C[1] - r_h * math.cos(phi))
                nb = out_vec(phi)
                pts = G._vg_curve(a, na, b, nb, obstacles({i: "a"}), ppm) or [a, b]
                seams.append(dict(pts=curved(pts), kind="botcap", plain_ok=rp.plain_caps, ends=("L", "T")))
                hub_ends.append(phi % (2 * math.pi))
            for a, b, _side in bridges:
                aw, bw = xf_pt(a, th, Rin, C), xf_pt(b, th, Rin, C)
                na = xf_vec((1.0, 0.0) if b[0] >= a[0] else (-1.0, 0.0), th)
                nb = (-na[0], -na[1])
                pts = G._vg_curve(aw, na, bw, nb, obstacles({i: "ab"}), ppm) or [aw, bw]
                seams.append(dict(pts=curved(pts), kind="bridge", plain_ok=rp.plain_caps, ends=("L", "L")))
        # --- ring seams between neighbouring slots ---------------------------
        levels = [0.5] if rp.rows == 2 else list(rp.levels3)  # frac of cap h
        H = []
        for i in range(n):
            miny, maxy = solids_l[i].bounds[1], solids_l[i].bounds[3]
            lo, hi = miny + 4 * ppm, maxy - 4 * ppm
            row = []
            for f in levels:
                y = -f * L["cap"] + rng.uniform(-0.07, 0.07) * L["cap"]
                row.append(min(max(y, lo), hi) if hi > lo else (miny + maxy) / 2)
            H.append(row)
        ring_seams = {}  # (gap, level) -> pts
        for g in range(n if rp.ring_seams else 0):
            i, j = g, (g + 1) % n
            for lv in range(len(levels)):
                ea = G._letter_edge_point(solids_l[i], "right", H[i][lv], ppm)
                eb = G._letter_edge_point(solids_l[j], "left", H[j][lv], ppm)
                if ea is None or eb is None:
                    continue
                a, na = xf_pt(ea[0], ths[i], Rin, C), xf_vec(ea[1], ths[i])
                b, nb = xf_pt(eb[0], ths[j], Rin, C), xf_vec(eb[1], ths[j])
                pts = G._vg_curve(a, na, b, nb, obstacles({i: "a", j: "b"}), ppm) or [
                    a,
                    b,
                ]
                pts = curved(pts)
                ring_seams[(g, lv)] = pts
                seams.append(dict(pts=pts, kind="ring", ends=("L", "L")))
        # --- no ring seams (rp.ring_seams False): polar sectors instead -------
        # Each gap between letters is split by seams running from the hub
        # circle (T) out to the rim, bending further off radial the further
        # out they get (a pinwheel that keeps going past the hub spokes);
        # with no ring seams to T onto, the rim/mid/hub subdivisions below
        # find no hosts and are skipped.
        if not rp.ring_seams:
            sdir = rng.choice((-1, 1))
            r_out = Rp if rp.frame_mm <= 0 else Rc
            for g in range(n):
                i, j = g, (g + 1) % n
                a0 = ths[i] + ang_extent(solids_l[i], Rin)[1]
                a1 = ths[j] - ang_extent(solids_l[j], Rin)[0]
                d = (a1 - a0) % (2 * math.pi)
                k = n_sub(r_out * d, rp, ppm)
                for s_ in range(1, k + 1):
                    phi0 = a0 + d * s_ / (k + 1) + math.radians(rng.uniform(-2, 2))
                    pts = []
                    for q in range(41):
                        r = r_h + (r_out - r_h) * q / 40
                        f = (r - r_h) / (r_out - r_h)
                        phi = phi0 + sdir * math.radians(rp.sector_twist_deg) * f * f
                        pts.append((C[0] + r * math.sin(phi), C[1] - r * math.cos(phi)))
                    pts[-1] = boundary_hit(panel, C, ang_of(pts[-1], C), far)
                    hub_ends.append(ang_of(pts[0], C) % (2 * math.pi))
                    seams.append(dict(pts=pts, kind="sector", ends=("T", "B")))
        # --- rim subdivisions: T off the outermost ring seam up to the rim ----
        top_lv = len(levels) - 1
        for g in range(n):
            i, j = g, (g + 1) % n
            host = ring_seams.get((g, top_lv))
            if host is None or i not in top_caps or j not in top_caps:
                continue
            a0 = max(
                top_caps[i],
                key=lambda a: (
                    (a - ths[i]) % (2 * math.pi)
                    if (a - ths[i]) % (2 * math.pi) < math.pi
                    else -1
                ),
            )
            a1 = min(top_caps[j], key=lambda a: (a - ths[j]) % (2 * math.pi))
            d = (a1 - a0) % (2 * math.pi)
            # piece budget by AREA of the rim region in this angular window, so
            # a square panel's deep corners get more (narrower) pieces
            wedge = Polygon(
                [C]
                + [
                    (
                        C[0] + far * math.sin(a0 + d * t / 16),
                        C[1] - far * math.cos(a0 + d * t / 16),
                    )
                    for t in range(17)
                ]
            )
            area = (
                panel.intersection(wedge)
                .intersection(Point(C).buffer(min(Rc, far), quad_segs=64))
                .difference(Point(C).buffer(L["Ro"], quad_segs=64))
                .area
            )
            k = n_sub(area / (Rp - L["Ro"]), rp, ppm)
            for s in range(1, k + 1):
                phi = a0 + d * s / (k + 1) + math.radians(rng.uniform(-2, 2))
                idx = closest_on(host, phi, C)
                a = host[idx]
                b, nb, eb = rim_target(ang_of(a, C) + math.radians(rng.uniform(-3, 3)))
                na = out_vec(ang_of(a, C))
                pts = G._vg_curve(a, na, b, nb, obstacles({}), ppm) or [a, b]
                seams.append(dict(pts=curved(pts), kind="rimsub", ends=("T", eb)))
        # --- middle-band subdivisions (wide gaps on short names) ---------------
        # every band between two neighbouring ring seams (1 band with rows=2,
        # 2 with rows=3) gets cross seams when its gap is wide
        for lv in range(len(levels) - 1):
            for g in range(n):
                lo_h, up_h = ring_seams.get((g, lv)), ring_seams.get((g, lv + 1))
                if lo_h is None or up_h is None:
                    continue
                w = (LineString(lo_h).length + LineString(up_h).length) / 2
                k = n_sub(w, rp, ppm)
                a_lo, a_hi = ang_of(lo_h[0], C), ang_of(lo_h[-1], C)
                d = (a_hi - a_lo) % (2 * math.pi)
                for s_ in range(1, k + 1):
                    phi = a_lo + d * s_ / (k + 1) + math.radians(rng.uniform(-2, 2))
                    a = lo_h[closest_on(lo_h, phi, C)]
                    b = up_h[closest_on(up_h, phi, C)]
                    na, nb = out_vec(ang_of(a, C)), out_vec(ang_of(b, C) + math.pi)
                    pts = G._vg_curve(a, na, b, nb, obstacles({}), ppm) or [a, b]
                    seams.append(dict(pts=curved(pts), kind="midsub", ends=("T", "T")))
        # --- hub-ring subdivisions: T off the innermost ring seam to the hub ---
        hub_sorted = sorted(hub_ends)
        r_hm = (Rin + r_h) / 2
        for g in range(n):
            host = ring_seams.get((g, 0))
            if host is None:
                continue
            ga = ang_of(host[len(host) // 2], C)
            # hub-cap angles bracketing this gap
            prev = max(
                [a for a in hub_sorted if (ga - a) % (2 * math.pi) < math.pi]
                or hub_sorted,
                key=lambda a: -((ga - a) % (2 * math.pi)),
            )
            nxt = min(hub_sorted, key=lambda a: (a - ga) % (2 * math.pi))
            d = (nxt - prev) % (2 * math.pi)
            k = n_sub(r_hm * d, rp, ppm)
            for s in range(1, k + 1):
                phi = prev + d * s / (k + 1)
                idx = closest_on(host, phi, C)
                a = host[idx]
                phb = ang_of(a, C) + math.radians(rng.uniform(-5, 5))
                b = (C[0] + r_h * math.sin(phb), C[1] - r_h * math.cos(phb))
                pts = G._vg_curve(
                    a,
                    out_vec(ang_of(a, C) + math.pi),
                    b,
                    out_vec(phb),
                    obstacles({}),
                    ppm,
                ) or [a, b]
                seams.append(dict(pts=curved(pts), kind="hubsub", ends=("T", "T")))
        # --- hub disc spokes (pinwheel), landing on the hub circle -------------
        # With a center_text medallion, the DISC must stay undivided (no spoke
        # slices through the text -- oversized_oriented() exempts that one big
        # piece, above), but the hub CIRCLE should still get its usual number
        # of landing points: they're what nearby hub-ring subdivisions (hubsub)
        # anchor to, and starving that down to a fixed 3 (the small-hub-disc
        # case) was quietly forcing bigger, occasionally oversized merges in
        # the hub-ring band -- unrelated to the medallion, but caused by it.
        k_hub = 1
        while (
            2 * r_h * (math.sin(math.pi / k_hub) if k_hub > 1 else 1.0)
            > rp.hub_piece_mm * ppm
        ):
            k_hub += 1
        a0 = rng.uniform(0, 2 * math.pi)
        split = []
        if center_polys and rp.center_split:
            # Split hub for two center words (center_style "ring"): a tabbed
            # DIVIDER across the disc between the words, an inner circle just
            # inside the letters, a cross seam through the inner disc, and
            # short radials across the letter band through the wide gaps
            # between letters -- normal-size, tabbed pieces, and no word owns
            # the disc's "up" (RING_SPEC.md §16.2).
            split = (_center_flat_grid_seams(center_polys, C, r_h, ppm, rp, seams)
                     if rp.center_style == "flat"
                     else _center_split_seams(center_polys, C, r_h, ppm, rp, seams, rng))
        elif k_hub > 1 and center_polys:
            # Loose center text: pick a pinwheel whose spokes never pass a
            # center letter without touching it (see plan_center_pinwheel).
            P, phis, twist, tw = plan_center_pinwheel(
                center_polys, C, r_h, k_hub, ppm, rp.center_min_gap_mm, rng
            )
            for phi in phis:
                b = (C[0] + r_h * math.sin(phi), C[1] - r_h * math.cos(phi))
                split.append(phi % (2 * math.pi))
                pts = _pinwheel_spoke(P, phi, b, twist, tw, r_h)
                seams.append(dict(pts=pts, kind="spoke", ends=("J", "J")))
        elif k_hub > 1:
            twist = rng.choice((-1, 1))
            for s_ in range(k_hub):
                phi = a0 + 2 * math.pi * s_ / k_hub
                b = (C[0] + r_h * math.sin(phi), C[1] - r_h * math.cos(phi))
                split.append(phi % (2 * math.pi))
                if _center_medallion is not None:
                    continue  # landing point only -- no spoke into the text
                # pinwheel: leave the centre along a rotated direction
                na = out_vec(phi + twist * math.radians(50))
                pts = G._vg_bez(C, na, b, out_vec(phi + math.pi), 0.45 * r_h)
                seams.append(dict(pts=pts, kind="spoke", ends=("J", "J")))
        else:
            split = [(a0 + 2 * math.pi * s_ / 3) % (2 * math.pi) for s_ in range(3)]
        # --- solid frame: its inner circle, split into a few host arcs ---------
        if rp.frame_mm > 0:
            if frame_ends:
                fa = sorted(frame_ends)
                fa = [a for k, a in enumerate(fa) if k == 0 or a - fa[k - 1] > 1e-6]
            else:
                f0 = rng.uniform(0, 2 * math.pi)
                fa = sorted(
                    (f0 + 2 * math.pi * q / rp.frame_arcs) % (2 * math.pi)
                    for q in range(rp.frame_arcs)
                )
            for q in range(len(fa)):
                seams.insert(
                    0,
                    dict(
                        pts=arc_pts(C, Rc, fa[q], fa[(q + 1) % len(fa)], 2 * ppm),
                        kind="framearc",
                        ends=("J", "J"),
                    ),
                )
        # --- square corners: arc on Rc across each corner + a diagonal split ---
        if rp.shape == "square" and rp.frame_mm <= 0:
            half = D / 2
            span = math.acos(
                min(1.0, half / Rc)
            )  # angle from the edge normal to the arc end
            for q in range(4):
                mid = math.pi / 4 + q * math.pi / 2  # corner direction
                a0 = mid - (math.pi / 4 - span) - math.radians(1.0)
                a1 = mid + (math.pi / 4 - span) + math.radians(1.0)
                seams.insert(
                    0,
                    dict(
                        pts=arc_pts(
                            C, Rc, a0 % (2 * math.pi), a1 % (2 * math.pi), 2 * ppm
                        ),
                        kind="cornerarc",
                        ends=("B", "B"),
                    ),
                )
                phi = mid + math.radians(rng.uniform(-8, 8))
                a = (C[0] + Rc * math.sin(phi), C[1] - Rc * math.cos(phi))
                b = boundary_hit(panel, C, phi, far)
                seams.append(
                    dict(pts=curved([a, b]), kind="cornersub", ends=("T", "B"))
                )
        # --- hub circle: arcs between spoke landings (caps T onto it) ---------
        hs = sorted(split)
        for q in range(len(hs)):
            b0, b1 = hs[q], hs[(q + 1) % len(hs)]
            seams.insert(
                0,
                dict(
                    pts=arc_pts(C, r_h, b0, b1, 2 * ppm), kind="hubarc", ends=("J", "J")
                ),
            )

        # letters only: a coastline's bays are not letter notches
        _snap_notch_ends(seams, letter_tab_solids, ppm)
        if not rp.ring_seams:
            # a twisted sector seam that clips a letter stops on it instead
            import globe_land as GL_

            seams = ([sm for sm in seams if sm["kind"] != "sector"]
                     + GL_.chop_seams([sm for sm in seams if sm["kind"] == "sector"],
                                      letter_tab_solids, ppm))
        if land_union is not None:
            seams = GL.chop_seams(seams, land_union, ppm)
        if rp.keep_seams:
            # never drop a seam for want of a tab: cut it plain instead of
            # merging two cells into one big piece
            for sm in seams:
                sm["keep"] = True
                sm["plain_ok"] = True
        # split long cells on the raw (untabbed) seam lines, so split ends
        # land exactly on the seams they meet; repeat until nothing is long.
        # Splits go first so they get first pick of tab positions.
        for _round in range(4 if rp.split_mm > 0 else 0):
            # the hub disc keeps its own spokes: splitting a wedge would land
            # T-junctions mid-spoke and leave the spokes no room for tabs
            cells = [c_ for c_ in raw_cells(seams, cut_union, background, panel)
                     if Point(C).distance(c_.representative_point()) > r_h]
            extra = split_seams(cells, cut_union, panel, rp, ppm, curved)
            if not extra:
                break
            if rp.keep_seams:
                for sm in extra:
                    sm["keep"] = True
            seams[:0] = extra
        with _oriented_oversized():
            surround, counters, st = assemble_qa(
                seams, cut_union, tab_solids, background, panel, cfg, C,
                distinct_tabs=rp.distinct_tabs,
                untabbed_kinds=("hubarc",) if rp.hub_rotates else (),
            )
        land_v = land
        if land:
            surround, land_v = absorb_ocean_into_land(surround, land, st["bulbs"], cfg,
                                                      rp.land_min_feature_mm)
            st["defects"] = [(k, (q.x, q.y)) for k, q, _i in
                             qa_defects(surround + [p["polygon"] for p in land_v],
                                        st["bulbs"], ppm)]
        st["land"] = land_v
        if land:
            # a coast-chopped seam may cut plain: never leave a rim piece gripless
            st["defects"] += [("loose_rim", q) for q in loose_rim_pieces(
                [{"kind": "cell", "polygon": g} for g in surround], st["bulbs"], panel, cfg)]
        sc = (len(st["defects"]),) + score(surround, panel, cfg) + (st["dropped"],)
        if rp.progress:
            el = _time.time() - _t_start
            eta = el / (variant + 1) * (rp.variants - variant - 1)
            print(
                f"  variant {variant + 1}/{rp.variants}: score (QA defects, thin, oversized, "
                f"sliver, nub, dropped) = {sc}  [{el:.0f}s elapsed, ~{eta:.0f}s left]",
                flush=True,
            )
        if best is None or sc < best[0]:
            best = (sc, surround, counters, st, seams)
    sc, surround, counters, st, seams = best
    st["score"] = sc
    pieces = {(k, 0): p for k, p in enumerate(surround + counters)}
    st["seams"] = seams
    # _center_medallion is process-global state, set only for the duration of
    # this build -- stash it in st too so a caller working from a pickled
    # (pieces, cfg, st, panel) tuple in a FRESH process (the normal workflow
    # for lint_pieces here) can still pass it through and get the same
    # oversized-check exemption, instead of silently losing it.
    st["center_medallion"] = _center_medallion
    return pieces, letter_union, cfg, L, st, panel, C


# --------------------------------------------------------------------------
# assembly with junction support (generalised _vg_assemble)
# --------------------------------------------------------------------------


def _extend(pts, end, d):
    if end == 0:
        (x0, y0), (x1, y1) = pts[1], pts[0]
    else:
        (x0, y0), (x1, y1) = pts[-2], pts[-1]
    L = math.hypot(x1 - x0, y1 - y0) or 1.0
    q = (x1 + (x1 - x0) / L * d, y1 + (y1 - y0) / L * d)
    return [q] + pts if end == 0 else pts + [q]


# Distinct-tab size classes (see RingParams.distinct_tabs, RING_SPEC.md
# §12.2). Multipliers on the base tab_circle_r_px/tab_stem_w_px. Chosen so
# even the smallest class clears ring_lint.MIN_PROVEN_TAB_STEM_PX/_R_PX with
# margin at the proven 30px/15px default (0.85 -> 25.5px/12.75px, both above
# the 22px/11px floor), and the largest is only a modest, likely-safe step
# above the proven default -- not yet physically confirmed at 1.10x, so
# don't push it further without a test cut.
TAB_SIZE_CLASSES = (0.85, 1.0, 1.10)


def _tab_size_class(pts):
    """Deterministic size-class index for a seam, from its own endpoints.
    Each seam is computed once (A1: shared edges are single-sourced) and
    read by both neighboring pieces, so hashing the seam's own points is
    enough to keep a tab and its matching socket consistent -- no need to
    coordinate across pieces."""
    a, b = pts[0], pts[-1]
    key = f"{a[0]:.1f},{a[1]:.1f},{b[0]:.1f},{b[1]:.1f}".encode()
    h = hashlib.md5(key).digest()
    return TAB_SIZE_CLASSES[h[0] % len(TAB_SIZE_CLASSES)]


def _snap_notch_ends(seams, letters_solid, ppm, min_notch_mm=1.0):
    """A seam ending on a letter must not route INTO the letter's open notch
    (between E/F fingers, into a C/S/U mouth) to reach an inner stroke -- that
    leaves a long, finger-thin strip of wood on each side of it. Cut such a
    seam where it first enters the letter's convex hull and end it on the
    nearest point of the letter outline there (the finger tip / mouth edge),
    so the notch fill stays whole in one piece."""
    comps = list(letters_solid.geoms) if isinstance(letters_solid, MultiPolygon) else [letters_solid]
    snapped = 0
    for s in seams:
        for e in (0, 1):
            pts = [tuple(p) for p in s["pts"]]
            if len(pts) < 3:
                continue
            tip = Point(pts[0] if e == 0 else pts[-1])
            letter = min(comps, key=lambda g: g.distance(tip))
            # an "L" end lands on a letter; a hub spoke's "J" end can sit
            # INSIDE the letter its pinwheel converges in -- that letter is
            # its own piece, so the spoke may leave it from any outline point
            near = 2 * ppm if s["ends"][e] == "L" else 0.0
            if letter.distance(tip) > near:
                continue
            hull = letter.convex_hull
            notch = hull.difference(letter.buffer(0.5))
            if LineString(pts).intersection(notch).length < min_notch_mm * ppm:
                continue
            seq = pts if e == 1 else pts[::-1]  # walk from the free end toward the letter
            k = next((i for i, q in enumerate(seq) if hull.contains(Point(q))), None)
            if not k:
                continue
            hit = LineString(seq[k - 1:k + 1]).intersection(hull.exterior)
            entry = hit if hit.geom_type == "Point" else Point(seq[k - 1])
            end = nearest_points(letter.exterior, entry)[0]
            new = seq[:k] + [(entry.x, entry.y), (end.x, end.y)]
            s["pts"] = new if e == 1 else new[::-1]
            snapped += 1
    return snapped


QA_NECK_MM = 3.0     # no material neck narrower than this (Alex: ~3mm material floor)
QA_STRIP_W_MM = 3.0  # strips narrower than this ...
QA_STRIP_L_MM = 6.0  # ... may not be longer than this


def qa_defects(pieces, bulbs, ppm):
    """Breakable features on background pieces: (kind, Point, piece_idx).
    'bridge': eroding by QA_NECK_MM/2 splits off a chunk (a neck under
    QA_NECK_MM) that is not a tab bulb. 'strip': material an opening of width
    QA_STRIP_W_MM removes, longer than QA_STRIP_L_MM, not part of a tab."""
    def parts(g):
        return [q for q in getattr(g, "geoms", [g]) if not q.is_empty and q.area > 0]
    tabs = unary_union([b.buffer(1.5 * ppm) for b in bulbs]) if bulbs else None
    out = []
    for i, g in enumerate(pieces):
        lobes = sorted(parts(g.buffer(-QA_NECK_MM / 2 * ppm)), key=lambda q: -q.area)
        for q in lobes[1:]:
            if q.area < 2.0 * ppm ** 2:
                continue
            if tabs is not None and tabs.contains(q):
                continue
            out.append(("bridge", q.centroid, i))
        r = QA_STRIP_W_MM / 2 * ppm
        lost = g.difference(g.buffer(-r).buffer(r).buffer(0.05 * ppm))
        for q in parts(lost.buffer(-0.2 * ppm).buffer(0.2 * ppm)):
            if q.length / 2 / ppm <= QA_STRIP_L_MM or q.area < 2.0 * ppm ** 2:
                continue
            if tabs is not None and tabs.buffer(1.0 * ppm).contains(q):
                continue
            out.append(("strip", q.centroid, i))
    return out


def assemble_qa(seams, letter_union, letters_solid, background, panel, cfg, C,
                distinct_tabs=True, rounds=8, untabbed_kinds=()):
    """assemble(), then repair breakable features: ban the tab choice of every
    seam near a defect (so it moves / flips sides), and after a seam's tab
    options run out, drop the seam. Returns the best (fewest defects) result;
    st["defects"] lists what could not be repaired."""
    ppm = cfg.px_per_mm
    bans = {}
    best = None
    for _round in range(rounds):
        surround, counters, st = assemble(seams, letter_union, letters_solid, background,
                                          panel, cfg, C, distinct_tabs, bans=bans,
                                          untabbed_kinds=untabbed_kinds)
        defects = qa_defects(surround, st["bulbs"], ppm)
        if best is None or len(defects) < len(best[3]):
            best = (surround, counters, st, defects, {k: (v if v == "drop" else set(v)) for k, v in bans.items()})
        if not defects:
            break
        changed = False
        for _kind, pt, _pi in defects:
            near = [(si, sm) for si, sm in enumerate(seams)
                    if sm.get("final") is not None and sm["final"].distance(pt) < 8 * ppm]
            near.sort(key=lambda t: t[1]["final"].distance(pt))
            for si, sm in near[:2]:
                if bans.get(si) == "drop":
                    continue
                ch = sm.get("tab_choice")
                if ch is not None and ch not in bans.setdefault(si, set()):
                    bans[si].add(ch)
                    changed = True
                elif sm.get("plain_ok") or sm["ends"] == ("J", "J"):
                    continue  # structural seam: never force-drop
                else:
                    bans[si] = "drop"
                    changed = True
        if not changed:
            break
    surround, counters, st, defects, _b = best
    st["defects"] = [(k, (p.x, p.y)) for k, p, _i in defects]
    return surround, counters, st


def assemble(seams, letter_union, letters_solid, background, panel, cfg, C, distinct_tabs=True,
             bans=None, untabbed_kinds=()):
    """bans: {seam index: set of tab choices (fallback, i, side) to skip, or
    the string "drop" to force the seam out} -- used by the QA repair loop to
    make a seam pick its next tab position / the other side."""
    ppm = cfg.px_per_mm
    bans = bans or {}
    st = {"total": 0, "centered": 0, "shifted": 0, "flipped": 0, "dropped": 0, "bulbs": []}
    min_gap = cfg.letter_clearance_px
    jr = 7 * ppm  # tab bulbs keep this far from any junction point
    # junction points = every non-letter/non-border endpoint (and T hosts' feet)
    junctions = []
    for s in seams:
        for e, p in ((0, s["pts"][0]), (1, s["pts"][-1])):
            if s["ends"][e] in ("T", "J"):
                junctions.append(Point(p))
    keep = [j.buffer(jr) for j in junctions]
    jzone = (
        unary_union([j.buffer(min_gap + 3 * ppm) for j in junctions])
        if junctions
        else None
    )
    placed = list(keep)
    accepted, tabbed = [], []

    def conflict(cand):
        core = cand if jzone is None else cand.difference(jzone)
        if core.is_empty:
            return False
        for a in accepted:
            if core.distance(a) >= min_gap:
                continue
            q1, q2 = nearest_points(core, a)
            mid = Point((q1.x + q2.x) / 2, (q1.y + q2.y) / 2)
            if letters_solid.distance(mid) > min_gap:
                return True
        return False

    for si, s in enumerate(seams):
        s.pop("tab_choice", None)
        s.pop("final", None)
        ban = bans.get(si, set())
        if ban == "drop":
            s["status"] = "banned"
            continue
        pts = [tuple(p) for p in s["pts"]]
        ea, eb = s["ends"]
        # Overshoot every letter/border/T end by 2px so the union NODES the
        # crossing (an endpoint merely touching a line is float-fragile);
        # polygonize discards the dangling stubs.
        if ea in ("L", "T", "B"):
            pts = _extend(pts, 0, 2)
        if eb in ("L", "T", "B"):
            pts = _extend(pts, 1, 2)
        ls0 = LineString(pts)
        if ls0.intersection(background).length < 3 * ppm:
            s["status"] = "short"
            continue
        n = max(6, int(ls0.length / (2 * ppm)))
        rs = [ls0.interpolate(t / n, normalized=True) for t in range(n + 1)]
        rs = [(p.x, p.y) for p in rs]
        st["total"] += 1
        chosen = None
        s["ncand"] = 0
        s["nconf"] = 0
        base_class = _tab_size_class(s["pts"]) if distinct_tabs else 1.0
        s["tab_class"] = base_class
        for fallback in (() if s.get("kind") in untabbed_kinds else (1.0, 0.85, 0.7)):
            scale = base_class * fallback
            c2 = (
                cfg
                if scale >= 0.999 and scale <= 1.001
                else replace(
                    cfg,
                    tab_circle_r_px=max(6, int(round(cfg.tab_circle_r_px * scale))),
                    tab_stem_w_px=(
                        cfg.tab_stem_w_px * scale if cfg.tab_stem_w_px else None
                    ),
                )
            )
            for pi, ps in G._vg_tab_candidates(rs, letters_solid, c2, panel, placed):
                if (fallback, pi, ps) in ban:
                    continue
                res = G._vg_splice(rs, pi, ps, c2)
                if res is None:
                    continue
                spliced, bulb = res
                cand = LineString(spliced)
                s["ncand"] += 1
                if conflict(cand):
                    s["nconf"] += 1
                    continue
                chosen = (spliced, bulb, cand)
                s["tab_choice"] = (fallback, pi, ps)
                break
            if chosen:
                break
        if chosen:
            tabbed.append(chosen[0])
            placed.append(chosen[1])
            accepted.append(chosen[2])
            st["bulbs"].append(chosen[1])
            s["final"] = chosen[2]
            st["centered"] += 1
            s["status"] = "ok"
        elif s.get("kind") in untabbed_kinds:
            # deliberately tab-free (the free-turning hub circle): always cut,
            # plain, regardless of neighbours -- it is the disc's outline
            cand = LineString(rs)
            tabbed.append(rs)
            accepted.append(cand)
            s["final"] = cand
            s["status"] = "plain"
        else:
            s["status"] = "drop"
            # keep the seam untabbed only if it's needed for structure (hub
            # arcs, spokes); otherwise drop it like wave-grid does.
            cand = LineString(rs)
            if s.get("keep") or (((ea, eb) == ("J", "J") or s.get("plain_ok"))
                                 and not conflict(cand)):
                tabbed.append(rs)
                accepted.append(cand)
                s["final"] = cand
                s["status"] = "plain"
            st["dropped"] += 1

    net = shapely.union_all(
        [panel.boundary, letter_union.boundary] + [LineString(t) for t in tabbed],
        grid_size=0.1,
    )
    faces = [
        f
        for f in polygonize(net)
        if background.contains(f.representative_point()) and f.area > ppm**2
    ]
    surround = [
        f for f in faces if not letters_solid.contains(f.representative_point())
    ]
    counters = [f for f in faces if letters_solid.contains(f.representative_point())]
    surround = G._vg_absorb(surround, panel, cfg)
    return surround, counters, st


def _fold_ocean_scraps(pieces, cfg, max_mm=8.0):
    """Last word on globe-land rings: an ocean scrap still under max_mm x
    max_mm after carving (a hairline pocket left where coast, seam and
    letter meet) joins the land or ocean neighbour it shares the most edge
    with -- never a letter, never a letter counter."""
    import globe_land as GL

    ppm = cfg.px_per_mm
    holes = unary_union([Polygon(r) for p in pieces if p["kind"] == "letter"
                         for g in GL._parts(p["polygon"]) for r in g.interiors])
    out = list(pieces)
    for p in sorted([q for q in pieces if q["kind"] == "cell"], key=lambda q: q["polygon"].area):
        g = p["polygon"]
        if p not in out or GL._mrr_sides(g)[1] > max_mm * ppm:
            continue
        if not holes.is_empty and holes.buffer(0.2 * ppm).contains(g):
            continue  # a letter counter
        nb = [(GL._shared(g, q["polygon"], 0.1 * ppm), k) for k, q in enumerate(out)
              if q is not p and q["kind"] in ("land", "cell")]
        sh, k = max(nb, default=(0.0, None))
        if k is None or sh <= 0:
            continue
        eps = 0.05 * ppm
        u = unary_union([out[k]["polygon"].buffer(eps), g.buffer(eps)]).buffer(-eps)
        if isinstance(u, Polygon):
            out[k] = {**out[k], "polygon": u}
            out.remove(p)
    return out


def absorb_ocean_into_land(surround, land, bulbs, cfg, min_feature_mm=3.0):
    """Ocean scraps a seam layout can't make whole -- a sliver or thin
    piece wedged between the coast and a seam or letter -- become part of
    the land piece they share the most border with (the coast grows by the
    scrap; nothing is cut there). Only tab-free scraps sharing at least a
    tenth of their outline with that land piece, and only when the grown
    land piece stays a clean, not-oversized piece. Returns (surround, land)
    as new lists; `land` items are copied, never mutated."""
    import globe_land as GL

    ppm = cfg.px_per_mm
    r = (min_feature_mm / 2 + 0.2) * ppm
    eps = 0.05 * ppm
    land = [dict(p) for p in land]
    tabs = unary_union([b.buffer(1.0 * ppm) for b in bulbs]) if bulbs else None
    keep = []
    for f in sorted(surround, key=lambda g: g.area):
        bad = G._vg_sliver(f, cfg) or G._vg_thin_bridge(f, cfg) or qa_defects([f], [], ppm)
        if not bad or (tabs is not None and f.intersects(tabs)):
            keep.append(f)
            continue
        best = max(((GL._shared(f, p["polygon"], 0.1 * ppm), k) for k, p in enumerate(land)),
                   default=(0.0, None))
        if best[1] is None or best[0] < f.length / 10:
            keep.append(f)
            continue
        # faces come off a 0.1px-snapped polygonize: close the hairline gap
        u = unary_union([land[best[1]]["polygon"].buffer(eps), f.buffer(eps)]).buffer(-eps)
        if (not isinstance(u, Polygon) or oversized_oriented(u, cfg)
                or not GL.piece_ok(u, r, ppm)):
            keep.append(f)
            continue
        land[best[1]]["polygon"] = u
    # a neck / strip on an ocean piece where it meets the coast (a seam end
    # stepping onto land, two capes nearly closing a bay): the thin part
    # goes to the land it touches, the ocean piece keeps its solid body
    out = []
    for f in keep:
        thin = G._vg_thin_bridge(f, cfg)  # ring_lint's 4mm floor, not just QA's 3mm
        if not qa_defects([f], bulbs, ppm) and not thin:
            out.append(f)
            continue
        # the solid parts: the body, plus any real lobe a sub-3mm (sub-4mm if
        # lint calls it thin) neck joins to it -- that lobe becomes its own
        # (tabless, drop-in) ocean piece
        rr = max(r, 2.1 * ppm) if thin else r
        solid = sorted(GL._parts(GL._open(f, rr)), key=lambda g: -g.area)
        solid = solid[:1] + [g for g in solid[1:] if g.area >= (10 * ppm) ** 2]
        if not solid:
            out.append(f)
            continue
        whole = f
        for b in GL._parts(whole.difference(unary_union(solid))):
            if b.area < 0.05 * ppm ** 2 or (tabs is not None and b.intersects(tabs)):
                continue
            sh, k = max(((GL._shared(b, p["polygon"], 0.1 * ppm), k) for k, p in enumerate(land)),
                        default=(0.0, None))
            if k is None or sh < b.length / 5:
                continue
            base = land[k]["polygon"]
            u = unary_union([base.buffer(eps), b.buffer(eps)]).buffer(-eps)
            if not isinstance(u, Polygon):
                continue
            if not GL.piece_ok(u, r, ppm):
                # only the part of the scrap that doesn't give the coast a
                # spit of its own; the rest stays ocean
                add = b.intersection(GL._open(u, r))
                u = unary_union([base.buffer(eps), add.buffer(eps)]).buffer(-eps)
                if add.area < 0.5 * ppm ** 2 or not isinstance(u, Polygon) or not GL.piece_ok(u, r, ppm):
                    continue
                b = add
            f2 = f.difference(b.buffer(eps))
            if oversized_oriented(u, cfg) or (len(qa_defects(GL._parts(f2), bulbs, ppm))
                                              >= len(qa_defects([f], bulbs, ppm))):
                continue
            land[k]["polygon"] = u
            f = f2
        out += [g for g in GL._parts(f) if g.area > ppm ** 2]
    return out, land


def oversized_oriented(poly, cfg):
    """Rotation-invariant _vg_oversized: ring pieces sit at every angle, so the
    axis-aligned bbox test would flag diagonal pieces falsely."""
    ppm = cfg.px_per_mm
    if poly.area <= (50 * ppm) ** 2:
        return False
    # The solid frame (frame_mm > 0) is a giant annulus by design -- correctly
    # "oversized" by ordinary standards, and there is only ever one of it.
    # Exempt anything more hole than material (a frame's defining shape) so
    # it doesn't perpetually trip this check, which would also skew variant
    # scoring away from an otherwise-clean layout during generate()'s search.
    polys = poly.geoms if poly.geom_type == "MultiPolygon" else [poly]
    hole_area = sum(Polygon(r).area for p in polys for r in p.interiors)
    if hole_area > poly.area * 2:
        return False
    # The undivided hub medallion background around a center_text block is
    # correctly "oversized" by ordinary standards too -- it's deliberately
    # one big solid piece (spokes skipped) instead of pinwheel wedges, same
    # reasoning as the frame exemption above. Exempt anything centered on
    # and mostly inside the hub circle.
    if _center_medallion is not None:
        cx, cy, r_h = _center_medallion
        c = poly.centroid
        if math.hypot(c.x - cx, c.y - cy) < r_h:
            return False
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mrr = poly.minimum_rotated_rectangle
    xs, ys = mrr.exterior.coords.xy
    e = sorted(math.hypot(xs[i + 1] - xs[i], ys[i + 1] - ys[i]) for i in range(2))
    return e[0] > 34 * ppm or e[1] > 64 * ppm


@contextlib.contextmanager
def _oriented_oversized():
    """_vg_absorb calls geometry._vg_oversized internally; swap in the
    rotation-invariant test for the duration of a ring build only. (In the
    real implementation, give _vg_oversized an `oriented` flag instead.)"""
    orig = G._vg_oversized
    G._vg_oversized = oversized_oriented
    try:
        yield
    finally:
        G._vg_oversized = orig


def score(surround, panel, cfg):
    thin = sum(1 for p in surround if G._vg_thin_bridge(p, cfg))
    over = sum(1 for p in surround if oversized_oriented(p, cfg))
    slv = sum(1 for p in surround if G._vg_sliver(p, cfg))
    nub = sum(1 for p in surround if G._vg_border_nub(p, panel, cfg))
    return (thin, over, slv, nub)


def make_cfg(rp):
    """Name-plate tab settings (banner_puzzle_config's fat capsule tabs, 4mm
    letter clearance) on a square canvas. Both border floors are 7mm: a ring has
    no 'short dimension', every tab near the rim gets the generous floor."""
    return G.PuzzleConfig(
        panel_mm=rp.diameter_mm,
        panel_h_mm=rp.diameter_mm,
        piece_mm=25,
        tab_circle_r_px=rp.tab_r_px,
        tab_stem_w_px=rp.tab_stem_px,
        letter_clearance_mm=rp.letter_clearance_mm,
        corner_radius_mm=0.0,
        tab_border_floor_v_mm=rp.border_floor_mm,
        tab_border_floor_h_mm=rp.border_floor_mm,
        font_path=rp.font,
    )


def generate(words, seed, rp):
    """Same (pieces, ...) shape as geometry.generate_pieces: carve pockets,
    fuse counters, append letters, absorb letter slivers. `words` is a single
    word (str) or a list of words, each followed by one rp.ornament slot
    (see fit_ring)."""
    cfg = make_cfg(rp)
    if rp.shape == "square":
        cfg = replace(cfg, corner_radius_mm=5.0)
    piece_polys, letter_union, cfg, L, st, panel, C = build_ring(
        words, seed, rp, cfg
    )
    land = st.get("land") or []
    cut_union = (unary_union([letter_union] + [p["polygon"] for p in land])
                 if land else letter_union)
    frags = G.carve_letter_pockets(piece_polys, cut_union)
    frags = G.fuse_counter_fragments(frags, letter_union, cfg)
    if rp.shape == "square":
        frags = G.round_panel_corners(frags, cfg)
    lp = [
        g
        for g in (
            letter_union.geoms
            if isinstance(letter_union, MultiPolygon)
            else [letter_union]
        )
        if g.area > 100
    ]
    pieces = list(frags) + [
        {"parent": None, "polygon": g, "kind": "letter"} for g in lp
    ] + [
        {"parent": None, "polygon": p["polygon"], "kind": "land", "names": p["names"],
         "borders": p.get("borders", [])}
        for p in land
    ]
    pieces = G.absorb_letter_slivers(pieces, letter_union, cfg)
    # jigsaw.render_preview's draw_geom always paints a piece's holes WHITE,
    # unconditionally, on the assumption that holes are small (a letter
    # counter) relative to the canvas -- true for every OTHER layout, but the
    # solid frame (frame_mm > 0) is a giant annulus whose "hole" is the ENTIRE
    # puzzle interior. If anything else were drawn before it in piece-list
    # order, the frame's white hole-paint wipes it out again (this bit a
    # 2-hole letter "B" in a 3-word ring: its list position happened to fall
    # after the frame). Draw largest-hole-first so a piece never gets
    # overpainted by something enclosing it; harmless for ordinary small
    # letter counters, which never enclose another whole piece.
    def _hole_area(poly):
        polys = poly.geoms if isinstance(poly, MultiPolygon) else [poly]
        return sum(Polygon(r).area for p in polys for r in p.interiors)

    pieces.sort(key=lambda p: -_hole_area(p["polygon"]))
    # Snap every outline to a fine (0.01mm) shared grid: cheap insurance against
    # true floating-point duplicate vertices (near-zero-length stub edges) from
    # the rotation + carving pipeline, with shared boundaries snapping
    # identically so pieces still tile exactly. NOT a fix for visible cut
    # quality -- keep this far finer than the kerf/laser spot size. An earlier
    # version used a 1px (0.2mm) grid to kill 0.00-0.04mm stub edges that were
    # causing laser restarts; once outline_smooth_px (glyph simplification) and
    # emitter._collapse_shuttles() landed, those were the actual fix and the
    # coarse grid was redundant -- it also visibly faceted every curved seam
    # (each sampled point nudged up to 0.1mm off-curve). 0.01mm keeps the
    # float-noise insurance without any visible effect.
    for p in pieces:
        p["polygon"] = shapely.set_precision(p["polygon"], 0.01 * cfg.px_per_mm)
        if isinstance(p["polygon"], MultiPolygon) and len(p["polygon"].geoms) == 1:
            p["polygon"] = p["polygon"].geoms[0]  # 1-part Multi: plain Polygon for consumers
    if land:
        pieces = _fold_ocean_scraps(pieces, cfg)
    for i, p in enumerate(pieces, 1):
        p["serial"] = i
    # QA again on the FINAL pieces (after pocket carving / counter fusing /
    # sliver absorption), so nothing breakable slips in after the search.
    nonletter = [p for p in pieces if p["kind"] != "letter"]
    defects = qa_defects([p["polygon"] for p in nonletter], st.get("bulbs", []), cfg.px_per_mm)
    # small letter counters (an A's triangle) stay in place on breakaway
    # bridges instead of dropping through the cutting bed
    bridged = add_counter_bridges(
        {i for _k, _q, i in defects}, nonletter, letter_union_of(pieces), cfg)
    st["bridged_counters"] = len(bridged)
    # small land pieces (islands, micro-countries) stay in place on
    # breakaway bridges too, like small letter counters
    st["bridged_land"] = add_land_bridges(nonletter, cfg, rp.land_bridge_mm)
    st["defects_final"] = [(k, (q.x, q.y)) for k, q, i in defects if i not in bridged]
    st["land_oversized"] = sum(1 for p in nonletter
                               if p["kind"] == "land" and oversized_oriented(p["polygon"], cfg))
    st["loose_rim"] = loose_rim_pieces(nonletter, st.get("bulbs", []), panel, cfg)
    return pieces, cfg, L, st, panel, C


def letter_union_of(pieces):
    return unary_union([p["polygon"] for p in pieces if p["kind"] == "letter"])


def _corner_arcpos(ring, min_turn_deg=30.0):
    """Arc-length positions of a closed ring's corners (vertices turning by
    more than min_turn_deg after simplification)."""
    pts = list(ring.coords)[:-1]
    out, pos = [], 0.0
    n = len(pts)
    cum = [0.0]
    for k in range(n):
        cum.append(cum[-1] + math.dist(pts[k], pts[(k + 1) % n]))
    for k in range(n):
        a, b, c = pts[k - 1], pts[k], pts[(k + 1) % n]
        a1 = math.atan2(b[1] - a[1], b[0] - a[0])
        a2 = math.atan2(c[1] - b[1], c[0] - b[0])
        if abs(math.degrees(math.remainder(a2 - a1, 2 * math.pi))) > min_turn_deg:
            out.append(cum[k])
    return out, cum[-1]


def counter_bridge_points(poly, ppm, n=3):
    """n points round a counter's edge, evenly spaced and placed as far from
    its corners as that spacing allows (an A's triangle gets one near the
    middle of each side)."""
    ring = poly.simplify(0.3 * ppm).exterior
    corners, P = _corner_arcpos(ring)
    if P <= 0:
        return []
    step = P / n

    def gap(s_):  # arc distance to the nearest corner
        return min((min(abs(s_ - c), P - abs(s_ - c)) for c in corners), default=P)

    best = max((o * 0.25 * ppm for o in range(max(1, int(step / (0.25 * ppm))))),
               key=lambda o: min(gap((o + k * step) % P) for k in range(n)))
    out = []
    for k in range(n):
        q = ring.interpolate((best + k * step) % P)
        out.append((q.x, q.y))
    return out


def add_counter_bridges(idxs, nonletter, letters, cfg, small_mm=10.0, n=3):
    """Hold letter counters in place with breakaway bridges, recorded as
    image-px points in p["bridges"] (the emitter leaves a short uncut gap of
    the cut line at each). Bridged: every counter -- a piece lying inside a
    hole of a letter -- that is in `idxs` (too thin to survive loose) or whose
    minimum rotated rectangle fits within small_mm x small_mm (small enough to
    drop through the cutting bed). Each gets n bridges, evenly spaced and away
    from corners. Returns the set of indices bridged."""
    ppm = cfg.px_per_mm
    holes = [Polygon(r) for g in getattr(letters, "geoms", [letters]) for r in g.interiors]
    done = set()
    for i, p in enumerate(nonletter):
        poly = p["polygon"]
        if not any(h.buffer(0.2 * ppm).contains(poly) for h in holes):
            continue  # not a counter
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            mrr = poly.minimum_rotated_rectangle
        xs, ys = mrr.exterior.coords.xy
        sides = [math.hypot(xs[k + 1] - xs[k], ys[k + 1] - ys[k]) / ppm for k in range(2)]
        if i not in idxs and max(sides) > small_mm:
            continue
        pts = counter_bridge_points(poly, ppm, n)
        if pts:
            p["bridges"] = pts
            done.add(i)
    return done





def loose_rim_pieces(nonletter, bulbs, panel, cfg):
    """Centroids of background pieces on the outer edge with no tab or
    socket at all: nothing holds them in the disc. (An enclosed tabless
    piece -- a letter counter, an ocean "lake" ringed by land -- is held by
    its surroundings like a letter is; only the rim can let one slide out.)"""
    ppm = cfg.px_per_mm
    tabs = unary_union([b.buffer(1.0 * ppm) for b in bulbs]) if bulbs else None
    out = []
    for p in nonletter:
        g = p["polygon"]
        if p["kind"] != "cell" or g.buffer(0.6).intersection(panel.boundary).length < 1.0 * ppm:
            continue
        if tabs is None or not g.intersects(tabs):
            c = g.representative_point()
            out.append((c.x, c.y))
    return out


def add_land_bridges(nonletter, cfg, small_mm=10.0, n=3):
    """Breakaway bridges (p["bridges"], as add_counter_bridges) on every land
    piece whose minimum rotated rectangle fits within small_mm x small_mm --
    a tabless island that small would drop through the cutting bed."""
    ppm = cfg.px_per_mm
    k = 0
    for p in nonletter:
        if p["kind"] != "land":
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            mrr = p["polygon"].minimum_rotated_rectangle
        xs, ys = mrr.exterior.coords.xy
        if max(math.hypot(xs[q + 1] - xs[q], ys[q + 1] - ys[q]) for q in range(2)) > small_mm * ppm:
            continue
        pts = counter_bridge_points(p["polygon"], ppm, n)
        if pts:
            p["bridges"] = pts
            k += 1
    return k


def render_debug_overlay(png_path, seams):
    """Draw every raw seam over a rendered preview: green = tabbed, blue =
    kept untabbed (hub arcs / spokes), red = dropped (its two faces merged)."""
    img = Image.open(png_path).convert("RGB")
    d = ImageDraw.Draw(img)
    col = {"ok": (0, 150, 0), "drop": (230, 0, 0), "plain": (0, 0, 230)}
    for s in seams:
        st = s.get("status")
        d.line(
            [tuple(p) for p in s["pts"]],
            fill=col.get(st, (255, 140, 0)),
            width=5 if st == "drop" else 2,
        )
        if st == "drop":
            d.text(tuple(s["pts"][len(s["pts"]) // 2]), s["kind"], fill=(200, 0, 0))
    img.save(png_path)


def main():
    import jigsaw as J

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "word",
        help="one word, or several separated by '+' (e.g. NORA+BECS+ALEX) -- "
        "each word gets one --ornament slot after it",
    )
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--center-word", default=None,
                    help="name (or 0-based index) of the ring word to centre at 12 o'clock")
    ap.add_argument("--variants", type=int, default=12)
    ap.add_argument("--shape", choices=("disc", "square"), default="disc")
    ap.add_argument("--diameter-mm", type=float, default=290.0,
                    help="disc diameter; default 290 fits 300mm stock with 5mm spare (RING_SPEC)")
    ap.add_argument("--max-xy-mm", type=float, default=290.0,
                    help="refuse to write GCode with any X/Y coordinate outside [0, this]; "
                         "the machine hard-stops near Y=297")
    ap.add_argument("--rows", type=int, choices=(2, 3), default=3)
    ap.add_argument("--keep-seams", action="store_true",
                    help="never drop a seam that found no tab -- cut it plain (no merged pieces)")
    ap.add_argument("--ornament-scale", type=float, default=1.0,
                    help="scale the ornaments (hearts etc.) up or down; 1.0 = default size")
    ap.add_argument("--pack-words", action="store_true",
                    help="keep letters inside a name tight; put the spare room either side of the ornaments")
    ap.add_argument("--split-mm", type=float, default=0.0,
                    help="split any piece longer than this (mm) with a tabbed cross seam (0 = off)")
    ap.add_argument("--target-w-mm", type=float, default=None,
                    help="target piece width (default 46); lower it to cut wide gaps between "
                         "few, small names into regular-size pieces")
    ap.add_argument("--hub-r-min-mm", type=float, default=None,
                    help="smallest center-disc radius (default 22); raise it for bigger center text "
                         "when there are few outer names")
    ap.add_argument("--cap-max-mm", type=float, default=None,
                    help="largest outer-letter cap height to try (default 46); lower it to "
                         "give the center disc and its text more room")
    ap.add_argument("--ornament", default="heart", help="heart | dot | star | none")
    ap.add_argument("--font", default=None)
    ap.add_argument(
        "--no-distinct-tabs", action="store_true",
        help="disable per-edge tab size variation, all tabs identical (old behavior)",
    )
    ap.add_argument(
        "--center-text", default=None,
        help="two words for the hub medallion, e.g. 'THE+PAULS' (forces the hub disc undivided)",
    )
    ap.add_argument("--debug", action="store_true", help="overlay seam status")
    ap.add_argument(
        "--center-style", choices=("medallion", "flat", "ring"), default="medallion",
        help="medallion = fused words + undivided hub (original); flat / ring = "
        "loose letters cut out of the normal pie-sliced hub",
    )
    ap.add_argument("--center-cap-max-mm", type=float, default=None,
                    help="cap the loose center letters' auto-maximised height (default 30)")
    ap.add_argument(
        "--center-split", action="store_true",
        help="ring-style center text: split the hub with a tabbed divider between the two "
        "words (+ inner circle, cross seam, band radials) instead of a pinwheel",
    )
    ap.add_argument(
        "--outline-etch-mm", type=float, default=0.0,
        help="etch an outline this far inside AND outside every letter cut "
        "(0 = off). Shows which side is up, and reads before painting",
    )
    ap.add_argument("--gcode", default=None, help="also emit cut GCode to this path")
    ap.add_argument("--frame-notches", action="store_true",
                    help="also cut the keyed stand feet's notches into the stock edge LAST, in "
                         "2mm slices alternating between the two slots (stand_feet_sheet.py)")
    ap.add_argument("--corner-feet", action="store_true",
                    help="with --frame-notches left: also cut 2 stand feet from the sheet's "
                         "right-hand corners (base on the x=295 stock edge)")
    ap.add_argument("--notch-edge", choices=("left", "bottom"), default="left",
                    help="stock edge for --frame-notches: left (-X, cut to x=-10) or bottom "
                         "(-Y, cut to y=-10)")
    ap.add_argument("--material", default="plywood_baltic_birch_3mm")
    ap.add_argument("--feed", type=int, default=None)
    ap.add_argument("--passes", type=int, default=None)
    ap.add_argument("--etch-power", type=float, default=None, help="outline etch power %% (default: material etch profile)")
    ap.add_argument("--etch-feed", type=int, default=None, help="outline etch feed mm/min (default: material etch profile)")
    ap.add_argument("--etch-arc-tol", type=float, default=None,
                    help="fit outline etch strokes to G2/G3 arcs within this many mm "
                         "(default off: G1 polylines; see quickcut/arcfit.py)")
    ap.add_argument("--etch-smooth-mm", type=float, default=None,
                    help="smooth outline etch strokes (spline + tangent G2/G3 biarcs) moving "
                         "them at most this many mm, e.g. 0.10 (default off)")
    ap.add_argument(
        "--max-backtrack-ms", type=float, default=2000.0,
        help="re-trace already-cut line up to this many ms to avoid a restart+warmup",
    )
    ap.add_argument(
        "--min-segment-mm", type=float, default=0.3,
        help="drop G1 chords shorter than this (mm) so GRBL never stalls on micro-moves",
    )
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    words = [w.upper() for w in a.word.split("+") if w.strip()]
    rp = RingParams(
        diameter_mm=a.diameter_mm,
        shape=a.shape,
        rows=a.rows,
        **({"cap_h_max_mm": a.cap_max_mm} if a.cap_max_mm is not None else {}),
        **({"hub_r_min_mm": a.hub_r_min_mm} if a.hub_r_min_mm is not None else {}),
        **({"target_w_mm": a.target_w_mm} if a.target_w_mm is not None else {}),
        pack_words=a.pack_words,
        ornament_scale=a.ornament_scale,
        keep_seams=a.keep_seams,
        split_mm=a.split_mm,
        variants=a.variants,
        ornament=None if a.ornament == "none" else a.ornament,
        font=a.font,
        distinct_tabs=not a.no_distinct_tabs,
        center_text=tuple(a.center_text.upper().split("+")) if a.center_text else None,
        center_style=a.center_style,
        center_split=a.center_split,
        **({"center_loose_cap_max_mm": a.center_cap_max_mm} if a.center_cap_max_mm is not None else {}),
        center_word=_center_word_index(a.center_word, words),
    )
    tag = "-".join(words)
    pieces, cfg, L, st, _panel, _C = generate(words, a.seed, rp)
    out = Path(
        a.out
        or Path(__file__).resolve().parent.parent
        / "figs"
        / f"ring_{tag}_{a.shape}.png"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    title = f"{tag} ring {a.shape} seed {a.seed}  cap {L['cap_mm']:.0f}mm  {len(pieces)}pc  score {st['score']}"
    J.render_preview(pieces, cfg, title, out)
    if a.debug:
        render_debug_overlay(out, st["seams"])
    strokes = []
    if a.outline_etch_mm > 0:
        from letter_outline_etch import draw_strokes, letter_outline_strokes

        strokes, warns = letter_outline_strokes(
            [p["polygon"] for p in pieces if p["kind"] == "letter"],
            _panel, cfg.px_per_mm, a.outline_etch_mm, a.outline_etch_mm,
        )
        for w in warns:
            print("WARNING outline etch:", w)
        draw_strokes(out, strokes)
    if st.get("defects") or st.get("defects_final"):
        bad = st.get("defects", []) + st.get("defects_final", [])
        print(f"QA FAILED: {len(bad)} breakable feature(s) no variant could repair: "
              + ", ".join(f"{k} near image ({x / cfg.px_per_mm:.0f},{y / cfg.px_per_mm:.0f})mm"
                          for k, (x, y) in bad))
    if a.gcode:
        from emitter import load_material

        material = load_material(a.material)
        if a.passes is not None:
            material = {**material, "laser": {**material["laser"], "passes": a.passes}}
        gcode = J._emit_cut_for(
            pieces, material, cfg, tag, "ring", mode="static",
            feed_override=a.feed, power_percent=100.0,
            min_segment_mm=a.min_segment_mm,
            max_backtrack_ms=a.max_backtrack_ms,
        )
        _check_xy_envelope(gcode, a.max_xy_mm)
        from emitter import combine_passes

        passes = []
        if strokes:
            from emitter import emit_etch_gcode

            passes.append(emit_etch_gcode(  # etch first, while still in the stock
                strokes, material, cfg, f"{tag} letter outlines",
                feed_override=a.etch_feed, power_percent=a.etch_power,
                min_segment_mm=a.min_segment_mm,
                arc_tolerance_mm=a.etch_arc_tol,
                smooth_mm=a.etch_smooth_mm,
            ))
        if passes:
            gcode = combine_passes(*passes, gcode)
        if a.frame_notches:
            # notches in the stock frame for the keyed stand feet (RING_SPEC.md
            # §13.18), cut LAST, deepest slice first, alternating between the
            # two slots: if the head hits an end stop near the stock edge and
            # the job aborts, everything else is already cut (§16.2)
            import stand_feet_sheet as SF

            if a.corner_feet:
                gcode = combine_passes(gcode, corner_feet_pass(material))
            gcode = combine_passes(gcode, frame_notch_pass(material, a.notch_edge))
            _check_xy_envelope(gcode, SF.REACH_MAX_MM, SF.REACH_MIN_MM, SF.REACH_MAX_Y_MM)
        else:
            _check_xy_envelope(gcode, a.max_xy_mm)
        Path(a.gcode).write_text(gcode)
        from ring_lint import lint_motion

        slow = lint_motion(gcode)
        if slow:
            print(f"MOTION QA ({slow[0].severity}, quickcut/grbl_model.py, UNVERIFIED "
                  f"machine profile): {len(slow)} stroke(s) predicted > x1.3 energy/mm "
                  "under M3; worst: " + "; ".join(f.detail for f in slow[:3]))
        png, _svg = J.render_gcode_previews(
            gcode, cfg, Path(a.gcode).with_suffix(""), title=f"{tag} ring cut"
        )
        print(f"-> {a.gcode}  ({len(gcode.splitlines())} lines), toolpath {png}")
    print(
        f"{tag}: cap {L['cap_mm']:.1f}mm, min letter gap {L['min_gap_mm']:.1f}mm, "
        f"hub r {L['r_h'] / cfg.px_per_mm:.0f}mm, {len(pieces)} pieces, "
        f"score (QA defects, thin, oversized, sliver, nub, dropped) = {st['score']} -> {out}"
    )


def _center_word_index(spec, words):
    if spec is None:
        return None
    if spec.isdigit():
        return int(spec)
    if spec.upper() not in words:
        raise SystemExit(f"--center-word {spec!r} is not one of {words}")
    return words.index(spec.upper())


def corner_feet_pass(material):
    """G-code for two keyed stand feet cut from the puzzle sheet's right-hand
    corners (stand_feet_sheet.corner_feet): each foot's holes, then its
    outline. Runs after the puzzle cut, before the frame notches."""
    import display_stand as DS
    import stand_feet_sheet as SF

    g, _info, _feet = SF.best_foot(75.0, 32)
    feet = SF.corner_feet(g)
    paths = []
    for f in feet:
        paths += [list(r.coords) for r in f.interiors]
        paths.append(list(f.exterior.coords))
    gap = min(f.distance(Point(145.0, 145.0).buffer(145.0, quad_segs=128).exterior)
              for f in feet)
    return DS.emit_gcode(paths, material, "stand feet from the sheet's right-hand corners", notes=[
        "2 keyed stand feet, base along the right stock edge x=295, nose in the "
        "top-right / bottom-right corner",
        f"{gap:.1f}mm of frame between each foot and the disc cut; the base cut runs "
        "along the stock edge (may mark the bed)",
        "SAME machine frame as the puzzle cut: run it on the same sheet without moving it"])


def frame_notch_pass(material, notch_edge="left"):
    """G-code for the keyed stand feet's frame notches, to append LAST
    (stand_feet_sheet.frame_notch_sections): a U per slot inside safe travel,
    then 2mm slices alternating between the slots past the axis zero."""
    import display_stand as DS
    import stand_feet_sheet as SF

    axis = "x" if notch_edge == "left" else "y"
    return DS.emit_gcode(
        SF.frame_notch_sections(edge=3 if notch_edge == "left" else 0), material,
        "frame notches for the keyed stand feet (LAST)",
        notes=[f"{notch_edge} stock edge, 2 slots: one U each from the slot end to {axis}=0, "
               f"then {axis}<0 in 2mm slices alternating slot A / slot B out to {axis}=-10",
               "if the head hits an end stop and aborts here, the puzzle is done: "
               "finish the remaining slot ends with a file, saw or knife",
               "SAME machine frame as the puzzle cut: run it on the same sheet "
               "without moving it"])


def _check_xy_envelope(gcode, max_xy_mm, min_xy_mm=0.0, max_y_mm=None):
    """Hard stop before writing: every X/Y word (G0 travel included) must lie
    in [min_xy_mm, max_xy_mm] (Y capped at max_y_mm when given). The Y limit
    switch tripped at MPos Y=294.453 (stand_feet_sheet.REACH_MAX_Y_MM);
    -10mm X/Y is confirmed reachable."""
    import re

    bad = []
    for n, line in enumerate(gcode.splitlines(), 1):
        code = line.split(";")[0]
        for ax, v in re.findall(r"([XY])(-?[\d.]+)", code):
            hi = max_y_mm if (ax == "Y" and max_y_mm is not None) else max_xy_mm
            if not (min_xy_mm <= float(v) <= min(hi, max_xy_mm)):
                bad.append(f"line {n}: {ax}{v}")
    if bad:
        raise SystemExit(
            f"REFUSING to write GCode: {len(bad)} coordinate(s) outside {min_xy_mm}..{max_xy_mm}mm "
            f"(first: {bad[0]}). Use a smaller --diameter-mm."
        )


if __name__ == "__main__":
    main()
