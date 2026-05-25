#!/usr/bin/env python3
"""Phase 7 — photo raster engraving on the puzzle, before slicing.

Pipeline:
  load image -> grayscale + crop-square + resize to (panel_mm / line_spacing)
  -> encode (halftone via Floyd-Steinberg, or grayscale quantized)
  -> emit raster GCode (M4 dynamic, G0 over white, G1 with per-run S)
  -> append cut GCode from phase6_small
  -> write three .gcode files:
       <stem>_raster.gcode  — engrave only
       <stem>_cut.gcode     — pieces only
       <stem>_full.gcode    — engrave then cut

The photo extends under the letter pockets so when the puzzle is
assembled, engraving across the letter pieces lines up with the
surrounding cells, forming one continuous image.

Reuses phase6_small for the piece set and cut emitter. The constant
overrides for the small puzzle (80x80mm panel, 40mm cells) propagate
because phase6_small is imported first.

Usage:
  python phase7_raster.py --image baby.jpg
  python phase7_raster.py --image baby.jpg --mode grayscale
  python phase7_raster.py --test-pattern --word N
"""

from __future__ import annotations

import argparse
import sys
from itertools import groupby
from pathlib import Path

from PIL import Image, ImageDraw

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

# Importing phase6_small triggers the small-puzzle constant overrides AND
# loads phase5/phase4/phase2 in the right order.
import phase6_small as p6  # noqa: E402

OUT_FIG_DIR = SCRIPT_DIR.parent / "figs"
BUILD_DIR = SCRIPT_DIR.parent / "build"
OUT_FIG_DIR.mkdir(parents=True, exist_ok=True)
BUILD_DIR.mkdir(parents=True, exist_ok=True)

# Conservative engrave defaults — tune via the calibration script before
# committing material. Engrave power for both halftone and grayscale is the
# MAXIMUM power; halftone fires at exactly this on every "on" pixel, and
# grayscale modulates between 0 and this based on pixel darkness.
DEFAULT_LINE_SPACING_MM = 0.20  # ~127 DPI
DEFAULT_ENGRAVE_POWER_PCT = 30
DEFAULT_ENGRAVE_FEED_MM_PER_MIN = 3000
# Grayscale quantization: fewer levels = longer runs = shorter GCode.
DEFAULT_GRAYSCALE_LEVELS = 16


# ---------------------------------------------------------------------------
# Image preprocessing
# ---------------------------------------------------------------------------


def _generate_test_pattern(pixels_per_side: int) -> Image.Image:
    """Brightness gradient L->R + a darker disc in the center.
    Exercises both grayscale modulation and dithering."""
    img = Image.new("L", (pixels_per_side, pixels_per_side), 255)
    d = ImageDraw.Draw(img)
    for x in range(pixels_per_side):
        shade = int(255 * x / max(pixels_per_side - 1, 1))
        d.line([(x, 0), (x, pixels_per_side)], fill=shade)
    cx = pixels_per_side // 2
    r = pixels_per_side // 4
    d.ellipse([cx - r, cx - r, cx + r, cx + r], fill=64)
    return img


def load_and_preprocess(
    path: Path | None, panel_mm: float, line_spacing_mm: float, test_pattern: bool
) -> Image.Image:
    """Load (or generate test pattern), convert to grayscale, crop to square,
    resize so 1 pixel == 1 raster line at line_spacing_mm."""
    pixels_per_side = int(round(panel_mm / line_spacing_mm))
    if test_pattern:
        return _generate_test_pattern(pixels_per_side)
    if path is None:
        raise SystemExit("--image PATH or --test-pattern required")
    if not path.exists():
        raise SystemExit(f"image not found: {path}")
    img = Image.open(path).convert("L")
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side))
    return img.resize((pixels_per_side, pixels_per_side), Image.LANCZOS)


def halftone_encode(img: Image.Image) -> Image.Image:
    """Floyd-Steinberg dither to 1-bit. PIL's '1' mode does this by default."""
    return img.convert("1")


def grayscale_quantize(img: Image.Image, n_levels: int) -> Image.Image:
    """Posterize to n_levels evenly-spaced grays. Returns L-mode image.
    Run-grouping is more effective on quantized levels."""
    if n_levels <= 1:
        return img
    step = 255 / (n_levels - 1)
    lut = [int(round(round(i / step) * step)) for i in range(256)]
    return img.point(lut)


