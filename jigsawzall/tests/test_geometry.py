"""Tests for geometry.py.

Includes a regression test against the original phase scripts: the
parametric pipeline must produce equivalent pieces to phase8 (full
config) and equivalent piece count + tab stats to phase6_small (small
config). This locks down the "productionization didn't change behavior"
contract.
"""

import json
import math
import sys
from dataclasses import replace
from pathlib import Path

import pytest

LESSON_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LESSON_DIR))

from geometry import (  # noqa: E402
    PuzzleConfig,
    banner_puzzle_config,
    build_pieces_letter_aligned,
    build_pieces_with_shifted_tabs,
    carve_letter_pockets,
    fit_config,
    full_puzzle_config,
    generate_pieces,
    letter_auto_origins,
    merge_small_fragments,
    mini_puzzle_config,
    place_tab_at_offset,
    render_letter_polygons,
    small_puzzle_config,
    tab_outline,
)


# ---------------------------------------------------------------------------
# PuzzleConfig invariants
# ---------------------------------------------------------------------------


def test_full_puzzle_config_matches_scratch_constants():
    cfg = full_puzzle_config()
    assert cfg.panel_mm == 300
    assert cfg.piece_mm == 50
    assert cfg.cols == 6
    assert cfg.rows == 6
    assert cfg.cell_w_px == 250  # 50 * 5
    assert cfg.tab_circle_r_px == 22
    assert cfg.tab_height_px == 66  # 3 * 22
    assert cfg.tab_len_px == 110  # max(100, 5*22)


def test_small_puzzle_config_matches_phase6_small_overrides():
    cfg = small_puzzle_config()
    assert cfg.panel_mm == 80
    assert cfg.piece_mm == 40
    assert cfg.cols == 2
    assert cfg.rows == 2
    assert cfg.cell_w_px == 200
    assert cfg.tab_circle_r_px == 15
    assert cfg.tab_height_px == 45
    assert cfg.tab_len_px == 80  # max(80, 5*15)


def test_clearance_and_thickness_scale_with_tab_radius():
    cfg = full_puzzle_config()
    assert cfg.letter_clearance_px == 22
    assert cfg.fragment_min_thickness_px == 22
    assert cfg.fragment_min_area_px == pytest.approx(0.10 * 250 * 250)


def test_two_configs_coexist_in_one_process():
    """Critical: this was impossible in scratch/ because phase6_small
    mutated phase2's module globals. The parametric design makes it
    trivial."""
    small = small_puzzle_config()
    full = full_puzzle_config()
    assert small.cols != full.cols
    assert small.tab_circle_r_px != full.tab_circle_r_px
    # And both still have their original values after the other was created
    assert small_puzzle_config().cols == 2
    assert full_puzzle_config().cols == 6


# ---------------------------------------------------------------------------
# Tab outline geometry
# ---------------------------------------------------------------------------


def test_tab_outline_starts_and_ends_on_edge():
    cfg = full_puzzle_config()
    pts = tab_outline(direction=+1, cfg=cfg)
    assert pts[0] == (0.0, 0.0)
    assert pts[-1] == (1.0, 0.0)


def test_tab_outline_apex_at_full_depth():
    """The bulb's tip (mid-arc) is at v=1 in normalized coords."""
    cfg = full_puzzle_config()
    pts = tab_outline(direction=+1, cfg=cfg, n=24)
    max_v = max(v for _, v in pts)
    assert max_v == pytest.approx(1.0, abs=0.01)


def test_tab_outline_negative_direction_mirrors_v():
    cfg = full_puzzle_config()
    pos = tab_outline(direction=+1, cfg=cfg)
    neg = tab_outline(direction=-1, cfg=cfg)
    # Same u, mirrored v
    for (u_p, v_p), (u_n, v_n) in zip(pos, neg):
        assert u_p == pytest.approx(u_n)
        assert v_p == pytest.approx(-v_n)


