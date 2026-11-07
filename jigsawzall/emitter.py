"""GCode emission for jigsawzall.

Unifies what previously lived in three scratch scripts:
- phase6_small.emit_gcode (per-polygon cut, simple ordering)
- phase8_full_puzzle.emit_gcode (edge dedup via unary_union+linemerge,
  containment toposort: letter perimeters → interior → panel border last,
  greedy nearest-neighbor within tier)
- phase7_raster.emit_raster_gcode (M4 dynamic; G0 over white; G1 with
  per-run S for either halftone or grayscale)

Plus the three combined-output forms (raster only / cut only / combined)
from phase7. All emitters produce validator-clean GCode (HEAD/MATERIAL
headers, $32=1, S in [0, 1000], coords within panel envelope). Cuts
default to STATIC M3 (constant power): a weak ~10W diode wants constant
power, because M4 scales power with feedrate and such a diode never
reaches the cutting threshold that way. Raster engraving uses M4 dynamic.

Coordinate convention: piece polygons come in image-pixel coords
(Y-down, panel inset by margin_px); emitter flips to machine mm (Y-up,
panel at 0,0). Conversion is centralized in img_to_machine_mm.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "quickcut"))
import gcode as _qc  # noqa: E402  (THE G-code core: emission, ordering, finishing all live there)

from PIL import Image
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPolygon,
    Polygon,
)
from shapely.ops import linemerge, unary_union

from motion import collapse_shuttles, decimate  # noqa: E402
import grbl_model  # noqa: E402

from geometry import PuzzleConfig

REPO_ROOT = Path(__file__).resolve().parent

# Diode cold-start ramp: time to reach full optical power after the beam fires.
# Fixed for this machine regardless of material — every cut warms up for
# this long (front-loaded as a fwd-half / back-half wiggle = 500ms each way).
WARMUP_MS = _qc.DEFAULT_WARMUP_MS

# emit_etch_gcode(smooth_mm=...): share of the shape-change budget spent on
# the biarc fit (the rest goes to the smoothing spline).
SMOOTH_ARC_TOL_MM = 0.02


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------


def load_material(material_id: str) -> dict:
    """the full material record from material_profiles/laser_materials.yaml (quickcut.gcode.material)"""
    return _qc.material(material_id)


def cut_job(material: dict, recipe: str = "laser", feed_override: int | None = None,
            power_percent: float | None = None, mode: str | None = None, ramp_ms: float = WARMUP_MS,
            min_segment_mm: float = 0.0) -> "_qc.CutJob":
    """a quickcut CutJob from a material record (laser = cut, etch = score) plus this tool's
    overrides. An etch recipe without lead_in_mm uses quickcut's linear warmup model."""
    if recipe not in material:
        raise SystemExit(
            f"material {material.get('id')!r} has no {recipe}: profile -- add one to "
            "laser_materials.yaml (see the etch: schema note) before emitting a pass for it")
    r = material[recipe]
    return _qc.CutJob(
        feed_mm_per_min=feed_override if feed_override is not None else r["feed_mm_per_min"],
        power_percent=power_percent if power_percent is not None else r["power_percent"],
        passes=r.get("passes", 1), warmup_ms=ramp_ms, min_segment_mm=min_segment_mm,
        material_id=material["id"], mode=mode or r.get("mode", "static"),
        lead_in_mm=float(r["lead_in_mm"]) if recipe == "etch" and "lead_in_mm" in r else None)


# ---------------------------------------------------------------------------
# Coord conversion (image px Y-down → machine mm Y-up)
# ---------------------------------------------------------------------------


def img_to_machine_mm(
    x_px: float, y_px: float, cfg: PuzzleConfig
) -> tuple[float, float]:
    """Image pixel coords (panel inset by margin_px, Y-down) to machine
    mm (panel at origin, Y-up). cfg.origin_offset_mm shifts the result —
    set it to (panel_mm/2, panel_mm/2) to center the panel on WCS origin
    instead of pinning the bottom-left corner there.

    The Y flip is about the FITTED panel height (puzzle_h_px), not the
    panel_h_mm bound: with fit_to_text the fitted banner is shorter than the
    bound, and using the bound would float the panel up by (bound - fit) in +Y.
    For non-fit configs puzzle_h_px/px_per_mm == panel_height_mm, so unchanged."""
    fitted_h_mm = cfg.puzzle_h_px / cfg.px_per_mm
    x_mm = (x_px - cfg.margin_px) / cfg.px_per_mm - cfg.origin_offset_mm[0]
    y_mm = (
        fitted_h_mm - (y_px - cfg.margin_px) / cfg.px_per_mm - cfg.origin_offset_mm[1]
    )
    return (x_mm, y_mm)


