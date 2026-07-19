// orpot.scad — laser-cut "expanding spiral" orchid pot.
//
// Hand-editable (NOT generated). Units: mm. One square of 3mm MDF -> a round
// disc with two interleaved spiral cuts (the pot; it expands into a bowl when
// the rim is lifted off the hub) plus 4 ribs from the leftover corners.
//
//   MODE = "cut"       -> flat 2D cutting pattern (export DXF/SVG for the laser)
//   MODE = "assembled" -> 3D preview of the pot stretched to `pot_height`
//
// Stretch is by TWIST, not by pulling in: the rigid hub and rim keep their radii,
// so as it rises the arms unwind and the rim rotates vs the hub by `twist`. The
// ring rib-slots are pre-offset by that twist so the ribs seat in true radial
// planes. The wood stays flat, so a cross-section reads as stacked rings.

/* ================= parameters ================= */
// MODE       = "assembled";  // "cut" or "assembled"
MODE = "cut";

IN         = 25.4;  // mm per inch
stock      = 300;   // square stock edge
thickness  = 3;     // MDF thickness
kerf       = 0.20;  // laser kerf; also the spiral cut width
fit        = 0.15;  // slip-fit clearance on tabs/slots

hub_dia    = 3*IN;    // solid center hub diameter (76.2)
ring_id    = 6*IN;    // rim ring INNER diameter (152.4) = opening
ring_w     = 0.75*IN; // rim ring width (19.05) — 3/4" so a 1/2" tab has margin
ramp_w     = 0.5*IN;  // spiral arm width (12.7)
n_spirals  = 2;       // interleaved spiral arms

n_ribs     = 4;       // radial ribs (from the corner offcuts)
pot_height = 3*IN;    // assembled height (also rib height in the flat pattern)
rib_w      = 0.5*IN;  // rib strut/body thickness reference
tab_w      = 0.5*IN;  // tab length along its slot (12.7), hub + ring
tab_thru   = thickness;      // tab pokes this far through the slot (then a step)
shoulder   = 3;              // min material each side of a slot / step width
hub_lap    = 8;              // cross-lap depth where the hub disc meets a rib leg

$fn = 180;

/* ================= derived ================= */
r_hub  = hub_dia/2;               // 38.1
r_rim  = ring_id/2;               // 76.2 : arms/cuts end here, ring inner edge
r_out  = r_rim + ring_w;          // 95.25: disc outer edge (od 7.5")
pitch  = n_spirals * ramp_w;      // 25.4 radial advance per revolution
turns  = (r_rim - r_hub) / pitch; // 1.5 : as many turns as fit the annulus
ring_c = r_out - ring_w/2;        // rib top-tab lands here (fixed from edge)
slot_w = thickness + fit;         // rib slot width (tangential)
slope  = pot_height / (r_rim - r_hub);   // cone slant: dz/dr

// --- stretch / twist ---
Theta     = turns * 360;                   // flat angular sweep of an arm (deg)
rbar      = (r_hub + r_rim) / 2;
dth       = (pot_height / rbar) * 180/PI;
Theta_eff = sqrt(max(Theta*Theta - dth*dth, 0));
twist     = Theta - Theta_eff;             // rim rotates this vs the hub

/* ================= flat spiral disc ================= */

function spiral_r(a) = r_hub + (pitch/360) * a;   // Archimedean radius at angle a

module spiral_cut(w, steps = 300) {               // one thin spiral cut, hub->rim
    amax = turns * 360;
    outer = [ for (i = [0:steps]) let(a = amax*i/steps, r = spiral_r(a) + w/2)
                [ r*cos(a), r*sin(a) ] ];
    inner = [ for (i = [steps:-1:0]) let(a = amax*i/steps, r = spiral_r(a) - w/2)
                [ r*cos(a), r*sin(a) ] ];
    polygon(concat(outer, inner));
}

// A radial slot centred on radius `rc`, `len` long (radial), `slot_w` wide.
module radial_slot(rc, len) {
    rotate([0,0,90]) translate([0, rc]) square([slot_w, len], center = true);
}

