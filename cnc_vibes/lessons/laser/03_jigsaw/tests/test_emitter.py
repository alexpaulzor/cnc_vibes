"""Tests for encoder.py + emitter.py — the productionized GCode pipeline.

Validates:
- Encoder modes produce expected pixel-value characteristics
- Emitter GCode passes the validator contract (HEAD/MATERIAL/$32=1,
  M4 not M3, S in [0, 1000])
- Cut emission for both small (simple per-polygon) and full
  (dedup + toposort) paths
- Coordinate conversion flips Y correctly + keeps moves within panel
- Combined (raster + cut) emission deduplicates headers
"""

import re
import sys
from pathlib import Path

import pytest
from PIL import Image
from shapely.geometry import LineString, Polygon

LESSON_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LESSON_DIR))

from encoder import (  # noqa: E402
    generate_test_pattern,
    grayscale_quantize,
    halftone_encode,
    load_and_preprocess,
)
from emitter import (  # noqa: E402
    classify_edge,
    combined_raster_and_cut,
    emit_cut_gcode_full,
    emit_cut_gcode_simple,
    emit_raster_gcode,
    extract_unique_edges,
    greedy_order,
    img_to_machine_mm,
    order_inside_out,
    raster_only_gcode,
    runs_in_row,
)
from geometry import full_puzzle_config, small_puzzle_config  # noqa: E402


# ---------------------------------------------------------------------------
# Encoder
# ---------------------------------------------------------------------------


def test_test_pattern_has_gradient_and_disc():
    img = generate_test_pattern(80)
    assert img.size == (80, 80)
    assert img.mode == "L"
    px = img.load()
    assert px[0, 40] < 30  # left edge ~ black
    assert px[79, 40] > 220  # right edge ~ white
    assert px[40, 40] == 64  # disc center


def test_halftone_pixels_are_only_0_or_255():
    img = generate_test_pattern(40)
    out = halftone_encode(img)
    assert out.mode == "L"
    seen = set(out.getdata())
    assert seen <= {0, 255}


def test_grayscale_quantize_reduces_levels():
    img = Image.new("L", (256, 1))
    img.putdata(list(range(256)))
    out = grayscale_quantize(img, n_levels=4)
    assert len(set(out.getdata())) <= 4


def test_load_and_preprocess_test_pattern_sized_to_grid():
    img = load_and_preprocess(None, panel_mm=80, line_spacing_mm=0.2, test_pattern=True)
    assert img.size == (400, 400)


def test_load_and_preprocess_requires_path_when_not_test():
    with pytest.raises(ValueError):
        load_and_preprocess(None, 80, 0.2, test_pattern=False)


def test_load_and_preprocess_crops_rectangle_to_square(tmp_path):
    rect = Image.new("L", (200, 100), 128)
    p = tmp_path / "rect.png"
    rect.save(p)
    img = load_and_preprocess(p, panel_mm=40, line_spacing_mm=0.5, test_pattern=False)
    assert img.size == (80, 80)


# ---------------------------------------------------------------------------
# Emitter: coord conversion
# ---------------------------------------------------------------------------


def test_img_to_machine_flips_y_at_panel_top():
    cfg = small_puzzle_config()
    x, y = img_to_machine_mm(cfg.margin_px, cfg.margin_px, cfg)
    assert x == pytest.approx(0)
    assert y == pytest.approx(cfg.panel_mm)


def test_img_to_machine_panel_bottom_right():
    cfg = full_puzzle_config()
    px = cfg.margin_px + cfg.puzzle_w_px
    py = cfg.margin_px + cfg.puzzle_h_px
    x, y = img_to_machine_mm(px, py, cfg)
    assert x == pytest.approx(cfg.panel_mm)
    assert y == pytest.approx(0)


# ---------------------------------------------------------------------------
# Emitter: simple cut (small puzzle path)
# ---------------------------------------------------------------------------


def _tiny_material():
    return {
        "id": "test_mat",
        "laser": {"power_percent": 80, "feed_mm_per_min": 500, "passes": 2},
    }


def _tiny_pieces(cfg):
    m = cfg.margin_px
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


def test_order_inside_out_letters_first():
    pieces = [
        {"kind": "cell", "polygon": "a"},
        {"kind": "letter", "polygon": "b"},
        {"kind": "cell", "polygon": "c"},
        {"kind": "letter", "polygon": "d"},
    ]
    kinds = [p["kind"] for p in order_inside_out(pieces)]
    assert kinds == ["letter", "letter", "cell", "cell"]


