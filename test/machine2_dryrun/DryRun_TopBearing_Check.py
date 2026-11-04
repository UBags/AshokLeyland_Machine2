"""
DryRun_TopBearing_Check - the real top bearing check (CheckTopBearing.checkTopBearing) on a folder of pictures, with
this machine's settings, shown one picture at a time. No GPU needed (OpenCV only).

The check needs the hub picture of the same part (taken at the hub station; production keeps it in
currentPictures[hubAndBottomBearingPicture]): the bearing ring must be brighter than the same ring in the hub picture.
  - Paired: for a picture X.jpg, X_hub.jpg (same folder, same extension) is used as its hub picture if it exists.
    Pictures whose name ends in _hub are only used as hub pictures.
  - Otherwise: the "Hub reference" picture chosen in the window is used (an open-bore hub picture of this machine).

  1. Choose the folder, the hub reference picture, the seconds per picture (default 3); Start.
  2. Each picture is checked; the result is shown for the chosen time, then the next picture is checked.
  3. Markings drawn by this window on the full picture:
       yellow circle = the area searched   (<vendor>.vision.topbearing.annular.center.* / outer.radius)
       yellow crosses = the start centres tried (centre + <vendor>.vision.topbearing.center.offsets)
       green ring    = the bearing ring found (inner and outer edge), when OK
       inset (top right) = the check's own result picture (the searched area with its markings)
       banner        = OK / NOT OK, the reason (brightness difference against the limit, or no ring found),
                       the hub picture used and the time taken.
  4. At the end: number OK / NOT OK and the list. "Save marked pictures" writes them to <folder>/top_bearing_results/.

Settings: <vendor>.vision.topbearing.* in ApplicationConfiguration.properties (vendor from the folder: machine1 =
allenbradley, machine2 = siemens).

Run from the project root:   python test/machine2_dryrun/DryRun_TopBearing_Check.py
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

EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp")
VIEW_W, VIEW_H = 1120, 630


class TopBearingCheckWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.module = None                # camera.CheckTopBearing
        self.hubKey = None                # currentPictures key of the hub picture
        self.messages = []                # what the check logged for the current picture
        self.results = queue.Queue()
        self.advance = threading.Event()
        self.stopRequested = threading.Event()
        self.photo = None
        self.summary = []

        root.title(f"Dry run - top bearing check ({os.environ['COSTHETA_PLC_VENDOR']})")
        bar = ttk.Frame(root, padding=6)
        bar.pack(fill="x")
        ttk.Button(bar, text="Folder...", command=self.chooseFolder).pack(side="left")
        self.folder = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.folder, width=48).pack(side="left", padx=6)
        ttk.Button(bar, text="Hub reference...", command=self.chooseReference).pack(side="left")
        self.reference = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.reference, width=40).pack(side="left", padx=6)
        bar = ttk.Frame(root, padding=(6, 0, 6, 6))
        bar.pack(fill="x")
        ttk.Label(bar, text="Seconds per picture").pack(side="left")
        self.seconds = tk.DoubleVar(value=3.0)
        ttk.Spinbox(bar, from_=0.5, to=30, increment=0.5, textvariable=self.seconds, width=5).pack(side="left", padx=4)
        self.save = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Save marked pictures", variable=self.save).pack(side="left", padx=10)
        self.startButton = ttk.Button(bar, text="Start", command=self.start, state="disabled")
        self.startButton.pack(side="left")
        self.stopButton = ttk.Button(bar, text="Stop", command=self.stop, state="disabled")
        self.stopButton.pack(side="left", padx=4)

        self.view = ttk.Label(root, anchor="center")
        self.view.pack(fill="both", expand=True)
        self.status = tk.StringVar(value="Loading the check ...")
        ttk.Label(root, textvariable=self.status, padding=6, font=("Segoe UI", 11, "bold")).pack(fill="x")
        self.list = tk.Listbox(root, height=7, font=("Consolas", 10))
        self.list.pack(fill="x", padx=6, pady=(0, 6))

        threading.Thread(target=self.load, name="Load check", daemon=True).start()
        root.after(100, self.poll)
        root.protocol("WM_DELETE_WINDOW", self.close)

    # ------------------------------------------------------------------ check
    def load(self):
        try:
            import camera.CheckTopBearing as module
            from utils.RedisUtils import hubAndBottomBearingPictureKeyString
            original = module.logBoth

            def capture(level, source, message, messageType=None):     # keep the reason, still log as usual
                self.messages.append(str(message))
                return original(level, source, message, messageType)

            module.logBoth = capture
            self.module, self.hubKey = module, hubAndBottomBearingPictureKeyString
            T = module.CheckTopBearing
            self.results.put(("ready", f"Ready. Search area centre ({T.ANNULAR_CENTER_X}, {T.ANNULAR_CENTER_Y}), "
                                       f"radius {T.ANNULAR_OUTER_RADIUS}; brightness limit "
                                       f"{T.FINAL_GAMMA_THRESHOLD}. Choose a folder and a hub reference, then Start."))
        except Exception as e:
            self.results.put(("fatal", f"Could not load the top bearing check: {e}"))

    # ------------------------------------------------------------------ controls
    def chooseFolder(self):
        folder = filedialog.askdirectory(title="Folder with top bearing pictures")
        if folder:
            self.folder.set(folder)

    def chooseReference(self):
        path = filedialog.askopenfilename(title="Hub reference picture (open bore, this machine)",
                                          filetypes=[("Pictures", "*.jpg *.jpeg *.png *.bmp"), ("All files", "*.*")])
        if path:
            self.reference.set(path)

    def pictures(self):
        folder = pathlib.Path(self.folder.get())
        if not folder.is_dir():
            return []
        return sorted(p for p in folder.iterdir() if p.suffix.lower() in EXTENSIONS and not p.stem.endswith("_hub"))

    def start(self):
        files = self.pictures()
        if not files:
            messagebox.showwarning("No pictures", "Choose a folder that contains jpg / png / bmp pictures.")
            return
        unpaired = [p for p in files if not p.with_name(p.stem + "_hub" + p.suffix).exists()]
        if unpaired and not pathlib.Path(self.reference.get()).is_file():
            messagebox.showwarning("Hub reference", f"{len(unpaired)} picture(s) have no X_hub picture next to them: "
                                                    f"choose a hub reference picture.")
            return
        self.summary = []
        self.list.delete(0, "end")
        self.stopRequested.clear()
        self.startButton.config(state="disabled")
        self.stopButton.config(state="normal")
        outFolder = pathlib.Path(self.folder.get()) / "top_bearing_results"
        threading.Thread(target=self.run, args=(files, self.reference.get(), self.save.get(), outFolder),
                         name="Top bearing check", daemon=True).start()

    def stop(self):
        self.stopRequested.set()
        self.advance.set()
        self.status.set("Stopping after the current picture ...")

    def close(self):
        self.stop()
        self.root.destroy()

    # ------------------------------------------------------------------ worker
    def run(self, files, referencePath, save, outFolder):
        if save:
            outFolder.mkdir(exist_ok=True)
        T = self.module.CheckTopBearing
        reference = cv2.imread(referencePath) if referencePath else None
        for n, path in enumerate(files, 1):
            if self.stopRequested.is_set():
                break
            self.results.put(("busy", f"{n}/{len(files)}  {path.name}: checking ..."))
            image = cv2.imread(str(path))
            pair = path.with_name(path.stem + "_hub" + path.suffix)
            hub, hubName = (cv2.imread(str(pair)), pair.name) if pair.exists() else (reference, pathlib.Path(referencePath).name)
            if image is None or hub is None:
                self.results.put(("shown", (n, len(files), path.name, None, False, "cannot read the picture", "", 0.0)))
                continue
            self.messages.clear()
            t0 = time.time()
            try:
                overlay, ok, geometry = T.checkTopBearing(image.copy(), {self.hubKey: hub})
                reason = self.reason(ok, geometry, image, hub)
            except Exception as e:
                overlay, ok, geometry, reason = None, False, None, f"exception: {e}"
            seconds = time.time() - t0
            shown = self.decorate(image, overlay, ok, geometry, reason, hubName, seconds, path.name)
            if save:
                cv2.imwrite(str(outFolder / f"{'OK' if ok else 'NOTOK'}_{path.stem}.png"), shown)
            self.advance.clear()
            self.results.put(("shown", (n, len(files), path.name, shown, ok, reason, hubName, seconds)))
            self.advance.wait()
        self.results.put(("done", None))

    def reason(self, ok, geometry, image, hub):
        T = self.module.CheckTopBearing
        if ok and geometry:
            # the same brightness comparison the check made, on the ring it found (gamma, grey, mean inside the ring)
            lut = T._create_gamma_lut(T.FINAL_GAMMA_VALUE)
            mask = np.zeros(image.shape[:2], np.uint8)
            centre = tuple(int(v) for v in geometry["center"])
            cv2.circle(mask, centre, int(geometry["outer_radius"]), 255, -1)
            cv2.circle(mask, centre, int(geometry["inner_radius"]), 0, -1)
            grey = lambda picture: cv2.cvtColor(lut[picture], cv2.COLOR_BGR2GRAY)[mask > 0].mean()
            return (f"bearing ring r {geometry['inner_radius']}-{geometry['outer_radius']} (outer diameter "
                    f"{geometry.get('outer_diameter', 0):.1f}, width {geometry.get('width', 0):.1f}, mean diameter "
                    f"{geometry.get('mean_diameter', 0):.1f}), brighter than the hub "
                    f"picture by {grey(image) - grey(hub):+.1f} (> {T.FINAL_GAMMA_THRESHOLD})")
        for message in self.messages:
            if "Failed brightness check" in message:
                diff = message.split("diff=")[1].split(" ")[0] if "diff=" in message else "?"
                return f"ring found, but brighter than the hub picture by only {diff} (limit {T.FINAL_GAMMA_THRESHOLD})"
        for message in self.messages:
            if "not a bearing's size" in message:              # ring found, but outside the bearing size ranges
                return "ring found, but " + message.split("ring is ", 1)[1][:240]
        for message in self.messages:
            if "NO VALID ANNULUS DETECTED" in message:
                return "no bearing ring found"
        return self.messages[-1][:120] if self.messages else "NOT OK"

    def decorate(self, image, overlay, ok, geometry, reason, hubName, seconds, name):
        T = self.module.CheckTopBearing
        out = image.copy()
        centre = (T.ANNULAR_CENTER_X, T.ANNULAR_CENTER_Y)
        cv2.circle(out, centre, T.ANNULAR_OUTER_RADIUS, (0, 255, 255), 1)
        for dx, dy in T.CENTER_OFFSETS:
            cv2.drawMarker(out, (centre[0] + dx, centre[1] + dy), (0, 255, 255), cv2.MARKER_CROSS, 12, 1)
        if ok and geometry:
            c = tuple(int(v) for v in geometry["center"])
            cv2.circle(out, c, int(geometry["outer_radius"]), (0, 220, 0), 2)
            cv2.circle(out, c, int(geometry["inner_radius"]), (0, 220, 0), 2)
        if overlay is not None and overlay.ndim == 3 and overlay.shape[0] < out.shape[0]:
            inset = cv2.resize(overlay, (260, 260)) if overlay.shape[0] != 260 else overlay
            out[10:270, out.shape[1] - 270:out.shape[1] - 10] = inset
            cv2.rectangle(out, (out.shape[1] - 271, 9), (out.shape[1] - 9, 271), (255, 255, 255), 1)
        colour = (0, 170, 0) if ok else (0, 0, 220)
        cv2.rectangle(out, (0, out.shape[0] - 70), (out.shape[1], out.shape[0]), colour, -1)
        cv2.putText(out, "OK  top bearing present" if ok else "NOT OK", (12, out.shape[0] - 38),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
        cv2.putText(out, f"{reason}  |  hub {hubName}  |  {seconds:.2f} s  |  {name}", (12, out.shape[0] - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
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
                    messagebox.showerror("Top bearing check", data)
                elif kind == "busy":
                    self.status.set(data)
                elif kind == "shown":
                    self.show(*data)
                elif kind == "done":
                    okCount = sum(1 for r in self.summary if r)
                    self.status.set(f"Done: {len(self.summary)} picture(s): {okCount} OK, "
                                    f"{len(self.summary) - okCount} NOT OK" +
                                    (f"  -  marked pictures in {self.folder.get()}/top_bearing_results"
                                     if self.save.get() else ""))
                    self.startButton.config(state="normal")
                    self.stopButton.config(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def show(self, n, total, name, image, ok, reason, hubName, seconds):
        self.summary.append(ok)
        self.list.insert("end", f"{n:3}/{total}  {'OK    ' if ok else 'NOT OK'}  {name:30}  {seconds:5.2f} s  "
                                f"hub {hubName}  {reason}")
        self.list.see("end")
        self.status.set(f"{n}/{total}  {name}: {'OK' if ok else 'NOT OK'}  -  {reason}")
        if image is not None:
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            h, w = rgb.shape[:2]
            scale = min(VIEW_W / w, VIEW_H / h, 1.0)
            self.photo = ImageTk.PhotoImage(Image.fromarray(rgb).resize((int(w * scale), int(h * scale))))
            self.view.config(image=self.photo)
        self.root.after(int(max(0.5, float(self.seconds.get())) * 1000), self.advance.set)


if __name__ == "__main__":
    window = tk.Tk()
    window.geometry("1180x930")
    TopBearingCheckWindow(window)
    window.mainloop()
