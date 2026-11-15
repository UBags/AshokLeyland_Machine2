"""Import ONE project module and print a JSON fingerprint of its namespace (last line of stdout).
usage: python dumpmod.py <repo root> <module.name>
For every global name: a fingerprint comparable across processes. For each top-level `from S import *` of the module:
which of its global names are the very objects S exports (identity), i.e. what that star import provided."""
import ast, json, os, sys, types, builtins, pathlib

root, modname = sys.argv[1], sys.argv[2]
sys.path.insert(0, root)
os.chdir(root)
os.environ.setdefault("COSTHETA_PLC_VENDOR", "siemens")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
for stub in ("winsound",):
    if stub not in sys.modules:
        try:
            __import__(stub)
        except Exception:
            sys.modules[stub] = types.SimpleNamespace(PlaySound=lambda *a, **k: None, SND_FILENAME=0, SND_ASYNC=0,
                                                      Beep=lambda *a, **k: None)

PROJECT_PREFIXES = None


def projectModules():
    out = {}
    for name, m in list(sys.modules.items()):
        f = getattr(m, "__file__", None) or ""
        if f and os.path.abspath(f).startswith(os.path.abspath(root)) and "site-packages" not in f:
            out[name] = m
    return out


SIMPLE = (int, float, str, bytes, bool, type(None), complex)


def simple(v):
    if isinstance(v, SIMPLE):
        return True
    if isinstance(v, (tuple, frozenset)) and len(v) < 50:
        return all(simple(x) for x in v)
    return False


def fingerprint(name, v, holders):
    if isinstance(v, types.ModuleType):
        return ["mod", v.__name__]
    if isinstance(v, (types.FunctionType, types.BuiltinFunctionType, type, types.MethodType)) or (
            callable(v) and hasattr(v, "__qualname__") and hasattr(v, "__module__")):
        return ["obj", str(getattr(v, "__module__", "?")), str(getattr(v, "__qualname__", getattr(v, "__name__", "?")))]
    if simple(v):
        r = repr(v)
        return ["val", type(v).__name__, r if len(r) < 400 else r[:400]]
    t = type(v)
    return ["inst", f"{t.__module__}.{t.__qualname__}", sorted(holders)]


try:
    M = __import__(modname, fromlist=["*"])
except BaseException as e:
    print("\n@@DUMP@@" + json.dumps({"module": modname, "ok": False, "error": f"{type(e).__name__}: {e}"[:500]}))
    sys.stdout.flush()
    os._exit(0)

proj = projectModules()
# which project modules hold each object under the same name (provenance of instances)
src = pathlib.Path(M.__file__).read_text(errors="ignore")
tree = ast.parse(src)
stars = [n for n in tree.body if isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names)]


def resolve(node):
    if node.level:
        base = modname.split(".")
        pkg = base[:-1] if not modname.endswith("__init__") else base
        pkg = pkg[:len(pkg) - (node.level - 1)] if node.level > 1 else pkg
        return ".".join(pkg + ([node.module] if node.module else []))
    return node.module


provided = {}
providedAll = {}
exports = {}
for node in stars:
    sname = resolve(node)
    S = sys.modules.get(sname)
    if S is None:
        try:
            S = __import__(sname, fromlist=["*"])
        except Exception:
            S = None
    if S is None:
        exports[sname] = None
        continue
    names = list(getattr(S, "__all__", [n for n in vars(S) if not n.startswith("_")]))
    exports[sname] = names
    for n in names:
        if n in vars(M) and n in vars(S) and vars(S)[n] is vars(M)[n]:
            provided[n] = sname            # later star statements win, as at import
            providedAll.setdefault(n, []).append(sname)   # every star source giving this very object, in order

import importlib


def origin(n, v):
    """where a provided name can be imported from directly, giving the very same object: ["module", modname] for a
    module bound to n, ["from", X, n] for an object of a non-project module X whose attribute n is that object;
    None for project objects (they stay with the star source)"""
    projectNames = set(proj)
    if isinstance(v, types.ModuleType):
        return ["module", v.__name__] if v.__name__ not in projectNames else None
    X = getattr(v, "__module__", None)
    if not isinstance(X, str) or X in projectNames or X == modname:
        return None
    cands = sorted({X} | {X.rsplit(".", i)[0] for i in range(1, X.count(".") + 1)}, key=lambda c: (
        any(part.startswith("_") for part in c.split(".")), len(c)))      # public, shortest first (redis, not redis.client)
    for cand in cands:
        try:
            mod = importlib.import_module(cand)
        except Exception:
            continue
        if getattr(mod, n, None) is v:
            return ["from", cand, n]
    return None


def projectOrigin(n, v):
    """a project class / function: the project module that defines it under this name (identity checked)"""
    if isinstance(v, types.ModuleType) or not (isinstance(v, type) or isinstance(v, (types.FunctionType,))):
        return None
    X = getattr(v, "__module__", None)
    if not isinstance(X, str) or X not in proj or X == modname:
        return None
    return ["project", X, n] if vars(proj[X]).get(n) is v else None


origins = {}
for n, sname in provided.items():
    try:
        origins[n] = origin(n, vars(M)[n]) or projectOrigin(n, vars(M)[n])
    except Exception:
        origins[n] = None

fps = {}
for n, v in list(vars(M).items()):
    if n.startswith("__") and n.endswith("__"):
        continue
    holders = [mn for mn, mm in proj.items() if mn != modname and vars(mm).get(n, None) is v] if not simple(v) else []
    try:
        fps[n] = fingerprint(n, v, holders)
    except Exception as e:
        fps[n] = ["err", repr(e)[:100]]

print("\n@@DUMP@@" + json.dumps({"module": modname, "ok": True, "fps": fps, "provided": provided, "providedAll": providedAll, "origins": origins,
                  "exports": {k: (sorted(v) if v is not None else None) for k, v in exports.items()},
                  "starOrder": [resolve(n) for n in stars]}))
sys.stdout.flush()
os._exit(0)
