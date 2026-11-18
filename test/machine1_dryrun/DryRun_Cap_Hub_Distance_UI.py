"""
DryRun_Cap_Hub_Distance_UI - the cap seating step's hub-centre distance (CHANGES 143) on real pictures, the way the
camera server runs it: the hub check on a hub picture gives the hub's centre, then the cap check runs on every cap
picture of a folder with that hub centre. Everything needed to set the distance limit and the holes / bolts offsets is
printed and written to files.

  1. "Model": DOST or DOSTPLUS - the QR code both checks are given (the hub check needs 4 / 5 holes or bolts of that
     model; the cap check needs that model's cap).
  2. "Hub picture...": the real hub check (CheckHubAndBottomBearing, YOLO + MobileSAM) runs on it. Its marked picture is
     shown (the winning group green, the hub centre a blue cross; no text) and the hub centre filled in: x, y and what
     it came from (holes / bolts), circle radius and spread in the list. The three fields can be edited (or typed in
     without a hub picture) - the cap checks use what they show.
  3. "Caps folder..." (every jpg / jpeg / png / bmp in it, by name), then Start: the real cap check (CheckCap.checkCap,
     as the camera server calls it, with the seating step and the hub centre) on each picture, one at a time,
     "Seconds between pictures" apart (1 - 10). Stop ends the run.
  4. The cap check's step 3 (brightening) needs the hub, top bearing and nut / washer pictures of the same part: the
     hub picture chosen in 2 (else the model's first in hub_images/), and the model's first top bearing and nut
     pictures in this machine's dry-run folders, or those chosen with "Top bearing..." / "Nut / washer...".
  5. "Max distance" (0.5 - 20 px) and the holes / bolts offsets (dx, dy px added to the hub centre) of the chosen model
     are used from the next picture on (they start at this machine's vision.cap.seating.hub.distance.offset.<holes|bolts>.
     <model> values and follow the Model choice; per model since CHANGES 147). The hub-centre rule is
     always applied here (also on Machine 1, whose seating step is off: there only the measurement is shown).
  6. Per cap: verdict and time; the colour rules' seating verdict, the distance verdict, the cap centre (stage-2
     ellipse), the hub centre + offset, distance, dx / dy, cap mask area / stability / predicted IoU, outline points
     within 2.5 px (stage 1 / 2), axis ratio, RMS; then every log line of the check. A cap the check stopped before
     the seating step (wrong cap, brightening failed) still gets the distance measured, marked "measured only".
  7. At the end, from the caps whose colour rules said SEATED: dx / dy mean, median, spread, min / max per hub kind,
     the offset that centres them (median) and the distance limit that keeps them all with 1.5 px to spare; the caps
     whose name contains "skewed" are listed against that limit. Written to <caps folder>/cap_hub_distance_results/:
     run_<time>.txt (everything shown) and details_<time>.csv (one row per cap); "Save marked pictures" adds the
     check's marked pictures there.

  8. Pairing by cycle (CHANGES 150): "Hub folder (pair by time)..." (batch: --hub-folder <folder>) instead of one
     hub picture. Each cap picture is measured against the hub picture of its own cycle: the latest picture in the hub
     folder taken before the cap picture and at most --pair-max-secs (300) before it, by the time in the file names
     (trial pictures "2026-11-17-17-04-00-749671-0.png", camera pictures "<ip>_01_20261113152706609.jpeg"). The hub
     check runs once per hub picture (kept); that picture is also the cap check's step-3 hub picture. A cap with no hub
     picture of its cycle gets no distance. This is how the camera server runs it, so offsets and limits measured this
     way are the ones production needs; one hub picture for caps of other parts only measures how far those parts sat
     from that one part. The summary also lists the caps the colour rules said NOT SEATED against the suggested limit.

Settings: <vendor>.vision.cap.* / vision.hub.* in ApplicationConfiguration.properties (vendor from the folder: machine1 =
allenbradley, machine2 = siemens). Models through ModelManager (MobileSAM, YOLO): needs models/ (the laptop).

Run from the project root:   python test/machine1_dryrun/DryRun_Cap_Hub_Distance_UI.py
Without a window:            python test/machine1_dryrun/DryRun_Cap_Hub_Distance_UI.py --batch --caps <folder>
                               [--model DOST|DOSTPLUS] [--hub <hub picture> | --hub-centre x,y,holes|bolts]
                               [--top <picture>] [--nut <picture>] [--max 5.0] [--offset-holes dx,dy]
                               [--offset-bolts dx,dy] [--save] [--hub-folder <folder> [--pair-max-secs 300]]
"""
import csv
import datetime
import os
import pathlib
import re
import statistics
import sys
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["COSTHETA_PLC_VENDOR"] = "siemens" if HERE.name.startswith("machine2") else "allenbradley"

import cv2
import numpy as np

QR = {"DOST": "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$",
      "DOSTPLUS": "8201206$400112VA1C$22881$15.10.2026$C5211702$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$"}
EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp")
VIEW_W, VIEW_H = 960, 540
DATA = HERE if (HERE / "cap_images").is_dir() else HERE.parent / "machine2_dryrun"
LIMIT_MARGIN = 1.5                           # px added to the largest seated deviation for the suggested limit
CSV_COLUMNS = ["picture", "model", "verdict", "seconds", "colour_seated", "seating_run", "distance_ok", "measured_only",
               "cap_found", "cap_cx", "cap_cy", "hub_kind", "hub_cx", "hub_cy", "offset_dx", "offset_dy", "target_cx",
               "target_cy", "dx", "dy", "distance", "limit", "mask_area", "mask_stability", "mask_iou", "outline_points",
               "in1", "in2", "ratio", "rms2", "seating_reason", "reason", "hub_picture", "hub_seconds_before"]
