"""
SAMSegmentation_CapSeating v1.0 - segments the cap on the cap picture and the hub bore (the "hole" of the hub check),
and compares them to tell a seated cap from a skewed one. Derived from SAMSegmentation_v0.996 (MobileSAMv2 viewer):
same MobileSAM loading and torch.load handling, but no YOLO, no hole counting and no normalisation pipeline - the cap and
the bore are single large objects, found with one point prompt (cap picture) or a box + 4 points (hub picture).

What it finds (all in the 1280x720 picture the checks use):
  Cap picture, one point at CX,CY, multimask (SAM returns 3 masks, small -> large):
    brass (DOSTPLUS) cap : the mask with radius in the brass range (default 75-95)  - the cap face
    silver (DOST) cap    : the mask with radius in the silver range (default 95-112) - the cap outline
    bore (brass only)    : the mask with radius in the bore range (default 104-120) - the bore + chamfer around the cap;
                           a silver cap covers the bore, so for silver the bore must come from the hub picture
  Hub picture (optional, same part), box CX,CY +-140 and 4 points at 55 px round CX,CY:
    bore                 : the mask with radius in the bore range
Model: from the cap colour (mean R-B in r60 round CX,CY > 25 = brass) unless set in the dropdown.

  Silver cap top face, on the cap picture: box = the cap outline's centre +- 0.8 x its radius, point at that centre;
                           the smallest mask. A tilted silver cup keeps its body in the bore but its top face slides
                           off-centre in its own outline (the side wall shows on one side).

Results:
  brass  : offset = brass cap centre - bore centre (bore from the same cap picture, or the hub picture)
  silver : face offset = top face centre - cap outline centre (self-referenced: no bore and no fixed centre needed);
           with a hub picture, the outline-to-bore offset is shown too (minus the expected offset of the model: a seated
           tall cap is seen a few px off the bore centre, the camera is not exactly overhead)
  axis ratio (minor / major) of the cap, shown; used only if ratio min > 0.
Seated when the offset (brass) / face offset (silver) is within its limit. The limits are entry boxes - this is a tool
to find them. First measurements (13 Nov pictures): brass cap-to-bore seated 0.8-1.7 px, skewed 16.0-17.6; silver
face offset seated 1.6-4.3 px, skewed 14.4-20.2.

Bore from: "cap picture" (brass only), "hub picture" (a file, or a folder paired by sorted order), or "none" (bore =
CX,CY - the fixed centre, as the check does today).

Run:
  GUI:   python test/SAMSegmentation_CapSeating.py
  Batch: python test/SAMSegmentation_CapSeating.py --batch <cap folder or file> [--hub <hub file or folder>]
                 [--csv out.csv] [--save <folder for marked pictures>]
  MobileSAM weights: models/MobileSAM/weights/mobile_sam.pt (or --sam <path>).
"""

import argparse
import csv
import glob
import os
import sys
import time
import warnings
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

warnings.filterwarnings("ignore", category=FutureWarning, module="timm")
warnings.filterwarnings("ignore", category=UserWarning, message="Overwriting .* in registry")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
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
    from mobile_sam import sam_model_registry, SamPredictor
except ImportError:
    sam_model_registry = None
    SamPredictor = None

IMAGE_SIZE = (1280, 720)
DOST, DOSTPLUS = "DOST", "DOSTPLUS"

DEFAULTS = dict(
    cx=644, cy=340,
    brass_r=(75, 95), silver_r=(95, 112), bore_r=(104, 120),
    brass_rb_min=25.0,                       # mean R-B within r60 above this = brass cap
    expected_offset={DOSTPLUS: (0.0, 0.0), DOST: (0.0, 0.0)},
    offset_limit={DOSTPLUS: 9.0, DOST: 9.0},     # brass: cap to bore; silver: outline to bore (only with a bore)
    face_offset_limit=9.0,                       # silver: top face to cap outline
    face_box_scale=0.8,
    ratio_min={DOSTPLUS: 0.0, DOST: 0.0},        # 0 = not used
)


