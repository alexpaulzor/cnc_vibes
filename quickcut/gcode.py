"""quickcut.gcode -- THE way laser G-code is made in this repo (and in scads/marbles).

Every generator builds its geometry however it likes, then emits through these functions and writes
with `write()`. That is where the shop rules live, once:

  * preamble: header comments (;HEAD: laser, ;MATERIAL:, ;LASER_MODE: static), $32=1, G21, G90, M5
  * cut(): static M3 at the recipe's power; a warmup wiggle (half the warmup distance out and back)
    on every path; recipe passes (open paths ping-pong, loops repeat); closed loops overlap the
    warmup distance past their start; M5 after every cut (never a G0 with the beam on)
  * mode: "static" (M3, the default: constant power, what a weak diode needs) or "dynamic" (M4)
  * etch(): a light score per stroke: warmup = the recipe's fixed lead-in, else the linear model
    (the cut warmup scaled by power); loops follow through past the start (laps until warm), open
    strokes ping-pong over themselves first (one out-and-back on a short tick); optional G2/G3 arc
    fitting or smoothing (arcfit.py) through moves()
  * raster(): M4 dynamic image engraving, S0 on every travel
  * bridged() / bridge_chains(): cut paths with uncut bridges (knock-outs, letter counters)
  * order(): middle-out (contents before their container) with short travel (order.py);
    nearest_chains / nearest_safe / fuse_touching for open chains
  * combine(): several programs as one (one preamble, one frame over the union, one park)
  * motion_report(): grbl_model's predicted speed / energy summary as header comments
  * write(): adds the frame pass (frame.py: rapid laser-off trace of the job's extent before the
    first move) and the park (laser off, X0 at the highest cut Y), refuses lines >= 255 characters
    (Grbl_ESP32's SD reader ends the job silently there), runs gcode_validate, and only then writes.

    import sys; sys.path.insert(0, "<cnc_vibes>/quickcut")
    import gcode
    job = gcode.job("mdf3")
    lines = gcode.preamble(job, title="my part", notes=["X0 Y0 = bottom-left"])
    for i, pl in enumerate(gcode.order(cuts), 1):
        lines += gcode.cut(pl, job, i, len(cuts))
    gcode.write(path, lines)          # frame + park + checks; raises GcodeError on any violation
"""
from __future__ import annotations

import math
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

from frame import MARK as FRAME_MARK, add_frame, strip_frame  # noqa: E402
from materials import SHARED_MATERIALS  # noqa: E402
from motion import collapse_shuttles, decimate, follow_through, path_length, warmup_wiggle  # noqa: E402,F401
import order as _order  # noqa: E402
from order import nearest_chains, nearest_safe  # noqa: E402,F401
from paths import CutIndex, bridge_chains, fuse_touching, trim_recut_ends  # noqa: E402,F401

DEFAULT_WARMUP_MS = 1000.0     # the diode's cold-start ramp to full optical power
MAX_LINE = 255                 # Grbl_ESP32 SDCard.cpp readFileLine(fileLine, 255)
PARK_MARK = "; park:"


class GcodeError(ValueError):
    pass


# ---------------------------------------------------------------- data model
@dataclass
class Polyline:
    """A single cut path: (x, y) points, closed loop or open path."""
    points: list
    closed: bool


@dataclass
class CutJob:
    """The recipe (not geometry) for one kind of cut."""
    feed_mm_per_min: int
    power_percent: float
    passes: int
    warmup_ms: float = DEFAULT_WARMUP_MS
    min_segment_mm: float = 0.3
    material_id: str = "custom"
    s_full_power: int = 1000          # GRBL $30 (S1000 = full power on the AnoleX)
    lead_in_mm: float | None = None   # etch: fixed lead-in instead of the timed warmup
    mode: str = "static"              # "static" = M3 (constant power), "dynamic" = M4 (scales with speed)

    @property
    def power_s(self) -> int:
        return round(self.power_percent / 100.0 * self.s_full_power)

    @property
    def on(self) -> str:
        return "M3" if self.mode == "static" else "M4"

    @property
    def etch_lead_mm(self) -> float:
        """etch warmup distance: the fixed lead-in, else the linear model (the cut warmup time
        scaled by power, at this feed): a 25 % score warms up for a quarter of a cut's time"""
        if self.lead_in_mm is not None:
            return float(self.lead_in_mm)
        return max(0.0, self.warmup_ms * self.power_percent / 100.0) / 60_000.0 * self.feed_mm_per_min

    @property
    def warmup_mm(self) -> float:
        if self.lead_in_mm is not None:
            return 2 * self.lead_in_mm
        return self.warmup_ms / 60_000.0 * self.feed_mm_per_min


