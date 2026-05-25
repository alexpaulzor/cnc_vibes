"""Tests for spoilboard.py — geometry, tiling, hole assignment, GCode shape."""

import re
import sys
from pathlib import Path

import pytest

LESSON_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LESSON_DIR))

from spoilboard import (  # noqa: E402
    Tile,
    compute_axis_splits,
    compute_hole_positions,
    compute_tiles,
    emit_circle_path,
    emit_tile_gcode,
)


# ---------------------------------------------------------------------------
# Hole positions
# ---------------------------------------------------------------------------


def test_hole_positions_count():
    holes = compute_hole_positions(400, 500, cols=9, rows=10, spacing=45)
    assert len(holes) == 90


def test_hole_positions_auto_center_in_x():
    # 9 holes at 45mm spacing span 360mm; panel 400mm => margin 20mm
    holes = compute_hole_positions(400, 500, cols=9, rows=10, spacing=45)
    xs = sorted({x for x, _ in holes})
    assert xs[0] == pytest.approx(20)
    assert xs[-1] == pytest.approx(380)


def test_hole_positions_auto_center_in_y():
    # 10 holes at 45 spacing span 405; panel 500 => margin 47.5
    holes = compute_hole_positions(400, 500, cols=9, rows=10, spacing=45)
    ys = sorted({y for _, y in holes})
    assert ys[0] == pytest.approx(47.5)
    assert ys[-1] == pytest.approx(452.5)


def test_hole_positions_custom_margins():
    holes = compute_hole_positions(
        400, 500, cols=2, rows=2, spacing=100, margin_x=10, margin_y=15
    )
    assert sorted(holes) == [(10, 15), (10, 115), (110, 15), (110, 115)]


def test_hole_positions_rejects_oversize_grid():
    with pytest.raises(ValueError, match="doesn't fit"):
        compute_hole_positions(100, 100, cols=9, rows=10, spacing=45)


# ---------------------------------------------------------------------------
# Axis splits
# ---------------------------------------------------------------------------


def test_axis_splits_none_needed_when_fits():
    # Panel 200, stock 300, no split needed
    holes_x = [20, 65, 110, 155]
    splits = compute_axis_splits(200, holes_x, stock_extent=300)
    assert splits == []


def test_axis_splits_one_split_when_panel_exceeds_stock():
    # Panel 400, stock 300, holes at 20, 65, 110, ..., 380 (9 holes)
    holes_x = [20 + i * 45 for i in range(9)]
    splits = compute_axis_splits(400, holes_x, stock_extent=300)
    assert len(splits) == 1
    # Split must fall between two consecutive hole positions
    s = splits[0]
    # Find consecutive holes that bracket s
    sorted_h = sorted(holes_x)
    for a, b in zip(sorted_h, sorted_h[1:]):
        if a < s < b:
            break
    else:
        pytest.fail(f"split {s} doesn't fall between any hole pair")


def test_axis_splits_fall_between_holes_never_through_one():
    holes_x = [20 + i * 45 for i in range(9)]
    splits = compute_axis_splits(400, holes_x, stock_extent=300)
    for s in splits:
        for h in holes_x:
            assert abs(s - h) > 1e-6, f"split {s} sits on hole {h}"


def test_axis_splits_each_tile_fits_in_stock():
    holes_x = [20 + i * 45 for i in range(9)]
    splits = compute_axis_splits(400, holes_x, stock_extent=300)
    bounds = [0.0] + splits + [400.0]
    for a, b in zip(bounds, bounds[1:]):
        assert (b - a) <= 300, f"tile {a}-{b} exceeds stock width 300"


def test_axis_splits_raises_when_stock_too_small():
    # 9 holes spaced 45mm; stock < min_between_holes won't work
    holes_x = [20 + i * 45 for i in range(9)]
    with pytest.raises(ValueError, match="can't find"):
        compute_axis_splits(400, holes_x, stock_extent=30)


# ---------------------------------------------------------------------------
# Tile assignment — every hole in exactly one tile
# ---------------------------------------------------------------------------


def test_compute_tiles_anolex_defaults_produces_four_tiles():
    holes = compute_hole_positions(400, 500, 9, 10, 45)
    tiles = compute_tiles(400, 500, holes, stock_w=300, stock_h=300)
    assert len(tiles) == 4


def test_every_hole_assigned_to_exactly_one_tile():
    holes = compute_hole_positions(400, 500, 9, 10, 45)
    tiles = compute_tiles(400, 500, holes, 300, 300)
    counts = {h: 0 for h in holes}
    for tile in tiles:
        for h in tile.holes:
            counts[h] += 1
    misassigned = [h for h, c in counts.items() if c != 1]
    assert misassigned == [], f"holes assigned to wrong number of tiles: {misassigned}"


def test_tile_total_area_equals_panel_area():
    holes = compute_hole_positions(400, 500, 9, 10, 45)
    tiles = compute_tiles(400, 500, holes, 300, 300)
    total = sum(t.w * t.h for t in tiles)
    assert total == pytest.approx(400 * 500)


