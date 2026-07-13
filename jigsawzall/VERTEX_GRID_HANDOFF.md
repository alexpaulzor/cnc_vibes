# vertex-grid — HANDOFF (updated 2026-07-12)

Status: the anchored perpendicular-launch tiling (concept **B**) is **implemented in
`vertex_grid.py`** and wired to `jigsaw.py vgrid`. All jigsawzall tests pass (109).
The old Delaunay/region-grow tiling and its 4 KNOWN PROBLEMS are **gone** (replaced).
The old grid `jigsaw.py cut` path is untouched and still the only hardware-verified cutter.

NOT pushed. Prototype lineage + running notes live in `build/` (gitignored):
`build/anchored_seams.py`, `build/tab_knob.py`, `build/VERTEX_GRID_SCRATCH.md`,
`build/anchored_gallery.png`.

Run: `python3 jigsaw.py vgrid --word WOJO --seed 7 --gcode`
→ `figs/vgrid_wojo_seed7.png` (preview) + `build/vgrid_wojo_seed7.gcode`.

---

## THE MODEL (user-locked, 2026-07-12) — anchored perpendicular-launch, concept B
1. Letters are **cut-out pieces**; background pieces **encase** them. No seam ever
   crosses a letter or a counter.
2. **Every seam vertex is ON a letter or the outer border — never on another seam.**
   Seams connect letter→letter or letter→border; **no seam-to-seam junctions.**
3. Seams meet letters **~perpendicular** (obtuse at convex corners); meet the outer
   border within **±30°** of their reference axis.
4. **CAP seams**: vertical, from a letter's top/bottom convex corners to the border
   (wide letters get an extra center cap).
5. **GAP seams** (letter→letter): orthogonal launch → S-curve → **STRAIGHT flat
   segment (≈ tab-bulb wide, carries the one knob)** → S-curve → orthogonal landing.
   The seed rotates the flat; the nearest convex vertex on each side connects to it.
6. **END seams**: outer letters → L/R border.
7. **One REAL jigsaw knob per shared edge** — narrow **neck < bulb** so pieces lock
   (neck≈5mm, bulb≈9mm, reach≈6.5mm). Flip seeded; skip only if it can't clear a letter.
8. **Durability is #1**: no material bridge < `wall_mm` (4mm). Measured by erosion
   (`buffer(-wall/2)` must stay a single non-empty polygon); topological pinches are
   split (`_split_pinched`); the `durable` flag is **honest** (no more false YES).
9. Pieces ~20–60mm; slivers merged (`_merge_small`). Counters = own loose cut-outs.
10. **Fits 300×150mm**: font/gap/margin auto-shrink for long words (`build(..auto_gap)`).
    Panel height ~90–95mm (leaves frame room; < the 150 stock height).

## REPO-WIDE LASER STANDARDS (emit_gcode holds all of these)
Static M3 (never M4) · 100% power (S1000) · 600mm/min single pass birch ·
1s out-and-back warmup per chain · interior-first, plaque border LAST · WCS bottom-left ·
continuous-cut chaining (NN travel order, edges deduped) · 0.15mm decimation ·
rounded plaque corners (5mm) · Arial Black. Verified: `$32=1`, `M3 S1000`, `F600`,
no `M4`, M3/M5 balanced, all coords ≥0 within panel.

## CURRENT STATUS (per name, seed 7)
| word   | pieces | tabs | durable | size (mm) |
|--------|--------|------|---------|-----------|
| WOJO   | 11     | 14   | YES     | 248×95    |
| KAIDEN | 18     | 23   | YES     | 294×90    |
| NORA   | 14     | 17   | YES     | 244×95    |
| KAI    | 11     | 13   | YES     | 166×94    |
| AYANA  | 17     | 21   | YES     | 294×94    |
| KARSON | 16     | 21   | **NO(1)** | 294×90  |

KARSON: one crowded piece still pinches after shrink-to-fit (6 letters into 300mm).
Honestly reported as durable=False. Needs per-piece nudging / seed search / a spacing
floor — the one open durability case.

## KEY CODE (vertex_grid.py)
`_render_letters` → cv2 outer/counter contours · `_build_seams` (cap/gap/end + gap tab
sites) · `knob` (real neck<bulb) · `_s_curve` · `_make_pieces` (polygonize) ·
`_merge_small` · `_split_pinched` · `_durable` · `_add_tabs`/`_place_knob` ·
`_build_one` · `build` (width auto-fit) · `emit_gcode` · `render_preview`.
Tests: `tests/test_vertex_grid.py` (8): pieces/cutouts, no-seam-crosses-letter,
durable-normal-word, one-tab-per-edge, fits-envelope, reasonable-size, gcode WCS/balanced,
static-M3-never-M4.

## OPEN / NEXT
- **Hardware test-cut** a vgrid gcode (e.g. KAIDEN) — validate feed/warmup/tab-fit/kerf
  on real 3mm birch before trusting. (Only real unknown left.)
- **KARSON-class** crowded words: add a seed search or per-piece nudge for the last pinch.
- **Frame/holder** feature — DEFERRED (see memory `vgrid-frame-holder-idea` +
  build/VERTEX_GRID_SCRATCH.md): outer profile at 150mm, puzzle <130mm, leftover =tray.
- Decide if/when vgrid becomes the banner default (currently opt-in; keep both paths
  until the test-cut validates vgrid).
- Kerf/fit clearance for the cut-out letters + counters (currently exact outline; a
  real cut needs the pocket slightly larger than the piece — revisit at test-cut).

## DON'T
- Don't push without an explicit ask (2 remotes: github, origin/gitlab).
- Don't retire the old `cut` path until vgrid is hardware-verified.
- Laser: always static M3 @ 100%, never M4.
