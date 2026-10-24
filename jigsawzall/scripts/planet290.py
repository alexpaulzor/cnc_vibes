"""PLANET 290mm on the stable branch: real wordmark + brand-ring ornament,
globe (SF-centred by default) + the ornament's p/ring art, MICHELS/RIEDELS
veneer settings.

Usage: planet290.py SEED OUTDIR [LON LAT] [--land] [--variants=N] [--reuse]

--reuse: load OUTDIR/sSEED_pieces.pkl (saved by every run) instead of
regenerating -- for re-emitting G-code / etch after an emitter change.

--land: countries are CUT as tabless drop-in pieces (RING_SPEC.md §13.14)
instead of etched coastlines; the etch keeps the graticule + limb, the
borders of countries merged into one piece, and the ornament art.

Always: inner + outer letter outline etch (OUTLINE_MM, the MICHELS cut's
setting), and every etch stroke SMOOTHED (spline + tangent G2/G3 biarcs,
ETCH_SMOOTH_MM): raw coastline / border polylines are hundreds of short
G1 chords GRBL can't stream at the etch feed -- the head slows, M3 power
doesn't, and the jagged coast scorched / burnt through on the SF-globe cut
(quickcut/grbl_model.py predicted x3.96 energy/mm on it). The motion model
is run on both passes and its verdict printed."""
import math
import os
import sys
from pathlib import Path

J_DIR = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, J_DIR + "/scripts"); sys.path.insert(0, J_DIR)
os.chdir(J_DIR)
import ring_prototype as R, globe_etch as GE, jigsaw as J  # noqa: E401,E402
from emitter import load_material, emit_etch_gcode, combine_passes  # noqa: E402
from letter_outline_etch import draw_strokes, letter_outline_strokes  # noqa: E402

OUTLINE_MM = 1.0  # letter outline etch inset/outset (MICHELS: 1.0mm)
ETCH_SMOOTH_MM = 0.10  # max shape change of a smoothed etch stroke

args = [a for a in sys.argv[1:] if not a.startswith("--")]
LAND = "--land" in sys.argv
VARIANTS = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--variants=")), 12)
seed, D = int(args[0]), args[1]
# globe centre (lon, lat); default near San Francisco
LON, LAT = (float(args[2]), float(args[3])) if len(args) > 3 else (-122.4, 37.7)
rp = R.RingParams(diameter_mm=290, rim_mm=27.0, hub_ring_min_mm=30.0,
                  font=R.PLANET_LOGO_FONT, ornament=R.PLANET_LOGO_ORNAMENT,
                  distinct_tabs=True, variants=VARIANTS,
                  globe_land=(LON, LAT) if LAND else None,
                  # Alex: the centre disc needn't turn -- tab it in like the
                  # rest (spokes kept); no ring seams, polar sectors instead
                  hub_rotates=False, ring_seams=False,
                  # a hub-to-rim sector runs ~80-110mm: one tabbed cross cut
                  # through any piece longer than this keeps it in size
                  split_mm=70.0)
import pickle  # noqa: E402

pk = Path(f"{D}/s{seed}_pieces.pkl")
if "--reuse" in sys.argv and pk.exists():  # re-emit without regenerating
    pieces, cfg, L, st, panel, C = pickle.loads(pk.read_bytes())
else:
    pieces, cfg, L, st, panel, C = R.generate(["PLANET"], seed, rp)
    Path(D).mkdir(parents=True, exist_ok=True)
    pk.write_bytes(pickle.dumps((pieces, cfg, L, st, panel, C)))
ppm = cfg.px_per_mm
if LAND:
    # land is cut: etch only the graticule + limb, on the land's own globe
    Rg = L["globe_r_px"]
    # 15deg graticule: with the coastlines cut, not etched, a 30deg grid misses
    # a dozen pieces, and the coverage tick each then gets is a ~3mm stroke the
    # warmup ping-pongs ~25 times (x2.7 energy/mm predicted: a scorch)
    strokes = GE.graticule(LON, LAT, Rg, C[0], C[1], lon_step=15, lat_step=15, lat_max=75,
                           meridian_lat_max=75)  # no 24-line starburst on the pole
    strokes += [b for p in pieces if p["kind"] == "land" for b in p.get("borders", [])]
else:
    Rg = rp.diameter_mm / 2 * ppm
    strokes = GE.build_globe(LON, LAT, Rg, C[0], C[1], GE.load_coastlines(),
                             meridian_lat_max=75)  # the cut pole starburst burnt


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
# A tick under MIN_TICK_MM can't get the head near the etch feed before it
# stops again (M3 power doesn't slow with it): x2.7 energy/mm predicted on a
# 3.5mm one. Those only land on tiny pieces (bridged islands), which are
# their own orientation mark -- leave them blank rather than scorch them.
MIN_TICK_MM = 10.0
ticks = GE.ensure_full_coverage(pieces, strokes)
short = [t for t in ticks if _len(t) / ppm < MIN_TICK_MM]
if short:
    print(f"{len(short)} piece(s) too small for a >= {MIN_TICK_MM:.0f}mm etch tick: left un-etched")
