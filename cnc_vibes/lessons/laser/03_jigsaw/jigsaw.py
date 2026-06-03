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

Validator-clean against profiles/anolex_4030_evo_ultra2.yaml.

Examples:
  jigsaw.py preview --word NORA
  jigsaw.py cut --size full --word NORA --seed 7 --material mdf_3mm
  jigsaw.py cut --size small --word N --seed 7
  jigsaw.py raster --image kitten.jpg --size small --mode halftone
  jigsaw.py mockup --image kitten.jpg --word NORA
"""

from __future__ import annotations

import argparse
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
    full_puzzle_config,
    generate_pieces,
    micro_puzzle_config,
    mini_puzzle_config,
    small_puzzle_config,
)

REPO_ROOT = SCRIPT_DIR.parent.parent.parent
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
    raise SystemExit(
        f"unknown --size {size!r} (expected 'small', 'mini', 'micro', or 'full')"
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
    warmup_ms=0,
    feed_override=None,
    min_segment_mm=0.0,
    power_percent=None,
):
    if size == "small":
        return emit_cut_gcode_simple(
            pieces,
            material,
            cfg,
            word,
            mode=mode,
            warmup_ms=warmup_ms,
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
        warmup_ms=warmup_ms,
        feed_override=feed_override,
        min_segment_mm=min_segment_mm,
        power_percent=power_percent,
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


def render_preview(pieces, cfg, title: str, out_path: Path):
    """Self-contained preview render (doesn't depend on scratch).
    Pastel-colors each piece, highlights letter pieces, labels with
    serial numbers."""
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
    for piece in pieces:
        cent = piece["polygon"].representative_point()
        label = str(piece["serial"])
        bbox = d.textbbox((0, 0), label, font=label_font)
        lw, lh = bbox[2] - bbox[0], bbox[3] - bbox[1]
        d.rectangle(
            [
                cent.x - lw / 2 - 4,
                cent.y - lh / 2 - 4,
                cent.x + lw / 2 + 4,
                cent.y + lh / 2 + 4,
            ],
            fill=(255, 255, 255),
            outline=_CUT_LINE,
        )
        d.text(
            (cent.x - lw / 2 - bbox[0], cent.y - lh / 2 - bbox[1]),
            label,
            fill=(20, 20, 20),
            font=label_font,
        )

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
    side = cfg.panel_mm + 2 * pad
    png_path = out_stem.with_suffix(".png")
    svg_path = out_stem.with_suffix(".svg")

    # --- SVG (mm units; Y flipped so up is +Y like the machine) ---
    def fy(y: float) -> float:
        return cfg.panel_mm - y + pad

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{side}mm" '
        f'height="{side}mm" viewBox="0 0 {side} {side}">',
        f'<rect x="0" y="0" width="{side}" height="{side}" fill="white"/>',
        f'<rect x="{pad}" y="{pad}" width="{cfg.panel_mm}" '
        f'height="{cfg.panel_mm}" fill="none" stroke="#ccc" stroke-width="0.2"/>',
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
    scale = max(4, int(900 / side))
    W = H = int(side * scale)
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)

    def px(x: float, y: float) -> tuple[float, float]:
        return ((x + pad) * scale, (cfg.panel_mm - y + pad) * scale)

    d.rectangle(
        [px(0, cfg.panel_mm), px(cfg.panel_mm, 0)], outline=(200, 200, 200), width=1
    )
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
    cfg = _config_for_size(args.size)
    word = args.word.upper()
    pieces, stats = generate_pieces(word, args.seed, cfg)
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    out = FIG_DIR / f"preview_{args.size}_{word.lower()}_seed{args.seed}.png"
    title = (
        f"{word} jigsaw preview — {args.size} ({cfg.panel_mm:.0f}x{cfg.panel_mm:.0f}mm), "
        f"{len(pieces)} pieces"
    )
    render_preview(pieces, cfg, title, out)
    n_cells = sum(1 for p in pieces if p["kind"] == "cell")
    n_letters = len(pieces) - n_cells
    print(f"pieces: {len(pieces)} ({n_cells} cells + {n_letters} letters)")
    print(f"tabs: {stats}")
    print(f"-> {out}")


def cmd_cut(args):
    cfg = _config_for_size(args.size)
    _apply_origin(cfg, args.origin)
    word = args.word.upper()
    pieces, stats = generate_pieces(word, args.seed, cfg)
    material = load_material(args.material)
    gcode = _emit_cut_for(
        pieces,
        material,
        cfg,
        word,
        args.size,
        mode=args.laser_mode,
        warmup_ms=args.warmup_ms,
        feed_override=args.feed,
        min_segment_mm=args.min_segment_mm,
        power_percent=args.power_percent,
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
    print(f"pieces: {len(pieces)}  tabs: {stats}")
    print(f"origin: {args.origin}  panel: {cfg.panel_mm:.0f}x{cfg.panel_mm:.0f}mm")
    print(f"laser: {args.laser_mode}  power: {pwr_note}  warmup: {args.warmup_ms}ms")
    print(f"feed: {feed_note}  min segment: {args.min_segment_mm}mm")
    print(f"-> {out}  ({len(gcode.splitlines())} lines)")

    # Always emit visual previews of the actual toolpath (PNG + SVG) so
    # issues are spottable without reading GCode.
    png_path, svg_path = render_gcode_previews(
        gcode,
        cfg,
        out.with_suffix(""),
        title=f"{word} {args.size} cut — {cfg.panel_mm:.0f}x{cfg.panel_mm:.0f}mm",
    )
    print(f"-> {png_path}")
    print(f"-> {svg_path}")
    print(f"\nValidate with:")
    print(f"  python cnc.py validate {out.relative_to(REPO_ROOT)}")


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
    print("\nValidate with:")
    for p in (raster_path, cut_path, full_path):
        print(f"  python cnc.py validate {p.relative_to(REPO_ROOT)}")


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


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------


def main():
    p = argparse.ArgumentParser(prog="jigsaw", description=__doc__.splitlines()[0])
    subs = p.add_subparsers(dest="command", required=True)

    # preview
    pv = subs.add_parser("preview", help="render a verification diagram only")
    pv.add_argument(
        "--size", default="full", choices=("small", "mini", "micro", "full")
    )
    pv.add_argument("--word", default="NORA")
    pv.add_argument("--seed", type=int, default=7)
    pv.set_defaults(func=cmd_preview)

    # cut
    cu = subs.add_parser("cut", help="emit cut GCode")
    cu.add_argument(
        "--size", default="full", choices=("small", "mini", "micro", "full")
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
        default="dynamic",
        choices=("dynamic", "static"),
        help="M4 dynamic (default) vs M3 static constant-power",
    )
    cu.add_argument(
        "--warmup-ms",
        dest="warmup_ms",
        type=int,
        default=0,
        help="G4 dwell (ms) after laser-on per path to defeat diode "
        "cold-start fade-in; dial in with `cnc.py cal-laser --sweep warmup` "
        "(default 0 = off)",
    )
    cu.add_argument(
        "--feed",
        type=int,
        default=None,
        help="override cut feedrate mm/min (default: use the material's "
        "feed_mm_per_min)",
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

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
