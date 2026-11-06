#!/usr/bin/env python3
"""Rename long material ids to the short ones (mdf3, ply3, birch3 ...) in every git-tracked text file of the
repos given. Used 2026-11-04 on cnc_vibes + scads; re-run after merging branches written with the old ids.
    python3 tools/rename_material_ids.py [--dry] /path/to/cnc_vibes /path/to/scads
"""
import re, sys, subprocess
from pathlib import Path
MAP = {
 "plywood_baltic_birch_3mm": "birch3", "plywood_veneer_3ply_3mm": "ply3", "plywood_baltic_birch_6mm": "birch6",
 "bamboo_plywood_3mm": "bamboo3", "mdf_3mm": "mdf3", "mdf_12mm": "mdf12", "balsa_3mm": "balsa3",
 "cast_acrylic_3mm_clear": "acrylic3clear", "cast_acrylic_3mm_colored": "acrylic3color",
 "cast_acrylic_3mm_black": "acrylic3", "acrylic_3mm": "acrylic3", "aluminum_6061_3mm": "alu3", "petg_3mm": "petg3",
 "cardboard_corrugated_3mm": "corrugated3", "cardboard_2mm": "card2", "cardstock_0.3mm": "cardstock03",
 "cardboard_thin_1mm": "card1",
}
rx = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(re.escape(k) for k in sorted(MAP, key=len, reverse=True)) + r")(?![A-Za-z0-9_])")
dry = "--dry" in sys.argv
tot = 0
for root in sys.argv[1:]:
    if root.startswith("--"): continue
    files = subprocess.run(["git", "-C", root, "ls-files"], capture_output=True, text=True).stdout.split()
    for f in files:
        p = Path(root) / f
        if "/gcode/" in str(p) or p.suffix in (".png", ".jpg", ".pyc", ".pdf", ".stl", ".gcode", ".nc"): continue
        try: s = p.read_text()
        except Exception: continue
        n = len(rx.findall(s))
        if n:
            tot += n
            print(f"{n:4d} {p}")
            if not dry: p.write_text(rx.sub(lambda m: MAP[m.group(1)], s))
print("total", tot)
