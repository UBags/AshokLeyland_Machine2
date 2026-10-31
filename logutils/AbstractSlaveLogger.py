"""
Abstract base class for slave loggers that communicate with master loggers via Redis.
"""
import inspect
import os
import threading
import time
import warnings
from abc import ABC, abstractmethod
from datetime import datetime
from queue import Queue
from collections import deque
from threading import Thread
from typing import Optional

from redis import Redis

from utils.CosThetaPrintUtils import printBoldRed
from utils.RedisUtils import sendData, getStopCommandFromQueue, getMessageCount
from logutils.Logger import Logger

warnings.filterwarnings('ignore', '.*h264.*')

from Configuration import *

CosThetaConfigurator.getInstance()

MESSAGE_KEY: str = "message"
callerOverride = threading.local()      # .value = (function, line) of the real caller (set by the print mirror)


class SlaveLogger(Thread, ABC):
    """
    Abstract base class for slave loggers.

    Slave loggers run in application processes and send log messages to Redis queues.
    Master loggers consume from these queues and handle actual logging (console/file).

    Attributes:
        currentFileLoggingLevel: Class-level file logging threshold
        currentConsoleLoggingLevel: Class-level console logging threshold
    """

    currentFileLoggingLevel: int = Logger.DEBUG
    currentConsoleLoggingLevel: int = Logger.DEBUG
    _lastTimeConfigLoaded: Optional[str] = None
    _configLock: threading.Lock = threading.Lock()
    logSource: str = ""

    # Messages are never dropped silently: a full in-process queue or a Redis outage is counted, messages that
    # could not be sent are kept (up to PENDING_LIMIT) and re-sent when Redis is back, and the counts of any that
    # had to be dropped are reported in the log as soon as it is possible again.
    DEFAULT_QUEUE_SIZE: int = 5000
    PENDING_LIMIT: int = 20000

    def __init__(self, max_size: int = 5000, **kwargs) -> None:
        """
        Initialize the slave logger.

        Args:
            max_size: Maximum size of the internal processing queue
            **kwargs: Additional keyword arguments
        """
        Thread.__init__(self)
        self.daemon = True
        self.kwargs = kwargs
        self.stopped: bool = False
        self.started: bool = False
        self.processingQueue: Queue = Queue(max(max_size, SlaveLogger.DEFAULT_QUEUE_SIZE))
        self.pending: deque = deque()                 # messages waiting for Redis
        self.droppedQueueFull: int = 0                # lost because the in-process queue was full
        self.droppedRedisDown: int = 0                # lost because Redis was down for too long
        self._reportedDrops = (0, 0)
        self.hostname: str = CosThetaConfigurator.getInstance().getRedisHost()
        self.port: int = CosThetaConfigurator.getInstance().getRedisPort()
        SlaveLogger.logSource = getFullyQualifiedName(__file__, __class__)
        # Default logging queue - subclasses should override
        self.loggingQ: str = CosThetaConfigurator.getInstance().getFileLoggingQueue()
        self.redisConnection: Optional[Redis] = None
        self.clientRedisConnected: bool = False
        self.connectToRedis()

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
            except Exception as e:
                self.clientRedisConnected = False
                printBoldRed(
                    f'Could not get Redis Connection in {SlaveLogger.logSource} '
                    f'in process {os.getpid()}: {e}'
                )

    _levelProcess: Optional[str] = None

    @classmethod
    def updateLoggingLevels(cls) -> None:
        """Logging levels for this process (log.file.level.<server> / log.console.level.<server>, else the global
        levels), re-read whenever the properties file has been re-loaded or the process identity changed."""
        from logutils.LogSettings import fileLevelName, consoleLevelName, getLogProcess
        CosThetaConfigurator._loadConfig()
        with cls._configLock:
            currentConfigTime = CosThetaConfigurator.getLastTimeLoaded()
            if cls._lastTimeConfigLoaded != currentConfigTime or cls._levelProcess != getLogProcess():
                cls._lastTimeConfigLoaded = currentConfigTime
                cls._levelProcess = getLogProcess()
                cls.currentFileLoggingLevel = Logger.getLoggingLevelInt(fileLevelName())
                cls.currentConsoleLoggingLevel = Logger.getLoggingLevelInt(consoleLevelName())

    def contextualise(self, message: str) -> str:
        """Prefix the current cycle context, e.g. '[QR=... cycle=12] message'."""
        try:
            from logutils.LogSettings import currentContext, getLogProcess
            ctx = currentContext(self.redisConnection)
            proc = getLogProcess()
            prefix = (f"<{proc}> " if proc else "") + (f"{ctx} " if ctx else "")
            return prefix + message
        except Exception:
            return message

    def enqueue(self, formatted: str) -> bool:
        try:
            self.processingQueue.put_nowait(formatted)
            return True
        except Exception:
            self.droppedQueueFull += 1
            return False

    def _sendOne(self, message: str) -> bool:
        try:
            ok = sendData(self.redisConnection, {MESSAGE_KEY: message}, self.loggingQ, aProducer=SlaveLogger.logSource)
            return bool(ok)
        except Exception:
            return False

    @abstractmethod
    def logMessage(self, messageLogLevel: int, source: str, message: str, messageType: int) -> None:
        """
        Log a message at the specified level.

        Args:
            messageLogLevel: The severity level of the message
            source: The source/origin of the message
            message: The message content
            messageType: The type of message (affects display formatting)
        """
        pass

    def logDebug(self, source: str, message: str, messageType: int = Logger.GENERAL) -> None:
        """Log a DEBUG level message."""
        if not self.stopped:
            self.logMessage(Logger.DEBUG, source, message, messageType)

    def logInfo(self, source: str, message: str, messageType: int = Logger.SUCCESS) -> None:
        """Log an INFO level message."""
        if not self.stopped:
            self.logMessage(Logger.INFO, source, message, messageType)

    def logTakeNote(self, source: str, message: str, messageType: int = Logger.RISK) -> None:
        """Log a TAKENOTE level message."""
        if not self.stopped:
            self.logMessage(Logger.TAKENOTE, source, message, messageType)

    def logConsiderAction(self, source: str, message: str, messageType: int = Logger.ISSUE) -> None:
        """Log a CONSIDERACTION level message."""
        if not self.stopped:
            self.logMessage(Logger.CONSIDERACTION, source, message, messageType)

    def logTakeAction(self, source: str, message: str, messageType: int = Logger.PROBLEM) -> None:
        """Log a TAKEACTION level message."""
        if not self.stopped:
            self.logMessage(Logger.TAKEACTION, source, message, messageType)

    def makeLoggingMessage(
            self,
            loggingLevel: str,
            source: str,
            message: str,
            messageType: int = Logger.GENERAL
    ) -> str:
        """
        Format a logging message with timestamp, source, and level info.

        Args:
            loggingLevel: String representation of log level
            source: Source of the log message
            message: The actual message content
            messageType: Type of message for display formatting

        Returns:
            Formatted log message string
        """
        # Clamp messageType to valid range
        messageType = max(Logger.GENERAL, min(messageType, Logger.PROBLEM))

        # Get caller information
        func_name = ""
        lno = ""
        override = getattr(callerOverride, "value", None)
        if override:
            func_name, lno = override
        else:
          try:
            currentFrame = inspect.currentframe()
            if currentFrame:
                callerFrame = currentFrame.f_back
                if callerFrame:
                    callerFrame = callerFrame.f_back
                    if callerFrame:
                        callerFrame = callerFrame.f_back
                        if callerFrame:
                            co = callerFrame.f_code
                            func_name = co.co_name
                            lno = callerFrame.f_lineno
          except (AttributeError, TypeError):
            pass

        # Format timestamp
        msTime = datetime.now().strftime('%Y-%m-%d-%H-%M-%S.%f')
        dt, ms = msTime.split('.')
        ms_int = int(ms) // 1000
        currentTime = f'{dt}.{ms_int:03}'

        # Build message
        mtText = Logger.getMessageTypeText(messageType)
        source_part = f"{source.strip()}." if source else ""
        func_part = func_name.strip() if func_name else ""

        return (
            f'{currentTime} :  {loggingLevel}->{mtText}->{source_part}{func_part}:{lno} :: {message}'
        )

    def grabMessageForPuttingIntoRedisQ(self, block: bool = True, timeout: int = 0) -> Optional[str]:
        """
        Get a message from the internal queue for sending to Redis.

        Args:
            block: Whether to block waiting for a message
            timeout: Timeout in seconds (0 = infinite)

        Returns:
            Message string or None if timeout/empty
        """
        try:
            return self.processingQueue.get(block=block, timeout=timeout)
        except Exception:
            return None

    def stop(self) -> None:
        """Signal the logger to stop processing."""
        self.stopped = True

    def doWork(self, block: bool = True, timeout: int = 3) -> None:
        """
        Main work loop - transfer messages from internal queue to Redis.

        Args:
            block: Whether to block on queue operations
            timeout: Timeout for queue operations
        """
        while not self.stopped or self.processingQueue.qsize() > 0 or (self.pending and self._sendPending()):
            aMessage = self.grabMessageForPuttingIntoRedisQ(block, 1 if self.pending else timeout)
            if aMessage is not None:
                self.pending.append(aMessage)
            self._sendPending()

            # Check for stop command (at most every 0.5 s: each check can take ~100 ms in Redis)
            if time.time() - getattr(self, "_lastStopCheck", 0.0) >= 0.5:
                self._lastStopCheck = time.time()
                try:
                    _, shallStop = getStopCommandFromQueue(self.redisConnection)
                    if shallStop:
                        self.stop()
                except Exception:
                    self.connectToRedis(True)

    def _sendPending(self) -> bool:
        """Send waiting messages in order; on a Redis failure keep them (bounded) and reconnect.
        Returns True while messages are still waiting."""
        drops = (self.droppedQueueFull, self.droppedRedisDown)
        if drops != self._reportedDrops and self.redisConnection is not None:
            notice = self.makeLoggingMessage("TAKE_ACTION", SlaveLogger.logSource,
                                             f"LOG MESSAGES LOST in process {os.getpid()}: "
                                             f"{drops[0]} (in-process queue full), {drops[1]} (Redis unavailable)",
                                             Logger.PROBLEM)
            if self._sendOne(notice):
                self._reportedDrops = drops
        while self.pending:
            if not self._sendOne(self.pending[0]):
                self.connectToRedis(True)
                while len(self.pending) > SlaveLogger.PENDING_LIMIT:
                    self.pending.popleft()
                    self.droppedRedisDown += 1
                time.sleep(0.5)
                return True
            self.pending.popleft()
        return False

    def run(self) -> None:
        """Thread entry point."""
        if not self.started:
            self.started = True
            self.stopped = False
            self.doWork(timeout=5)