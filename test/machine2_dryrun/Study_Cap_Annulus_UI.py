"""
Study_Cap_Annulus_UI - the cap annulus measure (_cap_annulus.py, CHANGES 203 / 204) in a window, study only.

Folder..., model (DOST / DOSTPLUS / from the file name), cap mask method ("YOLO boxes (cap check)" = the production cap
mask's steps on a disc round the hub centre, default; "24 points"), seconds between pictures (1 - 10; changeable during
the run), gamma of the picture given to MobileSAM (24 points only), Save; Start / Stop. Second row (CHANGES 207, set at
Start): YOLO confidence, MobileSAM stability and predicted IoU minimums, the box overlap above which masks are merged
(the properties values at start; they change the masks and the cap mask chosen from them, in this window only), and
the share of a mask's area that must be in the hole circle for the third panel; since CHANGES 208 also YOLO's NMS IoU and
maximum detections and MobileSAM's 3 masks per box, with "Production values" and "Over-detect" (Uddi's values of
2026-11-27 09:09) buttons. The table's boxes/disc/sam/pass/merged column shows where masks are lost: YOLO boxes, boxes
centred on the disc, MobileSAM masks (1 or 3 per box), masks past the IoU / stability filters, masks left after merging. Each picture: the whole picture and a 2x view of the cap,
both with outlines only - hole circle and hub centre (blue), MobileSAM prompt points (cyan), cap mask outline (green),
fitted ellipse (yellow), the 12 diameters (grey). Third panel (CHANGES 206): every MobileSAM mask with at least half its
area inside the hole circle (boxes method: the masks after MobileSAM's IoU / stability filters and merging - the ones
the cap mask is chosen from; points method: its one mask), cut to the circle and coloured, the largest painted first
and each smaller one over it; their areas in the text box. No values on the pictures: the table (one row per picture, its check
time included) and the text box below (steps 1 - 2 values, hub centre, ellipse, every diameter's lengths L+ / L- and
ratio, for the ellipse and for the mask outline). Click a row to see that picture again. At the end the summary in the
text box and annulus_<time>.csv (+ the marked pictures with Save) in <folder>/cap_annulus_results/.

Run from the project root:
    python test\\machine2_dryrun\\Study_Cap_Annulus_UI.py [<folder>] [--sam <mobile_sam.pt>] [--holes morphology]
"""
import argparse
import pathlib
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _cap_annulus as A                                                     # noqa: E402

import cv2                                                                    # noqa: E402
import numpy as np                                                            # noqa: E402
from PySide6.QtCore import Qt, QObject, Signal                                # noqa: E402
from PySide6.QtGui import QImage, QPixmap, QFont                              # noqa: E402
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout,  # noqa
                               QHeaderView, QLabel, QMainWindow, QPlainTextEdit, QPushButton, QSpinBox, QSplitter,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

COLUMNS = ("file", "cap", "R-B", "bright", "hub", "R", "boxes/disc/sam/pass/merged", "masks", "ell - hub", "score ellipse",
           "score mask", "time s", "note")
FULL_W, FULL_H = 640, 360
PANEL = 360                         # side of the 2x view and of the masks view
ZOOM_HALF = 170                     # px round the hub centre in the 2x view


def toPixmap(bgr: np.ndarray, w: int, h: int) -> QPixmap:
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    img = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format_RGB888).copy()
    return QPixmap.fromImage(img).scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def zoomOf(marked: np.ndarray, res: dict) -> np.ndarray:
    """The region round the hub centre (same for the outlines view and the masks view)."""
    h, w = marked.shape[:2]
    cx, cy = (res["hub"][0], res["hub"][1]) if res.get("hub") else (w / 2, h / 2)
    x0, y0 = int(max(0, min(w - 2 * ZOOM_HALF, cx - ZOOM_HALF))), int(max(0, min(h - 2 * ZOOM_HALF, cy - ZOOM_HALF)))
    return marked[y0:y0 + 2 * ZOOM_HALF, x0:x0 + 2 * ZOOM_HALF]


class Bridge(QObject):
    picture = Signal(int, str, object, object)       # index, file name, marked picture, result
    status = Signal(str)
    finished = Signal(str)


