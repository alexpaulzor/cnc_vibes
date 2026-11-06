"""quickcut.gcode / order / frame: the single laser G-code core."""
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gcode  # noqa: E402
import order  # noqa: E402
from gcode import Polyline  # noqa: E402


def square(x, y, s):
    return Polyline([(x, y), (x + s, y), (x + s, y + s), (x, y + s), (x, y)], True)


def test_job_reads_the_profile():
    j = gcode.job("mdf3")
    m = gcode.material("mdf3")["laser"]
    assert (j.feed_mm_per_min, j.passes, j.power_percent) == (m["feed_mm_per_min"], m["passes"], m["power_percent"])
    with pytest.raises(gcode.GcodeError):
        gcode.job("no_such_material")


def test_cut_is_static_m3_with_warmup_passes_and_overlap():
    j = gcode.job("mdf3")
    g = gcode.cut(square(10, 10, 20), j, 1, 1)
    assert g[1].startswith("G0 ") and g[2] == f"M3 S{j.power_s}" and g[-2] == "M5"
    assert any(l.startswith("; warmup wiggle") for l in g)
    assert sum(l.startswith("; pass ") for l in g) == (j.passes if j.passes > 1 else 0)
    assert any(l.startswith("; overlap") for l in g)
    assert not any(l.startswith("M4") for l in g)


def test_open_path_pingpongs():
    j = gcode.job("mdf3", passes=2)
    g = gcode.cut(Polyline([(0, 0), (50, 0)], False), j)
    assert "; pass 2/2 (reverse)" in g


def test_middle_out_holds_and_travel_shrinks():
    # two parts far apart, each with holes; the area sort cuts all holes then all outlines
    cuts = []
    for ox in (0, 200):
        cuts += [square(ox + 5, 5, 5), square(ox + 15, 5, 5), square(ox, 0, 30)]
    by_area = order.order_by_area(cuts)
    best = order.order_middle_out(cuts)
    before = order.precedence(best)
    seen = set()
    for i in range(len(best)):
        assert before[i] <= seen, "a cut came after the loop that contains it"
        seen.add(i)
    assert order.travel(best) < order.travel(by_area)


def test_hole_never_after_its_outline():
    outer, hole = square(0, 0, 100), square(40, 40, 10)
    out = order.order_middle_out([outer, hole], start=(39, 39))    # the hole is nearer the outline's start? still first
    assert out[0].points[0][0] >= 40 and out[-1].points[0][0] in (0, 100)


def test_bridged_leaves_gaps():
    pieces = gcode.bridged(square(0, 0, 20).points, [(10, 0), (10, 20)], width=2, kerf=0.1)
    assert len(pieces) == 2 and not any(p.closed for p in pieces)
    total = sum(math.dist(a, b) for p in pieces for a, b in zip(p.points, p.points[1:]))
    assert total == pytest.approx(80 - 2 * 2.1, abs=1e-6)


def test_render_adds_frame_and_park_and_validates(tmp_path):
    j = gcode.job("mdf3")
    lines = gcode.preamble(j, title="t") + gcode.cut(square(10, 10, 20), j)
    text = gcode.render(lines)
    assert text.index("; frame:") < text.index("M3 S")
    assert "; park:" in text
    p = gcode.write(tmp_path / "x.gcode", lines)
    assert p.read_text() == text


def test_render_refuses_long_lines_and_violations():
    j = gcode.job("mdf3")
    with pytest.raises(gcode.GcodeError):
        gcode.render(gcode.preamble(j, title="x" * 300) + gcode.cut(square(0, 0, 5), j))
    with pytest.raises(gcode.GcodeError):
        gcode.render(gcode.preamble(j) + ["G0 X10 Y10", "M3 S1000", "F99999", "G1 X20 Y10", "M5"])
