"""
Study_Legs_Profile_UI - the exact washer profile and the split pin leg profile in a window (Uddi, 2026-11-28; CHANGES
222; the steps are in _legs_profile.py). Study only: production and the properties file are not touched.

Per picture: the hub's holes and the MobileSAM washer mask with its fitted outline (Study_Washer_SAM, as the earlier leg
studies), the inner edge (_legs_length.ring), then _legs_profile: the outer outline scallop by scallop, the pin head on
its fixed axis, the leg profile on the washer (a) and beyond it (b).

Row 1: Folder... (OK\\ and NOT_OK\\ - or "Not OK" - sub-folders give the label; else the pictures in the folder), model,
seconds between pictures (changeable during the run), Save (marked pictures and a csv in <folder>/legs_profile_results/),
Start / Stop.
Row 2: the main values of _legs_profile (taken at Start, this window only): scallop shift +- px, scallop found share,
legs sector +- deg, region (b) start / end px past the outline, region (b) R-G floor, region (b) touch px.
Row 3 (CHANGES 223): (a) grey / R-G Otsu factors (0.8 / 1.2), gamma above / below the mean (1.5 / 0.6), (a) / (b) refine
Otsu factors (0.8 / 1.2).
Views (outlines only, no values on the pictures):
  - the whole picture;
  - the washer x3: scallops (dark blue; found / placed in the table and the text box), inner edge (yellow), pin head
    (white), leg profile (red);
  - the washer x3 with one mask, chosen in "Mask view" (redraws the picture shown): leg profile, (a) / (b) area before
    the grey refinement (CHANGES 223), (a) grey dark, (a) R-G
    bright, (a) strong R-G, (b) R-B bright, (b) R-G bright, (b) before the touch rule, rough legs; the regions (a) / (b)
    thin grey.
Table: one row per picture with its check time; click a row to see it again. Text box: the steps' values per picture
(every scallop's shift, found share and share on the rough legs; head; each Otsu value and the leg pixels).

  --auto <folder> [--model DOST|DOSTPLUS]   no window: every picture, its lines on the terminal
  --holes morphology, --sam <mobile_sam.pt>  as Study_Washer_SAM (morphology: the dark-blob hole finder, where the YOLO
                                             weights are missing)
Run from the project root (laptop: GPU and the YOLO weights):
    python test\\machine2_dryrun\\Study_Legs_Profile_UI.py [<folder>] [--model DOST]
"""
import csv
import pathlib
import sys
import threading
import time
import traceback

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import Study_Washer_SAM as S          # sets the paths, the vendor and cv2 / numpy                    # noqa: E402
S.STEPS["nut"] = S.STEPS["legs_study"] = False                     # the washer and its outline only (CHANGES 195)
import cv2                                                                                     # noqa: E402
import numpy as np                                                                             # noqa: E402
import _legs_length as LL                                                                      # noqa: E402
import _legs_profile as LP                                                                     # noqa: E402

NOT_OK_NAMES = ("NOT_OK", "Not OK", "NOT OK", "Not_OK", "not ok", "Not ok", "NotOK")
COLUMNS = ("file", "label", "holes", "scallops found / placed", "max |shift|", "head", "legs on washer px",
           "legs outside px", "Otsu a: grey / R-G / strong", "Otsu b: R-B / R-G", "time s", "note")
MASK_VIEWS = (("leg profile", "legs", (0, 0, 255)), ("(a) area before the grey refinement", "areaA", (0, 0, 255)),
              ("(b) area before the grey refinement", "areaB", (0, 0, 255)), ("(a) grey dark", "aDark", (255, 0, 0)),
              ("(a) R-G bright", "aRG", (0, 255, 255)), ("(a) strong R-G", "aStrong", (0, 165, 255)),
              ("(b) R-B bright", "bRB", (255, 0, 255)), ("(b) R-G bright", "bRG", (0, 255, 0)),
              ("(b) before the touch rule", "bRaw", (0, 255, 255)), ("rough legs", "rough", (0, 128, 255)))