def ellipse_of(mask: np.ndarray) -> Optional[Dict]:
    """Ellipse fitted to the largest outer contour of a mask: centre, semi-axes, ratio, contour."""
    m = mask.astype(np.uint8) * 255
    cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cs:
        return None
    c = max(cs, key=cv2.contourArea)
    if len(c) < 5:
        return None
    (x, y), (a, b), ang = cv2.fitEllipse(c)
    major, minor = max(a, b) / 2.0, min(a, b) / 2.0
    return dict(cx=x, cy=y, major=major, minor=minor, ratio=minor / major if major else 0.0, angle=ang,
                radius=(major + minor) / 2.0, area=float(cv2.contourArea(c)), contour=c)


def pick(fits: List[Optional[Dict]], rng: Tuple[float, float]) -> Optional[Dict]:
    """The mask whose mean radius is inside rng (closest to its middle if several)."""
    mid = (rng[0] + rng[1]) / 2.0
    ok = [f for f in fits if f is not None and rng[0] <= f["radius"] <= rng[1]]
    return min(ok, key=lambda f: abs(f["radius"] - mid)) if ok else None


class CapBoreSegmenter:
    """MobileSAM cap and bore segmentation, with no GUI (used by the GUI and by the batch mode)."""

    def __init__(self, sam_checkpoint: str, device: Optional[str] = None, **settings):
        if sam_model_registry is None:
            raise ImportError("mobile_sam is not importable (models/MobileSAM missing?)")
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        model = sam_model_registry["vit_t"](checkpoint=sam_checkpoint)
        model.to(self.device).eval()
        self.predictor = SamPredictor(model)
        self.s = {k: (v.copy() if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
        self.s.update(settings)

    @staticmethod
    def load(path: str) -> np.ndarray:
        image = cv2.imread(path)
        if image is None:
            raise ValueError(f"cannot read {path}")
        return cv2.resize(image, IMAGE_SIZE)

    def _masks(self, rgb: np.ndarray, points, labels, box=None) -> List[Optional[Dict]]:
        self.predictor.set_image(rgb)
        with torch.inference_mode():
            masks, scores, _ = self.predictor.predict(point_coords=np.array(points, dtype=np.float32),
                                                      point_labels=np.array(labels),
                                                      box=None if box is None else np.array(box, dtype=np.float32),
                                                      multimask_output=True)
        return [ellipse_of(m) for m in masks]

    def model_of(self, bgr: np.ndarray) -> Tuple[str, float]:
        m = np.zeros(bgr.shape[:2], np.uint8)
        cv2.circle(m, (int(self.s["cx"]), int(self.s["cy"])), 60, 255, -1)
        px = bgr[m > 0].astype(np.float32)
        rb = float(px[:, 2].mean() - px[:, 0].mean())
        return (DOSTPLUS if rb > self.s["brass_rb_min"] else DOST), rb

    def segment_cap(self, bgr: np.ndarray, model: Optional[str] = None) -> Dict:
        """Cap (and, for brass, the bore around it) on the cap picture."""
        found_model, rb = self.model_of(bgr)
        model = model or found_model
        c = (self.s["cx"], self.s["cy"])
        fits = self._masks(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), [c], [1])
        cap = pick(fits, self.s["brass_r"] if model == DOSTPLUS else self.s["silver_r"])
        bore = pick([f for f in fits if f is not cap], self.s["bore_r"]) if model == DOSTPLUS else None
        face = self.segment_face(cap) if (model == DOST and cap is not None) else None
        return dict(model=model, rb=rb, cap=cap, bore=bore, face=face, all=fits)

    def segment_face(self, cap: Dict) -> Optional[Dict]:
        """Silver cap top face: box +- face_box_scale x the outline radius round the outline centre, point at that
        centre; the smallest mask. Uses the embedding already set for the cap picture."""
        r = cap["radius"] * self.s["face_box_scale"]
        box = np.array([cap["cx"] - r, cap["cy"] - r, cap["cx"] + r, cap["cy"] + r], dtype=np.float32)
        with torch.inference_mode():
            masks, _, _ = self.predictor.predict(point_coords=np.array([[cap["cx"], cap["cy"]]], dtype=np.float32),
                                                 point_labels=np.array([1]), box=box, multimask_output=True)
        fits = [f for f in (ellipse_of(m) for m in masks) if f is not None]
        return min(fits, key=lambda f: f["area"]) if fits else None

    def segment_bore(self, bgr: np.ndarray) -> Optional[Dict]:
        """Bore (with its chamfer) on the hub picture: box +-140 and 4 points 55 px round the centre."""
        cx, cy, d = self.s["cx"], self.s["cy"], 55
        fits = self._masks(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB),
                           [(cx + d, cy), (cx - d, cy), (cx, cy + d), (cx, cy - d)], [1, 1, 1, 1],
                           box=(cx - 140, cy - 140, cx + 140, cy + 140))
        return pick(fits, self.s["bore_r"])

    def compare(self, cap_result: Dict, bore: Optional[Dict], bore_source: str) -> Dict:
        model, cap = cap_result["model"], cap_result["cap"]
        face = cap_result.get("face")
        out = dict(model=model, bore_source=bore_source, cap=cap, bore=bore, face=face)
        if cap is None:
            out.update(verdict="NO CAP FOUND", seated=False)
            return out
        reasons = []
        # Outline (brass: cap) against the bore. Silver without a bore: shown against the fixed centre, not judged.
        judge_bore = bore is not None or model == DOSTPLUS
        if bore is None:
            ref = (float(self.s["cx"]), float(self.s["cy"]))
            out["bore_source"] = "fixed centre"
        else:
            ref = (bore["cx"], bore["cy"])
        ex, ey = self.s["expected_offset"][model]
        dx, dy = cap["cx"] - ref[0] - ex, cap["cy"] - ref[1] - ey
        off = float(np.hypot(dx, dy))
        lim = self.s["offset_limit"][model]
        if judge_bore and off > lim:
            reasons.append(f"{'cap' if model == DOSTPLUS else 'outline'} to bore {off:.1f} > {lim}")
        out.update(ref=ref, dx=dx, dy=dy, offset=off, ratio=cap["ratio"])
        # Silver: top face in its own outline
        if model == DOST:
            if face is None:
                reasons.append("no top face found")
            else:
                fdx, fdy = face["cx"] - cap["cx"], face["cy"] - cap["cy"]
                foff = float(np.hypot(fdx, fdy))
                out.update(face_dx=fdx, face_dy=fdy, face_offset=foff)
                if foff > self.s["face_offset_limit"]:
                    reasons.append(f"face offset {foff:.1f} > {self.s['face_offset_limit']}")
        rmin = self.s["ratio_min"][model]
        if rmin > 0 and cap["ratio"] < rmin:
            reasons.append(f"ratio {cap['ratio']:.3f} < {rmin}")
        out.update(seated=not reasons, verdict="SEATED" if not reasons else "SKEWED (" + ", ".join(reasons) + ")")
        return out

    def annotate(self, bgr: np.ndarray, result: Dict, title: str = "") -> np.ndarray:
        img = bgr.copy()
        cap, bore = result.get("cap"), result.get("bore")
        if bore is not None:
            cv2.drawContours(img, [bore["contour"]], -1, (255, 128, 0), 2)
        if "ref" in result:
            cv2.drawMarker(img, (int(round(result["ref"][0])), int(round(result["ref"][1]))), (255, 128, 0),
                           cv2.MARKER_CROSS, 18, 2)
        if cap is not None:
            col = (0, 200, 0) if result.get("seated") else (0, 0, 255)
            cv2.drawContours(img, [cap["contour"]], -1, col, 2)
            cv2.drawMarker(img, (int(round(cap["cx"])), int(round(cap["cy"]))), col, cv2.MARKER_TILTED_CROSS, 14, 2)
        face = result.get("face")
        if face is not None:
            cv2.drawContours(img, [face["contour"]], -1, (0, 255, 255), 2)
            cv2.drawMarker(img, (int(round(face["cx"])), int(round(face["cy"]))), (0, 255, 255), cv2.MARKER_CROSS, 12, 2)
        lines = [title, f"{result['model']}  bore from: {result['bore_source']}", result["verdict"]]
        if cap is not None:
            lines.append(f"cap ({cap['cx']:.1f},{cap['cy']:.1f}) r {cap['major']:.1f}/{cap['minor']:.1f} "
                         f"ratio {cap['ratio']:.3f}")
        if bore is not None:
            lines.append(f"bore ({bore['cx']:.1f},{bore['cy']:.1f}) r {bore['major']:.1f}/{bore['minor']:.1f}")
        if "offset" in result:
            lines.append(f"{'cap' if result['model'] == DOSTPLUS else 'outline'} to {result['bore_source'][:12]}: "
                         f"({result['dx']:+.1f},{result['dy']:+.1f}) = {result['offset']:.1f} px")
        if "face_offset" in result:
            lines.append(f"top face to outline ({result['face_dx']:+.1f},{result['face_dy']:+.1f}) = "
                         f"{result['face_offset']:.1f} px, face r {face['radius']:.1f}")
        for i, t in enumerate(lines):
            cv2.putText(img, t, (20, 35 + 28 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 0, 0), 4)
            cv2.putText(img, t, (20, 35 + 28 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)
        return img


def find_sam_checkpoint(explicit: Optional[str] = None) -> str:
    for p in [explicit, os.path.join(_MOBILESAM_DIR, "weights", "mobile_sam.pt"), "weights/mobile_sam.pt",
              "../MobileSAM/weights/mobile_sam.pt", "C:/MobileSAM/weights/mobile_sam.pt"]:
        if p and os.path.exists(p):
            return p
    raise FileNotFoundError("mobile_sam.pt not found (models/MobileSAM/weights/mobile_sam.pt, or pass --sam)")


def image_files(path: Optional[str]) -> List[str]:
    if not path:
        return []
    if os.path.isfile(path):
        return [path]
    files = []
    for ext in ("*.png", "*.jpg", "*.jpeg", "*.PNG", "*.JPG", "*.JPEG"):
        files.extend(glob.glob(os.path.join(path, ext)))
    return sorted(set(files))


def hub_for(index: int, hub_files: List[str]) -> Optional[str]:
    """One hub file for all caps, or a folder paired by sorted order."""
    if not hub_files:
        return None
    return hub_files[0] if len(hub_files) == 1 else hub_files[index % len(hub_files)]


def run_one(seg: CapBoreSegmenter, cap_path: str, hub_path: Optional[str], bore_from: str,
            model: Optional[str] = None) -> Tuple[Dict, np.ndarray, Optional[np.ndarray]]:
    bgr = seg.load(cap_path)
    cap_result = seg.segment_cap(bgr, model)
    hub_bgr, bore = None, None
    if bore_from == "hub picture" and hub_path:
        hub_bgr = seg.load(hub_path)
        bore = seg.segment_bore(hub_bgr)
        source = f"hub picture {os.path.basename(hub_path)}"
    elif bore_from == "cap picture":
        bore = cap_result["bore"]
        source = "cap picture"
    else:
        source = "fixed centre"
    result = seg.compare(cap_result, bore, source)
    return result, bgr, hub_bgr


# =====================================================================================================================
# Batch mode
# =====================================================================================================================
def batch(args) -> int:
    seg = CapBoreSegmenter(find_sam_checkpoint(args.sam))
    caps, hubs = image_files(args.batch), image_files(args.hub)
    bore_from = "hub picture" if hubs else args.bore_from
    rows = []
    if args.save:
        os.makedirs(args.save, exist_ok=True)
    print(f"{'picture':28s} {'model':8s} {'bore from':22s} {'cap centre':>14s} {'r maj/min':>11s} {'ratio':>6s} "
          f"{'bore centre':>14s} {'offset':>16s} {'face off':>8s}  verdict")
    for i, f in enumerate(caps):
        t = time.perf_counter()
        r, bgr, hub_bgr = run_one(seg, f, hub_for(i, hubs), bore_from, args.model)
        cap, bore = r.get("cap"), r.get("bore")
        print(f"{os.path.basename(f):28s} {r['model']:8s} {r['bore_source'][:22]:22s} "
              + (f"({cap['cx']:6.1f},{cap['cy']:6.1f}) {cap['major']:5.1f}/{cap['minor']:5.1f} {cap['ratio']:6.3f} "
                 if cap else f"{'-':>14s} {'-':>11s} {'-':>6s} ")
              + (f"({bore['cx']:6.1f},{bore['cy']:6.1f}) " if bore else f"{'-':>14s} ")
              + (f"({r['dx']:+5.1f},{r['dy']:+5.1f}) {r['offset']:4.1f} " if 'offset' in r else f"{'-':>16s} ")
              + (f"{r['face_offset']:8.1f}  " if 'face_offset' in r else f"{'-':>8s}  ")
              + f"{r['verdict']}   [{time.perf_counter() - t:.1f}s]")
        rows.append(dict(picture=os.path.basename(f), model=r["model"], bore_source=r["bore_source"],
                         cap_cx=cap and round(cap["cx"], 2), cap_cy=cap and round(cap["cy"], 2),
                         cap_major=cap and round(cap["major"], 2), cap_minor=cap and round(cap["minor"], 2),
                         ratio=cap and round(cap["ratio"], 4),
                         bore_cx=bore and round(bore["cx"], 2), bore_cy=bore and round(bore["cy"], 2),
                         bore_r=bore and round(bore["radius"], 2),
                         dx=r.get("dx") and round(r["dx"], 2), dy=r.get("dy") and round(r["dy"], 2),
                         offset=r.get("offset") and round(r["offset"], 2),
                         face_offset=r.get("face_offset") and round(r["face_offset"], 2),
                         face_r=r.get("face") and round(r["face"]["radius"], 2), verdict=r["verdict"]))
        if args.save:
            out = seg.annotate(bgr, r, os.path.basename(f))
            cv2.imwrite(os.path.join(args.save, os.path.splitext(os.path.basename(f))[0] + "_seating.jpg"), out)
    if args.csv and rows:
        with open(args.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"CSV: {args.csv}")
    return 0


# =====================================================================================================================
# GUI
# =====================================================================================================================
def gui(args) -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from PIL import Image, ImageTk

    class App:
        def __init__(self, root: tk.Tk):
            self.root = root
            root.title("Cap seating - MobileSAM cap and bore segmentation v1.0")
            root.geometry("1500x900")
            self.seg: Optional[CapBoreSegmenter] = None
            self.caps: List[str] = []
            self.hubs: List[str] = []
            self.index = 0
            self.photos = []
            self.v = {k: tk.StringVar(value=str(DEFAULTS[k])) for k in ("cx", "cy")}
            self.bore_from = tk.StringVar(value="cap picture")
            self.model = tk.StringVar(value="Auto")
            self.limit = {m: tk.StringVar(value=str(DEFAULTS["offset_limit"][m])) for m in (DOST, DOSTPLUS)}
            self.ratio = {m: tk.StringVar(value=str(DEFAULTS["ratio_min"][m])) for m in (DOST, DOSTPLUS)}
            self.expected = {m: tk.StringVar(value="%g,%g" % DEFAULTS["expected_offset"][m]) for m in (DOST, DOSTPLUS)}
            self.face_limit = tk.StringVar(value=str(DEFAULTS["face_offset_limit"]))
            self._ui()
            root.after(100, self._load_model)

        def _ui(self):
            top = ttk.Frame(self.root, padding=6)
            top.pack(fill=tk.X)
            ttk.Button(top, text="Cap picture(s)...", command=self._choose_caps).pack(side=tk.LEFT, padx=3)
            ttk.Button(top, text="Hub picture(s)...", command=self._choose_hubs).pack(side=tk.LEFT, padx=3)
            ttk.Button(top, text="<- Prev", command=lambda: self._go(-1)).pack(side=tk.LEFT, padx=3)
            ttk.Button(top, text="Next ->", command=lambda: self._go(1)).pack(side=tk.LEFT, padx=3)
            ttk.Button(top, text="Re-run", command=self._run).pack(side=tk.LEFT, padx=3)
            ttk.Button(top, text="Run all -> CSV", command=self._run_all).pack(side=tk.LEFT, padx=3)
            self.counter = ttk.Label(top, text="")
            self.counter.pack(side=tk.LEFT, padx=10)
            self.status = ttk.Label(top, text="Loading MobileSAM...")
            self.status.pack(side=tk.LEFT, padx=10)

            row = ttk.Frame(self.root, padding=(6, 0))
            row.pack(fill=tk.X)
            for k in ("cx", "cy"):
                ttk.Label(row, text=k.upper() + ":").pack(side=tk.LEFT)
                ttk.Entry(row, textvariable=self.v[k], width=5).pack(side=tk.LEFT, padx=(0, 6))
            ttk.Label(row, text="Bore from:").pack(side=tk.LEFT)
            ttk.Combobox(row, textvariable=self.bore_from, values=["cap picture", "hub picture", "none"],
                         width=11, state="readonly").pack(side=tk.LEFT, padx=(0, 8))
            ttk.Label(row, text="Model:").pack(side=tk.LEFT)
            ttk.Combobox(row, textvariable=self.model, values=["Auto", DOST, DOSTPLUS], width=9,
                         state="readonly").pack(side=tk.LEFT, padx=(0, 8))
            for m in (DOST, DOSTPLUS):
                ttk.Label(row, text=f"{m}: offset<=").pack(side=tk.LEFT)
                ttk.Entry(row, textvariable=self.limit[m], width=4).pack(side=tk.LEFT)
                ttk.Label(row, text="ratio>=").pack(side=tk.LEFT)
                ttk.Entry(row, textvariable=self.ratio[m], width=5).pack(side=tk.LEFT)
                ttk.Label(row, text="expected dx,dy").pack(side=tk.LEFT)
                ttk.Entry(row, textvariable=self.expected[m], width=8).pack(side=tk.LEFT, padx=(0, 10))
            ttk.Label(row, text="silver face offset<=").pack(side=tk.LEFT)
            ttk.Entry(row, textvariable=self.face_limit, width=4).pack(side=tk.LEFT)

            self.canvas = tk.Canvas(self.root, bg="#1a1a1a", width=1460, height=700)
            self.canvas.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
            self.info = ttk.Label(self.root, text="", font=("Consolas", 10))
            self.info.pack(fill=tk.X, padx=6)

        def _load_model(self):
            try:
                self.seg = CapBoreSegmenter(find_sam_checkpoint(args.sam))
                self.status.config(text=f"MobileSAM loaded ({self.seg.device})")
            except Exception as e:
                self.status.config(text=f"Error: {e}")
                messagebox.showerror("Model load error", str(e))

        def _pick(self, title) -> List[str]:
            if messagebox.askquestion("Select", f"{title}: a folder?\n\nYes = folder, No = one file") == "yes":
                return image_files(filedialog.askdirectory(title=title))
            f = filedialog.askopenfilename(title=title, filetypes=[("Images", "*.png *.jpg *.jpeg *.PNG *.JPG *.JPEG")])
            return [f] if f else []

        def _choose_caps(self):
            self.caps = self._pick("Cap picture(s)")
            self.index = 0
            self._run()

        def _choose_hubs(self):
            self.hubs = self._pick("Hub picture(s) of the same part(s)")
            if self.hubs:
                self.bore_from.set("hub picture")
            self._run()

        def _go(self, step):
            if self.caps:
                self.index = max(0, min(len(self.caps) - 1, self.index + step))
                self._run()

        def _apply_settings(self):
            s = self.seg.s
            try:
                s["cx"], s["cy"] = int(self.v["cx"].get()), int(self.v["cy"].get())
                s["face_offset_limit"] = float(self.face_limit.get())
                for m in (DOST, DOSTPLUS):
                    s["offset_limit"][m] = float(self.limit[m].get())
                    s["ratio_min"][m] = float(self.ratio[m].get())
                    s["expected_offset"][m] = tuple(float(x) for x in self.expected[m].get().split(","))
            except ValueError:
                messagebox.showwarning("Settings", "A setting is not a number - previous values kept")

        def _run(self):
            if self.seg is None or not self.caps:
                return
            self._apply_settings()
            f = self.caps[self.index]
            self.counter.config(text=f"{self.index + 1}/{len(self.caps)}  {os.path.basename(f)}")
            t = time.perf_counter()
            model = None if self.model.get() == "Auto" else self.model.get()
            r, bgr, hub_bgr = run_one(self.seg, f, hub_for(self.index, self.hubs), self.bore_from.get(), model)
            left = self.seg.annotate(bgr, r, os.path.basename(f))
            right = None
            if hub_bgr is not None:
                right = hub_bgr.copy()
                if r.get("bore") is not None:
                    cv2.drawContours(right, [r["bore"]["contour"]], -1, (255, 128, 0), 2)
                if r.get("cap") is not None:
                    cv2.drawContours(right, [r["cap"]["contour"]], -1, (0, 200, 0) if r["seated"] else (0, 0, 255), 2)
                cv2.putText(right, "hub picture: bore (blue), cap outline from the cap picture", (20, 35),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)
            self._show(left, right)
            self.status.config(text=f"{r['verdict']}   [{time.perf_counter() - t:.1f}s]")
            self.info.config(text=self._text(r))

        @staticmethod
        def _text(r) -> str:
            cap, bore = r.get("cap"), r.get("bore")
            t = f"{r['model']}  bore from {r['bore_source']}  "
            if cap:
                t += f"cap ({cap['cx']:.1f},{cap['cy']:.1f}) r {cap['major']:.1f}/{cap['minor']:.1f} ratio {cap['ratio']:.3f}  "
            if bore:
                t += f"bore ({bore['cx']:.1f},{bore['cy']:.1f}) r {bore['radius']:.1f}  "
            if "offset" in r:
                t += f"offset ({r['dx']:+.1f},{r['dy']:+.1f}) {r['offset']:.1f} px  "
            if "face_offset" in r:
                t += f"face offset ({r['face_dx']:+.1f},{r['face_dy']:+.1f}) {r['face_offset']:.1f} px"
            return t

        def _show(self, left, right):
            self.canvas.delete("all")
            self.photos = []
            panels = [left] if right is None else [left, right]
            cw = max(self.canvas.winfo_width(), 800)
            ch = max(self.canvas.winfo_height(), 400)
            # Crop round the centre (the cap region), then fit the panels side by side
            cx, cy = int(self.seg.s["cx"]), int(self.seg.s["cy"])
            for i, p in enumerate(panels):
                crop = p[max(0, cy - 300):cy + 300, max(0, cx - 400):cx + 400]
                scale = min((cw / len(panels) - 10) / crop.shape[1], (ch - 10) / crop.shape[0])
                crop = cv2.resize(crop, (int(crop.shape[1] * scale), int(crop.shape[0] * scale)))
                photo = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)))
                self.photos.append(photo)
                self.canvas.create_image(5 + i * cw // len(panels), 5, anchor=tk.NW, image=photo)

        def _run_all(self):
            if self.seg is None or not self.caps:
                return
            out = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
            if not out:
                return
            self._apply_settings()
            model = None if self.model.get() == "Auto" else self.model.get()
            with open(out, "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["picture", "model", "bore_source", "cap_cx", "cap_cy", "cap_major", "cap_minor", "ratio",
                            "bore_cx", "bore_cy", "dx", "dy", "offset", "face_offset", "verdict"])
                for i, f in enumerate(self.caps):
                    self.status.config(text=f"{i + 1}/{len(self.caps)} {os.path.basename(f)}")
                    self.root.update()
                    r, _, _ = run_one(self.seg, f, hub_for(i, self.hubs), self.bore_from.get(), model)
                    cap, bore = r.get("cap"), r.get("bore")
                    w.writerow([os.path.basename(f), r["model"], r["bore_source"],
                                *(["%.2f" % cap[k] for k in ("cx", "cy", "major", "minor")] + ["%.4f" % cap["ratio"]]
                                  if cap else [""] * 5),
                                *(["%.2f" % bore["cx"], "%.2f" % bore["cy"]] if bore else ["", ""]),
                                *(["%.2f" % r[k] for k in ("dx", "dy", "offset")] if "offset" in r else [""] * 3),
                                "%.2f" % r["face_offset"] if "face_offset" in r else "",
                                r["verdict"]])
            self.status.config(text=f"CSV written: {out}")

    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batch", help="cap picture or folder: run without the GUI and print a table")
    ap.add_argument("--hub", help="hub picture or folder (same part; a folder is paired with the caps by sorted order)")
    ap.add_argument("--bore-from", default="cap picture", choices=["cap picture", "hub picture", "none"])
    ap.add_argument("--model", choices=[DOST, DOSTPLUS], help="force the model (default: from the cap colour)")
    ap.add_argument("--csv", help="batch: write the table to this CSV")
    ap.add_argument("--save", help="batch: write the marked pictures to this folder")
    ap.add_argument("--sam", help="path to mobile_sam.pt")
    args = ap.parse_args()
    return batch(args) if args.batch else gui(args)


if __name__ == "__main__":
    sys.exit(main())
