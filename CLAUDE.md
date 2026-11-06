## Stated-figure accuracy (non-negotiable)

Any numeric value written in prose — a distance, duration, angle, percentage,
power/feed setting, count, etc. — that corresponds to a value computed,
printed, or emitted elsewhere in the same turn (a script's stdout, a
G-code comment, a lint report, a file's contents) MUST be copied or
re-derived directly from that source at the moment of writing the
sentence, never restated from memory of "what I calculated earlier."

Before sending any response containing such a figure:
- Locate the authoritative source value (the actual computed/printed number).
- Either quote it verbatim, or show the arithmetic inline so the reader
  can verify it themselves.
- If the number can't be re-checked against a concrete source in this
  turn, say so explicitly ("uncalculated/unverified estimate") rather
  than stating it as if confirmed.

This matters most for anything that drives a physical action (laser
power/feed/timing, cut depth, tolerances, dimensions) where a wrong
figure by even a small factor can produce a bad or unsafe cut.

## Laser G-code always starts with a frame pass (non-negotiable)

Every laser G-code file any tool here (or in scads/marbles) generates must call
`gcode_frame.add_frame(lines)` (repo root) before writing: it inserts a rapid, laser-off
trace of the job's full X/Y extent ahead of the first move, so a job that won't fit
from the chosen zero hits an endstop before the laser has marked anything. The
validator enforces it (`laser_frame` rule: a `; frame:` block before the first M3/M4).
New emitters: route the final line list through `add_frame` (idempotent).
