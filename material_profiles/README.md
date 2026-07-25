# material_profiles

Shared material profiles for the whole `vibes` monorepo. This is the single
source of truth — edit a value here once and every sub-project picks it up.

## Files

- **`laser_materials.yaml`** — per-material diode-laser cut parameters
  (power / feed / passes). Read by:
  - `jigsawzall/emitter.py`
  - `orpot/emit.py`, `orpot/svg2laser.py`
  - `cnc_vibes/scripts/{cam_cli,laser_cam}.py`
  - `cnc_vibes/lessons/laser/{01_spacer,02_calibration,04_spoilboard}`

Each loader reaches this dir with a relative path from its own location
(`<module>/../material_profiles/laser_materials.yaml`), so there are no copies
to keep in sync.

## Not here (yet)

`cnc_vibes/profiles/materials.yaml` (spindle chipload/DOC tables) still lives in
cnc_vibes because it is used only by cnc_vibes and is not a laser profile. It
could move here too if a second sub-project ever needs it.

The machine/tool config (`cnc_vibes/profiles/{default,tools}.yaml`) is not a
material profile and stays with cnc_vibes.
