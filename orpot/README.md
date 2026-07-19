# orpot — laser-cut orchid pot

A 3mm-MDF orchid pot built from **two flat spiral ramps + vertical ribs**. Each
spiral is cut flat; lifting an end lets the flexible MDF climb into a shallow
coil. The two ramps interleave as a **two-start helix** (180° apart) and radial
ribs hold them at their graduated heights — an open, airy wall (good for orchid
root airflow) over a drained base.

## Design (settled)

The pot is a shallow cone: a small base disc low at the center, a full rim ring
high at the outside, and a wall of two spiral ramps rising between them, tied by
radial ribs.

- **Two flat spiral ramps**, one revolution each, staying parallel to the floor;
  height rises linearly with radius so the ramps run up-and-out from the base to
  the rim (`--rise`, default 40mm). The two ramps are the **same shape**, just
  anchored at opposite ends: the **bottom** at its inner end (a center base
  disc), the **top** at its outer end (a full rim ring). Each ramp is a full turn
  that starts just inside the rim ring and winds in to the base.
- **Interleaved 180°** (two-start helix): bottom winds **out**, top winds **in**,
  offset half a turn.
- **6 radial ribs** (`--n-ribs`), all ~the same wedge: a base tab that drops into
  a slot in the center disc, a slant following the cone up to the rim, and a top
  tab that rises into a slot in the rim ring. Where a ramp crosses a rib mid-wall
  there's an **open notch** (≥3.5mm deep) the ramp rests in — the ribs don't fully
  wrap the ramp, and the ramps are **not** notched (the twist under stretch is
  still unknown, so their crossing angle is left free).

Defaults: rim inner Ø 4in (101.6mm), 15mm strip → Ø131.6mm outside; base Ø 2in
(50.8mm). All CLI-overridable.

Preview: `orpot.py overlay` (both flat patterns superimposed) and `orpot.py view`
(3D assembly). See `figs/overlay.png`, `figs/assembly*.png`.

**Deferred:**
- a [kerf-bending](https://www.troteclaser.com/en-us/helpcenter/materials/application-techniques/bending-technique)
  pattern to ease the curl
- notching the ramps to positively locate them in the ribs (needs the measured
  twist-under-stretch)
- an inner net-pot liner ledge

## The parts

Built in machine mm (Y-up), each placed so all coordinates are positive. The
laser cuts interior holes (slots, openings) first, then each outer profile last.

- **top** — rim ring + inward ramp; the ring gets rib top-tab slots.
- **bottom** — center base disc + outward ramp; the disc gets rib base-tab slots.
- **ribs** — the N radial wedges (one sheet): base tab, cone slant with open
  notches at the mid ramp crossings, flat top, and a top tab into the rim ring.

## Usage

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Preview flat outlines (PNG in figs/; --svg also writes an SVG).
.venv/bin/python orpot.py preview --part all --svg

# 3D wireframe sketch of the assembled pot (figs/assembly.png).
.venv/bin/python orpot.py view                 # --az/--el rotate; --no-ribs
.venv/bin/python orpot.py view --az 0 --el 8   # side profile

# Emit GRBL laser G-code (build/*.gcode) + PNGs. One file per group
# (top / bottom / ribs); "all" emits all three.
.venv/bin/python orpot.py cut --part all --material mdf_3mm

# Tweak geometry:
.venv/bin/python orpot.py cut --part ribs --n-ribs 4
.venv/bin/python orpot.py view --rise 30 --strip-w 20
```

Key flags: `--part {top,bottom,ribs,both,all}`, `--inner-dia`, `--strip-w`,
`--base-dia`, `--turns`, `--top-pitch`, `--bottom-pitch`, `--rise`, `--n-ribs`,
`--seg`, `--material`, `--feed`, `--power`. See `orpot.py cut -h` / `view -h`.

## Cutting

G-code is the deliverable. Conventions match the sibling `jigsawzall` tool and
this machine: GRBL laser mode (`$32=1`), **static M3** constant power at 100%
(the weak diode under-fires on M4 dynamic), a ~1s out-and-back **warmup wiggle**
at the start of every cut to cover the diode cold-start ramp, and per-material
feed/passes from `profiles/laser_materials.yaml` (`mdf_3mm`: 350 mm/min ×
2 passes). Interior holes (slots, openings) are cut before each outer profile so
parts stay anchored. The G-code assumes Z is already at focal height in your WCS.

⚠️ MDF smoke is heavy — air assist mandatory. The spirals are long thin parts;
keep them supported so they don't shift between passes. Always cut a small test
first, and dry-fit the rib slots before committing.

## Layout

```
orpot.py                       CLI (preview / cut / view)
spiral.py                      spiral geometry + 3D helix (SpiralConfig)
ribs.py                        radial ribs, capture slots, base-disc slots
emit.py                        G-code emission + PNG/SVG + 3D assembly sketch
profiles/laser_materials.yaml  per-material laser params
tests/test_spiral.py           geometry + G-code invariants
figs/                          committed preview + assembly snapshots
build/                         G-code + preview outputs (gitignored)
```
