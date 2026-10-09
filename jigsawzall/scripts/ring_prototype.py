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
from shapely.geometry import LineString, MultiPoint, MultiPolygon, Point, Polygon, box  # noqa: E402
from shapely.ops import linemerge, nearest_points, polygonize, unary_union  # noqa: E402

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
        glyphs = {k: _wkt.loads(v) for k, v in data["glyphs"].items()}
        _planet_logo_glyphs_cache = (glyphs, data["cap_ref_px"], data["ring"])
    return _planet_logo_glyphs_cache


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
    # Thinnest wood the stock survives (mm), normally the material profile's
    # min_feature_mm (3.0 on the stiff 3-ply veneer). Every wood-strength limit
    # left as None below is derived from it in __post_init__.
    min_feature_mm: float = 3.0
    hub_arc_mm: float = 30.0  # min hub-circle arc per hub-ring piece
    # min radial depth of the band between the outer letters and the center
    # disc. None = 4.4 * min_feature_mm: the largest tab class's bulb
    # (2 * 1.2t) plus a t wall each side -- as thin as a tabbed seam allows,
    # so every letter gets the room (was a hand-set 26mm).
    hub_ring_min_mm: float | None = None
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
    tab_r_px: float | None = None  # bulb radius: min_feature_mm
    tab_stem_px: float | None = None  # neck width: 2 * min_feature_mm
    border_floor_mm: float | None = None  # tab <-> disc edge wall: min_feature_mm
    letter_clearance_mm: float | None = None  # tab <-> letter wall: min_feature_mm
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
    #   "hull"      -- (default) same loose letters, but no hub circle at all:
    #                  the whole inside of the ring is one continuous puzzle
    #                  grid; each center word's outline (never cut) is treated
    #                  like one big ring letter -- seams stop at it and finish on
    #                  the nearest small letter (see hull_center_seams).
    center_style: str = "hull"
    # Leave the hub circle (centre disc <-> ring) as a plain, tab-free circular
    # cut so the assembled centre disc turns freely: nobody's name is favoured
    # as "up". The disc's own spokes keep their tabs and hold it together.
    hub_rotates: bool = True
    center_track_mm: float = 2.0  # starting extra spacing between loose center letters
    # Wood between two adjacent loose center letters is a short finger
    # attached only at its ends -- must be at least this wide (Alex: 3mm is
    # fine on the stiff 3-ply veneer stock; was 5mm). Tracking widens until
    # it holds, then cap height shrinks if it can't fit.
    center_min_gap_mm: float | None = None  # = min_feature_mm
    # Loose center text (flat/ring) searches DOWN from this cap height in
    # 0.5mm steps and keeps the largest that fits -- i.e. it fills the hub
    # instead of stopping at center_text_cap_mm (the medallion's fixed start).
    center_loose_cap_max_mm: float = 30.0
    # Loose center text (flat) at EXACTLY this cap height: the hub disc grows
    # (outer ring letters shrink) until the center text fits at this size.
    # None = old behaviour (hub sized by the ring; center fills what's left).
    center_cap_target_mm: float | None = None
    # Print one line per variant (score + elapsed + ETA) while generating.
    progress: bool = False

    def __post_init__(self):
        t, ppm = self.min_feature_mm, 5  # PuzzleConfig.px_per_mm
        if self.tab_r_px is None:
            self.tab_r_px = int(round(t * ppm))  # px canvas sizes derive from it
        if self.tab_stem_px is None:
            self.tab_stem_px = 2 * t * ppm
        if self.border_floor_mm is None:
            self.border_floor_mm = t
        if self.letter_clearance_mm is None:
            self.letter_clearance_mm = t
        if self.center_min_gap_mm is None:
            self.center_min_gap_mm = t
        if self.hub_ring_min_mm is None:
            self.hub_ring_min_mm = 2 * max(TAB_SIZE_CLASSES) * t + 2 * t


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


def glyph_local(ch, font):
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
    s = 0.55 * cap_h / (p.bounds[3] - p.bounds[1])
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
        ref = G.find_font(1000, rp.font)
        cap_ratio = (ref.getbbox("H")[3] - ref.getbbox("H")[1]) / 1000.0
        font = G.find_font(max(8, int(round(cap_mm * ppm / cap_ratio))), rp.font)
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
    ref = G.find_font(1000, rp.font)
    cap_ratio = (ref.getbbox("H")[3] - ref.getbbox("H")[1]) / 1000.0
    return G.find_font(max(8, int(round(cap_mm * ppm / cap_ratio))), rp.font)


def _word_advances(word, font, track_px):
    """Per-letter local glyphs plus each letter's centre offset along the
    word (x=0 at the word's centre), using the font's own advances plus
    `track_px` extra between letters."""
    # simplify: traced glyphs are 1px staircases (see letter_glyph in fit_ring)
    glyphs = [glyph_local(ch, font).simplify(RingParams.outline_smooth_px) for ch in word]
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
    floor_mm = rp.center_text_cap_min_mm
    if rp.center_cap_target_mm is not None:
        cap_mm = floor_mm = rp.center_cap_target_mm
    while cap_mm >= floor_mm:
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


