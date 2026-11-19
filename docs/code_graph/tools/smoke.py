"""Import every reachable module; report failures. Run from repo root."""
import importlib, json, sys, traceback, os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.getcwd())
g = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "graph.json") + ""))
ok, bad = [], {}
for m in g["reachable"]:
    if m in ("MainProgram", "OtherServices"):
        continue  # these connect to Redis / exit at import time
    try:
        importlib.import_module(m); ok.append(m)
    except BaseException as e:
        bad[m] = f"{type(e).__name__}: {str(e)[:160]}"
print(f"OK {len(ok)}  FAIL {len(bad)}")
for m, e in bad.items():
    print(f"  {m}: {e}")
