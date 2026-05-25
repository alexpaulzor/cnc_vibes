#!/usr/bin/env python3
"""Mockup: what the finished NORA puzzle would look like with a photo
rastered onto it. Visualization only — not a cuttable artifact.

Renders two side-by-side panels: halftone vs grayscale rendering of the
same photo, both overlaid with the full 44-piece cut pattern and the
NORA letters highlighted. Simulates wood color + engraved (burned)
regions so the comparison shows what each technique would actually
look like on the final piece.

Doesn't depend on phase7_raster (which forces small-puzzle constants
via phase6_small import). Uses phase8 for the full-puzzle polygons.

Usage:
  python mockup_photo_puzzle.py --image /path/to/photo.jpg
  python mockup_photo_puzzle.py --image /tmp/cnc_mockup/kitten.jpg --word NORA
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import MultiPolygon, Polygon

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

# Use phase8's default 300x300 NORA pipeline
import phase8_full_puzzle as p8  # noqa: E402

FIG_DIR = SCRIPT_DIR.parent / "figs"
FIG_DIR.mkdir(parents=True, exist_ok=True)

# Wood-color palette for the mockup
WOOD_LIGHT = (228, 204, 168)  # unburned MDF/plywood
WOOD_DARK = (78, 50, 28)  # fully-burned (max engrave power)
CUT_LINE = (40, 30, 20)  # cut path color (slightly darker than burn)
LETTER_HIGHLIGHT = (160, 60, 60)  # subtle red so letters pop


# ---------------------------------------------------------------------------
# Image encoders (inlined from phase7 to avoid the phase6_small import)
# ---------------------------------------------------------------------------


def halftone_encode(img: Image.Image) -> Image.Image:
    """Floyd-Steinberg dither to 1-bit, then back to L for compositing."""
    return img.convert("1").convert("L")


def grayscale_quantize(img: Image.Image, n_levels: int = 16) -> Image.Image:
    if n_levels <= 1:
        return img
    step = 255 / (n_levels - 1)
    lut = [int(round(round(i / step) * step)) for i in range(256)]
    return img.point(lut)


# ---------------------------------------------------------------------------
# Wood-mockup composition
# ---------------------------------------------------------------------------


def wood_mockup_from_gray(gray: Image.Image) -> Image.Image:
    """Map an L-mode image to wood-color RGB:
    pixel 255 (white) -> WOOD_LIGHT (unburned)
    pixel 0   (black) -> WOOD_DARK  (fully burned)
    Linear interpolation in between (so grayscale shows continuous tone)."""
    rgb = Image.new("RGB", gray.size)
    pixels_in = gray.load()
    pixels_out = rgb.load()
    for y in range(gray.height):
        for x in range(gray.width):
            v = pixels_in[x, y]  # 0..255; 0=black=burn
            t = v / 255.0
            r = int(WOOD_DARK[0] + (WOOD_LIGHT[0] - WOOD_DARK[0]) * t)
            g = int(WOOD_DARK[1] + (WOOD_LIGHT[1] - WOOD_DARK[1]) * t)
            b = int(WOOD_DARK[2] + (WOOD_LIGHT[2] - WOOD_DARK[2]) * t)
            pixels_out[x, y] = (r, g, b)
    return rgb


def draw_cut_pattern(
    img: Image.Image, pieces: list[dict], px_per_mm: float = 4.0
) -> None:
    """Overlay the puzzle cut paths + letter highlights on a mockup image.
    pieces come from phase8.generate_pieces which uses phase2's coord system
    (image pixels with MARGIN inset at PX_PER_MM=5). We translate to the
    mockup's px_per_mm scale."""
    d = ImageDraw.Draw(img)
    scale = px_per_mm / p8.p2.PX_PER_MM

    def to_mockup(x_phase, y_phase):
        return (
            (x_phase - p8.p2.MARGIN) * scale,
            (y_phase - p8.p2.MARGIN) * scale,
        )

    for piece in pieces:
        poly = piece["polygon"]
        if isinstance(poly, MultiPolygon):
            polys = list(poly.geoms)
        else:
            polys = [poly]
        for p in polys:
            pts = [to_mockup(x, y) for x, y in p.exterior.coords]
            d.line(pts, fill=CUT_LINE, width=2)
            for interior in p.interiors:
                ipts = [to_mockup(x, y) for x, y in interior.coords]
                d.line(ipts, fill=CUT_LINE, width=2)

    # Letter pieces get a soft red outline so they pop in the mockup
    for piece in pieces:
        if piece.get("kind") != "letter":
            continue
        poly = piece["polygon"]
        polys = list(poly.geoms) if isinstance(poly, MultiPolygon) else [poly]
        for p in polys:
            pts = [to_mockup(x, y) for x, y in p.exterior.coords]
            # Slightly outboard so it doesn't overdraw the cut line
            d.line(pts, fill=LETTER_HIGHLIGHT, width=3)


