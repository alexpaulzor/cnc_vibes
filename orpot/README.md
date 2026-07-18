# orpot — laser-cut orchid pot

A 3mm-MDF orchid pot built from **two spirals that fit together with vertical
ribs**. You cut each spiral flat, then lift one end and the ribbon flexes/twists
up into a 3D coil (like a lifted paper party-spiral). The two coils nest into a
pot: an open, airy wall (good for orchid root airflow) over a drained base.

## Status — phase 1: the spirals

This first phase generates **only the two flat spirals**, meant for
**flex-testing**: cut them, lift an end, and measure how tall the MDF coils
before it cracks. That measured height then drives the *next* phase.

**Deferred** (not built yet):
- vertical ribs sized to the measured flex height, tab/slot into the spirals
- the interlocking end-joint between the two spirals
- a [kerf-bending](https://www.troteclaser.com/en-us/helpcenter/materials/application-techniques/bending-technique)
  pattern to ease the curl
- an inner net-pot liner ledge

## The two spirals

Built in machine mm (Y-up), each part placed so all coordinates are positive.

- **top** — the rim: a constant-width ribbon whose outer edge starts at the
  widest radius and winds **inward** one turn (a "washer that spirals in").
  Default pitch = strip width, so the turns nest tightly.
- **bottom** — the base: a solid disc (the footprint) merged with a ribbon that
  spirals **outward** one turn to the same max radius as the top. The pitch is
  sized so the ribbon's *outer edge* lands on that radius; the open gaps between
  turns are the drainage / airflow.

Defaults follow the initial brief: rim inner Ø 4in (101.6mm), 15mm strip →
Ø131.6mm outside; base Ø 2in (50.8mm). All are CLI-overridable.

Each part is a single simply-connected polygon (a one-revolution offset ribbon
leaves a radial slit, so no interior hole), so the laser cuts one exterior ring
to free it.

## Usage

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Preview outlines (PNG in figs/; --svg also writes an SVG). "both" lays the
# two parts side by side.
.venv/bin/python orpot.py preview --part both --svg

# Emit GRBL laser G-code (build/*.gcode) + a PNG. Each spiral is its own file.
.venv/bin/python orpot.py cut --part both --material mdf_3mm

# Tweak geometry:
.venv/bin/python orpot.py cut --part top --strip-w 12 --top-pitch 12
.venv/bin/python orpot.py cut --part bottom --base-dia 45 --bottom-pitch 30
```

Key flags: `--part {top,bottom,both}`, `--inner-dia`, `--strip-w`, `--base-dia`,
`--turns`, `--top-pitch`, `--bottom-pitch`, `--seg`, `--material`, `--feed`,
`--power`. See `orpot.py cut -h`.

## Cutting

G-code is the deliverable. Conventions match the sibling `jigsawzall` tool and
this machine: GRBL laser mode (`$32=1`), **static M3** constant power at 100%
(the weak diode under-fires on M4 dynamic), a ~1s out-and-back **warmup wiggle**
at the start of every cut to cover the diode cold-start ramp, and per-material
feed/passes from `profiles/laser_materials.yaml` (`mdf_3mm`: 350 mm/min ×
2 passes). The G-code assumes Z is already at focal height in your WCS.

⚠️ MDF smoke is heavy — air assist mandatory. The spiral is a long thin part;
keep it supported so it doesn't shift between passes. Always cut a small test
first.

## Layout

```
orpot.py                       CLI (preview / cut)
spiral.py                      geometry (SpiralConfig, build_top/bottom_spiral)
emit.py                        G-code emission + PNG/SVG preview
profiles/laser_materials.yaml  per-material laser params
tests/test_spiral.py           geometry + G-code invariants
figs/                          committed preview snapshots
build/                         G-code + preview outputs (gitignored)
```
