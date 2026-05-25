"""Tests for phase7_raster.py — photo engraving GCode emitter.

Covers:
  - Image preprocessing (crop-square, resize to pixels_per_side)
  - Halftone vs grayscale encode shape
  - Run extraction (groupby of equal-value pixel spans)
  - Raster GCode: M4 not M3, S in [0, 1000], Y axis flipped correctly
  - The three output forms have the validator headers and stay within
    the panel envelope.
"""

import re
import sys
from pathlib import Path

import pytest
from PIL import Image

SCRATCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRATCH_DIR))

import phase7_raster as p7  # noqa: E402  (triggers phase6_small import chain)


# ---------------------------------------------------------------------------
# Image preprocessing
# ---------------------------------------------------------------------------


def test_test_pattern_size_matches_pixel_grid():
    img = p7._generate_test_pattern(40)
    assert img.size == (40, 40)
    assert img.mode == "L"


def test_test_pattern_has_gradient_and_disc():
    img = p7._generate_test_pattern(80)
    px = img.load()
    # left edge ~ black, right edge ~ white
    assert px[0, 40] < 30
    assert px[79, 40] > 220
    # disc in middle is dark (fill=64)
    assert px[40, 40] == 64


def test_load_and_preprocess_test_pattern():
    img = p7.load_and_preprocess(
        None, panel_mm=80, line_spacing_mm=0.2, test_pattern=True
    )
    # 80 / 0.2 = 400 pixels per side
    assert img.size == (400, 400)


def test_load_and_preprocess_requires_source():
    with pytest.raises(SystemExit):
        p7.load_and_preprocess(None, 80, 0.2, test_pattern=False)


def test_load_and_preprocess_crops_rectangle_to_square(tmp_path):
    # 200x100 rectangle; expect cropped to 100x100 then resized
    rect = Image.new("L", (200, 100), 128)
    path = tmp_path / "rect.png"
    rect.save(path)
    img = p7.load_and_preprocess(
        path, panel_mm=40, line_spacing_mm=0.5, test_pattern=False
    )
    # 40/0.5 = 80 px square
    assert img.size == (80, 80)


# ---------------------------------------------------------------------------
# Encoders
# ---------------------------------------------------------------------------


def test_halftone_encode_is_1bit():
    img = p7._generate_test_pattern(40)
    out = p7.halftone_encode(img)
    assert out.mode == "1"


def test_halftone_pixels_are_black_or_white():
    img = p7._generate_test_pattern(40)
    out = p7.halftone_encode(img).convert("L")
    seen = set()
    for px in out.getdata():
        seen.add(px)
    assert seen <= {0, 255}, f"unexpected pixel values in halftone: {seen}"


def test_grayscale_quantize_reduces_levels():
    # gradient 0..255; quantize to 4 levels => at most 4 distinct values
    img = Image.new("L", (256, 1))
    img.putdata(list(range(256)))
    out = p7.grayscale_quantize(img, n_levels=4)
    levels = set(out.getdata())
    assert len(levels) <= 4


def test_grayscale_quantize_passthrough_at_one_level():
    img = Image.new("L", (10, 10), 128)
    out = p7.grayscale_quantize(img, n_levels=1)
    assert out.size == (10, 10)


# ---------------------------------------------------------------------------
# Run extraction
# ---------------------------------------------------------------------------


def test_runs_in_row_groups_equal_values():
    row = [0, 0, 0, 255, 255, 0, 128, 128, 128]
    runs = p7.runs_in_row(row)
    assert runs == [(0, 2, 0), (3, 4, 255), (5, 5, 0), (6, 8, 128)]


def test_runs_in_row_empty():
    assert p7.runs_in_row([]) == []


def test_runs_in_row_single_value():
    assert p7.runs_in_row([42, 42, 42]) == [(0, 2, 42)]


# ---------------------------------------------------------------------------
# Raster GCode shape + validator contract
# ---------------------------------------------------------------------------


def _tiny_halftone_img(width=4, height=2):
    img = Image.new("1", (width, height), 1)  # 1 = white
    px = img.load()
    px[0, 0] = 0  # black
    px[1, 0] = 0
    px[3, 1] = 0
    return img


