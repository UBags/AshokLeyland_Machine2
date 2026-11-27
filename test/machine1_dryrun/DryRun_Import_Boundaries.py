"""
DryRun_Import_Boundaries - production code must not depend on the test package (Uddi, 2026-11-27, CHANGES 214).

Production = every .py file in git outside test/ and archive/. For each, every import (module level or inside a
function, `import x` / `from x import y`, importlib.import_module("x"), runpy.run_path / run_module, sys.path
entries) is checked; it fails when one resolves to a module under test/ (the test/ package, a dry-run folder, or a
module whose only copy is a file under test/), or when a production file puts a test folder on sys.path.

    python test/machine1_dryrun/DryRun_Import_Boundaries.py       (from the project root; exit code 1 on a violation)
"""
import ast
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def gitFiles():
    out = subprocess.run(["git", "ls-files", "-z", "*.py"], cwd=ROOT, capture_output=True, text=True).stdout
    return [f for f in out.split("\0") if f]


def main() -> int:
    files = gitFiles()
    production = [f for f in files if not f.startswith(("test/", "archive/"))]
    testFiles = [f for f in files if f.startswith("test/")]
    productionModules = {f[:-3].replace("/", ".") for f in production} | \
                        {os.path.splitext(os.path.basename(f))[0] for f in production}
    testOnly = {os.path.splitext(os.path.basename(f))[0] for f in testFiles} - productionModules
    testDotted = {f[:-3].replace("/", ".") for f in testFiles}

    def isTest(module: str) -> bool:
        return bool(module) and (module == "test" or module.startswith("test.") or module in testDotted
                                 or module.split(".")[0] in testOnly)

    problems = []
    for f in production:
        try:
            tree = ast.parse((ROOT / f).read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError as e:
            problems.append(f"{f}: cannot be parsed ({e})")
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                fn = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                text = node.args[0].value
                if fn in ("import_module", "run_module", "__import__"):
                    names = [text]
                elif fn in ("run_path", "spec_from_file_location", "insert", "append") and \
                        ("test/" in text.replace("\\", "/") or "dryrun" in text):
                    problems.append(f"{f}:{node.lineno}: {fn}({text!r}) reaches into the test package")
            for m in names:
                if isTest(m):
                    problems.append(f"{f}:{node.lineno}: imports {m} (test package)")
    print(f"{len(production)} production files checked against {len(testFiles)} test files")
    for p in problems:
        print("FAIL", p)
    print("RESULT", "PASS: no production file depends on the test package" if not problems
          else f"{len(problems)} violation(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
