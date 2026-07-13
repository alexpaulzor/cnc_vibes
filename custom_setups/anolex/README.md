# custom_setups

Setup-specific configuration that isn't part of the generic `cnc_vibes` tool —
one directory per real machine/environment. Keep your machine's quirks here so
the shared tool stays generic.

Eventually this can grow into a proper plugin / config layer (a setup declares
its profile, sender, discovery method, calibration constants, etc.). For now
it's just a home for the concrete files a specific rig needs.

## anolex/

The author's reference rig: an **Anolex 4030-Evo Ultra 2** router with a
**LaserTree 10W** diode head, driven over GRBL.

- `anolex_4030_evo_ultra2.yaml` — the real machine profile (the generic
  `cnc_vibes/profiles/default.yaml` is a genericized copy of this). Use it with:
  `PROFILE=../custom_setups/anolex/anolex_4030_evo_ultra2.yaml python cnc.py validate ...`
- `RASPBERRY_PI.md` — running the toolchain / sender on a Raspberry Pi.
