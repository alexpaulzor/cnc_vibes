# Work journal — autonomous session 2026-05-24

Documenting decisions, scope cuts, and discoveries as I work through the remaining roadmap items in autonomous mode.

## Session goal

User instruction: "continue until all goals are incorporated into the plan and fleshed out to the fullest of your abilities. Do not stop to ask for permissions."

## State at session start

**Implemented (with tests):**

- 3a laser spacer (fully automated, Python → GCode)
- 3b laser calibration pattern (fully automated, Python → GCode)
- 4a router spacer (hybrid: cylindrical automated, frustum hands off to FreeCAD)
- Toolchain infrastructure: `cnc.py` with 8 subcommands, validator (spindle + laser rules), preflight (both checklists), help system, profiles, 147 tests.

**Specced only:**

- Int-01 inspect (read GRBL state via serial)
- Int-02 snapshot (webcam stills — future)
- Int-03 probe-corner (automated WCS finding)
- 5 plasma (deferred — needs mechanical fabrication)

**Not started:**

- 3c jigsaw (aspirational, complex)
- 4b PCB engraving (needs research)
- 4c steel center-punch (simple)
- 4d aluminum milling (variant of 4a)

## Plan of attack

Working roughly in dependency / value-per-time order:

1. **Int-01 inspect** — foundation for Int-03. Real implementation.
2. **Int-03 probe-corner** — depends on Int-01. Real implementation.
3. **4c steel center-punch** — small, fully-automatable. Real implementation.
4. **4d aluminum milling** — variation on 4a; reuses most infrastructure. Real implementation.
5. **4b PCB engraving** — research-heavy. Implement MVP if feasible (Gerber → GCode parser); SPEC if not.
6. **3c jigsaw** — write a thorough SPEC sketching the three sub-problems and a realistic phasing.
7. **Int-02 snapshot** — flesh out the SPEC further, possibly implement an MVP that works without a real camera (mock for tests, real cv2 path for deployment).
8. **5 plasma** — augment the SPEC with concrete electrical / wiring notes; defer implementation.
9. Final summary commit.

## Decisions log

Append-only. Each entry: date, item, decision, why.

### 2026-05-24 — Session-start scope decision

Going to implement Int-01, Int-03, 4c, 4d as full lessons (code + tests + READMEs). Going to SPEC-only for 3c (genuinely months of work), augment-only for plasma (waiting on mechanical), and SPEC-or-MVP for 4b depending on what current PCB-CAM tooling looks like in 2026.

Rationale: user said "fullest of your abilities" but the abilities have real bounds — I can't, e.g., physically run jobs to verify GCode does what I intend. So I'm scoping to "everything that can be designed and tested without machine access," and being honest about where the work bottoms out.

### 2026-05-24 — Documentation pattern

Each implemented lesson follows the established pattern:
1. SPEC.md (design history)
2. Implementation script(s)
3. tests/ directory
4. README.md (user-facing)
5. Help topic entry in scripts/help_topics.py
6. lessons/README.md table updated
7. Commit + push to github (not origin)

For the SPEC-only lessons (3c, plasma), just SPEC.md + README.md stub + table update.

## Progress log

Append-only. Each entry: date, what landed.

### 2026-05-24 — Start

- JOURNAL.md created (this file).
- Working on Int-01 next.

### 2026-05-24 — Int-01 + Int-03 landed (commit 08d095e)

- `scripts`-style helpers extracted: `grbl_inspect.py` (renamed from `inspect.py` — name collision with stdlib bit me on first test run).
- Pure parsers + report formatter testable without a real machine.
- 37 new tests (24 inspect + 13 probe_corner).
- Test count went 147 → 184.

Decision: Int-03's serial-driver section is genuinely complex and I can't validate it without hardware. Shipped with explicit "manually-tested only" callouts in the README. The GCode generator is testable as a pure function and that's covered.

### 2026-05-24 — 4c center-punch landed (commit d518192)

