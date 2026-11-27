"""Aluminium milling ops for a light router (the Anolex's 800 W trim router: relay on/off, speed on its dial).

What cam.py's generic ops don't do and thin aluminium on a weak router needs:
  - no plunges: every descent is a ramp along the path (profile laps spiral down, holes are helixes, slots zig-zag)
  - widened profile slot: the outline is roughed with two rings (a wide one, then one 1.0 mm in) so the cutter is
    never buried in a slot only its own width, then finished at size with a light radial pass
  - helical holes with the feed corrected to the cutter's EDGE (on a small helix the centre moves far slower than
    the edge: centre feed = edge feed x helix radius / hole radius)
  - frame pass at clearance height with the router OFF, before M3, so you see the job's box clear the clamps
  - relay spin-up dwell after M3; S is written (validator wants S > 0) but does nothing: set the DIAL

All moves in absolute mm, Z0 = top of the stock. Router jobs don't go through quickcut.render (its frame and park
are laser-only: they'd M5 the router mid-job); this module writes its own spindle-safe frame instead.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

from shapely.geometry import Polygon
from shapely.geometry.polygon import orient

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from cam import Material, Tool  # noqa: F401  (types)


@dataclass
class AluJob:
    tool: Tool
    rpm: int                      # what the DIAL is set to (manufacturer's figure for that step)
    chipload: float               # mm/tooth
    thickness: float              # stock, mm
    step_down: float              # Z per lap / per ramp, mm
    through: float = 0.2          # past the bottom of the stock, into the sacrificial board
    safe_z: float = 10.0          # travel height above the stock: clears the screw heads holding the parts
    clear_z: float = 15.0         # frame pass height (above clamps)
    leave: float = 0.15           # radial stock left by the roughing rings for the finish pass
    widen: float = 1.0            # the wide roughing ring sits this much further out
    spinup_s: float = 3.0

    @property
    def feed(self) -> int:
        return int(round(self.chipload * self.tool.flutes * self.rpm))

    @property
    def bottom(self) -> float:
        return -(self.thickness + self.through)


def approach(job_or_tool, z, feed) -> list[str]:
    """down to z (the stock top, where a ramp starts): rapid to 0.5 above it, then the last 0.5 at plunge feed"""
    tool = getattr(job_or_tool, "tool", job_or_tool)
    return [f"G0 Z{z + 0.5:.3f}", f"G1 Z{z:.3f} F{min(tool.max_plunge_mm_per_min, max(30, feed // 2))}"]


def f3(v: float) -> str:
    return f"{v:.3f}"


def header(job: AluJob, title: str, notes, ext) -> list[str]:
    """spindle-mode preamble, the frame (router off, at clear_z), then the router on + spin-up dwell"""
    x0, y0, x1, y1 = ext
    out = [f"; {title}", ";HEAD: spindle", f";TOOL: {job.tool.id}",
           f"; DIAL: set the router to {job.rpm} rpm BEFORE starting (S below does nothing: relay on/off only)",
           f"; feed {job.feed} mm/min = chipload {job.chipload:g} x {job.tool.flutes} flute(s) x {job.rpm} rpm; "
           f"step down {job.step_down:g}/lap; through to Z{f3(job.bottom)}",
           "; Z0 = TOP of the stock, X0 Y0 = its front-left corner; air on + lube nozzle aimed at the cutter",
           *[f"; {n}" for n in notes], "",
           "$32=0 ; spindle mode (not laser)", "G21", "G90", "G94", "M5",
           f"G0 Z{f3(job.clear_z)}",
           f"; frame: router OFF at Z{f3(job.clear_z)}, the job's box x {f3(x0)}..{f3(x1)}, y {f3(y0)}..{f3(y1)}: "
           "watch it clear every clamp",
           *[f"G0 X{f3(px)} Y{f3(py)}" for px, py in ((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0))],
           f"M3 S{job.rpm}", f"G4 P{job.spinup_s:g} ; relay spin-up", f"G0 Z{f3(job.safe_z)}", ""]
    return out


def footer(job: AluJob, park=(0.0, None)) -> list[str]:
    out = ["", f"G0 Z{f3(job.clear_z)}", "M5"]
    if park[1] is not None:
        out.append(f"G0 X{f3(park[0])} Y{f3(park[1])}")
    return out + ["M2", ""]


def _resample(coords, start_len=0.0):
    """closed ring -> [(x, y, cumulative length)] starting at its first point"""
    pts = list(coords)
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    out, s = [(pts[0][0], pts[0][1], 0.0)], 0.0
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        s += math.hypot(bx - ax, by - ay)
        out.append((bx, by, s))
    return out


def spiral_ring(ring_coords, z_top, z_bottom, step_down, feed, finish_lap=True, tool=None) -> list[str]:
    """follow a closed ring lap after lap, Z falling linearly along the path by step_down per lap from z_top to
    z_bottom, then one flat lap at z_bottom. No plunge: the cutter enters the stock on a ramp."""
    pts = _resample(ring_coords)
    L = pts[-1][2]
    laps = max(1, math.ceil((z_top - z_bottom) / step_down - 1e-9))
    dz = (z_top - z_bottom) / laps
    x0, y0, _ = pts[0]
    out = [f"G0 X{f3(x0)} Y{f3(y0)}"] + approach(tool, z_top, feed)
    for k in range(laps):
        for x, y, s in pts[1:]:
            out.append(f"G1 X{f3(x)} Y{f3(y)} Z{f3(z_top - dz * (k + s / L))} F{feed}")
    if finish_lap:
        out += [f"G1 X{f3(x)} Y{f3(y)} F{feed}" for x, y, s in pts[1:]]
    return out


def profile_outside(job: AluJob, part: Polygon, label="outline") -> list[str]:
    """cut the part free along its outside edge: wide roughing ring, inner roughing ring (both leaving job.leave),
    then the finish ring at size; climb milling (clockwise round the part for an M3 router)"""
    r = job.tool.radius_mm
    out = []
    rings = [(r + job.leave + job.widen, job.step_down, f"rough, wide (+{job.widen:g})"),
             (r + job.leave, job.step_down, f"rough, leaves {job.leave:g}"),
             (r, max(job.step_down, (job.thickness + job.through) / 2), "finish, at size")]
    for off, sd, what in rings:
        ring = orient(Polygon(part.exterior).buffer(off, 64, join_style=1), sign=-1.0).exterior
        out += ["", f"; {label}: {what}, offset {off:.3f}, {sd:g}/lap", f"G0 Z{f3(job.safe_z)}"]
        out += spiral_ring(list(ring.coords), 0.0, job.bottom, sd, job.feed, tool=job.tool)
        out.append(f"G0 Z{f3(job.safe_z)}")
    return out


def helical_hole(job: AluJob, c, d, label="hole") -> list[str]:
    """mill a through hole of diameter d (> tool) on a helix, step_down per turn, a flat turn at the bottom, then a
    second flat turn; centre feed slowed so the cutter's edge sees the job's feed"""
    r = job.tool.radius_mm
    rh = d / 2 - r
    if rh <= 0.05:
        raise ValueError(f"hole {d} too small for a {job.tool.diameter_mm} mm cutter to helix")
    feed = max(30, int(round(job.feed * rh / (d / 2))))
    cx, cy = c
    turns = max(1, math.ceil(-job.bottom / job.step_down - 1e-9))
    dz = -job.bottom / turns
    out = ["", f"; {label}: d {d:g} at ({f3(cx)}, {f3(cy)}), helix r {rh:.3f}, {turns} turns of {dz:.3f}, "
               f"centre feed {feed} (edge {job.feed})",
           f"G0 Z{f3(job.safe_z)}", f"G0 X{f3(cx + rh)} Y{f3(cy)}"] + approach(job, 0.0, feed)
    for k in range(turns):          # climb: counter-clockwise inside a hole for an M3 router
        out.append(f"G3 X{f3(cx + rh)} Y{f3(cy)} I{f3(-rh)} J0.000 Z{f3(-dz * (k + 1))} F{feed}")
    out += [f"G3 X{f3(cx + rh)} Y{f3(cy)} I{f3(-rh)} J0.000 F{feed}"] * 2
    out += [f"G1 X{f3(cx)} Y{f3(cy)} F{feed}", f"G0 Z{f3(job.safe_z)}"]
    return out


def ramp_slot(job: AluJob, a, b, depth, feed) -> list[str]:
    """a straight slot the cutter's width from a to b, depth deep: zig-zag ramps of step_down per traverse, then a
    flat pass back"""
    trav = max(1, math.ceil(depth / job.step_down - 1e-9))
    dz = depth / trav
    out = [f"G0 Z{f3(job.safe_z)}", f"G0 X{f3(a[0])} Y{f3(a[1])}"] + approach(job, 0.0, feed)
    ends = (b, a)
    for k in range(trav):
        p = ends[k % 2]
        out.append(f"G1 X{f3(p[0])} Y{f3(p[1])} Z{f3(-dz * (k + 1))} F{feed}")
    p = ends[trav % 2]
    out += [f"G1 X{f3(p[0])} Y{f3(p[1])} F{feed}", f"G0 Z{f3(job.safe_z)}"]
    return out


def dimples(job: AluJob, pts, depth=0.2, feed=60) -> list[str]:
    """tiny counting marks: the cutter touched down depth at each point"""
    out = []
    for x, y in pts:
        out += [f"G0 X{f3(x)} Y{f3(y)}", "G0 Z0.500", f"G1 Z{f3(-depth)} F{min(feed, job.tool.max_plunge_mm_per_min)}", f"G0 Z{f3(job.safe_z)}"]
    return out


def extent(lines):
    """(x0, y0, x1, y1) of every X/Y the cutting part of the program reaches (arcs: their full circle, safe side)"""
    import re
    xs, ys = [], []
    x = y = 0.0
    for ln in lines:
        c = ln.split(";")[0].strip()
        if not c.startswith(("G0", "G1", "G2", "G3")):
            continue
        v = dict((k, float(n)) for k, n in re.findall(r"([XYIJ])(-?[0-9.]+)", c))
        if c.startswith(("G2", "G3")):
            cx, cy = x + v.get("I", 0), y + v.get("J", 0)
            rr = math.hypot(v.get("I", 0), v.get("J", 0))
            xs += [cx - rr, cx + rr]
            ys += [cy - rr, cy + rr]
        x, y = v.get("X", x), v.get("Y", y)
        if "X" in v or "Y" in v:
            xs.append(x)
            ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


def program(job: AluJob, title: str, notes, body, park_y=None) -> list[str]:
    ext = extent(body)
    return header(job, title, notes, ext) + body + footer(job, (0.0, ext[3] if park_y is None else park_y))


def holes_then_outline(job: AluJob, title: str, parts, notes=()):
    """two programs for parts held by their own holes: [holes] mills every hole through; you then screw each part
    down through its holes into the sacrificial board; [outline] cuts the parts free round the screws.
    parts = [(name, Polygon with its holes as interiors)]. Returns {"holes": lines, "outline": lines}."""
    hb, ob = [], []
    for name, g in parts:
        for i, h in enumerate(g.interiors):
            hp = Polygon(h)
            d = 2 * math.sqrt(hp.area / math.pi)
            hb += helical_hole(job, (hp.centroid.x, hp.centroid.y), round(d, 3), f"{name} hole {i + 1}")
        ob += profile_outside(job, Polygon(g.exterior), f"{name} outline")
    full = extent(ob)
    return {"holes": program(job, f"{title} -- 1 of 2: HOLES", list(notes) +
                             ["after this: screw each part down through its holes (pan-head wood screws + washers into "
                              "the sacrificial board), keep the edge clamps, run 2 of 2"], hb, park_y=full[3]),
            "outline": program(job, f"{title} -- 2 of 2: OUTLINE (parts screwed down through their holes)",
                               list(notes), ob)}


def write(out_dir, base, material_id, lines):
    """<base>_<W>x<H>_<material>.gcode, W x H = the cut's extent rounded up (as quickcut names laser files)"""
    from pathlib import Path
    x0, y0, x1, y1 = extent([l for l in lines if not l.startswith(("G0 X", "; frame"))] or lines)
    p = Path(out_dir) / f"{base}_{math.ceil(x1 - 1e-9)}x{math.ceil(y1 - 1e-9)}_{material_id}.gcode"
    p.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines)
    long = [i for i, l in enumerate(lines, 1) if len(l) >= 255]
    if long:
        raise ValueError(f"lines of 255+ characters end a Grbl_ESP32 SD job silently: {long[:5]}")
    p.write_text(text + "\n")
    return p
