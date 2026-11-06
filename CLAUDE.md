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

## Laser G-code goes through quickcut only (non-negotiable)

`quickcut/gcode.py` is the single point of entry for laser G-code in this repo and in
scads/marbles. Every file is finished by `gcode.render()` / written by `gcode.write()`,
which add the frame pass (rapid laser-off trace of the job's full X/Y extent before the
first move, so a job that won't fit hits an endstop before the laser marks anything) and
the park (laser off, X0 at the highest cut Y), refuse lines of 255+ characters (they end a
Grbl_ESP32 SD job silently), and run `gcode_validate`. Standard cuts use `gcode.cut()`
(static M3, warmup wiggle on every path, recipe passes, loop overlap) and `gcode.order()`
(middle-out: contents before their container, with short travel). Materials: `gcode.job()`
/ `gcode.fit()` with the short ids (mdf3, ply3, birch3 ...). New rules go in quickcut,
once. `quickcut/tests/test_single_entry.py` fails any emitter that bypasses it.
