# Running the toolchain on a Raspberry Pi

Yes, the whole `cnc_vibes` Python toolchain runs on a Raspberry Pi 4 or 5 with **64-bit Raspberry Pi OS Lite (Bookworm)**. Your Grbl_ESP32 controller already generates its own step pulses, so the Pi is purely a *workstation + sender* — **no real-time kernel needed**, and LinuxCNC is the wrong abstraction here.

This doc is a research snapshot from 2026-05; verify links before relying on them.

## TL;DR

- **Hardware**: Pi 4 with 4 GB is the sweet spot. 2 GB works if you only stream pre-generated GCode; 8 GB if you want headroom.
- **OS**: Raspberry Pi OS Lite 64-bit (Bookworm). Skip the desktop edition.
- **Install**: `pip install -r requirements.txt` on Pi OS Lite — every C-extension dep has an `aarch64` wheel, no compilation needed. ~1-2 min total.
- **Sender**: **bCNC** (Python/Tk; talks to your ESP32 via `socket://host:port`) or the **FluidNC WebUI** baked into the firmware. **Avoid CNCjs** for ESP32-over-telnet — it's serial-only and requires a fragile `socat` bridge.
- **3D toolpath preview**: keep CAMotics on your desktop and `scp` GCode to the Pi. No CAMotics ARM build exists. For Pi-side preview, embed [ncviewer.com](https://ncviewer.com) in a browser tab.

## Python deps on aarch64

Every C-extension dep ships prebuilt 64-bit ARM wheels — no source compilation:

| Package                    | Wheel tag (verified 2026-05)                    |
|----------------------------|-------------------------------------------------|
| `opencv-python-headless`   | `manylinux_2_28_aarch64`, `manylinux2014_aarch64` (abi3, Py 3.7-3.14) |
| `shapely` 2.1.2            | `manylinux2014_aarch64` cp310-cp314 (bundles GEOS) |
| `Pillow` 12.2              | `manylinux2014_aarch64`, `manylinux_2_28_aarch64` cp310-cp314 |
| `pyserial`, `svgelements`, `pyyaml`, `prompt_toolkit`, `zeroconf` | pure-Python (trivial install) |

Source: `pypi.org/project/<package>/#files`.

> **Why `opencv-python-headless` if we don't do camera work?** OpenCV here is used as a *contour-extraction library*, not a vision library. The `-headless` suffix just drops GUI bindings (no `cv2.imshow`, no X11 dep), which makes the package smaller and Pi-friendly. The three usages in the repo:
> 1. `scripts/cam.py:751` — `cv2.findContours` for rasterizing text glyphs into polygons (`engrave_text` op).
> 2. `lessons/laser/03_jigsaw/geometry.py:373` — same contour-extraction trick for jigsaw letter shapes.
> 3. `lessons/integration/02_snapshot/snapshot.py:115` — the *one* actual camera use (optional, gated on a USB webcam being plugged in).
>
> So even on a Pi with no camera, `opencv-headless` is doing useful work for text/letter contour tracing.

## CAMotics is desktop-only

CAMotics 1.x publishes binaries for Windows, Debian amd64, Fedora, macOS — **no ARM Linux builds** ([camotics.org/download.html](https://camotics.org/download.html)). Source build is GPL-licensed but heavyweight (C++, Cbang, OpenGL) and not worth it.

**Recommended workflow**:

1. Generate GCode on the Pi (or anywhere): `cnc.py cam ...` → `.gcode`
2. Preview on your *desktop*: `cnc.py preview foo.gcode` opens CAMotics
3. `scp foo.gcode pi@<host>:~/jobs/` once it looks good
4. Send from the Pi with bCNC / FluidNC UI

**Lightweight Pi-side previewers** (open in a browser tab on the Pi or any other machine on the LAN):

- [ncviewer.com](https://ncviewer.com) — drop-in browser-based GCode visualizer. Simplest path.
- [nraynaud.github.io/webgcode](https://nraynaud.github.io/webgcode) — older but works.
- `gcode-preview` npm lib — for embedding in your own static page.

OpenBuilds CONTROL is Electron + x86 only; skip on Pi.

## Senders

Picking a sender matters because of how you connect to the controller — your Grbl_ESP32 speaks **raw TCP/telnet over WiFi**, not USB serial.

| Sender | TCP/telnet to ESP32 | Notes |
|---|---|---|
| **bCNC** | ✅ via `socket://host:port` (pyserial-builtin) | Python/Tk, mature, light. Recommended. |
| **FluidNC WebUI / ESP3D** | ✅ (it IS the controller) | Zero install, great for ad-hoc jogs and one-offs. |
| **UGS Platform** | ✅ TCP driver added for FluidNC/ESP32 | Java + OpenJDK. Heavier than bCNC but full-featured. |
| **CNCjs** | ❌ serial-only (needs `socat` bridge — fragile) | Don't use this with an ESP32. The Pi install script at [cncjs/cncjs-pi-raspbian](https://github.com/cncjs/cncjs-pi-raspbian) is the closest thing to a turnkey image. |

Either bCNC or FluidNC's own WebUI is enough for 95% of jobs.

## RAM tiers (headless Pi 4/5)

Pi OS Lite idles at ~100-150 MB; everything below assumes the desktop edition is NOT installed.

| RAM | Use case | Verdict |
|---|---|---|
| 1 GB (Pi 3, Pi Zero 2W) | Sender-only (bCNC streaming a pre-generated `.gcode`) | Works but tight. Generating jigsaw raster GCode will swap. |
| 2 GB (Pi 4) | Sender + occasional `cnc.py cam` one-shots | **Floor.** No raster engrave of photos. |
| 4 GB (Pi 4 / 5) | Full pipeline: `cnc.py cam`, jigsaw raster on multi-MP photo (cv2 + PIL), browser jog UI | **Sweet spot.** Recommended. |
| 8 GB (Pi 4 / 5) | Multiple senders, very large rasters, local 3D previewer in a tab | Comfortable headroom. |
| 16 GB (Pi 5) | Overkill for this workload | Save your money. |

**Skip entirely**: Pi 1, Pi Zero (original), Pi 2. `opencv-headless` won't fit in 512 MB, and the older ARMv6/v7 wheels are inconsistent.

## OS recommendation

| OS | Idle RAM | Comment |
|---|---|---|
| **Raspberry Pi OS Lite 64-bit (Bookworm)** | ~100-150 MB | **Default.** Modern Python 3.11, `raspi-config`, broadest community support. Not "clunky" — the clunkiness is in the *Desktop* edition's PIXEL UI, which Lite doesn't ship. |
| **DietPi** | ~50 MB | Leanest. `dietpi-software` one-shot installer for Python / bCNC / etc. Worth it if you're chasing minimal footprint. |
| **Ubuntu Server 24.04 LTS for Pi** | ~400 MB | Heavier than Pi OS Lite. Only worth it if you want LTS / cloud-init parity with other servers. |

**No CNC-specific turnkey distro** is being actively maintained in 2026. Pi OS Lite + `pip install -r requirements.txt` + bCNC (or the FluidNC WebUI you already have) is what everyone uses.

## Suggested first install

```bash
# On the Pi after first boot (Pi OS Lite Bookworm 64-bit)
sudo apt update && sudo apt install -y python3-pip python3-venv git
git clone <your-repo-url> cnc_vibes
cd cnc_vibes
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install bCNC   # or `apt install bcnc` if your Bookworm has it

# Verify the toolchain runs
python3 cnc.py doctor
python3 cnc.py cam profile --shape circle --diameter 30 \
    --depth 2 --material plywood_baltic_birch_3mm --tool flat_3.175mm_2flute

# Verify the network link to the controller (replace IP)
python3 cnc.py ip          # find machine on the LAN
python3 cnc.py inspect     # if it has a serial subcommand wired up
```

For sending, point bCNC at `socket://<grbl-esp32-ip>:23` (or whatever port your firmware exposes — FluidNC defaults to 23).

## What doesn't work / out of scope

- **CAMotics native** — see above. Preview on desktop.
- **OpenSCAD GUI** — `openscad` package exists for ARM, but the GUI is unpleasant on a Pi. Headless `openscad --export-format svg` is fine for the `openscad_loader` flow.
- **LinuxCNC** — wrong abstraction for a Grbl_ESP32 setup. LinuxCNC generates its own step pulses; your controller already does that. Adding LinuxCNC just adds a layer that gets in the way.
- **Real-time kernel patches** (PREEMPT_RT) — same reason. Not needed.
