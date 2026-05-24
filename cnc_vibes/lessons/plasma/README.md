# Plasma cutting (future)

> **No implementation yet — requires mechanical fabrication and electrical work before software matters.**
>
> Full design notes: [SPEC.md](SPEC.md).

## What this will be

Adding the ArcFony Cut53M Pro plasma cutter as a third tool head on the Anolex 4030. The plasma torch needs to be mounted on an outrigger (so it cuts off-bed, not into the Y ball screw), wired to the GRBL controller through opto-isolated relays (plasma EMI will fry unisolated logic), and integrated into the cnc_vibes pipeline with a plasma-specific validator rule set and preflight checklist.

## Workstream phases

| Phase | Deliverable | Prerequisite |
|---|---|---|
| 5-mech | Outrigger torch mount + sacrificial cut surface | T-slot extrusion + bracket + water table or steel grid |
| 5-elec | GRBL ↔ ArcFony wiring with opto-isolation | Opto-isolated relay module, ferrites, shielded cable |
| 5-sw | Plasma GCode generator + validator + preflight | Phase 5-elec passes a bench test |

Each phase only matters after the previous one works. The software is the easy part.

## Why this is harder than laser

Beyond just "another tool head":

- **Pierce delay**: arc takes 0.5-2 s to establish before motion is safe.
- **EMI**: plasma will crash USB serial unless every wire is treated like an antenna.
- **Torch height control**: cut quality depends on Z being right, and workpieces warp.
- **Safety**: UV, molten metal, fumes. Welding helmet, ventilation, fire watch.

The SPEC has the gory details.

## Reuses existing toolchain

- `scripts/gcode_validate.py` `detect_head` pattern (already supports `;HEAD: laser`; add `plasma`).
- `scripts/job_params.py` checklist pattern (PREFLIGHT_CHECKLIST, LASER_PREFLIGHT_CHECKLIST → PLASMA_PREFLIGHT_CHECKLIST).
- `cnc.py preflight` head auto-detection.

## When to start

After you've run real spindle and laser jobs, after you have an evening for mechanical work, after you're comfortable with the wiring (or have a helper). No rush — this is a months-long project that lives in the garage.
