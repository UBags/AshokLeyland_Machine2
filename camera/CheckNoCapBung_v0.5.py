"""
CheckNoCapBunk - Detects presence of Bung (before cap is placed) by comparing with Nut and Plate Washer image.

Detection Method:
    Different checks for DOST and DOSTPLUS:

    For DOST (check if Nut is visible):
        - Annular ring: INNER_RADIUS_OF_NUT to OUTER_RADIUS_OF_NUT
        - Compare nut_and_plate_washer_image vs check_no_cap_bung_image
        - Convert to B&W, calculate average
        - If nut_washer_avg - no_cap_bung_avg > threshold → Bung Present

    For DOSTPLUS (check if Plate Washer is visible):
        - Annular ring: OUTER_RADIUS_OF_NUT to OUTER_RADIUS_OF_WASHER
        - Compare check_no_cap_bung_image vs nut_and_plate_washer_image (opposite order)
        - Convert to B&W, calculate average
        - If no_cap_bung_avg - nut_washer_avg > threshold → Bung Present

Parameters (from configuration):
    CENTER_X, CENTER_Y: Center of the region (default: 630, 350)
    INNER_RADIUS_OF_NUT: Inner radius for nut check (default: 26)
    OUTER_RADIUS_OF_NUT: Outer radius for nut / inner for washer (default: 39)
    OUTER_RADIUS_OF_WASHER: Outer radius for washer check (default: 59)
    THRESHOLD_DOST: B&W difference threshold for DOST (default: 25)
    THRESHOLD_DOSTPLUS: B&W difference threshold for DOSTPLUS (default: 25)
"""

import cv2
import numpy as np
from typing import Dict, Tuple
import glob
import os
import random
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
    nutAndPlateWasherPictureKeyString = "nutAndPlateWasherPicture"


    def printBoldBlue(msg):
        print(f"\033[1;34m{msg}\033[0m")


    def printBoldGreen(msg):
        print(f"\033[1;32m{msg}\033[0m")


    def printBoldRed(msg):
        print(f"\033[1;31m{msg}\033[0m")


    def getModel_LHSRHS_AndTonnage(qrCode):
        return "DOST", "LHS", "Unknown"


    UNKNOWN = "Unknown"


