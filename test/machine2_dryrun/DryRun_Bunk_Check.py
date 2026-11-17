"""
DryRun_Bunk_Check - the real bunk checks on this machine's bung pictures, with this machine's settings
(<vendor>.vision.bunk.* in ApplicationConfiguration.properties). Console only. Needs the MobileSAM models (as the
camera server does); a CUDA GPU makes it fast.

Both checks run the same hole detection (camera/BunkSegmenter.py):
  - CheckNoCapBunk.checkNoCapBunk  - state 13, bung before the component press
  - CheckBunk.checkBunk            - state 24, bung before the cap press
The r<outer.disc.radius> disc round <vendor>.vision.bunk.outer.disc.center is segmented (gammas 2.0, 7.75, 4.0, 6.5, 1.0
until a definite answer; YOLO boxes + a point every hole.grid.step px); holes count only inside r<inner.disc.radius>
round the same centre (area min/max.mask.area). One "[Bung] ..." log line per gamma.
  DOST     : 4 holes forming a square (sides within square.side.length.tolerance of the median, corners
             square.min/max.interior.angle, angle spread / mean within square.max.angle.std of 90)
  DOSTPLUS : 2 holes dostplus.hole.pair.distance.min..max apart, opposite about the centre (the best pair
             among the candidates); a DOST square found = NOT OK

Pictures: <folder>/<MODEL>_*.jpg|png (MODEL = DOST or DOSTPLUS, from the file name; default folder: bunk_images next to
this file). Each picture is checked
  - as its own model           -> must be OK
  - as the other model         -> must be NOT OK (wrong bung)
Pictures with "not_seated" in the name are only reported (the check does not test whether the bung is seated).

Printed per picture: OK / NOT OK, the gamma that decided, the holes found (centres in the full picture), their spacing
(DOSTPLUS: pair distance; DOST: the 4 sides), and how far the centre of the holes is from the configured centre.

Run from the project root:
    python test/machine2_dryrun/DryRun_Bunk_Check.py                      (CheckBunk, state 24)
    python test/machine2_dryrun/DryRun_Bunk_Check.py --check nocap        (CheckNoCapBunk, state 13)
    python test/machine2_dryrun/DryRun_Bunk_Check.py --check both --save  (both; marked pictures to <folder>/bunk_results/)
    python test/machine2_dryrun/DryRun_Bunk_Check.py --folder D:/pictures/bungs
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

import cv2
import numpy as np

QR = {"DOST": "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$",
      "DOSTPLUS": "8201206$400112VA1C$22881$15.10.2026$C5211702$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$"}
OTHER = {"DOST": "DOSTPLUS", "DOSTPLUS": "DOST"}
EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp")

ap = argparse.ArgumentParser()
ap.add_argument("--folder", default=str(HERE / "bunk_images"))
ap.add_argument("--check", choices=("bunk", "nocap", "both"), default="bunk")
ap.add_argument("--save", action="store_true")
ARGS = ap.parse_args()


def modelOf(name: str):
    upper = name.upper()
    if upper.startswith("DOSTPLUS"):
        return "DOSTPLUS"
    if upper.startswith("DOST"):
        return "DOST"
    return None


def main() -> int:
    import camera.BunkSegmenter as BS
    from camera.CheckBunk import CheckBunk
    from camera.CheckNoCapBunk import CheckNoCapBunk

    # Keep what the hole detection returned last (the decisive gamma's holes), to print the hole positions
    last = {}
    original = BS.BunkSegmenter.segment_holes_single

    def recording(self, preprocessed_image, gamma, expected_count, model_type):
        result = original(self, preprocessed_image, gamma, expected_count, model_type)
        last["result"] = result
        return result
    BS.BunkSegmenter.segment_holes_single = recording

    centre = tuple(BS.OUTER_DISC_CENTER)
    offset = (centre[0] - BS.INNER_DISC_RADIUS, centre[1] - BS.INNER_DISC_RADIUS)   # inner-disc coords -> picture
    print(f"Machine: {os.environ['COSTHETA_PLC_VENDOR']}   centre {centre}   outer r{BS.OUTER_DISC_RADIUS}   "
          f"holes within r{BS.INNER_DISC_RADIUS}   hole area {BS.MIN_MASK_AREA}-{BS.MAX_MASK_AREA}")
    print(f"DOST: square, sides within {BS.SQUARE_SIDE_LENGTH_TOLERANCE:.0%}, corners {BS.SQUARE_MIN_INTERIOR_ANGLE}-"
          f"{BS.SQUARE_MAX_INTERIOR_ANGLE} deg;  DOSTPLUS: 2 holes {BS.DOSTPLUS_HOLE_PAIR_DISTANCE_MIN}-"
          f"{BS.DOSTPLUS_HOLE_PAIR_DISTANCE_MAX} px apart")

    files = sorted(f for f in glob.glob(os.path.join(ARGS.folder, "*")) if f.lower().endswith(EXTENSIONS))
    files = [f for f in files if modelOf(pathlib.Path(f).name)]
    if not files:
        print(f"\nNo DOST_* / DOSTPLUS_* pictures in {ARGS.folder}. Put bung pictures there (named by model) or pass "
              f"--folder.")
        return 1

    checks = {"bunk": [("CheckBunk (state 24)", CheckBunk.checkBunk)],
              "nocap": [("CheckNoCapBunk (state 13)", CheckNoCapBunk.checkNoCapBunk)]}
    checks["both"] = checks["nocap"] + checks["bunk"]
    outDir = pathlib.Path(ARGS.folder) / "bunk_results"
    failures, total = 0, 0

    for title, check in checks[ARGS.check]:
        print(f"\n######## {title}")
        for asOther in (False, True):
            print(f"\n=== {'as the OTHER model (wrong bung) - must be NOT OK' if asOther else 'as its own model - must be OK'}")
            for f in files:
                name = pathlib.Path(f).name
                own = modelOf(name)
                model = OTHER[own] if asOther else own
                reportOnly = "not_seated" in name.lower()
                if reportOnly and asOther:
                    continue
                image = cv2.imread(f)
                last.clear()
                t0 = time.time()
                marked, ok, gamma = check(image, QR[model], None)
                seconds = time.time() - t0
                holes = []
                if "result" in last:
                    _, components, _ = last["result"]
                    holes = [(c[0] + offset[0], c[1] + offset[1]) for c, _ in components]
                detail = ""
                if holes:
                    pts = np.array(holes)
                    mid = pts.mean(axis=0)
                    if len(pts) == 2:
                        detail = f"pair {np.hypot(*(pts[0] - pts[1])):5.1f} px"
                    elif len(pts) >= 3:
                        sides = [np.hypot(*(pts[i] - pts[(i + 1) % len(pts)])) for i in range(len(pts))]
                        detail = "sides " + "/".join(f"{s:.0f}" for s in sides)
                    detail += (f"  holes centre ({mid[0]:.0f},{mid[1]:.0f}), {np.hypot(mid[0] - centre[0], mid[1] - centre[1]):4.1f} px "
                               f"from the set centre")
                expected = None if reportOnly else (not asOther)
                verdict = "OK" if ok else "NOT OK"
                flag = ""
                if expected is not None:
                    total += 1
                    if ok != expected:
                        failures += 1
                        flag = "   <-- UNEXPECTED"
                else:
                    flag = "   (report only)"
                print(f"    {name:34s} as {model:8s} {verdict:7s} gamma {str(gamma):5s} holes {len(holes)}  {detail}  "
                      f"[{seconds:.1f}s]{flag}")
                if ARGS.save and marked is not None:
                    out = marked.copy()
                    cv2.circle(out, centre, BS.OUTER_DISC_RADIUS, (255, 255, 0), 1)
                    cv2.circle(out, centre, BS.INNER_DISC_RADIUS, (0, 255, 255), 1)
                    cv2.drawMarker(out, centre, (0, 255, 255), cv2.MARKER_CROSS, 20, 1)
                    for x, y in holes:
                        cv2.circle(out, (int(round(x)), int(round(y))), 4, (0, 0, 255), -1)
                    cv2.putText(out, f"{title} as {model}: {verdict}", (10, 700), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                                (0, 255, 0) if ok else (0, 0, 255), 2)
                    outDir.mkdir(parents=True, exist_ok=True)
                    tag = "nocap" if "NoCap" in title else "bunk"
                    cv2.imwrite(str(outDir / f"{pathlib.Path(name).stem}_{tag}_as_{model}.png"), out)

    print(f"\nRESULT: {total - failures}/{total} as expected" + ("" if failures == 0 else f" - {failures} UNEXPECTED"))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
