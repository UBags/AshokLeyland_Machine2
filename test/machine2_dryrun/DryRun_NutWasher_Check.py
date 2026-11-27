"""
DryRun_NutWasher_Check - the real nut and plate washer check (CheckNutAndPlateWasher.checkNutAndPlateWasher) on a
folder of nut pictures, with this machine's settings, shown one picture at a time.

The check has two parts:
  1. the nut: MobileSAMv2 + hexagon tests in a disc round vision.<vendor>.nutwasher.nut.detection.center (needs the
     models; a CUDA GPU makes it fast);
  2. the washer (when check.washers.presence.in.nutandplatewasher=True, or the tick box here): in the bearing ring
     found by the top bearing check, minus the nut, the top bearing picture of the same part must be brighter in red
     (diff_R) and in red-minus-blue (diff_RB) than the nut picture, by more than vision.<vendor>.nutwasher.washer.
     threshold.r / .rb. The washer covers the bright bearing race; without a washer the ring looks as it did.
So each nut picture needs the top bearing picture of the same part:
  - Paired: for a nut picture X.jpg, X_bearing.jpg (same folder, same extension) is used if it exists. Pictures whose
    name ends in _bearing are only used as bearing pictures.
  - Otherwise: the "Bearing picture" chosen in the window is used.
The bearing ring is found in the bearing picture by the real top bearing check (same settings, same code).

  Start: folder, bearing picture, model (DOST / DOSTPLUS), seconds per picture (default 3), "Check washer" (starts as
  the configuration says), "Save marked pictures" (<folder>/nut_washer_results/).
  Markings on the full picture: yellow circle and cross = the nut search area and its centre; magenta cross = the
  nut reference centre (vision.<vendor>.nut.reference.center.* in the search area); blue ring = the bearing ring;
  green = the nut found; inset (top right) = the check's own result picture; banner = OK / NOT OK with the reason
  (nut not found and why, or the two washer differences against their limits) and the time.

Run from the project root:   python test/machine2_dryrun/DryRun_NutWasher_Check.py
"""
import os
import pathlib
import queue
import re
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


class NutWasherCheckWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.module = None            # camera.CheckNutAndPlateWasher
        self.detectorModule = None    # camera.HexagonNutDetector
        self.topBearing = None        # CheckTopBearing (to find the bearing ring in the bearing picture)
        self.keys = None              # (hub picture key, top bearing picture key)
        self.messages = []            # what the check logged for the current picture
        self.lastNut = None           # what the nut detector returned for the current picture
        self.results = queue.Queue()
        self.advance = threading.Event()
        self.stopRequested = threading.Event()
        self.photo = None
        self.summary = []

        root.title(f"Dry run - nut and plate washer check ({os.environ['COSTHETA_PLC_VENDOR']})")
        bar = ttk.Frame(root, padding=6)
        bar.pack(fill="x")
        ttk.Button(bar, text="Folder...", command=self.chooseFolder).pack(side="left")
        self.folder = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.folder, width=48).pack(side="left", padx=6)
        ttk.Button(bar, text="Bearing picture...", command=self.chooseBearing).pack(side="left")
        self.bearing = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.bearing, width=40).pack(side="left", padx=6)
        bar = ttk.Frame(root, padding=(6, 0, 6, 6))
        bar.pack(fill="x")
        ttk.Label(bar, text="Model").pack(side="left")
        self.model = tk.StringVar(value="DOST")
        ttk.Combobox(bar, textvariable=self.model, values=("DOST", "DOSTPLUS"), state="readonly",
                     width=10).pack(side="left", padx=4)
        ttk.Label(bar, text="Seconds per picture").pack(side="left", padx=(10, 0))
        self.seconds = tk.DoubleVar(value=3.0)
        ttk.Spinbox(bar, from_=0.5, to=30, increment=0.5, textvariable=self.seconds, width=5).pack(side="left", padx=4)
        self.checkWasher = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Check washer", variable=self.checkWasher).pack(side="left", padx=10)
        self.save = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Save marked pictures", variable=self.save).pack(side="left", padx=(0, 10))
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
            import camera.CheckNutAndPlateWasher as module
            import camera.HexagonNutDetector as detectorModule
            from camera.CheckTopBearing import CheckTopBearing
            from Configuration import CosThetaConfigurator
            from utils.RedisUtils import hubAndBottomBearingPictureKeyString, topBearingPictureKeyString
            original = module.logBoth

            def capture(level, source, message, messageType=None):     # keep the reason, still log as usual
                self.messages.append(str(message))
                return original(level, source, message, messageType)

            module.logBoth = capture
            t0 = time.time()
            detector = module.CheckNutAndPlateWasher._get_nut_detector()      # loads MobileSAM + YOLO once
            detect = detector.detect_nut_in_image

            def detectAndKeep(*args, **kwargs):                               # keep what the detector said
                found, result = detect(*args, **kwargs)
                self.lastNut = (found, result)
                return found, result

            detector.detect_nut_in_image = detectAndKeep
            self.module, self.detectorModule, self.topBearing = module, detectorModule, CheckTopBearing
            self.keys = (hubAndBottomBearingPictureKeyString, topBearingPictureKeyString)
            configured = bool(CosThetaConfigurator.getInstance().getCheckWashersPresenceInNutAndPlateWasher())
            self.configurator = CosThetaConfigurator
            C = module.CheckNutAndPlateWasher
            self.results.put(("ready", (configured,
                f"Models loaded in {time.time() - t0:.1f} s. Nut search centre {tuple(C.NUT_DETECTION_CENTER)} radius "
                f"{C.NUT_DETECTION_RADIUS}; washer limits diff_R > {C.WASHER_THRESHOLD_R}, diff_RB "
                + (f"> {C.WASHER_THRESHOLD_RB}" if getattr(C, "WASHER_CHECK_RB", True) else "not checked") + f"; configuration: washer check {'ON' if configured else 'OFF'}.")))
        except Exception as e:
            self.results.put(("fatal", f"Could not load the nut and washer check: {e}"))

    # ------------------------------------------------------------------ controls
    def chooseFolder(self):
        folder = filedialog.askdirectory(title="Folder with nut pictures")
        if folder:
            self.folder.set(folder)

    def chooseBearing(self):
        path = filedialog.askopenfilename(title="Top bearing picture (same part / same machine)",
                                          filetypes=[("Pictures", "*.jpg *.jpeg *.png *.bmp"), ("All files", "*.*")])
        if path:
            self.bearing.set(path)

    def pictures(self):
        folder = pathlib.Path(self.folder.get())
        if not folder.is_dir():
            return []
        return sorted(p for p in folder.iterdir()
                      if p.suffix.lower() in EXTENSIONS and not p.stem.endswith("_bearing"))

    def start(self):
        files = self.pictures()
        if not files:
            messagebox.showwarning("No pictures", "Choose a folder that contains jpg / png / bmp pictures.")
            return
        unpaired = [p for p in files if not p.with_name(p.stem + "_bearing" + p.suffix).exists()]
        if unpaired and not pathlib.Path(self.bearing.get()).is_file():
            messagebox.showwarning("Bearing picture", f"{len(unpaired)} picture(s) have no X_bearing picture next to "
                                                      f"them: choose a top bearing picture.")
            return
        washer = self.checkWasher.get()
        self.configurator.getCheckWashersPresenceInNutAndPlateWasher = classmethod(lambda cls, _w=washer: _w)
        self.summary = []
        self.list.delete(0, "end")
        self.stopRequested.clear()
        self.startButton.config(state="disabled")
        self.stopButton.config(state="normal")
        outFolder = pathlib.Path(self.folder.get()) / "nut_washer_results"
        threading.Thread(target=self.run, args=(files, self.bearing.get(), self.model.get(), washer, self.save.get(),
                                                outFolder), name="Nut washer check", daemon=True).start()

    def stop(self):
        self.stopRequested.set()
        self.advance.set()
        self.status.set("Stopping after the current picture ...")

    def close(self):
        self.stop()
        self.root.destroy()

    # ------------------------------------------------------------------ worker
    def bearingRing(self, picture):
        """The bearing ring as production gets it: the top bearing check on the bearing picture (the hub reference is
        black, so its brightness test always passes and only the ring matters)."""
        hubKey, _ = self.keys
        _, _, geometry = self.topBearing.checkTopBearing(picture, {hubKey: np.zeros_like(picture)})
        return geometry

    def run(self, files, bearingPath, model, washer, save, outFolder):
        if save:
            outFolder.mkdir(exist_ok=True)
        _, topKey = self.keys
        C = self.module.CheckNutAndPlateWasher
        common = cv2.imread(bearingPath) if bearingPath else None
        rings = {}
        for n, path in enumerate(files, 1):
            if self.stopRequested.is_set():
                break
            self.results.put(("busy", f"{n}/{len(files)}  {path.name}: checking ..."))
            image = cv2.imread(str(path))
            pair = path.with_name(path.stem + "_bearing" + path.suffix)
            bearingName = pair.name if pair.exists() else pathlib.Path(bearingPath).name
            bearing = cv2.imread(str(pair)) if pair.exists() else common
            if image is None or bearing is None:
                self.results.put(("shown", (n, len(files), path.name, None, False, "cannot read the picture", 0.0)))
                continue
            if bearingName not in rings:
                rings[bearingName] = self.bearingRing(bearing)
            geometry = rings[bearingName]
            self.messages.clear()
            self.lastNut = None
            t0 = time.time()
            try:
                overlay, ok = C.checkNutAndPlateWasher(image.copy(), {topKey: bearing}, QR[model],
                                                       bearing_geometry=geometry)
                reason = self.reason(ok, washer, geometry, bearingName)
            except Exception as e:
                overlay, ok, reason = None, False, f"exception: {e}"
            seconds = time.time() - t0
            shown = self.decorate(image, overlay, ok, geometry, reason, seconds, path.name)
            if save:
                cv2.imwrite(str(outFolder / f"{'OK' if ok else 'NOTOK'}_{path.stem}.png"), shown)
            self.advance.clear()
            self.results.put(("shown", (n, len(files), path.name, shown, ok, reason, seconds)))
            self.advance.wait()
        self.results.put(("done", None))

    def reason(self, ok, washer, geometry, bearingName):
        C = self.module.CheckNutAndPlateWasher
        found, result = self.lastNut if self.lastNut else (False, {})
        fallback = next((m for m in self.messages if "ring fallback" in m), None)    # no hexagon: ring of a nut's size?
        if not found:
            failure = (result or {}).get("failure_metrics") or {}
            why = failure.get("detail") or failure.get("reason") or "no hexagon"
            if not (fallback and "nut accepted by the ring fallback" in fallback):
                return f"nut not found: {str(why)[:90]}" + (f"; {fallback.split('ring fallback: ', 1)[1][:140]}" if fallback else "")
            nutText = f"no hexagon ({str(why)[:60]}); nut accepted by the ring fallback: " + fallback.split("ring fallback: ", 1)[1][:110]
        else:
            nutText = f"nut found (symmetry {result.get('composite_score', 0):.0f})"
        for message in self.messages:
            if "not a nut's size" in message:                  # outline found, but outside the nut size ranges
                return "shape found is " + message.split("is ", 1)[1][:240]
            if "Nut size:" in message:
                nutText = f"nut found (symmetry {result.get('composite_score', 0):.0f}; {message.split('Nut size: ')[1]})"
        if not washer:
            return nutText + ", washer check off"
        if geometry is None:
            return nutText + f", no bearing ring in the bearing picture {bearingName}"
        for message in self.messages:
            if "diff_R=" in message:
                values = dict(part.split("=") for part in message.split("] ")[-1].split(", "))
                dR, dRB = float(values["diff_R"]), float(values["diff_RB"])
                rbText = (f"diff_RB {dRB:+.1f} ({'>' if dRB > C.WASHER_THRESHOLD_RB else '<='} {C.WASHER_THRESHOLD_RB})"
                          if getattr(C, "WASHER_CHECK_RB", True) else f"diff_RB {dRB:+.1f} (not checked)")
                return (f"{nutText}; washer: diff_R {dR:+.1f} ({'>' if dR > C.WASHER_THRESHOLD_R else '<='} "
                        f"{C.WASHER_THRESHOLD_R}), {rbText}")
        return nutText + ("" if ok else ", washer check could not run")

    def decorate(self, image, overlay, ok, geometry, reason, seconds, name):
        C, D = self.module.CheckNutAndPlateWasher, self.detectorModule
        out = image.copy()
        cx, cy = (int(v) for v in C.NUT_DETECTION_CENTER)
        r = int(C.NUT_DETECTION_RADIUS)
        cv2.circle(out, (cx, cy), r, (0, 255, 255), 1)
        cv2.drawMarker(out, (cx, cy), (0, 255, 255), cv2.MARKER_CROSS, 12, 1)
        cv2.drawMarker(out, (cx - r + D.NUT_REFERENCE_CENTER_X, cy - r + D.NUT_REFERENCE_CENTER_Y), (255, 0, 255),
                       cv2.MARKER_TILTED_CROSS, 12, 2)
        if geometry:
            c = tuple(int(v) for v in geometry["center"])
            cv2.circle(out, c, int(geometry["outer_radius"]), (255, 120, 0), 1)
            cv2.circle(out, c, int(geometry["inner_radius"]), (255, 120, 0), 1)
        found, result = self.lastNut if self.lastNut else (False, {})
        for message in self.messages:                     # nut accepted by the ring fallback: draw its ring
            m = re.search(r"ring fallback: ring outer diameter ([0-9.]+) px.*centre \(([0-9.]+), ([0-9.]+)\)", message)
            if m:
                cv2.circle(out, (int(round(float(m.group(2)))), int(round(float(m.group(3))))),
                           int(round(float(m.group(1)) / 2)), (0, 220, 0), 2)
        if found and result.get("mask") is not None:
            full = np.zeros(out.shape[:2], np.uint8)
            mask = result["mask"]
            full[cy - r:cy - r + mask.shape[0], cx - r:cx - r + mask.shape[1]] = mask
            contours, _ = cv2.findContours(full, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(out, contours, -1, (0, 220, 0), 2)
        if overlay is not None and overlay.ndim == 3 and overlay.shape[0] < out.shape[0]:
            inset = cv2.resize(overlay, (260, 260))
            out[10:270, out.shape[1] - 270:out.shape[1] - 10] = inset
            cv2.rectangle(out, (out.shape[1] - 271, 9), (out.shape[1] - 9, 271), (255, 255, 255), 1)
        colour = (0, 170, 0) if ok else (0, 0, 220)
        cv2.rectangle(out, (0, out.shape[0] - 70), (out.shape[1], out.shape[0]), colour, -1)
        cv2.putText(out, "OK  nut and plate washer" if ok else "NOT OK", (12, out.shape[0] - 38),
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
                    configured, text = data
                    self.checkWasher.set(configured)
                    self.status.set(text)
                    self.startButton.config(state="normal")
                elif kind == "fatal":
                    self.status.set(data)
                    messagebox.showerror("Nut and washer check", data)
                elif kind == "busy":
                    self.status.set(data)
                elif kind == "shown":
                    self.show(*data)
                elif kind == "done":
                    okCount = sum(1 for r in self.summary if r)
                    self.status.set(f"Done: {len(self.summary)} picture(s): {okCount} OK, "
                                    f"{len(self.summary) - okCount} NOT OK" +
                                    (f"  -  marked pictures in {self.folder.get()}/nut_washer_results"
                                     if self.save.get() else ""))
                    self.startButton.config(state="normal")
                    self.stopButton.config(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def show(self, n, total, name, image, ok, reason, seconds):
        self.summary.append(ok)
        self.list.insert("end", f"{n:3}/{total}  {'OK    ' if ok else 'NOT OK'}  {name:28}  {seconds:5.2f} s  {reason}")
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
    NutWasherCheckWindow(window)
    window.mainloop()
