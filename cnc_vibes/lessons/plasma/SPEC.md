# Lesson 5 — Plasma cutting (future, requires mechanical fabrication)

> **Status: SPEC only — captures the workstream design so it survives. No code yet, and no code is appropriate until the mechanical outrigger and electrical interface are built.**

## Hardware involved

- **Plasma source**: ArcFony Cut53M Pro plasma cutter. Has CNC control ports on the rear (the user mentioned this); typical ports include torch-on/off (a dry relay contact) and arc-OK feedback (a dry contact that closes when the arc is established).
- **GRBL controller**: the same Anolex 4030-Evo Ultra 2 controller used for spindle and laser. The hardware switch on the rear that toggles between spindle and laser will need extending or replacing for plasma — the plasma torch is a third tool head sharing the carriage.

## Why this is hard

Plasma cutting is fundamentally different from spindle / laser / FDM in several ways:

- **Pierce delay**: when the torch fires, it takes 0.5-2 seconds to establish a stable arc that can cut. Motion must wait until the arc-OK signal returns. GRBL has no native handshaking for this; it's a manual workaround (M0 pause, operator waits, presses CYCLE START — or custom firmware mod).
- **EMI hostility**: plasma generates massive electromagnetic interference. USB cables to the GRBL controller can drop comms or corrupt commands. Cabling needs ferrites, shielding, and physical separation from the plasma power leads. Galvanic isolation between the GRBL controller's I/O and the plasma cutter's control board is mandatory (opto-isolated relay).
- **Torch height control (THC)**: plasma cut quality depends on a consistent torch-to-workpiece distance. Workpieces warp under heat. Real plasma tables use THC sensors that adjust Z dynamically based on arc voltage. Without THC, you cut at fixed Z and accept inconsistent quality (still useful for prototyping).
- **Cut zone hazards**: UV from the arc (welding helmet required), molten metal spatter, fumes (chromium, manganese, zinc — depending on material). A dedicated cutting area with ventilation is non-negotiable.
- **Speed**: plasma cuts very fast (1000-3000 mm/min for 3 mm steel) — the bottleneck is the machine's acceleration, not cut quality.

## Workstream phases

Each phase has real prerequisites; don't start a phase until the previous one is done.

### Phase 5-mech: outrigger torch mount

- Design and fabricate a bracket that holds the plasma torch off the side of the gantry, beyond the bed edge. Torch points down at a sacrificial cutting surface (water table, slat grid, scrap steel) **off-machine**, NOT on the Y-axis ball screw.
- T-slot extrusion (the user has 20×20 mm) is the obvious build material. Torch clamp + adjustable Z for height tuning.
- The cutting surface might be a small water table (cardboard box lined with HDPE, water ~25 mm deep) or a 50×50 mm hole-grid steel plate.

**Deliverable**: torch mounted, swings out for use, retracts when not in use. Sacrificial cut surface positioned reliably.

### Phase 5-elec: GRBL ↔ ArcFony interface

- Identify which GRBL pin will drive the torch on/off relay. The cleanest choice: re-use the spindle PWM pin (since plasma is a third tool sharing the same carriage; only one head fires at a time). The hardware switch on the rear needs to grow a third position (or be replaced with a three-way selector).
- The relay must be **opto-isolated** to keep plasma's HF start noise out of the GRBL controller. Suitable parts: G3MB-202P (Omron), or any 5V-input opto-isolated relay module.
- Wire the relay across the ArcFony's "Torch ON" port (refer to ArcFony manual for the exact pinout; likely a 2-pin dry contact input).
- For arc-OK feedback: read the ArcFony's "Arc Established" output through another opto-isolator into an unused GRBL input pin. GRBL doesn't natively read arbitrary inputs into the GCode stream, but a custom firmware mod (or a simple Python-side handshake before sending the next G1) can listen.
- **Bench-test with the torch disconnected** before connecting anything to actual plasma current. Toggle the relay manually via `M3 S1` / `M5` in gSender; verify the LED on the relay module lights.

**Deliverable**: with the torch physically disconnected, `M3` toggles the relay; with arc-OK simulated via a button, the Python wrapper detects it.

### Phase 5-sw: software — plasma GCode generation

