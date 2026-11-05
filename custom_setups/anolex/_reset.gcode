; _reset.gcode -- RESERVED: run by the front button ($User/Macro0=$SD/Run=_reset.gcode). Do not delete.
; Homes X and Y only (Z is left alone), moves to the laser bed origin, and stores it as the G54 work zero.
M5
$HX
$HY
G90
G53 G0 X21 Y8
G10 L2 P1 X21 Y8
