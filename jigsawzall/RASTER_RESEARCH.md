# Photo Raster Engraving — Research Brief

Everything the team needs to succeed at grayscale-photo → laser G-code
**before** burning real material, targeted at *our* rig: a **weak ~10W diode
(LaserTree)** on a **GRBL Anolex 4030-Evo** in **laser mode `$32=1`** (laser
fires only while moving; `G4` dwells produce no beam — see ALGORITHMS.md R9).
We deliberately do **not** use LightBurn; the goal is a home-grown, open-source
Python pipeline (Pillow + numpy are already in `requirements.txt`).

> Scope: this is the "why + what" companion to a future `raster.py` emitter,
> mirroring how `ALGORITHMS.md` backs `geometry.py`/`emitter.py`. It closes
> TODO **T1** (raster engrave characterization).

Every non-obvious claim carries a source URL. A few widely-repeated "facts"
turned out to be lore; those are flagged **[premise correction]** so we don't
bake them into code.

---

## 0. TL;DR (read this, then the rest)

- **Dither, don't grayscale — for the MVP.** A weak diode reliably does exactly
  one thing: burn a single full-power dot. Dithering encodes tone as dot
  *density*, which sidesteps the material's non-linear, narrow power→darkness
  curve entirely. Grayscale power-modulation needs a calibrated LUT and real
  dynamic range we don't have yet. Use **Floyd–Steinberg (MVP)**, upgrade to
  **Jarvis/Stucki** for photos.
- **The OSS landscape is a real gap but the primitives are all free.** No patent
  moat. Best existing free tools: **LaserGRBL** (Windows-only) and **MeerK40t**
  (cross-platform, richest dither menu, but low-maintenance). Building a small
  Python CLI in the `image2gcode` mold + a dither stage is entirely reasonable
  and fits our repo — we already own the emitter conventions.
- **Interval = focused spot size** (~0.1 mm / 254 DPI baseline). Resample the
  image to that DPI so pixel pitch == scan pitch.
- **M4 dynamic power + `$32=1`** auto-solves the accel/decel edge-darkening that
  plagues a low-power, low-accel machine. Add overscan as belt-and-suspenders.
- **Top 5 snags:** (1) interval↔DPI mismatch banding, (2) char/flare from
  overpower, (3) focus drift over a warped/large panel, (4) bidirectional
  ghosting (mechanical backlash first, timing offset second), (5) soot staining
  + low substrate contrast (cardboard especially).

---

## 1. Core algorithms: grayscale photo → laser G-code

### 1.1 The two ways to render tone

There are only two fundamental approaches; they differ in *which physical
variable encodes gray*:

- **Grayscale (power modulation):** each pixel maps to a laser power (S value).
  Tone = **dot darkness**. Requires a reliable, monotonic power→darkness curve.
  LightBurn's Grayscale mode "Varies power output as a percentage between Min and
  Max power" (black→Max, white→Min):
  https://docs.lightburnsoftware.com/latest/Reference/CutSettingsEditor/ImageMode/
- **Dithering (1-bit):** every fired dot is identical (full-power ON) or absent.
  Tone = **dot density**, decoupled from the power-response curve. 1-bit
  dithering can "convincingly represent a full-range photograph using nothing but
  dots" because "the average color across a small area matches the original":
  https://www.grayscaleimage.org/posts/what-is-dithering ·
  https://en.wikipedia.org/wiki/Dither

A concrete oscilloscope measurement from the LightBurn forum makes the physical
difference vivid: with dithering "the duty cycle remained same … but as the laser
approached the darker areas you see the frequency change," whereas with grayscale
"the duty cycle varied from 50% (set power) to less than 10%." On the same
thread: 50% of a weak 4W diode at slow speed is only "a whopping 2 watts,"
producing brown/sepia rather than black — i.e. a weak diode *compresses the tonal
range*: https://forum.lightburnsoftware.com/t/eye-2-jarvis-vs-greyscale/14699

### 1.2 Error-diffusion kernels (exact matrices)

`X` = current pixel; error propagates left→right, top→bottom; divisor shown.
Numeric source: https://tannerhelland.com/2012/12/28/dithering-eleven-algorithms-source-code.html
(cross-checked against Wikipedia).

| Kernel | Divisor | Weights |
|---|---|---|
| **Floyd–Steinberg** | 16 | `X 7` / `3 5 1` |
| **Jarvis-Judice-Ninke** | 48 | `X 7 5` / `3 5 7 5 3` / `1 3 5 3 1` |
| **Stucki** | 42 | `X 8 4` / `2 4 8 4 2` / `1 2 4 2 1` |
| **Atkinson** | 8 | `X 1 1` / `1 1 1` / `1` (only ¾ of error diffused, ¼ discarded) |
| **Burkes** | 32 | `X 8 4` / `2 4 8 4 2` |
| **Sierra-3** | 32 | `X 5 3` / `2 4 5 4 2` / `2 3 2` |
| **Sierra Lite** | 4 | `X 2` / `1 1` |

Behavioral facts:
- **Floyd–Steinberg** diffuses the full error to 4 neighbors; 50% gray renders as
  a checkerboard: https://en.wikipedia.org/wiki/Floyd%E2%80%93Steinberg_dithering
