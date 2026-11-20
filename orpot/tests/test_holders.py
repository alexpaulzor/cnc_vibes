"""holders.py: the joints mate for any sheet thickness, the pot clears the wood, parts are sound."""
import sys
from pathlib import Path

import pytest
from shapely.geometry import LineString

DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DIR))
import holders as h  # noqa: E402

FITS = [h.Fit("ply3", 2.63, 0.10, 2.63), h.Fit("x", 3.1, 0.10, 3.2)]


def gaps_along(poly, line):
    """lengths of the empty stretches of `line` between the first and last material"""
    inside = poly.intersection(line)
    segs = sorted((list(g.coords) for g in getattr(inside, "geoms", [inside])), key=lambda c: min(c))
    out = []
    for a, b in zip(segs, segs[1:]):
        out.append(LineString([max(a), min(b)]).length)
    return out


def vertical_material(poly, u, z0=-10, z1=300):
    return poly.intersection(LineString([(u, z0), (u, z1)])).length


@pytest.mark.parametrize("f", FITS)
@pytest.mark.parametrize("pot", list(h.POTS.values()))
def test_stand_half_lap_and_disc(pot, f):
    parts, info = h.stand_parts(pot, f)
    a, b, disc = (p.shape for p in parts)
    # slot width = drawn slot, measured across each arm's slot
    assert gaps_along(a, LineString([(-20, info["z_pot"] - 1), (20, info["z_pot"] - 1)])) == pytest.approx([f.slot])
    assert gaps_along(b, LineString([(-20, 1), (20, 1)])) == pytest.approx([f.slot])
    # at the crossing the two arms' material stacks to no more than the full height: both reach the table
    za, zb = vertical_material(a, 0), vertical_material(b, 0)
    assert za + zb <= info["z_pot"] + 1e-6
    assert za + zb >= info["z_pot"] - 2 * h.SEAT - 1e-6
    # tongue is exactly one sheet tall above the floor, and the disc's + slot takes it
    assert b.intersection(LineString([(0, 0), (0, 300)])).bounds[3] == pytest.approx(info["z_pot"])
    assert gaps_along(disc, LineString([(-40, 0.5 * info["tongue"] - 1), (40, 0.5 * info["tongue"] - 1)])) == \
        pytest.approx([f.slot])
    # the pot (bottom on the disc) clears both arms
    pot_cut = h.section(pot, 0, info["z_pot"], info["top"], 0.25)
    assert a.intersection(pot_cut).area < 1e-6 and b.intersection(pot_cut).area < 1e-6
    assert info["disc_d"] / 2 < pot.half_chord(0, 0) + h.clearance(pot)


@pytest.mark.parametrize("f", FITS)
@pytest.mark.parametrize("pot", list(h.POTS.values()))
def test_flower_tabs_land_in_plate_slots(pot, f):
    parts, info = h.flower_parts(pot, f)
    fa, _, fb, _, plate = (p.shape for p in parts)
    zp, c = info["fin_top"], info["c"]
    # tabs: one sheet tall above the fin tops, at u = +-(h+gap .. h+gap+tab)
    for fin in (fa, fb):
        above = fin.intersection(h.rect(-500, zp + 0.05, 500, 500))
        assert above.bounds[3] == pytest.approx(zp + f.t)
        tabs = sorted(getattr(above, "geoms", [above]), key=lambda g: g.bounds[0])
        assert len(tabs) == 2
        for tb in tabs:
            u0, u1 = sorted(abs(v) for v in (tb.bounds[0], tb.bounds[2]))
            # the plate has a slot of the right width enclosing this tab, in each fin plane
            for x in (c, -c):
                s = LineString([(x - 10, (u0 + u1) / 2), (x + 10, (u0 + u1) / 2)])
                assert gaps_along(plate, s) == pytest.approx([f.slot])
                along = LineString([(x, u0 - 0.1), (x, u1 + 0.1)])
                assert plate.intersection(along).length < 1e-6      # whole tab length is open
    # half-lap slots at u = +-c: from the top on fins A, from the bottom on fins B, stacking to the lift
    for u in (c, -c):
        za, zb = vertical_material(fa, u, -10, info["lift"]), vertical_material(fb, u, -10, info["lift"])
        assert info["lift"] - 2 * h.SEAT - 1e-6 <= za + zb <= info["lift"] + 1e-6
    # the pot clears the fins and the plate
    pot_cut = h.section(pot, c, info["lift"], zp, 0.25)
    assert fa.intersection(pot_cut).area < 1e-6 and fb.intersection(pot_cut).area < 1e-6
    assert plate.intersection(pot.footprint(info["plate_top"] - info["lift"], 0.25)).area < 1e-6


@pytest.mark.parametrize("style", list(h.STYLES))
@pytest.mark.parametrize("pot", list(h.POTS.values()))
def test_parts_sound_and_on_one_sheet(style, pot):
    f = FITS[0]
    parts, _ = h.STYLES[style](pot, f)
    for p in parts:
        assert p.shape.geom_type == "Polygon" and p.shape.is_valid
        # no neck thinner than the material floor: eroding by just under half of it keeps one piece
        core = p.shape.buffer(-(h.MIN_FEATURE / 2 - 0.05))
        assert core.geom_type == "Polygon", f"{p.name} has a neck under {h.MIN_FEATURE} mm"
    assert len(h.layout(parts)) == 1


def test_gcode_written_and_named(tmp_path):
    written, _ = h.build("stand", h.POTS["square"], h.fit_for("ply3"), tmp_path, png=False)
    (path, _), = written
    assert path.name.startswith("orpot_stand_square_") and path.name.endswith("_ply3.gcode")
    text = path.read_text()
    assert ";MATERIAL: ply3" in text and "M3 S1000" in text


def test_net_cup_override():
    h.main(["--list-pots", "--net-height", "70"])
    assert h.POTS["net3"].height == 70 and "measured" in h.POTS["net3"].source


def test_toolpath_is_kerf_compensated():
    """the cut path sits kerf/2 outside the part: a slot's path gap is slot - kerf, so it finishes at slot"""
    f = FITS[0]
    parts, info = h.stand_parts(h.POTS["square"], f)
    path = h.toolpath(parts[1].shape, 0.15)
    assert gaps_along(path, LineString([(-20, 1), (20, 1)])) == pytest.approx([f.slot - 0.15])
    assert path.bounds[1] == pytest.approx(-0.075)
