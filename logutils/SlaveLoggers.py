"""
Concrete slave logger implementations for file and console logging.
"""
import threading
import warnings
from typing import Optional

from logutils.Logger import Logger
from logutils.AbstractSlaveLogger import SlaveLogger
from utils.CosThetaPrintUtils import printRed

warnings.filterwarnings('ignore', '.*h264.*')

from Configuration import *

CosThetaConfigurator.getInstance()


class SlaveFileLogger(SlaveLogger):
    """
    Singleton slave logger for file logging.

    Sends log messages to the file logging Redis queue where
    MasterFileLogger consumes and writes them to log files.
    """

    _instance: Optional['SlaveFileLogger'] = None
    _lock: threading.Lock = threading.Lock()
    logSource: str = ""

    def __init__(self, max_size: int = 5000, **kwargs) -> None:
        if SlaveFileLogger._instance is not None:
            raise Exception(
                f"{getFullyQualifiedName(__file__, __class__)} is a singleton class! "
                f"Access via {getFullyQualifiedName(__file__, __class__)}.getInstance()"
            )
        SlaveLogger.__init__(self, max_size, **kwargs)
        SlaveFileLogger.logSource = getFullyQualifiedName(__file__, __class__)
        self.loggingQ = CosThetaConfigurator.getInstance().getFileLoggingQueue()
        self.name = SlaveFileLogger.logSource

    @classmethod
    def getInstance(cls) -> 'SlaveFileLogger':
        """Thread-safe singleton accessor."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = SlaveFileLogger()
                    cls._instance.start()
        return cls._instance

    def logMessage(
            self,
            messageLogLevel: int,
            source: str,
            message: str,
            messageType: int = Logger.GENERAL
    ) -> bool:
        """
        Log a message to the file logging queue.

        Args:
            messageLogLevel: The severity level of the message
            source: The source/origin of the message
            message: The message content
            messageType: The type of message

        Returns:
            True if message was queued, False otherwise
        """
        SlaveFileLogger.updateLoggingLevels()

        if self.stopped:
            return False

        if messageLogLevel < SlaveFileLogger.currentFileLoggingLevel:
            return False

        formatted_message = self.makeLoggingMessage(
            Logger.getLoggingLevelText(messageLogLevel),
            source,
            self.contextualise(message),
            messageType
        )
        return self.enqueue(formatted_message)   # a full queue is counted and reported, not silently lost


    def logAudit(self, source: str, message: str) -> bool:
        """PLC audit trail (log.audit.plc): written whatever the logging level."""
        if self.stopped:
            return False
        return self.enqueue(self.makeLoggingMessage("AUDIT", source, self.contextualise(message), Logger.GENERAL))

    def logCrash(self, source: str, message: str) -> bool:
        """Uncaught exception / crash: written whatever the logging level (and even after a stop)."""
        return self.enqueue(self.makeLoggingMessage("CRASH", source, self.contextualise(message), Logger.PROBLEM))


class SlaveConsoleLogger(SlaveLogger):
    """
    Singleton slave logger for console logging.

    Sends log messages to the console logging Redis queue where
    MasterConsoleLogger consumes and prints them to the console.
    """

    _instance: Optional['SlaveConsoleLogger'] = None
    _lock: threading.Lock = threading.Lock()
    logSource: str = ""

    def __init__(self, max_size: int = 5000, **kwargs) -> None:
        if SlaveConsoleLogger._instance is not None:
            raise Exception(
                f"{getFullyQualifiedName(__file__, __class__)} is a singleton class! "
                f"Access via {getFullyQualifiedName(__file__, __class__)}.getInstance()"
            )
        SlaveLogger.__init__(self, max_size, **kwargs)
        SlaveConsoleLogger.logSource = getFullyQualifiedName(__file__, __class__)
        self.loggingQ = CosThetaConfigurator.getInstance().getConsoleLoggingQueue()
        self.name = SlaveConsoleLogger.logSource

    @classmethod
    def getInstance(cls) -> 'SlaveConsoleLogger':
        """Thread-safe singleton accessor."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = SlaveConsoleLogger()
                    cls._instance.start()
        return cls._instance

    def logMessage(
            self,
            messageLogLevel: int,
            source: str,
            message: str,
            messageType: int = Logger.GENERAL
    ) -> bool:
        """
        Log a message to the console logging queue.

        Args:
            messageLogLevel: The severity level of the message
            source: The source/origin of the message
            message: The message content
            messageType: The type of message

        Returns:
            True if message was queued, False otherwise
        """
        SlaveConsoleLogger.updateLoggingLevels()

        if self.stopped:
            printRed("Slave Console Logger stopped")
            return False

        if messageLogLevel < SlaveConsoleLogger.currentConsoleLoggingLevel:
            return False

        formatted_message = self.makeLoggingMessage(
            Logger.getLoggingLevelText(messageLogLevel),
            source,
            self.contextualise(message),
            messageType
        )
        return self.enqueue(formatted_message)   # a full queue is counted and reported, not silently lost


def stopSlaveLoggingThreads() -> None:
    """
    Gracefully stop both slave logging threads.

    Logs shutdown messages before stopping.
    """
    SlaveFileLogger.getInstance().logTakeNote(
        SlaveFileLogger.logSource,
        f'About to shutdown thread of {SlaveFileLogger.logSource}',
        Logger.RISK
    )
    SlaveConsoleLogger.getInstance().logTakeNote(
        SlaveConsoleLogger.logSource,
        f'About to shutdown thread of {SlaveConsoleLogger.logSource}',
        Logger.RISK
    )
    SlaveFileLogger.getInstance().stop()
    SlaveConsoleLogger.getInstance().stop()