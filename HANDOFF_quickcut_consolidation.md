# Handoff: make quickcut the single point of entry for laser G-code (+ clean up branches)

> **Status 2026-11-06 (see claude.log):** branches merged (main = jigsaw branch + this work;
> scads master fast-forwarded). Core built (`quickcut/gcode.py`, `order.py`, `frame.py`). scads/marbles,
> orpot, svg2gcode emit through it; cnc_calibrate, lessons, cam/laser_cam and jigsawzall finish every file
> through `gcode.render()`. A drift-guard test enforces it. **Remaining:** move jigsawzall's own cut/etch
> motion onto `gcode.cut()/etch()` (triage its 51 failing + 8 erroring tests first); give the calibration
> grids a validator exemption or fix their F5000 cells; delete the merged remote branches
> (funny-mccarthy, circular-text) in the GitHub UI; optional OR-tools backend for `order.py`.

Written 2026-11-06. ROADMAP items C108 (cut-order optimizer) and C109 (consolidation) live in
`scads/marbles/clock/ROADMAP.md`. Read `CLAUDE.md` first: two rules there are non-negotiable
(stated-figure accuracy; every laser file starts with a frame pass).

**Constraints that changed:** Alex has dropped the Raspberry Pi and the Xbox controller (a proper
pendant is on order). Compute load and heavy dependencies are **not** a concern: OR-tools, CP-SAT,
numpy, shapely and the like are all fine. `cncctl/HANDOFF.md` and `custom_setups/anolex/laser_trigger/`
are retired.

---

## 0. Do this first: branches (both repos)

State on 2026-11-06 (`git fetch --prune`; "unique" = commits with no patch-equivalent on the default branch):

**cnc_vibes** (default `main`):

| Branch | vs main | Last commit | Verdict |
|---|---|---|---|
| `claude/funny-mccarthy-gh3t8s` | +106 / −0 (fast-forward possible) | 2026-11-05 | **Active jigsaw work by another agent** (PARKER/MCGUIRE rings etc.). Merge into main *when that work is at a stopping point*: check with Alex. 0 conflicts with `wire-marble-track`. |
| `claude/circular-text-puzzle-algorithm-mcm0yl` | +71 / −110 | 2026-10-15 | **Vestigial:** it is an ancestor of `funny-mccarthy` (fully merged there). Delete after funny lands on main. |
| `claude/wire-marble-track` | +11 / −57 | 2026-11-06 | This session's work: frame pass, short material ids, park footer, line-length rule, mdf3 recipe, anolex setup docs, cncctl handoff, this file. **1 conflict vs main:** `jigsawzall/scripts/ring_prototype.py` (material id rename vs main's edits). |

**scads** (default `master`):

| Branch | vs master | Last commit | Verdict |
|---|---|---|---|
| `claude/wire-marble-track` | +103 / −0 (fast-forward) | 2026-11-06 | All the marble-clock work. Merge (fast-forward). |
| `pool` | +5 / −8 | 2022-03-20 "WIP" | Old unrelated WIP (9 files). Alex decides: merge, or tag (`archive/pool`) and delete. |
| `wips` | +16, **no merge base** | 2018-12-12 "wip" | Unrelated history. Same choice: tag `archive/wips`, then delete. |

**Merge order** (cnc_vibes):
1. `funny-mccarthy` → main, once its agent and Alex agree. It's a fast-forward.
2. `wire-marble-track` → main: resolve `ring_prototype.py`.
3. Then run `python3 tools/rename_material_ids.py <cnc_vibes> <scads>`. **Main's newer code still uses the old long
   material ids**: on 2026-11-06, 70 files on main and 37 on funny reference e.g. `mdf_3mm`. The profiles on this
   branch are renamed, so without the re-run those files break.
4. Run every test suite (§4), push, delete the merged branches.

Never rewrite history on someone else's branch (no rebase or force-push on `funny-mccarthy`): merge commits only.

---

## 1. Goal

Today every laser emitter re-implements the same conventions, so every new rule has to be added N times. The frame
pass had to go in 8 places. Move the union of all emitter behaviour into **quickcut**, behind one call that every
generator uses:

```python
from quickcut import gcode           # or: sys.path.insert(.../quickcut); import gcode
lines = gcode.preamble(job)           # $32=1, G21, G90, M5, header comments incl ;HEAD: laser ;MATERIAL:
lines += gcode.cut(polyline, job)     # static M3 + warmup + passes + overlap (one convention)
...
gcode.write(path, lines, job)         # ordering? no: write() = frame + park + line-length + VALIDATE, then write
```

