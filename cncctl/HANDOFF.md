> **SUPERSEDED 2026-11-06:** Alex dropped the Raspberry Pi + Xbox controller for a proper pendant. Kept for reference only.

# cncctl: handoff for a fresh session

**Goal:** one small Python program on the Raspberry Pi. It reads Alex's USB Xbox controller and talks to
the machine's Grbl_ESP32 directly over USB serial: jogging, a hold-to-fire laser with analog power,
and a laser-safe pause. **No cncjs.** It lives in this repo (`cnc_vibes/cncctl/`), so it can share
the material profiles and machine profile and improve with the rest of the toolchain.

Written 2026-11-05 at the end of a long design session. Everything below marked **verified** was
checked against source code in that session; anything marked **to verify** wasn't.

---

## 1. Alex's setup (facts to design around)

**Machine:** AnoleX 4030 Evo Ultra 2 (router frame) with a LaserTree 10 W diode head.
- **Controller:** Grbl_ESP32 (GRBL 1.3a). Spindle type LASER.
- **Web UI:** http://192.168.1.93/ (DHCP; hostname `AnoleX`; telnet on 23).
- **Jobs run on the ESP32 itself:** files on its SD card, started over WiFi or the web UI. **The Pi
  is not the job sender.** cncjs was only the easiest way to get controller jogging.
- **Settings** (dump of 2026-11-04, full copy in `custom_setups/anolex/SETTINGS_2026-11-04.md`):

  | Setting | Value |
  |---|---|
  | `$30` / `$GCode/MaxS` | 1000 (S1000 = 100 %) |
  | `$32` / `$GCode/LaserMode` | **Off** in the dump. Our job files send `$32=1`, so its live state is an open question; read it at startup. |
  | Max rate | 3000 mm/min X/Y/Z |
  | Acceleration | 300 mm/s² |
  | Travel | X 400, Y 600, Z 130 |
  | Soft limits | **Off** (`$20=0`); hard limits **On** |
  | Homing | `$Homing/Cycle0=Z`, `Cycle1=XY`, `$Homing/DirInvert=XY`, home MPos −0.100 |
  | Front button | `$User/Macro0=$SD/Run=_reset.gcode`: home X and Y only, `G53 G0 X21 Y8` (the laser bed origin), `G10 L2 P1 X21 Y8` (stores it as the G54 zero). File: `custom_setups/anolex/_reset.gcode`. |

- **Laser habits** (`material_profiles/laser_materials.yaml`):
  - Every cut is **static M3** at 100 %. M4 dynamic "did not fire at all on this controller", probably
    because laser mode is off.
  - Every cut starts with a 1 s warmup lead-in.
  - Cut recipes, by short material id:

    | Material | Feed (mm/min) | Passes | Notes |
    |---|---|---|---|
    | `ply3` | 350 | 1 | |
    | `birch3` | 500 | 1 | |
    | `mdf3` | 700 | 3 | **unverified**; new 2.82 mm batch |
    | `mdf6` | — | — | unverified guess |
    | `acrylic3` | — | — | black; untested |

**Raspberry Pi:** connected to the machine by USB.
- Today it runs cncjs plus a gamepad pendant plugin. Which plugin is unknown; Alex patched something
  locally, and the patch is not on GitHub.
- It has a **desktop**: Alex wants a desktop icon and a wallpaper. Note that `custom_setups/anolex/RASPBERRY_PI.md`
  recommends Pi OS *Lite*; that's outdated for this Pi.
- **To verify on the Pi:** OS version, Wayland/labwc or X11, screen resolution, how cncjs is started
  (it has to stop so cncctl can open the serial port), the serial device name (`/dev/ttyUSB0`?) and the baud rate (Grbl_ESP32 default 115200).

**Controller:** USB Xbox controller. The triggers are analog.
- With the `xpad` driver, LT = `ABS_Z`, RT = `ABS_RZ`, range 0..255 (360 pad) or 0..1023 (One pad).
- Over Bluetooth with the stock hid driver, RT is often `ABS_GAS` and LT `ABS_BRAKE`.
- Read `absinfo` for the range instead of assuming it. Build a `--monitor` mode first.

---

## 2. Button mapping (proposed; **TODO: revisit with Alex**)

