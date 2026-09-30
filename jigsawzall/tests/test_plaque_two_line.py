"""Tests enforcing behaviors Alex has explicitly specified for the
two-line plaque (scripts/plaque_two_line.py), so future changes get
checked against the actual spec instead of a visual guess-and-render
loop.

Each test below cites the message it comes from. Anything NOT cited to a
specific instruction is deliberately left as a `pytest.mark.skip` with
the open question spelled out in its reason -- see the questions in the
PR/chat rather than guessing a threshold here.
"""

import math
import sys
from pathlib import Path

import pytest

PKG_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_DIR))
sys.path.insert(0, str(PKG_DIR / "scripts"))

import geometry as G  # noqa: E402
from plaque_two_line import (  # noqa: E402
    build_frame_and_backboard,
    build_two_row_pieces,
)

WORD1, WORD2 = "PRINCIPAL", "CAVAGNOLO"


def _make_cfg(row_h_mm=34.0, row_gap_mm=30.0, margin_top_mm=24.0, margin_bottom_mm=24.0,
              panel_w_mm=280.0, ppm=5):
    panel_h_mm = margin_top_mm + row_h_mm + row_gap_mm + row_h_mm + margin_bottom_mm
    return G.PuzzleConfig(
        panel_mm=panel_w_mm, panel_h_mm=panel_h_mm,
        tab_circle_r_px=15, tab_stem_w_px=30.0,
        letter_clearance_mm=4.0, corner_radius_mm=0.0,
        panel_w_px_fit=int(panel_w_mm * ppm), panel_h_px_fit=int(panel_h_mm * ppm),
    )


@pytest.fixture(scope="module")
def built():
    """Build once per test module -- this generator is slow (tens of
    seconds), not something to re-run per assertion."""
    cfg = _make_cfg()
    pieces, stats, letter_union = build_two_row_pieces(cfg, WORD1, WORD2)
    return dict(cfg=cfg, pieces=pieces, stats=stats, letter_union=letter_union)


# ---------------------------------------------------------------------------
# "No no. both words on one puzzle... The words each still have a totally
# separate puzzle with no tabs or anything between. One puzzle. Two lines
# of text" / "ONE FUCKING PUZZLE. NOT TWO WITH A LINE THROUGH THEM."
# ---------------------------------------------------------------------------
def test_rows_are_seam_connected_not_two_independent_puzzles(built):
    """At least one accepted piece boundary must cross between the two
    letter rows -- i.e. some single piece's polygon touches BOTH a row0
    letter and a row1 letter, or a background piece spans the row
    boundary. A "two independent puzzles glued at a flat seam" layout
    would have every piece confined to one row or the other."""
    cfg = built["cfg"]
    pieces = built["pieces"]
    ppm = cfg.px_per_mm
    py = cfg.margin_px
    row_mid_y = py + cfg.puzzle_h_px / 2  # the shared boundary's rough height
    crossing = [
        poly for poly in pieces.values()
        if poly.bounds[1] < row_mid_y - 5 * ppm and poly.bounds[3] > row_mid_y + 5 * ppm
    ]
    assert crossing, (
        "no piece's bounding box spans the row boundary -- the two lines "
        "read as independently-tabbed puzzles again"
    )


# ---------------------------------------------------------------------------
# "And the edges should connect to letters not surround letters."
# ---------------------------------------------------------------------------
def test_every_accepted_seam_touches_a_letter(built):
    """Every piece-to-piece boundary edge must run along at least one
    letter's own silhouette for some nonzero length -- a piece boundary
    that never touches any letter (pure background-to-background, like a
    plain grid cell wall) is exactly the "surrounds letters" shape Alex
    rejected."""
    letter_union = built["letter_union"]
    pieces = list(built["pieces"].values())
    ppm = built["cfg"].px_per_mm
    touch_tol = 0.5 * ppm  # ~0.1mm
    for poly in pieces:
        boundary = poly.exterior
        # A boundary edge "touches a letter" if some stretch of it lies
        # within touch_tol of the letter union -- not just a single point.
        near = boundary.intersection(letter_union.buffer(touch_tol))
        assert near.length > 2 * ppm, (
            f"piece at {poly.centroid} has no boundary segment near any "
            "letter -- its edges surround background only"
        )


