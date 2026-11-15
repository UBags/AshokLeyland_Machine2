"""
DryRun_All_Checks_UI - one component through every vision check, as the camera server runs them: one picture per
check, loaded with a button per check (Load Knuckle Picture, Load Hub Picture, ...), then Run All.

What it does (the production code, this machine's settings):
  1. The component type (DOST / DOSTPLUS, LHS / RHS) gives the QR code of the cycle.
  2. Run All goes through the checks in the machine's order - knuckle, hub, top bearing, nut and washer, bung for the
     component press, no bung, split pin, cap, bung for the cap press - each through the camera server's
     assessPictures() with the cycle's state kept in ImageProcessor, exactly as in a cycle:
       - the knuckle check (Machine 2: by segmentation) builds the cycle's frame; every later check's centre is then
         placed from it (camera/VisionFrame.py) - the window lists the camera shift / rotation / zoom and the centre
         each check used;
       - the top bearing ring is kept for the nut / washer and bung checks; the hub, top bearing and nut pictures for
         the cap check;
       - a check with no picture loaded is skipped (a later check that needs its picture then says so).
     "Cap and split pin as in production": on = as the camera server runs them on its three pictures (here the loaded
     picture three times) - cap: steps 1-3 on each, then the whole check with the seating step once; split pin: the
     2-of-3 vote; off = one check of the picture.
  3. Each check's line: OK / NOT OK and its time. Clicking a line shows the check's own picture (outlines only, no
     values: exactly what the frontend gets) and, below, every log line the check wrote (values, reasons). The same
     lines go to the terminal.
  4. "Fill from dry-run folders" loads, for the chosen type, the first picture of each check that has one in this
     folder's picture sets (the no-bung check gets a nut picture; the bung pictures are not labelled by model: load
     them by hand). "Save results" writes each check's picture and a results.txt to a folder you choose.

Needs the model weights (models/MobileSAM: MobileSAM and YOLO) as in production; no Redis, PLC or camera (the log lines
are collected by this window, not sent to the logging server).

Run from the project root:      python test/machine2_dryrun/DryRun_All_Checks_UI.py
Without a window (the same run, results printed; exit 1 if a loaded check is NOT OK):
    python test/machine2_dryrun/DryRun_All_Checks_UI.py --auto --type DOST_LHS [--single] [--<station> <picture> ...]
    stations: --knuckle --hub --top --nut --bung1 --nobung --splitpin --cap --bung2 (default: the dry-run folders)
"""
import argparse
import os
import pathlib
import queue
import sys
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["COSTHETA_PLC_VENDOR"] = "siemens" if HERE.name.startswith("machine2") else "allenbradley"

# Every log line of the checks is collected here (and printed), not sent to the logging server: patched before any
# camera module is imported, so that their `from logutils.SlaveLoggers import logBoth` takes this one.
import logutils.SlaveLoggers as _SL

_LOG_LOCK = threading.Lock()
_LOG = []                                   # (station key or None, level, text)
_CURRENT = {"station": None}


def _collect(level, source, message, *args, **kwargs):
    with _LOG_LOCK:
        _LOG.append((_CURRENT["station"], str(level), str(message)))
    if str(level) not in ("logDebug",):
        print(f"  [{_CURRENT['station'] or '-'}] {message}", flush=True)


_SL.logBoth = _collect

import cv2                                   # noqa: E402

import camera.CameraProcessorServer as cps   # noqa: E402
from camera import VisionFrame               # noqa: E402
from statemachine.StateMachine import MachineState as MS   # noqa: E402

QR = {("DOST", "LHS"): "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$",
      ("DOST", "RHS"): "7204838$400102VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNURH$SNPL-MAT$",
      ("DOSTPLUS", "LHS"): "8201206$400112VA1C$22881$15.10.2026$C5211702$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$",
      ("DOSTPLUS", "RHS"): "8201206$400102VA1C$22881$15.10.2026$C5211702$MEK$40cR4-B$1$SPDL ASSY-KNURH$SNPL-MAT$"}

