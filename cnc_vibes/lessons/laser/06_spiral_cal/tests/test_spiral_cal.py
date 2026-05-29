"""Tests for lessons/laser/06_spiral_cal/spiral_cal.py."""

from __future__ import annotations

import math
import re
import subprocess
import sys
from pathlib import Path

import pytest

LESSON_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = LESSON_DIR.parent.parent.parent

sys.path.insert(0, str(LESSON_DIR))
import spiral_cal  # noqa: E402


def test_patch_centers_first_is_origin():
    centers = spiral_cal.patch_centers(1)
    assert centers == [(0.0, 0.0)]


def test_patch_centers_ring_1_count_and_spacing():
    centers = spiral_cal.patch_centers(7)  # center + 6
    assert len(centers) == 7
    pitch = spiral_cal.PATCH_OUTER_DIAMETER_MM + spiral_cal.PATCH_GAP_MM
    # All ring-1 centers should be exactly `pitch` from origin
    for cx, cy in centers[1:]:
        assert math.hypot(cx, cy) == pytest.approx(pitch, abs=1e-9)


def test_patch_centers_no_overlap_for_19_patches():
    # 19 patches = ring 0 + ring 1 (6) + ring 2 (12)
    centers = spiral_cal.patch_centers(19)
    assert len(centers) == 19
    min_dist = 2 * (spiral_cal.PATCH_OUTER_DIAMETER_MM / 2)  # touching threshold
    for i, c1 in enumerate(centers):
        for c2 in centers[i + 1 :]:
            d = math.hypot(c1[0] - c2[0], c1[1] - c2[1])
            assert d >= min_dist, (
                f"patches {c1} and {c2} overlap (distance {d:.2f} < {min_dist:.2f})"
            )


def test_patch_centers_empty_for_zero():
    assert spiral_cal.patch_centers(0) == []


def _laser_mat():
    return spiral_cal.LaserMaterial(
        id="cardboard_thin_1mm",
        family="paper",
        thickness_mm=1.0,
        power_percent=50.0,
        feed_mm_per_min=2500,
        passes=1,
    )


def test_generate_sweep_power_emits_per_value_block():
    out = spiral_cal.generate_sweep(
        _laser_mat(),
        "power",
        [30, 50, 70],
        mode="static",
        warmup_ms=200,
    )
    assert "; ===== patch 1/3: power=30" in out.gcode
    assert "; ===== patch 2/3: power=50" in out.gcode
    assert "; ===== patch 3/3: power=70" in out.gcode
    # Three different M3 S values (300, 500, 700)
    for s in (300, 500, 700):
        assert f"M3 S{s}" in out.gcode
    # No M4 in static mode
    assert "M4 " not in out.gcode


def test_generate_sweep_static_mode_header_present():
    out = spiral_cal.generate_sweep(
        _laser_mat(),
        "power",
        [50],
        mode="static",
    )
    assert ";LASER_MODE: static" in out.gcode


def test_generate_sweep_warmup_emits_dwell_per_ring():
    # one patch -> 3 rings (outer circle + 2 spirals) -> 3 G4 dwells
    out = spiral_cal.generate_sweep(
        _laser_mat(),
        "power",
        [50],
        mode="static",
        warmup_ms=300,
    )
    dwells = [l for l in out.gcode.splitlines() if l.startswith("G4 P0.300")]
    assert len(dwells) == 3


def test_generate_sweep_rejects_unknown_var():
    with pytest.raises(SystemExit):
        spiral_cal.generate_sweep(_laser_mat(), "voltage", [12, 24])


def test_generate_sweep_rejects_empty_values():
    with pytest.raises(SystemExit):
        spiral_cal.generate_sweep(_laser_mat(), "power", [])


def test_generate_sweep_passes_propagates():
    out = spiral_cal.generate_sweep(
        _laser_mat(),
        "passes",
        [1, 3],
        mode="static",
    )
    # patch 1: passes=1 (no per-pass comment); patch 2: passes=3 (3 per-pass)
    assert "; ===== patch 1/2: passes=1" in out.gcode
    assert "; ===== patch 2/2: passes=3" in out.gcode
    assert "; pass 1/3" in out.gcode
    assert "; pass 3/3" in out.gcode


def test_end_to_end_validator_clean(tmp_path):
    out = tmp_path / "sweep.gcode"
    rc = spiral_cal.main(
        [
            "--material",
            "cardboard_thin_1mm",
            "--sweep",
            "power",
            "--values",
            "30,50,70",
            "--laser-mode",
            "static",
            "--out",
            str(out),
            "--no-validate",
        ]
    )
    assert rc == 0
    r = subprocess.run(
        [sys.executable, str(REPO_ROOT / "cnc.py"), "validate", str(out)],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert re.search(r"\bok\b", r.stdout)


def test_cnc_py_predispatch_passes_through_flags(tmp_path):
    """Regression: cnc.py cal-laser must forward leading-dash flags to
    spiral_cal.main without argparse mangling order."""
    out = tmp_path / "via_cnc.gcode"
    r = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "cnc.py"),
            "cal-laser",
            "--material",
            "cardboard_thin_1mm",
            "--sweep",
            "feed",
            "--values",
            "2000,2500,3000",
            "--laser-mode",
            "static",
            "--out",
            str(out),
            "--no-validate",
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert out.exists()


# ---------------------------------------------------------------------------
# Z (focus) sweep — CNC Z axis moves carriage, changing focal distance
# ---------------------------------------------------------------------------


def test_z_sweep_emits_g0_z_per_patch():
    out = spiral_cal.generate_sweep(
        _laser_mat(), "z", [-2.0, -1.0, 0.0, 1.0, 2.0], mode="static",
    )
    g0_z = [l for l in out.gcode.splitlines() if l.startswith("G0 Z")]
    assert g0_z == ["G0 Z-2.000", "G0 Z-1.000", "G0 Z0.000", "G0 Z1.000", "G0 Z2.000"]


def test_z_sweep_keeps_constant_power_across_patches():
    out = spiral_cal.generate_sweep(
        _laser_mat(), "z", [0.0, 1.0], mode="static", power_percent=60.0,
    )
    s_values = {
        l.split("S")[1] for l in out.gcode.splitlines() if l.startswith("M3 S")
    }
    assert s_values == {"600"}


def test_fixed_z_applies_to_all_patches_when_not_sweeping_z():
    out = spiral_cal.generate_sweep(
        _laser_mat(), "power", [30, 50], mode="static", z_mm=-78.5,
    )
    g0_z = [l for l in out.gcode.splitlines() if l.startswith("G0 Z")]
    assert g0_z == ["G0 Z-78.500", "G0 Z-78.500"]


def test_no_z_emitted_when_neither_swept_nor_fixed():
    out = spiral_cal.generate_sweep(
        _laser_mat(), "power", [30, 50], mode="static",
    )
    assert "G0 Z" not in out.gcode


def test_z_sweep_emits_safety_header():
    out = spiral_cal.generate_sweep(
        _laser_mat(), "z", [0.0], mode="static",
    )
    assert "Z SWEEP" in out.gcode
    assert "crash" in out.gcode.lower()
