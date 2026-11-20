"""
DryRun_SplitPin_Check_UI - the real split pin and washer check (CheckSplitPinAndWasher, state 20; CHANGES 162) on
pictures, with this machine's settings, one picture at a time with the check's own marked picture.

  1. Starts with this folder's splitpin_images/ (sub-folders OK/ and NOT_OK/: the expected verdict comes from the
     sub-folder). "Folder..." takes another folder (its OK/ and NOT_OK/ sub-folders if it has them, else the pictures
     in it, with no expected verdict); "Picture..." one picture. Then Start.
  2. "Seconds between pictures" (0.5 - 10, starts at 1) can be changed at any time. "Only mismatches" skips the
     pictures whose verdict equals the expected one (they are still counted). Stop ends the run.
  3. The picture shown is exactly the one the check gives the frontend: outlines only - the washer outline (green OK /
     red NOT OK) and, when a leg ends outside the washer, the outline of that blue (red). Verdict, expected verdict,
     check time, reason and every value go to the list at the bottom and to the terminal. "Zoom on the nut" (on at
     start) shows the region round vision.splitpin.centre enlarged; off, the whole picture.
  4. At the end: OK / NOT OK counts and, with expected verdicts, how many matched. "Save marked pictures" writes the
     pictures to <folder>/splitpin_check_results/.

  --auto [folder]   no window: checks every picture, prints one line each and the counts; exit code 0 when every
                    picture with an expected verdict matched, except the pictures named in KNOWN_DISAGREEMENTS (none
                    since CHANGES 163); 1 otherwise.

Settings: <vendor>.vision.splitpin.* in ApplicationConfiguration.properties (vendor from the folder: machine1 =
allenbradley, machine2 = siemens). The hub's holes (where the washer is looked for) by YOLO + MobileSAM since
CHANGES 164: the models (models/MobileSAM with the YOLO weights) as in production; --holes morphology = the dark-blob
finder with its own offsets (no models needed). No Redis, PLC or camera needed. Without this cycle's hub check, the
hole finder first runs over the hub check's own annulus (slower than in production).

Run from the project root:   python test/machine2_dryrun/DryRun_SplitPin_Check_UI.py   [--auto [folder]] [--model DOST|DOSTPLUS] [--holes morphology]
                                                                    [--hub-image <hub picture>]
"""
import os
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["COSTHETA_PLC_VENDOR"] = "siemens" if HERE.name.startswith("machine2") else "allenbradley"

import cv2

import camera.CheckSplitPinAndWasher as CheckModule
from camera.CheckSplitPinAndWasher import CheckSplitPinAndWasher

EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp")
VIEW_W, VIEW_H = 1120, 630
ZOOM_HALF = 180                 # "Zoom on the nut": CENTRE +- this many px
QRS = {"DOST": "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$",
       "DOSTPLUS": "8201206$400112VA1C$22881$15.10.2026$C5211702$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$"}
# --model DOST|DOSTPLUS (CHANGES 177; "Model" in the window): production takes the model from the QR code; the hole
# count (4 / 5) and the offset depend on it, so a DOSTPLUS picture checked as DOST looks for 4 holes and fails
MODEL = {"value": (sys.argv[sys.argv.index("--model") + 1].upper()
                   if "--model" in sys.argv and sys.argv.index("--model") + 1 < len(sys.argv) else "DOST")}
if MODEL["value"] not in QRS:
    sys.exit(f"--model must be DOST or DOSTPLUS, not {MODEL['value']}")


def currentModel() -> str:
    return MODEL["value"]


def currentQR() -> str:
    return QRS[currentModel()]
# the pictures live in the Machine 2 folder; the Machine 1 copy uses them too
IMAGES = HERE / "splitpin_images" if (HERE / "splitpin_images").is_dir() else ROOT / "test" / "machine2_dryrun" / "splitpin_images"
# pictures whose expected verdict cannot be met for a known reason (file name -> reason); none since CHANGES 163
KNOWN_DISAGREEMENTS = {}

_messages = []
CheckModule.logBoth = lambda level, source, message, *args, **kwargs: _messages.append((level, message))


def picturesIn(folder: pathlib.Path):
    """[(path, expected 'OK' / 'NOT OK' / '')] - from OK/ and NOT_OK/ sub-folders if there are any."""
    found = []
    for sub, expected in (("OK", "OK"), ("NOT_OK", "NOT OK")):
        if (folder / sub).is_dir():
            found += [(p, expected) for p in sorted((folder / sub).iterdir()) if p.suffix.lower() in EXTENSIONS]
    if not found:
        found = [(p, "") for p in sorted(folder.iterdir()) if p.suffix.lower() in EXTENSIONS]
    return found


