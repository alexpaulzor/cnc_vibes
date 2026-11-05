#!/usr/bin/env python3
"""Hold-to-fire laser on the Xbox controller, through cncjs, next to the jog pendant.

  RB (right bumper)  hold to ARM. The laser never fires unless RB is held (dead-man).
  RT (right trigger) power while armed: released = off, a light pull = 10 %, full pull = 100 %.

Jog with your existing pendant plugin (LT + d-pad) while holding RB + RT: the jog moves cut at
the trigger's power. Grbl_ESP32 runs jogs with the current laser state ("Spindle and coolant are
allowed to fully function with overrides during a jog", Jog.cpp), and the power follows the
trigger live through Grbl's real-time spindle override (10 % .. 100 % in 1 % steps).

Releasing RB or RT turns the laser off. Each jog move carries the laser state it started with,
so on release this script also (1) drops the override to 10 % at once and (2) cancels the
running jog, so nothing is cut after release. (--keep-jog leaves the jog running at 10 % until
it ends.) The laser is also switched off when the script exits, the controller disconnects,
cncjs reports anything but Idle/Jog, or it has been on for --max-on seconds.

The laser keeps burning while it's armed and the head is standing still (that's the point:
hold = on). Don't park it on one spot.

    python3 laser_trigger.py --list                      # input devices, to find the controller
    python3 laser_trigger.py --monitor                   # print button / axis events (find codes)
    python3 laser_trigger.py --dry-run                   # no cncjs: print what it would send
    python3 laser_trigger.py                             # run (cncjs on http://localhost:8000)

Talks to cncjs the way pendants do (socket.io + a JWT signed with the secret in ~/.cncrc), so
it shares the serial port with the cncjs web UI and your gamepad plugin. Reads the controller
with evdev without grabbing it, so the jog plugin keeps working.
"""
import argparse
import json
import os
import signal
import sys
import threading
import time

POLL = 0.05            # s between power updates (realtime override bytes are cheap, but don't flood)
MIN_OVR, MAX_OVR = 10, 100   # Grbl's spindle override floor is 10 %; cap at 100 % (S = $30)


class LaserControl:
    """Decides what to send. `send(cmd, *args)` is a cncjs controller command, e.g.
    send('lasertest:on', 100, 0, 1000) or send('spindleOverride', -10)."""

    def __init__(self, send, max_s=1000, deadzone=0.04, max_on=120.0, cancel_jog=True, log=print):
        self.send, self.max_s, self.deadzone, self.max_on, self.cancel_jog, self.log = \
            send, max_s, deadzone, max_on, cancel_jog, log
        self.on = False
        self.ovr = 100             # our idea of Grbl's spindle override; corrected by status reports
        self.on_since = 0.0
        self.machine_ok = True     # cncjs says Idle or Jog
        self.locked_out = False    # max-on tripped: stays off until RB or RT is released

    def target_ovr(self, frac):
        return max(MIN_OVR, min(MAX_OVR, round(frac * 100)))

    def step_override(self, target):
        while self.ovr != target:
            d = target - self.ovr
            step = 10 if d >= 10 else -10 if d <= -10 else (1 if d > 0 else -1)
            self.send("spindleOverride", step)
            self.ovr += step

    def off(self, why):
        if not self.on:
            return
        self.step_override(MIN_OVR)            # realtime: drop power right now
        if self.cancel_jog:
            self.send("jogCancel")             # realtime: stop the move that carries the laser
        self.send("lasertest:off")             # M5S0 (runs once the planner is empty)
        self.on = False
        self.log(f"laser OFF ({why})")

    def update(self, armed, frac, now=None):
        now = time.monotonic() if now is None else now
        want = armed and frac > self.deadzone and self.machine_ok
        if not (armed and frac > self.deadzone):
            self.locked_out = False
        if self.locked_out:
            want = False
        if want and self.on and now - self.on_since > self.max_on:
            self.locked_out = True
            self.off(f"on for more than {self.max_on:g} s; release and press again")
            return
        if not want:
            self.off("released" if self.machine_ok else "machine not Idle/Jog")
            return
        target = self.target_ovr(frac)
        if not self.on:
            self.step_override(target)                         # set the power before firing
            self.send("lasertest:on", 100, 0, self.max_s)       # G1F1, M3S<max_s>: full S, scaled by the override
            self.on, self.on_since = True, now
            self.log(f"laser ON at {target} %")
        elif target != self.ovr:
            self.step_override(target)

    def status(self, active_state=None, spindle_ovr=None):
        if spindle_ovr is not None:
            self.ovr = int(spindle_ovr)
        if active_state is not None:
            self.machine_ok = active_state in ("Idle", "Jog")