PAIR_MAX_SECS = 300.0                        # a cycle's hub picture is at most this long before its cap picture
_TRIAL_NAME = re.compile(r"(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{1,6})")
_CAMERA_NAME = re.compile(r"_(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(\d{1,3})(?:\D|$)")


def pictureTime(path):
    """The time a picture was taken, from its file name (trial or camera picture name), in seconds; None if unknown."""
    name = pathlib.Path(path).stem
    m = _TRIAL_NAME.search(name)
    if m:
        frac = int(m.group(7)) / 1e6           # microseconds, written without leading zeros
    else:
        m = _CAMERA_NAME.search(name)
        if not m:
            return None
        frac = int(m.group(7)) / 1000.0         # milliseconds, written without leading zeros
    y, mo, d, h, mi, se = (int(v) for v in m.groups()[:6])
    try:
        return datetime.datetime(y, mo, d, h, mi, se).timestamp() + frac
    except ValueError:
        return None


class HubPairer:
    """The hub picture of each cap picture's cycle: the latest one in the hub folder taken before the cap picture and at
    most maxSecs before it. The hub check runs once per hub picture (kept)."""

    def __init__(self, folder, maxSecs=PAIR_MAX_SECS):
        self.maxSecs = float(maxSecs)
        self.hubs = sorted((t, p) for p in picturesIn(folder) for t in [pictureTime(p)] if t is not None)
        self.results = {}

    def hubPathFor(self, capPath):
        """(hub picture, seconds before the cap) or (None, reason)."""
        t = pictureTime(capPath)
        if t is None:
            return None, "no time in the cap picture's name"
        before = [(ht, hp) for ht, hp in self.hubs if ht <= t]
        if not before:
            return None, "no hub picture before it"
        ht, hp = before[-1]
        if t - ht > self.maxSecs:
            return None, f"no hub picture of this cycle (latest {t - ht:.0f} s before, limit {self.maxSecs:.0f} s)"
        return hp, t - ht

    def hubFor(self, checks, capPath, model):
        """(hub path, hub check result or None, seconds before or reason)."""
        hp, info = self.hubPathFor(capPath)
        if hp is None:
            return None, None, info
        key = (str(hp), model)
        if key not in self.results:
            picture = readPicture(hp)
            self.results[key] = None if picture is None else dict(runHubCheck(checks, picture, model), picture=picture)
        return hp, self.results[key], info


def picturesIn(folder):
    folder = pathlib.Path(folder)
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in EXTENSIONS) if folder.is_dir() else []


def readPicture(path):
    image = cv2.imread(str(path))
    if image is None:
        return None
    return cv2.resize(image, (1280, 720)) if image.shape[:2] != (720, 1280) else image


def storedPicture(model, kind):
    """The model's first stored hub / top bearing / nut picture in this machine's dry-run folders, or None."""
    pattern, sub = {"hub": ("{m}_[0-9]*", "hub_images"), "top": ("{m}_bearing_*", "top_bearing_images"),
                    "nut": ("{m}_nut_*", "nut_washer_images")}[kind]
    files = sorted(p for p in (DATA / sub).glob(pattern.format(m=model) + ".*") if p.suffix.lower() in EXTENSIONS)
    return files[0] if files else None


def parsePair(text):
    a, b = [float(v) for v in str(text).replace(" ", "").split(",")]
    return a, b


class Checks:
    """The real checks, loaded on first use (the hub check needs torch / YOLO). Their log lines are collected."""

    def __init__(self):
        self.messages = []
        self.capModule = self.capClass = self.hubModule = self.hubClass = None

    def _collect(self, level, source, message, *args, **kwargs):
        self.messages.append((level, str(message)))

    def loadCap(self):
        if self.capModule is None:
            import camera.CheckCap as module
            self.capModule, self.capClass = module, module.CheckCap
            module.logBoth = self._collect
        return self.capClass

    def loadHub(self):
        if self.hubModule is None:
            import camera.CheckHubAndBottomBearing as module
            self.hubModule, self.hubClass = module, module.CheckHubAndBottomBearing
            module.logBoth = self._collect
        return self.hubClass

    def warmUp(self):
        """Load MobileSAM and YOLO once ('' or the error)."""
        try:
            self.loadCap()
            from camera.ModelManager import ModelManager
            manager = ModelManager.get_instance()
            manager.get_sam_model()
            manager.get_yolo_model()
            return ""
        except Exception as e:
            return f"{type(e).__name__}: {e}"


def runHubCheck(checks, picture, model):
    """The hub check -> dict(ok, marked, hub, gamma, seconds, lines)."""
    hubClass = checks.loadHub()
    checks.messages.clear()
    t0 = time.perf_counter()
    marked, ok, gamma = hubClass.checkHubAndBottomBearing(picture.copy(), QR[model], use_smart_gamma=True)
    secs = time.perf_counter() - t0
    hub = hubClass.lastHubCentre if ok else None
    marked = (marked if marked is not None else picture).copy()
    if hub is not None:
        cv2.drawMarker(marked, (int(round(hub["cx"])), int(round(hub["cy"]))), (255, 128, 0), cv2.MARKER_CROSS, 18, 2)
    lines = [f"hub check {'OK' if ok else 'NOT OK'} as {model} (gamma {gamma}, {secs:.2f} s)"]
    lines.append(f"    hub centre ({hub['cx']:.1f}, {hub['cy']:.1f}) from {hub['count']} {hub['kind']} (circle r "
                 f"{hub['radius']:.1f}, spread {hub['spread']:.2f} px)" if hub else "    no hub centre")
    lines += [f"    [{level}] {m}" for level, m in checks.messages]
    return dict(ok=bool(ok), marked=marked, hub=hub, gamma=gamma, seconds=secs, lines=lines)


