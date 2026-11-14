"""
DryRun_Knuckle_Frame - the Machine 2 knuckle check by segmentation (CHANGES 102) and the frame it gives the later
checks, on the knuckle pictures (knuckle_images/<MODEL>_<SIDE>_<n>.jpg, EMPTY_<n>.jpg). Automatic PASS / FAIL checks:

  A. The real CheckKnuckle (vision.knuckle.method = segmentation, Machine 2 settings) with each of the four QR codes:
     OK with its own, NOT OK with the other three; the empty fixture NOT OK with all four. Time per picture.
  B. The frame on the unmoved pictures: camera shift within 1.5 px and rotation within 0.3 deg (the pictures were
     all taken at the reference camera position), every later check's centre within 4 px of its value in the
     properties file, and the spindle mask (where usable) within 2 px of where the pins put the spindle.
  C. The camera moved (the picture warped: shift, rotation, zoom - see MOVES): still OK with its own QR code, and every
     later check's centre lands where the same move takes the centre found on the unmoved picture (within 2 px).
     A move beyond vision.knuckle.frame.max.* is NOT OK ("camera moved").
  D. The centres reach the checks: VisionFrame.applyToChecks writes them into the check modules' constants (hub
     segmenter instance included); without a frame the properties values come back. The camera server's ImageProcessor
     keeps the frame as cycle state and drops it at the end of the cycle (endOfCycle) and at a knuckle retake.

  python test\\machine2_dryrun\\DryRun_Knuckle_Frame.py [--sam <mobile_sam.pt>] [--folder <pictures>] [--quick]

--quick: part C on 4 pictures instead of all. Needs the MobileSAM weights (default models/MobileSAM/weights); no Redis,
PLC or camera. Exit code 1 if a check fails.
"""

import argparse
import glob
import math
import os
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
os.environ["COSTHETA_PLC_VENDOR"] = "siemens"
_MOBILESAM_DIR = ROOT / "models" / "MobileSAM"
if _MOBILESAM_DIR.exists():
    sys.path.append(str(_MOBILESAM_DIR))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402

_original_torch_load = torch.load


def _trusted_load(f, map_location=None, pickle_module=None, *, weights_only=None, mmap=None, **kwargs):
    return _original_torch_load(f, map_location=map_location, pickle_module=pickle_module, weights_only=False,
                                mmap=mmap, **kwargs)


torch.load = _trusted_load

from camera.CheckKnuckle import CheckKnuckle as K  # noqa: E402
from camera import VisionFrame  # noqa: E402
from camera.KnuckleLocator import KnuckleLocator  # noqa: E402

QR = {("DOST", "LHS"): "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$",
      ("DOST", "RHS"): "7204838$400102VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNURH$SNPL-MAT$",
      ("DOSTPLUS", "LHS"): "8201206$400112VA1C$22881$15.10.2026$C5211702$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$",
      ("DOSTPLUS", "RHS"): "8201206$400102VA1C$22881$15.10.2026$C5211702$MEK$40cR4-B$1$SPDL ASSY-KNURH$SNPL-MAT$"}

# camera moves: (dx px, dy px, rotation deg about the picture centre, zoom)
MOVES = [(25, -15, 0.0, 1.0), (-20, 20, 1.5, 1.0), (0, 0, -1.0, 1.02), (30, 10, 0.8, 0.98), (-12, -25, -2.0, 1.01)]
TOO_FAR = (55, 0, 0.0, 1.0)

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  [{detail}]"), flush=True)


def move_matrix(dx, dy, deg, zoom, size=(1280, 720)):
    m = cv2.getRotationMatrix2D((size[0] / 2.0, size[1] / 2.0), -deg, zoom)   # +deg = clockwise on screen (y down)
    m[0, 2] += dx
    m[1, 2] += dy
    return m


