"""
Study_Knuckle_Segmentation v1.0 - what MobileSAM can see on the knuckle picture (state 3), as the first step towards
vision checks that do not depend on the camera staying exactly where it is.

The MobileSAM automatic mask generator (a grid of point prompts over the picture) returns every mask it can find. The
study then names the parts of the knuckle among them. The rules use sizes and shapes, and positions relative to the
spindle (which is itself found as a round mask), so they are not tied to fixed picture coordinates:

  SPINDLE      round mask (fill of its enclosing circle >= 0.75), radius 35-65 px, within 80 px of the expected
               spindle position and the nearest to it (default 642,360 - the hub sits on it later; the hub centre is 645,334 because the
               hub top is nearer the camera)
  BOSS_L/R     the 2 machined areas on the upper arm: round (fill >= 0.70), radius 28-55 px, brighter than the cast
               arm (mean grey >= 80), 100-240 px above the spindle and 130-290 px to its left / right
  UPPER_ARM_L/R the cast (dark) part of the upper arm next to each boss: mean grey < 100, 6000-45000 px, centre
               20-220 px above the spindle on that side, touching the boss (none without the boss)
  LOWER_ARM    the whole lower arm: dark (mean grey < 75), long and bent (fill of its rotated box < 0.75, which
               leaves out the dark side blocks and the bar under the arm), 8000-70000 px, starting at the knuckle
               body (top 20-150 px below the spindle) and reaching more than 220 px below it; of those, the one
               reaching lowest, with its inner holes (paint dots) filled
               If no spindle is found, the other parts are placed from the expected position (and the log says so).
  side         LHS when the lower arm is left of the spindle, RHS when right (the names of the pictures say which
               side the knuckle is, so the study checks this)

Measurements printed for each picture (the basis for following a camera movement): spindle centre and radius, each
boss centre and radius relative to the spindle, the boss-to-boss distance (scale) and angle (rotation), the lower
arm's area, box and tip (its lowest point).

No text is drawn on the picture: masks are coloured, the named parts are outlined (boss green, upper arm yellow,
lower arm red, spindle cyan) and the values go to the list, the text box and the terminal.

Window:
  Folder / picture, points per side (grid of the generator: 16 fast ... 48 fine), "Segment" (one picture), "Segment
  all" (every picture in the folder, one after the other; results are kept, so going back to a picture is instant),
  "Save" (CSV of every mask and of the named parts, and the marked pictures, into a folder).
  Show: all masks / named parts only.
  Click on the picture: lists the masks under the point and outlines the smallest in white.
  Shift + click: a single MobileSAM point prompt at that point (3 masks, small to large) - to try a prompt that the
  check could use.
  Click a row of the list: outlines that mask in white.

Batch (no window):
  python test/machine2_dryrun/Study_Knuckle_Segmentation.py --batch <folder or picture> [--pps 32]
          [--save <folder>] [--csv <file>]

MobileSAM weights: models/MobileSAM/weights/mobile_sam.pt (or --sam <path>). On a CPU a 32 x 32 grid takes minutes per
picture; with the laptop GPU a few seconds.
"""

import argparse
import csv
import glob
import math
import os
import queue
import sys
import threading
import time
import warnings
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

warnings.filterwarnings("ignore", category=FutureWarning, module="timm")
warnings.filterwarnings("ignore", category=UserWarning, message="Overwriting .* in registry")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
try:
    from BaseUtils import get_project_root
    _PROJECT_ROOT = get_project_root()
except Exception:
    _PROJECT_ROOT = ROOT
_MOBILESAM_DIR = os.path.join(_PROJECT_ROOT, "models", "MobileSAM")
if os.path.exists(_MOBILESAM_DIR) and _MOBILESAM_DIR not in sys.path:
    sys.path.append(_MOBILESAM_DIR)

import torch

# Trusted local checkpoint (same handling as SAMSegmentation_v0.996)
_original_torch_load = torch.load


def _patched_torch_load(f, map_location=None, pickle_module=None, *, weights_only=None, mmap=None, **kwargs):
    return _original_torch_load(f, map_location=map_location, pickle_module=pickle_module, weights_only=False,
                                mmap=mmap, **kwargs)


torch.load = _patched_torch_load

try:
    from mobile_sam import sam_model_registry, SamPredictor, SamAutomaticMaskGenerator
except ImportError:
    sam_model_registry = SamPredictor = SamAutomaticMaskGenerator = None

