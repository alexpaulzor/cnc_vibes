// spiral_stretch.scad — one (or two) spiral ribbon(s), rendered identically
// whether FLAT or STRETCHED to a height. Animatable (View > Animate).
//
// Model (centerline of the wood, for lining up notches/ribs). The outer rim and
// inner hub are RIGID rings, so their radii are fixed — the stretch cannot pull
// them inward, it must TWIST. Parameterize each arm by u in [0,1] (0 = hub,
// 1 = rim):
//     radius  r(u) = r0 + (R - r0)*u        // endpoints pinned at r0 and R
//     height  z(u) = H*u                     // linear rise
//     angle   phi  = Theta_eff * u           // Theta_eff shrinks as H grows
// Theta_eff = sqrt(Theta^2 - (H/rbar)^2) keeps the centerline length ~constant
// (arc length lost to rising is taken out of the winding), so the arms unwind /
// twist as the pot gets taller. stretch = 0 -> flat; stretch = 1 -> full height.

/* ---------------- parameters ---------------- */
r0         = 25;    // inner radius (rigid hub edge) — FIXED
pitch      = 24;    // flat radial growth per full turn (arm spacing = pitch/n_spirals)
turns      = 3;     // flat number of revolutions
width      = 8;     // ribbon width (across the arm). = pitch/n_spirals fills the
                    // wall solid (tight pack); smaller leaves gaps so you see the spiral
thickness  = 3;     // material thickness
n_spirals  = 2;     // interleaved arms (1 = a single spiral)
steps      = 60;    // samples per revolution (smoothness)

max_height = 60;    // height at full stretch (mm)

// Drag this with View > Animate ($t goes 0..1), or hardcode e.g. 0.5 to hold.
stretch    = $t;

/* ---------------- derived ---------------- */
R      = r0 + pitch * turns;         // outer radius (rigid rim) — FIXED
Theta  = turns * 360;                // flat angular sweep (deg)
rbar   = (r0 + R) / 2;               // representative radius (length approximation)
H      = stretch * max_height;       // current height
dth    = (H / rbar) * 180 / PI;      // sweep (deg) traded for rise, from length conservation
Theta_eff = sqrt(max(Theta*Theta - dth*dth, 0));   // remaining winding (the twist)
$fn    = 24;

// Centerline radius at parameter u in [0,1].
function rad(u) = r0 + (R - r0) * u;

// A thin cross-section of the ribbon at parameter u (phase in deg for arm k).
// The wood stays FLAT (horizontal): width is radial, thickness is vertical — so
// a cross-section reads as stacked flat rings (stairs), not a smooth funnel.
module section(u, phase) {
    r   = rad(u);
    z   = H * u;
    phi = Theta_eff * u + phase;
    translate([r * cos(phi), r * sin(phi), z])
        rotate([0, 0, phi])            // local +x = radial; ribbon stays horizontal
            cube([width, 0.1, thickness], center = true);
}

// One spiral arm = hull of consecutive cross-sections along the sweep.
module arm(phase = 0) {
    n = steps * turns;
    for (i = [0 : n - 1])
        hull() { section(i / n, phase); section((i + 1) / n, phase); }
}

/* ---------------- render ---------------- */
echo(str("H=", H, "  twist(rim vs hub)=", Theta - Theta_eff, " deg"));
for (k = [0 : n_spirals - 1])
    arm(k * 360 / n_spirals);
