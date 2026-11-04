"""
DryRun_Bunk_Check_UI - the real bunk check on a folder of bung pictures, with this machine's settings
(<vendor>.vision.bunk.* in ApplicationConfiguration.properties), shown one picture at a time. Needs the MobileSAM models
(as the camera server does); a CUDA GPU makes it fast.

Both bunk checks run the same hole detection (camera/BunkSegmenter.py); choose which one in the window:
  - CheckBunk.checkBunk            - state 24, bung before the cap press
  - CheckNoCapBunk.checkNoCapBunk  - state 13, bung before the component press
The r<outer.disc.radius> disc round <vendor>.vision.bunk.outer.disc.center is segmented (gammas 2.0, 7.75, 4.0, 6.5, 1.0
until a definite answer); holes count only inside r<inner.disc.radius> round the same centre.
  DOST     : 4 holes forming a square (sides within square.side.length.tolerance of the median, corners
             square.min/max.interior.angle)
  DOSTPLUS : exactly 2 holes, dostplus.hole.pair.distance.min..max apart; a DOST square found = NOT OK

  Start: model (DOST / DOSTPLUS - every picture in the folder is checked as this model), folder, seconds between
  pictures, check (state 24 / state 13), "Save marked pictures" (<folder>/bunk_results/).
  Markings: cyan circle = the segmented disc, yellow circle and cross = the hole search disc and its centre, green =
  the holes found, red dots = their centres; banner = OK / NOT OK with the holes found, their spacing (DOSTPLUS: pair
  distance; DOST: the 4 sides), how far their centre is from the set centre, the deciding gamma and the time.

Run from the project root:   python test/machine1_dryrun/DryRun_Bunk_Check_UI.py
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
WAIT_CHOICES = ("0.5", "1", "2", "3", "5", "10", "20")
CHECKS = ("CheckBunk (state 24)", "CheckNoCapBunk (state 13)")
VIEW_W, VIEW_H = 1120, 630


class BunkCheckWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.BS = None                # camera.BunkSegmenter
        self.checks = {}              # title -> check function
        self.last = {}                # what the hole detection returned last (the deciding gamma)
        self.results = queue.Queue()
        self.advance = threading.Event()
        self.stopRequested = threading.Event()
        self.photo = None
        self.summary = []

        root.title(f"Dry run - bunk check ({os.environ['COSTHETA_PLC_VENDOR']})")
        bar = ttk.Frame(root, padding=6)
        bar.pack(fill="x")
        ttk.Label(bar, text="Model").pack(side="left")
        self.model = tk.StringVar(value="DOST")
        ttk.Combobox(bar, textvariable=self.model, values=("DOST", "DOSTPLUS"), state="readonly",
                     width=10).pack(side="left", padx=4)
        ttk.Button(bar, text="Folder...", command=self.chooseFolder).pack(side="left", padx=(10, 0))
        self.folder = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.folder, width=52).pack(side="left", padx=6)
        ttk.Label(bar, text="Seconds between pictures").pack(side="left", padx=(10, 0))
        self.seconds = tk.StringVar(value="3")
        ttk.Combobox(bar, textvariable=self.seconds, values=WAIT_CHOICES, state="readonly",
                     width=5).pack(side="left", padx=4)
        bar = ttk.Frame(root, padding=(6, 0, 6, 6))
        bar.pack(fill="x")
        ttk.Label(bar, text="Check").pack(side="left")
        self.check = tk.StringVar(value=CHECKS[0])
        ttk.Combobox(bar, textvariable=self.check, values=CHECKS, state="readonly", width=26).pack(side="left", padx=4)
        self.save = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Save marked pictures", variable=self.save).pack(side="left", padx=10)
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

        threading.Thread(target=self.load, name="Load models", daemon=True).start()
        root.after(100, self.poll)
        root.protocol("WM_DELETE_WINDOW", self.close)

    # ------------------------------------------------------------------ check
    def load(self):
        try:
            import camera.BunkSegmenter as BS
            from camera.CheckBunk import CheckBunk
            from camera.CheckNoCapBunk import CheckNoCapBunk
            t0 = time.time()
            BS.BunkSegmenter.get_instance()                      # loads MobileSAM + YOLO once
            original = BS.BunkSegmenter.segment_holes_single

            def recording(segmenter, preprocessed_image, gamma, expected_count, model_type):
                result = original(segmenter, preprocessed_image, gamma, expected_count, model_type)
                self.last["result"] = result                     # the last call is the deciding gamma
                return result

            BS.BunkSegmenter.segment_holes_single = recording
            self.BS = BS
            self.checks = {CHECKS[0]: CheckBunk.checkBunk, CHECKS[1]: CheckNoCapBunk.checkNoCapBunk}
            self.results.put(("ready",
                f"Models loaded in {time.time() - t0:.1f} s. Centre {tuple(BS.OUTER_DISC_CENTER)}, segmented disc "
                f"r{BS.OUTER_DISC_RADIUS}, holes within r{BS.INNER_DISC_RADIUS}; DOSTPLUS pair "
                f"{BS.DOSTPLUS_HOLE_PAIR_DISTANCE_MIN}-{BS.DOSTPLUS_HOLE_PAIR_DISTANCE_MAX} px; DOST square sides within "
                f"{BS.SQUARE_SIDE_LENGTH_TOLERANCE:.0%}."))
        except Exception as e:
            self.results.put(("fatal", f"Could not load the bunk check: {e}"))

    # ------------------------------------------------------------------ controls
    def chooseFolder(self):
        folder = filedialog.askdirectory(title="Folder with bung pictures")
        if folder:
            self.folder.set(folder)

    def pictures(self):
        folder = pathlib.Path(self.folder.get())
        if not self.folder.get() or not folder.is_dir():
            return []
        return sorted(p for p in folder.iterdir() if p.suffix.lower() in EXTENSIONS)

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
        outFolder = pathlib.Path(self.folder.get()) / "bunk_results"
        threading.Thread(target=self.run, args=(files, self.model.get(), self.check.get(), self.save.get(), outFolder),
                         name="Bunk check", daemon=True).start()

    def stop(self):
        self.stopRequested.set()
        self.advance.set()
        self.status.set("Stopping after the current picture ...")

    def close(self):
        self.stop()
        self.root.destroy()

    # ------------------------------------------------------------------ worker
    def run(self, files, model, checkTitle, save, outFolder):
        if save:
            outFolder.mkdir(exist_ok=True)
        check = self.checks[checkTitle]
        for n, path in enumerate(files, 1):
            if self.stopRequested.is_set():
                break
            self.results.put(("busy", f"{n}/{len(files)}  {path.name}: checking as {model} ..."))
            image = cv2.imread(str(path))
            if image is None:
                self.results.put(("shown", (n, len(files), path.name, None, False, "cannot read the picture", 0.0)))
                continue
            self.last.clear()
            t0 = time.time()
            try:
                marked, ok, gamma = check(image.copy(), QR[model], None)
                holes = self.holes()
                reason = self.reason(holes, gamma)
            except Exception as e:
                marked, ok, holes, reason = None, False, [], f"exception: {e}"
            seconds = time.time() - t0
            shown = self.decorate(image, marked, ok, holes, reason, seconds, path.name, model)
            if save:
                cv2.imwrite(str(outFolder / f"{'OK' if ok else 'NOTOK'}_{path.stem}_as_{model}.png"), shown)
            self.advance.clear()
            self.results.put(("shown", (n, len(files), path.name, shown, ok, reason, seconds)))
            self.advance.wait()
        self.results.put(("done", None))

    def holes(self):
        """Centres of the holes the deciding gamma found, in full-picture coordinates."""
        if "result" not in self.last:
            return []
        BS = self.BS
        _, components, _ = self.last["result"]
        ox = BS.OUTER_DISC_CENTER[0] - BS.INNER_DISC_RADIUS
        oy = BS.OUTER_DISC_CENTER[1] - BS.INNER_DISC_RADIUS
        return [(float(c[0]) + ox, float(c[1]) + oy) for c, _ in components]

    def reason(self, holes, gamma):
        BS = self.BS
        if not holes:
            return f"no hole pattern found{'' if gamma is None else f' (gamma {gamma})'}"
        pts = np.array(holes)
        if len(pts) == 2:
            spacing = f"pair {np.hypot(*(pts[0] - pts[1])):.1f} px"
        else:
            sides = [np.hypot(*(pts[i] - pts[(i + 1) % len(pts)])) for i in range(len(pts))]
            spacing = "sides " + "/".join(f"{s:.0f}" for s in sides) + " px"
        mid = pts.mean(axis=0)
        off = np.hypot(mid[0] - BS.OUTER_DISC_CENTER[0], mid[1] - BS.OUTER_DISC_CENTER[1])
        decided = f"gamma {gamma}" if gamma is not None else "no definite pattern at any gamma (last gamma tried shown)"
        return (f"{len(holes)} holes, {spacing}, their centre ({mid[0]:.0f},{mid[1]:.0f}) is {off:.1f} px from the set "
                f"centre; {decided}")

    def decorate(self, image, marked, ok, holes, reason, seconds, name, model):
        BS = self.BS
        out = marked.copy() if marked is not None and marked.shape == image.shape else image.copy()
        centre = tuple(int(v) for v in BS.OUTER_DISC_CENTER)
        cv2.circle(out, centre, int(BS.OUTER_DISC_RADIUS), (255, 255, 0), 1)
        cv2.circle(out, centre, int(BS.INNER_DISC_RADIUS), (0, 255, 255), 1)
        cv2.drawMarker(out, centre, (0, 255, 255), cv2.MARKER_CROSS, 16, 1)
        for x, y in holes:
            cv2.circle(out, (int(round(x)), int(round(y))), 4, (0, 0, 255), -1)
        colour = (0, 170, 0) if ok else (0, 0, 220)
        cv2.rectangle(out, (0, out.shape[0] - 70), (out.shape[1], out.shape[0]), colour, -1)
        cv2.putText(out, f"{'OK' if ok else 'NOT OK'}  bung as {model}", (12, out.shape[0] - 38),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
        cv2.putText(out, f"{reason}  |  {seconds:.2f} s  |  {name}", (12, out.shape[0] - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
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
                    messagebox.showerror("Bunk check", data)
                elif kind == "busy":
                    self.status.set(data)
                elif kind == "shown":
                    self.show(*data)
                elif kind == "done":
                    okCount = sum(1 for r in self.summary if r)
                    self.status.set(f"Done: {len(self.summary)} picture(s) as {self.model.get()}: {okCount} OK, "
                                    f"{len(self.summary) - okCount} NOT OK" +
                                    (f"  -  marked pictures in {self.folder.get()}/bunk_results"
                                     if self.save.get() else ""))
                    self.startButton.config(state="normal")
                    self.stopButton.config(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def show(self, n, total, name, image, ok, reason, seconds):
        self.summary.append(ok)
        self.list.insert("end", f"{n:3}/{total}  {'OK    ' if ok else 'NOT OK'}  {name:34}  {seconds:5.2f} s  {reason}")
        self.list.see("end")
        self.status.set(f"{n}/{total}  {name}: {'OK' if ok else 'NOT OK'}  -  {reason}")
        if image is not None:
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            h, w = rgb.shape[:2]
            scale = min(VIEW_W / w, VIEW_H / h, 1.0)
            self.photo = ImageTk.PhotoImage(Image.fromarray(rgb).resize((int(w * scale), int(h * scale))))
            self.view.config(image=self.photo)
        self.root.after(int(float(self.seconds.get()) * 1000), self.advance.set)


if __name__ == "__main__":
    window = tk.Tk()
    window.geometry("1180x960")
    BunkCheckWindow(window)
    window.mainloop()
