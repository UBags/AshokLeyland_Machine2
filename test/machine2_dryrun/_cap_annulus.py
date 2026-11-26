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
MASK_METHODS = ("boxes", "points")

HEADER = (["file", "model", "mask_method", "cap_found", "right_cap", "rb_brass", "rb_silver", "brightness", "holes_found", "hub_x",
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


def capMaskByBoxes(img: np.ndarray, hx: float, hy: float):
    """The production cap mask's steps (CapMaskFinder.findCap) on a disc round H: (full-picture mask, info dict) or
    (None, info dict with reason). findCap returns no mask, so its steps are repeated here with its own functions and
    values; CapMaskFinder is not changed."""
    picture = CapMaskFinder.applyGamma(img, CapMaskFinder.GAMMA)
    crop, disc, (x0, y0) = CapMaskFinder.cutDisc(picture, (hx, hy), CapMaskFinder.DISC_RADIUS)
    found, counts = CapMaskFinder.yoloSamMasks(crop, disc, CheckCap._sam_predictor(), CheckCap._yolo_model())
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
    info = dict(counts=counts, score=None, reason="")
    if not kept:
        info["reason"] = "no cap mask (no mask left after the filters)"
        return None, info
    d, inside, n = max(kept, key=lambda t: t[2])
    full = np.zeros(img.shape[:2], bool)
    full[y0:y0 + inside.shape[0], x0:x0 + inside.shape[1]] = inside
    info["score"] = float(d["predicted_iou"])
    info["stability"] = float(d["stability_score"])
    return full, info


def describeCounts(c: dict) -> str:
    return (f"YOLO boxes {c.get('boxes', 0)} ({c.get('onDisc', 0)} on the disc), {c.get('passed', 0)} passed, "
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


def measure(img: np.ndarray, model: str, gamma: float = 1.0, floor: float = 1.0, maskMethod: str = "boxes") -> dict:
    """Everything for one picture: the csv row values (HEADER keys) and, for drawing / showing, hub (hx, hy, R),
    pts, mask, ellipse, ratiosE / lengthsE, ratiosM / lengthsM."""
    t0 = time.perf_counter()
    res = dict.fromkeys(HEADER, "")
    res.update(model=model, mask_method=maskMethod, maskMethod=maskMethod, maskInfo="", hub=None, pts=np.zeros((0, 2)),
               mask=None, ellipse=None, ratiosE=None, lengthsE=None, ratiosM=None, lengthsM=None)
    found, right, rbB, rbS, bright = steps12(img, model)
    res.update(cap_found=found or "none", right_cap=right, rb_brass=f"{rbB:.1f}", rb_silver=f"{rbS:.1f}",
               brightness=f"{bright:.1f}")
    notes = [] if right else [f"steps 1-2 NOT OK (cap found {found or 'none'})"]
    holes = FindHubHoles.findHoles(img, model, None, check="cap")
    res["holes_found"] = bool(holes.get("found"))
    if not holes.get("found"):
        notes.append(f"hub holes not found ({holes.get('reason')})")
    else:
        hx, hy, R = float(holes["cx"]), float(holes["cy"]), float(holes["radius"])
        res.update(hub=(hx, hy, R), hub_x=f"{hx:.1f}", hub_y=f"{hy:.1f}", hole_r=f"{R:.1f}")
        if maskMethod == "points":
            res["pts"] = promptPoints(hx, hy)
            mask, samScore = capMask(img, hx, hy, gamma)
            if mask is None:
                notes.append(samScore)
        else:
            mask, info = capMaskByBoxes(img, hx, hy)
            res["maskInfo"] = describeCounts(info["counts"]) + (
                f"; predicted IoU {info['score']:.3f}, stability {info['stability']:.3f}" if mask is not None else "")
            samScore = info["score"]
            if mask is None:
                notes.append(info["reason"])
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
    return res


def marked(img: np.ndarray, res: dict) -> np.ndarray:
    """Outlines only, no values: hole circle and hub centre (blue), prompt points (cyan, points method only), mask outline (green),
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
    for x, y in res["pts"]:
        cv2.circle(out, (int(round(x)), int(round(y))), 2, (255, 255, 0), -1)
    if res.get("mask") is not None:
        cs, _ = cv2.findContours(res["mask"].astype(np.uint8) * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        cv2.drawContours(out, cs, -1, (0, 255, 0), 1)
    if res.get("ellipse") is not None:
        cv2.drawContours(out, [CapMaskFinder.ellipseContour(res["ellipse"])], -1, (0, 255, 255), 2)
    return out


def detailLines(name: str, res: dict) -> list:
    """The text lines of one picture (values go here, never on the picture)."""
    lines = [f"{name}  {res['model']}  {res['seconds']} s",
             f"  steps 1-2: cap found {res['cap_found']}, right cap {res['right_cap']}, R-B brass {res['rb_brass']} / "
             f"silver {res['rb_silver']}, brightness r25 {res['brightness']}"]
    if res.get("hub") is not None:
        lines.append(f"  hub centre ({res['hub_x']}, {res['hub_y']}), hole circle r {res['hole_r']} ({holeFinder()})")
    if res.get("mask") is not None:
        lines.append(f"  cap mask ({res['maskMethod']}) area {res['mask_area']}, SAM score {res['sam_score']}")
    if res.get("maskInfo"):
        lines.append(f"  {res['maskInfo']}")
    if res.get("ellipse") is not None:
        lines.append(f"  ellipse centre ({res['ell_cx']}, {res['ell_cy']}), axes {res['ell_a']} / {res['ell_b']}, "
                     f"ellipse - hub {res['ell_minus_hub_dx']} / {res['ell_minus_hub_dy']} px")
    for label, ratios, lengths, total in (("ellipse", res.get("ratiosE"), res.get("lengthsE"), res["score_ellipse"]),
                                          ("mask", res.get("ratiosM"), res.get("lengthsM"), res["score_mask"])):
        if ratios is None:
            continue
        lines.append(f"  score ({label}) {total} - per diameter (deg): L+ / L- (px) = longer / shorter")
        lines.append("    " + "  ".join(f"{d:3d}: {a:4.1f}/{b:4.1f}={r:.2f}" for d, (a, b), r
                                        in list(zip(DIAMETER_ANGLES, lengths, ratios))[:6]))
        lines.append("    " + "  ".join(f"{d:3d}: {a:4.1f}/{b:4.1f}={r:.2f}" for d, (a, b), r
                                        in list(zip(DIAMETER_ANGLES, lengths, ratios))[6:]))
    if res["note"]:
        lines.append(f"  note: {res['note']}")
    return lines


def summaryLines(rows: list) -> list:
    lines = ["Summary (pictures with a score):"]
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