IMAGE_SIZE = (1280, 720)
DEFAULT_FOLDER = os.path.join(HERE, "knuckle_images")
DEFAULT_SAM = os.path.join(_PROJECT_ROOT, "models", "MobileSAM", "weights", "mobile_sam.pt")

RULES = dict(
    spindle_expected=(642, 360), spindle_max_shift=80, spindle_r=(35, 65), round_fill_min=0.75,
    boss_r=(28, 55), boss_fill_min=0.70, boss_grey_min=80, boss_dy=(-240, -100), boss_dx=(130, 290),
    upper_grey_max=100, upper_area=(6000, 45000), upper_dy=(-220, -20),
    lower_grey_max=75, lower_area=(8000, 70000), lower_rect_fill_max=0.75, lower_reach=220, lower_top_dy=(20, 150),
)

COLOURS = {"BOSS_L": (0, 255, 0), "BOSS_R": (0, 255, 0), "UPPER_ARM_L": (255, 220, 0), "UPPER_ARM_R": (255, 220, 0),
           "LOWER_ARM": (255, 40, 40), "SPINDLE": (0, 230, 255)}


# ----------------------------------------------------------------------------------------------------------------------
# Segmentation and naming (no GUI)
# ----------------------------------------------------------------------------------------------------------------------

def load_picture(path: str) -> np.ndarray:
    image = cv2.imread(path)
    if image is None:
        raise IOError("cannot read " + path)
    if (image.shape[1], image.shape[0]) != IMAGE_SIZE:
        image = cv2.resize(image, IMAGE_SIZE)
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def fill_holes(mask: np.ndarray) -> np.ndarray:
    m = np.ascontiguousarray(mask, dtype=np.uint8) * 255
    cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    out = np.zeros(m.shape, np.uint8)
    cv2.drawContours(out, cs, -1, 255, -1)
    return out > 0


