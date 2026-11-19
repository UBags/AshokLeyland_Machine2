"""Which process writes / reads which Redis stream or key, from the code."""
import ast, os, re, sys, glob
from collections import defaultdict
ROOT = sys.argv[1]
src = open(f"{ROOT}/utils/RedisUtils.py", encoding="utf-8").read()
tree = ast.parse(src)

# module-level name -> queue literal / getter
qvars = {}
for n in tree.body:
    if isinstance(n, (ast.Assign, ast.AnnAssign)):
        t = n.targets[0] if isinstance(n, ast.Assign) else n.target
        if isinstance(t, ast.Name) and n.value is not None:
            seg = ast.get_source_segment(src, n.value)
            m = re.search(r"get(\w+?)Queue\(", seg or "")
            if m: qvars[t.id] = "cfg:" + m.group(1)
            elif isinstance(n.value, ast.Constant) and isinstance(n.value.value, str) and (
                    n.value.value.endswith(".q") or ":" in n.value.value):
                qvars[t.id] = n.value.value

# function -> queues referenced, and direction from its name
funcs = {}
for n in tree.body:
    if isinstance(n, ast.FunctionDef):
        names = {x.id for x in ast.walk(n) if isinstance(x, ast.Name)}
        qs = sorted(qvars[v] for v in names if v in qvars)
        consts = sorted({c.value for c in ast.walk(n) if isinstance(c, ast.Constant) and isinstance(c.value, str)
                         and re.fullmatch(r"[a-z0-9]+(?:2[a-z0-9]+)?\.q|io:[a-z_]+|[a-z]+:[a-z_:]+", c.value)})
        if qs or consts:
            funcs[n.name] = qs + [c for c in consts if c not in qs]

def proc(path):
    p = os.path.relpath(path, ROOT).replace(os.sep, "/")
    for pre, name in [("camera/", "Camera"), ("costhetaio/commonplc/", "IO"), ("costhetaio/qrcodescanner/", "QR"),
                      ("persistence/DBServer", "DB"), ("frontend/", "FE"), ("monitorAllConnections/", "HB"),
                      ("MainProgram", "Main"), ("logutils/", "Log"), ("processors/", "(shared)"), ("utils/StateSync", "FE")]:
        if p.startswith(pre): return name
    return None

uses = defaultdict(set)
for f in glob.glob(f"{ROOT}/**/*.py", recursive=True):
    pr = proc(f)
    if not pr or "_v0" in f or "Old" in f: continue
    s = "\n".join(l for l in open(f, encoding="utf-8", errors="replace").read().splitlines() if not l.lstrip().startswith("#"))
    for fn in funcs:
        if re.search(rf"\b{fn}\s*\(", s):
            uses[fn].add(pr)
for fn in sorted(funcs):
    if uses[fn]:
        print(f"{fn:60s} {','.join(sorted(uses[fn])):18s} {funcs[fn]}")
print("\nqvars:", qvars)
