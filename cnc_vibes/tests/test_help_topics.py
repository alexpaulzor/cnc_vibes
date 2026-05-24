"""Tests for scripts/help_topics.py — every topic renders, index is complete,
search finds things, dynamic content stays in sync with its source.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from help_topics import (  # noqa: E402
    CATEGORIES,
    TOPICS,
    render_index,
    render_topic,
    search,
)
from job_params import LASER_PREFLIGHT_CHECKLIST, PREFLIGHT_CHECKLIST  # noqa: E402


@pytest.mark.parametrize("name", sorted(TOPICS.keys()))
def test_every_topic_renders_nontrivially(name):
    out = render_topic(name)
    assert isinstance(out, str)
    assert len(out) > 50, f"topic '{name}' rendered suspiciously short"


def test_every_categorized_topic_exists_in_TOPICS():
    for category, names in CATEGORIES.items():
        for name in names:
            assert name in TOPICS, (
                f"category '{category}' lists '{name}' but TOPICS has no entry"
            )


def test_every_topic_is_categorized():
    categorized = {n for names in CATEGORIES.values() for n in names}
    # 'topics' is the implicit index; exempt from categorization
    uncategorized = set(TOPICS) - categorized - {"topics"}
    assert not uncategorized, f"orphan topics: {sorted(uncategorized)}"


def test_index_lists_every_categorized_topic():
    idx = render_index()
    for names in CATEGORIES.values():
        for name in names:
            assert name in idx, f"index missing topic '{name}'"


def test_search_finds_topic_by_name():
    assert "preflight" in search("preflight")


def test_search_finds_topic_by_body_content():
    # 'chipload' appears in materials, params, validator-rules bodies.
    hits = search("chipload")
    assert "materials" in hits
    assert "params" in hits


def test_search_is_case_insensitive():
    assert search("GCODE") == search("gcode")


def test_search_returns_empty_for_unknown():
    assert search("zzz-no-such-keyword-zzz") == []


def test_unknown_topic_raises_keyerror():
    with pytest.raises(KeyError):
        render_topic("not-a-real-topic")


def test_checklist_topic_renders_every_preflight_item():
    out = render_topic("checklist")
    for key, _ in PREFLIGHT_CHECKLIST:
        assert f"[{key}]" in out, (
            f"checklist topic doesn't render preflight item '{key}'"
        )


def test_laser_checklist_topic_renders_every_laser_preflight_item():
    out = render_topic("laser-checklist")
    for key, _ in LASER_PREFLIGHT_CHECKLIST:
        assert f"[{key}]" in out, (
            f"laser-checklist topic doesn't render laser preflight item '{key}'"
        )


def test_laser_materials_topic_warns_about_dangerous_materials():
    out = render_topic("laser-materials")
    # The topic must call out at least the most common toxic-when-lasered items.
    for forbidden in ("PVC", "polycarbonate"):
        assert forbidden in out


def test_lesson_spacer_topic_names_the_script():
    out = render_topic("lesson-spacer")
    assert "spacer.py" in out
    assert "lessons/laser/01_spacer" in out


def test_search_finds_laser_checklist_by_dynamic_content():
    # 'hardware switch' is in the laser checklist text but in no static topic.
    hits = search("hardware switch")
    assert "laser-checklist" in hits


def test_validator_rules_topic_names_every_rule():
    out = render_topic("validator-rules")
    for rule in ("bounds", "max_feed", "max_plunge", "safe_z_rapid", "spindle_on"):
        assert rule in out, f"validator-rules topic missing '{rule}'"
