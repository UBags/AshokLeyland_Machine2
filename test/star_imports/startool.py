"""Replace `from X import *` in production modules with explicit imports, checked (CHANGES 109).

Run on a COPY of the repo (git clone), Redis running, from this folder:
  python startool.py before <copy>                 import every production module (original code), record each
                                                   global name's object and what each star import provided (by
                                                   identity)                                    -> before.json
  python startool.py plan <copy>                   round 1: decide per file                     -> plan.json
  python startool.py apply <copy>                  rewrite the files of plan.json
  cp plan.json plan_round1.json; python startool.py plan <copy> plan_round1.json; python startool.py apply <copy>
                                                   later rounds: files no longer star-imported by anything (merge the
                                                   round plans into the file given), until a round converts nothing
  python startool.py check <copy>                  import every module again and compare        -> check.json
Rules (keep the stars when in doubt): a file another file star-imports, a star import inside a block, a vision file
in the verifier's manifest, a package __init__, a module that cannot be imported here (both Pythons are tried), or any
name whose source cannot be proven - kept. Each name is imported from the first star import that gave that very
object; standard-library / third-party names from their own module; project classes and functions from the module
that defines them (after the star source's line, so modules are imported in the same order); a star import that
provided nothing used becomes `import X` (its import-time effects kept). The check: every module imports as before;
every global name it reads, and every name another file takes from it (`from M import x`, `M.x`, patch("M.x")), is
the same object as before. Then run ruff --select F821 and the dry runs."""
import ast, json, os, pathlib, subprocess, sys, builtins
from concurrent.futures import ThreadPoolExecutor

HERE = pathlib.Path(__file__).resolve().parent
EXCL = {"test", "archive", "models", "C:", "__pycache__", "Temp", "logs"}
PYTHONS = ["python3.13", "python3.12"]


def modname(rel: pathlib.Path) -> str:
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def prodFiles(repo):
    for f in sorted(repo.rglob("*.py")):
        rel = f.relative_to(repo)
        if any(p in EXCL for p in rel.parts):
            continue
        yield rel


def allFiles(repo):
    """production + test (tests import and patch production modules)"""
    for f in sorted(repo.rglob("*.py")):
        rel = f.relative_to(repo)
        if any(p in {"archive", "models", "C:", "__pycache__", "Temp", "logs"} for p in rel.parts):
            continue
        yield rel


def parse(repo, rel):
    try:
        return ast.parse((repo / rel).read_text(errors="ignore"))
    except SyntaxError:
        return None


def resolveFrom(mod, node, isPkg=False):
    if node.level:
        base = mod.split(".")
        pkg = base if isPkg else base[:-1]
        if node.level > 1:
            pkg = pkg[:len(pkg) - (node.level - 1)]
        return ".".join(pkg + ([node.module] if node.module else []))
    return node.module


def loadedNames(tree):
    """every name read anywhere in the module (functions, classes, decorators, defaults, f-strings), names in quoted
    annotations, and names declared global"""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Load, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.Global):
            names.update(node.names)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) < 200:
            pass
    # quoted annotations
    for node in ast.walk(tree):
        anns = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            anns += [a.annotation for a in node.args.args + node.args.kwonlyargs + node.args.posonlyargs if a.annotation]
            if node.args.vararg and node.args.vararg.annotation: anns.append(node.args.vararg.annotation)
            if node.args.kwarg and node.args.kwarg.annotation: anns.append(node.args.kwarg.annotation)
            if node.returns: anns.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            anns.append(node.annotation)
        for a in anns:
            for sub in ast.walk(a):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    try:
                        for n2 in ast.walk(ast.parse(sub.value, mode="eval")):
                            if isinstance(n2, ast.Name):
                                names.add(n2.id)
                    except SyntaxError:
                        pass
    return names


def moduleLevelBindings(tree):
    """name -> first line it is bound at module level (def, class, assignment, import, for, with)"""
    out = {}
    def bind(n, line):
        out.setdefault(n, line)
    for st in tree.body:
        for node in ast.walk(st) if not isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else [st]:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bind(node.name, node.lineno)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                bind(node.id, node.lineno)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for a in node.names:
                    if a.name != "*":
                        bind((a.asname or a.name).split(".")[0], node.lineno)
    return out


