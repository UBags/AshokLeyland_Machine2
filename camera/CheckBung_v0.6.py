"""
CheckBunk.py
Implements bung hole verification using MobileSAMv2 segmentation.
Uses BunkSegmenter with morphological post-processing.
"""

import numpy as np
import cv2
from typing import Dict, Tuple, Optional
import os

from BaseUtils import *
from logutils.SlaveLoggers import *
from utils.QRCodeHelper import getModel_LHSRHS_AndTonnage
from utils.CosThetaPrintUtils import *

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

    Uses MobileSAMv2 for segmentation and morphological operations to:
    1. Detect holes in circular bung region
    2. Apply morphological opening to clean up detections
    3. Filter connected components by shape and size
    4. Verify correct count based on component model (DOST=4, DOSTPLUS=2)
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
            try:
                # Find MobileSAM paths (from project root)
                from BaseUtils import get_project_root
                project_root = get_project_root()

                mobile_sam_path = os.path.join(project_root, "models", "MobileSAM", "weights", "mobile_sam.pt")
                yolo_path = os.path.join(project_root, "models", "MobileSAM", "MobileSAMv2", "weight",
                                         "ObjectAwareModel.pt")

                if os.path.exists(mobile_sam_path) and os.path.exists(yolo_path):
                    cls._segmenter = BungSegmenter(mobile_sam_path, yolo_path)
                    print("[CheckBunk] Initialized MobileSAMv2 segmenter")
                else:
                    print(f"[CheckBunk] Model files not found:")
                    print(f"  MobileSAM: {mobile_sam_path}")
                    print(f"  YOLO: {yolo_path}")
                    cls._segmenter = None
            except Exception as e:
                print(f"[CheckBunk] Failed to initialize segmenter: {e}")
                import traceback
                traceback.print_exc()
                cls._segmenter = None
            finally:
                cls._segmenter_initialized = True

        return cls._segmenter

    @staticmethod
    def checkBung(
            anImage: np.ndarray,
            currentPictures: Dict[str, np.ndarray | None] = None,
            componentQRCode: str = None
    ) -> Tuple[np.ndarray | None, bool,  None | float]:
        """
        Check bung by detecting holes using MobileSAMv2 with morphological processing.

        Processing Pipeline:
        1. Determine expected count from QR code (DOST=4, DOSTPLUS=2)
        2. Extract and preprocess bung region with adaptive gamma
        3. Run MobileSAMv2 segmentation
        4. Apply morphological operations and filter components
        5. Paint winning components on original image
        6. Verify count matches expected

        Args:
            anImage: Input camera image (RGB)
            currentPictures: Dictionary of all captured images (not used currently)
            componentQRCode: QR code string to determine model (DOST/DOSTPLUS)

        Returns:
            Tuple of:
                - processed_image: Original image with component masks painted
                - success: True if correct hole count detected
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

            if model == "DOST":
                expected_count = 4
            elif model == "DOSTPLUS":
                expected_count = 2
            else:
                printBoldRed(f"[CheckBunk] Unknown model: {model}")
                return anImage, False, None

            printBoldBlue(f"[CheckBunk] Expected count: {expected_count}")

        except Exception as e:
            printBoldRed(f"[CheckBunk] Failed to parse QR code: {e}")
            import traceback
            traceback.print_exc()
            return anImage, False, None

        # Run segmentation with batch processing
        try:
            printBoldBlue("[CheckBunk] Running segmentation...")

            # Process with adaptive gamma
            detected_count, components, winning_gamma = segmenter.segment_batch_from_unprocessed_image(
                anImage,
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

            return result_image, success, winning_gamma

        except Exception as e:
            printBoldRed(f"[CheckBunk] Segmentation failed: {e}")
            import traceback
            traceback.print_exc()
            return anImage, False, None


def test_checkBung():
    """Test function for CheckBunk."""
    import time
    from pathlib import Path

    print("CheckBunk Test")
    print("=" * 80)

    # Test folder path
    test_folder = "C:/AshokLeylandImages/DOST/LHS"  # Adjust as needed

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

        # Filter for bung images (you may need to adjust this filter)
        test_files = [f for f in all_files if 'nut' in f.name.lower()]
        # test_files = [f for f in all_files if 'bung' in f.name.lower()]

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

        test_image = cv2.cvtColor(test_image, cv2.COLOR_BGR2RGB)
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
            cv2.imwrite(output_filename, cv2.cvtColor(result_img, cv2.COLOR_RGB2BGR))
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


if __name__ == "__main__":
    test_checkBung()