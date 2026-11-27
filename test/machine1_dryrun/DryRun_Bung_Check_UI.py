"""
DryRun_Bung_Check_UI - the real bung check on a folder of bung pictures, with this machine's settings
(vision.<vendor>.bung.* in ApplicationConfiguration.properties), shown one picture at a time. Needs the MobileSAM models
(as the camera server does); a CUDA GPU makes it fast.

Both bung checks run the same hole detection (camera/BungSegmenter.py); choose which one in the window:
  - CheckBung.checkBung            - state 24, bung before the cap press
  - CheckNoCapBung.checkNoCapBung  - state 13, bung before the component press
The r<outer.disc.radius> disc round vision.<vendor>.bung.outer.disc.center is segmented (gammas 2.0, 7.75, 4.0, 6.5, 1.0
until a definite answer; YOLO boxes + a point every hole.grid.step px); holes count only inside r<inner.disc.radius>
round the same centre. One "[Bung] ..." log line per gamma.
  DOST     : 4 holes forming a square (sides within square.side.length.tolerance of the median, corners
             square.min/max.interior.angle)
  DOSTPLUS : 2 holes dostplus.hole.pair.distance.min..max apart, opposite about the centre (the best pair
             among the candidates); a DOST square found = NOT OK

  Start: model (DOST / DOSTPLUS - every picture is checked as this model; "Both" - every picture is checked as DOST
  and as DOSTPLUS), folder (default: bung_images_20261113 next to this script, the pictures that come with git pull;
  "Folder..." to choose another), seconds between pictures, check (state 24 / state 13), "Save marked pictures"
  (<folder>/bung_results/).
  Picture: no text on it - cyan circle = the segmented disc, yellow circle and cross = the hole search disc and its
  centre, red dots = the holes found (green border = OK, red = NOT OK). The list at the bottom and the terminal give,
  for each picture, the model checked, the number of holes found, OK / NOT OK, the time, the spacing, their centre's
  distance from the set centre and the deciding gamma. Terminal lines start "HOLES".
  Seating (vision.<model>.bung.seating.check, Machine 2): after a hole pattern passes, the check measures the hole-pattern
  centre against the model's seated position, the roundness of the top-face mask (MobileSAM), how well it fits an
  ellipse and how much of it lies outside the model's reference oval; the line after HOLES
  starts "SEATING" (SEATED / NOT SEATED and both values; "not run" when the holes did not pass). On the picture the
  top-face outline is green (seated) or red.
  Hub holes (the seated position comes from them, FindHubHoles, CHANGES 153, 164 - 166): each hole ringed blue, the
  hole circle's centre a blue cross; the terminal line "HUBHOLES" gives centre, radius, spread, gap, method, gamma and
  seed (or why not found). --holes morphology = the dark-blob finder with its own offsets (no YOLO weights).

  DOSTPLUS hub-centre study (CHANGES 215, Uddi 2026-11-27; "DOSTPLUS: hub-centre study", on by default; DOST is never
  studied - its check is production's): the hub's 5 holes first; the segmented disc = the hub-hole circle (dark blue),
  the hole search disc r135 round the hub centre (thin yellow, yellow cross); the production hole check runs on those
  discs; the seating measured with the reference oval centred on the hub centre (thick yellow); the hole-pattern centre
  must lie in the region round the hub centre (orange; east-west, north and south half-axes, HOLE_REGION; CHANGES 217:
  the hub offset is ignored). Production
  (camera/...) is not changed. One "STUDY" line per DOSTPLUS picture with every value; pictures in subfolders named
  OK and NOT_OK (or GOOD / BAD) are labelled, and a "STUDY SUMMARY" at the end gives the OK / NOT OK range of each
  value and a limit midway where they separate. The study's limits: STUDY_LIMITS in _bung_hubcentre_study.py.

Run from the project root:   python test/machine2_dryrun/DryRun_Bung_Check_UI.py   [--holes morphology] [--hub-image <hub picture>]
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
CHECKS = ("CheckBung (state 24)", "CheckNoCapBung (state 13)")
VIEW_W, VIEW_H = 1120, 630


_recorder = None  # HubHolesRecorder, installed in __main__: the hub's holes ringed on the picture, HUBHOLES line


class BungCheckWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.BS = None                # camera.BungSegmenter
        self.checks = {}              # title -> check function
        self.last = {}                # what the hole detection returned last (the deciding gamma)
        self.results = queue.Queue()
        self.advance = threading.Event()
        self.stopRequested = threading.Event()
        self.photo = None
        self.summary = []

        root.title(f"Dry run - bung check ({os.environ['COSTHETA_PLC_VENDOR']})")
        bar = ttk.Frame(root, padding=6)
        bar.pack(fill="x")
        ttk.Label(bar, text="Model").pack(side="left")
        self.model = tk.StringVar(value="Both")
        ttk.Combobox(bar, textvariable=self.model, values=("Both", "DOST", "DOSTPLUS"), state="readonly",
                     width=10).pack(side="left", padx=4)
        ttk.Button(bar, text="Folder...", command=self.chooseFolder).pack(side="left", padx=(10, 0))
        default = HERE / "bung_images_20261113"
        self.folder = tk.StringVar(value=str(default) if default.is_dir() else "")
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
        from _hub_holes_option import addHubPictureControl
        addHubPictureControl(bar, root, report=lambda line: self.status.set(line))
        self.save = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Save marked pictures", variable=self.save).pack(side="left", padx=10)
        self.studyOn = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="DOSTPLUS: hub-centre study", variable=self.studyOn).pack(side="left", padx=(0, 10))
        self.study = None
        self.startButton = ttk.Button(bar, text="Start", command=self.start, state="disabled")
        self.startButton.pack(side="left")
        self.stopButton = ttk.Button(bar, text="Stop", command=self.stop, state="disabled")
        self.stopButton.pack(side="left", padx=4)

        self.view = ttk.Label(root, anchor="center")
        self.view.pack(fill="both", expand=True)
        self.status = tk.StringVar(value="Loading the models ...")
        ttk.Label(root, textvariable=self.status, padding=6, font=("Segoe UI", 11, "bold")).pack(fill="x")
        listFrame = ttk.Frame(root)
        listFrame.pack(fill="x", padx=6, pady=(0, 6))
        self.list = tk.Listbox(listFrame, height=8, font=("Consolas", 10))
        yscroll = ttk.Scrollbar(listFrame, command=self.list.yview)
        xscroll = ttk.Scrollbar(listFrame, orient="horizontal", command=self.list.xview)
        self.list.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        xscroll.pack(side="bottom", fill="x"); yscroll.pack(side="right", fill="y")
        self.list.pack(side="left", fill="x", expand=True)

        threading.Thread(target=self.load, name="Load models", daemon=True).start()
        root.after(100, self.poll)
        root.protocol("WM_DELETE_WINDOW", self.close)

    # ------------------------------------------------------------------ check
    def load(self):
        try:
            import camera.BungSegmenter as BS
            from camera.CheckBung import CheckBung
            from camera.CheckNoCapBung import CheckNoCapBung
            t0 = time.time()
            BS.BungSegmenter.get_instance()                      # loads MobileSAM + YOLO once
            original = BS.BungSegmenter.segment_holes_single

            self.times = {}
            self.inSeating = False

            def add(key, seconds):
                self.times[key] = self.times.get(key, 0.0) + seconds
                if key == "bung holes":
                    self.times["gammas"] = self.times.get("gammas", 0) + 1

            def recording(segmenter, preprocessed_image, gamma, expected_count, model_type):
                t = time.perf_counter()
                result = original(segmenter, preprocessed_image, gamma, expected_count, model_type)
                add("bung holes", time.perf_counter() - t)
                self.last["result"] = result                     # the last call is the deciding gamma
                return result

            BS.BungSegmenter.segment_holes_single = recording
            originalSeating = BS.check_bung_seating

            def recordingSeating(image_rgb, components, model_type):
                self.inSeating = True
                t = time.perf_counter()
                try:
                    result = originalSeating(image_rgb, components, model_type)
                finally:
                    add("seating", time.perf_counter() - t)
                    self.inSeating = False
                self.last["seating"] = result
                return result

            BS.check_bung_seating = recordingSeating
            # step times (CHANGES 172): the hub's holes in the seating step / only for the marks on a picture whose
            # seating did not run (CheckBung.drawSeatingMarks); the dry run's own hub check (production has state 5's)
            from camera.FindHubHoles import FindHubHoles
            originalFind = FindHubHoles.findHoles

            def timedFind(anImage, model, hubCentre=None, check=None):
                t = time.perf_counter()
                try:
                    return originalFind(anImage, model, hubCentre, check=check)
                finally:
                    add("hub holes (study)" if getattr(self, "inStudy", False) else
                        "hub holes (seating)" if self.inSeating else "hub holes (marks only)", time.perf_counter() - t)

            FindHubHoles.findHoles = staticmethod(timedFind)
            if FindHubHoles.hubCentreFromPicture is not None:
                originalHubCheck = FindHubHoles.hubCentreFromPicture

                def timedHubCheck(anImage, model):
                    t = time.perf_counter()
                    try:
                        return originalHubCheck(anImage, model)
                    finally:
                        add("dry-run hub check (seating)" if self.inSeating else "dry-run hub check (marks only)",
                            time.perf_counter() - t)

                FindHubHoles.hubCentreFromPicture = staticmethod(timedHubCheck)
            self.BS = BS
            from _bung_hubcentre_study import DostplusHubCentreStudy
            self.study = DostplusHubCentreStudy(BS)
            self.checks = {CHECKS[0]: CheckBung.checkBung, CHECKS[1]: CheckNoCapBung.checkNoCapBung}
            self.results.put(("ready",
                f"Models loaded in {time.time() - t0:.1f} s. Centre {tuple(BS.OUTER_DISC_CENTER)}, segmented disc "
                f"r{BS.OUTER_DISC_RADIUS}, holes within r{BS.INNER_DISC_RADIUS}; DOSTPLUS pair "
                f"{BS.DOSTPLUS_HOLE_PAIR_DISTANCE_MIN}-{BS.DOSTPLUS_HOLE_PAIR_DISTANCE_MAX} px; DOST square sides within "
                f"{BS.SQUARE_SIDE_LENGTH_TOLERANCE:.0%}. Seating {'ON' if BS.SEATING_CHECK else 'OFF'}"
                + (f" (holes centre within {BS.SEATING_HOLE_CENTRE_OFFSET_MAX} px of DOST {tuple(BS.SEATING_SEATED_CENTRE_DOST)} "
                   f"/ DOSTPLUS {tuple(BS.SEATING_SEATED_CENTRE_DOSTPLUS)}, roundness >= {BS.SEATING_ROUNDNESS_MIN_DOST} / "
                   f"{BS.SEATING_ROUNDNESS_MIN_DOSTPLUS})." if BS.SEATING_CHECK else ".")))
        except Exception as e:
            self.results.put(("fatal", f"Could not load the bung check: {e}"))

    # ------------------------------------------------------------------ controls
    def chooseFolder(self):
        folder = filedialog.askdirectory(title="Folder with bung pictures")
        if folder:
            self.folder.set(folder)

    def pictures(self):
        folder = pathlib.Path(self.folder.get())
        if not self.folder.get() or not folder.is_dir():
            return []
        from _bung_hubcentre_study import OK_FOLDERS, NOT_OK_FOLDERS
        files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONS]
        for sub in folder.iterdir():                 # labelled subfolders OK / NOT_OK (GOOD / BAD) - CHANGES 215
            if sub.is_dir() and sub.name.strip().lower() in OK_FOLDERS + NOT_OK_FOLDERS:
                files += [p for p in sub.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONS]
        return sorted(files)

    def start(self):
        files = self.pictures()
        if not files:
            messagebox.showwarning("No pictures", "Choose a folder that contains jpg / png / bmp pictures.")
            return
        self.summary = []
        if self.study is not None:
            self.study.rows = []
        self.list.delete(0, "end")
        self.stopRequested.clear()
        self.startButton.config(state="disabled")
        self.stopButton.config(state="normal")
        outFolder = pathlib.Path(self.folder.get()) / "bung_results"
        threading.Thread(target=self.run, args=(files, self.model.get(), self.check.get(), self.save.get(), outFolder),
                         name="Bung check", daemon=True).start()

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
            for asModel in (("DOST", "DOSTPLUS") if model == "Both" else (model,)):
                self.last.clear()
                self.times = {}
                if _recorder is not None:
                    _recorder.clear()
                t0 = time.time()
                studying = asModel == "DOSTPLUS" and self.studyOn.get() and self.study is not None
                studyValues, studyText, studyHolesOK = None, "", False
                try:
                    if studying:
                        self.inStudy = True
                        try:
                            found, why = self.study.begin(image)
                        finally:
                            self.inStudy = False
                        if not found:
                            marked, ok, gamma, holes, reason = image.copy(), False, None, [], why
                            studyText = why
                    if not studying or found:
                        marked, ok, gamma = check(image.copy(), QR[asModel], None)
                        holes = self.holes()
                        reason = self.reason(holes, gamma)
                        if studying:
                            studyHolesOK = ok
                            if studyHolesOK:
                                studyValues = self.study.seating(cv2.cvtColor(image, cv2.COLOR_BGR2RGB),
                                                                 self.last["result"][1])
                                seated, studyText = self.study.verdict(studyValues)
                                ok = seated
                            else:
                                studyText = "hole pattern NOT OK"
                except Exception as e:
                    marked, ok, holes, reason = None, False, [], f"exception: {e}"
                    studyText = reason
                finally:
                    if studying:
                        self.study.end()
                seconds = time.time() - t0
                hubHoles = _recorder.take() if _recorder is not None else []
                seat = self.last.get("seating")
                holesOK = studyHolesOK if studying else (ok or (seat is not None))   # seating only after the holes passed
                print(f"HOLES    {path.name:28s} as {asModel:8s}  {len(holes)} holes  "
                      f"{'OK    ' if holesOK else 'NOT OK'}  {reason}", flush=True)
                if studying:
                    label = self.labelOf(path)
                    self.study.record(label, path.name, studyValues, "OK" if ok else "NOT OK", studyText)
                    print(f"STUDY    {path.name:28s} as DOSTPLUS  {'OK    ' if ok else 'NOT OK'}"
                          f"{'  (labelled ' + label + ')' if label else ''}  {studyText}"
                          + (f"  |  {self.study.line(studyValues)}" if studyValues else "")
                          + f"  |  hub holes {self.study.seconds.get('hub holes', 0):.2f} s, seating "
                            f"{self.study.seconds.get('seating', 0):.2f} s", flush=True)
                    reason += f"; study: {studyText}"
                elif self.BS.SEATING_CHECK:
                    seatText = ("not run (hole check failed)" if seat is None else
                                f"{'SEATED    ' if seat['seated'] else 'NOT SEATED'}  {seat['reason']}")
                    print(f"SEATING  {path.name:28s} as {asModel:8s}  {seatText}", flush=True)
                print(f"RESULT   {path.name:28s} as {asModel:8s}  {'OK' if ok else 'NOT OK'}  {seconds:5.2f} s",
                      flush=True)
                if hubHoles:
                    print(f"HUBHOLES {path.name:28s} as {asModel:8s}  {_recorder.text(hubHoles)}", flush=True)
                print(f"TIMES    {path.name:28s} as {asModel:8s}  {self.timesText(seconds)}", flush=True)
                if seat is not None:
                    reason += f"; seating: {'SEATED' if seat['seated'] else 'NOT SEATED'} - {seat['reason']}"
                shown = self.decorate(image, marked, ok, holes,
                                      study=(self.study.hub, studyValues) if studying else None)
                if hubHoles:
                    shown = _recorder.draw(shown, hubHoles)
                if save:
                    cv2.imwrite(str(outFolder / f"{'OK' if ok else 'NOTOK'}_{path.stem}_as_{asModel}.png"), shown)
                self.advance.clear()
                self.results.put(("shown", (n, len(files), f"{path.name} as {asModel}", shown, ok,
                                            f"{len(holes)} holes - {reason}", seconds)))
                self.advance.wait()
                if self.stopRequested.is_set():
                    break
        if self.study is not None:
            lines = self.study.summaryLines()
            for line in lines:
                print(line, flush=True)
            if lines:
                self.results.put(("summary", lines))
        self.results.put(("done", None))

    @staticmethod
    def labelOf(path) -> str:
        from _bung_hubcentre_study import labelOf
        return labelOf(path)

    def timesText(self, total: float) -> str:
        """Where the check's time went: the bung's holes (gammas), the seating step (its hub holes and the dry run's hub
        check inside it), the hub holes looked for only to draw the marks, the rest. The dry run's hub check is not in
        production (state 5 gives the centre)."""
        t = self.times
        hubCheckSeat, hubCheckMarks = t.get("dry-run hub check (seating)", 0.0), t.get("dry-run hub check (marks only)", 0.0)
        holesSeat = t.get("hub holes (seating)", 0.0) - hubCheckSeat
        holesMarks = t.get("hub holes (marks only)", 0.0) - hubCheckMarks
        seatingRest = t.get("seating", 0.0) - t.get("hub holes (seating)", 0.0)
        accounted = t.get("bung holes", 0.0) + t.get("seating", 0.0) + t.get("hub holes (marks only)", 0.0)
        return (f"total {total:.2f} s = bung holes {t.get('bung holes', 0.0):.2f} s ({t.get('gammas', 0)} gammas)"
                f" + hub holes {holesSeat:.2f} s + seating face {seatingRest:.2f} s"
                f" + hub holes for the marks only {holesMarks:.2f} s"
                + (f" + hub holes first (study) {t['hub holes (study)']:.2f} s" if "hub holes (study)" in t else "")
                + f" + other {total - accounted - t.get('hub holes (study)', 0.0):.2f} s"
                f"  [dry run's own hub check, not in production: {hubCheckSeat + hubCheckMarks:.2f} s]")

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

    def decorate(self, image, marked, ok, holes, study=None):
        """Shapes only, no text (verdict and values are in the list and the terminal). BGR. Production: cyan circle =
        the segmented disc, thin yellow circle + cross = the hole search disc and its centre. Study (DOSTPLUS, CHANGES
        215): dark blue circle = the hub-hole circle (= the segmented disc), thin yellow circle + yellow cross = the
        hole search disc r135 round the hub centre, thick yellow = the reference oval on the hub centre, orange = the
        allowed region of the hole-pattern centre round the hub centre (CHANGES 217; north and south half-axes may
        differ), the top-face outline green (seated) / red."""
        BS = self.BS
        out = marked.copy() if marked is not None and marked.shape == image.shape else image.copy()
        if study is not None:
            hub, values = study
            if hub is not None and hub.get("found"):
                c = (int(round(hub["cx"])), int(round(hub["cy"])))
                cv2.circle(out, c, int(round(hub["radius"])), (160, 0, 0), 2)
                cv2.circle(out, c, int(BS.INNER_DISC_RADIUS_DOSTPLUS), (0, 255, 255), 1)
                cv2.drawMarker(out, c, (0, 255, 255), cv2.MARKER_CROSS, 16, 1)
                from _bung_hubcentre_study import HOLE_REGION        # allowed region of the hole centre (CHANGES 217)
                ew = int(round(HOLE_REGION["east_west"]))
                cv2.ellipse(out, c, (ew, int(round(HOLE_REGION["north"]))), 0, 180, 360, (0, 140, 255), 2)
                cv2.ellipse(out, c, (ew, int(round(HOLE_REGION["south"]))), 0, 0, 180, (0, 140, 255), 2)
                oval = BS.SEATING_REFERENCE_OVAL_DOSTPLUS
                cv2.ellipse(out, ((float(hub["cx"]), float(hub["cy"])), (float(oval[0]), float(oval[1])),
                                  float(oval[2])), (0, 255, 255), 3)
            if values and values.get("face_contour") is not None:
                cv2.drawContours(out, [values["face_contour"]], -1, (0, 170, 0) if ok else (0, 0, 220), 2)
            for x, y in holes:
                cv2.circle(out, (int(round(x)), int(round(y))), 4, (0, 0, 255), -1)
            colour = (0, 170, 0) if ok else (0, 0, 220)
            cv2.rectangle(out, (0, 0), (out.shape[1] - 1, out.shape[0] - 1), colour, 8)
            return out
        centre = tuple(int(v) for v in BS.OUTER_DISC_CENTER)
        cv2.circle(out, centre, int(BS.OUTER_DISC_RADIUS), (255, 255, 0), 1)
        cv2.circle(out, centre, int(BS.INNER_DISC_RADIUS), (0, 255, 255), 1)
        cv2.drawMarker(out, centre, (0, 255, 255), cv2.MARKER_CROSS, 16, 1)
        for x, y in holes:
            cv2.circle(out, (int(round(x)), int(round(y))), 4, (0, 0, 255), -1)
        colour = (0, 170, 0) if ok else (0, 0, 220)
        cv2.rectangle(out, (0, 0), (out.shape[1] - 1, out.shape[0] - 1), colour, 8)
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
                    messagebox.showerror("Bung check", data)
                elif kind == "busy":
                    self.status.set(data)
                elif kind == "shown":
                    self.show(*data)
                elif kind == "summary":
                    for line in data:
                        self.list.insert("end", line)
                    self.list.see("end")
                elif kind == "done":
                    okCount = sum(1 for r in self.summary if r)
                    self.status.set(f"Done: {len(self.summary)} check(s), model {self.model.get()}: {okCount} OK, "
                                    f"{len(self.summary) - okCount} NOT OK" +
                                    (f"  -  marked pictures in {self.folder.get()}/bung_results"
                                     if self.save.get() else ""))
                    self.startButton.config(state="normal")
                    self.stopButton.config(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def show(self, n, total, name, image, ok, reason, seconds):
        self.summary.append(ok)
        self.list.insert("end", f"{n:3}/{total}  {'OK    ' if ok else 'NOT OK'}  {name:42}  {seconds:5.2f} s  {reason}")
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
    sys.path.insert(0, str(HERE))
    from _hub_holes_option import HubHolesRecorder, applyHolesOption
    applyHolesOption()
    _recorder = HubHolesRecorder()
    window = tk.Tk()
    window.geometry("1180x960")
    BungCheckWindow(window)
    window.mainloop()
