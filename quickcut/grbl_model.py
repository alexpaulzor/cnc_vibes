"""GRBL motion + streaming model: what speed does the head ACTUALLY reach?

Why this exists: under static M3 the diode's power is constant, so energy per
mm is power / ACTUAL speed. If the controller can't hold the commanded F, a
stroke burns deeper than the material profile says. On the MICHELS disc the S
and C of the center text (the strokes with the most G1 commands per second at
F5000) over-burned while straight strokes were fine. This module predicts that
before a cut instead of after.

It replays a G-code program through a model of:

  * the SENDER -> controller link (char-counting with an RX buffer, ping-pong
    send/response, a fixed lines/s ceiling, or "sd" = everything already on
    the controller), so a stroke whose commands arrive slower than the head
    consumes them starves the planner;
  * GRBL's PLANNER: a finite ring of N blocks, junction speeds from $11
    junction deviation (GRBL 1.1 planner.c formula), per-axis $110/$111 max
    rate and $120/$121 acceleration, and the rule that the newest planned
    block must end at rest -- so a short lookahead (few blocks, or short
    blocks) caps the cruise speed even with a perfect link;
  * GRBL's ARC segmentation: a G2/G3 line is cut into chords per $12 arc
    tolerance (mc_arc's segment count formula) and every chord is a planner
    block -- arcs shrink the STREAM, not the planner's block count;
  * spindle SYNC points: in GRBL every M3/M4/M5 state change (and an S change
    on a non-motion line) waits for the planner to drain, so each laser-on
    stroke starts and ends at rest; time stopped with the beam on is
    reported as dwell.

Velocity inside a block follows the trapezoid GRBL would execute (accelerate
at `a`, cruise at nominal, brake to the exit speed the current planner
contents allow), replanned every time a new block arrives. Not modelled: the
stepper segment buffer's few-ms lead, ESP32 task jitter, mechanical lag.

Every machine/sender value this file does not know for certain is marked
UNVERIFIED and listed in each report: the defaults are GRBL 1.1 stock values,
NOT this machine's. Feed real ones in with --dollars (a saved `$$` + `$I`
dump) or the CLI flags; nothing here should be read as calibrated until the
test plate (jigsawzall/scripts/motion_test_plate.py) agrees with it.

    python quickcut/grbl_model.py job.gcode [--dollars dump.txt] \\
        [--sender char_count|ping_pong|rate|sd] [--accel 1000] [--hotspots 10]
"""

from __future__ import annotations

import math
import re
from collections import deque
from dataclasses import dataclass, field, fields

# GRBL 1.1 planner constants (planner.c / config.h)
MINIMUM_JUNCTION_SPEED = 0.0  # mm/min in config.h; 0 => full stop at reversals
SOME_LARGE_VALUE = 1.0e38
ARC_ANGULAR_TRAVEL_EPSILON = 5e-7


# ---------------------------------------------------------------------------
# Machine + sender profiles
# ---------------------------------------------------------------------------


