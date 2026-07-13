#!/usr/bin/env python3
"""jigsawzall — single canonical CLI for the wooden name-puzzle tool.

Built on the productionized geometry / emitter modules.

Subcommands:
  preview   — render a verification image (no GCode)
  cut       — emit cut GCode (--size small or full)

Validator-clean GRBL laser G-code (static M3 constant power).

Examples:
  jigsaw.py preview --word NORA
  jigsaw.py cut --size full --word NORA --seed 7 --material mdf_3mm
  jigsaw.py cut --size small --word N --seed 7
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import MultiPolygon

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from emitter import (  # noqa: E402
    WARMUP_MS,
    emit_cut_gcode_full,
    emit_cut_gcode_simple,
    load_material,
)
from geometry import (  # noqa: E402
    banner_puzzle_config,
    fit_config,
    full_puzzle_config,
    generate_pieces,
    micro_puzzle_config,
    mini_puzzle_config,
    small_puzzle_config,
)

BUILD_DIR = SCRIPT_DIR / "build"
FIG_DIR = SCRIPT_DIR / "figs"


def _config_for_size(size: str):
    if size == "small":
        return small_puzzle_config()
    if size == "full":
        return full_puzzle_config()
    if size == "micro":
        return micro_puzzle_config()
    if size == "mini":
        return mini_puzzle_config()
    if size == "banner":
        return banner_puzzle_config()
    raise SystemExit(
        f"unknown --size {size!r} "
        "(expected 'small', 'mini', 'banner', 'micro', or 'full')"
    )


def _apply_size_overrides(cfg, args):
    """Return cfg with panel/piece dimensions overridden by any CLI flags that
    were provided (--panel-mm/--panel-h-mm/--piece-mm). Used to flex the banner
    preset to a specific name/stock without adding a new size preset. replace()
    re-runs __post_init__ so the derived cols/cell_px stay consistent."""
    from dataclasses import replace

    over = {}
    if getattr(args, "panel_mm", None) is not None:
        over["panel_mm"] = args.panel_mm
    if getattr(args, "panel_h_mm", None) is not None:
        over["panel_h_mm"] = args.panel_h_mm
    if getattr(args, "piece_mm", None) is not None:
        over["piece_mm"] = args.piece_mm
    # Fat capsule tabs: mm -> px (px_per_mm is fixed per config, default 5).
    if getattr(args, "tab_stem_mm", None) is not None:
        over["tab_stem_w_px"] = args.tab_stem_mm * cfg.px_per_mm
    if getattr(args, "tab_bulb_elong_mm", None) is not None:
        over["tab_bulb_elong_px"] = args.tab_bulb_elong_mm * cfg.px_per_mm
    if getattr(args, "letter_clearance_mm", None) is not None:
        over["letter_clearance_mm"] = args.letter_clearance_mm
    if getattr(args, "banner_h_mm", None) is not None:
        over["banner_target_h_mm"] = args.banner_h_mm
    if getattr(args, "font", None) is not None:
        over["font_path"] = args.font
    # Vertex-grid: opt-in letter-anchored seam layout (reuses tabs/pockets/emit).
    if getattr(args, "vertex_grid", False):
        over["vertex_grid"] = True
        over["letter_aligned_grid"] = False
        over["fit_to_text"] = True  # it's a name-banner layout: size panel to text
    return replace(cfg, **over) if over else cfg


def _add_size_override_flags(sub):
    """Attach the shared banner-sizing override flags to a subparser."""
    sub.add_argument(
        "--panel-mm",
        type=float,
        default=None,
        help="override panel width bound (mm); flexes the banner to a name/stock",
    )
    sub.add_argument(
        "--panel-h-mm",
        type=float,
        default=None,
        help="override panel height bound (mm)",
    )
    sub.add_argument(
        "--piece-mm",
        type=float,
        default=None,
        help="override nominal cell width (mm); sets the width-quantization step",
    )
    sub.add_argument(
        "--tab-stem-mm",
        type=float,
        default=None,
        help="fat capsule tabs: neck width (mm), e.g. 5 (~1.5-2x stock thickness)",
    )
    sub.add_argument(
        "--tab-bulb-elong-mm",
        type=float,
        default=None,
        help="fat capsule tabs: bulb center-to-center elongation (mm); bulb width "
        "= this + 2*bulb radius. Pair with --tab-stem-mm so the bulb still locks",
    )
    sub.add_argument(
        "--letter-clearance-mm",
        type=float,
        default=None,
        help="minimum wall (mm) a tab keeps from any letter = the material bridge "
        "left beside it. Raise to kill brittle thin bridges (costs dropped tabs)",
    )
    sub.add_argument(
        "--banner-h-mm",
        type=float,
        default=None,
        help="target banner height (mm) for a fit_to_text banner; grows the "
        "top/bottom rows so tabs have more room off the borders",
    )
    sub.add_argument(
        "--font",
        default=None,
        help="letter font: alias (bold/black/impact/narrow) or a .ttf path; "
        "default is 'black' (Arial Black), which cuts cleaner in wood than bold",
    )
    sub.add_argument(
        "--vertex-grid",
        dest="vertex_grid",
        action="store_true",
        help="opt-in vertex-grid layout: background tiled with letter-anchored "
        "seams (perpendicular caps + S-curve gap seams carrying the tab) instead "
        "of the rectangular grid. Same tabs/pockets/GCode as the default path",
    )


def _apply_origin(cfg, origin: str):
    """Mutate cfg.origin_offset_mm based on the --origin flag."""
    if origin == "corner":
        return
    if origin == "center":
        cfg.origin_offset_mm = (cfg.panel_mm / 2, cfg.panel_mm / 2)
        return
    raise SystemExit(f"unknown --origin {origin!r} (expected 'corner' or 'center')")


def _emit_cut_for(
    pieces,
    material,
    cfg,
    word,
    size,
    mode="static",
    feed_override=None,
    min_segment_mm=0.0,
    power_percent=None,
    ramp_ms=WARMUP_MS,
):
    if size == "small":
        return emit_cut_gcode_simple(
            pieces,
            material,
            cfg,
            word,
            mode=mode,
            feed_override=feed_override,
            min_segment_mm=min_segment_mm,
            power_percent=power_percent,
        )
    return emit_cut_gcode_full(
        pieces,
        material,
        cfg,
        word,
        mode=mode,
        feed_override=feed_override,
        min_segment_mm=min_segment_mm,
        power_percent=power_percent,
        ramp_ms=ramp_ms,
    )


# ---------------------------------------------------------------------------
# Preview — verification diagram (no GCode)
# ---------------------------------------------------------------------------


_WOOD_LIGHT = (228, 204, 168)
_LETTER_FILL = (200, 90, 90)
_CELL_FILL_BASE = (210, 215, 230)
_CUT_LINE = (40, 30, 20)


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


def _pastel(i: int, total: int) -> tuple[int, int, int]:
    """Distinct pastel from a hue wheel."""
    h = (i * 360 / max(total, 1)) % 360
    s = 0.35 if i % 2 == 0 else 0.45
    v = 0.96 if i % 3 == 0 else 0.92
    c = v * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = v - c
    if h < 60:
        r, g, b = c, x, 0
    elif h < 120:
        r, g, b = x, c, 0
    elif h < 180:
        r, g, b = 0, c, x
    elif h < 240:
        r, g, b = 0, x, c
    elif h < 300:
        r, g, b = x, 0, c
    else:
        r, g, b = c, 0, x
    return (int((r + m) * 255), int((g + m) * 255), int((b + m) * 255))


def render_preview(pieces, cfg, title: str, out_path: Path, origins=None):
    """Self-contained preview render (doesn't depend on scratch).
    Pastel-colors each piece, highlights letter pieces, labels with
    serial numbers. If `origins` is given (list of (char, (x_px, y_px))),
    draws a crosshair at each glyph's auto grid-origin."""
    img = Image.new("RGB", (cfg.canvas_w_px, cfg.canvas_h_px), (255, 255, 255))
    d = ImageDraw.Draw(img)

    def draw_geom(geom, fill, outline_width=2):
        if hasattr(geom, "exterior"):
            d.polygon(list(geom.exterior.coords), fill=fill, outline=_CUT_LINE)
            for interior in geom.interiors:
                d.polygon(
                    list(interior.coords), fill=(255, 255, 255), outline=_CUT_LINE
                )
        elif isinstance(geom, MultiPolygon):
            for sub in geom.geoms:
                draw_geom(sub, fill, outline_width)

    n = len(pieces)
    for piece in pieces:
        kind = piece.get("kind", "cell")
        fill = _LETTER_FILL if kind == "letter" else _pastel(piece["serial"], n)
        draw_geom(piece["polygon"], fill)

    title_font = _load_font(36)
    d.text((cfg.margin_px, 30), title, fill=(20, 20, 20), font=title_font)

    label_font = _load_font(18)

    if origins:
        for _ch, (ox, oy) in origins:
            # vertical guide line (where the letter-aligned grid line would fall)
            d.line(
                [ox, cfg.margin_px, ox, cfg.margin_px + cfg.puzzle_h_px],
                fill=(255, 0, 255),
                width=1,
            )
            r = 9
            d.line([ox - r, oy, ox + r, oy], fill=(210, 20, 210), width=3)
            d.line([ox, oy - r, ox, oy + r], fill=(210, 20, 210), width=3)

    # Serial labels on a semi-transparent overlay: soft white backing (no hard
    # black border) so numbers stay legible over cut lines without hiding detail.
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for piece in pieces:
        cent = piece["polygon"].representative_point()
        label = str(piece["serial"])
        bbox = od.textbbox((0, 0), label, font=label_font)
        lw, lh = bbox[2] - bbox[0], bbox[3] - bbox[1]
        od.rectangle(
            [
                cent.x - lw / 2 - 4,
                cent.y - lh / 2 - 4,
                cent.x + lw / 2 + 4,
                cent.y + lh / 2 + 4,
            ],
            fill=(255, 255, 255, 165),
        )
        od.text(
            (cent.x - lw / 2 - bbox[0], cent.y - lh / 2 - bbox[1]),
            label,
            fill=(25, 25, 25, 255),
            font=label_font,
        )
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    img.save(out_path, "PNG", optimize=True)


# ---------------------------------------------------------------------------
# Cut-path previews rendered FROM the emitted GCode (PNG + SVG)
# ---------------------------------------------------------------------------


def _parse_gcode_paths(gcode: str) -> list[list[tuple[float, float]]]:
    """Extract continuous cut paths (in machine mm) from emitted GCode.

    Each path is the run of points from a G0 rapid through its following
    G1 cut moves, up to the next G0 / M5. This is exactly what the laser
    traces, so the preview shows the real toolpath — including any orphan
    fragments or fragmented segments."""
    import re

    paths: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []

    def _xy(line: str) -> tuple[float, float]:
        x = re.search(r"X(-?\d+\.?\d*)", line)
        y = re.search(r"Y(-?\d+\.?\d*)", line)
        return (float(x.group(1)) if x else 0.0, float(y.group(1)) if y else 0.0)

    for ln in gcode.splitlines():
        if ln.startswith("G0 X"):
            if len(cur) > 1:
                paths.append(cur)
            cur = [_xy(ln)]
        elif ln.startswith("G1 X"):
            cur.append(_xy(ln))
        elif ln.startswith("M5"):
            if len(cur) > 1:
                paths.append(cur)
            cur = []
    if len(cur) > 1:
        paths.append(cur)
    return paths


def render_gcode_previews(
    gcode: str, cfg, out_stem: Path, title: str
) -> tuple[Path, Path]:
    """Write a PNG and an SVG of the actual toolpath next to the gcode.

    Returns (png_path, svg_path). Lines are the cut paths; G0 rapids
    between paths are drawn faintly so re-positioning is visible."""
    paths = _parse_gcode_paths(gcode)
    pad = 10.0
    # Use the FITTED panel size (what the gcode actually spans), not the
    # panel_mm/panel_h_mm bounds — else a fit_to_text banner is drawn short and
    # flipped to the bottom of an over-tall canvas (dead space above).
    pw = cfg.puzzle_w_px / cfg.px_per_mm
    ph = cfg.puzzle_h_px / cfg.px_per_mm
    vw = pw + 2 * pad
    vh = ph + 2 * pad
    png_path = out_stem.with_suffix(".png")
    svg_path = out_stem.with_suffix(".svg")

    # --- SVG (mm units; Y flipped so up is +Y like the machine) ---
    def fy(y: float) -> float:
        return ph - y + pad

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{vw}mm" '
        f'height="{vh}mm" viewBox="0 0 {vw} {vh}">',
        f'<rect x="0" y="0" width="{vw}" height="{vh}" fill="white"/>',
        f'<rect x="{pad}" y="{pad}" width="{pw}" '
        f'height="{ph}" fill="none" stroke="#ccc" stroke-width="0.2"/>',
        f"<title>{title}</title>",
    ]
    prev_end = None
    for p in paths:
        if prev_end is not None:
            svg.append(
                f'<line x1="{prev_end[0] + pad:.3f}" y1="{fy(prev_end[1]):.3f}" '
                f'x2="{p[0][0] + pad:.3f}" y2="{fy(p[0][1]):.3f}" '
                f'stroke="#e0e0e0" stroke-width="0.1" stroke-dasharray="0.5,0.5"/>'
            )
        pts = " ".join(f"{x + pad:.3f},{fy(y):.3f}" for x, y in p)
        svg.append(
            f'<polyline points="{pts}" fill="none" stroke="#b03020" '
            f'stroke-width="0.3"/>'
        )
        prev_end = p[-1]
    svg.append("</svg>")
    svg_path.write_text("\n".join(svg))

    # --- PNG (raster, same geometry) ---
    scale = max(4, int(900 / max(vw, vh)))
    W = int(vw * scale)
    H = int(vh * scale)
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)

    def px(x: float, y: float) -> tuple[float, float]:
        return ((x + pad) * scale, (ph - y + pad) * scale)

    d.rectangle([px(0, ph), px(pw, 0)], outline=(200, 200, 200), width=1)
    prev_end = None
    for p in paths:
        if prev_end is not None:
            d.line([px(*prev_end), px(*p[0])], fill=(225, 225, 225), width=1)
        d.line([px(x, y) for x, y in p], fill=(176, 48, 32), width=2)
        prev_end = p[-1]
    title_font = _load_font(max(12, scale * 3))
    d.text((pad * scale, 2), title, fill=(20, 20, 20), font=title_font)
    img.save(png_path, "PNG", optimize=True)
    return png_path, svg_path


