"""Pure-function geometry for jigsawzall.

Consolidates and parameterizes the polygon math that previously lived in
scratch/diagram_word_phase2.py and scratch/diagram_word_phase5.py with
module-level constants. All public functions here take a PuzzleConfig
object instead, so multiple puzzle sizes can coexist in one process
(unlike the scratch/ phases where phase6_small mutated phase2's globals
and phase8 had to assert phase6 wasn't loaded).

Functions are deliberately pure (no I/O, no globals beyond stdlib +
shapely + opencv + PIL). Visualization and GCode emission live in
sibling modules.

The geometry pipeline:
  1. PuzzleConfig defines panel size, piece size, tab geometry
  2. render_letter_polygons rasterizes the word and traces its contours
     into a shapely union (OpenCV findContours with RETR_CCOMP so
     letter holes — O's counter, R's bowl — are properly nested)
  3. build_pieces_with_shifted_tabs generates cell-grid pieces with
     lollipop tabs, shifting tabs along their edge to clear letter
     outlines (or dropping the tab entirely if no clear position
     exists)
  4. carve_letter_pockets subtracts the letter union from each cell
     and yields fragments
  5. merge_small_fragments absorbs thin/small fragments into their
     largest adjacent neighbor
  6. The letter union itself is appended as letter pieces

Shapes are in image-pixel coords: (MARGIN, MARGIN) is the top-left of
the panel, +X is right, +Y is DOWN. Conversion to machine mm (Y-up)
happens in the emitter.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from dataclasses import replace as dc_replace
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiPolygon,
    Point,
    Polygon,
    box,
)
from shapely.ops import polygonize, unary_union
from shapely.ops import nearest_points
from shapely.ops import split as shp_split


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class PuzzleConfig:
    """Everything the geometry functions need to know about a puzzle.

    Replaces the scratch-era module-level constants (PANEL_MM, PIECE_MM,
    TAB_CIRCLE_R, etc.) so multiple puzzle sizes can coexist in one
    process without import-order trickery.
    """

    panel_mm: float = 300.0  # outer panel WIDTH (square unless panel_h_mm set)
    panel_h_mm: float | None = None  # outer panel HEIGHT; None = square (= panel_mm)
    piece_mm: float = 50.0  # nominal cell WIDTH
    piece_h_mm: float | None = None  # nominal cell HEIGHT; None = square (= piece_mm)
    px_per_mm: int = 5  # render scale (5 px/mm = 0.2mm per pixel)
    tab_circle_r_px: int = 22  # lollipop bulb radius in pixels
    # Fat-tab (capsule) opt-in. When either is set the tab neck widens to
    # tab_stem_w_px and the round bulb becomes a stadium/capsule: two circles of
    # radius tab_circle_r_px whose centers are tab_bulb_elong_px apart, joined by
    # a rectangle (bulb width = elong + 2*R). This keeps the bulb wider than the
    # neck (undercut/lock) even with a thick neck, so pieces don't snap at the
    # stem. Both None/0 (default) => the original thin lollipop, byte-identical.
    tab_stem_w_px: float | None = None  # neck width px; None => R (old behavior)
    tab_bulb_elong_px: float = 0.0  # capsule center-to-center px; 0 => plain circle
    margin_px: int = 120  # canvas inset around the panel for rendering
    legend_h_px: int = (
        0  # extra canvas height below the panel; unused (no legend is drawn)
    )

    # Rounded outer corners. 0 (default) = sharp 90° panel corners (legacy).
    # >0 rounds the four outer panel-perimeter corners with this radius (mm).
    corner_radius_mm: float = 0.0

    # Vertically center the letter band on the nearest interior HORIZONTAL
    # grid line instead of free-centering it in the middle of the panel.
    # When the panel has an even row count this puts the letters straddling
    # a row boundary, so each row is carved symmetrically into larger,
    # tabbable chunks rather than thin mid-row slivers. Default True.
    snap_letters_to_grid: bool = True

    # Letter-aligned grid (single row of text): derive vertical grid lines from
    # each glyph's automatic origin (glyph_origins.auto_glyph_origin) so cuts
    # run along the letters' strokes; the middle row boundary bends through the
    # origins. 2-row layout. Default False = the classic uniform grid.
    letter_aligned_grid: bool = False

    # Vertex-grid layout: instead of a rectangular cell grid, tile the background
    # with letter-ANCHORED seams (vertical caps off each glyph's top/bottom to the
    # border; letter->letter gap seams that launch perpendicular, S-curve, and
    # carry the tab on a straight flat mid-span; end seams to the L/R border). No
    # seam crosses a letter. Reuses the same tabs/pockets/emitter as the grid path.
    vertex_grid: bool = False

    # Letter-aligned banner only: size the panel to the text (within the panel_mm
    # x panel_h_mm bounds) instead of forcing the text to fill fixed dimensions.
    # The aspect ratio flexes to the name — short names get a narrower panel,
    # long names a shorter one — so letters stay a comfortable size and every
    # name reserves margin for its end-column tabs (a downstream step can place
    # the fitted panel within stock). panel_mm/panel_h_mm act as MAX bounds.
    fit_to_text: bool = False
    # Optional target panel HEIGHT (mm) for a fit_to_text banner: the top/bottom
    # rows grow so the panel reaches ~this tall, giving tabs more room off the
    # borders. None = height follows the band (original behavior).
    banner_target_h_mm: float | None = None
    # Vertex-grid fit: uniform margin (mm) to leave around the letters' bounding
    # box on all four sides. The panel is sized to letters_bbox + 2*this (within
    # the stock bounds) — not crammed, not forced to fill the whole stock.
    banner_margin_mm: float | None = None
    # Vertex-grid fit: target CAP HEIGHT (mm) for the lettering, so letters stay a
    # consistent readable size across names instead of shrinking to fit width.
    # The panel WIDTH then flexes to the word (up to the stock width bound); only
    # a word too wide even at the bound shrinks the font. None = fit to bounds.
    banner_letter_h_mm: float | None = None
    # Optional font override for the lettering: an absolute path or an alias in
    # _FONT_ALIASES ('black' default, 'bold' for lighter, 'impact', 'narrow').
    # None = the default black list (Arial Black, falling back to Bold).
    font_path: str | None = None
    # Explicit fitted panel size in px (set by the fitting pass; overrides the
    # cols*cell derivation for puzzle_w_px/puzzle_h_px when present).
    panel_w_px_fit: int | None = None
    panel_h_px_fit: int | None = None

    # Shifting + merging parameters; default to "scale with tab radius"
    letter_clearance_factor: float = 1.0  # multiplied by tab_circle_r_px
    # Absolute minimum wall (mm) a tab must keep from any letter. When set it
    # overrides letter_clearance_factor: the tab shifts to a position at least
    # this far from the letters, or drops to a straight edge if none exists.
    # This is the minimum material bridge left beside a tab (raise it to stop
    # brittle thin bridges; costs dropped tabs where letters are dense).
    letter_clearance_mm: float | None = None
    fragment_min_thickness_factor: float = 1.0  # multiplied by tab_circle_r_px
    fragment_min_area_factor: float = 0.10  # fraction of (cell_w * cell_h)
    shift_steps: int = 12
    shift_step_frac: float = 0.2

    # Extra inter-letter tracking (mm) added on top of the tab-fit minimum gap
    # (tab_height + 2*bulb_radius). 0 (default) = the proven Arial-Bold spacing.
    # Arial Black is heavier/wider, so the vertex-grid banner bumps this to open
    # the letter gaps (more room for a clean gap seam + tab). Re-calibratable.
    letter_gap_extra_mm: float = 0.0

    # Vertex-grid: a straight perpendicular stub (mm) each gap seam launches off a
    # letter before it starts to curve, so the seam meets the letter at a clean
    # 90 deg with no sharp point / cusp. Effectively a minimum launch curve radius.
    min_launch_radius_mm: float = 5.0

    # Wavy-edge support. wave_amplitude_px = 0 (default) → straight edges,
    # matches the original grid-puzzle behavior and keeps all existing
    # regression tests stable. >0 enables a single-half-sine perpendicular
    # wave on each internal cell-cell edge's flat segments (corner→tab_base
    # and tab_base→corner), producing a more organic "commercial puzzle"
    # look. Panel-perimeter edges always stay straight.
    wave_amplitude_px: float = 0.0
    wave_steps: int = 12  # subdivisions per wavy segment

    # Machine-origin offset applied at GCode-emission time, in mm. Default
    # (0, 0) places the panel's bottom-left corner at WCS origin (legacy
    # behavior). Set to (panel_mm/2, panel_mm/2) to center the panel on
    # WCS origin — useful when stock is positioned around a known center
    # rather than registered against a corner.
    origin_offset_mm: tuple[float, float] = (0.0, 0.0)

    # Derived properties (computed in __post_init__ for backwards-compat
    # with phase2's int-coercion of these values)
    cols: int = field(init=False)
    rows: int = field(init=False)
    cell_w_px: int = field(init=False)
    cell_h_px: int = field(init=False)
    tab_height_px: int = field(init=False)
    tab_len_px: int = field(init=False)
    letter_clearance_px: float = field(init=False)
    fragment_min_thickness_px: float = field(init=False)
    fragment_min_area_px: float = field(init=False)

    def __post_init__(self):
        # Match phase2's int-coercion exactly so identical configs produce
        # byte-identical geometry vs the scratch code (regression-safety).
        # panel_h_mm=None means a square panel (height == width), and
        # piece_h_mm=None means square cells — both keep every existing
        # square config bit-for-bit identical.
        h_mm = self.panel_mm if self.panel_h_mm is None else self.panel_h_mm
        cell_h_mm = self.piece_mm if self.piece_h_mm is None else self.piece_h_mm
        self.cols = int(self.panel_mm // self.piece_mm)
        self.rows = int(h_mm // cell_h_mm)
        self.cell_w_px = int(self.piece_mm * self.px_per_mm)
        self.cell_h_px = int(cell_h_mm * self.px_per_mm)
        self.tab_height_px = 3 * self.tab_circle_r_px
        self.tab_len_px = max(int(0.40 * self.cell_w_px), 5 * self.tab_circle_r_px)
        if self.letter_clearance_mm is not None:
            self.letter_clearance_px = self.letter_clearance_mm * self.px_per_mm
        else:
            self.letter_clearance_px = (
                self.tab_circle_r_px * self.letter_clearance_factor
            )
        self.fragment_min_thickness_px = (
            self.tab_circle_r_px * self.fragment_min_thickness_factor
        )
        self.fragment_min_area_px = (
            self.fragment_min_area_factor * self.cell_w_px * self.cell_h_px
        )

    @property
    def panel_height_mm(self) -> float:
        """Outer panel height in mm (== panel_mm for a square panel)."""
        return self.panel_mm if self.panel_h_mm is None else self.panel_h_mm

    @property
    def puzzle_w_px(self) -> int:
        if self.panel_w_px_fit is not None:
            return self.panel_w_px_fit
        return self.cols * self.cell_w_px

    @property
    def puzzle_h_px(self) -> int:
        if self.panel_h_px_fit is not None:
            return self.panel_h_px_fit
        return self.rows * self.cell_h_px

    @property
    def bounds_w_px(self) -> int:
        """Max panel width in px (the configured bound, ignoring any fit)."""
        return self.cols * self.cell_w_px

    @property
    def bounds_h_px(self) -> int:
        """Max panel height in px (the configured bound, ignoring any fit)."""
        return self.rows * self.cell_h_px

    @property
    def canvas_w_px(self) -> int:
        return self.puzzle_w_px + 2 * self.margin_px + self.tab_height_px

    @property
    def canvas_h_px(self) -> int:
        return (
            self.puzzle_h_px
            + 2 * self.margin_px
            + self.tab_height_px
            + self.legend_h_px
        )


# Two preset configs that match the scratch-era hard-coded sizes,
# so the productionized code stays byte-equivalent for the same inputs.
# They pin font_path="bold" because their locked geometry (and the phase-script
# regression contracts) predate the Arial Black default; the heavier black face
# would merge letters on these un-respaced grids.
def small_puzzle_config() -> PuzzleConfig:
    """80x80mm panel with 40mm cells. Matches scratch/phase6_small.py."""
    return PuzzleConfig(panel_mm=80, piece_mm=40, tab_circle_r_px=15, font_path="bold")


def full_puzzle_config() -> PuzzleConfig:
    """300x300mm panel with 50mm cells. Matches scratch/phase2.py defaults."""
    return PuzzleConfig(panel_mm=300, piece_mm=50, tab_circle_r_px=22, font_path="bold")


def micro_puzzle_config() -> PuzzleConfig:
    """150x150mm panel with 50mm cells (3x3 grid). Sized for cardboard
    tram/tolerance test cuts."""
    return PuzzleConfig(panel_mm=150, piece_mm=50, tab_circle_r_px=22)


def mini_puzzle_config() -> PuzzleConfig:
    """100x100mm panel with 25mm cells (4x4 grid). Tab radius scaled to
    half the full-size bulb to suit the smaller cells. Sized for a mini
    NORA test cut on a 100mm cardboard scrap."""
    return PuzzleConfig(panel_mm=100, piece_mm=25, tab_circle_r_px=11)


def banner_puzzle_config() -> PuzzleConfig:
    """150x75mm landscape panel, 6x2 grid (25mm wide x 37.5mm tall cells),
    wavy internal edges and 3mm rounded outer corners. The 2-row layout
    puts the panel's horizontal center line ON a grid boundary, so the
    letters (snapped to that line by default) carve each row symmetrically
    into large tabbable chunks instead of mid-row slivers. Sized for a
    name-plate style NORA cut on a small cardboard scrap."""
    return PuzzleConfig(
        panel_mm=150,
        panel_h_mm=75,
        piece_mm=25,
        piece_h_mm=37.5,
        tab_circle_r_px=11,
        # Fat capsule tabs by default: 5mm neck (~1.7x a 3mm stock, so pieces
        # don't snap at the stem) rising into a ~9.4mm-wide stadium bulb (elong
        # 25px + 2*R) that keeps ~2.2mm of undercut lock each side. Needs a
        # real-size banner (see cut cmd panel overrides); the 150mm calibration
        # default is too short for them and will drop tabs.
        tab_stem_w_px=25,
        tab_bulb_elong_px=25,
        wave_amplitude_px=0,
        corner_radius_mm=5.0,
        letter_aligned_grid=True,
        fit_to_text=True,
    )


# ---------------------------------------------------------------------------
# Wavy segment helper (used when cfg.wave_amplitude_px > 0)
# ---------------------------------------------------------------------------


def wavy_points(
    p1: tuple[float, float],
    p2: tuple[float, float],
    amplitude_px: float,
    n_steps: int = 12,
) -> list[tuple[float, float]]:
    """Subdivide segment p1→p2 into n_steps+1 points with a single
    half-sine perpendicular displacement. Endpoints have zero displacement
    so the wave stitches cleanly to whatever the caller's path is doing.
    Returns the list INCLUDING both endpoints — caller drops p1 if it
    already exists in their accumulator.

    The displaced curve is computed in a *canonical* endpoint order (the
    two endpoints sorted), then returned in the caller's requested
    direction. This makes the wave traversal-invariant: the shared edge
    between two cells is the SAME world-space curve no matter which cell
    draws it (one cell goes top→bottom, the other bottom→top). Without
    this, each neighbour bows its copy of the edge the opposite way,
    cutting two arcs around a hollow sliver instead of one shared wave."""
    length = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    if length < 1e-6 or n_steps < 2 or amplitude_px <= 0:
        return [p1, p2]
    # Canonical order: smaller (x, y) endpoint first, so both neighbours
    # agree on direction (and therefore on the perpendicular sign).
    reverse = (p1[0], p1[1]) > (p2[0], p2[1])
    a, b = (p2, p1) if reverse else (p1, p2)
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    nx, ny = -dy / length, dx / length  # left perpendicular to canonical dir
    out = []
    for i in range(n_steps + 1):
        t = i / n_steps
        wave = amplitude_px * math.sin(t * math.pi)
        out.append((a[0] + dx * t + nx * wave, a[1] + dy * t + ny * wave))
    if reverse:
        out.reverse()
    return out


# ---------------------------------------------------------------------------
# Lollipop tab outline (normalized coords; scaled by tab_len_px / tab_height_px)
# ---------------------------------------------------------------------------


def tab_outline(
    direction: int, cfg: PuzzleConfig, n: int = 24, tab_len: float | None = None
) -> list[tuple[float, float]]:
    """Lollipop tab: short stem (width = R, length ~ R) rising from the
    edge into a circle of radius R. Returns (u, v) with u in [0, 1] across
    tab_len_px and v in [0, 1] across tab_height_px (= 3R). direction is +1
    for an outward bulb, -1 for an inward cavity. tab_len overrides
    cfg.tab_len_px for a shorter tab on a short edge (bulb stays radius R;
    only the flat lead-in/out shrink)."""
    R = cfg.tab_circle_r_px
    H = cfg.tab_height_px  # 3 * R
    L = cfg.tab_len_px if tab_len is None else tab_len
    # Fat capsule tab (opt-in): wide neck + stadium bulb (two R-circles E apart).
    if cfg.tab_stem_w_px is not None or cfg.tab_bulb_elong_px > 0:
        return _capsule_tab_outline(cfg, L, R, H, direction, n)
    v_tangent_px = 2 * R - R * math.sqrt(3) / 2
    v_tangent = v_tangent_px / H
    stem_half_u = (R / 2) / L
    u_stem_left = 0.5 - stem_half_u
    u_stem_right = 0.5 + stem_half_u

    pts = [(0.0, 0.0), (u_stem_left, 0.0)]
    pts.append((u_stem_left, v_tangent * direction))

    cx_norm = 0.5
    cy_norm = (2 * R) / H
    r_u = R / L
    r_v = R / H
    theta_start = 4 * math.pi / 3
    theta_sweep = -5 * math.pi / 3
    for i in range(1, n + 1):
        t = i / n
        theta = theta_start + t * theta_sweep
        u = cx_norm + r_u * math.cos(theta)
        v = (cy_norm + r_v * math.sin(theta)) * direction
        pts.append((u, v))

    pts.append((u_stem_right, 0.0))
    pts.append((1.0, 0.0))
    return pts


def _capsule_tab_outline(cfg, L, R, H, direction, n) -> list[tuple[float, float]]:
    """Fat tab = a neck of width W (cfg.tab_stem_w_px) capped by a FULL semicircle
    of radius R at each end — i.e. the rectangle width equals the stem width, so
    the two bulb circles sit exactly on the neck walls (E == W, always a clean
    stadium). The circles (radius R) and the protrusion (3R) never change; the
    ONLY size knob is W. Bulb width = W + 2R, overhang = R on each side. Returned
    as (u, v) with u over L and v over H, like tab_outline."""
    W = cfg.tab_stem_w_px if cfg.tab_stem_w_px is not None else float(R)
    uc = L / 2.0
    vc = 2.0 * R  # bulb center; semicircles span v in [R, 3R] -> protrusion 3R
    nl, nr = uc - W / 2.0, uc + W / 2.0  # neck walls == circle centers

    def semi(cx, a0, a1):  # sample a semicircle (skip first pt; caller is there)
        return [
            (
                cx + R * math.cos(a0 + (a1 - a0) * (i / n)),
                vc + R * math.sin(a0 + (a1 - a0) * (i / n)),
            )
            for i in range(1, n + 1)
        ]

    px = [(0.0, 0.0), (nl, 0.0), (nl, R)]  # lead-in, up left neck to bulb bottom
    px += semi(nl, -math.pi / 2, -3 * math.pi / 2)  # left cap: bottom -> left -> top
    px += semi(nr, math.pi / 2, -math.pi / 2)  # top flat + right cap down to bottom
    px += [(nr, 0.0), (L, 0.0)]  # down right neck, lead-out
    return [(x / L, (y / H) * direction) for x, y in px]


def place_tab_at_offset(
    edge_start: tuple[float, float],
    edge_dir: tuple[float, float],
    edge_length: float,
    direction: int,
    offset_u: float,
    cfg: PuzzleConfig,
    tab_len: float | None = None,
) -> list[tuple[float, float]]:
    """Tab outline placed in world coords at the given offset along the edge."""
    L = cfg.tab_len_px if tab_len is None else tab_len
    out = (edge_dir[1], -edge_dir[0])
    local = tab_outline(direction, cfg, tab_len=tab_len)
    world = []
    for u, v in local:
        u_world = offset_u + u * L
        x = edge_start[0] + u_world * edge_dir[0] + v * cfg.tab_height_px * out[0]
        y = edge_start[1] + u_world * edge_dir[1] + v * cfg.tab_height_px * out[1]
        world.append((x, y))
    return world


def _tab_bulb_polygon(
    edge_start, edge_dir, edge_length, direction, offset_u, cfg, tab_len=None
) -> Polygon | None:
    pts = place_tab_at_offset(
        edge_start, edge_dir, edge_length, direction, offset_u, cfg, tab_len=tab_len
    )
    if len(pts) < 3:
        return None
    try:
        poly = Polygon(pts)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if hasattr(poly, "area") and poly.area > 5:
            return poly
    except Exception:
        return None
    return None


# Minimum material wall (mm) a tab may leave against an obstacle (letter or
# panel border) before its bulb is shrunk/flipped to back off — the hard floor
# below which a bridge is considered brittle.
_BORDER_FLOOR_MM = 4.0


def find_clear_tab_offset(
    edge_start,
    edge_dir,
    edge_length,
    letter_union,
    direction,
    cfg: PuzzleConfig,
    tab_len: float | None = None,
    border=None,
) -> tuple[float | None, bool]:
    """Walk candidate offsets (center first, then alternating left/right by
    SHIFT_STEPS x SHIFT_STEP_FRAC * tab_len_px) and choose where to place the
    tab. The tab bulb must clear the letter union by letter_clearance_px, and —
    when `border` (the panel outline) is given — it should also stay that far
    from the outside edge so no brittle sliver is left against the border.

    Returns (offset, cleared_border):
      - (o, True):  a spot clearing letters and keeping the FULL wall from the
                    border (letter_clearance_px).
      - (o, False): letters clear and the border wall is at least a bulb-radius
                    (no brittle sliver) but short of the full wall — the offset
                    that MAXIMIZES the border wall. The caller may prefer a
                    flipped direction that reaches (o, True).
      - (None, False): letters can't be cleared, or every letter-clear spot
                    leaves a sub-radius sliver against the border -> the caller
                    flips or drops (a straight edge beats a brittle sliver).
    tab_len overrides cfg.tab_len_px (shorter tab for a short edge)."""
    L = cfg.tab_len_px if tab_len is None else tab_len
    max_offset = edge_length - L
    if max_offset <= 0:
        return None, False
    center = max_offset / 2
    step = L * cfg.shift_step_frac
    clr = cfg.letter_clearance_px
    # A wall thinner than this is the brittle sliver we're avoiding; never place
    # a tab that leaves less than this against the border (drop to a straight
    # edge instead). Scales with the bulb radius but capped so a big letter
    # clearance doesn't force wholesale drops on a compact banner.
    border_floor = min(clr, _BORDER_FLOOR_MM * cfg.px_per_mm)

    candidates = [center]
    for i in range(1, cfg.shift_steps + 1):
        for sign in (-1, +1):
            o = center + sign * i * step
            if 0 <= o <= max_offset:
                candidates.append(o)

    best = None  # (border_wall, offset) among letter-clear spots short of full
    for offset_u in candidates:
        bulb = _tab_bulb_polygon(
            edge_start, edge_dir, edge_length, direction, offset_u, cfg, tab_len=L
        )
        if bulb is None:
            continue
        try:
            if letter_union is not None and bulb.distance(letter_union) < clr:
                continue
            wall = bulb.distance(border) if border is not None else float("inf")
            if wall >= clr:
                return offset_u, True  # clears letters and the full border wall
            if best is None or wall > best[0]:
                best = (wall, offset_u)
        except Exception:
            continue

    if best is not None and best[0] >= border_floor:
        return best[1], False  # best achievable wall, no brittle sliver
    return None, False


# ---------------------------------------------------------------------------
# Letter polygons via Pillow + OpenCV contour tracing
# ---------------------------------------------------------------------------


_FONT_ALIASES = {
    "bold": "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "black": "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "impact": "/System/Library/Fonts/Supplemental/Impact.ttf",
    "narrow": "/System/Library/Fonts/Supplemental/Arial Narrow Bold.ttf",
}


def find_font(size: int, path: str | None = None) -> ImageFont.ImageFont:
    """Load a heavy sans face at `size`. `path` (an absolute path or an alias in
    _FONT_ALIASES, e.g. 'bold' for the lighter Arial Bold) is tried first. With
    no path the default resolves to the BLACK weight (Arial Black) — it cuts
    much cleaner in wood than Bold — falling back to Bold if Black is missing."""
    candidates = []
    if path is not None:
        candidates.append(_FONT_ALIASES.get(path, path))
    candidates += [
        # Default weight: BLACK first (chunkier strokes cut cleaner in wood),
        # then Bold fallbacks for platforms/installs without a black face.
        "/System/Library/Fonts/Supplemental/Arial Black.ttf",
        "C:\\Windows\\Fonts\\ariblk.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf",
    ]
    for p in candidates:
        try:
            return ImageFont.truetype(p, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def render_letter_polygons(word: str, cfg: PuzzleConfig):
    """Rasterize the word centered in the panel, then trace contours via
    cv2.findContours RETR_CCOMP (handles letter counters like O's hole).
    Returns (letter_union_or_None, text_x, text_y, font)."""
    img_w, img_h = cfg.canvas_w_px, cfg.canvas_h_px
    px, py = cfg.margin_px, cfg.margin_px
    puzzle_w, puzzle_h = cfg.puzzle_w_px, cfg.puzzle_h_px

    target_letter_h = int(puzzle_h * 0.70)
    font_size = int(target_letter_h * 1.4)
    font = find_font(font_size, cfg.font_path)

    tmp = Image.new("L", (img_w, img_h), 0)
    td = ImageDraw.Draw(tmp)
    bbox = td.textbbox((0, 0), word, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    max_text_w = int(puzzle_w * 0.88)
    if tw > max_text_w:
        scale = max_text_w / tw
        font_size = int(font_size * scale)
        font = find_font(font_size, cfg.font_path)
        bbox = ImageDraw.Draw(tmp).textbbox((0, 0), word, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]

    text_x = px + (puzzle_w - tw) // 2 - bbox[0]
    text_y = py + (puzzle_h - th) // 2 - bbox[1]

    # Optionally snap the letter band so its vertical center sits on the
    # nearest INTERIOR horizontal grid line, rather than free-centered in
    # the middle of a cell row. Carving the letters out of a row boundary
    # leaves larger, tabbable chunks instead of thin mid-row slivers.
    # The shift is applied only when it's at least 1px so even-row panels
    # (whose center already lands on a grid line) stay byte-identical.
    if cfg.snap_letters_to_grid and cfg.rows > 1:
        glyph_center_y = text_y + bbox[1] + th / 2.0
        grid_lines = [py + r * cfg.cell_h_px for r in range(1, cfg.rows)]
        nearest = min(grid_lines, key=lambda gy: abs(gy - glyph_center_y))
        shift = int(round(nearest - glyph_center_y))
        if abs(shift) >= 1:
            text_y += shift

    letter_mask = Image.new("L", (img_w, img_h), 0)
    ImageDraw.Draw(letter_mask).text((text_x, text_y), word, fill=255, font=font)
    letter_polys = _trace_mask_polygons(letter_mask)
    return letter_polys, text_x, text_y, font


def _trace_mask_polygons(mask: Image.Image):
    """OpenCV findContours with RETR_CCOMP. Each outer letter stroke
    becomes one shapely Polygon; its holes (O's counter etc.) are nested
    interior rings."""
    import cv2
    import numpy as np

    arr = np.array(mask)
    contours, hierarchy = cv2.findContours(arr, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if not contours or hierarchy is None:
        return None

    h = hierarchy[0]
    polygons = []
    for i, contour in enumerate(contours):
        if h[i][3] != -1:
            continue  # hole; handled when processing its parent
        outer = [(int(p[0][0]), int(p[0][1])) for p in contour]
        if len(outer) < 3:
            continue
        holes = []
        child_idx = h[i][2]
        while child_idx != -1:
            child = contours[child_idx]
            hole_pts = [(int(p[0][0]), int(p[0][1])) for p in child]
            if len(hole_pts) >= 3:
                holes.append(hole_pts)
            child_idx = h[child_idx][0]
        try:
            poly = Polygon(outer, holes=holes)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if hasattr(poly, "area") and poly.area > 100:
                polygons.append(poly)
        except Exception:
            continue
    if not polygons:
        return None
    return unary_union(polygons)


# ---------------------------------------------------------------------------
# Cell-grid piece generation with letter-aware tab shifting
# ---------------------------------------------------------------------------


def build_pieces_with_shifted_tabs(
    seed: int, letter_union, cfg: PuzzleConfig
) -> tuple[dict, dict]:
    """Build cell piece polygons. Tabs shift along their edge to clear
    letter outlines; if no clear slide exists, the tab flips in/out and
    retries; only if that also fails is the tab dropped (the edge becomes a
    straight cut).

    Returns (piece_polys, stats) where:
      piece_polys: {(col, row): shapely.Polygon}
      stats: {'total': N, 'centered': X, 'shifted': Y, 'flipped': F,
              'dropped': Z}
    """
    random.seed(seed)
    vertical_tabs = {
        (c, r): random.random() > 0.5
        for c in range(cfg.cols - 1)
        for r in range(cfg.rows)
    }
    horizontal_tabs = {
        (c, r): random.random() > 0.5
        for c in range(cfg.cols)
        for r in range(cfg.rows - 1)
    }

    stats = {"total": 0, "centered": 0, "shifted": 0, "flipped": 0, "dropped": 0}
    wave_amp = cfg.wave_amplitude_px
    wave_steps = cfg.wave_steps
    px, py = cfg.margin_px, cfg.margin_px
    cw, ch = cfg.cell_w_px, cfg.cell_h_px
    # Panel outline used to keep tabs a wall's-width off the outside edge — only
    # for the letter-aligned banner (the handled name-plate). Uniform grids pass
    # None so their tab placement stays byte-identical to the phase scripts.
    border = (
        box(px, py, px + cfg.puzzle_w_px, py + cfg.puzzle_h_px).exterior
        if cfg.letter_aligned_grid
        else None
    )

    def _edge_interior(c0, c1, tab_dir):
        """Build the point list strictly BETWEEN corners c0->c1 for one
        interior (cell-cell) grid edge, in canonical order. Counts stats
        once. Returns (interior_points, placed_bool). Computed a single
        time per shared edge so both adjacent cells reuse the exact same
        vertices (forward / reversed) — making the shared boundary
        bit-identical so edge dedup is clean and no tab segment is left
        uncut."""
        stats["total"] += 1
        length = math.hypot(c1[0] - c0[0], c1[1] - c0[1])
        edge_dir = ((c1[0] - c0[0]) / length, (c1[1] - c0[1]) / length)
        max_offset = length - cfg.tab_len_px
        offset = None
        used_dir = tab_dir

        if max_offset > 0:
            # Try the seeded direction; if it can't keep the tab off the outside
            # border, try the flipped direction (which usually protrudes inward,
            # away from the border). Prefer whichever clears the border; fall
            # back to a border-best-effort placement; drop only if letters can't
            # be cleared either way.
            o_seed, seed_clear = find_clear_tab_offset(
                c0, edge_dir, length, letter_union, tab_dir, cfg, border=border
            )
            if seed_clear:
                offset = o_seed
            else:
                o_flip, flip_clear = find_clear_tab_offset(
                    c0, edge_dir, length, letter_union, -tab_dir, cfg, border=border
                )
                if flip_clear:
                    offset, used_dir = o_flip, -tab_dir
                    stats["flipped"] += 1
                elif o_seed is not None:
                    offset = o_seed  # letters clear, border best-effort (centered)
                elif o_flip is not None:
                    offset, used_dir = o_flip, -tab_dir
                    stats["flipped"] += 1
        if offset is None:
            stats["dropped"] += 1
            # dropped tab: wavy connector on interior edges, else straight
            if wave_amp > 0:
                return wavy_points(c0, c1, wave_amp, wave_steps)[1:-1], False
            return [], False
        if abs(offset - max_offset / 2) < 1.0:
            stats["centered"] += 1
        else:
            stats["shifted"] += 1
        tw = place_tab_at_offset(c0, edge_dir, length, used_dir, offset, cfg)
        if wave_amp > 0:
            pts = wavy_points(c0, tw[1], wave_amp, wave_steps)[1:]
            pts += tw[2:-2]
            pts.append(tw[-2])
            pts += wavy_points(tw[-2], c1, wave_amp, wave_steps)[1:-1]
            return pts, True
        return list(tw[1:-1]), True

    # Precompute every interior edge ONCE in canonical orientation.
    #   vertical edge v[(c,r)]   : between col c and c+1, top->bottom
    #   horizontal edge h[(c,r)] : between row r and r+1, left->right
    v_int: dict = {}
    for c in range(cfg.cols - 1):
        for r in range(cfg.rows):
            x = px + (c + 1) * cw
            c0 = (x, py + r * ch)
            c1 = (x, py + (r + 1) * ch)
            tab_dir = +1 if vertical_tabs[(c, r)] else -1
            v_int[(c, r)] = _edge_interior(c0, c1, tab_dir)[0]
    h_int: dict = {}
    for c in range(cfg.cols):
        for r in range(cfg.rows - 1):
            y = py + (r + 1) * ch
            c0 = (px + c * cw, y)
            c1 = (px + (c + 1) * cw, y)
            tab_dir = -1 if horizontal_tabs[(c, r)] else +1
            h_int[(c, r)] = _edge_interior(c0, c1, tab_dir)[0]

    def piece_polygon(col, row):
        x0 = px + col * cw
        y0 = py + row * ch
        x1 = x0 + cw
        y1 = y0 + ch
        TL, TR, BR, BL = (x0, y0), (x1, y0), (x1, y1), (x0, y1)
        pts = [TL]
        # top edge TL->TR : horizontal edge h(col,row-1), canonical forward
        if row > 0:
            pts += h_int[(col, row - 1)]
        pts.append(TR)
        # right edge TR->BR : vertical edge v(col,row), canonical forward
        if col < cfg.cols - 1:
            pts += v_int[(col, row)]
        pts.append(BR)
        # bottom edge BR->BL : horizontal edge h(col,row), canonical reversed
        if row < cfg.rows - 1:
            pts += reversed(h_int[(col, row)])
        pts.append(BL)
        # left edge BL->TL : vertical edge v(col-1,row), canonical reversed
        if col > 0:
            pts += reversed(v_int[(col - 1, row)])
        # TL closes implicitly
        return pts

    pieces: dict = {}
    for c in range(cfg.cols):
        for r in range(cfg.rows):
            pts = piece_polygon(c, r)
            try:
                poly = Polygon(pts)
                if not poly.is_valid:
                    poly = poly.buffer(0)
                pieces[(c, r)] = poly
            except Exception:
                continue
    return pieces, stats


# ---------------------------------------------------------------------------
# Pocket carving + sliver merging
# ---------------------------------------------------------------------------


def carve_letter_pockets(piece_polys: dict, letter_union) -> list[dict]:
    """Subtract the letter union from each cell. Returns cell fragments
    as a list of {'parent': (c, r), 'polygon': Polygon, 'kind': 'cell'}.

    Keeps even tiny fragments (down to ~10px^2) so the little triangle
    tips pinched between a letter stroke and a grid line are NOT silently
    dropped (which would leave uncut gaps / orphan bits). They are cleaned
    up downstream: merged into a neighbour cell, or absorbed into the
    adjacent letter by absorb_letter_slivers()."""
    fragments = []
    for (c, r), piece in sorted(piece_polys.items()):
        remaining = piece if letter_union is None else piece.difference(letter_union)
        if remaining.is_empty:
            continue
        if isinstance(remaining, (MultiPolygon, GeometryCollection)):
            for geom in remaining.geoms:
                if isinstance(geom, Polygon) and geom.area > 10:
                    fragments.append(
                        {"parent": (c, r), "polygon": geom, "kind": "cell"}
                    )
        elif isinstance(remaining, Polygon) and remaining.area > 10:
            fragments.append({"parent": (c, r), "polygon": remaining, "kind": "cell"})
    return fragments


def _adjacency_length(poly_a, poly_b, eps: float = 0.5) -> float:
    try:
        inter = poly_a.buffer(eps).intersection(poly_b)
        if inter.is_empty:
            return 0.0
        return inter.area / (2 * eps)
    except Exception:
        return 0.0


def _too_thin_or_small(poly, cfg: PuzzleConfig) -> bool:
    if poly.area < cfg.fragment_min_area_px:
        return True
    try:
        eroded = poly.buffer(-cfg.fragment_min_thickness_px / 2)
        if eroded.is_empty or eroded.area < 1.0:
            return True
    except Exception:
        pass
    return False


def merge_small_fragments(
    pieces: list[dict], cfg: PuzzleConfig, min_shared: float = 5.0
) -> list[dict]:
    """Iteratively absorb any fragment thinner than min-thickness or smaller
    than min-area into its largest adjacent neighbor sharing at least
    min_shared pixels of boundary. Isolated small fragments (e.g. letter
    counters before letter insertion) are left alone."""
    pieces = list(pieces)
    skip: set[int] = set()
    while True:
        small_idx = None
        for i, p in enumerate(pieces):
            if i in skip:
                continue
            if _too_thin_or_small(p["polygon"], cfg):
                small_idx = i
                break
        if small_idx is None:
            return pieces
        best_idx = None
        best_area = -1.0
        for j, q in enumerate(pieces):
            if j == small_idx:
                continue
            shared = _adjacency_length(pieces[small_idx]["polygon"], q["polygon"])
            if shared >= min_shared and q["polygon"].area > best_area:
                best_area = q["polygon"].area
                best_idx = j
        if best_idx is None:
            skip.add(small_idx)
            continue
        merged = unary_union(
            [pieces[best_idx]["polygon"], pieces[small_idx]["polygon"]]
        )
        if isinstance(merged, MultiPolygon):
            merged = max(merged.geoms, key=lambda g: g.area)
        elif isinstance(merged, GeometryCollection):
            polys = [g for g in merged.geoms if isinstance(g, Polygon)]
            if not polys:
                skip.add(small_idx)
                continue
            merged = max(polys, key=lambda g: g.area)
        pieces[best_idx]["polygon"] = merged
        new_skip = set()
        for s in skip:
            if s < small_idx:
                new_skip.add(s)
            elif s > small_idx:
                new_skip.add(s - 1)
        skip = new_skip
        pieces.pop(small_idx)


# ---------------------------------------------------------------------------
# Top-level: full pipeline
# ---------------------------------------------------------------------------


def fuse_counter_fragments(
    pieces: list[dict], letter_union, cfg: PuzzleConfig
) -> list[dict]:
    """Fuse cell fragments that fall inside the SAME letter counter (a
    glyph interior hole, e.g. the center of an O) into one piece.

    Without this, a counter that straddles a cell-grid line gets carved
    into two half-discs that won't seat cleanly in the letter's pocket.
    We only merge fragments sharing a hole; fragments in different holes,
    or normal cells, are untouched. Counters bordering only the letter
    (R/A inner pockets) are single fragments already and pass through
    unchanged."""
    if letter_union is None:
        return pieces
    glyphs = (
        list(letter_union.geoms)
        if isinstance(letter_union, MultiPolygon)
        else [letter_union]
    )
    holes = [Polygon(r) for g in glyphs for r in g.interiors]
    if not holes:
        return pieces

    result = list(pieces)
    for hole in holes:
        members = [
            i
            for i, p in enumerate(result)
            if p.get("kind") == "cell"
            and p["polygon"].intersection(hole).area > 0.2 * p["polygon"].area
        ]
        if len(members) < 2:
            continue  # 0 or 1 fragment in this hole — nothing to fuse
        merged = unary_union([result[i]["polygon"] for i in members])
        if isinstance(merged, (MultiPolygon, GeometryCollection)):
            polys = [g for g in merged.geoms if isinstance(g, Polygon)]
            if not polys:
                continue
            merged = max(polys, key=lambda g: g.area)
        keep = members[0]
        result[keep] = {**result[keep], "polygon": merged}
        for i in sorted(members[1:], reverse=True):
            result.pop(i)
    return result


def _rounded_panel_mask(cfg: PuzzleConfig):
    """Return a shapely polygon of the panel rectangle (pixel space) with
    its four outer corners rounded by cfg.corner_radius_mm. Returns None
    when no rounding is requested."""
    if cfg.corner_radius_mm <= 0:
        return None
    x0 = cfg.margin_px
    y0 = cfg.margin_px
    x1 = cfg.margin_px + cfg.puzzle_w_px
    y1 = cfg.margin_px + cfg.puzzle_h_px
    rect = box(x0, y0, x1, y1)
    r_px = cfg.corner_radius_mm * cfg.px_per_mm
    # Erode then dilate (positive buffer) with round joins => rounded corners
    # on the outside, while leaving interior cell edges (which are far from
    # the panel border) untouched once we intersect each cell with this mask.
    rounded = rect.buffer(-r_px, join_style=1).buffer(r_px, join_style=1)
    return rounded


def round_panel_corners(pieces: list[dict], cfg: PuzzleConfig) -> list[dict]:
    """Clip cell pieces to a rounded-rectangle panel mask so the four
    OUTER panel corners are rounded by cfg.corner_radius_mm. Interior
    cells are unaffected (they lie wholly inside the mask). Tabs that bulge
    past the panel border are preserved because the mask only rounds the
    rectangle's corners, not its straight edges (tabs sit mid-edge)."""
    mask = _rounded_panel_mask(cfg)
    if mask is None:
        return pieces
    # Union the mask with a dilation along straight edges so tab bulges that
    # legitimately stick out past the panel rectangle aren't clipped — we
    # only want to remove the sharp corner triangles. Achieve this by
    # intersecting each piece with (mask ∪ piece-minus-corner-regions).
    # Simpler + robust: only clip the 4 corner squares.
    r_px = cfg.corner_radius_mm * cfg.px_per_mm
    x0, y0 = cfg.margin_px, cfg.margin_px
    x1 = cfg.margin_px + cfg.puzzle_w_px
    y1 = cfg.margin_px + cfg.puzzle_h_px
    corner_boxes = [
        box(x0, y0, x0 + r_px, y0 + r_px),
        box(x1 - r_px, y0, x1, y0 + r_px),
        box(x0, y1 - r_px, x0 + r_px, y1),
        box(x1 - r_px, y1 - r_px, x1, y1),
    ]
    corner_region = unary_union(corner_boxes)
    # The rounded fillet to KEEP within each corner box = mask ∩ corner box.
    keep_in_corner = mask.intersection(corner_region)
    out = []
    for p in pieces:
        poly = p["polygon"]
        if p.get("kind") != "cell" or not poly.intersects(corner_region):
            out.append(p)
            continue
        # piece outside the corner boxes (unchanged) + rounded bit inside
        outside = poly.difference(corner_region)
        inside = poly.intersection(keep_in_corner)
        new = unary_union([outside, inside])
        if isinstance(new, (MultiPolygon, GeometryCollection)):
            polys = [g for g in new.geoms if isinstance(g, Polygon)]
            new = max(polys, key=lambda g: g.area) if polys else poly
        out.append({**p, "polygon": new})
    return out


def absorb_letter_slivers(pieces: list[dict], letter_union, cfg: PuzzleConfig):
    """Absorb tiny cell fragments that are pinched against a letter into
    that letter piece.

    Where a letter stroke crosses a cell near a grid line it can leave a
    small triangle tip (e.g. inside the N's diagonal crook). Such a tip is
    cut off from the rest of its cell by the letter, so it can't merge into
    a neighbouring cell (merge_small_fragments) — it would otherwise be a
    tabless orphan. If it's small, not a letter counter, and borders a
    letter, fold it into that letter piece (fills the notch; the letter
    grows by a sliver). Counters (fragments inside a glyph hole) are left
    alone — they are intentional drop-in pieces."""
    if letter_union is None:
        return pieces
    glyphs = (
        list(letter_union.geoms)
        if isinstance(letter_union, MultiPolygon)
        else [letter_union]
    )
    holes = [Polygon(r) for g in glyphs for r in g.interiors]
    hole_union = unary_union(holes) if holes else None
    letters = [p for p in pieces if p.get("kind") == "letter"]
    if not letters:
        return pieces

    kept = []
    for p in pieces:
        if p.get("kind") != "cell":
            kept.append(p)
            continue
        poly = p["polygon"]
        if poly.area >= cfg.fragment_min_area_px:
            kept.append(p)
            continue  # a real piece, not a sliver
        is_counter = (
            hole_union is not None
            and poly.intersection(hole_union).area > 0.4 * poly.area
        )
        if is_counter:
            kept.append(p)
            continue  # drop-in counter — keep as its own piece
        # find the letter it shares the most boundary with
        best = None
        best_sh = 0.0
        for lp in letters:
            sh = _adjacency_length(poly, lp["polygon"])
            if sh > best_sh:
                best_sh = sh
                best = lp
        if best is not None and best_sh > 1.0:
            best["polygon"] = unary_union([best["polygon"], poly])
        else:
            kept.append(p)  # not letter-adjacent after all — leave it
    return kept


def generate_pieces(word: str, seed: int, cfg: PuzzleConfig) -> tuple[list[dict], dict]:
    """Full pipeline: render letter polygons, build cell pieces with
    shifted tabs, carve letter pockets, merge slivers, fuse split letter
    counters, round outer corners, append letters, absorb letter-pinched
    slivers into their letter.

    Returns (pieces, stats). pieces is a list of dicts each with
    'parent', 'polygon' (shapely), 'kind' ('cell' or 'letter'), 'serial'
    (1-indexed). stats is the tab-shifting stats dict.
    """
    word = word.upper()
    if cfg.vertex_grid:
        if cfg.fit_to_text:
            cfg = _fit_panel_to_text(word, cfg)
        letter_union, _boxes, origins = letter_layout_spaced(word, cfg)
        piece_polys, stats = build_pieces_vertex_grid(seed, letter_union, cfg, origins)
    elif cfg.letter_aligned_grid:
        # Size the panel to the text (within bounds) so the aspect ratio flexes
        # to the name and every name reserves end-column tab room.
        if cfg.fit_to_text:
            cfg = _fit_panel_to_text(word, cfg)
        # Spread letters (consistent tracking -> a tab fits in every gap), then
        # place a vertical seam through each glyph's dominant stroke (origin).
        letter_union, _boxes, origins = letter_layout_spaced(word, cfg)
        piece_polys, stats = build_pieces_letter_aligned(
            seed, letter_union, cfg, origins
        )
    else:
        letter_union, _text_x, _text_y, _font = render_letter_polygons(word, cfg)
        piece_polys, stats = build_pieces_with_shifted_tabs(seed, letter_union, cfg)
    cell_fragments = carve_letter_pockets(piece_polys, letter_union)
    if not cfg.vertex_grid:
        # vertex-grid already absorbs slivers pre-tab; the generic merge treats a
        # tab SOCKET as "thin" and would over-merge the tabbed pieces.
        cell_fragments = merge_small_fragments(cell_fragments, cfg)
    cell_fragments = fuse_counter_fragments(cell_fragments, letter_union, cfg)
    cell_fragments = round_panel_corners(cell_fragments, cfg)

    letter_polys: list[Polygon] = []
    if letter_union is not None:
        if isinstance(letter_union, MultiPolygon):
            letter_polys = [g for g in letter_union.geoms if g.area > 100]
        elif isinstance(letter_union, Polygon):
            letter_polys = [letter_union]
    pieces = list(cell_fragments) + [
        {"parent": None, "polygon": lp, "kind": "letter"} for lp in letter_polys
    ]
    pieces = absorb_letter_slivers(pieces, letter_union, cfg)
    for i, p in enumerate(pieces, start=1):
        p["serial"] = i
    return pieces, stats


def letter_auto_origins(
    word: str, cfg: PuzzleConfig
) -> list[tuple[str, tuple[float, float]]]:
    """For each glyph in `word` as rendered by render_letter_polygons, compute
    its automatic grid origin (glyph_origins.auto_glyph_origin) and return
    [(char, (x_px, y_px)), ...] in image-pixel space (same frame as the piece
    polygons). Used to annotate previews and, in the letter-aligned grid, to
    place the vertical grid lines. Spaces / empty glyphs are skipped."""
    import numpy as np
    from glyph_origins import auto_glyph_origin

    word = word.upper()
    _union, text_x, text_y, font = render_letter_polygons(word, cfg)
    out: list[tuple[str, tuple[float, float]]] = []
    for i, ch in enumerate(word):
        x_pen = text_x + font.getlength(word[:i])
        gl, gt, gr, gb = font.getbbox(ch)
        if gr <= gl or gb <= gt:  # space / empty glyph
            continue
        glyph_img = Image.new("L", (int(gr - gl), int(gb - gt)), 0)
        ImageDraw.Draw(glyph_img).text((-gl, -gt), ch, fill=255, font=font)
        ink = np.asarray(glyph_img) > 80
        nx, ny = auto_glyph_origin(ink)
        ink_l, ink_t = x_pen + gl, text_y + gt
        ink_r, ink_b = x_pen + gr, text_y + gb
        ox = ink_l + nx * (ink_r - ink_l)
        oy = ink_t + ny * (ink_b - ink_t)
        out.append((ch, (float(ox), float(oy))))
    return out


# Target column width (mm) for the letter-aligned grid's seam selection.
# Absolute (not per-letter), so short names with big letters get subdivided
# and long names with small letters share columns. Tunable.
LETTER_ALIGNED_TARGET_COL_MM = 28.0


def _end_side_margin_px(cfg: PuzzleConfig) -> float:
    """Outer margin (left of the first letter / right of the last) reserved for
    the end-column horizontal tab plus its border wall. Guarantees every name —
    even a long one like KARSON — keeps room for its end tabs instead of having
    to choose between squeezing the letters and dropping the end interlocks."""
    if cfg.banner_margin_mm is not None:
        return cfg.banner_margin_mm * cfg.px_per_mm
    return cfg.tab_len_px + 2 * cfg.tab_circle_r_px


def _measure_text(word: str, cfg: PuzzleConfig):
    """Size the font and lay out per-glyph pen positions for `word`, working
    from the panel BOUNDS (not any fitted size, so the fit pass and the real
    layout pass always agree on the font). Returns a dict with the font, the
    tracked pen offsets, per-glyph bboxes, and the text's total width / band
    height in px."""
    chars = [c for c in word if not c.isspace()]
    min_gap = (
        cfg.tab_height_px
        + 2 * cfg.tab_circle_r_px
        + cfg.letter_gap_extra_mm * cfg.px_per_mm
    )
    side_margin = _end_side_margin_px(cfg)
    if cfg.fit_to_text:
        avail = cfg.bounds_w_px - 2 * side_margin  # reserve end-tab room both sides
        row_min = cfg.tab_height_px + cfg.tab_circle_r_px
        init_band = min(cfg.bounds_h_px * 0.72, cfg.bounds_h_px - 2 * row_min)
        if cfg.banner_letter_h_mm is not None:
            # Fixed cap height: keep letters a consistent size; only shrink below
            # this if the word is too wide even at the full width bound.
            init_band = cfg.banner_letter_h_mm * cfg.px_per_mm
    else:
        avail = cfg.bounds_w_px * 0.92
        init_band = cfg.bounds_h_px * 0.72
    if not chars:
        return None

    font_size = max(10, int(init_band * 1.4))
    font = find_font(font_size, cfg.font_path)
    pen2 = bbox = None
    for _ in range(6):
        font = find_font(font_size, cfg.font_path)
        adv = [font.getlength(c) for c in chars]
        bbox = [font.getbbox(c) for c in chars]
        pen = [0.0]
        for a in adv[:-1]:
            pen.append(pen[-1] + a)
        ink_l = [pen[i] + bbox[i][0] for i in range(len(chars))]
        ink_r = [pen[i] + bbox[i][2] for i in range(len(chars))]
        gaps = [ink_l[i + 1] - ink_r[i] for i in range(len(chars) - 1)]
        delta = max(0.0, min_gap - min(gaps)) if gaps else 0.0
        pen2 = [pen[i] + i * delta for i in range(len(chars))]
        total = (pen2[-1] + bbox[-1][2]) - (pen2[0] + bbox[0][0])
        if total <= avail or font_size <= 10:
            break
        font_size = max(10, int(font_size * (avail / total)))

    tops = [bbox[i][1] for i in range(len(chars))]
    bots = [bbox[i][3] for i in range(len(chars))]
    return {
        "chars": chars,
        "font": font,
        "pen2": pen2,
        "bbox": bbox,
        "total": total,
        "band_h": max(bots) - min(tops),
        "tops": tops,
        "bots": bots,
    }


def _fit_panel_to_text(word: str, cfg: PuzzleConfig) -> PuzzleConfig:
    """Return a cfg copy whose panel is sized to `word` (within the panel_mm x
    panel_h_mm bounds): width = text + end margins, height = band + two rows.
    Short names get a narrower panel, long names a shorter one; the aspect ratio
    flexes to the name and the panel need not fill the bounds."""
    from dataclasses import replace

    m = _measure_text(word, cfg)
    if m is None:
        return cfg
    side_margin = _end_side_margin_px(cfg)
    row_min = cfg.tab_height_px + cfg.tab_circle_r_px
    row_h = max(row_min, int(m["band_h"] * 0.45))
    if cfg.banner_margin_mm is not None:
        # uniform margin around the letter bbox (vertex-grid): panel = bbox + 2*m
        row_h = max(row_min, int(cfg.banner_margin_mm * cfg.px_per_mm))
    # Optionally grow the top/bottom rows to reach a target panel height — taller
    # rows give border-adjacent tabs more room to keep their wall.
    elif cfg.banner_target_h_mm is not None:
        target_px = cfg.banner_target_h_mm * cfg.px_per_mm
        row_h = max(row_h, int((target_px - m["band_h"]) / 2))
    fit_w = int(min(cfg.bounds_w_px, m["total"] + 2 * side_margin))
    fit_h = int(min(cfg.bounds_h_px, m["band_h"] + 2 * row_h))
    return replace(cfg, panel_w_px_fit=fit_w, panel_h_px_fit=fit_h)


def fit_config(word: str, cfg: PuzzleConfig) -> PuzzleConfig:
    """Public wrapper: return the panel-fitted cfg for `word` when the config
    opts into fit-to-text (letter-aligned banner), else the cfg unchanged.
    Idempotent — the fit is recomputed from the immutable bounds each call, so
    callers can fit once and generate_pieces can safely fit again."""
    if (cfg.letter_aligned_grid or cfg.vertex_grid) and cfg.fit_to_text:
        return _fit_panel_to_text(word, cfg)
    return cfg


def letter_layout_spaced(word: str, cfg: PuzzleConfig):
    """Lay out `word` for the letter-aligned grid with a guaranteed tab-width
    gap between every adjacent letter, using CONSISTENT tracking relative to
    the font's natural (proportional, kerned) spacing — not fixed-width.

    Rule: place glyphs at their natural advances, measure each pair's
    ink-to-ink gap, then add ONE uniform tracking delta to every gap so the
    tightest pair clears exactly `tab_len_px` (looser pairs clear more). Shrink
    the font to fit the panel width if needed. Returns (letter_union, boxes)
    where boxes is [(char, (l, t, r, b)), ...] ink boxes in image px, left to
    right — the gap midpoints become the vertical grid lines."""
    word = word.upper()
    px, py = cfg.margin_px, cfg.margin_px
    pw, ph = cfg.puzzle_w_px, cfg.puzzle_h_px  # fitted dims when fit_to_text
    img_w, img_h = cfg.canvas_w_px, cfg.canvas_h_px
    m = _measure_text(word, cfg)
    if m is None:
        return None, [], []
    chars, font = m["chars"], m["font"]
    pen2, bbox = m["pen2"], m["bbox"]
    total, band_h = m["total"], m["band_h"]
    tops = m["tops"]

    text_y = py + (ph - band_h) / 2 - min(tops)
    world_off = px + (pw - total) / 2 - (pen2[0] + bbox[0][0])

    mask = Image.new("L", (img_w, img_h), 0)
    md = ImageDraw.Draw(mask)
    import numpy as np
    from glyph_origins import glyph_hcut_y, glyph_seam

    boxes = []
    seam_nx = []  # per-glyph normalized seam-x (through a solid stroke / center)
    through_ok = []  # False for capped-open letters (C, G) -> keep whole
    hcut_ny = []  # per-glyph normalized horizontal-cut y (feature-anchored)
    for i, c in enumerate(chars):
        pen_x = world_off + pen2[i]
        md.text((pen_x, text_y), c, fill=255, font=font)
        gl, gt, gr, gb = bbox[i]
        ink_l, ink_t, ink_r, ink_b = pen_x + gl, text_y + gt, pen_x + gr, text_y + gb
        boxes.append((c, (ink_l, ink_t, ink_r, ink_b)))
        gi = Image.new("L", (max(int(gr - gl), 1), max(int(gb - gt), 1)), 0)
        ImageDraw.Draw(gi).text((-gl, -gt), c, fill=255, font=font)
        ink = np.asarray(gi) > 80
        nx, ok = glyph_seam(ink)
        seam_nx.append(nx)
        through_ok.append(ok)
        hcut_ny.append(glyph_hcut_y(ink))
    union = _trace_mask_polygons(mask)

    # --- Vertical seam selection (general; works for arbitrary text) ---
    # DEFAULT: one seam per letter, through a SOLID part of the glyph (its center
    #   when the center has ink, else its dominant vertical stroke — see
    #   glyph_seam). This splits every glyph into two pieces, each showing a
    #   clear (non-zero) letter notch with the tab emerging from solid material
    #   — never a fragile whitespace crumb (CLEM's C, SLOAN/LEO's L). The
    #   inter-letter GAP lands mid-column, where the horizontal top/bottom tab
    #   has clear room, so long names still hold together.
    # EXCEPTION: a "capped open" letter (C, G — through_ok False) must not be
    #   split through its thin curved back, so route its seam to the adjacent GAP
    #   and let it stay a single whole piece.
    # ADD a between-letter GAP seam only to break up a column wider than max_col
    #   (a big letter on a short name). MERGE (drop a seam) if a column would be
    #   skinnier than min_col. No per-name carveouts; piece count is emergent.
    min_col = cfg.tab_height_px + 2 * cfg.tab_circle_r_px
    target = max(min_col * 1.3, LETTER_ALIGNED_TARGET_COL_MM * cfg.px_per_mm)
    max_col = 1.9 * target
    left_edge, right_edge = float(px), float(px + pw)

    # seam-x through each glyph at its solid-stroke anchor (not the bbox center)
    centers = [b[1][0] + seam_nx[i] * (b[1][2] - b[1][0]) for i, b in enumerate(boxes)]
    # per-glyph horizontal-cut y (feature-anchored) and ink center-x for mapping
    hcut_y = [b[1][1] + hcut_ny[i] * (b[1][3] - b[1][1]) for i, b in enumerate(boxes)]
    mid_x = [(b[1][0] + b[1][2]) / 2.0 for b in boxes]
    gaps = [(boxes[i][1][2] + boxes[i + 1][1][0]) / 2.0 for i in range(len(boxes) - 1)]
    # keep-whole letters route their seam to a bounding gap instead of through
    # (no left/right slice through the hollow). For a capped-open letter (C, G)
    # the column's row boundary cuts just INSIDE the ink near one arm, so the
    # letter still spans two rows (no letter ever sits entirely inside one
    # piece), the counter stays mostly whole on the big side, and the left
    # semicircle globs onto that 'rest' piece. Alternate the arm per occurrence
    # so the boundary undulates. glob_y_at maps that letter's seam-x -> the y.
    seam_xs = []
    glob_y_at = {}
    open_n = 0
    for i in range(len(boxes)):
        if through_ok[i]:
            seam_xs.append(centers[i])
            continue
        sx = gaps[i] if i < len(gaps) else gaps[i - 1]  # bounding gap seam
        _, ink_t, _, ink_b = boxes[i][1]
        h = ink_b - ink_t
        # ~quarter in from an arm: small arm piece + big rest (counter + back)
        gy = (ink_t + 0.25 * h) if (open_n % 2 == 0) else (ink_b - 0.25 * h)
        gy = min(
            max(gy, float(py) + cfg.tab_height_px), float(py + ph) - cfg.tab_height_px
        )
        glob_y_at[sx] = gy
        seam_xs.append(sx)
        open_n += 1
    seam_xs = sorted(set(seam_xs))

    # One vertical seam per letter (through its stroke), never an extra seam in
    # the empty GAP between letters — a between-letter cut looks like a phantom
    # edge. Wide columns on a short name are handled by narrowing the whole
    # banner (smaller --panel-mm), not by inserting gap seams.

    # enforce min column width (merge: drop seams too close to the previous/edge).
    # Each seam's y is the capped-open glob-y when it bounds such a letter, else
    # the HORIZONTAL-cut anchor of the nearest glyph — so the row boundary
    # undulates through each letter's feature (crossbar / mouth) and dives
    # around capped-open letters instead of slicing their hollow. A capped-open
    # glob seam has PRIORITY: if it collides with a neighbour's seam, drop the
    # neighbour's (that letter just stays whole) rather than lose the glob.
    def _y_at(x):
        if x in glob_y_at:
            return glob_y_at[x]
        li = min(range(len(boxes)), key=lambda i: abs(mid_x[i] - x))
        return hcut_y[li]

    kept = []  # list of x, closest-first
    for x in sorted(seam_xs):
        if kept and x - kept[-1] < min_col:
            # collision: keep the glob seam, drop the plain one
            if x in glob_y_at and kept[-1] not in glob_y_at:
                kept[-1] = x
            continue
        kept.append(x)
    origins = [
        ("|", (x, _y_at(x)))
        for x in kept
        if x - left_edge >= min_col and right_edge - x >= min_col
    ]
    return union, boxes, origins


def _straight_edge_with_tab(
    c0, c1, tab_dir, letter_union, cfg, stats, avoid=None, full_scan=False
):
    """Interior points (excluding endpoints) for a straight edge c0->c1 with a
    tab placed by this policy (banner needs every tab, so it never drops):

      1. Keep the FULL wall (letter_clearance_px) everywhere around the whole tab
         — from letters AND the panel border — aiming for the edge centerpoint.
         Prefer the largest tab and the seeded direction.
      2. If the full wall can't be met, CENTER the tab in the available space
         (the offset that maximizes the min wall), trying the FLIPPED direction
         first — keep the largest tab whose best wall is at least a sliver floor.
      3. As a LAST resort, shrink the tab (down to a single circle) to fit.

    `avoid` (a letter-counter/hole geometry) rejects any offset whose bulb sits
    substantially inside it — a tab in a counter gives no real interlock.
    `full_scan` scans the whole edge (end columns, whose only clear span is the
    outer margin) rather than a center-out window. Used by the letter-aligned
    builder for its (possibly sloped) node edges."""
    from dataclasses import replace

    length = math.hypot(c1[0] - c0[0], c1[1] - c0[1])
    if length < 1e-6:
        return []
    stats["total"] += 1
    edge_dir = ((c1[0] - c0[0]) / length, (c1[1] - c0[1]) / length)
    clr = cfg.letter_clearance_px
    floor = min(clr, _BORDER_FLOOR_MM * cfg.px_per_mm)
    R = cfg.tab_circle_r_px
    border = box(
        cfg.margin_px,
        cfg.margin_px,
        cfg.margin_px + cfg.puzzle_w_px,
        cfg.margin_px + cfg.puzzle_h_px,
    ).exterior
    obstacles = unary_union([g for g in (letter_union, border) if g is not None])

    # Tab size ladder, WIDEST bulb -> narrowest.
    full = min(cfg.tab_len_px, 0.7 * length)
    ladder = []
    if cfg.tab_stem_w_px is not None:
        # Fat capsule. Circles (radius R) and protrusion (3R) never change; the
        # ONLY knob is the stem width W (== rectangle width). To fit a crowded
        # tab we step W down from its configured value toward a 3.5mm floor —
        # the bulb (W + 2R) narrows with it while keeping a full-semicircle
        # overhang (R) on each side. Never below 3.5mm.
        w0 = cfg.tab_stem_w_px
        w_min = 3.5 * cfg.px_per_mm
        widths = sorted(
            {w0, (w0 + w_min) / 2, w_min} | {w for w in (w0 * 0.85,) if w >= w_min},
            reverse=True,
        )
        for w in widths:
            if w < w_min - 1e-6:
                continue
            vcfg = replace(cfg, tab_stem_w_px=w, tab_bulb_elong_px=w)  # E == W
            bulb_w = w + 2 * R  # tab length must contain the bulb
            for tl in (full, 0.7 * full):
                if bulb_w <= tl < length:
                    ladder.append((vcfg, tl))
    else:
        # Thin lollipop (default/calibration): shorten the tab, then a plain
        # circle, to fit a tight clear span.
        for tl in (full, 0.8 * full, 0.6 * full, 2.4 * R):
            if 2.4 * R <= tl < length and (not ladder or ladder[-1][1] != tl):
                ladder.append((cfg, tl))
    if not ladder:
        stats["dropped"] += 1
        return []

    def _in_avoid(poly):
        if avoid is None or not poly.intersects(avoid):
            return False
        try:
            return poly.intersection(avoid).area > 0.2 * poly.area
        except Exception:
            return False

    def _scan(vcfg, tl, d):
        """(full_off, best_off, best_wall): full_off = offset closest to edge
        center whose whole tab keeps the full wall; best_off/best_wall = offset
        that maximizes the min wall (centering the tab in the available space)."""
        mo = length - tl
        if mo <= 0:
            return None, None, -1.0
        center = mo / 2.0
        if full_scan:  # scan the whole edge (end column's span may be off-center)
            step = max(2.0, tl * 0.15)
            offs = [center]
            o = 0.0
            while o <= mo + 1e-6:
                offs.append(min(o, mo))
                o += step
        else:
            step = tl * cfg.shift_step_frac
            offs = [center]
            for i in range(1, cfg.shift_steps + 1):
                for s in (-1, +1):
                    o = center + s * i * step
                    if 0 <= o <= mo:
                        offs.append(o)
        full_off, best_off, best_wall = None, None, -1.0
        for o in offs:
            poly = _tab_bulb_polygon(c0, edge_dir, length, d, o, vcfg, tab_len=tl)
            if poly is None or _in_avoid(poly):
                continue
            wall = poly.distance(obstacles) if not obstacles.is_empty else 1e9
            if wall >= clr and (
                full_off is None or abs(o - center) < abs(full_off - center)
            ):
                full_off = o
            if wall > best_wall:
                best_wall, best_off = wall, o
        return full_off, best_off, best_wall

    def _emit(vcfg, tl, d, off):
        if abs(off - (length - tl) / 2) < 1.0:
            stats["centered"] += 1
        else:
            stats["shifted"] += 1
        if d != tab_dir:
            stats["flipped"] += 1
        tw = place_tab_at_offset(c0, edge_dir, length, d, off, vcfg, tab_len=tl)
        return list(tw[1:-1])

    # 1) Full wall everywhere: largest tab, centre-most offset, seed dir first.
    for vcfg, tl in ladder:
        for d in (tab_dir, -tab_dir):
            full_off, _, _ = _scan(vcfg, tl, d)
            if full_off is not None:
                return _emit(vcfg, tl, d, full_off)
    # 2) Best effort: largest tab whose centred wall clears the sliver floor,
    #    flipping first (pick the direction with the larger wall).
    for vcfg, tl in ladder:
        pick = None
        for d in (tab_dir, -tab_dir):
            _, best_off, best_wall = _scan(vcfg, tl, d)
            if best_off is not None and (pick is None or best_wall > pick[1]):
                pick = (best_off, best_wall, d)
        if pick is not None and pick[1] >= floor:
            return _emit(vcfg, tl, pick[2], pick[0])
    # 3) Never drop: place the smallest tab at its best (most central) spot.
    for vcfg, tl in reversed(ladder):
        pick = None
        for d in (tab_dir, -tab_dir):
            _, best_off, best_wall = _scan(vcfg, tl, d)
            if best_off is not None and (pick is None or best_wall > pick[1]):
                pick = (best_off, best_wall, d)
        if pick is not None:
            return _emit(vcfg, tl, pick[2], pick[0])
    stats["dropped"] += 1
    return []


def _clear_tab_offsets_full(
    c0, edge_dir, length, letter_union, hole_union, direction, cfg, tab_len
):
    """Every offset along the WHOLE edge (fine scan, not center-out) where a tab
    of `tab_len` clears the letter union by `letter_clearance_px` AND whose bulb
    does not sit inside a letter counter (hole). A tab placed in a counter is
    useless: the counter is carved out and fused into its own drop-in piece, so
    the two end-column pieces would still meet on a straight line. Returns the
    list of clear offsets (may be empty)."""
    max_offset = length - tab_len
    if max_offset <= 0:
        return []
    step = max(2.0, tab_len * 0.15)
    out = []
    o = 0.0
    while o <= max_offset + 1e-6:
        bulb = _tab_bulb_polygon(
            c0, edge_dir, length, direction, min(o, max_offset), cfg, tab_len=tab_len
        )
        if bulb is not None:
            ok = (
                letter_union is None
                or bulb.distance(letter_union) >= cfg.letter_clearance_px
            )
            if ok and hole_union is not None and bulb.intersects(hole_union):
                try:
                    if bulb.intersection(hole_union).area > 0.2 * bulb.area:
                        ok = False  # bulb lands in a counter -> no real interlock
                except Exception:
                    pass
            if ok:
                out.append(min(o, max_offset))
        o += step
    return out


def _end_column_h_edge(
    c0, c1, tab_dir, letter_union, hole_union, cfg, stats, outer_at_c1
):
    """Horizontal boundary of an END column (the one against a panel border).

    Unlike the interior edges (`_straight_edge_with_tab`, a center-out search
    over a bounded window), this scans the full edge and REJECTS tabs whose bulb
    falls inside a letter counter, then applies the same wall-keeping, centre-
    seeking, never-drop policy as the interior edges — so the end tab sits in the
    middle of its clear outer margin (a wall off the border, not jammed against
    it), while still guaranteeing the top/bottom end pieces interlock through
    that shared margin. `outer_at_c1` is unused now (kept for call compatibility)."""
    return _straight_edge_with_tab(
        c0, c1, tab_dir, letter_union, cfg, stats, avoid=hole_union, full_scan=True
    )


def build_pieces_letter_aligned(seed, letter_union, cfg, origins):
    """Letter-aligned 2-row grid: vertical grid lines pass THROUGH each glyph
    at its origin-x; the middle (r=1) row boundary is a polyline that bends to
    each glyph's origin-y (letters are not moved). origins is the list from
    letter_auto_origins (left-to-right). Returns (piece_polys{(c,r):Polygon},
    stats). Capped-open letters (C, G) are handled in letter_layout_spaced by
    routing the row boundary just outside their ink (via origins' y), so the
    whole letter globs onto one row — no special-casing needed here."""
    random.seed(seed)
    px, py = cfg.margin_px, cfg.margin_px
    pw, ph = cfg.puzzle_w_px, cfg.puzzle_h_px
    inset = max(cfg.tab_circle_r_px, 4)

    # Vertical line xs: panel-left, each origin-x (monotonic, clamped), panel-right.
    xs, oy = [], []
    for _ch, (ox, o_y) in origins:
        cx = min(max(ox, px + inset), px + pw - inset)
        if xs and cx <= xs[-1] + inset:
            cx = xs[-1] + inset  # keep strictly increasing with room for a tab
        if cx >= px + pw - inset:
            continue  # ran out of room; skip extra letters' lines
        xs.append(cx)
        oy.append(o_y)
    lines_x = [px] + xs + [px + pw]
    # r=1 node y at each vertical line: interior line i -> its origin-y; the two
    # panel-edge lines copy the nearest origin-y.
    if oy:
        node_y1 = [oy[0]] + oy + [oy[-1]]
    else:
        node_y1 = [py + ph / 2] * len(lines_x)
    ncols = len(lines_x) - 1
    ROWS = 2

    def node(c, r):
        if r == 0:
            return (lines_x[c], py)
        if r == ROWS:
            return (lines_x[c], py + ph)
        return (lines_x[c], node_y1[c])  # r == 1, the bent boundary

    stats = {"total": 0, "centered": 0, "shifted": 0, "flipped": 0, "dropped": 0}
    v_tab = {
        (c, r): random.random() > 0.5 for c in range(1, ncols) for r in range(ROWS)
    }
    h_tab = {c: random.random() > 0.5 for c in range(ncols)}

    # Interior vertical edges (lines c=1..ncols-1), split per row at the boundary.
    v_int = {}
    for c in range(1, ncols):
        for r in range(ROWS):
            tab_dir = +1 if v_tab[(c, r)] else -1
            v_int[(c, r)] = _straight_edge_with_tab(
                node(c, r), node(c, r + 1), tab_dir, letter_union, cfg, stats
            )
    # Letter counters (holes) — a horizontal tab dropped into one is useless
    # (the counter is carved out + fused into its own drop-in piece), so the
    # end-column placement steers clear of them.
    hole_union = None
    if letter_union is not None:
        glyphs = (
            list(letter_union.geoms)
            if isinstance(letter_union, MultiPolygon)
            else [letter_union]
        )
        holes = [Polygon(r) for g in glyphs for r in g.interiors]
        if holes:
            hole_union = unary_union(holes)

    # Interior horizontal edges (the r=1 boundary), one per column, possibly sloped.
    # The two END columns (against the panel borders) use a full-edge,
    # counter-avoiding placement that guarantees their top/bottom pieces
    # interlock (see _end_column_h_edge); interior columns keep the center-out
    # placement so existing layouts stay byte-identical.
    h_int = {}
    for c in range(ncols):
        tab_dir = -1 if h_tab[c] else +1
        if ncols >= 2 and c in (0, ncols - 1):
            h_int[c] = _end_column_h_edge(
                node(c, 1),
                node(c + 1, 1),
                tab_dir,
                letter_union,
                hole_union,
                cfg,
                stats,
                outer_at_c1=(c == ncols - 1),
            )
        else:
            h_int[c] = _straight_edge_with_tab(
                node(c, 1), node(c + 1, 1), tab_dir, letter_union, cfg, stats
            )

    def piece_polygon(c, r):
        TL, TR = node(c, r), node(c + 1, r)
        BR, BL = node(c + 1, r + 1), node(c, r + 1)
        pts = [TL]
        # top edge TL->TR : boundary only when r==1 (its top is h_int[c] forward)
        if r == 1:
            pts += h_int[c]
        pts.append(TR)
        # right edge TR->BR : vertical line c+1 (interior only)
        if c + 1 <= ncols - 1:
            pts += v_int[(c + 1, r)]
        pts.append(BR)
        # bottom edge BR->BL : boundary only when r==0 (its bottom is h_int[c] reversed)
        if r == 0:
            pts += reversed(h_int[c])
        pts.append(BL)
        # left edge BL->TL : vertical line c (interior only)
        if c >= 1:
            pts += reversed(v_int[(c, r)])
        return pts

    pieces = {}
    for c in range(ncols):
        for r in range(ROWS):
            try:
                poly = Polygon(piece_polygon(c, r))
                if not poly.is_valid:
                    poly = poly.buffer(0)
                if not poly.is_empty:
                    pieces[(c, r)] = poly
            except Exception:
                continue
    return pieces, stats


def _convex_vertices(glyph: Polygon, ppm: float) -> list[tuple[float, float]]:
    """Convex (outward) corners of a glyph's exterior + its 4 axis extrema,
    simplified to ~1mm so we key off real corners, not contour noise."""
    ext = glyph.exterior.simplify(max(ppm, 1.0))
    xy = [(float(x), float(y)) for x, y in list(ext.coords)[:-1]]
    n = len(xy)
    if n < 3:
        return xy
    sa = sum(
        xy[i][0] * xy[(i + 1) % n][1] - xy[(i + 1) % n][0] * xy[i][1] for i in range(n)
    )
    orient = 1.0 if sa > 0 else -1.0
    pts = []
    for i in range(n):
        p0, p1, p2 = xy[i - 1], xy[i], xy[(i + 1) % n]
        cr = (p1[0] - p0[0]) * (p2[1] - p1[1]) - (p1[1] - p0[1]) * (p2[0] - p1[0])
        if abs(cr) > 1e-6 and (cr > 0) == (orient > 0):
            pts.append(p1)
    xs = [p[0] for p in xy]
    ys = [p[1] for p in xy]
    pts += [
        xy[xs.index(min(xs))],
        xy[xs.index(max(xs))],
        xy[ys.index(min(ys))],
        xy[ys.index(max(ys))],
    ]
    return pts


def _poly_list_vg(g):
    if g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    return [p for p in getattr(g, "geoms", []) if isinstance(p, Polygon) and p.area > 1]


def _vg_anchors(
    glyph: Polygon, ppm: float, min_edge_mm: float = 20.0
) -> list[tuple[float, float]]:
    """Candidate seam anchors on a glyph's OUTER boundary: every convex corner
    (from _convex_vertices) PLUS midpoints of long straight edges. Any straight
    edge longer than min_edge_mm gets a vertex at its CENTER, then each half is
    subdivided the same way. This is symmetric about each edge's center — so the
    two long sides of an I (or the slopes of an A) get matching anchors — and it
    gives the density loop more places to T a seam off a flat edge and split a
    large piece. Curved letters (O, S) have only short edges, so they stay
    corner-driven. Midpoints coinciding with a corner are dropped.

    An anchor is REJECTED if it sits in a nook it couldn't launch out of without
    leaving a brittle bridge: within 4mm of a concave (reflex) corner, or where a
    short outward-normal probe can't stay >=4mm clear of the glyph (between the
    fingers of an E, inside the crooks of a W/R/S, etc.)."""
    corners = _convex_vertices(glyph, ppm)
    ext = glyph.exterior.simplify(max(ppm, 1.0))
    ring = [(float(x), float(y)) for x, y in list(ext.coords)[:-1]]
    thresh = min_edge_mm * ppm
    mids: list[tuple[float, float]] = []

    def _sub(a, b):
        if math.hypot(b[0] - a[0], b[1] - a[1]) > thresh:
            m = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            mids.append(m)
            _sub(a, m)
            _sub(m, b)

    n = len(ring)
    for i in range(n):
        _sub(ring[i], ring[(i + 1) % n])
    near = 4.0 * ppm
    extra = [
        m
        for m in mids
        if all(math.hypot(m[0] - c[0], m[1] - c[1]) > near for c in corners)
    ]

    # Reflex (concave) corners of the ring — anchors must stay 4mm clear of these.
    sa = sum(
        ring[i][0] * ring[(i + 1) % n][1] - ring[(i + 1) % n][0] * ring[i][1]
        for i in range(n)
    )
    orient = 1.0 if sa > 0 else -1.0
    reflex = []
    for i in range(n):
        p0, p1, p2 = ring[i - 1], ring[i], ring[(i + 1) % n]
        cr = (p1[0] - p0[0]) * (p2[1] - p1[1]) - (p1[1] - p0[1]) * (p2[0] - p1[0])
        if abs(cr) > 1e-6 and (cr > 0) != (orient > 0):
            reflex.append(p1)

    solid = Polygon(glyph.exterior)
    clear = 4.0 * ppm
    launch = 7.0 * ppm

    def _launchable(p):
        if any(math.hypot(p[0] - r[0], p[1] - r[1]) < clear for r in reflex):
            return False  # too close to a concave corner
        nx, ny = _vg_normal(glyph, p, ppm)
        probe = (p[0] + nx * launch, p[1] + ny * launch)
        if solid.distance(Point(probe)) < clear - 0.5:
            return False  # a nook: the launch corridor is pinched by the glyph
        if LineString([p, probe]).intersection(solid).length > 1.0:
            return False  # launch ray dives back into the glyph
        return True

    return [p for p in (corners + extra) if _launchable(p)]


def _vg_normal(glyph, v, ppm):
    """Local outward unit normal of the glyph outline at v (true perpendicular)."""
    ring = glyph.exterior
    dd = ring.project(Point(v))
    a = ring.interpolate(max(0.0, dd - 2 * ppm))
    b = ring.interpolate(min(ring.length, dd + 2 * ppm))
    tx, ty = b.x - a.x, b.y - a.y
    tn = math.hypot(tx, ty) or 1.0
    nx, ny = -ty / tn, tx / tn
    cen = glyph.centroid
    if nx * (v[0] - cen.x) + ny * (v[1] - cen.y) < 0:
        nx, ny = -nx, -ny
    return (nx, ny)


def _vg_bez(p0, n0, p3, n3, h, samples=40):
    """Cubic Bezier leaving p0 along n0, arriving at p3 along -n3."""
    c1 = (p0[0] + h * n0[0], p0[1] + h * n0[1])
    c2 = (p3[0] + h * n3[0], p3[1] + h * n3[1])
    out = []
    for i in range(samples + 1):
        t = i / samples
        m = 1 - t
        out.append(
            (
                m**3 * p0[0]
                + 3 * m * m * t * c1[0]
                + 3 * m * t * t * c2[0]
                + t**3 * p3[0],
                m**3 * p0[1]
                + 3 * m * m * t * c1[1]
                + 3 * m * t * t * c2[1]
                + t**3 * p3[1],
            )
        )
    return out


def _vg_min_radius_mm(pts, ppm):
    r = float("inf")
    for i in range(1, len(pts) - 1):
        a, b, c = pts[i - 1], pts[i], pts[i + 1]
        v1 = (b[0] - a[0], b[1] - a[1])
        v2 = (c[0] - b[0], c[1] - b[1])
        l1 = math.hypot(*v1)
        l2 = math.hypot(*v2)
        if l1 < 1e-6 or l2 < 1e-6:
            continue
        dot = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (l1 * l2)))
        dth = math.acos(dot)
        if dth > 1e-9:
            r = min(r, ((l1 + l2) / 2) / dth)
    return r / ppm


