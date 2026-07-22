#!/usr/bin/env python3
"""Elliptical spiral warmup + feed + PASS-COUNT laser calibration — flagship.

One small ELLIPTICAL plate maps two variables at once for a weak (~10W) diode:

  * FEED  — concentric rings, inner=slow -> outer=fast. Each ring's feed is
            perimeter / loop-time, so every ring takes the same time and feed is
            the only variable radially.
  * PASSES — each ring is split into equal-angle SECTORS, and the head ping-pongs
            so each sector ends up cut a DIFFERENT number of times (1..N). Read
            which (feed x pass) sectors cut clean.

Why an ellipse: after the nested rings pop out and jumble, the major/minor axis
tells you how each piece was oriented. Digits are engraved (not cut) on the plate
— feed per ring, pass-count per sector — and a full KEY png is produced to tape
to the machine.

Each ring opens with a spiral lead-in that joins the ellipse after exactly the
diode warmup window (WARMUP_MS), so the circle is cut at full power while the
spiral itself records the cold-start ramp.

Machine constants: static M3 @ 100% (M4 dynamic under-fires this diode), origin
at the CENTER of the plate. Passes ping-pong (forward then reverse over the same
arc) so a later pass never repositions with the beam crossing the part.

Invoked as `calibrate.py cal-laser`. Outputs gcode + toolpath PNG + KEY PNG to
build/cal_laser/.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# This diode ramps to full optical power in ~1s cold. Static M3 at 100%.
WARMUP_MS = 1000.0
# Label engraving (surface mark, NOT a cut). Overridable via CLI.
ENGRAVE_POWER_PCT = 15.0
ENGRAVE_FEED = 3000

DIR = Path(__file__).resolve().parent
BUILD_DIR = DIR / "build" / "cal_laser"


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def ellipse_perimeter(a: float, b: float) -> float:
    """Ramanujan's approximation of an ellipse perimeter (semi-axes a, b)."""
    h = ((a - b) / (a + b)) ** 2 if (a + b) else 0.0
    return math.pi * (a + b) * (1 + 3 * h / (10 + math.sqrt(4 - 3 * h)))


def ellipse_pt(a: float, b: float, theta_deg: float) -> tuple[float, float]:
    t = math.radians(theta_deg)
    return (a * math.cos(t), b * math.sin(t))


def sector_pass_counts(n_pass: int) -> list[int]:
    """Per-sector pass counts (sector 0 begins at the warmup-join boundary, going
    CCW). A base loop (everyone=1) plus a continuous ping-pong of sweeps
    n-1, n-2, ..., 1 sectors yields a distinct permutation of 1..n_pass. E.g.
    n=3 -> [2,3,1]; n=4 -> [2,4,3,1]; n=5 -> [2,4,5,3,1]."""
    counts = [1] * n_pass
    pos, direction = 0, 1
    for length in range(n_pass - 1, 0, -1):
        for _ in range(length):
            if direction > 0:
                counts[pos % n_pass] += 1
                pos += 1
            else:
                pos -= 1
                counts[pos % n_pass] += 1
        direction = -direction
    return counts


# ---------------------------------------------------------------------------
# GCode emit helpers
# ---------------------------------------------------------------------------
def _emit_ellipse_g1(lines, a, b, th0, th1, step_deg=3.0):
    """G1-sample the ellipse arc (semi-axes a,b) from th0 to th1 degrees (signed).
    Returns the ending angle. Caller must already be at ellipse_pt(a,b,th0)."""
    n = max(1, int(round(abs(th1 - th0) / step_deg)))
    for i in range(1, n + 1):
        x, y = ellipse_pt(a, b, th0 + (th1 - th0) * i / n)
        lines.append(f"G1 X{x:.3f} Y{y:.3f}")
    return th1


def _slash_strokes(ox, oy, h):
    """A '/' glyph: one diagonal stroke across the digit box (W=h/2)."""
    w = h / 2
    return [(ox, oy, ox + w, oy + h)]


