"""
noteSuppressed() - leave a trace of an exception that the code catches and deliberately ignores.

Used in place of a bare `pass` in `except ...:` blocks. Behaviour of the code is unchanged (the exception is still
swallowed); the log gets one DEBUG line per place where it happens: the first time, then every
REPEAT_EVERY-th time with the running count, so a busy loop cannot flood the log.
Controlled by log.suppressed.exceptions (default True); active only in server processes (setLogProcess), so tools and
tests are unaffected. Never raises.
"""
import sys
import threading

REPEAT_EVERY = 100
_counts = {}
_lock = threading.Lock()
_busy = threading.local()


def noteSuppressed() -> None:
    if getattr(_busy, "on", False):
        return
    _busy.on = True
    try:
        excType, excValue, _ = sys.exc_info()
        if excType is None:
            return
        from logutils.LogSettings import logSuppressedExceptions
        if not logSuppressedExceptions():
            return
        frame = sys._getframe(1)
        site = f"{frame.f_globals.get('__name__', '?')}.{frame.f_code.co_name}:{frame.f_lineno}"
        with _lock:
            n = _counts.get(site, 0) + 1
            _counts[site] = n
        if n != 1 and n % REPEAT_EVERY != 0:
            return
        from logutils.Logger import Logger
        from logutils.SlaveLoggers import SlaveFileLogger
        from logutils.AbstractSlaveLogger import callerOverride
        callerOverride.value = (frame.f_code.co_name, frame.f_lineno)
        try:
            SlaveFileLogger.getInstance().logMessage(
                Logger.DEBUG, frame.f_globals.get("__name__", "?"),
                f"suppressed {excType.__name__}: {excValue}" + (f" (occurrence {n})" if n > 1 else ""),
                Logger.GENERAL)
        finally:
            callerOverride.value = None
    except Exception:
        pass
    finally:
        _busy.on = False


def suppressedCounts() -> dict:
    with _lock:
        return dict(_counts)
