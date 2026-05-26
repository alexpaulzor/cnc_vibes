"""Tests for scripts/openscad_loader.py.

The SVG-parsing tests use hand-crafted SVG fixtures so they run anywhere.
The .scad → .svg pipeline tests skip if OpenSCAD isn't installed (so the
suite stays green on CI without OpenSCAD).
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from openscad_loader import (  # noqa: E402
    _find_openscad,
    _rings_to_polygons,
    openscad_to_polygons,
    scad_to_svg,
    svg_to_polygons,
)


# ---------------------------------------------------------------------------
# _find_openscad
# ---------------------------------------------------------------------------


def test_find_openscad_respects_env_var(tmp_path, monkeypatch):
    fake = tmp_path / "fake_openscad"
    fake.write_text("#!/bin/sh\necho fake\n")
    fake.chmod(0o755)
    monkeypatch.setenv("OPENSCAD", str(fake))
    assert _find_openscad() == str(fake)


def test_find_openscad_raises_when_nothing_found(monkeypatch):
    monkeypatch.setenv("OPENSCAD", "/nonexistent/path")
    monkeypatch.setenv("PATH", "/nonexistent")
    # Patch the fallback list to nothing exists
    import openscad_loader

    monkeypatch.setattr(openscad_loader, "_OPENSCAD_FALLBACKS", ["/nope"])
    with pytest.raises(SystemExit, match="openscad not found"):
        _find_openscad()


# ---------------------------------------------------------------------------
# svg_to_polygons (hand-crafted fixtures)
# ---------------------------------------------------------------------------


_OPENSCAD_SQUARE_SVG = """<?xml version="1.0" standalone="no"?>
<svg width="60mm" height="40mm" viewBox="0 -40 60 40" xmlns="http://www.w3.org/2000/svg">
<path d="M 0,-0 L 60,-0 L 60,-40 L 0,-40 z"/>
</svg>"""


def test_svg_simple_square_loads(tmp_path):
    svg = tmp_path / "square.svg"
    svg.write_text(_OPENSCAD_SQUARE_SVG)
    polys = svg_to_polygons(svg)
    assert len(polys) == 1
    bx0, by0, bx1, by1 = polys[0].bounds
    assert bx0 == pytest.approx(0)
    assert bx1 == pytest.approx(60, abs=0.01)
    assert by1 == pytest.approx(40, abs=0.01)
    assert polys[0].area == pytest.approx(60 * 40, abs=0.5)


_OPENSCAD_SQUARE_WITH_HOLE_SVG = """<?xml version="1.0" standalone="no"?>
<svg width="40mm" height="40mm" viewBox="0 -40 40 40" xmlns="http://www.w3.org/2000/svg">
<path d="M 0,-0 L 40,-0 L 40,-40 L 0,-40 z
         M 10,-15 L 30,-15 L 30,-25 L 10,-25 z"/>
