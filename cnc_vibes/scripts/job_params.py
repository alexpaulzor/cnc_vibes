"""Pure functions for loading job specs and computing derived CAM parameters.

This module is intentionally side-effect-free (no prints, no input(), no
subprocesses) so the math and the safety checks can be unit-tested.
The cnc.py CLI imports `format_report` and `PREFLIGHT_CHECKLIST` from here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class JobSpec:
    name: str
    material: str
    tool: str
    spindle_rpm: int
    gcode: str  # path to expected .gcode, relative to repo root


def load_yaml(path: Path):
    with path.open() as f:
        return yaml.safe_load(f)


def load_job(job_dir: Path) -> JobSpec:
    spec_path = job_dir / "job.yaml"
    if not spec_path.exists():
        raise FileNotFoundError(
            f"no job.yaml at {spec_path}. Create one with material, tool, "
            f"spindle_rpm, and gcode keys."
        )
    data = load_yaml(spec_path)
    required = {"material", "tool", "spindle_rpm", "gcode"}
    missing = required - data.keys()
    if missing:
        raise ValueError(f"job.yaml at {spec_path} missing keys: {sorted(missing)}")
    return JobSpec(
        name=job_dir.name,
        material=data["material"],
        tool=data["tool"],
        spindle_rpm=int(data["spindle_rpm"]),
        gcode=data["gcode"],
    )


def find_by_id(items: list[dict], item_id: str, kind: str) -> dict:
    for it in items:
        if it.get("id") == item_id:
            return it
    available = ", ".join(sorted(it.get("id", "?") for it in items))
    raise KeyError(f"{kind} '{item_id}' not found. Available: {available}")


def compute_derived(
    machine: dict, material: dict, tool: dict, spindle_rpm: int
) -> dict:
    """Compute derived CAM parameters and a list of pass/fail safety checks.

    Returns {values: dict, checks: list[dict]}.
    Raises KeyError if the material has no chipload entry for the tool.
    """
    chipload = material.get("chipload", {}).get(tool["id"])
    if chipload is None:
        raise KeyError(
            f"material '{material['id']}' has no chipload entry for tool '{tool['id']}'. "
            f"Add it to profiles/materials.yaml."
        )

    flutes = tool["flutes"]
    diameter = tool["diameter_mm"]
    thickness = material["thickness_mm"]

    feed_xy = chipload * flutes * spindle_rpm
    plunge_feed = tool["max_plunge_mm_per_min"]
    doc_rough = material["doc_fraction"] * diameter
    doc_finish = (
        material.get("doc_fraction_finish", material["doc_fraction"]) * diameter
    )
    through_cut_depth = -(thickness + 0.2)  # extra 0.2mm into spoilboard
    passes_through = math.ceil(abs(through_cut_depth) / doc_rough)

    spindle_range = machine["spindle"]
    max_feed_xy = machine["max_feed_mm_per_min"]["xy"]
    max_feed_z = machine["max_feed_mm_per_min"]["z"]

    checks = [
        {
            "ok": spindle_range["rpm_min"] <= spindle_rpm <= spindle_range["rpm_max"],
            "label": "spindle RPM within machine range",
            "detail": f"{spindle_range['rpm_min']} <= {spindle_rpm} <= {spindle_range['rpm_max']}",
        },
        {
            "ok": spindle_rpm <= tool["max_rpm"],
            "label": "spindle RPM within tool max",
            "detail": f"{spindle_rpm} <= {tool['max_rpm']}",
        },
        {
            "ok": feed_xy <= max_feed_xy,
            "label": "feed within machine XY max",
            "detail": f"{feed_xy:.0f} <= {max_feed_xy}",
        },
        {
            "ok": plunge_feed <= max_feed_z,
            "label": "plunge feed within machine Z max",
            "detail": f"{plunge_feed} <= {max_feed_z}",
        },
    ]

    return {
        "values": {
            "chipload_mm_per_tooth": chipload,
            "feed_xy_mm_per_min": feed_xy,
            "plunge_feed_mm_per_min": plunge_feed,
            "doc_rough_mm": doc_rough,
            "doc_finish_mm": doc_finish,
            "through_cut_depth_mm": through_cut_depth,
            "passes_through": passes_through,
        },
        "checks": checks,
    }


# Pre-cut safety checklist. Templates may reference {tool_id}, {tool_diameter},
# {gcode}. Add to this list; the CLI iterates over it in order.
PREFLIGHT_CHECKLIST: list[tuple[str, str]] = [
    (
        "workholding",
        "Stock clamped to spoilboard with at least 4 contact points, no flex when nudged?",
    ),
    (
        "tool_installed",
        "Correct tool installed in spindle: {tool_id} ({tool_diameter}mm)?",
    ),
    ("collet_tight", "Collet tightened with wrench (not finger-tight)?"),
    ("z_probed", "Z origin probed on top of stock (touch plate or paper-feeler)?"),
    ("xy_zero", "X/Y origin set to stock front-left corner (matches CAM WCS)?"),
    ("probe_removed", "Touch plate disconnected and stowed clear of spindle path?"),
    ("dust_collection", "Dust collection running, hose positioned over cut area?"),
    ("ppe", "Safety glasses on, sleeves/hair clear of moving parts?"),
    (
        "estop_reachable",
        "E-stop reachable from where you'll be standing during the cut?",
    ),
    ("gcode_validated", "`cnc.py validate {gcode}` ran with no violations?"),
    ("gcode_loaded", "Sender (gSender) shows the correct .gcode file loaded?"),
    ("dry_run", "If first run of this job: dry-run with spindle off at safe Z first?"),
]


def format_report(
    job: JobSpec,
    machine: dict,
    material: dict,
    tool: dict,
    derived: dict,
) -> str:
    """Render the params lookup + derivations + safety checks as plain text."""
    v = derived["values"]
    lines = [
        f"=== Job: {job.name} ===",
        "",
        f"Machine:  {machine['name']}",
        f"  envelope:           {machine['envelope_mm']['x']} x {machine['envelope_mm']['y']} x {machine['envelope_mm']['z']} mm",
        f"  max feed:           XY {machine['max_feed_mm_per_min']['xy']}  /  Z {machine['max_feed_mm_per_min']['z']} mm/min",
        f"  spindle range:      {machine['spindle']['rpm_min']}-{machine['spindle']['rpm_max']} rpm",
        "",
        f"Material: {material['id']} ({material['family']})",
        f"  thickness:          {material['thickness_mm']} mm",
        f"  doc_fraction:       {material['doc_fraction']}  -> DOC rough = {v['doc_rough_mm']:.2f} mm",
    ]
    if "doc_fraction_finish" in material:
        lines.append(
            f"  doc_fraction_finish: {material['doc_fraction_finish']}  -> DOC finish = {v['doc_finish_mm']:.2f} mm"
        )
    lines += [
        "",
        f"Tool: {tool['id']} ({tool['type']})",
        f"  diameter:           {tool['diameter_mm']} mm",
        f"  flutes:             {tool['flutes']}",
        f"  max RPM:            {tool['max_rpm']}",
        f"  max plunge:         {tool['max_plunge_mm_per_min']} mm/min",
        "",
        f"Spindle speed:        {job.spindle_rpm} rpm (from job.yaml)",
        "",
        "Derived parameters:",
        f"  chipload:           {v['chipload_mm_per_tooth']} mm/tooth",
        f"                      (materials.yaml: {material['id']} x {tool['id']})",
        f"  feed (XY):          {v['feed_xy_mm_per_min']:.0f} mm/min",
        f"                      = chipload x flutes x rpm = {v['chipload_mm_per_tooth']} x {tool['flutes']} x {job.spindle_rpm}",
        f"  plunge feed:        {v['plunge_feed_mm_per_min']} mm/min  (= tool max_plunge_mm_per_min)",
        f"  DOC roughing:       {v['doc_rough_mm']:.2f} mm  (= doc_fraction x diameter)",
        f"  DOC finishing:      {v['doc_finish_mm']:.2f} mm  (= doc_fraction_finish x diameter)",
        f"  through-cut depth:  {v['through_cut_depth_mm']:.2f} mm  (= -thickness - 0.2 spoilboard overcut)",
        f"  passes (roughing):  {v['passes_through']}  (= ceil(|through-cut| / DOC roughing))",
        "",
        "Safety checks:",
    ]
    for c in derived["checks"]:
        mark = "OK" if c["ok"] else "FAIL"
        lines.append(f"  [{mark}] {c['label']}: {c['detail']}")
    return "\n".join(lines)
