"""Tests for the GRBL motion/stream model (quickcut/grbl_model.py), the arc
fitter (quickcut/arcfit.py) and their hooks in emitter.emit_etch_gcode."""

import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from shapely.geometry import LineString, Point

PKG_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_DIR))
sys.path.insert(0, str(PKG_DIR.parent / "quickcut"))

import arcfit  # noqa: E402
import grbl_model as G  # noqa: E402
from emitter import emit_etch_gcode, load_material  # noqa: E402

SD = G.SenderProfile(kind="sd")


def _stroke(pts, feed=3000, s=1000):
    head = f"G0 X{pts[0][0]:.3f} Y{pts[0][1]:.3f}\nM3 S{s}\nF{feed}\n"
    return head + "".join(f"G1 X{x:.3f} Y{y:.3f}\n" for x, y in pts[1:]) + "M5\n"


def _circle(cx, cy, r, n, close=True):
    k = n + 1 if close else n
    return [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n))
            for i in range(k)]


# --- planner ---------------------------------------------------------------


def test_straight_line_matches_trapezoid():
    # 100mm at F3000 (50mm/s), a=1000: 1.25mm to accelerate, 1.25mm to brake
    res = G.simulate(_stroke([(0, 0), (100, 0)]), G.MachineProfile(), SD)
    (st,) = G.stroke_stats(res)
    expected = (100 - 2.5) / 50 + 2 * 0.05
    assert st.actual_s - st.dwell_s == pytest.approx(expected, rel=1e-6)


def test_junction_speed_follows_grbl_formula():
    m = G.MachineProfile(accel_x=1000, accel_y=1000, junction_deviation_mm=0.01)
    res = G.simulate(_stroke([(0, 0), (50, 0), (50, 50)]), m, SD)
    # 90deg corner: sin(theta/2)=sqrt(0.5), v^2 = a_j*jd*s/(1-s); the junction
    # vector (-1,1)/sqrt2 lets the junction accel reach 1000*sqrt2
    s = math.sqrt(0.5)
    vj = math.sqrt(1000 * math.sqrt(2) * 0.01 * s / (1 - s))
    assert res.block_vexit[0] == pytest.approx(vj, rel=1e-6)


def test_reversal_stops_dead():
    res = G.simulate(_stroke([(0, 0), (20, 0), (0, 0)]), G.MachineProfile(), SD)
    assert res.block_vexit[0] == pytest.approx(0.0, abs=1e-9)


def test_planner_depth_limits_cruise_on_short_blocks():
    # 200 x 0.2mm collinear blocks: with 3 blocks of lookahead the head must
    # always be able to stop within 0.6mm -> v <= sqrt(2*a*0.6) ~ 34.6mm/s
    pts = [(0.2 * i, 0) for i in range(201)]
    g = _stroke(pts, feed=5000)
    shallow = G.stroke_stats(G.simulate(g, G.MachineProfile(planner_blocks=3), SD))[0]
    deep = G.stroke_stats(G.simulate(g, G.MachineProfile(planner_blocks=31), SD))[0]
    assert max(G.simulate(g, G.MachineProfile(planner_blocks=3), SD).block_vmax) <= (
        math.sqrt(2 * 1000 * 0.6) + 1e-6)
    assert shallow.energy_factor > deep.energy_factor


def test_slow_sender_starves_planner():
    pts = _circle(50, 50, 10, 200)
    g = _stroke(pts, feed=5000)
    fast = G.stroke_stats(G.simulate(g, G.MachineProfile(), SD))[0]
    slow = G.stroke_stats(G.simulate(g, G.MachineProfile(),
                                     G.SenderProfile(kind="rate", lines_per_s=20)))[0]
    # 200 lines at 20 lines/s: ~10s, less the few lines that pre-filled the
    # RX buffer while the M3 sync waited
    assert slow.actual_s >= 180 / 20
    assert slow.energy_factor > 2 * fast.energy_factor


