"""
Study_Red_Legs_UI - the split pin legs by their red marking (Uddi, 2026-11-21 15:02; CHANGES 192; _legs_red.py). The
blue / not-washer-colour study stays in Study_Legs_Length_UI.
Choose a folder (OK\\ and NOT_OK\\ - or "Not OK" - sub-folders give the label; else the pictures in it), the model, an
optional hub picture, the seconds between pictures; Start runs every picture in turn and shows a 2 x 2 grid:
  (1) the washer mask (the ring between the fitted outline and the inner edge), light green on black;
  (2) the washer mask binarised: the red legs black, the rest of the ring white;
  (3) the red paint found on the picture (red; magenta = past the outline);
  (4) the picture with the washer mask light green and the legs red; the values.
Per picture: washer present, black share on the legs half of the ring, black length across the whole ring (and on the
legs half), how far the red reaches past the outline. The summary at the end: the values per label, the best cut on
each and on pairs.

  --auto <folder> [--model DOST|DOSTPLUS]   no window: every picture, the lines and the summary on the terminal
  --red-s-min N (35), --red-v-min N (30)    the red paint limits (the window's "Red S >=" / "V >=" dropdowns)
  --holes sam|morphology, --sam <mobile_sam.pt>  as Study_Washer_SAM
The hub's holes not found: the washer step from the fixed centre (vision.splitpin.centre) instead (HOLES line).

Run from the project root (laptop: GPU and the YOLO weights):
    python test/machine2_dryrun/Study_Red_Legs_UI.py [--auto <folder>] [--model DOST]
"""
import pathlib, queue, sys, threading, time, traceback

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import Study_Washer_SAM as S          # sets the paths, the vendor and cv2 / numpy
import cv2
import numpy as np
import _legs_red as LR
from Study_Legs_Length_UI import cutLine, bothLine

LR.RED_S_MIN = int(S.arg("--red-s-min", str(LR.RED_S_MIN)))
LR.RED_V_MIN = int(S.arg("--red-v-min", str(LR.RED_V_MIN)))

NOT_OK_NAMES = ("NOT_OK", "Not OK", "NOT OK", "Not_OK", "not ok", "Not ok", "NotOK")


def picturesIn(folder):
    """[(path, label)] from OK and NOT_OK / "Not OK" sub-folders if there are any, else the folder itself (no label)."""
    folder = pathlib.Path(folder); items = []
    if (folder / "OK").is_dir():
        items += [(p, "OK") for p in sorted((folder / "OK").iterdir()) if p.suffix.lower() in S.EXT]
    for name in NOT_OK_NAMES:
        if (folder / name).is_dir():
            items += [(p, "NOT OK") for p in sorted((folder / name).iterdir()) if p.suffix.lower() in S.EXT]
            break
    if not items:
        items = [(p, "") for p in sorted(folder.iterdir()) if p.suffix.lower() in S.EXT]
    return items


