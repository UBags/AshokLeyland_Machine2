"""
CheckNutAndPlateWasher - Detects presence of Nut and Plate Washer by comparing with previous assembly stage images.

Detection Method:
    Three-step verification:

    Step 1: Nut Check (B&W comparison)
        - Annular ring: INNER_RADIUS_OF_NUT to OUTER_RADIUS_OF_NUT
        - Compare nut_and_plate_washer_image vs hub_and_bottom_bearing_image
        - Convert to B&W, calculate average
        - If nut_avg - hub_avg > threshold → Step 1 passes

    Step 2: Plate Washer R-B Check
        - Annular ring: OUTER_RADIUS_OF_NUT to OUTER_RADIUS_OF_WASHER
        - Compare hub_and_bottom_bearing_image vs nut_and_plate_washer_image
        - Calculate R-B average
        - If hub_rb_avg - nut_rb_avg > threshold → Step 2 passes

    Step 3: Top Bearing Check (B&W comparison)
        - Annular ring: OUTER_RADIUS_OF_BEARING to OUTER_RADIUS_OF_WASHER
        - Compare top_bearing_image vs nut_and_plate_washer_image
        - Convert to B&W, calculate average
        - If top_avg - nut_avg > threshold → Step 3 passes

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


class CheckNutAndPlateWasher:
    """
    Checks for the presence of Nut and Plate Washer by comparing the current image
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
            cls.CENTER_X = CosThetaConfigurator.getNutAndPlateWasherCheckCenterX()
            cls.CENTER_Y = CosThetaConfigurator.getNutAndPlateWasherCheckCenterY()
            cls.INNER_RADIUS_OF_NUT = CosThetaConfigurator.getNutAndPlateWasherCheckInnerRadiusOfNut()
            cls.OUTER_RADIUS_OF_NUT = CosThetaConfigurator.getNutAndPlateWasherCheckOuterRadiusOfNut()
            cls.OUTER_RADIUS_OF_BEARING = CosThetaConfigurator.getNutAndPlateWasherCheckOuterRadiusOfBearing()
            cls.OUTER_RADIUS_OF_WASHER = CosThetaConfigurator.getNutAndPlateWasherCheckOuterRadiusOfWasher()
            cls.STEP1_THRESHOLD = CosThetaConfigurator.getNutAndPlateWasherCheckStep1Threshold()
            cls.STEP2_THRESHOLD = CosThetaConfigurator.getNutAndPlateWasherCheckStep2Threshold()
            cls.STEP3_THRESHOLD = CosThetaConfigurator.getNutAndPlateWasherCheckStep3Threshold()
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
                                is_nut_washer_present: bool,
                                step1_passed: bool,
                                step2_passed: bool,
                                step3_passed: bool,
                                step1_diff: float,
                                step2_diff: float,
                                step3_diff: float) -> np.ndarray:
        """Create an annotated version of the input image showing the detection result."""
        annotated = image.copy()

        # Draw the annular regions
        center = (CheckNutAndPlateWasher.CENTER_X, CheckNutAndPlateWasher.CENTER_Y)

        # Draw all radius circles for visualization
        cv2.circle(annotated, center, CheckNutAndPlateWasher.INNER_RADIUS_OF_NUT, (255, 255, 0), 1)  # Cyan
        cv2.circle(annotated, center, CheckNutAndPlateWasher.OUTER_RADIUS_OF_NUT, (255, 0, 255), 1)  # Magenta
        cv2.circle(annotated, center, CheckNutAndPlateWasher.OUTER_RADIUS_OF_BEARING, (0, 255, 255), 1)  # Yellow
        cv2.circle(annotated, center, CheckNutAndPlateWasher.OUTER_RADIUS_OF_WASHER,
                   (0, 255, 0) if is_nut_washer_present else (0, 0, 255), 2)

        # Add result text
        result_text = "NUT+WASHER: PRESENT" if is_nut_washer_present else "NUT+WASHER: NOT DETECTED"
        color = (0, 255, 0) if is_nut_washer_present else (0, 0, 255)
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
    def checkNutAndPlateWasher(anImage: np.ndarray,
                               currentPictures: Dict[str, np.ndarray | None] = None,
                               componentQRCode: str = DOST, gamma: float = 2.0) -> Tuple[np.ndarray | None, bool]:
        """
        Check for the presence of Nut and Plate Washer.

        Three-step verification:
        1. Nut check using B&W comparison with hub_and_bottom_bearing
        2. Plate Washer R-B check with hub_and_bottom_bearing
        3. Top Bearing B&W check

        Args:
            anImage: Current camera image to analyze (Nut and Plate Washer image)
            currentPictures: Dictionary containing previously captured images
            componentQRCode: Component QR code for the assembly

        Returns:
            Tuple of:
                - Annotated image showing the detection result
                - Boolean indicating if Nut and Plate Washer are present (True) or not (False)
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
        printBoldBlue(f"CheckNutAndPlateWasher: Radii - NutInner={CheckNutAndPlateWasher.INNER_RADIUS_OF_NUT}, "
                      f"NutOuter={CheckNutAndPlateWasher.OUTER_RADIUS_OF_NUT}, "
                      f"BearingOuter={CheckNutAndPlateWasher.OUTER_RADIUS_OF_BEARING}, "
                      f"WasherOuter={CheckNutAndPlateWasher.OUTER_RADIUS_OF_WASHER}")

        # Get base images
        hub_bottom_bearing_image = currentPictures.get(hubAndBottomBearingPictureKeyString, None)
        top_bearing_image = currentPictures.get(topBearingPictureKeyString, None)

        if hub_bottom_bearing_image is None:
            printBoldRed("CheckNutAndPlateWasher: Hub and Bottom Bearing image is missing, returning False")
            return anImage, False

        if top_bearing_image is None:
            printBoldRed("CheckNutAndPlateWasher: Top Bearing image is missing, returning False")
            return anImage, False

        try:
            # Get all annular masks
            mask_nut, mask_washer, mask_bearing = CheckNutAndPlateWasher._get_all_masks(anImage.shape)

            # =====================================================================
            # STEP 1: Nut Check (B&W comparison)
            # Annular ring: INNER_RADIUS_OF_NUT to OUTER_RADIUS_OF_NUT
            # =====================================================================
            nut_bw_avg = CheckNutAndPlateWasher._compute_bw_average(anImage, mask_nut)
            hub_bw_avg_step1 = CheckNutAndPlateWasher._compute_bw_average(hub_bottom_bearing_image, mask_nut)

            step1_diff = nut_bw_avg - hub_bw_avg_step1
            step1_passed = step1_diff > CheckNutAndPlateWasher.STEP1_THRESHOLD

            printBoldBlue(
                f"CheckNutAndPlateWasher Step1: Nut B&W avg = {nut_bw_avg:.1f}, Hub B&W avg = {hub_bw_avg_step1:.1f}")
            if step1_passed:
                printBoldGreen(
                    f"CheckNutAndPlateWasher: Step 1 PASSED - (Nut-Hub) B&W diff = {step1_diff:.1f} > threshold = {CheckNutAndPlateWasher.STEP1_THRESHOLD:.1f}"
                )
            else:
                printBoldRed(
                    f"CheckNutAndPlateWasher: Step 1 FAILED - (Nut-Hub) B&W diff = {step1_diff:.1f} <= threshold = {CheckNutAndPlateWasher.STEP1_THRESHOLD:.1f}"
                )

            # =====================================================================
            # STEP 2: Plate Washer R-B Check
            # Annular ring: OUTER_RADIUS_OF_NUT to OUTER_RADIUS_OF_WASHER
            # =====================================================================
            nut_rb_avg = CheckNutAndPlateWasher._compute_rb_average(anImage, mask_washer)
            hub_rb_avg = CheckNutAndPlateWasher._compute_rb_average(hub_bottom_bearing_image, mask_washer)

            # Note: hub_rb_avg - nut_rb_avg (opposite order from Step 1)
            step2_diff = hub_rb_avg - nut_rb_avg
            step2_passed = step2_diff > CheckNutAndPlateWasher.STEP2_THRESHOLD

            printBoldBlue(
                f"CheckNutAndPlateWasher Step2: Hub R-B avg = {hub_rb_avg:.1f}, Nut R-B avg = {nut_rb_avg:.1f}")
            if step2_passed:
                printBoldGreen(
                    f"CheckNutAndPlateWasher: Step 2 PASSED - (Hub-Nut) R-B diff = {step2_diff:.1f} > threshold = {CheckNutAndPlateWasher.STEP2_THRESHOLD:.1f}"
                )
            else:
                printBoldRed(
                    f"CheckNutAndPlateWasher: Step 2 FAILED - (Hub-Nut) R-B diff = {step2_diff:.1f} <= threshold = {CheckNutAndPlateWasher.STEP2_THRESHOLD:.1f}"
                )

            # =====================================================================
            # STEP 3: Top Bearing Check (B&W comparison)
            # Annular ring: OUTER_RADIUS_OF_BEARING to OUTER_RADIUS_OF_WASHER
            # =====================================================================
            nut_bw_avg_step3 = CheckNutAndPlateWasher._compute_bw_average(anImage, mask_bearing)
            top_bw_avg = CheckNutAndPlateWasher._compute_bw_average(top_bearing_image, mask_bearing)

            step3_diff = top_bw_avg - nut_bw_avg_step3
            step3_passed = step3_diff > CheckNutAndPlateWasher.STEP3_THRESHOLD

            printBoldBlue(
                f"CheckNutAndPlateWasher Step3: Top B&W avg = {top_bw_avg:.1f}, Nut B&W avg = {nut_bw_avg_step3:.1f}")
            if step3_passed:
                printBoldGreen(
                    f"CheckNutAndPlateWasher: Step 3 PASSED - (Top-Nut) B&W diff = {step3_diff:.1f} > threshold = {CheckNutAndPlateWasher.STEP3_THRESHOLD:.1f}"
                )
            else:
                printBoldRed(
                    f"CheckNutAndPlateWasher: Step 3 FAILED - (Top-Nut) B&W diff = {step3_diff:.1f} <= threshold = {CheckNutAndPlateWasher.STEP3_THRESHOLD:.1f}"
                )

            # =====================================================================
            # Final Decision
            # =====================================================================
            is_nut_washer_present = step1_passed and step2_passed and step3_passed

            if is_nut_washer_present:
                printBoldGreen("CheckNutAndPlateWasher: NUT AND PLATE WASHER DETECTED - All steps passed")
            else:
                printBoldRed("CheckNutAndPlateWasher: NUT AND PLATE WASHER NOT DETECTED - One or more steps failed")

            # Create annotated output image
            annotated_image = CheckNutAndPlateWasher._create_annotated_image(
                anImage, is_nut_washer_present,
                step1_passed, step2_passed, step3_passed,
                step1_diff, step2_diff, step3_diff
            )

            return annotated_image, is_nut_washer_present

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
        print(f"  INNER_RADIUS_OF_NUT: {CheckNutAndPlateWasher.INNER_RADIUS_OF_NUT}")
        print(f"  OUTER_RADIUS_OF_NUT: {CheckNutAndPlateWasher.OUTER_RADIUS_OF_NUT}")
        print(f"  OUTER_RADIUS_OF_BEARING: {CheckNutAndPlateWasher.OUTER_RADIUS_OF_BEARING}")
        print(f"  OUTER_RADIUS_OF_WASHER: {CheckNutAndPlateWasher.OUTER_RADIUS_OF_WASHER}")
        print(f"  Step 1 Threshold: {CheckNutAndPlateWasher.STEP1_THRESHOLD}")
        print(f"  Step 2 Threshold: {CheckNutAndPlateWasher.STEP2_THRESHOLD}")
        print(f"  Step 3 Threshold: {CheckNutAndPlateWasher.STEP3_THRESHOLD}")

        # Find all PNG files
        all_files = glob.glob(os.path.join(directory_path, "*.png"))

        # Categorize files into lists
        list_hub_and_bottom_bearing = sorted(
            [f for f in all_files if "Hub_and" in os.path.basename(f) or "Hub and" in os.path.basename(f)])
        list_top_bearing = sorted([f for f in all_files if "Top" in os.path.basename(f)])
        list_nut_and_plate_washer = sorted([f for f in all_files if "Nut" in os.path.basename(f)])

        print(f"\n{YELLOW}Found files:{RESET}")
        print(f"  Hub and Bottom Bearing: {len(list_hub_and_bottom_bearing)}")
        print(f"  Top Bearing: {len(list_top_bearing)}")
        print(f"  Nut and Plate Washer: {len(list_nut_and_plate_washer)}")

        if not list_hub_and_bottom_bearing:
            print(f"{RED}No Hub and Bottom Bearing images found!{RESET}")
            return

        if not list_top_bearing:
            print(f"{RED}No Top Bearing images found!{RESET}")
            return

        if not list_nut_and_plate_washer:
            print(f"{RED}No Nut and Plate Washer images found!{RESET}")
            return

        # Load all images
        print(f"\n{YELLOW}Loading images...{RESET}")

        hub_images = [(f, cv2.imread(f)) for f in list_hub_and_bottom_bearing]
        top_images = [(f, cv2.imread(f)) for f in list_top_bearing]
        nut_images = [(f, cv2.imread(f)) for f in list_nut_and_plate_washer]

        # Use first images as base
        hub_base = hub_images[0][1] if hub_images else None
        top_base = top_images[0][1] if top_images else None

        # =====================================================================
        # TEST 1: Nut and Plate Washer images (should detect)
        # =====================================================================
        print(f"\n{CYAN}{'=' * 80}{RESET}")
        print(f"{CYAN}TEST 1: Nut and Plate Washer images (should detect Nut+Washer){RESET}")
        print(f"{CYAN}{'=' * 80}{RESET}")
        print(
            f"{YELLOW}Base images: {os.path.basename(hub_images[0][0])}, {os.path.basename(top_images[0][0])}{RESET}\n")

        nut_pass = 0
        nut_fail = 0

        for nut_file, nut_img in nut_images:
            currentPictures = {
                hubAndBottomBearingPictureKeyString: hub_base,
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
                print(f"  {os.path.basename(nut_file):45s} {GREEN}Nut+Washer Present{RESET}")
            else:
                nut_fail += 1
                print(f"  {os.path.basename(nut_file):45s} {RED}Nut+Washer Not Present{RESET}")

        print(
            f"\n  Summary: {GREEN}{nut_pass} Nut+Washer Present{RESET}, {RED}{nut_fail} Nut+Washer Not Present{RESET}")

        # =====================================================================
        # TEST 2: Top Bearing images (should NOT detect Nut+Washer)
        # =====================================================================
        print(f"\n{CYAN}{'=' * 80}{RESET}")
        print(f"{CYAN}TEST 2: Top Bearing images (should NOT detect Nut+Washer){RESET}")
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
                _, result = CheckNutAndPlateWasher.checkNutAndPlateWasher(
                    top_img, currentPictures,
                    "8201206$400112VA1C$11770$09.03.2025$C5211701$MEK$40Cr4 - B$1$SPDLASSY - KNULH$SNPL - MAT$")
            else:
                _, result = CheckNutAndPlateWasher.checkNutAndPlateWasher(top_img, currentPictures, DOST)

            if result:
                top_pass += 1
                print(f"  {os.path.basename(top_file):45s} {GREEN}Nut+Washer Present{RESET}")
            else:
                top_fail += 1
                print(f"  {os.path.basename(top_file):45s} {RED}Nut+Washer Not Present{RESET}")

        print(
            f"\n  Summary: {GREEN}{top_pass} Nut+Washer Present{RESET}, {RED}{top_fail} Nut+Washer Not Present{RESET}")

        # =====================================================================
        # TEST 3: Hub and Bottom Bearing images (should NOT detect Nut+Washer)
        # =====================================================================
        print(f"\n{CYAN}{'=' * 80}{RESET}")
        print(f"{CYAN}TEST 3: Hub and Bottom Bearing images (should NOT detect Nut+Washer){RESET}")
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
                _, result = CheckNutAndPlateWasher.checkNutAndPlateWasher(
                    hub_img, currentPictures,
                    "8201206$400112VA1C$11770$09.03.2025$C5211701$MEK$40Cr4 - B$1$SPDLASSY - KNULH$SNPL - MAT$")
            else:
                _, result = CheckNutAndPlateWasher.checkNutAndPlateWasher(hub_img, currentPictures, DOST)

            if result:
                hub_pass += 1
                print(f"  {os.path.basename(hub_file):45s} {GREEN}Nut+Washer Present{RESET}")
            else:
                hub_fail += 1
                print(f"  {os.path.basename(hub_file):45s} {RED}Nut+Washer Not Present{RESET}")

        print(
            f"\n  Summary: {GREEN}{hub_pass} Nut+Washer Present{RESET}, {RED}{hub_fail} Nut+Washer Not Present{RESET}")

        # =====================================================================
        # FINAL SUMMARY
        # =====================================================================
        print(f"\n{CYAN}{'=' * 80}{RESET}")
        print(f"{CYAN}FINAL SUMMARY{RESET}")
        print(f"{CYAN}{'=' * 80}{RESET}")

        print(
            f"  Nut+Washer images:       {nut_pass}/{len(list_nut_and_plate_washer)} detected as Nut+Washer Present (expected: all)")
        print(
            f"  Top Bearing images:      {top_pass}/{len(list_top_bearing)} detected as Nut+Washer Present (expected: none)")
        print(
            f"  Hub+Bottom Brg images:   {hub_pass}/{len(list_hub_and_bottom_bearing)} detected as Nut+Washer Present (expected: none)")


# if __name__ == "__main__":
#     import sys
#
#     if len(sys.argv) > 1:
#         CheckNutAndPlateWasher.test(sys.argv[1])
#     else:
#         CheckNutAndPlateWasher.test("C:/AshokLeylandImages/DOST/LHS")



# ================================================================================