def test_ping_pong_slower_than_char_count():
    g = _stroke(_circle(50, 50, 10, 300), feed=5000)
    m = G.MachineProfile()
    cc = G.simulate(g, m, G.SenderProfile(kind="char_count", latency_ms=10)).total_s
    pp = G.simulate(g, m, G.SenderProfile(kind="ping_pong", latency_ms=10)).total_s
    assert pp > cc


def test_each_stroke_starts_from_rest_after_m3_sync():
    g = _stroke([(0, 0), (30, 0)]) + _stroke([(30, 0), (60, 0)])
    res = G.simulate(g, G.MachineProfile(), SD)
    first = [st["first_block"] for st in res.program.strokes]
    assert len(first) == 2
    assert all(res.block_vmin[i] == 0.0 for i in first)


def test_arc_segments_follow_mc_arc():
    # quarter circle r=10 with $12=0.002: floor(0.5*pi/2*10 / sqrt(0.002*19.998))
    pts = G.arc_segments(10, 0, 0, 10, -10, 0, False, 0.002)
    n = math.floor(0.5 * (math.pi / 2) * 10 / math.sqrt(0.002 * (2 * 10 - 0.002)))
    assert len(pts) == n
    assert pts[-1] == (0, 10)
    assert all(abs(math.hypot(x, y) - 10) < 1e-9 for x, y in pts)


def test_g2_g3_parse_into_blocks():
    g = "G0 X10 Y0\nM3 S1000\nG3 X0 Y10 I-10 J0 F3000\nM5\n"
    prog = G.parse_program(g, G.MachineProfile())
    laser = [b for b in prog.blocks if b.laser]
    assert len(laser) > 10
    assert (laser[-1].x1, laser[-1].y1) == (0.0, 10.0)
    assert len(prog.lines[2].blocks) == len(laser)  # one line, many blocks


def test_from_dump_reads_dollars_and_opt():
    dump = "$11=0.020\n$12=0.005\n$110=8000.000\n$111=7000.000\n$120=1500\n$121=1200\n" \
           "[VER:1.3a.20211103:]\n[OPT:PHSW,15,256]\nok\n"
    m = G.MachineProfile.from_dump(dump)
    assert (m.junction_deviation_mm, m.arc_tolerance_mm) == (0.02, 0.005)
    assert (m.max_rate_x, m.max_rate_y, m.accel_x, m.accel_y) == (8000, 7000, 1500, 1200)
    assert (m.planner_blocks, m.rx_buffer_bytes) == (15, 256)
    assert "parse_ms" in m.unverified() and "accel_x" not in m.unverified()
    assert all("UNVERIFIED" not in d for d in m.describe()[:5])


def test_unverified_defaults_are_labelled():
    rep = G.report_lines(G.simulate(_stroke([(0, 0), (10, 0)]), G.MachineProfile(), SD))
    assert sum("UNVERIFIED" in r for r in rep) >= 5


def test_lint_flags_dense_curve_not_straight_line():
    curve = _stroke(_circle(50, 50, 3, 40), feed=5000)
    line = _stroke([(0, 0), (150, 0)], feed=5000)
    found = G.lint_motion(G.simulate(curve + line, G.MachineProfile(), SD))
    assert [f.stroke for f in found] == [0]


# --- software M4 -----------------------------------------------------------


def test_scale_power_to_speed_bounds():
    g = _stroke(_circle(50, 50, 3, 40), feed=5000)
    out = G.scale_power_to_speed(g, G.MachineProfile(), SD, floor_frac=0.3)
    s_vals = [int(ln.split("S")[-1]) for ln in out.splitlines() if ln.startswith("G1")]
    assert len(s_vals) == 40
    assert all(300 <= s <= 1000 for s in s_vals)
    assert min(s_vals) < 1000  # the slow curve did get scaled down
    # geometry untouched
    strip = lambda t: [ln.split(" S")[0] for ln in t.splitlines()]  # noqa: E731
    assert strip(out) == strip(g)


