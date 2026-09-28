#!/usr/bin/env python3
"""Automated checks + annotated renders for scripts/ring_prototype.py output.

Two independent checkers, run before a file is ever shown as a candidate:

  lint_gcode(gcode, cfg)   -- parses the emitted G-code paths and flags every
                              known failure mode found on real cuts so far:
                              reversed/shuttled micro-moves, sub-tolerance
                              segments, aliasing (jittery zigzag) runs, out-
                              of-bounds coordinates, and the rim/frame not
                              being the final cut.
  lint_pieces(pieces, cfg) -- flags geometry-level defects on the piece set
                              itself: oversized pieces, thin bridges,
                              slivers, border nubs, and seams that couldn't
                              place a tab and merged two pieces instead.

annotate_flaws(...) draws both sets of findings onto copies of the piece
render and the G-code toolpath render, so a candidate can be screened
visually in one look. overlay_grid_mm(...) adds a faint 1cm reference grid
to a piece render (never the G-code toolpath, which is already in mm and
meant to be read at 1:1).

Extend lint_gcode/lint_pieces whenever a new defect is reported on a real
cut -- that is the whole point of this file: a defect spotted once should
never need spotting by eye again.
"""

import math
import re

from shapely.geometry import Polygon
from shapely.ops import unary_union
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw


# ---------------------------------------------------------------------------
# G-code checks
# ---------------------------------------------------------------------------


@dataclass
class GcodeFinding:
    kind: str  # "reversal" | "shuttle" | "short_segment" | "aliasing" |
    #             "out_of_bounds" | "rim_not_last"
    severity: str  # "defect" | "info"
    path_idx: int
    point: tuple[float, float]  # mm, machine coords
    detail: str


@dataclass
class GcodeReport:
    findings: list = field(default_factory=list)
    n_paths: int = 0
    n_moves: int = 0
    bounds: tuple = (0, 0, 0, 0)

    @property
    def defects(self):
        return [f for f in self.findings if f.severity == "defect"]

    def summary(self) -> str:
        if not self.defects:
            return f"clean: {self.n_paths} laser-on paths, {self.n_moves} moves, no defects found"
        from collections import Counter

        c = Counter(f.kind for f in self.defects)
        return f"{len(self.defects)} defect(s): " + ", ".join(
            f"{v} {k}" for k, v in c.items()
        )


def _parse_paths(gcode: str):
    paths = []
    x = y = 0.0
    on = False
    cur = None
    for line in gcode.splitlines():
        if line.startswith("M3"):
            on = True
            cur = [(x, y)]
            paths.append(cur)
        elif line.startswith("M5"):
            on = False
        m = dict(re.findall(r"([XY])(-?[\d.]+)", line))
        if m:
            x = float(m.get("X", x))
            y = float(m.get("Y", y))
            if on and line.startswith("G1"):
                cur.append((x, y))
    return paths