# ---------------------------------------------------------------------------
# Run extraction + raster GCode emission
# ---------------------------------------------------------------------------


def runs_in_row(row: list[int]) -> list[tuple[int, int, int]]:
    """Group consecutive same-value pixels into (start, end_inclusive, value)."""
    out = []
    idx = 0
    for value, group in groupby(row):
        length = sum(1 for _ in group)
        out.append((idx, idx + length - 1, value))
        idx += length
    return out


def emit_raster_gcode(
    img: Image.Image,
    mode: str,
    panel_mm: float,
    line_spacing_mm: float,
    engrave_power_pct: int,
    engrave_feed: int,
) -> list[str]:
    """Generate raster GCode lines (M4 dynamic; G0 over white; G1 with per-run S).

    Image Y is flipped so the top of the image maps to the top of the panel
    (machine Y=panel_mm) and the bottom maps to Y=0.
    """
    # Normalize to "L" mode: PIL's "1" (1-bit) mode returns 0/1 from .load(),
    # not 0/255, which breaks the val==255 white-skip check below.
    if img.mode != "L":
        img = img.convert("L")
    w, h = img.size
    pixels = img.load()
    max_s = int(round(engrave_power_pct * 10))

    lines = [
        f"; --- raster engrave: {mode}, {w}x{h} px @ {line_spacing_mm}mm/line ---",
        f"; max power S={max_s} ({engrave_power_pct}%), feed F={engrave_feed}",
        "M4 S0   ; arm dynamic-power mode; S in motion commands controls power",
        f"F{engrave_feed}",
    ]

    for row_idx in range(h):
        machine_y = panel_mm - (row_idx + 0.5) * line_spacing_mm
        if machine_y < 0 or machine_y > panel_mm:
            continue

        row = [pixels[x, row_idx] for x in range(w)]
        runs = runs_in_row(row)

        for start, end, val in runs:
            # Skip white runs (no burn).
            if val == 255:
                continue
            x_start = (start + 0.5) * line_spacing_mm
            x_end = (end + 0.5) * line_spacing_mm
            if mode == "halftone":
                # 1-bit: PIL stores 0=black=burn, 255=white=skip (skipped above).
                s_val = max_s
            else:
                # grayscale: darker pixel = higher S (more burn).
                s_val = int(round((255 - val) / 255 * max_s))
                if s_val <= 0:
                    continue
            lines.append(f"G0 X{x_start:.3f} Y{machine_y:.3f}")
            lines.append(f"G1 X{x_end:.3f} Y{machine_y:.3f} S{s_val}")
    lines.append("M5  ; raster done")
    return lines


# ---------------------------------------------------------------------------
# Combined output assembly
# ---------------------------------------------------------------------------


_PREAMBLE = (
    "$32=1   ; GRBL laser mode",
    "G21     ; mm",
    "G90     ; absolute",
    "M5      ; laser off",
    "G0 X0 Y0",
    "",
)


def _header(title: str, material_id: str, extra: list[str] | None = None) -> list[str]:
    out = [
        f"; {title}",
        f"; generated by lessons/laser/03_jigsaw/scratch/phase7_raster.py",
    ]
    if extra:
        out += [f"; {e}" for e in extra]
    out += [
        ";",
        ";HEAD: laser",
        f";MATERIAL: {material_id}",
        "",
        *_PREAMBLE,
    ]
    return out


def raster_only_gcode(
    raster_lines: list[str], material_id: str, image_label: str, mode: str
) -> str:
    head = _header(
        title=f"raster engrave only — image={image_label}, mode={mode}",
        material_id=material_id,
        extra=[
            "ASSUMES Z already at engraving focal height in your WCS.",
            "Run this file FIRST, verify the result, then run the _cut file.",
        ],
    )
    return "\n".join(head + raster_lines + ["", "G0 X0 Y0", ""]) + "\n"


def cut_only_gcode(cut_block: str) -> str:
    # phase6_small.emit_gcode already produces a self-contained file with
    # the validator headers and preamble; pass it through unchanged.
    return cut_block


