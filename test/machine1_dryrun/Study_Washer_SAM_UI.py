"""
Study_Washer_SAM_UI - a window over Study_Washer_SAM (CHANGES 181): choose a folder (OK\\ / NOT_OK\\ sub-folders give
the label; else the pictures in it), the model, an optional hub picture, the seconds between pictures (dropdown, 3 at
start); Start runs every picture in turn and shows it with everything drawn - outlines and markers only, no text on
the picture (the legend is beside it):
  blue        the hub's holes (small circles), their circle and its centre (thick cross)
  grey        the estimated centre = hole circle centre + the split pin offset (small cross)
  orange      the nut circle from MobileSAM and its centre (thick cross) = the spindle centre
  light blue  the washer mask from MobileSAM
  green       the fitted washer outline (12 scallops) and its centre (thick cross)
  purple      the nut edge used as the inner edge of the washer ring
  yellow      the eye direction (thick line), the two edges of the legs half (thin lines), leg masks (contours)
  red         pixels of the legs half that are not bright washer
  magenta     legs standing out past the outline
  white       the split pin mask from the eye prompt
The values of each picture are in the panel on the right, every log line in the log panel and on the terminal (as
Study_Washer_SAM prints them), one line per picture in the list at the bottom (click a line to see that picture again).
The summary (ranges per model and label, centres against the holes, cut-offs) is printed when the folder is done or
stopped. Optional: save the marked pictures to <folder>\\washer_study_results.

Run from the project root (laptop: GPU and the YOLO weights):
    python test/machine1_dryrun/Study_Washer_SAM_UI.py [--holes sam|morphology] [--sam <mobile_sam.pt>] [--yolo-conf 0.05]
"""
import pathlib, queue, sys, threading, time, traceback

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import Study_Washer_SAM as S          # sets the paths, the vendor and cv2 / numpy
import cv2
import numpy as np

LEGEND = [("blue", "#3060ff", "hub holes, their circle, centre (thick cross)"),
          ("grey", "#a0a0a0", "estimated centre = holes + split pin offset"),
          ("orange", "#ffa500", "nut circle (MobileSAM), centre = spindle"),
          ("light blue", "#78c8ff", "washer mask (MobileSAM)"),
          ("green", "#00c000", "fitted washer outline, its centre"),
          ("purple", "#c000c0", "nut edge = inner edge of the ring"),
          ("yellow", "#d0d000", "eye direction, legs half edges, leg masks"),
          ("red", "#ff0000", "legs half: not bright washer"),
          ("magenta", "#ff00ff", "legs standing out past the outline"),
          ("white", "#ffffff", "split pin mask from the eye")]
PANEL_KEYS = [("label", "label"), ("fail", "FAIL"),
              ("holes_cx", "holes centre x"), ("holes_cy", "holes centre y"), ("holes_r", "holes circle r"),
              ("nut_dx", "nut - holes dx"), ("nut_dy", "nut - holes dy"), ("nut_r", "nut r"), ("nut_sd", "nut circle sd"),
              ("prof_vs_holes_dx", "washer - holes dx"), ("prof_vs_holes_dy", "washer - holes dy"),
              ("prof_r", "washer outline r"), ("prof_a12", "12 scallops amplitude"), ("prof_a6", "6-fold (hex) amplitude"),
              ("prof_inliers", "outline angles used /360"), ("wash_set", "washer prompt set"), ("wash_shape", "washer mask shape"),
              ("wash_area", "washer mask area"), ("wash_iou", "washer mask IoU"),
              ("eye_angle", "eye angle"), ("eye_area", "eye area"), ("legs_area", "legs half area"),
              ("cov_legs", "legs half covered (colour)"), ("cov_mask_legs", "legs half not in mask"),
              ("stand_legs", "standing out (colour) px"), ("reach_legs", "reach past outline"),
              ("legs_cov", "grid legs: covered"), ("legs_beyond", "grid legs: beyond px"),
              ("yolo_boxes", "YOLO boxes"), ("ylegs_cov", "YOLO legs: covered"), ("ylegs_beyond", "YOLO legs: beyond px"),
              ("ylegs_reach", "YOLO legs: reach"), ("pin_set", "pin prompt set"), ("pin_area", "pin mask area"),
              ("pin_cov_legs", "pin: legs half covered"), ("pin_beyond_legs", "pin: beyond px"), ("blue_legs_px", "blue px (info)"),
              ("t_holes", "time holes s"), ("t_embed", "time embedding s"), ("t_washer", "time washer s"),
              ("t_yolo", "time YOLO legs s"), ("t_rest", "time after holes s")]


