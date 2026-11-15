# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
KnuckleLocator - finds the knuckle on the knuckle picture (state 3, Machine 2) with MobileSAM, and from it the type
(DOST / DOSTPLUS), the side (LHS / RHS), the spindle and the frame for the later checks (camera/VisionFrame.py).

Production version of the study tools test/machine2_dryrun/Study_Knuckle_Segmentation.py ("Prompted (fast)", boss
points "grid") and Study_Boss_Grid_Masks.py; same rules and numbers (CHANGES 101, 102). One image embedding, then:

  1. SPINDLE   one point prompt at its usual place; the mask kept if round (fill >= 0.75), radius 35-65 and within
               80 px. Usable as the anchor only with radius 46-52 (a bearing on the spindle makes it about 54).
  2. BOSSES    per boss a 4 x 4 grid (36.8 px spacing) round its usual place relative to the spindle (the usual place in
               the picture when there is no spindle mask), its 24 neighbouring pairs as 2-point prompts, 3 masks each.
               A boss: radius 30-60, circularity >= 0.75, fill >= 0.70, bright ring (>= 80) round a dark core (bore +
               pin, radius 6-22, at most 8 px off the mask centre, >= 40 grey darker). Same masks (IoU >= 0.85)
               grouped; best = votes x circularity x fill x contrast x concentricity. The PIN is the dark core's
               centre (steadier than the mask's centre). Neither boss found with the spindle-relative grid: the grid
               at the usual picture place is tried too.
  3. LOWER ARM 4 points down each possible lower arm (ARM_L for an LHS knuckle, ARM_R for an RHS one), relative to
               the spindle; the arm is the dark (< 75), long (reaching 220 px below the spindle) mask that starts 20-150
               px below the spindle, area 8,000-70,000, rect fill < 0.75. Exactly one: the side.
  4. TYPE      boss area x 1000 / pin distance^2 (limit 34.5: DOST 29-31, DOSTPLUS 37-41), cross-checked by boss radius
               / pin distance (limit 0.1095): NOT OK if the radius ratio clearly says the other type (beyond 0.1095
               +- 0.0025; inside that band it does not decide).
  One boss found and the other not: the other's grid is placed from the found pin by the pin vector (397, 4.8).
  No mask of a boss passing: its masks are opened with a 21 px disc (removes a strip SAM took in next to the boss) and
  judged again.

Measured on the 19 knuckle pictures of 13 Nov (+ empty fixture): type 19/19, side 19/19, pins within +-0.4 px per
type / side; 0.30-0.40 s per picture on the laptop GPU.
"""

import math
import time
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from camera import VisionFrame


# --------------------------------------------------------------------------------------------------- reference values
# Per machine: <vendor>.vision.knuckle.locator.* in ApplicationConfiguration.properties (2026-11-15; before, in this
# file). Machine 2 values from the 13 Nov 2026 pictures; Machine 1 (regions method, does not use them) has the Machine 2
# values until they are measured on Machine 1 pictures. Read where they are used, so a live reload applies.
SPINDLE_EXPECTED = (642, 360)      # point prompt for the spindle
SPINDLE_MAX_SHIFT = 80.0
SPINDLE_R = (35.0, 65.0)
# usable as the anchor: 48.1-49.9 measured; 53.9 with a bearing on the spindle
SPINDLE_R_OK = (46.0, 52.0)
SPINDLE_FILL_MIN = 0.75

# Each boss's usual place from the spindle, and the 4 x 4 prompt grid round it
BOSS_L_FROM_SPINDLE = (-198.0, -161.0)
BOSS_R_FROM_SPINDLE = (200.0, -161.0)
SPACING = 36.76955262170047    # 2 x 26 / sqrt(2) px
GRID_N = 4
DEDUPE_IOU = 0.85
# A boss: radius, circularity, fill, bright ring round a dark core (bore + pin), concentricity
BOSS_RADIUS = (30.0, 60.0)
BOSS_CIRCULARITY_MIN = 0.75
BOSS_FILL_MIN = 0.70
BOSS_RING_MIN = 80.0
BOSS_CONTRAST_MIN = 40.0
BOSS_CORE_RADIUS = (6.0, 22.0)
BOSS_CONCENTRICITY_MAX = 8.0

# Lower arm: 4 prompt points down each possible arm (from the spindle), and what the arm's mask must be
ARM_L_POINTS = [(-155, 115), (-165, 182), (-192, 248), (-220, 310)]
ARM_R_POINTS = [(150, 123), (162, 189), (186, 254), (210, 315)]
LOWER_GREY_MAX = 75.0
LOWER_AREA = (8000, 70000)
LOWER_RECT_FILL_MAX = 0.75
LOWER_REACH = 220                    # px below the spindle
LOWER_TOP_DY = (20, 150)           # top of the arm below the spindle

TYPE_AREA_RATIO = 34.5        # decides: DOST 29.0-31.2, DOSTPLUS 36.8-41.1
# cross-check: DOST 0.102-0.105, DOSTPLUS 0.114-0.124 (enclosing circle: one bump of the mask moves it); it vetoes only
# when beyond the limit +- the band
TYPE_RADIUS_RATIO = 0.1095
TYPE_RADIUS_BAND = 0.0025
# The right pin from the left one (and back) - for a boss not found where expected while the other was:
# (397, 4.8) = mean pin vector of the four types / sides (distance 394-400 px, angle 0.65-0.77 deg)
PIN_VECTOR = (397.0, 4.8)
OPEN_KERNEL_SIZE = 21              # opening of masks when no boss passed


def bossFromSpindle() -> Dict[str, Tuple[float, float]]:
    return {"BOSS_L": tuple(BOSS_L_FROM_SPINDLE), "BOSS_R": tuple(BOSS_R_FROM_SPINDLE)}


def bossLimits() -> Dict[str, Any]:
    return dict(radius=tuple(BOSS_RADIUS), circularity_min=BOSS_CIRCULARITY_MIN, fill_min=BOSS_FILL_MIN,
                ring_min=BOSS_RING_MIN, contrast_min=BOSS_CONTRAST_MIN, core_radius=tuple(BOSS_CORE_RADIUS),
                concentricity_max=BOSS_CONCENTRICITY_MAX)


def areaGate() -> Tuple[float, float]:
    """Mask areas that can pass the boss rule (radius and fill): only those are measured."""
    return (BOSS_FILL_MIN * math.pi * BOSS_RADIUS[0] ** 2, math.pi * BOSS_RADIUS[1] ** 2)


def armPoints() -> Dict[str, List[Tuple[float, float]]]:
    return {"ARM_L": [tuple(p) for p in ARM_L_POINTS], "ARM_R": [tuple(p) for p in ARM_R_POINTS]}


def lowerArmRule() -> Dict[str, Any]:
    return dict(grey_max=LOWER_GREY_MAX, area=tuple(LOWER_AREA), rect_fill_max=LOWER_RECT_FILL_MAX, reach=LOWER_REACH,
                top_dy=tuple(LOWER_TOP_DY))


def openKernel():
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (int(OPEN_KERNEL_SIZE), int(OPEN_KERNEL_SIZE)))


# As read at import (kept for tools that look at them); the code uses the functions above.
BOSS_FROM_SPINDLE = bossFromSpindle()
BOSS_LIMITS = bossLimits()
AREA_GATE = areaGate()
ARM_POINTS = armPoints()
LOWER = lowerArmRule()


# ------------------------------------------------------------------------------------------------------- mask measures
def fill_holes(mask: np.ndarray) -> np.ndarray:
    m = np.ascontiguousarray(mask, dtype=np.uint8) * 255
    cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    out = np.zeros(m.shape, np.uint8)
    cv2.drawContours(out, cs, -1, 255, -1)
    return out > 0


def describe(mask: np.ndarray, grey: np.ndarray) -> Dict[str, Any]:
    """Area, centroid, enclosing circle, fill, circularity, box, rect fill, grey, lowest point of one mask."""
    mask = np.ascontiguousarray(mask, dtype=bool)
    ys, xs = np.nonzero(mask)
    area = int(len(xs))
    cs, _ = cv2.findContours(mask.astype(np.uint8) * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    c = max(cs, key=cv2.contourArea)
    (ccx, ccy), cr = cv2.minEnclosingCircle(c)
    (_, _), (rw, rh), _ = cv2.minAreaRect(c)
    x, y, w, h = cv2.boundingRect(c)
    lowest = c[c[:, 0, 1].argmax(), 0]
    perimeter = float(cv2.arcLength(c, True))
    return dict(mask=mask, area=area, cx=float(xs.mean()), cy=float(ys.mean()), perimeter=perimeter,
                circularity=4.0 * math.pi * area / (perimeter * perimeter) if perimeter > 0 else 0.0,
                circle_x=float(ccx), circle_y=float(ccy), radius=float(cr),
                round_fill=area / (math.pi * cr * cr) if cr > 0 else 0.0,
                rect_fill=area / (rw * rh) if rw * rh > 0 else 0.0,
                bbox=(int(x), int(y), int(w), int(h)), grey=float(grey[mask].mean()),
                lowest=(int(lowest[0]), int(lowest[1])))


def dark_core(mask: np.ndarray, grey: np.ndarray, cx: float, cy: float) -> Dict[str, float]:
    """The dark blob (bore + pin) inside the mask's outline nearest its centre, and the ring round it."""
    filled = fill_holes(mask)
    ring_vals = grey[mask]
    ring0 = float(np.median(ring_vals)) if ring_vals.size else 0.0
    thr = min(70.0, ring0 - 40.0)
    out = dict(core_x=float("nan"), core_y=float("nan"), core_radius=0.0, core_grey=float("nan"),
               ring_grey=float(ring_vals.mean()) if ring_vals.size else 0.0, contrast=0.0, concentricity=float("nan"))
    if thr <= 0:
        return out
    n, lab, stats, cents = cv2.connectedComponentsWithStats((filled & (grey < thr)).astype(np.uint8), connectivity=8)
    best, best_d = None, 1e9
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < 20:
            continue
        d = math.hypot(cents[i][0] - cx, cents[i][1] - cy)
        if d < best_d:
            best, best_d = i, d
    if best is None:
        return out
    core = lab == best
    ring = mask & ~core
    out.update(core_x=float(cents[best][0]), core_y=float(cents[best][1]), core_radius=math.sqrt(core.sum() / math.pi),
               core_grey=float(grey[core].mean()), concentricity=best_d)
    if ring.any():
        out["ring_grey"] = float(grey[ring].mean())
    out["contrast"] = out["ring_grey"] - out["core_grey"]
    return out


def is_boss(m: Dict[str, Any], limits: Optional[Dict] = None) -> bool:
    limits = bossLimits() if limits is None else limits
    return (limits["radius"][0] <= m["radius"] <= limits["radius"][1] and m["circularity"] >= limits["circularity_min"]
            and m["round_fill"] >= limits["fill_min"] and m["ring_grey"] >= limits["ring_min"]
            and m["contrast"] >= limits["contrast_min"]
            and limits["core_radius"][0] <= m["core_radius"] <= limits["core_radius"][1]
            and m["concentricity"] <= limits["concentricity_max"])


def _bbox(mask: np.ndarray, pad: int = 2) -> Tuple[int, int, int, int]:
    ys = np.flatnonzero(mask.any(axis=1))
    xs = np.flatnonzero(mask.any(axis=0))
    return (max(int(xs[0]) - pad, 0), max(int(ys[0]) - pad, 0),
            min(int(xs[-1]) + pad + 1, mask.shape[1]), min(int(ys[-1]) + pad + 1, mask.shape[0]))


def measure_boss(mask: np.ndarray, grey: np.ndarray) -> Dict[str, Any]:
    """describe + dark_core on the mask's own box (same values as on the whole picture, much faster)."""
    x0, y0, x1, y1 = _bbox(mask)
    d = describe(mask[y0:y1, x0:x1], grey[y0:y1, x0:x1])
    d.update(dark_core(d["mask"], grey[y0:y1, x0:x1], d["cx"], d["cy"]))
    for k in ("cx", "circle_x", "core_x"):
        d[k] += x0
    for k in ("cy", "circle_y", "core_y"):
        d[k] += y0
    d["bbox"] = (d["bbox"][0] + x0, d["bbox"][1] + y0, d["bbox"][2], d["bbox"][3])
    d["box"] = (x0, y0, x1, y1)
    return d


def _iou(a: Dict, b: Dict) -> float:
    ax0, ay0, ax1, ay1 = a["box"]
    bx0, by0, bx1, by1 = b["box"]
    if ax1 <= bx0 or bx1 <= ax0 or ay1 <= by0 or by1 <= ay0:
        return 0.0
    x0, y0, x1, y1 = min(ax0, bx0), min(ay0, by0), max(ax1, bx1), max(ay1, by1)
    ma = np.zeros((y1 - y0, x1 - x0), bool)
    mb = np.zeros_like(ma)
    ma[ay0 - y0:ay1 - y0, ax0 - x0:ax1 - x0] = a["mask"]
    mb[by0 - y0:by1 - y0, bx0 - x0:bx1 - x0] = b["mask"]
    return float(np.logical_and(ma, mb).sum()) / float(np.logical_or(ma, mb).sum() or 1)


def grid_points(cx: float, cy: float) -> Dict[Tuple[int, int], Tuple[float, float]]:
    offs = [(i - (GRID_N - 1) / 2.0) * SPACING for i in range(GRID_N)]
    return {(r, c): (cx + offs[c], cy + offs[r]) for r in range(GRID_N) for c in range(GRID_N)}


def grid_pairs() -> List[Tuple[Tuple[int, int], Tuple[int, int]]]:
    pairs = [((r, c), (r, c + 1)) for r in range(GRID_N) for c in range(GRID_N - 1)]
    return pairs + [((r, c), (r + 1, c)) for r in range(GRID_N - 1) for c in range(GRID_N)]


# ------------------------------------------------------------------------------------------------------------ locator
class KnuckleLocator:
    """locate(picture) -> result dict. Uses the shared MobileSAM predictor (camera/ModelManager.py) unless one is
    given (the dry runs)."""

    def __init__(self, predictor=None, device=None):
        self._predictor = predictor
        self._device = device

    def _model(self):
        if self._predictor is None:
            from camera.ModelManager import ModelManager
            manager = ModelManager.get_instance()
            self._predictor = manager.get_sam_predictor()
            self._device = manager.get_device()
        if self._device is None:
            self._device = self._predictor.model.device
        return self._predictor, self._device

    # -------------------------------------------------------------------------------------------------- parts
    def _spindle(self, predictor, grey) -> Optional[Dict]:
        ex, ey = SPINDLE_EXPECTED
        ms, _, _ = predictor.predict(point_coords=np.array([[ex, ey]]), point_labels=np.array([1]),
                                     multimask_output=True)
        cands = [describe(m, grey) for m in ms if m.any()]
        cands = [c for c in cands if SPINDLE_R[0] <= c["radius"] <= SPINDLE_R[1] and c["round_fill"] >= SPINDLE_FILL_MIN
                 and math.hypot(c["circle_x"] - ex, c["circle_y"] - ey) <= SPINDLE_MAX_SHIFT]
        return min(cands, key=lambda c: math.hypot(c["circle_x"] - ex, c["circle_y"] - ey)) if cands else None

    def _bosses(self, predictor, device, shape, grey, centres: Dict[str, Tuple[float, float]]) -> Dict[str, Optional[Dict]]:
        import torch
        pairs = grid_pairs()
        found = {}
        gate = areaGate()
        for name, (cx, cy) in centres.items():
            # one decoder call per boss (24 prompts x 3 masks), as in the study: both at once doubles the GPU memory
            pts = grid_points(cx, cy)
            coords = np.array([[pts[a], pts[b]] for a, b in pairs], dtype=np.float32)
            tc = torch.as_tensor(predictor.transform.apply_coords(coords, shape), dtype=torch.float, device=device)
            masks_t, iou_t, _ = predictor.predict_torch(tc, torch.ones(tc.shape[:2], dtype=torch.int, device=device),
                                                        multimask_output=True)
            masks = masks_t.cpu().numpy()
            scores = iou_t.cpu().numpy()
            del masks_t, iou_t
            measured = []
            kept = []                  # (prompt, mask index) of the masks measured, for the second look below
            for p in range(len(pairs)):
                for k in range(masks.shape[1]):
                    m = masks[p, k]
                    area = int(np.count_nonzero(m))
                    if not (gate[0] <= area <= gate[1]):
                        continue           # cannot be a boss (see the study: most masks are background)
                    d = measure_boss(m, grey)
                    d["iou"] = float(scores[p, k])
                    d["candidate"] = is_boss(d)
                    measured.append(d)
                    kept.append((p, k))
            if not any(r["candidate"] for r in measured):
                # second look, only when no mask passed: SAM's masks can take in a strip next to the boss (fill
                # 0.6, radius 51); an opening with a 21 px disc removes such strips and leaves the round boss
                opened = []
                for (p, k) in kept:
                    m = cv2.morphologyEx(masks[p, k].astype(np.uint8), cv2.MORPH_OPEN, openKernel()) > 0
                    if not (gate[0] <= int(np.count_nonzero(m)) <= gate[1]):
                        continue
                    n, lab, stats, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), connectivity=8)
                    if n > 2:              # keep the largest piece
                        m = lab == (1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA])))
                    d = measure_boss(m, grey)
                    d["iou"] = float(scores[p, k])
                    d["candidate"] = is_boss(d)
                    d["opened"] = True
                    opened.append(d)
                measured = opened
            groups: List[List[Dict]] = []
            for r in sorted(measured, key=lambda r: -r["iou"]):
                for g in groups:
                    a0, a1 = g[0]["area"], r["area"]
                    if min(a0, a1) >= DEDUPE_IOU * max(a0, a1) and _iou(g[0], r) >= DEDUPE_IOU:
                        g.append(r)
                        break
                else:
                    groups.append([r])
            for g in groups:
                for r in g:
                    r["votes"] = len(g)
            cands = [r for r in measured if r["candidate"]]
            for r in cands:
                r["score"] = (r["votes"] * r["circularity"] * r["round_fill"] * min(max(r["contrast"], 0.0), 120.0)
                              / 120.0 * max(0.0, 1.0 - r["concentricity"] / 20.0))
            found[name] = max(cands, key=lambda r: (r["score"], r["iou"])) if cands else None
        return found

    def _arms(self, predictor, device, shape, grey, sx, sy) -> Dict[str, Dict]:
        import torch
        names = ["ARM_L", "ARM_R"]
        arm = armPoints()
        lower = lowerArmRule()
        pts = np.array([[(sx + dx, sy + dy) for dx, dy in arm[n]] for n in names], dtype=np.float32)
        tc = torch.as_tensor(predictor.transform.apply_coords(pts, shape), dtype=torch.float, device=device)
        am, _, _ = predictor.predict_torch(tc, torch.ones(tc.shape[:2], dtype=torch.int, device=device),
                                           multimask_output=False)
        arm_masks = am[:, 0].cpu().numpy()
        found = {}
        for name, m in zip(names, arm_masks):
            if not m.any():
                continue
            d = describe(fill_holes(m), grey)
            d["is_arm"] = (d["grey"] < lower["grey_max"] and lower["area"][0] <= d["area"] <= lower["area"][1]
                           and d["rect_fill"] < lower["rect_fill_max"]
                           and d["bbox"][1] + d["bbox"][3] > sy + lower["reach"]
                           and lower["top_dy"][0] <= d["bbox"][1] - sy <= lower["top_dy"][1])
            found[name] = d
        return found

    # ------------------------------------------------------------------------------------------------- locate
    def locate(self, picture_bgr: np.ndarray) -> Dict[str, Any]:
        """Everything found on the knuckle picture (BGR, 1280 x 720). Keys: spindle, bosses, pins, lowerArm, side,
        type, typeValues, frame (None unless type, side and pins are known), times, notes (why something is
        missing). Masks are kept (full-size for the spindle / arm, box crops for the bosses) for draw()."""
        import torch
        predictor, device = self._model()
        cuda = str(device).startswith("cuda")
        image = cv2.cvtColor(picture_bgr, cv2.COLOR_BGR2RGB)
        grey = cv2.cvtColor(picture_bgr, cv2.COLOR_BGR2GRAY)
        shape = image.shape[:2]
        notes: List[str] = []
        t0 = time.monotonic()
        with torch.no_grad():
            predictor.set_image(image)
            tEmbed = time.monotonic() - t0
            sp = self._spindle(predictor, grey)
            spOk = sp is not None and SPINDLE_R_OK[0] <= sp["radius"] <= SPINDLE_R_OK[1]
            base = (sp["circle_x"], sp["circle_y"]) if sp is not None else VisionFrame.REF_SPINDLE
            centres = {n: (base[0] + d[0], base[1] + d[1]) for n, d in bossFromSpindle().items()}
            bosses = self._bosses(predictor, device, shape, grey, centres)
            fixed = {n: (VisionFrame.REF_SPINDLE[0] + d[0], VisionFrame.REF_SPINDLE[1] + d[1])
                     for n, d in bossFromSpindle().items()}
            gridAt = "spindle"
            if (bosses["BOSS_L"] is None or bosses["BOSS_R"] is None) and sp is not None and \
                    math.hypot(base[0] - VisionFrame.REF_SPINDLE[0], base[1] - VisionFrame.REF_SPINDLE[1]) > 10:
                retry = self._bosses(predictor, device, shape, grey, fixed)
                if sum(b is not None for b in retry.values()) > sum(b is not None for b in bosses.values()):
                    bosses, gridAt = retry, "usual place (not found round the spindle)"
            # one boss found, the other not: look for it where the pin vector puts it from the one found
            for missing, found, sign in (("BOSS_R", "BOSS_L", 1), ("BOSS_L", "BOSS_R", -1)):
                if bosses[missing] is None and bosses[found] is not None:
                    fb = bosses[found]
                    centre = (fb["core_x"] + sign * PIN_VECTOR[0], fb["core_y"] + sign * PIN_VECTOR[1])
                    again = self._bosses(predictor, device, shape, grey, {missing: centre})
                    if again[missing] is not None:
                        bosses[missing] = again[missing]
                        gridAt += f"; {missing} from the other boss"
            sx, sy = base
            arms = self._arms(predictor, device, shape, grey, sx, sy)
            if cuda:
                torch.cuda.synchronize()
        secs = time.monotonic() - t0

        result: Dict[str, Any] = dict(spindle=sp, spindleUsable=spOk, bosses=bosses, arms=arms, gridAt=gridAt,
                                      times=dict(embedding=round(tEmbed, 3), total=round(secs, 3)), notes=notes)
        if sp is None:
            notes.append("spindle not found")
        elif not spOk:
            notes.append(f"spindle mask radius {sp['radius']:.1f} outside {SPINDLE_R_OK[0]:g}-{SPINDLE_R_OK[1]:g} "
                         f"(bearing on the spindle?)")
        bl, br = bosses["BOSS_L"], bosses["BOSS_R"]
        for name, b in (("left", bl), ("right", br)):
            if b is None:
                notes.append(f"{name} boss not found")
        armsFound = [n for n, d in arms.items() if d["is_arm"]]
        side = {("ARM_L",): "LHS", ("ARM_R",): "RHS"}.get(tuple(armsFound))
        if not armsFound:
            notes.append("lower arm not found on the back rest")
        elif side is None:
            notes.append("lower arm found on both sides")
        result["side"] = side
        result["lowerArm"] = arms.get(armsFound[0]) if side else None
        modelType, pins = None, None
        if bl and br:
            pins = ((bl["core_x"], bl["core_y"]), (br["core_x"], br["core_y"]))
            dist = math.hypot(pins[1][0] - pins[0][0], pins[1][1] - pins[0][1])
            areaRatio = (bl["area"] + br["area"]) / 2.0 * 1000.0 / dist ** 2
            radiusRatio = (bl["radius"] + br["radius"]) / 2.0 / dist
            byArea = "DOSTPLUS" if areaRatio >= TYPE_AREA_RATIO else "DOST"
            # the radius ratio vetoes only when it clearly says the other type (outside the band round its limit)
            if radiusRatio >= TYPE_RADIUS_RATIO + TYPE_RADIUS_BAND:
                byRadius = "DOSTPLUS"
            elif radiusRatio <= TYPE_RADIUS_RATIO - TYPE_RADIUS_BAND:
                byRadius = "DOST"
            else:
                byRadius = byArea
            result["typeValues"] = dict(areaRatio=round(areaRatio, 2), areaLimit=TYPE_AREA_RATIO,
                                        radiusRatio=round(radiusRatio, 4), radiusLimit=TYPE_RADIUS_RATIO,
                                        pinDistance=round(dist, 2),
                                        pinAngle=round(math.degrees(math.atan2(pins[1][1] - pins[0][1],
                                                                               pins[1][0] - pins[0][0])), 3))
            if byArea == byRadius:
                modelType = byArea
            else:
                notes.append(f"type unclear: boss area ratio {areaRatio:.2f} says {byArea}, radius ratio "
                             f"{radiusRatio:.4f} says {byRadius}")
        result["type"] = modelType
        result["pins"] = tuple((round(x, 2), round(y, 2)) for x, y in pins) if pins else None
        frame = None
        if modelType and side and pins:
            frame = VisionFrame.buildFrame(modelType, side, pins,
                                           (sp["circle_x"], sp["circle_y"]) if spOk else None)
        result["frame"] = frame
        return result