- **JJN** spreads error over 12 pixels — "coarser but has fewer visual artifacts":
  https://en.wikipedia.org/wiki/Dither . **Stucki**'s weights are powers of two
  (bit-shiftable → fast) and its output is near-identical to JJN.
- **Atkinson** diffuses only ¾ of the error → "a more localized dither, at the
  cost of lower performance on near-white and near-black areas," which "may be
  regarded as more visually desirable" (higher contrast, can blow out extremes):
  https://en.wikipedia.org/wiki/Atkinson_dithering
- Error diffusion inherently **edge-enhances**, trading gray-level accuracy for
  apparent resolution: https://en.wikipedia.org/wiki/Error_diffusion

**Directional artifacts ("worming"):** the hobby term isn't in primary sources,
but the phenomenon is real — 1-D error diffusion "tends to have severe image
artifacts that show up as distinct vertical lines," and FS's diagonal
down-and-right propagation spawns "an entire cone of jittering artifacts …
downwards and to the right":
https://bisqwit.iki.fi/story/howto/dither/jy/ . **Serpentine scanning**
(reverse error-propagation direction every row) reduces it — cheap to add and
worth doing since we scan bidirectionally anyway.

### 1.3 Ordered/Bayer and blue-noise

- **Bayer** (2×2 = ¼·[[0,2],[3,1]], 4×4 recursive) is a per-pixel point
  operation with no error propagation — fast, but a repeating crosshatch grid.
  Good for *large solid fills* where diffusion causes artifacts, less good for
  photos: https://en.wikipedia.org/wiki/Ordered_dithering
- **Blue noise** (Ulichney void-and-cluster, 1993) has weak low-frequency energy
  and isotropy, so after the eye blurs it, it "looks almost entirely uniform" —
  the least distracting dither, but more complex to generate:
  https://momentsingraphics.de/BlueNoise.html

### 1.4 Which wins on a weak diode, and why (the synthesis)

**Dithering, for the MVP.** Reasons, strongest first:

1. **Binary output matches the hardware.** A weak diode reliably burns one
   thing — a full-power dot — which is exactly what dithering asks for. This
   aligns with our standing rule (MEMORY: "Laser: max power for cuts"; ALGORITHMS
   R8) and needs no LUT.
2. **Grayscale needs dynamic range we don't have.** LightBurn's developer:
   grayscale "really only works with material like wood, where the amount it
   burns will vary," and "even then it's a big pain … compared to using
   dithering":
   https://forum.lightburnsoftware.com/t/grayscale-not-previewing-or-burning-compared-to-dithering/142002 .
   Grayscale fidelity is capped by "the number of intermediate shades that your
   material can predictably produce":
   https://forum.lightburnsoftware.com/t/greyscale-vs-dithering-with-a-diode-laser/106876
3. **Grain wrecks grayscale** because "different parts of the grain can be more
   (or less) susceptible":
   https://forum.lightburnsoftware.com/t/can-i-get-shades-of-gray-by-laser-power/77100
4. **Data volume:** grayscale is ~one instruction per pixel — heavier on the
   controller/streamer.