def center_hub_r_needed_mm(rp, ppm):
    """Hub radius (mm) the loose center text needs to fit at exactly
    rp.center_cap_target_mm with rp.center_min_gap_mm between letters, using
    the same tracking search as center_letters but in an unbounded hub."""
    if rp.center_style not in ("flat", "hull"):
        raise SystemExit("--center-cap-mm is only implemented for --center-style hull/flat")
    key = (rp.center_text, rp.font, rp.center_cap_target_mm, rp.center_min_gap_mm,
           rp.center_track_mm, rp.center_text_gap_mm, rp.center_text_fit_frac, ppm)
    if key not in _HUB_NEED_CACHE:
        _HUB_NEED_CACHE[key] = _center_hub_r_needed_mm(rp, ppm)
    return _HUB_NEED_CACHE[key]


_HUB_NEED_CACHE = {}


def _center_hub_r_needed_mm(rp, ppm):
    cap_mm = rp.center_cap_target_mm
    track_mm = rp.center_track_mm
    while track_mm <= rp.center_track_mm + 10:
        out = _center_layout(rp, ppm, 1e9, (0.0, 0.0), cap_mm, track_mm * ppm)
        gaps = [min(a.distance(b) for j, b in enumerate(out) if j != i)
                for i, a in enumerate(out)]
        if min(gaps) >= rp.center_min_gap_mm * ppm:
            r = max(math.hypot(px, py) for g in out
                    for px, py in g.convex_hull.exterior.coords)
            # +0.5mm: the fit check downstream is r <= r_h*frac; float ties fail it
            return r / ppm / rp.center_text_fit_frac + 0.5
        track_mm += 0.5
    raise SystemExit(
        f"center text {rp.center_text!r} can't reach {rp.center_min_gap_mm}mm letter gaps "
        f"at {cap_mm}mm cap within {rp.center_track_mm + 10}mm tracking")


