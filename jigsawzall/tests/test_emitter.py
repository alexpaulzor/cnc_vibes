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

import math
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
    chain_contiguous_paths,
    classify_edge,
    combined_raster_and_cut,
    decimate_min_segment,
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
from geometry import (  # noqa: E402
    full_puzzle_config,
    generate_pieces,
    small_puzzle_config,
)


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


def test_simple_cut_never_emits_dwell():
    """Warmup dwells were removed — GRBL laser mode fires only while moving,
    so a G4 dwell produces no beam. Cold-start fade is handled by lead-in."""
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


def test_full_cut_static_mode_uses_m3_no_dwell():
    cfg = small_puzzle_config()
    g = emit_cut_gcode_full(
        _tiny_pieces(cfg),
        _tiny_material(),
        cfg,
        "NORA",
        mode="static",
    )
    assert ";LASER_MODE: static" in g
    assert re.search(r"^M3 ", g, re.MULTILINE)
    assert not re.search(r"^M4\b", g, re.MULTILINE)
    assert "G4 P" not in g


# ---------------------------------------------------------------------------
# Feed override + min-segment decimation
# ---------------------------------------------------------------------------


def test_decimate_drops_short_segments_keeps_endpoints():
    pts = [(0.0, 0.0), (0.01, 0.0), (0.02, 0.0), (1.0, 0.0)]
    out = decimate_min_segment(pts, 0.05)
    # the two 0.01 hops collapse; endpoints preserved
    assert out[0] == (0.0, 0.0)
    assert out[-1] == (1.0, 0.0)
    # every surviving segment >= 0.05
    for a, b in zip(out, out[1:]):
        assert math.hypot(b[0] - a[0], b[1] - a[1]) >= 0.05 - 1e-9


def test_decimate_noop_when_zero():
    pts = [(0.0, 0.0), (0.01, 0.0), (1.0, 0.0)]
    assert decimate_min_segment(pts, 0.0) == pts


def test_decimate_preserves_closed_ring_endpoint():
    # closed ring with a tiny final hop back to start
    pts = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.001, 0.0), (0.0, 0.0)]
    out = decimate_min_segment(pts, 0.05)
    assert out[0] == out[-1] == (0.0, 0.0)


def test_full_cut_feed_override():
    cfg = small_puzzle_config()
    g = emit_cut_gcode_full(
        _tiny_pieces(cfg), _tiny_material(), cfg, "NORA", feed_override=800
    )
    feeds = set(re.findall(r"^F(\d+)", g, re.MULTILINE))
    assert feeds == {"800"}  # material feed (500) fully overridden


def test_simple_cut_feed_override():
    cfg = small_puzzle_config()
    g = emit_cut_gcode_simple(
        _tiny_pieces(cfg), _tiny_material(), cfg, "X", feed_override=800
    )
    feeds = set(re.findall(r"^F(\d+)", g, re.MULTILINE))
    assert feeds == {"800"}


def test_full_cut_power_override():
    cfg = small_puzzle_config()
    # material is 80% (S800); override to 100% -> S1000
    g = emit_cut_gcode_full(
        _tiny_pieces(cfg), _tiny_material(), cfg, "NORA", power_percent=100
    )
    s_vals = set(re.findall(r"S(\d+)", g))
    assert s_vals == {"1000"}


def test_simple_cut_power_override():
    cfg = small_puzzle_config()
    g = emit_cut_gcode_simple(
        _tiny_pieces(cfg), _tiny_material(), cfg, "X", power_percent=100
    )
    s_vals = set(re.findall(r"S(\d+)", g))
    assert s_vals == {"1000"}


def test_cut_power_defaults_to_material_when_unset():
    cfg = small_puzzle_config()
    # _tiny_material is 80% -> S800 when no override given
    g = emit_cut_gcode_full(_tiny_pieces(cfg), _tiny_material(), cfg, "NORA")
    s_vals = set(re.findall(r"S(\d+)", g))
    assert s_vals == {"800"}


def test_full_cut_min_segment_enforced_in_output():
    cfg = full_puzzle_config()
    g = emit_cut_gcode_full(
        _tiny_pieces(cfg), _tiny_material(), cfg, "NORA", min_segment_mm=0.05
    )
    # Walk consecutive G0/G1 XY and assert every G1 chord >= 0.05mm
    prev = None
    shortest = float("inf")
    for ln in g.splitlines():
        m = re.match(r"^G([01]) X([-\d.]+) Y([-\d.]+)", ln)
        if not m:
            continue
        x, y = float(m.group(2)), float(m.group(3))
        if ln.startswith("G1 ") and prev is not None:
            d = math.hypot(x - prev[0], y - prev[1])
            if d > 0:
                shortest = min(shortest, d)
        prev = (x, y)
    assert shortest >= 0.05 - 1e-9