def test_place_tab_centered_on_horizontal_edge():
    cfg = full_puzzle_config()
    # Edge from (100, 100) going +X, length 250
    edge_start = (100, 100)
    edge_dir = (1, 0)
    edge_length = 250
    offset_u = (edge_length - cfg.tab_len_px) / 2  # centered
    pts = place_tab_at_offset(edge_start, edge_dir, edge_length, +1, offset_u, cfg)
    # Apex (mid-arc) should be at x=midpoint, y=100 - tab_height (negative because
    # the out-vector for +X direction is (0, -1) by the cross-product convention)
    apex = max(pts, key=lambda p: abs(p[1] - 100))
    assert apex[0] == pytest.approx(225, abs=5)  # 100 + 250/2 ≈ 225
    assert abs(apex[1] - 100) == pytest.approx(cfg.tab_height_px, abs=2)


# ---------------------------------------------------------------------------
# build_pieces_with_shifted_tabs produces sensible output
# ---------------------------------------------------------------------------


def test_full_config_produces_36_cell_pieces_no_letter():
    cfg = full_puzzle_config()
    piece_polys, stats = build_pieces_with_shifted_tabs(
        seed=7, letter_union=None, cfg=cfg
    )
    assert len(piece_polys) == 36  # 6 x 6
    # No letter => all tabs centered, none shifted, none dropped
    assert stats["shifted"] == 0
    assert stats["dropped"] == 0
    assert stats["centered"] == stats["total"]


def test_small_config_produces_4_cell_pieces_no_letter():
    cfg = small_puzzle_config()
    piece_polys, stats = build_pieces_with_shifted_tabs(
        seed=7, letter_union=None, cfg=cfg
    )
    assert len(piece_polys) == 4  # 2 x 2


def test_letter_shifts_some_tabs_in_full_config():
    cfg = full_puzzle_config()
    letter_union, _, _, _ = render_letter_polygons("NORA", cfg)
    _piece_polys, stats = build_pieces_with_shifted_tabs(
        seed=7, letter_union=letter_union, cfg=cfg
    )
    # With NORA at seed 7, we expect a mix. Each interior edge is now
    # computed ONCE (shared by both adjacent cells), so total = 60 internal
    # edges (was double-counted as 120 before edge-sharing).
    assert stats["total"] == 60
    assert stats["shifted"] > 0
    assert stats["dropped"] > 0


# ---------------------------------------------------------------------------
# Full pipeline (generate_pieces)
# ---------------------------------------------------------------------------


def test_generate_pieces_full_nora_produces_43_pieces():
    cfg = full_puzzle_config()
    pieces, stats = generate_pieces("NORA", seed=7, cfg=cfg)
    # 43 not 44: the O's split center counter is fused into one disc.
    assert len(pieces) == 43
    cells = [p for p in pieces if p["kind"] == "cell"]
    letters = [p for p in pieces if p["kind"] == "letter"]
    assert len(cells) == 39
    assert len(letters) == 4  # N, O, R, A


def test_generate_pieces_small_n_produces_5_pieces():
    cfg = small_puzzle_config()
    pieces, stats = generate_pieces("N", seed=7, cfg=cfg)
    assert len(pieces) == 5  # 4 cells + 1 letter
    letters = [p for p in pieces if p["kind"] == "letter"]
    assert len(letters) == 1


def test_generate_pieces_serial_numbers_are_unique_and_contiguous():
    cfg = small_puzzle_config()
    pieces, _ = generate_pieces("N", seed=7, cfg=cfg)
    serials = [p["serial"] for p in pieces]
    assert serials == list(range(1, len(pieces) + 1))


# ---------------------------------------------------------------------------
# Regression: parametric geometry matches the original phase-script output
# ---------------------------------------------------------------------------


def test_regression_full_nora_matches_phase8():
    """generate_pieces(full_config, NORA, 7) piece count + tab stats.

    Tab stats are still pinned to phase8's known-good output. Piece count
    is 43 (not phase8's 44): the O's center counter straddles a grid line
    and is carved into two half-discs, which fuse_counter_fragments now
    fuses into a single disc piece so it seats cleanly in the O pocket."""
    cfg = full_puzzle_config()
    pieces, stats = generate_pieces("NORA", seed=7, cfg=cfg)
    assert len(pieces) == 43
    # Interior edges counted once (shared between cells), not double-counted.
    # seed 7's default tab directions already clear (or are truly impossible),
    # so flipped==0 here; the 6 drops are edges no direction/offset can clear.
    assert stats == {
        "total": 60,
        "centered": 44,
        "shifted": 10,
        "flipped": 0,
        "dropped": 6,
    }


