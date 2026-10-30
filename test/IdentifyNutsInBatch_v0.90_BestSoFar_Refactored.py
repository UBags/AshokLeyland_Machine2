"""
IdentifyNutsInBatch.py - Batch Hexagon Nut Detection Application

This is the main application file for batch processing of images to detect hexagonal nuts.
It imports the core detection logic from HexagonNutDetector module and provides the
BatchNutProcessor class for batch operations.

Architecture:
    - HexagonNutDetector.py: Core detection module (NutDetector class and helpers)
    - IdentifyNutsInBatch.py: Batch processing application (BatchNutProcessor class)

Classes:
    BatchNutProcessor: Handles batch processing of images in a directory

Functions:
    main(): Entry point for the application

Usage:
    python IdentifyNutsInBatch.py

The script will process all images in the configured directory and output:
    - Console results for each image (YES/NO detection with scores)
    - Summary statistics
    - VRAM usage monitoring

Version: 1.1 (Reorganized - BatchNutProcessor moved from HexagonNutDetector)
"""

import os
import sys
import glob
import time
import cv2
import torch

# Import the core detection class and utilities from HexagonNutDetector module
from camera.HexagonNutDetector import NutDetector, Colors, print_vram_usage

# ============================================================================
# CONFIGURATION
# ============================================================================

# Input directory containing images to process
INPUT_DIRECTORY = "C:/AshokLeylandImages/DOST/LHS"
# Alternative: INPUT_DIRECTORY = "C:/RHS_Images/NutAndPlateWasher"


# ============================================================================
# BATCH NUT PROCESSOR CLASS
# ============================================================================

class BatchNutProcessor:
    """
    Batch processor for detecting hexagon nuts in multiple images.
    Uses NutDetector internally for actual detection logic.
    """

    def __init__(self):
        """Initialize batch processor."""
        print(f"\n{Colors.CYAN}Initializing BatchNutProcessor...{Colors.RESET}")

        # Create NutDetector (it handles ModelManager internally)
        self.nut_detector = NutDetector()

        print_vram_usage("After initialization | ")
        print(f"{Colors.GREEN}✓ BatchNutProcessor initialized{Colors.RESET}")

    def process_directory(self, input_dir: str):
        """
        Process all images in a directory.

        Args:
            input_dir: Path to directory containing images
        """
        # Find all images
        image_files = []
        for ext in ['*.jpg', '*.jpeg', '*.png', '*.bmp']:
            image_files.extend(glob.glob(os.path.join(input_dir, ext)))

        if not image_files:
            print(f"{Colors.RED}No images found in {input_dir}{Colors.RESET}")
            return

        total = len(image_files)

        print(f"\n{Colors.BOLD}Processing {total} images{Colors.RESET}")
        print(f"Pipeline: bg_norm(35/30) → gamma(2.5/3.0/2.0)")
        print(f"Detection: Hough lines + Center of mass with 25px disc exclusion")
        print(f"Strategy: Sequential processing with immediate early termination")
        print(f"Expected VRAM: ~1.5-1.8 GB (safe for RTX 3050)")
        print("=" * 70)

        # Process each image
        detected_count = 0
        error_count = 0
        batch_start = time.time()

        for img_idx, file_path in enumerate(image_files, 1):
            filename = os.path.basename(file_path)

            # Skip nut images (testing filter - comment out if you want to process all)
            if "nut" not in filename.lower():
                continue

            # START TIMING
            file_start = time.time()

            # Progress update every 10 images
            if img_idx % 10 == 0:
                elapsed = time.time() - batch_start
                avg_time = elapsed / img_idx
                eta_seconds = avg_time * (total - img_idx)
                print(f"\n{Colors.CYAN}Progress: {img_idx}/{total} | "
                      f"Avg: {avg_time:.2f}s/img | "
                      f"ETA: {eta_seconds/60:.1f}min{Colors.RESET}")
                print_vram_usage()

            try:
                # Load image
                image_bgr = cv2.imread(file_path)
                if image_bgr is None:
                    print(f"{filename}: {Colors.RED}ERROR (load failed){Colors.RESET}")
                    error_count += 1
                    continue

                image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)

                # Detect nut using NutDetector
                hexagon_found, result = self.nut_detector.detect_nut_in_image(
                    image_rgb,
                    center=(632, 360),
                    outer_radius=80,
                    inner_radius=0,
                    image_id=filename,
                    gamma_order=[2.5, 3.0, 2.0]  # Most likely first
                )

                file_elapsed = time.time() - file_start

                # Print result
                if hexagon_found:
                    area = result.get('area')
                    orig_area = result.get('orig_area', 0)
                    hex_center = result.get('hex_center', (0, 0))
                    center_dist = result.get('center_dist', 0.0)
                    sym_score = result.get('sym_score', 0.0)
                    composite_score = result.get('composite_score', 0.0)
                    quality_rating = result.get('quality_rating', 'UNKNOWN')
                    original_edges_count = result.get('original_edges_count', 0)
                    total_deviated_pixels = result.get('total_deviated_pixels', 0)

                    print(
                        f"{filename}: {Colors.GREEN}YES{Colors.RESET} (area check), "
                        f"orig_area={orig_area}, actual_area={area}, "
                        f"center=({hex_center[0]},{hex_center[1]}), dist={center_dist:.1f}, "
                        f"symmetry={composite_score:.3f} ({quality_rating}), "
                        f"thickness={sym_score:.3f}, "
                        f"orig_edges≥ref={original_edges_count}, "
                        f"deviated_px={total_deviated_pixels}; {file_elapsed:.2f}s"
                    )
                    detected_count += 1
                else:
                    # Failure - use compact format
                    fail_metrics = result.get('failure_metrics')

                    if fail_metrics and isinstance(fail_metrics, dict):
                        reason = fail_metrics.get('reason')
                        area_val = fail_metrics.get('area', 0)

                        if reason == 'area':
                            print(f"{filename}: {Colors.RED}NO{Colors.RESET} (area={area_val}); {file_elapsed:.2f}s")
                        else:
                            print(f"{filename}: {Colors.RED}NO{Colors.RESET} (no detection); {file_elapsed:.2f}s")
                    else:
                        print(f"{filename}: {Colors.RED}NO{Colors.RESET} (no detection); {file_elapsed:.2f}s")

            except Exception as e:
                file_elapsed = time.time() - file_start
                print(f"{filename}: {Colors.RED}ERROR ({e}){Colors.RESET}; {file_elapsed:.2f}s")
                import traceback
                traceback.print_exc()
                error_count += 1

        total_elapsed = time.time() - batch_start

        # Summary
        print("\n" + "=" * 70)
        print(f"{Colors.BOLD}Summary:{Colors.RESET}")
        print(f"  Total images:      {total}")
        print(f"  Hexagons detected: {Colors.GREEN}{detected_count}{Colors.RESET}")
        print(f"  Not detected:      {total - detected_count - error_count}")
        print(f"  Errors:            {Colors.RED}{error_count}{Colors.RESET}")
        print(f"  Total time:        {total_elapsed:.2f}s ({total_elapsed/60:.1f}min)")
        print(f"  Avg per image:     {total_elapsed / total:.2f}s")
        print(f"  Detection rate:    {100 * detected_count / total:.1f}%")
        print_vram_usage("  Final ")
        print("=" * 70)