@pytest.mark.parametrize("word,seed", [("NORA", 42), ("NORA", 7), ("AYANA", 3)])
def test_full_cut_covers_every_piece_boundary(word, seed):
    """Regression for the shared-edge bug: adjacent cells must trace their
    shared edge with IDENTICAL vertices so dedup is clean and the emitted
    cut covers every piece boundary with no uncut gaps. Before the fix,
    mismatched tab-arc sampling left laser-off gaps mid-tab so pieces
    didn't separate."""
    from shapely.geometry import LineString
    from shapely.ops import unary_union
    from emitter import img_to_machine_mm

    cfg = full_puzzle_config()
    pieces, _ = generate_pieces(word, seed, cfg)
    material = {
        "id": "t",
        "laser": {"power_percent": 80, "feed_mm_per_min": 500, "passes": 1},
    }
    g = emit_cut_gcode_full(pieces, material, cfg, word)

    # Ideal boundary = union of every piece's rings (in machine mm).
    rings = []
    for p in pieces:
        poly = p["polygon"]
        for ring in [poly.exterior, *poly.interiors]:
            rings.append(
                LineString([img_to_machine_mm(x, y, cfg) for x, y in ring.coords])
            )
    ideal = unary_union(rings)

    # Emitted cut = all G1 segments.
    segs = []
    pos = None
    for ln in g.splitlines():
        m = re.match(r"^G([01]) X([-\d.]+) Y([-\d.]+)", ln)
        if not m:
            continue
        xy = (float(m.group(2)), float(m.group(3)))
        if ln.startswith("G1 ") and pos is not None:
            segs.append(LineString([pos, xy]))
        pos = xy
    cut = unary_union(segs)

    uncovered = ideal.difference(cut.buffer(0.05))
    assert uncovered.length < 0.1, (
        f"{word}/{seed}: {uncovered.length:.2f}mm of piece boundary is uncut "
        "(shared-edge dedup gap)"
    )


# ---------------------------------------------------------------------------
# Contiguous-path chaining (continuous cuts, no per-edge re-fire)
# ---------------------------------------------------------------------------


def test_chain_fuses_touching_edges():
    from shapely.geometry import LineString

    # three edges meeting end-to-end -> one chain
    e1 = LineString([(0, 0), (1, 0)])
    e2 = LineString([(1, 0), (1, 1)])
    e3 = LineString([(1, 1), (0, 1)])
    chains = chain_contiguous_paths([(e1, False), (e2, False), (e3, False)])
    assert len(chains) == 1
    assert chains[0][0] == (0.0, 0.0)
    assert chains[0][-1] == (0.0, 1.0)


def test_chain_splits_on_gap():
    from shapely.geometry import LineString

    e1 = LineString([(0, 0), (1, 0)])
    e2 = LineString([(5, 5), (6, 5)])  # far away -> new chain
    chains = chain_contiguous_paths([(e1, False), (e2, False)])
    assert len(chains) == 2


def test_chain_within_tolerance():
    from shapely.geometry import LineString

    e1 = LineString([(0, 0), (1.0, 0)])
    e2 = LineString([(1.05, 0), (2.0, 0)])  # 0.05 gap, under 0.1 tol
    chains = chain_contiguous_paths([(e1, False), (e2, False)], tol_px=0.1)
    assert len(chains) == 1


def test_full_cut_chaining_reduces_path_count():
    # A real puzzle: chained output must have far fewer M3 starts than the
    # raw deduped edge count, and zero needless re-fires (a G0 landing
    # exactly where the previous cut ended).
    cfg = full_puzzle_config()
    pieces, _ = generate_pieces("NORA", 7, cfg)
    # passes=1 so a 2nd pass's legitimate return-to-start isn't counted
    material = {
        "id": "t",
        "laser": {"power_percent": 80, "feed_mm_per_min": 500, "passes": 1},
    }
    g = emit_cut_gcode_full(pieces, material, cfg, "NORA")
    # Walk for G0 starts that coincide with the previous cut's end.
    seq = []
    for ln in g.splitlines():
        m = re.match(r"^G([01]) X([-\d.]+) Y([-\d.]+)", ln)
        if m:
            seq.append((ln[1], float(m.group(2)), float(m.group(3))))
    prev_end = None
    needless = 0
    i = 0
    while i < len(seq):
        if seq[i][0] == "0":
            gx, gy = seq[i][1], seq[i][2]
            if prev_end is not None:
                if math.hypot(gx - prev_end[0], gy - prev_end[1]) < 0.05:
                    needless += 1
            j = i + 1
            while j < len(seq) and seq[j][0] == "1":
                j += 1
            if j > i + 1:
                prev_end = (seq[j - 1][1], seq[j - 1][2])
            i = j
        else:
            i += 1
    assert needless == 0, f"{needless} cut paths needlessly re-fire at the prior end"


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


# ---------------------------------------------------------------------------
# GCode-derived previews (PNG + SVG emitted alongside every cut)
# ---------------------------------------------------------------------------


def test_parse_gcode_paths_extracts_cut_runs():
    import jigsaw  # noqa: E402

    gcode = "\n".join(
        [
            "G0 X0 Y0",
            "M3 S1000",
            "F800",
            "G1 X10 Y0",
            "G1 X10 Y10",
            "M5",
            "G0 X20 Y20",
            "M3 S1000",
            "G1 X30 Y20",
            "M5",
        ]
    )
    paths = jigsaw._parse_gcode_paths(gcode)
    assert len(paths) == 2
    assert paths[0][0] == (0.0, 0.0)
    assert paths[0][-1] == (10.0, 10.0)
    assert paths[1][0] == (20.0, 20.0)


def test_render_gcode_previews_writes_png_and_svg(tmp_path):
    import jigsaw  # noqa: E402

    cfg = full_puzzle_config()
    pieces, _ = generate_pieces("NORA", 7, cfg)
    material = {
        "id": "t",
        "laser": {"power_percent": 80, "feed_mm_per_min": 500, "passes": 1},
    }
    g = emit_cut_gcode_full(pieces, material, cfg, "NORA")
    stem = tmp_path / "preview_test"
    png, svg = jigsaw.render_gcode_previews(g, cfg, stem, title="t")
    assert png.exists() and png.suffix == ".png"
    assert svg.exists() and svg.suffix == ".svg"
    body = svg.read_text()
    assert body.startswith("<svg") and "</svg>" in body
    assert "<polyline" in body  # at least one cut path drawn
