"""Image encoders for jigsawzall's raster engraving.

Pure-function module: take a source image + a target pixel grid,
produce an L-mode image ready for raster GCode emission. Two modes:

- **halftone**: PIL's Floyd-Steinberg dither to 1-bit, returned as L
  so downstream code can use uniform 0/255 pixel values. Operator
  evaluates as binary on/off at a fixed laser power.

- **grayscale**: posterize to N levels. Downstream emitter modulates
  laser power per pixel based on darkness. Needs an empirical gamma
  LUT (on the roadmap) for accurate tonal reproduction.

The encoder is size-agnostic: it pairs with any puzzle config.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


# ---------------------------------------------------------------------------
# Image loading + preprocessing
# ---------------------------------------------------------------------------


def generate_test_pattern(pixels_per_side: int) -> Image.Image:
    """Brightness gradient L->R + a darker disc in the center.
    Exercises both grayscale modulation and halftone dithering."""
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
    path: Path | None,
    panel_mm: float,
    line_spacing_mm: float,
    test_pattern: bool = False,
) -> Image.Image:
    """Load (or generate test pattern), convert to grayscale, crop to
    square, resize so 1 pixel == 1 raster line at line_spacing_mm."""
    pixels_per_side = int(round(panel_mm / line_spacing_mm))
    if test_pattern:
        return generate_test_pattern(pixels_per_side)
    if path is None:
        raise ValueError("path required when test_pattern is False")
    if not Path(path).exists():
        raise FileNotFoundError(f"image not found: {path}")
    img = Image.open(path).convert("L")
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side))
    return img.resize((pixels_per_side, pixels_per_side), Image.LANCZOS)


# ---------------------------------------------------------------------------
# Encoders
# ---------------------------------------------------------------------------


def halftone_encode(img: Image.Image) -> Image.Image:
    """Floyd-Steinberg dither to 1-bit, returned as L mode so pixel values
    are uniformly 0 or 255 (avoids the PIL "1"-mode .load() returns 0/1
    bug that bit the scratch version of this code)."""
    return img.convert("1").convert("L")


def grayscale_quantize(img: Image.Image, n_levels: int = 16) -> Image.Image:
    """Posterize to n_levels evenly-spaced grays. Run-grouping in the
    raster emitter is more effective on quantized levels (long runs of
    same value compress into single G1 segments)."""
    if n_levels <= 1:
        return img
    step = 255 / (n_levels - 1)
    lut = [int(round(round(i / step) * step)) for i in range(256)]
    return img.point(lut)
