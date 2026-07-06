# Ramp strategies — coverage-aware re-cut of diode warm-up

Design spec for a future implementer. **This is a spec, not an
implementation.** It is the follow-up to task #114. Another agent owns
`emitter.py`; do not edit code from this document — it describes what the
code *should* grow into.

If anything here disagrees with the code, the code is the source of
truth once it lands; until then this is the intended design.

---

## 1. Background — what "ramp" is and why it matters

The diode does not reach full optical power instantly when the beam turns
on. It **ramps** from ~0 to steady-state output over the first `ramp_ms`
of every laser-on move. Because GRBL laser mode (`$32=1`) only fires the
beam while the machine is *moving*, the ramp is spatial, not temporal from
a standstill: the first stretch of travel after each `M3/M4` is cut at
less-than-full power and therefore **under-cut** (scored, not severed).

The length of that under-cut stretch is:

```
ramp_length_mm = ramp_ms / 1000 * feed_mm_per_s
              = ramp_ms / 1000 * (feed_mm_per_min / 60)
```

This is computed today in `emit_cut_gcode_full` as `lead_in_mm`
(emitter.py ~line 626):

```python
lead_in_mm = max(0.0, ramp_ms) / 1000.0 * (feed / 60.0)
```

`ramp_ms` defaults to 1000ms (deliberately conservative — better to
overcut than to leave a piece attached).

### Current state (the ONLY strategy implemented today)

`_append_lead_in_overlap(coords, lead_in_mm)` (emitter.py ~line 178)
handles exactly one case:

- If `coords` is a **closed loop** (last point == first point within
  `tol`), it re-traces the first `lead_in_mm` of the path *after* the loop
  has closed. By then the diode is hot, so the second pass over the
  opening stretch finishes the cut.
- If `coords` is an **open path**, it returns the path unchanged — "nowhere
  safe to overlap." This is the gap this spec fills.

It is called once per fused chain in the emission loop (emitter.py ~line
691), after `decimate_min_segment` and before the `G0`/`M3`/`G1` block.

The whole design rests on a single deliberate correctness bias documented
in the code: **it is acceptable to re-cut already-cut cardboard** (the
beam over a kerf does nothing harmful), but it is **not** acceptable to
leave a segment under-cut. So every ramp strategy below prefers redundant
coverage over risking an unsevered edge.

---

## 2. Definitions

- **laser-on path** — one continuous polyline emitted between a single
  `M3/M4 ... M5` pair. After `chain_contiguous_paths`, each element of
  `chains` becomes exactly one laser-on path (before the `_append_lead_in_overlap`
  tweak). A path is either a **closed loop** (endpoints coincide within
  `tol`) or an **open path**.

- **ramp prefix** — the first `ramp_length_mm` of arc-length of a laser-on
  path, measured from its start point along the path. This is the stretch
  cut while the diode was ramping.

- **fully-cut** vs **ramp-cut** — a segment is *fully-cut* if the beam
  passed over it at steady-state power; it is *ramp-cut* if the only pass
  over it was inside a ramp prefix. A ramp-cut segment is **not** severed
  and must be covered again by a later fully-cut pass.

- **coverage** — the set of geometric segments that have received at least
  one fully-cut pass. The correctness invariant is:

  > **Every edge in the puzzle must be fully-cut at least once.**

  Today the code assumes "edge dedup ⇒ each edge emitted once ⇒ done."
  That assumption is wrong for ramp prefixes: an edge that appears *only*
  as some path's ramp prefix is emitted but never fully-cut.

- **coverage debt** — the set of ramp prefixes that have not yet been
  covered by a fully-cut pass. The algorithm's job is to drive coverage
  debt to zero.

---

## 3. The coverage-accounting model (the core new idea)

Today there is no coverage accounting at all — dedup happens at the *edge*
level (`extract_unique_edges`) and the emitter trusts it. The fuller
design adds a second accounting layer at the *ramp-prefix* level:

> Treat the first `ramp_length_mm` of **every** laser-on path as **uncut**
> (coverage debt), even though some cutting happened there. Then the
> router/emitter must ensure each such prefix is later traversed by a
> hot beam.

Concretely, introduce a coverage ledger keyed by geometry:

```
# Pseudo-types
Segment    = (p0: xy, p1: xy)           # a directed sub-chord of a path
RampPrefix = list[Segment]              # the first ramp_length_mm of a path
Ledger     = { covered: set[Segment], debt: list[RampPrefix] }
```

