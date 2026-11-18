"""
CheckCap - Detects presence of Cap by comparing with previous assembly stage images.

Detection Method:
    Step 1: Which cap - brass if mean R-B in the DOSTPLUS circle is above the DOSTPLUS R-B limit; else silver if mean
            R-B in the DOST circle is below the DOST R-B limit AND the mean brightness within the silver brightness
            radius reaches its minimum; else no cap.
    Step 2: The cap found must be the model's cap (DOST: silver, DOSTPLUS: brass, from the QR code).
    Step 3 (limits of the cap found): Color Delta Analysis
        - Compare Cap image against 3 base images (Hub+Bottom Bearing, Top Bearing, Nut+Plate Washer)
        - For each pixel in circular mask, calculate (R_cap - R_base), (G_cap - G_base), (B_cap - B_base)
        - Clamp values to [0, 255] and average to get grayscale delta
        - All 3 averages must reach the limits of the cap found
    Step 4 (seating, Machine 2; vision.cap.seating.check): MobileSAM, one point prompt at CENTER (multimask):
        - brass cap: the cap face mask and the bore mask round it, both from the cap picture; the cap centre must be
          within seating.brass.offset.max of the bore centre (a cap pushed off-centre shows the bore on one side);
        - silver cap: the cap outline mask, then the top face (box = outline centre +- face.box.scale x its radius);
          the top face centre must be within seating.silver.face.offset.max of the outline centre (a tilted cup keeps
          its body in the bore but its top slides over and the side wall shows on one side), and the outline centre
          within seating.silver.outline.offset.max of CENTER (a cap pushed right off the bore; 0 = not checked).
        Masks are picked by radius (seating.*.radius.min/max). No mask found = NOT OK. Run only when steps 2 and 3 pass.
    Result OK = right cap AND all 3 comparisons pass AND (seating off or the cap is seated).

Parameters (per machine, <vendor>.vision.cap.* in ApplicationConfiguration.properties):
    CENTER: centre of the cap region (Machine 1: 635, 360)
    RADIUS: 100 for DOST, 70 for DOSTPLUS
    DELTA_THRESHOLD_HUB/TOP/NUT: Step 3 minimum brightening against each earlier picture, per cap
    RB_THRESHOLD: Step 1 R-B limits (silver: below -1, brass: above 15)
    SILVER_BRIGHTNESS_RADIUS / _MIN: Step 1 silver brightness test (radius 25; minimum 0 = off)
    SEATING_*: Step 4 (off on Machine 1)
"""

import cv2
import numpy as np
from typing import Dict, Tuple
import glob
import os
import threading
# from HandDetector import HandDetector
from camera.VisionConfig import vInt, vFloat, vTuple, vBool, vStr, vList

# Try to import production modules, fall back to mocks for standalone testing
try:
    from utils.RedisUtils import *
    from BaseUtils import *
    from Configuration import *
    from utils.QRCodeHelper import getModel_LHSRHS_AndTonnage, UNKNOWN
    from logutils.SlaveLoggers import logBoth
    from logutils.Logger import MessageType

    CosThetaConfigurator.getInstance()
except ImportError:
    # Standalone testing mode - define mocks
    DOST = "DOST"
    DOSTPLUS = "DOSTPLUS"
    hubAndBottomBearingPictureKeyString = "hubAndBottomBearingPicture"
    topBearingPictureKeyString = "topBearingPicture"
    nutAndPlateWasherPictureKeyString = "nutAndPlateWasherPicture"

    def logBoth(level, source, msg, messageType=None):
        colours = {'logInfo': '\033[1;32m', 'logError': '\033[1;31m', 'logWarning': '\033[1;33m', 'logDebug': '\033[1;34m', 'logCritical': '\033[1;31m'}
        reset = '\033[0m'
        colour = colours.get(level, '')
        print(f"{colour}{msg}{reset}")

    class MessageType:
        GENERAL = 1
        SUCCESS = 2
        RISK = 3
        ISSUE = 4
        PROBLEM = 5

    def getModel_LHSRHS_AndTonnage(qrCode):
        return "DOST", "LHS", "Unknown"

    UNKNOWN = "Unknown"

