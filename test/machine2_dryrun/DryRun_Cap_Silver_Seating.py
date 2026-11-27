"""
Dry run: silver cap seating by two rules (CHANGES 129), on the Machine 2 cap pictures. Since CHANGES 212 production
uses the annulus method (camera/CapAnnulus.py); this run sets vision.cap.seating.silver.method to "colour" (kept).

  Rule 1  no part of the hub centre may be left uncovered (hub circle from the hub + bottom bearing picture)
  Rule 2  the cap's top face edge must lie on one circle (share of the 360 angles >= cap.seating.silver.face.fit.min)

Checks
  S1 every seated silver cap (cap_images/DOST_cap_NN) SEATED
  S2 every skewed silver cap that reaches the seating step (cap_images/DOST_skewed_NN with a cap outline) NOT SEATED
  S3 rule 2 alone separates them (face fit of every seated cap > every skewed cap)
  S4 rule 1: a seated cap leaves 0 px of the hub centre uncovered; the cap pushed off the bore (DOST_skewed_07) more
     than cap.seating.hub.visible.max
  S5 without a hub picture rule 1 is reported "not checked" and the verdict is rule 2's
  S6 a picture without any hub circle (blank) as the hub picture: rule 1 "not checked", no exception

The hub picture is hub_images/DOST_01.jpg - not the same part as the caps (no paired pictures yet), so the uncovered
counts of rule 1 are only indicative. MobileSAM weights: models/MobileSAM/weights/mobile_sam.pt or --sam <path>.
Run from the project root:  python test/machine2_dryrun/DryRun_Cap_Silver_Seating.py [--sam PATH]
"""
import argparse
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
os.environ.setdefault("COSTHETA_PLC_VENDOR", "allenbradley" if HERE.name.startswith("machine1") else "siemens")
sys.path.insert(0, str(ROOT))

import cv2
import numpy as np


def capMaskYolo():
    """YOLO for the cap mask of the hub-centre distance (CHANGES 143; applied on every seating since the hub centre comes
    from the cap picture itself, CHANGES 152): ModelManager's object-aware model, else stand-in boxes round the disc
    (DryRun_Cap_Hub_Distance.StandInYolo; no YOLO weights in the sandbox) -> (model, note)."""
    import importlib.util
    try:
        import camera.ModelManager
        yolo = camera.ModelManager.ModelManager.get_instance().get_yolo_model()
        if yolo is None:
            raise RuntimeError("no YOLO model")
        return yolo, "YOLO from ModelManager"
    except Exception as e:
        spec = importlib.util.spec_from_file_location("hcd", str(HERE / "DryRun_Cap_Hub_Distance.py"))
        hcd = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hcd)
        return hcd.CircleBoxYolo(), f"stand-in YOLO boxes round the cap's circle ({e})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sam", default=str(ROOT / "models" / "MobileSAM" / "weights" / "mobile_sam.pt"))
    args = ap.parse_args()
    if not Path(args.sam).is_file():
        print(f"MobileSAM weights not found at {args.sam}: give --sam")
        return 2
    import camera.ModelManager  # noqa: F401 - its import sets the timm / registry warning filters (as in production)
    from mobile_sam import sam_model_registry
    from camera.CheckCap import CheckCap
    CheckCap.SEATING_SILVER_METHOD = "colour"    # the rules this run checks (CHANGES 129); production uses "annulus" since 212
    CheckCap.samModelProvider = staticmethod(lambda: sam_model_registry["vit_t"](checkpoint=args.sam).eval())
    yolo, yoloNote = capMaskYolo()
    CheckCap.yoloModelProvider = lambda: yolo
    print(yoloNote)
    if not CheckCap.SEATING_CHECK:
        print("Seating is off for this machine (vision.cap.seating.check=False): nothing to check here.")
        return 0

    data = HERE.parent / "machine2_dryrun"          # the silver pictures (seated and skewed) are Machine 2's
    rd = lambda p: (lambda im: cv2.resize(im, (1280, 720)) if im.shape[:2] != (720, 1280) else im)(cv2.imread(str(p)))
    hub = rd(data / "hub_images" / "DOST_01.jpg")
    results = []

    def check(name, ok, detail=""):
        results.append(bool(ok))
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   [{detail}]"), flush=True)

    print(f"Machine: {os.environ['COSTHETA_PLC_VENDOR']}; face fit >= {CheckCap.SEATING_SILVER_FACE_FIT_MIN}, "
          f"hub centre uncovered <= {CheckCap.SEATING_HUB_VISIBLE_MAX} px, outline to centre <= "
          f"{CheckCap.SEATING_SILVER_OUTLINE_OFFSET_MAX} px")
    seated, skewed = [], []
    for kind, pattern, bucket in (("seated", "DOST_cap_*.jpg", seated), ("skewed", "DOST_skewed_*.jpg", skewed)):
        for p in sorted(data.glob(f"cap_images/{pattern}")):
            t = time.time()
            s = CheckCap.checkSeating(rd(p), "DOST", hub)
            print(f"  {kind} {p.name:20s} {'SEATED    ' if s['seated'] else 'NOT SEATED'} {time.time() - t:4.1f} s  "
                  f"{s['reason']}", flush=True)
            bucket.append((p.name, s))
    check(f"S1 {len(seated)} seated silver caps SEATED", seated and all(s["seated"] for _, s in seated),
          [n for n, s in seated if not s["seated"]])
    reached = [(n, s) for n, s in skewed if s.get("cap") is not None]
    check(f"S2 {len(reached)} of {len(skewed)} skewed silver caps reach the seating step and are NOT SEATED",
          reached and not any(s["seated"] for _, s in reached), [n for n, s in reached if s["seated"]])
    fs = [s["fit"] for _, s in seated if s.get("fit") is not None]
    fk = [s["fit"] for _, s in reached if s.get("fit") is not None]
    check(f"S3 rule 2 alone separates them: seated face fit {min(fs):.2f}-{max(fs):.2f}, skewed {min(fk):.2f}-{max(fk):.2f}",
          fs and fk and min(fs) > max(fk))

    # S4: rule 1 on the cap pushed off the bore (DOST_skewed_07; rule 2 rejects it too - here rule 1 must flag it as well)
    name, s0 = seated[0]
    img = rd(data / "cap_images" / name)
    off = dict(skewed).get("DOST_skewed_07.jpg")
    check(f"S4 rule 1: {name} uncovered {s0.get('hub_visible')} px (SEATED); DOST_skewed_07 (cap pushed off the bore) "
          f"uncovered {off.get('hub_visible') if off else None} px > {CheckCap.SEATING_HUB_VISIBLE_MAX}",
          s0.get("hub_visible") == 0 and off is not None
          and (off.get("hub_visible") or 0) > CheckCap.SEATING_HUB_VISIBLE_MAX, off and off["reason"])

    s2 = CheckCap.checkSeating(img, "DOST", None)
    check(f"S5 no hub picture: rule 1 not checked, verdict by rule 2 ({'SEATED' if s2['seated'] else 'NOT SEATED'})",
          "hub centre not checked (no hub picture)" in s2["reason"] and s2["seated"] == s0["seated"], s2["reason"])
    s3 = CheckCap.checkSeating(img, "DOST", np.full_like(img, 128))
    check("S6 blank hub picture: rule 1 not checked, no exception",
          "not checked" in s3["reason"] and s3.get("hub") is None, s3["reason"])

    print(f"\nRESULT {sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
