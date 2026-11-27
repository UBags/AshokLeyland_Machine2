"""
DryRun_Cap_Check - the real cap check (CheckCap.checkCap) on this machine's cap pictures, with this machine's settings
(vision.<vendor>.cap.* in ApplicationConfiguration.properties). Console only.

The check:
  1. which cap: brass if mean R-B in the DOSTPLUS circle (vision.<vendor>.dostplus.cap.radius) is above
     rb.threshold.dostplus; else silver if mean R-B in the DOST circle is below rb.threshold.dost and the mean brightness
     within silver.brightness.radius reaches silver.brightness.min; else none;
  2. it must be the model's cap (DOST silver, DOSTPLUS brass - the model comes from the QR code);
  3. the cap picture must be brighter than each earlier picture of the same part (hub + bottom bearing, top bearing,
     nut + plate washer) by at least delta.threshold.<hub|top|nut>.<model of the cap found>;
  4. (cap.seating.check) the cap must be seated - MobileSAM: brass cap centre to the bore centre round it, silver top
     face centre to the cap outline centre, within seating.brass.offset.max / seating.silver.face.offset.max.

Pictures (in this folder):
  cap_images/<MODEL>_cap_NN.jpg                       the cap pictures (seated)
  cap_images/<MODEL>_skewed_NN.jpg                    caps placed skewed (off-centre or tilted)
  hub_images/<MODEL>_NN.jpg                            hub + bottom bearing pictures
  top_bearing_images/<MODEL>_bearing_NN.jpg            top bearing pictures
  nut_washer_images/<MODEL>_nut_NN.jpg (and _washer_)  nut + plate washer pictures
The earlier pictures are not of the same part as each cap, so every cap picture is compared with every earlier picture of
its model: the table shows the lowest and highest brightening; the check itself is run with the earlier pictures that
give the lowest brightening for a cap (hardest case to accept) and the highest for a picture without a cap (hardest case
to reject). Without earlier pictures only steps 1 and 2 are reported.

Rows: every seated cap as its own model (must be OK); every cap as the other model (wrong cap, must be NOT OK); every
skewed cap as its own model (must be NOT OK; the result with seating off is shown, to tell which step rejects it - a
cap pushed far off the bore is already "no cap" at step 1; only when seating is on for this machine); every nut / washer picture as the cap picture (cap left off), as
DOST and as DOSTPLUS (must be NOT OK). The seating column shows the measured offset (seated caps and skewed caps).
Seating needs MobileSAM (models/MobileSAM, through ModelManager), as in production.

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
    sys.path.insert(0, str(HERE))
    from _hub_holes_option import applyHolesOption
    applyHolesOption()          # sam: the hub centre from the real checkHub() on each picture (no hub check in this run)
    from camera.CheckCap import CheckCap
    import camera.CheckCap as module
    keys = (module.hubAndBottomBearingPictureKeyString, module.topBearingPictureKeyString,
            module.nutAndPlateWasherPictureKeyString)
    print(f"Machine: {os.environ['COSTHETA_PLC_VENDOR']}   centre {tuple(CheckCap.CENTER)}   "
          f"radius DOST {CheckCap.RADIUS_DOST} / DOSTPLUS {CheckCap.RADIUS_DOSTPLUS}")
    print(f"Seating: {'on' if CheckCap.SEATING_CHECK else 'off'} - brass cap to bore <= {CheckCap.SEATING_BRASS_OFFSET_MAX}, "
          f"silver top face edge on one circle >= {CheckCap.SEATING_SILVER_FACE_FIT_MIN} and hub centre uncovered <= "
          f"{CheckCap.SEATING_HUB_VISIBLE_MAX} px (hub picture), silver outline to centre <= "
          f"{CheckCap.SEATING_SILVER_OUTLINE_OFFSET_MAX} px (0 = off)")
    print(f"Which cap: brass if R-B(r{CheckCap.RADIUS_DOSTPLUS}) > {CheckCap.RB_THRESHOLD_DOSTPLUS}; else silver if "
          f"R-B(r{CheckCap.RADIUS_DOST}) < {CheckCap.RB_THRESHOLD_DOST} and brightness(r{CheckCap.SILVER_BRIGHTNESS_RADIUS}) "
          f">= {CheckCap.SILVER_BRIGHTNESS_MIN}; else none")
    failures = 0
    outDir = HERE / "cap_images" / "cap_results"

    def bases(model):
        return [pictures(f"hub_images/{model}_[0-9]*.*"), pictures(f"top_bearing_images/{model}_bearing_*.*"),
                pictures(f"nut_washer_images/{model}_nut_*.*")]

    def capFound(image):
        rbBrass = CheckCap._compute_rb_difference(image, CheckCap._get_circular_mask(image.shape, CheckCap.RADIUS_DOSTPLUS))
        rbSilver = CheckCap._compute_rb_difference(image, CheckCap._get_circular_mask(image.shape, CheckCap.RADIUS_DOST))
        bright = float(np.mean(image[CheckCap._get_circular_mask(image.shape, CheckCap.SILVER_BRIGHTNESS_RADIUS) > 0]))
        if rbBrass > CheckCap.RB_THRESHOLD_DOSTPLUS:
            found = "brass"
        elif rbSilver < CheckCap.RB_THRESHOLD_DOST and bright >= CheckCap.SILVER_BRIGHTNESS_MIN:
            found = "silver"
        else:
            found = "none"
        return found, rbBrass, rbSilver, bright

    def seatingText(image, found):
        if not CheckCap.SEATING_CHECK or found == "none":
            return ""
        s = CheckCap.checkSeating(image, "DOSTPLUS" if found == "brass" else "DOST")
        if s["offset"] is None:
            return f"seat: {s['reason']}"
        if s.get("fit") is not None:    # silver (CHANGES 129): face edge on one circle, hub centre uncovered
            return (f"face {s['fit']:.2f}/{s['fit_min']}" + (f" hub {s['hub_visible']}" if "hub_visible" in s else "")
                    + (f" ctr {s['outline_offset']:4.1f}" if "outline_offset" in s else ""))
        return f"seat {s['offset']:5.1f}/{s['limit']}" + (f" ctr {s['outline_offset']:4.1f}" if "outline_offset" in s else "")

    def run(name, image, model, expectOK, exclude=None, tag="", skewed=False):
        nonlocal failures
        found, rbBrass, rbSilver, bright = capFound(image)
        group = bases(model)
        capModel = {"brass": "DOSTPLUS", "silver": "DOST"}.get(found, model)
        radius = CheckCap.RADIUS_DOSTPLUS if capModel == "DOSTPLUS" else CheckCap.RADIUS_DOST
        mask = CheckCap._get_circular_mask(image.shape, radius)
        cells, chosen = [], []
        if all(group):
            for label, g in zip(("hub", "top", "nut"), group):
                d = [(CheckCap._compute_clamped_delta_grayscale(image, b, mask)[1], b) for n, b in g if n != exclude]
                pick = min(d, key=lambda x: x[0]) if expectOK else max(d, key=lambda x: x[0])
                chosen.append(pick[1])
                cells.append(f"{label} {min(x[0] for x in d):5.1f}-{max(x[0] for x in d):5.1f}")
            annotated, ok = CheckCap.checkCap(image, dict(zip(keys, chosen)), QR[model])
            verdict = "OK" if ok else "NOT OK"
            if skewed:      # with seating off: which step rejects it (information)
                CheckCap.SEATING_CHECK = False
                try:
                    _, okWithout = CheckCap.checkCap(image, dict(zip(keys, chosen)), QR[model])
                finally:
                    CheckCap.SEATING_CHECK = True
                verdict += (" (seating off: OK - rejected by the seating step)" if okWithout
                            else " (seating off: NOT OK - rejected before the seating step)")
        else:
            annotated = image
            ok = {"brass": "DOSTPLUS", "silver": "DOST"}.get(found) == model
            verdict = ("cap " + ("OK" if ok else "NOT OK")) + " (no earlier pictures)"
        wrong = ok != expectOK
        failures += wrong
        seat = seatingText(image, found) if (expectOK or skewed) else ""
        print(f"    {name:24s} as {model:8s} cap={found:6s} R-B {rbBrass:6.1f}/{rbSilver:6.1f} bright {bright:5.0f}  "
              f"{' | '.join(cells):45s} {seat:16s} {verdict}{'   <-- UNEXPECTED' if wrong else ''}")
        if SAVE and all(group):
            outDir.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(outDir / f"{pathlib.Path(name).stem}_as_{model}{tag}.png"), annotated)

    other = {"DOST": "DOSTPLUS", "DOSTPLUS": "DOST"}
    for model in ("DOST", "DOSTPLUS"):
        caps = pictures(f"cap_images/{model}_cap_*.*")
        print(f"\n=== {model} caps ({len(caps)}), as {model} - must be OK")
        for name, image in caps:
            run(name, image, model, True)
    for model in ("DOST", "DOSTPLUS"):
        caps = pictures(f"cap_images/{model}_cap_*.*")
        print(f"\n=== {model} caps as {other[model]} (wrong cap) - must be NOT OK")
        for name, image in caps:
            run(name, image, other[model], False)
    if CheckCap.SEATING_CHECK:
        for model in ("DOST", "DOSTPLUS"):
            skewed = pictures(f"cap_images/{model}_skewed_*.*")
            print(f"\n=== {model} caps placed skewed ({len(skewed)}), as {model} - must be NOT OK (not seated)")
            for name, image in skewed:
                run(name, image, model, False, skewed=True)
    else:
        print("\n(seating check off on this machine - skewed caps not checked)")
    noCap = pictures("nut_washer_images/*.*")
    for model in ("DOST", "DOSTPLUS"):
        print(f"\n=== no cap: nut / washer pictures as {model} - must be NOT OK")
        for name, image in noCap:
            run(name, image, model, False, exclude=name)
    print(f"\nRESULT: {'all as expected' if failures == 0 else f'{failures} unexpected result(s)'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
