#!/usr/bin/env python3
"""Motion-model test plate: does quickcut/grbl_model.py predict the over-burn?

The MICHELS disc's center S and C burned far too deep at 100%/F5000 static M3
while the I did not. The model says that is the head running SLOWER than F on
the S/C curves (junction slowdowns: energy/mm = power / actual speed under
M3). This plate etches those exact production strokes (S, C, I: the flat
inner sliver + 1mm outset, recovered from the MICHELS G-code into
data/michels_center_SCI_strokes.json) in several variants side by side, each
through emitter.emit_etch_gcode() -- same simplify/decimate, same linear
warmup follow-through, same 100% S1000 from the plywood_veneer_3ply_3mm etch
profile -- so the only differences are the ones under test:

  row variant  what changes
  ---------------------------------------------------------------------
  "poly"       current output: G1 polyline (the F5000 row reproduces the
               real cut)
  "arc"        polyline fitted to G2/G3 arcs (quickcut/arcfit.py)
  "scaled"     polyline with per-move S scaled to the PREDICTED speed
               (grbl_model.scale_power_to_speed: a software M4)

Each row's predicted per-letter energy factor (time spent / time at the
commanded F) is written into the G-code comments and onto the preview, so
the plate reads as a direct check: letters the model calls ~1.0 should look
like each other, and letters it calls high should look burned in that order.
All model inputs are UNVERIFIED defaults unless --dollars/--accel/... say
otherwise -- the plate exists to calibrate them.

Conventions follow cnc_calibrate/etch_matrix_cal.py: single pass, S on the G1
line for in-path power changes, every test laser start ringed by a 1cm
low-power annotation circle with a gap where the path exits it, annotations
and row labels engraved LAST (15% / F3000).

    python scripts/motion_test_plate.py [--rows 5000:poly,5000:scaled,3000:poly,3000:arc,3000:scaled]
        -> out/motion_test_plate/motion_test_plate.gcode + .png + _heat.png
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

DIR = Path(__file__).resolve().parent
ROOT = DIR.parent
for d in (ROOT, ROOT / "scripts", ROOT.parent / "quickcut", ROOT.parent / "cnc_calibrate"):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))

import grbl_model  # noqa: E402
from emitter import combine_passes, emit_etch_gcode, load_material  # noqa: E402
from etch_matrix_cal import _annotation_arc  # noqa: E402
from spiral_cal import _label_strokes, _order_strokes  # noqa: E402

STROKES_JSON = ROOT / "data" / "michels_center_SCI_strokes.json"
OUT_DIR = ROOT / "out" / "motion_test_plate"
DEFAULT_ROWS = "5000:poly,5000:scaled,3000:poly,3000:arc,3000:scaled"
VARIANT_NUM = {"poly": 1, "arc": 2, "scaled": 3}  # engraved label digit
LETTERS = "SCI"
ORIGIN = (10.0, 10.0)  # plate's bottom-left corner on the machine, mm
LABEL_W = 34.0  # row-label column width
CELL_W, CELL_H = 26.0, 30.0
ANNOT_POWER_PCT, ANNOT_FEED = 15.0, 3000
ARC_TOL_MM = 0.05
SCALE_FLOOR = 0.3  # speed-scaled S never drops below 30% of the profile S
H = 1000.0  # fake image height for the px->mm round trip through the emitter


def load_letters():
    data = json.loads(STROKES_JSON.read_text())
    return {ch: list(v.values()) for ch, v in data["letters"].items()}


def _place(loops, x0, y0):
    """Translate a letter's loops so their bbox min sits at (x0, y0)."""
    mx = min(p[0] for c in loops for p in c)
    my = min(p[1] for c in loops for p in c)
    return [[(x - mx + x0, y - my + y0) for x, y in c] for c in loops]


def _cell_gcode(loops, feed, variant, material, machine, sender):
    """One letter in one variant, via the production etch emitter."""
    cfg = SimpleNamespace(px_per_mm=1.0, margin_px=0.0,
                          origin_offset_mm=(0.0, 0.0), puzzle_h_px=H)
    px = [[(x, H - y) for x, y in c] for c in loops]
    g = emit_etch_gcode(
        px, material, cfg, f"motion test F{feed} {variant}", feed_override=feed,
        min_segment_mm=0.0, simplify_mm=0.0,  # strokes are already production output
        arc_tolerance_mm=ARC_TOL_MM if variant == "arc" else None,
        motion_report=False, machine=machine, sender=sender,
    )
    if variant == "scaled":
        g = grbl_model.scale_power_to_speed(g, machine, sender, floor_frac=SCALE_FLOOR)
    return g