# ------------------------------------------------------------------ gamepad
def find_device(evdev, path=None):
    if path:
        return evdev.InputDevice(path)
    for p in evdev.list_devices():
        d = evdev.InputDevice(p)
        caps = d.capabilities()
        abs_codes = [c[0] if isinstance(c, tuple) else c for c in caps.get(evdev.ecodes.EV_ABS, [])]
        keys = caps.get(evdev.ecodes.EV_KEY, [])
        if evdev.ecodes.BTN_TR in keys and (evdev.ecodes.ABS_RZ in abs_codes or evdev.ecodes.ABS_GAS in abs_codes):
            return d
    sys.exit("no gamepad with RB and a right trigger found; use --list and --device")


def trigger_axis(evdev, dev, name=None):
    e = evdev.ecodes
    if name:
        return e.ecodes[name]
    abs_codes = [c[0] for c in dev.capabilities().get(e.EV_ABS, [])]
    return e.ABS_GAS if e.ABS_GAS in abs_codes else e.ABS_RZ    # Bluetooth (hid) : xpad / xpadneo


def list_devices(evdev):
    for p in evdev.list_devices():
        d = evdev.InputDevice(p)
        caps = d.capabilities(verbose=True)
        print(f"{p}: {d.name}")
        for (tname, _), codes in caps.items():
            if tname in ("EV_KEY", "EV_ABS"):
                names = [c[0][0] if isinstance(c[0], tuple) and isinstance(c[0][0], (list, tuple)) else
                         (c[0] if isinstance(c, tuple) else c) for c in codes]
                print(f"   {tname}: {names}")


# ------------------------------------------------------------------ cncjs
def cncjs_token(cncrc):
    import jwt
    secret = json.load(open(os.path.expanduser(cncrc)))["secret"]
    tok = jwt.encode({"id": "", "name": "laser-trigger"}, secret, algorithm="HS256")
    return tok.decode() if isinstance(tok, bytes) else tok


