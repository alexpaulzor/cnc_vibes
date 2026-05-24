# Lesson 3a — Parametric laser-cut PCB spacer

> First **fully-automated toolchain** in the repo. Pure Python: parameters in → GRBL laser-mode GCode out → validator passes → preflight → cut. No FreeCAD, no CAM project, no GUI in the loop except the sender.
>
> Companion design doc: [SPEC.md](SPEC.md) — captures the decisions behind this lesson and why each one was made.

## Goal

Generate ready-to-cut GCode for a stackable laser-cut PCB spacer ring. Two concentric circles cut from sheet material. You stack N rings to reach the standoff height you need.

## What you'll learn

- The GRBL **laser-mode** GCode dialect: `$32=1`, `M4` dynamic power, `S<0-1000>` per-line, no spindle commands.
- Why **dynamic power** (`M4`) matters: power scales with feedrate, so corners and direction changes don't over-burn while the head briefly slows.
- The **CAM-as-code** pattern for simple parametric parts: input parameters → deterministic GCode → validator → preflight → cut. Reusable for any geometry simple enough to generate directly.

> **Kerf is not compensated.** The `--od` and `--id` you pass are *toolpath* dimensions. Real laser kerf is ~0.10–0.20 mm depending on material and power; finished holes come out that much *larger* than `--id` and finished outer diameters come out that much *smaller* than `--od`. If you need a precise fit, add (kerf) to `--id` and subtract (kerf) from `--od` yourself.

## Prerequisites

- You've read `cnc_for_the_scad.md` §1–§4 (concepts, vocabulary, machine-as-profile principle).
- Toolchain installed and `cnc.py doctor` is happy.
- LaserTree 10W head installed; hardware switch on rear of machine set to LASER; `$32=1` set in your sender.
- Bed prepared (T-slot grid, honeycomb, or pin bed — see top-level README "Bed support").
- Air assist running.

## Usage

```
python lessons/laser/01_spacer/spacer.py \
    --od 6.0 \
    --id 3.2 \
    --material plywood_baltic_birch_3mm \
    --out lessons/laser/01_spacer/build/spacer.gcode
```

All arguments are optional and default to the values above. The default produces a small M3-clearance spacer in 3 mm plywood.

| Flag | Default | Meaning |
|---|---|---|
| `--od FLOAT` | 6.0 | Outer diameter (toolpath), mm. |
| `--id FLOAT` | 3.2 | Inner diameter (toolpath), mm. M3 clearance. |
| `--material ID` | `plywood_baltic_birch_3mm` | Material id from `profiles/laser_materials.yaml` (see `cnc.py help laser-materials`). |
| `--out PATH` | derived from params | Output `.gcode` path. |

Common alternatives for `--id`: `4.2` (M4 clearance), `5.2` (M5 / T-slot M5 clearance).

## End-to-end run

```
# 1. Generate the gcode
python lessons/laser/01_spacer/spacer.py --od 6 --id 3.2

# 2. Validate against machine and laser rules
python cnc.py validate lessons/laser/01_spacer/build/spacer_od6.0_id3.2.gcode

# 3. Walk the laser-mode preflight checklist (head auto-detected from the file)
python cnc.py preflight lessons/laser/01_spacer/build/spacer_od6.0_id3.2.gcode

# 4. Load the gcode in gSender, focus the laser, hit run.
```

## What the GCode looks like

The script emits a small header (so the validator and preflight can detect the head and material), then `$32=1` to switch GRBL into laser mode, `M4` for dynamic-power output, two `G3` arcs per pass per circle, and a clean `M5` + park at the end. No Z motion — you set focus once by hand.

See `cnc.py help validate` and `cnc.py help laser-checklist` for the rules that fire against the output.

## Extensions to explore

- **Auto-nesting** — take `--count N` and arrange N spacers across the bed (hexagonal packing for high material yield).
- **Engraved labels** — burn the OD/ID into the ring face before cutting so you can identify spacers in a mixed bin.
- **Kerf compensation** — characterize kerf per material in lesson 3b's calibration, then add a `kerf_mm` to `laser_materials.yaml` and offset toolpaths inward/outward.
- **Slotted spacers** — replace the inner circle with an oval or T-slot mount.

## Status

Implemented and tested. Run `python cnc.py test` to confirm the validator, the spacer generator, and the help topics all stay in sync.