def moved(img, m):
    return cv2.warpAffine(img, m, (img.shape[1], img.shape[0]), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def apply(m, p):
    return (m[0, 0] * p[0] + m[0, 1] * p[1] + m[0, 2], m[1, 0] * p[0] + m[1, 1] * p[1] + m[1, 2])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sam", default=str(ROOT / "models" / "MobileSAM" / "weights" / "mobile_sam.pt"))
    ap.add_argument("--folder", default=str(HERE / "knuckle_images"))
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--parts", default="ABCD", help="which parts to run (A and B run together; C and D need a frame)")
    args = ap.parse_args()
    from mobile_sam import sam_model_registry, SamPredictor
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = sam_model_registry["vit_t"](checkpoint=args.sam).to(device).eval()
    K.locator = KnuckleLocator(SamPredictor(model), device)
    K.METHOD = "segmentation"
    print(f"MobileSAM on {device}; method {K.METHOD}; limits shift {K.FRAME_MAX_SHIFT} px, rotation "
          f"{K.FRAME_MAX_ROTATION} deg, zoom +-{K.FRAME_MAX_ZOOM_CHANGE}")

    files = sorted(glob.glob(os.path.join(args.folder, "*.jpg")) + glob.glob(os.path.join(args.folder, "*.png")))
    pictures = {}
    for f in files:
        img = cv2.imread(f)
        if img.shape[:2] != (720, 1280):
            img = cv2.resize(img, (1280, 720))
        pictures[os.path.basename(f)] = img
    # the check modules (their centres are what the frame changes)
    import camera.HubAndBearingSegmenter as HB
    import camera.CheckHubAndBottomBearing as HC
    import camera.CheckTopBearing as TB
    import camera.CheckNutAndPlateWasher as NW
    import camera.CheckNoBunk as NB
    import camera.BunkSegmenter as BS
    import camera.CheckCap as CC
    from camera.VisionConfig import getRegistry
    registry = getRegistry()

    def props(name):
        attrs = VisionFrame.CENTRES[name][2]
        if len(attrs) == 2:
            return registry[f"vision.{name}.x"]["value"], registry[f"vision.{name}.y"]["value"]
        return tuple(registry[f"vision.{name}"]["value"])

    # ---------------------------------------------------------------------------------------------- A and B
    print("\nA / B. pictures as taken")
    base = {}
    times = []
    for name, img in pictures.items():
        if "A" not in args.parts.upper() and "B" not in args.parts.upper() and base:
            break
        parts = name.split("_")
        own = (parts[0], parts[1]) if parts[0] in ("DOST", "DOSTPLUS") else None
        verdicts = {}
        for q, code in QR.items():
            t = time.monotonic()
            _, ok, frame = K.checkKnuckleBySegmentation(img, code)
            times.append(time.monotonic() - t)
            verdicts[q] = (ok, frame, K.lastResult)
        accepted = [q for q, v in verdicts.items() if v[0]]
        if own is None:
            check(f"{name}: empty fixture NOT OK with every QR code", not accepted, accepted)
            continue
        check(f"{name}: OK with its own QR code only", accepted == [own],
              f"accepted {accepted}; own: {verdicts[own][2].get('reasons')}")
        frame = verdicts[own][1]
        if frame is None:
            continue
        base[name] = frame
        centres = VisionFrame.placedCentres(frame)
        far = {n: (xy, props(n)) for n, xy in centres.items() if math.hypot(xy[0] - props(n)[0], xy[1] - props(n)[1]) > 4}
        check(f"{name}: camera shift {frame['cameraShift']}, rotation {frame['rotation']:+.2f}, scale "
              f"{frame['scale']:.4f}, anchor {frame['anchor']} ({frame['anchorSource']}); centres within 4 px of the "
              f"properties", math.hypot(*frame["cameraShift"]) <= 1.5 and abs(frame["rotation"]) <= 0.3 and not far,
              far)
        mo = frame.get("spindleMaskOffset")
        if mo is not None:
            check(f"{name}: spindle mask within 2 px of where the pins put the spindle ({mo})", math.hypot(*mo) <= 2.0)
    print(f"time per check: median {np.median(times):.2f} s, max {max(times):.2f} s ({device})")

    # ------------------------------------------------------------------------------------------------------ C
    print("\nC. camera moved")
    names = [n for n in base]
    if args.quick:
        names = [n for n in names if n.endswith("_01.jpg")]
    worst = 0.0
    for name in (names if "C" in args.parts.upper() else []):
        own = tuple(name.split("_")[:2])
        ref = VisionFrame.placedCentres(base[name])
        refExact = {n: VisionFrame.place(base[name], VisionFrame.CENTRES[n][3]) for n in VisionFrame.CENTRES}
        for mv in MOVES:
            m = move_matrix(*mv)
            _, ok, frame = K.checkKnuckleBySegmentation(moved(pictures[name], m), QR[own])
            if not ok:
                check(f"{name} moved {mv}: OK", False, K.lastResult.get("reasons"))
                continue
            errs = {n: math.hypot(VisionFrame.place(frame, VisionFrame.CENTRES[n][3])[0] - apply(m, refExact[n])[0],
                                  VisionFrame.place(frame, VisionFrame.CENTRES[n][3])[1] - apply(m, refExact[n])[1])
                    for n in ref}
            e = max(errs.values())
            worst = max(worst, e)
            check(f"{name} moved {mv}: OK; camera shift {frame['cameraShift']}, rotation {frame['rotation']:+.2f}, "
                  f"scale {frame['scale']:.4f}; centres off by at most {e:.2f} px", e <= 2.0,
                  {n: round(v, 2) for n, v in errs.items()})
    print(f"worst centre error over the moves: {worst:.2f} px (the pins move a little with the re-sampling)")
    name = names[0]
    _, ok, _ = K.checkKnuckleBySegmentation(moved(pictures[name], move_matrix(*TOO_FAR)), QR[tuple(name.split('_')[:2])])
    check(f"{name} moved {TOO_FAR} (beyond {K.FRAME_MAX_SHIFT} px): NOT OK, camera moved",
          not ok and any("camera moved" in r for r in K.lastResult.get("reasons", [])), K.lastResult.get("reasons"))

    # ------------------------------------------------------------------------------------------------------ D
    print("\nD. centres into the checks")

    def read():
        return {"hub.segment.center": (HB.HUB_SEGMENT_CENTER_X, HB.HUB_SEGMENT_CENTER_Y),
                "hub.centre.check": (HC.HUB_CENTRE_CHECK_X, HC.HUB_CENTRE_CHECK_Y),
                "topbearing.annular.center": (TB.CheckTopBearing.ANNULAR_CENTER_X, TB.CheckTopBearing.ANNULAR_CENTER_Y),
                "nutwasher.nut.detection.center": tuple(NW.CheckNutAndPlateWasher.NUT_DETECTION_CENTER),
                "nobunk.nut.detection.center": tuple(NB.CheckNoBunk.NUT_DETECTION_CENTER),
                "bunk.outer.disc.center": tuple(BS.OUTER_DISC_CENTER),
                "bunk.seating.seated.centre.dost": tuple(BS.SEATING_SEATED_CENTRE_DOST),
                "bunk.seating.seated.centre.dostplus": tuple(BS.SEATING_SEATED_CENTRE_DOSTPLUS),
                "cap.center": tuple(CC.CheckCap.CENTER)}

    before = read()
    check("before any frame the checks hold the properties values", all(before[n] == props(n) for n in before),
          {n: (before[n], props(n)) for n in before})
    m = move_matrix(*MOVES[1])
    name = names[0]
    _, ok, frame = K.checkKnuckleBySegmentation(moved(pictures[name], m), QR[tuple(name.split('_')[:2])])
    VisionFrame.applyToChecks(frame)
    now = read()
    want = VisionFrame.placedCentres(frame)
    check("with the frame of a moved camera every check holds the placed centre", now == want,
          {n: (now[n], want[n]) for n in now if now[n] != want[n]})

    class FakeSegmenter:          # the hub segmenter's single instance keeps its own copy of the centre
        annular_center = (0, 0)
    saved = HB.HubAndBearingSegmenter._instance
    fake = FakeSegmenter()
    HB.HubAndBearingSegmenter._instance = fake
    VisionFrame.applyToChecks(frame)
    check("hub segmenter instance's annular centre follows", tuple(fake.annular_center) ==
          want["hub.segment.center"], fake.annular_center)
    VisionFrame.applyToChecks(None)
    check("without a frame the properties values come back", read() == before and
          tuple(fake.annular_center) == props("hub.segment.center"), (read(), fake.annular_center))
    HB.HubAndBearingSegmenter._instance = saved

    # camera server state: frame kept as cycle state, dropped at the end of a cycle and at a knuckle retake
    try:
        import camera.CameraProcessorServer as cps
        IP = cps.ImageProcessor
        IP.setVisionFrame(frame)
        snap = IP.snapshotState()
        check("the frame is part of the cycle state sent to the worker", snap.get("visionFrame") == frame)
        IP.setVisionFrame(None)
        IP.restoreState(snap)            # the worker process restoring the state applies the frame
        check("restoring the state (worker process) applies the frame", read() == want)
        IP.endOfCycle()
        check("end of cycle (completed or aborted) drops the frame and puts back the properties values",
              IP.visionFrame is None and read() == before)
        IP.setVisionFrame(frame)
        IP.currentComponentQRCodeValue = QR[tuple(name.split('_')[:2])]
        IP.readComponentQR = None
        cps.readComponentQRCodeInCameraServerFromQRCodeServer = lambda *a, **k: (None, False)
        IP.connectToRedis = classmethod(lambda cls, forceRenew=False: None)
        IP.prepareForKnuckleCheck()
        check("knuckle retake drops the frame (the retake builds it again)", IP.visionFrame is None and read() == before)
        IP.endOfCycle()
    except Exception as e:
        check("camera server state", False, f"{type(e).__name__}: {e}")

    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
