"""
DryRun_CheckKnuckle_Images - the real CheckKnuckle on real pictures of this machine, with this machine's settings.

Pictures: knuckle_images/<MODEL>_<SIDE>_<n>.jpg next to this script (MODEL = DOST or DOSTPLUS, SIDE = LHS or RHS),
and knuckle_images/EMPTY_<n>.jpg (the fixture without a knuckle: must be NOT OK with every QR code).
For every picture, CheckKnuckle is run with the QR code of each of the four knuckles (DOST/DOSTPLUS x LHS/RHS): it
must pass with its own QR code and fail with the other three. Also checked:
  - a dark object lying on the other end of the bottom bar (both side boxes dark) is rejected;
  - the margin of every measured region to its threshold (printed; a margin below MIN_MARGIN fails);
  - yellow paint marks (operators mark knuckles at random spots): for every picture and every common check A-E, a
    yellow mark over the region -> still OK when the region has an alternate area (vision.knuckle.common.check.
    <a..e>.alt.*), NOT OK when it has none; a mark over the alternate only -> OK; marks over both -> NOT OK.
  - with vision.knuckle.side.by.difference=True (contours A / A' and side boxes D1 / D2 compared, CHANGES section 95):
    the margin of both differences to vision.knuckle.side.difference.threshold; paint over the darker contour ->
    NOT OK, over the brighter one -> OK; the rectangle A is not tested (not used);
  - the whole picture moved by +/-3 px in x and y (8 shifts): still OK with its own QR code (part or camera slightly off);
    a check on Machine 2, reported only on Machine 1 (not re-tuned).
Settings: vision.<vendor>.knuckle.* in ApplicationConfiguration.properties (vendor from the folder: machine1 =
allenbradley, machine2 = siemens). No Redis, PLC or camera needed.

Run from the project root:   python test/machine1_dryrun/DryRun_CheckKnuckle_Images.py
After re-tuning vision.siemens.knuckle.* (or vision.allenbradley.knuckle.*), run it again.
"""
import glob, os, pathlib, sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["COSTHETA_PLC_VENDOR"] = "siemens" if HERE.name.startswith("machine2") else "allenbradley"

import cv2
import numpy as np
from Configuration import CosThetaConfigurator as C
from camera.CheckKnuckle import CheckKnuckle as K

MIN_MARGIN = 20          # grey levels between a measured value and its threshold
MIN_DIFF_MARGIN = 10     # side.by.difference: grey levels beyond vision.knuckle.side.difference.threshold
MIN_ALT_MARGIN = 15      # the same for the alternate areas (Machine 2: the casting near A offers no area
                         # with 20; the A alternate has 15.1 as photographed, 11.2 with the part 3 px off,
                         # where A itself has 8.1)
QR = {("DOST", "LHS"): "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$",
      ("DOST", "RHS"): "7204838$400102VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNURH$SNPL-MAT$",
      ("DOSTPLUS", "LHS"): "8201206$400112VA1C$22881$15.10.2026$C5211702$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$",
      ("DOSTPLUS", "RHS"): "8201206$400102VA1C$22881$15.10.2026$C5211702$MEK$40cR4-B$1$SPDL ASSY-KNURH$SNPL-MAT$"}
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   [{detail}]"), flush=True)


def rect_mean(g, x, y, w, h):
    return float(g[y:y + h, x:x + w].mean())


def circ_mean(g, cx, cy, r):
    m = np.zeros(g.shape, np.uint8); cv2.circle(m, (cx, cy), r, 255, -1); return float(g[m == 255].mean())


YELLOW = (0, 225, 255)          # BGR: paint as seen on the knuckles (grey level about 210)
PAD = 3                         # the mark covers the region plus 3 px all round