def moduleLevelLoads(tree):
    """(name, line) read while the module is imported: module-level statements, class bodies, decorators and
    defaults - not function bodies"""
    out = []
    def visit(node, inFunctionBody=False):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for d in getattr(node, "decorator_list", []): visit(d)
            for d in node.args.defaults + [x for x in node.args.kw_defaults if x is not None]: visit(d)
            return
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            out.append((node.id, node.lineno))
        for child in ast.iter_child_nodes(node):
            visit(child)
    for st in tree.body:
        visit(st)
    return out


def scan(repo):
    """static facts over production + tests"""
    stars, loads, needs, starImporters, bindings, mlLoads, topNames = {}, {}, {}, {}, {}, {}, {}
    prod = set(str(r) for r in prodFiles(repo))
    modOfFile = {}
    for rel in allFiles(repo):
        tree = parse(repo, rel)
        if tree is None:
            continue
        mod = modname(rel)
        isPkg = rel.name == "__init__.py"
        modOfFile[str(rel)] = mod
        # star imports (top-level only count for conversion; nested ones mark the file)
        top = []
        nested = []
        aliases = {}                       # local name -> project module it refers to
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                src = resolveFrom(mod, node, isPkg)
                for a in node.names:
                    if a.name == "*":
                        (top if node in tree.body else nested).append((node.lineno, src))
                        starImporters.setdefault(src, set()).add(str(rel))
                    else:
                        needs.setdefault(src, set()).add(a.name)
                        aliases[a.asname or a.name] = f"{src}.{a.name}"     # maybe a submodule
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.asname:
                        aliases[a.asname] = a.name
                    else:
                        aliases[a.name.split(".")[0]] = a.name.split(".")[0]
        # attribute access m.x where m is an alias of a module (also dotted: a.b.c.x)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                chain = []
                cur = node
                while isinstance(cur, ast.Attribute):
                    chain.append(cur.attr); cur = cur.value
                if isinstance(cur, ast.Name) and cur.id in aliases:
                    chain = list(reversed(chain))
                    base = aliases[cur.id]
                    # every prefix could be a module: record (module, next attribute)
                    for i in range(len(chain)):
                        m = ".".join([base] + chain[:i])
                        needs.setdefault(m, set()).add(chain[i])
            # mock.patch("pkg.mod.name") / patch.object strings
            if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str) and getattr(node.func, "attr", getattr(node.func, "id", "")) == "patch":
                parts = node.args[0].value.split(".")
                for i in range(1, len(parts)):
                    needs.setdefault(".".join(parts[:i]), set()).add(parts[i])
        if str(rel) in prod:
            stars[str(rel)] = {"top": top, "nested": nested}
            loads[str(rel)] = sorted(loadedNames(tree))
            bindings[str(rel)] = moduleLevelBindings(tree)
            mlLoads[str(rel)] = moduleLevelLoads(tree)
    return {"stars": stars, "loads": loads, "needs": {k: sorted(v) for k, v in needs.items()},
            "starImporters": {k: sorted(v) for k, v in starImporters.items()}, "bindings": bindings,
            "mlLoads": mlLoads, "modOfFile": modOfFile}


def dumpOne(repo, mod, pythons):
    last = None
    for py in pythons:
        try:
            p = subprocess.run([py, str(HERE / "dumpmod.py"), str(repo), mod], capture_output=True, text=True,
                               timeout=150, cwd=str(repo))
            line = [l for l in p.stdout.splitlines() if l.startswith("@@DUMP@@")]
            res = json.JSONDecoder().raw_decode(line[-1][8:])[0] if line else \
                {"module": mod, "ok": False, "error": (p.stderr or "")[-300:]}
        except subprocess.TimeoutExpired:
            res = {"module": mod, "ok": False, "error": "timeout (import blocks)"}
        res["python"] = py
        if res.get("ok"):
            return res
        last = res
    return last


def dumpAll(repo, mods, pythonFor=None):
    out = {}
    def job(m):
        pys = [pythonFor[m]] if pythonFor and m in pythonFor else PYTHONS
        return m, dumpOne(repo, m, pys)
    with ThreadPoolExecutor(6) as ex:
        for m, r in ex.map(job, mods):
            out[m] = r
            print(f"  {'ok  ' if r.get('ok') else 'FAIL'} {m} ({r.get('python')}) {'' if r.get('ok') else r.get('error','')[:120]}",
                  flush=True)
    return out