def _predict(g, machine, sender):
    res = grbl_model.simulate(g, machine, sender)
    st = grbl_model.stroke_stats(res)
    nom = sum(s.nominal_s for s in st)
    act = sum(s.actual_s for s in st)
    # speed-scaled rows: dose = sum(S/S_cmd * dt) over the intended time
    dose = _scaled_dose(res)
    return act / nom, dose / nom, res


def _scaled_dose(res):
    """Energy delivered relative to the commanded power: sum over laser
    blocks of (S / stroke's M3 S) * time, plus beam-on dwell at full S."""
    prog = res.program
    tot = 0.0
    for b, t in zip(prog.blocks, res.block_time):
        if b.laser:
            tot += t * b.s / prog.strokes[b.stroke]["s_on"]
    tot += sum(d for _, _, d, st in res.dwell if st >= 0)
    return tot


def generate(rows, machine=None, sender=None, material_id="plywood_veneer_3ply_3mm"):
    machine = machine or grbl_model.MachineProfile()
    sender = sender or grbl_model.SenderProfile()
    material = load_material(material_id)
    letters = load_letters()
    programs, starts, labels, table = [], [], [], []
    n = len(rows)
    for r, (feed, variant) in enumerate(rows):
        y0 = ORIGIN[1] + (n - 1 - r) * CELL_H + 6.0  # first row on top
        lx, ly = ORIGIN[0] + LABEL_W / 2, y0 + 8.0
        labels += _label_strokes(f"{feed}/{VARIANT_NUM[variant]}", lx, ly, 4.0)
        row = []
        for c, ch in enumerate(LETTERS):
            x0 = ORIGIN[0] + LABEL_W + c * CELL_W + 6.0
            loops = _place(letters[ch], x0, y0)
            g = _cell_gcode(loops, feed, variant, material, machine, sender)
            time_f, dose_f, res = _predict(g, machine, sender)
            row.append((ch, time_f, dose_f))
            prog = res.program
            for st in prog.strokes:
                b = prog.blocks[st["first_block"]]
                path = [(b.x0, b.y0)] + [
                    (prog.blocks[i].x1, prog.blocks[i].y1)
                    for i in range(st["first_block"], st["last_block"] + 1)
                ]
                starts.append(path)
            g = g.replace(
                "; generated by emitter.py",
                f"; generated by emitter.py\n; PLATE CELL row {r + 1} ({feed}/"
                f"{VARIANT_NUM[variant]} = F{feed} {variant}), letter {ch}: predicted "
                f"time factor {time_f:.2f}, energy/mm factor {dose_f:.2f}",
                1,
            )
            programs.append(g)
        table.append((feed, variant, row))

    body = combine_passes(*programs).rstrip().splitlines()
    while body and body[-1].strip() in ("", "G0 X0 Y0"):
        body.pop()
    s_ann = int(round(ANNOT_POWER_PCT * 10))
    tail = [
        "",
        f"; --- annotation: {2 * 5:g}mm circles around every test laser start, gap "
        "where the path exits (NOT part of the test) ---",
    ]
    for path in starts:
        arc = _annotation_arc(path)
        tail += [f"G0 X{arc[0][0]:.3f} Y{arc[0][1]:.3f}", f"M3 S{s_ann}", f"F{ANNOT_FEED}"]
        tail += [f"G1 X{x:.3f} Y{y:.3f}" for x, y in arc[1:]]
        tail.append("M5")
    tail.append("; --- row labels FEED/VARIANT (1=poly 2=arc 3=scaled) ---")
    for x1, y1, x2, y2 in _order_strokes(labels):
        tail += [f"G0 X{x1:.3f} Y{y1:.3f}", f"M3 S{s_ann}", f"F{ANNOT_FEED}",
                 f"G1 X{x2:.3f} Y{y2:.3f}", "M5"]
    tail += ["G0 X0 Y0", ""]

    legend = [
        "; MOTION TEST PLATE -- jigsawzall/scripts/motion_test_plate.py",
        "; letters S C I = the MICHELS center-text strokes (flat inner sliver + 1mm outset)",
        f"; etch: {material_id} etch profile, 100% S1000 M3 single pass; arcs within "
        f"{ARC_TOL_MM}mm; scaled S floor {SCALE_FLOOR:.0%}",
        "; rows top->bottom, label FEED/VARIANT (1=poly 2=arc 3=scaled):",
    ]
    for feed, variant, row in table:
        legend.append(
            f";   {feed}/{VARIANT_NUM[variant]}: "
            + "  ".join(f"{ch} time x{tf:.2f} dose x{df:.2f}" for ch, tf, df in row)
        )
    legend += [f"; {d}" for d in machine.describe() + sender.describe()]
    legend += [
        "; READ: dose x1.00 = the etch profile's intended energy/mm. If the model is",
        ";   right, the 5000/1 S and C look over-burned vs its I, and every dose~1.0",
        ";   letter looks alike. Photo it straight down next to a ruler.",
        ";",
    ]
    gcode = "\n".join(legend + body + tail)
    xs_ys = [float(v) for ln in gcode.splitlines() if not ln.startswith(";")
             for v in __import__("re").findall(r"[XY](-?[\d.]+)", ln)]
    assert all(0.0 <= v <= 290.0 for v in xs_ys), "plate outside 0..290mm"
    return gcode, table


