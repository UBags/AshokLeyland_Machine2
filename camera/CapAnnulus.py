# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
CapAnnulus - seating of the silver (DOST) cap by the annulus between the cap and the hub's holes (CHANGES 212;
Uddi's method of 2026-11-27, studied in test/machine*_dryrun/Study_Cap_Annulus_UI.py, CHANGES 203 - 211).

In the cap picture itself:
  1. hub centre H and radius R of the circle through the hub's 4 holes (FindHubHoles.findHoles, by CheckCap);
  2. a MobileSAM mask of ONLY the annulus - positive prompts every PROMPT_STEP_DEG on a circle PROMPT_INSET px inside
     the hole circle, none within HOLE_CLEARANCE px of a hole centre, and negative prompts on the cap (H, and
     NEGATIVE_RADII round it every 45 deg); the picture brightened by MASK_GAMMA first. Annulus = that mask inside the
     hole circle; the region it encloses (inside the circle, not in the mask, connected to H) is the cap;
  3. the inner ellipse: the annulus pixels touching the enclosed region are the inner edge points; the ellipse with the
     MOST of them within INNER_TOLERANCE px (consensus, not least squares): candidates the least-squares ellipse of all
     and INNER_TRIES ellipses through 5 random points (fixed seed), then a local search on centre / axes / angle;
  4. values: ellipse centre - H ("centre offset"); the sum over 12 diameters through H (15 - 180 deg) of the longer /
     shorter length between the inner ellipse and the hole circle ("score", 12 = centred); the ring's width every
     WIDTH_STEP_DEG along rays from H (walking in from the full hole circle, gaps within HOLE_ZONE px of it - the holes -
     passed over, then the first pixel off the mask) -> max, 90th percentile, sd, largest |width(t) - width(t + 180)|
     ("w_opp_max"); "islands" = mask pixels more than INNER_TOLERANCE + 1 px inside the inner ellipse; share of the inner
     edge points on the ellipse ("inlier_share"). The outer edge is always the full hole circle, never the mask's own.
  5. verdict (Uddi's rule, 2026-11-27, islands limit 450 since 12:18):
       NOT OK if offset > 7.0 or score > 16.0 or islands > 450 or w_opp_max > 17 or width p90 > 50 or width_max > 52;
       else OK if inlier_share > 0.70 or width_sd < 3.5 or islands < 200 or w_opp_max < 11 or p90 < 47 or
               width_max < 48.25;
       else NOT OK.
     On Uddi's 116 pictures (13, 17 and 26 Nov; 71 OK, 45 NOT OK of which 2 without holes found): 0 errors.

ALTERNATIVE (recorded, not used - Uddi 2026-11-27): an AND rule that separated the same 116 pictures with 0 errors,
every limit midway between the worst OK and the closest NOT OK value:
       OK if offset < 6.5 and score < 15.0 and w_opp_max < 13.8 and width p90 < 47.7 and width_max < 49.6
       (optionally also islands < 1000 and width_min > 25), else NOT OK.
   Worst OK / closest NOT OK: offset 5.32 / 8.46, score 14.27 / 15.50, w_opp_max 13.5 / 14.0, p90 46.9 / 48.4,
   width_max 48.8 / 50.5; every NOT OK picture fails at least 2 of the 5. Its advantage: an OK cap must pass every
   limit, where the rule above passes a picture that clears stage 1 on any one good-looking value.
"""

import math
from typing import Dict, Optional

import cv2
import numpy as np

from camera import CapMaskFinder
from camera.VisionConfig import vInt, vFloat, vTuple

PROMPT_INSET = vInt("vision.cap.annulus.prompt.inset", 5)  # px: positive prompts on a circle this far inside the hole circle ...
PROMPT_STEP_DEG = vInt("vision.cap.annulus.prompt.step.deg", 5)  # ... one every this many degrees ...
HOLE_CLEARANCE = vInt("vision.cap.annulus.hole.clearance", 28)  # ... none within this distance of a hole centre (holes r ~15 - 20)
NEGATIVE_RADII = vTuple("vision.cap.annulus.negative.radii", (0, 30, 60))  # negative prompts on the cap: H, and these radii x every 45 deg
MASK_GAMMA = vFloat("vision.cap.annulus.mask.gamma", 2.0)  # the picture brightened by this gamma for the annulus mask
INNER_TOLERANCE = vFloat("vision.cap.annulus.inner.tolerance", 2.0)  # px: an inner edge point this close to the inner ellipse is on it
INNER_TRIES = vInt("vision.cap.annulus.inner.tries", 1500)  # RANSAC ellipses through 5 inner edge points (fixed seed)
WIDTH_STEP_DEG = vInt("vision.cap.annulus.width.step.deg", 10)  # ring width measured every this many degrees (36 rays)
HOLE_ZONE = vFloat("vision.cap.annulus.hole.zone", 45.0)  # px inside the hole circle where gaps in the ring (the holes) are passed over
SCORE_FLOOR = vFloat("vision.cap.annulus.score.floor", 1.0)  # px: the shorter length of a diameter is at least this in its ratio
# Values: <vendor>.vision.cap.annulus.* (CHANGES 212). Verdict (Uddi, 2026-11-27): stage 1 - any -> NOT OK
NOT_OK_OFFSET = vFloat("vision.cap.annulus.notok.offset", 7.0)
NOT_OK_SCORE = vFloat("vision.cap.annulus.notok.score", 16.0)
NOT_OK_ISLANDS = vInt("vision.cap.annulus.notok.islands", 450)
NOT_OK_OPPOSITE = vFloat("vision.cap.annulus.notok.opposite", 17.0)
NOT_OK_WIDTH_P90 = vFloat("vision.cap.annulus.notok.width.p90", 50.0)
NOT_OK_WIDTH_MAX = vFloat("vision.cap.annulus.notok.width.max", 52.0)
# stage 2 - any -> OK; else NOT OK
OK_INLIER_SHARE = vFloat("vision.cap.annulus.ok.inlier.share", 0.70)
OK_WIDTH_SD = vFloat("vision.cap.annulus.ok.width.sd", 3.5)
OK_ISLANDS = vInt("vision.cap.annulus.ok.islands", 200)
OK_OPPOSITE = vFloat("vision.cap.annulus.ok.opposite", 11.0)
OK_WIDTH_P90 = vFloat("vision.cap.annulus.ok.width.p90", 47.0)
OK_WIDTH_MAX = vFloat("vision.cap.annulus.ok.width.max", 48.25)

DIAMETER_ANGLES = tuple(range(15, 181, 15))            # 15, 30, ..., 180: 12 diameters


def prompts(hx: float, hy: float, R: float, holePoints) -> np.ndarray:
    """Positive prompts: every PROMPT_STEP_DEG on the circle PROMPT_INSET px inside the hole circle, none within
    HOLE_CLEARANCE px of a hole centre."""
    holes = np.asarray(holePoints, dtype=np.float64).reshape(-1, 2) if holePoints is not None else np.zeros((0, 2))
    r = R - PROMPT_INSET
    pts = []
    for a in range(0, 360, int(PROMPT_STEP_DEG)):
        x, y = hx + r * math.cos(math.radians(a)), hy + r * math.sin(math.radians(a))
        if len(holes) and np.hypot(holes[:, 0] - x, holes[:, 1] - y).min() < HOLE_CLEARANCE:
            continue
        pts.append((x, y))
    return np.array(pts, dtype=np.float32).reshape(-1, 2)


def negativePrompts(hx: float, hy: float) -> np.ndarray:
    """Negative prompts on the cap: H, and NEGATIVE_RADII round it every 45 deg."""
    return np.array([[hx + r * math.cos(math.radians(a)), hy + r * math.sin(math.radians(a))]
                     for r in NEGATIVE_RADII for a in (range(0, 360, 45) if r else (0,))], dtype=np.float32)


def annulusMask(img: np.ndarray, hx: float, hy: float, R: float, holePoints, predictor,
                gamma: Optional[float] = None):
    """The annulus mask and the region it encloses: (annulus, cap, MobileSAM score, positive prompts, negative prompts);
    annulus / cap None (score = the reason) when MobileSAM's mask covers H or is empty. gamma: None = MASK_GAMMA."""
    import torch
    pos, neg = prompts(hx, hy, R, holePoints), negativePrompts(hx, hy)
    if len(pos) < 3:
        return None, None, "too few annulus prompts", pos, neg
    rgb = CapMaskFinder.applyGamma(cv2.cvtColor(img, cv2.COLOR_BGR2RGB), MASK_GAMMA if gamma is None else gamma)
    predictor.set_image(rgb)
    coords = np.vstack([pos, neg]).astype(np.float32)
    labels = np.r_[np.ones(len(pos)), np.zeros(len(neg))]
    with torch.inference_mode():
        masks, scores, _ = predictor.predict(point_coords=coords, point_labels=labels, multimask_output=False)
    h, w = img.shape[:2]
    yy, xx = np.ogrid[:h, :w]
    circle = (xx - hx) ** 2 + (yy - hy) ** 2 <= R * R
    ann = np.asarray(masks[0]).astype(bool) & circle
    n, lab = cv2.connectedComponents((~ann & circle).astype(np.uint8))
    ix, iy = int(round(hx)), int(round(hy))
    if not ann.any() or n < 2 or not (0 <= iy < h and 0 <= ix < w) or lab[iy, ix] == 0:
        return None, None, "annulus mask covers the hub centre or is empty", pos, neg
    return ann, lab == lab[iy, ix], float(scores[0]), pos, neg


def innerEdgePoints(ann: np.ndarray, cap: np.ndarray) -> np.ndarray:
    """The annulus pixels that touch the region it encloses (pieces of the mask inside the cap give points too - they
    are outliers for the fit). -> N x 2 (x, y)."""
    grown = cv2.dilate(cap.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    ys, xs = np.nonzero(ann & grown)
    return np.c_[xs, ys].astype(np.float64)


def maxInliersEllipse(points: np.ndarray, tolerance: Optional[float] = None, tries: Optional[int] = None,
                      seed: int = 0) -> Optional[Dict]:
    """The ellipse with the MOST points within tolerance px (consensus, not least squares): candidates the
    least-squares ellipse of all points and `tries` ellipses through 5 random points (fixed seed); then a local search
    on (cx, cy, a, b, angle) - steps 1 px / 2 deg halved down to 0.05 px / 0.1 deg - taking any move that puts more
    points on it (equal count: the smaller mean distance of those points).
    -> dict(e=(cx, cy, a, b, angle deg), inliers, points, share, meanDist) or None."""
    tolerance = INNER_TOLERANCE if tolerance is None else tolerance
    tries = INNER_TRIES if tries is None else tries
    if len(points) < 10:
        return None

    def value(e):
        d = CapMaskFinder.ellipseDistances(points, e)
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


def ringWidthAt(hx: float, hy: float, R: float, ann: np.ndarray, phi: float) -> float:
    """The ring's width along the ray from H at phi (rad): walking in from the hole circle, gaps in the mask within
    HOLE_ZONE px of the circle (a hole, the ring's thin edge round it) are passed over; after that the first pixel off
    the mask ends the ring. Width = R minus the distance of the last mask pixel."""
    h, w = ann.shape
    c, s_ = math.cos(phi), math.sin(phi)
    t, last = R - 0.5, None
    while t > 0:
        x, y = int(round(hx + t * c)), int(round(hy + t * s_))
        if 0 <= x < w and 0 <= y < h and ann[y, x]:
            last = t
        elif R - t > HOLE_ZONE:
            break
        t -= 0.25
    return R - last if last is not None else 0.0


def rayToEllipse(hx: float, hy: float, phi: float, e) -> Optional[float]:
    """Distance from H along phi (rad) to the ellipse e = (cx, cy, a, b, angle deg); None if no crossing."""
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


def diameterScore(hx: float, hy: float, R: float, e):
    """Sum over the 12 diameters of longer / shorter length between the ellipse e and the hole circle (shorter floored
    at SCORE_FLOOR px): (score, ratios, (L+, L-) per diameter)."""
    ratios, lengths = [], []
    for deg in DIAMETER_ANGLES:
        sides = []
        for phi in (math.radians(deg), math.radians(deg + 180)):
            d = rayToEllipse(hx, hy, phi, e)
            sides.append(0.0 if d is None else max(R - d, 0.0))
        ratios.append(max(sides) / max(min(sides), SCORE_FLOOR))
        lengths.append(tuple(sides))
    return sum(ratios), ratios, lengths


def measure(img: np.ndarray, hx: float, hy: float, R: float, holePoints, predictor,
            gamma: Optional[float] = None) -> Dict:
    """Every value of the method for one cap picture: dict(found, reason, annulus, cap, positive, negative, samScore,
    annArea, innerPoints, inliers, inlierShare, inlierMeanDist, ellipse, offset, dx, dy, axisRatio, score, ratios,
    widths, widthMin, widthMax, widthMean, widthSd, widthP90, oppositeMax, oppositeMean, islands)."""
    out = dict(found=False, reason="", annulus=None, cap=None, ellipse=None)
    ann, cap, samScore, pos, neg = annulusMask(img, hx, hy, R, holePoints, predictor, gamma)
    out.update(positive=pos, negative=neg)
    if ann is None:
        out["reason"] = samScore
        return out
    inner = innerEdgePoints(ann, cap)
    fit = maxInliersEllipse(inner)
    widths = [ringWidthAt(hx, hy, R, ann, math.radians(d)) for d in range(0, 360, int(WIDTH_STEP_DEG))]
    half = len(widths) // 2
    opposite = [abs(widths[i] - widths[i + half]) for i in range(half)]
    out.update(annulus=ann, cap=cap, samScore=samScore, annArea=int(ann.sum()), innerPoints=len(inner),
               widths=widths, widthMin=float(min(widths)), widthMax=float(max(widths)),
               widthMean=float(np.mean(widths)), widthSd=float(np.std(widths)),
               widthP90=float(np.percentile(widths, 90)), oppositeMax=float(max(opposite)),
               oppositeMean=float(np.mean(opposite)))
    if fit is None:
        out["reason"] = "no inner ellipse (too few inner edge points)"
        return out
    e = fit["e"]
    h, w = ann.shape
    yy, xx = np.mgrid[:h, :w]
    c, s_ = math.cos(math.radians(e[4])), math.sin(math.radians(e[4]))
    u, v = c * (xx - e[0]) + s_ * (yy - e[1]), -s_ * (xx - e[0]) + c * (yy - e[1])
    m = INNER_TOLERANCE + 1.0                                 # pixels on the edge (within tolerance) are not islands
    insideEllipse = (u / max(e[2] - m, 1)) ** 2 + (v / max(e[3] - m, 1)) ** 2 < 1.0
    score, ratios, _ = diameterScore(hx, hy, R, e)
    out.update(found=True, ellipse=e, inliers=fit["inliers"], inlierShare=fit["share"], inlierMeanDist=fit["meanDist"],
               dx=e[0] - hx, dy=e[1] - hy, offset=math.hypot(e[0] - hx, e[1] - hy), axisRatio=e[3] / e[2],
               score=score, ratios=ratios, islands=int((ann & insideEllipse).sum()))
    return out


def verdict(values: Dict):
    """Uddi's rule on measure()'s values: (seated, "stage n: conditions met with their values")."""
    if not values.get("found"):
        return False, values.get("reason") or "no annulus values"
    v = values
    bad = [f"{name} {val:.4g} > {lim:g}" for name, val, lim in (
        ("centre offset", v["offset"], NOT_OK_OFFSET), ("score", v["score"], NOT_OK_SCORE),
        ("islands", v["islands"], NOT_OK_ISLANDS), ("w_opp_max", v["oppositeMax"], NOT_OK_OPPOSITE),
        ("width_p90", v["widthP90"], NOT_OK_WIDTH_P90), ("width_max", v["widthMax"], NOT_OK_WIDTH_MAX)) if val > lim]
    if bad:
        return False, "stage 1 (any -> NOT OK): " + "; ".join(bad)
    good = [f"{name} {val:.4g} {op} {lim:g}" for name, val, op, lim in (
        ("inlier_share", v["inlierShare"], ">", OK_INLIER_SHARE), ("width_sd", v["widthSd"], "<", OK_WIDTH_SD),
        ("islands", v["islands"], "<", OK_ISLANDS), ("w_opp_max", v["oppositeMax"], "<", OK_OPPOSITE),
        ("width_p90", v["widthP90"], "<", OK_WIDTH_P90), ("width_max", v["widthMax"], "<", OK_WIDTH_MAX))
        if (val > lim if op == ">" else val < lim)]
    if good:
        return True, "stage 2 (any -> OK): " + "; ".join(good)
    return False, "stage 3: no stage-1 and no stage-2 condition met"


def describe(values: Dict) -> str:
    """The values for the log line (the picture for the frontend carries no text)."""
    if values.get("annulus") is None:
        return values.get("reason", "")
    text = (f"annulus {values['annArea']} px from {len(values['positive'])} prompts, SAM score {values['samScore']:.3f}; "
            f"ring width min / max / mean / sd / p90 {values['widthMin']:.1f} / {values['widthMax']:.1f} / "
            f"{values['widthMean']:.1f} / {values['widthSd']:.2f} / {values['widthP90']:.1f}, opposite sides max "
            f"{values['oppositeMax']:.1f}")
    if values.get("found"):
        e = values["ellipse"]
        text += (f"; inner ellipse ({e[0]:.1f}, {e[1]:.1f}) {e[2]:.1f} / {e[3]:.1f}, {values['inliers']} of "
                 f"{values['innerPoints']} edge points within {INNER_TOLERANCE:g} px ({values['inlierShare']:.3f}); "
                 f"centre offset {values['offset']:.2f} ({values['dx']:+.1f} / {values['dy']:+.1f}), score "
                 f"{values['score']:.3f}, islands {values['islands']}")
    return text
