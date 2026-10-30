"""
CheckBunk.py v2.0 - UPDATED FOR V0.7 PIPELINE
Implements Bunk hole verification using MobileSAMv2 with v0.7 preprocessing.

CHANGES FROM V1:
1. Uses BunkSegmenter with two-stage cropping (outer R=250, inner R=110)
2. Fixed gamma sequence: [7.75, 6.5, 4.0, 2.0, 1.0] instead of brightness-based selection
3. Batch processing with early termination on definitive patterns
4. No morphological processing (direct SAM mask filtering)
5. Enhanced early termination: stops on definitive mismatch (e.g., 4-hole when expected 2)

PIPELINE:
1. Parse QR code → determine expected count (DOST=4, DOSTPLUS=2)
2. Preprocess ALL 4 gammas (7.75, 6.5, 4.0, 2.0, 1.0)
3. Batch process in single torch context with early termination
4. Apply cascade geometric validation on each gamma
5. Return image with masks painted + success status
"""

import numpy as np
import cv2
from typing import Dict, Tuple, Optional
import os

from BaseUtils import *
from logutils.SlaveLoggers import *
from utils.QRCodeHelper import getModel_LHSRHS_AndTonnage
from utils.CosThetaPrintUtils import *
from concurrent.futures import ThreadPoolExecutor

# Import the v2 Bunk segmentation engine
try:
    from camera.BunkSegmenter import (
        BunkSegmenter,
        paint_bunk_masks_on_image,
        GAMMA_SEQUENCE
    )

    SAM_AVAILABLE = True
except ImportError as e:
    print(f"Warning: BunkSegmenter not available: {e}")
    SAM_AVAILABLE = False
    BunkSegmenter = None

# =============================================================================
# PRINT CONTROL FLAGS
# =============================================================================
PRINT_QR_PARSING = False
PRINT_IMAGE_PREPROCESSING = False
PRINT_BATCH_SEGMENTATION = False
PRINT_RESULT_PAINTING = False
PRINT_SUCCESS_EVALUATION = True


