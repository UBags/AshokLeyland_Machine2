"""
DryRun_Cap_Check_UI - the real cap check (CheckCap, state 23) on a folder of pictures or one picture, with this machine's
settings, shown one picture at a time with the check's own marked picture: cap OK, or NOT OK and why (no cap, wrong
cap, comparisons failed, cap not seated).

  1. "Folder..." (every jpg / jpeg / png / bmp in it, by name) or "Picture...", then Start.
  2. "Component type": DOST (silver cap) or DOSTPLUS (brass cap) - the QR code the check is given - or "From file name"
     (DOSTPLUS if the name contains DOSTPLUS, else DOST if it contains DOST). Can be changed at any time.
  3. "Seconds between pictures" (1 - 10, starts at 3) can be changed at any time; the new value applies from the
     picture on screen. Stop ends the run; Start again begins from the first picture.
  4. The check's step 3 compares the cap picture with the hub, top bearing and nut / washer pictures of the same part.
     In production the camera server keeps those of the cycle; here the first picture of each kind for the component
     type is taken from this machine's dry-run folders (hub_images/, top_bearing_images/, nut_washer_images/), or from
     the folder chosen with "Earlier pictures..." (files named <MODEL>_NN, <MODEL>_bearing_NN, <MODEL>_nut_NN). For
     pictures of other parts the comparison values are only indicative.
  5. The picture shown is exactly the one the check gives the frontend: shapes only, no text or values (comparison
     circle green / red, brightness circle; with seating on (Machine 2) the bore blue, the cap green / red, a silver
     cap's top face yellow). Verdict, reason, time and every value (R-B, brightness, brightening, seating offsets) go
     to the list at the bottom and to the terminal.
  6. At the end: OK / NOT OK counts. "Save marked pictures" writes the pictures to <folder>/cap_check_results/.

Settings: <vendor>.vision.cap.* in ApplicationConfiguration.properties (vendor from the folder: machine1 = allenbradley,
machine2 = siemens). The seating step uses MobileSAM through ModelManager (models/MobileSAM), loaded in the background
when the window opens; no Redis, PLC or camera needed (the check's log messages are collected by this window).

Run from the project root:   python test/machine2_dryrun/DryRun_Cap_Check_UI.py   [--sam <mobile_sam.pt>]
(--sam: load MobileSAM from that file instead of through ModelManager, which also needs the YOLO weights)
"""
import os
import pathlib
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["COSTHETA_PLC_VENDOR"] = "siemens" if HERE.name.startswith("machine2") else "allenbradley"

import cv2
from PIL import Image, ImageTk

import camera.CheckCap as CheckCapModule
from camera.CheckCap import CheckCap

QR = {"DOST": "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$",
      "DOSTPLUS": "8201206$400112VA1C$22881$15.10.2026$C5211702$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$"}
TYPES = ["DOST", "DOSTPLUS", "From file name"]
EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp")
VIEW_W, VIEW_H = 1120, 630
KEYS = (CheckCapModule.hubAndBottomBearingPictureKeyString, CheckCapModule.topBearingPictureKeyString,
        CheckCapModule.nutAndPlateWasherPictureKeyString)
PATTERNS = ("{m}_[0-9]*", "{m}_bearing_*", "{m}_nut_*")
SUBFOLDERS = ("hub_images", "top_bearing_images", "nut_washer_images")

# The check's own log messages, collected per call (errors give the reason for NOT OK)
_messages = []
CheckCapModule.logBoth = lambda level, source, message, *args, **kwargs: _messages.append((level, message))

if "--sam" in sys.argv:
    _samPath = sys.argv[sys.argv.index("--sam") + 1]
    _samModel = {}

    def _loadSam():
        if "m" not in _samModel:
            from mobile_sam import sam_model_registry
            import torch
            model = sam_model_registry["vit_t"](checkpoint=_samPath)
            model.to("cuda" if torch.cuda.is_available() else "cpu").eval()
            _samModel["m"] = model
        return _samModel["m"]

    CheckCap.samModelProvider = staticmethod(_loadSam)


def preloadModel() -> str:
    """Load MobileSAM once (the first seating check would otherwise wait for it). '' or the error."""
    if not CheckCap.SEATING_CHECK:
        return ""
    try:
        if CheckCap.samModelProvider is not None:
            CheckCap.samModelProvider()
        else:
            from camera.ModelManager import ModelManager
            ModelManager.get_instance().get_sam_model()
        return ""
    except Exception as e:
        return str(e)


def modelFor(choice: str, name: str) -> str:
    if choice != "From file name":
        return choice
    upper = name.upper()
    return "DOSTPLUS" if "DOSTPLUS" in upper else "DOST"


def earlierPictures(folder: pathlib.Path | None, model: str):
    """{key: picture} - the first hub, top bearing and nut picture of the model; None where there is none."""
    found = {}
    for key, pattern, sub in zip(KEYS, PATTERNS, SUBFOLDERS):
        where = folder if folder is not None else HERE / sub
        files = sorted(p for p in where.glob(pattern.format(m=model) + ".*") if p.suffix.lower() in EXTENSIONS)
        found[key] = cv2.resize(cv2.imread(str(files[0])), (1280, 720)) if files else None
    return found


