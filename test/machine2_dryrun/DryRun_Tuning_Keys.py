"""
DryRun_Tuning_Keys - the named tuning constants read through CosThetaConfigurator.getTuning (CHANGES 154) and the
properties files agree.

For every getTuning("<key>", <default>, ...) in the production code (test/ and archive/ left out):
  T1 the key is in all three properties files (ApplicationConfiguration.properties, .Machine1, .Machine2);
  T2 its value there equals the code's default (so the properties and the code say the same until someone changes
     the properties on purpose);
  T3 getTuning returns that value with the default's type, on this machine's vendor;
  T4 no key is used twice with different defaults.
A missing or unreadable key falls back to the code's default (T5, checked with a key that does not exist).

Run from the project root:
    python test/machine2_dryrun/DryRun_Tuning_Keys.py
"""
import ast
import os
import pathlib
import re
import subprocess
import sys
import warnings

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("COSTHETA_PLC_VENDOR", "siemens" if HERE.name == "machine2_dryrun" else "allenbradley")
os.chdir(ROOT)

from Configuration import CosThetaConfigurator as C  # noqa: E402

SKIP = ("test/", "archive/", "bokeh/", "src/", "Temp/", "models/")
PROPERTIES = ("ApplicationConfiguration.properties", "ApplicationConfiguration.Machine1.properties",
              "ApplicationConfiguration.Machine2.properties")


PRUNE = {".venv", "venv", ".git", ".idea", "__pycache__", "site-packages", "models", "src", "archive", "bokeh",
         "Temp", "test", "MainProgram.dist", "MainProgram.build", "MainProgram.onefile-build"}


def productionFiles():
    """The repo's own .py files: the ones git tracks (so a virtual environment, the models and the Nuitka build
    folders in the working folder are never read); without git, a walk that skips those folders."""
    try:
        out = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True, timeout=30)
        if out.returncode == 0 and out.stdout.strip():
            return [ROOT / rel for rel in out.stdout.splitlines() if not rel.startswith(SKIP)]
    except Exception:
        pass
    files = []
    for folder, dirs, names in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in PRUNE and not d.startswith(".")]
        files += [pathlib.Path(folder) / n for n in names if n.endswith(".py")]
    return [p for p in files if not p.relative_to(ROOT).as_posix().startswith(SKIP)]


def codeKeys():
    found = {}
    for path in productionFiles():
        rel = path.relative_to(ROOT).as_posix()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")          # invalid escapes etc. in old files: not this test's business
                tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "getTuning"
                    and len(node.args) >= 2 and isinstance(node.args[0], ast.Constant)):
                try:
                    default = ast.literal_eval(node.args[1])
                except ValueError:
                    default = ast.unparse(node.args[1])      # a name (e.g. REPEAT_EVERY): resolved below
                found.setdefault(node.args[0].value, []).append((rel, node.lineno, default))
    return found


def propertiesValues(name):
    values = {}
    for line in (ROOT / name).read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def asType(text, like):
    if isinstance(like, bool):
        return text.lower() in ("true", "1", "yes", "on")
    if isinstance(like, int):
        return int(float(text))
    if isinstance(like, float):
        return float(text)
    return text


results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))


keys = codeKeys()
files = {name: propertiesValues(name) for name in PROPERTIES}
print(f"{len(keys)} tuning keys in the code; vendor {C.getPlcVendor()}")
for key, uses in sorted(keys.items()):
    defaults = []
    for rel, line, default in uses:
        if isinstance(default, str) and re.fullmatch(r"[A-Z_][A-Z0-9_]*", default):     # a module constant
            module = rel[:-3].replace("/", ".")
            default = getattr(__import__(module, fromlist=["x"]), default)
        defaults.append(default)
    where = ", ".join(f"{rel}:{line}" for rel, line, _ in uses)
    check(f"T4 {key}: one default", len({repr(d) for d in defaults}) == 1, f"{defaults} ({where})")
    default = defaults[0]
    missing = [name for name in PROPERTIES if key not in files[name]]
    check(f"T1 {key}: in all three properties files", not missing, f"missing in {missing}" if missing else "")
    if missing:
        continue
    values = {asType(files[name][key], default) for name in PROPERTIES}
    check(f"T2 {key}: properties value = code default {default!r}", values == {default}, f"{values}")
    got = C.getTuning(key, default)
    check(f"T3 {key}: getTuning gives {default!r} ({type(default).__name__})",
          got == default and type(got) is type(default), f"{got!r}")