def cmd_preview(args):
    cfg = fit_config(
        args.word.upper(), _apply_size_overrides(_config_for_size(args.size), args)
    )
    word = args.word.upper()
    pieces, stats = generate_pieces(word, args.seed, cfg)
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    out = FIG_DIR / f"preview_{args.size}_{word.lower()}_seed{args.seed}.png"
    fw = cfg.puzzle_w_px / cfg.px_per_mm
    fh = cfg.puzzle_h_px / cfg.px_per_mm
    title = f"{word} jigsaw preview — {args.size} ({fw:.0f}x{fh:.0f}mm), {len(pieces)} pieces"
    render_preview(pieces, cfg, title, out)
    n_cells = sum(1 for p in pieces if p["kind"] == "cell")
    n_letters = len(pieces) - n_cells
    print(f"pieces: {len(pieces)} ({n_cells} cells + {n_letters} letters)")
    print(f"tabs: {stats}")
    print(f"-> {out}")


def cmd_cut(args):
    cfg = fit_config(
        args.word.upper(), _apply_size_overrides(_config_for_size(args.size), args)
    )
    _apply_origin(cfg, args.origin)
    word = args.word.upper()
    pieces, stats = generate_pieces(word, args.seed, cfg)
    material = load_material(args.material)
    if getattr(args, "passes", None) is not None:
        # override the profile's pass count without mutating the cached profile
        material = {**material, "laser": {**material["laser"], "passes": args.passes}}
    gcode = _emit_cut_for(
        pieces,
        material,
        cfg,
        word,
        args.size,
        mode=args.laser_mode,
        feed_override=args.feed,
        min_segment_mm=args.min_segment_mm,
        power_percent=args.power_percent,
        ramp_ms=args.ramp_ms,
    )
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "_centered" if args.origin == "center" else ""
    out = BUILD_DIR / f"cut_{args.size}_{word.lower()}_seed{args.seed}{suffix}.gcode"
    out.write_text(gcode)
    feed_note = (
        f"{args.feed} (override)"
        if args.feed
        else f"{material['laser']['feed_mm_per_min']} (material)"
    )
    pct = (
        args.power_percent
        if args.power_percent is not None
        else material["laser"]["power_percent"]
    )
    pwr_note = (
        f"{pct}% (override)" if args.power_percent is not None else f"{pct}% (material)"
    )
    laser_on = gcode.count("\nM3 ") + gcode.count("\nM4 ")
    # Actual (fitted) panel size — what the cut spans, not the bound. Size any
    # companion photo engrave to THIS and share the same WCS origin so the cut
    # lands on the image (see the LaserGRBL two-job workflow).
    fw = cfg.puzzle_w_px / cfg.px_per_mm
    fh = cfg.puzzle_h_px / cfg.px_per_mm
    ox, oy = cfg.origin_offset_mm
    print(f"pieces: {len(pieces)}  tabs: {stats}")
    print(f"origin: {args.origin}  panel (actual cut): {fw:.1f}x{fh:.1f}mm")
    print(
        f"  photo/WCS: engrave image at {fw:.1f}x{fh:.1f}mm, "
        f"panel corner at machine ({ox:.1f}, {oy:.1f}); do not change WCS before cutting"
    )
    print(
        f"laser: {args.laser_mode}  power: {pwr_note}  "
        f"laser-on events: {laser_on}  ramp: {args.ramp_ms:.0f}ms"
    )
    print(f"feed: {feed_note}  min segment: {args.min_segment_mm}mm")
    print(f"-> {out}  ({len(gcode.splitlines())} lines)")

    # Always emit visual previews of the actual toolpath (PNG + SVG) so
    # issues are spottable without reading GCode.
    png_path, svg_path = render_gcode_previews(
        gcode,
        cfg,
        out.with_suffix(""),
        title=f"{word} {args.size} cut — {fw:.0f}x{fh:.0f}mm",
    )
    print(f"-> {png_path}")
    print(f"-> {svg_path}")


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------


