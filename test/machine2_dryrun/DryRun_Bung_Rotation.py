"""
DryRun_Bung_Rotation - the bung check must not depend on how the bung is turned.

Each seated bung picture of 13 Nov (bunk_images_20261113, labels below) is turned about the model's seated bung centre
(<vendor>.vision.bunk.seating.seated.centre.<model>) in --step degree steps (default 15: 24 angles) and checked with
the real CheckBunk.checkBunk (state 24; CheckNoCapBunk runs the same hole detection and seating):
  - as its own model    -> the hole pattern must pass at every angle
  - as the other model  -> the hole pattern must fail at every angle (wrong bung)    (--own-only skips this)
Turning the picture is not the same as turning the bung under the fixed light (the highlights turn with it), but it
moves the holes to every position on the ring, which is what made the old check (holes found only through YOLO boxes
and a sparse grid that missed the hole ring) depend on the bung's angle. So the hole pattern is judged. The seating
step's verdict is shown ('s') but not judged: the picture's light, the camera's perspective oval and the hub around the
face all turn with it, which a real bung turned on the hub does not do (first run, 17 Nov: the DOST face roundness of
A01 fell from 0.971 to 0.940-0.946 at 180 and 225 deg, under the 0.952 minimum).

Per picture one line per model: one mark per angle ('+' OK, '.' NOT OK), then the angles that went wrong, each with its
"[Bung] ..." gamma lines (the lines the camera server logs) and its "[Seating] ..." line; --verbose prints them for
every angle.

Needs MobileSAM (and the YOLO ObjectAwareModel for the YOLO boxes; --no-yolo leaves YOLO out, so the dense point grid
alone has to find the holes - the harder test, and the only one possible where the YOLO weights are missing).
A CUDA GPU makes it fast; on a CPU each gamma takes about half a minute.

Run from the project root:
    python test/machine2_dryrun/DryRun_Bung_Rotation.py                         (all 23 seated pictures, 15 deg steps)
    python test/machine2_dryrun/DryRun_Bung_Rotation.py --pictures A01 B11 --step 30 --no-yolo
"""
import argparse
import os
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["COSTHETA_PLC_VENDOR"] = "siemens" if HERE.name.startswith("machine2") else "allenbradley"

import cv2
import numpy as np

QR = {"DOST": "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$",
      "DOSTPLUS": "8201206$400112VA1C$22881$15.10.2026$C5211702$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$"}
OTHER = {"DOST": "DOSTPLUS", "DOSTPLUS": "DOST"}
EXPECTED = {"DOST": 4, "DOSTPLUS": 2}
# Seated bungs of the 13 Nov pictures (CHANGES 100, labelled by eye)
SEATED = {**{n: "DOST" for n in ["A01"] + [f"A{i:02d}" for i in range(7, 17)]},
          **{n: "DOSTPLUS" for n in ["A03", "A04"] + [f"B{i:02d}" for i in range(11, 21)]}}

ap = argparse.ArgumentParser()
ap.add_argument("--folder", default=str(HERE / "bunk_images_20261113"))
ap.add_argument("--pictures", nargs="*", default=None, help="picture names without extension (default: all seated)")
ap.add_argument("--step", type=float, default=15.0, help="degrees between angles")
ap.add_argument("--own-only", action="store_true", help="only check each picture as its own model")
ap.add_argument("--no-yolo", action="store_true", help="no YOLO boxes: the point grid alone finds the holes")
ap.add_argument("--verbose", action="store_true", help="print the [Bung] line of every gamma")
ARGS = ap.parse_args()


def turned(image: np.ndarray, centre, degrees: float) -> np.ndarray:
    if degrees == 0:
        return image
    m = cv2.getRotationMatrix2D((float(centre[0]), float(centre[1])), degrees, 1.0)
    return cv2.warpAffine(image, m, (image.shape[1], image.shape[0]), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REFLECT)


