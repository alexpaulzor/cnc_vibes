# cnc_calibrate — build notes, decisions, open questions

Running scratch for the autonomous (`/makeitso`) build. Decisions I made without
you (all reversible) + questions batched for your return.

## Session goal
1. Upgrade the spiral laser calibration: **elliptical** rings, **pass-count**
   sectors (ping-pong zigzag), **engraved** digit labels + KEY png, to
   characterize a ~10W diode on 3mm MDF (speed × passes on one plate).
2. Forklift the good calibration/machine tools out of `cnc_vibes/` into this
   clean dir. Minimal set now (spiral_cal + get_ip/find-machine); the rest is
   inventoried below for you to pick later.

## Decisions made (reversible)
- **Forklift = move (git rm from cnc_vibes) but MINIMAL**: only the flagship
  spiral_cal (rebuilt/upgraded here as canonical) and the get_ip stack
  (find_cnc.py, cnc_state.py) move now. Rest of cnc_vibes left intact to pick
  from later. `cnc_vibes/cnc.py` gets tombstones for cal-laser/ip/find-machine
  pointing here.
- **Recommended MDF test params**: passes 1,2,3; feeds 100–1000 mm/min; 10 rings
  (steps of 100); time_s≈11.3; power 100%; ellipse aspect 1.35. Brackets your
  known point (500×1 fail, 500×2 ok). Community 10W/3mm-MDF data clusters
  ~150–600 single-pass-marginal → 2–3 passes; this range brackets it.
- **Sector pass pattern** (per ring, N=3): base loop (pass 1 everywhere) then a
  continuous ping-pong of sweeps N-1,N-2,…,1 sectors → per-sector counts a
  distinct permutation of 1..N. N=3 → [2,3,1]. No laser-on repositioning.
- **Radials innermost→outermost only** (center disc left whole for the engraved
  legend), cut last at slowest feed. Cut lines crossing leader lines is fine.
- **Engraving**: digits only on the part (via vendored font_7seg): feed number
  per ring where the arc fits; pass digit per sector marked in the center legend.
  Full wording lives on the KEY png.
- **Ellipse model**: rings are scaled ellipses, semi-minor b_i linearly spaced,
  semi-major a_i = aspect·b_i. feed_i = perimeter_i/(T/60) (Ramanujan perimeter).

## Open questions for you (also surfaced as a CLI menu at the end)
- Q-FORKLIFT: From the inventory below, which additional cnc_vibes features do you
  want moved into cnc_calibrate/? My recommended keepers: gcode_validate,
  job_params (+preflight), find/ip (done), interactive_laser_cal, probe_corner,
  grbl_inspect, jog, spoilboard. Leave: laser/02 grid (superseded), laser/01
  spacer, all of mill/*, cam.py/cam_cli (that's a CAM library, not calibration).
- Q-ASPECT: ellipse aspect 1.35 ok, or more dramatic (e.g. 1.6) for easier
  re-orientation?
- Q-ENGRAVE-POWER: engrave labels at what power/feed? Default guess: 15% power,
  3000 mm/min (surface mark, not cut). You know your diode's marking sweet spot.
- Q-CUTVSMARK-ORDER: engrave labels first (before cuts) so a jumbled piece keeps
  its mark even if it falls? Default: engrave center legend + ring feeds FIRST,
  then rings, then radials last.

## Inventory of cnc_vibes calibration/testing/utility features (for later picks)
### Production (scripts/)
- spiral_cal — flagship laser feed/warmup cal (BEING UPGRADED + MOVED here).
- gcode_validate — state-machine gcode validator (spindle+laser rules). KEEP.
- job_params (+ cnc.py params/preflight) — feeds/speeds + safety checklist. KEEP.
- find_cnc + cnc_state + cnc.py ip/find-machine — network discovery. MOVING now.
- cam.py / laser_cam.py / cam_cli — 2.5D CAM library (profile/pocket/drill/
  engrave/…). Not "calibration"; big. Probably LEAVE (or its own forklift later).
- openscad_loader — scad/svg → shapely for cam. Support lib. Leave unless cam moves.
- help_topics — manpage browser. Leave (tied to cnc_vibes).
- post (FreeCAD) — STUB, not implemented. Skip.
- preview (CAMotics) — external app launcher. Marginal. Skip.
### Lessons — laser/integration
- integration/04 interactive_laser_cal — iterative cut/engrave dial-in (focus,
  power, feed, passes); complements spiral_cal. KEEP (needs font_7seg + parse_status).
- integration/03 probe_corner — touch-plate WCS probing. KEEP.
- integration/01 grbl_inspect — machine-state query; parse_status shared dep. KEEP.
- integration/05 jog — xbox/keyboard jogger + inline Z-probe. KEEP (hardware-specific).
- integration/02 snapshot — webcam before/after still. Useful adjunct. Maybe.
- laser/04 spoilboard — M6 hole-grid spoilboard tiles for Anolex 4030. KEEP (utility).
- laser/02 calibration — old square power×pass×speed GRID. SUPERSEDED by spiral. LEAVE
  (but its font_7seg is vendored here).
- laser/01 spacer — smallest example. LEAVE.
- mill/* — spindle part exercises (mounting_plate, pcb excellon, center_punch,
  trochoidal_slot, mill_spacer). Non-calibration. LEAVE (unless you want a
  cnc_mill dir later).
### Abandoned / stub / relocated (do not forklift)
- FreeCAD post (stub), V-carve/medial-axis engrave (roadmap only), plasma head
  (hardware-blocked, files removed), jigsaw (moved to ~/src/vibes/jigsawzall),
  curved-tab jigsaw phases (abandoned).
