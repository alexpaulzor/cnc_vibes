# Ring layout: circular name puzzle (implementation spec)

Handoff from a cloud prototyping session to the workstation Claude that owns
this repo. **Goal:** add a circular variant of the wave-grid name plate where
the name wraps around a ring instead of running in a row, so long names
(JONATHAN, CHRISTOPHER, MAXIMILIAN) stay at the full 46 mm cap height on
300 mm stock instead of shrinking.

A working reference implementation is on this branch at
[`scripts/ring_prototype.py`](scripts/ring_prototype.py). It imports
`geometry.py` unchanged and reuses its curve, tab, splice and validity helpers.
Port it into `geometry.py`/`jigsaw.py`/`emitter.py` following this document;
treat the prototype as the source of truth for details this spec glosses.

```bash
PYTHONPATH=. python3 scripts/ring_prototype.py JONATHAN              # -> figs/ring_JONATHAN_disc.png
PYTHONPATH=. python3 scripts/ring_prototype.py JONATHAN --debug      # overlay: green tabbed, blue untabbed, red dropped seams
PYTHONPATH=. python3 scripts/ring_prototype.py JONATHAN --shape square --rows 2 --ornament dot
```

~70 s per word at the default 12 variants.

---

## 1. Why

The wave-grid banner fixes cap height at 46 mm and flexes width up to the
290 mm bound; past that it shrinks the font. JONATHAN measured on the current
banner code: **275 × 61 mm panel, letters ~13 mm tall**, too small to survive
cutting. The same word on a 300 mm disc keeps **46 mm caps with a 21.8 mm
minimum letter gap** and tiles cleanly (score 0, 58 pieces).

