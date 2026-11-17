# Machine 2 vision positions relative to the hub centre

Recorded 2026-11-14 for the camera position of 13 Nov 2026 (CHANGES 95-100), branch `machine2-io-fe-db-cam`. Source:
the `siemens.vision.*` keys in `ApplicationConfiguration.Machine2.properties` (the three properties files are identical
for these keys). Pictures are 1280 x 720 (every picture is resized to this before a check). Offsets are (dx, dy) in
pixels from the **hub segmentation centre `hub.segment.center` = (645, 334)**; x to the right, y downwards.

Purpose: a later step to make the checks immune to small camera movements - find the hub (or another fixed feature)
in each picture, then place every region by these offsets (and scale them by the measured zoom) instead of by fixed
image coordinates.

## Since CHANGES 102: the centres follow the knuckle (2026-11-15)

With `siemens.vision.knuckle.method=segmentation` the knuckle check (state 3) finds the spindle, the two machined bosses
with the fixture pins in them and the lower arm (`camera/KnuckleLocator.py`), and builds the cycle's frame
(`camera/VisionFrame.py`): camera rotation and zoom from the pin line against its usual angle / length for the type and
side, and the anchor = where the pins put the spindle of that type / side (the spindle mask is the cross-check). Every
centre in the table below
is then placed for the cycle as

    centre = anchor + zoom * R(rotation) * (reference centre of the type - mean spindle of the type)

where, since the follow-up of CHANGES 102, the reference centre and spindle are per type: DOST and DOSTPLUS each have
their own reference centre (`siemens.vision.frame.centre.<name>.<dost|dostplus>`, since CHANGES §105; type spindles
`siemens.vision.frame.type.spindle.*`, pins `siemens.vision.frame.nominal.pins.*`) and mean spindle (DOST (642.35, 358.45), DOSTPLUS
(642.2, 359.7)), so each type has its own displacement of every centre from the spindle. The `siemens.vision.*.center`
keys below are used only when no knuckle frame exists in the cycle (method `regions`; a camera server restarted during a cycle takes the frame back from its state file, camera/CameraStateFile.py; or one restarted
during a cycle). The frame is dropped at the end of every cycle (completed or aborted), at a new cycle's knuckle check
and at a knuckle retake. A camera move beyond `vision.knuckle.frame.max.*` (40 px, 3 deg, 5 %) makes the knuckle check
NOT OK.

Why per type: on the 13 Nov pictures (the same parts through the stations) the DOST features sit 4-5 px higher than
the DOSTPLUS ones - hub inner bore DOST (644.0, 335.5) / DOSTPLUS (643.4, 339.4), top bearing ring (641.8, 338.3) /
(641.6, 343.2), nut ring (644.3, 334.1) / (643.4, 338.5), seated bung face (646, 325) / (647, 330); each type's mean
over its parts (DOST: two parts, 3-7 px apart themselves; DOSTPLUS: one). Each earlier single centre is split by half
its feature's type difference.

| Centre | DOST reference | from DOST spindle | DOSTPLUS reference | from DOSTPLUS spindle |
|---|---|---|---|---|
| hub.segment.center | (645.3, 332.0) | (+2.95, -26.40) | (644.7, 336.0) | (+2.50, -23.75) |
| hub.centre.check | (644.3, 338.0) | (+1.95, -20.40) | (643.7, 342.0) | (+1.50, -17.75) |
| topbearing.annular.center | (642.1, 342.6) | (-0.25, -15.90) | (641.9, 347.5) | (-0.30, -12.25) |
| nutwasher / nobunk nut.detection.center | (644.5, 341.8) | (+2.10, -16.65) | (643.5, 346.2) | (+1.35, -13.50) |
| bunk.outer.disc.center | (642.5, 331.5) | (+0.15, -26.95) | (643.5, 336.5) | (+1.30, -23.20) |
| bunk.seating.seated.centre.dost / .dostplus | (646, 325) / (647, 330) | | same | |
| cap.center | (644.3, 338.0) | (+1.95, -20.40) | (643.7, 342.0) | (+1.50, -17.75) |