def runCap(checks, picture, model, hubCentre, bases, maxDistance, offsetHoles, offsetBolts):
    """The cap check as the camera server calls it, with the hub centre -> dict(ok, marked, row values, lines)."""
    capClass = checks.loadCap()
    module = checks.capModule
    plus = model == "DOSTPLUS"                  # the offsets are per model (CHANGES 147): set this model's pair
    holesName = "SEATING_HUB_DISTANCE_OFFSET_HOLES_" + ("DOSTPLUS" if plus else "DOST")
    boltsName = "SEATING_HUB_DISTANCE_OFFSET_BOLTS_" + ("DOSTPLUS" if plus else "DOST")
    saved = (capClass.SEATING_HUB_DISTANCE_CHECK, capClass.SEATING_HUB_DISTANCE_MAX,
             getattr(capClass, holesName), getattr(capClass, boltsName))
    realSeating, realColour = capClass.checkSeating, capClass._seatingByColour
    seen = {}

    def colour(*a, **k):
        result = realColour(*a, **k)
        seen["colour"] = dict(result)            # a copy: checkSeating adds the distance rule to this dict
        return result

    def seating(*a, **k):
        seen["seating"] = realSeating(*a, **k)
        return seen["seating"]
    checks.messages.clear()
    try:
        capClass.SEATING_HUB_DISTANCE_CHECK = True
        capClass.SEATING_HUB_DISTANCE_MAX = float(maxDistance)
        setattr(capClass, holesName, tuple(float(v) for v in offsetHoles))
        setattr(capClass, boltsName, tuple(float(v) for v in offsetBolts))
        capClass._seatingByColour = staticmethod(colour)
        capClass.checkSeating = staticmethod(seating)
        current = {module.hubAndBottomBearingPictureKeyString: bases["hub"],
                   module.topBearingPictureKeyString: bases["top"],
                   module.nutAndPlateWasherPictureKeyString: bases["nut"]}
        t0 = time.perf_counter()
        marked, ok = capClass.checkCap(anImage=picture.copy(), currentPictures=current, componentQRCode=QR[model],
                                       runSeating=True, hubCentre=hubCentre)
        secs = time.perf_counter() - t0
        rule, measuredOnly = None, False
        if "seating" not in seen and hubCentre:                  # stopped before the seating step: measure anyway
            rule, measuredOnly = capClass.hubCentreDistance(picture, hubCentre, model), True
        elif hubCentre:
            rule = dict(cap=None, target=seen["seating"].get("hub_target"), distance=seen["seating"].get("hub_distance"))
            rule["ok"] = rule["distance"] is not None and rule["distance"] <= float(maxDistance)
    finally:
        capClass.SEATING_HUB_DISTANCE_CHECK, capClass.SEATING_HUB_DISTANCE_MAX = saved[0], saved[1]
        setattr(capClass, holesName, saved[2])
        setattr(capClass, boltsName, saved[3])
        capClass.checkSeating, capClass._seatingByColour = realSeating, realColour
    return dict(ok=bool(ok), marked=marked if marked is not None else picture, seconds=secs, colour=seen.get("colour"),
                seating=seen.get("seating"), rule=rule, measuredOnly=measuredOnly, messages=list(checks.messages))


class CapCatcher:
    """Keeps CapMaskFinder.findCap's last result (the seating step does not return it)."""

    def __init__(self):
        self.last = None
        self._real = None

    def install(self):
        from camera import CapMaskFinder
        if self._real is None:
            self._real = CapMaskFinder.findCap

            def find(*a, **k):
                self.last = self._real(*a, **k)
                return self.last
            CapMaskFinder.findCap = find


