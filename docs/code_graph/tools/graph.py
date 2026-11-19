"""Static import graph of the repo, reachable from entry points."""
# Usage (repo root): python3 docs/code_graph/tools/graph.py .  -> graph.json next to this script
import ast, os, sys, json, re
from collections import defaultdict

ROOT = sys.argv[1]
ENTRIES = ["MainProgram", "OtherServices"]

def mod_of(path):
    rel = os.path.relpath(path, ROOT)[:-3]
    parts = rel.split(os.sep)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)

mods = {}
for d, dirs, files in os.walk(ROOT):
    dirs[:] = [x for x in dirs if x not in (".git", "models", ".venv", "bokeh", "onvif")]
    for f in files:
        if f.endswith(".py") and " " not in f and "-" not in f:
            p = os.path.join(d, f)
            mods[mod_of(p)] = p

def resolve(name):
    # longest local prefix
    parts = name.split(".")
    for i in range(len(parts), 0, -1):
        c = ".".join(parts[:i])
        if c in mods:
            return c
    return None

edges = defaultdict(set)      # mod -> local deps
lazy = defaultdict(set)       # imports inside functions
ext = defaultdict(set)
for m, p in mods.items():
    try:
        tree = ast.parse(open(p, encoding="utf-8", errors="replace").read())
    except SyntaxError as e:
        print("SYNTAX", m, e, file=sys.stderr); continue
    top_nodes = set(id(n) for n in tree.body)
    def walk(node, in_func):
        for ch in ast.iter_child_nodes(node):
            f = in_func or isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef))
            if isinstance(ch, (ast.Import, ast.ImportFrom)):
                names = []
                if isinstance(ch, ast.Import):
                    names = [a.name for a in ch.names]
                elif ch.module and ch.level == 0:
                    names = [ch.module] + [ch.module + "." + a.name for a in ch.names]
                for n in names:
                    r = resolve(n)
                    if r and r != m:
                        (lazy if f else edges)[m].add(r)
                    elif not r:
                        ext[m].add(n.split(".")[0])
            walk(ch, f)
    walk(tree, False)

# reachability
reach = set(); stack = list(ENTRIES)
while stack:
    x = stack.pop()
    if x in reach: continue
    reach.add(x)
    stack += list(edges[x] | lazy[x])

out = {
  "reachable": sorted(reach),
  "edges": {m: sorted(edges[m]) for m in sorted(reach)},
  "lazy": {m: sorted(lazy[m]) for m in sorted(reach) if lazy[m]},
  "ext": {m: sorted(ext[m] - set(sys.stdlib_module_names)) for m in sorted(reach)},
  "unreachable_count": len(set(mods) - reach),
  "loc": {m: sum(1 for _ in open(mods[m], errors="replace")) for m in reach},
}
json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "graph.json"), "w"), indent=1)
for m in sorted(reach):
    print(f"{m:45s} {out['loc'][m]:5d}  -> {', '.join(edges[m])}" + (f"  || lazy: {', '.join(lazy[m])}" if lazy[m] else ""))
print("unreachable modules:", out["unreachable_count"], "of", len(mods))