ZOOM_HALF, ZOOM_SCALE = 75, 3
SCALLOP_COLOUR = (139, 0, 0)          # dark blue (Uddi 02:51; found / placed: the table and the text box)
INNER_COLOUR = (0, 255, 255)          # yellow
FULL_W, FULL_H = 640, 360
PANEL = 450


def picturesIn(folder):
    folder = pathlib.Path(folder)
    items = []
    if (folder / "OK").is_dir():
        items += [(p, "OK") for p in sorted((folder / "OK").iterdir()) if p.suffix.lower() in S.EXT]
    for name in NOT_OK_NAMES:
        if (folder / name).is_dir():
            items += [(p, "NOT OK") for p in sorted((folder / name).iterdir()) if p.suffix.lower() in S.EXT]
            break
    if not items:
        items = [(p, "") for p in sorted(folder.iterdir()) if p.suffix.lower() in S.EXT]
    return items


def setValues(v: dict):
    for k, val in v.items():
        setattr(LP, k, val)


def checkPicture(path, label, model, models):
    """Holes, washer mask and fitted outline, inner edge, then _legs_profile. dict for the table / views."""
    pred, FindHubHoles, SP = models
    t0 = time.perf_counter()
    r, g, im = S.processPicture(path, label, model, pred, FindHubHoles, SP)
    out = dict(name=pathlib.Path(path).name, label=label, im=im, row=r, note="", res=None)
    if g is None or g.get("fo") is None:
        out["note"] = r.get("fail") or "no washer outline"
        out["seconds"] = time.perf_counter() - t0
        return out
    x0, y0 = g["x0"], g["y0"]
    crop = im[y0:y0 + 2 * S.CROP_HALF, x0:x0 + 2 * S.CROP_HALF].copy()
    g["crop"] = crop
    R = LL.ring(g, None)
    if not R.get("found"):
        out["note"] = f"inner edge not found ({R.get('reason')})"
        out["seconds"] = time.perf_counter() - t0
        return out
    res = LP.profile(crop, tuple(g["cc"]), g["fo"], R["rInner"], R["centroid"])
    out.update(res=res, crop=crop, x0=x0, y0=y0, cc=tuple(g["cc"]), rInner=R["rInner"], centroid=R["centroid"],
               seconds=time.perf_counter() - t0, holes=f"({r.get('holes_cx', 0):.0f}, {r.get('holes_cy', 0):.0f})")
    if not res["head"]["found"]:
        out["note"] = "no pin head (no split pin?)"
    return out


def _poly(img, pts, off, scale, col, closed=True):
    p = np.round((np.asarray(pts, float) - off) * scale + (scale - 1) / 2).astype(np.int32)
    cv2.polylines(img, [p], closed, col, 1, cv2.LINE_AA)


def _contours(img, mask, off, scale, col):
    if mask is None:
        return
    for c in cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)[0]:
        _poly(img, c[:, 0, :], off, scale, col)


def _outlines(img, out, off, scale, legs=True):
    res = out["res"]
    o = res["outline"]
    for sg in o["segs"]:
        _poly(img, o["pts"][sg["idx"]], off, scale, SCALLOP_COLOUR, closed=False)
    cx, cy = out["centroid"]
    cv2.circle(img, (int(round((cx - off[0]) * scale)), int(round((cy - off[1]) * scale))), int(round(out["rInner"] * scale)),
               INNER_COLOUR, 1, cv2.LINE_AA)
    if res["head"]["found"]:
        _contours(img, res["head"]["mask"], off, scale, (255, 255, 255))
    if legs and res["legs"] is not None:
        _contours(img, res["legs"]["legs"], off, scale, (0, 0, 255))


