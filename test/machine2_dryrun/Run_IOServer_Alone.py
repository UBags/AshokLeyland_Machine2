"""Starts the IOServer ON ITS OWN (with the logging server), for Test_IOServer_With_Real_PLC_UI.py.

Not MainProgram.py: that also starts the camera server and the heartbeat server, which answer the pictures themselves
(camera unplugged: every picture NOT OK at once). Here only the IOServer runs, connected to the PLC in the properties
file (plc.vendor, IP, ...); the window then supplies the QR code and the camera answers over Redis. No heartbeat
server runs, so the IOServer applies no server-down rules. Stop with Ctrl+C.

    python test/<this folder>/Run_IOServer_Alone.py
"""
import sys, pathlib, multiprocessing
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

def _loggingServer():                    # top level: Windows starts processes with 'spawn'
    sys.path.insert(0, str(ROOT))
    from logutils.LogSettings import setLogProcess
    setLogProcess("logging")
    from logutils.CentralLoggers import startLoggers
    startLoggers()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    loggers = multiprocessing.Process(target=_loggingServer, name="Logging Server", daemon=True)
    loggers.start()
    from logutils.LogSettings import setLogProcess
    setLogProcess("ioserver")               # as MainProgram.startTheIOServer: per-server logging settings
    from costhetaio.commonplc.PLCIOServer import startIOServer
    try:
        startIOServer(mode="TEST")
    except KeyboardInterrupt:
        print("IOServer stopped (Ctrl+C)")
