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
n_spirals  = 1;       // 1 = single spiral (gentler bend: same width, 2x turns, half
                      // the stretch per length); 2 = double helix
spiral_offset = 10;   // rotate the spiral cut(s) off the rib slots — ~6.6mm arc gap
                      // at the hub, enough (>=5mm) that the tab-to-spiral can't split

n_ribs     = 4;       // radial ribs (from the corner offcuts)
pot_height = 3*IN;    // assembled height (also rib height in the flat pattern)
rib_w      = 0.5*IN;  // rib strut/body thickness reference
tab_w      = 0.5*IN;  // tab length along its slot (12.7), hub + ring
tab_thru   = thickness;      // ring tab pokes this far through the ring slot
shoulder   = 3;              // min material each side of a slot / step width
base_engage = 20;    // rib<->hub cross-lap length (hub slot reaches this far in
                     // from the first spiral; rib base slot matches)
outer_fillet = 1*IN; // round the rib's outer-bottom corner (bigger later)

layout_side = 310;   // square envelope the parts pack into (parts are big now;
                     // stock size is not a constraint for the moment)

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

// A radial slot centred on radius `rc`, `len` long (radial), `slot_w` wide,
// lying on the +x axis (rotate it to the target azimuth).
module radial_slot(rc, len) {
    translate([rc, 0]) square([len, slot_w], center = true);
}

// Radius where the spiral CUT crosses azimuth a (deg) on its first turn — the
// seam a hub slot must reach so a rib can slide in and mate.
function first_cut_r(a) = r_hub + (pitch/360) * ((a - spiral_offset + 3600) % 360);

// The spiral centerline as a point list (open path), phase in degrees.
function spiral_centerline(phase, steps = 300) = [
    for (i = [0:steps]) let(a = turns*360*i/steps, r = spiral_r(a))
        [ r*cos(a + phase), r*sin(a + phase) ]
];

module disc2d(with_spirals = true) {
    difference() {
        circle(r = r_out);
        if (with_spirals)
            for (k = [0 : n_spirals-1])
                rotate([0, 0, k*360/n_spirals + spiral_offset]) spiral_cut(kerf);
        // ring slot (pre-offset by twist) for the rib top tab. Hub slot runs from
        // base_engage inside the hub OUT to the first cut seam, so it's not a blind
        // pocket — the rib slides in through the seam and mates with it.
        for (i = [0 : n_ribs-1]) {
            a = i*360/n_ribs;
            hub_in  = r_hub - base_engage;
            hub_out = first_cut_r(a);
            rotate([0, 0, a])         radial_slot((hub_in + hub_out)/2, hub_out - hub_in);
            rotate([0, 0, a + twist]) radial_slot(ring_c, tab_w + fit);
        }
    }
}

/* ================= rib =================
   In the rib's (x = radius s, y = height z) frame. The rib is a full-height,
   full-width fin (outer-bottom corner rounded, outer_fillet). The spiral threads
   through a full-width SLOT at each crossing. Its BASE cross-laps the hub: a
   horizontal slot 3mm above the bottom takes the disc (so the disc rides 3mm up
   and the rib's bottom 3mm are feet through the disc's radial slot). A top tab
   plugs into the (closed) ring slot. Assembly: stretch the spiral, slide each rib
   in from outside, then set the ring onto the top tabs. */

// Sort a list of [r,z] by radius (ascending).
function _sortr(v) = len(v) <= 1 ? v : let(
    p  = v[floor(len(v)/2)][0],
    lo = [for (x = v) if (x[0] <  p) x],
    eq = [for (x = v) if (x[0] == p) x],
    hi = [for (x = v) if (x[0] >  p) x]
) concat(_sortr(lo), eq, _sortr(hi));

