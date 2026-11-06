"""Dry run: a server's heartbeat says DEAD when its WORK threads have died or are stuck (2026-11-07, CHANGES_MACHINE2
section 85). Before, each heartbeat was sent by a thread of its own and said ALIVE whatever the work threads did.
Needs Redis on localhost:6379 and PostgreSQL (DB part); the camera server's packages (torch, mobile_sam importable).

  U  utils/WorkerHealth.WorkerWatch: a running thread that goes round its loop is fine; a thread that ended, or one
     whose loop has not come round for longer than the limit, is a problem (with the reason); a thread replaced
     (callable) is followed; a change is logged once.
  G  GenericQueueProcessor: lastLoopAt advances while it waits for items; stops advancing while processItem is stuck.
  C  Camera server heartbeat (TakePictures thread): ALIVE; DEAD while the assessment thread is stuck; ALIVE again.
  Q  QR code server heartbeat: ALIVE; DEAD while the scanner-reading thread has ended; the reading loop's waiting for
     a scan counts as going round.
  D  DB server heartbeat: ALIVE; DEAD while the record-saving loop is stuck; ALIVE again.
  (The IOServer's read / write loops: fault scenario L; the frontend's GUI thread: frontend dry run F11.)

Run from the project root:  python test/machine2_dryrun/DryRun_Work_Thread_Health.py   (exit code 1 on failure)
"""
import os, pathlib, sys, threading, time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
os.chdir(ROOT)

RESULTS = []
def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition)))
    print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""), flush=True)

