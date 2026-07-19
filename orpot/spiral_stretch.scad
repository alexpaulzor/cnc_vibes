// spiral_stretch.scad — one (or two) spiral ribbon(s), rendered identically
// whether FLAT or STRETCHED to a height, with the horizontal squish (and
// optional twist) that stretching implies. Animatable (View > Animate).
//
// Model: the flat spiral's radius is the SLANT distance on a cone. A point at
// flat radius r maps to horizontal radius r*cos(a) and height r*sin(a), so as it
// rises (a grows) it squishes inward — arc length (r) is preserved. a is set by
// the target height. stretch = 0 -> flat; stretch = 1 -> full height.

/* ---------------- parameters ---------------- */
r0         = 25;    // inner radius (hub edge)
pitch      = 24;    // radial growth per full turn  (arm spacing = pitch/n_spirals)
turns      = 3;     // number of revolutions
width      = 8;     // ribbon width (across the arm). = pitch/n_spirals fills the
                    // wall solid (tight pack); smaller leaves gaps so you see the spiral
thickness  = 3;     // material thickness
n_spirals  = 2;     // interleaved arms (1 = a single spiral)
steps      = 60;    // samples per revolution (smoothness)

max_height = 60;    // height at full stretch (mm)
twist      = 0;     // extra differential twist at full stretch (deg); 0 = none

// Drag this with View > Animate ($t goes 0..1), or hardcode e.g. 0.5 to hold.
stretch    = $t;

/* ---------------- derived ---------------- */
R    = r0 + pitch * turns;                 // outer (flat) radius
a    = asin(min((stretch * max_height) / R, 0.999));  // cone tilt from flat
amax = turns * 360;                        // total sweep in degrees
$fn  = 24;

// Flat radius at spiral angle t (degrees).
function rad(t) = r0 + (pitch / 360) * t;

// A thin cross-section of the ribbon at spiral angle t: width along the cone
// slant, thickness along the surface normal, placed at its stretched position.
module section(t) {
    r     = rad(t);
    horiz = r * cos(a);                     // horizontal radius (squish)
    z     = r * sin(a);                     // height on the cone
    th    = t + twist * stretch * (r - r0) / (R - r0);   // optional differential twist
    translate([horiz * cos(th), horiz * sin(th), z])
        rotate([0, 0, th])                 // local +x = radial (horizontal)
            rotate([0, -a, 0])             // tilt up by a: +x -> slant, +z -> normal
                cube([width, 0.1, thickness], center = true);
}

// One spiral arm = hull of consecutive cross-sections along the sweep.
module arm(phase = 0) {
    n = steps * turns;
    rotate([0, 0, phase])
        for (i = [0 : n - 1])
            hull() { section(amax * i / n); section(amax * (i + 1) / n); }
}

/* ---------------- render ---------------- */
for (k = [0 : n_spirals - 1])
    arm(k * 360 / n_spirals);
