#!/usr/bin/env python3
"""orpot — laser-cut orchid pot generator (phase 1: the two spirals).

Cut a 3mm-MDF orchid pot from two flat spiral ribbons that flex up into a coil
once an end is lifted. Phase 1 emits only the spirals (for flex-testing); the
vertical ribs, interlocking joint, and kerf-bending are deferred.

Subcommands:
  preview  — render outline PNG (+ optional SVG), no GCode
  cut      — emit static-M3 GRBL laser GCode (+ a PNG)

Examples:
  orpot.py preview --part both --svg
  orpot.py cut --part both --material mdf_3mm
  orpot.py cut --part top --strip-w 12 --top-pitch 12
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from emit import (  # noqa: E402
    emit_cut_gcode,
    emit_disc_gcode,
    load_material,
    render_assembly_sketch,
    render_disc,
    render_preview,
)
from spiral import (  # noqa: E402
    SpiralConfig,
    part_helix,
)
from ribs import (  # noqa: E402
    build_all_ribs,
    rib_3d,
    rib_azimuths,
)

BUILD_DIR = SCRIPT_DIR / "build"
FIG_DIR = SCRIPT_DIR / "figs"


def _add_geometry_args(sub: argparse.ArgumentParser) -> None:
    sub.add_argument(
        "--part",
        choices=["disc", "ribs", "all"],
        default="all",
        help="disc (single spiral piece), ribs, or all",
    )
    sub.add_argument(
        "--n-ribs",
        type=int,
        default=SpiralConfig.n_ribs,
        help="number of radial ribs",
    )
    sub.add_argument(
        "--inner-dia",
        type=float,
        default=SpiralConfig.inner_dia_mm,
        help="top spiral inner Ø (rim opening) in mm",
    )
    sub.add_argument(
        "--strip-w",
        type=float,
        default=SpiralConfig.strip_w_mm,
        help="ribbon width in mm",
    )
    sub.add_argument(
        "--base-dia",
        type=float,
        default=SpiralConfig.base_dia_mm,
        help="bottom spiral base disc Ø in mm",
    )
    sub.add_argument(
        "--turns", type=float, default=SpiralConfig.turns, help="revolutions per spiral"
    )
    sub.add_argument(
        "--top-pitch",
        type=float,
        default=SpiralConfig.top_pitch_mm,
        help="top ribbon radial advance per rev (inward), mm",
    )
    sub.add_argument(
        "--bottom-pitch",
        type=float,
        default=None,
        help="bottom ribbon radial advance per rev; default auto",
    )
    sub.add_argument(
        "--seg",
        type=float,
        default=SpiralConfig.seg_mm,
        help="spiral point spacing (curve smoothness), mm",
    )
    sub.add_argument(
        "--min-segment",
        type=float,
        default=SpiralConfig.min_segment_mm,
        help="gcode decimation floor, mm",
    )
    sub.add_argument(
        "--margin",
        type=float,
        default=SpiralConfig.margin_mm,
        help="inset from origin so coords stay positive, mm",
    )
    sub.add_argument(
        "--rise",
        type=float,
        default=SpiralConfig.rise_per_rev_mm,
        help="3D lift per full revolution, mm (assembly/view only)",
    )


def _config_from_args(args) -> SpiralConfig:
    return SpiralConfig(
        inner_dia_mm=args.inner_dia,
        strip_w_mm=args.strip_w,
        base_dia_mm=args.base_dia,
        turns=args.turns,
        top_pitch_mm=args.top_pitch,
        bottom_pitch_mm=args.bottom_pitch,
        seg_mm=args.seg,
        min_segment_mm=args.min_segment,
        margin_mm=args.margin,
        rise_per_rev_mm=args.rise,
        n_ribs=args.n_ribs,
    )


def _placed_disc(cfg: SpiralConfig):
    """The single spiral disc (profile + cuts) placed into positive coords."""
    from shapely.affinity import translate
    from spiral import build_disc

    profile, cuts = build_disc(cfg)
    minx, miny, _, _ = profile.bounds
    dx, dy = cfg.margin_mm - minx, cfg.margin_mm - miny
    profile = translate(profile, xoff=dx, yoff=dy)
    cuts = [translate(c, xoff=dx, yoff=dy) for c in cuts]
    return profile, cuts


def _placed_ribs(cfg: SpiralConfig):
    """The N rib outlines (in s-z coords) each placed into positive coords,
    laid out left-to-right for a single sheet."""
    from spiral import place

    raw = [
        (f"rib{i + 1}", place(p, cfg.margin_mm))
        for i, p in enumerate(build_all_ribs(cfg))
    ]
    return _layout_row(raw, gap=cfg.strip_w_mm)


def _layout_row(parts, gap: float):
    """Translate parts left-to-right with `gap` between bboxes so a multi-part
    preview reads side by side instead of overlapping."""
    from shapely.affinity import translate

    out = []
    cursor = 0.0
    for name, poly in parts:
        minx, miny, maxx, _ = poly.bounds
        out.append((name, translate(poly, xoff=cursor - minx, yoff=0.0)))
        cursor += (maxx - minx) + gap
    return out


def _describe(cfg: SpiralConfig) -> str:
    from spiral import disc_radii

    r_hub, r_rim_in, r_outer = disc_radii(cfg)
    return (
        f"disc Ø{2 * r_outer:.1f}mm, hub Ø{2 * r_hub:.1f}mm, "
        f"ramp {cfg.strip_w_mm:.1f}mm x{cfg.n_spirals}, {cfg.turns:g} turn(s), "
        f"{cfg.n_ribs} ribs"
    )


def cmd_preview(args) -> int:
    cfg = _config_from_args(args)
    FIG_DIR.mkdir(exist_ok=True)
    if args.part in ("disc", "all"):
        profile, cuts = _placed_disc(cfg)
        png, svg = render_disc(
            profile,
            cuts,
            FIG_DIR / "preview_disc",
            f"orpot disc: {_describe(cfg)}",
            write_svg=args.svg,
        )
        print(f"-> {png}")
        if svg is not None:
            print(f"-> {svg}")
    if args.part in ("ribs", "all"):
        ribs = _placed_ribs(cfg)
        png, svg = render_preview(
            ribs,
            FIG_DIR / "preview_ribs",
            f"orpot ribs: {_describe(cfg)}",
            write_svg=args.svg,
        )
        print(f"-> {png}")
        if svg is not None:
            print(f"-> {svg}")
    return 0


def cmd_view(args) -> int:
    """3D wireframe sketch of the assembled pot (both spirals + ribs)."""
    cfg = _config_from_args(args)
    # Two-start helix: offset the top spiral's azimuth by 180 deg.
    helices = [
        ("bottom", part_helix("bottom", cfg, phase_rad=0.0)),
        ("top", part_helix("top", cfg, phase_rad=math.pi)),
    ]
    ribs = None
    if not args.no_ribs:
        ribs = [rib_3d(cfg, a) for a in rib_azimuths(cfg)]
    total_h = cfg.rise_per_rev_mm * cfg.turns  # interleaved: both share one rise
    FIG_DIR.mkdir(exist_ok=True)
    stem = FIG_DIR / "assembly"
    rib_note = "no ribs" if args.no_ribs else f"{cfg.n_ribs} ribs"
    title = (
        f"orpot assembled (sketch): {_describe(cfg)}, "
        f"rise {cfg.rise_per_rev_mm:g}mm/rev -> ~{total_h:g}mm tall, {rib_note}"
    )
    png = render_assembly_sketch(
        helices,
        cfg,
        stem,
        title,
        base_r=cfg.base_r,
        az_deg=args.az,
        el_deg=args.el,
        ribs=ribs,
    )
    print(f"-> {png}")
    return 0


def cmd_scad(args) -> int:
    """Write an OpenSCAD file of the ASSEMBLED 3D pot for interactive viewing."""
    from scad import write_scad

    cfg = _config_from_args(args)
    BUILD_DIR.mkdir(exist_ok=True)
    out = write_scad(cfg, BUILD_DIR / "orpot.scad")
    print(f"-> {out}")
    print("   open in OpenSCAD and press F5 to preview the assembled pot")
    return 0


def cmd_cut(args) -> int:
    cfg = _config_from_args(args)
    material = load_material(args.material)
    BUILD_DIR.mkdir(exist_ok=True)

    if args.part in ("disc", "all"):
        profile, cuts = _placed_disc(cfg)
        title = f"orpot disc: {_describe(cfg)}"
        gcode = emit_disc_gcode(
            profile,
            cuts,
            material,
            title,
            cfg,
            feed_override=args.feed,
            power_percent=args.power,
        )
        out = BUILD_DIR / f"cut_disc_{args.material}.gcode"
        out.write_text(gcode)
        laser_on = gcode.count("\nM3 ")
        print(f"-> {out}  ({len(gcode.splitlines())} lines, {laser_on} cut(s))")
        png, _ = render_disc(
            profile, cuts, BUILD_DIR / f"cut_disc_{args.material}", title
        )
        print(f"-> {png}")

    if args.part in ("ribs", "all"):
        ribs = _placed_ribs(cfg)
        title = f"orpot ribs: {_describe(cfg)}"
        gcode = emit_cut_gcode(
            ribs,
            material,
            title,
            cfg,
            feed_override=args.feed,
            power_percent=args.power,
        )
        out = BUILD_DIR / f"cut_ribs_{args.material}.gcode"
        out.write_text(gcode)
        laser_on = gcode.count("\nM3 ")
        print(f"-> {out}  ({len(gcode.splitlines())} lines, {laser_on} cut(s))")
        png, _ = render_preview(
            ribs, BUILD_DIR / f"cut_ribs_{args.material}", title, write_svg=False
        )
        print(f"-> {png}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="orpot", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    pv = sub.add_parser("preview", help="render outline PNG (+ optional SVG)")
    _add_geometry_args(pv)
    pv.add_argument("--svg", action="store_true", help="also write an SVG")
    pv.set_defaults(func=cmd_preview)

    ct = sub.add_parser("cut", help="emit GRBL laser GCode (+ a PNG)")
    _add_geometry_args(ct)
    ct.add_argument("--material", default="mdf_3mm")
    ct.add_argument("--feed", type=int, default=None, help="override feed mm/min")
    ct.add_argument("--power", type=float, default=None, help="override power percent")
    ct.set_defaults(func=cmd_cut)

    vw = sub.add_parser("view", help="3D wireframe sketch of the assembled pot")
    _add_geometry_args(vw)
    vw.add_argument("--az", type=float, default=32.0, help="view azimuth deg")
    vw.add_argument("--el", type=float, default=22.0, help="view elevation deg")
    vw.add_argument("--no-ribs", action="store_true", help="omit the vertical ribs")
    vw.set_defaults(func=cmd_view)

    sc = sub.add_parser("scad", help="write an OpenSCAD file of the assembled pot")
    _add_geometry_args(sc)
    sc.set_defaults(func=cmd_scad)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
