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
       absorb_letter_slivers()         fold letter-pinched tips into letters
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
on adjacent pieces describing that shared line **identically** (see A1).

### R2. Interlocking lollipop tabs
Adjacent cells connect with a "lollipop" tab: a short stem rising into a
circular bulb. One cell's outward bulb is the other's inward socket — the
**same curve**. Tab direction (in/out) per interior edge is chosen by a
seeded coin flip shared by both cells.

### R3. Tabs slide, then flip, then drop to clear letters
A tab first **slides** along its edge to avoid overlapping a letter stroke
(`find_clear_tab_offset`, center-out). If no offset clears, the tab
**flips** its in/out direction and retries. Only if both directions fail
at every offset is the tab **dropped** and that edge becomes a plain (or
wavy) cut. Dropped tabs mean two pieces are only held by their other
edges — watch the `dropped` stat (it's seed-independent for a given word:
a drop means the letter blocks that edge in *both* directions). `flipped`
counts tabs saved by the direction flip.

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
panel center is then a grid line) — see the `banner` preset. When possible,
letters should be nudged to vertical grid lines as well. Grid lines can be
respaced as needed to align with the actual letter geometries (i.e. not
a fixed-width font, so not a fixed-width grid.)

### R6. Rounded outer corners are optional
`corner_radius_mm` fillets only the four outer panel corners (morphology
on the panel rectangle, applied to the 4 corner cells). Interior tabs are
untouched. Default 0 = sharp.

### R7. Wavy edges (legacy — superseded by the letter-aligned grid)
`wave_amplitude_px > 0` gives internal cell-cell edges a half-sine wave
(A1). A naive bolt-on: uniform wobble on a fixed grid, blind to the
letters. **Superseded** by the letter-aligned grid (R12/A12/A13) for the
`banner` nameplate preset, which now uses `wave_amplitude_px=0`. Wavy mode
still exists for the uniform presets but is not the direction.

### R12. Letter-aligned grid (single-row nameplates)
For a single row of text (`letter_aligned_grid=True`, the `banner` preset),
the grid is derived from the letters instead of a uniform lattice:
- **Letter spacing (A14):** letters are laid out at their natural
  (proportional, kerned) advances plus ONE consistent tracking delta, sized
  so the tightest adjacent pair still clears a full tab (bulge + stick-width
  walls). Not fixed-width; letters may shrink to fit the panel.