def main():
    p = argparse.ArgumentParser(prog="jigsaw", description=__doc__.splitlines()[0])
    subs = p.add_subparsers(dest="command", required=True)

    # preview
    pv = subs.add_parser("preview", help="render a verification diagram only")
    pv.add_argument(
        "--size", default="full", choices=("small", "mini", "banner", "micro", "full")
    )
    pv.add_argument("--word", default="NORA")
    pv.add_argument("--seed", type=int, default=7)
    _add_size_override_flags(pv)
    pv.set_defaults(func=cmd_preview)

    # cut
    cu = subs.add_parser("cut", help="emit cut GCode")
    cu.add_argument(
        "--size", default="full", choices=("small", "mini", "banner", "micro", "full")
    )
    cu.add_argument("--word", default="NORA")
    cu.add_argument("--seed", type=int, default=7)
    cu.add_argument("--material", default="mdf_3mm")
    cu.add_argument(
        "--origin",
        default="corner",
        choices=("corner", "center"),
        help="WCS origin placement: 'corner' = panel bottom-left at (0,0) "
        "(default); 'center' = panel center at (0,0), coords symmetric around 0",
    )
    cu.add_argument(
        "--laser-mode",
        dest="laser_mode",
        default="static",
        choices=("dynamic", "static"),
        help="M3 static constant-power (default — this weak diode under-fires in "
        "M4 dynamic, which scales power with feed) vs M4 dynamic",
    )
    cu.add_argument(
        "--ramp-ms",
        dest="ramp_ms",
        type=float,
        default=WARMUP_MS,
        help="diode warmup duration (ms): before each cut the head runs forward "
        "ramp_ms/2 then back to the start (laser at full power on return), so the "
        "cut then runs full-power over the whole path. Fixed machine constant "
        "(WARMUP_MS); ~1000ms measured — you shouldn't need to change it",
    )
    cu.add_argument(
        "--feed",
        type=int,
        default=None,
        help="override cut feedrate mm/min (default: use the material's "
        "feed_mm_per_min)",
    )
    cu.add_argument(
        "--passes",
        type=int,
        default=None,
        help="override number of cut-through passes (default: material profile). "
        "Use 1 to dial in a single-pass clean cut, or bump for stubborn stock",
    )
    cu.add_argument(
        "--min-segment-mm",
        dest="min_segment_mm",
        type=float,
        default=0.0,
        help="decimate so no emitted G1 chord is shorter than this (mm); "
        "trims tiny segments that stall M4 planning (default 0 = off)",
    )
    cu.add_argument(
        "--power-percent",
        dest="power_percent",
        type=float,
        default=100.0,
        help="cut power as %% of $30 max (default 100 — this laser is weak "
        "and only cuts reliably at full power; pass a lower value to use "
        "the material profile's value instead via e.g. --power-percent <n>)",
    )
    _add_size_override_flags(cu)
    cu.set_defaults(func=cmd_cut)


    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
