"""
Annular Region Comparison Tool - OPTIMIZED Production Version with CSV Export
Uses CheckTopBearing.detect_geometry_only() for detection.

OPTIMIZATIONS:
1. Pre-compute all nut detections ONCE (instead of 82 times)
2. Pre-load nut images into memory
3. Reuse gamma-corrected channels
4. Vectorized operations where possible
"""

import os
import sys
import glob
import random
import time
import csv
import numpy as np
import cv2
from typing import Tuple, Optional, Dict, Any, List

# Import CheckTopBearing
try:
    from camera.CheckTopBearing import CheckTopBearing

    HAVE_CHECKTOPBEARING = True
except ImportError:
    print("ERROR: Could not import CheckTopBearing. Make sure CheckTopBearing.py is in the same directory.")
    sys.exit(1)

# Create gamma=3.0 LUT (same as CheckTopBearing's FINAL_GAMMA_VALUE)
FINAL_GAMMA_VALUE = 1.0
_gamma_lut_3 = None


def get_gamma_lut():
    global _gamma_lut_3
    if _gamma_lut_3 is None:
        inv_gamma = 1.0 / FINAL_GAMMA_VALUE
        _gamma_lut_3 = np.array([((i / 255.0) ** inv_gamma) * 255 for i in range(256)]).astype(np.uint8)
    return _gamma_lut_3


def create_annular_mask(image_shape: Tuple[int, int],
                        center: Tuple[int, int],
                        outer_radius: int,
                        inner_radius: int = 0) -> np.ndarray:
    """Create binary mask for annular region."""
    h, w = image_shape
    cx, cy = center
    y, x = np.ogrid[:h, :w]
    distances = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)

    if inner_radius > 0:
        mask = ((distances <= outer_radius) & (distances >= inner_radius)).astype(np.uint8) * 255
    else:
        mask = (distances <= outer_radius).astype(np.uint8) * 255

    return mask


def create_disc_mask(image_shape: Tuple[int, int],
                     center: Tuple[int, int],
                     radius: int) -> np.ndarray:
    """Create binary mask for a circular disc."""
    h, w = image_shape
    cx, cy = center
    y, x = np.ogrid[:h, :w]
    distances = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)
    mask = (distances <= radius).astype(np.uint8) * 255
    return mask


def calculate_red_channel_average_in_mask(image: np.ndarray, mask: np.ndarray) -> float:
    """
    Extract red channel, apply gamma=3.0, then calculate average in masked region.

    Process:
    1. Extract red channel from BGR image
    2. Apply gamma=3.0 to red channel
    3. Calculate average of gamma-enhanced red channel pixels in mask
    """
    # Extract red channel (OpenCV uses BGR, so red is index 2)
    if len(image.shape) == 3:
        red_channel = image[:, :, 2]
    else:
        red_channel = image

    # Apply gamma=3.0 to red channel
    gamma_lut = get_gamma_lut()
    red_gamma = gamma_lut[red_channel]

    # Calculate average in mask
    masked_pixels = red_gamma[mask == 255]

    if len(masked_pixels) == 0:
        return 0.0

    return float(np.mean(masked_pixels))


def calculate_rb_diff_average_in_mask(image: np.ndarray, mask: np.ndarray) -> float:
    """
    Extract R and B channels, apply gamma=3.0, calculate R-B difference, then average in masked region.

    Process:
    1. Extract red channel (index 2) and blue channel (index 0) from BGR image
    2. Apply gamma=3.0 to both channels
    3. Calculate R_gamma - B_gamma for each pixel
    4. Calculate average of (R-B) differences in mask
    """
    # Extract red and blue channels (OpenCV uses BGR: B=0, G=1, R=2)
    if len(image.shape) == 3:
        red_channel = image[:, :, 2]
        blue_channel = image[:, :, 0]
    else:
        # Grayscale - use same channel for both
        red_channel = image
        blue_channel = image

    # Apply gamma=3.0 to both channels
    gamma_lut = get_gamma_lut()
    red_gamma = gamma_lut[red_channel]
    blue_gamma = gamma_lut[blue_channel]

    # Calculate R - B difference (as float to avoid underflow)
    rb_diff = red_gamma.astype(np.float32) - blue_gamma.astype(np.float32)

    # Calculate average in mask
    masked_rb_diff = rb_diff[mask == 255]

    if len(masked_rb_diff) == 0:
        return 0.0

    return float(np.mean(masked_rb_diff))


