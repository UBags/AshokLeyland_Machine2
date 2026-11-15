"""
Core logging initialization using Python's built-in logging module.
Provides timed rotating file handlers and console handlers.
"""
import codecs
import logging
import logging.handlers
import os
import time

import regex as re

from utils.CosThetaPrintUtils import printBoldBlue, printBoldGreen, printBoldRed
from BaseUtils import get_project_root

from Configuration import CosThetaConfigurator

CosThetaConfigurator.getInstance()

# Module-level state
_logging_initialized: bool = False


class MyTimedRotatingFileHandler(logging.handlers.TimedRotatingFileHandler):
    """
    Custom TimedRotatingFileHandler that rotates logs at midnight
    with date-based filenames.
    """

    def __init__(self, dir_log: str) -> None:
        """
        Initialize the handler.

        Args:
            dir_log: Directory path for log files (must end with os.sep)
        """
        self.dir_log = dir_log
        filename = self.dir_log + time.strftime("%m%d%Y") + ".txt"
        logging.handlers.TimedRotatingFileHandler.__init__(
            self,
            filename,
            when='midnight',
            interval=1,
            backupCount=0,
            encoding=None
        )

    def doRollover(self) -> None:
        """
        Perform log rollover at midnight.
        Creates a new log file with current date in filename.
        """
        self.stream.close()

        # Get the time that this sequence started at
        t = self.rolloverAt - self.interval
        timeTuple = time.localtime(t)

        self.baseFilename = self.dir_log + time.strftime("%m%d%Y") + ".txt"

        if self.encoding:
            self.stream = codecs.open(self.baseFilename, 'a', self.encoding)
        else:
            self.stream = open(self.baseFilename, 'a')

        self.rolloverAt = self.rolloverAt + self.interval


def getCoreLoggingLevel(inputLevel: str) -> int:
    """
    Convert string log level to logging module constant.

    Args:
        inputLevel: Log level string ('DEBUG', 'INFO', 'WARN', 'ERROR', 'CRITICAL')

    Returns:
        Corresponding logging module constant
    """
    level_map = {
        'DEBUG': logging.DEBUG,
        'INFO': logging.INFO,
        'WARN': logging.WARN,
        'ERROR': logging.ERROR,
        'CRITICAL': logging.CRITICAL,
    }
    return level_map.get(inputLevel, logging.WARN)


def initialiseLogging() -> bool:
    """
    Initialize the logging system with console and file handlers.

    Sets up:
    - Console handler with configurable level and format
    - Timed rotating file handler with configurable level and format

    Returns:
        True if initialization successful, False otherwise
    """
    global _logging_initialized

    if _logging_initialized:
        return True

    try:
        root_logger = logging.getLogger('')
        root_logger.setLevel(logging.DEBUG)

        # Setup console handler
        consoleLogLevel = getCoreLoggingLevel(
            CosThetaConfigurator.getInstance().getConsoleLoggingLevel()
        )
        consoleFormatter = logging.Formatter(
            CosThetaConfigurator.getInstance().getConsoleLoggingFormat()
        )
        console = logging.StreamHandler()
        console.setLevel(consoleLogLevel)
        printBoldBlue(f"Set console logging level to {consoleLogLevel}")
        console.setFormatter(consoleFormatter)
        root_logger.addHandler(console)

        # Setup file handler
        fileFormatter = logging.Formatter(
            fmt=CosThetaConfigurator.getInstance().getFileLoggingFormat(),
            datefmt=CosThetaConfigurator.getInstance().getFileLoggingDateFormat()
        )

        curr_time = time.strftime("%Y-%m-%d-%H-%M-%S", time.localtime())
        log_dir = CosThetaConfigurator.getInstance().getLoggingDirectory()
        app_name = CosThetaConfigurator.getInstance().getApplicationName()
        suffix = CosThetaConfigurator.getInstance().getFileLoggingSuffix()

        logFileName = f"{get_project_root()}/{log_dir}/{app_name}-{curr_time}.{suffix}"

        try:
            os.makedirs(os.path.dirname(logFileName), exist_ok=True)
        except Exception:
            logFileName = f"{get_project_root()}/{app_name}-{curr_time}.{suffix}"

        filehandler = logging.handlers.TimedRotatingFileHandler(
            logFileName,
            when='midnight',
            interval=1,
            backupCount=CosThetaConfigurator.getInstance().getBackupLogsCount()
        )
        filehandler.suffix = ""
        filehandler.extMatch = re.compile(r"^\d{8}$")

        fileLoggingLevel = getCoreLoggingLevel(
            CosThetaConfigurator.getInstance().getFileLoggingLevel()
        )
        filehandler.setLevel(fileLoggingLevel)
        filehandler.setFormatter(fileFormatter)
        root_logger.addHandler(filehandler)

        printBoldBlue(f"Root logger name is {root_logger}")
        printBoldBlue(f"Set file logging level to {fileLoggingLevel}")
        printBoldGreen(f'Created logging file {filehandler.baseFilename}')

        _logging_initialized = True

        # Set root logger to minimum of both levels
        root_logger.setLevel(min(consoleLogLevel, fileLoggingLevel))

        return True

    except KeyboardInterrupt:
        printBoldRed("Initialise Logging cancelled by user")
        return False
    except Exception as ex:
        printBoldRed(f"Unhandled error\n{ex} in initialise logging")
        raise


def reinitialiseLogging() -> bool:
    """
    Shutdown and reinitialize the logging system.

    Returns:
        True if reinitialization successful, False otherwise
    """
    global _logging_initialized

    try:
        logging.shutdown()
    except Exception as ex:
        printBoldRed(f"Could not shutdown logging due to {ex}")

    _logging_initialized = False
    printBoldBlue("Reinitialising logging")

    return initialiseLogging()


def isLoggingInitialized() -> bool:
    """Check if logging has been initialized."""
    return _logging_initialized