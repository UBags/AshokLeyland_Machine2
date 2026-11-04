"""
DryRun_Cap_Check - the real cap check (CheckCap.checkCap) on this machine's cap pictures, with this machine's settings
(<vendor>.vision.cap.* in ApplicationConfiguration.properties). Console only.

The check, in a circle of <vendor>.vision.cap.radius.<model> round <vendor>.vision.cap.center:
  Step 1: the cap picture must be brighter than each earlier picture of the same part (hub + bottom bearing, top
          bearing, nut + plate washer) by at least <vendor>.vision.cap.delta.threshold.<hub|top|nut>.<model>
          (brightening = cap minus earlier picture, negative values counted as 0, averaged over the colours and the circle);
  Step 2: mean R-B in the circle: DOST below <vendor>.vision.cap.rb.threshold.dost (silver cap),
          DOSTPLUS above <vendor>.vision.cap.rb.threshold.dostplus (brass cap).

Pictures (in this folder):
  cap_images/<MODEL>_cap_NN.jpg                       the cap pictures
  hub_images/<MODEL>_NN.jpg                            hub + bottom bearing pictures
  top_bearing_images/<MODEL>_bearing_NN.jpg            top bearing pictures
  nut_washer_images/<MODEL>_nut_NN.jpg                 nut + plate washer pictures
The earlier pictures are not of the same part as each cap, so every cap picture is compared with every earlier picture of
its model: the table shows the lowest and highest brightening, and the check itself is run with the earlier pictures that
give the lowest brightening (the hardest case). Without earlier pictures only Step 2 is reported.

"No cap" rows: each nut picture used as the cap picture (the cap left off), against the other earlier pictures - these
must all be NOT OK.

Run from the project root:   python test/machine2_dryrun/DryRun_Cap_Check.py   [--save]   (--save writes the check's
marked pictures to cap_images/cap_results/)
"""
import glob
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["COSTHETA_PLC_VENDOR"] = "siemens" if HERE.name.startswith("machine2") else "allenbradley"

import cv2
import numpy as np

QR = {"DOST": "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$",
      "DOSTPLUS": "8201206$400112VA1C$22881$15.10.2026$C5211702$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$"}
SAVE = "--save" in sys.argv


def pictures(pattern):
    return [(pathlib.Path(f).name, cv2.imread(f)) for f in sorted(glob.glob(str(HERE / pattern)))]


def main() -> int:
    from camera.CheckCap import CheckCap
    import camera.CheckCap as module
    keys = (module.hubAndBottomBearingPictureKeyString, module.topBearingPictureKeyString,
            module.nutAndPlateWasherPictureKeyString)
    print(f"Machine: {os.environ['COSTHETA_PLC_VENDOR']}   centre {tuple(CheckCap.CENTER)}   "
          f"radius DOST {CheckCap.RADIUS_DOST} / DOSTPLUS {CheckCap.RADIUS_DOSTPLUS}")
    failures = 0
    outDir = HERE / "cap_images" / "cap_results"
    for model in ("DOST", "DOSTPLUS"):
        caps = pictures(f"cap_images/{model}_cap_*.*")
        bases = [pictures(f"hub_images/{model}_[0-9]*.*"), pictures(f"top_bearing_images/{model}_bearing_*.*"),
                 pictures(f"nut_washer_images/{model}_nut_*.*")]
        plus = model == "DOSTPLUS"
        radius = CheckCap.RADIUS_DOSTPLUS if plus else CheckCap.RADIUS_DOST
        limits = ((CheckCap.DELTA_THRESHOLD_HUB_DOSTPLUS, CheckCap.DELTA_THRESHOLD_TOP_DOSTPLUS,
                   CheckCap.DELTA_THRESHOLD_NUT_DOSTPLUS) if plus else
                  (CheckCap.DELTA_THRESHOLD_HUB_DOST, CheckCap.DELTA_THRESHOLD_TOP_DOST, CheckCap.DELTA_THRESHOLD_NUT_DOST))
        rbLimit = (">", CheckCap.RB_THRESHOLD_DOSTPLUS) if plus else ("<", CheckCap.RB_THRESHOLD_DOST)
        haveBases = all(bases)
        print(f"\n=== {model}: {len(caps)} cap pictures; earlier pictures hub {len(bases[0])}, top bearing "
              f"{len(bases[1])}, nut {len(bases[2])}")
        print(f"    limits: brightening hub >= {limits[0]}, top >= {limits[1]}, nut >= {limits[2]};  R-B {rbLimit[0]} {rbLimit[1]}")

        def run(name, image, excludeNut=None, expectOK=True):
            nonlocal failures
            mask = CheckCap._get_circular_mask(image.shape, radius)
            rb = CheckCap._compute_rb_difference(image, mask)
            cells, worst = [], []
            if haveBases:
                for label, group in zip(("hub", "top", "nut"), bases):
                    pool = [(n, b) for n, b in group if not (label == "nut" and n == excludeNut)]
                    d = [(CheckCap._compute_clamped_delta_grayscale(image, b, mask)[1], b) for n, b in pool]
                    lo = min(d, key=lambda x: x[0])
                    worst.append(lo[1])
                    cells.append(f"{label} {lo[0]:5.1f}-{max(x[0] for x in d):5.1f}")
                annotated, ok = CheckCap.checkCap(image, dict(zip(keys, worst)), QR[model])
                verdict = "OK" if ok else "NOT OK"
                wrong = ok != expectOK
            else:
                annotated, ok = image, (rb > rbLimit[1] if plus else rb < rbLimit[1])
                verdict = "Step 2 " + ("OK" if ok else "NOT OK")
                wrong = ok != expectOK
            failures += wrong
            print(f"    {name:24s} {' | '.join(cells):48s} R-B {rb:6.1f}   {verdict}{'   <-- UNEXPECTED' if wrong else ''}")
            if SAVE and haveBases:
                outDir.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(outDir / f"{pathlib.Path(name).stem}.png"), annotated)

        for name, image in caps:
            run(name, image)
        if haveBases:
            print("    -- no cap (nut picture as the cap picture) --")
            for name, image in bases[2]:
                run(name, image, excludeNut=name, expectOK=False)
    print(f"\nRESULT: {'all as expected' if failures == 0 else f'{failures} unexpected result(s)'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
