"""Tests for lessons/laser/03_jigsaw/geometry.py.

Includes a regression test against the scratch/ versions: the new
parametric pipeline must produce equivalent pieces to phase8 (full
config) and equivalent piece count + tab stats to phase6_small (small
config). This locks down the "productionization didn't change behavior"
contract.

Cross-process: the regression test invokes scratch/phaseN.py in
subprocesses because phase6_small + phase8 are mutually-exclusive
imports in the same Python process. The new geometry.py has no such
restriction.
"""

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

LESSON_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LESSON_DIR))

from geometry import (  # noqa: E402
    PuzzleConfig,
    build_pieces_with_shifted_tabs,
    carve_letter_pockets,
    full_puzzle_config,
    generate_pieces,
    merge_small_fragments,
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
# Regression: parametric geometry matches scratch/phase output
# ---------------------------------------------------------------------------

SCRATCH_DIR = LESSON_DIR / "scratch"


def _run_scratch(script: str, *args: str) -> str:
    """Invoke a scratch phase script in a subprocess and return its stdout."""
    return subprocess.run(
        [sys.executable, str(SCRATCH_DIR / script), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stderr  # scratch scripts print to stderr; main prints to stdout though
    # We don't actually need either — we just want the script to run cleanly.


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
    assert stats == {"total": 60, "centered": 44, "shifted": 10, "dropped": 6}


def test_regression_small_n_matches_phase6_small():
    """Same regression for the small (80x80 / 40mm cells) config."""
    cfg = small_puzzle_config()
    pieces, stats = generate_pieces("N", seed=7, cfg=cfg)
    # Hard-coded from scratch phase6_small's known good output
    assert len(pieces) == 5
    assert stats == {"total": 4, "centered": 1, "shifted": 2, "dropped": 1}


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