class NutDatabase:
    """Pre-computed nut detection database for fast lookups."""

    def __init__(self):
        self.nut_data = []  # List of dicts with filename, image, detection, red_gamma, blue_gamma

    def add_nut(self, filepath: str, image: np.ndarray, detection: Dict) -> bool:
        """Add a nut to the database with pre-computed data."""
        if detection is None:
            return False

        # Pre-compute gamma-corrected channels
        gamma_lut = get_gamma_lut()
        red_gamma = gamma_lut[image[:, :, 2]]
        blue_gamma = gamma_lut[image[:, :, 0]]

        self.nut_data.append({
            'filename': os.path.basename(filepath),
            'filepath': filepath,
            'image': image,
            'detection': detection,
            'red_gamma': red_gamma,
            'blue_gamma': blue_gamma
        })
        return True

    def __len__(self):
        return len(self.nut_data)

    def __iter__(self):
        return iter(self.nut_data)


def precompute_nut_database(nut_files: List[str], verbose: bool = True) -> NutDatabase:
    """
    Pre-compute all nut detections and gamma-corrected channels.
    This is the KEY OPTIMIZATION - do this once instead of 82 times!
    """
    print("=" * 80)
    print("PRE-COMPUTING NUT DATABASE (One-time operation)")
    print("=" * 80)

    db = NutDatabase()
    failed = 0

    start_time = time.time()

    for idx, nut_file in enumerate(nut_files, 1):
        if verbose and idx % 50 == 0:
            print(f"  Processing nut {idx}/{len(nut_files)}...")

        # Load nut image
        nut_img = cv2.imread(nut_file)
        if nut_img is None:
            failed += 1
            continue

        # Detect nut geometry
        nut_detection = CheckTopBearing.detect_geometry_only(nut_img)

        if nut_detection is None:
            failed += 1
            continue

        # Add to database
        db.add_nut(nut_file, nut_img, nut_detection)

    elapsed = time.time() - start_time

    print(f"  Completed in {elapsed:.2f}s")
    print(f"  Successfully processed: {len(db)}")
    print(f"  Failed: {failed}")
    print(f"  Average time per nut: {elapsed / len(nut_files):.3f}s")
    print("=" * 80)
    print()

    return db


def compare_with_nut_database(top_img: np.ndarray,
                              mask1: np.ndarray,
                              nut_db: NutDatabase,
                              image_shape: Tuple[int, int]) -> Tuple[List, List]:
    """
    Compare top bearing against entire nut database.
    Uses pre-computed nut data for massive speedup.
    """
    h, w = image_shape

    # Pre-compute gamma-corrected channels for top bearing
    gamma_lut = get_gamma_lut()
    top_red_gamma = gamma_lut[top_img[:, :, 2]]
    top_blue_gamma = gamma_lut[top_img[:, :, 0]]
    top_rb_gamma = top_red_gamma.astype(np.float32) - top_blue_gamma.astype(np.float32)

    r_differences = []
    rb_differences = []
    nut_no_intersection = 0

    for nut in nut_db:
        nut_center = nut['detection']['center']
        nut_outer_r = nut['detection']['outer_radius']

        # Create Mask-2: Exclude nut disc
        nut_disc = create_disc_mask((h, w), nut_center, nut_outer_r)
        mask2 = np.ones((h, w), dtype=np.uint8) * 255
        mask2[nut_disc == 255] = 0

        # Create Mask-3: Intersection
        mask3 = cv2.bitwise_and(mask1, mask2)
        mask3_area = np.sum(mask3 == 255)

        if mask3_area == 0:
            nut_no_intersection += 1
            continue

        # Use pre-computed gamma channels
        mask_bool = mask3 == 255

        # R channel comparison
        top_r_avg = float(np.mean(top_red_gamma[mask_bool]))
        nut_r_avg = float(np.mean(nut['red_gamma'][mask_bool]))
        r_diff = top_r_avg - nut_r_avg

        # R-B difference comparison
        top_rb_avg = float(np.mean(top_rb_gamma[mask_bool]))
        nut_rb = nut['red_gamma'].astype(np.float32) - nut['blue_gamma'].astype(np.float32)
        nut_rb_avg = float(np.mean(nut_rb[mask_bool]))
        rb_diff = top_rb_avg - nut_rb_avg

        r_differences.append((r_diff, nut['filename'], mask3_area, top_r_avg, nut_r_avg))
        rb_differences.append((rb_diff, nut['filename'], mask3_area, top_rb_avg, nut_rb_avg))

    return r_differences, rb_differences, nut_no_intersection


