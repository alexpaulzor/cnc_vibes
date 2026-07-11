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
    mode="dynamic",
    feed_override=None,
    min_segment_mm=0.0,
    power_percent=None,
    ramp_ms=1000.0,
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
    pw = cfg.panel_mm
    ph = cfg.panel_height_mm
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
        default=1000.0,
        help="diode ramp duration (ms): closed loops re-trace their first "
        "ramp_ms/1000 * feed_mm_per_s at the end (laser warm) to finish "
        "the cold under-cut start. Default 1000 (conservative — overcut > undercut)",
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
