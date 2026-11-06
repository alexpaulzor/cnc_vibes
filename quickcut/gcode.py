"""quickcut.gcode -- THE way laser G-code is made in this repo (and in scads/marbles).

Every generator builds its geometry however it likes, then emits through these functions and writes
with `write()`. That is where the shop rules live, once:

  * preamble: header comments (;HEAD: laser, ;MATERIAL:, ;LASER_MODE: static), $32=1, G21, G90, M5
  * cut(): static M3 at the recipe's power; a warmup wiggle (half the warmup distance out and back)
    on every path; recipe passes (open paths ping-pong, loops repeat); closed loops overlap the
    warmup distance past their start; M5 after every cut (never a G0 with the beam on)
  * etch(): a light single pass per stroke with a short fixed lead-in (the etch recipe)
  * bridged(): a loop cut as open pieces, leaving uncut bridges (knock-outs)
  * order(): middle-out (contents before their container) with short travel (order.py)
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

from frame import MARK as FRAME_MARK, add_frame  # noqa: E402
from materials import SHARED_MATERIALS  # noqa: E402
from motion import decimate, follow_through, path_length, warmup_wiggle  # noqa: E402
import order as _order  # noqa: E402

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

    @property
    def power_s(self) -> int:
        return round(self.power_percent / 100.0 * self.s_full_power)

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
              passes=r.get("passes", 1), material_id=material_id, s_full_power=s_full_power)
    if recipe == "etch":
        kw["lead_in_mm"] = r.get("lead_in_mm", 3.0)
        kw["min_segment_mm"] = 0.3
    kw.update(override)
    return CutJob(**kw)


# ---------------------------------------------------------------- emission
def preamble(j: CutJob, title: str = "", notes=(), etch: CutJob | None = None) -> list[str]:
    out = [f"; {title}"] if title else []
    out += [f"; {n}" for n in notes]
    out += [
        f"; quickcut: GRBL diode-laser {('etch + ' if etch else '')}cut, static M3, warmup on every path",
        f"; cut: {j.material_id} {j.power_percent:g}% (S{j.power_s} of {j.s_full_power}) "
        f"F{j.feed_mm_per_min} x{j.passes}; warmup {j.warmup_ms:.0f}ms = {j.warmup_mm:.1f}mm",
    ]
    if etch:
        out.append(f"; etch: {etch.power_percent:g}% F{etch.feed_mm_per_min}, {etch.lead_in_mm:g}mm lead-in")
    out += [
        "; every cut: warmup wiggle (half the warmup out and back), then the passes;",
        "; closed loops overlap the warmup distance past their start; middle-out order",
        ";",
        ";HEAD: laser",
        f";MATERIAL: {j.material_id}",
        ";LASER_MODE: static  (M3, constant power; assumes Z already at focus)",
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
         f"G0 X{pts[0][0]:.3f} Y{pts[0][1]:.3f}", f"M3 S{j.power_s}", f"F{j.feed_mm_per_min}",
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


def etch(pl: Polyline, j: CutJob, index: int = 1, total: int = 1) -> list[str]:
    """one etch stroke: G0, M3 at the etch power, the stroke (+ lead-in past the start on a loop), M5"""
    pts = decimate(list(pl.points), j.min_segment_mm) if len(pl.points) > 2 else list(pl.points)
    if len(pts) < 2:
        return []
    g = [f"; --- etch {index}/{total} ---", f"G0 X{pts[0][0]:.3f} Y{pts[0][1]:.3f}",
         f"M3 S{j.power_s}", f"F{j.feed_mm_per_min}"]
    g += _g1(pts[1:])
    if pl.closed:
        g += _g1(follow_through(pts, j.lead_in_mm or 3.0))
    return g + ["M5", ""]


def bridged(loop_points, bridge_points, width: float, kerf: float = 0.0) -> list[Polyline]:
    """a closed loop cut as open pieces with a bridge (`width` of material left, the path gap
    allowing for the kerf) centred on each bridge point"""
    from shapely.geometry import LineString, Point
    from shapely.ops import substring
    pts = list(loop_points)
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    line = LineString(pts)
    L, gap = line.length, width + kerf
    ds = sorted(line.project(Point(q)) for q in bridge_points)
    if not ds:
        return [Polyline(pts, True)]
    spans = [(ds[i] + gap / 2, ds[i + 1] - gap / 2) for i in range(len(ds) - 1)]
    wrap = list(substring(line, ds[-1] + gap / 2, L).coords) + list(substring(line, 0, ds[0] - gap / 2).coords)[1:]
    return [Polyline(list(substring(line, a, b).coords), False) for a, b in spans] + [Polyline(wrap, False)]


def order(polylines, start=(0.0, 0.0), optimise: bool = True):
    """middle-out cut order; optimise=False keeps the plain smallest-area-first rule"""
    return _order.order_middle_out(polylines, start) if optimise else _order.order_by_area(polylines)


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


def render(lines) -> str:
    """finished text (frame + park), checked: raises GcodeError on a long line or any violation"""
    lines = finish(lines)
    long = [(i, len(l)) for i, l in enumerate(lines, 1) if len(l) >= MAX_LINE]
    if long:
        raise GcodeError(f"lines of {MAX_LINE}+ characters end a Grbl_ESP32 SD job silently: {long[:5]}")
    text = "\n".join(lines)
    bad = check(text)
    if bad:
        raise GcodeError("G-code fails validation:\n  " + "\n  ".join(str(v) for v in bad[:20])
                         + (f"\n  ... {len(bad) - 20} more" if len(bad) > 20 else ""))
    return text


def write(path, lines, validate: bool = True) -> Path:
    """the one way to write laser G-code: finish, check, write"""
    text = render(lines) if validate else "\n".join(finish(lines))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path
