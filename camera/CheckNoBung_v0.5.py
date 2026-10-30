"""
CheckNoBunk - Detects presence of No Bung state by comparing with previous assembly stage images.

Detection Method:
    Three-step verification (same logic as CheckNutAndPlateWasher):

    Step 1: Nut Check (B&W comparison)
        - Annular ring: INNER_RADIUS_OF_NUT to OUTER_RADIUS_OF_NUT
        - Compare no_bung_image vs hub_and_bottom_bearing_image
        - Convert to B&W, calculate average
        - If no_bung_avg - hub_avg > threshold → Step 1 passes

    Step 2: Plate Washer R-B Check
        - Annular ring: OUTER_RADIUS_OF_NUT to OUTER_RADIUS_OF_WASHER
        - Compare hub_and_bottom_bearing_image vs no_bung_image
        - Calculate R-B average
        - If hub_rb_avg - no_bung_rb_avg > threshold → Step 2 passes

    Step 3: Top Bearing Check (B&W comparison)
        - Annular ring: OUTER_RADIUS_OF_BEARING to OUTER_RADIUS_OF_WASHER
        - Compare top_bearing_image vs no_bung_image
        - Convert to B&W, calculate average
        - If top_avg - no_bung_avg > threshold → Step 3 passes

Parameters (from configuration):
    CENTER_X, CENTER_Y: Center of the region (default: 630, 350)
    INNER_RADIUS_OF_NUT: Inner radius for nut check (default: 26)
    OUTER_RADIUS_OF_NUT: Outer radius for nut check (default: 39)
    OUTER_RADIUS_OF_BEARING: Outer radius for bearing region (default: 48)
    OUTER_RADIUS_OF_WASHER: Outer radius for washer check (default: 59)
    STEP1_THRESHOLD: Threshold for nut B&W check (default: 25)
    STEP2_THRESHOLD: Threshold for plate washer R-B check (default: 20)
    STEP3_THRESHOLD: Threshold for top bearing B&W check (default: 25)
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


class CheckNoBunk:
    """
    Checks for the presence of No Bung state by comparing the current image
    with previously captured images using annular masks and color/brightness analysis.
    """

    # Default detection parameters (will be overridden by configuration)
    CENTER_X = 630
    CENTER_Y = 350
    INNER_RADIUS_OF_NUT = 26
    OUTER_RADIUS_OF_NUT = 39
    OUTER_RADIUS_OF_BEARING = 48
    OUTER_RADIUS_OF_WASHER = 59
    STEP1_THRESHOLD = 25.0
    STEP2_THRESHOLD = 20.0
    STEP3_THRESHOLD = 25.0

    # Cached annular masks
    _annular_mask_nut = None
    _annular_mask_washer = None
    _annular_mask_bearing = None
    _mask_shape = None

    @classmethod
    def _load_parameters_from_config(cls):
        """Load parameters from configuration file."""
        try:
            cls.CENTER_X = CosThetaConfigurator.getNoBunkCheckCenterX()
            cls.CENTER_Y = CosThetaConfigurator.getNoBunkCheckCenterY()
            cls.INNER_RADIUS_OF_NUT = CosThetaConfigurator.getNoBungCheckInnerRadiusOfNut()
            cls.OUTER_RADIUS_OF_NUT = CosThetaConfigurator.getNoBungCheckOuterRadiusOfNut()
            cls.OUTER_RADIUS_OF_BEARING = CosThetaConfigurator.getNoBungCheckOuterRadiusOfBearing()
            cls.OUTER_RADIUS_OF_WASHER = CosThetaConfigurator.getNoBungCheckOuterRadiusOfWasher()
            cls.STEP1_THRESHOLD = CosThetaConfigurator.getNoBungCheckStep1Threshold()
            cls.STEP2_THRESHOLD = CosThetaConfigurator.getNoBungCheckStep2Threshold()
            cls.STEP3_THRESHOLD = CosThetaConfigurator.getNoBungCheckStep3Threshold()
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
        Get all three annular masks, creating them if needed.
        """
        if cls._mask_shape != image_shape[:2]:
            # Mask for Step 1: Nut check (INNER_RADIUS_OF_NUT to OUTER_RADIUS_OF_NUT)
            cls._annular_mask_nut = cls._get_annular_mask(
                image_shape, cls.INNER_RADIUS_OF_NUT, cls.OUTER_RADIUS_OF_NUT
            )

            # Mask for Step 2: Plate Washer R-B check (OUTER_RADIUS_OF_NUT to OUTER_RADIUS_OF_WASHER)
            cls._annular_mask_washer = cls._get_annular_mask(
                image_shape, cls.OUTER_RADIUS_OF_NUT, cls.OUTER_RADIUS_OF_WASHER
            )

            # Mask for Step 3: Top Bearing check (OUTER_RADIUS_OF_BEARING to OUTER_RADIUS_OF_WASHER)
            cls._annular_mask_bearing = cls._get_annular_mask(
                image_shape, cls.OUTER_RADIUS_OF_BEARING, cls.OUTER_RADIUS_OF_WASHER
            )

            cls._mask_shape = image_shape[:2]

        return cls._annular_mask_nut, cls._annular_mask_washer, cls._annular_mask_bearing

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
    def _create_annotated_image(image: np.ndarray,
                                is_no_bung_present: bool,
                                step1_passed: bool,
                                step2_passed: bool,
                                step3_passed: bool,
                                step1_diff: float,
                                step2_diff: float,
                                step3_diff: float) -> np.ndarray:
        """Create an annotated version of the input image showing the detection result."""
        annotated = image.copy()

        # Draw the annular regions
        center = (CheckNoBung.CENTER_X, CheckNoBung.CENTER_Y)

        # Draw all radius circles for visualization
        cv2.circle(annotated, center, CheckNoBung.INNER_RADIUS_OF_NUT, (255, 255, 0), 1)  # Cyan
        cv2.circle(annotated, center, CheckNoBung.OUTER_RADIUS_OF_NUT, (255, 0, 255), 1)  # Magenta
        cv2.circle(annotated, center, CheckNoBung.OUTER_RADIUS_OF_BEARING, (0, 255, 255), 1)  # Yellow
        cv2.circle(annotated, center, CheckNoBung.OUTER_RADIUS_OF_WASHER,
                   (0, 255, 0) if is_no_bung_present else (0, 0, 255), 2)

        # Add result text
        result_text = "NO BUNG: PRESENT" if is_no_bung_present else "NO BUNG: NOT DETECTED"
        color = (0, 255, 0) if is_no_bung_present else (0, 0, 255)
        cv2.putText(annotated, result_text, (50, 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 3)

        # Add Step 1 details
        step1_color = (0, 255, 0) if step1_passed else (0, 0, 255)
        step1_text = f"Step1 (Nut B&W): {'PASS' if step1_passed else 'FAIL'} diff={step1_diff:.1f}"
        cv2.putText(annotated, step1_text, (50, 90),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, step1_color, 2)

        # Add Step 2 details
        step2_color = (0, 255, 0) if step2_passed else (0, 0, 255)
        step2_text = f"Step2 (Washer R-B): {'PASS' if step2_passed else 'FAIL'} diff={step2_diff:.1f}"
        cv2.putText(annotated, step2_text, (50, 120),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, step2_color, 2)

        # Add Step 3 details
        step3_color = (0, 255, 0) if step3_passed else (0, 0, 255)
        step3_text = f"Step3 (Top Brg B&W): {'PASS' if step3_passed else 'FAIL'} diff={step3_diff:.1f}"
        cv2.putText(annotated, step3_text, (50, 150),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, step3_color, 2)

        return annotated

    @staticmethod
    def checkNoBung(anImage: np.ndarray,
                    currentPictures: Dict[str, np.ndarray | None] = None,
                    componentQRCode: str = DOST, gamma: float = 2.0) -> Tuple[np.ndarray | None, bool]:
        """
        Check for the presence of No Bung state.

        Three-step verification:
        1. Nut check using B&W comparison with hub_and_bottom_bearing
        2. Plate Washer R-B check with hub_and_bottom_bearing
        3. Top Bearing B&W check

        Args:
            anImage: Current camera image to analyze (No Bung image)
            currentPictures: Dictionary containing previously captured images
            componentQRCode: Component QR code for the assembly

        Returns:
            Tuple of:
                - Annotated image showing the detection result
                - Boolean indicating if No Bung state is present (True) or not (False)
        """
        printBoldBlue(
            f"CheckNoBunk.checkNoBung() called with "
            f"anImage = {anImage.shape if anImage is not None else 'None'}"
        )

        # Basic validation
        if componentQRCode is None:
            printBoldRed("CheckNoBunk: componentQRCode is None, returning False")
            return anImage, False

        if anImage is None:
            printBoldRed("CheckNoBunk: anImage is None, returning False")
            return anImage, False

        if currentPictures is None:
            printBoldRed("CheckNoBunk: currentPictures is None, returning False")
            return anImage, False

        # =====================================================================
        # HAND DETECTION CHECK - First check before any other processing
        # Ensure no operator hands are still in the frame
        # =====================================================================
        # if HandDetector.detectHands(anImage):
        #     printBoldRed("CheckNoBunk: HAND DETECTION FAILED - Operator hands detected in image!")
        #     printBoldRed("CheckNoBunk: Please ensure hands are completely out of frame before capturing")
        #     return anImage, False
        #
        # printBoldGreen("CheckNoBunk: Hand detection passed - No hands in frame")

        # Get component type from QR code
        modelName, _, _ = getModel_LHSRHS_AndTonnage(componentQRCode)
        printBoldBlue(f"Got modelName as {modelName}")
        if modelName == UNKNOWN:
            modelName = "DOST"

        # Load parameters from configuration
        CheckNoBung._load_parameters_from_config()

        printBoldBlue(
            f"CheckNoBunk: Using center=({CheckNoBung.CENTER_X}, {CheckNoBung.CENTER_Y})")
        printBoldBlue(f"CheckNoBunk: Radii - NutInner={CheckNoBung.INNER_RADIUS_OF_NUT}, "
                      f"NutOuter={CheckNoBung.OUTER_RADIUS_OF_NUT}, "
                      f"BearingOuter={CheckNoBung.OUTER_RADIUS_OF_BEARING}, "
                      f"WasherOuter={CheckNoBung.OUTER_RADIUS_OF_WASHER}")

        # Get base images
        hub_bottom_bearing_image = currentPictures.get(hubAndBottomBearingPictureKeyString, None)
        top_bearing_image = currentPictures.get(topBearingPictureKeyString, None)

        if hub_bottom_bearing_image is None:
            printBoldRed("CheckNoBunk: Hub and Bottom Bearing image is missing, returning False")
            return anImage, False

        if top_bearing_image is None:
            printBoldRed("CheckNoBunk: Top Bearing image is missing, returning False")
            return anImage, False

        try:
            # Get all annular masks
            mask_nut, mask_washer, mask_bearing = CheckNoBung._get_all_masks(anImage.shape)

            # =====================================================================
            # STEP 1: Nut Check (B&W comparison)
            # Annular ring: INNER_RADIUS_OF_NUT to OUTER_RADIUS_OF_NUT
            # =====================================================================
            no_bung_bw_avg = CheckNoBung._compute_bw_average(anImage, mask_nut)
            hub_bw_avg_step1 = CheckNoBung._compute_bw_average(hub_bottom_bearing_image, mask_nut)

            step1_diff = no_bung_bw_avg - hub_bw_avg_step1
            step1_passed = step1_diff > CheckNoBung.STEP1_THRESHOLD

            printBoldBlue(
                f"CheckNoBunk Step1: NoBung B&W avg = {no_bung_bw_avg:.1f}, Hub B&W avg = {hub_bw_avg_step1:.1f}")
            if step1_passed:
                printBoldGreen(
                    f"CheckNoBunk: Step 1 PASSED - (NoBung-Hub) B&W diff = {step1_diff:.1f} > threshold = {CheckNoBung.STEP1_THRESHOLD:.1f}"
                )
            else:
                printBoldRed(
                    f"CheckNoBunk: Step 1 FAILED - (NoBung-Hub) B&W diff = {step1_diff:.1f} <= threshold = {CheckNoBung.STEP1_THRESHOLD:.1f}"
                )

            # =====================================================================
            # STEP 2: Plate Washer R-B Check
            # Annular ring: OUTER_RADIUS_OF_NUT to OUTER_RADIUS_OF_WASHER
            # =====================================================================
            no_bung_rb_avg = CheckNoBung._compute_rb_average(anImage, mask_washer)
            hub_rb_avg = CheckNoBung._compute_rb_average(hub_bottom_bearing_image, mask_washer)

            # Note: hub_rb_avg - no_bung_rb_avg (opposite order from Step 1)
            step2_diff = hub_rb_avg - no_bung_rb_avg
            step2_passed = step2_diff > CheckNoBung.STEP2_THRESHOLD

            printBoldBlue(
                f"CheckNoBunk Step2: Hub R-B avg = {hub_rb_avg:.1f}, NoBung R-B avg = {no_bung_rb_avg:.1f}")
            if step2_passed:
                printBoldGreen(
                    f"CheckNoBunk: Step 2 PASSED - (Hub-NoBung) R-B diff = {step2_diff:.1f} > threshold = {CheckNoBung.STEP2_THRESHOLD:.1f}"
                )
            else:
                printBoldRed(
                    f"CheckNoBunk: Step 2 FAILED - (Hub-NoBung) R-B diff = {step2_diff:.1f} <= threshold = {CheckNoBung.STEP2_THRESHOLD:.1f}"
                )

            # =====================================================================
            # STEP 3: Top Bearing Check (B&W comparison)
            # Annular ring: OUTER_RADIUS_OF_BEARING to OUTER_RADIUS_OF_WASHER
            # =====================================================================
            no_bung_bw_avg_step3 = CheckNoBung._compute_bw_average(anImage, mask_bearing)
            top_bw_avg = CheckNoBung._compute_bw_average(top_bearing_image, mask_bearing)

            step3_diff = top_bw_avg - no_bung_bw_avg_step3
            step3_passed = step3_diff > CheckNoBung.STEP3_THRESHOLD

            printBoldBlue(
                f"CheckNoBunk Step3: Top B&W avg = {top_bw_avg:.1f}, NoBung B&W avg = {no_bung_bw_avg_step3:.1f}")
            if step3_passed:
                printBoldGreen(
                    f"CheckNoBunk: Step 3 PASSED - (Top-NoBung) B&W diff = {step3_diff:.1f} > threshold = {CheckNoBung.STEP3_THRESHOLD:.1f}"
                )
            else:
                printBoldRed(
                    f"CheckNoBunk: Step 3 FAILED - (Top-NoBung) B&W diff = {step3_diff:.1f} <= threshold = {CheckNoBung.STEP3_THRESHOLD:.1f}"
                )

            # =====================================================================
            # Final Decision
            # =====================================================================
            is_no_bung_present = step1_passed and step2_passed and step3_passed

            if is_no_bung_present:
                printBoldGreen("CheckNoBunk: NO BUNG STATE DETECTED - All steps passed")
            else:
                printBoldRed("CheckNoBunk: NO BUNG STATE NOT DETECTED - One or more steps failed")

            # Create annotated output image
            annotated_image = CheckNoBung._create_annotated_image(
                anImage, is_no_bung_present,
                step1_passed, step2_passed, step3_passed,
                step1_diff, step2_diff, step3_diff
            )

            return annotated_image, is_no_bung_present

        except Exception as e:
            printBoldRed(f"CheckNoBunk: Exception during detection: {e}")
            import traceback
            traceback.print_exc()
            return anImage, False

    @staticmethod
    def test(directory_path: str):
        """
        Test the CheckNoBunk algorithm on images in the specified directory.

        Args:
            directory_path: Path to directory containing test images
        """
        GREEN = "\033[1;32m"
        RED = "\033[1;31m"
        YELLOW = "\033[1;33m"
        CYAN = "\033[1;36m"
        RESET = "\033[0m"

        print(f"{CYAN}{'=' * 80}{RESET}")
        print(f"{CYAN}CheckNoBunk Test Suite{RESET}")
        print(f"{CYAN}{'=' * 80}{RESET}")
        print(f"\nDirectory: {directory_path}")
        print(f"Parameters:")
        print(f"  Center: ({CheckNoBung.CENTER_X}, {CheckNoBung.CENTER_Y})")
        print(f"  INNER_RADIUS_OF_NUT: {CheckNoBung.INNER_RADIUS_OF_NUT}")
        print(f"  OUTER_RADIUS_OF_NUT: {CheckNoBung.OUTER_RADIUS_OF_NUT}")
        print(f"  OUTER_RADIUS_OF_BEARING: {CheckNoBung.OUTER_RADIUS_OF_BEARING}")
        print(f"  OUTER_RADIUS_OF_WASHER: {CheckNoBung.OUTER_RADIUS_OF_WASHER}")
        print(f"  Step 1 Threshold: {CheckNoBung.STEP1_THRESHOLD}")
        print(f"  Step 2 Threshold: {CheckNoBung.STEP2_THRESHOLD}")
        print(f"  Step 3 Threshold: {CheckNoBung.STEP3_THRESHOLD}")

        # Find all PNG files
        all_files = glob.glob(os.path.join(directory_path, "*.png"))

        # Categorize files into lists
        list_hub_and_bottom_bearing = sorted(
            [f for f in all_files if "Hub_and" in os.path.basename(f) or "Hub and" in os.path.basename(f)])
        list_top_bearing = sorted([f for f in all_files if "Top" in os.path.basename(f)])
        # Look for NoBung images - files containing "NoBung" or "No_Bung" or "No Bung"
        # list_no_bung = sorted([f for f in all_files if "NoBung" in os.path.basename(f) or "No_Bung" in os.path.basename(f) or "No Bung" in os.path.basename(f)])
        list_no_bung = sorted([f for f in all_files if "Nut" in os.path.basename(f)])

        print(f"\n{YELLOW}Found files:{RESET}")
        print(f"  Hub and Bottom Bearing: {len(list_hub_and_bottom_bearing)}")
        print(f"  Top Bearing: {len(list_top_bearing)}")
        print(f"  No Bung: {len(list_no_bung)}")

        if not list_hub_and_bottom_bearing:
            print(f"{RED}No Hub and Bottom Bearing images found!{RESET}")
            return

        if not list_top_bearing:
            print(f"{RED}No Top Bearing images found!{RESET}")
            return

        if not list_no_bung:
            print(f"{RED}No No Bung images found!{RESET}")
            return

        # Load all images
        print(f"\n{YELLOW}Loading images...{RESET}")

        hub_images = [(f, cv2.imread(f)) for f in list_hub_and_bottom_bearing]
        top_images = [(f, cv2.imread(f)) for f in list_top_bearing]
        no_bung_images = [(f, cv2.imread(f)) for f in list_no_bung]

        # Use first images as base
        hub_base = hub_images[0][1] if hub_images else None
        top_base = top_images[0][1] if top_images else None

        # =====================================================================
        # TEST 1: No Bung images (should detect)
        # =====================================================================
        print(f"\n{CYAN}{'=' * 80}{RESET}")
        print(f"{CYAN}TEST 1: No Bung images (should detect No Bung){RESET}")
        print(f"{CYAN}{'=' * 80}{RESET}")
        print(
            f"{YELLOW}Base images: {os.path.basename(hub_images[0][0])}, {os.path.basename(top_images[0][0])}{RESET}\n")

        no_bung_pass = 0
        no_bung_fail = 0

        for no_bung_file, no_bung_img in no_bung_images:
            currentPictures = {
                hubAndBottomBearingPictureKeyString: hub_base,
                topBearingPictureKeyString: top_base
            }

            if DOSTPLUS in directory_path:
                _, result = CheckNoBung.checkNoBunk(
                    no_bung_img, currentPictures,
                    "8201206$400112VA1C$11770$09.03.2025$C5211701$MEK$40Cr4 - B$1$SPDLASSY - KNULH$SNPL - MAT$")
            else:
                _, result = CheckNoBung.checkNoBunk(no_bung_img, currentPictures, DOST)

            if result:
                no_bung_pass += 1
                print(f"  {os.path.basename(no_bung_file):45s} {GREEN}No Bung Present{RESET}")
            else:
                no_bung_fail += 1
                print(f"  {os.path.basename(no_bung_file):45s} {RED}No Bung Not Present{RESET}")

        print(
            f"\n  Summary: {GREEN}{no_bung_pass} No Bung Present{RESET}, {RED}{no_bung_fail} No Bung Not Present{RESET}")

        # =====================================================================
        # TEST 2: Top Bearing images (should NOT detect No Bung)
        # =====================================================================
        print(f"\n{CYAN}{'=' * 80}{RESET}")
        print(f"{CYAN}TEST 2: Top Bearing images (should NOT detect No Bung){RESET}")
        print(f"{CYAN}{'=' * 80}{RESET}")
        print(
            f"{YELLOW}Base images: {os.path.basename(hub_images[0][0])}, {os.path.basename(top_images[0][0])}{RESET}\n")

        top_pass = 0
        top_fail = 0

        for top_file, top_img in top_images:
            currentPictures = {
                hubAndBottomBearingPictureKeyString: hub_base,
                topBearingPictureKeyString: top_base
            }

            if DOSTPLUS in directory_path:
                _, result = CheckNoBung.checkNoBunk(
                    top_img, currentPictures,
                    "8201206$400112VA1C$11770$09.03.2025$C5211701$MEK$40Cr4 - B$1$SPDLASSY - KNULH$SNPL - MAT$")
            else:
                _, result = CheckNoBung.checkNoBunk(top_img, currentPictures, DOST)

            if result:
                top_pass += 1
                print(f"  {os.path.basename(top_file):45s} {GREEN}No Bung Present{RESET}")
            else:
                top_fail += 1
                print(f"  {os.path.basename(top_file):45s} {RED}No Bung Not Present{RESET}")

        print(
            f"\n  Summary: {GREEN}{top_pass} No Bung Present{RESET}, {RED}{top_fail} No Bung Not Present{RESET}")

        # =====================================================================
        # TEST 3: Hub and Bottom Bearing images (should NOT detect No Bung)
        # =====================================================================
        print(f"\n{CYAN}{'=' * 80}{RESET}")
        print(f"{CYAN}TEST 3: Hub and Bottom Bearing images (should NOT detect No Bung){RESET}")
        print(f"{CYAN}{'=' * 80}{RESET}")
        print(
            f"{YELLOW}Base images: {os.path.basename(hub_images[0][0])}, {os.path.basename(top_images[0][0])}{RESET}\n")

        hub_pass = 0
        hub_fail = 0

        for hub_file, hub_img in hub_images:
            currentPictures = {
                hubAndBottomBearingPictureKeyString: hub_base,
                topBearingPictureKeyString: top_base
            }

            if DOSTPLUS in directory_path:
                _, result = CheckNoBung.checkNoBunk(
                    hub_img, currentPictures,
                    "8201206$400112VA1C$11770$09.03.2025$C5211701$MEK$40Cr4 - B$1$SPDLASSY - KNULH$SNPL - MAT$")
            else:
                _, result = CheckNoBung.checkNoBunk(hub_img, currentPictures, DOST)

            if result:
                hub_pass += 1
                print(f"  {os.path.basename(hub_file):45s} {GREEN}No Bung Present{RESET}")
            else:
                hub_fail += 1
                print(f"  {os.path.basename(hub_file):45s} {RED}No Bung Not Present{RESET}")

        print(
            f"\n  Summary: {GREEN}{hub_pass} No Bung Present{RESET}, {RED}{hub_fail} No Bung Not Present{RESET}")

        # =====================================================================
        # FINAL SUMMARY
        # =====================================================================
        print(f"\n{CYAN}{'=' * 80}{RESET}")
        print(f"{CYAN}FINAL SUMMARY{RESET}")
        print(f"{CYAN}{'=' * 80}{RESET}")

        print(
            f"  No Bung images:          {no_bung_pass}/{len(list_no_bung)} detected as No Bung Present (expected: all)")
        print(
            f"  Top Bearing images:      {top_pass}/{len(list_top_bearing)} detected as No Bung Present (expected: none)")
        print(
            f"  Hub+Bottom Brg images:   {hub_pass}/{len(list_hub_and_bottom_bearing)} detected as No Bung Present (expected: none)")


# if __name__ == "__main__":
#     import sys
#
#     if len(sys.argv) > 1:
#         CheckNoBunk.test(sys.argv[1])
#     else:
#         CheckNoBunk.test("C:/AshokLeylandImages/DOSTPLUS/LHS")