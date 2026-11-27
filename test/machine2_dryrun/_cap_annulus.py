"""
_cap_annulus - the measure of Study_Cap_Annulus.py / Study_Cap_Annulus_UI.py (CHANGES 203, 204; Uddi 2026-11-27).
Study only: production is not changed.

measure(picture, model) per cap picture:
  1. Steps 1 - 2 of CheckCap (production helpers, round vision.cap.center): R-B in the brass and silver circles,
     brightness within r 25 -> which cap (brass / silver / none) and whether it is the model's cap. The brightening
     step (needs the hub / top bearing / nut pictures of the same part) is not run.
  2. Hub centre: the hub's holes in this picture by the production routine (FindHubHoles.findHoles, check "cap") ->
     centre H and radius R of the circle through the hole centres.
  3. Cap mask, two ways (maskMethod):
     "boxes" (default, CHANGES 205): the steps of the production cap mask (CapMaskFinder.findCap, CHANGES 139 - 142):
       the picture brightened by gamma vision.cap.mask.gamma (2.0), a disc of r vision.cap.mask.disc.radius (160) cut
       round H, the object-aware YOLO's boxes on the disc as MobileSAM box prompts (MobileSAMv2), predicted IoU /
       stability filters and box merging, then masks mostly outside the disc, below area.min, the disc's own edge and
       above area.max dropped; the largest left is the cap. Only the disc's centre differs from production (H here,
       vision.cap.center there).
     "points" (CHANGES 203): MobileSAM with 24 positive points round H (r 30 / 60 / 90 px x angles 0, 45, ..., 315),
       one mask; the part connected to H.
     Then its outline (largest outer contour) and the two-stage ellipse fitted to it (CapMaskFinder.fitCapEllipse, the
     production cap ellipse fit).
  4. Annulus = between the cap outline and the hole circle. Diameters through H at 15, 30, ..., 180 deg (12); on each,
     the length inside the annulus on the + side and on the - side (R - distance from H to the cap outline along
     that ray, 0 if the outline is outside the circle). Ratio = longer / shorter (shorter floored at `floor` px).
     Score = sum of the 12 ratios: 12.0 for a cap centred on the hole circle.
     Computed twice: with the fitted ellipse as the outline ("ellipse") and with the mask's own outline ("mask").
"""
import math
import os
from typing import Optional
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("COSTHETA_PLC_VENDOR", "siemens" if HERE.name.startswith("machine2") else "allenbradley")
MOBILESAM_DIR = ROOT / "models" / "MobileSAM"
if MOBILESAM_DIR.exists() and str(MOBILESAM_DIR) not in sys.path:
    sys.path.append(str(MOBILESAM_DIR))

import cv2
import numpy as np

import camera.CheckCap as CheckCapModule
from camera.CheckCap import CheckCap
from camera import CapMaskFinder
from camera.FindHubHoles import FindHubHoles

CheckCapModule.logBoth = lambda *a, **k: None          # the study shows its own lines

PROMPT_RADII = (30, 60, 90)
PROMPT_ANGLES = tuple(range(0, 360, 45))
DIAMETER_ANGLES = tuple(range(15, 181, 15))            # 15, 30, ..., 180: 12 diameters
EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp")
MODELS = ("DOST", "DOSTPLUS")
MASK_METHODS = ("boxes", "points", "annulus")
ANNULUS_INSET = 5                  # px: the annulus prompts lie on a circle this far inside the hole circle
ANNULUS_STEP_DEG = 5               # one prompt every this many degrees on it ...
ANNULUS_HOLE_CLEARANCE = 28        # px: ... except within this distance of a hole centre (the holes are r ~15 - 20)
ANNULUS_NEGATIVE_RADII = (0, 30, 60)   # negative prompts on the cap: H, and r 30 / 60 x every 45 deg (17 points)
MASK_GAMMA_DEFAULT = float(CapMaskFinder.GAMMA)        # production cap mask: 2.0

HEADER = (["file", "model", "mask_method", "prompts", "ann_area", "inner_points", "inliers", "inlier_share",
           "inlier_mean_dist", "ell_angle", "axis_ratio", "ell_hub_dist", "islands", "width_min", "width_max",
           "width_mean", "width_sd", "width_p90", "w_opp_max", "w_opp_mean", "verdict", "why", "widths", "holes_gamma", "mask_gamma", "yolo_conf", "yolo_iou", "max_det", "multimask", "stability_min", "iou_min", "merge_iou", "stages",
           "masks_in_circle", "cap_found", "right_cap", "rb_brass", "rb_silver", "brightness", "holes_found", "hub_x",
           "hub_y", "hole_r", "sam_score", "mask_area", "ell_cx", "ell_cy", "ell_a", "ell_b", "ell_minus_hub_dx",
           "ell_minus_hub_dy", "score_ellipse", "score_mask", "max_ratio_ellipse", "max_ratio_mask", "seconds", "note"]
          + [f"ell_{d}" for d in DIAMETER_ANGLES] + [f"mask_{d}" for d in DIAMETER_ANGLES])


def configure(samPath: str | None = None, holes: str | None = None) -> None:
    """samPath: MobileSAM weights file (else ModelManager, as production). holes "morphology": the old dark-blob hole
    finder (where the YOLO weights are missing - the sandbox)."""
    if holes == "morphology":
        FindHubHoles.METHOD = "morphology"
    if samPath:
        cache = {}

        def _loadSam():
            if "m" not in cache:
                import torch
                from mobile_sam import sam_model_registry
                m = sam_model_registry["vit_t"](checkpoint=samPath)
                m.to("cuda" if torch.cuda.is_available() else "cpu").eval()
                cache["m"] = m
            return cache["m"]

        CheckCap.samModelProvider = staticmethod(_loadSam)
        FindHubHoles.samModelProvider = staticmethod(_loadSam)


# The boxes method's settings (CHANGES 207, 208). Defaults = production (CapMaskFinder: the properties values, YOLO's
# NMS 0.7 and 300 detections, one MobileSAM mask per box), so a run at the defaults gives the cap check's masks.
MASK_DEFAULTS = dict(conf=float(CapMaskFinder.YOLO_CONFIDENCE), yolo_iou=0.7, max_det=300, multimask=False,
                     iou=float(CapMaskFinder.IOU_MIN), stability=float(CapMaskFinder.STABILITY_MIN),
                     merge=float(CapMaskFinder.MERGE_IOU))
