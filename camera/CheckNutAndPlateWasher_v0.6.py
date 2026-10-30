"""
CheckNutAndPlateWasher - Detects presence of Nut and Plate Washer state by comparing with Top Bearing image.

Detection Method:
    Two-step color channel verification:

    Step 1: Nut Check (Blue Channel)
        - Annular ring: INNER_RADIUS (26) to MIDDLE_RADIUS (34)
        - Compare nut_image vs top_bearing_image
        - Extract blue channel averages
        - If nut_blue_avg - tb_blue_avg >= 10 → Nut present

    Step 2: Washer Check (Red Channel)
        - Annular ring: MIDDLE_RADIUS (34) to OUTER_RADIUS (49)
        - Compare top_bearing_image vs nut_image
        - Extract red channel averages
        - If tb_red_avg - nut_red_avg >= 8 → Washer present

    Final Decision: Return True only if BOTH nut and washer are present

Parameters (from configuration):
    CENTER_X, CENTER_Y: Center of the region (default: 634, 349)
    INNER_RADIUS: Inner radius for nut check (default: 26)
    MIDDLE_RADIUS: Middle radius separating nut/washer regions (default: 34)
    OUTER_RADIUS: Outer radius for washer check (default: 49)
    NUT_THRESHOLD: Threshold for nut blue channel check (default: 10)
    WASHER_THRESHOLD: Threshold for washer red channel check (default: 8)
"""

import cv2
import numpy as np
from typing import Dict, Tuple
import glob
import os
# from HandDetector import HandDetector

# Try to import production modules, fall back to mocks for standalone testing
try:
    from utils.RedisUtils import *
    from BaseUtils import *
    from Configuration import *
    from utils.QRCodeHelper import getModel_LHSRHS_AndTonnage, UNKNOWN

    CosThetaConfigurator.getInstance()
except ImportError:
    # Standalone testing mode - define mocks
    DOST = "DOST"
    DOSTPLUS = "DOSTPLUS"
    hubAndBottomBearingPictureKeyString = "hubAndBottomBearingPicture"
    topBearingPictureKeyString = "topBearingPicture"


    def printBoldBlue(msg):
        print(f"\033[1;34m{msg}\033[0m")


    def printBoldGreen(msg):
        print(f"\033[1;32m{msg}\033[0m")


    def printBoldRed(msg):
        print(f"\033[1;31m{msg}\033[0m")


    def getModel_LHSRHS_AndTonnage(qrCode):
        return "DOST", "LHS", "Unknown"


    UNKNOWN = "Unknown"


