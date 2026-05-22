"""Schema sanity for profile YAML files.

These are interface tests: anything that consumes a profile (the validator,
future CAM templating, future post-processor wrappers) relies on these keys
being present and well-typed.
"""

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
PROFILES = REPO / "profiles"


def _load(name: str):
    with (PROFILES / name).open() as f:
        return yaml.safe_load(f)


def test_machine_profile_required_keys():
    p = _load("anolex_4030_evo_ultra2.yaml")
    assert p["name"]
    assert p["controller"]["dialect"].startswith("grbl")
    for axis in ("x", "y", "z"):
        assert isinstance(p["envelope_mm"][axis], (int, float))
        assert p["envelope_mm"][axis] > 0
    assert p["max_feed_mm_per_min"]["xy"] > 0
    assert p["max_feed_mm_per_min"]["z"] > 0
    assert p["spindle"]["rpm_max"] > p["spindle"]["rpm_min"] > 0
    assert "default_safe_z_mm" in p


def test_tools_schema():
    tools = _load("tools.yaml")
    assert isinstance(tools, list) and tools
    seen_ids = set()
    for t in tools:
        assert t["id"] not in seen_ids, f"duplicate tool id: {t['id']}"
        seen_ids.add(t["id"])
        assert t["type"] in {"flat_endmill", "ball_endmill", "v_bit"}
        assert t["diameter_mm"] > 0
        assert t["max_rpm"] > 0
        assert t["max_plunge_mm_per_min"] > 0


def test_materials_chipload_refs_real_tools():
    tools = {t["id"] for t in _load("tools.yaml")}
    mats = _load("materials.yaml")
    for m in mats:
        for tool_id in m.get("chipload", {}):
            assert tool_id in tools, (
                f"material '{m['id']}' references unknown tool '{tool_id}'"
            )


def test_materials_doc_fractions_sane():
    for m in _load("materials.yaml"):
        assert 0 < m["doc_fraction"] <= 2.0
        if "doc_fraction_finish" in m:
            assert 0 < m["doc_fraction_finish"] <= m["doc_fraction"]
