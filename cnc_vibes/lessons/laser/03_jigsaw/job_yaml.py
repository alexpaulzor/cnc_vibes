"""Job YAML loader + CLI arg derivation for jigsaw jobs.

Bridges the `cnc.py jigsaw <job.yaml>` dispatcher to `jigsaw.py`'s
existing subcommand CLI. Keeping this in a standalone module (vs
inlining in cnc.py) makes the conversion testable without spawning
subprocesses.

Schema, jigsaw-specific block (under `jigsaw:`):
  mode:                  preview | cut | raster   (required)
  size:                  small | mini | micro | full  (default: full for cut/preview, small for raster)
  word:                  e.g. NORA                 (default: NORA)
  seed:                  int                       (default: 7)

  # cut-only:
  laser_mode:            dynamic | static          (default: dynamic / M4)
  warmup_ms:             int                       (default: 0; G4 cold-start dwell per path)
  feed:                  int                       (default: material feed; override mm/min)
  min_segment_mm:        float                     (default: 0; decimate chords shorter than this)
  power_percent:         float                     (default: 100 via CLI; override cut power %)

  # raster-only:
  image:                 path to source image      (required for raster)
  raster_mode:           halftone | grayscale      (default: halftone)
  line_spacing_mm:       float                     (default: 0.20)
  engrave_power_percent: int                       (default: 30)
  engrave_feed:          int                       (default: 3000)
  grayscale_levels:      int                       (default: 16)
  test_pattern:          bool                      (default: false; mutually exclusive with image)

Top-level keys consumed: head, material, gcode (passed via --material).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


VALID_MODES = ("preview", "cut", "raster")
VALID_SIZES = ("small", "mini", "micro", "full")
VALID_RASTER_MODES = ("halftone", "grayscale")


def validate_jigsaw_job(data: dict) -> dict:
    """Return the `jigsaw:` block after schema-checking it.

    Raises ValueError on missing required fields or bad enum values.
    Does NOT check that image: paths exist (that's the caller's job —
    tests want to validate the schema with placeholder paths).
    """
    if data.get("head") != "laser":
        raise ValueError(
            f"jigsaw job.yaml must have head: laser, got {data.get('head')!r}"
        )
    jig = data.get("jigsaw")
    if not isinstance(jig, dict):
        raise ValueError("jigsaw job.yaml missing `jigsaw:` block")
    mode = jig.get("mode")
    if mode not in VALID_MODES:
        raise ValueError(f"jigsaw.mode must be one of {VALID_MODES}, got {mode!r}")
    size = jig.get("size", "full" if mode != "raster" else "small")
    if size not in VALID_SIZES:
        raise ValueError(f"jigsaw.size must be one of {VALID_SIZES}, got {size!r}")
    if mode == "raster":
        if "image" not in jig and not jig.get("test_pattern", False):
            raise ValueError(
                "jigsaw.mode: raster requires either `image:` or `test_pattern: true`"
            )
        rmode = jig.get("raster_mode", "halftone")
        if rmode not in VALID_RASTER_MODES:
            raise ValueError(
                f"jigsaw.raster_mode must be one of {VALID_RASTER_MODES}, got {rmode!r}"
            )
    return jig


def jigsaw_argv(data: dict) -> list[str]:
    """Convert a parsed jigsaw job.yaml into argv for `jigsaw.py`.

    Returns the argv list AFTER the `jigsaw.py` script path itself
    (i.e. starting with the subcommand). Caller prepends `python` and
    the script path.
    """
    jig = validate_jigsaw_job(data)
    material = data["material"]
    mode = jig["mode"]
    size = jig.get("size", "full" if mode != "raster" else "small")
    word = str(jig.get("word", "NORA"))
    seed = int(jig.get("seed", 7))

    argv: list[str] = [mode, "--size", size, "--word", word, "--seed", str(seed)]

    if mode == "cut":
        argv += ["--material", material]
        if "laser_mode" in jig:
            argv += ["--laser-mode", str(jig["laser_mode"])]
        if "warmup_ms" in jig:
            argv += ["--warmup-ms", str(int(jig["warmup_ms"]))]
        if "feed" in jig:
            argv += ["--feed", str(int(jig["feed"]))]
        if "min_segment_mm" in jig:
            argv += ["--min-segment-mm", str(float(jig["min_segment_mm"]))]
        if "power_percent" in jig:
            argv += ["--power-percent", str(float(jig["power_percent"]))]
    elif mode == "raster":
        argv += ["--material", material]
        if jig.get("test_pattern"):
            argv += ["--test-pattern"]
        else:
            argv += ["--image", str(_expand(jig["image"]))]
        argv += ["--mode", jig.get("raster_mode", "halftone")]
        argv += ["--line-spacing-mm", str(jig.get("line_spacing_mm", 0.20))]
        argv += ["--engrave-power-percent", str(jig.get("engrave_power_percent", 30))]
        argv += ["--engrave-feed", str(jig.get("engrave_feed", 3000))]
        argv += ["--grayscale-levels", str(jig.get("grayscale_levels", 16))]
    # preview: takes only --size/--word/--seed, no material
    return argv


def _expand(p: Any) -> Path:
    """Expand ~ and env vars in a path-like string."""
    import os

    return Path(os.path.expandvars(str(p))).expanduser()