class CheckNutAndPlateWasher:
    """
    Checks for the presence of Nut and Plate Washer state by comparing the current image
    with previously captured images using annular masks and color channel analysis.
    """

    # Default detection parameters (will be overridden by configuration)
    CENTER_X = 634
    CENTER_Y = 349
    INNER_RADIUS = 26
    MIDDLE_RADIUS = 34
    OUTER_RADIUS = 49
    NUT_THRESHOLD = 10.0
    WASHER_THRESHOLD = 8.0

    # Cached annular masks
    _annular_mask_nut = None
    _annular_mask_washer = None
    _mask_shape = None

    @classmethod
    def _load_parameters_from_config(cls):
        """Load parameters from configuration file."""
        try:
            cls.CENTER_X = CosThetaConfigurator.getNutAndPlateWasherCheckCenterX()
            cls.CENTER_Y = CosThetaConfigurator.getNutAndPlateWasherCheckCenterY()
            cls.INNER_RADIUS = CosThetaConfigurator.getNutAndPlateWasherCheckInnerRadius()
            cls.MIDDLE_RADIUS = CosThetaConfigurator.getNutAndPlateWasherCheckMiddleRadius()
            cls.OUTER_RADIUS = CosThetaConfigurator.getNutAndPlateWasherCheckOuterRadius()
            cls.NUT_THRESHOLD = CosThetaConfigurator.getNutAndPlateWasherCheckNutThreshold()
            cls.WASHER_THRESHOLD = CosThetaConfigurator.getNutAndPlateWasherCheckWasherThreshold()
        except:
            pass

    @classmethod
    def _get_annular_mask(cls, image_shape: tuple, inner_radius: int, outer_radius: int) -> np.ndarray:
        """
        Create an annular (ring) mask with given inner and outer radii.
        """
        h, w = image_shape[:2]
        mask = np.zeros((h, w), dtype=np.uint8)

        # Draw outer circle (filled)
        cv2.circle(mask, (cls.CENTER_X, cls.CENTER_Y), outer_radius, 255, -1)

        # Draw inner circle (filled with black to create the hole)
        cv2.circle(mask, (cls.CENTER_X, cls.CENTER_Y), inner_radius, 0, -1)

        return mask

    @classmethod
    def _get_all_masks(cls, image_shape: tuple):
        """
        Get both annular masks, creating them if needed.
        """
        if cls._mask_shape != image_shape[:2]:
            # Mask for nut check: INNER_RADIUS to MIDDLE_RADIUS (blue channel)
            cls._annular_mask_nut = cls._get_annular_mask(
                image_shape, cls.INNER_RADIUS, cls.MIDDLE_RADIUS
            )

            # Mask for washer check: MIDDLE_RADIUS to OUTER_RADIUS (red channel)
            cls._annular_mask_washer = cls._get_annular_mask(
                image_shape, cls.MIDDLE_RADIUS, cls.OUTER_RADIUS
            )

            cls._mask_shape = image_shape[:2]

        return cls._annular_mask_nut, cls._annular_mask_washer

    @staticmethod
    def _compute_bw_average(image: np.ndarray, mask: np.ndarray) -> float:
        """
        Convert image to B&W (grayscale) and compute the average within the masked region.
        """
        # Convert to grayscale
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        # Get average within mask
        masked_pixels = gray[mask > 0]
        average = float(np.mean(masked_pixels)) if len(masked_pixels) > 0 else 0.0

        return average

    @staticmethod
    def _compute_rb_average(image: np.ndarray, mask: np.ndarray) -> float:
        """
        Compute the average R - B within the masked region.
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

    @staticmethod
    def _compute_blue_channel_average(image: np.ndarray, mask: np.ndarray) -> float:
        """
        Compute the average of blue channel within the masked region.
        """
        # OpenCV uses BGR order, blue is channel 0
        b_channel = image[:, :, 0].astype(np.float32)

        # Get average within mask
        masked_pixels = b_channel[mask > 0]
        average = float(np.mean(masked_pixels)) if len(masked_pixels) > 0 else 0.0

        return average

    @staticmethod
    def _compute_red_channel_average(image: np.ndarray, mask: np.ndarray) -> float:
        """
        Compute the average of red channel within the masked region.
        """
        # OpenCV uses BGR order, red is channel 2
        r_channel = image[:, :, 2].astype(np.float32)

        # Get average within mask
        masked_pixels = r_channel[mask > 0]
        average = float(np.mean(masked_pixels)) if len(masked_pixels) > 0 else 0.0

        return average

    @staticmethod
    def _create_annotated_image(image: np.ndarray,
                                is_nut_present: bool,
                                nut_present: bool,
                                washer_present: bool,
                                nut_diff: float,
                                washer_diff: float) -> np.ndarray:
        """Create an annotated version of the input image showing the detection result."""
        annotated = image.copy()

        # Draw the annular regions
        center = (CheckNutAndPlateWasher.CENTER_X, CheckNutAndPlateWasher.CENTER_Y)

        # Draw all radius circles for visualization
        cv2.circle(annotated, center, CheckNutAndPlateWasher.INNER_RADIUS, (255, 255, 0), 1)  # Yellow - inner
        cv2.circle(annotated, center, CheckNutAndPlateWasher.MIDDLE_RADIUS, (255, 0, 255), 2)  # Magenta - middle
        cv2.circle(annotated, center, CheckNutAndPlateWasher.OUTER_RADIUS,
                   (0, 255, 0) if is_nut_present else (0, 0, 255), 2)  # Green/Red - outer

        # Add result text
        result_text = "NUT+WASHER: PRESENT" if is_nut_present else "NUT+WASHER: NOT DETECTED"
        color = (0, 255, 0) if is_nut_present else (0, 0, 255)
        cv2.putText(annotated, result_text, (50, 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 3)

        # Add Nut check details
        nut_color = (0, 255, 0) if nut_present else (0, 0, 255)
        nut_text = f"Nut (Blue): {'PASS' if nut_present else 'FAIL'} diff={nut_diff:.2f}"
        cv2.putText(annotated, nut_text, (50, 90),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, nut_color, 2)

        # Add Washer check details
        washer_color = (0, 255, 0) if washer_present else (0, 0, 255)
        washer_text = f"Washer (Red): {'PASS' if washer_present else 'FAIL'} diff={washer_diff:.2f}"
        cv2.putText(annotated, washer_text, (50, 120),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, washer_color, 2)

        return annotated

    @staticmethod
    def checkNutAndPlateWasher(anImage: np.ndarray,
                    currentPictures: Dict[str, np.ndarray | None] = None,
                    componentQRCode: str = DOST, gamma: float = 2.0) -> Tuple[np.ndarray | None, bool]:
        """
        Check for the presence of Nut and Plate Washer state.

        Two-step color channel verification:
        1. Nut check using blue channel comparison with top_bearing
        2. Washer check using red channel comparison with top_bearing

        Args:
            anImage: Current camera image to analyze (Nut and Plate Washer image)
            currentPictures: Dictionary containing previously captured images
            componentQRCode: Component QR code for the assembly
            gamma: Unused parameter kept for compatibility

        Returns:
            Tuple of:
                - Annotated image showing the detection result
                - Boolean indicating if Nut and Plate Washer state is present (True) or not (False)
        """
        printBoldBlue(
            f"CheckNutAndPlateWasher.checkNutAndPlateWasher() called with "
            f"anImage = {anImage.shape if anImage is not None else 'None'}"
        )

        # Basic validation
        if componentQRCode is None:
            printBoldRed("CheckNutAndPlateWasher: componentQRCode is None, returning False")
            return anImage, False

        if anImage is None:
            printBoldRed("CheckNutAndPlateWasher: anImage is None, returning False")
            return anImage, False

        if currentPictures is None:
            printBoldRed("CheckNutAndPlateWasher: currentPictures is None, returning False")
            return anImage, False

        # =====================================================================
        # HAND DETECTION CHECK - First check before any other processing
        # Ensure no operator hands are still in the frame
        # =====================================================================
        # if HandDetector.detectHands(anImage):
        #     printBoldRed("CheckNutAndPlateWasher: HAND DETECTION FAILED - Operator hands detected in image!")
        #     printBoldRed("CheckNutAndPlateWasher: Please ensure hands are completely out of frame before capturing")
        #     return anImage, False
        #
        # printBoldGreen("CheckNutAndPlateWasher: Hand detection passed - No hands in frame")

        # Get component type from QR code
        modelName, _, _ = getModel_LHSRHS_AndTonnage(componentQRCode)
        printBoldBlue(f"Got modelName as {modelName}")
        if modelName == UNKNOWN:
            modelName = "DOST"

        # Load parameters from configuration
        CheckNutAndPlateWasher._load_parameters_from_config()

        printBoldBlue(
            f"CheckNutAndPlateWasher: Using center=({CheckNutAndPlateWasher.CENTER_X}, {CheckNutAndPlateWasher.CENTER_Y})")
        printBoldBlue(f"CheckNutAndPlateWasher: Radii - Inner={CheckNutAndPlateWasher.INNER_RADIUS}, "
                      f"Middle={CheckNutAndPlateWasher.MIDDLE_RADIUS}, "
                      f"Outer={CheckNutAndPlateWasher.OUTER_RADIUS}")
        printBoldBlue(f"CheckNutAndPlateWasher: Thresholds - Nut={CheckNutAndPlateWasher.NUT_THRESHOLD}, "
                      f"Washer={CheckNutAndPlateWasher.WASHER_THRESHOLD}")

        # Get top bearing image (this is our reference)
        top_bearing_image = currentPictures.get(topBearingPictureKeyString, None)

        if top_bearing_image is None:
            printBoldRed("CheckNutAndPlateWasher: Top Bearing image is missing, returning False")
            return anImage, False

        try:
            # Get both annular masks
            mask_nut, mask_washer = CheckNutAndPlateWasher._get_all_masks(anImage.shape)

            # =====================================================================
            # STEP 1: Nut Check (Blue Channel)
            # Annular ring: INNER_RADIUS to MIDDLE_RADIUS
            # Compare nut vs top_bearing
            # =====================================================================
            nut_blue_avg = CheckNutAndPlateWasher._compute_blue_channel_average(anImage, mask_nut)
            tb_blue_avg = CheckNutAndPlateWasher._compute_blue_channel_average(top_bearing_image, mask_nut)

            nut_diff = nut_blue_avg - tb_blue_avg
            nut_present = nut_diff >= CheckNutAndPlateWasher.NUT_THRESHOLD

            printBoldBlue(
                f"CheckNutAndPlateWasher Step1: Nut blue avg = {nut_blue_avg:.2f}, TB blue avg = {tb_blue_avg:.2f}")
            if nut_present:
                printBoldGreen(
                    f"CheckNutAndPlateWasher: Nut Check PASSED - (Nut-TB) blue diff = {nut_diff:.2f} >= threshold = {CheckNutAndPlateWasher.NUT_THRESHOLD:.2f}"
                )
            else:
                printBoldRed(
                    f"CheckNutAndPlateWasher: Nut Check FAILED - (Nut-TB) blue diff = {nut_diff:.2f} < threshold = {CheckNutAndPlateWasher.NUT_THRESHOLD:.2f}"
                )

            # =====================================================================
            # STEP 2: Washer Check (Red Channel)
            # Annular ring: MIDDLE_RADIUS to OUTER_RADIUS
            # Compare top_bearing vs nut
            # =====================================================================
            tb_red_avg = CheckNutAndPlateWasher._compute_red_channel_average(top_bearing_image, mask_washer)
            nut_red_avg = CheckNutAndPlateWasher._compute_red_channel_average(anImage, mask_washer)

            washer_diff = tb_red_avg - nut_red_avg
            washer_present = washer_diff >= CheckNutAndPlateWasher.WASHER_THRESHOLD

            printBoldBlue(
                f"CheckNutAndPlateWasher Step2: TB red avg = {tb_red_avg:.2f}, Nut red avg = {nut_red_avg:.2f}")
            if washer_present:
                printBoldGreen(
                    f"CheckNutAndPlateWasher: Washer Check PASSED - (TB-Nut) red diff = {washer_diff:.2f} >= threshold = {CheckNutAndPlateWasher.WASHER_THRESHOLD:.2f}"
                )
            else:
                printBoldRed(
                    f"CheckNutAndPlateWasher: Washer Check FAILED - (TB-Nut) red diff = {washer_diff:.2f} < threshold = {CheckNutAndPlateWasher.WASHER_THRESHOLD:.2f}"
                )

            # =====================================================================
            # Final Decision: Both must be present
            # =====================================================================
            is_nut_present = nut_present and washer_present

            if is_nut_present:
                printBoldGreen("CheckNutAndPlateWasher: NUT+WASHER STATE DETECTED - Both checks passed")
            else:
                printBoldRed("CheckNutAndPlateWasher: NUT+WASHER STATE NOT DETECTED - One or both checks failed")

            # Create annotated output image
            annotated_image = CheckNutAndPlateWasher._create_annotated_image(
                anImage, is_nut_present,
                nut_present, washer_present,
                nut_diff, washer_diff
            )

            return annotated_image, is_nut_present

        except Exception as e:
            printBoldRed(f"CheckNutAndPlateWasher: Exception during detection: {e}")
            import traceback
            traceback.print_exc()
            return anImage, False

    @staticmethod
    def test(directory_path: str):
        """
        Test the CheckNutAndPlateWasher algorithm on images in the specified directory.

        Args:
            directory_path: Path to directory containing test images
        """
        GREEN = "\033[1;32m"
        RED = "\033[1;31m"
        YELLOW = "\033[1;33m"
        CYAN = "\033[1;36m"
        RESET = "\033[0m"

        print(f"{CYAN}{'=' * 80}{RESET}")
        print(f"{CYAN}CheckNutAndPlateWasher Test Suite{RESET}")
        print(f"{CYAN}{'=' * 80}{RESET}")
        print(f"\nDirectory: {directory_path}")
        print(f"Parameters:")
        print(f"  Center: ({CheckNutAndPlateWasher.CENTER_X}, {CheckNutAndPlateWasher.CENTER_Y})")
        print(f"  INNER_RADIUS: {CheckNutAndPlateWasher.INNER_RADIUS}")
        print(f"  MIDDLE_RADIUS: {CheckNutAndPlateWasher.MIDDLE_RADIUS}")
        print(f"  OUTER_RADIUS: {CheckNutAndPlateWasher.OUTER_RADIUS}")
        print(f"  Nut Threshold: {CheckNutAndPlateWasher.NUT_THRESHOLD}")
        print(f"  Washer Threshold: {CheckNutAndPlateWasher.WASHER_THRESHOLD}")

        # Find all PNG files
        all_files = glob.glob(os.path.join(directory_path, "*.png"))

        # Categorize files into lists
        list_top_bearing = sorted([f for f in all_files if "Top" in os.path.basename(f)])
        list_nut = sorted([f for f in all_files if "Nut and Plate Washer" in os.path.basename(f) or "Nut" in os.path.basename(f) or "No_Bung" in os.path.basename(f)])

        print(f"\n{YELLOW}Found files:{RESET}")
        print(f"  Top Bearing: {len(list_top_bearing)}")
        print(f"  Nut and Plate Washer: {len(list_nut)}")

        if not list_top_bearing:
            print(f"{RED}No Top Bearing images found!{RESET}")
            return

        if not list_nut:
            print(f"{RED}No Nut and Plate Washer images found!{RESET}")
            return

        # Load all images
        print(f"\n{YELLOW}Loading images...{RESET}")

        top_images = [(f, cv2.imread(f)) for f in list_top_bearing]
        # nut_images = [(f, cv2.imread(f)) for f in list_nut]

        nut_images = [(f, cv2.imread(f)) for f in list_nut]

        # Use first top bearing image as base
        top_base = top_images[0][1] if top_images else None

        # =====================================================================
        # TEST 1: Nut and Plate Washer images (should detect)
        # =====================================================================
        print(f"\n{CYAN}{'=' * 80}{RESET}")
        print(f"{CYAN}TEST 1: Nut and Plate Washer images (should detect Nut and Plate Washer){RESET}")
        print(f"{CYAN}{'=' * 80}{RESET}")
        print(f"{YELLOW}Base image: {os.path.basename(top_images[0][0])}{RESET}\n")

        nut_pass = 0
        nut_fail = 0

        for nut_file, nut_img in nut_images:
            currentPictures = {
                topBearingPictureKeyString: top_base
            }

            if DOSTPLUS in directory_path:
                _, result = CheckNutAndPlateWasher.checkNutAndPlateWasher(
                    nut_img, currentPictures,
                    "8201206$400112VA1C$11770$09.03.2025$C5211701$MEK$40Cr4 - B$1$SPDLASSY - KNULH$SNPL - MAT$")
            else:
                _, result = CheckNutAndPlateWasher.checkNutAndPlateWasher(nut_img, currentPictures, DOST)

            if result:
                nut_pass += 1
                print(f"  {os.path.basename(nut_file):45s} {GREEN}Nut and Plate Washer Present{RESET}")
            else:
                nut_fail += 1
                print(f"  {os.path.basename(nut_file):45s} {RED}Nut and Plate Washer Not Present{RESET}")

        print(
            f"\n  Summary: {GREEN}{nut_pass} Nut and Plate Washer Present{RESET}, {RED}{nut_fail} Nut and Plate Washer Not Present{RESET}")

        # =====================================================================
        # TEST 2: Top Bearing images (should NOT detect Nut and Plate Washer)
        # =====================================================================
        print(f"\n{CYAN}{'=' * 80}{RESET}")
        print(f"{CYAN}TEST 2: Top Bearing images (should NOT detect Nut and Plate Washer){RESET}")
        print(f"{CYAN}{'=' * 80}{RESET}")
        print(f"{YELLOW}Base image: {os.path.basename(top_images[0][0])}{RESET}\n")

        top_pass = 0
        top_fail = 0

        for top_file, top_img in top_images:
            currentPictures = {
                topBearingPictureKeyString: top_base
            }

            if DOSTPLUS in directory_path:
                _, result = CheckNutAndPlateWasher.checkNutAndPlateWasher(
                    top_img, currentPictures,
                    "8201206$400112VA1C$11770$09.03.2025$C5211701$MEK$40Cr4 - B$1$SPDLASSY - KNULH$SNPL - MAT$")
            else:
                _, result = CheckNutAndPlateWasher.checkNutAndPlateWasher(top_img, currentPictures, DOST)

            if result:
                top_pass += 1
                print(f"  {os.path.basename(top_file):45s} {GREEN}Nut and Plate Washer Present{RESET}")
            else:
                top_fail += 1
                print(f"  {os.path.basename(top_file):45s} {RED}Nut and Plate Washer Not Present{RESET}")

        print(
            f"\n  Summary: {GREEN}{top_pass} Nut and Plate Washer Present{RESET}, {RED}{top_fail} Nut and Plate Washer Not Present{RESET}")

        # =====================================================================
        # FINAL SUMMARY
        # =====================================================================
        print(f"\n{CYAN}{'=' * 80}{RESET}")
        print(f"{CYAN}FINAL SUMMARY{RESET}")
        print(f"{CYAN}{'=' * 80}{RESET}")

        print(
            f"  Nut and Plate Washer images:          {nut_pass}/{len(list_nut)} detected as Nut and Plate Washer Present (expected: all)")
        print(
            f"  Top Bearing images:      {top_pass}/{len(list_top_bearing)} detected as Nut and Plate Washer Present (expected: none)")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        CheckNutAndPlateWasher.test(sys.argv[1])
    else:
        CheckNutAndPlateWasher.test("C:/AshokLeylandImages/DOST/LHS")