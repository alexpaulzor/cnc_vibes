# Lesson 4c — Steel center-punch SPEC

> Implemented 2026-05-24. This SPEC was the design plan; the lesson followed it closely.

## Goal

Make precisely-located center-punch divets in mild steel (or softer metals) using the engraver V-bit. No "cutting" — just superficial surface deformation to register a follow-up drill bit.

## Why this works on a 500W spindle

You're not removing material; you're plastically deforming the top ~0.4 mm of the surface with the point of a hardened V-bit at low feed. The force is high but localized and brief. The 500W spindle does no real work — it just spins the tool while gravity (and the controlled Z plunge) provides the force.

If anyone tries to use this script to actually cut metal, they'll break the bit.

## CLI design

Three point sources, mutually exclusive:

| Source | Use case |
|---|---|
| `--points "x,y,x,y,..."` | A few divets you're typing by hand. |
| `--points-file path.yaml` | A list of holes from CAD or hand-curated. |
| `--grid AxB --pitch P --origin X,Y` | Perforated-panel layout. |

Other params:

- `--depth` (default 0.4 mm, capped at 2.0)
- `--plunge-feed` (default 80 mm/min — slow for steel)
- `--tool` (default `vbit_60deg_6mm`)
- `--spindle-rpm` (default 12000 — low end of the tool's range)
- `--out` (derived)

## GCode structure

```
header (units, absolute, $32=0, safe Z retract, M3 spindle on, ;TOOL: marker)
for each point:
  G0 X<x> Y<y>           ; rapid to XY at safe Z
  G0 Z<approach_z>       ; rapid down to just above stock
  G1 Z<-depth> F<feed>   ; controlled plunge
  G4 P<dwell>            ; brief dwell at bottom
  G0 Z<safe_z>           ; retract for next move
footer (M5, park to 0,0)
```

Spindle stays on across all points (more efficient than M3/M5 per point); single M5 at the end.

## Validation gates (in the script itself)

- `depth > 0` and `depth <= 2.0` (refuses excessive depths)
- `spindle_rpm <= tool.max_rpm`
- `plunge_feed <= tool.max_plunge_mm_per_min`
- Every point in the machine envelope (`0 <= x <= envelope.x`, same for Y)
- At least one point provided

These run before any GCode is written; bad inputs fail fast.

## Reuses existing infrastructure

- `profiles/anolex_4030_evo_ultra2.yaml` (envelope)
- `profiles/tools.yaml` (tool limits)
- `scripts/job_params.py` (`find_by_id`, `load_yaml`)
- `scripts/gcode_validate.py` (bounds, max_feed, max_plunge — the `;TOOL:` marker enables max_plunge enforcement)
- `scripts/job_params.py` PREFLIGHT_CHECKLIST (the standard spindle checklist applies)

No new files in `profiles/` or `scripts/`. No new tests in `tests/`.

## Decisions made during implementation

- **No steel material profile.** Center-punching doesn't involve removing material, so chipload tables don't apply. The validator doesn't need it.
- **Safe-Z retract first in header.** Same lesson learned from 4a: state.z starts at 0, so the first G0 X/Y motion needs Z to be >= safe_z first.
- **Dwell of 0.1 s at the bottom** of each plunge to let the mark stabilize. Configurable in source if you want different.
- **Excessive depth refused.** A center-punch divet is sub-millimeter. Anyone passing `--depth 5` is misusing the script and might break the bit; refuse to generate.
