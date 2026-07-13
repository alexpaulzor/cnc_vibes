"""Tests for the DORMANT raster pipeline: encoder.py + emitter.py raster path.

Split out of tests/test_emitter.py when raster was encapsulated under raster/.
Covers:
- Encoder modes (halftone / grayscale / preprocess)
- Raster GCode emission (M4 dynamic, S range, run-grouping)
- Combined (raster + cut) / raster-only emission header dedup
"""

import re
import sys
from pathlib import Path

import pytest
from PIL import Image

# raster/tests/ -> raster/ (encoder) and jigsawzall/ (emitter, geometry).
OWN_DIR = Path(__file__).resolve().parent
RASTER_DIR = OWN_DIR.parent
PKG_DIR = RASTER_DIR.parent
sys.path.insert(0, str(PKG_DIR))
sys.path.insert(0, str(RASTER_DIR))

from encoder import (  # noqa: E402
    generate_test_pattern,
    grayscale_quantize,
    halftone_encode,
    load_and_preprocess,
)
from emitter import (  # noqa: E402
    combined_raster_and_cut,
    emit_raster_gcode,
    raster_only_gcode,
    runs_in_row,
)
from geometry import small_puzzle_config  # noqa: E402


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
    assert not [ln for ln in lines if ln.startswith("G1 ")]


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
