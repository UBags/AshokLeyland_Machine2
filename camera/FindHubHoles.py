"""
FindHubHoles - the hub's 4 (DOST) / 5 (DOSTPLUS) holes in a picture of the hub with something on it (cap, bung, nut and
washer), for the cap, bung and split pin checks (CHANGES 164). The circle through the holes gives the hub's centre in
that picture.

Three methods (vision.hubholes.method):

  "sam" (findHolesWithSAM, CHANGES 169) - checkHub()'s own steps (camera/HubAndBearingSegmenter.py, not changed) in a
        wide annulus (WIDE_ANNULUS_INNER - WIDE_ANNULUS_OUTER) round this cycle's hub-check centre
        (ImageProcessor.hubCentre: checkHub()'s circle through the hub's holes or bolts), so that both the holes
        (circle r ~148) and the bolts (circle r 178 - 196) are in it: the annulus cut out and normalised
        (preprocess_image), YOLO boxes and grid prompts to MobileSAM, the masks classified, filtered by area ratio and
        shape, grouped by distance from the centre and by size, small groups and groups off the centre dropped, the
        polygon candidates of each group through the equidistant (strict, else relaxed) and equal-angle filters - up to
        there exactly checkHub()'s steps; then, instead of checkHub()'s single pick, the best set of the model's count
        of each group, the CANDIDATES_MAX best of them, and of those whose circle radius is in HOLES_CIRCLE_RADIUS and
        whose total mask area is at most HOLES_AREA_MAX_<model>, the one with the smallest total mask area is the holes.
        The gammas in checkHub()'s smart order, first success wins. No hub-check centre, or no holes at any gamma ->
        not found (the holes are most likely covered).
  "sam_slow" (findHolesWithSAM_Slow, CHANGES 164 - 166) - checkHub()'s steps in a thin annulus round the hub centre
        that keeps the bolts out, the winning group gated (radius, spread, gaps, centre), each hole refined by a
        MobileSAM box prompt; without a hub centre one pass over checkHub()'s own annulus first. Kept, not used.
  "morphology" (findHolesWithMorphology) - the dark-blob method of CHANGES 152 / 157, moved here from
        CheckCap.hubHoles unchanged: the darkest percentile of the band round cap.center, an opening, the model's count
        of blobs most evenly spaced on a circle with radius / spread / angle-gap checks. Its offsets are the dark-blob
        ones of CHANGES 152, 153, 163 - not those in the properties since CHANGES 165.

The MobileSAM predictor and the segmenter object are one per thread (the split pin check runs its pictures in threads);
the YOLO model is shared behind a lock. Results are kept for the last SAM_CACHE_SIZE pictures (the split pin check gets
each picture twice).

findHoles() runs the configured one. Result: dict(found, cx, cy, radius, spread, gap, count, points, reason, method,
...) - "reason" says why the holes were not found, complete (with the gamma / darkest percentile).
"""

import itertools
import threading
import zlib
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from utils.RedisUtils import DOST, DOSTPLUS
from camera.VisionConfig import expectOneOf, vBool, vFloat, vInt, vList, vStr, vTuple


class _LockedYolo:
    """The shared YOLO model, one predict at a time (it is not thread-safe)."""

    def __init__(self, model, lock: threading.Lock):
        self.model, self.lock = model, lock

    def predict(self, *args, **kwargs):
        with self.lock:
            return self.model.predict(*args, **kwargs)


