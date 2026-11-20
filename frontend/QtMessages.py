# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""
QtMessages - Qt's own messages (warnings, criticals, fatals) go to the application's log, not to the C stderr
(CHANGES 176).

Why: without a message handler, Qt writes its messages itself, through the C runtime's stderr. In the Nuitka exe
the frontend crashed (exit code 0xC0000409, fast fail in ucrtbase.dll, nothing printed, the faulthandler file empty)
as soon as Indic text was laid out - at start in Kannada / Hindi, or at the change of language - and never in English
or when run from PyCharm. Qt prints "qt.text.font.db: OpenType support missing for "Courier", script 18" (11 / 16 /
18 = Devanagari / Tamil / Kannada) whenever it lays out an Indic text in a font that has no tables for that script
(the tiles use Courier; Qt then falls back to Nirmala UI through FrontendI18n.installIndicFonts); in English it prints
nothing. That write to the C stderr - reopened by Nuitka's console attach (HelpersConsole.c) - was the one C-runtime
write that happened only with an Indic language.

The handler installed here takes every Qt message in Python: each distinct message (type, category, text) is logged
once through logBoth (to the log file and the console, both through Python and the logging server), later repeats
only counted; a fatal message is logged with the Python stack before Qt aborts; critical and fatal messages are also
written to Python's sys.stderr (WriteConsoleW in the exe, not the C runtime), so they are on the console even when
the logging server does not have them yet. Qt no longer writes to the C stderr.
Install it before the QApplication exists (getAppInstance does).
"""
import threading
import traceback
from typing import Dict, Tuple

_installed = False
_counts: Dict[Tuple[str, str, str], int] = {}
_lock = threading.Lock()
_inHandler = threading.local()
LOG_SOURCE = "frontend.QtMessages"


def _typeName(msgType) -> str:
    from PySide6.QtCore import QtMsgType
    names = {QtMsgType.QtDebugMsg: "debug", QtMsgType.QtInfoMsg: "info", QtMsgType.QtWarningMsg: "warning",
             QtMsgType.QtCriticalMsg: "critical", QtMsgType.QtFatalMsg: "fatal"}
    return names.get(msgType, str(msgType))


def _levelFor(typeName: str) -> str:
    return {"debug": "logDebug", "info": "logInfo", "warning": "logWarning", "critical": "logError",
            "fatal": "logCritical"}.get(typeName, "logWarning")


def handleQtMessage(msgType, context, message) -> None:
    """The Qt message handler. Never raises (an exception here would go back into Qt's C++)."""
    if getattr(_inHandler, "active", False):     # a Qt message while logging one: dropped, no recursion
        return
    _inHandler.active = True
    try:
        typeName = _typeName(msgType)
        category = str(getattr(context, "category", "") or "default")
        text = str(message)
        key = (typeName, category, text)
        with _lock:
            count = _counts.get(key, 0) + 1
            _counts[key] = count
        if count > 1 and typeName != "fatal":
            return                                # repeats counted only (Indic layout repeats them hundreds of times)
        line = f"Qt {typeName} ({category}): {text}"
        if count == 1 and typeName != "fatal":
            line += "  [repeats are counted, not logged]"
        if typeName == "fatal":
            line += "\n" + "".join(traceback.format_stack())
        try:
            from logutils.SlaveLoggers import logBoth
            from logutils.Logger import Logger
            logBoth(_levelFor(typeName), LOG_SOURCE, line, Logger.PROBLEM if typeName in ("critical", "fatal")
                    else Logger.ISSUE if typeName == "warning" else Logger.GENERAL)
        except Exception:
            pass
        if typeName in ("critical", "fatal"):
            # also straight to the console through Python's sys.stderr (Python's own writer, not the C stderr): a
            # fatal message comes just before Qt aborts, maybe before the logging server has it (e.g. "no Qt
            # platform plugin could be initialized")
            try:
                import sys
                sys.stderr.write(line + "\n")
                sys.stderr.flush()
            except Exception:
                pass
    except Exception:
        pass
    finally:
        _inHandler.active = False


def installQtMessageHandler() -> bool:
    """Install handleQtMessage as Qt's message handler (once per process). True if installed now or before."""
    global _installed
    if _installed:
        return True
    try:
        from PySide6.QtCore import qInstallMessageHandler
        qInstallMessageHandler(handleQtMessage)
        _installed = True
    except Exception:
        _installed = False
    return _installed


def messageCounts() -> Dict[Tuple[str, str, str], int]:
    """A copy of the counts per (type, category, text) - for the dry run and the shutdown summary."""
    with _lock:
        return dict(_counts)