# key, button text, state, frame centres it uses, default picture (relative to this folder; {T} type, {S} side)
STATIONS = [
    ("knuckle", "Knuckle", MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE, (), "knuckle_images/{T}_{S}_01.jpg"),
    ("hub", "Hub", MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING, ("hub.segment.center",), "hub_images/{T}_01.jpg"),
    ("top", "Top Bearing", MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING, ("topbearing.annular.center",),
     "top_bearing_images/{T}_bearing_01.jpg"),
    ("nut", "Nut and Washer", MS.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER, ("nutwasher.nut.detection.center",),
     "nut_washer_images/{T}_nut_01.jpg"),
    ("bung1", "Bung (Component Press)", MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS,
     ("bunk.outer.disc.center",), None),
    ("nobung", "No Bung", MS.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK, ("nobunk.nut.detection.center",),
     "nut_washer_images/{T}_nut_01.jpg"),
    ("splitpin", "Split Pin and Washer", MS.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER, (), None),
    ("cap", "Cap", MS.READ_TAKE_PICTURE_FOR_CHECKING_CAP, ("cap.center",), "cap_images/{T}_cap_01.jpg"),
    ("bung2", "Bung (Cap Press)", MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS, ("bunk.outer.disc.center",), None),
]
VOTE_STATES = (MS.READ_TAKE_PICTURE_FOR_CHECKING_CAP, MS.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER)


def readPicture(path):
    img = cv2.imread(str(path))
    if img is None:
        raise ValueError(f"cannot read {path}")
    return cv2.resize(img, (1280, 720))      # as the camera server (every state's target size)


def defaultPictures(modelType, side):
    out = {}
    for key, _, _, _, pattern in STATIONS:
        if pattern:
            path = HERE / pattern.format(T=modelType, S=side)
            if path.is_file():
                out[key] = str(path)
    return out


def _singleCheck(state, img):
    """Cap / split pin without the vote: the check once on the picture, with the cycle's state (as one vote thread)."""
    IP = cps.ImageProcessor
    if state == MS.READ_TAKE_PICTURE_FOR_CHECKING_CAP:
        IP.currentPictures[cps.capPictureKeyString] = img
        processed, ok = cps.CheckCap.checkCap(img, IP.currentPictures, IP.currentComponentQRCodeValue)
    else:
        IP.currentPictures[cps.splitPinAndWasherPictureKeyString] = img
        processed, ok = cps.CheckSplitPinAndWasher.checkSplitPinAndWasher(
            anImage=img, currentPictures=IP.currentPictures, componentQRCode=IP.currentComponentQRCodeValue)
    return bool(ok), processed, img


def runChecks(pictures, modelType, side, vote=True, onResult=None, stopFlag=None):
    """Every loaded picture through its check, in the machine's order, as in one cycle. pictures: station key -> path
    or BGR array. Returns a list of dicts (key, name, loaded, ok, secs, processed, centre, lines, frame)."""
    IP = cps.ImageProcessor
    IP.stateFile = None                       # nothing written to the camera server's state file
    IP.fetchQRCodeInChecks = False
    IP.endOfCycle()
    IP.setCurrentComponentName(qrCodeValue=QR[(modelType, side)])
    results = []
    for key, name, state, centreNames, _ in STATIONS:
        if stopFlag is not None and stopFlag.is_set():
            break
        source = pictures.get(key)
        result = dict(key=key, name=name, loaded=source is not None, ok=None, secs=0.0, processed=None, centre="",
                      lines=[], frame=None)
        if source is None:
            results.append(result)
            if onResult:
                onResult(result)
            continue
        img = readPicture(source) if isinstance(source, (str, pathlib.Path)) else source
        _CURRENT["station"] = key
        with _LOG_LOCK:
            start = len(_LOG)
        written = VisionFrame.applyToChecks(IP.visionFrame)
        if centreNames:
            result["centre"] = ", ".join(f"{n} {written.get(n, '?')}" for n in centreNames)
        t0 = time.monotonic()
        try:
            if state in VOTE_STATES and not vote:
                ok, processed, _ = _singleCheck(state, img)
            else:
                ok, processed, _ = cps.assessPictures(state, [img] * 6)
        except Exception as e:
            ok, processed = False, img
            _collect("logError", "DryRun_All_Checks_UI", f"{name}: the check raised {type(e).__name__}: {e}")
        result["secs"] = time.monotonic() - t0
        result["ok"], result["processed"] = bool(ok), processed if processed is not None else img
        _CURRENT["station"] = None
        with _LOG_LOCK:
            result["lines"] = [f"{level}: {text}" for st, level, text in _LOG[start:] if st == key]
        if key == "knuckle":
            result["frame"] = IP.visionFrame
            result["lines"].insert(0, "Frame: " + VisionFrame.describe(IP.visionFrame))
        if result["centre"]:
            result["lines"].insert(0, "Centre used: " + result["centre"])
        results.append(result)
        if onResult:
            onResult(result)
    return results


def summaryLine(r):
    if not r["loaded"]:
        return f"{r['name']:<24} -  (no picture)"
    return f"{r['name']:<24} {'OK    ' if r['ok'] else 'NOT OK'}  {r['secs']:6.2f} s" + (f"   {r['centre']}" if r["centre"] else "")


