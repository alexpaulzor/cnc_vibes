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
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
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

    panel_mm: float = 300.0  # outer panel side length (square)
    piece_mm: float = 50.0  # nominal cell size
    px_per_mm: int = 5  # render scale (5 px/mm = 0.2mm per pixel)
    tab_circle_r_px: int = 22  # lollipop bulb radius in pixels
    margin_px: int = 120  # canvas inset around the panel for rendering
    legend_h_px: int = 240  # extra canvas height below for labels

    # Shifting + merging parameters; default to "scale with tab radius"
    letter_clearance_factor: float = 1.0  # multiplied by tab_circle_r_px
    fragment_min_thickness_factor: float = 1.0  # multiplied by tab_circle_r_px
    fragment_min_area_factor: float = 0.10  # fraction of (cell_w * cell_h)
    shift_steps: int = 12
    shift_step_frac: float = 0.2

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
        self.cols = int(self.panel_mm // self.piece_mm)
        self.rows = int(self.panel_mm // self.piece_mm)
        self.cell_w_px = int(self.piece_mm * self.px_per_mm)
        self.cell_h_px = int(self.piece_mm * self.px_per_mm)
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

    def add_tab(pts, edge_start, edge_dir, edge_length, direction):
        stats["total"] += 1
        max_offset = edge_length - cfg.tab_len_px
        if max_offset <= 0:
            stats["dropped"] += 1
            return
        center = max_offset / 2
        offset = find_clear_tab_offset(
            edge_start, edge_dir, edge_length, letter_union, direction, cfg
        )
        if offset is None:
            stats["dropped"] += 1
            return
        if abs(offset - center) < 1.0:
            stats["centered"] += 1
        else:
            stats["shifted"] += 1
        pts.extend(
            place_tab_at_offset(
                edge_start, edge_dir, edge_length, direction, offset, cfg
            )[1:-1]
        )

    def piece_polygon(col, row, ox, oy):
        x0 = ox + col * cfg.cell_w_px
        y0 = oy + row * cfg.cell_h_px
        x1 = x0 + cfg.cell_w_px
        y1 = y0 + cfg.cell_h_px
        pts = [(x0, y0)]
        if row > 0:
            bulges_down = horizontal_tabs[(col, row - 1)]
            d = -1 if bulges_down else +1
            add_tab(pts, (x0, y0), (1, 0), cfg.cell_w_px, d)
        pts.append((x1, y0))
        if col < cfg.cols - 1:
            bulges_right = vertical_tabs[(col, row)]
            d = +1 if bulges_right else -1
            add_tab(pts, (x1, y0), (0, 1), cfg.cell_h_px, d)
        pts.append((x1, y1))
        if row < cfg.rows - 1:
            bulges_down = horizontal_tabs[(col, row)]
            d = +1 if bulges_down else -1
            add_tab(pts, (x1, y1), (-1, 0), cfg.cell_w_px, d)
        pts.append((x0, y1))
        if col > 0:
            bulges_right = vertical_tabs[(col - 1, row)]
            d = -1 if bulges_right else +1
            add_tab(pts, (x0, y1), (0, -1), cfg.cell_h_px, d)
        return pts

    px, py = cfg.margin_px, cfg.margin_px
    pieces: dict = {}
    for c in range(cfg.cols):
        for r in range(cfg.rows):
            pts = piece_polygon(c, r, px, py)
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


def generate_pieces(word: str, seed: int, cfg: PuzzleConfig) -> tuple[list[dict], dict]:
    """Full pipeline: render letter polygons, build cell pieces with
    shifted tabs, carve letter pockets, merge slivers, append letters as
    intact pieces.

    Returns (pieces, stats). pieces is a list of dicts each with
    'parent', 'polygon' (shapely), 'kind' ('cell' or 'letter'), 'serial'
    (1-indexed). stats is the tab-shifting stats dict.
    """
    word = word.upper()
    letter_union, _text_x, _text_y, _font = render_letter_polygons(word, cfg)
    piece_polys, stats = build_pieces_with_shifted_tabs(seed, letter_union, cfg)
    cell_fragments = carve_letter_pockets(piece_polys, letter_union)
    cell_fragments = merge_small_fragments(cell_fragments, cfg)

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
