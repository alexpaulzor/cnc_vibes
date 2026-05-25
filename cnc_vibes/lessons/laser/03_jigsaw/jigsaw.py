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
    raise SystemExit(f"unknown --size {size!r} (expected 'small' or 'full')")


def _emit_cut_for(pieces, material, cfg, word, size):
    if size == "small":
        return emit_cut_gcode_simple(pieces, material, cfg, word)
    return emit_cut_gcode_full(pieces, material, cfg, word)


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
# Subcommand handlers
# ---------------------------------------------------------------------------


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
    word = args.word.upper()
    pieces, stats = generate_pieces(word, args.seed, cfg)
    material = load_material(args.material)
    gcode = _emit_cut_for(pieces, material, cfg, word, args.size)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    out = BUILD_DIR / f"cut_{args.size}_{word.lower()}_seed{args.seed}.gcode"
    out.write_text(gcode)
    print(f"pieces: {len(pieces)}  tabs: {stats}")
    print(f"-> {out}  ({len(gcode.splitlines())} lines)")
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
    pv.add_argument("--size", default="full", choices=("small", "full"))
    pv.add_argument("--word", default="NORA")
    pv.add_argument("--seed", type=int, default=7)
    pv.set_defaults(func=cmd_preview)

    # cut
    cu = subs.add_parser("cut", help="emit cut GCode")
    cu.add_argument("--size", default="full", choices=("small", "full"))
    cu.add_argument("--word", default="NORA")
    cu.add_argument("--seed", type=int, default=7)
    cu.add_argument("--material", default="mdf_3mm")
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