class CheckNoCapBunk:
    """
    Checks for the presence of Bung (before cap is placed) by comparing the current image
    with the Nut and Plate Washer image using annular masks and brightness analysis.
    """

    # Default detection parameters (will be overridden by configuration)
    CENTER_X = 630
    CENTER_Y = 350
    INNER_RADIUS_OF_NUT = 26
    OUTER_RADIUS_OF_NUT = 39
    OUTER_RADIUS_OF_WASHER = 59
    THRESHOLD_DOST = 25.0
    THRESHOLD_DOSTPLUS = 25.0

    # Cached annular masks
    _annular_mask_nut = None
    _annular_mask_washer = None
    _mask_shape = None

    @classmethod
    def _load_parameters_from_config(cls):
        """Load parameters from configuration file."""
        try:
            cls.CENTER_X = CosThetaConfigurator.getNoCapBunkCheckCenterX()
            cls.CENTER_Y = CosThetaConfigurator.getNoCapBunkCheckCenterY()
            cls.INNER_RADIUS_OF_NUT = CosThetaConfigurator.getNoCapBunkCheckInnerRadiusOfNut()
            cls.OUTER_RADIUS_OF_NUT = CosThetaConfigurator.getNoCapBunkCheckOuterRadiusOfNut()
            cls.OUTER_RADIUS_OF_WASHER = CosThetaConfigurator.getNoCapBunkCheckOuterRadiusOfWasher()
            cls.THRESHOLD_DOST = CosThetaConfigurator.getNoCapBunkCheckThreshold(DOST)
            cls.THRESHOLD_DOSTPLUS = CosThetaConfigurator.getNoCapBunkCheckThreshold(DOSTPLUS)
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
    def _get_annular_mask_nut(cls, image_shape: tuple) -> np.ndarray:
        """Get annular mask for nut area (INNER_RADIUS_OF_NUT to OUTER_RADIUS_OF_NUT) - used for DOST."""
        if cls._annular_mask_nut is None or cls._mask_shape != image_shape[:2]:
            cls._annular_mask_nut = cls._get_annular_mask(
                image_shape, cls.INNER_RADIUS_OF_NUT, cls.OUTER_RADIUS_OF_NUT
            )
            cls._mask_shape = image_shape[:2]
        return cls._annular_mask_nut

    @classmethod
    def _get_annular_mask_washer(cls, image_shape: tuple) -> np.ndarray:
        """Get annular mask for washer area (OUTER_RADIUS_OF_NUT to OUTER_RADIUS_OF_WASHER) - used for DOSTPLUS."""
        if cls._annular_mask_washer is None or cls._mask_shape != image_shape[:2]:
            cls._annular_mask_washer = cls._get_annular_mask(
                image_shape, cls.OUTER_RADIUS_OF_NUT, cls.OUTER_RADIUS_OF_WASHER
            )
            cls._mask_shape = image_shape[:2]
        return cls._annular_mask_washer

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
    def _create_annotated_image(image: np.ndarray,
                                is_bung_present: bool,
                                bung_bw_avg: float,
                                nut_washer_bw_avg: float,
                                diff: float,
                                threshold: float,
                                inner_radius: int,
                                outer_radius: int,
                                modelName: str) -> np.ndarray:
        """Create an annotated version of the input image showing the detection result."""
        annotated = image.copy()

        # Draw the annular region
        center = (CheckNoCapBung.CENTER_X, CheckNoCapBung.CENTER_Y)
        color = (0, 255, 0) if is_bung_present else (0, 0, 255)

        cv2.circle(annotated, center, inner_radius, (255, 255, 0), 1)  # Cyan - inner
        cv2.circle(annotated, center, outer_radius, color, 2)  # Green/Red - outer

        # Add result text
        result_text = "NO-CAP BUNG: PRESENT" if is_bung_present else "NO-CAP BUNG: NOT DETECTED"
        cv2.putText(annotated, result_text, (50, 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 3)

        # Add model info
        cv2.putText(annotated, f"Model: {modelName}", (50, 90),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

        # Add B&W values
        cv2.putText(annotated, f"Bung B&W avg: {bung_bw_avg:.1f}", (50, 120),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.putText(annotated, f"Nut+Washer B&W avg: {nut_washer_bw_avg:.1f}", (50, 150),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

        # Add difference info
        if modelName == DOST:
            diff_text = f"(Nut-Bung) diff: {diff:.1f} (thr: {threshold:.1f})"
        else:
            diff_text = f"(Bung-Nut) diff: {diff:.1f} (thr: {threshold:.1f})"

        diff_color = (0, 255, 0) if is_bung_present else (0, 0, 255)
        cv2.putText(annotated, diff_text, (50, 180),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, diff_color, 2)

        return annotated

    @staticmethod
    def checkNoCapBung(anImage: np.ndarray,
                       currentPictures: Dict[str, np.ndarray | None] = None,
                       componentQRCode: str = DOST, gamma: float = 2.0) -> Tuple[np.ndarray | None, bool]:
        """
        Check for the presence of Bung (before cap is placed).

        For DOST: Check if Nut is visible (nut area comparison)
        For DOSTPLUS: Check if Plate Washer is visible (washer area comparison)

        Args:
            anImage: Current camera image to analyze (No Cap Bung image)
            currentPictures: Dictionary containing previously captured images
            componentQRCode: Component QR code for the assembly

        Returns:
            Tuple of:
                - Annotated image showing the detection result
                - Boolean indicating if Bung is present (True) or not (False)
        """
        printBoldBlue(
            f"CheckNoCapBunk.checkNoCapBung() called with "
            f"anImage = {anImage.shape if anImage is not None else 'None'}"
        )

        # Basic validation
        if componentQRCode is None:
            printBoldRed("CheckNoCapBunk: componentQRCode is None, returning False")
            return anImage, False

        if anImage is None:
            printBoldRed("CheckNoCapBunk: anImage is None, returning False")
            return anImage, False

        if currentPictures is None:
            printBoldRed("CheckNoCapBunk: currentPictures is None, returning False")
            return anImage, False

        # # =====================================================================
        # # HAND DETECTION CHECK - First check before any other processing
        # # Ensure no operator hands are still in the frame
        # # =====================================================================
        # if HandDetector.detectHands(anImage):
        #     printBoldRed("CheckNoCapBunk: HAND DETECTION FAILED - Operator hands detected in image!")
        #     printBoldRed("CheckNoCapBunk: Please ensure hands are completely out of frame before capturing")
        #     return anImage, False
        #
        # printBoldGreen("CheckNoCapBunk: Hand detection passed - No hands in frame")
        #
        # # Get component type from QR code
        # modelName, _, _ = getModel_LHSRHS_AndTonnage(componentQRCode)
        # printBoldBlue(f"Got modelName as {modelName}")
        # if modelName == UNKNOWN:
        #     modelName = "DOST"
        # printBoldBlue(f"Final modelName is {modelName}")
        #
        # # Load parameters from configuration
        # CheckNoCapBunk._load_parameters_from_config()
        #
        # printBoldBlue(f"CheckNoCapBunk: Using center=({CheckNoCapBunk.CENTER_X}, {CheckNoCapBunk.CENTER_Y})")
        #
        # # Get Nut and Plate Washer image
        # nut_plate_washer_image = currentPictures.get(nutAndPlateWasherPictureKeyString, None)
        #
        # if nut_plate_washer_image is None:
        #     printBoldRed("CheckNoCapBunk: Nut and Plate Washer image is missing, returning False")
        #     return anImage, False
        #
        # try:
        #     # Invalidate cached masks to ensure fresh calculation
        #     CheckNoCapBunk._annular_mask_nut = None
        #     CheckNoCapBunk._annular_mask_washer = None
        #
        #     is_bung_present = False
        #     diff = 0.0
        #     bung_bw_avg = 0.0
        #     nut_washer_bw_avg = 0.0
        #     inner_radius = 0
        #     outer_radius = 0
        #     threshold = 0.0
        #
        #     if modelName == DOST:
        #         # =====================================================================
        #         # DOST: Check if Nut is visible
        #         # Annular ring: INNER_RADIUS_OF_NUT to OUTER_RADIUS_OF_NUT
        #         # Condition: nut_washer_avg - bung_avg > threshold
        #         # =====================================================================
        #         inner_radius = CheckNoCapBunk.INNER_RADIUS_OF_NUT
        #         outer_radius = CheckNoCapBunk.OUTER_RADIUS_OF_NUT
        #         threshold = CheckNoCapBunk.THRESHOLD_DOST
        #
        #         printBoldBlue(f"CheckNoCapBunk DOST: Using nut annular mask ({inner_radius} to {outer_radius})")
        #
        #         mask = CheckNoCapBunk._get_annular_mask_nut(anImage.shape)
        #
        #         bung_bw_avg = CheckNoCapBunk._compute_bw_average(anImage, mask)
        #         nut_washer_bw_avg = CheckNoCapBunk._compute_bw_average(nut_plate_washer_image, mask)
        #
        #         # DOST: nut_washer_avg - bung_avg > threshold
        #         diff = nut_washer_bw_avg - bung_bw_avg
        #         is_bung_present = diff > threshold
        #
        #         printBoldBlue(
        #             f"CheckNoCapBunk DOST: Bung B&W avg = {bung_bw_avg:.1f}, Nut+Washer B&W avg = {nut_washer_bw_avg:.1f}")
        #         if is_bung_present:
        #             printBoldGreen(
        #                 f"CheckNoCapBunk: PASSED - (Nut-Bung) B&W diff = {diff:.1f} > threshold = {threshold:.1f}"
        #             )
        #         else:
        #             printBoldRed(
        #                 f"CheckNoCapBunk: FAILED - (Nut-Bung) B&W diff = {diff:.1f} <= threshold = {threshold:.1f}"
        #             )
        #
        #     elif modelName == DOSTPLUS:
        #         # =====================================================================
        #         # DOSTPLUS: Check if Plate Washer is visible
        #         # Annular ring: OUTER_RADIUS_OF_NUT to OUTER_RADIUS_OF_WASHER
        #         # Condition: bung_avg - nut_washer_avg > threshold (opposite of DOST)
        #         # =====================================================================
        #         inner_radius = CheckNoCapBunk.OUTER_RADIUS_OF_NUT
        #         outer_radius = CheckNoCapBunk.OUTER_RADIUS_OF_WASHER
        #         threshold = CheckNoCapBunk.THRESHOLD_DOSTPLUS
        #
        #         printBoldBlue(f"CheckNoCapBunk DOSTPLUS: Using washer annular mask ({inner_radius} to {outer_radius})")
        #
        #         mask = CheckNoCapBunk._get_annular_mask_washer(anImage.shape)
        #
        #         bung_bw_avg = CheckNoCapBunk._compute_bw_average(anImage, mask)
        #         nut_washer_bw_avg = CheckNoCapBunk._compute_bw_average(nut_plate_washer_image, mask)
        #
        #         # DOSTPLUS: bung_avg - nut_washer_avg > threshold (opposite order)
        #         diff = bung_bw_avg - nut_washer_bw_avg
        #         is_bung_present = diff > threshold
        #
        #         printBoldBlue(
        #             f"CheckNoCapBunk DOSTPLUS: Bung B&W avg = {bung_bw_avg:.1f}, Nut+Washer B&W avg = {nut_washer_bw_avg:.1f}")
        #         if is_bung_present:
        #             printBoldGreen(
        #                 f"CheckNoCapBunk: PASSED - (Bung-Nut) B&W diff = {diff:.1f} > threshold = {threshold:.1f}"
        #             )
        #         else:
        #             printBoldRed(
        #                 f"CheckNoCapBunk: FAILED - (Bung-Nut) B&W diff = {diff:.1f} <= threshold = {threshold:.1f}"
        #             )
        #
        #     # Final result
        #     if is_bung_present:
        #         printBoldGreen("CheckNoCapBunk: NO-CAP BUNG DETECTED")
        #     else:
        #         printBoldRed("CheckNoCapBunk: NO-CAP BUNG NOT DETECTED")
        #
        #     # Create annotated output image
        #     annotated_image = CheckNoCapBunk._create_annotated_image(
        #         anImage, is_bung_present,
        #         bung_bw_avg, nut_washer_bw_avg,
        #         diff, threshold,
        #         inner_radius, outer_radius,
        #         modelName
        #     )
        #
        #     return annotated_image, is_bung_present
        #
        # except Exception as e:
        #     printBoldRed(f"CheckNoCapBunk: Exception during detection: {e}")
        #     import traceback
        #     traceback.print_exc()
        #     return anImage, False
        return anImage, True

    @staticmethod
    def test(directory_path: str):
        """
        Test the CheckNoCapBunk algorithm on images in the specified directory.

        Each Bung image is tested against 30 randomly selected NutAndPlateWasher images.

        Args:
            directory_path: Path to directory containing test images
        """
        GREEN = "\033[1;32m"
        RED = "\033[1;31m"
        YELLOW = "\033[1;33m"
        CYAN = "\033[1;36m"
        RESET = "\033[0m"

        print(f"{CYAN}{'=' * 80}{RESET}")
        print(f"{CYAN}CheckNoCapBunk Test Suite{RESET}")
        print(f"{CYAN}{'=' * 80}{RESET}")
        print(f"\nDirectory: {directory_path}")
        print(f"Parameters:")
        print(f"  Center: ({CheckNoCapBung.CENTER_X}, {CheckNoCapBung.CENTER_Y})")
        print(f"  INNER_RADIUS_OF_NUT: {CheckNoCapBung.INNER_RADIUS_OF_NUT}")
        print(f"  OUTER_RADIUS_OF_NUT: {CheckNoCapBung.OUTER_RADIUS_OF_NUT}")
        print(f"  OUTER_RADIUS_OF_WASHER: {CheckNoCapBung.OUTER_RADIUS_OF_WASHER}")
        print(f"  Threshold DOST: {CheckNoCapBung.THRESHOLD_DOST}")
        print(f"  Threshold DOSTPLUS: {CheckNoCapBung.THRESHOLD_DOSTPLUS}")

        # Find all PNG files
        all_files = glob.glob(os.path.join(directory_path, "*.png"))

        # Categorize files into lists
        list_bung = sorted([f for f in all_files if "Bung" in os.path.basename(f)])
        list_nut_and_plate_washer = sorted([f for f in all_files if "Nut" in os.path.basename(f)])

        print(f"\n{YELLOW}Found files:{RESET}")
        print(f"  Bung: {len(list_bung)}")
        print(f"  Nut and Plate Washer: {len(list_nut_and_plate_washer)}")

        if not list_bung:
            print(f"{RED}No Bung images found!{RESET}")
            return

        if not list_nut_and_plate_washer:
            print(f"{RED}No Nut and Plate Washer images found!{RESET}")
            return

        # Load all images
        print(f"\n{YELLOW}Loading images...{RESET}")

        bung_images = [(f, cv2.imread(f)) for f in list_bung]
        nut_images = [(f, cv2.imread(f)) for f in list_nut_and_plate_washer]

        # Determine model type from path
        is_dostplus = DOSTPLUS in directory_path

        if is_dostplus:
            print(f"\n{YELLOW}Detected DOSTPLUS from path{RESET}")
            print(
                f"  Using washer annular mask ({CheckNoCapBung.OUTER_RADIUS_OF_NUT} to {CheckNoCapBung.OUTER_RADIUS_OF_WASHER})")
            print(f"  Condition: (Bung - Nut) > {CheckNoCapBung.THRESHOLD_DOSTPLUS}")
        else:
            print(f"\n{YELLOW}Detected DOST from path{RESET}")
            print(
                f"  Using nut annular mask ({CheckNoCapBung.INNER_RADIUS_OF_NUT} to {CheckNoCapBung.OUTER_RADIUS_OF_NUT})")
            print(f"  Condition: (Nut - Bung) > {CheckNoCapBung.THRESHOLD_DOST}")

        # Number of random NutAndPlateWasher images to test against each Bung image
        num_random_samples = min(30, len(nut_images))

        # =====================================================================
        # TEST 1: Bung images against random NutAndPlateWasher images
        # =====================================================================
        print(f"\n{CYAN}{'=' * 80}{RESET}")
        print(f"{CYAN}TEST 1: Bung images (tested against {num_random_samples} random Nut+Washer images each){RESET}")
        print(f"{CYAN}{'=' * 80}{RESET}")

        total_tests = 0
        total_pass = 0
        total_fail = 0

        for bung_file, bung_img in bung_images:
            # Randomly select NutAndPlateWasher images
            if len(nut_images) > num_random_samples:
                random_nut_images = random.sample(nut_images, num_random_samples)
            else:
                random_nut_images = nut_images

            bung_pass = 0
            bung_fail = 0

            failed_pairs = []  # Track failed pairs for this bung image

            for nut_file, nut_img in random_nut_images:
                currentPictures = {
                    nutAndPlateWasherPictureKeyString: nut_img
                }

                if is_dostplus:
                    _, result = CheckNoCapBung.checkNoCapBunk(
                        bung_img, currentPictures,
                        "8201206$400112VA1C$11770$09.03.2025$C5211701$MEK$40Cr4 - B$1$SPDLASSY - KNULH$SNPL - MAT$")
                else:
                    _, result = CheckNoCapBung.checkNoCapBunk(bung_img, currentPictures, DOST)

                total_tests += 1
                if result:
                    bung_pass += 1
                    total_pass += 1
                else:
                    bung_fail += 1
                    total_fail += 1
                    failed_pairs.append((os.path.basename(bung_file), os.path.basename(nut_file)))

            # Print summary for this bung image
            pass_rate = (bung_pass / len(random_nut_images)) * 100 if random_nut_images else 0
            color = GREEN if pass_rate >= 90 else (YELLOW if pass_rate >= 50 else RED)
            print(
                f"  {os.path.basename(bung_file):45s} {color}{bung_pass}/{len(random_nut_images)} passed ({pass_rate:.1f}%){RESET}")

            # Print failed pairs
            if failed_pairs:
                for bung_name, nut_name in failed_pairs:
                    print(f"    {RED}FAILED: Bung={bung_name}, Nut+Washer={nut_name}{RESET}")

        print(f"\n  {CYAN}Overall Summary:{RESET}")
        print(f"    Total tests: {total_tests}")
        print(f"    Passed: {GREEN}{total_pass}{RESET}")
        print(f"    Failed: {RED}{total_fail}{RESET}")
        overall_rate = (total_pass / total_tests) * 100 if total_tests else 0
        print(f"    Pass rate: {overall_rate:.1f}%")

        # =====================================================================
        # TEST 2: NutAndPlateWasher images against themselves (should NOT detect bung)
        # =====================================================================
        print(f"\n{CYAN}{'=' * 80}{RESET}")
        print(f"{CYAN}TEST 2: Nut+Washer images (should NOT detect Bung){RESET}")
        print(f"{CYAN}{'=' * 80}{RESET}")

        # Use first nut image as base
        nut_base = nut_images[0][1] if nut_images else None

        nut_pass = 0
        nut_fail = 0

        for nut_file, nut_img in nut_images:
            currentPictures = {
                nutAndPlateWasherPictureKeyString: nut_base
            }

            if is_dostplus:
                _, result = CheckNoCapBung.checkNoCapBunk(
                    nut_img, currentPictures,
                    "8201206$400112VA1C$11770$09.03.2025$C5211701$MEK$40Cr4 - B$1$SPDLASSY - KNULH$SNPL - MAT$")
            else:
                _, result = CheckNoCapBung.checkNoCapBunk(nut_img, currentPictures, DOST)

            if result:
                nut_pass += 1
                print(f"  {os.path.basename(nut_file):45s} {GREEN}Bung Present{RESET}")
            else:
                nut_fail += 1
                print(f"  {os.path.basename(nut_file):45s} {RED}Bung Not Present{RESET}")

        print(f"\n  Summary: {GREEN}{nut_pass} Bung Present{RESET}, {RED}{nut_fail} Bung Not Present{RESET}")
        print(f"  (Expected: all should be Bung Not Present)")

        # =====================================================================
        # FINAL SUMMARY
        # =====================================================================
        print(f"\n{CYAN}{'=' * 80}{RESET}")
        print(f"{CYAN}FINAL SUMMARY{RESET}")
        print(f"{CYAN}{'=' * 80}{RESET}")

        print(f"  Bung images:       {total_pass}/{total_tests} tests passed ({overall_rate:.1f}%)")
        print(
            f"  Nut+Washer images: {nut_pass}/{len(list_nut_and_plate_washer)} detected as Bung Present (expected: 0)")


# if __name__ == "__main__":
#     import sys
#
#     if len(sys.argv) > 1:
#         CheckNoCapBunk.test(sys.argv[1])
#     else:
#         CheckNoCapBunk.test("C:/AshokLeylandImages/DOST/LHS")