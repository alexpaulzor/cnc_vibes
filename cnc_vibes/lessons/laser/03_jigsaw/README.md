# Lesson 3c — Jigsaw puzzle (FUTURE)

> **Aspirational endgoal — not implemented.** This README serves as a brief pointer; the substantive content is in [SPEC.md](SPEC.md).

## What this lesson will be

A wooden jigsaw puzzle that:

1. Has a custom raster-engraved image on its top face.
2. Is cut into classic interlocking puzzle pieces.
3. Has a name embedded such that letters assemble independently and then fit into the larger puzzle.

## Why it's deferred

Three independent sub-problems, each non-trivial:

| | Sub-problem | Effort estimate |
|---|---|---|
| A | Raster engrave a photo from a bitmap image | ~1 session |
| B | Generate classic jigsaw piece cut paths | ~2 sessions |
| C | Name-preserving cut algorithm (the novel piece) | ~1-2 weeks |

The first two have well-known algorithms and existing open-source examples to study. The third is a small research project — I'm not aware of an existing tool that does name-preserving puzzle cuts as a parametric algorithm.

## Suggested phasing

1. **3c-1**: `raster_engrave.py` — image to GCode (Pillow + Floyd-Steinberg).
2. **3c-2**: `tessellate.py` — classic jigsaw pieces (Bezier ball-and-stem tabs).
3. **3c-3**: Integration: engrave then cut. **Already a giftable artifact** at this point — a photo jigsaw.
4. **3c-4**: Name-preserving cut algorithm. Stretch goal.

Each phase produces a real, useful deliverable.

## Status

SPEC.md is the planning document. No code yet. When you're ready to start, begin with 3c-1.

## Existing toolchain this will reuse

- `profiles/laser_materials.yaml` for engrave + cut power/feed
- `scripts/gcode_validate.py` (laser-aware rules)
- The laser-mode GCode pattern from `lessons/laser/01_spacer/` and `02_calibration/`

## New dependencies it will add

- **Pillow** (PIL) — image loading, font rendering, rasterization
- Possibly **numpy** for dithering math
- Possibly **fontTools** for high-quality letter outline extraction (alternative to Pillow's basic font support)
