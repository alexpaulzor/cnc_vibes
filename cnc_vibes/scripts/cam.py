"""Parametric 2.5D CNC operations.

Single-file library that produces validator-clean GCode for the common
2.5D spindle ops without needing a CAM GUI. Same pattern as the laser
emitters in lessons/laser/{01_spacer,03_jigsaw}: pure function takes a
shapely shape + tool + material, returns GcodeOutput.

Operations (this file, this turn):
  - profile_cut: cut around a polygon's perimeter (inside, outside, on)

Operations on the roadmap (follow-up commits):
  - pocket_mill: clear the interior with an offset spiral
  - drill_array: G81/explicit cycle at each (x, y)
  - engrave_text: centerline trace (constant depth) or V-carve (variable)

Defaults are designed to fail loud, not silent. When the caller didn't
specify a tool, a warning prints with the default tool's name and the
implications of using the wrong tool for the requested op. CamConfig(
strict=True) upgrades all warnings to fatal errors — for CI / batch
runs where surprises are unacceptable.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry


REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILES_DIR = REPO_ROOT / "profiles"

DEFAULT_TOOL_ID = "flat_3.175mm_2flute"
DEFAULT_MATERIAL_ID = "plywood_baltic_birch_3mm"
DEFAULT_SAFE_Z_MM = 5.0
DEFAULT_PLUNGE_FACTOR = 0.5  # fraction of cut feed used for Z plunge


# ---------------------------------------------------------------------------
# Profile loading
# ---------------------------------------------------------------------------


@dataclass
class Tool:
    id: str
    type: str  # flat_endmill | ball_endmill | v_bit | drill
    diameter_mm: float
    flutes: int = 1
    flute_length_mm: float | None = None
    shank_mm: float | None = None
    max_rpm: int = 24000
    max_plunge_mm_per_min: int = 300
    angle_deg: float | None = None  # v_bit only

    @property
    def radius_mm(self) -> float:
        return self.diameter_mm / 2


@dataclass
class Material:
    id: str
    family: str  # wood | acrylic | aluminum | ...
    thickness_mm: float
    chipload: dict  # tool_id → mm/tooth
    doc_fraction: float = 0.5
    doc_fraction_finish: float | None = None

    def chipload_for(self, tool_id: str) -> float | None:
        return self.chipload.get(tool_id)


def load_tool(tool_id: str) -> Tool:
    with (PROFILES_DIR / "tools.yaml").open() as f:
        tools = yaml.safe_load(f)
    for t in tools:
        if t.get("id") == tool_id:
            return Tool(
                id=t["id"],
                type=t["type"],
                diameter_mm=float(t["diameter_mm"]),
                flutes=int(t.get("flutes", 1)),
                flute_length_mm=t.get("flute_length_mm"),
                shank_mm=t.get("shank_mm"),
                max_rpm=int(t.get("max_rpm", 24000)),
                max_plunge_mm_per_min=int(t.get("max_plunge_mm_per_min", 300)),
                angle_deg=t.get("angle_deg"),
            )
    available = ", ".join(sorted(t.get("id", "?") for t in tools))
    raise SystemExit(f"unknown tool: {tool_id}. Available: {available}")


def load_material(material_id: str) -> Material:
    with (PROFILES_DIR / "materials.yaml").open() as f:
        materials = yaml.safe_load(f)
    for m in materials:
        if m.get("id") == material_id:
            return Material(
                id=m["id"],
                family=m["family"],
                thickness_mm=float(m["thickness_mm"]),
                chipload=dict(m.get("chipload", {})),
                doc_fraction=float(m.get("doc_fraction", 0.5)),
                doc_fraction_finish=m.get("doc_fraction_finish"),
            )
    available = ", ".join(sorted(m.get("id", "?") for m in materials))
    raise SystemExit(f"unknown material: {material_id}. Available: {available}")


# ---------------------------------------------------------------------------
# CamConfig: cross-op settings + warning/strict policy
# ---------------------------------------------------------------------------


@dataclass
class CamConfig:
    safe_z_mm: float = DEFAULT_SAFE_Z_MM
    spindle_rpm: int = 18000  # within range for most wood-cutting tools
    plunge_factor: float = DEFAULT_PLUNGE_FACTOR
    step_down_mm: float | None = None  # None = auto from material.doc_fraction
    strict: bool = False  # True = warnings become fatal SystemExit


@dataclass
class GcodeOutput:
    lines: list[str]
    warnings: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def _warn_or_fail(msg: str, cfg: CamConfig, warnings: list[str]) -> None:
    """Emit a warning. In strict mode, raise SystemExit instead."""
    if cfg.strict:
        raise SystemExit(f"error (strict mode): {msg}")
    print(f"warning: {msg}", file=sys.stderr)
    warnings.append(msg)


# ---------------------------------------------------------------------------
# Tool-vs-op compatibility warnings
#
# Each op calls _check_tool_for_op with the operation name; this function
# emits warnings tailored to the op + tool + material combination. Strict
# mode escalates to errors.
# ---------------------------------------------------------------------------


_DEFAULT_TOOL_WARNING_IMPLICATIONS = {
    "profile_cut": (
        "Default flat endmill is generally fine for plywood ≤6mm. Beware: "
        "top-surface tear-out on plywood (consider downcut bit), and rough "
        "edges on thicker stock (consider compression bit)."
    ),
    "pocket_mill": (
        "Default flat endmill is fine for shallow pockets; for pockets deeper "
        "than ~3x diameter, chip evacuation worsens (consider upcut helical)."
    ),
    "drill_array": (
        "Default flat endmill is NOT a drill bit. It can plunge-drill in wood "
        "with care (slow plunge feed), but for precision holes use a real "
        "drill bit defined as type=drill in profiles/tools.yaml."
    ),
    "engrave_text": (
        "Default flat endmill produces constant-width engraved lines. For "
        "variable-width letterforms (V-carve), use type=v_bit instead."
    ),
}


def _check_tool_for_op(
    tool: Tool,
    material: Material,
    op: str,
    depth_mm: float,
    is_default: bool,
    cfg: CamConfig,
    warnings: list[str],
) -> None:
    if is_default:
        impl = _DEFAULT_TOOL_WARNING_IMPLICATIONS.get(op, "")
        _warn_or_fail(
            f"using default tool '{tool.id}' for {op}. {impl} "
            f"Pass tool=... explicitly to suppress this warning.",
            cfg,
            warnings,
        )
    # Op-specific cross-checks (run even with explicit tools)
    if op == "profile_cut":
        if tool.type == "ball_endmill":
            _warn_or_fail(
                f"profile_cut with type=ball_endmill: ball-end leaves a curved "
                f"profile at the bottom of the cut; expect rounded outer edges. "
                f"Use a flat endmill for square edges.",
                cfg,
                warnings,
            )
        elif tool.type == "v_bit":
            _warn_or_fail(
                f"profile_cut with type=v_bit: V-bit produces a non-vertical "
                f"cut wall; the part's outline will be wider at the top than "
                f"the bottom by 2*depth*tan(angle/2). Usually wrong choice for "
                f"profile cuts.",
                cfg,
                warnings,
            )
        elif tool.type == "drill":
            _warn_or_fail(
                f"profile_cut with type=drill: drill bits aren't designed for "
                f"side-cutting; you'll snap the bit or burn the material. Use "
                f"an endmill.",
                cfg,
                warnings,
            )
    if tool.flute_length_mm is not None and depth_mm > tool.flute_length_mm:
        _warn_or_fail(
            f"requested depth {depth_mm}mm exceeds tool flute length "
            f"{tool.flute_length_mm}mm; tool shank will rub against the cut "
            f"wall. Either reduce depth, multi-pass to a wider clearance, or "
            f"use a longer tool.",
            cfg,
            warnings,
        )
    if material.chipload_for(tool.id) is None:
        _warn_or_fail(
            f"material '{material.id}' has no chipload entry for tool "
            f"'{tool.id}'; feed rate will fall back to a conservative default "
            f"(may produce slow cuts and rubbing-rather-than-cutting). Add a "
            f"chipload entry to profiles/materials.yaml to suppress this.",
            cfg,
            warnings,
        )


# ---------------------------------------------------------------------------
# Derived feed / DOC math
# ---------------------------------------------------------------------------


_FALLBACK_FEED_MM_PER_MIN = 600  # used when chipload is missing


def _derive_feed(tool: Tool, material: Material, cfg: CamConfig) -> int:
    chipload = material.chipload_for(tool.id)
    if chipload is None:
        return _FALLBACK_FEED_MM_PER_MIN
    return int(chipload * tool.flutes * cfg.spindle_rpm)


def _derive_step_down(tool: Tool, material: Material, cfg: CamConfig) -> float:
    if cfg.step_down_mm is not None:
        return cfg.step_down_mm
    return material.doc_fraction * tool.diameter_mm


def _plunge_feed(feed: int, tool: Tool, cfg: CamConfig) -> int:
    """Plunge feed = min(plunge_factor * cut_feed, tool max_plunge limit).
    Capping at the tool limit prevents the validator from flagging plunge
    moves; without it, a fast cut feed produces a fast plunge that bottoms
    out the tool's spec."""
    derived = max(50, int(feed * cfg.plunge_factor))
    return min(derived, tool.max_plunge_mm_per_min)