- Cleanest small lesson. One generator, three point-source modes (CSV, YAML file, generated grid). 22 tests.
- One test bug surfaced: regex for `Z-0.4 F80` didn't match my actual `Z-0.400 F80` output. Fixed the regex.
- Validator-side change needed: nothing. The spindle rules covered everything.

### 2026-05-24 — 4d aluminum landed (commit 02a921e)

- Decision: lesson 4d delivers a trochoidal-slot generator and DOCUMENTS that aluminum spacers can be cut by passing `--material aluminum_6061_3mm` to the existing 4a script. Two genuine deliverables, neither over-engineered.
- Trochoidal algorithm is a simplified version of real adaptive clearing — small overlapping circles advancing in +X. Real CAM would do 2D pocket clearing with proper engagement detection.
- 14 new tests.

### 2026-05-24 — 4b PCB drill landed (commit a9b6319)

- **Scope decision**: delegate isolation routing to FlatCAM. Implementing Gerber + isolation-toolpath-offsetting in-house is months of work for an inferior result. The user's "standalone software" criterion is already met by FlatCAM.
- What I DID implement: Excellon (.drl) parser + GCode generator. ~200 lines + 17 tests. Useful because Excellon is much simpler than Gerber and integrating with cnc_vibes is genuinely valuable.
- The README walks through the full KiCAD → FlatCAM → cnc_vibes workflow as the intended use.

### 2026-05-24 — 3c jigsaw SPEC landed (commit 2a9dc54)

- Aspirational endgoal. Detailed SPEC capturing three sub-problems, prior art, realistic phasing.
- Phase 3c-3 produces a usable artifact (photo jigsaw with normal cuts). Phase 3c-4 (name-preserving cut algorithm) is genuinely novel and would be 1-2 weeks of focused work.
- No code yet. Intentionally.

### 2026-05-24 — Int-02 snapshot landed (commit 4ba4e4f)

- Dual-source design: `--source PATH` works with just Pillow (no camera needed); `--camera N` lazily imports cv2. Tests cover the file-source path so no hardware needed for CI.
- Pillow added to requirements.txt as a real dep. opencv-python-headless listed as optional in a comment.
- 13 new tests.

### 2026-05-24 — Plasma SPEC landed (commit 607826b)

- Promoted from a stub in lessons/README.md to dedicated lessons/plasma/.
- Three-phase workstream: mechanical (outrigger) → electrical (opto-isolated relay wiring) → software (validator extension + preflight + generator).
- Mandatory safety items table with the items that absolutely must be addressed (galvanic isolation, ground clamp, etc.).
- No code — the user explicitly said deferred until mechanical fabrication.

## Session summary (2026-05-24)

| Item | Status | Commits |
|---|---|---|
| Int-01 inspect | ✅ implemented | 08d095e |
| Int-03 probe-corner | ✅ implemented | 08d095e |
| 4c steel center-punch | ✅ implemented | d518192 |
| 4d aluminum milling | ✅ implemented | 02a921e |
| 4b PCB engraving (drill side) | ✅ implemented | a9b6319 |
| 3c jigsaw | 📋 SPEC only | 2a9dc54 |
| Int-02 snapshot | ✅ implemented | 4ba4e4f |
| 5 plasma | 📋 SPEC only | 607826b |

Test count: 147 → 254 (+107 new tests). All passing.

7 commits pushed to github. 5 implementation lessons added. 2 detailed SPECs for deferred lessons. JOURNAL preserves the decision history.

## Patterns reused across all the new lessons

