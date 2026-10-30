# # import ctypes
# import subprocess
# import threading
#
# kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
#
# ctrl_cancel_event = threading.Event()
# ctrl_break_event = threading.Event()
# ctrl_close_event = threading.Event()
# exit_event = threading.Event()
#
# @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_ulong)
# def ctrl_handler(event_type):
#     if event_type == 0:
#         # CTRL_C_EVENT
#         ctrl_cancel_event.set()
#     elif event_type == 1:
#         # CTRL_BREAK_EVENT
#         ctrl_break_event.set()
#     else:
#         # CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT
#         ctrl_close_event.set()
#         # Give cleanup handlers time to exit.
#         exit_event.wait()
#     return True
#
#
# def allocate_console(create_window=True):
#     if create_window:
#         if not kernel32.AllocConsole():
#             raise ctypes.WinError(ctypes.get_last_error())
#         if not kernel32.SetConsoleCtrlHandler(ctrl_handler, True):
#             raise ctypes.WinError(ctypes.get_last_error())
#         return
#     with subprocess.Popen(
#             'echo ready & set /p=',
#             shell=True, creationflags=subprocess.CREATE_NO_WINDOW,
#             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
#             bufsize=0) as p_shell:
#         try:
#             p_shell.stdout.read(1)
#             if not kernel32.AttachConsole(p_shell.pid):
#                 raise ctypes.WinError(ctypes.get_last_error())
#             if not kernel32.SetConsoleCtrlHandler(ctrl_handler, True):
#                 raise ctypes.WinError(ctypes.get_last_error())
#         finally:
#             p_shell.terminate()
#
# allocate_console()

import logging
from BaseUtils import *
from utils.CosThetaFileUtils import *

# logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)
#
# loggingFile = get_project_root() + '/logs/logfile.log'
#
# createDirectory(loggingFile)
#
# print(loggingFile)
#
# print(type(logger))
# file_handler = logging.FileHandler(loggingFile)
# formatter = logging.Formatter('%(asctime)s : %(levelname)s : %(name)s : %(message)s')
# file_handler.setFormatter(formatter)
#
# # add file handler to logger
# logger.addHandler(file_handler)
# logger.debug("Made it")

loggingFile = get_project_root() + '/logs/logfile.log'
createDirectory((loggingFile))

logging.basicConfig(filename=loggingFile, filemode='a', level=logging.DEBUG,
                    format='%(asctime)s : %(levelname)s : %(name)s : %(message)s')

logging.warning("Something")