**[premise correction] "Coarse spot" is a *minor* driver, not the main one.**
A *well-focused* diode (~0.08 mm) can equal or beat a typical CO2 spot
(0.10–0.20 mm):
https://forum.lightburnsoftware.com/t/question-about-line-interval/177991 .
The stronger reasons to dither are the binary output + narrow, non-linear tonal
range on wood + grain inconsistency. (The diode beam is also *rectangular*, not
round, and smallest at the focal surface: https://www.sculpfun.com/blogs/news/settings-guide )

**Grayscale's legitimate niche (later upgrade):** materials with a consistent,
predictable power response (leather, glass, smooth coatings) — and only once we
have a calibration LUT (§2). LightBurn image-mode guidance, for reference:
Jarvis "usually the best choice for … photo images"; plain Dither (FS) also good
for photos; Stucki faster but weaker midtones; Atkinson preserves detail but
struggles at extremes; Ordered for solid fills:
https://docs.lightburnsoftware.com/latest/Reference/CutSettingsEditor/ImageMode/

### 1.5 The gamma / power-response problem and calibration LUTs

**Why the response is non-linear (a threshold→saturation S-curve):** below a
marking threshold the laser leaves no mark and light tones "drop out" entirely
rather than fading:
https://www.freefall-laser.com/lasercuttingblog/2019/11/27/lower-that-resolution .
At the top, "diminishing returns emerge as pulse overlap saturates energy
deposition":
https://www.qijunlaser.com/blog/how-pulse-frequency-settings-affect-fiber-laser-marking-quality .
LightBurn's own explainer: beyond a point "increasing power or decreasing speed
has limited usefulness":
https://docs.lightburnsoftware.com/2.1/Explainers/SpeedVsPower/
*(The full "S-curve" framing is a synthesis of separately-sourced threshold and
saturation ends — no single page states it whole.)*

Darkness is governed by **energy per unit area** = power × dwell × coverage, not
power alone: https://sdgloballaser.com/laser-power-vs-speed/ . Line interval
co-determines it (too-high interval needs more power for the same darkness;
too-low goes "dark and muddy"):
https://docs.lightburnsoftware.com/2.1/Reference/IntervalTest/

**The only linear link in the whole chain is the firmware S→PWM map** (GRBL
"linearly relates the max-min RPMs to 5V-0.02V PWM pin output in 255 equally
spaced increments":
https://github.com/gnea/grbl/wiki/Grbl-v1.1-Configuration ). Everything
downstream (the material) is non-linear — which is exactly why you **pre-warp the
source image**, and why gamma correction (`V_out = A·V_in^γ`) is a power law
matched to "the non-linear manner in which humans perceive light":
https://en.wikipedia.org/wiki/Gamma_correction

**Building the calibration artifact:**
- **Power × Speed grid** (see §4) — the standard way to find the working box.
- **image2gcode** ships gradient calibration images (`gradient_diagonal`,
  `gradient_banding` via `--genimages`) and instructs you to "calibrate the white
  balance by varying head speed and maximum laser power" — the closest verified
  concrete "engrave a ramp, balance the response" recipe:
  https://github.com/johannesnoordanus/image2gcode
- **Honest gap:** the precise "engrave a stepped gray wedge → densitometer each
  patch → fit curve → apply the inverse as a LUT" recipe was **not** found on a
  fetchable page (it lives mostly on Reddit, which was bot-blocked). For our MVP
  we don't need it — dithering makes it optional. For the grayscale upgrade,
  build it ourselves: engrave an N-step wedge, photograph flat-lit, measure mean
  pixel value per patch, invert to a 256-entry LUT.

**Applying tone correction — order matters: tone-correct the source, THEN
dither.** `didder` documents linearizing the image and adjusting
saturation/brightness/contrast *before* dithering:
https://github.com/makeworld-the-better-one/didder . Because error diffusion
makes local dot density approximate the input gray, linearizing first is what
makes the density map perceptually correct. Practical prep clamps black/white
points to the histogram edges (GIMP Levels / numpy) before engraving:
https://mr-carve.com/blogs/featured-blog/9-steps-to-prepare-a-photo-for-laser-engraving-free .
Makers routinely lift mid-tones (gamma ≈0.7) so shadows don't crush:
https://forum.lightburnsoftware.com/t/engraving-portrait-plywood-poplar/51294

**[premise correction]** LaserGRBL has **no gamma slider** (only Brightness,
Contrast, White-Clip); its "Line to Line" is the *name of its grayscale mode*,
not a gamma feature. Explicit Gamma is a LightBurn thing:
https://lasergrbl.com/usage/raster-image-import/import-parameters/

### 1.6 Scan strategies

**Line spacing: LPI/DPI vs kerf/spot.** Identity: **interval(mm) = 25.4 ÷ DPI**
(127 DPI = 0.2 mm; 254 DPI = 0.1 mm; 318 DPI = 0.08 mm):
https://dithx.optlasers.com/en/dithx-dpi-lpi-line-interval.html . **Golden
rule: interval = focused spot size.** interval > spot → gaps/banding;
interval < spot → overlap/overburn/blur; interval = spot → sharp (same source).
You cannot engrave a feature smaller than the beam; higher DPI just overlaps the
same spot into a "charred mess":
https://www.1laser.com/blogs/topic/lpi-and-dpi — and this over-sampling
"is slightly more true when using dithering than … grayscale" because merged
dots destroy the density gradient:
https://forum.lightburnsoftware.com/t/dpi-settings-for-raster/6223 .
LightBurn's official photo recommendation is only **120–300 DPI (0.08–0.2 mm)**
with "higher isn't better":
https://docs.lightburnsoftware.com/2.1/Guides/PerfectImageEngraving/ . Diode
consensus sweet spot: **0.08–0.1 mm**:
https://blazexlaser.com/blogs/news/dpi-vs-line-interval-in-laser-engraving-what-really-impacts-quality

**Bidirectional scanning + the offset artifact.** Bidirectional (zig-zag) is
faster than unidirectional, but a roughly constant firing/mechanical delay
produces a positional error that grows linearly with speed, so alternating rows
land offset → "ghosted"/skewed edges:
https://docs.lightburnsoftware.com/legacy/ScanningOffsetAdjustment.html .
At 500 mm/s a 1 ms delay = 5 dots (0.5 mm) of skew — hence per-speed calibration:
https://docs.lightburnsoftware.com/2.1/Guides/ScanningOffsetAdjustment/ .
**Critical:** the offset table only fixes *timing* shift; loose belts, backlash,
and Y-accel wobble mimic the same ghosting and must be fixed first — and on a
CNC-converted machine (ours) those are the more likely culprit:
https://forum.lightburnsoftware.com/t/ghosting-double-engraving-help/8812

**Overscan / acceleration lead-in.** "Dwell time = energy per area," so at line
ends where the head decelerates, "the edges will absorb more power than the
center, and burn darker":
https://docs.lightburnsoftware.com/latest/Explainers/Overscanning/ .
"Engraving quality exists only inside the constant velocity zone":
https://www.rabbitlaserusa.com/high-speed-laser-engraving-physics-control .
Overscan adds laser-off run-in/run-out so firing happens only at full speed —
**especially needed on limited-power machines** like our diode:
https://forum.lightburnsoftware.com/t/when-to-use-the-overscanning-option/3392 .
Amounts: LightBurn default **2.5% of speed**; GRBL guidance 2–5 mm ("start with
3 mm," more for higher speed / lower accel):
https://rayforge.org/docs/features/overscan/ . This is the raster analog of our
existing **overburn / lead-in** concept (ALGORITHMS A7/R10) — same physics,
different axis.

**Whitespace handling. [premise correction]** LightBurn does **not** truly
"jump over blank pixels" — its *Fast Whitespace Scan* just boosts head speed
across blank spans (~54% time saving in one case):
https://forum.lightburnsoftware.com/t/overscan-vs-fast-whitespace-scan/43417 .
For our generator, the equivalent is trivial and worth doing: within a row,
**skip leading/trailing white pixel runs** (rapid `G0` to the first ON dot,
laser off after the last), and skip fully-white rows entirely. image2gcode
already does exactly this run-length "white-skip" optimization.

### 1.7 GRBL specifics

Canonical spec: the official gnea/grbl v1.1 wiki.

**M4 dynamic vs M3 constant.** Verbatim
(https://github.com/gnea/grbl/wiki/Grbl-v1.1-Laser-Mode):
- **M3:** "keeps the laser power as programmed, regardless if the machine is
  moving, accelerating, or stopped."
- **M4:** "will automatically adjust laser power based on the current speed
  relative to the programmed rate … ensures the amount of laser energy along a
  cut is consistent even though the machine may be stopped or actively
  accelerating."

**How M4 solves accel/decel darkening:** energy per mm ≈ power ÷ feed. A real
machine can't change velocity instantly (limited by `$120–$122` accel), so at row
start/end and corners the actual feed drops → at fixed S, more energy per mm →
scorched edges. M4 scales the PWM S output proportionally to *actual* feed vs
*programmed* feed — half speed → half power, stopped → zero — so "you can get
super clean and crisp results, even on a low-acceleration machine!" It assumes
"laser power is linear with speed and the material," and it turns the laser off
when stationary. **This is the single most important GRBL choice for raster on
our low-accel diode → use M4.** (Note: image2gcode's author prefers M3 for
grayscale *depth* work, but for our dithered on/off + accel-heavy raster, M4's
edge behavior wins. MeerK40t defaults M4 for raster.)

**`$32` laser mode** (https://github.com/gnea/grbl/wiki/Grbl-v1.1-Configuration):
`$32=1` → "The spindle PWM pin will be updated instantaneously through each
motion without stopping" (flows through per-move S changes — essential for
raster). `$32=0` inserts a spin-up pause on every S change. The laser "will only
turn on when Grbl is in a G1, G2, or G3 motion mode"; **G0 rapids and G38 probes
never fire** — this is what makes white-run skipping (G0) safe. Confirms our R9:
no motion = no beam.

**S values / PWM.** `$30` = S for full output (default 1000, so S500 = 50%
duty); `$31` = min; `S0` disables. Our `_power_s()` already does percent→S0–1000
(`scripts/laser_cam.py`), consistent with the default `$30=1000`.

**How raster generators emit lines (the pattern to copy).** Header sets modes
once (`$32=1` assumed on controller, `M4`, `G21 G90`), then **one feed-bearing
`G1` per raster row**, with **S modulated per pixel-*run* (run-length), not per
pixel**:
- **LaserGRBL** (`GrblFile.cs`): advances X to where the pixel value changes and
  writes one `X<coord> S<power>` per constant run; arms with `<LaserOn> S0`
  first: https://github.com/arkypita/LaserGRBL
- **image2gcode**: header defaults to **M4**, then per row: `G0` to position →
  `G1 F<speed>` → coordinate + S written only when power changes; "pixels with
  same intensity are drawn with one gcode move command":
  https://github.com/johannesnoordanus/image2gcode

For our **dithered MVP** the S value is constant (full power); a row is just:
rapid to first ON dot, `M4 S<full>`, `G1 X.. F..` across the ON run, `M5` (or
S0) across white, repeat — bidirectional, serpentine.

---

## 2. Is this a gap in open source, or already solved?

**Verdict: no patent moat; the tech is unencumbered and decades old. It's a
labor/incentive gap — solved *commercially* by a cheap dominant incumbent
(LightBurn), so nobody polished a free cross-platform rival.**

- **No moat.** The whole pipeline (error-diffusion dither + per-pixel PWM power)
  is public-domain math: Floyd–Steinberg 1976, JJN 1976, Stucki 1981. The most
  on-point grayscale-laser patent (Troitski US20060235564A1, modulating point
  density for gray) was **abandoned, never granted**; his granted one
  (US6605797B1) was limited to *subsurface glass* and expired ~2019. Live
  patents (Glowforge US10737355B2 spot-spacing; Kodak/EO multi-beam flexo heads)
  are narrow and trivially avoided by a single-beam dither+PWM design.
  *(Caveat: citation-graph traversal, not a formal freedom-to-operate search.)*
- **The enabling primitive is open:** GRBL laser mode + M4 dynamic power is
  exactly what raster needs, and grbl is GPL.
- **It's economics.** LightBurn (Core $99 / Pro $199) is the de-facto standard
  with a polished image engine; community sentiment is "just buy it." Contributor
  attention in OSS flows to *machine control* (LaserGRBL, MeerK40t), not
  *image-processing UX*.

### OSS tool survey (what each does, quality, limits)

| Tool | Lang / platform | Dither? | Grayscale PWM? | GRBL/M4 | Status | Notes |
|---|---|---|---|---|---|---|
| **LaserGRBL** | C#/.NET, **Windows only**, GPLv3 | **Yes, 9** (FS default, Atkinson, Burkes, Jarvis, Stucki, Sierra 2/3/Lite, Random) | Yes ("Line2Line") | Yes, M3/M4 selectable | Active (v7.14.1, 2025) | Best free photo feature set; Windows-only is the dealbreaker for us. https://github.com/arkypita/LaserGRBL |
| **MeerK40t** | Python (numpy/Pillow/numba), wx, cross-platform, MIT | **Yes, 13** (FS, Atkinson, Jarvis, Stucki, Burkes, Sierra family, Shiau-Fan, **Bayer**, **Bayer-blue-noise**) | Essentially no (1-bit; dormant `is_depthmap`) | Yes, **M4 default** | Active but self-declared low-maintenance (0.9.8100, 2025) | **Study its code.** Deepest raster/dither codebase; rich raster wizard (resample-DPI, gamma, unsharp, halftone) in `imagetools.py`; traversal (bidir, path-opt, overscan, skip-white, spot-overlap comp) in `tools/rasterplotter.py`. https://github.com/meerk40t/meerk40t |
| **J Tech Photonics Laser Tool** | Python, Inkscape ext | No | No | Yes | v2.5.1 (2022) | **Vector paths only** — not a photo tool. https://github.com/JTechPhotonics/J-Tech-Photonics-Laser-Tool |
| **lasertools** (Inkscape) | Python, Inkscape ext, GPL-2 | No | No | Yes | Active (push 2026-01) | Perimeter + parallel infill; **vector only**. https://github.com/ChrisWag91/Inkscape-Lasertools-Plugin |
| **raster2laser** | Python, **Inkscape** ext | No (B/W thr, B/W random, **Halftone**) | **Yes (PWM)** | Yes | ~2021, Inkscape 0.9x | **[correction]** It's an *Inkscape* extension, not GIMP; author "Adliesio" does not exist. Modes are threshold/halftone/grayscale, NOT FS/Jarvis/Stucki. https://github.com/305engineering/Inkscape (1.x fork: https://github.com/bferrarese/raster2laser_gcode) |
| **gimp-laser-plugin** | Python, GIMP 2.8/2.10, GPL-3 | No | **Yes** | Yes | ~2022 | The *actual* GIMP raster→gcode plugin (true raster + grayscale S-map). https://github.com/buildbotics/gimp-laser-plugin |
| **LaserWeb4** | JS/Electron, cross-platform, AGPL-3 | **FS only** | Yes (default) | Yes (M4 via user gcode) | Dev commits 2026, tags stale | Raster works; only one dither. https://github.com/LaserWeb/LaserWeb4 |
| **CNCjs** | JS, cross-platform, MIT | — | — | sender only | Very active | **[correction] Not a raster tool** — a G-code *sender/controller* (we run this on the Anolex Pi). Author gcode elsewhere, stream with CNCjs. https://github.com/cncjs/cncjs |
| **image2gcode** | Python CLI, MIT | **No** (relies on power-mod + contrast) | **Yes** (intensity→S, white-skip) | Yes, M3 default / M4 opt | Active (v2.9.17, 2026) | **Best structural model for our CLI.** Run-length row emission, gradient calibration images. https://github.com/johannesnoordanus/image2gcode |
| **Scorchworks** (Dmap2gcode, F-Engrave) | Python, GPLv3 | No | No (Z-depth carve / Potrace vector) | No M4 | Active | **[correction]** Open-source (not closed freeware); Dmap2gcode is milling depth-map, F-Engrave is vector — **neither is a photo raster engraver**. https://www.scorchworks.com/ |
| **svg2gcode** | Rust (sameer) / Python (PadLex) | — | — | Yes | Active / stale | **Vector paths only, no raster** (answers the explicit question). https://github.com/sameer/svg2gcode |
| **PixelToLaser** | — | — | — | — | **Not found** | No resolving site/repo; likely misremembered. Nearest: Pixample (advertises Atkinson), Pixel2Lines. |
| **grbl_image** | — | — | — | — | **No such repo** | Historical ancestor villamany/3dpBurner-Image2Gcode (C#, dead 2015), superseded by LaserGRBL. |

**Dithering libraries (the raster step):**
- **Pillow**: native dithering is **Floyd–Steinberg only** (`Image.convert("1")`);
  ORDERED/RASTERIZE enum values are **not implemented**.
  https://pillow.readthedocs.io/en/stable/reference/Image.html
- **numpy**: substrate for hand-rolling any kernel (what MeerK40t does).
- **hitherdither**: richest pure-Python set (FS, Jarvis, Stucki, Burkes, Sierra,
  Atkinson, Bayer, cluster-dot). **Git-install only, dormant (~2023)**. This is
  the code MeerK40t borrowed its coefficient maps from.
  https://github.com/hbldh/hitherdither
- **didder**: Go CLI, ~15 algorithms + custom matrices, actively good — but you
  shell out to it. https://github.com/makeworld-the-better-one/didder

**Practical conclusion for us:** a polished cross-platform OSS photo-raster
engraver is a genuine gap in the *open-source* landscape (not the market). Since
we already own our emitter conventions (`laser_cam.py`, `emitter.py`, the
validator, previews), the cheapest correct path is a **small home-grown Python
module** in the `image2gcode` structural mold + a dither stage (Pillow FS for the
MVP; port hitherdither's Jarvis/Stucki maps to numpy for the upgrade). No new
heavy dependency required — Pillow + numpy are already in `requirements.txt`.

---

## 3. Concrete recommendation for THIS project

**MVP: 1-bit Floyd–Steinberg dither at a single calibrated fixed power** (S at our
usual 100% for cardboard cuts is *too hot* for engraving — see §4; the engrave
"fixed power" is a calibrated lower value). This is *calibration-tolerant*: dot
density carries tone, so we don't need a LUT to get a recognizable image.
Grayscale power-mod is a **later upgrade** once a LUT exists.

### Processing pipeline (all doable with Pillow + numpy, already installed)

1. **Load + grayscale.** `Image.open(...).convert("L")`.
2. **Resize to DPI matched to kerf.** Choose interval = focused spot size
   (baseline **0.1 mm → 254 DPI**; validate per §4). Target pixel grid =
   `panel_mm / interval_mm` in each axis. Resample with `LANCZOS`.
3. **Tone-correct BEFORE dithering** (order is load-bearing, §1.5):
   - autocontrast / clamp black & white points to histogram edges,
   - lift mid-tones (gamma ≈0.7 as a start; tune),
   - light unsharp mask (error diffusion edge-enhances anyway; don't over-sharpen).
   For the MVP this can be manual/fixed; expose as CLI flags.
4. **Dither → 1-bit.** MVP: `img.convert("1")` (Pillow FS). Upgrade: numpy
   Jarvis/Stucki with **serpentine** row reversal (reduces worming, §1.2).
5. **Row-scan emit with M4, bidirectional + serpentine, skip white:**
   - Header mirrors `laser_cam._laser_header`: `;HEAD: laser`, `;MATERIAL: ...`,
     `$32=1`, `G21`, `G90`, `M5`. Add `;RASTER` metadata (DPI, interval, dither,
     power, feed, direction).
   - Map pixel row → machine Y via the same Y-flip convention as
     `img_to_machine_mm` (ALGORITHMS config note).
   - Per row: rapid `G0` to the first ON dot (laser off across leading white),
     `M4 S<engrave_power>`, `F<engrave_feed>`, `G1` across the ON run
     (run-length: only emit X where a black→white/white→black transition happens),
     `M5` across interior white gaps, alternate scan direction each row.
   - Skip fully-white rows entirely.
   - **Overscan:** add ~3 mm laser-off lead-in/lead-out at each row end so
     firing is at steady feed (§1.6). This is the raster sibling of our existing
     overburn (A7). M4 already attenuates the residual accel zone.
6. **Preview + validate.** Reuse the `render_gcode_previews` idea (ALGORITHMS
   A10) to render the actual emitted toolpath (cuts red, rapids grey) and run it
   through `gcode_validate.py` before it ever touches material.

### Why this fits our rig
- Uses **M4 + `$32=1`** — auto-fixes accel/decel edge darkening on our low-accel
  gantry (§1.7), the exact reason M4 exists.
- **No LUT dependency** — dither tolerates our uncalibrated, narrow, non-linear
  response.
- Reuses existing conventions (validator-clean header, previews, material YAML,
  percent→S mapping) so it drops into the repo cleanly.
- Honors R9 (no dwells) and the fire rules already in the material profiles.

### Upgrade path (post-MVP)
- Build a **256-entry LUT** from an engraved gray-wedge (§1.5) → enable grayscale
  power-mod for smooth materials (leather/anodized), keep dither as default for
  wood/cardboard.
- Add **Scanning-Offset compensation** per feed once we measure bidir ghosting
  (§5), and **blue-noise/Jarvis** dithers for photo quality.

---

## 4. Bulletproof dial-in flowchart (minimal virgin material)

Goal: go from "never engraved a photo" to a good real panel wasting as little
stock as possible. Each step gates the next.

```
STEP 0  Machine + safety pre-flight
  ├─ $32=1 laser mode ON;  $30 = S-max (match generator, e.g. 1000);  $31=0
  ├─ Belt tension / pinion set-screws / couplers tight  (mechanics BEFORE software)
  ├─ Air assist ON (~10–20 PSI; >30 rarely needed, can cool + spread soot)
  ├─ OD5+ goggles for 445/450 nm  (CO2 goggles do NOT protect — wavelength must match)
  ├─ Ducted exhaust / P100;  extinguisher (CO2, ~2 kg) within reach
  └─ NEVER leave running unattended  (paper/cardboard flare fastest)

STEP 1  FOCUS  (one scrap strip)
  ├─ Ramp test: burn one line across a tilted scrap (1:10 slope = 5.71°,
  │   ~10× magnification). Thinnest/sharpest/darkest point = true focus.
  │   Settings: 15–20% power, air OFF for the ramp, engrave mode.
  ├─ Record head-to-work height; DON'T trust factory acrylic spacers
  │   (documented off by 1–2 mm).
  └─ Confirm the panel is FLAT (level check); diode tolerates only ~1–2 mm Z error.

STEP 2  POWER × SPEED GRID  (one small tile, ~40×40 mm, covers whole envelope)
  ├─ 10×10 (or 5×5) grid: columns = power, rows = speed; interval + passes fixed.
  ├─ Order boxes ascending burn-risk: safe corner (fast + low power) FIRST,
  │   burn-through corner diagonally opposite → stop early, save material.
  ├─ Engrave the SAME material + batch you'll use; focus first; air on if prod uses it.
  ├─ Cardboard start: ~8–20% power, 4000–6000 mm/min   (scorches/ignites FAST)
  │   Plywood/basswood: ~20–40% power, 3000–6000 mm/min
  │   MDF:               ~25–45% power, 3000–5000 mm/min  (heavy smoke)
  ├─ JUDGE AFTER COOLING (tone changes as it cools).
  └─ Pick the box: darkest RICH BROWN without char/halo/burn-through,
      with lighter neighbors present (⇒ material can render a full tonal range).

STEP 3  INTERVAL / LINE-SPACING  (one small strip, or a param sweep on the grid)
  ├─ Sweep interval 0.08 → 0.16 mm at the Step-2 power/speed.
  ├─ Pick where scan lines JUST TOUCH — no gaps (banding), no overlap (muddy).
  │   Read under raking light / loupe.
  ├─ Diode baseline: ~0.08–0.1 mm.  interval = focused spot size.
  └─ interval(mm) = 25.4 / DPI  → resample the source image to THAT DPI.

STEP 4  IMAGE PIPELINE DRY-RUN  (no material)
  ├─ Run raster.py: grayscale → resample to Step-3 DPI → tone-correct → dither.
  ├─ Inspect the dithered PNG on screen (does the face read?).
  ├─ Render the emitted-gcode PREVIEW (A10) + gcode_validate.py.
  └─ Fix image prep (contrast/gamma) in software — free iterations, zero stock.

STEP 5  SMALL REAL-PHOTO TEST  (~40 mm swatch, the REAL image, on SCRAP)
  ├─ Black-square grids don't predict photo behavior — dithered midtones differ.
  ├─ Run the actual photo (or a gray-gradient strip) at 2–3 candidate
  │   power/speed combos from Step 2.
  ├─ Confirm: full light→dark range, no char, no banding, no ghosting.
  └─ If ghosting between alternate rows → §5 (mechanics first, then offset table).

STEP 6  REAL PANEL
  ├─ Mask surface with painter's tape (squeegee flat) to fight soot staining.
  ├─ Hold panel FLAT (magnets/pins/weights; watch magnets near the head fan).
  ├─ Air on, exhaust on, goggles on, human present.
  └─ Engrave single pass, M4 dynamic, overscan on.
```

**Material-frugality summary:** one scrap strip (focus) + one small tile (P×S
grid) + one small strip (interval) + on-screen dry-runs + one small real-photo
swatch — typically **< 4 small pieces** before committing the real panel.

**Focus / air / fire notes (sources):**
- Ramp-test geometry & the spacer caveat:
  https://softsolder.com/2024/10/18/laser-cutter-focus-ramp-fixture/ ·
  https://blog.commarker.com/archives/52091
- Why flatness matters (shallow diode DOF, warp → banding):
  https://blazexlaser.com/blogs/news/laser-engraving-focus-problems-signs-your-laser-is-out-of-focus
- Material-test-grid method & reading it:
  https://docs.lightburnsoftware.com/2.1/Reference/MaterialTest/ ·
  https://www.laserparams.com/lightburn-settings-guide ·
  open-source generator: https://rayforge.org/docs/features/operations/material-test-grid/
- Interval test:
  https://docs.lightburnsoftware.com/2.1/Reference/IntervalTest/
- Air assist pressure (~10–20 PSI; doesn't cool the diode):
  https://uk.blazexlaser.com/blogs/news/why-is-my-10w-diode-laser-burning-edges-air-assist-explained
- Cardboard exception (strong air can feed flare / tear sheet — go low power/high
  speed instead): https://www.bonnycreations.com/settings/materials/cardboard
- Fire safety (never unattended, CO2 extinguisher, Class 4):
  https://www.americanlaserco.com/laser101/laser-cutter-fire-safety/ ·
  https://www.orturus.com/resources.html
- Fumes (MDF worst — formaldehyde/benzene/PM2.5; enclose+duct or HEPA+carbon):
  https://fabcon.com/articles/precision-cnc-machining/plywood-vs-mdf-laser-cutting/
- Eye safety (445/450 nm, OD5+; CO2 glasses don't protect):
  https://jtechphotonics.com/?product=laser-safety-goggles-for-445nm-lasers

---

## 5. Snags/gotchas they WILL hit — prioritized, with pre-emptions

1. **Banding from interval↔DPI mismatch.** Too-wide interval → gaps; too-tight →
   muddy overlap. **Pre-empt:** run the interval test (Step 3), pick where lines
   just touch (~0.08–0.1 mm), then **resample the image to that exact DPI** so
   pixel pitch == scan pitch. Slight defocus (0.5–1 mm) can fill hairline gaps.
   https://docs.lightburnsoftware.com/2.1/Reference/IntervalTest/

2. **Char / flare / halo from overpower on a weak diode.** The instinct to crank
   power (our cut rule R8) is *wrong for engraving* — that just chars and risks
   fire. Fault is "too much energy in the wrong place." **Pre-empt:** raise speed
   rather than power; use M4 (constant-power M3 makes scorch worse for raster);
   air assist; mask tape; find the darkest-brown-not-charred box in Step 2.
   https://blazexlaser.com/blogs/news/why-your-laser-engraving-looks-bad-7-common-problems-and-how-to-fix-them ·
   https://docs.lightburnsoftware.com/latest/Explainers/SpeedVsPower/

3. **Focus drift over a warped / large panel → uneven darkness + banding.**
   Diode DOF tolerates only ~1–2 mm Z error; a bowed board goes soft/faint at
   edges & corners. **Pre-empt:** ramp-test focus (Step 1), check panel with a
   level, hold flat (magnets/pins/weights/honeycomb+vacuum). For very large/warped
   stock, split into zones and refocus. Watch magnets near the head's air-assist
   fan. https://www.thunderlaser.com/laser-wiki/improve-laser-processing-material-flatness.html

4. **Bidirectional ghosting / double image between alternate rows.** Two causes:
   finite firing delay (timing) AND belt stretch/backlash (mechanical).
   **Pre-empt in order:** (a) fix mechanics first — belt tension, set-screws,
   couplers, lower Y accel if the gantry bounces; (b) THEN calibrate a
   speed→offset table (LightBurn's Scanning Offset equivalent): draw a fill box
   at several speeds, measure end-gap, apply **half** the measured offset per
   speed. On our CNC-converted machine, mechanics are the likely culprit.
   https://forum.lightburnsoftware.com/t/ghosting-double-engraving-help/8812 ·
   https://docs.lightburnsoftware.com/2.1/Guides/ScanningOffsetAdjustment/

5. **Soot staining + low substrate contrast.** Rising smoke redeposits soot and
   discolors the engrave; and the substrate caps achievable contrast — **cardboard
   is inherently low-contrast** (brown-on-brown), light tight-grained woods
   (basswood/birch/maple) give the best photo contrast, dark woods engrave
   dark-on-dark (poor). **Pre-empt:** painter's-tape mask (squeegee flat, peel
   after), air assist to clear smoke, wipe residual soot *along the grain* with
   alcohol/vinegar; borax/baking-soda mist darkens wood marks toward black; choose
   light wood for real photos, treat cardboard as a low-fidelity draft substrate.
   https://blazexlaser.com/blogs/news/diode-laser-engraver-how-to-improve-engraving-contrast ·
   https://blog.snapmaker.com/blog/how-to-darken-laser-engraving-on-wood-baking-soda-and-borax/

**Secondary gotchas (still worth pre-empting):**

6. **Over-burn at row turnarounds (decel dwell).** Even with M4, add **overscan**
   (~3 mm laser-off run-in/out) so firing is at steady feed. If corners still
   over-burn on GRBL, raise X/Y max accel in small steps (100–500). Watch the
   "use G0 for overscan" pause gotcha.
   https://docs.lightburnsoftware.com/latest/Explainers/Overscanning/

7. **Resin / glue / voids in plywood & MDF → blotchy burn.** Big-box ply hides
   voids/knots/patches; the beam "sails through in one spot and stalls in the
   next." **Pre-empt:** use void-free **Baltic birch** for even photo results;
   MDF is actually the *most uniform* surface (no grain) but smokes heavily.
   https://www.americanlaserco.com/laser101/laser-cutting-plywood/

8. **Grain direction & moisture.** Orient scan lines **perpendicular to the
   grain**; run fine detail across the grain; store stock flat & dry (moisture →
   blotchy). https://docs.lightburnsoftware.com/2.1/Guides/PerfectImageEngraving/

9. **"Worming"/directional dither artifacts.** FS propagates errors diagonally →
   cone artifacts. **Pre-empt:** serpentine scanning (reverse error direction per
   row); for photos prefer Jarvis/Stucki over plain FS.

10. **Data volume / streamer stalls.** Grayscale = ~1 command/pixel; a big photo
    can stall an 8-bit sender. **Pre-empt:** run-length encode rows (S/coords only
    on change), skip white runs, keep DPI sane (≤300). Dithered on/off is already
    lighter than per-pixel grayscale.

---

## Appendix: honest gaps in this research

- **Reddit (/r/lasercutting, /r/Diode_Laser), all3dp, imag-r** were
  CAPTCHA/anti-bot-blocked; those are cited from search snippets, not full fetches.
- The exact **densitometer→LUT** recipe and the "bidirectional = 2× faster"
  multiplier are framed as synthesis, not verbatim-sourced.
- **[premise corrections] baked in above:** LaserGRBL has no gamma slider;
  image2gcode is grayscale not dither; raster2laser is Inkscape (not GIMP) and
  lacks FS/Jarvis/Stucki; CNCjs & svg2gcode & Scorchworks tools are NOT photo
  raster engravers; PixelToLaser/grbl_image don't exist as named; "100% power for
  photos" is a *cutting/metal* rule, not a wood-photo rule; a focused diode spot
  can rival CO2 (so "coarse spot" is a minor driver for dithering, not the main).
```