Two operations:

- `prefix_of(path, ramp_length_mm) -> RampPrefix` — walk the path
  accumulating arc-length until `>= ramp_length_mm`; return the sub-chords
  (this is the same walk `_append_lead_in_overlap` already does at
  emitter.py ~lines 191-198, refactor it out so it is reusable).
- `mark_covered(ledger, segments)` — record that a hot pass traversed
  these segments; discharge any debt prefix whose segments are now all
  covered.

The strategies in §4 are the mechanisms that produce hot passes over debt
prefixes.

### Why "treat the whole prefix as uncut" rather than "partially cut"

Modeling partial depth is not worth it. The ramp is monotonic but its
depth profile is material- and diode-dependent and not measured here. The
safe, simple model is binary: a segment is either fully-cut or it is debt.
This matches the existing "overcut is free, undercut is fatal" bias.

---

## 4. Strategies

Listed in the order the router should *prefer* them for a given path.

### 4.0 Closed loop — continue-past-start (CURRENT behavior)

**Applies to:** closed loops only.

**Idea:** After the loop returns to its start, keep the beam on and re-do
only the ramp prefix. The diode is hot for this second pass, so the
opening stretch is now fully-cut.

**Pseudo-code** (this is what `_append_lead_in_overlap` already does):

```
def cover_closed_loop(coords, ramp_length_mm):
    if not is_closed(coords):          # endpoints within tol
        return coords                  # not our case
    prefix = prefix_of(coords, ramp_length_mm)
    return coords + points_of(prefix)  # append; laser stays on through the join
```

**Coverage effect:** the loop's own ramp prefix is discharged by the
loop's own tail. Net cost: one extra `ramp_length_mm` of travel per loop.
No coverage debt leaks out of a closed loop.

**Hook:** unchanged — `emit_cut_gcode_full`, per-chain, at the
`_append_lead_in_overlap(coords_mm, lead_in_mm)` call (~line 691).

---

### 4.1 Open path, strategy 1 — HAND OFF to an adjacent hot path (preferred)

**Applies to:** open paths whose ramp prefix is geometrically covered by a
*different* laser-on path that will pass over it while already hot.

**Idea:** Do nothing to this path. Instead, guarantee that some *other*
continuous path traverses this path's ramp prefix (or a co-located
segment) at steady-state power. This is the cheapest strategy — **zero
extra travel** — because it reuses cutting that is going to happen anyway.
It is only available when the geometry cooperates: the ramp prefix must
lie on, or coincident with, an edge that another chain covers hot.

In jigsaw geometry this happens constantly: interior grid edges are shared
between cells, letter-pocket boundaries abut grid edges, and the Eulerian
router already re-traces connectors (Chinese-Postman) — any of those
passes can be the "adjacent hot path."

**Pseudo-code (planning pass over all chains):**

```
def assign_handoffs(chains, ramp_length_mm, ledger):
    # 1. Every path contributes a debt prefix.
    for path in chains:
        ledger.debt.append(prefix_of(path, ramp_length_mm))

    # 2. Every path's body PAST its own ramp prefix is hot coverage.
    for path in chains:
        hot = segments_after(path, ramp_length_mm)   # steady-state portion
        mark_covered(ledger, hot)

    # 3. Any debt prefix now fully covered by someone else's hot body
    #    is discharged for free. Remaining debt goes to 4.2 / 4.3.
    return [p for p in ledger.debt if not is_discharged(p, ledger)]
```

Note the ordering subtlety: a path's *own* body cannot discharge its *own*
ramp prefix (the body is cut after the prefix, but the prefix is a
different piece of geometry — they only coincide for a closed loop, which
is §4.0). Hand-off requires a **distinct** path.

**Segment coincidence test:** two segments "co-locate" if they overlap
geometrically within `tol` (same underlying edge, possibly opposite
direction). Reuse the `key(p)` quantization already used in
`eulerian_order` (~line 390) so hand-off detection agrees with how the
router deduped edges.

**Hooks (two places):**

- `eulerian_order` (emitter.py ~line 368) — the router decides trail
  order and start points. To *create* hand-off opportunities, bias trail
  starts so that a path's ramp prefix falls on an edge another trail will
  cover hot. Minimal version: when choosing `start` for a trail
  (~lines 538-545), prefer a start whose outgoing ramp prefix coincides
  with an already-emitted or soon-to-be-emitted hot segment.