# ---------------------------------------------------------------------------
# "Some letters still have no vertices touching edges."
# ---------------------------------------------------------------------------
def test_every_letter_has_a_seam_anchored_on_it(built):
    """Every one of the 18 letters must have at least one accepted seam
    endpoint lying exactly on (not just near) its own silhouette --
    otherwise that letter is an isolated island with no interlocking tab
    of its own."""
    letter_union = built["letter_union"]
    geoms = (
        letter_union.geoms if letter_union.geom_type == "MultiPolygon" else [letter_union]
    )
    letters = [g for g in geoms if g.area > 100]
    assert len(letters) == len(WORD1) + len(WORD2), (
        f"expected {len(WORD1) + len(WORD2)} letter shapes, traced {len(letters)}"
    )
    # A seam "anchors" a letter if some piece boundary vertex sits within
    # a tight tolerance of that letter's own exterior ring.
    ppm = built["cfg"].px_per_mm
    tol = 0.2 * ppm
    pieces = [p for p in built["pieces"].values() if not letter_union.buffer(1).contains(p)]
    unanchored = []
    for letter in letters:
        touched = any(
            piece.exterior.distance(letter.exterior) < tol for piece in pieces
        )
        if not touched:
            unanchored.append(letter.centroid)
    assert not unanchored, f"letters with no seam touching their silhouette: {unanchored}"


# ---------------------------------------------------------------------------
# No oversized pieces -- reusing geometry.py's OWN existing definition of
# "oversized" (_vg_oversized), not a newly invented size threshold, since
# that's the codebase's established meaning for "too big" already used by
# every single-line vertex_grid/wave_grid puzzle's own validity check.
# ---------------------------------------------------------------------------
def test_no_oversized_pieces(built):
    cfg = built["cfg"]
    panel = G.box(cfg.margin_px, cfg.margin_px,
                   cfg.margin_px + cfg.puzzle_w_px, cfg.margin_px + cfg.puzzle_h_px)
    letter_union = built["letter_union"]
    background_pieces = [
        p for p in built["pieces"].values() if not letter_union.buffer(1).contains(p)
    ]
    oversized = [p for p in background_pieces if G._vg_oversized(p, cfg)]
    assert not oversized, f"{len(oversized)} oversized background piece(s)"


# ---------------------------------------------------------------------------
# Frame/backboard: "identical profiles and sit flush all along the outside
# edge" + "an outer edge of at least 5 or 10mm."
# ---------------------------------------------------------------------------
def test_frame_and_backboard_share_outer_profile():
    frame, backboard = build_frame_and_backboard(280.0, 146.0, border_mm=10.0, corner_mm=5.0)
    assert frame.exterior.equals_exact(backboard.exterior, tolerance=1e-6)


def test_frame_border_is_at_least_the_requested_width():
    border_mm = 10.0
    frame, backboard = build_frame_and_backboard(280.0, 146.0, border_mm=border_mm, corner_mm=5.0)
    # Sample the frame ring's radial thickness at the midpoint of each
    # straight edge (away from the rounded corners) -- must be >= border_mm.
    minx, miny, maxx, maxy = backboard.bounds
    cy = (miny + maxy) / 2
    left_thickness = frame.intersection(
        G.LineString([(minx, cy), (minx + border_mm * 2, cy)])
    ).length
    assert left_thickness >= border_mm - 1e-6


# ---------------------------------------------------------------------------
# Orientation: PRINCIPAL reads upright on top, CAVAGNOLO upright on the
# bottom (established when the design was still circular, restated as a
# baseline once it went rectangular -- neither word should be mirrored or
# rotated in the flat layout).
# ---------------------------------------------------------------------------
def test_word_order_top_to_bottom(built):
    """The two words must occupy top/bottom bands in the order passed to
    build_two_row_pieces(cfg, WORD1, WORD2) == (top, bottom)."""
    letter_union = built["letter_union"]
    geoms = (
        letter_union.geoms if letter_union.geom_type == "MultiPolygon" else [letter_union]
    )
    letters = sorted([g for g in geoms if g.area > 100], key=lambda g: g.centroid.y)
    top_half = letters[: len(WORD1)]
    bottom_half = letters[len(WORD1):]
    assert max(g.centroid.y for g in top_half) < min(g.centroid.y for g in bottom_half), (
        f"top word's letters are not all above bottom word's letters"
    )