`write()` (or `finish()` returning text) is the **only** way files get written: it adds the frame, adds the park,
enforces line length, runs `gcode_validate`, and refuses to write on a violation. Emitters keep their geometry
(puzzle pieces, spirals, plates) and stop owning G-code conventions.

## 2. The conventions to centralise (the union of all emitters)

Every number below comes from the code or profiles. Re-check before restating (the CLAUDE.md rule).

| Convention | Where it lives now | Notes |
|---|---|---|
| **Frame pass** | `gcode_frame.py` (repo root), called from 8 emitters | Rapid laser-off trace of the job's X/Y extent (arcs as full circles) before the first move. Validator rule `laser_frame`. Move into quickcut. |
| **Park at end** | `quickcut/svg2gcode._footer(lines)` | `M5`, `G0 X0 Y<max cut Y>`. jigsawzall / orpot still end with `G0 X0 Y0`. |
| **Line length < 255** | validator rule `line_length` | Grbl_ESP32's `SDCard.cpp readFileLine(fileLine, 255)`: a longer line silently ends an SD job. This bit Alex once (a long comment). |
| **Static M3 at 100 %**, `M5` before every G0 | quickcut, orpot, jigsawzall cuts | M4 "did not fire at all" on this controller. `$32` was 0 in Alex's dump although every file sends `$32=1` (open question). jigsawzall's photo raster uses M4 + `S0` on travel. |
| **Warmup** | `quickcut/motion.py` `warmup_wiggle`, `follow_through`; profile notes | 1 s at full power = an out-and-back wiggle along the path; closed loops overlap past their start; etch scales the warmup by power. jigsawzall `emitter.py` has its own etch lead-in variants. |
| **Passes** | quickcut (`_emit_cut`), scads `laser_parts.emit_cut`, orpot | Open paths ping-pong; closed loops repeat the same direction. orpot tests check "pass k of n (reverse)". |
| **Cut order: middle-out** | `quickcut.order_cuts` (area sort); scads scripts sort by hand (15 call sites); jigsawzall `order_inside_out`, `greedy_order`, `eulerian_order`, `_order_chains_*` | Every hole / inner cut before the outline containing it, so loose pieces don't shift. jigsawzall already has travel-minimising ordering: fold it in and add C108 (TSP / OR-tools with precedence constraints from a shapely containment tree, plus start-point choice per loop). |
| **Kerf** | profiles `fit:` block; scads buffers polygons ±kerf/2 | Kept part grows kerf/2, holes shrink kerf/2. Bridged knock-outs are cut on the nominal line (`feeder_scad_gcode.py`). |
| **Open paths with bridges** | scads `feeder_scad_gcode.py` / `feeder_nest.py` (`knockout_paths`) | A loop cut as open segments, leaving uncut bridges. Generalise: `gcode.bridged(loop, bridge_points, width)`. |
| **Materials** | `material_profiles/laser_materials.yaml` (laser), `cnc_vibes/profiles/materials.yaml` (mill) | Short ids: `mdf3`, `ply3`, `birch3`, `birch6`, `bamboo3`, `mdf6`, `balsa3`, `acrylic3`, `acrylic3clear`, `acrylic3color`, `card1/2`, `corrugated3`, `cardstock03`; mill `mdf12`, `alu3`, `petg3`. Three different `load_material` functions today (quickcut returns the laser block, orpot / jigsawzall the full record): one loader returning the full record. |
| **File naming** | scads `laser_parts.std_path`, `quickcut.svg2gcode.gcode_name` | `<part>_<variant>_<ceil W>x<ceil H>_<material>.gcode`, longest side along X. |
| **Validation** | `cnc_vibes/scripts/gcode_validate.py` (`cnc.py validate`) | Rules: bounds, max_feed (XY 3000), laser_mode ($32=1), laser_dynamic_power, laser_power_range, laser_frame, line_length. |
| **Machine reach** | anolex profile + Alex | Bed origin = machine X21 Y8 (front button: `_reset.gcode`, G54 set there). Y travel 300, so a job from that zero must stay ≤ Y 292. Main has a note: Y limit switch at MPos Y = 294.453, Y capped at 293.5. X travel 400. Soft limits are off and hard limits on. `$131` still says 600: wrong. |
| **Arc fitting / motion model** | `quickcut/arcfit.py`, `quickcut/grbl_model.py` (used by jigsawzall) | Already in quickcut: keep. |

## 3. Emitters to port (entry points)

