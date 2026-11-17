"""Plant PC check: how long each vision check takes on THIS PC, with the real models, through the vision check worker
process (camera/VisionCheckWorker.py), against <vendor>.vision.check.timeout.sec (2026-11-07, CHANGES section 85;
docs/plant-pc-verification.md, step 2).

Runs every picture in this folder's knuckle / hub / top bearing / nut-washer / bung / cap image folders through
the check of its station (the picture resized as the camera server does), and prints per check: the number of
pictures, the median and the largest time in the worker, the transfer to the worker and back, and the margin to the
timeout. The OK / NOT OK of each picture is printed too, but this is a timing run, not a check of the verdicts (the
dry runs per check do that).

Needs the models (models/MobileSAM/..., the YOLO weights) and the camera server's packages; meant for the plant PC
(GPU), runs on a CPU as well (slowly: the times are then much longer than on the plant PC).

    python test/machine2_dryrun/PlantPC_Vision_Timing.py            (from the project root; exit code 1 if a check's
                                                                     largest time is over 80 % of the timeout)
"""
import os, pathlib, statistics, sys, time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("COSTHETA_PLC_VENDOR", "siemens" if HERE.name.startswith("machine2") else "allenbradley")

QR = {"DOST": "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$",
      "DOSTPLUS": "8201206$400112VA1C$22881$15.10.2026$C5211702$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$"}

if __name__ == "__main__":
    import cv2
    from statemachine.StateMachine import MachineState as MS
    from Configuration import CosThetaConfigurator as C
    import camera.CameraProcessorServer as cps
    from camera.VisionCheckWorker import VisionCheckWorker

    STATIONS = [("knuckle", "knuckle_images", MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE),
                ("hub and bottom bearing", "hub_images", MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING),
                ("top bearing", "top_bearing_images", MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING),
                ("nut and plate washer", "nut_washer_images", MS.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER),
                ("bung for component press", "bung_images", MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_COMPONENT_PRESS),
                ("bung for cap press", "bung_images", MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_CAP_PRESS),
                ("cap", "cap_images", MS.READ_TAKE_PICTURE_FOR_CHECKING_CAP)]
    timeout = C.getInstance().getVisionCheckTimeout()
    print(f"Vision check timeout ({os.environ['COSTHETA_PLC_VENDOR']}.vision.check.timeout.sec): {timeout:g} s", flush=True)
    w = VisionCheckWorker()
    w.start()
    t0 = time.monotonic()
    if not w.waitUntilReady(900):
        print("The vision check worker did not get ready in 15 minutes."); os._exit(1)
    print(f"Worker ready in {time.monotonic() - t0:.1f} s (models loaded, warm-up done)", flush=True)
    if w._problems:
        print("Missing vision dependencies - nothing can be timed:\n  - " + "\n  - ".join(w._problems)); os._exit(1)

    rows, slow = [], []
    for label, folder, state in STATIONS:
        files = sorted(p for p in (HERE / folder).glob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp"))
        times, transfers, verdicts = [], [], []
        width, height = C.getInstance().getTargetWidthAndHeight(currentMachineState=MS.getMachineStateAsInt(state))
        for f in files:
            img = cv2.imread(str(f))
            if img is None:
                continue
            if (img.shape[1], img.shape[0]) != (width, height):
                img = cv2.resize(img, (width, height), interpolation=cv2.INTER_LANCZOS4)
            model = "DOSTPLUS" if f.name.upper().startswith("DOSTPLUS") else "DOST"
            cps.ImageProcessor.endOfCycle()
            cps.ImageProcessor.setCurrentComponentName(QR[model])
            allok, _, _, reason = w.assess(state, [img] * 6, max(timeout, 600))    # no deadline here: measure it
            if reason and w.lastCheckSecs is None:
                print(f"  {label}: {f.name}: {reason}", flush=True)
                continue
            times.append(w.lastCheckSecs); transfers.append(w.lastTransferSecs)
            verdicts.append("OK" if allok else "NOT OK")
            print(f"  {label}: {f.name}: {w.lastCheckSecs:.2f} s ({verdicts[-1]})", flush=True)
        if times:
            worst = max(times)
            rows.append((label, len(times), statistics.median(times), worst, max(transfers), timeout - worst))
            if worst > 0.8 * timeout:
                slow.append(label)

    print(f"\n{'check':<28}{'pictures':>9}{'median s':>10}{'max s':>8}{'transfer s':>12}{'margin s':>10}")
    for label, n, med, worst, tr, margin in rows:
        print(f"{label:<28}{n:>9}{med:>10.2f}{worst:>8.2f}{tr:>12.2f}{margin:>10.2f}")
    if slow:
        print(f"\nOVER 80 % OF THE {timeout:g} s TIMEOUT: {', '.join(slow)} - raise the timeout or check the GPU")
    else:
        print(f"\nAll checks within 80 % of the {timeout:g} s timeout")
    w.stop()
    os._exit(1 if slow or not rows else 0)
