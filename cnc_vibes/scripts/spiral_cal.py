#!/usr/bin/env python3
"""Concentric spiral warmup + feed laser calibration — cnc_vibes flagship.

One small disc replaces the LaserGRBL/xTool material grid. Cut concentric rings
inner->outer, each ONE single pass; the feed for ring K is set so
`feed = circumference / time-s`, i.e. every ring takes the same time and the only
variable is feed (inner = slow, outer = fast). Watch the center and STOP when a
ring stops falling free — the last ring that dropped is your fastest clean
single-pass feed. No calipers, no reading scorch: the part tells you.

Each ring opens with a smooth spiral lead-in that starts ~half a gap inside the
ring and joins the circle after exactly the diode's warmup window
(WARMUP_MS), so the circle is cut once at full power while the spiral itself
records the cold-start ramp — backlight it and read the angle where it starts
biting (1 degree = WARMUP_MS/join_ang ms). Two radials mark t=0 (cold) and
t=warm.

WCS origin is at the CENTER of the rings, so you can zero the machine roughly in
the middle of a scrap without measuring or squaring anything.

Invoked as `cnc.py cal-laser`. Outputs gcode + a toolpath PNG + a self-
explanatory KEY PNG to build/cal_laser/.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Fixed machine constant: this diode ramps to full optical power in ~1s from a
# cold start. Static M3 at 100% (weak diode under-fires on M4 dynamic).
WARMUP_MS = 1000.0

ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = ROOT / "build" / "cal_laser"


def _load_font(size: int) -> ImageFont.ImageFont:
    for path in (
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _render_warmup_key(radii, feeds, order, r_max, T):
    """Self-explanatory KEY figure: colored rings labeled with their feed, the
    smooth spiral warmup lead-ins, labeled t=0 / t=warm radials, and a 'how to
    read' panel. Designed to stand alone as a shareable image."""
    n = len(radii)
    r_min = radii[0]
    gap = (r_max - r_min) / max(1, n - 1) if n > 1 else r_min
    delta = gap / 2.0
    join_ang = 360.0 * (WARMUP_MS / 1000.0) / T
    ms_per_deg = WARMUP_MS / join_ang

    S = 22  # px/mm
    margin = 5.0  # mm of room for the outer radial + bottom feed labels
    title_h = 104
    diag = int(2 * (r_max + margin) * S)
    panelw = 470
    H = title_h + diag
    W = diag + panelw
    cpx = (r_max + margin) * S  # center offset in px

    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    palette = [
        (200, 40, 40),
        (210, 120, 20),
        (170, 160, 20),
        (40, 160, 60),
        (30, 120, 200),
        (120, 60, 190),
        (200, 40, 140),
        (90, 90, 90),
        (0, 150, 150),
        (150, 90, 30),
        (100, 100, 220),
        (60, 60, 60),
    ]

    def px(mx, my):
        return (cpx + mx * S, title_h + cpx - my * S)

    ft, fs, fb = _load_font(30), _load_font(18), _load_font(14)

    # ---- title band ----
    d.text((16, 14), "Concentric warmup + feed calibration", fill=(0, 0, 0), font=ft)
    d.text(
        (16, 54),
        f"one plate finds your max single-pass feed AND your cold-start ramp  "
        f"(loop {T:.0f}s/ring, {WARMUP_MS / 1000:.0f}s warmup, static M3 100%)",
        fill=(90, 90, 90),
        font=fb,
    )
    d.line([16, title_h - 6, W - 16, title_h - 6], fill=(210, 210, 210), width=1)

    # ---- radials: t=0 (cold start) and join_ang (warm / spiral joins) ----
    rout = r_max + 2
    for a, lab in (
        (0.0, "t=0  (cold start)"),
        (join_ang, f"t={WARMUP_MS / 1000:.0f}s (warm)"),
    ):
        c0 = px(0, 0)
        e = px(rout * math.cos(math.radians(a)), rout * math.sin(math.radians(a)))
        d.line([c0[0], c0[1], e[0], e[1]], fill=(130, 130, 130), width=1)
        tw = d.textlength(lab, font=fb)
        tx = min(e[0] + 4, diag - tw - 6)  # keep the label inside the diagram
        d.text((tx, e[1] - 18), lab, fill=(110, 110, 110), font=fb)

    # ---- rings + smooth spiral lead-ins, colored; feed labels at the bottom ----
    for k, i in enumerate(order):
        r, feed, col = radii[i], feeds[i], palette[k % len(palette)]
        x0, y0 = px(-r, r)
        x1, y1 = px(r, -r)
        d.ellipse([x0, y0, x1, y1], outline=col, width=3)
        # spiral lead-in: rs -> r over 0 -> join_ang (matches the gcode)
        rs = r - delta
        pts = []
        steps = max(24, int(round(join_ang / 3.0)))
        for j in range(steps + 1):
            a = join_ang * j / steps
            rr = rs + delta * j / steps
            pts.append(
                px(rr * math.cos(math.radians(a)), rr * math.sin(math.radians(a)))
            )
        d.line(pts, fill=col, width=2)
        # feed label at the ring's bottom, in the gap just below it
        lx, ly = px(0, -r)
        txt = f"{feed}"
        tw = d.textlength(txt, font=fb)
        d.text((lx - tw / 2, ly + 4), txt, fill=col, font=fb)

    # ---- right panel: how to read + legend ----
    lx = diag + 16
    yy = title_h + 6
    d.text((lx, yy), "HOW TO READ", fill=(0, 0, 0), font=fs)
    yy += 30
    how = [
        "Rings cut inner->outer, each ONE",
        "pass at its labeled feed (slow->fast).",
        "Feed = circumference / loop-time, so",
        "every ring takes the same time.",
        "",
        "Watch the center: STOP when a ring",
        "stops falling free. The last ring that",
        "dropped = your fastest clean",
        "single-pass feed.",
        "",
        "Each ring opens with a spiral lead-in",
        "from just inside, joining the circle",
        "exactly when the diode hits full power",
        f"({WARMUP_MS / 1000:.0f}s). Backlight it: the angle where",
        "the spiral starts biting = the warmup",
        f"ramp at that feed (1 deg = {ms_per_deg:.0f} ms).",
        "",
        "Radials mark t=0 and t=warm.",
    ]
    for ln in how:
        d.text((lx, yy), ln, fill=(50, 50, 50), font=fb)
        yy += 19
    yy += 8
    d.text((lx, yy), "feed (mm/min)  ->  radius", fill=(0, 0, 0), font=fs)
    yy += 28
    for k, i in enumerate(order):
        col = palette[k % len(palette)]
        d.rectangle([lx, yy, lx + 14, yy + 14], fill=col)
        d.text(
            (lx + 22, yy),
            f"#{k + 1}   {feeds[i]:>4}   r{radii[i]:.1f}mm",
            fill=(20, 20, 20),
            font=fb,
        )
        yy += 20

    p = BUILD_DIR / "cal_laser_key.png"
    img.save(p, "PNG", optimize=True)
    return p


