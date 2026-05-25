"""Tests for scripts/cnc_state.py — persistent IP/MAC cache for the CNC.

Uses tmp_path to redirect the state file so the user's actual
~/.cnc_state.json is never touched.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import cnc_state  # noqa: E402


def _override_state_file(monkeypatch, path: Path):
    monkeypatch.setenv("CNC_STATE_FILE", str(path))


# ---------------------------------------------------------------------------
# load/save round-trip
# ---------------------------------------------------------------------------


def test_load_missing_file_returns_empty_skeleton(tmp_path, monkeypatch):
    _override_state_file(monkeypatch, tmp_path / "nope.json")
    data = cnc_state.load_state()
    assert data == {"version": 1, "machines": {}}


def test_load_corrupt_file_returns_empty_skeleton(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")
    _override_state_file(monkeypatch, bad)
    assert cnc_state.load_state() == {"version": 1, "machines": {}}


def test_save_and_load_round_trip(tmp_path, monkeypatch):
    _override_state_file(monkeypatch, tmp_path / "state.json")
    cnc_state.save_state({"version": 1, "machines": {"k": {"ip": "10.0.0.5"}}})
    data = cnc_state.load_state()
    assert data["machines"]["k"]["ip"] == "10.0.0.5"


def test_save_state_is_atomic_via_tmp(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    _override_state_file(monkeypatch, target)
    cnc_state.save_state({"version": 1, "machines": {}})
    assert target.exists()
    assert not (target.with_suffix(".json.tmp")).exists()


# ---------------------------------------------------------------------------
# save_machine + get_machine
# ---------------------------------------------------------------------------


def test_save_machine_keyed_by_mac(tmp_path, monkeypatch):
    _override_state_file(monkeypatch, tmp_path / "s.json")
    rec = cnc_state.save_machine(
        ip="192.168.1.10", mac="AA-BB-CC-DD-EE-FF", ssid="Net1"
    )
    assert rec.ip == "192.168.1.10"
    data = cnc_state.load_state()
    assert "AA-BB-CC-DD-EE-FF" in data["machines"]
    assert data["machines"]["AA-BB-CC-DD-EE-FF"]["ssid"] == "Net1"


def test_save_machine_default_key_when_no_mac(tmp_path, monkeypatch):
    _override_state_file(monkeypatch, tmp_path / "s.json")
    cnc_state.save_machine(ip="192.168.1.11")
    data = cnc_state.load_state()
    assert "default" in data["machines"]


def test_save_machine_preserves_existing_fields_on_upsert(tmp_path, monkeypatch):
    _override_state_file(monkeypatch, tmp_path / "s.json")
    cnc_state.save_machine(
        ip="192.168.1.10", mac="MAC1", hostname="grbl.local", ssid="Net1"
    )
    # Second call only updates IP; hostname + ssid should be preserved
    cnc_state.save_machine(ip="192.168.1.99", mac="MAC1")
    rec = cnc_state.get_machine(key="MAC1")
    assert rec.ip == "192.168.1.99"
    assert rec.hostname == "grbl.local"
    assert rec.ssid == "Net1"


def test_get_machine_returns_none_when_empty(tmp_path, monkeypatch):
    _override_state_file(monkeypatch, tmp_path / "s.json")
    assert cnc_state.get_machine() is None


def test_get_machine_by_key(tmp_path, monkeypatch):
    _override_state_file(monkeypatch, tmp_path / "s.json")
    cnc_state.save_machine(ip="1.1.1.1", mac="A")
    cnc_state.save_machine(ip="2.2.2.2", mac="B")
    rec = cnc_state.get_machine(key="B")
    assert rec.ip == "2.2.2.2"


def test_get_machine_picks_most_recent_when_no_key(tmp_path, monkeypatch):
    _override_state_file(monkeypatch, tmp_path / "s.json")
    cnc_state.save_machine(ip="1.1.1.1", mac="A")
    # Force a later timestamp via direct save
    import time

    time.sleep(1.1)
    cnc_state.save_machine(ip="2.2.2.2", mac="B")
    rec = cnc_state.get_machine()
    assert rec.ip == "2.2.2.2"


# ---------------------------------------------------------------------------
# Freshness + age formatting
# ---------------------------------------------------------------------------


def test_age_seconds_handles_z_suffix():
    now = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)
    past = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rec = cnc_state.MachineRecord(ip="x", last_seen=past)
    assert rec.age_seconds(now=now) == pytest.approx(300, abs=1)


def test_age_seconds_returns_none_for_empty():
    rec = cnc_state.MachineRecord(ip="x", last_seen="")
    assert rec.age_seconds() is None


def test_age_seconds_returns_none_for_garbage():
    rec = cnc_state.MachineRecord(ip="x", last_seen="garbage")
    assert rec.age_seconds() is None


def test_is_fresh_within_threshold():
    now = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)
    past = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rec = cnc_state.MachineRecord(ip="x", last_seen=past)
    assert cnc_state.is_fresh(rec, max_age_sec=6 * 3600, now=now) is True


def test_is_fresh_rejects_stale():
    now = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)
    past = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rec = cnc_state.MachineRecord(ip="x", last_seen=past)
    assert cnc_state.is_fresh(rec, max_age_sec=6 * 3600, now=now) is False


def test_is_fresh_false_for_empty_timestamp():
    rec = cnc_state.MachineRecord(ip="x", last_seen="")
    assert cnc_state.is_fresh(rec) is False


def test_format_age_human_readable():
    assert "seconds" in cnc_state.format_age(30)
    assert "minutes" in cnc_state.format_age(180)
    assert "hours" in cnc_state.format_age(3700)
    assert "days" in cnc_state.format_age(180000)
