"""
Deletes old pictures, so that the picture folders do not fill the disk (CHANGES 110).

Run once, in a background thread, whenever MainProgram starts (startPictureRetentionThread):
- trial pictures (picture.saving.dir.for.mode.trial, default C:/Temp/TrialImages/, all sub-folders): older than
  picture.retention.days.trial (default 30 days);
- cycle pictures of both modes (basefolder.for.images + Test/ and Production/, all sub-folders): older than
  picture.retention.days.main (default 60 days).

Only picture files are deleted (by extension); anything else (the DB server's FailedRecords JSON files, for example)
is left alone. Age is the file's modification time. Empty folders are removed if they were already older than the
cutoff before the run (never the roots, never a folder just made for today). A value of 0 or less for a retention key turns that clean-up off.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Iterable, Tuple

PICTURE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp")
SECONDS_PER_DAY = 24 * 60 * 60


def deleteOldPictures(root: str, maxAgeDays: float, now: float | None = None) -> Tuple[int, int, int]:
    """Deletes picture files older than maxAgeDays under root (all sub-folders) and the folders left empty.
    Returns (files deleted, bytes freed, folders removed). A root that does not exist gives (0, 0, 0)."""
    if maxAgeDays is None or maxAgeDays <= 0 or not root or not os.path.isdir(root):
        return 0, 0, 0
    now_ = time.time() if now is None else now
    cutoff = now_ - maxAgeDays * SECONDS_PER_DAY
    rootPath = os.path.normcase(os.path.abspath(root))
    files = freed = folders = 0
    # folder ages as they were before this run: a folder is removed only if it is empty AND was already older than
    # the cutoff (a folder just made for today's pictures - makeFoldersForImages runs at the same time - is kept)
    folderTimes = {}
    for folder, _subFolders, _names in os.walk(root):
        try:
            folderTimes[folder] = os.stat(folder).st_mtime
        except OSError:
            pass
    for folder, _subFolders, names in os.walk(root, topdown=False):
        for name in names:
            if not name.lower().endswith(PICTURE_EXTENSIONS):
                continue
            path = os.path.join(folder, name)
            try:
                info = os.stat(path)
                if info.st_mtime < cutoff:
                    os.remove(path)
                    files += 1
                    freed += info.st_size
            except OSError:
                pass                                    # in use or already gone: try again at the next start
        if os.path.normcase(os.path.abspath(folder)) != rootPath and folderTimes.get(folder, now_) < cutoff:
            try:
                if not os.listdir(folder):
                    os.rmdir(folder)
                    folders += 1
            except OSError:
                pass
    return files, freed, folders


def retentionTargets() -> Iterable[Tuple[str, str, float]]:
    """(description, folder, days) for every folder to clean, from the properties."""
    from Configuration import CosThetaConfigurator
    config = CosThetaConfigurator.getInstance()
    trialDays = config.getPictureRetentionDaysTrial()
    mainDays = config.getPictureRetentionDaysMain()
    base = config.getBaseFolderForImages()
    targets = [("trial pictures", config.getMainPictureSavingDirForModeTrial(), trialDays)]
    if base:                                            # never relative to the current folder
        targets += [("Test pictures", os.path.join(base, "Test"), mainDays),
                    ("Production pictures", os.path.join(base, "Production"), mainDays)]
    return targets


def runPictureRetention() -> None:
    from utils.CosThetaPrintUtils import printBoldBlue, printBoldYellow
    for description, folder, days in retentionTargets():
        if days <= 0:
            printBoldBlue(f"Picture clean-up of {description} ({folder}) is turned off")
            continue
        started = time.monotonic()
        try:
            files, freed, folders = deleteOldPictures(folder, days)
        except Exception as e:                          # never stops MainProgram
            printBoldYellow(f"Picture clean-up of {description} ({folder}) failed: {e!r}")
            continue
        printBoldBlue(f"Picture clean-up: {files} {description} older than {days:g} days deleted from {folder} "
                      f"({freed / 1e6:.1f} MB freed, {folders} empty folders removed, "
                      f"{time.monotonic() - started:.1f} s)")


def startPictureRetentionThread() -> threading.Thread:
    """Starts the clean-up in a daemon thread (it may take a while on a full disk) and returns the thread."""
    thread = threading.Thread(name="Picture Retention", target=runPictureRetention, daemon=True)
    thread.start()
    return thread