def main():
    import tkinter as tk
    from tkinter import filedialog, ttk
    from PIL import Image, ImageTk

    events = queue.Queue()
    termLog = S.log

    def uiLog(*a):                                   # every line to the terminal (as before) and to the log panel
        termLog(*a)
        events.put(("log", " ".join(str(x) for x in a)))

    S.log = uiLog

    class Window:
        def __init__(self, root):
            self.root = root
            self.worker = None; self.stopFlag = threading.Event(); self.shown = threading.Event()
            self.models = None; self.rows = []; self.views = {}; self.photo = None
            root.title(f"Washer and split pin legs by MobileSAM - study ({S.os.environ['COSTHETA_PLC_VENDOR']} settings)")
            bar = ttk.Frame(root); bar.pack(fill="x", padx=6, pady=4)
            ttk.Button(bar, text="Folder...", command=self.chooseFolder).pack(side="left")
            self.folder = tk.StringVar(master=root, value="")
            ttk.Label(bar, textvariable=self.folder, width=48).pack(side="left", padx=6)
            ttk.Label(bar, text="Model").pack(side="left", padx=(8, 2))
            self.model = tk.StringVar(master=root, value="DOST")
            ttk.Combobox(bar, textvariable=self.model, values=("DOST", "DOSTPLUS"), state="readonly", width=10).pack(side="left")
            ttk.Button(bar, text="Hub picture...", command=self.chooseHub).pack(side="left", padx=(10, 2))
            ttk.Button(bar, text="No hub picture", command=lambda: self.hub.set("")).pack(side="left")
            self.hub = tk.StringVar(master=root, value="")
            ttk.Label(bar, textvariable=self.hub, width=22).pack(side="left", padx=4)
            ttk.Label(bar, text="Seconds between pictures").pack(side="left", padx=(10, 2))
            self.seconds = tk.StringVar(master=root, value="3")
            ttk.Combobox(bar, textvariable=self.seconds, values=["0", "0.5", "1", "2", "3", "4", "5", "6", "8", "10", "15", "20"],
                         state="readonly", width=4).pack(side="left")
            ttk.Label(bar, text="View").pack(side="left", padx=(10, 2))
            self.viewKind = tk.StringVar(master=root, value="washer (zoom)")
            vk = ttk.Combobox(bar, textvariable=self.viewKind, values=("washer (zoom)", "hub holes (wide)"), state="readonly", width=15)
            vk.pack(side="left"); vk.bind("<<ComboboxSelected>>", lambda e: self.showSelected(None, last=True))
            self.save = tk.BooleanVar(master=root, value=False)
            ttk.Checkbutton(bar, text="Save marked pictures", variable=self.save).pack(side="left", padx=8)
            self.startButton = ttk.Button(bar, text="Start", command=self.start); self.startButton.pack(side="left")
            self.stopButton = ttk.Button(bar, text="Stop", command=self.stop, state="disabled"); self.stopButton.pack(side="left", padx=4)

            mid = ttk.Frame(root); mid.pack(fill="both", expand=True, padx=6)
            left = ttk.Frame(mid); left.pack(side="left", fill="y")
            self.canvas = tk.Label(left, background="black", width=700, height=700); self.canvas.pack()
            leg = ttk.Frame(left); leg.pack(fill="x", pady=2)
            for i, (name, col, text) in enumerate(LEGEND):
                f = ttk.Frame(leg); f.grid(row=i // 2, column=i % 2, sticky="w", padx=4)
                tk.Label(f, text="  ", background=col).pack(side="left")
                ttk.Label(f, text=f" {name}: {text}").pack(side="left")
            right = ttk.Frame(mid); right.pack(side="left", fill="both", expand=True, padx=(6, 0))
            ttk.Label(right, text="This picture").pack(anchor="w")
            self.panel = tk.Text(right, height=24, width=60, font=("Consolas", 10)); self.panel.pack(fill="x")
            ttk.Label(right, text="Log").pack(anchor="w", pady=(4, 0))
            lf = ttk.Frame(right); lf.pack(fill="both", expand=True)
            self.logBox = tk.Text(lf, height=12, width=60, font=("Consolas", 9), wrap="none")
            ys = ttk.Scrollbar(lf, command=self.logBox.yview); xs = ttk.Scrollbar(lf, orient="horizontal", command=self.logBox.xview)
            self.logBox.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
            xs.pack(side="bottom", fill="x"); ys.pack(side="right", fill="y"); self.logBox.pack(side="left", fill="both", expand=True)

            self.status = tk.StringVar(master=root, value="Choose a folder, the model (and a hub picture if wanted), then Start.")
            ttk.Label(root, textvariable=self.status).pack(fill="x", padx=6)
            bf = ttk.Frame(root); bf.pack(fill="both", padx=6, pady=4)
            self.listbox = tk.Listbox(bf, height=8, font=("Consolas", 10))
            sb = ttk.Scrollbar(bf, command=self.listbox.yview); self.listbox.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y"); self.listbox.pack(side="left", fill="both", expand=True)
            self.listbox.bind("<<ListboxSelect>>", self.showSelected)
            root.protocol("WM_DELETE_WINDOW", self.close)
            default = HERE / "splitpin_images"
            if default.is_dir():
                self.folder.set(str(default))
            self.poll()

        def chooseFolder(self):
            f = filedialog.askdirectory(title="Folder with split pin pictures (OK / NOT_OK sub-folders give the label)", parent=self.root)
            if f:
                self.folder.set(f)

        def chooseHub(self):
            f = filedialog.askopenfilename(title="Hub picture of the same part (state 5)", parent=self.root,
                                           filetypes=[("Pictures", "*.png *.jpg *.jpeg *.bmp")])
            if f:
                self.hub.set(f)

        def start(self):
            folder = self.folder.get()
            if not folder or not pathlib.Path(folder).is_dir():
                self.status.set("Choose a folder first."); return
            items = S.picturesIn(folder)
            hub = pathlib.Path(self.hub.get()).resolve() if self.hub.get() else None
            items = [(p, l) for p, l in items if hub is None or p.resolve() != hub]
            if not items:
                self.status.set(f"No pictures in {folder}."); return
            self.rows = []; self.views = {}; self.listbox.delete(0, "end")
            self.stopFlag.clear(); self.startButton.configure(state="disabled"); self.stopButton.configure(state="normal")
            self.status.set(f"{len(items)} pictures; loading the models (first time) ...")
            self.worker = threading.Thread(target=self.run, args=(items, self.model.get(), self.hub.get() or None,
                                                                  pathlib.Path(folder)), daemon=True)
            self.worker.start()

        def stop(self):
            self.stopFlag.set(); self.shown.set()
            self.status.set("Stopping after the current picture ...")

        def close(self):
            self.stopFlag.set(); self.shown.set()
            self.root.after(200, self.root.destroy)

        # ---- worker thread: models, every picture, the summary
        def run(self, items, model, hub, folder):
            try:
                if self.models is None:
                    self.models = S.setUp()
                pred, FindHubHoles, SP, method, src = self.models
                from _hub_holes_option import setHubPicture
                S.log(setHubPicture(hub) + f" (model {model})")
                S.log(f"SET {model}: {len(items)} pictures from {folder}")
                outDir = folder / "washer_study_results"
                for n, (p, lab) in enumerate(items, 1):
                    if self.stopFlag.is_set():
                        break
                    t0 = time.perf_counter()
                    r, g, im = S.processPicture(p, lab, model, pred, FindHubHoles, SP)
                    if g is not None:
                        view = {"zoom": S.drawFull(im, g, half=72, scale=5), "wide": S.drawFull(im, g, half=175, scale=2)}
                    elif im is not None:
                        view = {"zoom": cv2.resize(im[275:420, 570:715], None, fx=5, fy=5, interpolation=cv2.INTER_NEAREST),
                                "wide": cv2.resize(im[175:525, 470:820], None, fx=2, fy=2)}
                    else:
                        view = None
                    if view is not None and self.saveOn():
                        outDir.mkdir(parents=True, exist_ok=True)
                        stem = f"{model}_{lab.replace(' ', '_') or 'nolabel'}_{p.stem}"
                        cv2.imwrite(str(outDir / f"{stem}_washer.png"), view["zoom"])
                        cv2.imwrite(str(outDir / f"{stem}_holes.png"), view["wide"])
                    self.shown.clear()
                    events.put(("picture", n, len(items), p, r, view, time.perf_counter() - t0))
                    self.shown.wait()                                   # the window has shown it
                    gap = float(self.secondsValue())
                    end = time.monotonic() + gap
                    while time.monotonic() < end and not self.stopFlag.is_set():
                        time.sleep(0.05)
                rows = list(self.rows)
                S.log(f"\nSUMMARY of {len(rows)} pictures (model {model}, {folder})")
                S.summary(rows)
            except Exception:
                S.log("ERROR in the study run:\n" + traceback.format_exc())
            events.put(("done",))

        def saveOn(self):
            return bool(self.save.get())

        def secondsValue(self):
            try:
                return float(self.seconds.get())
            except ValueError:
                return 3.0

        # ---- window thread
        def poll(self):
            try:
                while True:
                    ev = events.get_nowait()
                    if ev[0] == "log":
                        self.logBox.insert("end", ev[1] + "\n"); self.logBox.see("end")
                    elif ev[0] == "picture":
                        self.showPicture(*ev[1:])
                        self.shown.set()
                    elif ev[0] == "done":
                        self.startButton.configure(state="normal"); self.stopButton.configure(state="disabled")
                        self.status.set(f"Done: {len(self.rows)} pictures - the summary is in the log panel and on the terminal")
            except queue.Empty:
                pass
            self.root.after(100, self.poll)

        def showPicture(self, n, total, p, r, view, secs):
            self.rows.append(r)
            if view is not None:
                self.views[len(self.rows) - 1] = {k: cv2.imencode(".png", v)[1] for k, v in view.items()}
                self.display(view[self.viewKey()])
            self.fillPanel(p, r)
            f = lambda k, fmt: (fmt % r[k]) if isinstance(r.get(k), (int, float)) else "-"
            line = (f"{p.name:44s} {r.get('label') or '-':6s} washer r {f('prof_r', '%.1f')} a12 {f('prof_a12', '%.2f')} "
                    f"cov {f('cov_legs', '%.2f')} grid {f('legs_cov', '%.2f')}/{f('legs_beyond', '%d')} "
                    f"YOLO {f('ylegs_cov', '%.2f')}/{f('ylegs_beyond', '%d')} nut-holes ({f('nut_dx', '%.1f')}, {f('nut_dy', '%.1f')}) "
                    f"{secs:.1f} s" + (f"  FAIL {r['fail']}" if r.get("fail") else ""))
            self.listbox.insert("end", line); self.listbox.see("end")
            self.status.set(f"Picture {n} of {total}: {p.name} ({r.get('label') or 'no label'}) - {secs:.1f} s")

        def display(self, view):
            from PIL import Image, ImageTk
            h, w = view.shape[:2]
            k = min(700 / w, 700 / h)
            img = cv2.resize(view, (int(w * k), int(h * k)), interpolation=cv2.INTER_NEAREST)
            self.photo = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))
            self.canvas.configure(image=self.photo, width=700, height=700)

        def fillPanel(self, p, r):
            self.panel.delete("1.0", "end")
            self.panel.insert("end", f"{p.name}\n")
            for k, name in PANEL_KEYS:
                if k in r and r[k] is not None:
                    v = r[k]
                    self.panel.insert("end", f"{name:30s} {v:.3f}\n" if isinstance(v, float) else f"{name:30s} {v}\n")

        def viewKey(self):
            return "wide" if self.viewKind.get().startswith("hub") else "zoom"

        def showSelected(self, _, last=False):
            sel = self.listbox.curselection()
            if not sel and not (last and self.views):
                return
            i = sel[0] if sel else max(self.views)
            if i in self.views:
                self.display(cv2.imdecode(self.views[i][self.viewKey()], cv2.IMREAD_COLOR))
            if i < len(self.rows):
                self.fillPanel(pathlib.Path(self.rows[i].get("name", "?")), self.rows[i])

    root = tk.Tk()
    Window(root)
    root.mainloop()


if __name__ == "__main__":
    main()
