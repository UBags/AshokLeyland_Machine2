"""
DryRun_Cap_Production_Path - the cap check as the camera server runs it (CHANGES 105): assessPictures() for the cap state
with the camera server's pictures (three, each sent twice), the earlier pictures in ImageProcessor as in a cycle.
  - steps 1-3 (which cap, right cap, brightening; no MobileSAM) on each of the three pictures;
  - at least 2 of 3 passing: the whole check including the seating step (MobileSAM) ONCE, on the first picture that
    passed; its verdict is the result. Fewer than 2: NOT OK, no MobileSAM run.
Automatic checks (this machine's settings and pictures):
  P1 every seated cap: OK, exactly one seating run; the time is printed (one MobileSAM run, not six in parallel);
  P2 every skewed cap: NOT OK (by the seating step, or already at step 1 for a cap pushed off the bore), at most one
     seating run;
  P3 every cap as the other model (wrong cap): NOT OK, no seating run;
  P4 a nut / washer picture as the cap picture (cap left off): NOT OK, no seating run;
  P5 2 of 3: one cap picture among two without a cap -> NOT OK, no seating run; two cap pictures and one without ->
     OK, the seating run on the first picture that passed.
The cycle's hub check (CHANGES 169): the real checkHub() on the model's first hub picture sets ImageProcessor.hubCentre,
as state 5 does in a cycle; the cap's hole finder looks round it. --holes morphology = the dark-blob finder with its own
offsets (no YOLO weights: the sandbox).
MobileSAM weights needed (models/MobileSAM/weights/mobile_sam.pt, or --sam); no Redis, YOLO, camera or PLC.
Machine 1 (seating off, no Machine 1 hub / bearing / nut pictures in the repo): skipped, said so.

Run from the project root:   python test/machine1_dryrun/DryRun_Cap_Production_Path.py [--sam <mobile_sam.pt>]
"""
import argparse
import glob
import os
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["COSTHETA_PLC_VENDOR"] = "siemens" if HERE.name.startswith("machine2") else "allenbradley"

