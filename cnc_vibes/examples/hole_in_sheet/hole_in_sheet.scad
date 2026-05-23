// hole_in_sheet — parametric sheet with a grid of circular holes.
//
// One source, two outputs:
//   openscad -o build/hole_in_sheet.dxf -D 'mode="dxf"' hole_in_sheet.scad
//   openscad -o build/hole_in_sheet.stl -D 'mode="stl"' hole_in_sheet.scad
//
// DXF is the 2D top-down silhouette (for FreeCAD Path 2.5D operations).
// STL is the 3D solid (for inspection or future 3D-contour CAM).

// ---- parameters -------------------------------------------------------
sheet_x = 60;
sheet_y = 45;
sheet_z = 3;     // material thickness


hole_dia = 3.2;
hole_grid_x = 2;
hole_grid_y = 2;
hole_margin = 3;
corner_or = 3;

// ---- output mode ------------------------------------------------------
mode = "stl";    // overridden via -D 'mode="dxf"'
//mode = "dxf";

// ---- geometry ---------------------------------------------------------
module main_solid() {
    difference() {
        translate([corner_or, corner_or, 0])
            minkowski() {
                cube([sheet_x - 2*corner_or, sheet_y - 2*corner_or, sheet_z/2]);
                cylinder(r=corner_or, h=sheet_z/4, $fn=64);
            }
        for (i = [0:hole_grid_x-1], j = [0:hole_grid_y-1]) {
            x = hole_margin + i * (sheet_x - 2*hole_margin) / (hole_grid_x - 1);
            y = hole_margin + j * (sheet_y - 2*hole_margin) / (hole_grid_y - 1);
            translate([x, y, -1])
                cylinder(d=hole_dia, h=sheet_z+2, $fn=64);
        }
        
        # for (x = [-20 * floor((sheet_x/2 - corner_or/2) / 20):20:sheet_x/2 - corner_or/2]) {
            for (y = [-20 * floor((sheet_y/2 - corner_or/2) / 20):20:sheet_y/2 - corner_or/2]) {
                 echo(x=x, y=y);
                translate([
                    sheet_x / 2 + x, 
                    sheet_y/2 + y, 
                    -1
                ])
                    cylinder(d=hole_dia, h=sheet_z+2, $fn=64);
            }
        }
    }
}

if (mode == "dxf") projection(cut=false) main_solid();
else               main_solid();
