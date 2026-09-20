
bottom_w = 47;
bottom_diag = 61;
top_w = 60;
top_diag = 75;
height = 82;

bottom_exp_diag = 2 * bottom_w / 2 /sqrt(2);
top_exp_diag = 2 * top_w / 2 /sqrt(2);

bottom_or = abs(bottom_exp_diag - bottom_diag) / 2;
top_or = abs(top_exp_diag - top_diag) / 2;

echo(top_w=top_w, top_diag=top_diag, bottom_w=bottom_w, bottom_exp_diag=bottom_exp_diag,
    bottom_or=bottom_or, top_or=top_or);

module minipot() {
    rotate([0, 0, 45])
        minkowski() {
            cylinder($fn=4, h=height, r1=bottom_diag/2 - bottom_or, r2=top_diag/2 - top_or);
            cylinder(r1=bottom_or, r2=top_or);
        }
}

// ! minipot();

th = 3;
lift = 10;
brace_h = height + lift;
brace_ext = 10;
brace_w = top_w + 2*brace_ext + brace_h;

module brace_arm() {
    difference() {
        translate([brace_w/2 - top_w/2 - 2*brace_ext, 0, brace_h/2 - lift])
            cube([brace_w, th, brace_h], center=true);
        translate([brace_w -top_w/2 - brace_ext*2, -th, -lift])
            rotate([0, -atan(brace_h / (brace_w - top_w - 1 * brace_ext)), 0])
            cube([brace_h *2, th*2, brace_h*2]);
        translate([-top_w/2 - brace_ext*2, th, -lift])
            rotate([0, 90-atan(brace_h / brace_ext), 0])
            rotate([0, 0, 180])
            cube([brace_ext*2, th*2, brace_h + brace_ext]);
        translate([(brace_w - top_w)/2, 0, 21])
            rotate([90, 0, 0])
            cylinder(r=17, h=th*2, center=true);
        translate([(brace_w - top_w)/2 - 11, 0, 53])
            rotate([90, 0, 0])
            cylinder(r=17/2, h=th*2, center=true);
        translate([(brace_w - top_w)/2 + 30, 0, 7])
            rotate([90, 0, 0])
            cylinder(r=17/2, h=th*2, center=true);
        minipot();

    }
}

module x_arm() {
    difference() {
        brace_arm();
        translate([0, 0, -lift/4])
            cube([th, th*2, lift/2], center=true);
    }
}

// ! x_arm();

module y_arm() {
    rotate([0, 0, 90]) {
        difference() {
            brace_arm();
            translate([0, 0, -3*lift/4])
                cube([th, th*2, lift/2], center=true);
        }
    }
}

// ! y_arm();

module plate() {
    translate([0, 0, 0])
        rotate([90, 0, 0])
        x_arm();
    translate([165, -height + lift, 0])
        rotate([0, 90, 90])
        y_arm();
}

// ! projection() plate();

module design() {
    // % minipot();
    x_arm();
    y_arm();
}

design();