# ---------------------------------------------------------------- materials
_MATS = None


def materials() -> dict:
    """{id: full record} from material_profiles/laser_materials.yaml"""
    global _MATS
    if _MATS is None:
        import yaml
        _MATS = {m["id"]: m for m in yaml.safe_load(Path(SHARED_MATERIALS).read_text())}
    return _MATS


def material(material_id: str) -> dict:
    m = materials().get(material_id)
    if m is None:
        raise GcodeError(f"unknown material {material_id!r} (known: {', '.join(sorted(materials()))})")
    return m


def fit(material_id: str):
    """(thickness_mm, kerf_mm, slot_extra_mm); kerf 0.10 / extra 0 when the profile has no fit block"""
    m = material(material_id)
    f = m.get("fit") or {}
    return float(m["thickness_mm"]), float(f.get("kerf_mm", 0.10)), float(f.get("slot_extra_mm", 0.0))


def job(material_id: str, recipe: str = "laser", s_full_power: int = 1000, **override) -> CutJob:
    """CutJob from a material's `laser` (cut) or `etch` recipe; keyword overrides win"""
    m = material(material_id)
    r = m.get(recipe)
    if r is None:
        raise GcodeError(f"material {material_id!r} has no {recipe!r} recipe")
    kw = dict(feed_mm_per_min=r["feed_mm_per_min"], power_percent=r["power_percent"],
              passes=r.get("passes", 1), material_id=material_id, s_full_power=s_full_power,
              mode=r.get("mode", "static"))
    if recipe == "etch":
        kw["lead_in_mm"] = r.get("lead_in_mm")     # None: the linear warmup model
    kw.update(override)
    return CutJob(**kw)


# ---------------------------------------------------------------- emission
def preamble(j: CutJob, title: str = "", notes=(), etch: CutJob | None = None) -> list[str]:
    out = [f"; {title}"] if title else []
    out += [f"; {n}" for n in notes]
    out += [
        f"; quickcut: GRBL diode-laser {('etch + ' if etch else '')}cut, {j.mode} {j.on}, warmup on every path",
        f"; cut: {j.material_id} {j.power_percent:g}% (S{j.power_s} of {j.s_full_power}) "
        f"F{j.feed_mm_per_min} x{j.passes}; warmup {j.warmup_ms:.0f}ms = {j.warmup_mm:.1f}mm",
    ]
    if etch:
        out.append(f"; etch: {etch.power_percent:g}% F{etch.feed_mm_per_min} {etch.on}, {etch.etch_lead_mm:.2f}mm warmup")
    out += [
        "; every cut: warmup wiggle (half the warmup out and back), then the passes;",
        "; closed loops overlap the warmup distance past their start; middle-out order",
        ";",
        ";HEAD: laser",
        f";MATERIAL: {j.material_id}",
        (";LASER_MODE: static  (M3, constant power; assumes Z already at focus)" if j.mode == "static" else
         ";LASER_MODE: dynamic  (M4, power scales with speed; assumes Z already at focus)"),
        "",
        f"$32=1   ; GRBL laser mode (assumes firmware $30={j.s_full_power})",
        "G21     ; mm",
        "G90     ; absolute",
        "M5      ; laser off",
        "",
    ]
    return out


def _g1(points):
    return [f"G1 X{x:.3f} Y{y:.3f}" for x, y in points]


