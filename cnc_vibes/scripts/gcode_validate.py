#!/usr/bin/env python3
"""Validate a GCode file against a machine profile and tool table.

Reads YAML profiles (machine envelope, max feeds, tool plunge limits) and
walks the GCode as a small state machine, accumulating violations. Designed
to run in CI or as `cnc.py validate <file>`.

The validator detects the target head from a comment in the first 20 lines:

    ;HEAD: laser     -> apply laser-mode rules
    (no marker)      -> apply spindle rules (default)

Spindle rules (default):
  * bounds         — every coordinate is inside the machine envelope
  * max_feed       — every F value is <= machine max feed (XY or Z plunge)
  * max_plunge     — pure-Z-down moves <= tool.max_plunge_mm_per_min (if a
                     tool is declared via the ;TOOL: <id> comment)
  * safe_z_rapid   — rapid (G0) moves never travel with Z below safe_z while
                     XY is changing (would mean rapiding through stock)
  * spindle_on     — first feed (G1/G2/G3) move below safe_z is preceded by
                     an M3 with S > 0

Laser rules:
  * bounds         — same as spindle
  * max_feed       — same as spindle (XY cap; laser jobs have no Z motion)
  * laser_mode     — $32=1 must appear somewhere in the file
  * laser_m4_required — M3 (static) is rejected; laser jobs use M4 (dynamic)
  * laser_power_range — every S value is in [0, 1000] (GRBL convention)

A GCode file can declare the tool it uses by including a comment like
    ;TOOL: flat_3.175mm_2flute
anywhere before the first cutting move. Without it, max_plunge is skipped
and a note is emitted (not a violation).
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml


# Word tokens like "G0", "X-12.34", "F3000". Comments stripped before tokenizing.
_TOKEN = re.compile(r"([A-Za-z])(-?\d+\.?\d*)")


@dataclass
class Violation:
    line_no: int
    rule: str
    message: str

    def __str__(self) -> str:
        return f"line {self.line_no}: [{self.rule}] {self.message}"


@dataclass
class State:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    f: float | None = None
    s: float = 0.0
    spindle_on: bool = False
    absolute: bool = True
    declared_tool_id: str | None = None
    has_cut_below_safe_z: bool = False


def _strip_comments(line: str) -> str:
    line = re.sub(r"\(.*?\)", "", line)  # parenthesized comments
    return line.split(";", 1)[0]


def _parse_words(line: str) -> list[tuple[str, float]]:
    bare = _strip_comments(line).strip()
    if not bare:
        return []
    return [(m.group(1).upper(), float(m.group(2))) for m in _TOKEN.finditer(bare)]


def _extract_tool_decl(line: str) -> str | None:
    m = re.search(r";\s*TOOL:\s*(\S+)", line)
    return m.group(1) if m else None


def detect_head(gcode_text: str, scan_lines: int = 20) -> str:
    """Return 'laser' if ;HEAD: laser appears in the first N lines, else 'spindle'."""
    for line in gcode_text.splitlines()[:scan_lines]:
        m = re.search(r";\s*HEAD:\s*(\w+)", line)
        if m and m.group(1).lower() == "laser":
            return "laser"
    return "spindle"


def _load_yaml(path: Path):
    with path.open() as f:
        return yaml.safe_load(f)


def _tool_by_id(tools: list[dict], tool_id: str) -> dict | None:
    return next((t for t in tools if t.get("id") == tool_id), None)


def validate(gcode_text: str, profile: dict, tools: list[dict]) -> list[Violation]:
    head = detect_head(gcode_text)
    envelope = profile["envelope_mm"]
    max_feed_xy = profile["max_feed_mm_per_min"]["xy"]
    max_feed_z = profile["max_feed_mm_per_min"]["z"]
    safe_z = profile.get("default_safe_z_mm", 5.0)

    state = State()
    violations: list[Violation] = []

    # Laser-mode file-level precondition: $32=1 (GRBL laser-mode setting)
    # must appear somewhere in the GCode so the controller switches into
    # dynamic-power mode before any cuts.
    if head == "laser" and not re.search(
        r"^\s*\$32\s*=\s*1\b", gcode_text, re.MULTILINE
    ):
        violations.append(
            Violation(
                0, "laser_mode", "laser job is missing $32=1 (GRBL laser-mode setting)"
            )
        )

    for line_no, raw in enumerate(gcode_text.splitlines(), start=1):
        if tool_id := _extract_tool_decl(raw):
            state.declared_tool_id = tool_id

        words = _parse_words(raw)
        if not words:
            continue

        # Track modal G state via the leading G word if present.
        motion = None
        params: dict[str, float] = {}
        for letter, value in words:
            if letter == "G" and value in (0, 1, 2, 3):
                motion = int(value)
            elif letter == "G" and value == 90:
                state.absolute = True
            elif letter == "G" and value == 91:
                state.absolute = False
            elif letter == "M" and value == 3:
                state.spindle_on = True
            elif letter == "M" and value in (5,):
                state.spindle_on = False
            elif letter == "S":
                state.s = value
            elif letter in "XYZF":
                params[letter] = value

        if "F" in params:
            state.f = params["F"]

        prev_x, prev_y, prev_z = state.x, state.y, state.z
        if state.absolute:
            state.x = params.get("X", state.x)
            state.y = params.get("Y", state.y)
            state.z = params.get("Z", state.z)
        else:
            state.x += params.get("X", 0.0)
            state.y += params.get("Y", 0.0)
            state.z += params.get("Z", 0.0)

        # Bounds check. We can't tell from the GCode alone where the WCS
        # was set in machine coordinates, so the strongest machine-agnostic
        # rule is: every coordinate's absolute value fits in the envelope.
        # That catches "200mm Z plunge on a 100mm-Z machine" without
        # rejecting normal positive-Z safe-traverse moves.
        if motion in (0, 1, 2, 3):
            for axis, val in (("x", state.x), ("y", state.y), ("z", state.z)):
                limit = envelope[axis]
                if abs(val) > limit + 0.001:
                    violations.append(
                        Violation(
                            line_no,
                            "bounds",
                            f"{axis.upper()}={val:.3f} exceeds envelope |{limit}|",
                        )
                    )

        # Feed checks (G1/G2/G3 only — G0 ignores F).
        if motion in (1, 2, 3) and state.f is not None:
            is_pure_z = "Z" in params and "X" not in params and "Y" not in params
            cap = max_feed_z if is_pure_z else max_feed_xy
            if state.f > cap + 0.001:
                violations.append(
                    Violation(
                        line_no,
                        "max_feed",
                        f"F={state.f} exceeds {'Z' if is_pure_z else 'XY'} cap {cap}",
                    )
                )

        # ---- Laser-specific per-line checks (run only for laser jobs) ----
        if head == "laser":
            for letter, value in words:
                if letter == "M" and value == 3:
                    violations.append(
                        Violation(
                            line_no,
                            "laser_m4_required",
                            "M3 (static power) used; laser jobs must use M4 (dynamic)",
                        )
                    )
                elif letter == "S" and not (0 <= value <= 1000):
                    violations.append(
                        Violation(
                            line_no,
                            "laser_power_range",
                            f"S={value} outside GRBL range 0..1000",
                        )
                    )

        # ---- Spindle-only per-line checks (skip for laser jobs) ----
        if head != "spindle":
            continue

        # Plunge check vs declared tool, if any.
        if motion in (1, 2, 3) and state.f is not None:
            is_pure_z = "Z" in params and "X" not in params and "Y" not in params
            if is_pure_z and state.z < prev_z and state.declared_tool_id:
                tool = _tool_by_id(tools, state.declared_tool_id)
                if tool and (tplunge := tool.get("max_plunge_mm_per_min")):
                    if state.f > tplunge + 0.001:
                        violations.append(
                            Violation(
                                line_no,
                                "max_plunge",
                                f"plunge F={state.f} exceeds tool '{state.declared_tool_id}' "
                                f"max_plunge {tplunge}",
                            )
                        )

        # Safe-Z compliance: a G0 with XY change while Z is below safe_z.
        if motion == 0:
            xy_changes = "X" in params or "Y" in params
            if xy_changes and state.z < safe_z - 0.001:
                violations.append(
                    Violation(
                        line_no,
                        "safe_z_rapid",
                        f"rapid traverse at Z={state.z:.3f} (below safe_z={safe_z}); "
                        f"would crash through stock",
                    )
                )

        # Spindle-on check: first feed move that goes below safe_z must have
        # the spindle running with S > 0.
        if (
            motion in (1, 2, 3)
            and state.z < safe_z - 0.001
            and not state.has_cut_below_safe_z
        ):
            state.has_cut_below_safe_z = True
            if not (state.spindle_on and state.s > 0):
                violations.append(
                    Violation(
                        line_no,
                        "spindle_on",
                        "first cutting move below safe_z but spindle is off or S=0",
                    )
                )

    return violations


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--profile", required=True, type=Path)
    p.add_argument("--tools", required=True, type=Path)
    p.add_argument("--gcode", required=True, type=Path)
    args = p.parse_args(argv)

    if not args.gcode.exists():
        print(f"error: gcode file not found: {args.gcode}", file=sys.stderr)
        return 2

    profile = _load_yaml(args.profile)
    tools = _load_yaml(args.tools)
    text = args.gcode.read_text()

    violations = validate(text, profile, tools)
    if not violations:
        print(
            f"ok: {args.gcode} passes all checks against {profile.get('name', args.profile.name)}"
        )
        return 0

    print(f"FAIL: {len(violations)} violation(s) in {args.gcode}:", file=sys.stderr)
    for v in violations:
        print(f"  {v}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