def processOne(p, lab, model, models):
    """(row, picture to show or None, seconds)."""
    pred, FindHubHoles, SP, method, src = models
    t0 = time.perf_counter()
    r, g, im = S.processPicture(p, lab, model, pred, FindHubHoles, SP)
    if r.get("fail") == "holes not found" and im is not None:
        # the hub's holes not found: the washer step from the fixed centre (vision.splitpin.centre) instead, so the
        # legs can still be looked at - logged; the production check would be NOT OK here
        off = tuple(SP.HUB_OFFSET_DOSTPLUS if model == "DOSTPLUS" else SP.HUB_OFFSET_DOST)
        fake = dict(cx=float(SP.CENTRE[0]) - float(off[0]), cy=float(SP.CENTRE[1]) - float(off[1]), radius=None, points=None)
        S.log(f"   HOLES not found - washer step from the fixed centre {tuple(SP.CENTRE)} instead")
        t1 = time.perf_counter()
        r2, g = S.analyse(im, model, pred, fake, off)
        r2.update(model=model, label=lab, name=p.name, holes_fallback="fixed centre", t_holes=r.get("t_holes", 0),
                  t_rest=time.perf_counter() - t1)
        r = r2
    if g is not None and im is not None:
        x0, y0 = g["x0"], g["y0"]
        g["crop"] = im[y0:y0 + 2 * S.CROP_HALF, x0:x0 + 2 * S.CROP_HALF].copy()
    t1 = time.perf_counter()
    res = LR.measureRed(g, r, model)
    R = res.get("ring") or {}
    r.update(washer_present=res["washerPresent"], washer_reason=res["washerReason"], legs_side=res["side"],
             reach=round(res["reach"], 1), t_red=round(time.perf_counter() - t1, 3))
    for k_out, k_in in (("black_share_legs", "blackShareLegs"), ("black_share", "blackShare"),
                        ("black_length", "blackLength"), ("black_length_legs", "blackLengthLegs")):
        if res.get(k_in) is not None:
            r[k_out] = round(res[k_in], 3)
    if R.get("found"):
        r.update(ring_inner_r=round(R["rInner"], 1), ring_tips_r=round(R["rTips"], 1))
    S.log(f"   RED washer present {res['washerPresent']} ({res['washerReason']}); side: {res['side']}"
          + (f" axis {res['axis']:.0f} deg" if res["axis"] is not None else "")
          + (f"; black share legs half {res['blackShareLegs']:.3f} (whole {res['blackShare']:.3f}); black length whole "
             f"{res['blackLength']:.3f} (legs half {res['blackLengthLegs']:.3f})" if res.get("blackShareLegs") is not None else "")
          + f"; reach past the outline {res['reach']:.0f} px; {r['t_red']:.2f} s")
    if g is not None and g.get("crop") is not None and R.get("found"):
        view = LR.drawnRed(g, res)
    elif im is not None:
        view = cv2.resize(im[270:420, 570:720], None, fx=5, fy=5, interpolation=cv2.INTER_NEAREST)
    else:
        view = None
    r["_g"] = g                                                 # kept for redrawing at other red limits
    return r, view, time.perf_counter() - t0


def redraw(r, model):
    """The red legs measured again on the kept geometry at the current red limits (the window's dropdowns)."""
    g = r.get("_g")
    if g is None or g.get("crop") is None:
        return None
    res = LR.measureRed(g, r, model)
    S.log(f"   REDRAW {r.get('name')} at red S >= {LR.RED_S_MIN}, V >= {LR.RED_V_MIN}: black share legs half "
          f"{res.get('blackShareLegs') if res.get('blackShareLegs') is None else round(res['blackShareLegs'], 3)}, black length "
          f"{res.get('blackLength') if res.get('blackLength') is None else round(res['blackLength'], 3)}, reach {res['reach']:.0f} px")
    return LR.drawnRed(g, res) if (res.get("ring") or {}).get("found") else None


KEYS = ("black_share_legs", "black_length", "black_length_legs", "black_share", "reach")


