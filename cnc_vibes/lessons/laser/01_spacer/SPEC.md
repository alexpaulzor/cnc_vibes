# Lesson 3a — Parametric laser-cut PCB spacer

> **Status: SPEC (pre-implementation).** This document captures the design intent so we can agree on it before code lands. Open questions at the bottom. Once we settle them, this file evolves into the lesson's README.md.

## Goal

Generate ready-to-cut GCode for a stackable laser-cut PCB spacer ring, **fully automated end-to-end**. No FreeCAD, no CAM project. Pure Python: parameters in → GCode out → validator passes → cut.

The artifact is a flat ring of laser-cuttable material with OD/ID tuned for PCB-standoff use. The user stacks N rings to reach the standoff height they need (a single 3 mm sheet is too thin for typical 10–20 mm standoffs, so stacking is the intended assembly).

## What you'll learn

- The GRBL **laser-mode** GCode dialect: `$32=1`, `M4` dynamic power, `S<0-1000>` per-line, no spindle commands.
- Why **dynamic power (`M4`)** matters: power scales with feedrate, so corners and direction changes don't over-burn while the head briefly slows.
- **Fully automated toolchain pattern**: input params → deterministic GCode generation → validator → preflight → cut. No GUI in the loop. This pattern reuses for any simple parametric part.

> **Kerf is not compensated.** The `--od` and `--id` you pass are *toolpath* dimensions, not finished-part dimensions. Real laser kerf is ~0.10–0.20 mm depending on material and power; finished holes come out that much *larger* than `--id`, and finished outer diameters come out that much *smaller* than `--od`. If you need a precise fit, add (kerf) to `--id` and subtract (kerf) from `--od` yourself. This keeps the lesson focused on the toolchain rather than on per-material kerf calibration.

## Prerequisites

