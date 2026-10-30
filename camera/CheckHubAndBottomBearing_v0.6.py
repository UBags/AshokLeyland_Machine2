"""
CheckHubAndBottomBearing.py
Implements hub and bottom bearing verification using MobileSAMv2 segmentation.
Uses HubAndBearingSegmenter_STANDALONE (no dependency on SAMSegmentation_v0_99)
"""

import copy
import numpy as np
import cv2
from typing import Dict, Tuple, Optional, List
import os
import sys

import torch
from numpy import ndarray

from BaseUtils import *
from logutils.SlaveLoggers import *
from utils.QRCodeHelper import getModel_LHSRHS_AndTonnage
from Configuration import CosThetaConfigurator
from utils.CosThetaPrintUtils import *

# Import the core segmentation engine (STANDALONE VERSION)
try:
    from camera.HubAndBearingSegmenter import (
        HubAndBearingSegmenter,
        select_best_group,
        paint_masks_on_image
    )

    SAM_AVAILABLE = True
except ImportError as e:
    print(f"Warning: HubAndBearingSegmenter not available: {e}")
    SAM_AVAILABLE = False
    HubAndBearingSegmenter = None  # ✅ Prevents NameError at line 87


def determine_smart_gamma_order(image: np.ndarray) -> List[int]:
    """
    Determine optimal gamma testing order based on image brightness.

    Args:
        image: Input RGB image

    Returns:
        List of gamma values in priority order
    """
    # Calculate average brightness (0-255)
    brightness = np.mean(image)

    printBoldBlue(f"[SmartGamma] Image brightness: {brightness:.1f}")

    if brightness < 60:
        # Very dark image - needs strong brightening
        gamma_order = [4, 6, 3, 5, 2, 1]
        printBoldBlue("[SmartGamma] Very dark → trying high gammas first")
    elif brightness < 90:
        # Dark image - moderate brightening
        gamma_order = [3, 4, 2, 5, 1, 6]
        printBoldBlue("[SmartGamma] Dark → trying medium-high gammas")
    elif brightness < 120:
        # Normal brightness
        gamma_order = [1, 3, 2, 4, 5, 6]
        printBoldBlue("[SmartGamma] Normal → standard order")
    elif brightness < 150:
        # Slightly bright
        gamma_order = [1, 2, 3, 4, 5, 6]
        printBoldBlue("[SmartGamma] Bright → trying lower gammas")
    else:
        # Very bright - may need darkening
        gamma_order = [1, 2, 3, 4, 5, 6]
        printBoldBlue("[SmartGamma] Very bright → trying lowest gammas")

    return gamma_order