def test_regression_small_n_matches_phase6_small():
    """Same regression for the small (80x80 / 40mm cells) config."""
    cfg = small_puzzle_config()
    pieces, stats = generate_pieces("N", seed=7, cfg=cfg)
    # Hard-coded from scratch phase6_small's known good output
    assert len(pieces) == 5
    assert stats == {
        "total": 4,
        "centered": 1,
        "shifted": 2,
        "flipped": 0,
        "dropped": 1,
    }


def test_tab_flip_recovers_some_dropped_tabs():
    """When the seeded tab direction can't clear a letter at any offset,
    the tab flips in/out and retries before being dropped (a dropped tab
    weakens the joint). seed 0 / NORA exercises this: several tabs are
    saved by flipping. Truly-blocked edges (no direction/offset clears)
    still drop, so `dropped` is seed-independent for a given word."""
    cfg = full_puzzle_config()
    _pieces, stats = generate_pieces("NORA", seed=0, cfg=cfg)
    assert stats["flipped"] > 0, f"expected some flipped tabs, got {stats}"
    # flip is a strict improvement: it only ever converts a would-be drop
    # into a placed tab, never the reverse.
    assert stats["dropped"] == 6


def test_regression_full_emits_polygons_in_expected_area():
    """All pieces lie within the panel + reasonable margin (tab bulges)."""
    cfg = full_puzzle_config()
    pieces, _ = generate_pieces("NORA", seed=7, cfg=cfg)
    # Panel is at (margin, margin) to (margin + 1500, margin + 1500)
    min_x = cfg.margin_px - cfg.tab_height_px
    max_x = cfg.margin_px + cfg.puzzle_w_px + cfg.tab_height_px
    min_y = cfg.margin_px - cfg.tab_height_px
    max_y = cfg.margin_px + cfg.puzzle_h_px + cfg.tab_height_px
    for piece in pieces:
        poly = piece["polygon"]
        bx_min, by_min, bx_max, by_max = poly.bounds
        assert min_x <= bx_min <= max_x
        assert min_x <= bx_max <= max_x
        assert min_y <= by_min <= max_y
        assert min_y <= by_max <= max_y


# ---------------------------------------------------------------------------
# Sliver-merge contract — surfaced during AYANA red-team
# ---------------------------------------------------------------------------

from geometry import (  # noqa: E402
    _adjacency_length,
    _too_thin_or_small,
    build_pieces_with_shifted_tabs,
    carve_letter_pockets,
    merge_small_fragments,
    render_letter_polygons,
)


def _surviving_slivers_with_eligible_neighbors(fragments, cfg, min_shared=5.0):
    """Return list of (idx, area, n_eligible) tuples for any sliver that
    survived merge_small_fragments while having mergeable neighbors. The
    contract is: surviving slivers MUST be truly isolated (e.g. letter
    counters). If this list is non-empty, the merge algorithm has a bug."""
    out = []
    for i, f in enumerate(fragments):
        if f.get("kind") != "cell":
            continue
        if not _too_thin_or_small(f["polygon"], cfg):
            continue
        n_eligible = sum(
            1
            for j, g in enumerate(fragments)
            if j != i
            and _adjacency_length(f["polygon"], g["polygon"]) >= min_shared
        )
        if n_eligible > 0:
            out.append((i, f["polygon"].area, n_eligible))
    return out


@pytest.mark.parametrize("word", ["NORA", "AYANA", "OAT", "LILY"])
def test_merge_leaves_only_isolated_slivers(word):
    """After merge_small_fragments, every surviving sliver must have
    ZERO eligible neighbors. The only reason a sliver should survive is
    geometric isolation (letter counters like O's hole, A's triangle).

    Word list deliberately includes:
    - NORA: original canonical case with one O counter
    - AYANA: red-team case with three A counters (surfaced this test)
    - OAT: O + A counters in one word
    - LILY: no counters in any letter
    """
    cfg = full_puzzle_config()
    letter_union, _, _, _ = render_letter_polygons(word, cfg)
    piece_polys, _ = build_pieces_with_shifted_tabs(7, letter_union, cfg)
    fragments = merge_small_fragments(
        carve_letter_pockets(piece_polys, letter_union), cfg
    )
    leaked = _surviving_slivers_with_eligible_neighbors(fragments, cfg)
    assert leaked == [], (
        f"{word}: {len(leaked)} slivers survived merge despite having "
        f"eligible neighbors. (idx, area, n_eligible) = {leaked}"
    )


