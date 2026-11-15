"""
DryRun_Bearing_Ring_Guard - the nut / washer check's bearing-ring guard as it runs in production: in the camera server,
in a thread, while the vision check worker process runs the nut check (CHANGES 98).

The real VisionCheckWorker and its assess(); the worker process here plays the nut check by 1.2 s of Python-level work
(it holds the GIL, as the nut detector's Python parts do) and answers OK, so the guard alone decides. Checked:
  G1 nut + washer pictures: OK, and the check takes no longer than the worker (the guard ran during it);
  G2 washer-only pictures: the guard finds no bearing ring (the real check rejects them for the missing nut);
  G3 bearing pictures (no nut, no washer): NOT OK with "bearing ring visible ...", no longer than the worker;
  G4 the guard does not run for other states (top bearing picture in the top bearing state: OK);
  G5 guard switched off (<vendor>.vision.nutwasher.bearing.ring.guard=False): OK;
  G6 in the worker process the check itself leaves the guard to the camera server (GUARD_DONE_BY_CALLER);
  G7 / G8 the guard cannot decide (ring finder fault / no answer in time): NOT OK with the reason (2026-11-15);
  G9 the same inside the check (thread mode).
Pictures: nut_washer_images/, top_bearing_images/ next to this script. Settings: this machine's (folder name).
Needs Redis (the camera server modules connect at import); not the model weights.

Run from the project root:   python test/machine1_dryrun/DryRun_Bearing_Ring_Guard.py
"""
import os, pathlib, sys, time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["COSTHETA_PLC_VENDOR"] = "siemens" if HERE.name.startswith("machine2") else "allenbradley"
WORK_SECS = 1.2


def playedWorkerMain(conn):
    """The worker process's protocol (camera/VisionCheckWorker._workerMain); each check = WORK_SECS of Python work, OK.
    Also reports whether the real check would leave the guard to the camera server in a worker process."""
    import camera.VisionCheckWorker as V
    import camera.CameraProcessorServer as cps
    cps.CheckNutAndPlateWasher.GUARD_DONE_BY_CALLER = True       # as _workerMain does
    conn.send(("ready", []))
    while True:
        try:
            message = conn.recv()
        except (EOFError, OSError):
            return
        if not message or message[0] == "stop":
            return
        if message[0] == "ping":
            conn.send(("pong", message[1]))
            continue
        _, jobId, state, images, snapshot = message
        started = time.time(); x = 0
        while time.time() - started < WORK_SECS:
            x += sum(i * i for i in range(2000))
        conn.send(("result", jobId, True, images[0], images[0], {}, None, time.time() - started))