def test_simple_cut_validator_headers():
    cfg = small_puzzle_config()
    g = emit_cut_gcode_simple(_tiny_pieces(cfg), _tiny_material(), cfg, "X")
    assert ";HEAD: laser" in g
    assert ";MATERIAL: test_mat" in g
    assert "$32=1" in g


def test_simple_cut_uses_m4_not_m3():
    cfg = small_puzzle_config()
    g = emit_cut_gcode_simple(_tiny_pieces(cfg), _tiny_material(), cfg, "X")
    assert re.search(r"^M4 ", g, re.MULTILINE)
    assert not re.search(r"^M3\b", g, re.MULTILINE)


def test_simple_cut_pass_count_matches_material():
    cfg = small_puzzle_config()
    g = emit_cut_gcode_simple(_tiny_pieces(cfg), _tiny_material(), cfg, "X")
    # 2 passes per polygon * 2 polygons = 2 "pass 1 of 2" + 2 "pass 2 of 2"
    assert g.count("pass 1 of 2") == 2
    assert g.count("pass 2 of 2") == 2


def test_simple_cut_warmup_emits_dwell_per_path():
    cfg = small_puzzle_config()
    # 2 pieces, 1 path each -> 2 warmup dwells
    g = emit_cut_gcode_simple(
        _tiny_pieces(cfg), _tiny_material(), cfg, "X", warmup_ms=300
    )
    dwells = [l for l in g.splitlines() if l.startswith("G4 P0.300")]
    assert len(dwells) == 2


def test_simple_cut_no_dwell_when_warmup_zero():
    cfg = small_puzzle_config()
    g = emit_cut_gcode_simple(_tiny_pieces(cfg), _tiny_material(), cfg, "X")
    assert "G4 P" not in g


def test_simple_cut_static_mode_uses_m3_with_header():
    cfg = small_puzzle_config()
    g = emit_cut_gcode_simple(
        _tiny_pieces(cfg), _tiny_material(), cfg, "X", mode="static"
    )
    assert re.search(r"^M3 ", g, re.MULTILINE)
    assert not re.search(r"^M4\b", g, re.MULTILINE)
    assert ";LASER_MODE: static" in g


def test_full_cut_warmup_and_static_mode():
    cfg = small_puzzle_config()
    g = emit_cut_gcode_full(
        _tiny_pieces(cfg),
        _tiny_material(),
        cfg,
        "NORA",
        mode="static",
        warmup_ms=250,
    )
    assert ";LASER_MODE: static" in g
    assert re.search(r"^M3 ", g, re.MULTILINE)
    assert not re.search(r"^M4\b", g, re.MULTILINE)
    assert any(l.startswith("G4 P0.250") for l in g.splitlines())


def test_simple_cut_coords_within_panel():
    cfg = small_puzzle_config()
    g = emit_cut_gcode_simple(_tiny_pieces(cfg), _tiny_material(), cfg, "X")
    for m in re.finditer(r"^G[01].*?X([-\d.]+).*?Y([-\d.]+)", g, re.MULTILINE):
        x, y = float(m.group(1)), float(m.group(2))
        assert 0 <= x <= cfg.panel_mm
        assert 0 <= y <= cfg.panel_mm


# ---------------------------------------------------------------------------
# Emitter: full cut (dedup + toposort path)
# ---------------------------------------------------------------------------


def test_extract_unique_edges_dedupes_shared_boundary():
    a = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    b = Polygon([(10, 0), (20, 0), (20, 10), (10, 10)])
    pieces = [{"polygon": a, "kind": "cell"}, {"polygon": b, "kind": "cell"}]
    edges = extract_unique_edges(pieces)
    # If duplicated: 40+40=80 total length. Deduped: 40+40-10 = 70.
    assert sum(e.length for e in edges) == pytest.approx(70, abs=0.1)


def test_classify_panel_border():
    cfg = full_puzzle_config()
    # Vertical line on the left panel border
    panel_x0 = cfg.margin_px
    e = LineString([(panel_x0, panel_x0 + 100), (panel_x0, panel_x0 + 200)])
    assert classify_edge(e, letter_polys=[], cfg=cfg) == "panel"


