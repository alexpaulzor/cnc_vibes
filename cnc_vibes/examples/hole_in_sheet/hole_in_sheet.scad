// hole_in_sheet — parametric sheet with a grid of circular holes.
//
// One source, two outputs:
//   openscad -o build/hole_in_sheet.dxf -D 'mode="dxf"' hole_in_sheet.scad
//   openscad -o build/hole_in_sheet.stl -D 'mode="stl"' hole_in_sheet.scad
//
// DXF is the 2D top-down silhouette (for FreeCAD Path 2.5D operations).
// STL is the 3D solid (for inspection or future 3D-contour CAM).

// ---- parameters -------------------------------------------------------
sheet_x = 200;
sheet_y = 100;
sheet_z = 6;     // material thickness

hole_dia = 8;
hole_grid_x = 4;
hole_grid_y = 2;
hole_margin = 20;

// ---- output mode ------------------------------------------------------
mode = "stl";    // overridden via -D 'mode="dxf"'

// ---- geometry ---------------------------------------------------------
module main_solid() {
    difference() {
        cube([sheet_x, sheet_y, sheet_z]);
        for (i = [0:hole_grid_x-1], j = [0:hole_grid_y-1]) {
            x = hole_margin + i * (sheet_x - 2*hole_margin) / (hole_grid_x - 1);
            y = hole_margin + j * (sheet_y - 2*hole_margin) / (hole_grid_y - 1);
            translate([x, y, -1])
                cylinder(d=hole_dia, h=sheet_z+2, $fn=64);
        }
    }
}

if (mode == "dxf") projection(cut=false) main_solid();
else               main_solid();