# ---------------------------------------------------------------------------------------------------------- drawing
def draw(picture_bgr: np.ndarray, result: Dict[str, Any], ok: bool) -> np.ndarray:
    """The parts found, outlined (no text or values on the picture): spindle, bosses with their pin centres, lower
    arm; green when the check is OK, red when not."""
    out = picture_bgr.copy()
    colour = (0, 200, 0) if ok else (0, 0, 255)

    def outline(mask, offset=(0, 0)):
        cs, _ = cv2.findContours(np.ascontiguousarray(mask, dtype=np.uint8) * 255, cv2.RETR_EXTERNAL,
                                 cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(out, cs, -1, colour, 2, offset=offset)

    sp = result.get("spindle")
    if sp is not None:
        outline(sp["mask"])
    for b in (result.get("bosses") or {}).values():
        if b is not None:
            outline(b["mask"], offset=(b["box"][0], b["box"][1]))
            cv2.circle(out, (int(round(b["core_x"])), int(round(b["core_y"]))), 4, (0, 0, 255), -1)
    arm = result.get("lowerArm")
    if arm is not None:
        outline(arm["mask"])
    return out


def summary(result: Dict[str, Any]) -> str:
    """One line for the log."""
    tv = result.get("typeValues") or {}
    sp = result.get("spindle")
    parts = [f"type {result.get('type') or '-'}", f"side {result.get('side') or '-'}"]
    if tv:
        parts.append(f"pins {result['pins']} distance {tv['pinDistance']} angle {tv['pinAngle']:+.2f} deg, boss area "
                     f"ratio {tv['areaRatio']} (limit {tv['areaLimit']}), radius ratio {tv['radiusRatio']} "
                     f"(limit {tv['radiusLimit']})")
    if sp is not None:
        parts.append(f"spindle mask {sp['circle_x']:.1f}, {sp['circle_y']:.1f} r {sp['radius']:.1f}")
    parts.append(f"{result['times']['total']:.2f} s")
    if result.get("notes"):
        parts.append("; ".join(result["notes"]))
    return ", ".join(parts)