@dataclass
class MachineProfile:
    """GRBL settings that shape motion. Units: mm, mm/min (rates), mm/s^2."""

    junction_deviation_mm: float = 0.010  # $11
    arc_tolerance_mm: float = 0.002  # $12
    max_rate_x: float = 6000.0  # $110 mm/min
    max_rate_y: float = 6000.0  # $111 mm/min
    accel_x: float = 1000.0  # $120 mm/s^2
    accel_y: float = 1000.0  # $121 mm/s^2
    planner_blocks: int = 15  # usable blocks ($I [OPT:...,15,128] -> 15)
    rx_buffer_bytes: int = 128  # ($I [OPT:...,15,128] -> 128)
    parse_ms: float = 0.3  # controller time to parse+plan one line
    verified: set = field(default_factory=set)  # names confirmed from a dump

    _DOLLAR = {
        "11": "junction_deviation_mm",
        "12": "arc_tolerance_mm",
        "110": "max_rate_x",
        "111": "max_rate_y",
        "120": "accel_x",
        "121": "accel_y",
    }

    @classmethod
    def from_dump(cls, text: str, **overrides) -> "MachineProfile":
        """Parse a saved `$$` dump (`$11=0.010` lines) and optional `$I`
        output (`[OPT:V,15,128]` -> planner blocks, RX bytes). Any value
        found is marked verified; the rest keep their UNVERIFIED default."""
        prof = cls()
        for num, val in re.findall(r"^\s*\$(\d+)\s*=\s*([-\d.]+)", text, re.M):
            name = cls._DOLLAR.get(num)
            if name:
                setattr(prof, name, float(val))
                prof.verified.add(name)
        m = re.search(r"\[OPT:[^,\]]*,(\d+),(\d+)", text)
        if m:
            prof.planner_blocks = int(m.group(1))
            prof.rx_buffer_bytes = int(m.group(2))
            prof.verified |= {"planner_blocks", "rx_buffer_bytes"}
        for k, v in overrides.items():
            if v is not None:
                setattr(prof, k, v)
                prof.verified.add(k)
        return prof

    def unverified(self) -> list[str]:
        return [
            f.name
            for f in fields(self)
            if f.name != "verified" and f.name not in self.verified
        ]

    def describe(self) -> list[str]:
        def tag(n):
            return "" if n in self.verified else " (UNVERIFIED default)"

        return [
            f"$11 junction deviation {self.junction_deviation_mm}mm"
            f"{tag('junction_deviation_mm')}",
            f"$12 arc tolerance {self.arc_tolerance_mm}mm{tag('arc_tolerance_mm')}",
            f"$110/$111 max rate {self.max_rate_x:g}/{self.max_rate_y:g}mm/min"
            f"{tag('max_rate_x')}",
            f"$120/$121 accel {self.accel_x:g}/{self.accel_y:g}mm/s^2{tag('accel_x')}",
            f"planner {self.planner_blocks} blocks, RX {self.rx_buffer_bytes} bytes"
            f"{tag('planner_blocks')}",
            f"controller parse {self.parse_ms}ms/line{tag('parse_ms')}",
        ]


@dataclass
class SenderProfile:
    """How lines reach the controller.

    kind:
      char_count -- keep up to rx_buffer_bytes un-acked bytes in flight
                    (LightBurn / LaserGRBL / UGS "buffered" streaming)
      ping_pong  -- one line in flight; wait for each `ok`
      rate       -- char_count plus a ceiling of lines_per_s (a measured
                    sender/UI rate, e.g. a WiFi web sender)
      sd         -- program already on the controller (ESP32 SD card run):
                    no link limit, only the planner
    """

    kind: str = "char_count"
    bytes_per_s: float = 11520.0  # 115200 baud 8N1
    latency_ms: float = 2.0  # one-way host<->controller (USB ~1-16ms, WiFi more)
    lines_per_s: float = 100.0  # kind=rate only
    verified: set = field(default_factory=set)

    def describe(self) -> list[str]:
        tag = "" if self.verified else " (UNVERIFIED default)"
        if self.kind == "sd":
            return ["sender: sd (no link limit)"]
        if self.kind == "rate":
            return [f"sender: flat {self.lines_per_s:g} lines/s{tag}"]
        return [
            f"sender: {self.kind}, {self.bytes_per_s:g} bytes/s, "
            f"{self.latency_ms}ms one-way latency{tag}"
        ]


# ---------------------------------------------------------------------------
# G-code -> lines + planner blocks
# ---------------------------------------------------------------------------


@dataclass
class Block:
    line: int  # index into Program.lines
    x0: float
    y0: float
    x1: float
    y1: float
    length: float
    ux: float
    uy: float
    feed: float  # programmed mm/min (G0 -> rapid)
    rapid: bool
    laser: bool  # beam on while this block runs (M3/M4 active, not G0, S>0)
    dynamic: bool  # M4: power follows speed, so no over-burn
    s: float  # S word in effect
    stroke: int  # laser-on stroke index, -1 if laser off


@dataclass
class Line:
    text: str
    nbytes: int
    blocks: list = field(default_factory=list)  # Block indices
    sync: bool = False  # waits for planner empty (spindle change / G4)
    dwell_s: float = 0.0


@dataclass
class Program:
    lines: list
    blocks: list
    strokes: list  # per stroke: dict(name, first_block, last_block)


_WORD = re.compile(r"([A-Z])\s*([-+]?\d*\.?\d+)")


def _strip(line: str) -> str:
    line = re.sub(r"\(.*?\)", "", line)
    return line.split(";")[0].strip().upper()


