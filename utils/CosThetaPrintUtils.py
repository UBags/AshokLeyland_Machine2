"""
Print utilities for colored and formatted console output.
"""
from datetime import datetime
from typing import Any

from utils.CosThetaColors import CosThetaColors
# Import Logger lazily to avoid circular imports
# from logutils.Logger import Logger


def getCurrentTime() -> str:
    """
    Get current time formatted as string with milliseconds.

    Returns:
        Formatted time string: 'YYYY-MM-DD-HH-MM-SS-mmm'
    """
    msTime = datetime.now().strftime('%Y-%m-%d-%H-%M-%S.%f')
    dt, ms = msTime.split('.')
    ms_int = int(ms) // 1000
    return f'{dt}-{ms_int:03}'


def printWithTime(*args: Any) -> None:
    """Print with timestamp prefix."""
    currentTime = f'{getCurrentTime()} : '
    print(currentTime, *args, end="")


def printRed(*args: Any, includeTime: bool = True) -> None:
    """Print text in red."""
    print(CosThetaColors.CRED, end="")
    if includeTime:
        printWithTime(*args)
    else:
        print(*args, end="")
    print(CosThetaColors.CEND)


def printBoldRed(*args: Any, includeTime: bool = True) -> None:
    """Print text in bold red."""
    print(CosThetaColors.CRED, end="")
    print(CosThetaColors.CBOLD, end="")
    if includeTime:
        printWithTime(*args)
    else:
        print(*args, end="")
    print(CosThetaColors.CEND)


def printGreen(*args: Any, includeTime: bool = True) -> None:
    """Print text in green."""
    print(CosThetaColors.CGREEN, end="")
    if includeTime:
        printWithTime(*args)
    else:
        print(*args, end="")
    print(CosThetaColors.CEND)


def printBoldGreen(*args: Any, includeTime: bool = True) -> None:
    """Print text in bold green."""
    print(CosThetaColors.CGREEN, end="")
    print(CosThetaColors.CBOLD, end="")
    if includeTime:
        printWithTime(*args)
    else:
        print(*args, end="")
    print(CosThetaColors.CEND)


def printBlue(*args: Any, includeTime: bool = True) -> None:
    """Print text in blue."""
    print(CosThetaColors.CBLUE, end="")
    if includeTime:
        printWithTime(*args)
    else:
        print(*args, end="")
    print(CosThetaColors.CEND)


def printBoldBlue(*args: Any, includeTime: bool = True) -> None:
    """Print text in bold blue."""
    print(CosThetaColors.CBLUE, end="")
    print(CosThetaColors.CBOLD, end="")
    if includeTime:
        printWithTime(*args)
    else:
        print(*args, end="")
    print(CosThetaColors.CEND)


def printYellow(*args: Any, includeTime: bool = True) -> None:
    """Print text in yellow."""
    print(CosThetaColors.CYELLOW, end="")
    if includeTime:
        printWithTime(*args)
    else:
        print(*args, end="")
    print(CosThetaColors.CEND)


def printBoldYellow(*args: Any, includeTime: bool = True) -> None:
    """Print text in bold yellow."""
    print(CosThetaColors.CYELLOW, end="")
    print(CosThetaColors.CBOLD, end="")
    if includeTime:
        printWithTime(*args)
    else:
        print(*args, end="")
    print(CosThetaColors.CEND)


def printBold(*args: Any, includeTime: bool = True) -> None:
    """Print text in bold black."""
    print(CosThetaColors.CBLACK, end="")
    print(CosThetaColors.CBOLD, end="")
    if includeTime:
        printWithTime(*args)
    else:
        print(*args, end="")
    print(CosThetaColors.CEND)


def printPlain(*args: Any, includeTime: bool = True) -> None:
    """Print text in plain black."""
    print(CosThetaColors.CBLACK, end="")
    if includeTime:
        printWithTime(*args)
    else:
        print(*args, end="")
    print(CosThetaColors.CEND)


def printLight(*args: Any, includeTime: bool = True) -> None:
    """Print text in grey (dim)."""
    print(CosThetaColors.CGREY, end="")
    if includeTime:
        printWithTime(*args)
    else:
        print(*args, end="")
    print(CosThetaColors.CEND)


def printBoldSeparator() -> None:
    """Print a bold separator line."""
    print(CosThetaColors.CBLACK, end="")
    print(CosThetaColors.CBOLD, end="")
    print('------------------------------------------------------------------------')
    print(CosThetaColors.CEND)


def printSeparator() -> None:
    """Print a separator line."""
    print(CosThetaColors.CBLACK, end="")
    print('------------------------------------------------------------------------')
    print(CosThetaColors.CEND)


def printInColor(message: str, messageType: int) -> None:
    """
    Print a message in color based on message type.

    Args:
        message: The message to print
        messageType: Integer message type (1=GENERAL, 2=SUCCESS, 3=RISK, 4=ISSUE, 5=PROBLEM)
    """
    # Import here to avoid circular import
    from logutils.Logger import Logger

    if messageType == Logger.GENERAL:
        printLight(message, includeTime=False)
    elif messageType == Logger.SUCCESS:
        printBoldGreen(message, includeTime=False)
    elif messageType == Logger.RISK:
        printBoldYellow(message, includeTime=False)
    elif messageType == Logger.ISSUE:
        printBoldRed(message, includeTime=False)
    elif messageType == Logger.PROBLEM:
        printBoldRed(message, includeTime=False)
    else:
        printBold(message, includeTime=False)