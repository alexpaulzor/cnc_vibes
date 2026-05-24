"""Seven-segment block-font digit renderer.

Renders digits 0-9 as a list of line segments suitable for tracing with
a laser or CNC engraving operation. No font library dependency.

Each digit occupies a W x H box where W = H / 2. Segments are labeled
a-g per the standard 7-segment-display convention:

       a
     _____
    |     |
   f|     |b
    |__g__|
    |     |
   e|     |c
    |__d__|

A segment is a single line drawn as one stroke.
"""

from __future__ import annotations

# Each digit -> set of segment letters that are "on" for that digit.
_DIGIT_SEGMENTS: dict[str, set[str]] = {
    "0": set("abcdef"),
    "1": set("bc"),
    "2": set("abdeg"),
    "3": set("abcdg"),
    "4": set("bcfg"),
    "5": set("acdfg"),
    "6": set("acdefg"),
    "7": set("abc"),
    "8": set("abcdefg"),
    "9": set("abcdfg"),
}


def _segment_endpoints(
    name: str, w: float, h: float
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Return ((x1,y1),(x2,y2)) for a segment in a W x H box with origin (0,0)."""
    half_h = h / 2
    if name == "a":
        return (0, h), (w, h)
    if name == "b":
        return (w, half_h), (w, h)
    if name == "c":
        return (w, 0), (w, half_h)
    if name == "d":
        return (0, 0), (w, 0)
    if name == "e":
        return (0, 0), (0, half_h)
    if name == "f":
        return (0, half_h), (0, h)
    if name == "g":
        return (0, half_h), (w, half_h)
    raise ValueError(f"unknown segment '{name}' (expected a-g)")


def render_digit(
    digit: str, origin_x: float, origin_y: float, height: float
) -> list[tuple[float, float, float, float]]:
    """Return line segments [(x1,y1,x2,y2), ...] for one digit."""
    if digit not in _DIGIT_SEGMENTS:
        raise ValueError(f"unsupported glyph '{digit}'; supports 0-9 only")
    w = height / 2
    segs = []
    for seg in sorted(_DIGIT_SEGMENTS[digit]):
        (x1, y1), (x2, y2) = _segment_endpoints(seg, w, height)
        segs.append((origin_x + x1, origin_y + y1, origin_x + x2, origin_y + y2))
    return segs


def render_text(
    text: str,
    origin_x: float,
    origin_y: float,
    height: float,
    spacing: float = 1.0,
) -> list[tuple[float, float, float, float]]:
    """Render a string of digits as line segments at the given origin.

    `spacing` is the horizontal gap (mm) between adjacent digits.
    Returns segments anchored so the string starts at (origin_x, origin_y)
    and grows in +X.
    """
    w = height / 2
    pitch = w + spacing
    out = []
    for i, ch in enumerate(text):
        out.extend(render_digit(ch, origin_x + i * pitch, origin_y, height))
    return out


def text_width(text: str, height: float, spacing: float = 1.0) -> float:
    """Total width (mm) of a rendered string."""
    if not text:
        return 0.0
    w = height / 2
    return len(text) * w + (len(text) - 1) * spacing
