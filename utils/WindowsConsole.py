"""
Turns on ANSI colour (escape-sequence) processing in the Windows console.

Without it, a console started by cmd.exe shows the colour codes of the coloured prints and
log lines as text, e.g. "<-[32m<-[1m...<-[0m". The setting belongs to the console window,
which every process of the application shares (multiprocessing spawn inherits it), so
enabling it once is enough; calling it again is harmless.

Does nothing on other operating systems, or when output is not a console (redirected to a
file or pipe).
"""
import os

ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
STD_OUTPUT_HANDLE = -11
STD_ERROR_HANDLE = -12

_enabled = None   # None = not tried yet; True / False = result of the first try


def enableAnsiColours() -> bool:
    """Turns on ANSI colour processing for stdout and stderr. True if at least one is on."""
    global _enabled
    if _enabled is not None:
        return _enabled
    if os.name != 'nt':
        _enabled = True
        return True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetConsoleMode.restype = wintypes.BOOL
        kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.SetConsoleMode.restype = wintypes.BOOL

        invalid = {None, 0, ctypes.c_void_p(-1).value}
        anyOn = False
        for which in (STD_OUTPUT_HANDLE, STD_ERROR_HANDLE):
            handle = kernel32.GetStdHandle(wintypes.DWORD(which & 0xFFFFFFFF))
            if handle in invalid:
                continue
            mode = wintypes.DWORD()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                continue   # not a console (redirected)
            if mode.value & ENABLE_VIRTUAL_TERMINAL_PROCESSING:
                anyOn = True
            elif kernel32.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING):
                anyOn = True
        _enabled = anyOn
    except Exception:
        _enabled = False
    return _enabled