def main():
    cmd, repo = sys.argv[1], pathlib.Path(sys.argv[2]).resolve()
    if cmd == "before":
        st = scan(repo)
        json.dump(st, open(HERE / "scan_before.json", "w"), indent=0, default=list)
        mods = sorted({modname(pathlib.Path(f)) for f in prodFiles(repo)})
        before = dumpAll(repo, mods)
        json.dump(before, open(HERE / "before.json", "w"))
        print("dumped", len(before), "ok", sum(1 for v in before.values() if v.get("ok")))
    elif cmd == "plan":
        plan(repo, sys.argv[3] if len(sys.argv) > 3 else None)
    elif cmd == "apply":
        apply(repo)
    elif cmd == "check":
        check(repo)


BUILTINS = set(dir(builtins))


_TREES = {}


def treeOf(f):
    if f not in _TREES:
        _TREES[f] = ast.parse((REPO / f).read_text(errors="ignore"))
    return _TREES[f]


def onlyImportsBetween(tree, line, bl):
    """True when the statement binding at line bl is an import and every module-level statement after `line` and
    before it is an import too (no code runs in between that could read the name)"""
    binding = [st for st in tree.body if st.lineno == bl]
    if not binding or not isinstance(binding[0], (ast.Import, ast.ImportFrom)):
        return False
    return all(isinstance(st, (ast.Import, ast.ImportFrom)) for st in tree.body if line < st.lineno < bl)


def alreadyImportedBefore(tree, mod, f, line, n, o):
    """the same object is already bound at module level before this star import, by an explicit import of the same
    module attribute (`import os` / `from X import n` with X the name's origin) - nothing to add"""
    if not o:
        return False
    for st in tree.body:
        if st.lineno >= line:
            break
        if o[0] == "module" and isinstance(st, ast.Import):
            if any((a.asname or a.name) == n and a.name == o[1] for a in st.names):
                return True
        if o[0] in ("from", "project") and isinstance(st, ast.ImportFrom) and st.level == 0 and st.module == o[1]:
            if any(a.name == n and (a.asname or a.name) == n for a in st.names):
                return True
    return False


