"""Tests for font_7seg.py — every supported glyph has correct segment count,
bounds correct, error paths fire."""

import sys
from pathlib import Path

import pytest

LESSON_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LESSON_DIR))

from font_7seg import render_digit, render_text, text_width  # noqa: E402


# Expected segment counts per digit (per standard 7-seg convention)
EXPECTED_SEG_COUNTS = {
    "0": 6,
    "1": 2,
    "2": 5,
    "3": 5,
    "4": 4,
    "5": 5,
    "6": 6,
    "7": 3,
    "8": 7,
    "9": 6,
}


@pytest.mark.parametrize("digit,n", EXPECTED_SEG_COUNTS.items())
def test_each_digit_has_correct_segment_count(digit, n):
    assert len(render_digit(digit, 0, 0, 10)) == n


def test_digit_bounds_fit_w_by_h_box():
    h = 10.0
    w = h / 2
    for digit in "0123456789":
        segs = render_digit(digit, 0, 0, h)
        for x1, y1, x2, y2 in segs:
            assert 0 <= x1 <= w + 0.001 and 0 <= x2 <= w + 0.001
            assert 0 <= y1 <= h + 0.001 and 0 <= y2 <= h + 0.001


def test_digit_origin_translation_works():
    segs_at_origin = render_digit("8", 0, 0, 10)
    segs_translated = render_digit("8", 100, 50, 10)
    assert len(segs_at_origin) == len(segs_translated)
    for (x1a, y1a, x2a, y2a), (x1b, y1b, x2b, y2b) in zip(
        segs_at_origin, segs_translated
    ):
        assert x1b - x1a == pytest.approx(100)
        assert y1b - y1a == pytest.approx(50)
        assert x2b - x2a == pytest.approx(100)
        assert y2b - y2a == pytest.approx(50)


def test_unsupported_glyph_raises():
    for bad in ("x", "A", " ", "."):
        with pytest.raises(ValueError, match="unsupported glyph"):
            render_digit(bad, 0, 0, 10)


def test_text_renders_each_digit_at_advancing_x():
    segs = render_text("123", 0, 0, 10, spacing=1)
    # 1 + 2 + 3 == 2 + 5 + 5 segments
    assert len(segs) == 2 + 5 + 5


def test_text_width_matches_render_extents():
    h = 10.0
    spacing = 1.5
    segs = render_text("12345", 0, 0, h, spacing=spacing)
    max_x = max(max(x1, x2) for x1, y1, x2, y2 in segs)
    width = text_width("12345", h, spacing=spacing)
    # max_x should equal width within float epsilon (last digit's right edge)
    assert max_x == pytest.approx(width)


def test_text_width_empty_string_is_zero():
    assert text_width("", 10) == 0.0


def test_text_width_single_digit_has_no_extra_spacing():
    h = 10.0
    assert text_width("8", h, spacing=2) == h / 2  # just the digit width
