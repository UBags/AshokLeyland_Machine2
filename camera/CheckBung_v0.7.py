"""
CheckBunk.py - REWRITTEN FOR BATCH OPTIMIZATION
Implements bung hole verification using MobileSAMv2 segmentation.
Uses BunkSegmenter with batch processing optimization.

CHANGES FROM ORIGINAL:
1. Moved gamma determination to checkBung() (from BunkSegmenter)
2. Preprocess ALL gammas before calling segmenter
3. Call new segment_holes_batch_from_preprocessed() method
4. Early termination on definitive pattern match
5. No threading - direct batch call
"""

import numpy as np
import cv2
from typing import Dict, Tuple, Optional
import os

from BaseUtils import *
from logutils.SlaveLoggers import *
from utils.QRCodeHelper import getModel_LHSRHS_AndTonnage
from utils.CosThetaPrintUtils import *
from utils.RedisUtils import capPressBunkPictureKeyString

# Import the bung segmentation engine
try:
    from camera.BungSegmenter import (
        BungSegmenter,
        paint_bung_masks_on_image
    )

    SAM_AVAILABLE = True
except ImportError as e:
    print(f"Warning: BunkSegmenter not available: {e}")
    SAM_AVAILABLE = False
    BungSegmenter = None


class CheckBunk:
    """
    Checks bung assembly by detecting holes in the bung region.

    Uses MobileSAMv2 for segmentation with optimized batch processing:
    1. Preprocesses all gamma variants upfront
    2. Processes in single torch context
    3. Early termination on definitive pattern match
    4. GPU cache management
    """

    # Class-level segmenter (lazy initialization)
    _segmenter: Optional[BungSegmenter] = None
    _segmenter_initialized = False

    @classmethod
    def _get_segmenter(cls) -> Optional[BungSegmenter]:
        """Get or initialize the segmenter (singleton pattern)."""
        if not SAM_AVAILABLE:
            return None

        if not cls._segmenter_initialized:
            # No need to pass paths - ModelManager handles it
            cls._segmenter = BungSegmenter(None, None)
            cls._segmenter_initialized = True

        return cls._segmenter

    @staticmethod
    def checkBung(
            anImage: np.ndarray,
            componentQRCode: str | None = None
    ) -> Tuple[np.ndarray | None, bool, None | float]:
        """
        Check bung by detecting holes using MobileSAMv2 with OPTIMIZED batch processing.

        NEW Processing Pipeline:
        1. Determine expected count from QR code (DOST=4, DOSTPLUS=2)
        2. Determine gamma values based on image brightness
        3. PREPROCESS ALL GAMMAS FIRST (key optimization)
        4. Run batch segmentation in single torch context
        5. Early termination on definitive pattern match
        6. Paint winning components on original image
        7. Verify count matches expected

        Args:
            anImage: Input camera image (RGB)
            currentPictures: Dictionary of all captured images (not used currently)
            componentQRCode: QR code string to determine model (DOST/DOSTPLUS)

        Returns:
            Tuple of:
                - processed_image: Original image with component masks painted
                - success: True if correct hole count detected with definitive pattern
                - winning_gamma: Gamma value that produced the correct count (or best attempt)
        """
        printBoldBlue(f"[CheckBunk] Called with QRCode={componentQRCode}")

        # Validate inputs
        if anImage is None:
            printBoldRed("[CheckBunk] anImage is None")
            return None, False, None

        if componentQRCode is None:
            printBoldRed("[CheckBunk] componentQRCode is None")
            return anImage, False, None

        # Get segmenter
        segmenter = CheckBung._get_segmenter()
        if segmenter is None:
            printBoldRed("[CheckBunk] Segmenter not available")
            return anImage, False, None

        # Determine expected count from QR code
        try:
            model, lhs_rhs, tonnage = getModel_LHSRHS_AndTonnage(componentQRCode)
            printBoldBlue(f"[CheckBunk] Model: {model}, LHS/RHS: {lhs_rhs}, Tonnage: {tonnage}")

            if lhs_rhs == "RHS":
                saveImage(anImage, "Bung")

            if model == "DOST":
                expected_count = 4
            elif model == "DOSTPLUS":
                expected_count = 2
            else:
                printBoldRed(f"[CheckBunk] Unknown model: {model}")
                return anImage, False, None

            printBoldBlue(f"[CheckBunk] Expected count: {expected_count}")

        except Exception as e3:
            printBoldRed(f"[CheckBunk] Failed to parse QR code: {e3}")
            import traceback
            traceback.print_exc()
            return anImage, False, None

        # Convert BGR to RGB (camera provides BGR, segmenter expects RGB)
        anImage = cv2.cvtColor(anImage, cv2.COLOR_BGR2RGB)
        printBoldBlue("[CheckBunk] Converted BGR to RGB")

        # OPTIMIZATION 1: Determine gamma values (moved from segmenter)
        try:
            gamma_values = segmenter._determine_gamma_values(anImage)
            printBoldBlue(f"[CheckBunk] Gamma values: {gamma_values}")
        except Exception as e2:
            printBoldRed(f"[CheckBunk] Failed to determine gamma values: {e2}")
            return cv2.cvtColor(anImage, cv2.COLOR_RGB2BGR), False, None

        # OPTIMIZATION 2: Preprocess ALL gammas FIRST
        try:
            printBoldBlue(f"[CheckBunk] Preprocessing {len(gamma_values)} gamma variants...")
            preprocessed_images = []

            for gamma in gamma_values:
                preprocessed = segmenter._preprocess_image(anImage, gamma)
                preprocessed_images.append(preprocessed)

            printBoldGreen(f"[CheckBunk] ✓ Preprocessed {len(preprocessed_images)} images")

        except Exception as e1:
            printBoldRed(f"[CheckBunk] Preprocessing failed: {e1}")
            import traceback
            traceback.print_exc()
            return cv2.cvtColor(anImage, cv2.COLOR_RGB2BGR), False, None

        # OPTIMIZATION 3: Batch segmentation with single torch context
        try:
            printBoldBlue("[CheckBunk] Running batch segmentation...")

            # Call optimized batch method
            detected_count, components, winning_gamma = segmenter.segment_holes_batch_from_preprocessed(
                preprocessed_images,
                gamma_values,
                expected_count=expected_count,
                model_type=model
            )

            printBoldBlue(f"[CheckBunk] Detected count: {detected_count} (expected: {expected_count})")
            printBoldBlue(f"[CheckBunk] Winning gamma: {winning_gamma}")

            # Check if count matches expected
            success = (detected_count == expected_count)

            if success:
                printBoldGreen(f"[CheckBunk] ✓ SUCCESS - Count matches expected ({expected_count})")
            else:
                printBoldRed(f"[CheckBunk] ✗ FAIL - Count {detected_count} != expected {expected_count}")

            # Paint masks on original image
            result_image = paint_bung_masks_on_image(anImage, components, color=(0, 255, 0))

            return cv2.cvtColor(result_image, cv2.COLOR_RGB2BGR), success, winning_gamma

        except Exception as e4:
            printBoldRed(f"[CheckBunk] Segmentation failed: {e4}")
            import traceback
            traceback.print_exc()
            return cv2.cvtColor(anImage, cv2.COLOR_RGB2BGR), False, None


