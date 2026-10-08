# Why CAVAGNOLO can't cut cleanly in a 300mm-wide panel

This documents every rule in the vertex-grid puzzle generator (`geometry.py`)
that combines to force a 9-letter word's cap height down to ~7mm inside a
300mm panel — and, at that size, to make several letters (G, C, N) end up
with zero safe places left to cut a seam from at all. Every number below is
quoted or computed directly from the source cited in "Where", not from
memory.

## The arithmetic that shrinks the letters

For a 9-letter word there are 8 inter-letter gaps and 2 outer side margins.
Two independent floors stack on top of each other:

| Quantity | Formula | Value | Where |
|---|---|---|---|
| Side margin (both ends) | `banner_margin_mm` × 2 | 24mm × 2 = **48mm** | `jigsaw.py:143` sets `banner_margin_mm=24.0`; consumed as `_end_side_margin_px` in `geometry.py:1483-1485` |
| Per-gap mandatory floor | `tab_height_px + 2×tab_circle_r_px + letter_gap_extra_mm×px_per_mm` | 9mm + 6mm + 10mm = **25mm** | `geometry.py:1495-1499` (`_measure_text`'s `min_gap`) |
| Total gap floor (8 gaps) | 25mm × 8 | **200mm** | same formula, applied per adjacent pair in `_measure_text`'s shrink loop |
| Fixed floor total | 48 + 200 | **248mm** | — |
| Left for actual letter ink (9 letters) | 300 − 248 | **52mm ÷ 9 ≈ 5.8mm/letter** | — |

The font-shrink loop in `_measure_text` (`geometry.py:1515-1533`) keeps
halving the font size toward that ~52mm ink budget instead of the intended
46mm cap height, which is why the measured result comes out around 7mm
cap height, not 46mm.

## Constraint table

| Constraint | Value | Where (file:line) | Derived from | Why it exists |
|---|---|---|---|---|
| Stock width | 300mm | user's physical material, not code | the plywood sheet you actually have | hard physical boundary — not a code limit at all |
| `banner_letter_h_mm` (target cap height) | 46.0mm | `jigsaw.py:151` | comment: "Keep letters a consistent, readable size across names" | so every name-plate reads at the same, tested-legible size regardless of word length |
| `banner_margin_mm` (top/bottom/end margin) | 24.0mm | `jigsaw.py:143`; consumed in `geometry.py:1483` | comment: "~98% of tabs place at full size... while the panel stays compact (~87mm tall for NORA)" — empirically tuned against real cut tests | tabs placed too close to the panel edge get rejected by the border-clearance floor (see `tab_border_floor_v/h_mm` below); this margin keeps the majority of tabs valid |
| `letter_gap_extra_mm` (extra inter-letter tracking, vertex-grid) | 10.0mm | `jigsaw.py:124` | comment: "so there's real background between glyphs to seam through... tight words leave gaps so narrow that any split makes a sub-4mm thin bridge" (`jigsaw.py:113-116`) | leaves enough open background between letters for a structurally-sound (≥4mm) wood bridge next to a tab |
| `tab_height_px` = 3 × `tab_circle_r_px` | 3 × 15px = 45px = **9mm** | `geometry.py:240` (`__post_init__`) | geometric definition of the lollipop/capsule tab's own footprint | a tab physically needs this much material depth to exist at all |
| `tab_circle_r_px` (tab bulb radius) | 15px = 3mm | `banner_puzzle_config`, `geometry.py:336` | comment: "sized for 3mm-stock durability (the first NORA cut snapped at thin 11px knobs)" — i.e. a **real prior cut failure**, not arbitrary | undersized tabs literally snapped off during a real test cut; this is the empirically-recovered minimum |
| `tab_stem_w_px` (tab neck width) | 30px = 6mm | `banner_puzzle_config`, `geometry.py:345` | comment: "6mm neck (~2x a 3mm stock, so pieces don't snap at the stem)" | same snapped-cut failure; neck needs to be ~2x stock thickness to survive |
| Minimum seam length to carry a tab | `1.5 × tab_len_px` where `tab_len_px = max(0.4×piece_mm×px_per_mm, 5×tab_circle_r_px)` = max(20mm, 15mm) = 20mm → **30mm min seam length** | `geometry.py:241` (`tab_len_px` def), used at `geometry.py:2698, 2713` etc. | a tab (with its lock geometry) has to physically fit somewhere along the seam with room to spare | a seam shorter than this can't fit a full tab at all; the whole seam gets dropped (pieces stay merged) instead of getting a tiny/unsafe tab |
| Anchor "nook" rejection: reflex-corner clearance | 4.0mm | `geometry.py:2159, 2180` (`_vg_anchors`, `near`/`clear`) | comment: "within 4mm of a concave (reflex) corner" | a cut launched right next to a concave notch leaves a wedge of wood too thin to survive |
| Anchor "nook" rejection: launch-probe clearance | 7.0mm | `geometry.py:2181` (`_vg_anchors`, `launch`) | comment: "a short outward-normal probe can't stay ≥4mm clear of the glyph... a nook: between the fingers of an E, inside the crooks of a W/R/S" | confirms the point can actually launch a cut outward without immediately running back into the letter's own body |
| Minimum curve bend radius | 5.0mm | `geometry.py:2252` (`_vg_curve`, `min_r_mm`) | comment: "bends no tighter than min_r_mm" | a tighter bend in laser-cut wood is a stress-concentration point that's prone to snapping |
| Minimum obstacle clearance along a curve | 4.0mm | `geometry.py:2252` (`_vg_curve`, `clear_mm`) | same file, curve construction | keeps a seam's curve from grazing a neighboring letter's edge |
| Border tab clearance (top/bottom) | 7.0mm | `geometry.py:90` (`tab_border_floor_v_mm`) | field comment: "top/bottom clearance" | a tab cut too close to the panel's outer edge blows out the edge instead of interlocking |
| Border tab clearance (left/right) | 3.0mm | `geometry.py:91` (`tab_border_floor_h_mm`) | field comment: "left/right clearance" | same reasoning, smaller floor since the long edge has more natural clearance |
| Letter-to-tab minimum wood bridge | 4.0mm | `geometry.py:164` field def; `jigsaw.py:154` override | field comment: "the minimum material bridge left beside a tab (raise it to stop brittle thin bridges)" | prevents a tab from being placed so close to a letter that the sliver of wood between them snaps |
| `px_per_mm` (render/geometry resolution) | 5 | `geometry.py:76` (`PuzzleConfig` default) | fixed conversion constant | not a physical limit — just the pixel scale every mm-based rule above is computed in |

## The compounding failure at 300mm

1. The two floors above (`banner_margin_mm` × 2 + 8 × per-gap floor = 248mm)
   leave only ~52mm of your 300mm for actual letter ink across 9 letters.
2. `_measure_text`'s shrink loop (`geometry.py:1515-1533`) has no lower
   bound tied to legibility — it shrinks the font until the ink fits that
   52mm, landing around 7mm cap height (measured this session).
3. At 7mm cap height, a letter's own concave features (G's opening, C's
   opening, N's inner notch) are smaller than the fixed 4mm/7mm nook-clearance
   floors in `_vg_anchors` — so every candidate point on that side gets
   rejected, sometimes leaving a letter with **zero** anchors on its open
   side, and in one case (this session, before a fix) zero anchors at all
   (a crash in `geometry.py`'s `gen_seams`, `max()`/`min()` of an empty list).
4. Even where an anchor survives, a seam between two 7mm-tall letters that
   are 25mm apart can't always reach the 30mm minimum tab-carrying length
   (`1.5 × tab_len_px`), so it gets dropped and the two letters/background
   regions stay merged into one piece.

None of the individual numbers above are large — they're all tuned against
real prior cut failures (the "first NORA cut snapped" comments) or basic
tab geometry. The problem is that they're all **fixed mm values**, so they
don't scale down with the letters; at 46mm cap height they're a small
fraction of the letter and barely noticed, but at 7mm cap height several of
them exceed the letter itself.