QR = {"DOST": "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$",
      "DOSTPLUS": "8201206$400112VA1C$22881$15.10.2026$C5211702$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$"}


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sam", default=str(ROOT / "models" / "MobileSAM" / "weights" / "mobile_sam.pt"))
    ap.add_argument("--holes", choices=("sam", "sam_slow", "morphology"), default="sam")
    args = ap.parse_args()
    sys.path.insert(0, str(HERE))
    from _hub_holes_option import applyHolesOption, hubCheckOnPicture
    applyHolesOption(args.holes)
    from camera.FindHubHoles import FindHubHoles
    FindHubHoles.hubCentreFromPicture = None    # as production: the hub centre only from this cycle's hub check
    import cv2
    import torch
    import camera.CameraProcessorServer as cps
    from camera.CheckCap import CheckCap
    from statemachine.StateMachine import MachineState as MS
    results = []

    def check(name, ok, detail=""):
        results.append(bool(ok))
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   [{detail}]"), flush=True)

    if not CheckCap.SEATING_CHECK:
        print("Seating is off for this machine (vision.cap.seating.check=False): nothing to check here.")
        return 0
    data = HERE if (HERE / "hub_images").is_dir() else HERE.parent / "machine2_dryrun"
    if not os.path.isfile(args.sam):
        print(f"MobileSAM weights not found at {args.sam}: give --sam")
        return 1
    load = torch.load
    torch.load = lambda f, *a, **k: load(f, *a, **{**k, "weights_only": False})
    from mobile_sam import sam_model_registry
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = sam_model_registry["vit_t"](checkpoint=args.sam).to(device).eval()
    CheckCap.samModelProvider = lambda: model
    yolo, yoloNote = capMaskYolo()
    CheckCap.yoloModelProvider = lambda: yolo
    print(f"MobileSAM on {device}; {yoloNote}")

    runs = []
    realSeating = CheckCap.checkSeating
    CheckCap.checkSeating = staticmethod(lambda img, found, *rest: (runs.append(1), realSeating(img, found, *rest))[1])
    IP = cps.ImageProcessor
    IP.stateFile = None

    hubCentres = {}

    def rd(path):
        return cv2.resize(cv2.imread(str(path)), (1280, 720))

    def setUp(model_):
        IP.endOfCycle()
        IP.setCurrentComponentName(qrCodeValue=QR[model_])
        hubPicture = rd(sorted(data.glob(f"hub_images/{model_}_0*.jpg"))[0])
        IP.currentPictures[cps.hubAndBottomBearingPictureKeyString] = hubPicture
        # the cycle's hub check (state 5) on its hub picture gives ImageProcessor.hubCentre, as the camera server keeps it
        # (CHANGES 169: the cap's hole finder looks round it; without it the holes are not looked for)
        if model_ not in hubCentres:
            hubCentres[model_] = None if args.holes == "morphology" else hubCheckOnPicture(hubPicture, model_)
            print(f"{model_}: hub check on hub_images/{model_}_01: "
                  + (f"centre ({hubCentres[model_]['cx']:.1f}, {hubCentres[model_]['cy']:.1f}) from "
                     f"{hubCentres[model_].get('count')} {hubCentres[model_].get('kind')}" if hubCentres[model_]
                     else "no centre" + (" (--holes morphology: not needed)" if args.holes == "morphology" else "")),
                  flush=True)
        IP.hubCentre = dict(hubCentres[model_]) if hubCentres[model_] else None
        IP.currentPictures[cps.topBearingPictureKeyString] = rd(sorted(data.glob(f"top_bearing_images/{model_}_bearing_0*.jpg"))[0])
        IP.currentPictures[cps.nutAndPlateWasherPictureKeyString] = rd(sorted(data.glob(f"nut_washer_images/{model_}_nut_0*.jpg"))[0])

    def assess(model_, pics):
        setUp(model_)
        runs.clear()
        t0 = time.monotonic()
        ok, processed, original = cps.assessPictures(MS.READ_TAKE_PICTURE_FOR_CHECKING_CAP, [p for p in pics for _ in (0, 1)])
        return ok, time.monotonic() - t0, len(runs), original

    times = []
    for model_ in ("DOST", "DOSTPLUS"):
        other = "DOSTPLUS" if model_ == "DOST" else "DOST"
        seated = sorted(data.glob(f"cap_images/{model_}_cap_*.jpg"))
        skewed = sorted(data.glob(f"cap_images/{model_}_skewed_*.jpg"))
        bad = []
        for f in seated:
            ok, secs, n, _ = assess(model_, [rd(f)] * 3)
            times.append(secs)
            if not (ok and n == 1):
                bad.append((f.name, ok, n, round(secs, 2)))
        check(f"P1 {model_}: {len(seated)} seated caps OK, one seating run each", seated and not bad, bad)
        bad = []
        for f in skewed:
            ok, secs, n, _ = assess(model_, [rd(f)] * 3)
            if ok or n > 1:
                bad.append((f.name, ok, n))
        check(f"P2 {model_}: {len(skewed)} skewed caps NOT OK, at most one seating run", skewed and not bad, bad)
        bad = []
        for f in seated:
            ok, secs, n, _ = assess(other, [rd(f)] * 3)
            if ok or n:
                bad.append((f.name, ok, n))
        check(f"P3 {model_} caps checked as {other}: NOT OK (wrong cap), no seating run", not bad, bad)
        nut = rd(sorted(data.glob(f"nut_washer_images/{model_}_nut_*.jpg"))[-1])
        ok, secs, n, _ = assess(model_, [nut] * 3)
        check(f"P4 {model_}: nut / washer picture as the cap picture NOT OK, no seating run", not ok and n == 0, (ok, n))
        cap = rd(seated[0])
        ok, secs, n, _ = assess(model_, [cap, nut, nut])
        check(f"P5 {model_}: 1 of 3 pictures with the cap -> NOT OK, no seating run", not ok and n == 0, (ok, n))
        ok, secs, n, original = assess(model_, [nut, cap, cap])
        check(f"P5 {model_}: 2 of 3 with the cap -> OK, seating once on the first that passed (picture 2)",
              ok and n == 1 and original is not None and (original == cap).all(), (ok, n))
    print(f"\ncap check through the camera server's path: {min(times):.2f}-{max(times):.2f} s per check on {device}")
    print(f"\nRESULT {sum(results)}/{len(results)} checks passed", flush=True)
    return 0 if all(results) else 1


if __name__ == "__main__":
    os._exit(main())
