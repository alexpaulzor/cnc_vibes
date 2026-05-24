# Integration 02 — snapshot (webcam stills for setup verification)

> Standalone Python tool. Captures a single image from a USB webcam (or processes an existing image file) and writes a timestamped, labeled JPEG. Used as a pre/post-cut visual record paired with the preflight checklist.
>
> See [SPEC.md](SPEC.md) for design rationale.

## Why this exists

The preflight asks "is the clamp clear of the toolpath?" but can't verify it. A camera can. Snap a frame before the cut, snap another after, and you've got a visual log of every job.

This is **not a real-time vision watchdog** — the script captures one frame per invocation. For mid-job monitoring (catch fire, catch tool break) you need a dedicated CV process; that's out of scope.

## Usage

```
# Capture from USB camera 0 (requires opencv-python)
python lessons/integration/02_snapshot/snapshot.py \
    --camera 0 \
    --label before-cut \
    --job hole_in_sheet \
    --out logs/snapshots/

# Process an existing image (no camera dep — useful for testing)
python lessons/integration/02_snapshot/snapshot.py \
    --source path/to/existing.jpg \
    --label after-cut \
    --job hole_in_sheet

# Raw frame without overlay
python lessons/integration/02_snapshot/snapshot.py \
    --camera 0 --no-overlay --label test
```

| Flag | Default | Meaning |
|---|---|---|
| `--camera N` | — | USB camera index. Requires `opencv-python`. |
| `--source PATH` | — | Existing image file. No camera dependency. |
| `--label LABEL` | `snap` | Free-form label; embedded in filename and overlay. |
| `--job JOB` | — | Job name for cross-reference. |
| `--out DIR` | `logs/snapshots/` | Output directory; created if missing. |
| `--no-overlay` | off | Skip the timestamp/label overlay; save the raw frame. |

`--camera` and `--source` are mutually exclusive; pass exactly one.

## Output

Filename: `<ISO8601-timestamp>_<job>_<label>.jpg`
e.g. `2026-05-24T14-32-01_hole_in_sheet_before-cut.jpg`

Overlay (unless `--no-overlay`): timestamp in the top-left corner, label (or `<job>/<label>` if job is set) in the top-right corner. Both have black backgrounds for legibility on bright surfaces.

## Setup

```
python -m pip install Pillow             # always required
python -m pip install opencv-python-headless  # only if using --camera
```

`Pillow` is added to `requirements.txt`. `opencv-python(-headless)` is optional and only loaded when `--camera` is used — `--source` mode works without it.

## Why opencv vs Pillow alone

Pillow handles image processing and JPEG output cleanly but does not capture from cameras. `opencv-python-headless` is the smallest dependency that does USB camera capture portably across Windows / macOS / Linux. The headless variant skips ~80 MB of GUI deps you don't need.

For a true minimum-deps setup that skips OpenCV entirely, use `--source` and capture the camera frame with some external tool (e.g. `fswebcam` on Linux, `imagesnap` on macOS, `ffmpeg` everywhere) then pass the result to `snapshot.py` for labeling.

## Integration with `cnc.py preflight` (future)

Once Int-02 is part of the standard workflow:

- `cnc.py preflight` calls `snapshot.py --camera $CNC_CAMERA --label preflight-confirmed --job <name>` after the checklist completes.
- The snapshot is appended to a per-job manifest as `before_cut_image: logs/...jpg`.
- After cut completion, `cnc.py post-cut` captures another snapshot with `--label after-cut`.

Not wired up yet; the snapshot tool stands alone for now.

## What it does NOT do

- Does not run a video stream or do continuous monitoring.
- Does not perform image analysis (no "is the stock present?" CV logic).
- Does not interact with the machine at all (no serial port).
- Does not replace a real safety camera with watchdog software.

## Extensions

- **Multi-camera** capture (top-down + spindle-side, in one invocation).
- **Time-lapse mode**: `--continuous --interval 30s` captures a frame every 30 s during a long job.
- **EXIF metadata**: stuff job name, machine WCS (queried via `grbl_inspect`), and feed/spindle into the JPEG EXIF for searchable archives.
- **Image diff vs baseline**: `--compare PATH` highlights changes from a reference frame.
- **Camera config file**: friendly names mapped to indices (e.g. `bed-overhead = 0`, `spindle-side = 2`).

## Status

Implemented and tested (13 unit tests covering filename construction, sanitization, file-source loading, overlay composition, end-to-end via source file). Camera capture path requires manual testing on a real machine with a USB camera attached.
