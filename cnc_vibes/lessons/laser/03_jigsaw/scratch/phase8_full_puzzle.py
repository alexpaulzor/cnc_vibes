#!/usr/bin/env python3
"""Phase 8 — full NORA-scale puzzle GCode (300×300mm, ~44 pieces).

Differences vs phase6_small:
  - Uses phase2's default panel constants (300mm, 50mm cells, 6x6 grid)
  - Edge dedup via shapely.ops.unary_union — shared cell-cell boundaries
    are cut exactly once instead of twice
  - Containment-aware ordering: letter perimeters first (they sit inside
    cell pockets), then interior cell-cell boundaries, then panel
    perimeter last (so the stock stays attached until the final cut)
  - Within each tier, greedy nearest-neighbor reduces rapid travel
  - linemerge consolidates collinear/connected segments into single
    continuous paths so the laser doesn't lift between every vertex

Loose-fit puzzle: cuts on centerline, kerf becomes the natural clearance.

Photo raster engraving for the full puzzle is intentionally deferred —
phase7_raster.py demonstrates the raster pipeline against the small
puzzle; combining raster + full-cut needs a refactor to decouple
phase7's image emitter from phase6_small's constant overrides. Not done
here.

Usage:
  python phase8_full_puzzle.py --word NORA --seed 7
  python cnc.py validate lessons/laser/03_jigsaw/build/full_puzzle_nora.gcode
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPolygon,
    Polygon,
)
from shapely.ops import linemerge, unary_union

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

# phase8 wants phase2's DEFAULT constants (300x300mm panel, 50mm cells).
# phase6_small mutates those to small-puzzle values, which would silently
# poison phase8's piece generation. The check lives in generate_pieces()
# (not at import time) so the test suite — which uses synthetic geometry
# and never calls generate_pieces — can coexist with the phase6 tests in
# the same pytest session.

import diagram_word_phase2 as p2  # noqa: E402
import diagram_word_phase5 as p5  # noqa: E402
from diagram_word_phase4 import render_diagram  # noqa: E402

REPO_ROOT = SCRIPT_DIR.parent.parent.parent.parent
OUT_FIG_DIR = SCRIPT_DIR.parent / "figs"
BUILD_DIR = SCRIPT_DIR.parent / "build"
OUT_FIG_DIR.mkdir(parents=True, exist_ok=True)
BUILD_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Piece generation (mirrors phase5 v04)
# ---------------------------------------------------------------------------


def generate_pieces(word: str, seed: int) -> tuple[list[dict], dict]:
    if "phase6_small" in sys.modules:
        raise RuntimeError(
            "phase8 generate_pieces() requires phase2's default constants, "
            "but phase6_small is loaded in this process and has mutated them "
            "to small-puzzle values. Run phase8 in a fresh Python process."
        )
    puzzle_w = p2.COLS * p2.CELL_W
    puzzle_h = p2.ROWS * p2.CELL_H
    img_w = puzzle_w + 2 * p2.MARGIN + p2.TAB_HEIGHT
    img_h = puzzle_h + 2 * p2.MARGIN + p2.TAB_HEIGHT + p2.LEGEND_H

    letter_union, _, _, _ = p2.render_letter_polygons(
        word, img_w, img_h, p2.MARGIN, p2.MARGIN, puzzle_w, puzzle_h
    )
    piece_polys, stats = p5.build_pieces_with_shifted_tabs(seed, letter_union)

    cell_fragments = []
    for (c, r), piece in sorted(piece_polys.items()):
        remaining = piece if letter_union is None else piece.difference(letter_union)
        if remaining.is_empty:
            continue
        if isinstance(remaining, (MultiPolygon, GeometryCollection)):
            for geom in remaining.geoms:
                if isinstance(geom, Polygon) and geom.area > 100:
                    cell_fragments.append(
                        {"parent": (c, r), "polygon": geom, "kind": "cell"}
                    )
        elif isinstance(remaining, Polygon) and remaining.area > 100:
            cell_fragments.append(
                {"parent": (c, r), "polygon": remaining, "kind": "cell"}
            )
    cell_fragments = p5.merge_small_fragments(cell_fragments)

    letter_polys = []
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


# ---------------------------------------------------------------------------
# Edge extraction + classification
# ---------------------------------------------------------------------------


def extract_unique_edges(pieces: list[dict]) -> list[LineString]:
    """unary_union dedupes shared edges; linemerge combines collinear
    connected segments into continuous chains."""
    boundaries = [p["polygon"].boundary for p in pieces]
    merged = unary_union(boundaries)
    if isinstance(merged, (MultiLineString, GeometryCollection)):
        segs = [g for g in merged.geoms if isinstance(g, LineString)]
    elif isinstance(merged, LineString):
        segs = [merged]
    else:
        segs = []
    if len(segs) <= 1:
        return segs
    chained = linemerge(MultiLineString(segs))
    if isinstance(chained, LineString):
        return [chained]
    if isinstance(chained, MultiLineString):
        return list(chained.geoms)
    return [g for g in chained.geoms if isinstance(g, LineString)]


def classify_edge(
    edge: LineString,
    letter_polys: list[Polygon],
    panel_x0: float,
    panel_y0: float,
    panel_w: float,
    panel_h: float,
    eps: float = 0.5,
) -> str:
    """Return 'letter', 'panel', or 'interior' for one edge."""
    # Panel border: ALL points of the edge lie on one panel side
    coords = list(edge.coords)
    on_left = all(abs(x - panel_x0) < eps for x, _ in coords)
    on_right = all(abs(x - (panel_x0 + panel_w)) < eps for x, _ in coords)
    on_top = all(abs(y - panel_y0) < eps for _, y in coords)
    on_bottom = all(abs(y - (panel_y0 + panel_h)) < eps for _, y in coords)
    if on_left or on_right or on_top or on_bottom:
        return "panel"
    # Letter perimeter: edge lies on a letter polygon's boundary
    for lp in letter_polys:
        try:
            if edge.distance(lp.boundary) < eps:
                return "letter"
        except Exception:
            continue
    return "interior"


# ---------------------------------------------------------------------------
# Greedy travel-minimizing order
# ---------------------------------------------------------------------------


def greedy_order(
    edges: list[LineString], start_pt: tuple[float, float] = (0.0, 0.0)
) -> list[tuple[LineString, bool]]:
    """Pick the nearest unused edge endpoint to the current pen position;
    return (edge, reverse) so the caller knows which direction to cut.
    O(n^2); fine for <2000 edges."""
    remaining = list(range(len(edges)))
    ordered: list[tuple[LineString, bool]] = []
    cur = start_pt
    while remaining:
        best_idx = None
        best_dist = float("inf")
        best_reverse = False
        for i in remaining:
            e = edges[i]
            c0 = e.coords[0]
            cn = e.coords[-1]
            d0 = (c0[0] - cur[0]) ** 2 + (c0[1] - cur[1]) ** 2
            dn = (cn[0] - cur[0]) ** 2 + (cn[1] - cur[1]) ** 2
            if d0 < best_dist:
                best_dist = d0
                best_idx = i
                best_reverse = False
            if dn < best_dist:
                best_dist = dn
                best_idx = i
                best_reverse = True
        e = edges[best_idx]
        coords = list(e.coords)
        if best_reverse:
            coords = coords[::-1]
            ordered.append((LineString(coords), True))
        else:
            ordered.append((e, False))
        cur = coords[-1]
        remaining.remove(best_idx)
    return ordered


# ---------------------------------------------------------------------------
# GCode emission
# ---------------------------------------------------------------------------


def img_to_machine_mm(x_px: float, y_px: float) -> tuple[float, float]:
    """Image (Y-down, panel offset by MARGIN) → machine mm (Y-up, panel at 0,0)."""
    x_mm = (x_px - p2.MARGIN) / p2.PX_PER_MM
    y_mm = p2.PANEL_MM - (y_px - p2.MARGIN) / p2.PX_PER_MM
    return (x_mm, y_mm)


def load_material(material_id: str) -> dict:
    with (REPO_ROOT / "profiles" / "laser_materials.yaml").open() as f:
        materials = yaml.safe_load(f)
    for m in materials:
        if m.get("id") == material_id:
            return m
    raise SystemExit(f"unknown material: {material_id}")


def emit_gcode(
    ordered: list[tuple[LineString, bool]], material: dict, word: str
) -> str:
    laser = material["laser"]
    power_s = int(round(laser["power_percent"] * 10))
    feed = laser["feed_mm_per_min"]
    passes = laser["passes"]

    lines = [
        f"; full puzzle — word={word}, panel={p2.PANEL_MM}x{p2.PANEL_MM}mm, "
        f"{len(ordered)} ordered cut paths",
        f"; generated by lessons/laser/03_jigsaw/scratch/phase8_full_puzzle.py",
        f"; loose-fit: centerline cuts, laser kerf becomes the clearance",
        f"; edge dedup: shared cell-cell boundaries cut exactly once",
        f"; ASSUMES Z already at focal height in your WCS",
        f";",
        f";HEAD: laser",
        f";MATERIAL: {material['id']}",
        "",
        "$32=1   ; GRBL laser mode",
        "G21     ; mm",
        "G90     ; absolute",
        "M5      ; laser off",
        "G0 X0 Y0",
        "",
    ]

    for idx, (edge, _reversed) in enumerate(ordered, start=1):
        coords_mm = [img_to_machine_mm(x, y) for x, y in edge.coords]
        if len(coords_mm) < 2:
            continue
        x0, y0 = coords_mm[0]
        lines.append(f"; --- path {idx}/{len(ordered)} ({len(coords_mm)} pts) ---")
        lines.append(f"G0 X{x0:.3f} Y{y0:.3f}")
        lines.append(f"M4 S{power_s}")
        lines.append(f"F{feed}")
        for pass_n in range(passes):
            if passes > 1:
                lines.append(f"; pass {pass_n + 1} of {passes}")
            # If multipass and we're not on the first pass, we need to
            # re-traverse from the start. Alternate direction to skip the
            # rapid back, but simpler: rapid back to start each time.
            if pass_n > 0:
                lines.append(f"G0 X{x0:.3f} Y{y0:.3f}")
                lines.append(f"M4 S{power_s}")
            for x, y in coords_mm[1:]:
                lines.append(f"G1 X{x:.3f} Y{y:.3f}")
        lines.append("M5")
        lines.append("")

    lines += ["G0 X0 Y0", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--word", default="NORA")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--material", default="plywood_baltic_birch_3mm")
    args = ap.parse_args()
    word = args.word.upper()

    puzzle_w = p2.COLS * p2.CELL_W
    puzzle_h = p2.ROWS * p2.CELL_H
    img_w = puzzle_w + 2 * p2.MARGIN + p2.TAB_HEIGHT
    img_h = puzzle_h + 2 * p2.MARGIN + p2.TAB_HEIGHT + p2.LEGEND_H

    print(
        f"full puzzle: word={word} panel={p2.PANEL_MM}x{p2.PANEL_MM}mm "
        f"cells={p2.COLS}x{p2.ROWS}@{p2.PIECE_MM}mm tab_R={p2.TAB_CIRCLE_R}px"
    )

    pieces, stats = generate_pieces(word, args.seed)
    print(f"  tabs: {stats}")
    n_cells = sum(1 for p in pieces if p["kind"] == "cell")
    n_letters = len(pieces) - n_cells
    print(f"  pieces: {len(pieces)} ({n_cells} cells + {n_letters} letters)")

    # Verification diagram
    diagram_path = OUT_FIG_DIR / f"full_puzzle_{word.lower()}.png"
    render_diagram(
        pieces,
        img_w,
        img_h,
        p2.MARGIN,
        p2.MARGIN,
        puzzle_w,
        puzzle_h,
        title=f"Full puzzle — {word}, {len(pieces)} pieces, "
        f"{p2.PANEL_MM}x{p2.PANEL_MM}mm @ {p2.PIECE_MM}mm cells",
        out_path=diagram_path,
        show_letter_marker=False,
        highlight_letters=False,
    )
    print(f"  diagram: {diagram_path}")

    # Edge extraction + classification + ordering
    edges = extract_unique_edges(pieces)
    print(f"  unique cut paths after linemerge: {len(edges)}")

    letter_polys = [p["polygon"] for p in pieces if p["kind"] == "letter"]
    letters, interior, panel = [], [], []
    for e in edges:
        cat = classify_edge(e, letter_polys, p2.MARGIN, p2.MARGIN, puzzle_w, puzzle_h)
        if cat == "letter":
            letters.append(e)
        elif cat == "panel":
            panel.append(e)
        else:
            interior.append(e)
    print(
        f"  classified: {len(letters)} letter, {len(interior)} interior, {len(panel)} panel"
    )

    # Cut order: letter -> interior -> panel; greedy within each tier
    start_pt = (p2.MARGIN, p2.MARGIN)  # image coords; converted at emit time
    ordered = (
        greedy_order(letters, start_pt)
        + greedy_order(interior, start_pt)
        + greedy_order(panel, start_pt)
    )

    # Compute approximate total cut length for sanity
    total_px = sum(e.length for e, _ in ordered)
    total_mm = total_px / p2.PX_PER_MM
    print(f"  total cut length: {total_mm:.0f} mm")

    material = load_material(args.material)
    gcode = emit_gcode(ordered, material, word)
    gcode_path = BUILD_DIR / f"full_puzzle_{word.lower()}.gcode"
    gcode_path.write_text(gcode)
    print(f"-> {gcode_path}  ({len(gcode.splitlines())} lines)")
    print("\nValidate with:")
    print(f"  python cnc.py validate {gcode_path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
