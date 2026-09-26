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
