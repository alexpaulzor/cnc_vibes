"""Tests for scripts/cam.py — the parametric 2.5D CAM library.

Covers profile_cut for now; pocket_mill / drill_array / engrave_text
land in their own commits.

Test categories:
  - Profile loading (Tool, Material) round-trip
  - CamConfig defaults are sensible
  - profile_cut emits validator-clean GCode shape
  - Warnings fire for default-tool / op-tool mismatch cases
  - Strict mode escalates warnings to SystemExit
  - Plunge feed respects tool.max_plunge_mm_per_min
"""

import re
import sys
from pathlib import Path

import pytest
from shapely.geometry import Polygon

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from cam import (  # noqa: E402
    CamConfig,
    GcodeOutput,
    Material,
    Tool,
    _check_tool_for_op,
    _derive_feed,
    _derive_step_down,
    _plunge_feed,
    load_material,
    load_tool,
    profile_cut,
)


# ---------------------------------------------------------------------------
# Profile loading
# ---------------------------------------------------------------------------


def test_load_tool_known_id():
    t = load_tool("flat_3.175mm_2flute")
    assert t.id == "flat_3.175mm_2flute"
    assert t.type == "flat_endmill"
    assert t.diameter_mm == pytest.approx(3.175)
    assert t.flutes == 2
    assert t.max_plunge_mm_per_min == 300


def test_load_tool_unknown_exits():
    with pytest.raises(SystemExit, match="unknown tool"):
        load_tool("no_such_tool")


def test_load_material_known_id():
    m = load_material("plywood_baltic_birch_3mm")
    assert m.id == "plywood_baltic_birch_3mm"
    assert m.family == "wood"
    assert m.chipload_for("flat_3.175mm_2flute") == pytest.approx(0.04)


def test_load_material_unknown_exits():
    with pytest.raises(SystemExit, match="unknown material"):
        load_material("no_such_material")


# ---------------------------------------------------------------------------
# Feed / DOC derivation
# ---------------------------------------------------------------------------


def test_derive_feed_uses_chipload():
    t = load_tool("flat_3.175mm_2flute")
    m = load_material("plywood_baltic_birch_3mm")
    cfg = CamConfig(spindle_rpm=18000)
    # chipload 0.04 * flutes 2 * rpm 18000 = 1440
    assert _derive_feed(t, m, cfg) == 1440


def test_derive_feed_falls_back_when_no_chipload():
    t = Tool(id="custom", type="flat_endmill", diameter_mm=3, flutes=2)
    m = Material(id="x", family="wood", thickness_mm=3, chipload={})
    cfg = CamConfig()
    # Fallback constant (currently 600 mm/min)
    assert _derive_feed(t, m, cfg) == 600


def test_plunge_feed_capped_by_tool_max():
    t = load_tool("flat_3.175mm_2flute")  # max_plunge=300
    cfg = CamConfig(plunge_factor=0.5)
    # 50% of 1440 = 720, but tool caps at 300
    assert _plunge_feed(1440, t, cfg) == 300


def test_plunge_feed_floor_at_50():
    t = Tool(id="x", type="flat_endmill", diameter_mm=3, max_plunge_mm_per_min=400)
    cfg = CamConfig(plunge_factor=0.001)  # would yield ~0
    assert _plunge_feed(1000, t, cfg) == 50  # min floor


def test_derive_step_down_from_doc_fraction():
    t = load_tool("flat_3.175mm_2flute")  # diameter 3.175
    m = load_material("plywood_baltic_birch_3mm")  # doc_fraction 0.5
    cfg = CamConfig()
    assert _derive_step_down(t, m, cfg) == pytest.approx(0.5 * 3.175)


def test_derive_step_down_explicit_override():
    t = load_tool("flat_3.175mm_2flute")
    m = load_material("plywood_baltic_birch_3mm")
    cfg = CamConfig(step_down_mm=1.0)
    assert _derive_step_down(t, m, cfg) == 1.0


# ---------------------------------------------------------------------------
# profile_cut GCode shape
# ---------------------------------------------------------------------------


def _square(side=40, x=20, y=20):
    return Polygon([(x, y), (x + side, y), (x + side, y + side), (x, y + side)])


def test_profile_cut_validator_headers():
    out = profile_cut(_square(), depth_mm=3.0)
    text = out.text
    assert ";HEAD: spindle" in text
    assert ";MATERIAL: plywood_baltic_birch_3mm" in text
    assert ";TOOL: flat_3.175mm_2flute" in text
    assert "$32=0" in text  # spindle mode, not laser


def test_profile_cut_uses_m3_not_m4():
    out = profile_cut(_square(), depth_mm=3.0)
    assert re.search(r"^M3 S\d+", out.text, re.MULTILINE)
    assert not re.search(r"^M4\b", out.text, re.MULTILINE)


