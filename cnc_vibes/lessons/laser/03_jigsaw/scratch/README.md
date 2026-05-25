# scratch/ — historical phase-by-phase development

The phase scripts here (`diagram_word_phase*.py`, `phase6_small.py`, `phase7_raster.py`, `phase8_full_puzzle.py`, `mockup_photo_puzzle.py`) were the iterative development path that produced the algorithm. They are **superseded** by the productionized modules at the lesson root:

| Was | Now use |
|---|---|
| `phase6_small.py` | `python jigsaw.py cut --size small` |
| `phase8_full_puzzle.py` | `python jigsaw.py cut --size full` |
| `phase7_raster.py` | `python jigsaw.py raster --size {small,full}` |
| `mockup_photo_puzzle.py` | `python jigsaw.py mockup` |
| `diagram_word_phase2.py` polygon math | `geometry.py` (`generate_pieces` etc., parametric over `PuzzleConfig`) |
| `diagram_word_phase4.py` rendering | covered by `jigsaw.py preview` |
| `diagram_word_phase5.py` shift + merge | folded into `geometry.py` |

Scratch tests (`scratch/tests/`) lock the scratch versions; the lesson-root `tests/` directory tests the productionized modules.

## Why scratch is still here

1. Regression reference — `tests/test_geometry.py` compares `geometry.py` output to scratch piece counts and tab stats so we'd catch a behavior regression introduced by the refactor.
2. The user may want to A/B compare scratch vs production output before deletion.
3. The intermediate phases document the algorithm's evolution (Bezier knobs → lollipop tabs, letter-perimeter tabs abandoned in favor of letter-as-piece, etc.) — useful history.

## When this gets deleted

After the productionized code has been verified in actual cuts and the user gives the OK, this directory will be removed in a follow-up commit. Until then, please don't add new code here — use the lesson-root modules.
