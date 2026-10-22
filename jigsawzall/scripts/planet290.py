"""PLANET 290mm on the stable branch: real wordmark + brand-ring ornament,
globe (SF-centred by default) + the ornament's p/ring art, MICHELS/RIEDELS
veneer settings.

Usage: planet290.py SEED OUTDIR [LON LAT] [--land]

--land: countries are CUT as tabless drop-in pieces (RING_SPEC.md §13.13)
instead of etched coastlines; the etch keeps the graticule + limb, the
borders of countries merged into one piece, and the ornament art."""
import math
import os
import sys
from pathlib import Path

J_DIR = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, J_DIR + "/scripts"); sys.path.insert(0, J_DIR)
os.chdir(J_DIR)
import ring_prototype as R, globe_etch as GE, jigsaw as J  # noqa: E401,E402
from emitter import load_material, emit_etch_gcode, combine_passes  # noqa: E402
from letter_outline_etch import draw_strokes  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
LAND = "--land" in sys.argv
seed, D = int(args[0]), args[1]
# globe centre (lon, lat); default near San Francisco
LON, LAT = (float(args[2]), float(args[3])) if len(args) > 3 else (-122.4, 37.7)
rp = R.RingParams(diameter_mm=290, rim_mm=27.0, hub_ring_min_mm=30.0,
                  font=R.PLANET_LOGO_FONT, ornament=R.PLANET_LOGO_ORNAMENT,
                  distinct_tabs=True, variants=12,
                  globe_land=(LON, LAT) if LAND else None)
pieces, cfg, L, st, panel, C = R.generate(["PLANET"], seed, rp)
ppm = cfg.px_per_mm
if LAND:
    # land is cut: etch only the graticule + limb, on the land's own globe
    Rg = L["globe_r_px"]
    strokes = GE.build_globe(LON, LAT, Rg, C[0], C[1], coastlines=[])
    strokes += [b for p in pieces if p["kind"] == "land" for b in p.get("borders", [])]
else:
    Rg = rp.diameter_mm / 2 * ppm
    strokes = GE.build_globe(LON, LAT, Rg, C[0], C[1], GE.load_coastlines())


def _len(st):
    return sum(math.dist(a, b) for a, b in zip(st, st[1:]))


def _keep(st):
    L_ = _len(st) / ppm
    closed = len(st) > 2 and math.dist(st[0], st[-1]) < 0.05 * ppm
    # tiny islands: a closed loop gets warm-up laps (~83mm at F5000), so a
    # 2mm island is etched dozens of times over with tiny moves -> stalls
    return L_ >= 2.0 and not (closed and L_ < 20.0)


n0 = len(strokes)
strokes = [st_ for st_ in strokes if _keep(st_)]
print(f"dropped {n0 - len(strokes)} tiny globe strokes of {n0}")
strokes = strokes + GE.ensure_full_coverage(pieces, strokes)
# the ornament's own p + ring art, upright, on its disc
for k, lab in enumerate(L["labels"]):
    if lab == "*":
        for ls in R.planet_logo_ornament_artwork(L["cap"]):
            w = R.place_upright(ls, L["ths"][k], L["Rin"], C, -L["cap"] / 2)
            strokes.append(list(w.coords))
tag = f"PLANET seed {seed}" + (" land" if LAND else "")
out = f"{D}/s{seed}.png"
n_land = sum(1 for p in pieces if p["kind"] == "land")
J.render_preview(pieces, cfg, f"{tag}  cap {L['cap_mm']:.0f}mm  {len(pieces)}pc"
                 + (f" ({n_land} land)" if LAND else "") + f"  score {st['score']}", out)
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
R._check_xy_envelope(cut, 290.0)
open(f"{D}/s{seed}_cut_only.gcode", "w").write(cut)
R._check_xy_envelope(g, 290.0)
open(f"{D}/s{seed}.gcode", "w").write(g)
J.render_gcode_previews(g, cfg, Path(f"{D}/s{seed}_toolpath"), title=f"{tag} cut")
print(f"{tag}: cap {L['cap_mm']:.1f}mm, hub r {L['r_h']/ppm:.0f}mm, {len(pieces)} pieces "
      f"({n_land} land, {st.get('bridged_land', 0)} bridged), "
      f"{st.get('bridged_counters', 0)} bridged counters, score {st['score']}, "
      f"{len(strokes)} etch strokes, {len(g.splitlines())} lines")
if LAND:
    print("land:", L.get("land_stats"), "land oversized:", st.get("land_oversized"))
