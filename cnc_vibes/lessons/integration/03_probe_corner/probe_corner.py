#!/usr/bin/env python3
"""Automated WCS corner-finding via touch-plate probing.

Standalone Python tool. Does not depend on Claude or any LLM. Drives
the spindle through a Z + X + Y probing sequence, parses PRB responses,
and writes the resulting offsets to G54 via G10 L20.

PHYSICAL SETUP this script assumes:
  * Touch plate is positioned at the front-left corner of the stock
    such that the plate's right edge is flush with stock's left edge,
    and plate's back edge is flush with stock's front edge.
  * Plate dimensions are known: thickness in Z, plus the XY offset from
    the probed edge to where you want WCS = (0, 0).
  * Spindle is jogged to a position above the center of the plate,
    roughly 3-5 mm above the plate top, with the tool installed.
  * Probe wire is connected to the GRBL probe pin; touching the tool
    to the plate completes a circuit.

Usage:
  python probe_corner.py --plate-thickness 12.0 \\
                         --plate-x-offset 25.0 \\
                         --plate-y-offset 25.0 \\
                         --tool-diameter 3.175 \\
                         [--port PORT] [--feed 50] [--dry-run] [--yes]

The default behavior PRINTS the GCode it would send and prompts for
explicit confirmation before any motion. Pass --yes to skip the prompt
(scripted use).

Exit codes:
  0 — probing succeeded, G54 written
  1 — probing failed (alarm, no contact, user aborted)
  2 — bad inputs / connection failure
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

LESSON_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LESSON_DIR.parent / "01_inspect"))
from grbl_inspect import parse_status  # noqa: E402


# ---------------------------------------------------------------------------
# Pure GCode generator — testable without a real machine.
# ---------------------------------------------------------------------------


@dataclass
class ProbeConfig:
    plate_thickness_mm: float
    plate_x_offset_mm: float
    plate_y_offset_mm: float
    tool_diameter_mm: float
    feed_mm_per_min: int = 50  # slow for accuracy
    retract_mm: float = 5.0
    max_probe_distance_mm: float = 15.0
    safe_z_mm: float = 10.0
    edge_clearance_mm: float = 8.0  # how far off the plate before XY probes


def generate_probe_sequence(cfg: ProbeConfig) -> list[str]:
    """Return GCode lines for the full Z + X + Y probing sequence.

    The sequence assumes:
      - tool is currently positioned ABOVE the plate center
      - probe wire is connected and working
      - $32 (laser mode) is OFF

    Lines are emitted as a list (one command per line) so the sender
    can stream them with handshaking and parse responses per probe.
    """
    F = cfg.feed_mm_per_min
    tool_radius = cfg.tool_diameter_mm / 2

    lines = [
        "; --- probe_corner.py — touch-plate corner finding ---",
        f"; plate: {cfg.plate_thickness_mm}mm thick, "
        f"offsets X={cfg.plate_x_offset_mm} Y={cfg.plate_y_offset_mm}",
        f"; tool diameter: {cfg.tool_diameter_mm}mm",
        ";",
        "G21     ; mm",
        "G90     ; absolute",
        "$32=0   ; laser mode OFF",
        "",
        "; ---- 1. probe Z down to find plate top ----",
        f"G38.2 Z-{cfg.max_probe_distance_mm} F{F}",
        # After probe, current position is plate top.
        # WCS Z = 0 at stock top; stock top = plate top - plate_thickness.
        # So WCS Z at current position = +plate_thickness.
        f"G10 L20 P1 Z{cfg.plate_thickness_mm}",
        f"G0 Z{cfg.safe_z_mm}  ; retract clear of plate",
        "",
        "; ---- 2. probe X (plate's right edge) ----",
        # Move +X off the plate, then down below plate top, then probe -X.
        f"G0 X{cfg.edge_clearance_mm}",
        f"G0 Z-{cfg.plate_thickness_mm / 2}  ; descend below plate top edge",
        f"G38.2 X-{cfg.max_probe_distance_mm} F{F}",
        # After probe, tool's LEFT side is touching plate's right edge.
        # Plate's right edge = stock's left edge = WCS X=0.
        # Tool center is at WCS X = 0 + tool_radius = +tool_radius.
        f"G10 L20 P1 X{tool_radius}",
        f"G0 X{cfg.edge_clearance_mm}  ; retract clear of plate",
        f"G0 Z{cfg.safe_z_mm}",
        "",
        "; ---- 3. probe Y (plate's back edge) ----",
        f"G0 Y{cfg.edge_clearance_mm}",
        f"G0 Z-{cfg.plate_thickness_mm / 2}",
        f"G38.2 Y-{cfg.max_probe_distance_mm} F{F}",
        # Tool's FRONT side now touching plate's back edge.
        # Plate's back edge = stock's front edge = WCS Y=0.
        # Tool center is at WCS Y = +tool_radius.
        f"G10 L20 P1 Y{tool_radius}",
        f"G0 Y{cfg.edge_clearance_mm}  ; retract clear of plate",
        f"G0 Z{cfg.safe_z_mm}",
        "",
        "; ---- 4. return to new WCS origin (above stock) ----",
        f"G0 X{cfg.plate_x_offset_mm} Y{cfg.plate_y_offset_mm}",
        "; probing complete — verify with `python lessons/integration/01_inspect/grbl_inspect.py`",
    ]
    return lines


# ---------------------------------------------------------------------------
# Serial driver — runs the probe sequence on a real machine.
# Lightly testable without hardware (mockable).
# ---------------------------------------------------------------------------


def _import_pyserial():
    try:
        import serial  # type: ignore

        return serial
    except ImportError:
        sys.exit(
            "error: pyserial is not installed. Run: python -m pip install pyserial"
        )


def _read_until(ser, marker: str, timeout_s: float = 5.0) -> list[str]:
    """Read lines from `ser` until one starts with `marker` (or `error`)."""
    deadline = time.monotonic() + timeout_s
    out: list[str] = []
    buf = b""
    while time.monotonic() < deadline:
        chunk = ser.read(256)
        if chunk:
            buf += chunk
            while b"\n" in buf:
                line, _, buf = buf.partition(b"\n")
                decoded = line.decode("ascii", errors="replace").strip()
                if decoded:
                    out.append(decoded)
                if (
                    decoded.startswith(marker)
                    or decoded.startswith("error")
                    or decoded.startswith("ALARM")
                ):
                    return out
        else:
            time.sleep(0.01)
    return out


def _parse_prb(lines: list[str]) -> tuple[float, float, float, bool] | None:
    """Find a `[PRB:x,y,z:s]` line and return the parsed tuple."""
    for line in lines:
        m = re.search(r"\[PRB:(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*):(\d+)\]", line)
        if m:
            return (
                float(m.group(1)),
                float(m.group(2)),
                float(m.group(3)),
                bool(int(m.group(4))),
            )
    return None


def _send_line_and_wait(ser, line: str, timeout_s: float = 5.0) -> list[str]:
    """Send one GCode line and wait for ok/error/alarm."""
    ser.write(line.encode("ascii") + b"\n")
    return _read_until(ser, "ok", timeout_s=timeout_s)


def run_probing(
    port: str,
    baud: int,
    cfg: ProbeConfig,
) -> int:
    """Stream the probe sequence to the machine. Returns process exit code."""
    serial = _import_pyserial()
    try:
        ser = serial.Serial(port, baud, timeout=0.1)
    except Exception as e:  # noqa: BLE001
        print(f"error: could not open {port}: {e}", file=sys.stderr)
        return 2

    try:
        time.sleep(2.0)  # GRBL banner
        ser.reset_input_buffer()

        # Sanity check: current state should be Idle.
        ser.write(b"?")
        time.sleep(0.2)
        status_line = ser.readline().decode("ascii", errors="replace").strip()
        status = parse_status(status_line)
        if not status.state.lower().startswith("idle"):
            print(
                f"error: machine state is '{status.state}', expected 'Idle'. "
                f"Clear any alarms and re-position.",
                file=sys.stderr,
            )
            return 1

        # Stream the probe lines.
        for line in generate_probe_sequence(cfg):
            stripped = line.strip()
            if not stripped or stripped.startswith(";"):
                continue
            print(f"  > {stripped}")
            resp = _send_line_and_wait(ser, stripped, timeout_s=10.0)
            for r in resp:
                print(f"    < {r}")
            # If this was a G38.2, parse PRB and bail on failure.
            if stripped.startswith("G38.2"):
                prb = _parse_prb(resp)
                if prb is None:
                    print(
                        "error: probe did not return a PRB response.", file=sys.stderr
                    )
                    return 1
                if not prb[3]:
                    print(
                        f"error: probe FAILED to make contact (PRB success bit = 0). "
                        f"Check probe wire and reposition.",
                        file=sys.stderr,
                    )
                    return 1
            # Bail on alarm.
            if any(r.startswith("ALARM") for r in resp):
                print("error: machine raised ALARM during probing.", file=sys.stderr)
                return 1

        print("\nProbing complete. G54 has been written.")
        print("Verify with: python lessons/integration/01_inspect/grbl_inspect.py")
        return 0
    finally:
        ser.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--plate-thickness",
        type=float,
        required=True,
        help="Z dimension of touch plate, mm",
    )
    p.add_argument(
        "--plate-x-offset",
        type=float,
        default=0.0,
        help="X distance from probed edge to where you want WCS X=0 (mm)",
    )
    p.add_argument(
        "--plate-y-offset",
        type=float,
        default=0.0,
        help="Y distance from probed edge to where you want WCS Y=0 (mm)",
    )
    p.add_argument(
        "--tool-diameter", type=float, required=True, help="installed tool diameter, mm"
    )
    p.add_argument("--feed", type=int, default=50)
    p.add_argument("--retract", type=float, default=5.0)
    p.add_argument(
        "--max-distance",
        type=float,
        default=15.0,
        help="abort probing if no contact within this distance, mm",
    )
    p.add_argument(
        "--edge-clearance",
        type=float,
        default=8.0,
        help="how far off the plate to position before XY probes, mm",
    )
    p.add_argument("--safe-z", type=float, default=10.0)
    p.add_argument("--port", default=None)
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="print the GCode that would be sent; don't open the port",
    )
    p.add_argument(
        "--yes", action="store_true", help="skip the are-you-sure confirmation prompt"
    )
    args = p.parse_args()

    cfg = ProbeConfig(
        plate_thickness_mm=args.plate_thickness,
        plate_x_offset_mm=args.plate_x_offset,
        plate_y_offset_mm=args.plate_y_offset,
        tool_diameter_mm=args.tool_diameter,
        feed_mm_per_min=args.feed,
        retract_mm=args.retract,
        max_probe_distance_mm=args.max_distance,
        safe_z_mm=args.safe_z,
        edge_clearance_mm=args.edge_clearance,
    )

    sequence = generate_probe_sequence(cfg)

    print("Probing sequence to be sent:\n")
    for line in sequence:
        print(f"  {line}")
    print()

    if args.dry_run:
        print("(--dry-run: not sending. Use without --dry-run to execute.)")
        return 0

    port = args.port or os.environ.get("CNC_PORT")
    if not port:
        print("error: --port required (or set CNC_PORT env var).", file=sys.stderr)
        return 2

    if not args.yes:
        try:
            ans = (
                input(
                    "Send to machine? Spindle is about to move. "
                    "Make sure tool is positioned above plate center. [y/N]: "
                )
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt):
            print("\nABORTED.", file=sys.stderr)
            return 1
        if ans not in ("y", "yes"):
            print("ABORTED.", file=sys.stderr)
            return 1

    return run_probing(port, args.baud, cfg)


if __name__ == "__main__":
    sys.exit(main())