def test_ayana_keeps_three_a_counters_as_isolated_pieces():
    """Regression for the AYANA red-team finding: the three A's each have
    a triangular counter that survives as its own piece (drops into the A
    pocket on assembly). Lock that they exist + are roughly the same size."""
    cfg = full_puzzle_config()
    pieces, _ = generate_pieces("AYANA", 7, cfg)
    counters = [
        p for p in pieces
        if p["kind"] == "cell" and _too_thin_or_small(p["polygon"], cfg)
    ]
    assert len(counters) == 3, f"expected 3 A counters, got {len(counters)}"
    areas = sorted(c["polygon"].area for c in counters)
    # All three should be within 10% of each other (Arial Bold A counters
    # are the same shape regardless of position)
    assert areas[-1] - areas[0] < areas[0] * 0.10, (
        f"A counters vary in size more than 10%: {areas}"
    )


@pytest.mark.parametrize(
    "cfg_fn,word,seed",
    [
        (mini_puzzle_config, "NORA", 7),
        (mini_puzzle_config, "NORA", 42),
        (banner_puzzle_config, "NORA", 42),
        (full_puzzle_config, "NORA", 7),
    ],
)
def test_no_notch_gaps(cfg_fn, word, seed):
    """Every point inside the panel must belong to a piece (cell or
    letter). Regression for the letter-notch triangle tips that carve used
    to DROP (<=100px), leaving uncut gaps / orphan bits inside letters like
    the N's diagonal crook. absorb_letter_slivers folds them into the
    adjacent letter so the panel tiles completely."""
    from shapely.geometry import box
    from shapely.ops import unary_union
    from geometry import _rounded_panel_mask

    cfg = cfg_fn()
    cfg = fit_config(word, cfg)
    pieces, _ = generate_pieces(word, seed, cfg)
    m = cfg.margin_px
    # When corner_radius_mm > 0 the panel corners are intentionally rounded
    # off, so check coverage against the rounded mask, not the square box.
    panel = _rounded_panel_mask(cfg) or box(
        m, m, m + cfg.puzzle_w_px, m + cfg.puzzle_h_px
    )
    covered = unary_union([p["polygon"] for p in pieces])
    gap = panel.difference(covered)
    gap_mm2 = gap.area / (cfg.px_per_mm**2)
    assert gap_mm2 < 0.5, (
        f"{word}/{seed}: {gap_mm2:.2f}mm^2 of uncovered panel (notch gaps)"
    )


# ---------------------------------------------------------------------------
# Wavy edges (Option A organic-grid implementation)
# ---------------------------------------------------------------------------


def test_wave_amplitude_default_is_zero():
    """Default config produces straight grid edges — locks
    backwards-compatibility with the regression tests above."""
    assert full_puzzle_config().wave_amplitude_px == 0
    assert small_puzzle_config().wave_amplitude_px == 0


def test_wavy_mode_produces_more_vertices_than_straight():
    """Same word + seed in wavy mode yields significantly more cell-edge
    vertices than straight mode (the half-sine subdivision adds points
    along every flat segment of every internal edge)."""
    straight = full_puzzle_config()
    wavy = PuzzleConfig(
        panel_mm=300, piece_mm=50, tab_circle_r_px=22, wave_amplitude_px=12
    )
    p_s, _ = generate_pieces("NORA", 7, straight)
    p_w, _ = generate_pieces("NORA", 7, wavy)
    cell_verts_s = sum(
        len(p["polygon"].exterior.coords) for p in p_s if p["kind"] == "cell"
    )
    cell_verts_w = sum(
        len(p["polygon"].exterior.coords) for p in p_w if p["kind"] == "cell"
    )
    assert cell_verts_w > cell_verts_s * 1.15, (
        f"expected wavy to add ≥15% more vertices; got "
        f"straight={cell_verts_s} wavy={cell_verts_w}"
    )


