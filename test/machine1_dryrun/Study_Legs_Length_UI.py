"""
Study_Legs_Length_UI - the split pin legs as a length over the washer (Uddi, 2026-11-21; _legs_length.py): choose a
folder (OK\\ / NOT_OK\\ sub-folders give the label; else the pictures in it), the model, an optional hub picture, the
seconds between pictures (dropdown, 3 at start); Start runs every picture in turn and shows the washer enlarged:
  light green  the washer ring (filled): between the fitted outline and the inner edge found by the light-share jump
               (CHANGES 186; MobileSAM's mask when the ring is not found)
  red          the pin head (eye), where found; the legs' lines (skeleton of their imprint over the washer)
  text         the length of each leg and the total (px), "washer: NOT found" when there is none
Per picture the values go to the panel on the right, every log line to the log panel and the terminal, one line to
the list at the bottom (click it to see that picture again). When the folder is done or stopped: the summary (washer
present / not, the totals per label, the cut-off on the total that best splits OK from NOT OK, and with the
outside length as well). Optional: save the pictures to <folder>\\legs_length_results.

  --auto <folder> [--model DOST|DOSTPLUS]   no window: every picture, the lines and the summary on the terminal
  --legs-colour none|blue|both              the legs by not-washer colour (default none), the blue marker, or both
  --otsu-adjust N (0), --close-kernel 3|4|5|6|7 (4), --close-kernel-2 3|4 (4)   as the window's "Otsu +" and "Close" dropdowns
  --holes sam|morphology, --sam <mobile_sam.pt>, --yolo-conf  as Study_Washer_SAM

Run from the project root (laptop: GPU and the YOLO weights):
    python test/machine2_dryrun/Study_Legs_Length_UI.py [--auto <folder>] [--model DOSTPLUS] [--legs-colour none]
"""
import pathlib, queue, sys, threading, time, traceback

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import Study_Washer_SAM as S          # sets the paths, the vendor and cv2 / numpy
import cv2
import numpy as np
import _legs_length as L

COLOUR = S.arg("--legs-colour", "none")
OTSU = {"adjust": int(S.arg("--otsu-adjust", str(L.OTSU_ADJUST))),   # the window's dropdowns set them (-50 .. +50)
        "close": int(S.arg("--close-kernel", str(L.CLOSE_KERNEL))),
        "close2": int(S.arg("--close-kernel-2", str(L.CLOSE_KERNEL_2)))}  # 3 / 4     # 3 / 4 / 5 / 6 / 7
if COLOUR not in ("none", "blue", "both"):
    sys.exit(f"--legs-colour must be none, blue or both, not {COLOUR}")


def processOne(p, lab, model, models):
    """(row, picture to show or None, seconds) - the study's washer step, then the legs' length."""
    pred, FindHubHoles, SP, method, src = models
    t0 = time.perf_counter()
    r, g, im = S.processPicture(p, lab, model, pred, FindHubHoles, SP)
    if g is not None and im is not None:
        x0, y0 = g["x0"], g["y0"]
        g["crop"] = im[y0:y0 + 2 * S.CROP_HALF, x0:x0 + 2 * S.CROP_HALF].copy()
    t1 = time.perf_counter()
    res = L.measure(g, r, model, COLOUR, OTSU["adjust"], OTSU["close"], OTSU["close2"])
    r.update(washer_present=res["washerPresent"], washer_reason=res["washerReason"], legs_side=res["side"],
             legs=[round(x, 1) for x in res["legs"]], legs_total=round(res["total"], 1),
             inner_edge=None if res["innerEdge"] is None else round(res["innerEdge"], 1),
             outside_len=round(res["outside"], 1), outside_blue_len=round(res["outsideBlue"], 1),
             t_legs_length=round(time.perf_counter() - t1, 3))
    R = res.get("ring") or {}
    if R.get("found"):
        r.update(ring_inner_r=round(R["rInner"], 1), ring_estimate_r=round(R["rEstimate"], 1), ring_tips_r=round(R["rTips"], 1),
                 ring_valleys_r=round(R["rValleys"], 1), ring_jump=round(R["jump"], 3), otsu=R["otsu"],
                 dark_share=round(R["darkShare"], 3), dark_share_legs=round(R.get("darkShareLegs", float("nan")), 3),
                 dark_length=round(R["darkLength"], 3), dark_length_legs=round(R.get("darkLengthLegs", float("nan")), 3))
        S.log(f"   RING centroid ({R['centroid'][0] + g['x0']:.1f}, {R['centroid'][1] + g['y0']:.1f}) outline tips r {R['rTips']:.1f} "
              f"valleys r {R['rValleys']:.1f} -> inner estimate {R['rEstimate']:.1f}; close {R['close']}; Otsu {R['otsu']:.0f} {R['adjust']:+.0f} = "
              f"{R['threshold']:.0f}; inner edge r {R['rInner']:.1f} (light share jump {R['jump']:.2f}); dark share "
              f"{R['darkShare']:.3f} (legs half {R.get('darkShareLegs', float('nan')):.3f}); dark length {R['darkLength']:.3f} "
              f"(legs half {R.get('darkLengthLegs', float('nan')):.3f})")
    elif g is not None:
        S.log(f"   RING not found ({R.get('reason', 'no outline')})")
    S.log(f"   LEGS washer present {res['washerPresent']} ({res['washerReason']}); side: {res['side']}"
          + (f" axis {res['axis']:.0f} deg" if res["axis"] is not None else "")
          + f"; legs {' + '.join(f'{x:.0f}' for x in res['legs']) or 'none'} = total {res['total']:.0f} px"
          + f"; past the outline {res['outside']:.0f} px (blue {res['outsideBlue']:.0f}); colour {COLOUR}; "
            f"{r['t_legs_length']:.2f} s")
    if g is not None and g.get("crop") is not None:
        view = L.drawn(g, res)
    elif im is not None:
        view = cv2.resize(im[270:420, 570:720], None, fx=5, fy=5, interpolation=cv2.INTER_NEAREST)
    else:
        view = None
    r["_g"] = g                                                 # kept for redrawing at another Otsu adjustment
    return r, view, time.perf_counter() - t0