def test_profile_cut_ends_at_safe_z_with_m5():
    out = profile_cut(_square(), depth_mm=3.0, cfg=CamConfig(safe_z_mm=5))
    # Footer should have a final raise to safe Z and M5
    text = out.text
    assert "G0 Z5.000" in text
    assert "\nM5\n" in text


def test_profile_cut_outside_increases_polygon():
    """side='outside' offsets the polygon OUTWARD by tool radius — the
    first move in the toolpath should be outside the input square."""
    sq = _square(side=40, x=20, y=20)  # 20..60 in both axes
    out = profile_cut(sq, depth_mm=3.0, side="outside")
    # Find the first G0 X<x> Y<y> line in the body (skip header parking)
    body = out.text
    # Tool radius = 3.175/2 = 1.5875; outside path's bounding box should
    # extend outside [20, 60] in X and Y
    coords = re.findall(r"G[01] X([-\d.]+) Y([-\d.]+)", body)
    xs = [float(x) for x, _ in coords]
    ys = [float(y) for _, y in coords]
    assert min(xs) < 20  # outside the original square
    assert max(xs) > 60


def test_profile_cut_inside_decreases_polygon():
    sq = _square(side=40, x=20, y=20)
    out = profile_cut(sq, depth_mm=3.0, side="inside")
    coords = re.findall(r"G[01] X([-\d.]+) Y([-\d.]+)", out.text)
    xs = [float(x) for x, _ in coords if 0 < float(x) < 100]
    # All path X-coords (excluding return-home G0 X0 Y0) should be inside [20, 60]
    cut_xs = [x for x in xs if x != 0]
    assert all(20 < x < 60 for x in cut_xs)


def test_profile_cut_multi_pass_when_depth_exceeds_step_down():
    """3mm depth with default plywood_3mm step_down (0.5 * 3.175 = 1.5875)
    needs 2 passes."""
    out = profile_cut(_square(), depth_mm=3.0)
    # Count "pass N of M" comments
    n_passes = out.text.count("of 2")
    assert n_passes >= 1


def test_profile_cut_handles_empty_offset():
    """A polygon smaller than 2*tool_radius can't be inside-offset — the
    op should warn (or fail in strict) instead of crashing."""
    tiny = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])  # 1x1 mm
    t = load_tool("flat_6mm_2flute")
    out = profile_cut(tiny, depth_mm=1, tool=t, side="inside")
    assert len(out.warnings) >= 1
    assert any("empty toolpath" in w for w in out.warnings)


# ---------------------------------------------------------------------------
# Warnings (default tool, op-tool mismatch) and strict-mode escalation
# ---------------------------------------------------------------------------


def test_default_tool_warning_fires():
    out = profile_cut(_square(), depth_mm=3.0)  # no tool= → default
    assert any("default tool" in w for w in out.warnings)


def test_explicit_tool_suppresses_default_warning():
    t = load_tool("flat_3.175mm_2flute")  # same as default, but passed explicitly
    out = profile_cut(_square(), depth_mm=3.0, tool=t)
    assert not any("default tool" in w for w in out.warnings)


def test_ball_endmill_warning_for_profile_cut():
    t = load_tool("ball_3mm_2flute")
    out = profile_cut(_square(), depth_mm=3.0, tool=t)
    assert any("ball_endmill" in w for w in out.warnings)


def test_v_bit_warning_for_profile_cut():
    t = load_tool("vbit_60deg_6mm")
    out = profile_cut(_square(), depth_mm=3.0, tool=t)
    assert any("v_bit" in w for w in out.warnings)


def test_depth_exceeds_flute_length_warning():
    t = load_tool("flat_3.175mm_2flute")  # flute_length_mm = 17
    out = profile_cut(_square(), depth_mm=20.0, tool=t)
    assert any("flute length" in w for w in out.warnings)


def test_strict_mode_escalates_default_tool_warning():
    cfg = CamConfig(strict=True)
    with pytest.raises(SystemExit, match="default tool"):
        profile_cut(_square(), depth_mm=3.0, cfg=cfg)


def test_strict_mode_escalates_op_tool_mismatch():
    cfg = CamConfig(strict=True)
    t = load_tool("ball_3mm_2flute")
    with pytest.raises(SystemExit, match="ball_endmill"):
        profile_cut(_square(), depth_mm=3.0, tool=t, cfg=cfg)


def test_missing_chipload_warning():
    """Material with no chipload entry for the chosen tool fires a warning."""
    t = load_tool("vbit_60deg_6mm")  # no chipload for v_bit in plywood_3mm
    out = profile_cut(_square(), depth_mm=2.0, tool=t)
    assert any("chipload" in w for w in out.warnings)


# ---------------------------------------------------------------------------
# pocket_mill
# ---------------------------------------------------------------------------


