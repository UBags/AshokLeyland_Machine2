"""Study (CHANGES 151, not a test): the hub centre from the 4 hub holes visible round the cap in the cap picture itself
(dark blobs at r 128 - 168 round cap.center, circle through their centroids), against the cap centre of the production
cap mask (CapMaskFinder; YOLO from ModelManager when its weights are there, else stand-in boxes). Prints per DOST cap
picture of cap_images/ the hole circle, the cap centre and cap - hub. A stand-in for the real hub check on the cap
picture (DryRun_Cap_Hub_Distance_UI.py, "Hub from the cap picture"), which needs YOLO.

    python test/machine2_dryrun/Study_Hub_Holes_In_Cap_Picture.py [--sam <mobile_sam.pt>]
"""
import os, sys, pathlib, importlib.util, numpy as np, cv2
HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]; sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
os.environ["COSTHETA_PLC_VENDOR"] = "siemens"     # the Machine 2 pictures and settings
import torch
load = torch.load; torch.load = lambda f, *a, **k: load(f, *a, **{**k, "weights_only": False})
from mobile_sam import sam_model_registry
import camera.ModelManager
from camera import CheckCap as CCm
from camera.CheckCap import CheckCap
import camera.CapMaskFinder as CM
spec = importlib.util.spec_from_file_location("hcd", str(HERE / "DryRun_Cap_Hub_Distance.py"))
hcd = importlib.util.module_from_spec(spec); spec.loader.exec_module(hcd)
model = sam_model_registry["vit_t"](checkpoint=(sys.argv[sys.argv.index("--sam") + 1] if "--sam" in sys.argv else str(ROOT / "models/MobileSAM/weights/mobile_sam.pt"))).eval()
CheckCap.samModelProvider = lambda: model
try:
    yolo = camera.ModelManager.ModelManager.get_instance().get_yolo_model()
    if yolo is None:
        raise RuntimeError("no YOLO model")
    print("YOLO from ModelManager")
except Exception as e:
    yolo = hcd.StandInYolo(); print(f"stand-in YOLO boxes ({e})")
CheckCap.yoloModelProvider = lambda: yolo

def circleFit(pts):
    pts = np.asarray(pts, float); A = np.c_[2 * pts, np.ones(len(pts))]; b = (pts ** 2).sum(1)
    (cx, cy, c), *_ = np.linalg.lstsq(A, b, rcond=None); return cx, cy, float(np.sqrt(c + cx * cx + cy * cy))

def blobs(img, centre, rmin, rmax, dark=True):
    g = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    h, w = g.shape; yy, xx = np.mgrid[:h, :w]; r = np.hypot(xx - centre[0], yy - centre[1])
    ring = (r > rmin) & (r < rmax)
    vals = g[ring]
    thr = np.percentile(vals, 6) if dark else np.percentile(vals, 97)
    m = ((g <= thr) if dark else (g >= thr)) & ring
    m = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, lab, st, cen = cv2.connectedComponentsWithStats(m)
    cand = [(st[i, 4], tuple(cen[i])) for i in range(1, n) if 150 < st[i, 4] < 2500]
    cand.sort(reverse=True)
    return [c for _, c in cand[:6]]

centre = (float(CheckCap.CENTER[0]), float(CheckCap.CENTER[1]))
rows = []
for f in sorted((ROOT / "test/machine2_dryrun/cap_images").glob("DOST_*.jpg")):
    img = cv2.imread(str(f))
    hb = blobs(img, centre, 128, 168, dark=True)
    hole = circleFit(hb) if len(hb) >= 3 else None
    cap = CM.findCap(img, centre, CheckCap._sam_predictor(), yolo)
    cc = tuple(cap["centre"]) if cap and cap.get("found") else None
    if hole and cc:
        rows.append((f.name, len(hb), hole, cc))
        print(f"{f.name:22s} holes {len(hb)} centre ({hole[0]:.1f},{hole[1]:.1f}) r{hole[2]:.1f} | cap ({cc[0]:.1f},{cc[1]:.1f}) | cap-hub dx {cc[0]-hole[0]:+.2f} dy {cc[1]-hole[1]:+.2f}  d {np.hypot(cc[0]-hole[0], cc[1]-hole[1]):.2f}", flush=True)
    else:
        print(f"{f.name}: holes {len(hb)} {hole} cap {cc}", flush=True)