class CheckBunk:
    """
    Checks bunk assembly by detecting holes in the bunk region.

    Uses MobileSAMv2 with V0.7 pipeline:
    - Two-stage circular cropping (outer R=250, inner R=110)
    - Fixed gamma sequence [7.75, 6.5, 4.0, 2.0, 1.0]
    - Batch processing with early termination
    - Cascade geometric validation
    """

    # Class-level segmenter (singleton pattern)
    # _segmenter: Optional[BunkSegmenter] = None
    # _segmenter_initialized = False

    # @classmethod
    # def _get_segmenter(cls) -> Optional[BunkSegmenter]:
    #     """Get or initialize the segmenter (singleton pattern)."""
    #     if not SAM_AVAILABLE:
    #         return None
    #
    #     if not cls._segmenter_initialized:
    #         # cls._segmenter = BunkSegmenter(None, None)
    #         cls._segmenter = BunkSegmenter.get_instance()
    #         cls._segmenter_initialized = True
    #
    #     return cls._segmenter

    @staticmethod
    def checkBunk(
            anImage: np.ndarray,
            componentQRCode: str | None = None
    ) -> Tuple[np.ndarray | None, bool, None | float]:
        """
        Check bunk by detecting holes using MobileSAMv2 with V0.7 pipeline.

        NEW Processing Pipeline:
        1. Determine expected count from QR code (DOST=4, DOSTPLUS=2)
        2. PREPROCESS ALL 4 GAMMAS: [7.75, 6.5, 4.0, 2.0, 1.0]
        3. Run batch segmentation in single torch context
        4. Apply cascade geometric validation on each gamma
        5. EARLY TERMINATION on:
           - Found expected pattern with valid geometry
           - Found definitive mismatch (e.g., 4-hole square when expected 2)
           - Found definitive rejection (irregular geometry)
        6. Paint winning components on original image
        7. Return success based on count AND geometry validation

        Args:
            anImage: Input camera image (BGR from OpenCV)
            componentQRCode: QR code string to determine model (DOST/DOSTPLUS)

        Returns:
            Tuple of:
                - processed_image: Original image with component masks painted
                - success: True if correct hole count detected with valid geometry
                - winning_gamma: Gamma value that produced the result
        """
        if PRINT_QR_PARSING:
            printBoldBlue(f"[CheckBunk v2.0] Called with QRCode={componentQRCode}")

        # Validate inputs
        if anImage is None:
            if PRINT_QR_PARSING:
                printBoldRed("[CheckBunk] anImage is None")
            return None, False, None

        if componentQRCode is None:
            if PRINT_QR_PARSING:
                printBoldRed("[CheckBunk] componentQRCode is None")
            return anImage, False, None

        # Get segmenter
        # segmenter = CheckBunk._get_segmenter()
        segmenter = BunkSegmenter.get_instance() if SAM_AVAILABLE else None
        if segmenter is None:
            if PRINT_QR_PARSING:
                printBoldRed("[CheckBunk] Segmenter not available")
            return anImage, False, None

        # Determine expected count from QR code
        try:
            model, lhs_rhs, tonnage = getModel_LHSRHS_AndTonnage(componentQRCode)
            if PRINT_QR_PARSING:
                printBoldBlue(f"[CheckBunk] Model: {model}, LHS/RHS: {lhs_rhs}, Tonnage: {tonnage}")

            if model == "DOST":
                expected_count = 4
            elif model == "DOSTPLUS":
                expected_count = 2
            else:
                if PRINT_QR_PARSING:
                    printBoldRed(f"[CheckBunk] Unknown model: {model}")
                return anImage, False, None

            if PRINT_QR_PARSING:
                printBoldBlue(f"[CheckBunk] Expected count: {expected_count}")

        except Exception as e:
            if PRINT_QR_PARSING:
                printBoldRed(f"[CheckBunk] Failed to parse QR code: {e}")
                import traceback
                traceback.print_exc()
            return anImage, False, None

        # Convert BGR to RGB (camera provides BGR, segmenter expects RGB)
        anImage_rgb = cv2.cvtColor(anImage, cv2.COLOR_BGR2RGB)
        if PRINT_IMAGE_PREPROCESSING:
            printBoldBlue("[CheckBunk] Converted BGR to RGB")

        # STEP 1: Determine gamma sequence based on expected count
        from camera.BunkSegmenter import get_gamma_sequence_for_model
        gamma_sequence = get_gamma_sequence_for_model(expected_count)

        # Preprocess ALL gammas FIRST
        try:
            if PRINT_IMAGE_PREPROCESSING:
                printBoldBlue(f"[CheckBunk] Preprocessing {len(gamma_sequence)} gamma variants: {gamma_sequence}")
            preprocessed_images = []

            for gamma in gamma_sequence:
                preprocessed = segmenter._preprocess_image(anImage_rgb, gamma)
                preprocessed_images.append(preprocessed)

            if PRINT_IMAGE_PREPROCESSING:
                printBoldGreen(f"[CheckBunk] ✓ Preprocessed {len(preprocessed_images)} images")

        except Exception as e:
            if PRINT_IMAGE_PREPROCESSING:
                printBoldRed(f"[CheckBunk] Preprocessing failed: {e}")
                import traceback
                traceback.print_exc()
            return cv2.cvtColor(anImage_rgb, cv2.COLOR_RGB2BGR), False, None

        # STEP 2: Batch segmentation with cascade validation and early termination
        try:
            if PRINT_BATCH_SEGMENTATION:
                printBoldBlue("[CheckBunk] Running batch segmentation with cascade validation...")

            # Call batch processing method
            detected_count, components, winning_gamma, found_definitive = \
                segmenter.segment_holes_batch_from_preprocessed(
                    preprocessed_images,
                    gamma_sequence,  # ← NEW
                    expected_count=expected_count,
                    model_type=model
                )

            if PRINT_BATCH_SEGMENTATION:
                printBoldBlue(f"[CheckBunk] Result:")
                printBoldBlue(f"  Detected count: {detected_count}")
                printBoldBlue(f"  Expected count: {expected_count}")
                printBoldBlue(f"  Winning gamma: {winning_gamma}")
                printBoldBlue(f"  Definitive pattern: {found_definitive}")

            # SUCCESS CRITERIA:
            # - Count must match expected
            # - Must have found definitive pattern (geometry validated)
            success = (detected_count == expected_count and found_definitive)

            # SPECIAL CASE: If expected 2 but found valid 4-hole square
            # This is a FAIL (wrong model type - should be DOST not DOSTPLUS)
            if expected_count == 2 and detected_count == 4 and found_definitive:
                if PRINT_SUCCESS_EVALUATION:
                    printBoldRed(f"[CheckBunk] ✗ FAIL - Found 4-hole DOST pattern but expected 2-hole DOSTPLUS")
                success = False

            if PRINT_SUCCESS_EVALUATION:
                if success:
                    printBoldGreen(f"[CheckBunk] ✓ SUCCESS - Count {detected_count} matches expected with valid geometry")
                else:
                    printBoldRed(f"[CheckBunk] ✗ FAIL - Count {detected_count} != expected {expected_count} OR invalid geometry")

            # Paint masks on original image (even for failures, to show what was detected)
            result_image = paint_bunk_masks_on_image(anImage_rgb, components, color=(0, 255, 0))

            if PRINT_RESULT_PAINTING:
                printBoldBlue(f"[CheckBunk] Painted {len(components)} masks on image")

            # Convert back to BGR for OpenCV
            result_bgr = cv2.cvtColor(result_image, cv2.COLOR_RGB2BGR)

            return result_bgr, success, winning_gamma

        except Exception as e:
            if PRINT_BATCH_SEGMENTATION:
                printBoldRed(f"[CheckBunk] Segmentation failed: {e}")
                import traceback
                traceback.print_exc()
            return cv2.cvtColor(anImage_rgb, cv2.COLOR_RGB2BGR), False, None


def test_checkBunk():
    """Test function for CheckBunk v2.0."""
    import time
    from pathlib import Path

    print("CheckBunk v2.0 Test - V0.7 PIPELINE WITH FIXED GAMMA SEQUENCE")
    print("=" * 80)

    # Test folder path
    test_folder = "C:/AshokLeylandImages/DOST/LHS/"  # Adjust as needed

    # Output directory
    output_dir = "C:/Test/Bunk"
    os.makedirs(output_dir, exist_ok=True)
    print(f"✓ Output directory: {output_dir}")

    # Get all image files
    image_extensions = ['.png', '.jpg', '.jpeg', '.bmp']
    all_files = []

    if os.path.exists(test_folder):
        for ext in image_extensions:
            all_files.extend(Path(test_folder).glob(f'*{ext}'))

        # Filter for bunk images
        test_files = [f for f in all_files if 'bung' in f.name.lower()]

        print(f"✓ Found {len(test_files)} bunk image files")
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

    print(f"\nUsing QR code: {test_qr[:50]}...")

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

        result_img, success, gamma_used = CheckBunk.checkBunk(
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


# Uncomment to run tests
# if __name__ == "__main__":
#     test_checkBunk()