"""Drift guard: every script that emits laser G-code (M3/M4) must finish its files through
quickcut.gcode (render()/write()), the one place that frames, parks and checks them.

Covers cnc_vibes and, when checked out beside it, scads/marbles. Exempt: quickcut itself, the
validator / simulators / linters (they read G-code), the mill lessons (spindle M3, not laser), and the
interactive calibration lesson (streams live to the machine, no file).
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCADS = ROOT.parent / "scads" / "marbles"
EMITS = re.compile(r"""["']M[34] S|f["']M[34] S|M3 S\{|M4 S\{""")
USES_CORE = re.compile(r"\b(?:_qc|gcode)\.(?:render|write)\(|\bfrom laser_parts import\b|\bimport gcode\b")
EXEMPT = ("quickcut/", "gcode_validate", "grbl_model", "ring_lint", "/lessons/mill/", "interactive_cal",
          "/tests/", "excellon_to_gcode", "cam_cli.py")


def _emitters():
    for base in (ROOT, SCADS):
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            rel = str(p.relative_to(base.parent))
            if any(e in rel for e in EXEMPT) or "__pycache__" in rel:
                continue
            text = p.read_text(errors="ignore")
            if EMITS.search(text):
                yield rel, text


def test_every_laser_emitter_finishes_through_quickcut():
    bad = [rel for rel, text in _emitters() if not USES_CORE.search(text)]
    assert not bad, "laser G-code emitted outside quickcut.gcode (finish with gcode.render/write): " + ", ".join(bad)
