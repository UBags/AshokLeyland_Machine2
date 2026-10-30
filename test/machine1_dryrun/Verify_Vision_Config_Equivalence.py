"""
Verify_Vision_Config_Equivalence - proves that making the vision checks configurable changed no value.

Static check (no torch / camera needed). For every vInt/vFloat/vBool/vStr/vTuple/vList/vPoints("key", default)
call in the live vision modules it verifies:
  1. the default equals the value the same name had in the BASELINE commit (before parameters were made
     configurable), with the same Python type; for parameters that were extracted from inline literals, the
     manifest (vision_config_manifest.json) must point at the baseline line, and that line must contain the literal;
  2. ApplicationConfiguration.properties contains the key and its value parses (VisionConfig rules) to exactly the default;
  3. a key used in more than one place always has the same default.
Exit code 0 = all equivalent.

Usage (from the project root):
    python test/machine2_dryrun/Verify_Vision_Config_Equivalence.py
options: --baseline <commit>  --files camera/X.py ...  --manifest extra.json ...  --props extra.properties ...
"""
import argparse, ast, json, pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
FUNCS = {"vInt", "vFloat", "vBool", "vStr", "vTuple", "vList", "vPoints"}

_ap = argparse.ArgumentParser()
_ap.add_argument("--baseline", default=None)
_ap.add_argument("--files", nargs="*", default=None)
_ap.add_argument("--manifest", nargs="*", default=[])
_ap.add_argument("--props", nargs="*", default=[])
ARGS = _ap.parse_args()

MANIFEST = pathlib.Path(__file__).with_name("vision_config_manifest.json")
manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
manifest.setdefault("extracted", {})
for extra in ARGS.manifest:
    manifest["extracted"].update(json.loads(pathlib.Path(extra).read_text()).get("extracted", {}))
BASELINE = ARGS.baseline or manifest.get("_baseline", "39ead96")


def git_show(rel: str) -> str:
    return subprocess.run(["git", "show", f"{BASELINE}:{rel}"], cwd=ROOT, capture_output=True, text=True).stdout


def scope_assignments(tree: ast.AST):
    """Map (scope path, name) -> literal value, for simple NAME = <literal> assignments (module/class/function)."""
    out = {}

    def visit(node, path):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, path + (child.name,))
            elif isinstance(child, (ast.Assign, ast.AnnAssign)):
                targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                for t in targets:
                    name = t.id if isinstance(t, ast.Name) else (t.attr if isinstance(t, ast.Attribute) else None)
                    if name is None or child.value is None:
                        continue
                    val = child.value
                    # np.array([...], dtype=...) -> compare the literal list it was built from
                    if isinstance(val, ast.Call) and isinstance(val.func, ast.Attribute) and val.func.attr == "array" and val.args:
                        val = val.args[0]
                    try:
                        out[(path, name)] = ast.literal_eval(val)
                    except Exception:
                        out[(path, name)] = ("<expr>", ast.unparse(child.value))
                visit(child, path)
            else:
                visit(child, path)
    visit(tree, ())
    return out


def vcalls(tree: ast.AST):
    """Yield (scope path, target name or None, func, key, default, lineno) for every vX(...) call."""
    res = []

    def visit(node, path, target):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, path + (child.name,), None)
                continue
            tgt = target
            if isinstance(child, (ast.Assign, ast.AnnAssign)):
                ts = child.targets if isinstance(child, ast.Assign) else [child.target]
                t = ts[0]
                tgt = t.id if isinstance(t, ast.Name) else (t.attr if isinstance(t, ast.Attribute) else None)
            if isinstance(child, ast.Call):
                fn = child.func.id if isinstance(child.func, ast.Name) else (child.func.attr if isinstance(child.func, ast.Attribute) else None)
                if fn in FUNCS and len(child.args) >= 2:
                    key = ast.literal_eval(child.args[0])
                    default = ast.literal_eval(child.args[1])
                    res.append((path, tgt, fn, key, default, child.lineno))
            visit(child, path, tgt)
    visit(tree, (), None)
    return res


def same(a, b) -> bool:
    if type(a) != type(b):
        return False
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