def cut(pl: Polyline, j: CutJob, index: int = 1, total: int = 1, label: str = "cut") -> list[str]:
    """one path: G0 to its start, M3, warmup wiggle, passes, (loop) overlap, M5"""
    pts = decimate(list(pl.points), j.min_segment_mm)
    if len(pts) < 2:
        return []
    if path_length(pts) < j.warmup_mm / 2:      # a stub: one out-and-back, not a shuttle
        warm = pts[1:] + pts[-2::-1]
    else:
        warm = warmup_wiggle(pts, j.warmup_mm)
    g = [f"; --- {label} {index}/{total} ({'loop' if pl.closed else 'open path'}, {len(pts)} pts) ---",
         f"G0 X{pts[0][0]:.3f} Y{pts[0][1]:.3f}", f"{j.on} S{j.power_s}", f"F{j.feed_mm_per_min}",
         f"; warmup wiggle: {j.warmup_mm / 2:.1f}mm out and back"]
    g += _g1(warm)
    for k in range(j.passes):
        if j.passes > 1:
            g.append(f"; pass {k + 1}/{j.passes}" + ("" if pl.closed else (" (forward)" if k % 2 == 0 else " (reverse)")))
        seq = pts[1:] if (pl.closed or k % 2 == 0) else pts[-2::-1]
        g += _g1(seq)
    if pl.closed:
        g.append(f"; overlap: {j.warmup_mm:.1f}mm past the start")
        g += _g1(follow_through(pts, j.warmup_mm))
    return g + ["M5", ""]


def moves(start, pts, arc_tol_mm: float | None = None, smooth_tol_mm: float | None = None) -> list[str]:
    """motion from `start` through `pts`: G1 lines, or G2/G3 arcs + G1 fitted within arc_tol_mm
    (arcfit.fit_arcs), or tangent-continuous biarcs within smooth_tol_mm (arcfit.fit_smooth_arcs)"""
    if smooth_tol_mm or arc_tol_mm:
        import arcfit
        fit = arcfit.fit_smooth_arcs if smooth_tol_mm else arcfit.fit_arcs
        return arcfit.prims_to_gcode(fit([start, *pts], smooth_tol_mm or arc_tol_mm))
    return _g1(pts)


def prepare(points, simplify_mm: float = 0.0, min_segment_mm: float = 0.0, smooth_mm: float | None = None,
            smooth_arc_tol_mm: float = 0.02):
    """a traced stroke made streamable: a smoothing spline within (smooth_mm - smooth_arc_tol_mm)
    when smooth_mm is set, else simplify (raw traces are ~0.1 mm staircases GRBL can't stream at
    etch feeds) then decimate"""
    pts = list(points)
    if smooth_mm:
        import arcfit
        return arcfit.smooth_polyline(pts, smooth_mm - smooth_arc_tol_mm)
    if simplify_mm > 0 and len(pts) > 2:
        from shapely.geometry import LineString
        pts = list(LineString(pts).simplify(simplify_mm).coords)
    return decimate(pts, min_segment_mm)


def etch(pl: Polyline, j: CutJob, index: int = 1, total: int = 1, arc_tol_mm: float | None = None,
         smooth_tol_mm: float | None = None) -> list[str]:
    """one etch stroke (a score, not a cut). The warmup SPREADS the cold-start energy instead of
    piling it up: a loop is etched, then followed round past its start for j.etch_lead_mm (laps if
    it is shorter); an open stroke ping-pongs over itself first (warmup_wiggle), and a short tick
    gets ONE out-and-back. Strokes are never re-traced to save a warmup (that double-etches).
    Passes ping-pong. The points go out as given: prepare() them first."""
    pts = list(pl.points)
    if len(pts) < 2:
        return []
    lead = j.etch_lead_mm
    warm, tail = [], []
    if pl.closed:
        tail = follow_through(pts, lead, laps=True)
    else:
        warm = warmup_wiggle(pts, lead)                       # ends back at pts[0]
        if path_length(pts) < max(1.0, lead / 4):
            warm = list(pts[1:]) + list(reversed(pts))[1:]
    seq = []
    for k in range(j.passes):
        if k == 0:
            seq += warm
        seq += pts[1:] if k % 2 == 0 else pts[-2::-1]
    seq += tail
    g = [f"; --- etch {index}/{total} ({'loop' if pl.closed else 'open'}) ---",
         f"G0 X{pts[0][0]:.3f} Y{pts[0][1]:.3f}", f"{j.on} S{j.power_s}", f"F{j.feed_mm_per_min}"]
    return g + moves(pts[0], seq, arc_tol_mm, smooth_tol_mm) + ["M5", ""]


