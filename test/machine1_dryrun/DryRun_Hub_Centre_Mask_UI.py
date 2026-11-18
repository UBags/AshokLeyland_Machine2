"""
DryRun_Hub_Centre_Mask_UI - MobileSAM masks round the hub centre on hub (and cap) pictures. A study window: nothing in
production changes.

  1. "Folder..." (every jpg / jpeg / png / bmp in it, by name) or "Picture...", then Start. Default folder:
     test/machine2_dryrun/hub_images. "Seconds between pictures" (1 - 10, starts at 1); Stop ends the run. The work runs
     off the window's thread: the status line shows the picture and its time so far; each picture's time goes to the list.
  2. "Gamma" (1.0 - 6.0 in steps of 0.2, starts at 3.0; 1.0 = as taken): the picture is brightened before MobileSAM sees it,
     as the checks do (255 x (v / 255) ^ (1 / gamma)); the marked picture shows it brightened. Both modes.
  3. "Find masks only around the centre" (unticked at start): ring prompts - one point every "Angle step" degrees
     (default 30, from 0 = right, clockwise) on each ring of "Ring radii" px (default 50, 60) round "Centre" (default
     `cap.center`, Machine 2 644,340); one decoder call, MobileSAM's 3 masks (small to large). "Masks shown": All or
     mask 1 / 2 / 3. Outlines: mask 1 cyan, 2 magenta, 3 yellow; prompt points blue dots; centre blue cross.
  4. Unticked: all masks on a disc of "Disc radius" (default 300) round the centre. The disc is cut out first (black
     outside it; thin white circle on the marked picture) and everything runs on it alone. "Prompts":
       - Grid points: MobileSAM's automatic mask generator, "Points per side" x "Points per side" (16 - 64, default 32)
         (default 64) over the disc's square, only the points inside the disc used (about 21 % fewer); 256 points per decoder pass
         on a GPU, 64 on a CPU. Time grows with the number of points.
       - YOLO boxes (MobileSAMv2, as the hub check's MobileSAMv2AutomaticMaskGenerator): the object-aware YOLO model
         (models/MobileSAM/MobileSAMv2/weight/ObjectAwareModel.pt, through ModelManager) proposes boxes on the disc
         ("YOLO confidence", default 0.25; its own box NMS 0.7); boxes whose centre lies outside the disc are left out;
         one MobileSAM decoder pass per 64 boxes, one mask per box. Tens of prompts instead of hundreds: much faster.
     Default prompts: YOLO boxes. Filters, both: "Min stability" (0.70 - 1.00, step 0.02, default 0.70; SAM's own
     stability score of the mask), "Min predicted IoU" (0.50 - 1.00, step 0.02, default 0.50), "Box merge IoU"
     (0.30 - 1.00, step 0.05, default 0.50: of two masks whose boxes overlap more than this, the one with the higher predicted IoU is kept). Then each
     mask is kept to the disc; one with less than half of it inside the disc is dropped. Each mask outlined in its own
     colour, listed largest first. Every value used is in the picture's line.
     "Min area" (default 1000 px): smaller masks dropped (counted in the line).
     "Refine the largest mask with point prompts" (ticked at start, no YOLO): from the largest mask's fitted-ellipse
     centre and radius, positive points on rings at "positive rings" x the radius (default 0.5, 0.8), negative points
     on a ring at "negative ring" x the radius (default 1.15), every "angle step" degrees (default 30), plus ("with its
     box") its bounding box grown by 4 px; one decoder call on the same embedding; MobileSAM's 3 masks listed with
     score, size, fitted circle, how far the outline lies off that circle (RMS / max px), IoU with the first mask and
     how far the circle centre moved; the one with the highest score is drawn white (positive points blue, negative
     black). Defaults (CHANGES 136, the user's settings): gamma 2.0, disc 160, YOLO confidence 0.15, 3 s between
     pictures, the rest as above.
  5. Per picture and mask, in the list at the bottom and in the terminal: score (predicted IoU), stability, area,
     centroid, fitted-ellipse centre, mean radius, roundness, distance from the centre; the picture's time.
  6. Every run writes <folder>/hub_centre_mask_results/details_<time>.csv (one row per picture, every value of the
     unticked mode: settings, YOLO boxes, times, first mask, the 3 refined masks) and run_<time>.txt (every line of the
     list). "Save marked pictures" also writes each marked picture there. No text on any picture.

MobileSAM: models/MobileSAM/weights/mobile_sam.pt, or --sam <mobile_sam.pt>; loaded in the background when the window
opens (GPU if there is one), together with YOLO, and both run once on a blank disc (warm-up, as the vision check
worker does), so the first picture's time is the real one. YOLO boxes need the YOLO weights (the laptop has them). No Redis, PLC or camera needed.

Run from the project root:   python test/machine1_dryrun/DryRun_Hub_Centre_Mask_UI.py   [--sam <mobile_sam.pt>]
Without a window:            python test/machine1_dryrun/DryRun_Hub_Centre_Mask_UI.py --batch <folder or picture>
                             [--centre 644,340] [--radii 50,60] [--step 30] [--gamma 2.0] [--save]
                             [--whole [--disc 160] [--prompts yolo|grid] [--pps 64] [--stability 0.70] [--iou 0.50]
                              [--nms 0.50] [--conf 0.15] [--min-area 1000]
                              [--no-refine] [--refine-rings 0.5,0.8] [--refine-neg 1.15] [--refine-step 30] [--refine-no-box]]
"""
import os
import pathlib
import sys
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("COSTHETA_PLC_VENDOR", "siemens" if HERE.name.startswith("machine2") else "allenbradley")

import cv2
import numpy as np

EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp")
VIEW_W, VIEW_H = 1120, 630
MASK_COLOURS = ((255, 255, 0), (255, 0, 255), (0, 255, 255))     # BGR: cyan, magenta, yellow
POINT_COLOUR = (255, 0, 0)                                        # BGR blue
DISC_RADIUS = 160                                                 # px; the disc cut round the centre (whole mode)
BOX_BATCH = 64                                                    # YOLO boxes per MobileSAM decoder pass
WHOLE_DEFAULTS = dict(prompts="yolo", pointsPerSide=64, stabilityMin=0.70, iouMin=0.50, nmsIou=0.50, yoloConf=0.15,
                      discRadius=160.0, minArea=1000, refine=True, refineRings=(0.5, 0.8), refineNegative=1.15,
                      refineStep=30.0, refineBox=True)