def _vg_curve(a, na, b, nb, obstacles, ppm, min_r_mm=5.0, clear_mm=4.0):
    """Smoothest perpendicular-launch curve a->b that bends no tighter than
    min_r_mm, crosses no obstacle, and keeps >=clear_mm from each obstacle except
    within its attachment neighborhood. obstacles = [(poly, end)] with end in
    {'a','b',None}. Returns sample pts or None."""
    dist = math.hypot(b[0] - a[0], b[1] - a[1])
    best = None
    for hf in (0.4, 0.6, 0.85, 1.15):
        pts = _vg_bez(a, na, b, nb, hf * dist)
        ls = LineString(pts)
        if any(ls.intersection(poly).length > 1.0 for poly, _e in obstacles):
            continue
        if _vg_min_radius_mm(pts, ppm) < min_r_mm - 0.2:
            continue
        k = max(3, int(0.18 * len(pts)))
        ok = True
        for poly, end in obstacles:
            seg = (
                LineString(pts[k:])
                if end == "a"
                else LineString(pts[: len(pts) - k])
                if end == "b"
                else ls
            )
            if seg.distance(poly) < clear_mm * ppm:
                ok = False
                break
        if not ok:
            continue
        r = _vg_min_radius_mm(pts, ppm)
        if best is None or r > best[0]:
            best = (r, pts)
    return best[1] if best else None