def _label_strokes(text, cx, cy, h, spacing_frac=0.3):
    """Strokes for `text` (digits + '/'), centered horizontally on cx with vertical
    center at cy. Returns [(x1,y1,x2,y2), ...]."""
    import font_7seg

    w = h / 2
    spacing = spacing_frac * h
    pitch = w + spacing
    total_w = len(text) * w + (len(text) - 1) * spacing
    x0 = cx - total_w / 2
    y0 = cy - h / 2
    out = []
    for i, ch in enumerate(text):
        ox = x0 + i * pitch
        if ch == "/":
            out += _slash_strokes(ox, y0, h)
        elif ch.isdigit():
            out += font_7seg.render_digit(ch, ox, y0, h)
        # any other char: leave its pitch as a gap
    return out


def _order_strokes(strokes, start=(0.0, 0.0)):
    """Greedy nearest-endpoint ordering (flipping strokes as needed) to minimize
    the connector 'drag' distance of a single continuous engraving path."""
    remaining = list(strokes)
    pen = start
    ordered = []
    while remaining:
        best_i, best_flip, best_d = 0, False, float("inf")
        for i, (x1, y1, x2, y2) in enumerate(remaining):
            d1 = (x1 - pen[0]) ** 2 + (y1 - pen[1]) ** 2
            d2 = (x2 - pen[0]) ** 2 + (y2 - pen[1]) ** 2
            if d1 < best_d:
                best_d, best_i, best_flip = d1, i, False
            if d2 < best_d:
                best_d, best_i, best_flip = d2, i, True
        x1, y1, x2, y2 = remaining.pop(best_i)
        if best_flip:
            x1, y1, x2, y2 = x2, y2, x1, y1
        ordered.append((x1, y1, x2, y2))
        pen = (x2, y2)
    return ordered


def _emit_engrave_boustrophedon(lines, strokes, engrave_s, engrave_feed, label=""):
    """Engrave every stroke as ONE continuous laser-on path, then retrace it in
    reverse. The beam never blanks (glyph-to-glyph connectors are drawn as faint
    drag lines), so the diode warms up once; the cold first-second of the forward
    pass is redrawn LAST in the reverse pass, when the beam is fully warm."""
    if not strokes:
        return
    ordered = _order_strokes(strokes)
    pts = [(ordered[0][0], ordered[0][1])]
    for x1, y1, x2, y2 in ordered:
        if (x1, y1) != pts[-1]:
            pts.append((x1, y1))  # connector drag line into this stroke
        pts.append((x2, y2))
    if label:
        lines.append(f"; engrave: {label} (continuous fwd+reverse; warms once)")
    lines.append(f"G0 X{pts[0][0]:.3f} Y{pts[0][1]:.3f}")
    lines.append(f"M3 S{engrave_s}")
    lines.append(f"F{engrave_feed}")
    for x, y in pts[1:]:
        lines.append(f"G1 X{x:.3f} Y{y:.3f}")
    lines.append("; reverse pass: redraw the cold first-second while warm")
    for x, y in pts[-2::-1]:
        lines.append(f"G1 X{x:.3f} Y{y:.3f}")
    lines.append("M5")