def regions():
    """{letter: (primary, alternate or None)}; a region is ("r", x, y, w, h) or ("c", cx, cy, r)."""
    r = lambda x, y, w, h: ("r", x, y, w, h) if w > 0 and h > 0 else None
    c = lambda x, y, rad: ("c", x, y, rad) if rad > 0 else None
    return {
        "A": (r(K.COMMON_CHECK_A_X, K.COMMON_CHECK_A_Y, K.COMMON_CHECK_A_WIDTH, K.COMMON_CHECK_A_HEIGHT),
              r(K.COMMON_CHECK_A_ALT_X, K.COMMON_CHECK_A_ALT_Y, K.COMMON_CHECK_A_ALT_WIDTH, K.COMMON_CHECK_A_ALT_HEIGHT)),
        "B": (r(K.COMMON_CHECK_B_X, K.COMMON_CHECK_B_Y, K.COMMON_CHECK_B_WIDTH, K.COMMON_CHECK_B_HEIGHT),
              r(K.COMMON_CHECK_B_ALT_X, K.COMMON_CHECK_B_ALT_Y, K.COMMON_CHECK_B_ALT_WIDTH, K.COMMON_CHECK_B_ALT_HEIGHT)),
        "C": (c(K.COMMON_CHECK_C_CENTER_X, K.COMMON_CHECK_C_CENTER_Y, K.COMMON_CHECK_C_RADIUS),
              c(K.COMMON_CHECK_C_ALT_CENTER_X, K.COMMON_CHECK_C_ALT_CENTER_Y, K.COMMON_CHECK_C_ALT_RADIUS)),
        "D": (c(K.COMMON_CHECK_D_CENTER_X, K.COMMON_CHECK_D_CENTER_Y, K.COMMON_CHECK_D_RADIUS),
              c(K.COMMON_CHECK_D_ALT_CENTER_X, K.COMMON_CHECK_D_ALT_CENTER_Y, K.COMMON_CHECK_D_ALT_RADIUS)),
        "E": (r(K.COMMON_CHECK_E_X, K.COMMON_CHECK_E_Y, K.COMMON_CHECK_E_WIDTH, K.COMMON_CHECK_E_HEIGHT),
              r(K.COMMON_CHECK_E_ALT_X, K.COMMON_CHECK_E_ALT_Y, K.COMMON_CHECK_E_ALT_WIDTH, K.COMMON_CHECK_E_ALT_HEIGHT)),
    }


def paint(img, region):
    out = img.copy()
    if region[0] == "r":
        _, x, y, w, h = region
        cv2.rectangle(out, (x - PAD, y - PAD), (x + w + PAD, y + h + PAD), YELLOW, -1)
    else:
        _, cx, cy, rad = region
        cv2.circle(out, (cx, cy), rad + PAD, YELLOW, -1)
    return out


def region_mean(g, region):
    if region[0] == "r":
        return rect_mean(g, *region[1:])
    return circ_mean(g, *region[1:])


def poly_mean(g, pts):
    m = np.zeros(g.shape, np.uint8); cv2.fillPoly(m, [np.array(pts, np.int32).reshape(-1, 2)], 255); return float(g[m == 255].mean())


def paint_poly(img, pts):
    out = img.copy(); P = np.array(pts, np.int32).reshape(-1, 2)
    cv2.fillPoly(out, [P], YELLOW); cv2.polylines(out, [P], True, YELLOW, 2 * PAD)
    return out


def shifted(img, dx, dy):
    return cv2.warpAffine(img, np.float32([[1, 0, dx], [0, 1, dy]]), (img.shape[1], img.shape[0]), borderMode=cv2.BORDER_REPLICATE)


DIFF = bool(K.SIDE_BY_DIFFERENCE)


def measure(img):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    extra = dict(CA=poly_mean(g, K.COMMON_CHECK_A_POLYGON_POINTS), CAalt=poly_mean(g, K.COMMON_CHECK_A_ALT_POLYGON_POINTS),
                 D1=rect_mean(g, K.LHS_CROP_X, K.LHS_CROP_Y, K.LHS_CROP_WIDTH, K.LHS_CROP_HEIGHT),
                 D2=rect_mean(g, K.RHS_CROP_X, K.RHS_CROP_Y, K.RHS_CROP_WIDTH, K.RHS_CROP_HEIGHT)) if DIFF else {}
    return dict(**extra,
        A=rect_mean(g, K.COMMON_CHECK_A_X, K.COMMON_CHECK_A_Y, K.COMMON_CHECK_A_WIDTH, K.COMMON_CHECK_A_HEIGHT),
        B=rect_mean(g, K.COMMON_CHECK_B_X, K.COMMON_CHECK_B_Y, K.COMMON_CHECK_B_WIDTH, K.COMMON_CHECK_B_HEIGHT),
        C=circ_mean(g, K.COMMON_CHECK_C_CENTER_X, K.COMMON_CHECK_C_CENTER_Y, K.COMMON_CHECK_C_RADIUS),
        D=circ_mean(g, K.COMMON_CHECK_D_CENTER_X, K.COMMON_CHECK_D_CENTER_Y, K.COMMON_CHECK_D_RADIUS),
        E=rect_mean(g, K.COMMON_CHECK_E_X, K.COMMON_CHECK_E_Y, K.COMMON_CHECK_E_WIDTH, K.COMMON_CHECK_E_HEIGHT),
        LHS=K._crop_and_analyze(img, K.LHS_CROP_X, K.LHS_CROP_Y, K.LHS_CROP_WIDTH, K.LHS_CROP_HEIGHT),
        RHS=K._crop_and_analyze(img, K.RHS_CROP_X, K.RHS_CROP_Y, K.RHS_CROP_WIDTH, K.RHS_CROP_HEIGHT),
        S2=K._analyze_polygon_region(img, K.STEP2_POLYGON_POINTS),
        S3=K._analyze_polygon_region(img, K.STEP3_POLYGON_POINTS))


