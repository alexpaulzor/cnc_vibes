# Lesson 4b — PCB engraving SPEC

> Implemented 2026-05-24. Excellon converter done; isolation routing delegated to FlatCAM.

## Goal

Cover the "drilling" half of the no-chemical PCB workflow with an in-repo Python tool. Delegate the "isolation routing" half to FlatCAM (an existing mature tool that does it well).

## Scope decision: delegate isolation routing

I considered implementing Gerber-to-GCode isolation routing in-house, but:

- The Gerber spec is large (RS-274X, with many aperture types and macros).
- Toolpath offsetting (the actual "isolation" math) is non-trivial geometry.
- FlatCAM (Python, GPL, ~10 years old, actively maintained) already does this well.
- pcb2gcode (C++, CLI) is an even simpler standalone alternative.

Reinventing this would be months of work and the result would be inferior to existing tools. The user's "standalone software that does not depend on Claude" criterion is already satisfied by FlatCAM.

What IS in scope and worth building: the **Excellon drill file → GCode converter**. Excellon is much simpler than Gerber; the parsing is ~80 lines, and integrating with the cnc_vibes pipeline (validator, preflight) gives the user a complete drill-side experience.

## Excellon format (the subset we handle)

KiCAD's Excellon output looks like:

```
M48                  ; header start
METRIC              ; or INCH
T1C0.800            ; tool 1 = 0.8mm drill
T2C1.000
%                   ; header end / body start
T1                  ; select tool 1
X1.500Y2.500        ; hole at (1.5, 2.5)
X3.500Y2.500
T2                  ; select tool 2
X10.000Y10.000
M30                 ; end
```

The parser handles:

- METRIC / INCH headers (inch values are multiplied by 25.4 to give mm internally)
- T<n>C<dia> tool definitions (in header section)
- T<n> tool selects (in body)
- X<n>Y<n> coordinate lines
- M30 / M00 footers
- Blank lines and `;` comments

Not handled (deferred):

- Coordinate format specifications (`FMAT,2;`) — KiCAD typically emits decimal-aligned coordinates that just work.
- Repeat codes (`R5X1Y1`) — KiCAD doesn't emit these.
- Slot drilling (`G85`) — slots become straight drill operations in our output, which is wrong but rare in hobby PCBs.
- Routing commands (G00/G01 between holes) — Excellon can include these for board outline cuts; we ignore them.

## GCode strategy

For each tool diameter (ordered smallest-to-largest so you progress to bigger bits):

- M5 (spindle off)
- M0 (pause — operator swaps bit and re-probes Z) — **skipped for first tool**
- M3 S<rpm> (spindle on)
- For each hole at (x, y):
  - G0 X<x> Y<y> at safe Z
  - G0 to approach Z
  - Peck drill: G1 Z<-peck>, G0 retract, G1 Z<deeper>, retract, ... until at final_z
  - G0 to safe Z

Final Z is `-(copper_thickness + 0.3 mm)` to ensure clean breakthrough into the sacrificial backer.

## Why peck drilling

FR4 drill cuttings are abrasive and pack tightly in the flutes if not cleared. Peck (plunge — retract — deeper plunge — retract) lets each plunge stay shallow so chips can escape. Drill bits in FR4 are tungsten carbide and SHATTER if loaded sideways; chip-induced sideways load is the most common failure mode.

## Decisions made during implementation

- **Tools sorted by ascending diameter** in the output. Smallest first means least-impactful tool changes if a bit breaks early.
- **M0 between tool changes** rather than M6 (GRBL doesn't support automatic tool change). The operator manually swaps the bit, re-probes Z (via Int-03 or sender), and presses CYCLE START in gSender.
- **No auto-leveling.** Surface-flatness compensation is FlatCAM's domain; for drilling alone, the depth tolerance is large enough (~0.5 mm slop) that auto-leveling doesn't matter.
- **Per-tool `;TOOL:` markers** named `drill_<dia>mm` so the validator could in principle look them up — but they're not in `profiles/tools.yaml` since drill bits are short-lived consumables; the user doesn't manage them as named tools.

## Reuses existing infrastructure

- `profiles/anolex_4030_evo_ultra2.yaml` for envelope bounds.
- `scripts/job_params.py` `load_yaml` helper.
- `scripts/gcode_validate.py` bounds rule (catches holes outside the bed).
- Standard spindle PREFLIGHT_CHECKLIST applies (mostly — PCB-specific items are an extension idea).

No new files in `profiles/` or `scripts/`.

## Extensions

- Gerber metadata inspector (`gerber_inspect.py`) — pure parser for layer metadata, no toolpath generation.
- Merge multiple Excellon files (PTH + NPTH) into one.
- PCB-specific preflight checklist (board flat, copper-up, hold-downs not on copper, etc.).
- Slot drill support — recognize G85 / X1Y1G85X2Y2 patterns and emit pocket-style GCode.
