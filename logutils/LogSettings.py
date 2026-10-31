"""
LogSettings - per-process logging settings, cycle context and crash capture.

Everything here is read from ApplicationConfiguration.properties at the time of use, so a change to the file takes
effect while the machine is running (CosThetaConfigurator reloads the file when it changes):

  log.file.level.<server>=      file logging level for that server  (empty = file.loggingLevel)
  log.console.level.<server>=   console logging level for that server (empty = console.logginglevel)
      <server> = mainprogram | ioserver | cameraserver | qrcodeserver | frontend | dbserver | heartbeatserver
      levels   = DEBUG | INFO | TAKE_NOTE | CONSIDER_ACTION | TAKE_ACTION
      In PRODUCTION mode a level below CONSIDER_ACTION is raised to CONSIDER_ACTION (the rule checkIntegrity
      enforces for the global levels).
  log.mirror.prints.to.file=True   the coloured console prints (printBold..., printRed...) also go to the log file
  log.audit.plc=True               PLC audit trail (every PC->PLC write, every PLC->PC trigger, state changes),
                                   written whatever the level
  log.audit.plc.connection.toggle=False   include the PC_PLC_ConnectionStatusTag toggle (every 2 s) in the audit
  log.suppressed.exceptions=True   exceptions caught and ignored in the code are logged once per place (DEBUG)

Process identity: each server calls setLogProcess("<server>") at start-up. Until then (tools, tests) nothing is
mirrored and no crash hooks are installed, so command-line tools behave exactly as before.

Cycle context: the IOServer publishes the current QR code and cycle number when a QR code is accepted
(publishCycleContext); every log line written by any server then carries "[QR=... cycle=N]". A server may set a
local context instead (setLocalCycleContext), e.g. the DB server for the record it is storing.
"""
import faulthandler
import os
import sys
import threading
import time
import traceback
from typing import Optional

SERVERS = ("mainprogram", "ioserver", "cameraserver", "qrcodeserver", "frontend", "dbserver", "heartbeatserver",
           "logging")
LEVEL_NAMES = ("DEBUG", "INFO", "TAKE_NOTE", "CONSIDER_ACTION", "TAKE_ACTION")

_process: Optional[str] = None
_localContext: Optional[str] = None
_sharedContext = {"value": "", "readAt": 0.0}
_hooksInstalled = False
_faultFile = None
CONTEXT_KEY = "log:cycleContext"          # Redis key holding the current "[QR=... cycle=N]"
CONTEXT_TTL_SECS = 6 * 3600


# ------------------------------------------------------------------ process identity
def setLogProcess(name: str) -> None:
    """Called once by each server at start-up (e.g. 'ioserver'). Enables print mirroring, crash capture and the
    live configuration watcher for this process."""
    global _process
    _process = (name or "").strip().lower() or None
    if _process and _process != "logging":
        installCrashHandlers()
        try:
            from Configuration import CosThetaConfigurator
            CosThetaConfigurator.startConfigWatcher()
        except Exception:
            pass


def getLogProcess() -> Optional[str]:
    return _process


# ------------------------------------------------------------------ configuration helpers
def _cfg(key: str, default: str = "") -> str:
    try:
        from Configuration import CosThetaConfigurator
        value = CosThetaConfigurator.getValue(key, default=default)
        return default if value is None else str(value).strip()
    except Exception:
        return default


def _flag(key: str, default: bool) -> bool:
    value = _cfg(key, "")
    if value == "":
        return default
    return value.lower() in ("true", "yes", "1", "on")


def mirrorPrintsToFile() -> bool:
    return _process is not None and _process != "logging" and _flag("log.mirror.prints.to.file", True)


def auditPLC() -> bool:
    return _flag("log.audit.plc", True)


def auditConnectionToggle() -> bool:
    return _flag("log.audit.plc.connection.toggle", False)


def logSuppressedExceptions() -> bool:
    return _process is not None and _flag("log.suppressed.exceptions", True)


def _isProduction() -> bool:
    return "PRODUCTION" in _cfg("application.mode", "TEST").upper()


