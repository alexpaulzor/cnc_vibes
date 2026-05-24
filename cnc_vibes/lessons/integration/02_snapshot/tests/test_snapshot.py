"""Tests for snapshot.py — filename construction and overlay pipeline.

Pillow is required for the overlay tests; opencv-python is not (the
camera path is integration-tested only).
"""

import datetime as dt
import sys
from pathlib import Path

import pytest

LESSON_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LESSON_DIR))

from snapshot import (  # noqa: E402
    _sanitize,
    add_overlay,
    load_from_file,
    make_filename,
)


TS = dt.datetime(2026, 5, 24, 14, 32, 1)


# ---------------------------------------------------------------------------
# make_filename
# ---------------------------------------------------------------------------


def test_filename_with_job_and_label():
    assert (
        make_filename(TS, "hole_in_sheet", "before-cut")
        == "2026-05-24T14-32-01_hole_in_sheet_before-cut.jpg"
    )


def test_filename_without_job():
    assert make_filename(TS, None, "snap") == "2026-05-24T14-32-01_snap.jpg"


def test_filename_sanitizes_unsafe_chars():
    assert "?" not in make_filename(TS, "weird?job", "?label")
    assert "/" not in make_filename(TS, "weird/job", "?label")


def test_filename_respects_ext():
    assert make_filename(TS, None, "snap", ext="png").endswith(".png")


def test_sanitize_allows_alnum_and_safe_chars():
    assert _sanitize("foo_bar-baz.1") == "foo_bar-baz.1"


def test_sanitize_replaces_unsafe():
    assert _sanitize("a/b\\c?d") == "a_b_c_d"


# ---------------------------------------------------------------------------
# load_from_file + overlay (requires Pillow)
# ---------------------------------------------------------------------------


def _make_test_image(tmp_path: Path) -> Path:
    """Create a small RGB test image on disk and return its path."""
    from PIL import Image

    img = Image.new("RGB", (320, 240), color=(128, 128, 128))
    p = tmp_path / "test_input.jpg"
    img.save(p, "JPEG")
    return p


def test_load_from_file_happy_path(tmp_path):
    p = _make_test_image(tmp_path)
    image = load_from_file(p)
    assert image.size == (320, 240)
    assert image.mode == "RGB"


def test_load_from_file_missing(tmp_path):
    with pytest.raises(SystemExit, match="not found"):
        load_from_file(tmp_path / "missing.jpg")


def test_overlay_does_not_crash(tmp_path):
    p = _make_test_image(tmp_path)
    image = load_from_file(p)
    overlaid = add_overlay(image, TS, "before-cut", job="hole_in_sheet")
    assert overlaid.size == (320, 240)


def test_overlay_preserves_image_size(tmp_path):
    from PIL import Image

    img = Image.new("RGB", (1920, 1080), color=(0, 0, 0))
    overlaid = add_overlay(img, TS, "test")
    assert overlaid.size == (1920, 1080)


def test_overlay_writable_to_jpeg(tmp_path):
    p = _make_test_image(tmp_path)
    image = load_from_file(p)
    overlaid = add_overlay(image, TS, "before-cut")
    out = tmp_path / "out.jpg"
    overlaid.save(out, "JPEG")
    assert out.exists()
    assert out.stat().st_size > 0


def test_overlay_handles_no_job():
    from PIL import Image

    img = Image.new("RGB", (320, 240), color=(0, 0, 0))
    # Should not crash with job=None
    add_overlay(img, TS, "snap", job=None)


# ---------------------------------------------------------------------------
# End-to-end via the file source (no camera needed)
# ---------------------------------------------------------------------------


def test_end_to_end_via_source_file(tmp_path):
    src = _make_test_image(tmp_path)
    image = load_from_file(src)
    overlaid = add_overlay(image, TS, "before-cut", job="hole_in_sheet")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    fname = make_filename(TS, "hole_in_sheet", "before-cut")
    out_path = out_dir / fname
    overlaid.save(out_path, "JPEG", quality=85)
    assert out_path.exists()
    assert out_path.stat().st_size > 1000  # JPEG with overlay should be non-trivial