- `chain_contiguous_paths` (emitter.py ~line 562) — after chaining,
  before emission, run `assign_handoffs` to compute which prefixes are
  already discharged and which fall through to 4.2/4.3.

---

### 4.2 Open path, strategy 2 — DOUBLE BACK over the ramp prefix

**Applies to:** open paths whose ramp prefix could not be handed off
(§4.1 failed).

**Idea:** At the *end* of the path, reverse direction and re-trace the
first `ramp_length_mm` — the analogue of the closed-loop trick, but the
re-trace has to travel back to the start region first. Because the beam is
hot at the end, the re-traced prefix is now fully-cut.

Two sub-variants depending on where the path ends:

- **End near start** — if the path's end is within ~`ramp_length_mm` of
  its start anyway, the double-back is cheap (this is a near-closed path).
- **End far from start** — the beam must travel (laser ON, re-cutting the
  whole path in reverse, which is harmless) or lift (`M5`, `G0` rapid back
  to start, `M3` again — but then the re-fire itself ramps!). The re-fire
  problem means **you cannot lift**: lifting reintroduces a fresh ramp
  prefix at the new start. So double-back must keep the laser on and walk
  back.

**Pseudo-code:**

```
def cover_open_doubleback(coords, ramp_length_mm):
    prefix = prefix_of(coords, ramp_length_mm)          # first N mm, forward
    # Walk to the end, then reverse the whole path back to just past the
    # prefix's far end, keeping the beam on the entire time.
    back = reversed(coords)                              # end -> start, hot
    # Truncate the return once the prefix is fully re-covered.
    return coords + points_of(prefix_reverse_until_covered(back, prefix))
```

The naive form ("append the full reverse") doubles the cut length; the
truncated form only pays extra for the return leg up to the prefix. Prefer
the truncated form. Cost: worst case ~path length, best case
~`ramp_length_mm`.

**Hook:** `emit_cut_gcode_full`, per-chain, same call site as §4.0. Extend
`_append_lead_in_overlap` (or a new sibling `_append_ramp_cover`) to
branch on closed-vs-open and, for open, emit the double-back tail.

---

### 4.3 Open path, strategy 3 — REVERSE OVER the prefix (worst case)

**Applies to:** open paths where neither hand-off (§4.1) nor a full
double-back (§4.2) is desirable — e.g. an isolated open path where doubling
back costs too much travel, and no adjacent hot path exists.

**Idea:** Traverse the first `ramp_length_mm` in the **opposite**
direction to the original cut. The theory the user described: the ramp
"blends both ways" — a segment that was ramp-cut left-to-right, then
ramp-cut again right-to-left, receives two partial passes from opposite
ends whose under-cut regions are at opposite ends, so the union approaches
a full-depth cut **without ever dumping full power twice on the same
sub-segment**. This is gentler on the material than double-backing at full
power and cheaper than a hand-off that doesn't exist.

Mechanically: start the laser-on path at the *interior* end of the ramp
prefix instead of at the geometric start, cut toward the start (this is
ramp-cut in reverse), then continue forward through the rest of the path.

**Pseudo-code:**

```
def cover_open_reverse(coords, ramp_length_mm):
    prefix = prefix_of(coords, ramp_length_mm)
    j = split_index(coords, ramp_length_mm)   # first vertex past the prefix
    # Emit: interior point -> back to true start (reverse ramp-cut),
    #       then true start -> end (forward, the second ramp overlaps
    #       the first from the other direction).
    return points_of(reverse(coords[:j+1])) + coords[1:]
```

This is the only strategy that intentionally leaves a segment covered by
**two ramp passes rather than one hot pass**. It relies on the
blend-both-ways assumption and should be flagged in the G-code header
comment so a human reviewing a failed cut knows this segment took the
risky path. Treat it as the fallback of last resort.

**Hook:** same call site as §4.2. This changes the path's *start point*
and leading segments, so it must run before the `G0 X.. Y..` /
`{on} S..` block is emitted (emitter.py ~lines 692-696) — i.e. the
coords list handed to that block already encodes the reverse-over.

---

## 5. Preference order & decision procedure

For each laser-on path, in order:

