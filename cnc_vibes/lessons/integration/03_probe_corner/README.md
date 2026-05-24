# Integration 03 — automated WCS corner finding (touch-plate probing)

> Standalone Python tool. Drives the spindle through a Z + X + Y probing sequence using a touch plate, then writes the resulting offsets into G54 via `G10 L20`. No LLM dependency.
>
> See [SPEC.md](SPEC.md) for the design rationale and physical setup assumptions.

## Why this exists

Every CNC job starts with the same ritual: jog to the stock corner, edge-find X, edge-find Y, probe Z, write each offset into the sender's WCS dialog. 2–3 minutes per job, error-prone, easy to forget a step. This tool collapses it to one command.

## Physical setup (the script assumes this)

- Touch plate is positioned at the front-left corner of the stock such that:
  - Plate's **right edge** is flush with stock's **left edge**.
  - Plate's **back edge** is flush with stock's **front edge**.
- Plate dimensions are known (you pass them as flags).
- Spindle is jogged to above the center of the plate, ~3–5 mm above plate top, with the cutting tool installed.
- Probe wire is connected to the GRBL probe pin; touching the tool to the plate completes a circuit.

## Usage

```
python lessons/integration/03_probe_corner/probe_corner.py \
    --plate-thickness 12.0 \
    --plate-x-offset 25.0 \
    --plate-y-offset 25.0 \
    --tool-diameter 3.175 \
    [--port PORT] [--feed 50] [--dry-run] [--yes]
```

| Flag | Default | Meaning |
|---|---|---|
| `--plate-thickness FLOAT` | required | Z dimension of the touch plate, mm. |
| `--plate-x-offset FLOAT` | 0.0 | X distance from probed edge to where you want WCS X=0 (= plate dimension if probing flush-with-stock-edge). |
| `--plate-y-offset FLOAT` | 0.0 | Y distance from probed edge to WCS Y=0. |
| `--tool-diameter FLOAT` | required | Installed tool diameter, mm. Tool-radius compensation. |
| `--feed INT` | 50 | Probing feed rate, mm/min. Slow for accuracy. |
| `--max-distance FLOAT` | 15 | Abort probing if no contact within this distance. Safety. |
| `--edge-clearance FLOAT` | 8 | How far off the plate to position before XY probes. |
| `--safe-z FLOAT` | 10 | Retract height between probes. |
| `--port` | `$CNC_PORT` | Serial port. |
| `--dry-run` | off | Print the GCode, don't open the port. |
| `--yes` | off | Skip the confirmation prompt. |

Exit codes: 0 = probed and G54 written; 1 = probe failed / aborted; 2 = bad inputs / connection.

## What it sends (look at this first)

Default behavior **always** prints the GCode sequence before doing anything. Without `--yes`, it then prompts for explicit confirmation. Read what's about to happen.

```
Probing sequence to be sent:

  ; --- probe_corner.py — touch-plate corner finding ---
  ; plate: 12.0mm thick, offsets X=25.0 Y=25.0
  ; tool diameter: 3.175mm
  ;
  G21
  G90
  $32=0
  ...
  G38.2 Z-15.0 F50
  G10 L20 P1 Z12.0
  G0 Z10.0
  ...
```

## Workflow

```
# 1. Position tool above plate center manually (using gSender or jog buttons)
# 2. Verify state with the inspect tool:
python lessons/integration/01_inspect/grbl_inspect.py

# 3. Dry-run to see the GCode:
python lessons/integration/03_probe_corner/probe_corner.py \
    --plate-thickness 12 --plate-x-offset 25 --plate-y-offset 25 \
    --tool-diameter 3.175 --dry-run

# 4. Actually probe:
python lessons/integration/03_probe_corner/probe_corner.py \
    --plate-thickness 12 --plate-x-offset 25 --plate-y-offset 25 \
    --tool-diameter 3.175 --port /dev/ttyUSB0

# 5. Verify the new WCS:
python lessons/integration/01_inspect/grbl_inspect.py
# G54 should now reflect your stock front-left corner.
```

## Failure modes the script catches

- **Machine not Idle** at start: aborts with a message before any motion.
- **Probe didn't trigger** within `--max-distance`: PRB response has success bit 0; script aborts.
- **Alarm raised** during probing: caught from response lines, aborts.
- **Serial port unavailable**: clean error at startup.

## What it does NOT do

- Does not handle non-canonical plate positions. Reposition the plate if it's not flush with stock corner.
- Does not establish tool-length offset (TLO). Use a separate procedure / reference plate.
- Does not handle multi-tool jobs. Reprobing after a tool change is a separate run.

## Extensions

- Other corners (front-right, back-left, back-right) — just sign flips.
- Center-finding for round stock — probe four edges, compute center.
- TLO probing as a sibling command.
- Probe history log for repeatability diagnostics.
- Integration with `cnc.py preflight` to auto-offer probing when G54 looks unset.

## Status

GCode generator implemented and tested (13 unit tests). Serial driver implemented but needs real-machine validation — manual testing only at present.