def _vg_tab_at(pts, i, s, cfg):
    tx = pts[i + 1][0] - pts[i - 1][0]
    ty = pts[i + 1][1] - pts[i - 1][1]
    tn = math.hypot(tx, ty) or 1.0
    tx, ty = tx / tn, ty / tn
    p = pts[i]
    L = cfg.tab_len_px
    estart = (p[0] - tx * L / 2, p[1] - ty * L / 2)  # stem-center stays at p
    tp = _tab_bulb_polygon(estart, (tx, ty), L, s, 0.0, cfg, tab_len=L)
    return tp, (tx, ty)


def _vg_tab_candidates(
    pts, letters_solid, cfg, panel, placed=(), cap_mm=15.0, min_span_mm=4.0, top=12
):
    """Ranked tab positions for a seam: each valid (i, s) that stays inside the
    panel, off the border, and clear of already-placed tabs, sorted by thinnest
    bridge to a letter (capped at cap_mm; ties -> most central). Returns up to
    `top` candidates best-first so the caller can fall back to another side or
    spot when its first choice collides with a neighbouring seam."""
    ppm = cfg.px_per_mm
    border_floor = 3.0 * ppm
    mid = len(pts) // 2
    cands = []
    for s in (1, -1):
        for i in range(2, len(pts) - 2):
            tp, tan = _vg_tab_at(pts, i, s, cfg)
            if tp is None or tp.is_empty:
                continue
            if tp.difference(panel).area > 1.0:  # runs off the panel
                continue
            if panel.exterior.distance(tp) < border_floor:  # too close to edge
                continue
            if any(tp.intersects(pk) for pk in placed):  # tabs must not touch
                continue
            span = letters_solid.distance(tp) / ppm
            if span < min_span_mm:
                continue
            key = (round(min(span, cap_mm), 2), -abs(i - mid))
            cands.append((key, i, s))
    cands.sort(key=lambda c: c[0], reverse=True)
    return [(i, s) for _k, i, s in cands[:top]]


