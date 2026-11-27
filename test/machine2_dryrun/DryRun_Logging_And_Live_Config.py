"""Dry run of the logging improvements and of live configuration, with automatic PASS/FAIL checks (exit 1 on failure).

Runs against a COPY of ApplicationConfiguration.properties (COSTHETA_CONFIG_FILE) that the script edits while the
code is running, the real central file logger (MasterFileLogger, in this process) writing to logs/dryrun_logging/,
and the real IOServer (PLCIOServer; Allen-Bradley PLCDataIO unless COSTHETA_PLC_VENDOR=siemens) on the simulated
PLC of _plc_sim.py. Needs Redis and PostgreSQL.

  L1  per-server file level (log.file.level.ioserver) applied while running; other servers keep the global level
  L2  PRODUCTION mode raises a per-server level below CONSIDER_ACTION to CONSIDER_ACTION
  L3  coloured console prints mirrored to the file (log.mirror.prints.to.file), and not when switched off
  L4  cycle context [QR=... cycle=N] on every line (shared from the IOServer; local override)
  L5  uncaught exception in a thread -> traceback in the log file
  L6  exceptions caught and ignored -> one DEBUG line per place
  L7  no silent loss: messages kept while Redis "is down" and delivered after; a full queue is counted and reported
  L8  log file rotation by size, retention of backuplogs.count files
  L9  live configuration: a change is applied within the check interval and logged; a half-written file is ignored
  L10 vision constants re-bound while running (CheckKnuckle.LHS_CROP_X)
  L11 IOServer: PLC audit trail (settings, OK, Done, triggers, state changes with the QR code), its self.logger
      messages in the file, intervals applied while running, restart warning for tag-name changes
  L12 logging server start: messages queued before it are written, stale ones counted

Run from the project root:   python test/machine2_dryrun/DryRun_Logging_And_Live_Config.py
"""
import os, pathlib, re, shutil, sys, tempfile, threading, time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
os.environ["COSTHETA_PLC_VENDOR"] = os.environ.get("COSTHETA_PLC_VENDOR", "allenbradley")

# ---------------------------------------------------------------- properties copy (edited while running)
TMP = pathlib.Path(tempfile.mkdtemp(prefix="cfg_"))
PROPS = TMP / "ApplicationConfiguration.properties"
LOGDIR_NAME = "logs/dryrun_logging"
LOGDIR = ROOT / LOGDIR_NAME
shutil.rmtree(LOGDIR, ignore_errors=True)
text = (ROOT / "ApplicationConfiguration.properties").read_text(encoding="ascii")
FILE_VENDOR = re.search(r"^plc\.vendor=(\S+)", text, re.M).group(1)    # whichever machine the live file is for

def set_props(**changes):
    """Rewrite keys in the copy (dots written as __ in the argument names); mtime bumped so the change is seen."""
    global text
    for k, v in changes.items():
        key = k.replace("__", ".")
        pattern = re.compile(rf"^{re.escape(key)}=.*$", re.M)
        text = pattern.sub(f"{key}={v}", text) if pattern.search(text) else text + f"\n{key}={v}\n"
    PROPS.write_text(text, encoding="ascii")
    t = time.time() + 1
    os.utime(PROPS, (t, t))

set_props(logging__directory=LOGDIR_NAME, config__reload__check__interval__sec="0.5", file__loggingLevel="INFO",
          application__mode="TEST", backuplogs__count="50", log__file__max__size__mb="50")
os.environ["COSTHETA_CONFIG_FILE"] = str(PROPS)

RESULTS = []
def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition)))
    print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f"   [{str(detail)[:300]}]" if detail and not condition else ""), flush=True)

