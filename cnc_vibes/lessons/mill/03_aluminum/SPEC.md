# Lesson 4d — Aluminum milling SPEC

> Implemented 2026-05-24.

## Goal

Two pieces:

1. Document that aluminum milling on this machine is mostly "use lesson 4a's spacer generator with `--material aluminum_6061_3mm`." Material profile already exists and gives safe feeds (chipload 0.015, DOC 0.15 × tool_dia).
2. Add a new generator (`trochoidal_slot.py`) demonstrating the low-engagement clearing strategy aluminum needs on a 500W spindle.

## Why aluminum is special

- **Tiny chipload**. 0.015 mm/tooth vs 0.04 for plywood. Already in `profiles/materials.yaml`.
- **Conservative DOC**. 0.15 × tool_diameter. Already in the profile.
- **Tool engagement matters**. A naïve slot cut at full diameter engagement stalls the 500W spindle. Trochoidal motion keeps engagement low.
- **Lubrication is mandatory**. WD-40 or kerosene every 30-60 seconds. No code can enforce this; the operator must do it.

## Trochoidal slot algorithm

For a slot of (`x0`, `y0`) to (`x0+length`, `y0+width`) at depth `d`:

1. Tool diameter D; loop radius `r` = `min((width - D) / 2, 0.4 × D)`. The min ensures we don't try to loop wider than the slot allows.
2. Per-loop X advance: `step_x` = `0.15 × D`. Small enough that the tool is always engaged on only a small arc.
3. Layer count: `ceil(d / DOC)` where DOC comes from the material profile.
4. For each Z layer:
   - Plunge at the slot's start position (small enough to be safe at plunge_feed).
   - For each X step: move to right side of the loop, then full CCW circle around (cx, cy_center). The full circle clears a small region; the next X step shifts the region forward.
   - Retract Z for the next layer.

The script outputs a parameter summary in the GCode header so you can read off `loop_r`, `step_x`, `layers`, `x_steps_per_layer` and verify they look right before sending.

## CLI

```
python lessons/mill/03_aluminum/trochoidal_slot.py \
    --x0 10 --y0 10 \
    --length 30 --width 6 \
    --depth 3 \
    [--tool flat_3.175mm_2flute] \
    [--material aluminum_6061_3mm] \
    [--spindle-rpm 18000] \
    [--trochoidal-radius-frac 0.4] \
    [--trochoidal-step-frac 0.15]
```

## Decisions made during implementation

- **No 2D pocket generator in v1.** A pocket (rectangular cavity) is the natural extension but the slot is enough to demonstrate the technique and is genuinely useful (panel-mount cutouts, T-slot keys, etc.).
- **Loop radius is capped** by both the requested fraction (0.4 × tool_dia) and the available width ((width − tool_dia) / 2). If the user's slot is too narrow, we shrink the loop rather than try to cut wider than the requested slot.
- **Reuses 4a's machinery indirectly.** `compute_derived` from `scripts/job_params.py` provides the feed and DOC. No new code in `scripts/` or `profiles/`.
- **No coolant macro.** M7/M8 (mist/flood coolant) is optional GRBL feature; emitting it would require wiring a coolant relay. Operator-applied WD-40 is the realistic answer.

## What this lesson does NOT do

- Does **not** cut steel. The 500W spindle can't.
- Does **not** generate aluminum pockets, only straight slots.
- Does **not** enforce lubrication (operator's responsibility).
- Does **not** include chip-load auto-calibration. Lesson-3b-style calibration for the spindle side is a future workstream.

## Extensions

- 2D trochoidal pocket (Y-rows of slot-style motion stepping over).
- Adaptive engagement detection (vary loop_r based on remaining stock).
- Chatter detection via vision (camera watching the chips) → integration with Int-02 snapshot.
- Coolant M7 emission with a wiring SPEC for the relay.
