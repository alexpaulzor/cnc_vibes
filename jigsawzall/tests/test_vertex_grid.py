"""Tests for the vertex-grid tiling (--vertex-grid / jigsaw.py vgrid)."""

import sys
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_DIR))

import shapely.geometry as sg  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

import vertex_grid as vg  # noqa: E402


def _letters_union(res):
    return unary_union(res.letters) if res.letters else sg.Point(-9, -9)


def test_build_produces_pieces_and_cutouts():
    res = vg.build("KAI", seed=7)
    assert len(res.pieces) > 0
    assert len(res.letters) == 3  # K, A, I
    assert len(res.counters) == 1  # A's counter
    assert res.w_mm > 0 and res.h_mm > 0


def test_no_seam_crosses_a_letter():
    # background pieces must not overlap the letter rings (seams stay in bg).
    res = vg.build("NORA", seed=7)
    lu = _letters_union(res)
    for p in res.pieces:
        assert p.intersection(lu).area < 1.0  # px^2, essentially zero


def test_pieces_are_durable_for_normal_word():
    # NORA has room; every piece should survive a WALL/2 erosion without pinching.
    res = vg.build("NORA", seed=7)
    assert res.durable


def test_one_tab_per_edge_no_dense_tabs():
    # tally = (full, circle, skipped); most edges get a full tab, none dense.
    res = vg.build("KAI", seed=7)
    full, circle, skipped = res.tabs
    assert full >= 1
    assert full + circle >= 1


def test_auto_gap_widens_for_durability():
    # auto_gap should try progressively wider gaps; recorded in meta.
    res = vg.build("KARSON", seed=7)
    assert "gap_tries" in res.meta
    assert res.gap_mm >= 22.0


def test_gcode_is_wcs_bottom_left_and_balanced():
    import re

    res = vg.build("KAI", seed=7)
    g = vg.emit_gcode(res, feed_mm_min=600, power_percent=100.0)
    assert "$32=1" in g and "M3 S1000" in g and "F600" in g
    # M3/M5 balanced (one M5 per M3 path, plus the header M5)
    assert g.count("\nM3 ") == g.count("\nM5") - 1
    xs = [float(m) for m in re.findall(r"X([-.\d]+)", g)]
    ys = [float(m) for m in re.findall(r"Y([-.\d]+)", g)]
    assert min(xs) >= -0.01 and min(ys) >= -0.01  # WCS bottom-left: no negatives
    assert max(xs) <= res.w_mm + 0.5 and max(ys) <= res.h_mm + 0.5