- Read `cnc_for_the_scad.md` §1–§4 (concepts and machine-as-profile principle).
- Know what `cnc.py validate` does and have run it on at least one .gcode file.
- Laser head physically installed, hardware switch flipped to laser, `$32=1` set in your controller.
- Bed prepared (see main README "Bed support" section or this lesson's setup).

## Inputs (CLI)

```
python lessons/laser/01_spacer/spacer.py \
    --od 6.0 \
    --id 3.2 \
    --material plywood_baltic_birch_3mm \
    --out lessons/laser/01_spacer/build/spacer_od6_id3.2.gcode
```

| Flag | Default | Meaning |
|---|---|---|
| `--od FLOAT` | 6.0 | Outer diameter, mm. Tight footprint that fits most through-hole component layouts. |
| `--id FLOAT` | 3.2 | Inner diameter, mm. M3 screw clearance. Common alternatives: 4.2 (M4), 5.2 (M5 or T-slot M5). |
| `--material ID` | `plywood_baltic_birch_3mm` | Material id from `profiles/laser_materials.yaml`. |
| `--out PATH` | derived from params | Output `.gcode` path. |

## Outputs

```
lessons/laser/01_spacer/build/spacer_od6.0_id3.2.gcode
```

Filename encodes the params so you can build a collection without overwriting.

## Toolchain

```
┌──────────────────┐    ┌─────────────────┐    ┌──────────────────┐
│  spacer.py       │ -> │  validator     │ -> │  preflight       │
│  (Python -> .gcode) │   │  (laser-aware) │    │  (checklist)     │
└──────────────────┘    └─────────────────┘    └──────────────────┘
        |                       |                      |
        v                       v                      v
   GRBL laser-mode GCode    pass/fail            user confirms,
                                                 then sends to machine
```

No CAM. No FreeCAD. The script generates GRBL laser-mode GCode directly:

- Header: `$32=1` (laser mode), `M5` (laser off), `G21` mm, `G90` absolute, `;HEAD: laser` comment so the validator knows.
- Move to inner-circle start at Z = focus_height.
- `M4 S<power>` dynamic mode on.
- `G3` arc for inner circle. Repeat for `--passes` (from material profile).
- Move to outer-circle start.
- `G3` arc for outer circle. Repeat for `--passes`.
- Footer: `M5` off, park to X0 Y0.

One ring per gcode file. To cut multiple, run the script again (with different `--out`) and append the files, or stage multiple jobs in the sender.

## Files this lesson creates or extends

```
profiles/
  laser_materials.yaml             ← NEW. Per-material laser params.
scripts/
  gcode_validate.py                ← EXTEND. Laser-aware rules
                                     gated by `;HEAD: laser` comment.
lessons/laser/01_spacer/
  README.md                         ← NEW (this file evolves into it)
  spacer.py                         ← NEW. Argparse CLI, GCode generator.
  tests/
    test_spacer.py                  ← NEW. Unit tests on generated GCode.
```

## profiles/laser_materials.yaml shape

```yaml
- id: plywood_baltic_birch_3mm
  family: wood
  thickness_mm: 3.0
  laser:
    power_percent: 100        # S value as percent of 1000
    feed_mm_per_min: 400      # cut speed
    passes: 2                 # passes to cut through
    focus_height_mm: 0.0      # Z offset to apply (0 if already focused)
- id: plywood_baltic_birch_6mm
  family: wood
  thickness_mm: 6.0
  laser:
    power_percent: 100
    feed_mm_per_min: 200
    passes: 4
- id: mdf_3mm
  family: wood
  thickness_mm: 3.0
  laser:
    power_percent: 100
    feed_mm_per_min: 350
    passes: 2
- id: cast_acrylic_3mm_clear
  family: acrylic
  thickness_mm: 3.0
  laser:
    power_percent: 100
    feed_mm_per_min: 150
    passes: 3
    notes: |
      Diode lasers struggle with clear acrylic — IR passes through.
      Coat the cut line with masking tape or paint it first.
# ... bamboo plywood, balsa, cardstock, cardboard, colored/black acrylic, etc.
```

Numbers are **starting points** for 10 W diode lasers. First-time-with-a-material calibration (lesson 3b) refines them.

## Validator extension

`scripts/gcode_validate.py` currently checks: bounds, max_feed, max_plunge, safe_z_rapid, spindle_on. These were designed for spindle jobs.

Extension plan:

1. **Detection.** A GCode file is treated as a laser job if it contains the comment `;HEAD: laser` anywhere in the first 20 lines. Otherwise it's a spindle job (existing behavior).
2. **Laser-mode rules** (replace some spindle rules when head=laser):
   - **require `$32=1`** somewhere in the file. Fail otherwise.
   - **require `M4` not `M3`** for laser-on. `M3` is constant-power; `M4` is dynamic-power, which is what diode lasers want for clean corners.
   - **skip `spindle_on` rule** (laser doesn't have a spindle; M4 with S>0 serves the same gate).
   - **skip `max_plunge` rule** (no plunge in laser jobs; Z stays at focus height).
   - **add `power_in_range` rule**: every `S` value is between 0 and 1000 (GRBL convention).
   - **keep `bounds` and `max_feed` rules unchanged** (both still apply).
3. **Backward compat:** existing spindle test fixtures and the hole_in_sheet flow are unchanged. The new rules only fire when the `;HEAD: laser` marker is present.

## Validation that the lesson works end-to-end

1. `python lessons/laser/01_spacer/spacer.py --od 8 --id 3.2` produces a `.gcode` in `build/`.
2. `python cnc.py validate <that.gcode>` passes with the new laser-aware rules.
3. `python cnc.py preflight` — laser-mode checklist (focus set, $32=1, material flat, fire extinguisher reachable, air assist on, area clear) — separate item set from the spindle one.
4. Load in gSender, run on the machine, get rings.

## My critique of this design (be honest with yourself)

- **No kerf compensation.** Finished holes are ~0.15 mm larger than `--id`; finished ODs are ~0.15 mm smaller than `--od`. For PCB-clearance use, this is fine (loose holes are forgiving). For press-fit applications, user adjusts inputs manually. Keeps lesson scope tight; kerf calibration is its own rabbit hole.
- **No support for engraved details.** A real artifact might have markings (the OD/ID stamped on the ring). Lesson 3a is intentionally narrow — cut only. Engraving lands in lesson 3b's territory.
- **One ring per file.** To cut a batch, run the script N times. Auto-nesting would be a yield improvement but it's deferred — most-time is in the laser pass, not in seek time between rings.
- **Stacking strategy is manual.** N rings stack to height N × material_thickness, but the script doesn't generate the stacking jig or any alignment marks. The user aligns by eye + screw-through-the-ID. Could be extended.

## Extensions for later

- Auto-nesting: take `--count` and arrange N rings (hexagonal packing for high yield, or linear for simplicity).
- Oval or slotted spacers (T-slot mount with M5 slot rather than round hole).
- Engraved labels on the ring face (OD, ID, material — useful when you have a bin of mixed spacers).
- Kerf compensation per material (deferred from v1 as discussed).
- Multi-material batch: take a list of (od, id, material) tuples and produce one GCode per material.

## Open questions

Grouped by topic. We'll walk through these one group at a time before any code lands.

### Group A — Geometric defaults *(settled 2026-05-23)*

1. ~~Default `--od`~~: **6.0 mm**. Tight footprint, fits between through-hole component layouts.
2. ~~Default `--id`~~: **3.2 mm** (M3 clearance). Most common maker screw size.
3. ~~Default `--count`~~: **1**. Single ring per file by default; user passes `--count N` to nest more.

(Note: with default `--count 1`, the nesting logic is less essential — see Group C.)

### Group B — Code organization *(settled 2026-05-23)*

4. ~~Laser params file~~: **new `profiles/laser_materials.yaml`** (separate from spindle-oriented `materials.yaml`).
5. ~~Validator architecture~~: **extend `scripts/gcode_validate.py`** with `;HEAD: laser` detection and laser-aware rules. Single entry point.
6. ~~Lesson script home~~: **`lessons/laser/01_spacer/spacer.py` standalone**, imports shared loaders from `scripts/`.

### Group C — Feature scope for v1 *(settled 2026-05-23)*

7. ~~Kerf compensation in v1~~: **dropped from all laser lessons**.
8. ~~`--count` nesting in v1~~: **dropped**. One ring per gcode file. Multiple rings = run the script multiple times. Auto-nesting deferred to Extensions.
9. ~~Laser-variant preflight checklist~~: **included in v1**. New laser-mode checklist (~12 items) separate from the spindle one. Triggered by `;HEAD: laser` marker or job spec.

### Group D — Material settings sourcing *(settled 2026-05-23)*

10. ~~Per-material starting numbers source~~: **I supply starting numbers from public 10W-diode-laser tables**, flagged as "starting points, calibrate per machine in lesson 3b". Lesson 3a ships with usable defaults the user refines empirically.
