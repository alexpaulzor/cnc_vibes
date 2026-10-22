"""PLANET 290mm on the stable branch: real wordmark + brand-ring ornament,
globe etch (SF-centred) + the ornament's p/ring art, MICHELS/RIEDELS
veneer settings. Usage: planet290.py SEED OUTDIR"""
import sys, os
J_DIR = "/home/user/michels_good/jigsawzall"
sys.path.insert(0, J_DIR + "/scripts"); sys.path.insert(0, J_DIR)
os.chdir(J_DIR)
import ring_prototype as R, globe_etch as GE, jigsaw as J
from emitter import load_material, emit_etch_gcode, combine_passes
from letter_outline_etch import draw_strokes

seed, D = int(sys.argv[1]), sys.argv[2]
rp = R.RingParams(diameter_mm=290, rim_mm=27.0, hub_ring_min_mm=30.0,
                  font=R.PLANET_LOGO_FONT, ornament=R.PLANET_LOGO_ORNAMENT,
                  distinct_tabs=True, variants=12)
pieces, cfg, L, st, panel, C = R.generate(["PLANET"], seed, rp)
ppm = cfg.px_per_mm
Rp = rp.diameter_mm / 2 * ppm
strokes = GE.build_globe(-122.4, 37.7, Rp, C[0], C[1], GE.load_coastlines())
strokes = strokes + GE.ensure_full_coverage(pieces, strokes)
# the ornament's own p + ring art, upright, on its disc
for k, lab in enumerate(L["labels"]):
    if lab == "*":
        for ls in R.planet_logo_ornament_artwork(L["cap"]):
            w = R.place_upright(ls, L["ths"][k], L["Rin"], C, -L["cap"] / 2)
            strokes.append(list(w.coords))
tag = f"PLANET seed {seed}"
out = f"{D}/s{seed}.png"
J.render_preview(pieces, cfg, f"{tag}  cap {L['cap_mm']:.0f}mm  {len(pieces)}pc  score {st['score']}", out)
draw_strokes(out, strokes)
bad = st.get("defects", []) + st.get("defects_final", [])
if bad:
    print("QA FAILED:", ", ".join(f"{k} ({x/ppm:.0f},{y/ppm:.0f})mm" for k, (x, y) in bad))
material = load_material("plywood_veneer_3ply_3mm")
cut = J._emit_cut_for(pieces, material, cfg, "PLANET", "ring", mode="static",
                      power_percent=100.0, min_segment_mm=0.3, max_backtrack_ms=2000.0)
etch = emit_etch_gcode(strokes, material, cfg, "PLANET globe + logo art", min_segment_mm=0.3,
                       arc_tolerance_mm=0.05)
g = combine_passes(etch, cut)
R._check_xy_envelope(g, 290.0)
open(f"{D}/s{seed}.gcode", "w").write(g)
from pathlib import Path
J.render_gcode_previews(g, cfg, Path(f"{D}/s{seed}_toolpath"), title=f"{tag} cut")
print(f"{tag}: cap {L['cap_mm']:.1f}mm, hub r {L['r_h']/ppm:.0f}mm, {len(pieces)} pieces, "
      f"{st.get('bridged_counters', 0)} bridged counters, score {st['score']}, "
      f"{len(strokes)} etch strokes, {len(g.splitlines())} lines")