def _vg_best_tab(
    pts, letters_solid, cfg, panel, placed=(), cap_mm=15.0, min_span_mm=4.0
):
    """The single best tab position (i, s), or None — see _vg_tab_candidates."""
    c = _vg_tab_candidates(
        pts, letters_solid, cfg, panel, placed, cap_mm, min_span_mm, top=1
    )
    return c[0] if c else None


def _vg_splice(pts, i, s, cfg):
    """Splice the tab OUTLINE into the seam polyline at sample i (side s): the
    curve runs up to the tab base, up one stem side, around the bulb, down the
    other stem side, then continues — so the cut goes AROUND the tab, connected
    to either side of its base (never across the stem). Returns (spliced_pts,
    bulb_polygon) or None."""
    L = cfg.tab_len_px
    seg = math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1]) or 1.0
    k = max(2, int(round((L / seg) / 2)))
    iL, iR = i - k, i + k
    if iL < 1 or iR > len(pts) - 2:
        return None
    pL, pR = pts[iL], pts[iR]
    d = math.hypot(pR[0] - pL[0], pR[1] - pL[1])
    if d < 2 * cfg.tab_circle_r_px:
        return None
    edir = ((pR[0] - pL[0]) / d, (pR[1] - pL[1]) / d)
    detour = place_tab_at_offset(pL, edir, d, s, 0.0, cfg, tab_len=d)
    spliced = pts[:iL] + detour + pts[iR + 1 :]
    bulb = _tab_bulb_polygon(pL, edir, d, s, 0.0, cfg, tab_len=d)
    return spliced, bulb


