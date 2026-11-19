"""
FindHubHoles - the hub's 4 (DOST) / 5 (DOSTPLUS) holes in a picture of the hub with something on it (cap, bung, nut and
washer), for the cap, bung and split pin checks (CHANGES 164). The circle through the holes gives the hub's centre in
that picture.

Two methods (vision.hubholes.method):

  "sam" (findHolesWithSAM) - YOLO + MobileSAM, with checkHub()'s own steps (camera/HubAndBearingSegmenter.py, not
        changed): the picture's annulus cut out and normalised (preprocess_image), YOLO boxes and grid prompts to
        MobileSAM, the masks classified, filtered by area ratio and shape, grouped by distance from the centre and by
        size, then the equispacing filter with the dual 4 / 5 search (mismatch detection) and select_best_group; the
        gammas in checkHub()'s smart order, first success wins. checkHub() itself may take the holes or the bolts of a
        hub; here the annulus is thin and centred on the hub centre that checkHub() found in this cycle (its circle
        through the holes or the bolts - both are concentric with the hub): the holes (circle r 147 - 151, holes r 10 /
        14.5 px) lie inside it, the stud tops (circle r 182 - 196, inner edge 169 / 183 px) outside it but for a sliver
        too small to pass the mask area filter. The group must also make a circle with a radius in SAM_HOLES_RADIUS
        (the bolts' circle is 178 px and more), spread <= SAM_SPREAD_MAX, evenly spaced (SAM_GAP_MAX) and its centre
        within SAM_SEED_DISTANCE_MAX of the seed (the holes are concentric with the hub; four look-alike masks on the bare
        face can make an evenly spaced square round another centre), else the next
        gamma is tried; checkHub()'s "other model" verdict on a gamma is noted and the next gamma tried too (in the thin
        annulus its spacing ranges, set for the whole hub, can call 4 of the 5 DOSTPLUS holes a DOST hub).
        Without this cycle's hub centre (dry runs, a hub check that gave none) the seed comes from one pass over
        checkHub()'s own annulus (hub.segment.center, r 116 - 253) - holes or bolts, either gives the centre.
        The MobileSAM predictor and the segmenter object are one per thread (the split pin check runs its pictures in
        threads); the YOLO model is shared behind a lock. Results are kept for the last SAM_CACHE_SIZE pictures (the
        split pin check gets each picture twice).
  "morphology" (findHolesWithMorphology) - the dark-blob method of CHANGES 152 / 157, moved here from
        CheckCap.hubHoles unchanged: the darkest percentile of the band round cap.center, an opening, the model's count
        of blobs most evenly spaced on a circle with radius / spread / angle-gap checks. Kept for comparison and as a
        switch back; writing on the hub face in the band can be as dark as a hole.

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
from camera.VisionConfig import expectOneOf


class _LockedYolo:
    """The shared YOLO model, one predict at a time (it is not thread-safe)."""

    def __init__(self, model, lock: threading.Lock):
        self.model, self.lock = model, lock

    def predict(self, *args, **kwargs):
        with self.lock:
            return self.model.predict(*args, **kwargs)


class FindHubHoles:

    METHOD = "sam"  # "sam" (YOLO + MobileSAM) or "morphology" (dark blobs)
    # SAM: the annulus round the seed (this cycle's hub centre) in which the holes lie and the bolts do not
    SAM_ANNULUS_INNER = 130  # px; holes from 140 (DOST) / 133.5 (DOSTPLUS)
    SAM_ANNULUS_OUTER_DOST = 166  # holes to 161, stud tops from 169
    SAM_ANNULUS_OUTER_DOSTPLUS = 172  # holes to 163, stud tops from 183
    SAM_HOLES_RADIUS = (140.0, 165.0)  # px, circle through the holes found (bolts: 178+)
    SAM_SPREAD_MAX = 3.0  # px, the holes' distances from that circle (sd)
    SAM_GAP_MAX = 15.0  # deg, an angle between neighbouring holes off 360 / count
    SAM_SEED_DISTANCE_MAX = 8.0  # px, the holes' circle centre from the seed
    SAM_CACHE_SIZE = 12  # pictures whose result is kept

    # Morphology (CHANGES 152, 157; moved from CheckCap, same keys). The holes (dark, at r ~150) - never the bolts
    # (bright stud tops at r 182 - 196): dark blobs only, searched in the band round cap.center, the circle through them
    # must have a radius in holes.radius, all the holes of the model (DOST 4, DOSTPLUS 5) evenly spaced.
    SEATING_HUB_HOLES_BAND = (128, 168)  # px from cap.center
    SEATING_HUB_HOLES_DARK_PERCENTILES = [6.0, 9.0, 12.0]  # darkest % of the band, tried in turn
    SEATING_HUB_HOLES_AREA = (150, 2500)  # px, one hole's dark blob
    SEATING_HUB_HOLES_OPEN = 5  # px; opening that drops thin dark lines
    SEATING_HUB_HOLES_BLOBS_KEEP = 8  # largest blobs tried
    SEATING_HUB_HOLES_RADIUS = (140.0, 160.0)  # px, hole circle
    SEATING_HUB_HOLES_SPREAD_MAX = 3.0  # px, holes off the circle (sd)
    SEATING_HUB_HOLES_GAP_MAX = 15.0  # deg, a gap off 360 / count
    # the model's hole count (both methods)
    SEATING_HUB_HOLES_COUNT_DOST = 4
    SEATING_HUB_HOLES_COUNT_DOSTPLUS = 5
    # True: the radius / spread / angle-gap checks are applied to every combination of blobs and the most evenly spaced
    # combination that passes them is taken; False: the most evenly spaced combination first, then checked. CHANGES 157.
    SEATING_HUB_HOLES_GATE_EACH_COMBINATION = True

    # This cycle's hub centre (dict cx, cy, ... from checkHub(); ImageProcessor.hubCentre) when the caller does not
    # pass one: set by the camera server. samModelProvider / yoloModelProvider replace ModelManager's models (tests).
    hubCentreProvider = None
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
    def findHoles(anImage: np.ndarray, model: Optional[str], hubCentre: Optional[Dict] = None) -> Dict:
        """The hub's holes by the configured method (vision.hubholes.method). hubCentre: this cycle's hub centre from
        checkHub() (the SAM method's seed); None = FindHubHoles.hubCentreProvider(), if set. A not-found result's
        reason names the gamma / darkest percentile."""
        expectOneOf("vision.hubholes.method", FindHubHoles.METHOD, ("sam", "morphology"))
        if str(FindHubHoles.METHOD).strip().lower() == "morphology":
            result = FindHubHoles.findHolesWithMorphology(anImage, model)
            result["method"] = "morphology"
            if not result["found"]:
                result["reason"] = f"{result.get('reason')}; darkest {result.get('percentile')} %"
            return result
        return FindHubHoles.findHolesWithSAM(anImage, model, hubCentre)

    @staticmethod
    def describe(result: Dict) -> str:
        """'hub from 4 holes (x, y) r .., spread .., gap .. deg (sam, gamma 2, seed ...)' - for the log lines."""
        if not result.get("found"):
            return f"the hub's {result.get('count')} holes not found ({result.get('reason')})"
        how = result.get("method", "")
        if how == "sam":
            how = f"sam, gamma {result.get('gamma')}, seed {result.get('seedFrom')}"
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
    def _seed(hubCentre: Optional[Dict]):
        """(cx, cy, 'from ...') of this cycle's hub centre, or None."""
        centre = hubCentre
        if centre is None and FindHubHoles.hubCentreProvider is not None:
            try:
                centre = FindHubHoles.hubCentreProvider()
            except Exception:
                centre = None
        try:
            if centre and np.isfinite(float(centre["cx"])) and np.isfinite(float(centre["cy"])):
                return (float(centre["cx"]), float(centre["cy"]),
                        f"the hub centre given ({centre.get('count', '?')} {centre.get('kind', '?')})")
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
    def _samPass(rgb: np.ndarray, model: str, centre, inner: int, outer: int, gates: bool = True) -> Dict:
        """checkHub()'s steps on the annulus inner - outer round centre; the first gamma whose winning group has the
        model's count (and, with gates, a circle with radius, spread and angle gaps within their limits) wins."""
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
            if gates:
                low, high = float(FindHubHoles.SAM_HOLES_RADIUS[0]), float(FindHubHoles.SAM_HOLES_RADIUS[1])
                off = float(np.hypot(cx - float(centre[0]), cy - float(centre[1])))
                bad = [text for failed, text in (
                    (not low <= radius <= high, f"circle r {radius:.1f} outside {low:g} - {high:g}"),
                    (off > float(FindHubHoles.SAM_SEED_DISTANCE_MAX),
                     f"centre {off:.1f} px from the seed > {FindHubHoles.SAM_SEED_DISTANCE_MAX}"),
                    (spread > float(FindHubHoles.SAM_SPREAD_MAX), f"spread {spread:.1f} > {FindHubHoles.SAM_SPREAD_MAX}"),
                    (gap > float(FindHubHoles.SAM_GAP_MAX), f"angle gap off by {gap:.0f} > {FindHubHoles.SAM_GAP_MAX} deg"))
                    if failed]
                if bad:
                    tries.append(f"gamma {gamma}: " + ", ".join(bad))
                    continue
            return dict(found=True, cx=cx, cy=cy, radius=radius, spread=spread, gap=gap, count=n, points=points,
                        gamma=gamma, kind=circle.get("kind"), reason="")
        return dict(found=False, count=n, gamma=None, reason="; ".join(tries) or "no gamma tried")

    @staticmethod
    def findHolesWithSAM(anImage: np.ndarray, model: Optional[str], hubCentre: Optional[Dict] = None) -> Dict:
        """The holes by YOLO + MobileSAM in the thin annulus round this cycle's hub centre (see the module text)."""
        model = DOSTPLUS if model == DOSTPLUS else DOST
        n = FindHubHoles.holeCount(model)
        if anImage is None:
            return dict(found=False, count=n, method="sam", reason="no picture")
        seed = FindHubHoles._seed(hubCentre)
        small = np.ascontiguousarray(anImage[::4, ::4])
        key = (zlib.crc32(small.tobytes()), anImage.shape, model,
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
                                             int(H.HUB_SEGMENT_INNER_RADIUS), int(H.HUB_SEGMENT_OUTER_RADIUS), gates=False)
                if not wide["found"]:
                    return dict(found=False, count=n, method="sam", seedFrom=None,
                                reason=f"no hub centre from this cycle's hub check, and none in the hub check's annulus "
                                       f"({wide['reason']})")
                seed = (wide["cx"], wide["cy"], f"the hub check's annulus ({wide['count']} {wide.get('kind')}, "
                                                f"gamma {wide['gamma']})")
            outer = FindHubHoles.SAM_ANNULUS_OUTER_DOSTPLUS if model == DOSTPLUS else FindHubHoles.SAM_ANNULUS_OUTER_DOST
            result = FindHubHoles._samPass(rgb, model, seed[:2], int(FindHubHoles.SAM_ANNULUS_INNER), int(outer))
        except Exception as e:
            return dict(found=False, count=n, method="sam", seedFrom=None if seed is None else seed[2],
                        reason=f"YOLO + MobileSAM failed: {e}")
        result.update(method="sam", seed=(round(seed[0], 1), round(seed[1], 1)), seedFrom=seed[2])
        if not result["found"]:
            result["reason"] = (f"annulus {int(FindHubHoles.SAM_ANNULUS_INNER)} - {int(outer)} px round "
                                f"({seed[0]:.1f}, {seed[1]:.1f}) from {seed[2]}: {result['reason']}")
        return result

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
