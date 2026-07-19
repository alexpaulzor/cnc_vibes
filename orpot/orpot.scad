// orpot.scad — laser-cut "expanding spiral" orchid pot.
//
// Hand-editable (NOT generated). Units: mm. One square of 3mm MDF -> a round
// disc with two interleaved spiral cuts (the pot; it expands into a bowl when
// the rim is lifted off the hub) plus 4 S-curve ribs from the leftover corners.
//
//   MODE = "cut"       -> flat 2D cutting pattern (export DXF/SVG for the laser)
//   MODE = "assembled" -> 3D preview of the pot stretched to `pot_height`
//
// The pot stretches by TWIST, not by pulling in: the rigid hub and rim keep
// their radii, so as it rises the arms unwind and the rim rotates relative to
// the hub by `twist`. The ring rib-slots are pre-offset by that twist in the
// flat pattern, so once assembled the ribs sit in true radial planes
// (perpendicular to base/rim) and drop straight into their slots. The wood stays
// flat (horizontal), so a cross-section reads as stacked rings, not a cone.

/* ================= parameters ================= */
MODE       = "assembled";  // "cut" or "assembled"

stock      = 300;   // square stock edge
thickness  = 3;     // MDF thickness
kerf       = 0.20;  // laser kerf; also the spiral cut width
fit        = 0.10;  // extra slot clearance for a slip fit

disc_dia   = 200;   // POT outer diameter (<= stock). Main size knob.
hub_dia    = 50;    // solid center hub diameter
ring_w     = 12;    // solid outer rim-ring width
ramp_w     = 12;    // spiral arm (ramp) width  = spacing between cuts
n_spirals  = 2;     // interleaved spiral arms

n_ribs     = 4;     // radial ribs (from the corner offcuts)
pot_height = 40;    // assembled height (also the rib height in the flat pattern)
rib_w      = 12;    // rib strut width
tab        = 5;     // rib end-tab length (into hub / ring slots)
top_tab_w  = 10;    // rib top-tab width (into ring slot)
bot_tab_w  = 10;    // rib bottom-tab width (into hub slot)

$fn = 180;

/* ================= derived ================= */
r_out  = disc_dia/2;
r_hub  = hub_dia/2;
r_rim  = r_out - ring_w;          // inner edge of the rim ring; cuts end here
pitch  = n_spirals * ramp_w;      // radial advance per full revolution
turns  = (r_rim - r_hub) / pitch; // whole-ish turns that fit the annulus
ring_c = r_out - ring_w/2;        // rib top-tab lands here (fixed from edge)
slot_w = thickness + fit;         // rib slot width (tangential)

// --- stretch / twist (see header) ---
Theta     = turns * 360;                   // flat angular sweep of an arm (deg)
rbar      = (r_hub + r_rim) / 2;           // representative radius
dth       = (pot_height / rbar) * 180/PI;  // sweep traded for the rise (deg)
Theta_eff = sqrt(max(Theta*Theta - dth*dth, 0));  // winding left after stretch
twist     = Theta - Theta_eff;             // rim rotates this much vs the hub

/* ================= flat spiral disc ================= */

// Archimedean spiral radius at angle a (deg).
function spiral_r(a) = r_hub + (pitch/360) * a;

// One thin spiral cut (a slot of radial width `w`), from hub to rim.
module spiral_cut(w, steps = 300) {
    amax = turns * 360;
    outer = [ for (i = [0:steps]) let(a = amax*i/steps, r = spiral_r(a) + w/2)
                [ r*cos(a), r*sin(a) ] ];
    inner = [ for (i = [steps:-1:0]) let(a = amax*i/steps, r = spiral_r(a) - w/2)
                [ r*cos(a), r*sin(a) ] ];
    polygon(concat(outer, inner));
}

// A radial slot (for a rib tab) centred on radius `rc`, `len` long, `slot_w` wide.
module radial_slot(rc, len) {
    rotate([0,0,90]) translate([0, rc]) square([slot_w, len], center = true);
}

