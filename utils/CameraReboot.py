"""
Camera reboot from the PC (CHANGES 177): the Hikvision ISAPI call

    PUT http://<vision.camera.ip>:<vision.camera.port>/ISAPI/System/reboot     (HTTP digest authentication)

with the camera's own user and password (<vendor>.vision.camera.uid / .pwd) - the same authentication as the camera
server's ISAPI keepalive (camera/RTSPCam.py, GET /ISAPI/System/deviceInfo). The camera answers at once with an XML
ResponseStatus (statusCode 1 / statusString OK) and then restarts; the stream is back after about a minute or two, and
the camera server's own reconnect thread (RTSPCam.launchMonitorCameraConnectionThread) picks it up - nothing in the
camera server changes. Started by the frontend from Machine Settings (frontend/SimplePopups.py: the Reboot Camera
button, only between components, the machine settings password as the final confirmation).
"""
import re
from typing import Tuple

from Configuration import CosThetaConfigurator

REBOOT_PATH = "/ISAPI/System/reboot"
TIMEOUT_SEC = CosThetaConfigurator.getTuning("camera.reboot.timeout.sec", 10, minimum=1)
LOG_SOURCE = "utils.CameraReboot"


def rebootUrl(ip: str | None = None, port: int | None = None) -> str:
    c = CosThetaConfigurator
    ip = ip or c.getCameraIP()
    port = int(port or c.getCameraPort())
    return f"http://{ip}{'' if port == 80 else f':{port}'}{REBOOT_PATH}"


def _statusOf(text: str) -> Tuple[str, str]:
    """(statusCode, statusString) of an ISAPI ResponseStatus, '' where absent."""
    code = re.search(r"<statusCode>\s*([^<]*?)\s*</statusCode>", text or "")
    words = re.search(r"<statusString>\s*([^<]*?)\s*</statusString>", text or "")
    return (code.group(1) if code else ""), (words.group(1) if words else "")


def rebootCamera(ip: str | None = None, port: int | None = None, uid: str | None = None, pwd: str | None = None,
                 timeout: float | None = None) -> Tuple[bool, str]:
    """Ask the camera to reboot. (True, line) when it accepted (HTTP 200, statusCode 1 or none given), else
    (False, why). Never raises. Logged either way."""
    import requests
    from requests.auth import HTTPDigestAuth
    c = CosThetaConfigurator
    url = rebootUrl(ip, port)
    uid = uid if uid is not None else c.getCameraUid()
    pwd = pwd if pwd is not None else c.getCameraPwd()
    try:
        response = requests.put(url, auth=HTTPDigestAuth(uid, pwd), timeout=float(timeout or TIMEOUT_SEC))
    except Exception as e:
        ok, line = False, f"Camera reboot: no answer from {url} ({type(e).__name__}: {e})"
    else:
        code, words = _statusOf(response.text)
        if response.status_code == 200 and code in ("", "1"):
            ok, line = True, f"Camera reboot: accepted by {url} ({words or 'HTTP 200'})"
        elif response.status_code == 401:
            ok, line = False, f"Camera reboot: refused by {url} - user / password not accepted (HTTP 401)"
        else:
            ok, line = False, (f"Camera reboot: refused by {url} (HTTP {response.status_code}"
                               + (f", statusCode {code} {words}" if code or words else "") + ")")
    _log(ok, line)
    return ok, line


def _log(ok: bool, line: str):
    try:
        from logutils.SlaveLoggers import logBoth
        from logutils.Logger import Logger
        logBoth('logTakeAction' if ok else 'logTakeNote', LOG_SOURCE, line, Logger.SUCCESS if ok else Logger.PROBLEM)
    except Exception:
        print(line, flush=True)
