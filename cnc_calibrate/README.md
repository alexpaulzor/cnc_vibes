# cnc_calibrate

Clean home for the CNC / laser **calibration and machine utilities** forklifted
out of the older, messier `cnc_vibes/`. Each tool is self-contained.

## Tools

| Command | What it does |
|---------|--------------|
| `calibrate.py cal-laser` | Concentric **elliptical** speed × pass-count laser calibration plate (flagship). One plate maps feed (inner→outer rings) against number of passes (angular sectors), with an engraved legend so the cut piece is a tape-to-the-machine reference. |
| `calibrate.py ip` | Print the controller IP (fresh cache else mDNS scan). |
| `calibrate.py find-machine` | Discover Grbl_ESP32 controllers on the LAN (mDNS + SSDP). |

## Quick start

```sh
# Recommended 3mm-MDF test on a ~10W diode: feeds 100–1000 over 10 rings,
# passes 1/2/3 as sectors, elliptical plate for re-orientation.
python calibrate.py cal-laser --passes 1,2,3 --circles 10 --min-r 3 --max-r 30 \
    --time-s 11.3 --aspect 1.35
# -> build/cal_laser/cal_laser.gcode  + toolpath PNG + KEY PNG
```

Origin is the CENTER of the plate. Cut inner→outer; watch which ring/sector
combinations fall free. The elliptical outline lets you re-orient the jumbled
nested pieces; the engraved digits + the KEY PNG decode which is which.

Machine constants (weak ~10W diode): static **M3** at 100% (M4 dynamic
under-fires), ~1s cold-start warmup handled by a spiral lead-in per ring.

## Notes / limitations
- Sectors are equal **parameter-angle** (`360/N`), not equal arc-length, so on an
  ellipse the minor-axis sectors have slightly less perimeter than major-axis
  ones. Fine for a qualitative cut/no-cut read; the pass-count digit is engraved
  at each sector for reference.
- `--min-r/--max-r` are the **semi-minor** axis (they equal the radius only when
  `--aspect 1.0`). Feeds are derived (`perimeter / --time-s`); the default
  `--time-s` targets ~100–1000 mm/min for the default ellipse, so adjust it if
  you change the radii or aspect (the printed output and KEY show the real feeds).
- On-part **feed** digits are stacked up the center and cross the ring cuts —
  legible but the KEY png is the authoritative decoder. Pass-count digits sit
  outside the rings.


## Deps
`pillow` (PNG renders). Network discovery uses `zeroconf` (optional) + stdlib
sockets. No numpy/opencv/shapely.