# Uddi's over-detection settings of 2026-11-27 09:09, as they map onto the box-prompt pipeline
MASK_OVERDETECT = dict(conf=0.10, yolo_iou=0.80, max_det=1000, multimask=True, iou=0.72, stability=0.78, merge=0.88)
# The study's defaults since CHANGES 210: Uddi's window values of 2026-11-27 11:00 (his Not OK run)
MASK_STUDY = dict(conf=0.05, yolo_iou=0.95, max_det=300, multimask=True, iou=0.15, stability=0.25, merge=0.90)
STUDY_METHOD = "annulus"
STUDY_HOLES_GAMMA = 1.0
STUDY_MASK_GAMMA = 2.0
INNER_TOLERANCE = 2.0              # px: an inner edge point this close to the inner ellipse is on it
INNER_TRIES = 1500                 # RANSAC ellipses through 5 inner edge points (fixed seed)
WIDTH_STEP_DEG = 10                # annulus width printed every this many degrees (36 values)

# Uddi's verdict rule of 2026-11-27 11:29 (CHANGES 211), annulus method. 0 errors on his 39 OK / 33 NOT OK pictures of
# 26 Nov (two sessions, 13 parts each). Stage 1: any -> NOT OK. Stage 2: any -> OK. Else NOT OK.
RULE_NOT_OK = (("centre offset", "ell_hub_dist", ">", 7.0), ("score_ellipse", "score_ellipse", ">", 16.0),
               ("islands", "islands", ">", 230), ("w_opp_max", "w_opp_max", ">", 17), ("width_p90", "width_p90", ">", 50),
               ("width_max", "width_max", ">", 52))
RULE_OK = (("inlier_share", "inlier_share", ">", 0.70), ("width_sd", "width_sd", "<", 3.5), ("islands", "islands", "<", 200),
           ("w_opp_max", "w_opp_max", "<", 11), ("width_p90", "width_p90", "<", 47), ("width_max", "width_max", "<", 48.25))
_mask = dict(MASK_STUDY)


def maskParameters() -> dict:
    """The values the boxes method uses now."""
    return dict(_mask)


def setMaskParameters(conf=None, stability=None, iou=None, merge=None, yolo_iou=None, max_det=None,
                      multimask=None) -> dict:
    """Study only (CHANGES 207, 208): the boxes method's YOLO confidence, YOLO NMS IoU, YOLO maximum detections,
    MobileSAM masks per box (multimask: 3 nested masks instead of 1), MobileSAM's minimum predicted IoU and stability
    score, and the box overlap above which two masks are merged (1.0 = none). None leaves a value as it is. The
    properties file, CapMaskFinder and production are not touched. They change both the masks in the third panel and
    the cap mask chosen from them."""
    for name, value in (("conf", conf), ("stability", stability), ("iou", iou), ("merge", merge),
                        ("yolo_iou", yolo_iou)):
        if value is not None:
            _mask[name] = float(value)
    if max_det is not None:
        _mask["max_det"] = int(max_det)
    if multimask is not None:
        _mask["multimask"] = bool(multimask)
    return maskParameters()


def yoloSamMasksTunable(crop: np.ndarray, disc: np.ndarray, predictor, yolo):
    """CapMaskFinder.yoloSamMasks with every setting from _mask (CHANGES 208): YOLO conf / NMS IoU / max_det, MobileSAM
    multimask, predicted IoU / stability minimums, merge overlap. At MASK_DEFAULTS it is the production function's steps
    exactly (one mask per box). -> (list of dict(segmentation, predicted_iou, stability_score), counts)."""
    import torch
    from torchvision.ops import batched_nms
    from mobile_sam.utils.amg import batched_mask_to_box, calculate_stability_score
    rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
    results = yolo.predict(rgb, conf=float(_mask["conf"]), iou=float(_mask["yolo_iou"]), max_det=int(_mask["max_det"]),
                           verbose=False)
    boxes = (results[0].boxes.xyxy.cpu().numpy() if len(results) and results[0].boxes is not None
             else np.zeros((0, 4)))
    counts = dict(boxes=len(boxes))
    h, w = disc.shape
    if len(boxes):
        cxs = np.clip(((boxes[:, 0] + boxes[:, 2]) / 2).astype(int), 0, w - 1)
        cys = np.clip(((boxes[:, 1] + boxes[:, 3]) / 2).astype(int), 0, h - 1)
        boxes = boxes[disc[cys, cxs]]
    counts["onDisc"] = len(boxes)
    if len(boxes) == 0:
        counts.update(samMasks=0, passed=0, merged=0)
        return [], counts
    predictor.set_image(rgb)
    model = predictor.model
    multimask = bool(_mask["multimask"])
    allMasks, allIou, allStab = [], [], []
    with torch.inference_mode():
        for i in range(0, len(boxes), CapMaskFinder.BOX_BATCH):
            b = torch.as_tensor(boxes[i:i + CapMaskFinder.BOX_BATCH], dtype=torch.float, device=predictor.device)
            tb = predictor.transform.apply_boxes_torch(b, rgb.shape[:2])
            logits, iou, _ = predictor.predict_torch(point_coords=None, point_labels=None, boxes=tb,
                                                     multimask_output=multimask, return_logits=True)
            logits, iou = logits.flatten(0, 1), iou.flatten()       # B x 1 (or B x 3) masks -> one list
            allStab.append(calculate_stability_score(logits, model.mask_threshold, 1.0))
            allMasks.append(logits > model.mask_threshold)
            allIou.append(iou)
        masks, iou, stab = torch.cat(allMasks), torch.cat(allIou), torch.cat(allStab)
        counts["samMasks"] = len(masks)
        keep = (iou >= float(_mask["iou"])) & (stab >= float(_mask["stability"])) & (masks.flatten(1).sum(1) > 0)
        counts["passed"] = int(keep.sum())
        masks, iou, stab = masks[keep], iou[keep], stab[keep]
        if len(masks) and float(_mask["merge"]) < 1.0:
            mboxes = batched_mask_to_box(masks).float()
            k = batched_nms(mboxes, iou, torch.zeros(len(masks), device=masks.device), float(_mask["merge"]))
            masks, iou, stab = masks[k], iou[k], stab[k]
    found = [dict(segmentation=m.cpu().numpy(), predicted_iou=float(q), stability_score=float(st))
             for m, q, st in zip(masks, iou, stab)]
    counts["merged"] = len(found)
    return found, counts