def process_directory(directory: str,
                      top_keyword: str = "top",
                      nut_keyword: str = "nut",
                      num_samples: int = 30,
                      random_seed: Optional[int] = None,
                      verbose: bool = True,
                      output_csv: str = "analysis.csv") -> None:
    """
    Main processing function with OPTIMIZATIONS.
    """
    if random_seed is not None:
        random.seed(random_seed)

    # Find files
    image_extensions = ['*.jpg', '*.jpeg', '*.png', '*.bmp', '*.tif', '*.tiff']
    all_files_set = set()

    for ext in image_extensions:
        all_files_set.update(glob.glob(os.path.join(directory, ext)))
        all_files_set.update(glob.glob(os.path.join(directory, ext.upper())))

    all_files = list(all_files_set)

    top_files = [f for f in all_files if top_keyword.lower() in os.path.basename(f).lower()]
    nut_files = [f for f in all_files if nut_keyword.lower() in os.path.basename(f).lower()]

    print("=" * 80)
    print("ANNULAR REGION COMPARISON - OPTIMIZED Production Version")
    print("=" * 80)
    print(f"Directory: {directory}")
    print(f"Total files found: {len(all_files)}")
    print(f"Top files found: {len(top_files)}")
    print(f"Nut files found: {len(nut_files)}")
    print()

    if len(top_files) == 0:
        print(f"ERROR: No files with '{top_keyword}' in filename found!")
        return

    if len(nut_files) == 0:
        print(f"ERROR: No files with '{nut_keyword}' in filename found!")
        return

    # Sample top files
    if len(top_files) > num_samples:
        top_files_selected = random.sample(top_files, num_samples)
        print(f"Randomly selected {num_samples} top files from {len(top_files)} available")
    else:
        top_files_selected = top_files
        print(f"Using all {len(top_files)} top files")

    print(f"Using all {len(nut_files)} nut files")
    print()

    # **KEY OPTIMIZATION**: Pre-compute nut database ONCE
    nut_db = precompute_nut_database(nut_files, verbose=verbose)

    if len(nut_db) == 0:
        print("ERROR: No valid nut detections!")
        return

    print("=" * 80)
    print("PROCESSING TOP BEARINGS")
    print("=" * 80)
    print()

    # CSV data collection
    csv_data = []

    # Process each top file
    for top_idx, top_file in enumerate(top_files_selected, 1):
        top_filename = os.path.basename(top_file)
        print(f"[{top_idx}/{len(top_files_selected)}] Processing: {top_filename}")

        # Load top file
        top_img = cv2.imread(top_file)
        if top_img is None:
            print(f"  ERROR: Could not load {top_file}")
            print()
            continue

        # Detect top bearing (ONCE per top file)
        if verbose:
            print(f"  [Step 1] Detecting top bearing annulus...")

        start_time = time.time()
        top_detection = CheckTopBearing.detect_geometry_only(top_img)
        detect_time = time.time() - start_time

        if top_detection is None:
            print(f"  WARNING: Detection failed for {top_filename}")
            print(f"  Skipping this file.")
            print()
            continue

        top_center = top_detection['center']
        top_inner_r = top_detection['inner_radius']
        top_outer_r = top_detection['outer_radius']

        print(f"  Top bearing detected in {detect_time:.2f}s:")
        print(f"    Center: {top_center}, Inner_r: {top_inner_r}, Outer_r: {top_outer_r}")
        print(
            f"    Thickness: {top_detection['thickness']:.1f}, Score: {top_detection['score']:.3f}, Avg_G: {top_detection['average_G']:.1f}")

        # Create Mask-1 (ONCE)
        h, w = top_img.shape[:2]
        mask1 = create_annular_mask((h, w), top_center, top_outer_r, top_inner_r)
        mask1_area = np.sum(mask1 == 255)

        if verbose:
            print(f"    Mask-1: {mask1_area} pixels")

        # Process against nut database (OPTIMIZED)
        print(f"  [Step 2] Comparing against {len(nut_db)} nut references...")

        start_nuts = time.time()
        r_differences, rb_differences, nut_no_intersection = compare_with_nut_database(
            top_img, mask1, nut_db, (h, w)
        )
        nuts_time = time.time() - start_nuts

        # Report results
        nut_processed = len(r_differences)
        print(f"  Comparison completed in {nuts_time:.2f}s ({nuts_time / len(nut_db) * 1000:.1f}ms per nut)")
        print(f"    Processed: {nut_processed}, No intersection: {nut_no_intersection}")

        # Initialize CSV row data
        csv_row = {
            'Sl No': top_idx,
            'Filename': top_filename,
            'Top avg (R+gamma)': '',
            'Nut avg (R+gamma)': '',
            'diff_min_R': '',
            'diff_max_R': '',
            '0.1%_R': '',
            '0.2%_R': '',
            '0.5%_R': '',
            '1%_R': '',
            'Top avg (R-B+gamma)': '',
            'Nut avg (R-B+gamma)': '',
            'diff_min_RB': '',
            'diff_max_RB': '',
            '0.1%_RB': '',
            '0.2%_RB': '',
            '0.5%_RB': '',
            '1%_RB': ''
        }

        if len(r_differences) > 0:
            # R channel results
            min_r_diff, min_r_nut_file, min_r_mask3_area, min_r_top_avg, min_r_nut_avg = min(r_differences,
                                                                                             key=lambda x: x[0])

            print(f"  Results (R Channel):")
            print(f"    Minimum difference: {min_r_diff:.2f} (with {min_r_nut_file})")
            print(f"      Mask-3 area: {min_r_mask3_area} pixels")
            print(f"      Top avg (R+gamma): {min_r_top_avg:.2f}, Nut avg (R+gamma): {min_r_nut_avg:.2f}")

            all_r_diffs = [d for d, _, _, _, _ in r_differences]
            all_r_diffs_sorted = sorted(all_r_diffs)

            # Calculate R channel percentiles
            r_p01 = np.percentile(all_r_diffs_sorted, 0.1)
            r_p02 = np.percentile(all_r_diffs_sorted, 0.2)
            r_p05 = np.percentile(all_r_diffs_sorted, 0.5)
            r_p1 = np.percentile(all_r_diffs_sorted, 1.0)

            print(f"      Difference range: [{min(all_r_diffs):.2f}, {max(all_r_diffs):.2f}]")
            print(f"      Percentiles: 0.1%={r_p01:.2f}, 0.2%={r_p02:.2f}, 0.5%={r_p05:.2f}, 1%={r_p1:.2f}")

            # R-B difference results
            min_rb_diff, min_rb_nut_file, min_rb_mask3_area, min_rb_top_avg, min_rb_nut_avg = min(rb_differences,
                                                                                                  key=lambda x: x[0])

            print(f"  Results (R-B Difference):")
            print(f"    Minimum difference: {min_rb_diff:.2f} (with {min_rb_nut_file})")
            print(f"      Mask-3 area: {min_rb_mask3_area} pixels")
            print(f"      Top avg (R-B+gamma): {min_rb_top_avg:.2f}, Nut avg (R-B+gamma): {min_rb_nut_avg:.2f}")

            all_rb_diffs = [d for d, _, _, _, _ in rb_differences]
            all_rb_diffs_sorted = sorted(all_rb_diffs)

            # Calculate R-B difference percentiles
            rb_p01 = np.percentile(all_rb_diffs_sorted, 0.1)
            rb_p02 = np.percentile(all_rb_diffs_sorted, 0.2)
            rb_p05 = np.percentile(all_rb_diffs_sorted, 0.5)
            rb_p1 = np.percentile(all_rb_diffs_sorted, 1.0)

            print(f"      Difference range: [{min(all_rb_diffs):.2f}, {max(all_rb_diffs):.2f}]")
            print(f"      Percentiles: 0.1%={rb_p01:.2f}, 0.2%={rb_p02:.2f}, 0.5%={rb_p05:.2f}, 1%={rb_p1:.2f}")

            # Populate CSV row
            csv_row['Top avg (R+gamma)'] = f"{min_r_top_avg:.2f}"
            csv_row['Nut avg (R+gamma)'] = f"{min_r_nut_avg:.2f}"
            csv_row['diff_min_R'] = f"{min(all_r_diffs):.2f}"
            csv_row['diff_max_R'] = f"{max(all_r_diffs):.2f}"
            csv_row['0.1%_R'] = f"{r_p01:.2f}"
            csv_row['0.2%_R'] = f"{r_p02:.2f}"
            csv_row['0.5%_R'] = f"{r_p05:.2f}"
            csv_row['1%_R'] = f"{r_p1:.2f}"
            csv_row['Top avg (R-B+gamma)'] = f"{min_rb_top_avg:.2f}"
            csv_row['Nut avg (R-B+gamma)'] = f"{min_rb_nut_avg:.2f}"
            csv_row['diff_min_RB'] = f"{min(all_rb_diffs):.2f}"
            csv_row['diff_max_RB'] = f"{max(all_rb_diffs):.2f}"
            csv_row['0.1%_RB'] = f"{rb_p01:.2f}"
            csv_row['0.2%_RB'] = f"{rb_p02:.2f}"
            csv_row['0.5%_RB'] = f"{rb_p05:.2f}"
            csv_row['1%_RB'] = f"{rb_p1:.2f}"
        else:
            print(f"  ERROR: No valid comparisons")

        # Add row to CSV data
        csv_data.append(csv_row)
        print()

    # Write CSV file
    if len(csv_data) > 0:
        headers = [
            'Sl No',
            'Filename',
            'Top avg (R+gamma)', 'Nut avg (R+gamma)',
            'diff_min_R', 'diff_max_R',
            '0.1%_R', '0.2%_R', '0.5%_R', '1%_R',
            'Top avg (R-B+gamma)', 'Nut avg (R-B+gamma)',
            'diff_min_RB', 'diff_max_RB',
            '0.1%_RB', '0.2%_RB', '0.5%_RB', '1%_RB'
        ]

        with open(output_csv, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=headers)
            writer.writeheader()
            writer.writerows(csv_data)

        print("=" * 80)
        print(f"CSV file written: {output_csv} with {len(csv_data)} rows")
        print("=" * 80)

    print("=" * 80)
    print("PROCESSING COMPLETE")
    print("=" * 80)


def main():
    """Main entry point."""
    directory = "C:/AshokLeylandImages/DOSTPLUS/LHS"
    output_csv = "TopBearing_Analysis_Optimized.csv"

    # Run processing
    process_directory(
        directory,
        "top",
        "nut",
        150,
        31,
        output_csv=output_csv
    )


if __name__ == "__main__":
    main()