Still to confirm with paired pictures (knuckle, hub and cap of the same component over a few full cycles): whether
the hub follows the spindle 1:1 - the DOST RHS knuckles' spindle sits 2.7 px higher than DOST LHS and their centres
move with it; the two DOST parts photographed sat 3-7 px apart at every station, and without their knuckle pictures it
is not known whether that was their spindles (side) or the hub's own seating.

## Checks on the hub and what is pressed onto it (states 5-24)

| Region (key) | Absolute (x, y) | Offset from hub centre (dx, dy) | Size | Note |
|---|---|---|---|---|
| Hub segmentation centre `hub.segment.center` (**reference**) | (645, 334) | (+0, +0) | ring r 116-253 | holes + studs searched in this ring |
| Hub 'no bearing in bore' circle `hub.centre.check` | (644, 340) | (-1, +6) | annulus r 38-60 | only when enforceBearingAbsenceCheck=True (off in production) |
| Top bearing search disc `topbearing.annular.center` | (642, 345) | (-3, +11) | disc r 132; start points 0,0;0,-11 (relative); prompt r 40 | 3 px below the rings' median centre |
| Nut / washer nut search `nutwasher.nut.detection.center` | (644, 344) | (-1, +10) | disc r 80 | washer comparison uses the top bearing ring of the same cycle |
| No-bunk nut search `nobunk.nut.detection.center` | (644, 344) | (-1, +10) | disc r 80 | same as nut / washer |
| Bung (states 13, 24) `bunk.outer.disc.center` | (643, 334) | (-2, +0) | segmented disc r 250, holes within r 135 (110 until 17 Nov, then 125) |  |
| Bung seated position `bunk.seating.seated.centre.dost` | (646, 325) | (+1, -9) | hole-pattern centre within 20.0 px (15.0 until 17 Nov); face r 100.0-145.0; face inside the reference oval 241 x 249 at 122 deg | seating step |
| Bung seated position `bunk.seating.seated.centre.dostplus` | (647, 330) | (+2, -4) | hole-pattern centre within 20.0 px (15.0 until 17 Nov); face r 100.0-145.0; face inside the reference oval 232 x 230 | seating step |
| Cap (state 23) `cap.center` | (644, 340) | (-1, +6) | circles r 109 (DOST) / 76 (DOSTPLUS) / 25 (brightness) | seating: point prompt here; silver outline within 20 px of it |

The split pin + washer check (state 20, `camera/CheckSplitPinAndWasher.py`) has no position: it is a placeholder.

## Measured feature positions behind these settings (13 Nov pictures)

| Feature | Measured | Offset from hub centre | Source |
|---|---|---|---|
| Hub hole circle centre (10 hubs) | (643.5-645.0, 335.9-342.6), r 147.9-149.0 | (-1.5..0, +1.9..+8.6) | CHANGES 96 |
| Hub studs circle | r 181-196, centre about 7 px above the hole circle | | CHANGES 96 |
| Hub bore / recess (cap pictures, fitted) | (642-648, 336-344), r 106-111 | (-3..+3, +2..+10) | CHANGES 99 |
| Top bearing ring centre (median) | (641, 342); DOST about y 335-336, DOSTPLUS 342-344 | (-4, +8) | CHANGES 97 |
| Nut centre | (643.6-644.5, 328.5-336.6); DOST about y 329, DOSTPLUS 336.5 | (-1..0, -5..+3) | CHANGES 98 |
| Silver cap outline, seated | (644.9-645.9, 331.4-332.8), r 103-105 | (0..+1, -3..-1) | CHANGES 99 |
| Brass cap face / bore round it, seated | cap (642-644, 339-340) r 84-86; bore (642-644, 339-340) r 109-114 | (-3..-1, +5..+6) | CHANGES 99 |
| Bung top face, seated | DOST (646, 325) r 120; DOSTPLUS (647, 330) r 112 | DOST (+1, -9); DOSTPLUS (+2, -4) | CHANGES 100 |
| Bung hole pattern centre, seated (YOLO + MobileSAM, laptop run) | DOST (646-651, 317-325); DOSTPLUS (647-653, 322-333) | (+1..+8, -17..-1) | CHANGES 100 |

