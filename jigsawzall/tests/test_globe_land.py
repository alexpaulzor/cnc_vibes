"""Tests for scripts/globe_land.py (globe land as cut pieces, RING_SPEC §13.14)."""

import math
import sys
from pathlib import Path

import pytest
from shapely.geometry import Point, Polygon, box

PKG_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_DIR))
sys.path.insert(0, str(PKG_DIR / "scripts"))

import globe_land as GL  # noqa: E402
import ring_prototype as R  # noqa: E402

PPM = 5.0
RG = 100 * PPM
C = (600.0, 600.0)


def test_project_country_near_centre_and_far_side():
    sq = [[[-5, -5], [5, -5], [5, 5], [-5, 5], [-5, -5]]]
    g = GL.project_country([sq], 0.0, 0.0, RG, C[0], C[1])
    assert g is not None and g.contains(Point(C))
    # 10 degrees across at the centre: ~R * sin(5deg) * 2 wide
    w = g.bounds[2] - g.bounds[0]
    assert abs(w - 2 * RG * math.sin(math.radians(5))) < 0.02 * w
    assert GL.project_country([sq], 150.0, 0.0, RG, C[0], C[1]) is None  # far side
    with pytest.raises(ValueError):  # around the antipode: refused, not inverted
        GL.project_country([sq], 180.0, 0.0, RG, C[0], C[1])


def test_project_country_clips_at_the_limb():
    # spans the horizon at lon 90: only the near half is kept, ending on the limb
    sq = [[[80, -5], [100, -5], [100, 5], [80, 5], [80, -5]]]
    g = GL.project_country([sq], 0.0, 0.0, RG, C[0], C[1])
    far = max(math.dist(C, p) for p in g.exterior.coords)
    assert abs(far - RG) < 0.5


def test_inverse_ortho_roundtrip():
    for lon, lat in [(-122.4, 37.7), (-100.0, 50.0), (-140.0, 10.0)]:
        lon0, lat0 = -122.4, 37.7
        # forward via the cached-polygon path: a tiny square around the point
        sq = [[[lon - .01, lat - .01], [lon + .01, lat - .01], [lon + .01, lat + .01],
               [lon - .01, lat + .01], [lon - .01, lat - .01]]]
        g = GL.project_country([sq], lon0, lat0, RG, C[0], C[1])
        c = g.centroid
        lo, la = GL.inverse_ortho(c.x, c.y, lon0, lat0, RG, C[0], C[1])
        assert abs(lo - lon) < 0.05 and abs(la - lat) < 0.05


def test_chop_seams_meets_the_coast_square_on():
    land = box(100, -50, 200, 50)
    seam = dict(pts=[(0, 0), (300, 0)], kind="ring", ends=("L", "B"))
    out = GL.chop_seams([seam], land, PPM)
    assert len(out) == 2
    a, b = sorted(out, key=lambda s: s["pts"][0][0])
    assert a["ends"] == ("L", "L") and b["ends"] == ("L", "B")
    assert a["pts"][-1] == (100.0, 0.0) and b["pts"][0] == (200.0, 0.0)
    assert all(s["plain_ok"] and s["land_chopped"] for s in out)


def test_chop_seams_leaves_seams_clear_of_land_alone():
    land = box(100, 100, 200, 200)
    seam = dict(pts=[(0, 0), (300, 0)], kind="ring", ends=("L", "B"))
    assert GL.chop_seams([seam], land, PPM) == [seam]


def test_land_pieces_are_clean_and_tile_the_land():
    cfg = R.make_cfg(R.RingParams())
    ppm = cfg.px_per_mm
    rg = 121 * ppm
    c = (rg + 30 * ppm, rg + 30 * ppm)
    disc = Point(c).buffer(rg, quad_segs=96)
    pcs, st = GL.land_pieces(GL.load_countries(), -122.4, 37.7, rg, c, disc, Polygon(), ppm,
                             lambda g: R.oversized_oriented(g, cfg))
    assert st["unsplittable"] == 0 and len(pcs) > 10
    r = 1.7 * ppm
    for p in pcs:
        g = p["polygon"]
        assert g.geom_type == "Polygon" and g.is_valid
        assert not R.oversized_oriented(g, cfg)
        assert not GL.piece_issues(g, r, ppm), p["names"]
        assert disc.buffer(1).contains(g)
    # pieces never overlap
    for i, a in enumerate(pcs):
        for b in pcs[i + 1:]:
            assert a["polygon"].intersection(b["polygon"]).area < 1.0
