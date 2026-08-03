#!/usr/bin/env python3
"""Render one or more WORDs across a grid of seeds, using the wave-grid settings
we use for the birthday name puzzles (3 background rows, banner size). This is
the seed-survey renderer for dialing in a name before cutting.

    # default seeds 1..9 in a 3x3 grid
    PYTHONPATH=. python3.13 scripts/name_grid.py NORA

    # pick the seeds (grid rows derive from the count)
    NGRID_SEEDS=11,12,13,14,15,16,17,18,19 PYTHONPATH=. python3.13 \
        scripts/name_grid.py NORA

Each cell is labeled with its seed + whether it's clean. count_err (tiling
deviation) is REWARDED by the scorer, so it does NOT count against a seed — real
defects are only thin/oversized/sliver/nub. Output lands in figs/<word>_seed_grid.png.
"""

import warnings

warnings.simplefilter("ignore")
import math
import os
import sys
from argparse import Namespace
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jigsaw as J
from geometry import fit_config, generate_pieces
from PIL import Image

WORDS = [w.upper() for w in sys.argv[1:]] or ["BUG"]
SEEDS = [int(s) for s in os.environ.get("NGRID_SEEDS", "1,2,3,4,5,6,7,8,9").split(",")]
COLS = 3
ROWS = max(1, math.ceil(len(SEEDS) / COLS))

args = Namespace(
    panel_mm=None,
    panel_h_mm=None,
    piece_mm=None,
    tab_stem_mm=None,
    letter_clearance_mm=None,
    letter_gap_extra_mm=None,
    banner_h_mm=None,
    font=None,
    vertex_grid=False,
    wave_grid=True,
)
base = J._apply_size_overrides(J._config_for_size("banner"), args)
base = replace(base, wave_rows=3)

FIG = Path(__file__).resolve().parent.parent / "figs"
FIG.mkdir(parents=True, exist_ok=True)

for word in WORDS:
    tiles = []
    for seed in SEEDS:
        cfg = fit_config(word, base)
        pieces, st = generate_pieces(word, seed, base)
        cfg = st.get("cfg", cfg)
        bg = len([x for x in pieces if x["kind"] == "cell"])
        clean = (
            st["thin"] == 0
            and st["oversized"] == 0
            and st["sliver"] == 0
            and st["nub"] == 0
        )
        tmp = FIG / f"_pl_{word}_{seed}.png"
        title = f"{word} seed {seed} - {bg} cells {'OK' if clean else 'DIRTY'}"
        J.render_preview(pieces, cfg, title, tmp)
        print(f"{word} seed {seed}: {len(pieces)}pc ({bg} cells) clean={clean}")
        tiles.append(Image.open(tmp))

    tw = max(t.width for t in tiles)
    th = max(t.height for t in tiles)
    grid = Image.new("RGB", (tw * COLS, th * ROWS), "white")
    for i, t in enumerate(tiles):
        r, c = divmod(i, COLS)
        grid.paste(t, (c * tw, r * th))
    out = FIG / f"{word.lower()}_seed_grid.png"
    grid.save(out)
    for seed in SEEDS:
        (FIG / f"_pl_{word}_{seed}.png").unlink(missing_ok=True)
    print(f"-> {out}\n")
