# raster/ — dormant photo-engrave pipeline

**Status: DORMANT / unsupported.** Raster photo-engraving was dropped from the
jigsawzall MVP. It still works but is fully de-wired from the main `jigsaw.py`
CLI (no `raster` subcommand) and is not maintained.

## Running it

Run the standalone CLI directly (from the `jigsawzall/` package root):

```
python3 raster/raster_cli.py --test-pattern --size small
python3 raster/raster_cli.py --image kitten.jpg --size small --mode halftone
```

It writes three GCode files (`*_raster.gcode`, `*_cut.gcode`, `*_full.gcode`)
to the jigsawzall `build/` dir and an encoded preview PNG to `raster/figs/`.

## Layout

- `encoder.py` — image load / halftone dither / grayscale quantize (PIL only).
- `raster_cli.py` — standalone CLI (`main(argv) -> int`).
- `tests/test_raster.py` — encoder + raster-emitter tests.
- `RASTER_RESEARCH.md` — background notes.
- `figs/` — sample raster previews.

The raster *emitter* functions (`emit_raster_gcode`, `raster_only_gcode`,
`combined_raster_and_cut`) still live in the package's `emitter.py` — they are
dormant there and add no dependencies.

## Dependencies

Encapsulating raster is about keeping the MVP surface clean, **not** about
dropping dependencies. `opencv`/`numpy` remain **core** deps of jigsawzall via
`geometry.py`'s contour tracing (`cv2.findContours`). Raster itself only needs
PIL.
