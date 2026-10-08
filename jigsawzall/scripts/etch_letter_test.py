#!/usr/bin/env python3
"""Letter-outline ETCH test plate: the real puzzle etch on real letters.

The ring plate (cnc_calibrate/etch_matrix_cal.py) etches long smooth arcs,
which hides what actually goes wrong on a puzzle: letter-outline strokes are
dense, curvy, full of short strokes (counters, outset fragments), and that is
where stalls and warmup re-tracing burn through. This plate runs the exact
puzzle pipeline -- letter_outline_strokes() -> emitter.emit_etch_gcode() with
the material's etch profile (mode, lead-in, simplify, min segment) -- on a
row of letters per test power, then cuts the letters out exactly like the
puzzle does (the cut runs between the inset and outset etch lines).

Rows, bottom -> top, are the powers in --powers order; each row's power is
etched as a 7-segment label at the left of the row. Work zero is the plate's
BOTTOM-LEFT corner (same as the puzzles); the plate outline is cut last.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "cnc_calibrate"))

from shapely import affinity  # noqa: E402
from shapely.geometry import box  # noqa: E402

import geometry as G  # noqa: E402
import jigsaw as J  # noqa: E402
from emitter import combine_passes, emit_etch_gcode, load_material  # noqa: E402
from letter_outline_etch import draw_strokes, letter_outline_strokes  # noqa: E402
from ring_prototype import _check_xy_envelope, glyph_local  # noqa: E402
from spiral_cal import _label_strokes  # noqa: E402


def build(letters, cap_mm, powers, outline_mm, material_id, min_segment_mm,
          feed=None, font_name="round", ppm=5):
    ref = G.find_font(1000, font_name)
    cap_ratio = (ref.getbbox("H")[3] - ref.getbbox("H")[1]) / 1000.0
    font = G.find_font(max(8, int(round(cap_mm * ppm / cap_ratio))), font_name)

    label_w_mm, margin_mm, gap_mm, row_h_mm = 14.0, 6.0, 4.0, cap_mm + 9.0
    glyphs = [glyph_local(ch, font) for ch in letters]
    widths = [(g.bounds[2] - g.bounds[0]) / ppm for g in glyphs]
    W = margin_mm + label_w_mm + sum(widths) + gap_mm * (len(letters) - 1) + margin_mm
    H = margin_mm * 2 + row_h_mm * len(powers) - (row_h_mm - cap_mm)
    W, H = round(W), round(H)
    cfg = G.PuzzleConfig(panel_mm=W, panel_h_mm=H, px_per_mm=ppm,
                         panel_w_px_fit=W * ppm, panel_h_px_fit=H * ppm)
    m = cfg.margin_px

    def mm_to_px(x, y):  # machine mm (y up) -> image px (y down)
        return (m + x * ppm, m + (H - y) * ppm)

    rows = []  # (power, letter polys px, label strokes px)
    for r, pwr in enumerate(powers):
        base_y = margin_mm + r * row_h_mm  # baseline, machine mm
        x = margin_mm + label_w_mm
        polys = []
        for g, w in zip(glyphs, widths):
            gx0 = g.bounds[0]
            px, py = mm_to_px(x, base_y)
            polys.append(affinity.translate(g, px - gx0, py))
            x += w + gap_mm
        lab = _label_strokes(str(int(pwr)), margin_mm + label_w_mm / 2 - 1.0,
                             base_y + cap_mm / 2, 5.0)
        lab_px = [[mm_to_px(x1, y1), mm_to_px(x2, y2)] for x1, y1, x2, y2 in lab]
        rows.append((pwr, polys, lab_px))

    panel = box(m, m, m + W * ppm, m + H * ppm)
    material = load_material(material_id)
    etches, all_strokes, warns = [], [], []
    for pwr, polys, lab_px in rows:
        strokes, w = letter_outline_strokes(polys, panel, ppm, outline_mm, outline_mm)
        warns += w
        all_strokes += strokes + lab_px
        etches.append(emit_etch_gcode(
            strokes + lab_px, material, cfg, f"letter etch test {pwr}%",
            power_percent=pwr, feed_override=feed, min_segment_mm=min_segment_mm,
        ))

    letters_all = [p for _pw, polys, _l in rows for p in polys]
    bg = panel
    for p in letters_all:
        bg = bg.difference(p)
    pieces = [dict(polygon=p, kind="letter", serial=i + 1) for i, p in enumerate(letters_all)]
    pieces.append(dict(polygon=bg, kind="cell", serial=len(pieces) + 1))
    cut = J._emit_cut_for(pieces, material, cfg, "letter etch test", "full",
                          mode="static", power_percent=100.0,
                          min_segment_mm=min_segment_mm, max_backtrack_ms=2000.0)
    gcode = combine_passes(*etches, cut)
    return gcode, cfg, pieces, all_strokes, warns, (W, H)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--letters", default="RAS")
    ap.add_argument("--cap-mm", type=float, default=25.0)
    ap.add_argument("--powers", default="50,75,100",
                    type=lambda s: [float(x) for x in s.split(",")])
    ap.add_argument("--feed", type=int, default=None, help="etch feed (default: profile)")
    ap.add_argument("--outline-mm", type=float, default=1.0)
    ap.add_argument("--material", default="plywood_veneer_3ply_3mm")
    ap.add_argument("--min-segment-mm", type=float, default=0.3)
    ap.add_argument("--out", default=str(HERE.parent / "figs" / "etch_letter_test.gcode"))
    a = ap.parse_args(argv)

    gcode, cfg, pieces, strokes, warns, (W, H) = build(
        a.letters.upper(), a.cap_mm, a.powers, a.outline_mm, a.material,
        a.min_segment_mm, feed=a.feed)
    _check_xy_envelope(gcode, 290.0)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(gcode)
    prev = out.with_name(out.stem + "_pieces.png")
    J.render_preview(pieces, cfg, f"etch letter test {a.letters} {a.powers}% {W}x{H}mm", prev)
    draw_strokes(prev, strokes)
    png, _ = J.render_gcode_previews(gcode, cfg, out.with_suffix(""), title="etch letter test")
    for w in warns:
        print("WARNING outline etch:", w)
    print(f"plate {W}x{H}mm, rows bottom->top: {a.powers}%")
    print(f"-> {out}  ({len(gcode.splitlines())} lines)\n-> {prev}\n-> {png}")


if __name__ == "__main__":
    main()
