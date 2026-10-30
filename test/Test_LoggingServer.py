"""
Smoke test of the central Logging Server (logutils.CentralLoggers) - run from the project root with
`python -m test.Test_LoggingServer` (or `python test/Test_LoggingServer.py`); needs Redis on the configured host/port.

What it does (no hardware, no GUI):
  1. Starts the Logging Server in a separate process exactly as MainProgram does
     (Process(target=startTheLoggingServer) -> logutils.CentralLoggers.startLoggers(), i.e. MasterConsoleLogger +
     MasterFileLogger). The child's stdout is tee'd to a capture file so console output can be checked.
  2. Emits uniquely-tagged messages at every level through logutils.SlaveLoggers.SlaveConsoleLogger and
     SlaveFileLogger (the way every server logs).
  3. Checks: console messages at/above console.logginglevel are printed by MasterConsoleLogger and those below it
     are not; file messages at/above file.loggingLevel land in the new log file under <project root>/<logging.directory>
     and those below it do not; both Redis logging queues drain to 0.
  4. Stops everything with sendStopCommand() (as MainProgram.stopProcesses does), checks the server process exits
     and the log file ends with "Closing down MasterFileLogger", then removes the stop command from Redis.

Options:
  --keep-log   keep the log file this test created (default: it is deleted, together with the logs folder if the
               test created it and it is empty).
Exit code 0 = all checks passed.
"""

import os
import sys
import time
import uuid
import argparse
import multiprocessing as mp

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from redis import Redis

from Configuration import CosThetaConfigurator
from BaseUtils import get_project_root
from logutils.Logger import Logger
from utils.CosThetaPrintUtils import printBoldBlue, printBoldGreen, printBoldRed, printBoldYellow

CosThetaConfigurator.getInstance()


# ----------------------------------------------------------------------------------------------------------------
# Logging-server process (same entry point MainProgram uses), with stdout tee'd to a file for verification
# ----------------------------------------------------------------------------------------------------------------
class _Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            try:
                s.write(data)
                s.flush()
            except Exception:
                pass
        return len(data)

    def flush(self):
        for s in self.streams:
            try:
                s.flush()
            except Exception:
                pass

    def isatty(self):
        return False


def startTheLoggingServer(captureFile: str) -> None:
    """Mirror of MainProgram.startTheLoggingServer(), plus capturing of the console output."""
    sys.path.insert(0, PROJECT_ROOT)
    capture = open(captureFile, "a", encoding="utf-8")
    sys.stdout = _Tee(sys.__stdout__, capture)
    from logutils.CentralLoggers import startLoggers
    startLoggers()


# ----------------------------------------------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------------------------------------------
results = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed, detail))
    (printBoldGreen if passed else printBoldRed)(f"{'PASS' if passed else 'FAIL'}: {name}{(' - ' + detail) if detail else ''}")