class CheckCap:
    """
    Checks for the presence of the Cap by comparing the current image
    with previously captured images using a circular mask and color analysis.
    """

    # Per machine (allenbradley.vision.cap.* / siemens.vision.cap.*), read through VisionConfig; a change in the
    # properties file is used from the next picture on.
    CENTER = vTuple("vision.cap.center", (635, 360))               # centre of the circle the check looks at
    RADIUS_DOST = vInt("vision.cap.radius.dost", 100)
    RADIUS_DOSTPLUS = vInt("vision.cap.radius.dostplus", 70)
    RB_THRESHOLD_DOST = vFloat("vision.cap.rb.threshold.dost", -1.0)            # Step 2: R-B must be below this
    RB_THRESHOLD_DOSTPLUS = vFloat("vision.cap.rb.threshold.dostplus", 15.0)    # Step 2: R-B must be above this

    # Step 1: the cap picture must be brighter than each earlier picture of the same part by at least these
    DELTA_THRESHOLD_HUB_DOST = vFloat("vision.cap.delta.threshold.hub.dost", 30.0)
    DELTA_THRESHOLD_TOP_DOST = vFloat("vision.cap.delta.threshold.top.dost", 30.0)
    DELTA_THRESHOLD_NUT_DOST = vFloat("vision.cap.delta.threshold.nut.dost", 35.0)

    DELTA_THRESHOLD_HUB_DOSTPLUS = vFloat("vision.cap.delta.threshold.hub.dostplus", 35.0)
    DELTA_THRESHOLD_TOP_DOSTPLUS = vFloat("vision.cap.delta.threshold.top.dostplus", 50.0)
    DELTA_THRESHOLD_NUT_DOSTPLUS = vFloat("vision.cap.delta.threshold.nut.dostplus", 35.0)

    # Silver cap test (after the brass test fails): mean brightness (B+G+R)/3 within this radius must reach the
    # minimum. A silver cap is bright in the middle; without a cap the bore and shaft end are dark there.
    # 0 = test off (every non-brass picture with a silver R-B counts as a silver cap, as before).
    SILVER_BRIGHTNESS_RADIUS = vInt("vision.cap.silver.brightness.radius", 25)
    SILVER_BRIGHTNESS_MIN = vFloat("vision.cap.silver.brightness.min", 0.0)

    # Step 4: seating (MobileSAM). Off = the check as before (Machine 1).
    SEATING_CHECK = vBool("vision.cap.seating.check", False)
    SEATING_BRASS_RADIUS_MIN = vFloat("vision.cap.seating.brass.radius.min", 75.0)    # brass cap face mask
    SEATING_BRASS_RADIUS_MAX = vFloat("vision.cap.seating.brass.radius.max", 95.0)
    SEATING_BORE_RADIUS_MIN = vFloat("vision.cap.seating.bore.radius.min", 104.0)     # bore round a brass cap
    SEATING_BORE_RADIUS_MAX = vFloat("vision.cap.seating.bore.radius.max", 120.0)
    SEATING_SILVER_RADIUS_MIN = vFloat("vision.cap.seating.silver.radius.min", 95.0)  # silver cap outline mask
    SEATING_SILVER_RADIUS_MAX = vFloat("vision.cap.seating.silver.radius.max", 112.0)
    SEATING_BRASS_OFFSET_MAX = vFloat("vision.cap.seating.brass.offset.max", 9.0)     # cap centre to bore centre
    SEATING_SILVER_FACE_OFFSET_MAX = vFloat("vision.cap.seating.silver.face.offset.max", 9.0)  # face to outline
    SEATING_FACE_BOX_SCALE = vFloat("vision.cap.seating.face.box.scale", 0.8)
    # silver: the cap outline centre within this of CENTER (a cap pushed right off the bore); 0 = not checked
    SEATING_SILVER_OUTLINE_OFFSET_MAX = vFloat("vision.cap.seating.silver.outline.offset.max", 0.0)
    # brass seating by fitted circles (CHANGES 128)
    SEATING_BRASS_EDGE_BAND = vTuple("vision.cap.seating.brass.edge.band", (55, 112))    # radii searched for the brass cap's edge (brass -> not brass)
    SEATING_BORE_EDGE_BAND = vTuple("vision.cap.seating.bore.edge.band", (106, 140))    # radii searched for the bore's edge round a brass cap
    SEATING_CIRCLE_FIT_TOLERANCE = vFloat("vision.cap.seating.circle.fit.tolerance", 2.0)    # px; an edge point within this of the circle counts as on it
    SEATING_BRASS_FIT_MIN = vFloat("vision.cap.seating.brass.fit.min", 0.5)    # share of the angles whose cap edge is on the fitted circle
    SEATING_BORE_FIT_MIN = vFloat("vision.cap.seating.bore.fit.min", 0.3)    # share of the angles whose bore edge is on the fitted circle
    # silver seating by two rules (CHANGES 129). Rule 1: no part of the hub centre may be left uncovered - the hub circle
    # comes from this cycle's hub + bottom bearing picture (MobileSAM, ring of points round CENTER plus a box).
    SEATING_HUB_RING_RADIUS = vInt("vision.cap.seating.hub.ring.radius", 95)    # px; radius of the ring of prompt points
    SEATING_HUB_BOX_HALF = vInt("vision.cap.seating.hub.box.half", 125)    # px; half side of the prompt box
    SEATING_HUB_RADIUS_MIN = vFloat("vision.cap.seating.hub.radius.min", 100.0)    # hub circle radius accepted
    SEATING_HUB_RADIUS_MAX = vFloat("vision.cap.seating.hub.radius.max", 125.0)
    SEATING_HUB_CENTRE_INSET = vFloat("vision.cap.seating.hub.centre.inset", 12.0)    # px; hub centre = hub circle less this
    SEATING_HUB_DARK_LEVEL = vInt("vision.cap.seating.hub.dark.level", 60)    # HSV value below this = uncovered (dark) hub
    SEATING_HUB_DARK_OPEN = vInt("vision.cap.seating.hub.dark.open", 9)    # px; dark parts narrower than this are shadow
    SEATING_HUB_VISIBLE_MAX = vInt("vision.cap.seating.hub.visible.max", 400)    # px; uncovered hub centre allowed
    # Rule 2: the cap's top face must still be one circle (a tilted cap shows its side wall on one side and the face
    # edge stops lying on one circle). Face edge per angle: strongest fall of brightness outward between band.min and
    # the cap outline radius less edge.margin, round the cap outline centre; share of the angles on the fitted circle.
    SEATING_SILVER_FACE_BAND_MIN = vInt("vision.cap.seating.silver.face.band.min", 60)
    SEATING_SILVER_FACE_EDGE_MARGIN = vInt("vision.cap.seating.silver.face.edge.margin", 6)
    SEATING_SILVER_FACE_FIT_MIN = vFloat("vision.cap.seating.silver.face.fit.min", 0.64)
    # Hub-centre distance (CHANGES 143), brass and silver: the cap's centre - the stage-2 ellipse centre of the cap mask
    # (camera/CapMaskFinder.py, the method frozen in CHANGES 139 - 142) - must lie within hub.distance.max of the hub's
    # centre found by this cycle's hub check (circle through its 4 / 5 holes or bolts, ImageProcessor.hubCentre) moved
    # by the offset for what the hub check found (holes on the disc face, bolt tips higher up: the camera sees the
    # three heights - holes, bolt tips, cap face - from a slight angle). The part comes back to the same place for the
    # hub and cap pictures (servo, sensors; user, 2026-11-18). Without a hub centre the rule is not applied (logged).
    SEATING_HUB_DISTANCE_CHECK = vBool("vision.cap.seating.hub.distance.check", False)
    SEATING_HUB_DISTANCE_MAX = vFloat("vision.cap.seating.hub.distance.max", 5.0)    # px
    # px (x, y) added to the hub centre, per hub kind and per model (CHANGES 147): the silver and the brass cap face sit at
    # different heights, so the camera's slight angle moves each by its own amount against the holes and the bolt tips
    SEATING_HUB_DISTANCE_OFFSET_HOLES_DOST = vTuple("vision.cap.seating.hub.distance.offset.holes.dost", (0.0, 0.0))
    SEATING_HUB_DISTANCE_OFFSET_HOLES_DOSTPLUS = vTuple("vision.cap.seating.hub.distance.offset.holes.dostplus", (0.0, 0.0))
    SEATING_HUB_DISTANCE_OFFSET_BOLTS_DOST = vTuple("vision.cap.seating.hub.distance.offset.bolts.dost", (0.0, 0.0))
    SEATING_HUB_DISTANCE_OFFSET_BOLTS_DOSTPLUS = vTuple("vision.cap.seating.hub.distance.offset.bolts.dostplus", (0.0, 0.0))
    # Where the hub centre for the distance rule comes from (CHANGES 152):
    #   "cap"      - the circle through the hub's holes round the cap, in the cap picture itself (hubHoles): hub and cap in
    #                one picture, so where the part sits on the fixture and any camera move cancel out
    #   "hubcheck" - this cycle's hub check (ImageProcessor.hubCentre, CHANGES 143), with the offsets above
    # The hub picture is of the part at another moment and often from a part that sat elsewhere: against one fixed hub
    # picture the seated DOST caps spread over 12 px (three part positions), in their own picture within 3.3 px.
    SEATING_HUB_SOURCE = vStr("vision.cap.seating.hub.source", "cap")
    # The holes (dark, at r ~150) - never the bolts (bright stud tops at r 182 - 196): dark blobs only, searched in the
    # band round CENTER, the circle through them must have a radius in holes.radius, all the holes of the model
    # (DOST 4, DOSTPLUS 5) evenly spaced. Not found -> the cap is NOT seated (a cap off the bore covers holes).
    SEATING_HUB_HOLES_BAND = vTuple("vision.cap.seating.hub.holes.band", (128, 168))    # px from CENTER
    SEATING_HUB_HOLES_DARK_PERCENTILES = vList("vision.cap.seating.hub.holes.dark.percentiles", [6.0, 9.0, 12.0])    # darkest % of the band, tried in turn
    SEATING_HUB_HOLES_AREA = vTuple("vision.cap.seating.hub.holes.area", (150, 2500))    # px, one hole's dark blob
    SEATING_HUB_HOLES_OPEN = vInt("vision.cap.seating.hub.holes.open", 5)    # px; opening that drops thin dark lines
    SEATING_HUB_HOLES_BLOBS_KEEP = vInt("vision.cap.seating.hub.holes.blobs.keep", 8)    # largest blobs tried
    SEATING_HUB_HOLES_RADIUS = vTuple("vision.cap.seating.hub.holes.radius", (140.0, 160.0))    # px, hole circle
    SEATING_HUB_HOLES_SPREAD_MAX = vFloat("vision.cap.seating.hub.holes.spread.max", 3.0)    # px, holes off the circle (sd)
    SEATING_HUB_HOLES_GAP_MAX = vFloat("vision.cap.seating.hub.holes.gap.max", 15.0)    # deg, a gap off 360 / count
    SEATING_HUB_HOLES_COUNT_DOST = vInt("vision.cap.seating.hub.holes.count.dost", 4)
    SEATING_HUB_HOLES_COUNT_DOSTPLUS = vInt("vision.cap.seating.hub.holes.count.dostplus", 5)
    # px (x, y) added to the hole circle's centre (source "cap"), per model: the cap face and the holes are at different
    # heights, seen from a slight angle
    SEATING_HUB_DISTANCE_OFFSET_CAP_DOST = vTuple("vision.cap.seating.hub.distance.offset.cap.dost", (0.0, 0.0))
    SEATING_HUB_DISTANCE_OFFSET_CAP_DOSTPLUS = vTuple("vision.cap.seating.hub.distance.offset.cap.dostplus", (0.0, 0.0))

    # Cached circular masks, keyed on (image size, centre, radius)
    _circular_masks = {}

    # One MobileSAM predictor per thread, on the shared model (ModelManager); the model loads on first use.
    # samModelProvider can be replaced (tests); it returns a MobileSAM model.
    _samLocal = threading.local()
    samModelProvider = None
    # The object-aware YOLO model for the cap mask (ModelManager's); yoloModelProvider can be replaced (tests).
    yoloModelProvider = None

    @classmethod
    def _get_radius_for_component(cls, componentType: str) -> int:
        """Get the appropriate radius based on component type."""
        if componentType.upper() == "DOSTPLUS":
            return cls.RADIUS_DOSTPLUS
        return cls.RADIUS_DOST

    @classmethod
    def _get_circular_mask(cls, image_shape: tuple, radius: int) -> np.ndarray:
        """
        Get the circular mask for the given image shape and radius.
        Creates it on first call or if parameters change, then reuses cached version.
        """
        key = (tuple(image_shape[:2]), tuple(cls.CENTER), radius)      # the centre can change on a live reload
        mask = cls._circular_masks.get(key)
        if mask is None:
            h, w = image_shape[:2]
            mask = np.zeros((h, w), dtype=np.uint8)
            cv2.circle(mask, (int(cls.CENTER[0]), int(cls.CENTER[1])), radius, 255, -1)
            if len(cls._circular_masks) > 16:
                cls._circular_masks.clear()
            cls._circular_masks[key] = mask

        return mask

    @staticmethod
    def _compute_clamped_delta_grayscale(cap_image: np.ndarray,
                                         base_image: np.ndarray,
                                         mask: np.ndarray) -> Tuple[np.ndarray, float]:
        """
        Compute the clamped color delta between cap and base image.

        For each pixel: delta = clamp(cap - base, 0, 255) for each channel
        Then average the 3 channels to get grayscale delta.
        """
        cap_float = cap_image.astype(np.float32)
        base_float = base_image.astype(np.float32)

        # Calculate delta for each channel: cap - base
        delta = cap_float - base_float

        # Clamp to [0, 255]
        delta = np.clip(delta, 0, 255)

        # Average across channels to get grayscale (B + G + R) / 3
        delta_grayscale = np.mean(delta, axis=2).astype(np.uint8)

        # Calculate average within the mask
        masked_pixels = delta_grayscale[mask > 0]
        average = float(np.mean(masked_pixels)) if len(masked_pixels) > 0 else 0.0

        return delta_grayscale, average

    @staticmethod
    def _compute_rb_difference(image: np.ndarray, mask: np.ndarray) -> float:
        """
        Compute the average R - B within the masked region.
        Simple subtraction, no absolute value, no clamping.
        """
        # OpenCV uses BGR order
        b_channel = image[:, :, 0].astype(np.float32)
        r_channel = image[:, :, 2].astype(np.float32)

        # Calculate R - B
        rb_diff = r_channel - b_channel

        # Get average within mask
        masked_pixels = rb_diff[mask > 0]
        average = float(np.mean(masked_pixels)) if len(masked_pixels) > 0 else 0.0

        return average

    @classmethod
    def _sam_predictor(cls):
        predictor = getattr(cls._samLocal, "predictor", None)
        if predictor is None:
            from mobile_sam import SamPredictor
            if cls.samModelProvider is not None:
                model = cls.samModelProvider()
            else:
                from camera.ModelManager import ModelManager
                model = ModelManager.get_instance().get_sam_model()
            predictor = SamPredictor(model)
            cls._samLocal.predictor = predictor
        return predictor

    @staticmethod
    def _ellipse_of(mask: np.ndarray):
        """Ellipse fitted to the largest outer contour of a mask: dict(cx, cy, radius, ratio, area, contour) or None."""
        cs, _ = cv2.findContours(mask.astype(np.uint8) * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if not cs:
            return None
        c = max(cs, key=cv2.contourArea)
        if len(c) < 5:
            return None
        (x, y), (a, b), _ = cv2.fitEllipse(c)
        major, minor = max(a, b) / 2.0, min(a, b) / 2.0
        if major <= 0:
            return None
        return dict(cx=float(x), cy=float(y), radius=(major + minor) / 2.0, ratio=minor / major,
                    area=float(cv2.contourArea(c)), contour=c)

    @staticmethod
    def _pick_by_radius(fits, low: float, high: float):
        """The mask with mean radius in [low, high] (closest to the middle if several)."""
        mid = (low + high) / 2.0
        ok = [f for f in fits if f is not None and low <= f["radius"] <= high]
        return min(ok, key=lambda f: abs(f["radius"] - mid)) if ok else None

    # ---- brass cap seating by fitted circles (CHANGES 128) ---------------------------------------------------------
    # MobileSAM's second mask at the cap centre was taken for the bore. On the 17 Nov production pictures it is the cap
    # plus the dark gap round it (radius 86-103 instead of the bore's 104-120, its outline leaking into the bearing), so
    # 31 of 36 seated brass caps were "bore not found". Now both circles come from the picture unwrapped round CENTER
    # (angle x radius): per angle the strongest fall of R-B (brass -> not brass) in SEATING_BRASS_EDGE_BAND and the
    # strongest fall of grey in SEATING_BORE_EDGE_BAND; a circle is fitted to each by RANSAC (deterministic).
    _POLAR_RADIUS = vInt("vision.cap.polar.radius", 170)
    _POLAR_ANGLES = vInt("vision.cap.polar.angles", 360)

    @staticmethod
    def _polar(anImage: np.ndarray) -> np.ndarray:
        """The picture unwrapped round CENTER: rows = angle (1 degree), columns = radius (1 px)."""
        return cv2.warpPolar(cv2.GaussianBlur(anImage, (5, 5), 0), (CheckCap._POLAR_RADIUS, CheckCap._POLAR_ANGLES),
                             (float(CheckCap.CENTER[0]), float(CheckCap.CENTER[1])), CheckCap._POLAR_RADIUS,
                             cv2.WARP_POLAR_LINEAR).astype(np.float64)

    @staticmethod
    def _edgePoints(profile: np.ndarray, band) -> np.ndarray:
        """Per angle, the radius in band with the strongest fall outward; the weakest 30 % of the angles left out.
        Returns the points (x, y) in the picture."""
        low, high = int(band[0]), int(band[1])
        fall = -np.gradient(cv2.GaussianBlur(profile, (1, 5), 0), axis=1)[:, low:high]
        radius = fall.argmax(1) + low
        strength = fall.max(1)
        keep = strength > np.percentile(strength, 30)
        angle = np.deg2rad(np.arange(profile.shape[0]) * 360.0 / profile.shape[0])[keep]
        r = radius[keep].astype(np.float64)
        return np.c_[float(CheckCap.CENTER[0]) + r * np.cos(angle), float(CheckCap.CENTER[1]) + r * np.sin(angle)]

    @staticmethod
    def _fitCircle(points: np.ndarray, tolerance: float):
        """RANSAC circle (fixed seed, 300 tries), refined by least squares on its inliers: dict(cx, cy, radius,
        fit = share of the points on it, contour) or None."""
        if len(points) < 10:
            return None
        rng = np.random.default_rng(0)
        best = None
        for _ in range(300):
            s = points[rng.choice(len(points), 3, replace=False)]
            try:
                c = np.linalg.solve(np.c_[2 * s, np.ones(3)], (s ** 2).sum(1))
            except np.linalg.LinAlgError:
                continue
            r2 = c[2] + c[0] ** 2 + c[1] ** 2
            if r2 <= 0:
                continue
            inliers = np.abs(np.hypot(*(points - c[:2]).T) - np.sqrt(r2)) < tolerance
            if best is None or inliers.sum() > best.sum():
                best = inliers
        if best is None or best.sum() < 3:
            return None
        p = points[best]
        c = np.linalg.lstsq(np.c_[2 * p, np.ones(len(p))], (p ** 2).sum(1), rcond=None)[0]
        r = float(np.sqrt(max(c[2] + c[0] ** 2 + c[1] ** 2, 0.0)))
        contour = cv2.ellipse2Poly((int(round(c[0])), int(round(c[1]))), (int(round(r)), int(round(r))), 0, 0, 360, 2)
        return dict(cx=float(c[0]), cy=float(c[1]), radius=r, fit=float(best.mean()), contour=contour.reshape(-1, 1, 2))

    @staticmethod
    def brassCapAndBore(anImage: np.ndarray):
        """(cap circle, bore circle) of a brass cap, each None when not found (fit share below its minimum, or radius
        outside seating.brass.radius / seating.bore.radius)."""
        polar = CheckCap._polar(anImage)
        rb = polar[..., 2] - polar[..., 0]
        grey = polar.mean(2)
        tol = float(CheckCap.SEATING_CIRCLE_FIT_TOLERANCE)
        cap = CheckCap._fitCircle(CheckCap._edgePoints(rb, CheckCap.SEATING_BRASS_EDGE_BAND), tol)
        bore = CheckCap._fitCircle(CheckCap._edgePoints(grey, CheckCap.SEATING_BORE_EDGE_BAND), tol)
        if cap is not None and not (cap["fit"] >= CheckCap.SEATING_BRASS_FIT_MIN and
                                    CheckCap.SEATING_BRASS_RADIUS_MIN <= cap["radius"] <= CheckCap.SEATING_BRASS_RADIUS_MAX):
            cap = None
        if bore is not None and not (bore["fit"] >= CheckCap.SEATING_BORE_FIT_MIN and
                                     CheckCap.SEATING_BORE_RADIUS_MIN <= bore["radius"] <= CheckCap.SEATING_BORE_RADIUS_MAX):
            bore = None
        return cap, bore

    # ---- silver cap seating (CHANGES 129) ----------------------------------------------------------------------------
    # The silver cap is wider than the hub centre, so a seated cap covers it completely (rule 1), and a seated cap's top
    # face projects as one circle (rule 2). The earlier test (top face centre to outline centre <= 9 px, MobileSAM face
    # mask) rejected seated 17 Nov caps: the embossed face gives ragged masks, and the face of a seated cap already sits
    # about 8 px from the outline centre (camera angle) - see silverFaceBySegmenter, kept.
    @staticmethod
    def hubCircle(hubImage: np.ndarray):
        """The hub circle in the hub + bottom bearing picture: dict(cx, cy, radius, ratio, area, contour) or None."""
        import torch
        cx, cy = float(CheckCap.CENTER[0]), float(CheckCap.CENTER[1])
        a = np.linspace(0, 2 * np.pi, 12, endpoint=False)
        rr, h = float(CheckCap.SEATING_HUB_RING_RADIUS), float(CheckCap.SEATING_HUB_BOX_HALF)
        points = np.c_[cx + rr * np.cos(a), cy + rr * np.sin(a)].astype(np.float32)
        predictor = CheckCap._sam_predictor()
        predictor.set_image(cv2.cvtColor(hubImage, cv2.COLOR_BGR2RGB))
        with torch.inference_mode():
            masks, _, _ = predictor.predict(point_coords=points, point_labels=np.ones(len(points)),
                                            box=np.array([cx - h, cy - h, cx + h, cy + h], dtype=np.float32),
                                            multimask_output=True)
        fits = [f for f in (CheckCap._ellipse_of(m) for m in masks) if f is not None and
                CheckCap.SEATING_HUB_RADIUS_MIN <= f["radius"] <= CheckCap.SEATING_HUB_RADIUS_MAX]
        return max(fits, key=lambda f: f["ratio"]) if fits else None

    @staticmethod
    def hubCentreVisible(anImage: np.ndarray, capMask: np.ndarray, hub: Dict) -> int:
        """Pixels of the hub centre (hub circle less the inset) that are dark in the cap picture and not under the cap
        mask, after removing dark parts narrower than hub.dark.open (the shadow line round a seated cap)."""
        h, w = anImage.shape[:2]
        disc = np.zeros((h, w), np.uint8)
        cv2.circle(disc, (int(round(hub["cx"])), int(round(hub["cy"]))),
                   max(int(round(hub["radius"] - CheckCap.SEATING_HUB_CENTRE_INSET)), 1), 1, -1)
        cap = cv2.dilate(capMask.astype(np.uint8), np.ones((5, 5), np.uint8))
        value = cv2.cvtColor(anImage, cv2.COLOR_BGR2HSV)[:, :, 2]
        dark = ((value < CheckCap.SEATING_HUB_DARK_LEVEL) & (disc > 0) & (cap == 0)).astype(np.uint8)
        k = max(int(CheckCap.SEATING_HUB_DARK_OPEN), 1)
        dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
        return int(dark.sum())

    @staticmethod
    def silverFaceCircle(anImage: np.ndarray, cap: Dict):
        """Circle fitted to the silver cap's top-face edge round the cap outline centre: dict(cx, cy, radius, fit,
        contour) or None. fit = share of the 360 angles whose face edge is on the circle."""
        cx, cy = float(cap["cx"]), float(cap["cy"])
        value = cv2.GaussianBlur(cv2.cvtColor(anImage, cv2.COLOR_BGR2HSV)[:, :, 2].astype(np.float32), (5, 5), 0)
        polar = cv2.warpPolar(value, (CheckCap._POLAR_RADIUS, CheckCap._POLAR_ANGLES), (cx, cy),
                              CheckCap._POLAR_RADIUS, cv2.WARP_POLAR_LINEAR)
        fall = polar[:, :-2] - polar[:, 2:]
        low = int(CheckCap.SEATING_SILVER_FACE_BAND_MIN)
        high = min(int(cap["radius"]) - int(CheckCap.SEATING_SILVER_FACE_EDGE_MARGIN), fall.shape[1])
        if high - low < 5:
            return None
        r = (fall[:, low:high].argmax(1) + low + 1).astype(np.float64)
        angle = np.deg2rad(np.arange(polar.shape[0]) * 360.0 / polar.shape[0])
        return CheckCap._fitCircle(np.c_[cx + r * np.cos(angle), cy + r * np.sin(angle)],
                                   float(CheckCap.SEATING_CIRCLE_FIT_TOLERANCE))

    @staticmethod
    def silverFaceBySegmenter(predictor, cap: Dict):
        """The silver cap's top face by MobileSAM (box prompt inside the outline; the test before CHANGES 129):
        dict(cx, cy, radius, ratio, area, contour) or None. Needs predictor.set_image(cap picture) done."""
        import torch
        r = cap["radius"] * CheckCap.SEATING_FACE_BOX_SCALE
        box = np.array([cap["cx"] - r, cap["cy"] - r, cap["cx"] + r, cap["cy"] + r], dtype=np.float32)
        with torch.inference_mode():
            masks, _, _ = predictor.predict(point_coords=np.array([[cap["cx"], cap["cy"]]], dtype=np.float32),
                                            point_labels=np.array([1]), box=box, multimask_output=True)
        faces = [f for f in (CheckCap._ellipse_of(m) for m in masks) if f is not None]
        return min(faces, key=lambda f: f["area"]) if faces else None

    @classmethod
    def _yolo_model(cls):
        if cls.yoloModelProvider is not None:
            return cls.yoloModelProvider()
        from camera.ModelManager import ModelManager
        return ModelManager.get_instance().get_yolo_model()

    @staticmethod
    def hubHoles(anImage: np.ndarray, model: str | None) -> Dict:
        """The hub's holes round the cap in the cap picture (CHANGES 152): dict(found, cx, cy, radius, spread, gap, count,
        percentile, points, blobs, reason). Dark blobs in the band round CENTER (the darkest percentile of the band; the
        next percentile is tried when the holes are not all found), the count of the model's holes most evenly spaced on
        a circle; found only with the circle radius in holes.radius, spread and angle gaps within their limits. Then
        once more with the band round the circle found, so that a part sitting off CENTER does not cut holes at the
        band's edge (their centres would be pulled inward)."""
        n = int(CheckCap.SEATING_HUB_HOLES_COUNT_DOSTPLUS if model == DOSTPLUS else CheckCap.SEATING_HUB_HOLES_COUNT_DOST)
        grey = cv2.GaussianBlur(cv2.cvtColor(anImage, cv2.COLOR_BGR2GRAY), (5, 5), 0)
        result = dict(found=False, count=n, reason="no dark blobs")
        for percentile in CheckCap.SEATING_HUB_HOLES_DARK_PERCENTILES:
            result = CheckCap._holesAround(grey, (float(CheckCap.CENTER[0]), float(CheckCap.CENTER[1])), n,
                                           float(percentile))
            if result["found"]:
                again = CheckCap._holesAround(grey, (result["cx"], result["cy"]), n, float(percentile))
                return again if again["found"] else result
        return result

    @staticmethod
    def _holesAround(grey: np.ndarray, centre, n: int, percentile: float) -> Dict:
        """One try of hubHoles: the band round centre, the darkest percentile of it."""
        import itertools
        h, w = grey.shape
        yy, xx = np.mgrid[:h, :w]
        r = np.hypot(xx - centre[0], yy - centre[1])
        band = (r > float(CheckCap.SEATING_HUB_HOLES_BAND[0])) & (r < float(CheckCap.SEATING_HUB_HOLES_BAND[1]))
        k = max(int(CheckCap.SEATING_HUB_HOLES_OPEN), 1)
        lowR, highR = float(CheckCap.SEATING_HUB_HOLES_RADIUS[0]), float(CheckCap.SEATING_HUB_HOLES_RADIUS[1])
        dark = ((grey <= np.percentile(grey[band], percentile)) & band).astype(np.uint8)
        dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
        count, _, stats, centroids = cv2.connectedComponentsWithStats(dark)
        blobs = sorted([(int(stats[i, 4]), (float(centroids[i][0]), float(centroids[i][1]))) for i in range(1, count)
                        if CheckCap.SEATING_HUB_HOLES_AREA[0] < stats[i, 4] < CheckCap.SEATING_HUB_HOLES_AREA[1]],
                       reverse=True)[:int(CheckCap.SEATING_HUB_HOLES_BLOBS_KEEP)]
        result = dict(found=False, count=n, percentile=percentile, blobs=len(blobs))
        if len(blobs) < n:
            result["reason"] = f"{len(blobs)} dark blobs, {n} holes needed"
            return result
        best = None
        for points in itertools.combinations([c for _, c in blobs], n):
            p = np.asarray(points, dtype=np.float64)
            sol = np.linalg.lstsq(np.c_[2 * p, np.ones(n)], (p ** 2).sum(1), rcond=None)[0]
            cx, cy = float(sol[0]), float(sol[1])
            radius = float(np.sqrt(max(sol[2] + cx * cx + cy * cy, 0.0)))
            q = p - (cx, cy)
            spread = float(np.hypot(q[:, 0], q[:, 1]).std())
            angles = np.sort(np.degrees(np.arctan2(q[:, 1], q[:, 0])))
            gap = float(np.abs(np.diff(np.r_[angles, angles[0] + 360.0]) - 360.0 / n).max())
            score = spread + gap / 5.0
            if best is None or score < best[0]:
                best = (score, points, cx, cy, radius, spread, gap)
        _, points, cx, cy, radius, spread, gap = best
        bad = []
        if not lowR <= radius <= highR:
            bad.append(f"circle r {radius:.1f} outside {lowR:g} - {highR:g}")
        if spread > CheckCap.SEATING_HUB_HOLES_SPREAD_MAX:
            bad.append(f"spread {spread:.1f} > {CheckCap.SEATING_HUB_HOLES_SPREAD_MAX}")
        if gap > CheckCap.SEATING_HUB_HOLES_GAP_MAX:
            bad.append(f"angle gap off by {gap:.0f} > {CheckCap.SEATING_HUB_HOLES_GAP_MAX} deg")
        result.update(found=not bad, cx=cx, cy=cy, radius=radius, spread=spread, gap=gap, points=points,
                      reason="; ".join(bad))
        return result

    @staticmethod
    def hubOffset(kind: str | None, model: str | None):
        """The (dx, dy) added to the hub centre for this hub kind (holes / bolts from the hub check; "cap holes" from the
        cap picture) and model (DOST / DOSTPLUS)."""
        plus = model == DOSTPLUS
        if kind == "cap holes":
            return CheckCap.SEATING_HUB_DISTANCE_OFFSET_CAP_DOSTPLUS if plus else CheckCap.SEATING_HUB_DISTANCE_OFFSET_CAP_DOST
        if kind == "bolts":
            return CheckCap.SEATING_HUB_DISTANCE_OFFSET_BOLTS_DOSTPLUS if plus else CheckCap.SEATING_HUB_DISTANCE_OFFSET_BOLTS_DOST
        return CheckCap.SEATING_HUB_DISTANCE_OFFSET_HOLES_DOSTPLUS if plus else CheckCap.SEATING_HUB_DISTANCE_OFFSET_HOLES_DOST

    @staticmethod
    def hubCentreDistance(anImage: np.ndarray, hubCentre: Dict | None, model: str | None = DOST) -> Dict:
        """The hub-centre distance rule (CHANGES 143): dict(applied, ok, distance, limit, reason, cap (CapMaskFinder's
        result), target (hub centre + offset), hub (the hub centre used)). Hub source "cap" (CHANGES 152): the hub's holes
        in this picture - not found -> NOT seated; hubCentre is not used. Source "hubcheck": hubCentre from this cycle's
        hub check; applied False (ok True) without one. applied False also with the rule switched off."""
        from camera import CapMaskFinder
        out = dict(applied=False, ok=True, distance=None, limit=CheckCap.SEATING_HUB_DISTANCE_MAX, reason="",
                   cap=None, target=None, hub=None)
        if not CheckCap.SEATING_HUB_DISTANCE_CHECK:
            return out
        if str(CheckCap.SEATING_HUB_SOURCE).strip().lower() == "cap":
            holes = CheckCap.hubHoles(anImage, model)
            out["applied"] = True
            if not holes["found"]:
                out.update(ok=False, reason=f"cap to hub centre: the hub's {holes['count']} holes round the cap not found "
                                            f"({holes['reason']}; darkest {holes.get('percentile')} %)")
                return out
            hubCentre = dict(cx=holes["cx"], cy=holes["cy"], radius=holes["radius"], kind="cap holes",
                             count=holes["count"], spread=holes["spread"], gap=holes["gap"])
            out["hub"] = hubCentre
        elif not hubCentre:
            out["reason"] = "cap to hub centre not checked (no hub centre from this cycle's hub check)"
            return out
        else:
            out["hub"] = hubCentre
        out["applied"] = True
        cap = CapMaskFinder.findCap(anImage, CheckCap.CENTER, CheckCap._sam_predictor(), CheckCap._yolo_model())
        out["cap"] = cap
        if not cap["found"]:
            out.update(ok=False, reason=f"cap to hub centre: {cap['reason']} ({CapMaskFinder.describe(cap)})")
            return out
        dx, dy = CheckCap.hubOffset(hubCentre.get("kind"), model)
        tx, ty = float(hubCentre["cx"]) + float(dx), float(hubCentre["cy"]) + float(dy)
        cx, cy = cap["centre"]
        distance = float(np.hypot(cx - tx, cy - ty))
        ok = distance <= CheckCap.SEATING_HUB_DISTANCE_MAX
        out.update(ok=ok, distance=distance, target=(tx, ty),
                   reason=(f"cap centre ({cx:.1f}, {cy:.1f}) to hub centre ({tx:.1f}, {ty:.1f}) {distance:.1f} px "
                           f"{'<=' if ok else '>'} {CheckCap.SEATING_HUB_DISTANCE_MAX} (hub from {hubCentre.get('count')} "
                           f"{hubCentre.get('kind')}{CheckCap._holesNote(hubCentre)}, {model} offset ({float(dx):g}, {float(dy):g}), cap "
                           f"{cx - tx:+.1f} / {cy - ty:+.1f} px; {CapMaskFinder.describe(cap)})"))
        return out

    @staticmethod
    def _holesNote(hub: Dict) -> str:
        """' (circle r 151.2, spread 0.4, gap 2 deg)' for the holes found in the cap picture, else ''."""
        if hub.get("kind") != "cap holes":
            return ""
        return f" (circle r {hub['radius']:.1f}, spread {hub['spread']:.1f}, gap {hub['gap']:.0f} deg)"

    @staticmethod
    def checkSeating(anImage: np.ndarray, cap_found: str, hubImage: np.ndarray | None = None,
                     hubCentre: Dict | None = None) -> Dict:
        """Step 4: the seating by the cap's colour (brass / silver rules, _seatingByColour) and the hub-centre distance
        (hubCentreDistance); seated only when both say so. The distance values are added as hub_distance, hub_target,
        cap_ellipse (contour) and hub_centre."""
        result = CheckCap._seatingByColour(anImage, cap_found, hubImage)
        rule = CheckCap.hubCentreDistance(anImage, hubCentre, cap_found)
        if rule["reason"]:
            result["reason"] = (result["reason"] + "; " if result["reason"] else "") + rule["reason"]
        if rule["applied"]:
            from camera import CapMaskFinder
            result["seated"] = bool(result["seated"]) and rule["ok"]
            result["hub_distance"] = rule["distance"]
            result["hub_target"] = rule["target"]
            result["hub_centre"] = rule["hub"]
            hub = rule["hub"] or {}
            if hub.get("kind") == "cap holes":        # the hole circle in the cap picture (blue, thin)
                result["hub_holes"] = dict(contour=cv2.ellipse2Poly((int(round(hub["cx"])), int(round(hub["cy"]))),
                                                                    (int(round(hub["radius"])),) * 2, 0, 0, 360, 4)
                                           .reshape(-1, 1, 2).astype(np.int32))
            fit = (rule["cap"] or {}).get("fit")
            result["cap_ellipse"] = dict(contour=CapMaskFinder.ellipseContour(fit["e2"])) if fit else None
        return result

    @staticmethod
    def _seatingByColour(anImage: np.ndarray, cap_found: str, hubImage: np.ndarray | None = None) -> Dict:
        """Step 4. Returns dict(seated, offset, limit, reason, cap, bore, face, hub; silver also fit, fit_min, hub_visible)
        - contours for the marked picture. Brass: offset = cap centre to bore centre, limit its maximum. Silver: fit = share
        of the top-face edge on one circle (fit_min its minimum), offset = face centre to outline centre (logged only).
        hubImage: this cycle's hub + bottom bearing picture (silver rule 1; without it rule 1 is not applied)."""
        import torch
        result = dict(seated=False, offset=None, limit=None, reason="", cap=None, bore=None, face=None, hub=None)
        if cap_found == DOSTPLUS:
            # brass: circles fitted to the cap edge and the bore edge (CHANGES 128; no MobileSAM)
            cap, bore = CheckCap.brassCapAndBore(anImage)
            result.update(cap=cap, bore=bore, limit=CheckCap.SEATING_BRASS_OFFSET_MAX)
            if cap is None or bore is None:
                result["reason"] = "brass cap edge or bore edge not found" + (
                    "" if cap is not None else " (cap)") + ("" if bore is not None else " (bore)")
                return result
            offset = float(np.hypot(cap["cx"] - bore["cx"], cap["cy"] - bore["cy"]))
            result["offset"] = offset
            result["seated"] = offset <= result["limit"]
            result["reason"] = f"cap centre to bore centre {offset:.1f} px {'<=' if result['seated'] else '>'} {result['limit']}"
            return result
        # silver (CHANGES 129): the cap outline (MobileSAM, point at CENTER), then rule 2 and rule 1
        predictor = CheckCap._sam_predictor()
        predictor.set_image(cv2.cvtColor(anImage, cv2.COLOR_BGR2RGB))
        centre = np.array([[float(CheckCap.CENTER[0]), float(CheckCap.CENTER[1])]], dtype=np.float32)
        with torch.inference_mode():
            masks, _, _ = predictor.predict(point_coords=centre, point_labels=np.array([1]), multimask_output=True)
        fits = [CheckCap._ellipse_of(m) for m in masks]
        cap = CheckCap._pick_by_radius(fits, CheckCap.SEATING_SILVER_RADIUS_MIN, CheckCap.SEATING_SILVER_RADIUS_MAX)
        result.update(cap=cap, fit=None, fit_min=CheckCap.SEATING_SILVER_FACE_FIT_MIN)
        if cap is None:
            result["reason"] = "silver cap outline not found by the segmenter"
            return result
        capMask = masks[next(i for i, f in enumerate(fits) if f is cap)]
        face = CheckCap.silverFaceCircle(anImage, cap)
        result["face"] = face
        fit = face["fit"] if face is not None else 0.0
        result["fit"] = fit
        if face is not None:    # informative only (a seated cap's face already sits ~8 px from the outline centre)
            result["offset"] = float(np.hypot(face["cx"] - cap["cx"], face["cy"] - cap["cy"]))
        face_ok = fit >= CheckCap.SEATING_SILVER_FACE_FIT_MIN
        reasons = [f"top face edge on one circle {fit:.2f} {'>=' if face_ok else '<'} {CheckCap.SEATING_SILVER_FACE_FIT_MIN}"
                   + ("" if face is None else f" (face r {face['radius']:.1f}, {result['offset']:.1f} px from outline centre)")]
        hub_ok = True
        if hubImage is None:
            reasons.append("hub centre not checked (no hub picture)")
        else:
            hub = CheckCap.hubCircle(hubImage)
            result["hub"] = hub
            if hub is None:
                reasons.append("hub centre not checked (hub circle not found in the hub picture)")
            else:
                visible = CheckCap.hubCentreVisible(anImage, capMask, hub)
                result["hub_visible"] = visible
                hub_ok = visible <= CheckCap.SEATING_HUB_VISIBLE_MAX
                reasons.append(f"hub centre uncovered {visible} px {'<=' if hub_ok else '>'} "
                               f"{CheckCap.SEATING_HUB_VISIBLE_MAX} (hub r {hub['radius']:.1f}, cap outline "
                               f"{np.hypot(cap['cx'] - hub['cx'], cap['cy'] - hub['cy']):.1f} px from hub centre)")
        result["seated"] = face_ok and hub_ok
        result["reason"] = "; ".join(reasons)
        if CheckCap.SEATING_SILVER_OUTLINE_OFFSET_MAX > 0:
            outline = float(np.hypot(cap["cx"] - float(CheckCap.CENTER[0]), cap["cy"] - float(CheckCap.CENTER[1])))
            outline_ok = outline <= CheckCap.SEATING_SILVER_OUTLINE_OFFSET_MAX
            result["outline_offset"] = outline
            result["seated"] = result["seated"] and outline_ok
            result["reason"] += (f"; cap outline centre to cap centre {outline:.1f} px "
                                 f"{'<=' if outline_ok else '>'} {CheckCap.SEATING_SILVER_OUTLINE_OFFSET_MAX}")
        return result

    @staticmethod
    def _create_annotated_image(image: np.ndarray,
                                is_cap_present: bool,
                                cap_found: str | None,
                                model_name: str,
                                step1_passed: bool,
                                delta_averages: list,
                                rb_average: float,
                                brightness: float,
                                radius: int,
                                seating: Dict | None = None) -> np.ndarray:
        """The picture for the frontend: shapes only, no text or values (those are logged - checkCap's summary line).
        Comparison circle (green = cap OK, red = NOT OK), silver brightness circle (cyan, thin); with the seating step:
        the bore round a brass cap or the hub circle from the hub picture round a silver cap (blue), the cap (green =
        seated, red = not seated), a silver cap's top-face circle (yellow)."""
        annotated = image.copy()
        centre = (int(CheckCap.CENTER[0]), int(CheckCap.CENTER[1]))
        color = (0, 255, 0) if is_cap_present else (0, 0, 255)
        cv2.circle(annotated, centre, radius, color, 2)
        cv2.circle(annotated, centre, int(CheckCap.SILVER_BRIGHTNESS_RADIUS), (255, 255, 0), 1)
        if seating is not None:
            seat_color = (0, 255, 0) if seating["seated"] else (0, 0, 255)
            for key, col in (("bore", (255, 128, 0)), ("hub", (255, 128, 0)), ("cap", seat_color), ("face", (0, 255, 255)),
                             ("cap_ellipse", seat_color), ("hub_holes", (255, 128, 0))):
                if seating.get(key) is not None:
                    cv2.drawContours(annotated, [seating[key]["contour"]], -1, col, 1 if key == "hub_holes" else 2)
            if seating.get("hub_target") is not None:     # the hub's centre (+ offset): a blue cross, no text
                tx, ty = seating["hub_target"]
                cv2.drawMarker(annotated, (int(round(tx)), int(round(ty))), (255, 128, 0), cv2.MARKER_CROSS, 16, 2)
        return annotated

    @staticmethod
    def checkCap(anImage: np.ndarray,
                 currentPictures: Dict[str, np.ndarray | None] = None,
                 componentQRCode: str | None = DOST, gamma: float = 2.0,
                 runSeating: bool = True, hubCentre: Dict | None = None) -> Tuple[np.ndarray | None, bool]:
        """
        Check for the presence of the Cap.

        runSeating False: steps 1-3 only (no MobileSAM) - the camera server runs them on each of its three pictures,
        then the whole check (with seating) once, on a picture that passed them (2026-11-15).
        hubCentre: this cycle's hub centre (ImageProcessor.hubCentre, from the hub check) for the seating step's
        hub-centre distance (CHANGES 143), used with hub source "hubcheck"; with source "cap" (CHANGES 152) the hub
        centre comes from the holes round the cap in anImage and hubCentre is not used.

        Two-step verification:
        1. Compare with base images - cap should show significant brightness increase
        2. Verify cap color - R - B should be less than threshold

        Args:
            anImage: Current camera image to analyze (Cap image)
            currentPictures: Dictionary containing previously captured images
            componentQRCode: Component QR code for the assembly

        Returns:
            Tuple of:
                - Annotated image showing the detection result
                - Boolean indicating if Cap is present (True) or not (False)
        """
        _src = getFullyQualifiedName(__file__, CheckCap)

        logBoth('logDebug', _src,
                f"CheckCap.checkCap() called with "
                f"anImage = {anImage.shape if anImage is not None else 'None'}",
                MessageType.GENERAL)

        # Basic validation
        if componentQRCode is None:
            logBoth('logError', _src, "CheckCap: componentQRCode is None, returning False", MessageType.ISSUE)
            return anImage, False

        if anImage is None:
            logBoth('logError', _src, "CheckCap: anImage is None, returning False", MessageType.ISSUE)
            return anImage, False

        if currentPictures is None:
            logBoth('logError', _src, "CheckCap: currentPictures is None, returning False", MessageType.ISSUE)
            return anImage, False

        # =====================================================================
        # HAND DETECTION CHECK - First check before any other processing
        # Ensure no operator hands are still in the frame
        # =====================================================================
        # if HandDetector.detectHands(anImage):
        #     logBoth('logError', _src, "CheckCap: HAND DETECTION FAILED - Operator hands detected in image!", MessageType.PROBLEM)
        #     logBoth('logError', _src, "CheckCap: Please ensure hands are completely out of frame before capturing", MessageType.PROBLEM)
        #     return anImage, False
        #
        # logBoth('logInfo', _src, "CheckCap: Hand detection passed - No hands in frame", MessageType.SUCCESS)

        # Get component type from QR code
        modelName, lhs_rhs, _ = getModel_LHSRHS_AndTonnage(componentQRCode)
        logBoth('logDebug', _src, f"Got modelName as {modelName}", MessageType.GENERAL)
        if modelName == UNKNOWN:
            modelName = "DOST"

        # Get base images
        hub_bottom_bearing_image = currentPictures.get(hubAndBottomBearingPictureKeyString, None)
        top_bearing_image = currentPictures.get(topBearingPictureKeyString, None)
        nut_platewasher_image = currentPictures.get(nutAndPlateWasherPictureKeyString, None)

        base_images = [
            ("Hub+Bottom Bearing", hub_bottom_bearing_image),
            ("Top Bearing", top_bearing_image),
            ("Nut+Plate Washer", nut_platewasher_image)
        ]

        missing_images = [name for name, img in base_images if img is None]
        if missing_images:
            logBoth('logError', _src, f"CheckCap: Missing base images: {missing_images}, returning False", MessageType.ISSUE)
            return anImage, False

        try:
            # =====================================================================
            # STEP 1: Which cap is on the part? Brass first, then silver, else none
            # =====================================================================
            brass_mask = CheckCap._get_circular_mask(anImage.shape, CheckCap.RADIUS_DOSTPLUS)
            rb_brass = CheckCap._compute_rb_difference(anImage, brass_mask)
            silver_mask = CheckCap._get_circular_mask(anImage.shape, CheckCap.RADIUS_DOST)
            rb_silver = CheckCap._compute_rb_difference(anImage, silver_mask)
            brightness_mask = CheckCap._get_circular_mask(anImage.shape, CheckCap.SILVER_BRIGHTNESS_RADIUS)
            brightness = float(np.mean(anImage[brightness_mask > 0]))

            if rb_brass > CheckCap.RB_THRESHOLD_DOSTPLUS:
                cap_found = DOSTPLUS
                logBoth('logInfo', _src,
                        f"CheckCap: brass cap found - R-B={rb_brass:.1f} > {CheckCap.RB_THRESHOLD_DOSTPLUS} (r{CheckCap.RADIUS_DOSTPLUS})",
                        MessageType.SUCCESS)
            elif rb_silver < CheckCap.RB_THRESHOLD_DOST and brightness >= CheckCap.SILVER_BRIGHTNESS_MIN:
                cap_found = DOST
                logBoth('logInfo', _src,
                        f"CheckCap: silver cap found - R-B={rb_silver:.1f} < {CheckCap.RB_THRESHOLD_DOST} (r{CheckCap.RADIUS_DOST}), "
                        f"brightness={brightness:.1f} >= {CheckCap.SILVER_BRIGHTNESS_MIN} (r{CheckCap.SILVER_BRIGHTNESS_RADIUS})",
                        MessageType.SUCCESS)
            else:
                cap_found = None
                logBoth('logError', _src,
                        f"CheckCap: no cap found - not brass (R-B={rb_brass:.1f} <= {CheckCap.RB_THRESHOLD_DOSTPLUS}, r{CheckCap.RADIUS_DOSTPLUS}), "
                        f"not silver (R-B={rb_silver:.1f} vs < {CheckCap.RB_THRESHOLD_DOST}, r{CheckCap.RADIUS_DOST}; "
                        f"brightness={brightness:.1f} vs >= {CheckCap.SILVER_BRIGHTNESS_MIN}, r{CheckCap.SILVER_BRIGHTNESS_RADIUS})",
                        MessageType.ISSUE)

            # =====================================================================
            # STEP 2: Is it the cap this model needs? (DOST: silver, DOSTPLUS: brass)
            # =====================================================================
            right_cap = cap_found == modelName
            if cap_found is not None and not right_cap:
                logBoth('logError', _src,
                        f"CheckCap: WRONG CAP - {'brass' if cap_found == DOSTPLUS else 'silver'} cap on a {modelName} part",
                        MessageType.ISSUE)

            # =====================================================================
            # STEP 3: The cap must brighten the circle against each earlier picture of the part (limits of the cap found)
            # =====================================================================
            delta_averages = []
            step1_passed = False
            radius = CheckCap._get_radius_for_component(cap_found if cap_found is not None else modelName)
            if cap_found is not None:
                mask = CheckCap._get_circular_mask(anImage.shape, radius)
                if cap_found == DOST:
                    thresholds = [
                        CheckCap.DELTA_THRESHOLD_HUB_DOST,
                        CheckCap.DELTA_THRESHOLD_TOP_DOST,
                        CheckCap.DELTA_THRESHOLD_NUT_DOST
                    ]
                else:
                    thresholds = [
                        CheckCap.DELTA_THRESHOLD_HUB_DOSTPLUS,
                        CheckCap.DELTA_THRESHOLD_TOP_DOSTPLUS,
                        CheckCap.DELTA_THRESHOLD_NUT_DOSTPLUS
                    ]
                step1_passed = True
                for (name, base_image), threshold in zip(base_images, thresholds):
                    _, delta_avg = CheckCap._compute_clamped_delta_grayscale(
                        anImage, base_image, mask
                    )
                    delta_averages.append(delta_avg)

                    if delta_avg < threshold:
                        step1_passed = False
                        logBoth('logError', _src,
                                f"CheckCap comparison: {name} delta={delta_avg:.1f} < threshold={threshold}",
                                MessageType.ISSUE)
                    else:
                        logBoth('logInfo', _src,
                                f"CheckCap comparison: {name} delta={delta_avg:.1f} >= threshold={threshold}",
                                MessageType.SUCCESS)

            # =====================================================================
            # STEP 4: Is the cap seated? (MobileSAM; only when steps 2 and 3 passed)
            # =====================================================================
            seating = None
            seated = True
            if CheckCap.SEATING_CHECK and runSeating and right_cap and step1_passed:
                try:
                    seating = CheckCap.checkSeating(anImage, cap_found, hub_bottom_bearing_image, hubCentre)
                    seated = seating["seated"]
                except Exception as e:
                    seated = False
                    seating = dict(seated=False, offset=None, limit=None, cap=None, bore=None, face=None, hub=None,
                                   reason=f"segmenter error: {e}")
                logBoth('logInfo' if seated else 'logError', _src,
                        f"CheckCap seating: {'SEATED' if seated else 'NOT SEATED'} - {seating['reason']}",
                        MessageType.SUCCESS if seated else MessageType.ISSUE)

            # =====================================================================
            # Final Decision
            # =====================================================================
            is_cap_present = right_cap and step1_passed and seated

            if is_cap_present:
                logBoth('logInfo', _src, "CheckCap: CAP DETECTED - right cap, all comparisons passed", MessageType.SUCCESS)
            else:
                logBoth('logError', _src, "CheckCap: CAP NOT DETECTED - " +
                        ("no cap" if cap_found is None else "wrong cap" if not right_cap
                         else "comparisons failed" if not step1_passed else "cap not seated"),
                        MessageType.ISSUE)

            # One summary line with every value (the picture for the frontend carries no text)
            found = {DOSTPLUS: "brass", DOST: "silver"}.get(cap_found, "none")
            summary = (f"CheckCap summary: {'OK' if is_cap_present else 'NOT OK'} - model {modelName}, cap found {found}, "
                       f"R-B r{CheckCap.RADIUS_DOSTPLUS} {rb_brass:.1f} / r{CheckCap.RADIUS_DOST} {rb_silver:.1f}, "
                       f"brightness r{CheckCap.SILVER_BRIGHTNESS_RADIUS} {brightness:.0f}")
            if delta_averages:
                summary += ", brightening hub/top/nut " + "/".join(f"{d:.1f}" for d in delta_averages)
            if seating is not None:
                summary += f", seating: {seating['reason']}"
            logBoth('logInfo', _src, summary, MessageType.GENERAL)

            # Create annotated output image
            annotated_image = CheckCap._create_annotated_image(
                anImage, is_cap_present, cap_found, modelName, step1_passed,
                delta_averages, rb_brass if cap_found == DOSTPLUS else rb_silver, brightness, radius, seating
            )

            return annotated_image, is_cap_present

        except Exception as e:
            logBoth('logCritical', _src, f"CheckCap: Exception during detection: {e}", MessageType.PROBLEM)
            import traceback
            traceback.print_exc()
            return anImage, False

    @staticmethod
    def test(directory_path: str):
        """
        Test the CheckCap algorithm on images in the specified directory.

        Args:
            directory_path: Path to directory containing test images
        """
        GREEN = "\033[1;32m"
        RED = "\033[1;31m"
        YELLOW = "\033[1;33m"
        CYAN = "\033[1;36m"
        RESET = "\033[0m"

        print(f"{CYAN}{'=' * 80}{RESET}")
        print(f"{CYAN}CheckCap Test Suite{RESET}")
        print(f"{CYAN}{'=' * 80}{RESET}")
        print(f"\nDirectory: {directory_path}")
        print(f"Parameters:")
        print(f"  Center: {CheckCap.CENTER}")
        if DOSTPLUS in directory_path:
            print("In DOSTPLUS path")
            print(f"  Radius DOSTPLUS: {CheckCap.RADIUS_DOSTPLUS}")
            print(f"  Delta Thresholds hub/top/nut: {CheckCap.DELTA_THRESHOLD_HUB_DOSTPLUS}/{CheckCap.DELTA_THRESHOLD_TOP_DOSTPLUS}/{CheckCap.DELTA_THRESHOLD_NUT_DOSTPLUS}")
            print(f"  R-B Threshold: {CheckCap.RB_THRESHOLD_DOSTPLUS}")
        else:
            print("In DOST path")
            print(f"  Radius DOST: {CheckCap.RADIUS_DOST}")
            print(f"  Delta Thresholds hub/top/nut: {CheckCap.DELTA_THRESHOLD_HUB_DOST}/{CheckCap.DELTA_THRESHOLD_TOP_DOST}/{CheckCap.DELTA_THRESHOLD_NUT_DOST}")
            print(f"  R-B Threshold: {CheckCap.RB_THRESHOLD_DOST}")


        # Find all PNG files
        all_files = glob.glob(os.path.join(directory_path, "*.png"))

        # Categorize files into lists
        list_cap = sorted([f for f in all_files if "Cap" in os.path.basename(f)])
        # Test against previous stage image
        # list_cap = sorted([f for f in all_files if "Nut" in os.path.basename(f)])
        list_hub_and_bottom_bearing = sorted(
            [f for f in all_files if "Hub_and" in os.path.basename(f) or "Hub and" in os.path.basename(f)])
        list_top_bearing = sorted([f for f in all_files if "Top" in os.path.basename(f)])
        list_nut_and_plate_washer = sorted([f for f in all_files if "Nut" in os.path.basename(f)])

        print(f"\n{YELLOW}Found files:{RESET}")
        print(f"  Cap: {len(list_cap)}")
        print(f"  Hub and Bottom Bearing: {len(list_hub_and_bottom_bearing)}")
        print(f"  Top Bearing: {len(list_top_bearing)}")
        print(f"  Nut and Plate Washer: {len(list_nut_and_plate_washer)}")

        if not list_hub_and_bottom_bearing:
            print(f"{RED}No Hub and Bottom Bearing images found!{RESET}")
            return

        # Load all images
        print(f"\n{YELLOW}Loading images...{RESET}")

        cap_images = [(f, cv2.imread(f)) for f in list_cap]
        hub_images = [(f, cv2.imread(f)) for f in list_hub_and_bottom_bearing]
        top_images = [(f, cv2.imread(f)) for f in list_top_bearing]
        nut_images = [(f, cv2.imread(f)) for f in list_nut_and_plate_washer]

        # Use first image of each type as base for testing
        hub_base = hub_images[0][1] if hub_images else None
        top_base = top_images[0][1] if top_images else None
        nut_base = nut_images[0][1] if nut_images else None

        # =====================================================================
        # TEST 1: Cap images against all 3 base lists
        # =====================================================================
        if list_cap:
            print(f"\n{CYAN}{'=' * 80}{RESET}")
            print(f"{CYAN}TEST 1: Cap images (tested against Hub+Bottom, Top, Nut+Washer){RESET}")
            print(f"{CYAN}{'=' * 80}{RESET}")
            print(
                f"{YELLOW}Base images: {os.path.basename(hub_images[0][0])}, {os.path.basename(top_images[0][0])}, {os.path.basename(nut_images[0][0])}{RESET}\n")

            cap_pass = 0
            cap_fail = 0

            for cap_file, cap_img in cap_images:
                currentPictures = {
                    hubAndBottomBearingPictureKeyString: hub_base,
                    topBearingPictureKeyString: top_base,
                    nutAndPlateWasherPictureKeyString: nut_base
                }

                result = False
                if DOSTPLUS in directory_path:
                    _, result = CheckCap.checkCap(cap_img, currentPictures, "8201206$400112VA1C$11770$09.03.2025$C5211701$MEK$40Cr4 - B$1$SPDLASSY - KNULH$SNPL - MAT$")
                elif DOST in directory_path:
                    _, result = CheckCap.checkCap(cap_img, currentPictures, DOST)

                if result:
                    cap_pass += 1
                    print(f"  {os.path.basename(cap_file):45s} {GREEN}Cap Present{RESET}")
                else:
                    cap_fail += 1
                    print(f"  {os.path.basename(cap_file):45s} {RED}Cap Not Present{RESET}")

            print(f"\n  Summary: {GREEN}{cap_pass} Cap Present{RESET}, {RED}{cap_fail} Cap Not Present{RESET}")

        # =====================================================================
        # TEST 2: Nut+Plate Washer images against 2 base lists (Hub+Bottom, Top)
        # =====================================================================
        if list_nut_and_plate_washer and list_top_bearing:
            print(f"\n{CYAN}{'=' * 80}{RESET}")
            print(f"{CYAN}TEST 2: Nut+Plate Washer images (tested against Hub+Bottom, Top){RESET}")
            print(f"{CYAN}{'=' * 80}{RESET}")
            print(
                f"{YELLOW}Base images: {os.path.basename(hub_images[0][0])}, {os.path.basename(top_images[0][0])}{RESET}\n")

            nut_pass = 0
            nut_fail = 0

            for nut_file, nut_img in nut_images:
                currentPictures = {
                    hubAndBottomBearingPictureKeyString: hub_base,
                    topBearingPictureKeyString: top_base,
                    nutAndPlateWasherPictureKeyString: nut_base  # Use same nut base as reference
                }

                result = False
                if DOSTPLUS in directory_path:
                    _, result = CheckCap.checkCap(nut_img, currentPictures,
                                                  "8201206$400112VA1C$11770$09.03.2025$C5211701$MEK$40Cr4 - B$1$SPDLASSY - KNULH$SNPL - MAT$")
                elif DOST in directory_path:
                    _, result = CheckCap.checkCap(nut_img, currentPictures, "DOST")

                if result:
                    nut_pass += 1
                    print(f"  {os.path.basename(nut_file):45s} {GREEN}Cap Present{RESET}")
                else:
                    nut_fail += 1
                    print(f"  {os.path.basename(nut_file):45s} {RED}Cap Not Present{RESET}")

            print(f"\n  Summary: {GREEN}{nut_pass} Cap Present{RESET}, {RED}{nut_fail} Cap Not Present{RESET}")

        # =====================================================================
        # TEST 3: Top Bearing images against 1 base list (Hub+Bottom)
        # =====================================================================
        if list_top_bearing:
            print(f"\n{CYAN}{'=' * 80}{RESET}")
            print(f"{CYAN}TEST 3: Top Bearing images (tested against Hub+Bottom){RESET}")
            print(f"{CYAN}{'=' * 80}{RESET}")
            print(f"{YELLOW}Base image: {os.path.basename(hub_images[0][0])}{RESET}\n")

            top_pass = 0
            top_fail = 0

            for top_file, top_img in top_images:
                currentPictures = {
                    hubAndBottomBearingPictureKeyString: hub_base,
                    topBearingPictureKeyString: top_base,  # Use same top base as reference
                    nutAndPlateWasherPictureKeyString: nut_base  # Use same nut base as reference
                }

                if DOSTPLUS in directory_path:
                    _, result = CheckCap.checkCap(top_img, currentPictures,
                                                  "8201206$400112VA1C$11770$09.03.2025$C5211701$MEK$40Cr4 - B$1$SPDLASSY - KNULH$SNPL - MAT$")
                elif DOST in directory_path:
                    _, result = CheckCap.checkCap(top_img, currentPictures, "DOST")

                if result:
                    top_pass += 1
                    print(f"  {os.path.basename(top_file):45s} {GREEN}Cap Present{RESET}")
                else:
                    top_fail += 1
                    print(f"  {os.path.basename(top_file):45s} {RED}Cap Not Present{RESET}")

            print(f"\n  Summary: {GREEN}{top_pass} Cap Present{RESET}, {RED}{top_fail} Cap Not Present{RESET}")

        # =====================================================================
        # FINAL SUMMARY
        # =====================================================================
        print(f"\n{CYAN}{'=' * 80}{RESET}")
        print(f"{CYAN}FINAL SUMMARY{RESET}")
        print(f"{CYAN}{'=' * 80}{RESET}")

        if list_cap:
            print(f"  Cap images:              {cap_pass}/{len(list_cap)} detected as Cap Present")
        if list_nut_and_plate_washer:
            print(f"  Nut+Plate Washer images: {nut_pass}/{len(list_nut_and_plate_washer)} detected as Cap Present")
        if list_top_bearing:
            print(f"  Top Bearing images:      {top_pass}/{len(list_top_bearing)} detected as Cap Present")


# if __name__ == "__main__":
#     import sys
#
#     if len(sys.argv) > 1:
#         CheckCap.test(sys.argv[1])
#     else:
#         CheckCap.test("C:/AshokLeylandImages/DOSTPLUS/LHS")