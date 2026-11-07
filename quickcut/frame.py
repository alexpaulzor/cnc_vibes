"""Frame pass for laser G-code: before anything else moves, rapid-trace the job's full extent with
the laser off.

If the job doesn't fit the machine (wrong zero, too big for the travel), the head hits an endstop
during this trace (hard limits: alarm), before the laser has ever fired, so the workpiece isn't
marked. It also shows you where the job will land.

    from gcode_frame import add_frame
    lines = add_frame(lines)          # list of G-code lines in, list out (unchanged if already framed)

The extent is every X/Y the file reaches (cuts, travel, park, arcs over their actual sweep),
in absolute coordinates. Files that switch to G91 (relative) are left alone, with a comment saying why.
The frame goes right before the first move, after the preamble's mode lines (G21/G90/M5...).
Part of quickcut (the single laser G-code core). gcode.write() applies it to every file.
"""
import math
import re

MARK = "; frame:"
_WORD = re.compile(r"([GMXYIJ])\s*(-?\d+(?:\.\d+)?)", re.I)


def _code(line):
    return line.split(";")[0].split("(")[0].strip().upper()


def job_extent(lines):
    """(x0, y0, x1, y1) of every absolute X/Y the file reaches, or None (no motion, or G91 in use)"""
    x = y = 0.0
    xs, ys = [], []
    motion = None
    for line in lines:
        c = _code(line)
        if not c:
            continue
        words = _WORD.findall(c)
        gs = [float(v) for k, v in words if k.upper() == "G"]
        if 91 in gs:
            return None
        for g in gs:
            if g in (0, 1, 2, 3):
                motion = int(g)
        vals = {k.upper(): float(v) for k, v in words if k.upper() in "XYIJ"}
        if "X" not in vals and "Y" not in vals:
            continue
        nx, ny = vals.get("X", x), vals.get("Y", y)
        if motion in (2, 3) and ("I" in vals or "J" in vals):
            ax, ay = _arc_extent(x, y, nx, ny, x + vals.get("I", 0.0), y + vals.get("J", 0.0), motion == 2)
            xs += ax
            ys += ay
        xs += [x, nx]
        ys += [y, ny]
        x, y = nx, ny
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _arc_extent(x0, y0, x1, y1, cx, cy, cw):
    """x and y values the arc actually reaches: its ends plus every axis crossing (0/90/180/270
    degrees) inside its sweep. (The whole circle would be wrong: a near-straight arc fitted to a
    gentle curve has a huge radius, and its circle runs far off the bed.)"""
    r = math.hypot(x0 - cx, y0 - cy)
    a0 = math.atan2(y0 - cy, x0 - cx)
    a1 = math.atan2(y1 - cy, x1 - cx)
    sweep = (a0 - a1) if cw else (a1 - a0)
    sweep %= 2 * math.pi
    if sweep < 1e-9:
        sweep = 2 * math.pi                     # same start and end: a full circle
    xs, ys = [x0, x1], [y0, y1]
    for k in range(4):
        q = k * math.pi / 2
        d = ((a0 - q) if cw else (q - a0)) % (2 * math.pi)
        if d <= sweep:
            xs.append(cx + r * math.cos(q))
            ys.append(cy + r * math.sin(q))
    return xs, ys


def frame_lines(ext, feed=None):
    x0, y0, x1, y1 = ext
    out = [f"{MARK} rapid trace of the job's extent, laser OFF: x {x0:.3f} to {x1:.3f}, y {y0:.3f} to {y1:.3f}",
           "; (an endstop or alarm here means the job won't fit from this zero: nothing has been marked yet)",
           "M5"]
    for px, py in ((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)):
        out.append(f"G0 X{px:.3f} Y{py:.3f}")
    return out


def add_frame(lines):
    """the same lines with the frame inserted before the first X/Y move (no-op if already framed)"""
    if isinstance(lines, str):
        return "\n".join(add_frame(lines.split("\n")))
    lines = list(lines)
    if any(l.startswith(MARK) for l in lines):
        return lines
    ext = job_extent(lines)
    if ext is None:
        return lines if not any(_code(l) for l in lines) else (
            ["; frame: none (relative G91 moves or no motion: extent unknown)"] + lines)
    for i, line in enumerate(lines):
        c = _code(line)
        if c and re.search(r"[XY]\s*-?\d", c) and re.match(r"G0?[0-3]\b|[XY]", c):
            return lines[:i] + frame_lines(ext) + [""] + lines[i:]
    return lines


def strip_frame(lines):
    """the same lines with any frame block removed (to re-frame a combined program)"""
    lines, out, skip = list(lines), [], 0
    for line in lines:
        if skip:
            skip -= 1
            continue
        if line.startswith(MARK):
            if "none (" in line:
                continue
            skip = 8                       # endstop note, M5, 5 x G0, blank
            continue
        out.append(line)
    return out
