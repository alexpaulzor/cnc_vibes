# Lesson 4b — PCB drilling and (TODO: isolation routing)

> **Two pieces:**
>
> 1. **Excellon drill-file → GCode converter** — implemented (`excellon_to_gcode.py`). Takes a KiCAD-style `.drl` file and emits drill GCode for the cnc_vibes pipeline.
> 2. **Isolation routing** (cut isolation traces around copper islands) — **not implemented in this lesson**. Use FlatCAM or pcb2gcode for this; they do it well and they're already standalone tools matching your "no LLM in the loop" preference.

## Goal

Make prototype circuit boards at home without chemical etching. Process:

1. Design the PCB in KiCAD (or any tool that outputs Gerber + Excellon).
2. **For the copper traces**: use FlatCAM (or pcb2gcode) to convert the top-copper Gerber into isolation-routing GCode. The tool cuts a thin channel around every copper feature, isolating the traces from each other and from the ground plane.
3. **For the through-holes**: use this lesson's `excellon_to_gcode.py` to convert the drill file into peck-drill GCode for cnc.py.
4. Run both GCode files on the CNC with copper-clad PCB blank stock + a small (60° or 90°) V-bit for isolation + appropriately-sized drill bits.

Result: a single-sided through-hole prototype board, no chemicals.

## What you'll learn

- The PCB-CAM workflow as a series of standalone tools (KiCAD → FlatCAM → cnc_vibes).
- How to parse Excellon, the universal drill-file format.
- Why surface-flatness probing matters for PCB engraving (and how this lesson punts on it).

## Why "no chemicals"

Standard PCB etching uses ferric chloride or HCl/H2O2 — both nasty to handle, dispose of, and store. The fully-mechanical approach (cut isolation traces with a V-bit, drill holes with the same machine) avoids all of that. Trade-off: each board takes 10-30 minutes of machine time, versus 5 minutes of unattended etching.

## Prerequisites

- **KiCAD installed** (the user designs PCBs in KiCAD; export Gerber and Excellon files at the end).
- **FlatCAM installed** (open-source, GPL, Python-based — `https://flatcam.org/`). Alternative: `pcb2gcode` (C++, CLI, simpler but less flexible).
- **Single-sided copper-clad PCB blank** (1 oz copper on 1.6 mm FR4 is standard). Available from any electronics supplier; ~$10 for 10 small boards.
- **Engraving V-bit** for isolation cuts (60° or 90°, 0.1 mm flat tip; ~$5 each on Amazon/AliExpress).
- **Small drill bits** matching the holes in your design. Common: 0.8 mm (for IC pins), 1.0 mm (for resistor/cap leads), 1.5 mm (for connectors). PCB drill bits are tungsten-carbide and very brittle.
- A **probe / touch plate** (lesson Int-03's intended use). PCB engraving needs Z-precision around 0.05 mm.

## Part 1: Excellon → GCode (this lesson)

```
python lessons/mill/04_pcb/excellon_to_gcode.py my_board.drl \
    --copper-thickness 1.6 \
    --spindle-rpm 12000
```

Reads the Excellon drill file, groups holes by tool diameter, emits GCode that:

- Drills all holes using one tool diameter
- Pauses with `M0` between tool changes (you swap the bit, re-probe Z, press CYCLE START)
- Uses peck drilling (plunge, retract, deeper plunge, retract) to clear chips from FR4 dust

| Flag | Default | Meaning |
|---|---|---|
| `drill_file` | required | Path to `.drl` file from KiCAD or similar. |
| `--copper-thickness FLOAT` | 1.6 | Board thickness in mm (1.6 = standard FR4). |
| `--spindle-rpm INT` | 12000 | Within drill-bit limits. |
| `--plunge-feed INT` | 80 | mm/min — slow for FR4 (it shatters PCB drill bits). |
| `--peck-depth FLOAT` | 0.5 | mm per peck cycle. |
| `--out PATH` | derived | Output gcode path. |

## Part 2: isolation routing (use FlatCAM, not this lesson)

```
# In FlatCAM:
# 1. File > Open > top-copper.gbr   (the Gerber from KiCAD)
# 2. Tool > Isolation Routing
# 3. Configure: tool diameter (V-bit), passes (usually 1 for prototyping),
#    cut depth (Z = -0.1 mm typical — just below copper surface).
# 4. Generate Geometry, then Generate GCode.
# 5. Save the GCode file.

# Then in cnc_vibes:
python cnc.py validate path/to/flatcam_output.gcode
python cnc.py preflight path/to/flatcam_output.gcode
# load in gSender, run
```

FlatCAM's GCode is GRBL-compatible out of the box (pick the GRBL post-processor). The `cnc.py validate` checks apply unchanged.

## End-to-end workflow (the full picture)

```
KiCAD                                FlatCAM                cnc_vibes
  │                                     │                      │
  │ Design board                        │                      │
  │ Plot top-copper.gbr                 │                      │
  │ Plot .drl (Excellon)                │                      │
  ├────────────────────────────────────>│                      │
  │  (top-copper.gbr)                   │                      │
  │                                     │ Isolation routing    │
  │                                     │ Generate GCode       │
  │                                     ├─────────────────────>│
  │                                     │  (isolation.gcode)   │
  │                                                            │
  │ (.drl file)                                                │
  ├──────────── excellon_to_gcode.py ─────────────────────────>│
  │                                              (drill.gcode) │
  │                                                            │
  │              cnc.py validate                               │
  │              cnc.py preflight                              │
  │              cnc.py preflight                              │
  │              gSender: run isolation.gcode                  │
  │                       run drill.gcode                      │
  │                                                            ▼
  │                                                       finished PCB
```

## Critical: surface flatness

PCB engraving needs the isolation cut to be a uniform 0.05-0.1 mm into the copper surface — too deep and you melt FR4 / dull the bit; too shallow and you don't isolate. Copper-clad blanks are **never perfectly flat**; they bow by 0.1-0.5 mm across a 100 mm span.

The standard solution is **auto-leveling**: probe a grid of points across the board surface, build a height map, then transform the GCode to follow the surface. **FlatCAM has this built in.** It's a major feature; turn it on.

`cnc.py validate` doesn't currently check for auto-leveled GCode (the toolpath looks the same — just with `Z` values varying per-segment based on the height map). That's fine; the auto-leveling is FlatCAM's job.

## What is NOT in this lesson

- **No isolation routing implementation.** Reimplementing FlatCAM is months of work. The existing tool is good.
- **No Gerber parser.** Same reason. The Gerber spec is large; gerbv and FlatCAM already parse it.
- **No auto-leveling probe routine.** FlatCAM emits the probing sub-sequence as part of the leveled GCode. If you wanted a standalone version, it would be Int-04 (sibling to Int-03's corner-finding).
- **No double-sided board support.** Single-sided only. Double-sided requires alignment fiducials and a flip operation; out of scope.

## Status

`excellon_to_gcode.py` implemented and tested (16 tests covering Excellon parsing and drill-GCode generation).

The isolation-routing piece is intentionally **delegated to FlatCAM**.

## Extensions

- `gerber_inspect.py` — pure parser for Gerber files that just prints layer metadata (size, layer name, aperture list). Useful sanity check before sending to FlatCAM.
- `excellon_merge.py` — combine multiple drill files (separate PTH/NPTH) into one Excellon for single-pass operation.
- `pcb_preflight.py` — a PCB-specific checklist (board flat, copper-side up, hold-downs not on copper, drill-press extraction running, V-bit fresh).