def lint_gcode(
    gcode: str,
    diameter_mm: float | None = None,
    panel_mm: tuple[float, float] | None = None,
    reversal_leg_mm: float = 1.0,
    short_segment_mm: float = 0.25,
    alias_run_len: int = 4,
    alias_angle_deg: float = 20.0,
    alias_seg_mm: float = 1.2,
) -> GcodeReport:
    """Parse emitted G-code and flag every defect class seen on a real cut:

    - reversal: a >90deg direction change with either leg shorter than
      `reversal_leg_mm` (the wobble/flicker pattern -- a near-180 turn on a
      short leg means the head is nearly retracing itself).
    - shuttle: >=3 reversals in a row at the same point (the "stuck in
      place" pattern -- warmup repeating full out-and-back trips over a
      sub-mm stub).
    - short_segment: a G1 chord shorter than `short_segment_mm` outside a
      reversal/shuttle (decimate() should already prevent these; a surviving
      cluster means something upstream is emitting undecimated points).
    - aliasing: a run of >=`alias_run_len` consecutive short segments
      (<=`alias_seg_mm`) whose turning angle alternates sign by more than
      `alias_angle_deg` -- the zigzag signature of a curve's points getting
      knocked off-curve (e.g. by too-coarse precision snapping).
    - out_of_bounds: a coordinate outside [0, panel_mm] on either axis.
    - rim_not_last: for a disc/framed ring (diameter_mm given), some other
      path draws material after the rim/frame's own final cut -- the puzzle
      would separate from the stock before the job finishes.
    """
    paths = _parse_paths(gcode)
    rep = GcodeReport(n_paths=len(paths))
    xs, ys = [], []

    C = (diameter_mm / 2, diameter_mm / 2) if diameter_mm else None
    r_out = diameter_mm / 2 if diameter_mm else None
    rim_last_seen_at = None

    for pi, pts in enumerate(paths):
        rep.n_moves += max(0, len(pts) - 1)
        xs += [p[0] for p in pts]
        ys += [p[1] for p in pts]

        # The warmup wiggle is an INTENTIONAL near-180deg out-and-back: by
        # construction (motion.warmup_wiggle) it ends exactly back at the
        # path's own start point before the real cut begins. Detect that
        # return-to-start (not a fixed point count, since a short seam's
        # warmup differs from a long one's) and exclude it from the
        # reversal/shuttle/aliasing scan below -- otherwise every path's
        # warmup false-positives as the exact defect pattern it's designed
        # to prevent.
        warmup_end = 0
        for i in range(1, min(len(pts), 60)):
            if math.hypot(pts[i][0] - pts[0][0], pts[i][1] - pts[0][1]) < 0.01:
                warmup_end = i
        # The real cut's own first stretch necessarily RE-TREADS the same
        # ground the warmup already sampled (that's the whole point: the cold
        # start gets re-cut hot) -- so points immediately after warmup_end
        # keep echoing earlier warmup points, which looks like more
        # reversals if not also skipped. Extend past every point that's a
        # near-exact repeat of something already seen, stopping at the first
        # genuinely new point.
        if warmup_end:
            seen = pts[: warmup_end + 1]
            k = warmup_end + 1
            while k < min(len(pts), warmup_end + 60) and any(
                math.hypot(pts[k][0] - s[0], pts[k][1] - s[1]) < 0.01 for s in seen
            ):
                warmup_end = k
                k += 1

        if C is not None:
            on_rim = sum(
                1
                for a, b in zip(pts, pts[1:])
                if abs(math.hypot(a[0] - C[0], a[1] - C[1]) - r_out) < 0.6
                and abs(math.hypot(b[0] - C[0], b[1] - C[1]) - r_out) < 0.6
            )
            if on_rim:
                rim_last_seen_at = pi

        run = 0
        for k in range(max(1, warmup_end), len(pts) - 1):
            a, b, c = pts[k - 1], pts[k], pts[k + 1]
            v1 = (b[0] - a[0], b[1] - a[1])
            v2 = (c[0] - b[0], c[1] - b[1])
            l1, l2 = math.hypot(*v1), math.hypot(*v2)
            if l1 < 1e-9 or l2 < 1e-9:
                continue
            cos = (v1[0] * v2[0] + v1[1] * v2[1]) / (l1 * l2)
            cos = max(-1.0, min(1.0, cos))
            ang = math.degrees(math.acos(cos))
            is_reversal = cos < -0.5 and min(l1, l2) < reversal_leg_mm
            if is_reversal:
                run += 1
                if run == 3:
                    rep.findings.append(
                        GcodeFinding(
                            "shuttle",
                            "defect",
                            pi,
                            b,
                            f"{run}+ back-and-forth moves in place at this point "
                            "-- the flicker/stall pattern",
                        )
                    )
                elif run < 3:
                    # A LONE reversal (not part of a run) is usually the router's
                    # Chinese-Postman connector retrace (ALGORITHMS.md A6): the
                    # path deliberately turns around once to keep the laser
                    # continuous across an odd-degree junction, then continues
                    # smoothly in the new direction for many points. That's
                    # working as designed, not a flicker -- only a RUN of
                    # reversals (the shuttle case above) is the real defect.
                    # Still logged, at "info" severity, since a cluster of
                    # "info" reversals close together is itself worth a look.
                    rep.findings.append(
                        GcodeFinding(
                            "reversal",
                            "info",
                            pi,
                            b,
                            f"near-180deg turn, legs {l1:.2f}/{l2:.2f}mm "
                            "(likely a normal connector-retrace turnaround)",
                        )
                    )
            else:
                run = 0
            if l1 < short_segment_mm and not is_reversal:
                rep.findings.append(
                    GcodeFinding(
                        "short_segment", "defect", pi, a, f"{l1:.3f}mm chord"
                    )
                )

        # aliasing: sliding window over short segments with alternating turns
        segs = [
            (pts[k], pts[k + 1], math.hypot(pts[k + 1][0] - pts[k][0], pts[k + 1][1] - pts[k][1]))
            for k in range(warmup_end, len(pts) - 1)
        ]
        zz = 0
        last_sign = 0
        for k in range(1, len(segs) - 1):
            (a0, a1, la), (b0, b1, lb) = segs[k - 1], segs[k]
            if la > alias_seg_mm or lb > alias_seg_mm or la < 1e-6 or lb < 1e-6:
                zz = 0
                continue
            v1 = (a1[0] - a0[0], a1[1] - a0[1])
            v2 = (b1[0] - b0[0], b1[1] - b0[1])
            cross = v1[0] * v2[1] - v1[1] * v2[0]
            dot = v1[0] * v2[0] + v1[1] * v2[1]
            ang = math.degrees(math.atan2(abs(cross), dot))
            sign = 1 if cross > 0 else -1
            if ang > alias_angle_deg and sign != last_sign and last_sign != 0:
                zz += 1
                if zz == alias_run_len:
                    rep.findings.append(
                        GcodeFinding(
                            "aliasing",
                            "defect",
                            pi,
                            b0,
                            f"{zz}+ alternating short-segment turns near here",
                        )
                    )
            else:
                zz = 0
            last_sign = sign

    if xs:
        rep.bounds = (min(xs), min(ys), max(xs), max(ys))
        if panel_mm:
            pw, ph = panel_mm
            for pi, pts in enumerate(paths):
                for x, y in pts:
                    if x < -0.05 or y < -0.05 or x > pw + 0.05 or y > ph + 0.05:
                        rep.findings.append(
                            GcodeFinding(
                                "out_of_bounds",
                                "defect",
                                pi,
                                (x, y),
                                f"outside 0..{pw}x0..{ph} panel",
                            )
                        )
                        break

    if C is not None and rim_last_seen_at is not None and rim_last_seen_at != len(paths) - 1:
        rep.findings.append(
            GcodeFinding(
                "rim_not_last",
                "defect",
                rim_last_seen_at,
                paths[rim_last_seen_at][0],
                f"rim/frame cut at path {rim_last_seen_at + 1}/{len(paths)}, "
                f"but {len(paths) - 1 - rim_last_seen_at} more path(s) follow -- "
                "stock may separate before the job finishes",
            )
        )

    return rep


