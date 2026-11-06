"""Dry run: utils/ProcessSupervisor.py (MainProgram restarts a server process that dies; 2026-11-06, CHANGES_MACHINE2
section 81). No Redis or database needed: dummy processes stand in for the servers, started with "spawn" as on
Windows.

  S1 a process killed from outside is restarted (new pid), the pid list MainProgram uses at shutdown is updated
  S2 a process that exits by itself is restarted too
  S3 once a stop command is given, a process that dies is NOT restarted
  S4 after max.restarts.per.hour restarts within the hour, a server that keeps dying is left down (logged)
  S5 the other processes are not touched

Run from the project root:  python test/machine2_dryrun/DryRun_Process_Supervisor.py   (exit code 1 on failure)
"""
import multiprocessing, os, pathlib, signal, sys, time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))


def server(lifetime):
    """A dummy server: runs for `lifetime` s (0 = until killed)."""
    time.sleep(lifetime if lifetime else 3600)


if __name__ == "__main__":
    from utils.ProcessSupervisor import ProcessSupervisor
    ctx = multiprocessing.get_context("spawn")
    RESULTS, LOG = [], []
    def check(name, condition, detail=""):
        RESULTS.append((name, bool(condition)))
        print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""), flush=True)
    def factory(target, args):
        p = ctx.Process(target=target, args=args, daemon=True); p.start(); return p
    def wait_for(pred, timeout):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if pred(): return True
            time.sleep(0.05)
        return False
    STOP = {"on": False}
    pids = []
    sup = ProcessSupervisor(processFactory=factory, isStopping=lambda: STOP["on"], pidList=pids, checkInterval=0.3,
                            maxRestartsPerHour=3, log=lambda m: (LOG.append(m), print("LOG  " + m, flush=True)))
    sup.GRACE_SECS = 0.3
    procs = {n: factory(server, (life,)) for n, life in (("Camera server", 0), ("Frontend", 0), ("DB server", 0))}
    for n, p in procs.items():
        sup.add(n, server, (0,), p); pids.append(p.pid)
    sup.start()

    old = procs["Camera server"].pid
    os.kill(old, signal.SIGKILL)
    ok1 = wait_for(lambda: sup.process("Camera server").pid != old and sup.process("Camera server").is_alive(), 10)
    new = sup.process("Camera server").pid
    check("S1 a killed process is restarted (new pid)", ok1, f"{old} -> {new}")
    check("S1 the pid list for shutdown is updated", old not in pids and new in pids, str(pids))

    sup.add("QR code server", server, (0.5,), factory(server, (0.5,)))        # exits by itself after 0.5 s, every time
    check("S2 a process that exits by itself is restarted", wait_for(lambda: sup.restarts.get("QR code server", 0) >= 1, 10))
    check("S4 after 3 restarts within the hour it is left down (logged)",
          wait_for(lambda: any("QR code server" in m and "NOT restarted" in m for m in LOG), 20)
          and sup.restarts["QR code server"] == 3, str(sup.restarts))

    STOP["on"] = True
    fe = sup.process("Frontend"); os.kill(fe.pid, signal.SIGKILL)
    time.sleep(2.0)
    check("S3 after a stop command, a process that dies is NOT restarted", sup.process("Frontend") is fe
          and not fe.is_alive() and sup.restarts.get("Frontend", 0) == 0)
    check("S5 the other processes are not touched", sup.process("DB server").is_alive()
          and sup.restarts.get("DB server", 0) == 0)

    for p in multiprocessing.active_children():
        p.kill()
    failed = [n for n, ok_ in RESULTS if not ok_]
    print(f"\nRESULT {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed" + (f"; FAILED: {failed}" if failed else ""), flush=True)
    os._exit(1 if failed else 0)