def raster(img, line_spacing_mm: float, power_percent: float, feed_mm_per_min: int, mode: str = "halftone",
           height_mm: float | None = None, s_full_power: int = 1000) -> list[str]:
    """image engraving, one row per line_spacing_mm, top row at the top of height_mm (default: the
    image's own height): M4 dynamic, G0 over white, G1 at a per-run S (halftone: full; grayscale:
    darker = more). S0 on every travel: with laser mode off Grbl keeps the last S during G0."""
    from itertools import groupby
    if img.mode != "L":
        img = img.convert("L")
    w, h = img.size
    px = img.load()
    top = h * line_spacing_mm if height_mm is None else height_mm
    max_s = round(power_percent / 100.0 * s_full_power)
    out = [f"; --- raster engrave: {mode}, {w}x{h} px @ {line_spacing_mm}mm/line ---",
           f"; max power S={max_s} ({power_percent}%), feed F={feed_mm_per_min}",
           "M4 S0   ; arm dynamic-power mode; S in motion commands controls power",
           f"F{feed_mm_per_min}"]
    for r in range(h):
        y = top - (r + 0.5) * line_spacing_mm
        if y < 0 or y > top:
            continue
        x = 0
        for val, grp in groupby(px[c, r] for c in range(w)):
            n = sum(1 for _ in grp)
            a, b, x = x, x + n - 1, x + n
            if val == 255:
                continue
            s = max_s if mode == "halftone" else round((255 - val) / 255 * max_s)
            if s <= 0:
                continue
            out.append(f"G0 X{(a + 0.5) * line_spacing_mm:.3f} Y{y:.3f} S0")
            out.append(f"G1 X{(b + 0.5) * line_spacing_mm:.3f} Y{y:.3f} S{s}")
    out.append("M5  ; raster done")
    return out


def bridged(loop_points, bridge_points, width: float, kerf: float = 0.0) -> list[Polyline]:
    """a closed loop cut as open pieces with a bridge (`width` of material left, the path gap
    allowing for the kerf) centred on each bridge point (projected onto the loop): bridge_chains"""
    from shapely.geometry import LineString, Point
    pts = list(loop_points)
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    if not bridge_points:
        return [Polyline(pts, True)]
    line = LineString(pts)
    on = [line.interpolate(line.project(Point(q))).coords[0] for q in bridge_points]
    return [Polyline(p, False) for p in bridge_chains([pts], on, width + kerf, tol_mm=1e-3)]


def order(polylines, start=(0.0, 0.0), optimise: bool = True):
    """middle-out cut order; optimise=False keeps the plain smallest-area-first rule"""
    return _order.order_middle_out(polylines, start) if optimise else _order.order_by_area(polylines)


# ---------------------------------------------------------------- header notes, motion report, combining
def insert_notes(lines, notes) -> list[str]:
    """`notes` as comment lines at the end of the header (before the ';' above ';HEAD: laser')"""
    lines = list(lines)
    at = lines.index(";HEAD: laser") - 1 if ";HEAD: laser" in lines else 0
    lines[at:at] = [n if n.startswith(";") else f"; {n}" for n in notes]
    return lines


def motion_report(lines, machine=None, sender=None, hotspots: int = 5) -> list[str]:
    """grbl_model's prediction (per-stroke speed / energy factor, hotspots) for these lines, as
    header notes; UNVERIFIED machine / sender defaults when None"""
    import grbl_model
    res = grbl_model.simulate("\n".join(lines), machine, sender)
    return insert_notes(lines, grbl_model.report_lines(res, hotspots=hotspots))


def translate(program, dx: float = 0.0, dy: float = 0.0):
    """the same program moved by (dx, dy) mm: every absolute X / Y word on a motion line
    (G0-G3, or a modal continuation line) shifts; I / J arc offsets are relative and stay.
    Comments are kept as they are; an existing frame pass and park are dropped (render() adds
    them again for the new extent). Returns text for text, a list for a list."""
    as_text = isinstance(program, str)
    lines = program.split("\n") if as_text else list(program)
    # a frame or park computed before the move would be stale (its comment
    # quotes the old extent): drop both, render()/finish() adds them anew
    lines = strip_frame(_strip_park(lines))
    out = []
    for line in lines:
        code, sep, comment = line.partition(";")
        if not re.search(r"\b[XY]-?[0-9.]", code) or re.match(r"\s*(G9[12]|\$)", code) \
                or not (re.match(r"\s*G0?[0-3]\b", code) or re.match(r"\s*[XY]", code)):
            out.append(line)
            continue
        code = re.sub(r"\b([XY])(-?[0-9.]+)",
                      lambda m: f"{m.group(1)}{float(m.group(2)) + (dx if m.group(1) == 'X' else dy):.3f}",
                      code)
        out.append(code + sep + comment)
    return "\n".join(out) if as_text else out


