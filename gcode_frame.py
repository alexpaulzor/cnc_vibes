"""Compatibility shim: the frame pass now lives in quickcut/frame.py (quickcut is the single
G-code core). New code: `from quickcut import gcode` and write files with `gcode.write()`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "quickcut"))
from frame import MARK, add_frame, frame_lines, job_extent  # noqa: E402,F401