class Window(QMainWindow):
    def __init__(self, folder: str | None):
        super().__init__()
        self.setWindowTitle(f"Study_Cap_Annulus_UI - cap seating by the annulus (hole finder {A.holeFinder()})")
        self.folder = pathlib.Path(folder) if folder else None
        self.results = []                                # (name, marked, res) per row
        self.stopFlag = threading.Event()
        self.worker = None
        self.bridge = Bridge()
        self.bridge.picture.connect(self.onPicture)
        self.bridge.status.connect(lambda s: self.statusLine.setText(s))
        self.bridge.finished.connect(self.onFinished)

        top = QHBoxLayout()
        self.folderButton = QPushButton("Folder...")
        self.folderButton.clicked.connect(self.chooseFolder)
        self.folderLabel = QLabel(str(self.folder) if self.folder else "(no folder)")
        self.model = QComboBox()
        self.model.addItems(["DOST", "DOSTPLUS", "From file name"])
        self.maskMethod = QComboBox()
        self.maskMethod.addItems(["YOLO boxes (cap check)", "24 points"])
        mp = A.maskParameters()

        def spin(value, low, high, step, tip):
            box = QDoubleSpinBox()
            box.setDecimals(2)
            box.setRange(low, high)
            box.setSingleStep(step)
            box.setValue(value)
            box.setToolTip(tip)
            return box

        self.conf = spin(mp["conf"], 0.01, 0.95, 0.01, "YOLO confidence: boxes below it are not prompts "
                         "(vision.cap.mask.yolo.confidence)")
        self.stability = spin(mp["stability"], 0.0, 1.0, 0.05, "MobileSAM stability score a mask must reach "
                              "(vision.cap.mask.stability.min)")
        self.iou = spin(mp["iou"], 0.0, 1.0, 0.05, "MobileSAM predicted IoU a mask must reach (vision.cap.mask.iou.min)")
        self.merge = spin(mp["merge"], 0.05, 1.0, 0.05, "Two masks whose boxes overlap more than this are one "
                          "(vision.cap.mask.merge.iou); 1.0 = no merging")
        self.share = spin(0.5, 0.0, 1.0, 0.05, "Third panel: share of a mask's area that must lie in the hole circle")
        self.yoloIou = spin(mp["yolo_iou"], 0.05, 1.0, 0.05, "YOLO's own NMS: boxes overlapping more than this become "
                            "one (production 0.7)")
        self.maxDet = QSpinBox()
        self.maxDet.setRange(10, 5000)
        self.maxDet.setSingleStep(100)
        self.maxDet.setValue(mp["max_det"])
        self.maxDet.setToolTip("YOLO's maximum detections per picture (production 300)")
        self.multimask = QCheckBox("3 masks per box")
        self.multimask.setChecked(mp["multimask"])
        self.multimask.setToolTip("MobileSAM multimask_output: 3 nested masks per YOLO box (part, whole, context) "
                                  "instead of 1 (production: 1)")
        self.productionButton = QPushButton("Production values")
        self.productionButton.clicked.connect(lambda: self.applyPreset(A.MASK_DEFAULTS))
        self.overButton = QPushButton("Over-detect")
        self.overButton.setToolTip("conf 0.10, YOLO NMS 0.80, max det 1000, 3 masks per box, predicted IoU 0.72, "
                                   "stability 0.78, merge 0.88")
        self.overButton.clicked.connect(lambda: self.applyPreset(A.MASK_OVERDETECT))
        self.seconds = QSpinBox()
        self.seconds.setRange(1, 10)
        self.seconds.setValue(3)
        self.gamma = QDoubleSpinBox()
        self.gamma.setRange(1.0, 6.0)
        self.gamma.setSingleStep(0.5)
        self.gamma.setValue(1.0)
        self.save = QCheckBox("Save")
        self.startButton = QPushButton("Start")
        self.startButton.clicked.connect(self.start)
        self.stopButton = QPushButton("Stop")
        self.stopButton.setEnabled(False)
        self.stopButton.clicked.connect(self.stopFlag.set)
        for wdg in (self.folderButton, self.folderLabel):
            top.addWidget(wdg)
        top.addStretch(1)
        for text, wdg in (("Model", self.model), ("Cap mask", self.maskMethod), ("Seconds between pictures", self.seconds), ("Gamma (SAM)", self.gamma)):
            top.addWidget(QLabel(text))
            top.addWidget(wdg)
        for wdg in (self.save, self.startButton, self.stopButton):
            top.addWidget(wdg)
        second = QHBoxLayout()
        second.addWidget(QLabel("YOLO:"))
        for text, wdg in (("confidence", self.conf), ("NMS IoU", self.yoloIou), ("max det", self.maxDet)):
            second.addWidget(QLabel(text))
            second.addWidget(wdg)
        second.addWidget(QLabel("   MobileSAM:"))
        second.addWidget(self.multimask)
        for text, wdg in (("pred. IoU >=", self.iou), ("stability >=", self.stability), ("merge above", self.merge),
                          ("   in-circle share", self.share)):
            second.addWidget(QLabel(text))
            second.addWidget(wdg)
        second.addStretch(1)
        second.addWidget(self.productionButton)
        second.addWidget(self.overButton)

        views = QHBoxLayout()
        self.fullView = QLabel()
        self.fullView.setFixedSize(FULL_W, FULL_H)
        self.fullView.setAlignment(Qt.AlignCenter)
        self.fullView.setStyleSheet("background: #202020")
        self.zoomView = QLabel()
        self.zoomView.setFixedSize(PANEL, PANEL)
        self.zoomView.setAlignment(Qt.AlignCenter)
        self.zoomView.setStyleSheet("background: #202020")
        self.masksView = QLabel()
        self.masksView.setFixedSize(PANEL, PANEL)
        self.masksView.setAlignment(Qt.AlignCenter)
        self.masksView.setStyleSheet("background: #202020")
        self.masksView.setToolTip("Every MobileSAM mask in the hole circle, largest painted first, smaller ones over it")
        views.addWidget(self.fullView)
        views.addWidget(self.zoomView)
        views.addWidget(self.masksView)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.cellClicked.connect(lambda row, _c: self.showRow(row))
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(QFont("Consolas", 9))
        split = QSplitter(Qt.Vertical)
        split.addWidget(self.table)
        split.addWidget(self.text)
        split.setSizes([260, 260])
        self.statusLine = QLabel("Choose a folder, then Start")

        layout = QVBoxLayout()
        layout.addLayout(top)
        layout.addLayout(second)
        layout.addLayout(views)
        layout.addWidget(split, 1)
        layout.addWidget(self.statusLine)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)
        self.resize(FULL_W + 2 * PANEL + 50, 980)

    def applyPreset(self, values: dict):
        self.conf.setValue(values["conf"])
        self.yoloIou.setValue(values["yolo_iou"])
        self.maxDet.setValue(values["max_det"])
        self.multimask.setChecked(values["multimask"])
        self.iou.setValue(values["iou"])
        self.stability.setValue(values["stability"])
        self.merge.setValue(values["merge"])

    def chooseFolder(self):
        chosen = QFileDialog.getExistingDirectory(self, "Folder of cap pictures", str(self.folder or A.HERE))
        if chosen:
            self.folder = pathlib.Path(chosen)
            self.folderLabel.setText(chosen)

    def log(self, line: str):
        self.text.appendPlainText(line)

    def start(self):
        if self.folder is None or not self.folder.is_dir():
            self.statusLine.setText("Choose a folder first")
            return
        files = A.pictures(self.folder)
        if not files:
            self.statusLine.setText(f"No pictures in {self.folder}")
            return
        self.results.clear()
        self.table.setRowCount(0)
        self.text.clear()
        self.log(f"{len(files)} pictures in {self.folder} - hole finder {A.holeFinder()}, gamma {self.gamma.value()}")
        self.stopFlag.clear()
        self.startButton.setEnabled(False)
        self.stopButton.setEnabled(True)
        choice, gamma, save = self.model.currentText(), self.gamma.value(), self.save.isChecked()
        method = "points" if self.maskMethod.currentIndex() == 1 else "boxes"
        mp = A.setMaskParameters(self.conf.value(), self.stability.value(), self.iou.value(), self.merge.value(),
                                 self.yoloIou.value(), self.maxDet.value(), self.multimask.isChecked())
        share = self.share.value()
        self.log(f"cap mask: {method}; YOLO confidence {mp['conf']}, NMS IoU {mp['yolo_iou']}, max det {mp['max_det']}; "
                 f"MobileSAM {'3 masks' if mp['multimask'] else '1 mask'} per box, predicted IoU >= {mp['iou']}, "
                 f"stability >= {mp['stability']}, merge above {mp['merge']}; in-circle share {share}")
        self.worker = threading.Thread(target=self.run, args=(files, choice, gamma, save, method, share), daemon=True)
        self.worker.start()

    def run(self, files, choice, gamma, save, method, share):
        resultsDir = self.folder / "cap_annulus_results"
        resultsDir.mkdir(exist_ok=True)
        rows = []
        try:
            for i, p in enumerate(files):
                if self.stopFlag.is_set():
                    break
                self.bridge.status.emit(f"{i + 1} / {len(files)}: {p.name} ... checking")
                img = cv2.imread(str(p))
                if img is None:
                    res = dict.fromkeys(A.HEADER, "")
                    res.update(file=p.name, note="could not read", hub=None)
                    self.bridge.picture.emit(i, p.name, np.zeros((720, 1280, 3), np.uint8), res)
                    rows.append(res)
                    continue
                try:
                    res = A.measure(img, A.modelFor(choice, p.name), gamma, 1.0, method, share)
                except Exception as e:                    # shown, and the run goes on
                    res = dict.fromkeys(A.HEADER, "")
                    res.update(model=A.modelFor(choice, p.name), hub=None, note=f"exception: {e}")
                res["file"] = p.name
                picture = A.marked(img, res)
                if save:
                    cv2.imwrite(str(resultsDir / f"{p.stem}_annulus.jpg"), picture)
                    if res.get("masksPicture") is not None:
                        cv2.imwrite(str(resultsDir / f"{p.stem}_masks.jpg"), res["masksPicture"])
                rows.append(res)
                self.bridge.picture.emit(i, p.name, picture, res)
                end = time.monotonic() + self.seconds.value()
                while time.monotonic() < end and not self.stopFlag.is_set():
                    time.sleep(0.05)
            csvPath = resultsDir / f"annulus_{time.strftime('%Y%m%d_%H%M%S')}.csv"
            A.writeCsv(csvPath, rows)
            summary = A.summaryLines(rows) + [f"written: {csvPath}"]
            self.bridge.finished.emit(("Stopped. " if self.stopFlag.is_set() else "") + "\n".join(summary))
        except Exception as e:
            self.bridge.finished.emit(f"Run ended by an exception: {e}")

    def onPicture(self, index, name, picture, res):
        self.results.append((name, picture, res))
        row = self.table.rowCount()
        self.table.insertRow(row)
        hub = f"({res.get('hub_x')}, {res.get('hub_y')})" if res.get("hub_x") else "-"
        off = f"{res['ell_minus_hub_dx']} / {res['ell_minus_hub_dy']}" if res.get("ell_cx") else "-"
        values = (name, res.get("cap_found", ""), res.get("rb_silver", ""), res.get("brightness", ""), hub,
                  res.get("hole_r") or "-", res.get("stages") or "-", res.get("masks_in_circle", ""), off, res.get("score_ellipse") or "-", res.get("score_mask") or "-",
                  res.get("seconds", ""), res.get("note", ""))
        for col, v in enumerate(values):
            self.table.setItem(row, col, QTableWidgetItem(str(v)))
        self.table.scrollToBottom()
        self.showRow(row)
        if res.get("model"):
            for line in A.detailLines(name, res):
                self.log(line)
        else:
            self.log(f"{name}: {res.get('note')}")

    def showRow(self, row):
        if not (0 <= row < len(self.results)):
            return
        name, picture, res = self.results[row]
        self.fullView.setPixmap(toPixmap(picture, FULL_W, FULL_H))
        self.zoomView.setPixmap(toPixmap(zoomOf(picture, res), PANEL, PANEL))
        if res.get("masksPicture") is not None:
            self.masksView.setPixmap(toPixmap(zoomOf(res["masksPicture"], res), PANEL, PANEL))
        else:
            self.masksView.clear()
        self.statusLine.setText(f"{name}: score ellipse {res.get('score_ellipse') or '-'}, mask "
                                f"{res.get('score_mask') or '-'}, {res.get('seconds')} s")

    def onFinished(self, text):
        self.log("")
        self.log(text)
        self.startButton.setEnabled(True)
        self.stopButton.setEnabled(False)
        self.statusLine.setText("Finished" if not self.stopFlag.is_set() else "Stopped")


def main():
    ap = argparse.ArgumentParser(description="Cap seating by the annulus, in a window (study)")
    ap.add_argument("folder", nargs="?", default=None)
    ap.add_argument("--sam", default=None)
    ap.add_argument("--holes", default=None, choices=["morphology"])
    ap.add_argument("--conf", type=float, default=None, help="YOLO confidence at start (else the properties value)")
    ap.add_argument("--overdetect", action="store_true", help="start with the over-detection values")
    args = ap.parse_args()
    A.configure(args.sam, args.holes)
    if args.overdetect:
        A.setMaskParameters(**A.MASK_OVERDETECT)
    A.setMaskParameters(conf=args.conf)
    app = QApplication.instance() or QApplication(sys.argv)
    try:
        from frontend.QtMessages import installQtMessageHandler   # Qt's own messages to the log, not the C stderr
        installQtMessageHandler()
    except Exception:
        pass
    w = Window(args.folder)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