class Cncjs:
    def __init__(self, url, cncrc, port, baud, ctl, log=print):
        import socketio                      # python-socketio 4.x speaks cncjs's socket.io 2.x
        self.sio, self.port, self.log, self.ctl = socketio.Client(reconnection=True), port, log, ctl
        self.ready = threading.Event()
        sio = self.sio

        @sio.on("serialport:list")
        def _list(ports):
            if not self.port:
                used = [p["port"] for p in ports if p.get("inuse")]
                if used:
                    self.port = used[0]
            if self.port:
                sio.emit("open", (self.port, {"baudrate": baud, "controllerType": "Grbl"}))
            else:
                self.log("no open serial port in cncjs; connect the machine in the cncjs UI, or pass --port")

        @sio.on("serialport:open")
        def _open(opts):
            self.log(f"cncjs: {opts.get('port')} open")
            self.ready.set()

        @sio.on("controller:state")
        def _state(ctype, state):
            st = (state or {}).get("status", {})
            ov = st.get("ov") or []
            self.ctl.status(st.get("activeState"), ov[2] if len(ov) > 2 else None)

        @sio.event
        def disconnect():
            self.log("cncjs disconnected")
            self.ctl.machine_ok = False

        sio.connect(f"{url}?token={cncjs_token(cncrc)}")
        sio.emit("list")

    def send(self, cmd, *args):
        if self.port:
            self.sio.emit("command", (self.port, cmd) + args)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="list input devices and exit")
    ap.add_argument("--monitor", action="store_true", help="print the controller's events and exit on Ctrl-C")
    ap.add_argument("--dry-run", action="store_true", help="don't connect to cncjs; print the commands")
    ap.add_argument("--device", help="/dev/input/eventN (default: first pad with RB + a right trigger)")
    ap.add_argument("--arm", default="BTN_TR", help="hold-to-arm button (default BTN_TR = RB); 'none' = trigger alone")
    ap.add_argument("--trigger", help="power axis (default ABS_GAS if present, else ABS_RZ)")
    ap.add_argument("--deadzone", type=float, default=0.04, help="trigger fraction treated as released")
    ap.add_argument("--max-s", type=int, default=1000, help="S for 100 %% (Grbl $30; AnoleX: 1000)")
    ap.add_argument("--max-on", type=float, default=120, help="seconds the laser may stay on in one press")
    ap.add_argument("--keep-jog", action="store_true", help="on release don't cancel the running jog (it ends at 10 %%)")
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--cncrc", default="~/.cncrc")
    ap.add_argument("--port", help="serial port as cncjs names it (default: the one cncjs has open)")
    ap.add_argument("--baud", type=int, default=115200)
    a = ap.parse_args()

    import evdev
    if a.list:
        list_devices(evdev)
        return
    dev = find_device(evdev, a.device)
    e = evdev.ecodes
    axis = trigger_axis(evdev, dev, a.trigger)
    info = dev.absinfo(axis)
    arm_code = None if a.arm.lower() == "none" else e.ecodes[a.arm]
    print(f"{dev.path}: {dev.name}; arm {a.arm}, power {e.ABS[axis] if axis in e.ABS else axis} "
          f"({info.min}..{info.max})", flush=True)
    if a.monitor:
        for ev in dev.read_loop():
            if ev.type in (e.EV_KEY, e.EV_ABS):
                print(evdev.categorize(ev), flush=True)
        return

    if a.dry_run:
        def send(cmd, *args):
            print(f"  -> {cmd} {' '.join(map(str, args))}", flush=True)
        ctl = LaserControl(send, a.max_s, a.deadzone, a.max_on, not a.keep_jog)
    else:
        ctl = LaserControl(lambda *x: None, a.max_s, a.deadzone, a.max_on, not a.keep_jog)
        cnc = Cncjs(a.url, a.cncrc, a.port, a.baud, ctl)
        ctl.send = cnc.send
        if not cnc.ready.wait(10):
            sys.exit("cncjs never opened the serial port")
        cnc.send("spindleOverride", 0)       # start from a known 100 %
        ctl.ovr = 100

    state = {"armed": arm_code is None, "frac": 0.0}

    def stop(*_):
        ctl.off("exiting")
        time.sleep(0.3)
        os._exit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def reader():
        try:
            for ev in dev.read_loop():
                if ev.type == e.EV_KEY and arm_code is not None and ev.code == arm_code:
                    state["armed"] = ev.value != 0
                elif ev.type == e.EV_ABS and ev.code == axis:
                    state["frac"] = (ev.value - info.min) / max(1, info.max - info.min)
        except OSError:
            print("controller disconnected", flush=True)
            state["armed"], state["frac"] = False, 0.0
            state["gone"] = True
    threading.Thread(target=reader, daemon=True).start()

    while not state.get("gone"):
        ctl.update(state["armed"], state["frac"])
        time.sleep(POLL)
    ctl.update(False, 0.0)
    stop()


if __name__ == "__main__":
    main()