# --------------------------------------------------------------------------------------------------------- window
def runWindow():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from PIL import Image, ImageTk

    VIEW_W, VIEW_H = 960, 540
    root = tk.Tk()
    root.title(f"All vision checks - {os.environ['COSTHETA_PLC_VENDOR']} settings")
    paths = {}
    rows = {}
    results = {}
    events = queue.Queue()
    stopFlag = threading.Event()
    state = {"running": False, "photo": None}

    top = ttk.Frame(root); top.pack(fill="x", padx=8, pady=6)
    ttk.Label(top, text="Component:").pack(side="left")
    typeVar = tk.StringVar(value="DOST LHS")
    ttk.Combobox(top, textvariable=typeVar, width=14, state="readonly",
                 values=["DOST LHS", "DOST RHS", "DOSTPLUS LHS", "DOSTPLUS RHS"]).pack(side="left", padx=4)
    voteVar = tk.BooleanVar(value=True)
    ttk.Checkbutton(top, text="Cap and split pin as in production (three pictures)", variable=voteVar).pack(side="left", padx=10)
    status = tk.StringVar(value="Load the pictures, then Run All")

    body = ttk.Frame(root); body.pack(fill="both", expand=True, padx=8)
    left = ttk.Frame(body); left.pack(side="left", fill="y")
    right = ttk.Frame(body); right.pack(side="left", fill="both", expand=True, padx=8)

    def load(key, name):
        f = filedialog.askopenfilename(title=f"{name} picture", initialdir=str(HERE),
                                       filetypes=[("Pictures", "*.jpg *.jpeg *.png *.bmp"), ("All", "*.*")])
        if f:
            paths[key] = f
            rows[key]["file"].set(pathlib.Path(f).name)
            rows[key]["verdict"].set("")

    def clear(key):
        paths.pop(key, None)
        rows[key]["file"].set("(none)")
        rows[key]["verdict"].set("")

    for i, (key, name, _, _, _) in enumerate(STATIONS):
        fileVar, verdictVar = tk.StringVar(value="(none)"), tk.StringVar(value="")
        ttk.Button(left, text=f"Load {name} Picture", width=34, command=lambda k=key, n=name: load(k, n)).grid(row=i, column=0, sticky="w", pady=2)
        ttk.Label(left, textvariable=fileVar, width=26).grid(row=i, column=1, sticky="w")
        ttk.Button(left, text="x", width=2, command=lambda k=key: clear(k)).grid(row=i, column=2)
        verdictLabel = ttk.Label(left, textvariable=verdictVar, width=20)
        verdictLabel.grid(row=i, column=3, sticky="w")
        ttk.Button(left, text="Show", width=6, command=lambda k=key: show(k)).grid(row=i, column=4, padx=2)
        rows[key] = dict(file=fileVar, verdict=verdictVar, label=verdictLabel)

    canvas = tk.Label(right, background="#202020", width=VIEW_W, height=VIEW_H)
    canvas.pack(fill="both", expand=False)
    textFrame = ttk.Frame(right); textFrame.pack(fill="both", expand=True, pady=4)
    text = tk.Text(textFrame, height=14, wrap="none", font=("Consolas", 9))
    ys = ttk.Scrollbar(textFrame, orient="vertical", command=text.yview)
    xs = ttk.Scrollbar(textFrame, orient="horizontal", command=text.xview)
    text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
    ys.pack(side="right", fill="y"); xs.pack(side="bottom", fill="x"); text.pack(side="left", fill="both", expand=True)

    def setText(lines):
        text.delete("1.0", "end"); text.insert("end", "\n".join(lines))

    def showImage(img):
        if img is None:
            return
        h, w = img.shape[:2]
        scale = min(VIEW_W / w, VIEW_H / h)
        rgb = cv2.cvtColor(cv2.resize(img, (int(w * scale), int(h * scale))), cv2.COLOR_BGR2RGB)
        state["photo"] = ImageTk.PhotoImage(Image.fromarray(rgb))
        canvas.configure(image=state["photo"])

    def show(key):
        r = results.get(key)
        if r is not None and r["loaded"]:
            showImage(r["processed"])
            setText([summaryLine(r), ""] + r["lines"])
        elif key in paths:
            showImage(readPicture(paths[key]))
            setText([f"{key}: {paths[key]} (not run yet)"])

    def fill():
        t, s = typeVar.get().split()
        for key, path in defaultPictures(t, s).items():
            paths[key] = path
            rows[key]["file"].set(pathlib.Path(path).name)
            rows[key]["verdict"].set("")
        status.set(f"Pictures from the dry-run folders for {t} {s} (bung pictures: load by hand)")

    def runAll():
        if state["running"]:
            return
        t, s = typeVar.get().split()
        results.clear()
        for k in rows:
            rows[k]["verdict"].set("..." if k in paths else "")
        stopFlag.clear(); state["running"] = True
        status.set(f"Running {t} {s} ... (the first check loads the models)")
        snapshot, vote = dict(paths), voteVar.get()

        def work():
            try:
                runChecks(snapshot, t, s, vote=vote, onResult=lambda r: events.put(("result", r)), stopFlag=stopFlag)
            except Exception as e:
                events.put(("error", f"{type(e).__name__}: {e}"))
            events.put(("done", None))
        threading.Thread(target=work, name="all checks", daemon=True).start()

    def poll():
        try:
            while True:
                kind, payload = events.get_nowait()
                if kind == "result":
                    r = payload; results[r["key"]] = r
                    if r["loaded"]:
                        rows[r["key"]]["verdict"].set(f"{'OK' if r['ok'] else 'NOT OK'}  {r['secs']:.2f} s")
                        rows[r["key"]]["label"].configure(foreground="#007000" if r["ok"] else "#c00000")
                        show(r["key"])
                    print(summaryLine(r), flush=True)
                elif kind == "error":
                    messagebox.showerror("All checks", payload)
                elif kind == "done":
                    state["running"] = False
                    loaded = [r for r in results.values() if r["loaded"]]
                    status.set(f"Done: {sum(1 for r in loaded if r['ok'])} of {len(loaded)} checks OK - click Show for a check")
                    setText([summaryLine(r) for r in sorted(results.values(), key=lambda r: [s[0] for s in STATIONS].index(r["key"]))])
        except queue.Empty:
            pass
        root.after(100, poll)

    def save():
        if not results:
            return
        folder = filedialog.askdirectory(title="Folder for the results", initialdir=str(HERE))
        if not folder:
            return
        lines = []
        for key, *_ in STATIONS:
            r = results.get(key)
            if r is None:
                continue
            lines += [summaryLine(r)] + [f"    {x}" for x in r["lines"]]
            if r["loaded"] and r["processed"] is not None:
                cv2.imwrite(os.path.join(folder, f"{key}.png"), r["processed"])
        pathlib.Path(folder, "results.txt").write_text("\n".join(lines), encoding="utf-8")
        status.set(f"Saved to {folder}")

    buttons = ttk.Frame(root); buttons.pack(fill="x", padx=8, pady=6)
    ttk.Button(buttons, text="Fill from dry-run folders", command=fill).pack(side="left")
    ttk.Button(buttons, text="Run All", command=runAll).pack(side="left", padx=6)
    ttk.Button(buttons, text="Stop", command=stopFlag.set).pack(side="left")
    ttk.Button(buttons, text="Save results", command=save).pack(side="left", padx=6)
    ttk.Label(buttons, textvariable=status).pack(side="left", padx=10)
    root._dryrun = dict(paths=paths, results=results, rows=rows, typeVar=typeVar, voteVar=voteVar, fill=fill,
                        runAll=runAll, status=status, state=state, show=show, text=text)   # for the automatic test
    poll()
    return root


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", action="store_true", help="no window: run once and print the results")
    ap.add_argument("--type", default="DOST_LHS", choices=[f"{t}_{s}" for t, s in QR])
    ap.add_argument("--single", action="store_true", help="cap and split pin: one check, not as the camera server runs them")
    for key, *_ in STATIONS:
        ap.add_argument(f"--{key}", default=None)
    args = ap.parse_args()
    if not args.auto:
        runWindow().mainloop()
        return
    t, s = args.type.split("_")
    pictures = defaultPictures(t, s)
    pictures.update({key: getattr(args, key) for key, *_ in STATIONS if getattr(args, key)})
    results = runChecks(pictures, t, s, vote=not args.single)
    print("\n" + "\n".join(summaryLine(r) for r in results))
    frame = next((r["frame"] for r in results if r["key"] == "knuckle"), None)
    print("\nFrame: " + VisionFrame.describe(frame))
    failed = [r["name"] for r in results if r["loaded"] and not r["ok"]]
    print(f"\nRESULT {sum(1 for r in results if r['loaded'] and r['ok'])}/{sum(1 for r in results if r['loaded'])} checks OK"
          + (f"; NOT OK: {failed}" if failed else ""), flush=True)
    os._exit(1 if failed else 0)


if __name__ == "__main__":
    main()
