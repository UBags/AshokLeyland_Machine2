"""
make_project_copies - writes the claude.ai Project copies of the docs (CLAUDE.md section 6) into ProjectCopies/.

The Project "Ashok Leyland - Machine 2" holds copies under claude/: HLD.md, LLD.md, PLC_Interface_Spec.md,
state_sync.md, plant-pc-verification.md, vision_positions_machine2.md and machine2-changes-log-81-onwards.md
(CHANGES_MACHINE2.md from section 81, with a two-line header). Run this after a pull, then replace those docs in the
Project with the files written here. ProjectCopies/ is not in git.

Run from anywhere:  python docs/tools/make_project_copies.py
"""
import pathlib
import re
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "ProjectCopies"
DOCS = ("HLD.md", "LLD.md", "PLC_Interface_Spec.md", "state_sync.md", "plant-pc-verification.md",
        "vision_positions_machine2.md")
FIRST_SECTION = 81


def changesFrom(text: str, first: int):
    """(the text from '## <first>.' on, the number of the last section)"""
    start = re.search(rf"^## {first}\. ", text, re.M)
    if start is None:
        raise SystemExit(f"CHANGES_MACHINE2.md has no section {first}")
    numbers = [int(n) for n in re.findall(r"^## (\d+)\. ", text, re.M)]
    return text[start.start():], max(numbers)


def main() -> int:
    OUT.mkdir(exist_ok=True)
    for name in DOCS:
        src = ROOT / "docs" / name
        if not src.is_file():
            print(f"missing: {src}")
            continue
        shutil.copyfile(src, OUT / name)
        print(f"{name:<40} {src.stat().st_size:>9,} bytes")
    body, last = changesFrom((ROOT / "CHANGES_MACHINE2.md").read_text(encoding="utf-8"), FIRST_SECTION)
    log = OUT / "machine2-changes-log-81-onwards.md"
    log.write_text(f"# Machine 2 changes log, from section {FIRST_SECTION}\n"
                   f"Copy of CHANGES_MACHINE2.md from §{FIRST_SECTION} onwards (branch main, up to §{last}).\n\n" + body,
                   encoding="utf-8")
    print(f"{log.name:<40} {log.stat().st_size:>9,} bytes (sections {FIRST_SECTION} - {last})")
    print(f"\nWritten to {OUT}. Replace the claude/<name> docs of the Project with these files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