def test_classify_letter_on_letter_boundary():
    cfg = full_puzzle_config()
    letter = Polygon(
        [
            (cfg.margin_px + 100, cfg.margin_px + 100),
            (cfg.margin_px + 200, cfg.margin_px + 100),
            (cfg.margin_px + 200, cfg.margin_px + 200),
            (cfg.margin_px + 100, cfg.margin_px + 200),
        ]
    )
    e = LineString(
        [
            (cfg.margin_px + 100, cfg.margin_px + 120),
            (cfg.margin_px + 100, cfg.margin_px + 180),
        ]
    )
    assert classify_edge(e, letter_polys=[letter], cfg=cfg) == "letter"


def test_classify_interior_when_neither():
    cfg = full_puzzle_config()
    e = LineString(
        [
            (cfg.margin_px + 300, cfg.margin_px + 300),
            (cfg.margin_px + 310, cfg.margin_px + 310),
        ]
    )
    assert classify_edge(e, letter_polys=[], cfg=cfg) == "interior"


def test_greedy_order_visits_every_edge_once():
    edges = [LineString([(0, 0), (10, 0)]), LineString([(50, 0), (60, 0)])]
    ordered = greedy_order(edges, start_pt=(0, 0))
    assert len(ordered) == 2


def test_full_cut_validator_headers_and_m4():
    cfg = full_puzzle_config()
    g = emit_cut_gcode_full(_tiny_pieces(cfg), _tiny_material(), cfg, "NORA")
    assert ";HEAD: laser" in g
    assert ";MATERIAL: test_mat" in g
    assert "$32=1" in g
    assert re.search(r"^M4 ", g, re.MULTILINE)
    assert not re.search(r"^M3\b", g, re.MULTILINE)


# ---------------------------------------------------------------------------
# Emitter: raster
# ---------------------------------------------------------------------------


def test_runs_in_row_groups_equal_values():
    assert runs_in_row([0, 0, 255, 255, 0]) == [
        (0, 1, 0),
        (2, 3, 255),
        (4, 4, 0),
    ]


def test_runs_in_row_empty():
    assert runs_in_row([]) == []


def test_raster_uses_m4_not_m3():
    cfg = small_puzzle_config()
    img = Image.new("L", (4, 1), 0)  # all black so we emit some G1s
    lines = emit_raster_gcode(img, "halftone", cfg, 0.5, 30, 3000)
    text = "\n".join(lines)
    assert re.search(r"^M4 ", text, re.MULTILINE)
    assert not re.search(r"^M3\b", text, re.MULTILINE)


def test_raster_skips_white_runs():
    cfg = small_puzzle_config()
    img = Image.new("L", (4, 1), 255)  # all white
    lines = emit_raster_gcode(img, "halftone", cfg, 0.5, 30, 3000)
    assert not [l for l in lines if l.startswith("G1 ")]


def test_raster_s_within_range():
    cfg = small_puzzle_config()
    img = Image.new("L", (4, 4), 0)
    lines = emit_raster_gcode(img, "halftone", cfg, 0.5, 30, 3000)
    for m in re.finditer(r"\bS(\d+)\b", "\n".join(lines)):
        assert 0 <= int(m.group(1)) <= 1000


def test_grayscale_darker_pixel_yields_higher_s():
    cfg = small_puzzle_config()
    img = Image.new("L", (3, 1))
    img.putdata([200, 100, 50])  # decreasing brightness
    lines = emit_raster_gcode(img, "grayscale", cfg, 0.5, 100, 3000)
    s_vals = [int(m.group(1)) for m in re.finditer(r"\bS(\d+)\b", "\n".join(lines))]
    s_vals = [s for s in s_vals if s > 0]
    assert s_vals == sorted(s_vals)


# ---------------------------------------------------------------------------
# Combined raster + cut: no duplicate headers
# ---------------------------------------------------------------------------


def test_combined_has_single_head_and_material():
    raster_lines = ["; raster header", "G1 X1 Y1 S500"]
    cut_block = ";HEAD: laser\n;MATERIAL: x\n$32=1\nG21\nG90\nM5\nG0 X0 Y0\n; cut body\nG1 X2 Y2\n"
    out = combined_raster_and_cut(
        raster_lines, cut_block, "test_mat", "img.png", "halftone"
    )
    assert out.count(";HEAD: laser") == 1
    assert "; raster header" in out
    assert "; cut body" in out


def test_raster_only_includes_validator_headers():
    out = raster_only_gcode(["G1 X1 Y1 S500"], "test_mat", "img.png", "halftone")
    assert ";HEAD: laser" in out
    assert ";MATERIAL: test_mat" in out
    assert "$32=1" in out