def combine(*programs, validate: bool = False) -> str:
    """several programs (texts or line lists, finished or not) as ONE: the first keeps its
    preamble; later ones lose their $-settings writes (a $32=1 mid-stream stopped a real sender
    after the etch pass) and their ;HEAD / ;MATERIAL / ;LASER_MODE header lines; every frame and park is dropped and the whole is finished once (one
    frame over the union, one park)"""
    parts = []
    for k, p in enumerate(programs):
        ls = p.split("\n") if isinstance(p, str) else list(p)
        ls = strip_frame(_strip_park(ls))
        if k:                              # later programs: no settings writes, no second file header
            ls = [l for l in ls if not l.lstrip().startswith(("$", ";HEAD:", ";MATERIAL:", ";LASER_MODE:"))]
        while ls and not ls[-1].strip():
            ls.pop()
        parts += ls + [""]
    return render(parts, validate)


def _strip_park(lines):
    out, skip = [], 0
    for l in lines:
        if skip:
            skip -= 1
            continue
        if l.startswith(PARK_MARK):
            skip = 2                       # M5, G0
            continue
        out.append(l)
    return out


# ---------------------------------------------------------------- finishing + writing
_Y = re.compile(r"\bY(-?[0-9.]+)")


def max_cut_y(lines) -> float:
    ys = [float(m.group(1)) for ln in lines
          if ln.split(";")[0].strip().startswith("G1") and (m := _Y.search(ln.split(";")[0]))]
    return max(ys, default=0.0)


def park(y_max: float) -> list[str]:
    return [f"{PARK_MARK} laser off, X0 at the part's max Y", "M5", f"G0 X0 Y{y_max:.3f}", ""]


def finish(lines) -> list[str]:
    """frame (if missing) + park (if missing); idempotent"""
    lines = add_frame(list(lines))
    if not any(l.startswith(PARK_MARK) for l in lines):
        lines += park(max_cut_y(lines))
    return lines


def name(base: str, w: float, h: float, material_id: str) -> str:
    """standard file stem: <part>_<variant>_<W>x<H>_<material>, W x H rounded up to whole mm"""
    return f"{base}_{math.ceil(w - 1e-9)}x{math.ceil(h - 1e-9)}_{material_id}"


def cut_extent(lines):
    xs, ys = [0.0], [0.0]
    for ln in lines:
        c = ln.split(";")[0].strip()
        if c.startswith("G1"):
            for ax, v in re.findall(r"\b([XY])(-?[0-9.]+)", c):
                (xs if ax == "X" else ys).append(float(v))
    return max(xs), max(ys)


def std_path(out_dir, base, lines, material_id) -> Path:
    w, h = cut_extent(lines)
    return Path(out_dir) / f"{name(base, w, h, material_id)}.gcode"


def check(text: str, profile: Path | None = None) -> list:
    """gcode_validate violations for this text against the machine profile (PROFILE env or default.yaml)"""
    import yaml
    sys.path.insert(0, str(REPO / "cnc_vibes" / "scripts"))
    from gcode_validate import validate
    prof = Path(profile or os.environ.get("PROFILE") or REPO / "cnc_vibes" / "profiles" / "default.yaml")
    tools = yaml.safe_load((REPO / "cnc_vibes" / "profiles" / "tools.yaml").read_text())
    return validate(text, yaml.safe_load(prof.read_text()), tools)


def render(lines, validate: bool = True) -> str:
    """finished text (frame + park), checked: raises GcodeError on a long line or (validate=True)
    any validator violation. Accepts a line list or text."""
    if isinstance(lines, str):
        lines = lines.split("\n")
    lines = finish(lines)
    long = [(i, len(l)) for i, l in enumerate(lines, 1) if len(l) >= MAX_LINE]
    if long:
        raise GcodeError(f"lines of {MAX_LINE}+ characters end a Grbl_ESP32 SD job silently: {long[:5]}")
    text = "\n".join(lines)
    bad = check(text) if validate else []
    if bad:
        raise GcodeError("G-code fails validation:\n  " + "\n  ".join(str(v) for v in bad[:20])
                         + (f"\n  ... {len(bad) - 20} more" if len(bad) > 20 else ""))
    return text


def write(path, lines, validate: bool = True) -> Path:
    """the one way to write laser G-code: finish, check, write"""
    text = render(lines, validate)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path
