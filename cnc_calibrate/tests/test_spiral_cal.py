"""Tests for the elliptical feed × pass-count laser calibration generator.

Validates: the pass-count zigzag yields a distinct permutation of 1..N; the
emitted gcode ping-pongs multi-pass (no beam-on reposition between passes);
machine conventions (static M3, no M4, engrave vs cut power); classic back-compat
(passes=[1], aspect=1.0 = single loop + two warmup radials, no engraving)."""

import math
import re
import sys
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_DIR))

import spiral_cal as sc  # noqa: E402


# --- sector pass-count pattern ---


def test_sector_counts_are_distinct_permutation():
    for n in range(1, 9):
        counts = sc.sector_pass_counts(n)
        assert len(counts) == n
        assert sorted(counts) == list(range(1, n + 1)), (n, counts)


def test_sector_counts_known_values():
    assert sc.sector_pass_counts(3) == [2, 3, 1]
    assert sc.sector_pass_counts(4) == [2, 4, 3, 1]
    assert sc.sector_pass_counts(5) == [2, 4, 5, 3, 1]


# --- geometry ---


def test_ellipse_perimeter_circle_limit():
    # a == b -> 2*pi*r
    assert abs(sc.ellipse_perimeter(10, 10) - 2 * math.pi * 10) < 1e-6


def test_feeds_scale_and_count():
    _, b, feeds, T, meta = sc.generate(
        10, 3, 30, 13.4, 100.0, passes=[1, 2, 3], aspect=1.35
    )
    assert len(feeds) == 10
    assert feeds == sorted(feeds)  # inner slow -> outer fast
    assert feeds[0] < 150 and feeds[-1] > 900  # ~100..1000 range


# --- gcode conventions ---


def _ring_blocks(lines):
    """Yield the list of lines for each '--- ring #k ...' block."""
    block, in_block = [], False
    for ln in lines:
        if ln.startswith("; --- ring #"):
            if block:
                yield block
            block, in_block = [ln], True
        elif in_block:
            if ln.startswith("; --- ") and not ln.startswith("; --- ring #"):
                yield block
                block, in_block = [], False
            else:
                block.append(ln)
    if block:
        yield block


def test_static_m3_no_m4_and_engrave_power():
    lines, *_ = sc.generate(6, 3, 24, 13.0, 100.0, passes=[1, 2, 3], aspect=1.35)
    g = "\n".join(lines)
    assert "\nM4" not in g  # static M3 only
    s_vals = {int(m) for m in re.findall(r"M3 S(\d+)", g)}
    assert 1000 in s_vals  # cut power
    assert any(s < 1000 for s in s_vals)  # engrave power distinct
    assert all(0 <= s <= 1000 for s in s_vals)


def test_multipass_pingpongs_no_reposition_between_passes():
    """Within a ring, once the base loop starts there must be NO G0 until M5 —
    the passes ping-pong continuously (no beam-on / blanked reposition)."""
    lines, *_ = sc.generate(4, 4, 20, 13.0, 100.0, passes=[1, 2, 3], aspect=1.35)
    checked = 0
    for block in _ring_blocks(lines):
        # find the base loop marker; after it, no G0 until the closing M5
        try:
            start = next(i for i, ln in enumerate(block) if "base loop" in ln)
        except StopIteration:
            continue
        end = next(i for i, ln in enumerate(block) if ln.strip() == "M5")
        assert not any(block[i].startswith("G0") for i in range(start, end)), block
        checked += 1
    assert checked >= 4


def test_classic_back_compat():
    """passes=[1], aspect=1.0 -> classic: one loop per ring, two warmup radials,
    no engraving."""
    lines, _, _, _, meta = sc.generate(5, 3, 20, 3.0, 100.0, passes=[1], aspect=1.0)
    g = "\n".join(lines)
    assert meta["classic"] is True
    assert "S150" not in g and "engrave" not in g.lower()
    assert "base loop" not in g  # single loop, no zigzag
    assert "radial cross-sections" in g  # the two warmup radials


def test_sector_dividers_present_for_passes():
    lines, *_ = sc.generate(6, 3, 24, 13.0, 100.0, passes=[1, 2, 3], aspect=1.35)
    g = "\n".join(lines)
    assert "sector dividers" in g


def test_parse_passes_rejects_non_contiguous():
    import argparse

    import pytest

    assert sc._parse_passes("1,2,3") == [1, 2, 3]
    assert sc._parse_passes("3,1,2") == [1, 2, 3]
    assert sc._parse_passes("") == [1]
    for bad in ("2,3,4", "1,3", "5", "1,1,2"):
        with pytest.raises(argparse.ArgumentTypeError):
            sc._parse_passes(bad)
