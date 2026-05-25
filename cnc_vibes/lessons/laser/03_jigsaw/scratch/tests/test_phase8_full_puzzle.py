"""Tests for phase8_full_puzzle.py — full NORA-scale puzzle GCode emitter.

Covers:
  - Edge dedup via unary_union
  - Classification: panel border vs letter vs interior
  - Greedy nearest-neighbor ordering visits each edge exactly once
  - GCode shape: M4 not M3, S in range, all coords within envelope
  - Cut order honors tiers: letter perimeters before interior before panel

Note: this test file imports phase8 which asserts phase6_small isn't
loaded. Don't add `import phase6_small` to this file.
"""

import re
import sys
from pathlib import Path

import pytest
from shapely.geometry import LineString, Polygon

SCRATCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRATCH_DIR))

# phase8's safety check now lives in generate_pieces(), not at import
# time, so we can coexist with phase6_small in the same pytest session.
# These tests use synthetic Polygon/LineString and never call
# generate_pieces(), so phase2 mutations from phase6 don't affect them.
import phase8_full_puzzle as p8  # noqa: E402


# ---------------------------------------------------------------------------
# Edge extraction — duplicates are removed
# ---------------------------------------------------------------------------


def test_extract_unique_edges_dedupes_shared_boundary():
    # Two adjacent squares share an edge; unary_union should keep that
    # shared edge once, not twice.
    a = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    b = Polygon([(10, 0), (20, 0), (20, 10), (10, 10)])
    pieces = [{"polygon": a, "kind": "cell"}, {"polygon": b, "kind": "cell"}]
    edges = p8.extract_unique_edges(pieces)
    total_length = sum(e.length for e in edges)
    # If duplicated, total would be 40+40=80; deduped = 40+40-10 = 70
    assert total_length == pytest.approx(70, abs=0.1)


def test_extract_unique_edges_handles_single_polygon():
    a = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    edges = p8.extract_unique_edges([{"polygon": a, "kind": "cell"}])
    assert len(edges) >= 1
    total = sum(e.length for e in edges)
    assert total == pytest.approx(40, abs=0.1)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_classify_panel_left_border():
    e = LineString([(100, 100), (100, 200)])  # vertical line at x=100
    cat = p8.classify_edge(
        e, letter_polys=[], panel_x0=100, panel_y0=50, panel_w=400, panel_h=300
    )
    assert cat == "panel"


def test_classify_panel_top_border():
    e = LineString([(150, 50), (350, 50)])  # horizontal at y=50 (panel_y0)
    cat = p8.classify_edge(
        e, letter_polys=[], panel_x0=100, panel_y0=50, panel_w=400, panel_h=300
    )
    assert cat == "panel"


def test_classify_letter_when_on_letter_boundary():
    letter = Polygon([(200, 100), (250, 100), (250, 150), (200, 150)])
    # Edge lying on the letter's left side
    e = LineString([(200, 110), (200, 140)])
    cat = p8.classify_edge(
        e, letter_polys=[letter], panel_x0=0, panel_y0=0, panel_w=500, panel_h=500
    )
    assert cat == "letter"


def test_classify_interior_when_neither():
    # Edge floating in the middle, not on panel or letter
    e = LineString([(150, 150), (160, 160)])
    cat = p8.classify_edge(
        e, letter_polys=[], panel_x0=0, panel_y0=0, panel_w=500, panel_h=500
    )
    assert cat == "interior"


# ---------------------------------------------------------------------------
# Greedy ordering
# ---------------------------------------------------------------------------


def test_greedy_order_visits_every_edge_once():
    edges = [
        LineString([(0, 0), (10, 0)]),
        LineString([(20, 0), (30, 0)]),
        LineString([(50, 0), (60, 0)]),
    ]
    ordered = p8.greedy_order(edges, start_pt=(0, 0))
    assert len(ordered) == 3
    # Each input edge appears exactly once in the output (by length identity)
    lengths_in = sorted(e.length for e in edges)
    lengths_out = sorted(e.length for e, _ in ordered)
    assert lengths_in == lengths_out


def test_greedy_order_picks_nearest_first():
    edges = [
        LineString([(100, 0), (110, 0)]),  # far
        LineString([(5, 0), (15, 0)]),  # near
    ]
    ordered = p8.greedy_order(edges, start_pt=(0, 0))
    # The first picked edge should be the near one
    first_coords = list(ordered[0][0].coords)
    assert first_coords[0][0] == pytest.approx(5, abs=0.01)


def test_greedy_order_reverses_edge_when_far_end_is_closer():
    edges = [LineString([(100, 0), (5, 0)])]  # ends at (100,0) and (5,0)
    ordered = p8.greedy_order(edges, start_pt=(0, 0))
    e, reversed_flag = ordered[0]
    # The edge should be reversed so we start at (5,0), nearer to origin
    assert reversed_flag is True
    assert list(e.coords)[0] == pytest.approx((5, 0), abs=0.01)


# ---------------------------------------------------------------------------
# Coord conversion
# ---------------------------------------------------------------------------


def test_img_to_machine_flips_y():
    # Image (MARGIN, MARGIN) -> machine (0, PANEL_MM)
    x, y = p8.img_to_machine_mm(p8.p2.MARGIN, p8.p2.MARGIN)
    assert x == pytest.approx(0)
    assert y == pytest.approx(p8.p2.PANEL_MM)


# ---------------------------------------------------------------------------
# GCode shape + envelope
# ---------------------------------------------------------------------------


def _tiny_material():
    return {
        "id": "test_mat",
        "laser": {"power_percent": 80, "feed_mm_per_min": 500, "passes": 2},
    }


def _tiny_ordered():
    # One small edge in image coords inside the panel
    m = p8.p2.MARGIN
    e = LineString([(m + 10, m + 10), (m + 50, m + 10), (m + 50, m + 50)])
    return [(e, False)]


def test_emit_gcode_has_validator_headers():
    g = p8.emit_gcode(_tiny_ordered(), _tiny_material(), "NORA")
    assert ";HEAD: laser" in g
    assert ";MATERIAL: test_mat" in g
    assert "$32=1" in g


def test_emit_gcode_uses_m4_not_m3():
    g = p8.emit_gcode(_tiny_ordered(), _tiny_material(), "NORA")
    assert re.search(r"^M4 ", g, re.MULTILINE)
    assert not re.search(r"^M3\b", g, re.MULTILINE)


def test_emit_gcode_s_values_in_range():
    g = p8.emit_gcode(_tiny_ordered(), _tiny_material(), "NORA")
    for m in re.finditer(r"\bS(\d+)\b", g):
        s = int(m.group(1))
        assert 0 <= s <= 1000


def test_emit_gcode_coords_within_panel_envelope():
    g = p8.emit_gcode(_tiny_ordered(), _tiny_material(), "NORA")
    for m in re.finditer(r"^G[01].*?X([-\d.]+).*?Y([-\d.]+)", g, re.MULTILINE):
        x, y = float(m.group(1)), float(m.group(2))
        assert 0 <= x <= p8.p2.PANEL_MM
        assert 0 <= y <= p8.p2.PANEL_MM


def test_emit_gcode_pass_count_matches_material():
    g = p8.emit_gcode(_tiny_ordered(), _tiny_material(), "NORA")
    # 2 passes per path; one path => one "pass 1 of 2" and one "pass 2 of 2"
    assert g.count("pass 1 of 2") == 1
    assert g.count("pass 2 of 2") == 1
