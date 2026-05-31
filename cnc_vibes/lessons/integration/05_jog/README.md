# Int-05 — `cnc.py jog`: xbox + keyboard jogger with inline Z-probe

A single entrypoint to drive the Anolex 4030-Evo from the operator's chair: jog
with an xbox controller (preferred) or the keyboard (fallback), and run an
auto Z-probe inline with one button press. Replaces the round-trip to Candle
for the most common operator workflows.

## Why this exists

- Candle's Z-probe errors past 50mm of travel, but the Anolex's home
  position is ~200mm above the working surface — so probing from home is
  unusable. Default `--probe-max-mm` here is **250mm**.
- Switching to Candle just to jog before running a `.gcode` file from the
  shell is annoying. This stays in one terminal.

## Usage

```bash
python cnc.py jog --print-map                # show the button map, no machine needed
python cnc.py jog --auto                     # mDNS-discover the controller and go
python cnc.py jog --telnet 192.168.4.116     # explicit telnet (Grbl_ESP32 port 23)
python cnc.py jog --port /dev/cu.usbserial-X # explicit USB serial
python cnc.py jog --auto --no-controller     # keyboard only, even if a joystick is plugged in
```

## Button map

```
─── MOTION ──────────────────────────────────────────────────────
  Xbox left stick (analog)      Keyboard W A S D       X / Y
  Xbox right stick Y            Keyboard ↑ / ↓         Z
  Xbox D-pad ←→↑↓               (same WASD = step)     X/Y step
  Xbox RB + D-pad ↑↓            (no kb equivalent)     Z step

─── SPEED MODIFIERS ─────────────────────────────────────────────
  Xbox LB (held)                Keyboard SHIFT+letter  slow ×0.1
  (none)                        lowercase              normal feed
  Xbox RT (analog)              (no kb equivalent)     fast (×N)

─── ACTIONS ─────────────────────────────────────────────────────
  Xbox A                        Keyboard p             Z-PROBE start
  Xbox B                        Keyboard Esc           CANCEL motion / probe
  Xbox X                        Keyboard 0             zero WCS at current pos
  Xbox Y (hold 1s)              Keyboard H (capital)   HOME ($H)

─── SESSION ─────────────────────────────────────────────────────
  Xbox Back / Select            Keyboard q             exit cleanly
  Xbox Start                    Keyboard ?             reprint this map
```

**Keyboard limitation**: most terminals deliver key-press but not key-release
events, so keyboard motion is **tap-to-step** (one `--step-mm` move per key
press). The xbox controller supports continuous analog jog via the sticks AND
step jog via the D-pad.

**Why uppercase letters mean "slow"**: matches the same convention as `H`
(uppercase) meaning HOME — anything that needs a shift modifier is intentional.
Lowercase `h` is unmapped on purpose.

## Z-probe sequence

Default flow (two-stage):

1. Refuse if not `Idle` (or in `Alarm` — surfaces a hint to send `$X`).
2. `G38.2 Z-{max} F{feed_fast}` — fast approach (default max 250mm, feed 200).
3. Retract `--probe-retract-mm` (default 2mm), then `G38.2 Z-{retract*2} F{feed_slow}`
   for a precision touch (default feed 25).
4. `G10 L20 P1 Z{plate}` — sets WCS Z to the touch-plate thickness. With
   `--probe-plate-mm 0` (no plate), WCS Z=0 lands at the touched surface.
5. Final clearance retract.
6. Print the touched machine Z, the WCS write, and the slow-vs-fast repeatability delta.

Mid-probe, press **B** (or `Esc`) to send the `0x85` realtime jog-cancel byte.
GRBL 1.1h aborts the in-flight `G38.2`. If the machine lands in Alarm, the
output names the alarm and points at `$X`.

### Probe flags

| Flag | Default | What |
|---|---|---|
| `--probe-max-mm` | 250 | Maximum Z travel during the fast approach |
| `--probe-feed-fast` | 200 | Fast-approach feedrate mm/min |
| `--probe-feed-slow` | 25 | Slow precision-touch feedrate mm/min |
| `--probe-retract-mm` | 2 | Retract between fast and slow touch |
| `--probe-plate-mm` | 0 | Touch-plate thickness (written as WCS Z) |
| `--probe-no-set-wcs` | off | Probe but skip the `G10 L20 P1 Z` write |
| `--probe-one-stage` | off | Skip the slow re-touch (single `G38.2`) |

## Jog flags

| Flag | Default | What |
|---|---|---|
| `--step-mm` | 1.0 | D-pad / WASD step distance |
| `--feed` | 1500 | Base jog feedrate mm/min |
| `--fast-mult` | 5.0 | Max multiplier when RT is held fully |
| `--slow-mult` | 0.1 | Multiplier when LB held or SHIFT+letter |
| `--deadzone` | 0.15 | Stick deflection below this is ignored |

## Architecture notes

- The pure-function core (`translate_controller`, `translate_keyboard`,
  `build_probe_sequence`) is fully unit-tested without controller, serial,
  or terminal. See `tests/test_jog.py`.
- The transport layer (`TelnetTransport`, `_send_line`, `_read_status`) is
  imported from `lessons/integration/04_interactive_laser_cal/interactive_cal.py`
  via `sys.path` insertion — same pattern `probe_corner.py` already uses.
  Extraction to `scripts/grbl_transport.py` is deferred until the third
  consumer.
- Jog motion uses `$J=` (not `G91 G0/G1`) because `$J=` is the only motion
  type that GRBL cleanly aborts on the `0x85` realtime byte.
- Send rate is capped at 20 Hz (the event loop tick) to keep the planner
  from saturating.

## Tests

```bash
python cnc.py test lessons/integration/05_jog/
```

32 tests, all pure-function. No machine needed.

## Verified hardware

- macOS dev mac: USB Xbox controller (Series X/S generation) works out of the
  box with SDL2. Bluetooth pairing varies.
- Raspberry Pi (headless): `SDL_VIDEODRIVER=dummy` is set by `jog.py` itself,
  so pygame works without an X server.

## See also

- [Int-03 probe-corner](../03_probe_corner/) — older one-shot probe that drives
  X+Y+Z corner-finding for stock origin. The new `jog` Z-probe is a simpler
  inline tool; corner probing still lives in Int-03.
- [Int-04 interactive laser cal](../04_interactive_laser_cal/) — laser
  parameter wizard; the transport code that `jog` reuses lives there.
- [Int-01 inspect](../01_inspect/) — read-only GRBL state queries.
