# Handoff: ring jigsaw puzzles (laser-cut), branch `claude/funny-mccarthy-gh3t8s`

Read this first, then `CLAUDE.md` (stated-figure accuracy rule) and
`RING_SPEC.md` (§13.14–13.18, §15, §16, §16.2, §16.3 are the recent work).
Repo: `alexpaulzor/cnc_vibes`, code in `jigsawzall/`. Always develop, commit
and push on `claude/funny-mccarthy-gh3t8s`. Outputs Alex cuts go in
`jigsawzall/out/final/` (git-ignored: use `git add -f`).

## How Alex works (process rules)

- **Do only what he asked.** When he says STOP, stop: no more tool calls.
  When he shows an image and says "only look at X", look only at X.
- **Look critically at your own renders before sending them.** He has had to
  point out asymmetric lobes, notches, stubs, lumps, missing tails. Zoom in
  (render letters large) and check symmetry and consistency yourself first.
  When you can measure something (symmetry, areas), measure it.
- **Don't plow on with a choice he hasn't approved.** For design questions
  (letter shapes, serif style), render options or a specimen and let him
  pick. Never default to an option he called bad.
- **Don't run full puzzle searches** (`ring_prototype.py` without
  `--variants 1`, ~15–25 min each) while he's still iterating on letters or
  layout. Use `--variants 1` quick renders or specimen sheets. Run the
  4-seed search only once he OKs the design, then let him pick the seed.
- **Show several seeds** (comparison grid) and let him choose before G-code.
- Every figure you state must be copied from a source in the same turn
  (CLAUDE.md). For anything you can't re-check, say it's an unverified
  estimate.
- Commit and push after each meaningful step (a stop hook enforces it).

## Standing design rules (puzzle)

**Pieces**
- QA score `(QA defects, thin, oversized, sliver, nub, dropped)` must read
  `(0, 0, 0, 0, 0, *)`. `dropped` (seams cut plain because no tab fit) is
  fine with `--keep-seams`.
- Oversized (`oversized_oriented` in `ring_prototype.py`): area > 2500 mm²
  (50² mm) AND (rotated-bbox short side > 34 mm OR long side > 64 mm).
- Sliver (`_vg_sliver` in `geometry.py`): area < 256 mm² (16²), or no part
  of it is 14 mm thick (erodes away at 7 mm). Slivers get absorbed into a
  neighbour, which can silently MERGE pieces.
- Thin bridge floor 4 mm (`_vg_thin_bridge`).
- No long thin planks: Alex rejected 70 × 26 mm pieces. Keep pieces
  roughly as wide as tall.
- **No single piece for a whole half of the centre disc**, or any huge piece.
- Every piece must interlock (have a tab somewhere). A piece whose seams are
  all plain is loose: avoid.
- Tabs need room: ~24 mm of seam between junctions (`center_arc_min_mm`).

**Letters / layout**
- Centre and ring letters must be **the same cap height** (interchangeable,
  easier to assemble): `center_match_ring_cap = True` (the default).
- Hearts are as tall as the letters they separate (`HEART_CAP_FRAC = 1.0`).
- Family surnames in the centre: drop "THE" and the plural s (PARKER, not
  THE PARKERS).
- Two surnames in the centre must be **equal**: half-turn copies of each
  other (both feet-to-centre, reading clockwise), with a tabbed divider
  between them, so neither is "up". Done with `--center-split`, ring style.
- The centre text must be **integrated with the seams the way the ring
  letters are** (seams run from letter to letter and out to the rim). A
  plain grid with the word sitting on top was rejected as boring.
- The disc is round: never rotate the puzzle in G-code to suit the stand.

**Geometric letter font** (`scripts/geo_font.py`, `--font geo`)
- Letters are built from geometry: straight strokes and circular arcs. No
  TrueType outlines.
- Symmetric letters must be exactly symmetric: mirror (A H I M O T U V W X
  Y), half-turn (N S Z), top/bottom (H I O X). The H crossbar is centred.
- Serif "lobes": at every free stroke end, a circle on EACH side edge, one
  radius back from the end, bulging outward. With the stroke as the neck,
  the end becomes a puzzle-tab bulb. Diameter **2 mm** (`SERIF_MM`; Alex
  picked 2 over 3 and 1.5).
- Model each stroke as drawn with a round pen: lobes sit on the stroke's own
  axis, mirrored about it, at any angle. Slanted ends are cut square to the
  stroke (not flat along the baseline) and pulled back until they touch the
  line. On curves, lobes sit on the true edge (offset curve) and are balanced
  by visible area after rounding.
- **No balls** and no plain round terminals. Every free end gets the lobe
  pair, curved ends (C G J S) included.
- **Round every corner**, inside and out (`FILLET_MM = 0.75`).
- Each end gets both lobes or neither, never one. A lobe is dropped only
  when another stroke is within 3 mm straight ahead of it (`NOTCH_MIN_MM`),
  where it would pinch the wood (E/F middle bars at larger sizes).
- Lobes must not create concavities except the natural neck behind the bulb.
- Q tail: radial, square across the ring at 45°, from inside the bowl to the
  baseline. Its lobes may touch the ring.
- Specimen: `PYTHONPATH=. python3 scripts/geo_font.py OUT.png`. The current
  sheet is `out/final/geo_font_specimen.png` (2 mm). `glyph_units(ch,
  log=[])` logs every lobe decision.

