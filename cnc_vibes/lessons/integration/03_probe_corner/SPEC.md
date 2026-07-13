# Integration 03 — automated WCS corner-finding via touch plate

> **Status: SPEC.** Planning doc; no implementation yet.
>
> Standalone Python script that drives the spindle through a touch-plate probing routine to find the front-left corner of stock, then writes the resulting offsets into G54. Run from any shell. Does not depend on Claude or any LLM.

## Goal

Eliminate the per-job manual touch-off ritual. Today: jog to roughly the stock corner, edge-find X, edge-find Y, probe Z, write each offset into the sender's WCS dialog. 2–3 minutes per job, error-prone, easy to forget a step.

After this tool: position the spindle near the corner with a touch plate of known dimensions clamped to the stock, type one command, watch the spindle probe two edges and the top surface, and have G54 set correctly when it's done.

## What it does (MVP)

Issues a controlled probing sequence:

1. **User pre-positions** the spindle ~3mm above the touch plate, with the plate hanging off the front-left corner of the stock so its X and Y edges are accessible.
2. **Tool emits a small GCode sequence** that:
   - Probes -Z until contact (`G38.2 Z-10 F50`).
   - Computes Z=0 from the probe-plate-thickness setting and writes to G54 Z.
   - Retracts Z to safe.
   - Probes +X until contact with the plate's right edge (`G38.2 X+20 F50`).
   - Computes X=0 from the probe-plate-X-offset setting and writes to G54 X.
   - Retracts X.
   - Probes +Y until contact (`G38.2 Y+20 F50`).
   - Writes Y to G54.
   - Returns to safe Z + the new G54 origin.
3. **Tool reports** the new G54 values and any anomalies (probe didn't trigger, probe distance exceeded, controller alarm).

## CLI

```
python lessons/integration/03_probe_corner/probe_corner.py \
    --plate-thickness 12.0 \
    --plate-x-offset 10.0 \
    --plate-y-offset 10.0 \
    [--port PORT] [--baud N] [--feed F] [--retract N] [--dry-run]
```

| Flag | Default | Meaning |
|---|---|---|
| `--plate-thickness` | from `profiles/default.yaml` `probe.thickness_mm` | Z dimension of the touch plate; subtracted from probed Z to get stock-top Z=0. |
| `--plate-x-offset` | 10.0 | Distance from plate's right edge to where you want WCS X=0 (typically: stock-front-left-corner X). |
| `--plate-y-offset` | 10.0 | Same for Y. |
| `--port` | `$CNC_PORT` env var | Serial port. |
| `--feed` | 50 | Probing feed rate, mm/min. Slow for accuracy. |
| `--retract` | 5.0 | Retract distance between probes, mm. |
| `--dry-run` | off | Print the GCode that *would* be sent; don't open the port or move. |

## Standalone, not Claude-dependent

Plain Python script. The user runs it directly. `cnc.py preflight` (future) could call it as a setup step when no WCS is set yet, but the tool itself stands alone.

The script writes the WCS using GRBL's `G10 L20 P1 X<x> Y<y> Z<z>` command (P1 = G54). No external file is modified.

## Risk surface

**This tool issues motion.** Specifically:

- Probing moves at `--feed` (default 50 mm/min) for short distances (10-20 mm typical).
- Z retract between probes (G0 +Z by `--retract`).
- A final positioning move back to the new origin.

Failure modes the script must guard against:

- **Probe doesn't trigger** within the probe distance: GRBL returns `ALARM:4` or similar. Script must detect and abort with a clear message, not continue blindly into a crash.
- **Probe wire shorted at start**: GRBL refuses to start probing. Script must check before issuing the next probe and abort.
- **User pre-positioning wrong**: probe travels >20mm without triggering, hits something. Hard to detect from software alone. Mitigation: short max probe distance (15mm); user reads "if your spindle is more than 15mm from the plate, retract first" in the prompt.
- **Tool length differs from last calibration**: Z reference is off by tool-length-difference. Out of scope for this tool; user must establish tool length offset (TLO) separately via the sender or a future `set-tlo` tool.

**The script will prompt for explicit confirmation before any motion**, printing the moves it's about to make. `--yes` to skip the prompt (for scripted use, with the obvious caveat).

## MVP scope

- Single tool (no tool change inside the routine).
- Front-left corner only (most common case). Other corners would just be sign flips; extension.
- Linear-axis probing (X/Y/Z separately). No fancy edge-finding via two-point-line-fit; that's an extension.
- Writes to G54 only. Other WCSes (G55–G59) are extensions.

## Extensions

- **Center-finding**: probe four edges of a known-OD round stock or rectangular pocket; compute and set the center as WCS origin.
- **Other corners**: front-right, back-left, back-right; trivial sign flips.
- **Tool-length offset (TLO) probing**: use a fixed reference plate on the bed to set TLO before the workpiece probe, so swapping tools doesn't require re-zeroing Z.
- **Probe history log**: append every probe result to `logs/probes.jsonl` with timestamp, requested vs achieved coordinates, and any alarms. Useful for debugging machine repeatability.
- **`cnc.py preflight` integration**: if preflight detects G54 is at machine-zero (i.e., never been set), offer to run `probe-corner` as a setup step.

## Dependencies this adds

```
pyserial >= 3.5     # already added by 01_inspect
```

No new dependencies beyond what 01_inspect introduces.

## What it does NOT do

- Does **not** run a job. After it sets the WCS, you load your job in gSender and start it.
- Does **not** verify the WCS afterwards visually — only the controller's report. Pair with `01_inspect` and `02_snapshot` for a complete pre-job verification.
- Does **not** detect a missing touch plate. If you forget the plate and the spindle probes into the stock surface directly, the math still produces a number (just a wrong one). Mitigation: documented in the prompt.

## Files this lesson will create

```
lessons/integration/03_probe_corner/
  SPEC.md             ← this file
  README.md           ← user-facing once implemented
  probe_corner.py     ← the script (argparse + pyserial + GCode emit)
  tests/
    test_gcode_emit.py ← unit tests on the probe-sequence GCode generator
    test_parsers.py    ← parse PRB:x,y,z:ok responses, alarm codes
```

The GCode emission is testable as a pure function (input: plate-thickness, offsets, feed; output: GCode string). The serial interaction is integration-testable only on a real machine.

## Order of operations matters

This tool depends on `01_inspect`:

1. Before probing, the script calls the same response-parsing code from `01_inspect` to verify:
   - Machine is in `Idle` state (not alarmed, not running)
   - No probe-pin already triggered
   - `$22=1` (homing enabled) and machine is homed (some controllers require this for valid probing)

If any of these fail, abort before issuing motion.

That dependency means **01_inspect should ship first**, with its parsers exposed as importable functions. `probe-corner` imports them; no code duplication.
