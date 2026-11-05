# Xbox controller: hold to fire the laser (cncjs on the Pi)

Hold **RB** to arm, then pull **RT** for power: light pull 10 %, full pull 100 %, released = off.
Jog with your existing pendant (LT + d-pad) while holding them, and the jog moves cut.
It runs beside the jog plugin: both read the same controller and both talk to cncjs. Nothing about
the plugin changes.

## How it works (checked against the source code)
- **Grbl_ESP32 cuts during jogs.** Each jog move carries the current laser state and power
  (`Jog.cpp`: "Spindle and coolant are allowed to fully function with overrides during a jog").
- **Fire / stop:** cncjs's own `lasertest:on` (`G1F1`, `M3S1000`) and `lasertest:off` (`M5S0`).
- **Live power:** cncjs `spindleOverride`, which sends Grbl's real-time 0x9A–0x9D bytes. These act at once, even mid-jog.
  The range is 10–100 % (Grbl's override floor is 10 %).
- **On release:** override to 10 % at once, cancel the jog (`jogCancel`, 0x85), then `M5`. Without the jog cancel, a jog that
  started with the laser on keeps it on until the move ends: Grbl runs a G-code M5 only after the move finishes.

## Safety
- The laser **stays on while the head stands still** if you keep holding. That's the design. Don't dwell on one spot.
- It switches the laser off on: release, script exit, controller unplugged, cncjs reporting anything but Idle/Jog
  (it won't fire during a job), or **2 minutes** on in one press (`--max-on`).
- **Arm and pull first, then jog.** If you press while a jog is already moving, the laser comes on when that move ends,
  because `M3` waits for the move.
- If cncjs itself dies while the laser is on, nothing can send M5. Keep the machine's stop button in reach,
  as with any job.
- `lasertest:on` leaves the G-code feed at F1. Jogs and our job files always give their own F, so this is harmless,
  but a hand-typed `G1` without F would crawl.

## Install on the Pi
```sh
cd ~/cnc_vibes/custom_setups/anolex/laser_trigger
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
sudo usermod -aG input $USER          # read /dev/input without sudo (log out and back in)
.venv/bin/python laser_trigger.py --list      # is the controller there?
.venv/bin/python laser_trigger.py --monitor   # press RB, pull RT: note the names it prints
.venv/bin/python laser_trigger.py --dry-run   # prints what it would send cncjs; nothing fires
```
**First live test:** laser glasses on, a scrap under the head, and focus set. Run `laser_trigger.py`,
hold RB and barely pull RT (10 %). Release. Then try a short jog.

To start it at boot: `cp laser-trigger.service ~/.config/systemd/user/ && systemctl --user enable --now laser-trigger`
(also `loginctl enable-linger $USER` so it runs without you logged in).

## If something doesn't match
- **Other buttons:** `--arm BTN_TL` (LB), `--arm none` (trigger alone, no dead-man), `--trigger ABS_Z`
  (left trigger). `--monitor` shows the names your controller uses. Over Bluetooth with the stock
  driver, RT is usually `ABS_GAS` (picked automatically); over USB / xpadneo it's `ABS_RZ`.
- **cncjs elsewhere:** `--url http://host:8000`.
- **cncjs has user accounts:** the token is signed as user `laser-trigger` with an empty id. If you
  added users in cncjs's settings, it may refuse. Say so and I'll add a `--user` option.
- **cncjs fork / plugin fix:** neither is on your GitHub (checked 2026-11-05). If the fix lives on the
  Pi, this doesn't touch it.
