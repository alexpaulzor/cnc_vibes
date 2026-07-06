"""Per-glyph "grid origin" lookup table for the letter-aligned puzzle grid.

The letter-aligned grid derives its lines from the letters themselves: a
vertical grid line passes THROUGH each glyph at its origin-x, and the
horizontal row boundary is a polyline that bends to pass through each
glyph's origin-y. The "grid origin" is a semantically meaningful anchor
point inside a glyph — e.g. an O's origin is its center, an L's origin is
the inner corner where its two bars meet.

`GLYPH_GRID_ORIGIN[char]` is that anchor in NORMALIZED ink-bbox
coordinates: (0, 0) = top-left of the glyph's ink bounding box, (1, 1) =
bottom-right. So (0.5, 0.5) is the ink-bbox center.

These start as heuristic guesses and are meant to be corrected by eye
using the `jigsaw.py glyphs` contact sheet — tweak the values here until
the crosshairs sit where the grid should cross each glyph.
"""

from __future__ import annotations

# Default anchor for any glyph not listed below: ink-bbox center.
DEFAULT_ORIGIN: tuple[float, float] = (0.5, 0.5)

# Heuristic first guesses (shown as the RED crosshair on the contact sheet).
# Do not edit these to record review decisions — put those in
# USER_ORIGIN_OVERRIDES so the sheet can show guess-vs-chosen side by side.
BASELINE_GLYPH_GRID_ORIGIN: dict[str, tuple[float, float]] = {
    # Closed round / symmetric — center (per user: "O = center").
    "O": (0.50, 0.50),
    "Q": (0.50, 0.50),
    "D": (0.50, 0.50),
    "G": (0.50, 0.50),
    "0": (0.50, 0.50),
    "8": (0.50, 0.50),
    # Open-on-the-right curve — anchor on the left stroke (per user's C dot).
    "C": (0.18, 0.475),
    # Left-vertical-stem letters — anchor on the spine, just above center.
    # Propagated from the user's B/E placements (~0.24, 0.475).
    "B": (0.24, 0.475),
    "E": (0.24, 0.475),
    "F": (0.24, 0.475),
    "H": (0.24, 0.475),
    "K": (0.24, 0.475),
    "M": (0.24, 0.475),
    "N": (0.24, 0.475),
    "P": (0.24, 0.475),
    "R": (0.24, 0.475),
    # Strong low-left corner.
    "L": (0.24, 0.72),
    # Top-heavy junction (crossbar meets stem near the top).
    "T": (0.50, 0.14),
    # Apex letters — anchor near the junction.
    "A": (0.50, 0.62),
    "V": (0.50, 0.30),
    "W": (0.50, 0.30),
    "Y": (0.50, 0.40),
    # Narrow uprights.
    "I": (0.50, 0.50),
    "1": (0.50, 0.50),
    "J": (0.40, 0.40),
}

# Corrections adopted from contact-sheet review (shown as the BLUE crosshair).
# Populated from the green dots the user places on figs/glyph_origins.png,
# read back via `jigsaw.py glyphs --read <edited.png>` (raw placement).
USER_ORIGIN_OVERRIDES: dict[str, tuple[float, float]] = {
    "A": (0.505, 0.680),
    "B": (0.217, 0.475),
    "C": (0.171, 0.475),
    "E": (0.253, 0.474),
    "F": (0.263, 0.473),
    "G": (0.907, 0.512),
    "H": (0.318, 0.464),
    "J": (0.742, 0.887),
    "K": (0.266, 0.508),
    "L": (0.260, 0.895),
    "M": (0.512, 0.648),
    "N": (0.219, 0.463),
    "P": (0.230, 0.462),
    "R": (0.298, 0.563),
    "U": (0.495, 0.902),
    "V": (0.516, 0.904),
}

# Active table = baseline with user corrections layered on top.
GLYPH_GRID_ORIGIN: dict[str, tuple[float, float]] = {
    **BASELINE_GLYPH_GRID_ORIGIN,
    **USER_ORIGIN_OVERRIDES,
}


def baseline_grid_origin(char: str) -> tuple[float, float]:
    """The heuristic first guess for a glyph (RED crosshair)."""
    return BASELINE_GLYPH_GRID_ORIGIN.get(char.upper(), DEFAULT_ORIGIN)


def glyph_grid_origin(char: str) -> tuple[float, float]:
    """Adopted normalized (x, y) grid origin for a glyph (BLUE crosshair).
    Case-insensitive; unknown glyphs fall back to the ink-bbox center."""
    return GLYPH_GRID_ORIGIN.get(char.upper(), DEFAULT_ORIGIN)


