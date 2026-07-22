"""Persistent state for the cnc toolchain.

Stores the last-seen IP/MAC/hostname of each Grbl_ESP32 controller in
~/.cnc_state.json so we can:
- short-circuit a mDNS scan when the IP was recently confirmed
- give the user a starting guess when discovery fails

This module lives in scripts/ so both grbl_inspect.py and find_cnc.py
can import it via sys.path.

State file shape (versioned for forward-compat):

    {
      "version": 1,
      "machines": {
        "AA-BB-CC-DD-EE-FF": {                   # MAC, or "default"
          "ip": "192.168.4.116",
          "hostname": "grblesp.local.",
          "ssid": "MyNet",
          "last_seen": "2026-05-24T19:23:00Z"
        }
      }
    }

The key is the MAC when we have one (stable across IP changes), else
"default" when the caller doesn't know the MAC yet. Multi-machine setups
just write multiple entries; queries can target by MAC or use the
most-recently-seen.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


STATE_FILE_ENV = "CNC_STATE_FILE"
DEFAULT_FRESHNESS_SEC = 6 * 60 * 60  # 6 hours


def state_path() -> Path:
    """Return the state file path, honoring CNC_STATE_FILE override.

    Override exists so tests (and Windows installs that want a different
    home dir) can redirect cleanly.
    """
    if env := os.environ.get(STATE_FILE_ENV):
        return Path(env)
    return Path.home() / ".cnc_state.json"


@dataclass
class MachineRecord:
    ip: str
    hostname: str | None = None
    ssid: str | None = None
    mac: str | None = None
    last_seen: str = ""  # ISO-8601 UTC

    def age_seconds(self, now: datetime | None = None) -> float | None:
        """How long ago last_seen was, in seconds. None if unparseable."""
        if not self.last_seen:
            return None
        try:
            seen = datetime.fromisoformat(self.last_seen.replace("Z", "+00:00"))
        except ValueError:
            return None
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        now = now or datetime.now(timezone.utc)
        return (now - seen).total_seconds()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_state(path: Path | None = None) -> dict:
    """Load the state file. Returns an empty skeleton if it doesn't exist
    or is unreadable (silent — state is best-effort, never load-bearing)."""
    path = path or state_path()
    if not path.exists():
        return {"version": 1, "machines": {}}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "machines": {}}
    # Forward-compat: drop unrecognized versions, keep machines dict
    # if structurally valid.
    if not isinstance(data, dict):
        return {"version": 1, "machines": {}}
    data.setdefault("version", 1)
    data.setdefault("machines", {})
    if not isinstance(data["machines"], dict):
        data["machines"] = {}
    return data


def save_state(data: dict, path: Path | None = None) -> None:
    """Atomically write state file. Creates parent dir if needed."""
    path = path or state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(path)


def save_machine(
    ip: str,
    mac: str | None = None,
    hostname: str | None = None,
    ssid: str | None = None,
    path: Path | None = None,
) -> MachineRecord:
    """Upsert a machine record, keyed by MAC (or "default" if no MAC).

    Returns the record as written. Best-effort: callers should wrap
    in try/except if a missing-permission state file would be fatal.
    """
    data = load_state(path)
    key = mac or "default"
    existing = data["machines"].get(key, {})
    # Preserve fields not being overwritten this call.
    record = MachineRecord(
        ip=ip,
        hostname=hostname or existing.get("hostname"),
        ssid=ssid or existing.get("ssid"),
        mac=mac or existing.get("mac"),
        last_seen=_utcnow_iso(),
    )
    data["machines"][key] = asdict(record)
    save_state(data, path)
    return record


def get_machine(
    key: str | None = None,
    path: Path | None = None,
) -> MachineRecord | None:
    """Return one machine record. If key=None, return the most recently seen.

    Returns None if no machines are recorded.
    """
    data = load_state(path)
    machines = data["machines"]
    if not machines:
        return None
    if key is not None:
        raw = machines.get(key)
        return _record_from_dict(raw) if raw else None
    # Most-recently-seen wins.
    best_record = None
    best_seen = ""
    for raw in machines.values():
        seen = raw.get("last_seen", "")
        if seen >= best_seen:
            best_seen = seen
            best_record = raw
    return _record_from_dict(best_record) if best_record else None


def _record_from_dict(raw: dict) -> MachineRecord:
    return MachineRecord(
        ip=raw.get("ip", ""),
        hostname=raw.get("hostname"),
        ssid=raw.get("ssid"),
        mac=raw.get("mac"),
        last_seen=raw.get("last_seen", ""),
    )


def is_fresh(
    record: MachineRecord,
    max_age_sec: float = DEFAULT_FRESHNESS_SEC,
    now: datetime | None = None,
) -> bool:
    """True if the record's last_seen is within max_age_sec of now."""
    age = record.age_seconds(now=now)
    return age is not None and age <= max_age_sec


def format_age(seconds: float) -> str:
    """Human-readable age: '3 minutes ago', '2 hours ago', etc."""
    if seconds < 60:
        return f"{int(seconds)} seconds ago"
    if seconds < 3600:
        return f"{int(seconds / 60)} minutes ago"
    if seconds < 86400:
        hours = seconds / 3600
        return f"{hours:.1f} hours ago"
    days = seconds / 86400
    return f"{days:.1f} days ago"
