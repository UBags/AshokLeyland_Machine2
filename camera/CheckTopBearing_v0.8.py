"""
CheckTopBearing - Detects presence of Top Bearing by comparing with Hub+Bottom Bearing image.

Detection Method:
    Compares the average brightness in an annular (ring) region between the current 
    image and the stored Hub+Bottom Bearing image.

    The Top Bearing has brighter metallic rings in this region compared to the
    darker grease-covered area in the Hub+Bottom Bearing.

Parameters (calibrated from test data):
    CENTER_X, CENTER_Y: 635, 345 - Center of the bearing in the camera image
    INNER_RADIUS: 20 - Inner radius of the annular mask
    OUTER_RADIUS: 60 - Outer radius of the annular mask
    GAMMA: 3.0 - Gamma correction value (applied via pre-calculated LUT)
    THRESHOLD: 20.0 - Difference above this indicates bearing present

Test Results:
    Same category differences: -7.75 to +7.75 (mean ~0)
    Different category differences: +32.33 to +47.81 (mean ~40)
    Gap: 24.58 points
    Accuracy: 100% on test set with threshold 20
"""

import copy
from threading import Thread
from queue import *
import cv2
import numpy as np
from typing import Dict, Tuple
# from HandDetector import HandDetector

import warnings

from utils.QRCodeHelper import getModel_LHSRHS_AndTonnage

warnings.filterwarnings('ignore', '.*h264.*', )

# Try to import production modules, fall back to mocks for standalone testing
try:
    import logutils.SlaveLoggers
    from utils.CosThetaFileUtils import *
    from utils.RedisUtils import *
    from BaseUtils import *
    from logutils import SlaveLoggers
    from logutils.SlaveLoggers import *
    from concurrent.futures import ThreadPoolExecutor, wait
    from Configuration import *
    CosThetaConfigurator.getInstance()
except ImportError:
    # Standalone testing mode - define mocks
    DOST = "DOST"
    hubAndBottomBearingPictureKeyString = "hubAndBottomBearingPicture"

    def printBoldBlue(msg):
        print(f"\033[1;34m{msg}\033[0m")

    def printBoldGreen(msg):
        print(f"\033[1;32m{msg}\033[0m")

    def printBoldRed(msg):
        print(f"\033[1;31m{msg}\033[0m")