</svg>"""


def test_svg_square_with_hole_detects_inner_as_hole(tmp_path):
    svg = tmp_path / "sqh.svg"
    svg.write_text(_OPENSCAD_SQUARE_WITH_HOLE_SVG)
    polys = svg_to_polygons(svg)
    assert len(polys) == 1
    p = polys[0]
    assert len(p.interiors) == 1
    # Total area = 40*40 - 20*10 = 1600 - 200 = 1400
    assert p.area == pytest.approx(1400, abs=0.5)


def test_svg_missing_file_raises(tmp_path):
    with pytest.raises(SystemExit, match="svg file not found"):
        svg_to_polygons(tmp_path / "nope.svg")


# ---------------------------------------------------------------------------
# _rings_to_polygons (the containment-classifier — pure-function unit)
# ---------------------------------------------------------------------------


def test_rings_outer_only():
    # Single outer ring
    rings = [[(0, 0), (10, 0), (10, 10), (0, 10)]]
    polys = _rings_to_polygons(rings)
    assert len(polys) == 1
    assert len(polys[0].interiors) == 0


def test_rings_outer_plus_hole():
    rings = [
        [(0, 0), (10, 0), (10, 10), (0, 10)],  # 10x10 outer
        [(3, 3), (7, 3), (7, 7), (3, 7)],  # 4x4 hole
    ]
    polys = _rings_to_polygons(rings)
    assert len(polys) == 1
    assert len(polys[0].interiors) == 1


def test_rings_nested_outer_in_hole():
    """Three-deep nesting: outer + hole + inner outer (sits inside the hole).
    Depth 0 → outer; depth 1 → hole; depth 2 → outer-again."""
    rings = [
        [(0, 0), (20, 0), (20, 20), (0, 20)],  # outer
        [(4, 4), (16, 4), (16, 16), (4, 16)],  # hole
        [(8, 8), (12, 8), (12, 12), (8, 12)],  # inner outer
    ]
    polys = _rings_to_polygons(rings)
    assert len(polys) == 2  # outer + inner-outer; the hole is attached to outer


def test_rings_two_independent_outer_rings():
    rings = [
        [(0, 0), (10, 0), (10, 10), (0, 10)],
        [(20, 0), (30, 0), (30, 10), (20, 10)],
    ]
    polys = _rings_to_polygons(rings)
    assert len(polys) == 2


def test_rings_empty_input_returns_empty():
    assert _rings_to_polygons([]) == []


# ---------------------------------------------------------------------------
# .scad → polygons (skip when OpenSCAD isn't available)
# ---------------------------------------------------------------------------


def _has_openscad() -> bool:
    if os.environ.get("OPENSCAD") and Path(os.environ["OPENSCAD"]).exists():
        return True
    from shutil import which

    if which("openscad"):
        return True
    for path in (
        "/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD",
        "/usr/bin/openscad",
        "/usr/local/bin/openscad",
    ):
        if Path(path).exists():
            return True
    return False


requires_openscad = pytest.mark.skipif(
    not _has_openscad(), reason="openscad binary not available"
)


@requires_openscad
def test_scad_to_svg_produces_file(tmp_path):
    scad = tmp_path / "x.scad"
    scad.write_text("square([20, 15]);")
    svg = scad_to_svg(scad, tmp_path / "x.svg")
    assert svg.exists()
    text = svg.read_text()
    assert "<svg" in text and "<path" in text


@requires_openscad
def test_scad_simple_square_bounds():
    scad_text = "square([60, 40]);"
    with tempfile.NamedTemporaryFile(suffix=".scad", delete=False, mode="w") as tf:
        tf.write(scad_text)
        scad_path = Path(tf.name)
    try:
        polys = openscad_to_polygons(scad_path)
        assert len(polys) == 1
        bx0, by0, bx1, by1 = polys[0].bounds
        assert bx0 == pytest.approx(0, abs=0.01)
        assert bx1 == pytest.approx(60, abs=0.01)
        assert by1 == pytest.approx(40, abs=0.01)
    finally:
        scad_path.unlink()


@requires_openscad
def test_scad_difference_produces_holes():
    """square minus circles → one polygon with N holes."""
    scad_text = """
    difference() {
        square([50, 50]);
        translate([15, 15]) circle(r=4, $fn=32);
        translate([35, 35]) circle(r=4, $fn=32);
    }
    """
    with tempfile.NamedTemporaryFile(suffix=".scad", delete=False, mode="w") as tf:
        tf.write(scad_text)
        scad_path = Path(tf.name)
    try:
        polys = openscad_to_polygons(scad_path)
        assert len(polys) == 1
        assert len(polys[0].interiors) == 2
    finally:
        scad_path.unlink()


@requires_openscad
def test_scad_missing_input_raises(tmp_path):
    with pytest.raises(SystemExit, match="not found"):
        scad_to_svg(tmp_path / "nope.scad")


@requires_openscad
def test_unknown_extension_rejected(tmp_path):
    bad = tmp_path / "x.dxf"
    bad.write_text("")
    with pytest.raises(SystemExit, match=".scad or .svg"):
        openscad_to_polygons(bad)