def render(gcode, out_png, heat_png, table, machine, sender):
    """Toolpath preview (etch black, annotation/labels gray) and a heat map
    of the predicted per-block energy factor."""
    from PIL import Image, ImageDraw, ImageFont

    res = grbl_model.simulate(gcode, machine, sender)
    prog = res.program
    fac = grbl_model.block_factors(res)
    xs = [c for b in prog.blocks for c in (b.x0, b.x1)]
    ys = [c for b in prog.blocks for c in (b.y0, b.y1)]
    x0, y0, x1, y1 = 0.0, 0.0, max(max(xs) + 8, 160.0), max(ys) + 14
    S = 7
    W, Hh = int((x1 - x0) * S), int((y1 - y0) * S)
    P = lambda x, y: ((x - x0) * S, (y1 - y) * S)  # noqa: E731
    try:
        font = ImageFont.truetype(str(ROOT / "fonts" / "Quicksand-Bold.ttf"), 14)
    except OSError:
        font = ImageFont.load_default()

    def color_for(f):  # 1.0 green -> 1.5 orange -> 2.5+ red
        t = max(0.0, min(1.0, (f - 1.0) / 1.5))
        return (int(40 + 215 * t), int(170 - 140 * t), 40)

    for path, heat in ((out_png, False), (heat_png, True)):
        img = Image.new("RGB", (W, Hh), "white")
        d = ImageDraw.Draw(img)
        for b, f in zip(prog.blocks, fac):
            if b.rapid or not b.laser:
                continue
            s_on = prog.strokes[b.stroke]["s_on"]
            if s_on < 900:  # annotation / labels (15%)
                d.line([P(b.x0, b.y0), P(b.x1, b.y1)], fill=(170, 170, 170), width=1)
            else:  # dose = time factor x the move's share of the commanded S
                col = color_for(f * b.s / s_on) if heat else (0, 0, 0)
                d.line([P(b.x0, b.y0), P(b.x1, b.y1)], fill=col, width=2)
        n = len(table)
        for r, (feed, variant, row) in enumerate(table):
            ty = (n - 1 - r) * CELL_H + ORIGIN[1] + 6.0 + 18.5
            for c, (ch, tf, df) in enumerate(row):
                tx = ORIGIN[0] + LABEL_W + c * CELL_W + 4.0
                d.text(P(tx, ty), f"x{df:.2f}", fill=(0, 0, 200), font=font)
            d.text(P(ORIGIN[0], ty), f"F{feed} {variant}", fill=(0, 0, 200), font=font)
        title = ("predicted energy/mm (dose) per move: green 1.0 -> red 2.5+" if heat
                 else "toolpath: etch black, annotations gray; blue = predicted dose")
        d.text((6, 4), title, fill=(0, 0, 0), font=font)
        for k, ln in enumerate(machine.describe()[:4] + sender.describe()):
            d.text((W - 330, 4 + 16 * k), ln.replace(" (UNVERIFIED default)", " ?"),
                   fill=(120, 0, 0), font=font)
        img.save(path)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--rows", default=DEFAULT_ROWS,
                   help="comma list FEED:VARIANT, variant poly|arc|scaled, top->bottom")
    p.add_argument("--out", default=str(OUT_DIR / "motion_test_plate.gcode"))
    grbl_model.add_cli_args(p)
    a = p.parse_args(argv)
    rows = []
    for item in a.rows.split(","):
        f, v = item.split(":")
        if v not in VARIANT_NUM:
            raise SystemExit(f"unknown variant {v!r}")
        rows.append((int(f), v))
    machine, sender = grbl_model._profiles_from_args(a)
    gcode, table = generate(rows, machine, sender)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(gcode)
    png = out.with_suffix(".png")
    heat = out.with_name(out.stem + "_heat.png")
    render(gcode, png, heat, table, machine, sender)
    for feed, variant, row in table:
        print(f"F{feed} {variant:6s} " + "  ".join(
            f"{ch}: time x{tf:.2f} dose x{df:.2f}" for ch, tf, df in row))
    print(f"-> {out} ({len(gcode.splitlines())} lines)\n-> {png}\n-> {heat}")


if __name__ == "__main__":
    raise SystemExit(main())