class CheckTopBearing:
    """
    Checks for the presence of the Top Bearing by comparing the current image
    with the previously captured Hub+Bottom Bearing image using an annular mask.
    """

    # Detection parameters - calibrated from test images
    CENTER_X = 635
    CENTER_Y = 350
    INNER_RADIUS = 20
    OUTER_RADIUS = 60
    GAMMA = 3.0
    THRESHOLD = 16.0  # Difference above this = bearing present

    # Pre-calculated gamma LUT (calculated once at class load time)
    _gamma_lut = None

    # Cached annular mask (created on first use)
    _annular_mask = None
    _mask_shape = None

    @classmethod
    def _get_gamma_lut(cls) -> np.ndarray:
        """
        Get the pre-calculated gamma lookup table.
        Creates it on first call, then reuses cached version.
        """
        if cls._gamma_lut is None:
            inv_gamma = 1.0 / cls.GAMMA
            cls._gamma_lut = np.array([
                ((i / 255.0) ** inv_gamma) * 255
                for i in range(256)
            ]).astype(np.uint8)
        return cls._gamma_lut

    @classmethod
    def _get_annular_mask(cls, image_shape: tuple) -> np.ndarray:
        """
        Get the annular mask for the given image shape.
        Creates it on first call or if shape changes, then reuses cached version.
        """
        if cls._annular_mask is None or cls._mask_shape != image_shape[:2]:
            h, w = image_shape[:2]
            mask = np.zeros((h, w), dtype=np.uint8)

            # Draw outer circle (filled)
            cv2.circle(mask, (cls.CENTER_X, cls.CENTER_Y), cls.OUTER_RADIUS, 255, -1)

            # Draw inner circle (filled with 0 to create the hole)
            cv2.circle(mask, (cls.CENTER_X, cls.CENTER_Y), cls.INNER_RADIUS, 0, -1)

            cls._annular_mask = mask
            cls._mask_shape = image_shape[:2]

        return cls._annular_mask

    @staticmethod
    def _apply_gamma_lut(image: np.ndarray, lut: np.ndarray) -> np.ndarray:
        """Apply gamma correction using pre-calculated LUT."""
        return cv2.LUT(image, lut)

    @staticmethod
    def _compute_average_in_annulus(image: np.ndarray, mask: np.ndarray,
                                     gamma_lut: np.ndarray) -> float:
        """
        Compute average pixel value in the annular region.

        Steps:
        1. Apply gamma correction using LUT
        2. Convert to grayscale (B&W)
        3. Calculate average in the mask region

        Args:
            image: Input BGR image
            mask: Annular mask (255 = include, 0 = exclude)
            gamma_lut: Pre-calculated gamma lookup table

        Returns:
            average: Mean pixel value in the annular region
        """
        # Apply gamma correction using LUT
        gamma_image = CheckTopBearing._apply_gamma_lut(image, gamma_lut)

        # Convert to grayscale (B&W)
        gray_image = cv2.cvtColor(gamma_image, cv2.COLOR_BGR2GRAY)

        # Calculate average in the annular mask
        masked_pixels = gray_image[mask > 0]
        average = float(np.mean(masked_pixels))

        return average

    @staticmethod
    def _create_annotated_image(image: np.ndarray, is_bearing_present: bool,
                                 base_avg: float, curr_avg: float,
                                 difference: float) -> np.ndarray:
        """
        Create an annotated version of the input image showing the detection result.

        Args:
            image: Original input image
            is_bearing_present: Detection result
            base_avg: Average pixel value in base image
            curr_avg: Average pixel value in current image
            difference: curr_avg - base_avg

        Returns:
            Annotated image with result overlay
        """
        annotated = image.copy()

        # Draw the annular region circles
        color = (0, 255, 0) if is_bearing_present else (0, 0, 255)  # Green if OK, Red if not
        cv2.circle(annotated, (CheckTopBearing.CENTER_X, CheckTopBearing.CENTER_Y),
                   CheckTopBearing.OUTER_RADIUS, color, 2)
        cv2.circle(annotated, (CheckTopBearing.CENTER_X, CheckTopBearing.CENTER_Y),
                   CheckTopBearing.INNER_RADIUS, color, 2)

        # Add result text
        result_text = "TOP BEARING: PRESENT" if is_bearing_present else "TOP BEARING: NOT DETECTED"
        cv2.putText(annotated, result_text, (50, 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 3)

        # Add detailed values
        detail_text = f"Base: {base_avg:.1f}, Curr: {curr_avg:.1f}, Diff: {difference:+.1f}"
        cv2.putText(annotated, detail_text, (50, 100),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)

        threshold_text = f"Threshold: {CheckTopBearing.THRESHOLD}"
        cv2.putText(annotated, threshold_text, (50, 140),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)

        return annotated

    @staticmethod
    def checkTopBearing(anImage: np.ndarray,
                        currentPictures: Dict[str, np.ndarray | None] = None,
                        componentQRCode: str = DOST, gamma: float = 2.0) -> Tuple[np.ndarray | None, bool]:
        """
        Check for the presence of the Top Bearing.

        Compares the average brightness in an annular region between the current
        image and the previously captured Hub+Bottom Bearing image.

        Args:
            anImage: Current camera image to analyze
            currentPictures: Dictionary containing previously captured images,
                           must include 'hubAndBottomBearingPicture'
            componentQRCode: Component QR code for the assembly

        Returns:
            Tuple of:
                - Annotated image showing the detection result
                - Boolean indicating if Top Bearing is present (True) or not (False)
        """
        printBoldBlue(
            f"CheckTopBearing.checkSecondBearing() called with "
            f"anImage = {anImage.shape if anImage is not None else 'None'}"
        )

        # Basic validation
        # if componentQRCode is None:
        #     printBoldRed("CheckTopBearing: componentQRCode is None, returning False")
        #     return anImage, False

        modelName, lhs_rhs, _ = getModel_LHSRHS_AndTonnage(componentQRCode)
        printBoldBlue(f"Got modelName as {modelName}")
        if lhs_rhs == "RHS":
            saveImage(anImage, "TopBearing")

        if anImage is None:
            printBoldRed("CheckTopBearing: anImage is None, returning False")
            return anImage, False

        if currentPictures is None:
            printBoldRed("CheckTopBearing: currentPictures is None, returning False")
            return anImage, False

        # =====================================================================
        # HAND DETECTION CHECK - First check before any other processing
        # Ensure no operator hands are still in the frame
        # =====================================================================
        # if HandDetector.detectHands(anImage):
        #     printBoldRed("CheckTopBearing: HAND DETECTION FAILED - Operator hands detected in image!")
        #     printBoldRed("CheckTopBearing: Please ensure hands are completely out of frame before capturing")
        #     return anImage, False
        #
        # printBoldGreen("CheckTopBearing: Hand detection passed - No hands in frame")

        # Get the Hub+Bottom Bearing reference image
        base_image = currentPictures.get(hubAndBottomBearingPictureKeyString, None)

        if base_image is None:
            printBoldRed(
                "CheckTopBearing: Hub+Bottom Bearing reference image not found, "
                "returning False"
            )
            return anImage, False

        try:
            # Get pre-calculated gamma LUT and annular mask
            gamma_lut = CheckTopBearing._get_gamma_lut()
            mask = CheckTopBearing._get_annular_mask(anImage.shape)

            # Compute average pixel values in the annular region for both images
            base_avg = CheckTopBearing._compute_average_in_annulus(
                base_image, mask, gamma_lut)
            curr_avg = CheckTopBearing._compute_average_in_annulus(
                anImage, mask, gamma_lut)

            # Calculate difference
            difference = curr_avg - base_avg

            # Determine if bearing is present (current image should be brighter)
            is_bearing_present = difference > CheckTopBearing.THRESHOLD

            # Log the result
            if is_bearing_present:
                printBoldGreen(
                    f"CheckTopBearing: TOP BEARING DETECTED "
                    f"(base={base_avg:.1f}, curr={curr_avg:.1f}, "
                    f"diff={difference:+.1f} > threshold={CheckTopBearing.THRESHOLD})"
                )
            else:
                printBoldRed(
                    f"CheckTopBearing: TOP BEARING NOT DETECTED "
                    f"(base={base_avg:.1f}, curr={curr_avg:.1f}, "
                    f"diff={difference:+.1f} <= threshold={CheckTopBearing.THRESHOLD})"
                )

            # Create annotated output image
            annotated_image = CheckTopBearing._create_annotated_image(
                anImage, is_bearing_present, base_avg, curr_avg, difference
            )

            return annotated_image, is_bearing_present

        except Exception as e:
            printBoldRed(f"CheckTopBearing: Exception during detection: {e}")
            import traceback
            traceback.print_exc()
            return anImage, False


# =============================================================================
# ANALYSIS METHOD FOR BASE FILES VARIATION
# =============================================================================

def analyze_base_files_variation():
    """
    Analyze the variation in average brightness within base files (Hub and Bottom Bearing).

    This helps determine the natural variation in the "same category" images
    to fine-tune the detection threshold.

    Method:
        - Loop 1: i goes from 0 to n-1
        - Loop 2: j goes from i+1 to n-1
        - Calculate |avg(image_i) - avg(image_j)| for each pair
        - Store all absolute differences in a list
        - Publish statistics: max, min, avg, median, percentiles
    """
    import os
    import glob

    # Directory path
    DIRECTORY_PATH = "C:/AshokLeylandImages/DOST/LHS"

    # Color codes
    GREEN = "\033[1;32m"
    RED = "\033[1;31m"
    YELLOW = "\033[1;33m"
    CYAN = "\033[1;36m"
    RESET = "\033[0m"

    print(f"{CYAN}{'='*80}{RESET}")
    print(f"{CYAN}Base Files Variation Analysis{RESET}")
    print(f"{CYAN}{'='*80}{RESET}")
    print(f"\nDirectory: {DIRECTORY_PATH}")
    print(f"Parameters:")
    print(f"  Center: ({CheckTopBearing.CENTER_X}, {CheckTopBearing.CENTER_Y})")
    print(f"  Inner Radius: {CheckTopBearing.INNER_RADIUS}")
    print(f"  Outer Radius: {CheckTopBearing.OUTER_RADIUS}")
    print(f"  Gamma: {CheckTopBearing.GAMMA}")

    # Pick up all base files
    all_files = glob.glob(os.path.join(DIRECTORY_PATH, "*"))
    base_files = [f for f in all_files
                  if "Hub and" in os.path.basename(f) or "Hub_and" in os.path.basename(f)]
    base_files.sort()

    n = len(base_files)

    print(f"\n{YELLOW}Base files found: {n}{RESET}")
    for idx, f in enumerate(base_files):
        print(f"  [{idx}] {os.path.basename(f)}")

    if n < 2:
        print(f"{RED}Need at least 2 base files for comparison!{RESET}")
        return []

    # Get gamma LUT
    gamma_lut = CheckTopBearing._get_gamma_lut()

    # Pre-compute all averages
    print(f"\n{YELLOW}Computing averages for each file...{RESET}")
    averages = []
    for idx, base_file in enumerate(base_files):
        img = cv2.imread(base_file)
        if img is None:
            print(f"{RED}Could not load: {os.path.basename(base_file)}{RESET}")
            averages.append(None)
            continue

        mask = CheckTopBearing._get_annular_mask(img.shape)
        avg = CheckTopBearing._compute_average_in_annulus(img, mask, gamma_lut)
        averages.append(avg)
        print(f"  [{idx}] {os.path.basename(base_file)}: avg = {avg:.2f}")

    # Calculate all pairwise absolute differences
    print(f"\n{YELLOW}Computing pairwise absolute differences...{RESET}")
    absolute_differences = []
    pair_details = []

    # Loop 1: i from 0 to n-1
    for i in range(n):
        if averages[i] is None:
            continue

        # Loop 2: j from i+1 to n-1
        for j in range(i + 1, n):
            if averages[j] is None:
                continue

            # Calculate absolute difference
            abs_diff = abs(averages[i] - averages[j])
            absolute_differences.append(abs_diff)

            pair_details.append({
                'i': i,
                'j': j,
                'file_i': os.path.basename(base_files[i]),
                'file_j': os.path.basename(base_files[j]),
                'avg_i': averages[i],
                'avg_j': averages[j],
                'abs_diff': abs_diff
            })

            print(f"  [{i}] vs [{j}]: |{averages[i]:.2f} - {averages[j]:.2f}| = {abs_diff:.2f}")

    if not absolute_differences:
        print(f"{RED}No valid comparisons could be made!{RESET}")
        return []

    # Calculate statistics
    print(f"\n{CYAN}{'='*80}{RESET}")
    print(f"{CYAN}Statistics{RESET}")
    print(f"{CYAN}{'='*80}{RESET}")

    abs_diff_array = np.array(absolute_differences)

    min_val = np.min(abs_diff_array)
    max_val = np.max(abs_diff_array)
    avg_val = np.mean(abs_diff_array)
    median_val = np.median(abs_diff_array)
    p95 = np.percentile(abs_diff_array, 95)
    p99 = np.percentile(abs_diff_array, 99)
    p999 = np.percentile(abs_diff_array, 99.9)

    print(f"\nNumber of pairs compared: {len(absolute_differences)}")
    print(f"\n{YELLOW}Absolute Difference Statistics:{RESET}")
    print(f"  {'Min:':<20} {min_val:.4f}")
    print(f"  {'Max:':<20} {max_val:.4f}")
    print(f"  {'Average:':<20} {avg_val:.4f}")
    print(f"  {'Median:':<20} {median_val:.4f}")
    print(f"  {'95th Percentile:':<20} {p95:.4f}")
    print(f"  {'99th Percentile:':<20} {p99:.4f}")
    print(f"  {'99.9th Percentile:':<20} {p999:.4f}")

    # Recommendation for threshold
    print(f"\n{CYAN}{'='*80}{RESET}")
    print(f"{CYAN}Threshold Recommendation{RESET}")
    print(f"{CYAN}{'='*80}{RESET}")
    print(f"\n  Current threshold: {CheckTopBearing.THRESHOLD}")
    print(f"  Max same-category variation: {max_val:.4f}")
    print(f"\n  To avoid false positives, threshold should be > {max_val:.2f}")
    print(f"  Suggested safe threshold: {max_val * 1.5:.2f} to {max_val * 2:.2f}")

    print(f"\n{CYAN}{'='*80}{RESET}")

    return absolute_differences


# =============================================================================
# MAIN METHOD FOR TESTING
# =============================================================================

def main():
    """
    Test the CheckTopBearing implementation.

    a) Define a directory path variable
    b) Pick up all files with "Hub and" in their name as base_files
    c) Pick up all files with "Top Bearing" in their name as target_files
    d) Loop through each base_file and target_file combination
    e) Print success in green, failure in red with reason
    f) Print summary statistics
    """
    import os
    import glob

    # ==========================================================================
    # a) Define directory path variable
    # ==========================================================================
    DIRECTORY_PATH = "C:/AshokLeylandImages/DOST/LHS"

    # Color codes for terminal output
    GREEN = "\033[1;32m"
    RED = "\033[1;31m"
    YELLOW = "\033[1;33m"
    CYAN = "\033[1;36m"
    RESET = "\033[0m"

    print(f"{CYAN}{'='*80}{RESET}")
    print(f"{CYAN}CheckTopBearing Test Suite{RESET}")
    print(f"{CYAN}{'='*80}{RESET}")
    print(f"\nDirectory: {DIRECTORY_PATH}")
    print(f"Parameters:")
    print(f"  Center: ({CheckTopBearing.CENTER_X}, {CheckTopBearing.CENTER_Y})")
    print(f"  Inner Radius: {CheckTopBearing.INNER_RADIUS}")
    print(f"  Outer Radius: {CheckTopBearing.OUTER_RADIUS}")
    print(f"  Gamma: {CheckTopBearing.GAMMA}")
    print(f"  Threshold: {CheckTopBearing.THRESHOLD}")

    # ==========================================================================
    # b) Pick up all files with "Hub and" in their name as base_files
    # ==========================================================================
    all_files = glob.glob(os.path.join(DIRECTORY_PATH, "*"))
    base_files = [f for f in all_files
                  if "Hub and" in os.path.basename(f) or "Hub_and" in os.path.basename(f)]
    base_files.sort()

    print(f"\n{YELLOW}Base files (containing 'Hub and'):{RESET}")
    for f in base_files:
        print(f"  - {os.path.basename(f)}")

    if not base_files:
        print(f"{RED}No base files found!{RESET}")
        return

    # ==========================================================================
    # c) Pick up all files with "Top Bearing" in their name as target_files
    # ==========================================================================
    target_files = [f for f in all_files
                    if "Top Bearing" in os.path.basename(f) or "Top_Bearing" in os.path.basename(f)]
    # Test on previous stages files - None of these should qualify as a yes
    # target_files = [f for f in all_files
    #                 if "Hub and" in os.path.basename(f) or "Hub_and" in os.path.basename(f)]

    target_files.sort()

    print(f"\n{YELLOW}Target files (containing 'Top Bearing'):{RESET}")
    for f in target_files:
        print(f"  - {os.path.basename(f)}")

    if not target_files:
        print(f"{RED}No target files found!{RESET}")
        return

    # ==========================================================================
    # d) & e) & f) Loop through combinations and check
    # ==========================================================================
    print(f"\n{CYAN}{'='*80}{RESET}")
    print(f"{CYAN}Running Detection Tests{RESET}")
    print(f"{CYAN}{'='*80}{RESET}")

    successful_count = 0
    unsuccessful_count = 0
    results = []
    max_base_value = float('-inf')  # Track maximum base across all images
    min_target_value = float('inf')  # Track minimum target across all images

    # Loop 1: For each base_file
    for base_file in base_files:
        base_name = os.path.basename(base_file)

        # Load base image
        base_image = cv2.imread(base_file)
        if base_image is None:
            print(f"{RED}Could not load base file: {base_name}{RESET}")
            continue

        # Create currentPictures dict with the base image
        currentPictures = {hubAndBottomBearingPictureKeyString: base_image}

        print(f"\n{YELLOW}Base: {base_name}{RESET}")

        # Loop 2: For each target_file
        for target_file in target_files:
            target_name = os.path.basename(target_file)

            # Load target image
            target_image = cv2.imread(target_file)
            if target_image is None:
                print(f"  {RED}Could not load target file: {target_name}{RESET}")
                continue

            # Get the values for detailed reporting
            gamma_lut = CheckTopBearing._get_gamma_lut()
            mask = CheckTopBearing._get_annular_mask(target_image.shape)

            base_avg = CheckTopBearing._compute_average_in_annulus(
                base_image, mask, gamma_lut)
            curr_avg = CheckTopBearing._compute_average_in_annulus(
                target_image, mask, gamma_lut)
            difference = curr_avg - base_avg

            max_base_value = max(max_base_value, base_avg)
            min_target_value = min(min_target_value, curr_avg)

            # Run the check (suppress the built-in print statements)
            import io
            import sys
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()

            _, is_bearing_present = CheckTopBearing.checkTopBearing(
                target_image, currentPictures, "TEST_QR"
            )

            sys.stdout = old_stdout

            # e) & f) Print result
            if is_bearing_present:
                # Success - bearing was detected
                # print(f"  vs {target_name[:35]:<35} -> {GREEN}Check Successful{RESET} "
                #       f"(diff={difference:+.1f})")
                successful_count += 1
                results.append({
                    'base': base_name,
                    'target': target_name,
                    'success': True,
                    'difference': difference,
                    'base_avg': base_avg,
                    'curr_avg': curr_avg
                })
            else:
                # Failure - bearing was NOT detected
                print(f"  vs {target_name[:35]:<35} -> {RED}Check Unsuccessful{RESET}")
                print(f"      Reason: diff={difference:+.1f}, needed >{CheckTopBearing.THRESHOLD}")
                print(f"      Base avg: {base_avg:.1f}, Current avg: {curr_avg:.1f}")
                unsuccessful_count += 1
                results.append({
                    'base': base_name,
                    'target': target_name,
                    'success': False,
                    'difference': difference,
                    'base_avg': base_avg,
                    'curr_avg': curr_avg
                })

    # ==========================================================================
    # g) Summary statistics
    # ==========================================================================
    total_checks = successful_count + unsuccessful_count

    print(f"\n{YELLOW}Value Statistics:{RESET}")
    print(f"  Maximum base value across all images:   {max_base_value:.2f}")
    print(f"  Minimum target value across all images: {min_target_value:.2f}")

    print(f"\n{CYAN}{'='*80}{RESET}")
    print(f"{CYAN}Summary{RESET}")
    print(f"{CYAN}{'='*80}{RESET}")

    if total_checks > 0:
        success_percentage = (successful_count / total_checks) * 100

        print(f"\nTotal Checks:       {total_checks}")
        print(f"{GREEN}Successful:         {successful_count}{RESET}")
        print(f"{RED}Unsuccessful:       {unsuccessful_count}{RESET}")
        print(f"\n{CYAN}Success Rate:       {success_percentage:.1f}%{RESET}")

        # Additional statistics
        if results:
            differences = [r['difference'] for r in results]
            print(f"\nDifference Statistics:")
            print(f"  Min:  {min(differences):+.2f}")
            print(f"  Max:  {max(differences):+.2f}")
            print(f"  Mean: {sum(differences)/len(differences):+.2f}")

        # List unsuccessful checks if any
        if unsuccessful_count > 0:
            print(f"\n{RED}Unsuccessful Checks Details:{RESET}")
            for r in results:
                if not r['success']:
                    print(f"  - {r['base']}")
                    print(f"    vs {r['target']}")
                    print(f"    Base avg: {r['base_avg']:.1f}, Curr avg: {r['curr_avg']:.1f}, "
                          f"Diff: {r['difference']:+.1f}")
    else:
        print(f"{RED}No checks were performed!{RESET}")

    print(f"\n{CYAN}{'='*80}{RESET}")


# if __name__ == "__main__":
#     import sys
#
#     if len(sys.argv) > 1:
#         if sys.argv[1] == "--analyze" or sys.argv[1] == "-a":
#             # Run only the base files variation analysis
#             analyze_base_files_variation()
#         elif sys.argv[1] == "--test" or sys.argv[1] == "-t":
#             # Run only the detection tests
#             main()
#         elif sys.argv[1] == "--help" or sys.argv[1] == "-h":
#             print("Usage: python CheckTopBearing.py [option]")
#             print("\nOptions:")
#             print("  (no args)    Run both analysis and detection tests")
#             print("  --analyze, -a   Run only base files variation analysis")
#             print("  --test, -t      Run only detection tests")
#             print("  --help, -h      Show this help message")
#         else:
#             print(f"Unknown option: {sys.argv[1]}")
#             print("Use --help for usage information")
#     else:
#         # Default: run both
#         # analyze_base_files_variation()
#         print("\n\n")
#         main()