from cam import _offset_rings, pocket_mill  # noqa: E402


def test_offset_rings_returns_at_least_one_ring_for_normal_pocket():
    sq = Polygon([(0, 0), (50, 0), (50, 50), (0, 50)])
    rings = _offset_rings(sq, tool_radius_mm=1.5, stepover_factor=0.5)
    assert len(rings) >= 1
    # Outermost ring is inset by tool_radius from polygon — its bounds
    # should be tighter than the input by ~tool_radius on each side
    x0, y0, x1, y1 = rings[0].bounds
    assert x0 >= 1.0 and x1 <= 49.0
    assert y0 >= 1.0 and y1 <= 49.0


def test_offset_rings_empty_when_pocket_smaller_than_tool():
    tiny = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])  # 1mm square
    rings = _offset_rings(tiny, tool_radius_mm=3, stepover_factor=0.5)
    assert rings == []


def test_offset_rings_advances_inward():
    """Successive rings should be smaller (in area) than the previous."""
    sq = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
    rings = _offset_rings(sq, tool_radius_mm=2, stepover_factor=0.5)
    assert len(rings) >= 3
    for i in range(len(rings) - 1):
        assert rings[i + 1].area < rings[i].area


def test_pocket_mill_validator_headers():
    out = pocket_mill(_square(), depth_mm=2.0)
    text = out.text
    assert ";HEAD: spindle" in text
    assert ";MATERIAL: plywood_baltic_birch_3mm" in text
    assert ";TOOL: flat_3.175mm_2flute" in text


def test_pocket_mill_uses_m3():
    out = pocket_mill(_square(), depth_mm=2.0)
    assert re.search(r"^M3 S\d+", out.text, re.MULTILINE)
    assert not re.search(r"^M4\b", out.text, re.MULTILINE)


def test_pocket_mill_multi_pass_when_depth_exceeds_step_down():
    out = pocket_mill(_square(), depth_mm=4.0)
    text = out.text
    # step_down for plywood_3mm + 1/8" tool = 0.5 * 3.175 = 1.5875
    # 4mm / 1.5875 = 3 passes
    assert "Z pass 1/3" in text
    assert "Z pass 3/3" in text


def test_pocket_mill_traverses_rings_per_z_pass():
    out = pocket_mill(_square(80, 0, 0), depth_mm=2.0)
    # Each Z pass walks every ring; ring comments include "ring N/M"
    text = out.text
    assert re.search(r"; ring 1/\d+", text)


def test_pocket_mill_empty_when_pocket_too_small_for_tool():
    tiny = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])  # 1mm square
    t = load_tool("flat_6mm_2flute")  # 6mm tool, can't fit
    out = pocket_mill(tiny, depth_mm=1.0, tool=t)
    assert len(out.warnings) >= 1
    assert any("smaller than 2x tool" in w for w in out.warnings)
    assert out.lines == []  # no GCode emitted


def test_pocket_mill_warns_on_deep_pocket_with_flat_endmill():
    """Pocket depth > 3x tool diameter triggers chip-evacuation warning."""
    t = load_tool("flat_3.175mm_2flute")  # 3.175mm diameter
    out = pocket_mill(_square(), depth_mm=12.0, tool=t)  # 12 > 3*3.175
    assert any("chip evacuation" in w for w in out.warnings)


def test_pocket_mill_warns_on_default_tool():
    out = pocket_mill(_square(), depth_mm=2.0)  # no tool=
    assert any("default tool" in w for w in out.warnings)


def test_pocket_mill_strict_mode_escalates_default_tool():
    cfg = CamConfig(strict=True)
    with pytest.raises(SystemExit, match="default tool"):
        pocket_mill(_square(), depth_mm=2.0, cfg=cfg)


def test_pocket_mill_coords_within_pocket_envelope():
    """All G0/G1 inside the pocket should stay within the original polygon's
    bounds (with tool_radius slack)."""
    sq = _square(side=40, x=20, y=20)  # 20..60
    out = pocket_mill(sq, depth_mm=2.0, tool=load_tool("flat_3.175mm_2flute"))
    for m in re.finditer(
        r"^G[01]\s+X([-\d.]+)\s+Y([-\d.]+)", out.text, re.MULTILINE
    ):
        x, y = float(m.group(1)), float(m.group(2))
        if x == 0 and y == 0:
            continue  # park move
        assert 20 <= x <= 60, f"X={x} outside pocket"
        assert 20 <= y <= 60, f"Y={y} outside pocket"


def test_pocket_mill_stepover_factor_validation():
    with pytest.raises(SystemExit, match="stepover_factor"):
        _offset_rings(_square(), tool_radius_mm=1.5, stepover_factor=0)
    with pytest.raises(SystemExit, match="stepover_factor"):
        _offset_rings(_square(), tool_radius_mm=1.5, stepover_factor=1.0)