def arc_segments(x0, y0, x1, y1, i, j, cw, tol, radius=None):
    """GRBL 1.1 mc_arc(): the chord endpoints a G2/G3 is cut into.
    `segments = floor(0.5*|angle|*r / sqrt(tol*(2r - tol)))`."""
    cx, cy = x0 + i, y0 + j
    r = math.hypot(i, j) if radius is None else radius
    rx, ry = -i, -j
    rtx, rty = x1 - cx, y1 - cy
    ang = math.atan2(rx * rty - ry * rtx, rx * rtx + ry * rty)
    if cw:
        if ang >= -ARC_ANGULAR_TRAVEL_EPSILON:
            ang -= 2 * math.pi
    elif ang <= ARC_ANGULAR_TRAVEL_EPSILON:
        ang += 2 * math.pi
    n = int(math.floor(abs(0.5 * ang * r) / math.sqrt(tol * (2 * r - tol))))
    pts = []
    if n:
        a0 = math.atan2(ry, rx)
        for k in range(1, n):
            a = a0 + ang * k / n
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    pts.append((x1, y1))
    return pts


def parse_program(gcode: str, machine: MachineProfile) -> Program:
    """Modal parse of absolute-mode GRBL laser G-code into lines + blocks.
    Strokes are laser-on runs (M3/M4 ... M5); a preceding `; --- etch stroke
    N` / `; --- ... ---` comment names the stroke."""
    lines, blocks, strokes = [], [], []
    x = y = 0.0
    motion = 0
    feed = 0.0
    s = 0.0
    spindle = 5  # 3, 4 or 5
    last_comment = ""
    cur_stroke = -1
    for raw in gcode.splitlines():
        text = raw.rstrip("\n")
        if text.strip().startswith(";"):
            if text.strip().startswith("; ---"):
                last_comment = text.strip("; -").strip()
            continue  # whole-line comments: assume the sender strips them
        code = _strip(text)
        if not code:
            continue
        ln = Line(text=code, nbytes=len(code) + 1)
        li = len(lines)
        lines.append(ln)
        if code.startswith("$"):
            continue
        words = _WORD.findall(code)
        g_words = [float(v) for k, v in words if k == "G"]
        m_words = [int(float(v)) for k, v in words if k == "M"]
        vals = {k: float(v) for k, v in words if k not in "GM"}
        if 91 in [int(g) for g in g_words]:
            raise ValueError("G91 incremental mode not supported by the model")
        new_spindle = spindle
        for m in m_words:
            if m in (3, 4, 5):
                new_spindle = m
        for g in g_words:
            if int(g) in (0, 1, 2, 3):
                motion = int(g)
            if int(g) == 4:
                ln.sync = True
                ln.dwell_s = vals.get("P", 0.0)
        if "F" in vals:
            feed = vals["F"]
        has_axis = "X" in vals or "Y" in vals
        is_motion = has_axis and not any(int(g) == 4 for g in g_words)
        if new_spindle != spindle:
            ln.sync = True
        if "S" in vals and vals["S"] != s and not is_motion and spindle != 5:
            ln.sync = True
        if "S" in vals:
            s = vals["S"]
        if new_spindle != spindle:
            if new_spindle in (3, 4):
                cur_stroke = len(strokes)
                strokes.append(
                    {"name": last_comment or f"path {cur_stroke + 1}",
                     "first_block": len(blocks), "last_block": len(blocks) - 1,
                     "s_on": s}  # S at beam-on = the stroke's commanded power
                )
            else:
                cur_stroke = -1
            spindle = new_spindle
        if not is_motion:
            continue
        nx, ny = vals.get("X", x), vals.get("Y", y)
        if motion in (2, 3):
            if "R" in vals:
                raise ValueError("G2/G3 R-form not supported; use I/J")
            pts = arc_segments(
                x, y, nx, ny, vals.get("I", 0.0), vals.get("J", 0.0),
                motion == 2, machine.arc_tolerance_mm,
            )
        else:
            pts = [(nx, ny)]
        rapid = motion == 0
        on = (not rapid) and spindle in (3, 4) and s > 0
        for px, py in pts:
            d = math.hypot(px - x, py - y)
            if d > 1e-6:
                b = Block(
                    line=li, x0=x, y0=y, x1=px, y1=py, length=d,
                    ux=(px - x) / d, uy=(py - y) / d,
                    feed=feed, rapid=rapid, laser=on, dynamic=spindle == 4,
                    s=s, stroke=cur_stroke if on else -1,
                )
                ln.blocks.append(len(blocks))
                blocks.append(b)
                if on:
                    strokes[cur_stroke]["last_block"] = len(blocks) - 1
            x, y = px, py
    strokes = [st for st in strokes if st["last_block"] >= st["first_block"]]
    for k, st in enumerate(strokes):
        for bi in range(st["first_block"], st["last_block"] + 1):
            if blocks[bi].stroke >= 0:
                blocks[bi].stroke = k
    return Program(lines=lines, blocks=blocks, strokes=strokes)


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------