strokes = strokes + [t for t in ticks if _len(t) / ppm >= MIN_TICK_MM]
# the ornament's own p + ring art, upright, on its disc
for k, lab in enumerate(L["labels"]):
    if lab == "*":
        for ls in R.planet_logo_ornament_artwork(L["cap"]):
            w = R.place_upright(ls, L["ths"][k], L["Rin"], C, -L["cap"] / 2)
            strokes.append(list(w.coords))
# inner + outer outline on every letter (and the ornament disc)
outl, warns = letter_outline_strokes([p["polygon"] for p in pieces if p["kind"] == "letter"],
                                     panel, ppm, OUTLINE_MM, OUTLINE_MM)
for w in warns:
    print("WARNING outline etch:", w)
strokes += outl
tag = f"PLANET seed {seed}" + (" land" if LAND else "")
out = f"{D}/s{seed}.png"
n_land = sum(1 for p in pieces if p["kind"] == "land")
J.render_preview(pieces, cfg, f"{tag}  cap {L['cap_mm']:.0f}mm  {len(pieces)}pc"
                 + (f" ({n_land} land)" if LAND else "") + f"  score {st['score']}", out)
draw_strokes(out, strokes)
bad = st.get("defects", []) + st.get("defects_final", [])
bad += [("loose_rim", q) for q in st.get("loose_rim", [])]
if bad:
    print("QA FAILED:", ", ".join(f"{k} ({x/ppm:.0f},{y/ppm:.0f})mm" for k, (x, y) in bad))
import ring_lint as RL  # noqa: E402
pf = [f for f in RL.lint_pieces(pieces, cfg, panel, st.get("seams"), st.get("center_medallion"))
      if f.kind not in ("no_tab",)]
print("lint_pieces:", ", ".join(f"{f.kind} #{f.serial}" for f in pf) or "clean")
print("etch coverage:", ", ".join(f"#{f.serial}" for f in RL.lint_etch_coverage(pieces, strokes)) or "clean")
material = load_material("plywood_veneer_3ply_3mm")
cut = J._emit_cut_for(pieces, material, cfg, "PLANET", "ring", mode="static",
                      power_percent=100.0, min_segment_mm=0.3, max_backtrack_ms=2000.0)
etch = emit_etch_gcode(strokes, material, cfg, "PLANET globe + logo art + letter outlines",
                       min_segment_mm=0.3, smooth_mm=ETCH_SMOOTH_MM)
# frame notches for the keyed display-stand feet (stand_feet_sheet.py): cut
# with the puzzle -- the frame is on the bed anyway -- on all four stock
# edges (-5.3 / 295.3mm in this frame), before the rim frees the disc
import stand_feet_sheet as SF  # noqa: E402
import display_stand as DS  # noqa: E402

npaths, _band = SF.frame_notch_paths()
notches = DS.emit_gcode(npaths, material, "frame notches for the keyed stand feet")
g = combine_passes(etch, notches, cut)
R._check_xy_envelope(cut, 290.0)
open(f"{D}/s{seed}_cut_only.gcode", "w").write(cut)
R._check_xy_envelope(g, SF.REACH_MAX_MM, SF.REACH_MIN_MM)  # notches reach the stock edges
open(f"{D}/s{seed}.gcode", "w").write(g)
print("lint_gcode cut:", RL.lint_gcode(cut, diameter_mm=290.0).summary())
for name, gc in (("cut", cut), ("etch", etch)):
    slow = RL.lint_motion(gc)
    print(f"motion QA {name}: " + (f"{len(slow)} stroke(s) > x1.3 energy/mm; worst: "
                                   + "; ".join(f.detail for f in slow[:3]) if slow else "clean"))
print("lint_gcode etch:", RL.lint_gcode(etch, diameter_mm=290.0, is_etch=True).summary())
J.render_gcode_previews(g, cfg, Path(f"{D}/s{seed}_toolpath"), title=f"{tag} cut")
print(f"{tag}: cap {L['cap_mm']:.1f}mm, hub r {L['r_h']/ppm:.0f}mm, {len(pieces)} pieces "
      f"({n_land} land, {st.get('bridged_land', 0)} bridged), "
      f"{st.get('bridged_counters', 0)} bridged counters, score {st['score']}, "
      f"{len(strokes)} etch strokes, {len(g.splitlines())} lines")
if LAND:
    print("land:", L.get("land_stats"), "land oversized:", st.get("land_oversized"))