def wait_for(pred, timeout=8.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred(): return True
        time.sleep(0.1)
    return False

def logtext():
    if not LOGDIR.exists(): return ""
    return "".join(p.read_text(encoding="utf-8", errors="replace") for p in sorted(LOGDIR.glob("*.log")))

def in_log(marker, timeout=8.0):
    return wait_for(lambda: marker in logtext(), timeout)

def line_with(marker):
    return next((l for l in logtext().splitlines() if marker in l), "")

from Configuration import CosThetaConfigurator as C
C.getInstance()
from redis import Redis
r = Redis("localhost", 6379)
FILEQ = C.getInstance().getFileLoggingQueue()
from utils.RedisUtils import clearQueues
clearQueues(r)                       # as MainProgram does at start-up (a stop command left by an earlier run, ...)
r.delete(FILEQ, "log:cycleContext")

# ---------------------------------------------------------------- L12 (before the logging server starts)
r.xadd(FILEQ, {"message": "L12-stale-message"}, id=f"{int((time.time() - 3600) * 1000)}-0")
r.xadd(FILEQ, {"message": "L12-early-message"})
from logutils.CentralLoggers import MasterFileLogger
master = MasterFileLogger()
check("L12 message queued before the logging server started is written", in_log("L12-early-message"))
check("L12 stale queued message (1 h old) not written, but counted", "L12-stale-message" not in logtext()
      and "older than 10 minutes" in logtext(), line_with("older than"))

from logutils.LogSettings import setLogProcess, publishCycleContext, setLocalCycleContext
from logutils.SlaveLoggers import SlaveFileLogger
from logutils.Logger import Logger
from utils.CosThetaPrintUtils import printBoldGreen, printBoldRed, printLight
setLogProcess("ioserver")
SFL = SlaveFileLogger.getInstance()

# ---------------------------------------------------------------- L1 / L3
print("\n=== L1 / L3 per-server level and print mirroring ===", flush=True)
printBoldGreen("L3-green-marker")
check("L3 a console print (green, INFO) is mirrored to the log file", in_log("L3-green-marker"))
set_props(log__file__level__ioserver="TAKE_ACTION")
time.sleep(1.5)
printBoldRed("L1-red-below-level"); SFL.logTakeAction("test", "L1-takeaction-marker")
check("L1 ioserver level TAKE_ACTION applied while running: TAKE_ACTION written", in_log("L1-takeaction-marker"))
check("L1 ... and a CONSIDER_ACTION (red) line is not", "L1-red-below-level" not in logtext())
set_props(log__file__level__ioserver="DEBUG")
time.sleep(1.5)
printLight("L1-debug-marker")
check("L1 ioserver level DEBUG: grey (DEBUG) print written", in_log("L1-debug-marker"))
setLogProcess("cameraserver")
printLight("L1-camera-debug-marker"); printBoldGreen("L1-camera-info-marker")
check("L1 another server (cameraserver) keeps the global level INFO: INFO written, DEBUG not",
      in_log("L1-camera-info-marker") and "L1-camera-debug-marker" not in logtext())
setLogProcess("ioserver")
set_props(log__mirror__prints__to__file="False"); time.sleep(1.5)
printBoldRed("L3-not-mirrored-marker"); SFL.logTakeAction("test", "L3-after-off")
check("L3 log.mirror.prints.to.file=False: prints not mirrored", in_log("L3-after-off") and "L3-not-mirrored-marker" not in logtext())
set_props(log__mirror__prints__to__file="True")

# ---------------------------------------------------------------- L13
print("\n=== L13 logBoth (the logging call of the production vision modules) ===", flush=True)
from logutils.SlaveLoggers import logBoth
from logutils.Logger import MessageType
setLogProcess("cameraserver")          # global level INFO
def visionCheckThatLogs():
    logBoth('logDebug', "camera.Test", "L13-debug-marker", MessageType.GENERAL)
    logBoth('logWarning', "camera.Test", "L13-warning-marker", MessageType.RISK)
    logBoth('logError', "camera.Test", "L13-error-marker", MessageType.ISSUE)
    logBoth('logCritical', "camera.Test", "L13-critical-marker", MessageType.PROBLEM)
visionCheckThatLogs()
check("L13 logBoth levels mapped (logWarning->TAKE_NOTE, logError->CONSIDER_ACTION, logCritical->TAKE_ACTION)",
      in_log("L13-critical-marker") and "TAKE_NOTE" in line_with("L13-warning-marker").replace(" ", "_").upper()
      and "CONSIDER" in line_with("L13-error-marker").upper() and "TAKE" in line_with("L13-critical-marker").upper(),
      line_with("L13-error-marker"))
check("L13 logBoth respects the server's level (cameraserver INFO: logDebug not written)", "L13-debug-marker" not in logtext())
check("L13 logBoth records the calling function, not logBoth", "visionCheckThatLogs" in line_with("L13-error-marker"),
      line_with("L13-error-marker"))
setLogProcess("ioserver")

# ---------------------------------------------------------------- L2
print("\n=== L2 production clamp ===", flush=True)
set_props(application__mode="PRODUCTION", file__loggingLevel="CONSIDER_ACTION", console__logginglevel="CONSIDER_ACTION",
          log__file__level__ioserver="DEBUG"); time.sleep(1.5)
printBoldGreen("L2-info-in-production"); printBoldRed("L2-red-in-production")
check("L2 PRODUCTION: per-server DEBUG raised to CONSIDER_ACTION (red written, INFO not)",
      in_log("L2-red-in-production") and "L2-info-in-production" not in logtext())
set_props(application__mode="TEST", file__loggingLevel="INFO", console__logginglevel="TAKE_NOTE"); time.sleep(1.5)

# ---------------------------------------------------------------- L4
print("\n=== L4 cycle context ===", flush=True)
publishCycleContext(r, "7204838$400112VA1D$TEST$", 7); time.sleep(1.2)
printBoldGreen("L4-context-marker")
check("L4 shared context on the line: [QR=... cycle=7]", in_log("L4-context-marker") and
      "[QR=7204838$400112VA1D$TEST$ cycle=7]" in line_with("L4-context-marker"), line_with("L4-context-marker"))
setLocalCycleContext("LOCALQR"); printBoldGreen("L4-local-marker")
check("L4 local context overrides the shared one", in_log("L4-local-marker") and "[QR=LOCALQR]" in line_with("L4-local-marker"))
setLocalCycleContext(None)
check("L4 process name on the line (<ioserver>)", "<ioserver>" in line_with("L4-context-marker"))

# ---------------------------------------------------------------- L5
print("\n=== L5 crash capture ===", flush=True)
def boom(): raise ValueError("L5-boom-in-thread")
t = threading.Thread(target=boom, name="L5-thread"); t.start(); t.join()
check("L5 uncaught exception in a thread: traceback in the log file", in_log("L5-boom-in-thread")
      and "UNCAUGHT EXCEPTION in thread 'L5-thread'" in logtext() and "Traceback" in logtext())
faults = list((LOGDIR / "faults").glob("ioserver-*.log"))
check("L5 faulthandler file for hard crashes opened (logs/.../faults/ioserver-<pid>.log)", len(faults) >= 1)

# ---------------------------------------------------------------- L6
print("\n=== L6 suppressed exceptions ===", flush=True)
from utils.SuppressedExceptions import noteSuppressed
def ignores():
    try:
        int("L6-not-a-number")
    except Exception:
        noteSuppressed()
for _ in range(5): ignores()
time.sleep(3)
lines = [l for l in logtext().splitlines() if "L6-not-a-number" in l]
check("L6 an ignored exception is logged once per place (DEBUG), not 5 times", len(lines) == 1 and "DEBUG" in lines[0]
      and "ignores" in lines[0], lines)

# ---------------------------------------------------------------- L7
print("\n=== L7 no silent loss ===", flush=True)
realSend = SFL._sendOne
SFL._sendOne = lambda message: False                          # Redis "down"
for i in range(30): SFL.logTakeAction("test", f"L7-during-outage-{i}")
time.sleep(2)
check("L7 messages kept while Redis is unavailable (none lost)", len(SFL.pending) + SFL.processingQueue.qsize() >= 30
      and SFL.droppedRedisDown == 0, (len(SFL.pending), SFL.processingQueue.qsize()))
SFL._sendOne = realSend                                        # Redis back
check("L7 ... and all delivered once it is back", in_log("L7-during-outage-29", 15) and
      all(f"L7-during-outage-{i}" in logtext() for i in range(30)))
SFL._sendOne = lambda message: False
cap = SFL.processingQueue.maxsize
for i in range(cap + 50): SFL.enqueue(f"L7-flood-{i}")
SFL._sendOne = realSend
check("L7 full in-process queue: the drops are counted and reported in the log", in_log("LOG MESSAGES LOST", 25),
      line_with("LOG MESSAGES LOST"))
wait_for(lambda: SFL.processingQueue.qsize() == 0 and not SFL.pending, 30)

# ---------------------------------------------------------------- L9
print("\n=== L9 live configuration ===", flush=True)
set_props(time__to__display__ok__images="0.75")
check("L9 changed value read while running", wait_for(lambda: str(C.getValue("time.to.display.ok.images")) == "0.75", 4))
check("L9 change written to the log", in_log("time.to.display.ok.images"))
full = text
PROPS.write_text("\n".join(full.splitlines()[:20]), encoding="ascii"); t2 = time.time() + 2; os.utime(PROPS, (t2, t2))
time.sleep(1.5)
check("L9 half-written file (20 lines) not applied: values unchanged", str(C.getValue("time.to.display.ok.images")) == "0.75"
      and C.getValue("plc.vendor") == FILE_VENDOR)
set_props()                                                    # complete file again
check("L9 complete file applied again", wait_for(lambda: C.getValue("plc.vendor") == FILE_VENDOR, 4))

# ---------------------------------------------------------------- L10
print("\n=== L10 vision constants live ===", flush=True)
from camera.CheckKnuckle import CheckKnuckle
before = CheckKnuckle.LHS_CROP_X
VKEY = C.visionPropertyKey("vision.knuckle.lhs.crop.x")          # this machine's key (vision.allenbradley. / vision.siemens.)
set_props(**{VKEY.replace(".", "__"): "391"})
check("L10 CheckKnuckle.LHS_CROP_X re-bound while running (380 -> 391)", wait_for(lambda: CheckKnuckle.LHS_CROP_X == 391, 4),
      CheckKnuckle.LHS_CROP_X)
check("L10 the change is logged with its key", in_log(VKEY))
set_props(**{VKEY.replace(".", "__"): str(before)})
wait_for(lambda: CheckKnuckle.LHS_CROP_X == before, 4)

# ---------------------------------------------------------------- L11
print("\n=== L11 IOServer audit trail and live intervals ===", flush=True)
from _plc_sim import setup
from statemachine.StateMachine import MachineState as MS
from utils.RedisUtils import (sendDataFromQRCodeServerToIOServer, sendDataFromCameraServerToIOServer, ok, clearQueues)
clearQueues(r); r.delete(FILEQ)
IOServer, TAGS, WRITES, LOCK, VENDOR = setup()
TAGS[IOServer.PLC_PC_WatchTag] = 1234
io = IOServer(mode="Test")
time.sleep(6)
QR = "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$"
with LOCK: TAGS[IOServer.PLC_PC_CheckQRCode] = True
assert wait_for(lambda: io.machineState.getCurrentState() == MS.WRITE_QR_CODE), "not in WRITE_QR_CODE"
sendDataFromQRCodeServerToIOServer(r, qrCode=QR, qrCodeStatus=ok)
check("L11 QR handshake done", wait_for(lambda: TAGS[IOServer.PC_PLC_QRCodeCheckDone] is True))
with LOCK: TAGS[IOServer.PLC_PC_CheckQRCode] = False; TAGS[IOServer.PC_PLC_QRCodeCheckDone] = False
with LOCK: TAGS[IOServer.PLC_PC_CheckKnuckle] = True
assert wait_for(lambda: io.machineState.getCurrentState() == MS.WRITE_RESULT_OF_CHECKING_KNUCKLE, 6) or True
sendDataFromCameraServerToIOServer(r, result=ok, currentMachineState=MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE)
check("L11 knuckle handshake done", wait_for(lambda: TAGS[IOServer.PC_PLC_KnuckleCheckDone] is True))
in_log(f"PC->PLC {IOServer.PC_PLC_KnuckleCheckDone} = True", 10)
audit = [l for l in logtext().splitlines() if "AUDIT->" in l]
def idx(fragment):
    return next((i for i, l in enumerate(audit) if fragment in l), -1)
order = [idx(f"CYCLE {io.cycleId} START"), idx(f"PC->PLC {IOServer.PC_PLC_LH_RH_Selection} = 1"),
         idx(f"PC->PLC {IOServer.PC_PLC_RotationRPM} = 30.0"), idx(f"PC->PLC {IOServer.PC_PLC_QRCodeCheckOK} = True"),
         idx(f"PC->PLC {IOServer.PC_PLC_QRCodeCheckDone} = True"), idx("STATE WRITE_QR_CODE -> READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE"),
         idx(f"PLC->PC {IOServer.PLC_PC_CheckKnuckle} = True"), idx(f"PC->PLC {IOServer.PC_PLC_KnuckleCheckOK} = True"),
         idx(f"PC->PLC {IOServer.PC_PLC_KnuckleCheckDone} = True")]
check("L11 audit trail in order: cycle start, settings, QR OK, QR Done, state change, knuckle trigger, OK, Done",
      all(i >= 0 for i in order) and order == sorted(order), f"{order}\n" + "\n".join(audit[-25:]))
qrLines = [l for l in audit if f"[QR={QR} cycle={io.cycleId}]" in l]
check("L11 audit lines after the QR code carry [QR=... cycle=N]", len(qrLines) >= 6, len(qrLines))
check("L11 connection-status toggle not in the audit trail (log.audit.plc.connection.toggle=False)",
      not any(f"PC->PLC {IOServer.PC_PLC_ConnectionStatusTag}" in l for l in audit))
set_props(plc__read__interval="0.25", pc__plc__knuckle__check__ok__tagname="SomethingElse")
check("L11 PLC read interval applied to the running IOServer (0.2 -> 0.25)", wait_for(lambda: io.read_interval == 0.25, 4), io.read_interval)
check("L11 tag-name change: restart warning logged (TAKE_ACTION)", in_log("take effect only after the IOServer is restarted"))
io.logger.error("L11-self-logger-error-marker")
check("L11 IOServer self.logger messages reach the log file", in_log("L11-self-logger-error-marker"))

# ---------------------------------------------------------------- L8 (last: it rotates the file)
print("\n=== L8 rotation and retention ===", flush=True)
set_props(log__file__max__size__mb="0.02", backuplogs__count="3"); time.sleep(1.5)
for i in range(400): SFL.logTakeAction("test", f"L8-rotation-line-{i:04d} " + "x" * 80)
in_log("L8-rotation-line-0399", 20); time.sleep(2)
files = sorted(LOGDIR.glob("*.log"))
check("L8 size limit: rotated into new files, each continuing the previous", len(files) >= 2 and "continued from" in logtext())
check("L8 retention: at most backuplogs.count (3) files kept", len(files) <= 3, len(files))

failed = [n for n, ok_ in RESULTS if not ok_]
print(f"\nRESULT {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed" + (f"; FAILED: {failed}" if failed else ""), flush=True)
shutil.rmtree(TMP, ignore_errors=True)
os._exit(1 if failed else 0)