# ---------------------------------------------------------------------------
# drill_array
# ---------------------------------------------------------------------------


from cam import drill_array  # noqa: E402


def _holes_2x2():
    return [(10, 10), (40, 10), (10, 40), (40, 40)]


def test_drill_array_validator_headers():
    t = load_tool("drill_3.2mm_m4_clearance")
    m = load_material("plywood_baltic_birch_6mm")
    out = drill_array(_holes_2x2(), depth_mm=6, tool=t, material=m)
    text = out.text
    assert ";HEAD: spindle" in text
    assert ";TOOL: drill_3.2mm_m4_clearance" in text


def test_drill_array_uses_m3():
    t = load_tool("drill_3.2mm_m4_clearance")
    m = load_material("plywood_baltic_birch_6mm")
    out = drill_array(_holes_2x2(), depth_mm=6, tool=t, material=m)
    assert re.search(r"^M3 S\d+", out.text, re.MULTILINE)
    assert not re.search(r"^M4\b", out.text, re.MULTILINE)


def test_drill_array_one_section_per_hole():
    t = load_tool("drill_3.2mm_m4_clearance")
    m = load_material("plywood_baltic_birch_6mm")
    out = drill_array(_holes_2x2(), depth_mm=6, tool=t, material=m)
    # One "; --- hole N/M ---" comment per hole
    n_holes = sum(1 for l in out.lines if l.startswith("; --- hole"))
    assert n_holes == 4


def test_drill_array_empty_list_warns_and_emits_nothing():
    out = drill_array([], depth_mm=6, tool=load_tool("drill_3.2mm_m4_clearance"))
    assert any("empty holes list" in w for w in out.warnings)
    assert out.lines == []


def test_drill_array_single_plunge_when_no_peck():
    t = load_tool("drill_3.2mm_m4_clearance")
    out = drill_array([(10, 10)], depth_mm=6, tool=t, peck_depth_mm=None)
    # Exactly one G1 Z<negative> per hole (no peck cycle)
    g1_z_negative = [l for l in out.lines if re.match(r"^G1 Z-[\d.]+", l)]
    assert len(g1_z_negative) == 1


def test_drill_array_peck_cycle_emits_multiple_plunges():
    t = load_tool("drill_3.2mm_m4_clearance")
    out = drill_array([(10, 10)], depth_mm=6, tool=t, peck_depth_mm=2.0)
    # 6mm / 2mm = 3 pecks
    peck_comments = [l for l in out.lines if l.startswith("; peck")]
    assert len(peck_comments) == 3


def test_drill_array_warns_when_using_flat_endmill():
    """type=flat_endmill on drill_array → wrong tool warning (allowed in
    wood but flagged)."""
    t = load_tool("flat_3.175mm_2flute")
    out = drill_array(_holes_2x2(), depth_mm=3, tool=t)
    assert any("flat_endmill" in w for w in out.warnings)


def test_drill_array_warns_when_using_v_bit():
    t = load_tool("vbit_60deg_6mm")
    out = drill_array(_holes_2x2(), depth_mm=3, tool=t)
    assert any("v_bit" in w for w in out.warnings)


def test_drill_array_warns_when_using_ball_endmill():
    t = load_tool("ball_3mm_2flute")
    out = drill_array(_holes_2x2(), depth_mm=3, tool=t)
    assert any("ball_endmill" in w for w in out.warnings)


def test_drill_array_strict_mode_escalates_wrong_tool():
    t = load_tool("ball_3mm_2flute")
    cfg = CamConfig(strict=True)
    with pytest.raises(SystemExit, match="ball_endmill"):
        drill_array(_holes_2x2(), depth_mm=3, tool=t, cfg=cfg)


def test_drill_array_warns_when_default_tool_used():
    out = drill_array(_holes_2x2(), depth_mm=3)  # no tool=
    assert any("default tool" in w for w in out.warnings)


def test_drill_array_depth_exceeds_flute_length_warning():
    t = load_tool("drill_3.2mm_m4_clearance")  # flute_length=30
    out = drill_array(_holes_2x2(), depth_mm=40, tool=t)
    assert any("flute length" in w for w in out.warnings)


def test_drill_array_all_holes_within_machine_envelope():
    """G0 + G1 coords visit each hole position exactly."""
    holes = [(15.5, 25.5), (50.0, 75.0)]
    t = load_tool("drill_3.2mm_m4_clearance")
    out = drill_array(holes, depth_mm=3, tool=t)
    xy_pairs = re.findall(r"^G[01]\s+X([-\d.]+)\s+Y([-\d.]+)", out.text, re.MULTILINE)
    visited = {(float(x), float(y)) for x, y in xy_pairs}
    for h in holes:
        assert h in visited