A new `lessons/plasma/01_sheet_cutout/` or similar:

- A parametric sheet-metal part generator (perhaps a simple bracket: rectangle with two mounting holes).
- GCode generation includes:
  - **`;HEAD: plasma`** comment so the validator knows this is a plasma job (parallel to the laser-mode validator extension).
  - `M3` to fire torch.
  - **Manual pierce delay**: `G4 P1.0` (dwell 1 second) after `M3` before any motion. Or replace with a `;WAIT_ARC_OK` comment that a custom sender handler watches for.
  - High feed rates (1000-3000 mm/min for thin steel, derived from a plasma-materials profile we'll add).
  - `M5` to cut torch off at end of cut.
- Validator extension: plasma rules (`require M3 not M4`, `require pierce dwell after M3`, `;HEAD: plasma` triggers them).
- Preflight extension: plasma-specific checklist (torch outrigger deployed, sacrificial surface positioned, plasma air supply on, ground clamp attached to workpiece, welding helmet on, fire watch, ventilation).

**Deliverable**: `python lessons/plasma/01_sheet_cutout/bracket.py --width 40 --height 80 --hole-pitch 30 --thickness 3` → ready-to-cut GCode for a simple bracket.

## Why the existing toolchain mostly applies

- `scripts/gcode_validate.py` already has the `;HEAD:` detection pattern from laser; adding `plasma` is a small extension.
- `scripts/job_params.py` already has parallel checklists (PREFLIGHT_CHECKLIST and LASER_PREFLIGHT_CHECKLIST); a third (PLASMA_PREFLIGHT_CHECKLIST) follows the same pattern.
- `cnc.py preflight` already auto-detects the head via `detect_head`; just add `plasma` to that function.
- New: `profiles/plasma_materials.yaml` with per-material (thickness × material kind) parameter sets — feed rate, current setting on the ArcFony, pierce delay.

The new code that's plasma-specific is small (maybe 100-200 lines once the mechanical/electrical work is done).

## Safety items that absolutely must be addressed

| Item | Why |
|---|---|
| Galvanic isolation between GRBL and plasma | EMI from HF start can crash USB serial or fry the controller. |
| Ground clamp on workpiece | Plasma current returns through here; without it the arc current can find a path through the machine, killing the controller and possibly causing fire. |
| Outrigger positions torch OFF the machine bed | Plasma slag burns through anything; protect the ball screws. |
| Air supply (clean, dry, ~80 PSI) | Plasma needs clean compressed air. Water in the line ruins the consumables and the cut. |
| Welding helmet, gloves, sleeves | UV from the arc burns skin and eyes in seconds. Standard auto-darkening welding helmet shade 5+. |
| Fire watch + extinguisher | Spatter is hot enough to ignite shop debris. Keep area clear. |
| Ventilation | Even mild steel produces metal-oxide fumes. Galvanized produces zinc fumes (toxic). Painted/coated metal produces unknown toxins. Cut clean material only. |

## What this lesson does NOT do

- Does not magically make the ArcFony controllable. The wiring, the mechanical mount, and probably some custom firmware tweaks are real fabrication work.
- Does not provide torch height control. Cut quality will vary with workpiece warp until a proper THC is added.
- Does not handle thick steel cuts. The Cut53M Pro is rated for some max thickness; consult the manual.
- Does not handle aluminum or stainless. Possible with the right gas (often argon for those), but adds another variable.

## Prior art / starting points for research when ready

- **CandCNC** sells a commercial THC + plasma-table-control board. Worth understanding their interface even though we won't buy it.
- **proma-electronica** has standalone THC modules suitable for retrofitting hobby setups.
- The **LinuxCNC plasma community** has detailed wiring guides for relay-based torch control on hobby CNC machines. Most of the wiring SPECs translate to GRBL.
- The **bitsetters / openbuilds** forum has many threads on EMI mitigation for plasma + GRBL.

## When to start this

After:
1. You've actually done some spindle and laser cuts and the toolchain feels solid.
2. You have an evening (or weekend) for the outrigger mechanical work.
3. You're comfortable with the electrical work (or have access to someone who is).

There's no rush. This is the kind of project where the mechanical and electrical work happens slowly, in the garage, and the software is the trivial last step.