Parts sit on the fixture with about +/-4 px of their own variation (hub recess spread over the cap pictures; DOST and
DOSTPLUS parts sat a few px apart). Tall parts (cap, bung, nut) are seen a few px off the hub centre because the camera
is not exactly overhead and their tops are nearer the camera (they also look larger: the nut grew x1.09 in the camera
move against x1.035 for the hub).

## Knuckle check (state 3) - before the hub is on the fixture

The knuckle picture has no hub. Its regions are positioned on the knuckle and the fixture; the offsets below use the
same reference point so that both can be moved together. The camera move of 13 Nov measured on the knuckles: zoom
x1.037-1.041, rotation +0.84..+0.98 deg; on the plate x1.049, +0.95 deg (CHANGES 95).

| Region | Shape | Centre (x, y) | Offset from hub centre (dx, dy) |
|---|---|---|---|
| Side box D1 (left) `knuckle.lhs.crop` | rect 409,661 23x23 | (420.5, 672.5) | (-224, +338) |
| Side box D2 (right) `knuckle.rhs.crop` | rect 842,658 23x23 | (853.5, 669.5) | (+208, +336) |
| Contour A (left) `knuckle.common.check.a.polygon.points` | polygon `-64,-135;-32,-135;-32,-110;-36,-109;-40,-108;-44,-108;-48,-108;-52,-108;-56,-108;-60,-108;-64,-109` (relative to the hub centre) | (597.0, 220.7) | (-48, -113) |
| Contour A' (right) `knuckle.common.check.a.alt.polygon.points` | polygon `+23,-135;+55,-135;+55,-106;+51,-106;+47,-106;+43,-106;+39,-106;+35,-108;+31,-108;+27,-110;+23,-110` (relative to the hub centre) | (684.0, 221.6) | (+39, -112) |
| Rectangle A (unused while side.by.difference) | rect 726,200 32x25 | (742.0, 212.5) | (+97, -122) |
| Rectangle A alternate (unused) | rect 668,199 32x25 | (684.0, 211.5) | (+39, -122) |
| B | rect 464,284 23x16 | (475.5, 292.0) | (-170, -42) |
| B alternate | rect 528,213 23x16 | (539.5, 221.0) | (-106, -113) |
| C | circle r 10 | (475, 444) | (-170, +110) |
| C alternate | circle r 10 | (451, 507) | (-194, +173) |
| D | circle r 10 | (819, 479) | (+174, +145) |
| D alternate | circle r 7 | (823, 512) | (+178, +178) |
| E | rect 623,593 57x23 | (651.5, 604.5) | (+6, +270) |
| E alternate | rect 570,601 37x15 | (588.5, 608.5) | (-56, +274) |
| Step 2 model polygon (left boss) `knuckle.step2.polygon.points` | polygon `-156,-147;-166,-116;-200,-100;-180,-97;-165,-106;-153,-133` (relative to the hub centre) | (475.0, 217.5) | (-170, -116) |
| Step 3 model polygon (right boss) `knuckle.step3.polygon.points` | polygon `+156,-144;+153,-130;+161,-103;+181,-88;+199,-94;+166,-113` (relative to the hub centre) | (814.3, 222.0) | (+169, -112) |

## Scale

Radii and sizes above are in pixels at this camera position. After a camera move, positions move with the reference
point AND scale with the zoom (the 13 Nov move: x1.035 on the hub flange, x1.049 on the plate, x1.09 on the nut top).
Sizes that a check compares against limits (hole areas, ring and nut diameters, face radii) change with the zoom too.
