#!/usr/bin/env python3
"""Interactive laser calibration — cut, evaluate, adjust, repeat.

Drives the laser via USB serial. Each iteration:
  1. Engraves the iteration number (using font_7seg from lesson 3b).
  2. Cuts a small test circle at the current params.
  3. Returns to safe Z and laser off.
  4. Prompts the operator to evaluate and adjust params.
  5. Saves a manifest entry recording params + position + notes.
  6. Moves to the next grid slot and loops.

Useful for dialing in Z offset / focus distance, power, feed, and pass
count when standard static calibration patterns don't give enough
resolution — especially when something about the focus assembly has
been modified.

Standalone Python tool. No LLM dependency. Run from any shell.

Manifest is saved per-run in `runs/<timestamp>.json` so each session
has its own record.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

LESSON_DIR = Path(__file__).resolve().parent
REPO_ROOT = LESSON_DIR.parent.parent.parent
RUNS_DIR = LESSON_DIR / "runs"
RUNS_DIR.mkdir(exist_ok=True)

# Import font_7seg from lesson 3b for label engraving
sys.path.insert(0, str(REPO_ROOT / "lessons" / "laser" / "02_calibration"))
from font_7seg import render_text, text_width  # noqa: E402

# Import GRBL response parser from lesson Int-01
sys.path.insert(0, str(REPO_ROOT / "lessons" / "integration" / "01_inspect"))
from grbl_inspect import parse_status  # noqa: E402


# ---- layout defaults ----
DEFAULT_SLOT_W = 30  # mm per iteration slot
DEFAULT_SLOT_H = 30
DEFAULT_SLOTS_PER_ROW = 6  # 6 * 30 = 180mm — fits the 400mm X envelope easily
DEFAULT_CIRCLE_DIA = 8  # mm — small test cut
DEFAULT_ENGRAVE_HEIGHT = 4  # mm digit height
DEFAULT_ENGRAVE_POWER_PCT = 25  # always low for the label so it doesn't cut through
DEFAULT_ENGRAVE_FEED = 1500  # mm/min for label engrave moves


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class CalParams:
    z_mm: float = 0.0  # machine Z offset to apply before cut
    power_percent: int = 100
    feed_mm_per_min: int = 400
    passes: int = 2


@dataclass
class IterationLog:
    n: int
    params: dict
    position: tuple[float, float]
    notes: str = ""
    outcome: str = "unknown"


# ---------------------------------------------------------------------------
# Pure GCode emitters — testable without serial
# ---------------------------------------------------------------------------


def grid_position(
    iter_n: int,
    origin_x: float,
    origin_y: float,
    slots_per_row: int,
    slot_w: float,
    slot_h: float,
) -> tuple[float, float]:
    """Return (x, y) for the lower-left corner of iteration N's slot."""
    col = (iter_n - 1) % slots_per_row
    row = (iter_n - 1) // slots_per_row
    return (origin_x + col * slot_w, origin_y + row * slot_h)


def emit_label_gcode(
    slot_x: float,
    slot_y: float,
    n: int,
    digit_height: float,
    power_s: int,
    feed: int,
    slot_w: float = DEFAULT_SLOT_W,
    slot_h: float = DEFAULT_SLOT_H,
) -> list[str]:
    """Engrave the iteration number ABOVE the test circle position.

    The label is centered horizontally on the slot's center; rendered with
    font_7seg from lesson 3b.
    """
    label_text = str(n)
    label_w = text_width(label_text, digit_height, spacing=1.0)
    cx = slot_x + slot_w / 2
    label_origin_x = cx - label_w / 2
    label_origin_y = slot_y + slot_h - digit_height - 2  # near top of slot
    segments = render_text(
        label_text,
        label_origin_x,
        label_origin_y,
        height=digit_height,
        spacing=1.0,
    )
    lines = [f"; iter {n} label"]
    for x1, y1, x2, y2 in segments:
        lines += [
            f"G0 X{x1:.3f} Y{y1:.3f}",
            f"M4 S{power_s}",
            f"G1 X{x2:.3f} Y{y2:.3f} F{feed}",
            "M5",
        ]
    return lines


