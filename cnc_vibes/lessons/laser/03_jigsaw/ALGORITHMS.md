# Jigsaw — rules & algorithms

Accumulated design rules and high-level algorithms for the `03_jigsaw`
lesson (`cnc.py jigsaw`). This is the "why" companion to the code in
`geometry.py` (piece generation) and `emitter.py` (G-code emission). If a
rule here disagrees with the code, the code is the source of truth — fix
one or the other.

The pipeline, end to end:

```
word + seed + PuzzleConfig
  └─ geometry.generate_pieces()
       render_letter_polygons()        letters rasterized + contour-traced
       build_pieces_with_shifted_tabs() cell grid w/ interlocking tabs
       carve_letter_pockets()          subtract letters from cells
       merge_small_fragments()         absorb slivers into neighbours
       fuse_counter_fragments()        one disc per letter hole
       round_panel_corners()           fillet the 4 outer corners
  └─ emitter.emit_cut_gcode_full()
       extract_unique_edges()          dedup shared boundaries
       eulerian_order()                continuous-cut routing (CPP)
       chain_contiguous_paths()        fuse into laser-on runs
       decimate_min_segment()          drop sub-tolerance chords
       _append_lead_in_overlap()       re-cut loop starts hot
  └─ jigsaw.render_gcode_previews()    PNG + SVG of the real toolpath
```

---

## Geometry rules

### R1. Loose-fit, centerline cuts
Cuts run on the piece centerline; the laser kerf itself becomes the
clearance between pieces. We do **not** offset for kerf — a piece and its
neighbour share one cut line. Consequence: everything downstream depends
on adjacent pieces describing that shared line **identically** (see A2).

### R2. Interlocking lollipop tabs
Adjacent cells connect with a "lollipop" tab: a short stem rising into a
circular bulb. One cell's outward bulb is the other's inward socket — the
**same curve**. Tab direction (in/out) per interior edge is chosen by a
seeded coin flip shared by both cells.

### R3. Tabs shift to clear letters; drop if they can't
A tab slides along its edge to avoid overlapping a letter stroke
(`find_clear_tab_offset`). If no clear offset exists, the tab is dropped
and that edge becomes a plain (or wavy) cut. Dropped tabs mean two
pieces are only held by their other edges — watch the `dropped` stat.

### R4. Letters are pieces; their counters are drop-ins
Each glyph (N, O, R, A…) is a solid piece that drops into the pocket
carved from the cells. A glyph's **counter** (the hole in O/R/A) becomes
a separate small piece that drops into that hole on assembly. Counters
are intentionally kept (see A4), NOT treated as orphan slivers.

### R5. Letters snap to a horizontal grid line (default on)
`snap_letters_to_grid`: the letter band is vertically centered on the
nearest interior horizontal grid line instead of the middle of a cell
row. Carving letters out of a row *boundary* leaves large, tabbable
chunks; carving them out of a row *middle* leaves thin mid-row slivers
and eats the edges where tabs go. Works best with an even row count (the
panel center is then a grid line) — see the `banner` preset.

### R6. Rounded outer corners are optional
`corner_radius_mm` fillets only the four outer panel corners (morphology
on the panel rectangle, applied to the 4 corner cells). Interior tabs are
untouched. Default 0 = sharp.

### R7. Wavy edges are optional and shared
`wave_amplitude_px > 0` gives internal cell-cell edges an organic
half-sine wave. The wave is computed traversal-invariantly so both cells
share ONE curve (see A2). Panel-perimeter edges stay straight.

---

## Geometry algorithms

### A1. Non-square panels & cells
`panel_mm` = width, `panel_h_mm` = height (None ⇒ square). `piece_mm` =
cell width, `piece_h_mm` = cell height (None ⇒ square). `cols =
panel_w // piece_w`, `rows = panel_h // piece_h`. The Y-flip in
`img_to_machine_mm` uses `panel_height_mm`, not `panel_mm`. Square
configs are byte-identical to before these fields existed.

### A2. Shared interior edges computed ONCE (critical)
Each interior grid edge (its tab offset, drop decision, bulb vertices,
and wave) is computed **one time in a canonical orientation** and reused
by both adjacent cells — one forward, one reversed. Before this, each
cell sampled its copy of the shared edge independently (opposite
traversal directions → mismatched vertices). `unary_union`/`linemerge`
then couldn't merge the near-coincident curves, fragmenting the boundary
into slivers that the cut left **laser-off gaps** in → pieces didn't
separate. Rule of thumb: **a shared boundary must be vertex-identical
from both sides.** (Same fix applied to `wavy_points`.)

### A3. Sliver merge
`merge_small_fragments` absorbs any cell fragment thinner than
`fragment_min_thickness_px` or smaller than `fragment_min_area_px` into
its largest adjacent **cell** neighbour sharing ≥ `min_shared` px of
boundary. Isolated fragments (no cell neighbour) are left alone.
KNOWN GAP: fragments that border only a *letter* (e.g. a triangle tip
inside the N) aren't merged — this is TODO T2.