# ---------------------------------------------------------------------------
# Piece-geometry checks
# ---------------------------------------------------------------------------


@dataclass
class PieceFinding:
    kind: str  # "oversized" | "thin_bridge" | "sliver" | "border_nub" | "no_tab"
    serial: int | None
    centroid: tuple[float, float]
    detail: str


# Minimum tab hardware proven to survive an actual cut. tab_stem_px=30/
# tab_r_px=15 (6mm neck / 3mm bulb radius at 5px/mm) is the name-plate
# default and has held up on every physical cut so far, including this
# tool's own JONATHAN ring. tab_stem_px=22/tab_r_px=11 (4.4mm neck) is the
# smallest size ALSO confirmed on a real small-disc cut (the 142mm 4-up
# ring). Anything narrower than that has never been cut and must not be
# presented as a candidate -- this is exactly the regression that slipped
# through once already: chasing a lower dropped-tab count by shrinking the
# tab hardware itself, producing pieces that looked fine on screen but had
# a neck too narrow to survive being picked up.
MIN_PROVEN_TAB_STEM_PX = 22.0
MIN_PROVEN_TAB_R_PX = 11.0


def lint_tab_hardware(cfg, min_class: float = 1.0) -> list:
    """Check the config's tab hardware against MIN_PROVEN_TAB_*. Call this on
    every candidate BEFORE it's shown -- unlike lint_pieces, this doesn't need
    a generated puzzle, just the cfg, so it can gate a whole tuning sweep.

    `min_class`: when distinct-tab size classes are in play (RingParams.
    distinct_tabs, ring_prototype.TAB_SIZE_CLASSES), pass the SMALLEST class
    multiplier here (e.g. 0.85) so the check validates the smallest tab that
    will actually get cut, not just the cfg's base/default size."""
    findings = []
    base_stem = cfg.tab_stem_w_px if cfg.tab_stem_w_px is not None else cfg.tab_circle_r_px
    stem = base_stem * min_class
    r = cfg.tab_circle_r_px * min_class
    if stem < MIN_PROVEN_TAB_STEM_PX:
        findings.append(
            PieceFinding(
                "narrow_tab_neck",
                None,
                (0, 0),
                f"tab neck {stem / cfg.px_per_mm:.1f}mm < proven-safe minimum "
                f"{MIN_PROVEN_TAB_STEM_PX / cfg.px_per_mm:.1f}mm -- will likely "
                "snap off before or during assembly",
            )
        )
    if r < MIN_PROVEN_TAB_R_PX:
        findings.append(
            PieceFinding(
                "narrow_tab_bulb",
                None,
                (0, 0),
                f"tab bulb radius {r / cfg.px_per_mm:.1f}mm < "
                f"proven-safe minimum {MIN_PROVEN_TAB_R_PX / cfg.px_per_mm:.1f}mm",
            )
        )
    return findings