def emit_circle_cut_gcode(
    slot_x: float,
    slot_y: float,
    circle_dia: float,
    params: CalParams,
    slot_w: float = DEFAULT_SLOT_W,
    slot_h: float = DEFAULT_SLOT_H,
) -> list[str]:
    """Cut a circle at the center of the slot at the given params."""
    r = circle_dia / 2
    cx = slot_x + slot_w / 2
    cy = slot_y + slot_h / 2 - 4  # below the label
    power_s = int(round(params.power_percent * 10))

    lines = [
        f"; iter cut: Z={params.z_mm} S={power_s} F={params.feed_mm_per_min} "
        f"P={params.passes} at ({cx:.1f}, {cy:.1f})",
        f"G0 X{cx + r:.3f} Y{cy:.3f}",
    ]
    # Apply the Z offset for this iteration. For laser cal we DO move Z to
    # test focus position — unusual for laser jobs, but the whole point here.
    if abs(params.z_mm) > 1e-6:
        lines.append(f"G0 Z{params.z_mm:.3f}")
    lines.append(f"M4 S{power_s}")
    for _ in range(params.passes):
        lines.append(
            f"G3 X{cx + r:.3f} Y{cy:.3f} I{-r:.3f} J0 F{params.feed_mm_per_min}"
        )
    lines.append("M5")
    # Return Z to baseline (0) so the next iteration's G0 XY rapid is safe.
    if abs(params.z_mm) > 1e-6:
        lines.append("G0 Z0")
    return lines


def emit_raster_patch_gcode(
    slot_x: float,
    slot_y: float,
    patch_size: float,
    params: CalParams,
    slot_w: float = DEFAULT_SLOT_W,
    slot_h: float = DEFAULT_SLOT_H,
    line_spacing_mm: float = 0.2,
) -> list[str]:
    """Raster-fill a small square patch at the iteration's params.

    Used for grayscale-engrave calibration: the operator iterates through
    power values and visually evaluates the resulting darkness on the
    material. After enough iterations, a power-vs-darkness LUT can be
    baked for phase7_raster's grayscale mode.

    Same scan pattern as phase7_raster (unidirectional horizontal, G0
    rapids between rows), so this calibration patch's behavior matches
    what the real engrave job emits.
    """
    cx = slot_x + slot_w / 2
    cy = slot_y + slot_h / 2 - 4  # below the label, mirrors the cut placement
    half = patch_size / 2
    x_start = cx - half
    x_end = cx + half
    y_top = cy + half
    y_bot = cy - half
    power_s = int(round(params.power_percent * 10))

    lines = [
        f"; iter patch: Z={params.z_mm} S={power_s} F={params.feed_mm_per_min} "
        f"P={params.passes} at ({cx:.1f}, {cy:.1f}) {patch_size}x{patch_size}mm",
        f"G0 X{x_start:.3f} Y{y_top:.3f}",
    ]
    if abs(params.z_mm) > 1e-6:
        lines.append(f"G0 Z{params.z_mm:.3f}")
    lines.append(f"M4 S{power_s}")
    lines.append(f"F{params.feed_mm_per_min}")

    # Unidirectional raster: G0 back to x_start between lines. Same pattern
    # phase7_raster uses, so the calibration matches the real engrave.
    n_lines = max(2, int(round(patch_size / line_spacing_mm)) + 1)
    for pass_n in range(params.passes):
        if params.passes > 1:
            lines.append(f"; pass {pass_n + 1} of {params.passes}")
        for i in range(n_lines):
            # Walk top-down so first line is at y_top, last at y_bot
            y = y_top - i * line_spacing_mm
            if y < y_bot - 1e-6:
                y = y_bot
            lines.append(f"G0 X{x_start:.3f} Y{y:.3f}")
            lines.append(f"G1 X{x_end:.3f} Y{y:.3f}")
    lines.append("M5")
    if abs(params.z_mm) > 1e-6:
        lines.append("G0 Z0")
    return lines