_recorder = None  # HubHolesRecorder, installed in __main__: the hub's holes ringed on the picture, HUBHOLES line


def runCheck(path: pathlib.Path):
    """(verdict, marked picture or None, seconds, reason, values) of the real check on one file."""
    image = cv2.imread(str(path))
    if image is None:
        return "UNREADABLE", None, 0.0, "the file could not be read as a picture", ""
    image = cv2.resize(image, (1280, 720))
    if _recorder is not None:
        _recorder.clear()
    started = time.perf_counter()
    result = CheckSplitPinAndWasher.measure(image, currentModel())
    marked = CheckSplitPinAndWasher.markedPicture(image, result)
    elapsed = time.perf_counter() - started
    holes = _recorder.take() if _recorder is not None else []
    if holes:
        marked = _recorder.draw(marked, holes)
    # the production entry point too (its log line), on the same picture
    _messages.clear()
    _, ok = CheckSplitPinAndWasher.checkSplitPinAndWasher(image.copy(), {}, currentQR())
    assert bool(ok) == bool(result["ok"]), "checkSplitPinAndWasher and measure disagree"
    values = CheckSplitPinAndWasher.summary(result)
    if holes:
        values += f"\n      HUBHOLES {_recorder.text(holes)}"
    return ("OK" if result["ok"] else "NOT OK"), marked, elapsed, result["reason"], values


def line(path, expected, verdict, elapsed, reason):
    mark = "" if not expected else ("  " if expected == verdict else " X")
    return (f"{mark} {path.name:44s} {verdict:7s} expected {expected or '-':7s} {elapsed * 1000:6.1f} ms"
            + (f"   {reason}" if reason else ""))


def skipSwitch(items) -> list:
    """skip.splitpin.and.washer.check (CHANGES 173): True -> checkSplitPinAndWasher returns OK with the picture as taken,
    whatever the picture (here a NOT OK one); False -> the check's own result. The switch is read through getTuning at
    every check; set here by standing in for getTuning."""
    from Configuration import CosThetaConfigurator as C
    bad = [p for p, e in items if e == "NOT OK"][:1] or [p for p, _ in items][:1]
    if not bad:
        return []
    image = cv2.resize(cv2.imread(str(bad[0])), (1280, 720))
    real = C.getTuning
    out = []
    try:
        for value in (True, False):
            C.getTuning = classmethod(lambda cls, key, default, minimum=None, _v=value: _v
                                      if key == "skip.splitpin.and.washer.check" else real(key, default, minimum))
            picture, ok = CheckSplitPinAndWasher.checkSplitPinAndWasher(image.copy(), {}, currentQR())
            expected = True if value else bool(CheckSplitPinAndWasher.measure(image, currentModel())["ok"])
            same = picture is not None and (not value or (picture.shape == image.shape and (picture == image).all()))
            print(f"  skip.splitpin.and.washer.check={value}: {bad[0].name} -> {'OK' if ok else 'NOT OK'}"
                  + (" (picture as taken)" if value and same else ""), flush=True)
            if bool(ok) != expected or not same:
                out.append((bad[0].name, f"switch {value}", "OK" if ok else "NOT OK"))
    finally:
        C.getTuning = real
    return out


