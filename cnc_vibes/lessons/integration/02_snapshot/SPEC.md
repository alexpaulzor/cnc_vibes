# Integration 02 — webcam snapshots for setup verification

> **Status: SPEC (future).** Planning doc; no implementation yet, and explicitly lower priority than `01_inspect` and `03_probe-corner`. Captured here so the idea isn't lost.
>
> Standalone Python script that grabs single frames from a USB webcam pointed at the machine bed and writes them as timestamped JPEGs. Used to record "before-cut" and "after-cut" states for each job. Does not depend on Claude or any LLM.

## Goal

Pair the preflight checklist with a visual record. When the operator confirms "stock is clamped," the system also captures a photo. If something goes wrong later, you can look at the before-photo and see what was actually true at run time — not what you remember.

After a cut, a second snapshot records what came out. Over time you build a per-job photo log that's useful for diagnosing failures, comparing material behavior, and remembering which jig setup produced which part.

## What it does (MVP)

Opens a configured USB webcam, grabs one frame, writes it to a path with a timestamp embedded. That's it.

```
python lessons/integration/02_snapshot/snapshot.py \
    --camera 0 \
    --label before-cut \
    --job hole_in_sheet \
    --out logs/snapshots/
```

Output filename: `logs/snapshots/2026-05-24T14-32-01_hole_in_sheet_before-cut.jpg`

## CLI

```
python lessons/integration/02_snapshot/snapshot.py [--camera N|PATH]
                                                   [--label LABEL]
                                                   [--job JOB_NAME]
                                                   [--out DIR]
                                                   [--list]
                                                   [--no-overlay]
```

| Flag | Default | Meaning |
|---|---|---|
| `--camera` | `$CNC_CAMERA` env var, else 0 | OpenCV camera index or device path (`/dev/video0` on Linux). |
| `--label` | `snap` | Free-form label embedded in filename. Typical values: `before-cut`, `after-cut`, `setup`. |
| `--job` | (none) | Job name embedded in filename for cross-reference. |
| `--out` | `./logs/snapshots/` | Output directory; created if missing. |
| `--list` | off | Print available cameras and exit. No frame captured. |
| `--no-overlay` | off | Skip the in-image timestamp/label overlay. |

Default behavior overlays a small timestamp + label corner-overlay onto the captured frame so the photo is self-identifying. `--no-overlay` skips that for raw frames.

## Standalone, not Claude-dependent

Plain CLI. The user runs it from a Windows PowerShell shell as part of their workflow. `cnc.py preflight` (future) could shell out to it to auto-snapshot at checklist confirmation, but that's a `cnc.py` enhancement.

No file format dependencies. Writes a JPEG. That's it.

## Risk surface

Read-only with respect to the machine. The tool doesn't touch the serial port, doesn't issue commands. The only failure modes are:

- Camera not connected / wrong index → clear error, exit 1.
- Out directory not writable → clear error, exit 1.
- USB conflict with another video app → may get a black frame; user should close other apps.

## MVP scope

- Single frame per invocation.
- Single camera at a time.
- JPEG output only (PNG/etc as extension).
- Hardcoded overlay format (timestamp top-left, label top-right).
- No image processing — raw frame plus overlay text.

## Extensions

- **`--continuous` mode**: capture frames at an interval. Useful as a poor-man's time-lapse during a long job. Stops on Ctrl-C.
- **Multi-camera support**: take frames from N cameras simultaneously (useful if you mount one looking down at the bed and one looking horizontally at the spindle).
- **Image comparison**: `--compare PATH` diffs the current frame against a reference and highlights changes. Could catch "the clamp moved between jobs" or "the stock isn't where it usually is."
- **`cnc.py preflight` integration**: call `snapshot.py --label preflight-pass` after the checklist completes, append the photo path to the job's run log.
- **Camera config file**: `lessons/integration/02_snapshot/cameras.yaml` mapping camera IDs to friendly names and roles (bed-overhead, spindle-side, etc).
- **EXIF metadata**: embed job name, label, machine WCS (queried via `inspect.py`) into the JPEG EXIF for searchable archives.

## Why this is "future"

- No camera physically mounted yet. The user needs a bracket + cable management before this tool is useful.
- The first machine-talking tool (`inspect`) and the probing macro (`probe-corner`) close bigger workflow gaps. Snapshots are nice-to-have.
- OpenCV is a meaningful dependency (50+ MB on Windows, native compile). Worth deferring until it's actually needed.

## Dependencies this adds

```
opencv-python >= 4.8       # for capture + JPEG write + overlay text
# OR a lighter alternative:
imageio[ffmpeg] >= 2.30    # capture
Pillow >= 10               # JPEG write + overlay
```

The `imageio + Pillow` combination is lighter than full OpenCV (~10 MB vs ~50 MB) and probably sufficient for one-shot frame capture. OpenCV becomes worth it only if extensions add image processing.

Decision deferred to implementation time.

## What it does NOT do

- Does **not** monitor video streams or run vision algorithms. Single-shot frame capture only.
- Does **not** track motion or trigger on events. The operator (or `cnc.py preflight`) decides when to snapshot.
- Does **not** replace a real safety camera. If you need to detect a fire mid-cut, use a dedicated CV process with a watchdog — not this tool.
- Does **not** drive the machine.

## Files this lesson will create

```
lessons/integration/02_snapshot/
  SPEC.md           ← this file
  README.md         ← user-facing once implemented
  snapshot.py       ← the script (argparse + cv2 or imageio)
  cameras.yaml      ← optional, friendly camera naming
  tests/
    test_filename.py ← unit tests on the timestamp/label filename formatter
```

Tests cover the filename construction (pure function) without needing a real camera. Integration testing requires a connected camera and is manual.