class CheckHubAndBottomBearing:
    """
    Checks hub and bottom bearing assembly by detecting and counting mounting holes.

    Uses MobileSAMv2 for segmentation and sophisticated grouping algorithms to:
    1. Detect circular holes in annular region
    2. Group holes by distance and size
    3. Filter by spatial distribution and equispacing
    4. Verify correct count based on component model (DOST=4, DOSTPLUS=5)
    """

    # Class-level segmenter (lazy initialization)
    _segmenter: Optional[HubAndBearingSegmenter] = None
    _segmenter_initialized = False

    # Gamma correction LUT (gamma = 3.0)
    _gamma_lut = None

    @classmethod
    def _get_gamma_lut(cls) -> np.ndarray:
        """Get or create gamma correction lookup table (gamma=3.0)."""
        if cls._gamma_lut is None:
            inv_gamma = 1.0 / 3.0
            cls._gamma_lut = np.array([
                ((i / 255.0) ** inv_gamma) * 255
                for i in range(256)
            ]).astype(np.uint8)
        return cls._gamma_lut

    @classmethod
    def _get_segmenter(cls) -> Optional[HubAndBearingSegmenter]:
        """Get or initialize the segmenter (singleton pattern)."""
        if not SAM_AVAILABLE:
            return None

        if not cls._segmenter_initialized:
            cls._segmenter = HubAndBearingSegmenter(None, None)  # No paths needed
            cls._segmenter_initialized = True
        return cls._segmenter

    @classmethod
    def ensureNoBearingInCentre(cls, image: np.ndarray) -> bool:
        """
        Check if there's no bearing present in the center region.

        Args:
            image: Input BGR image

        Returns:
            True if average pixel value < 144 (no bearing), False otherwise
        """
        # Get gamma LUT
        gamma_lut = cls._get_gamma_lut()

        # Apply gamma correction
        gamma_corrected = cv2.LUT(image, gamma_lut)

        # Convert to grayscale
        gray = cv2.cvtColor(gamma_corrected, cv2.COLOR_BGR2GRAY)

        # Create annular mask (center: 635, 350; inner: 20; outer: 60)
        h, w = gray.shape
        mask = np.zeros((h, w), dtype=np.uint8)
        cv2.circle(mask, (635, 350), 60, 255, -1)  # Outer circle
        cv2.circle(mask, (635, 350), 20, 0, -1)  # Inner circle (hole)

        # Extract pixels within mask
        masked_pixels = gray[mask > 0]

        # Calculate average
        average = np.mean(masked_pixels)

        printBoldBlue(f"[ensureNoBearingInCentre] Average pixel value: {average:.2f}")

        # Return True if average < 144 (no bearing present)
        return average < 144

    @staticmethod
    def checkHubAndBottomBearing(
            anImage: np.ndarray,
            componentQRCode: str = None,
            use_smart_gamma: bool = True  # NEW PARAMETER
    ) -> tuple[None, bool, None] | tuple[ndarray, bool, None] | tuple[ndarray, bool, int]:
        """
        Test multiple gamma values in a single batch (faster than sequential).

        Returns:
            Tuple of (result_image, success, winning_gamma)
        """
        # if gamma_values is None:
        #     gamma_values = [1, 2, 3, 4, 5, 6]
        # printBoldBlue(f"[CheckHubAndBottomBearing] Batch mode with gammas={gamma_values}")

        if anImage is None or componentQRCode is None:
            return None, False, None

        # Get model info
        model_name, lhs_rhs, tonnage = getModel_LHSRHS_AndTonnage(componentQRCode)
        if model_name not in ["DOST", "DOSTPLUS"]:
            return anImage, False, None

        expected_count = 4 if model_name == "DOST" else 5

        # Check if bearing is present in center region
        if not CheckHubAndBottomBearing.ensureNoBearingInCentre(anImage):
            printBoldRed("[CheckHubAndBottomBearing] Bearing detected in center - should not be present")
            return anImage, False, None

        printBoldGreen("[CheckHubAndBottomBearing] No bearing in center - proceeding with verification")

        # Get segmenter
        segmenter = CheckHubAndBottomBearing._get_segmenter()
        if segmenter is None:
            return anImage, False, None

        # Convert BGR to RGB (camera provides BGR, segmenter expects RGB)
        anImage = cv2.cvtColor(anImage, cv2.COLOR_BGR2RGB)
        printBoldBlue("[CheckHubAndBottomBearing] Converted BGR to RGB")

        try:
            # SMART GAMMA ORDERING (MUST BE FIRST!)
            if use_smart_gamma:
                gamma_values = determine_smart_gamma_order(anImage)
            else:
                gamma_values = [1, 2, 3, 4, 5, 6]

            printBoldBlue(f"[Batch] Testing gammas in order: {gamma_values}")
            printBoldBlue(f"[Batch] Preprocessing {len(gamma_values)} gamma variants...")

            # STEP 1: Preprocess all gamma variants
            preprocessed_images = []
            for gamma in gamma_values:
                preprocessed, _ = segmenter.preprocess_image(
                    anImage, gamma=gamma,
                    bg_sx=10, bg_sy=10, cn_sx=10, cn_sy=10,
                    apply_bilateral=True, alter_gamma=True
                )
                preprocessed_images.append(preprocessed)

            # STEP 2: Batch segmentation
            printBoldBlue(f"[Batch] Running batch segmentation...")
            batch_results = segmenter.segment_holes_batch(
                preprocessed_images,
                model_type=model_name
            )

            # STEP 3: Find first success (early termination)
            for idx, (count, groups_with_tuples) in enumerate(batch_results):
                gamma = gamma_values[idx]

                if count > 0 and groups_with_tuples:
                    winning_group = select_best_group(groups_with_tuples)

                    if winning_group and len(winning_group) == expected_count:
                        printBoldGreen(f"[Batch] >>> WINNER: Gamma {gamma} (position {idx + 1}/{len(gamma_values)}) <<<")
                        result_image = paint_masks_on_image(anImage, winning_group, color=(0, 255, 0))

                        # Clear GPU after finding winner
                        if torch.cuda.is_available():
                            torch.cuda.empty_cache()

                        return cv2.cvtColor(result_image, cv2.COLOR_RGB2BGR), True, gamma

            # No success
            printBoldRed("[Batch] No valid detections in any gamma")
            return cv2.cvtColor(anImage, cv2.COLOR_RGB2BGR), False, None

        except Exception as e:
            printBoldRed(f"[Batch] Exception: {e}")
            import traceback

            traceback.print_exc()
            return cv2.cvtColor(anImage, cv2.COLOR_RGB2BGR), False, None

