"""
Study_SplitPin_Steps_UI - the split pin legs step by step, with the steps' values and choice boxes to do or bypass a
step (Uddi, 2026-11-28 12:10; CHANGES 229). Study only: production and the properties file are not touched.

Screen (CHANGES 230): top 25 % the values and choice boxes; bottom 75 % split into the text (left 25 %) and the four
pictures (right 75 %).
  top left      the original picture round the washer (+- VIEW_HALF px round its centre, x3: the whole split pin in view);
  top right     the steps over the split pin washer (region A);
  bottom left   the steps over the plate washer / seal beyond it (region B);
  bottom right  the result: the red masks of A and B on the whole picture ("Final zoomed" shows the washer only).

Load (or a new picture) runs, without stopping, everything up to the two 120 deg regions: the hub's holes and the
MobileSAM washer outline (Study_Washer_SAM), the inner edge (_legs_length.ring), the outer outline scallop by scallop
and the pin head on its fixed axis (_legs_profile, CHANGES 222); the legs axis = the head's end + 180 deg.
  region A: +- sector deg round the legs axis, inner edge .. outer outline (on the split pin washer);
  region B: +- sector deg, outline + from px .. outline + to px (on the plate washer / seal).
Then "Next Step" goes through, for A and then for B:
  0  the region (light fill on the original);
  1  channel 1 as a grey picture inside the region (its source chosen in the box; A: grey, dark = mask);
  2  optional bilateral filter (check box; d, sigmaColor, sigmaSpace; utils/ImageNormalisationWithMask.
     bilateral_filter_masked - only the region's own pixels);
  3  Otsu over the region x the factor (%, 50 - 150) -> mask 1 (blue);
  4  channel 2 as a grey picture (A: clamped 2R - G - B);
  5  optional bilateral filter;
  6  Otsu x the factor -> mask 2 (green), shown over mask 1 (blue) on the original;
  7  mask 3 = mask 1 AND mask 2 (each "in the AND" box can leave one out), painted red on the original;
and last the result - since CHANGES 231 the A and B masks joined first: a white copy of the washer area, the masks
black, an open with a 5 x 5 kernel (on white a closing of the black: masks broken at the scallops' edge join), the black
= the red mask shown ("Join the masks" box, kernel spin box). "Back" goes one step back, "Run all" to the result, "Reset" to step 0. Changing a value or a box
redraws the step shown. The text box: each step's values (Otsu value, factor, threshold, pixels).
Channel sources: grey (dark = 1), grey (bright = 1), 2R - G - B (bright = 1), max(R - B, 0) (bright = 1), R (bright = 1),
R - G (bright = 1); 2R - G - B, R - B and R - G clamped to 0 .. 255.
Defaults: A - grey (dark), 2R - G - B; B - max(R - B, 0), 2R - G - B (the last choices of CHANGES 223 - 225); sector +- 60
deg, B from 1 to 30 px; since CHANGES 231 (Uddi 12:56), for A and B: bilateral 1 on (d 9, sigmaColor 75, sigmaSpace 50),
Otsu 1 100 %; bilateral 2 on (d 9, sigmaColor 75, sigmaSpace 75), Otsu 2 70 %.

Run from the project root (laptop: GPU and the YOLO weights):
    python test\\machine2_dryrun\\Study_SplitPin_Steps_UI.py [<folder>] [--model DOST] [--holes morphology] [--sam <pt>]
"""
import pathlib
import sys
import threading
import traceback

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import Study_Washer_SAM as S          # sets the paths, the vendor and cv2 / numpy                    # noqa: E402
S.STEPS["nut"] = S.STEPS["legs_study"] = False
import cv2                                                                                     # noqa: E402
import numpy as np                                                                             # noqa: E402
import _legs_length as LL                                                                      # noqa: E402
import _legs_profile as LP                                                                     # noqa: E402
from Study_Legs_Profile_UI import picturesIn                                                   # noqa: E402

try:
    from utils.ImageNormalisationWithMask import bilateral_filter_masked
