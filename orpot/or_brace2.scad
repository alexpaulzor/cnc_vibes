// TODO: increase $fn per item
// $fn = 64;
th = 3.3;

bottom_w = 47;
top_w = 65;
top_or = 10;
rib_c_c = 30;
// overhang_l = 10;
lift = 20;
top_h = 85;

module pot() {
    rotate([0, 0, 45])
        cylinder(h=top_h, r1=bottom_w/2*sqrt(2), r2=top_w/2*sqrt(2), center=false, $fn=4);
}

// ! pot();

module flowercube(cube_dims, num_ovals=6, scale=1/3, width=13, start_r0=1, start_offs=1) {
    max_r = sqrt(cube_dims.x ^ 2 + cube_dims.y ^ 2)/2;
    major_inside_scale = (max_r - width)/max_r;
    minor_inside_size = max_r * scale - width;
    minor_inside_scale = minor_inside_size / max_r;
    echo(max_r=max_r, major_inside_scale=major_inside_scale, minor_inside_scale=minor_inside_scale);
    dr = 360/num_ovals/2;
    r0 = ((num_ovals % 2) == start_r0) ? dr/2 : 0;
    for (r=[-90+dr*start_offs + r0:dr:90-dr*start_offs + r0]) {
        rotate([0, 0, r]) {
            difference() {
                scale([1, scale, 1])
                    cylinder(r=max_r, h=cube_dims.z, center=true, $fn=128);
                scale([major_inside_scale, minor_inside_scale, 1])
                    cylinder(r=max_r, h=cube_dims.z + 1, center=true, $fn=128);
            }
        }
    }
}

// ! flowercube([2 * bottom_w, top_h, th]);

module fin() {
    difference() {
        union() {
            * translate([-bottom_w, 0, -th/2])
                cube([2 * bottom_w, top_h, th]);
            translate([0, top_h/2, 0])
                
                flowercube([2 * bottom_w, top_h, th]);
        }
        translate([0, lift, 0])
            rotate([-90, 0, 0])
            pot();

        // for (i=[-1, 1]) {
        //     translate([i * rib_c_c/2, i + lift/2 + i * lift/4, 0])
        //         cube([th, lift/2 + 2, 2 * th], center=true);
        // }
    }
}

// ! fin();

module fin1() {
    difference() {
        union() {
            fin();
            // translate([0, lift/2 + lift/8, 0])
            //     cube([bottom_w + lift/2, lift*3/4, th], center=true);
            for (i=[-1, 1]) {
                translate([i * (rib_c_c/2 + th), lift/2 - th/4, 0])
                    cube([lift, lift+th/2, th], center=true);
            }
        }
        for (i=[-1, 1]) {
            translate([i * rib_c_c/2, lift*3/4 + 1, 0])
                cube([th, lift/2 + 2, 2 * th], center=true);
        }
    }
    
}

//  ! fin1();

module fin2() {
    difference() {
        union() {
            fin();
            for (i=[-1, 1]) {
                translate([i * (rib_c_c/2 + th), lift/2 - th/4, 0])
                    cube([lift, lift+th/2, th], center=true);
            }
        }
        for (i=[-1, 1]) {
            translate([i * rib_c_c/2, -1, 0])
                cube([th, lift + 2, 2 * th], center=true);
        }
    }
}

//  ! fin2();

module fins() {
    fin1();
    translate([rib_c_c/2, 0, rib_c_c/2])
        rotate([0, 90, 0])
        fin2();
}

//! fins();

module allfins() {
    for (i=[-1, 1]) {
        color(i > 0 ? "red" : "pink", alpha=0.75 + 0.25 * i)
            translate([0, i * rib_c_c/2, 0])
            rotate([90, 0, 90 + i * 90])
            fin1();
        color(i > 0 ? "green" : "lightgreen", alpha=0.75 + 0.25 * i)
            translate([i * rib_c_c/2, 0, 0])
            rotate([90, 0, i * 90])
            fin2();
    }
}

// ! allfins();

topplate_h =  top_h - th - 1;

module topplate() {
    difference() {
        union() {
            flowercube([100, 100, th], start_r0=0, start_offs=0);
            linear_extrude(th, center=true)
                offset(top_or + 13)
                square([w, w], center=true);
        }
        // % translate([0, 0, -top_h + th]) pot();
        w =  top_w - 2*top_or - th;
        linear_extrude(th * 2, center=true)
            offset(top_or)
            square([w, w], center=true);
        translate([0, 0, -topplate_h]) {
        //   % allfins();
            import("allfins.stl");
        }
    }
}

// ! 
// projection()
// topplate();

module design() {
    * % color([0, 0.3, 0, 0.5])
        translate([0, 0, lift])
        pot();
    allfins();
    translate([0, 0, topplate_h])
        topplate();
}

design();

module plate() {
    translate([-top_h*3/2, -top_h/2, 0])
        fin1();
    translate([top_h*3/2, -top_h/2, 0])
        rotate([0, 0, 180])
        fin2();
    translate([0, 0, 0])
        topplate(); 
}

// !     plate();

module platefins() {
    translate([0, 0, 0])
        fin1();
    translate([top_h/2, top_h + 2 * lift, 0])
        rotate([0, 0, 180])
        fin2();
}

!     
    projection() 
platefins();

// !
//     projection() 
//     // fin1();
//     // fin2();
//     topplate();

