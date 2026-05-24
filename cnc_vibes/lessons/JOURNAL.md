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