# ---------------------------------------------------------------------------
# GCode header / preamble
# ---------------------------------------------------------------------------


def _header(title: str, job: "_qc.CutJob", extra: list[str] | None = None) -> list[str]:
    return _qc.preamble(job, title=title, notes=["generated by jigsawzall/emitter.py", *(extra or [])])


# ---------------------------------------------------------------------------
# Cut GCode: simple (per-polygon, no dedup) — for small puzzles
# ---------------------------------------------------------------------------


def _polygon_to_paths_mm(poly, cfg: PuzzleConfig) -> list[list[tuple[float, float]]]:
    """Exterior + holes of one polygon, in machine mm. Handles
    MultiPolygon / GeometryCollection recursively."""
    paths = []
    if isinstance(poly, Polygon):
        ext = [img_to_machine_mm(x, y, cfg) for x, y in poly.exterior.coords]
        paths.append(ext)
        for interior in poly.interiors:
            paths.append([img_to_machine_mm(x, y, cfg) for x, y in interior.coords])
    elif isinstance(poly, (MultiPolygon, GeometryCollection)):
        for sub in poly.geoms:
            if isinstance(sub, Polygon):
                paths.extend(_polygon_to_paths_mm(sub, cfg))
    return paths


def emit_cut_gcode_simple(
    pieces: list[dict],
    material: dict,
    cfg: PuzzleConfig,
    word: str,
    mode: str = "static",
    feed_override: int | None = None,
    min_segment_mm: float = 0.0,
    power_percent: float | None = None,
) -> str:
    """Per-polygon cut for small-piece-count puzzles (shared cell edges get cut twice; fine for
    ~5 pieces). Each ring is one quickcut cut (warmup wiggle, passes, overlap), in quickcut's
    middle-out order (letters sit inside their cell pockets, so they go first).
    mode "static" = M3 (default: a weak diode never reaches the cutting threshold under M4),
    "dynamic" = M4. feed_override / power_percent replace the material recipe; min_segment_mm
    decimates."""
    job = cut_job(material, feed_override=feed_override, power_percent=power_percent, mode=mode,
                  min_segment_mm=min_segment_mm)
    cuts = [_qc.Polyline(pts, True) for piece in pieces
            for pts in _polygon_to_paths_mm(piece["polygon"], cfg) if len(pts) >= 4]
    cuts = _qc.order(cuts)
    extra = [
        "loose-fit puzzle: centerline cuts, kerf becomes the clearance",
        "ASSUMES Z already at focal height in your WCS",
        "cut order: middle-out (letters inside their pockets first); shared edges cut twice",
    ]
    if min_segment_mm > 0:
        extra.append(f"min segment: {min_segment_mm}mm (shorter chords decimated)")
    lines = _header(f"small puzzle: word={word}, {len(pieces)} pieces, "
                    f"{cfg.puzzle_w_px / cfg.px_per_mm:.0f}x{cfg.puzzle_h_px / cfg.px_per_mm:.0f}mm", job, extra)
    for i, pl in enumerate(cuts, 1):
        lines += _qc.cut(pl, job, i, len(cuts))
    return _qc.render(lines, validate=False)


# ---------------------------------------------------------------------------
# Etch GCode: shallow non-cutting score pass (orientation / decorative marks)
# ---------------------------------------------------------------------------