## Machine / G-code rules

- LaserTree 10 W diode on a CNC. **M3 only** (static power), **exactly one
  `$32=1`** (`emitter.combine_passes` handles it).
- Reach (puzzle-job frame, disc bbox 0..290, stock −5..295):
  X −10 .. 295.5; **Y ≤ 293.5**. The Y limit switch tripped at MPos
  Y = 294.453 (`stand_feet_sheet.REACH_MAX_Y_MM`). `_check_xy_envelope`
  takes a separate Y max.
- The laser sits ~80 mm −Y and ~20 mm −X from the spindle, so cut stand
  notches on the **left (−X) edge**.
- Material: `plywood_veneer_3ply_3mm`, cut F350 × 1 pass at 100% (S1000).
- Letter outline etch: `--outline-etch-mm 1.0 --etch-smooth-mm 0.10
  --etch-feed 2000 --etch-power 40`. The same energy/mm as F5000/100%
  but far fewer motion-QA hotspots. The darkness at 40% is not yet verified
  on veneer, so scrap-test it.
- Job order: etch → puzzle cut → corner feet → frame notches **LAST**.
- Frame notches (`--frame-notches --notch-edge left`): 3.2 mm wide, 12 mm
  deep, 200 mm apart. Each slot is one U from the slot end down to x = 0,
  then 2 mm slices alternating slot A / slot B from x < 0 out to x = −10. An
  end-stop abort near the edge then only leaves slot ends, finished by hand.
  Past the stock edge (x < −5), slices cut sides only, never across the bed.
- Stand feet from the same sheet (`--corner-feet`): two keyed 75° feet
  (105 × 23.2 mm) in the right-hand corners, base on the x = 295 edge, the
  top one held under Y 293.5. Cutting along the stock edge over the bed is
  fine.
- Feet from scrap: `python3 scripts/stand_feet_sheet.py out/final --scrap`
  writes 1× and 2× (stacked head-to-tail).
- Check every file: extents, one `$32=1`, no M4, F/S values, cut-pass
  `ring_lint.lint_gcode` clean, `lint_motion` flagged count.

## Key code

- `scripts/ring_prototype.py`: generator. `build_ring` builds the seams;
  `generate` runs the variant search.
  - `_center_split_seams`: ring-style centre text (PARKER/MCGUIRE,
    CAVAGNOLO).
  - `_center_flat_letter_seams`: flat centre text, the newest; seams fan
    from the letters.
  - `_center_flat_grid_seams`: the old grid, now unused.
  - `frame_notch_pass`, `corner_feet_pass`.
- `scripts/geo_font.py`: the geometric font.
- `scripts/stand_feet_sheet.py`: `foot`, `best_foot`, `pair_feet`,
  `compact_layout`, `corner_feet`, `frame_notch_sections`; the reach
  constants live here.
- `scripts/ring_lint.py`: G-code / piece lint.
- Gotcha: never `pkill -f`/`pgrep -f` with a pattern that also appears in
  the same shell command (it kills your own shell, exit 144). Write the PIDs
  to a file in one call and `kill` them in a second.

## Finished puzzles (in `out/final/`)

- `parker_mcguire_s1.gcode`: MELODY/SHANE/LUCA/AURELIA + PARKER/MCGUIRE,
  round font, left-edge notches last.
- `cavagnolo_s2.gcode`: BRIAN/GILLIAN/LUCY/MAIA + CAVAGNOLO round the hub
  with a heart, corner feet, notches last. It crashed at the Y limit on the
  top foot; `cavagnolo_s2_RESUME_feet_slots.gcode` cuts the rest, with the
  feet moved X −2 mm.

## In progress: BROWN family puzzle

Names: CHELSEA / KYLE / KARSON / KAIDEN outside, **BROWN** in the centre as
one straight upright line across the spinning disc, with `--font geo`.

Quick-test command (one variant, about 5 minutes):

```
cd jigsawzall && python3 scripts/ring_prototype.py CHELSEA+KYLE+KARSON+KAIDEN \
  --seed 1 --variants 1 --font geo --center-text BROWN --center-style flat \
  --center-split --pack-words --keep-seams --out /path/preview.png
```

Latest centre layout is `_center_flat_letter_seams` (commit fe2aa4b):
- From each letter's top and bottom, a fan seam out to the rim, spread
  ±60° about 12 and 6 o'clock.
- Bridges across the gaps between letters, plus from B and N out to the
  9 and 3 o'clock edges.
- Concentric arcs at 0.65 × the hub radius, between neighbouring fans.

The seed-1 one-variant run scored `(0,0,0,0,0,70)` with 109 pieces. Preview:
`out/final/brown_fan_s1_preview.png`. **Alex has not approved it yet.** Show
it to him, iterate on his feedback, and only then run seeds 1–4 and let him
pick. G-code then uses the same flags as CAVAGNOLO: material, etch, and
`--frame-notches --notch-edge left --corner-feet`.

Open items:
- Some fan-to-fan pieces are long-ish, and the W–N column above the text has
  no arc (N's fan starts beyond the arc radius).
- 2 mm serifs widen letters a little; the ring's letter gap dropped to about
  9.6 mm.
- Watch the QA for thin wood around the serif necks.