def runCheck(image, model: str, earlier):
    """(ok, marked picture, reason, summary) of the real check."""
    _messages.clear()
    marked, ok = CheckCap.checkCap(image.copy(), dict(earlier), QR[model])
    ok = bool(ok)
    errors = [m.replace("CheckCap seating: ", "").replace("CheckCap: ", "")
              for level, m in _messages if level in ("logError", "logCritical")]
    reason = ""
    if not ok:
        final = [e for e in errors if e.startswith("CAP NOT DETECTED")]
        detail = [e for e in errors if not e.startswith("CAP NOT DETECTED")]
        reason = ": ".join(x for x in ((final[0].replace("CAP NOT DETECTED - ", "") if final else ""),
                                       (detail[0] if detail else "")) if x)
    summary = next((m.replace("CheckCap summary: ", "") for level, m in _messages if m.startswith("CheckCap summary:")), "")
    return ok, (marked if marked is not None else image), reason or "", summary


class CapCheckWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.files, self.index, self.running, self.timer = [], 0, False, None
        self.results, self.outFolder, self.photo, self.earlierFolder = [], None, None, None
        self.modelError, self.modelReady = "", not CheckCap.SEATING_CHECK
        root.title(f"Cap check - {os.environ['COSTHETA_PLC_VENDOR']} settings")

        bar = ttk.Frame(root); bar.pack(fill="x", padx=6, pady=4)
        ttk.Button(bar, text="Folder...", command=self.chooseFolder).pack(side="left")
        ttk.Button(bar, text="Picture...", command=self.choosePicture).pack(side="left", padx=4)
        self.source = tk.StringVar(master=root, value="(nothing chosen)")
        ttk.Label(bar, textvariable=self.source, width=48).pack(side="left", padx=6)
        ttk.Label(bar, text="Component type").pack(side="left", padx=(8, 2))
        self.componentType = tk.StringVar(master=root, value="DOST")
        ttk.Combobox(bar, textvariable=self.componentType, values=TYPES, state="readonly", width=14).pack(side="left")
        ttk.Label(bar, text="Seconds between pictures").pack(side="left", padx=(12, 2))
        self.seconds = tk.StringVar(master=root, value="3")
        ttk.Combobox(bar, textvariable=self.seconds, values=[str(i) for i in range(1, 11)], state="readonly",
                     width=4).pack(side="left")
        self.save = tk.BooleanVar(master=root, value=False)
        ttk.Checkbutton(bar, text="Save marked pictures", variable=self.save).pack(side="left", padx=10)
        self.startButton = ttk.Button(bar, text="Start", command=self.start); self.startButton.pack(side="left")
        self.stopButton = ttk.Button(bar, text="Stop", command=self.stop, state="disabled"); self.stopButton.pack(side="left", padx=4)

        bar2 = ttk.Frame(root); bar2.pack(fill="x", padx=6)
        ttk.Button(bar2, text="Earlier pictures...", command=self.chooseEarlier).pack(side="left")
        self.earlierText = tk.StringVar(master=root, value=f"hub / top bearing / nut pictures: {HERE.name} dry-run folders")
        ttk.Label(bar2, textvariable=self.earlierText).pack(side="left", padx=6)
        seat = (f"seating ON (brass cap to bore <= {CheckCap.SEATING_BRASS_OFFSET_MAX}, silver face on one circle >= "
                f"{CheckCap.SEATING_SILVER_FACE_FIT_MIN}, hub centre uncovered <= {CheckCap.SEATING_HUB_VISIBLE_MAX}, "
                f"silver outline to centre <= "
                f"{CheckCap.SEATING_SILVER_OUTLINE_OFFSET_MAX} px)" if CheckCap.SEATING_CHECK else "seating OFF")
        ttk.Label(bar2, text=f"centre {tuple(CheckCap.CENTER)}, {seat}").pack(side="right")

        self.canvas = tk.Label(root, background="black", width=VIEW_W, height=VIEW_H)
        self.canvas.pack(padx=6)
        self.status = tk.StringVar(master=root, value="Choose a folder or a picture, then Start."
                                   + ("" if self.modelReady else "  (loading MobileSAM...)"))
        ttk.Label(root, textvariable=self.status).pack(fill="x", padx=6)
        listFrame = ttk.Frame(root); listFrame.pack(fill="both", expand=True, padx=6, pady=4)
        self.listbox = tk.Listbox(listFrame, height=10, font=("Consolas", 10))
        scroll = ttk.Scrollbar(listFrame, command=self.listbox.yview)
        xscroll = ttk.Scrollbar(listFrame, orient="horizontal", command=self.listbox.xview)
        self.listbox.configure(yscrollcommand=scroll.set, xscrollcommand=xscroll.set)
        xscroll.pack(side="bottom", fill="x"); scroll.pack(side="right", fill="y")
        self.listbox.pack(side="left", fill="both", expand=True)
        root.protocol("WM_DELETE_WINDOW", self.close)
        if not self.modelReady:
            # the loading thread only sets flags (Tk may be used from this thread only); polled below
            threading.Thread(target=self._preload, daemon=True).start()
            self.root.after(200, self._pollPreload)

    def _preload(self):
        self.modelError = preloadModel()
        self.modelReady = True

    def _pollPreload(self):
        if not self.modelReady:
            self.root.after(200, self._pollPreload); return
        if self.modelError:
            self.status.set(f"MobileSAM could not be loaded: {self.modelError} - the seating step will be NOT OK")
        elif not self.running:
            self.status.set("MobileSAM loaded. Choose a folder or a picture, then Start.")

    def chooseFolder(self):
        folder = filedialog.askdirectory(title="Folder with cap pictures", parent=self.root)
        if folder:
            self.files = sorted(str(p) for p in pathlib.Path(folder).iterdir() if p.suffix.lower() in EXTENSIONS)
            self.outFolder = pathlib.Path(folder) / "cap_check_results"
            self.source.set(f"{folder}  ({len(self.files)} pictures)")

    def choosePicture(self):
        f = filedialog.askopenfilename(title="Cap picture", parent=self.root,
                                       filetypes=[("Pictures", " ".join("*" + e for e in EXTENSIONS)), ("All files", "*.*")])
        if f:
            self.files = [f]; self.outFolder = pathlib.Path(f).parent / "cap_check_results"; self.source.set(f)

    def chooseEarlier(self):
        folder = filedialog.askdirectory(title="Folder with the hub, top bearing and nut pictures", parent=self.root)
        if folder:
            self.earlierFolder = pathlib.Path(folder)
            self.earlierText.set(f"hub / top bearing / nut pictures: {folder}")

    def start(self):
        if not self.files:
            messagebox.showinfo("Cap check", "Choose a folder or a picture first.", parent=self.root); return
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
        if not self.modelReady:                       # wait for MobileSAM, then check
            self.status.set("Waiting for MobileSAM to load...")
            self.timer = self.root.after(200, self.step); return
        if self.index >= len(self.files):
            self.finish(); return
        path = self.files[self.index]; name = os.path.basename(path)
        model = modelFor(self.componentType.get(), name)       # read now: a change applies from this picture
        image = cv2.imread(path)
        marked, elapsed, summary = None, 0.0, ""
        if image is None:
            verdict, reason = "UNREADABLE", "the file could not be read as a picture"
        else:
            image = cv2.resize(image, (1280, 720))
            earlier = earlierPictures(self.earlierFolder, model)
            missing = [s for s, k in zip(("hub", "top bearing", "nut"), KEYS) if earlier[k] is None]
            t0 = time.perf_counter()
            ok, marked, reason, summary = runCheck(image, model, earlier)
            elapsed = time.perf_counter() - t0
            if missing:
                reason = f"no {', '.join(missing)} picture for {model}" + (f"; {reason}" if reason else "")
            verdict = "OK" if ok else "NOT OK"
        self.results.append((name, model, verdict, reason))
        line = f"{name:34s} {model:9s} {verdict:7s} {elapsed:5.2f} s" + (f"   {reason}" if reason else "")
        self.listbox.insert("end", line)
        if summary:
            self.listbox.insert("end", f"    {summary}")
        self.listbox.see("end")
        print(line + (f"\n    {summary}" if summary else ""), flush=True)
        if marked is not None:
            self.show(marked)
            if self.save.get():
                self.outFolder.mkdir(exist_ok=True); cv2.imwrite(str(self.outFolder / name), marked)
        self.status.set(f"Picture {self.index + 1} of {len(self.files)}: {name} as {model} - {verdict}"
                        + (f"  ({elapsed:.1f} s)" if image is not None else ""))
        self.index += 1
        # read the dropdown now, so a change made during the run applies from this picture
        self.timer = self.root.after(int(float(self.seconds.get()) * 1000), self.step)

    def show(self, marked):
        rgb = cv2.cvtColor(marked, cv2.COLOR_BGR2RGB); h, w = rgb.shape[:2]; s = min(VIEW_W / w, VIEW_H / h)
        picture = Image.fromarray(rgb).resize((int(w * s), int(h * s)), Image.LANCZOS)
        self.photo = ImageTk.PhotoImage(picture, master=self.root); self.canvas.configure(image=self.photo)

    def finish(self, stopped=False):
        self.running = False
        self.startButton.configure(state="normal"); self.stopButton.configure(state="disabled")
        counts = {}
        for _, _, verdict, _ in self.results:
            counts[verdict] = counts.get(verdict, 0) + 1
        summary = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "nothing checked"
        self.status.set(f"{'Stopped' if stopped else 'Done'} after {len(self.results)} of {len(self.files)} pictures - {summary}"
                        + (f"; marked pictures in {self.outFolder}" if self.save.get() and self.results else ""))


if __name__ == "__main__":
    root = tk.Tk()
    CapCheckWindow(root)
    root.mainloop()