def test_raster_uses_m4_not_m3():
    img = _tiny_halftone_img()
    lines = p7.emit_raster_gcode(img, "halftone", 10, 0.5, 30, 3000)
    text = "\n".join(lines)
    assert re.search(r"^M4 ", text, re.MULTILINE)
    assert not re.search(r"^M3\b", text, re.MULTILINE)


def test_raster_s_values_within_range():
    img = _tiny_halftone_img()
    lines = p7.emit_raster_gcode(img, "halftone", 10, 0.5, 30, 3000)
    for m in re.finditer(r"\bS(\d+)\b", "\n".join(lines)):
        s = int(m.group(1))
        assert 0 <= s <= 1000, f"S={s} out of range"


def test_raster_y_flip_top_row_maps_to_top_y():
    # 1-row image, single black pixel at x=0. Top row -> machine Y near panel top.
    img = Image.new("1", (1, 1), 0)  # 0 = black = burn
    lines = p7.emit_raster_gcode(
        img,
        "halftone",
        panel_mm=10,
        line_spacing_mm=1,
        engrave_power_pct=30,
        engrave_feed=3000,
    )
    text = "\n".join(lines)
    # Single row (row_idx=0) -> machine_y = 10 - 0.5*1 = 9.5
    assert re.search(r"Y9\.500", text), f"expected Y9.500 in:\n{text}"


def test_raster_skips_white_runs():
    # All-white 4x1 image: no G1 lines emitted
    img = Image.new("1", (4, 1), 1)  # all white
    lines = p7.emit_raster_gcode(img, "halftone", 10, 0.5, 30, 3000)
    g1s = [l for l in lines if l.startswith("G1 ")]
    assert g1s == []


def test_grayscale_darker_pixel_yields_higher_s():
    img = Image.new("L", (3, 1))
    img.putdata([200, 100, 50])  # decreasing brightness => increasing burn
    lines = p7.emit_raster_gcode(img, "grayscale", 10, 0.5, 100, 3000)
    s_vals = [int(m.group(1)) for m in re.finditer(r"\bS(\d+)\b", "\n".join(lines))]
    # Skip the M4 S0 prefix
    s_vals = [s for s in s_vals if s > 0]
    assert s_vals == sorted(s_vals), f"expected ascending burn S values, got {s_vals}"


# ---------------------------------------------------------------------------
# Output assembly — three files
# ---------------------------------------------------------------------------


def test_raster_only_has_validator_headers():
    lines = p7.emit_raster_gcode(_tiny_halftone_img(), "halftone", 10, 0.5, 30, 3000)
    out = p7.raster_only_gcode(lines, "test_mat", "x.png", "halftone")
    assert ";HEAD: laser" in out
    assert ";MATERIAL: test_mat" in out
    assert "$32=1" in out


def test_cut_only_passes_through_phase6_output():
    cut = "; cut block contents\nG0 X0 Y0\n"
    assert p7.cut_only_gcode(cut) == cut


def test_combined_includes_both_raster_and_cut():
    raster_lines = ["; raster header", "G1 X1 Y1 S500"]
    cut_block = ";HEAD: laser\n;MATERIAL: x\n$32=1\nG21\nG90\nM5\nG0 X0 Y0\n; cut body\nG1 X2 Y2\n"
    out = p7.combined_gcode(raster_lines, cut_block, "test_mat", "img.png", "halftone")
    assert "; raster header" in out
    assert "G1 X1 Y1 S500" in out
    assert "; cut body" in out
    assert "G1 X2 Y2" in out
    # Should have ONE ;HEAD: laser line (combined file owns it), not two
    assert out.count(";HEAD: laser") == 1


def test_combined_envelope_check():
    """All raster moves must stay inside the panel."""
    img = p7.load_and_preprocess(None, 80, 0.5, test_pattern=True)
    encoded = p7.halftone_encode(img)
    raster_lines = p7.emit_raster_gcode(encoded, "halftone", 80, 0.5, 30, 3000)
    for m in re.finditer(
        r"^G[01].*?X([-\d.]+).*?Y([-\d.]+)", "\n".join(raster_lines), re.MULTILINE
    ):
        x, y = float(m.group(1)), float(m.group(2))
        assert 0 <= x <= 80
        assert 0 <= y <= 80
