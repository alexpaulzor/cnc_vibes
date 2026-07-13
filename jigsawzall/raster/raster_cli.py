#!/usr/bin/env python3
"""Standalone raster-engrave CLI for jigsawzall (DORMANT / unsupported).

Raster engraving was dropped from the jigsawzall MVP but kept here, fully
de-wired from the main `jigsaw.py` CLI. Emits three GCode files (raster only,
cut only, combined) plus an encoded preview.

Run it directly (it is NOT a `jigsaw.py` subcommand):
  python3 raster/raster_cli.py --test-pattern --size small
  python3 raster/raster_cli.py --image kitten.jpg --size small --mode halftone
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# raster_cli lives in jigsawzall/raster/. Put the jigsawzall package dir (for
# jigsaw/geometry/emitter) AND this dir (for the co-located encoder) on the path.
OWN_DIR = Path(__file__).resolve().parent
PKG_DIR = OWN_DIR.parent
sys.path.insert(0, str(PKG_DIR))
sys.path.insert(0, str(OWN_DIR))

from jigsaw import _config_for_size, _emit_cut_for  # noqa: E402
from geometry import generate_pieces  # noqa: E402
from emitter import (  # noqa: E402
    combined_raster_and_cut,
    emit_raster_gcode,
    load_material,
    raster_only_gcode,
)
from encoder import (  # noqa: E402
    grayscale_quantize,
    halftone_encode,
    load_and_preprocess,
)

BUILD_DIR = PKG_DIR / "build"
FIG_DIR = OWN_DIR / "figs"


def run(args: argparse.Namespace) -> int:
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
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="raster_cli",
        description="emit raster engrave + cut GCode (3 files) — DORMANT",
    )
    p.add_argument("--size", default="small", choices=("small", "full"))
    p.add_argument("--word", default="N")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--material", default="mdf_3mm")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--image", type=Path)
    src.add_argument("--test-pattern", action="store_true")
    p.add_argument("--mode", choices=("halftone", "grayscale"), default="halftone")
    p.add_argument("--line-spacing-mm", type=float, default=0.20)
    p.add_argument("--engrave-power-percent", type=int, default=30)
    p.add_argument("--engrave-feed", type=int, default=3000)
    p.add_argument("--grayscale-levels", type=int, default=16)
    args = p.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