def test_wavy_mode_preserves_sliver_merge_contract():
    """Wavy edges don't break the merge algorithm — surviving slivers
    must still all be geometrically isolated."""
    cfg = PuzzleConfig(
        panel_mm=300, piece_mm=50, tab_circle_r_px=22, wave_amplitude_px=12
    )
    letter_union, _, _, _ = render_letter_polygons("NORA", cfg)
    piece_polys, _ = build_pieces_with_shifted_tabs(7, letter_union, cfg)
    fragments = merge_small_fragments(
        carve_letter_pockets(piece_polys, letter_union), cfg
    )
    leaked = _surviving_slivers_with_eligible_neighbors(fragments, cfg)
    assert leaked == [], f"wavy mode leaked mergeable slivers: {leaked}"


# ---------------------------------------------------------------------------
# Letter-aligned grid (Phase 2) — auto origins + node-lattice builder
# ---------------------------------------------------------------------------

from glyph_origins import auto_glyph_origin  # noqa: E402


def _glyph_ink(ch, size=140):
    """Rasterize one bold glyph to a tight ink-bbox bool array (for seam tests)."""
    import numpy as np
    from geometry import find_font
    from PIL import Image, ImageDraw

    font = find_font(size)
    gl, gt, gr, gb = font.getbbox(ch)
    im = Image.new("L", (max(int(gr - gl), 1), max(int(gb - gt), 1)), 0)
    ImageDraw.Draw(im).text((-gl, -gt), ch, fill=255, font=font)
    return np.asarray(im) > 80


def test_glyph_seam_x_center_vs_stroke():
    """The general seam rule: glyphs with ink at their center (crossbar,
    mid-arm, diagonal, central stem) or a symmetric closed ring seam through
    the CENTER; open glyphs whose center is empty/hollow (L's blank upper-right,
    C's open counter) seam through their dominant vertical stroke instead — so a
    tab never sprouts in whitespace and no fragile crumb shears off."""
    from glyph_origins import glyph_seam_x

    for ch in "HEANSTXIZOUF":  # center-supported (ink or symmetric ring)
        sx = glyph_seam_x(_glyph_ink(ch))
        assert 0.42 <= sx <= 0.58, f"{ch}: expected center seam, got {sx:.2f}"
    for ch in "LC":  # open at center -> seam moves off-center to the stroke
        sx = glyph_seam_x(_glyph_ink(ch))
        assert sx < 0.42, f"{ch}: expected stroke seam left of center, got {sx:.2f}"


def test_glyph_hcut_y_feature_anchored():
    """The horizontal split anchors to a glyph feature so the row boundary
    undulates: A's crossbar sits low, so its cut is below mid; an open C has no
    central bar, so it falls back to the mouth-center (~mid) for two robust arcs
    rather than skimming a terminal."""
    from glyph_origins import glyph_hcut_y

    a = glyph_hcut_y(_glyph_ink("A"))
    assert a > 0.52, f"A: expected low cut through the crossbar, got {a:.2f}"
    c = glyph_hcut_y(_glyph_ink("C"))
    assert 0.4 <= c <= 0.6, f"C: expected mouth-center cut, got {c:.2f}"


def test_banner_config_is_letter_aligned():
    cfg = banner_puzzle_config()
    assert cfg.letter_aligned_grid is True
    assert cfg.wave_amplitude_px == 0  # straight cuts in aligned mode


def test_auto_origin_returns_normalized_coords():
    import numpy as np

    # a tall vertical bar on the left third -> stem => x on the bar, y mid
    ink = np.zeros((100, 100), dtype=bool)
    ink[5:95, 20:30] = True
    nx, ny = auto_glyph_origin(ink)
    assert 0.0 <= nx <= 1.0 and 0.0 <= ny <= 1.0
    assert nx < 0.5  # sits on the left bar, not center
    assert 0.4 <= ny <= 0.6  # mid-height of a full stem


