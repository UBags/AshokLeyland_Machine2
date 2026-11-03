"""
DryRun_CheckKnuckle_Images - the real CheckKnuckle on real pictures of this machine, with this machine's settings.

Pictures: knuckle_images/<MODEL>_<SIDE>_<n>.jpg next to this script (MODEL = DOST or DOSTPLUS, SIDE = LHS or RHS),
and knuckle_images/EMPTY_<n>.jpg (the fixture without a knuckle: must be NOT OK with every QR code).
For every picture, CheckKnuckle is run with the QR code of each of the four knuckles (DOST/DOSTPLUS x LHS/RHS): it
must pass with its own QR code and fail with the other three. Also checked:
  - a dark object lying on the other end of the bottom bar (both side boxes dark) is rejected;
  - the margin of every measured region to its threshold (printed; a margin below MIN_MARGIN fails).
Settings: <vendor>.vision.knuckle.* in ApplicationConfiguration.properties (vendor from the folder: machine1 =
allenbradley, machine2 = siemens). No Redis, PLC or camera needed.

Run from the project root:   python test/machine1_dryrun/DryRun_CheckKnuckle_Images.py
After re-tuning siemens.vision.knuckle.* (or allenbradley.vision.knuckle.*), run it again.
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


def measure(img):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return dict(
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
print(f"{len(files)} pictures, settings {C.getVisionKeyPrefix()}vision.knuckle.*: common < {K.COMMON_CHECK_THRESHOLD}, "
      f"side < {K.LHS_RHS_DARKNESS_THRESHOLD}, model < {K.COMPONENT_IDENTIFICATION_DARKNESS_THRESHOLD} = DOST\n")
check("pictures found", len(files) >= 4, str(HERE / "knuckle_images"))
print(f"{'picture':24} " + " ".join(f"{k:>6}" for k in ("A", "B", "C", "D", "E", "LHS", "RHS", "S2", "S3")))
margins = {}
for f in files:
    model, side = os.path.basename(f).split("_")[:2]
    img = cv2.imread(f); m = measure(img)
    print(f"{os.path.basename(f):24} " + " ".join(f"{m[k]:6.1f}" for k in ("A", "B", "C", "D", "E", "LHS", "RHS", "S2", "S3")))
    for k in "ABCDE":
        margins.setdefault(k, []).append(K.COMMON_CHECK_THRESHOLD - m[k])
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

for f in empties:
    img = cv2.imread(f)
    accepted = [f"{q[0]} {q[1]}" for q, code in QR.items() if K.checkKnuckle(img, componentQRCode=code)[1]]
    check(f"{os.path.basename(f)}: empty fixture NOT OK with every QR code", not accepted, accepted)

print("\nSmallest margin of each region to its threshold:")
for k, v in margins.items():
    print(f"  {k:26} {min(v):6.1f}")
check(f"every region at least {MIN_MARGIN} grey levels from its threshold", all(min(v) >= MIN_MARGIN for v in margins.values()),
      {k: round(min(v), 1) for k, v in margins.items() if min(v) < MIN_MARGIN})
print(f"\nRESULT {sum(results)}/{len(results)} checks passed", flush=True)
os._exit(0 if all(results) else 1)
