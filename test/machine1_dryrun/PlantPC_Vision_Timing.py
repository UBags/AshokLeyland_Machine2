"""Plant PC check: how long each vision check takes on THIS PC, with the real models, through the vision check worker
process (camera/VisionCheckWorker.py), against the check-time targets (2026-11-07, CHANGES section 85;
docs/plant-pc-verification.md, step 2). Targets (Uddi, 2026-11-27, CHANGES 214): ideally within 1.5 s per check, at
most 2 s on average. vision.<vendor>.check.timeout.sec (12 s) is only the deadline after which a check is cut off as
NOT OK - not a target; its margin is printed for information.

Runs every picture in this folder's knuckle / hub / top bearing / nut-washer / bung / cap image folders through
the check of its station (the picture resized as the camera server does), and prints per check: the number of
pictures, the mean, median and largest time in the worker, the transfer to the worker and back, and the margin to the
timeout. The OK / NOT OK of each picture is printed too, but this is a timing run, not a check of the verdicts (the
dry runs per check do that).

Needs the models (models/MobileSAM/..., the YOLO weights) and the camera server's packages; meant for the plant PC
(GPU), runs on a CPU as well (slowly: the times are then much longer than on the plant PC).

    python test/machine1_dryrun/PlantPC_Vision_Timing.py            (from the project root; exit code 1 if a check's
                                                                     mean time is over 2 s)
"""
import os, pathlib, statistics, sys, time

TARGET_IDEAL_SECS = 1.5         # per check, ideally (Uddi, 2026-11-27)
TARGET_AVERAGE_SECS = 2.0       # mean per check, at most

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
    print(f"Vision check timeout (vision.{os.environ['COSTHETA_PLC_VENDOR']}.check.timeout.sec): {timeout:g} s", flush=True)
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
            worst, mean = max(times), statistics.mean(times)
            rows.append((label, len(times), mean, statistics.median(times), worst, max(transfers), timeout - worst))
            if mean > TARGET_AVERAGE_SECS:
                slow.append(label)

    print(f"\n{'check':<28}{'pictures':>9}{'mean s':>8}{'median s':>10}{'max s':>8}{'transfer s':>12}"
          f"{'to timeout s':>14}  target")
    for label, n, mean, med, worst, tr, margin in rows:
        mark = ("ideal (<= %.1f s)" % TARGET_IDEAL_SECS if worst <= TARGET_IDEAL_SECS else
                "within average (mean <= %.1f s)" % TARGET_AVERAGE_SECS if mean <= TARGET_AVERAGE_SECS else
                "OVER (mean > %.1f s)" % TARGET_AVERAGE_SECS)
        print(f"{label:<28}{n:>9}{mean:>8.2f}{med:>10.2f}{worst:>8.2f}{tr:>12.2f}{margin:>14.2f}  {mark}")
    print(f"\nTargets: ideally <= {TARGET_IDEAL_SECS} s per check, mean <= {TARGET_AVERAGE_SECS} s; the {timeout:g} s "
          f"timeout is only the cut-off, not a target")
    if slow:
        print(f"OVER THE {TARGET_AVERAGE_SECS} s AVERAGE: {', '.join(slow)}")
    else:
        print(f"Every check's mean within {TARGET_AVERAGE_SECS} s")
    w.stop()
    os._exit(1 if slow or not rows else 0)
