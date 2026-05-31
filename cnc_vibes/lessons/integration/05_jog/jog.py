#!/usr/bin/env python3
"""Interactive xbox-controller + keyboard jogger with inline Z-probe.

One screen, one process. Reads an xbox controller (if plugged in) or the
keyboard (fallback) and translates inputs into GRBL jog commands ($J=)
streamed over USB serial or raw TCP (Grbl_ESP32 port 23). One button
runs an auto Z-probe inline; another cancels any in-flight motion.

Replaces Candle's 50mm-limited Z-probe and the round-trip to a desktop
sender for routine jogging.

Standalone Python tool. No LLM dependency. Run from any shell.

Usage:
  python cnc.py jog --print-map                # show the button map and exit
  python cnc.py jog --auto                     # mDNS-discover machine + go
  python cnc.py jog --telnet 192.168.4.116     # explicit telnet
  python cnc.py jog --port /dev/cu.usbserial-X # explicit serial
  python cnc.py jog --auto --no-controller     # keyboard-only

Exit codes:
  0 — clean exit (Back / q)
  1 — runtime error (machine in Alarm, probe failed, transport dropped)
  2 — bad inputs / connection failure
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

LESSON_DIR = Path(__file__).resolve().parent
REPO_ROOT = LESSON_DIR.parent.parent.parent

# Reuse the transport + parser bits already living in sibling lessons.
sys.path.insert(0, str(REPO_ROOT / "lessons" / "integration" / "01_inspect"))
sys.path.insert(
    0, str(REPO_ROOT / "lessons" / "integration" / "04_interactive_laser_cal")
)
sys.path.insert(0, str(REPO_ROOT / "scripts"))


# ---------------------------------------------------------------------------
# Defaults / constants
# ---------------------------------------------------------------------------

DEFAULT_STEP_MM = 1.0
DEFAULT_BASE_FEED = 1500  # mm/min — middle of the road for jog
DEFAULT_FAST_MULT = 5.0
DEFAULT_SLOW_MULT = 0.1
DEFAULT_DEADZONE = 0.15  # stick deflection below this = noop
DEFAULT_TICK_HZ = 20  # event loop rate for analog jog
HOLD_HOME_MS = 1000  # how long Y / H must be held to fire $H

# GRBL realtime bytes
RT_JOG_CANCEL = b"\x85"
RT_SOFT_RESET = b"\x18"
RT_STATUS = b"?"


# ---------------------------------------------------------------------------
# Pure-function core — fully testable without controller / serial / terminal
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JogSettings:
    """Static settings the translator needs to compute jog vectors."""

    step_mm: float = DEFAULT_STEP_MM
    base_feed: int = DEFAULT_BASE_FEED
    fast_mult: float = DEFAULT_FAST_MULT
    slow_mult: float = DEFAULT_SLOW_MULT
    deadzone: float = DEFAULT_DEADZONE
    tick_hz: int = DEFAULT_TICK_HZ


@dataclass(frozen=True)
class ProbeConfig:
    """All the knobs for the inline Z-probe sequence."""

    max_mm: float = 250.0
    feed_fast: int = 200
    feed_slow: int = 25
    retract_mm: float = 2.0
    plate_mm: float = 0.0
    set_wcs: bool = True
    two_stage: bool = True


@dataclass(frozen=True)
class ControllerSnapshot:
    """One tick's worth of controller state. All buttons boolean, all axes
    normalized to [-1, 1] (sticks) or [0, 1] (triggers)."""

    left_x: float = 0.0
    left_y: float = 0.0  # already corrected: positive = "up" = +Y machine
    right_y: float = 0.0  # positive = "up" = +Z machine
    rt: float = 0.0
    a: bool = False
    b: bool = False
    x: bool = False
    y: bool = False
    lb: bool = False
    rb: bool = False
    back: bool = False
    start: bool = False
    dpad_up: bool = False
    dpad_down: bool = False
    dpad_left: bool = False
    dpad_right: bool = False


@dataclass(frozen=True)
class JogCommand:
    """One thing for the dispatcher to do this tick.

    `kind` is the discriminator. The other fields are interpreted per-kind:
      jog        : (dx, dy, dz, feed) — incremental mm + feed mm/min
      cancel     : (no extra fields) — emit 0x85 realtime
      probe      : (no extra fields) — run build_probe_sequence
      home       : (no extra fields) — send $H
      zero_wcs   : (no extra fields) — send G10 L20 P1 X0 Y0 Z0
      reprint    : (no extra fields) — re-print the button map
      exit       : (no extra fields) — clean shutdown
      noop       : (no extra fields) — explicit do-nothing
    """

    kind: str = "noop"
    dx: float = 0.0
    dy: float = 0.0
    dz: float = 0.0
    feed: int = 0


NOOP = JogCommand(kind="noop")


@dataclass
class TranslatorState:
    """Edge-detection + timer memory threaded through translate_controller().

    Kept mutable for ergonomic in-place updates; tests construct fresh ones.
    """

    prev_a: bool = False
    prev_b: bool = False
    prev_x: bool = False
    prev_y: bool = False
    prev_back: bool = False
    prev_start: bool = False
    prev_dpad: tuple[bool, bool, bool, bool] = (False, False, False, False)  # u/d/l/r
    prev_stick_active: bool = False
    y_pressed_at_ms: int | None = None
    home_fired_this_hold: bool = False
    kb_h_pressed_at_ms: int | None = None
    kb_home_fired_this_hold: bool = False


def _feed_with_modifiers(
    base: int, settings: JogSettings, slow: bool, fast_amt: float
) -> int:
    """Apply slow / fast modifiers to the base feedrate. slow wins over fast."""
    if slow:
        return max(1, int(round(base * settings.slow_mult)))
    if fast_amt > 0.0:
        # Linear blend: 0 → 1.0×, 1.0 → fast_mult×
        scale = 1.0 + (settings.fast_mult - 1.0) * max(0.0, min(1.0, fast_amt))
        return max(1, int(round(base * scale)))
    return base


def translate_controller(
    state: TranslatorState,
    snap: ControllerSnapshot,
    settings: JogSettings,
    now_ms: int,
) -> tuple[TranslatorState, list[JogCommand]]:
    """Pure: snapshot + prev-state -> (new-state, commands).

    Edge detection: button-press commands fire on the leading edge
    (False -> True). Analog sticks fire jog commands every tick they're
    out of deadzone; sticks back in deadzone fire a single Cancel.
    """
    cmds: list[JogCommand] = []
    # Copy mutable state for return; caller does not retain `state`
    new = TranslatorState(
        prev_a=snap.a,
        prev_b=snap.b,
        prev_x=snap.x,
        prev_y=snap.y,
        prev_back=snap.back,
        prev_start=snap.start,
        prev_dpad=(snap.dpad_up, snap.dpad_down, snap.dpad_left, snap.dpad_right),
        prev_stick_active=state.prev_stick_active,  # updated below
        y_pressed_at_ms=state.y_pressed_at_ms,
        home_fired_this_hold=state.home_fired_this_hold,
        kb_h_pressed_at_ms=state.kb_h_pressed_at_ms,
        kb_home_fired_this_hold=state.kb_home_fired_this_hold,
    )

    # --- discrete action buttons (leading edge) ---
    if snap.a and not state.prev_a:
        cmds.append(JogCommand(kind="probe"))
    if snap.b and not state.prev_b:
        cmds.append(JogCommand(kind="cancel"))
    if snap.x and not state.prev_x:
        cmds.append(JogCommand(kind="zero_wcs"))
    if snap.back and not state.prev_back:
        cmds.append(JogCommand(kind="exit"))
    if snap.start and not state.prev_start:
        cmds.append(JogCommand(kind="reprint"))

    # --- Y / Home: hold-1s semantics ---
    if snap.y and not state.prev_y:
        new.y_pressed_at_ms = now_ms
        new.home_fired_this_hold = False
    elif snap.y and state.y_pressed_at_ms is not None:
        held_ms = now_ms - state.y_pressed_at_ms
        if held_ms >= HOLD_HOME_MS and not state.home_fired_this_hold:
            cmds.append(JogCommand(kind="home"))
            new.home_fired_this_hold = True
    elif not snap.y:
        new.y_pressed_at_ms = None
        new.home_fired_this_hold = False

    # --- analog stick jog ---
    slow = snap.lb
    fast = snap.rt
    sx = snap.left_x if abs(snap.left_x) >= settings.deadzone else 0.0
    sy = snap.left_y if abs(snap.left_y) >= settings.deadzone else 0.0
    sz = snap.right_y if abs(snap.right_y) >= settings.deadzone else 0.0
    stick_active = sx != 0.0 or sy != 0.0 or sz != 0.0

    if stick_active:
        feed = _feed_with_modifiers(settings.base_feed, settings, slow, fast)
        # Per-tick travel distance for continuous jog: feed (mm/min) / 60s * tick_period.
        # Multiply by stick magnitude so partial deflection = slower travel within
        # the same feedrate (GRBL plans at F; magnitude scales distance per tick).
        tick_period_s = 1.0 / settings.tick_hz
        dist_per_axis = feed / 60.0 * tick_period_s * 2.0  # ×2 = small overlap
        cmds.append(
            JogCommand(
                kind="jog",
                dx=sx * dist_per_axis,
                dy=sy * dist_per_axis,
                dz=sz * dist_per_axis,
                feed=feed,
            )
        )
    elif state.prev_stick_active:
        # Stick just returned to deadzone — drain queued jog
        cmds.append(JogCommand(kind="cancel"))
    new.prev_stick_active = stick_active

    # --- D-pad step jog (leading edge, one step per press) ---
    feed = _feed_with_modifiers(settings.base_feed, settings, slow, fast)
    dpad_now = (snap.dpad_up, snap.dpad_down, snap.dpad_left, snap.dpad_right)
    for i, (was, now) in enumerate(zip(state.prev_dpad, dpad_now)):
        if now and not was:
            # u, d, l, r
            if snap.rb:
                # RB + dpad ↑↓ = Z step
                if i == 0:  # up
                    cmds.append(JogCommand(kind="jog", dz=+settings.step_mm, feed=feed))
                elif i == 1:  # down
                    cmds.append(JogCommand(kind="jog", dz=-settings.step_mm, feed=feed))
            else:
                if i == 0:  # up
                    cmds.append(JogCommand(kind="jog", dy=+settings.step_mm, feed=feed))
                elif i == 1:  # down
                    cmds.append(JogCommand(kind="jog", dy=-settings.step_mm, feed=feed))
                elif i == 2:  # left
                    cmds.append(JogCommand(kind="jog", dx=-settings.step_mm, feed=feed))
                elif i == 3:  # right
                    cmds.append(JogCommand(kind="jog", dx=+settings.step_mm, feed=feed))

    return new, cmds


# Keyboard map: lowercase = base feed, UPPERCASE = slow (×0.1). Arrows = Z.
# H must be uppercase (deliberate shift) to mean HOME — same shift-as-confirm
# convention used for the slow modifier; documents "scary actions need shift".
_KB_AXIS_KEYS = {
    "w": (0, +1, 1.0),
    "s": (0, -1, 1.0),
    "a": (-1, 0, 1.0),
    "d": (+1, 0, 1.0),
    "W": (0, +1, DEFAULT_SLOW_MULT),
    "S": (0, -1, DEFAULT_SLOW_MULT),
    "A": (-1, 0, DEFAULT_SLOW_MULT),
    "D": (+1, 0, DEFAULT_SLOW_MULT),
}
# Up / down arrows (ANSI escape sequences delivered by termios cbreak)
KB_ARROW_UP = "\x1b[A"
KB_ARROW_DOWN = "\x1b[B"
KB_ESC = "\x1b"


def translate_keyboard(key: str, settings: JogSettings) -> JogCommand:
    """Pure: one keystroke -> one command. Empty/unknown keys -> noop.

    Keyboard is tap-to-step only (most terminals don't deliver key-release).
    UPPERCASE letters apply the slow modifier; H (uppercase) is HOME.
    """
    if not key:
        return NOOP

    # Bare Escape: cancel. Arrow escapes are handled before this check.
    if key == KB_ARROW_UP:
        feed = settings.base_feed
        return JogCommand(kind="jog", dz=+settings.step_mm, feed=feed)
    if key == KB_ARROW_DOWN:
        feed = settings.base_feed
        return JogCommand(kind="jog", dz=-settings.step_mm, feed=feed)
    if key == KB_ESC:
        return JogCommand(kind="cancel")

    if key in _KB_AXIS_KEYS:
        ux, uy, mult = _KB_AXIS_KEYS[key]
        feed = max(1, int(round(settings.base_feed * mult)))
        return JogCommand(
            kind="jog",
            dx=ux * settings.step_mm,
            dy=uy * settings.step_mm,
            feed=feed,
        )

    if key == "p":
        return JogCommand(kind="probe")
    if key == "0":
        return JogCommand(kind="zero_wcs")
    if key == "H":
        return JogCommand(kind="home")
    if key == "q":
        return JogCommand(kind="exit")
    if key == "?":
        return JogCommand(kind="reprint")

    return NOOP


def build_probe_sequence(cfg: ProbeConfig) -> list[str]:
    """Return the happy-path GCode lines for a Z-probe.

    The runtime sends these one-by-one, parsing the [PRB:...] response
    after each G38.2 and aborting on no-contact. Retracts are issued as
    G91 increments so we don't need to know the touched Z up front.
    """
    lines: list[str] = []
    # First (fast) probe
    lines.append(f"G38.2 Z-{cfg.max_mm:.3f} F{cfg.feed_fast}")
    if cfg.two_stage:
        # Retract above touch point, then slow re-touch
        lines += [
            "G91",
            f"G0 Z{cfg.retract_mm:.3f}",
            f"G38.2 Z-{cfg.retract_mm * 2:.3f} F{cfg.feed_slow}",
            "G90",
        ]
    if cfg.set_wcs:
        # Set WCS Z=plate_mm at the current (touched) position. If plate_mm=0,
        # WCS Z=0 lands at the touched surface (no touch plate).
        lines.append(f"G10 L20 P1 Z{cfg.plate_mm:.3f}")
    # Final clearance retract
    lines += [
        "G91",
        f"G0 Z{cfg.retract_mm:.3f}",
        "G90",
    ]
    return lines


BUTTON_MAP = """\
─── MOTION ──────────────────────────────────────────────────────
  Xbox left stick (analog)      Keyboard W A S D       X / Y
  Xbox right stick Y            Keyboard ↑ / ↓         Z
  Xbox D-pad ←→↑↓               (same WASD = step)     X/Y step
  Xbox RB + D-pad ↑↓            (no kb equivalent)     Z step

─── SPEED MODIFIERS ─────────────────────────────────────────────
  Xbox LB (held)                Keyboard SHIFT+letter  slow ×0.1
  (none)                        lowercase              normal feed
  Xbox RT (analog)              (no kb equivalent)     fast (×N)

─── ACTIONS ─────────────────────────────────────────────────────
  Xbox A                        Keyboard p             Z-PROBE start
  Xbox B                        Keyboard Esc           CANCEL motion / probe
  Xbox X                        Keyboard 0             zero WCS at current pos
  Xbox Y (hold 1s)              Keyboard H (capital)   HOME ($H)

─── SESSION ─────────────────────────────────────────────────────
  Xbox Back / Select            Keyboard q             exit cleanly
  Xbox Start                    Keyboard ?             reprint this map

Keyboard limitation: most terminals deliver key-press but not key-release,
so keyboard motion is tap-to-step (one --step-mm per press). The xbox
controller supports continuous analog jog via the sticks AND step jog via
the D-pad.
"""


def render_button_map() -> str:
    """Return the button-map ASCII for printing at session start / --print-map."""
    return BUTTON_MAP


# ---------------------------------------------------------------------------
# I/O layer — controller, keyboard, transport. NOT covered by unit tests.
# ---------------------------------------------------------------------------


class ControllerInput:
    """Polls a pygame.joystick at the configured tick rate."""

    def __init__(self):
        # Headless: don't open an X window on the Pi.
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        try:
            import pygame  # type: ignore
        except ImportError:
            raise RuntimeError(
                "pygame is not installed. Run: python -m pip install 'pygame>=2.5'"
            )
        self._pygame = pygame
        pygame.init()
        pygame.joystick.init()
        if pygame.joystick.get_count() == 0:
            pygame.quit()
            raise RuntimeError("no joystick detected")
        self.js = pygame.joystick.Joystick(0)
        self.js.init()
        self.name = self.js.get_name()

    def poll(self) -> ControllerSnapshot:
        self._pygame.event.pump()
        js = self.js
        # Xbox controller via SDL: axes 0=LX, 1=LY, 2=LT, 3=RX, 4=RY, 5=RT
        # Buttons (XInput on Linux/Windows): 0=A, 1=B, 2=X, 3=Y,
        #   4=LB, 5=RB, 6=Back, 7=Start, 8=LStick, 9=RStick
        # Hat 0 = D-pad: (x, y) where y is +1 up, -1 down

        def axis(i: int) -> float:
            try:
                return float(js.get_axis(i))
            except Exception:
                return 0.0

        def button(i: int) -> bool:
            try:
                return bool(js.get_button(i))
            except Exception:
                return False

        hat = (0, 0)
        try:
            hat = js.get_hat(0)
        except Exception:
            pass

        # Y axes are inverted: SDL gives +1 = stick pulled DOWN. Invert so
        # +1 = stick UP = +Y / +Z machine, which matches operator intuition.
        return ControllerSnapshot(
            left_x=axis(0),
            left_y=-axis(1),
            right_y=-axis(4),
            rt=(axis(5) + 1.0) / 2.0,  # SDL triggers are -1..+1; remap to 0..1
            a=button(0),
            b=button(1),
            x=button(2),
            y=button(3),
            lb=button(4),
            rb=button(5),
            back=button(6),
            start=button(7),
            dpad_up=hat[1] > 0,
            dpad_down=hat[1] < 0,
            dpad_left=hat[0] < 0,
            dpad_right=hat[0] > 0,
        )

    def close(self) -> None:
        try:
            self._pygame.joystick.quit()
            self._pygame.quit()
        except Exception:
            pass


class KeyboardInput:
    """Non-blocking single-keystroke input via termios cbreak + select.

    Returns one key per poll() call (or "" when no key is pending). Handles
    the multi-byte ANSI escape sequences for arrow keys by buffering.
    """

    def __init__(self):
        if not sys.stdin.isatty():
            raise RuntimeError("stdin is not a TTY; keyboard input requires a terminal")
        import termios
        import tty

        self._termios = termios
        self._tty = tty
        self.fd = sys.stdin.fileno()
        self._old = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)

    def poll(self) -> str:
        import select

        # Read up to a small buffer; lets us pick up arrow-key escape sequences
        # (3 bytes) in one go without waiting for more.
        rlist, _, _ = select.select([self.fd], [], [], 0)
        if not rlist:
            return ""
        ch = os.read(self.fd, 8).decode("utf-8", errors="replace")
        return ch

    def close(self) -> None:
        try:
            self._termios.tcsetattr(self.fd, self._termios.TCSADRAIN, self._old)
        except Exception:
            pass


def _open_transport(args):
    """Open the GRBL transport. Reuses TelnetTransport from interactive_cal."""
    from interactive_cal import TelnetTransport  # type: ignore

    if args.telnet:
        host, _, port_str = args.telnet.partition(":")
        tcp_port = int(port_str) if port_str else 23
        return TelnetTransport(host, tcp_port, timeout=2.0)
    try:
        import serial  # type: ignore
    except ImportError:
        sys.exit("error: pyserial not installed. Run: python -m pip install pyserial")
    return serial.Serial(args.port, args.baud, timeout=0.1)


def _resolve_transport(args) -> tuple[object, str]:
    """Return (open_transport, human_label). Handles --auto discovery.

    Mutates args.telnet on auto-discovery success so the downstream
    _open_transport sees the discovered host.
    """
    if args.auto:
        from find_cnc import discover  # type: ignore

        print("auto: scanning mDNS / SSDP for Grbl_ESP32...", file=sys.stderr)
        hits = discover(timeout=5.0, first_only=True, probe=True)
        if not hits:
            sys.exit("auto-discovery found no machines. Pass --telnet HOST or --port.")
        args.telnet = hits[0].ip
        print(
            f"auto: using {hits[0].ip} ({hits[0].hostname or 'unnamed'})",
            file=sys.stderr,
        )
    if not args.telnet and not args.port:
        args.port = os.environ.get("CNC_PORT")
    if not args.telnet and not args.port:
        sys.exit(
            "error: no transport. Pass --auto, --telnet HOST, --port DEV, "
            "or set CNC_PORT."
        )
    ser = _open_transport(args)
    label = f"telnet {args.telnet}" if args.telnet else f"serial {args.port}"
    return ser, label


# ---------------------------------------------------------------------------
# GRBL send helpers — small wrappers around interactive_cal._send_line so we
# can also issue realtime bytes without a newline.
# ---------------------------------------------------------------------------


def _send_line(ser, line: str, timeout_s: float = 5.0) -> list[str]:
    from interactive_cal import _send_line as base_send  # type: ignore

    return base_send(ser, line, timeout_s=timeout_s)


def _send_realtime(ser, byte: bytes) -> None:
    """Write one realtime byte (no newline)."""
    ser.write(byte)


def _read_status(ser):
    from interactive_cal import _read_status as base_read_status  # type: ignore

    return base_read_status(ser)


def _parse_prb(lines: list[str]) -> tuple[float, float, float, bool] | None:
    """Re-implementation of probe_corner._parse_prb to avoid the heavy import."""
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


# ---------------------------------------------------------------------------
# Probe runner — drives build_probe_sequence on a real transport.
# ---------------------------------------------------------------------------


def run_probe(ser, cfg: ProbeConfig) -> int:
    """Execute the probe sequence. Returns 0 on success, 1 on failure."""
    state = _read_status(ser)
    if state.lower().startswith("alarm"):
        print(
            f"  probe REFUSED: machine in Alarm ({state}). Send $X to unlock.",
            file=sys.stderr,
        )
        return 1
    if not state.lower().startswith("idle"):
        print(
            f"  probe REFUSED: machine state is '{state}', expected Idle.",
            file=sys.stderr,
        )
        return 1

    print(
        f"  probe: max={cfg.max_mm}mm fast={cfg.feed_fast} slow={cfg.feed_slow} "
        f"plate={cfg.plate_mm}mm two_stage={cfg.two_stage} set_wcs={cfg.set_wcs}"
    )
    last_prb_z: float | None = None
    fast_prb_z: float | None = None
    for line in build_probe_sequence(cfg):
        print(f"  > {line}")
        resp = _send_line(ser, line, timeout_s=120.0)
        for r in resp:
            print(f"    < {r}")
        if line.startswith("G38.2"):
            prb = _parse_prb(resp)
            if prb is None:
                print("  probe: no PRB response — aborting", file=sys.stderr)
                return 1
            if not prb[3]:
                print(
                    f"  probe: no contact within {cfg.max_mm}mm (PRB:0). "
                    f"Reposition or extend --probe-max-mm.",
                    file=sys.stderr,
                )
                return 1
            if fast_prb_z is None:
                fast_prb_z = prb[2]
            last_prb_z = prb[2]
        if any(r.startswith("ALARM") for r in resp):
            print("  probe: machine raised ALARM — aborting", file=sys.stderr)
            return 1
    if last_prb_z is not None:
        repeat = ""
        if fast_prb_z is not None and cfg.two_stage:
            delta = last_prb_z - fast_prb_z
            repeat = f"  (slow vs fast delta: {delta:+.3f}mm)"
        print(f"  probe OK: touched at machine Z {last_prb_z:.3f}{repeat}")
        if cfg.set_wcs:
            print(f"  WCS Z=0 set at touched + {cfg.plate_mm:.3f}mm")
    return 0


# ---------------------------------------------------------------------------
# Event loop + dispatch
# ---------------------------------------------------------------------------


def _dispatch(cmd: JogCommand, ser, probe_cfg: ProbeConfig) -> bool:
    """Apply one command. Returns False if the session should exit."""
    if cmd.kind == "noop":
        return True
    if cmd.kind == "exit":
        return False
    if cmd.kind == "reprint":
        print(render_button_map())
        return True
    if cmd.kind == "cancel":
        _send_realtime(ser, RT_JOG_CANCEL)
        return True
    if cmd.kind == "jog":
        parts = []
        if cmd.dx != 0.0:
            parts.append(f"X{cmd.dx:.3f}")
        if cmd.dy != 0.0:
            parts.append(f"Y{cmd.dy:.3f}")
        if cmd.dz != 0.0:
            parts.append(f"Z{cmd.dz:.3f}")
        if not parts:
            return True
        line = "$J=G91 " + " ".join(parts) + f" F{cmd.feed}"
        ser.write(line.encode("ascii") + b"\n")
        # GRBL acks $J= with "ok" — drain quickly so the buffer doesn't fill.
        # Don't print every jog ack; that'd flood the console.
        return True
    if cmd.kind == "probe":
        run_probe(ser, probe_cfg)
        return True
    if cmd.kind == "home":
        print("  > $H")
        resp = _send_line(ser, "$H", timeout_s=60.0)
        for r in resp:
            print(f"    < {r}")
        return True
    if cmd.kind == "zero_wcs":
        print("  > G10 L20 P1 X0 Y0 Z0")
        resp = _send_line(ser, "G10 L20 P1 X0 Y0 Z0", timeout_s=5.0)
        for r in resp:
            print(f"    < {r}")
        return True
    return True


def run_event_loop(
    input_dev, ser, settings: JogSettings, probe_cfg: ProbeConfig, use_controller: bool
) -> int:
    """Tick at settings.tick_hz, poll input, translate, dispatch. Blocks until
    an Exit command is emitted or KeyboardInterrupt."""
    tick_period = 1.0 / settings.tick_hz
    state = TranslatorState()
    try:
        while True:
            t0 = time.monotonic()
            now_ms = int(t0 * 1000)
            if use_controller:
                snap = input_dev.poll()
                state, cmds = translate_controller(state, snap, settings, now_ms)
            else:
                key = input_dev.poll()
                cmds = [translate_keyboard(key, settings)] if key else []
            for c in cmds:
                if not _dispatch(c, ser, probe_cfg):
                    return 0
            # Sleep to maintain tick rate
            elapsed = time.monotonic() - t0
            if elapsed < tick_period:
                time.sleep(tick_period - elapsed)
    except KeyboardInterrupt:
        print("\n(KeyboardInterrupt — exiting)", file=sys.stderr)
        return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cnc.py jog",
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Transport
    p.add_argument("--port", default=None, help="serial port (or CNC_PORT env)")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument(
        "--telnet",
        default=None,
        help="raw TCP host[:port] for Grbl_ESP32 (default port 23)",
    )
    p.add_argument(
        "--auto",
        action="store_true",
        help="mDNS / SSDP discover the machine and use first hit",
    )
    # Input
    p.add_argument(
        "--no-controller",
        action="store_true",
        help="force keyboard input even if a joystick is plugged in",
    )
    p.add_argument(
        "--print-map",
        action="store_true",
        help="print the button map and exit (no machine required)",
    )
    # Jog settings
    p.add_argument(
        "--step-mm",
        type=float,
        default=DEFAULT_STEP_MM,
        help=f"step size per D-pad/keyboard press (default {DEFAULT_STEP_MM})",
    )
    p.add_argument(
        "--feed",
        type=int,
        default=DEFAULT_BASE_FEED,
        help=f"base jog feed mm/min (default {DEFAULT_BASE_FEED})",
    )
    p.add_argument(
        "--fast-mult",
        type=float,
        default=DEFAULT_FAST_MULT,
        help=f"max multiplier when RT held (default {DEFAULT_FAST_MULT})",
    )
    p.add_argument(
        "--slow-mult",
        type=float,
        default=DEFAULT_SLOW_MULT,
        help=f"multiplier when LB held / SHIFT+letter (default {DEFAULT_SLOW_MULT})",
    )
    p.add_argument(
        "--deadzone",
        type=float,
        default=DEFAULT_DEADZONE,
        help=f"stick deadzone (0-1, default {DEFAULT_DEADZONE})",
    )
    # Probe settings
    p.add_argument(
        "--probe-max-mm",
        type=float,
        default=250.0,
        help="max Z travel during probe (default 250mm)",
    )
    p.add_argument(
        "--probe-feed-fast",
        type=int,
        default=200,
        help="feed mm/min for initial probe approach (default 200)",
    )
    p.add_argument(
        "--probe-feed-slow",
        type=int,
        default=25,
        help="feed mm/min for precision re-touch (default 25)",
    )
    p.add_argument(
        "--probe-retract-mm",
        type=float,
        default=2.0,
        help="retract between fast and slow probe (default 2mm)",
    )
    p.add_argument(
        "--probe-plate-mm",
        type=float,
        default=0.0,
        help="touch-plate thickness; WCS Z is set to this value (default 0)",
    )
    p.add_argument(
        "--probe-no-set-wcs",
        action="store_true",
        help="probe but skip the G10 L20 P1 Z write",
    )
    p.add_argument(
        "--probe-one-stage",
        action="store_true",
        help="skip the slow precision re-touch (one G38.2 only)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.print_map:
        print(render_button_map())
        return 0

    if args.telnet and args.port:
        print("error: --telnet and --port are mutually exclusive", file=sys.stderr)
        return 2

    settings = JogSettings(
        step_mm=args.step_mm,
        base_feed=args.feed,
        fast_mult=args.fast_mult,
        slow_mult=args.slow_mult,
        deadzone=args.deadzone,
    )
    probe_cfg = ProbeConfig(
        max_mm=args.probe_max_mm,
        feed_fast=args.probe_feed_fast,
        feed_slow=args.probe_feed_slow,
        retract_mm=args.probe_retract_mm,
        plate_mm=args.probe_plate_mm,
        set_wcs=not args.probe_no_set_wcs,
        two_stage=not args.probe_one_stage,
    )

    # Open transport
    try:
        ser, label = _resolve_transport(args)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"error opening transport: {e}", file=sys.stderr)
        return 2
    # Telnet: no reset; Serial: wait for boot banner
    if not args.telnet:
        time.sleep(5.0)
    try:
        ser.reset_input_buffer()
    except Exception:
        pass

    # Choose input device
    use_controller = False
    input_dev = None
    if not args.no_controller:
        try:
            input_dev = ControllerInput()
            use_controller = True
            print(f"controller: {input_dev.name}", file=sys.stderr)
        except RuntimeError as e:
            print(f"controller: {e} — falling back to keyboard", file=sys.stderr)
    if input_dev is None:
        try:
            input_dev = KeyboardInput()
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            try:
                ser.close()
            except Exception:
                pass
            return 2

    # Print banner
    print(render_button_map())
    print(f"transport: {label}")
    print(f"input: {'controller' if use_controller else 'keyboard (tap-to-step)'}")
    print(
        f"jog: step={settings.step_mm}mm feed={settings.base_feed} "
        f"slow×{settings.slow_mult} fast×{settings.fast_mult}"
    )
    print("")

    rc = 0
    try:
        rc = run_event_loop(input_dev, ser, settings, probe_cfg, use_controller)
    finally:
        # Safety: laser off + close transport
        try:
            ser.write(b"M5\n")
            time.sleep(0.05)
        except Exception:
            pass
        try:
            ser.close()
        except Exception:
            pass
        try:
            input_dev.close()
        except Exception:
            pass
    return rc


if __name__ == "__main__":
    sys.exit(main())
