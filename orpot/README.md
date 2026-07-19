# orpot — laser-cut orchid pot

A 3mm-MDF orchid pot cut from **one disc** with two interleaved thin spiral cuts,
plus separate slot-in **ribs**. The disc stays a single connected piece (solid
central hub + solid outer rim joined by two spiral arms); lifting the hub away
from the rim expands the arms into a two-start helical wall — an open, airy wall
(good for orchid root airflow). The ribs slot in to lock the expanded height.

## Design (single-piece, current)

- **One solid disc.** Two interleaved **open spiral cuts** (thin kerf, nothing
  removed) run from the hub edge out to the rim inner edge. Because the cuts are
  open curves that stop short of both the center and the edge, the disc remains
  one connected piece: a **solid hub**, a **solid rim**, and two spiral **arms**
  between them. Expanding it (lift hub vs rim) raises the arms into the wall.
- **Ramp width = cut spacing.** Two cuts 180° apart with pitch = `n_spirals ×
  strip_w` → the arms sit edge-to-edge (tight pack, no gap). Default ½″ (12.7mm)
  arms and rim; ½″ ribs. `--turns` sets how many revolutions (taller wall).
- **Rib mortises.** Short radial slots are cut into the arms at each rib azimuth
  × arm crossing, sized for the rib to thread through once expanded.
- **Separate ribs** (`--n-ribs`, default 4): radial wedges that span hub→rim,
  with open notches where the arms cross and tabs at hub/rim. They lock the 3D
  height.

Defaults (tight pack): hub Ø 2in (50.8mm), ½″ arms ×2, 1 turn → disc Ø ~127mm.
All CLI-overridable.

Preview: `orpot.py preview` (flat disc + ribs). 3D: `orpot.py view` / `scad`
(these still render the expanded arms + ribs; being updated for the single
piece). See `figs/preview_disc.png`.

**Deferred / TODO:**
- refine the rib shape + hub/rim tab engagement for the single-piece assembly
- rework the 3D `view`/`scad` export to show the single disc expanding
- a 4-spiral variant (`--n-spirals`, someday)
- a [kerf-bending](https://www.troteclaser.com/en-us/helpcenter/materials/application-techniques/bending-technique)
  pattern to ease the curl — see the excellent `~/src/boxes` (boxes.py) library
- an inner net-pot liner ledge

## The parts

Built in machine mm (Y-up), placed so all coordinates are positive.

- **disc** — the single spiral piece: outer profile + rib mortise slots (cut
  first) + the two open spiral cuts (cut before the profile so it stays anchored).
- **ribs** — the N radial wedges (one sheet), with notches at the arm crossings.

## Usage

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Preview flat outlines (PNG in figs/; --svg also writes an SVG).
.venv/bin/python orpot.py preview --part all --svg     # disc + ribs
.venv/bin/python orpot.py preview --part disc

# Emit GRBL laser G-code (build/*.gcode) + PNGs. "all" = disc + ribs.
.venv/bin/python orpot.py cut --part all --material mdf_3mm

# 3D sketch / OpenSCAD of the assembled pot.
.venv/bin/python orpot.py view
.venv/bin/python orpot.py scad          # -> build/orpot.scad (open, press F5)

# Tweak geometry:
.venv/bin/python orpot.py preview --turns 2 --n-ribs 6
```

Key flags: `--part {disc,ribs,all}`, `--n-ribs`, `--strip-w`, `--base-dia`
(hub Ø), `--turns`, `--top-ring-w`… `--rise`, `--seg`, `--material`, `--feed`,
`--power`. See `orpot.py <cmd> -h`.

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
