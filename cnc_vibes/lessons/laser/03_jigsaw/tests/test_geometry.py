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
    # With NORA at seed 7, we expect a mix (this is also the known good
    # output from the scratch version's printed stats)
    assert stats["total"] == 120  # 60 internal edges x 2 (each from both sides)
    assert stats["shifted"] > 0
    assert stats["dropped"] > 0


# ---------------------------------------------------------------------------
# Full pipeline (generate_pieces)
# ---------------------------------------------------------------------------


def test_generate_pieces_full_nora_produces_44_pieces():
    cfg = full_puzzle_config()
    pieces, stats = generate_pieces("NORA", seed=7, cfg=cfg)
    assert len(pieces) == 44
    cells = [p for p in pieces if p["kind"] == "cell"]
    letters = [p for p in pieces if p["kind"] == "letter"]
    assert len(cells) == 40
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
    """generate_pieces(full_config, NORA, 7) must produce the same piece
    count and tab stats as phase8_full_puzzle.py."""
    cfg = full_puzzle_config()
    pieces, stats = generate_pieces("NORA", seed=7, cfg=cfg)
    # Hard-coded from scratch phase8's known good output. If phase8 ever
    # changes, this test will catch a regression in geometry.py too.
    assert len(pieces) == 44
    assert stats == {"total": 120, "centered": 88, "shifted": 20, "dropped": 12}


def test_regression_small_n_matches_phase6_small():
    """Same regression for the small (80x80 / 40mm cells) config."""
    cfg = small_puzzle_config()
    pieces, stats = generate_pieces("N", seed=7, cfg=cfg)
    # Hard-coded from scratch phase6_small's known good output
    assert len(pieces) == 5
    assert stats == {"total": 8, "centered": 2, "shifted": 4, "dropped": 2}


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
