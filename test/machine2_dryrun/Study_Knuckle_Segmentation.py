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

Output per picture (terminal, text box, knuckle_parts.txt): TYPE (DOST / DOSTPLUS from boss area x1000 / distance^2,
cross-checked by boss radius / distance), side, REFERENCE spindle (the mask when its radius is 46-52, else worked out
from the boss midpoint - a bearing on the spindle makes the mask too big), SHIFT of the spindle from the reference
spindle 642.2,359.1 (the mean of 27 knuckles of 13 Nov, with which the present check centres were set) and the hub
centre it implies, zoom and rotation against the type / side's usual boss distance and angle, BOSS_MID, and one MASK
line per mask: area, centroid (and from the spindle), perimeter, equivalent diameter, circularity (4 pi A / P^2),
enclosing circle, fitted ellipse (centre, axes, ratio, angle), holes inside, box, grey, SAM score. The CSV has the same.

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

# Fast method: prompt points relative to the spindle (picked as the common core of each part's mask on the 19 knuckle
# pictures of 13 Nov - every point 20-34 px inside the part on every picture). ARM_L / ARM_R: the lower arm of an LHS /
# RHS knuckle, from the knuckle body down to the part resting on the bar; on a knuckle of the other side that spot is
# bare plate / bar, and the mask found there is rejected as an arm.
# Bosses: a box (centre +- BOSS_BOX_HALF) and 4 points on the machined ring (BOSS_POINT_R from the centre, on the
# diagonals - nearer the centre they fall on the rim of the bore and SAM returns the bore); SAM's 3 candidates, the
# roundest that passes the boss rule kept. Measured on the 19 pictures with the prompts moved +-8 px: 228/228.
BOSS_CENTRES = {"BOSS_L": (-198, -161), "BOSS_R": (200, -161)}
# Boss points: 4 (default) on the diagonals at 26 px; 8 / 16 evenly round a ring at 24 px (the ring 24 px out stays
# 9.8 px inside every boss of the 19 pictures). Tested with the prompts moved +-8 px (266 boss masks each): 4 points
# 266/266, centroid moving median 0.34 / worst 1.39 px; 16 points 258/266, 0.72 / 1.82 px.
BOSS_POINT_CHOICES = ("grid", 4, 8, 16)
# "grid" (default): Study_Boss_Grid_Masks - per boss a 4 x 4 grid (36.8 px spacing, about +-55 px round the boss's
# usual place, fixed in the picture), its 24 neighbouring pairs as 2-point prompts (3 masks each), every boss-sized
# mask judged by its bright machined ring round the dark core (bore + pin); the best group kept. Its pin centre (the
# dark core's centre) is steadier than the mask centre and is used for the distance, angle, type and spindle fallback.


def boss_point_offsets(n: int) -> List[Tuple[float, float]]:
    if n == 4:
        return [(26 * math.cos(math.radians(a)), 26 * math.sin(math.radians(a))) for a in (45, 135, 225, 315)]
    return [(24 * math.cos(math.radians(a)), 24 * math.sin(math.radians(a))) for a in np.arange(0, 360, 360.0 / n)]