def _axis_limit(limit_x, limit_y, ux, uy):
    """GRBL limit_value_by_axis_maximum(): the largest magnitude along unit
    vector (ux, uy) that keeps every axis within its own limit."""
    v = SOME_LARGE_VALUE
    if abs(ux) > 1e-12:
        v = min(v, limit_x / abs(ux))
    if abs(uy) > 1e-12:
        v = min(v, limit_y / abs(uy))
    return v


def _trapezoid(v0, vmax, vexit, a, d):
    """Accelerate from v0 toward vmax, brake to vexit, over distance d.
    Returns (vpeak, t1, d1, t2, d2, t3, d3)."""
    vpeak = min(vmax, math.sqrt(max(0.0, (2 * a * d + v0 * v0 + vexit * vexit) / 2)))
    if vpeak < v0:  # can't happen with consistent limits; brake straight down
        vpeak = v0
    vexit = min(vexit, vpeak)
    d1 = (vpeak * vpeak - v0 * v0) / (2 * a)
    d3 = (vpeak * vpeak - vexit * vexit) / (2 * a)
    d2 = max(0.0, d - d1 - d3)
    t1 = (vpeak - v0) / a
    t3 = (vpeak - vexit) / a
    t2 = d2 / vpeak if vpeak > 1e-12 else 0.0
    return vpeak, t1, d1, t2, d2, t3, d3


def _state_at(v0, a, prof, tau):
    """(distance, velocity) tau seconds into a trapezoid from _trapezoid()."""
    vpeak, t1, d1, t2, d2, t3, d3 = prof
    if tau <= t1:
        return v0 * tau + 0.5 * a * tau * tau, v0 + a * tau
    tau -= t1
    if tau <= t2:
        return d1 + vpeak * tau, vpeak
    tau -= t2
    tau = min(tau, t3)
    return d1 + d2 + vpeak * tau - 0.5 * a * tau * tau, vpeak - a * tau


@dataclass
class SimResult:
    program: Program
    block_time: list  # seconds spent executing each block
    block_vmin: list  # lowest speed seen inside each block (mm/s)
    block_vmax: list
    block_vexit: list  # speed leaving each block (the junction speed)
    dwell: list  # (x, y, seconds, stroke) stopped with the beam on
    total_s: float
    machine: MachineProfile
    sender: SenderProfile