| Control | Action |
|---|---|
| **LT** (analog) + **d-pad** | Jog X/Y while held. LT depth sets the speed: light = slow, full = fast (speeds TBD; machine max 3000 mm/min). This matches his current habit "left trigger + d-pad". |
| **LB** + d-pad up/down | Jog Z |
| **RB** (hold) | Arm the laser: the dead-man. The laser never fires unless RB is held. |
| **RT** (analog) | Laser power while armed: released = off, light pull = 10 %, full = 100 % |
| **Start** | **Pause / resume**, laser guaranteed off while paused (see §3) |
| **Back / View** | (proposal) cycle the active material (`mdf3` → `ply3` → `birch3` …) for the cut-line feature |
| **A / B / X / Y** | (proposal) A = tap-step jog toggle; B = laser off + jog cancel (panic, always works); X = set work zero here (`G10 L20 P1 X0 Y0`); Y = "go home" (same as `_reset.gcode`: `$HX`, `$HY`, `G53 G0 X21 Y8`) |
| **Xbox / guide** | (proposal) quit / restart cncctl |

Open questions for Alex:
- Is this mapping right, or does he want his current plugin's exact layout?
- Jog speeds: slowest and fastest?
- Fixed-distance tap jogging, for example a d-pad tap moving exactly 0.1 or 1 mm?
- What should the face buttons do?

