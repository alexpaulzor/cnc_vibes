// orpot.scad — laser-cut "expanding spiral" orchid pot.
//
// Hand-editable (NOT generated). Units: mm. One square of 3mm MDF -> a round
// disc with a spiral cut (the pot; it expands into a bowl when the rim is lifted
// off the hub) plus 3 radial ribs from the leftover corners.
//
//   MODE = "preview"   -> flat cut + the assembled pot floating above it (default)
//   MODE = "cut"       -> flat 2D cutting pattern (export DXF/SVG for the laser)
//   MODE = "assembled" -> 3D preview of the pot stretched to `pot_height`
//   MODE = "frame"     -> cut without spirals (single-kerf gcode pipeline)
//
// Stretch is by TWIST, not by pulling in: the rigid hub and rim keep their radii,
// so as it rises the arms unwind and the rim rotates vs the hub by `twist`. The
// ring rib-slots are pre-offset by that twist so the ribs seat in true radial
// planes. The wood stays flat, so a cross-section reads as stacked rings.

/* ================= parameters ================= */
MODE       = "preview";  // "preview" (cut + floating assembled), "cut", "assembled", "frame"

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
spiral_offset = 45;   // rotate the spiral cut(s) well off the rib slots so the
                      // slot-to-spiral sliver is wide enough not to crack

n_ribs     = 3;       // radial ribs. 3 gives more clearance between the hub and
                      // the lowest spiral turn than 4.
rib_offset = spiral_offset + 180;  // first rib opposite the spiral start; the rest
                                   // step by 360/n_ribs (120 for 3)
pot_height = 3*IN;    // spiral rise, disc floor -> rim
foot_drop  = 0;       // rib is flush with the disc bottom on the table (no feet)
rib_w      = 0.5*IN;  // rib strut/body thickness reference
tab_w      = 0.5*IN;  // tab length along its slot (12.7), hub + ring
tab_thru   = thickness;      // (unused legacy)
top_tab_up = 2*thickness;    // ring tab: through the ring (thickness) + proud by one thickness
shoulder   = 3;              // min material each side of a slot / step width
base_engage = 20;    // rib<->hub cross-lap length (hub slot reaches this far in
                     // from the first spiral; rib base slot matches)
outer_fillet = 2*IN; // round the rib's outer-bottom corner

layout_side = 300;   // square envelope the parts pack into (= stock; not shrunk yet)

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

hub_3d_r = r_hub + 1/2 * IN; // how much of the bottom disc to show in assembled view

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

// Azimuth (deg) of rib i.
function rib_az(i) = rib_offset + i*360/n_ribs;

module disc2d(with_spirals = true) {
    difference() {
        circle(r = r_out);
        if (with_spirals)
            for (k = [0 : n_spirals-1])
                rotate([0, 0, k*360/n_spirals + spiral_offset]) spiral_cut(kerf);
        // ring slot (pre-offset by twist) for the rib top tab; hub HOLE (mortise)
        // for the rib's down-facing tab.
        for (i = [0 : n_ribs-1]) {
            a = rib_az(i);
            rotate([0, 0, a])         radial_slot(r_hub - tab_w, tab_w);
            rotate([0, 0, a + twist]) radial_slot(ring_c, tab_w + fit);
        }
    }
}

/* ================= rib =================
   In the rib's (x = radius s, y = height z) frame. Inner-top edge is the hybrid
   slant: VERTICAL along each spiral slot's inner edge (so the spiral threads in
   flush, no lip) and SLANTED between slots. Outer-bottom corner rounded to
   outer_fillet. Feet drop foot_drop below the disc; a horizontal cross-lap slot at
   the disc height takes the hub disc. Ring top tab plugs the (closed) ring slot
   and stands proud by one thickness. Assembly: stretch the spiral, slide each rib
   in from outside through the seam, then set the ring onto the top tabs. */

// Sort a list of [r,z] by radius (ascending).
function _sortr(v) = len(v) <= 1 ? v : let(
    p  = v[floor(len(v)/2)][0],
    lo = [for (x = v) if (x[0] <  p) x],
    eq = [for (x = v) if (x[0] == p) x],
    hi = [for (x = v) if (x[0] >  p) x]
) concat(_sortr(lo), eq, _sortr(hi));

// Arm crossings on this rib's plane: [r, z]. Every place the arm crosses gets a
// slot (excluding only the very ends), else the arm hits solid rib.
function rib_crossings(a) = _sortr([
    for (k = [0:n_spirals-1]) for (m = [0:ceil(turns)+1])
        let(u = (a - k*360/n_spirals - spiral_offset + m*360) / Theta_eff,
            r = r_hub + (r_rim - r_hub)*u)
        if (u > 0.02 && u < 0.98)
            [ r, foot_drop + pot_height*u ]
]);