# Reference values from the user's laptop run (28 knuckles, 13 Nov camera position), per type and side: boss-to-boss
# distance (px), its angle (deg), the boss midpoint from the spindle, and the spindle. REF_SPINDLE: the mean spindle
# of the 27 pictures without a bearing on it - the positions the present check centres were tuned with.
NOMINAL = {
    ("DOST", "LHS"): dict(distance=393.9, angle=0.06, mid=(0.2, -160.9), spindle=(643.2, 359.8)),
    ("DOST", "RHS"): dict(distance=398.5, angle=0.10, mid=(3.1, -160.0), spindle=(641.5, 357.1)),
    ("DOSTPLUS", "LHS"): dict(distance=383.8, angle=1.25, mid=(3.0, -155.9), spindle=(642.1, 359.1)),
    ("DOSTPLUS", "RHS"): dict(distance=383.7, angle=0.63, mid=(1.6, -156.4), spindle=(642.3, 360.3)),
}
# The same from the pin centres (grid method; user's laptop run of the 19 knuckles, 15 Nov): distance between the
# pins, its angle, and the pin midpoint from the spindle.
NOMINAL_PINS = {
    ("DOST", "LHS"): dict(distance=400.35, angle=0.65, mid=(0.92, -164.82), spindle=(643.2, 359.8),
                          pin_mid=(644.15, 194.93)),
    ("DOST", "RHS"): dict(distance=400.17, angle=0.68, mid=(2.55, -164.18), spindle=(641.5, 357.1),
                          pin_mid=(643.75, 192.95)),
    ("DOSTPLUS", "LHS"): dict(distance=394.26, angle=0.69, mid=(1.74, -159.46), spindle=(642.1, 359.1),
                              pin_mid=(643.70, 199.65)),
    ("DOSTPLUS", "RHS"): dict(distance=394.64, angle=0.77, mid=(1.42, -160.24), spindle=(642.3, 360.3),
                              pin_mid=(643.62, 200.00)),
}
REF_SPINDLE = (642.2, 359.1)
HUB_CENTRE = (645, 334)                 # siemens.vision.hub.segment.center, set with the spindle at REF_SPINDLE
TYPE_AREA_RATIO = 34.5                  # boss area x 1000 / distance^2: DOST 29.5-31.2, DOSTPLUS 38.0-41.1
TYPE_RADIUS_RATIO = 0.111               # boss radius / distance: DOST 0.104-0.107, DOSTPLUS 0.116-0.122
TYPE_RADIUS_RATIO_PINS = 0.1095         # the same over the pin distance (grid): DOST 0.102-0.105, DOSTPLUS 0.114-0.124
SPINDLE_R_OK = (46.0, 52.0)             # 48.1-49.9 measured; 53.9 with a bearing on the spindle
BOSS_BOX_HALF = 52
BOSS_POINT_R = 26
PROMPTS = {
    "BOSS_L": [(-198 + dx, -161 + dy) for dx, dy in boss_point_offsets(4)],
    "BOSS_R": [(200 + dx, -161 + dy) for dx, dy in boss_point_offsets(4)],
    "ARM_L": [(-155, 115), (-165, 182), (-192, 248), (-220, 310)],
    "ARM_R": [(150, 123), (162, 189), (186, 254), (210, 315)],
}
FAST = "Prompted (fast)"

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
    perimeter = float(cv2.arcLength(c, True))
    if len(c) >= 5:
        (ex, ey), (ea, eb), eang = cv2.fitEllipse(c)
        emaj, emin = max(ea, eb) / 2.0, min(ea, eb) / 2.0
    else:
        ex, ey, emaj, emin, eang = float(xs.mean()), float(ys.mean()), 0.0, 0.0, 0.0
    # holes inside the mask (e.g. a boss's bore when SAM leaves it out)
    csh, hier = cv2.findContours(m8, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    holes = [h_ for h_, hh in zip(csh, hier[0]) if hh[3] >= 0] if hier is not None else []
    return dict(id=idx, mask=mask, area=area, cx=float(xs.mean()), cy=float(ys.mean()),
                perimeter=perimeter, eq_diameter=2.0 * math.sqrt(area / math.pi),
                circularity=4.0 * math.pi * area / (perimeter * perimeter) if perimeter > 0 else 0.0,
                ellipse_x=float(ex), ellipse_y=float(ey), ellipse_major=float(emaj), ellipse_minor=float(emin),
                ellipse_ratio=float(emin / emaj) if emaj > 0 else 0.0, ellipse_angle=float(eang),
                holes=len(holes), holes_area=int(sum(cv2.contourArea(h_) for h_ in holes)),
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
        if "measures" in found:
            sp = found.get("SPINDLE")
            self._derive(found, found["measures"], sp, found["measures"]["spindle"][:2])
        return masks, found, time.time() - t0

    def segment_prompted(self, image_rgb: np.ndarray, rules: Dict = RULES, boss_points="grid"
                         ) -> Tuple[List[Dict], Dict, float]:
        """Fast method: one image embedding, one point prompt on the spindle, then the 4 parts (4 points each) in one
        batched decoder call, placed relative to the spindle found. Each mask is then judged by the same size / shape
        rules as in the automatic method."""
        r = rules
        grey = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
        t0 = time.time()
        with self._lock, torch.no_grad():
            self.predictor.set_image(image_rgb)
            t_embed = time.time() - t0
            ex, ey = r["spindle_expected"]
            ms, scores, _ = self.predictor.predict(point_coords=np.array([[ex, ey]]), point_labels=np.array([1]),
                                                   multimask_output=True)
            cands = [describe(m, grey, -1, sc) for m, sc in zip(ms, scores) if m.any()]
            spin = [c for c in cands if r["spindle_r"][0] <= c["radius"] <= r["spindle_r"][1]
                    and c["round_fill"] >= r["round_fill_min"]
                    and math.hypot(c["circle_x"] - ex, c["circle_y"] - ey) <= r["spindle_max_shift"]]
            sp = min(spin, key=lambda c: math.hypot(c["circle_x"] - ex, c["circle_y"] - ey)) if spin else None
            sx, sy = (sp["circle_x"], sp["circle_y"]) if sp else (float(ex), float(ey))
            shape = image_rgb.shape[:2]
            tf = self.predictor.transform
            bnames = ["BOSS_L", "BOSS_R"]
            grid = None
            if boss_points == "grid":
                import Study_Boss_Grid_Masks as BG       # imports this module; loaded lazily to avoid a cycle
                _rows, grid, grid_pts, _tm = BG.grid_bosses(self.predictor, self.device, image_rgb, grey,
                                                            dict(BG.BOSS_FIXED))
                bpts = np.array(list(grid_pts.values()), dtype=np.float32)
                boxes = np.zeros((0, 4))
            else:
                # bosses: box + N points, 3 candidates each (one batched call)
                bpts = np.array([[(sx + BOSS_CENTRES[n][0] + dx, sy + BOSS_CENTRES[n][1] + dy)
                                  for dx, dy in boss_point_offsets(int(boss_points))] for n in bnames],
                                dtype=np.float32)
                boxes = np.array([[sx + BOSS_CENTRES[n][0] - BOSS_BOX_HALF, sy + BOSS_CENTRES[n][1] - BOSS_BOX_HALF,
                                   sx + BOSS_CENTRES[n][0] + BOSS_BOX_HALF, sy + BOSS_CENTRES[n][1] + BOSS_BOX_HALF]
                                  for n in bnames], dtype=np.float32)
                bc = torch.as_tensor(tf.apply_coords(bpts, shape), dtype=torch.float, device=self.device)
                bb = torch.as_tensor(tf.apply_boxes(boxes, shape), dtype=torch.float, device=self.device)
                bm, bq, _ = self.predictor.predict_torch(
                    bc, torch.ones(bc.shape[:2], dtype=torch.int, device=self.device), boxes=bb,
                    multimask_output=True)
                boss_cands = bm.cpu().numpy()
                boss_q = bq.cpu().numpy()
            # arms: 4 points each, one mask each (one batched call)
            anames = ["ARM_L", "ARM_R"]
            apts = np.array([[(sx + dx, sy + dy) for dx, dy in PROMPTS[n]] for n in anames], dtype=np.float32)
            ac = torch.as_tensor(tf.apply_coords(apts, shape), dtype=torch.float, device=self.device)
            am, aq, _ = self.predictor.predict_torch(ac, torch.ones(ac.shape[:2], dtype=torch.int, device=self.device),
                                                     multimask_output=False)
            arm_masks = am[:, 0].cpu().numpy()
            arm_q = aq[:, 0].cpu().numpy()
        secs = time.time() - t0

        def boss_ok(d, side):
            sign = -1 if side == "L" else 1
            return (r["boss_r"][0] <= d["radius"] <= r["boss_r"][1] and d["round_fill"] >= r["boss_fill_min"]
                    and d["grey"] >= r["boss_grey_min"]
                    and r["boss_dy"][0] <= d["circle_y"] - sy <= r["boss_dy"][1]
                    and r["boss_dx"][0] <= sign * (d["circle_x"] - sx) <= r["boss_dx"][1])

        part_masks, ious, boss_pass, boss_extra = [], [], {}, {}
        for j, n in enumerate(bnames):
            if grid is not None:
                b = grid[n]["best"]
                full = np.zeros(shape, bool)
                if b is not None:
                    x0, y0, x1, y1 = b["box"]
                    full[y0:y1, x0:x1] = b["mask"]
                    boss_extra[n] = {k: b[k] for k in ("core_x", "core_y", "core_radius", "core_grey", "ring_grey",
                                                       "contrast", "concentricity", "votes", "score")}
                    boss_extra[n]["candidate_groups"] = grid[n]["candidate_groups"]
                part_masks.append(full)
                ious.append(b["iou"] if b else 0.0)
                boss_pass[n] = b is not None
                continue
            ds = [describe(m, grey, 0, float(q)) for m, q in zip(boss_cands[j], boss_q[j]) if m.any()]
            good = [d for d in ds if boss_ok(d, n[-1])]
            best = max(good or ds, key=lambda d: d["round_fill"]) if ds else None
            part_masks.append(best["mask"] if best else np.zeros(shape, bool))
            ious.append(best["iou"] if best else 0.0)
            boss_pass[n] = bool(good)
        part_masks += list(arm_masks)
        ious += list(arm_q)
        names = bnames + anames

        masks: List[Dict] = []
        found: Dict = {}
        if sp:
            sp["id"] = 0
            masks.append(sp)
            found["SPINDLE"] = sp
        for i, (n, m, q) in enumerate(zip(names, part_masks, ious), start=1):
            if not m.any():
                continue
            d = describe(m, grey, i, float(q))
            d["prompt"] = n
            d.update(boss_extra.get(n, {}))
            masks.append(d)
            if n.startswith("BOSS"):
                if boss_pass[n]:
                    found[n] = d
            else:
                filled = fill_holes(d["mask"])
                if int(filled.sum()) != d["area"]:
                    d = describe(filled, grey, i, float(q))
                    d["prompt"] = n
                    masks[-1] = d
                if (d["grey"] < r["lower_grey_max"] and r["lower_area"][0] <= d["area"] <= r["lower_area"][1]
                        and d["rect_fill"] < r["lower_rect_fill_max"]
                        and d["bbox"][1] + d["bbox"][3] > sy + r["lower_reach"]
                        and r["lower_top_dy"][0] <= d["bbox"][1] - sy <= r["lower_top_dy"][1]):
                    found[n] = d
        if "ARM_L" in found and "ARM_R" not in found:
            found["LOWER_ARM"], found["side"] = found["ARM_L"], "LHS"
        elif "ARM_R" in found and "ARM_L" not in found:
            found["LOWER_ARM"], found["side"] = found["ARM_R"], "RHS"
        elif "ARM_L" in found and "ARM_R" in found:
            found["side"] = "BOTH ARMS?"
        for k in ("ARM_L", "ARM_R"):
            found.pop(k, None)
        for m in masks:
            m["label"] = next((k for k, v in found.items() if isinstance(v, dict) and v is m), "")
            if not m["label"] and "prompt" in m:
                m["label"] = "(" + m["prompt"] + ": no)"
        meas = dict(spindle=(round(sx, 1), round(sy, 1), round(sp["radius"], 1) if sp else None))
        for side in ("L", "R"):
            b = found.get("BOSS_" + side)
            if b:
                meas["boss_" + side] = (round(b["circle_x"] - sx, 1), round(b["circle_y"] - sy, 1),
                                        round(b["radius"], 1))
        if "BOSS_L" in found and "BOSS_R" in found:
            bl, br = found["BOSS_L"], found["BOSS_R"]
            dx, dy = br["circle_x"] - bl["circle_x"], br["circle_y"] - bl["circle_y"]
            meas["boss_distance"] = round(math.hypot(dx, dy), 1)
            meas["boss_angle_deg"] = round(math.degrees(math.atan2(dy, dx)), 2)
            if "core_x" in bl and "core_x" in br:          # grid method: the pins are the reference
                dx, dy = br["core_x"] - bl["core_x"], br["core_y"] - bl["core_y"]
                meas["pins"] = ((round(bl["core_x"], 1), round(bl["core_y"], 1)),
                                (round(br["core_x"], 1), round(br["core_y"], 1)))
                meas["boss_distance"] = round(math.hypot(dx, dy), 1)
                meas["boss_angle_deg"] = round(math.degrees(math.atan2(dy, dx)), 2)
        if "LOWER_ARM" in found:
            lo = found["LOWER_ARM"]
            meas["lower_area"], meas["lower_bbox"] = lo["area"], lo["bbox"]
            meas["lower_tip"] = (lo["lowest"][0] - round(sx), lo["lowest"][1] - round(sy))
        meas["times"] = (round(t_embed, 2), round(secs - t_embed, 2))
        meas["prompts"] = [(int(round(x)), int(round(y))) for row in list(bpts) + list(apts) for x, y in row]
        meas["boss_points"] = boss_points
        self._derive(found, meas, sp, (sx, sy))
        meas["boxes"] = [tuple(int(round(v)) for v in b) for b in boxes]
        found["measures"] = meas
        return masks, found, secs

    @staticmethod
    def _derive(found: Dict, meas: Dict, sp: Optional[Dict], s_xy: Tuple[float, float]) -> None:
        """Type, spindle (guarded), and the camera shift / zoom / rotation against the reference."""
        side = found.get("side")
        bl, br = found.get("BOSS_L"), found.get("BOSS_R")
        if bl and br:
            dist = meas["boss_distance"]
            area_ratio = (bl["area"] + br["area"]) / 2.0 * 1000.0 / (dist * dist)
            radius_ratio = (bl["radius"] + br["radius"]) / 2.0 / dist
            rad_limit = TYPE_RADIUS_RATIO_PINS if "pins" in meas else TYPE_RADIUS_RATIO
            t_area = "DOSTPLUS" if area_ratio >= TYPE_AREA_RATIO else "DOST"
            t_rad = "DOSTPLUS" if radius_ratio >= rad_limit else "DOST"
            meas["type_values"] = (round(area_ratio, 2), round(radius_ratio, 4), rad_limit)
            found["type"] = t_area if t_area == t_rad else f"? (area ratio {t_area}, radius ratio {t_rad})"
        typ = found.get("type")
        pins = meas.get("pins")
        nom = (NOMINAL_PINS if pins else NOMINAL).get((typ, side))
        # spindle: the mask if its radius is plausible, else from the boss midpoint (bearing on the spindle, not seen)
        sx, sy = s_xy
        src = "mask"
        if (sp is None or not (SPINDLE_R_OK[0] <= sp["radius"] <= SPINDLE_R_OK[1])) and bl and br and nom:
            mx, my = ((pins[0][0] + pins[1][0]) / 2.0, (pins[0][1] + pins[1][1]) / 2.0) if pins else \
                ((bl["circle_x"] + br["circle_x"]) / 2.0, (bl["circle_y"] + br["circle_y"]) / 2.0)
            sx, sy = mx - nom["mid"][0], my - nom["mid"][1]
            src = ("bosses (spindle mask r %.1f outside %.0f-%.0f)" % (sp["radius"], *SPINDLE_R_OK)) if sp \
                else "bosses (spindle mask not found)"
        elif sp is None:
            src = "expected position (not found)"
        meas["spindle_used"] = (round(sx, 1), round(sy, 1), src)
        if bl and br:
            mx, my = ((pins[0][0] + pins[1][0]) / 2.0, (pins[0][1] + pins[1][1]) / 2.0) if pins else \
                ((bl["circle_x"] + br["circle_x"]) / 2.0, (bl["circle_y"] + br["circle_y"]) / 2.0)
            meas["boss_mid"] = (round(mx, 1), round(my, 1), round(mx - sx, 1), round(my - sy, 1))
            if pins and nom:
                # the pins against their usual place for this type / side: how far the camera has moved
                meas["pin_shift"] = (round(mx - nom["pin_mid"][0], 1), round(my - nom["pin_mid"][1], 1))
        if src != "expected position (not found)":
            dx, dy = sx - REF_SPINDLE[0], sy - REF_SPINDLE[1]
            meas["shift"] = (round(dx, 1), round(dy, 1))
            meas["hub_centre"] = (round(HUB_CENTRE[0] + dx, 1), round(HUB_CENTRE[1] + dy, 1))
            if nom and "boss_distance" in meas:
                meas["zoom"] = round(meas["boss_distance"] / nom["distance"], 4)
                meas["rotation"] = round(meas["boss_angle_deg"] - nom["angle"], 2)
                meas["vs_nominal"] = (round(sx - nom["spindle"][0], 1), round(sy - nom["spindle"][1], 1))

    def run(self, image_rgb: np.ndarray, method: str, boss_points="grid") -> Tuple[List[Dict], Dict, float]:
        """method: FAST, or the grid size of the automatic generator ("16" ... "48")."""
        if method == FAST:
            return self.segment_prompted(image_rgb, boss_points=boss_points)
        return self.segment(image_rgb, int(method))

    def point_prompt(self, image_rgb: np.ndarray, x: int, y: int) -> List[Dict]:
        grey = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
        with self._lock, torch.no_grad():
            self.predictor.set_image(image_rgb)
            ms, scores, _ = self.predictor.predict(point_coords=np.array([[x, y]]), point_labels=np.array([1]),
                                                   multimask_output=True)
        out = [describe(m, grey, i, s, 0.0) for i, (m, s) in enumerate(zip(ms, scores)) if m.any()]
        return sorted(out, key=lambda d: d["area"])


def summary_lines(name: str, found: Dict, n: int, secs: float, masks: Optional[List[Dict]] = None) -> List[str]:
    meas = found.get("measures", {})
    exp = "LHS" if "LHS" in name.upper() else ("RHS" if "RHS" in name.upper() else "?")
    side = found.get("side", "-")
    parts = [k for k in ("SPINDLE", "BOSS_L", "BOSS_R", "UPPER_ARM_L", "UPPER_ARM_R", "LOWER_ARM") if k in found]
    lines = [f"{name}: {n} masks in {secs:.2f} s; found {', '.join(parts) if parts else 'nothing'}; side {side}"
             + (f" (file name {exp}{'' if exp in ('?', side) else ' - DIFFERENT'})" if exp != "?" else "")]
    if "times" in meas:
        lines.append(f"    TIME      embedding {meas['times'][0]} s, prompts {meas['times'][1]} s"
                     f" (boss points {meas.get('boss_points', 4)})")
    if "type" in found:
        tv = meas.get("type_values", ("-", "-"))
        lines.append(f"    TYPE      {found['type']}  (boss area x1000/distance^2 {tv[0]}, limit {TYPE_AREA_RATIO}; "
                     f"boss radius/distance {tv[1]}, limit {tv[2] if len(tv) > 2 else TYPE_RADIUS_RATIO})")
    if "spindle_used" in meas:
        su = meas["spindle_used"]
        lines.append(f"    REFERENCE spindle {su[0]}, {su[1]} from {su[2]}")
    if "shift" in meas:
        z = (f"; zoom {meas['zoom']:.4f}, rotation {meas['rotation']:+.2f} deg, spindle vs this type/side's usual "
             f"{meas['vs_nominal'][0]:+.1f}, {meas['vs_nominal'][1]:+.1f}") if "zoom" in meas else ""
        lines.append(f"    SHIFT     {meas['shift'][0]:+.1f}, {meas['shift'][1]:+.1f} px from the reference spindle "
                     f"{REF_SPINDLE[0]}, {REF_SPINDLE[1]} -> hub centre {meas['hub_centre'][0]}, "
                     f"{meas['hub_centre'][1]} (set {HUB_CENTRE[0]}, {HUB_CENTRE[1]}){z}")
    if "boss_mid" in meas:
        bm = meas["boss_mid"]
        what = "PIN_MID " if "pins" in meas else "BOSS_MID"
        lines.append(f"    {what}  {bm[0]}, {bm[1]} (from spindle {bm[2]:+.1f}, {bm[3]:+.1f})"
                     + (f"; against its usual place for this type / side {meas['pin_shift'][0]:+.1f}, "
                        f"{meas['pin_shift'][1]:+.1f} (camera movement)" if "pin_shift" in meas else ""))
    if "SPINDLE" in found:
        lines.append(f"    SPINDLE   centre {meas['spindle'][0]}, {meas['spindle'][1]}  r {meas['spindle'][2]}")
    elif "spindle" in meas:
        lines.append(f"    SPINDLE   not found - positions below are from the expected spindle position "
                     f"{meas['spindle'][0]:.0f}, {meas['spindle'][1]:.0f}")
    for s in ("L", "R"):
        b = meas.get("boss_" + s)
        if b:
            bb = found["BOSS_" + s]
            pin = (f"  pin {bb['core_x']:.1f}, {bb['core_y']:.1f}  ring {bb['ring_grey']:.0f} core "
                   f"{bb['core_grey']:.0f}  votes {bb['votes']}") if "core_x" in bb else ""
            lines.append(f"    BOSS_{s}    from spindle {b[0]:+.1f}, {b[1]:+.1f}  r {b[2]}  area {bb['area']}  "
                         f"(fill {bb['round_fill']:.2f}, grey {bb['grey']:.0f}){pin}")
    if "boss_distance" in meas:
        lines.append(f"    {'PINS     ' if 'pins' in meas else 'BOSSES   '} distance {meas['boss_distance']} px  "
                     f"angle {meas['boss_angle_deg']:+.2f} deg")
    for s in ("L", "R"):
        u = found.get("UPPER_ARM_" + s)
        if u:
            lines.append(f"    UPPER_ARM_{s} area {u['area']}  box {u['bbox']}  grey {u['grey']:.0f}")
    if "lower_area" in meas:
        lines.append(f"    LOWER_ARM area {meas['lower_area']}  box {meas['lower_bbox']}  "
                     f"tip from spindle {meas['lower_tip'][0]:+d}, {meas['lower_tip'][1]:+d}")
    if masks is not None:
        ref = meas.get("spindle_used", meas.get("spindle", (0, 0)))
        for m in masks:
            lines.append("    MASK      " + mask_line(m, ref))
    return lines


def named(masks: List[Dict]) -> List[Dict]:
    """The masks worth a line: all of the fast method's (4-5), only the named ones of the automatic method (~90)."""
    return [m for m in masks if m["label"]]


def mask_line(m: Dict, ref=None) -> str:
    """Every measure of one mask on one line."""
    rel = f" (from spindle {m['cx'] - ref[0]:+.1f}, {m['cy'] - ref[1]:+.1f})" if ref else ""
    return (f"{(m['label'] or str(m['id'])):16s} area {m['area']}, centroid {m['cx']:.1f}, {m['cy']:.1f}{rel}, "
            f"perimeter {m['perimeter']:.1f}, eq.diameter {m['eq_diameter']:.1f}, circularity {m['circularity']:.3f}, "
            f"circle {m['circle_x']:.1f}, {m['circle_y']:.1f} r {m['radius']:.1f} (fill {m['round_fill']:.2f}), "
            f"ellipse {m['ellipse_x']:.1f}, {m['ellipse_y']:.1f} axes {m['ellipse_major']:.1f}/{m['ellipse_minor']:.1f} "
            f"(ratio {m['ellipse_ratio']:.3f}, {m['ellipse_angle']:.1f} deg), holes {m['holes']} "
            f"({m['holes_area']} px), box {m['bbox']}, rect fill {m['rect_fill']:.2f}, grey {m['grey']:.0f}, "
            f"score {m['iou']:.2f}")


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
    for k in ("BOSS_L", "BOSS_R"):
        b = found.get(k)
        if b is not None and "core_x" in b:
            cv2.circle(out, (int(round(b["core_x"])), int(round(b["core_y"]))), 4, (255, 0, 0), -1)
    for (x0, y0, x1, y1) in found.get("measures", {}).get("boxes", []):
        cv2.rectangle(out, (x0, y0), (x1, y1), (255, 255, 255), 1)
    for (px, py) in found.get("measures", {}).get("prompts", []):
        cv2.circle(out, (px, py), 4, (255, 255, 255), -1)
        cv2.circle(out, (px, py), 4, (0, 0, 0), 1)
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


MASK_FIELDS = ["picture", "id", "label", "area", "cx", "cy", "perimeter", "eq_diameter", "circularity",
               "circle_x", "circle_y", "radius", "round_fill", "ellipse_x", "ellipse_y", "ellipse_major",
               "ellipse_minor", "ellipse_ratio", "ellipse_angle", "holes", "holes_area", "rect_fill", "bbox", "grey",
               "iou", "stability"]


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
            fh.write("\n".join(summary_lines(name, found, len(masks), secs, named(masks))) + "\n")
    return csv_path


def run_batch(args) -> None:
    study = KnuckleMaskStudy(args.sam)
    method = FAST if args.method == "prompted" else str(args.pps)
    print(f"MobileSAM on {study.device}, method {method if method == FAST else 'all masks, grid ' + method}")
    if method == FAST:      # first call pays for CUDA / kernel set-up: not a measure of the method
        study.run(np.zeros((IMAGE_SIZE[1], IMAGE_SIZE[0], 3), np.uint8), FAST, args.boss_points)
    results = {}
    for path in list_pictures(args.batch):
        image = load_picture(path)
        masks, found, secs = study.run(image, method, args.boss_points)
        name = os.path.basename(path)
        results[name] = (image, masks, found, secs)
        print("\n".join(summary_lines(name, found, len(masks), secs, named(masks))), flush=True)
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
    pps_var = tk.StringVar(value=FAST if args.method == "prompted" else str(args.pps))
    bp_var = tk.StringVar(value=str(args.boss_points))

    def method_key():
        return pps_var.get() + ("|%s" % bp_var.get() if pps_var.get() == FAST else "")
    show_var = tk.StringVar(value="Named parts" if args.method == "prompted" else "All masks")
    ttk.Button(top, text="Folder...", command=lambda: choose_folder()).pack(side="left")
    ttk.Label(top, textvariable=folder_var, width=40).pack(side="left", padx=4)
    ttk.Label(top, text="Picture").pack(side="left")
    pic_box = ttk.Combobox(top, textvariable=pic_var, width=34, state="readonly")
    pic_box.pack(side="left", padx=4)
    ttk.Label(top, text="Method / grid").pack(side="left")
    ttk.Combobox(top, textvariable=pps_var, values=[FAST, "16", "24", "32", "48"], width=14, state="readonly").pack(
        side="left", padx=4)
    ttk.Label(top, text="Boss points").pack(side="left")
    bp_box = ttk.Combobox(top, textvariable=bp_var, values=[str(n) for n in BOSS_POINT_CHOICES], width=5,
                          state="readonly")
    bp_box.pack(side="left", padx=4)
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
    cols = ("id", "label", "area", "centroid", "perim.", "circ.", "r", "fill", "grey")
    tree = ttk.Treeview(mid, columns=cols, show="headings", height=26)
    for c, w in zip(cols, (35, 110, 60, 100, 55, 45, 40, 40, 40)):
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
        return name, state["results"].get((name, method_key()))

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
                    m["id"], m["label"], m["area"], f"{m['cx']:.1f},{m['cy']:.1f}", f"{m['perimeter']:.0f}",
                    f"{m['circularity']:.2f}", f"{m['radius']:.0f}", f"{m['round_fill']:.2f}", f"{m['grey']:.0f}"))

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
            meas = res[2].get("measures", {})
            say("mask " + mask_line(m, meas.get("spindle_used", meas.get("spindle"))))
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
                state["study"].run(np.zeros((IMAGE_SIZE[1], IMAGE_SIZE[0], 3), np.uint8), FAST)   # warm-up
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
        pps = pps_var.get()
        bp = bp_var.get()
        key = method_key()

        def work():
            for n in names:
                if (n, key) in state["results"]:
                    jobs.put(("done", n, key, None))
                    continue
                image = load_picture(os.path.join(state["folder"], n))
                masks, found, secs = state["study"].run(image, pps, bp)
                jobs.put(("done", n, key, (image, masks, found, secs)))
            jobs.put(("idle", None, None, None))

        say(f"segmenting {len(names)} picture(s), " + (pps if pps == FAST else f"all masks, grid {pps} x {pps}") + " ...")
        threading.Thread(target=work, daemon=True).start()

    def poll():
        try:
            while True:
                kind, name, extra, payload = jobs.get_nowait()
                if kind == "done":
                    if payload is not None:
                        state["results"][(name, extra)] = payload
                        image, masks, found, secs = payload
                        for line in summary_lines(name, found, len(masks), secs, named(masks)):
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
                        say("    prompt mask " + mask_line(m))
                    if out:
                        state["prompt"] = out[0]["mask"]
                        state["highlight"] = None
                        show()
        except queue.Empty:
            pass
        root.after(150, poll)

    def save():
        res = {n: v for (n, p), v in state["results"].items() if p == method_key()}
        if not res:
            say("nothing segmented yet at this grid")
            return
        d = filedialog.askdirectory(initialdir=state["folder"], title="Folder for the results")
        if d:
            say("saved " + save_results(d, res))

    pic_box.bind("<<ComboboxSelected>>", on_pick)
    show_box.bind("<<ComboboxSelected>>", show)
    bp_box.bind("<<ComboboxSelected>>", on_pick)
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
    ap.add_argument("--method", choices=["prompted", "all"], default="prompted",
                    help="prompted: 4 points per part, about 1 s (default); all: every mask (automatic generator)")
    ap.add_argument("--pps", type=int, default=32, help="all: points per side of the generator grid")
    ap.add_argument("--boss-points", choices=[str(c) for c in BOSS_POINT_CHOICES], default="grid",
                    help="prompted: how the bosses are found - grid (default: 4 x 4 grid of 2-point prompts, best "
                         "boss by its bright ring round the dark pin) or a box + 4 / 8 / 16 points")
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