- a **vertical seam** passes through each glyph at a SOLID point (A12/`glyph_seam`):
  the ink center when the center has ink (crossbar/mid-arm/central stem/diagonal,
  or a symmetric closed ring like O), else the dominant full-height stroke (L's
  stem, C's back). Never along an edge or through whitespace — so the tab sprouts
  from solid material and neither piece gets a fragile crumb.
- **capped-open letters (C, G)** — a wide stroke at both top and bottom around a
  hollow center — are never sliced through that hollow. Their vertical seam
  routes to an adjacent gap (no left/right slice), and the row boundary for
  their column runs just OUTSIDE the ink (below → the whole letter + counter
  globs onto the TOP piece; above → the BOTTOM), alternating side per occurrence
  so the boundary undulates. The counter stays one piece; the back is never cut.
  This glob seam has priority in the min-column merge (a crowding neighbour's
  seam is dropped, that letter staying whole, rather than losing the glob).
- the **middle (r=1) row boundary undulates**: each column's split anchors to the
  nearest glyph's horizontal feature (A15/`glyph_hcut_y`) — through a crossbar/arm
  where one exists (A sits low), else the ink centroid (an open C → mouth center,
  two robust arcs). The boundary rises and falls letter-to-letter instead of
  running dead flat.
- **fit-to-text (A16):** the panel is sized to the name *within* the `panel_mm`
  bounds — width = text + reserved end-tab margins, height = band + two rows. The
  aspect ratio flexes (KARSON → 150×29, KAI → 150×75); reserving end-tab margin in
  the width budget means long names shrink slightly rather than dropping end tabs.
- 2 rows, straight cuts. Spacing + reserved margins guarantee every gap and both
  end columns have room for a tab plus stick-width walls (no pinched slivers, no
  tab crowding the panel border).

---

## Geometry algorithms

### A1. Shared interior edges computed ONCE (the core invariant)
Each interior grid edge — its tab offset, direction, drop/flip decision,
bulb vertices, and wave — is computed **one time in a canonical
orientation** and reused by both adjacent cells (one forward, one
reversed). **Invariant: a shared boundary must be vertex-identical from
both sides.** Violate it and `unary_union`/`linemerge` can't merge the
near-coincident curves; the boundary fragments into slivers the cut
leaves **laser-off gaps** in, and pieces don't separate. This is the
single most important correctness property in the geometry.

### A2. Tab placement: slide → flip → drop
`find_clear_tab_offset` walks candidate offsets center-out
(`shift_steps` × `shift_step_frac` of the tab length) and returns the
first where the tab bulb clears the letter union by `letter_clearance_px`.
If none clears, the caller flips the tab's in/out direction and calls
again; only if that also fails is the tab dropped (R3). All three
outcomes are decided on the canonical edge so both cells stay consistent
(A1).

### A3. Fragment absorption — the tab-ability rule
**Target rule:** every final piece must carry at least one tab of its
own. Any fragment that a letter cut has severed from all of its tabs —
**regardless of size** — must be absorbed into a neighbouring piece that
*does* have a tab. The lone exception is a fragment fully inside a letter
with no adjacent non-letter piece: that's a **counter** (R4/A4), kept as
a drop-in.

**Current implementation** approximates this with two passes (true
tab-presence detection is the follow-up — see TODO):
- `merge_small_fragments`: a fragment that is thin (< `fragment_min_thickness_px`,
  ~one tab-bulb radius) or small (< `fragment_min_area_px`) folds into the
  adjacent **cell** it shares the most boundary with. (Today it picks the
  largest-area neighbour sharing ≥ `min_shared` px; the target is
  longest-shared-edge, which makes a stronger joint.)
- `absorb_letter_slivers`: a leftover small, non-counter fragment that
  borders only a *letter* folds INTO that letter piece (A5).

### A4. Counter fusion
`fuse_counter_fragments`: cell fragments that fall inside the *same*
glyph interior hole are unioned into one piece. Without this, a counter
straddling a grid line is carved into two half-discs that won't seat.
Counters are detected as fragments inside a glyph's interior ring.

### A5. Absorb letter-pinched slivers
`carve_letter_pockets` keeps even tiny fragments (>10px²) instead of
dropping them (was: drop ≤100px²). Where a letter stroke crosses a cell
near a grid line it leaves a triangle tip (e.g. the N's diagonal crook)
severed from the rest of its cell — it has no tab and can't merge into a
cell (A3). `absorb_letter_slivers` folds any such sub-`fragment_min_area_px`,
non-counter, letter-adjacent fragment INTO the letter piece (the notch
fills; the letter grows by a sliver). Counters (A4) are skipped. Before
this, those tips were dropped → uncut gaps / orphan bits inside letters.
Guarded by `test_no_notch_gaps` (panel must tile completely).

> **Config note (non-square):** `panel_mm`/`piece_mm` are widths;
> `panel_h_mm`/`piece_h_mm` are heights (None ⇒ square). `cols = panel_w //
> piece_w`, `rows = panel_h // piece_h`. `img_to_machine_mm`'s Y-flip uses
> `panel_height_mm`. Corner rounding is R6 (`round_panel_corners` clips the
> 4 corner cells to `mask ∩ corner-box`; no separate algorithm entry).

### A12. Automatic glyph origin (font-independent)
`glyph_origins.auto_glyph_origin(ink)` derives a grid origin from a glyph's
rendered ink mask — no per-glyph/-font lookup table:
- **origin-x** = the leftmost FULL-HEIGHT vertical stroke (longest vertical
  ink run ≥ `stem_frac`·height): stems of B/E/F/H/K/N/P/R, the back of C, a
  side of O/U, etc. No such stem (pure diagonals/curves: A/V/X/Y) → ink
  centroid-x.
- **origin-y** = the vertical center of that stem's run (mid-height for a
  full stem); centroid-y when there's no stem.
This snaps a reference point onto a real edge (the intent behind the manual
dots, which were only a demonstration). Used now to draw the preview crosshair;
the grid SEAM itself is chosen by `glyph_seam` (see R12 and A14). A manual
`USER_ORIGIN_OVERRIDES` table remains only as an escape hatch.

### A13. Letter-aligned node lattice
`build_pieces_letter_aligned(seed, letter_union, cfg, origins)` builds the
R12 grid on an explicit node lattice `node(c, r)`: vertical line xs =
panel-left, each origin-x (clamped monotonic, min one tab-radius apart),
panel-right; `r=0`/`r=2` = panel top/bottom; `r=1` = the origin-y at each
line (the bent boundary). Interior edges get lollipop tabs via the shared
slide→flip→drop helper (`_straight_edge_with_tab`); sloped `r=1` segments
place tabs along the slope automatically (endpoint-driven). The uniform
builder is untouched, so all uniform regression tests stay byte-identical.

### A14. Letter spacing with a guaranteed tab gap
`letter_layout_spaced(word, cfg)` renders the word for the aligned grid:
glyphs at natural advances, then a single uniform tracking delta added to
every gap so the tightest pair's ink-to-ink clearance reaches
`tab_height_px + 2·tab_circle_r_px` (a tab's perpendicular bulge plus a
stick-width wall each side). The font is sized from the panel BOUNDS and
shrinks to fit the reserved width (A16), so long names get smaller letters.
Per-glyph seam-x (`glyph_seam`: ink center / dominant stroke / centroid, plus a
`through_ok` flag — False for capped-open C/G, whose seam routes to a gap and
whose row boundary is globbed just outside the ink, alternating side) and
horizontal-cut y (A15/`glyph_hcut_y`) are computed here on the spread layout;
the spread `letter_union` is carved. Returns `(letter_union, boxes, origins)`
where each origin is `("|", (seam_x, boundary_y))`.

### A15. Feature-anchored horizontal boundary (undulation)
`glyph_origins.glyph_hcut_y(ink)` picks the row where the r=1 boundary crosses
each glyph: the strongest horizontal stroke in the glyph's central band (a
crossbar/arm — A's sits low, H/E's mid) so the cut goes through solid ink and
each half keeps a solid edge; else the ink centroid (an open C → mouth center →
two robust arcs). Searched only in the central band so the split never skims a
letter's very top/bottom into an untabbable sliver. Because different letters'
bars sit at different heights, the boundary rises and falls across the name
instead of running flat.

