"""
Central/Master loggers that consume from Redis queues and handle actual logging output.
"""
import os
import time
import warnings
from threading import Thread
from typing import Optional, TextIO
import sys

from redis import Redis

from logutils.SlaveLoggers import SlaveConsoleLogger, SlaveFileLogger
from logutils.Logger import Logger
from utils.CosThetaFileUtils import createDirectory
from utils.CosThetaPrintUtils import printBoldRed, getCurrentTime, printInColor, printBoldBlue

warnings.filterwarnings('ignore')

from Configuration import *

CosThetaConfigurator.getInstance()

MESSAGE_KEY: str = "message"


class MasterFileLogger(Thread):
    """
    Master logger that consumes messages from Redis and writes to log files.

    Runs in a dedicated process/thread and handles:
    - Creating and rotating log files
    - Buffered writing with periodic flushing
    - Graceful shutdown with queue draining
    """

    logSource: str = ""

    def __init__(self, **kwargs) -> None:
        Thread.__init__(self)
        self.daemon = True
        self.kwargs = kwargs
        self.stopped: bool = False
        self.started: bool = False

        MasterFileLogger.logSource = getFullyQualifiedName(__file__, __class__)

        self.hostname: str = CosThetaConfigurator.getInstance().getRedisHost()
        self.port: int = CosThetaConfigurator.getInstance().getRedisPort()
        self.loggingQ: str = CosThetaConfigurator.getInstance().getFileLoggingQueue()

        self.redisConnection: Optional[Redis] = None
        self.clientRedisConnected: bool = False

        self.loggingDirectory: str = (
            f"{get_project_root()}/{CosThetaConfigurator.getInstance().getLoggingDirectory()}"
        )
        createDirectory(self.loggingDirectory)

        self.fileName: str = ""
        self.fileHandle: Optional[TextIO] = None

        self.connectToRedis()
        self.createLoggingFile()
        # Messages sent before this logger started (other servers starting up, or still queued from the previous
        # run) are written, not discarded.
        self._discardStaleQueuedMessages()
        waiting = self._getMessageCount()
        if waiting and self.fileHandle:
            self.fileHandle.write(f"{getCurrentTime()} : MasterFileLogger started; {waiting} message(s) were queued "
                                  f"before it started and follow\n")
        self.start()

        SlaveConsoleLogger.getInstance().logTakeNote(
            MasterFileLogger.logSource,
            f"{MasterFileLogger.logSource} started",
            Logger.SUCCESS
        )
        # Prime the MasterFileLogger - prevents indefinite blocking on first xread()
        SlaveFileLogger.getInstance().logTakeNote(
            MasterFileLogger.logSource,
            f"{MasterFileLogger.logSource} started",
            Logger.SUCCESS
        )

    def _discardStaleQueuedMessages(self) -> None:
        """Queued messages older than log.file.keep.queued.minutes (default 10) are left over from an earlier run or
        from tests run without the logging server: they are counted in the new file, not written."""
        try:
            minutes = float(CosThetaConfigurator.getValue("log.file.keep.queued.minutes", "10") or 10)
            if not self.redisConnection:
                return
            before = self._getMessageCount()
            cutoffMs = int((time.time() - minutes * 60) * 1000)
            self.redisConnection.xtrim(self.loggingQ, minid=f"{cutoffMs}-0", approximate=False)
            discarded = before - self._getMessageCount()
            if discarded and self.fileHandle:
                self.fileHandle.write(f"{getCurrentTime()} : {discarded} queued log message(s) older than {minutes:g} "
                                      f"minutes (an earlier run / tests) were not written\n")
        except Exception as e:
            print(f"Could not check for stale queued log messages: {e}")

    def createLoggingFile(self) -> None:
        """Create a new log file with timestamp in filename; keep only the newest backuplogs.count files."""
        suffix = CosThetaConfigurator.getInstance().getFileLoggingSuffix()
        self.fileName = f"{self.loggingDirectory}/{getCurrentTime()}.{suffix}"
        self.fileDate = time.strftime("%Y-%m-%d")
        self._removeOldLogFiles(suffix)

        try:
            self.fileHandle = open(self.fileName, 'a', encoding='utf-8')
        except Exception as e:
            SlaveConsoleLogger.getInstance().logConsiderAction(
                MasterFileLogger.logSource,
                f"Could not create logging file {self.fileName} due to {e}",
                Logger.ISSUE
            )

    def connectToRedis(self, forceRenew: bool = False) -> None:
        """
        Establish Redis connection.

        Args:
            forceRenew: If True, close existing connection and create new one
        """
        if forceRenew:
            self.redisConnection = None
            self.clientRedisConnected = False

        if not self.clientRedisConnected:
            try:
                self.redisConnection = Redis(
                    self.hostname,
                    self.port,
                    retry_on_timeout=True
                )
                self.clientRedisConnected = True
            except Exception:
                self.clientRedisConnected = False
                SlaveConsoleLogger.getInstance().logTakeAction(
                    MasterFileLogger.logSource,
                    f'Could not get Redis Connection in process {os.getpid()}',
                    Logger.ISSUE
                )

    def clearQueue(self) -> None:
        """Clear the logging queue."""
        try:
            if self.redisConnection:
                self.redisConnection.xtrim(self.loggingQ, 0)
        except Exception:
            pass

    def stop(self) -> None:
        """Signal shutdown and stop slave loggers."""
        self.stopped = True
        SlaveFileLogger.getInstance().stop()
        SlaveConsoleLogger.getInstance().stop()

    def _getMessageCount(self) -> int:
        """Get current message count in the queue."""
        try:
            if self.redisConnection:
                return self.redisConnection.xlen(self.loggingQ)
        except Exception:
            pass
        return 0

    BATCH: int = 200
    STOP_CHECK_INTERVAL: float = 0.5        # s between checks of the stop queue (each check can take ~100 ms)

    def _getData(self) -> Optional[list]:
        """
        Get data from Redis queue.

        Returns:
            Dictionary with message data or None
        """
        try:
            if not self.redisConnection:
                return None

            # Up to BATCH messages per read (one message per read limited the logger to ~50 lines/s)
            resp = self.redisConnection.xread({self.loggingQ: 0}, count=MasterFileLogger.BATCH, block=1000)
            if resp:
                key, messages = resp[0]
                batch = []
                for _id, data in messages:
                    batch.append({k.decode("utf-8"): v for k, v in data.items()})
                self.redisConnection.xdel(self.loggingQ, *[_id for _id, _ in messages])
                return batch

        except Exception as e:
            SlaveConsoleLogger.getInstance().logTakeAction(
                MasterFileLogger.logSource,
                f'Could not get data due to {e}',
                Logger.PROBLEM
            )
            self.connectToRedis(True)

        return None

    def _checkStopCommand(self) -> bool:
        """Check if stop command was received."""
        try:
            if not self.redisConnection:
                return False

            from utils.RedisUtils import getStopCommandFromQueue
            _, shallStop = getStopCommandFromQueue(self.redisConnection)
            return shallStop

        except Exception as e:
            SlaveConsoleLogger.getInstance().logTakeAction(
                MasterFileLogger.logSource,
                f'Could not complete looking for stop command due to {e}',
                Logger.PROBLEM
            )
            self.connectToRedis(True)

        return False

    def _removeOldLogFiles(self, suffix: str) -> None:
        """Retention: keep the newest backuplogs.count log files (the one being opened included)."""
        try:
            keep = max(1, int(CosThetaConfigurator.getInstance().getBackupLogsCount()))
            files = sorted((f for f in os.listdir(self.loggingDirectory) if f.endswith(f".{suffix}")),
                           key=lambda f: os.path.getmtime(os.path.join(self.loggingDirectory, f)))
            for old in files[:max(0, len(files) - (keep - 1))]:
                os.remove(os.path.join(self.loggingDirectory, old))
        except Exception as e:
            print(f"Could not remove old log files: {e}")

    def _rotateIfNeeded(self) -> None:
        """New file at midnight (log.file.rotate.daily) or when the file exceeds log.file.max.size.mb."""
        try:
            daily = str(CosThetaConfigurator.getValue("log.file.rotate.daily", "True")).strip().lower() in ("true", "1", "yes", "on")
            maxMb = float(CosThetaConfigurator.getValue("log.file.max.size.mb", "50") or 50)
            newDay = daily and time.strftime("%Y-%m-%d") != getattr(self, "fileDate", time.strftime("%Y-%m-%d"))
            tooBig = self.fileHandle is not None and maxMb > 0 and self.fileHandle.tell() > maxMb * 1024 * 1024
            if newDay or tooBig:
                old = self.fileName
                if self.fileHandle:
                    self.fileHandle.write(f"{getCurrentTime()} : continued in a new log file "
                                          f"({'new day' if newDay else f'size limit {maxMb} MB'})\n")
                self._safeCloseFile()
                self.createLoggingFile()
                if self.fileHandle:
                    self.fileHandle.write(f"{getCurrentTime()} : continued from {os.path.basename(old)}\n")
        except Exception as e:
            print(f"Log rotation check failed: {e}")

    def _writeToFile(self, data: str) -> bool:
        """
        Write data to log file.

        Args:
            data: String data to write

        Returns:
            True if successful, False otherwise
        """
        self._rotateIfNeeded()
        if not self.fileHandle:
            return False

        try:
            self.fileHandle.write(data)
            return True
        except Exception as e:
            SlaveConsoleLogger.getInstance().logTakeAction(
                MasterFileLogger.logSource,
                f'Could not write to log file {self.fileName} due to {e}',
                Logger.PROBLEM
            )
            # Try to flush/close and create new file
            self._safeCloseFile()
            self.createLoggingFile()
            return False

    def _flushFile(self) -> None:
        """Flush file buffer to disk."""
        if self.fileHandle:
            try:
                self.fileHandle.flush()
                os.fsync(self.fileHandle.fileno())
            except Exception:
                pass

    def _safeCloseFile(self) -> None:
        """Safely close the log file with flushing."""
        if self.fileHandle:
            try:
                self.fileHandle.flush()
                os.fsync(self.fileHandle.fileno())
                self.fileHandle.close()
            except Exception:
                pass
            self.fileHandle = None

    def doWork(self, timeout: int = 1) -> None:
        """
        Main work loop - consume messages and write to file.

        Args:
            timeout: Sleep timeout when queue is empty
        """
        count: int = 0
        flushAfter: int = 3

        lastStopCheck = 0.0
        while not (self.stopped and self._getMessageCount() == 0):
            for aMessage in (self._getData() or []):
                if MESSAGE_KEY not in aMessage:
                    continue
                data = aMessage[MESSAGE_KEY]
                if isinstance(data, bytes):
                    data = data.decode('utf-8')
                if self._writeToFile(data + "\n"):
                    count += 1

            # Check for stop command (at most every STOP_CHECK_INTERVAL)
            if time.time() - lastStopCheck < MasterFileLogger.STOP_CHECK_INTERVAL:
                stopNow = False
            else:
                lastStopCheck = time.time()
                stopNow = self._checkStopCommand()
            if stopNow:
                printBoldRed(f"Just after getting Stop Command in {MasterFileLogger.logSource}")
                self.stop()

            # Queue empty: flush (so the file is always up to date for whoever reads it) and wait
            if self._getMessageCount() == 0:
                if count:
                    self._flushFile()
                    count = 0
                time.sleep(timeout)

        # Final cleanup
        if self.fileHandle:
            try:
                self.fileHandle.write(f"{getCurrentTime()} : Closing down MasterFileLogger\n")
            except Exception:
                pass
        self._safeCloseFile()     # (the loop above has written every queued message before stopping)

    def run(self) -> None:
        """Thread entry point."""
        if not self.started:
            self.started = True
            self.stopped = False
            self.doWork(timeout=1)