def plan(repo, roundFile=None):
    global REPO
    REPO = repo
    st = json.load(open(HERE / "scan_before.json"))
    done = {}
    if roundFile:
        # a later round: star importers and the names other files take now come from the tree as converted so far
        # (converted files import those names explicitly); the module's own facts and identities stay those of the
        # original code (it has not been changed)
        now = scan(repo)
        st["starImporters"] = now["starImporters"]
        st["needs"] = {k: sorted(set(st["needs"].get(k, [])) | set(v)) for k, v in now["needs"].items()} | \
                      {k: v for k, v in st["needs"].items() if k not in now["needs"]}
        done = json.load(open(HERE / roundFile))
        json.dump(st, open(HERE / "scan_round.json", "w"), default=list)
    before = json.load(open(HERE / "before.json"))
    manifest = set()
    for m in ("machine1", "machine2"):
        mf = json.load(open(repo / f"test/{m}_dryrun/vision_config_manifest.json"))
        for k in ("files", "baseline_per_file"):
            v = mf.get(k)
            if isinstance(v, dict): manifest |= set(v)
            elif isinstance(v, list): manifest |= set(x if isinstance(x, str) else x.get("path", "") for x in v)
    decisions = {}
    starFiles = {f: v for f, v in st["stars"].items() if v["top"] or v["nested"]}
    for f, info in starFiles.items():
        if f in done and not done[f]["keep"]:
            continue                     # converted in an earlier round
        mod = st["modOfFile"][f]
        reasons = []
        if info["nested"]:
            reasons.append(f"star import inside a block (lines {[l for l, _ in info['nested']]})")
        if f in manifest or f.replace("\\", "/") in manifest:
            reasons.append("vision file in the equivalence verifier's manifest")
        importers = st["starImporters"].get(mod, [])
        if importers:
            reasons.append(f"star-imported by {len(importers)} file(s), e.g. {importers[:3]}")
        if f.endswith("__init__.py"):
            reasons.append("package __init__")
        d = before.get(mod) or {}
        if not d.get("ok"):
            reasons.append(f"cannot be imported here: {d.get('error', '?')[:100]}")
        decision = {"module": mod, "file": f, "python": d.get("python"), "keep": bool(reasons), "reasons": reasons}
        if not reasons:
            used = set(st["loads"][f]) | set(st["needs"].get(mod, []))
            provided = d["provided"]                  # name -> star source (identity proven)
            exports = d["exports"]
            fps = d["fps"]
            # a name exported by a star source, read by the module, but not that object in the module's namespace:
            # rebound somewhere - fine only if bound at module level before any import-time read
            bindings = st["bindings"][f]
            mlLoads = st["mlLoads"][f]
            for src in d["starOrder"]:
                if exports.get(src) is None:
                    reasons.append(f"star source {src} could not be imported"); continue
            starLine = {src: line for line, src in info["top"]}
            for n in sorted(used):
                srcs = [s for s in d["starOrder"] if exports.get(s) and n in exports[s]]
                if not srcs or n in provided:
                    continue
                if n not in fps:
                    reasons.append(f"'{n}' exported by {srcs} and read, but not in the module's namespace"); continue
                bl = bindings.get(n)
                if bl is None:
                    reasons.append(f"'{n}' exported by {srcs} but rebound outside module level (global statement?)"); continue
                early = [line for name, line in mlLoads if name == n and line < bl and any(starLine.get(s, 10**9) < line for s in srcs)]
                if early:
                    reasons.append(f"'{n}' read at import (line {early[0]}) before its module-level binding (line {bl}) while a star import provides it")
            # each provided name comes from the FIRST star import that gives this very object (later ones give the
            # same object); an earlier star import exporting a DIFFERENT object under that name, read at import time
            # before the first identical one: doubt
            firstSource = {}
            for n in sorted(used):
                same = d.get("providedAll", {}).get(n)
                if not same:
                    continue
                exporting = [s_ for s_ in d["starOrder"] if exports.get(s_) and n in exports[s_]]
                firstSame = same[0]
                earlier = exporting[:exporting.index(firstSame)] if firstSame in exporting else []
                if earlier:
                    l0, l1 = starLine.get(earlier[0], 0), starLine.get(firstSame, 0)
                    if [x for x in mlLoads if x[0] == n and l0 < x[1] < l1]:
                        reasons.append(f"'{n}' read at import while an earlier star import gave a different object")
                firstSource[n] = firstSame
            if not reasons:
                imports = {}
                origins = d.get("origins", {})
                provided = dict(provided)
                provided.update(firstSource)
                for line, src in info["top"]:
                    names = sorted(n for n in used if provided.get(n) == src)
                    project, modules, froms, definers = [], [], {}, {}
                    for n in names:
                        o = origins.get(n)
                        bl = bindings.get(n)
                        # bound explicitly at module level (an import of the same object) with no import-time read
                        # between this star import and that binding: the star import adds nothing for it
                        if bl is not None and bl > line and onlyImportsBetween(treeOf(f), line, bl):
                            continue
                        if alreadyImportedBefore(treeOf(f), d["module"], f, line, n, o):
                            continue
                        if o and o[0] == "module":
                            modules.append((n, o[1]))
                        elif o and o[0] == "from":
                            froms.setdefault(o[1], []).append(n)
                        elif o and o[0] == "project" and o[1] != src:
                            definers.setdefault(o[1], []).append(n)
                        else:
                            project.append(n)
                    imports[src] = {"project": project, "modules": modules, "froms": froms, "definers": definers}
                decision["imports"] = imports
                decision["starLines"] = info["top"]
            decision["keep"] = bool(reasons)
        decisions[f] = decision
    json.dump(decisions, open(HERE / "plan.json", "w"), indent=1)
    conv = [d for d in decisions.values() if not d["keep"]]
    print(f"files with star imports: {len(decisions)}; to convert: {len(conv)}; kept: {len(decisions) - len(conv)}")
    from collections import Counter
    c = Counter(r.split(":")[0].split(" (")[0][:60] for d in decisions.values() for r in d["reasons"])
    for k, v in c.most_common(): print(f"  kept because {k}: {v}")


def wrapFrom(indent, mod, names):
    one = f"{indent}from {mod} import {', '.join(names)}"
    if len(one) <= 118:
        return one
    rows, cur = [], ""
    for n in names:
        piece = n + ", "
        if cur and len(indent) + 4 + len(cur) + len(piece) > 118:
            rows.append(cur.rstrip()); cur = ""
        cur += piece
    rows.append(cur.rstrip().rstrip(","))
    return f"{indent}from {mod} import (\n" + "\n".join(f"{indent}    {r}" for r in rows) + f")"