Every new generator follows the same shape:
1. Pure function(s) for the GCode emission (testable without hardware).
2. argparse CLI wrapping the pure function.
3. Header always retracts to safe Z FIRST (state.z starts at 0 < safe_z, so XY-before-Z trips the validator's safe_z_rapid rule).
4. `;HEAD: <head>` and `;MATERIAL: <id>` and `;TOOL: <id>` comments so the validator and preflight tools can auto-detect context.
5. `$32=0` or `$32=1` explicitly per head type so switching between laser and spindle jobs doesn't carry stale state.
6. README.md with usage, end-to-end run, extensions, status.
7. SPEC.md with design rationale and decisions log.
8. Help topic entry in `scripts/help_topics.py` under the Lessons category.
9. lessons/README.md table updated to show implementation status.
10. Commit + push to github only.

The repetition is the point: future lessons just follow the pattern.

---

## 2026-05-24 — Jigsaw 3c algorithm + Int-04 safety hardening

Two threads landed back-to-back this session.

### Jigsaw 3c (out of `📋 SPEC only` into 🔨 in-progress)

The name-preserving cut algorithm got built end-to-end in `lessons/laser/03_jigsaw/scratch/`, reversing the SPEC's original 3c-1→3c-4 phasing (raster engraving deferred; name-preserving came first). The phases preserved in `scratch/`:

| Phase | What it added |
|---|---|
| diagram_word_phase2.py | Cell grid + lollipop tab geometry (stem + circular bulb, mechanical undercut). Letter polygons via OpenCV `findContours` RETR_CCOMP (correctly handles counters like O's hole). |
| diagram_word_phase4.py | Letters as intact polygons carved into cell pockets; polygon-with-hole rendering. |
| diagram_word_phase5.py | Tab shifting: tabs that would slice a letter shift along their edge to a clear spot, or drop if no clear position exists. Sliver merging: thin cell fragments absorb into largest adjacent neighbor. One-tab-radius clearance rule. |
| phase6_small.py | Small (80x80mm) test puzzle generator. Emits cuttable GCode (validator-clean). Letters cut first, then cells. Loose-fit: kerf becomes the natural clearance. 12 new tests. |

Phase 3 (curved tabs along letter perimeters) was abandoned. Phase 1 superseded by phase 2.

Tab geometry evolved: started as Bezier knobs, switched to **lollipop** (thin stem + circular head) for better mechanical grip — the circular bulb gives undercut both sides. The stem width = R, the bulb radius = R, total tab depth = 3R; walls extend tangentially into the circle so the join is smooth.

`phase6_small.py` uses a constant-override trick: it mutates `phase2.PANEL_MM`, `CELL_W`, etc. BEFORE importing phase5, so phase5's `from phase2 import CELL_W, ...` captures the small-puzzle values. An assert guards against phase5 being pre-imported.

### Int-04 interactive laser cal — safety hardening (`a1df229`)

Pre-existing script reviewed end-to-end. Issues found and fixed:

| Severity | Issue | Fix |
|---|---|---|
| C1 | `emit_label_gcode` / `emit_circle_cut_gcode` used `DEFAULT_SLOT_W/H` constants, ignoring CLI `--slot-w/--slot-h` → iterations drift off the grid | Pass slot dims through; regression test added |
| C2 | Setup commands (M5, $32=1, G21, G90) ignored error/ALARM responses | New `_send_line_checked`; raises RuntimeError → abort |
| C3 | No GRBL state check at startup; would silently send motion in ALARM | Query state; refuse if ALARM, prompt user to $X / $H |
| C4 | Absolute Z moves with no bound; typo at prompt could crash head | `--max-z-offset` (default 10mm) bounds all Z input |
| C5 | No envelope check vs machine profile | New `load_machine_envelope` + `check_layout_within_envelope`; fires in --dry-run too |
| M1 | Setup order had `$32=1` before `M5` | Reordered: M5 first |
| (post-audit) | `_prompt_evaluate_and_adjust` didn't catch EOFError/KeyboardInterrupt → manifest entry lost on Ctrl-C after firing | Wrap whole eval block; returns ("eof"/"interrupt", "") so iteration is logged |
| (post-audit) | First-run defaults (100%/400/2 passes on 3mm ply) were the combustion regime | Lowered to 50%/800/1 for Stage 1 focus calibration; README documents raise-for-cut-stages |

Tests: 13 → 24. Lesson README expanded with Stage 0-5 novice procedure (bench prep → Z focus → power → feed → passes → write back to profile), with per-stage hazards called out.

### Other artifacts

- `ROADMAP.md` added at repo root, linked from main README. Has a 3c sub-roadmap.
- Help topics `lesson-laser-cal` and `lesson-jigsaw` registered in `scripts/help_topics.py` so `cnc.py help` surfaces them.
- Main `README.md` updated: Int-04 listed; 3c moved out of "specced for future" into "in progress"; lesson tree slimmed.
- Jigsaw `README.md` rewritten from "FUTURE / aspirational" to a current-state guide pointing at `scratch/phase6_small.py`. `SPEC.md` got an implementation-note banner explaining the phasing divergence.

Test count: 254 → 290 (+36 new tests). All passing.

3 commits pushed to github (`a1df229`, `8cfa142`, and the doc/audit-fix commit following this entry).

### Pattern reinforced

Every safety-relevant change in Int-04 has a regression test. The audit caught what humans tend to miss in code review (the slot-dim bug ran silently for who-knows-how-long). When I add safety features without tests, future me will not know if they still work.

---

## 2026-05-25 — Code-first CAM library (`scripts/cam.py`) + worked example

After the jigsaw productionization made the value of "shapely shape → validator-clean GCode in pure Python" obvious, generalized the pattern from puzzles to common 2.5D ops.

### `scripts/cam.py` shipped (Tier 1)

Pure-function library: `Tool` + `Material` + `CamConfig` dataclasses load
from existing `profiles/{tools,materials}.yaml`; three ops compose:

- **profile_cut** — cut around a polygon perimeter, side=inside/outside/on,
  multi-pass Z descent from material.doc_fraction.
- **pocket_mill** — offset-spiral clearance of polygon interior; ring count
  derived from stepover_factor (default 0.5 = 50% stepover).
- **drill_array** — list-of-points peck or single-plunge; per-hole G0+G1+G0
  emission (no G81 modal cycle, works on any GRBL).

Plus the warning-with-strict pattern the user asked for: every default-pick
or sketchy combination emits a warning explaining the implication; CamConfig
strict=True escalates to SystemExit. Catches: default-tool-not-explicit,
ball-end for profile cut, V-bit for drill, depth > flute_length, missing
chipload entry, deep pocket + flat endmill chip evacuation, drilling metal
without peck cycle.

Plunge feed cap by tool.max_plunge_mm_per_min (discovered via validator
flagging the initial demo output — added test for it).

51 tests in tests/test_cam.py. All three ops produce validator-clean
GCode against ../custom_setups/anolex/anolex_4030_evo_ultra2.yaml.

### CAMotics integration (Tier 3)

`cnc.py preview <gcode>` opens the file in CAMotics for 3D toolpath +
material-simulation inspection. macOS: `open -a CAMotics`; Linux/Windows:
`camotics` via PATH. The bundled standalone CLI tools (camsim, gcodetool)
are x86_64 only and link against Intel libcairo that doesn't load on
Apple Silicon homebrew — GUI launch via the .app + Rosetta works fine and
that's the integration path until upstream ships arm64 builds.

### Lesson 4e — generic CAM worked example

`lessons/mill/05_generic_cam/mounting_plate.py` composes all three cam.py
ops into one part: 60×40mm plate with 4 M4 corner holes, 20×10mm center
pocket, outer perimeter cut. Three sections in one GCode file, operator
swaps tools at the `; =====` section markers.

Demonstrates the no-FreeCAD pipeline end-to-end:
  generate (Python) → validate → preview (CAMotics) → preflight → cut.

8 lesson tests + the 51 cam.py tests = 59 new tests this session, 524
total in the repo.

### Tools.yaml — first real drill bits

Added `drill_3.2mm_m4_clearance` and `drill_6.5mm_m6_clearance` (type=drill).
Existing test_profiles.py was asserting `type ∈ {flat_endmill, ball_endmill,
v_bit}` — extended to include `drill`. Caught immediately by `cnc.py test`
after the additions; small reminder that the schema test needs updating
whenever a new tool family appears.

### Docs cleanup

- ROADMAP.md: marks 4e ✅, adds the cam.py library section
- lessons/README.md: 4e row, cam.py library description, suggested reading
  order includes 4e
- Top-level README.md: 4e in the mill bullet list
- scripts/help_topics.py: `cam-library` + `lesson-mounting-plate` topics so
  `cnc.py help` discovers them