| Emitter | Lines | Uses quickcut today | Port notes |
|---|---|---|---|
| `quickcut/svg2gcode.py` | 663 | is quickcut | Split into `gcode.py` (conventions), `order.py` (C108), keep `svg2gcode` as the SVG CLI. |
| `jigsawzall/emitter.py` | 1332 | motion helpers, arcfit, grbl_model | Own `_PREAMBLE`, `_header`, `load_material`, `emit_cut_gcode_simple/full`, `emit_etch_gcode`, raster (`emit_raster_gcode`, M4 dynamic), `combined_raster_and_cut`, edge ordering. The bulk of the work. **Its suite has 51 failed + 8 errors before any change** (mostly missing modules): triage them first. |
| `jigsawzall/scripts/etch_calibration.py`, `motion_test_plate.py`, `ring_prototype.py` | | | Small; `ring_prototype.py` is the merge-conflict file. |
| `orpot/emit.py`, `orpot/svg2laser.py` | 494 / 524 | motion helpers, materials | Spiral discs + ribs; tests in `orpot/tests` (11 pass). |
| `cnc_vibes/scripts/cam.py`, `laser_cam.py` | 2086 / 471 | no | cam.py does mill **and** laser (`GcodeOutput.text` frames only `;HEAD: laser`). Leave mill alone. |
| `cnc_calibrate/spiral_cal.py`, `etch_matrix_cal.py` | | no | Calibration grids. |
| `cnc_vibes/lessons/laser/*` (spacer, calibration, spoilboard) | | no | Teaching code with tests that count lines exactly. Port them, or keep as standalone lessons and only route their output through `gcode.write`. |
| `cnc_vibes/lessons/integration/04_interactive_laser_cal` | | no | Streams live to serial (no file); frame deliberately not added. |
| **scads** `marbles/laser_parts.py` + ~15 scripts (`clock/*.py`, `fit_coupon.py`, `line_cut.py`, `kerf_test.py`, `laser_set.py`) | | most of quickcut | Has its own `emit_cut` / `preamble` wrappers and hand area sorts. After porting, `feeder_scad_gcode.py` and `feeder_nest.py` keep only geometry + layout. The scads→cnc_vibes link is a sibling-checkout path today; `scads/marbles/SPLIT_REPO.md` plans a git submodule. |

## 4. Tests and checks (baselines)

- **Suites:** `cd <dir> && python3 -m pytest -q` for `cnc_vibes`, `quickcut`, `orpot`, `jigsawzall`, `cnc_calibrate`.
  - On this branch, 2026-11-06: cnc_vibes 32 failed / 535 passed; quickcut 9 passed; orpot 11 passed;
    jigsawzall 51 failed + 8 errors / 93 passed; cnc_calibrate 51 passed.
  - Record the failing test IDs before you start and diff them after every step: **no new failures**.
- **scads:** regenerate every file, then validate all of them:

  ```sh
  cd scads/marbles
  for c in clock/feeder_scad_gcode.py "clock/feeder_nest.py --v2 --h 291 --gap 0.7" clock/short_parts.py \
           clock/wheel_box_gcode.py "clock/wheel_box_gcode.py --plain" clock/lift_sieve.py clock/lift_parts.py \
           clock/feed_funnel.py clock/ring_jig_compact.py clock/escapement.py fit_coupon.py kerf_test.py; do
    python3 $c >/dev/null || echo FAIL $c; done
  cd ../../cnc_vibes/cnc_vibes
  for f in ../../scads/marbles/gcode/*.gcode; do python3 cnc.py validate $f | tail -1; done
  ```

  On 2026-11-06 every file passes except the superseded 3-layer `ring_jig.py` (F5000 etch; delete it, ROADMAP C106).
- **Golden check:** before porting each emitter, save its current output for fixed inputs. After porting, the
  geometry must match. Ordering and comment text may differ, so compare the cut paths: `scads/marbles/compare_cuts.py
  OLD:NEW` draws new / removed / unchanged cuts.

## 5. Suggested order
1. Branches (§0).
2. `quickcut/gcode.py`: preamble, cut, etch, raster, park, frame (move `gcode_frame.py` in; keep a shim at the repo
   root), bridged paths, materials loader, `write()` with validation. Unit-test each.
3. `quickcut/order.py`: containment tree + middle-out precedence; jigsawzall's orderings; C108 optimiser
   (OR-tools allowed). Test that a hole is never cut after its container.
4. Port scads (`laser_parts.py` becomes a thin shim over quickcut), then orpot, cnc_calibrate, the lessons, cam.py's
   laser path, and jigsawzall last.
5. Make `gcode_validate` (or a test) fail any `.py` in either repo that writes `M3` / `M4` without going through
   quickcut, so drift is caught.
6. Update `CLAUDE.md`: "laser G-code only through `quickcut.gcode.write()`".