module disc2d() {
    difference() {
        circle(r = r_out);
        for (k = [0 : n_spirals-1])
            rotate([0, 0, k*360/n_spirals]) spiral_cut(kerf);
        // ring slot (pre-offset by twist) for the rib top tab; hub cross-lap slot
        // (cut inward from the hub edge) that the rib's bottom notch interlocks with.
        for (i = [0 : n_ribs-1]) {
            a = i*360/n_ribs;
            rotate([0, 0, a])         radial_slot(r_hub - hub_lap/2, hub_lap);
            rotate([0, 0, a + twist]) radial_slot(ring_c, tab_w + fit);
        }
    }
}

/* ================= rib =================
   In the rib's (x = radius s, y = height z) frame. The rib is a SOLID leg: a
   flat bottom on the table (z=0), the outside-bottom right-angle corner at
   (r_out, 0), a solid body up under the arms, and the outer/ring wall. The only
   things carved away are flat SHELVES where each arm lands (so a flat arm lands
   flat, no tunnelling) and a hub cross-lap notch so the centre disc slots in.
   Ring top tab (stepped) plugs into the ring slot. */

// Where the arms cross this rib's radial plane (azimuth a, deg): [r, z] each.
function rib_crossings(a) = [
    for (k = [0:n_spirals-1]) for (m = [0:ceil(turns)+1])
        let(u = (a - k*360/n_spirals + m*360) / Theta_eff)
        if (u > 0.03 && u < 0.995)
            [ r_hub + (r_rim - r_hub)*u, pot_height*u ]
];

module rib2d(a) {
    inner = r_hub - hub_lap;                       // inner extent (laps the hub edge)
    union() {
        translate([inner, 0]) square([r_out - inner, shoulder]);    // flat foot (leg on table)
        translate([r_rim, 0]) square([r_out - r_rim, pot_height]);  // outer riser + ring wall
        for (c = rib_crossings(a))                                  // solid column up to each shelf
            translate([c[0] - (ramp_w+fit)/2, 0]) square([ramp_w+fit, c[1] - thickness/2]);
        translate([ring_c - tab_w/2, pot_height]) square([tab_w, tab_thru]); // ring top tab
    }
}

/* ================= 2D cutting layout ================= */

module layout2d() {
    *%square([stock, stock], center = true);   // stock outline (reference, not cut)
    disc2d();
    // Each rib's right-angle (outside-bottom) corner tucks into a STOCK corner,
    // its two legs along the sheet edges, hypotenuse (the slant) facing the disc.
    // scale flips send the body inward toward the center for each corner.
    m = 4;  S = stock/2 - m;                 // small margin from the very edge
    corners = [[ S,  S,  1, -1], [-S,  S, -1, -1],
               [-S, -S, -1,  1], [ S, -S,  1,  1]];
    for (i = [0 : n_ribs-1])
        translate([corners[i][0], corners[i][1]]) scale([corners[i][2], corners[i][3]])
            translate([-r_out, 0]) rib2d(i*360/n_ribs);
}

/* ================= 3D assembled preview ================= */
// True to the cut: the actual flat disc (extruded 3mm) is the BOTTOM plane; a
// copy of just the rim ring (from the same cut) is the TOP plane at pot_height;
// the ribs stand between, in radial planes, feet into the bottom / tops into the
// ring. Extra: the 3D spiral arms lofting from bottom to top.

module ring2d() {                                  // the top-circle cut (ring + its slots)
    difference() {
        circle(r = r_out);
        circle(r = r_rim);
        for (i = [0 : n_ribs-1])
            rotate([0, 0, i*360/n_ribs + twist]) radial_slot(ring_c, tab_w + fit);
    }
}

module arm3d(phase, seg = 60) {                    // extra-credit lofted spiral
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
    color("BurlyWood") linear_extrude(thickness) disc2d();                 // BOTTOM = the cut
    color("SteelBlue") translate([0,0,pot_height]) linear_extrude(thickness) ring2d(); // TOP ring
    color("SaddleBrown")                                                   // ribs between
        for (i = [0:n_ribs-1])
            rotate([0,0,i*360/n_ribs]) rotate([90,0,0])
                linear_extrude(thickness, center=true) rib2d(i*360/n_ribs);
    color("Goldenrod")                                                     // extra: spiral arms
        for (k = [0:n_spirals-1]) arm3d(k*360/n_spirals);
}

/* ================= top level ================= */
echo(str("turns=", turns, "  twist=", twist, " deg  disc od=", 2*r_out/IN, "in"));
if (MODE == "assembled") assembled3d();
else                     layout2d();
