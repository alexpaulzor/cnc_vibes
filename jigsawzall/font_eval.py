#!/usr/bin/env python3
"""Evaluate locally-installed fonts for the letter-split puzzle.

A good puzzle font gives every glyph at least one clear FULL-HEIGHT THICK
vertical stroke (a stem) that the layout can bisect with a seam, and is not
so tight/condensed that neighbouring letters can't be spaced a tab apart.
This script scores that property using the exact splitting criterion the
pipeline uses (glyph_origins.glyph_stroke_anchors / auto_glyph_origin),
renders a per-font contact sheet marking the detected stem anchors, and
writes scores.csv + a ranked FONTS.md report.

Standalone / repeatable:  python3 font_eval.py
Outputs:
  build/font_eval/<font>.png   contact sheet with stem anchors marked
  build/font_eval/scores.csv   per-font metrics
  FONTS.md                     ranked report + per-letter table

Only LOCAL installed fonts are used; nothing is fetched from the network.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

LESSON_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LESSON_DIR))

from glyph_origins import (  # noqa: E402
    _clusters,
    _longest_run_span,
    auto_glyph_origin,
    glyph_stroke_anchors,
)

OUT_DIR = LESSON_DIR / "build" / "font_eval"
CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
FONT_PX = 150  # match jigsaw.py's glyph-sheet render size
STEM_FRAC = 0.6  # same default as glyph_stroke_anchors
INK_THRESH = 80  # same threshold jigsaw.py uses on the L mask

# Candidate faces: (label, path, ttc_index, category). Only bold /
# non-decorative sans / slab / grotesque faces, plus mono & serif controls.
# Condensed faces are kept explicitly as "tight" controls.
SUP = "/System/Library/Fonts/Supplemental"
SYS = "/System/Library/Fonts"
CANDIDATES = [
    # --- sans / grotesque (bold) ---
    ("Arial-Bold", f"{SUP}/Arial Bold.ttf", 0, "sans (current)"),
    ("Helvetica-Bold", f"{SYS}/Helvetica.ttc", 1, "sans"),
    ("HelveticaNeue-Bold", f"{SYS}/HelveticaNeue.ttc", 1, "sans"),
    ("Verdana-Bold", f"{SUP}/Verdana Bold.ttf", 0, "sans"),
    ("Tahoma-Bold", f"{SUP}/Tahoma Bold.ttf", 0, "sans"),
    ("TrebuchetMS-Bold", f"{SUP}/Trebuchet MS Bold.ttf", 0, "sans"),
    ("MicrosoftSansSerif", f"{SUP}/Microsoft Sans Serif.ttf", 0, "sans"),
    ("AvenirNext-Bold", f"{SYS}/Avenir Next.ttc", 0, "sans (geometric)"),
    ("ArialBlack", f"{SUP}/Arial Black.ttf", 0, "sans (heavy)"),
    ("DINAlternate-Bold", f"{SUP}/DIN Alternate Bold.ttf", 0, "sans (industrial)"),
    (
        "PTSans-Bold",
        f"{SUP}/PTSans.ttc",
        7,
        "sans",
    ),
    # --- geometric / humanist (control) ---
    ("Futura-Bold", f"{SUP}/Futura.ttc", 2, "geometric"),
    ("GillSans-Bold", f"{SUP}/GillSans.ttc", 1, "humanist"),
    ("Optima-Bold", f"{SYS}/Optima.ttc", 1, "humanist"),
    # --- slab ---
    ("Rockwell-Bold", f"{SUP}/Rockwell.ttc", 2, "slab"),
    ("AmericanTypewriter-Bold", f"{SUP}/AmericanTypewriter.ttc", 2, "slab"),
    ("Superclarendon-Bold", f"{SUP}/SuperClarendon.ttc", 5, "slab"),
    # --- condensed / heavy (tight controls) ---
    ("ArialNarrow-Bold", f"{SUP}/Arial Narrow Bold.ttf", 0, "condensed"),
    ("DINCondensed-Bold", f"{SUP}/DIN Condensed Bold.ttf", 0, "condensed"),
    ("Impact", f"{SUP}/Impact.ttf", 0, "condensed (heavy)"),
    # --- mono controls ---
    ("CourierNew-Bold", f"{SUP}/Courier New Bold.ttf", 0, "mono"),
    ("Menlo-Bold", f"{SYS}/Menlo.ttc", 1, "mono"),
    ("AndaleMono", f"{SUP}/Andale Mono.ttf", 0, "mono"),
    # --- serif controls ---
    ("Georgia-Bold", f"{SUP}/Georgia Bold.ttf", 0, "serif"),
    ("TimesNewRoman-Bold", f"{SUP}/Times New Roman Bold.ttf", 0, "serif"),
]


def load_font(path: str, index: int, size: int):
    p = str(Path(path).resolve())
    if not Path(p).exists():
        return None
    try:
        return ImageFont.truetype(p, size, index=index)
    except (OSError, IOError):
        return None


def render_ink(font, ch: str):
    """Render one glyph to a tight bool ink array (True = ink).

    Mirrors jigsaw.py: font.getbbox -> draw on an L image -> threshold.
    Returns (ink_bool_2d, advance_px) or (None, advance) if the glyph is blank.
    """
    l, t, r, b = font.getbbox(ch)
    gw, gh = r - l, b - t
    advance = font.getlength(ch)
    if gw <= 0 or gh <= 0:
        return None, advance
    pad = 4
    img = Image.new("L", (gw + 2 * pad, gh + 2 * pad), 0)
    ImageDraw.Draw(img).text((pad - l, pad - t), ch, fill=255, font=font)
    ink = np.asarray(img) > INK_THRESH
    return ink, advance


def stem_report(ink):
    """Return (n_stems, thicknesses_frac) for a glyph ink mask, using the
    same full-height-vertical-run detection as glyph_stroke_anchors.

    n_stems counts distinct full-height stem clusters (0 => the anchor code
    falls back to a centroid, i.e. no splittable stem). thicknesses_frac is
    each stem cluster's pixel width as a fraction of the glyph's ink height
    (a proxy for how "thick" / bisectable the stem is)."""
    ys, xs = np.nonzero(ink)
    if len(xs) == 0:
        return 0, []
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    gh = y1 - y0 + 1
    spans = [_longest_run_span(ink[:, c]) for c in range(x0, x1 + 1)]
    runs = [s[0] for s in spans]
    stem = [r >= STEM_FRAC * gh for r in runs]
    clusters = _clusters(stem)
    thick = [(b - a + 1) / gh for a, b in clusters]
    return len(clusters), thick


def eval_font(label, path, index):
    font = load_font(path, index, FONT_PX)
    if font is None:
        return None
    # cap height reference for normalizing spacing
    cap_ink, _ = render_ink(font, "H")
    cap_h = 0
    if cap_ink is not None:
        ys = np.nonzero(cap_ink)[0]
        cap_h = int(ys.max() - ys.min() + 1)
    cap_h = cap_h or FONT_PX

    per_letter = {}
    n_with_stem = 0
    total_stems = 0
    thicks = []
    spacings = []
    for ch in CHARS:
        ink, advance = render_ink(font, ch)
        if ink is None:
            per_letter[ch] = {"stems": 0, "thick": 0.0, "anchors": []}
            continue
        n_stems, thick = stem_report(ink)
        anchors = glyph_stroke_anchors(ink)
        auto = auto_glyph_origin(ink)
        ys, xs = np.nonzero(ink)
        ink_w = int(xs.max() - xs.min() + 1)
        side_space = max(0.0, advance - ink_w)  # advance beyond the ink
        spacing_ratio = side_space / cap_h
        spacings.append(spacing_ratio)
        if n_stems >= 1:
            n_with_stem += 1
        total_stems += n_stems
        if thick:
            thicks.append(max(thick))  # dominant stem thickness
        per_letter[ch] = {
            "stems": n_stems,
            "thick": round(max(thick), 3) if thick else 0.0,
            "anchors": anchors,
            "auto": auto,
        }

    n = len(CHARS)
    coverage = n_with_stem / n
    avg_anchors = total_stems / n
    avg_thick = float(np.mean(thicks)) if thicks else 0.0
    avg_spacing = float(np.mean(spacings)) if spacings else 0.0
    return {
        "label": label,
        "font": font,
        "coverage": coverage,
        "avg_anchors": avg_anchors,
        "avg_thick": avg_thick,
        "avg_spacing": avg_spacing,
        "per_letter": per_letter,
    }


def composite_score(r):
    """Higher = better puzzle font.
    Coverage dominates (every glyph must have a stem to bisect); looser
    spacing and more/anchors help; extreme thickness is fine but not scored
    up unboundedly."""
    spacing_norm = min(r["avg_spacing"] / 0.30, 1.0)  # 0.30*capH ~ generous
    anchor_norm = min(r["avg_anchors"] / 2.0, 1.0)
    return round(0.55 * r["coverage"] + 0.25 * spacing_norm + 0.20 * anchor_norm, 4)


def contact_sheet(r, out_path):
    """Grid of glyphs with each detected stem anchor drawn as a red vertical
    seam line + dot (green dot = auto_glyph_origin chosen origin)."""
    font = r["font"]
    cols = 6
    cell = 200
    label_h = 26
    rows = (len(CHARS) + cols - 1) // cols
    W, H = cols * cell, rows * cell
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    small = load_font(f"{SUP}/Arial Bold.ttf", 0, 16) or ImageFont.load_default()

    for i, ch in enumerate(CHARS):
        cx, cy = (i % cols) * cell, (i // cols) * cell
        d.rectangle([cx, cy, cx + cell - 1, cy + cell - 1], outline=(220, 220, 220))
        l, t, br, bb = font.getbbox(ch)
        gw, gh = br - l, bb - t
        if gw <= 0 or gh <= 0:
            d.text((cx + 4, cy + 4), ch + " (blank)", fill=(200, 0, 0), font=small)
            continue
        area_h = cell - label_h
        ox = cx + (cell - gw) // 2 - l
        oy = cy + (area_h - gh) // 2 - t
        # ink bbox in image space
        ibl, ibt, ibr, ibb = ox + l, oy + t, ox + br, oy + bb
        d.text((ox, oy), ch, fill=(70, 70, 70), font=font)

        pl = r["per_letter"][ch]
        for nx, ny in pl["anchors"]:
            ax = ibl + nx * (ibr - ibl)
            ay = ibt + ny * (ibb - ibt)
            col = (0, 120, 220) if pl["stems"] >= 1 else (230, 140, 0)
            d.line([ax, ibt, ax, ibb], fill=col, width=1)
            d.ellipse([ax - 4, ay - 4, ax + 4, ay + 4], outline=col, width=2)
        aux = pl.get("auto")
        if aux:
            gx = ibl + aux[0] * (ibr - ibl)
            gy = ibt + aux[1] * (ibb - ibt)
            d.ellipse([gx - 3, gy - 3, gx + 3, gy + 3], fill=(0, 170, 0))

        tag = f"{ch}: {pl['stems']} stem"
        color = (0, 130, 0) if pl["stems"] >= 1 else (200, 0, 0)
        d.text((cx + 4, cy + cell - label_h + 4), tag, fill=color, font=small)

    img.save(out_path)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    missing = []
    for label, path, index, cat in CANDIDATES:
        r = eval_font(label, path, index)
        if r is None:
            missing.append((label, path))
            continue
        r["category"] = cat
        r["score"] = composite_score(r)
        results.append(r)
        contact_sheet(r, OUT_DIR / f"{label}.png")
        print(
            f"{label:26s} score={r['score']:.3f} cov={r['coverage']:.2f} "
            f"anch={r['avg_anchors']:.2f} space={r['avg_spacing']:.3f}"
        )

    results.sort(key=lambda r: r["score"], reverse=True)

    # ---- scores.csv ----
    with open(OUT_DIR / "scores.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "rank",
                "font",
                "category",
                "score",
                "stem_coverage",
                "avg_stems_per_glyph",
                "avg_stem_thickness_frac",
                "avg_side_space_frac",
                "problem_letters",
            ]
        )
        for rank, r in enumerate(results, 1):
            probs = [c for c in CHARS if r["per_letter"][c]["stems"] == 0]
            w.writerow(
                [
                    rank,
                    r["label"],
                    r["category"],
                    f"{r['score']:.4f}",
                    f"{r['coverage']:.3f}",
                    f"{r['avg_anchors']:.3f}",
                    f"{r['avg_thick']:.3f}",
                    f"{r['avg_spacing']:.3f}",
                    "".join(probs),
                ]
            )

    write_report(results, missing)
    print(f"\nWrote {OUT_DIR}/scores.csv, FONTS.md, and {len(results)} contact sheets.")
    if missing:
        print("Missing on disk:", ", ".join(m[0] for m in missing))


def write_report(results, missing):
    lines = []
    lines.append("# Font evaluation for the letter-split puzzle\n")
    lines.append(
        "Generated by `font_eval.py`. Fonts are scored on how well they suit "
        "the puzzle's letter-splitting rule: every glyph should expose at "
        "least one **full-height thick vertical stroke (stem)** that a seam "
        "can bisect (detected by `glyph_origins.glyph_stroke_anchors`), and "
        "the face should be **loose enough** that neighbouring letters can be "
        "spaced a tab apart (side-bearing space vs. cap height). Only "
        "locally-installed fonts were tested; nothing was fetched online.\n"
    )
    lines.append("## Metrics\n")
    lines.append(
        "- **stem_coverage** - fraction of A-Z0-9 with >=1 splittable "
        "full-height stem (higher = better; 1.00 ideal).\n"
        "- **avg_stems** - mean full-height stems per glyph (more = more "
        "choices of where to place a seam).\n"
        "- **stem_thick** - dominant stem width as a fraction of glyph height "
        "(thicker = a more forgiving bisection target).\n"
        "- **side_space** - mean advance-minus-ink width as a fraction of cap "
        "height (higher = looser = easier to keep a tab between letters; "
        "condensed faces score low here).\n"
        "- **score** = 0.55*coverage + 0.25*min(side_space/0.30,1) + "
        "0.20*min(avg_stems/2,1).\n"
    )

    lines.append("## Ranking\n")
    lines.append(
        "| Rank | Font | Category | Score | stem_cov | avg_stems | "
        "stem_thick | side_space | problem letters |"
    )
    lines.append("|---:|---|---|---:|---:|---:|---:|---:|---|")
    for rank, r in enumerate(results, 1):
        probs = "".join(c for c in CHARS if r["per_letter"][c]["stems"] == 0)
        lines.append(
            f"| {rank} | {r['label']} | {r['category']} | {r['score']:.3f} | "
            f"{r['coverage']:.2f} | {r['avg_anchors']:.2f} | "
            f"{r['avg_thick']:.2f} | {r['avg_spacing']:.3f} | {probs or '-'} |"
        )

    # recommendation
    non_control = [
        r
        for r in results
        if r["category"] not in ("mono", "serif") and "condensed" not in r["category"]
    ]
    top = non_control[:2] if non_control else results[:2]
    arial = next((r for r in results if r["label"] == "Arial-Bold"), None)
    lines.append("\n## Recommendation\n")
    if top:
        best = top[0]
        lines.append(
            f"**Top pick: {best['label']}** ({best['category']}), score "
            f"{best['score']:.3f}, stem coverage {best['coverage']:.2f}, "
            f"side-space {best['avg_spacing']:.3f}.\n"
        )
        if len(top) > 1:
            second = top[1]
            lines.append(
                f"**Runner-up: {second['label']}** ({second['category']}), "
                f"score {second['score']:.3f}, coverage "
                f"{second['coverage']:.2f}, side-space "
                f"{second['avg_spacing']:.3f}.\n"
            )
    if arial:
        arank = results.index(arial) + 1
        lines.append(
            f"\nCurrent font **Arial-Bold** ranks #{arank} with score "
            f"{arial['score']:.3f} (coverage {arial['coverage']:.2f}, "
            f"avg_stems {arial['avg_anchors']:.2f}, side-space "
            f"{arial['avg_spacing']:.3f}).\n"
        )

    # per-letter table: which fonts split each letter well (>=1 stem)
    lines.append("\n## Per-letter split quality\n")
    lines.append(
        "For each glyph, the count of tested fonts that expose a splittable "
        "full-height stem, the **best** font for that glyph (most stems, then "
        "thickest), and its stem thickness. Use this to mix fonts per glyph "
        "later - pick the best-splitting font per letter.\n"
    )
    lines.append("| Glyph | #fonts w/ stem | best font | best #stems | best thick |")
    lines.append("|:---:|---:|---|---:|---:|")
    hard_all = []
    for ch in CHARS:
        good = [r for r in results if r["per_letter"][ch]["stems"] >= 1]
        n_good = len(good)
        if n_good == 0:
            hard_all.append(ch)
            lines.append(f"| {ch} | 0 | - (none) | 0 | 0.00 |")
            continue
        best = max(
            good,
            key=lambda r: (r["per_letter"][ch]["stems"], r["per_letter"][ch]["thick"]),
        )
        pl = best["per_letter"][ch]
        lines.append(
            f"| {ch} | {n_good} | {best['label']} | {pl['stems']} | {pl['thick']:.2f} |"
        )

    lines.append("\n### Letters that split poorly in ALL fonts\n")
    if hard_all:
        lines.append(
            "These have **no** full-height stem in any tested font (pure "
            "diagonals / curves - the anchor code falls back to the ink "
            "centroid, so the seam has no stem to hide in): **"
            + ", ".join(hard_all)
            + "**. Consider special handling (e.g. "
            "seam through the diagonal, or a per-letter font swap to a slab "
            "face whose serifs add near-vertical mass).\n"
        )
    else:
        lines.append("None - every glyph has a stem in at least one font.\n")

    # letters that are hard in the majority of fonts (< half)
    weak = []
    for ch in CHARS:
        n_good = sum(1 for r in results if r["per_letter"][ch]["stems"] >= 1)
        if 0 < n_good <= len(results) // 3:
            weak.append((ch, n_good))
    if weak:
        lines.append("\n### Letters that split well in only a few fonts\n")
        lines.append(
            ", ".join(f"{c} ({n})" for c, n in weak)
            + "  (number = fonts with a stem).\n"
        )

    if missing:
        lines.append("\n## Candidates not found on disk\n")
        for label, path in missing:
            lines.append(f"- {label} (`{path}`)")

    lines.append("\n## Research needed (not installed, not fetched)\n")
    lines.append(
        "Fonts that would likely help the puzzle but are not installed "
        "locally (listed only - not downloaded):\n"
        "- **DejaVu Sans Bold** - the pipeline's Linux fallback; very even, "
        "thick stems, generous spacing; good cross-platform baseline.\n"
        "- **Roboto Bold / Roboto Slab Bold** - open, wide, thick uniform "
        "stems; slab variant adds vertical mass to A/V/W/X/Y.\n"
        "- **Source Sans / IBM Plex Sans Bold** - open apertures, clean "
        "stems, comfortable spacing.\n"
        "- **Archivo / Libre Franklin Bold** - grotesques with strong even "
        "stems and loose default tracking.\n"
    )

    (LESSON_DIR / "FONTS.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