def holeFinder() -> str:
    return str(FindHubHoles.METHOD)


def modelFor(choice: str, name: str) -> str:
    """choice DOST / DOSTPLUS, or anything else = from the file name (DOSTPLUS if it holds it, else DOST)."""
    if choice in MODELS:
        return choice
    return "DOSTPLUS" if "DOSTPLUS" in name.upper() else "DOST"


def pictures(folder) -> list:
    folder = pathlib.Path(folder)
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONS)


def steps12(img: np.ndarray, model: str):
    """CheckCap steps 1 - 2 with the production helpers: (cap found, right cap, R-B brass, R-B silver, brightness)."""
    rbBrass = CheckCap._compute_rb_difference(img, CheckCap._get_circular_mask(img.shape, CheckCap.RADIUS_DOSTPLUS))
    rbSilver = CheckCap._compute_rb_difference(img, CheckCap._get_circular_mask(img.shape, CheckCap.RADIUS_DOST))
    bright = float(np.mean(img[CheckCap._get_circular_mask(img.shape, CheckCap.SILVER_BRIGHTNESS_RADIUS) > 0]))
    if rbBrass > CheckCap.RB_THRESHOLD_DOSTPLUS:
        found = "DOSTPLUS"
    elif rbSilver < CheckCap.RB_THRESHOLD_DOST and bright >= CheckCap.SILVER_BRIGHTNESS_MIN:
        found = "DOST"
    else:
        found = None
    return found, found == model, rbBrass, rbSilver, bright


def promptPoints(hx: float, hy: float) -> np.ndarray:
    return np.array([[hx + r * math.cos(math.radians(a)), hy + r * math.sin(math.radians(a))]
                     for r in PROMPT_RADII for a in PROMPT_ANGLES], dtype=np.float32)


def capMask(img: np.ndarray, hx: float, hy: float, gamma: float = 1.0):
    """(mask connected to H, MobileSAM score) or (None, reason)."""
    import torch
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    if gamma != 1.0:
        rgb = CapMaskFinder.applyGamma(rgb, gamma)
    predictor = CheckCap._sam_predictor()
    predictor.set_image(rgb)
    pts = promptPoints(hx, hy)
    with torch.inference_mode():
        masks, scores, _ = predictor.predict(point_coords=pts, point_labels=np.ones(len(pts)), multimask_output=False)
    mask = np.asarray(masks[0]).astype(np.uint8)
    n, labels = cv2.connectedComponents(mask)
    ix, iy = int(round(hx)), int(round(hy))
    if n < 2 or not (0 <= iy < mask.shape[0] and 0 <= ix < mask.shape[1]) or labels[iy, ix] == 0:
        return None, "mask does not cover the hub centre"
    return labels == labels[iy, ix], float(scores[0])


def annulusPrompts(hx: float, hy: float, R: float, holePoints) -> np.ndarray:
    """Points every ANNULUS_STEP_DEG on the circle ANNULUS_INSET px inside the hole circle, none within
    ANNULUS_HOLE_CLEARANCE px of a hole centre."""
    holes = np.asarray(holePoints, dtype=np.float64).reshape(-1, 2) if holePoints is not None else np.zeros((0, 2))
    r = R - ANNULUS_INSET
    pts = []
    for a in range(0, 360, ANNULUS_STEP_DEG):
        x, y = hx + r * math.cos(math.radians(a)), hy + r * math.sin(math.radians(a))
        if len(holes) and np.hypot(holes[:, 0] - x, holes[:, 1] - y).min() < ANNULUS_HOLE_CLEARANCE:
            continue
        pts.append((x, y))
    return np.array(pts, dtype=np.float32)


def annulusMask(img: np.ndarray, hx: float, hy: float, R: float, holePoints, gamma: float):
    """CHANGES 209 (Uddi 2026-11-27): a mask of ONLY the annulus between the cap and the holes. MobileSAM, one mask, from
    positive prompts on the circle ANNULUS_INSET px inside the hole circle (avoiding the holes) and negative prompts on
    the cap (H, and r 30 / 60 round it every 45 deg). With the one negative at H alone MobileSAM took hub and cap as one
    mask (99 % of the cap covered on 3 OK pictures); with the 17 it covered none of it and 96 % of the ring. The annulus = that mask inside the hole circle; the cap = the region inside the hole circle, not in
    the annulus, connected to H (its outline is the annulus's inner edge). -> (annulus, cap, score, prompts) or
    (None, None, reason, prompts)."""
    import torch
    pts = annulusPrompts(hx, hy, R, holePoints)
    if len(pts) < 3:
        return None, None, "too few annulus prompts", pts
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    rgb = CapMaskFinder.applyGamma(rgb, gamma)
    predictor = CheckCap._sam_predictor()
    predictor.set_image(rgb)
    negatives = np.array([[hx + r * math.cos(math.radians(a)), hy + r * math.sin(math.radians(a))]
                          for r in ANNULUS_NEGATIVE_RADII for a in (range(0, 360, 45) if r else (0,))], dtype=np.float32)
    coords = np.vstack([pts, negatives]).astype(np.float32)
    labels = np.r_[np.ones(len(pts)), np.zeros(len(negatives))]
    with torch.inference_mode():
        masks, scores, _ = predictor.predict(point_coords=coords, point_labels=labels, multimask_output=False)
    h, w = img.shape[:2]
    yy, xx = np.ogrid[:h, :w]
    circle = (xx - hx) ** 2 + (yy - hy) ** 2 <= R * R
    ann = np.asarray(masks[0]).astype(bool) & circle
    inside = (~ann & circle).astype(np.uint8)
    n, lab = cv2.connectedComponents(inside)
    ix, iy = int(round(hx)), int(round(hy))
    if not ann.any() or n < 2 or lab[iy, ix] == 0:
        return None, None, "annulus mask covers the hub centre or is empty", pts
    return ann, lab == lab[iy, ix], float(scores[0]), np.vstack([pts, negatives])