def summary(rows, model):
    S.log(f"\n================ RED LEGS SUMMARY ({len(rows)} pictures, model {model}, red S >= {LR.RED_S_MIN}, V >= {LR.RED_V_MIN}) ================")
    for lab in sorted({r.get("label") or "-" for r in rows}):
        rs = [r for r in rows if (r.get("label") or "-") == lab]
        present = [r for r in rs if r.get("washer_present")]
        S.log(f"{lab:7s} {len(rs)} pictures: washer present {len(present)}, not present {len(rs) - len(present)}"
              + "".join(f"\n      no washer: {r['name']} ({r.get('washer_reason')})" for r in rs if not r.get("washer_present")))
        for k in KEYS:
            v = sorted(r[k] for r in present if k in r)
            if v:
                S.log(f"        {k:17s} {v[0]:.3f} .. {v[-1]:.3f} (median {np.median(v):.3f}): " + " ".join(f"{x:.2f}" for x in v))
    ok = [r for r in rows if r.get("label") == "OK" and r.get("washer_present") and "black_share_legs" in r]
    bad = [r for r in rows if r.get("label") == "NOT OK" and r.get("washer_present") and "black_share_legs" in r]
    if ok and bad:
        for k in KEYS:
            S.log("        " + cutLine(ok, bad, k))
        for a, b in (("black_share_legs", "black_length"), ("black_share_legs", "reach"), ("black_length", "reach"),
                     ("black_length_legs", "reach")):
            S.log("        " + bothLine(ok, bad, a, b))
        S.log("NOT OK pictures with no washer are NOT OK by the washer check and are left out of the cuts.")
    else:
        S.log("(no OK / NOT OK labels in this folder: no cut-off - put the pictures in OK\\ and NOT_OK\\ sub-folders)")
    times = [r.get("t_rest", 0) + r.get("t_holes", 0) for r in rows if r.get("t_rest") is not None]
    if times:
        S.log(f"time per picture {min(times):.2f} .. {max(times):.2f} s (median {np.median(times):.2f})")


def auto(folder, model):
    from _hub_holes_option import setHubPicture
    models = S.setUp()
    S.log(setHubPicture(None) + f" (model {model})")
    items = picturesIn(folder)
    S.log(f"SET {model}: {len(items)} pictures from {folder}; red marking")
    rows = []
    for p, lab in items:
        r, _, _ = processOne(p, lab, model, models)
        rows.append(r)
    summary(rows, model)