def emit_iteration_gcode(
    iter_n: int,
    origin_x: float,
    origin_y: float,
    slots_per_row: int,
    slot_w: float,
    slot_h: float,
    circle_dia: float,
    digit_height: float,
    engrave_power_s: int,
    engrave_feed: int,
    params: CalParams,
    mode: str = "cut",
    patch_size: float = 6.0,
    line_spacing_mm: float = 0.2,
) -> tuple[list[str], tuple[float, float]]:
    """Compose the full GCode for one iteration. Returns (lines, position).

    mode="cut" (default) emits a circle cut at params (Stage 1-4 cal).
    mode="engrave" emits a raster-filled patch at params (Stage 5+ cal,
    or grayscale calibration). The iteration-number label is the same
    in both modes.
    """
    slot_x, slot_y = grid_position(
        iter_n, origin_x, origin_y, slots_per_row, slot_w, slot_h
    )
    cx = slot_x + slot_w / 2
    cy = slot_y + slot_h / 2 - 4
    lines = emit_label_gcode(
        slot_x,
        slot_y,
        iter_n,
        digit_height=digit_height,
        power_s=engrave_power_s,
        feed=engrave_feed,
        slot_w=slot_w,
        slot_h=slot_h,
    )
    if mode == "engrave":
        lines.extend(
            emit_raster_patch_gcode(
                slot_x,
                slot_y,
                patch_size,
                params,
                slot_w=slot_w,
                slot_h=slot_h,
                line_spacing_mm=line_spacing_mm,
            )
        )
    else:  # "cut"
        lines.extend(
            emit_circle_cut_gcode(
                slot_x, slot_y, circle_dia, params, slot_w=slot_w, slot_h=slot_h
            )
        )
    return lines, (cx, cy)


# ---------------------------------------------------------------------------
# Serial driver
# ---------------------------------------------------------------------------


def _import_pyserial():
    try:
        import serial  # type: ignore

        return serial
    except ImportError:
        sys.exit("error: pyserial not installed. Run: python -m pip install pyserial")


# ---------------------------------------------------------------------------
# Transport: USB serial or TCP/telnet — both quack like pyserial.Serial for
# the subset of methods this script uses (.read, .readline, .write,
# .reset_input_buffer, .in_waiting, .close).
#
# The Grbl_ESP32 build on the Anolex exposes a raw-TCP listener on port 23
# (labeled "telnet" in the boot banner but no IAC negotiation). Using TCP
# avoids the boot-banner reset that happens on every USB port open.
# ---------------------------------------------------------------------------


class TelnetTransport:
    """Minimal pyserial-compatible adapter for raw TCP to a Grbl_ESP32 board."""

    def __init__(self, host: str, port: int = 23, timeout: float = 2.0):
        import socket

        self._socket_mod = socket
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self._buf = b""
        self._default_timeout = timeout

    def write(self, data: bytes) -> int:
        self.sock.sendall(data)
        return len(data)

    def read(self, n: int = 1) -> bytes:
        if self._buf:
            chunk, self._buf = self._buf[:n], self._buf[n:]
            return chunk
        try:
            return self.sock.recv(n)
        except self._socket_mod.timeout:
            return b""

    def readline(self) -> bytes:
        while b"\n" not in self._buf:
            try:
                chunk = self.sock.recv(256)
                if not chunk:
                    out, self._buf = self._buf, b""
                    return out
                self._buf += chunk
            except self._socket_mod.timeout:
                return b""
        line, _, self._buf = self._buf.partition(b"\n")
        return line + b"\n"

    def reset_input_buffer(self) -> None:
        self._buf = b""
        self.sock.setblocking(False)
        try:
            while True:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
        except (BlockingIOError, OSError):
            pass
        finally:
            self.sock.setblocking(True)
            self.sock.settimeout(self._default_timeout)

    @property
    def in_waiting(self) -> int:
        if self._buf:
            return len(self._buf)
        self.sock.setblocking(False)
        try:
            data = self.sock.recv(4096, self._socket_mod.MSG_PEEK)
            return len(data)
        except (BlockingIOError, OSError):
            return 0
        finally:
            self.sock.setblocking(True)
            self.sock.settimeout(self._default_timeout)

    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:
            pass


