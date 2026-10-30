"""
Tests the CameraProcessorServer with the live camera through all 9 Machine 2 picture states - run from the project
root with `python -m test.Test_CameraServer_Live_Camera` (or `python test/Test_CameraServer_Live_Camera.py`).

Requires a running CameraProcessorServer (camera.CameraProcessorServer.startCameraServer, normally started by
MainProgram) connected to the camera, plus Redis. Alternatively pass --start-server to start it inside this process
(needs the camera and all camera/model dependencies). Do NOT run the IOServer, QRCodeServer or Frontend at the same
time: this program plays the IOServer (and the QRCodeServer for the component QR) and consumes the camera results
the IOServer and Frontend would otherwise read.

For each picture state, in Machine 2 order -
  knuckle, hub (with bottom bearing), top bearing, nut & plate washer, bunk for component press, no bunk,
  split pin & washer, cap, bunk for cap press -
it sends sendDataFromIOServerToCameraServer(<state>) on io2camera.q, then waits for the result on camera2io.q (what the
IOServer reads) and the original + processed images on camera2fe.q (what the Frontend reads), saves the images to the
output folder and finally prints a table. Before the knuckle picture the component QR code is sent on qrcode2camera.q
(as the QRCodeServer does after a scan), because the knuckle check needs the model/side from the QR code.

A state PASSES when the camera server answered on both queues for that same state with the same result; the OK /
NOT OK verdict of the inspection itself is shown (use --expect-ok to also require OK). Exit code 0 = all passed.

Options:
  --qr TEXT        component QR code sent before the knuckle picture (default: a DOST LHS QR code)
  --no-qr          do not send a component QR code
  --states N ...   only these picture states (MachineState names or numbers), still in Machine 2 order
  --timeout SEC    seconds to wait for each result (default 60)
  --pause SEC      pause between states, e.g. to change the part on the fixture (default 1)
  --output DIR     folder for the images (default ./camera_test_output/<timestamp>)
  --expect-ok      a NOT OK inspection result counts as a failure
  --start-server   start camera.CameraProcessorServer.startCameraServer(mode) in this process
  --mode MODE      mode for --start-server (Test or Production; default Test)
"""

import os
import sys
import time
import argparse
import threading
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import cv2
from redis import Redis

from Configuration import CosThetaConfigurator
from statemachine.StateMachine import MachineState
from utils.RedisUtils import (
    sendDataFromIOServerToCameraServer,
    sendComponentQRCodeFromQRCodeServerToCameraServer,
    readDataInIOServerFromCameraServer,
    readDataInFEServerFromCameraServer,
    io2cameraq, camera2ioq, camera2feq, qrcode2cameraq,
    ok,
)
from utils.CosThetaPrintUtils import printBoldBlue, printBoldGreen, printBoldRed, printBoldYellow

# A QR code that matches qrcode.partmapping.1 (DOST - LHS - 2.0T) in ApplicationConfiguration.properties
DEFAULT_QR_CODE = "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$"

# The 9 Machine 2 picture states, in cycle order
PICTURE_STATES = [
    MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE,
    MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING,
    MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING,
    MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER,
    MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS,
    MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK,
    MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER,
    MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP,
    MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS,
]


def parseStates(values):
    if not values:
        return list(PICTURE_STATES)
    wanted = set()
    for v in values:
        state = MachineState.getMachineStateFromString(v)
        if state not in PICTURE_STATES:
            raise SystemExit(f"'{v}' is not one of the 9 picture states: {[s.name for s in PICTURE_STATES]}")
        wanted.add(state)
    return [s for s in PICTURE_STATES if s in wanted]


def startCameraServerInProcess(mode: str) -> None:
    from camera.CameraProcessorServer import startCameraServer   # heavy import: camera + models
    threading.Thread(target=startCameraServer, kwargs=dict(mode=mode), name="CameraProcessorServer",
                     daemon=True).start()