def parse_like(text: str, default, fn: str):
    from camera import VisionConfig as VC
    VC._raw = lambda k, _t=text: _t     # feed the properties text through the real parser
    VC._registry.pop("__verify__", None)
    value = {"vInt": VC.vInt, "vFloat": VC.vFloat, "vBool": VC.vBool, "vStr": VC.vStr,
             "vTuple": VC.vTuple, "vList": VC.vList, "vPoints": VC.vPoints}[fn]("__verify__", default)
    return value, VC._registry["__verify__"]["source"]


class _Normalise(ast.NodeTransformer):
    """Turn the NEW source back into what the OLD source should be: vX(key, default) -> default, drop the
    VisionConfig import, drop the definitions of constants extracted from inline literals and put the literal
    back wherever such a constant is referenced."""

    def __init__(self, extracted_consts):
        self.consts = extracted_consts          # name -> literal value

    def visit_Call(self, node):
        self.generic_visit(node)
        fn = node.func.id if isinstance(node.func, ast.Name) else (node.func.attr if isinstance(node.func, ast.Attribute) else None)
        if fn in FUNCS and len(node.args) >= 2:
            return node.args[1]
        return node

    def visit_ImportFrom(self, node):
        if node.module and node.module.endswith("VisionConfig"):
            return None
        return node

    def _drop_def(self, node):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = {t.id if isinstance(t, ast.Name) else (t.attr if isinstance(t, ast.Attribute) else None) for t in targets}
        return names and names <= set(self.consts)

    def visit_Assign(self, node):
        if self._drop_def(node):
            return None
        self.generic_visit(node)
        return node

    def visit_AnnAssign(self, node):
        if self._drop_def(node):
            return None
        self.generic_visit(node)
        return node

    def visit_Name(self, node):
        if node.id in self.consts and isinstance(node.ctx, ast.Load):
            return ast.copy_location(ast.parse(repr(self.consts[node.id]), mode="eval").body, node)
        return node

    def visit_Attribute(self, node):
        self.generic_visit(node)
        if node.attr in self.consts and isinstance(node.ctx, ast.Load):
            return ast.copy_location(ast.parse(repr(self.consts[node.attr]), mode="eval").body, node)
        return node

    def visit_JoinedStr(self, node):
        """Messages that now print a configurable value (f'... {NUT_MIN_ACTUAL_AREA}') are folded with the default
        substituted, exactly as Python would format it, so they must reproduce the baseline text character for character."""
        self.generic_visit(node)
        parts = []
        for v in node.values:
            if isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Constant) and v.conversion == -1:
                spec = v.format_spec            # already folded by generic_visit: None, a Constant, or a JoinedStr
                if isinstance(spec, ast.JoinedStr) and all(isinstance(x, ast.Constant) for x in spec.values):
                    spec = ast.Constant("".join(x.value for x in spec.values))
                if spec is not None and not isinstance(spec, ast.Constant):
                    parts.append(v); continue
                spec_text = "" if spec is None else spec.value
                v = ast.Constant(format(v.value.value, spec_text))
            if isinstance(v, ast.Constant) and parts and isinstance(parts[-1], ast.Constant):
                parts[-1] = ast.Constant(parts[-1].value + v.value)
            else:
                parts.append(v)
        if all(isinstance(p, ast.Constant) for p in parts):
            return ast.copy_location(ast.Constant("".join(p.value for p in parts)), node)
        node.values = parts
        return node


def ast_equivalent(rel, old_src, new_src, extracted):
    consts = {}
    for key, ex in extracted.items():
        if ex.get("file", rel) == rel and ex.get("const"):
            consts[ex["const"]] = ast.literal_eval(ex["old_literal"])
    old_names = {n.id for n in ast.walk(ast.parse(old_src)) if isinstance(n, ast.Name)} | \
                {n.attr for n in ast.walk(ast.parse(old_src)) if isinstance(n, ast.Attribute)}
    clash = [c for c in consts if c in old_names]
    norm_new = ast.fix_missing_locations(_Normalise(consts).visit(ast.parse(new_src)))
    norm_old = ast.fix_missing_locations(_Normalise({}).visit(ast.parse(old_src)))   # same f-string folding on both sides
    a, b = ast.dump(norm_old), ast.dump(norm_new)
    if a == b:
        return clash, None
    # locate the first differing top-level statement for the report
    for i, (x, y) in enumerate(zip(ast.walk(norm_old), ast.walk(norm_new))):
        if type(x) != type(y) or ast.dump(x)[:200] != ast.dump(y)[:200]:
            if hasattr(x, "lineno"):
                return clash, f"first difference near baseline line {getattr(x, 'lineno', '?')}: " \
                              f"{ast.unparse(x)[:160]!r}  vs  {ast.unparse(y)[:160]!r}"
    return clash, "ASTs differ (statement count differs)"


