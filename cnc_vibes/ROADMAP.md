# Roadmap

What's done, what's in flight, what's next. Maintained alongside the lessons. The full table of contents with descriptions lives in [lessons/README.md](lessons/README.md); this file is the at-a-glance status view.

## Status legend
- ✅ implemented and tested
- 🔨 in progress
- 📋 spec only / aspirational
- ⛔ blocked (typically on hardware)

## Laser

| # | Lesson | Status |
|---|---|---|
| 3a | [Parametric laser-cut PCB spacer](lessons/laser/01_spacer/) | ✅ |
| 3b | [Laser calibration pattern (power × passes × speed)](lessons/laser/02_calibration/) | ✅ |
| 3c | Photo-engraved wooden jigsaw with name-preserving cuts — **moved to `~/src/vibes/jigsawzall`** | ➡️ Now its own repo (letter-aligned grid, dither raster, standalone CLI). |
| 3d | [Laser-cut spoilboard with M6 hole grid](lessons/laser/04_spoilboard/) | ✅ Parametric grid + auto-tiling for stock larger than design. Default matches Anolex 4030 bed (400×500mm, 9×10 holes @ 45mm). |
| — | **Concentric spiral laser calibration** — `cnc.py cal-laser` (backed by `scripts/spiral_cal.py`) | ✅ Recommended laser calibration. One small disc of concentric rings cut inner→outer at feeds = circumference/loop-time; STOP when a ring stops falling free = your max clean single-pass feed. Each ring has a spiral warmup lead-in that records the cold-start ramp. Center origin; outputs gcode + toolpath PNG + a self-explanatory key PNG. Supersedes the old square test card + hex-spiral cal. |

## Mill

| # | Lesson | Status |
|---|---|---|
| 4a | [Parametric router-cut spacer](lessons/mill/01_spacer/) | ✅ |
| 4b | [PCB engraving (Excellon drill side)](lessons/mill/04_pcb/) | ✅ |
| 4c | [Steel center-punch divets](lessons/mill/02_steel_center_punch/) | ✅ |
| 4d | [Aluminum trochoidal slot](lessons/mill/03_aluminum/) | ✅ |
| 4e | [Generic 2.5D CAM — no FreeCAD](lessons/mill/05_generic_cam/) | ✅ Composes `profile_cut` + `pocket_mill` + `drill_array` from `scripts/cam.py` into one mounting-plate part. Demonstrates the code-first CAM workflow end-to-end. |

## Integration (machine state + camera)

| # | Tool | Status |
|---|---|---|
| Int-01 | [`inspect`](lessons/integration/01_inspect/) — read GRBL state via serial | ✅ |
| Int-02 | [`snapshot`](lessons/integration/02_snapshot/) — webcam stills for setup verification | ✅ |
| Int-03 | [`probe-corner`](lessons/integration/03_probe_corner/) — automated WCS-finding via touch plate | ✅ |
| Int-04 | [interactive laser cal](lessons/integration/04_interactive_laser_cal/) — iterative Z/power/feed/passes tuning | ✅ (cut mode + engrave mode for grayscale calibration) |

## Plasma

| # | Lesson | Status |
|---|---|---|
| 5 | ArcFony Cut53M Pro as third tool head | ⛔ blocked — requires mechanical fabrication (outrigger mount, opto-isolator). Machine noted for context; lesson removed. |

## Active library — `scripts/cam.py` (parametric 2.5D CAM, no FreeCAD)

A code-first CAM library that produces validator-clean GCode for the common 2.5D ops from shapely shapes. Pure-function ops, op-tool warning framework, `strict=True` mode escalates warnings to errors.

- ✅ `profile_cut` — inside/outside/on cut around a polygon's perimeter (multi-pass Z)
- ✅ `pocket_mill` — offset-spiral clearance of a polygon interior (multi-pass Z, configurable stepover)
- ✅ `drill_array` — peck or single-plunge drill cycle at each (x, y)
- ✅ `engrave_text` — constant-depth outline trace of glyph contours (PIL+cv2 rasterize → findContours → per-contour G-code). NOT V-carve; variable-depth medial-axis variant still on the roadmap.
- ✅ `text_profile` (spindle) + `laser_cam.text_profile` — cut each glyph's silhouette OUT of stock, with interior counters (O, A, P, etc.) preserved as holes. Reuses `text_to_polygons` (cv2 RETR_CCOMP hierarchy → shapely Polygon with holes) for both heads.
- ✅ `chamfer_edge` — V-bit single-pass chamfer along a polygon's outer perimeter; computes chamfer width from tool angle + depth and warns when it would cross into an adjacent interior hole's wall
- ✅ `profile_cut_with_tabs` — `profile_cut` variant that leaves N small bridges holding the part to stock on the final pass; tab boundary arclens are interpolated into the toolpath even on straight runs so Z lifts emit at the right XY
- ✅ `slot_mill` — stadium-shape pocket (rectangle with semicircular ends) from p1 → p2 at the given width; dispatches to `pocket_mill` for the actual clearance
- ✅ `face_mill` — zig-zag raster surfacing of a rectangular bounds polygon at a uniform Z (parallel scanlines for predictable chip evac, unlike `pocket_mill`'s spiral)
- ✅ Worked example: `lessons/mill/05_generic_cam/` composes profile/pocket/drill into a mounting plate
- ✅ `scripts/openscad_loader.py` — load 2D OpenSCAD designs into shapely polygons via `--export-format svg` → svgelements → shapely. Closes the loop between OpenSCAD authoring and the cam.py CAM library.
- ✅ `cnc.py preview <gcode>` opens CAMotics for 3D toolpath simulation
- ✅ `cnc.py cam <op>` — thin CLI + interactive (prompt_toolkit) shim wrapping every cam.py op. 5 shape primitives, 4 hole patterns, spindle + laser heads, auto-validates emitted GCode. Run `cnc.py cam` with no args for the wizard, or `cnc.py help cam-cli` for the flag catalog.
- ✅ `scripts/laser_cam.py` — laser-mode counterparts (`laser_profile`, `laser_engrave`) for the cam-cli shim; reuses `cam._text_to_contours` for glyph rasterization.
- 📋 V-carve (medial-axis variable-depth) for `engrave_text` — bigger algorithmic problem; constant-depth handles most cases

## Pick by task (not in order)

These recipes don't build into a curriculum — grab the one that matches the
job in front of you. The [Where to start](../README.md#where-to-start-pick-by-task)
table in the README is the primary situation → start-here map; the tables above
are the full capability matrix. A rough sense of dependencies, if you want it:
the machine-talking tools (Int-03, Int-04) reuse the serial pattern from Int-01,
and the harder metal work (4d aluminum) assumes you're comfortable with the
parametric spindle path from 4a. Everything else stands alone.

(jigsaw moved to its own repo, `~/src/vibes/jigsawzall`)

## Jigsaw (moved out)

The photo-engraved jigsaw lesson graduated to its own repo: `~/src/vibes/jigsawzall` (letter-aligned grid, dither photo raster, standalone CLI). Its detailed roadmap now lives there.


## Next session candidates

Software-side, all unblocked (the bed is on the way but no software work depends on it):

- **`scripts/cam.py` V-carve** (medial-axis variable-depth `engrave_text`) — bigger algorithmic problem; constant-depth handles most cases today.
- (Jigsaw + photo-raster work now lives in `~/src/vibes/jigsawzall`.)

Hardware-side (waiting on bed arrival):
- First-corner Z-focus measurement after the bed is installed.
- Run Int-04 cal on a real piece of stock to dial in cutting params.