def describe(mask: np.ndarray, grey: np.ndarray, idx: int, iou: float = 0.0, stab: float = 0.0) -> Dict:
    """Shape and brightness values of one mask."""
    mask = np.ascontiguousarray(mask, dtype=bool)
    ys, xs = np.nonzero(mask)
    area = int(len(xs))
    m8 = mask.astype(np.uint8) * 255
    cs, _ = cv2.findContours(m8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    c = max(cs, key=cv2.contourArea)
    (ccx, ccy), cr = cv2.minEnclosingCircle(c)
    (_, _), (rw, rh), _ = cv2.minAreaRect(c)
    x, y, w, h = cv2.boundingRect(c)
    lowest = c[c[:, 0, 1].argmax(), 0]
    return dict(id=idx, mask=mask, area=area, cx=float(xs.mean()), cy=float(ys.mean()),
                circle_x=float(ccx), circle_y=float(ccy), radius=float(cr),
                round_fill=area / (math.pi * cr * cr) if cr > 0 else 0.0,
                rect_fill=area / (rw * rh) if rw * rh > 0 else 0.0,
                bbox=(int(x), int(y), int(w), int(h)), grey=float(grey[mask].mean()),
                lowest=(int(lowest[0]), int(lowest[1])), iou=float(iou), stability=float(stab), label="")


def touches(a: np.ndarray, b: np.ndarray, gap: int = 5) -> bool:
    k = np.ones((2 * gap + 1, 2 * gap + 1), np.uint8)
    return bool(np.any(cv2.dilate(a.astype(np.uint8), k) & b.astype(np.uint8)))


def name_parts(masks: List[Dict], grey: np.ndarray, rules: Dict = RULES) -> Dict:
    """Names the knuckle parts among the masks. Returns {label: mask dict} plus 'side' and 'measures'."""
    r = rules
    found: Dict = {}
    for m in masks:
        m["label"] = ""

    ex, ey = r["spindle_expected"]
    spindles = [m for m in masks if r["spindle_r"][0] <= m["radius"] <= r["spindle_r"][1]
                and m["round_fill"] >= r["round_fill_min"]
                and math.hypot(m["circle_x"] - ex, m["circle_y"] - ey) <= r["spindle_max_shift"]]
    sp = None
    if spindles:
        sp = min(spindles, key=lambda m: math.hypot(m["circle_x"] - ex, m["circle_y"] - ey))
        found["SPINDLE"] = sp
        sx, sy = sp["circle_x"], sp["circle_y"]
    else:                                   # not seen (coarse grid, empty fixture): place from the expected spot
        sx, sy = float(ex), float(ey)

    for side, sign in (("L", -1), ("R", 1)):
        bosses = [m for m in masks if m is not sp
                  and r["boss_r"][0] <= m["radius"] <= r["boss_r"][1] and m["round_fill"] >= r["boss_fill_min"]
                  and m["grey"] >= r["boss_grey_min"]
                  and r["boss_dy"][0] <= m["circle_y"] - sy <= r["boss_dy"][1]
                  and r["boss_dx"][0] <= sign * (m["circle_x"] - sx) <= r["boss_dx"][1]]
        if bosses:
            found["BOSS_" + side] = max(bosses, key=lambda m: m["round_fill"])
        boss = found.get("BOSS_" + side)
        uppers = [m for m in masks if m["grey"] < r["upper_grey_max"]
                  and r["upper_area"][0] <= m["area"] <= r["upper_area"][1]
                  and r["upper_dy"][0] <= m["cy"] - sy <= r["upper_dy"][1] and sign * (m["cx"] - sx) > 40
                  and boss is not None and touches(m["mask"], boss["mask"])]
        if uppers:
            found["UPPER_ARM_" + side] = max(uppers, key=lambda m: m["area"])

    lowers = [m for m in masks if m["grey"] < r["lower_grey_max"]
              and r["lower_area"][0] <= m["area"] <= r["lower_area"][1]
              and m["rect_fill"] < r["lower_rect_fill_max"]
              and m["bbox"][1] + m["bbox"][3] > sy + r["lower_reach"] and m["cy"] > sy + 60
              and r["lower_top_dy"][0] <= m["bbox"][1] - sy <= r["lower_top_dy"][1]]
    if lowers:
        lo = max(lowers, key=lambda m: m["bbox"][1] + m["bbox"][3])
        filled = fill_holes(lo["mask"])
        if int(filled.sum()) != lo["area"]:
            lo = describe(filled, grey, lo["id"], lo["iou"], lo["stability"])
        found["LOWER_ARM"] = lo
        found["side"] = "LHS" if lo["cx"] < sx else "RHS"

    for k, m in found.items():
        if isinstance(m, dict) and "id" in m:
            m["label"] = k
            for mm in masks:
                if mm["id"] == m["id"]:
                    mm["label"] = k

    meas = dict(spindle=(round(sx, 1), round(sy, 1), round(sp["radius"], 1) if sp else None))
    for side in ("L", "R"):
        b = found.get("BOSS_" + side)
        if b:
            meas["boss_" + side] = (round(b["circle_x"] - sx, 1), round(b["circle_y"] - sy, 1), round(b["radius"], 1))
    if "BOSS_L" in found and "BOSS_R" in found:
        bl, br = found["BOSS_L"], found["BOSS_R"]
        dx, dy = br["circle_x"] - bl["circle_x"], br["circle_y"] - bl["circle_y"]
        meas["boss_distance"] = round(math.hypot(dx, dy), 1)
        meas["boss_angle_deg"] = round(math.degrees(math.atan2(dy, dx)), 2)
    if "LOWER_ARM" in found:
        lo = found["LOWER_ARM"]
        meas["lower_area"] = lo["area"]
        meas["lower_bbox"] = lo["bbox"]
        meas["lower_tip"] = (lo["lowest"][0] - round(sx), lo["lowest"][1] - round(sy))
    found["measures"] = meas
    return found


class KnuckleMaskStudy:
    def __init__(self, sam_checkpoint: str, device: Optional[str] = None):
        if sam_model_registry is None:
            raise ImportError("mobile_sam is not importable (models/MobileSAM missing?)")
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = sam_model_registry["vit_t"](checkpoint=sam_checkpoint)
        self.model.to(self.device).eval()
        self.predictor = SamPredictor(self.model)
        self._generators: Dict[int, object] = {}
        self._lock = threading.Lock()

    def generator(self, pps: int):
        if pps not in self._generators:
            self._generators[pps] = SamAutomaticMaskGenerator(
                self.model, points_per_side=pps, pred_iou_thresh=0.86, stability_score_thresh=0.90,
                min_mask_region_area=300)
        return self._generators[pps]

    def segment(self, image_rgb: np.ndarray, pps: int = 32) -> Tuple[List[Dict], Dict, float]:
        grey = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
        t0 = time.time()
        with self._lock, torch.no_grad():
            raw = self.generator(pps).generate(image_rgb)
        raw.sort(key=lambda d: -d["area"])
        masks = [describe(d["segmentation"], grey, i, d["predicted_iou"], d["stability_score"])
                 for i, d in enumerate(raw)]
        found = name_parts(masks, grey)
        return masks, found, time.time() - t0

    def point_prompt(self, image_rgb: np.ndarray, x: int, y: int) -> List[Dict]:
        grey = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
        with self._lock, torch.no_grad():
            self.predictor.set_image(image_rgb)
            ms, scores, _ = self.predictor.predict(point_coords=np.array([[x, y]]), point_labels=np.array([1]),
                                                   multimask_output=True)
        out = [describe(m, grey, i, s, 0.0) for i, (m, s) in enumerate(zip(ms, scores)) if m.any()]
        return sorted(out, key=lambda d: d["area"])


def summary_lines(name: str, found: Dict, n: int, secs: float) -> List[str]:
    meas = found.get("measures", {})
    exp = "LHS" if "LHS" in name.upper() else ("RHS" if "RHS" in name.upper() else "?")
    side = found.get("side", "-")
    parts = [k for k in ("SPINDLE", "BOSS_L", "BOSS_R", "UPPER_ARM_L", "UPPER_ARM_R", "LOWER_ARM") if k in found]
    lines = [f"{name}: {n} masks in {secs:.1f} s; found {', '.join(parts) if parts else 'nothing'}; side {side}"
             + (f" (file name {exp}{'' if exp in ('?', side) else ' - DIFFERENT'})" if exp != "?" else "")]
    if "SPINDLE" in found:
        lines.append(f"    SPINDLE   centre {meas['spindle'][0]}, {meas['spindle'][1]}  r {meas['spindle'][2]}")
    elif "spindle" in meas:
        lines.append(f"    SPINDLE   not found - positions below are from the expected spindle position "
                     f"{meas['spindle'][0]:.0f}, {meas['spindle'][1]:.0f}")
    for s in ("L", "R"):
        b = meas.get("boss_" + s)
        if b:
            lines.append(f"    BOSS_{s}    from spindle {b[0]:+.1f}, {b[1]:+.1f}  r {b[2]}  "
                         f"(fill {found['BOSS_' + s]['round_fill']:.2f}, grey {found['BOSS_' + s]['grey']:.0f})")
    if "boss_distance" in meas:
        lines.append(f"    BOSSES    distance {meas['boss_distance']} px  angle {meas['boss_angle_deg']:+.2f} deg")
    for s in ("L", "R"):
        u = found.get("UPPER_ARM_" + s)
        if u:
            lines.append(f"    UPPER_ARM_{s} area {u['area']}  box {u['bbox']}  grey {u['grey']:.0f}")
    if "lower_area" in meas:
        lines.append(f"    LOWER_ARM area {meas['lower_area']}  box {meas['lower_bbox']}  "
                     f"tip from spindle {meas['lower_tip'][0]:+d}, {meas['lower_tip'][1]:+d}")
    return lines


def draw(image_rgb: np.ndarray, masks: List[Dict], found: Dict, show_all: bool = True,
         highlight: Optional[np.ndarray] = None) -> np.ndarray:
    """Coloured masks + outlines of the named parts; no text."""
    out = image_rgb.copy()
    if show_all:
        rng = np.random.default_rng(7)
        for m in masks:
            if m["area"] > 200000:
                continue
            c = rng.integers(40, 255, 3)
            sel = m["mask"]
            out[sel] = (0.55 * out[sel] + 0.45 * c).astype(np.uint8)
    for k, colour in COLOURS.items():
        m = found.get(k)
        if m is None:
            continue
        if not show_all:
            sel = m["mask"]
            out[sel] = (0.55 * out[sel] + 0.45 * np.array(colour)).astype(np.uint8)
        cs, _ = cv2.findContours(m["mask"].astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        cv2.drawContours(out, cs, -1, colour, 3)
    if highlight is not None:
        cs, _ = cv2.findContours(highlight.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        cv2.drawContours(out, cs, -1, (255, 255, 255), 3)
    return out


def list_pictures(path: str) -> List[str]:
    if os.path.isfile(path):
        return [path]
    files = []
    for ext in ("*.jpg", "*.jpeg", "*.png", "*.bmp"):
        files += glob.glob(os.path.join(path, ext))
    return sorted(set(files))


MASK_FIELDS = ["picture", "id", "label", "area", "cx", "cy", "circle_x", "circle_y", "radius", "round_fill",
               "rect_fill", "bbox", "grey", "iou", "stability"]


def mask_rows(name: str, masks: List[Dict]) -> List[Dict]:
    rows = []
    for m in masks:
        row = {"picture": name}
        for f in MASK_FIELDS[1:]:
            v = m[f]
            row[f] = round(v, 3) if isinstance(v, float) else (" ".join(map(str, v)) if isinstance(v, tuple) else v)
        rows.append(row)
    return rows


def save_results(folder: str, results: Dict[str, Tuple], csv_path: Optional[str] = None) -> str:
    os.makedirs(folder, exist_ok=True)
    csv_path = csv_path or os.path.join(folder, "knuckle_masks.csv")
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=MASK_FIELDS)
        w.writeheader()
        for name, (image, masks, found, secs) in results.items():
            w.writerows(mask_rows(name, masks))
            for show_all, tag in ((True, "all"), (False, "parts")):
                pic = draw(image, masks, found, show_all)
                cv2.imwrite(os.path.join(folder, f"{os.path.splitext(name)[0]}_{tag}.png"),
                            cv2.cvtColor(pic, cv2.COLOR_RGB2BGR))
    with open(os.path.join(folder, "knuckle_parts.txt"), "w") as fh:
        for name, (image, masks, found, secs) in results.items():
            fh.write("\n".join(summary_lines(name, found, len(masks), secs)) + "\n")
    return csv_path


def run_batch(args) -> None:
    study = KnuckleMaskStudy(args.sam)
    print(f"MobileSAM on {study.device}, grid {args.pps} x {args.pps}")
    results = {}
    for path in list_pictures(args.batch):
        image = load_picture(path)
        masks, found, secs = study.segment(image, args.pps)
        name = os.path.basename(path)
        results[name] = (image, masks, found, secs)
        print("\n".join(summary_lines(name, found, len(masks), secs)), flush=True)
    if args.save or args.csv:
        print("saved", save_results(args.save or os.path.dirname(os.path.abspath(args.csv)), results, args.csv))


# ----------------------------------------------------------------------------------------------------------------------
# Window
# ----------------------------------------------------------------------------------------------------------------------

def run_window(args) -> None:
    import tkinter as tk
    from tkinter import filedialog, ttk
    from PIL import Image, ImageTk

    root = tk.Tk()
    root.title("Knuckle segmentation study (MobileSAM)")
    VIEW_W, VIEW_H = 960, 540
    scale = VIEW_W / IMAGE_SIZE[0]

    state = dict(study=None, folder=args.folder, files=[], results={}, highlight=None, busy=False, prompt=None)
    jobs: "queue.Queue" = queue.Queue()

    top = ttk.Frame(root)
    top.pack(side="top", fill="x", padx=6, pady=4)
    folder_var = tk.StringVar(value=args.folder)
    pic_var = tk.StringVar()
    pps_var = tk.StringVar(value=str(args.pps))
    show_var = tk.StringVar(value="All masks")
    ttk.Button(top, text="Folder...", command=lambda: choose_folder()).pack(side="left")
    ttk.Label(top, textvariable=folder_var, width=40).pack(side="left", padx=4)
    ttk.Label(top, text="Picture").pack(side="left")
    pic_box = ttk.Combobox(top, textvariable=pic_var, width=34, state="readonly")
    pic_box.pack(side="left", padx=4)
    ttk.Label(top, text="Points per side").pack(side="left")
    ttk.Combobox(top, textvariable=pps_var, values=["16", "24", "32", "48"], width=4, state="readonly").pack(
        side="left", padx=4)
    ttk.Button(top, text="Segment", command=lambda: start([pic_var.get()])).pack(side="left", padx=2)
    ttk.Button(top, text="Segment all", command=lambda: start(list(state["files"]))).pack(side="left", padx=2)
    ttk.Button(top, text="Save...", command=lambda: save()).pack(side="left", padx=2)
    ttk.Label(top, text="Show").pack(side="left", padx=(8, 0))
    show_box = ttk.Combobox(top, textvariable=show_var, values=["All masks", "Named parts"], width=12,
                            state="readonly")
    show_box.pack(side="left", padx=4)

    mid = ttk.Frame(root)
    mid.pack(side="top", fill="both", expand=True)
    canvas = tk.Canvas(mid, width=VIEW_W, height=VIEW_H, bg="black")
    canvas.pack(side="left", padx=6)
    cols = ("id", "label", "area", "centre", "r", "fill", "grey")
    tree = ttk.Treeview(mid, columns=cols, show="headings", height=26)
    for c, w in zip(cols, (40, 95, 70, 90, 45, 45, 45)):
        tree.heading(c, text=c)
        tree.column(c, width=w, anchor="center")
    tree.pack(side="left", fill="y")
    sb = ttk.Scrollbar(mid, orient="vertical", command=tree.yview)
    sb.pack(side="left", fill="y")
    tree.configure(yscrollcommand=sb.set)

    log = tk.Text(root, height=12, font=("Courier", 9))
    log.pack(side="top", fill="both", expand=False, padx=6, pady=4)

    def say(text: str) -> None:
        print(text, flush=True)
        log.insert("end", text + "\n")
        log.see("end")

    def choose_folder():
        d = filedialog.askdirectory(initialdir=state["folder"])
        if d:
            set_folder(d)

    def set_folder(d):
        state["folder"] = d
        folder_var.set(d)
        state["files"] = [os.path.basename(f) for f in list_pictures(d)]
        pic_box["values"] = state["files"]
        if state["files"]:
            pic_var.set(state["files"][0])
            show()

    def current():
        name = pic_var.get()
        return name, state["results"].get((name, pps_var.get()))

    def show(*_):
        name, res = current()
        if not name:
            return
        if res:
            image, masks, found, secs = res
            pic = draw(image, masks, found, show_var.get() == "All masks", state["highlight"])
        else:
            pic = load_picture(os.path.join(state["folder"], name))
            if state["highlight"] is not None:
                pic = draw(pic, [], {}, False, state["highlight"])
        if state["prompt"] is not None:
            pic = draw(pic, [], {}, False, state["prompt"])
        img = ImageTk.PhotoImage(Image.fromarray(pic).resize((VIEW_W, VIEW_H)))
        canvas.image = img
        canvas.delete("all")
        canvas.create_image(0, 0, anchor="nw", image=img)
        tree.delete(*tree.get_children())
        if res:
            for m in res[1]:
                tree.insert("", "end", iid=str(m["id"]), values=(
                    m["id"], m["label"], m["area"], f"{m['cx']:.0f},{m['cy']:.0f}", f"{m['radius']:.0f}",
                    f"{m['round_fill']:.2f}", f"{m['grey']:.0f}"))

    def on_pick(*_):
        state["highlight"] = None
        state["prompt"] = None
        show()

    def on_tree(_):
        name, res = current()
        sel = tree.selection()
        if res and sel:
            m = next(mm for mm in res[1] if str(mm["id"]) == sel[0])
            state["highlight"] = m["mask"]
            state["prompt"] = None
            say(f"mask {m['id']} {m['label']}: area {m['area']}, centre {m['cx']:.1f},{m['cy']:.1f}, "
                f"circle r {m['radius']:.1f} (fill {m['round_fill']:.2f}), rect fill {m['rect_fill']:.2f}, "
                f"box {m['bbox']}, grey {m['grey']:.0f}, iou {m['iou']:.2f}, stability {m['stability']:.2f}")
            show()

    def on_click(event, prompt=False):
        x, y = int(event.x / scale), int(event.y / scale)
        name, res = current()
        if not name:
            return
        if prompt:
            if state["busy"] or state["study"] is None and not ensure_model():
                return
            image = res[0] if res else load_picture(os.path.join(state["folder"], name))
            state["busy"] = True
            say(f"point prompt at {x},{y} ...")
            threading.Thread(target=lambda: jobs.put(("prompt", name, (x, y), state["study"].point_prompt(
                image, x, y))), daemon=True).start()
            return
        if not res:
            say(f"{x},{y}: segment the picture first (or Shift + click for a point prompt)")
            return
        under = [m for m in res[1] if m["mask"][y, x]]
        if not under:
            say(f"{x},{y}: no mask")
            return
        say(f"{x},{y}: masks " + ", ".join(f"{m['id']}{('=' + m['label']) if m['label'] else ''} ({m['area']})"
                                         for m in sorted(under, key=lambda m: m["area"])))
        small = min(under, key=lambda m: m["area"])
        state["highlight"] = small["mask"]
        state["prompt"] = None
        if tree.exists(str(small["id"])):
            tree.see(str(small["id"]))
        show()

    def ensure_model() -> bool:
        if state["study"] is None:
            try:
                say(f"loading MobileSAM from {args.sam} ...")
                state["study"] = KnuckleMaskStudy(args.sam)
                say(f"MobileSAM on {state['study'].device}")
            except Exception as e:
                say(f"cannot load MobileSAM: {e}")
                return False
        return True

    def start(names):
        names = [n for n in names if n]
        if state["busy"] or not names or not ensure_model():
            return
        state["busy"] = True
        pps = int(pps_var.get())

        def work():
            for n in names:
                if (n, str(pps)) in state["results"]:
                    jobs.put(("done", n, str(pps), None))
                    continue
                image = load_picture(os.path.join(state["folder"], n))
                masks, found, secs = state["study"].segment(image, pps)
                jobs.put(("done", n, str(pps), (image, masks, found, secs)))
            jobs.put(("idle", None, None, None))

        say(f"segmenting {len(names)} picture(s), grid {pps} x {pps} ...")
        threading.Thread(target=work, daemon=True).start()

    def poll():
        try:
            while True:
                kind, name, extra, payload = jobs.get_nowait()
                if kind == "done":
                    if payload is not None:
                        state["results"][(name, extra)] = payload
                        image, masks, found, secs = payload
                        for line in summary_lines(name, found, len(masks), secs):
                            say(line)
                    pic_var.set(name)
                    state["highlight"] = None
                    state["prompt"] = None
                    show()
                elif kind == "idle":
                    state["busy"] = False
                    say("done")
                elif kind == "prompt":
                    state["busy"] = False
                    out = payload
                    for m in out:
                        say(f"    prompt mask {m['id']}: area {m['area']}, centre {m['cx']:.1f},{m['cy']:.1f}, "
                            f"circle r {m['radius']:.1f} (fill {m['round_fill']:.2f}), box {m['bbox']}, "
                            f"grey {m['grey']:.0f}, score {m['iou']:.2f}")
                    if out:
                        state["prompt"] = out[0]["mask"]
                        state["highlight"] = None
                        show()
        except queue.Empty:
            pass
        root.after(150, poll)

    def save():
        res = {n: v for (n, p), v in state["results"].items() if p == pps_var.get()}
        if not res:
            say("nothing segmented yet at this grid")
            return
        d = filedialog.askdirectory(initialdir=state["folder"], title="Folder for the results")
        if d:
            say("saved " + save_results(d, res))

    pic_box.bind("<<ComboboxSelected>>", on_pick)
    show_box.bind("<<ComboboxSelected>>", show)
    tree.bind("<<TreeviewSelect>>", on_tree)
    canvas.bind("<Button-1>", lambda e: on_click(e, False))
    canvas.bind("<Shift-Button-1>", lambda e: on_click(e, True))
    set_folder(args.folder)
    root.after(150, poll)
    if args.auto:
        root.after(500, lambda: start(list(state["files"])[:args.auto]))
        if args.quit_after:
            root.after(args.quit_after * 1000, root.destroy)
    root.mainloop()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--batch", help="folder or picture: run without the window")
    ap.add_argument("--folder", default=DEFAULT_FOLDER, help="window: first folder")
    ap.add_argument("--pps", type=int, default=32, help="points per side of the generator grid")
    ap.add_argument("--sam", default=DEFAULT_SAM, help="MobileSAM weights")
    ap.add_argument("--save", help="batch: folder for the CSV, the marked pictures and knuckle_parts.txt")
    ap.add_argument("--csv", help="batch: CSV file of every mask")
    ap.add_argument("--auto", type=int, default=0, help=argparse.SUPPRESS)        # test: segment the first N
    ap.add_argument("--quit-after", type=int, default=0, help=argparse.SUPPRESS)  # test: close after N s
    args = ap.parse_args()
    global sam_model_registry, SamPredictor, SamAutomaticMaskGenerator
    if sam_model_registry is None:          # MobileSAM next to the weights (<MobileSAM>/weights/mobile_sam.pt)
        sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(args.sam))))
        try:
            from mobile_sam import sam_model_registry, SamPredictor, SamAutomaticMaskGenerator
        except ImportError:
            pass
    if args.batch:
        run_batch(args)
    else:
        run_window(args)


if __name__ == "__main__":
    main()