def redraw(r, model):
    """The legs measured again on the kept geometry at the current Otsu adjustment; (row values, picture)."""
    g = r.get("_g")
    if g is None or g.get("crop") is None:
        return None
    res = L.measure(g, r, model, COLOUR, OTSU["adjust"], OTSU["close"], OTSU["close2"])
    R = res.get("ring") or {}
    S.log(f"   REDRAW {r.get('name')} at Otsu {OTSU['adjust']:+d}, close {OTSU['close']} + {OTSU['close2']}: legs {' + '.join(f'{x:.0f}' for x in res['legs']) or 'none'} = "
          f"{res['total']:.0f} px" + (f"; dark share legs half {R.get('darkShareLegs', float('nan')):.3f}, dark length legs half "
                                      f"{R.get('darkLengthLegs', float('nan')):.3f}" if R.get("found") else ""))
    return L.drawn(g, res)


def summary(rows, model):
    S.log(f"\n================ LEGS LENGTH SUMMARY ({len(rows)} pictures, model {model}, colour {COLOUR}, Otsu {OTSU['adjust']:+d}, close {OTSU['close']} + {OTSU['close2']}) ================")
    labels = sorted({r.get("label") or "-" for r in rows})
    for lab in labels:
        rs = [r for r in rows if (r.get("label") or "-") == lab]
        present = [r for r in rs if r.get("washer_present")]
        S.log(f"{lab:7s} {len(rs)} pictures: washer present {len(present)}, not present {len(rs) - len(present)}"
              + "".join(f"\n      no washer: {r['name']} ({r.get('washer_reason')})" for r in rs if not r.get("washer_present")))
        tot = sorted(r["legs_total"] for r in present)
        outs = sorted(r["outside_len"] for r in present)
        if tot:
            S.log(f"        legs total   {tot[0]:.0f} .. {tot[-1]:.0f} (median {np.median(tot):.0f}): " + " ".join(f"{x:.0f}" for x in tot))
            S.log(f"        past outline {outs[0]:.0f} .. {outs[-1]:.0f} (median {np.median(outs):.0f}): " + " ".join(f"{x:.0f}" for x in outs))
    ok = [r for r in rows if r.get("label") == "OK" and r.get("washer_present")]
    bad = [r for r in rows if r.get("label") == "NOT OK" and r.get("washer_present")]
    if ok and bad:
        def errors(f):
            return sum(1 for r in ok if not f(r)), sum(1 for r in bad if f(r))
        best = min(((errors(lambda r, c=c: r["legs_total"] >= c), c) for c in range(0, 301)), key=lambda x: (sum(x[0]), x[1]))
        S.log(f"CUT on the total alone: OK when total >= {best[1]} px -> {best[0][0]} of {len(ok)} OK called NOT OK, "
              f"{best[0][1]} of {len(bad)} NOT OK called OK")
        both = min(((errors(lambda r, c=c, o=o: r["legs_total"] >= c and r["outside_len"] <= o), c, o)
                    for c in range(0, 301, 2) for o in range(0, 61)), key=lambda x: (sum(x[0]), x[1], -x[2]))
        S.log(f"CUT with the outside length too: OK when total >= {both[1]} and past the outline <= {both[2]} px -> "
              f"{both[0][0]} OK called NOT OK, {both[0][1]} NOT OK called OK")
        for key in ("dark_share", "dark_share_legs", "dark_length", "dark_length_legs"):
            S.log(f"        {key:17s} OK " + " ".join(f"{r[key]:.2f}" for r in sorted(ok, key=lambda r: r.get(key, 0)) if key in r)
                  + "\n" + " " * 26 + "NOT OK " + " ".join(f"{r[key]:.2f}" for r in sorted(bad, key=lambda r: r.get(key, 0)) if key in r))
            S.log("        " + cutLine(ok, bad, key))
        S.log("        " + bothLine(ok, bad, "dark_share_legs", "dark_length_legs"))
        S.log("        " + bothLine(ok, bad, "dark_share", "dark_length"))
    else:
        S.log("(no OK / NOT OK labels in this folder: no cut-off - put the pictures in OK\\ and NOT_OK\\ sub-folders)")
    times = [r.get("t_rest", 0) + r.get("t_holes", 0) for r in rows if r.get("t_rest") is not None]
    if times:
        S.log(f"time per picture {min(times):.2f} .. {max(times):.2f} s (median {np.median(times):.2f})")


