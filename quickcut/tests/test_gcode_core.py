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


# ---------------------------------------------------------------- ported from jigsawzall/emitter.py
import paths  # noqa: E402
from motion import follow_through  # noqa: E402


def test_trim_recut_ends_strips_dead_end_recut_only():
    """Re-cut line at a path's start/end connects to nothing and is dropped;
    a re-cut stretch BETWEEN two new cuts (a useful backtrack) is kept."""
    cut = paths.CutIndex()
    cut.add([(0.0, 0.0), (10.0, 0.0)])
    assert paths.trim_recut_ends([(0.0, 0.0), (5.0, 0.0), (5.0, 5.0)], cut) == [(5.0, 0.0), (5.0, 5.0)]
    assert paths.trim_recut_ends([(5.0, 5.0), (5.0, 0.0), (0.0, 0.0)], cut) == [(5.0, 5.0), (5.0, 0.0)]
    p = [(2.0, 5.0), (2.0, 0.0), (8.0, 0.0), (8.0, 5.0)]
    assert paths.trim_recut_ends(p, cut) == p
    assert len(paths.trim_recut_ends([(0.0, 0.0), (10.0, 0.0)], cut)) < 2


def test_follow_through_laps_until_lead_covered():
    sq = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]  # 40mm loop
    tail = follow_through(sq, 15.0, laps=True)
    assert tail[-1] == pytest.approx((10.0, 5.0))
    long_tail = follow_through(sq, 100.0, laps=True)   # 2.5 laps
    walked = sum(math.dist(a, b) for a, b in zip([sq[0]] + long_tail, long_tail))
    assert walked == pytest.approx(100.0)
    one = follow_through(sq, 100.0)                     # cut overlap: one lap at most
    assert sum(math.dist(a, b) for a, b in zip([sq[0]] + one, one)) == pytest.approx(40.0)


def test_bridge_chains_gaps_closed_loop_once():
    sq = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
    out = paths.bridge_chains([sq], [(5.0, 0.0)], 0.5)
    assert len(out) == 1  # loop opened at the gap, not split in two
    assert abs(sum(math.dist(a, b) for a, b in zip(out[0], out[0][1:])) - 39.5) < 1e-6
    assert math.dist(out[0][0], (5.25, 0)) < 1e-6 and math.dist(out[0][-1], (4.75, 0)) < 1e-6


def test_bridge_chains_three_faces_and_untouched_chain():
    tri = [(0, 0), (10, 0), (5, 8), (0, 0)]
    other = [(20, 0), (30, 0)]
    out = paths.bridge_chains([tri, other], [(5, 0), (7.5, 4), (2.5, 4)], 0.5)
    assert other in [list(map(tuple, c)) for c in out]
    tri_parts = [c for c in out if c != other]
    assert len(tri_parts) == 3
    total = sum(math.dist(a, b) for c in tri_parts for a, b in zip(c, c[1:]))
    assert abs(total - (10 + 2 * math.dist((0, 0), (5, 8)) - 3 * 0.5)) < 1e-6


def test_bridged_loop_leaves_the_bridges():
    sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
    out = gcode.bridged(sq, [(5, 0.3), (5, 10)], 2.0, kerf=0.1)   # first point off the line: projected
    assert len(out) == 2 and all(not pl.closed for pl in out)
    cut_len = sum(math.dist(a, b) for pl in out for a, b in zip(pl.points, pl.points[1:]))
    assert abs(cut_len - (40 - 2 * 2.1)) < 1e-6          # each bridge leaves width + kerf uncut


def test_etch_linear_warmup_and_loop_laps():
    j = gcode.CutJob(feed_mm_per_min=6000, power_percent=25, passes=1)
    assert j.etch_lead_mm == pytest.approx(1000 * 0.25 / 60000 * 6000)        # 25 mm
    loop = Polyline([(0, 0), (5, 0), (5, 5), (0, 5), (0, 0)], True)            # 20 mm loop: laps
    g = gcode.etch(loop, j)
    assert g[2] == "M3 S250" and g[-2] == "M5"
    tick = gcode.etch(Polyline([(0, 0), (2, 0)], False), j)                    # short tick: one out-and-back
    assert sum(l.startswith("G1") for l in tick) == 3
    assert gcode.etch(Polyline([(0, 0), (2, 0)], False), gcode.CutJob(1000, 50, 1, mode="dynamic"))[2] == "M4 S500"


def test_combine_one_preamble_frame_and_park():
    j = gcode.job("mdf3")
    a = gcode.render(gcode.preamble(j, "a") + gcode.cut(square(0, 0, 10), j))
    b = gcode.render(gcode.preamble(j, "b") + gcode.cut(square(100, 50, 10), j))
    t = gcode.combine(a, b).split("\n")
    assert sum(l.startswith("$32=") for l in t) == 1
    assert sum(l.startswith(gcode.FRAME_MARK) for l in t) == 1
    assert sum(l.startswith(gcode.PARK_MARK) for l in t) == 1
    assert sum(l.startswith(";HEAD:") for l in t) == 1
    frame = next(l for l in t if l.startswith(gcode.FRAME_MARK))
    assert "x 0.000 to 110.000" in frame and "to 60.000" in frame


def test_raster_s0_on_every_travel():
    from PIL import Image
    img = Image.new("L", (4, 2), 255)
    img.putpixel((1, 0), 0)
    img.putpixel((2, 1), 128)
    g = gcode.raster(img, 0.5, 50, 3000, "grayscale")
    assert all(l.endswith(" S0") for l in g if l.startswith("G0"))
    assert [l for l in g if l.startswith("G1")] == ["G1 X0.750 Y0.750 S500", "G1 X1.250 Y0.250 S249"]


def test_frame_arc_extent_is_the_sweep_not_the_circle():
    import frame
    # a quarter arc CCW from (10,0) to (0,10) about (0,0): reaches x 0..10, y 0..10, not -10
    assert frame.job_extent(["G0 X10 Y0", "G3 X0 Y10 I-10 J0"]) == (0.0, 0.0, 10.0, 10.0)
    # the same ends CW: three quarters round, through (0,-10) and (-10,0)
    assert frame.job_extent(["G0 X10 Y0", "G2 X0 Y10 I-10 J0"]) == (-10.0, -10.0, 10.0, 10.0)
    # a near-straight huge-radius arc stays near its chord
    ext = frame.job_extent(["G0 X0 Y0", "G2 X10 Y0 I5 J-1000"])
    assert ext[0] >= 0 and ext[2] <= 10 and -0.1 < ext[1] and ext[3] < 0.1
    # a full circle (same start and end)
    assert frame.job_extent(["G0 X10 Y0", "G2 X10 Y0 I-10 J0"]) == (-10.0, -10.0, 10.0, 10.0)