# ---------------------------------------------------------------------------
# Generate
# ---------------------------------------------------------------------------
def generate(
    circles,
    min_r,
    max_r,
    time_s,
    power_percent,
    passes=(1,),
    aspect=1.0,
    engrave_power=ENGRAVE_POWER_PCT,
    engrave_feed=ENGRAVE_FEED,
):
    """Build the elliptical warmup + feed + pass-count calibration gcode.

    Returns (lines, radii, feeds, T, meta). `radii` are the semi-MINOR axes b_i.
    """
    n = circles
    N = max(1, len(passes))
    T = time_s
    power_s = int(round(power_percent * 10))
    engrave_s = int(round(engrave_power * 10))

    b = [min_r + (max_r - min_r) * i / max(1, n - 1) for i in range(n)]  # semi-minor
    a = [aspect * bi for bi in b]  # semi-major
    perim = [ellipse_perimeter(a[i], b[i]) for i in range(n)]
    feeds = [int(round(perim[i] / (T / 60.0))) for i in range(n)]
    gap = (max_r - min_r) / max(1, n - 1) if n > 1 else min_r  # semi-minor spacing
    delta = gap / 2.0  # spiral starts this far (in b) inside the ring
    join_ang = 360.0 * (WARMUP_MS / 1000.0) / T  # param-deg swept during warmup
    counts = sector_pass_counts(N)
    sec = 360.0 / N
    slow = min(feeds)
    aout = a[-1] + 2  # outer reach for radials / labels

    lines = [
        f"; elliptical feed x pass calibration (aspect {aspect:g}) — origin CENTER",
        f"; {n} rings, semi-minor {min_r}->{max_r}mm, aspect {aspect:g}; "
        f"feed = perimeter/{T:.1f}s (inner slow -> outer fast).",
        f"; passes tested: {list(range(1, N + 1))} as {N} equal-angle sectors; "
        f"per-sector pass counts (CCW from warmup join): {counts}.",
        f"; warmup {WARMUP_MS:.0f}ms spiral lead-in per ring; static M3 @ "
        f"{power_percent:g}%. Passes ping-pong (no beam-on repositioning).",
        "; ring# (inner->outer): semi-axes mm -> feed mm/min:",
    ]
    for i in range(n):
        lines.append(f";   #{i + 1}  a={a[i]:.1f} b={b[i]:.1f}mm  feed={feeds[i]}")
    lines += ["$32=1   ; GRBL laser mode", "G21", "G90", "M5", "G0 X0 Y0", ""]

    classic = N == 1 and abs(aspect - 1.0) < 1e-9

    # ---- ENGRAVE a {feed}/{passes} label on EVERY sector of EVERY ring, as one
    # continuous fwd+reverse low-power path (warms once; cold start redrawn last).
    if not classic:
        lab_h = min(2.0, max(1.3, gap * 0.6))
        strokes = []
        for i in range(n):
            r_b = b[i] + gap * 0.5  # band just OUTSIDE ring i (past its cut)
            r_a = aspect * r_b
            for k in range(N):
                th = join_ang + (k + 0.5) * sec  # sector mid, clear of the warmup arc
                px, py = ellipse_pt(r_a, r_b, th)
                strokes += _label_strokes(f"{feeds[i]}/{counts[k]}", px, py, lab_h)
        _emit_engrave_boustrophedon(
            lines, strokes, engrave_s, engrave_feed, "feed/passes on every sector"
        )
        lines.append("")

    # ---- CUT rings, inner (slow) -> outer (fast) ----
    for i in range(n):
        ai, bi, feed = a[i], b[i], feeds[i]
        scale0 = (bi - delta) / bi  # spiral starts delta (in b) inside the ring
        sx, sy = ellipse_pt(ai * scale0, bi * scale0, 0.0)
        lines += [
            f"; --- ring #{i + 1} a={ai:.1f} b={bi:.1f}mm feed={feed} ---",
            f"G0 X{sx:.3f} Y{sy:.3f}",
            f"M3 S{power_s}",
            f"F{feed}",
            f"; spiral warmup: joins the ellipse after {join_ang:.0f}deg (~{WARMUP_MS:.0f}ms)",
        ]
        # spiral lead-in: scale climbs scale0 -> 1 over 0 -> join_ang
        steps = max(24, int(round(join_ang / 3.0)))
        for j in range(1, steps + 1):
            s = scale0 + (1 - scale0) * j / steps
            x, y = ellipse_pt(ai * s, bi * s, join_ang * j / steps)
            lines.append(f"G1 X{x:.3f} Y{y:.3f}")
        ang = join_ang  # on the ellipse at sector-boundary 0

        if classic:
            ang = _emit_ellipse_g1(lines, ai, bi, ang, ang + 360.0)
            lines += ["M5", ""]
            continue

        lines.append(f"; base loop: pass 1 over all {N} sectors")
        ang = _emit_ellipse_g1(lines, ai, bi, ang, ang + 360.0)
        direction = 1
        for length in range(N - 1, 0, -1):
            d = "forward" if direction > 0 else "reverse"
            lines.append(f"; +1 pass over {length} sector(s) ({d})")
            ang = _emit_ellipse_g1(lines, ai, bi, ang, ang + direction * length * sec)
            direction = -direction
        lines += ["M5", ""]

    # ---- RADIALS (cut LAST, slowest feed) ----
    if classic:
        # two warmup cross-section radials at 0deg and join_ang (original behavior)
        a2 = math.radians(join_ang)
        lines += [
            f"; --- radial cross-sections at 0deg and {join_ang:.0f}deg (feed {slow}) ---",
            f"G0 X{aout:.3f} Y0.000",
            f"M3 S{power_s}",
            f"F{slow}",
            "G1 X0.000 Y0.000",
            f"G1 X{aout * math.cos(a2):.3f} Y{aout * math.sin(a2):.3f}",
            "M5",
            "",
        ]
    else:
        # N pie-slice dividers at sector boundaries, innermost ring -> outermost
        # ring (NOT through the center). Each is a straight line through the origin
        # direction, so it crosses every ring's boundary point.
        lines.append(
            f"; --- {N} sector dividers (feed {slow}), innermost->outermost ring ---"
        )
        for k in range(N):
            th = join_ang + k * sec
            ix, iy = ellipse_pt(a[0], b[0], th)
            ox, oy = ellipse_pt(a[-1], b[-1], th)
            lines += [
                f"G0 X{ix:.3f} Y{iy:.3f}",
                f"M3 S{power_s}",
                f"F{slow}",
                f"G1 X{ox:.3f} Y{oy:.3f}",
                "M5",
            ]
        lines.append("")

    lines += ["G0 X0 Y0", ""]
    meta = {
        "a": a,
        "b": b,
        "aspect": aspect,
        "N": N,
        "counts": counts,
        "join_ang": join_ang,
        "sec": sec,
        "classic": classic,
    }
    return lines, b, feeds, T, meta