def emit_etch_gcode(
    strokes_px: list[list[tuple[float, float]]],
    material: dict,
    cfg: PuzzleConfig,
    title: str,
    mode: str | None = None,
    feed_override: int | None = None,
    power_percent: float | None = None,
    min_segment_mm: float = 0.0,
    simplify_mm: float = 0.05,
    arc_tolerance_mm: float | None = None,
    smooth_mm: float | None = None,
    motion_report: bool = True,
    machine: "grbl_model.MachineProfile | None" = None,
    sender: "grbl_model.SenderProfile | None" = None,
) -> str:
    """A shallow, non-cutting score pass tracing `strokes_px` (open polylines, image px, e.g. from
    scripts/globe_etch.py): line-follow only, no tabs / kerf. Recipe = material["etch"] (a hard
    failure without one: etch numbers are meant to be looked at, not defaulted from the cut,
    which would gouge). Emission is quickcut's etch(): the recipe's fixed lead_in_mm or the linear
    warmup model, loops followed round past the start, open strokes ping-ponged first, a short
    tick one out-and-back; never re-traced to save a warmup.

    Strokes are prepared with quickcut.prepare(): simplified (`simplify_mm`) then decimated
    (`min_segment_mm`), or with `smooth_mm` replaced by a smoothing spline within
    (smooth_mm - 0.02) of the trace and emitted as tangent biarcs within 0.02.
    `arc_tolerance_mm` fits plain G2/G3 arcs instead. Chains are ordered nearest-next, fused where
    they touch (one start = one warmup), then re-ordered nearest-safe (min separation = half each
    chain's warmup, so consecutive laser-on events don't stack heat). `motion_report` adds
    grbl_model's prediction to the header (UNVERIFIED machine / sender defaults when None)."""
    job = cut_job(material, "etch", feed_override=feed_override, power_percent=power_percent, mode=mode)
    lead = job.etch_lead_mm
    chains = [_qc.prepare([img_to_machine_mm(x, y, cfg) for x, y in s], simplify_mm, min_segment_mm, smooth_mm,
                          SMOOTH_ARC_TOL_MM) for s in strokes_px if len(s) >= 2]
    chains = [c for c in chains if len(c) >= 2]
    start = (0.0, 0.0)
    chains = _qc.fuse_touching(_qc.nearest_chains(chains, start))
    min_sep_mm = lead / 2.0
    chains = _qc.nearest_safe(chains, start, lambda ch: min_sep_mm)

    pct, feed = job.power_percent, job.feed_mm_per_min
    extra = [
        "SHALLOW ETCH PASS -- does not cut through, orientation/decorative mark only",
        f"etch power/feed: {pct}% / {feed}mm/min -- UNVERIFIED, scrap-test first (see laser_materials.yaml)",
        f"{len(chains)} strokes",
        f"chain order: nearest-safe (min separation = half each chain's own wiggle lead-in, "
        f"{min_sep_mm:.2f}mm) -- keeps consecutive laser-off/on events apart",
        f"power mode: {job.on} ({job.mode}); strokes simplified {simplify_mm}mm, min segment {min_segment_mm}mm",
    ]
    if job.lead_in_mm is not None:
        extra.append(f"warmup: fixed {lead:.2f}mm lead-in (~{lead / (feed / 60.0) * 1000.0:.0f}ms at F{feed}) "
                     "-- loops follow through past the start, open strokes ping-pong")
    elif lead > 0:
        extra.append(f"warmup: linear model, {job.warmup_ms * pct / 100.0:.0f}ms = {lead:.2f}mm at F{feed} "
                     f"({pct}% of the {job.warmup_ms:.0f}ms full-power ramp) -- closed loops follow through "
                     "past the start (laps until warm), open strokes ping-pong over themselves first")
    if smooth_mm:
        extra.append(f"strokes SMOOTHED: spline within {smooth_mm - SMOOTH_ARC_TOL_MM:.2f}mm of the trace + "
                     f"tangent biarcs within {SMOOTH_ARC_TOL_MM}mm (max shape change {smooth_mm}mm, quickcut/arcfit.py)")
    elif arc_tolerance_mm:
        extra.append(f"strokes fitted to G2/G3 arcs within {arc_tolerance_mm}mm (quickcut/arcfit.py)")
    lines = _header(f"ETCH: {title}", job, extra)
    for i, pts in enumerate(chains, start=1):
        closed = len(pts) > 3 and math.dist(pts[0], pts[-1]) <= 0.05
        lines += _qc.etch(_qc.Polyline(pts, closed), job, i, len(chains),
                          arc_tol_mm=None if smooth_mm else arc_tolerance_mm,
                          smooth_tol_mm=SMOOTH_ARC_TOL_MM if smooth_mm else None)
    if motion_report:
        lines = _qc.motion_report(lines, machine, sender)
    return _qc.render(lines, validate=False)


# ---------------------------------------------------------------------------
# Cut GCode: edge-dedup with containment-aware ordering — for full puzzle
# ---------------------------------------------------------------------------


