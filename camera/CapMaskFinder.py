"""
CapMaskFinder - the cap's mask and its ellipse, by the method frozen in CHANGES 139 - 142
(test/machine*_dryrun/DryRun_Cap_Centre_Mask_UI.py), for the cap seating step (CheckCap, CHANGES 143).

  1. The cap picture brightened (255 x (v / 255) ^ (1 / gamma), gamma 2.0) and a disc of radius 160 cut round the
     cap centre (black outside it).
  2. MobileSAMv2: the object-aware YOLO model's boxes on the disc (confidence >= 0.15) whose centre lies on the disc,
     as box prompts through MobileSAM (one mask per box); masks kept with predicted IoU >= 0.50 and stability >= 0.70;
     of two masks whose boxes overlap more than 0.50 the one with the higher predicted IoU is kept.
  3. Each mask kept to the disc; dropped: less than half inside the disc, smaller than 15000 px, the disc's own edge
     (least-squares circle radius above 0.9 x the disc's), larger than 50000 px. The largest left is the cap mask.
  4. An ellipse fitted to the cap mask's outline in two stages: (1) least squares - the ellipse minimising the sum of
     squared distances of the outline points from it; (2) the most outline points within 2.5 px - the stage-1 ellipse
     and 300 RANSAC ellipses (fixed seed, 5 points each) compete, the winner refitted by least squares to its inliers
     while the count rises. The stage-2 centre is the cap's centre (user, 2026-11-18).

No torch at import time; the models come from the caller (CheckCap: its MobileSAM predictor, ModelManager's YOLO).
"""
import time
from typing import Any, Dict, Optional, Tuple

import cv2
import numpy as np


GAMMA = 2.0
DISC_RADIUS = 160                  # px, round the cap centre
YOLO_CONFIDENCE = 0.15
STABILITY_MIN = 0.70           # MobileSAM's stability score of a mask
IOU_MIN = 0.50                       # MobileSAM's predicted IoU of a mask
MERGE_IOU = 0.50                   # box overlap above which two masks are one
AREA_MIN = 15000                      # px, inside the disc
AREA_MAX = 50000
DISC_EDGE_FRACTION = 0.9  # radius above this x the disc's = the disc
ELLIPSE_TOLERANCE = 2.5    # px; an outline point this close is on it
ELLIPSE_TRIES = 300              # RANSAC ellipses in stage 2

BOX_BATCH = 64                                                          # boxes per MobileSAM decoder pass


def applyGamma(picture: np.ndarray, gamma: float) -> np.ndarray:
    """255 x (v / 255) ^ (1 / gamma); gamma 1.0 leaves the picture as it is."""
    if abs(float(gamma) - 1.0) < 1e-9:
        return picture
    table = np.array([((i / 255.0) ** (1.0 / float(gamma))) * 255 for i in range(256)]).clip(0, 255).astype(np.uint8)
    return cv2.LUT(picture, table)


def cutDisc(picture: np.ndarray, centre, radius) -> Tuple[np.ndarray, np.ndarray, Tuple[int, int]]:
    """The square round the disc (clipped to the picture), black outside the disc: (crop, disc mask of the crop,
    (x0, y0) of the crop in the picture)."""
    h, w = picture.shape[:2]
    cx, cy, r = int(round(centre[0])), int(round(centre[1])), int(round(radius))
    x0, y0, x1, y1 = max(cx - r, 0), max(cy - r, 0), min(cx + r + 1, w), min(cy + r + 1, h)
    crop = picture[y0:y1, x0:x1].copy()
    disc = np.zeros(crop.shape[:2], np.uint8)
    cv2.circle(disc, (cx - x0, cy - y0), r, 1, -1)
    crop[disc == 0] = 0
    return crop, disc.astype(bool), (x0, y0)


