#!/usr/bin/env python3
"""Read GRBL state via serial and print a structured machine-state report.

Standalone tool — does not depend on Claude or any LLM. Run from any
shell. Used pre-job to verify what the operator believes about machine
state before they cut.

Usage:
  python inspect.py [--port PORT] [--baud N]
                    [--verbose] [--expect-head laser|spindle]
                    [--write-json PATH]

The script sends three GRBL read-only queries (?, $$, $#) and parses
the responses. Read-only: no motion commands are issued.

Exit codes:
  0 — everything looks consistent
  1 — flagged anomaly (head mismatch, soft limits off, alarm active...)
  2 — connection / parse failure
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Pure response parsers — testable without a real machine.
# ---------------------------------------------------------------------------


@dataclass
class MachineStatus:
    """Parsed from a `?` status response, e.g. <Idle|MPos:...|FS:0,0>."""

    state: str = "Unknown"
    mpos: tuple[float, float, float] = (0.0, 0.0, 0.0)
    feed: float | None = None
    spindle: float | None = None
    buffer: tuple[int, int] | None = None  # planner blocks, RX bytes free
    raw: str = ""


def parse_status(line: str) -> MachineStatus:
    """Parse a GRBL `?` response. Returns MachineStatus with raw line preserved.

    Tolerant: missing fields are left as defaults, unknown fields are ignored.
    """
    line = line.strip()
    if not (line.startswith("<") and line.endswith(">")):
        return MachineStatus(state="Unknown", raw=line)

    inside = line[1:-1]
    parts = inside.split("|")
    status = MachineStatus(state=parts[0], raw=line)
    for part in parts[1:]:
        if ":" not in part:
            continue
        key, _, val = part.partition(":")
        if key == "MPos":
            try:
                x, y, z = (float(v) for v in val.split(",")[:3])
                status.mpos = (x, y, z)
            except ValueError:
                pass
        elif key == "FS":
            try:
                f, s = (float(v) for v in val.split(",")[:2])
                status.feed = f
                status.spindle = s
            except ValueError:
                pass
        elif key == "Bf":
            try:
                blocks, rx = (int(v) for v in val.split(",")[:2])
                status.buffer = (blocks, rx)
            except ValueError:
                pass
    return status


def parse_settings(lines: list[str]) -> dict[int, float]:
    """Parse a GRBL `$$` response into {setting_number: value}.

    Tolerates noise (`ok`, blank lines, unrelated chatter).
    """
    out: dict[int, float] = {}
    for line in lines:
        line = line.strip()
        m = re.match(r"^\$(\d+)\s*=\s*(-?\d+\.?\d*)\s*$", line)
        if m:
            out[int(m.group(1))] = float(m.group(2))
    return out


@dataclass
class Parameters:
    """Parsed from a `$#` response.

    wcs maps coordinate system name (G54..G59) to (x, y, z) offsets in mm.
    """

    wcs: dict[str, tuple[float, float, float]] = field(default_factory=dict)
    g28: tuple[float, float, float] | None = None
    g30: tuple[float, float, float] | None = None
    g92: tuple[float, float, float] | None = None
    tlo: float | None = None
    last_probe: tuple[float, float, float, int] | None = None  # x, y, z, success


def parse_parameters(lines: list[str]) -> Parameters:
    """Parse a GRBL `$#` response."""
    out = Parameters()
    for line in lines:
        line = line.strip()
        m = re.match(r"^\[([A-Z0-9]+):([^\]]+)\]\s*$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        if key.startswith("G") and key[1:].isdigit():
            try:
                coords = val.split(",")
                xyz = tuple(float(v) for v in coords[:3])
                if key in ("G54", "G55", "G56", "G57", "G58", "G59"):
                    out.wcs[key] = xyz
                elif key == "G28":
                    out.g28 = xyz
                elif key == "G30":
                    out.g30 = xyz
                elif key == "G92":
                    out.g92 = xyz
            except ValueError:
                pass
        elif key == "TLO":
            try:
                out.tlo = float(val)
            except ValueError:
                pass
        elif key == "PRB":
            # Format: PRB:x,y,z:success
            m2 = re.match(r"^(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*):(\d+)$", val)
            if m2:
                out.last_probe = (
                    float(m2.group(1)),
                    float(m2.group(2)),
                    float(m2.group(3)),
                    int(m2.group(4)),
                )
    return out


def parse_version(lines: list[str]) -> str | None:
    """Parse a GRBL version line, typically `[VER:1.1h.20190825:]`."""
    for line in lines:
        line = line.strip()
        m = re.match(r"^\[VER:([^:]+):", line)
        if m:
            return m.group(1)
    return None


@dataclass
class WifiInfo:
    """Parsed from a Grbl_ESP32 `$I` response WiFi message line.

    Grbl_ESP32 emits something like:
      [MSG:Mode=STA:SSID=MyNet:Status=Connected:IP=192.168.4.116:MAC=AA-BB-CC-DD-EE-FF]

    Vanilla GRBL (AVR) never emits this line — in that case all fields are
    None and presence-checks should treat the machine as not WiFi-attached.
    """

    mode: str | None = None  # "STA", "AP", or unknown
    ssid: str | None = None
    ip: str | None = None
    mac: str | None = None
    status: str | None = None
    raw: str | None = None


def parse_wifi(lines: list[str]) -> WifiInfo:
    """Parse a Grbl_ESP32 `$I` response for the WiFi MSG line.

    Tolerant: if no MSG line is present (vanilla GRBL, USB-only build,
    WiFi disabled) returns an empty WifiInfo. Field order inside the
    bracket may vary across firmware builds; parses key=value pairs by
    name rather than position.
    """
    info = WifiInfo()
    # Look for [MSG:...IP=...] — the MAC suffix is the most reliable
    # marker but we accept any MSG line that has Mode= or IP= in it.
    for line in lines:
        line = line.strip()
        if not (line.startswith("[MSG:") and line.endswith("]")):
            continue
        body = line[len("[MSG:") : -1]
        # Some MSG lines are status-only ("[MSG:SSDP Started]"); skip
        # those — they have no "=" anywhere.
        if "=" not in body:
            continue
        # Tokenize on ":" between key=val pairs. Values themselves may
        # contain "-" (MAC) or "." (IP) but not ":".
        fields: dict[str, str] = {}
        for token in body.split(":"):
            if "=" in token:
                k, _, v = token.partition("=")
                fields[k.strip()] = v.strip()
        # Heuristic: only treat as WiFi info if we see at least one of
        # Mode/SSID/IP/MAC keys.
        if not any(k in fields for k in ("Mode", "SSID", "IP", "MAC")):
            continue
        info.raw = line
        info.mode = fields.get("Mode")
        info.ssid = fields.get("SSID")
        info.ip = fields.get("IP")
        info.mac = fields.get("MAC")
        info.status = fields.get("Status")
        # First match wins — there's normally only one WiFi MSG in $I.
        return info
    return info


# ---------------------------------------------------------------------------
# GRBL setting metadata — keys that the report calls out by name.
# ---------------------------------------------------------------------------


KEY_SETTINGS = {
    13: ("units", lambda v: "mm" if int(v) == 0 else "inches"),
    20: ("soft limits", lambda v: "enabled" if int(v) == 1 else "DISABLED"),
    21: ("hard limits", lambda v: "enabled" if int(v) == 1 else "disabled"),
    22: ("homing enabled", lambda v: "yes" if int(v) == 1 else "NO"),
    32: (
        "laser mode ($32)",
        lambda v: "ON (laser)" if int(v) == 1 else "off (spindle)",
    ),
    130: ("max travel X (mm)", lambda v: f"{v:.1f}"),
    131: ("max travel Y (mm)", lambda v: f"{v:.1f}"),
    132: ("max travel Z (mm)", lambda v: f"{v:.1f}"),
}


# ---------------------------------------------------------------------------
# Report formatter — also a pure function.
# ---------------------------------------------------------------------------


def format_report(
    port: str,
    version: str | None,
    status: MachineStatus,
    settings: dict[int, float],
    params: Parameters,
    verbose: bool = False,
    expect_head: str | None = None,
    wifi: WifiInfo | None = None,
) -> tuple[str, list[str]]:
    """Render the human-readable report. Returns (text, flags).

    `flags` is a list of strings naming any anomalies the report calls out
    (used by the CLI to set exit code 1 vs 0).
    """
    flags: list[str] = []
    lines = []
    lines.append("=== machine state ===")
    lines.append(f"Serial:        {port}")
    lines.append(f"GRBL version:  {version or '(unknown)'}")
    if wifi is not None and (wifi.ip or wifi.ssid or wifi.mac):
        lines.append("")
        lines.append("WiFi:")
        if wifi.mode:
            lines.append(f"  Mode:    {wifi.mode}")
        if wifi.ssid:
            lines.append(f"  SSID:    {wifi.ssid}")
        if wifi.ip:
            lines.append(f"  IP:      {wifi.ip}")
        if wifi.mac:
            lines.append(f"  MAC:     {wifi.mac}")
        if wifi.status:
            lines.append(f"  Status:  {wifi.status}")
    lines.append("")
    lines.append(f"State:         {status.state}")
    lines.append(
        f"Position (MPos):  X {status.mpos[0]:>9.3f}  Y {status.mpos[1]:>9.3f}  Z {status.mpos[2]:>9.3f}"
    )
    if status.feed is not None and status.spindle is not None:
        lines.append(f"Feed / Spindle:   F {status.feed:.0f}   S {status.spindle:.0f}")
    if status.buffer is not None:
        lines.append(
            f"Buffer:           {status.buffer[0]} planner blocks free, {status.buffer[1]} RX bytes free"
        )
    lines.append("")

    if status.state.lower().startswith("alarm"):
        flags.append("machine is in ALARM state — clear before continuing")

    # Key settings
    lines.append("Key settings:")
    for n, (label, fmt) in KEY_SETTINGS.items():
        if n in settings:
            val = settings[n]
            try:
                rendered = fmt(val)
            except Exception:
                rendered = str(val)
            highlight = ""
            if n == 32 and expect_head:
                expected = "1" if expect_head == "laser" else "0"
                if str(int(val)) != expected:
                    highlight = "  <-- MISMATCH"
                    flags.append(f"$32={int(val)} but --expect-head={expect_head}")
            if n == 20 and int(val) == 0:
                highlight = "  <-- soft limits OFF"
                flags.append("soft limits disabled")
            lines.append(f"  ${n:<4} {label:<20} {rendered}{highlight}")
    lines.append("")

    if verbose:
        lines.append("All settings:")
        for n in sorted(settings):
            if n in KEY_SETTINGS:
                continue
            lines.append(f"  ${n:<4} = {settings[n]}")
        lines.append("")

    # Work coordinate offsets
    lines.append("Work coordinate offsets:")
    for name in ("G54", "G55", "G56", "G57", "G58", "G59"):
        if name in params.wcs:
            x, y, z = params.wcs[name]
            lines.append(f"  {name}:  X {x:>9.3f}  Y {y:>9.3f}  Z {z:>9.3f}")
    if params.tlo is not None:
        lines.append(f"  TLO (tool length offset):  {params.tlo:.3f}")
    if params.last_probe is not None:
        px, py, pz, ok = params.last_probe
        ok_str = "ok" if ok else "FAILED"
        lines.append(
            f"  last probe (PRB):  X {px:.3f}  Y {py:.3f}  Z {pz:.3f}  [{ok_str}]"
        )
    lines.append("")

    if not flags:
        lines.append("(no anomalies detected)")
    else:
        lines.append("Flags:")
        for f in flags:
            lines.append(f"  ! {f}")

    return "\n".join(lines), flags


# ---------------------------------------------------------------------------
# Serial layer — talks to a real machine. Skipped in unit tests.
# ---------------------------------------------------------------------------


def _import_pyserial():
    try:
        import serial  # type: ignore

        return serial
    except ImportError:
        sys.exit(
            "error: pyserial is not installed. Run:\n"
            "  python -m pip install pyserial>=3.5"
        )


def _read_until_ok(ser, timeout_s: float = 2.0) -> list[str]:
    """Read lines from the serial port until we see `ok`, `error`, or timeout."""
    deadline = time.monotonic() + timeout_s
    lines: list[str] = []
    buf = b""
    while time.monotonic() < deadline:
        chunk = ser.read(256)
        if chunk:
            buf += chunk
            while b"\n" in buf:
                line, _, buf = buf.partition(b"\n")
                decoded = line.decode("ascii", errors="replace").strip()
                if decoded:
                    lines.append(decoded)
                if decoded == "ok" or decoded.startswith("error"):
                    return lines
        else:
            time.sleep(0.01)
    return lines


def _read_one_line(ser, timeout_s: float = 1.0) -> str:
    """Read a single line. Used for `?` real-time status query."""
    deadline = time.monotonic() + timeout_s
    buf = b""
    while time.monotonic() < deadline:
        chunk = ser.read(256)
        if chunk:
            buf += chunk
            if b"\n" in buf:
                line, _, _ = buf.partition(b"\n")
                return line.decode("ascii", errors="replace").strip()
        else:
            time.sleep(0.01)
    return ""


def query_machine(
    port: str, baud: int = 115200
) -> tuple[str | None, MachineStatus, dict, Parameters, WifiInfo]:
    """Connect to the machine, issue queries, return parsed results."""
    serial = _import_pyserial()
    try:
        ser = serial.Serial(port, baud, timeout=0.1)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"error: could not open {port}: {e}")

    try:
        # GRBL emits a banner on connection; give it a beat.
        # Grbl_ESP32 boards reset on USB-open and need ~5s before the
        # WiFi MSG appears in the $I response.
        time.sleep(2.0)
        ser.reset_input_buffer()

        # Version (and on Grbl_ESP32, WiFi info too — both ride on $I).
        ser.write(b"$I\n")
        ver_lines = _read_until_ok(ser)
        version = parse_version(ver_lines)
        wifi = parse_wifi(ver_lines)

        # Status (real-time, no newline needed; GRBL responds to a single '?')
        ser.write(b"?")
        status_line = _read_one_line(ser, timeout_s=0.5)
        status = parse_status(status_line)

        # Settings
        ser.write(b"$$\n")
        setting_lines = _read_until_ok(ser)
        settings = parse_settings(setting_lines)

        # Parameters
        ser.write(b"$#\n")
        param_lines = _read_until_ok(ser)
        params = parse_parameters(param_lines)

        return version, status, settings, params, wifi
    finally:
        ser.close()


def _resolve_port(arg_port: str | None) -> str | None:
    if arg_port:
        return arg_port
    env = os.environ.get("CNC_PORT")
    if env:
        return env
    return None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--port", default=None, help="serial port (or CNC_PORT env var)")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="print every setting, not just the key ones",
    )
    p.add_argument(
        "--expect-head",
        choices=["laser", "spindle"],
        default=None,
        help="flag a mismatch between $32 and expected head mode",
    )
    p.add_argument(
        "--write-json",
        type=Path,
        default=None,
        help="also write machine state to PATH as JSON",
    )
    p.add_argument(
        "--ip-only",
        action="store_true",
        help=(
            "print just the IP from $I and exit 0 (or exit 1 with stderr "
            "if the machine isn't WiFi-attached). Suitable for shell capture: "
            "IP=$(grbl_inspect.py --ip-only --port /dev/cu.usbserial-140)"
        ),
    )
    args = p.parse_args()

    port = _resolve_port(args.port)
    if not port:
        print(
            "error: no serial port specified. Pass --port or set CNC_PORT.\n"
            "  Linux/macOS: typically /dev/ttyUSB0 or /dev/ttyACM0\n"
            "  Windows:     COM3 (check Device Manager)",
            file=sys.stderr,
        )
        return 2

    try:
        version, status, settings, params, wifi = query_machine(port, args.baud)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"error: query failed: {e}", file=sys.stderr)
        return 2

    if args.ip_only:
        if wifi.ip:
            print(wifi.ip)
            # Best-effort persist: write to the state cache so later
            # cache-only lookups (cnc.py ip) can find it without USB.
            try:
                from cnc_state import save_machine  # type: ignore

                save_machine(wifi.ip, mac=wifi.mac, ssid=wifi.ssid)
            except Exception:  # noqa: BLE001
                # Don't fail --ip-only just because the cache is unwritable;
                # the IP itself is the contract.
                pass
            return 0
        print(
            "error: machine did not report an IP in $I response.\n"
            "  This usually means vanilla GRBL (no WiFi) or WiFi disabled in "
            "the Grbl_ESP32 build.",
            file=sys.stderr,
        )
        return 1

    text, flags = format_report(
        port=port,
        version=version,
        status=status,
        settings=settings,
        params=params,
        verbose=args.verbose,
        expect_head=args.expect_head,
        wifi=wifi,
    )
    print(text)

    # Best-effort: cache discovered IP for cnc.py ip lookups.
    if wifi.ip:
        try:
            from cnc_state import save_machine  # type: ignore

            save_machine(wifi.ip, mac=wifi.mac, ssid=wifi.ssid)
        except Exception:  # noqa: BLE001
            pass

    if args.write_json:
        payload = {
            "port": port,
            "version": version,
            "status": asdict(status),
            "settings": {f"${n}": v for n, v in settings.items()},
            "wcs": params.wcs,
            "tlo": params.tlo,
            "last_probe": params.last_probe,
            "wifi": asdict(wifi),
            "flags": flags,
        }
        args.write_json.write_text(json.dumps(payload, indent=2, default=str))

    return 1 if flags else 0


if __name__ == "__main__":
    sys.exit(main())