def _center_layout(rp, ppm, r_h, C, cap_mm, track):
    """One layout attempt at a given cap height and tracking; None if it
    doesn't fit the hub."""
    w1, w2 = rp.center_text
    limit = r_h * rp.center_text_fit_frac
    font = _center_font(rp, cap_mm, ppm)
    cap = cap_mm * ppm
    out = []
    if rp.center_style in ("flat", "hull"):
        # the strip between the two lines is wood too -- same floor applies
        gap = max(rp.center_text_gap_mm, rp.center_min_gap_mm) * ppm + 1
        if rp.center_style == "hull":
            # room for a TABBED, wobbly seam between the two words: on each
            # side the biggest tab (3 x its 1.2t bulb radius) + t of wood to
            # the letters + the seam's 0.6t wobble
            t_ = rp.min_feature_mm * ppm
            gap = max(gap, 2 * (3 * max(TAB_SIZE_CLASSES) * t_ + t_ + 0.6 * t_) + 1)
        g1, x1, _t1 = _word_advances(w1, font, track)
        g2, x2, _t2 = _word_advances(w2, font, track)
        # line 1 baseline above centre, line 2 baseline below
        y1 = -gap / 2
        y2 = gap / 2 + cap
        # Round letters overshoot the baseline / cap line (C O S G dip below,
        # O C bulge above), so baseline spacing alone leaves < gap of wood
        # between the lines. Measure the real shapes and push the lines apart.
        l1 = unary_union([affinity.translate(g, x, 0) for g, x in zip(g1, x1)])
        l2 = unary_union([affinity.translate(g, x, 0) for g, x in zip(g2, x2)])
        d = affinity.translate(l1, 0, y1).distance(affinity.translate(l2, 0, y2))
        if d < gap:
            y1 -= (gap - d) / 2
            y2 += (gap - d) / 2
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
            flip = wi == 1
            seq = word[::-1] if flip else word
            gl, xs, total = _word_advances(seq, font, track)
            if total / r_m > math.radians(160):
                ok = False
                break
            th0 = 0.0 if not flip else math.pi
            r_base = r_m - cap / 2 if not flip else r_m + cap / 2
            for g, x in zip(gl, xs):
                if flip:
                    g = affinity.rotate(g, 180, origin=(0, 0))
                out.append(place(g, th0 + x / r_m, r_base, C))
        return out if ok and r_m - cap / 2 > 0 else None


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
    hub_min_mm = rp.hub_r_min_mm
    if rp.center_text and rp.center_cap_target_mm is not None:
        hub_min_mm = max(hub_min_mm, center_hub_r_needed_mm(rp, ppm))
    is_logo_font = rp.font == PLANET_LOGO_FONT
    if is_logo_font:
        _logo_glyphs, _logo_cap_ref_px, _ = _load_planet_logo_glyphs()
    else:
        ref = G.find_font(1000, rp.font)
        cap_ratio = (ref.getbbox("H")[3] - ref.getbbox("H")[1]) / 1000.0
    while True:
        cap = h_mm * ppm
        if is_logo_font:
            font = None  # no system font in play; nothing downstream reads
            # `font` when is_logo_font, except L["font_size"] below.
            logo_scale = cap / _logo_cap_ref_px
        else:
            font = G.find_font(max(10, int(round(cap / cap_ratio))), rp.font)
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
                locs.append(ornament_local(orn, cap))
                labels.append("*")
                word_of.append(None)
        n = len(locs)
        ext = [ang_extent(solid_of(g), Rin) for g in locs]
        span = sum(l + r for l, r in ext)
        beta = (2 * math.pi - span) / n  # equal angular gap
        # hub sizing
        r_h = max(hub_min_mm * ppm, n * rp.hub_arc_mm * ppm / (2 * math.pi))
        r_h = min(r_h, Rin - rp.hub_ring_min_mm * ppm)
        ths, t = [], 0.0
        for l, r in ext:
            t += l
            ths.append(t)
            t += r + beta
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
            and r_h >= hub_min_mm * ppm
        )
        if not ok and h_mm <= rp.cap_h_min_mm and r_h < hub_min_mm * ppm:
            raise SystemExit(
                f"hub radius {hub_min_mm:.1f}mm (needed for the center text) doesn't fit "
                f"even with {rp.cap_h_min_mm}mm ring letters")
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
    global _center_medallion, _frame_exempt
    _center_medallion = None
    _frame_exempt = rp.frame_mm > 0
    center_solids = []
    center_polys = []
    center_words = []
    hull_mode = bool(rp.center_text) and rp.center_style == "hull"
    L["center_cap_mm"] = None
    if rp.center_text and rp.center_style in ("flat", "ring", "hull"):
        # Loose letters cut straight out of the ordinary pinwheel hub: no
        # medallion, no exemption, spokes stay. They join letter_union (so
        # they become pockets/pieces like any letter) but NOT the outer
        # letters' seam-routing obstacles -- only tab clearance sees them.
        cl, ccap = center_letters(rp, ppm, r_h, C)
        if cl is None:
            raise SystemExit(
                f"center_text {rp.center_text!r} ({rp.center_style}) doesn't fit "
                f"hub radius {r_h / ppm:.0f}mm -- try --center-cap-mm to grow the hub"
            )
        else:
            world.extend(cl)
            center_polys = list(cl)
            n1 = sum(1 for ch in rp.center_text[0] if not ch.isspace())
            center_words = [cl[:n1], cl[n1:]]
            center_solids = [solid_of(g) for g in cl]
            L["center_cap_mm"] = ccap
    elif rp.center_text:
        found = None
        for cap_mm, r, block in center_text_block(rp, ppm):
            if r <= r_h * rp.center_text_fit_frac:
                found = block
                break
        if found is None:
            raise SystemExit(
                f"center_text {rp.center_text!r} doesn't fit hub radius "
                f"{r_h / ppm:.0f}mm even at {rp.center_text_cap_min_mm}mm cap"
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
    background = panel.difference(letter_union)
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
                pts = G._vg_curve(a, na, b, nb, obstacles({i: "a"}), ppm, clear_mm=rp.min_feature_mm) or [a, b]
                seams.append(dict(pts=curved(pts), kind="topcap", plain_ok=rp.plain_caps, ends=("L", eb)))
                top_caps.setdefault(i, []).append(ang_of(b, C))
            for p in ([] if hull_mode else bp):
                a = xf_pt(p, th, Rin, C)
                na = xf_vec((0.0, 1.0), th)
                phi = ang_of(a, C) + math.radians(rng.uniform(-6, 6))
                b = (C[0] + r_h * math.sin(phi), C[1] - r_h * math.cos(phi))
                nb = out_vec(phi)
                pts = G._vg_curve(a, na, b, nb, obstacles({i: "a"}), ppm, clear_mm=rp.min_feature_mm) or [a, b]
                seams.append(dict(pts=curved(pts), kind="botcap", plain_ok=rp.plain_caps, ends=("L", "T")))
                hub_ends.append(phi % (2 * math.pi))
            for a, b, _side in bridges:
                aw, bw = xf_pt(a, th, Rin, C), xf_pt(b, th, Rin, C)
                na = xf_vec((1.0, 0.0) if b[0] >= a[0] else (-1.0, 0.0), th)
                nb = (-na[0], -na[1])
                pts = G._vg_curve(aw, na, bw, nb, obstacles({i: "ab"}), ppm, clear_mm=rp.min_feature_mm) or [aw, bw]
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
        for g in range(n):
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
                # hull style: the innermost ring seams host the center grid's T
                # ends -- dropping one leaves those grid seams dangling
                seams.append(dict(pts=pts, kind="ring", ends=("L", "L"),
                                  plain_ok=hull_mode, structural=hull_mode))
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
                pts = G._vg_curve(a, na, b, nb, obstacles({}), ppm, clear_mm=rp.min_feature_mm) or [a, b]
                seams.append(dict(pts=curved(pts), kind="rimsub", ends=("T", eb)))
        # --- middle-band subdivisions (wide gaps on short names) ---------------
        if len(levels) == 2:
            for g in range(n):
                lo_h, up_h = ring_seams.get((g, 0)), ring_seams.get((g, 1))
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
                    pts = G._vg_curve(a, na, b, nb, obstacles({}), ppm, clear_mm=rp.min_feature_mm) or [a, b]
                    seams.append(dict(pts=curved(pts), kind="midsub", ends=("T", "T")))
        if hull_mode:
            # --- no hub: one continuous grid inside the ring ------------------
            seams.extend(hull_center_seams(
                seams, solids, panel, C, Rin, center_words, rp, ppm, rng, curved))
        # --- hub-ring subdivisions: T off the innermost ring seam to the hub ---
        hub_sorted = sorted(hub_ends) if not hull_mode else []
        r_hm = (Rin + r_h) / 2
        for g in range(n if not hull_mode else 0):
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
        if hull_mode:
            pass  # no spokes, no hub circle
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
        hs = sorted(split) if not hull_mode else []
        for q in range(len(hs)):
            b0, b1 = hs[q], hs[(q + 1) % len(hs)]
            seams.insert(
                0,
                dict(
                    pts=arc_pts(C, r_h, b0, b1, 2 * ppm), kind="hubarc", ends=("J", "J")
                ),
            )

        _snap_notch_ends(seams, tab_solids, ppm)
        with _oriented_oversized():
            surround, counters, st = assemble_qa(
                seams, letter_union, tab_solids, background, panel, cfg, C,
                distinct_tabs=rp.distinct_tabs,
                untabbed_kinds=("hubarc",) if rp.hub_rotates else (),
            )
        # st["defects"] includes oversized pieces (assemble_qa): a piece too
        # big to be fun, or wrapping all the way round, is a hard QA failure.
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


def _wobble(pts, amp, wavelength, phase, flat_ends=False, step=2.0):
    """Offset a polyline sideways by a smooth wave that vanishes at both ends
    (endpoints stay put). flat_ends also zeroes the slope there, so the line
    still meets whatever it lands on head-on (an S-curve)."""
    ln = LineString(pts)
    L = ln.length
    if L < 3 * step or amp <= 0:
        return [tuple(p) for p in pts]
    n = max(4, int(L / step))
    out = []
    for i in range(n + 1):
        sd = L * i / n
        a, b = ln.interpolate(max(0.0, sd - step / 2)), ln.interpolate(min(L, sd + step / 2))
        dx, dy = b.x - a.x, b.y - a.y
        m = math.hypot(dx, dy) or 1.0
        u = sd / L
        if flat_ends:
            w = amp * math.sin(2 * math.pi * u) * math.sin(math.pi * u)
        else:
            w = amp * math.sin(2 * math.pi * sd / wavelength + phase) * math.sin(math.pi * u)
        q = ln.interpolate(sd)
        out.append((q.x - dy / m * w, q.y + dx / m * w))
    return out


def hull_center_seams(seams, ring_solids, panel, C, Rin, center_words, rp, ppm, rng, curved):
    """Seams for the inside of the ring in "hull" style (no hub circle).

    The two center words sit a seam's width apart (see _center_layout). Each
    word's outline (convex hull padded by min_feature_mm, never cut) is
    treated like one row of big letters:
      * a wobbly middle seam runs along the gap between the words;
      * a wobbly row seam runs ~5t outside each word, out to the grid;
      * wavy column cuts run from each row seam onto a letter, and on from
        that letter to the middle seam -- so the word is cut into slices.
    A Lloyd-relaxed Voronoi grid fills the rest of the region inside the
    innermost ring seams. Ends: "J" junction, "L" letter, "T" on a seam."""
    t = rp.min_feature_mm * ppm
    # ~35mm between column cuts: each row-seam stretch between two columns
    # must hold a tab clear of both junctions' keep-out zones, or the slices
    # come out tab-free and fall out of the finished puzzle
    col = 0.75 * rp.target_w_mm * ppm
    ring_letters = unary_union(ring_solids)
    lines = [LineString(sm["pts"]) for sm in seams] + [g.boundary for g in ring_solids]
    faces = list(polygonize(shapely.union_all(lines + [panel.boundary], grid_size=0.1)))
    # The inner region is the face around the centre bounded by the innermost
    # ring seams and the ring letters -- it reaches up BETWEEN the letters, so
    # don't clip it to the letters' base circle (seams would end in mid-wood).
    R = next((f for f in faces if f.contains(Point(C))), None)
    if R is None:
        raise SystemExit("hull style: no closed inner region inside the ring seams")
    R = R.difference(ring_letters)
    if R.area > math.pi * Rin ** 2 * 1.5:
        raise SystemExit("hull style: inner region leaks past the ring letters (open ring seam)")
    if R.geom_type == "MultiPolygon":
        R = max(R.geoms, key=lambda g: g.area)
    words = [unary_union(w) for w in center_words if w]
    allw = unary_union(words)
    blocks = [w.convex_hull.buffer(t) for w in words]
    core = unary_union(blocks).convex_hull  # both outlines + the gap between them
    row_d = 3 * t  # row seams this far outside the outlines
    excl = core.buffer(row_d)
    if not R.buffer(-t).contains(excl):
        raise SystemExit("hull style: center words + row seams don't fit inside the ring")
    out = []

    # --- Voronoi grid outside the word rows --------------------------------
    free = R.difference(excl)
    a_target = 0.45 * (rp.target_w_mm * ppm) ** 2
    k = max(2, round(free.area / a_target))
    inner = free.buffer(-2 * t)
    if inner.is_empty:
        inner = free
    bx0, by0, bx1, by1 = inner.bounds
    pts = []
    while len(pts) < k:
        q = Point(rng.uniform(bx0, bx1), rng.uniform(by0, by1))
        if inner.contains(q):
            pts.append(q)
    env = R.envelope.buffer(R.length)
    for _ in range(4):  # Lloyd relaxation: even out piece sizes
        cells = shapely.voronoi_polygons(MultiPoint(pts), extend_to=env)
        new = []
        for q in pts:
            cell = next(c for c in cells.geoms if c.contains(q))
            cc = cell.intersection(free)
            new.append(cc.centroid if not cc.is_empty else q)
        pts = new
    edges = shapely.voronoi_polygons(MultiPoint(pts), extend_to=env, only_edges=True)
    net = edges.intersection(free)
    segs = [g for g in getattr(net, "geoms", [net]) if g.geom_type == "LineString"]
    segs = list(getattr(linemerge(segs), "geoms", [linemerge(segs)])) if segs else []
    segs = [ln for ln in segs if ln.length >= 2 * t]
    grid = unary_union(segs + [R.boundary])

    def end_type(q):
        return "L" if ring_letters.distance(Point(q)) < 2.0 else "T"

    def reach(p0, d, far):
        """First point on the grid along a ray from p0 (outside excl)."""
        ray = LineString([p0, (p0[0] + d[0] * far, p0[1] + d[1] * far)])
        hit = ray.difference(excl.buffer(-0.5)).intersection(grid)
        cs = [c for g in getattr(hit, "geoms", [hit]) for c in getattr(g, "coords", [])]
        return min(cs, key=lambda c: math.dist(c, p0)) if cs else None

    def horizontal(y, amp):
        """A wobbly seam at height y across the word rows, reaching out to
        the grid on both sides. Returns (pts, ends)."""
        x0, x1 = excl.bounds[0], excl.bounds[2]
        a = reach((x0 + 1, y), (-1, 0), 2 * col)
        b = reach((x1 - 1, y), (1, 0), 2 * col)
        if a is None:
            a = reach((x0 + 1, y), (-1, 0), 4 * Rin)
        if b is None:
            b = reach((x1 - 1, y), (1, 0), 4 * Rin)
        a = a or (x0, y)
        b = b or (x1, y)
        straight = [a, (x0, y), (x1, y), b]
        for k_ in range(4):  # shrink the wave until it keeps t off every letter
            pw = _wobble(straight, amp, 1.4 * col, rng.uniform(0, 2 * math.pi))
            if LineString(pw).distance(allw) >= t:
                break
            amp *= 0.6
        else:
            pw = straight
        return pw, (end_type(a), end_type(b))

    # Horizontal seams are emitted LAST, split at every point where a column
    # or grid seam lands on them: a seam carries at most one tab, so one long
    # seam across the words would get a single tab for the whole row.
    horiz = []  # [kind, pts, ends, contact points]
    rows = {}  # word index -> row seam LineString
    y_top = core.bounds[1] - row_d
    y_bot = core.bounds[3] + row_d
    pw, ends = horizontal(y_top, 0.8 * t)
    horiz.append(["hullrow", pw, ends, []])
    rows[0] = LineString(pw)
    pw, ends = horizontal(y_bot, 0.8 * t)
    horiz.append(["hullrow", pw, ends, []])
    rows[len(words) - 1] = LineString(pw)
    mid = None
    if len(words) > 1:
        y_mid = (blocks[0].bounds[3] + blocks[1].bounds[1]) / 2
        pw, ends = horizontal(y_mid, 0.6 * t)
        horiz.append(["hullmid", pw, ends, []])
        mid = LineString(pw)

    def touch(line, q):
        """Record q as a contact point on the horizontal seam `line`."""
        for h in horiz:
            if LineString(h[1]).distance(Point(q)) < 1.0:
                h[3].append(tuple(q))
                return

    def cross(line, x, y_hint):
        """Where the vertical at x meets `line` (nearest to y_hint)."""
        hit = LineString([(x, y_hint - 4 * Rin), (x, y_hint + 4 * Rin)]).intersection(line)
        cs = [c for g in getattr(hit, "geoms", [hit]) for c in getattr(g, "coords", [])]
        return min(cs, key=lambda c: abs(c[1] - y_hint)) if cs else None

    # --- column cuts: row seam -> letter -> middle seam ----------------------
    def edge_angle(g, pt):
        ring = min([g.exterior] + list(g.interiors), key=lambda r: r.distance(Point(pt)))
        d = ring.project(Point(pt))
        p0, p1 = ring.interpolate(d - 0.8 * ppm), ring.interpolate(d + 0.8 * ppm)
        dx, dy = p1.x - p0.x, p1.y - p0.y
        n = math.hypot(dx, dy) or 1.0
        return math.degrees(math.acos(min(1.0, abs(dy) / n)))  # 90 = edge square to the cut

    def column(word, x):
        """(top y, bottom y, quality) of the letter under column x. Quality 0
        if the cut leaving either side would run alongside another part of
        the same letter (a P's stem beside a cut from its bowl) -- that
        leaves a sliver."""
        for g in getattr(word, "geoms", [word]):
            if g.bounds[0] < x < g.bounds[2]:
                cut = LineString([(x, g.bounds[1] - 1), (x, g.bounds[3] + 1)]).intersection(g)
                ys = [c[1] for k_ in getattr(cut, "geoms", [cut]) for c in getattr(k_, "coords", [])]
                if ys:
                    y0, y1 = min(ys), max(ys)
                    q = min(edge_angle(g, (x, y0)), edge_angle(g, (x, y1)))
                    # other parts of this letter within t + the cut's wobble
                    # sideways, beyond where the cut leaves it (a P's stem
                    # beside a cut from its bowl) would leave a sliver
                    side = 1.8 * t
                    for lo, hi in ((g.bounds[1], y0 - 0.5 * t), (y1 + 0.5 * t, g.bounds[3])):
                        if hi > lo and not g.intersection(box(x - side, lo, x + side, hi)).is_empty:
                            q = 0.0
                    return y0, y1, q
        return None

    for wi, word in enumerate(words):
        outer_up = wi == 0
        good = []
        x = word.bounds[0] + 0.5 * ppm
        while x < word.bounds[2]:
            c = column(word, x)
            if c and c[2] >= 55:
                good.append(x)
            x += 0.5 * ppm
        # columns spaced evenly along the WHOLE row seam (out to the grid at
        # both ends): over the word they hop through a letter, beyond it
        # they're plain cuts from the row seam to the middle seam
        row = rows[wi]
        xa, xb = sorted((row.coords[0][0], row.coords[-1][0]))
        n_cols = max(1, round((xb - xa) / col) - 1)
        xs, plain = [], []
        for j in range(1, n_cols + 1):
            w = xa + (xb - xa) * j / (n_cols + 1)
            if word.bounds[0] - 0.25 * col < w < word.bounds[2] + 0.25 * col:
                cand = [g for g in good if all(abs(g - u) > 0.5 * col for u in xs)]
                if cand:
                    best = min(cand, key=lambda g: abs(g - w))
                    if abs(best - w) < 0.5 * col:
                        xs.append(best)
            elif mid is not None:
                plain.append(w)
        for x in plain:
            a_ = cross(row, x, row.centroid.y)
            b_ = cross(mid, x, mid.centroid.y)
            if a_ is None or b_ is None:
                continue
            ln_ = LineString([a_, b_])
            if ln_.distance(allw) < 1.8 * t or not R.contains(ln_):  # t + wobble
                continue
            touch(row, a_)
            touch(mid, b_)
            pw = _wobble([a_, b_], min(0.8 * t, ln_.length / 6), 0, 0, flat_ends=True)
            out.append(dict(pts=pw, kind="hullcut", plain_ok=True, structural=True,
                            ends=("J", "J")))
        for x in xs:
            y0, y1, _q = column(word, x)
            near_y, far_y = (y0, y1) if outer_up else (y1, y0)
            o = cross(rows[wi], x, near_y)
            if o is not None:
                pw = [o, (x, near_y)]
                amp = min(0.8 * t, LineString(pw).length / 6)
                touch(rows[wi], o)
                out.append(dict(pts=_wobble(pw, amp, 0, 0, flat_ends=True), kind="hullcut",
                                plain_ok=True, structural=True, ends=("J", "L")))
            if mid is not None:
                m = cross(mid, x, far_y)
                if m is not None:
                    pw = [(x, far_y), m]
                    amp = min(0.8 * t, LineString(pw).length / 6)
                    touch(mid, m)
                    out.append(dict(pts=_wobble(pw, amp, 0, 0, flat_ends=True), kind="hullcut",
                                    plain_ok=True, structural=True, ends=("L", "J")))

    # --- grid seams: ends on the rows land on the row seam; at the row ends
    # they finish on the nearest letter ---------------------------------------
    row_lines = [r for r in rows.values()] + ([mid] if mid is not None else [])
    for ln in segs:
        p = [tuple(c) for c in ln.coords]
        ends = []
        for e in (0, -1):
            q = Point(p[e])
            if excl.exterior.distance(q) < 1.0:
                # carry on in the seam's own direction onto the nearest row seam
                a_ = p[1] if e == 0 else p[-2]
                dx, dy = q.x - a_[0], q.y - a_[1]
                m_ = math.hypot(dx, dy) or 1.0
                ray = LineString([(q.x, q.y), (q.x + dx / m_ * 3 * row_d, q.y + dy / m_ * 3 * row_d)])
                hits = [h for r in row_lines for h in [ray.intersection(r)] if not h.is_empty]
                cs = [c for h in hits for g in getattr(h, "geoms", [h]) for c in getattr(g, "coords", [])]
                if cs:
                    tgt, et = min(cs, key=lambda c: math.dist(c, (q.x, q.y))), "J"
                    touch(None, tgt)
                else:
                    h = nearest_points(allw, q)[0]
                    tgt, et = (h.x, h.y), "L"
                if e == 0:
                    p.insert(0, tgt)
                else:
                    p.append(tgt)
                ends.append(et)
            elif R.boundary.distance(q) < 1.0:
                ends.append(end_type(q))
            else:
                ends.append("J")
        if ends == ["J", "J"]:
            p = curved(p)
        # structural: a grid seam with no room for a tab is cut plain rather
        # than dropped (dropping merges cells into oversized pieces)
        out.append(dict(pts=p, kind="hullgrid", plain_ok=True, structural=True, ends=tuple(ends)))
    # --- the horizontal seams, split at every contact ------------------------
    for kind, pw, ends, contacts in horiz:
        ln = LineString(pw)
        cuts = sorted({round(ln.project(Point(q)), 3) for q in contacts
                       if 0.5 < ln.project(Point(q)) < ln.length - 0.5})
        stops = [0.0] + cuts + [ln.length]
        for k_ in range(len(stops) - 1):
            a, b = stops[k_], stops[k_ + 1]
            if b - a < 0.5:
                continue
            n = max(2, int((b - a) / 2.0))
            seg = [ln.interpolate(a + (b - a) * i / n) for i in range(n + 1)]
            seg = [(q.x, q.y) for q in seg]
            # contact ends sit exactly on the other seams' endpoints (J)
            if k_ > 0:
                seg[0] = min(contacts, key=lambda c: math.dist(c, seg[0]))
            if k_ < len(stops) - 2:
                seg[-1] = min(contacts, key=lambda c: math.dist(c, seg[-1]))
            e0 = ends[0] if k_ == 0 else "J"
            e1 = ends[1] if k_ == len(stops) - 2 else "J"
            out.append(dict(pts=seg, kind=kind, plain_ok=True, structural=True, ends=(e0, e1)))
    return out


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
TAB_SIZE_CLASSES = (1.0, 1.10, 1.20)  # smallest = the full min_feature_mm tab


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


# QA floors are the material's min_feature_mm (cfg.min_feature_mm): no neck
# narrower than t, no strip narrower than t that is longer than 2t.


def qa_defects(pieces, bulbs, ppm, t_mm=3.0):
    """Breakable features on background pieces: (kind, Point, piece_idx).
    'bridge': eroding by t_mm/2 splits off a chunk (a neck under t_mm) that
    is not a tab bulb. 'strip': material an opening of width t_mm removes,
    longer than 2 * t_mm, not part of a tab."""
    def parts(g):
        return [q for q in getattr(g, "geoms", [g]) if not q.is_empty and q.area > 0]
    tabs = unary_union([b.buffer(1.5 * ppm) for b in bulbs]) if bulbs else None
    out = []
    for i, g in enumerate(pieces):
        lobes = sorted(parts(g.buffer(-t_mm / 2 * ppm)), key=lambda q: -q.area)
        for q in lobes[1:]:
            if q.area < 2.0 * ppm ** 2:
                continue
            if tabs is not None and tabs.contains(q):
                continue
            out.append(("bridge", q.centroid, i))
        r = t_mm / 2 * ppm
        lost = g.difference(g.buffer(-r).buffer(r).buffer(0.05 * ppm))
        for q in parts(lost.buffer(-0.2 * ppm).buffer(0.2 * ppm)):
            if q.length / 2 / ppm <= 2 * t_mm or q.area < 2.0 * ppm ** 2:
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
        defects = qa_defects(surround, st["bulbs"], ppm, cfg.min_feature_mm)
        # Oversized pieces count when picking the best round: dropping seams
        # kills thin-strip defects by merging cells into giant pieces, and
        # without this the loop would prefer exactly that.
        over = [("oversized", f.representative_point(), i)
                for i, f in enumerate(surround) if oversized_oriented(f, cfg)]
        if best is None or len(defects) + len(over) < len(best[3]):
            best = (surround, counters, st, defects + over,
                    {k: (v if v == "drop" else set(v)) for k, v in bans.items()})
        if not defects:
            break
        changed = False
        for _kind, pt, _pi in defects:  # thin features only: oversized has no seam to ban
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
            # structural seams (hull-style grid) are always cut: dropping one
            # merges cells into an oversized piece, worse than a crowded seam
            if s.get("structural") or (
                    ((ea, eb) == ("J", "J") or s.get("plain_ok")) and not conflict(cand)):
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


_frame_exempt = False  # set by build_ring: only a frame_mm > 0 build has a legit annulus


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
    if _frame_exempt and hole_area > poly.area * 2:
        return False  # only a real frame may be a giant annulus
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
    """Ring tab settings on a square canvas: fat capsule tabs, letter and
    border clearances, all derived from rp.min_feature_mm (see RingParams)."""
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
        min_feature_mm=rp.min_feature_mm,
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
    frags = G.carve_letter_pockets(piece_polys, letter_union)
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
    for i, p in enumerate(pieces, 1):
        p["serial"] = i
    # QA again on the FINAL pieces (after pocket carving / counter fusing /
    # sliver absorption), so nothing breakable slips in after the search.
    nonletter = [p for p in pieces if p["kind"] != "letter"]
    defects = qa_defects([p["polygon"] for p in nonletter], st.get("bulbs", []),
                         cfg.px_per_mm, cfg.min_feature_mm)
    # A letter counter too thin to survive as a loose piece (the triangle in a
    # small A) isn't a puzzle piece anyone needs: leave it in place, held by a
    # breakaway bridge on each face (see add_counter_bridges), and stop
    # counting it as a defect.
    bridged = add_counter_bridges(
        {i for _k, _q, i in defects}, nonletter, letter_union_of(pieces), cfg)
    st["bridged_counters"] = len(bridged)
    st["defects_final"] = [(k, (q.x, q.y)) for k, q, i in defects if i not in bridged]
    for p in nonletter:
        if oversized_oriented(p["polygon"], cfg):
            c = p["polygon"].representative_point()
            st["defects_final"].append(("oversized", (c.x, c.y)))
    return pieces, cfg, L, st, panel, C


def letter_union_of(pieces):
    return unary_union([p["polygon"] for p in pieces if p["kind"] == "letter"])


def add_counter_bridges(idxs, nonletter, letters, cfg, max_faces=3):
    """For each nonletter[i] (i in idxs) that is a letter counter -- a piece
    lying inside a hole of a letter -- record breakaway bridge points
    (image px) at the midpoints of its longest straight faces in
    p["bridges"]. The emitter leaves a short uncut gap of the cut line there.
    Returns the set of indices bridged."""
    ppm = cfg.px_per_mm
    holes = [Polygon(r) for g in getattr(letters, "geoms", [letters]) for r in g.interiors]
    done = set()
    for i in idxs:
        poly = nonletter[i]["polygon"]
        if not any(h.buffer(0.2 * ppm).contains(poly) for h in holes):
            continue  # not a counter: a real defect, leave it reported
        ring = list(poly.simplify(0.3 * ppm).exterior.coords)
        faces = sorted(zip(ring, ring[1:]), key=lambda e: -math.dist(*e))
        pts = [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
               for a, b in faces[:max_faces] if math.dist(a, b) >= 2.0 * ppm]
        if pts:
            nonletter[i]["bridges"] = pts
            done.add(i)
    return done


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
    ap.add_argument("--center-cap-mm", type=float, default=None,
                    help="loose center text at exactly this cap height; the hub grows "
                         "(ring letters shrink) until it fits (flat style)")
    ap.add_argument("--center-min-gap-mm", type=float, default=None,
                    help="min wood between adjacent loose center letters (default 3)")
    ap.add_argument("--hub-ring-mm", type=float, default=None,
                    help="min radial depth of the ring band between the outer letters "
                         "and the center disc (default 4.4 x min_feature_mm)")
    ap.add_argument("--debug", action="store_true", help="overlay seam status")
    ap.add_argument(
        "--center-style", choices=("hull", "medallion", "flat", "ring"), default="hull",
        help="hull (default) = loose center letters inside one continuous puzzle grid, "
        "each word's outline treated like one big letter; flat / ring = loose letters "
        "in a free-turning pie-sliced hub disc; medallion = fused words + undivided hub",
    )
    ap.add_argument(
        "--outline-etch-mm", type=float, default=0.0,
        help="etch an outline this far inside AND outside every letter cut "
        "(0 = off). Shows which side is up, and reads before painting",
    )
    ap.add_argument("--gcode", default=None, help="also emit cut GCode to this path")
    ap.add_argument("--material", default="plywood_veneer_3ply_3mm",
                    help="material profile id; its min_feature_mm sets every wood-strength limit")
    ap.add_argument("--min-feature-mm", type=float, default=None,
                    help="override the material's min_feature_mm (thinnest wood it survives)")
    ap.add_argument("--feed", type=int, default=None)
    ap.add_argument("--passes", type=int, default=None)
    ap.add_argument("--etch-power", type=float, default=None, help="outline etch power %% (default: material etch profile)")
    ap.add_argument("--etch-feed", type=int, default=None, help="outline etch feed mm/min (default: material etch profile)")
    ap.add_argument(
        "--max-backtrack-ms", type=float, default=2000.0,
        help="re-trace already-cut line up to this many ms to avoid a restart+warmup",
    )
    ap.add_argument(
        "--min-segment-mm", type=float, default=0.3,
        help="drop G1 chords shorter than this (mm) so GRBL never stalls on micro-moves",
    )
    ap.add_argument("--bridge-mm", type=float, default=0.5,
                    help="uncut gap (laser centerline, mm) holding each too-thin letter "
                         "counter in place; ~0.3mm of wood after a ~0.2mm kerf")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    words = [w.upper() for w in a.word.split("+") if w.strip()]
    from emitter import load_material

    material = load_material(a.material)
    t_mm = a.min_feature_mm or material.get("min_feature_mm")
    if t_mm is None:
        raise SystemExit(f"material {a.material} has no min_feature_mm; pass --min-feature-mm")
    rp = RingParams(
        min_feature_mm=t_mm,
        diameter_mm=a.diameter_mm,
        shape=a.shape,
        rows=a.rows,
        variants=a.variants,
        ornament=None if a.ornament == "none" else a.ornament,
        font=a.font,
        distinct_tabs=not a.no_distinct_tabs,
        center_text=tuple(a.center_text.upper().split("+")) if a.center_text else None,
        center_style=a.center_style,
        center_word=_center_word_index(a.center_word, words),
        center_cap_target_mm=a.center_cap_mm,
        **({"hub_ring_min_mm": a.hub_ring_mm} if a.hub_ring_mm is not None else {}),
        **({"center_min_gap_mm": a.center_min_gap_mm} if a.center_min_gap_mm is not None else {}),
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
        if a.passes is not None:
            material = {**material, "laser": {**material["laser"], "passes": a.passes}}
        gcode = J._emit_cut_for(
            pieces, material, cfg, tag, "ring", mode="static",
            feed_override=a.feed, power_percent=100.0,
            min_segment_mm=a.min_segment_mm,
            max_backtrack_ms=a.max_backtrack_ms,
            bridge_mm=a.bridge_mm,
        )
        if strokes:
            from emitter import combine_passes, emit_etch_gcode

            etch = emit_etch_gcode(
                strokes, material, cfg, f"{tag} letter outlines",
                feed_override=a.etch_feed, power_percent=a.etch_power,
                min_segment_mm=a.min_segment_mm,
            )
            gcode = combine_passes(etch, gcode)  # etch first, while still in the stock
        _check_xy_envelope(gcode, a.max_xy_mm)
        Path(a.gcode).write_text(gcode)
        png, _svg = J.render_gcode_previews(
            gcode, cfg, Path(a.gcode).with_suffix(""), title=f"{tag} ring cut"
        )
        print(f"-> {a.gcode}  ({len(gcode.splitlines())} lines), toolpath {png}")
    print(
        f"{tag}: cap {L['cap_mm']:.1f}mm, min letter gap {L['min_gap_mm']:.1f}mm, "
        f"hub r {L['r_h'] / cfg.px_per_mm:.0f}mm, center cap {L.get('center_cap_mm')}mm, {len(pieces)} pieces, "
        f"{st.get('bridged_counters', 0)} bridged counters, "
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


def _check_xy_envelope(gcode, max_xy_mm):
    """Hard stop before writing: every X/Y word (G0 travel included) must lie
    in [0, max_xy_mm]. A 300mm job ran into the Y hard stop at ~297mm."""
    import re

    bad = []
    for n, line in enumerate(gcode.splitlines(), 1):
        code = line.split(";")[0]
        for ax, v in re.findall(r"([XY])(-?[\d.]+)", code):
            if not (0.0 <= float(v) <= max_xy_mm):
                bad.append(f"line {n}: {ax}{v}")
    if bad:
        raise SystemExit(
            f"REFUSING to write GCode: {len(bad)} coordinate(s) outside 0..{max_xy_mm}mm "
            f"(first: {bad[0]}). Use a smaller --diameter-mm."
        )


if __name__ == "__main__":
    main()