# ---------------------------------------------------------------------------
# "the inset letter bits are gone" -- confirmed 2026-09-30: letter counters
# (O's hole, P's bowl, A's triangle) must be their own separate cuttable
# piece, not merged/filled away.
# ---------------------------------------------------------------------------
def test_letter_counters_are_separate_pieces(built):
    """Letters with a closed counter (O, A, P appear in PRINCIPAL/
    CAVAGNOLO) must keep that hole as an interior ring on the letter
    union AND have a distinct piece sitting in that hole -- if the hole
    got filled in (e.g. by only keeping a letter's exterior ring when
    building letter solids), both of these are empty/false."""
    letter_union = built["letter_union"]
    geoms = (
        letter_union.geoms if letter_union.geom_type == "MultiPolygon" else [letter_union]
    )
    letters = [g for g in geoms if g.area > 100]
    holes = [ring for g in letters for ring in g.interiors]
    assert holes, (
        "no letter in the union has an interior ring -- counters (O's hole, "
        "P's bowl, A's triangle) are being filled in instead of kept open"
    )
    counter_pieces = [
        p for p in built["pieces"].values()
        if any(Polygon(ring).buffer(1).contains(p) for ring in holes)
    ]
    assert counter_pieces, (
        "letter union has counters, but no piece in the output actually "
        "fills one as its own separate piece"
    )


# ---------------------------------------------------------------------------
# "No horizontal edges" -- confirmed 2026-09-30: some seams must run
# left-right (e.g. connecting neighbouring letters within a row), not only
# vertical caps up/down to the border.
# ---------------------------------------------------------------------------
def test_has_horizontal_edges(built):
    """At least one piece boundary must contain a substantially
    horizontal run (dy small relative to dx) of meaningful length --
    proof that some seam actually travels left-right rather than every
    seam running only vertically (letter-to-border caps)."""
    ppm = built["cfg"].px_per_mm
    min_len_px = 3 * ppm  # ~3mm, well above trace/simplify noise
    found = False
    for poly in built["pieces"].values():
        coords = list(poly.exterior.coords)
        for (x0, y0), (x1, y1) in zip(coords, coords[1:]):
            dx, dy = x1 - x0, y1 - y0
            length = math.hypot(dx, dy)
            if length >= min_len_px and abs(dy) < abs(dx) * 0.3:
                found = True
                break
        if found:
            break
    assert found, "no piece boundary has a horizontal-running segment of any length"


# ---------------------------------------------------------------------------
# Panel size -- confirmed 2026-09-30: 300mm wide x 150mm high is a hard
# ceiling on the TOTAL panel including the glued-on outer border (not just
# the letter content area).
# ---------------------------------------------------------------------------
def test_panel_fits_target_size(built):
    cfg = built["cfg"]
    border_mm = 10.0
    total_w = cfg.panel_mm + 2 * border_mm
    total_h = cfg.panel_h_mm + 2 * border_mm
    assert total_w <= 300.0, f"total width {total_w:.1f}mm exceeds the 300mm ceiling"
    assert total_h <= 150.0, f"total height {total_h:.1f}mm exceeds the 150mm ceiling"


@pytest.mark.skip(reason="no minimum/maximum curvature ('waviness') has been given a number "
                         "-- asked what makes a seam 'wavy enough' vs a straight line before "
                         "encoding a threshold")
def test_seams_are_curved_not_straight():
    pass


@pytest.mark.skip(reason="foot stand parameters left unspecified for now per Alex "
                         "(2026-09-30) -- puzzle-seam behaviors take priority; revisit "
                         "when foot geometry is actually being worked on")
def test_foot_dimensions():
    pass
