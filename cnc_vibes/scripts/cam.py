"""Parametric 2.5D CNC operations.

Single-file library that produces validator-clean GCode for the common
2.5D spindle ops without needing a CAM GUI. Same pattern as the laser
emitters in lessons/laser/{01_spacer,03_jigsaw}: pure function takes a
shapely shape + tool + material, returns GcodeOutput.

Operations (this file):
  - profile_cut: cut around a polygon's perimeter (inside, outside, on)
  - pocket_mill: clear the interior with an offset spiral
  - drill_array: peck or single-plunge drill cycle at each (x, y)
  - engrave_text: constant-depth outline trace of rendered text glyphs

Not yet shipped:
  - True V-carve (variable depth via medial-axis transform). engrave_text
    is OUTLINE-only — it traces glyph contours at constant depth. See its
    docstring for the distinction.

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
    "chamfer_edge": (
        "Default flat endmill cannot cut a chamfer — it makes a square-bottom "
        "rabbet instead. Use type=v_bit so the cut walls match the chamfer "
        "angle."
    ),
    "profile_cut_with_tabs": (
        "Default flat endmill is generally fine for plywood ≤6mm. Tabs add "
        "more holding strength but you'll still want clamps or vacuum hold-down."
    ),
    "slot_mill": (
        "Default flat endmill is fine for shallow slots; for deep slots, "
        "consider an upcut helical bit for chip evacuation."
    ),
    "face_mill": (
        "Default flat endmill is fine for face-milling thin layers; for "
        "production-quality flat surfaces use a dedicated insert face mill "
        "(larger diameter, multiple inserts) — much faster and flatter."
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
# Operation: engrave_text (constant-depth outline)
# ---------------------------------------------------------------------------


# Cross-platform font search roots, in order. _find_default_font_path returns
# the first one that exists. macOS Helvetica is the canonical first pick;
# Windows Arial and Linux DejaVu are the platform fallbacks.
_FONT_SEARCH_PATHS = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
]


def _find_default_font_path() -> str | None:
    for p in _FONT_SEARCH_PATHS:
        if Path(p).exists():
            return p
    return None


# Render resolution: how many pixels per mm in the rasterized text mask.
# Higher = smoother contours but slower. 20 px/mm is plenty for 0.5-3mm
# strokes traced by a 0.5-3mm tool. Coordinates are quantized to 3 decimal
# places downstream anyway.
_ENGRAVE_PX_PER_MM = 20


def _load_font_at_cap_height(font_path: str, sample_char: str, target_cap_px: int):
    """Load font_path at whatever pixel size makes sample_char's cap-height
    ~= target_cap_px. Same approach as the multi-line text renderer in
    lessons/laser/03_jigsaw/scratch/redteam_multiline.py: probe at a fixed
    size to derive scale, then refine once."""
    from PIL import ImageFont

    probe = ImageFont.truetype(font_path, 200)
    bbox = probe.getbbox(sample_char)
    probe_h = max(1, bbox[3] - bbox[1])
    scale = target_cap_px / probe_h
    size = max(8, int(round(200 * scale)))
    final = ImageFont.truetype(font_path, size)
    bbox = final.getbbox(sample_char)
    actual = max(1, bbox[3] - bbox[1])
    if abs(actual - target_cap_px) > target_cap_px * 0.05:
        size = max(8, int(round(size * target_cap_px / actual)))
        final = ImageFont.truetype(font_path, size)
    return final


def _text_to_contours(
    text: str,
    font_path: str,
    height_mm: float,
    px_per_mm: int,
    simplify_tolerance_mm: float = 0.05,
) -> list[list[tuple[float, float]]]:
    """Rasterize text to a high-resolution mask, then extract outer + inner
    glyph contours with cv2.findContours(RETR_CCOMP).

    Returns a list of closed polylines, each in mm coordinates relative to
    (0, 0) at the text's baseline-left. Y axis is FLIPPED relative to the
    image (image Y is top-down; CNC Y is bottom-up). Caller adds the final
    translation to the requested baseline position.

    simplify_tolerance_mm > 0 runs Douglas-Peucker (cv2.approxPolyDP) on
    each contour. cv2.findContours over-samples — a typical 8mm glyph
    yields ~1000+ near-collinear points spaced ~0.03mm apart, which is
    invisible quality but starves laser M4 dynamic power (segments are
    too short to reach programmed feed). 0.05mm collapses to ~50 points
    per glyph with no visible difference. Set to 0 to disable.

    Each contour is a tuple of (x, y) points in mm, closed (first point
    repeated at end). Both outer and inner contours (e.g. O's counter) are
    emitted as separate closed paths — for outline engraving we want to
    trace every closed curve."""
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw

    target_cap_px = max(8, int(round(height_mm * px_per_mm)))

    # Pick a sample char for cap-height calibration. Prefer an uppercase
    # ASCII letter from the text; fall back to "H" if text has none.
    sample = next((c for c in text if c.isupper() and c.isascii()), None) or "H"
    font = _load_font_at_cap_height(font_path, sample, target_cap_px)

    # Measure the full text bbox so we can size the mask canvas tightly.
    # We use a throwaway 1x1 draw context just to call textbbox.
    tmp = Image.new("L", (1, 1), 0)
    bbox = ImageDraw.Draw(tmp).textbbox((0, 0), text, font=font)
    # Pad by a few pixels so contours don't clip the edge.
    pad = 4
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    canvas_w = text_w + 2 * pad
    canvas_h = text_h + 2 * pad

    mask = Image.new("L", (canvas_w, canvas_h), 0)
    # Draw with bbox offset compensated so glyphs land at (pad, pad).
    ImageDraw.Draw(mask).text((pad - bbox[0], pad - bbox[1]), text, fill=255, font=font)

    # Track baseline position in the mask: PIL textbbox includes ascent
    # above and descent below the visible glyphs, so the baseline in mask
    # pixel coords is at: pad + ascent (where ascent = -bbox[1] effectively,
    # because bbox[1] is the top of the ink relative to drawing origin).
    # The font.getmetrics() ascent is the distance from baseline to top of
    # the font's drawing area; we drew the text such that the text-bbox
    # top-of-ink sits at y=pad. So baseline in mask = pad + (ascent - top_of_ink_offset).
    ascent, _ = font.getmetrics()
    # The text was drawn starting at y = pad - bbox[1]. The baseline of
    # the font is `ascent` pixels below that origin.
    baseline_y_mask = (pad - bbox[1]) + ascent
    # Glyph left edge sits at pad - bbox[0] + bbox[0] = pad. So the text's
    # "baseline-left" anchor in mask pixel coords is (pad, baseline_y_mask).

    arr = np.array(mask)
    if arr.max() == 0:
        return []
    contours, hierarchy = cv2.findContours(arr, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return []

    polylines: list[list[tuple[float, float]]] = []
    tol_px = max(0.0, simplify_tolerance_mm * px_per_mm)
    for contour in contours:
        if len(contour) < 3:
            continue
        if tol_px > 0:
            # Douglas-Peucker in image space. approxPolyDP is closed-aware
            # when given closed=True; the result keeps the contour rings
            # closed and visually identical at this tolerance.
            contour = cv2.approxPolyDP(contour, tol_px, True)
            if len(contour) < 3:
                continue
        # Convert pixel coords -> mm, with Y flipped (mask Y is top-down,
        # CNC Y is bottom-up). Anchor at the text baseline-left.
        path = []
        for p in contour:
            px = float(p[0][0])
            py = float(p[0][1])
            x_mm = (px - pad) / px_per_mm
            # Baseline-left anchor: y=0 at baseline, positive y is UP.
            y_mm = (baseline_y_mask - py) / px_per_mm
            path.append((x_mm, y_mm))
        # Close the path (cv2 contours are already cyclic in storage but
        # the first point isn't repeated at the end).
        if path[0] != path[-1]:
            path.append(path[0])
        polylines.append(path)
    return polylines


def engrave_text(
    text: str,
    position: tuple[float, float],
    height_mm: float,
    depth_mm: float,
    tool: Tool | None = None,
    material: Material | None = None,
    font_path: str | None = None,
    simplify_tolerance_mm: float = 0.05,
    cfg: CamConfig | None = None,
) -> GcodeOutput:
    """Engrave text as a constant-depth outline trace of glyph contours.

    OUTLINE engrave only. Each glyph is rasterized at high resolution, its
    outer + inner contours extracted (cv2.findContours RETR_CCOMP, so e.g.
    'O' produces both rings, 'A' produces both rings), and each closed
    contour becomes one G0+G1+G0 cycle at the requested depth. The result
    looks like letters traced with a pen at uniform stroke width.

    NOT a V-carve: V-carve modulates depth so a V-bit's cut width matches
    the stroke width at every point, producing the impression of variable
    stroke thickness in 3D. That requires medial-axis-transform algorithms
    and is explicitly out of scope here. If you pass a V-bit, a warning
    fires recommending a flat endmill or fine engraving cutter instead.

    Args:
        text: the string to engrave (multi-char supported, no kerning beyond
              what PIL does with the chosen font).
        position: (x, y) of text baseline-left in CNC coordinates (mm).
        height_mm: cap-height of uppercase letters in mm. Lowercase
                   descenders may extend below baseline; ascenders may
                   extend above cap-height — both fine.
        depth_mm: how deep to engrave (positive number; Z is driven negative).
        tool: a Tool, or None to use the default flat endmill.
        material: a Material, or None to use the default plywood.
        font_path: full path to a .ttf/.ttc/.otf font file, or None to use
                   a sensible platform default (Helvetica on macOS, Arial on
                   Windows, DejaVu on Linux).
        cfg: a CamConfig, or None for defaults.

    Warning categories:
      - Default tool used
      - V-bit (this op is constant-depth, not V-carve)
      - Ball endmill / drill (wrong tool)
      - tool.diameter_mm > 1.5mm with small text (cap-height < 5mm): the
        tool is wider than the glyph strokes; you'll lose fine detail.
      - depth > flute_length
      - Font file not found (falls back to platform default)
      - Empty text or no contours extracted
    """
    cfg = cfg or CamConfig()
    is_default_tool = tool is None
    tool = tool or load_tool(DEFAULT_TOOL_ID)
    material = material or load_material(DEFAULT_MATERIAL_ID)
    warnings: list[str] = []

    # Tool checks — engrave has its own list because the right answer for
    # engraving is a fine flat endmill or engraving cutter, NOT a v-bit
    # (despite what the v-bit name suggests; v-bit engraving requires
    # variable-depth control which we don't do here).
    if is_default_tool:
        _warn_or_fail(
            f"using default tool '{tool.id}' for engrave_text. "
            f"{_DEFAULT_TOOL_WARNING_IMPLICATIONS['engrave_text']} "
            f"Pass tool=... explicitly to suppress this warning.",
            cfg,
            warnings,
        )
    if tool.type == "v_bit":
        _warn_or_fail(
            f"engrave_text with type=v_bit: this op is CONSTANT-DEPTH outline "
            f"tracing, NOT V-carve. A V-bit produces a wider cut at depth than "
            f"its tip diameter would suggest, so outlines will be thicker than "
            f"expected. For true variable-width V-carve you need medial-axis "
            f"depth modulation (not implemented). Use a small flat_endmill or "
            f"a dedicated engraving cutter for predictable outlines.",
            cfg,
            warnings,
        )
    elif tool.type == "ball_endmill":
        _warn_or_fail(
            f"engrave_text with type=ball_endmill produces rounded-bottom "
            f"grooves. Usable but cosmetically inferior to a flat endmill "
            f"for outline engraving.",
            cfg,
            warnings,
        )
    elif tool.type == "drill":
        _warn_or_fail(
            f"engrave_text with type=drill: drills don't side-cut. You'll "
            f"snap the bit on the first horizontal move. Use a flat_endmill "
            f"or engraving cutter.",
            cfg,
            warnings,
        )
    if height_mm < 5.0 and tool.diameter_mm > 1.5:
        _warn_or_fail(
            f"engrave_text: tool diameter {tool.diameter_mm}mm is too wide "
            f"to trace small text (cap-height {height_mm}mm < 5mm). Glyph "
            f"strokes will be obliterated by the tool. Use a tool with "
            f"diameter <= ~1.5mm for sub-5mm text, or increase height_mm.",
            cfg,
            warnings,
        )
    if tool.flute_length_mm is not None and depth_mm > tool.flute_length_mm:
        _warn_or_fail(
            f"requested depth {depth_mm}mm exceeds tool flute length "
            f"{tool.flute_length_mm}mm; tool shank will rub against the cut "
            f"wall.",
            cfg,
            warnings,
        )
    if material.chipload_for(tool.id) is None:
        _warn_or_fail(
            f"material '{material.id}' has no chipload entry for tool "
            f"'{tool.id}'; feed rate will fall back to a conservative default.",
            cfg,
            warnings,
        )

    if not text:
        _warn_or_fail(
            "engrave_text called with empty text; no output emitted.",
            cfg,
            warnings,
        )
        return GcodeOutput(lines=[], warnings=warnings)

    # Resolve font: explicit path > platform default > PIL default (rare).
    if font_path is not None and not Path(font_path).exists():
        _warn_or_fail(
            f"font_path {font_path!r} not found; falling back to platform "
            f"default font.",
            cfg,
            warnings,
        )
        font_path = None
    if font_path is None:
        font_path = _find_default_font_path()
        if font_path is None:
            _warn_or_fail(
                "no system font found in any of the standard locations "
                f"({', '.join(_FONT_SEARCH_PATHS)}); cannot rasterize text. "
                "Pass font_path=... explicitly to a .ttf/.ttc/.otf file.",
                cfg,
                warnings,
            )
            return GcodeOutput(lines=[], warnings=warnings)

    contours = _text_to_contours(
        text,
        font_path,
        height_mm,
        _ENGRAVE_PX_PER_MM,
        simplify_tolerance_mm=simplify_tolerance_mm,
    )
    if not contours:
        _warn_or_fail(
            f"engrave_text: rasterizing text={text!r} at height={height_mm}mm "
            f"produced no contours (font may not have the characters, or "
            f"height too small). No output emitted.",
            cfg,
            warnings,
        )
        return GcodeOutput(lines=[], warnings=warnings)

    feed = _derive_feed(tool, material, cfg)
    plunge_f = _plunge_feed(feed, tool, cfg)
    x_origin, y_origin = position

    lines = _spindle_header("engrave_text", tool, material, cfg, depth_mm)
    lines.append(f"; text={text!r}  height={height_mm}mm  font={Path(font_path).name}")
    lines.append(f"; {len(contours)} closed contour(s), feed={feed}, plunge={plunge_f}")
    lines.append(f"; constant-depth outline engrave (NOT V-carve); see docstring")
    lines.append("")

    for ci, contour in enumerate(contours, start=1):
        if len(contour) < 3:
            continue
        x0, y0 = contour[0]
        lines.append(f"; --- contour {ci}/{len(contours)} ({len(contour)} pts) ---")
        lines.append(f"G0 Z{cfg.safe_z_mm:.3f}")
        lines.append(f"G0 X{x_origin + x0:.3f} Y{y_origin + y0:.3f}")
        lines.append(f"G1 Z{-depth_mm:.3f} F{plunge_f}")
        for x, y in contour[1:]:
            lines.append(f"G1 X{x_origin + x:.3f} Y{y_origin + y:.3f} F{feed}")
        lines.append(f"G0 Z{cfg.safe_z_mm:.3f}")
        lines.append("")

    lines.extend(_spindle_footer(cfg))
    return GcodeOutput(lines=lines, warnings=warnings)


# ---------------------------------------------------------------------------
# Operation: text_profile (cut glyph silhouettes out of stock)
# ---------------------------------------------------------------------------


def text_to_polygons(
    text: str,
    font_path: str,
    height_mm: float,
    px_per_mm: int = _ENGRAVE_PX_PER_MM,
    simplify_tolerance_mm: float = 0.05,
) -> list[Polygon]:
    """Rasterize text and return one shapely Polygon per glyph, with
    interior counters (the inside of O, A, P, etc.) preserved as holes.

    Unlike _text_to_contours which returns a flat list of closed
    polylines, this groups each outer ring with its child holes using
    cv2.findContours RETR_CCOMP hierarchy (level 0 = outer rings; level
    1 = immediate children = holes). The result feeds straight into
    laser_profile or per-ring profile_cut for cutting glyph silhouettes
    out of stock.

    Returns polygons in mm with Y-up, anchored at the text's baseline-
    left (consistent with _text_to_contours).
    """
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw

    target_cap_px = max(8, int(round(height_mm * px_per_mm)))
    sample = next((c for c in text if c.isupper() and c.isascii()), None) or "H"
    font = _load_font_at_cap_height(font_path, sample, target_cap_px)

    tmp = Image.new("L", (1, 1), 0)
    bbox = ImageDraw.Draw(tmp).textbbox((0, 0), text, font=font)
    pad = 4
    canvas_w = bbox[2] - bbox[0] + 2 * pad
    canvas_h = bbox[3] - bbox[1] + 2 * pad

    mask = Image.new("L", (canvas_w, canvas_h), 0)
    ImageDraw.Draw(mask).text((pad - bbox[0], pad - bbox[1]), text, fill=255, font=font)

    ascent, _ = font.getmetrics()
    baseline_y_mask = (pad - bbox[1]) + ascent

    arr = np.array(mask)
    if arr.max() == 0:
        return []

    contours, hierarchy = cv2.findContours(arr, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if not contours or hierarchy is None:
        return []

    tol_px = max(0.0, simplify_tolerance_mm * px_per_mm)

    def _to_mm(contour) -> list[tuple[float, float]]:
        if tol_px > 0:
            contour = cv2.approxPolyDP(contour, tol_px, True)
        pts = []
        for p in contour:
            x_mm = (float(p[0][0]) - pad) / px_per_mm
            y_mm = (baseline_y_mask - float(p[0][1])) / px_per_mm
            pts.append((x_mm, y_mm))
        return pts

    # RETR_CCOMP hierarchy: hierarchy[0][i] = [next, prev, first_child, parent]
    # Outer rings have parent = -1; holes have parent >= 0.
    h = hierarchy[0]
    polygons: list[Polygon] = []
    for i, contour in enumerate(contours):
        if h[i][3] != -1:
            continue  # this is a hole; will be picked up by its parent
        if len(contour) < 3:
            continue
        outer = _to_mm(contour)
        if len(outer) < 3:
            continue
        holes: list[list[tuple[float, float]]] = []
        # Walk children: first_child + sibling chain via [0] = next
        child = h[i][2]
        while child != -1:
            child_contour = contours[child]
            if len(child_contour) >= 3:
                hole_pts = _to_mm(child_contour)
                if len(hole_pts) >= 3:
                    holes.append(hole_pts)
            child = h[child][0]
        try:
            poly = Polygon(outer, holes=holes)
            if poly.is_valid and not poly.is_empty:
                polygons.append(poly)
        except Exception:
            # If shapely rejects the glyph (rare; usually a self-intersecting
            # contour from extreme tolerance), skip it.
            continue
    return polygons


def text_profile(
    text: str,
    position: tuple[float, float],
    height_mm: float,
    depth_mm: float,
    tool: Tool | None = None,
    material: Material | None = None,
    font_path: str | None = None,
    side: Side = "outside",
    simplify_tolerance_mm: float = 0.05,
    cfg: CamConfig | None = None,
) -> GcodeOutput:
    """Cut each glyph's silhouette out of stock at the requested depth.

    side="outside" (default): tool offsets OUTSIDE the outer ring and
    INSIDE each counter, so the glyph piece falls out of the stock as a
    clean letter shape. Use this to make hanging letters.

    side="inside": cuts holes in the shape of glyphs (toolpath OFFSETS
    inward on the outer ring, outward on the counter). Useful for inlay
    pockets where you want a letter-shaped recess in a surface.

    side="on": centerline trace — only useful when the kerf is small
    relative to glyph stroke width.

    Internally: build glyph polygons (with counters as holes), then for
    each glyph call profile_cut on the outer ring + each interior ring
    with the side flipped appropriately. One header per emit; sub-calls
    are stitched together with their per-ring path-section comments
    intact.
    """
    cfg = cfg or CamConfig()
    warnings: list[str] = []

    if not text:
        _warn_or_fail("text_profile: empty text; no output", cfg, warnings)
        return GcodeOutput(lines=[], warnings=warnings)

    if font_path is not None and not Path(font_path).exists():
        _warn_or_fail(
            f"font_path {font_path!r} not found; using platform default",
            cfg,
            warnings,
        )
        font_path = None
    if font_path is None:
        font_path = _find_default_font_path()
        if font_path is None:
            _warn_or_fail(
                "no system font found; pass font_path=... to a .ttf/.ttc/.otf",
                cfg,
                warnings,
            )
            return GcodeOutput(lines=[], warnings=warnings)

    glyphs = text_to_polygons(
        text,
        font_path,
        height_mm,
        simplify_tolerance_mm=simplify_tolerance_mm,
    )
    if not glyphs:
        _warn_or_fail(
            f"text_profile: text={text!r} at height={height_mm}mm produced no glyph polygons",
            cfg,
            warnings,
        )
        return GcodeOutput(lines=[], warnings=warnings)

    from shapely.affinity import translate as _translate

    x0, y0 = position
    glyphs = [_translate(g, xoff=x0, yoff=y0) for g in glyphs]

    is_default_tool = tool is None
    tool = tool or load_tool(DEFAULT_TOOL_ID)
    material = material or load_material(DEFAULT_MATERIAL_ID)
    _check_tool_for_op(
        tool, material, "profile_cut", depth_mm, is_default_tool, cfg, warnings
    )

    opposite: Side = (
        "inside" if side == "outside" else ("outside" if side == "inside" else "on")
    )

    # One header for the whole job; per-ring profile_cut calls have their
    # own headers which we strip (keep their motion + section comments).
    header = _spindle_header("text_profile", tool, material, cfg, depth_mm)
    header.append(f"; text={text!r}  height={height_mm}mm  font={Path(font_path).name}")
    header.append(
        f"; {len(glyphs)} glyph polygon(s), side={side}, font_path={font_path}"
    )
    header.append("")

    body: list[str] = []
    for gi, glyph in enumerate(glyphs, start=1):
        body.append(f"; ===== glyph {gi}/{len(glyphs)} =====")
        outer_poly = Polygon(glyph.exterior)
        out = profile_cut(
            outer_poly, depth_mm, tool=tool, material=material, side=side, cfg=cfg
        )
        body.extend(_strip_spindle_wrappers(out.lines))
        warnings.extend(out.warnings)
        for hi, interior in enumerate(glyph.interiors, start=1):
            body.append(f"; --- glyph {gi} counter {hi}/{len(glyph.interiors)} ---")
            inner_poly = Polygon(interior)
            out = profile_cut(
                inner_poly,
                depth_mm,
                tool=tool,
                material=material,
                side=opposite,
                cfg=cfg,
            )
            body.extend(_strip_spindle_wrappers(out.lines))
            warnings.extend(out.warnings)

    return GcodeOutput(
        lines=header + body + _spindle_footer(cfg),
        warnings=warnings,
    )


def _strip_spindle_wrappers(lines: list[str]) -> list[str]:
    """Drop the per-op _spindle_header/footer wrapper from a sub-call's
    output, keeping only the motion + section comments. We rely on a
    blank line separating the header block from the toolpath, and on
    _spindle_footer being a small known suffix."""
    # Find first blank line — header ends right before the motion starts.
    try:
        start = lines.index("", 0) + 1
    except ValueError:
        start = 0
    # Trim the footer: _spindle_footer emits "M5", "G0 Z<safe>", "M30"
    # (or similar). Conservatively drop anything from the LAST M5 onward
    # if it's near the end of the output.
    end = len(lines)
    for i in range(len(lines) - 1, max(start, len(lines) - 5), -1):
        if lines[i].startswith("M5"):
            end = i
            break
    return lines[start:end]


# ---------------------------------------------------------------------------
# Operation: chamfer_edge (V-bit perimeter chamfer)
# ---------------------------------------------------------------------------


def chamfer_edge(
    polygon: BaseGeometry,
    chamfer_depth_mm: float,
    tool: Tool | None = None,
    material: Material | None = None,
    cfg: CamConfig | None = None,
) -> GcodeOutput:
    """Cut a V-bit chamfer along the outer perimeter of a polygon.

    The tool follows the polygon's exterior at the specified chamfer depth.
    The chamfer's horizontal width is `chamfer_depth_mm * tan(angle/2)` for a
    V-bit with the given included angle — so a 60° bit at 1mm depth produces
    a ~0.577mm chamfer width on each side.

    Geometry:
      Tool centerline traces the polygon exterior (the tool's tip rides
      along the edge, not offset). Because a V-bit's cutting flank widens
      with depth, the resulting chamfer extends both INWARD into the part
      AND OUTWARD into the surrounding stock by chamfer_width on each side.
      Caller is responsible for having already cut the part free (or having
      a profile_cut planned afterwards) so the outward portion isn't
      cutting into adjacent stock you want to keep.

    Warning categories:
      - tool.type != v_bit (wrong tool — flat endmills make square-bottom
        rabbets, not chamfers)
      - chamfer_width > polygon's smallest wall thickness (cuts through
        the part interior)
      - tool.angle_deg missing on a v_bit (can't compute width)
      - default-tool, chipload, flute-length warnings (shared)
    """
    cfg = cfg or CamConfig()
    is_default_tool = tool is None
    tool = tool or load_tool(DEFAULT_TOOL_ID)
    material = material or load_material(DEFAULT_MATERIAL_ID)
    warnings: list[str] = []

    # Tool-type check: must be a v_bit to actually produce a chamfer.
    if is_default_tool:
        _warn_or_fail(
            f"using default tool '{tool.id}' for chamfer_edge. "
            f"{_DEFAULT_TOOL_WARNING_IMPLICATIONS['chamfer_edge']} "
            f"Pass tool=... explicitly to suppress this warning.",
            cfg,
            warnings,
        )
    if tool.type != "v_bit":
        _warn_or_fail(
            f"chamfer_edge with type={tool.type}: wrong tool. A flat or ball "
            f"endmill cuts a square / round rabbet, not a chamfer. Use "
            f"type=v_bit for a true angled chamfer.",
            cfg,
            warnings,
        )
    # Width computation requires the bit angle. If missing, skip width-check
    # and warn — the cut still proceeds at the requested depth.
    chamfer_width_mm: float | None = None
    if tool.type == "v_bit" and tool.angle_deg is not None:
        import math

        half_angle = math.radians(tool.angle_deg / 2.0)
        chamfer_width_mm = chamfer_depth_mm * math.tan(half_angle)
        # Wall-thickness check: if the polygon has interior holes, the
        # chamfer mustn't exceed the smallest distance to any interior ring
        # or the chamfer crosses into the hole. For convex outer-only
        # polygons we can't easily bound "wall thickness", so this check
        # only fires when there are interior rings (typical for parts with
        # mounting holes near the edge).
        if hasattr(polygon, "interiors") and list(polygon.interiors):
            # Distance from each interior ring to the exterior boundary
            from shapely.geometry import LineString

            ext = LineString(polygon.exterior.coords)
            for interior in polygon.interiors:
                int_line = LineString(interior.coords)
                wall = ext.distance(int_line)
                if chamfer_width_mm >= wall:
                    _warn_or_fail(
                        f"chamfer_edge: chamfer width {chamfer_width_mm:.3f}mm "
                        f"≥ wall thickness {wall:.3f}mm between exterior and "
                        f"an interior hole. The chamfer will cut INTO the "
                        f"hole. Reduce chamfer_depth_mm or pick a steeper "
                        f"V-bit.",
                        cfg,
                        warnings,
                    )
                    break
    elif tool.type == "v_bit" and tool.angle_deg is None:
        _warn_or_fail(
            f"chamfer_edge: tool '{tool.id}' is v_bit but has no angle_deg; "
            f"cannot compute chamfer width. Add angle_deg to "
            f"profiles/tools.yaml.",
            cfg,
            warnings,
        )
    if tool.flute_length_mm is not None and chamfer_depth_mm > tool.flute_length_mm:
        _warn_or_fail(
            f"chamfer depth {chamfer_depth_mm}mm exceeds tool flute length "
            f"{tool.flute_length_mm}mm; shank will rub.",
            cfg,
            warnings,
        )
    if material.chipload_for(tool.id) is None:
        _warn_or_fail(
            f"material '{material.id}' has no chipload entry for tool "
            f"'{tool.id}'; feed rate will fall back to a conservative default.",
            cfg,
            warnings,
        )

    # The path is the polygon's exterior: tool centerline rides the edge.
    if polygon.is_empty:
        _warn_or_fail(
            "chamfer_edge: polygon is empty; no output emitted.",
            cfg,
            warnings,
        )
        return GcodeOutput(lines=[], warnings=warnings)

    if isinstance(polygon, MultiPolygon):
        polys = list(polygon.geoms)
    else:
        polys = [polygon]

    feed = _derive_feed(tool, material, cfg)
    plunge_f = _plunge_feed(feed, tool, cfg)

    lines = _spindle_header("chamfer_edge", tool, material, cfg, chamfer_depth_mm)
    width_note = (
        f"; chamfer width = {chamfer_width_mm:.3f}mm per side (angle={tool.angle_deg}°)"
        if chamfer_width_mm is not None
        else "; chamfer width unknown (tool.angle_deg missing)"
    )
    lines.append(width_note)
    lines.append(
        f"; single-pass at Z=-{chamfer_depth_mm:.3f}, feed={feed}, plunge={plunge_f}"
    )
    lines.append("")

    for path_idx, poly in enumerate(polys, start=1):
        coords = list(poly.exterior.coords)
        if len(coords) < 3:
            continue
        x0, y0 = coords[0]
        lines.append(f"; --- path {path_idx}/{len(polys)} ({len(coords)} pts) ---")
        lines.append(f"G0 Z{cfg.safe_z_mm:.3f}")
        lines.append(f"G0 X{x0:.3f} Y{y0:.3f}")
        lines.append(f"G1 Z{-chamfer_depth_mm:.3f} F{plunge_f}")
        for x, y in coords[1:]:
            lines.append(f"G1 X{x:.3f} Y{y:.3f} F{feed}")
        lines.append(f"G0 Z{cfg.safe_z_mm:.3f}")
        lines.append("")

    lines.extend(_spindle_footer(cfg))
    return GcodeOutput(lines=lines, warnings=warnings)


# ---------------------------------------------------------------------------
# Operation: profile_cut_with_tabs
# ---------------------------------------------------------------------------


def _perimeter_tab_spans(
    perimeter_pts: list[tuple[float, float]],
    tab_count: int,
    tab_width_mm: float,
) -> list[tuple[float, float]]:
    """Compute (start_arclen, end_arclen) intervals along the perimeter where
    tabs should sit. Tabs are distributed evenly: starting at offset
    (perimeter_len / tab_count) / 2, then every (perimeter_len / tab_count)
    after that. Each tab is tab_width_mm wide along the perimeter."""
    if tab_count <= 0:
        return []
    # Compute cumulative arc length
    cum = [0.0]
    for i in range(1, len(perimeter_pts)):
        x0, y0 = perimeter_pts[i - 1]
        x1, y1 = perimeter_pts[i]
        d = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        cum.append(cum[-1] + d)
    total = cum[-1]
    if total <= 0:
        return []
    spacing = total / tab_count
    spans: list[tuple[float, float]] = []
    for i in range(tab_count):
        center = (i + 0.5) * spacing
        start = (center - tab_width_mm / 2) % total
        end = (center + tab_width_mm / 2) % total
        spans.append((start, end))
    return spans


def _arclen_at_point(cum_arclens: list[float], idx: int, segment_t: float) -> float:
    """Helper: arc length up to perimeter_pts[idx] + segment_t * length to
    perimeter_pts[idx+1]. segment_t in [0, 1]."""
    base = cum_arclens[idx]
    if idx + 1 >= len(cum_arclens):
        return base
    seg = cum_arclens[idx + 1] - cum_arclens[idx]
    return base + segment_t * seg


def profile_cut_with_tabs(
    polygon: BaseGeometry,
    depth_mm: float,
    tab_count: int = 4,
    tab_width_mm: float = 4.0,
    tab_height_mm: float = 1.0,
    tool: Tool | None = None,
    material: Material | None = None,
    side: Side = "outside",
    cfg: CamConfig | None = None,
) -> GcodeOutput:
    """profile_cut variant leaving N small bridges holding the part to stock.

    All passes except the FINAL one cut the full perimeter normally. On the
    final (deepest) pass, the tool lifts Z to -(depth - tab_height_mm) for
    each tab span and descends back to -depth for the rest of the perimeter.
    The result: tabs of height tab_height_mm holding the part in place after
    cutting is complete.

    Tabs are distributed evenly along the perimeter.

    After cutting: snap the part free with a thin chisel or saw, then sand
    the tab stubs flush with a sanding block.

    Warning categories:
      - tab_count * tab_width_mm >= perimeter (no room for the part itself)
      - tab_height_mm > depth_mm / 3 (taller tabs are harder to clean up)
      - tab_count < 3 (risky on large parts)
      - shares warnings with profile_cut (default tool, ball/v/drill, depth>flute)
    """
    cfg = cfg or CamConfig()
    is_default_tool = tool is None
    tool = tool or load_tool(DEFAULT_TOOL_ID)
    material = material or load_material(DEFAULT_MATERIAL_ID)
    warnings: list[str] = []

    # Reuse the standard profile_cut tool checks but under our op name so
    # the user knows which op is complaining.
    if is_default_tool:
        _warn_or_fail(
            f"using default tool '{tool.id}' for profile_cut_with_tabs. "
            f"{_DEFAULT_TOOL_WARNING_IMPLICATIONS['profile_cut_with_tabs']} "
            f"Pass tool=... explicitly to suppress this warning.",
            cfg,
            warnings,
        )
    if tool.type == "ball_endmill":
        _warn_or_fail(
            f"profile_cut_with_tabs with type=ball_endmill: rounded bottom "
            f"means tabs won't have square shoulders. Use a flat endmill.",
            cfg,
            warnings,
        )
    elif tool.type == "v_bit":
        _warn_or_fail(
            f"profile_cut_with_tabs with type=v_bit: angled walls; tabs will "
            f"be weak / asymmetric. Use a flat endmill.",
            cfg,
            warnings,
        )
    elif tool.type == "drill":
        _warn_or_fail(
            f"profile_cut_with_tabs with type=drill: drills don't side-cut. "
            f"Use an endmill.",
            cfg,
            warnings,
        )
    if tool.flute_length_mm is not None and depth_mm > tool.flute_length_mm:
        _warn_or_fail(
            f"requested depth {depth_mm}mm exceeds tool flute length "
            f"{tool.flute_length_mm}mm; shank will rub.",
            cfg,
            warnings,
        )
    if material.chipload_for(tool.id) is None:
        _warn_or_fail(
            f"material '{material.id}' has no chipload entry for tool "
            f"'{tool.id}'; feed rate will fall back to a conservative default.",
            cfg,
            warnings,
        )

    # Tab-specific warnings
    if tab_count < 3:
        _warn_or_fail(
            f"profile_cut_with_tabs: tab_count={tab_count} < 3 is risky on "
            f"larger parts. The part can pivot around a single tab. Consider "
            f"≥3 tabs unless the part is very small.",
            cfg,
            warnings,
        )
    if tab_height_mm > depth_mm / 3:
        _warn_or_fail(
            f"profile_cut_with_tabs: tab_height_mm={tab_height_mm} > depth/3="
            f"{depth_mm / 3:.2f}. Taller tabs are stronger but much harder to "
            f"sand flush after the part is released. Consider ≤depth/3.",
            cfg,
            warnings,
        )
    if tab_height_mm >= depth_mm:
        _warn_or_fail(
            f"profile_cut_with_tabs: tab_height_mm={tab_height_mm} >= "
            f"depth_mm={depth_mm}. Tabs would be full-thickness; the part is "
            f"not actually cut free. Reduce tab_height_mm.",
            cfg,
            warnings,
        )

    # Build toolpath polygon (same offset logic as profile_cut)
    if side == "outside":
        path_geom = polygon.buffer(tool.radius_mm)
    elif side == "inside":
        path_geom = polygon.buffer(-tool.radius_mm)
    elif side == "on":
        path_geom = polygon
    else:
        raise SystemExit(
            f"profile_cut_with_tabs: side must be outside|inside|on, got {side!r}"
        )

    if path_geom.is_empty:
        _warn_or_fail(
            f"profile_cut_with_tabs produced empty toolpath; skipping.",
            cfg,
            warnings,
        )
        return GcodeOutput(lines=[], warnings=warnings)

    if isinstance(path_geom, MultiPolygon):
        polys = list(path_geom.geoms)
    else:
        polys = [path_geom]

    feed = _derive_feed(tool, material, cfg)
    plunge_f = _plunge_feed(feed, tool, cfg)
    step_down = _derive_step_down(tool, material, cfg)
    n_passes = max(1, int(-(-depth_mm // step_down)))

    lines = _spindle_header("profile_cut_with_tabs", tool, material, cfg, depth_mm)
    lines.append(
        f"; {n_passes} pass(es) at step_down={step_down}mm, feed={feed}, plunge={plunge_f}"
    )
    lines.append(
        f"; side={side}, paths={len(polys)}, "
        f"tabs={tab_count}x{tab_width_mm}mm wide x {tab_height_mm}mm tall"
    )
    lines.append("")

    tab_z = -(depth_mm - tab_height_mm)  # Z at top of tab

    for path_idx, poly in enumerate(polys, start=1):
        coords = list(poly.exterior.coords)
        if len(coords) < 3:
            continue
        # Tab-span check against perimeter length
        total_perim = 0.0
        for i in range(1, len(coords)):
            x0, y0 = coords[i - 1]
            x1, y1 = coords[i]
            total_perim += ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        if tab_count * tab_width_mm >= total_perim:
            _warn_or_fail(
                f"profile_cut_with_tabs path {path_idx}: total tab width "
                f"{tab_count * tab_width_mm}mm >= perimeter {total_perim:.1f}mm. "
                f"No room for actual cuts between tabs. Reduce tab_count or "
                f"tab_width_mm.",
                cfg,
                warnings,
            )

        spans = _perimeter_tab_spans(coords, tab_count, tab_width_mm)

        x0, y0 = coords[0]
        lines.append(f"; --- path {path_idx}/{len(polys)} ({len(coords)} pts) ---")
        lines.append(f"G0 Z{cfg.safe_z_mm:.3f}")
        lines.append(f"G0 X{x0:.3f} Y{y0:.3f}")
        for pass_n in range(1, n_passes + 1):
            cur_z = -min(pass_n * step_down, depth_mm)
            is_final = pass_n == n_passes
            if n_passes > 1:
                lines.append(f"; pass {pass_n} of {n_passes} (Z={cur_z:.3f})")
            lines.append(f"G1 Z{cur_z:.3f} F{plunge_f}")

            if not is_final or not spans:
                # Normal full-perimeter pass.
                for x, y in coords[1:]:
                    lines.append(f"G1 X{x:.3f} Y{y:.3f} F{feed}")
            else:
                # Final pass: walk perimeter, lift Z to tab_z over tab spans.
                # The perimeter has straight runs with no intermediate
                # vertices, so we must interpolate XY points at tab span
                # boundaries to actually emit the Z lifts where intended.
                cum = [0.0]
                for i in range(1, len(coords)):
                    cx0, cy0 = coords[i - 1]
                    cx1, cy1 = coords[i]
                    cum.append(cum[-1] + ((cx1 - cx0) ** 2 + (cy1 - cy0) ** 2) ** 0.5)
                total = cum[-1]

                # Enrich the arc-length sequence with tab boundary
                # arclens so every tab edge gets a vertex with the right Z.
                breakpoints: set[float] = set(cum)
                for s, e in spans:
                    breakpoints.add(s % total)
                    breakpoints.add(e % total)
                ordered = sorted(breakpoints)

                def point_at_arclen(a: float) -> tuple[float, float]:
                    a = a % total
                    # Binary search for segment idx
                    lo, hi = 0, len(cum) - 1
                    while lo < hi - 1:
                        mid = (lo + hi) // 2
                        if cum[mid] <= a:
                            lo = mid
                        else:
                            hi = mid
                    seg_len = cum[lo + 1] - cum[lo]
                    if seg_len <= 0:
                        return coords[lo]
                    t = (a - cum[lo]) / seg_len
                    x0, y0 = coords[lo]
                    x1, y1 = coords[lo + 1]
                    return (x0 + t * (x1 - x0), y0 + t * (y1 - y0))

                def in_tab(arclen: float) -> bool:
                    a = arclen % total
                    for s, e in spans:
                        if s <= e:
                            if s <= a <= e:
                                return True
                        else:
                            # span wraps around end
                            if a >= s or a <= e:
                                return True
                    return False

                lines.append(
                    f"; final pass with {len(spans)} tab(s); "
                    f"Z lifts to {tab_z:.3f} over each tab"
                )
                current_z = cur_z
                for arclen in ordered:
                    if arclen <= 0:
                        continue  # starting point already plunged
                    # in_tab on the MIDPOINT of [prev, this] arclen would be
                    # the "right" answer for the segment we just traversed,
                    # but emitting the lift at the boundary itself works
                    # fine because the boundary is repeated as both
                    # "end-of-cut" and "start-of-tab" arclens (they're equal
                    # so visited once in the set). Lift Z just before
                    # entering / exiting a tab span.
                    target_z = tab_z if in_tab(arclen) else cur_z
                    if target_z != current_z:
                        lines.append(f"G1 Z{target_z:.3f} F{plunge_f}")
                        current_z = target_z
                    x, y = point_at_arclen(arclen)
                    lines.append(f"G1 X{x:.3f} Y{y:.3f} F{feed}")
                # End: leave Z at cut depth, will be lifted to safe Z next.

        lines.append(f"G0 Z{cfg.safe_z_mm:.3f}")
        lines.append("")

    lines.extend(_spindle_footer(cfg))
    return GcodeOutput(lines=lines, warnings=warnings)


# ---------------------------------------------------------------------------
# Operation: slot_mill (stadium-shape pocket from p1 → p2)
# ---------------------------------------------------------------------------


def _stadium_polygon(
    p1: tuple[float, float],
    p2: tuple[float, float],
    width_mm: float,
    segments: int = 32,
) -> Polygon:
    """Build a stadium (rectangle with semicircular ends) of the given width
    centered on the line from p1 to p2.

    Implementation: buffer a LineString from p1→p2 by width/2 with round caps.
    Shapely's LineString.buffer(cap_style='round', join_style='round') does
    exactly this and produces a clean stadium shape.
    """
    from shapely.geometry import LineString

    line = LineString([p1, p2])
    radius = width_mm / 2
    # cap_style=1 = round, quad_segs = quarter-circle subdivision count
    return line.buffer(radius, cap_style=1, quad_segs=max(4, segments // 4))


def slot_mill(
    p1: tuple[float, float],
    p2: tuple[float, float],
    width_mm: float,
    depth_mm: float,
    tool: Tool | None = None,
    material: Material | None = None,
    cfg: CamConfig | None = None,
) -> GcodeOutput:
    """Cut an elongated stadium-shape slot (oversized hole for mounting-position
    adjustment).

    Geometry: rectangle of length |p2 - p1| and width `width_mm`, with
    semicircular ends, centered on the line p1→p2. Built as a shapely Polygon
    and dispatched to pocket_mill for the actual clearance.

    Warning categories:
      - width_mm < tool.diameter_mm (slot narrower than tool — impossible)
      - width_mm > 2 * tool.diameter_mm (requires multi-pass clearance —
        OK, pocket_mill handles it, but warn so caller knows)
      - shares warnings with pocket_mill (default tool, deep+flat, ball/v/drill)
    """
    cfg = cfg or CamConfig()
    is_default_tool = tool is None
    tool_resolved = tool or load_tool(DEFAULT_TOOL_ID)
    material_resolved = material or load_material(DEFAULT_MATERIAL_ID)
    warnings: list[str] = []

    # Width / tool relationship checks BEFORE delegating to pocket_mill
    if width_mm < tool_resolved.diameter_mm:
        _warn_or_fail(
            f"slot_mill: width_mm={width_mm} < tool diameter "
            f"{tool_resolved.diameter_mm}mm. Cannot cut a slot narrower than "
            f"the tool. Pick a smaller tool or widen the slot.",
            cfg,
            warnings,
        )
        return GcodeOutput(lines=[], warnings=warnings)
    if width_mm > 2 * tool_resolved.diameter_mm:
        _warn_or_fail(
            f"slot_mill: width_mm={width_mm} > 2x tool diameter "
            f"{2 * tool_resolved.diameter_mm}mm. Requires multi-pass clearance "
            f"(pocket_mill will handle it via offset rings, but expect multiple "
            f"side-by-side passes per Z step).",
            cfg,
            warnings,
        )

    # Build stadium and delegate to pocket_mill, but we want our own header
    # so the comment says "slot_mill" not "pocket_mill". Easiest: call
    # pocket_mill, then patch the header line.
    stadium = _stadium_polygon(p1, p2, width_mm)
    if stadium.is_empty or stadium.area <= 0:
        _warn_or_fail(
            f"slot_mill: stadium geometry is degenerate (p1=p2? width=0?); no output.",
            cfg,
            warnings,
        )
        return GcodeOutput(lines=[], warnings=warnings)

    # Override the default-tool warning so the user sees the right op name.
    # We pass the explicit tool/material to pocket_mill to suppress its own
    # default-tool warning, then re-emit ours under slot_mill.
    if is_default_tool:
        _warn_or_fail(
            f"using default tool '{tool_resolved.id}' for slot_mill. "
            f"{_DEFAULT_TOOL_WARNING_IMPLICATIONS['slot_mill']} "
            f"Pass tool=... explicitly to suppress this warning.",
            cfg,
            warnings,
        )

    inner = pocket_mill(
        stadium,
        depth_mm=depth_mm,
        tool=tool_resolved,
        material=material_resolved,
        cfg=cfg,
    )

    # Re-label the header so output advertises slot_mill.
    patched_lines: list[str] = []
    for ln in inner.lines:
        if ln.startswith("; pocket_mill:"):
            patched_lines.append(
                ln.replace("; pocket_mill:", "; slot_mill:", 1)
                + f"  [via pocket_mill, stadium p1={p1} p2={p2} width={width_mm}mm]"
            )
        else:
            patched_lines.append(ln)

    warnings.extend(inner.warnings)
    return GcodeOutput(lines=patched_lines, warnings=warnings)


# ---------------------------------------------------------------------------
# Operation: face_mill (zig-zag raster surfacing)
# ---------------------------------------------------------------------------


def face_mill(
    bounds_polygon: BaseGeometry,
    depth_mm: float,
    tool: Tool | None = None,
    material: Material | None = None,
    stepover_factor: float = 0.7,
    cfg: CamConfig | None = None,
) -> GcodeOutput:
    """Clean the top surface of stock to a uniform Z via zig-zag raster.

    Used to flatten warped material or skim a thin layer before precision
    work. The tool covers `bounds_polygon` with parallel zigzag passes along
    the X axis, spaced by `stepover_factor * tool.diameter_mm`.

    Why zigzag instead of pocket_mill's spiral: face-milling wants
    predictable parallel scanlines so chip evacuation is uniform across the
    surface. Spirals leave a center-out chip pattern that's poor for
    surface flatness.

    Warning categories:
      - tool.type != flat_endmill (ball/v leave non-flat surface)
      - depth_mm > 1mm in single pass (chip load risk; auto multi-passes if
        material doc_fraction yields that, but warn anyway)
      - stepover_factor > 0.8 (visible scallop ridges)
      - default-tool, chipload, flute-length warnings (shared)
    """
    cfg = cfg or CamConfig()
    is_default_tool = tool is None
    tool = tool or load_tool(DEFAULT_TOOL_ID)
    material = material or load_material(DEFAULT_MATERIAL_ID)
    warnings: list[str] = []

    if is_default_tool:
        _warn_or_fail(
            f"using default tool '{tool.id}' for face_mill. "
            f"{_DEFAULT_TOOL_WARNING_IMPLICATIONS['face_mill']} "
            f"Pass tool=... explicitly to suppress this warning.",
            cfg,
            warnings,
        )
    if tool.type == "ball_endmill":
        _warn_or_fail(
            f"face_mill with type=ball_endmill: ball-end leaves curved "
            f"scallops between passes (non-flat surface). Use a flat endmill.",
            cfg,
            warnings,
        )
    elif tool.type == "v_bit":
        _warn_or_fail(
            f"face_mill with type=v_bit: V-bit leaves a non-flat (grooved) "
            f"surface. Use a flat endmill.",
            cfg,
            warnings,
        )
    elif tool.type == "drill":
        _warn_or_fail(
            f"face_mill with type=drill: drills don't side-cut. Use a flat endmill.",
            cfg,
            warnings,
        )
    if depth_mm > 1.0:
        _warn_or_fail(
            f"face_mill: depth_mm={depth_mm} > 1mm in single op. The op will "
            f"multi-pass via material.doc_fraction, but for surfacing you "
            f"usually want shallow skims (~0.3-0.5mm) and multiple separate "
            f"face_mill calls. Heavy face cuts overload the tool laterally.",
            cfg,
            warnings,
        )
    if stepover_factor > 0.8:
        _warn_or_fail(
            f"face_mill: stepover_factor={stepover_factor} > 0.8. Visible "
            f"scallop ridges will remain between passes. Use 0.5-0.7 for a "
            f"clean surface.",
            cfg,
            warnings,
        )
    if not (0 < stepover_factor <= 1.0):
        raise SystemExit(
            f"face_mill: stepover_factor must be in (0, 1], got {stepover_factor}"
        )
    if tool.flute_length_mm is not None and depth_mm > tool.flute_length_mm:
        _warn_or_fail(
            f"face_mill depth {depth_mm}mm exceeds flute length "
            f"{tool.flute_length_mm}mm; shank will rub.",
            cfg,
            warnings,
        )
    if material.chipload_for(tool.id) is None:
        _warn_or_fail(
            f"material '{material.id}' has no chipload entry for tool "
            f"'{tool.id}'; feed rate will fall back to a conservative default.",
            cfg,
            warnings,
        )

    if bounds_polygon.is_empty:
        _warn_or_fail(
            "face_mill: bounds_polygon is empty; no output.",
            cfg,
            warnings,
        )
        return GcodeOutput(lines=[], warnings=warnings)

    # Inset bounds by tool_radius so the cutter EDGE stays within the
    # requested region (otherwise the cutter overshoots outside the bounds
    # by tool_radius on each side).
    inset = bounds_polygon.buffer(-tool.radius_mm)
    if inset.is_empty:
        _warn_or_fail(
            f"face_mill: bounds_polygon is smaller than 2x tool diameter "
            f"({2 * tool.diameter_mm}mm); cannot fit even one pass. Use a "
            f"smaller tool.",
            cfg,
            warnings,
        )
        return GcodeOutput(lines=[], warnings=warnings)

    minx, miny, maxx, maxy = inset.bounds
    stepover = stepover_factor * tool.diameter_mm
    feed = _derive_feed(tool, material, cfg)
    plunge_f = _plunge_feed(feed, tool, cfg)
    step_down = _derive_step_down(tool, material, cfg)
    n_z_passes = max(1, int(-(-depth_mm // step_down)))

    # Generate Y scanlines top→bottom (or bottom→top, doesn't matter).
    # Each scanline runs left→right, then the next runs right→left, etc.
    scanlines: list[tuple[float, float, float]] = []  # (y, x_start, x_end)
    y = miny
    direction = 1  # 1 = left→right, -1 = right→left
    while y <= maxy + 1e-9:
        if direction == 1:
            scanlines.append((y, minx, maxx))
        else:
            scanlines.append((y, maxx, minx))
        y += stepover
        direction *= -1
    if scanlines and abs(scanlines[-1][0] - maxy) > 1e-6:
        # Ensure final pass at the far edge so we don't leave a strip uncut
        if direction == 1:
            scanlines.append((maxy, minx, maxx))
        else:
            scanlines.append((maxy, maxx, minx))

    lines = _spindle_header("face_mill", tool, material, cfg, depth_mm)
    lines.append(
        f"; {n_z_passes} Z-pass(es) at step_down={step_down}mm, "
        f"feed={feed}, plunge={plunge_f}"
    )
    lines.append(
        f"; {len(scanlines)} scanline(s) per Z, stepover={stepover}mm "
        f"({stepover_factor * 100:.0f}% of {tool.diameter_mm}mm dia)"
    )
    lines.append(
        f"; bounds (inset by tool radius): X=[{minx:.3f}, {maxx:.3f}] "
        f"Y=[{miny:.3f}, {maxy:.3f}]"
    )
    lines.append("")

    for pass_n in range(1, n_z_passes + 1):
        cur_z = -min(pass_n * step_down, depth_mm)
        lines.append(f"; --- Z pass {pass_n}/{n_z_passes} at Z={cur_z:.3f} ---")
        for i, (sy, sx, ex) in enumerate(scanlines, start=1):
            lines.append(f"; scanline {i}/{len(scanlines)} y={sy:.3f}")
            if i == 1:
                # Initial: rapid to start, then plunge.
                lines.append(f"G0 Z{cfg.safe_z_mm:.3f}")
                lines.append(f"G0 X{sx:.3f} Y{sy:.3f}")
                lines.append(f"G1 Z{cur_z:.3f} F{plunge_f}")
            else:
                # Subsequent: G1 to next scanline start (still at cut depth)
                lines.append(f"G1 Y{sy:.3f} F{feed}")
            lines.append(f"G1 X{ex:.3f} F{feed}")
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