class _Motion:
    """Executes planner blocks between controller events."""

    def __init__(self, prog, machine):
        self.b = prog.blocks
        self.m = machine
        n = len(self.b)
        self.t = 0.0
        self.k = 0  # executing block (first not completed)
        self.planned = 0  # blocks [k, planned) are in the planner
        self.s = 0.0
        self.v = 0.0
        self.max_entry_sq = [0.0] * n
        self.time = [0.0] * n
        self.vmin = [math.inf] * n
        self.vmax = [0.0] * n
        self.vexit = [0.0] * n
        self.dwell = []
        self.laser_on = False
        self.stroke = -1
        self._exit = None
        self.prev_unit = None
        self.prev_nominal = 0.0
        self.accel = [0.0] * n
        self.nominal = [0.0] * n
        for i, blk in enumerate(self.b):
            rate = (
                min(machine.max_rate_x, machine.max_rate_y)
                if blk.rapid
                else max(blk.feed, 1e-6)
            )
            self.nominal[i] = (
                min(rate, _axis_limit(machine.max_rate_x, machine.max_rate_y,
                                      blk.ux, blk.uy)) / 60.0
            )
            self.accel[i] = _axis_limit(machine.accel_x, machine.accel_y,
                                        blk.ux, blk.uy)

    def count(self):
        return self.planned - self.k

    def plan(self, i):
        """plan_buffer_line(): junction speed vs the previous block."""
        blk = self.b[i]
        nominal_sq = self.nominal[i] ** 2
        if self.count() == 0 or self.prev_unit is None:
            self.max_entry_sq[i] = 0.0
        else:
            pux, puy = self.prev_unit
            cos_t = -(pux * blk.ux + puy * blk.uy)
            if cos_t > 0.999999:
                jsq = MINIMUM_JUNCTION_SPEED ** 2
            elif cos_t < -0.999999:
                jsq = SOME_LARGE_VALUE
            else:
                jx, jy = blk.ux - pux, blk.uy - puy
                jn = math.hypot(jx, jy)
                ja = _axis_limit(self.m.accel_x, self.m.accel_y, jx / jn, jy / jn)
                sin_d2 = math.sqrt(0.5 * (1.0 - cos_t))
                jsq = max(
                    MINIMUM_JUNCTION_SPEED ** 2,
                    ja * self.m.junction_deviation_mm * sin_d2 / (1.0 - sin_d2),
                )
            self.max_entry_sq[i] = min(jsq, nominal_sq, self.prev_nominal ** 2)
        self.prev_unit = (blk.ux, blk.uy)
        self.prev_nominal = self.nominal[i]
        self.planned = i + 1
        self._exit = None

    def reset_junction(self):
        self.prev_unit = None

    def exit_speed(self):
        """Exit speed of the executing block: backward pass from the newest
        planned block (which must end at rest)."""
        if self._exit is None:
            v_sq = 0.0
            for j in range(self.planned - 1, self.k, -1):
                v_sq = min(self.max_entry_sq[j], v_sq + 2 * self.accel[j] * self.b[j].length)
            self._exit = math.sqrt(v_sq)
        return self._exit

    def _note(self, i, v):
        if v < self.vmin[i]:
            self.vmin[i] = v
        if v > self.vmax[i]:
            self.vmax[i] = v

    def advance_to(self, t_target, stop_on_complete=False):
        """Run motion until t_target (or the first block completion when
        stop_on_complete). Returns the time reached."""
        while self.t < t_target - 1e-12:
            if self.count() == 0:
                if self.laser_on:
                    self._idle(t_target - self.t)
                self.t = t_target
                break
            i = self.k
            blk = self.b[i]
            a = self.accel[i]
            vex = self.exit_speed()
            d = blk.length - self.s
            prof = _trapezoid(self.v, self.nominal[i], vex, a, d)
            T = prof[1] + prof[3] + prof[5]
            self._note(i, self.v)
            if self.t + T <= t_target + 1e-12:
                self.time[i] += T
                self.t += T
                self.v = min(vex, prof[0])
                self.vexit[i] = self.v
                self._note(i, prof[0])
                self._note(i, self.v)
                self.k += 1
                self.s = 0.0
                self._exit = None
                if stop_on_complete:
                    return self.t
            else:
                tau = t_target - self.t
                ds, v = _state_at(self.v, a, prof, tau)
                if tau > prof[1]:
                    self._note(i, prof[0])
                self.time[i] += tau
                self.s += ds
                self.v = v
                self._note(i, v)
                self.t = t_target
        return self.t

    def run_until_slot(self):
        """Advance until the executing block completes (frees a slot)."""
        return self.advance_to(math.inf, stop_on_complete=True)

    def drain(self):
        while self.count() > 0:
            self.run_until_slot()
        self.v = 0.0
        return self.t

    def _idle(self, dt):
        if self.k > 0:
            blk = self.b[self.k - 1]
            x, y = blk.x1, blk.y1
        else:
            x = y = 0.0
        self.dwell.append((x, y, dt, self.stroke))


class _Link:
    """Host side of the stream: when does line n arrive at the controller?"""

    def __init__(self, sender, machine):
        self.snd = sender
        self.rx = machine.rx_buffer_bytes
        self.inflight = deque()  # (ack_time, nbytes)
        self.link_free = 0.0
        self.last_arrival = 0.0

    def arrival(self, nbytes):
        k = self.snd.kind
        if k == "sd":
            return 0.0
        t = self.link_free
        if k == "rate":  # char-count gating plus a minimum send interval
            t = max(t, self.last_arrival + 1.0 / self.snd.lines_per_s)
        cap = 0 if k == "ping_pong" else self.rx
        while self.inflight:
            while self.inflight and self.inflight[0][0] <= t:
                self.inflight.popleft()
            used = sum(nb for _, nb in self.inflight)
            if not self.inflight or used + nbytes <= cap:
                break
            t = self.inflight[0][0]
        tx = nbytes / self.snd.bytes_per_s
        self.last_arrival = t
        self.link_free = t + tx
        self._pending = nbytes
        return t + tx + self.snd.latency_ms / 1000.0

    def ack(self, t_done):
        if self.snd.kind == "sd":
            return
        self.inflight.append((t_done + self.snd.latency_ms / 1000.0, self._pending))