def main() -> int:
    import camera.BunkSegmenter as BS
    from camera.CheckBunk import CheckBunk

    if ARGS.no_yolo:                       # MobileSAM only; the YOLO weights need not exist
        from camera.ModelManager import ModelManager
        from mobile_sam import sam_model_registry, SamPredictor

        class _NoBoxes:
            def predict(self, *a, **k):
                return []
        mm = ModelManager.get_instance()
        if mm.sam_model is None:
            mm.sam_model = sam_model_registry["vit_t"](checkpoint=mm.mobile_sam_path).to(mm.device).eval()
            mm.sam_predictor = SamPredictor(mm.sam_model)
        mm.yolo_model = _NoBoxes()
    segmenter = BS.BunkSegmenter.get_instance()

    gammaLines, lastHoles = [], {}
    original = BS.BunkSegmenter._holes_in_preprocessed

    def recording(self, *a, **k):
        result = original(self, *a, **k)
        gammaLines.append(self.last_gamma_text)
        lastHoles["result"] = result
        return result
    BS.BunkSegmenter._holes_in_preprocessed = recording

    seatingLines, lastSeating = [], {}
    originalSeating = BS.check_bung_seating

    def recordingSeating(*a, **k):
        result = originalSeating(*a, **k)
        lastSeating["seated"] = result["seated"]
        seatingLines.append(f"[Seating] {'SEATED' if result['seated'] else 'NOT SEATED'} - {result['reason']}")
        return result
    BS.check_bung_seating = recordingSeating

    names = ARGS.pictures or sorted(SEATED)
    angles = [a for a in np.arange(0.0, 360.0, ARGS.step)]
    print(f"Machine: {os.environ['COSTHETA_PLC_VENDOR']}   disc centre {tuple(BS.OUTER_DISC_CENTER)}   holes within "
          f"r{BS.INNER_DISC_RADIUS}, {BS.MIN_MASK_AREA}-{BS.MAX_MASK_AREA} px, grid every {BS.HOLE_GRID_STEP} px   "
          f"YOLO {'off' if ARGS.no_yolo else 'on'}   seating check {BS.SEATING_CHECK}")
    print(f"{len(names)} picture(s) x {len(angles)} angles ({ARGS.step:g} deg steps)   judged: the hole pattern   "
          f"'+' holes OK (and seated)  's' holes OK, seating said NOT SEATED  '.' holes NOT OK\n")
    failures, total, notSeated, t0 = 0, 0, 0, time.time()
    for name in names:
        own = SEATED.get(name)
        path = next((p for p in (pathlib.Path(ARGS.folder) / f"{name}{e}" for e in (".jpg", ".png")) if p.exists()), None)
        if own is None or path is None:
            print(f"  {name}: not a labelled seated picture or not found - skipped")
            continue
        image = cv2.imread(str(path))
        for model in ([own] if ARGS.own_only else [own, OTHER[own]]):
            mustBeOk = model == own
            centre = BS.SEATING_SEATED_CENTRE_DOSTPLUS if own == "DOSTPLUS" else BS.SEATING_SEATED_CENTRE_DOST
            marks, wrong = [], []
            for a in angles:
                gammaLines.clear()
                seatingLines.clear()
                lastHoles.clear()
                lastSeating.clear()
                _, ok, gamma = CheckBunk.checkBunk(turned(image, centre, a), QR[model], None)
                count, _, definitive = lastHoles.get("result", (0, [], False))
                holesOk = bool(definitive and count == EXPECTED[model])
                seatedFalse = holesOk and lastSeating.get("seated") is False
                notSeated += int(seatedFalse and mustBeOk)
                marks.append("." if not holesOk else ("s" if seatedFalse else "+"))
                ok = holesOk
                total += 1
                if ok != mustBeOk:
                    failures += 1
                    wrong.append(f"{a:g}")
                if ARGS.verbose or ok != mustBeOk or (seatedFalse and mustBeOk):
                    for line in gammaLines + seatingLines:
                        print(f"      {a:5g} deg  {line}")
            print(f"  {name} ({own}) as {model:8s} must be {'OK    ' if mustBeOk else 'NOT OK'}  {''.join(marks)}"
                  + (f"   WRONG at {', '.join(wrong)} deg" if wrong else ""), flush=True)
    print(f"\nRESULT: {total - failures}/{total} hole patterns as expected ({time.time() - t0:.0f} s)"
          + ("" if failures == 0 else f" - {failures} UNEXPECTED")
          + (f"; seating NOT SEATED at {notSeated} angle(s) of seated bungs (information, see the docstring)"
             if notSeated else ""))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
