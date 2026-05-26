# 3e — Laser test card (kerf / tram / dimensional check)

A small, fast, low-cost cut for verifying that the machine, the bed, and
your material params are dialed in *before* you cut a real part. One
file, one cut, calipers in hand.

## What it cuts

A square within a square, centered on WCS origin:

- Outer perimeter (default 50x50mm)
- Inner perimeter (default 30x30mm)

Cut the inner square first so the drop-out doesn't shift after the
outer cut releases the part.

## What to measure (calipers)

| Measurement                         | What it tells you                              |
|-------------------------------------|------------------------------------------------|
| Outer-square outside dim            | Effective kerf: `kerf = 50.0 - measured`       |
| Inner drop-out outside dim          | Kerf cross-check: should match the outer       |
| Wall thickness (4 sides)            | Axis-skew / dimensional drift at 10mm scale    |
| Diagonals A-A vs B-B                | Squareness (no skew, machine is trammed)       |

## Why "centered on origin"

When the WCS origin is set to the *center* of the test square instead
of a corner, all toolpath coords are symmetric around 0 (here, +/- 25
and +/- 15 mm). Useful when stock is registered around a known machine
point (e.g. a chuck or fixture center) rather than against a corner.

The companion `jigsaw.py cut --origin center` uses the same convention.

## Usage

```
# Cardboard tram check (recommended first run on cereal-box cardboard)
python test_card.py --material cardboard_thin_1mm

# Plywood, default 50/30
python test_card.py --material plywood_baltic_birch_3mm

# Custom sizes
python test_card.py --outer 40 --inner 20 --material mdf_3mm
```

Validate before sending to the machine:

```
python cnc.py validate lessons/laser/05_test_card/build/test_card_*.gcode
```

## When to use this vs the other laser-cal lessons

This lesson is the "is the machine even cutting square" check — minutes
to design, minutes to cut, immediately reveals tram and kerf issues. It
is NOT a substitute for the systematic per-material parameter sweep.

- **3b** (power x speed x passes grid) — characterize a new material.
  Run once per material; results feed back into `profiles/laser_materials.yaml`.
- **Int-04** (interactive laser cal) — iterative single-spot Z/power tuning.
  Run when a single setting feels off, or for raster engraving patches.
- **3e** (this lesson) — quick pre-flight before any real cut. Run any
  time you change bed height, swap the lens, or doubt the machine.

## Industry-standard companion tests (not yet in repo)

- **Focus ramp** — cut a single horizontal line with Z ramping linearly
  from -2mm to +2mm. The narrowest, cleanest segment marks true focus.
- **Burn-through ladder** — same line repeated at increasing pass counts;
  shows the minimum passes for full-through cut.
- **Dogbone / interference fit** — for inlay work, pairs of male/female
  squares at varying offsets to find the snug-fit tolerance.

These are good follow-ups if dialing in interference fits or focus
becomes a recurring need.
