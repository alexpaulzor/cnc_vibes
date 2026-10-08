# quickcut

Lean, general-purpose tools for turning drawings into GRBL G-code for a **weak
diode laser**. Extracted from the techniques that actually cut clean in `orpot/`
and `jigsawzall/`, with the cruft left behind.

The guiding rule for a weak (~10W) diode — the opposite of what CO2-oriented
tools assume:

- **Static M3 constant power**, never M4 dynamic. M4 scales power with feedrate;
  a weak diode never reaches cutting threshold that way.
- **Warm the beam up** over the diode's ~1 s cold-start ramp before the cut
  matters (GRBL only fires while moving, so you can't just dwell).
- **Full power, explicit S ceiling.** Power is a percentage of `--s-full-power`,
  which must equal your controller's GRBL **`$30`**. Check it with `$$`.

## svg2gcode.py

SVG of cut paths → validator-clean GRBL laser G-code.

```bash
# from ~/src/vibes
uv run --with pyyaml python quickcut/svg2gcode.py cut.svg -o cut.gcode --material mdf_3mm

# override the recipe, or skip the material profile entirely
uv run python quickcut/svg2gcode.py cut.svg --feed 350 --power 100 --passes 2 --warmup-ms 1000

# center the work on X0 Y0 instead of the default bottom-left-corner origin
uv run --with pyyaml python quickcut/svg2gcode.py cut.svg --material mdf_3mm --origin center
```

The whole program is four steps (read `svg_to_gcode()` first — everything else
is a named subroutine it calls):

```
svg text  --parse_svg-----> polylines in SVG units
          --place_on_bed--> polylines in machine mm (Y-up, positive)
          --order_cuts----> interior loops first, boundary last
          --emit_gcode----> GRBL text
```

### How it cuts

- **Closed loops** (holes, outlines): trace the loop `passes` times in one
  direction — no ping-pong, a loop already returns to its own start — then
  **follow through** past the start for `warmup_mm`, re-cutting the cold-start
  region at full power. The beam turns off mid-arc in already-severed material,
  so small parts drop free with no snag at the burn seam.
- **Open paths**: an out-and-back **warmup wiggle** over the start, then
  **ping-pong** passes (forward, reverse, …) so a multi-pass cut never fires the
  laser on a move back to the start.
- **Cut order**: interior loops first (smallest area first), outer boundary
  last, so the workpiece stays attached to the sheet until the final pass.

### The S / `$30` ceiling

`--s-full-power` (default **1000**) is the S value that means 100% power; the
emitter scales `--power` against it. This must match GRBL `$30`:

- `$30 = 1000` (the usual GRBL laser setting) → default is correct, `S1000` =
  full power.
- If yours reads something else (`$$` to check), pass `--s-full-power <that>`.

This is the one thing `svg2gcode` fixes over the older emitters, which all
hardcoded `S = power% × 10` and only worked by luck of `$30` being 1000.

### Input coverage

SVG `<path>` (lines `M/L/H/V/Z` exact; curves `C/S/Q/T/A` flattened),
`<polygon>`, and `<polyline>`. This covers OpenSCAD 2D exports and most cut
files. It does **not** yet handle bare `<rect>/<circle>/<line>` elements (older
`orpot/svg2laser.py` does) — convert those to paths, or ask to have them added.

## Material profiles

Recipes live in the shared `../material_profiles/laser_materials.yaml` (same file
the orpot/jigsawzall tools use). `--feed/--power/--passes` override the profile.

## Tests

```bash
uv run --with pytest python -m pytest quickcut/tests -q
```

## grbl_model.py — what speed does the head actually reach?

Under static M3 the diode's power is constant, so energy per mm is
power / **actual** speed. `grbl_model.py` replays G-code through a model of
the sender link (char-counting RX buffer, ping-pong, a lines/s ceiling, or
SD-card), GRBL's planner (N-block lookahead, `$11` junction deviation,
`$110/$111` max rate, `$120/$121` accel, newest block ends at rest), `$12`
arc segmentation, and the M3/M4/M5 sync stops. It reports, per laser-on
stroke, the predicted average/min speed and **energy factor** (time spent /
time at commanded F; 2.0 = double the dose), commands/s, and hotspots.

```bash
python quickcut/grbl_model.py job.gcode                     # UNVERIFIED defaults
python quickcut/grbl_model.py job.gcode --dollars dump.txt  # saved `$$` + `$I`
python quickcut/grbl_model.py job.gcode --accel 2000 --sender ping_pong --latency-ms 10
```

Every value not read from a dump/flag is printed as `UNVERIFIED default`
(GRBL 1.1 stock values, not this machine's). `scale_power_to_speed()` is a
software M4 for controllers where M4 doesn't fire: per-move `S` scaled to the
predicted speed.

## arcfit.py — polylines to G2/G3

Greedy ArcWelder-style fit (no maintained Python package does this): grows
arcs through exact endpoints while every vertex stays within `tol_mm` and no
chord bulges more than `tol_mm + source_tol_mm`. GRBL re-chops each arc into
fine `$12` chords, so this cuts the streamed line count and the junction
angles, not the planner's block count.

`smooth_polyline()` + `fit_smooth_arcs()` go further for traced (staircase)
outlines: a smoothing spline kept within a max shape change of the trace,
then tangent-continuous biarcs, so consecutive moves join without corners
(GRBL's `$11` junction slowdowns disappear). Needs scipy. In the etch
emitter: `emit_etch_gcode(smooth_mm=0.10)` / `ring_prototype.py --etch-smooth-mm 0.10`.