def _render_gcode_png(lines, out_path, px_per_mm=14):
    """Render a gcode toolpath (G0/G1 lines + G2/G3 arcs) to a PNG. Cuts (laser
    on) are red, rapids (laser off) faint gray. Handles negative coords (center
    origin) by shifting to the bbox."""
    import re

    segs = []  # (x0, y0, x1, y1, is_cut)
    x = y = None
    laser = False
    for ln in lines:
        s = ln.strip()
        if s.startswith(("M3", "M4")):
            laser = True
        elif s.startswith("M5"):
            laser = False
        m = re.match(r"^(G[0123])\b", s)
        if not m:
            continue
        g = m.group(1)
        mx, my = re.search(r"X([-.\d]+)", s), re.search(r"Y([-.\d]+)", s)
        nx = float(mx.group(1)) if mx else x
        ny = float(my.group(1)) if my else y
        if x is not None and nx is not None:
            if g in ("G0", "G1"):
                segs.append((x, y, nx, ny, g == "G1" and laser))
            else:  # G2 (CW) / G3 (CCW) arc via I/J center offset
                mi, mj = re.search(r"I([-.\d]+)", s), re.search(r"J([-.\d]+)", s)
                ccx = x + (float(mi.group(1)) if mi else 0.0)
                ccy = y + (float(mj.group(1)) if mj else 0.0)
                rad = math.hypot(x - ccx, y - ccy)
                a0 = math.atan2(y - ccy, x - ccx)
                a1 = math.atan2(ny - ccy, nx - ccx)
                if g == "G2":  # CW
                    while a1 >= a0:
                        a1 -= 2 * math.pi
                else:  # CCW
                    while a1 <= a0:
                        a1 += 2 * math.pi
                steps = max(8, int(abs(a1 - a0) / (math.pi / 60)))
                prev = (x, y)
                for k in range(1, steps + 1):
                    aa = a0 + (a1 - a0) * k / steps
                    cur = (ccx + rad * math.cos(aa), ccy + rad * math.sin(aa))
                    segs.append((prev[0], prev[1], cur[0], cur[1], laser))
                    prev = cur
        x, y = nx, ny
    if not segs:
        return out_path
    xs = [c for s in segs for c in (s[0], s[2])]
    ys = [c for s in segs for c in (s[1], s[3])]
    minx, miny, maxx, maxy = min(xs), min(ys), max(xs), max(ys)
    S, pad = px_per_mm, 2
    W = int((maxx - minx + 2 * pad) * S)
    H = int((maxy - miny + 2 * pad) * S)
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)

    def to(px_, py_):
        return ((px_ - minx + pad) * S, H - (py_ - miny + pad) * S)

    for x0, y0, x1, y1, cut in segs:
        a, b = to(x0, y0), to(x1, y1)
        d.line(
            [a[0], a[1], b[0], b[1]],
            fill=(180, 30, 30) if cut else (215, 215, 215),
            width=2 if cut else 1,
        )
    img.save(out_path, "PNG", optimize=True)
    return out_path