```
def cover_ramp(path, chains, ledger, ramp_length_mm):
    if ramp_length_mm <= 0:
        return path                              # feature disabled

    if is_closed(path):
        return cover_closed_loop(path, ramp_length_mm)          # §4.0

    # open path:
    if handoff_available(path, chains, ledger):  # §4.1, zero extra travel
        return path                              # nothing to append
    if doubleback_cost(path) <= reverse_penalty_budget:
        return cover_open_doubleback(path, ramp_length_mm)      # §4.2
    return cover_open_reverse(path, ramp_length_mm)             # §4.3
```

`handoff_available` must be computed **globally** (a planning pass over all
chains, §4.1 pseudo-code) before the per-path emission loop, because it
depends on what *other* paths cover. So the real control flow is:

1. `eulerian_order` per tier (letters / interior / panel) — unchanged,
   optionally ramp-aware start biasing (§4.1 hook).
2. `chain_contiguous_paths` → `chains` — unchanged.
3. **NEW planning pass:** build the ledger, run `assign_handoffs`, and
   tag each chain with its chosen strategy (`closed | handoff | doubleback
   | reverse`).
4. Emission loop (emitter.py ~lines 687-706): for each chain, apply the
   tagged strategy to produce the final `coords_mm`, then emit the
   `G0/M3/G1.../M5` block as today.

---

## 6. Where each strategy hooks — summary table

| Strategy | Decision hook | Geometry mutation hook |
|---|---|---|
| §4.0 closed loop | none (detected per-path) | `emit_cut_gcode_full` per-chain, `_append_lead_in_overlap` call (~L691) |
| §4.1 hand-off | `eulerian_order` start biasing (~L538-545) + new planning pass after `chain_contiguous_paths` (~L662) | none (no extra travel) |
| §4.2 double-back | planning pass tags chain | `emit_cut_gcode_full` per-chain, new `_append_ramp_cover` (~L691) |
| §4.3 reverse-over | planning pass tags chain | `emit_cut_gcode_full` per-chain, rewrites `coords_mm` **before** the `G0`/`{on}` block (~L692) |

Shared refactor prerequisites:

- Extract the arc-length prefix walk from `_append_lead_in_overlap`
  (~L191-198) into a reusable `prefix_of(coords, length_mm)`.
- Add a coverage ledger (§3) constructed once per `emit_cut_gcode_full`
  call, threaded into the planning pass.
- Reuse `eulerian_order`'s `key(p)` quantization (~L390) for all segment
  coincidence tests so dedup and hand-off agree.

---

## 7. Edge cases & open questions for the implementer

1. **Multi-pass** (`passes > 1`, emitter.py ~lines 697-704): each pass
   currently re-fires from the same start (`G0` back, `{on}` again), so
   pass 2+ each has its *own* ramp prefix. Decide whether ramp coverage
   is per-pass or whether only the final pass needs full coverage. Cheapest
   correct answer: only the geometry matters, and pass N≥2 over the prefix
   is itself hot after pass 1 — so multi-pass may discharge ramp debt for
   free. Verify before adding cost.
2. **`ramp_length_mm` longer than the path** — clamp the prefix to the
   whole path; a very short path may be entirely ramp-cut. For such paths
   double-back (§4.2) degenerates to "cut it twice," which is fine.
3. **Hand-off direction** — a hot pass in the *opposite* direction still
   discharges debt (the segment is fully-cut regardless of direction). Do
   not require matching direction in `is_discharged`.
4. **Panel-border-last invariant** — the panel tier is cut last so stock
   stays attached (emitter.py ~lines 646-650). Ramp coverage must not
   reorder tiers; hand-off across tiers is allowed only if it does not
   move a panel edge earlier.
5. **Interaction with Chinese-Postman re-traces** — `eulerian_order`
   already duplicates connector edges (~lines 480-486). Those re-traced
   connectors are hot passes and are *excellent* hand-off donors; the
   planning pass should treat them as coverage, not as new debt.
6. **Header honesty** — when §4.3 (reverse-over) is used, emit a header
   note (like the existing `ramp:` line at ~L671-675) naming the risky
   segments, so a human debugging an unsevered piece knows which strategy
   was applied.

---

## 8. Scope reminder

This document specifies behavior only. No code in `emitter.py`,
`geometry.py`, or elsewhere should be changed on the basis of this file
alone — it is the design input for the task #114 follow-up. The
implementing agent owns `emitter.py`.
