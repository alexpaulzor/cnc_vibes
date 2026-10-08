#!/bin/bash
# PRINCIPALS / CAVAGNOLO, 10mm center letters (hub grows to fit). Run from jigsawzall/.
BASE="BRIAN+GILLIAN --font round --center-text PRINCIPALS+CAVAGNOLO --center-style flat --outline-etch-mm 1.0 --center-cap-mm 10"
O=out/principals2
run() { name=$1; shift; python scripts/ring_prototype.py $BASE "$@" --out $O/$name.png > $O/$name.log 2>&1; }
run g5_s1 --seed 1 --center-min-gap-mm 5 & run g5_s2 --seed 2 --center-min-gap-mm 5 & run g3_s1 --seed 1 --center-min-gap-mm 3 & run g3_s2 --seed 2 --center-min-gap-mm 3 & wait
run g5_s3 --seed 3 --center-min-gap-mm 5 & run g5_s4 --seed 4 --center-min-gap-mm 5 & run g3_s3 --seed 3 --center-min-gap-mm 3 & run g3_s4 --seed 4 --center-min-gap-mm 3 & wait
run g5_s5 --seed 5 --center-min-gap-mm 5 & run g5_s6 --seed 6 --center-min-gap-mm 5 & run g3_s1_rows2 --seed 1 --center-min-gap-mm 3 --rows 2 & run g3_s1_star --seed 1 --center-min-gap-mm 3 --ornament star & wait
run g3_s1_gillian_top --seed 1 --center-min-gap-mm 3 --center-word GILLIAN & run g3_s1_no_ornament --seed 1 --center-min-gap-mm 3 --ornament none & wait
grep -H score $O/*.log