def _levelName(kind: str) -> str:
    """kind = 'file' or 'console': the level name for this process (per-server key, else the global key)."""
    globalKey = "file.loggingLevel" if kind == "file" else "console.logginglevel"
    name = ""
    if _process:
        name = _cfg(f"log.{kind}.level.{_process}", "").upper()
    if name not in LEVEL_NAMES:
        name = _cfg(globalKey, "INFO").upper()
    if name not in LEVEL_NAMES:
        name = "INFO"
    if _isProduction() and LEVEL_NAMES.index(name) < LEVEL_NAMES.index("CONSIDER_ACTION"):
        name = "CONSIDER_ACTION"
    return name


def fileLevelName() -> str:
    return _levelName("file")


def consoleLevelName() -> str:
    return _levelName("console")


# ------------------------------------------------------------------ cycle context
def setLocalCycleContext(qrCode: Optional[str] = None, cycleId: Optional[int] = None) -> None:
    """Context for this process only (overrides the shared one); None/None clears it."""
    global _localContext
    _localContext = _formatContext(qrCode, cycleId) if (qrCode or cycleId) else None


def _formatContext(qrCode: Optional[str], cycleId: Optional[int]) -> str:
    parts = []
    if qrCode:
        parts.append(f"QR={qrCode}")
    if cycleId is not None:
        parts.append(f"cycle={cycleId}")
    return f"[{' '.join(parts)}]" if parts else ""


def publishCycleContext(redisConnection, qrCode: Optional[str], cycleId: Optional[int]) -> None:
    """IOServer: make the current cycle known to every server's log lines."""
    text = _formatContext(qrCode, cycleId)
    _sharedContext["value"], _sharedContext["readAt"] = text, time.time()
    try:
        if redisConnection is not None:
            redisConnection.set(CONTEXT_KEY, text, ex=CONTEXT_TTL_SECS)
    except Exception:
        pass


def currentContext(redisConnection=None) -> str:
    """'[QR=... cycle=N]' or ''. The shared context is re-read from Redis at most once a second."""
    if _localContext is not None:
        return _localContext
    now = time.time()
    if redisConnection is not None and now - _sharedContext["readAt"] > 1.0:
        _sharedContext["readAt"] = now
        try:
            raw = redisConnection.get(CONTEXT_KEY)
            _sharedContext["value"] = raw.decode("utf-8", "replace") if raw else ""
        except Exception:
            pass
    return _sharedContext["value"]


# ------------------------------------------------------------------ crash capture
def _logCrash(title: str, excType, excValue, excTb) -> None:
    text = "".join(traceback.format_exception(excType, excValue, excTb))
    try:
        from logutils.SlaveLoggers import SlaveFileLogger
        SlaveFileLogger.getInstance().logCrash(f"process={_process} pid={os.getpid()}", f"{title}\n{text}")
    except Exception:
        pass
    sys.__stderr__.write(f"{title}\n{text}")


def installCrashHandlers() -> None:
    """Uncaught exceptions in the main thread and in any thread are written to the log file (whatever the level)
    with the full traceback; hard crashes (segfaults in native code) are dumped by faulthandler to
    <logging.directory>/faults/<process>-<pid>.log."""
    global _hooksInstalled, _faultFile
    if _hooksInstalled:
        return
    _hooksInstalled = True
    previousSys = sys.excepthook

    def sysHook(excType, excValue, excTb):
        if not issubclass(excType, KeyboardInterrupt):
            _logCrash("UNCAUGHT EXCEPTION (main thread)", excType, excValue, excTb)
        previousSys(excType, excValue, excTb)

    def threadHook(args):
        if args.exc_type is SystemExit:
            return
        name = args.thread.name if args.thread is not None else "?"
        _logCrash(f"UNCAUGHT EXCEPTION in thread '{name}' - the thread has stopped",
                  args.exc_type, args.exc_value, args.exc_traceback)

    sys.excepthook = sysHook
    threading.excepthook = threadHook
    try:
        from BaseUtils import get_project_root
        from Configuration import CosThetaConfigurator
        folder = os.path.join(str(get_project_root()), CosThetaConfigurator.getInstance().getLoggingDirectory(), "faults")
        os.makedirs(folder, exist_ok=True)
        _faultFile = open(os.path.join(folder, f"{_process}-{os.getpid()}.log"), "a", encoding="utf-8")
        faulthandler.enable(file=_faultFile, all_threads=True)
    except Exception as e:
        sys.__stderr__.write(f"faulthandler not enabled: {e}\n")