def fullPicture(out):
    img = out["im"].copy() if out.get("im") is not None else np.zeros((720, 1280, 3), np.uint8)
    if out.get("res") is not None:
        # the outlines drawn on the crop's place in the whole picture
        sub = img[out["y0"]:out["y0"] + 2 * S.CROP_HALF, out["x0"]:out["x0"] + 2 * S.CROP_HALF]
        _outlines(sub, out, np.array([0.0, 0.0]), 1)
    return img


def zoomPicture(out, maskKey=None):
    if out.get("res") is None:
        return None
    crop = out["crop"]
    cx, cy = out["cc"]
    x0 = int(max(0, min(crop.shape[1] - 2 * ZOOM_HALF, round(cx) - ZOOM_HALF)))
    y0 = int(max(0, min(crop.shape[0] - 2 * ZOOM_HALF, round(cy) - ZOOM_HALF)))
    z = cv2.resize(crop[y0:y0 + 2 * ZOOM_HALF, x0:x0 + 2 * ZOOM_HALF], None, fx=ZOOM_SCALE, fy=ZOOM_SCALE,
                   interpolation=cv2.INTER_CUBIC)
    off = np.array([x0, y0], float)
    if maskKey is None:
        _outlines(z, out, off, ZOOM_SCALE)
        return z
    L = out["res"]["legs"]
    if L is not None:
        _contours(z, L["regA"], off, ZOOM_SCALE, (110, 110, 110))
        _contours(z, L["regB"], off, ZOOM_SCALE, (110, 110, 110))
    key, col = maskKey
    m = out["res"]["rough"] if key == "rough" else (L[key] if L is not None else None)
    _contours(z, m, off, ZOOM_SCALE, col)
    return z


def tableValues(out):
    res = out.get("res")
    if res is None:
        return (out["name"], out["label"], "-", "-", "-", "-", "-", "-", "-", "-", f"{out.get('seconds', 0):.2f}", out["note"])
    o, h, L = res["outline"], res["head"], res["legs"]
    placed = sum(not s["found"] for s in o["segs"])
    shiftMax = max(abs(s["shift"]) for s in o["segs"])
    return (out["name"], out["label"], out["holes"], f"{len(o['segs']) - placed} / {placed}", f"{shiftMax:.1f}",
            f"{h['end']:.0f} deg" if h["found"] else "-", L["pxA"] if L else "-", L["pxB"] if L else "-",
            f"{L['tA_grey']:.0f} / {L['tA_rg']:.0f} / {L['tA_strong']:.0f}" if L else "-",
            f"{L['tB_rb']:.0f} / {L['tB_rg']:.0f}" if L else "-", f"{out['seconds']:.2f}", out["note"])


def detailLines(out):
    lines = [f"{out['name']} ({out['label'] or '-'}): {out['seconds']:.2f} s"]
    res = out.get("res")
    if res is None:
        return lines + [f"   {out['note']}"]
    lines.append("   " + LP.summaryLine(res))
    for i, s in enumerate(res["outline"]["segs"]):
        lines.append(f"   scallop {i:2d} deg {int(s['idx'][0]):3d} - {int(s['idx'][-1]):3d}: shift {s['shift']:+.1f} px, "
                     f"share {s['share']:.2f}, contrast {s['contrast']:.0f}, on the rough legs {s['obscured']:.2f} -> "
                     f"{'found' if s['found'] else 'placed from the adjacent corners'}")
    return lines


def csvRow(out):
    v = tableValues(out)
    return dict(zip(COLUMNS, v))


def writeCsv(path, outs):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for o in outs:
            w.writerow(csvRow(o))