def glyph_origin_px(
    char: str,
    ink_bbox_px: tuple[float, float, float, float],
    use_baseline: bool = False,
) -> tuple[float, float]:
    """Map a glyph's normalized origin into absolute pixel coordinates,
    given its ink bounding box (left, top, right, bottom) in pixel space.

    ink_bbox_px is the glyph's actual rendered ink box in the target image
    (e.g. from `font.getbbox(char)` offset to the pen position, or measured
    on the rendered mask). use_baseline=True returns the heuristic guess
    (red crosshair) instead of the adopted value (blue)."""
    nx, ny = baseline_grid_origin(char) if use_baseline else glyph_grid_origin(char)
    l, t, r, b = ink_bbox_px
    return (l + nx * (r - l), t + ny * (b - t))


# ---------------------------------------------------------------------------
# Automatic, font-independent origin from the glyph raster.
# ---------------------------------------------------------------------------
#
# The manual LUT above was the discovery exercise (the green dots only
# demonstrated intent — "sit on the stem" — not exact targets). This derives
# the anchor directly from a glyph's rendered ink so any letter/font works
# with no table, snapping to the glyph's dominant stroke:
#   origin-x = the leftmost FULL-HEIGHT vertical stroke (stem/back); if the
#              glyph has no such stem (pure diagonals/curves) -> ink centroid.
#   origin-y = the vertical center of that stem's ink run (mid-height for a
#              full stem); centroid-y when there's no stem.


def _longest_run(mask_1d) -> int:
    best = cur = 0
    for v in mask_1d:
        cur = cur + 1 if v else 0
        if cur > best:
            best = cur
    return best


def _longest_run_span(mask_1d) -> tuple[int, int, int]:
    """(length, start, end) of the longest contiguous True run."""
    best = cur = 0
    bs = be = cs = 0
    for i, v in enumerate(mask_1d):
        if v:
            if cur == 0:
                cs = i
            cur += 1
            if cur > best:
                best, bs, be = cur, cs, i
        else:
            cur = 0
    return best, bs, be


def _clusters(flags) -> list[tuple[int, int]]:
    """Contiguous True-runs in a bool sequence as (start, end) index pairs."""
    out = []
    s = None
    for i, v in enumerate(flags):
        if v and s is None:
            s = i
        elif not v and s is not None:
            out.append((s, i - 1))
            s = None
    if s is not None:
        out.append((s, len(flags) - 1))
    return out


def auto_glyph_origin(ink, stem_frac: float = 0.65) -> tuple[float, float]:
    """Compute a normalized (nx, ny) grid origin from a glyph ink mask
    (2D bool array, True = ink), snapping to the glyph's dominant stroke.
    Returns coordinates normalized to the glyph's ink bounding box, matching
    glyph_grid_origin's convention."""
    import numpy as np

    ys, xs = np.nonzero(ink)
    if len(xs) == 0:
        return (0.5, 0.5)
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    gw, gh = x1 - x0 + 1, y1 - y0 + 1

    # Longest vertical ink run per column (and its extent, for the y anchor).
    spans = [_longest_run_span(ink[:, c]) for c in range(x0, x1 + 1)]
    runs = [s[0] for s in spans]
    stem = [r >= stem_frac * gh for r in runs]

    if any(stem):
        a, b = _clusters(stem)[0]  # leftmost full-height stem cluster
        rep = max(range(a, b + 1), key=lambda i: runs[i])  # tallest col in it
        origin_x = x0 + (a + b) / 2.0
        _len, bs, be = spans[rep]
        origin_y = (bs + be) / 2.0  # center of that stem's run (mid for a full stem)
    else:
        origin_x = float(xs.mean())  # no stem: fall back to the ink centroid
        origin_y = float(ys.mean())

    nx = (origin_x - x0) / gw if gw else 0.5
    ny = (origin_y - y0) / gh if gh else 0.5
    return (round(float(nx), 3), round(float(ny), 3))


def glyph_seam_x(ink) -> float:
    """Normalized seam-x (0..1 across the full raster). Thin wrapper over
    glyph_seam; see there for the rule."""
    return glyph_seam(ink)[0]


