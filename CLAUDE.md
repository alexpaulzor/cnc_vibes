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