def test_checkBung():
    """Test function for CheckBunk."""
    import time
    from pathlib import Path

    print("CheckBunk Test - BATCH OPTIMIZED VERSION")
    print("=" * 80)

    # Test folder path
    test_folder = "C:/AshokLeylandImages/DOST/LHS/"  # Adjust as needed

    # Output directory
    output_dir = "C:/Test/Bung"
    os.makedirs(output_dir, exist_ok=True)
    print(f"✓ Output directory: {output_dir}")

    # Get all image files
    image_extensions = ['.png', '.jpg', '.jpeg', '.bmp']
    all_files = []

    if os.path.exists(test_folder):
        for ext in image_extensions:
            all_files.extend(Path(test_folder).glob(f'*{ext}'))

        # Filter for nut and plate washer images to check false positives
        # test_files = [f for f in all_files if 'nut' in f.name.lower()]

        # Filter for bung images (you may need to adjust this filter)
        test_files = [f for f in all_files if 'bung' in f.name.lower()]

        print(f"✓ Found {len(test_files)} bung image files")
        print(f"  Folder: {test_folder}")
    else:
        print(f"✗ Folder not found: {test_folder}")
        test_files = []

    if not test_files:
        print("No test files found. Exiting.")
        return

    # Test QR codes
    # DOST - expects 4 holes
    dost_qr = "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$"

    # DOSTPLUS - expects 2 holes
    dostplus_qr = "8201206$400112VA1C$11770$09.03.2025$C5211701$MEK$40Cr4-B$1$SPDL ASSY-KNULH$SNPL-MAT$"

    # Choose based on folder
    test_qr = dost_qr if 'DOST' in test_folder and 'DOSTPLUS' not in test_folder else dostplus_qr

    # Process each file
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

        print(f"  ✓ Loaded image: {test_image.shape}")

        # Run check
        start_time = time.time()

        result_img, success, gamma_used = CheckBung.checkBunk(
            test_image,
            componentQRCode=test_qr
        )

        elapsed_time = time.time() - start_time

        status = '✓ SUCCESS' if success else '✗ FAIL'
        print(f"  Result: {status}")
        print(f"  Time: {elapsed_time:.3f}s")
        print(f"  Gamma: {gamma_used}")

        # Save result
        if result_img is not None:
            output_filename = os.path.join(output_dir, f"result_{test_file.stem}.png")
            cv2.imwrite(output_filename, result_img)
            print(f"  ✓ Saved: {output_filename}")

        results_summary.append((test_file.name, success, elapsed_time, gamma_used))

    # Final Summary
    print(f"\n{'=' * 80}")
    print("FINAL SUMMARY")
    print(f"{'=' * 80}")
    print(f"Total files processed: {len(test_files)}")
    print()

    passed = sum(1 for _, success, _, _ in results_summary if success)
    failed = len(results_summary) - passed

    print(f"PASS: {passed}/{len(results_summary)}")
    print(f"FAIL: {failed}/{len(results_summary)}")
    print()

    print("Detailed Results:")
    print(f"{'File':<50} {'Result':<8} {'Time':<8} {'Gamma':<8}")
    print("-" * 80)
    for filename, success, elapsed, gamma in results_summary:
        result_str = 'PASS' if success else 'FAIL'
        time_str = f"{elapsed:.2f}s" if elapsed else 'N/A'
        gamma_str = f"{gamma:.2f}" if gamma is not None else 'N/A'
        print(f"{filename:<50} {result_str:<8} {time_str:<8} {gamma_str:<8}")

    print(f"{'=' * 80}")

# if __name__ == "__main__":
#     test_checkBung()