def extract_unique_edges(pieces: list[dict]) -> list[LineString]:
    """unary_union dedupes shared edges; linemerge combines collinear
    connected segments into continuous chains. Each interior edge
    appears exactly once in the output."""
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
    cfg: PuzzleConfig,
    eps: float = 0.5,
) -> str:
    """Return 'letter', 'panel', or 'interior'. Panel edges lie on the
    panel rectangle; letter edges lie on a letter polygon's boundary;
    everything else is interior cell-to-cell."""
    coords = list(edge.coords)
    panel_x0 = cfg.margin_px
    panel_y0 = cfg.margin_px
    panel_w = cfg.puzzle_w_px
    panel_h = cfg.puzzle_h_px
    if getattr(cfg, "panel_shape", "rect") == "disc":
        cx, cy, r = panel_x0 + panel_w / 2, panel_y0 + panel_h / 2, panel_w / 2
        if all(abs(math.hypot(x - cx, y - cy) - r) < eps for x, y in coords):
            return "panel"
    on_left = all(abs(x - panel_x0) < eps for x, _ in coords)
    on_right = all(abs(x - (panel_x0 + panel_w)) < eps for x, _ in coords)
    on_top = all(abs(y - panel_y0) < eps for _, y in coords)
    on_bottom = all(abs(y - (panel_y0 + panel_h)) < eps for _, y in coords)
    if on_left or on_right or on_top or on_bottom:
        return "panel"
    for lp in letter_polys:
        try:
            if edge.distance(lp.boundary) < eps:
                return "letter"
        except Exception:
            continue
    return "interior"


def greedy_order(
    edges: list[LineString], start_pt: tuple[float, float] = (0.0, 0.0)
) -> list[tuple[LineString, bool]]:
    """O(n²) greedy nearest-endpoint TSP. Returns (edge, reversed) per
    pick so the caller knows direction."""
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