def test_tiles_all_fit_in_stock():
    holes = compute_hole_positions(400, 500, 9, 10, 45)
    tiles = compute_tiles(400, 500, holes, 300, 300)
    for t in tiles:
        assert t.w <= 300 + 1e-6 and t.h <= 300 + 1e-6


def test_tile_hole_count_sums_to_total():
    holes = compute_hole_positions(400, 500, 9, 10, 45)
    tiles = compute_tiles(400, 500, holes, 300, 300)
    assert sum(len(t.holes) for t in tiles) == 90


def test_tile_local_coords_are_relative_to_tile_origin():
    tile = Tile(index=1, x0=100, y0=200, w=50, h=80, holes=[(110, 210), (140, 270)])
    local = tile.holes_tile_local
    assert local == [(10, 10), (40, 70)]


def test_smaller_panel_fits_in_one_tile():
    # Panel that already fits in stock; expect a single tile
    holes = compute_hole_positions(200, 200, 4, 4, 45)
    tiles = compute_tiles(200, 200, holes, 300, 300)
    assert len(tiles) == 1
    assert tiles[0].holes == holes


# ---------------------------------------------------------------------------
# Circle approximation
# ---------------------------------------------------------------------------


def test_circle_path_closes():
    pts = emit_circle_path(0, 0, 5)
    assert pts[0] == pytest.approx(pts[-1])


def test_circle_path_radius():
    pts = emit_circle_path(10, 20, 3)
    import math

    for x, y in pts:
        r = math.hypot(x - 10, y - 20)
        assert r == pytest.approx(3, abs=1e-6)


def test_circle_path_segment_count():
    pts = emit_circle_path(0, 0, 5, n_segments=12)
    assert len(pts) == 13  # 12 segments => 13 points (closed)


# ---------------------------------------------------------------------------
# GCode shape + validator contract
# ---------------------------------------------------------------------------


def _tiny_material():
    return {
        "id": "test_mat",
        "laser": {"power_percent": 80, "feed_mm_per_min": 400, "passes": 2},
    }


def _tile_with_two_holes():
    return Tile(
        index=1,
        x0=0,
        y0=0,
        w=100,
        h=100,
        holes=[(25, 25), (75, 75)],
    )


def test_gcode_has_validator_headers():
    g = emit_tile_gcode(_tile_with_two_holes(), hole_dia=6.5, material=_tiny_material())
    assert ";HEAD: laser" in g
    assert ";MATERIAL: test_mat" in g
    assert "$32=1" in g


def test_gcode_uses_m4_not_m3():
    g = emit_tile_gcode(_tile_with_two_holes(), 6.5, _tiny_material())
    assert re.search(r"^M4 ", g, re.MULTILINE)
    assert not re.search(r"^M3\b", g, re.MULTILINE)


def test_gcode_s_values_in_range():
    g = emit_tile_gcode(_tile_with_two_holes(), 6.5, _tiny_material())
    for m in re.finditer(r"\bS(\d+)\b", g):
        s = int(m.group(1))
        assert 0 <= s <= 1000


def test_gcode_cuts_holes_before_perimeter():
    g = emit_tile_gcode(_tile_with_two_holes(), 6.5, _tiny_material())
    hole_idx = g.find("; --- hole 1")
    perim_idx = g.find("; --- perimeter")
    assert hole_idx >= 0 and perim_idx >= 0
    assert hole_idx < perim_idx, "holes must be cut before perimeter releases the tile"


def test_gcode_all_coords_within_tile():
    tile = _tile_with_two_holes()
    g = emit_tile_gcode(tile, 6.5, _tiny_material())
    for m in re.finditer(r"^G[01].*?X([-\d.]+).*?Y([-\d.]+)", g, re.MULTILINE):
        x, y = float(m.group(1)), float(m.group(2))
        assert -0.1 <= x <= tile.w + 0.1, f"X={x} outside tile width {tile.w}"
        assert -0.1 <= y <= tile.h + 0.1, f"Y={y} outside tile height {tile.h}"


def test_gcode_one_hole_block_per_hole_plus_perimeter():
    tile = _tile_with_two_holes()
    g = emit_tile_gcode(tile, 6.5, _tiny_material())
    n_hole_blocks = g.count("; --- hole ")
    n_perim_blocks = g.count("; --- perimeter")
    assert n_hole_blocks == 2
    assert n_perim_blocks == 1


def test_gcode_pass_count_matches_material():
    g = emit_tile_gcode(_tile_with_two_holes(), 6.5, _tiny_material())
    # 2 passes per path * (2 holes + 1 perimeter) = 6 "pass 1 of 2" + 6 "pass 2 of 2"... no, 3 paths * 2 passes
    assert g.count("pass 1 of 2") == 3
    assert g.count("pass 2 of 2") == 3
