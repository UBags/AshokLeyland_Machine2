"""
Study_Cap_Annulus_UI - the cap annulus measure (_cap_annulus.py, CHANGES 203 / 204) in a window, study only.

Row 1: Folder..., model (DOST / DOSTPLUS / from the file name), cap mask method - "YOLO boxes (cap check)" (the
production cap mask's steps on a disc round the hub centre, default), "24 points" (r 30 / 60 / 90 round the hub centre),
"Annulus only" (CHANGES 209: positive prompts every 5 deg on a circle 5 px inside the hole circle, none within 28 px of a
hole centre, one negative prompt at the hub centre; the annulus mask inside the hole circle, the cap = what it encloses)
- seconds between pictures (1 - 10, changeable during the run), Save, Start / Stop.
Row 2, YOLO (YOLO boxes method): conf, iou (YOLO's NMS), max_det.
Row 3, MobileSAM: multimask_output (3 masks per box), pred_iou_thresh, stability_score_thresh, box_nms_thresh (masks
whose boxes overlap more than this are merged; 1.0 = none) - YOLO boxes method; "Production values" / "Over-detect".
There is no crop_nms_thresh: this pipeline has no crop layers (one disc cut round the hub centre).
Defaults (CHANGES 210, Uddi's values of 2026-11-27 11:00): Annulus only, 1 s; conf 0.05, iou 0.95, max_det 300,
multimask_output on, pred_iou_thresh 0.15, stability_score_thresh 0.25, box_nms_thresh 0.90; gamma holes 1.0, masks 2.0
("Study defaults" button; "Production values" = the cap check's). Annulus only draws and scores only the annulus: its
edges (magenta) and the inner ellipse (yellow) - the ellipse with the most inner edge points within 2 px (not least
squares) - and prints one "ANNULUS file=... key=value ..." line per picture on the terminal, to send back.
Verdict (CHANGES 211, Uddi's rule of 2026-11-27, islands limit 450 since CHANGES 212; annulus only; production's
camera/CapAnnulus.py, the DOST cap seating since CHANGES 212): stage 1 - centre offset > 7, score_ellipse > 16,
islands > 450, w_opp_max > 17, ring width p90 > 50 or width_max > 52 -> NOT OK; stage 2 - inlier_share > 0.70,
width_sd < 3.5, islands < 200, w_opp_max < 11, p90 < 47 or width_max < 48.25 -> OK; else NOT OK. A "VERDICT file: OK /
NOT OK - stage n: the conditions met" line on the terminal, a verdict column (green / red), OK in green or NOT OK in red
written on the third panel, and a beep for NOT OK.
Alternative AND rule (recorded, not used; Uddi 2026-11-27): OK if centre offset < 6.5 and score_ellipse < 15.0 and
w_opp_max < 13.8 and width_p90 < 47.7 and width_max < 49.6 (optionally also islands < 1000, width_min > 25), else NOT
OK - 0 errors on the same 116 pictures (71 OK / 43 NOT OK with values; worst OK / closest NOT OK: offset 5.32 / 8.46,
score 14.27 / 15.50, w_opp_max 13.5 / 14.0, p90 46.9 / 48.4, width_max 48.8 / 50.5); _cap_annulus.verdictAnd, shown
for information as an "AND file: ..." line on the terminal.
Row 4, gamma: of the picture given to the hole finder (1 = as taken), and of the picture given to MobileSAM for the
masks (every method; production cap mask 2.0).
The values are taken at Start and apply in this window only (production and the properties file are not touched); the
table's boxes/disc/sam/pass/merged column shows where masks are lost.
Views: the whole picture and a 2x view round the hub centre, outlines only - hole circle and hub centre (blue), prompt
points (cyan), annulus mask edges (magenta, annulus method), cap mask outline (green), fitted ellipse (yellow), the 12
diameters (grey); third view: the masks in the hole circle coloured, largest painted first (YOLO boxes: every mask left
after the filters and merging with at least half its area in the circle; points: its mask; annulus: the annulus, then
the cap inside it). No values on the pictures: the table (one row per picture, its check time included) and the text
box (steps 1 - 2 values, hub centre, masks, ellipse, every diameter's L+ / L- and ratio, for the ellipse and for the mask
outline). Click a row to see that picture again. At the end the summary in the text box and annulus_<time>.csv (+ the
marked pictures with Save) in <folder>/cap_annulus_results/.

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
from PySide6.QtGui import QImage, QPixmap, QFont, QPainter, QColor                              # noqa: E402
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout,  # noqa
                               QHeaderView, QLabel, QMainWindow, QPlainTextEdit, QPushButton, QSpinBox, QSplitter,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

COLUMNS = ("file", "verdict", "cap", "R-B", "bright", "hub", "R", "boxes/disc/sam/pass/merged", "masks", "ell - hub", "score ellipse",
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


def withVerdict(pixmap: QPixmap, text: str) -> None:
    """Uddi, 2026-11-27: the verdict written on the third panel - OK in green, NOT OK in red (the only text on a picture
    in this window; values stay in the table and the text box)."""
    painter = QPainter(pixmap)
    font = QFont("Arial", 30)
    font.setBold(True)
    painter.setFont(font)
    rect = pixmap.rect().adjusted(0, 6, 0, 0)
    painter.setPen(QColor(0, 0, 0))                      # dark edge so the word reads on any background
    for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
        painter.drawText(rect.translated(dx, dy), Qt.AlignHCenter | Qt.AlignTop, text)
    painter.setPen(QColor(0, 220, 0) if text == "OK" else QColor(255, 30, 30))
    painter.drawText(rect, Qt.AlignHCenter | Qt.AlignTop, text)
    painter.end()


def alarm() -> None:
    """A sound for a NOT OK picture: Windows' beep (winsound, in a thread so the window does not wait), else Qt's."""
    try:
        import winsound
        threading.Thread(target=lambda: winsound.Beep(1200, 400), daemon=True).start()
    except Exception:
        QApplication.beep()


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
        self.maskMethod.addItems(["YOLO boxes (cap check)", "24 points", "Annulus only"])
        self.maskMethod.setCurrentIndex(A.MASK_METHODS.index(A.STUDY_METHOD))
        mp = A.maskParameters()

        def spin(value, low, high, step, tip):
            box = QDoubleSpinBox()
            box.setDecimals(2)
            box.setRange(low, high)
            box.setSingleStep(step)
            box.setValue(value)
            box.setToolTip(tip)
            return box

        self.conf = spin(mp["conf"], 0.01, 0.95, 0.01, "YOLO conf: boxes below it are not prompts "
                         "(production vision.cap.mask.yolo.confidence)")
        self.stability = spin(mp["stability"], 0.0, 1.0, 0.01, "MobileSAM stability_score_thresh: a mask's stability "
                              "score must reach it (production vision.cap.mask.stability.min)")
        self.iou = spin(mp["iou"], 0.0, 1.0, 0.01, "MobileSAM pred_iou_thresh: a mask's predicted IoU must reach it "
                        "(production vision.cap.mask.iou.min)")
        self.merge = spin(mp["merge"], 0.05, 1.0, 0.01, "MobileSAM box_nms_thresh: masks whose boxes overlap more than "
                          "this are merged (production vision.cap.mask.merge.iou); 1.0 = no merging")
        self.yoloIou = spin(mp["yolo_iou"], 0.05, 1.0, 0.01, "YOLO iou (its NMS): boxes overlapping more than this "
                            "become one (production 0.7)")
        self.maxDet = QSpinBox()
        self.maxDet.setRange(10, 5000)
        self.maxDet.setSingleStep(100)
        self.maxDet.setValue(mp["max_det"])
        self.maxDet.setToolTip("YOLO's maximum detections per picture (production 300)")
        self.multimask = QCheckBox("multimask_output (3 masks per box)")
        self.multimask.setChecked(mp["multimask"])
        self.multimask.setToolTip("MobileSAM multimask_output: 3 nested masks per YOLO box (part, whole, context) "
                                  "instead of 1 (production: 1)")
        self.studyButton = QPushButton("Study defaults")
        self.studyButton.setToolTip("conf 0.05, iou 0.95, max_det 300, multimask_output on, pred_iou_thresh 0.15, "
                                    "stability_score_thresh 0.25, box_nms_thresh 0.90 (Uddi, 2026-11-27)")
        self.studyButton.clicked.connect(lambda: self.applyPreset(A.MASK_STUDY))
        self.productionButton = QPushButton("Production values")
        self.productionButton.clicked.connect(lambda: self.applyPreset(A.MASK_DEFAULTS))
        self.overButton = QPushButton("Over-detect")
        self.overButton.setToolTip("conf 0.10, YOLO NMS 0.80, max det 1000, 3 masks per box, predicted IoU 0.72, "
                                   "stability 0.78, merge 0.88")
        self.overButton.clicked.connect(lambda: self.applyPreset(A.MASK_OVERDETECT))
        self.seconds = QSpinBox()
        self.seconds.setRange(1, 10)
        self.seconds.setValue(1)
        self.holesGamma = spin(A.STUDY_HOLES_GAMMA, 0.2, 6.0, 0.1, "Gamma of the picture given to the hole finder (1 = as taken; "
                               "the finder still tries its own gammas on it)")
        self.maskGamma = spin(A.STUDY_MASK_GAMMA, 0.2, 6.0, 0.1, "Gamma of the picture given to MobileSAM for the "
                              "masks, every method (production cap mask 2.0)")
        self.save = QCheckBox("Save")
        self.startButton = QPushButton("Start")
        self.startButton.clicked.connect(self.start)
        self.stopButton = QPushButton("Stop")
        self.stopButton.setEnabled(False)
        self.stopButton.clicked.connect(self.stopFlag.set)
        for wdg in (self.folderButton, self.folderLabel):
            top.addWidget(wdg)
        top.addStretch(1)
        for text, wdg in (("Model", self.model), ("Cap mask", self.maskMethod), ("Seconds between pictures", self.seconds)):
            top.addWidget(QLabel(text))
            top.addWidget(wdg)
        for wdg in (self.save, self.startButton, self.stopButton):
            top.addWidget(wdg)
        def row(title, items, extra=()):
            line = QHBoxLayout()
            label = QLabel(title)
            label.setMinimumWidth(150)
            label.setStyleSheet("font-weight: bold")
            line.addWidget(label)
            for text, wdg in items:
                line.addWidget(QLabel(text))
                line.addWidget(wdg)
            line.addStretch(1)
            for wdg in extra:
                line.addWidget(wdg)
            return line

        yoloRow = row("YOLO (boxes method):", (("conf", self.conf), ("iou", self.yoloIou), ("max_det", self.maxDet)))
        samRow = row("MobileSAM (boxes method):", (("", self.multimask), ("pred_iou_thresh", self.iou),
                                                   ("stability_score_thresh", self.stability),
                                                   ("box_nms_thresh", self.merge)),
                     (self.studyButton, self.productionButton, self.overButton))
        gammaRow = row("Gamma:", (("picture for the holes", self.holesGamma),
                                  ("picture for the masks", self.maskGamma)))

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
        layout.addLayout(yoloRow)
        layout.addLayout(samRow)
        layout.addLayout(gammaRow)
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
        self.log(f"{len(files)} pictures in {self.folder} - hole finder {A.holeFinder()}")
        self.stopFlag.clear()
        self.startButton.setEnabled(False)
        self.stopButton.setEnabled(True)
        choice, save = self.model.currentText(), self.save.isChecked()
        method = A.MASK_METHODS[self.maskMethod.currentIndex()]
        gammas = (self.holesGamma.value(), self.maskGamma.value())
        mp = A.setMaskParameters(self.conf.value(), self.stability.value(), self.iou.value(), self.merge.value(),
                                 self.yoloIou.value(), self.maxDet.value(), self.multimask.isChecked())
        self.log(f"cap mask: {method}; gamma: holes {gammas[0]}, masks {gammas[1]}")
        if method == "boxes":
            self.log(f"YOLO conf {mp['conf']}, iou {mp['yolo_iou']}, max_det {mp['max_det']}; MobileSAM multimask_output "
                     f"{mp['multimask']}, pred_iou_thresh {mp['iou']}, stability_score_thresh {mp['stability']}, "
                     f"box_nms_thresh {mp['merge']}")
        self.worker = threading.Thread(target=self.run, args=(files, choice, gammas, save, method), daemon=True)
        self.worker.start()

    def run(self, files, choice, gammas, save, method):
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
                    res = A.measure(img, A.modelFor(choice, p.name), 1.0, method, gammas[0], gammas[1])
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
                if method == "annulus":
                    print(A.annulusLine(p.name, res), flush=True)        # the terminal line to send back
                    print(f"VERDICT {p.name}: {res.get('verdict')} - {res.get('why')}", flush=True)
                    print("AND %s: %s - %s (alternative, not used)" % ((p.name,) + A.verdictAnd(res)), flush=True)
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
        values = (name, res.get("verdict") or "-", res.get("cap_found", ""), res.get("rb_silver", ""), res.get("brightness", ""), hub,
                  res.get("hole_r") or "-", res.get("stages") or "-", res.get("masks_in_circle", ""), off, res.get("score_ellipse") or "-", res.get("score_mask") or "-",
                  res.get("seconds", ""), res.get("note", ""))
        for col, v in enumerate(values):
            item = QTableWidgetItem(str(v))
            if col == 1 and res.get("verdict"):
                item.setForeground(QColor(0, 150, 0) if res["verdict"] == "OK" else QColor(210, 0, 0))
                bold = item.font()
                bold.setBold(True)
                item.setFont(bold)
            self.table.setItem(row, col, item)
        if res.get("verdict") == "NOT OK":
            alarm()
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
            panel = toPixmap(zoomOf(res["masksPicture"], res), PANEL, PANEL)
            if res.get("verdict"):
                withVerdict(panel, res["verdict"])
            self.masksView.setPixmap(panel)
        else:
            self.masksView.clear()
        self.statusLine.setText(f"{name}: {res.get('verdict') or ''} score ellipse {res.get('score_ellipse') or '-'}, mask "
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
