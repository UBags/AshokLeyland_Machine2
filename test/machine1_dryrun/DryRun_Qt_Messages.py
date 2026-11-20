"""
DryRun_Qt_Messages - Qt's own messages go to the log through frontend/QtMessages.py, never to the C stderr
(CHANGES 176: the frontend crashed in the exe at Indic text, fast fail in ucrtbase.dll, while Qt wrote its
"OpenType support missing ..." warnings to the C stderr).

Each case runs in a child process whose C-level stderr (file descriptor 2) is captured, so a write by Qt itself
would be seen:
  Q1  getAppInstance() installs the handler before the QApplication exists
  Q2  Kannada, Tamil and Hindi laid out in Courier (as the tiles): Qt's messages arrive in the handler, NOT on fd 2;
      each distinct message logged once through logBoth, the repeats only counted
  Q3  English laid out in Courier: no Qt message at all (why English never crashed)
  Q4  without the handler (the old behaviour) the same Indic layout writes Qt's warnings to fd 2 - the write that
      crashed in the exe (shown here on Linux, where it does not crash)
  Q5  a qWarning sent from Python: logged once with its category, repeats counted; the handler never raises
  Q6  a fatal Qt message (no such platform plugin): Qt still aborts, and the message is on the console (Python's
      sys.stderr), with the Python stack - not lost with the logging server not running

No Redis, database, PLC or window needed (Qt offscreen). Run from the project root:
    python test/machine1_dryrun/DryRun_Qt_Messages.py
"""
import json, os, pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
VENDOR = "allenbradley" if "machine1" in str(pathlib.Path(__file__).parent) else "siemens"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   [{detail}]"), flush=True)


CHILD = r'''
import json, os, sys, types
sys.path.insert(0, ROOT)
os.chdir(ROOT)
sys.modules.setdefault("winsound", types.SimpleNamespace(PlaySound=lambda *a, **k: None, SND_FILENAME=0,
                                                         SND_ASYNC=0, Beep=lambda *a: None))
logged = []
import logutils.SlaveLoggers as SL
SL.logBoth = lambda level, source, message, messageType=0: logged.append([level, source, message])
import frontend.QtMessages as QM
from PySide6.QtGui import QFont, QFontMetrics, QPixmap, QPainter
mode = sys.argv[1]
if mode == "nohandler":
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
else:
    from frontend.CosThetaMonitorDimensions import getAppInstance
    app = getAppInstance()
texts = {"indic": ["ನಕಲ್ ಪರಿಶೀಲನೆ", "ஹப் சரிபார்ப்பு", "हब जाँच"], "english": ["Knuckle Check", "Hub Check"]}
which = "english" if mode == "english" else "indic"
font = QFont("Courier", 20)
pix = QPixmap(400, 100)
for t in texts[which]:
    QFontMetrics(font).horizontalAdvance(t)
    p = QPainter(pix); p.setFont(font); p.drawText(10, 50, t); p.end()
if mode == "qwarning":
    from PySide6.QtCore import qWarning
    for _ in range(3):
        qWarning("dry-run warning from Python")
print("RESULT " + json.dumps({"installed": QM._installed, "logged": logged,
                              "counts": [[list(k), v] for k, v in QM.messageCounts().items()]}), flush=True)
'''


def runChild(mode, platform="offscreen"):
    env = dict(os.environ, QT_QPA_PLATFORM=platform, COSTHETA_PLC_VENDOR=VENDOR)
    env.pop("QT_LOGGING_RULES", None)
    p = subprocess.run([sys.executable, "-c", f"ROOT={str(ROOT)!r}\n" + CHILD, mode], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=300)
    out = next((l[7:] for l in p.stdout.splitlines() if l.startswith("RESULT ")), None)
    return (json.loads(out) if out else None), p.stderr, p.returncode


print("\n=== Q1 / Q2 Indic text with the handler ===", flush=True)
res, err, code = runChild("indic")
check("Q1 child ran", res is not None and code == 0, (code, err[-400:]))
res = res or {"installed": False, "logged": [], "counts": []}
check("Q1 handler installed by getAppInstance", res["installed"] is True)
qtOnFd2 = [l for l in err.splitlines() if l.startswith("qt.")]
check("Q2 nothing written by Qt to the C stderr", not qtOnFd2, qtOnFd2[:3])
fontMsgs = [c for c in res["counts"] if c[0][1] == "qt.text.font.db"]
check("Q2 Qt's font messages arrived in the handler", len(fontMsgs) > 0, res["counts"][:3])
loggedFont = [l for l in res["logged"] if "(qt.text.font.db)" in l[2]]
check("Q2 each distinct message logged once", len(loggedFont) == len(fontMsgs), (len(loggedFont), len(fontMsgs)))
check("Q2 repeats counted, not logged", sum(c[1] for c in fontMsgs) >= len(fontMsgs), fontMsgs[:2])
print(f"      ({len(fontMsgs)} distinct Qt messages, {sum(c[1] for c in fontMsgs)} in all; e.g. "
      f"{fontMsgs[0][0][2] if fontMsgs else '-'})", flush=True)

print("\n=== Q3 English text ===", flush=True)
res, err, code = runChild("english")
check("Q3 no Qt message at all in English", res is not None and res["counts"] == [] and
      not [l for l in err.splitlines() if l.startswith("qt.")], (res, err[-300:]))

print("\n=== Q4 without the handler (old behaviour) ===", flush=True)
res, err, code = runChild("nohandler")
qtOnFd2 = [l for l in err.splitlines() if l.startswith("qt.text.font.db")]
check("Q4 Qt writes its Indic font warnings to the C stderr itself", len(qtOnFd2) > 0, err[-300:])

print("\n=== Q5 qWarning from Python ===", flush=True)
res, err, code = runChild("qwarning")
res = res or {"logged": [], "counts": []}
mine = [l for l in res["logged"] if "dry-run warning from Python" in l[2]]
cnt = [c for c in res["counts"] if c[0][2] == "dry-run warning from Python"]
check("Q5 logged once at warning level", len(mine) == 1 and mine[0][0] == "logWarning", mine)
check("Q5 three counted", cnt and cnt[0][1] == 3, cnt)
check("Q5 not on the C stderr", "dry-run warning from Python" not in err, err[-200:])
import importlib
sys.path.insert(0, str(ROOT))
QM = importlib.import_module("frontend.QtMessages")
try:
    QM.handleQtMessage(object(), object(), object())
    check("Q5 the handler never raises (bad arguments)", True)
except Exception as e:
    check("Q5 the handler never raises (bad arguments)", False, e)

print("\n=== Q6 fatal message ===", flush=True)
res, err, code = runChild("indic", platform="nosuchplatform")
check("Q6 Qt aborts (as before)", res is None and code != 0, code)
fatal = [l for l in err.splitlines() if l.startswith("Qt fatal (")]
check("Q6 the fatal message on the console through Python", len(fatal) == 1, err[-400:])
check("Q6 ... with the Python stack", "getAppInstance" in err, err[-400:])
print(f"      ({fatal[0][:110] if fatal else '-'})", flush=True)

print(f"\n{sum(results)}/{len(results)} passed", flush=True)
sys.exit(0 if all(results) else 1)
