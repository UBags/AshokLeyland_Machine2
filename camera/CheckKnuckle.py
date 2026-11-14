import copy
from threading import Thread
from queue import *
import cv2
import os
import glob
import numpy as np
from typing import Dict, Tuple, Optional, Union

from utils.CosThetaFileUtils import *
from utils.RedisUtils import *
from BaseUtils import *
from logutils.SlaveLoggers import logBoth
from logutils.Logger import MessageType
from concurrent.futures import ThreadPoolExecutor, wait
from utils.QRCodeHelper import getModel_LHSRHS_AndTonnage, UNKNOWN
# from HandDetector import HandDetector

import warnings

warnings.filterwarnings('ignore', '.*h264.*', )

from Configuration import *

CosThetaConfigurator.getInstance()

# Global debug flag - will be linked to Configuration parameter later
CHECK_KNUCKLE_IMAGE_DEBUG: bool = False


class CheckKnuckle():
    """
    Check Knuckle - For State 3 (READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE)

    This class verifies that the knuckle placed on the fixture matches
    the QR code scanned in the previous step.

    Every region, threshold and filter size below is read from ApplicationConfiguration.properties, per machine:
    <vendor>.vision.knuckle.* (allenbradley.vision.knuckle.* for Machine 1, siemens.vision.knuckle.* for Machine 2).
    The numbers in the code are only the defaults used when a key is missing.

    Validation steps:
    Common checks A-E: the mean grey level of each region (rectangles A, B, E; circles C, D) must be below
        vision.knuckle.common.check.threshold. They check that the expected dark features are where they should be
        (knuckle casting next to the bosses, dark fixture features), i.e. a knuckle is there and the camera has
        not moved. Each region can have an alternate area (vision.knuckle.common.check.<a..e>.alt.*; width / height
        / radius 0 = none): operators mark knuckles with yellow paint at random spots, and a mark makes a region
        bright; the region passes if it OR its alternate is dark.
    1. LHS / RHS: the knuckle's steering arm lies across the bright bar at the bottom of the fixture, on the left
       for an LHS knuckle and on the right for an RHS one. Each side box is binarised and mean-filtered; an average
       below vision.knuckle.lhs.rhs.darkness.threshold means the arm is there. Exactly one side must show the arm
       (both dark = something else on the bar: rejected), and it must match the QR code.
    With vision.knuckle.side.by.difference = True (Machine 2), check A and step 1 work by differences instead: contour A
       (left of the hub) and contour A' (right of the hub) lie just above where the RHS / LHS knuckle's casting ends
       between the bosses; the darker one tells the side, and they must differ by more than
       vision.knuckle.side.difference.threshold (the bare plate of an empty fixture shows in both). The side boxes
       (D1 = lhs.crop, D2 = rhs.crop) must also differ by more than that threshold (the arm darkens one of them), and
       both comparisons must give the same side, which must match the QR code. The rectangle A, its alternate and
       vision.knuckle.lhs.rhs.darkness.threshold are then not used.
    2./3. DOST / DOSTPLUS: two polygons next to the two kingpin bosses (vision.knuckle.step2.polygon.points and
       vision.knuckle.step3.polygon.points), binarised and mean-filtered. The DOSTPLUS boss face reaches into them
       (bright), the DOST casting does not (dark): an average below
       vision.knuckle.component.identification.darkness.threshold means DOST. Both must agree with the QR code.
    """

    # Region of Interest for LHS detection
    LHS_CROP_X: int = 380
    LHS_CROP_Y: int = 650
    LHS_CROP_WIDTH: int = 130
    LHS_CROP_HEIGHT: int = 50

    # Region of Interest for RHS detection
    RHS_CROP_X: int = 765
    RHS_CROP_Y: int = 650
    RHS_CROP_WIDTH: int = 100
    RHS_CROP_HEIGHT: int = 55

    # Common check regions (apply to all knuckles)
    COMMON_CHECK_A_X: int = 775
    COMMON_CHECK_A_Y: int = 300
    COMMON_CHECK_A_WIDTH: int = 35
    COMMON_CHECK_A_HEIGHT: int = 25

    COMMON_CHECK_B_X: int = 440
    COMMON_CHECK_B_Y: int = 310
    COMMON_CHECK_B_WIDTH: int = 40
    COMMON_CHECK_B_HEIGHT: int = 20

    # Threshold for common checks
    COMMON_CHECK_THRESHOLD: int = 84

    # Common check circular regions (apply to all knuckles)
    COMMON_CHECK_C_CENTER_X: int = 390
    COMMON_CHECK_C_CENTER_Y: int = 465
    COMMON_CHECK_C_RADIUS: int = 13

    COMMON_CHECK_D_CENTER_X: int = 865
    COMMON_CHECK_D_CENTER_Y: int = 465
    COMMON_CHECK_D_RADIUS: int = 13

    # Common check rectangular region (apply to all knuckles)
    COMMON_CHECK_E_X: int = 600
    COMMON_CHECK_E_Y: int = 600
    COMMON_CHECK_E_WIDTH: int = 50
    COMMON_CHECK_E_HEIGHT: int = 20

    # Alternate area of each common check (same rule; the check passes if the region OR its alternate is dark).
    # A width, height or radius of 0 means no alternate (the default).
    COMMON_CHECK_A_ALT_X: int = 0
    COMMON_CHECK_A_ALT_Y: int = 0
    COMMON_CHECK_A_ALT_WIDTH: int = 0
    COMMON_CHECK_A_ALT_HEIGHT: int = 0
    COMMON_CHECK_B_ALT_X: int = 0
    COMMON_CHECK_B_ALT_Y: int = 0
    COMMON_CHECK_B_ALT_WIDTH: int = 0
    COMMON_CHECK_B_ALT_HEIGHT: int = 0
    COMMON_CHECK_C_ALT_CENTER_X: int = 0
    COMMON_CHECK_C_ALT_CENTER_Y: int = 0
    COMMON_CHECK_C_ALT_RADIUS: int = 0
    COMMON_CHECK_D_ALT_CENTER_X: int = 0
    COMMON_CHECK_D_ALT_CENTER_Y: int = 0
    COMMON_CHECK_D_ALT_RADIUS: int = 0
    COMMON_CHECK_E_ALT_X: int = 0
    COMMON_CHECK_E_ALT_Y: int = 0
    COMMON_CHECK_E_ALT_WIDTH: int = 0
    COMMON_CHECK_E_ALT_HEIGHT: int = 0

    # Polygon points for Step 2 verification
    STEP2_POLYGON_POINTS = np.array([
        [480, 260],
        [490, 228],
        [495, 270],
        [460, 290]
    ], dtype=np.int32)

    # Polygon points for Step 3 - DOSTPLUS vs DOST determination
    STEP3_POLYGON_POINTS = np.array([
        [785, 265],
        [820, 290],
        [770, 280],
        [760, 235]
    ], dtype=np.int32)

    # Threshold for dark region detection (below this = dark/present)
    LHS_RHS_DARKNESS_THRESHOLD: int = 220
    COMPONENT_IDENTIFICATION_DARKNESS_THRESHOLD: int = 96

    # Binarisation threshold: grey levels below this become black, the rest white
    BINARIZATION_THRESHOLD: int = 48

    # Mean filter kernel size
    KERNEL_SIZE: Tuple[int, int] = (5, 5)

    # Side and presence by differences (vision.knuckle.side.by.difference = True). Two comparisons decide the side and
    # whether a knuckle is there; both must agree:
    #   (a) contour A (vision.knuckle.common.check.a.polygon.points, between the bosses left of the hub, just above
    #       where the RHS knuckle's casting ends) and contour A' (vision.knuckle.common.check.a.alt.polygon.points,
    #       right of the hub, just above where the LHS knuckle's casting ends). The LHS knuckle's casting reaches
    #       higher on the left and darkens A; the RHS knuckle's reaches higher on the right and darkens A'.
    #       mean(A') - mean(A) > vision.knuckle.side.difference.threshold -> LHS;
    #       mean(A) - mean(A') > vision.knuckle.side.difference.threshold -> RHS;
    #       otherwise (no knuckle: both show the bare plate) -> NOT OK.
    #   (b) side boxes D1 (vision.knuckle.lhs.crop.*) and D2 (vision.knuckle.rhs.crop.*) on the bar where the steering
    #       arm rests, mean grey level: mean(D1) - mean(D2) > the same threshold -> RHS (arm on D2);
    #       mean(D2) - mean(D1) > it -> LHS; otherwise -> NOT OK.
    # Then the rectangle A and its alternate, and vision.knuckle.lhs.rhs.darkness.threshold, are not used.
    # A polygon 0,0;0,0;0,0 means not set.
    SIDE_BY_DIFFERENCE: bool = False
    SIDE_DIFFERENCE_THRESHOLD: int = 10
    COMMON_CHECK_A_POLYGON_POINTS = [(0, 0), (0, 0), (0, 0)]
    COMMON_CHECK_A_ALT_POLYGON_POINTS = [(0, 0), (0, 0), (0, 0)]

    # Which knuckle check runs (vision.knuckle.method):
    #   regions       - the grey-level regions above (checkKnuckleByRegions; Machine 1, and Machine 2 until CHANGES 102)
    #   segmentation  - MobileSAM finds the spindle, the 2 machined bosses (with the fixture pins in them) and the lower
    #                   arm (camera/KnuckleLocator.py); type and side from them; also gives the frame that places the
    #                   later checks' centres (camera/VisionFrame.py). Machine 2 from CHANGES 102.
    METHOD: str = "regions"
    # Segmentation: largest camera movement accepted (measured from the fixture pins). The later checks' search areas
    # are 80-250 px; beyond these the camera has been knocked and must be set again (NOT OK, "camera moved").
    FRAME_MAX_SHIFT: float = 40.0            # px
    FRAME_MAX_ROTATION: float = 3.0       # degrees
    FRAME_MAX_ZOOM_CHANGE: float = 0.05  # 0.05 = +-5 %
    # The locator (created on first use; a dry run can set one with its own MobileSAM predictor)
    locator = None
    # The last segmentation result (for the camera server: the frame for the later checks; for dry runs: the values)
    lastResult: Dict = None

    @staticmethod
    def _show_debug_step3(original_image: np.ndarray, polygon_points: np.ndarray,
                          cropped_region: np.ndarray, mean_filtered: np.ndarray,
                          cropped_mask: np.ndarray, average_value: float, detected_model: str):
        """
        Display a 2x2 debug window showing Step 3 polygon analysis.

        Args:
            original_image: The full original BGR image
            polygon_points: The polygon points used for Step 3
            cropped_region: The cropped bounding rectangle region (BGR)
            mean_filtered: The mean filtered binarized image
            cropped_mask: The polygon mask for the cropped region
            average_value: The calculated average pixel value
            detected_model: The detected model name (DOST/DOSTPLUS)
        """
        # Get dimensions - half of original image size
        orig_h, orig_w = original_image.shape[:2]
        cell_width = orig_w // 2  # 640 for 1280 wide image
        cell_height = orig_h // 2  # 360 for 720 high image

        # Create the canvas for 2x2 grid
        canvas = np.zeros((cell_height * 2, cell_width * 2, 3), dtype=np.uint8)

        # =========================================================
        # (a) Top-left: Original image (half size) with polygon marked
        # =========================================================
        orig_half = cv2.resize(original_image, (cell_width, cell_height), interpolation=cv2.INTER_AREA)

        # Scale polygon points for half-size image
        scaled_polygon = (polygon_points // 2).astype(np.int32)
        cv2.polylines(orig_half, [scaled_polygon], True, (0, 0, 255), 2)

        # Place in top-left cell
        canvas[0:cell_height, 0:cell_width] = orig_half

        # Add label
        cv2.putText(canvas, "Original + Polygon (0.5x)", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

        # =========================================================
        # (b) Top-right: Cropped polygon region on white background
        # =========================================================
        crop_h, crop_w = cropped_region.shape[:2]

        # Create white background and apply mask to show only polygon pixels
        white_bg = np.ones_like(cropped_region) * 255
        polygon_only = white_bg.copy()
        polygon_only[cropped_mask == 255] = cropped_region[cropped_mask == 255]

        # Scale up the cropped region to be more visible (fit within cell with padding)
        max_scale = min((cell_width - 40) / crop_w, (cell_height - 60) / crop_h)
        scale = max(max_scale, 4)  # At least 4x enlargement for small regions
        new_w = int(crop_w * scale)
        new_h = int(crop_h * scale)

        cropped_resized = cv2.resize(polygon_only, (new_w, new_h), interpolation=cv2.INTER_NEAREST)

        # Center in cell
        x_offset = cell_width + (cell_width - new_w) // 2
        y_offset = (cell_height - new_h) // 2 + 20  # +20 for label space

        # Ensure we stay within bounds
        y_end = min(y_offset + new_h, cell_height)
        x_end = min(x_offset + new_w, cell_width * 2)
        actual_h = y_end - y_offset
        actual_w = x_end - x_offset
        canvas[y_offset:y_end, x_offset:x_end] = cropped_resized[:actual_h, :actual_w]

        # Add label
        cv2.putText(canvas, "Cropped Polygon Region", (cell_width + 10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (255, 255, 255), 2)

        # =========================================================
        # (c) Bottom-left: Mean filtered image of the polygon on white background
        # =========================================================
        # Create white background and apply mask to show only polygon pixels
        white_bg_gray = np.ones_like(mean_filtered) * 255
        mean_polygon_only = white_bg_gray.copy()
        mean_polygon_only[cropped_mask == 255] = mean_filtered[cropped_mask == 255]

        # Convert grayscale to BGR for display
        mean_filtered_bgr = cv2.cvtColor(mean_polygon_only, cv2.COLOR_GRAY2BGR)

        # Resize for visibility (same scale as cropped region)
        mean_resized = cv2.resize(mean_filtered_bgr, (new_w, new_h), interpolation=cv2.INTER_NEAREST)

        # Center in cell
        x_offset = (cell_width - new_w) // 2
        y_offset = cell_height + (cell_height - new_h) // 2 + 20

        y_end = min(y_offset + new_h, cell_height * 2)
        x_end = min(x_offset + new_w, cell_width)
        actual_h = y_end - y_offset
        actual_w = x_end - x_offset
        canvas[y_offset:y_end, x_offset:x_end] = mean_resized[:actual_h, :actual_w]

        # Add label
        cv2.putText(canvas, "Mean Filtered (masked)", (10, cell_height + 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (255, 255, 255), 2)

        # =========================================================
        # (d) Bottom-right: Average calculation and result
        # =========================================================
        info_x_start = cell_width + 20
        info_y_start = cell_height + 40

        cv2.putText(canvas, "Step 3 Analysis", (info_x_start, info_y_start),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)

        cv2.putText(canvas, f"Average: {average_value:.2f}", (info_x_start, info_y_start + 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

        cv2.putText(canvas, f"Threshold: {CheckKnuckle.COMPONENT_IDENTIFICATION_DARKNESS_THRESHOLD}",
                    (info_x_start, info_y_start + 90),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

        is_dark = average_value < CheckKnuckle.COMPONENT_IDENTIFICATION_DARKNESS_THRESHOLD
        dark_text = "YES (< threshold)" if is_dark else "NO (>= threshold)"
        dark_color = (0, 255, 0) if is_dark else (0, 0, 255)
        cv2.putText(canvas, f"Is Dark: {dark_text}", (info_x_start, info_y_start + 130),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, dark_color, 2)

        model_color = (0, 255, 0) if detected_model == "DOST" else (0, 255, 255)
        cv2.putText(canvas, f"Detected: {detected_model}", (info_x_start, info_y_start + 180),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, model_color, 2)

        cv2.putText(canvas, "Press ESC or close window to continue", (info_x_start, info_y_start + 230),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (128, 128, 128), 1)

        # =========================================================
        # Display the window
        # =========================================================
        window_name = "CheckKnuckle Step 3 Debug"
        cv2.imshow(window_name, canvas)

        # Wait until user closes window or presses ESC
        while True:
            key = cv2.waitKey(100) & 0xFF
            if key == 27:  # ESC key
                break
            # Check if window was closed
            try:
                if cv2.getWindowProperty(window_name, cv2.WND_PROP_VISIBLE) < 1:
                    break
            except cv2.error:
                break

        try:
            cv2.destroyWindow(window_name)
        except cv2.error:
            pass
        cv2.waitKey(1)  # Process any pending GUI events

    @staticmethod
    def _alt_rect_mean(gray: np.ndarray, x: int, y: int, width: int, height: int) -> Optional[float]:
        """Mean grey level of an alternate rectangle; None if it is not set (width or height 0) or outside the image."""
        if width <= 0 or height <= 0 or x < 0 or y < 0 or x + width > gray.shape[1] or y + height > gray.shape[0]:
            return None
        return float(np.mean(gray[y:y + height, x:x + width]))

    @staticmethod
    def _alt_circle_mean(gray: np.ndarray, cx: int, cy: int, radius: int) -> Optional[float]:
        """Mean grey level of an alternate circle; None if it is not set (radius 0) or outside the image."""
        if radius <= 0 or cx - radius < 0 or cy - radius < 0 or cx + radius >= gray.shape[1] or cy + radius >= gray.shape[0]:
            return None
        mask = np.zeros(gray.shape[:2], dtype=np.uint8)
        cv2.circle(mask, (cx, cy), radius, 255, -1)
        return float(np.mean(gray[mask == 255]))

    @staticmethod
    def _polygon_mean(gray: np.ndarray, points) -> Optional[float]:
        """Mean grey level inside a polygon; None if it is not set (no area) or reaches outside the image."""
        pts = np.array(points, dtype=np.int32).reshape(-1, 2)
        if len(pts) < 3 or cv2.contourArea(pts) <= 0:
            return None
        if pts[:, 0].min() < 0 or pts[:, 1].min() < 0 or pts[:, 0].max() >= gray.shape[1] or pts[:, 1].max() >= gray.shape[0]:
            return None
        mask = np.zeros(gray.shape[:2], dtype=np.uint8)
        cv2.fillPoly(mask, [pts], 255)
        values = gray[mask == 255]
        return float(np.mean(values)) if len(values) > 0 else None

    @staticmethod
    def _alt_text(alt_mean: Optional[float]) -> str:
        return "" if alt_mean is None else f"; alternate mean {alt_mean:.2f}"

    @staticmethod
    def _crop_and_analyze(image: np.ndarray, x: int, y: int, width: int, height: int) -> float:
        """
        Crop a rectangular region, convert to grayscale, binarize with fixed threshold,
        apply mean filter, and return the average pixel value.

        Args:
            image: Source BGR image
            x: X coordinate of top-left corner
            y: Y coordinate of top-left corner
            width: Width of crop region
            height: Height of crop region

        Returns:
            Average pixel value of the processed region
        """
        # Crop the region
        cropped = image[y:y + height, x:x + width]

        # Convert to grayscale
        gray = cv2.cvtColor(cropped, cv2.COLOR_BGR2GRAY)

        # Binarise: below vision.knuckle.binarization.threshold = black, the rest white
        _, binarized = cv2.threshold(gray, CheckKnuckle.BINARIZATION_THRESHOLD, 255, cv2.THRESH_BINARY)

        # Apply mean filter (box blur) on the binarized image
        mean_filtered = cv2.blur(binarized, CheckKnuckle.KERNEL_SIZE)

        # Calculate and return average
        return np.mean(mean_filtered)

    @staticmethod
    def _analyze_polygon_region(image: np.ndarray, polygon_points: np.ndarray,
                                return_intermediates: bool = False) -> float | Tuple[
        float, np.ndarray, np.ndarray, np.ndarray]:
        """
        Analyze a polygon region: convert to grayscale, binarize with fixed threshold,
        apply mean filter, and return the average pixel value within the polygon.

        Args:
            image: Source BGR image
            polygon_points: Numpy array of polygon vertices
            return_intermediates: If True, also return intermediate images for debugging

        Returns:
            If return_intermediates is False:
                Average pixel value within the polygon region
            If return_intermediates is True:
                Tuple of (average, cropped_region, mean_filtered, cropped_mask)
        """
        # Create mask for polygon
        mask = np.zeros(image.shape[:2], dtype=np.uint8)
        cv2.fillPoly(mask, [polygon_points], 255)

        # Get bounding rectangle for efficiency
        x, y, w, h = cv2.boundingRect(polygon_points)

        # Crop to bounding rectangle
        cropped_region = image[y:y + h, x:x + w]
        cropped_mask = mask[y:y + h, x:x + w]

        # Convert to grayscale
        gray_region = cv2.cvtColor(cropped_region, cv2.COLOR_BGR2GRAY)

        # Binarise: below vision.knuckle.binarization.threshold = black, the rest white
        _, binarized = cv2.threshold(gray_region, CheckKnuckle.BINARIZATION_THRESHOLD, 255, cv2.THRESH_BINARY)

        # Apply mean filter on the binarized image
        mean_filtered = cv2.blur(binarized, CheckKnuckle.KERNEL_SIZE)

        # Calculate average only within the polygon
        polygon_pixels = mean_filtered[cropped_mask == 255]

        if len(polygon_pixels) > 0:
            avg = np.mean(polygon_pixels)
        else:
            avg = 255.0  # Return max value if no pixels (fail-safe)

        if return_intermediates:
            return avg, cropped_region, mean_filtered, cropped_mask
        else:
            return avg

    @staticmethod
    def checkKnuckle(anImage: np.ndarray, currentPictures: Dict[str, np.ndarray | None] = None,
                     componentQRCode: str = DOST, gamma: float = 2.0) -> Tuple[np.ndarray | None, bool]:
        """The knuckle check: (picture for the frontend, OK). vision.knuckle.method = segmentation (Machine 2): MobileSAM
        (checkKnuckleBySegmentation; its frame is in CheckKnuckle.lastResult); otherwise the grey-level regions
        (checkKnuckleByRegions)."""
        if str(CheckKnuckle.METHOD).strip().lower() == "segmentation":
            image, ok, _ = CheckKnuckle.checkKnuckleBySegmentation(anImage, componentQRCode)
            return image, ok
        return CheckKnuckle.checkKnuckleByRegions(anImage, currentPictures, componentQRCode, gamma)

    @staticmethod
    def checkKnuckleBySegmentation(anImage: np.ndarray, componentQRCode: Optional[str]
                                   ) -> Tuple[np.ndarray | None, bool, Optional[Dict]]:
        """MobileSAM knuckle check (camera/KnuckleLocator.py): (picture with the parts outlined, OK, frame for the
        later checks or None). OK when both bosses (with their pins), the lower arm on the back rest and a clear type
        are found, type and side match the QR code, and the camera has not moved beyond vision.knuckle.frame.max.*.
        The frame is returned only with OK."""
        _src = getFullyQualifiedName(__file__, CheckKnuckle)
        CheckKnuckle.lastResult = None
        if anImage is None:
            logBoth('logError', _src, "CheckKnuckle: Input image is None", MessageType.ISSUE)
            return anImage, False, None
        modelName, sideFromQR, _ = getModel_LHSRHS_AndTonnage(componentQRCode)
        if modelName == UNKNOWN or sideFromQR == UNKNOWN:
            logBoth('logError', _src, f"CheckKnuckle: Invalid QR Code - could not parse model/side: {componentQRCode}",
                    MessageType.ISSUE)
            return anImage, False, None
        try:
            from camera.KnuckleLocator import KnuckleLocator, draw, summary
            from camera import VisionFrame
            if CheckKnuckle.locator is None:
                CheckKnuckle.locator = KnuckleLocator()
            result = CheckKnuckle.locator.locate(anImage)
        except Exception as e:
            logBoth('logCritical', _src, f"CheckKnuckle: segmentation failed: {e}", MessageType.PROBLEM)
            import traceback
            traceback.print_exc()
            return anImage, False, None
        reasons = list(result["notes"])
        frame = result.get("frame")
        if result.get("type") and result["type"].upper() != str(modelName).upper():
            reasons.append(f"type {result['type']} but the QR code says {modelName}")
        if result.get("side") and result["side"].upper() != str(sideFromQR).upper():
            reasons.append(f"side {result['side']} but the QR code says {sideFromQR}")
        if frame is not None:
            within, why = VisionFrame.withinLimits(frame, CheckKnuckle.FRAME_MAX_SHIFT, CheckKnuckle.FRAME_MAX_ROTATION,
                                                   CheckKnuckle.FRAME_MAX_ZOOM_CHANGE)
            if not within:
                reasons.append(why + " - set the camera again")
        # the notes about the spindle mask alone do not fail the check (the pins give the spindle)
        failing = [r for r in reasons if not r.startswith("spindle")]
        ok = frame is not None and not failing
        result["ok"], result["reasons"] = ok, failing
        CheckKnuckle.lastResult = result
        line = summary(result)
        if ok:
            logBoth('logInfo', _src, f"CheckKnuckle: SUCCESS - {result['side']} {result['type']} matches the QR code; "
                                     f"{line}; frame: {VisionFrame.describe(frame)}", MessageType.SUCCESS)
        else:
            logBoth('logError', _src, f"CheckKnuckle: NOT OK - {'; '.join(failing) or 'knuckle not found'} (QR code "
                                      f"{modelName} {sideFromQR}); {line}", MessageType.ISSUE)
        return draw(anImage, result, ok), ok, (frame if ok else None)

    # The check by grey-level regions (vision.knuckle.method = regions): Machine 1, and Machine 2 until CHANGES 102.
    @staticmethod
    def checkKnuckleByRegions(anImage: np.ndarray, currentPictures: Dict[str, np.ndarray | None] = None,
                              componentQRCode: str = DOST, gamma: float = 2.0) -> Tuple[np.ndarray | None, bool]:
        """
        Verify that the knuckle in the image matches the scanned QR code.

        Args:
            anImage: The captured image to analyze
            currentPictures: Dictionary of previously captured images (not used for knuckle)
            componentQRCode: The QR code of the current component being assembled

        Returns:
            Tuple[np.ndarray, bool]: (original_image, is_valid)
                - original_image: The unmodified input image
                - is_valid: True if knuckle matches QR code, False otherwise
        """
        _src = getFullyQualifiedName(__file__, CheckKnuckle)

        try:
            logBoth('logDebug', _src,
                    f"CheckKnuckle.checkKnuckle() called with anImage = {anImage.shape if anImage is not None else 'None'}",
                    MessageType.GENERAL)

            # Validate inputs
            if anImage is None:
                logBoth('logError', _src, "CheckKnuckle: Input image is None", MessageType.ISSUE)
                return anImage, False

            if componentQRCode is None:
                logBoth('logError', _src, "CheckKnuckle: Component QR code is None", MessageType.ISSUE)
                return anImage, False

            # =====================================================================
            # HAND DETECTION CHECK - First check before any other processing
            # Ensure no operator hands are still in the frame
            # =====================================================================
            # if HandDetector.detectHands(anImage):
            #     logBoth('logError', _src, "CheckKnuckle: HAND DETECTION FAILED - Operator hands detected in image!", MessageType.PROBLEM)
            #     logBoth('logError', _src, "CheckKnuckle: Please ensure hands are completely out of frame before capturing", MessageType.PROBLEM)
            #     return anImage, False

            # logBoth('logInfo', _src, "CheckKnuckle: Hand detection passed - No hands in frame", MessageType.SUCCESS)

            # =====================================================================
            # Step 1: Get model info from QR code using QRCodeHelper
            # =====================================================================
            modelName, lhs_rhs_from_qr, tonnage = getModel_LHSRHS_AndTonnage(componentQRCode)

            logBoth('logDebug', _src, f"QR Code parsing result: Model={modelName}, Side={lhs_rhs_from_qr}, Tonnage={tonnage}", MessageType.GENERAL)

            if modelName == UNKNOWN or lhs_rhs_from_qr == UNKNOWN:
                logBoth('logError', _src, f"CheckKnuckle: Invalid QR Code - could not parse model/side: {componentQRCode}", MessageType.ISSUE)
                return anImage, False

            # =====================================================================
            # Convert image to grayscale once - reuse for all checks
            # =====================================================================
            gray_image = cv2.cvtColor(anImage, cv2.COLOR_BGR2GRAY)
            logBoth('logDebug', _src, "Converted image to grayscale for all checks", MessageType.GENERAL)

            # =====================================================================
            # COMMON CHECKS - Apply to all knuckles (DOST/DOSTPLUS, LHS/RHS)
            # These checks verify basic knuckle presence before detailed analysis
            # =====================================================================

            side_from_contours = None
            if CheckKnuckle.SIDE_BY_DIFFERENCE:
                # =================================================================
                # Common Check A by contours A / A' (vision.knuckle.common.check.a.polygon.points,
                # vision.knuckle.common.check.a.alt.polygon.points): their mean grey levels must differ by more than
                # vision.knuckle.side.difference.threshold; which one is darker tells the side
                # =================================================================
                mean_a_poly = CheckKnuckle._polygon_mean(gray_image, CheckKnuckle.COMMON_CHECK_A_POLYGON_POINTS)
                mean_a_alt_poly = CheckKnuckle._polygon_mean(gray_image, CheckKnuckle.COMMON_CHECK_A_ALT_POLYGON_POINTS)
                if mean_a_poly is None or mean_a_alt_poly is None:
                    logBoth('logError', _src,
                            "CheckKnuckle: Common Check A failed - contour A or A' not set or outside the picture "
                            "(vision.knuckle.common.check.a.polygon.points / a.alt.polygon.points)",
                            MessageType.ISSUE)
                    return anImage, False
                difference_a = mean_a_alt_poly - mean_a_poly
                if difference_a > CheckKnuckle.SIDE_DIFFERENCE_THRESHOLD:
                    side_from_contours = "LHS"
                elif -difference_a > CheckKnuckle.SIDE_DIFFERENCE_THRESHOLD:
                    side_from_contours = "RHS"
                else:
                    logBoth('logError', _src,
                            f"CheckKnuckle: Common Check A failed - contour A mean {mean_a_poly:.2f}, contour A' mean "
                            f"{mean_a_alt_poly:.2f}: they differ by {abs(difference_a):.2f}, not more than "
                            f"{CheckKnuckle.SIDE_DIFFERENCE_THRESHOLD} (no knuckle, or its side cannot be told)",
                            MessageType.ISSUE)
                    return anImage, False
                logBoth('logInfo', _src,
                        f"CheckKnuckle: Common Check A passed - contour A mean {mean_a_poly:.2f}, contour A' mean "
                        f"{mean_a_alt_poly:.2f} (difference {difference_a:+.2f}): {side_from_contours}",
                        MessageType.SUCCESS)
            else:
                # =====================================================================
                # Common Check A: rectangle vision.knuckle.common.check.a.* (knuckle casting next to the right boss)
                # Mean must be below vision.knuckle.common.check.threshold, otherwise the component is invalid
                # =====================================================================
                cropped_a = gray_image[
                            CheckKnuckle.COMMON_CHECK_A_Y:CheckKnuckle.COMMON_CHECK_A_Y + CheckKnuckle.COMMON_CHECK_A_HEIGHT,
                            CheckKnuckle.COMMON_CHECK_A_X:CheckKnuckle.COMMON_CHECK_A_X + CheckKnuckle.COMMON_CHECK_A_WIDTH
                            ]
                mean_a = np.mean(cropped_a)

                logBoth('logDebug', _src,
                        f"Common Check A - Region ({CheckKnuckle.COMMON_CHECK_A_X}, {CheckKnuckle.COMMON_CHECK_A_Y}) "
                        f"{CheckKnuckle.COMMON_CHECK_A_WIDTH}x{CheckKnuckle.COMMON_CHECK_A_HEIGHT}: "
                        f"mean = {mean_a:.2f}, threshold = {CheckKnuckle.COMMON_CHECK_THRESHOLD}",
                        MessageType.GENERAL)

                # alternate area: passes if the region OR its alternate is dark (a paint mark can make one bright)
                mean_a_alt = CheckKnuckle._alt_rect_mean(gray_image, CheckKnuckle.COMMON_CHECK_A_ALT_X, CheckKnuckle.COMMON_CHECK_A_ALT_Y,
                                                          CheckKnuckle.COMMON_CHECK_A_ALT_WIDTH, CheckKnuckle.COMMON_CHECK_A_ALT_HEIGHT)
                if mean_a >= CheckKnuckle.COMMON_CHECK_THRESHOLD and (
                        mean_a_alt is None or mean_a_alt >= CheckKnuckle.COMMON_CHECK_THRESHOLD):
                    logBoth('logError', _src,
                            f"CheckKnuckle: Common Check A failed - Region A mean {mean_a:.2f} >= {CheckKnuckle.COMMON_CHECK_THRESHOLD}"
                            f"{CheckKnuckle._alt_text(mean_a_alt)}",
                            MessageType.ISSUE)
                    return anImage, False

                logBoth('logInfo', _src,
                        f"CheckKnuckle: Common Check A passed - Region A mean {mean_a:.2f} < {CheckKnuckle.COMMON_CHECK_THRESHOLD}"
                            f"{CheckKnuckle._alt_text(mean_a_alt)}",
                        MessageType.SUCCESS)

            # =====================================================================
            # Common Check B: rectangle vision.knuckle.common.check.b.* (knuckle casting next to the left boss)
            # Mean must be below vision.knuckle.common.check.threshold, otherwise the component is invalid
            # =====================================================================
            cropped_b = gray_image[
                        CheckKnuckle.COMMON_CHECK_B_Y:CheckKnuckle.COMMON_CHECK_B_Y + CheckKnuckle.COMMON_CHECK_B_HEIGHT,
                        CheckKnuckle.COMMON_CHECK_B_X:CheckKnuckle.COMMON_CHECK_B_X + CheckKnuckle.COMMON_CHECK_B_WIDTH
                        ]
            mean_b = np.mean(cropped_b)

            logBoth('logDebug', _src,
                    f"Common Check B - Region ({CheckKnuckle.COMMON_CHECK_B_X}, {CheckKnuckle.COMMON_CHECK_B_Y}) "
                    f"{CheckKnuckle.COMMON_CHECK_B_WIDTH}x{CheckKnuckle.COMMON_CHECK_B_HEIGHT}: "
                    f"mean = {mean_b:.2f}, threshold = {CheckKnuckle.COMMON_CHECK_THRESHOLD}",
                    MessageType.GENERAL)

            # alternate area: passes if the region OR its alternate is dark (a paint mark can make one bright)
            mean_b_alt = CheckKnuckle._alt_rect_mean(gray_image, CheckKnuckle.COMMON_CHECK_B_ALT_X, CheckKnuckle.COMMON_CHECK_B_ALT_Y,
                                                      CheckKnuckle.COMMON_CHECK_B_ALT_WIDTH, CheckKnuckle.COMMON_CHECK_B_ALT_HEIGHT)
            if mean_b >= CheckKnuckle.COMMON_CHECK_THRESHOLD and (
                    mean_b_alt is None or mean_b_alt >= CheckKnuckle.COMMON_CHECK_THRESHOLD):
                logBoth('logError', _src,
                        f"CheckKnuckle: Common Check B failed - Region B mean {mean_b:.2f} >= {CheckKnuckle.COMMON_CHECK_THRESHOLD}"
                        f"{CheckKnuckle._alt_text(mean_b_alt)}",
                        MessageType.ISSUE)
                return anImage, False

            logBoth('logInfo', _src,
                    f"CheckKnuckle: Common Check B passed - Region B mean {mean_b:.2f} < {CheckKnuckle.COMMON_CHECK_THRESHOLD}"
                        f"{CheckKnuckle._alt_text(mean_b_alt)}",
                    MessageType.SUCCESS)

            # =====================================================================
            # Common Check C: circle vision.knuckle.common.check.c.* (dark fixture feature, left)
            # Mean must be below vision.knuckle.common.check.threshold, otherwise the component is invalid
            # =====================================================================
            # Create circular mask
            mask_c = np.zeros(gray_image.shape[:2], dtype=np.uint8)
            cv2.circle(mask_c,
                       (CheckKnuckle.COMMON_CHECK_C_CENTER_X, CheckKnuckle.COMMON_CHECK_C_CENTER_Y),
                       CheckKnuckle.COMMON_CHECK_C_RADIUS, 255, -1)

            # Get bounding box for efficiency
            x_c = CheckKnuckle.COMMON_CHECK_C_CENTER_X - CheckKnuckle.COMMON_CHECK_C_RADIUS
            y_c = CheckKnuckle.COMMON_CHECK_C_CENTER_Y - CheckKnuckle.COMMON_CHECK_C_RADIUS
            w_c = 2 * CheckKnuckle.COMMON_CHECK_C_RADIUS
            h_c = 2 * CheckKnuckle.COMMON_CHECK_C_RADIUS

            cropped_c = gray_image[y_c:y_c + h_c, x_c:x_c + w_c]
            mask_c_cropped = mask_c[y_c:y_c + h_c, x_c:x_c + w_c]

            # Calculate mean only within circle
            circle_pixels_c = cropped_c[mask_c_cropped == 255]
            mean_c = np.mean(circle_pixels_c) if len(circle_pixels_c) > 0 else 255.0

            logBoth('logDebug', _src,
                    f"Common Check C - Circle ({CheckKnuckle.COMMON_CHECK_C_CENTER_X}, {CheckKnuckle.COMMON_CHECK_C_CENTER_Y}) "
                    f"radius {CheckKnuckle.COMMON_CHECK_C_RADIUS}: "
                    f"mean = {mean_c:.2f}, threshold = {CheckKnuckle.COMMON_CHECK_THRESHOLD}",
                    MessageType.GENERAL)

            # alternate area: passes if the region OR its alternate is dark (a paint mark can make one bright)
            mean_c_alt = CheckKnuckle._alt_circle_mean(gray_image, CheckKnuckle.COMMON_CHECK_C_ALT_CENTER_X,
                                                        CheckKnuckle.COMMON_CHECK_C_ALT_CENTER_Y, CheckKnuckle.COMMON_CHECK_C_ALT_RADIUS)
            if mean_c >= CheckKnuckle.COMMON_CHECK_THRESHOLD and (
                    mean_c_alt is None or mean_c_alt >= CheckKnuckle.COMMON_CHECK_THRESHOLD):
                logBoth('logError', _src,
                        f"CheckKnuckle: Common Check C failed - Circle C mean {mean_c:.2f} >= {CheckKnuckle.COMMON_CHECK_THRESHOLD}"
                        f"{CheckKnuckle._alt_text(mean_c_alt)}",
                        MessageType.ISSUE)
                return anImage, False

            logBoth('logInfo', _src,
                    f"CheckKnuckle: Common Check C passed - Circle C mean {mean_c:.2f} < {CheckKnuckle.COMMON_CHECK_THRESHOLD}"
                        f"{CheckKnuckle._alt_text(mean_c_alt)}",
                    MessageType.SUCCESS)

            # =====================================================================
            # Common Check D: circle vision.knuckle.common.check.d.* (dark fixture feature, right)
            # Mean must be below vision.knuckle.common.check.threshold, otherwise the component is invalid
            # =====================================================================
            # Create circular mask
            mask_d = np.zeros(gray_image.shape[:2], dtype=np.uint8)
            cv2.circle(mask_d,
                       (CheckKnuckle.COMMON_CHECK_D_CENTER_X, CheckKnuckle.COMMON_CHECK_D_CENTER_Y),
                       CheckKnuckle.COMMON_CHECK_D_RADIUS, 255, -1)

            # Get bounding box for efficiency
            x_d = CheckKnuckle.COMMON_CHECK_D_CENTER_X - CheckKnuckle.COMMON_CHECK_D_RADIUS
            y_d = CheckKnuckle.COMMON_CHECK_D_CENTER_Y - CheckKnuckle.COMMON_CHECK_D_RADIUS
            w_d = 2 * CheckKnuckle.COMMON_CHECK_D_RADIUS
            h_d = 2 * CheckKnuckle.COMMON_CHECK_D_RADIUS

            cropped_d = gray_image[y_d:y_d + h_d, x_d:x_d + w_d]
            mask_d_cropped = mask_d[y_d:y_d + h_d, x_d:x_d + w_d]

            # Calculate mean only within circle
            circle_pixels_d = cropped_d[mask_d_cropped == 255]
            mean_d = np.mean(circle_pixels_d) if len(circle_pixels_d) > 0 else 255.0

            logBoth('logDebug', _src,
                    f"Common Check D - Circle ({CheckKnuckle.COMMON_CHECK_D_CENTER_X}, {CheckKnuckle.COMMON_CHECK_D_CENTER_Y}) "
                    f"radius {CheckKnuckle.COMMON_CHECK_D_RADIUS}: "
                    f"mean = {mean_d:.2f}, threshold = {CheckKnuckle.COMMON_CHECK_THRESHOLD}",
                    MessageType.GENERAL)

            # alternate area: passes if the region OR its alternate is dark (a paint mark can make one bright)
            mean_d_alt = CheckKnuckle._alt_circle_mean(gray_image, CheckKnuckle.COMMON_CHECK_D_ALT_CENTER_X,
                                                        CheckKnuckle.COMMON_CHECK_D_ALT_CENTER_Y, CheckKnuckle.COMMON_CHECK_D_ALT_RADIUS)
            if mean_d >= CheckKnuckle.COMMON_CHECK_THRESHOLD and (
                    mean_d_alt is None or mean_d_alt >= CheckKnuckle.COMMON_CHECK_THRESHOLD):
                logBoth('logError', _src,
                        f"CheckKnuckle: Common Check D failed - Circle D mean {mean_d:.2f} >= {CheckKnuckle.COMMON_CHECK_THRESHOLD}"
                        f"{CheckKnuckle._alt_text(mean_d_alt)}",
                        MessageType.ISSUE)
                return anImage, False

            logBoth('logInfo', _src,
                    f"CheckKnuckle: Common Check D passed - Circle D mean {mean_d:.2f} < {CheckKnuckle.COMMON_CHECK_THRESHOLD}"
                        f"{CheckKnuckle._alt_text(mean_d_alt)}",
                    MessageType.SUCCESS)

            # =====================================================================
            # Common Check E: rectangle vision.knuckle.common.check.e.* (dark fixture feature, bottom centre)
            # Mean must be below vision.knuckle.common.check.threshold, otherwise the component is invalid
            # =====================================================================
            cropped_e = gray_image[
                        CheckKnuckle.COMMON_CHECK_E_Y:CheckKnuckle.COMMON_CHECK_E_Y + CheckKnuckle.COMMON_CHECK_E_HEIGHT,
                        CheckKnuckle.COMMON_CHECK_E_X:CheckKnuckle.COMMON_CHECK_E_X + CheckKnuckle.COMMON_CHECK_E_WIDTH
                        ]
            mean_e = np.mean(cropped_e)

            logBoth('logDebug', _src,
                    f"Common Check E - Region ({CheckKnuckle.COMMON_CHECK_E_X}, {CheckKnuckle.COMMON_CHECK_E_Y}) "
                    f"{CheckKnuckle.COMMON_CHECK_E_WIDTH}x{CheckKnuckle.COMMON_CHECK_E_HEIGHT}: "
                    f"mean = {mean_e:.2f}, threshold = {CheckKnuckle.COMMON_CHECK_THRESHOLD}",
                    MessageType.GENERAL)

            # alternate area: passes if the region OR its alternate is dark (a paint mark can make one bright)
            mean_e_alt = CheckKnuckle._alt_rect_mean(gray_image, CheckKnuckle.COMMON_CHECK_E_ALT_X, CheckKnuckle.COMMON_CHECK_E_ALT_Y,
                                                      CheckKnuckle.COMMON_CHECK_E_ALT_WIDTH, CheckKnuckle.COMMON_CHECK_E_ALT_HEIGHT)
            if mean_e >= CheckKnuckle.COMMON_CHECK_THRESHOLD and (
                    mean_e_alt is None or mean_e_alt >= CheckKnuckle.COMMON_CHECK_THRESHOLD):
                logBoth('logError', _src,
                        f"CheckKnuckle: Common Check E failed - Region E mean {mean_e:.2f} >= {CheckKnuckle.COMMON_CHECK_THRESHOLD}"
                        f"{CheckKnuckle._alt_text(mean_e_alt)}",
                        MessageType.ISSUE)
                return anImage, False

            logBoth('logInfo', _src,
                    f"CheckKnuckle: Common Check E passed - Region E mean {mean_e:.2f} < {CheckKnuckle.COMMON_CHECK_THRESHOLD}"
                        f"{CheckKnuckle._alt_text(mean_e_alt)}",
                    MessageType.SUCCESS)

            if CheckKnuckle.SIDE_BY_DIFFERENCE:
                # =================================================================
                # Side boxes D1 (vision.knuckle.lhs.crop.*) and D2 (vision.knuckle.rhs.crop.*): mean grey levels must
                # differ by more than vision.knuckle.side.difference.threshold; the darker one has the arm on it.
                # Must agree with the side from contours A / A'.
                # =================================================================
                mean_d1 = float(np.mean(gray_image[CheckKnuckle.LHS_CROP_Y:CheckKnuckle.LHS_CROP_Y + CheckKnuckle.LHS_CROP_HEIGHT,
                                                   CheckKnuckle.LHS_CROP_X:CheckKnuckle.LHS_CROP_X + CheckKnuckle.LHS_CROP_WIDTH]))
                mean_d2 = float(np.mean(gray_image[CheckKnuckle.RHS_CROP_Y:CheckKnuckle.RHS_CROP_Y + CheckKnuckle.RHS_CROP_HEIGHT,
                                                   CheckKnuckle.RHS_CROP_X:CheckKnuckle.RHS_CROP_X + CheckKnuckle.RHS_CROP_WIDTH]))
                difference_d = mean_d1 - mean_d2
                if difference_d > CheckKnuckle.SIDE_DIFFERENCE_THRESHOLD:
                    side_from_boxes = "RHS"
                elif -difference_d > CheckKnuckle.SIDE_DIFFERENCE_THRESHOLD:
                    side_from_boxes = "LHS"
                else:
                    logBoth('logError', _src,
                            f"CheckKnuckle: Invalid component - side box D1 mean {mean_d1:.2f}, D2 mean {mean_d2:.2f}: "
                            f"they differ by {abs(difference_d):.2f}, not more than {CheckKnuckle.SIDE_DIFFERENCE_THRESHOLD} "
                            f"(no arm on the bar, or something lying on the other end?)",
                            MessageType.ISSUE)
                    return anImage, False
                logBoth('logDebug', _src, f"Side boxes D1 {mean_d1:.2f}, D2 {mean_d2:.2f}: {side_from_boxes}", MessageType.GENERAL)
                if side_from_boxes != side_from_contours:
                    logBoth('logError', _src,
                            f"CheckKnuckle: Invalid component - contours A / A' say {side_from_contours}, side boxes "
                            f"D1 / D2 say {side_from_boxes}",
                            MessageType.ISSUE)
                    return anImage, False
                detected_side = side_from_boxes
            else:
                # =====================================================================
                # Step 1A: LHS side box vision.knuckle.lhs.crop.* (left end of the bottom bar)
                # Binarised (vision.knuckle.binarization.threshold) and mean-filtered (vision.knuckle.kernel.size);
                # an average below vision.knuckle.lhs.rhs.darkness.threshold means the arm of an LHS knuckle is there
                # =====================================================================
                lhs_avg = CheckKnuckle._crop_and_analyze(
                    anImage,
                    CheckKnuckle.LHS_CROP_X,
                    CheckKnuckle.LHS_CROP_Y,
                    CheckKnuckle.LHS_CROP_WIDTH,
                    CheckKnuckle.LHS_CROP_HEIGHT
                )
                is_lhs_component = lhs_avg < CheckKnuckle.LHS_RHS_DARKNESS_THRESHOLD

                logBoth('logDebug', _src, f"LHS region average: {lhs_avg:.2f}, is_LHS: {is_lhs_component}", MessageType.GENERAL)

                # =====================================================================
                # Step 1B: RHS side box vision.knuckle.rhs.crop.* (right end of the bottom bar), same processing;
                # an average below vision.knuckle.lhs.rhs.darkness.threshold means the arm of an RHS knuckle is there
                # =====================================================================
                rhs_avg = CheckKnuckle._crop_and_analyze(
                    anImage,
                    CheckKnuckle.RHS_CROP_X,
                    CheckKnuckle.RHS_CROP_Y,
                    CheckKnuckle.RHS_CROP_WIDTH,
                    CheckKnuckle.RHS_CROP_HEIGHT
                )
                is_rhs_component = rhs_avg < CheckKnuckle.LHS_RHS_DARKNESS_THRESHOLD

                logBoth('logDebug', _src, f"RHS region average: {rhs_avg:.2f}, is_RHS: {is_rhs_component}", MessageType.GENERAL)

                # =====================================================================
                # Validation: At least one side must be detected
                # =====================================================================
                if not is_lhs_component and not is_rhs_component:
                    logBoth('logError', _src,
                            f"CheckKnuckle: Invalid component - neither LHS nor RHS detected "
                            f"(LHS avg={lhs_avg:.2f}, RHS avg={rhs_avg:.2f})",
                            MessageType.ISSUE)
                    return anImage, False

                # Both sides dark: the arm is on one side and something else (a glove, a rag, a hand) lies on the other.
                # The side cannot be told, and assuming one could accept a knuckle of the wrong side: rejected.
                if is_lhs_component and is_rhs_component:
                    logBoth('logError', _src,
                            f"CheckKnuckle: Invalid image - both LHS and RHS side boxes are dark "
                            f"(LHS avg={lhs_avg:.2f}, RHS avg={rhs_avg:.2f}, threshold={CheckKnuckle.LHS_RHS_DARKNESS_THRESHOLD}); "
                            f"is something lying on the bar?",
                            MessageType.ISSUE)
                    return anImage, False

                # Determine detected side from image
                detected_side = "LHS" if is_lhs_component else "RHS"

            # =====================================================================
            # Match detected side with QR code side
            # =====================================================================
            if detected_side.upper() != lhs_rhs_from_qr.upper():
                logBoth('logError', _src,
                        f"CheckKnuckle: Side mismatch - QR code says {lhs_rhs_from_qr}, "
                        f"but image shows {detected_side}",
                        MessageType.ISSUE)
                return anImage, False

            logBoth('logInfo', _src, f"CheckKnuckle: Side matched - {detected_side}", MessageType.SUCCESS)

            # =====================================================================
            # Step 2: polygon vision.knuckle.step2.polygon.points (next to the left boss)
            # Binarised and mean-filtered as above; an average below
            # vision.knuckle.component.identification.darkness.threshold means DOST, otherwise DOSTPLUS.
            # Must agree with the model in the QR code.
            # =====================================================================
            step2_avg = CheckKnuckle._analyze_polygon_region(anImage, CheckKnuckle.STEP2_POLYGON_POINTS)
            step2_passed = False
            if modelName is not None:
                if modelName.upper() == "DOST":
                    step2_passed = step2_avg < CheckKnuckle.COMPONENT_IDENTIFICATION_DARKNESS_THRESHOLD
                elif modelName.upper() == "DOSTPLUS":
                    step2_passed = step2_avg >= CheckKnuckle.COMPONENT_IDENTIFICATION_DARKNESS_THRESHOLD

            logBoth('logDebug', _src, f"Step 2 polygon average: {step2_avg:.2f}, passed: {step2_passed}", MessageType.GENERAL)

            if not step2_passed:
                logBoth('logError', _src,
                        f"CheckKnuckle: Step 2 failed - polygon average {step2_avg:.2f} does not match {modelName} "
                        f"(below {CheckKnuckle.COMPONENT_IDENTIFICATION_DARKNESS_THRESHOLD} = DOST, otherwise DOSTPLUS)",
                        MessageType.ISSUE)
                return anImage, False

            # =====================================================================
            # Step 3: polygon vision.knuckle.step3.polygon.points (next to the right boss), same rule as step 2:
            # below vision.knuckle.component.identification.darkness.threshold = DOST, otherwise DOSTPLUS
            # =====================================================================
            if CHECK_KNUCKLE_IMAGE_DEBUG:
                # Get intermediate values for debug visualization
                step3_avg, cropped_region, mean_filtered, cropped_mask = CheckKnuckle._analyze_polygon_region(
                    anImage, CheckKnuckle.STEP3_POLYGON_POINTS, return_intermediates=True
                )
            else:
                step3_avg = CheckKnuckle._analyze_polygon_region(anImage, CheckKnuckle.STEP3_POLYGON_POINTS)

            step3_passed = False
            if modelName is not None:
                if modelName.upper() == "DOST":
                    step3_passed = step3_avg < CheckKnuckle.COMPONENT_IDENTIFICATION_DARKNESS_THRESHOLD
                elif modelName.upper() == "DOSTPLUS":
                    step3_passed = step3_avg >= CheckKnuckle.COMPONENT_IDENTIFICATION_DARKNESS_THRESHOLD

            detected_model = modelName if step3_passed else "Correct model not detected"

            logBoth('logDebug', _src, f"Step 3 polygon average: {step3_avg:.2f}, detected model: {detected_model}", MessageType.GENERAL)

            # Show debug window if enabled
            if CHECK_KNUCKLE_IMAGE_DEBUG:
                CheckKnuckle._show_debug_step3(
                    original_image=anImage,
                    polygon_points=CheckKnuckle.STEP3_POLYGON_POINTS,
                    cropped_region=cropped_region,
                    mean_filtered=mean_filtered,
                    cropped_mask=cropped_mask,
                    average_value=step3_avg,
                    detected_model=detected_model
                )

            # =====================================================================
            # Match detected model with QR code model
            # =====================================================================
            if modelName.upper() != detected_model.upper():
                logBoth('logError', _src,
                        f"CheckKnuckle: Model mismatch - QR code says {modelName}, "
                        f"but image shows {detected_model}",
                        MessageType.ISSUE)
                return anImage, False

            # =====================================================================
            # All checks passed!
            # =====================================================================
            logBoth('logInfo', _src, f"CheckKnuckle: SUCCESS - Component matched: {detected_side} {detected_model}", MessageType.SUCCESS)
            return anImage, True
        except Exception as e:
            logBoth('logCritical', _src, f"CheckKnuckle: Exception during detection: {e}", MessageType.PROBLEM)
            import traceback
            traceback.print_exc()
            return anImage, False

    @staticmethod
    def main(directory: str, qrCode: str):
        """
        Test method to process all knuckle images in a directory.

        Goes through all PNG, JPG, and JPEG files in the directory,
        filters for files containing "Knuckle" in the filename,
        and runs the checkKnuckle validation on each.

        Args:
            directory: Path to directory containing images
            qrCode: QR code value to use for validation
        """
        _src = getFullyQualifiedName(__file__, CheckKnuckle)

        print(f"\n{'=' * 60}")
        print(f"CheckKnuckle Test Runner")
        print(f"{'=' * 60}")
        print(f"Directory: {directory}")
        print(f"QR Code: {qrCode}")
        print(f"{'=' * 60}\n")

        # Validate directory exists
        if not os.path.isdir(directory):
            logBoth('logError', _src, f"Error: Directory does not exist: {directory}", MessageType.ISSUE)
            return

        # Find all image files (case-insensitive extensions)
        image_extensions = ['*.png', '*.jpg', '*.jpeg', '*.PNG', '*.JPG', '*.JPEG']

        image_files = []
        for ext in image_extensions:
            pattern = os.path.join(directory, ext)
            image_files.extend(glob.glob(pattern))

        # Remove duplicates (in case of case-sensitive filesystem issues)
        image_files = list(set(image_files))

        # Filter for files containing "Knuckle" in the filename (case-insensitive)
        # knuckle_files = [f for f in image_files if "knuckle" in os.path.basename(f).lower()]
        knuckle_files = [f for f in image_files]

        if not knuckle_files:
            logBoth('logError', _src, f"No files with 'Knuckle' in filename found in {directory}", MessageType.ISSUE)
            print(f"Total image files found: {len(image_files)}")
            if image_files:
                print("Available image files:")
                for f in sorted(image_files)[:10]:  # Show first 10
                    print(f"  - {os.path.basename(f)}")
                if len(image_files) > 10:
                    print(f"  ... and {len(image_files) - 10} more")
            return

        print(f"Found {len(knuckle_files)} knuckle image(s) to process\n")

        # Sort for consistent ordering
        knuckle_files = sorted(knuckle_files)

        # Process each file
        passed_count = 0
        failed_count = 0

        for filepath in knuckle_files:
            filename = os.path.basename(filepath)
            print(f"Processing: {filename}")

            # Load image
            image = cv2.imread(filepath)

            if image is None:
                logBoth('logError', _src, f"  Could not load image: {filepath}", MessageType.ISSUE)
                failed_count += 1
                continue

            print(f"  Image shape: {image.shape}")

            # Apply check
            _, result = CheckKnuckle.checkKnuckle(
                anImage=image,
                currentPictures=None,
                componentQRCode=qrCode
            )

            if result:
                logBoth('logInfo', _src, f"  Component matched", MessageType.SUCCESS)
                passed_count += 1
            else:
                logBoth('logError', _src, f"  Component did not match", MessageType.ISSUE)
                failed_count += 1

            print()  # Empty line between files

        # Summary
        print(f"{'=' * 60}")
        print(f"Summary: {passed_count} passed, {failed_count} failed, {passed_count + failed_count} total")
        print(f"{'=' * 60}\n")


# if __name__ == "__main__":
#     # directory = "C:/AshokLeylandImages/DOST/LHS"
#     # qr_code = "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$"
#
#     directory = "C:/AshokLeylandImages/DOSTPLUS/LHS"
#     qr_code = "8201206$400112VA1C$11770$09.03.2025$C5211701$MEK$40Cr4-B$1$SPDL ASSY-KNULH$SNPL-MAT$"
#     #
#     # directory = "C:/AshokLeylandImages/DOSTPLUS/RHS"
#     # qr_code = "8201206$400102VA1C$11770$09.03.2025$C5211701$MEK$40Cr4-B$1$SPDL ASSY-KNURH$SNPL-MAT$"
#
#     CheckKnuckle.main(directory, qr_code)