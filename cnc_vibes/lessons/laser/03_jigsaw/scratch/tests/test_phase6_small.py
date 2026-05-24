"""Tests for phase6_small.py — the small-puzzle GCode generator.

These tests verify:
  - The constant overrides hold (small panel dimensions actually apply).
  - polygon_to_paths_mm flips Y correctly and offsets by the margin.
  - emit_gcode produces a header the validator will accept (HEAD/MATERIAL/$32=1).
  - emit_gcode uses M4 (not M3) and S in [0, 1000].
  - Letters come before cells in the cut order.
"""

import re
import sys
from pathlib import Path

import pytest
from shapely.geometry import Polygon

SCRATCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRATCH_DIR))

# Import triggers the constant overrides; do this once at module load.
import phase6_small as p6  # noqa: E402


def test_overrides_applied_to_phase2():
    assert p6.p2.PANEL_MM == 80
    assert p6.p2.PIECE_MM == 40
    assert p6.p2.COLS == 2
    assert p6.p2.ROWS == 2
    assert p6.p2.CELL_W == 200
    assert p6.p2.TAB_CIRCLE_R == 15
    assert p6.p2.TAB_HEIGHT == 45


def test_overrides_propagated_to_phase5():
    # phase5 must see the overridden values, not its original defaults.
    assert p6.p5.TAB_CIRCLE_R == 15
    assert p6.p5.LETTER_CLEARANCE_PX == 15
    assert p6.p5.MIN_FRAGMENT_THICKNESS_PX == 15


def test_img_to_machine_flips_y_and_offsets_margin():
    # Image point at the panel's top-left corner (MARGIN, MARGIN) becomes
    # machine (0, PANEL_MM). Image grows Y-down, machine grows Y-up.
    x_mm, y_mm = p6.img_to_machine_mm(p6.p2.MARGIN, p6.p2.MARGIN)
    assert x_mm == pytest.approx(0)
    assert y_mm == pytest.approx(p6.p2.PANEL_MM)


def test_img_to_machine_bottom_right_corner():
    # Bottom-right of panel in the image -> bottom-right in machine coords
    # except Y is flipped so it's (PANEL_MM, 0).
    px = p6.p2.MARGIN + p6.p2.COLS * p6.p2.CELL_W
    py = p6.p2.MARGIN + p6.p2.ROWS * p6.p2.CELL_H
    x_mm, y_mm = p6.img_to_machine_mm(px, py)
    assert x_mm == pytest.approx(p6.p2.PANEL_MM)
    assert y_mm == pytest.approx(0)


def test_polygon_to_paths_returns_mm_points():
    # Square at image (MARGIN..MARGIN+CELL_W) in both axes => unit cell in mm
    m = p6.p2.MARGIN
    cw = p6.p2.CELL_W
    sq = Polygon([(m, m), (m + cw, m), (m + cw, m + cw), (m, m + cw)])
    paths = p6.polygon_to_paths_mm(sq)
    assert len(paths) == 1
    # All x values in [0, PIECE_MM] and y values in [PANEL_MM - PIECE_MM, PANEL_MM]
    for x, y in paths[0]:
        assert 0 <= x <= p6.p2.PIECE_MM + 1e-6
        assert (p6.p2.PANEL_MM - p6.p2.PIECE_MM) - 1e-6 <= y <= p6.p2.PANEL_MM + 1e-6


def test_order_inside_out_letters_first():
    pieces = [
        {"kind": "cell", "polygon": "a"},
        {"kind": "letter", "polygon": "b"},
        {"kind": "cell", "polygon": "c"},
        {"kind": "letter", "polygon": "d"},
    ]
    ordered = p6.order_inside_out(pieces)
    kinds = [p["kind"] for p in ordered]
    assert kinds == ["letter", "letter", "cell", "cell"]


# ---------------------------------------------------------------------------
# GCode shape tests — use a tiny synthetic piece list to keep tests fast
# ---------------------------------------------------------------------------


def _tiny_pieces():
    """One small letter + one small cell, valid polygons."""
    m = p6.p2.MARGIN
    return [
        {
            "kind": "letter",
            "polygon": Polygon(
                [(m + 10, m + 10), (m + 30, m + 10), (m + 30, m + 30), (m + 10, m + 30)]
            ),
        },
        {
            "kind": "cell",
            "polygon": Polygon(
                [(m + 50, m + 50), (m + 80, m + 50), (m + 80, m + 80), (m + 50, m + 80)]
            ),
        },
    ]


def _tiny_material():
    return {
        "id": "test_material",
        "laser": {"power_percent": 80, "feed_mm_per_min": 500, "passes": 2},
    }


def test_emit_gcode_has_validator_headers():
    g = p6.emit_gcode(_tiny_pieces(), _tiny_material(), "X")
    assert ";HEAD: laser" in g
    assert ";MATERIAL: test_material" in g
    assert "$32=1" in g


def test_emit_gcode_uses_m4_not_m3():
    g = p6.emit_gcode(_tiny_pieces(), _tiny_material(), "X")
    assert re.search(r"^M4 ", g, re.MULTILINE)
    assert not re.search(r"^M3\b", g, re.MULTILINE)


def test_emit_gcode_s_value_in_range():
    g = p6.emit_gcode(_tiny_pieces(), _tiny_material(), "X")
    for m in re.finditer(r"\bS(\d+)\b", g):
        s = int(m.group(1))
        assert 0 <= s <= 1000, f"S={s} out of range"


def test_emit_gcode_includes_pass_count_per_piece():
    g = p6.emit_gcode(_tiny_pieces(), _tiny_material(), "X")
    # 2 passes per piece * 2 pieces = 4 "pass N of 2" comments
    assert g.count("pass 1 of 2") == 2
    assert g.count("pass 2 of 2") == 2


def test_emit_gcode_ends_with_park_and_laser_off():
    g = p6.emit_gcode(_tiny_pieces(), _tiny_material(), "X")
    # Must end the cut sequence with M5, and have a final G0 X0 Y0 park
    lines = [l for l in g.splitlines() if l.strip()]
    assert "M5" in lines  # at least one M5 (one per piece)
    assert lines[-1] == "G0 X0 Y0"


def test_emit_gcode_within_panel_envelope():
    """All G0/G1 X,Y coords stay within [0, PANEL_MM]."""
    g = p6.emit_gcode(_tiny_pieces(), _tiny_material(), "X")
    for m in re.finditer(r"^G[01].*?X([-\d.]+).*?Y([-\d.]+)", g, re.MULTILINE):
        x, y = float(m.group(1)), float(m.group(2))
        assert 0 <= x <= p6.p2.PANEL_MM
        assert 0 <= y <= p6.p2.PANEL_MM
