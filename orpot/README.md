# orpot — laser-cut orchid pot

A 3mm-MDF orchid pot built from **two flat spiral ramps + vertical ribs**. Each
spiral is cut flat; lifting an end lets the flexible MDF climb into a shallow
coil. The two ramps interleave as a **two-start helix** (180° apart) and radial
ribs hold them at their graduated heights — an open, airy wall (good for orchid
root airflow) over a drained base.

## Design (settled)

- **Two flat spiral ramps**, one revolution each, staying parallel to the floor
  and climbing **~40mm** total (the measured flex limit; `--rise`).
- **Interleaved 180°** (two-start helix): bottom winds **out** from the 2in base
  disc, top winds **in** from the rim; they're offset half a turn.
- **6 radial ribs** (`--n-ribs`) with horizontal **capture slots** (material-
  thickness tall × strip-width long) that the ramps thread through, held top and
  bottom without glue. Each rib also has a tab that plugs into a slot in the
  base disc. Because the ramps are 180° out of phase, each rib meets them at two
  different heights → two slots per rib, distributed like a spiral staircase.

Defaults: rim inner Ø 4in (101.6mm), 15mm strip → Ø131.6mm outside; base Ø 2in
(50.8mm). All CLI-overridable.

Preview the assembled form with `orpot.py view` (see `figs/assembly*.png`).

**Deferred:**
- the interlocking end-joint between the two spiral ends
- a [kerf-bending](https://www.troteclaser.com/en-us/helpcenter/materials/application-techniques/bending-technique)
  pattern to ease the curl
- an inner net-pot liner ledge

## The parts

Built in machine mm (Y-up), each placed so all coordinates are positive. The
laser cuts interior holes (slots, openings) first, then each outer profile last.

- **top** — the rim ramp: a constant-width ribbon whose outer edge starts at the
  widest radius and winds **inward** one turn. Its nested turns enclose the
  central opening, which is cut out as a hole.
- **bottom** — the base ramp: a solid disc (footprint, with rib-tab slots)
  merged with a ribbon spiralling **outward** one turn to the same max radius.
- **ribs** — the N radial fins (one sheet), each with two capture slots + a base
  tab. Built in the (radius, height) plane from where each ramp crosses that
  rib's azimuth.

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