**Deliverable: a controller diagram** (PNG at the Pi's screen resolution) labelling every button. Set it as
the desktop wallpaper. Generate it from the same mapping table the code uses, so it can't drift.
For example, an SVG controller outline with labels rendered to PNG with cairosvg or Pillow; `cncctl --wallpaper` writes it.

---

## 3. Behaviour spec

### Jogging (Grbl `$J=`)
- **Use Grbl's documented "joystick" method:** while a direction is held, stream short incremental jogs
  (`$J=G91 X.. Y.. F..`), each about v × dt with dt ≈ 0.05–0.1 s, keeping only a couple in the
  planner. On release, send **0x85** (jog cancel), which stops at once and flushes the queue.
  ([Grbl jogging doc](https://github.com/gnea/grbl/blob/master/doc/markdown/jogging.md))
- **Soft limits are OFF on this machine:** a runaway jog hits a hard limit and alarms.
  Clamp jog targets to the travel (X 400, Y 600, Z 130) using MPos from status reports.
  The machine-coordinate sign convention after homing (DirInvert XY, home MPos −0.1) is **to
  verify**: read MPos at both ends. Alternatively, recommend Alex turns soft limits on.
- Jog speed comes from LT depth. Diagonals work: d-pad up+right = X+Y.

### Hold-to-fire laser
- **Firing:** RB held and RT past a small deadzone → fire. **Verified in source:** Grbl_ESP32 jog moves carry the current laser state and
  power (`Jog.cpp`: "Spindle and coolant are allowed to fully function with overrides during a
  jog"; `GCode.cpp` copies `gc_state.modal.spindle` and `spindle_speed` into each jog's planner data). So
  jogging while firing cuts.
- **Turn on:** send `G1 F1` then `M3 S1000`, the same as cncjs's `lasertest:on`. In laser mode, Grbl only lights the laser in G1/2/3 motion mode.
  **Set the power override *before* firing** (below), so it never blips at 100 %.
- **Live power:** Grbl real-time spindle override bytes, written raw to the serial port: 0x99 = 100 %, 0x9A = +10, 0x9B = −10, 0x9C = +1, 0x9D = −1.
  Range 10–200 %; cap at 100 %. **RT 0–100 % → override 10–100 %.**
  These act immediately, even mid-jog. Track the current value and correct it from the `Ov:` field of status reports.
- **Release** (RB or RT): override → 10 % (real-time, immediate), **0x85 jog cancel**, then `M5`.
  Why both: a G-code `M5` only runs after the planner empties, so without the cancel a running jog keeps
  cutting until it ends. (Verified: `spindle->sync()` waits for the buffer.)
- **While held and standing still the laser keeps burning.** That's intended ("hold = on"), but put a
  **max on-time** per press (2 min) that turns it off until the buttons are released.
- **Arm first, then jog.** M3 sent mid-jog waits for the move to finish.

### Pause (Start button), laser guaranteed off
- **Pause:** `!` (feed hold), then once the status says `Hold:0` (stopped), **0x9E** (spindle stop
  override, valid only in Hold), so the laser is off during the pause.
- **Resume:** `~` (cycle start). Grbl restores the spindle on resume, with its spin-up delay (`$Spindle/Delay/SpinUp=0`).
- This is the main reason pause exists: a plain feed hold with laser mode off **keeps the laser on and
  torches the workpiece** (Alex's experience).
- **To verify** on the machine: whether `$32` changes this, and that 0x9E in Hold switches the
  beam off on Grbl_ESP32 1.3a (`Protocol.cpp` has the spindle-stop-override code: `spindle_stop_ovr`,
  `spindle->set_state(SpindleState::Disable, 0)`).
- **Pause also works on jobs running over WiFi.** Grbl_ESP32 takes real-time commands from every channel, so
  the controller becomes a pendant for SD/WiFi jobs too. **To verify.**

### Safety rules (non-negotiable)
- **The laser never fires unless the machine is Idle or Jog.** Poll `?` at ~5 Hz. During **Run** (a WiFi
  job) only pause/resume/panic are live: no jogging, no laser, no overrides. Those bytes would change the job.
- **Laser off on every exit path:** quit, Ctrl-C, SIGTERM, exception, controller unplugged, serial
  error, terminal closed (SIGHUP). On a serial error, try `M5` then close.
- **Opening the USB port can reset the ESP32** through the DTR/RTS lines. Mid-job, a reset would kill the WiFi job.
  - Create `serial.Serial()` unopened, set `dtr=False`, `rts=False`, then `open()`.
  - **Test once while idle** to see whether it still resets.
  - Never open the port while a job runs unless that test passes.
- **Single instance** (a lock file), so the autostart and the desktop icon can't run two copies.

### Grbl line protocol
- **Send/response:** wait for `ok`/`error` before the next line. Real-time bytes (`?`, `!`, `~`, 0x85, 0x9x)
  bypass the queue and can go any time.
- **Errors:** parse `error:N` and `ALARM:N` and show them.
- **Status reports:** `<Idle|MPos:..|FS:..|Ov:..>`. The 2026-11-04 dump showed `$Report/Status=1`, which gives MPos.

---

## 4. Desktop integration on the Pi
- **Runs at login in a visible terminal** (Alex wants to see it and close / restart it):
  - An XDG autostart entry, `~/.config/autostart/cncctl.desktop`, with `Exec=lxterminal -t cncctl -e <venv>/bin/cncctl`.
    On Pi OS Bookworm with labwc, confirm XDG autostart is honoured; otherwise use `~/.config/labwc/autostart`.
  - **Desktop icon:** the same `.desktop` on `~/Desktop` to relaunch it.
  - `cncctl --install-desktop` writes both, plus the wallpaper.
- **The terminal shows a live status line:** machine state, position, laser on/off and %, override, the active
  material, paused/armed flags, and the last error. Keep it compact (curses or plain ANSI redraws).

## 5. Integration with cnc_vibes
- **Package:** `cnc_vibes/cncctl/`, a top-level tool like `quickcut/`. Entry point `cncctl`.
- **Read the shared profiles:**
  - `material_profiles/laser_materials.yaml` (use the short ids: `mdf3`, `ply3`, `birch3`, …)
  - `custom_setups/anolex/anolex_4030_evo_ultra2.yaml` (travel, max rates)
  - So a recipe change reaches the controller too.
- **Reuse:** `custom_setups/anolex/laser_trigger/` is the abandoned cncjs-based helper ("approach A").
  - Its `LaserControl` class (hold-to-arm, deadzone, override stepping, power set before fire, release
    sequence, max-on lockout, refuse unless Idle/Jog) and its 7 tests port almost unchanged.
  - Only the `send` layer changes: `lasertest:on` → `G1F1` + `M3S1000`, `spindleOverride` → raw 0x9x bytes, `jogCancel` → 0x85.
  - **Delete `laser_trigger/` once ported.**
- **Tests without hardware:** a fake serial port that answers `ok` and status reports, and fake evdev
  events. Cover the jog streaming and cancel, the travel clamp, the laser state machine, pause → hold → 0x9E,
  refusal during Run, and laser-off on every exit path.
- **Dependencies:** `evdev`, `pyserial`, `pyyaml`. Optional: `cairosvg` for the wallpaper.
- **Follow the repo CLAUDE.md:** any number stated in prose must be copied from a source, not from memory.

## 6. Feature backlog (after the core works)
- **Cut a line between two points:** mark A, mark B (face buttons), cut the straight line with the active material's
  recipe (feed, passes, 1 s warmup). This was Alex's original "cut this in half" wish, without typing G-code.
- **Feed override during a WiFi job** (0x90–0x94) from the controller.
- **Frame the current job's bounding box** with the laser off (this was offered earlier).
- **Aim mode:** the lowest visible power for positioning. Note the 10 % override floor: use a direct `M3 S10`-ish
  value instead, **to verify** that it's visible but doesn't mark.
- **Rumble feedback** while the laser is on (evdev force feedback) and on alarms.
- **Step jog** (fixed 0.1 / 1 / 10 mm taps), and a slow "creep" mode.

---

## 7. What was researched (prior art and findings)

**Grbl_ESP32 source** (bdring/Grbl_Esp32, cloned and read 2026-11-04/05):
- **Front-button macros:** `$User/Macro0..3`, run only in Idle. `$SD/Run=<file>` refuses in Alarm or while busy.
  `&` in a macro becomes a newline (250-character limit). The wiki calls the macro button "not very robust".
- **Single-axis homing** exists: `$HX`, `$HY`, `$HZ`. The startup script runs only after a full `$H`.
- **Jogging carries the laser state and power;** overrides work during a jog (quoted in §3).
- **Laser mode:** a block without motion and not in G1/2/3 disables the laser. Changing to G1 without
  axis words forces a sync. That's why `G1 F1` precedes `M3`.
- **Spindle stop override (0x9E)** is handled in `Protocol.cpp`, only in Hold.

**cncjs source** (cncjs/cncjs `GrblController.js`): the commands it offers are thin wrappers:
- `lasertest:on` = `G1F1` + `M3S<pct × maxS>`, with the comment "The laser will only turn on when Grbl is in a G1, G2, or G3 motion mode"
- `lasertest:off` = `M5S0`
- `spindleOverride` ±10/±1/0 → 0x9A/0x9C/0x99…
- `jogCancel` → 0x85
- Auth is a JWT signed with `~/.cncrc`'s `secret`.

  cncctl doesn't need cncjs: the same bytes go straight to the serial port.

**Gamepad pendants for cncjs (prior art):**
- [balthisar/cncjs-pendant-gamepad](https://github.com/balthisar/cncjs-pendant-gamepad), TypeScript, the closest
  match and possibly the base of Alex's current plugin (unconfirmed).
  - L1/R1 (bumpers) are "slow" jog dead-man buttons and LT/RT "fast" ones; bumper + trigger switches the d-pad to Z.
  - Its config `actionsMap` has `spindleOn` / `spindleOff` actions, but **on press only, at a fixed speed: no
    hold-to-fire and no analog power.**
  - It streams fixed-interval jog moves.
- [cncjs-pendant-ps3](https://github.com/williamkapke/cncjs-pendant-ps3) (fork: [nyos/cncjs-pendant-gamepad](https://github.com/nyos/cncjs-pendant-gamepad)): DualShock 3 only.
- [cncjs-pendant-keyboard](https://github.com/cncjs/cncjs-pendant-keyboard), [cncjs-pendant-ble](https://github.com/anthonymyatt/cncjs-pendant-ble),
  and the [cncjs wiki on pendants](https://github.com/cncjs/cncjs/wiki/Using-Pendants).
- **Alex's GitHub** (checked with the session's repo list): no cncjs or pendant fork. He has `grbl`, `grbl-1` and
  `lasercutter` repos, which weren't examined. His local plugin patch is presumably only on the Pi.

**Approaches weighed** (2026-11-05):

| Approach | Verdict |
|---|---|
| A. Separate laser helper next to the cncjs plugin | Built (`laser_trigger/`). Conflicts with the plugin's bumper/trigger dead-man buttons, two programs share one controller, and it depends on cncjs. **Rejected.** |
| B. Fork and extend the cncjs plugin | Needs TypeScript changes for hold and analog power, and keeps the cncjs stack Alex dislikes. **Rejected.** |
| C. **cncctl:** one Python process, evdev → pyserial → Grbl | One owner of the controller and the port, full mapping control, few dependencies, reuses A's tested laser logic. **Chosen.** |

## 8. First steps for the new session
1. **Read** this file, `custom_setups/anolex/laser_trigger/laser_trigger.py` and its tests, and `custom_setups/anolex/SETTINGS_2026-11-04.md`.
2. **Ask Alex** the open questions in §1 (Pi facts) and §2 (mapping, jog speeds, face buttons).
3. **Build:** the serial layer (with the DTR-safe open) → status polling → jogging → laser (ported) → pause → terminal UI → desktop/wallpaper. Test each with fakes first.
4. **First hardware test** (idle machine, laser glasses, scrap): `--monitor`, then jogging only, then a 10 % laser, then pause with the laser on.