def test_scale_power_leaves_cruise_at_full_power():
    out = G.scale_power_to_speed(
        _stroke([(0, 0), (50, 0), (150, 0), (200, 0)], feed=3000), G.MachineProfile(), SD)
    s_vals = [int(ln.split("S")[-1]) for ln in out.splitlines() if ln.startswith("G1")]
    assert s_vals[1] == 1000  # the 100mm middle move cruises at F


# --- arc fitting -----------------------------------------------------------


def test_fit_arcs_circle_polygon():
    pts = _circle(50, 50, 5, 72)
    prims = arcfit.fit_arcs(pts, 0.02)
    assert len(prims) <= 3
    dense = LineString(arcfit.primitives_to_points(pts[0], prims, 0.02))
    assert max(dense.distance(Point(p)) for p in pts) <= 0.02 + 1e-6
    assert arcfit.primitives_to_points(pts[0], prims)[-1] == pytest.approx(pts[-1])


def test_fit_arcs_keeps_rectangle_straight():
    # every corner of a rectangle lies on its circumscribed circle: the arc
    # fitter must still not replace the straight sides with an arc
    rect = [(0, 0), (0.4, 0), (0.4, 12), (0, 12), (0, 0)]
    prims = arcfit.fit_arcs(rect, 0.05)
    assert all(p[0] == "G1" for p in prims)


def test_fit_arcs_s_curve_within_tolerance():
    a = [(10 * math.cos(t), 10 * math.sin(t)) for t in [math.pi * i / 40 for i in range(41)]]
    # second half-turn curves the other way round a centre at (-20, 0)
    b = [(-20 + 10 * math.cos(t), -10 * math.sin(t))
         for t in [math.pi * i / 40 for i in range(1, 41)]]
    pts = a + b
    prims = arcfit.fit_arcs(pts, 0.02)
    assert sum(p[0] != "G1" for p in prims) >= 2
    assert len(prims) < len(pts) / 10
    dense = LineString(arcfit.primitives_to_points(pts[0], prims, 0.02))
    assert max(dense.distance(Point(p)) for p in pts) <= 0.02 + 1e-6
    # GRBL checks start/end radius agreement (error 33): I/J centre must be
    # equidistant from both ends to within 0.005mm
    x, y = pts[0]
    for p in prims:
        if p[0] != "G1":
            cx, cy = x + p[2][0], y + p[2][1]
            assert abs(math.hypot(x - cx, y - cy) - math.hypot(p[1][0] - cx, p[1][1] - cy)) < 0.005
        x, y = p[1]


def test_arcs_raise_predicted_speed_on_curves():
    pts = _circle(50, 50, 4, 30)
    poly = _stroke(pts, feed=5000)
    arcs_g = ("G0 X%.3f Y%.3f\nM3 S1000\nF5000\n" % pts[0]
              + "\n".join(arcfit.prims_to_gcode(arcfit.fit_arcs(pts, 0.05))) + "\nM5\n")
    m = G.MachineProfile()
    fp = G.stroke_stats(G.simulate(poly, m, SD))[0].energy_factor
    fa = G.stroke_stats(G.simulate(arcs_g, m, SD))[0].energy_factor
    assert fa < fp


# --- emitter hooks -----------------------------------------------------------


def _etch(**kw):
    cfg = SimpleNamespace(px_per_mm=1.0, margin_px=0.0, origin_offset_mm=(0.0, 0.0),
                          puzzle_h_px=100.0)
    loop = [(50 + 5 * math.cos(2 * math.pi * i / 60), 100 - 50 - 5 * math.sin(2 * math.pi * i / 60))
            for i in range(61)]
    return emit_etch_gcode([loop], load_material("plywood_veneer_3ply_3mm"), cfg, "t",
                           simplify_mm=0.0, **kw)


def test_etch_default_is_polyline_with_motion_report():
    g = _etch()
    assert not any(ln.startswith(("G2 ", "G3 ")) for ln in g.splitlines())
    head = g.split(";HEAD: laser")[0]
    assert "GRBL motion model" in head and "UNVERIFIED" in head


