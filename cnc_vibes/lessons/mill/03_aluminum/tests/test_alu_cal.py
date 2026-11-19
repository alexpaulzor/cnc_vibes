"""alu_ops / alu_cal: validator-clean, no plunges into the stock, helix feed at the edge, frame before the router."""
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(HERE.parent))

import alu_cal  # noqa: E402
import alu_ops as A  # noqa: E402
from cam import load_tool  # noqa: E402
from gcode_validate import validate  # noqa: E402

PROFILE = yaml.safe_load((ROOT.parent / "custom_setups" / "anolex" / "anolex_4030_evo_ultra2.yaml").read_text())
TOOLS = yaml.safe_load((ROOT / "profiles" / "tools.yaml").read_text())


def _files(tmp_path):
    return alu_cal.main(["--feeds", "--size", "-o", str(tmp_path)])


def test_files_validate(tmp_path):
    for p in _files(tmp_path):
        assert validate(p.read_text(), PROFILE, TOOLS) == [], p.name


def test_no_pure_z_move_below_the_top_except_dimples(tmp_path):
    for p in _files(tmp_path):
        z = 99.0
        for ln in p.read_text().splitlines():
            c = ln.split(";")[0].strip()
            m = re.search(r"Z(-?[0-9.]+)", c)
            if not m or not c.startswith(("G0", "G1")):
                continue
            nz = float(m.group(1))
            if nz < 0 and nz < z and not re.search(r"[XY]", c):
                assert nz >= -0.2 - 1e-9, f"{p.name}: plunge to {nz}: {ln}"   # only the 0.2 counting dimples
            z = nz


def test_frame_runs_before_the_router_starts(tmp_path):
    for p in _files(tmp_path):
        t = p.read_text()
        assert t.index("; frame:") < t.index("M3 S") < t.index("G4 P")


def test_helix_feed_is_corrected_to_the_edge():
    job = A.AluJob(load_tool("flat_3.175mm_1flute_alu"), 24000, 0.02, 6.35, 0.476)
    lines = A.helical_hole(job, (10, 10), 5.3)
    fs = {int(f) for f in re.findall(r"^G3 .*F(\d+)", "\n".join(lines), re.M)}
    assert fs == {round(480 * (2.65 - 1.5875) / 2.65)}