def combined_gcode(
    raster_lines: list[str],
    cut_block: str,
    material_id: str,
    image_label: str,
    mode: str,
) -> str:
    head = _header(
        title=f"combined puzzle — image={image_label}, mode={mode}, then cut",
        material_id=material_id,
        extra=[
            "ASSUMES Z already at focal height. Same Z is used for engrave + cut.",
            "If your focal height differs between engrave and cut, run the",
            "_raster and _cut files separately and refocus between them.",
        ],
    )
    # Strip the preamble + header from the cut block since we already emitted ours.
    cut_lines = cut_block.splitlines()
    body_start = 0
    for i, ln in enumerate(cut_lines):
        if ln.startswith("G0 X0 Y0") and i < 20:  # find the preamble's park line
            body_start = i + 1
            break
    cut_body = cut_lines[body_start:]
    return (
        "\n".join(head + raster_lines + [""] + ["; --- cut phase ---"] + cut_body)
        + "\n"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--image", type=Path, help="path to source photo (any common format)"
    )
    src.add_argument(
        "--test-pattern",
        action="store_true",
        help="generate a built-in gradient+disc test pattern instead of loading a photo",
    )
    ap.add_argument("--word", default="N")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--material", default="plywood_baltic_birch_3mm")
    ap.add_argument(
        "--mode",
        choices=["halftone", "grayscale"],
        default="halftone",
        help="halftone = binary dither at fixed power (default; clean, calibration-tolerant); "
        "grayscale = variable power per pixel (smoother gradients, needs calibrated power curve)",
    )
    ap.add_argument("--line-spacing-mm", type=float, default=DEFAULT_LINE_SPACING_MM)
    ap.add_argument(
        "--engrave-power-percent",
        type=int,
        default=DEFAULT_ENGRAVE_POWER_PCT,
        help="MAX power for the engrave. halftone always uses this; grayscale "
        "scales 0..MAX based on pixel darkness.",
    )
    ap.add_argument("--engrave-feed", type=int, default=DEFAULT_ENGRAVE_FEED_MM_PER_MIN)
    ap.add_argument("--grayscale-levels", type=int, default=DEFAULT_GRAYSCALE_LEVELS)
    args = ap.parse_args()

    panel_mm = p6.p2.PANEL_MM
    word = args.word.upper()
    stem = args.image.stem if args.image else "test_pattern"
    image_label = str(args.image) if args.image else "test-pattern"

    print(
        f"phase7 raster: word={word} panel={panel_mm}x{panel_mm}mm "
        f"line_spacing={args.line_spacing_mm}mm mode={args.mode}"
    )

    img = load_and_preprocess(
        args.image, panel_mm, args.line_spacing_mm, args.test_pattern
    )
    print(f"  preprocessed: {img.size} pixels")

    if args.mode == "halftone":
        encoded = halftone_encode(img)
    else:
        encoded = grayscale_quantize(img, args.grayscale_levels)
    preview_path = OUT_FIG_DIR / f"raster_preview_{stem}_{args.mode}.png"
    encoded.save(preview_path)
    print(f"  encoded preview: {preview_path}")

    raster_lines = emit_raster_gcode(
        encoded,
        args.mode,
        panel_mm,
        args.line_spacing_mm,
        args.engrave_power_percent,
        args.engrave_feed,
    )
    print(f"  raster gcode: {len(raster_lines)} lines")

    # Reuse phase6_small for the cut GCode.
    pieces, stats = p6.generate_pieces(word, args.seed)
    print(f"  pieces: {len(pieces)}  tabs: {stats}")
    ordered = p6.order_inside_out(pieces)
    material = p6.load_material(args.material)
    cut_block = p6.emit_gcode(ordered, material, word)
    print(f"  cut gcode: {len(cut_block.splitlines())} lines")

    # Three output files.
    raster_path = BUILD_DIR / f"{stem}_{args.mode}_raster.gcode"
    cut_path = BUILD_DIR / f"{stem}_{args.mode}_cut.gcode"
    full_path = BUILD_DIR / f"{stem}_{args.mode}_full.gcode"
    raster_path.write_text(
        raster_only_gcode(raster_lines, args.material, image_label, args.mode)
    )
    cut_path.write_text(cut_only_gcode(cut_block))
    full_path.write_text(
        combined_gcode(raster_lines, cut_block, args.material, image_label, args.mode)
    )
    print(f"-> {raster_path}")
    print(f"-> {cut_path}")
    print(f"-> {full_path}")
    print("\nValidate with:")
    for p in (raster_path, cut_path, full_path):
        print(f"  python cnc.py validate {p.relative_to(p6.REPO_ROOT)}")


if __name__ == "__main__":
    main()