def generate(circles, min_r, max_r, time_s, power_percent):
    """Build the concentric spiral cut-through calibration gcode + PNGs."""
    n = circles
    r_min, r_max = min_r, max_r
    T = time_s  # loop seconds per ring (EXCLUDING the WARMUP_MS warmup)
    power_s = int(round(power_percent * 10))
    radii = [r_min + (r_max - r_min) * i / max(1, n - 1) for i in range(n)]
    feeds = [int(round(120 * math.pi * r / T)) for r in radii]  # circumference/(T/60)
    gap = (r_max - r_min) / max(1, n - 1) if n > 1 else r_min  # inter-ring spacing
    delta = gap / 2.0  # spiral starts this far INSIDE the target ring (half the gap)
    join_ang = 360.0 * (WARMUP_MS / 1000.0) / T

    lines = [
        "; cut-through feed test — concentric rings, WCS origin at CENTER",
        f"; per ring: {WARMUP_MS:.0f}ms spiral warmup ({delta:.1f}mm inside, sweeping "
        f"{join_ang:.0f}deg) joins the circle, then one full-power loop.",
        f"; feed = circumference / {T:.1f}s. Cut order INNER (slow) -> OUTER (fast).",
        "; STOP when a ring stops falling free -> last clean = fastest single-pass feed.",
        "; spiral shows the warmup gradient (backlight, read the degrees where it first",
        "; cuts through). Rings are G2/G3 arcs; spiral is a smooth G1 lead-in. Use KEY png.",
        "; cut# (inner->outer): radius mm -> feed mm/min:",
    ]
    for j, (r, feed) in enumerate(zip(radii, feeds), 1):
        lines.append(f";   #{j}  r={r:.1f}mm  feed={feed}")
    lines += ["$32=1   ; GRBL laser mode", "G21", "G90", "M5", "G0 X0 Y0", ""]

    for r, feed in zip(radii, feeds):  # ascending radius = inner/slow first
        rs = r - delta  # spiral start radius (inside the target ring)
        lines += [
            f"; --- ring r={r:.1f}mm feed={feed} ---",
            f"G0 X{rs:.3f} Y0.000",  # spiral start: 3 o'clock, delta inside the ring
            f"M3 S{power_s}",
            f"F{feed}",
            f"; spiral warmup: {rs:.1f}->{r:.1f}mm over {join_ang:.0f}deg (~{WARMUP_MS:.0f}ms)",
        ]
        # Smooth Archimedean spiral lead-in: radius climbs linearly with angle
        # from (rs, 0deg) to (r, join_ang), sampled finely as short G1 moves.
        # This is the warmup zone (ramping power, in scrap) so segment stutter is
        # irrelevant here — smoothness wins.
        steps = max(24, int(round(join_ang / 3.0)))
        for j in range(1, steps + 1):
            a = join_ang * j / steps
            rr = rs + delta * j / steps
            lines.append(
                f"G1 X{rr * math.cos(math.radians(a)):.3f} "
                f"Y{rr * math.sin(math.radians(a)):.3f}"
            )
        # now joined the circle at (r, join_ang); cut one full loop ending there so
        # every point on the circle is cut exactly once at full power.
        jx, jy = (
            r * math.cos(math.radians(join_ang)),
            r * math.sin(math.radians(join_ang)),
        )
        lines += [
            "; full-power loop (ends exactly at the spiral join)",
            f"G3 X{-jx:.3f} Y{-jy:.3f} I{-jx:.3f} J{-jy:.3f}",  # half 1 CCW
            f"G3 X{jx:.3f} Y{jy:.3f} I{jx:.3f} J{jy:.3f}",  # half 2 CCW back to join
            "M5",
            "",
        ]

    # Two radial slices (from center out past the outer ring) to expose the ring
    # cross-sections at 0deg (common spiral start) and join_ang (where the spiral
    # meets the circle = start of the pure single-pass cut). Cut last (an early
    # stop skips them) at the slowest, most reliable feed.
    slow = min(feeds)
    rout = r_max + 2
    a2 = math.radians(join_ang)
    lines += [
        f"; --- radial cross-section slices at 0deg and {join_ang:.0f}deg (feed {slow}) ---",
        f"G0 X{rout:.3f} Y0.000",  # outer edge at 0deg (common spiral start)
        f"M3 S{power_s}",
        f"F{slow}",
        "G1 X0.000 Y0.000",  # radial in to center
        f"G1 X{rout * math.cos(a2):.3f} Y{rout * math.sin(a2):.3f}",  # out at join_ang
        "M5",
        "",
    ]
    lines += ["G0 X0 Y0", ""]
    return lines, radii, feeds, T


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="cnc.py cal-laser",
        description="Concentric spiral warmup + feed laser calibration (one disc "
        "finds your max single-pass feed and cold-start ramp).",
    )
    p.add_argument("--circles", type=int, default=12, help="number of rings")
    p.add_argument("--min-r", type=float, default=3.0, help="smallest ring radius mm")
    p.add_argument("--max-r", type=float, default=24.0, help="largest ring radius mm")
    p.add_argument(
        "--time-s",
        dest="time_s",
        type=float,
        default=3.0,
        help="loop seconds per ring, EXCLUDING the warmup (feed = circumference / "
        "time-s). 3s + r_min=3mm gives a ~377mm/min slowest ring",
    )
    p.add_argument("--power-percent", dest="power_percent", type=float, default=100.0)
    args = p.parse_args(argv)

    lines, radii, feeds, T = generate(
        args.circles, args.min_r, args.max_r, args.time_s, args.power_percent
    )
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    out = BUILD_DIR / "cal_laser.gcode"
    out.write_text("\n".join(lines))
    key_path = _render_warmup_key(radii, feeds, list(range(len(radii))), args.max_r, T)
    png_path = _render_gcode_png(lines, BUILD_DIR / "cal_laser.png")
    print(
        f"circles: {args.circles}  radii: {args.min_r}-{args.max_r}mm  "
        f"loop={T}s  feeds: {feeds}"
    )
    print("origin at CENTER; cut inner->outer, stop when a ring stops dropping free.")
    print(f"-> {out}")
    print(f"-> {png_path}  (toolpath)")
    print(f"-> {key_path}  (lookup key)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
