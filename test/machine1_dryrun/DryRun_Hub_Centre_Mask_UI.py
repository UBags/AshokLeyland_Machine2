"""
DryRun_Hub_Centre_Mask_UI - MobileSAM masks round the hub centre on hub pictures (state 5, hub + bottom bearing),
prompted by points on rings round the centre. A study window: nothing in production changes.

Prompts: one point every "Angle step" degrees (default 30, starting at 0 = to the right, going clockwise on the
picture) on each ring of "Ring radii" px (default 50 and 60) round the "Centre" (default the cap check's centre
`cap.center`, which sits on the hub bore: Machine 2 644,340). Defaults: 2 rings x 12 = 24 points, all positive, one
MobileSAM decoder call, 3 masks back (multimask). Centre, radii and step can be changed at any time (they apply from
the next picture).

  1. "Folder..." (every jpg / jpeg / png / bmp in it, by name) or "Picture...", then Start. Default folder:
     test/machine2_dryrun/hub_images.
  2. "Seconds between pictures" (1 - 10, starts at 3), changeable during the run; Stop ends it.
     "Gamma" (1.0 - 6.0 in steps of 0.2, starts at 1.0 = picture as taken): the picture is brightened before MobileSAM
     sees it, as the checks do (each pixel 255 x (v / 255) ^ (1 / gamma)); the marked picture shows it brightened.
     Applies from the next picture.
  3. "Find masks only around the centre" (ticked at start): the ring prompts above. Unticked: masks all over the
     picture: first a disc of "Disc radius" (default 300 px) round the centre is cut out (black outside it, thin
     white circle on the marked picture); MobileSAM's automatic mask generator then runs on that alone (a 32 x 32 grid
     of point prompts over the disc's square; its own filters at their defaults: predicted IoU >= 0.88, stability
     score >= 0.95, overlapping boxes merged at IoU 0.7, no minimum area); each mask is kept to the disc and one with
     less than half of it inside the disc (the black corners) is dropped. Each mask outlined in its own colour,
     listed largest first. Slower: several seconds per picture on the laptop GPU, minutes on a CPU. Applies from the
     next picture.
  4. "Masks shown" (around the centre only): All, or mask 1 / 2 / 3 (MobileSAM's order: small to large). Outline colours: mask 1 cyan,
     mask 2 magenta, mask 3 yellow; prompt points blue dots; centre a small blue cross. No text on the picture.
  5. Per picture and mask, in the list at the bottom and in the terminal: SAM score, area, centroid, the fitted
     ellipse's centre, mean radius and roundness (minor / major axis), the centroid's distance from the centre given,
     and the picture's time (embedding + decoder).
  6. "Save marked pictures" writes each marked picture to <folder>/hub_centre_mask_results/.

MobileSAM: models/MobileSAM/weights/mobile_sam.pt, or --sam <mobile_sam.pt>; loaded in the background when the window
opens (GPU if there is one). No Redis, PLC or camera needed.

Run from the project root:   python test/machine1_dryrun/DryRun_Hub_Centre_Mask_UI.py   [--sam <mobile_sam.pt>]
Without a window:            python test/machine1_dryrun/DryRun_Hub_Centre_Mask_UI.py --batch <folder or picture>
                             [--centre 644,340] [--radii 50,60] [--step 30] [--gamma 1.0] [--whole [--disc 300]] [--save]
                             (--whole: masks on the disc round the centre)
"""
import os
import pathlib
import sys
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("COSTHETA_PLC_VENDOR", "siemens" if HERE.name.startswith("machine2") else "allenbradley")

import cv2
import numpy as np

EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp")
VIEW_W, VIEW_H = 1120, 630
MASK_COLOURS = ((255, 255, 0), (255, 0, 255), (0, 255, 255))     # BGR: cyan, magenta, yellow
POINT_COLOUR = (255, 0, 0)                                        # BGR blue
AUTO_POINTS_PER_SIDE = 32                                         # the automatic generator's grid (its default)
DISC_RADIUS = 300                                                 # px; the disc cut round the centre (whole mode)
DEFAULT_SAM = ROOT / "models" / "MobileSAM" / "weights" / "mobile_sam.pt"


