#!/usr/bin/env python3
"""Capture a one-shot webcam still or process an existing image file,
overlay a timestamp + label, and save as a JPEG. Used for pre/post-cut
setup records.

Standalone Python tool. No LLM dependency.

Two source modes:
  --camera N    grab a frame from USB camera N (requires opencv-python)
  --source PATH process an existing image file (no camera dependency;
                useful for testing the overlay pipeline)

Usage:
  python snapshot.py --camera 0 --label before-cut --job hole_in_sheet
  python snapshot.py --source path/to/photo.jpg --label after-cut
  python snapshot.py --camera 0 --no-overlay --out logs/raw/
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Filename construction — pure, testable.
# ---------------------------------------------------------------------------


def make_filename(
    timestamp: dt.datetime, job: str | None, label: str, ext: str = "jpg"
) -> str:
    """Build a timestamped filename: <iso>_<job>_<label>.<ext>"""
    ts = timestamp.strftime("%Y-%m-%dT%H-%M-%S")
    parts = [ts]
    if job:
        parts.append(_sanitize(job))
    if label:
        parts.append(_sanitize(label))
    return "_".join(parts) + "." + ext


def _sanitize(s: str) -> str:
    """Make a string filesystem-safe (replace anything not alnum/_/-)."""
    return "".join(c if c.isalnum() or c in "_-." else "_" for c in s)


# ---------------------------------------------------------------------------
# Overlay — Pillow-based.
# ---------------------------------------------------------------------------


def add_overlay(
    image,  # PIL.Image.Image
    timestamp: dt.datetime,
    label: str,
    job: str | None = None,
):
    """Stamp the image with a timestamp (top-left) and label (top-right)."""
    from PIL import ImageDraw, ImageFont  # lazy

    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("Arial.ttf", size=max(14, image.height // 40))
    except (OSError, IOError):
        font = ImageFont.load_default()

    margin = 8

    # Timestamp top-left.
    ts_text = timestamp.strftime("%Y-%m-%d %H:%M:%S")
    _draw_with_background(draw, (margin, margin), ts_text, font)

    # Label top-right.
    label_text = label if not job else f"{job} / {label}"
    bbox = draw.textbbox((0, 0), label_text, font=font)
    text_w = bbox[2] - bbox[0]
    _draw_with_background(
        draw, (image.width - text_w - margin, margin), label_text, font
    )

    return image


def _draw_with_background(draw, xy, text, font):
    bbox = draw.textbbox(xy, text, font=font)
    pad = 4
    bg_box = (bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad)
    draw.rectangle(bg_box, fill=(0, 0, 0, 200))
    draw.text(xy, text, font=font, fill=(255, 255, 255))


# ---------------------------------------------------------------------------
# Sources — file-based (always available) and camera (cv2-conditional).
# ---------------------------------------------------------------------------


def load_from_file(path: Path):
    """Load an image from disk via Pillow. Returns PIL.Image."""
    from PIL import Image

    if not path.exists():
        sys.exit(f"error: source file not found: {path}")
    try:
        return Image.open(path).convert("RGB")
    except Exception as e:  # noqa: BLE001
        sys.exit(f"error: could not open {path}: {e}")


def capture_from_camera(camera_index: int):
    """Grab one frame from USB camera N. Requires opencv-python."""
    try:
        import cv2  # type: ignore
    except ImportError:
        sys.exit(
            "error: opencv-python is not installed. Run:\n"
            "  python -m pip install opencv-python-headless\n"
            "Or use --source PATH to process an existing image instead."
        )
    from PIL import Image

    cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        sys.exit(f"error: could not open camera {camera_index}")
    try:
        # Throw away a few frames in case the camera auto-exposes.
        for _ in range(3):
            cap.read()
        ok, frame = cap.read()
        if not ok or frame is None:
            sys.exit(f"error: camera {camera_index} returned no frame")
        # OpenCV is BGR; convert to RGB for Pillow.
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return Image.fromarray(frame_rgb)
    finally:
        cap.release()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])

    src = p.add_argument_group("source (exactly one)")
    src.add_argument(
        "--camera",
        type=int,
        default=None,
        help="USB camera index (requires opencv-python)",
    )
    src.add_argument(
        "--source", type=Path, default=None, help="existing image file to process"
    )

    p.add_argument(
        "--label", default="snap", help="free-form label embedded in filename + overlay"
    )
    p.add_argument("--job", default=None, help="job name for cross-reference")
    p.add_argument(
        "--out", type=Path, default=Path("logs/snapshots"), help="output directory"
    )
    p.add_argument(
        "--no-overlay",
        action="store_true",
        help="skip the timestamp/label overlay (raw frame)",
    )
    args = p.parse_args()

    if (args.camera is None) == (args.source is None):
        sys.exit("error: provide exactly one of --camera or --source")

    timestamp = dt.datetime.now()

    if args.source is not None:
        image = load_from_file(args.source)
    else:
        image = capture_from_camera(args.camera)

    if not args.no_overlay:
        image = add_overlay(image, timestamp, args.label, args.job)

    args.out.mkdir(parents=True, exist_ok=True)
    fname = make_filename(timestamp, args.job, args.label)
    out_path = args.out / fname
    image.save(out_path, "JPEG", quality=85)
    print(f"-> wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
