# cnc_vibes — OpenSCAD → GCode pipeline for GRBL-class CNC routers.
#
# Per-example build targets follow the pattern:
#   examples/<name>/build/<name>.{dxf,stl,gcode}
# The .FCStd CAM project is created once by hand in FreeCAD; the gcode
# target re-posts from it. See cnc_for_the_scad.md §6.

OPENSCAD     ?= openscad
FREECAD_CMD  ?= FreeCADCmd
PYTHON       ?= python3
PROFILE      ?= profiles/default.yaml
TOOLS        ?= profiles/tools.yaml

EXAMPLES := hole_in_sheet
.PHONY: all test validate clean $(EXAMPLES)

all: $(addprefix examples/,$(addsuffix /build/.geom_stamp,$(EXAMPLES)))

# Per-example geometry stamp: forces both DXF and STL regeneration.
examples/%/build/.geom_stamp: examples/%/build/%.dxf examples/%/build/%.stl
	@touch $@

# Generic SCAD -> DXF
examples/%/build/%.dxf: examples/%/%.scad | examples/%/build
	$(OPENSCAD) -o $@ -D 'mode="dxf"' $<

# Generic SCAD -> STL
examples/%/build/%.stl: examples/%/%.scad | examples/%/build
	$(OPENSCAD) -o $@ -D 'mode="stl"' $<

examples/%/build:
	mkdir -p $@

# GCode regeneration from a saved FreeCAD project.
# Wired up in a later iteration — implementing requires a real .FCStd to
# drive. See cnc_for_the_scad.md §5 and §8.
examples/%/build/%.gcode: examples/%/%.FCStd
	@echo "TODO: $(FREECAD_CMD) scripts/freecad_post.py -- $< $@"
	@echo "      (not implemented yet; for now, post via FreeCAD GUI and save here)"
	@false

# Validate a GCode file against the machine profile and tool table.
# Usage:  make validate JOB=examples/hole_in_sheet/build/hole_in_sheet.gcode
validate:
	@test -n "$(JOB)" || (echo "Usage: make validate JOB=path/to/file.gcode"; exit 2)
	$(PYTHON) scripts/gcode_validate.py \
		--profile $(PROFILE) \
		--tools $(TOOLS) \
		--gcode $(JOB)

test:
	$(PYTHON) -m pytest -q tests/

clean:
	rm -rf examples/*/build
