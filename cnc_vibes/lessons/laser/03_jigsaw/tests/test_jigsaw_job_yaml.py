"""Tests for the jigsaw lesson's job.yaml integration.

Validates:
- Each example job.yaml parses + passes schema validation
- jigsaw_argv() derives the right argv for each mode
- argparse on jigsaw.py accepts the derived argv (round-trip without
  invoking the actual cut)
- A laser-head job.yaml routes to the LASER_PREFLIGHT_CHECKLIST via
  scripts/job_params.load_job_yaml
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

LESSON_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = LESSON_DIR.parent.parent.parent
EXAMPLES_DIR = LESSON_DIR / "examples"

sys.path.insert(0, str(LESSON_DIR))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from job_yaml import (  # noqa: E402
    VALID_MODES,
    VALID_RASTER_MODES,
    VALID_SIZES,
    jigsaw_argv,
    validate_jigsaw_job,
)
from job_params import (  # noqa: E402
    LASER_PREFLIGHT_CHECKLIST,
    PREFLIGHT_CHECKLIST,
    laser_job_from_yaml,
    load_job_yaml,
)

EXAMPLE_FILES = sorted(EXAMPLES_DIR.glob("*.yaml"))


# ---------------------------------------------------------------------------
# Sanity: examples directory layout
# ---------------------------------------------------------------------------


def test_examples_dir_exists():
    assert EXAMPLES_DIR.is_dir(), f"missing examples dir: {EXAMPLES_DIR}"


def test_three_example_yamls_present():
    names = {p.name for p in EXAMPLE_FILES}
    assert names == {"small_n.yaml", "nora_300.yaml", "nora_with_photo.yaml"}


# ---------------------------------------------------------------------------
# Schema validation: every example parses cleanly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("yaml_path", EXAMPLE_FILES, ids=lambda p: p.name)
def test_example_yaml_loads_as_laser_job(yaml_path):
    data = load_job_yaml(yaml_path)
    assert data["head"] == "laser"
    assert "material" in data
    assert "gcode" in data
    assert "jigsaw" in data


@pytest.mark.parametrize("yaml_path", EXAMPLE_FILES, ids=lambda p: p.name)
def test_example_yaml_schema_valid(yaml_path):
    data = load_job_yaml(yaml_path)
    jig = validate_jigsaw_job(data)
    assert jig["mode"] in VALID_MODES
    assert jig.get("size", "full") in VALID_SIZES


@pytest.mark.parametrize("yaml_path", EXAMPLE_FILES, ids=lambda p: p.name)
def test_example_yaml_round_trips_to_laser_job_dataclass(yaml_path):
    job = laser_job_from_yaml(yaml_path)
    assert job.material  # truthy
    assert job.gcode
    assert "jigsaw" in job.extras


# ---------------------------------------------------------------------------
# argv derivation: each example produces argv that jigsaw.py's parser accepts
# ---------------------------------------------------------------------------


def _build_jigsaw_parser():
    """Construct jigsaw.py's argparse parser without running main().

    Imports jigsaw.py and rebuilds its parser by introspecting `main`.
    Simpler: copy the subparser setup. We dispatch on subcommand below.
    """
    import argparse

    p = argparse.ArgumentParser()
    subs = p.add_subparsers(dest="command", required=True)

    pv = subs.add_parser("preview")
    pv.add_argument("--size", default="full", choices=("small", "full"))
    pv.add_argument("--word", default="NORA")
    pv.add_argument("--seed", type=int, default=7)

    cu = subs.add_parser("cut")
    cu.add_argument("--size", default="full", choices=("small", "full"))
    cu.add_argument("--word", default="NORA")
    cu.add_argument("--seed", type=int, default=7)
    cu.add_argument("--material", default="mdf_3mm")

    ra = subs.add_parser("raster")
    ra.add_argument("--size", default="small", choices=("small", "full"))
    ra.add_argument("--word", default="N")
    ra.add_argument("--seed", type=int, default=7)
    ra.add_argument("--material", default="mdf_3mm")
    src = ra.add_mutually_exclusive_group(required=True)
    src.add_argument("--image", type=Path)
    src.add_argument("--test-pattern", action="store_true")
    ra.add_argument("--mode", choices=("halftone", "grayscale"), default="halftone")
    ra.add_argument("--line-spacing-mm", type=float, default=0.20)
    ra.add_argument("--engrave-power-percent", type=int, default=30)
    ra.add_argument("--engrave-feed", type=int, default=3000)
    ra.add_argument("--grayscale-levels", type=int, default=16)
    return p


@pytest.mark.parametrize("yaml_path", EXAMPLE_FILES, ids=lambda p: p.name)
def test_argv_parses_against_jigsaw_cli(yaml_path):
    data = load_job_yaml(yaml_path)
    argv = jigsaw_argv(data)
    parser = _build_jigsaw_parser()
    # Should NOT raise SystemExit — argparse exits on bad args.
    parsed = parser.parse_args(argv)
    assert parsed.command in VALID_MODES


def test_small_n_argv_shape():
    data = load_job_yaml(EXAMPLES_DIR / "small_n.yaml")
    argv = jigsaw_argv(data)
    assert argv[0] == "cut"
    assert "--size" in argv and argv[argv.index("--size") + 1] == "small"
    assert "--word" in argv and argv[argv.index("--word") + 1] == "N"
    assert "--material" in argv and argv[argv.index("--material") + 1] == "mdf_3mm"


def test_nora_300_argv_shape():
    data = load_job_yaml(EXAMPLES_DIR / "nora_300.yaml")
    argv = jigsaw_argv(data)
    assert argv[0] == "cut"
    assert argv[argv.index("--size") + 1] == "full"
    assert argv[argv.index("--word") + 1] == "NORA"
    assert argv[argv.index("--seed") + 1] == "7"


def test_nora_with_photo_argv_shape():
    data = load_job_yaml(EXAMPLES_DIR / "nora_with_photo.yaml")
    argv = jigsaw_argv(data)
    assert argv[0] == "raster"
    assert argv[argv.index("--size") + 1] == "full"
    assert argv[argv.index("--mode") + 1] == "halftone"
    # `--image` should be present and point at an expanded path
    img_arg = argv[argv.index("--image") + 1]
    assert img_arg  # not empty
    assert "~" not in img_arg  # tilde-expansion happened


# ---------------------------------------------------------------------------
# Schema rejection — make sure bad shapes raise ValueError
# ---------------------------------------------------------------------------


def test_validate_rejects_non_laser_head():
    with pytest.raises(ValueError, match="head: laser"):
        validate_jigsaw_job(
            {"head": "spindle", "material": "mdf_3mm", "jigsaw": {"mode": "cut"}}
        )


def test_validate_rejects_missing_jigsaw_block():
    with pytest.raises(ValueError, match="`jigsaw:` block"):
        validate_jigsaw_job({"head": "laser", "material": "mdf_3mm"})


def test_validate_rejects_unknown_mode():
    with pytest.raises(ValueError, match="jigsaw.mode"):
        validate_jigsaw_job(
            {"head": "laser", "material": "mdf_3mm", "jigsaw": {"mode": "etch"}}
        )


def test_validate_rejects_raster_without_image_or_pattern():
    with pytest.raises(ValueError, match="image.*test_pattern"):
        validate_jigsaw_job(
            {"head": "laser", "material": "mdf_3mm", "jigsaw": {"mode": "raster"}}
        )


def test_validate_accepts_raster_with_test_pattern():
    jig = validate_jigsaw_job(
        {
            "head": "laser",
            "material": "mdf_3mm",
            "jigsaw": {"mode": "raster", "test_pattern": True},
        }
    )
    assert jig["test_pattern"] is True


def test_validate_rejects_bad_raster_mode():
    with pytest.raises(ValueError, match="raster_mode"):
        validate_jigsaw_job(
            {
                "head": "laser",
                "material": "mdf_3mm",
                "jigsaw": {
                    "mode": "raster",
                    "test_pattern": True,
                    "raster_mode": "vector",
                },
            }
        )


# ---------------------------------------------------------------------------
# Preflight routing: a laser-head job.yaml goes to the laser checklist
# ---------------------------------------------------------------------------


def _checklist_for_head(head: str) -> list[tuple[str, str]]:
    """Mirror cnc.py's _preflight_from_yaml dispatch."""
    if head == "laser":
        return LASER_PREFLIGHT_CHECKLIST
    return PREFLIGHT_CHECKLIST