def auto(folder, model):
    models = S.setUp()[:3]
    items = picturesIn(folder)
    outs = []
    for p, lab in items:
        try:
            out = checkPicture(p, lab, model, models)
        except Exception as e:
            traceback.print_exc()
            out = dict(name=p.name, label=lab, note=f"exception: {e}", res=None, seconds=0.0)
        outs.append(out)
        for line in detailLines(out):
            print("PROFILE " + line if line.startswith(out["name"]) else line, flush=True)
    t = [o["seconds"] for o in outs if o.get("res") is not None]
    withHead = sum(1 for o in outs if o.get("res") is not None and o["res"]["head"]["found"])
    print(f"SUMMARY {len(outs)} pictures: profile {len(t)}, pin head {withHead}, time {min(t):.2f} - {max(t):.2f} s "
          f"(median {float(np.median(t)):.2f})" if t else f"SUMMARY {len(outs)} pictures: no profile", flush=True)


# ---------------------------------------------------------------------------------------------------- window
def window(folder):
    from PySide6.QtCore import Qt, QObject, Signal
    from PySide6.QtGui import QImage, QPixmap
    from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout,
                                   QLabel, QMainWindow, QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QTableWidget,
                                   QTableWidgetItem, QVBoxLayout, QWidget)

    def toPixmap(bgr, w, h):
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        img = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format_RGB888).copy()
        return QPixmap.fromImage(img).scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    class Bridge(QObject):
        picture = Signal(object)
        status = Signal(str)
        finished = Signal(str)

    class Window(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Study_Legs_Profile_UI - washer profile and split pin leg profile")
            self.folder = pathlib.Path(folder) if folder else None
            self.outs = []
            self.models = None
            self.stopFlag = threading.Event()
            self.bridge = Bridge()
            self.bridge.picture.connect(self.onPicture)
            self.bridge.status.connect(lambda s: self.statusLine.setText(s))
            self.bridge.finished.connect(self.onFinished)

            row1 = QHBoxLayout()
            b = QPushButton("Folder...")
            b.clicked.connect(self.chooseFolder)
            self.folderLabel = QLabel(str(self.folder) if self.folder else "(no folder)")
            self.model = QComboBox()
            self.model.addItems(["DOST", "DOSTPLUS"])
            self.model.setCurrentText(S.arg("--model", "DOST").upper())
            self.seconds = QSpinBox()
            self.seconds.setRange(0, 10)
            self.seconds.setValue(1)
            self.save = QCheckBox("Save")
            self.startButton = QPushButton("Start")
            self.startButton.clicked.connect(self.start)
            self.stopButton = QPushButton("Stop")
            self.stopButton.setEnabled(False)
            self.stopButton.clicked.connect(self.stopFlag.set)
            for wdg in (b, self.folderLabel, QLabel("Model"), self.model, QLabel("Seconds"), self.seconds, self.save,
                        self.startButton, self.stopButton):
                row1.addWidget(wdg)
            row1.addStretch(1)

            row2 = QHBoxLayout()
            self.spins = {}

            def spin(key, title, low, high, step, dec=0, row=None):
                row = row2 if row is None else row
                s = QDoubleSpinBox() if dec else QSpinBox()
                if dec:
                    s.setDecimals(dec)
                s.setRange(low, high)
                s.setSingleStep(step)
                s.setValue(getattr(LP, key))
                row.addWidget(QLabel(title))
                row.addWidget(s)
                self.spins[key] = s
            spin("SHIFT", "Scallop shift +- px", 1, 30, 1, 1)
            spin("GOOD_SHARE", "Scallop found share", 0.3, 1.0, 0.05, 2)
            spin("SECTOR", "Legs sector +- deg", 20, 90, 5)
            spin("GAP_B", "(b) from px", 0, 15, 1)
            spin("OUT", "(b) to px", 10, 60, 5)
            spin("RG_FLOOR_B", "(b) R-G floor", 0, 40, 1)
            spin("TOUCH_B", "(b) touch px", 1, 20, 1)
            row2.addStretch(1)
            row2b = QHBoxLayout()
            spin("A_GREY_FACTOR", "(a) grey Otsu x", 0.3, 1.5, 0.05, 2, row2b)
            spin("A_RG_FACTOR", "(a) R-G Otsu x", 0.5, 2.0, 0.05, 2, row2b)
            spin("GAMMA_HIGH", "Gamma above mean", 1.0, 3.0, 0.1, 2, row2b)
            spin("GAMMA_LOW", "Gamma below mean", 0.2, 1.0, 0.05, 2, row2b)
            spin("A_REFINE_FACTOR", "(a) refine Otsu x", 0.3, 1.5, 0.05, 2, row2b)
            spin("B_REFINE_FACTOR", "(b) refine Otsu x", 0.5, 2.0, 0.05, 2, row2b)
            row2b.addStretch(1)

            row3 = QHBoxLayout()
            self.maskView = QComboBox()
            self.maskView.addItems([m[0] for m in MASK_VIEWS])
            self.maskView.currentIndexChanged.connect(lambda _: self.showRow(self.table.currentRow()))
            row3.addWidget(QLabel("Mask view"))
            row3.addWidget(self.maskView)
            row3.addStretch(1)

            views = QHBoxLayout()
            self.fullView, self.zoomView, self.maskPanel = QLabel(), QLabel(), QLabel()
            for v, (w, h) in ((self.fullView, (FULL_W, FULL_H)), (self.zoomView, (PANEL, PANEL)), (self.maskPanel, (PANEL, PANEL))):
                v.setFixedSize(w, h)
                v.setPixmap(toPixmap(np.zeros((h, w, 3), np.uint8), w, h))
                views.addWidget(v)

            self.table = QTableWidget(0, len(COLUMNS))
            self.table.setHorizontalHeaderLabels(COLUMNS)
            self.table.cellClicked.connect(lambda r, c: self.showRow(r))
            self.table.currentCellChanged.connect(lambda r, c, pr, pc: self.showRow(r))
            self.text = QPlainTextEdit()
            self.text.setReadOnly(True)
            split = QSplitter(Qt.Vertical)
            split.addWidget(self.table)
            split.addWidget(self.text)
            self.statusLine = QLabel("")

            lay = QVBoxLayout()
            for l_ in (row1, row2, row2b, row3, views):
                lay.addLayout(l_)
            lay.addWidget(split, 1)
            lay.addWidget(self.statusLine)
            c = QWidget()
            c.setLayout(lay)
            self.setCentralWidget(c)

        def chooseFolder(self):
            chosen = QFileDialog.getExistingDirectory(self, "Folder of split pin pictures", str(self.folder or HERE))
            if chosen:
                self.folder = pathlib.Path(chosen)
                self.folderLabel.setText(chosen)

        def log(self, line):
            self.text.appendPlainText(line)

        def start(self):
            if self.folder is None or not self.folder.is_dir():
                self.statusLine.setText("Choose a folder first")
                return
            items = picturesIn(self.folder)
            if not items:
                self.statusLine.setText(f"No pictures in {self.folder}")
                return
            values = {k: (float(s.value()) if isinstance(s, QDoubleSpinBox) else int(s.value())) for k, s in self.spins.items()}
            setValues(values)
            self.outs.clear()
            self.table.setRowCount(0)
            self.text.clear()
            self.log(f"{len(items)} pictures in {self.folder}; values {values}")
            self.stopFlag.clear()
            self.startButton.setEnabled(False)
            self.stopButton.setEnabled(True)
            threading.Thread(target=self.run, args=(items, self.model.currentText(), self.save.isChecked()), daemon=True).start()

        def run(self, items, model, save):
            try:
                if self.models is None:
                    self.bridge.status.emit("Loading the hole finder and MobileSAM ...")
                    self.models = S.setUp()[:3]
                resultsDir = self.folder / "legs_profile_results"
                outs = []
                for i, (p, lab) in enumerate(items):
                    if self.stopFlag.is_set():
                        break
                    self.bridge.status.emit(f"{i + 1} / {len(items)}: {p.name} ... checking")
                    try:
                        out = checkPicture(p, lab, model, self.models)
                    except Exception as e:
                        traceback.print_exc()
                        out = dict(name=p.name, label=lab, note=f"exception: {e}", res=None, seconds=0.0, im=None)
                    outs.append(out)
                    if save:
                        resultsDir.mkdir(exist_ok=True)
                        cv2.imwrite(str(resultsDir / f"{p.stem}_profile.jpg"), fullPicture(out))
                        z = zoomPicture(out)
                        if z is not None:
                            cv2.imwrite(str(resultsDir / f"{p.stem}_washer.jpg"), z)
                    self.bridge.picture.emit(out)
                    end = time.monotonic() + self.seconds.value()
                    while time.monotonic() < end and not self.stopFlag.is_set():
                        time.sleep(0.05)
                text = ""
                if save and outs:
                    path = resultsDir / f"legs_profile_{time.strftime('%Y%m%d_%H%M%S')}.csv"
                    writeCsv(path, outs)
                    text = f"written: {path}"
                t = [o["seconds"] for o in outs if o.get("res") is not None]
                head = sum(1 for o in outs if o.get("res") is not None and o["res"]["head"]["found"])
                summary = (f"{len(outs)} pictures: profile {len(t)}, pin head {head}; time {min(t):.2f} - {max(t):.2f} s "
                           f"(median {float(np.median(t)):.2f})") if t else f"{len(outs)} pictures: no profile"
                self.bridge.finished.emit(("Stopped. " if self.stopFlag.is_set() else "") + summary + ("\n" + text if text else ""))
            except Exception as e:
                traceback.print_exc()
                self.bridge.finished.emit(f"Run ended by an exception: {e}")

        def onPicture(self, out):
            self.outs.append(out)
            r = self.table.rowCount()
            self.table.insertRow(r)
            for c, v in enumerate(tableValues(out)):
                self.table.setItem(r, c, QTableWidgetItem(str(v)))
            self.table.scrollToBottom()
            self.showRow(r)
            for line in detailLines(out):
                self.log(line)

        def showRow(self, r):
            if not (0 <= r < len(self.outs)):
                return
            out = self.outs[r]
            self.fullView.setPixmap(toPixmap(fullPicture(out), FULL_W, FULL_H))
            z = zoomPicture(out)
            blank = np.zeros((PANEL, PANEL, 3), np.uint8)
            self.zoomView.setPixmap(toPixmap(z if z is not None else blank, PANEL, PANEL))
            _, key, col = MASK_VIEWS[self.maskView.currentIndex()]
            m = zoomPicture(out, (key, col))
            self.maskPanel.setPixmap(toPixmap(m if m is not None else blank, PANEL, PANEL))
            self.statusLine.setText(f"{out['name']} ({out['label'] or '-'}): {out.get('seconds', 0):.2f} s {out['note']}")

        def onFinished(self, text):
            self.log("")
            self.log(text)
            self.startButton.setEnabled(True)
            self.stopButton.setEnabled(False)
            self.statusLine.setText("Stopped" if self.stopFlag.is_set() else "Finished")

    app = QApplication.instance() or QApplication(sys.argv)
    try:
        from frontend.QtMessages import installQtMessageHandler   # Qt's own messages to the log, not the C stderr
        installQtMessageHandler()
    except Exception:
        pass
    w = Window()
    w.resize(1600, 1000)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    folderArg = S.arg("--auto")
    if folderArg:
        auto(pathlib.Path(folderArg), S.arg("--model", "DOST").upper())
    else:
        positional = [a for a in sys.argv[1:] if not a.startswith("--")]
        flagsWithValue = {S.arg(f) for f in ("--model", "--holes", "--sam", "--limit", "--out")}
        folderPos = next((a for a in positional if a not in flagsWithValue), None)
        window(folderPos)
