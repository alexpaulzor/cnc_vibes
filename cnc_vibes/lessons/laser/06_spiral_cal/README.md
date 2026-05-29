# 3f — Spiral laser calibration card

A hex spiral of small test patches starting at WCS origin. Each patch
is a 15mm circle with a double Archimedean spiral inside it — when the
patch cuts through cleanly, the inside disk falls apart into several
pie-slice pieces and you get instant visual confirmation.

The whole layout grows outward from the center, so you can drop a small
scrap of material under the laser head, jog to a free spot at its
center, and run a sweep without burning through a fresh sheet.

## What it tests

You pick ONE variable to sweep:

- **power** — the laser power S value (as % of $30 max)
- **feed** — the cut feedrate (mm/min)
- **passes** — number of times each ring is re-traced

The other two parameters stay at the material default (or you override
with `--power` / `--feed` / `--passes`).

**Z (focus) is NOT swept** here — the diode laser has no Z control. To
compare focus distances, swap the spacer manually between runs and re-
cut a sweep.

## Layout math

Patches are 15mm OD with 2mm edge gap → 17mm center-to-center.

- Ring 0: 1 patch at origin
- Ring K: 6K patches on a circle of radius K × 17mm

So 1 / 7 / 19 / 37 / 61 patches at rings 0 / 1 / 2 / 3 / 4. A 5-value
sweep needs only ring 0 + part of ring 1 (fits in ~50mm square).

## Usage

```
# Top-level shim
python cnc.py cal-laser --material cardboard_thin_1mm \
    --sweep power --values 30,40,50,60,70 \
    --laser-mode static --laser-warmup-ms 250

# Or invoke the script directly
python lessons/laser/06_spiral_cal/spiral_cal.py \
    --material plywood_baltic_birch_3mm \
    --sweep feed --values 1500,2000,2500,3000,3500 \
    --power 80 --laser-mode static --laser-warmup-ms 300

# Guided interactive mode (prompts walk through all the choices)
python cnc.py cal-laser interactive
```

Defaults: `--laser-mode static` (M3 — calibration is easier to read
with constant power, and avoids any M4 firmware quirks),
`--laser-warmup-ms 250` (defeats cold-start fade-in per patch).

## How to read the output

After cutting, look at each patch:

| Observation                            | Meaning                              |
|----------------------------------------|--------------------------------------|
| Inside pieces fall out cleanly         | through-cut, settings work           |
| Top scored but back uncut              | underpowered or too fast             |
| Pieces fall but edges heavily charred  | overpowered — back off a step        |
| Pieces fall, edges clean               | this is your setting; pick the leanest|
| Inside fuses to outer ring             | M4 starving on short segments — try `--laser-mode static` |

The script prints the patch grid in cut order before emitting:

```
patch  1: (  +0.00,   +0.00)  power=30.0
patch  2: ( +17.00,   +0.00)  power=40.0
patch  3: (  +8.50,  +14.72)  power=50.0
...
```

So you can identify which patch was which setting after cutting.

## Workflow comparison

| Lesson | Use when |
|---|---|
| **3b** (linear cal grid) | First-time material characterization; sweeps power × passes × speed in one matrix. Uses a fresh rectangular sheet. |
| **3e** (test card) | Pre-flight before any real cut — kerf measurement + tram check. |
| **3f** (this) | Iterative single-axis sweep on a scrap. Cheap, dense, no wasted material. Run it before a real cut whenever you suspect drift. |
| **Int-04** (interactive cal) | Per-patch fine-tuning with the machine connected; spends one fresh row per parameter change. |

## What it doesn't do (yet)

- No Z sweep (manual spacer change required)
- No automatic best-pick — you eyeball results
- No mid-sweep stop-and-adjust — the whole sweep emits as one GCode
  file. Send it, then evaluate.

If you want a true wizard-style "cut one, evaluate, adjust, repeat"
flow with the controller live, that's [Int-04](../../integration/04_interactive_laser_cal/)
— different tool, different tradeoffs.