def runState(r: Redis, state: MachineState, timeout: float, outputDir: str, index: int) -> dict:
    """Trigger one picture state and collect what the IOServer and the Frontend would receive."""
    row = dict(index=index, state=state, ioState=None, ioResult=None, feState=None, feResult=None,
               files=[], seconds=None, error="")
    t0 = time.time()
    if not sendDataFromIOServerToCameraServer(r, currentMachineState=state):
        row["error"] = "could not send command"
        return row
    gotIO = gotFE = False
    while time.time() - t0 < timeout and not (gotIO and gotFE):
        if not gotIO:
            ioState, ioOk = readDataInIOServerFromCameraServer(r, block=200)
            if ioState != MachineState.INVALID_STATE:
                gotIO = True
                row["ioState"], row["ioResult"] = ioState, ("OK" if ioOk else "NOT OK")
        if not gotFE:
            tm, original, processed, result, feState = readDataInFEServerFromCameraServer(r, block=200)
            if result is not None:
                gotFE = True
                row["feState"], row["feResult"] = feState, ("OK" if result == ok else "NOT OK")
                for kind, img in (("original", original), ("processed", processed)):
                    if img is not None:
                        path = os.path.join(outputDir, f"{index:02d}_{state.name}_{kind}.png")
                        if cv2.imwrite(path, img):
                            row["files"].append(path)
    row["seconds"] = round(time.time() - t0, 1)
    if not gotIO:
        row["error"] += "no camera2io result; "
    if not gotFE:
        row["error"] += "no camera2fe result; "
    return row


def evaluate(row: dict, expectOk: bool) -> bool:
    if row["error"]:
        return False
    consistent = (row["ioState"] == row["state"] and row["feState"] == row["state"]
                  and row["ioResult"] == row["feResult"])
    if not consistent:
        row["error"] = "state/result mismatch between camera2io and camera2fe"
        return False
    if expectOk and row["ioResult"] != "OK":
        row["error"] = "inspection NOT OK (--expect-ok)"
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="CameraProcessorServer live-camera test (Machine 2 picture states)")
    parser.add_argument("--qr", default=DEFAULT_QR_CODE)
    parser.add_argument("--no-qr", action="store_true")
    parser.add_argument("--states", nargs="*")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--pause", type=float, default=1.0)
    parser.add_argument("--output", default=None)
    parser.add_argument("--expect-ok", action="store_true")
    parser.add_argument("--start-server", action="store_true")
    parser.add_argument("--mode", default="Test")
    args = parser.parse_args()

    states = parseStates(args.states)
    outputDir = args.output or os.path.join(os.getcwd(), "camera_test_output", datetime.now().strftime("%Y-%m-%d-%H-%M-%S"))
    os.makedirs(outputDir, exist_ok=True)

    cfg = CosThetaConfigurator.getInstance()
    r = Redis(cfg.getRedisHost(), cfg.getRedisPort(), retry_on_timeout=True)
    r.ping()
    # Start from empty queues (no flushdb: other servers' queues are left alone)
    for q in (io2cameraq, camera2ioq, camera2feq, qrcode2cameraq):
        r.xtrim(q, 0)

    if args.start_server:
        printBoldBlue(f"Starting CameraProcessorServer in-process (mode={args.mode})...")
        startCameraServerInProcess(args.mode)
        time.sleep(10)

    printBoldBlue(f"Testing {len(states)} picture state(s); images go to {outputDir}")
    rows = []
    for i, state in enumerate(states, start=1):
        if state == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE and not args.no_qr:
            sendComponentQRCodeFromQRCodeServerToCameraServer(r, componentQRCode=args.qr, aProducer="Test_CameraServer")
            printBoldBlue(f"Sent component QR code on {qrcode2cameraq}: {args.qr}")
        printBoldBlue(f"[{i}/{len(states)}] {state.name}: sending take-picture command")
        row = runState(r, state, args.timeout, outputDir, i)
        row["passed"] = evaluate(row, args.expect_ok)
        (printBoldGreen if row["passed"] else printBoldRed)(
            f"    camera2io: {row['ioResult']} ({row['ioState'].name if row['ioState'] else '-'}), "
            f"camera2fe: {row['feResult']} ({row['feState'].name if row['feState'] else '-'}), "
            f"{len(row['files'])} image(s), {row['seconds']} s {row['error']}")
        rows.append(row)
        if i < len(states):
            time.sleep(args.pause)

    # Pass / fail table
    print()
    header = f"{'#':>2}  {'Picture state':58s} {'IO':7s} {'FE':7s} {'Imgs':>4} {'Secs':>6}  Verdict"
    printBoldBlue(header)
    printBoldBlue("-" * len(header))
    for row in rows:
        line = (f"{row['index']:>2}  {row['state'].name:58s} {row['ioResult'] or '-':7s} {row['feResult'] or '-':7s} "
                f"{len(row['files']):>4} {row['seconds']:>6}  {'PASS' if row['passed'] else 'FAIL ' + row['error']}")
        (printBoldGreen if row["passed"] else printBoldRed)(line)
    passed = sum(r_["passed"] for r_ in rows)
    (printBoldGreen if passed == len(rows) else printBoldYellow)(f"{passed}/{len(rows)} picture states passed; "
                                                                f"images in {outputDir}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)      # do not wait on logger / camera-server threads