if __name__ == "__main__":
    import cv2
    import camera.VisionCheckWorker as V
    from camera.CameraProcessorServer import CheckNutAndPlateWasher as C
    from statemachine.StateMachine import MachineState as MS
    results = []

    def check(name, ok, detail=""):
        results.append(bool(ok))
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   [{detail}]"), flush=True)

    NUT, TOP = MS.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER, MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING
    # Machine 1 has no nut / bearing pictures in the repo: the Machine 2 ones are used (with Machine 1's limits)
    PICS = HERE if (HERE / "nut_washer_images").is_dir() else HERE.parent / "machine2_dryrun"
    nuts = sorted((PICS / "nut_washer_images").glob("*_nut_*.jpg"))
    washers = sorted((PICS / "nut_washer_images").glob("*_washer_*.jpg"))
    bearings = sorted((PICS / "top_bearing_images").glob("*.jpg"))
    print(f"guard {'on' if C.BEARING_RING_GUARD else 'OFF'}; {len(nuts)} nut, {len(washers)} washer-only, {len(bearings)} bearing pictures")
    w = V.VisionCheckWorker(target=playedWorkerMain); w.start()
    check("worker ready", w.waitUntilReady(120))
    w.assess(NUT, [cv2.imread(str(nuts[0]))] * 6, 12)
    slack = 0.25                                      # pickling and the pipe

    def run(path, state):
        img = cv2.imread(str(path)); t = time.time(); r = w.assess(state, [img] * 6, 12); return r, time.time() - t

    t1 = [(p.name,) + run(p, NUT) for p in nuts]
    check("G1 nut + washer: OK", all(r[0] for _, r, _ in t1), [n for n, r, _ in t1 if not r[0]])
    check(f"G1 nut + washer: no longer than the worker ({WORK_SECS} s + {slack} s)", max(d for *_, d in t1) <= WORK_SECS + slack,
          f"{max(d for *_, d in t1):.2f} s")
    t2 = [(p.name,) + run(p, NUT) for p in washers]
    check("G2 washer only: no bearing ring found by the guard", all(r[0] for _, r, _ in t2), [(n, r[3]) for n, r, _ in t2 if not r[0]])
    t3 = [(p.name,) + run(p, NUT) for p in bearings]
    check("G3 bearing pictures: NOT OK 'bearing ring visible'", all((not r[0]) and "bearing ring visible" in (r[3] or "") for _, r, _ in t3),
          [(n, r[0], r[3]) for n, r, _ in t3 if r[0] or "bearing ring visible" not in (r[3] or "")])
    check(f"G3 bearing pictures: no longer than the worker ({WORK_SECS} s + {slack} s)", max(d for *_, d in t3) <= WORK_SECS + slack,
          f"{max(d for *_, d in t3):.2f} s")
    r, _ = run(bearings[0], TOP)
    check("G4 the guard does not run for another state (top bearing picture, top bearing state)", r[0], r[3])
    C.BEARING_RING_GUARD = False
    r, _ = run(bearings[0], NUT)
    check("G5 guard switched off: the worker's verdict stands", r[0], r[3])
    C.BEARING_RING_GUARD = True
    # G7 / G8 (2026-11-15): a guard that cannot decide makes the check NOT OK (before, it was "not applied": OK)
    realWait = V.GUARD_MAX_WAIT_SECS
    from camera.CameraProcessorServer import CheckTopBearing as TB
    _find = TB.findRing
    TB.findRing = staticmethod(lambda img: (_ for _ in ()).throw(RuntimeError("simulated ring finder fault")))
    r, _ = run(nuts[0], NUT)
    check("G7 ring finder fails: NOT OK, reason 'bearing-ring guard: ring finder failed'", (not r[0]) and
          "ring finder failed" in (r[3] or ""), r[3])
    V.GUARD_MAX_WAIT_SECS = 0.2
    TB.findRing = staticmethod(lambda img: (time.sleep(WORK_SECS + 1.5), None)[1])
    r, _ = run(nuts[0], NUT)
    check("G8 guard gives no answer in time: NOT OK, reason 'bearing-ring guard: no answer'", (not r[0]) and
          "no answer" in (r[3] or ""), r[3])
    TB.findRing, V.GUARD_MAX_WAIT_SECS = _find, realWait
    time.sleep(WORK_SECS + 1.6)                       # let the slow guard thread end
    r, _ = run(nuts[0], NUT)
    check("G8 afterwards a nut + washer picture is OK again", r[0], r[3])
    # the guard inside the check (thread mode / dry-run windows): a failing ring finder -> NOT OK, without a nut search
    import camera.CheckNutAndPlateWasher as CN
    src = __import__("inspect").getsource(CN.CheckNutAndPlateWasher.checkNutAndPlateWasher)
    check("G9 the guard inside the check returns NOT OK when the ring finder fails",
          'Ring finder failed ({e}) - NOT OK' in src and "; guard not applied" not in src)
    w.stop()
    import camera.VisionCheckWorker as V2, inspect
    check("G6 the worker process leaves the guard to the camera server (_workerMain sets GUARD_DONE_BY_CALLER)",
          "GUARD_DONE_BY_CALLER = True" in inspect.getsource(V2._workerMain))
    print(f"times: nut + washer {min(d for *_, d in t1):.2f}-{max(d for *_, d in t1):.2f} s, bearings "
          f"{min(d for *_, d in t3):.2f}-{max(d for *_, d in t3):.2f} s (worker {WORK_SECS} s)")
    print(f"\nRESULT {sum(results)}/{len(results)} checks passed", flush=True)
    os._exit(0 if all(results) else 1)