# ============================================================================
# MAIN FUNCTION
# ============================================================================

def main():
    print("=" * 70)
    print("Batch Hexagon Nut Detection - Single-Threaded Version v0.90")
    print("=" * 70)
    print("Architecture:")
    print("  • Single-threaded: Simple sequential processing")
    print("  • ModelManager: Shared SAM and YOLO models")
    print("  • NutDetector: Reusable detection class")
    print("  • BatchProcessor: Handles batch image processing")
    print("Pipeline:")
    print("  • Annular region: outer_radius=80, inner_radius=0")
    print("  • bg_norm variations: (35,35) and (30,30)")
    print("  • gamma variations: 2.5, 3.0, 2.0 (most likely first)")
    print("  • 6 total variations with immediate early termination")
    print("Detection:")
    print("  • Hough lines-based hexagon identification")
    print("  • Center of mass calculation")
    print("  • 25-pixel disc exclusion for actual_nut_mask")
    print("  • Hexagon validation (side lengths, angles)")
    print("  • Comprehensive symmetry and thickness scoring")
    print("  • Original edge validation")
    print("Parameters:")
    print("  • Area limits: 3000-5500 pixels")
    print("  • Vertices: 5-8 (or exactly 6 from Hough)")
    print("  • Aspect ratio: 0.7-1.3")
    print("  • Disc exclusion radius: 25 pixels")
    print("Expected:")
    print("  • VRAM: ~1.5-1.8 GB")
    print("  • Processing: 0.5-1.5s per image")
    print("  • Memory savings: ~100MB vs threaded version")
    print("=" * 70)

    input_dir = INPUT_DIRECTORY

    # Verify input directory
    if not os.path.exists(input_dir):
        print(f"{Colors.RED}ERROR: Input directory not found: {input_dir}{Colors.RESET}")
        print(f"Please update INPUT_DIRECTORY at the top of this file")
        return 1

    # GPU info
    if torch.cuda.is_available():
        gpu_name = torch.cuda.get_device_name(0)
        gpu_mem = torch.cuda.get_device_properties(0).total_memory / 1024**3
        print(f"\nGPU: {gpu_name} ({gpu_mem:.1f}GB)")
        print(f"CUDA Version: {torch.version.cuda}")
        print(f"PyTorch Version: {torch.__version__}")
    else:
        print(f"\n{Colors.YELLOW}WARNING: No CUDA available, running on CPU{Colors.RESET}")

    try:
        processor = BatchNutProcessor()
        processor.process_directory(input_dir)
        return 0
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Interrupted by user{Colors.RESET}")
        return 130
    except Exception as e:
        print(f"\n{Colors.RED}FATAL ERROR: {e}{Colors.RESET}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())