# ---------------------------------------------------------------------------
# PNG renders
# ---------------------------------------------------------------------------
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


def _render_gcode_png(lines, out_path, px_per_mm=14):
    """Render a gcode toolpath to a PNG. Cuts (laser on) red, engrave marks orange,
    rapids faint gray. Handles negative (center-origin) coords."""
    import re

    segs = []  # (x0,y0,x1,y1, kind) kind: 'cut'|'engrave'|'rapid'
    x = y = None
    power = 0
    laser = False
    for ln in lines:
        s = ln.strip()
        mp = re.match(r"^M3 S(\d+)", s)
        if mp:
            power = int(mp.group(1))
            laser = True
            continue
        if s.startswith(("M3", "M4")):
            laser = True
            continue
        if s.startswith("M5"):
            laser = False
            continue
        m = re.match(r"^(G[0123])\b", s)
        if not m:
            continue
        g = m.group(1)
        mx, my = re.search(r"X([-.\d]+)", s), re.search(r"Y([-.\d]+)", s)
        nx = float(mx.group(1)) if mx else x
        ny = float(my.group(1)) if my else y
        if x is not None and nx is not None:
            on = laser and g == "G1"
            kind = ("cut" if power >= 900 else "engrave") if on else "rapid"
            if g in ("G0", "G1"):
                segs.append((x, y, nx, ny, kind))
            else:
                mi, mj = re.search(r"I([-.\d]+)", s), re.search(r"J([-.\d]+)", s)
                ccx = x + (float(mi.group(1)) if mi else 0.0)
                ccy = y + (float(mj.group(1)) if mj else 0.0)
                rad = math.hypot(x - ccx, y - ccy)
                a0 = math.atan2(y - ccy, x - ccx)
                a1 = math.atan2(ny - ccy, nx - ccx)
                if g == "G2":
                    while a1 >= a0:
                        a1 -= 2 * math.pi
                else:
                    while a1 <= a0:
                        a1 += 2 * math.pi
                steps = max(8, int(abs(a1 - a0) / (math.pi / 60)))
                prev = (x, y)
                for k in range(1, steps + 1):
                    aa = a0 + (a1 - a0) * k / steps
                    cur = (ccx + rad * math.cos(aa), ccy + rad * math.sin(aa))
                    segs.append((prev[0], prev[1], cur[0], cur[1], kind))
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

    style = {
        "cut": ((180, 30, 30), 2),
        "engrave": ((230, 140, 20), 1),
        "rapid": ((222, 222, 222), 1),
    }
    for x0, y0, x1, y1, kind in segs:
        c, wdt = style[kind]
        p, q = to(x0, y0), to(x1, y1)
        d.line([p[0], p[1], q[0], q[1]], fill=c, width=wdt)
    img.save(out_path, "PNG", optimize=True)
    return out_path