DEFAULT_SAM = ROOT / "models" / "MobileSAM" / "weights" / "mobile_sam.pt"


def argValue(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def defaultCentre():
    """The cap check's centre (on the hub bore), from this machine's properties."""
    try:
        from camera.CheckCap import CheckCap
        return float(CheckCap.CENTER[0]), float(CheckCap.CENTER[1])
    except Exception:
        return 644.0, 340.0


def promptPoints(centre, radii, stepDeg):
    """Points on each ring, every stepDeg degrees from 0 (right), clockwise on the picture (y down)."""
    angles = np.deg2rad(np.arange(0.0, 360.0 - 1e-9, float(stepDeg)))
    return np.array([[centre[0] + r * np.cos(a), centre[1] + r * np.sin(a)] for r in radii for a in angles],
                    dtype=np.float32)


def parseCentre(text):
    x, y = (float(v) for v in text.replace(" ", "").split(","))
    return x, y


def parseRadii(text):
    radii = [float(v) for v in text.replace(" ", "").split(",") if v]
    if not radii or any(r <= 0 for r in radii):
        raise ValueError("radii must be positive numbers, e.g. 50,60")
    return radii


def parseStep(text):
    step = float(text)
    if not 1.0 <= step <= 180.0:
        raise ValueError("angle step must be 1 - 180 degrees")
    return step


def parseGamma(text):
    gamma = float(text)
    if not 1.0 <= gamma <= 6.0:
        raise ValueError("gamma must be 1.0 - 6.0")
    return gamma


def inRange(name, text, low, high, cast=float):
    value = cast(float(text))
    if not low - 1e-9 <= value <= high + 1e-9:
        raise ValueError(f"{name} must be {low} - {high}")
    return value


def wholeSettings(prompts, pps, stability, iou, nms, conf, disc, minArea=WHOLE_DEFAULTS["minArea"],
                  refine=WHOLE_DEFAULTS["refine"], refineRings="0.5,0.8", refineNegative=WHOLE_DEFAULTS["refineNegative"],
                  refineStep=WHOLE_DEFAULTS["refineStep"], refineBox=WHOLE_DEFAULTS["refineBox"]):
    """The whole-mode settings, checked."""
    if prompts not in ("grid", "yolo"):
        raise ValueError("prompts must be grid or yolo")
    rings = tuple(float(v) for v in str(refineRings).replace(" ", "").split(",") if v) if not isinstance(
        refineRings, tuple) else refineRings
    if not rings or any(not 0.05 <= r <= 0.98 for r in rings):
        raise ValueError("refine rings must be fractions of the radius, 0.05 - 0.98, e.g. 0.5,0.8")
    return dict(prompts=prompts, pointsPerSide=inRange("points per side", pps, 16, 64, int),
                stabilityMin=inRange("min stability", stability, 0.70, 1.00),
                iouMin=inRange("min predicted IoU", iou, 0.50, 1.00), nmsIou=inRange("box merge IoU", nms, 0.30, 1.00),
                yoloConf=inRange("YOLO confidence", conf, 0.05, 0.95), discRadius=inRange("disc radius", disc, 20, 2000),
                minArea=inRange("min area", minArea, 0, 1000000, int), refine=bool(refine), refineRings=rings,
                refineNegative=inRange("negative ring", refineNegative, 1.02, 2.0),
                refineStep=inRange("refine angle step", refineStep, 5, 180), refineBox=bool(refineBox))


def _noGrad():
    try:
        import torch
        return torch.inference_mode()
    except ImportError:                                      # stand-in models in tests
        import contextlib
        return contextlib.nullcontext()


class HubCentreSegmenter:
    """MobileSAM predictor plus, when needed, the automatic generator (grid) and the YOLO model (boxes)."""

    def __init__(self, samPath=None, predictor=None, generatorFactory=None, yolo=None):
        self.samPath = str(samPath or DEFAULT_SAM)
        self.predictor = predictor
        self.generatorFactory = generatorFactory             # tests: (settings, pointGrid) -> object with generate()
        self.yolo = yolo
        self._generator, self._generatorKey = None, None
        self.device = "given"
        self.yoloNote = ""
        self.lastTimes = {}                                   # the last whole-mode picture's times (for the CSV)
        self.embedded = None                                  # the crop MobileSAM's embedding is of (id)

    def load(self):
        if self.predictor is not None:
            return
        import camera.ModelManager  # noqa: F401 - its import sets the timm / registry warning filters (as in production)
        import torch
        from mobile_sam import SamPredictor, sam_model_registry
        if not os.path.isfile(self.samPath):
            raise FileNotFoundError(f"MobileSAM weights not found at {self.samPath} (give --sam)")
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        model = sam_model_registry["vit_t"](checkpoint=self.samPath).to(self.device).eval()
        self.predictor = SamPredictor(model)
        self.warmUp()

    def warmUp(self):
        """Load YOLO and run both models once on a blank disc, as the vision check worker warms up at start, so that the
        first picture's time is the real one. YOLO missing: noted (yoloNote), the grid and ring modes still work."""
        t0 = time.perf_counter()
        blank = np.zeros((2 * DISC_RADIUS + 1, 2 * DISC_RADIUS + 1, 3), np.uint8)
        try:
            with _noGrad():
                self.predictor.set_image(blank)
            self._yoloModel().predict(blank, conf=0.25, iou=0.7, verbose=False)
            self.yoloNote = f"YOLO ready; warm-up {time.perf_counter() - t0:.1f} s"
        except Exception as e:
            self.yoloNote = f"YOLO not available ({type(e).__name__}: {e}) - Grid points and ring prompts still work"

    # ---- ring prompts round the centre ----
    def segment(self, picture, centre, radii, stepDeg):
        points = promptPoints(centre, radii, stepDeg)
        t0 = time.perf_counter()
        self.predictor.set_image(cv2.cvtColor(picture, cv2.COLOR_BGR2RGB))
        tEmbed = time.perf_counter() - t0
        with _noGrad():
            masks, scores, _ = self.predictor.predict(point_coords=points, point_labels=np.ones(len(points)),
                                                      multimask_output=True)
        return masks, np.asarray(scores, dtype=float), points, (tEmbed, time.perf_counter() - t0 - tEmbed)

    # ---- all masks on the disc ----
    def segmentWhole(self, crop, disc, s):
        """Masks on the cut-out disc (crop is black outside it): (list of dicts with segmentation, area,
        predicted_iou, stability_score; seconds; a note on the prompts)."""
        t0 = time.perf_counter()
        if s["prompts"] == "yolo":
            found, note = self._yoloMasks(crop, disc, s)
        else:
            found, note = self._gridMasks(crop, disc, s)
        return found, time.perf_counter() - t0, note

    def refine(self, crop, mask, s):
        """Fresh point prompts from the first mask's outline (no YOLO): positive points on rings at refineRings x its
        radius and negative points on a ring at refineNegative x its radius, every refineStep degrees round its
        fitted-ellipse centre, plus (refineBox) its bounding box grown by 4 px; MobileSAM's 3 masks back.
        -> (masks, scores, points, labels, box or None, seconds) in crop coordinates, or None if it cannot be fitted."""
        meas = measure(mask, (0.0, 0.0))
        if meas["ellipse"] is None:
            return None
        (cx, cy), r = meas["ellipse"], meas["radius"]
        t0 = time.perf_counter()
        if self.embedded != id(crop):
            self.predictor.set_image(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
            self.embedded = id(crop)
        h, w = crop.shape[:2]
        pos = promptPoints((cx, cy), [f * r for f in s["refineRings"]], s["refineStep"])
        neg = promptPoints((cx, cy), [s["refineNegative"] * r], s["refineStep"])
        neg = neg[(neg[:, 0] >= 0) & (neg[:, 0] < w) & (neg[:, 1] >= 0) & (neg[:, 1] < h)]
        points = np.concatenate([pos, neg]).astype(np.float32)
        labels = np.concatenate([np.ones(len(pos)), np.zeros(len(neg))])
        box = None
        if s["refineBox"]:
            x, y, bw, bh = cv2.boundingRect(mask.astype(np.uint8))
            box = np.array([max(x - 4, 0), max(y - 4, 0), min(x + bw + 4, w - 1), min(y + bh + 4, h - 1)], np.float32)
        with _noGrad():
            masks, scores, _ = self.predictor.predict(point_coords=points, point_labels=labels, box=box,
                                                      multimask_output=True)
        return masks, np.asarray(scores, dtype=float), points, labels, box, time.perf_counter() - t0

    @staticmethod
    def pointGrid(shape, disc, n):
        """The n x n grid of the automatic generator (normalised x, y), only the points inside the disc."""
        h, w = shape[:2]
        offset = 1.0 / (2 * n)
        side = np.linspace(offset, 1 - offset, n)
        pts = np.stack(np.meshgrid(side, side), axis=-1).reshape(-1, 2)
        keep = disc[np.clip((pts[:, 1] * h).astype(int), 0, h - 1), np.clip((pts[:, 0] * w).astype(int), 0, w - 1)]
        return pts[keep]

    def _gridMasks(self, crop, disc, s):
        grid = self.pointGrid(crop.shape, disc, s["pointsPerSide"])
        perPass = 256 if self.device == "cuda" else 64
        key = (s["pointsPerSide"], s["stabilityMin"], s["iouMin"], s["nmsIou"], crop.shape[:2])
        if self._generator is None or self._generatorKey != key:
            if self.generatorFactory is not None:
                self._generator = self.generatorFactory(s, grid)
            else:
                from mobile_sam import SamAutomaticMaskGenerator
                self._generator = SamAutomaticMaskGenerator(
                    self.predictor.model, points_per_side=None, point_grids=[grid], points_per_batch=perPass,
                    pred_iou_thresh=s["iouMin"], stability_score_thresh=s["stabilityMin"], box_nms_thresh=s["nmsIou"])
            self._generatorKey = key
        t0 = time.perf_counter()
        with _noGrad():
            found = self._generator.generate(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
        self.lastTimes = dict(grid=time.perf_counter() - t0, gridPoints=len(grid), merged=len(found))
        return found, (f"grid {s['pointsPerSide']} x {s['pointsPerSide']}, {len(grid)} points inside the disc, "
                       f"{perPass} per pass")

    def _yoloModel(self):
        if self.yolo is None:
            from camera.ModelManager import ModelManager
            self.yolo = ModelManager.get_instance().get_yolo_model()
        return self.yolo

    def _yoloMasks(self, crop, disc, s):
        """MobileSAMv2: YOLO boxes on the disc as box prompts, batched through MobileSAM; SAM's stability and
        predicted IoU filters and box merging as for the grid."""
        import torch
        from torchvision.ops import batched_nms
        from mobile_sam.utils.amg import batched_mask_to_box, calculate_stability_score
        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        t0 = time.perf_counter()
        results = self._yoloModel().predict(rgb, conf=s["yoloConf"], iou=0.7, verbose=False)
        boxes = (results[0].boxes.xyxy.cpu().numpy() if len(results) and results[0].boxes is not None
                 else np.zeros((0, 4)))
        tYolo = time.perf_counter() - t0
        self.lastTimes = dict(yolo=tYolo, boxesFound=len(boxes))
        h, w = disc.shape
        if len(boxes):
            cxs = np.clip(((boxes[:, 0] + boxes[:, 2]) / 2).astype(int), 0, w - 1)
            cys = np.clip(((boxes[:, 1] + boxes[:, 3]) / 2).astype(int), 0, h - 1)
            onDisc = boxes[disc[cys, cxs]]
        else:
            onDisc = boxes
        note = (f"YOLO boxes: {len(boxes)} found (confidence >= {s['yoloConf']:.2f}, {tYolo:.2f} s), "
                f"{len(onDisc)} centred on the disc")
        self.lastTimes["boxesOnDisc"] = len(onDisc)
        if len(onDisc) == 0:
            return [], note
        t1 = time.perf_counter()
        self.predictor.set_image(rgb)
        self.embedded = id(crop)
        tEmbed = time.perf_counter() - t1
        t1 = time.perf_counter()
        model = self.predictor.model
        allMasks, allIou, allStab = [], [], []
        with torch.inference_mode():
            for i in range(0, len(onDisc), BOX_BATCH):
                b = torch.as_tensor(onDisc[i:i + BOX_BATCH], dtype=torch.float, device=self.predictor.device)
                tb = self.predictor.transform.apply_boxes_torch(b, rgb.shape[:2])
                logits, iou, _ = self.predictor.predict_torch(point_coords=None, point_labels=None, boxes=tb,
                                                              multimask_output=False, return_logits=True)
                logits, iou = logits[:, 0], iou[:, 0]
                allStab.append(calculate_stability_score(logits, model.mask_threshold, 1.0))
                allMasks.append(logits > model.mask_threshold)
                allIou.append(iou)
            masks, iou, stab = torch.cat(allMasks), torch.cat(allIou), torch.cat(allStab)
            keep = (iou >= s["iouMin"]) & (stab >= s["stabilityMin"]) & (masks.flatten(1).sum(1) > 0)
            passed = int(keep.sum())
            masks, iou, stab = masks[keep], iou[keep], stab[keep]
            if len(masks):
                mboxes = batched_mask_to_box(masks).float()
                k = batched_nms(mboxes, iou, torch.zeros(len(masks), device=masks.device), s["nmsIou"])
                masks, iou, stab = masks[k], iou[k], stab[k]
        found = [dict(segmentation=m.cpu().numpy(), area=int(m.sum()), predicted_iou=float(q), stability_score=float(st))
                 for m, q, st in zip(masks, iou, stab)]
        self.lastTimes.update(embed=tEmbed, decode=time.perf_counter() - t1, passed=passed, merged=len(found))
        return found, (note + f", {passed} passed the filters, {len(found)} after box merging; SAM embedding "
                       f"{tEmbed:.2f} s, decoder + filters {time.perf_counter() - t1:.2f} s")


def measure(mask, centre):
    """Area, centroid, fitted ellipse (centre, mean radius, roundness) and the centroid's distance from centre."""
    m = mask.astype(np.uint8)
    area = int(m.sum())
    out = dict(area=area, centroid=None, ellipse=None, radius=None, roundness=None, fromCentre=None, contour=None)
    if area == 0:
        return out
    ys, xs = np.nonzero(m)
    cx, cy = float(xs.mean()), float(ys.mean())
    out.update(centroid=(cx, cy), fromCentre=float(np.hypot(cx - centre[0], cy - centre[1])))
    contours, _ = cv2.findContours(m * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if contours:
        c = max(contours, key=cv2.contourArea)
        out["contour"] = c
        if len(c) >= 5:
            (ex, ey), (a, b), _ = cv2.fitEllipse(c)
            major, minor = max(a, b) / 2.0, min(a, b) / 2.0
            if major > 0:
                out.update(ellipse=(float(ex), float(ey)), radius=(major + minor) / 2.0, roundness=minor / major)
            pts = c.reshape(-1, 2).astype(np.float64)
            try:                                              # least-squares circle through the outline
                sol = np.linalg.lstsq(np.c_[2 * pts, np.ones(len(pts))], (pts ** 2).sum(1), rcond=None)[0]
                rc = float(np.sqrt(max(sol[2] + sol[0] ** 2 + sol[1] ** 2, 0.0)))
                d = np.hypot(pts[:, 0] - sol[0], pts[:, 1] - sol[1]) - rc
                out.update(circle=(float(sol[0]), float(sol[1]), rc), edgeRms=float(np.sqrt((d ** 2).mean())),
                           edgeMax=float(np.abs(d).max()))
            except np.linalg.LinAlgError:
                pass
    return out


def describe(i, score, meas):
    if meas["area"] == 0:
        return f"mask {i + 1}: score {score:.3f}, empty"
    text = (f"mask {i + 1}: score {score:.3f}, area {meas['area']}, centroid ({meas['centroid'][0]:.1f}, "
            f"{meas['centroid'][1]:.1f}) {meas['fromCentre']:.1f} px from the centre")
    if meas["ellipse"] is not None:
        text += (f", ellipse centre ({meas['ellipse'][0]:.1f}, {meas['ellipse'][1]:.1f}), radius {meas['radius']:.1f}, "
                 f"roundness {meas['roundness']:.3f}")
    if meas.get("circle") is not None:
        text += (f", fitted circle ({meas['circle'][0]:.1f}, {meas['circle'][1]:.1f}) r {meas['circle'][2]:.1f}, "
                 f"outline off the circle RMS {meas['edgeRms']:.2f} px / max {meas['edgeMax']:.1f} px")
    return text


def draw(picture, masks, measures, points, centre, show="All"):
    """Outlines only (no text): masks in their colours, prompt points as blue dots, the centre as a blue cross."""
    marked = picture.copy()
    for i, meas in enumerate(measures):
        if meas["contour"] is not None and (show == "All" or show == f"Mask {i + 1}"):
            cv2.drawContours(marked, [meas["contour"]], -1, MASK_COLOURS[i % 3], 2)
    for x, y in points:
        cv2.circle(marked, (int(round(x)), int(round(y))), 3, POINT_COLOUR, -1)
    c = (int(round(centre[0])), int(round(centre[1])))
    cv2.line(marked, (c[0] - 6, c[1]), (c[0] + 6, c[1]), POINT_COLOUR, 1)
    cv2.line(marked, (c[0], c[1] - 6), (c[0], c[1] + 6), POINT_COLOUR, 1)
    return marked


def applyGamma(picture, gamma):
    """255 x (v / 255) ^ (1 / gamma), as the checks do: gamma > 1 brightens; 1.0 leaves the picture as it is."""
    if abs(gamma - 1.0) < 1e-9:
        return picture
    table = np.array([((i / 255.0) ** (1.0 / gamma)) * 255 for i in range(256)]).clip(0, 255).astype(np.uint8)
    return cv2.LUT(picture, table)


def distinctColours(n):
    """n well-separated BGR colours (hues spread by the golden ratio, full saturation)."""
    out = []
    for i in range(n):
        hsv = np.uint8([[[int((i * 0.618034 % 1.0) * 179), 255, 255 if i % 2 == 0 else 200]]])
        out.append(tuple(int(v) for v in cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0, 0]))
    return out


def drawWhole(picture, measures):
    """Outlines only (no text): every mask in its own colour."""
    marked = picture.copy()
    for meas, colour in zip(measures, distinctColours(len(measures))):
        if meas["contour"] is not None:
            cv2.drawContours(marked, [meas["contour"]], -1, colour, 2)
    return marked


def cutDisc(picture, centre, radius):
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


def loadPicture(path):
    image = cv2.imread(str(path))
    if image is None:
        return None
    return cv2.resize(image, (1280, 720)) if image.shape[:2] != (720, 1280) else image


def picturesIn(target):
    target = pathlib.Path(target)
    if target.is_file():
        return [target]
    return sorted(p for p in target.iterdir() if p.suffix.lower() in EXTENSIONS)


def processOne(segmenter, path, centre, radii, stepDeg, show="All", gamma=1.0):
    """Ring prompts round the centre -> (marked picture or None, list of lines)."""
    picture = loadPicture(path)
    if picture is None:
        return None, [f"{path.name}: could not read the picture"]
    picture = applyGamma(picture, gamma)
    masks, scores, points, (tEmbed, tDecode) = segmenter.segment(picture, centre, radii, stepDeg)
    measures = [measure(m, centre) for m in masks]
    lines = [f"{path.name}: {len(points)} prompt points (rings {', '.join(f'{r:g}' for r in radii)} px, every "
             f"{stepDeg:g} deg round ({centre[0]:g}, {centre[1]:g})); gamma {gamma:.1f}; time {tEmbed + tDecode:.2f} s "
             f"(embedding {tEmbed:.2f} s, decoder {tDecode:.2f} s)"]
    lines += ["    " + describe(i, float(scores[i]), m) for i, m in enumerate(measures)]
    return draw(picture, masks, measures, points, centre, show), lines


def _iou(a, b):
    u = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / u) if u else 0.0


_MEAS = ("score", "stability", "area", "cx", "cy", "circle_r", "ellipse_r", "roundness", "edge_rms", "edge_max",
         "from_centre")
CSV_COLUMNS = (["picture", "gamma", "disc", "prompts", "yolo_conf", "min_stability", "min_iou", "merge_iou", "min_area",
                "boxes_found", "boxes_on_disc", "passed_filters", "masks_after_merge", "masks_kept", "t_yolo", "t_embed",
                "t_decode", "t_grid", "t_masks"] + [f"first_{k}" for k in _MEAS]
               + ["t_refine", "refine_positive", "refine_negative", "refine_box", "refine_pick"]
               + [f"ref{i}_{k}" for i in (1, 2, 3) for k in _MEAS + ("iou_first", "shift")] + ["t_total"])


def appendCsv(path, row):
    """One row per picture, the fixed columns CSV_COLUMNS (empty where a value does not apply); header when new."""
    import csv
    new = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore", restval="")
        if new:
            w.writeheader()
        w.writerow(row)


def _measureRow(prefix, meas, score=None, stability=None):
    c = meas.get("circle") or (None, None, None)
    return {f"{prefix}_score": score, f"{prefix}_stability": stability, f"{prefix}_area": meas["area"],
            f"{prefix}_cx": c[0], f"{prefix}_cy": c[1], f"{prefix}_circle_r": c[2],
            f"{prefix}_ellipse_r": meas["radius"], f"{prefix}_roundness": meas["roundness"],
            f"{prefix}_edge_rms": meas.get("edgeRms"), f"{prefix}_edge_max": meas.get("edgeMax"),
            f"{prefix}_from_centre": meas["fromCentre"]}


def processWhole(segmenter, path, centre, gamma=1.0, settings=None, csvPath=None):
    """All masks on the disc round the centre -> (marked picture or None, list of lines). The disc is cut first
    (black outside it); the masks are found on that alone; each is kept to the disc; one with less than half of its
    area inside the disc, or smaller than minArea, is dropped. With refine: the largest mask's outline gives fresh
    point prompts (see HubCentreSegmenter.refine). csvPath: one row per picture with every value."""
    s = dict(WHOLE_DEFAULTS, **(settings or {}))
    picture = loadPicture(path)
    if picture is None:
        return None, [f"{path.name}: could not read the picture"]
    picture = applyGamma(picture, gamma)
    crop, disc, (x0, y0) = cutDisc(picture, centre, s["discRadius"])
    tStart = time.perf_counter()
    found, secs, note = segmenter.segmentWhole(crop, disc, s)
    kept, outside, small = [], 0, 0
    for d in found:
        seg = np.asarray(d["segmentation"]).astype(bool)
        inside = seg & disc
        if inside.sum() * 2 < seg.sum() or not inside.any():
            outside += 1
            continue
        if inside.sum() < s["minArea"]:
            small += 1
            continue
        kept.append((d, inside))
    kept.sort(key=lambda t: -int(t[1].sum()))

    def full(m):
        out = np.zeros(picture.shape[:2], bool)
        out[y0:y0 + m.shape[0], x0:x0 + m.shape[1]] = m
        return out

    measures = [measure(full(m), centre) for _, m in kept]
    lines = [f"{path.name}: disc r {s['discRadius']:g} round ({centre[0]:g}, {centre[1]:g}), {len(kept)} masks; "
             f"{note}; filters: stability >= {s['stabilityMin']:.2f}, predicted IoU >= {s['iouMin']:.2f}, box merge "
             f"IoU {s['nmsIou']:.2f}, area >= {s['minArea']} px; dropped {outside} (less than half inside the disc), "
             f"{small} (smaller than the min area); gamma {gamma:.1f}; time {secs:.2f} s for the masks"]
    for i, ((d, _), meas) in enumerate(zip(kept, measures)):
        lines.append(f"    {describe(i, float(d.get('predicted_iou', 0.0)), meas)}, "
                     f"stability {float(d.get('stability_score', 0.0)):.3f}")
    row = dict(picture=path.name, gamma=gamma, disc=s["discRadius"], prompts=s["prompts"], yolo_conf=s["yoloConf"],
               min_stability=s["stabilityMin"], min_iou=s["iouMin"], merge_iou=s["nmsIou"], min_area=s["minArea"],
               boxes_found=segmenter.lastTimes.get("boxesFound"), boxes_on_disc=segmenter.lastTimes.get("boxesOnDisc"),
               passed_filters=segmenter.lastTimes.get("passed"), masks_after_merge=segmenter.lastTimes.get("merged"),
               masks_kept=len(kept), t_yolo=segmenter.lastTimes.get("yolo"), t_embed=segmenter.lastTimes.get("embed"),
               t_decode=segmenter.lastTimes.get("decode"), t_grid=segmenter.lastTimes.get("grid"), t_masks=secs)
    row.update(_measureRow("first", measures[0], float(kept[0][0].get("predicted_iou", 0)),
                           float(kept[0][0].get("stability_score", 0))) if kept else {"first_area": None})
    marked = drawWhole(picture, measures)
    refined = None
    if s["refine"] and kept:
        res = segmenter.refine(crop, kept[0][1], s)
        if res is None:
            lines.append("    refine: the first mask's outline could not be fitted")
        else:
            rmasks, rscores, points, labels, box, tRef = res
            rmeas = [measure(full(m & disc), centre) for m in rmasks]
            first = full(kept[0][1])
            pick = int(np.argmax(rscores))
            lines.append(f"    refine (first mask's outline -> {int(labels.sum())} positive points on rings "
                         f"{', '.join(f'{f:g}' for f in s['refineRings'])} x its radius, {int((labels == 0).sum())} "
                         f"negative at {s['refineNegative']:g} x, every {s['refineStep']:g} deg"
                         f"{', box' if box is not None else ''}): {tRef:.2f} s; picked the highest score, mask {pick + 1}")
            row.update(t_refine=tRef, refine_positive=int(labels.sum()), refine_negative=int((labels == 0).sum()),
                       refine_box=box is not None, refine_pick=pick + 1)
            for k, (m, sc, me) in enumerate(zip(rmasks, rscores, rmeas)):
                iou = _iou(full(m & disc), first)
                shift = (float(np.hypot(me["circle"][0] - measures[0]["circle"][0], me["circle"][1] - measures[0]["circle"][1]))
                         if me.get("circle") and measures[0].get("circle") else None)
                lines.append(f"      refined {describe(k, float(sc), me)}; IoU with the first mask {iou:.3f}"
                             + (f", circle centre moved {shift:.1f} px" if shift is not None else ""))
                row.update(_measureRow(f"ref{k + 1}", me, float(sc)))
                row.update({f"ref{k + 1}_iou_first": iou, f"ref{k + 1}_shift": shift})
            refined = rmeas[pick]
            if refined["contour"] is not None:
                cv2.drawContours(marked, [refined["contour"]], -1, (255, 255, 255), 2)
            for (px, py), lab in zip(points, labels):
                cv2.circle(marked, (int(round(px + x0)), int(round(py + y0))), 3, POINT_COLOUR if lab else (0, 0, 0), -1)
    total = time.perf_counter() - tStart
    lines[0] += f"; {total:.2f} s in all (with refine, measuring and drawing)"
    row["t_total"] = total
    cv2.circle(marked, (int(round(centre[0])), int(round(centre[1]))), int(round(s["discRadius"])), (200, 200, 200), 1)
    if csvPath is not None:
        try:
            appendCsv(csvPath, row)
        except Exception as e:
            lines.append(f"    CSV not written: {e}")
    return marked, lines


def runBatch():
    target = argValue("--batch")
    centre = parseCentre(argValue("--centre")) if "--centre" in sys.argv else defaultCentre()
    radii = parseRadii(argValue("--radii", "50,60"))
    step = parseStep(argValue("--step", "30"))
    gamma = parseGamma(argValue("--gamma", "2.0"))
    d = WHOLE_DEFAULTS
    whole = wholeSettings(argValue("--prompts", d["prompts"]), argValue("--pps", d["pointsPerSide"]),
                          argValue("--stability", d["stabilityMin"]), argValue("--iou", d["iouMin"]),
                          argValue("--nms", d["nmsIou"]), argValue("--conf", d["yoloConf"]),
                          argValue("--disc", d["discRadius"]), argValue("--min-area", d["minArea"]),
                          "--no-refine" not in sys.argv, argValue("--refine-rings", "0.5,0.8"),
                          argValue("--refine-neg", d["refineNegative"]), argValue("--refine-step", d["refineStep"]),
                          "--refine-no-box" not in sys.argv)
    segmenter = HubCentreSegmenter(argValue("--sam"))
    segmenter.load()
    print(f"MobileSAM on {segmenter.device}")
    files = picturesIn(target)
    outDir = (pathlib.Path(target) if pathlib.Path(target).is_dir() else pathlib.Path(target).parent) / "hub_centre_mask_results"
    outDir.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    csvPath, logPath = outDir / f"details_{stamp}.csv", outDir / f"run_{stamp}.txt"
    print(f"Details: {csvPath}; all lines: {logPath}")
    for path in files:
        if "--whole" in sys.argv:
            marked, lines = processWhole(segmenter, path, centre, gamma, whole, csvPath)
        else:
            marked, lines = processOne(segmenter, path, centre, radii, step, gamma=gamma)
        print("\n".join(lines), flush=True)
        with open(logPath, "a") as f:
            f.write("\n".join(lines) + "\n")
        if marked is not None and "--save" in sys.argv:
            outDir.mkdir(exist_ok=True)
            cv2.imwrite(str(outDir / (path.stem + "_masks.png")), marked)
    print(f"{len(files)} picture(s)")
    return 0


def runWindow():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from PIL import Image, ImageTk

    class App:
        def __init__(self, root, segmenter):
            self.root, self.segmenter = root, segmenter
            self.files, self.index, self.running, self.after = [], 0, False, None
            self.modelReady, self.modelError = False, None
            self.job = None                                   # (thread, started, result, path) of the picture in work
            self.folder = HERE.parent / "machine2_dryrun" / "hub_images"
            root.title("Hub centre masks - MobileSAM")
            bar = ttk.Frame(root); bar.pack(fill="x", padx=6, pady=4)
            ttk.Button(bar, text="Folder...", command=self.chooseFolder).pack(side="left")
            ttk.Button(bar, text="Picture...", command=self.choosePicture).pack(side="left", padx=4)
            self.sourceText = tk.StringVar(master=root, value=str(self.folder))
            ttk.Label(bar, textvariable=self.sourceText).pack(side="left", padx=6)
            self.startButton = ttk.Button(bar, text="Start", command=self.start); self.startButton.pack(side="right")
            self.stopButton = ttk.Button(bar, text="Stop", command=self.stop, state="disabled")
            self.stopButton.pack(side="right", padx=4)

            d = WHOLE_DEFAULTS
            c = defaultCentre()
            V = lambda v: tk.StringVar(master=root, value=v)
            self.centreVar, self.radiiVar, self.stepVar = V(f"{c[0]:g},{c[1]:g}"), V("50,60"), V("30")
            self.secondsVar = tk.IntVar(master=root, value=3)
            self.gammaVar, self.showVar = V("2.0"), V("All")
            self.saveVar = tk.BooleanVar(master=root, value=False)
            self.centreOnlyVar = tk.BooleanVar(master=root, value=False)
            self.discVar, self.promptsVar = V(f"{d['discRadius']:g}"), V("YOLO boxes" if d["prompts"] == "yolo" else "Grid points")
            self.ppsVar, self.stabilityVar = V(str(d["pointsPerSide"])), V(f"{d['stabilityMin']:.2f}")
            self.iouVar, self.nmsVar = V(f"{d['iouMin']:.2f}"), V(f"{d['nmsIou']:.2f}")
            self.confVar = V(f"{d['yoloConf']:.2f}")
            self.minAreaVar = V(str(d["minArea"]))
            self.refineVar = tk.BooleanVar(master=root, value=d["refine"])
            self.refineBoxVar = tk.BooleanVar(master=root, value=d["refineBox"])
            self.refineRingsVar = V(",".join(f"{f:g}" for f in d["refineRings"]))
            self.refineNegVar, self.refineStepVar = V(f"{d['refineNegative']:.2f}"), V(f"{d['refineStep']:g}")
            self.csvPath = self.logPath = None

            def spin(parent, label, var, lo, hi, inc, fmt, width=5):
                ttk.Label(parent, text=label).pack(side="left")
                ttk.Spinbox(parent, from_=lo, to=hi, increment=inc, format=fmt, textvariable=var,
                            width=width).pack(side="left", padx=(2, 10))

            bar2 = ttk.Frame(root); bar2.pack(fill="x", padx=6)
            for label, var, width in (("Centre x,y", self.centreVar, 10), ("Ring radii (px)", self.radiiVar, 10),
                                      ("Angle step (deg)", self.stepVar, 5)):
                ttk.Label(bar2, text=label).pack(side="left")
                ttk.Entry(bar2, textvariable=var, width=width).pack(side="left", padx=(2, 10))
            ttk.Label(bar2, text="Seconds between pictures").pack(side="left")
            ttk.Spinbox(bar2, from_=1, to=10, textvariable=self.secondsVar, width=4).pack(side="left", padx=(2, 10))
            spin(bar2, "Gamma", self.gammaVar, 1.0, 6.0, 0.2, "%.1f")
            ttk.Label(bar2, text="Masks shown").pack(side="left")
            ttk.Combobox(bar2, textvariable=self.showVar, values=["All", "Mask 1", "Mask 2", "Mask 3"], width=8,
                         state="readonly").pack(side="left", padx=(2, 10))
            ttk.Checkbutton(bar2, text="Save marked pictures", variable=self.saveVar).pack(side="left")

            bar3 = ttk.Frame(root); bar3.pack(fill="x", padx=6, pady=(2, 0))
            ttk.Checkbutton(bar3, text="Find masks only around the centre", variable=self.centreOnlyVar).pack(side="left")
            ttk.Label(bar3, text="   Unticked:  Disc radius").pack(side="left")
            ttk.Entry(bar3, textvariable=self.discVar, width=6).pack(side="left", padx=(2, 10))
            ttk.Label(bar3, text="Prompts").pack(side="left")
            ttk.Combobox(bar3, textvariable=self.promptsVar, values=["Grid points", "YOLO boxes"], width=11,
                         state="readonly").pack(side="left", padx=(2, 10))
            spin(bar3, "Points per side", self.ppsVar, 16, 64, 8, "%.0f", 4)
            spin(bar3, "YOLO confidence", self.confVar, 0.05, 0.95, 0.05, "%.2f")
            bar4 = ttk.Frame(root); bar4.pack(fill="x", padx=6)
            spin(bar4, "Min stability", self.stabilityVar, 0.70, 1.00, 0.02, "%.2f")
            spin(bar4, "Min predicted IoU", self.iouVar, 0.50, 1.00, 0.02, "%.2f")
            spin(bar4, "Box merge IoU", self.nmsVar, 0.30, 1.00, 0.05, "%.2f")
            spin(bar4, "Min area (px)", self.minAreaVar, 0, 50000, 250, "%.0f", 7)
            bar5 = ttk.Frame(root); bar5.pack(fill="x", padx=6)
            ttk.Checkbutton(bar5, text="Refine the largest mask with point prompts", variable=self.refineVar).pack(side="left")
            ttk.Label(bar5, text="  positive rings (x radius)").pack(side="left")
            ttk.Entry(bar5, textvariable=self.refineRingsVar, width=9).pack(side="left", padx=(2, 10))
            spin(bar5, "negative ring (x radius)", self.refineNegVar, 1.02, 2.0, 0.01, "%.2f")
            spin(bar5, "angle step", self.refineStepVar, 5, 90, 5, "%.0f", 4)
            ttk.Checkbutton(bar5, text="with its box", variable=self.refineBoxVar).pack(side="left")
            ttk.Label(root, text="Around the centre: mask 1 cyan, 2 magenta, 3 yellow, points blue.  Unticked: each mask "
                                 "its own colour, disc grey; refined mask white, its positive points blue, negative black."
                      ).pack(fill="x", padx=6)

            self.blank = ImageTk.PhotoImage(Image.new("RGB", (VIEW_W, VIEW_H)))
            self.canvas = tk.Label(root, image=self.blank, background="black"); self.canvas.pack(padx=6)
            self.status = tk.StringVar(master=root, value="Loading MobileSAM ...")
            ttk.Label(root, textvariable=self.status).pack(fill="x", padx=6)
            frame = ttk.Frame(root); frame.pack(fill="both", expand=True, padx=6, pady=4)
            self.listbox = tk.Listbox(frame, height=9, font=("Consolas", 10))
            ys = ttk.Scrollbar(frame, command=self.listbox.yview)
            xs = ttk.Scrollbar(frame, orient="horizontal", command=self.listbox.xview)
            self.listbox.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
            xs.pack(side="bottom", fill="x"); ys.pack(side="right", fill="y")
            self.listbox.pack(side="left", fill="both", expand=True)
            root.protocol("WM_DELETE_WINDOW", self.close)
            threading.Thread(target=self._load, daemon=True).start()
            root.after(200, self._pollLoad)

        def _load(self):                      # background thread: only sets flags (Tk is used on its own thread)
            try:
                self.segmenter.load()
            except Exception as e:
                self.modelError = str(e)
            self.modelReady = True

        def _pollLoad(self):
            if not self.modelReady:
                self.root.after(200, self._pollLoad); return
            self.status.set(f"MobileSAM error: {self.modelError}" if self.modelError
                            else f"MobileSAM ready ({self.segmenter.device}); {self.segmenter.yoloNote}. Choose a folder or "
                                 f"a picture, then Start.")

        def log(self, line):
            print(line, flush=True)
            if getattr(self, "logPath", None) is not None:
                try:
                    with open(self.logPath, "a") as f:
                        f.write(line + "\n")
                except Exception:
                    pass
            self.listbox.insert("end", line); self.listbox.see("end")

        def chooseFolder(self):
            d = filedialog.askdirectory(initialdir=str(self.folder))
            if d:
                self.folder = pathlib.Path(d); self.sourceText.set(d)

        def choosePicture(self):
            f = filedialog.askopenfilename(initialdir=str(self.folder),
                                           filetypes=[("Pictures", "*.jpg *.jpeg *.png *.bmp"), ("All", "*.*")])
            if f:
                self.folder = pathlib.Path(f); self.sourceText.set(f)

        def settings(self):
            return parseCentre(self.centreVar.get()), parseRadii(self.radiiVar.get()), parseStep(self.stepVar.get())

        def gamma(self):
            try:
                return parseGamma(self.gammaVar.get())
            except Exception:
                self.log(f"Gamma '{self.gammaVar.get()}' not 1.0 - 6.0: 1.0 used"); return 1.0

        def whole(self):
            return wholeSettings("yolo" if self.promptsVar.get() == "YOLO boxes" else "grid", self.ppsVar.get(),
                                 self.stabilityVar.get(), self.iouVar.get(), self.nmsVar.get(), self.confVar.get(),
                                 self.discVar.get(), self.minAreaVar.get(), self.refineVar.get(),
                                 self.refineRingsVar.get(), self.refineNegVar.get(), self.refineStepVar.get(),
                                 self.refineBoxVar.get())

        def start(self):
            if not self.modelReady or self.modelError:
                messagebox.showinfo("Hub centre masks", self.status.get()); return
            if self.job is not None:
                return
            try:
                self.settings(); self.whole()
                self.files = picturesIn(self.folder)
            except Exception as e:
                messagebox.showerror("Hub centre masks", str(e)); return
            if not self.files:
                messagebox.showinfo("Hub centre masks", "No pictures there."); return
            self.index, self.running = 0, True
            out = (self.folder if self.folder.is_dir() else self.folder.parent) / "hub_centre_mask_results"
            try:
                out.mkdir(exist_ok=True)
                stamp = time.strftime("%Y%m%d-%H%M%S")
                self.csvPath, self.logPath = out / f"details_{stamp}.csv", out / f"run_{stamp}.txt"
                self.log(f"Details of this run: {self.csvPath}  (all lines: {self.logPath})")
            except Exception as e:
                self.csvPath = self.logPath = None
                self.log(f"Cannot write the details files in {out}: {e}")
            self.startButton.configure(state="disabled"); self.stopButton.configure(state="normal")
            self.step()

        def stop(self):
            self.running = False
            if self.after:
                self.root.after_cancel(self.after); self.after = None
            if self.job is None:
                self.finish(stopped=True)
            else:
                self.status.set(self.status.get() + " - stopping after this picture")

        def close(self):
            self.running = False; self.root.destroy()

        def step(self):
            """Start the next picture in a worker thread; poll() picks up its result."""
            if not self.running:
                return
            if self.index >= len(self.files):
                self.finish(); return
            path = self.files[self.index]
            try:
                centre, radii, stepDeg = self.settings()
            except Exception as e:
                self.log(f"Settings: {e} - using the defaults"); centre, radii, stepDeg = defaultCentre(), [50.0, 60.0], 30.0
            gamma, show, centreOnly = self.gamma(), self.showVar.get(), self.centreOnlyVar.get()
            try:
                whole = self.whole()
            except Exception as e:
                self.log(f"Settings: {e} - using the defaults"); whole = dict(WHOLE_DEFAULTS)
            result = {}

            def work():
                try:
                    if centreOnly:
                        result["out"] = processOne(self.segmenter, path, centre, radii, stepDeg, show, gamma)
                    else:
                        result["out"] = processWhole(self.segmenter, path, centre, gamma, whole, self.csvPath)
                except Exception as e:
                    result["out"] = (None, [f"{path.name}: error {type(e).__name__}: {e}"])

            thread = threading.Thread(target=work, daemon=True)
            self.job = (thread, time.perf_counter(), result, path)
            thread.start()
            self.poll()

        def poll(self):
            thread, started, result, path = self.job
            if thread.is_alive():
                self.status.set(f"{self.index + 1} / {len(self.files)}: {path.name} - "
                                f"{time.perf_counter() - started:.1f} s ...")
                self.root.after(200, self.poll); return
            self.job = None
            marked, lines = result.get("out", (None, [f"{path.name}: no result"]))
            for line in lines:
                self.log(line)
            if marked is not None:
                self.show(marked)
                if self.saveVar.get():
                    out = (self.folder if self.folder.is_dir() else self.folder.parent) / "hub_centre_mask_results"
                    out.mkdir(exist_ok=True)
                    cv2.imwrite(str(out / (path.stem + "_masks.png")), marked)
            self.status.set(f"{self.index + 1} / {len(self.files)}: {path.name} - {time.perf_counter() - started:.1f} s")
            self.index += 1
            if not self.running:
                self.finish(stopped=True); return
            if self.index >= len(self.files):
                self.finish(); return
            try:
                secs = max(1, min(10, int(self.secondsVar.get())))
            except Exception:
                secs = 3
            self.after = self.root.after(secs * 1000, self.step)

        def show(self, marked):
            rgb = cv2.cvtColor(marked, cv2.COLOR_BGR2RGB); h, w = rgb.shape[:2]; s = min(VIEW_W / w, VIEW_H / h)
            self.photo = ImageTk.PhotoImage(Image.fromarray(cv2.resize(rgb, (int(w * s), int(h * s)))))
            self.canvas.configure(image=self.photo)

        def finish(self, stopped=False):
            self.running = False
            self.startButton.configure(state="normal"); self.stopButton.configure(state="disabled")
            self.log(f"{'Stopped' if stopped else 'Done'}: {self.index} of {len(self.files)} picture(s)")

    root = tk.Tk()
    App(root, HubCentreSegmenter(argValue("--sam")))
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(runBatch() if "--batch" in sys.argv else runWindow())