def load_properties():
    props = {}
    lines = (ROOT / "ApplicationConfiguration.properties").read_text(encoding="ascii").splitlines()
    for extra in ARGS.props:
        lines += pathlib.Path(extra).read_text(encoding="ascii").splitlines()
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, v = s.split("=", 1)
            if k.strip() in props and k.strip().startswith("vision."):
                print("PROBLEM: duplicate properties key", k.strip())
            props[k.strip()] = v.strip()
    return props


def main() -> int:
    files = ARGS.files or sorted(
        str(p.relative_to(ROOT)) for p in (ROOT / "camera").glob("*.py")
        if p.name != "VisionConfig.py" and any(f + "(" in p.read_text(encoding="utf-8") for f in FUNCS))
    props = load_properties()
    problems, checked, seen = [], 0, {}
    extracted = manifest.get("extracted", {})
    for rel in files:
        new_src = (ROOT / rel).read_text(encoding="utf-8")
        old_src = git_show(rel)
        if not old_src:
            problems.append(f"{rel}: not found in baseline {BASELINE}")
            continue
        clash, diff = ast_equivalent(rel, old_src, new_src, extracted)
        for c in clash:
            problems.append(f"{rel}: extracted constant name '{c}' already existed in the baseline file")
        if diff:
            problems.append(f"{rel}: NOT structurally identical to baseline after substituting defaults - {diff}")
        else:
            print(f"OK  {rel}: identical to baseline once every configurable value is replaced by its default")
        old_assign = scope_assignments(ast.parse(old_src))
        old_lines = old_src.splitlines()
        for path, tgt, fn, key, default, line in vcalls(ast.parse(new_src)):
            checked += 1
            where = f"{rel}:{line} {'.'.join(path + ((tgt,) if tgt else ()))} [{key}]"
            # 3. shared keys consistent
            if key in seen and not same(seen[key][0], default):
                problems.append(f"{where}: key also declared at {seen[key][1]} with a different default")
            seen.setdefault(key, (default, where))
            # 1. default == baseline value
            if key in extracted:
                ex = extracted[key]
                ln = ex.get("old_line", 0)
                text = old_lines[ln - 1] if 0 < ln <= len(old_lines) and ex.get("old_file", rel) == rel else \
                    (git_show(ex["old_file"]).splitlines()[ln - 1] if ex.get("old_file") and 0 < ln else "")
                if ex.get("old_snippet", "\x00") not in text:
                    problems.append(f"{where}: manifest snippet {ex.get('old_snippet')!r} not on baseline line {ln}: {text.strip()!r}")
                lit = ex.get("old_literal")
                if lit is None or not same(ast.literal_eval(lit), default):
                    problems.append(f"{where}: default {default!r} != manifest old_literal {lit!r}")
            else:
                cand = [v for (p, n), v in old_assign.items() if n == tgt and p == path] or \
                       [v for (p, n), v in old_assign.items() if n == tgt]
                if not cand:
                    problems.append(f"{where}: no baseline assignment for '{tgt}' and no manifest entry (extracted literal?)")
                elif not any(same(c, default) for c in cand):
                    problems.append(f"{where}: default {default!r} != baseline value(s) {cand!r}")
            # 2. properties value == default
            if key not in props:
                problems.append(f"{where}: key missing from ApplicationConfiguration.properties")
            else:
                parsed, source = parse_like(props[key], default, fn)
                if source != "config":
                    problems.append(f"{where}: properties value {props[key]!r} is not valid for {fn} (would fall back to default)")
                elif not same(parsed if not isinstance(default, tuple) else tuple(parsed), default):
                    problems.append(f"{where}: properties value {props[key]!r} parses to {parsed!r}, default is {default!r}")
    print(f"Baseline {BASELINE}: {checked} parameters in {len(files)} files, {len(seen)} distinct keys")
    for p in problems:
        print("PROBLEM:", p)
    print("RESULT:", "ALL EQUIVALENT" if not problems else f"{len(problems)} PROBLEM(S)")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
