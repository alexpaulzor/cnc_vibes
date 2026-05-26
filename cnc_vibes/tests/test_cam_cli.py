"""Smoke + unit tests for scripts/cam_cli.py."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import cam_cli  # noqa: E402


# ---------------------------------------------------------------------------
# Shape factory unit tests
# ---------------------------------------------------------------------------


def _ns(**kw):
    import argparse

    return argparse.Namespace(**kw)


def _shape_ns(shape, **fields):
    defaults = dict(
        shape=shape,
        width=None,
        height=None,
        radius=None,
        diameter=None,
        points=None,
        svg_file=None,
        scad_file=None,
        center=None,
    )
    defaults.update(fields)
    return _ns(**defaults)


def test_shape_rect_centered():
    geom = cam_cli.build_shape(_shape_ns("rect", width=60, height=40))
    assert geom.area == pytest.approx(60 * 40)
    minx, miny, maxx, maxy = geom.bounds
    assert (minx, miny, maxx, maxy) == pytest.approx((-30, -20, 30, 20))


def test_shape_rrect_radius_check():
    # Radius larger than half min dim should fail
    with pytest.raises(SystemExit):
        cam_cli.build_shape(_shape_ns("rrect", width=20, height=10, radius=20))


def test_shape_rrect_basic():
    geom = cam_cli.build_shape(_shape_ns("rrect", width=60, height=40, radius=5))
    # Area should be close to rect area minus 4 corner squares + 4 quarter circles
    rect = 60 * 40
    corners_removed = 4 * (5 * 5) - 4 * (3.14159 * 25 / 4)
    expected = rect - corners_removed
    assert geom.area == pytest.approx(expected, rel=0.01)


def test_shape_circle():
    geom = cam_cli.build_shape(_shape_ns("circle", diameter=20))
    assert geom.area == pytest.approx(3.14159 * 100, rel=0.01)


def test_shape_polygon_needs_three_points():
    with pytest.raises(SystemExit):
        cam_cli.build_shape(_shape_ns("polygon", points="0,0 10,0"))


def test_shape_center_shift():
    geom = cam_cli.build_shape(_shape_ns("rect", width=20, height=20, center="5,5"))
    minx, miny, _, _ = geom.bounds
    assert (minx, miny) == pytest.approx((-5, -5))


# ---------------------------------------------------------------------------
# Hole pattern factory
# ---------------------------------------------------------------------------


def _hole_ns(pattern, **fields):
    defaults = dict(
        pattern=pattern,
        cols=None,
        rows=None,
        spacing=None,
        count=None,
        radius=None,
        angle=None,
        points=None,
        origin=None,
    )
    defaults.update(fields)
    return _ns(**defaults)


def test_holes_grid_centered():
    pts = cam_cli.build_holes(_hole_ns("grid", cols=3, rows=3, spacing=10))
    assert len(pts) == 9
    # Center hole should be at origin
    assert (0.0, 0.0) in [(round(x, 6), round(y, 6)) for x, y in pts]


def test_holes_bolt_circle_count():
    pts = cam_cli.build_holes(_hole_ns("bolt-circle", count=6, radius=10))
    assert len(pts) == 6
    # All should be on the circle of radius 10
    import math

    for x, y in pts:
        assert math.hypot(x, y) == pytest.approx(10, rel=1e-6)


def test_holes_linear_horizontal():
    pts = cam_cli.build_holes(_hole_ns("linear", count=4, spacing=5, angle=0))
    assert len(pts) == 4
    ys = [round(y, 6) for _, y in pts]
    assert all(y == 0 for y in ys)


def test_holes_explicit():
    pts = cam_cli.build_holes(
        _hole_ns("explicit", points="0,0 10,5 -3,2", origin="1,1")
    )
    assert pts == [(1.0, 1.0), (11.0, 6.0), (-2.0, 3.0)]


# ---------------------------------------------------------------------------
# End-to-end: every (op, head) combo emits validator-clean gcode
# ---------------------------------------------------------------------------


def _run_cli(args: list[str], tmp_path: Path) -> Path:
    out = tmp_path / "out.gcode"
    rc = cam_cli.main(args + ["--out", str(out), "--no-validate"])
    assert rc == 0, f"cam_cli.main returned {rc}"
    assert out.exists()
    return out


def _validate(path: Path) -> None:
    r = subprocess.run(
        [sys.executable, str(REPO_ROOT / "cnc.py"), "validate", str(path)],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, f"validator failed:\n{r.stdout}\n{r.stderr}"


SPINDLE = ["--material", "plywood_baltic_birch_3mm", "--tool", "flat_3.175mm_2flute"]
LASER = ["--head", "laser", "--material", "cardboard_thin_1mm"]


@pytest.mark.parametrize(
    "args",
    [
        # Spindle profile (every shape primitive)
        [
            "profile",
            *SPINDLE,
            "--shape",
            "rect",
            "--width",
            "30",
            "--height",
            "20",
            "--depth",
            "2",
        ],
        [
            "profile",
            *SPINDLE,
            "--shape",
            "rrect",
            "--width",
            "30",
            "--height",
            "20",
            "--radius",
            "3",
            "--depth",
            "2",
        ],
        ["profile", *SPINDLE, "--shape", "circle", "--diameter", "20", "--depth", "2"],
        [
            "profile",
            *SPINDLE,
            "--shape",
            "ellipse",
            "--width",
            "30",
            "--height",
            "15",
            "--depth",
            "2",
        ],
        [
            "profile",
            *SPINDLE,
            "--shape",
            "polygon",
            "--points",
            "0,0 20,0 10,15",
            "--depth",
            "2",
        ],
        # Spindle other ops
        ["pocket", *SPINDLE, "--shape", "circle", "--diameter", "20", "--depth", "2"],
        [
            "drill",
            *SPINDLE.copy()[:2],
            "--tool",
            "drill_3.2mm_m4_clearance",
            "--pattern",
            "grid",
            "--cols",
            "2",
            "--rows",
            "2",
            "--spacing",
            "10",
            "--depth",
            "3",
        ],
        [
            "chamfer",
            *SPINDLE.copy()[:2],
            "--tool",
            "vbit_60deg_6mm",
            "--shape",
            "rect",
            "--width",
            "30",
            "--height",
            "20",
            "--depth",
            "0.5",
        ],
        [
            "profile-tabs",
            *SPINDLE,
            "--shape",
            "rect",
            "--width",
            "30",
            "--height",
            "20",
            "--depth",
            "2",
            "--tab-count",
            "4",
        ],
        [
            "slot",
            *SPINDLE,
            "--p1",
            "0,0",
            "--p2",
            "20,0",
            "--width",
            "5",
            "--depth",
            "1",
        ],
        [
            "face",
            *SPINDLE,
            "--shape",
            "rect",
            "--width",
            "30",
            "--height",
            "20",
            "--depth",
            "0.3",
        ],
        [
            "engrave",
            *SPINDLE.copy()[:2],
            "--tool",
            "vbit_60deg_6mm",
            "--text",
            "TEST",
            "--height",
            "5",
            "--depth",
            "0.3",
        ],
        # Laser
        ["profile", *LASER, "--shape", "rect", "--width", "30", "--height", "20"],
        ["profile", *LASER, "--shape", "circle", "--diameter", "20"],
        ["slot", *LASER, "--p1", "0,0", "--p2", "20,0", "--width", "4"],
        ["engrave", *LASER, "--text", "TEST", "--height", "5"],
    ],
)
def test_emits_validator_clean(args, tmp_path):
    out = _run_cli(args, tmp_path)
    _validate(out)


# ---------------------------------------------------------------------------
# Refusal messages for laser-incompatible ops
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "op,fragment",
    [
        ("pocket", "can't pocket"),
        ("drill", "doesn't drill"),
        ("chamfer", "V-bit"),
        ("profile-tabs", "tabs"),
        ("face", "face"),
    ],
)
def test_laser_refusals(op, fragment, tmp_path, capsys):
    args = [op, *LASER]
    if op in ("pocket", "chamfer", "profile-tabs", "face"):
        args += ["--shape", "circle", "--diameter", "20"]
    elif op == "drill":
        args += ["--pattern", "grid", "--cols", "2", "--rows", "2", "--spacing", "10"]
    with pytest.raises(SystemExit) as exc:
        cam_cli.main(args + ["--out", str(tmp_path / "x.gcode"), "--no-validate"])
    assert fragment.lower() in str(exc.value).lower()


# ---------------------------------------------------------------------------
# Missing required-by-context args
# ---------------------------------------------------------------------------


def test_missing_depth_spindle(tmp_path):
    with pytest.raises(SystemExit):
        cam_cli.main(
            [
                "profile",
                *SPINDLE,
                "--shape",
                "circle",
                "--diameter",
                "20",
                "--out",
                str(tmp_path / "x.gcode"),
                "--no-validate",
            ]
        )