def cutLine(ok, bad, key):
    """The best single cut on key, either direction: 'OK when key >= c' or 'OK when key <= c'."""
    vals = sorted({round(r[key], 3) for r in ok + bad if key in r and r[key] == r[key]})
    best = None
    for c in vals:
        for sign, word in ((1, ">="), (-1, "<=")):
            e1 = sum(1 for r in ok if not (sign * (r.get(key, 0) - c) >= 0))
            e2 = sum(1 for r in bad if sign * (r.get(key, 0) - c) >= 0)
            if best is None or e1 + e2 < best[0]:
                best = (e1 + e2, e1, e2, word, c)
    return (f"CUT {key}: OK when {key} {best[3]} {best[4]:.3f} -> {best[1]} of {len(ok)} OK called NOT OK, {best[2]} of "
            f"{len(bad)} NOT OK called OK") if best else f"CUT {key}: no values"


def bothLine(ok, bad, a, b):
    """The best pair of cuts on a and b together (each either direction)."""
    va = sorted({round(r[a], 3) for r in ok + bad if a in r and r[a] == r[a]})
    vb = sorted({round(r[b], 3) for r in ok + bad if b in r and r[b] == r[b]})
    best = None
    for ca in va:
        for cb in vb:
            for sa in (1, -1):
                for sb in (1, -1):
                    f = lambda r: sa * (r.get(a, 0) - ca) >= 0 and sb * (r.get(b, 0) - cb) >= 0
                    e1 = sum(1 for r in ok if not f(r)); e2 = sum(1 for r in bad if f(r))
                    if best is None or e1 + e2 < best[0]:
                        best = (e1 + e2, e1, e2, ca, cb, ">=" if sa > 0 else "<=", ">=" if sb > 0 else "<=")
    return (f"CUT {a} and {b}: OK when {a} {best[5]} {best[3]:.3f} and {b} {best[6]} {best[4]:.3f} -> {best[1]} OK called "
            f"NOT OK, {best[2]} NOT OK called OK") if best else "CUT both: no values"


