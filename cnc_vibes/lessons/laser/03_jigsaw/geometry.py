"""Pure-function geometry for the jigsaw lesson.

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
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, box
from shapely.ops import unary_union


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
    margin_px: int = 120  # canvas inset around the panel for rendering
    legend_h_px: int = 240  # extra canvas height below for labels

    # Rounded outer corners. 0 (default) = sharp 90° panel corners (legacy).
    # >0 rounds the four outer panel-perimeter corners with this radius (mm).
    corner_radius_mm: float = 0.0

    # Vertically center the letter band on the nearest interior HORIZONTAL
    # grid line instead of free-centering it in the middle of the panel.
    # When the panel has an even row count this puts the letters straddling
    # a row boundary, so each row is carved symmetrically into larger,
    # tabbable chunks rather than thin mid-row slivers. Default True.
    snap_letters_to_grid: bool = True

    # Shifting + merging parameters; default to "scale with tab radius"
    letter_clearance_factor: float = 1.0  # multiplied by tab_circle_r_px
    fragment_min_thickness_factor: float = 1.0  # multiplied by tab_circle_r_px
    fragment_min_area_factor: float = 0.10  # fraction of (cell_w * cell_h)
    shift_steps: int = 12
    shift_step_frac: float = 0.2

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
        self.letter_clearance_px = self.tab_circle_r_px * self.letter_clearance_factor
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
        return self.cols * self.cell_w_px

    @property
    def puzzle_h_px(self) -> int:
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
def small_puzzle_config() -> PuzzleConfig:
    """80x80mm panel with 40mm cells. Matches scratch/phase6_small.py."""
    return PuzzleConfig(panel_mm=80, piece_mm=40, tab_circle_r_px=15)


def full_puzzle_config() -> PuzzleConfig:
    """300x300mm panel with 50mm cells. Matches scratch/phase2.py defaults."""
    return PuzzleConfig(panel_mm=300, piece_mm=50, tab_circle_r_px=22)


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
        wave_amplitude_px=4,
        corner_radius_mm=3.0,
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
    direction: int, cfg: PuzzleConfig, n: int = 24
) -> list[tuple[float, float]]:
    """Lollipop tab: short stem (width = R, length ~ R) rising from the
    edge into a circle of radius R. Returns (u, v) with u in [0, 1] across
    tab_len_px and v in [0, 1] across tab_height_px (= 3R). direction is +1
    for an outward bulb, -1 for an inward cavity."""
    R = cfg.tab_circle_r_px
    H = cfg.tab_height_px  # 3 * R
    L = cfg.tab_len_px
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


def place_tab_at_offset(
    edge_start: tuple[float, float],
    edge_dir: tuple[float, float],
    edge_length: float,
    direction: int,
    offset_u: float,
    cfg: PuzzleConfig,
) -> list[tuple[float, float]]:
    """Tab outline placed in world coords at the given offset along the edge."""
    out = (edge_dir[1], -edge_dir[0])
    local = tab_outline(direction, cfg)
    world = []
    for u, v in local:
        u_world = offset_u + u * cfg.tab_len_px
        x = edge_start[0] + u_world * edge_dir[0] + v * cfg.tab_height_px * out[0]
        y = edge_start[1] + u_world * edge_dir[1] + v * cfg.tab_height_px * out[1]
        world.append((x, y))
    return world


def _tab_bulb_polygon(
    edge_start, edge_dir, edge_length, direction, offset_u, cfg
) -> Polygon | None:
    pts = place_tab_at_offset(
        edge_start, edge_dir, edge_length, direction, offset_u, cfg
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


def find_clear_tab_offset(
    edge_start,
    edge_dir,
    edge_length,
    letter_union,
    direction,
    cfg: PuzzleConfig,
) -> float | None:
    """Walk candidate offsets (center first, then alternating left/right by
    SHIFT_STEPS x SHIFT_STEP_FRAC * tab_len_px) and return the first one
    where the tab bulb clears the letter union by letter_clearance_px.
    Returns None if no clear position exists (caller should drop the tab)."""
    max_offset = edge_length - cfg.tab_len_px
    if max_offset <= 0:
        return None
    center = max_offset / 2
    step = cfg.tab_len_px * cfg.shift_step_frac

    candidates = [center]
    for i in range(1, cfg.shift_steps + 1):
        for sign in (-1, +1):
            o = center + sign * i * step
            if 0 <= o <= max_offset:
                candidates.append(o)

    if letter_union is None:
        return center

    for offset_u in candidates:
        bulb = _tab_bulb_polygon(
            edge_start, edge_dir, edge_length, direction, offset_u, cfg
        )
        if bulb is None:
            continue
        try:
            if bulb.distance(letter_union) >= cfg.letter_clearance_px:
                return offset_u
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Letter polygons via Pillow + OpenCV contour tracing
# ---------------------------------------------------------------------------


def find_font(size: int) -> ImageFont.ImageFont:
    for path in (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
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
    font = find_font(font_size)

    tmp = Image.new("L", (img_w, img_h), 0)
    td = ImageDraw.Draw(tmp)
    bbox = td.textbbox((0, 0), word, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    max_text_w = int(puzzle_w * 0.88)
    if tw > max_text_w:
        scale = max_text_w / tw
        font_size = int(font_size * scale)
        font = find_font(font_size)
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
    letter outlines; if no clear position exists, the tab is dropped (the
    edge becomes a straight cut).

    Returns (piece_polys, stats) where:
      piece_polys: {(col, row): shapely.Polygon}
      stats: {'total': N, 'centered': X, 'shifted': Y, 'dropped': Z}
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

    stats = {"total": 0, "centered": 0, "shifted": 0, "dropped": 0}
    wave_amp = cfg.wave_amplitude_px
    wave_steps = cfg.wave_steps
    px, py = cfg.margin_px, cfg.margin_px
    cw, ch = cfg.cell_w_px, cfg.cell_h_px

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
        if max_offset > 0:
            offset = find_clear_tab_offset(
                c0, edge_dir, length, letter_union, tab_dir, cfg
            )
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
        tw = place_tab_at_offset(c0, edge_dir, length, tab_dir, offset, cfg)
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
    as a list of {'parent': (c, r), 'polygon': Polygon, 'kind': 'cell'}."""
    fragments = []
    for (c, r), piece in sorted(piece_polys.items()):
        remaining = piece if letter_union is None else piece.difference(letter_union)
        if remaining.is_empty:
            continue
        if isinstance(remaining, (MultiPolygon, GeometryCollection)):
            for geom in remaining.geoms:
                if isinstance(geom, Polygon) and geom.area > 100:
                    fragments.append(
                        {"parent": (c, r), "polygon": geom, "kind": "cell"}
                    )
        elif isinstance(remaining, Polygon) and remaining.area > 100:
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


def generate_pieces(word: str, seed: int, cfg: PuzzleConfig) -> tuple[list[dict], dict]:
    """Full pipeline: render letter polygons, build cell pieces with
    shifted tabs, carve letter pockets, merge slivers, fuse split letter
    counters, round outer corners, append letters as intact pieces.

    Returns (pieces, stats). pieces is a list of dicts each with
    'parent', 'polygon' (shapely), 'kind' ('cell' or 'letter'), 'serial'
    (1-indexed). stats is the tab-shifting stats dict.
    """
    word = word.upper()
    letter_union, _text_x, _text_y, _font = render_letter_polygons(word, cfg)
    piece_polys, stats = build_pieces_with_shifted_tabs(seed, letter_union, cfg)
    cell_fragments = carve_letter_pockets(piece_polys, letter_union)
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
    for i, p in enumerate(pieces, start=1):
        p["serial"] = i
    return pieces, stats
