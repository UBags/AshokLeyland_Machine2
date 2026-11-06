"""
DryRun_Heartbeat_And_Startup - heartbeat reading (readAllHeartbeatsInHeartbeatServer), the heartbeat server loop,
and the MainProgram start-up fix (Redis no longer flushed when a server process re-imports MainProgram).

Needs Redis. No PLC, camera or GUI. Run from the project root:
    python test/machine2_dryrun/DryRun_Heartbeat_And_Startup.py
"""
import os, pathlib, subprocess, sys, threading, time, types

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
if sys.platform != 'win32':
    sys.modules['winsound'] = types.SimpleNamespace(PlaySound=lambda *a, **k: None, SND_FILENAME=0, SND_ASYNC=0,
                                                    Beep=lambda *a: None)

from Configuration import CosThetaConfigurator as C
from redis import Redis
from utils import RedisUtils as RU

r = Redis(C.getInstance().getRedisHost(), C.getInstance().getRedisPort())
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   [{detail}]"), flush=True)


QUEUES = {'io': RU.io2hbq, 'qrcode': RU.qrcode2hbq, 'camera': RU.camera2hbq, 'db': RU.db2hbq, 'fe': RU.fe2hbq}
SENDERS = {'io': RU.sendHeartbeatFromIOServerToHeartbeatServer,
           'qrcode': RU.sendHeartbeatFromQRCodeServerToHeartbeatServer,
           'camera': RU.sendHeartbeatFromCameraServerToHeartbeatServer,
           'db': RU.sendHeartbeatFromDBServerToHeartbeatServer,
           'fe': RU.sendHeartbeatFromFEServerToHeartbeatServer}


def clear():
    for q in list(QUEUES.values()) + [RU.hb2feq, RU.hb2ioq]:
        r.delete(q)


def send(names, status=RU.ALIVE):
    for n in names:
        SENDERS[n](r, status)


def timed_read(block=500):
    t0 = time.monotonic()
    res = RU.readAllHeartbeatsInHeartbeatServer(r, block=block)
    return res, time.monotonic() - t0


# ---------------------------------------------------------------- H1-H6 readAllHeartbeatsInHeartbeatServer
print("\n=== H1-H6 readAllHeartbeatsInHeartbeatServer ===", flush=True)
clear(); send(QUEUES)
res, dt = timed_read()
check("H1 all five servers sent ALIVE: all True, returned at once", all(res.values()) and dt < 0.2, (res, round(dt, 3)))
check("H1 ... and the heartbeats read are deleted", all(r.xlen(q) == 0 for q in QUEUES.values()),
      {n: r.xlen(q) for n, q in QUEUES.items()})

clear(); send(['io', 'fe'])
res, dt = timed_read()
check("H2 only IO and FE sent: those True, the other three False, returned at once (no 0.5 s wait per dead server)",
      res == {'io': True, 'qrcode': False, 'camera': False, 'db': False, 'fe': True} and dt < 0.2, (res, round(dt, 3)))

clear()
res, dt = timed_read(block=400)
check("H3 nobody sent: all False after waiting the block time (0.4 s)", not any(res.values()) and 0.35 < dt < 1.0,
      (res, round(dt, 3)))

clear(); send(['camera'], RU.DEAD)
res, _ = timed_read()
check("H4 a DEAD heartbeat counts as not alive", res['camera'] is False, res)

clear(); send(['db'], RU.DEAD); send(['db'], RU.ALIVE)
res, _ = timed_read()
check("H5 several heartbeats queued: only the latest counts (DEAD then ALIVE -> True)", res['db'] is True, res)

clear()
def late():
    time.sleep(0.15); send(['qrcode'])
threading.Thread(target=late).start()
res, dt = timed_read(block=1000)
check("H6 nobody sent yet: returns as soon as the first heartbeat arrives", res['qrcode'] is True and dt < 0.6,
      (res, round(dt, 3)))
check("H6 the old reads (5 x 0.5 s when servers are down) would have taken 2.5 s; this read took", dt < 0.6, round(dt, 3))

# ---------------------------------------------------------------- H7-H8 heartbeat server loop
print("\n=== H7-H8 heartbeat server loop ===", flush=True)
from monitorAllConnections.HeartbeatAndAlarmServer import HeartbeatAndAlarmServer
MIN = HeartbeatAndAlarmServer.MINIMUM_CONSECUTIVE_DOWNS_NEEDED_FOR_ALARM
INTERVAL = C.getInstance().getCombinedHeartbeatConnectionStatusSleepInterval()
clear()
stopSending = threading.Event()
sendCamera = threading.Event(); sendCamera.set()


def servers():
    while not stopSending.is_set():
        send([n for n in QUEUES if n != 'camera' or sendCamera.is_set()])
        time.sleep(0.5)


threading.Thread(target=servers, daemon=True).start()
C.getRedisHealthCheckInterval = classmethod(lambda cls: 1.0)       # R: every 1 s here (60 s in production)
hb = HeartbeatAndAlarmServer()
hb.start()


def latest(q):
    entries = r.xrevrange(q, count=1)
    if not entries:
        return {}
    return {k.decode(): v.decode() for k, v in entries[0][1].items()}


