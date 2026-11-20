"""
DryRun_Camera_Reboot - the camera reboot of Machine Settings (CHANGES 177), against a stand-in camera.

A local HTTP server plays the Hikvision camera: it asks for HTTP digest authentication (realm, nonce, qop=auth, MD5,
as the camera does), checks the response, and answers PUT /ISAPI/System/reboot with an ISAPI ResponseStatus.

R1  the URL from the properties: http://<vision.camera.ip>/ISAPI/System/reboot (port 80 left out, others kept);
    camera.reboot.timeout.sec in the properties equals the code default
R2  right user / password: rebootCamera() -> accepted; the stand-in saw exactly one authenticated PUT on
    /ISAPI/System/reboot (and the first, unauthenticated, try answered 401 - digest as on the camera)
R3  wrong password -> refused (HTTP 401), no reboot
R4  camera answers 200 with statusCode 4 (Invalid Operation) -> refused; HTTP 403 -> refused
R5  nothing listening -> "no answer", within the timeout, never raises
R6  the button's flow (frontend/SimplePopups.rebootCameraFromMachineSettings, dialogs stood in):
    a component in the cycle -> no password asked, no call; cycle state unknown (None) / raising -> blocked;
    password cancelled / empty / wrong -> no call; right password -> one call, the result shown;
    a component arriving while the password is typed -> no call; the camera refusing -> the error shown
R7  the dialog has the Reboot Camera button (disabled without cycleIdle) and handles its event; the frontend passes
    cycleIdle = (state READ_QR_CODE) to updateMachineSettingsDialog (AST)
R8  the new texts are in the kn / ta / hi catalogues with a translation, their {placeholders} kept

Needs Python with tkinter, requests and FreeSimpleGUI (the sandbox: venv312). No camera, PLC, Redis or database.
Run from the project root:   python test/machine1_dryrun/DryRun_Camera_Reboot.py
"""
import ast, hashlib, os, pathlib, re, socket, sys, threading, time, types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("COSTHETA_PLC_VENDOR", "allenbradley" if "machine1" in str(pathlib.Path(__file__).parent) else "siemens")
if sys.platform != 'win32':
    sys.modules['winsound'] = types.SimpleNamespace(PlaySound=lambda *a, **k: None, SND_FILENAME=0, SND_ASYNC=0,
                                                    Beep=lambda *a: None)

from Configuration import CosThetaConfigurator as C
import utils.CameraReboot as CR

passed, failed = 0, 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name}  {detail}")


# ------------------------------------------------------------------------------------------------- the stand-in camera
USER, PASSWORD, REALM, NONCE = "admin", "Secret#123", "DS-2CD", "4e6f6e6365303030"
OK_BODY = ('<?xml version="1.0" encoding="UTF-8"?><ResponseStatus version="2.0" xmlns="http://www.isapi.org/ver20/XMLSchema">'
           '<requestURL>/ISAPI/System/reboot</requestURL><statusCode>1</statusCode><statusString>OK</statusString>'
           '<subStatusCode>ok</subStatusCode></ResponseStatus>')
seen = []                 # (method, path, authenticated)
behaviour = {"mode": "ok"}


def md5(text):
    return hashlib.md5(text.encode()).hexdigest()


