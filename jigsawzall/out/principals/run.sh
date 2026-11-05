#!/bin/bash
# PRINCIPALS / CAVAGNOLO ring: seeds + knob variations. Run from jigsawzall/.
BASE="BRIAN+GILLIAN --font round --center-text PRINCIPALS+CAVAGNOLO --center-style flat --outline-etch-mm 1.0"
O=out/principals
run() { name=$1; shift; python scripts/ring_prototype.py $BASE "$@" --out $O/$name.png > $O/$name.log 2>&1; }
run s1 --seed 1 & run s2 --seed 2 & run s3 --seed 3 & run s4 --seed 4 & wait
run s5 --seed 5 & run s6 --seed 6 & run s1_rows2 --seed 1 --rows 2 & run s1_star --seed 1 --ornament star & wait
run s1_gillian_top --seed 1 --center-word GILLIAN & run s1_no_ornament --seed 1 --ornament none & wait
grep -H score $O/*.log