def argValue(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def defaultCentre():
    """The cap check's centre (on the hub bore), from this machine's properties."""
    try:
        from camera.CheckCap import CheckCap
        return float(CheckCap.CENTER[0]), float(CheckCap.CENTER[1])
    except Exception:
        return 644.0, 340.0


def promptPoints(centre, radii, stepDeg):
    """Points on each ring, every stepDeg degrees from 0 (right), clockwise on the picture (y down)."""
    angles = np.deg2rad(np.arange(0.0, 360.0 - 1e-9, float(stepDeg)))
    return np.array([[centre[0] + r * np.cos(a), centre[1] + r * np.sin(a)] for r in radii for a in angles],
                    dtype=np.float32)


def parseCentre(text):
    x, y = (float(v) for v in text.replace(" ", "").split(","))
    return x, y


def parseRadii(text):
    radii = [float(v) for v in text.replace(" ", "").split(",") if v]
    if not radii or any(r <= 0 for r in radii):
        raise ValueError("radii must be positive numbers, e.g. 50,60")
    return radii


def parseStep(text):
    step = float(text)
    if not 1.0 <= step <= 180.0:
        raise ValueError("angle step must be 1 - 180 degrees")
    return step


class HubCentreSegmenter:
    """One MobileSAM predictor; segment(picture, centre, radii, step) -> (masks, scores, points, seconds)."""

    def __init__(self, samPath=None, predictor=None, generator=None):
        self.samPath = str(samPath or DEFAULT_SAM)
        self.predictor = predictor
        self.generator = generator
        self.device = "given"

    def load(self):
        if self.predictor is not None:
            return
        import camera.ModelManager  # noqa: F401 - its import sets the timm / registry warning filters (as in production)
        import torch
        from mobile_sam import SamPredictor, sam_model_registry
        if not os.path.isfile(self.samPath):
            raise FileNotFoundError(f"MobileSAM weights not found at {self.samPath} (give --sam)")
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        model = sam_model_registry["vit_t"](checkpoint=self.samPath).to(self.device).eval()
        self.predictor = SamPredictor(model)

    def segment(self, picture, centre, radii, stepDeg):
        points = promptPoints(centre, radii, stepDeg)
        t0 = time.perf_counter()
        self.predictor.set_image(cv2.cvtColor(picture, cv2.COLOR_BGR2RGB))
        tEmbed = time.perf_counter() - t0
        try:
            import torch
            ctx = torch.inference_mode()
        except ImportError:                                  # stand-in predictor in tests
            import contextlib
            ctx = contextlib.nullcontext()
        with ctx:
            masks, scores, _ = self.predictor.predict(point_coords=points, point_labels=np.ones(len(points)),
                                                      multimask_output=True)
        return masks, np.asarray(scores, dtype=float), points, (tEmbed, time.perf_counter() - t0 - tEmbed)

    def segmentWhole(self, picture):
        """Masks all over the picture (MobileSAM's automatic mask generator, its default filters), largest first:
        (list of the generator's dicts, seconds)."""
        if self.generator is None:
            from mobile_sam import SamAutomaticMaskGenerator
            self.generator = SamAutomaticMaskGenerator(self.predictor.model, points_per_side=AUTO_POINTS_PER_SIDE)
        t0 = time.perf_counter()
        try:
            import torch
            ctx = torch.inference_mode()
        except ImportError:
            import contextlib
            ctx = contextlib.nullcontext()
        with ctx:
            found = self.generator.generate(cv2.cvtColor(picture, cv2.COLOR_BGR2RGB))
        return sorted(found, key=lambda d: -int(d["area"])), time.perf_counter() - t0


def measure(mask, centre):
    """Area, centroid, fitted ellipse (centre, mean radius, roundness) and the centroid's distance from centre."""
    m = mask.astype(np.uint8)
    area = int(m.sum())
    out = dict(area=area, centroid=None, ellipse=None, radius=None, roundness=None, fromCentre=None, contour=None)
    if area == 0:
        return out
    ys, xs = np.nonzero(m)
    cx, cy = float(xs.mean()), float(ys.mean())
    out.update(centroid=(cx, cy), fromCentre=float(np.hypot(cx - centre[0], cy - centre[1])))
    contours, _ = cv2.findContours(m * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if contours:
        c = max(contours, key=cv2.contourArea)
        out["contour"] = c
        if len(c) >= 5:
            (ex, ey), (a, b), _ = cv2.fitEllipse(c)
            major, minor = max(a, b) / 2.0, min(a, b) / 2.0
            if major > 0:
                out.update(ellipse=(float(ex), float(ey)), radius=(major + minor) / 2.0, roundness=minor / major)
    return out


def describe(i, score, meas):
    if meas["area"] == 0:
        return f"mask {i + 1}: score {score:.3f}, empty"
    text = (f"mask {i + 1}: score {score:.3f}, area {meas['area']}, centroid ({meas['centroid'][0]:.1f}, "
            f"{meas['centroid'][1]:.1f}) {meas['fromCentre']:.1f} px from the centre")
    if meas["ellipse"] is not None:
        text += (f", ellipse centre ({meas['ellipse'][0]:.1f}, {meas['ellipse'][1]:.1f}), radius {meas['radius']:.1f}, "
                 f"roundness {meas['roundness']:.3f}")
    return text


def draw(picture, masks, measures, points, centre, show="All"):
    """Outlines only (no text): masks in their colours, prompt points as blue dots, the centre as a blue cross."""
    marked = picture.copy()
    for i, meas in enumerate(measures):
        if meas["contour"] is not None and (show == "All" or show == f"Mask {i + 1}"):
            cv2.drawContours(marked, [meas["contour"]], -1, MASK_COLOURS[i % 3], 2)
    for x, y in points:
        cv2.circle(marked, (int(round(x)), int(round(y))), 3, POINT_COLOUR, -1)
    c = (int(round(centre[0])), int(round(centre[1])))
    cv2.line(marked, (c[0] - 6, c[1]), (c[0] + 6, c[1]), POINT_COLOUR, 1)
    cv2.line(marked, (c[0], c[1] - 6), (c[0], c[1] + 6), POINT_COLOUR, 1)
    return marked


def parseGamma(text):
    gamma = float(text)
    if not 1.0 <= gamma <= 6.0:
        raise ValueError("gamma must be 1.0 - 6.0")
    return gamma


def applyGamma(picture, gamma):
    """255 x (v / 255) ^ (1 / gamma), as the checks do: gamma > 1 brightens; 1.0 leaves the picture as it is."""
    if abs(gamma - 1.0) < 1e-9:
        return picture
    table = np.array([((i / 255.0) ** (1.0 / gamma)) * 255 for i in range(256)]).clip(0, 255).astype(np.uint8)
    return cv2.LUT(picture, table)


def distinctColours(n):
    """n well-separated BGR colours (hues spread by the golden ratio, full saturation)."""
    out = []
    for i in range(n):
        hsv = np.uint8([[[int((i * 0.618034 % 1.0) * 179), 255, 255 if i % 2 == 0 else 200]]])
        out.append(tuple(int(v) for v in cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0, 0]))
    return out


def drawWhole(picture, measures):
    """Outlines only (no text): every mask in its own colour."""
    marked = picture.copy()
    for meas, colour in zip(measures, distinctColours(len(measures))):
        if meas["contour"] is not None:
            cv2.drawContours(marked, [meas["contour"]], -1, colour, 2)
    return marked


def cutDisc(picture, centre, radius):
    """The square round the disc (clipped to the picture), black outside the disc: (crop, disc mask of the crop,
    (x0, y0) of the crop in the picture)."""
    h, w = picture.shape[:2]
    cx, cy, r = int(round(centre[0])), int(round(centre[1])), int(round(radius))
    x0, y0, x1, y1 = max(cx - r, 0), max(cy - r, 0), min(cx + r + 1, w), min(cy + r + 1, h)
    crop = picture[y0:y1, x0:x1].copy()
    disc = np.zeros(crop.shape[:2], np.uint8)
    cv2.circle(disc, (cx - x0, cy - y0), r, 1, -1)
    crop[disc == 0] = 0
    return crop, disc.astype(bool), (x0, y0)


def processWhole(segmenter, path, centre, gamma=1.0, discRadius=DISC_RADIUS):
    """Masks on the disc of discRadius round the centre -> (marked picture or None, list of lines). The disc is cut
    first (black outside it); the automatic generator runs on that alone; each mask is kept to the disc, and a mask
    with less than half of its area inside the disc (the black corners) is dropped."""
    picture = loadPicture(path)
    if picture is None:
        return None, [f"{path.name}: could not read the picture"]
    picture = applyGamma(picture, gamma)
    crop, disc, (x0, y0) = cutDisc(picture, centre, discRadius)
    found, secs = segmenter.segmentWhole(crop)
    kept, dropped = [], 0
    for d in found:
        seg = d["segmentation"].astype(bool)
        inside = seg & disc
        if inside.sum() * 2 < seg.sum() or not inside.any():
            dropped += 1
            continue
        full = np.zeros(picture.shape[:2], bool)
        full[y0:y0 + inside.shape[0], x0:x0 + inside.shape[1]] = inside
        kept.append((d, full))
    kept.sort(key=lambda t: -int(t[1].sum()))
    measures = [measure(full, centre) for _, full in kept]
    lines = [f"{path.name}: disc r {discRadius:g} round ({centre[0]:g}, {centre[1]:g}), {len(kept)} masks "
             f"(automatic generator, {AUTO_POINTS_PER_SIDE} x {AUTO_POINTS_PER_SIDE} points on the disc's square; "
             f"predicted IoU >= 0.88, stability >= 0.95, box NMS 0.7; {dropped} dropped: less than half inside the "
             f"disc); gamma {gamma:.1f}; time {secs:.2f} s"]
    for i, ((d, _), meas) in enumerate(zip(kept, measures)):
        text = describe(i, float(d.get("predicted_iou", 0.0)), meas).replace(": score", ": predicted IoU", 1)
        lines.append(f"    {text}, stability {float(d.get('stability_score', 0.0)):.3f}")
    marked = drawWhole(picture, measures)
    cv2.circle(marked, (int(round(centre[0])), int(round(centre[1]))), int(round(discRadius)), (255, 255, 255), 1)
    return marked, lines


def loadPicture(path):
    image = cv2.imread(str(path))
    if image is None:
        return None
    return cv2.resize(image, (1280, 720)) if image.shape[:2] != (720, 1280) else image


def picturesIn(target):
    target = pathlib.Path(target)
    if target.is_file():
        return [target]
    return sorted(p for p in target.iterdir() if p.suffix.lower() in EXTENSIONS)


def processOne(segmenter, path, centre, radii, stepDeg, show="All", gamma=1.0):
    """-> (marked picture or None, list of lines)."""
    picture = loadPicture(path)
    if picture is None:
        return None, [f"{path.name}: could not read the picture"]
    picture = applyGamma(picture, gamma)
    masks, scores, points, (tEmbed, tDecode) = segmenter.segment(picture, centre, radii, stepDeg)
    measures = [measure(m, centre) for m in masks]
    lines = [f"{path.name}: {len(points)} prompt points (rings {', '.join(f'{r:g}' for r in radii)} px, every "
             f"{stepDeg:g} deg round ({centre[0]:g}, {centre[1]:g})); gamma {gamma:.1f}; time {tEmbed + tDecode:.2f} s "
             f"(embedding {tEmbed:.2f} s, decoder {tDecode:.2f} s)"]
    lines += ["    " + describe(i, float(scores[i]), m) for i, m in enumerate(measures)]
    return draw(picture, masks, measures, points, centre, show), lines


def runBatch():
    target = argValue("--batch")
    centre = parseCentre(argValue("--centre")) if "--centre" in sys.argv else defaultCentre()
    radii = parseRadii(argValue("--radii", "50,60"))
    step = parseStep(argValue("--step", "30"))
    gamma = parseGamma(argValue("--gamma", "1.0"))
    segmenter = HubCentreSegmenter(argValue("--sam"))
    segmenter.load()
    print(f"MobileSAM on {segmenter.device}")
    files = picturesIn(target)
    outDir = (pathlib.Path(target) if pathlib.Path(target).is_dir() else pathlib.Path(target).parent) / "hub_centre_mask_results"
    for path in files:
        if "--whole" in sys.argv:
            marked, lines = processWhole(segmenter, path, centre, gamma, float(argValue("--disc", str(DISC_RADIUS))))
        else:
            marked, lines = processOne(segmenter, path, centre, radii, step, gamma=gamma)
        print("\n".join(lines), flush=True)
        if marked is not None and "--save" in sys.argv:
            outDir.mkdir(exist_ok=True)
            cv2.imwrite(str(outDir / (path.stem + "_masks.png")), marked)
    print(f"{len(files)} picture(s)")
    return 0


def runWindow():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from PIL import Image, ImageTk

    class App:
        def __init__(self, root, segmenter):
            self.root, self.segmenter = root, segmenter
            self.files, self.index, self.running, self.after = [], 0, False, None
            self.modelReady, self.modelError = False, None
            self.folder = HERE.parent / "machine2_dryrun" / "hub_images"
            root.title("Hub centre masks - MobileSAM, ring prompts")
            bar = ttk.Frame(root); bar.pack(fill="x", padx=6, pady=4)
            ttk.Button(bar, text="Folder...", command=self.chooseFolder).pack(side="left")
            ttk.Button(bar, text="Picture...", command=self.choosePicture).pack(side="left", padx=4)
            self.sourceText = tk.StringVar(master=root, value=str(self.folder))
            ttk.Label(bar, textvariable=self.sourceText).pack(side="left", padx=6)
            self.startButton = ttk.Button(bar, text="Start", command=self.start); self.startButton.pack(side="right")
            self.stopButton = ttk.Button(bar, text="Stop", command=self.stop, state="disabled")
            self.stopButton.pack(side="right", padx=4)

            bar2 = ttk.Frame(root); bar2.pack(fill="x", padx=6)
            c = defaultCentre()
            self.centreVar = tk.StringVar(master=root, value=f"{c[0]:g},{c[1]:g}")
            self.radiiVar = tk.StringVar(master=root, value="50,60")
            self.stepVar = tk.StringVar(master=root, value="30")
            self.secondsVar = tk.IntVar(master=root, value=3)
            self.gammaVar = tk.StringVar(master=root, value="1.0")
            self.centreOnlyVar = tk.BooleanVar(master=root, value=True)
            self.discVar = tk.StringVar(master=root, value=str(DISC_RADIUS))
            self.showVar = tk.StringVar(master=root, value="All")
            self.saveVar = tk.BooleanVar(master=root, value=False)
            for label, var, width in (("Centre x,y", self.centreVar, 10), ("Ring radii (px)", self.radiiVar, 10),
                                      ("Angle step (deg)", self.stepVar, 5)):
                ttk.Label(bar2, text=label).pack(side="left")
                ttk.Entry(bar2, textvariable=var, width=width).pack(side="left", padx=(2, 10))
            ttk.Label(bar2, text="Seconds between pictures").pack(side="left")
            ttk.Spinbox(bar2, from_=1, to=10, textvariable=self.secondsVar, width=4).pack(side="left", padx=(2, 10))
            ttk.Label(bar2, text="Gamma").pack(side="left")
            ttk.Spinbox(bar2, from_=1.0, to=6.0, increment=0.2, format="%.1f", textvariable=self.gammaVar,
                        width=5).pack(side="left", padx=(2, 10))
            ttk.Label(bar2, text="Masks shown").pack(side="left")
            ttk.Combobox(bar2, textvariable=self.showVar, values=["All", "Mask 1", "Mask 2", "Mask 3"], width=8,
                         state="readonly").pack(side="left", padx=(2, 10))
            ttk.Checkbutton(bar2, text="Save marked pictures", variable=self.saveVar).pack(side="left")
            bar3 = ttk.Frame(root); bar3.pack(fill="x", padx=6)
            ttk.Checkbutton(bar3, text="Find masks only around the centre", variable=self.centreOnlyVar).pack(side="left")
            ttk.Label(bar3, text="   Disc radius (unticked)").pack(side="left")
            ttk.Entry(bar3, textvariable=self.discVar, width=6).pack(side="left", padx=2)
            ttk.Label(bar3, text="   Around the centre: mask 1 cyan, mask 2 magenta, mask 3 yellow; prompt points blue "
                                 "dots; centre blue cross.  Unticked: masks on the disc, each its own colour (slower)."
                      ).pack(side="left")

            self.blank = ImageTk.PhotoImage(Image.new("RGB", (VIEW_W, VIEW_H)))
            self.canvas = tk.Label(root, image=self.blank, background="black"); self.canvas.pack(padx=6)
            self.status = tk.StringVar(master=root, value="Loading MobileSAM ...")
            ttk.Label(root, textvariable=self.status).pack(fill="x", padx=6)
            frame = ttk.Frame(root); frame.pack(fill="both", expand=True, padx=6, pady=4)
            self.listbox = tk.Listbox(frame, height=9, font=("Consolas", 10))
            ys = ttk.Scrollbar(frame, command=self.listbox.yview)
            xs = ttk.Scrollbar(frame, orient="horizontal", command=self.listbox.xview)
            self.listbox.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
            xs.pack(side="bottom", fill="x"); ys.pack(side="right", fill="y")
            self.listbox.pack(side="left", fill="both", expand=True)
            root.protocol("WM_DELETE_WINDOW", self.close)
            threading.Thread(target=self._load, daemon=True).start()
            root.after(200, self._pollLoad)

        def _load(self):                      # background thread: only sets flags (Tk is used on its own thread)
            try:
                self.segmenter.load()
            except Exception as e:
                self.modelError = str(e)
            self.modelReady = True

        def _pollLoad(self):
            if not self.modelReady:
                self.root.after(200, self._pollLoad); return
            self.status.set(f"MobileSAM error: {self.modelError}" if self.modelError
                            else f"MobileSAM ready ({self.segmenter.device}). Choose a folder or a picture, then Start.")

        def log(self, line):
            print(line, flush=True)
            self.listbox.insert("end", line); self.listbox.see("end")

        def chooseFolder(self):
            d = filedialog.askdirectory(initialdir=str(self.folder))
            if d:
                self.folder = pathlib.Path(d); self.sourceText.set(d)

        def choosePicture(self):
            f = filedialog.askopenfilename(initialdir=str(self.folder),
                                           filetypes=[("Pictures", "*.jpg *.jpeg *.png *.bmp"), ("All", "*.*")])
            if f:
                self.folder = pathlib.Path(f); self.sourceText.set(f)

        def settings(self):
            return parseCentre(self.centreVar.get()), parseRadii(self.radiiVar.get()), parseStep(self.stepVar.get())

        def gamma(self):
            try:
                return parseGamma(self.gammaVar.get())
            except Exception:
                self.log(f"Gamma '{self.gammaVar.get()}' not 1.0 - 6.0: 1.0 used"); return 1.0

        def start(self):
            if not self.modelReady or self.modelError:
                messagebox.showinfo("Hub centre masks", self.status.get()); return
            try:
                self.settings()
                self.files = picturesIn(self.folder)
            except Exception as e:
                messagebox.showerror("Hub centre masks", str(e)); return
            if not self.files:
                messagebox.showinfo("Hub centre masks", "No pictures there."); return
            self.index, self.running = 0, True
            self.startButton.configure(state="disabled"); self.stopButton.configure(state="normal")
            self.step()

        def stop(self):
            self.running = False
            if self.after:
                self.root.after_cancel(self.after); self.after = None
            self.finish(stopped=True)

        def close(self):
            self.running = False; self.root.destroy()

        def step(self):
            if not self.running:
                return
            if self.index >= len(self.files):
                self.finish(); return
            path = self.files[self.index]
            try:
                centre, radii, stepDeg = self.settings()
            except Exception as e:
                self.log(f"Settings: {e} - using the defaults"); centre, radii, stepDeg = defaultCentre(), [50.0, 60.0], 30.0
            self.status.set(f"{self.index + 1} / {len(self.files)}: {path.name} ...")
            self.root.update_idletasks()
            try:
                if self.centreOnlyVar.get():
                    marked, lines = processOne(self.segmenter, path, centre, radii, stepDeg, self.showVar.get(),
                                               self.gamma())
                else:
                    self.status.set(f"{self.index + 1} / {len(self.files)}: {path.name} - masks on the disc ...")
                    self.root.update_idletasks()
                    try:
                        disc = float(self.discVar.get()); assert disc > 0
                    except Exception:
                        self.log(f"Disc radius '{self.discVar.get()}' not a positive number: {DISC_RADIUS} used")
                        disc = float(DISC_RADIUS)
                    marked, lines = processWhole(self.segmenter, path, centre, self.gamma(), disc)
            except Exception as e:
                marked, lines = None, [f"{path.name}: error {e}"]
            for line in lines:
                self.log(line)
            if marked is not None:
                self.show(marked)
                if self.saveVar.get():
                    out = (self.folder if self.folder.is_dir() else self.folder.parent) / "hub_centre_mask_results"
                    out.mkdir(exist_ok=True)
                    cv2.imwrite(str(out / (path.stem + "_masks.png")), marked)
            self.status.set(f"{self.index + 1} / {len(self.files)}: {path.name}")
            self.index += 1
            if self.index >= len(self.files):
                self.finish(); return
            try:
                secs = max(1, min(10, int(self.secondsVar.get())))
            except Exception:
                secs = 3
            self.after = self.root.after(secs * 1000, self.step)

        def show(self, marked):
            rgb = cv2.cvtColor(marked, cv2.COLOR_BGR2RGB); h, w = rgb.shape[:2]; s = min(VIEW_W / w, VIEW_H / h)
            self.photo = ImageTk.PhotoImage(Image.fromarray(cv2.resize(rgb, (int(w * s), int(h * s)))))
            self.canvas.configure(image=self.photo)

        def finish(self, stopped=False):
            self.running = False
            self.startButton.configure(state="normal"); self.stopButton.configure(state="disabled")
            self.log(f"{'Stopped' if stopped else 'Done'}: {self.index} of {len(self.files)} picture(s)")

    root = tk.Tk()
    App(root, HubCentreSegmenter(argValue("--sam")))
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(runBatch() if "--batch" in sys.argv else runWindow())
