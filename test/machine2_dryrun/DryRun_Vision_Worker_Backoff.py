"""Dry run: the vision check worker's restart back-off (CHANGES 110). A worker process that dies while starting (before
it reports ready - e.g. a missing DLL on the plant PC) used to be started again at once, in a tight loop of process
spawns. Now each such restart waits 1, 2, 4 ... (at most 60) s; checks meanwhile are NOT OK at once ("vision check
worker starting"); a worker that reaches ready resets the pause. Needs Redis on localhost:6379 (for the log); no
models (stand-in worker processes).

  B1 a worker that always dies at start: few starts in 8 s (back-off), not a tight loop; the pauses grow
  B2 a check while a start is pending: NOT OK at once, no extra start
  B3 a worker that dies twice and then starts: ready, and the pause count is reset to 0

Run from the project root:  python test/machine2_dryrun/DryRun_Vision_Worker_Backoff.py   (exit code 1 on failure)
"""
import os, pathlib, sys, time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("COSTHETA_PLC_VENDOR", "siemens" if HERE.name.startswith("machine2") else "allenbradley")
COUNTER = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"costheta_backoff_{os.getpid() if __name__ == '__main__' else os.getppid()}.txt")


def dyingWorker(conn):
    """Dies before reporting ready."""
    os._exit(3)


def flakyWorker(conn):
    """Dies the first two times, then reports ready and waits."""
    path = os.environ["COSTHETA_BACKOFF_COUNTER"]
    n = int(open(path).read() or 0) if os.path.exists(path) else 0
    open(path, "w").write(str(n + 1))
    if n < 2:
        os._exit(3)
    conn.send(("ready", []))
    while True:
        time.sleep(1)


RESULTS = []
def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition)))
    print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""), flush=True)


if __name__ == "__main__":
    import numpy as np
    import camera.VisionCheckWorker as vcw
    import camera.CameraProcessorServer             # loaded already in the camera server (assess imports from it)
    from statemachine.StateMachine import MachineState as MS

    print("\n=== B1-B2. Worker that always dies at start ===", flush=True)
    w = vcw.VisionCheckWorker(target=dyingWorker)
    w.start()
    t0 = time.monotonic()
    time.sleep(8)
    starts8 = w.starts
    check("B1 a worker that always dies at start: few starts in 8 s (back-off), not a tight loop", 2 <= starts8 <= 6,
          f"{starts8} starts; failed starts in a row {w.failedStarts}; next pause {w.restartDelay():g} s")
    check("B1 ... the pause grows (1, 2, 4 ... s)", w.restartDelay() >= 4, f"{w.restartDelay():g} s")
    img = np.zeros((10, 10, 3), np.uint8)
    # wait for a moment when a start is pending (the process stopped, the timer running)
    deadline = time.monotonic() + 20
    while not w.restartPending and time.monotonic() < deadline:
        time.sleep(0.05)
    before = w.starts
    t1 = time.monotonic()
    allok, _, _, reason = w.assess(MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING, [img] * 6, 12)
    took = time.monotonic() - t1
    check("B2 a check while a start is pending: NOT OK at once, no extra start",
          w.restartPending is True or before == w.starts,
          f"allok {allok}, reason {reason!r}, {took:.3f} s, starts {before} -> {w.starts}")
    check("B2 ... answered within 0.5 s", allok is False and reason == "vision check worker starting" and took < 0.5,
          f"{took:.3f} s")
    w.restartPending = True                               # no more starts of this one
    w.stop()

    print("\n=== B3. Worker that dies twice, then starts ===", flush=True)
    os.environ["COSTHETA_BACKOFF_COUNTER"] = COUNTER
    if os.path.exists(COUNTER):
        os.remove(COUNTER)
    w2 = vcw.VisionCheckWorker(target=flakyWorker)
    w2.start()
    ready = w2.waitUntilReady(30)
    check("B3 ready after two failed starts (1 s and 2 s pauses)", ready and w2.starts == 3, f"starts {w2.starts}")
    check("B3 ... the pause count reset to 0", w2.failedStarts == 0 and w2.restartDelay() == 0, w2.failedStarts)
    w2.stop()
    if os.path.exists(COUNTER):
        os.remove(COUNTER)
    failed = [n for n, ok_ in RESULTS if not ok_]
    print(f"\nRESULT {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed" + (f"; FAILED: {failed}" if failed else ""), flush=True)
    os._exit(1 if failed else 0)