# ---------------------------------------------------------------------------
# GCode header (validator-friendly)
# ---------------------------------------------------------------------------


def _spindle_header(
    op: str, tool: Tool, material: Material, cfg: CamConfig, depth_mm: float
) -> list[str]:
    return [
        f"; {op}: depth={depth_mm}mm  tool={tool.id}  material={material.id}",
        f"; generated by scripts/cam.py",
        f";",
        f";HEAD: spindle",
        f";MATERIAL: {material.id}",
        f";TOOL: {tool.id}",
        "",
        "$32=0   ; spindle mode (not laser)",
        "G21     ; mm",
        "G90     ; absolute",
        "G94     ; feed/min (mm/min)",
        f"G0 Z{cfg.safe_z_mm:.3f}",
        f"M3 S{cfg.spindle_rpm}",
        "",
    ]


def _spindle_footer(cfg: CamConfig) -> list[str]:
    return [
        "",
        f"G0 Z{cfg.safe_z_mm:.3f}",
        "M5",
        "G0 X0 Y0",
        "",
    ]


# ---------------------------------------------------------------------------
# Operation: profile_cut
# ---------------------------------------------------------------------------


Side = Literal["outside", "inside", "on"]


def profile_cut(
    polygon: BaseGeometry,
    depth_mm: float,
    tool: Tool | None = None,
    material: Material | None = None,
    side: Side = "outside",
    cfg: CamConfig | None = None,
) -> GcodeOutput:
    """Cut around a polygon's perimeter at the given depth.

    side:
      "outside" — tool offset OUTSIDE the polygon by tool radius; the finished
                  part has the original outline (correct for cutting a part
                  OUT of stock)
      "inside"  — tool offset INSIDE; the finished HOLE has the original
                  outline (correct for cutting a hole through stock)
      "on"      — tool centerline traces the polygon exactly; finished cut
                  is approximately polygon ± kerf/2 (only useful for very
                  thin laser-like cuts; usually wrong for spindle)

    Depth is reached over multiple passes of cfg.step_down_mm (auto-derived
    from material.doc_fraction if not specified). No tabs in this first cut;
    caller should clamp the workpiece down.
    """
    cfg = cfg or CamConfig()
    is_default_tool = tool is None
    tool = tool or load_tool(DEFAULT_TOOL_ID)
    material = material or load_material(DEFAULT_MATERIAL_ID)
    warnings: list[str] = []
    _check_tool_for_op(
        tool, material, "profile_cut", depth_mm, is_default_tool, cfg, warnings
    )

    # Build the toolpath polygon (offset the input by tool radius)
    if side == "outside":
        path_geom = polygon.buffer(tool.radius_mm)
    elif side == "inside":
        path_geom = polygon.buffer(-tool.radius_mm)
    elif side == "on":
        path_geom = polygon
    else:
        raise SystemExit(f"profile_cut: side must be outside|inside|on, got {side!r}")

    if path_geom.is_empty:
        _warn_or_fail(
            f"profile_cut produced empty toolpath (polygon may be smaller than "
            f"tool diameter for side={side!r}); skipping.",
            cfg,
            warnings,
        )
        return GcodeOutput(lines=[], warnings=warnings)

    # Flatten MultiPolygon → list of single-polygon paths
    if isinstance(path_geom, MultiPolygon):
        polys = list(path_geom.geoms)
    else:
        polys = [path_geom]

    feed = _derive_feed(tool, material, cfg)
    plunge_f = _plunge_feed(feed, tool, cfg)
    step_down = _derive_step_down(tool, material, cfg)
    n_passes = max(1, int(-(-depth_mm // step_down)))  # ceil division

    lines = _spindle_header("profile_cut", tool, material, cfg, depth_mm)
    lines.append(
        f"; {n_passes} pass(es) at step_down={step_down}mm, feed={feed}, plunge={plunge_f}"
    )
    lines.append(f"; side={side}, paths={len(polys)}")
    lines.append("")

    for path_idx, poly in enumerate(polys, start=1):
        coords = list(poly.exterior.coords)
        if len(coords) < 3:
            continue
        x0, y0 = coords[0]
        lines.append(f"; --- path {path_idx}/{len(polys)} ({len(coords)} pts) ---")
        lines.append(f"G0 Z{cfg.safe_z_mm:.3f}")
        lines.append(f"G0 X{x0:.3f} Y{y0:.3f}")
        for pass_n in range(1, n_passes + 1):
            cur_z = -min(pass_n * step_down, depth_mm)
            if n_passes > 1:
                lines.append(f"; pass {pass_n} of {n_passes} (Z={cur_z:.3f})")
            lines.append(f"G1 Z{cur_z:.3f} F{plunge_f}")
            for x, y in coords[1:]:
                lines.append(f"G1 X{x:.3f} Y{y:.3f} F{feed}")
        lines.append(f"G0 Z{cfg.safe_z_mm:.3f}")
        lines.append("")

    lines.extend(_spindle_footer(cfg))
    return GcodeOutput(lines=lines, warnings=warnings)


# ---------------------------------------------------------------------------
# Operation: pocket_mill (offset-spiral clearance)
# ---------------------------------------------------------------------------


def _offset_rings(
    polygon: BaseGeometry, tool_radius_mm: float, stepover_factor: float
) -> list[Polygon]:
    """Repeatedly inset polygon by stepover until the result is empty.
    Returns the list of rings (outermost first, innermost last). The
    outermost ring is the polygon inset by exactly tool_radius (so the
    tool's cutter edge tangent the polygon boundary)."""
    if not (0 < stepover_factor < 1):
        raise SystemExit(f"stepover_factor must be in (0, 1), got {stepover_factor}")
    stepover = stepover_factor * (2 * tool_radius_mm)  # fraction of tool dia
    rings: list[Polygon] = []
    # First ring: inset by tool_radius so cutter edge tangent polygon
    current = polygon.buffer(-tool_radius_mm)
    if current.is_empty:
        return rings
    while not current.is_empty:
        # Decompose MultiPolygon → individual rings
        if isinstance(current, MultiPolygon):
            rings.extend(g for g in current.geoms if isinstance(g, Polygon))
        elif isinstance(current, Polygon):
            rings.append(current)
        # Next inset
        current = current.buffer(-stepover)
    return rings


def pocket_mill(
    polygon: BaseGeometry,
    depth_mm: float,
    tool: Tool | None = None,
    material: Material | None = None,
    stepover_factor: float = 0.5,
    cfg: CamConfig | None = None,
) -> GcodeOutput:
    """Clear the interior of a polygon to the given depth using offset-spiral
    rings. Each Z step traverses every ring outermost-first to evacuate
    chips outward; depth is reached over multiple Z passes per cfg.step_down_mm.

    stepover_factor: fraction of tool diameter to advance between rings
    (default 0.5 = 50% stepover, conservative for wood). Smaller is
    finer; bigger gambles on chip evacuation.

    Warning categories specific to pockets:
    - Pocket too small to fit even one ring (polygon < 2*tool_radius)
    - Deep pocket + flat endmill (chip-evac concern)
    - Ball / V-bit / drill misuse
    """
    cfg = cfg or CamConfig()
    is_default_tool = tool is None
    tool = tool or load_tool(DEFAULT_TOOL_ID)
    material = material or load_material(DEFAULT_MATERIAL_ID)
    warnings: list[str] = []
    _check_tool_for_op(
        tool, material, "pocket_mill", depth_mm, is_default_tool, cfg, warnings
    )

    # Deep-pocket + flat endmill chip-evacuation check
    if tool.type == "flat_endmill" and depth_mm > tool.diameter_mm * 3:
        _warn_or_fail(
            f"pocket depth {depth_mm}mm exceeds 3x tool diameter "
            f"({tool.diameter_mm}mm); flat-endmill chip evacuation degrades "
            f"sharply with depth. Consider an upcut helical bit or reduce "
            f"step_down via CamConfig(step_down_mm=...).",
            cfg,
            warnings,
        )

    rings = _offset_rings(polygon, tool.radius_mm, stepover_factor)
    if not rings:
        _warn_or_fail(
            f"pocket polygon is smaller than 2x tool diameter "
            f"({2 * tool.diameter_mm}mm); cannot fit even one ring. "
            f"Use a smaller tool or skip pocketing this region.",
            cfg,
            warnings,
        )
        return GcodeOutput(lines=[], warnings=warnings)

    feed = _derive_feed(tool, material, cfg)
    plunge_f = _plunge_feed(feed, tool, cfg)
    step_down = _derive_step_down(tool, material, cfg)
    n_z_passes = max(1, int(-(-depth_mm // step_down)))

    lines = _spindle_header("pocket_mill", tool, material, cfg, depth_mm)
    lines.append(
        f"; {n_z_passes} Z-pass(es) at step_down={step_down}mm, "
        f"feed={feed}, plunge={plunge_f}"
    )
    lines.append(
        f"; {len(rings)} ring(s) per Z-pass at stepover_factor={stepover_factor} "
        f"(= {stepover_factor * 2 * tool.radius_mm:.2f}mm)"
    )
    lines.append("")

    for pass_n in range(1, n_z_passes + 1):
        cur_z = -min(pass_n * step_down, depth_mm)
        lines.append(f"; --- Z pass {pass_n}/{n_z_passes} at Z={cur_z:.3f} ---")
        for ring_idx, ring in enumerate(rings, start=1):
            coords = list(ring.exterior.coords)
            if len(coords) < 3:
                continue
            x0, y0 = coords[0]
            lines.append(f"; ring {ring_idx}/{len(rings)}")
            lines.append(f"G0 Z{cfg.safe_z_mm:.3f}")
            lines.append(f"G0 X{x0:.3f} Y{y0:.3f}")
            lines.append(f"G1 Z{cur_z:.3f} F{plunge_f}")
            for x, y in coords[1:]:
                lines.append(f"G1 X{x:.3f} Y{y:.3f} F{feed}")
        lines.append("")
    lines.append(f"G0 Z{cfg.safe_z_mm:.3f}")
    lines.append("")

    lines.extend(_spindle_footer(cfg))
    return GcodeOutput(lines=lines, warnings=warnings)


# ---------------------------------------------------------------------------
# Operation: drill_array
# ---------------------------------------------------------------------------


def drill_array(
    holes: list[tuple[float, float]],
    depth_mm: float,
    tool: Tool | None = None,
    material: Material | None = None,
    peck_depth_mm: float | None = None,
    cfg: CamConfig | None = None,
) -> GcodeOutput:
    """Drill a hole at each (x, y) coordinate to the given depth.

    Pure G0+G1+G0 implementation (no G81 cycle). Works on every GRBL
    controller without modal-state surprises. Each hole: rapid to XY at
    safe Z, slow plunge to -depth, rapid retract to safe Z.

    peck_depth_mm (optional): peck drilling — plunge by peck_depth, retract
    to safe Z for chip clear, plunge to peck_depth * 2, retract, ... until
    target depth. Use for deep holes in wood / plastic where chip evac
    matters. None (default) = single continuous plunge per hole.

    Warning categories:
    - Default tool is not a drill (warned + still emits)
    - tool.type != "drill" (probably wrong but might be intentional for
      plunge-milling with a flat endmill in wood)
    - V-bit or ball-end (almost always wrong)
    - depth > flute_length (shank rubbing)
    - Drilling metal without coolant comment (hard to enforce, just warn)
    """
    cfg = cfg or CamConfig()
    is_default_tool = tool is None
    tool = tool or load_tool(DEFAULT_TOOL_ID)
    material = material or load_material(DEFAULT_MATERIAL_ID)
    warnings: list[str] = []

    # Op-specific tool checks (don't reuse profile_cut's list — drill is
    # very different)
    if is_default_tool:
        _warn_or_fail(
            f"using default tool '{tool.id}' for drill_array — NOT a drill bit. "
            f"Flat endmills can plunge-drill in wood (with slow plunge feed) "
            f"but for precision holes use a real drill bit (type=drill) "
            f"defined in profiles/tools.yaml.",
            cfg,
            warnings,
        )
    elif tool.type == "v_bit":
        _warn_or_fail(
            f"drill_array with type=v_bit produces conical holes, not "
            f"cylindrical. Wrong tool for through-holes; OK for chamfering "
            f"if intentional.",
            cfg,
            warnings,
        )
    elif tool.type == "ball_endmill":
        _warn_or_fail(
            f"drill_array with type=ball_endmill produces hemispherical "
            f"holes. Almost always wrong unless you specifically want "
            f"that profile.",
            cfg,
            warnings,
        )
    elif tool.type == "flat_endmill":
        _warn_or_fail(
            f"drill_array with type=flat_endmill plunge-drills (not "
            f"twist-drills). Tolerable in wood at slow plunge; risky in "
            f"metal. For precision use type=drill.",
            cfg,
            warnings,
        )
    if tool.flute_length_mm is not None and depth_mm > tool.flute_length_mm:
        _warn_or_fail(
            f"depth {depth_mm}mm exceeds tool flute length "
            f"{tool.flute_length_mm}mm; shank will rub at full depth.",
            cfg,
            warnings,
        )
    if material.family in ("aluminum", "steel") and peck_depth_mm is None:
        _warn_or_fail(
            f"drilling {material.family} without peck cycle (peck_depth_mm=None) "
            f"risks chip-pack and tool snap. Consider peck_depth_mm = "
            f"{tool.diameter_mm:.1f}mm (one diameter) with WD-40 or coolant.",
            cfg,
            warnings,
        )
    if not holes:
        _warn_or_fail(
            "drill_array called with empty holes list; no output emitted.",
            cfg,
            warnings,
        )
        return GcodeOutput(lines=[], warnings=warnings)

    feed = _derive_feed(tool, material, cfg)
    plunge_f = _plunge_feed(feed, tool, cfg)

    lines = _spindle_header("drill_array", tool, material, cfg, depth_mm)
    lines.append(
        f"; {len(holes)} hole(s), depth={depth_mm}mm, plunge={plunge_f}"
        + (f", peck={peck_depth_mm}mm" if peck_depth_mm else " (single plunge)")
    )
    lines.append("")

    for hi, (hx, hy) in enumerate(holes, start=1):
        lines.append(f"; --- hole {hi}/{len(holes)} at ({hx:.3f}, {hy:.3f}) ---")
        lines.append(f"G0 Z{cfg.safe_z_mm:.3f}")
        lines.append(f"G0 X{hx:.3f} Y{hy:.3f}")
        if peck_depth_mm is None or peck_depth_mm >= depth_mm:
            # Single plunge
            lines.append(f"G1 Z{-depth_mm:.3f} F{plunge_f}")
        else:
            # Peck loop
            cur_depth = 0.0
            peck_n = 0
            while cur_depth < depth_mm:
                peck_n += 1
                cur_depth = min(cur_depth + peck_depth_mm, depth_mm)
                lines.append(f"; peck {peck_n} → Z={-cur_depth:.3f}")
                lines.append(f"G1 Z{-cur_depth:.3f} F{plunge_f}")
                if cur_depth < depth_mm:
                    lines.append(f"G0 Z{cfg.safe_z_mm:.3f}")
                    lines.append(f"G0 X{hx:.3f} Y{hy:.3f}")
        lines.append(f"G0 Z{cfg.safe_z_mm:.3f}")
        lines.append("")

    lines.extend(_spindle_footer(cfg))
    return GcodeOutput(lines=lines, warnings=warnings)


# ---------------------------------------------------------------------------
# Public CLI (light) — `python scripts/cam.py demo` produces a sample part
# so you can validate end-to-end before composing your own jobs.
# ---------------------------------------------------------------------------


def _demo() -> int:
    """Generate a sample profile_cut: 40x40mm square cut from 3mm plywood."""
    sq = Polygon([(20, 20), (60, 20), (60, 60), (20, 60)])
    out = profile_cut(sq, depth_mm=3.0, side="outside")
    target = REPO_ROOT / "examples" / "cam_demo" / "build" / "demo_profile.gcode"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(out.text)
    print(f"-> {target}  ({len(out.lines)} lines)")
    print(f"   warnings: {len(out.warnings)}")
    for w in out.warnings:
        print(f"     - {w[:80]}…" if len(w) > 80 else f"     - {w}")
    print(f"\nValidate with:")
    print(f"  python cnc.py validate {target.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["demo"], help="run a smoke-test demo")
    args = ap.parse_args()
    sys.exit({"demo": _demo}[args.command]())