except Exception:                                   # the same steps, if the project module cannot be imported
    def bilateral_filter_masked(image, mask, d=9, sigmaColor=75.0, sigmaSpace=75.0):
        g = image.copy()
        if mask.sum() > 0:
            g[~mask] = int(np.mean(image[mask]))
        f = cv2.bilateralFilter(g, d, sigmaColor, sigmaSpace)
        out = image.copy()
        out[mask] = f[mask]
        return out

SOURCES = ("grey (dark = 1)", "grey (bright = 1)", "2R - G - B (bright = 1)", "max(R - B, 0) (bright = 1)",
           "R (bright = 1)", "R - G (bright = 1)")
STEP_NAMES = ("region", "channel 1", "bilateral 1", "Otsu 1 -> mask 1", "channel 2", "bilateral 2",
              "Otsu 2 -> mask 2", "mask 3 = mask 1 AND mask 2")
MASK1_COLOUR, MASK2_COLOUR, MASK3_COLOUR = (255, 90, 0), (0, 220, 0), (0, 0, 255)
REGION_COLOUR = (255, 255, 0)
SCALLOP_COLOUR, INNER_COLOUR = (139, 0, 0), (0, 255, 255)
# Uddi's values of 2026-11-28 12:56 (CHANGES 231), the same for regions A and B
DEFAULTS = dict(bil1=True, d1=9, sc1=75.0, ss1=50.0, pct1=100.0, bil2=True, d2=9, sc2=75.0, ss2=75.0, pct2=70.0)
JOIN_KERNEL = 5                      # px: the open on the white copy with the masks black (CHANGES 231)
VIEW_HALF = 85                       # px round the washer centre in the three washer panels (head and straight legs in view)


def view(img, geo):
    cx, cy = (int(round(v)) for v in geo["cc"])
    h, w = img.shape[:2]
    x0, y0 = max(0, min(w - 2 * VIEW_HALF, cx - VIEW_HALF)), max(0, min(h - 2 * VIEW_HALF, cy - VIEW_HALF))
    return cv2.resize(img[y0:y0 + 2 * VIEW_HALF, x0:x0 + 2 * VIEW_HALF], None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)


def channel(crop: np.ndarray, source: str):
    """(uint8 grey picture, dark = 1?) for a source of SOURCES."""
    B, G, R = (crop[..., k].astype(float) for k in range(3))
    if source.startswith("grey"):
        return cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), "dark" in source
    if source.startswith("2R"):
        v = 2 * R - G - B
    elif source.startswith("max(R - B"):
        v = R - B
    elif source.startswith("R - G"):
        v = R - G
    else:
        v = R
    return np.clip(v, 0, 255).astype(np.uint8), False


