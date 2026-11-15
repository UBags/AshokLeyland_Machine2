"""Dry run: a stop command left in Redis by an earlier run is ignored (CHANGES 111). The stop queue is read from its
start and never trimmed (every process must see the one stop), so a stop from an earlier run - MainProgram's shutdown,
a dry run - stayed there, and every later process on a Redis that had not been flushed stopped its log threads at once
and printed "Slave Console Logger stopped" at every log call. Needs Redis on localhost:6379.

  S1 a stop sent by another process before this one started: ignored (noted once), the console logger keeps running,
     no "Slave Console Logger stopped"
  S2 a stop sent while the process runs: the logger stops (as before), and says so once, not at every log call

Each case runs in its own process (the start time is per process).
Run from the project root:  python test/machine2_dryrun/DryRun_Stale_Stop_Command.py   (exit code 1 on failure)
"""
import os, pathlib, subprocess, sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
os.environ.setdefault("COSTHETA_PLC_VENDOR", "siemens" if HERE.name.startswith("machine2") else "allenbradley")

RESULTS = []
def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition)))
    print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""), flush=True)

SEND = ("import sys; sys.path.insert(0, %r); from redis import Redis; from utils.RedisUtils import sendStopCommand; "
        "sendStopCommand(Redis('localhost', 6379), aProducer='earlier run')" % str(ROOT))

CASE = r'''
import sys, os, time
sys.path.insert(0, %r)
from redis import Redis
from logutils.SlaveLoggers import SlaveConsoleLogger, logBoth
from logutils.Logger import Logger
from utils.RedisUtils import sendStopCommand
SlaveConsoleLogger.getInstance()
time.sleep(0.3)
if sys.argv[1] == "during":
    sendStopCommand(Redis("localhost", 6379), aProducer="this run")
time.sleep(6.5)                       # the logger thread checks the stop queue after at most 5 s of waiting
for i in range(5):
    logBoth("logInfo", "dryrun", "message %%d" %% i, Logger.GENERAL)
print("STOPPED", SlaveConsoleLogger.getInstance().stopped, flush=True)
os._exit(0)
''' % str(ROOT)

if __name__ == "__main__":
    from redis import Redis
    sys.path.insert(0, str(ROOT))
    from utils.RedisUtils import stopQ
    r = Redis("localhost", 6379)

    print("\n=== S1. Stop command from an earlier run ===", flush=True)
    r.delete(stopQ)
    subprocess.run([sys.executable, "-c", SEND], cwd=str(ROOT), check=True, capture_output=True)
    out = subprocess.run([sys.executable, "-c", CASE, "earlier"], cwd=str(ROOT), capture_output=True, text=True,
                         timeout=60).stdout
    check("S1 the console logger keeps running", "STOPPED False" in out, [l for l in out.splitlines() if "STOPPED" in l])
    check("S1 no 'Slave Console Logger stopped'", out.count("Slave Console Logger stopped") == 0,
          out.count("Slave Console Logger stopped"))
    check("S1 the old stop noted once", out.count("Stop command from an earlier run ignored") == 1,
          out.count("Stop command from an earlier run ignored"))

    print("\n=== S2. Stop command during the run ===", flush=True)
    r.delete(stopQ)
    out = subprocess.run([sys.executable, "-c", CASE, "during"], cwd=str(ROOT), capture_output=True, text=True,
                         timeout=60).stdout
    check("S2 the console logger stops", "STOPPED True" in out, [l for l in out.splitlines() if "STOPPED" in l])
    check("S2 'Slave Console Logger stopped' once for 5 log calls (was once per call)",
          out.count("Slave Console Logger stopped") == 1, out.count("Slave Console Logger stopped"))
    r.delete(stopQ)

    failed = [n for n, ok_ in RESULTS if not ok_]
    print(f"\nRESULT {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed" + (f"; FAILED: {failed}" if failed else ""),
          flush=True)
    os._exit(1 if failed else 0)
