"""svg_parts: colours sort cut / etch / hatch / dropped, holes and etch lines go with their part,
plates fit the stock, and every file goes out through gcode.write."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import svg_parts as sp  # noqa: E402

# 100 x 40 mm drawing (viewBox units = mm): a 60x20 part with a hole + score line + filled eye, a
# 20x20 part, a 90 mm long bar, and an orange "size reference" fill to drop
SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="200mm" height="40mm" viewBox="0 0 200 40">
<path d="M0,0 L60,0 L60,20 L0,20 Z M5,5 L9,5 L9,9 L5,9 Z" fill="none" stroke="#df1f26"/>
<path d="M20,10 L50,10" fill="none" stroke="#00a651"/>
<path d="M30,4 L34,4 L34,8 L30,8 Z" fill="none" stroke="#231f20"/>
<path d="M70,0 L90,0 L90,20 L70,20 Z" fill="none" stroke="#df1f26"/>
<path d="M100,30 L190,30 L190,36 L100,36 Z" fill="none" stroke="#df1f26"/>
<path d="M1,30 L6,30 L6,35 L1,35 Z" fill="#e65125"/>
</svg>"""


def _parts(scale=1.0):
    items = sp.read_svg(SVG)
    return sp.build_parts(items, sp._colours(sp.DEFAULT_CUT), sp._colours(sp.DEFAULT_ETCH),
                          sp._colours(sp.DEFAULT_HATCH), scale)


def test_colours_parts_holes_etch():
    parts, dropped = _parts()
    assert dropped == {"fill #e65125": 1}
    sizes = sorted((round(p.size[0]), round(p.size[1])) for p in parts)
    assert sizes == [(20, 20), (60, 20), (90, 6)]
    big = next(p for p in parts if round(p.size[0]) == 60)
    assert len(big.poly.interiors) == 1 and len(big.etch) == 1 and len(big.hatch) == 1
    assert round(big.etch[0].length) == 30


def test_scale():
    parts, _ = _parts(scale=0.5)
    assert sorted(round(p.size[0]) for p in parts) == [10, 30, 45]


def test_hatch_fills_inside():
    from shapely.geometry import LineString, box
    sq = box(0, 0, 4, 4)
    chains = sp.hatch_lines(sq, 0.3)
    total = sum(LineString(c).length for c in chains)
    assert total > 4 * 4 / 0.3 * 0.8                 # about area / spacing of line
    assert all(sq.buffer(0.2).contains(LineString(c)) for c in chains)


def test_plates_and_each(tmp_path):
    svg = tmp_path / "toy.svg"
    svg.write_text(SVG)
    assert sp.main([str(svg), "-o", str(tmp_path), "--stock", "120", "80"]) == 0
    files = sorted(tmp_path.glob("toy*_ply3.gcode"))
    assert files, "no plate written"
    text = files[0].read_text()
    assert ";MATERIAL: ply3" in text and "etch" in text and "; frame:" in text
    assert text.index("===== etch") < text.index("===== cut")
    assert sp.main([str(svg), "-o", str(tmp_path / "each"), "--each", "--no-etch"]) == 0
    assert len(list((tmp_path / "each").glob("*.gcode"))) == 3
