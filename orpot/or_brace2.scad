th = 3;

bottom_w = 47;
top_w = 62;
rib_c_c = 30;
// overhang_l = 10;
lift = 20;
top_h = 85;

module pot() {
    rotate([0, 0, 45])
        cylinder(h=top_h, r1=bottom_w/2*sqrt(2), r2=top_w/2*sqrt(2), center=false, $fn=4);
}

module fin() {
    difference() {
        union() {
            translate([-bottom_w, 0, -th/2])
                cube([2 * bottom_w, top_h, th]);
        }
        translate([0, lift, 0])
            rotate([-90, 0, 0])
            pot();

        for (i=[-1, 1]) {
            translate([i * rib_c_c/2, i + lift/2 + i * lift/4, 0])
                cube([th, lift/2 + 2, 2 * th], center=true);
        }
    }
}

// ! fin();

module design() {
    % color([0, 0.3, 0, 0.5])
        translate([0, 0, lift])
        pot();
    for (i=[-1, 1]) {
        translate([0, i * rib_c_c/2, 0])
            rotate([90, 0, 90 + i * 90])
            fin();
        translate([i * rib_c_c/2, 0, 0])
            rotate([90, 0, i * 90])
            fin();
    }
}

design();

module plate() {
    fin();
    translate([bottom_w * 2 + th, 0, 0])
        fin();
    translate([bottom_w, top_h + lift + th, 0])
        rotate([0, 0, 180])
        fin();
    translate([bottom_w + bottom_w * 2 + th, top_h + lift + th, 0])
        rotate([0, 0, 180])
        fin();
}

!projection() plate();