def describeCap(name, model, res, hubCentre, maxDistance, offsets, cap):
    """(csv row, list of text lines) for one cap."""
    colour, seating, rule = res["colour"], res["seating"], res["rule"]
    row = dict(picture=name, model=model, verdict="OK" if res["ok"] else "NOT OK", seconds=round(res["seconds"], 3),
               colour_seated=(None if colour is None else bool(colour.get("seated"))), seating_run=seating is not None,
               measured_only=res["measuredOnly"], limit=maxDistance,
               seating_reason=(seating or {}).get("reason", ""))
    errors = [m for level, m in res["messages"] if level in ("logError", "logCritical")]
    row["reason"] = "" if res["ok"] else "; ".join(errors[-2:])
    lines = [f"{name}: {row['verdict']} as {model} ({res['seconds']:.2f} s)"
             + ("" if res["ok"] else f" - {row['reason']}")]
    if colour is not None:
        lines.append(f"    colour rules: {'SEATED' if colour.get('seated') else 'NOT SEATED'} - {colour.get('reason', '')}")
    elif not res["ok"]:
        lines.append("    the cap check stopped before the seating step")
    if hubCentre:
        kind = hubCentre.get("kind", "holes")
        dx0, dy0 = offsets[1] if kind == "bolts" else offsets[0]
        row.update(hub_kind=kind, hub_cx=hubCentre["cx"], hub_cy=hubCentre["cy"], offset_dx=dx0, offset_dy=dy0)
    if cap is not None:
        fit = cap.get("fit")
        row.update(cap_found=bool(cap.get("found")), mask_area=cap.get("area"), mask_stability=cap.get("stability"),
                   mask_iou=cap.get("iou"))
        if fit:
            e2 = fit["e2"]
            row.update(cap_cx=e2[0], cap_cy=e2[1], outline_points=fit["points"], in1=fit["in1"], in2=fit["in2"],
                       ratio=e2[3] / max(e2[2], 1e-6), rms2=fit["rms2"])
    if rule is not None and rule.get("target") is not None and row.get("cap_cx") is not None:
        tx, ty = rule["target"]
        dx, dy = row["cap_cx"] - tx, row["cap_cy"] - ty
        row.update(target_cx=tx, target_cy=ty, dx=dx, dy=dy, distance=float(np.hypot(dx, dy)),
                   distance_ok=float(np.hypot(dx, dy)) <= float(maxDistance))
        lines.append(f"    distance{' (measured only)' if res['measuredOnly'] else ''}: cap ({row['cap_cx']:.1f}, "
                     f"{row['cap_cy']:.1f}) to hub + offset ({tx:.1f}, {ty:.1f}) = {row['distance']:.2f} px "
                     f"{'<=' if row['distance_ok'] else '>'} {maxDistance:g}; dx {dx:+.2f}, dy {dy:+.2f} "
                     f"(hub {row.get('hub_kind')}, offset {row.get('offset_dx'):+g}, {row.get('offset_dy'):+g})")
    elif hubCentre:
        lines.append(f"    distance: not measured ({(cap or {}).get('reason') or 'no cap mask'})")
    else:
        lines.append("    distance: no hub centre")
    if cap is not None:
        from camera import CapMaskFinder
        lines.append(f"    cap mask: {CapMaskFinder.describe(cap)}")
    lines += [f"    [{level}] {m}" for level, m in res["messages"]]
    return row, lines


def summarise(rows, maxDistance):
    """The figures for the limit and the offsets, from the caps whose colour rules said SEATED."""
    out = ["", "=== Summary ===", f"{len(rows)} caps; OK {sum(r['verdict'] == 'OK' for r in rows)}, "
                                   f"NOT OK {sum(r['verdict'] != 'OK' for r in rows)} (limit {maxDistance:g} px)"]
    measured = [r for r in rows if r.get("dx") is not None]
    out.append(f"distance measured on {len(measured)}; colour rules SEATED on "
               f"{sum(1 for r in rows if r.get('colour_seated'))}")
    for kind in sorted({r.get("hub_kind") for r in measured if r.get("hub_kind")}):
        seated = [r for r in measured if r.get("hub_kind") == kind and r.get("colour_seated")
                  and "skewed" not in r["picture"].lower()]
        if not seated:
            out.append(f"hub from {kind}: no seated cap with a distance")
            continue
        dx = [r["dx"] + r["offset_dx"] for r in seated]           # against the hub centre itself (offset taken out)
        dy = [r["dy"] + r["offset_dy"] for r in seated]
        mdx, mdy = statistics.median(dx), statistics.median(dy)
        dev = [float(np.hypot(a - mdx, b - mdy)) for a, b in zip(dx, dy)]
        stat = lambda v: (f"mean {statistics.mean(v):+.2f}, median {statistics.median(v):+.2f}, sd "
                          f"{statistics.pstdev(v):.2f}, min {min(v):+.2f}, max {max(v):+.2f}")
        out.append(f"hub from {kind}, {len(seated)} seated caps (cap centre - hub centre, no offset):")
        out.append(f"    dx {stat(dx)}")
        out.append(f"    dy {stat(dy)}")
        models = sorted({r["model"] for r in seated})
        out.append(f"    suggested offset.{kind}.{'/'.join(m.lower() for m in models)} = {mdx:.1f},{mdy:.1f}; with it the seated caps lie "
                   f"{min(dev):.2f} - {max(dev):.2f} px from the hub centre -> suggested max = {max(dev) + LIMIT_MARGIN:.1f} "
                   f"(largest + {LIMIT_MARGIN:g})")
        skewed = [r for r in measured if r.get("hub_kind") == kind and "skewed" in r["picture"].lower()]
        for r in skewed:
            d = float(np.hypot(r["dx"] + r["offset_dx"] - mdx, r["dy"] + r["offset_dy"] - mdy))
            out.append(f"    skewed {r['picture']}: {d:.2f} px with the suggested offset "
                       f"({'caught' if d > max(dev) + LIMIT_MARGIN else 'NOT caught'} by the suggested max)")
        notSeated = [r for r in measured if r.get("hub_kind") == kind and not r.get("colour_seated")
                     and r.get("seating_run")]
        for r in notSeated:                        # the colour rules said NOT SEATED: does the distance agree?
            d = float(np.hypot(r["dx"] + r["offset_dx"] - mdx, r["dy"] + r["offset_dy"] - mdy))
            out.append(f"    colour NOT SEATED {r['picture']}: {d:.2f} px with the suggested offset "
                       f"({'also caught' if d > max(dev) + LIMIT_MARGIN else 'NOT caught'} by the suggested max)")
    paired = [r for r in rows if r.get("hub_picture")]
    if paired:
        out.append(f"paired by cycle: {len(paired)} caps measured against the hub picture of their own cycle "
                   f"({len({r['hub_picture'] for r in paired})} hub pictures); "
                   f"{sum(1 for r in rows if not r.get('hub_picture'))} without one")
    else:
        out.append("The hub picture is not of the same part as the caps unless they were taken in one cycle: the "
                   "figures are only as good as that pairing (use Hub folder (pair by time) for real figures).")
    return out