class Camera(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _answer(self, code, body="", headers=()):
        self.send_response(code)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Type", "application/xml")
        self.send_header("Content-Length", str(len(body.encode())))
        self.end_headers()
        self.wfile.write(body.encode())

    def _authenticated(self):
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Digest "):
            return False
        f = dict(re.findall(r'(\w+)="?([^",]*)"?', auth[7:]))
        ha1 = md5(f"{f.get('username')}:{REALM}:{PASSWORD}")
        ha2 = md5(f"{self.command}:{f.get('uri')}")
        expected = md5(f"{ha1}:{NONCE}:{f.get('nc')}:{f.get('cnonce')}:{f.get('qop')}:{ha2}")
        return f.get("username") == USER and f.get("response") == expected and f.get("uri") == self.path

    def do_PUT(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        ok = self._authenticated()
        seen.append((self.command, self.path, ok))
        if not ok:
            return self._answer(401, "", [("WWW-Authenticate", f'Digest qop="auth", realm="{REALM}", nonce="{NONCE}", stale="FALSE"')])
        if self.path != "/ISAPI/System/reboot":
            return self._answer(404)
        if behaviour["mode"] == "invalid":
            return self._answer(200, OK_BODY.replace("<statusCode>1<", "<statusCode>4<").replace(">OK<", ">Invalid Operation<"))
        if behaviour["mode"] == "forbidden":
            return self._answer(403, "")
        return self._answer(200, OK_BODY)

    do_GET = do_PUT


server = ThreadingHTTPServer(("127.0.0.1", 0), Camera)
PORT = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()


def freePort():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


print("R1 the URL and the timeout key")
ip = C.getCameraIP()
check("URL from the properties", CR.rebootUrl() == (f"http://{ip}/ISAPI/System/reboot" if int(C.getCameraPort()) == 80
                                                     else f"http://{ip}:{C.getCameraPort()}/ISAPI/System/reboot"), CR.rebootUrl())
check("port 80 left out, another kept", CR.rebootUrl("1.2.3.4", 80) == "http://1.2.3.4/ISAPI/System/reboot"
      and CR.rebootUrl("1.2.3.4", 8080) == "http://1.2.3.4:8080/ISAPI/System/reboot")
props = (ROOT / "ApplicationConfiguration.properties").read_text(encoding="utf-8", errors="replace")
check("camera.reboot.timeout.sec in the properties = code default 10",
      re.search(r"^camera\.reboot\.timeout\.sec=10\s*$", props, re.M) is not None and CR.TIMEOUT_SEC == 10)

print("R2 right user / password")
seen.clear(); behaviour["mode"] = "ok"
ok, line = CR.rebootCamera("127.0.0.1", PORT, USER, PASSWORD)
check("accepted", ok and "accepted" in line, line)
check("first try 401 (no auth), then one authenticated PUT /ISAPI/System/reboot",
      seen == [("PUT", "/ISAPI/System/reboot", False), ("PUT", "/ISAPI/System/reboot", True)], seen)

print("R3 wrong password")
seen.clear()
ok, line = CR.rebootCamera("127.0.0.1", PORT, USER, "wrong")
check("refused, HTTP 401", not ok and "401" in line, line)
check("no authenticated request", not any(a for _, _, a in seen), seen)

print("R4 the camera refuses")
behaviour["mode"] = "invalid"
ok, line = CR.rebootCamera("127.0.0.1", PORT, USER, PASSWORD)
check("statusCode 4 -> refused", not ok and "statusCode 4" in line, line)
behaviour["mode"] = "forbidden"
ok, line = CR.rebootCamera("127.0.0.1", PORT, USER, PASSWORD)
check("HTTP 403 -> refused", not ok and "403" in line, line)
behaviour["mode"] = "ok"

print("R5 nothing listening")
started = time.monotonic()
try:
    ok, line = CR.rebootCamera("127.0.0.1", freePort(), USER, PASSWORD, timeout=2)
    raised = False
except Exception as e:
    ok, line, raised = False, repr(e), True
check("no answer, not raised, within the timeout", not ok and not raised and "no answer" in line
      and time.monotonic() - started < 5, line)

print("R6 the button's flow")
import frontend.SimplePopups as S
calls, shown, asked = [], [], []
state = {"idle": True, "password": None, "turnBusy": False, "result": (True, "Camera reboot: accepted (OK)")}


def fakePassword(text="", modal=False):
    asked.append(text)
    if state["turnBusy"]:
        state["idle"] = False
    return state["password"]


S.getJustPassword = fakePassword
S.showMessage = lambda text, *a, **k: shown.append(("message", text))
S.showErrorMessage = lambda text, *a, **k: shown.append(("error", text))
CR.rebootCamera = lambda *a, **k: calls.append(1) or state["result"]
right = S.machineSettingsPassword()


def run(idle=True, password=None, turnBusy=False, result=(True, "Camera reboot: accepted (OK)"), cycleIdle="state"):
    calls.clear(); shown.clear(); asked.clear()
    state.update(idle=idle, password=password, turnBusy=turnBusy, result=result)
    return S.rebootCameraFromMachineSettings((lambda: state["idle"]) if cycleIdle == "state" else cycleIdle, "tester")


r = run(idle=False, password=right)
check("component in the cycle: no password asked, no call, reason shown",
      not r and not asked and not calls and shown and shown[0][0] == "error" and "between components" in shown[0][1], (asked, calls, shown))
r = run(password=right, cycleIdle=None)
check("cycle state unknown (None): blocked", not r and not asked and not calls)
r = run(password=right, cycleIdle=lambda: 1 / 0)
check("cycle state raising: blocked", not r and not asked and not calls)
for pw, name in ((None, "cancelled"), ("", "empty"), ("not-it", "wrong")):
    r = run(password=pw)
    check(f"password {name}: no call", not r and asked and not calls
          and (name != "wrong" or any("Wrong password" in t for _, t in shown)), (calls, shown))
r = run(password=right)
check("right password: one call, accepted shown", r and len(calls) == 1 and shown and shown[-1][0] == "message"
      and "rebooting" in shown[-1][1], (calls, shown))
check("the password dialog says it is the final confirmation and names the camera IP",
      asked and "final confirmation" in asked[0] and C.getCameraIP() in asked[0], asked)
r = run(password=right, turnBusy=True)
check("component arriving while the password is typed: no call", not r and not calls, (calls, shown))
r = run(password=right, result=(False, "Camera reboot: refused (HTTP 401)"))
check("camera refusing: error shown with the reason", not r and len(calls) == 1 and shown[-1][0] == "error"
      and "HTTP 401" in shown[-1][1], shown)

print("R7 the dialog and the frontend")
src = (ROOT / "frontend" / "SimplePopups.py").read_text(encoding="utf-8")
dialog = src[src.index("def updateMachineSettingsDialog"):src.index("# ****************************************** End - Machine Settings")]
check("button RebootCamera, disabled without cycleIdle", 'key="RebootCamera", disabled=cycleIdle is None' in dialog)
check("event handled by rebootCameraFromMachineSettings", 'if event == "RebootCamera":' in dialog
      and "rebootCameraFromMachineSettings(cycleIdle, currentUser)" in dialog)
tree = ast.parse((ROOT / "frontend" / "AshokLeylandFrontEnd.py").read_text(encoding="utf-8"))
passes = False
for node in ast.walk(tree):
    if isinstance(node, ast.Call) and any(isinstance(a, ast.Name) and a.id == "updateMachineSettingsDialog" for a in node.args):
        for kw in node.keywords:
            if kw.arg == "cycleIdle" and "READ_QR_CODE" in ast.unparse(kw.value):
                passes = True
check("frontend passes cycleIdle = state READ_QR_CODE", passes)

print("R8 translations")
from babel.messages.pofile import read_po
NEW = ("Reboot Camera", "Wrong password: the camera was not rebooted", "The camera could not be rebooted: {reason}",
       "The camera is rebooting. Its picture is back after about one to two minutes.",
       "Only between components; the password is asked again as the final confirmation.")
for lang in ("kn", "ta", "hi"):
    cat = read_po(open(ROOT / "Internal" / "locale" / lang / "LC_MESSAGES" / "frontend.po", "rb"))
    ids = [m for m in cat if m.id and ("camera" in m.id.lower() and ("reboot" in m.id.lower() or "rebooting" in m.id.lower())
                                       or m.id in NEW or m.id.startswith("A component is in the cycle"))]
    good = len(ids) >= 7 and all(m.string and set(re.findall(r"{\w+}", m.id)) == set(re.findall(r"{\w+}", m.string)) for m in ids)
    check(f"{lang}: {len(ids)} reboot texts translated, placeholders kept", good, [m.id for m in ids if not m.string])

server.shutdown()
print(f"\n{passed} passed, {failed} failed")
print("RESULT:", "PASS" if not failed else "FAIL")
sys.exit(0 if not failed else 1)