files = sorted(f for f in glob.glob(str(HERE / "knuckle_images" / "*.jpg")) if not os.path.basename(f).startswith("EMPTY"))
empties = sorted(glob.glob(str(HERE / "knuckle_images" / "EMPTY_*.jpg")))
print(f"{len(files)} pictures, settings {C.visionPropertyKey("vision.knuckle")}.*: common < {K.COMMON_CHECK_THRESHOLD}, "
      + (f"side by differences > {K.SIDE_DIFFERENCE_THRESHOLD} (contours A / A', side boxes D1 / D2)" if DIFF else
         f"side < {K.LHS_RHS_DARKNESS_THRESHOLD}")
      + f", model < {K.COMPONENT_IDENTIFICATION_DARKNESS_THRESHOLD} = DOST\n")
check("pictures found", len(files) >= 4, str(HERE / "knuckle_images"))
COLS = ("CA", "CAalt", "B", "C", "D", "E", "D1", "D2", "S2", "S3") if DIFF else ("A", "B", "C", "D", "E", "LHS", "RHS", "S2", "S3")
print(f"{'picture':34} " + " ".join(f"{k:>6}" for k in COLS))
margins = {}
for f in files:
    model, side = os.path.basename(f).split("_")[:2]
    img = cv2.imread(f); m = measure(img)
    print(f"{os.path.basename(f):34} " + " ".join(f"{m[k]:6.1f}" for k in COLS))
    for k in ("BCDE" if DIFF else "ABCDE"):
        margins.setdefault(k, []).append(K.COMMON_CHECK_THRESHOLD - m[k])
    if DIFF:
        sgn = 1 if side == "LHS" else -1          # LHS: A' brighter than A, D2 brighter than D1
        margins.setdefault("contours A'-A (diff)", []).append(sgn * (m["CAalt"] - m["CA"]) - K.SIDE_DIFFERENCE_THRESHOLD)
        margins.setdefault("side boxes D2-D1 (diff)", []).append(sgn * (m["D2"] - m["D1"]) - K.SIDE_DIFFERENCE_THRESHOLD)
    else:
        armBox, otherBox = (m["LHS"], m["RHS"]) if side == "LHS" else (m["RHS"], m["LHS"])
        margins.setdefault("side box with the arm", []).append(K.LHS_RHS_DARKNESS_THRESHOLD - armBox)
        margins.setdefault("side box without the arm", []).append(otherBox - K.LHS_RHS_DARKNESS_THRESHOLD)
    for k in ("S2", "S3"):
        t = K.COMPONENT_IDENTIFICATION_DARKNESS_THRESHOLD
        margins.setdefault(f"{k} {model}", []).append(t - m[k] if model == "DOST" else m[k] - t)
    verdicts = {q: K.checkKnuckle(img, componentQRCode=code)[1] for q, code in QR.items()}
    wrong = [f"{q[0]} {q[1]}={'OK' if v else 'NOT OK'}" for q, v in verdicts.items() if v != (q == (model, side))]
    check(f"{os.path.basename(f)}: OK with its own QR code, NOT OK with the other three", not wrong, wrong)
    # something dark lying on the other end of the bar: both side boxes dark -> must be rejected
    other = (K.RHS_CROP_X, K.RHS_CROP_Y, K.RHS_CROP_WIDTH, K.RHS_CROP_HEIGHT) if side == "LHS" else \
            (K.LHS_CROP_X, K.LHS_CROP_Y, K.LHS_CROP_WIDTH, K.LHS_CROP_HEIGHT)
    rag = img.copy(); x, y, w, h = other
    cv2.rectangle(rag, (x - 10, y - 10), (x + w + 10, y + h + 10), (30, 30, 30), -1)
    check(f"{os.path.basename(f)}: dark object on the other end of the bar -> NOT OK",
          not K.checkKnuckle(rag, componentQRCode=QR[(model, side)])[1])
    # yellow paint marks
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    own = QR[(model, side)]
    for letter, (prim, alt) in regions().items():
        if DIFF and letter == "A":
            continue                                # rectangle A not used: contours tested below
        if alt is not None:
            margins.setdefault(f"{letter} alternate", []).append(K.COMMON_CHECK_THRESHOLD - region_mean(g, alt))
        okPrim = K.checkKnuckle(paint(img, prim), componentQRCode=own)[1]
        if alt is None:
            check(f"{os.path.basename(f)}: paint over {letter} (no alternate set) -> NOT OK", not okPrim)
            continue
        okAlt = K.checkKnuckle(paint(img, alt), componentQRCode=own)[1]
        okBoth = K.checkKnuckle(paint(paint(img, prim), alt), componentQRCode=own)[1]
        check(f"{os.path.basename(f)}: paint over {letter} -> OK (alternate dark); over its alternate -> OK; over both -> NOT OK",
              okPrim and okAlt and not okBoth, f"over {letter}: {okPrim}, over alternate: {okAlt}, over both: {okBoth}")
    if DIFF:
        dark, bright = (K.COMMON_CHECK_A_POLYGON_POINTS, K.COMMON_CHECK_A_ALT_POLYGON_POINTS) if side == "LHS" else \
                       (K.COMMON_CHECK_A_ALT_POLYGON_POINTS, K.COMMON_CHECK_A_POLYGON_POINTS)
        check(f"{os.path.basename(f)}: paint over the darker contour -> NOT OK; over the brighter one -> OK",
              not K.checkKnuckle(paint_poly(img, dark), componentQRCode=own)[1] and K.checkKnuckle(paint_poly(img, bright), componentQRCode=own)[1])
    bad = [(dx, dy) for dx in (-3, 0, 3) for dy in (-3, 0, 3) if (dx, dy) != (0, 0)
           and not K.checkKnuckle(shifted(img, dx, dy), componentQRCode=own)[1]]
    if os.environ["COSTHETA_PLC_VENDOR"] == "siemens":
        check(f"{os.path.basename(f)}: picture moved +/-3 px (8 shifts) -> still OK with its own QR code", not bad, f"NOT OK at {bad}")
    elif bad:      # Machine 1: not re-tuned (decision of 2026-11-03); reported only (C at +3,+3 on DOSTPLUS_RHS_4)
        print(f"NOTE  {os.path.basename(f)}: picture moved +/-3 px -> NOT OK at {bad} (Machine 1, reported only)")

for f in empties:
    img = cv2.imread(f)
    accepted = [f"{q[0]} {q[1]}" for q, code in QR.items() if K.checkKnuckle(img, componentQRCode=code)[1]]
    check(f"{os.path.basename(f)}: empty fixture NOT OK with every QR code", not accepted, accepted)

print("\nSmallest margin of each region to its threshold:")
for k, v in margins.items():
    print(f"  {k:26} {min(v):6.1f}")
need = lambda k: MIN_ALT_MARGIN if k.endswith("alternate") else MIN_DIFF_MARGIN if k.endswith("(diff)") else MIN_MARGIN
check(f"every region at least {MIN_MARGIN} grey levels from its threshold (alternate areas {MIN_ALT_MARGIN}"
      + (f", differences {MIN_DIFF_MARGIN} beyond {K.SIDE_DIFFERENCE_THRESHOLD})" if DIFF else ")"),
      all(min(v) >= need(k) for k, v in margins.items()),
      {k: round(min(v), 1) for k, v in margins.items() if min(v) < need(k)})
print(f"\nRESULT {sum(results)}/{len(results)} checks passed", flush=True)
os._exit(0 if all(results) else 1)
