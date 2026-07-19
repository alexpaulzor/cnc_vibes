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
    load_material,
    render_assembly_sketch,
    render_overlay,
    render_preview,
)
from spiral import (  # noqa: E402
    SpiralConfig,
    build_bottom_spiral,
    build_part,
    build_top_spiral,
    part_helix,
)
from ribs import (  # noqa: E402
    base_disc_slots,
    build_all_ribs,
    rib_3d,
    rib_azimuths,
    ring_slots,
)

BUILD_DIR = SCRIPT_DIR / "build"
FIG_DIR = SCRIPT_DIR / "figs"


def _add_geometry_args(sub: argparse.ArgumentParser) -> None:
    sub.add_argument(
        "--part",
        choices=["top", "bottom", "ribs", "both", "all"],
        default="all",
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
    )


def _bottom_with_slots(cfg: SpiralConfig):
    """Bottom spiral (base disc + ramp) with the rib base-tab slots cut into the
    disc, placed into positive coords."""
    from spiral import build_bottom_spiral, place

    poly = build_bottom_spiral(cfg)
    if cfg.n_ribs > 0:
        for slot in base_disc_slots(cfg):
            poly = poly.difference(slot)
    return place(poly, cfg.margin_mm)


def _top_with_slots(cfg: SpiralConfig):
    """Top spiral (rim ring + inward ramp) with the rib top-tab slots cut into
    the ring, placed into positive coords."""
    from spiral import build_top_spiral, place

    poly = build_top_spiral(cfg)
    if cfg.n_ribs > 0:
        for slot in ring_slots(cfg):
            poly = poly.difference(slot)
    return place(poly, cfg.margin_mm)


def _placed_ribs(cfg: SpiralConfig):
    """The N rib outlines (in s-z coords) each placed into positive coords,
    laid out left-to-right for a single sheet."""
    from spiral import place

    raw = [
        (f"rib{i + 1}", place(p, cfg.margin_mm))
        for i, p in enumerate(build_all_ribs(cfg))
    ]
    return _layout_row(raw, gap=cfg.strip_w_mm)


def _cut_groups(part: str, cfg: SpiralConfig):
    """List of (group_name, [(part_name, polygon), ...]); each group becomes one
    gcode file. Spirals are one part each; 'ribs' is all N ribs on one sheet."""
    groups = {
        "top": [("top", [("top", _top_with_slots(cfg))])],
        "bottom": [("bottom", [("bottom", _bottom_with_slots(cfg))])],
        "ribs": [("ribs", _placed_ribs(cfg))],
    }
    if part in groups:
        return groups[part]
    if part == "both":
        return groups["top"] + groups["bottom"]
    if part == "all":
        return groups["top"] + groups["bottom"] + groups["ribs"]
    raise ValueError(f"unknown part: {part!r}")


def _layout_row(parts, gap: float):
    """Translate parts left-to-right with `gap` between bboxes so a multi-part
    preview reads side by side instead of overlapping. (Cut files keep one part
    each, so this is preview-only cosmetics.)"""
    from shapely.affinity import translate

    out = []
    cursor = 0.0
    for name, poly in parts:
        minx, miny, maxx, _ = poly.bounds
        out.append((name, translate(poly, xoff=cursor - minx, yoff=0.0)))
        cursor += (maxx - minx) + gap
    return out


def _describe(cfg: SpiralConfig) -> str:
    return (
        f"rim Ø{cfg.inner_dia_mm:.1f}mm..Ø{2 * cfg.top_outer_r:.1f}mm, "
        f"base Ø{cfg.base_dia_mm:.1f}mm, strip {cfg.strip_w_mm:.1f}mm, "
        f"{cfg.turns:g} turn(s)"
    )


def cmd_preview(args) -> int:
    cfg = _config_from_args(args)
    # Flatten all groups' parts into one image, laid out in a row.
    parts = [p for _, group in _cut_groups(args.part, cfg) for p in group]
    if len(parts) > 1:
        parts = _layout_row(parts, gap=cfg.strip_w_mm)
    FIG_DIR.mkdir(exist_ok=True)
    stem = FIG_DIR / f"preview_{args.part}"
    title = f"orpot {args.part}: {_describe(cfg)}"
    png, svg = render_preview(parts, stem, title, write_svg=args.svg)
    print(f"-> {png}")
    if svg is not None:
        print(f"-> {svg}")
    return 0


def cmd_overlay(args) -> int:
    """Superimpose the two flat spiral patterns (concentric) in distinct colors."""
    cfg = _config_from_args(args)
    bottom = build_bottom_spiral(cfg)
    top = build_top_spiral(cfg)
    named = [
        ("bottom (base disc + winds out)", bottom, (184, 118, 58)),  # ochre
        ("top (rim ring + winds in)", top, (176, 48, 32)),  # brick red
    ]
    FIG_DIR.mkdir(exist_ok=True)
    stem = FIG_DIR / "overlay"
    title = f"orpot overlay: {_describe(cfg)}"
    png = render_overlay(named, stem, title)
    print(f"-> {png}")
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


def cmd_cut(args) -> int:
    cfg = _config_from_args(args)
    material = load_material(args.material)
    BUILD_DIR.mkdir(exist_ok=True)

    # One gcode file per group (top / bottom / ribs). Ribs share a sheet.
    for group_name, parts in _cut_groups(args.part, cfg):
        title = f"orpot {group_name}: {_describe(cfg)}"
        gcode = emit_cut_gcode(
            parts,
            material,
            title,
            cfg,
            feed_override=args.feed,
            power_percent=args.power,
        )
        out = BUILD_DIR / f"cut_{group_name}_{args.material}.gcode"
        out.write_text(gcode)
        laser_on = gcode.count("\nM3 ")
        print(f"-> {out}  ({len(gcode.splitlines())} lines, {laser_on} cut(s))")
        png, _ = render_preview(
            parts,
            BUILD_DIR / f"cut_{group_name}_{args.material}",
            title,
            write_svg=False,
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

    ov = sub.add_parser("overlay", help="superimpose the two flat spirals")
    _add_geometry_args(ov)
    ov.set_defaults(func=cmd_overlay)

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

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
