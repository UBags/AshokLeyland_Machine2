"""
DryRun_Models_Check - MainProgram checks the vision model files before it starts anything (CHANGES 175).

M1  the required files (models.required.files): the two weights files ModelManager loads; the properties value equals
    the code default
M2  all files present and non-empty under a project root: no problems, checkModelFilesPresent True, no popup
M3  a file missing / a file empty: each reported with its path and why
M4  checkModelFilesPresent with a file missing: the console lists it, the popup gets the models folder and the
    file names, the wait is models.missing.message.sec in total (also when OK closes the popup at once), False
M5  main() with the files missing: exits (code 1) before clearing Redis and before any server process is created;
    the check is main()'s first step after setLogProcess (AST)
M6  the real project root of this clone: the result matches what is on disk (the sandbox has no YOLO weights)

Needs Redis (MainProgram connects at import). No PLC, camera, GUI window or database. Run from the project root:
    python test/machine2_dryrun/DryRun_Models_Check.py
"""
import ast, os, pathlib, re, sys, tempfile, time, types

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("COSTHETA_PLC_VENDOR", "allenbradley" if "machine1" in str(pathlib.Path(__file__).parent) else "siemens")
if sys.platform != 'win32':
    sys.modules['winsound'] = types.SimpleNamespace(PlaySound=lambda *a, **k: None, SND_FILENAME=0, SND_ASYNC=0,
                                                    Beep=lambda *a: None)

import MainProgram as M
import utils.ModelsCheck as MC
import BaseUtils

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   [{detail}]"), flush=True)


def makeRoot(files: dict) -> str:
    root = tempfile.mkdtemp(prefix="models_check_")
    for relative, size in files.items():
        path = os.path.join(root, *relative.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"x" * size)
    return root


SAM = "models/MobileSAM/weights/mobile_sam.pt"
YOLO = "models/MobileSAM/MobileSAMv2/weight/ObjectAwareModel.pt"

# ---------------------------------------------------------------- M1
print("\n=== M1 the required files ===", flush=True)
check("M1 the two weights files ModelManager loads", MC.requiredModelFiles() == [SAM, YOLO], MC.requiredModelFiles())
check("M1 properties value = code default", MC.REQUIRED_FILES == MC.REQUIRED_FILES_DEFAULT, MC.REQUIRED_FILES)
check("M1 message time 5 s", MC.MESSAGE_SEC == 5, MC.MESSAGE_SEC)
mm = (ROOT / "camera" / "ModelManager.py").read_text(encoding="utf-8")
check("M1 ModelManager still loads exactly these paths",
      '"MobileSAM", "weights", "mobile_sam.pt"' in mm and '"MobileSAMv2", "weight", "ObjectAwareModel.pt"' in mm)

# popups and sleeps recorded, not shown / slept
popups, sleeps = [], []
M.showErrorMessage = lambda text, auto_close_duration=3, **k: popups.append((text, auto_close_duration))
realSleep = M.time.sleep
M.time.sleep = lambda s: sleeps.append(s)
realRoot = BaseUtils.get_project_root

# ---------------------------------------------------------------- M2
print("\n=== M2 all present ===", flush=True)
root = makeRoot({SAM: 10, YOLO: 10})
check("M2 no problems", MC.missingModelFiles(root) == [], MC.missingModelFiles(root))
BaseUtils.get_project_root = lambda: root
popups.clear()
check("M2 checkModelFilesPresent True, no popup", M.checkModelFilesPresent() is True and not popups, popups)

# ---------------------------------------------------------------- M3
print("\n=== M3 missing / empty ===", flush=True)
root = makeRoot({SAM: 10})
problems = MC.missingModelFiles(root)
check("M3 the YOLO weights missing", problems == [(YOLO, "missing")], problems)
root = makeRoot({SAM: 0, YOLO: 10})
problems = MC.missingModelFiles(root)
check("M3 an empty file reported", problems == [(SAM, "empty (0 bytes)")], problems)
root = tempfile.mkdtemp(prefix="models_check_none_")
problems = MC.missingModelFiles(root)
check("M3 no models folder: both missing", [p for p, _ in problems] == [SAM, YOLO], problems)

# ---------------------------------------------------------------- M4
print("\n=== M4 the message and the wait ===", flush=True)
BaseUtils.get_project_root = lambda: root
popups.clear(); sleeps.clear()
ok = M.checkModelFilesPresent()
check("M4 False", ok is False)
check("M4 one popup for 5 s", len(popups) == 1 and popups[0][1] == 5, popups)
text = popups[0][0] if popups else ""
check("M4 popup: models folder and both file names, 'closes in 5 seconds'",
      os.path.join(root, "models") in text and "mobile_sam.pt" in text and "ObjectAwareModel.pt" in text
      and "closes in 5 seconds" in text, text)
check("M4 popup is one paragraph (the popup re-wraps text)", "\n" not in text, repr(text))
check("M4 popup returned at once -> the rest of the 5 s waited", len(sleeps) == 1 and 4.5 < sleeps[0] <= 5.0, sleeps)
screen, console = MC.missingModelsMessages(root, problems)
check("M4 console lists each full path", os.path.join(root, *SAM.split("/")) in console
      and os.path.join(root, *YOLO.split("/")) in console, console)

# ---------------------------------------------------------------- M5
print("\n=== M5 main() starts nothing ===", flush=True)
created, cleared = [], []
realProcess, realClear = M.Process, M.clearQueues
M.Process = lambda *a, **k: created.append(k.get("target")) or types.SimpleNamespace(start=lambda: None, pid=0)
M.clearQueues = lambda *a, **k: cleared.append(1)
code = None
try:
    M.main()
except SystemExit as e:
    code = e.code
check("M5 main() exits with code 1", code == 1, code)
check("M5 ... no server process created", created == [], created)
check("M5 ... Redis queues not cleared", cleared == [], cleared)
M.Process, M.clearQueues = realProcess, realClear
tree = ast.parse((ROOT / "MainProgram.py").read_text(encoding="utf-8"))
mainFn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
firstIf = next((i for i, s in enumerate(mainFn.body) if isinstance(s, ast.If)
                and "checkModelFilesPresent" in ast.unparse(s.test)), None)
firstProcess = next((i for i, s in enumerate(mainFn.body) if re.search(r"\bProcess\(", ast.unparse(s))), None)
setLog = next((i for i, s in enumerate(mainFn.body) if "setLogProcess(" in ast.unparse(s)), None)
check("M5 AST: the check right after setLogProcess, before the first Process(...)",
      firstIf is not None and setLog is not None and firstIf == setLog + 1 and firstProcess is not None
      and firstIf < firstProcess, (setLog, firstIf, firstProcess))

# ---------------------------------------------------------------- M6
print("\n=== M6 this clone ===", flush=True)
BaseUtils.get_project_root = realRoot
onDisk = [p for p in MC.requiredModelFiles()
          if not os.path.isfile(os.path.join(str(realRoot()), *p.split("/")))
          or os.path.getsize(os.path.join(str(realRoot()), *p.split("/"))) == 0]
found = [p for p, _ in MC.missingModelFiles(str(realRoot()))]
check("M6 matches the disk", found == onDisk, (found, onDisk))
print(f"      (this clone: {'all present' if not found else 'missing ' + ', '.join(found)})", flush=True)

M.time.sleep = realSleep
print(f"\n{sum(results)}/{len(results)} passed", flush=True)
sys.exit(0 if all(results) else 1)
