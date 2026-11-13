"""
Diag_Cap_Seating - is the cap check's seating step really using MobileSAM, on which device, and how long does it take?

Uses the production path: CheckCap._sam_predictor() -> ModelManager -> models/MobileSAM (MobileSAM and YOLO weights).
Prints: whether seating is on and the cap centre (Machine 2 settings), CUDA and the GPU, the device the SAM model is on;
then for 4 cap pictures, twice (the second round is warm): the image embedding time, the whole seating step's time, the
radii of the masks MobileSAM found (cap, bore round a brass cap, silver top face) and the verdict.
Expected verdicts: DOST_cap_01 face ~4 px / outline ~8 px (seated); DOST_skewed_03 face ~16 px > 9 (not seated);
DOSTPLUS_cap_01 cap to bore ~1 px (seated); DOSTPLUS_skewed_02 ~16 px > 9 (not seated).

Run from the project root:   python test/machine2_dryrun/Diag_Cap_Seating.py
"""
import os
import pathlib
import sys
import time

os.environ["COSTHETA_PLC_VENDOR"] = "siemens"
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import cv2
import torch

import camera.CheckCap as cc

cc.logBoth = lambda *a, **k: None
C = cc.CheckCap


def sync():
    if torch.cuda.is_available():
        torch.cuda.synchronize()


def main() -> int:
    print("SEATING_CHECK", C.SEATING_CHECK, "| CENTER", C.CENTER, "| CUDA", torch.cuda.is_available(),
          torch.cuda.get_device_name(0) if torch.cuda.is_available() else "")
    t = time.perf_counter()
    P = C._sam_predictor()                      # ModelManager -> MobileSAM (loads it)
    print(f"SAM model on {next(P.model.parameters()).device} (loaded in {time.perf_counter() - t:.1f} s)")
    for n in ["DOST_cap_01", "DOST_skewed_03", "DOSTPLUS_cap_01", "DOSTPLUS_skewed_02"] * 2:   # 2nd round = warm
        im = cv2.imread(f"test/machine2_dryrun/cap_images/{n}.jpg")
        im = cv2.resize(im, (1280, 720))
        sync()
        t = time.perf_counter()
        P.set_image(cv2.cvtColor(im, cv2.COLOR_BGR2RGB))
        sync()
        emb = time.perf_counter() - t
        t = time.perf_counter()
        s = C.checkSeating(im, "DOSTPLUS" if n.startswith("DOSTPLUS") else "DOST")
        sync()
        seat = time.perf_counter() - t
        masks = {k: (round(s[k]["radius"], 1) if s[k] else None) for k in ("cap", "bore", "face")}
        print(f"{n:20s} embedding {emb * 1000:6.0f} ms  seating {seat * 1000:6.0f} ms  masks r {masks}  -> {s['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