def eulerian_order(
    edges: list[LineString],
    start_pt: tuple[float, float] = (0.0, 0.0),
    tol: float = 0.5,
    close_circuits: bool = True,
    max_dup_len: float | None = None,
) -> list[tuple[LineString, bool]]:
    """Order edges as continuous trails so the laser walks THROUGH grid
    junctions without lifting. Returns (edge, reversed) like greedy_order;
    the downstream chainer fuses each trail into one continuous laser-on
    run.

    With close_circuits=True this runs a Chinese-Postman pass: odd-degree
    junctions (where letter pockets cross grid edges) are paired and the
    shortest connector between each pair is RE-TRACED, making every
    connected region an even-degree graph that Hierholzer covers in a
    single closed circuit. The re-traced segments just pass back over
    already-cut lines (harmless on cardboard) but keep the laser on, so a
    whole region cuts with one laser-on event instead of one per junction.

    max_dup_len (in the edges' own units — px here) BOUNDS that re-tracing:
    an odd-node pair is only connected (re-traced) if the shortest connector
    between them is <= max_dup_len. Longer connectors are left unpaired, so
    Hierholzer splits the trail there and the laser restarts instead of
    backtracking. This is the ">1s" rule: re-cutting D mm at the feed costs
    D/feed of time, a restart costs the warmup lead-in; re-trace only when it
    is the cheaper of the two. None = unbounded (original always-continuous
    behavior); a wood grid without it re-cuts most edges twice.
    """
    if not edges:
        return []

    def key(p):
        return (round(p[0] / tol), round(p[1] / tol))

    ends = [(key(e.coords[0]), key(e.coords[-1])) for e in edges]
    pt_of = {}
    for i, e in enumerate(edges):
        pt_of[ends[i][0]] = e.coords[0]
        pt_of[ends[i][1]] = e.coords[-1]

    from collections import defaultdict, deque
    import heapq

    # Multigraph edge list: (u, v, geom_idx). Duplicates (re-traced
    # connectors) append more entries pointing at the same geometry.
    medges = [(ends[i][0], ends[i][1], i) for i in range(len(edges))]

    def node_adj(active):
        a = defaultdict(list)
        for gi in active:
            u, v, _ = medges[gi]
            a[u].append(gi)
            a[v].append(gi)
        return a

    # Connected components over the original nodes.
    base_adj = node_adj(range(len(medges)))
    seen = set()
    components = []
    for n in list(base_adj):
        if n in seen:
            continue
        comp = []
        dq = deque([n])
        seen.add(n)
        while dq:
            x = dq.popleft()
            comp.append(x)
            for gi in base_adj[x]:
                for y in (medges[gi][0], medges[gi][1]):
                    if y not in seen:
                        seen.add(y)
                        dq.append(y)
        components.append(set(comp))

    def dijkstra(src, comp_nodes):
        dist = {src: 0.0}
        prev = {}  # node -> (prev_node, geom_idx)
        pq = [(0.0, src)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist.get(u, 1e18):
                continue
            for gi in base_adj[u]:
                a, b, geom = medges[gi]
                w = edges[geom].length
                v = b if a == u else a
                nd = d + w
                if nd < dist.get(v, 1e18):
                    dist[v] = nd
                    prev[v] = (u, gi)
                    heapq.heappush(pq, (nd, v))
        return dist, prev

    if close_circuits:
        for comp in components:
            odd = [n for n in comp if len(base_adj[n]) % 2 == 1]
            if len(odd) < 2:
                continue
            # All-pairs shortest among odd nodes, then greedy nearest match.
            dmap = {}
            pmap = {}
            for o in odd:
                dmap[o], pmap[o] = dijkstra(o, comp)
            unmatched = set(odd)
            pairs = []
            while unmatched:
                a = min(unmatched, key=lambda n: (n[0], n[1]))
                unmatched.discard(a)
                best = None
                bestd = 1e18
                for b in unmatched:
                    dd = dmap[a].get(b, 1e18)
                    if dd < bestd:
                        bestd = dd
                        best = b
                if best is None:
                    continue
                # ">1s" rule: only re-trace (duplicate) the connector when it is
                # cheaper than a laser restart's warmup. A connector longer than
                # max_dup_len is left unpaired -> Hierholzer splits the trail here
                # and the laser restarts, instead of backtracking the long way.
                if max_dup_len is not None and bestd > max_dup_len:
                    continue
                unmatched.discard(best)
                pairs.append((a, best))
            # Duplicate the connector edges along each matched shortest path.
            for a, b in pairs:
                node = b
                while node != a and node in pmap[a]:
                    pn, gi = pmap[a][node]
                    u, v, geom = medges[gi]
                    medges.append((u, v, geom))  # re-traced connector
                    node = pn

    # Hierholzer over the (augmented) multigraph.
    adj = node_adj(range(len(medges)))
    used = [False] * len(medges)

    def walk(start):
        node_st = [start]
        edge_st = [None]
        out_nodes = []
        out_edges = []
        while node_st:
            v = node_st[-1]
            gi = None
            while adj[v]:
                cand = adj[v][-1]
                if used[cand]:
                    adj[v].pop()
                    continue
                gi = cand
                break
            if gi is None:
                out_nodes.append(node_st.pop())
                out_edges.append(edge_st.pop())
            else:
                used[gi] = True
                a, b, _ = medges[gi]
                other = b if a == v else a
                node_st.append(other)
                edge_st.append(gi)
        out_nodes.reverse()
        out_edges.reverse()
        directed = []
        for k in range(1, len(out_nodes)):
            gi = out_edges[k]
            a, _b, geom = medges[gi]
            directed.append((geom, a != out_nodes[k - 1]))
        return directed

    result: list[tuple[LineString, bool]] = []
    cur = start_pt
    remaining = set(range(len(medges)))
    while remaining:
        cand_nodes = set()
        for gi in remaining:
            if used[gi]:
                continue
            cand_nodes.add(medges[gi][0])
            cand_nodes.add(medges[gi][1])
        cand_nodes = {n for n in cand_nodes if any(not used[gi] for gi in adj[n])}
        if not cand_nodes:
            break
        odd = [
            n for n in cand_nodes if sum(1 for gi in adj[n] if not used[gi]) % 2 == 1
        ]
        pool = odd or list(cand_nodes)
        start = min(
            pool,
            key=lambda n: (pt_of[n][0] - cur[0]) ** 2 + (pt_of[n][1] - cur[1]) ** 2,
        )
        trail = walk(start)
        remaining = {gi for gi in range(len(medges)) if not used[gi]}
        if not trail:
            continue
        for geom, rev in trail:
            e = edges[geom]
            if rev:
                result.append((LineString(list(e.coords)[::-1]), True))
            else:
                result.append((e, False))
        last_geom, last_rev = trail[-1]
        lc = list(edges[last_geom].coords)
        cur = lc[0] if last_rev else lc[-1]
    return result


def chain_contiguous_paths(
    ordered: list[tuple[LineString, bool]], tol_px: float = 0.5
) -> list[list[tuple[float, float]]]:
    """Fuse an ordered list of (edge, reversed) into continuous polylines (quickcut fuse_touching):
    consecutive edges whose join is within tol_px become one path, one laser-on. Each edge's
    direction was already resolved by the ordering; it is honoured."""
    return _qc.fuse_touching([list(edge.coords) for edge, _ in ordered], tol_px)


def reconcile_letter_pockets(pieces, px_per_mm, tol_mm=0.6):
    """Make every letter's outline IDENTICAL to the pocket the surrounding
    pieces leave for it. Upstream, a letter's ring and its pocket's ring can
    disagree by up to ~0.5mm over long stretches; unary_union then keeps BOTH
    near-parallel lines and the cutter traces the letter twice (measured:
    ~20% of a ring puzzle's cut length doubled). The pocket -- the area
    within tol of the letter that no other piece covers -- is bounded by the
    neighbours' real edges, so using it as the letter makes the shared edges
    coincide exactly and dedupe. Skipped (letter kept) if the pocket differs
    from the letter by more than a thin tol-wide band."""
    letters = [p for p in pieces if p.get("kind") == "letter"]
    if not letters:
        return pieces
    others = unary_union([p["polygon"] for p in pieces if p.get("kind") != "letter"])
    tol = tol_mm * px_per_mm
    out = []
    for p in pieces:
        if p.get("kind") != "letter":
            out.append(p)
            continue
        g = p["polygon"]
        pocket = g.buffer(tol).difference(others).buffer(0)
        if isinstance(pocket, MultiPolygon):  # drop slivers from other letters' fringes
            pocket = max(pocket.geoms, key=lambda q: q.intersection(g).area)
        band = g.length * tol  # max plausible area of a tol-wide mismatch band
        if pocket.is_empty or g.symmetric_difference(pocket).area > band:
            out.append(p)
        else:
            out.append({**p, "polygon": pocket})
    # Globe land pieces (ring_prototype globe_land) are drop-in pieces like
    # letters, and their coast / borders are carved and re-joined through
    # buffers, so they can disagree with their neighbours by hairlines in the
    # same way -- each one becomes exactly the pocket ALL its neighbours
    # leave (other land, letters, ocean), with the same safety band.
    if any(p.get("kind") == "land" for p in out):
        from shapely.strtree import STRtree

        polys = [p["polygon"] for p in out]
        tree = STRtree(polys)
        for k, p in enumerate(out):
            if p.get("kind") != "land":
                continue
            g = p["polygon"]
            near = [polys[j] for j in tree.query(g.buffer(tol)) if j != k]
            pocket = g.buffer(tol).difference(unary_union(near)).buffer(0) if near else g
            if isinstance(pocket, MultiPolygon):
                pocket = max(pocket.geoms, key=lambda q: q.intersection(g).area)
            if not pocket.is_empty and g.symmetric_difference(pocket).area <= g.length * tol:
                out[k] = {**p, "polygon": pocket}
                polys[k] = pocket
    return out


def _drop_short_dangles(edges, max_len_px, tol_px=0.05):
    """Drop dead-end edges shorter than max_len_px: a hairline spike where
    two near-coincident outlines meet (a seam stepping onto a coast) is a
    0.3-0.4mm spur that separates nothing, and routing into it and back is
    a shuttle in place (ring_lint)."""
    from collections import Counter

    def key(p):
        return (round(p[0] / tol_px), round(p[1] / tol_px))

    while True:
        deg = Counter()
        for e in edges:
            c = list(e.coords)
            if len(c) >= 2 and key(c[0]) != key(c[-1]):
                deg[key(c[0])] += 1
                deg[key(c[-1])] += 1
        keep = []
        for e in edges:
            c = list(e.coords)
            dangling = len(c) >= 2 and (deg[key(c[0])] == 1 or deg[key(c[-1])] == 1)
            if dangling and e.length < max_len_px and key(c[0]) != key(c[-1]):
                continue
            keep.append(e)
        if len(keep) == len(edges):
            return keep
        edges = keep


def emit_cut_gcode_full(
    pieces: list[dict],
    material: dict,
    cfg: PuzzleConfig,
    word: str,
    mode: str = "static",
    feed_override: int | None = None,
    min_segment_mm: float = 0.0,
    power_percent: float | None = None,
    ramp_ms: float = WARMUP_MS,
    max_backtrack_ms: float | None = None,
    bridge_mm: float = 0.5,
) -> str:
    """Full-panel cut emission with edge dedup + containment-aware
    ordering. Shared cell-cell boundaries cut exactly once. Cut order:
    letter perimeters → interior cell-to-cell → panel border last (so
    stock stays attached until the very final cut).

    mode / feed_override / min_segment_mm / power_percent behave as in
    emit_cut_gcode_simple. Diode cold-start fade is handled by RAMP, not
    dwells (GRBL laser mode keeps the beam off while stationary): the first
    `ramp_ms` of every closed loop under-cut while the diode ramped, so
    after the loop closes (laser now hot) we re-trace that same distance.
    ramp length = ramp_ms/1000 * feed_mm_per_s, derived from the
    feedrate. Default 1000ms — conservative (better to overcut than under)."""
    job = cut_job(material, feed_override=feed_override, power_percent=power_percent, mode=mode,
                  ramp_ms=max(0.0, ramp_ms), min_segment_mm=min_segment_mm)
    feed = job.feed_mm_per_min
    lead_in_mm = job.warmup_mm
    # Backtrack budget: keep the laser continuous by re-tracing a connector only
    # when re-cutting it is cheaper than a restart's warmup. Same units as the
    # warmup (ms of travel at this feed); default = ramp_ms, i.e. re-trace up to
    # "1s worth" of already-cut line to avoid a restart, no more. Converted to px
    # for eulerian_order, which routes on image-px edges. None-safe: 0 disables
    # all re-tracing (pure minimal-length cut).
    bt_ms = ramp_ms if max_backtrack_ms is None else max_backtrack_ms
    max_dup_px = max(0.0, bt_ms) / 1000.0 * (feed / 60.0) * cfg.px_per_mm

    bridges_mm = [img_to_machine_mm(x, y, cfg) for p in pieces for x, y in p.get("bridges", ())]
    pieces = reconcile_letter_pockets(pieces, cfg.px_per_mm)
    edges = _drop_short_dangles(extract_unique_edges(pieces), 0.5 * cfg.px_per_mm)
    letter_polys = [p["polygon"] for p in pieces if p["kind"] == "letter"]
    letters, interior, panel = [], [], []
    for e in edges:
        cat = classify_edge(e, letter_polys, cfg)
        if cat == "letter":
            letters.append(e)
        elif cat == "panel":
            panel.append(e)
        else:
            interior.append(e)

    start_pt = (cfg.margin_px, cfg.margin_px)
    # Eulerian routing keeps the laser cutting continuously through grid
    # junctions (fewest laser-on events), which matters when the diode
    # ramps up over the first few mm after every restart. Interior and panel
    # border are fused/ordered separately below so the border can run last.

    # Convert each ordered edge to machine mm (honoring greedy_order's
    # direction), then fuse contiguous edges into single continuous cuts so
    # the laser doesn't lift / re-dwell / re-fire mid-line. tol is in mm.
    def _to_mm(edges):
        return [
            (LineString([img_to_machine_mm(x, y, cfg) for x, y in edge.coords]), rev)
            for edge, rev in edges
        ]

    # Fuse ALL edges into continuous chains, then split by GEOMETRY (not the
    # per-edge panel test, which misses rounded corners): any chain that runs
    # along the panel perimeter is part of the outside profile and must be cut
    # LAST, so the workpiece stays attached to the surrounding stock until the
    # end. Interior chains (letters + cells) are cut first. Nearest-neighbor
    # within each group trims pen-up travel.
    all_edges = (
        eulerian_order(letters, start_pt, max_dup_len=max_dup_px)
        + eulerian_order(interior, start_pt, max_dup_len=max_dup_px)
        + eulerian_order(panel, start_pt, max_dup_len=max_dup_px)
    )
    all_chains = chain_contiguous_paths(_to_mm(all_edges), tol_px=0.1)
    pw = cfg.puzzle_w_px / cfg.px_per_mm
    ph = cfg.puzzle_h_px / cfg.px_per_mm
    eps = 1.0

    ox, oy = cfg.origin_offset_mm

    def _on_perim(x, y):
        x, y = x + ox, y + oy  # back to panel-corner coords
        if getattr(cfg, "panel_shape", "rect") == "disc":
            return abs(math.hypot(x - pw / 2, y - ph / 2) - pw / 2) < eps
        return x < eps or x > pw - eps or y < eps or y > ph - eps

    def _perim_frac(ch):
        on = sum(1 for x, y in ch if _on_perim(x, y))
        return on / len(ch)

    border_chains = [c for c in all_chains if _perim_frac(c) > 0.15]
    inner_chains = [c for c in all_chains if _perim_frac(c) <= 0.15]
    # Order to trim pen-up travel, then fuse any chains left touching end-to-end
    # (the trail splits from the bounded backtrack can put two chains that share
    # a junction next to each other — fuse them so the laser doesn't re-fire at
    # the exact spot it just finished). Fuse WITHIN each group so the border
    # still runs last (never fused into an interior chain).
    inner_chains = _qc.fuse_touching(_qc.nearest_chains(inner_chains, start=(0.0, 0.0)))
    last = inner_chains[-1][-1] if inner_chains else (0.0, 0.0)
    border_chains = _qc.fuse_touching(_qc.nearest_chains(border_chains, start=last))
    chains = inner_chains + border_chains
    if bridges_mm and bridge_mm > 0:
        chains = _qc.bridge_chains(chains, bridges_mm, bridge_mm)
    n_edges = len(all_edges)

    extra = [
        "loose-fit: centerline cuts, laser kerf becomes the clearance",
        "edge dedup: shared cell-cell boundaries cut exactly once",
        "cut order: letters first, interior next, panel border last",
        "contiguous edges fused into single continuous cuts (no per-edge re-fire)",
        "ASSUMES Z already at focal height in your WCS",
    ]
    if lead_in_mm > 0:
        extra.append(
            f"warmup: {ramp_ms:.0f}ms = {lead_in_mm:.1f}mm at F{feed}; each cut "
            "runs fwd half / back to start first (laser at full power on return), "
            "then cuts the whole path at full power"
        )
    extra.append(
        f"backtrack budget: {bt_ms:.0f}ms = {max_dup_px / cfg.px_per_mm:.1f}mm — "
        "re-trace already-cut line to stay continuous only when it's cheaper than "
        "a restart's warmup; longer gaps restart instead (no wholesale double-cut)"
    )
    if min_segment_mm > 0:
        extra.append(f"min segment: {min_segment_mm}mm (shorter chords decimated)")
    if bridges_mm and bridge_mm > 0:
        extra.append(
            f"breakaway bridges: {len(bridges_mm)} x {bridge_mm}mm uncut gaps hold tiny "
            "letter counters in place (snap out by hand)")

    lines = _header(
        f"full puzzle: word={word}, "
        f"{cfg.puzzle_w_px / cfg.px_per_mm:.0f}x{cfg.puzzle_h_px / cfg.px_per_mm:.0f}mm, "
        f"{len(chains)} continuous cut paths ({n_edges} edges after dedup)", job, extra)

    cut_segs = _qc.CutIndex()
    for idx, path_mm in enumerate(chains, start=1):
        coords_mm = collapse_shuttles(decimate(path_mm, min_segment_mm))
        # A backtrack only earns its keep BETWEEN two new cuts: re-cut line at a path's start or
        # end connects to nothing (the laser restarts anyway), so strip it.
        coords_mm = _qc.trim_recut_ends(coords_mm, cut_segs)
        if len(coords_mm) < 2 or LineString(coords_mm).length < 0.1:
            # a hairline stub between two near-coincident outlines: the kerf covers it
            continue
        cut_segs.add(coords_mm)
        closed = len(coords_mm) > 3 and math.dist(coords_mm[0], coords_mm[-1]) < 1e-6
        lines += _qc.cut(_qc.Polyline(coords_mm, closed), job, idx, len(chains), label="path")
    return _qc.render(lines, validate=False)


def emit_raster_gcode(
    img: Image.Image,
    mode: str,
    cfg: PuzzleConfig,
    line_spacing_mm: float,
    engrave_power_pct: int,
    engrave_feed: int,
) -> list[str]:
    """Raster body for the panel (quickcut raster: M4 dynamic, G0 + S0 over white, G1 per run)."""
    return _qc.raster(img, line_spacing_mm, engrave_power_pct, engrave_feed, mode, height_mm=cfg.panel_mm)


def _raster_job(material_id: str) -> "_qc.CutJob":
    return _qc.CutJob(feed_mm_per_min=1000, power_percent=100, passes=1, material_id=material_id, mode="dynamic")


def combined_raster_and_cut(
    raster_lines: list[str],
    cut_block: str,
    material_id: str,
    image_label: str,
    mode: str,
) -> str:
    """One G-code file: raster engrave, then the cut (quickcut.combine: one preamble, one frame,
    one park)."""
    head = _qc.preamble(_raster_job(material_id),
                        title=f"combined puzzle — image={image_label}, mode={mode}, then cut",
                        notes=["ASSUMES Z already at focal height. Same Z used for engrave + cut.",
                               "If your focal height differs (e.g. red vs green spacer on LaserTree),",
                               "run the _raster and _cut files separately and refocus between them."])
    return _qc.combine(head + raster_lines + ["", "; --- cut phase ---"], cut_block) + "\n"


def raster_only_gcode(
    raster_lines: list[str], material_id: str, image_label: str, mode: str
) -> str:
    """Engrave-only G-code. Pair with the _cut file if you want separate runs."""
    head = _qc.preamble(_raster_job(material_id), title=f"raster engrave only — image={image_label}, mode={mode}",
                        notes=["ASSUMES Z already at engraving focal height in your WCS.",
                               "Run this file FIRST, verify, then run the _cut file."])
    return _qc.render(head + raster_lines + [""], validate=False) + "\n"
