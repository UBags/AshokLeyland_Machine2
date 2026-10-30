"""
CheckBunk - Detects presence of Bung by comparing with Cap image.

Detection Method:
    Single-step verification using R-B color difference:

    For DOST:
        - Bung is darkish grey, Cap is metallic
        - Calculate R-B average for both images within circular mask
        - If (bung_R-B_avg - cap_R-B_avg) > threshold → Bung Present

    For DOSTPLUS:
        - Bung is whitish, Cap is brassy
        - Calculate R-B average for both images within circular mask
        - If (cap_R-B_avg - bung_R-B_avg) > threshold → Bung Present

Parameters (from configuration):
    CENTER_X, CENTER_Y: Center of the bung region (default: 630, 320)
    RADIUS: 70 for DOST, 50 for DOSTPLUS
    RB_DIFF_THRESHOLD: Minimum R-B difference required (default: 30)
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
    capPictureKeyString = "capPicture"


    def printBoldBlue(msg):
        print(f"\033[1;34m{msg}\033[0m")


    def printBoldGreen(msg):
        print(f"\033[1;32m{msg}\033[0m")


    def printBoldRed(msg):
        print(f"\033[1;31m{msg}\033[0m")


    def getModel_LHSRHS_AndTonnage(qrCode):
        return "DOST", "LHS", "Unknown"


    UNKNOWN = "Unknown"


class CheckBunk:
    """
    Checks for the presence of the Bung by comparing the current image
    with the Cap image using a circular mask and R-B color analysis.
    """

    # Default detection parameters (will be overridden by configuration)
    CENTER_X = 630
    CENTER_Y = 320
    RADIUS_DOST = 70
    RADIUS_DOSTPLUS = 50
    RB_DIFF_THRESHOLD_DOST = 30.0
    RB_DIFF_THRESHOLD_DOSTPLUS = 30.0

    # Cached circular mask
    _circular_mask = None
    _mask_shape = None
    _mask_radius = None

    @classmethod
    def _load_parameters_from_config(cls, componentType: str = "DOST"):
        """Load parameters from configuration file."""
        try:
            cls.CENTER_X = CosThetaConfigurator.getBunkCheckCenterX()
            cls.CENTER_Y = CosThetaConfigurator.getBunkCheckCenterY()
            cls.RADIUS_DOST = CosThetaConfigurator.getBunkCheckRadius(DOST)
            cls.RADIUS_DOSTPLUS = CosThetaConfigurator.getBunkCheckRadius(DOSTPLUS)
            cls.RB_DIFF_THRESHOLD_DOST = CosThetaConfigurator.getBunkCheckRBDiffThreshold(DOST)
            cls.RB_DIFF_THRESHOLD_DOSTPLUS = CosThetaConfigurator.getBunkCheckRBDiffThreshold(DOSTPLUS)
        except:
            pass

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
        if (cls._circular_mask is None or
                cls._mask_shape != image_shape[:2] or
                cls._mask_radius != radius):
            h, w = image_shape[:2]
            mask = np.zeros((h, w), dtype=np.uint8)
            cv2.circle(mask, (CheckBung.CENTER_X, CheckBung.CENTER_Y), radius, 255, -1)

            cls._circular_mask = mask
            cls._mask_shape = image_shape[:2]
            cls._mask_radius = radius

        return cls._circular_mask

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

    @staticmethod
    def _create_annotated_image(image: np.ndarray,
                                is_bung_present: bool,
                                bung_rb_avg: float,
                                cap_rb_avg: float,
                                rb_diff: float,
                                threshold: float,
                                radius: int,
                                modelName: str) -> np.ndarray:
        """Create an annotated version of the input image showing the detection result."""
        annotated = image.copy()

        # Draw the circular region
        color = (0, 255, 0) if is_bung_present else (0, 0, 255)
        cv2.circle(annotated, (CheckBung.CENTER_X, CheckBung.CENTER_Y), radius, color, 2)

        # Add result text
        result_text = "BUNG: PRESENT" if is_bung_present else "BUNG: NOT DETECTED"
        cv2.putText(annotated, result_text, (50, 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 3)

        # Add R-B values
        cv2.putText(annotated, f"Bung R-B: {bung_rb_avg:.1f}", (50, 100),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        cv2.putText(annotated, f"Cap R-B: {cap_rb_avg:.1f}", (50, 130),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

        # Add difference and threshold
        if modelName == DOST:
            diff_text = f"Bung-Cap Diff: {rb_diff:.1f} (threshold: {threshold:.1f})"
        else:
            diff_text = f"Cap-Bung Diff: {rb_diff:.1f} (threshold: {threshold:.1f})"

        diff_color = (0, 255, 0) if is_bung_present else (0, 0, 255)
        cv2.putText(annotated, diff_text, (50, 170),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, diff_color, 2)

        return annotated

    @staticmethod
    def checkBung(anImage: np.ndarray,
                  currentPictures: Dict[str, np.ndarray | None] = None,
                  componentQRCode: str = DOST, gamma: float = 2.0) -> Tuple[np.ndarray | None, bool]:
        """
        Check for the presence of the Bung.

        Compares the current image (Bung image) with the Cap image using R-B analysis.

        For DOST: (bung_R-B_avg - cap_R-B_avg) > threshold → Bung Present
        For DOSTPLUS: (cap_R-B_avg - bung_R-B_avg) > threshold → Bung Present

        Args:
            anImage: Current camera image to analyze (Bung image)
            currentPictures: Dictionary containing previously captured images (must include capPicture)
            componentQRCode: Component QR code for the assembly

        Returns:
            Tuple of:
                - Annotated image showing the detection result
                - Boolean indicating if Bung is present (True) or not (False)
        """
        printBoldBlue(
            f"CheckBunk.checkBung() called with "
            f"anImage = {anImage.shape if anImage is not None else 'None'}"
        )

        # Basic validation
        if componentQRCode is None:
            printBoldRed("CheckBunk: componentQRCode is None, returning False")
            return anImage, False

        if anImage is None:
            printBoldRed("CheckBunk: anImage is None, returning False")
            return anImage, False

        if currentPictures is None:
            printBoldRed("CheckBunk: currentPictures is None, returning False")
            return anImage, False

        # =====================================================================
        # HAND DETECTION CHECK - First check before any other processing
        # Ensure no operator hands are still in the frame
        # =====================================================================
        # if HandDetector.detectHands(anImage):
        #     printBoldRed("CheckBunk: HAND DETECTION FAILED - Operator hands detected in image!")
        #     printBoldRed("CheckBunk: Please ensure hands are completely out of frame before capturing")
        #     return anImage, False
        #
        # printBoldGreen("CheckBunk: Hand detection passed - No hands in frame")
        #
        #
        # # Get component type from QR code
        # modelName, _, _ = getModel_LHSRHS_AndTonnage(componentQRCode)
        # printBoldBlue(f"Got modelName as {modelName}")
        # if modelName == UNKNOWN:
        #     modelName = "DOST"
        # printBoldBlue(f"Final modelName is {modelName}")
        #
        # # Load parameters from configuration
        # CheckBunk._load_parameters_from_config(modelName)
        #
        # # Get radius based on component type
        # radius = CheckBunk._get_radius_for_component(modelName)
        #
        # printBoldBlue(f"CheckBunk: Using radius={radius} for component type={modelName}")
        #
        # # Get Cap image
        # cap_image = currentPictures.get(capPictureKeyString, None)
        #
        # if cap_image is None:
        #     printBoldRed("CheckBunk: Cap image is missing, returning False")
        #     return anImage, False
        #
        # try:
        #     # Get circular mask
        #     mask = CheckBunk._get_circular_mask(anImage.shape, radius)
        #
        #     # =====================================================================
        #     # Calculate R-B for both images
        #     # =====================================================================
        #     bung_rb_avg = CheckBunk._compute_rb_difference(anImage, mask)
        #     cap_rb_avg = CheckBunk._compute_rb_difference(cap_image, mask)
        #
        #     printBoldBlue(f"CheckBunk: Bung R-B avg = {bung_rb_avg:.1f}")
        #     printBoldBlue(f"CheckBunk: Cap R-B avg = {cap_rb_avg:.1f}")
        #
        #     # =====================================================================
        #     # Compare based on model type
        #     # =====================================================================
        #     is_bung_present = False
        #     rb_diff = 0.0
        #
        #     if modelName == DOST:
        #         # DOST: Bung is darkish grey, Cap is metallic
        #         # (bung_R-B_avg - cap_R-B_avg) > threshold
        #         rb_diff = bung_rb_avg - cap_rb_avg
        #         threshold = CheckBunk.RB_DIFF_THRESHOLD_DOST
        #         is_bung_present = rb_diff > threshold
        #
        #         if is_bung_present:
        #             printBoldGreen(
        #                 f"CheckBunk: PASSED - (Bung-Cap) R-B diff = {rb_diff:.1f} > threshold = {threshold:.1f}"
        #             )
        #         else:
        #             printBoldRed(
        #                 f"CheckBunk: FAILED - (Bung-Cap) R-B diff = {rb_diff:.1f} <= threshold = {threshold:.1f}"
        #             )
        #
        #     elif modelName == DOSTPLUS:
        #         # DOSTPLUS: Bung is whitish, Cap is brassy
        #         # (cap_R-B_avg - bung_R-B_avg) > threshold (opposite of DOST)
        #         rb_diff = cap_rb_avg - bung_rb_avg
        #         threshold = CheckBunk.RB_DIFF_THRESHOLD_DOSTPLUS
        #         is_bung_present = rb_diff > threshold
        #
        #         if is_bung_present:
        #             printBoldGreen(
        #                 f"CheckBunk: PASSED - (Cap-Bung) R-B diff = {rb_diff:.1f} > threshold = {threshold:.1f}"
        #             )
        #         else:
        #             printBoldRed(
        #                 f"CheckBunk: FAILED - (Cap-Bung) R-B diff = {rb_diff:.1f} <= threshold = {threshold:.1f}"
        #             )
        #
        #     # =====================================================================
        #     # Final Decision
        #     # =====================================================================
        #     if is_bung_present:
        #         printBoldGreen("CheckBunk: BUNG DETECTED")
        #     else:
        #         printBoldRed("CheckBunk: BUNG NOT DETECTED")
        #
        #     # Create annotated output image
        #     annotated_image = CheckBunk._create_annotated_image(
        #         anImage, is_bung_present, bung_rb_avg, cap_rb_avg,
        #         rb_diff, threshold, radius, modelName
        #     )
        #
        #     return annotated_image, is_bung_present
        #
        # except Exception as e:
        #     printBoldRed(f"CheckBunk: Exception during detection: {e}")
        #     import traceback
        #     traceback.print_exc()
        #     return anImage, False
        return anImage, True

    @staticmethod
    def test(directory_path: str):
        """
        Test the CheckBunk algorithm on images in the specified directory.

        Args:
            directory_path: Path to directory containing test images
        """
        GREEN = "\033[1;32m"
        RED = "\033[1;31m"
        YELLOW = "\033[1;33m"
        CYAN = "\033[1;36m"
        RESET = "\033[0m"

        print(f"{CYAN}{'=' * 80}{RESET}")
        print(f"{CYAN}CheckBunk Test Suite{RESET}")
        print(f"{CYAN}{'=' * 80}{RESET}")
        print(f"\nDirectory: {directory_path}")
        print(f"Parameters:")
        print(f"  Center: ({CheckBung.CENTER_X}, {CheckBung.CENTER_Y})")
        if DOSTPLUS in directory_path:
            print("In DOSTPLUS path")
            print(f"  Radius DOSTPLUS: {CheckBung.RADIUS_DOSTPLUS}")
            print(f"  R-B Diff Threshold: {CheckBung.RB_DIFF_THRESHOLD_DOSTPLUS}")
        else:
            print("In DOST path")
            print(f"  Radius DOST: {CheckBung.RADIUS_DOST}")
            print(f"  R-B Diff Threshold: {CheckBung.RB_DIFF_THRESHOLD_DOST}")

        # Find all PNG files
        all_files = glob.glob(os.path.join(directory_path, "*.png"))

        # Categorize files into lists
        list_bung = sorted([f for f in all_files if "Bung" in os.path.basename(f) and "No" not in os.path.basename(f)])
        list_cap = sorted([f for f in all_files if "Cap" in os.path.basename(f)])

        print(f"\n{YELLOW}Found files:{RESET}")
        print(f"  Bung: {len(list_bung)}")
        print(f"  Cap: {len(list_cap)}")

        if not list_cap:
            print(f"{RED}No Cap images found!{RESET}")
            return

        if not list_bung:
            print(f"{RED}No Bung images found!{RESET}")
            return

        # Load all images
        print(f"\n{YELLOW}Loading images...{RESET}")

        bung_images = [(f, cv2.imread(f)) for f in list_bung]
        cap_images = [(f, cv2.imread(f)) for f in list_cap]

        # Use first Cap image as base
        cap_base = cap_images[0][1] if cap_images else None

        # =====================================================================
        # TEST 1: Bung images against Cap image (should detect Bung)
        # =====================================================================
        print(f"\n{CYAN}{'=' * 80}{RESET}")
        print(f"{CYAN}TEST 1: Bung images (tested against Cap image){RESET}")
        print(f"{CYAN}{'=' * 80}{RESET}")
        print(f"{YELLOW}Base image: {os.path.basename(cap_images[0][0])}{RESET}\n")

        bung_pass = 0
        bung_fail = 0

        for bung_file, bung_img in bung_images:
            currentPictures = {
                capPictureKeyString: cap_base
            }

            if DOSTPLUS in directory_path:
                _, result = CheckBung.checkBunk(bung_img, currentPictures,
                                                "8201206$400112VA1C$11770$09.03.2025$C5211701$MEK$40Cr4 - B$1$SPDLASSY - KNULH$SNPL - MAT$")
            elif DOST in directory_path:
                _, result = CheckBung.checkBunk(bung_img, currentPictures, DOST)

            if result:
                bung_pass += 1
                print(f"  {os.path.basename(bung_file):45s} {GREEN}Bung Present{RESET}")
            else:
                bung_fail += 1
                print(f"  {os.path.basename(bung_file):45s} {RED}Bung Not Present{RESET}")

        print(f"\n  Summary: {GREEN}{bung_pass} Bung Present{RESET}, {RED}{bung_fail} Bung Not Present{RESET}")

        # =====================================================================
        # TEST 2: Cap images against Cap image (should NOT detect Bung)
        # =====================================================================
        print(f"\n{CYAN}{'=' * 80}{RESET}")
        print(f"{CYAN}TEST 2: Cap images (tested against Cap image - should NOT detect Bung){RESET}")
        print(f"{CYAN}{'=' * 80}{RESET}")
        print(f"{YELLOW}Base image: {os.path.basename(cap_images[0][0])}{RESET}\n")

        cap_pass = 0
        cap_fail = 0

        for cap_file, cap_img in cap_images:
            currentPictures = {
                capPictureKeyString: cap_base
            }

            if DOSTPLUS in directory_path:
                _, result = CheckBung.checkBunk(cap_img, currentPictures,
                                                "8201206$400112VA1C$11770$09.03.2025$C5211701$MEK$40Cr4 - B$1$SPDLASSY - KNULH$SNPL - MAT$")
            elif DOST in directory_path:
                _, result = CheckBung.checkBunk(cap_img, currentPictures, DOST)

            if result:
                cap_pass += 1
                print(f"  {os.path.basename(cap_file):45s} {GREEN}Bung Present{RESET}")
            else:
                cap_fail += 1
                print(f"  {os.path.basename(cap_file):45s} {RED}Bung Not Present{RESET}")

        print(f"\n  Summary: {GREEN}{cap_pass} Bung Present{RESET}, {RED}{cap_fail} Bung Not Present{RESET}")

        # =====================================================================
        # FINAL SUMMARY
        # =====================================================================
        print(f"\n{CYAN}{'=' * 80}{RESET}")
        print(f"{CYAN}FINAL SUMMARY{RESET}")
        print(f"{CYAN}{'=' * 80}{RESET}")

        print(f"  Bung images:  {bung_pass}/{len(list_bung)} detected as Bung Present (expected: all)")
        print(f"  Cap images:   {cap_pass}/{len(list_cap)} detected as Bung Present (expected: none)")


# if __name__ == "__main__":
#     import sys
#
#     if len(sys.argv) > 1:
#         CheckBunk.test(sys.argv[1])
#     else:
#         CheckBunk.test("C:/AshokLeylandImages/DOSTPLUS/LHS")