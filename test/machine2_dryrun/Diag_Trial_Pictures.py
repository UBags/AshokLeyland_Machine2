"""Diag_Trial_Pictures - what is in C:/Temp/TrialImages (read only, nothing moved or deleted).

For every picture in the sub-folders: the time in its name (the time it was SAVED), its folder, its mean brightness
(dark = below --dark), and a 16x9 grey thumbnail. Prints the pictures in time order across all folders, so one
cycle reads top to bottom, and marks:
  DARK      mean brightness below --dark
  SAME-AS   the picture is (nearly) the same scene as a picture in ANOTHER folder within --window seconds
            (thumbnail difference below --same): the same scene in two step folders.
Then a per-folder count and a csv (trial_pictures.csv in the current folder).

python Diag_Trial_Pictures.py [C:/Temp/TrialImages] [--since 2026-11-26] [--dark 25] [--same 6] [--window 120]
"""
import argparse, csv, re, sys
from datetime import datetime
from pathlib import Path
import cv2, numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("folder", nargs="?", default="C:/Temp/TrialImages")
ap.add_argument("--since", default=None, help="only pictures saved on or after this date (YYYY-MM-DD)")
ap.add_argument("--dark", type=float, default=25.0)
ap.add_argument("--same", type=float, default=6.0, help="mean abs grey difference of the thumbnails (0-255)")
ap.add_argument("--window", type=float, default=120.0)
a = ap.parse_args()
NAME = re.compile(r"(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d+)")
since = datetime.fromisoformat(a.since) if a.since else None
rows = []
for p in sorted(Path(a.folder).rglob("*.png")):
    m = NAME.search(p.stem)
    if not m:
        continue
    y, mo, d, h, mi, s, frac = m.groups()
    t = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s)).timestamp() + float("0." + frac)
    if since and t < since.timestamp():
        continue
    img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
    if img is None:
        continue
    rows.append(dict(time=t, folder=p.parent.relative_to(a.folder).as_posix(), file=p.name,
                     brightness=float(img.mean()), thumb=cv2.resize(img, (16, 9), interpolation=cv2.INTER_AREA).astype(np.float32)))
rows.sort(key=lambda r: r["time"])
if not rows:
    sys.exit(f"No pictures named by time under {a.folder}")
for i, r in enumerate(rows):
    r["dark"] = r["brightness"] < a.dark
    r["same_as"] = ""
    if r["dark"]:
        continue
    for o in rows[max(0, i - 60):i + 60]:
        if o is r or o["folder"] == r["folder"] or o["brightness"] < a.dark or abs(o["time"] - r["time"]) > a.window:
            continue
        if float(np.abs(o["thumb"] - r["thumb"]).mean()) < a.same:
            r["same_as"] = f"{o['folder']}/{o['file']} ({o['time'] - r['time']:+.1f} s)"
            break
prev = None
for r in rows:
    if prev is not None and r["time"] - prev > 60:
        print("-" * 100)
    prev = r["time"]
    flags = ("DARK " if r["dark"] else "") + (f"SAME-AS {r['same_as']}" if r["same_as"] else "")
    print(f"{datetime.fromtimestamp(r['time']).strftime('%m-%d %H:%M:%S.%f')[:-3]}  {r['folder']:<22} "
          f"{r['file']:<40} {r['brightness']:6.1f}  {flags}")
print("\nper folder: pictures / dark / same scene as another folder")
for f in sorted({r["folder"] for r in rows}):
    rs = [r for r in rows if r["folder"] == f]
    print(f"  {f:<22} {len(rs):5d} {sum(r['dark'] for r in rs):5d} {sum(bool(r['same_as']) for r in rs):5d}")
with open("trial_pictures.csv", "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(["saved", "folder", "file", "brightness", "dark", "same_as"])
    for r in rows:
        w.writerow([datetime.fromtimestamp(r["time"]).isoformat(), r["folder"], r["file"], f"{r['brightness']:.1f}", r["dark"], r["same_as"]])
print("written: trial_pictures.csv")
