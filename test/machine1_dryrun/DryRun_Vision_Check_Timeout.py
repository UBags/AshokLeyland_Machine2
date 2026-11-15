"""Dry run: vision check deadline, the vision check worker process, the knuckle retake QR code, request ids
(2026-11-06, CHANGES_MACHINE2 section 81). Needs Redis on localhost:6379 (and the camera server's Python packages:
torch, ultralytics, mobile_sam; the models/ weights are NOT needed - the worker's model check is replaced here).

  W  camera/VisionCheckWorker.py: the worker process starts and reports ready; a check's verdict and the state it
     changed come back; a check over the timeout is NOT OK ("check timed out after N s") within the timeout, the
     worker is stopped and a new one started (new pid); while it starts, a check is NOT OK at once ("vision check
     worker starting"); a stopped check changes nothing of the cycle's state; the worker process killed from outside
     -> NOT OK, restarted.
  T  the in-thread mode is gone (2026-11-15): the camera server always runs the checks in the worker process.
  K  the real camera pipeline (ConverterScalerAndProcessor.processItem) with the real CheckKnuckle on a Machine 2
     knuckle picture: QR code from the QR code server -> OK; a retake of the knuckle (no new QR code) -> OK again,
     at once (before 2026-11-06 the QR code was dropped at every knuckle check: the retake waited 5 s per picture and
     was NOT OK); the answers carry the IOServer's request id; end of cycle -> QR code dropped.
  R  a check that times out in the pipeline: NOT OK to the IOServer and the frontend with the request id, the
     picture marked with a red border (no text; the reason logged) and the "check" notice for the frontend.

Run from the project root:  python test/machine2_dryrun/DryRun_Vision_Check_Timeout.py   (exit code 1 on failure)
"""
import json, os, pathlib, sys, threading, time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
os.chdir(ROOT)

RESULTS = []
def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition)))
    print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""), flush=True)


def testWorkerMain(conn):
    """The worker process for the dry run: the real _workerMain, with the model check replaced (no weights here) and
    assessPictures delayed per state (COSTHETA_TEST_WORKER_DELAYS = {"STATE_NAME": seconds}); the delayed check also
    sets a marker in the cycle's state, to see whether a stopped check's changes are kept."""
    import camera.CameraProcessorServer as cps
    import camera.VisionCheckWorker as vcw
    cps.verifyVisionDependencies = lambda loadModels=True: []
    vcw._warmUp = lambda cps_: None
    delays = json.loads(os.environ.get("COSTHETA_TEST_WORKER_DELAYS", "{}"))
    real = cps.assessPictures

    def delayed(state, images):
        secs = delays.get(getattr(state, "name", str(state)), 0)
        if secs:
            cps.ImageProcessor.currentModelName = "SET-BY-DELAYED-CHECK"
            time.sleep(secs)
        if getattr(state, "name", "") == "READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING":
            cps.ImageProcessor.bearingCentreAndRadii = {"center": (707, 351), "from": "worker"}
            cps.ImageProcessor.currentPictures["topBearingPicture"] = images[0]
            return True, images[0], images[0]
        return real(state, images)
    cps.assessPictures = delayed
    vcw._workerMain(conn)


