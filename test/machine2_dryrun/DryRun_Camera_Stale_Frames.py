"""Dry run: no check on an old camera frame (2026-11-07, CHANGES_MACHINE2 section 84). Needs Redis on localhost:6379
and the camera server's Python packages (torch, ultralytics, mobile_sam importable; no model weights, no camera).

  S  camera/RTSPCam.getFrameAfter: a frame that arrived after the given time is returned with its arrival time;
     an older frame is not (waits at most the given time, then nothing); nothing while the camera is not available.
  P  TakePictures.processItem on a simulated camera:
       P1 live stream (a new frame every 50 ms): three different frames, each newer than the request and than the
          one before; no reason
       P2 stream stalled before the request (the last frame is old): no pictures, reason "camera stream stalled"
       P3 stream stalls after the first picture: reason names the second and third picture
       P4 camera not connected: reason "camera not connected"
  C  ConverterScalerAndProcessor.processItem with such a TakePictures result: NOT OK to the IOServer (request id
     echoed) and to the frontend, the reason in the "check" notice - and the vision check is NOT run on the picture.
     With pictures it is run as before.
  A  VisionCheckWorker: 3 timed-out checks in a row -> the reason says the checks are too slow on this PC; a check
     that answers in time resets the count.
  O  Configuration: plc.camera.answer.timeout.sec below the vision check timeout + 8 s is raised to that.

Run from the project root:  python test/machine2_dryrun/DryRun_Camera_Stale_Frames.py   (exit code 1 on failure)
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


class FakeStream:
    """Plays the RTSPCam frame thread: puts a new frame (numbered in pixel [0,0,0]) every 50 ms while running."""
    def __init__(self, cam):
        self.cam, self.n, self.running, self.stop = cam, 0, False, False
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        import numpy as np
        while not self.stop:
            if self.running:
                self.n += 1
                frame = np.zeros((72, 128, 3), np.uint8); frame[0, 0, 0] = self.n % 256
                self.cam.latestCamFrame = frame
                self.cam.latestCamFrameAndTime = (frame, time.monotonic())
            time.sleep(0.05)


def slowWorkerMain(conn):
    """Worker process for A: the real _workerMain, models check replaced, the hub check hangs 5 s."""
    import camera.CameraProcessorServer as cps
    import camera.VisionCheckWorker as vcw
    cps.verifyVisionDependencies = lambda loadModels=True: []
    vcw._warmUp = lambda cps_: None
    real = cps.assessPictures
    def slow(state, images):
        if getattr(state, "name", "") == "READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING":
            time.sleep(5)
        if getattr(state, "name", "") == "READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING":
            return True, images[0], images[0]
        return real(state, images)
    cps.assessPictures = slow
    vcw._workerMain(conn)


if __name__ == "__main__":
    import numpy as np
    from redis import Redis
    from statemachine.StateMachine import MachineState as MS
    from utils.RedisUtils import *
    from Configuration import CosThetaConfigurator as C
    import camera.CameraProcessorServer as cps
    from camera.RTSPCam import RTSPCam
    from camera.VisionCheckWorker import VisionCheckWorker, TIMEOUTS_IN_A_ROW_FOR_ALARM

    r = Redis("localhost", 6379)

    # ------------------------------------------------------------------------------------------------ S
    print("\n=== S. RTSPCam.getFrameAfter ===", flush=True)
    cam = object.__new__(RTSPCam)              # no connection: the frame thread is played by FakeStream
    cam.camAvailable, cam.connected = True, True
    cam.latestCamFrame, cam.latestCamFrameAndTime = None, (None, 0.0)
    stream = FakeStream(cam)
    stream.running = True; time.sleep(0.2)
    t0 = time.monotonic()
    got, f, at = cam.getFrameAfter(t0, 1.0)
    check("S1 a frame that arrived after the request is returned (within one frame interval)",
          got and f is not None and at > t0 and time.monotonic() - t0 < 0.2, f"{time.monotonic() - t0:.3f} s")
    check("S1 ... as a copy", got and f is not cam.latestCamFrameAndTime[0])
    stream.running = False; time.sleep(0.1)
    t0 = time.monotonic()
    got, f, at = cam.getFrameAfter(t0, 0.5)
    took = time.monotonic() - t0
    check("S2 stream stalled: the old frame is NOT returned; gives up after the wait", not got and f is None
          and 0.45 <= took < 0.8, f"{took:.2f} s")
    check("S2 ... while getLatestFrame() still returns the old frame (what the pictures were taken with before)",
          cam.getLatestFrame()[0] is True)
    stream.running = True; cam.camAvailable = False
    got, f, _ = cam.getFrameAfter(time.monotonic(), 0.3)
    check("S3 camera not available: nothing", not got and f is None)
    cam.camAvailable = True

    # ------------------------------------------------------------------------------------------------ P
    print("\n=== P. TakePictures ===", flush=True)
    tp = object.__new__(cps.TakePictures)
    tp.camera, tp.gapBetweenPictures, tp.debugOtherExceptions = cam, 0.1, True
    req = {timeKeyString: time.time(), actionKeyString: takePictureCommand,
           currentMachineStateKeyString: MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING, requestIdKeyString: "7-1"}
    stream.running = True; time.sleep(0.2)
    n0 = stream.n
    out = tp.processItem(dict(req))
    pics = [out[cps.FIRST_PICTURE], out[cps.SECOND_PICTURE], out[cps.THIRD_PICTURE]]
    nums = [int(p[0, 0, 0]) if p is not None else None for p in pics]
    check("P1 live stream: three pictures, three different frames, all newer than the request, no reason",
          all(p is not None for p in pics) and len(set(nums)) == 3 and all(x > n0 % 256 for x in nums)
          and cps.NO_PICTURE_REASON not in out and out[requestIdKeyString] == "7-1", f"frames {nums}, before {n0}")
    stream.running = False; time.sleep(0.1)
    C.getCameraFrameMaxWait = classmethod(lambda cls: 0.4)
    out = tp.processItem(dict(req))
    check("P2 stream stalled: reason 'camera stream stalled', no picture of the old frame",
          "camera stream stalled" in out.get(cps.NO_PICTURE_REASON, "") and out[cps.FIRST_PICTURE] is None
          and out[cps.SECOND_PICTURE] is None and out[cps.THIRD_PICTURE] is None, out.get(cps.NO_PICTURE_REASON))
    stream.running = True; time.sleep(0.2)
    stopper = threading.Timer(0.08, lambda: setattr(stream, "running", False)); stopper.start()
    out = tp.processItem(dict(req))
    reason = out.get(cps.NO_PICTURE_REASON, "")
    check("P3 stall after the first picture: the second and third named", out[cps.FIRST_PICTURE] is not None
          and "second" in reason and "third" in reason, reason)
    cam.connected = False
    out = tp.processItem(dict(req))
    check("P4 camera not connected: reason 'camera not connected'",
          out.get(cps.NO_PICTURE_REASON) == "camera not connected" and out[requestIdKeyString] == "7-1")
    cam.connected = True; stream.running = True

    # ------------------------------------------------------------------------------------------------ C
    print("\n=== C. ConverterScalerAndProcessor: no check without a (new) picture ===", flush=True)
    csp = object.__new__(cps.ConverterScalerAndProcessor)
    csp.redisConnection, csp.debugOtherExceptions = r, True
    assessed = []
    csp.runAssessment = lambda state, images: (assessed.append(state) or (True, images[0], images[0], None))
    for q in (camera2ioq, camera2feq): r.delete(q)
    r.delete(f"{FRONTEND_NOTICE_PREFIX}check")
    setActiveCameraRequest(r, "7-2")
    stalledItem = {timeKeyString: time.time(), cps.FIRST_PICTURE: None, cps.SECOND_PICTURE: None, cps.THIRD_PICTURE: None,
                   currentMachineStateKeyString: MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING,
                   requestIdKeyString: "7-2", cps.NO_PICTURE_REASON: "camera stream stalled: no new frame in 1 s (first picture)"}
    csp.processItem(stalledItem)
    state_, result_, rid_ = readDataInIOServerFromCameraServer(r, block=200, withRequestId=True)
    check("C1 stalled: NOT OK to the IOServer for the requested picture, request id echoed",
          result_ is False and state_ == MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING and rid_ == "7-2",
          f"{state_} {result_} {rid_}")
    t_, orig_, proc_, res_, st_ = readDataInFEServerFromCameraServer(r, block=200)
    check("C1 NOT OK to the frontend", res_ == notok, res_)
    check("C1 the reason is in the frontend's 'check' notice",
          b"camera stream stalled" in (r.get(f"{FRONTEND_NOTICE_PREFIX}check") or b""))
    check("C1 the vision check was NOT run", assessed == [], f"{assessed}")
    img = np.full((720, 1280, 3), 128, np.uint8)
    setActiveCameraRequest(r, "7-3")
    goodItem = dict(stalledItem); goodItem.pop(cps.NO_PICTURE_REASON)
    goodItem.update({cps.FIRST_PICTURE: img, cps.SECOND_PICTURE: img, cps.THIRD_PICTURE: img, requestIdKeyString: "7-3"})
    csp.processItem(goodItem)
    state_, result_, rid_ = readDataInIOServerFromCameraServer(r, block=200, withRequestId=True)
    check("C2 with pictures the check is run as before (OK from the stand-in check)",
          assessed == [MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING] and result_ is True and rid_ == "7-3",
          f"{assessed} {result_} {rid_}")
    noneItem = dict(goodItem); noneItem.update({cps.FIRST_PICTURE: None, cps.SECOND_PICTURE: None, cps.THIRD_PICTURE: None,
                                                requestIdKeyString: "7-4"})
    setActiveCameraRequest(r, "7-4"); assessed.clear()
    csp.processItem(noneItem)
    state_, result_, rid_ = readDataInIOServerFromCameraServer(r, block=200, withRequestId=True)
    check("C3 no pictures and no reason given: NOT OK ('no picture from the camera'), check not run",
          result_ is False and assessed == [] and b"no picture" in (r.get(f"{FRONTEND_NOTICE_PREFIX}check") or b""))
    stream.stop = True

    # ------------------------------------------------------------------------------------------------ A
    print("\n=== A. Vision checks timing out in a row ===", flush=True)
    w = VisionCheckWorker(target=slowWorkerMain)
    w.start()
    check("A0 worker ready", w.waitUntilReady(180), f"pid {w.pid}")
    reasons = []
    for i in range(TIMEOUTS_IN_A_ROW_FOR_ALARM):
        w.waitUntilReady(180)
        _, _, _, reason = w.assess(MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING, [img] * 6, 1.5)
        reasons.append(reason)
    check(f"A1 timeouts 1..{TIMEOUTS_IN_A_ROW_FOR_ALARM - 1}: 'check timed out' only",
          all(x == "check timed out after 1.5 s" for x in reasons[:-1]), f"{reasons}")
    check(f"A2 timeout {TIMEOUTS_IN_A_ROW_FOR_ALARM} in a row: the reason says the checks are too slow on this PC",
          "in a row" in (reasons[-1] or "") and "too slow" in (reasons[-1] or ""), reasons[-1])
    w.waitUntilReady(180)
    allok, _, _, reason = w.assess(MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING, [img] * 6, 5)
    check("A3 a check in time resets the count", allok is True and reason is None and w.timeoutsInARow == 0,
          f"{allok} {reason} {w.timeoutsInARow}")
    check("A3 ... its time in the worker and the transfer around it are recorded (logged per check)",
          w.lastCheckSecs is not None and w.lastTransferSecs is not None and 0 <= w.lastTransferSecs < 1.0,
          f"check {w.lastCheckSecs}, transfer {w.lastTransferSecs}")
    w.waitUntilReady(180)
    _, _, _, reason = w.assess(MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING, [img] * 6, 1.5)
    check("A4 ... the next timeout is the first of a new run", reason == "check timed out after 1.5 s", reason)
    w.stop()

    # ------------------------------------------------------------------------------------------------ O
    print("\n=== O. Camera answer timeout at least the vision check timeout + 8 s ===", flush=True)
    origVision, origGet = C.getVisionCheckTimeout, C.getValue
    C.getVisionCheckTimeout = classmethod(lambda cls: 12.0)
    check("O1 20 s with a 12 s check: 20 s", C.getPlcCameraAnswerTimeout() == 20.0, C.getPlcCameraAnswerTimeout())
    C.getVisionCheckTimeout = classmethod(lambda cls: 18.0)
    check("O2 20 s with an 18 s check: raised to 26 s", C.getPlcCameraAnswerTimeout() == 26.0, C.getPlcCameraAnswerTimeout())
    C.getVisionCheckTimeout = origVision

    failed = [n for n, ok_ in RESULTS if not ok_]
    print(f"\nRESULT {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed" + (f"; FAILED: {failed}" if failed else ""),
          flush=True)
    os._exit(1 if failed else 0)