def _render_key(b, feeds, T, meta):
    """Self-explanatory KEY figure: the elliptical rings colored by feed, sector
    dividers with pass counts, per-ring feed labels, and a how-to panel."""
    a = meta["a"]
    N = meta["N"]
    counts = meta["counts"]
    join_ang = meta["join_ang"]
    sec = meta["sec"]
    n = len(b)
    a_max, b_max = a[-1], b[-1]

    S = 20
    margin = 6.0
    title_h = 108
    diagw = int(2 * (a_max + margin) * S)
    diagh = int(2 * (b_max + margin) * S)
    panelw = 430
    W = diagw + panelw
    H = title_h + diagh
    cx = (a_max + margin) * S
    cy = title_h + (b_max + margin) * S

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
        (0, 150, 150),
        (150, 90, 30),
        (90, 90, 90),
        (100, 100, 220),
        (60, 60, 60),
    ]
    ft, fs, fb = _load_font(30), _load_font(18), _load_font(13)

    def px(mx, my):
        return (cx + mx * S, cy - my * S)

    # title
    d.text((16, 12), "Elliptical feed × pass calibration", fill=(0, 0, 0), font=ft)
    d.text(
        (16, 52),
        f"one plate: rings=feed (inner slow→outer fast), sectors=passes  "
        f"(loop {T:.0f}s/ring, {WARMUP_MS / 1000:.0f}s warmup, static M3 100%)",
        fill=(90, 90, 90),
        font=fb,
    )
    d.line([16, title_h - 6, W - 16, title_h - 6], fill=(210, 210, 210), width=1)

    # sector dividers + pass-count labels
    for k in range(N):
        th = join_ang + k * sec
        i0, i1 = px(*ellipse_pt(a[0], b[0], th)), px(*ellipse_pt(a_max, b_max, th))
        d.line([i0[0], i0[1], i1[0], i1[1]], fill=(150, 150, 150), width=1)
        thm = join_ang + (k + 0.5) * sec
        lx, ly = px(*ellipse_pt(a_max * 1.06, b_max * 1.06, thm))
        d.text((lx - 6, ly - 10), f"{counts[k]}×", fill=(20, 20, 20), font=fs)

    # rings + feed labels
    for i in range(n):
        col = palette[i % len(palette)]
        x0, y0 = px(-a[i], b[i])
        x1, y1 = px(a[i], -b[i])
        d.ellipse([x0, y0, x1, y1], outline=col, width=2)
        lx, ly = px(0, b[i] - (b[1] - b[0]) * 0.5 if n > 1 else 0)
        txt = f"{feeds[i]}"
        tw = d.textlength(txt, font=fb)
        d.text((lx - tw / 2, ly - 7), txt, fill=col, font=fb)

    # panel
    lx = diagw + 16
    yy = title_h + 6
    d.text((lx, yy), "HOW TO READ", fill=(0, 0, 0), font=fs)
    yy += 28
    how = [
        "Rings = feed (mm/min), cut inner->outer,",
        "each the same loop-time so feed is the",
        "only variable. Labels sit inside each ring.",
        "",
        f"Each ring is split into {N} sectors, cut a",
        "different number of PASSES (the x number",
        "at the rim). The head ping-pongs so each",
        "sector gets 1..N passes with no beam-on",
        "repositioning.",
        "",
        "Read: for each ring (feed) note which",
        "sectors (passes) cut clean through. The",
        "slowest feed that cuts at your chosen",
        "pass-count is your setting.",
        "",
        "The plate is an ELLIPSE so the jumbled",
        "nested pieces can be re-oriented (major",
        "axis = the wide way). Digits are engraved",
        "on the plate too.",
        "",
        "Spiral lead-ins record the warmup ramp;",
        "sector dividers are cut last at the",
        "slowest feed.",
    ]
    for line in how:
        d.text((lx, yy), line, fill=(50, 50, 50), font=fb)
        yy += 18
    yy += 8
    d.text((lx, yy), "feed (mm/min) -> ring", fill=(0, 0, 0), font=fs)
    yy += 26
    for i in range(n):
        col = palette[i % len(palette)]
        d.rectangle([lx, yy, lx + 14, yy + 14], fill=col)
        d.text(
            (lx + 22, yy),
            f"#{i + 1}   {feeds[i]:>4}   a{a[i]:.1f} b{b[i]:.1f}mm",
            fill=(20, 20, 20),
            font=fb,
        )
        yy += 19

    p = BUILD_DIR / "cal_laser_key.png"
    img.save(p, "PNG", optimize=True)
    return p


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_passes(s: str) -> list[int]:
    """Parse '1,2,3' -> [1,2,3]. The plate tests pass counts 1..N as N sectors, so
    the values must be exactly the contiguous run 1..N (any order); reject
    anything else rather than silently testing 1..N regardless."""
    vals = [int(x) for x in str(s).replace(" ", "").split(",") if x != ""]
    if not vals:
        return [1]
    if sorted(vals) != list(range(1, len(vals) + 1)):
        raise argparse.ArgumentTypeError(
            f"--passes must be the contiguous run 1..N (e.g. '1,2,3'); got {vals}. "
            "Each ring is split into N sectors cut 1..N times; arbitrary values "
            "aren't representable."
        )
    return sorted(vals)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="calibrate.py cal-laser",
        description="Elliptical spiral warmup + feed + pass-count laser calibration.",
    )
    p.add_argument("--circles", type=int, default=10, help="number of rings")
    p.add_argument("--min-r", type=float, default=3.0, help="innermost semi-minor mm")
    p.add_argument("--max-r", type=float, default=30.0, help="outermost semi-minor mm")
    p.add_argument(
        "--time-s",
        dest="time_s",
        type=float,
        default=13.4,
        help="loop seconds per ring (feed = perimeter / time-s). Default targets "
        "~100-1000 mm/min for the default ellipse.",
    )
    p.add_argument("--power-percent", dest="power_percent", type=float, default=100.0)
    p.add_argument(
        "--passes",
        type=_parse_passes,
        default=[1],
        help="pass counts to test, as N equal-ANGLE sectors, e.g. '1,2,3' (must be "
        "the run 1..N). NOTE: sectors are equal parameter-angle, so on an ellipse "
        "their arc-lengths differ slightly. Default '1' = classic single-pass test.",
    )
    p.add_argument(
        "--aspect",
        type=float,
        default=1.0,
        help="ellipse major/minor ratio (1.0 = circle). ~1.35 recommended so "
        "jumbled pieces are re-orientable.",
    )
    p.add_argument(
        "--engrave-power",
        dest="engrave_power",
        type=float,
        default=ENGRAVE_POWER_PCT,
        help="label engrave power %%",
    )
    p.add_argument(
        "--engrave-feed",
        dest="engrave_feed",
        type=int,
        default=ENGRAVE_FEED,
        help="label engrave feed mm/min",
    )
    args = p.parse_args(argv)

    lines, b, feeds, T, meta = generate(
        args.circles,
        args.min_r,
        args.max_r,
        args.time_s,
        args.power_percent,
        passes=args.passes,
        aspect=args.aspect,
        engrave_power=args.engrave_power,
        engrave_feed=args.engrave_feed,
    )
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    out = BUILD_DIR / "cal_laser.gcode"
    out.write_text("\n".join(lines))
    key_path = _render_key(b, feeds, T, meta)
    png_path = _render_gcode_png(lines, BUILD_DIR / "cal_laser.png")
    print(
        f"circles: {args.circles}  semi-minor: {args.min_r}-{args.max_r}mm  "
        f"aspect: {args.aspect:g}  loop={T}s"
    )
    print(
        f"passes (sectors): {list(range(1, meta['N'] + 1))}  "
        f"per-sector counts: {meta['counts']}"
    )
    print(f"feeds mm/min: {feeds}")
    print(
        "origin at CENTER; cut inner->outer; read which (feed x pass) sectors "
        "cut clean."
    )
    print(f"-> {out}")
    print(f"-> {png_path}  (toolpath)")
    print(f"-> {key_path}  (lookup key — tape to the machine)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