def _open_transport(args):
    """Return an open transport (serial or telnet), or raise."""
    if args.telnet:
        host, _, port_str = args.telnet.partition(":")
        tcp_port = int(port_str) if port_str else 23
        return TelnetTransport(host, tcp_port, timeout=2.0)
    serial = _import_pyserial()
    return serial.Serial(args.port, args.baud, timeout=0.1)


def _read_until_ok(ser, timeout_s: float = 30.0) -> list[str]:
    """Read lines until we see `ok`, `error`, or `ALARM`, or timeout."""
    deadline = time.monotonic() + timeout_s
    buf = b""
    out: list[str] = []
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
                    decoded == "ok"
                    or decoded.startswith("error")
                    or decoded.startswith("ALARM")
                ):
                    return out
        else:
            time.sleep(0.01)
    return out


def _send_line(ser, line: str, timeout_s: float = 30.0) -> list[str]:
    ser.write(line.encode("ascii") + b"\n")
    return _read_until_ok(ser, timeout_s=timeout_s)


def _send_line_checked(ser, line: str, timeout_s: float = 30.0) -> list[str]:
    """Send a line and raise RuntimeError on `error:` or `ALARM:` in the
    response. Use for setup commands where silent failure would be unsafe."""
    resp = _send_line(ser, line, timeout_s=timeout_s)
    for r in resp:
        if r.startswith("error") or r.startswith("ALARM"):
            raise RuntimeError(f"GRBL refused {line!r}: {r}")
    return resp


def _read_status(ser) -> str:
    ser.write(b"?")
    time.sleep(0.2)
    raw = ser.readline().decode("ascii", errors="replace").strip()
    s = parse_status(raw)
    return s.state