def test_etch_arc_option_emits_arcs_and_renders():
    g = _etch(arc_tolerance_mm=0.05)
    assert any(ln.startswith(("G2 ", "G3 ")) for ln in g.splitlines())
    from jigsaw import _parse_gcode_paths

    (path,) = _parse_gcode_paths(g)
    # the rendered path follows the r=5 circle, not the arcs' chords
    assert all(abs(math.hypot(x - 50, y - 50) - 5) < 0.1 for x, y in path)


# --- smoothing (spline + tangent biarcs) --------------------------------------


def _noisy_loop(r=4.0, n=60, amp=0.04):
    """A traced-looking circle: staircase-ish noise of +-amp on the radius."""
    pts = [(50 + (r + amp * (-1) ** i) * math.cos(2 * math.pi * i / n),
            50 + (r + amp * (-1) ** i) * math.sin(2 * math.pi * i / n)) for i in range(n)]
    return pts + [pts[0]]


def _join_angles(start, prims):
    out, P, Tprev = [], start, None
    for p in prims:
        dense = arcfit.primitives_to_points(P, [p], 0.005)
        dx, dy = dense[1][0] - P[0], dense[1][1] - P[1]
        n = math.hypot(dx, dy)
        if Tprev:
            out.append(math.degrees(math.acos(max(-1, min(1, (dx * Tprev[0] + dy * Tprev[1]) / n)))))
        Tprev = arcfit._end_tangent(P, p)
        P = p[1]
    return out


def test_smooth_polyline_stays_within_budget_and_closed():
    loop = _noisy_loop()
    sm = arcfit.smooth_polyline(loop, 0.08)
    assert sm[0] == sm[-1]
    assert LineString(sm).hausdorff_distance(LineString(loop)) <= 0.08 + 1e-9


def test_smooth_arcs_are_tangent_and_within_total_budget():
    loop = _noisy_loop()
    sm = arcfit.smooth_polyline(loop, 0.08)
    prims = arcfit.fit_smooth_arcs(sm, 0.02)
    path = LineString(arcfit.primitives_to_points(sm[0], prims, 0.02))
    assert path.hausdorff_distance(LineString(loop)) <= 0.10 + 1e-6
    assert max(_join_angles(sm[0], prims)) < 3.0
    assert len(prims) < len(loop)  # fewer commands than the traced polyline


def test_smooth_keeps_a_real_corner():
    # a square's corners can't be rounded smooth within 0.08mm: the fit must
    # still follow them (within budget), not cut across
    sq = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
    sm = arcfit.smooth_polyline(sq, 0.08)
    prims = arcfit.fit_smooth_arcs(sm, 0.02)
    path = LineString(arcfit.primitives_to_points(sm[0], prims, 0.02))
    assert path.hausdorff_distance(LineString(sq)) <= 0.10 + 1e-6


def test_smoothing_speeds_up_noisy_curve():
    loop = _noisy_loop(n=90)
    poly = _stroke(loop, feed=5000)
    sm = arcfit.smooth_polyline(loop, 0.08)
    g = ("G0 X%.3f Y%.3f\nM3 S1000\nF5000\n" % sm[0]
         + "\n".join(arcfit.prims_to_gcode(arcfit.fit_smooth_arcs(sm, 0.02))) + "\nM5\n")
    m = G.MachineProfile()
    fp = G.stroke_stats(G.simulate(poly, m, SD))[0].energy_factor
    fs = G.stroke_stats(G.simulate(g, m, SD))[0].energy_factor
    assert fs < fp


def test_etch_smooth_option():
    g = _etch(smooth_mm=0.10)
    assert "strokes SMOOTHED" in g
    assert any(ln.startswith(("G2 ", "G3 ")) for ln in g.splitlines())
    from jigsaw import _parse_gcode_paths

    (path,) = _parse_gcode_paths(g)
    assert all(abs(math.hypot(x - 50, y - 50) - 5) <= 0.10 + 1e-3 for x, y in path)