class MasterConsoleLogger(Thread):
    """
    Master logger that consumes messages from Redis and prints to console.

    Runs in a dedicated process/thread and handles:
    - Colored console output based on message type
    - Graceful shutdown with queue draining
    """

    logSource: str = ""

    def __init__(self, **kwargs) -> None:
        Thread.__init__(self)
        self.daemon = True
        self.kwargs = kwargs
        self.stopped: bool = False
        self.started: bool = False

        MasterConsoleLogger.logSource = getFullyQualifiedName(__file__, __class__)

        self.hostname: str = CosThetaConfigurator.getInstance().getRedisHost()
        self.port: int = CosThetaConfigurator.getInstance().getRedisPort()
        self.loggingQ: str = CosThetaConfigurator.getInstance().getConsoleLoggingQueue()

        self.redisConnection: Optional[Redis] = None
        self.clientRedisConnected: bool = False

        self.connectToRedis()
        self.clearQueue()
        self.start()

    def connectToRedis(self, forceRenew: bool = False) -> None:
        """
        Establish Redis connection.

        Args:
            forceRenew: If True, close existing connection and create new one
        """
        if forceRenew:
            self.redisConnection = None
            self.clientRedisConnected = False

        if not self.clientRedisConnected:
            try:
                self.redisConnection = Redis(
                    self.hostname,
                    self.port,
                    retry_on_timeout=True
                )
                self.clientRedisConnected = True
            except Exception:
                self.clientRedisConnected = False
                printBoldRed(
                    f'Could not get Redis Connection in {MasterConsoleLogger.logSource} '
                    f'in process {os.getpid()}'
                )

    def clearQueue(self) -> None:
        """Clear the logging queue."""
        try:
            if self.redisConnection:
                self.redisConnection.xtrim(self.loggingQ, 0)
        except Exception:
            pass

    def stop(self) -> None:
        """Signal shutdown and stop slave loggers."""
        self.stopped = True
        SlaveConsoleLogger.getInstance().stop()
        SlaveFileLogger.getInstance().stop()

    def _getMessageCount(self) -> int:
        """Get current message count in the queue."""
        try:
            if self.redisConnection:
                return self.redisConnection.xlen(self.loggingQ)
        except Exception:
            pass
        return 0

    def _getData(self) -> Optional[dict]:
        """
        Get data from Redis queue.

        Returns:
            Dictionary with message data or None
        """
        try:
            if not self.redisConnection:
                return None

            resp = self.redisConnection.xread(
                {self.loggingQ: 0},
                count=1,
                block=1000
            )

            if resp:
                key, messages = resp[0]
                last_id, data = messages[0]

                # Decode keys
                newData = {}
                for k in data.keys():
                    newData[k.decode("utf-8")] = data[k]

                self.redisConnection.xdel(self.loggingQ, last_id)
                return newData

        except Exception as e:
            printBoldRed(
                f"Exception in {MasterConsoleLogger.logSource} while getting data from Redis: {e}"
            )
            self.connectToRedis(True)

        return None

    def _checkStopCommand(self) -> bool:
        """Check if stop command was received."""
        try:
            if not self.redisConnection:
                return False

            from utils.RedisUtils import getStopCommandFromQueue
            _, shallStop = getStopCommandFromQueue(self.redisConnection)
            return shallStop

        except Exception as e:
            printBoldRed(
                f"Exception in {MasterConsoleLogger.logSource} while getting stop command: {e}"
            )
            self.connectToRedis(True)

        return False

    def doWork(self, timeout: int = 1) -> None:
        """
        Main work loop - consume messages and print to console.

        Args:
            timeout: Sleep timeout when queue is empty
        """
        while not (self.stopped and self._getMessageCount() == 0):
            aMessage = self._getData()

            if aMessage and MESSAGE_KEY in aMessage:
                data = aMessage[MESSAGE_KEY]
                if isinstance(data, bytes):
                    data = data.decode('utf-8')

                messageType = Logger.getMessageType(data)
                printInColor(data, messageType)

            # Sleep if queue is empty
            if self._getMessageCount() == 0:
                time.sleep(timeout)

            # Check for stop command
            if self._checkStopCommand():
                self.stop()

        self.clearQueue()

    def run(self) -> None:
        """Thread entry point."""
        if not self.started:
            self.started = True
            self.stopped = False
            self.doWork(timeout=5)


def startLoggers() -> None:
    """Start both master loggers and wait for them to complete."""
    from logutils.LogSettings import setLogProcess
    setLogProcess("logging")                 # the logging server itself: its prints are not mirrored
    mcl = MasterConsoleLogger()
    mfl = MasterFileLogger()
    printBoldBlue("******************")
    printBoldBlue("Started Logging Server")
    printBoldBlue("******************")
    mcl.join()
    mfl.join()
    sys.exit(0)