def auto(folder: pathlib.Path) -> int:
    items = picturesIn(folder)
    print(f"Model: {currentModel()} (--model)", flush=True)
    counts, mismatches, known = {}, [], []
    for path, expected in items:
        verdict, _, elapsed, reason, values = runCheck(path)
        counts[verdict] = counts.get(verdict, 0) + 1
        print(line(path, expected, verdict, elapsed, reason) + f"\n      {values}", flush=True)
        if expected and verdict != expected:
            (known if path.name in KNOWN_DISAGREEMENTS else mismatches).append((path.name, expected, verdict))
    labelled = sum(1 for _, e in items if e)
    print(f"\n{len(items)} pictures: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    for sub, expected in (("OK", "OK"), ("NOT_OK", "NOT OK")):
        n = sum(1 for _, e in items if e == expected)
        if n:
            right = n - sum(1 for _, e, _ in mismatches + known if e == expected)
            print(f"  expected {expected:6s}: {right} of {n} right")
    for name, e, v in known:
        print(f"  known disagreement: {name} labelled {e}, checked {v} ({KNOWN_DISAGREEMENTS[name]})")
    for name, e, v in mismatches:
        print(f"  MISMATCH: {name} expected {e}, checked {v}")
    mismatches += skipSwitch(items)
    print("RESULT:", "PASS" if not mismatches else f"FAIL ({len(mismatches)} mismatch(es))", f"({labelled} labelled)")
    return 0 if not mismatches else 1


def window():
    import tkinter as tk
    from tkinter import filedialog, ttk
    from PIL import Image, ImageTk

    class SplitPinWindow:
        def __init__(self, root):
            self.root = root
            self.items, self.index, self.running, self.timer, self.photo = [], 0, False, None, None
            self.results, self.outFolder = [], None
            root.title(f"Split pin and washer check - {os.environ['COSTHETA_PLC_VENDOR']} settings")
            bar = ttk.Frame(root); bar.pack(fill="x", padx=6, pady=4)
            ttk.Button(bar, text="Folder...", command=self.chooseFolder).pack(side="left")
            ttk.Button(bar, text="Picture...", command=self.choosePicture).pack(side="left", padx=4)
            ttk.Label(bar, text="Model").pack(side="left", padx=(8, 2))
            self.model = tk.StringVar(master=root, value=currentModel())
            ttk.Combobox(bar, textvariable=self.model, values=tuple(QRS), state="readonly", width=10).pack(side="left")
            self.model.trace_add("write", lambda *_: MODEL.update(value=self.model.get()))
            from _hub_holes_option import addHubPictureControl
            addHubPictureControl(bar, root, report=lambda line: self.status.set(line))
            self.source = tk.StringVar(master=root, value="")
            ttk.Label(bar, textvariable=self.source, width=44).pack(side="left", padx=6)
            ttk.Label(bar, text="Seconds between pictures").pack(side="left", padx=(12, 2))
            self.seconds = tk.StringVar(master=root, value="1")
            ttk.Combobox(bar, textvariable=self.seconds, values=["0.5"] + [str(i) for i in range(1, 11)],
                         state="readonly", width=4).pack(side="left")
            self.onlyMismatches = tk.BooleanVar(master=root, value=False)
            ttk.Checkbutton(bar, text="Only mismatches", variable=self.onlyMismatches).pack(side="left", padx=8)
            self.zoom = tk.BooleanVar(master=root, value=True)
            ttk.Checkbutton(bar, text="Zoom on the nut", variable=self.zoom, command=self.redraw).pack(side="left", padx=4)
            self.save = tk.BooleanVar(master=root, value=False)
            ttk.Checkbutton(bar, text="Save marked pictures", variable=self.save).pack(side="left", padx=4)
            self.startButton = ttk.Button(bar, text="Start", command=self.start); self.startButton.pack(side="left")
            self.stopButton = ttk.Button(bar, text="Stop", command=self.stop, state="disabled")
            self.stopButton.pack(side="left", padx=4)
            C = CheckSplitPinAndWasher
            ttk.Label(root, text=(f"washer centre: {C.CENTRE_SOURCE} (hub holes + offset DOST {tuple(C.HUB_OFFSET_DOST)} / DOSTPLUS {tuple(C.HUB_OFFSET_DOSTPLUS)}; fixed {tuple(C.CENTRE)}), washer tip radius {C.WASHER_TIP_RADIUS_MIN} - "
                                  f"{C.WASHER_TIP_RADIUS_MAX} px, cover >= {C.WASHER_COVER_MIN}, blue >= "
                                  f"{C.MARKER_PIXELS_MIN} px, at most {C.LEG_OUTSIDE_PIXELS_MAX} blue px more than "
                                  f"{C.LEG_OUTSIDE_DISTANCE} px outside the washer")).pack(fill="x", padx=6)
            self.canvas = tk.Label(root, background="black", width=VIEW_W, height=VIEW_H); self.canvas.pack(padx=6)
            self.status = tk.StringVar(master=root, value="Start checks the pictures listed above.")
            ttk.Label(root, textvariable=self.status).pack(fill="x", padx=6)
            frame = ttk.Frame(root); frame.pack(fill="both", expand=True, padx=6, pady=4)
            self.listbox = tk.Listbox(frame, height=10, font=("Consolas", 10))
            scroll = ttk.Scrollbar(frame, command=self.listbox.yview)
            xscroll = ttk.Scrollbar(frame, orient="horizontal", command=self.listbox.xview)
            self.listbox.configure(yscrollcommand=scroll.set, xscrollcommand=xscroll.set)
            xscroll.pack(side="bottom", fill="x"); scroll.pack(side="right", fill="y")
            self.listbox.pack(side="left", fill="both", expand=True)
            root.protocol("WM_DELETE_WINDOW", self.close)
            if IMAGES.is_dir():
                self.useFolder(IMAGES)

        def useFolder(self, folder: pathlib.Path):
            self.items = picturesIn(folder); self.outFolder = folder / "splitpin_check_results"
            labelled = sum(1 for _, e in self.items if e)
            self.source.set(f"{folder}  ({len(self.items)} pictures, {labelled} with an expected verdict)")

        def chooseFolder(self):
            folder = filedialog.askdirectory(title="Folder with split pin pictures", parent=self.root)
            if folder:
                self.useFolder(pathlib.Path(folder))

        def choosePicture(self):
            f = filedialog.askopenfilename(title="Split pin picture", parent=self.root,
                                           filetypes=[("Pictures", " ".join("*" + e for e in EXTENSIONS))])
            if f:
                p = pathlib.Path(f)
                expected = {"OK": "OK", "NOT_OK": "NOT OK"}.get(p.parent.name, "")
                self.items = [(p, expected)]; self.outFolder = p.parent / "splitpin_check_results"; self.source.set(f)

        def start(self):
            if not self.items:
                self.status.set("Choose a folder or a picture first."); return
            self.index, self.results, self.running = 0, [], True
            self.listbox.delete(0, "end")
            self.startButton.configure(state="disabled"); self.stopButton.configure(state="normal")
            self.step()

        def stop(self):
            self.running = False
            if self.timer is not None:
                self.root.after_cancel(self.timer); self.timer = None
            self.finish(stopped=True)

        def close(self):
            self.running = False; self.root.destroy()

        def step(self):
            self.timer = None
            if not self.running:
                return
            while self.index < len(self.items):
                path, expected = self.items[self.index]; self.index += 1
                verdict, marked, elapsed, reason, values = runCheck(path)
                self.results.append((path.name, expected, verdict))
                text = line(path, expected, verdict, elapsed, reason)
                print(text + f"\n      {values}", flush=True)
                if self.onlyMismatches.get() and (not expected or expected == verdict):
                    continue
                self.listbox.insert("end", text); self.listbox.insert("end", f"      {values}"); self.listbox.see("end")
                if marked is not None:
                    self.show(marked)
                    if self.save.get():
                        self.outFolder.mkdir(exist_ok=True); cv2.imwrite(str(self.outFolder / path.name), marked)
                self.status.set(f"Picture {self.index} of {len(self.items)}: {path.name} - {verdict}"
                                + (f" (expected {expected})" if expected else "") + f"  {elapsed * 1000:.1f} ms")
                self.timer = self.root.after(int(float(self.seconds.get()) * 1000), self.step)
                return
            self.finish()

        def redraw(self):
            if getattr(self, "lastMarked", None) is not None:
                self.show(self.lastMarked)

        def show(self, marked):
            self.lastMarked = marked
            if self.zoom.get():                          # the nut region, CENTRE +- ZOOM_HALF, enlarged
                cx, cy = (int(v) for v in CheckSplitPinAndWasher.CENTRE)
                y0, x0 = max(0, cy - ZOOM_HALF), max(0, cx - ZOOM_HALF)
                marked = marked[y0:cy + ZOOM_HALF, x0:cx + ZOOM_HALF]
            rgb = cv2.cvtColor(marked, cv2.COLOR_BGR2RGB); h, w = rgb.shape[:2]; s = min(VIEW_W / w, VIEW_H / h)
            picture = Image.fromarray(rgb).resize((int(w * s), int(h * s)), Image.LANCZOS)
            self.photo = ImageTk.PhotoImage(picture, master=self.root); self.canvas.configure(image=self.photo)

        def finish(self, stopped=False):
            self.running = False
            self.startButton.configure(state="normal"); self.stopButton.configure(state="disabled")
            counts = {}
            for _, _, v in self.results:
                counts[v] = counts.get(v, 0) + 1
            labelled = [(n, e, v) for n, e, v in self.results if e]
            right = sum(1 for _, e, v in labelled if e == v)
            text = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "nothing checked"
            if labelled:
                text += f"; {right} of {len(labelled)} as expected"
            self.status.set(f"{'Stopped' if stopped else 'Done'} after {len(self.results)} of {len(self.items)} "
                            f"pictures - {text}")
            if labelled:
                self.listbox.insert("end", f"{right} of {len(labelled)} as expected"
                                    + "".join(f"; X {n}" for n, e, v in labelled if e != v))
                self.listbox.see("end")

    root = tk.Tk()
    SplitPinWindow(root)
    root.mainloop()


if __name__ == "__main__":
    from _hub_holes_option import HubHolesRecorder, applyHolesOption
    applyHolesOption()
    _recorder = HubHolesRecorder()
    if "--auto" in sys.argv:
        i = sys.argv.index("--auto")
        folder = pathlib.Path(sys.argv[i + 1]) if len(sys.argv) > i + 1 and not sys.argv[i + 1].startswith("--") else IMAGES
        sys.exit(auto(folder))
    window()
