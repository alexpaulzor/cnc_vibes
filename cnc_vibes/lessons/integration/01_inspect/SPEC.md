# Integration 01 — inspect machine state via serial

> **Status: SPEC.** Planning doc; no implementation yet.
>
> Standalone Python script that opens the USB serial port to the GRBL controller, sends a small set of read-only queries, parses the responses, and prints a structured report. Run from any shell. Does not depend on Claude or any LLM.

## Goal

Verify what the operator believes about machine state before they cut. Catches a class of "I forgot to home" / "I left `$32=1` from the last laser job" / "the WCS isn't where I think it is" errors that the checklist alone can't.

Run from the same terminal you're about to launch a job from, get a 10-line report, fix any surprises, then continue to preflight.

## What it does (MVP)

Sends three GRBL read-only queries and parses the responses:

| Query | What it returns | What we extract |
|---|---|---|
| `?` (status) | `<Idle\|MPos:0.000,0.000,0.000\|FS:0,0>` | run state, machine position, current feed/spindle |
| `$$` | settings dump (`$0=10\n$1=25\n...`) | every setting, with key ones labeled: `$32` (laser mode), `$13` (mm/in), `$22` (homing enable), `$20/$21` (soft/hard limits) |
| `$#` | parameter dump (`[G54:0.000,0.000,0.000]\n...`) | WCS offsets G54–G59, plus G28/G30/G92 references and tool-length offset (TLO) |

The output is a one-page report:

```
=== Anolex 4030-Evo Ultra 2 — machine state ===
Serial:        /dev/ttyUSB0 @ 115200  (resolved via $PORT env var)
GRBL version:  Grbl 1.1h ['$' for help]

State:         Idle
Position (MPos):  X 0.000  Y 0.000  Z 0.000
Feed / Spindle:   F 0   S 0

Key settings (full dump in --verbose):
  $32 (laser mode):       1            <-- LASER MODE ACTIVE
  $13 (units):            mm
  $20 (soft limits):      enabled
  $21 (hard limits):      enabled
  $22 (homing enabled):   yes
  $130/131/132 (max travel): 400.0 / 300.0 / 100.0 mm

Work coordinate offsets:
  G54:  X  10.000  Y  10.000  Z  -3.000   <-- ACTIVE
  G55:  X   0.000  Y   0.000  Z   0.000
  ...

Tool-length offset (TLO):  0.000

Last alarm:    none recorded since power-on
```

When something is off-pattern (e.g. `$32=1` for a job that the user is about to start as a spindle job), the report highlights it.

## CLI

```
python lessons/integration/01_inspect/inspect.py [--port PORT] [--baud N]
                                                 [--verbose] [--watch]
                                                 [--expect-head laser|spindle]
```

| Flag | Default | Meaning |
|---|---|---|
| `--port` | `$CNC_PORT` env var, else auto-detect | Serial port (`/dev/ttyUSB0` or `COM3`). |
| `--baud` | 115200 | GRBL default; Anolex uses this. |
| `--verbose` | off | Print every setting, not just the key ones. |
| `--watch` | off | Re-query `?` every 500ms and live-update the state line. Useful while jogging. |
| `--expect-head` | (unset) | If set, the report flags a mismatch between `$32` and the expected head mode. |

Exit code: 0 if everything looks consistent, 1 if anything was flagged (`$32` mismatch, hard alarm active, soft-limits disabled while job ahead, etc), 2 on connection failure.

## Standalone, not Claude-dependent

The script is invokable from any shell. `cnc.py preflight` (future) could shell out to it for automated verification, but that's a `cnc.py` enhancement, not a requirement of this tool.

No file format dependencies. No YAML loading. Just `pyserial` + stdlib argparse + print.

## Risk surface

Read-only. The script sends `?`, `$$`, `$#` and nothing else. None of these change machine state. There is no path through this tool that issues motion.

The serial port is exclusive — if gSender or another sender has the port open, this tool will fail with a clear error message. That's the right behavior.

## MVP scope

- Single-platform first (Linux/macOS via `/dev/ttyUSB*`, Windows via `COM*`). `pyserial` handles both.
- No port auto-detection in MVP; user passes `--port` or sets `$CNC_PORT`.
- Single-query mode only (no `--watch`) in MVP. `--watch` is a clear extension.
- `--expect-head` flagging is extension; MVP just prints state.

## Extensions

- `--watch`: live status updates while operator jogs.
- `--port-list`: enumerate available serial ports (cross-platform via `pyserial.tools.list_ports`).
- `--write-json`: emit machine state as JSON so other tools can consume it. Useful for `cnc.py preflight` integration.
- Integration with `cnc.py preflight`: when running preflight, automatically call `inspect --write-json` to verify the auto-checkable items.
- Alarm code interpretation: map GRBL alarm code numbers to human-readable causes.

## Why standalone matters

Putting this logic in a regular Python script (with `if __name__ == "__main__": main()`) means:

- The user can run it interactively at the keyboard while debugging.
- A shell script or Makefile can call it.
- A CI job can use it to test on a real machine.
- A different LLM (or no LLM) can drive it.
- Future-you can read the source without context-switching into "Claude tools."

Keeping the tool boundary clean lets `cnc.py preflight` learn to call it without coupling — preflight just shells out to `python inspect.py --write-json` and parses the result.

## Dependencies this adds

```
pyserial >= 3.5
```

Single addition. Pure Python, no native compilation. Installs cleanly on Windows 11.

## What it does NOT do

- Does **not** issue any motion commands. (See `03_probe-corner` for that.)
- Does **not** stream a job. (Use gSender.)
- Does **not** modify machine settings. Use the sender's settings UI or `$N=V` commands you type yourself.
- Does **not** persist a log file. Stdout only in MVP. JSON output is an extension.

## Files this lesson will create

```
lessons/integration/01_inspect/
  SPEC.md           ← this file
  README.md         ← user-facing once implemented
  grbl_inspect.py   ← the script (argparse + pyserial)
                      (named grbl_inspect.py, not inspect.py,
                       to avoid colliding with stdlib `inspect`)
  tests/
    test_inspect.py ← unit tests on response parsers (synthetic GRBL output)
```

Note: tests cover the response *parsers* (which take a string and return structured data) without needing a real machine. Integration testing requires a connected controller and is manual.