def wait_for(pred, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.2)
    return pred()


ok = wait_for(lambda: latest(RU.hb2ioq).get(RU.connectionStatusKeyString) == RU.ALIVE, 3 * INTERVAL + 2)
check("H7 all servers sending: combined status to the IOServer is ALIVE", ok, latest(RU.hb2ioq))
check("H7 ... and every server ALIVE to the frontend",
      all(latest(RU.hb2feq).get(k) == RU.ALIVE for k in (RU.cameraServerStatusKeyString, RU.qrCodeServerStatusKeyString,
                                                         RU.ioServerStatusKeyString, RU.dbServerStatusKeyString)),
      latest(RU.hb2feq))
sendCamera.clear()
ok = wait_for(lambda: latest(RU.hb2ioq).get(RU.connectionStatusKeyString) == RU.DEAD, (MIN + 2) * INTERVAL + 3)
check(f"H8 camera stops: combined DEAD after {MIN} missed checks", ok, latest(RU.hb2ioq))
check("H8 ... and the camera DEAD to the frontend, the others still ALIVE",
      latest(RU.hb2feq).get(RU.cameraServerStatusKeyString) == RU.DEAD
      and latest(RU.hb2feq).get(RU.ioServerStatusKeyString) == RU.ALIVE, latest(RU.hb2feq))
sendCamera.set()
ok = wait_for(lambda: latest(RU.hb2ioq).get(RU.connectionStatusKeyString) == RU.ALIVE, 2 * INTERVAL + 3)
check("H8 camera back: combined ALIVE again", ok, latest(RU.hb2ioq))
stopSending.set()

# ---------------------------------------------------------------- R Redis health check (2026-11-06, section 83)
print("\n=== R. Redis health check (write to a stream nothing else uses, read back) ===", flush=True)
healthy, why = RU.checkRedisHealth(r)
check("R1 checkRedisHealth on a working Redis: OK, and the check entry is not left behind",
      healthy and r.xlen(RU.REDIS_HEALTH_CHECK_STREAM) == 0, why)
check("R2 the heartbeat server reports Redis ALIVE to the IOServer and the frontend",
      wait_for(lambda: latest(RU.hb2ioq).get(RU.redisServerStatusKeyString) == RU.ALIVE
               and latest(RU.hb2feq).get(RU.redisServerStatusKeyString) == RU.ALIVE, 5) and hb.redisChecks >= 1,
      (latest(RU.hb2ioq), hb.redisChecks))
r.config_set("maxmemory-policy", "noeviction"); r.config_set("maxmemory", "1")    # Redis answers, but stores nothing
try:
    healthy, why = RU.checkRedisHealth(r)
    check("R3 Redis answering PING but refusing writes: the check fails (PING alone would pass)",
          (not healthy) and "memory" in why.lower() and r.ping(), why)
    check("R3 the heartbeat server sees it within its retry interval", wait_for(lambda: hb.redisHealthy is False, 5),
          hb.redisHealthReason)
finally:
    r.config_set("maxmemory", "0")
check("R4 Redis storing again: the heartbeat server reports it healthy again",
      wait_for(lambda: hb.redisHealthy is True, 12) and
      wait_for(lambda: latest(RU.hb2ioq).get(RU.redisServerStatusKeyString) == RU.ALIVE, 5), latest(RU.hb2ioq))
src = (ROOT / "MainProgram.py").read_text(encoding="utf-8")
check("R5 MainProgram checks Redis (write / read back) at start-up, after clearing it, before any server starts",
      "checkRedisHealth(redisConnection)" in src.split("def main()")[1].split("Process(target=startTheLoggingServer")[0])
hb.stopped = True

# ---------------------------------------------------------------- H9 MainProgram import does not flush Redis
print("\n=== H9 MainProgram start-up ===", flush=True)
r.set("dryrun:H9:marker", "kept")
src = (ROOT / "MainProgram.py").read_text(encoding="utf-8")
moduleLevel = src.split("\ndef main():")[0]
check("H9 no flushdb / clearQueues in MainProgram's module-level code (re-run by every spawned server process)",
      "redisConnection.flushdb()" not in moduleLevel and "\nclearQueues(" not in moduleLevel
      and "    clearQueues(redisConnection)" not in moduleLevel.split("def startTheLoggingServer")[0])
mainBody = src.split("\ndef main():")[1]
check("H9 main() still clears Redis once, before any server is started",
      mainBody.index("clearQueues(redisConnection)") < mainBody.index("Process(target="))
probe = subprocess.run([sys.executable, "-c", "import MainProgram"], cwd=str(ROOT), capture_output=True, text=True,
                       timeout=120, env={**os.environ, "PYTHONPATH": str(ROOT), "QT_QPA_PLATFORM": "offscreen"})
check("H9 importing MainProgram (what a spawned server process does) leaves Redis intact",
      r.get("dryrun:H9:marker") == b"kept", (probe.returncode, probe.stderr[-300:]))
r.delete("dryrun:H9:marker")

print(f"\nRESULT {sum(results)}/{len(results)} checks passed", flush=True)
os._exit(0 if all(results) else 1)