### A4. Counter fusion
`fuse_counter_fragments`: cell fragments that fall inside the *same*
glyph interior hole are unioned into one piece. Without this, a counter
straddling a grid line is carved into two half-discs that won't seat.
Counters are detected as fragments inside a glyph's interior ring.

### A5. Corner rounding
`round_panel_corners` clips the corner cells to `mask ∩ corner-box`,
where `mask` is the panel rectangle eroded-then-dilated by the radius
(round joins). Only the 4 corners change; tabs mid-edge are preserved.

---

## Cut-emission rules

### R8. Full power for cuts on this laser
The diode is weak; **cut at 100% power for every material** (calibration
sweeps excepted). `cnc.py jigsaw cut` defaults `--power-percent 100`.
Material-profile `power_percent` values are conservative starting points
for calibration, not cut settings.

### R9. GRBL laser mode fires only while moving
With `$32=1`, the laser is **off whenever the machine is stationary** —
so a `G4` dwell does NOT warm the diode (no motion = no beam, no scorch).
Warmup must be done with MOTION, not dwells. `--warmup-ms*` default to 0
and are kept only for controllers that behave differently.

### R10. The diode ramps over the first few mm of every laser-on
After any laser-off→on, optical power ramps up over ~5–10mm of travel
(driver soft-start / thermal), so the first few mm of every cut path
under-cut. Two levers: (a) minimize laser-on events (A6), (b) re-cut the
cold start when hot (A7). Measure the ramp with
`build/warmup_ramp_test.gcode` (cut it, see how far in each line starts
cutting through) and set `--lead-in-mm` to that + ~2mm.

### R11. Cut order: letters → interior → panel
Letters first (while stock is most rigid), interior next, panel border
last (so the sheet stays attached until the final cut). Continuous
routing operates within each tier.

---

## Cut-emission algorithms

### A6. Continuous-cut routing (Eulerian + Chinese-Postman)
`eulerian_order` walks the deduped edge graph as continuous trails
(Hierholzer) so the laser cuts THROUGH junctions without lifting. Because
letter pockets create odd-degree junctions, a Chinese-Postman pass pairs
odd nodes (greedy nearest-pair on shortest-path distance) and **re-traces
the shortest connector** between each pair, making every connected region
even-degree ⇒ one closed circuit ⇒ one laser-on event per region.
- Trade-off: fewer restarts ⇄ more re-traced (double-cut) length. Current
  code minimizes restarts (1 per region) at ~+40% travel. The re-traced
  connectors are the "extra partial passes" you may notice — they re-cut
  already-cut lines to keep the laser continuous, not for cut quality.
- Tuning ideas (not yet done): optimal min-weight matching (Blossom)
  instead of greedy to shorten connectors; allow a few extra lifts to
  skip the longest bridges now that lead-in handles warmup.

### A7. Lead-in overlap
`_append_lead_in_overlap`: since CPP makes every region a closed loop,
after finishing the loop (laser now hot) re-trace its first
`lead_in_mm` — re-cutting the cold under-cut start. No waste area needed.
Only applies to closed loops. Default 10mm.

### A8. Edge dedup + chaining
`extract_unique_edges` = `unary_union` + `linemerge` of all piece
boundaries → each shared boundary appears once. `chain_contiguous_paths`
fuses consecutive edges whose endpoints coincide (≤0.1mm) into one
continuous G1 run (one M3, one M5).

### A9. Min-segment decimation
`decimate_min_segment` drops points that would make a G1 chord shorter
than `--min-segment-mm`, endpoints preserved. Trims tiny segments that
stall planning. (Puzzle geometry is natively ~0.2mm-resolution, so small
values are usually no-ops.)

### A10. Previews from the emitted G-code
`render_gcode_previews` parses the actual G-code (not the piece polygons)
into PNG + SVG — cuts in red, rapids faint grey — so problems are visible
without reading G-code. Emitted by default with every `cut`.

---

## Laser / material rules

- **Fire risk on cardboard/paper.** Air assist non-negotiable; never
  leave unattended.
- **`cardboard_corrugated_3mm`**: static M3, 100% power, ~600–800mm/min,
  single pass, air assist. The air gap in the flutes usually lets a
  single fast pass through; prefer a 2nd pass over more power (high power
  chars + flares the kerf).
- **M4 dynamic vs M3 static**: M4 scales power with feed (corner-safe) but
  fades at low speed; M3 is constant. We use static for cardboard.

---

## Open TODOs

- **T1 — Raster engrave characterization.** Push a small (~40×40mm) photo
  raster on a couple materials to draw an image *without* burning
  through; flesh out the dials (power, feed, line spacing, halftone vs
  grayscale levels, passes, air assist) and record per-material params.
  Likely a pendant/CLI script.
- **T2 — Orphaned letter-corner slivers.** Small fragments that border
  only a letter (e.g. the N's inner triangle tips) aren't merged by A3
  (cell-neighbour only) and end up orphaned onto a piece the letter cuts
  off. Merge them into the adjacent letter or nearest reachable cell;
  keep counters (R4/A4) intact.
- **T3 — Routing tuning.** Optional: optimal odd-node matching and/or
  allow a few lifts to reduce CPP re-trace overhead (A6).
