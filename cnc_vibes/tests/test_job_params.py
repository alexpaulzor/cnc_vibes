"""Tests for scripts/job_params.py — math, safety checks, and config loading."""

import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from job_params import (  # noqa: E402
    compute_derived,
    find_by_id,
    format_report,
    load_job,
)


MACHINE = {
    "name": "test machine",
    "envelope_mm": {"x": 400, "y": 300, "z": 100},
    "max_feed_mm_per_min": {"xy": 3000, "z": 1000},
    "spindle": {"rpm_min": 8000, "rpm_max": 24000, "control": "pwm"},
}
TOOL = {
    "id": "flat_3mm_2flute",
    "type": "flat_endmill",
    "diameter_mm": 3.0,
    "flutes": 2,
    "max_rpm": 24000,
    "max_plunge_mm_per_min": 300,
}
MATERIAL = {
    "id": "plywood_test",
    "family": "wood",
    "thickness_mm": 6.0,
    "chipload": {"flat_3mm_2flute": 0.05},
    "doc_fraction": 0.5,
    "doc_fraction_finish": 0.25,
}


def test_feed_is_chipload_times_flutes_times_rpm():
    d = compute_derived(MACHINE, MATERIAL, TOOL, spindle_rpm=18000)
    # 0.05 * 2 * 18000 = 1800
    assert d["values"]["feed_xy_mm_per_min"] == pytest.approx(1800.0)


def test_doc_is_fraction_times_diameter():
    d = compute_derived(MACHINE, MATERIAL, TOOL, spindle_rpm=18000)
    assert d["values"]["doc_rough_mm"] == pytest.approx(1.5)  # 0.5 * 3.0
    assert d["values"]["doc_finish_mm"] == pytest.approx(0.75)  # 0.25 * 3.0


def test_through_cut_includes_spoilboard_overcut():
    d = compute_derived(MACHINE, MATERIAL, TOOL, spindle_rpm=18000)
    assert d["values"]["through_cut_depth_mm"] == pytest.approx(-6.2)


def test_passes_rounds_up():
    d = compute_derived(MACHINE, MATERIAL, TOOL, spindle_rpm=18000)
    # |6.2| / 1.5 = 4.133 -> 5
    assert d["values"]["passes_through"] == 5


def test_check_flags_rpm_above_machine_max():
    d = compute_derived(MACHINE, MATERIAL, TOOL, spindle_rpm=30000)
    rpm_check = next(c for c in d["checks"] if "machine range" in c["label"])
    assert rpm_check["ok"] is False


def test_check_flags_rpm_above_tool_max():
    tool = {**TOOL, "max_rpm": 12000}
    d = compute_derived(MACHINE, MATERIAL, tool, spindle_rpm=18000)
    tool_check = next(c for c in d["checks"] if "tool max" in c["label"])
    assert tool_check["ok"] is False


def test_check_flags_feed_above_machine_max():
    # Force a feed > 3000 mm/min: chipload 0.20 * 2 * 18000 = 7200
    mat = {**MATERIAL, "chipload": {"flat_3mm_2flute": 0.20}}
    d = compute_derived(MACHINE, mat, TOOL, spindle_rpm=18000)
    feed_check = next(c for c in d["checks"] if "XY max" in c["label"])
    assert feed_check["ok"] is False


def test_check_flags_plunge_above_machine_z_max():
    tool = {**TOOL, "max_plunge_mm_per_min": 2000}
    d = compute_derived(MACHINE, MATERIAL, tool, spindle_rpm=18000)
    plunge_check = next(c for c in d["checks"] if "Z max" in c["label"])
    assert plunge_check["ok"] is False


def test_chipload_missing_raises_keyerror():
    mat = {**MATERIAL, "chipload": {"other_tool": 0.05}}
    with pytest.raises(KeyError, match="no chipload entry"):
        compute_derived(MACHINE, mat, TOOL, spindle_rpm=18000)


def test_find_by_id_returns_matching_dict():
    items = [{"id": "a", "x": 1}, {"id": "b", "x": 2}]
    assert find_by_id(items, "b", "thing") == {"id": "b", "x": 2}


def test_find_by_id_raises_for_unknown():
    items = [{"id": "a"}, {"id": "b"}]
    with pytest.raises(KeyError, match="thing 'c' not found"):
        find_by_id(items, "c", "thing")


def test_load_job_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="no job.yaml"):
        load_job(tmp_path)


def test_load_job_missing_keys(tmp_path):
    (tmp_path / "job.yaml").write_text("material: foo\n")
    with pytest.raises(ValueError, match="missing keys"):
        load_job(tmp_path)


def test_load_job_happy_path(tmp_path):
    (tmp_path / "job.yaml").write_text(
        yaml.safe_dump(
            {
                "material": "plywood_test",
                "tool": "flat_3mm_2flute",
                "spindle_rpm": 18000,
                "gcode": "out.gcode",
            }
        )
    )
    job = load_job(tmp_path)
    assert job.material == "plywood_test"
    assert job.tool == "flat_3mm_2flute"
    assert job.spindle_rpm == 18000
    assert job.gcode == "out.gcode"
    assert job.name == tmp_path.name


def test_format_report_includes_derivation_explanation():
    from job_params import JobSpec

    job = JobSpec(name="test", material="x", tool="y", spindle_rpm=18000, gcode="z")
    d = compute_derived(MACHINE, MATERIAL, TOOL, spindle_rpm=18000)
    out = format_report(job, MACHINE, MATERIAL, TOOL, d)
    # Spot-check that the derivation formulas are visible to the user.
    assert "chipload x flutes x rpm" in out
    assert "1800 mm/min" in out
    assert "doc_fraction x diameter" in out
