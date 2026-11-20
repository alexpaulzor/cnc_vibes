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

- **Ribs** (`--n-ribs`, default 4): radial wedges spanning hub→rim along the cone
  slant, each with a ≤5mm **tab at each end** — the inner-bottom tab drops into a
  hub slot, the outer-top tab rises into a rim-ring slot (always the same
  distance from the outer edge). A ~5mm **shelf notch** wherever a spiral arm
  crosses cradles it. The arms themselves are **not** slotted.

Preview: `orpot.py preview` (flat disc + ribs). 3D: `orpot.py view` (wireframe)
or `orpot.py scad` → `build/orpot.scad` (open in OpenSCAD, F5). See
`figs/preview_disc.png`, `figs/assembly.png`.

## Hand-editable OpenSCAD (`orpot.scad`)

`orpot.scad` is a **legible, parametric, hand-authored** OpenSCAD file (not
generated) — the starting point for refining the shape directly in OpenSCAD. It
lays out a 2D cutting pattern for one 300×300mm sheet: a spiral disc (two
interleaved cuts + hub/ring slots) with the 4 S-curve ribs nested in the corner
offcuts. Tune `disc_dia` (main size knob) and the rib placement, then Render (F6)
and File → Export → DXF/SVG for the laser. `figs/orpot_scad_preview.png` shows
the default. (This is separate from the Python `scad` command's generated file.)

## Pot holders (`holders.py`)

There are two slot-together holder styles. Each comes in two pot sizes, and every slot is sized from
the material profile:

| style | parts | pot sits on |
|---|---|---|
| `stand` (was `or_brace.scad`) | 2 crossed triangle arms (half-lapped under the pot) + a lock disc with a + slot that drops over the crossing tongues | the disc |
| `flower` (was `or_brace2.scad` flowercube) | 4 flowercube fins in a # (half-lapped at the feet) + a flower top plate held by 8 fin tabs in closed slots | the fin floors |

Pots: `square` (47 → 65 mm, 85 tall, from or_brace2.scad) and `net3`, a 3" net cup. The `net3` size
uses **typical catalogue diameters, not measured**: lip 80, body 72 under the lip, bottom 55. The height
is 62.5: the stand cut for 75 came out 1–1.5 cm too tall, so 12.5 came off. Caliper your cup and pass `--net-lip/--net-top/--net-bottom/--net-height`.

```bash
python holders.py                                   # all four, ply3 -> out/*.gcode, *.png, *_3d.png
python holders.py --style stand --pot net3 --net-height 73
```

Why the first cuts wobbled or cracked: the .scad files drew slots at 3.0 mm (stand) and 3.3 mm
(flowercube), but the ply3 sheet is 2.63 mm. Now the slot width comes from `gcode.fit()`: the slot is the
measured thickness, and the toolpath is kerf-compensated (kerf 0.15 mm from the profile), so the
finished slot is that width too. This is how the tight, snug mdf3old fit was cut. The half-laps engage
10 mm (the stand's were 5 mm). The plate is held by closed slots, not notches cut into its inner
edge. Inside corners are filleted or relieved. `tests/test_holders.py` checks that the joints mate
at any thickness and that the pot clears the wood.

**Deferred / TODO:**
- a 4-spiral variant (`--n-spirals`, someday)
- a [kerf-bending](https://www.troteclaser.com/en-us/helpcenter/materials/application-techniques/bending-technique)
  pattern to ease the curl — see the excellent `~/src/boxes` (boxes.py) library
- an inner net-pot liner ledge

## G-code

Both tools are built for a **weak diode laser**: unlike typical svg→gcode tools
(which assume an instant-on CO2 laser — M4 dynamic power, cut-on-fire), they emit
**static M3 constant power** with a front-loaded **warmup lead-in** that traces
back and forth over the start of each cut so the beam reaches full power before it
bites. Cuts are ordered interior-first / outer-boundary-last so the part stays
anchored to the sheet.

### One command: `orpot_gcode.py`

```bash
python orpot_gcode.py --material mdf3          # -> build/orpot.gcode
```

This drives everything from `orpot.scad`: it exports the **frame** (disc without
the spiral slots + the 4 ribs) as SVG outlines, reads the **spiral centerlines**
the .scad echoes, and cuts the spirals as **single-kerf** open paths (traced once
down the centerline, not both sides of a slot). The spiral is rotated
`spiral_offset` (45°) off the rib slots so the tab between a slot and the spiral
start can't split. Extra flags (`--feed`, `--power`, `--warmup-ms`,
`--origin`, …) pass through to `svg2laser.py`.

### General tool: `svg2laser.py`

Standalone CLI (no dependency on the rest of orpot) — turns *any* polygonal SVG
of cut paths into diode-laser G-code. Closed loops are cut once around; **open
paths (`<polyline>`) are cut once = single kerf**. Straight segments are exact,
curves/arcs flattened. Reads feed/power/passes from
`profiles/laser_materials.yaml` (`--material`) or `--feed/--power/--passes`; also
`--warmup-ms`, `--min-seg`, `--margin`, `--origin {corner,center}`. See
`svg2laser.py -h`.

```bash
openscad -o build/cut.svg -D 'MODE="cut"' orpot.scad   # (double-cuts the spirals)
python svg2laser.py build/cut.svg --material mdf3    # any SVG works
```

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
.venv/bin/python orpot.py cut --part all --material mdf3

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
feed/passes from `profiles/laser_materials.yaml` (`mdf3`: 350 mm/min ×
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
holders.py                     pot holders: stand + flowercube, square + net3 pots
profiles/laser_materials.yaml  per-material laser params
tests/test_spiral.py           geometry + G-code invariants
tests/test_holders.py          holder joints mate, pot clears, parts sound
figs/                          committed preview + assembly snapshots
build/                         G-code + preview outputs (gitignored)
out/                           committed holder G-code + layout / assembly PNGs
```