// Arm crossings on this rib's plane, kept clear of the hub edge and ring wall.
function rib_crossings(a) = _sortr([
    for (k = [0:n_spirals-1]) for (m = [0:ceil(turns)+1])
        let(u = (a - k*360/n_spirals - spiral_offset + m*360) / Theta_eff,
            r = r_hub + (r_rim - r_hub)*u)
        if (u > 0.03 && u < 0.995 && r > r_hub + ramp_w/2 && r < r_rim - ramp_w/2)
            [ r, pot_height*u ]
]);

module rib2d(a) {
    cr = rib_crossings(a);
    sw = ramp_w / 2;                 // half spiral width
    hw = (thickness + fit) / 2;      // half slot height (spiral thickness)
    r_in = r_hub - base_engage;      // inner edge (over the hub, for the cross-lap)
    difference() {
        union() {
            // triangle with a DIAGONAL cutoff (inner-top removed): inner-bottom ->
            // diagonal up to the rim -> flat top to r_out -> rounded outer-bottom.
            hull() {
                translate([r_in, 0]) square(0.01);
                translate([r_rim, pot_height]) square(0.01);
                translate([r_out, pot_height]) square(0.01);
                translate([r_out - outer_fillet, outer_fillet]) circle(r = outer_fillet);
            }
            translate([ring_c - tab_w/2, pot_height]) square([tab_w, tab_thru]); // ring top tab
        }
        // full-width slots the spiral threads through
        for (c = cr)
            translate([c[0] - sw, c[1] - hw]) square([ramp_w, thickness + fit]);
        // base cross-lap: horizontal slot 3mm above the bottom takes the hub disc
        // (open at the inner edge so the rib slides on from outside)
        translate([r_in - 1, thickness]) square([base_engage + 1, thickness + fit]);
    }
}


// ! rib2d(0);

/* ================= 2D cutting layout ================= */


// Place the 4 ribs into the corners of a `side` square.
module ribs_layout(side) {
    m = 4;
    S = side/2 - m;                 // small margin from the very edge
    corners = [[ S,  S,  1, -1], [-S,  S, -1, -1],
               [-S, -S, -1,  1], [ S, -S,  1,  1]];
    for (i = [0 : n_ribs-1])
        translate([corners[i][0], corners[i][1]])
            scale([corners[i][2], corners[i][3]])
            translate([-r_out, 0]) rib2d(i*360/n_ribs);
}

module layout2d() {
    *%square([stock, stock], center = true);   // stock outline (reference, not cut)
    disc2d();
    ribs_layout(layout_side);
}

// ! layout2d();

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
    color("BurlyWood")
        translate([0, 0, -thickness])
        linear_extrude(thickness) disc2d();                 // BOTTOM = the cut
    color("SteelBlue") translate([0,0,pot_height]) linear_extrude(thickness) ring2d(); // TOP ring
    color("SaddleBrown")                                                   // ribs between
            for (i = [0:n_ribs-1])
            rotate([0,0,i*360/n_ribs])
                rotate([90,0,0])
                linear_extrude(thickness, center=true)
                rib2d(i*360/n_ribs);
    color("Goldenrod")                                                     // extra: spiral arms
        for (k = [0:n_spirals-1]) arm3d(k*360/n_spirals + spiral_offset);
}

/* ================= 2D frame (single-kerf pipeline) ================= */
// The disc WITHOUT the spiral slots (so the outline export doesn't double-cut
// the thin spirals) + the ribs. The spiral centerlines are echoed for the
// wrapper (orpot_gcode.py) to cut single-kerf as open paths.

module frame2d() {
    disc2d(with_spirals = false);
    ribs_layout(layout_side);
}

/* ================= top level ================= */
echo(str("turns=", turns, "  twist=", twist, " deg  disc od=", 2*r_out/IN, "in"));
if (MODE == "frame") {
    echo("DISC_AREA", PI*r_out*r_out);      // guard: a rib fused into the disc blows this up
    for (k = [0:n_spirals-1])              // echoed for the single-kerf wrapper
        echo("SPIRAL", spiral_centerline(k*360/n_spirals + spiral_offset));
}
if (MODE == "assembled") assembled3d();
else if (MODE == "frame") frame2d();
else                      layout2d();
