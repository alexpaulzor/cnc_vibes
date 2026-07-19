"""OpenSCAD export of the ASSEMBLED 3D orpot — for interactive exploration.

The flat cut files (see emit.py) show the parts as they leave the laser; this
module instead emits the pot as it looks once flexed and assembled, as real
3mm-thick solids, so you can spin it around in OpenSCAD (F5 preview).

Everything reuses the SAME geometry the rest of the tool relies on, so the SCAD
can never drift from the cut/preview outputs:

  * spiral ramps  — `part_helix` gives the assembled inner/outer rails (Nx3);
                    each ramp becomes one `polyhedron` by lofting a rectangular
                    cross-section (inner..outer, thickness material_th in +z)
                    along the rails.
  * base disc     — a `cylinder` at z~0.
  * rim ring      — `difference` of two cylinders at the top (z=rise).
  * ribs          — each flat rib polygon (build_rib, in the s-z plane) is a 2D
                    `polygon` (with slot holes as inner paths), `linear_extrude`d
                    to material_th, then `multmatrix`ed into its radial plane so
                    local x=radius -> radial, local y=height -> world z, and the
                    extrude thickness runs tangentially.

OpenSCAD's F5 preview (OpenCSG) tolerates the minor face-winding imperfections of
a lofted ribbon, which is all we need for interactive viewing.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from ribs import build_rib, rib_azimuths
from spiral import ASSEMBLY_PHASE_RAD, SpiralConfig, part_helix

# RGBA colors (0..1) per group, so the assembly reads clearly.
_COLORS = {
    "bottom": (0.72, 0.46, 0.23, 1.0),  # ochre
    "top": (0.69, 0.19, 0.13, 1.0),  # brick red
    "disc": (0.55, 0.55, 0.58, 1.0),  # grey
    "ring": (0.30, 0.45, 0.62, 1.0),  # steel blue
    "ribs": (0.85, 0.78, 0.55, 1.0),  # straw
}


def _f(x: float) -> str:
    """Compact fixed-precision float for SCAD literals."""
    return f"{float(x):.4f}"


def _pt(p) -> str:
    return f"[{_f(p[0])}, {_f(p[1])}, {_f(p[2])}]"


def _loft(sections: list[np.ndarray]) -> tuple[list, list[list[int]]]:
    """Loft a sequence of cross-sections (each an (M,3) ring of points, M fixed)
    into a closed prism. Returns (points, faces). Consecutive sections are joined
    by M side quads; the first/last sections are capped."""
    m = len(sections[0])
    pts: list = []
    for sec in sections:
        pts.extend(sec)
    faces: list[list[int]] = []
    n = len(sections)
    for i in range(n - 1):
        a, b = i * m, (i + 1) * m
        for k in range(m):
            k2 = (k + 1) % m
            faces.append([a + k, a + k2, b + k2, b + k])
    # End caps (first reversed so its outward normal points the other way).
    faces.append(list(range(m - 1, -1, -1)))
    last = (n - 1) * m
    faces.append(list(range(last, last + m)))
    return pts, faces


def _ribbon_polyhedron(name: str, cfg: SpiralConfig) -> str:
    """A spiral ramp as an OpenSCAD polyhedron: the flat horizontal ribbon lofted
    along its assembled rails, given real material_th thickness in +z."""
    rails = part_helix(name, cfg, phase_rad=ASSEMBLY_PHASE_RAD[name])
    inner, outer = rails["inner"], rails["outer"]
    th = cfg.material_th_mm
    up = np.array([0.0, 0.0, th])
    # Each cross-section is a rectangle: inner/outer at z, then their +th copies.
    sections = [
        np.array([inner[i], outer[i], outer[i] + up, inner[i] + up])
        for i in range(len(inner))
    ]
    pts, faces = _loft(sections)
    pts_str = ", ".join(_pt(p) for p in pts)
    faces_str = ", ".join("[" + ", ".join(str(i) for i in f) + "]" for f in faces)
    r, g, b, a = _COLORS[name]
    return (
        f"// {name} spiral ramp ({len(pts)} pts)\n"
        f"color([{_f(r)}, {_f(g)}, {_f(b)}, {_f(a)}])\n"
        f"  polyhedron(\n"
        f"    points=[{pts_str}],\n"
        f"    faces=[{faces_str}],\n"
        f"    convexity=8\n"
        f"  );\n"
    )


def _disc(cfg: SpiralConfig) -> str:
    """Center base disc: a solid cylinder at z~0 (ramps start on its rim)."""
    r, g, b, a = _COLORS["disc"]
    return (
        "// base disc\n"
        f"color([{_f(r)}, {_f(g)}, {_f(b)}, {_f(a)}])\n"
        f"  cylinder(r={_f(cfg.base_r)}, h={_f(cfg.material_th_mm)}, $fn=180);\n"
    )


def _ring(cfg: SpiralConfig) -> str:
    """Outer rim ring at the top (z=rise): difference of two cylinders."""
    r, g, b, a = _COLORS["ring"]
    z = cfg.z_at_r(cfg.span_r_hi)  # top of the ramps
    return (
        "// rim ring\n"
        f"color([{_f(r)}, {_f(g)}, {_f(b)}, {_f(a)}])\n"
        f"  translate([0, 0, {_f(z)}])\n"
        f"  difference() {{\n"
        f"    cylinder(r={_f(cfg.top_outer_r)}, h={_f(cfg.material_th_mm)}, $fn=220);\n"
        f"    translate([0, 0, -1])\n"
        f"      cylinder(r={_f(cfg.ring_inner_r)}, "
        f"h={_f(cfg.material_th_mm + 2)}, $fn=220);\n"
        f"  }}\n"
    )


def _ring_path(coords) -> str:
    return "[" + ", ".join(f"[{_f(x)}, {_f(z)}]" for x, z in coords) + "]"


def _rib(cfg: SpiralConfig, azimuth_rad: float) -> str:
    """One rib: extrude its flat (s, z) outline (with slot holes) to material_th
    thickness, then rotate into its radial plane. multmatrix maps local
    x=radius->radial, y=height->world z, z=thickness->tangential (centered)."""
    poly = build_rib(cfg, azimuth_rad)
    # Build a single point list; paths index into it (exterior first, then holes).
    points: list[tuple[float, float]] = []
    paths: list[list[int]] = []
    for ring in [poly.exterior, *poly.interiors]:
        coords = list(ring.coords)[:-1]  # drop shapely's closing duplicate
        start = len(points)
        points.extend(coords)
        paths.append(list(range(start, start + len(coords))))

    pts_str = "[" + ", ".join(f"[{_f(x)}, {_f(z)}]" for x, z in points) + "]"
    paths_str = (
        "[" + ", ".join("[" + ", ".join(str(i) for i in p) + "]" for p in paths) + "]"
    )

    ca, sa = math.cos(azimuth_rad), math.sin(azimuth_rad)
    # Columns are images of local (x,y,z): x->radial, y->up, z->tangential.
    mat = (
        f"[[{_f(ca)}, 0, {_f(-sa)}, 0], "
        f"[{_f(sa)}, 0, {_f(ca)}, 0], "
        f"[0, 1, 0, 0], "
        f"[0, 0, 0, 1]]"
    )
    return (
        f"  multmatrix({mat})\n"
        f"    linear_extrude(height={_f(cfg.material_th_mm)}, center=true)\n"
        f"      polygon(points={pts_str}, paths={paths_str});\n"
    )


def _ribs(cfg: SpiralConfig) -> str:
    if cfg.n_ribs <= 0:
        return ""
    r, g, b, a = _COLORS["ribs"]
    body = "".join(_rib(cfg, az) for az in rib_azimuths(cfg))
    return (
        f"// {cfg.n_ribs} radial ribs\n"
        f"color([{_f(r)}, {_f(g)}, {_f(b)}, {_f(a)}]) {{\n"
        f"{body}"
        f"}}\n"
    )


def build_scad(cfg: SpiralConfig) -> str:
    """Return the full OpenSCAD source for the assembled pot."""
    total_h = cfg.rise_per_rev_mm * cfg.turns
    header = (
        "// orpot — assembled 3D pot (generated by scad.py; do not edit by hand)\n"
        f"// base Ø{cfg.base_dia_mm:.1f}mm, strip {cfg.strip_w_mm:.1f}mm, "
        f"{cfg.turns:g} turn(s), rise {cfg.rise_per_rev_mm:g}mm/rev "
        f"-> ~{total_h:g}mm tall, {cfg.n_ribs} ribs\n"
        f"$fn = 96;\n\n"
    )
    parts = [
        _ribbon_polyhedron("bottom", cfg),
        _ribbon_polyhedron("top", cfg),
        _disc(cfg),
        _ring(cfg),
        _ribs(cfg),
    ]
    return header + "\n".join(parts)


def write_scad(cfg: SpiralConfig, out_path: str | Path) -> Path:
    """Write the assembled-pot OpenSCAD file and return its path."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_scad(cfg))
    return out_path