def wait_for(pred, timeout=8.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred(): return True
        time.sleep(0.05)
    return False


if __name__ == "__main__":
    from redis import Redis
    from utils.RedisUtils import *
    from Configuration import CosThetaConfigurator as C
    from utils.WorkerHealth import WorkerWatch
    from processors.GenericQueueProcessor import GenericQueueProcessor

    LIMIT = 2.0
    C.getWorkLoopMaxSilence = classmethod(lambda cls: LIMIT)
    r = Redis("localhost", 6379)

    def latest(q):
        last = r.xrevrange(q, count=1)
        if not last:
            return None
        v = last[0][1].get(connectionStatusKeyString.encode())
        return v.decode() if v else None

    # ------------------------------------------------------------------------------------------------ U
    print("\n=== U. WorkerWatch ===", flush=True)
    state = {"tick": time.monotonic(), "run": True}
    def looping():
        while state["run"]:
            state["tick"] = time.monotonic(); time.sleep(0.1)
    t1 = threading.Thread(target=looping, daemon=True); t1.start()
    w = WorkerWatch("test")
    w.add("looping", t1, lastTick=lambda: state["tick"])
    check("U1 a thread going round its loop: no problem", w.problems() == [], w.problems())
    frozen = time.monotonic()
    w2 = WorkerWatch("test2"); w2.add("stuck", t1, lastTick=lambda: frozen)
    time.sleep(LIMIT + 0.3)
    check("U2 a loop that has not come round for longer than the limit: problem with the time",
          len(w2.problems()) == 1 and "no loop for" in w2.problems()[0], w2.problems())
    state["run"] = False; t1.join(1)
    check("U3 a thread that ended: 'thread not running'", w.problems() == ["looping: thread not running"], w.problems())
    holder = {"t": None}
    w3 = WorkerWatch("test3"); w3.add("replaced", lambda: holder["t"])
    check("U4 no thread yet: a problem", w3.problems() == ["replaced: thread not running"])
    holder["t"] = threading.Thread(target=lambda: time.sleep(3), daemon=True); holder["t"].start()
    check("U4 ... the thread that replaced it is followed", w3.problems() == [])
    logs = []
    w2.problemsLogged(log=logs.append); w2.problemsLogged(log=logs.append)
    check("U5 a problem is logged once, not at every heartbeat", len(logs) == 1, logs)

    # ------------------------------------------------------------------------------------------------ G
    print("\n=== G. GenericQueueProcessor loop tick ===", flush=True)
    class Proc(GenericQueueProcessor):
        def __init__(self):
            super().__init__(name="StuckTest", consumer=None, timeout=0.2, sleepTime=0.0)
            self.block = threading.Event(); self.release = threading.Event()
        def preWorkLoop(self): return
        def postWorkLoop(self): return
        def processItem(self, item):
            self.block.set(); self.release.wait(30)
    p = Proc(); p.start()
    a = p.lastLoopAt; time.sleep(0.6)
    check("G1 lastLoopAt advances while it waits for items", p.lastLoopAt > a)
    p.addItem({"x": 1}); p.block.wait(3)
    b = p.lastLoopAt; time.sleep(0.6)
    check("G2 ... and stops while processItem is stuck", p.lastLoopAt == b)

    # ------------------------------------------------------------------------------------------------ C
    print("\n=== C. Camera server heartbeat ===", flush=True)
    import camera.CameraProcessorServer as cps
    class FakeCam: connected = True
    tp = object.__new__(cps.TakePictures)
    tp.camera, tp.redisConnectionForHeartbeat, tp.stopped = FakeCam(), r, False
    C.getCameraConnectionStatusSleepInterval = classmethod(lambda cls: 0.2)
    healthy = Proc(); healthy.start()
    cps.cameraWorkers.add("healthy", healthy)
    r.delete(camera2hbq)
    tp.launchThreadForHeartbeat()
    check("C1 work threads going round: ALIVE", wait_for(lambda: latest(camera2hbq) == ALIVE, 3), latest(camera2hbq))
    cps.cameraWorkers.add("assessment (stuck)", p)          # p is stuck in processItem since G2
    check("C2 assessment thread stuck longer than the limit: DEAD",
          wait_for(lambda: latest(camera2hbq) == DEAD, LIMIT + 3), latest(camera2hbq))
    p.release.set()
    check("C3 it goes round again: ALIVE", wait_for(lambda: latest(camera2hbq) == ALIVE, 3), latest(camera2hbq))
    tp.stopped = True

    # ------------------------------------------------------------------------------------------------ Q
    print("\n=== Q. QR code server heartbeat ===", flush=True)
    from costhetaio.qrcodescanner.QRCodeScanningServer import QRCodeScanningServer
    qs = object.__new__(QRCodeScanningServer)
    qs.heartbeatLock, qs.heartbeatRedisConnected, qs.heartbeatRedisConnection = threading.Lock(), True, r
    qs.connected, qs.stopped = True, False
    qs.workers = WorkerWatch("QR code server")
    reader = Proc(); reader.start()
    qs.workers.add("reading the scanner", reader)
    C.getQRCodeConnectionStatusSleepInterval = classmethod(lambda cls: 0.2)
    r.delete(qrcode2hbq)
    threading.Thread(target=qs.communicateHeartbeat, daemon=True).start()
    check("Q1 scanner connected, threads running: ALIVE", wait_for(lambda: latest(qrcode2hbq) == ALIVE, 3))
    reader.stop(); reader.join(3)
    check("Q2 the scanner-reading thread has ended: DEAD", wait_for(lambda: latest(qrcode2hbq) == DEAD, 3),
          latest(qrcode2hbq))
    qs.stopped = True
    src = (ROOT / "costhetaio/qrcodescanner/QRCodeScanningServer.py").read_text(encoding="utf-8")
    i = src.index("while not self.abortRead and not self.getStopped():")
    check("Q3 waiting for a scan counts as going round (lastLoopAt set in the reading loop)",
          "self.lastLoopAt = time.monotonic()" in src[i:i + 200])

    # ------------------------------------------------------------------------------------------------ D
    print("\n=== D. DB server heartbeat ===", flush=True)
    import persistence.Persistence as P
    P.setDatabaseName("Test")
    from persistence.DBServer import DataPersistence
    dp = object.__new__(DataPersistence)
    dp.stopped, dp.stopRequested, dp.heartbeatRedisConnected, dp.heartbeatRedisConnection = False, False, True, r
    dp.lastWorkLoopAt = time.monotonic()
    dp.workers = WorkerWatch("DB server")
    work = threading.Thread(target=lambda: time.sleep(60), daemon=True); work.start()
    dp.workers.add("saving records", lambda: work, lastTick=lambda: dp.lastWorkLoopAt)
    C.getDatabaseConnectionStatusSleepInterval = classmethod(lambda cls: 0.3)
    r.delete(db2hbq)
    ticking = {"on": True}
    def tick():
        while not dp.stopped:
            if ticking["on"]: dp.lastWorkLoopAt = time.monotonic()
            time.sleep(0.1)
    threading.Thread(target=tick, daemon=True).start()
    threading.Thread(target=dp.sendHeartbeat, daemon=True).start()
    check("D1 database reachable, saving loop going round: ALIVE", wait_for(lambda: latest(db2hbq) == ALIVE, 5),
          latest(db2hbq))
    ticking["on"] = False
    check("D2 saving loop stuck longer than the limit: DEAD", wait_for(lambda: latest(db2hbq) == DEAD, LIMIT + 4),
          latest(db2hbq))
    ticking["on"] = True
    check("D3 going round again: ALIVE", wait_for(lambda: latest(db2hbq) == ALIVE, 4), latest(db2hbq))
    dp.stopped = True

    failed = [n for n, ok_ in RESULTS if not ok_]
    print(f"\nRESULT {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed" + (f"; FAILED: {failed}" if failed else ""),
          flush=True)
    os._exit(1 if failed else 0)