@pytest.mark.parametrize("yaml_path", EXAMPLE_FILES, ids=lambda p: p.name)
def test_preflight_picks_laser_checklist_for_jigsaw_jobs(yaml_path):
    data = load_job_yaml(yaml_path)
    checklist = _checklist_for_head(data["head"])
    assert checklist is LASER_PREFLIGHT_CHECKLIST
    # Sanity: laser-specific items should be in the chosen checklist
    keys = {key for key, _ in checklist}
    assert "laser_glasses" in keys
    assert "air_assist" in keys
    assert "fire_ready" in keys
    # And spindle-specific items should NOT be in it
    assert "collet_tight" not in keys
    assert "z_probed" not in keys


def test_spindle_yaml_would_pick_spindle_checklist(tmp_path):
    # Sanity: the routing isn't hardcoded to laser
    p = tmp_path / "spindle_job.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "head": "spindle",
                "material": "plywood_baltic_birch_3mm",
                "tool": "flat_3.175mm_2flute",
                "spindle_rpm": 18000,
                "gcode": "out.gcode",
            }
        )
    )
    data = load_job_yaml(p)
    assert _checklist_for_head(data["head"]) is PREFLIGHT_CHECKLIST


def test_load_job_yaml_missing_keys_for_laser(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump({"head": "laser"}))
    with pytest.raises(ValueError, match="missing keys"):
        load_job_yaml(p)


def test_load_job_yaml_missing_keys_for_spindle(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump({"head": "spindle", "material": "x", "gcode": "y"}))
    with pytest.raises(ValueError, match="missing keys"):
        load_job_yaml(p)


def test_load_job_yaml_unknown_head(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump({"head": "waterjet", "material": "x", "gcode": "y"}))
    with pytest.raises(ValueError, match="unknown head"):
        load_job_yaml(p)


def test_load_job_yaml_defaults_to_spindle(tmp_path):
    # Backward compatibility for the original dir-based job.yaml files
    p = tmp_path / "legacy.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "material": "x",
                "tool": "y",
                "spindle_rpm": 18000,
                "gcode": "z.gcode",
            }
        )
    )
    data = load_job_yaml(p)
    assert data.get("head", "spindle") == "spindle"
