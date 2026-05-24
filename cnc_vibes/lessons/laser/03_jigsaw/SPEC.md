# Lesson 3c — Wooden jigsaw with engraved photo and name-preserving cuts

> **Status: SPEC only — aspirational endgoal.** Not implemented and not appropriate to implement in one session. This document captures the three sub-problems, a realistic phasing, and the prior art.
>
> When you're ready to tackle this for real, the SPEC is here as the starting point.

## The endgoal

A wooden jigsaw puzzle that:

1. Has a **custom raster-engraved image** on its top face (e.g. a child's photo for their birthday).
2. Is **cut into puzzle pieces** by an algorithm that produces classic interlocking jigsaw shapes (or organic Voronoi-style — design choice).
3. Has the child's **name spelled out** in larger assembly-level pieces such that each letter is itself made of a few smaller puzzle pieces, but the cuts respect letter boundaries — letters assemble into themselves, and the named letters then assemble into the larger puzzle.

This is three subproblems, each substantial. They compose, but each one is its own week (or month) of design and implementation work.

## Sub-problem A: photo raster engraving

**Goal**: convert a 2D bitmap image into GCode that the laser executes as a series of horizontal sweeps with per-pixel power modulation. Darker pixels = more power = deeper / darker engraving.

### Prior art

- **LaserGRBL** (Windows, GUI) — best-in-class for diode-laser image rastering. Open source.
- **LightBurn** (paid, multi-platform) — the commercial standard; not in scope per the project's no-paid-software rule.
- **inkscape-laserengraver** (Inkscape plugin) — works but slow.
- **j-tech-photonics-laser-tool** — Python tool for diode lasers; well-regarded.
- **k40-whisperer** (K40 laser specific, but algorithms transfer) — Python; vector + raster.

### Realistic scope for cnc_vibes

A new `lessons/laser/03_jigsaw/raster_engrave.py` that:

1. Loads an image via Pillow.
2. Resizes to a target physical size at a target pixel pitch (e.g. 0.15mm/pixel for a 100mm × 100mm engraving = ~660×660 pixels).
3. Converts to grayscale, optionally applies error diffusion (Floyd-Steinberg) to give darker dithering for the low-power laser.
4. For each row of pixels:
   - Sweep tool left-to-right at a configured feed.
   - Per-pixel, set `S<power>` (M4 dynamic mode for clean corners).
   - Use G1 moves so M4 modulates power with momentary feed.
5. Emit reverse sweep on the next row (zig-zag — much faster than always-LTR).

This is **doable** as a standalone lesson. Likely ~300 lines. Tests on the parser + the row-emitter math. ~1 session of work.

**Dependencies it adds**: Pillow (`PIL.Image`), already a Python ecosystem mainstay.

## Sub-problem B: jigsaw piece tessellation

**Goal**: generate a set of closed cut paths that, applied to a rectangular blank, tile it into a fixed-count grid of classic interlocking puzzle pieces.

### Algorithm

Classic jigsaw piece edges:

- Each edge is one of three types: **straight** (border), **tab-out**, or **tab-in**.
- Adjacent pieces share an edge; the shared edge has opposite tabs (tab-in on one, tab-out on the other).
- Within a non-border row/column, every internal edge has a tab; the tab direction is randomized.
- Each tab is a smooth curve (typically a cubic Bezier shaped like a "ball-and-stem").

### Implementation outline

1. Parameters: outer rectangle (W × H), grid size (cols × rows), tab amplitude, tab smoothness.
2. Build a directed adjacency graph of edges (each internal vertical edge between columns, each internal horizontal edge between rows).
3. Randomly assign each internal edge's tab direction (so adjacent pieces fit).
4. For each piece (col, row), build its boundary by walking its 4 edges (one cell border at a time), substituting the configured tab curve.
5. Emit each piece as a closed polyline (or a series of GCode moves: `G0 X<start>`, `G1 X... Y... F<feed>` around the loop).

### Prior art

- **jigsawpiece** (GitHub: jrabkin/jigsawpiece) — Python, generates jigsaw piece SVG.
- **bezier-jigsaw** (various academic and hobby implementations).
- The actual math is well-published; this is "implement carefully," not "research first."

### Realistic scope for cnc_vibes

`lessons/laser/03_jigsaw/tessellate.py` that takes (W, H, cols, rows, seed) and emits GCode for cutting all pieces in one run with appropriate ordering (cut interior pieces first so the outer perimeter holds the workpiece). ~500 lines, ~2 sessions of work.

## Sub-problem C: name-preserving cut algorithm

**This is the novel and genuinely hard part.** The user's specific ask:

> "embed the name spelled out in pieces, but the python/openscad script which generates the jigsaw pattern should avoid slicing up the spelled out name too much; ideally each letter of the name could be assembled independently, and then the whole letter fits into the larger puzzle"

### What that means concretely

Imagine the name "EMMA" engraved across the middle of the puzzle. Standard jigsaw cuts would slice each letter into ~3-5 pieces with no regard for the letter shape, mixing pixels from "E" and "M" within a single piece.

The desired output:

- A "named region" is identified (the bounding box of "EMMA" plus a small margin).
- Within that region, the cut algorithm produces:
  - A few **per-letter macro pieces**: each letter is a single contiguous piece (you can assemble "E" by itself).
  - Within each letter, **smaller sub-pieces** that interlock to form the letter (so each letter is itself a mini-puzzle of 3-5 pieces).
  - The **letter-as-a-whole** then fits into the surrounding puzzle as a single piece.
- Outside the named region, normal jigsaw tessellation.

So the named region has two cut layers:
- **Outer layer**: cuts that separate one letter from the next (and from the surrounding puzzle).
- **Inner layer**: cuts within each letter that break it into sub-pieces.

### Algorithm sketch

1. **Render the name** as a vector outline (using a font library like fontTools or PIL with a font file). Get one closed path per letter glyph.
2. **Expand each glyph** by a small offset (e.g. 1.5 mm) so the cuts don't run exactly on the letter's edge.
3. **Outer cut layer**: the expanded glyph boundaries become cut paths. These split each letter from the others and from the surrounding puzzle area.
4. **Pre-process the surrounding puzzle tessellation** (sub-problem B) to skip any cuts that would cross into a letter region. Cuts that would have terminated inside a letter instead terminate at the letter's boundary.
5. **Inner cut layer**: within each letter, apply a *smaller-scale* puzzle tessellation. Same algorithm as sub-problem B but bounded to the letter's polygon and with smaller piece count (3-5 sub-pieces per letter).
6. **Order the cuts** carefully: cut the inner-letter pieces first (they're held in place by the letter's perimeter); then cut the letter perimeters (each letter now separates from the surrounding puzzle area); then cut the surrounding puzzle tessellation. Reverse cutting order would lose pieces.

### Tab compatibility across boundary

The tricky bit: the inner sub-pieces have tabs that need to fit each other AND fit into the letter's perimeter. The letter perimeter is an arbitrary curved shape, not a grid. The classic ball-and-stem tab might not lie nicely on a curved edge.

Two design choices:
- **Straight cuts within each letter** — no interlocking tabs inside letters. Each letter is a small jigsaw with straight edges between pieces. Less satisfying as a puzzle but much simpler.
- **Voronoi-style organic pieces inside letters** — generate sub-piece boundaries as Voronoi cells of random points within the letter shape, then convert each cell's edges to smooth Bezier curves. Curves can follow the letter's curvature naturally.

Voronoi is more visually pleasing but harder to implement. Straight cuts inside letters is the pragmatic MVP.

### Prior art

I'm not aware of any open-source tool that does name-preserving jigsaw cuts specifically. The closest analogues:

- **Puzzle In A Puzzle** custom commissions (Etsy, Stave Puzzles) — done by hand by craftspeople; not algorithmic.
- Various academic papers on **constrained Voronoi tessellation** that could provide the algorithmic backbone.

This is **the genuinely novel piece of the lesson** and likely a small research project on its own.

### Realistic scope

Stretch goal. Probably 1-2 weeks of focused work, not 1-2 days. Worth doing only after sub-problems A and B are solid.

## Suggested phasing

Each phase produces a real artifact that's useful on its own.

| Phase | Deliverable | Effort |
|---|---|---|
| 3c-1 | `raster_engrave.py` — image → GCode. First milestone: engrave a known photo cleanly. | 1 session |
| 3c-2 | `tessellate.py` — classic jigsaw piece generator. Cut a plain wooden plaque into pieces. | 2 sessions |
| 3c-3 | Integration: engrave the photo, then cut the pieces. **Working jigsaw of a photo.** That's already a giftable artifact. | 0.5 session |
| 3c-4 | Name-preserving cut algorithm (sub-problem C). Hard. | 1-2 weeks |

After 3c-3 you have a useful, presentable thing (a photo jigsaw with normal cuts). 3c-4 is the cherry on top that distinguishes this lesson from "just use a commercial puzzle maker."

## Reuses existing infrastructure

When implemented, this lesson will use:

- `profiles/laser_materials.yaml` for the engrave power/feed of the photo and the cut power/feed of the puzzle pieces.
- `scripts/gcode_validate.py` laser rules (the GCode is laser-mode throughout).
- `lessons/laser/01_spacer/` patterns for the cutting passes.
- `lessons/laser/02_calibration/` for finding the right power/feed for the chosen plywood.
- `lessons/laser/02_calibration/font_7seg.py` — *no, this won't help, we need real letterforms.* Use Pillow's font rendering.

Will add:
- `Pillow` to requirements.txt for image and font work.
- Possibly `numpy` for the rasterization math (depends on implementation choice).

## What this lesson does NOT do

(Same constraints as other laser lessons.)

- Does not perform kerf compensation. Pieces will be ~0.15 mm smaller than designed, which is fine for assembly (slight slop = pieces actually go in).
- Does not perform color separation for multi-color photos. Grayscale only.
- Does not handle very large puzzles (the 4030 bed is 400×300 mm; pieces must fit).
- Does not include the inverse algorithm: given a jigsaw puzzle, identify which pieces came from which row/column. (That's a puzzle-solving problem; out of scope.)

## Extensions if 3c-4 is ever real

- **Family puzzle**: render multiple names (parent + child) in different regions with their own preserved letter structures.
- **Photo-aware piece boundaries**: the name-preservation algorithm could be extended to also preserve high-contrast features in the photo (e.g. avoid cutting through eyes or faces).
- **Multi-material**: engrave the photo on light plywood, cut the pieces from dark plywood, glue together for color contrast. Two-material handling on a single bed.
