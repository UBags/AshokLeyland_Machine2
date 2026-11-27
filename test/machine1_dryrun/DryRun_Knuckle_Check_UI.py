"""
DryRun_Knuckle_Check_UI - the real knuckle check (CheckKnuckle, state 3) on a folder of pictures or one picture, with
this machine's settings, shown one picture at a time: which knuckle it is (DOST / DOSTPLUS, LHS / RHS), or neither.

  1. "Folder..." (every jpg / jpeg / png / bmp in it, by name) or "Picture..." (one file), then Start.
  2. Each picture is checked with the QR code of each of the four knuckles (DOST LHS, DOST RHS, DOSTPLUS LHS,
     DOSTPLUS RHS). The one it passes with is the knuckle shown; none = NEITHER, with the reasons the check gave.
     (More than one would be a fault of the settings: shown as AMBIGUOUS.)
  3. "Seconds between pictures" (1 - 10, starts at 3) can be changed at any time; the new value applies from the
     picture on screen. Stop ends the run; Start again begins from the first picture.
  4. Markings drawn by this window, with the mean grey level of each region:
       Machine 2 (side.by.difference = True):
         contours A (left) / A' (right)  - green = the darker one, when darker by more than side.difference.threshold
                                            (it gives the side); white otherwise
         side boxes D1 (left) / D2 (right) on the bar - yellow = the darker one (the arm), when darker by more than
                                            side.difference.threshold; white otherwise
       Machine 1: rectangle A (and its alternate) like B-E; side boxes yellow when below the side threshold (arm)
       B, C, D, E (thin = alternate) - green below common.check.threshold, red at or above
       S2 / S3 (next to the bosses) - cyan = DOSTPLUS (at or above the model threshold), magenta = DOST
       banner - the knuckle found (or NEITHER / AMBIGUOUS) and, for NEITHER, the check's reasons
  5. At the end: count per knuckle and the list of results. "Save marked pictures" writes each marked picture to
     <folder>/knuckle_check_results/.

Settings: vision.<vendor>.knuckle.* in ApplicationConfiguration.properties (vendor from the folder: machine1 =
allenbradley, machine2 = siemens). No models, Redis, PLC or camera needed: the check's log messages are collected by
this window (not sent to the logging server).

Run from the project root:   python test/machine1_dryrun/DryRun_Knuckle_Check_UI.py
"""
import os
import pathlib
import sys
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

import camera.CheckKnuckle as CheckKnuckleModule
from camera.CheckKnuckle import CheckKnuckle as K

QR = {("DOST", "LHS"): "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$",
      ("DOST", "RHS"): "7204838$400102VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNURH$SNPL-MAT$",
      ("DOSTPLUS", "LHS"): "8201206$400112VA1C$22881$15.10.2026$C5211702$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$",
      ("DOSTPLUS", "RHS"): "8201206$400102VA1C$22881$15.10.2026$C5211702$MEK$40cR4-B$1$SPDL ASSY-KNURH$SNPL-MAT$"}
EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp")
VIEW_W, VIEW_H = 1120, 630
GREEN, RED, YELLOW, WHITE, CYAN, MAGENTA = (0, 220, 0), (0, 0, 255), (0, 255, 255), (255, 255, 255), (255, 255, 0), (255, 0, 255)

# The check's own log messages, collected per call (errors give the reason for NOT OK)
_messages = []
CheckKnuckleModule.logBoth = lambda level, source, message, *args, **kwargs: _messages.append((level, message))


def identify(image):
    """{(model, side): (ok, reason)} for the four QR codes."""
    verdicts = {}
    for knuckle, code in QR.items():
        _messages.clear()
        ok = bool(K.checkKnuckle(image.copy(), componentQRCode=code)[1])
        errors = [m.replace("CheckKnuckle: ", "") for level, m in _messages if level in ("logError", "logCritical")]
        verdicts[knuckle] = (ok, errors[0] if errors else "")
    return verdicts


def _text(img, s, org, colour, scale=0.5):
    cv2.putText(img, s, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, s, org, cv2.FONT_HERSHEY_SIMPLEX, scale, colour, 1, cv2.LINE_AA)


def _rect_mean(g, x, y, w, h):
    return float(g[y:y + h, x:x + w].mean())


def _circle_mean(g, cx, cy, r):
    m = np.zeros(g.shape, np.uint8); cv2.circle(m, (cx, cy), r, 255, -1); return float(g[m == 255].mean())


def _poly_mean(g, pts):
    m = np.zeros(g.shape, np.uint8); cv2.fillPoly(m, [np.array(pts, np.int32).reshape(-1, 2)], 255)
    return float(g[m == 255].mean())


