// orpot.scad — laser-cut "expanding spiral" orchid pot, 2D cutting pattern.
//
// Hand-editable starting point (NOT generated). Units: mm. Designed for one
// square of 3mm MDF: a round disc with two interleaved spiral cuts (the pot —
// it expands into a cone when you lift the hub) plus 4 S-curve ribs nested in
// the leftover corners.
//
// Render / export:
//   - This file is 2D. F5 to preview, then Design > Render (F6) and
//     File > Export > Export as DXF/SVG for the laser.
//   - Set PREVIEW_3D = true for a quick extruded 3D sanity check (not for cut).
//
// The knob for "as large a pot as fits" is disc_dia: push it up until the ribs
// stop fitting in the corners (they turn red / overlap the disc).

/* ================= parameters ================= */
stock      = 300;   // square stock edge
thickness  = 3;     // MDF thickness (slot widths + 3D preview)
kerf       = 0.20;  // laser kerf; also the spiral cut width
fit        = 0.10;  // extra slot clearance for a slip fit

disc_dia   = 200;   // POT outer diameter (<= stock). Main size knob.
hub_dia    = 50;    // solid center hub diameter
ring_w     = 12;    // solid outer rim-ring width
ramp_w     = 12;    // spiral arm (ramp) width  = spacing between cuts
n_spirals  = 2;     // interleaved spiral arms

n_ribs     = 4;     // radial ribs (from the corner offcuts)
rise       = 40;    // assembled pot height (rib height in the flat pattern)
rib_w      = 12;    // rib strut width
tab        = 5;     // rib end-tab length (into hub / ring slots)
top_tab_w  = 10;    // rib top-tab width (tangential -> into ring slot)
bot_tab_w  = 10;    // rib bottom-tab width (into hub slot)

PREVIEW_3D = false; // true = extrude to thickness for a rough 3D look

$fn = 180;

/* ================= derived ================= */
r_out  = disc_dia/2;
r_hub  = hub_dia/2;
r_rim  = r_out - ring_w;          // inner edge of the rim ring; cuts end here
pitch  = n_spirals * ramp_w;      // radial advance per full revolution
turns  = (r_rim - r_hub) / pitch; // whole-ish turns that fit the annulus
ring_c = r_out - ring_w/2;        // rib top-tab lands here (fixed from edge)
slot_w = thickness + fit;         // rib slot width (tangential)

/* ================= spiral disc ================= */

// Archimedean spiral point: radius grows linearly with angle (degrees).
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
    rotate([0,0,90])              // long axis radial
      translate([0, rc])
        square([slot_w, len], center = true);
}

module disc2d() {
    difference() {
        circle(r = r_out);                       // the disc / pot blank
        // two interleaved spiral cuts, 360/n_spirals apart
        for (k = [0 : n_spirals-1])
            rotate([0, 0, k*360/n_spirals]) spiral_cut(kerf);
        // rib slots: one in the HUB and one in the RIM RING per rib
        for (i = [0 : n_ribs-1]) rotate([0, 0, i*360/n_ribs]) {
            radial_slot(r_hub - bot_tab_w/2, bot_tab_w);   // hub slot
            radial_slot(ring_c,              top_tab_w);    // ring slot
        }
    }
}

/* ================= S-curve rib ================= */

// Cubic Bezier point (works on 2D vectors).
function bez(p0,p1,p2,p3,t) = let(u = 1-t)
    u*u*u*p0 + 3*u*u*t*p1 + 3*u*t*t*p2 + t*t*t*p3;

// Thick polyline: a chain of hull()'d discs of radius `hw` along `pts`.
module thick_path(pts, hw) {
    for (i = [0 : len(pts)-2])
        hull() { translate(pts[i]) circle(hw); translate(pts[i+1]) circle(hw); }
}

// One rib in its own (x = radius s, y = height z) frame: an ogee strut that
// sweeps from the rim ring (top, outer) inward and down to the hub (bottom).
module rib2d() {
    hw  = rib_w/2;
    top = [ring_c, rise - hw];
    bot = [r_hub,  hw];
    p1  = [ring_c, rise*0.55];   // leave the rim heading down (outer)
    p2  = [r_hub,  rise*0.45];   // sweep inward, then drop to the hub
    union() {
        thick_path([ for (i = [0:48]) bez(top, p1, p2, bot, i/48) ], hw);
        translate([ring_c - top_tab_w/2, rise - hw])   // top tab (into ring slot)
            square([top_tab_w, hw + tab]);
        translate([r_hub - bot_tab_w, -tab])           // bottom tab (into hub slot)
            square([bot_tab_w + hw, tab + hw]);
    }
}

/* ================= layout on the stock ================= */

module layout2d() {
    // stock outline for reference (not a cut) — comment out for export
    %square([stock, stock], center = true);

    disc2d();

    // 4 ribs laid along the diagonals into the corner offcuts. Each rib's inner
    // (hub) end starts just outside the disc; it runs out toward the corner.
    // Tune `gap` / disc_dia so the ribs sit clear of the disc and the edges.
    gap = 4;
    tx  = r_out + gap - (r_hub - bot_tab_w);   // shift so the hub tab clears the disc
    for (ang = [45, 135, 225, 315])
        rotate([0, 0, ang]) translate([tx, 0]) rib2d();
}

/* ================= top level ================= */
if (PREVIEW_3D)
    linear_extrude(height = thickness) layout2d();
else
    layout2d();
