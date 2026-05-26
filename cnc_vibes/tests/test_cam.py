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
