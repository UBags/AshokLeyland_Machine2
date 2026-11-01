"""
DryRun_Hub_Check - the real hub and bottom bearing check (CheckHubAndBottomBearing: MobileSAMv2 hole/stud polygon,
and - only if "Enforce bearing absence check" is ticked - the "no bearing in centre" check first) on a folder of
pictures, with this machine's settings, shown one picture at a time.

  1. Choose a folder of pictures (jpg / png / bmp) and the model (DOST = 4, DOSTPLUS = 5), then Start.
     "Enforce bearing absence check" = checkHubAndBottomBearing(..., enforceBearingAbsenceCheck=True). Off by
     default, as in production (CameraProcessorServer does not pass it). The centre value is shown either way.
  2. Each picture is checked; the result is shown with the program's markings for the chosen number of seconds
     (default 3), then the next picture is checked.
  3. Markings: the painted holes/studs of the winning polygon and "COUNT: n" (from the check itself; red with
     "MISMATCH" if the other model was found), plus, drawn by this window:
       yellow rings  = the area searched for holes/studs   (<vendor>.vision.hub.segment.*)
       magenta rings = the "no bearing in centre" area     (<vendor>.vision.hub.centre.check.*) with its value;
                       red when the value is at or above the limit (a bearing in the bore)
       banner        = OK / NOT OK, the reason, the winning gamma and the time taken.
  4. At the end: number OK / NOT OK and the list of results. "Save marked pictures" writes each marked picture to
     <folder>/hub_check_results/.

Settings: <vendor>.vision.hub.* in ApplicationConfiguration.properties (vendor from the folder: machine1 =
allenbradley, machine2 = siemens). Needs the models (models/MobileSAM/...) and a CUDA GPU (runs on CPU too, slowly).

Run from the project root:   python test/machine2_dryrun/DryRun_Hub_Check.py
"""
import os
import pathlib
import queue
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
import numpy as np
from PIL import Image, ImageTk

QR = {"DOST": "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$",
      "DOSTPLUS": "8201206$400112VA1C$22881$15.10.2026$C5211702$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$"}
EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp")
VIEW_W, VIEW_H = 1120, 630


class HubCheckWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.check = None                 # CheckHubAndBottomBearing, once the models are loaded
        self.module = None                # camera.CheckHubAndBottomBearing (for the centre-check settings)
        self.segmenterModule = None       # camera.HubAndBearingSegmenter (for the search-area settings)
        self.results = queue.Queue()
        self.advance = threading.Event()  # set by the window when a picture has been shown long enough
        self.stopRequested = threading.Event()
        self.worker = None
        self.photo = None
        self.summary = []

        root.title(f"Dry run - hub and bottom bearing check ({os.environ['COSTHETA_PLC_VENDOR']})")
        bar = ttk.Frame(root, padding=6)
        bar.pack(fill="x")
        ttk.Button(bar, text="Folder...", command=self.chooseFolder).pack(side="left")
        self.folder = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.folder, width=70).pack(side="left", padx=6)
        ttk.Label(bar, text="Model").pack(side="left")
        self.model = tk.StringVar(value="DOSTPLUS")
        ttk.Combobox(bar, textvariable=self.model, values=("DOST", "DOSTPLUS"), state="readonly",
                     width=10).pack(side="left", padx=4)
        ttk.Label(bar, text="Seconds per picture").pack(side="left", padx=(10, 0))
        self.seconds = tk.DoubleVar(value=3.0)
        ttk.Spinbox(bar, from_=0.5, to=30, increment=0.5, textvariable=self.seconds, width=5).pack(side="left", padx=4)
        bar = ttk.Frame(root, padding=(6, 0, 6, 6))
        bar.pack(fill="x")
        self.save = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Save marked pictures", variable=self.save).pack(side="left", padx=(0, 10))
        self.enforce = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Enforce bearing absence check", variable=self.enforce).pack(side="left", padx=(0, 10))
        self.startButton = ttk.Button(bar, text="Start", command=self.start, state="disabled")
        self.startButton.pack(side="left")
        self.stopButton = ttk.Button(bar, text="Stop", command=self.stop, state="disabled")
        self.stopButton.pack(side="left", padx=4)

        self.view = ttk.Label(root, anchor="center")
        self.view.pack(fill="both", expand=True)
        self.status = tk.StringVar(value="Loading the models ...")
        ttk.Label(root, textvariable=self.status, padding=6, font=("Segoe UI", 11, "bold")).pack(fill="x")
        self.list = tk.Listbox(root, height=7, font=("Consolas", 10))
        self.list.pack(fill="x", padx=6, pady=(0, 6))

        threading.Thread(target=self.loadModels, name="Load models", daemon=True).start()
        root.after(100, self.poll)
        root.protocol("WM_DELETE_WINDOW", self.close)

    # ------------------------------------------------------------------ models
    def loadModels(self):
        try:
            import camera.CheckHubAndBottomBearing as module
            import camera.HubAndBearingSegmenter as segmenterModule
            t0 = time.time()
            if module.SAM_AVAILABLE:
                module.HubAndBearingSegmenter.get_instance()      # loads MobileSAM + YOLO once
            self.module, self.segmenterModule, self.check = module, segmenterModule, module.CheckHubAndBottomBearing
            device = "CUDA" if (module.torch.cuda.is_available()) else "CPU"
            note = "" if module.SAM_AVAILABLE else "  -  SEGMENTER NOT AVAILABLE: every picture will be NOT OK"
            self.results.put(("ready", f"Models loaded on {device} in {time.time() - t0:.1f} s{note}. "
                                       f"Choose a folder and a model, then Start."))
        except Exception as e:
            self.results.put(("fatal", f"Could not load the hub check: {e}"))

    # ------------------------------------------------------------------ controls
    def chooseFolder(self):
        folder = filedialog.askdirectory(title="Folder with hub pictures")
        if folder:
            self.folder.set(folder)

    def pictures(self):
        folder = pathlib.Path(self.folder.get())
        return sorted(p for p in folder.iterdir() if p.suffix.lower() in EXTENSIONS) if folder.is_dir() else []

    def start(self):
        files = self.pictures()
        if not files:
            messagebox.showwarning("No pictures", "Choose a folder that contains jpg / png / bmp pictures.")
            return
        self.summary = []
        self.list.delete(0, "end")
        self.stopRequested.clear()
        self.startButton.config(state="disabled")
        self.stopButton.config(state="normal")
        outFolder = pathlib.Path(self.folder.get()) / "hub_check_results"
        self.worker = threading.Thread(target=self.run, args=(files, self.model.get(), self.save.get(), outFolder,
                                                              self.enforce.get()),
                                       name="Hub check", daemon=True)
        self.worker.start()

    def stop(self):
        self.stopRequested.set()
        self.advance.set()
        self.status.set("Stopping after the current picture ...")

    def close(self):
        self.stop()
        self.root.destroy()

    # ------------------------------------------------------------------ worker (checks; one picture at a time)
    def run(self, files, model, save, outFolder, enforce):
        if save:
            outFolder.mkdir(exist_ok=True)
        for n, path in enumerate(files, 1):
            if self.stopRequested.is_set():
                break
            self.results.put(("busy", f"{n}/{len(files)}  {path.name}: checking ..."))
            image = cv2.imread(str(path))
            if image is None:
                self.results.put(("shown", (n, len(files), path.name, None, False, "cannot read the picture", None, 0.0)))
                continue
            t0 = time.time()
            centreMean, centreLimit = self.centreValue(image)
            segmenter = self.module.HubAndBearingSegmenter.get_instance() if self.module.SAM_AVAILABLE else None
            if segmenter is not None:
                segmenter._last_detected_model, segmenter._last_is_match = None, True
            try:
                marked, ok, gamma = self.check.checkHubAndBottomBearing(image.copy(), QR[model],
                                                                        enforceBearingAbsenceCheck=enforce)
            except Exception as e:
                marked, ok, gamma = image, False, None
                reason = f"exception: {e}"
            else:
                if enforce and centreMean >= centreLimit:
                    reason = f"bearing in the centre (value {centreMean:.0f} >= {centreLimit})"
                elif ok:
                    reason = f"{4 if model == 'DOST' else 5} found, {model}"
                elif segmenter is not None and segmenter._last_is_match is False:
                    reason = f"MISMATCH: expected {model}, found {segmenter._last_detected_model}"
                else:
                    reason = f"no regular {4 if model == 'DOST' else 5}-hole/stud polygon found"
            seconds = time.time() - t0
            shown = self.decorate(marked if marked is not None else image, model, ok, reason, gamma, seconds,
                                  centreMean, centreLimit, path.name, enforce)
            if save:
                cv2.imwrite(str(outFolder / f"{'OK' if ok else 'NOTOK'}_{path.stem}.png"), shown)
            self.advance.clear()
            self.results.put(("shown", (n, len(files), path.name, shown, ok, reason, gamma, seconds)))
            self.advance.wait()                                   # the window says when to go on
        self.results.put(("done", None))

    def centreValue(self, image):
        """The value the 'no bearing in centre' check compares with its limit (same computation, same settings)."""
        m = self.module
        gray = cv2.cvtColor(cv2.LUT(image, self.check._get_gamma_lut()), cv2.COLOR_BGR2GRAY)
        mask = np.zeros(gray.shape, np.uint8)
        cv2.circle(mask, (m.HUB_CENTRE_CHECK_X, m.HUB_CENTRE_CHECK_Y), m.HUB_CENTRE_CHECK_OUTER_RADIUS, 255, -1)
        cv2.circle(mask, (m.HUB_CENTRE_CHECK_X, m.HUB_CENTRE_CHECK_Y), m.HUB_CENTRE_CHECK_INNER_RADIUS, 0, -1)
        return float(gray[mask > 0].mean()), m.HUB_CENTRE_CHECK_MAX_MEAN

    def decorate(self, image, model, ok, reason, gamma, seconds, centreMean, centreLimit, name, enforce):
        m, s = self.module, self.segmenterModule
        out = image.copy()
        centre = (s.HUB_SEGMENT_CENTER_X, s.HUB_SEGMENT_CENTER_Y)
        cv2.circle(out, centre, s.HUB_SEGMENT_OUTER_RADIUS, (0, 255, 255), 1)
        cv2.circle(out, centre, s.HUB_SEGMENT_INNER_RADIUS, (0, 255, 255), 1)
        cc = (m.HUB_CENTRE_CHECK_X, m.HUB_CENTRE_CHECK_Y)
        centreColour = (255, 0, 255) if centreMean < centreLimit else (0, 0, 255)
        cv2.circle(out, cc, m.HUB_CENTRE_CHECK_OUTER_RADIUS, centreColour, 1)
        cv2.circle(out, cc, m.HUB_CENTRE_CHECK_INNER_RADIUS, centreColour, 1)
        cv2.putText(out, f"centre {centreMean:.0f} (limit {centreLimit}"
                         f"{'' if enforce else ', not enforced'})", (10, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                    centreColour, 2)
        colour = (0, 170, 0) if ok else (0, 0, 220)
        cv2.rectangle(out, (0, out.shape[0] - 70), (out.shape[1], out.shape[0]), colour, -1)
        cv2.putText(out, f"{'OK' if ok else 'NOT OK'}  {model}", (12, out.shape[0] - 38), cv2.FONT_HERSHEY_SIMPLEX,
                    1.0, (255, 255, 255), 2)
        cv2.putText(out, f"{reason}  |  gamma {gamma if gamma else '-'}  |  {seconds:.2f} s  |  {name}",
                    (12, out.shape[0] - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
        return out

    # ------------------------------------------------------------------ window side
    def poll(self):
        try:
            while True:
                kind, data = self.results.get_nowait()
                if kind == "ready":
                    self.status.set(data)
                    self.startButton.config(state="normal")
                elif kind == "fatal":
                    self.status.set(data)
                    messagebox.showerror("Hub check", data)
                elif kind == "busy":
                    self.status.set(data)
                elif kind == "shown":
                    self.show(*data)
                elif kind == "done":
                    okCount = sum(1 for r in self.summary if r)
                    self.status.set(f"Done: {len(self.summary)} picture(s): {okCount} OK, "
                                    f"{len(self.summary) - okCount} NOT OK" +
                                    (f"  -  marked pictures in {self.folder.get()}/hub_check_results"
                                     if self.save.get() else ""))
                    self.startButton.config(state="normal")
                    self.stopButton.config(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def show(self, n, total, name, image, ok, reason, gamma, seconds):
        self.summary.append(ok)
        self.list.insert("end", f"{n:3}/{total}  {'OK    ' if ok else 'NOT OK'}  {name:32}  gamma {gamma or '-'}  "
                                f"{seconds:5.2f} s  {reason}")
        self.list.see("end")
        self.status.set(f"{n}/{total}  {name}: {'OK' if ok else 'NOT OK'}  -  {reason}")
        if image is not None:
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            h, w = rgb.shape[:2]
            scale = min(VIEW_W / w, VIEW_H / h, 1.0)
            picture = Image.fromarray(rgb).resize((int(w * scale), int(h * scale)))
            self.photo = ImageTk.PhotoImage(picture)
            self.view.config(image=self.photo)
        self.root.after(int(max(0.5, float(self.seconds.get())) * 1000), self.advance.set)


if __name__ == "__main__":
    window = tk.Tk()
    window.geometry("1180x930")
    HubCheckWindow(window)
    window.mainloop()