def load_font(size: int) -> ImageFont.ImageFont:
    for path in (
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--image", required=True, type=Path)
    ap.add_argument("--word", default="NORA")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--px-per-mm", type=float, default=4.0)
    ap.add_argument(
        "--out",
        default=None,
        help="output path (default figs/mockup_<word>_<image-stem>.png)",
    )
    args = ap.parse_args()
    word = args.word.upper()

    # 1. Generate the full-puzzle polygons
    print(f"generating {word} puzzle polygons...")
    pieces, stats = p8.generate_pieces(word, args.seed)
    print(f"  {len(pieces)} pieces, tabs: {stats}")

    # 2. Load image, crop to square, resize to panel size
    panel_px = int(p8.p2.PANEL_MM * args.px_per_mm)
    print(f"loading image: {args.image}")
    src = Image.open(args.image).convert("L")
    w, h = src.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    src = src.crop((left, top, left + side, top + side))
    src = src.resize((panel_px, panel_px), Image.LANCZOS)
    print(f"  cropped + resized to {panel_px}x{panel_px}")

    # 3. Encode in both modes
    print("encoding halftone + grayscale...")
    ht = halftone_encode(src)
    # 6 levels = obviously visible posterization, helps the comparison
    # read clearly. Real cutting would use 8-16 levels for smoother tones.
    gs = grayscale_quantize(src, n_levels=6)

    # 4. Wood-mockup each
    print("compositing wood-color mockups...")
    mockup_ht = wood_mockup_from_gray(ht)
    mockup_gs = wood_mockup_from_gray(gs)

    # 5. Overlay cut pattern on each
    print("overlaying cut pattern...")
    draw_cut_pattern(mockup_ht, pieces, args.px_per_mm)
    draw_cut_pattern(mockup_gs, pieces, args.px_per_mm)

    # 6. Compose side-by-side comparison with zoom-in detail boxes
    # Zoom: take a 200x200 px region from each mockup, scale 4x (no
    # interpolation) so the dots / quantization steps are clearly visible.
    zoom_src_px = 200
    zoom_scale = 4
    zoom_size = zoom_src_px * zoom_scale  # 800
    # Pick a region with interesting tonal variation — the cat's face area,
    # roughly centered. Avoid the dead-center where NORA outline is busy.
    zoom_x0 = panel_px // 2 - zoom_src_px - 60
    zoom_y0 = panel_px // 4
    zoom_ht_src = mockup_ht.crop(
        (zoom_x0, zoom_y0, zoom_x0 + zoom_src_px, zoom_y0 + zoom_src_px)
    )
    zoom_gs_src = mockup_gs.crop(
        (zoom_x0, zoom_y0, zoom_x0 + zoom_src_px, zoom_y0 + zoom_src_px)
    )
    zoom_ht = zoom_ht_src.resize((zoom_size, zoom_size), Image.NEAREST)
    zoom_gs = zoom_gs_src.resize((zoom_size, zoom_size), Image.NEAREST)

    label_h = 60
    caption_h = 90
    gap = 20
    panel_block_w = max(panel_px, zoom_size) + gap  # accommodate either width
    total_w = panel_block_w * 2 + gap * 2
    total_h = panel_px + zoom_size + label_h + caption_h + gap * 4
    comparison = Image.new("RGB", (total_w, total_h), (250, 250, 250))
    cd = ImageDraw.Draw(comparison)
    label_font = load_font(36)
    caption_font = load_font(20)
    zoom_label_font = load_font(22)

    title = f"{word} puzzle mockup — halftone vs grayscale photo engraving"
    title_font = load_font(28)
    cd.text((gap, 16), title, fill=(20, 20, 20), font=title_font)

    # Outline the zoom-source region on each main panel for clarity
    zoom_box_color = (200, 30, 30)

    def panel(
        ix: int, mockup: Image.Image, zoom: Image.Image, heading: str, caption: str
    ):
        x = gap + ix * panel_block_w
        y = label_h
        comparison.paste(mockup, (x, y))
        # Draw the zoom source rectangle on the main mockup
        bx0, by0 = x + zoom_x0, y + zoom_y0
        cd.rectangle(
            [bx0, by0, bx0 + zoom_src_px, by0 + zoom_src_px],
            outline=zoom_box_color,
            width=3,
        )
        # Label below main mockup
        cd.text((x, y + panel_px + 8), heading, fill=(20, 20, 20), font=label_font)
        cd.multiline_text(
            (x, y + panel_px + 50), caption, fill=(60, 60, 60), font=caption_font
        )
        # Zoom detail below caption
        zy = y + panel_px + caption_h + label_h
        comparison.paste(zoom, (x, zy))
        cd.rectangle(
            [x - 1, zy - 1, x + zoom_size + 1, zy + zoom_size + 1],
            outline=zoom_box_color,
            width=2,
        )
        cd.text(
            (x, zy - 28),
            f"4x zoom of red box (raw pixels, no interpolation)",
            fill=zoom_box_color,
            font=zoom_label_font,
        )

    panel(
        0,
        mockup_ht,
        zoom_ht,
        "HALFTONE (Floyd-Steinberg, fixed power)",
        "Binary on/off pixels at one fixed laser power.\n"
        "Calibration-tolerant. Dot pattern visible up close,\n"
        "photographic effect at arm's length.",
    )
    panel(
        1,
        mockup_gs,
        zoom_gs,
        "GRAYSCALE (variable power per pixel)",
        "Per-pixel laser power scaled to image darkness.\n"
        "Smoother tonal gradients. Needs a calibrated\n"
        "power-vs-darkness curve for accurate tones.",
    )

    out_path = (
        Path(args.out)
        if args.out
        else FIG_DIR / f"mockup_{word.lower()}_{args.image.stem}.png"
    )
    comparison.save(out_path, "PNG", optimize=True)
    print(f"-> {out_path}  ({total_w}x{total_h})")


if __name__ == "__main__":
    main()