def decorate(image, verdicts, name):
    """The picture with the regions, their values and the result banner."""
    out = image.copy(); g = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY); T = K.COMMON_CHECK_THRESHOLD

    def rect(letter, alt, thick):
        x, y, w, h = [getattr(K, f"COMMON_CHECK_{letter}{alt}_{k}") for k in ("X", "Y", "WIDTH", "HEIGHT")]
        if w <= 0 or h <= 0:
            return
        v = _rect_mean(g, x, y, w, h); c = GREEN if v < T else RED
        cv2.rectangle(out, (x, y), (x + w, y + h), c, thick)
        _text(out, f"{letter}{chr(39) if alt else ''} {v:.0f}", (x, y - 4 if not alt else y + h + 13), c, 0.45)

    def circle(letter, alt, thick):
        x, y, r = [getattr(K, f"COMMON_CHECK_{letter}{alt}_{k}") for k in ("CENTER_X", "CENTER_Y", "RADIUS")]
        if r <= 0:
            return
        v = _circle_mean(g, x, y, r); c = GREEN if v < T else RED
        cv2.circle(out, (x, y), r, c, thick)
        _text(out, f"{letter}{chr(39) if alt else ''} {v:.0f}", (x - 18, y - r - 4 if not alt else y + r + 13), c, 0.45)

    letters = "BCDE" if K.SIDE_BY_DIFFERENCE else "ABCDE"
    for letter in letters:
        for alt, thick in (("", 2), ("_ALT", 1)):
            (rect if letter in "ABE" else circle)(letter, alt, thick)

    boxes = {S: [getattr(K, f"{S}_CROP_{k}") for k in ("X", "Y", "WIDTH", "HEIGHT")] for S in ("LHS", "RHS")}
    if K.SIDE_BY_DIFFERENCE:
        a, aAlt = _poly_mean(g, K.COMMON_CHECK_A_POLYGON_POINTS), _poly_mean(g, K.COMMON_CHECK_A_ALT_POLYGON_POINTS)
        for pts, label, v, other in ((K.COMMON_CHECK_A_POLYGON_POINTS, "A", a, aAlt), (K.COMMON_CHECK_A_ALT_POLYGON_POINTS, "A'", aAlt, a)):
            P = np.array(pts, np.int32).reshape(-1, 2); c = GREEN if other - v > K.SIDE_DIFFERENCE_THRESHOLD else WHITE
            cv2.polylines(out, [P], True, c, 2); _text(out, f"{label} {v:.0f}", (int(P[:, 0].min()), int(P[:, 1].min()) - 5), c)
        means = {S: _rect_mean(g, *b) for S, b in boxes.items()}
        for S, label in (("LHS", "D1"), ("RHS", "D2")):
            x, y, w, h = boxes[S]; other = means["RHS" if S == "LHS" else "LHS"]; c = YELLOW if other - means[S] > K.SIDE_DIFFERENCE_THRESHOLD else WHITE
            cv2.rectangle(out, (x, y), (x + w, y + h), c, 2); _text(out, f"{label} {means[S]:.0f}", (x - 6, y - 6), c)
    else:
        for S, b in boxes.items():
            x, y, w, h = b; v = float(K._crop_and_analyze(image, x, y, w, h)); c = YELLOW if v < K.LHS_RHS_DARKNESS_THRESHOLD else WHITE
            cv2.rectangle(out, (x, y), (x + w, y + h), c, 2); _text(out, f"{S} {v:.0f}", (x - 6, y - 6), c)

    for label, P in (("S2", K.STEP2_POLYGON_POINTS), ("S3", K.STEP3_POLYGON_POINTS)):
        v = float(K._analyze_polygon_region(image, P)); c = CYAN if v >= K.COMPONENT_IDENTIFICATION_DARKNESS_THRESHOLD else MAGENTA
        cv2.polylines(out, [P.reshape(-1, 1, 2)], True, c, 2)
        tag = f"{label} {v:.0f} {'DOSTPLUS' if c == CYAN else 'DOST'}"
        x = int(P[:, 0].min()) - 8 - 9 * len(tag) if label == "S2" else int(P[:, 0].max()) + 8      # outside the bosses
        _text(out, tag, (max(x, 2), int(P[:, 1].mean()) + 5), c, 0.5)

    found = [f"{m} {s}" for (m, s), (ok, _) in verdicts.items() if ok]
    verdict = found[0] if len(found) == 1 else ("NEITHER" if not found else "AMBIGUOUS: " + ", ".join(found))
    reasons = [] if found else list(dict.fromkeys(r for _, r in verdicts.values() if r))
    lines = [f"{name}:  {verdict}"] + reasons[:3]
    height = 34 + 22 * (len(lines) - 1)
    cv2.rectangle(out, (0, 0), (out.shape[1], height), (0, 0, 0), -1)
    _text(out, lines[0], (10, 25), GREEN if len(found) == 1 else RED, 0.75)
    for i, reason in enumerate(lines[1:]):
        _text(out, reason[:150], (10, 50 + 22 * i), WHITE, 0.5)
    return out, verdict, reasons


class KnuckleCheckWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.files, self.index, self.running, self.timer = [], 0, False, None
        self.results, self.outFolder, self.photo = [], None, None
        root.title(f"Knuckle check - {os.environ['COSTHETA_PLC_VENDOR']} settings")

        bar = ttk.Frame(root); bar.pack(fill="x", padx=6, pady=4)
        ttk.Button(bar, text="Folder...", command=self.chooseFolder).pack(side="left")
        ttk.Button(bar, text="Picture...", command=self.choosePicture).pack(side="left", padx=4)
        self.source = tk.StringVar(master=root, value="(nothing chosen)")
        ttk.Label(bar, textvariable=self.source, width=60).pack(side="left", padx=6)
        ttk.Label(bar, text="Seconds between pictures").pack(side="left", padx=(12, 2))
        self.seconds = tk.StringVar(master=root, value="3")
        ttk.Combobox(bar, textvariable=self.seconds, values=[str(i) for i in range(1, 11)], state="readonly",
                     width=4).pack(side="left")
        self.save = tk.BooleanVar(master=root, value=False)
        ttk.Checkbutton(bar, text="Save marked pictures", variable=self.save).pack(side="left", padx=10)
        self.startButton = ttk.Button(bar, text="Start", command=self.start); self.startButton.pack(side="left")
        self.stopButton = ttk.Button(bar, text="Stop", command=self.stop, state="disabled"); self.stopButton.pack(side="left", padx=4)

        self.canvas = tk.Label(root, background="black", width=VIEW_W, height=VIEW_H)
        self.canvas.pack(padx=6)
        self.status = tk.StringVar(master=root, value="Choose a folder or a picture, then Start.")
        ttk.Label(root, textvariable=self.status).pack(fill="x", padx=6)
        listFrame = ttk.Frame(root); listFrame.pack(fill="both", expand=True, padx=6, pady=4)
        self.listbox = tk.Listbox(listFrame, height=8, font=("Consolas", 10))
        scroll = ttk.Scrollbar(listFrame, command=self.listbox.yview); self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.pack(side="left", fill="both", expand=True); scroll.pack(side="right", fill="y")
        root.protocol("WM_DELETE_WINDOW", self.close)

    def chooseFolder(self):
        folder = filedialog.askdirectory(title="Folder with knuckle pictures", parent=self.root)
        if folder:
            self.files = sorted(str(p) for p in pathlib.Path(folder).iterdir() if p.suffix.lower() in EXTENSIONS)
            self.outFolder = pathlib.Path(folder) / "knuckle_check_results"
            self.source.set(f"{folder}  ({len(self.files)} pictures)")

    def choosePicture(self):
        f = filedialog.askopenfilename(title="Knuckle picture", parent=self.root,
                                       filetypes=[("Pictures", " ".join("*" + e for e in EXTENSIONS)), ("All files", "*.*")])
        if f:
            self.files = [f]; self.outFolder = pathlib.Path(f).parent / "knuckle_check_results"; self.source.set(f)

    def start(self):
        if not self.files:
            messagebox.showinfo("Knuckle check", "Choose a folder or a picture first.", parent=self.root); return
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
        if self.index >= len(self.files):
            self.finish(); return
        path = self.files[self.index]; name = os.path.basename(path)
        image = cv2.imread(path)
        if image is None:
            verdict, reasons, marked = "UNREADABLE", ["the file could not be read as a picture"], None
        else:
            t0 = time.perf_counter()
            verdicts = identify(image)
            marked, verdict, reasons = decorate(image, verdicts, name)
            elapsed = time.perf_counter() - t0
        self.results.append((name, verdict, reasons))
        self.listbox.insert("end", f"{name:45s} {verdict}" + (f"   ({reasons[0]})" if reasons else ""))
        self.listbox.see("end")
        if marked is not None:
            self.show(marked)
            if self.save.get():
                self.outFolder.mkdir(exist_ok=True); cv2.imwrite(str(self.outFolder / name), marked)
        self.status.set(f"Picture {self.index + 1} of {len(self.files)}: {name} - {verdict}"
                        + (f"  ({elapsed * 1000:.0f} ms)" if image is not None else ""))
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
        for _, verdict, _ in self.results:
            key = verdict if verdict in {f"{m} {s}" for m, s in QR} else ("AMBIGUOUS" if verdict.startswith("AMBIGUOUS") else verdict)
            counts[key] = counts.get(key, 0) + 1
        summary = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "nothing checked"
        self.status.set(f"{'Stopped' if stopped else 'Done'} after {len(self.results)} of {len(self.files)} pictures - {summary}"
                        + (f"; marked pictures in {self.outFolder}" if self.save.get() and self.results else ""))


if __name__ == "__main__":
    root = tk.Tk()
    KnuckleCheckWindow(root)
    root.mainloop()