### A16. Fit-to-text panel sizing
`fit_config(word, cfg)` → `_fit_panel_to_text`: when `fit_to_text` is set
(banner), the panel is sized to the word WITHIN the `panel_mm × panel_h_mm`
bounds — `width = text_total + 2·(tab_len + 2R)` (reserved end-tab margin),
`height = band + 2·row`. Reserving the end-tab margin in the width budget means
a long name shrinks its font rather than dropping its end tabs (KARSON's
either/or). The bounds are exposed as `bounds_w_px`/`bounds_h_px` (font sizing)
while `puzzle_w_px`/`puzzle_h_px` return the fitted size (positioning + build);
the fit is idempotent (recomputed from the immutable bounds), so callers fit
once and `generate_pieces` can fit again safely. Report the FITTED size (not the
bound) when sizing a companion photo engrave.

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
Warmup must be done with MOTION, not dwells. Warmup-dwell flags have been
removed from `jigsaw.py` AND the shared laser CAM (`scripts/laser_cam.py`,
`cam_cli.py`, `help_topics.py`) entirely; cold-start fade is handled by
RAMP (A7). (Lesson 06 `spiral_cal.py` still has a dwell sweep — pending
scrub / repurpose to an ramp sweep.)

### R10. The diode ramps over the first few mm of every laser-on
After any laser-off→on, optical power ramps up over ~5–10mm of travel
(driver soft-start / thermal), so the first few mm of every cut path
under-cut. Two levers: (a) minimize laser-on events (A6), (b) re-cut the
cold start when hot (A7, "ramp"). Measure the ramp duration with
`build/warmup_ramp_test.gcode` (cut it, see how far in each line starts
cutting through) and set `--ramp-ms` so `ramp_ms/1000 * feed_mm_per_s`
covers that distance + margin. Default 1000ms (conservative).

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

### A7. Ramp (lead-in overlap)
Since CPP makes every region a closed loop, after finishing the loop (laser
now hot) `_append_lead_in_overlap` re-traces its start — re-cutting the cold
under-cut section. The distance is derived from a DURATION, `ramp_ms`
(default 1000, conservative): `lead = ramp_ms/1000 * feed_mm_per_s`, so
it auto-scales with feed. No waste area needed; only applies to closed loops.
Future (per design notes): treat the ramp region as "uncut" and, for
open paths, double back or hand it to an adjacent continuous path; worst case
reverse over it (the ramp blends both ways).

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
- **T2 — Letter-aligned grid.** DONE (R12/A12/A13): `banner` preset now
  derives vertical lines from glyph auto-origins with a bent middle
  boundary. Follow-ups below (T6–T9).
- **T3 — True tab-ability merge.** Implement A3's target rule directly:
  detect whether a fragment retains a tab, and absorb any tab-less
  fragment into its longest-shared-edge neighbour (drop the area/thickness
  heuristics; keep counters). Today's code only approximates this.
- **T4 — Routing tuning.** Optional: optimal odd-node matching (Blossom)
  and/or allow a few lifts to reduce CPP re-trace overhead (A6).
- **T5 — Scrub shared-CAM dwell flags.** DONE: warmup G4 dwell removed from
  `scripts/laser_cam.py`, `cam_cli.py`, `help_topics.py`, and lesson 06
  `spiral_cal.py` (all tests assert no dwell). Nothing left to scrub.
- **T6 — Letter-aligned follow-ups.** (a) Letter *spacing*: currently the
  glyphs use PIL default tracking; add a gap sized to fit a full tab
  between letters. (b) Steep-slope rule: if adjacent origin-ys differ too
  much (near-vertical `r=1` segment), omit that edge (merge into one taller
  piece) or widen spacing — rarely hit now that origin-y≈mid. (c) Narrow
  columns: thread a per-edge `edge_tab_len = min(tab_len_px,
  0.4·edge_length)` so a skinny column (e.g. "I") keeps a tab instead of
  dropping it. (d) Multi-row letter-aligned grids (text as a band in a
  taller panel) — currently single-row (2-row) only.
- **T7 — Letter sizing.** Short names (KAI/LEO) still gigantify because the
  font is fit to panel width regardless of letter count; cap letter height
  and center rather than stretch. Independent of the grid.