check("T5 a missing key gives the code default", C.getTuning("no.such.tuning.key", 7.5) == 7.5
      and C.getTuning("no.such.tuning.key", 3, minimum=5) == 5)

# T6 (CHANGES 157): a missing key, a value of the wrong type and a value below the minimum are logged once and give the
# default (the minimum); a valid value logs nothing
reported = []
_report = C.reportConfigurationProblem.__func__
C.reportConfigurationProblem = classmethod(lambda cls, key, message: reported.append((key, message)))
_getValue = C.getValue
fake = {"t.bool.typo": "Ture", "t.bool.ok": "False", "t.int.frac": "3.7", "t.int.whole": "4.0", "t.int.ok": "8000",
        "t.float.bad": "abc", "t.float.ok": "0.25", "t.low": "1", "t.str": "x,y"}
C.getValue = classmethod(lambda cls, key, default=None: fake.get(key, default))
try:
    cases = [("t.bool.typo", True, None, True, True), ("t.bool.ok", True, None, False, False),
             ("t.int.frac", 3, None, 3, True), ("t.int.whole", 3, None, 4, False), ("t.int.ok", 1, None, 8000, False),
             ("t.float.bad", 1.5, None, 1.5, True), ("t.float.ok", 1.0, None, 0.25, False), ("t.low", 3, 2, 2, True),
             ("t.str", "a", None, "x,y", False), ("t.missing", 5, None, 5, True), ("t.missing2", 1, 4, 4, True)]
    for key, default, minimum, expected, logged in cases:
        reported.clear()
        got = C.getTuning(key, default, minimum=minimum)
        check(f"T6 {key}={fake.get(key)!r} (default {default!r}, minimum {minimum}): {expected!r}, "
              f"{'logged' if logged else 'nothing logged'}",
              got == expected and type(got) is type(default) and (len(reported) == 1) == logged and len(reported) <= 1,
              f"{got!r} {reported}")
    reported.clear()
    okChoice = C.expectOneOf("t.source", " Cap ", ("cap", "hubcheck"))
    badChoice = C.expectOneOf("t.source", "hub", ("cap", "hubcheck"))
    check("T6 expectOneOf: ' Cap ' accepted silently, 'hub' logged", okChoice and not badChoice and len(reported) == 1,
          str(reported))
finally:
    C.getValue = _getValue
    C.reportConfigurationProblem = classmethod(_report)
reported.clear()
C._configurationProblemsReported.discard(("t.once", "x"))
printed = []
import builtins
_print = builtins.print
builtins.print = lambda *a, **k: printed.append(" ".join(map(str, a)))
try:
    C.reportConfigurationProblem("t.once", "x"); C.reportConfigurationProblem("t.once", "x")
finally:
    builtins.print = _print
check("T6 the same problem is logged once per process", sum("[Configuration] x" in l for l in printed) == 1, str(printed))

# T7 (CHANGES 157): the per-language font size adjustments
from frontend.FrontendI18n import fontSizeAdjustment, PROPERTIES_NAMES, DEFAULT_FONT_SIZE_ADJUSTMENTS
adjustments = {code: fontSizeAdjustment(code) for code in PROPERTIES_NAMES}
inFiles = all(f"language.{n}.fontsize.adjustment" in files[name] for name in PROPERTIES for n in PROPERTIES_NAMES.values())
fileValues = {code: {int(files[name][f"language.{n}.fontsize.adjustment"]) for name in PROPERTIES}
              for code, n in PROPERTIES_NAMES.items()} if inFiles else {}
check("T7 language.<english|kannada|tamil|hindi>.fontsize.adjustment in all three files: 0, -2, 0, -2 (CHANGES 158), "
      "= the code defaults", inFiles and adjustments == {"en": 0, "kn": -2, "ta": 0, "hi": -2}
      and adjustments == DEFAULT_FONT_SIZE_ADJUSTMENTS and all(v == {adjustments[c]} for c, v in fileValues.items()),
      f"{adjustments} files {fileValues}")

passed = sum(1 for _, ok in results if ok)
failed = [name for name, ok in results if not ok]
print(f"RESULT {passed}/{len(results)} checks passed" + (f"; FAILED: {failed}" if failed else ""))
sys.exit(0 if not failed else 1)