def circleOfOutline(mask: np.ndarray) -> Optional[Tuple[float, float, float]]:
    """Least-squares circle (cx, cy, r) through the largest outer contour of a mask, or None."""
    contours, _ = cv2.findContours(mask.astype(np.uint8) * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return None
    pts = max(contours, key=cv2.contourArea).reshape(-1, 2).astype(np.float64)
    if len(pts) < 5:
        return None
    try:
        sol = np.linalg.lstsq(np.c_[2 * pts, np.ones(len(pts))], (pts ** 2).sum(1), rcond=None)[0]
    except np.linalg.LinAlgError:
        return None
    return float(sol[0]), float(sol[1]), float(np.sqrt(max(sol[2] + sol[0] ** 2 + sol[1] ** 2, 0.0)))


def yoloSamMasks(crop: np.ndarray, disc: np.ndarray, predictor, yolo) -> Tuple[list, Dict[str, Any]]:
    """MobileSAMv2 on the cut disc: the YOLO boxes centred on the disc as box prompts, batched through MobileSAM;
    predicted IoU / stability filters and box merging. -> (list of dict(segmentation, predicted_iou, stability_score),
    counts)."""
    import torch
    from torchvision.ops import batched_nms
    from mobile_sam.utils.amg import batched_mask_to_box, calculate_stability_score
    rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
    results = yolo.predict(rgb, conf=float(YOLO_CONFIDENCE), iou=0.7, verbose=False)
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
        counts.update(passed=0, merged=0)
        return [], counts
    predictor.set_image(rgb)
    model = predictor.model
    allMasks, allIou, allStab = [], [], []
    with torch.inference_mode():
        for i in range(0, len(boxes), BOX_BATCH):
            b = torch.as_tensor(boxes[i:i + BOX_BATCH], dtype=torch.float, device=predictor.device)
            tb = predictor.transform.apply_boxes_torch(b, rgb.shape[:2])
            logits, iou, _ = predictor.predict_torch(point_coords=None, point_labels=None, boxes=tb,
                                                     multimask_output=False, return_logits=True)
            logits, iou = logits[:, 0], iou[:, 0]
            allStab.append(calculate_stability_score(logits, model.mask_threshold, 1.0))
            allMasks.append(logits > model.mask_threshold)
            allIou.append(iou)
        masks, iou, stab = torch.cat(allMasks), torch.cat(allIou), torch.cat(allStab)
        keep = (iou >= float(IOU_MIN)) & (stab >= float(STABILITY_MIN)) & (masks.flatten(1).sum(1) > 0)
        counts["passed"] = int(keep.sum())
        masks, iou, stab = masks[keep], iou[keep], stab[keep]
        if len(masks):
            mboxes = batched_mask_to_box(masks).float()
            k = batched_nms(mboxes, iou, torch.zeros(len(masks), device=masks.device), float(MERGE_IOU))
            masks, iou, stab = masks[k], iou[k], stab[k]
    found = [dict(segmentation=m.cpu().numpy(), predicted_iou=float(q), stability_score=float(st))
             for m, q, st in zip(masks, iou, stab)]
    counts["merged"] = len(found)
    return found, counts


# ---- ellipse fit ------------------------------------------------------------------------------------------------------
def ellipseDistances(points: np.ndarray, e) -> np.ndarray:
    """Distance of each point from the ellipse e = (cx, cy, a, b, angle in degrees; a, b semi-axes): the closest
    point on the ellipse by Newton on its angle parameter (10 steps)."""
    cx, cy, a, b, ang = e
    t = np.deg2rad(ang)
    c, s_ = np.cos(t), np.sin(t)
    dx, dy = points[:, 0] - cx, points[:, 1] - cy
    u, v = c * dx + s_ * dy, -s_ * dx + c * dy
    a, b = max(abs(a), 1e-6), max(abs(b), 1e-6)
    th = np.arctan2(a * v, b * u)
    for _ in range(10):
        st, ct = np.sin(th), np.cos(th)
        f = (a * a - b * b) * st * ct - a * u * st + b * v * ct
        df = (a * a - b * b) * (ct * ct - st * st) - a * u * ct - b * v * st
        th = th - f / np.where(np.abs(df) < 1e-9, 1e-9, df)
    return np.hypot(u - a * np.cos(th), v - b * np.sin(th))


def _cvToEllipse(box):
    """cv2's ((cx, cy), (w, h), angle) -> (cx, cy, a, b, angle) with a >= b and the angle that of a, in 0 - 180."""
    (cx, cy), (w, h), ang = box
    a, b = w / 2.0, h / 2.0
    if b > a:
        a, b, ang = b, a, ang + 90.0
    return (float(cx), float(cy), float(a), float(b), float(ang % 180.0))


def leastSquaresEllipse(points: np.ndarray, start=None):
    """Stage 1: the ellipse minimising the sum of squared distances of the points from it (start: cv2.fitEllipseDirect,
    then scipy least_squares on the true distances)."""
    from scipy.optimize import least_squares
    if start is None:
        start = _cvToEllipse(cv2.fitEllipseDirect(points.astype(np.float32)))
    res = least_squares(lambda e: ellipseDistances(points, e), np.array(start, float), method="lm", max_nfev=200)
    cx, cy, a, b, ang = res.x
    if abs(b) > abs(a):                                       # keep a the larger semi-axis
        a, b, ang = b, a, ang + 90.0
    return (float(cx), float(cy), abs(float(a)), abs(float(b)), float(ang % 180.0))


def mostInliersEllipse(points: np.ndarray, start, tolerance: float, tries: int, seed: int = 0):
    """Stage 2: the ellipse with the most points within tolerance of it. Candidates: the stage-1 ellipse and RANSAC
    ellipses through 5 points (fixed seed); the winner is refitted by least squares to its inliers while that raises
    the count. -> (ellipse, inlier mask, rounds of refitting)."""
    rng = np.random.default_rng(seed)
    count = lambda e: ellipseDistances(points, e) <= tolerance
    best, bestIn = start, count(start)
    pts32 = points.astype(np.float32)
    for _ in range(int(tries)):
        try:
            e = _cvToEllipse(cv2.fitEllipse(pts32[rng.choice(len(points), 5, replace=False)]))
        except cv2.error:
            continue
        if not (np.isfinite(e).all() and 0 < e[2] < 1e4 and 0 < e[3] < 1e4):
            continue
        inl = count(e)
        if inl.sum() > bestIn.sum():
            best, bestIn = e, inl
    rounds = 0
    while bestIn.sum() >= 6 and rounds < 10:
        e = leastSquaresEllipse(points[bestIn], best)
        inl = count(e)
        if inl.sum() <= bestIn.sum():
            break
        best, bestIn, rounds = e, inl, rounds + 1
    return best, bestIn, rounds


def fitCapEllipse(mask: np.ndarray, tolerance: float, tries: int) -> Optional[Dict[str, Any]]:
    """Both stages on the outline of mask (its largest outer contour) -> dict(points, e1, rms1, in1, e2, rms2, in2,
    rounds, contour) or None."""
    contours, _ = cv2.findContours(mask.astype(np.uint8) * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    points = contour.reshape(-1, 2).astype(np.float64)
    if len(points) < 10:
        return None
    # fitted round a whole-pixel origin near the outline's middle (CHANGES 144): the same outline anywhere in a picture
    # gives the same ellipse - in absolute coordinates float32 rounding (cv2.fitEllipse) picked a different RANSAC winner
    # between candidates with equal counts in the cut-out disc and in the full picture (DOST_cap_08: 0.7 px)
    origin = np.floor(points.mean(0))
    points = points - origin
    e1 = leastSquaresEllipse(points)
    d1 = ellipseDistances(points, e1)
    e2, inl2, rounds = mostInliersEllipse(points, e1, tolerance, tries)
    d2 = ellipseDistances(points, e2)
    back = lambda e: (e[0] + float(origin[0]), e[1] + float(origin[1]), e[2], e[3], e[4])
    return dict(points=len(points), e1=back(e1), rms1=float(np.sqrt((d1 ** 2).mean())), in1=int((d1 <= tolerance).sum()),
                e2=back(e2), rms2=float(np.sqrt((d2 ** 2).mean())), in2=int(inl2.sum()), rounds=rounds, contour=contour)


def ellipseContour(e, step: int = 4) -> np.ndarray:
    """Points of the ellipse e as an OpenCV contour (for drawing)."""
    cx, cy, a, b, ang = e
    pts = cv2.ellipse2Poly((int(round(cx)), int(round(cy))), (max(int(round(a)), 1), max(int(round(b)), 1)),
                           int(round(ang)), 0, 360, step)
    return pts.reshape(-1, 1, 2).astype(np.int32)


# ---- the cap ----------------------------------------------------------------------------------------------------------
def findCap(anImage: np.ndarray, centre, predictor, yolo) -> Dict[str, Any]:
    """The cap's mask and ellipse in a cap picture (BGR), round centre (the cap centre). Returns dict(found, reason,
    counts, area, stability, iou, fit (fitCapEllipse's dict, stage-2 centre = e2[:2]), centre (stage-2 centre, full
    picture), seconds)."""
    t0 = time.perf_counter()
    picture = applyGamma(anImage, GAMMA)
    crop, disc, (x0, y0) = cutDisc(picture, centre, DISC_RADIUS)
    found, counts = yoloSamMasks(crop, disc, predictor, yolo)
    kept, outside, small, edge, large = [], 0, 0, 0, 0
    for d in found:
        seg = np.asarray(d["segmentation"]).astype(bool)
        inside = seg & disc
        n = int(inside.sum())
        if n * 2 < int(seg.sum()) or n == 0:
            outside += 1
            continue
        if n < AREA_MIN:
            small += 1
            continue
        circle = circleOfOutline(inside)
        if circle is not None and circle[2] > DISC_EDGE_FRACTION * DISC_RADIUS:
            edge += 1                                         # the cut-out disc's own edge, not an object on it
            continue
        if n > AREA_MAX:
            large += 1
            continue
        kept.append((d, inside, n))
    counts.update(outside=outside, small=small, edge=edge, large=large, kept=len(kept))
    result = dict(found=False, reason="", counts=counts, area=None, stability=None, iou=None, fit=None, centre=None)
    if not kept:
        result.update(reason="no cap mask (no mask left after the filters)", seconds=time.perf_counter() - t0)
        return result
    d, inside, n = max(kept, key=lambda t: t[2])
    full = np.zeros(anImage.shape[:2], bool)
    full[y0:y0 + inside.shape[0], x0:x0 + inside.shape[1]] = inside
    fit = fitCapEllipse(full, float(ELLIPSE_TOLERANCE), int(ELLIPSE_TRIES))
    result.update(area=n, stability=d["stability_score"], iou=d["predicted_iou"], fit=fit)
    if fit is None:
        result.update(reason="cap mask outline too short for an ellipse", seconds=time.perf_counter() - t0)
        return result
    result.update(found=True, centre=(fit["e2"][0], fit["e2"][1]), seconds=time.perf_counter() - t0)
    return result


def describe(cap: Dict[str, Any]) -> str:
    """One log line part with every value of findCap's result."""
    c = cap.get("counts", {})
    text = (f"YOLO boxes {c.get('boxes', 0)} ({c.get('onDisc', 0)} on the disc), {c.get('passed', 0)} passed, "
            f"{c.get('merged', 0)} after merging; dropped {c.get('outside', 0)} outside / {c.get('small', 0)} small / "
            f"{c.get('edge', 0)} disc edge / {c.get('large', 0)} large")
    fit = cap.get("fit")
    if cap.get("area") is not None:
        text += f"; cap mask area {cap['area']}, stability {cap['stability']:.3f}, predicted IoU {cap['iou']:.3f}"
    if fit is not None:
        e1, e2 = fit["e1"], fit["e2"]
        text += (f"; ellipse stage 1 ({e1[0]:.1f}, {e1[1]:.1f}) {e1[2]:.1f}/{e1[3]:.1f}, {fit['in1']}/{fit['points']} "
                 f"within {ELLIPSE_TOLERANCE} px; stage 2 ({e2[0]:.1f}, {e2[1]:.1f}) {e2[2]:.1f}/{e2[3]:.1f} "
                 f"(ratio {e2[3] / max(e2[2], 1e-6):.3f}), {fit['in2']}/{fit['points']} within, RMS {fit['rms2']:.2f}")
    if cap.get("seconds") is not None:
        text += f"; {cap['seconds']:.2f} s"
    return text
