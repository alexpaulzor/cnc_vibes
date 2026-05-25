# Honeycomb laser bed — purchase criteria and tradeoffs

Hardware notes for picking a honeycomb bed to sit on top of the Anolex 4030 + LaserTree 10W. Captured from a session where the user wanted "quality over cost" and was about to buy.

## Decision the user made

- **Size**: 400×400mm
- **Format**: framed tray with magnetic edge strips for hold-downs
- **Z budget available**: 50mm+ free under the LaserTree head
- **Price ceiling**: ~$150 (quality over cost)

## Decision tradeoffs we walked through

### Size: 400×400 vs 500×500 vs larger

| Option | Pro | Con |
|---|---|---|
| **400×400** *(picked)* | Matches X travel exactly; small bed footprint on the CNC | Tight on the Y side; no margin if you want to position oversized stock |
| 500×500 | ~80mm margin all around the 400×300 laser envelope; common stock size, lots of listings | Bigger footprint on the CNC bed |
| 600×400+ | Future-proofs for a bigger machine | Eats space + costs more |

### Format: framed tray vs bare panel vs pin bed

| Option | Pro | Con |
|---|---|---|
| **Framed tray with magnets** *(picked)* | Catches debris; lifts work above the CNC bed for airflow; magnet strips give pleasant hold-down without clamps | Pricier than a bare panel; ~25-35mm of Z |
| Bare aluminum panel | Cheaper; thinner (saves Z); minimal | No debris containment; you supply your own hold-down system |
| Framed + removable pin bed | Pin bed supports very small intricate parts | Pricier; only worth it if you cut tiny jigsaw pieces routinely |

### Z clearance

- Plenty (50mm+ free): any standard framed tray fits — user's situation
- Tight (20-40mm): need to measure carefully against the candidate's total height
- Unknown: measure before ordering

## What to verify before buying (apply as Amazon filters)

Apply roughly in this order — eliminates the most listings first:

1. **Size 400×400mm** (±20mm) — eliminates the majority of generic-honeycomb listings.
2. **Framed**, not a bare panel — there's a frame around the honeycomb (steel or aluminum). About half of listings are bare panels.
3. **Magnetic edge strips** — strips of magnetic material attached to the frame edges so steel hold-downs / washers stick. NOT a feature on most cheap framed beds; this is the second-strongest filter.
4. **Flatness spec ≤0.5mm** stated by the seller. Cheap unbranded panels almost never mention flatness — if they don't say it, it's probably warped.
5. **Cell size 6-10mm** — finer than 6mm clogs with debris; coarser than 10mm doesn't support small parts.
6. **Total height ≤35mm** — confirms it fits the user's Z budget.
7. **Price $50-150** — below $50 = quality risk; above $150 = paying for features you don't need at this scale.

For survivors after the above, prioritize products whose **review text** (not stars) mentions:

- "Arrived flat" / "lays flat across the bed" (good)
- "Solid frame" / "well-made" (good)
- "Magnets hold well" (good)
- "Warped" / "bowed" / "rocked on the bed" (avoid)
- "Smaller than advertised" / "off-square" (avoid)
- "Flimsy" / "bent in shipping" (avoid)

## Brand breadcrumbs (search starting points — NOT endorsements)

A 2026-05 sub-agent scan was blocked by Amazon's anti-bot wall, so these are unverified brand names seen in indirect search results. Use them as starting points to filter Amazon yourself:

- **YOOPAI** — 400×400mm steel-frame versions with measurement markers
- **ACMER** (E10 line) — frame + protective backplate
- **Lunyee** — 400×400mm with 0-400mm rulers on the frame
- **VXB** — 400×400mm; sold direct via vxb.com, also Amazon
- **Sculpfun** (H1 / H3) — frequently recommended in diode-laser communities; availability varies
- **OMTech** — US-based; their accessories occasionally come and go from Amazon

**Honest caveat**: at the time these notes were captured, none of the indirect snippets mentioned integrated magnetic edge strips on the 400×400 framed beds in the $50-150 band. The magnetic-strip feature may be rarer than it sounds — you may need to compromise (no magnets and use clamps), buy a larger model that includes them, or look outside Amazon.

## After purchase — first-use checklist

1. **Measure flatness** on arrival: lay a known-straight edge across it, look for daylight. Reject if >0.5mm gap.
2. **Mount it** on the CNC bed; verify it sits flat (no rocking).
3. **Z focus per corner**: with the laser homed, jog to each corner and re-check focal distance. A flat bed is not the same as a bed aligned to the gantry. Save the four Z values in your machine profile if they differ significantly.
4. **Test cut a 50mm square** at known-good params from `profiles/laser_materials.yaml`. Inspect the kerf at each corner. If it varies, you've found a Z-alignment issue, not a laser-power issue.

## What I (Claude) got wrong in the original recommendation

In the original session I named "Sculpfun H1/H3" and "OMTech 400×400 framed" as top picks with ~prices. Those were from training data, not from a current Amazon search. Neither was available when the user looked. **Better practice**: provide criteria and filtering steps (this file), not specific products, unless I've actually verified availability.