def otsu(vals) -> float:
    v = np.asarray(vals, np.uint8).reshape(-1, 1)
    if v.size == 0:
        return 0.0
    t, _ = cv2.threshold(v, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return float(t)


def geometry(path, label, model, models):
    """Everything up to the two regions' axis: dict, or dict(note=...) when a step fails."""
    pred, FindHubHoles, SP = models
    r, g, im = S.processPicture(path, label, model, pred, FindHubHoles, SP)
    out = dict(name=pathlib.Path(path).name, label=label, im=im, note="")
    if g is None or g.get("fo") is None:
        out["note"] = r.get("fail") or "no washer outline"
        return out
    x0, y0 = g["x0"], g["y0"]
    crop = im[y0:y0 + 2 * S.CROP_HALF, x0:x0 + 2 * S.CROP_HALF].copy()
    g["crop"] = crop
    R = LL.ring(g, None)
    if not R.get("found"):
        out["note"] = f"inner edge not found ({R.get('reason')})"
        return out
    cc = tuple(g["cc"])
    head0 = LP.findHead(crop, cc, g["fo"])
    rough = LP.roughLegs(crop, cc, g["fo"], R["rInner"], R["centroid"], (head0["end"] + 180) % 360) if head0["found"] else None
    outline = LP.refineOutline(crop, cc, g["fo"], rough)
    head = LP.findHead(crop, cc, outline["fr"])
    out.update(crop=crop, x0=x0, y0=y0, cc=cc, rInner=R["rInner"], centroid=R["centroid"], outline=outline, head=head,
               axis=(head["end"] + 180) % 360)
    if not head["found"]:
        out["note"] = "no pin head (no split pin?) - the legs axis is taken opposite the end tried first"
    return out


def regions(geo, sector, fromPx, toPx):
    crop, cc = geo["crop"], geo["cc"]
    h, w = crop.shape[:2]
    yy, xx = np.mgrid[:h, :w]
    d = np.hypot(xx - cc[0], yy - cc[1])
    a = np.degrees(np.arctan2(yy - cc[1], xx - cc[0])) % 360
    FR = geo["outline"]["fr"][np.clip(a.astype(int), 0, 359)]
    rel = np.abs(((a - geo["axis"]) + 180) % 360 - 180)
    sec = rel <= sector
    din = np.hypot(xx - geo["centroid"][0], yy - geo["centroid"][1])
    return sec & (din >= geo["rInner"]) & (d <= FR), sec & (d > FR + fromPx) & (d <= FR + toPx)


def runRegion(crop, region, p):
    """Steps 1 - 7 on one region with the values p; every intermediate picture and value."""
    res = dict(region=region, px=int(region.sum()))
    for k in (1, 2):
        g, dark = channel(crop, p[f"src{k}"])
        res[f"chan{k}"] = g
        if p[f"bil{k}"] and region.any():
            g = bilateral_filter_masked(g, region, int(p[f"d{k}"]), float(p[f"sc{k}"]), float(p[f"ss{k}"]))
        res[f"filt{k}"] = g
        t0 = otsu(g[region]) if region.any() else 0.0
        t = t0 * p[f"pct{k}"] / 100.0
        m = region & ((g <= t) if dark else (g > t))
        res.update({f"otsu{k}": t0, f"thr{k}": t, f"mask{k}": m, f"dark{k}": dark})
    use1, use2 = p["and1"], p["and2"]
    m3 = (res["mask1"] if use1 else region) & (res["mask2"] if use2 else region)
    if not use1 and not use2:
        m3 = np.zeros_like(region)
    res["mask3"] = m3
    return res


def joinMasks(mask: np.ndarray, k: int) -> np.ndarray:
    """Uddi 12:56: a white copy of the washer area, the masks' pixels black, an open with a k x k kernel - on white that
    is a closing of the black, so masks broken at the scallops' edge join; the black is the final mask."""
    white = np.full(mask.shape, 255, np.uint8)
    white[mask] = 0
    opened = cv2.morphologyEx(white, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
    return opened == 0


def fill(img, mask, colour, alpha=0.45):
    if mask is None or not mask.any():
        return img
    over = img.copy()
    over[mask] = colour
    out = cv2.addWeighted(over, alpha, img, 1 - alpha, 0)
    for c in cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)[0]:
        cv2.polylines(out, [c], True, colour, 1)
    return out


def greyInRegion(crop, grey, region):
    """The grey picture inside the region, the original dimmed outside it."""
    out = (crop * 0.35).astype(np.uint8)
    out[region] = cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR)[region]
    return out


def outlines(img, geo):
    o = geo["outline"]
    cv2.polylines(img, [np.round(o["pts"]).astype(np.int32)], True, SCALLOP_COLOUR, 1, cv2.LINE_AA)
    cx, cy = geo["centroid"]
    cv2.circle(img, (int(round(cx)), int(round(cy))), int(round(geo["rInner"])), INNER_COLOUR, 1, cv2.LINE_AA)
    if geo["head"]["found"]:
        for c in cv2.findContours(geo["head"]["mask"].astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)[0]:
            cv2.polylines(img, [c], True, (255, 255, 255), 1)
    return img


def stepPicture(crop, res, step):
    if step <= 0:
        return fill(crop, res["region"], REGION_COLOUR, 0.25)
    if step in (1, 2, 4, 5):
        k = 1 if step < 3 else 2
        return greyInRegion(crop, res[f"chan{k}"] if step in (1, 4) else res[f"filt{k}"], res["region"])
    if step == 3:
        return fill(crop, res["mask1"], MASK1_COLOUR)
    if step == 6:
        return fill(fill(crop, res["mask1"], MASK1_COLOUR, 0.35), res["mask2"], MASK2_COLOUR, 0.35)
    return fill(crop, res["mask3"], MASK3_COLOUR, 0.55)