def waitFor(predicate, timeout: float, interval: float = 0.25) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def readText(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test of the central Logging Server")
    parser.add_argument("--keep-log", action="store_true", help="keep the log file created by this test")
    args = parser.parse_args()

    cfg = CosThetaConfigurator.getInstance()
    redisConnection = Redis(cfg.getRedisHost(), cfg.getRedisPort(), retry_on_timeout=True)
    redisConnection.ping()

    from utils.RedisUtils import sendStopCommand, stopQ
    fileQ = cfg.getFileLoggingQueue()
    consoleQ = cfg.getConsoleLoggingQueue()
    # A stale stop command (never consumed by design) would make the loggers stop immediately
    redisConnection.xtrim(stopQ, 0)

    logDir = f"{get_project_root()}/{cfg.getLoggingDirectory()}"
    logDirExisted = os.path.isdir(logDir)
    before = set(os.listdir(logDir)) if logDirExisted else set()

    fileLevel = Logger.getLoggingLevelInt(cfg.getFileLoggingLevel())
    consoleLevel = Logger.getLoggingLevelInt(cfg.getConsoleLoggingLevel())
    printBoldBlue(f"Redis {cfg.getRedisHost()}:{cfg.getRedisPort()}, queues file='{fileQ}' console='{consoleQ}', "
                  f"stop='{stopQ}'")
    printBoldBlue(f"Levels: file={cfg.getFileLoggingLevel()} ({fileLevel}), console={cfg.getConsoleLoggingLevel()} "
                  f"({consoleLevel}); log directory {logDir}")

    import tempfile
    captureFile = os.path.join(tempfile.gettempdir(), f"Test_LoggingServer_console_{os.getpid()}.txt")
    open(captureFile, "w").close()

    # 1. Start the logging server process, as MainProgram does (it waits 3 s before carrying on)
    ctx = mp.get_context("spawn")      # same start method as on the Windows production PC
    P1 = ctx.Process(target=startTheLoggingServer, args=(captureFile,))
    P1.start()
    printBoldBlue(f"Logging server process is {P1.pid}")
    time.sleep(3)
    check("Logging server process alive after start", P1.is_alive())

    newFiles = []
    check("MasterFileLogger created a new log file",
          waitFor(lambda: len(set(os.listdir(logDir)) - before) > 0 if os.path.isdir(logDir) else False, 5))
    if os.path.isdir(logDir):
        newFiles = sorted(set(os.listdir(logDir)) - before)
    logFile = os.path.join(logDir, newFiles[-1]) if newFiles else ""
    printBoldBlue(f"Log file: {logFile}")

    # 2. Emit messages through the slave loggers
    from logutils.SlaveLoggers import SlaveConsoleLogger, SlaveFileLogger
    tag = uuid.uuid4().hex[:8]
    src = "test.Test_LoggingServer"
    levels = [(Logger.DEBUG, "logDebug", Logger.GENERAL), (Logger.INFO, "logInfo", Logger.SUCCESS),
              (Logger.TAKENOTE, "logTakeNote", Logger.RISK), (Logger.CONSIDERACTION, "logConsiderAction", Logger.ISSUE),
              (Logger.TAKEACTION, "logTakeAction", Logger.PROBLEM)]
    expected = {}   # token -> (should_appear, where)
    for level, method, mtype in levels:
        ctoken = f"CONSOLE-{tag}-{method}"
        ftoken = f"FILE-{tag}-{method}"
        getattr(SlaveConsoleLogger.getInstance(), method)(src, ctoken, mtype)
        getattr(SlaveFileLogger.getInstance(), method)(src, ftoken, mtype)
        expected[ctoken] = (level >= consoleLevel, "console")
        expected[ftoken] = (level >= fileLevel, "file")
    printBoldBlue(f"Emitted {len(expected)} messages tagged {tag}")

    # 3. Verify delivery
    wantConsole = [t for t, (s, w) in expected.items() if s and w == "console"]
    check("Console messages printed by MasterConsoleLogger",
          waitFor(lambda: all(t in readText(captureFile) for t in wantConsole), 20),
          f"{sum(t in readText(captureFile) for t in wantConsole)}/{len(wantConsole)}")
    check("Redis logging queues drained",
          waitFor(lambda: redisConnection.xlen(fileQ) == 0 and redisConnection.xlen(consoleQ) == 0, 15),
          f"file={redisConnection.xlen(fileQ)}, console={redisConnection.xlen(consoleQ)}")

    # 4. Stop as MainProgram.stopProcesses does
    sendStopCommand(redisConnection, aProducer="Test_LoggingServer")
    P1.join(timeout=30)
    exited = not P1.is_alive()
    check("Logging server process exited after stop command", exited, f"exitcode={P1.exitcode}")
    if not exited:
        P1.terminate()
        P1.join(5)
    redisConnection.xtrim(stopQ, 0)    # never leave a stop command behind for other servers

    console = readText(captureFile)
    logText = readText(logFile) if logFile else ""
    for token, (shouldAppear, where) in expected.items():
        text = console if where == "console" else logText
        present = token in text
        check(f"{where:7s} {token.split('-', 2)[2]:18s} {'present' if shouldAppear else 'filtered'}",
              present == shouldAppear)
    check("Log file closed cleanly", "Closing down MasterFileLogger" in logText)

    printBoldBlue("---- log file lines from this test ----")
    for line in logText.splitlines():
        if tag in line or "Closing down" in line:
            print(line)

    # Cleanup
    try:
        os.remove(captureFile)
    except OSError:
        pass
    if not args.keep_log and logFile:
        for f in newFiles:
            try:
                os.remove(os.path.join(logDir, f))
            except OSError:
                pass
        if not logDirExisted:
            try:
                os.rmdir(logDir)
            except OSError:
                pass

    failed = [r for r in results if not r[1]]
    printBoldBlue("=" * 60)
    (printBoldGreen if not failed else printBoldRed)(f"{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        for name, _, detail in failed:
            printBoldYellow(f"  failed: {name} {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    # The slave-logger threads in this process are daemons, but exit hard to avoid waiting on Redis blocking reads
    os._exit(code)