def apply(repo):
    decisions = json.load(open(HERE / "plan.json"))
    for f, d in decisions.items():
        if d["keep"]:
            continue
        path = repo / f
        lines = path.read_text().split("\n")
        tree = ast.parse("\n".join(lines))
        bound = set(moduleLevelBindings(tree))
        for node in sorted([n for n in tree.body if isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names)],
                           key=lambda n: -n.lineno):
            src = resolveFrom(d["module"], node, f.endswith("__init__.py"))
            spec = d["imports"].get(src, {"project": [], "modules": [], "froms": {}})
            names = spec["project"]
            text = lines[node.lineno - 1]
            indent = text[:len(text) - len(text.lstrip())]
            modText = "." * node.level + (node.module or "")
            direct = []
            for n, m in sorted(spec["modules"]):
                direct.append(f"{indent}import {m}" if n == m else f"{indent}import {m} as {n}")
            for X, ns in sorted(spec["froms"].items()):
                if X == (node.module or "") and node.level == 0:
                    names = sorted(set(names) | set(ns))      # the star source itself is where they live
                else:
                    direct.append(wrapFrom(indent, X, sorted(ns)))
            after = [wrapFrom(indent, X, sorted(ns)) for X, ns in sorted(spec.get("definers", {}).items())]
            if names:
                new = wrapFrom(indent, modText, names)
            elif after:
                # keep the star source imported first (it imports the defining modules, as before)
                top = (node.module or "").split(".")[0]
                new = (f"{indent}import {node.module}  # noqa: F401 - imported first, as before" if node.level == 0 and top and top not in bound
                       else wrapFrom(indent, modText, [after_ns for X, ns in sorted(spec["definers"].items()) for after_ns in ns][:1]))
            elif direct:
                new = None
            else:
                top = (node.module or "").split(".")[0]
                if node.level == 0 and top and top not in bound:
                    new = f"{indent}import {node.module}  # noqa: F401 - was \"from {modText} import *\"; nothing used from it, imported for its import-time effects"
                else:
                    new = text          # cannot import it without binding a name: kept
            block = direct + ([new] if new is not None else []) + after
            lines[node.lineno - 1:node.end_lineno] = "\n".join(block).split("\n")
        path.write_text("\n".join(lines))
        print("rewrote", f)


def check(repo):
    before = json.load(open(HERE / "before.json"))
    stb = json.load(open(HERE / "scan_before.json"))
    if (HERE / "scan_round.json").exists():                 # needs found in later rounds (explicit imports)
        sr = json.load(open(HERE / "scan_round.json"))
        stb["needs"] = {k: sorted(set(stb["needs"].get(k, [])) | set(sr["needs"].get(k, [])))
                        for k in set(stb["needs"]) | set(sr["needs"])}
    decisions = {}
    for pf in sorted(HERE.glob("plan_round*.json")) + [HERE / "plan.json"]:
        for f, dd in json.load(open(pf)).items():
            if not dd["keep"] or f not in decisions:
                decisions[f] = dd
    st = scan(repo)
    json.dump(st, open(HERE / "scan_after.json", "w"), default=list)
    mods = sorted(m for m, v in before.items() if v.get("ok"))
    after = dumpAll(repo, mods, pythonFor={m: before[m]["python"] for m in mods})
    json.dump(after, open(HERE / "after.json", "w"))
    problems = {}
    fileOf = {v: k for k, v in stb["modOfFile"].items()}
    for m in mods:
        b, a = before[m], after[m]
        f = fileOf.get(m)
        p = []
        if not a.get("ok"):
            p.append(f"no longer imports: {a.get('error')}")
        else:
            used = set(stb["loads"].get(f, [])) | set(stb["needs"].get(m, [])) if f else set(stb["needs"].get(m, []))
            converted = {d["module"] for d in decisions.values() if not d["keep"]}
            def norm(fp):
                # instances: the modules holding the same object - converted modules no longer hold names they never
                # used, so they are left out of the comparison
                if fp and fp[0] == "inst":
                    return ["inst", fp[1], sorted(set(fp[2]) - converted)]
                return fp
            for n in sorted(used):
                fb, fa = norm(b["fps"].get(n)), norm(a["fps"].get(n))
                if fb != fa and fb is not None:
                    p.append(f"'{n}': before {fb} after {fa}")
            for n in stb["needs"].get(m, []):
                if n in b["fps"] and n not in a["fps"]:
                    p.append(f"'{n}' taken by another file but gone")
        if p:
            problems[m] = p
    json.dump(problems, open(HERE / "check.json", "w"), indent=1)
    print(f"checked {len(mods)} modules; problems in {len(problems)}")
    for m, p in problems.items():
        print(" ", m, p[:4])


if __name__ == "__main__":
    main()