def build_pieces_vertex_grid(seed, letter_union, cfg, origins):
    """Vertex-grid layout (curved anchored-seam model). Background tiled by:
      * GAP seams: letter->letter CURVES between allowed convex vertices (line of
        sight + both normals point toward the other letter), launching
        perpendicular, bending no tighter than 5mm, staying >=4mm off letters.
        The seed picks which allowed vertex pair(s) become the seam(s) (one near
        mid-height; a second if the column is lopsided).
      * CAP seams: one vertical from each letter's top and bottom to the border.
      * END seams: outer letters -> L/R border.
    Every seam gets ONE deterministic tab (max thinnest bridge, cap 15mm, best
    side). Returns (piece_polys{(i,0):Polygon}, stats) — standard contract."""
    ppm = cfg.px_per_mm
    px, py = cfg.margin_px, cfg.margin_px
    pw, ph = cfg.puzzle_w_px, cfg.puzzle_h_px
    panel = box(px, py, px + pw, py + ph)
    stats = {"total": 0, "centered": 0, "shifted": 0, "flipped": 0, "dropped": 0}
    if letter_union is None:
        return {(0, 0): panel}, stats

    glyphs = sorted(
        [
            g
            for g in (
                letter_union.geoms
                if isinstance(letter_union, MultiPolygon)
                else [letter_union]
            )
            if g.area > 100
        ],
        key=lambda g: g.centroid.x,
    )
    solids = [Polygon(g.exterior) for g in glyphs]
    letters_solid = unary_union(solids)
    verts = [_vg_anchors(g, ppm) for g in glyphs]
    norms = [[_vg_normal(g, v, ppm) for v in vs] for g, vs in zip(glyphs, verts)]
    def obstacles(attach):
        return [(sol, attach.get(k)) for k, sol in enumerate(solids)]

    # Virtual vertices every ~10mm along the border (normal points INTO panel so
    # a seam arrives perpendicular to the edge, like a letter vertex).
    cr = cfg.corner_radius_mm * ppm
    step = 10 * ppm

    def _frange(lo, hi, d):
        out, t = [], lo
        while t <= hi:
            out.append(t)
            t += d
        return out

    bv_top = [((x, py), (0.0, 1.0)) for x in _frange(px + cr, px + pw - cr, step)]
    bv_bot = [((x, py + ph), (0.0, -1.0)) for x in _frange(px + cr, px + pw - cr, step)]
    bv_left = [((px, y), (1.0, 0.0)) for y in _frange(py + cr, py + ph - cr, step)]
    bv_right = [
        ((px + pw, y), (-1.0, 0.0)) for y in _frange(py + cr, py + ph - cr, step)
    ]
    background = panel.difference(letter_union)

    # Allowed letter->letter gap edges per adjacent pair (curve + feasible tab).
    # Density-independent, so compute once and reuse across attempts.
    gap_allowed = []
    for gi in range(len(glyphs) - 1):
        gj = gi + 1
        allowed = []
        for ia, a in enumerate(verts[gi]):
            na = norms[gi][ia]
            for ib, b in enumerate(verts[gj]):
                nb = norms[gj][ib]
                chord = LineString([a, b])
                if (
                    chord.intersection(solids[gi]).length > 1
                    or chord.intersection(solids[gj]).length > 1
                ):
                    continue  # line of sight
                dxb, dyb = b[0] - a[0], b[1] - a[1]
                dn = math.hypot(dxb, dyb) or 1.0
                if (na[0] * dxb + na[1] * dyb) / dn <= 0.2:
                    continue  # A's normal must point toward B
                if (nb[0] * -dxb + nb[1] * -dyb) / dn <= 0.2:
                    continue
                pts = _vg_curve(a, na, b, nb, obstacles({gi: "a", gj: "b"}), ppm)
                if pts is None:
                    continue
                if LineString(pts).length < 1.5 * cfg.tab_len_px:
                    continue  # too short to carry a tab (real tab placed later)
                allowed.append(((a[1] + b[1]) / 2, pts, ia, ib))
        gap_allowed.append(allowed)

    def _border_seam(gi, a, na, bvs, jitter_span, rng):
        """Curve from letter vertex a to a seed-picked border virtual vertex, same
        perpendicular-launch + smooth-curve machinery as the gap seams."""
        allow = []
        for b, nb in bvs:
            dxb, dyb = b[0] - a[0], b[1] - a[1]
            dn = math.hypot(dxb, dyb) or 1.0
            if (na[0] * dxb + na[1] * dyb) / dn <= 0.2:  # launch toward the border
                continue
            pts = _vg_curve(a, na, b, nb, obstacles({gi: "a"}), ppm)
            if pts is None or LineString(pts).length < 1.5 * cfg.tab_len_px:
                continue
            allow.append((b, pts))
        if not allow:
            return None
        tgt = a[0] if jitter_span == "x" else a[1]
        key = 0 if jitter_span == "x" else 1
        tgt += rng.uniform(-15, 15) * ppm  # seed nudges which border node
        allow.sort(key=lambda e: abs(e[0][key] - tgt))
        return allow[0][1]

    def gen_seams(density, variant=0):
        """Lay out ALL seams for a given density. `density` = how many gap seams
        to aim for per letter gap and how many caps per letter top/bottom edge;
        higher density -> more, smaller pieces. Every seam follows the same
        curved, letter-anchored, exclusive-vertex rules. The seed (+ variant)
        decides which allowed vertices are picked for the requested targets."""
        rng = random.Random(seed * 131 + density * 17 + variant * 9973)
        gap_seams, end_seams = [], []
        used = set()  # (glyph_idx, vertex_idx) — vertices are exclusive
        min_sep = 1.6 * cfg.tab_len_px  # keep seams on a pair from bunching

        # GAP seams: up to `density` per pair, spread across the panel height.
        for gi in range(len(glyphs) - 1):
            gj = gi + 1
            allowed = gap_allowed[gi]
            if not allowed:
                # crowded gap: no curved edge cleared the filters — straight
                # facing-vertex fallback so the column still partitions.
                r = max(verts[gi], key=lambda p: p[0])
                lft = min(verts[gj], key=lambda p: p[0])
                gap_seams.append([(r[0], r[1]), (lft[0], lft[1])])
                continue
            targets = [py + ph * (m + 0.5) / density for m in range(density)]
            jit = rng.uniform(-0.08, 0.08) * ph
            chosen_h = []
            for tgt in targets:
                t = tgt + jit
                for e in sorted(allowed, key=lambda e: abs(e[0] - t)):
                    if (gi, e[2]) in used or (gj, e[3]) in used:
                        continue
                    if any(abs(e[0] - h) < min_sep for h in chosen_h):
                        continue
                    used.add((gi, e[2]))
                    used.add((gj, e[3]))
                    chosen_h.append(e[0])
                    gap_seams.append(e[1])
                    break
            if not chosen_h:  # guarantee at least one seam per pair
                e = min(allowed, key=lambda e: abs(e[0] - (py + ph / 2)))
                used.add((gi, e[2]))
                used.add((gj, e[3]))
                gap_seams.append(e[1])

        # CAP seams: up to `density` per letter top & bottom, spread across width.
        # Claim ONLY vertices whose normal actually points up/down toward that
        # border — leave the side-facing vertices (e.g. the A's right leg) free
        # for END seams. The FIRST (most central) cap of each letter side is a
        # "primary" cap: it is accepted before the gap seams so the top/bottom
        # margin is always broken into per-letter cells instead of one long
        # strip. Extra caps are accepted last (after gaps) as a bonus.
        primary_caps, extra_caps = [], []
        for gi, g in enumerate(glyphs):
            anchors = verts[gi]
            minx, _mn, maxx, _mx = g.bounds
            cx = (minx + maxx) / 2
            for is_top in (True, False):
                grp = [
                    iv
                    for iv in range(len(anchors))
                    if (norms[gi][iv][1] < -0.4 if is_top else norms[gi][iv][1] > 0.4)
                ]
                if not grp:
                    continue
                ncap = max(1, min(density, len(grp)))
                span = max(1.0, maxx - minx)
                # most-central target first, then spread — so the primary cap
                # lands near the letter's middle.
                xtargets = sorted(
                    [minx + span * (m + 0.5) / ncap for m in range(ncap)],
                    key=lambda x: abs(x - cx),
                )
                first = True
                for xt in xtargets:
                    avail = [iv for iv in grp if (gi, iv) not in used]
                    if not avail:
                        break
                    iv = min(avail, key=lambda k: abs(anchors[k][0] - xt))
                    used.add((gi, iv))
                    a = anchors[iv]
                    na = norms[gi][iv]
                    bvs = [
                        (b, nb)
                        for b, nb in (bv_top if is_top else bv_bot)
                        if abs(b[0] - a[0]) < 45 * ppm
                    ]
                    pts = _border_seam(gi, a, na, bvs, "x", rng)
                    if pts is None:
                        pts = [
                            (a[0], a[1]),
                            (a[0], py - 20 if is_top else py + ph + 20),
                        ]
                    (primary_caps if first else extra_caps).append(pts)
                    first = False

        # END seams: outer letters -> curved seams to the L/R border. Up to
        # `density` per side, spread by height, so the side margins subdivide
        # instead of leaving one tall end piece.
        for gi_end, side_bvs, want_left in (
            (0, bv_left, True),
            (len(glyphs) - 1, bv_right, False),
        ):
            vs = verts[gi_end]
            ns = norms[gi_end]
            # vertices whose normal faces outward to this side
            face = [
                k
                for k in range(len(vs))
                if (ns[k][0] < -0.2 if want_left else ns[k][0] > 0.2)
            ]
            if not face:
                face = [
                    min(range(len(vs)), key=lambda k: vs[k][0])
                    if want_left
                    else max(range(len(vs)), key=lambda k: vs[k][0])
                ]
            miny = min(vs[k][1] for k in face)
            maxy = max(vs[k][1] for k in face)
            spanY = max(1.0, maxy - miny)
            nend = max(1, min(density, len(face)))
            ytargets = [miny + spanY * (m + 0.5) / nend for m in range(nend)]
            placed_any = False
            for yt in ytargets:
                avail = [k for k in face if (gi_end, k) not in used]
                if not avail:
                    break
                k = min(avail, key=lambda k: abs(vs[k][1] - yt))
                used.add((gi_end, k))
                pts = _border_seam(gi_end, vs[k], ns[k], side_bvs, "y", rng)
                if pts is not None:
                    end_seams.append(pts)
                    placed_any = True
            if not placed_any:  # straight fallback so the margin still splits
                k = (
                    min(range(len(vs)), key=lambda k: vs[k][0])
                    if want_left
                    else max(range(len(vs)), key=lambda k: vs[k][0])
                )
                a = vs[k]
                xto = px - 20 if want_left else px + pw + 20
                end_seams.append([(a[0], a[1]), (xto, a[1])])
        # Acceptance order: HORIZONTAL edges first (gap seams across each gap +
        # end seams to the L/R border) to establish the row structure into a top
        # and bottom band, THEN VERTICAL caps to slice those bands into clean
        # per-letter cells. There's only one row (few L/R ends) but many letters
        # (many caps), so the caps are what carve up any band left too big.
        return gap_seams + end_seams + primary_caps + extra_caps

    # merge only genuinely small / thin-bbox surround slivers into a neighbor.
    # (Do NOT use an erosion-split test: a large L-shaped piece that wraps a
    # letter corner erodes into pieces yet is perfectly durable — that test
    # cascaded and collapsed crowded words like KARSON.)
    def absorb(polys):
        min_a = (13 * ppm) ** 2
        min_side = 10 * ppm
        changed = True
        while changed and len(polys) > 1:
            changed = False
            polys.sort(key=lambda p: p.area)
            for i, a in enumerate(polys):
                bx = a.bounds
                if a.area >= min_a and min(bx[2] - bx[0], bx[3] - bx[1]) >= min_side:
                    continue
                # neighbor with the longest shared boundary — buffer-based so
                # curved / tab-spliced edges (float-imperfect) still register.
                ab = a.buffer(0.75)
                best = None
                for j, b in enumerate(polys):
                    if i == j:
                        continue
                    shared = ab.intersection(b).area
                    if shared <= 0:
                        continue
                    u = unary_union([a, b])
                    if u.geom_type == "Polygon" and (best is None or shared > best[1]):
                        best = (j, shared, u)
                if best:
                    polys[best[0]] = best[2]
                    polys.pop(i)
                    changed = True
                    break
        return polys

    def assemble(seams):
        """Splice ONE tab into each seam (cut goes AROUND the tab, connected to
        either side of its base), polygonize into faces, absorb slivers. Returns
        (surround, counters, stats).

        Seams are accepted GREEDILY with a hard spacing rule: a candidate is
        dropped unless its whole spliced path (curve + tab outline) stays
        >=min_gap from every already-accepted seam. Because the tab is spliced
        into the path, this one test keeps seams from crossing each other,
        clipping each other's tabs, sitting back-to-back, or leaving a sub-4mm
        wood bridge between two seams — the "no seam-to-seam junction in open
        background" rule. A dropped seam just means its two faces stay merged."""
        st = {"total": 0, "centered": 0, "shifted": 0, "flipped": 0, "dropped": 0}
        placed_bulbs = []
        tabbed = []
        accepted = []  # LineString of each accepted spliced seam (curve + tab)
        min_gap = cfg.letter_clearance_px  # >=4mm bridge between any two seams

        def _bg_conflict(cand):
            """A candidate seam conflicts with an accepted seam only where the
            space between them is OPEN BACKGROUND thinner than min_gap (a sliver
            or a crossing). If they approach near a letter, the letter fills the
            space — that is an allowed junction ON the letter (a cap and a gap
            seam sharing a corner), not a thin wood bridge."""
            for a in accepted:
                if cand.distance(a) >= min_gap:
                    continue
                q1, q2 = nearest_points(cand, a)
                mid = Point((q1.x + q2.x) / 2, (q1.y + q2.y) / 2)
                if letters_solid.distance(mid) > min_gap:
                    return True
            return False

        for pts0 in seams:
            ls0 = LineString(pts0).intersection(background)
            if ls0.geom_type == "MultiLineString":
                ls0 = max(ls0.geoms, key=lambda g: g.length)
            if ls0.geom_type != "LineString" or ls0.length < 3 * ppm:
                continue
            n = max(6, int(ls0.length / (2 * ppm)))
            rs = [
                (
                    ls0.interpolate(t / n, normalized=True).x,
                    ls0.interpolate(t / n, normalized=True).y,
                )
                for t in range(n + 1)
            ]
            st["total"] += 1
            chosen = None
            for scale in (1.0, 0.72, 0.5):
                c2 = (
                    cfg
                    if scale >= 0.999
                    else dc_replace(
                        cfg,
                        tab_circle_r_px=max(6, int(round(cfg.tab_circle_r_px * scale))),
                        tab_stem_w_px=(
                            cfg.tab_stem_w_px * scale if cfg.tab_stem_w_px else None
                        ),
                        tab_bulb_elong_px=cfg.tab_bulb_elong_px * scale,
                    )
                )
                # Try ranked tab positions: if the best one collides with a
                # neighbour seam, fall back to the other side / another spot
                # before giving up on this seam.
                for pi, ps in _vg_tab_candidates(
                    rs, letters_solid, c2, panel, placed_bulbs
                ):
                    res = _vg_splice(rs, pi, ps, c2)
                    if res is None:
                        continue
                    spliced, bulb = res
                    if bulb is None or spliced is None:
                        continue
                    cand = LineString(spliced)
                    # No crossing / no thin OPEN-BACKGROUND bridge to another
                    # seam. Approaching near a letter is fine (a shared junction
                    # on the letter — a cap and a gap seam off the same corner).
                    if _bg_conflict(cand):
                        continue
                    chosen = (spliced, bulb, cand)
                    break
                if chosen is not None:
                    break
            if chosen is not None:
                tabbed.append(chosen[0])
                placed_bulbs.append(chosen[1])
                accepted.append(chosen[2])
                st["centered"] += 1
            else:
                # No feasible / well-spaced tab -> NOT a valid seam. Drop it
                # entirely (the two faces it would have split merge cleanly)
                # rather than emit a crossing or tabless edge. If the merge makes
                # a piece too big, the density loop raises seam count and retries.
                st["dropped"] += 1

        net = unary_union(
            [panel.boundary, letter_union.boundary] + [LineString(t) for t in tabbed]
        )
        faces = [
            f
            for f in polygonize(net)
            if background.contains(f.representative_point()) and f.area > (ppm) ** 2
        ]
        surround = [
            f for f in faces if not letters_solid.contains(f.representative_point())
        ]
        counters = [
            f for f in faces if letters_solid.contains(f.representative_point())
        ]
        surround = absorb(surround)
        return surround, counters, st

    # ---- validity: size band + the critical no-thin-bridge durability rule ----
    # "Oversized" = a genuinely large BLOB, judged by area AND a fat short side.
    # A long L-shaped piece that wraps a letter corner has a big bbox but a small
    # area / thin waist — it is durable and fine, so it must NOT trip this.
    max_area = (50 * ppm) ** 2
    min_side_big = 34 * ppm
    max_dim = 60 * ppm

    def _oversized(poly):
        # Too big = large area AND either a fat short side (a big blob) or a long
        # max dimension (a long strip, e.g. a top-margin band spanning several
        # letters). A thin L-shaped letter-wrap has small area, so it passes.
        b = poly.bounds
        w, h = b[2] - b[0], b[3] - b[1]
        if poly.area <= max_area:
            return False
        return min(w, h) > min_side_big or max(w, h) > max_dim

    def _thin_bridge(poly):
        # Erode by half the 4mm floor. Flags a background piece that would snap:
        #  * empty after erosion    -> thinner than 4mm everywhere (a sliver),
        #  * >=2 substantial lobes  -> a <4mm waist joining two blobs (dumbbell),
        #  * erodes to <10% of area -> a thin strip.
        # A tab bulb erodes to a sub-7mm nub, so a normal piece-with-a-tab passes.
        er = poly.buffer(-2.0 * ppm)
        if er.is_empty:
            return True
        lobes = [p for p in _poly_list_vg(er) if p.area > (7 * ppm) ** 2]
        if len(lobes) >= 2:
            return True
        return er.area < 0.10 * poly.area

    def _score(surround, st):
        thin = sum(1 for p in surround if _thin_bridge(p))
        over = sum(1 for p in surround if _oversized(p))
        big = max((p.area for p in surround), default=0.0)
        # Rank: durability first (no sub-4mm bridge), then how many pieces bust
        # the size band, then the AREA of the single largest piece — so among
        # otherwise-equal layouts we keep the one whose biggest piece is smallest.
        return (thin, over, round(big, 1))

    # Generate-and-test: sweep densities LOW -> HIGH and keep the SPARSEST layout
    # that is fully valid (durable + no oversized piece). Fewest seams that still
    # keep every piece in the size band = reasonable-size pieces without over-
    # slicing a region into tiny awkward bits (now that corner junctions let low
    # densities partition cleanly). If none is fully valid, fall back to the
    # best-scoring attempt (fewest thin, then fewest oversized, then smallest max).
    best = None
    for density in (1, 2, 3, 4, 5):
        seams = gen_seams(density)
        surround, counters, st = assemble(seams)
        sc = _score(surround, st)
        if best is None or sc < best[0]:
            best = (sc, surround, counters, st, density)
        if sc[0] == 0 and sc[1] == 0:
            break
    sc, surround, counters, stats, density = best
    stats["density"] = density
    stats["thin"], stats["oversized"] = sc[0], sc[1]

    pieces = {}
    for idx, poly in enumerate(surround + counters):
        pieces[(idx, 0)] = poly
    return pieces, stats