# For testing the new 6 images stacked on top of each other implementation - same process, same result, 3x faster
def test6NumpyStackedImagesImplementation():
    import time
    from pathlib import Path

    print("CheckHubAndBottomBearing Test - BATCH PROCESSING with Smart Gamma Ordering")
    print("=" * 80)

    # Folder path containing test images
    test_folder = "C:/AshokLeylandImages/DOSTPLUS/LHS/"
    # Use real QR code
    # DOST
    # test_qr = "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$"

    # DOSTPLUS
    test_qr = "8201206$400112VA1C$11770$09.03.2025$C5211701$MEK$40Cr4-B$1$SPDL ASSY-KNULH$SNPL-MAT$"

    # Output directory
    output_dir = "C:/Test"
    os.makedirs(output_dir, exist_ok=True)
    print(f"✓ Output directory: {output_dir}")

    # Get all image files (excluding knuckle)
    image_extensions = ['.png', '.jpg', '.jpeg', '.bmp']
    all_files = []

    if os.path.exists(test_folder):
        for ext in image_extensions:
            all_files.extend(Path(test_folder).glob(f'*{ext}'))

        # Filter out files with "knuckle" in name
        test_files = [f for f in all_files if 'knuckle' not in f.name.lower()]

        # Test on previous stages image
        # test_files = [f for f in all_files if 'knuckle' in f.name.lower()]

        print(f"✓ Found {len(test_files)} image files (excluding knuckle)")
        print(f"  Folder: {test_folder}")
    else:
        print(f"✗ Folder not found: {test_folder}")
        test_files = []

    if not test_files:
        print("No test files found. Exiting.")
        sys.exit(1)

    # Process each file
    overall_start = time.time()
    results_summary = []

    for idx, test_file in enumerate(test_files, 1):
        print(f"\n{'=' * 80}")
        print(f"[{idx}/{len(test_files)}] Processing: {test_file.name}")
        print(f"{'=' * 80}")

        # Load image
        test_image = cv2.imread(str(test_file))
        if test_image is None:
            print(f"  ✗ Failed to load image")
            results_summary.append((test_file.name, False, None, None))
            continue

        # test_image = cv2.cvtColor(test_image, cv2.COLOR_BGR2RGB)
        print(f"  ✓ Loaded image: {test_image.shape}")

        # TEST 1: Batch processing WITH smart gamma ordering
        print(f"\n  === Test 1: Batch with Smart Gamma ===")
        start_time = time.time()

        result_img_smart, success_smart, winning_gamma_smart = \
            CheckHubAndBottomBearing.checkHubAndBottomBearing(
                test_image,
                test_qr,
                use_smart_gamma=True
            )

        time_smart = time.time() - start_time
        status_smart = '✓ SUCCESS' if success_smart else '✗ FAIL'
        print(f"  Result: {status_smart}")
        if winning_gamma_smart:
            print(f"  Winning gamma: {winning_gamma_smart}")
        print(f"  Time: {time_smart:.3f}s")

        # TEST 2: Batch processing WITHOUT smart gamma (standard order)
        print(f"\n  === Test 2: Batch with Standard Order ===")
        start_time = time.time()

        result_img_standard, success_standard, winning_gamma_standard = \
            CheckHubAndBottomBearing.checkHubAndBottomBearing(
                test_image,
                test_qr,
                use_smart_gamma=False
            )

        time_standard = time.time() - start_time
        status_standard = '✓ SUCCESS' if success_standard else '✗ FAIL'
        print(f"  Result: {status_standard}")
        if winning_gamma_standard:
            print(f"  Winning gamma: {winning_gamma_standard}")
        print(f"  Time: {time_standard:.3f}s")

        # Compare
        if time_smart < time_standard:
            speedup = ((time_standard - time_smart) / time_standard) * 100
            print(f"\n  🚀 Smart gamma was {speedup:.1f}% FASTER")
        elif time_smart > time_standard:
            slowdown = ((time_smart - time_standard) / time_standard) * 100
            print(f"\n  ⚠️ Smart gamma was {slowdown:.1f}% SLOWER")
        else:
            print(f"\n  ⚖️ Same performance")

        # Use smart gamma result as final
        final_result = result_img_smart
        final_success = success_smart
        winning_gamma = winning_gamma_smart
        elapsed_time = time_smart

        # Save result to C:/Test
        if final_result is not None and final_success:
            output_filename = os.path.join(output_dir, f"result_{test_file.stem}_gamma{winning_gamma}.png")
            cv2.imwrite(output_filename, cv2.cvtColor(final_result, cv2.COLOR_RGB2BGR))
            print(f"\n  ✓ Saved: {output_filename}")

        results_summary.append((test_file.name, final_success, winning_gamma, elapsed_time, time_smart, time_standard))

        # Clear GPU cache
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.synchronize()

    # Final Summary
    overall_time = time.time() - overall_start

    print(f"\n{'=' * 80}")
    print("FINAL SUMMARY - BATCH PROCESSING PERFORMANCE")
    print(f"{'=' * 80}")
    print(f"Total files processed: {len(test_files)}")
    print(f"Total time: {overall_time:.2f}s")
    print(f"Average time per file: {overall_time / len(test_files):.2f}s")
    print()

    passed = sum(1 for _, success, _, _, _, _ in results_summary if success)
    failed = len(results_summary) - passed

    print(f"PASS: {passed}/{len(results_summary)}")
    print(f"FAIL: {failed}/{len(results_summary)}")
    print()

    # Calculate average speedup
    total_smart_time = sum(t_smart for _, _, _, _, t_smart, _ in results_summary if t_smart)
    total_standard_time = sum(t_std for _, _, _, _, _, t_std in results_summary if t_std)

    if total_standard_time > 0:
        overall_speedup = ((total_standard_time - total_smart_time) / total_standard_time) * 100
        print(f"Smart Gamma Overall Speedup: {overall_speedup:.1f}%")
        print(f"  Smart gamma total: {total_smart_time:.2f}s")
        print(f"  Standard order total: {total_standard_time:.2f}s")
        print()

    print("Detailed Results:")
    print(f"{'File':<40} {'Result':<8} {'Gamma':<7} {'Smart':<10} {'Standard':<10} {'Speedup':<10}")
    print("-" * 95)

    for filename, success, gamma, _, time_smart, time_standard in results_summary:
        result_str = 'PASS' if success else 'FAIL'
        gamma_str = str(gamma) if gamma else 'N/A'
        smart_str = f"{time_smart:.3f}s" if time_smart else 'N/A'
        standard_str = f"{time_standard:.3f}s" if time_standard else 'N/A'

        if time_smart and time_standard and time_standard > 0:
            speedup_pct = ((time_standard - time_smart) / time_standard) * 100
            speedup_str = f"{speedup_pct:+.1f}%"
        else:
            speedup_str = 'N/A'

        print(f"{filename:<40} {result_str:<8} {gamma_str:<7} {smart_str:<10} {standard_str:<10} {speedup_str:<10}")

    print(f"{'=' * 95}")

# test6NumpyStackedImagesImplementation()