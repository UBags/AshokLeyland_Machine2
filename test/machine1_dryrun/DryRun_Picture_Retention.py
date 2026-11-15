"""Dry run: picture clean-up at MainProgram start (utils/PictureRetention.py, CHANGES 110). No Redis, no database;
works on a temporary folder tree.

  P1 pictures older than the limit are deleted in every sub-folder (any picture extension, any case); younger ones,
     and files that are not pictures (FailedRecords JSON, notes), are kept
  P2 an empty date folder that was already older than the limit is removed; the per-check folders and the root stay
  P3 today's empty folder (makeFoldersForImages makes it while the clean-up runs) is kept
  P4 a folder that does not exist, and a limit of 0 (clean-up off), do nothing
  P5 the folders cleaned come from the properties: trial (30 days), Test and Production under basefolder.for.images
     (60 days)
  P6 MainProgram starts the clean-up in a background thread

Run from the project root:  python test/machine2_dryrun/DryRun_Picture_Retention.py   (exit code 1 on failure)
"""
import os, pathlib, sys, tempfile, time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("COSTHETA_PLC_VENDOR", "siemens" if HERE.name.startswith("machine2") else "allenbradley")

RESULTS = []
def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition)))
    print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""), flush=True)

from utils.PictureRetention import deleteOldPictures, retentionTargets

DAY = 86400
now = time.time()
root = tempfile.mkdtemp()

def make(rel, ageDays, folderAgeDays=None):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * 100)
    os.utime(path, (now - ageDays * DAY, now - ageDays * DAY))
    if folderAgeDays is not None:
        d = os.path.dirname(path)
        os.utime(d, (now - folderAgeDays * DAY, now - folderAgeDays * DAY))
    return path

old = [make("1-Knuckle/2026-08-01/a.png", 70, folderAgeDays=70), make("6-Cap/DOST/2026-08-02/b.JPG", 65)]
young = [make("1-Knuckle/2026-10-01/c.png", 40)]
other = [make("FailedRecords/2026-08-01_rec.json", 400), make("6-Cap/DOST/2026-08-02/notes.txt", 90)]
today = os.path.join(root, "2-HubAndBottomBearing/2026-11-16")
os.makedirs(today)
files, freed, folders = deleteOldPictures(root, 60, now)
check("P1 old pictures deleted in every sub-folder (any extension case)", all(not os.path.exists(p) for p in old)
      and files == 2 and freed == 200, f"{files} files, {freed} bytes")
check("P1 younger pictures kept", all(os.path.exists(p) for p in young))
check("P1 files that are not pictures kept (FailedRecords JSON, notes)", all(os.path.exists(p) for p in other))
check("P2 the old empty date folder removed; the per-check folder and the root stay",
      not os.path.isdir(os.path.join(root, "1-Knuckle/2026-08-01")) and os.path.isdir(os.path.join(root, "1-Knuckle"))
      and os.path.isdir(root), f"{folders} folder(s) removed")
check("P3 today's empty folder kept", os.path.isdir(today))
check("P4 a folder that does not exist: nothing", deleteOldPictures(os.path.join(root, "missing"), 30, now) == (0, 0, 0))
check("P4 limit 0 (clean-up off): nothing", deleteOldPictures(root, 0, now) == (0, 0, 0)
      and all(os.path.exists(p) for p in young))

targets = {d: (folder, days) for d, folder, days in retentionTargets()}
check("P5 trial pictures: 30 days, from picture.saving.dir.for.mode.trial",
      targets.get("trial pictures", (None, None))[1] == 30, targets.get("trial pictures"))
check("P5 Test and Production pictures: 60 days, under basefolder.for.images",
      targets.get("Test pictures", (None, None))[1] == 60 and targets.get("Production pictures", (None, None))[1] == 60
      and targets["Test pictures"][0].replace("\\", "/").endswith("/Test")
      and targets["Production pictures"][0].replace("\\", "/").endswith("/Production"),
      (targets.get("Test pictures"), targets.get("Production pictures")))
source = (ROOT / "MainProgram.py").read_text(encoding="utf-8")
check("P6 MainProgram starts the clean-up thread", "startPictureRetentionThread()" in source
      and "daemon=True" in (ROOT / "utils" / "PictureRetention.py").read_text(encoding="utf-8"))

failed = [n for n, ok_ in RESULTS if not ok_]
print(f"\nRESULT {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed" + (f"; FAILED: {failed}" if failed else ""),
      flush=True)
os._exit(1 if failed else 0)