def test_auto_origin_ring_sits_on_a_stroke():
    import numpy as np

    # a hollow ring (O-like): the auto rule anchors on the left arc (a real
    # vertical stroke -> a clean cut edge), at mid height. Not forced to center.
    ink = np.zeros((120, 120), dtype=bool)
    yy, xx = np.ogrid[:120, :120]
    d = (yy - 60) ** 2 + (xx - 60) ** 2
    ink[(d <= 55**2) & (d >= 38**2)] = True
    nx, ny = auto_glyph_origin(ink)
    assert nx < 0.5  # sits on the left arc, not the empty counter
    assert 0.4 <= ny <= 0.6  # mid-height


def test_letter_auto_origins_one_per_glyph_in_bounds():
    cfg = banner_puzzle_config()
    origins = letter_auto_origins("NORA", cfg)
    assert [c for c, _ in origins] == list("NORA")
    for _c, (ox, oy) in origins:
        assert cfg.margin_px <= ox <= cfg.margin_px + cfg.puzzle_w_px
        assert cfg.margin_px <= oy <= cfg.margin_px + cfg.puzzle_h_px
    # origins run left-to-right
    xs = [ox for _c, (ox, _oy) in origins]
    assert xs == sorted(xs)


def test_letter_aligned_grid_splits_at_origins():
    """Each glyph origin should lie on a boundary between two horizontally
    adjacent cells (a vertical grid line passes through it), i.e. the origin
    is not deep in the interior of a single cell."""
    from shapely.geometry import Point

    cfg = banner_puzzle_config()
    origins = letter_auto_origins("NORA", cfg)
    lu, _x, _y, _f = render_letter_polygons("NORA", cfg)
    pieces, _stats = build_pieces_letter_aligned(7, lu, cfg, origins)
    cells = list(pieces.values())
    for _c, (ox, oy) in origins:
        # a vertical line at ox borders cells on both sides: some cell's edge
        # is within a few px of ox in x.
        near_edge = min(
            min(abs(ox - poly.bounds[0]), abs(ox - poly.bounds[2])) for poly in cells
        )
        assert near_edge <= cfg.tab_height_px + 3, (
            f"origin x={ox:.0f} not on a grid line (nearest cell edge {near_edge:.0f}px)"
        )


def test_letter_aligned_banner_nora_piece_count():
    """Regression lock for the aligned banner NORA layout."""
    cfg = banner_puzzle_config()
    pieces, _stats = generate_pieces("NORA", 7, cfg)
    assert len(pieces) == 17
    letters = [p for p in pieces if p["kind"] == "letter"]
    assert len(letters) == 4


@pytest.mark.parametrize("word", ["NORA", "KARSON", "KAI", "LEO", "AYANA"])
def test_letter_aligned_tiles_panel(word):
    """The aligned grid must cover the whole panel (no uncut gaps)."""
    from shapely.geometry import box
    from shapely.ops import unary_union
    from geometry import _rounded_panel_mask

    cfg = banner_puzzle_config()
    cfg = fit_config(word, cfg)
    pieces, _ = generate_pieces(word, 7, cfg)
    m = cfg.margin_px
    panel = _rounded_panel_mask(cfg) or box(
        m, m, m + cfg.puzzle_w_px, m + cfg.puzzle_h_px
    )
    covered = unary_union([p["polygon"] for p in pieces])
    gap_mm2 = panel.difference(covered).area / (cfg.px_per_mm**2)
    assert gap_mm2 < 0.5, f"{word}: {gap_mm2:.2f}mm^2 uncovered"


def test_letter_aligned_leftmost_column_interlocks():
    """The two pieces of the leftmost column (top/bottom, against the panel
    edge) must share a tabbed boundary — regression for the pieces 1/2
    no-tab bug on the narrow margin strip. A tab makes the shared boundary
    much longer than a straight cut across the column width."""
    cfg = banner_puzzle_config()
    pieces, _stats = generate_pieces("NORA", 7, cfg)
    m = cfg.margin_px
    left = [
        p["polygon"]
        for p in pieces
        if p["kind"] == "cell" and abs(p["polygon"].bounds[0] - m) <= 2
    ]
    assert len(left) >= 2, "expected top+bottom pieces against the left edge"
    left.sort(key=lambda g: g.bounds[1])
    a, b = left[0], left[1]
    col_w = a.bounds[2] - a.bounds[0]
    shared = a.buffer(0.5).intersection(b).length
    assert shared > 1.5 * col_w, (
        f"leftmost pieces share only {shared:.0f}px (col {col_w:.0f}px) — no tab"
    )


