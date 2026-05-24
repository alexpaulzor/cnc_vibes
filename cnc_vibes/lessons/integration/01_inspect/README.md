# Integration 01 — inspect machine state

> Standalone Python tool. Reads GRBL state via the USB serial port and prints a structured report. Run from any shell. No LLM dependency.

See [SPEC.md](SPEC.md) for the design rationale.

## Why this exists

The preflight checklist asks "is $32 = 1?" but can't verify it. This tool can. Run it before any cut to catch:

- Machine in alarm state (need to home or unlock)
- `$32` in the wrong mode (laser-mode on for a spindle job, or vice versa)
- Soft limits disabled
- WCS offsets not where you think they are
- Hard alarms still pending from the last run

## Usage

```
python lessons/integration/01_inspect/grbl_inspect.py [--port PORT] [--baud N]
                                                       [--verbose] [--write-json PATH]
                                                       [--expect-head laser|spindle]
```

| Flag | Default | Meaning |
|---|---|---|
| `--port` | `$CNC_PORT` env var | Serial port (e.g. `/dev/ttyUSB0`, `COM3`). |
| `--baud` | 115200 | GRBL default. |
| `--verbose` / `-v` | off | Print every setting, not just the key ones. |
| `--expect-head` | (unset) | Flag a mismatch between `$32` and the head you expect to use. |
| `--write-json` | (unset) | Also write the parsed state to a JSON file (for consumption by other tools). |

Exit codes: 0 = consistent, 1 = anomaly flagged, 2 = connection/parse failure.

## Setup

```
python -m pip install pyserial>=3.5
```

`pyserial` was added to `requirements.txt` with this lesson; `python -m pip install -r requirements.txt` from the repo root covers it.

On Windows: the device is usually `COM3` or similar; check Device Manager. On Linux/macOS: `/dev/ttyUSB0` or `/dev/ttyACM0`. Set `CNC_PORT` in your shell profile.

## Example output

```
=== machine state ===
Serial:        /dev/ttyUSB0
GRBL version:  1.1h.20190825

State:         Idle
Position (MPos):  X     0.000  Y     0.000  Z     0.000
Feed / Spindle:   F 0   S 0

Key settings:
  $13   units                mm
  $20   soft limits          enabled
  $21   hard limits          enabled
  $22   homing enabled       yes
  $32   laser mode ($32)     off (spindle)
  $130  max travel X (mm)    400.0
  $131  max travel Y (mm)    300.0
  $132  max travel Z (mm)    100.0

Work coordinate offsets:
  G54:  X    10.000  Y    10.000  Z    -3.000
  G55:  X     0.000  Y     0.000  Z     0.000

(no anomalies detected)
```

## What it does NOT do

- Does not issue any motion commands. Read-only.
- Does not modify settings. Use the sender's settings UI for that.
- Does not stream a job. Use gSender.

## Extensions

- `--watch` mode: live status updates while you jog.
- `--port-list`: enumerate available serial ports.
- Integration with `cnc.py preflight` to auto-verify checkable items.
- Alarm-code interpretation table.

## Status

Implemented and tested (24 unit tests on the parsers + formatter; serial path is integration-tested manually).