def simulate(
    gcode: str,
    machine: MachineProfile | None = None,
    sender: SenderProfile | None = None,
) -> SimResult:
    machine = machine or MachineProfile()
    sender = sender or SenderProfile()
    prog = parse_program(gcode, machine)
    mo = _Motion(prog, machine)
    link = _Link(sender, machine)
    t_ctrl = 0.0
    parse_s = machine.parse_ms / 1000.0
    N = machine.planner_blocks
    for ln in prog.lines:
        t = max(t_ctrl, link.arrival(ln.nbytes))
        mo.advance_to(t)
        t += parse_s
        mo.advance_to(t)
        if ln.sync:
            t = max(t, mo.drain())
            mo.reset_junction()
            if ln.dwell_s:
                mo.advance_to(t + ln.dwell_s)
                t += ln.dwell_s
        for bi in ln.blocks:
            while mo.count() >= N:
                t = max(t, mo.run_until_slot())
            mo.advance_to(t)
            mo.plan(bi)
        t_ctrl = t
        link.ack(t)
        _update_laser(mo, prog, ln)
    t_end = mo.drain()
    for i in range(len(prog.blocks)):
        if mo.vmin[i] == math.inf:
            mo.vmin[i] = 0.0
    return SimResult(
        program=prog, block_time=mo.time, block_vmin=mo.vmin, block_vmax=mo.vmax,
        block_vexit=mo.vexit,
        dwell=mo.dwell, total_s=t_end, machine=machine, sender=sender,
    )


_SPINDLE = re.compile(r"\bM0*([345])\b")


def _update_laser(mo, prog, ln):
    """Track beam state for idle-dwell accounting: M3/M4 with S>0 turns it
    on (it fires only while moving in laser mode, but an M3 static beam
    still heats while the planner is starved between blocks), M5 off."""
    m = _SPINDLE.findall(ln.text)
    if not m:
        return
    if m[-1] == "5":
        mo.laser_on = False
        mo.stroke = -1
    else:
        mo.laser_on = True
        # stroke index = stroke of the next laser block
        nxt = next((b for b in prog.blocks[mo.planned:] if b.laser), None)
        mo.stroke = nxt.stroke if nxt is not None else -1


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


@dataclass
class StrokeStats:
    index: int
    name: str
    length_mm: float
    n_lines: int
    n_blocks: int
    feed: float  # commanded mm/min (length-weighted)
    nominal_s: float  # time at commanded F
    actual_s: float  # predicted time incl. beam-on dwell
    vmin: float  # mm/s
    dwell_s: float
    lines_per_s: float  # lines/s needed to hold commanded F
    worst_window_factor: float
    worst_window_at: tuple
    dynamic: bool

    @property
    def avg_speed(self):
        return self.length_mm / self.actual_s if self.actual_s > 0 else 0.0

    @property
    def energy_factor(self):
        """Energy per mm vs. the material profile's intent (commanded F).
        1.0 = on spec; 2.0 = twice the dose. M4 strokes report 1.0."""
        if self.dynamic or self.nominal_s <= 0:
            return 1.0
        return self.actual_s / self.nominal_s


def stroke_stats(res: SimResult, window_mm: float = 2.0) -> list[StrokeStats]:
    prog = res.program
    dwell_by_stroke = {}
    for x, y, dt, st in res.dwell:
        dwell_by_stroke.setdefault(st, []).append((x, y, dt))
    out = []
    for si, st in enumerate(prog.strokes):
        idx = [i for i in range(st["first_block"], st["last_block"] + 1)
               if prog.blocks[i].stroke == si]
        if not idx:
            continue
        L = sum(prog.blocks[i].length for i in idx)
        nominal = sum(prog.blocks[i].length / (prog.blocks[i].feed / 60.0) for i in idx)
        dw = dwell_by_stroke.get(si, [])
        dwell_s = sum(d for _, _, d in dw)
        actual = sum(res.block_time[i] for i in idx) + dwell_s
        lines = {prog.blocks[i].line for i in idx}
        # worst sliding window of >= window_mm: time spent / time intended
        worst, at = 0.0, (prog.blocks[idx[0]].x0, prog.blocks[idx[0]].y0)
        lo, accL, accT, accN = 0, 0.0, 0.0, 0.0
        for hi, i in enumerate(idx):
            b = prog.blocks[i]
            accL += b.length
            accT += res.block_time[i]
            accN += b.length / (b.feed / 60.0)
            while accL - prog.blocks[idx[lo]].length >= window_mm:
                j = idx[lo]
                accL -= prog.blocks[j].length
                accT -= res.block_time[j]
                accN -= prog.blocks[j].length / (prog.blocks[j].feed / 60.0)
                lo += 1
            if accL >= min(window_mm, L) - 1e-9 and accN > 0 and accT / accN > worst:
                worst = accT / accN
                at = (b.x1, b.y1)
        for x, y, d in dw:  # a dwell is a point dose: add it to one window
            f = (d + window_mm / (prog.blocks[idx[0]].feed / 60.0)) / (
                window_mm / (prog.blocks[idx[0]].feed / 60.0))
            if f > worst:
                worst, at = f, (x, y)
        dyn = prog.blocks[idx[0]].dynamic
        out.append(StrokeStats(
            index=si, name=st["name"], length_mm=L, n_lines=len(lines),
            n_blocks=len(idx), feed=L / nominal * 60.0 if nominal else 0.0,
            nominal_s=nominal, actual_s=actual,
            vmin=min(res.block_vmin[i] for i in idx), dwell_s=dwell_s,
            lines_per_s=len(lines) / nominal if nominal else 0.0,
            worst_window_factor=1.0 if dyn else worst, worst_window_at=at,
            dynamic=dyn,
        ))
    return out