def stepLines(tag, res, step, p):
    if step < 0:
        return []
    lines = []
    for s in range(0, min(step, 7) + 1):
        if s == 0:
            lines.append(f"{tag}0 region: {res['px']} px")
        elif s in (1, 4):
            k = 1 if s == 1 else 2
            lines.append(f"{tag}{s} channel {k}: {p[f'src{k}']}")
        elif s in (2, 5):
            k = 1 if s == 2 else 2
            lines.append(f"{tag}{s} bilateral {k}: " + (f"d {p[f'd{k}']}, sigmaColor {p[f'sc{k}']}, sigmaSpace {p[f'ss{k}']}"
                                                         if p[f"bil{k}"] else "bypassed"))
        elif s in (3, 6):
            k = 1 if s == 3 else 2
            lines.append(f"{tag}{s} Otsu {k}: {res[f'otsu{k}']:.0f} x {p[f'pct{k}']:.2f} % = {res[f'thr{k}']:.1f} -> mask {k} "
                         f"({'dark' if res[f'dark{k}'] else 'bright'} = 1) {int(res[f'mask{k}'].sum())} px")
        else:
            used = " AND ".join(n for n, u in (("mask 1", p["and1"]), ("mask 2", p["and2"])) if u) or "nothing"
            lines.append(f"{tag}7 mask 3 = {used}: {int(res['mask3'].sum())} px")
    return lines