# A merged piece can be well under the absolute oversized-area cutoff and
# still look wrong: long and thin, reaching from one background band into
# the next (found by Alex on a real render -- the "elongated" piece between
# the middle band and the outer ring, aspect 2.87). oversized_oriented()'s
# area+side thresholds didn't catch it. Flag by SHAPE (min-rotated-rect
# long/short side ratio) as a second, independent signal.
MAX_PIECE_ASPECT = 2.5


def _aspect_ratio(poly) -> float:
    import math
    import warnings as _w

    with _w.catch_warnings():
        _w.simplefilter("ignore")
        mrr = poly.minimum_rotated_rectangle
    xs, ys = mrr.exterior.coords.xy
    e = sorted(math.hypot(xs[i + 1] - xs[i], ys[i + 1] - ys[i]) for i in range(2))
    return e[1] / max(e[0], 1e-6)


def lint_pieces(pieces, cfg, panel, seams=None, center_medallion="_unset") -> list:
    """Geometry-level defects: reuses ring_prototype's own scoring predicates
    (oversized/thin/sliver/nub) so this can never silently drift from what
    the generator itself optimizes against, plus reports every seam that
    dropped its tab (from `seams`, if the caller has them -- these merge two
    pieces into one and are the main source of "why is this piece so big").
    Always also runs lint_tab_hardware(cfg) -- a low dropped-tab count is
    worthless (actively dangerous) if it was bought by shrinking the tabs
    themselves below what's been proven to survive a real cut.

    `center_medallion`: pass `st["center_medallion"]` (from generate()'s
    stats dict) when checking a center_text ring, so oversized_oriented()'s
    hub-medallion exemption applies here too -- it's process-global state
    set only during generation, which is lost if `pieces` came from a
    pickled tuple loaded in a fresh process (the normal workflow for this
    tool). Default sentinel leaves the module's current value alone."""
    import ring_prototype as R

    if center_medallion != "_unset":
        orig_medallion = R._center_medallion
        R._center_medallion = center_medallion
    else:
        orig_medallion = None

    findings = list(lint_tab_hardware(cfg))

    # Letter COUNTERS (O's centre, A's triangle hole, ...) are small, isolated
    # drop-in pieces by design (R4/A4 in ALGORITHMS.md) -- exempt from
    # sliver/thin-bridge/nub, which assume "small" means a badly-tiled
    # background piece. A counter is a "cell" fragment sitting inside a
    # letter's own silhouette (its exterior ring, holes filled back in).
    letters_solid = unary_union(
        [Polygon(p["polygon"].exterior) for p in pieces if p["kind"] == "letter"]
    )

    for p in pieces:
        if p["kind"] != "cell":
            continue
        poly = p["polygon"]
        cen = poly.representative_point()
        is_counter = not letters_solid.is_empty and letters_solid.contains(cen)
        cen = (cen.x, cen.y)
        if R.oversized_oriented(poly, cfg):
            findings.append(PieceFinding("oversized", p["serial"], cen, "exceeds the target piece size"))
        if is_counter:
            continue
        if R.G._vg_thin_bridge(poly, cfg):
            findings.append(PieceFinding("thin_bridge", p["serial"], cen, "material bridge under ~4mm somewhere on this piece"))
        if R.G._vg_sliver(poly, cfg):
            findings.append(PieceFinding("sliver", p["serial"], cen, "too small or too narrow everywhere"))
        if R.G._vg_border_nub(poly, panel, cfg):
            findings.append(PieceFinding("border_nub", p["serial"], cen, "touches the outer edge for <20mm"))
        if not is_counter:
            asp = _aspect_ratio(poly)
            if asp > MAX_PIECE_ASPECT:
                findings.append(
                    PieceFinding(
                        "elongated",
                        p["serial"],
                        cen,
                        f"{asp:.1f}x longer than wide -- reads as reaching out of "
                        "its own ring/band into a neighbour's territory, even "
                        "though it's under the absolute oversized-area cutoff",
                    )
                )
    if seams:
        for s in seams:
            if s.get("status") == "drop":
                pts = s["pts"]
                mid = pts[len(pts) // 2]
                findings.append(
                    PieceFinding("no_tab", None, tuple(mid), f"{s['kind']} seam: no tab fit, pieces merged")
                )
            elif s.get("status") == "plain" and "B" in s.get("ends", ()):
                # A "plain" seam is a COMPLETE cut with no tab -- unlike "drop"
                # (merged, still one piece), this one fully separates the two
                # pieces with nothing gripping them together. Fine on an
                # INTERIOR seam (the piece still has tabs on its other edges),
                # but on the true outer panel boundary it means an outermost
                # piece can have an edge that just abuts its neighbour with no
                # interlock at all -- exactly what came loose/jumbled on a
                # real cut. Interior-only "plain" (frame-facing, ends has "J"
                # instead of "B") is unaffected.
                pts = s["pts"]
                mid = pts[len(pts) // 2]
                findings.append(
                    PieceFinding(
                        "no_interlock",
                        None,
                        tuple(mid),
                        f"{s['kind']} seam: fully cut but untabbed on the outer "
                        "edge -- this piece has no grip on that side",
                    )
                )
    if center_medallion != "_unset":
        R._center_medallion = orig_medallion
    return findings


# ---------------------------------------------------------------------------
# Rendering: grid overlay + flaw annotation
# ---------------------------------------------------------------------------


def _spreadsheet_col(i: int) -> str:
    """0 -> 'A', 25 -> 'Z', 26 -> 'AA', ... (Battleship-style column letters,
    extended past Z the way spreadsheet columns are)."""
    s = ""
    i += 1
    while i > 0:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def overlay_grid_mm(png_path, cfg, step_mm: float = 10.0, color=(0, 0, 0), alpha: int = 22):
    """Draw a faint reference grid (default 1cm) over an existing PNG, aligned
    to the panel's own mm coordinates (origin at cfg.margin_px), labeled
    Battleship-style: letters across the top (A, B, C... one per column),
    numbers down the left (1, 2, 3...) -- so a spot can be named ("C10")
    instead of marked up on the image. Piece renders only -- never call this
    on a G-code toolpath render, which is already plotted in real mm and
    meant to be read at 1:1."""
    img = Image.open(png_path).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    step_px = step_mm * cfg.px_per_mm
    m = cfg.margin_px
    w, h = img.size

    label_font = None
    try:
        from PIL import ImageFont

        label_font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13
        )
    except Exception:
        pass
    label_color = (*color, min(255, alpha * 6))

    x, col = m, 0
    while x <= w:
        d.line([(x, 0), (x, h)], fill=(*color, alpha), width=1)
        if label_font and x + 2 < w:
            d.text((x + 2, 2), _spreadsheet_col(col), fill=label_color, font=label_font)
        x += step_px
        col += 1
    y, row = m, 1
    while y <= h:
        d.line([(0, y), (w, y)], fill=(*color, alpha), width=1)
        if label_font and y + 2 < h:
            d.text((2, y + 2), str(row), fill=label_color, font=label_font)
        y += step_px
        row += 1

    out = Image.alpha_composite(img, overlay).convert("RGB")
    out.save(png_path)


def annotate_flaws(pieces_png, cfg, piece_findings, gcode_findings=None, out_path=None):
    """Draw every PieceFinding onto a copy of the piece render: a hatched
    orange outline + label for each flawed piece, a red X + label for each
    dropped (merged) seam. Returns the output path."""
    out_path = Path(out_path or str(pieces_png).replace(".png", "_flaws.png"))
    img = Image.open(pieces_png).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    colors = {
        "oversized": (255, 120, 0),
        "thin_bridge": (255, 0, 120),
        "sliver": (255, 200, 0),
        "border_nub": (255, 0, 200),
        "no_tab": (220, 0, 0),
    }
    label_font = None
    try:
        from PIL import ImageFont

        label_font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22
        )
    except Exception:
        pass

    legend_lines = []
    seen_kinds = set()
    for f in piece_findings:
        c = colors.get(f.kind, (200, 0, 0))
        seen_kinds.add(f.kind)
        cx, cy = f.centroid
        r = 55
        if f.kind == "no_tab":
            d.line([(cx - r, cy - r), (cx + r, cy + r)], fill=(*c, 230), width=6)
            d.line([(cx - r, cy + r), (cx + r, cy - r)], fill=(*c, 230), width=6)
        else:
            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(*c, 230), width=6)
        if label_font:
            d.text((cx + r + 6, cy - 12), f.kind.upper(), fill=(*c, 255), font=label_font)

    KIND_LABEL = {
        "oversized": "oversized piece",
        "thin_bridge": "thin material bridge",
        "sliver": "sliver / too small",
        "border_nub": "border nub",
        "no_tab": "no tab fit -- pieces merged",
    }
    for k in sorted(seen_kinds):
        legend_lines.append((colors.get(k, (200, 0, 0)), KIND_LABEL.get(k, k)))

    if gcode_findings:
        from collections import Counter

        c = Counter(f.kind for f in gcode_findings if f.severity == "defect")
        for k, v in c.items():
            legend_lines.append(((255, 0, 0), f"gcode: {v}x {k}"))

    ly = img.height - 24 - 26 * (len(legend_lines) + 1)
    if label_font:
        d.rectangle([16, ly - 10, 480, img.height - 16], fill=(255, 255, 255, 210))
        d.text((26, ly), "FLAWS (this render only -- not the final)", fill=(0, 0, 0, 255), font=label_font)
        for i, (col, text) in enumerate(legend_lines):
            yy = ly + 30 + i * 26
            d.rectangle([26, yy + 4, 44, yy + 20], fill=(*col, 255))
            d.text((52, yy), text, fill=(0, 0, 0, 255), font=label_font)
    if not legend_lines and label_font:
        d.rectangle([16, img.height - 60, 460, img.height - 16], fill=(230, 255, 230, 220))
        d.text((26, img.height - 50), "No flaws found by the automated checks", fill=(0, 90, 0, 255), font=label_font)

    out = Image.alpha_composite(img, overlay).convert("RGB")
    out.save(out_path)
    return out_path