def glyph_seam(ink) -> tuple[float, bool]:
    """Return (seam_x, through_ok) for a glyph:

    seam_x — normalized x (0..1 across the FULL glyph raster) for the vertical
    seam, in the raster frame so the caller can denormalize with the same width
    it rasterized at:
      * ink CENTER when the glyph has ink there — crossbar / mid-arm / central
        stem / diagonal (H E A N S T M X Y I Z) or a symmetric closed ring
        (O Q D B U W). Both halves keep a solid, comparable notch.
      * else the dominant FULL-HEIGHT vertical stroke (L's stem, C's back).
      * else the ink centroid (pure diagonal/round).

    through_ok — whether it's SAFE to run that vertical seam THROUGH the glyph.
    False for "capped open" round letters (C, G): a wide stroke at both the top
    AND bottom wrapping a hollow center. Splitting those through the back shears
    the thin curved spine into a fragile sliver, so the caller keeps them whole
    (routes the seam into a gap instead). Straight-stem letters (L, F, J) stay
    splittable — only one end is capped.
    """
    import numpy as np

    W = ink.shape[1]
    ys, xs = np.nonzero(ink)
    if len(xs) == 0 or W == 0:
        return 0.5, True
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    gw, gh = x1 - x0 + 1, y1 - y0 + 1

    # Center-supported: ink in the central box, OR ink on BOTH sides at
    # mid-height (a closed ring like O — symmetric, so a center seam yields two
    # robust halves even though the middle is hollow). An OPEN glyph (C, L) has
    # ink on only one side at mid-height and fails this test.
    cx0, cx1 = x0 + int(0.40 * gw), x0 + int(0.60 * gw) + 1
    cy0, cy1 = y0 + int(0.40 * gh), y0 + int(0.60 * gh) + 1
    band = ink[cy0:cy1, :]
    third = max(1, int(0.35 * gw))
    left_has = band[:, x0 : x0 + third].any()
    right_has = band[:, x1 - third + 1 : x1 + 1].any()
    if ink[cy0:cy1, cx0:cx1].any() or (left_has and right_has):
        return (x0 + x1) / 2.0 / W, True  # ink center, in the raster frame

    # No central support: seam through the dominant full-height vertical stroke.
    # But a "capped open" letter (C, G) — wide stroke at BOTH top and bottom
    # around a hollow middle — must not be split through that thin curved back.
    def _wide(band_rows):
        return (
            bool(band_rows.sum(axis=1).max() >= 0.6 * gw) if band_rows.size else False
        )

    cap = max(1, int(0.20 * gh))
    capped = _wide(ink[y0 : y0 + cap, x0 : x1 + 1]) and _wide(
        ink[y1 - cap + 1 : y1 + 1, x0 : x1 + 1]
    )
    runs = [_longest_run(ink[:, c]) for c in range(x0, x1 + 1)]
    stem = [r >= 0.65 * gh for r in runs]
    clusters = _clusters(stem)
    if clusters:
        a, b = max(clusters, key=lambda ab: sum(runs[ab[0] : ab[1] + 1]))
        return (x0 + (a + b) / 2.0) / W, not capped  # stroke center, raster frame
    return float(xs.mean()) / W, not capped  # ink centroid, raster frame



def glyph_hcut_y(ink) -> float:
    """Normalized y (0..1 across the FULL glyph raster) for the HORIZONTAL split
    line through this glyph — the row boundary that divides the top/bottom
    pieces. Feature-anchored so the boundary undulates letter-to-letter:

      * If the glyph has a strong horizontal stroke near its middle (a crossbar
        or arm — A's bar sits low, H/E's sit mid), cut THROUGH it so each half
        keeps a solid edge. Different letters' bars sit at different heights, so
        the boundary rises and falls across the name.
      * Otherwise (open/round C G, or a plain stem) fall back to the ink
        centroid, which for a C lands in the middle of the mouth -> two robust
        arcs, no thin twig.

    Searched only in the central band so the split never skims a letter's very
    top/bottom (which would leave an untabbable sliver)."""
    import numpy as np

    H = ink.shape[0]
    ys, xs = np.nonzero(ink)
    if len(ys) == 0 or H == 0:
        return 0.5
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    gw, gh = x1 - x0 + 1, y1 - y0 + 1

    lo, hi = y0 + int(0.28 * gh), y0 + int(0.72 * gh)
    rowsum = ink[:, x0 : x1 + 1].sum(axis=1)
    band = rowsum[lo : hi + 1]
    if len(band) and band.max() >= 0.6 * gw:  # a real horizontal stroke spans it
        y = lo + int(np.argmax(band))
    else:
        y = float(ys.mean())  # no central bar -> centroid (C: mouth center)
    return y / H


def glyph_stroke_anchors(ink, stem_frac: float = 0.6) -> list[tuple[float, float]]:
    """All candidate seam anchors for a glyph: the center (nx, ny) of every
    full-height vertical stroke (e.g. N's left bar, right bar; O's two arcs).
    Normalized to the ink bbox. Falls back to a single centroid anchor when
    the glyph has no full-height stroke (pure diagonals/curves). The layout
    chooses among these to keep piece sizes in range (a big letter can take
    a seam on more than one of its strokes)."""
    import numpy as np

    ys, xs = np.nonzero(ink)
    if len(xs) == 0:
        return [(0.5, 0.5)]
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    gw, gh = x1 - x0 + 1, y1 - y0 + 1
    spans = [_longest_run_span(ink[:, c]) for c in range(x0, x1 + 1)]
    runs = [s[0] for s in spans]
    stem = [r >= stem_frac * gh for r in runs]

    anchors = []
    for a, b in _clusters(stem):
        rep = max(range(a, b + 1), key=lambda i: runs[i])
        _len, bs, be = spans[rep]
        cx = x0 + (a + b) / 2.0
        cy = (bs + be) / 2.0
        anchors.append(((cx - x0) / gw, (cy - y0) / gh))
    if not anchors:
        anchors = [((float(xs.mean()) - x0) / gw, (float(ys.mean()) - y0) / gh)]
    return [(round(nx, 3), round(ny, 3)) for nx, ny in anchors]