module rib2d(a) {
    cr = rib_crossings(a);
    sw = ramp_w / 2;                 // half spiral width
    // arm is ~flat where it crosses the rib; only a small tangential rise over the
    // 3mm rib thickness, so the spiral slot is just over one thickness.
    slot_slant = pot_height * (thickness/rbar) / (Theta_eff * PI/180);
    vh = (thickness + fit + slot_slant) / 2;   // half spiral-slot height (~3.3mm)
    r_in = r_hub - base_engage;      // inner edge (over the hub)
    z_rim = pot_height;              // rim height (feet on the table at z=0)
    r_tab = r_hub - tab_w;           // down-tab / hub-hole centre
    base_clear = r_hub + ramp_w;     // cut the rib base up to here to clear disc + low spiral
    // Inner edge: vertical along each spiral slot's inner edge, slanting between.
    inner = [ for (c = cr) each [ [c[0]-sw, c[1]-vh], [c[0]-sw, c[1]+vh] ] ];
    union() {
        difference() {
            union() {
                // fin: inner slant -> rim -> flat top -> rounded outer-bottom corner
                polygon(concat(
                    [ [r_in, 0] ], inner, [ [r_rim - sw, z_rim] ], round_outer(z_rim)));
                translate([r_in, 0]) square([base_clear - r_in, 3*thickness]);      // solid hub base
                translate([ring_c - tab_w/2, z_rim]) square([tab_w, top_tab_up]);   // ring top tab
            }
            // spiral slots (open on the inner edge; spiral threads through)
            for (c = cr)
                translate([c[0] - sw, c[1] - vh]) square([ramp_w + fit, 2*vh]);
            // base cutout: clear the centre disc + the lowest spiral turn (raise the
            // bottom to one thickness for r < base_clear); the down tab is added back.
            translate([r_in - 1, -1]) square([base_clear - r_in + 1, thickness + 1]);
        }
        // down tab into the hub hole: 1/2in wide, one thickness deep, flush with the
        // disc bottom on the table (does not extend below).
        translate([r_tab - tab_w/2, 0]) square([tab_w, thickness]);
    }
}

// Outer profile from the rim corner down to the foot, with an outer_fillet-radius
// rounded outer-bottom corner (point list for the rib polygon).
function round_outer(z_rim, n = 24) = concat(
    [ [r_out, z_rim] ],
    [ for (i = [0:n]) let(t = 90*i/n)          // arc: (r_out, fillet) -> (r_out-fillet, 0)
        [ r_out - outer_fillet + outer_fillet*cos(t),
          outer_fillet - outer_fillet*sin(t) ] ]
);


// ! rib2d(0);

/* ================= 2D cutting layout ================= */


// Place the ribs into the corners of a `side` square (first n_ribs corners).
module ribs_layout(side) {
    m = 4;
    S = side/2 - m;                 // small margin from the very edge
    corners = [[ S,  S,  1, -1], [-S,  S, -1, -1],
               [-S, -S, -1,  1], [ S, -S,  1,  1]];
    for (i = [0 : n_ribs-1])
        translate([corners[i][0], corners[i][1]])
            scale([corners[i][2], corners[i][3]])
            translate([-r_out, 0]) rib2d(rib_az(i));
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
            rotate([0, 0, rib_az(i) + twist]) radial_slot(ring_c, tab_w + fit);
    }
}

module arm3d(phase, seg = 60) {                    // extra-credit lofted spiral
    n = seg * turns;
    for (i = [0 : n-1]) hull()
        for (u = [i/n, (i+1)/n]) {
            r   = r_hub + (r_rim - r_hub)*u;
            z   = foot_drop + pot_height*u;
            phi = Theta_eff*u + phase;
            translate([r*cos(phi), r*sin(phi), z]) rotate([0,0,phi])
                cube([ramp_w, 0.1, thickness], center = true);
        }
}

module assembled3d() {
    // BOTTOM: only the centre disc (trimmed near where the spiral starts), so the
    // view is neat — the real cut disc includes the whole spiral, but showing just
    // the hub + rib slots reads more clearly. Disc rides foot_drop up on the feet.
    color("BurlyWood")
        translate([0,0,foot_drop])
        linear_extrude(thickness) intersection() {
            disc2d(with_spirals = false);
            circle(r = hub_3d_r);   // trim to just past the hub slots
        }
    color("SteelBlue") translate([0,0,foot_drop+pot_height])
        linear_extrude(thickness) ring2d();                                // TOP ring
    color("SaddleBrown")                                                   // ribs between
            for (i = [0:n_ribs-1])
            rotate([0,0,rib_az(i)])
                rotate([90,0,0])
                linear_extrude(thickness, center=true)
                rib2d(rib_az(i));
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

// Solid 3D ribs in place (for the assembled view and the clip test).
module ribs3d() {
    for (i = [0:n_ribs-1])
        rotate([0,0,rib_az(i)]) rotate([90,0,0])
            linear_extrude(thickness, center=true) rib2d(rib_az(i));
}
module arms3d() { for (k = [0:n_spirals-1]) arm3d(k*360/n_spirals + spiral_offset); }

// Where the spiral overlaps the rib bodies. Should be empty (spiral threads the
// slots). Non-empty -> the spiral clips a rib. Rendered/exported for the test.
module clip_test() { intersection() { arms3d(); ribs3d(); } }

// Default preview: the flat 2D cut (extruded thin) at z=0, with the assembled pot
// floating preview_gap above it, rotationally aligned with the bottom disc.
preview_gap = 20;
module preview() {
    color("BurlyWood") linear_extrude(thickness) layout2d();
    translate([0, 0, thickness + preview_gap]) assembled3d();
}

/* ================= top level ================= */
echo(str("turns=", turns, "  twist=", twist, " deg  disc od=", 2*r_out/IN, "in"));
if (MODE == "frame") {
    echo("DISC_AREA", PI*r_out*r_out);      // guard: a rib fused into the disc blows this up
    for (k = [0:n_spirals-1])              // echoed for the single-kerf wrapper
        echo("SPIRAL", spiral_centerline(k*360/n_spirals + spiral_offset));
}
if      (MODE == "assembled") assembled3d();
else if (MODE == "frame")     frame2d();
else if (MODE == "cliptest")  clip_test();
else if (MODE == "cut")       layout2d();
else                          preview();   // default: cut + floating assembled