| Word (sketch font: Arial Bold metrics) | Letters | Cap height | Min letter gap | Hub radius | Pieces | Score (thin, oversized, sliver, nub, dropped tabs) |
|---|---|---|---|---|---|---|
| JONATHAN, disc, 3 rows, heart | 8 | 46 mm | 21.8 mm | 43 mm | 58 | (0, 0, 0, 0, 0) |
| JONATHAN, disc, 2 rows, heart | 8 | 46 mm | 21.8 mm | 43 mm | 51 | (0, 2, 0, 0, 1) |
| JONATHAN, disc, 3 rows, dot | 8 | 46 mm | 22.3 mm | 43 mm | 56 | (0, 0, 0, 0, 1) |
| JONATHAN, square, 3 rows | 8 | 46 mm | 21.8 mm | 43 mm | 71 | (0, 1, 0, 1, 4) |
| ALEXANDRA, disc, 3 rows | 9 | 44 mm | 15.4 mm | 48 mm | 58 | (0, 0, 0, 0, 3) |
| MAXIMILIAN, disc, 3 rows | 10 | 46 mm | 14.9 mm | 53 mm | 63 | (0, 0, 0, 0, 3) |
| CHRISTOPHER, disc, 3 rows | 11 | 42 mm | 14.0 mm | 57 mm | 59 | (0, 2, 0, 0, 8) |
| NORA, disc, 3 rows (don't: see §9) | 4 | 46 mm | 65.3 mm | 24 mm | 44 | (0, 4, 0, 0, 5) |

All at seed 1, 12 variants, 300 mm. "Pieces" includes letters, the ornament
and letter counters (O/A/R holes).

**Font caveat.** The sketches used Liberation Sans Bold (metric-compatible
with Arial Bold, `--font bold`). With no `--font`, `find_font` resolves to
**Arial Black**, which is wider, so expect long names to land a few mm shorter
on cap height. Check both on the workstation.

---

## 2. Frames and conventions

- Image px, y **down**, `px_per_mm = 5`, panel at `(margin_px, margin_px)`,
  same as every other layout. Centre `C = (margin + D/2, margin + D/2)`.
- **Polar angle** `θ` is measured **clockwise from 12 o'clock**:
  `out(θ) = (sin θ, −cos θ)` is the outward unit vector,
  `ang_of(p) = atan2(p.x − C.x, −(p.y − C.y))`.
- **Glyph local frame:** each glyph is rendered and traced by itself
  (`_trace_mask_polygons`), translated so `x = 0` is the ink centre and
  `y = 0` is the **baseline**, y down (so the cap top is at `y = −cap`).
  Keeping y-down means `_letter_caps` and `_letter_edge_point` work
  **unchanged** in this frame: do all per-letter reasoning (caps, side points
  at a height, bridges) locally, then transform points and normals to world.
- **Placement** of a local point at slot angle `θ`, baseline radius `R_in`
  (letter tops point outward, text reads clockwise):

  ```
  world = C + (R_in − y)·out(θ) + x·(cos θ, sin θ)
  shapely affine: [cos θ, −sin θ, sin θ, cos θ, C.x + R_in·sin θ, C.y − R_in·cos θ]
  normals:        (cos θ·nx − sin θ·ny, sin θ·nx + cos θ·ny)
  ```

  Letters in the bottom half read upside down. That is inherent to a single
  ring. The "badge" alternative is in §10.

---

## 3. Sizing: `fit_ring(word, params)`

Radii, outermost first:

```
R_p  = D/2                        panel radius (disc) / inscribed radius (square)
R_o  = R_p − rim_mm               letter tops          (rim_mm = 24 = banner_margin_mm)
R_in = R_o − cap                  letter baselines
r_h  = hub radius                 hub circle
```

1. Start at `cap = cap_h_max_mm` (46, = `banner_letter_h_mm`). Size the font
   from the measured `H` cap ratio so the rendered cap is exactly `cap`.
2. Build the slot list: the word's glyphs plus an optional **ornament** slot
   (§4) at the join.
3. Angular half-extents of each slot at `R_in`: over the glyph's exterior
   vertices, `a = atan2(x, R_in − y)`; `left = −min a`, `right = max a`.
4. **Equal angular gaps** (consistent tracking, the ring analogue of A14):
   `β = (2π − Σ(left + right)) / n_slots`. Walk the slots accumulating
   `left`, then centre, then `right + β`.
5. Rotate every slot so the ornament (or, with no ornament, the end→start
   join) sits at 6 o'clock. The name is then centred at 12 o'clock.
6. Hub radius: `r_h = max(hub_r_min, n_slots·hub_arc / 2π)`, capped at
   `R_in − hub_ring_min` (defaults 22 mm, 30 mm, 26 mm). This gives each
   hub-ring piece ~30 mm of hub arc, enough for a tab.
7. Accept when `β > 0`, the **real** minimum polygon distance between every
   adjacent placed pair (cyclic) is ≥ `min_gap_mm` (14 = tab fit 11 + the
   wave-grid's 3 mm extra), and `r_h ≥ hub_r_min`. Otherwise drop `cap` by
   1 mm and retry, down to `cap_h_min_mm` (18).

The inner side is the tight one: letters converge toward the centre, so the
gap check uses real placed geometry, not the advance widths.

---

## 4. Ornament slot

A drop-in shape at the join that marks where the name starts. It is treated
exactly like a letter: caps, ring seams, carved pocket, own piece. Centred on
the letter band.

| `ornament` | Shape (local frame, centre at `y = −cap/2`) |
|---|---|
| `heart` (default in sketches) | parametric heart, height 0.55·cap, point toward the hub |
| `dot` | circle, radius 0.22·cap |
| `star` | 5-point star r 0.30/0.14·cap, points rounded ±0.03·cap. Likely fragile in wood; not recommended. |
| `None` | no slot; the join is just one more equal gap, so the start is ambiguous |

---

## 5. Background tiling: the polar wave-grid

Same idea as `build_pieces_wave_grid` (letters are fence posts, seams run
letter to letter or letter to border), mapped to polar. Rings from outside in:
**rim ring**, **letter band** (split by `rows − 1` ring seams per gap),
**hub ring**, **hub disc**. Sectors are the gaps between slots, cyclic.

```
         rim  ─────────────────────────────  (panel boundary)
               │ rimsub   │ topcap         rim ring pieces
          ┌────┴──────────┴─── ring (upper, 0.82·cap) ──┐
  letter  │   mid piece   midsub (short names only)     │ letter
  i       └──────────────── ring (lower, 0.18·cap) ─────┘ i+1
               │ botcap  │ hubsub                hub ring pieces
         hub  ─┴─────────┴─────────  hub arcs (split only at spoke ends)
                  \  spokes (pinwheel)  /         hub disc pieces
```

### Seam kinds

Every seam carries an **end type** per end, used by assembly (§6):
`L` = on a letter, `B` = on the panel boundary, `T` = lands on another seam
(T-junction), `J` = shared endpoint with other seams at the same exact point.

| Kind | From → to | Ends | Notes |
|---|---|---|---|
| `hubarc` | hub circle, between consecutive spoke landings | J, J | Inserted **first** (hosts). With no spokes, split the circle at 3 points. |
| `cornerarc` | square only: circle `Rc = R_p + corner_ring_mm` (20) across each corner, ±1° past the edges | B, B | Inserted first. Fences off the deep corners. |
| `topcap` | letter top → rim, launched along `out(θ)`, rim point jittered ±4° | L, B (or T on a corner arc) | `_letter_caps` in the local frame. |
| `botcap` | letter bottom → hub circle, jittered ±6° | L, T | Lands anywhere on the hub circle. |
| `bridge` | across an open mouth (H, N, A feet, U top) | L, L | From `_letter_caps`. **One cap per bridged side:** if a side has a bridge, keep only the cap nearest the letter's centre line. Per-prong caps put ~14 landings on the hub circle, leaving 19 mm arcs with no room for tabs. |
| `ring` | letter i right side → letter i+1 left side at a local height | L, L | Heights per slot, shared by its left and right seams (continuity): rows=3 → `(0.18, 0.82)·cap`, rows=2 → `0.5·cap`, each ±0.07·cap, clamped 4 mm inside the glyph. `_vg_curve` with tangent normals makes these follow the ring. |
| `rimsub` | point on the outermost ring seam → rim | T, B/T | Count from rim **area** in the sector's angular window (§5.1). |
| `midsub` | lower ring seam → upper ring seam | T, T | rows=3 only. Needed only for wide gaps (short names). |
| `hubsub` | point on the innermost ring seam → hub circle | T, T | Count from arc length at the hub-ring mid radius. |
| `spoke` | hub centre → hub circle | J, J | `k_hub` = smallest k with chord `2·r_h·sin(π/k) ≤ hub_piece_mm` (62). Pinwheel: leaves the centre at ±50° off radial (`_vg_bez`, handle 0.45·r_h). |
| `cornersub` | corner arc midpoint (±8°) → square corner | T, B | Splits each corner region in two. |

Every non-hub seam passes through `curved()`, the same "never dead straight"
bow as wave-grid (`_vg_deflection < 2.5 mm` → `_vg_bow`).

### 5.1 Subdivision count

`n_sub(length) = max(0, ceil(length / (1.3 · target_w_mm)) − 1)` with
`target_w_mm = 46`, so no piece exceeds about 60 mm along the ring (the
oversized limit, §7). For the rim the "length" is
`area(panel ∩ wedge(a0..a1) ∩ disc(Rc) − disc(R_o)) / (R_p − R_o)`: an
area-based width, so a square panel's deeper regions get more cuts.
Subdivision angles are evenly spaced in the window, ±2° jitter. The T start
point is the host sample whose polar angle is nearest.

---

## 6. Assembly: generalizing `_vg_assemble`

`assemble()` in the prototype is `_vg_assemble` plus junction support. The
existing function only ever joins seams at letters or the border; the ring
needs T-junctions and shared endpoints. Recommended: extend `_vg_assemble`
with an optional `ends` per seam (default `("L", "L")` keeps wave-grid and
vertex-grid byte-identical), not a fork.

1. **Junction points** = every endpoint of type `T` or `J`.
2. **Tab keep-out:** add a 7 mm disc around every junction to the
   `placed` list passed to `_vg_tab_candidates`, so no bulb sits on a joint.
   (12.5 mm was tried first; it left ring seams and hub arcs with no legal tab position.)
3. **Conflict test** (`_bg_conflict`): evaluate on
   `candidate − union(junction.buffer(min_gap + 3 mm))`, so seams may meet at a
   junction but are still kept ≥ 4 mm apart everywhere else.
4. **Noding (critical):** an endpoint that merely *touches* a line after float
   transforms is not noded by `unary_union`, the face never closes, and
   neighbouring pieces silently merge. The first prototype lost ~70 % of its
   pieces this way. Two fixes, use both:
   - overshoot every `L`, `B` and `T` end by 2 px along its end tangent and
     **do not clip** the seam to the background (the old code's
     `intersection(background)` step). `polygonize` discards the dangling
     stubs;
   - build the net with snap-rounding: `shapely.union_all([...], grid_size=0.1)`
     (shapely ≥ 2.0). Plain `unary_union` has no `grid_size`.
   Only reject a seam when its length inside the background is < 3 mm.
5. **Untabbed structural seams:** when a `J,J` seam (hub arc, spoke) finds no
   tab, keep it as a plain cut (if it doesn't conflict) instead of dropping it.
   Dropping a hub arc merges the hub disc into the ring. Count it in `dropped`.
6. Processing order = list order: hosts (`hubarc`, `cornerarc`) first, then
   caps, bridges, ring seams, subdivisions, spokes, corner splits.

## 7. Validity and scoring

- **`_vg_oversized` must be rotation-invariant for the ring.** The current
  test uses the axis-aligned bbox, which flags every diagonal ring piece. The
  prototype swaps in `oversized_oriented` (min-rotated-rectangle sides:
  area > 50² mm² and (short side > 34 mm or long side > 64 mm)) via a context
  manager, because `_vg_absorb` calls `_vg_oversized` internally. In the port,
  add an `oriented: bool` parameter (or a cfg flag) instead of monkeypatching.
  Keep the banner's axis-aligned behaviour byte-identical.
- **Border floors:** set `tab_border_floor_v_mm = tab_border_floor_h_mm = 7`.
  A ring has no short dimension. With both at 7 the generic
  `panel.exterior.distance(tab) ≥ 7 mm` check is exact on a disc, and the
  bbox-directional check becomes a harmless no-op.
- `_vg_thin_bridge`, `_vg_sliver`, `_vg_border_nub` work as is (the nub test
  measures contact with `panel.boundary`, which is the circle).
- Variant score: `(thin, oversized, sliver, nub, dropped_tabs)`, lowest wins,
  over `variants` (12) seeded as `Random(seed·131 + variant·9973)`. No
  deviation reward: piece count comes out of the subdivision rule.

## 8. Integration plan

### `geometry.py`
- `PuzzleConfig`: `ring: bool = False`, `ring_shape: str = "disc"`
  (`"disc"|"square"`), `ring_rows: int = 3`, `ring_ornament: str | None = "heart"`,
  `ring_rim_mm = 24.0`, `ring_min_gap_mm = 14.0`, `ring_target_w_mm = 46.0`,
  `ring_corner_ring_mm = 20.0`, hub knobs from `RingParams`.
  Reuse `banner_letter_h_mm` as the cap-height ceiling.
- `fit_ring(word, cfg)` → layout; set `panel_w_px_fit = panel_h_px_fit = D·ppm`.
- `ring_letter_layout(word, cfg)` → `(letter_union, slots)`, where each slot
  holds its local polygon, `θ`, `R_in`.
- `build_pieces_ring(seed, letter_union, cfg, layout)`: same contract as
  `build_pieces_wave_grid`, returning `(pieces_dict, stats)`, and
  `stats["cfg"]` carrying the fitted cfg.
- `generate_pieces`: new `elif cfg.ring:` branch before `wave_grid`. Skip
  `merge_small_fragments` as for wave/vertex grids. Call `round_panel_corners`
  **only** for square (it is rectangle-based; `corner_radius_mm = 0` for disc).
- A panel-geometry helper, e.g. `panel_polygon(cfg)`, returning the disc,
  rounded square or rectangle in image px. Everything below that tests
  "is on the panel edge" should use it.

### `emitter.py`: critical for a safe cut
- `_classify_edge` decides `"panel"` by testing the four rectangle sides, and
  the cut-order split uses `_perim_frac` against the bbox edges. **A disc rim
  matches neither**, so the outside profile would not be recognized, would not
  be cut last, and the puzzle could drop out of the stock mid-job (R11).
  Replace both tests with a distance test against the real panel outline
  (`panel_polygon(cfg).exterior.distance(pt) < eps`). Add a test: for a ring
  config the final chain(s) of the emitted cut are the rim.
- `img_to_machine_mm` and the fitted-size header use `puzzle_w_px`/`puzzle_h_px`,
  which work once the fit sets both to `D`.

### `jigsaw.py`
- Flags on `preview` and `cut` (via `_add_size_override_flags`): `--ring`,
  `--ring-shape disc|square`, `--ring-rows 2|3`, `--ornament heart|dot|star|none`,
  `--diameter-mm` (default **290**, matching the banner's 290 mm bound so a disc
  sits inside 300 mm stock with 5 mm to spare; the sketches used 300).
- `_apply_size_overrides`: `--ring` implies the same name-plate tab settings
  as wave-grid (15 px bulb, 30 px neck, 4 mm letter clearance), both border
  floors 7 mm, `corner_radius_mm` 0 for disc and 5 for square.
- Optional hint: when a banner fit lands below ~30 mm cap, print
  `hint: try --ring`. Whether to switch automatically is an open decision (§10).
- `scripts/name_grid.py`: accept `--ring` so seed surveys work for rings.

### Tests (`tests/test_geometry.py`, `tests/test_emitter.py`)
- Tiling: the pieces' union equals the panel area (±0.5 %), with no pairwise
  overlaps above a few px². This catches the noding bug in §6.4.
- Every slot pair's minimum distance ≥ `ring_min_gap_mm` after `fit_ring`.
- Letter-count invariant: one letter piece per glyph, plus the ornament.
- Hub: `k_hub` wedges exist; no piece is an untabbed island other than
  letters, counters and ornament.
- Emitter: rim chains ordered last for disc and square.
- Existing wave/vertex/banner regression tests must stay byte-identical,
  which guards the `_vg_assemble` / `_vg_oversized` generalization.

### Docs
- README "Sizes"/usage: add `--ring`. ALGORITHMS.md: new **R13 Ring layout**
  (rule) and **A17 Polar wave-grid** (algorithm, §§3–7 condensed), plus the
  assembly-junction and noding note as a general rule, since it would bite
  any future layout.

## 9. Known gaps

- **Short names (≤ 5 letters):** equal spacing around 360° leaves 65–115 mm
  gaps (NORA) and oversized middle/hub pieces. Keep short names on the banner.
  If rings are wanted anyway: cap-height growth past 46 mm, or an arc mode
  (text over a partial arc, plain background below).
- **Dropped seams merge pieces.** CHRISTOPHER still shows 2 oversized pieces
  after 12 variants. Levers: more variants, retry a dropped ring seam at a
  second height, or shrink the tab (the 1.0/0.85/0.7 ladder exists already).
- **Square stock:** corner arcs work, but the square variant is the least
  polished (score (0, 1, 0, 1, 4) for JONATHAN). The corner-arc radius and
  its tab placement need a pass.
- **Hub disc** with more than ~6 spokes (CHRISTOPHER: 7) gets long thin
  wedges near the centre. Consider an inner mini-disc or a lower `hub_piece_mm`.
- The prototype's `closest_on` / bracket logic assumes angles don't wrap
  mid-window in odd ways. Fine in practice, but write it with explicit
  unwrapping in the port.

## 10. Decisions pending from Alex

Sketches for each option are in the review gallery from this session. The
defaults below are what the prototype does.

1. **Ornament at the join:** heart (default) / dot / star / none.
2. **Rows across the letter band:** 3 (≈58 pieces for JONATHAN, matches the
   banner's above/between/below look) or 2 (≈51 pieces, chunkier).
3. **Stock shape:** disc (clean, default) or full square with corner pieces.
4. **Bottom-half orientation:** single ring, so bottom letters read upside
   down (default), or a "badge" split where bottom-arc letters flip to read
   upright (counter-clockwise) and the name breaks across the two arcs.
5. **Hub:** interlocking pinwheel (default) or a single drop-in medallion
   (a spot for an engraved age/number).
6. **Auto-switch:** pick ring automatically when the banner cap would fall
   below ~30 mm, or require `--ring`.

## 11. Multi-word rings, the frame, and lessons from a real tuning session

Since the above, `scripts/ring_prototype.py` grew three more features and
`scripts/ring_lint.py` was added as a standing QA pass. All committed on
this branch.

### 11.1 Multi-word rings
`fit_ring`/`build_ring`/`generate` accept `words` as a single string
(unchanged, byte-identical) or a list of words; each word is followed by one
`rp.ornament` slot, so `["NORA","BECS","ALEX"]` reads NORA♥BECS♥ALEX♥ around
the ring. CLI: `word` arg takes `+`-separated words (`"NORA+BECS+ALEX"`).

**Backlog idea from Alex, not yet designed:** make the seam either side of
each word's ornament *interchangeable* between words of equal letter count,
so a multi-name ring's words can be reordered without re-cutting — e.g. swap
which name reads first. Needs the angular slot width, the ring-seam
heights, and the ornament's own geometry to be identical across each word's
boundary; easiest starting case is same-length words. Not started.

### 11.2 Solid outer frame (`frame_mm`) -- ABANDONED, default is now `frame_mm=0`
A continuous annulus around the puzzle, never cut radially. Rim seams land
on its inner circle as shared endpoints; the circle is split into arcs AT
those landings (not a fixed count), so every piece touching the frame gets
its own tab into it.

**Status: dropped per Alex, 2026-09-27.** The physical NORA+BECS+ALEX
contingency cut (frame_mm=15) came off the machine heavily warped as the
ring was cut free -- Alex attributed this to the workpiece itself shifting
during the long uninterrupted rim cut (a workholding problem), not a
G-code defect, and asked to abandon the frame rather than keep chasing
§12.1's start/end-gap hypothesis. Every generation from here on defaults
to `frame_mm=0`: rim seams land directly on the real panel boundary
(`ends="B"`) instead of a frame's inner circle, so every outermost piece
gets a real tab straight into the panel edge -- see §11.5. The frame code
path in `ring_prototype.py` (`frame_mm > 0`, `frame_arcs`, `plain_caps`,
the oversized-check exemption for the frame's giant hole) is left in place
and still works, just unused by default; revisit only if a future need
for a rigid outer lip resurfaces, and reconsider the rim-cut motion first
(§12.1) before assuming a frame is required at all.

**Rendering pitfall (fixed, but worth knowing):** `jigsaw.render_preview`'s
`draw_geom` always paints a piece's holes white, unconditionally — correct
for an ordinary letter counter, catastrophic for the frame, whose "hole" is
the entire puzzle interior: whatever draws before the frame in piece-list
order gets wiped white. `generate()` now sorts pieces by descending total
hole area before assigning serials, so the frame (or any future giant-hole
piece) always draws first.

### 11.3 `scripts/ring_lint.py` — automated QA, run before anything is shown
Two checkers: `lint_gcode()` parses emitted paths for shuttles (3+
back-and-forth reversals in place — the real flicker pattern), short
segments, aliasing runs, out-of-bounds coords, and rim-not-last.
`lint_pieces()` reuses the generator's own oversized/thin/sliver/nub
predicates plus reports every dropped-tab seam. `lint_tab_hardware(cfg)`
checks tab neck/bulb size against `MIN_PROVEN_TAB_STEM_PX`/`_R_PX` — see
11.4. `overlay_grid_mm()` draws a faint 1cm grid with Battleship-style
labels (A/B/C.../1/2/3...) on piece renders so a spot can be named instead
of marked up on an image; never call it on a G-code toolpath render, which
is already plotted in real mm. `annotate_flaws()` draws both checkers'
findings onto a piece render with a legend.

**This tool is not infallible — every check in it was wrong at least once
before it was right.** Three false-positive classes were found and fixed
by cross-referencing actual G-code context against ALGORITHMS.md, not by
guessing:
- A lone near-180° reversal is usually the router's intentional
  Chinese-Postman connector retrace (A6) — only a *run* of 3+ (genuine
  oscillation) is a real defect.
- The warmup wiggle's own tail, AND the real cut's necessary echo of it
  (the first stretch of the real path re-treads the same ground the warmup
  already sampled, by design — that's the whole point of the warmup), both
  look like reversals unless explicitly excluded.
- A piece flagged "oversized" or "sliver" may be the frame (huge by design)
  or a letter counter (small by design, R4/A4) — both need exemption from
  checks written for ordinary background pieces.
- A run of 4+ alternating short-segment turns (`aliasing`) may be a
  genuinely smooth curve finely sampled -- an "S", a rounded tab bulb --
  not a stalled zigzag. Found on the first round-font (Quicksand Bold)
  candidate: an "S"-shaped stretch of real letter outline tripped the old
  heuristic, which only looked at local turn angles with no notion of
  whether the path was actually GOING anywhere. Fixed by requiring low net
  displacement over the run relative to the path length traveled (< 0.4
  ratio) before flagging -- a real flicker pattern covers a lot of path
  length while barely moving; a curve keeps advancing. Expect MORE of this
  class of false alarm now that letters can be genuinely round instead of
  Arial Black's straighter strokes -- verify with a net-displacement check
  (or by eye on the raw coordinates) before trusting any future aliasing
  finding, the same way.
When extending either checker, verify a new "defect" against raw G-code
context or the piece geometry before trusting it, the same way — an
unverified check that cries wolf is worse than no check.

### 11.4 "Rounder letters" means a different font, not a Minkowski sum
`RingParams.letter_round_mm` (a `buffer(r).buffer(-2r).buffer(r)`
close-then-open on the traced glyph outline, applied per letter) has been
**removed** — the CLI flag, the field, and the code path. When Alex asked
for rounder letters, the ask was a genuinely different typeface, not a
morphological filleting pass over Arial Black's corners; the Minkowski
approach also never looked right (the earlier "too bulbous" feedback on
the banner's similar `letter_bold_mm`/`letter_round_mm` was the same
category of problem). Alex also called it "vestigial," and it defaulted
to 0 (unused) in every delivered candidate except the one experiment that
prompted this feedback, so removing it outright (rather than leaving a
dead opt-in) is the right call.

In its place: `find_font()` in `geometry.py` now has a `"round"` font
alias resolving to `fonts/Quicksand-Bold.ttf`, **bundled directly in the
repo** (OFL-1.1, `fonts/Quicksand-OFL-LICENSE.txt`) rather than assumed to
be present on whatever machine runs the generator — the existing Arial
Black/Bold/DejaVu candidates in `find_font` are all absolute paths to
fonts that happen to be installed on this container or a Mac, which
doesn't help the actual cutting machine. Quicksand Bold was picked over
Comfortaa Bold and Dosis ExtraBold (also apt-installed and compared side
by side) for keeping the boldest, most closed stroke shapes of the three
rounded options while still reading as visibly rounded — important since
thin strokes are exactly what tends to fail in wood. Use it with
`RingParams(font="round")` / `--font round`. Not yet re-benchmarked against
the sizing table in §1 (cap height / min letter gap will differ slightly
from Arial Black's metrics); re-run `fit_ring` sizing checks on real names
before treating it as a drop-in default.

### 11.5 Tab-hardware regression (read this before ever tuning tab size)
Chasing a lower dropped-tab count by shrinking `tab_r_px`/`tab_stem_px`
produces pieces that render fine on screen but have a tab neck too narrow
to survive assembly — caught only because Alex looked at a render and said
the necks looked fragile, not by any automated check at the time.
`lint_tab_hardware()` now exists specifically to catch this again:
`MIN_PROVEN_TAB_STEM_PX = 22.0` / `MIN_PROVEN_TAB_R_PX = 11.0` (4.4mm/2.2mm
at 5px/mm) are the smallest values confirmed on a real cut (the 142mm 4-up
ring); the name-plate default (30px/15px = 6mm/3mm bulb) is proven on
every full-size ring cut including JONATHAN. **Tune ring layouts by
adjusting geometry (`rim_mm`, `hub_ring_min_mm`, `rows`, seed, variants) to
open up room for tabs, never by shrinking the tabs themselves** below
those minimums. `generate()`'s own internal variant-scoring search doesn't
know about this constraint either — it will happily pick a smaller-tab
config if asked to; the caller must hold tab size fixed and only vary
geometry.

Worked example (NORA+BECS+ALEX, 290mm disc, 15mm frame): baseline
(`rim_mm=16, hub_ring_min_mm=22`, default safe tabs) scored 29 dropped
tabs. Shrinking tabs to `tab_r_px=13, tab_stem_px=9` (1.8mm neck — well
under the minimum) got that down to 4, but with dangerously fragile tabs.
Holding tabs at the safe default and instead sweeping `rim_mm`/
`hub_ring_min_mm` (opening more background room) found `rim_mm=24,
hub_ring_min_mm=30` at 8 dropped tabs with zero lint defects — worse than
the unsafe version's score, better than baseline, and actually safe to cut.

### 11.6 `no_interlock` check: every outermost piece must grip its neighbor
Added alongside the frame removal (§11.2). With `frame_mm=0`, the true
panel boundary is a `topcap`/`botcap`/`bridge` seam's `ends` containing
`"B"`; if that seam's `status` is `"plain"` (cut all the way through but
no tab fit), the piece it borders can touch its neighbor but not interlock
with it -- exactly the "outermost pieces abutting without interlock"
failure Alex flagged. `lint_pieces()` now reports this as a `no_interlock`
finding, separate from `no_tab` (which covers a seam that got *merged*,
i.e. `status="drop"`, not one that's cut clean but tab-less). `rimsub`
seams can't trigger this (no `plain_ok`, so they're always `"ok"` or
`"drop"`, never `"plain"`). A 24-seed sweep of NORA+BECS+ALEX at
`frame_mm=0, rim_mm=24, hub_ring_min_mm=30` with safe tabs (15px/30px)
found **every seed clears `no_interlock=0`** -- once the frame's
constrained inner-circle geometry is gone, the rim naturally has enough
room for a real tab at every outer piece. Seed 11 was selected as the
delivered no-frame candidate: 0 `no_interlock`, 0 `elongated`, 0 real
G-code defects, safe tab hardware, 9 `no_tab` (all interior/hub merges,
none on the panel boundary), 61 pieces.

### 11.7 Center medallion text (`RingParams.center_text`)
**Status: implemented, `scripts/ring_prototype.py`.** The extra-credit ask
from earlier this session: two lines of plain, non-wrapped text (e.g.
`("THE", "PAULS")`) in the hub disc, exempt from the ring's own
letter-gap rules, each word fused into one piece so it survives handling.

**Implementation:** `RingParams.center_text: tuple | None = None` (CLI
`--center-text "THE+PAULS"`), plus `center_text_cap_mm` (13, shrinks in
1mm steps to `center_text_cap_min_mm`=6 if it doesn't fit),
`center_text_gap_mm` (3, between the two lines), `center_text_baseline_mm`
(5, the support-bar height under each line), `center_text_fit_frac` (0.82
of the hub radius `r_h`). `center_text_block()` renders each word with
`glyph_local()` (already generalizes to a whole string, not just one
char) and unions it with a support bar spanning its width just below the
baseline -- **critical detail:** the bar must overlap a couple px PAST
the glyph's own lowest point, not start exactly at the nominal baseline
y=0, because font metrics leave a hairline gap there; get this wrong (as
the first version did) and `unary_union` produces a MultiPolygon of
disconnected per-letter chips instead of one fused word piece, which
defeats the entire point ("so the tiny pieces don't break apart") while
looking fine in a quick glance at the render -- caught only by explicitly
counting pieces near the hub center, not by eyeballing. Each word's block
is independent of the other (no bar connects "THE" to "PAULS"), so they
come out of the hub as two separate one-piece medallion pieces, matching
"treating each word like a single letter of the full-sized text."

**Wiring:** when `center_text` fits, the block is unioned straight into
`letter_union` before the background carve, so it rides the *existing*
letter-pocket machinery (`carve_letter_pockets`, `fuse_counter_fragments`)
for free -- no new seam/tab bookkeeping needed, same as why the main ring
letters themselves never need tabs (a letter just fills its own pocket by
shape). Setting `center_text` also forces the hub disc to skip pinwheel
spoke generation entirely (stays one undivided background piece) so
nothing slices through the text; `oversized_oriented()` gained a matching
exemption for that one big medallion-background piece, keyed off a
`(Cx, Cy, r_h)` tuple that has to be threaded through explicitly --
**it's process-global state during generation only**, so a caller
re-running `lint_pieces()` from a pickled `(pieces, cfg, st, panel)` tuple
in a fresh process (this tool's own normal workflow all session) must
pass `center_medallion=st["center_medallion"]` or the exemption silently
doesn't apply and the medallion background piece gets misreported as
oversized. `generate()` now stashes it in `st` for exactly this reason.

**Verified:** NORA+BECS+ALEX, no frame, round font, distinct tabs, seed
11: "THE"/"PAULS" render as two clean fused pieces centered in the hub
(cap settled at the full 13mm, easily inside `r_h`=57.8mm with room to
spare), 0 `no_interlock`, 0 `elongated`, tab hardware safe. One `oversized`
finding remains at seed 11 specifically, elsewhere in the ring (unrelated
to the medallion, centroid nowhere near hub) -- combining round font +
distinct tabs + center text shifts the wave-grid subdivision boundaries
enough that seed 11 (clean for the no-frame-only candidate) isn't
automatically clean for the full combined feature set; screen a fresh
seed pool for the actual delivered candidate rather than assuming a seed
that worked for one feature set carries over.

**Not yet done:** the double-extra-credit ask (rotate the medallion so no
single outer name is privileged as "up") isn't touched by this --
`center_text` places the block at a fixed 12-o'clock-relative orientation
regardless of which outer word starts there. Sizing (`cap_mm` 13/6mm
default/floor) is a first guess, not benchmarked against real letter
legibility at this scale the way §1's table does for the outer ring.

## 12. TODO, from cutting the real contingency puzzle

Two issues reported after physically cutting and assembling the
NORA+BECS+ALEX contingency (frame_mm=15, 290mm disc). Not fixed yet —
logged here with the diagnostic legwork already done, so whoever picks
this up isn't starting cold. **Item 1 needs Alex's photo before attempting
a fix**; guessing further without it risks the same false-start pattern
§11.3 already documents for the lint checks.

### 12.1 Outer-rim start/end doesn't fully separate; scattered backside burn spots
**Status: reattributed, superseded by §11.2.** Alex's own read after seeing
the ring warp as it was cut free was that the workpiece itself moved
during the cut (a workholding/hold-down problem), not a G-code defect, and
asked to drop the frame entirely rather than keep chasing this. The
diagnostic work below is kept for reference (the G-code-level observation
that the loop closes cold at an exact coordinate with no hot re-trace
overlap is real and could still matter for some other long uninterrupted
cut), but it is no longer the leading theory for what actually happened on
this cut, and nothing here is currently being pursued. If a similar
symptom shows up again on a future cut with the workpiece properly
secured, this section is the place to pick the investigation back up.

Symptom: a sliver of wood stayed connected where the outermost cut should
have met itself, causing warping/splitting on separation; several distinct
burn spots on the back suggest uneven power/dwell somewhere in that cut's
motion, not just a single clean under-cut.

**Ruled out:** the outer edge is NOT fragmented into multiple G-code paths
(verified on the `frame_mm=15` design -- nothing else touches the frame's
outer ring, so it stays one connected loop end to end; the frame's INNER
circle is deliberately split into many `framearc` segments for per-piece
tabs (§11.2), but that's a different circle from the one the user is
describing).

**Most likely cause, not yet confirmed:** inspected the actual emitted
G-code for that loop. It starts and ends at the *exact same coordinate*
(e.g. `X289.920 Y149.740` both times, to 3 decimals) after one full
uninterrupted lap, with a `warmup_wiggle` (motion.py) out-and-back
excursion BEFORE the real cut begins, not a re-trace AFTER the loop closes.
ALGORITHMS.md A7 describes the latter: cut the loop once (its start is
necessarily under-cut, cold), THEN re-trace that start a second time once
the diode is hot from finishing the loop. This code does the opposite
(warm up first, cut once, stop exactly at closure) -- so there is zero
physical redundancy at the one point the cut has to fully meet itself. If
the diode isn't *quite* at full power by the time the warmup excursion
ends (the default `ramp_ms=1000` is a conservative guess, not measured for
this material/feed), or if a few microns of machine backlash means the
beam doesn't perfectly retrace its own kerf on final approach, that's
exactly this failure: a hairline connection at one specific point, everywhere
else clean.

**Proposed fix (once confirmed):** give closed loops actual overlap, not
just closure -- after reaching back to the start point, continue a few mm
PAST it along the same path (re-cutting that stretch a second time, now
hot), the way `motion.py`'s `follow_through()` already describes for
exactly this purpose. Cheap to try, likely helps regardless of which of
the above is the precise mechanism. Needs the photo first to confirm this
is really a single-point gap (supports the above) rather than something
else entirely (e.g. a kerf/backlash issue visible as a dogleg in the cut
line, which would point elsewhere).

**Secondary hypothesis for the burn spots specifically:** the Eulerian/
Chinese-Postman router (A6) already re-traces some connectors twice by
design elsewhere in the cut (documented, expected). Separately, static
M3 mode fires at constant power regardless of feed, so any point where
GRBL's motion planner slows for a sharp direction change (every tab
neck-to-bulb transition is exactly this) gets more beam dwell than a
straight run -- worth checking whether the burn spots cluster at tab
corners specifically, which would point at cornering dwell rather than
the warmup/closure mechanism above.

### 12.2 Every tab is identical -- any piece's tab fits any matching socket
**Status: implemented, `scripts/ring_prototype.py`, not yet physically
tested.** By design (A1/A2 in ALGORITHMS.md, load-bearing for
correctness): a shared edge's tab is computed ONCE and reused by both
neighbors, so a piece always fits its true partner. Nothing about that
design made a tab *distinctive* -- the same lollipop shape (fixed bulb
radius, fixed neck width) repeated at every interior seam, so a piece's
tab would just as happily nest into a stranger's matching socket. Alex
had to sort loose pieces by wood grain to reassemble before painting.

**Implementation:** `RingParams.distinct_tabs: bool = True` (CLI:
`--no-distinct-tabs` to turn it off). `TAB_SIZE_CLASSES = (0.85, 1.0,
1.10)` -- multipliers on the base `tab_circle_r_px`/`tab_stem_w_px`.
`_tab_size_class(pts)` hashes each seam's own two endpoints (MD5, first
byte mod 3) to deterministically pick one of the three classes; since a
shared edge's seam entry is computed exactly once and read by both
neighboring pieces (A1), hashing the seam's own points is sufficient --
no need to coordinate the choice across pieces separately. The choice
feeds in as the STARTING point of the existing space-constrained
shrink ladder in `assemble()` (previously `(1.0, 0.85, 0.7)` flat,
now `(1.0, 0.85, 0.7) * base_class`), so a seam that can't fit its
assigned class still falls back to smaller sizes exactly as before --
distinctness never costs a dropped tab that would otherwise have fit.

**Safety margins (not yet cut):** at the proven 30px/15px (6mm/3mm)
default, the small class is 25.5px/12.75px neck/bulb -- both comfortably
above `MIN_PROVEN_TAB_STEM_PX`/`_R_PX` (22px/11px, §11.4) even before the
shrink ladder's further fallback. The large class is 33px/16.5px, a
modest ~10% step above the proven default; `lint_tab_hardware()` now
takes an optional `min_class` parameter so a caller using distinct tabs
can validate against the smallest class actually cut rather than the
cfg's base value (`lint_tab_hardware(cfg, min_class=min(TAB_SIZE_CLASSES))`).
Smoke-tested on NORA+BECS+ALEX (seed 11, no frame): tabs spread roughly
evenly across all three classes, `lint_tab_hardware` clean at the
smallest class, no new oversized/elongated/no_interlock findings.
**Not yet on a physical cut** -- treat the same way §11.4 treats any
untested tab dimension: fine to generate and preview, worth a real test
cut before fully trusting the large class holds up and the small class
is actually graspable by hand, not just clear of the lint floor.

**Open question, now narrower:** whether a ~10-15% size spread is
perceptible enough by feel/sight to meaningfully stop a stranger's socket
from accepting a mismatched bulb, or whether the classes need to be
pushed further apart (revisit `TAB_SIZE_CLASSES` after a real assembly
test, not before -- this is exactly the kind of number that looked fine
on screen before, per §11.4's own history).

## 13. Etched orientation/decorative patterns (PLANET, globe ornament)

New for the PLANET puzzle (Alex works at Planet Labs, a satellite imagery
company). Two asks combined into one feature:

1. A **generic orientation problem**: shuffled loose pieces give no way to
   tell "which side is up" before painting. Fix: etch (shallow, non-cutting
   score) some design that's guaranteed to touch every piece.
2. For PLANET specifically: the etched design IS a world map -- an
   orthographic (satellite-view) globe with coastlines + lat/lon graticule
   -- doubling as decoration and as the orientation guarantee, since a
   full-disc design naturally crosses nearly every piece.

### 13.1 `scripts/globe_etch.py` -- orthographic globe line art
`build_globe(lon0, lat0, R, cx, cy, coastlines=None)` returns a list of
open polyline "strokes" (point lists, same image-px frame as everything
else) for the visible hemisphere centered on `(lon0, lat0)`: coastlines
(`project_ring`, clipped at the horizon by checking `cosc = sin(lat0)*
sin(lat) + cos(lat0)*cos(lat)*cos(lon-lon0) >= 0` per densified vertex,
breaking the polyline wherever visibility flips) plus a 30°/30° lat/lon
graticule and the limb (horizon circle) itself. `R` should be the full
panel radius (not just the hub) so the design spans rim to center and
actually has a chance of crossing every piece, not just the hub disc's.

Coastline source: GSHHS crude resolution (`apt install
python-cartopy-data`, then `geopandas.read_file(..., on_invalid="fix")` --
needs `OGR_GEOMETRY_ACCEPT_UNCLOSED_RING=YES` in the environment, some
crude-resolution rings aren't quite closed). **Cached** as
`data/gshhs_coastlines_crude.json` (~140KB, committed) so regenerating a
puzzle never needs geopandas/cartopy-data installed again -- only
`scripts/globe_etch.py --refresh-cache` does, e.g. to switch to a finer
resolution later. Visually verified at seed-independent standalone scale
before wiring into the ring: a genuine "satellite view of Earth" look,
recognizable continents, no visual defects from the crude resolution at
puzzle scale (fine detail would be illegible at this size anyway).

**Default view: centered near San Francisco** (`lon0=-122.4, lat0=37.7`,
Planet Labs HQ) as a small deliberate nod -- easy to change, it's one
function call.

### 13.2 Coverage guarantee: `ensure_full_coverage(pieces, strokes)`
A sparse line-art design (coastlines + a 30°-step graticule) can still
miss an occasional piece by chance -- verified on the first PLANET
candidate: 1 piece out of 51 landed in a gap with no coastline or grid
line through it. `ensure_full_coverage()` finds every piece the given
strokes don't intersect and adds one short tick through its centroid,
along its own longest axis (via `minimum_rotated_rectangle`, so it reads
as a plausible continuation of nearby line-work rather than a random
mark) -- generic, not globe-specific, so any future etch design can reuse
it as a correctness backstop rather than hand-tuning line density per
puzzle. `ring_lint.lint_etch_coverage(pieces, strokes)` is the
corresponding standing check (reports a `no_etch` finding for anything
still missed after patching) -- run it the same way as every other lint
check, never trust an etch design by eye.

### 13.3 New G-code capability: `emitter.emit_etch_gcode()`
A shallow, non-cutting score pass, structurally similar to
`emit_cut_gcode_simple` (nearest-neighbor chain ordering via the existing
`_order_chains_min_travel`/`_fuse_touching_chains`) but for **open**
polylines with no tab/kerf concerns at all -- just line-follow at much
lower power. Reads `material["etch"]` (power_percent/feed_mm_per_min/
passes) rather than silently scaling down the cut settings, so a material
with no etch profile fails loudly instead of gouging. Combine with a
normal cut pass by emitting both and concatenating (etch first, then cut
-- matches common engrave-then-cut practice; the piece hasn't separated
from the stock yet when the etch pass runs, so alignment is trivial).
Concatenate with `emitter.combine_passes()` (§13.11), never naive string
concat.

**Per-stroke warmup wiggle, linear power model (§13.12).** Every etch
stroke gets a `warmup_wiggle()` lead-in (fwd half / back to start, same
mechanism as the cut pass), sized from a linear ramp-vs-power model:
`ramp_ms = WARMUP_MS * (power_percent/100)`. A 25% etch's lead-in is a
quarter of the 1000ms/full-power ramp (~250ms, ~2mm at typical etch
feeds) rather than either the full 1s or nothing. A fused chain (already
touching strokes joined by `_fuse_touching_chains`) has one true start
and gets one wiggle, not one per original stroke. Strokes are NEVER
re-traced/backtracked to avoid a wiggle (no `max_backtrack_ms`-style
logic in the etch path) -- retracing an already-etched line would
double-etch and visibly darken it unevenly, unlike a cut where retracing
the same kerf is invisible.

`laser_materials.yaml` gained an optional `etch:` block per material.
`plywood_baltic_birch_3mm`'s is **calibrated** (§13.10: 25% power /
2500mm/min / 1 pass, from a real scrap grid test) -- any other
material's `etch:` block is still an unverified starting guess until it
gets the same treatment. Score a scrap corner before trusting a real
part.

### 13.4 `lint_gcode(..., is_etch=True)`
The existing G-code checker's `shuttle`/`aliasing`/`rim_not_last` classes
all assume CUT semantics and produced heavy false-positive noise the
first time they were run on etch G-code (59 "defects" on the PLANET
candidate, all illusory): a jagged coastline or a decorative curve
legitimately reverses direction constantly (that's what a coastline
looks like), and unlike a through-cut there's no burn-through/stall risk
from dwelling at 20% power, so `shuttle`/`aliasing` report as `"info"`
instead of `"defect"` when `is_etch=True`. `rim_not_last` is skipped
entirely -- an etch pass has no rim/frame-last ordering requirement,
nothing separates from the stock partway through. `out_of_bounds` and
`short_segment` still apply at full severity (machine-safety / G-code
quality concerns regardless of etch vs. cut). Verified: 0 real defects
on the PLANET etch pass after this fix, versus 59 false ones before it --
same "verify before trusting a new finding" discipline as §11.3/§11.6,
just applied to a genuinely new kind of G-code this checker had never
seen before.

### 13.5 Globe ornament (`ornament="globe"`)
A plain filled disc (like `"dot"`, slightly larger — `0.30*cap_h` radius),
**deliberately not a reproduction of Planet Labs' actual logo** -- a
generic "planet" circle, not anyone's brand mark. Its "globe" look comes
for free from the background etch overlay's graticule/coastline lines
crossing over its placed position, not from special ornament geometry.
**Superseded by §13.7 below** -- Alex clarified he wants the real logo
after all (marketing use, his own desk); `ornament="globe"` still works
as a generic fallback but isn't the default choice for this puzzle anymore.

### 13.7 The real Planet Labs logo (`font="planet_logo"`, `ornament="planet_logo"`)
Alex is a Planet Labs employee, making this for his own desk, and wants
the ACTUAL logo and wordmark, not a generic placeholder. No internet
access in this session to fetch it (site + Wikimedia Commons both blocked
by network egress policy) -- rather than approximate their trademark from
training-data memory or guess at the brand font's name, traced it
directly from a photo Alex sent of a real Planet sticker.

**Pipeline (`scripts/trace_planet_logo.py`, one-off, not re-run
automatically):** crop the photo upright (`data/
planet_logo_source_tight.png`, committed for reproducibility), threshold
into two ink layers by color (dark charcoal wordmark vs. teal brand
ring/period -- simple brightness + channel-difference heuristics, no ML
needed), hand-picked pixel boxes per letter (the six are cleanly
separated, non-touching, so no connected-component search was needed),
trace each with `geometry._trace_mask_polygons` (the same contour tracer
`glyph_local` already uses for system fonts) after a small morphological
closing (dilate+erode) to bridge JPEG/threshold gaps -- caught for real on
"a", whose bowl-counter wasn't quite closed and traced as two disconnected
polygons instead of one with a hole until this was added. Output cached
in `data/planet_logo_glyphs.json`: WKT per glyph (`p,l,a,n,e,t,.`,
lowercase -- the brand is never capitalized), a `cap_ref_px` scaling
reference (the "l"/"t" ascender height, this wordmark's tallest letters,
analogous to a system font's cap-height), and the brand ring's measured
`cx/cy/outer_r/inner_r` relative to the "p" glyph's own local frame.

**Wordmark integration (`RingParams.font = PLANET_LOGO_FONT`):**
`fit_ring()` branches around the whole `G.find_font`/`font.getbbox`
machinery when this sentinel is set -- there's no real font file, so nothing
to ask Pillow to size. Instead `logo_scale = target_cap_px / cap_ref_px`
and each letter is `affinity.scale(traced_glyph, logo_scale, logo_scale)`,
looked up by `c.lower()` (ring letters are conventionally upper()-cased
for placement bookkeeping; the trace is real lowercase brand type). A
request for a character outside `p,l,a,n,e,t,.` raises loudly rather than
silently falling back to a system font, which would defeat the entire
point silently. Everything downstream (placement, tabs, seams, lint) is
unchanged -- the traced glyphs are drop-in `glyph_local()` replacements.

**Ornament integration (`RingParams.ornament = PLANET_LOGO_ORNAMENT`):**
the source logo's brand ring is a thin OPEN stroke (~18px of a 170px
radius, roughly 5% of diameter) sitting behind a NORMAL-sized "p" --
initially assumed the circle WAS the p's bowl (wrong; they're independent
elements, the "p" in the wordmark itself is perfectly ordinary). Cutting
that stroke as a literal thin annulus at ornament scale (~14mm diameter)
would be exactly the kind of fragile sliver this project has flagged
before (§11.4's tab-neck regression, the general thin-bridge checks) --
so `ornament_local("planet_logo", cap_h)` cuts a SOLID disc (same
`0.30*cap_h` radius as `"globe"`) as the physical piece, and
`planet_logo_ornament_artwork(cap_h)` returns the real "p" outline + both
ring-stroke edges as ETCH-only strokes, scaled so the ring's outer radius
matches the disc radius and positioned so the ring -- not the "p" -- sits
at the disc's own center (the disc radius was derived FROM the ring, so
that's the anchor; "p" sits at its measured offset from the ring's true
center). Caller places these local strokes into world space with the
ornament's own `(th, Rin, C)` from `fit_ring`'s result, same `place()`
transform every other slot uses -- so it inherits the same "bottom-of-
ring reads upside down" behavior as any other slot there (§2), which is
correct/expected, not a bug to special-case around.

**First-draft bug (caught before it shipped, not after):** the ornament
artwork initially anchored the "p" glyph at the disc center and added the
ring as an offset from THAT, backwards from how the disc's radius was
actually derived -- produced a ring visibly off-center from the disc
boundary in the render. Caught by cropping and looking at the ornament
piece in isolation before sizing up, the same "verify before trusting"
discipline as every other rendered candidate this session.

**Status:** integrated and rendering correctly (verified: recognizable
traced letterforms in the piece render, ring/p artwork concentric with
the ornament disc, upside-down-at-bottom behavior matches every other
ring slot). Seed-swept for a clean final candidate — see §13.8.

### 13.6 Status (round-font/generic-globe delivery -- superseded by §13.8)
Delivered: PLANET, 290mm disc, no frame, round font, distinct tabs, globe
ornament, globe etch overlay centered near San Francisco. Seed-swept (8
seeds, `rim_mm=27, hub_ring_min_mm=30`) to seed 3: 0 piece findings
(oversized/elongated/no_interlock/no_tab all clear), safe tab hardware
at the smallest distinct-tab class, 0 real G-code defects on both the
etch and cut passes, 100% etch coverage (1 auto-patched tick out of 49
pieces). Both F350 1-pass and F500 2-pass cut variants generated, etch
pass prepended to each. **Not yet**: any physical test cut -- the etch
power/feed numbers are still an unverified starting guess (§13.3); the
cut settings are the same proven values as every prior ring.

### 13.8 Status: real-logo delivery
Delivered: PLANET, real Planet Labs wordmark + brand-ring ornament
(§13.7), 290mm disc, no frame, distinct tabs, globe etch overlay
(coastlines + graticule, centered near San Francisco) plus the ornament's
own etched p+ring detail unioned into the same pass. Seed-swept (8 seeds,
`rim_mm=27, hub_ring_min_mm=30`, `variants=16`) -- seed 1 picked: 51
pieces, only 2 `no_tab` (interior merges, nowhere near the boundary or
the ornament), 0 oversized/elongated/no_interlock, safe tab hardware at
the smallest distinct-tab class, 0 real G-code defects on both etch and
cut passes, 100% etch coverage (1 auto-patched tick). Both F350 1-pass
and F500 2-pass cut variants generated, etch pass prepended to each.

**Separately delivered:** `scripts/etch_calibration.py`, a standalone
self-labeled scrap-test grid (5 power levels x 4 feeds, each swatch
etched at its own exact setting with the numbers traced onto the wood
itself) so the placeholder `etch:` profile in laser_materials.yaml can be
replaced with a real number instead of a guess -- sent to Alex ahead of
the full puzzle files since it doesn't depend on which puzzle design
ships.

**Not yet**: any physical test cut of either the calibration grid or the
real puzzle -- once Alex reports back a good calibration swatch, update
`laser_materials.yaml`'s `etch:` block and this becomes a real, tested
value instead of a starting guess.

### 13.9 How small can PLANET go? (4-up / 2-up on a 300mm panel)

Alex asked for the smallest diameter that still avoids fragile tabs,
ideally fitting 4 discs per 300mm square panel (2 is an acceptable
fallback). Swept `diameter_mm` from 145 down to 75, tabs held EXACTLY at
`MIN_PROVEN_TAB_STEM_PX`/`_R_PX` (22px/11px, §11.4/§11.5) with
`distinct_tabs=False` -- its smallest class (0.85x) would otherwise push
an already-floor-sized tab below the proven minimum, a real bug caught by
`lint_tab_hardware` itself on the first sweep attempt.

**First finding: the real bottleneck isn't tab geometry, it's the traced
letterforms' own stroke width.** The delivered 290mm design's `rim_mm`/
`hub_ring_min_mm` etc. are absolute-mm values tuned for that size; naively
scaling them down proportionally with diameter breaks two different ways
that both trace back to the SAME cause -- those margins need to stay
closer to fixed (not shrink with diameter), because they exist to fit a
FIXED-size tab (the proven floor doesn't get smaller just because the
disc does):
- Scaled proportionally, `hub_r_min_mm`/`min_gap_mm`/etc default to
  values tuned for 290mm and become infeasible below ~125mm (`fit_ring`
  returns `ok=False`) well before tab hardware is actually the problem.
- Fixed at `rim_mm=18, hub_ring_min_mm=20` (vs. the 290mm design's 27/30
  -- notably NOT scaled down by the same 140/290 ratio) `min_gap_mm`
  derived from the actual tab length in play (`(tab_stem_px + 2*tab_r_px)
  /ppm + 2mm` clearance, ~11mm at the proven floor, vs. the default
  14mm tuned for the larger default tabs) rather than the disc: seams get
  enough absolute room to fit a tab regardless of how small the disc is,
  **down to where the traced glyphs themselves run out of stroke width**
  -- confirmed by a parallel morphological-opening sweep (erode-then-
  dilate by increasing radii, find the largest radius that doesn't shrink
  a glyph's area by >3%) measuring the thinnest stroke across all seven
  traced glyphs at each candidate cap height. Below `cap_mm≈18` (the
  existing `cap_h_min_mm` floor) the thinnest stroke ("t") drops under
  1.5mm -- fragile in 3mm ply regardless of anything tab-related. This is
  a property of the ACTUAL brand font (traced faithfully, not a bold
  placeholder) -- §13.7 already flagged this tradeoff in the abstract;
  this sweep puts a real number on it.

**Two real candidates**, both `rim_mm=18, hub_ring_min_mm=20,
hub_r_min_mm=11, hub_arc_mm=20, target_w_mm=40, distinct_tabs=False`,
tabs at the proven floor:

| | diameter | cap height | pieces | no_tab | min letter stroke | fits |
|---|---|---|---|---|---|---|
| 4-up | 140mm | 21mm | 25 | 3 | ~1.6mm | 2x2, 280mm across, 20mm total margin in 300mm |
| 2-up | 175mm | 38mm | 30 | 8 (unswept) | ~3.0mm | diagonal packing, the max 2 circles fit in a 300mm square |

4-up's letter strokes are thinner than anything cut so far in this
project (the 290mm/1-up delivery measures ~4.6mm at its thinnest by the
same method) -- workable, but a real fragility risk specific to this
font, worth its own scrap confirmation separate from the etch power/feed
test. 2-up's margins are back in the same range as every prior delivery.
**Waiting on Alex's choice before running a full seed sweep for a clean
final candidate at whichever size he picks** -- neither 140mm nor 175mm
above has been screened past seed 1.

**Resolved: Alex picked 2-up (175mm).** Seed-swept (8 seeds) to seed 2:
33 pieces, 6 interior merges only, 0 oversized/elongated, safe tab
hardware, 0 real G-code defects, 100% etch coverage. Delivered.

Also caught and fixed a real orientation bug on this candidate: the
ornament's etched "p" was rotating WITH the ring (upside-down whenever
the join lands in the bottom half, same as any letter) -- correct for a
spelled letter (§2), wrong for a brand mark that isn't part of the word.
Added `place_upright()` (translation-only placement -- the disc itself is
a circle, so rotation never affected the physical piece, only the etched
decoration) and used it for the ornament artwork specifically. Verified
the ring stays exactly circular (must, being pure translation) and the
"p" now reads upright regardless of ring position.

### 13.10 Etch calibration: real numbers, not a guess
Alex ran `scripts/etch_calibration.py`'s grid on scrap
`plywood_baltic_birch_3mm` and picked **25% power / 2500mm/min** by
inspection: clearly visible medium-brown contrast, no smoke, not
approaching cut depth; 30% showed diminishing returns over 25% (no more
visible, just more energy); 10-15% too faint to rely on once painted
over. `laser_materials.yaml`'s `etch:` block for that material is now a
tested value, not a starting guess -- every etch pass generated from here
on for this material uses it. Other materials' `etch:` blocks (should any
get added) still need their own scrap test before being trusted.

### 13.11 Fixed: duplicate `$32=1` in combined etch+cut files
Every combined etch+cut file delivered earlier in this project used naive
string concatenation of two full `_header()`-built programs (etch, then
cut), which duplicated the ENTIRE `_PREAMBLE` mid-file -- including
`$32=1`, a GRBL **settings write**, not a motion command, landing where a
sender expects ordinary G-code. This is the most likely cause of a real
physical run stalling partway through on the 175mm 2-up PLANET job. Fixed
with a new `emitter.combine_passes(*gcodes)`: keeps the first program's
preamble, strips every subsequent program's `$32=1`/`G21`/`G90`/`M5`/`G0
X0 Y0` block (keeping its descriptive header comments). Verified on the
actual broken file's regenerated equivalent: `$32=1` count went from 2 to
1. This is now the required way to concatenate any two emitted programs
-- never `a + b` string concat.

### 13.12 Etch warmup wiggle, linear power model
Alex, after a physical scrap-piece photo showed the etched "p" ring's
outer circle visibly faint for the first stretch after the seam (cold
diode start, same effect §12.1 documented for cuts): "treat the warmup
time as linear and implement the wiggle behavior for etching too. But it
always must wiggle every start, rather than 'follow-through' on loop cuts
or retracing already-etched areas to avoid a warmup wiggle." Implemented
in `emit_etch_gcode()` (§13.3): `ramp_ms = WARMUP_MS * (power_percent /
100)` (linear, not the fixed full-power 1000ms), `lead_in_mm` from that
at the etch feed, and every stroke gets `warmup_wiggle()` before its real
path -- mirroring `emit_cut_gcode_full()`'s per-chain pattern (including
the short-stub fallback to a single out-and-back when a stroke is shorter
than half the lead-in). No backtrack/follow-through re-tracing was added
or exists in the etch path, matching the "always wiggle every start"
requirement. `_fuse_touching_chains()` still runs first, so genuinely
continuous strokes get exactly one wiggle at their one true start, not
one per original disconnected polyline segment.

### 13.13 Etch chain ordering: nearest-safe (min separation), not pure nearest
Alex: nearby laser-off/on events can locally heat the material, so a
stroke etched right next to one that JUST finished can scorch/darken more
than its own power/feed setting alone would predict (residual heat bias)
-- but pure farthest-first (the fix tried once before, see below) wastes
machine travel time jumping across the whole plate every move when a
nearer stroke would have been just as thermally safe. Wants: from the
chain just cut, jump to the CLOSEST remaining chain that still clears a
safety floor (a multiple of that chain's own wiggle lead-in distance,
the physical length scale of the heat just deposited there), falling
back to plain-nearest once too few chains remain to satisfy the floor.

**Not a new algorithm** -- exactly this ordering was already designed and
validated once, in `cnc_calibrate/etch_matrix_cal.py`'s tick-cluster
ordering (`_order_nearest_safe`/`SAFETY_MARGIN`, `git show e6421bf`), for
a different, standalone calibration test pattern with tuple-keyed cells
and an ellipse-based center function. That file has since moved on to a
different test-pattern design and no longer has this code (a parallel
session's track, not touched here). Ported the ALGORITHM (not the code
verbatim) into `emitter.py` to operate on `emit_etch_gcode()`'s real
`chains` list of point-lists in machine mm.

**Implementation:** `emitter.SAFETY_MARGIN = 1.5` (same value as the
original validated version -- an assumed margin, not independently
measured) and `emitter._order_chains_nearest_safe(chains, start,
min_sep_of)`, wired into `emit_etch_gcode()` to run AFTER
`_order_chains_min_travel` + `_fuse_touching_chains`, not instead of
them: fusion still needs strokes grouped adjacently by
`_order_chains_min_travel` to find genuinely touching pairs, so
nearest-safe reorders the FINAL fused chains -- each one a real
laser-off/on event -- rather than raw stroke fragments. `min_sep_of` is
`SAFETY_MARGIN * lead_in_mm`, reusing the per-pass `lead_in_mm` (linear
power model, previous section) already resolved earlier in the same
function call. A chain's own first point stands in for its position for
distance purposes (matches the `G0` entry point the emission loop
actually targets) -- a single-point approximation, not meant to be exact
for long strokes like coastlines.

**Verified** (scratch test, not committed to the repo -- see commit
message for the exact script if it needs re-running):
- Unit-level: 4 synthetic chains at known mm positions with a 3mm floor.
  The chain 1mm from the start point (inside the floor) is correctly
  skipped on the first move even though it's globally closest, revisited
  once a later position makes it safe, and the algorithm correctly falls
  back to the plain-nearest chain when NO candidate clears the floor
  (both remaining chains inside a 10mm floor).
- Integration-level, 6 synthetic clustered strokes on a 300mm panel
  (25%/2500mm/min etch, `lead_in_mm=10.42mm`, floor=`15.62mm`): the plain
  min-travel order placed two chains only **6.08mm** apart mid-sequence
  (under the floor); nearest-safe reordered around that gap to insert a
  **156.2mm** separation there instead, at a total entry-to-entry travel
  cost of 793.8mm vs. 679.3mm for min-travel-only on this deliberately
  clustered synthetic case -- exactly the correctness-over-speed tradeoff
  asked for, not a pure farthest-first blowout. (The one remaining
  6.08mm gap is the required end-of-sequence fallback: only one chain is
  left and it can't be avoided.)
- Real pipeline: ran the actual PLANET globe etch
  (`scripts/globe_etch.py build_globe(lon0=-122.4, lat0=37.7, ...)` on a
  300mm panel config) through the patched `emit_etch_gcode()` -- 490 raw
  strokes fuse to 476 chains, valid G-code, single `$32=1` preamble.
  `ring_lint.lint_gcode(is_etch=True)` reports the identical 16
  `short_segment` defects / 403 `reversal` + 43 `shuttle` + 16 `aliasing`
  info findings before and after this change (confirmed by re-running
  lint against the pre-change code via `git stash`) -- those are
  pre-existing artifacts of the raw coastline point density within each
  stroke, completely unaffected by which order the strokes are cut in.
  **No new G-code defects from this change.**

**Not yet**: no physical test cut of this specific change (it only
reorders WHICH stroke is cut when, not any geometry or power/feed
number) -- the residual-heat-bias hypothesis itself and the `1.5x`
margin are both unverified assumptions carried over from the
`etch_matrix_cal.py` version, same status they had there.