if __name__ == "__main__":
    import numpy as np, cv2
    from redis import Redis
    from statemachine.StateMachine import MachineState as MS
    from utils.RedisUtils import *
    from Configuration import CosThetaConfigurator as C
    import camera.CameraProcessorServer as cps
    from camera.VisionCheckWorker import VisionCheckWorker

    os.environ["COSTHETA_TEST_WORKER_DELAYS"] = json.dumps({"READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING": 6})
    IP = cps.ImageProcessor
    img = np.full((720, 1280, 3), 128, np.uint8)

    # ------------------------------------------------------------------------------------------------ W
    print("\n=== W. Vision check worker process ===", flush=True)
    w = VisionCheckWorker(target=testWorkerMain)
    w.start()
    check("W1 worker process started and ready", w.waitUntilReady(120), f"pid {w.pid}")
    IP.endOfCycle(); IP.setCurrentComponentName("7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$")
    t0 = time.monotonic()
    allok, proc, orig, reason = w.assess(MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING, [img] * 6, 12)
    check("W2 a check's verdict comes back (OK, no reason)", allok is True and reason is None and proc is not None,
          f"{time.monotonic() - t0:.2f} s")
    check("W2 the state the check changed is applied in the camera server (bearing ring, its picture)",
          (IP.bearingCentreAndRadii or {}).get("from") == "worker" and IP.currentPictures.get("topBearingPicture") is not None)
    before = IP.snapshotState(); pid0, starts0 = w.pid, w.starts
    t0 = time.monotonic()
    allok, proc, orig, reason = w.assess(MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING, [img] * 6, 2.0)
    took = time.monotonic() - t0
    check("W3 a check over the 2 s timeout -> NOT OK 'check timed out after 2 s', within the timeout (+ stop time)",
          allok is False and reason == "check timed out after 2 s" and took < 6.0, f"{took:.2f} s, {reason}")
    check("W3 the worker was stopped and a new one started", w.starts == starts0 + 1 and w.pid != pid0, f"{pid0} -> {w.pid}")
    check("W3 the stopped check changed nothing of the cycle's state", IP.currentModelName == before["currentModelName"]
          and (IP.bearingCentreAndRadii or {}).get("from") == "worker", IP.currentModelName)
    t0 = time.monotonic()
    allok, proc, orig, reason = w.assess(MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING, [img] * 6, 12)
    check("W4 while the new worker starts: NOT OK at once ('vision check worker starting')",
          allok is False and reason == "vision check worker starting" and time.monotonic() - t0 < 0.5, reason)
    check("W5 the new worker becomes ready", w.waitUntilReady(120))
    allok, proc, orig, reason = w.assess(MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING, [img] * 6, 12)
    check("W5 and checks are answered again", allok is True and reason is None)
    os.kill(w.pid, 9); time.sleep(0.5)
    allok, proc, orig, reason = w.assess(MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING, [img] * 6, 12)
    check("W6 worker process killed from outside -> NOT OK, a new one started", allok is False and reason is not None
          and w.waitUntilReady(120), reason)
    allok, *_ = w.assess(MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING, [img] * 6, 12)
    check("W6 checks answered again afterwards", allok is True)
    w.stop()

    # ------------------------------------------------------------------------------------------------ T
    print("\n=== T. No in-thread mode (process only) ===", flush=True)
    import inspect
    srcRun = inspect.getsource(cps.ConverterScalerAndProcessor.runAssessment)
    check("T1 runAssessment always uses the vision check worker process; no in-thread path",
          "getVisionCheckWorker().assess(" in srcRun and not hasattr(cps, "_assessInThread")
          and not hasattr(C, "getVisionCheckInWorkerProcess"))
    props = (ROOT / "ApplicationConfiguration.properties").read_text(encoding="utf-8")
    check("T1 no vision.check.in.worker.process key left in the properties file", "check.in.worker.process" not in props)
    realAssess = cps.assessPictures

    # ------------------------------------------------------------------------------------------------ K
    print("\n=== K. Knuckle check in the real pipeline: QR code kept for a retake ===", flush=True)
    r = Redis("localhost", 6379)
    for q in (camera2ioq, camera2feq, qrcode2cameraq, io2cameraq):
        r.delete(q)
    # The pipeline under test is the real one; only the worker process is stood in for by the same call in this process
    # (dry run: the models are loaded here) - camera/VisionCheckWorker.assess has its own tests (W above)
    import camera.VisionCheckWorker as VCW
    class InProcessWorker:
        def assess(self, state, images, timeout):
            allok, proc, orig = cps.assessPictures(state, images)
            return allok, proc, orig, None
    inProcess = InProcessWorker()
    VCW.getVisionCheckWorker = lambda: inProcess
    IP.fetchQRCodeInChecks = False
    conv = cps.ConverterScalerAndProcessor(name="dry run", consumer=None)     # not started: processItem called here
    QR = "8201206$400102VA1C$22881$15.10.2026$C5211702$MEK$40cR4-B$1$SPDL ASSY-KNURH$SNPL-MAT$"   # DOSTPLUS RHS
    picFile = sorted((HERE / "knuckle_images").glob("DOSTPLUS_RHS_*.jpg"))[0]     # this machine's pictures
    pic = cv2.imread(str(picFile))
    def item(state, rid):
        return {timeKeyString: time.time(), "firstPicture": pic, "secondPicture": pic, "thirdPicture": pic,
                currentMachineStateKeyString: state, requestIdKeyString: rid}
    def lastAnswer(q):
        m = r.xrevrange(q, count=1)
        return {k.decode(): (v.decode() if k != b"originalImage" and k != b"processedImage" else v) for k, v in m[0][1].items()} if m else {}
    IP.endOfCycle()
    sendComponentQRCodeFromQRCodeServerToCameraServer(r, QR)
    t0 = time.monotonic()
    conv.processItem(item(MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE, "7-1"))
    a = lastAnswer(camera2ioq)
    check("K1 new cycle: the QR code from the QR code server is used; knuckle OK", a.get("result") == ok
          and IP.currentComponentQRCodeValue == QR, f"{a.get('result')} {time.monotonic() - t0:.2f} s")
    check("K1 the answer to the IOServer carries the request id", a.get(requestIdKeyString) == "7-1")
    check("K1 the answer to the frontend carries the request id", lastAnswer(camera2feq).get(requestIdKeyString) == "7-1")
    t0 = time.monotonic()
    conv.processItem(item(MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE, "7-2"))
    took = time.monotonic() - t0
    a = lastAnswer(camera2ioq)
    check("K2 knuckle retake (no new QR code): the cycle's QR code is kept, OK again, no 5 s wait",
          a.get("result") == ok and a.get(requestIdKeyString) == "7-2" and took < 3.0, f"{a.get('result')} {took:.2f} s")
    IP.endOfCycle()
    check("K3 end of cycle drops the QR code", IP.currentComponentQRCodeValue is None)

    # ------------------------------------------------------------------------------------------------ R
    print("\n=== R. A check that times out in the pipeline ===", flush=True)
    C.getVisionCheckTimeout = classmethod(lambda cls: 1.0)
    def timedOut(state, images, timeout):      # as VisionCheckWorker.assess answers a check over the timeout
        time.sleep(timeout); return False, images[0], images[0], f"check timed out after {timeout:g} s"
    realWorkerAssess = inProcess.assess
    inProcess.assess = timedOut
    r.delete("feNotice:check")
    IP.setCurrentComponentName(QR)
    t0 = time.monotonic()
    conv.processItem(item(MS.READ_TAKE_PICTURE_FOR_CHECKING_CAP, "7-3"))
    a, f = lastAnswer(camera2ioq), lastAnswer(camera2feq)
    check("R1 NOT OK to the IOServer after the 1 s timeout, with the request id", a.get("result") == notok
          and a.get(requestIdKeyString) == "7-3" and time.monotonic() - t0 < 2.5, f"{time.monotonic() - t0:.2f} s")
    check("R1 NOT OK to the frontend with the reason", f.get("result") == notok and f.get(reasonKeyString) == "check timed out after 1 s")
    shown = cv2.imdecode(np.frombuffer(f.get("processedImage", b""), np.uint8), 1)
    # 2026-11-15: no text on the pictures for the frontend; the NOT OK picture has a red border, the reason is logged
    check("R1 the picture sent to the frontend has a red border and no text (centre band unchanged)",
          shown is not None and tuple(int(v) for v in shown[6, 640]) == (0, 0, 255)
          and tuple(int(v) for v in shown[714, 640]) == (0, 0, 255)
          and float(np.abs(shown[30:70, 20:400].astype(int)
                           - cv2.resize(pic, (1280, 720))[30:70, 20:400].astype(int)).mean()) < 2.0)
    check("R1 'Picture NOT OK: check timed out ...' notice for the frontend", b"timed out" in (r.get("feNotice:check") or b""))
    inProcess.assess = realWorkerAssess

    failed = [n for n, ok_ in RESULTS if not ok_]
    print(f"\nRESULT {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed" + (f"; FAILED: {failed}" if failed else ""), flush=True)
    os._exit(1 if failed else 0)
