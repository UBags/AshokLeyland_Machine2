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
          its body in the bore but its top slides over and the side wall shows on one side).
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
    CENTER = (635, 360)               # centre of the circle the check looks at
    RADIUS_DOST = 100
    RADIUS_DOSTPLUS = 70
    RB_THRESHOLD_DOST = -1.0            # Step 2: R-B must be below this
    RB_THRESHOLD_DOSTPLUS = 15.0    # Step 2: R-B must be above this

    # Step 1: the cap picture must be brighter than each earlier picture of the same part by at least these
    DELTA_THRESHOLD_HUB_DOST = 30.0
    DELTA_THRESHOLD_TOP_DOST = 30.0
    DELTA_THRESHOLD_NUT_DOST = 35.0

    DELTA_THRESHOLD_HUB_DOSTPLUS = 35.0
    DELTA_THRESHOLD_TOP_DOSTPLUS = 50.0
    DELTA_THRESHOLD_NUT_DOSTPLUS = 35.0

    # Silver cap test (after the brass test fails): mean brightness (B+G+R)/3 within this radius must reach the
    # minimum. A silver cap is bright in the middle; without a cap the bore and shaft end are dark there.
    # 0 = test off (every non-brass picture with a silver R-B counts as a silver cap, as before).
    SILVER_BRIGHTNESS_RADIUS = 25
    SILVER_BRIGHTNESS_MIN = 0.0

    # Step 4: seating (MobileSAM). Off = the check as before (Machine 1).
    SEATING_CHECK = False
    SEATING_BRASS_RADIUS_MIN = 75.0    # brass cap face mask
    SEATING_BRASS_RADIUS_MAX = 95.0
    SEATING_BORE_RADIUS_MIN = 104.0     # bore round a brass cap
    SEATING_BORE_RADIUS_MAX = 120.0
    SEATING_SILVER_RADIUS_MIN = 95.0  # silver cap outline mask
    SEATING_SILVER_RADIUS_MAX = 112.0
    SEATING_BRASS_OFFSET_MAX = 9.0     # cap centre to bore centre
    SEATING_SILVER_FACE_OFFSET_MAX = 9.0  # face to outline
    SEATING_FACE_BOX_SCALE = 0.8

    # Cached circular masks, keyed on (image size, centre, radius)
    _circular_masks = {}

    # One MobileSAM predictor per thread, on the shared model (ModelManager); the model loads on first use.
    # samModelProvider can be replaced (tests); it returns a MobileSAM model.
    _samLocal = threading.local()
    samModelProvider = None

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

    @staticmethod
    def checkSeating(anImage: np.ndarray, cap_found: str) -> Dict:
        """Step 4. Returns dict(seated, offset, limit, reason, cap, bore, face) - contours for the marked picture."""
        import torch
        predictor = CheckCap._sam_predictor()
        predictor.set_image(cv2.cvtColor(anImage, cv2.COLOR_BGR2RGB))
        centre = np.array([[float(CheckCap.CENTER[0]), float(CheckCap.CENTER[1])]], dtype=np.float32)
        with torch.inference_mode():
            masks, _, _ = predictor.predict(point_coords=centre, point_labels=np.array([1]), multimask_output=True)
        fits = [CheckCap._ellipse_of(m) for m in masks]
        result = dict(seated=False, offset=None, limit=None, reason="", cap=None, bore=None, face=None)
        if cap_found == DOSTPLUS:
            cap = CheckCap._pick_by_radius(fits, CheckCap.SEATING_BRASS_RADIUS_MIN, CheckCap.SEATING_BRASS_RADIUS_MAX)
            bore = CheckCap._pick_by_radius([f for f in fits if f is not cap], CheckCap.SEATING_BORE_RADIUS_MIN,
                                            CheckCap.SEATING_BORE_RADIUS_MAX)
            result.update(cap=cap, bore=bore, limit=CheckCap.SEATING_BRASS_OFFSET_MAX)
            if cap is None or bore is None:
                result["reason"] = "brass cap or bore not found by the segmenter"
                return result
            offset = float(np.hypot(cap["cx"] - bore["cx"], cap["cy"] - bore["cy"]))
            what = "cap centre to bore centre"
        else:
            cap = CheckCap._pick_by_radius(fits, CheckCap.SEATING_SILVER_RADIUS_MIN, CheckCap.SEATING_SILVER_RADIUS_MAX)
            result.update(cap=cap, limit=CheckCap.SEATING_SILVER_FACE_OFFSET_MAX)
            if cap is None:
                result["reason"] = "silver cap outline not found by the segmenter"
                return result
            r = cap["radius"] * CheckCap.SEATING_FACE_BOX_SCALE
            box = np.array([cap["cx"] - r, cap["cy"] - r, cap["cx"] + r, cap["cy"] + r], dtype=np.float32)
            with torch.inference_mode():
                masks, _, _ = predictor.predict(point_coords=np.array([[cap["cx"], cap["cy"]]], dtype=np.float32),
                                                point_labels=np.array([1]), box=box, multimask_output=True)
            faces = [f for f in (CheckCap._ellipse_of(m) for m in masks) if f is not None]
            face = min(faces, key=lambda f: f["area"]) if faces else None
            result["face"] = face
            if face is None:
                result["reason"] = "silver cap top face not found by the segmenter"
                return result
            offset = float(np.hypot(face["cx"] - cap["cx"], face["cy"] - cap["cy"]))
            what = "top face centre to cap outline centre"
        result["offset"] = offset
        result["seated"] = offset <= result["limit"]
        result["reason"] = f"{what} {offset:.1f} px {'<=' if result['seated'] else '>'} {result['limit']}"
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
        """Create an annotated version of the input image showing the detection result."""
        annotated = image.copy()
        centre = (int(CheckCap.CENTER[0]), int(CheckCap.CENTER[1]))

        # Draw the circular regions: the comparison circle and the silver brightness circle
        color = (0, 255, 0) if is_cap_present else (0, 0, 255)
        cv2.circle(annotated, centre, radius, color, 2)
        cv2.circle(annotated, centre, int(CheckCap.SILVER_BRIGHTNESS_RADIUS), (255, 255, 0), 1)

        # Add result text
        result_text = "CAP: PRESENT" if is_cap_present else "CAP: NOT DETECTED"
        cv2.putText(annotated, result_text, (50, 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 3)

        # Cap found against the cap this model needs
        found = {DOSTPLUS: "brass", DOST: "silver"}.get(cap_found, "none")
        needed = "brass" if model_name == DOSTPLUS else "silver"
        cap_color = (0, 255, 0) if cap_found == model_name else (0, 0, 255)
        cv2.putText(annotated, f"Cap found: {found} (needed: {needed})", (50, 90),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, cap_color, 2)
        cv2.putText(annotated, f"  R-B={rb_average:.1f}  brightness r{CheckCap.SILVER_BRIGHTNESS_RADIUS}={brightness:.0f}",
                    (50, 115), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

        # Add Step 1 details
        if delta_averages:
            step1_color = (0, 255, 0) if step1_passed else (0, 0, 255)
            cv2.putText(annotated, f"Step1: {'PASS' if step1_passed else 'FAIL'}", (50, 150),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, step1_color, 2)
            labels = ["Hub+Bot", "TopBrg", "Nut+PW"]
            for i, (label, avg) in enumerate(zip(labels, delta_averages)):
                cv2.putText(annotated, f"  {label}: {avg:.1f}", (50, 180 + i * 25),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

        # Step 4: seating - cap / bore / top face outlines and the verdict
        if seating is not None:
            seat_color = (0, 255, 0) if seating["seated"] else (0, 0, 255)
            for key, col in (("bore", (255, 128, 0)), ("cap", seat_color), ("face", (0, 255, 255))):
                if seating.get(key) is not None:
                    cv2.drawContours(annotated, [seating[key]["contour"]], -1, col, 2)
            cv2.putText(annotated, f"Seating: {'SEATED' if seating['seated'] else 'NOT SEATED'}", (50, 270),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, seat_color, 2)
            cv2.putText(annotated, f"  {seating['reason']}", (50, 295), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (255, 255, 255), 2)

        return annotated

    @staticmethod
    def checkCap(anImage: np.ndarray,
                 currentPictures: Dict[str, np.ndarray | None] = None,
                 componentQRCode: str | None = DOST, gamma: float = 2.0) -> Tuple[np.ndarray | None, bool]:
        """
        Check for the presence of the Cap.

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
            if CheckCap.SEATING_CHECK and right_cap and step1_passed:
                try:
                    seating = CheckCap.checkSeating(anImage, cap_found)
                    seated = seating["seated"]
                except Exception as e:
                    seated = False
                    seating = dict(seated=False, offset=None, limit=None, cap=None, bore=None, face=None,
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