def auto(folder, model):
    from _hub_holes_option import setHubPicture
    models = S.setUp()
    S.log(setHubPicture(None) + f" (model {model})")
    items = S.picturesIn(folder)
    S.log(f"SET {model}: {len(items)} pictures from {folder}; legs colour {COLOUR}; Otsu {OTSU['adjust']:+d}, close {OTSU['close']} + {OTSU['close2']}")
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
             ("prof_r", "washer outline r"), ("prof_a6", "6-fold (hex) amplitude"), ("legs_side", "legs side"),
             ("eye_angle", "eye angle"), ("inner_edge", "washer inner edge r"), ("legs", "legs (px)"),
             ("legs_total", "legs total (px)"), ("outside_len", "past the outline (px)"),
             ("outside_blue_len", "past the outline, blue (px)"), ("ring_tips_r", "outline tips r"),
             ("ring_valleys_r", "outline valleys r"), ("ring_estimate_r", "inner edge estimate r"),
             ("ring_inner_r", "inner edge r (jump)"), ("ring_jump", "light share jump"), ("otsu", "Otsu threshold"),
             ("dark_share", "dark share (ring)"), ("dark_share_legs", "dark share (legs half)"),
             ("dark_length", "dark length (ring)"), ("dark_length_legs", "dark length (legs half)"), ("fail", "study step failed"),
             ("t_holes", "time holes s"), ("t_rest", "time washer etc. s"), ("t_legs_length", "time legs s")]

    class Window:
        def __init__(self, root):
            self.root = root
            self.worker = None; self.stopFlag = threading.Event(); self.shown = threading.Event()
            self.models = None; self.rows = []; self.views = {}; self.photo = None
            root.title(f"Split pin legs as a length over the washer - study ({S.os.environ['COSTHETA_PLC_VENDOR']} settings, colour {COLOUR})")
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
            ttk.Label(bar, text="Otsu +").pack(side="left", padx=(10, 2))
            self.otsu = tk.StringVar(master=root, value=str(OTSU["adjust"]))
            oc = ttk.Combobox(bar, textvariable=self.otsu, values=[str(v) for v in range(-50, 51, 5)], state="readonly", width=4)
            oc.pack(side="left"); oc.bind("<<ComboboxSelected>>", self.otsuChanged)
            ttk.Label(bar, text="Close").pack(side="left", padx=(8, 2))
            self.close_ = tk.StringVar(master=root, value=str(OTSU["close"]))
            cc = ttk.Combobox(bar, textvariable=self.close_, values=["3", "4", "5", "6", "7"], state="readonly", width=3)
            cc.pack(side="left"); cc.bind("<<ComboboxSelected>>", self.otsuChanged)
            ttk.Label(bar, text="then").pack(side="left", padx=(4, 2))
            self.close2_ = tk.StringVar(master=root, value=str(OTSU["close2"]))
            c2 = ttk.Combobox(bar, textvariable=self.close2_, values=["3", "4"], state="readonly", width=3)
            c2.pack(side="left"); c2.bind("<<ComboboxSelected>>", self.otsuChanged)
            self.save = tk.BooleanVar(master=root, value=False)
            ttk.Checkbutton(bar, text="Save pictures", variable=self.save).pack(side="left", padx=8)
            self.startButton = ttk.Button(bar, text="Start", command=self.start); self.startButton.pack(side="left")
            self.stopButton = ttk.Button(bar, text="Stop", command=self.stop, state="disabled"); self.stopButton.pack(side="left", padx=4)

            mid = ttk.Frame(root); mid.pack(fill="both", expand=True, padx=6)
            left = ttk.Frame(mid); left.pack(side="left", fill="y")
            self.canvas = tk.Label(left, background="black", width=900, height=900); self.canvas.pack()
            leg = ttk.Frame(left); leg.pack(fill="x", pady=2)
            for col, text in (("#90ee90", "washer ring (outline to inner edge)"), ("#ff0000", "pin head; legs' lines (skeleton)")):
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

        def run(self, items, model, hub, folder):
            try:
                if self.models is None:
                    self.models = S.setUp()
                from _hub_holes_option import setHubPicture
                S.log(setHubPicture(hub) + f" (model {model})")
                S.log(f"SET {model}: {len(items)} pictures from {folder}; legs colour {COLOUR}")
                outDir = folder / "legs_length_results"
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
            line = (f"{p.name:44s} {r.get('label') or '-':6s} washer {'yes' if r.get('washer_present') else 'NO '} "
                    f"legs {' + '.join(f'{x:.0f}' for x in r.get('legs', [])) or '-'} = {r.get('legs_total', 0):.0f} px  "
                    f"past outline {r.get('outside_len', 0):.0f}  {secs:.1f} s"
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

        def otsuChanged(self, _):
            """A new Otsu adjustment: used from the next picture on; the picture shown is measured again with it."""
            try:
                OTSU["adjust"] = int(self.otsu.get()); OTSU["close"] = int(self.close_.get()); OTSU["close2"] = int(self.close2_.get())
            except ValueError:
                return
            S.log(f"Otsu adjustment now {OTSU['adjust']:+d}, closing kernels {OTSU['close']} then {OTSU['close2']} (from the next picture on; the picture shown is redrawn)")
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
