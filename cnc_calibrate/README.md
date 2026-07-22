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

## Deps
`pillow` (PNG renders). Network discovery uses `zeroconf` (optional) + stdlib
sockets. No numpy/opencv/shapely.
