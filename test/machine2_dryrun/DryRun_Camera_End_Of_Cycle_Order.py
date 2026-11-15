"""Dry run: the IOServer's end of cycle is handled on the camera server's check thread, after the check in progress
(CHANGES 110). Before, MonitorGetPicQueue dropped the cycle's state at once, and a check still running then wrote the
old cycle's state back (and into the state file). Needs Redis on localhost:6379; no camera, no models (the check is
replaced by a 2 s stand-in that changes the cycle's state, as a real check does).

  E1 MonitorGetPicQueue reads the end of cycle from io2camera.q and passes it on (it no longer drops the state itself)
  E2 TakePictures accepts it and passes it on without taking pictures
  E3 a check running when the end of cycle arrives: the state is dropped after the check, not during it
  E4 the cycle's state is empty afterwards (QR code, model, pictures, bearing ring, knuckle frame)

Run from the project root:  python test/machine2_dryrun/DryRun_Camera_End_Of_Cycle_Order.py   (exit code 1 on failure)
"""
import os, pathlib, sys, threading, time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("COSTHETA_PLC_VENDOR", "siemens" if HERE.name.startswith("machine2") else "allenbradley")

RESULTS = []
def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition)))
    print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""), flush=True)

if __name__ == "__main__":
    import numpy as np
    from redis import Redis
    from statemachine.StateMachine import MachineState as MS
    from utils.RedisUtils import *
    import camera.CameraProcessorServer as cps

    r = Redis("localhost", 6379)
    r.delete(io2cameraq, camera2ioq, camera2feq)
    IP = cps.ImageProcessor
    IP.stateFile = None                                   # no state file in this run
    QR = "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$"
    img = np.full((720, 1280, 3), 128, np.uint8)

    events = []
    realEnd = IP.endOfCycle.__func__
    def recordedEnd(cls):
        events.append(("endOfCycle", time.monotonic(), threading.current_thread().name))
        realEnd(cls)
    IP.endOfCycle = classmethod(recordedEnd)

    # ------------------------------------------------------------------ E1, E2
    print("\n=== E1-E2. End of cycle passed down the queues ===", flush=True)
    IP.setCurrentComponentName(qrCodeValue=QR)
    class _Sink(threading.Thread):
        def addItem(self, item): self.item = item
    monitor = cps.MonitorGetPicQueue(name="TestMonitor", consumer=_Sink(), monitorRedisQueueForStopping=False)
    sendEndOfCycleFromIOServerToCameraServer(r, aborted=True)
    item = monitor.processItem(cps.MonitorGetPicQueue.monitorQCommand)
    check("E1 MonitorGetPicQueue passes the end of cycle on", cps.isEndOfCycleItem(item), item)
    check("E1 ... and does not drop the cycle's state itself", IP.currentComponentQRCodeValue == QR and not events,
          (IP.currentComponentQRCodeValue, events))
    # TakePictures without a camera: its processItem for an end-of-cycle item never touches the camera
    tp = cps.TakePictures.__new__(cps.TakePictures)
    accepted = []
    cps.GenericQueueProcessor.addItem = (lambda self, it, _orig=cps.GenericQueueProcessor.addItem:
                                         accepted.append(it) if self is tp else _orig(self, it))
    cps.TakePictures.addItem(tp, item)
    out = cps.TakePictures.processItem(tp, item)
    check("E2 TakePictures accepts the end of cycle and passes it on without pictures",
          accepted == [item] and out is item, (len(accepted), out is item))

    # ------------------------------------------------------------------ E3, E4
    print("\n=== E3-E4. End of cycle while a check is running ===", flush=True)
    converter = cps.ConverterScalerAndProcessor(name="TestConverter", monitorRedisQueueForStopping=False)
    def slowCheck(state, images):                          # a check that takes 2 s and changes the cycle's state
        events.append(("check start", time.monotonic(), threading.current_thread().name))
        time.sleep(2)
        IP.currentModelName = "DOST"
        IP.currentPictures["topBearingPicture"] = images[0]
        IP.bearingCentreAndRadii = {"center": (707, 351)}
        events.append(("check end", time.monotonic(), threading.current_thread().name))
        return True, images[0], images[0], None
    converter.runAssessment = slowCheck
    converter.start()
    pictures = {timeKeyString: time.time(), cps.FIRST_PICTURE: img, cps.SECOND_PICTURE: img, cps.THIRD_PICTURE: img,
                currentMachineStateKeyString: MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING, requestIdKeyString: "req-1"}
    converter.addItem(pictures)
    time.sleep(0.5)                                        # the check is running
    converter.addItem(item)                                # the end of cycle arrives
    time.sleep(3)
    names = [e[0] for e in events]
    times = {e[0]: e[1] for e in events}
    check("E3 the end of cycle waits for the check in progress (check start, check end, end of cycle)",
          names == ["check start", "check end", "endOfCycle"], names)
    check("E3 ... and runs on the check thread", events and events[-1][2] == converter.name, events[-1][2] if events else None)
    check("E4 the cycle's state is empty afterwards (QR code, model, pictures, bearing ring, knuckle frame)",
          IP.currentComponentQRCodeValue is None and IP.currentModelName is None
          and not any(v is not None for v in IP.currentPictures.values()) and not IP.bearingCentreAndRadii
          and IP.visionFrame is None,
          (IP.currentComponentQRCodeValue, IP.currentModelName, IP.bearingCentreAndRadii))
    converter.stopped = True
    try:
        converter.setStopped(True)
    except Exception:
        pass
    failed = [n for n, ok_ in RESULTS if not ok_]
    print(f"\nRESULT {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed" + (f"; FAILED: {failed}" if failed else ""), flush=True)
    os._exit(1 if failed else 0)