class FindHubHoles:

    METHOD = vStr("vision.hubholes.method", "sam")  # "sam" (wide annulus, CHANGES 169), "sam_slow" (thin annulus, 164 - 166) or "morphology" (dark blobs)
    # SAM: the annulus round the seed (this cycle's hub centre) in which the holes lie and the bolts do not
    SAM_ANNULUS_INNER = vInt("vision.hubholes.sam.annulus.inner", 130)  # px; holes from 140 (DOST) / 133.5 (DOSTPLUS)
    SAM_ANNULUS_OUTER_DOST = vInt("vision.hubholes.sam.annulus.outer.dost", 166)  # holes to 161, stud tops from 169
    SAM_ANNULUS_OUTER_DOSTPLUS = vInt("vision.hubholes.sam.annulus.outer.dostplus", 172)  # holes to 163, stud tops from 183
    SAM_HOLES_RADIUS = vTuple("vision.hubholes.sam.radius", (140.0, 155.0))  # px, circle through the holes found (laptop: 147 - 149; 161 = not the holes)
    SAM_SPREAD_MAX = vFloat("vision.hubholes.sam.spread.max", 3.0)  # px, the holes' distances from that circle (sd)
    SAM_GAP_MAX = vFloat("vision.hubholes.sam.gap.max", 15.0)  # deg, an angle between neighbouring holes off 360 / count
    SAM_SEED_DISTANCE_MAX = vFloat("vision.hubholes.sam.seed.distance.max", 8.0)  # px, the holes' circle centre from the seed
    # checkHub()'s centre from the bolts sits off the holes' centre (the stud tops are higher; CHANGES 96, 147): added to
    # a seed from bolts, per model (holes - bolts; DOST from the CHANGES 147 offsets, DOSTPLUS measured on the laptop,
    # DryRun_Hub_Holes_SAM --seed hubcheck, CHANGES 165)
    SAM_SEED_BOLTS_OFFSET_DOST = vTuple("vision.hubholes.sam.seed.bolts.offset.dost", (-0.7, 4.4))
    SAM_SEED_BOLTS_OFFSET_DOSTPLUS = vTuple("vision.hubholes.sam.seed.bolts.offset.dostplus", (-0.7, 3.7))
    SAM_WIDE_SEED_MAX = vFloat("vision.hubholes.sam.wide.seed.max", 15.0)  # px, a seed from the hub check's annulus from hub.segment.center (parts: within 13)
    SAM_CACHE_SIZE = vInt("vision.hubholes.sam.cache.size", 12)  # pictures whose result is kept
    # Each hole of the group found, refined on the picture as taken (CHANGES 166): MobileSAM box prompt of +-half px round
    # it, three masks, the largest with an area in refine.area taken (the hole's whole outline), centred again on it and
    # prompted once more (iterations). Without it the DOST groups came either as the whole outlines or as their darker
    # lower part - centres 2 - 2.5 px apart in y on one still part; refined, the centre holds within 0.1 - 0.2 px sd.
    SAM_REFINE = vBool("vision.hubholes.sam.refine", True)
    SAM_REFINE_HALF_DOST = vInt("vision.hubholes.sam.refine.half.dost", 18)  # px; holes r ~10
    SAM_REFINE_HALF_DOSTPLUS = vInt("vision.hubholes.sam.refine.half.dostplus", 24)  # px; holes r ~14.5
    SAM_REFINE_AREA_DOST = vTuple("vision.hubholes.sam.refine.area.dost", (60, 1200))  # px
    SAM_REFINE_AREA_DOSTPLUS = vTuple("vision.hubholes.sam.refine.area.dostplus", (60, 2000))  # px
    SAM_REFINE_ITERATIONS = vInt("vision.hubholes.sam.refine.iterations", 2)
    SAM_REFINE_MOVE_MAX = vFloat("vision.hubholes.sam.refine.move.max", 6.0)  # px; more = the group was not the holes

    # "sam" since CHANGES 169 (findHolesWithSAM): a wide annulus round this cycle's hub-check centre that holds the holes
    # (circle r ~148) and the bolts (circle r 178 - 196); checkHub()'s steps up to the candidate sets that pass its
    # equidistant and equal-angle filters; of the model's count, the best set of each group (holes and bolts are separate
    # groups), the CANDIDATES_MAX of least distance variance; of those with a circle radius in HOLES_CIRCLE_RADIUS (holes
    # ~148, bolts 178 and more) and a total mask area <= HOLES_AREA_MAX_<model> (a DOST stud top's mask is larger than a
    # hole's: holes 2100 - 2700, bolts ~4200 in the sandbox; DOSTPLUS holes and stud tops both 3000 - 3500, so there the
    # radius decides), the one with the smallest total mask area is the holes.
    WIDE_ANNULUS_INNER = vInt("vision.hubholes.annulus.inner", 116)  # px round the hub-check centre (checkHub's own 116 - 253)
    WIDE_ANNULUS_OUTER = vInt("vision.hubholes.annulus.outer", 253)
    CANDIDATES_MAX = vInt("vision.hubholes.candidates.max", 3)  # candidate sets kept per gamma (least distance variance first)
    HOLES_AREA_MAX_DOST = vInt("vision.hubholes.holes.area.max.dost", 3400)  # px, total mask area of the 4 holes at most
    HOLES_AREA_MAX_DOSTPLUS = vInt("vision.hubholes.holes.area.max.dostplus", 4500)  # px, total mask area of the 5 holes at most
    HOLES_CIRCLE_RADIUS = vTuple("vision.hubholes.holes.circle.radius", (135.0, 165.0))  # px, circle through the holes
    # Where the wide annulus is centred (CHANGES 182, Uddi 2026-11-21): "picture" = from the picture being checked
    # itself - checkHub()'s steps replayed round hub.segment.center (pass 1: any equally spaced set of the model's count,
    # holes or bolts; its circle's centre, a bolts centre moved by sam.seed.bolts.offset), then the wide annulus round
    # that centre (pass 2) - the cycle's hub-check centre only when pass 1 finds no set; "hubcheck" = this cycle's
    # hub-check centre (ImageProcessor.hubCentre) as before.
    CENTRE_SOURCE = vStr("vision.hubholes.centre.source", "picture")
    BOLTS_CIRCLE_RADIUS = vTuple("vision.hubholes.bolts.circle.radius", (165.0, 210.0))  # px, circle through the bolts' tops (laptop: 178 - 196) - pass 1 only
    # The checks whose holes from the wide annulus are refined (CHANGES 183, Uddi 2026-11-21): each hole of the set found
    # re-segmented on the picture as taken by a MobileSAM box prompt (_refine, the sam.refine.* values of CHANGES 166:
    # the largest of the three masks with an area in refine.area, centred again and prompted once more), then the circle
    # fitted again and gated (radius in HOLES_CIRCLE_RADIUS, spread <= sam.spread.max); a hole without a plausible
    # mask, or moved more than sam.refine.move.max, rejects the set (the next holes set, else the next gamma). Without
    # it the DOST sets came in two outlines - the whole hole (circle r ~149, spread <= 0.5) or its darker lower part
    # (r ~148, spread 0.7 - 1.5), the circle centre ~1.8 px apart in y on parts whose washer did not move (laptop,
    # 2026-11-21, 78 split pin pictures). Only the split pin check for now: the cap and bung offsets were measured on
    # unrefined centres. The caller names its check: findHoles(..., check="splitpin").
    REFINE_CHECKS = vList("vision.hubholes.refine.checks", ["splitpin"])

    # Morphology (CHANGES 152, 157; moved from CheckCap, same keys). The holes (dark, at r ~150) - never the bolts
    # (bright stud tops at r 182 - 196): dark blobs only, searched in the band round cap.center, the circle through them
    # must have a radius in holes.radius, all the holes of the model (DOST 4, DOSTPLUS 5) evenly spaced.
    SEATING_HUB_HOLES_BAND = vTuple("vision.cap.seating.hub.holes.band", (128, 168))  # px from cap.center
    SEATING_HUB_HOLES_DARK_PERCENTILES = vList("vision.cap.seating.hub.holes.dark.percentiles", [6.0, 9.0, 12.0])  # darkest % of the band, tried in turn
    SEATING_HUB_HOLES_AREA = vTuple("vision.cap.seating.hub.holes.area", (150, 2500))  # px, one hole's dark blob
    SEATING_HUB_HOLES_OPEN = vInt("vision.cap.seating.hub.holes.open", 5)  # px; opening that drops thin dark lines
    SEATING_HUB_HOLES_BLOBS_KEEP = vInt("vision.cap.seating.hub.holes.blobs.keep", 8)  # largest blobs tried
    SEATING_HUB_HOLES_RADIUS = vTuple("vision.cap.seating.hub.holes.radius", (140.0, 160.0))  # px, hole circle
    SEATING_HUB_HOLES_SPREAD_MAX = vFloat("vision.cap.seating.hub.holes.spread.max", 3.0)  # px, holes off the circle (sd)
    SEATING_HUB_HOLES_GAP_MAX = vFloat("vision.cap.seating.hub.holes.gap.max", 15.0)  # deg, a gap off 360 / count
    # the model's hole count (both methods)
    SEATING_HUB_HOLES_COUNT_DOST = vInt("vision.cap.seating.hub.holes.count.dost", 4)
    SEATING_HUB_HOLES_COUNT_DOSTPLUS = vInt("vision.cap.seating.hub.holes.count.dostplus", 5)
    # True: the radius / spread / angle-gap checks are applied to every combination of blobs and the most evenly spaced
    # combination that passes them is taken; False: the most evenly spaced combination first, then checked. CHANGES 157.
    SEATING_HUB_HOLES_GATE_EACH_COMBINATION = vBool("vision.cap.seating.hub.holes.gate.each.combination", True)

    # This cycle's hub centre (dict cx, cy, ... from checkHub(); ImageProcessor.hubCentre) when the caller does not
    # pass one: set by the camera server. samModelProvider / yoloModelProvider replace ModelManager's models (tests).
    hubCentreProvider = None
    # dry runs only (no hub check in their cycle): callable(picture, model) -> hub centre dict, e.g. the real checkHub()
    # on that picture. None in production.
    hubCentreFromPicture = None
    samModelProvider = None
    yoloModelProvider = None

    _local = threading.local()
    _yoloLock = threading.Lock()
    _sharedYolo = None
    _cache: "OrderedDict" = OrderedDict()
    _cacheLock = threading.Lock()
    _keyLocks: Dict = {}

    # ------------------------------------------------------------------ either method

    @staticmethod
    def holeCount(model: Optional[str]) -> int:
        return int(FindHubHoles.SEATING_HUB_HOLES_COUNT_DOSTPLUS if model == DOSTPLUS
                   else FindHubHoles.SEATING_HUB_HOLES_COUNT_DOST)

    @staticmethod
    def findHoles(anImage: np.ndarray, model: Optional[str], hubCentre: Optional[Dict] = None,
                  check: Optional[str] = None) -> Dict:
        """The hub's holes by the configured method (vision.hubholes.method). hubCentre: this cycle's hub centre from
        checkHub() (the SAM method's seed); None = FindHubHoles.hubCentreProvider(), if set. check: the calling check
        ("splitpin", "cap", "bung"); its holes are refined when it is in REFINE_CHECKS (sam method only). A not-found
        result's reason names the gamma / darkest percentile."""
        expectOneOf("vision.hubholes.method", FindHubHoles.METHOD, ("sam", "sam_slow", "morphology"))
        method = str(FindHubHoles.METHOD).strip().lower()
        if method == "sam_slow":
            return FindHubHoles.findHolesWithSAM_Slow(anImage, model, hubCentre)
        if method == "morphology":
            result = FindHubHoles.findHolesWithMorphology(anImage, model)
            result["method"] = "morphology"
            if not result["found"]:
                result["reason"] = f"{result.get('reason')}; darkest {result.get('percentile')} %"
            return result
        return FindHubHoles.findHolesWithSAM(anImage, model, hubCentre, refine=FindHubHoles.refineFor(check))

    @staticmethod
    def refineFor(check: Optional[str]) -> bool:
        """True when check's holes are to be refined (REFINE_CHECKS)."""
        if not check:
            return False
        return str(check).strip().lower() in [str(c).strip().lower() for c in (FindHubHoles.REFINE_CHECKS or [])]

    @staticmethod
    def describe(result: Dict) -> str:
        """'hub from 4 holes (x, y) r .., spread .., gap .. deg (sam, gamma 2, seed ...)' - for the log lines."""
        if not result.get("found"):
            return f"the hub's {result.get('count')} holes not found ({result.get('reason')})"
        how = result.get("method", "")
        if how in ("sam", "sam_slow"):
            how = f"{how}, gamma {result.get('gamma')}, " + (f"mask area {result['area']:.0f}, " if "area" in result else "") \
                  + (f"refined (moved {result['refineMoved']:.1f} px; unrefined ({result['unrefined'][0]:.1f}, "
                     f"{result['unrefined'][1]:.1f}) r {result['unrefined'][2]:.1f}), " if result.get("refined") else "") \
                  + f"seed {result.get('seedFrom')}"
        return (f"hub from {result['count']} holes ({result['cx']:.1f}, {result['cy']:.1f}) r {result['radius']:.1f}, "
                f"spread {result['spread']:.1f}, gap {result['gap']:.0f} deg ({how})")

    @staticmethod
    def _circleOf(points) -> Tuple[float, float, float, float, float]:
        """(cx, cy, radius, spread, gap) of the least-squares circle through the points."""
        p = np.asarray(points, dtype=np.float64)
        n = len(p)
        sol = np.linalg.lstsq(np.c_[2 * p, np.ones(n)], (p ** 2).sum(1), rcond=None)[0]
        cx, cy = float(sol[0]), float(sol[1])
        radius = float(np.sqrt(max(sol[2] + cx * cx + cy * cy, 0.0)))
        q = p - (cx, cy)
        spread = float(np.hypot(q[:, 0], q[:, 1]).std())
        angles = np.sort(np.degrees(np.arctan2(q[:, 1], q[:, 0])))
        gap = float(np.abs(np.diff(np.r_[angles, angles[0] + 360.0]) - 360.0 / n).max())
        return cx, cy, radius, spread, gap

    # ------------------------------------------------------------------ YOLO + MobileSAM

    @staticmethod
    def _seed(hubCentre: Optional[Dict], model: str = DOST):
        """(cx, cy, 'from ...') of this cycle's hub centre (moved by SAM_SEED_BOLTS_OFFSET_<model> when it came from the
        bolts), or None."""
        centre = hubCentre
        if centre is None and FindHubHoles.hubCentreProvider is not None:
            try:
                centre = FindHubHoles.hubCentreProvider()
            except Exception:
                centre = None
        try:
            if centre and np.isfinite(float(centre["cx"])) and np.isfinite(float(centre["cy"])):
                dx, dy = 0.0, 0.0
                if centre.get("kind") == "bolts":
                    dx, dy = (float(v) for v in (FindHubHoles.SAM_SEED_BOLTS_OFFSET_DOSTPLUS if model == DOSTPLUS
                                                 else FindHubHoles.SAM_SEED_BOLTS_OFFSET_DOST))
                return (float(centre["cx"]) + dx, float(centre["cy"]) + dy,
                        f"the hub centre given ({centre.get('count', '?')} {centre.get('kind', '?')}"
                        + (f", + bolts offset ({dx:g}, {dy:g})" if (dx, dy) != (0.0, 0.0) else "") + ")")
        except (KeyError, TypeError, ValueError):
            pass
        return None

    @staticmethod
    def _engine():
        """This thread's segmenter object: HubAndBearingSegmenter's methods (checkHub()'s steps) on its own MobileSAM
        predictor and mask generator, with the shared YOLO behind a lock; not checkHub()'s own instance."""
        seg = getattr(FindHubHoles._local, "segmenter", None)
        if seg is not None:
            return seg
        import camera.HubAndBearingSegmenter as H
        from mobile_sam import SamPredictor
        if FindHubHoles.samModelProvider is not None:
            samModel = FindHubHoles.samModelProvider()
        else:
            from camera.ModelManager import ModelManager
            samModel = ModelManager.get_instance().get_sam_model()
        with FindHubHoles._yoloLock:
            if FindHubHoles._sharedYolo is None:
                if FindHubHoles.yoloModelProvider is not None:
                    yolo = FindHubHoles.yoloModelProvider()
                else:
                    from camera.ModelManager import ModelManager
                    yolo = ModelManager.get_instance().get_yolo_model()
                FindHubHoles._sharedYolo = _LockedYolo(yolo, threading.Lock())
        seg = object.__new__(H.HubAndBearingSegmenter)
        seg.sam_predictor = SamPredictor(samModel)
        seg.yolo_model = FindHubHoles._sharedYolo
        seg.conf_thresh = H.HUB_YOLO_CONF_THRESHOLD
        seg.iou_thresh = 0.7
        seg.min_area = H.HUB_MIN_MASK_AREA
        seg.max_area = H.HUB_MAX_MASK_AREA
        seg.use_grid = True
        seg.mask_generator = H.MobileSAMv2AutomaticMaskGenerator(
            sam_predictor=seg.sam_predictor, yolo_model=seg.yolo_model, conf_threshold=seg.conf_thresh,
            iou_threshold=seg.iou_thresh, min_mask_region_area=seg.min_area, max_mask_region_area=seg.max_area,
            use_grid_points=seg.use_grid, points_per_side=32)
        seg.annular_center = (int(H.HUB_SEGMENT_CENTER_X), int(H.HUB_SEGMENT_CENTER_Y))
        seg.annular_outer_radius = int(H.HUB_SEGMENT_OUTER_RADIUS)
        seg.annular_inner_radius = int(H.HUB_SEGMENT_INNER_RADIUS)
        seg._last_masks = seg._last_groups = seg._winning_group_id = None
        seg._winning_count, seg._last_detected_model, seg._last_is_match = 0, None, True
        FindHubHoles._local.segmenter = seg
        return seg

    @staticmethod
    def _refine(rgb: np.ndarray, points, model: str):
        """Each hole centre refined on the picture as taken (see SAM_REFINE); None if a hole gives no plausible mask."""
        import torch
        seg = FindHubHoles._engine()
        plus = model == DOSTPLUS
        half = int(FindHubHoles.SAM_REFINE_HALF_DOSTPLUS if plus else FindHubHoles.SAM_REFINE_HALF_DOST)
        low, high = (float(v) for v in (FindHubHoles.SAM_REFINE_AREA_DOSTPLUS if plus else FindHubHoles.SAM_REFINE_AREA_DOST))
        p = np.asarray(points, dtype=np.float64)
        reach = int(np.hypot(*(p - p.mean(axis=0)).T).max()) + 2 * half + 10
        h, w = rgb.shape[:2]
        cx, cy = (int(round(v)) for v in p.mean(axis=0))
        x0, y0 = max(0, cx - reach), max(0, cy - reach)
        crop = np.ascontiguousarray(rgb[y0:min(h, cy + reach), x0:min(w, cx + reach)])
        with torch.inference_mode():
            seg.sam_predictor.set_image(crop)
            refined = []
            for x, y in p:
                bx, by = x - x0, y - y0
                for _ in range(max(1, int(FindHubHoles.SAM_REFINE_ITERATIONS))):
                    masks, _, _ = seg.sam_predictor.predict(box=np.array([bx - half, by - half, bx + half, by + half]),
                                                            multimask_output=True)
                    best = None
                    for mask in masks:
                        area = float(mask.sum())
                        if low < area < high and (best is None or area > best[0]):
                            ys, xs = np.nonzero(mask)
                            best = (area, float(xs.mean()), float(ys.mean()))
                    if best is None:
                        return None
                    _, bx, by = best
                refined.append((bx + x0, by + y0))
        return refined

    @staticmethod
    def _failedGates(centre, cx: float, cy: float, radius: float, spread: float, gap: float, gates: bool,
                     maxOff: Optional[float]) -> List[str]:
        """The gates a hole circle fails: its centre within maxOff (default SAM_SEED_DISTANCE_MAX) of centre and, with
        gates, its radius, spread and angle gaps within their limits."""
        limit = float(FindHubHoles.SAM_SEED_DISTANCE_MAX if maxOff is None else maxOff)
        off = float(np.hypot(cx - float(centre[0]), cy - float(centre[1])))
        low, high = float(FindHubHoles.SAM_HOLES_RADIUS[0]), float(FindHubHoles.SAM_HOLES_RADIUS[1])
        return [text for failed, text in (
            (off > limit, f"centre {off:.1f} px from ({float(centre[0]):.0f}, {float(centre[1]):.0f}) > {limit:g}"),
            (gates and not low <= radius <= high, f"circle r {radius:.1f} outside {low:g} - {high:g}"),
            (gates and spread > float(FindHubHoles.SAM_SPREAD_MAX), f"spread {spread:.1f} > {FindHubHoles.SAM_SPREAD_MAX}"),
            (gates and gap > float(FindHubHoles.SAM_GAP_MAX), f"angle gap off by {gap:.0f} > {FindHubHoles.SAM_GAP_MAX} deg"))
            if failed]

    @staticmethod
    def _samPass(rgb: np.ndarray, model: str, centre, inner: int, outer: int, gates: bool = True,
                 maxOff: Optional[float] = None) -> Dict:
        """checkHub()'s steps on the annulus inner - outer round centre; the first gamma whose winning group has the
        model's count, a circle centred within maxOff of centre (default SAM_SEED_DISTANCE_MAX) and, with gates, a
        radius, spread and angle gaps within their limits wins."""
        from camera.CheckHubAndBottomBearing import CheckHubAndBottomBearing, determine_smart_gamma_order
        from camera.HubAndBearingSegmenter import select_best_group
        n = FindHubHoles.holeCount(model)
        seg = FindHubHoles._engine()
        seg.annular_center = (int(round(centre[0])), int(round(centre[1])))
        seg.annular_inner_radius, seg.annular_outer_radius = int(inner), int(outer)
        x0 = max(0, seg.annular_center[0] - seg.annular_outer_radius)
        y0 = max(0, seg.annular_center[1] - seg.annular_outer_radius)
        tries = []
        for gamma in determine_smart_gamma_order(rgb):
            preprocessed, _ = seg.preprocess_image(rgb, gamma=gamma, bg_sx=10, bg_sy=10, cn_sx=10, cn_sy=10,
                                                   apply_bilateral=True, alter_gamma=True)
            count, groups, detected, isMatch = seg.segment_holes(preprocessed, model_type=model)
            if count <= 0 or not groups:
                tries.append(f"gamma {gamma}: no group")
                continue
            winning = select_best_group(groups)
            if not isMatch:
                tries.append(f"gamma {gamma}: a {detected} group of {len(winning or [])}")
                continue
            if not winning or len(winning) != n:
                tries.append(f"gamma {gamma}: {len(winning or [])} of {n}")
                continue
            circle = CheckHubAndBottomBearing.hubCentreFromGroup(winning, seg.annular_center, seg.annular_outer_radius)
            if circle is None:
                tries.append(f"gamma {gamma}: no circle")
                continue
            points = [(float(c[0]) + x0, float(c[1]) + y0) for _, c in winning if c is not None]
            cx, cy, radius, spread, gap = FindHubHoles._circleOf(points)
            bad = FindHubHoles._failedGates(centre, cx, cy, radius, spread, gap, gates, maxOff)
            if not bad and gates and FindHubHoles.SAM_REFINE:   # only a group that passed is refined, then gated again
                refined = FindHubHoles._refine(rgb, points, model)
                if refined is None:
                    bad = ["a hole gave no outline mask when refined"]
                else:
                    moved = max(float(np.hypot(a[0] - b[0], a[1] - b[1])) for a, b in zip(points, refined))
                    if moved > float(FindHubHoles.SAM_REFINE_MOVE_MAX):
                        bad = [f"a hole moved {moved:.1f} px when refined > {FindHubHoles.SAM_REFINE_MOVE_MAX}"]
                    else:
                        points = refined
                        cx, cy, radius, spread, gap = FindHubHoles._circleOf(points)
                        bad = [f"refined: {text}" for text in
                               FindHubHoles._failedGates(centre, cx, cy, radius, spread, gap, gates, maxOff)]
            if bad:
                tries.append(f"gamma {gamma}: " + ", ".join(bad))
                continue
            return dict(found=True, cx=cx, cy=cy, radius=radius, spread=spread, gap=gap, count=n, points=points,
                        gamma=gamma, kind=circle.get("kind"), reason="")
        return dict(found=False, count=n, gamma=None, reason="; ".join(tries) or "no gamma tried")

    @staticmethod
    def findHolesWithSAM_Slow(anImage: np.ndarray, model: Optional[str], hubCentre: Optional[Dict] = None) -> Dict:
        """The holes by YOLO + MobileSAM in the thin annulus round this cycle's hub centre, each hole refined (CHANGES
        164 - 166; findHolesWithSAM until CHANGES 169; vision.hubholes.method=sam_slow)."""
        model = DOSTPLUS if model == DOSTPLUS else DOST
        n = FindHubHoles.holeCount(model)
        if anImage is None:
            return dict(found=False, count=n, method="sam", reason="no picture")
        seed = FindHubHoles._seed(hubCentre, model)
        small = np.ascontiguousarray(anImage[::4, ::4])
        key = ("slow", zlib.crc32(small.tobytes()), anImage.shape, model,
               None if seed is None else (round(seed[0], 1), round(seed[1], 1)))
        with FindHubHoles._cacheLock:
            if key in FindHubHoles._cache:
                FindHubHoles._cache.move_to_end(key)
                return dict(FindHubHoles._cache[key])
            keyLock = FindHubHoles._keyLocks.setdefault(key, threading.Lock())
        with keyLock:                                   # the same picture in two threads: computed once
            with FindHubHoles._cacheLock:
                if key in FindHubHoles._cache:
                    return dict(FindHubHoles._cache[key])
            result = FindHubHoles._findWithSAM(anImage, model, seed)
            with FindHubHoles._cacheLock:
                FindHubHoles._cache[key] = dict(result)
                while len(FindHubHoles._cache) > max(1, int(FindHubHoles.SAM_CACHE_SIZE)):
                    FindHubHoles._cache.popitem(last=False)
                FindHubHoles._keyLocks.pop(key, None)
        return dict(result)

    @staticmethod
    def _findWithSAM(anImage: np.ndarray, model: str, seed) -> Dict:
        import camera.HubAndBearingSegmenter as H
        n = FindHubHoles.holeCount(model)
        rgb = cv2.cvtColor(anImage, cv2.COLOR_BGR2RGB)
        try:
            if seed is None:
                wide = FindHubHoles._samPass(rgb, model, (H.HUB_SEGMENT_CENTER_X, H.HUB_SEGMENT_CENTER_Y),
                                             int(H.HUB_SEGMENT_INNER_RADIUS), int(H.HUB_SEGMENT_OUTER_RADIUS), gates=False,
                                             maxOff=float(FindHubHoles.SAM_WIDE_SEED_MAX))
                if not wide["found"]:
                    return dict(found=False, count=n, method="sam_slow", seedFrom=None,
                                reason=f"no hub centre from this cycle's hub check, and none in the hub check's annulus "
                                       f"({wide['reason']})")
                moved = FindHubHoles._seed(dict(cx=wide["cx"], cy=wide["cy"], count=wide["count"],
                                                kind=wide.get("kind")), model)
                dx, dy = moved[0] - wide["cx"], moved[1] - wide["cy"]
                seed = (moved[0], moved[1], f"the hub check's annulus ({wide['count']} {wide.get('kind')}, gamma "
                                            f"{wide['gamma']}" + (f", + bolts offset ({dx:.1f}, {dy:.1f})"
                                                                   if (dx, dy) != (0.0, 0.0) else "") + ")")
            outer = FindHubHoles.SAM_ANNULUS_OUTER_DOSTPLUS if model == DOSTPLUS else FindHubHoles.SAM_ANNULUS_OUTER_DOST
            result = FindHubHoles._samPass(rgb, model, seed[:2], int(FindHubHoles.SAM_ANNULUS_INNER), int(outer))
        except Exception as e:
            return dict(found=False, count=n, method="sam_slow", seedFrom=None if seed is None else seed[2],
                        reason=f"YOLO + MobileSAM failed: {e}")
        result.update(method="sam_slow", seed=(round(seed[0], 1), round(seed[1], 1)), seedFrom=seed[2])
        if not result["found"]:
            result["reason"] = (f"annulus {int(FindHubHoles.SAM_ANNULUS_INNER)} - {int(outer)} px round "
                                f"({seed[0]:.1f}, {seed[1]:.1f}) from {seed[2]}: {result['reason']}")
        return result

    @staticmethod
    def findHolesWithSAM(anImage: np.ndarray, model: Optional[str], hubCentre: Optional[Dict] = None,
                         refine: bool = False) -> Dict:
        """The hub's holes by checkHub()'s steps in a wide annulus round this cycle's hub-check centre (CHANGES 169):
          1. the centre: hubCentre (ImageProcessor.hubCentre from checkHub(), passed by the cap check), else
             hubCentreProvider() (the camera server's ImageProcessor.hubCentre), else hubCentreFromPicture (dry runs);
             none -> not found;
          2. the annulus WIDE_ANNULUS_INNER - WIDE_ANNULUS_OUTER round it (holes and bolts), per gamma in checkHub()'s
             smart order: checkHub()'s steps up to the polygon candidates that pass its equidistant (strict, else
             relaxed) and equal-angle filters (_candidateSets);
          3. of those with the model's count, the best (least distance variance) of each group, the CANDIDATES_MAX best of
             them; of those whose circle radius is in HOLES_CIRCLE_RADIUS and whose total mask area is <=
             HOLES_AREA_MAX_<model>, the one with the smallest total mask area is the holes; none -> the next gamma. No
             gamma -> not found (the holes are most likely covered);
          4. with refine (REFINE_CHECKS, CHANGES 183): each hole of that set re-segmented by a box prompt and the circle
             fitted again (_refinedSet); a set that fails it gives way to the next holes set, else the next gamma.
        Result as findHoles(), with gamma, area (the holes' total mask area), candidates (area, radius, count, centre of
        each set looked at), seed, seedFrom."""
        model = DOSTPLUS if model == DOSTPLUS else DOST
        n = FindHubHoles.holeCount(model)
        if anImage is None:
            return dict(found=False, count=n, method="sam", reason="no picture")
        centre, centreFrom = FindHubHoles._hubCheckCentre(anImage, model, hubCentre)
        if centre is None:
            return dict(found=False, count=n, method="sam", seedFrom=None,
                        reason="no hub centre from this cycle's hub check")
        small = np.ascontiguousarray(anImage[::4, ::4])
        key = ("wide", zlib.crc32(small.tobytes()), anImage.shape, model, (round(centre[0], 1), round(centre[1], 1)),
               bool(refine))
        with FindHubHoles._cacheLock:
            if key in FindHubHoles._cache:
                FindHubHoles._cache.move_to_end(key)
                return dict(FindHubHoles._cache[key])
            keyLock = FindHubHoles._keyLocks.setdefault(key, threading.Lock())
        with keyLock:                                   # the same picture in two threads: computed once
            with FindHubHoles._cacheLock:
                if key in FindHubHoles._cache:
                    return dict(FindHubHoles._cache[key])
            try:
                result = FindHubHoles._holesInWideAnnulus(cv2.cvtColor(anImage, cv2.COLOR_BGR2RGB), model, centre,
                                                          refine=bool(refine))
            except Exception as e:
                result = dict(found=False, count=n, reason=f"YOLO + MobileSAM failed: {e}")
            result.update(method="sam", seed=(round(centre[0], 1), round(centre[1], 1)), seedFrom=centreFrom)
            if not result["found"]:
                result["reason"] = (f"annulus {int(FindHubHoles.WIDE_ANNULUS_INNER)} - {int(FindHubHoles.WIDE_ANNULUS_OUTER)}"
                                    f" px round ({centre[0]:.1f}, {centre[1]:.1f}) from {centreFrom}: {result['reason']}")
            with FindHubHoles._cacheLock:
                FindHubHoles._cache[key] = dict(result)
                while len(FindHubHoles._cache) > max(1, int(FindHubHoles.SAM_CACHE_SIZE)):
                    FindHubHoles._cache.popitem(last=False)
                FindHubHoles._keyLocks.pop(key, None)
        return dict(result)

    @staticmethod
    def _hubCheckCentre(anImage: np.ndarray, model: str, hubCentre: Optional[Dict]):
        """((cx, cy), 'from ...'): with CENTRE_SOURCE "picture" the centre found in this picture (_centreFromPicture),
        else - and when that finds none - this cycle's hub-check centre as it is (holes or bolts); (None, None)."""
        expectOneOf("vision.hubholes.centre.source", FindHubHoles.CENTRE_SOURCE, ("picture", "hubcheck"))
        if str(FindHubHoles.CENTRE_SOURCE).strip().lower() == "picture":
            own = FindHubHoles._centreFromPicture(anImage, model)
            if own.get("found"):
                return ((own["cx"], own["cy"]), f"this picture: {own['count']} {own['kind']} (r {own['radius']:.1f}, "
                                                f"spread {own['spread']:.1f}, gamma {own['gamma']})")
            fallback = FindHubHoles._hubCheckCentreGiven(anImage, model, hubCentre)
            if fallback[0] is not None:
                return fallback[0], f"{fallback[1]} (this picture: {own.get('reason', 'no set')})"
            return None, None
        return FindHubHoles._hubCheckCentreGiven(anImage, model, hubCentre)

    @staticmethod
    def _centreFromPicture(anImage: np.ndarray, model: str) -> Dict:
        """Pass 1 of CENTRE_SOURCE "picture": checkHub()'s steps (_candidateSets, on this thread's own segmenter object)
        in the wide annulus round hub.segment.center (where checkHub() itself looks; moved by the knuckle frame), per
        gamma in checkHub()'s order until a gamma gives sets: of the model's count, the best set of each group; a set
        that passes the holes' rule (circle r in HOLES_CIRCLE_RADIUS, total mask area <= HOLES_AREA_MAX_<model>; the
        smallest area) gives the centre directly, else the bolts' set (circle r in BOLTS_CIRCLE_RADIUS, the smallest
        distance variance) with sam.seed.bolts.offset added (holes - bolts). A part off hub.segment.center by more than
        a few px splits the holes' distances from it over checkHub()'s 15 % groups, the bolts' less: the bolts then give
        the centre and pass 2 finds the holes round it. dict(found, cx, cy, radius, spread, kind, count, gamma, reason);
        kept per picture (cache)."""
        n = FindHubHoles.holeCount(model)
        if anImage is None:
            return dict(found=False, reason="no picture")
        import camera.HubAndBearingSegmenter as H
        centre = (float(H.HUB_SEGMENT_CENTER_X), float(H.HUB_SEGMENT_CENTER_Y))
        small = np.ascontiguousarray(anImage[::4, ::4])
        key = ("centre", zlib.crc32(small.tobytes()), anImage.shape, model, centre)
        with FindHubHoles._cacheLock:
            if key in FindHubHoles._cache:
                FindHubHoles._cache.move_to_end(key)
                return dict(FindHubHoles._cache[key])
            keyLock = FindHubHoles._keyLocks.setdefault(key, threading.Lock())
        with keyLock:
            with FindHubHoles._cacheLock:
                if key in FindHubHoles._cache:
                    return dict(FindHubHoles._cache[key])
            try:
                result = FindHubHoles._centreInWideAnnulus(cv2.cvtColor(anImage, cv2.COLOR_BGR2RGB), model, centre, n)
            except Exception as e:
                result = dict(found=False, reason=f"YOLO + MobileSAM failed: {e}")
            with FindHubHoles._cacheLock:
                FindHubHoles._cache[key] = dict(result)
                while len(FindHubHoles._cache) > max(1, int(FindHubHoles.SAM_CACHE_SIZE)):
                    FindHubHoles._cache.popitem(last=False)
                FindHubHoles._keyLocks.pop(key, None)
        return dict(result)

    @staticmethod
    def _centreInWideAnnulus(rgb: np.ndarray, model: str, centre, n: int) -> Dict:
        from camera.CheckHubAndBottomBearing import determine_smart_gamma_order
        seg = FindHubHoles._engine()
        seg.annular_center = (int(round(centre[0])), int(round(centre[1])))
        seg.annular_inner_radius = int(FindHubHoles.WIDE_ANNULUS_INNER)
        seg.annular_outer_radius = int(FindHubHoles.WIDE_ANNULUS_OUTER)
        x0 = max(0, seg.annular_center[0] - seg.annular_outer_radius)
        y0 = max(0, seg.annular_center[1] - seg.annular_outer_radius)
        areaMax = float(FindHubHoles.HOLES_AREA_MAX_DOSTPLUS if model == DOSTPLUS else FindHubHoles.HOLES_AREA_MAX_DOST)
        hLow, hHigh = (float(v) for v in FindHubHoles.HOLES_CIRCLE_RADIUS)
        bLow, bHigh = (float(v) for v in FindHubHoles.BOLTS_CIRCLE_RADIUS)
        dx, dy = (float(v) for v in (FindHubHoles.SAM_SEED_BOLTS_OFFSET_DOSTPLUS if model == DOSTPLUS
                                     else FindHubHoles.SAM_SEED_BOLTS_OFFSET_DOST))
        tries = []
        for gamma in determine_smart_gamma_order(rgb):
            preprocessed, _ = seg.preprocess_image(rgb, gamma=gamma, bg_sx=10, bg_sy=10, cn_sx=10, cn_sy=10,
                                                   apply_bilateral=True, alter_gamma=True)
            candidates, masks, others = FindHubHoles._candidateSets(seg, preprocessed, n)
            if not candidates:
                tries.append(f"gamma {gamma}: no set of {n}")
                continue
            bestPerGroup = {}
            for c in candidates:
                if c['group_id'] not in bestPerGroup or c['distance_variance'] < bestPerGroup[c['group_id']]['distance_variance']:
                    bestPerGroup[c['group_id']] = c
            sets = []
            for c in sorted(bestPerGroup.values(), key=lambda c: c['distance_variance'])[:max(1, int(FindHubHoles.CANDIDATES_MAX))]:
                points = [(float(masks[i]['shape_info']['center'][0]) + x0,
                           float(masks[i]['shape_info']['center'][1]) + y0) for i in c['indices']]
                area = float(sum(float(masks[i]['area']) for i in c['indices']))
                cx, cy, radius, spread, gap = FindHubHoles._circleOf(points)
                sets.append(dict(area=area, cx=cx, cy=cy, radius=radius, spread=spread, gap=gap))
            holes = sorted([s for s in sets if hLow <= s['radius'] <= hHigh and s['area'] <= areaMax], key=lambda s: s['area'])
            if holes:
                h = holes[0]
                return dict(found=True, cx=h['cx'], cy=h['cy'], radius=h['radius'], spread=h['spread'], kind="holes",
                            count=n, gamma=gamma, reason="")
            bolts = [s for s in sets if bLow <= s['radius'] <= bHigh]
            if bolts:
                b = bolts[0]                            # sets are in order of distance variance
                return dict(found=True, cx=b['cx'] + dx, cy=b['cy'] + dy, radius=b['radius'], spread=b['spread'],
                            kind="bolts", count=n, gamma=gamma, reason="")
            tries.append(f"gamma {gamma}: sets (area, r) " + ", ".join(f"({s['area']:.0f}, {s['radius']:.1f})" for s in sets)
                         + " - neither holes nor bolts")
        return dict(found=False, reason="; ".join(tries) or "no gamma tried")

    @staticmethod
    def _hubCheckCentreGiven(anImage: np.ndarray, model: str, hubCentre: Optional[Dict]):
        """((cx, cy), 'from ...') of this cycle's hub-check centre as it is (holes or bolts), or (None, None)."""
        tried = []
        if hubCentre is not None:
            tried.append((hubCentre, "the hub check (given)"))
        elif FindHubHoles.hubCentreProvider is not None:
            try:
                tried.append((FindHubHoles.hubCentreProvider(), "the hub check (this cycle)"))
            except Exception:
                pass
        if not any(c for c, _ in tried) and FindHubHoles.hubCentreFromPicture is not None:
            try:
                tried.append((FindHubHoles.hubCentreFromPicture(anImage, model), "the hub check on this picture"))
            except Exception:
                pass
        for centre, source in tried:
            try:
                if centre and np.isfinite(float(centre["cx"])) and np.isfinite(float(centre["cy"])):
                    return ((float(centre["cx"]), float(centre["cy"])),
                            f"{source}: {centre.get('count', '?')} {centre.get('kind', '?')}")
            except (KeyError, TypeError, ValueError):
                continue
        return None, None

    @staticmethod
    def _candidateSets(seg, preprocessed: np.ndarray, n: int):
        """checkHub()'s steps (HubAndBearingSegmenter.segment_holes, steps 1 - 7, and filter_subgroups_by_equispacing
        steps 1 - 3) on one preprocessed annulus, without the final pick: (candidates of K = n that pass the equidistant
        and equal-angle filters, masks, count of other-K candidates that passed)."""
        import torch
        import camera.HubAndBearingSegmenter as H
        rgb = cv2.cvtColor(preprocessed, cv2.COLOR_GRAY2RGB) if preprocessed.ndim == 2 else preprocessed
        with torch.inference_mode():
            masks = seg.mask_generator.generate(rgb)
        if not masks:
            return [], [], 0
        for mask_data in masks:
            mask_data['shape_info'] = seg._classify_mask_shape(mask_data['segmentation'])
        masks = H.filter_masks_by_area_ratio(masks)
        if not masks:
            return [], [], 0
        masks = H.filter_valid_shapes_only(masks)
        if not masks:
            return [], [], 0
        groups = seg._subgroup_by_size(masks, seg._group_by_distance_from_center(masks))
        cx = cy = seg.annular_outer_radius
        groups = H.filter_small_groups(groups, min_size=3)
        groups = H.filter_subgroups_by_center_envelope(masks, groups, cx, cy)
        passed = []
        for group_id, indices in groups.items():
            if len(indices) < 4:
                continue
            candidates = H._generate_all_polygon_candidates(masks, indices, cx, cy, K=4)
            if len(indices) >= 5:
                candidates += H._generate_all_polygon_candidates(masks, indices, cx, cy, K=5)
            for c in candidates:
                c['group_id'] = group_id
            equal = H._filter_by_equidistant(candidates, H.EQUISPACING_STRICT_TOLERANCE)
            if not equal:
                equal = H._filter_by_equidistant(candidates, H.EQUISPACING_RELAXED_TOLERANCE)
            passed += H._filter_by_equal_angles(equal, H.HUB_EQUAL_ANGLE_TOLERANCE)
        ours = [c for c in passed if c['K'] == n]
        return ours, masks, len(passed) - len(ours)

    @staticmethod
    def _holesInWideAnnulus(rgb: np.ndarray, model: str, centre, refine: bool = False) -> Dict:
        from camera.CheckHubAndBottomBearing import determine_smart_gamma_order
        n = FindHubHoles.holeCount(model)
        seg = FindHubHoles._engine()
        seg.annular_center = (int(round(centre[0])), int(round(centre[1])))
        seg.annular_inner_radius = int(FindHubHoles.WIDE_ANNULUS_INNER)
        seg.annular_outer_radius = int(FindHubHoles.WIDE_ANNULUS_OUTER)
        x0 = max(0, seg.annular_center[0] - seg.annular_outer_radius)
        y0 = max(0, seg.annular_center[1] - seg.annular_outer_radius)
        areaMax = float(FindHubHoles.HOLES_AREA_MAX_DOSTPLUS if model == DOSTPLUS else FindHubHoles.HOLES_AREA_MAX_DOST)
        low, high = (float(v) for v in FindHubHoles.HOLES_CIRCLE_RADIUS)
        tries, looked = [], []
        for gamma in determine_smart_gamma_order(rgb):
            preprocessed, _ = seg.preprocess_image(rgb, gamma=gamma, bg_sx=10, bg_sy=10, cn_sx=10, cn_sy=10,
                                                   apply_bilateral=True, alter_gamma=True)
            candidates, masks, others = FindHubHoles._candidateSets(seg, preprocessed, n)
            if not candidates:
                tries.append(f"gamma {gamma}: no set of {n}" + (f" ({others} of the other count)" if others else ""))
                continue
            bestPerGroup = {}                           # one set per distance / size group (holes and bolts are apart)
            for c in candidates:
                if c['group_id'] not in bestPerGroup or c['distance_variance'] < bestPerGroup[c['group_id']]['distance_variance']:
                    bestPerGroup[c['group_id']] = c
            sets = []
            for c in sorted(bestPerGroup.values(), key=lambda c: c['distance_variance'])[:max(1, int(FindHubHoles.CANDIDATES_MAX))]:
                points = [(float(masks[i]['shape_info']['center'][0]) + x0,
                           float(masks[i]['shape_info']['center'][1]) + y0) for i in c['indices']]
                area = float(sum(float(masks[i]['area']) for i in c['indices']))
                cx, cy, radius, spread, gap = FindHubHoles._circleOf(points)
                sets.append(dict(area=area, cx=cx, cy=cy, radius=radius, spread=spread, gap=gap, points=points))
            sets.sort(key=lambda s: s['area'])
            summary = [dict(area=round(s['area']), radius=round(s['radius'], 1), centre=(round(s['cx'], 1),
                            round(s['cy'], 1)), count=n) for s in sets]
            looked.append(dict(gamma=gamma, sets=summary))
            holes = [s for s in sets if low <= s['radius'] <= high and s['area'] <= areaMax]
            if not holes:
                tries.append(f"gamma {gamma}: no set with circle r {low:g} - {high:g} and total mask area <= {areaMax:g}"
                             + "; sets (area, r): " + ", ".join(f"({s['area']:.0f}, {s['radius']:.1f})" for s in sets))
                continue
            if not refine:
                best = holes[0]                         # sets are sorted by total mask area: the smallest
                return dict(found=True, cx=best['cx'], cy=best['cy'], radius=best['radius'], spread=best['spread'],
                            gap=best['gap'], count=n, points=best['points'], gamma=gamma, area=best['area'],
                            candidates=looked, kind="holes", reason="")
            refusals = []
            for best in holes:                          # the smallest area first; one that fails refining gives way
                refined, why = FindHubHoles._refinedSet(rgb, model, best)
                if refined is None:
                    refusals.append(f"set ({best['area']:.0f}, r {best['radius']:.1f}): {why}")
                    continue
                return dict(found=True, cx=refined['cx'], cy=refined['cy'], radius=refined['radius'],
                            spread=refined['spread'], gap=refined['gap'], count=n, points=refined['points'],
                            gamma=gamma, area=best['area'], candidates=looked, kind="holes", refined=True,
                            refineMoved=refined['moved'], unrefined=(best['cx'], best['cy'], best['radius'],
                                                                    best['spread']), reason="")
            tries.append(f"gamma {gamma}: refined - " + "; ".join(refusals))
        return dict(found=False, count=n, gamma=None, candidates=looked, reason="; ".join(tries) or "no gamma tried")

    @staticmethod
    def _refinedSet(rgb: np.ndarray, model: str, chosen: Dict):
        """(refined set dict(cx, cy, radius, spread, gap, points, moved), '') or (None, why): each hole of the chosen set
        re-segmented (_refine), the circle fitted again and gated (REFINE_CHECKS, CHANGES 183)."""
        refined = FindHubHoles._refine(rgb, chosen['points'], model)
        if refined is None:
            return None, "a hole gave no outline mask when refined"
        moved = max(float(np.hypot(a[0] - b[0], a[1] - b[1])) for a, b in zip(chosen['points'], refined))
        if moved > float(FindHubHoles.SAM_REFINE_MOVE_MAX):
            return None, f"a hole moved {moved:.1f} px when refined > {FindHubHoles.SAM_REFINE_MOVE_MAX}"
        cx, cy, radius, spread, gap = FindHubHoles._circleOf(refined)
        low, high = (float(v) for v in FindHubHoles.HOLES_CIRCLE_RADIUS)
        bad = [text for failed, text in (
            (not low <= radius <= high, f"refined circle r {radius:.1f} outside {low:g} - {high:g}"),
            (spread > float(FindHubHoles.SAM_SPREAD_MAX), f"refined spread {spread:.1f} > {FindHubHoles.SAM_SPREAD_MAX}"),
            (gap > float(FindHubHoles.SAM_GAP_MAX), f"refined angle gap off by {gap:.0f} > {FindHubHoles.SAM_GAP_MAX} deg"))
            if failed]
        if bad:
            return None, "; ".join(bad)
        return dict(cx=cx, cy=cy, radius=radius, spread=spread, gap=gap, points=refined, moved=moved), ""

    # ------------------------------------------------------------------ dark blobs (CHANGES 152, 157)

    @staticmethod
    def findHolesWithMorphology(anImage: np.ndarray, model: str | None) -> Dict:
        """The hub's holes round the cap in the cap picture (CHANGES 152): dict(found, cx, cy, radius, spread, gap, count,
        percentile, points, blobs, reason). Dark blobs in the band round CENTER (the darkest percentile of the band; the
        next percentile is tried when the holes are not all found), the count of the model's holes most evenly spaced on
        a circle; found only with the circle radius in holes.radius, spread and angle gaps within their limits. Then
        once more with the band round the circle found, so that a part sitting off CENTER does not cut holes at the
        band's edge (their centres would be pulled inward). Not found: the result (and reason) of the most informative
        try (CHANGES 157) - a circle failing the fewest checks before one failing more, a circle before no circle, more
        blobs before fewer - not simply the last percentile's. (Moved from CheckCap.hubHoles, CHANGES 164.)"""
        from camera.CheckCap import CheckCap
        n = FindHubHoles.holeCount(model)
        grey = cv2.GaussianBlur(cv2.cvtColor(anImage, cv2.COLOR_BGR2GRAY), (5, 5), 0)
        tries = []
        for percentile in FindHubHoles.SEATING_HUB_HOLES_DARK_PERCENTILES:
            result = FindHubHoles._holesAround(grey, (float(CheckCap.CENTER[0]), float(CheckCap.CENTER[1])), n,
                                               float(percentile))
            if result["found"]:
                again = FindHubHoles._holesAround(grey, (result["cx"], result["cy"]), n, float(percentile))
                return again if again["found"] else result
            tries.append(result)
        if not tries:
            return dict(found=False, count=n, reason="no dark blobs")
        return max(tries, key=FindHubHoles._informativeness)

    @staticmethod
    def _informativeness(result: Dict):
        """Sort key of a failed findHolesWithMorphology try (larger = says more about why the holes were not found)."""
        circle = result.get("radius") is not None
        return (1 if circle else 0, -int(result.get("failed", 0)) if circle else 0, int(result.get("blobs", 0)))

    @staticmethod
    def _holesGates(radius: float, spread: float, gap: float) -> List[str]:
        """The hole circle's checks that fail (empty: it passes)."""
        lowR, highR = float(FindHubHoles.SEATING_HUB_HOLES_RADIUS[0]), float(FindHubHoles.SEATING_HUB_HOLES_RADIUS[1])
        bad = []
        if not lowR <= radius <= highR:
            bad.append(f"circle r {radius:.1f} outside {lowR:g} - {highR:g}")
        if spread > FindHubHoles.SEATING_HUB_HOLES_SPREAD_MAX:
            bad.append(f"spread {spread:.1f} > {FindHubHoles.SEATING_HUB_HOLES_SPREAD_MAX}")
        if gap > FindHubHoles.SEATING_HUB_HOLES_GAP_MAX:
            bad.append(f"angle gap off by {gap:.0f} > {FindHubHoles.SEATING_HUB_HOLES_GAP_MAX} deg")
        return bad

    @staticmethod
    def _holesAround(grey: np.ndarray, centre, n: int, percentile: float) -> Dict:
        """One try of findHolesWithMorphology: the band round centre, the darkest percentile of it."""
        h, w = grey.shape
        yy, xx = np.mgrid[:h, :w]
        r = np.hypot(xx - centre[0], yy - centre[1])
        band = (r > float(FindHubHoles.SEATING_HUB_HOLES_BAND[0])) & (r < float(FindHubHoles.SEATING_HUB_HOLES_BAND[1]))
        k = max(int(FindHubHoles.SEATING_HUB_HOLES_OPEN), 1)
        lowR, highR = float(FindHubHoles.SEATING_HUB_HOLES_RADIUS[0]), float(FindHubHoles.SEATING_HUB_HOLES_RADIUS[1])
        dark = ((grey <= np.percentile(grey[band], percentile)) & band).astype(np.uint8)
        dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
        count, _, stats, centroids = cv2.connectedComponentsWithStats(dark)
        blobs = sorted([(int(stats[i, 4]), (float(centroids[i][0]), float(centroids[i][1]))) for i in range(1, count)
                        if FindHubHoles.SEATING_HUB_HOLES_AREA[0] < stats[i, 4] < FindHubHoles.SEATING_HUB_HOLES_AREA[1]],
                       reverse=True)[:int(FindHubHoles.SEATING_HUB_HOLES_BLOBS_KEEP)]
        result = dict(found=False, count=n, percentile=percentile, blobs=len(blobs))
        if len(blobs) < n:
            result["reason"] = f"{len(blobs)} dark blobs, {n} holes needed"
            return result
        gateEach = bool(FindHubHoles.SEATING_HUB_HOLES_GATE_EACH_COMBINATION)
        spreadMax, gapMax = float(FindHubHoles.SEATING_HUB_HOLES_SPREAD_MAX), float(FindHubHoles.SEATING_HUB_HOLES_GAP_MAX)
        best, bestPassing, combinations = None, None, 0
        for points in itertools.combinations([c for _, c in blobs], n):
            combinations += 1
            p = np.asarray(points, dtype=np.float64)
            sol = np.linalg.lstsq(np.c_[2 * p, np.ones(n)], (p ** 2).sum(1), rcond=None)[0]
            cx, cy = float(sol[0]), float(sol[1])
            radius = float(np.sqrt(max(sol[2] + cx * cx + cy * cy, 0.0)))
            q = p - (cx, cy)
            spread = float(np.hypot(q[:, 0], q[:, 1]).std())
            angles = np.sort(np.degrees(np.arctan2(q[:, 1], q[:, 0])))
            gap = float(np.abs(np.diff(np.r_[angles, angles[0] + 360.0]) - 360.0 / n).max())
            score = spread + gap / 5.0
            candidate = (score, points, cx, cy, radius, spread, gap)
            if best is None or score < best[0]:
                best = candidate
            if gateEach and lowR <= radius <= highR and spread <= spreadMax and gap <= gapMax and (
                    bestPassing is None or score < bestPassing[0]):
                bestPassing = candidate
        _, points, cx, cy, radius, spread, gap = bestPassing if bestPassing is not None else best
        bad = FindHubHoles._holesGates(radius, spread, gap)
        reason = "; ".join(bad)
        if bad and gateEach:
            reason = f"none of the {combinations} combinations of {len(blobs)} blobs passes; most evenly spaced: {reason}"
        result.update(found=not bad, cx=cx, cy=cy, radius=radius, spread=spread, gap=gap, points=points,
                      failed=len(bad), reason=reason)
        return result