def innerEdgePoints(ann: np.ndarray, cap: np.ndarray) -> np.ndarray:
    """The annulus pixels that touch the region it encloses (the inner edge; pieces of the mask lying inside the cap
    give points too - they are outliers for the fit). -> N x 2 (x, y)."""
    grown = cv2.dilate(cap.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    ys, xs = np.nonzero(ann & grown)
    return np.c_[xs, ys].astype(np.float64)


def maxInliersEllipse(points: np.ndarray, tolerance: float = INNER_TOLERANCE, tries: int = INNER_TRIES,
                      seed: int = 0) -> Optional[dict]:
    """CHANGES 210 (Uddi 2026-11-27): the ellipse on which the MOST points lie within `tolerance` px - a consensus fit,
    not least squares (a least-squares ellipse is pulled by every stray point; this one ignores the points off it).
      1. candidates: the least-squares ellipse of all points (start only) and `tries` ellipses through 5 random points
         (fixed seed): the one with the most points within tolerance;
      2. then a local search on (cx, cy, a, b, angle) - steps 1 px / 1 px / 1 px / 1 px / 2 deg, halved down to
         0.05 px / 0.1 deg - taking any move that puts more points on the ellipse (equal count: a smaller mean distance
         of those points).
    -> dict(e=(cx, cy, a, b, angle deg), inliers, points, share, meanDist) or None."""
    if len(points) < 10:
        return None
    dist = lambda e: CapMaskFinder.ellipseDistances(points, e)

    def value(e):
        d = dist(e)
        on = d <= tolerance
        return int(on.sum()), (-float(d[on].mean()) if on.any() else 0.0)

    rng = np.random.default_rng(seed)
    pts32 = points.astype(np.float32)
    best = CapMaskFinder._cvToEllipse(cv2.fitEllipse(pts32))
    bestV = value(best)
    for _ in range(int(tries)):
        try:
            e = CapMaskFinder._cvToEllipse(cv2.fitEllipse(pts32[rng.choice(len(points), 5, replace=False)]))
        except cv2.error:
            continue
        if not (np.isfinite(e).all() and 20 < e[3] <= e[2] < 400):
            continue
        v = value(e)
        if v > bestV:
            best, bestV = e, v
    steps = np.array([1.0, 1.0, 1.0, 1.0, 2.0])
    e = np.array(best, dtype=np.float64)
    while steps[0] >= 0.05:
        moved = False
        for k in range(5):
            for sign in (1, -1):
                trial = e.copy()
                trial[k] += sign * steps[k]
                if trial[3] <= 0 or trial[2] <= 0:
                    continue
                v = value(tuple(trial))
                if v > bestV:
                    e, bestV, moved = trial, v, True
        if not moved:
            steps = steps / 2.0
    cx, cy, a, b, ang = e
    if b > a:
        a, b, ang = b, a, ang + 90.0
    e = (float(cx), float(cy), float(a), float(b), float(ang % 180.0))
    n, neg = value(e)
    return dict(e=e, inliers=n, points=len(points), share=n / len(points), meanDist=-neg)


def annulusWidths(hx: float, hy: float, R: float, ann: np.ndarray, stepDeg: int = WIDTH_STEP_DEG) -> list:
    """The ring's width along rays from H every stepDeg (see _ringWidthAt)."""
    return [_ringWidthAt(hx, hy, R, ann, math.radians(deg)) for deg in range(0, 360, stepDeg)]


def capMaskByBoxes(img: np.ndarray, hx: float, hy: float, gamma: float = MASK_GAMMA_DEFAULT):
    """The production cap mask's steps (CapMaskFinder.findCap) on a disc round H: (full-picture mask, info dict) or
    (None, info dict with reason). findCap returns no mask, so its steps are repeated here with its own functions and
    values; CapMaskFinder is not changed."""
    picture = CapMaskFinder.applyGamma(img, gamma)
    crop, disc, (x0, y0) = CapMaskFinder.cutDisc(picture, (hx, hy), CapMaskFinder.DISC_RADIUS)
    found, counts = yoloSamMasksTunable(crop, disc, CheckCap._sam_predictor(), CheckCap._yolo_model())
    allMasks = []                                      # every mask MobileSAM gave (after its filters and merging)
    for d in found:
        m = np.zeros(img.shape[:2], bool)
        seg = np.asarray(d["segmentation"]).astype(bool)
        m[y0:y0 + seg.shape[0], x0:x0 + seg.shape[1]] = seg
        allMasks.append(m)
    kept, outside, small, edge, large = [], 0, 0, 0, 0
    for d in found:
        seg = np.asarray(d["segmentation"]).astype(bool)
        inside = seg & disc
        n = int(inside.sum())
        if n * 2 < int(seg.sum()) or n == 0:
            outside += 1
            continue
        if n < CapMaskFinder.AREA_MIN:
            small += 1
            continue
        circle = CapMaskFinder.circleOfOutline(inside)
        if circle is not None and circle[2] > CapMaskFinder.DISC_EDGE_FRACTION * CapMaskFinder.DISC_RADIUS:
            edge += 1
            continue
        if n > CapMaskFinder.AREA_MAX:
            large += 1
            continue
        kept.append((d, inside, n))
    counts.update(outside=outside, small=small, edge=edge, large=large, kept=len(kept))
    info = dict(counts=counts, score=None, reason="", allMasks=allMasks)
    if not kept:
        info["reason"] = "no cap mask (no mask left after the filters)"
        return None, info
    d, inside, n = max(kept, key=lambda t: t[2])
    full = np.zeros(img.shape[:2], bool)
    full[y0:y0 + inside.shape[0], x0:x0 + inside.shape[1]] = inside
    info["score"] = float(d["predicted_iou"])
    info["stability"] = float(d["stability_score"])
    return full, info


def masksInCircle(allMasks: list, hx: float, hy: float, R: float, share: float = 0.5) -> list:
    """The masks with at least `share` of their area inside the hole circle, cut to the circle, largest first."""
    h, w = allMasks[0].shape if allMasks else (0, 0)
    yy, xx = np.ogrid[:h, :w]
    circle = (xx - hx) ** 2 + (yy - hy) ** 2 <= R * R
    out = []
    for m in allMasks:
        n = int(m.sum())
        inside = m & circle
        if n and inside.sum() >= share * n:
            out.append(inside)
    return sorted(out, key=lambda m: int(m.sum()), reverse=True)


PALETTE = [(230, 25, 75), (60, 180, 75), (255, 225, 25), (0, 130, 200), (245, 130, 48), (145, 30, 180),
           (70, 240, 240), (240, 50, 230), (210, 245, 60), (250, 190, 212), (0, 128, 128), (220, 190, 255),
           (170, 110, 40), (255, 250, 200), (128, 0, 0), (170, 255, 195), (128, 128, 0), (255, 215, 180)]


def masksPicture(img: np.ndarray, hub, masks: list) -> np.ndarray:
    """Every mask in the hole circle coloured (BGR, distinct colours), the largest painted first and each smaller one
    over it, blended 60 / 40 with the picture; the hole circle (blue). No values."""
    out = img.copy()
    paint = img.copy()
    for i, m in enumerate(masks):
        r, g, b = PALETTE[i % len(PALETTE)]
        paint[m] = (b, g, r)
    covered = np.zeros(img.shape[:2], bool)
    for m in masks:
        covered |= m
    out[covered] = (0.6 * paint[covered] + 0.4 * img[covered]).astype(np.uint8)
    for m in masks:                                       # thin dark edge round each mask so neighbours separate
        cs, _ = cv2.findContours(m.astype(np.uint8) * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        cv2.drawContours(out, cs, -1, (30, 30, 30), 1)
    if hub is not None:
        hx, hy, R = hub
        cv2.circle(out, (int(round(hx)), int(round(hy))), int(round(R)), (255, 128, 0), 1)
    return out


def describeCounts(c: dict) -> str:
    return (f"YOLO boxes {c.get('boxes', 0)} ({c.get('onDisc', 0)} on the disc), {c.get('samMasks', 0)} MobileSAM "
            f"masks, {c.get('passed', 0)} passed, "
            f"{c.get('merged', 0)} after merging; dropped {c.get('outside', 0)} outside / {c.get('small', 0)} small / "
            f"{c.get('edge', 0)} disc edge / {c.get('large', 0)} large; {c.get('kept', 0)} kept")


def rayToEllipse(hx, hy, phi, e):
    """Distance from H along direction phi (rad) to the ellipse e = (cx, cy, a, b, angle deg); None if no crossing."""
    cx, cy, a, b, ang = e
    c, s = math.cos(math.radians(ang)), math.sin(math.radians(ang))
    px, py = hx - cx, hy - cy
    px, py = c * px + s * py, -s * px + c * py
    vx, vy = math.cos(phi), math.sin(phi)
    vx, vy = c * vx + s * vy, -s * vx + c * vy
    A = vx * vx / (a * a) + vy * vy / (b * b)
    B = 2 * (px * vx / (a * a) + py * vy / (b * b))
    C = px * px / (a * a) + py * py / (b * b) - 1
    disc = B * B - 4 * A * C
    if disc < 0:
        return None
    t = (-B + math.sqrt(disc)) / (2 * A)
    return t if t > 0 else None


def rayToMask(hx, hy, phi, mask, limit):
    """Distance from H along phi to where the mask ends (first step outside it, 0.25 px steps), up to limit."""
    h, w = mask.shape
    t = 0.0
    while t <= limit:
        x, y = int(round(hx + t * math.cos(phi))), int(round(hy + t * math.sin(phi)))
        if not (0 <= x < w and 0 <= y < h) or not mask[y, x]:
            return t
        t += 0.25
    return limit


def score(dist, R, floor: float = 1.0):
    """dist(phi) -> distance to the outline. Returns (sum of ratios, [ratio per diameter], [(L+, L-) per diameter])."""
    ratios, lengths = [], []
    for deg in DIAMETER_ANGLES:
        sides = []
        for phi in (math.radians(deg), math.radians(deg + 180)):
            d = dist(phi)
            sides.append(0.0 if d is None else max(R - d, 0.0))
        ratios.append(max(sides) / max(min(sides), floor))
        lengths.append(tuple(sides))
    return sum(ratios), ratios, lengths


def measure(img: np.ndarray, model: str, floor: float = 1.0, maskMethod: str = STUDY_METHOD, holesGamma: float = 1.0,
            maskGamma: float = MASK_GAMMA_DEFAULT, inCircleShare: float = 0.5) -> dict:
    """holesGamma: gamma of the picture given to the hole finder (1 = as taken; FindHubHoles still tries its own
    gammas on it). maskGamma: gamma of the picture given to MobileSAM for the masks (every method; production cap
    mask 2.0)."""
    """Everything for one picture: the csv row values (HEADER keys) and, for drawing / showing, hub (hx, hy, R),
    pts, mask, ellipse, ratiosE / lengthsE, ratiosM / lengthsM."""
    t0 = time.perf_counter()
    res = dict.fromkeys(HEADER, "")
    mp = maskParameters()
    res.update(yolo_conf=mp["conf"], yolo_iou=mp["yolo_iou"], max_det=mp["max_det"], multimask=mp["multimask"],
               stability_min=mp["stability"], iou_min=mp["iou"], merge_iou=mp["merge"])
    res.update(holes_gamma=holesGamma, mask_gamma=maskGamma, annulus=None)
    res.update(model=model, mask_method=maskMethod, maskMethod=maskMethod, maskInfo="", masksPicture=None,
               masksInCircle="", hub=None, pts=np.zeros((0, 2)),
               mask=None, ellipse=None, ratiosE=None, lengthsE=None, ratiosM=None, lengthsM=None)
    found, right, rbB, rbS, bright = steps12(img, model)
    res.update(cap_found=found or "none", right_cap=right, rb_brass=f"{rbB:.1f}", rb_silver=f"{rbS:.1f}",
               brightness=f"{bright:.1f}")
    notes = [] if right else [f"steps 1-2 NOT OK (cap found {found or 'none'})"]
    holes = FindHubHoles.findHoles(CapMaskFinder.applyGamma(img, holesGamma), model, None, check="cap")
    res["holes_found"] = bool(holes.get("found"))
    if not holes.get("found"):
        notes.append(f"hub holes not found ({holes.get('reason')})")
    else:
        hx, hy, R = float(holes["cx"]), float(holes["cy"]), float(holes["radius"])
        res.update(hub=(hx, hy, R), hub_x=f"{hx:.1f}", hub_y=f"{hy:.1f}", hole_r=f"{R:.1f}")
        if maskMethod == "points":
            res["pts"] = promptPoints(hx, hy)
            mask, samScore = capMask(img, hx, hy, maskGamma)
            if mask is None:
                notes.append(samScore)
            inCircle = masksInCircle([mask], hx, hy, R, inCircleShare) if mask is not None else []
        elif maskMethod == "annulus":
            ann, cap, samScore, res["pts"] = annulusMask(img, hx, hy, R, holes.get("points"), maskGamma)
            res["annulus"] = ann
            mask = None                                   # the annulus method draws and scores only the annulus
            if ann is None:
                notes.append(samScore)
                inCircle = []
            else:
                inCircle = [ann]
                measureAnnulus(res, hx, hy, R, ann, cap, samScore, floor)
                if res.get("ellipse") is None:
                    notes.append("no inner ellipse (too few inner edge points)")
        else:
            mask, info = capMaskByBoxes(img, hx, hy, maskGamma)
            inCircle = masksInCircle(info["allMasks"], hx, hy, R, inCircleShare)
            c = info["counts"]
            res["stages"] = (f"{c.get('boxes', 0)}/{c.get('onDisc', 0)}/{c.get('samMasks', 0)}/{c.get('passed', 0)}/"
                             f"{c.get('merged', 0)}")
            res["maskInfo"] = describeCounts(info["counts"]) + (
                f"; predicted IoU {info['score']:.3f}, stability {info['stability']:.3f}" if mask is not None else "")
            samScore = info["score"]
            if mask is None:
                notes.append(info["reason"])
        res["masksPicture"] = masksPicture(img, res["hub"], inCircle)
        res["masksInCircle"] = ", ".join(str(int(m.sum())) for m in inCircle)
        res["masks_in_circle"] = len(inCircle)
        if mask is not None:
            res.update(mask=mask, sam_score=f"{samScore:.3f}", mask_area=int(mask.sum()))
            fit = CapMaskFinder.fitCapEllipse(mask, float(CapMaskFinder.ELLIPSE_TOLERANCE),
                                              int(CapMaskFinder.ELLIPSE_TRIES))
            if fit is None:
                notes.append("no ellipse (outline too short)")
            else:
                e = fit["e2"]
                res.update(ellipse=e, ell_cx=f"{e[0]:.1f}", ell_cy=f"{e[1]:.1f}", ell_a=f"{e[2]:.1f}",
                           ell_b=f"{e[3]:.1f}", ell_minus_hub_dx=f"{e[0] - hx:+.1f}", ell_minus_hub_dy=f"{e[1] - hy:+.1f}")
                sE, rE, lE = score(lambda phi: rayToEllipse(hx, hy, phi, e), R, floor)
                res.update(ratiosE=rE, lengthsE=lE, score_ellipse=f"{sE:.3f}", max_ratio_ellipse=f"{max(rE):.3f}",
                           **{f"ell_{d}": f"{r:.3f}" for d, r in zip(DIAMETER_ANGLES, rE)})
            sM, rM, lM = score(lambda phi: rayToMask(hx, hy, phi, mask, R + 5), R, floor)
            res.update(ratiosM=rM, lengthsM=lM, score_mask=f"{sM:.3f}", max_ratio_mask=f"{max(rM):.3f}",
                       **{f"mask_{d}": f"{r:.3f}" for d, r in zip(DIAMETER_ANGLES, rM)})
    res["seconds"] = f"{time.perf_counter() - t0:.2f}"
    res["note"] = "; ".join(notes)
    if maskMethod == "annulus":
        res["verdict"], res["why"] = verdict(res)
    return res


def measureAnnulus(res: dict, hx, hy, R, ann, cap, samScore, floor) -> None:
    """Annulus method (CHANGES 210): inner ellipse by the most inner edge points on it; scores on it ("ellipse") and on
    the ring's own inner edge along each ray ("mask" column = R minus the ring width); widths, islands; the ANNULUS
    line for the terminal."""
    inner = innerEdgePoints(ann, cap)
    fit = maxInliersEllipse(inner)
    widths = annulusWidths(hx, hy, R, ann)
    res.update(sam_score=f"{samScore:.3f}", mask_area=int(ann.sum()), ann_area=int(ann.sum()), inner_points=len(inner),
               width_min=f"{min(widths):.1f}", width_max=f"{max(widths):.1f}", width_mean=f"{np.mean(widths):.1f}",
               width_sd=f"{np.std(widths):.2f}", widths=" ".join(f"{w:.0f}" for w in widths),
               width_p90=f"{np.percentile(widths, 90):.1f}",
               w_opp_max=f"{max(abs(widths[i] - widths[i + len(widths) // 2]) for i in range(len(widths) // 2)):.1f}",
               w_opp_mean=f"{np.mean([abs(widths[i] - widths[i + len(widths) // 2]) for i in range(len(widths) // 2)]):.2f}",
               prompts=len(res["pts"]) - 17)
    res["maskInfo"] = (f"annulus mask {int(ann.sum())} px from {len(res['pts']) - 17} prompts on r {R - ANNULUS_INSET:.1f}"
                       f" + 17 negative on the cap; {len(inner)} inner edge points")
    if fit is not None:
        e = fit["e"]
        res.update(ellipse=e, ell_cx=f"{e[0]:.1f}", ell_cy=f"{e[1]:.1f}", ell_a=f"{e[2]:.1f}", ell_b=f"{e[3]:.1f}",
                   ell_angle=f"{e[4]:.1f}", ell_minus_hub_dx=f"{e[0] - hx:+.1f}", ell_minus_hub_dy=f"{e[1] - hy:+.1f}",
                   ell_hub_dist=f"{math.hypot(e[0] - hx, e[1] - hy):.2f}", axis_ratio=f"{e[3] / e[2]:.3f}",
                   inliers=fit["inliers"], inlier_share=f"{fit['share']:.3f}", inlier_mean_dist=f"{fit['meanDist']:.2f}")
        h, w = ann.shape
        yy, xx = np.mgrid[:h, :w]
        c, s_ = math.cos(math.radians(e[4])), math.sin(math.radians(e[4]))
        u, v = c * (xx - e[0]) + s_ * (yy - e[1]), -s_ * (xx - e[0]) + c * (yy - e[1])
        m = INNER_TOLERANCE + 1.0                             # pixels on the edge (within tolerance) are not islands
        insideEllipse = (u / max(e[2] - m, 1)) ** 2 + (v / max(e[3] - m, 1)) ** 2 < 1.0
        res["islands"] = int((ann & insideEllipse).sum())     # mask pixels well inside the inner ellipse
        sE, rE, lE = score(lambda phi: rayToEllipse(hx, hy, phi, e), R, floor)
        res.update(ratiosE=rE, lengthsE=lE, score_ellipse=f"{sE:.3f}", max_ratio_ellipse=f"{max(rE):.3f}",
                   **{f"ell_{d}": f"{r:.3f}" for d, r in zip(DIAMETER_ANGLES, rE)})
    sM, rM, lM = score(lambda phi: R - _ringWidthAt(hx, hy, R, ann, phi), R, floor)
    res.update(ratiosM=rM, lengthsM=lM, score_mask=f"{sM:.3f}", max_ratio_mask=f"{max(rM):.3f}",
               **{f"mask_{d}": f"{r:.3f}" for d, r in zip(DIAMETER_ANGLES, rM)})


HOLE_ZONE = 45.0                   # px inside the hole circle where gaps in the ring (the holes) are passed over


def _ringWidthAt(hx, hy, R, ann, phi, holeZone: float = HOLE_ZONE) -> float:
    """The ring's width along the ray from H at phi (rad): walking in from the hole circle, gaps in the mask within
    holeZone px of the circle (a hole, the ring's thin edge round it) are passed over; after that the first pixel off
    the mask ends the ring. Width = R minus the distance of the last mask pixel (so pieces of the mask inside the cap,
    not joined to the ring, do not count)."""
    h, w = ann.shape
    c, s_ = math.cos(phi), math.sin(phi)
    t, last = R - 0.5, None
    while t > 0:
        x, y = int(round(hx + t * c)), int(round(hy + t * s_))
        if 0 <= x < w and 0 <= y < h and ann[y, x]:
            last = t
        elif R - t > holeZone:
            break
        t -= 0.25
    return R - last if last is not None else 0.0


def _ruleHits(res: dict, rules) -> list:
    hits = []
    for label, key, op, limit in rules:
        v = res.get(key)
        if v in ("", None):
            continue
        v = float(v)
        if (v > limit) if op == ">" else (v < limit):
            hits.append(f"{label} {v:g} {op} {limit:g}")
    return hits


def verdict(res: dict):
    """Uddi's rule (RULE_NOT_OK, RULE_OK): ("OK" / "NOT OK", "stage 1: ..." / "stage 2: ..." / "stage 3: ..."), or
    ("", "") where the annulus values are missing (no holes / no annulus / no ellipse -> NOT OK, reason given)."""
    if res.get("annulus") is None or res.get("ellipse") is None:
        return "NOT OK", f"no annulus values ({res.get('note') or 'annulus method not run'})"
    bad = _ruleHits(res, RULE_NOT_OK)
    if bad:
        return "NOT OK", "stage 1 (any -> NOT OK): " + "; ".join(bad)
    good = _ruleHits(res, RULE_OK)
    if good:
        return "OK", "stage 2 (any -> OK): " + "; ".join(good)
    return "NOT OK", "stage 3: no stage-1 and no stage-2 condition met"


def annulusLine(name: str, res: dict) -> str:
    """One line per picture for the terminal (CHANGES 210): every annulus value, key=value, for Uddi to send back."""
    keys = ("model", "rb_silver", "brightness", "hub_x", "hub_y", "hole_r", "prompts", "sam_score", "ann_area",
            "inner_points", "inliers", "inlier_share", "inlier_mean_dist", "ell_cx", "ell_cy", "ell_a", "ell_b",
            "ell_angle", "axis_ratio", "ell_minus_hub_dx", "ell_minus_hub_dy", "ell_hub_dist", "islands", "width_min",
            "width_max", "width_mean", "width_sd", "width_p90", "w_opp_max", "w_opp_mean", "score_ellipse", "max_ratio_ellipse", "score_mask", "max_ratio_mask",
            "seconds")
    parts = [f"{k}={res.get(k, '')}" for k in keys]
    return (f"ANNULUS file={name} " + " ".join(parts) + f" widths10=[{res.get('widths', '')}] note={res.get('note', '')!r}"
            f" verdict={res.get('verdict', '')!r} why={res.get('why', '')!r}")


def marked(img: np.ndarray, res: dict) -> np.ndarray:
    """Outlines only, no values: hole circle and hub centre (blue), prompt points (cyan; points / annulus methods),
    annulus mask edges (magenta; annulus method), cap mask outline (green),
    ellipse (yellow), diameters (thin grey)."""
    out = img.copy()
    if res.get("hub") is None:
        return out
    hx, hy, R = res["hub"]
    for deg in DIAMETER_ANGLES:
        phi = math.radians(deg)
        p1 = (int(round(hx + R * math.cos(phi))), int(round(hy + R * math.sin(phi))))
        p2 = (int(round(hx - R * math.cos(phi))), int(round(hy - R * math.sin(phi))))
        cv2.line(out, p1, p2, (160, 160, 160), 1)
    cv2.circle(out, (int(round(hx)), int(round(hy))), int(round(R)), (255, 128, 0), 1)
    cv2.drawMarker(out, (int(round(hx)), int(round(hy))), (255, 128, 0), cv2.MARKER_CROSS, 16, 2)
    negatives = 17 if res.get("annulus") is not None else 0      # annulus method: the last 17 prompts are negative
    for i, (x, y) in enumerate(res["pts"]):
        colour = (0, 0, 255) if i >= len(res["pts"]) - negatives else (255, 255, 0)
        cv2.circle(out, (int(round(x)), int(round(y))), 2, colour, -1)
    if res.get("annulus") is not None:                   # annulus method: the annulus mask's edges (magenta)
        cs, _ = cv2.findContours(res["annulus"].astype(np.uint8) * 255, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
        cv2.drawContours(out, cs, -1, (255, 0, 255), 1)
    if res.get("mask") is not None:
        cs, _ = cv2.findContours(res["mask"].astype(np.uint8) * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        cv2.drawContours(out, cs, -1, (0, 255, 0), 1)
    if res.get("ellipse") is not None:
        cv2.drawContours(out, [CapMaskFinder.ellipseContour(res["ellipse"])], -1, (0, 255, 255), 2)
    return out


def detailLines(name: str, res: dict) -> list:
    """The text lines of one picture (values go here, never on the picture)."""
    lines = [f"{name}  {res['model']}  {res['seconds']} s  (gamma: holes {res.get('holes_gamma')}, masks {res.get('mask_gamma')})",
             f"  steps 1-2: cap found {res['cap_found']}, right cap {res['right_cap']}, R-B brass {res['rb_brass']} / "
             f"silver {res['rb_silver']}, brightness r25 {res['brightness']}"]
    if res.get("hub") is not None:
        lines.append(f"  hub centre ({res['hub_x']}, {res['hub_y']}), hole circle r {res['hole_r']} ({holeFinder()})")
    if res.get("mask") is not None:
        lines.append(f"  cap mask ({res['maskMethod']}) area {res['mask_area']}, SAM score {res['sam_score']}")
    if res.get("maskInfo"):
        lines.append(f"  {res['maskInfo']}")
    if res.get("masks_in_circle") not in ("", None):
        lines.append(f"  masks in the hole circle: {res['masks_in_circle']} (areas, largest first: {res['masksInCircle']})")
    if res.get("mask_method") == "boxes":
        lines.append(f"  YOLO conf {res['yolo_conf']}, iou {res['yolo_iou']}, max_det {res['max_det']}; MobileSAM "
                     f"multimask_output {res['multimask']}, pred_iou_thresh {res['iou_min']}, stability_score_thresh "
                     f"{res['stability_min']}, box_nms_thresh {res['merge_iou']}")
    if res.get("annulus") is not None and res.get("inliers") not in ("", None):
        lines.append(f"  inner ellipse: {res['inliers']} of {res['inner_points']} inner edge points within "
                     f"{INNER_TOLERANCE} px ({res['inlier_share']}), mean distance {res['inlier_mean_dist']} px; angle "
                     f"{res['ell_angle']}, axis ratio {res['axis_ratio']}, centre - hub distance {res['ell_hub_dist']} px; "
                     f"mask inside it {res['islands']} px")
    if res.get("annulus") is not None:
        lines.append(f"  ring width min / mean / max / sd / p90 {res['width_min']} / {res['width_mean']} / {res['width_max']} / "
                     f"{res['width_sd']} / {res['width_p90']} px, opposite sides max / mean {res['w_opp_max']} / {res['w_opp_mean']}; every {WIDTH_STEP_DEG} deg from 0: {res['widths']}")
    if res.get("ellipse") is not None:
        lines.append(f"  ellipse centre ({res['ell_cx']}, {res['ell_cy']}), axes {res['ell_a']} / {res['ell_b']}, "
                     f"ellipse - hub {res['ell_minus_hub_dx']} / {res['ell_minus_hub_dy']} px")
    maskLabel = "ring's inner edge" if res.get("annulus") is not None else "mask"
    for label, ratios, lengths, total in (("ellipse", res.get("ratiosE"), res.get("lengthsE"), res["score_ellipse"]),
                                          (maskLabel, res.get("ratiosM"), res.get("lengthsM"), res["score_mask"])):
        if ratios is None:
            continue
        lines.append(f"  score ({label}) {total} - per diameter (deg): L+ / L- (px) = longer / shorter")
        lines.append("    " + "  ".join(f"{d:3d}: {a:4.1f}/{b:4.1f}={r:.2f}" for d, (a, b), r
                                        in list(zip(DIAMETER_ANGLES, lengths, ratios))[:6]))
        lines.append("    " + "  ".join(f"{d:3d}: {a:4.1f}/{b:4.1f}={r:.2f}" for d, (a, b), r
                                        in list(zip(DIAMETER_ANGLES, lengths, ratios))[6:]))
    if res["note"]:
        lines.append(f"  note: {res['note']}")
    if res.get("verdict"):
        lines.append(f"  VERDICT {res['verdict']} - {res['why']}")
    return lines


def summaryLines(rows: list) -> list:
    lines = ["Summary (pictures with a score):"]
    verdicts = [r.get("verdict") for r in rows if r.get("verdict")]
    if verdicts:
        lines.append(f"  verdicts: OK {verdicts.count('OK')}, NOT OK {verdicts.count('NOT OK')}")
    for key in ("score_ellipse", "score_mask", "max_ratio_ellipse", "max_ratio_mask"):
        v = np.array([float(r[key]) for r in rows if r.get(key) not in ("", None)])
        if len(v):
            lines.append(f"  {key:<18} n {len(v):3d}  min {v.min():7.3f}  p10 {np.percentile(v, 10):7.3f}  median "
                         f"{np.median(v):7.3f}  p90 {np.percentile(v, 90):7.3f}  max {v.max():7.3f}")
    lines.append(f"  with a note (steps 1-2 NOT OK / no holes / no mask): {sum(1 for r in rows if r.get('note'))}")
    return lines


def writeCsv(path, rows: list) -> None:
    import csv
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADER, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