class Run:
    """One run over a caps folder: rows, lines, files."""

    def __init__(self, capsFolder):
        self.out = pathlib.Path(capsFolder) / "cap_hub_distance_results"
        self.out.mkdir(exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.txt, self.csv = self.out / f"run_{stamp}.txt", self.out / f"details_{stamp}.csv"
        self.rows = []
        with open(self.csv, "w", newline="") as f:
            csv.DictWriter(f, CSV_COLUMNS).writeheader()

    def write(self, lines):
        with open(self.txt, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def add(self, row):
        self.rows.append(row)
        with open(self.csv, "a", newline="") as f:
            csv.DictWriter(f, CSV_COLUMNS, restval="", extrasaction="ignore").writerow(
                {k: (f"{v:.3f}" if isinstance(v, float) else v) for k, v in row.items()})


def capPass(checks, catcher, path, model, hubCentre, bases, maxDistance, offsets, pairer=None):
    picture = readPicture(path)
    if picture is None:
        row = dict(picture=path.name, model=model, verdict="UNREADABLE")
        return row, [f"{path.name}: could not be read"], None
    pairLines, pairCols = [], {}
    if pairer is not None:                       # the hub picture of this cap's cycle (CHANGES 150)
        hp, hubRes, info = pairer.hubFor(checks, path, model)
        hubCentre = None
        if hp is None:
            pairLines.append(f"    paired hub: {info} - distance not measured")
        elif hubRes is None:
            pairLines.append(f"    paired hub: {hp.name} could not be read")
            pairCols = dict(hub_picture=hp.name, hub_seconds_before=float(info))
        else:
            hubCentre = hubRes["hub"]
            bases = dict(bases, hub=hubRes["picture"])
            pairLines.append(f"    paired hub: {hp.name}, {info:.1f} s before; " + hubRes["lines"][0]
                             + (f"; centre ({hubCentre['cx']:.1f}, {hubCentre['cy']:.1f}) from {hubCentre['count']} "
                                f"{hubCentre['kind']}" if hubCentre else " - no hub centre"))
            pairCols = dict(hub_picture=hp.name, hub_seconds_before=float(info))
    catcher.last = None
    res = runCap(checks, picture, model, hubCentre, bases, maxDistance, offsets[0], offsets[1])
    cap = (res["rule"] or {}).get("cap") or catcher.last
    row, lines = describeCap(path.name, model, res, hubCentre, maxDistance, offsets, cap)
    row.update(pairCols)
    return row, lines[:1] + pairLines + lines[1:], res["marked"]


def basesFor(model, hubPicture, topPath, nutPath):
    """The earlier pictures for step 3 and the lines saying where they came from."""
    hubPath = storedPicture(model, "hub")
    bases = dict(hub=hubPicture if hubPicture is not None else (readPicture(hubPath) if hubPath else None),
                 top=readPicture(topPath or storedPicture(model, "top")) if (topPath or storedPicture(model, "top")) else None,
                 nut=readPicture(nutPath or storedPicture(model, "nut")) if (nutPath or storedPicture(model, "nut")) else None)
    note = (f"earlier pictures for step 3: hub {'the hub picture chosen' if hubPicture is not None else hubPath}, "
            f"top bearing {topPath or storedPicture(model, 'top')}, nut {nutPath or storedPicture(model, 'nut')}")
    return bases, note


def argValue(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def runBatch():
    checks, catcher = Checks(), CapCatcher()
    capClass = checks.loadCap()
    catcher.install()
    model = argValue("--model", "DOST")
    hubPicture, hub = None, None
    header = [f"Machine {os.environ['COSTHETA_PLC_VENDOR']}, model {model}"]
    if "--hub" in sys.argv:
        hubPicture = readPicture(argValue("--hub"))
        h = runHubCheck(checks, hubPicture, model)
        hub = h["hub"]
        header += [f"hub picture {argValue('--hub')}"] + h["lines"]
    if "--hub-centre" in sys.argv:
        x, y, kind = argValue("--hub-centre").split(",")
        hub = dict(cx=float(x), cy=float(y), kind=kind, count=None, radius=None, spread=None)
        header.append(f"hub centre typed: ({float(x):.1f}, {float(y):.1f}) {kind}")
    maxDistance = float(argValue("--max", capClass.SEATING_HUB_DISTANCE_MAX))
    offsets = (parsePair(argValue("--offset-holes", ",".join(map(str, capClass.hubOffset("holes", model))))),
               parsePair(argValue("--offset-bolts", ",".join(map(str, capClass.hubOffset("bolts", model))))))
    bases, note = basesFor(model, hubPicture, argValue("--top"), argValue("--nut"))
    header += [note, f"max distance {maxDistance:g} px, offset holes {offsets[0]}, bolts {offsets[1]}"]
    pairer = None
    if "--hub-folder" in sys.argv:
        pairer = HubPairer(argValue("--hub-folder"), float(argValue("--pair-max-secs", PAIR_MAX_SECS)))
        header.append(f"pairing by cycle: hub folder {argValue('--hub-folder')} ({len(pairer.hubs)} pictures with a "
                      f"time in the name), hub picture at most {pairer.maxSecs:.0f} s before the cap picture")
    run = Run(argValue("--caps"))
    print("\n".join(header), flush=True)
    run.write(header)
    for path in picturesIn(argValue("--caps")):
        row, lines, marked = capPass(checks, catcher, path, model, hub, bases, maxDistance, offsets, pairer)
        run.add(row)
        run.write(lines)
        print("\n".join(lines), flush=True)
        if "--save" in sys.argv and marked is not None:
            cv2.imwrite(str(run.out / path.name), marked)
    tail = summarise(run.rows, maxDistance) + [f"files: {run.txt}, {run.csv}"]
    run.write(tail)
    print("\n".join(tail), flush=True)
    return 0


def runWindow():
    import tkinter as tk
    from tkinter import filedialog, ttk
    from tkinter.scrolledtext import ScrolledText
    from PIL import Image, ImageTk

    class App:
        def __init__(self, root):
            self.root, self.checks, self.catcher = root, Checks(), CapCatcher()
            root.title("Cap seating: hub-centre distance (CHANGES 143)")
            self.model = tk.StringVar(master=root, value="DOST")
            self.model.trace_add("write", self.fillOffsets)
            self.hubPath = self.capsFolder = self.topPath = self.nutPath = self.hubFolder = None
            self.hubPicture = None
            self.hx, self.hy = tk.StringVar(master=root), tk.StringVar(master=root)
            self.hkind = tk.StringVar(master=root, value="holes")
            self.hubInfo = dict(count=None, radius=None, spread=None)
            self.seconds = tk.StringVar(master=root, value="1")
            self.maxVar = tk.StringVar(master=root, value="5.0")
            self.ohx, self.ohy = tk.StringVar(master=root, value="0.0"), tk.StringVar(master=root, value="0.0")
            self.obx, self.oby = tk.StringVar(master=root, value="0.0"), tk.StringVar(master=root, value="0.0")
            self.save = tk.BooleanVar(master=root, value=False)
            self.status = tk.StringVar(master=root, value="Loading the models...")
            self.ready, self.loadError = False, ""
            self.running, self.job, self.files, self.index, self.run = False, None, [], 0, None
            self.pending = []                                  # results from the work thread, shown by poll()
            self.pairer = None
            self.lock = threading.Lock()

            bar = ttk.Frame(root); bar.pack(fill="x", padx=6, pady=3)
            ttk.Label(bar, text="Model").pack(side="left")
            ttk.Combobox(bar, textvariable=self.model, values=["DOST", "DOSTPLUS"], width=10,
                         state="readonly").pack(side="left", padx=(2, 10))
            self.hubButton = ttk.Button(bar, text="Hub picture...", command=self.chooseHub); self.hubButton.pack(side="left")
            ttk.Label(bar, text="  Hub centre x").pack(side="left")
            ttk.Entry(bar, textvariable=self.hx, width=7).pack(side="left")
            ttk.Label(bar, text="y").pack(side="left")
            ttk.Entry(bar, textvariable=self.hy, width=7).pack(side="left")
            ttk.Combobox(bar, textvariable=self.hkind, values=["holes", "bolts"], width=6,
                         state="readonly").pack(side="left", padx=(4, 10))
            ttk.Button(bar, text="Hub folder (pair by time)...", command=self.chooseHubFolder).pack(side="left", padx=(0, 10))
            ttk.Button(bar, text="Top bearing...", command=lambda: self.chooseBase("top")).pack(side="left")
            ttk.Button(bar, text="Nut / washer...", command=lambda: self.chooseBase("nut")).pack(side="left", padx=4)
            bar2 = ttk.Frame(root); bar2.pack(fill="x", padx=6, pady=3)
            ttk.Button(bar2, text="Caps folder...", command=self.chooseCaps).pack(side="left")
            self.startButton = ttk.Button(bar2, text="Start", command=self.start); self.startButton.pack(side="left", padx=4)
            self.stopButton = ttk.Button(bar2, text="Stop", command=self.stop, state="disabled"); self.stopButton.pack(side="left")

            def spin(label, var, lo, hi, inc, fmt, width=5):
                ttk.Label(bar2, text=label).pack(side="left", padx=(10, 0))
                ttk.Spinbox(bar2, from_=lo, to=hi, increment=inc, format=fmt, textvariable=var, width=width,
                            state="readonly").pack(side="left", padx=2)
            spin("Seconds between pictures", self.seconds, 1, 10, 1, "%.0f", 3)
            spin("Max distance (px)", self.maxVar, 0.5, 20, 0.5, "%.1f")
            ttk.Label(bar2, text="  offset holes dx,dy").pack(side="left")
            ttk.Entry(bar2, textvariable=self.ohx, width=5).pack(side="left")
            ttk.Entry(bar2, textvariable=self.ohy, width=5).pack(side="left")
            ttk.Label(bar2, text="  bolts dx,dy").pack(side="left")
            ttk.Entry(bar2, textvariable=self.obx, width=5).pack(side="left")
            ttk.Entry(bar2, textvariable=self.oby, width=5).pack(side="left")
            ttk.Checkbutton(bar2, text="Save marked pictures", variable=self.save).pack(side="left", padx=10)
            self.blank = ImageTk.PhotoImage(Image.new("RGB", (VIEW_W, VIEW_H)), master=root)
            self.canvas = ttk.Label(root, image=self.blank); self.canvas.pack(padx=6)
            self.listbox = tk.Listbox(root, height=7, font=("Consolas", 9)); self.listbox.pack(fill="x", padx=6)
            self.text = ScrolledText(root, height=12, font=("Consolas", 9)); self.text.pack(fill="both", expand=True, padx=6)
            ttk.Label(root, textvariable=self.status).pack(fill="x", padx=6, pady=3)
            threading.Thread(target=self._load, daemon=True).start()
            root.after(200, self.poll)

        # ---- work off the window's thread ----
        def _load(self):
            err = self.checks.warmUp()
            try:
                self.catcher.install()
                capClass = self.checks.loadCap()
                vals = (capClass.SEATING_HUB_DISTANCE_MAX,
                        {m: (capClass.hubOffset("holes", m), capClass.hubOffset("bolts", m)) for m in ("DOST", "DOSTPLUS")})
            except Exception as e:
                err, vals = err or f"{type(e).__name__}: {e}", None
            with self.lock:
                self.pending.append(("loaded", err, vals))

        def _hubWork(self, picture, model):
            try:
                result = runHubCheck(self.checks, picture, model)
            except Exception as e:
                result = dict(ok=False, marked=picture, hub=None, lines=[f"hub check error: {type(e).__name__}: {e}"],
                              seconds=0.0)
            with self.lock:
                self.pending.append(("hub", result))

        def _capWork(self, path, model, hub, bases, maxDistance, offsets):
            try:
                row, lines, marked = capPass(self.checks, self.catcher, path, model, hub, bases, maxDistance, offsets,
                                             self.pairer)
            except Exception as e:
                row, lines, marked = (dict(picture=path.name, model=model, verdict="ERROR", reason=str(e)),
                                      [f"{path.name}: error {type(e).__name__}: {e}"], None)
            with self.lock:
                self.pending.append(("cap", path, row, lines, marked))

        # ---- window ----
        def log(self, lines):
            for line in lines:
                self.text.insert("end", line + "\n")
                print(line, flush=True)
            self.text.see("end")
            if self.run is not None:
                self.run.write(lines)

        def show(self, picture):
            rgb = cv2.cvtColor(picture, cv2.COLOR_BGR2RGB); h, w = rgb.shape[:2]; s = min(VIEW_W / w, VIEW_H / h)
            self.photo = ImageTk.PhotoImage(Image.fromarray(rgb).resize((int(w * s), int(h * s))), master=self.root)
            self.canvas.configure(image=self.photo)

        def chooseHub(self):
            path = filedialog.askopenfilename(parent=self.root, initialdir=str(DATA / "hub_images"),
                                              filetypes=[("Pictures", "*.jpg *.jpeg *.png *.bmp")])
            if not path:
                return
            picture = readPicture(path)
            if picture is None:
                self.status.set(f"Could not read {path}"); return
            self.hubPath, self.hubPicture = path, picture
            self.show(picture)
            if not self.ready:
                self.status.set("Models still loading: the hub check runs when they are ready")
                with self.lock:
                    self.pending.append(("hubwait",))
                return
            self.startHub()

        def startHub(self):
            self.hubButton.configure(state="disabled")
            self.status.set(f"Hub check on {os.path.basename(self.hubPath)} as {self.model.get()}...")
            threading.Thread(target=self._hubWork, args=(self.hubPicture, self.model.get()), daemon=True).start()

        def chooseHubFolder(self):
            """Pair each cap with the hub picture of its own cycle (CHANGES 150); cancel = back to one hub picture."""
            folder = filedialog.askdirectory(parent=self.root, initialdir=str(DATA / "hub_images"))
            self.hubFolder = folder or None
            n = len(HubPairer(folder).hubs) if folder else 0
            self.status.set(f"Pairing by cycle with {folder}: {n} hub pictures with a time in the name" if folder
                            else "Pairing off: one hub picture for all caps")

        def chooseBase(self, kind):
            path = filedialog.askopenfilename(parent=self.root, filetypes=[("Pictures", "*.jpg *.jpeg *.png *.bmp")])
            if path:
                setattr(self, "topPath" if kind == "top" else "nutPath", path)
                self.status.set(f"{'Top bearing' if kind == 'top' else 'Nut / washer'} picture: {path}")

        def chooseCaps(self):
            folder = filedialog.askdirectory(parent=self.root, initialdir=str(DATA / "cap_images"))
            if folder:
                self.capsFolder = folder
                self.status.set(f"Caps folder {folder}: {len(picturesIn(folder))} pictures")

        def fillOffsets(self, *args):
            """The holes / bolts offsets of the chosen model from the properties (offsets are per model, CHANGES 147)."""
            pair = getattr(self, "configOffsets", {}).get(self.model.get())
            if pair:
                self.ohx.set(f"{float(pair[0][0]):g}"); self.ohy.set(f"{float(pair[0][1]):g}")
                self.obx.set(f"{float(pair[1][0]):g}"); self.oby.set(f"{float(pair[1][1]):g}")

        def hubCentre(self):
            try:
                return dict(cx=float(self.hx.get()), cy=float(self.hy.get()), kind=self.hkind.get(), **self.hubInfo)
            except ValueError:
                return None

        def start(self):
            if not self.ready:
                self.status.set("Models still loading"); return
            if not self.capsFolder:
                self.status.set("Choose a caps folder first"); return
            self.files, self.index = picturesIn(self.capsFolder), 0
            if not self.files:
                self.status.set("No pictures in the caps folder"); return
            self.run = Run(self.capsFolder)
            self.listbox.delete(0, "end")
            self.pairer = HubPairer(self.hubFolder) if self.hubFolder else None
            if self.pairer is not None:
                self.listbox.insert("end", f"pairing by cycle: {len(self.pairer.hubs)} hub pictures in {self.hubFolder}")
                self.log([f"=== pairing by cycle: hub folder {self.hubFolder} ({len(self.pairer.hubs)} pictures), hub "
                          f"picture at most {self.pairer.maxSecs:.0f} s before the cap picture"])
            hub = self.hubCentre()
            if self.pairer is None:
                self.listbox.insert("end", "hub centre " + (f"({hub['cx']:.1f}, {hub['cy']:.1f}) {hub['kind']}" if hub
                                                            else "none - the distance is not measured"))
            self.bases, note = basesFor(self.model.get(), self.hubPicture, self.topPath, self.nutPath)
            self.log([f"=== Machine {os.environ['COSTHETA_PLC_VENDOR']}, model {self.model.get()}, caps {self.capsFolder} "
                      f"({len(self.files)} pictures)",
                      ("each cap against the hub picture of its own cycle" if self.pairer is not None else
                       f"hub picture {self.hubPath or '(none)'}; hub centre "
                       + (f"({hub['cx']:.1f}, {hub['cy']:.1f}) {hub['kind']}" if hub else
                          "none - the distance is not measured")),
                      note])
            self.running = True
            self.startButton.configure(state="disabled"); self.stopButton.configure(state="normal")
            self.next()

        def settings(self):
            maxDistance = float(self.maxVar.get())
            offsets = ((float(self.ohx.get()), float(self.ohy.get())), (float(self.obx.get()), float(self.oby.get())))
            return maxDistance, offsets

        def next(self):
            self.job = None
            if not self.running:
                return
            if self.index >= len(self.files):
                self.finish(); return
            path = self.files[self.index]
            try:
                maxDistance, offsets = self.settings()
            except ValueError:
                self.status.set("Max distance / offsets must be numbers"); self.finish(True); return
            self.workStart = time.perf_counter()
            self.status.set(f"Picture {self.index + 1} of {len(self.files)}: {path.name} ...")
            threading.Thread(target=self._capWork, args=(path, self.model.get(), self.hubCentre(), self.bases,
                                                         maxDistance, offsets), daemon=True).start()

        def stop(self):
            self.running = False
            if self.job is not None:
                self.root.after_cancel(self.job); self.job = None
            self.finish(True)

        def finish(self, stopped=False):
            self.running = False
            self.startButton.configure(state="normal"); self.stopButton.configure(state="disabled")
            if self.run is not None and self.run.rows:
                maxDistance = float(self.maxVar.get())
                tail = summarise(self.run.rows, maxDistance) + [f"files: {self.run.txt}, {self.run.csv}"]
                self.log(tail)
                self.status.set(f"{'Stopped' if stopped else 'Done'}: {len(self.run.rows)} caps; results in {self.run.out}")
                self.run = None

        def poll(self):
            with self.lock:
                items, self.pending = self.pending, []
            for item in items:
                if item[0] == "loaded":
                    _, err, vals = item
                    self.ready, self.loadError = not err, err
                    if vals:
                        self.maxVar.set(f"{float(vals[0]):.1f}")
                        self.configOffsets = vals[1]
                        self.fillOffsets()
                    self.status.set("Models loaded: choose a hub picture and a caps folder" if not err
                                    else f"Models not loaded: {err}")
                    if self.ready and any(i[0] == "hubwait" for i in items):
                        self.startHub()
                elif item[0] == "hubwait":
                    if self.ready:
                        self.startHub()
                    else:
                        with self.lock:
                            self.pending.append(item)
                elif item[0] == "hub":
                    result = item[1]
                    self.hubButton.configure(state="normal")
                    self.show(result["marked"])
                    hub = result["hub"]
                    if hub:
                        self.hx.set(f"{hub['cx']:.1f}"); self.hy.set(f"{hub['cy']:.1f}"); self.hkind.set(hub["kind"])
                        self.hubInfo = dict(count=hub.get("count"), radius=hub.get("radius"), spread=hub.get("spread"))
                    self.listbox.insert("end", result["lines"][0] + ("" if not hub else
                                        f"; centre ({hub['cx']:.1f}, {hub['cy']:.1f}) from {hub['count']} {hub['kind']}"))
                    self.log([f"--- hub picture {self.hubPath}"] + result["lines"])
                    self.status.set(result["lines"][0] + ("" if hub else " - type a hub centre to go on"))
                elif item[0] == "cap":
                    _, path, row, lines, marked = item
                    if self.run is not None:
                        self.run.add(row)
                        self.log(lines)
                    d = row.get("distance")
                    self.listbox.insert("end", f"{path.name:30s} {row.get('verdict', ''):7s} {row.get('seconds', 0):5.2f} s"
                                        + (f"  d {d:5.2f} px (dx {row['dx']:+.1f}, dy {row['dy']:+.1f})" if d is not None else "")
                                        + ("  measured only" if row.get("measured_only") else ""))
                    self.listbox.see("end")
                    if marked is not None:
                        self.show(marked)
                        if self.save.get() and self.run is not None:
                            cv2.imwrite(str(self.run.out / path.name), marked)
                    self.index += 1
                    if self.running:
                        self.job = self.root.after(int(float(self.seconds.get()) * 1000), self.next)
            if self.running and self.job is None and self.index < len(self.files):
                self.status.set(f"Picture {self.index + 1} of {len(self.files)}: "
                                f"{self.files[self.index].name} - {time.perf_counter() - self.workStart:.1f} s ...")
            self.root.after(150, self.poll)

    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    sys.exit(runBatch() if "--batch" in sys.argv else runWindow())