def block_factors(res: SimResult) -> list[float]:
    """Per-block energy factor (time spent / time at commanded F)."""
    out = []
    for b, t in zip(res.program.blocks, res.block_time):
        nominal = b.length / (b.feed / 60.0) if b.feed > 0 else 0.0
        out.append(t / nominal if nominal > 0 and b.laser and not b.dynamic else 1.0)
    return out


def report_lines(res: SimResult, stats=None, hotspots: int = 10,
                 threshold: float = 1.3) -> list[str]:
    """Human-readable summary (also used verbatim as G-code header comments)."""
    stats = stats if stats is not None else stroke_stats(res)
    out = ["GRBL motion model (quickcut/grbl_model.py) -- predicted, not measured:"]
    out += ["  " + s for s in res.machine.describe() + res.sender.describe()]
    on = [s for s in stats if s.length_mm > 0]
    if on:
        L = sum(s.length_mm for s in on)
        nom = sum(s.nominal_s for s in on)
        act = sum(s.actual_s for s in on)
        out.append(
            f"  {len(on)} laser-on strokes, {L:.0f}mm: {nom:.0f}s at commanded F, "
            f"{act:.0f}s predicted (overall energy factor {act / nom:.2f})"
        )
    out.append(f"  total predicted job time {res.total_s / 60:.1f} min")
    bad = sorted(on, key=lambda s: -s.energy_factor)[:hotspots]
    if bad:
        out.append(
            f"  top {len(bad)} strokes by energy/mm factor (commanded F / predicted "
            f"avg speed; flag > {threshold}):"
        )
        for s in bad:
            flag = "  <-- OVER" if s.energy_factor > threshold else ""
            out.append(
                f"    {s.name}: F{s.feed:.0f}, {s.length_mm:.1f}mm, {s.n_lines} lines "
                f"({s.lines_per_s:.0f} lines/s needed), avg {s.avg_speed * 60:.0f}mm/min, "
                f"factor {s.energy_factor:.2f}, worst {2:g}mm window "
                f"{s.worst_window_factor:.2f} @({s.worst_window_at[0]:.1f},"
                f"{s.worst_window_at[1]:.1f}), dwell {s.dwell_s * 1000:.0f}ms{flag}"
            )
    return out


@dataclass
class MotionFinding:
    stroke: int
    name: str
    factor: float
    worst_window: float
    at: tuple
    detail: str


def lint_motion(res: SimResult, threshold: float = 1.3,
                window_threshold: float | None = None) -> list[MotionFinding]:
    """Flag M3 strokes predicted to run slower than commanded by more than
    `threshold` (energy factor) on average, or in their worst window."""
    window_threshold = window_threshold or threshold * 1.5
    out = []
    for s in stroke_stats(res):
        if s.dynamic:
            continue
        if s.energy_factor > threshold or s.worst_window_factor > window_threshold:
            out.append(MotionFinding(
                stroke=s.index, name=s.name, factor=s.energy_factor,
                worst_window=s.worst_window_factor, at=s.worst_window_at,
                detail=(f"avg {s.avg_speed * 60:.0f} of F{s.feed:.0f} "
                        f"({s.lines_per_s:.0f} lines/s needed, vmin "
                        f"{s.vmin * 60:.0f}mm/min)"),
            ))
    return out


# ---------------------------------------------------------------------------
# Software "M4": per-move S scaled to the predicted speed (static M3)
# ---------------------------------------------------------------------------