# ---------------------------------------------------------------------------------------------------- window
def main():
    from PySide6.QtCore import Qt, QObject, Signal
    from PySide6.QtGui import QImage, QPixmap
    from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QGridLayout,
                                   QGroupBox, QHBoxLayout, QLabel, QMainWindow, QPlainTextEdit, QPushButton, QSizePolicy,
                                   QSpinBox, QVBoxLayout, QWidget)

    class Picture(QLabel):
        """A picture that keeps its aspect when the panel is resized."""
        def __init__(self, title):
            super().__init__()
            self.setAlignment(Qt.AlignCenter)
            self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
            self.setStyleSheet("background: black;")
            self.setToolTip(title)
            self.bgr = None

        def show_(self, bgr):
            self.bgr = bgr
            self._draw()

        def _draw(self):
            if self.bgr is None:
                self.clear()
                return
            rgb = cv2.cvtColor(self.bgr, cv2.COLOR_BGR2RGB)
            img = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format_RGB888).copy()
            self.setPixmap(QPixmap.fromImage(img).scaled(max(self.width(), 10), max(self.height(), 10), Qt.KeepAspectRatio,
                                                         Qt.SmoothTransformation))

        def resizeEvent(self, e):
            self._draw()
            super().resizeEvent(e)

    class Bridge(QObject):
        loaded = Signal(object)
        status = Signal(str)

    class Window(QMainWindow):
        def __init__(self, folder):
            super().__init__()
            self.setWindowTitle("Study_SplitPin_Steps_UI - the split pin legs step by step")
            self.items, self.geo, self.models, self.step, self.busy = [], None, None, -1, False
            self.bridge = Bridge()
            self.bridge.loaded.connect(self.onLoaded)
            self.bridge.status.connect(lambda s: self.status.setText(s))
            central = QWidget()
            lay = QVBoxLayout(central)

            # ---- top 25 %: values and choice boxes
            top = QWidget()
            tl = QVBoxLayout(top)
            tl.setContentsMargins(0, 0, 0, 0)
            row = QHBoxLayout()
            fb = QPushButton("Folder...")
            fb.clicked.connect(self.chooseFolder)
            self.pictureBox = QComboBox()
            self.pictureBox.setMinimumWidth(320)
            self.pictureBox.currentIndexChanged.connect(lambda i: self.load())
            prev, nxt = QPushButton("< Picture"), QPushButton("Picture >")
            prev.clicked.connect(lambda: self.pictureBox.setCurrentIndex(max(self.pictureBox.currentIndex() - 1, 0)))
            nxt.clicked.connect(lambda: self.pictureBox.setCurrentIndex(min(self.pictureBox.currentIndex() + 1, self.pictureBox.count() - 1)))
            self.model = QComboBox()
            self.model.addItems(["DOST", "DOSTPLUS"])
            self.model.setCurrentText(S.arg("--model", "DOST").upper())
            self.model.currentIndexChanged.connect(lambda i: self.load())
            self.nextButton, back, runAll, reset = QPushButton("Next Step"), QPushButton("Back"), QPushButton("Run all"), QPushButton("Reset")
            self.nextButton.clicked.connect(lambda: self.goto(self.step + 1))
            back.clicked.connect(lambda: self.goto(self.step - 1))
            runAll.clicked.connect(lambda: self.goto(16))
            reset.clicked.connect(lambda: self.goto(0))
            self.outlinesBox = QCheckBox("Outlines on original")
            self.outlinesBox.setChecked(True)
            self.finalZoom = QCheckBox("Final zoomed")
            for wdg in (self.outlinesBox, self.finalZoom):
                wdg.stateChanged.connect(lambda _: self.redraw())
            self.stepLabel = QLabel("")
            for wdg in (fb, self.pictureBox, prev, nxt, QLabel("Model"), self.model, self.nextButton, back, runAll, reset,
                        self.outlinesBox, self.finalZoom, self.stepLabel):
                row.addWidget(wdg)
            row.addStretch(1)
            tl.addLayout(row)
            self.p = {"A": {}, "B": {}}
            self.widgets = {"A": {}, "B": {}}
            boxes = QHBoxLayout()
            boxes.addWidget(self.regionBox("A", "Split pin washer (region A)", ("grey (dark = 1)", "2R - G - B (bright = 1)")))
            boxes.addWidget(self.regionBox("B", "Plate washer / seal (region B)", ("max(R - B, 0) (bright = 1)", "2R - G - B (bright = 1)")))
            row3 = QHBoxLayout()
            self.joinBox = QCheckBox("Join the masks before the result (white copy, masks black, open)")
            self.joinBox.setChecked(True)
            self.joinBox.stateChanged.connect(lambda _: self.redraw())
            self.joinKernel = QSpinBox()
            self.joinKernel.setRange(1, 15)
            self.joinKernel.setValue(JOIN_KERNEL)
            self.joinKernel.valueChanged.connect(lambda _: self.redraw())
            for wdg in (self.joinBox, QLabel("kernel px"), self.joinKernel):
                row3.addWidget(wdg)
            row3.addStretch(1)
            tl.addLayout(row3)
            tl.addLayout(boxes)

            # ---- four pictures
            mid = QWidget()
            grid = QGridLayout(mid)
            grid.setContentsMargins(0, 0, 0, 0)
            self.pics = [Picture(t) for t in ("Original", "Split pin washer (A)", "Plate washer / seal (B)", "Result")]
            for i, pic in enumerate(self.pics):
                grid.addWidget(pic, i // 2, i % 2)

            # ---- the text (left of the pictures since CHANGES 230)
            self.text = QPlainTextEdit()
            self.text.setReadOnly(True)
            self.status = QLabel("")
            bottom = QWidget()
            bl = QVBoxLayout(bottom)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.addWidget(self.text, 1)
            bl.addWidget(self.status)
            # top 25 %: the values; bottom 75 %: the text (left 25 %) and the pictures (right 75 %) - CHANGES 230
            lower = QWidget()
            ll = QHBoxLayout(lower)
            ll.setContentsMargins(0, 0, 0, 0)
            ll.addWidget(bottom, 25)
            ll.addWidget(mid, 75)
            lay.addWidget(top, 25)
            lay.addWidget(lower, 75)
            self.setCentralWidget(central)
            if folder:
                self.setFolder(pathlib.Path(folder))

        def regionBox(self, tag, title, sources):
            box = QGroupBox(title)
            g = QGridLayout(box)
            w = self.widgets[tag]

            def spin(key, low, high, value, step=1.0, dec=0):
                s = QDoubleSpinBox() if dec else QSpinBox()
                if dec:
                    s.setDecimals(dec)
                s.setRange(low, high)
                s.setSingleStep(step)
                s.setValue(value)
                s.valueChanged.connect(lambda _: self.redraw(recompute=True))
                w[key] = s
                return s

            def check(key, text, value):
                c = QCheckBox(text)
                c.setChecked(value)
                c.stateChanged.connect(lambda _: self.redraw(recompute=True))
                w[key] = c
                return c

            g.addWidget(QLabel("Sector +- deg"), 0, 0)
            g.addWidget(spin("sector", 10, 120, 60), 0, 1)
            if tag == "B":
                g.addWidget(QLabel("From px past outline"), 0, 2)
                g.addWidget(spin("from", 0, 20, 1), 0, 3)
                g.addWidget(QLabel("To px"), 0, 4)
                g.addWidget(spin("to", 5, 80, 30), 0, 5)
            for k in (1, 2):
                r = k
                src = QComboBox()
                src.addItems(SOURCES)
                src.setCurrentText(sources[k - 1])
                src.currentIndexChanged.connect(lambda _: self.redraw(recompute=True))
                w[f"src{k}"] = src
                g.addWidget(QLabel(f"Channel {k}"), r, 0)
                g.addWidget(src, r, 1)
                g.addWidget(check(f"bil{k}", "Bilateral", DEFAULTS[f"bil{k}"]), r, 2)
                g.addWidget(QLabel("d"), r, 3)
                g.addWidget(spin(f"d{k}", -1, 25, DEFAULTS[f"d{k}"]), r, 4)
                g.addWidget(QLabel("sigmaColor"), r, 5)
                g.addWidget(spin(f"sc{k}", 1, 300, DEFAULTS[f"sc{k}"], 5, 1), r, 6)
                g.addWidget(QLabel("sigmaSpace"), r, 7)
                g.addWidget(spin(f"ss{k}", 1, 300, DEFAULTS[f"ss{k}"], 5, 1), r, 8)
                g.addWidget(QLabel("Otsu %"), r, 9)
                g.addWidget(spin(f"pct{k}", 50, 150, DEFAULTS[f"pct{k}"], 0.05, 2), r, 10)
                g.addWidget(check(f"and{k}", f"Mask {k} in the AND", True), r, 11)
            return box

        def params(self, tag):
            p = {}
            for k, wdg in self.widgets[tag].items():
                if isinstance(wdg, QComboBox):
                    p[k] = wdg.currentText()
                elif isinstance(wdg, QCheckBox):
                    p[k] = wdg.isChecked()
                else:
                    p[k] = wdg.value()
            return p

        def chooseFolder(self):
            chosen = QFileDialog.getExistingDirectory(self, "Folder of split pin pictures", str(HERE))
            if chosen:
                self.setFolder(pathlib.Path(chosen))

        def setFolder(self, folder):
            self.items = picturesIn(folder)
            self.pictureBox.blockSignals(True)
            self.pictureBox.clear()
            self.pictureBox.addItems([f"{lab or '-'}  {p.name}" for p, lab in self.items])
            self.pictureBox.blockSignals(False)
            self.status.setText(f"{len(self.items)} pictures in {folder}")
            if self.items:
                self.load()

        def load(self):
            i = self.pictureBox.currentIndex()
            if self.busy or not (0 <= i < len(self.items)):
                return
            self.busy = True
            self.nextButton.setEnabled(False)
            p, lab = self.items[i]
            self.status.setText(f"{p.name}: holes, washer, outline, head ...")
            threading.Thread(target=self._load, args=(p, lab, self.model.currentText()), daemon=True).start()

        def _load(self, p, lab, model):
            try:
                if self.models is None:
                    self.bridge.status.emit("Loading the hole finder and MobileSAM ...")
                    self.models = S.setUp()[:3]
                self.bridge.loaded.emit(geometry(p, lab, model, self.models))
            except Exception as e:
                traceback.print_exc()
                self.bridge.loaded.emit(dict(name=p.name, label=lab, im=None, note=f"exception: {e}"))

        def onLoaded(self, geo):
            self.busy = False
            self.nextButton.setEnabled(True)
            self.geo = geo
            self.res = None
            self.status.setText(f"{geo['name']} ({geo['label'] or '-'}) {geo['note']}")
            self.goto(0)

        def compute(self):
            geo = self.geo
            pa, pb = self.params("A"), self.params("B")
            regA, _ = regions(geo, pa["sector"], 0, 0)
            _, regB = regions(geo, pb["sector"], pb["from"], pb["to"])
            self.res = dict(A=runRegion(geo["crop"], regA, pa), B=runRegion(geo["crop"], regB, pb), pa=pa, pb=pb)

        def goto(self, step):
            if self.geo is None:
                return
            self.step = int(np.clip(step, 0, 16))
            self.redraw(recompute=True)

        def redraw(self, recompute=False):
            geo = self.geo
            if geo is None:
                return
            if geo.get("crop") is None:
                for pic in self.pics:
                    pic.show_(None)
                if geo.get("im") is not None:
                    self.pics[0].show_(geo["im"])
                self.text.setPlainText(geo["note"])
                return
            if recompute or self.res is None:
                self.compute()
            crop = geo["crop"]
            orig = crop.copy()
            if self.outlinesBox.isChecked():
                outlines(orig, geo)
            self.pics[0].show_(view(orig, geo))
            sA = min(self.step, 7)                       # steps 0 - 7: A; 8 - 15: B; 16: the result
            sB = self.step - 8 if self.step >= 8 else -1
            self.pics[1].show_(view(stepPicture(crop, self.res["A"], sA), geo))
            self.pics[2].show_(view(stepPicture(crop, self.res["B"], sB) if sB >= 0 else crop, geo))
            if self.step >= 16:
                red = self.res["A"]["mask3"] | self.res["B"]["mask3"]
                self.redBefore = int(red.sum())
                if self.joinBox.isChecked():
                    red = joinMasks(red, self.joinKernel.value())
                self.redAfter = int(red.sum())
                if self.finalZoom.isChecked():
                    final = view(fill(crop, red, MASK3_COLOUR, 0.55), geo)
                else:
                    full = np.zeros(geo["im"].shape[:2], bool)
                    full[geo["y0"]:geo["y0"] + crop.shape[0], geo["x0"]:geo["x0"] + crop.shape[1]] = red
                    final = fill(geo["im"], full, MASK3_COLOUR, 0.55)
                self.pics[3].show_(final)
            else:
                self.pics[3].show_(geo["im"])
            name = (f"A{sA} {STEP_NAMES[sA]}" if self.step < 8 else f"B{sB} {STEP_NAMES[sB]}" if self.step < 16 else "result")
            self.stepLabel.setText(f"Step: {name}")
            lines = [f"{geo['name']} ({geo['label'] or '-'})  head {'at %.0f deg' % geo['head']['end'] if geo['head']['found'] else 'not found'},"
                     f" legs axis {geo['axis']:.0f} deg; scallops placed from neighbours "
                     f"{sum(not s['found'] for s in geo['outline']['segs'])} of {len(geo['outline']['segs'])}"]
            lines += stepLines("A", self.res["A"], sA, self.res["pa"])
            lines += stepLines("B", self.res["B"], sB, self.res["pb"])
            if self.step >= 16:
                lines.append(f"result: A | B {self.redBefore} px; " + (f"joined (white copy, masks black, open {self.joinKernel.value()} x "
                             f"{self.joinKernel.value()}): {self.redAfter} px red" if self.joinBox.isChecked() else "not joined"))
            self.text.setPlainText("\n".join(lines))

    app = QApplication.instance() or QApplication(sys.argv)
    try:
        from frontend.QtMessages import installQtMessageHandler   # Qt's own messages to the log, not the C stderr
        installQtMessageHandler()
    except Exception:
        pass
    flagsWithValue = {S.arg(f) for f in ("--model", "--holes", "--sam")}
    folder = next((a for a in sys.argv[1:] if not a.startswith("--") and a not in flagsWithValue), None)
    w = Window(folder)
    w.resize(1600, 1000)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
