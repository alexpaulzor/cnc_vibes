#!/usr/bin/env python3
"""Jigsaw 3c — single canonical CLI for the wooden-jigsaw lesson.

Replaces the scratch/ phase scripts (phase6_small, phase7_raster,
phase8_full_puzzle, mockup_photo_puzzle) with one entry point built on
the productionized geometry / encoder / emitter modules.

Subcommands:
  preview   — render a verification image (no GCode)
  cut       — emit cut GCode (--size small or full)
  raster    — emit raster engrave + cut, three output files
  mockup    — comparison visualization of halftone vs grayscale

Validator-clean GRBL laser G-code (M3/M4 dynamic power).

Examples:
  jigsaw.py preview --word NORA
  jigsaw.py cut --size full --word NORA --seed 7 --material mdf_3mm
  jigsaw.py cut --size small --word N --seed 7
  jigsaw.py raster --image kitten.jpg --size small --mode halftone
  jigsaw.py mockup --image kitten.jpg --word NORA
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import MultiPolygon

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from encoder import (  # noqa: E402
    grayscale_quantize,
    halftone_encode,
    load_and_preprocess,
)
from emitter import (  # noqa: E402
    WARMUP_MS,
    combined_raster_and_cut,
    emit_cut_gcode_full,
    emit_cut_gcode_simple,
    emit_raster_gcode,
    load_material,
    raster_only_gcode,
)
from geometry import (  # noqa: E402
    banner_puzzle_config,
    find_font,
    fit_config,
    full_puzzle_config,
    generate_pieces,
    micro_puzzle_config,
    mini_puzzle_config,
    small_puzzle_config,
)
import glyph_origins as glyph_origins_mod  # noqa: E402

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
        "'black' is chunkier than the default bold",
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


_SEVEN_SEG = {
    # segments: a top, b top-right, c bot-right, d bottom, e bot-left, f top-left, g mid
    "0": "abcdef",
    "1": "bc",
    "2": "abged",
    "3": "abgcd",
    "4": "fgbc",
    "5": "afgcd",
    "6": "afgecd",
    "7": "abc",
    "8": "abcdefg",
    "9": "abgfcd",
}


def _seg_endpoints(w, h):
    return {
        "a": ((0, h), (w, h)),
        "b": ((w, h), (w, h / 2)),
        "c": ((w, h / 2), (w, 0)),
        "d": ((0, 0), (w, 0)),
        "e": ((0, h / 2), (0, 0)),
        "f": ((0, h), (0, h / 2)),
        "g": ((0, h / 2), (w, h / 2)),
    }


def _seven_seg_segments(text, x, y, h):
    """7-segment strokes for `text` (digits), bottom-left at (x, y), height h.
    Returns a list of ((x0,y0),(x1,y1)) segments in absolute coords."""
    w = h * 0.55
    gap = h * 0.35
    segd = _seg_endpoints(w, h)
    out = []
    cx = x
    for ch in text:
        for s in _SEVEN_SEG.get(ch, ""):
            (x0, y0), (x1, y1) = segd[s]
            out.append(((cx + x0, y + y0), (cx + x1, y + y1)))
        cx += w + gap
    return out


def _engrave_number(text, x, y, h, power_s, feed):
    """G-code lines that engrave `text` (digits) with a 7-segment stroke font,
    bottom-left at (x, y), glyph height h. Each stroke is its own M3..M5 so there
    are no stray marks between segments. Marks (light) rather than cuts."""
    w = h * 0.55
    gap = h * 0.35
    segd = _seg_endpoints(w, h)
    out = []
    cx = x
    for ch in text:
        for s in _SEVEN_SEG.get(ch, ""):
            (x0, y0), (x1, y1) = segd[s]
            out += [
                f"G0 X{cx + x0:.3f} Y{y + y0:.3f}",
                f"M3 S{power_s}",
                f"F{feed}",
                f"G1 X{cx + x1:.3f} Y{y + y1:.3f}",
                "M5",
            ]
        cx += w + gap
    return out


def _render_warmup_key(radii, feeds, order, cx, cy, r_max, T, m):
    """Annotated key PNG for the warmup ring test: each ring drawn to scale in
    its own color with a start-angle marker, plus a legend (color -> cut#, feed,
    start angle, radius). Reference this on-screen to read a compact card."""
    import math

    S = 14  # px/mm
    n = len(radii)
    diag = int(2 * (r_max + m) * S)
    legw = 240
    H = max(diag, 40 + n * 26)
    img = Image.new("RGB", (diag + legw, H), "white")
    d = ImageDraw.Draw(img)
    palette = [
        (200, 40, 40),
        (210, 120, 20),
        (180, 165, 20),
        (40, 160, 60),
        (30, 120, 200),
        (120, 60, 190),
        (200, 40, 140),
        (90, 90, 90),
    ]

    def px(mx, my):
        return (mx * S, H - my * S)

    tf, sf = _load_font(15), _load_font(13)
    d.text(
        (6, 6),
        f"CUT-THROUGH KEY  loop={T:.1f}s/ring, cut inner->outer",
        fill=(0, 0, 0),
        font=tf,
    )
    for k, i in enumerate(order):
        r, col = radii[i], palette[k % len(palette)]
        x0, y0 = px(cx - r, cy + r)
        x1, y1 = px(cx + r, cy - r)
        d.ellipse([x0, y0, x1, y1], outline=col, width=2)
    # radial cross-section slices: 0deg (common spiral start) and join_ang (where
    # the spiral meets the circle = start of the pure single-pass cut) — matches
    # the gcode's radials.
    join_ang = 360.0 * (WARMUP_MS / 1000.0) / T
    rout = r_max + 2
    for a in (0.0, join_ang):
        c0 = px(cx, cy)
        e = px(
            cx + rout * math.cos(math.radians(a)), cy + rout * math.sin(math.radians(a))
        )
        d.line([c0[0], c0[1], e[0], e[1]], fill=(120, 120, 120), width=1)
    d.text((diag + 8, 6), "cut# feed   r", fill=(0, 0, 0), font=sf)
    for k, i in enumerate(order):
        col, yy = palette[k % len(palette)], 30 + k * 26
        d.rectangle([diag + 8, yy, diag + 22, yy + 14], fill=col)
        d.text(
            (diag + 28, yy),
            f"#{k + 1}   {feeds[i]}   r{radii[i]:.0f}",
            fill=(20, 20, 20),
            font=sf,
        )
    p = BUILD_DIR / "warmup_ring_test_key.png"
    img.save(p, "PNG", optimize=True)
    return p


def _render_gcode_png(lines, out_path, px_per_mm=14):
    """Render a gcode toolpath (G0/G1 lines + G2/G3 arcs) to a PNG. Cuts (laser
    on) are red, rapids (laser off) faint gray. Handles negative coords (center
    origin) by shifting to the bbox. Standalone — no cfg needed."""
    import math
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


def cmd_warmuptest(args):
    """Single-pass CUT-THROUGH feed test: concentric rings, WCS origin at the
    CENTER of the rings (zero the machine in the middle of a scrap). Each ring is
    warmed up first (WARMUP_MS, front-loaded fwd-half/back-to-start at full power)
    then its loop is cut in --time-s seconds, so feed = circumference / time-s.
    The innermost (slowest) is cut FIRST and each ring outward is faster.

    Cut inner->outer and STOP when a ring no longer falls free — the last one
    that dropped is your fastest clean single-pass feed. Rings are native G3 arcs
    (constant feed, no short-segment stutter). No engraving; read the KEY png. It
    is fine for the fast outer rings to overrun the scrap edge."""
    import math

    n = args.circles
    r_min, r_max = args.min_r, args.max_r
    T = args.time_s  # loop seconds per ring (EXCLUDING the WARMUP_MS warmup)
    power_s = int(round(args.power_percent * 10))
    radii = [r_min + (r_max - r_min) * i / max(1, n - 1) for i in range(n)]
    feeds = [int(round(120 * math.pi * r / T)) for r in radii]  # circumference/(T/60)
    gap = (r_max - r_min) / max(1, n - 1) if n > 1 else r_min  # inter-ring spacing
    delta = gap / 2.0  # spiral starts this far INSIDE the target ring (half the gap)
    # A ring's circumference is covered in T seconds, so 1s of travel sweeps this
    # many degrees. The spiral lead-in climbs delta over the WARMUP_MS window while
    # sweeping join_ang, joining the target circle exactly when the laser is warm.
    join_ang = 360.0 * (WARMUP_MS / 1000.0) / T
    K = 6  # chained-arc steps approximating the spiral (arcs stutter-free; the tiny
    # radial connectors happen mid-warmup, low power, in scrap — stutter irrelevant)

    lines = [
        "; cut-through feed test — concentric rings, WCS origin at CENTER",
        f"; per ring: {WARMUP_MS:.0f}ms spiral warmup ({delta:.1f}mm inside, sweeping "
        f"{join_ang:.0f}deg) joins the circle, then one full-power loop.",
        f"; feed = circumference / {T:.1f}s. Cut order INNER (slow) -> OUTER (fast).",
        "; STOP when a ring stops falling free -> last clean = fastest single-pass feed.",
        "; spiral shows the warmup gradient (backlight, read the degrees where it first",
        "; cuts through). Rings/spiral are G2/G3 arcs (no segment stutter). Use KEY png.",
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
        cur_r, cur_a = rs, 0.0
        for j in range(K):  # chained concentric arcs stepping outward
            na = join_ang * (j + 1) / K
            cx0, cy0 = (
                cur_r * math.cos(math.radians(cur_a)),
                cur_r * math.sin(math.radians(cur_a)),
            )
            ex, ey = (
                cur_r * math.cos(math.radians(na)),
                cur_r * math.sin(math.radians(na)),
            )
            lines.append(  # CCW arc at cur_r about center
                f"G3 X{ex:.3f} Y{ey:.3f} I{-cx0:.3f} J{-cy0:.3f}"
            )
            nr = rs + delta * (j + 1) / K
            lines.append(  # radial step outward to next radius (at same angle)
                f"G1 X{nr * math.cos(math.radians(na)):.3f} "
                f"Y{nr * math.sin(math.radians(na)):.3f}"
            )
            cur_r, cur_a = nr, na
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
    # cross-sections at the two informative angles: 0deg = the common spiral start,
    # and join_ang = where the spiral meets the circle, i.e. the start of the pure
    # single-pass full-power cut. Cut last (an early stop skips them) at the
    # slowest, most reliable feed.
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

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    out = BUILD_DIR / "warmup_ring_test.gcode"
    out.write_text("\n".join(lines))
    key_path = _render_warmup_key(
        radii, feeds, list(range(n)), r_max, r_max, r_max, T, 0.0
    )
    png_path = _render_gcode_png(lines, BUILD_DIR / "warmup_ring_test.png")
    print(f"circles: {n}  radii: {r_min}-{r_max}mm  loop={T}s  feeds: {feeds}")
    print("origin at CENTER; cut inner->outer, stop when a ring stops dropping free.")
    print(f"-> {out}")
    print(f"-> {png_path}  (toolpath)")
    print(f"-> {key_path}  (lookup key)")


def cmd_raster(args):
    cfg = _config_for_size(args.size)
    word = args.word.upper()
    stem = args.image.stem if args.image else "test_pattern"
    image_label = str(args.image) if args.image else "test-pattern"

    # 1. Pieces + cut block
    pieces, stats = generate_pieces(word, args.seed, cfg)
    material = load_material(args.material)
    cut_block = _emit_cut_for(pieces, material, cfg, word, args.size)

    # 2. Image + encode
    src = load_and_preprocess(
        args.image, cfg.panel_mm, args.line_spacing_mm, args.test_pattern
    )
    if args.mode == "halftone":
        encoded = halftone_encode(src)
    else:
        encoded = grayscale_quantize(src, args.grayscale_levels)
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    preview_path = FIG_DIR / f"raster_preview_{args.size}_{stem}_{args.mode}.png"
    encoded.save(preview_path)

    # 3. Raster GCode + three output files
    raster_lines = emit_raster_gcode(
        encoded,
        args.mode,
        cfg,
        args.line_spacing_mm,
        args.engrave_power_percent,
        args.engrave_feed,
    )
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    base = f"{args.size}_{stem}_{args.mode}"
    raster_path = BUILD_DIR / f"{base}_raster.gcode"
    cut_path = BUILD_DIR / f"{base}_cut.gcode"
    full_path = BUILD_DIR / f"{base}_full.gcode"
    raster_path.write_text(
        raster_only_gcode(raster_lines, args.material, image_label, args.mode)
    )
    cut_path.write_text(cut_block)
    full_path.write_text(
        combined_raster_and_cut(
            raster_lines, cut_block, args.material, image_label, args.mode
        )
    )

    print(f"pieces: {len(pieces)}  tabs: {stats}")
    print(f"raster: {len(raster_lines)} lines  encoded preview: {preview_path}")
    print(f"-> {raster_path}")
    print(f"-> {cut_path}")
    print(f"-> {full_path}")


def cmd_mockup(args):
    """Thin wrapper around scratch/mockup_photo_puzzle.py for now —
    the wood-color mockup logic + zoom inset isn't worth re-extracting
    until productionization is complete. Reuses the same image-based
    comparison the scratch script produces."""
    import subprocess

    scratch_script = SCRIPT_DIR / "scratch" / "mockup_photo_puzzle.py"
    cmd = [
        sys.executable,
        str(scratch_script),
        "--image",
        str(args.image),
        "--word",
        args.word,
        "--seed",
        str(args.seed),
    ]
    if args.out:
        cmd += ["--out", str(args.out)]
    sys.exit(subprocess.run(cmd).returncode)


GLYPH_SHEET_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_GLYPH_SHEET_COLS = 6
_GLYPH_SHEET_CELL = 220
_GLYPH_SHEET_LABEL_H = 34
_GLYPH_SHEET_FONT_PX = 150


def _glyph_sheet_layout():
    """Deterministic layout shared by the renderer and the dot-reader.

    Returns (width, height, cells) where cells is a list of
    (char, cell_x, cell_y, pen_xy, ink_bbox); pen_xy is where the glyph is
    drawn, ink_bbox = (l, t, r, b) its ink box in image space. Because
    render and read use the SAME layout, a dot placed on the rendered sheet
    maps back to exact normalized ink-bbox coordinates."""
    n = len(GLYPH_SHEET_CHARS)
    cols = _GLYPH_SHEET_COLS
    rows = (n + cols - 1) // cols
    cell = _GLYPH_SHEET_CELL
    label_h = _GLYPH_SHEET_LABEL_H
    glyph_font = find_font(_GLYPH_SHEET_FONT_PX)

    cells = []
    for i, ch in enumerate(GLYPH_SHEET_CHARS):
        cx, cy = (i % cols) * cell, (i // cols) * cell
        l, t, r, b = glyph_font.getbbox(ch)
        gw, gh = r - l, b - t
        area_h = cell - label_h
        ox = cx + (cell - gw) // 2 - l
        oy = cy + (area_h - gh) // 2 - t
        ink_bbox = (ox + l, oy + t, ox + r, oy + b)
        cells.append((ch, cx, cy, (ox, oy), ink_bbox))
    return cols * cell, rows * cell, cells


def _draw_crosshair(d, x, y, color, r=13, w=2):
    d.line([x - r, y, x + r, y], fill=color, width=w)
    d.line([x, y - r, x, y + r], fill=color, width=w)


def _snap_to_ink(ink, gx, gy, band=8):
    """Nudge a hand-placed point onto the local stroke's centerline, moving
    PERPENDICULAR to the stroke and keeping the along-stroke axis where the
    user put it. `ink` is a bool array (True = glyph pixel).

    At the point, measure the ink run width across the row (horizontal) and
    down the column (vertical). The thinner direction is the stroke's cross
    section, so snap that axis to the run center and keep the other: on a
    vertical stem -> snap x only; on a horizontal bar -> snap y only; on a
    small junction -> snap both. Off ink (an open counter/gap) -> keep the
    point as placed, so deliberate center anchors like O/C are trusted."""
    h, w = ink.shape
    ix = min(max(int(round(gx)), 0), w - 1)
    iy = min(max(int(round(gy)), 0), h - 1)

    if not ink[iy, ix]:
        # snap onto the nearest ink pixel within a small band, else keep raw
        best = None
        for dy in range(-band, band + 1):
            for dx in range(-band, band + 1):
                y, x = iy + dy, ix + dx
                if 0 <= y < h and 0 <= x < w and ink[y, x]:
                    dist = dx * dx + dy * dy
                    if best is None or dist < best[0]:
                        best = (dist, x, y)
        if best is None:
            return gx, gy  # open space: trust the placement
        _d, ix, iy = best

    lo = ix
    while lo > 0 and ink[iy, lo - 1]:
        lo -= 1
    hi = ix
    while hi < w - 1 and ink[iy, hi + 1]:
        hi += 1
    run_w = hi - lo + 1

    tlo = iy
    while tlo > 0 and ink[tlo - 1, ix]:
        tlo -= 1
    thi = iy
    while thi < h - 1 and ink[thi + 1, ix]:
        thi += 1
    run_h = thi - tlo + 1

    sx, sy = float(ix), float(iy)
    if run_w <= run_h:  # vertical-ish stroke -> center across its width
        sx = (lo + hi) / 2.0
    if run_h <= run_w:  # horizontal-ish stroke -> center across its height
        sy = (tlo + thi) / 2.0
    return sx, sy


def _read_green_dots(path: Path, snap=True):
    """Detect the green dot in each glyph cell of an edited contact sheet
    and convert to normalized ink-bbox origins. Hand-placed dots are
    snapped to the glyph's ink midpoints (see _snap_to_ink) unless
    snap=False. Returns {char: {"raw": (nx,ny), "snapped": (nx,ny)}}."""
    import numpy as np

    _w, _h, cells = _glyph_sheet_layout()
    glyph_font = find_font(_GLYPH_SHEET_FONT_PX)
    src = Image.open(path).convert("RGB")
    if src.size != (_w, _h):
        # Tolerate any export size / Retina scaling / PDF round-trip: the
        # sheet is a fixed grid, so rescaling to canonical preserves every
        # dot's relative position. (Assumes the image is the sheet only.)
        src = src.resize((_w, _h), Image.BILINEAR)
    arr = np.asarray(src, dtype=np.int16)
    R, G, B = arr[..., 0], arr[..., 1], arr[..., 2]
    green = (G > 140) & (R < 120) & (B < 120)  # tolerant "green dot" mask

    found = {}
    label_h = _GLYPH_SHEET_LABEL_H
    cell = _GLYPH_SHEET_CELL
    for ch, cx, cy, (ox, oy), (l, t, r, b) in cells:
        y0, y1 = cy, cy + cell - label_h  # glyph area, excluding label strip
        x0, x1 = cx, cx + cell
        ys, xs = np.nonzero(green[y0:y1, x0:x1])
        if len(xs) == 0:
            continue
        gx, gy = x0 + xs.mean(), y0 + ys.mean()

        sx, sy = gx, gy
        if snap:
            # clean glyph mask in cell-local coords (no dot to interfere)
            m = Image.new("L", (cell, cell - label_h), 0)
            ImageDraw.Draw(m).text((ox - cx, oy - cy), ch, fill=255, font=glyph_font)
            ink = np.asarray(m) > 80
            lx, ly = _snap_to_ink(ink, gx - cx, gy - cy)
            sx, sy = lx + cx, ly + cy

        def norm(px, py):
            nx = (px - l) / (r - l) if r > l else 0.5
            ny = (py - t) / (b - t) if b > t else 0.5
            return (round(float(nx), 3), round(float(ny), 3))

        found[ch] = {"raw": norm(gx, gy), "snapped": norm(sx, sy)}
    return found


def cmd_glyphs(args):
    """Render a contact sheet of every glyph with crosshairs at its grid
    origin (RED = baseline guess, BLUE = adopted value), for reviewing and
    correcting glyph_origins.py.

    With --read <edited.png>: detect green dots the user drew on a copy of
    the sheet, print the corresponding normalized origins to paste into
    USER_ORIGIN_OVERRIDES, then (re)render the sheet."""
    if getattr(args, "read", None):
        detected = _read_green_dots(args.read)
        if not detected:
            print(f"no green dots detected in {args.read}")
            print("draw a solid green dot (#00FF00) in each cell to override")
            return
        use_snap = getattr(args, "snap", False)
        which = "snapped" if use_snap else "raw"
        print(f"detected {len(detected)} green dot(s) in {args.read} (using {which}):")
        print("paste into USER_ORIGIN_OVERRIDES in glyph_origins.py:")
        for ch in sorted(detected):
            base = glyph_origins_mod.baseline_grid_origin(ch)
            vx, vy = detected[ch][which]
            other = detected[ch]["snapped" if not use_snap else "raw"]
            print(
                f'    "{ch}": ({vx:.3f}, {vy:.3f}),'
                f"   # {'raw' if not use_snap else 'snapped'}; "
                f"{'snapped' if not use_snap else 'raw'} {other}, was {base}"
            )
        return

    _w, _h, cells = _glyph_sheet_layout()
    glyph_font = find_font(_GLYPH_SHEET_FONT_PX)
    label_font = find_font(22)
    img = Image.new("RGB", (_w, _h), (250, 250, 250))
    d = ImageDraw.Draw(img)
    import numpy as np

    for ch, cx, cy, (ox, oy), ink_bbox in cells:
        d.rectangle(
            [cx, cy, cx + _GLYPH_SHEET_CELL - 1, cy + _GLYPH_SHEET_CELL - 1],
            outline=(215, 215, 215),
        )
        l, t, r, b = ink_bbox
        d.text((ox, oy), ch, fill=(120, 120, 120), font=glyph_font)
        d.rectangle([l, t, r, b], outline=(185, 205, 235))

        # Operative anchors from the general rules (glyph_seam / glyph_hcut_y).
        m = Image.new(
            "L", (_GLYPH_SHEET_CELL, _GLYPH_SHEET_CELL - _GLYPH_SHEET_LABEL_H), 0
        )
        ImageDraw.Draw(m).text((ox - cx, oy - cy), ch, fill=255, font=glyph_font)
        ink = np.asarray(m) > 80
        nx, through = glyph_origins_mod.glyph_seam(ink)
        hcy = glyph_origins_mod.glyph_hcut_y(ink)
        seam_px = l + nx * (r - l)
        cxc = (l + r) / 2.0

        if through:
            # vertical seam (blue) x horizontal row boundary (green); dot = anchor
            d.line([(seam_px, t), (seam_px, b)], fill=(30, 90, 220), width=2)
            hy = t + hcy * (b - t)
            d.line([(l, hy), (r, hy)], fill=(20, 160, 60), width=2)
            _draw_crosshair(d, seam_px, hy, (220, 30, 30))
            kind = "split"
        else:
            # capped-open (C, G): no vertical slice (dashed grey at center), row
            # boundary cuts just inside an arm — mark BOTH alternating anchors.
            for yy in range(int(t), int(b), 8):
                d.line(
                    [(cxc, yy), (cxc, min(yy + 4, b))], fill=(170, 170, 170), width=1
                )
            for f in (0.25, 0.75):
                ay = t + f * (b - t)
                d.line([(l, ay), (r, ay)], fill=(230, 140, 20), width=2)
                _draw_crosshair(d, cxc, ay, (230, 140, 20), r=8)
            kind = "glob"

        d.text(
            (cx + 6, cy + _GLYPH_SHEET_CELL - _GLYPH_SHEET_LABEL_H + 6),
            f"{ch}  {kind}  seam {nx:.2f}",
            fill=(40, 40, 40),
            font=label_font,
        )

    FIG_DIR.mkdir(parents=True, exist_ok=True)
    out = FIG_DIR / "glyph_origins.png"
    img.save(out, "PNG", optimize=True)
    print(f"glyph anchors: {len(cells)} glyphs")
    print(
        "  blue=vertical seam  green=row boundary  red=anchor  "
        "orange=capped-open arm cuts  grey dashes=no vertical slice"
    )
    print(f"-> {out}")


# ---------------------------------------------------------------------------
# Versioned image save + banner demo suite
# ---------------------------------------------------------------------------

BANNER_DEMO_NAMES = ["NORA", "AYANA", "KARSON", "KAI", "KADE", "SLOAN", "LEO"]
# Extra names for testing coverage only — NOT cut in real material. Handy for
# exercising the letter-splitting / spacing / tab logic across more glyphs.
BONUS_DEMO_NAMES = [
    "ALEX",
    "REBECCA",
    "CLEM",
    "AIDA",
    "KYLE",
    "CHELSEA",
    "ERIC",
    "MARIA",
]


def save_versioned(path: Path, img: Image.Image) -> str:
    """Save `img` to `path`, first archiving any existing (different) file
    into `<dir>/.history/<stem>/NNNN.ext`. Dedup by pixel hash: if the new
    image is identical to the current file, nothing is archived. History
    lives under build/ so it stays out of git. Returns a status string."""
    import hashlib

    path.parent.mkdir(parents=True, exist_ok=True)
    new_hash = hashlib.md5(img.convert("RGB").tobytes()).hexdigest()
    status = "written"
    if path.exists():
        try:
            cur = Image.open(path).convert("RGB")
            cur_hash = hashlib.md5(cur.tobytes()).hexdigest()
        except Exception:
            cur_hash = None
        if cur_hash == new_hash:
            return "unchanged"
        hist = path.parent / ".history" / path.stem
        hist.mkdir(parents=True, exist_ok=True)
        existing = sorted(hist.glob(f"[0-9]*{path.suffix}"))
        n = 1 + (int(existing[-1].stem) if existing else 0)
        shutil.copy2(path, hist / f"{n:04d}{path.suffix}")
        status = f"versioned (prev -> .history/{path.stem}/{n:04d}{path.suffix})"
    img.save(path, "PNG", optimize=True)
    return status


def _render_banner_demo(name: str, annotate_origins: bool) -> Path:
    cfg = fit_config(name, _config_for_size("banner"))
    pieces, stats = generate_pieces(name, 7, cfg)
    origins = None
    if annotate_origins:
        from geometry import letter_auto_origins

        origins = letter_auto_origins(name, cfg)
    fw = cfg.puzzle_w_px / cfg.px_per_mm
    fh = cfg.puzzle_h_px / cfg.px_per_mm
    title = f"{name} — banner ({fw:.0f}x{fh:.0f}mm), {len(pieces)} pcs"
    out = BUILD_DIR / "name_grids" / "banner" / f"{name}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    # render_preview saves internally; capture to an Image via a temp render
    tmp = out.with_suffix(".tmp.png")
    render_preview(pieces, cfg, title, tmp, origins=origins)
    img = Image.open(tmp).convert("RGB")
    status = save_versioned(out, img)
    tmp.unlink(missing_ok=True)
    print(f"  {name}: {len(pieces)} pcs — {status}")
    return out


def cmd_bannerdemos(args):
    """Render the banner demo previews, versioning any prior copy into a
    git-ignored history folder. --origins overlays the auto grid-origin;
    --bonus also renders the non-critical test names (not cut in material)."""
    names = list(BANNER_DEMO_NAMES)
    if args.bonus:
        names += BONUS_DEMO_NAMES
    print(
        f"banner demos ({len(names)} names, origins={'on' if args.origins else 'off'}):"
    )
    for name in names:
        _render_banner_demo(name, args.origins)
    print(f"-> {BUILD_DIR / 'name_grids' / 'banner'}/")


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

    # warmup-test — single-pass cut-through feed test (concentric rings)
    wt = subs.add_parser(
        "warmup-test",
        help="single-pass cut-through feed test: concentric rings, center origin",
    )
    wt.add_argument("--circles", type=int, default=12, help="number of rings")
    wt.add_argument("--min-r", type=float, default=3.0, help="smallest ring radius mm")
    wt.add_argument("--max-r", type=float, default=24.0, help="largest ring radius mm")
    wt.add_argument(
        "--time-s",
        dest="time_s",
        type=float,
        default=3.0,
        help="loop seconds per ring, EXCLUDING the warmup (feed = circumference / "
        "time-s). 3s + r_min=3mm gives a ~377mm/min slowest ring",
    )
    wt.add_argument("--power-percent", dest="power_percent", type=float, default=100.0)
    wt.set_defaults(func=cmd_warmuptest)

    # raster
    ra = subs.add_parser("raster", help="emit raster engrave + cut GCode (3 files)")
    ra.add_argument("--size", default="small", choices=("small", "full"))
    ra.add_argument("--word", default="N")
    ra.add_argument("--seed", type=int, default=7)
    ra.add_argument("--material", default="mdf_3mm")
    src = ra.add_mutually_exclusive_group(required=True)
    src.add_argument("--image", type=Path)
    src.add_argument("--test-pattern", action="store_true")
    ra.add_argument("--mode", choices=("halftone", "grayscale"), default="halftone")
    ra.add_argument("--line-spacing-mm", type=float, default=0.20)
    ra.add_argument("--engrave-power-percent", type=int, default=30)
    ra.add_argument("--engrave-feed", type=int, default=3000)
    ra.add_argument("--grayscale-levels", type=int, default=16)
    ra.set_defaults(func=cmd_raster)

    # mockup
    mo = subs.add_parser(
        "mockup", help="halftone vs grayscale comparison visualization"
    )
    mo.add_argument("--image", type=Path, required=True)
    mo.add_argument("--word", default="NORA")
    mo.add_argument("--seed", type=int, default=7)
    mo.add_argument("--out", type=Path, default=None)
    mo.set_defaults(func=cmd_mockup)

    # glyphs — contact sheet of grid origins for LUT review
    gl = subs.add_parser(
        "glyphs", help="render a contact sheet of glyph grid-origins for review"
    )
    gl.add_argument(
        "--read",
        type=Path,
        default=None,
        help="read green dots from an edited copy of the sheet and print "
        "snapped origins for USER_ORIGIN_OVERRIDES",
    )
    gl.add_argument(
        "--snap",
        action="store_true",
        help="snap dots to local stroke centers (default: use raw placement, "
        "which is usually more faithful — snapping misfires at junctions)",
    )
    gl.set_defaults(func=cmd_glyphs)

    # bannerdemos — render the 7 banner demo previews (versioned)
    bd = subs.add_parser(
        "bannerdemos", help="render the 7 banner demo previews (versioned history)"
    )
    bd.add_argument(
        "--origins",
        action="store_true",
        help="overlay the automatic grid-origin crosshair on each letter",
    )
    bd.add_argument(
        "--bonus",
        action="store_true",
        help="also render the non-critical bonus test names (not cut in material)",
    )
    bd.set_defaults(func=cmd_bannerdemos)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