def scale_power_to_speed(gcode: str, machine: MachineProfile | None = None,
                         sender: SenderProfile | None = None,
                         floor_frac: float = 0.3, iterations: int = 2) -> str:
    """M4 never fires on this controller, so do its job in the G-code: for
    every laser-on M3 G1/G2/G3 move, append S = S_cmd * (predicted average
    speed over that move / commanded F), clamped to [floor_frac*S_cmd, S_cmd].
    GRBL laser mode ($32=1) applies an S word on a motion line at that block
    without a sync stop. Energy/mm then tracks the material profile wherever
    the model is right -- it is only as good as the machine/sender profile
    (UNVERIFIED defaults unless supplied), and it assumes diode output is
    linear in S (UNVERIFIED). Re-simulated `iterations` times since the S
    words lengthen every line (more bytes to stream)."""
    machine = machine or MachineProfile()
    sender = sender or SenderProfile()
    src = gcode.splitlines()
    out = src
    s_orig = {b.line: b.s for b in parse_program(gcode, machine).blocks}
    for _ in range(iterations):
        res = simulate("\n".join(out), machine, sender)
        prog = res.program
        per_line = {}  # stripped-line index -> (time, nominal time, S_cmd)
        for b, t in zip(prog.blocks, res.block_time):
            if not b.laser or b.dynamic or b.feed <= 0:
                continue
            acc = per_line.setdefault(b.line, [0.0, 0.0, s_orig[b.line]])
            acc[0] += t
            acc[1] += b.length / (b.feed / 60.0)
        # map program line index -> source line number (same filter as parse)
        src_idx = [n for n, raw in enumerate(src)
                   if not raw.strip().startswith(";") and _strip(raw)]
        out = list(src)
        for li, n in enumerate(src_idx):
            if li in per_line:
                t, nom, s_cmd = per_line[li]
                frac = min(1.0, max(floor_frac, nom / t if t > 0 else 1.0))
                code = re.sub(r"\s*S[-+]?\d*\.?\d+", "", src[n].split(";")[0]).rstrip()
                out[n] = f"{code} S{int(round(s_cmd * frac))}"
    return "\n".join(out) + ("\n" if gcode.endswith("\n") else "")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _profiles_from_args(a):
    text = open(a.dollars).read() if a.dollars else ""
    machine = MachineProfile.from_dump(
        text,
        junction_deviation_mm=a.junction_deviation,
        arc_tolerance_mm=a.arc_tolerance,
        accel_x=a.accel, accel_y=a.accel,
        max_rate_x=a.max_rate, max_rate_y=a.max_rate,
        planner_blocks=a.planner_blocks, rx_buffer_bytes=a.rx_buffer,
    )
    sender = SenderProfile(kind=a.sender)
    if a.latency_ms is not None:
        sender.latency_ms = a.latency_ms
        sender.verified.add("latency_ms")
    if a.lines_per_s is not None:
        sender.lines_per_s = a.lines_per_s
        sender.verified.add("lines_per_s")
    if a.baud is not None:
        sender.bytes_per_s = a.baud / 10.0
    return machine, sender


def add_cli_args(p):
    g = p.add_argument_group("GRBL motion model (UNVERIFIED defaults unless given)")
    g.add_argument("--dollars", help="saved `$$` (+ `$I`) output to read settings from")
    g.add_argument("--sender", default="char_count",
                   choices=("char_count", "ping_pong", "rate", "sd"))
    g.add_argument("--accel", type=float, default=None, help="$120/$121 mm/s^2")
    g.add_argument("--max-rate", type=float, default=None, help="$110/$111 mm/min")
    g.add_argument("--junction-deviation", type=float, default=None, help="$11 mm")
    g.add_argument("--arc-tolerance", type=float, default=None, help="$12 mm")
    g.add_argument("--planner-blocks", type=int, default=None)
    g.add_argument("--rx-buffer", type=int, default=None)
    g.add_argument("--latency-ms", type=float, default=None)
    g.add_argument("--lines-per-s", type=float, default=None)
    g.add_argument("--baud", type=float, default=None)
    return p


def main(argv=None):
    import argparse

    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("gcode")
    p.add_argument("--hotspots", type=int, default=10)
    p.add_argument("--threshold", type=float, default=1.3)
    add_cli_args(p)
    a = p.parse_args(argv)
    machine, sender = _profiles_from_args(a)
    res = simulate(open(a.gcode).read(), machine, sender)
    print("\n".join(report_lines(res, hotspots=a.hotspots, threshold=a.threshold)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