module disc2d() {
    difference() {
        circle(r = r_out);                       // the disc / pot blank
        for (k = [0 : n_spirals-1])              // interleaved spiral cuts
            rotate([0, 0, k*360/n_spirals]) spiral_cut(kerf);
        // rib slots: hub slot at the rib azimuth; ring slot PRE-OFFSET by `twist`
        // so that after the pot twists open, both land in the same radial plane.
        for (i = [0 : n_ribs-1]) {
            a = i*360/n_ribs;
            rotate([0, 0, a])         radial_slot(r_hub - bot_tab_w/2, bot_tab_w);
            rotate([0, 0, a + twist]) radial_slot(ring_c, top_tab_w);
        }
    }
}

/* ================= S-curve rib ================= */

// Cubic Bezier point (2D vectors).
function bez(p0,p1,p2,p3,t) = let(u = 1-t)
    u*u*u*p0 + 3*u*u*t*p1 + 3*u*t*t*p2 + t*t*t*p3;

// Thick polyline: a chain of hull()'d discs of radius `hw` along `pts`.
module thick_path(pts, hw) {
    for (i = [0 : len(pts)-2])
        hull() { translate(pts[i]) circle(hw); translate(pts[i+1]) circle(hw); }
}

// One rib in its own (x = radius s, y = height z) frame: an ogee strut sweeping
// from the rim ring (top, outer) inward and down to the hub (bottom).
module rib2d() {
    hw  = rib_w/2;
    top = [ring_c, pot_height - hw];
    bot = [r_hub,  hw];
    p1  = [ring_c, pot_height*0.55];   // leave the rim heading down (outer)
    p2  = [r_hub,  pot_height*0.45];   // sweep inward, then drop to the hub
    union() {
        thick_path([ for (i = [0:48]) bez(top, p1, p2, bot, i/48) ], hw);
        translate([ring_c - top_tab_w/2, pot_height - hw])   // top tab -> ring slot
            square([top_tab_w, hw + tab]);
        translate([r_hub - bot_tab_w, -tab])                 // bottom tab -> hub slot
            square([bot_tab_w + hw, tab + hw]);
    }
}

/* ================= 2D cutting layout ================= */

module layout2d() {
    %square([stock, stock], center = true);   // stock outline (not cut)
    disc2d();
    // 4 ribs laid along the diagonals into the corner offcuts. Tune gap/disc_dia.
    gap = 4;
    tx  = r_out + gap - (r_hub - bot_tab_w);
    for (ang = [45, 135, 225, 315])
        rotate([0, 0, ang]) translate([tx, 0]) rib2d();
}

/* ================= 3D assembled preview ================= */
// Same twist model as the flat pattern: radius pinned (r_hub..r_rim), linear
// rise, winding = Theta_eff. Wood stays flat (horizontal) -> stacked-ring look.

module arm3d(phase, seg = 60) {
    n = seg * turns;
    for (i = [0 : n-1]) hull()
        for (u = [i/n, (i+1)/n]) {
            r   = r_hub + (r_rim - r_hub)*u;
            z   = pot_height*u;
            phi = Theta_eff*u + phase;
            translate([r*cos(phi), r*sin(phi), z]) rotate([0,0,phi])
                cube([ramp_w, 0.1, thickness], center = true);
        }
}

module assembled3d() {
    color("SaddleBrown")                                    // hub (bottom)
        translate([0,0,-thickness/2]) cylinder(r = r_hub, h = thickness);
    color("SteelBlue")                                      // rim ring (top)
        translate([0,0,pot_height-thickness/2])
            difference() { cylinder(r=r_out,h=thickness); cylinder(r=r_rim,h=thickness); }
    color("Goldenrod")                                      // the two spiral arms
        for (k = [0:n_spirals-1]) arm3d(k*360/n_spirals);
    // ribs, each standing in a true radial plane at its azimuth
    color("Tan")
        for (i = [0:n_ribs-1])
            rotate([0,0,i*360/n_ribs]) rotate([90,0,0])
                linear_extrude(thickness, center=true) rib2d();
}

/* ================= top level ================= */
echo(str("turns=", turns, "  rim-vs-hub twist=", twist, " deg at ", pot_height, "mm"));
if (MODE == "assembled") assembled3d();
else                     layout2d();
