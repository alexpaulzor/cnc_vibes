# Lesson 4d — Aluminum milling

> Two parts:
> 1. **Use lesson 4a with aluminum** — the existing spacer generator already supports `--material aluminum_6061_3mm`. No new code needed for simple cylindrical aluminum parts.
> 2. **Trochoidal slot demonstration** — a new generator (`trochoidal_slot.py`) that shows the low-engagement clearing strategy aluminum needs to avoid the 500W spindle stalling.

## Why aluminum on a 500W router is different

The 500W spindle can mill aluminum but is **massively underpowered** compared to commercial machines (typically 1500W+). To avoid stalling, breaking the tool, or melting chips onto the cutter:

- **Tiny chipload.** Aluminum's chipload for the 3.175mm tool is 0.015 mm/tooth — about 4× smaller than for plywood. That's already in `profiles/materials.yaml`.
- **Conservative DOC.** 0.15 × tool_diameter (so ~0.5 mm per pass for a 3.175 mm tool). Also already in the profile.
- **Reduced tool engagement.** Trochoidal / adaptive clearing maintains a constant small engagement angle by using curved looping motion instead of straight cuts at full engagement. This is what `trochoidal_slot.py` demonstrates.
- **Lubrication.** A drop of WD-40, kerosene, or cutting fluid every minute prevents chips from welding to the cutter. **Critical.** Without it you'll snap a tool inside an hour.
- **Chip evacuation.** Aluminum chips are hot, sticky, and conductive. Dust collection helps; brushing periodically is mandatory; compressed air at the cut is best if you have it.

## Part 1: cylindrical aluminum spacer (using 4a)

Lesson 4a's `mill_spacer.py` already handles aluminum. Material `aluminum_6061_3mm` is in `profiles/materials.yaml` with chipload + DOC values calibrated for the 500W spindle.

```
python lessons/mill/01_spacer/mill_spacer.py \
    --material aluminum_6061_3mm \
    --height 3 --od 12 --id 4.2 \
    --tool flat_3.175mm_2flute \
    --spindle-rpm 18000
```

What the derivation produces (verify with `cnc.py params` once a job.yaml is set up, or read off mill_spacer's own header):

- feed = `0.015 × 2 × 18000 = 540 mm/min` (vs `1440 mm/min` for plywood with same tool)
- DOC = `0.15 × 3.175 ≈ 0.48 mm` (so 6-7 passes for a 3 mm sheet)

This is *just* about safe for the 500W spindle. Lube + chip evacuation are still your responsibility.

## Part 2: trochoidal slot

`trochoidal_slot.py` generates GCode for a single straight slot using trochoidal motion. Use it to cut a slot (e.g. for a panel mount) in aluminum without overloading the spindle.

```
python lessons/mill/03_aluminum/trochoidal_slot.py \
    --x0 10 --y0 10 \
    --length 30 \
    --width 6 \
    --depth 3 \
    --tool flat_3.175mm_2flute
```

The slot runs along +X from `(x0, y0)` for `length` mm, with width `width` mm. The trochoidal motion clears it in shallow circular looping passes rather than naïve straight cuts.

| Flag | Default | Meaning |
|---|---|---|
| `--x0 FLOAT` | required | Slot start X (mm) |
| `--y0 FLOAT` | required | Slot start Y (mm) |
| `--length FLOAT` | required | Slot length along +X (mm) |
| `--width FLOAT` | required | Slot width along Y (mm). Must be > tool_diameter. |
| `--depth FLOAT` | required | Slot depth (negative Z reached). |
| `--tool ID` | `flat_3.175mm_2flute` | Tool from `profiles/tools.yaml`. |
| `--material ID` | `aluminum_6061_3mm` | Material from `profiles/materials.yaml`. |
| `--spindle-rpm INT` | 18000 | Within tool max. |
| `--trochoidal-radius-frac FLOAT` | 0.4 | Loop radius as fraction of tool diameter. |
| `--trochoidal-step-frac FLOAT` | 0.15 | Per-loop X advance as fraction of tool diameter. |
| `--out PATH` | derived | Output gcode path. |

## Safety notes

- **Cutting fluid is mandatory.** WD-40 in a squeeze bottle, applied every 30-60 seconds during the cut. The script can't enforce this; you have to remember.
- **Listen for chatter.** Aluminum on an underpowered machine tells you when it's unhappy. If you hear high-pitched squealing or feel vibration, hit the e-stop, check the tool, reduce feed or DOC.
- **Don't try thick aluminum.** This setup is realistic for 3-6 mm 6061. Above that, factor of 2-3 more conservative on everything, and consider not at all.
- **No PVC near the workpiece.** Coolant aerosol + heated PVC = hydrochloric acid gas.

## End-to-end run

```
# 1. Generate
python lessons/mill/03_aluminum/trochoidal_slot.py \
    --x0 10 --y0 10 --length 30 --width 6 --depth 3

# 2. Validate
python cnc.py validate lessons/mill/03_aluminum/build/trochoidal_slot_L30_W6_D3.gcode

# 3. Inspect machine state (verify $32=0 from a prior laser job, etc)
python lessons/integration/01_inspect/grbl_inspect.py --expect-head spindle

# 4. Preflight
python cnc.py preflight lessons/mill/03_aluminum/build/trochoidal_slot_L30_W6_D3.gcode

# 5. Setup: clamp aluminum securely, install tool, probe Z, set WCS, prepare lube.
# 6. gSender, run. Apply lube periodically.
```

## Extensions

- **2D trochoidal pocket** (not just a slot — clearing a rectangular pocket with trochoidal raster). Several Y rows of slot-style motion, stepping over in Y between rows.
- **Adaptive engagement detection**: vary trochoidal radius based on remaining material in the cell.
- **Coolant macro**: emit M7 / M8 if you wire up a coolant relay.
- **Real aluminum chipload calibration**: lesson 3b style for the spindle side. Cut small test slots at varying (feed, DOC, RPM) and inspect for chatter/burn marks.

## Status

`trochoidal_slot.py` implemented and tested. Spacer-in-aluminum uses 4a's existing machinery; no new code needed beyond this documentation.