@pytest.mark.parametrize(
    "word", ["KARSON", "LEO", "NORA", "AYANA", "KADE", "REBECCA", "CHELSEA"]
)
def test_letter_aligned_holds_together(word):
    """The banner must hold together for any name length: with center-seams the
    inter-letter gap sits mid-column, so the top/bottom horizontal tab has room
    (fixes REBECCA/KARSON losing all top-bottom tabs). Allow at most one dropped
    tab (a first/last half-letter jammed against a thin panel margin can lose
    its horizontal tab, but that piece still interlocks via its vertical
    neighbour). Also every interior column's two pieces must interlock.

    This exercises the SEAM/GRID logic, so it runs with the thin lollipop tab
    (tab_stem_w_px=None, tab_bulb_elong_px=0). The banner default is now the fat
    capsule tab, which needs a real-size panel and does not fit these long names
    on the tiny fit-to-text default (see test_fat_capsule_tab_is_banner_default
    for the capsule's own hold-together check)."""
    cfg = replace(banner_puzzle_config(), tab_stem_w_px=None, tab_bulb_elong_px=0.0)
    pieces, stats = generate_pieces(word, 7, cfg)
    assert stats["dropped"] <= 1, (
        f"{word}: {stats['dropped']} dropped tabs — won't hold"
    )
    # at least one clean top/bottom interlock exists (a real bulge, not flat)
    R = cfg.tab_circle_r_px
    cells = [p["polygon"] for p in pieces if p["kind"] == "cell"]
    bulges = 0
    for a in cells:
        for b in cells:
            if a is b:
                continue
            sh = a.buffer(0.5).intersection(b)
            if (
                not sh.is_empty
                and (sh.bounds[3] - sh.bounds[1]) > R
                and (sh.bounds[2] - sh.bounds[0]) < (sh.bounds[3] - sh.bounds[1])
            ):
                bulges += 1
    assert bulges >= 2, f"{word}: too few top/bottom interlocks ({bulges})"


def test_fat_capsule_tab_is_banner_default():
    """The banner preset defaults to the fat capsule tab: a wide (~5mm) neck and
    a stadium bulb wider than the neck (so it still locks). Verifies the neck
    width, the capsule bulb width, and that the bulb overhangs the neck."""
    cfg = banner_puzzle_config()
    assert cfg.tab_stem_w_px == 25  # 5mm neck at 5px/mm
    assert cfg.tab_bulb_elong_px == 25
    L, H = cfg.tab_len_px, cfg.tab_height_px
    pts = tab_outline(direction=+1, cfg=cfg)
    # neck walls are the two interior points sitting on the edge (v == 0)
    on_edge = [u for u, v in pts if abs(v) < 1e-9 and 0.0 < u < 1.0]
    neck_px = (max(on_edge) - min(on_edge)) * L
    assert neck_px == pytest.approx(cfg.tab_stem_w_px, abs=1)  # 25px == 5mm
    # bulb width = elong + 2R, measured across the raised (v > 0) points
    bulb_us = [u for u, v in pts if v > 1e-6]
    bulb_px = (max(bulb_us) - min(bulb_us)) * L
    assert bulb_px == pytest.approx(
        cfg.tab_bulb_elong_px + 2 * cfg.tab_circle_r_px, abs=2
    )
    assert bulb_px > neck_px + cfg.tab_circle_r_px  # real undercut / lock
    # apex still reaches full tab depth
    assert max(v for _u, v in pts) == pytest.approx(1.0, abs=0.01)


def test_fat_capsule_banner_holds_at_real_size():
    """At a real name-plate size the fat-capsule banner holds together with no
    dropped tabs for a typical (<=6 letter) name — the size KAIDEN is cut at."""
    cfg = replace(banner_puzzle_config(), panel_mm=290, panel_h_mm=170, piece_mm=29)
    cfg = fit_config("KAIDEN", cfg)
    _pieces, stats = generate_pieces("KAIDEN", 7, cfg)
    assert stats["dropped"] == 0, f"KAIDEN dropped {stats['dropped']} fat tabs"
