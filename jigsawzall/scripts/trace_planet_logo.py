#!/usr/bin/env python3
"""One-off extraction: trace the real Planet Labs wordmark ("planet.") and
brand ring from a photo of an actual sticker into reusable vector data
(data/planet_logo_glyphs.json), instead of approximating their trademark
from memory or a system font. Alex is a Planet Labs employee making this
puzzle for his own desk -- see RING_SPEC.md for the full story.

Not meant to be re-run casually: SOURCE_IMAGE and all the pixel-box
coordinates below are specific to one photo. Re-run only if re-tracing
from a new/better source photo (crop it upright first, letters reading
left to right, same as SOURCE_IMAGE).

    PYTHONPATH=. python3 scripts/trace_planet_logo.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from PIL import Image, ImageFilter
from shapely.affinity import translate

import geometry as G

SOURCE_IMAGE = str(
    Path(__file__).resolve().parent.parent
    / "data"
    / "planet_logo_source_tight.png"
)

# Pixel boxes in SOURCE_IMAGE, hand-picked by inspecting the traced photo.
LETTER_BOXES = {
    "p": (141, 174, 251, 313),
    "l": (268, 144, 291, 276),
    "a": (307, 168, 416, 289),
    "n": (437, 174, 528, 275),
    "e": (546, 174, 649, 278),
    "t": (658, 144, 708, 275),
}
PERIOD_BOX = (713, 255, 734, 276)
BASELINE_Y = 277.0
ASCEND_TOP_Y = 144.0  # l/t top -> the "cap height" scaling reference


def _dark_teal_masks(img):
    a = np.array(img.convert("RGB")).astype(int)
    R, G_, B = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    brightness = (R + G_ + B) / 3
    dark = (brightness < 160) & (np.abs(R - G_) < 25) & (np.abs(G_ - B) < 25)
    teal = (brightness < 200) & ((G_ - R) > 12) & ((B - R) > 12)
    return dark, teal


def _trace_box(mask, box, pad=6, close_px=2):
    """Crop `box` (+pad) out of `mask`, close small gaps (JPEG/threshold
    artifacts otherwise split a glyph's counter into two polygons -- caught
    on 'a', whose bowl wasn't quite closed), trace, translate back to the
    source image's coordinate frame."""
    x0, y0, x1, y1 = box
    x0 -= pad
    y0 -= pad
    x1 += pad
    y1 += pad
    sub = (mask[y0:y1, x0:x1] * 255).astype(np.uint8)
    im = Image.fromarray(sub, mode="L")
    im = im.filter(ImageFilter.MaxFilter(2 * close_px + 1)).filter(
        ImageFilter.MinFilter(2 * close_px + 1)
    )
    poly = G._trace_mask_polygons(im)
    return translate(poly, x0, y0)


def main():
    img = Image.open(SOURCE_IMAGE)
    dark_mask, teal_mask = _dark_teal_masks(img)

    glyphs = {}
    p_raw_cx = None
    for ch, box in LETTER_BOXES.items():
        poly = _trace_box(dark_mask, box)
        minx, miny, maxx, maxy = poly.bounds
        cx = (minx + maxx) / 2
        if ch == "p":
            p_raw_cx = cx
        glyphs[ch] = translate(poly, -cx, -BASELINE_Y)

    period_poly = _trace_box(teal_mask, PERIOD_BOX)
    minx, miny, maxx, maxy = period_poly.bounds
    glyphs["."] = translate(period_poly, -(minx + maxx) / 2, -BASELINE_Y)

    # Ring: fit a circle to the teal pixels directly (bbox-based) rather than
    # tracing its outline -- the ring is partly OCCLUDED by the "p" ink
    # drawn on top, so its traced outline is a broken arc-set, not a clean
    # annulus. A clean computed circle (used for ETCHING, never a physical
    # cut -- a ring this thin relative to puzzle scale would be a fragile
    # sliver, see RING_SPEC.md) is both more accurate and simpler.
    ys, xs = np.where(teal_mask)
    keep = xs < 700  # exclude the period, which is also teal
    xs, ys = xs[keep], ys[keep]
    ring_cx = (xs.min() + xs.max()) / 2
    ring_cy = (ys.min() + ys.max()) / 2
    outer_r = (xs.max() - xs.min()) / 2
    # stroke width sampled off-occlusion earlier (~15-20px); use a round
    # value rather than over-fitting to a few scanlines
    stroke_w = 18.0

    cap_ref_px = BASELINE_Y - ASCEND_TOP_Y

    out = {
        "glyphs": {k: v.wkt for k, v in glyphs.items()},
        "cap_ref_px": cap_ref_px,
        "ring": {
            # relative to the "p" glyph's own local frame (x=0 at p's ink
            # center, y=0 at baseline) -- that's the frame the ornament
            # artwork (p + ring) is built in.
            "cx": ring_cx - p_raw_cx,
            "cy": ring_cy - BASELINE_Y,
            "outer_r": outer_r,
            "inner_r": outer_r - stroke_w,
        },
    }
    out_path = Path(__file__).resolve().parent.parent / "data" / "planet_logo_glyphs.json"
    out_path.write_text(json.dumps(out))
    print(f"wrote {out_path}")
    for k, v in glyphs.items():
        print(" ", repr(k), v.geom_type, v.bounds)
    print("  ring", out["ring"])


if __name__ == "__main__":
    main()
