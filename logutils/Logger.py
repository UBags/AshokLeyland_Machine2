"""
Logger module providing logging level constants and utility methods.
Uses Enum for type-safe log levels and message types.
"""
import threading
import warnings
from enum import IntEnum
from typing import Optional

warnings.filterwarnings('ignore', '.*h264.*')

from Configuration import *

CosThetaConfigurator.getInstance()


class LogLevel(IntEnum):
    """Enumeration of logging severity levels."""
    DEBUG = 1
    INFO = 2
    TAKENOTE = 3
    CONSIDERACTION = 4
    TAKEACTION = 5


class MessageType(IntEnum):
    """Enumeration of message display types."""
    GENERAL = 1
    SUCCESS = 2
    RISK = 3
    ISSUE = 4
    PROBLEM = 5


class Logger:
    """
    Singleton Logger class providing logging level constants and utility methods.

    Maintains backward compatibility with integer constants while using Enums internally.
    """

    # Backward-compatible integer constants
    DEBUG: int = LogLevel.DEBUG
    INFO: int = LogLevel.INFO
    TAKENOTE: int = LogLevel.TAKENOTE
    CONSIDERACTION: int = LogLevel.CONSIDERACTION
    TAKEACTION: int = LogLevel.TAKEACTION

    GENERAL: int = MessageType.GENERAL
    SUCCESS: int = MessageType.SUCCESS
    RISK: int = MessageType.RISK
    ISSUE: int = MessageType.ISSUE
    PROBLEM: int = MessageType.PROBLEM

    logSource: Optional[str] = None

    _instance: Optional['Logger'] = None
    _lock: threading.Lock = threading.Lock()

    def __init__(self) -> None:
        if Logger._instance is not None:
            raise Exception(
                f"{getFullyQualifiedName(__file__, __class__)} is a singleton class! "
                f"Access via {getFullyQualifiedName(__file__, __class__)}.getInstance()"
            )

    @classmethod
    def getInstance(cls) -> 'Logger':
        """Thread-safe singleton accessor."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = Logger()
                    cls.logSource = getFullyQualifiedName(__file__, cls)
        return cls._instance

    @classmethod
    def getLoggingLevelInt(cls, inputLevel: str) -> int:
        """
        Convert logging level string to integer.

        Args:
            inputLevel: String representation of log level

        Returns:
            Integer log level, defaults to TAKENOTE if invalid
        """
        level_map = {
            'DEBUG': LogLevel.DEBUG,
            'INFO': LogLevel.INFO,
            'TAKE_NOTE': LogLevel.TAKENOTE,
            'CONSIDER_ACTION': LogLevel.CONSIDERACTION,
            'TAKE_ACTION': LogLevel.TAKEACTION,
        }
        return level_map.get(inputLevel, LogLevel.TAKENOTE)

    @classmethod
    def getLoggingLevelText(cls, inputLevel: int) -> str:
        """
        Convert logging level integer to string.

        Args:
            inputLevel: Integer log level

        Returns:
            String representation of log level
        """
        level_map = {
            LogLevel.DEBUG: "DEBUG",
            LogLevel.INFO: "INFO",
            LogLevel.TAKENOTE: "TAKE_NOTE",
            LogLevel.CONSIDERACTION: "CONSIDER_ACTION",
            LogLevel.TAKEACTION: "TAKE_ACTION",
        }
        return level_map.get(inputLevel, "TAKE_NOTE")

    @classmethod
    def getMessageTypeText(cls, inputLevel: int) -> str:
        """
        Convert message type integer to string.

        Args:
            inputLevel: Integer message type

        Returns:
            String representation of message type
        """
        type_map = {
            MessageType.GENERAL: "GENERAL",
            MessageType.SUCCESS: "SUCCESS",
            MessageType.RISK: "RISK",
            MessageType.ISSUE: "ISSUE",
            MessageType.PROBLEM: "PROBLEM",
        }
        return type_map.get(inputLevel, "GENERAL")

    @classmethod
    def getMessageType(cls, message: str) -> int:
        """
        Extract message type from a log message string.

        Args:
            message: Log message string containing type indicator

        Returns:
            Integer message type
        """
        # Check in order of severity (most severe first for efficiency in error cases)
        if "PROBLEM" in message:
            return MessageType.PROBLEM
        if "ISSUE" in message:
            return MessageType.ISSUE
        if "RISK" in message:
            return MessageType.RISK
        if "SUCCESS" in message:
            return MessageType.SUCCESS
        return MessageType.GENERAL