def main():
    import tkinter as tk
    from tkinter import filedialog, ttk

    events = queue.Queue()
    termLog = S.log

    def uiLog(*a):                                   # every line to the terminal and to the log panel
        termLog(*a)
        events.put(("log", " ".join(str(x) for x in a)))

    S.log = uiLog
    PANEL = [("label", "label"), ("washer_present", "washer present"), ("washer_reason", "washer"),
             ("prof_r", "washer outline r"), ("ring_tips_r", "outline tips r"), ("ring_inner_r", "inner edge r"),
             ("legs_side", "legs side"), ("black_share_legs", "black share, legs half"), ("black_share", "black share, whole"),
             ("black_length", "black length, whole"), ("black_length_legs", "black length, legs half"),
             ("reach", "reach past the outline px"), ("fail", "study step failed"),
             ("t_holes", "time holes s"), ("t_rest", "time washer etc. s"), ("t_red", "time red s")]

    class Window:
        def __init__(self, root):
            self.root = root
            self.worker = None; self.stopFlag = threading.Event(); self.shown = threading.Event()
            self.models = None; self.rows = []; self.views = {}; self.photo = None
            root.title(f"Split pin legs by the red marking - study ({S.os.environ['COSTHETA_PLC_VENDOR']} settings)")
            bar = ttk.Frame(root); bar.pack(fill="x", padx=6, pady=4)
            ttk.Button(bar, text="Folder...", command=self.chooseFolder).pack(side="left")
            self.folder = tk.StringVar(master=root, value="")
            ttk.Label(bar, textvariable=self.folder, width=48).pack(side="left", padx=6)
            ttk.Label(bar, text="Model").pack(side="left", padx=(8, 2))
            self.model = tk.StringVar(master=root, value=S.arg("--model", "DOST").upper())
            ttk.Combobox(bar, textvariable=self.model, values=("DOST", "DOSTPLUS"), state="readonly", width=10).pack(side="left")
            ttk.Button(bar, text="Hub picture...", command=self.chooseHub).pack(side="left", padx=(10, 2))
            ttk.Button(bar, text="No hub picture", command=lambda: self.hub.set("")).pack(side="left")
            self.hub = tk.StringVar(master=root, value="")
            ttk.Label(bar, textvariable=self.hub, width=22).pack(side="left", padx=4)
            ttk.Label(bar, text="Seconds between pictures").pack(side="left", padx=(10, 2))
            self.seconds = tk.StringVar(master=root, value="3")
            ttk.Combobox(bar, textvariable=self.seconds, values=["0", "0.5", "1", "2", "3", "4", "5", "6", "8", "10", "15", "20"],
                         state="readonly", width=4).pack(side="left")
            ttk.Label(bar, text="Red S >=").pack(side="left", padx=(10, 2))
            self.sMin = tk.StringVar(master=root, value=str(LR.RED_S_MIN))
            sc = ttk.Combobox(bar, textvariable=self.sMin, values=[str(v) for v in range(20, 71, 5)], state="readonly", width=3)
            sc.pack(side="left"); sc.bind("<<ComboboxSelected>>", self.redChanged)
            ttk.Label(bar, text="V >=").pack(side="left", padx=(6, 2))
            self.vMin = tk.StringVar(master=root, value=str(LR.RED_V_MIN))
            vc = ttk.Combobox(bar, textvariable=self.vMin, values=[str(v) for v in range(15, 61, 5)], state="readonly", width=3)
            vc.pack(side="left"); vc.bind("<<ComboboxSelected>>", self.redChanged)
            self.save = tk.BooleanVar(master=root, value=False)
            ttk.Checkbutton(bar, text="Save pictures", variable=self.save).pack(side="left", padx=8)
            self.startButton = ttk.Button(bar, text="Start", command=self.start); self.startButton.pack(side="left")
            self.stopButton = ttk.Button(bar, text="Stop", command=self.stop, state="disabled"); self.stopButton.pack(side="left", padx=4)

            mid = ttk.Frame(root); mid.pack(fill="both", expand=True, padx=6)
            left = ttk.Frame(mid); left.pack(side="left", fill="y")
            self.canvas = tk.Label(left, background="black", width=900, height=900); self.canvas.pack()
            leg = ttk.Frame(left); leg.pack(fill="x", pady=2)
            for col, text in (("#90ee90", "washer ring (outline to inner edge)"), ("#ff0000", "red paint (legs)"), ("#ff00ff", "red past the outline"), ("#ffc800", "pin head")):
                f = ttk.Frame(leg); f.pack(side="left", padx=6)
                tk.Label(f, text="  ", background=col).pack(side="left")
                ttk.Label(f, text=" " + text).pack(side="left")
            right = ttk.Frame(mid); right.pack(side="left", fill="both", expand=True, padx=(6, 0))
            ttk.Label(right, text="This picture").pack(anchor="w")
            self.panel = tk.Text(right, height=18, width=64, font=("Consolas", 10)); self.panel.pack(fill="x")
            ttk.Label(right, text="Log").pack(anchor="w", pady=(4, 0))
            lf = ttk.Frame(right); lf.pack(fill="both", expand=True)
            self.logBox = tk.Text(lf, height=14, width=64, font=("Consolas", 9), wrap="none")
            ys = ttk.Scrollbar(lf, command=self.logBox.yview); xs = ttk.Scrollbar(lf, orient="horizontal", command=self.logBox.xview)
            self.logBox.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
            xs.pack(side="bottom", fill="x"); ys.pack(side="right", fill="y"); self.logBox.pack(side="left", fill="both", expand=True)

            self.status = tk.StringVar(master=root, value="Choose a folder and the model, then Start.")
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
            items = picturesIn(folder)
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

        def run(self, items, model, hub, folder):
            try:
                if self.models is None:
                    self.models = S.setUp()
                from _hub_holes_option import setHubPicture
                S.log(setHubPicture(hub) + f" (model {model})")
                S.log(f"SET {model}: {len(items)} pictures from {folder}; red marking")
                outDir = folder / "red_legs_results"
                for n, (p, lab) in enumerate(items, 1):
                    if self.stopFlag.is_set():
                        break
                    r, view, secs = processOne(p, lab, model, self.models)
                    if view is not None and bool(self.save.get()):
                        outDir.mkdir(parents=True, exist_ok=True)
                        cv2.imwrite(str(outDir / f"{model}_{lab.replace(' ', '_') or 'nolabel'}_{p.stem}.png"), view)
                    self.shown.clear()
                    events.put(("picture", n, len(items), p, r, view, secs))
                    self.shown.wait()
                    try:
                        gap = float(self.seconds.get())
                    except ValueError:
                        gap = 3.0
                    end = time.monotonic() + gap
                    while time.monotonic() < end and not self.stopFlag.is_set():
                        time.sleep(0.05)
                summary(list(self.rows), model)
            except Exception:
                S.log("ERROR in the study run:\n" + traceback.format_exc())
            events.put(("done",))

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
                self.views[len(self.rows) - 1] = cv2.imencode(".png", view)[1]
                self.display(view)
            self.fillPanel(p, r)
            f = lambda k: ("%.3f" % r[k]) if isinstance(r.get(k), float) else "-"
            line = (f"{p.name:44s} {r.get('label') or '-':6s} washer {'yes' if r.get('washer_present') else 'NO '} "
                    f"black share legs half {f('black_share_legs')}  black length {f('black_length')}  "
                    f"reach {r.get('reach', 0):.0f} px  {secs:.1f} s"
                    + (f"  ({r['legs_side']})" if r.get("legs_side") and "no eye" in r["legs_side"] else ""))
            self.listbox.insert("end", line); self.listbox.see("end")
            self.status.set(f"Picture {n} of {total}: {p.name} ({r.get('label') or 'no label'}) - {secs:.1f} s")

        def display(self, view):
            from PIL import Image, ImageTk
            h, w = view.shape[:2]
            k = min(900 / w, 900 / h)
            img = cv2.resize(view, (int(w * k), int(h * k)), interpolation=cv2.INTER_NEAREST)
            self.photo = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))
            self.canvas.configure(image=self.photo, width=900, height=900)

        def fillPanel(self, p, r):
            self.panel.delete("1.0", "end")
            self.panel.insert("end", f"{p.name}\n")
            for k, name in PANEL:
                if k in r and r[k] is not None:
                    v = r[k]
                    self.panel.insert("end", f"{name:30s} {v:.3f}\n" if isinstance(v, float) else f"{name:30s} {v}\n")

        def redChanged(self, _):
            """New red limits: used from the next picture on; the picture shown (or selected) is measured again."""
            try:
                LR.RED_S_MIN = int(self.sMin.get()); LR.RED_V_MIN = int(self.vMin.get())
            except ValueError:
                return
            S.log(f"red limits now S >= {LR.RED_S_MIN}, V >= {LR.RED_V_MIN} (from the next picture on; the picture shown is redrawn)")
            sel = self.listbox.curselection()
            i = sel[0] if sel else (len(self.rows) - 1)
            if 0 <= i < len(self.rows) and (self.worker is None or not self.worker.is_alive() or sel):
                view = redraw(self.rows[i], self.rows[i].get("model", self.model.get()))
                if view is not None:
                    self.views[i] = cv2.imencode(".png", view)[1]
                    self.display(view)

        def showSelected(self, _):
            sel = self.listbox.curselection()
            if not sel:
                return
            i = sel[0]
            if i in self.views:
                self.display(cv2.imdecode(self.views[i], cv2.IMREAD_COLOR))
            if i < len(self.rows):
                self.fillPanel(pathlib.Path(self.rows[i].get("name", "?")), self.rows[i])

    root = tk.Tk()
    Window(root)
    root.mainloop()


if __name__ == "__main__":
    folderArg = S.arg("--auto")
    if folderArg:
        auto(pathlib.Path(folderArg), S.arg("--model", "DOST").upper())
    else:
        main()