def _wait_for_idle(ser, timeout_s: float = 60.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        state = _read_status(ser)
        if state.lower().startswith("idle"):
            return True
        time.sleep(0.2)
    return False


# ---------------------------------------------------------------------------
# Pre-run safety: envelope and Z bounds
# ---------------------------------------------------------------------------


def load_machine_envelope(profile_path: Path) -> dict:
    """Read envelope_mm from a machine YAML profile. Falls back to a
    conservative default if the profile is missing or unreadable."""
    try:
        import yaml  # type: ignore
    except ImportError:
        yaml = None
    if yaml is not None and profile_path.exists():
        try:
            data = yaml.safe_load(profile_path.read_text())
            env = data.get("envelope_mm") or {}
            return {
                "x": float(env.get("x", 200)),
                "y": float(env.get("y", 200)),
                "z": float(env.get("z", 50)),
            }
        except Exception:
            pass
    return {"x": 200.0, "y": 200.0, "z": 50.0}


def check_layout_within_envelope(
    origin_x: float,
    origin_y: float,
    slot_w: float,
    slot_h: float,
    slots_per_row: int,
    max_iterations: int,
    envelope: dict,
) -> list[str]:
    """Return a list of human-readable problems with the planned grid vs
    the machine envelope. Empty list = layout fits."""
    problems: list[str] = []
    max_col = min(max_iterations, slots_per_row) - 1
    max_row = (max_iterations - 1) // slots_per_row
    max_x = origin_x + (max_col + 1) * slot_w
    max_y = origin_y + (max_row + 1) * slot_h
    if origin_x < 0 or origin_y < 0:
        problems.append(f"origin ({origin_x},{origin_y}) is negative")
    if max_x > envelope["x"]:
        problems.append(
            f"grid extends to X={max_x:.1f} but envelope X={envelope['x']:.1f}"
        )
    if max_y > envelope["y"]:
        problems.append(
            f"grid extends to Y={max_y:.1f} but envelope Y={envelope['y']:.1f}"
        )
    return problems


def check_z_bounds(z_mm: float, max_offset: float) -> str | None:
    """Return a problem string if abs(z_mm) > max_offset, else None.
    Bounds the blast radius of a typo in the Z prompt."""
    if abs(z_mm) > max_offset:
        return (
            f"Z={z_mm} exceeds safety bound ±{max_offset}mm. "
            f"Override with --max-z-offset if intentional."
        )
    return None


# ---------------------------------------------------------------------------
# Interactive prompts
# ---------------------------------------------------------------------------


def _prompt_initial_params(defaults: CalParams) -> CalParams:
    print("\n=== Initial parameters ===")
    print("Press ENTER to accept default in [brackets].")

    def ask(label, current, cast=str):
        ans = input(f"  {label} [{current}]: ").strip()
        if not ans:
            return current
        try:
            return cast(ans)
        except ValueError:
            print(f"    (invalid, keeping {current})")
            return current

    return CalParams(
        z_mm=ask("Z offset (mm, absolute)", defaults.z_mm, float),
        power_percent=ask("Power %", defaults.power_percent, int),
        feed_mm_per_min=ask("Feed mm/min", defaults.feed_mm_per_min, int),
        passes=ask("Passes", defaults.passes, int),
    )


def _prompt_evaluate_and_adjust(
    current: CalParams,
    max_z_offset: float = 10.0,
) -> tuple[CalParams | None, str, str]:
    """Prompt user to evaluate the last cut and either adjust or quit.

    Returns (next_params or None to stop, outcome, notes). Z values that
    exceed max_z_offset are rejected at the prompt rather than sent to
    the machine. EOFError / KeyboardInterrupt anywhere in the eval block
    returns (None, "eof"/"interrupt", "") so the just-fired iteration
    still gets logged before the run wraps.
    """
    try:
        print("\n--- Evaluate the cut ---")
        outcome_help = (
            "clean / incomplete / burnt / kerf-wide / kerf-narrow / abort / done"
        )
        while True:
            outcome = (
                input(f"  Outcome [{outcome_help}]: ").strip().lower() or "unknown"
            )
            if outcome in ("done", "abort", "q", "quit"):
                return None, outcome, ""
            break

        notes = input("  Notes (free-form, optional): ").strip()

        print("\n--- Adjust params for next iteration ---")
        print("  Press ENTER to keep current value. Type 'done' on any line to finish.")

        def ask_adjust(label, current, cast):
            ans = input(f"  {label} (current {current}): ").strip()
            if not ans:
                return current, False
            if ans.lower() in ("done", "abort", "q", "quit"):
                return current, True
            try:
                return cast(ans), False
            except ValueError:
                print(f"    (invalid, keeping {current})")
                return current, False

        while True:
            new_z, stop = ask_adjust("Z (mm)", current.z_mm, float)
            if stop:
                return None, "done", notes
            z_problem = check_z_bounds(new_z, max_z_offset)
            if z_problem is None:
                break
            print(f"    REJECTED: {z_problem}")
        new_p, stop = ask_adjust("Power %", current.power_percent, int)
        if stop:
            return None, "done", notes
        new_f, stop = ask_adjust("Feed mm/min", current.feed_mm_per_min, int)
        if stop:
            return None, "done", notes
        new_n, stop = ask_adjust("Passes", current.passes, int)
        if stop:
            return None, "done", notes

        return CalParams(new_z, new_p, new_f, new_n), outcome, notes
    except EOFError:
        return None, "eof", ""
    except KeyboardInterrupt:
        return None, "interrupt", ""


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def _save_manifest(manifest_path: Path, logs: list[IterationLog]):
    payload = {
        "version": 1,
        "saved_at": datetime.now().isoformat(),
        "iterations": [
            {
                "n": l.n,
                "params": l.params,
                "position": list(l.position),
                "notes": l.notes,
                "outcome": l.outcome,
            }
            for l in logs
        ],
    }
    manifest_path.write_text(json.dumps(payload, indent=2))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--port", default=None, help="serial port (or CNC_PORT env)")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument(
        "--telnet",
        default=None,
        help="Use raw-TCP transport instead of serial: 'host[:port]' (default port 23). "
        "Grbl_ESP32 listens on port 23 as labeled 'TELNET' (no IAC negotiation). "
        "Avoids the boot-banner reset that happens on every USB port open.",
    )
    p.add_argument(
        "--origin-x", type=float, default=10.0, help="X for first iteration's slot, mm"
    )
    p.add_argument(
        "--origin-y", type=float, default=10.0, help="Y for first iteration's slot, mm"
    )
    p.add_argument("--slot-w", type=float, default=DEFAULT_SLOT_W)
    p.add_argument("--slot-h", type=float, default=DEFAULT_SLOT_H)
    p.add_argument("--slots-per-row", type=int, default=DEFAULT_SLOTS_PER_ROW)
    p.add_argument("--circle-dia", type=float, default=DEFAULT_CIRCLE_DIA)
    p.add_argument(
        "--mode",
        choices=["cut", "engrave"],
        default="cut",
        help="cut (default) = circle G3 cut test for kerf/cut calibration; "
        "engrave = raster-filled patch for grayscale-engrave power calibration "
        "(operator visually evaluates the darkness produced at each S value).",
    )
    p.add_argument(
        "--patch-size",
        type=float,
        default=6.0,
        help="raster patch size (mm square) in engrave mode (default 6.0)",
    )
    p.add_argument(
        "--patch-line-spacing",
        type=float,
        default=0.20,
        help="raster line spacing (mm) in engrave mode (default 0.20)",
    )
    p.add_argument("--engrave-height", type=float, default=DEFAULT_ENGRAVE_HEIGHT)
    p.add_argument(
        "--engrave-power-percent", type=int, default=DEFAULT_ENGRAVE_POWER_PCT
    )
    p.add_argument("--start-z", type=float, default=0.0)
    # Conservative defaults: Stage 1 (Z/focus) wants low power and few passes —
    # cleaner kerf to measure and far below combustion regime even if focus is
    # way off. The material profile values (e.g. 100% / 400 / 2 for 3mm ply)
    # are the right STARTING POINT for power/feed/passes tuning AFTER focus is
    # dialed in; override these defaults explicitly when you reach Stages 2-4.
    p.add_argument("--start-power", type=int, default=50)
    p.add_argument("--start-feed", type=int, default=800)
    p.add_argument("--start-passes", type=int, default=1)
    p.add_argument(
        "--dry-run", action="store_true", help="print GCode without opening serial port"
    )
    p.add_argument(
        "--max-iterations", type=int, default=24, help="hard limit, safety stop"
    )
    p.add_argument(
        "--max-z-offset",
        type=float,
        default=10.0,
        help="reject Z values whose absolute value exceeds this (mm). "
        "Bounds the blast radius of a typo. Default 10mm.",
    )
    p.add_argument(
        "--machine-profile",
        type=Path,
        default=REPO_ROOT / "profiles" / "default.yaml",
        help="machine profile YAML for envelope check",
    )
    p.add_argument(
        "--skip-envelope-check",
        action="store_true",
        help="skip the layout-vs-envelope check (use if your profile is unrepresentative)",
    )
    args = p.parse_args()

    if args.telnet and args.port:
        print(
            "error: --telnet and --port are mutually exclusive (pick one transport)",
            file=sys.stderr,
        )
        return 2
    port = args.port or os.environ.get("CNC_PORT")
    if not args.telnet and not port and not args.dry_run:
        print(
            "error: --port or --telnet required (or set CNC_PORT, or use --dry-run)",
            file=sys.stderr,
        )
        return 2
    # Stash resolved port back into args so _open_transport picks it up
    args.port = port

    # Envelope check — runs in both dry-run and real-run so dry-run catches
    # bad layouts before you ever connect the machine.
    if not args.skip_envelope_check:
        envelope = load_machine_envelope(args.machine_profile)
        problems = check_layout_within_envelope(
            args.origin_x,
            args.origin_y,
            args.slot_w,
            args.slot_h,
            args.slots_per_row,
            args.max_iterations,
            envelope,
        )
        if problems:
            print("error: planned grid does not fit machine envelope:", file=sys.stderr)
            for p_ in problems:
                print(f"  - {p_}", file=sys.stderr)
            print(
                f"  envelope from {args.machine_profile.name}: "
                f"X={envelope['x']} Y={envelope['y']} Z={envelope['z']}",
                file=sys.stderr,
            )
            return 2

    print(f"Interactive laser calibration")
    print(f"  origin: ({args.origin_x}, {args.origin_y}) mm")
    print(f"  slots: {args.slot_w}x{args.slot_h} mm in rows of {args.slots_per_row}")
    print(f"  circle dia: {args.circle_dia} mm")
    print(f"  max iterations: {args.max_iterations}")
    print(f"  max Z offset: ±{args.max_z_offset} mm (safety bound)")
    if args.dry_run:
        print("  DRY RUN — no serial connection; printing GCode only.")

    defaults = CalParams(
        z_mm=args.start_z,
        power_percent=args.start_power,
        feed_mm_per_min=args.start_feed,
        passes=args.start_passes,
    )

    # Bound the starting Z too — refuse to start with an out-of-bounds default.
    z_problem = check_z_bounds(defaults.z_mm, args.max_z_offset)
    if z_problem:
        print(f"error: {z_problem}", file=sys.stderr)
        return 2

    if not args.dry_run:
        try:
            ans = input(
                "\nReady? Make sure: laser preflight checklist done, $32=1,\n"
                "  material clamped at origin, focus rough-set, fire safety on,\n"
                "  Z=0 is set at your desired starting focal height (NOT at the\n"
                "  material surface — Z values in this script are absolute moves).\n"
                "Press ENTER to start (Ctrl-C to abort): "
            )
            if ans.lower() in ("q", "quit", "abort"):
                return 1
        except (EOFError, KeyboardInterrupt):
            return 1

    params = defaults if args.dry_run else _prompt_initial_params(defaults)
    # Re-bound Z after the prompt (user may have entered a new value).
    z_problem = check_z_bounds(params.z_mm, args.max_z_offset)
    if z_problem:
        print(f"error: {z_problem}", file=sys.stderr)
        return 2
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    manifest_path = RUNS_DIR / f"cal_{timestamp}.json"

    logs: list[IterationLog] = []
    ser = None
    if not args.dry_run:
        try:
            ser = _open_transport(args)
            # USB serial: opening the port pulses DTR which resets the
            # ESP32, so we wait for the boot banner + WiFi association to
            # finish. Telnet: no reset, just clear any queued bytes.
            if not args.telnet:
                time.sleep(5.0)  # Grbl_ESP32 boot + WiFi takes ~3-5s
            ser.reset_input_buffer()
            # Refuse to start if GRBL is in ALARM — operator must $X or $H
            # first to acknowledge state.
            state = _read_status(ser)
            if state.lower().startswith("alarm"):
                print(
                    f"error: GRBL is in ALARM state ({state}). Resolve via $X "
                    "(unlock) or $H (home) in your sender before re-running.",
                    file=sys.stderr,
                )
                return 2
            # Order matters: laser OFF first, then mode + units + absolute.
            # Use checked sends so a refusal aborts before any motion.
            try:
                for setup in ("M5", "$32=1", "G21", "G90"):
                    _send_line_checked(ser, setup)
            except RuntimeError as e:
                print(f"error during setup: {e}", file=sys.stderr)
                return 2
        except Exception as e:  # noqa: BLE001
            transport_label = (
                f"telnet {args.telnet}" if args.telnet else f"serial {args.port}"
            )
            print(f"error opening transport ({transport_label}): {e}", file=sys.stderr)
            return 2

    try:
        for iter_n in range(1, args.max_iterations + 1):
            lines, pos = emit_iteration_gcode(
                iter_n=iter_n,
                origin_x=args.origin_x,
                origin_y=args.origin_y,
                slots_per_row=args.slots_per_row,
                slot_w=args.slot_w,
                slot_h=args.slot_h,
                circle_dia=args.circle_dia,
                digit_height=args.engrave_height,
                engrave_power_s=int(args.engrave_power_percent * 10),
                engrave_feed=DEFAULT_ENGRAVE_FEED,
                params=params,
                mode=args.mode,
                patch_size=args.patch_size,
                line_spacing_mm=args.patch_line_spacing,
            )

            print(f"\n=== Iteration {iter_n} ===")
            print(
                f"  params: Z={params.z_mm}  S={params.power_percent}%  "
                f"F={params.feed_mm_per_min}  P={params.passes}"
            )
            print(f"  position: ({pos[0]:.1f}, {pos[1]:.1f}) mm")

            if args.dry_run:
                print("  GCode that would be sent:")
                for l in lines:
                    print(f"    {l}")
            else:
                try:
                    fire = (
                        input("  Press ENTER to fire (or 'q' to quit): ")
                        .strip()
                        .lower()
                    )
                except (EOFError, KeyboardInterrupt):
                    fire = "q"
                if fire in ("q", "quit", "abort"):
                    print("ABORTED.")
                    break
                # Stream the lines
                for line in lines:
                    if not line.strip() or line.strip().startswith(";"):
                        continue
                    resp = _send_line(ser, line.strip())
                    alarm_lines = [r for r in resp if r.startswith("ALARM")]
                    error_lines = [r for r in resp if r.startswith("error")]
                    if alarm_lines or error_lines:
                        print(f"  GRBL refused '{line.strip()}'. Full response:")
                        for r in resp:
                            print(f"    {r}")
                        if alarm_lines:
                            print(
                                "  Machine is now in ALARM lockout. Send $X in your "
                                "sender to unlock before retrying."
                            )
                        return 1
                # Wait for machine to finish moving
                _wait_for_idle(ser, timeout_s=120)

            # Evaluate + adjust (skip in dry-run; keep params constant)
            if args.dry_run:
                next_params, outcome, notes = params, "dry-run", ""
            else:
                next_params, outcome, notes = _prompt_evaluate_and_adjust(
                    params, max_z_offset=args.max_z_offset
                )
            logs.append(
                IterationLog(
                    n=iter_n,
                    params=asdict(params),
                    position=pos,
                    notes=notes,
                    outcome=outcome,
                )
            )
            _save_manifest(manifest_path, logs)
            print(f"  saved iteration {iter_n} -> {manifest_path}")

            if next_params is None:
                print(f"\nFinished after {iter_n} iterations.")
                break
            params = next_params

    finally:
        if ser is not None:
            try:
                ser.write(b"M5\n")  # ensure laser off
                ser.close()
            except Exception:
                pass

    print(f"\nManifest: {manifest_path}")
    print(f"Total iterations: {len(logs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
