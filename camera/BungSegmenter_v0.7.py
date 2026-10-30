"""
BunkSegmenter.py
Bung hole detection using MobileSAMv2 segmentation with morphological post-processing.
Uses preprocessing pipeline similar to HubAndBearingSegmenter but with different post-SAM processing.
"""
import warnings
import numpy as np
import torch
import cv2
from typing import List, Dict, Any, Tuple, Optional, Union
import sys
import os

from BaseUtils import get_project_root
from camera.ModelManager import ModelManager

# Import normalization methods
from utils.ImageNormalisationWithMask import (
    rgb2gray,
    ensure_float32,
    create_annular_mask,
    extract_annular_region,
    pixBackgroundNorm_masked,
    pixContrastNorm_masked
)

# Configuration constants
BUNG_CENTER = (627, 334)
BUNG_RADIUS = 98
BUNG_INNER_RADIUS = 0  # Full disk, not annular

# Brightness threshold for gamma selection
BRIGHTNESS_THRESHOLD = 112

# Gamma values based on brightness
BRIGHT_GAMMA_VALUES = [0.75, 1.25, 1.5, 1.75, 2.0, 2.5, 4.0]  # Added 4.0
DARK_GAMMA_VALUES = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]

# Post-SAM morphological parameters
CROP_MARGIN = 1  # Radius reduction for cropping
SQUARE_SIZE = 240  # Square region size (radius + padding)
MORPH_KERNEL_SIZE = 11  # Circular kernel size for opening

# Connected component filtering thresholds
ASPECT_RATIO_THRESHOLD = 0.5  # min(h,w) / max(h,w) must be >= this
MIN_COMPONENT_AREA = 125
AREA_RATIO_THRESHOLD = 0.5  # component_area / (h*w) must be >= this
EQUIDISTANT_DISTANCE_DEVIATION_THRESHOLD = 0.2 # 20% deviation is OK while finding equidistance
                                               # because of bungs kept at angle will give skewed quadrilaterals
MINIMUM_INTERIOR_ANGLE = 65.0 # Minimum angle of a quadrilateral
ALLOWED_STD_DEVIATION_IN_ANGLES = 20.0

def apply_bilateral_filter_masked(image: np.ndarray, mask: np.ndarray,
                                  d: int, sigmaColor: int, sigmaSpace: int) -> np.ndarray:
    """
    Apply bilateral filter only to masked region.

    This ensures that pixels outside the mask do not influence pixels inside the mask.

    Args:
        image: Input image (grayscale or RGB)
        mask: Boolean mask (True inside region of interest)
        d: Diameter of pixel neighborhood
        sigmaColor: Filter sigma in color space
        sigmaSpace: Filter sigma in coordinate space

    Returns:
        Filtered image with filtering applied only to masked region
    """
    # Apply bilateral filter to entire image
    filtered = cv2.bilateralFilter(image, d=d, sigmaColor=sigmaColor, sigmaSpace=sigmaSpace)

    # Create result starting with original image
    result = image.copy()

    # Copy filtered pixels only from masked region
    if image.ndim == 3:
        for c in range(3):
            result[:, :, c] = np.where(mask, filtered[:, :, c], image[:, :, c])
    else:
        result = np.where(mask, filtered, image)

    return result



def order_centers_clockwise(centers: List[Tuple[float, float]],
                            reference_center: Tuple[float, float]) -> List[int]:
    """
    Order centers in clockwise direction based on angle from reference center.

    Args:
        centers: List of (x, y) tuples
        reference_center: (cx, cy) center point for angle calculation

    Returns:
        List of indices in clockwise order
    """
    if len(centers) < 2:
        return list(range(len(centers)))

    ref_x, ref_y = reference_center

    # Calculate angles from reference center
    angles = []
    for cx, cy in centers:
        angle = np.arctan2(cy - ref_y, cx - ref_x)
        angles.append(angle)

    # Sort by angle (counterclockwise)
    sorted_indices = sorted(range(len(angles)), key=lambda i: angles[i], reverse=False)

    return sorted_indices


def calculate_adjacent_distances(centers: List[Tuple[float, float]],
                                 clockwise_order: List[int]) -> List[float]:
    """
    Calculate distances between adjacent centers in circular arrangement.

    Args:
        centers: List of (x, y) tuples
        clockwise_order: Indices in clockwise order

    Returns:
        List of distances (length = len(centers))
    """
    if len(centers) < 2:
        return []

    distances = []
    n = len(clockwise_order)

    for i in range(n):
        idx1 = clockwise_order[i]
        idx2 = clockwise_order[(i + 1) % n]  # Wrap around

        c1 = centers[idx1]
        c2 = centers[idx2]

        dist = np.sqrt((c2[0] - c1[0])**2 + (c2[1] - c1[1])**2)
        distances.append(dist)

    return distances


def calculate_angle_between_points(p1: Tuple[float, float],
                                   p2: Tuple[float, float],
                                   center: Tuple[float, float]) -> float:
    """
    Calculate angle subtended at center by two points (in degrees).

    Args:
        p1: First point (x, y)
        p2: Second point (x, y)
        center: Center point (cx, cy)

    Returns:
        Angle in degrees (0-360)
    """
    cx, cy = center

    # Calculate angles from center to each point
    angle1 = np.arctan2(p1[1] - cy, p1[0] - cx)
    angle2 = np.arctan2(p2[1] - cy, p2[0] - cx)

    # Calculate difference
    angle_diff = angle2 - angle1

    # Normalize to [0, 2π]
    if angle_diff < 0:
        angle_diff += 2 * np.pi

    # Convert to degrees
    return np.degrees(angle_diff)



def find_best_equidistant_subset_euclidean(
    points: List[Tuple[float, float]],
    n: int,
    center: Tuple[float, float],
    tolerance: float = EQUIDISTANT_DISTANCE_DEVIATION_THRESHOLD
) -> Tuple[Optional[List[int]], float, dict]:
    from itertools import combinations

    if len(points) < n:
        return None, float('inf'), {}

    # Don't skip validation even when len(points) == n
    # We still need to check geometry for squares

    angles = []
    for i, (px, py) in enumerate(points):
        angle = np.arctan2(py - center[1], px - center[0])
        angles.append((angle, i, (px, py)))

    angles.sort()

    best_indices = None
    best_score = float('inf')
    best_diagnostics = {}

    combo_number = 0
    for combo_indices in combinations(range(len(points)), n):
        combo_number += 1

        # Extract selected points maintaining angular sort order
        selected = [angles[i] for i in range(len(angles))
                    if angles[i][1] in combo_indices]
        selected.sort()

        if len(selected) != n:
            continue

        # Print combination being tested
        tested_indices = [angles[i][1] for i in range(len(angles)) if angles[i][1] in combo_indices]
        print(f"\n--- Testing combination #{combo_number}: indices {tested_indices} ---")

        # 1. Check Side Lengths (Distances between adjacent vertices)
        distances = []
        for i in range(n):
            p1 = selected[i][2]
            p2 = selected[(i + 1) % n][2]
            dist = np.sqrt((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2)
            distances.append(dist)

        print(f"  Side lengths: {[f'{d:.2f}' for d in distances]}")

        median_dist = np.median(distances)
        if median_dist == 0:
            print(f"  ✗ REJECTED: Median distance is zero")
            continue

        deviations = [abs(d - median_dist) / median_dist for d in distances]
        max_deviation = max(deviations)

        print(f"  Median distance: {median_dist:.2f}")
        print(f"  Deviations: {[f'{d:.1%}' for d in deviations]}")
        print(f"  Max deviation: {max_deviation:.1%} (threshold: {tolerance:.1%})")

        # Only proceed if side lengths are reasonably uniform
        if max_deviation > tolerance:
            print(f"  ✗ REJECTED: Distance deviation {max_deviation:.1%} > {tolerance:.1%}")
            continue

        print(f"  ✓ Distance check passed")

        # 2. Check Interior Angles
        interior_angles = []
        for i in range(n):
            p_prev = selected[(i - 1) % n][2]
            p_curr = selected[i][2]
            p_next = selected[(i + 1) % n][2]

            v1 = np.array([p_prev[0] - p_curr[0], p_prev[1] - p_curr[1]])
            v2 = np.array([p_next[0] - p_curr[0], p_next[1] - p_curr[1]])

            dot = v1[0] * v2[0] + v1[1] * v2[1]
            det = v1[0] * v2[1] - v1[1] * v2[0]
            angle_rad = np.arctan2(det, dot)

            angle_deg = np.degrees(angle_rad)
            if angle_deg < 0:
                angle_deg += 360

            if angle_deg > 180:
                angle_deg = 360 - angle_deg

            interior_angles.append(angle_deg)

        print(f"  Interior angles: {[f'{a:.1f}°' for a in interior_angles]}")

        min_interior_angle = min(interior_angles)
        max_interior_angle = max(interior_angles)

        print(f"  Min angle: {min_interior_angle:.1f}° (threshold: ≥{MINIMUM_INTERIOR_ANGLE}°)")
        print(f"  Max angle: {max_interior_angle:.1f}°")

        # Check minimum angle requirement
        if min_interior_angle < MINIMUM_INTERIOR_ANGLE:
            print(f"  ✗ REJECTED: Min angle {min_interior_angle:.1f}° < {MINIMUM_INTERIOR_ANGLE}°")
            continue

        print(f"  ✓ Min angle check passed")

        # Special checks for Square (4 holes)
        if n == 4:
            print(f"  [4-hole square checks]")

            if max_interior_angle > (180.0 - MINIMUM_INTERIOR_ANGLE):
                print(f"  ✗ REJECTED: Max angle {max_interior_angle:.1f}° > {180.0-MINIMUM_INTERIOR_ANGLE}° (square must be {MINIMUM_INTERIOR_ANGLE}-{180.0-MINIMUM_INTERIOR_ANGLE}°)")
                continue
            print(f"  ✓ Max angle check passed (≤{MINIMUM_INTERIOR_ANGLE}°)")

            angle_std = np.std(interior_angles)
            print(f"  Angle std dev: {angle_std:.1f}° (threshold: ≤{ALLOWED_STD_DEVIATION_IN_ANGLES}°)")
            if angle_std > ALLOWED_STD_DEVIATION_IN_ANGLES:
                print(f"  ✗ REJECTED: Angle std {angle_std:.1f}° > {ALLOWED_STD_DEVIATION_IN_ANGLES}°")
                continue
            print(f"  ✓ Angle uniformity check passed")

            mean_angle = np.mean(interior_angles)
            print(f"  Mean angle: {mean_angle:.1f}° (target: 90° ± {ALLOWED_STD_DEVIATION_IN_ANGLES}°)")
            if abs(mean_angle - 90.0) > ALLOWED_STD_DEVIATION_IN_ANGLES:
                print(f"  ✗ REJECTED: Mean angle {mean_angle:.1f}° too far from 90°")
                continue
            print(f"  ✓ Mean angle check passed")

        # 3. Check Angular Spacing relative to Center
        angles_between = []
        for i in range(n):
            a1 = selected[i][0]
            a2 = selected[(i + 1) % n][0]

            angle_diff = a2 - a1
            if angle_diff < 0:
                angle_diff += 2 * np.pi

            angles_between.append(np.degrees(angle_diff))

        expected_angle = 360.0 / n
        angle_deviations = [abs(a - expected_angle) / expected_angle
                            for a in angles_between]
        max_angle_deviation = max(angle_deviations)

        print(f"  Angular spacing from center: {[f'{a:.1f}°' for a in angles_between]}")
        print(f"  Expected: {expected_angle:.1f}° each")
        print(f"  Max deviation: {max_angle_deviation:.1%}")

        # Calculate score
        score = (0.7 * max_deviation + 0.3 * max_angle_deviation)
        print(f"  Combined score: {score:.4f} (lower is better)")

        if score < best_score:
            print(f"  ★ NEW BEST! (previous best: {best_score:.4f})")
            best_score = score
            best_indices = [angles[i][1] for i in range(len(angles))
                            if angles[i][1] in combo_indices]

            best_diagnostics = {
                'distances': distances,
                'median_distance': median_dist,
                'max_distance_deviation': max_deviation,
                'angles': angles_between,
                'interior_angles': interior_angles,
                'min_interior_angle': min_interior_angle,
                'expected_angle': expected_angle,
                'max_angle_deviation': max_angle_deviation,
                'score': score
            }
        else:
            print(f"  Score not better than current best ({best_score:.4f})")

    print(f"\n{'=' * 70}")
    if best_indices is None:
        print(f"FINAL RESULT: No valid combination found after testing {combo_number} combinations")
    else:
        print(f"FINAL RESULT: Best combination found with score {best_score:.4f}")
        print(f"  Best indices: {best_indices}")

    return best_indices, best_score, best_diagnostics


class MobileSAMv2AutomaticMaskGenerator:
    """
    MobileSAMv2 Automatic Mask Generator.

    Uses YOLOv8 (ObjectAwareModel) for object detection to get bounding boxes,
    then MobileSAM sam_predictor to generate masks for each detected object.
    """

    def __init__(
            self,
            sam_predictor,
            yolo_model,
            conf_threshold: float = 0.3,
            iou_threshold: float = 0.7,
            min_mask_region_area: int = 100,
            max_mask_region_area: int = 2100,
            use_grid_points: bool = True,
            points_per_side: int = 32,
    ):
        """
        Args:
            sam_predictor: MobileSAM SamPredictor instance
            yolo_model: Loaded YOLO model for object detection
            conf_threshold: Confidence threshold for YOLO detections
            iou_threshold: IoU threshold for NMS
            min_mask_region_area: Minimum mask area to keep
            max_mask_region_area: Maximum mask area to keep
            use_grid_points: If True, also use grid points for objects not detected by YOLO
            points_per_side: Number of points per side for grid-based prompting
        """
        self.predictor = sam_predictor
        self.yolo_model = yolo_model
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.min_mask_region_area = min_mask_region_area
        self.max_mask_region_area = max_mask_region_area
        self.use_grid_points = use_grid_points
        self.points_per_side = points_per_side

    def generate(self, image: np.ndarray) -> List[Dict[str, Any]]:
        """
        Generate masks for all objects in the image.

        Args:
            image: RGB image as numpy array (H, W, 3)

        Returns:
            List of mask dictionaries with 'segmentation', 'area', 'bbox', etc.
        """
        masks = []

        # Set image for SAM sam_predictor
        self.predictor.set_image(image)

        # Run YOLO object detection
        try:
            results = self.yolo_model.predict(
                image,
                conf=self.conf_threshold,
                iou=self.iou_threshold,
                verbose=False
            )
        except AttributeError as e:
            # Catch known issue with MobileSAM's ObjectAwareModel vs newer Ultralytics
            if "'Segment' object has no attribute 'proto'" in str(e):
                error_msg = (
                    "YOLO Compatibility Error:\n"
                    "The loaded ObjectAwareModel.pt seems incompatible with this version of 'ultralytics'.\n"
                    "Attempting to force detection mode failed.\n\n"
                    "Fix:\n"
                    "Try downgrading ultralytics: pip install ultralytics==8.0.200"
                )
                print(error_msg)
                raise RuntimeError(error_msg) from e
            raise e

        # Get bounding boxes from YOLO
        boxes = []
        if len(results) > 0 and results[0].boxes is not None:
            boxes = results[0].boxes.xyxy.cpu().numpy()  # [x1, y1, x2, y2]

        print(f"[YOLO] Detected {len(boxes)} boxes")
        # Generate masks using box prompts
        if len(boxes) > 0:
            for box in boxes:
                try:
                    mask_data = self._generate_mask_from_box(image, box)
                    if mask_data is not None:
                        area = mask_data['area']
                        if self.min_mask_region_area <= area <= self.max_mask_region_area:
                            masks.append(mask_data)
                except Exception as e:
                    print(f"Warning: Failed to generate mask for box {box}: {e}")

        # Optionally add grid-based point prompts for areas not covered
        if self.use_grid_points and len(masks) < 5:
            grid_masks = self._generate_grid_masks(image)
            # Filter out grid masks that overlap significantly with existing masks
            for gm in grid_masks:
                if not self._overlaps_existing(gm['segmentation'], masks):
                    area = gm['area']
                    if self.min_mask_region_area <= area <= self.max_mask_region_area:
                        masks.append(gm)

        return masks

    def _generate_mask_from_box(self, image: np.ndarray, box: np.ndarray) -> Optional[Dict[str, Any]]:
        """Generate a single mask from a bounding box."""
        x1, y1, x2, y2 = box

        # Use SAM sam_predictor
        masks, scores, _ = self.predictor.predict(
            point_coords=None,
            point_labels=None,
            box=np.array([x1, y1, x2, y2]),
            multimask_output=False
        )

        if masks is None or len(masks) == 0:
            return None

        mask = masks[0]
        score = scores[0] if scores is not None else 0.0
        area = int(mask.sum())

        return {
            'segmentation': mask,
            'area': area,
            'bbox': [int(x1), int(y1), int(x2 - x1), int(y2 - y1)],
            'predicted_iou': float(score),
            'stability_score': float(score),
        }

    def _generate_grid_masks(self, image: np.ndarray) -> List[Dict[str, Any]]:
        """Generate masks using grid point prompts."""
        masks = []
        h, w = image.shape[:2]

        # Create grid of points
        points_x = np.linspace(0, w, self.points_per_side + 2)[1:-1]
        points_y = np.linspace(0, h, self.points_per_side + 2)[1:-1]

        # Sample a subset of grid points
        step = max(1, self.points_per_side // 8)
        for i, y in enumerate(points_y[::step]):
            for j, x in enumerate(points_x[::step]):
                try:
                    mask_pred, scores, _ = self.predictor.predict(
                        point_coords=np.array([[x, y]]),
                        point_labels=np.array([1]),  # Foreground point
                        multimask_output=True
                    )

                    if mask_pred is not None and len(mask_pred) > 0:
                        # Take the mask with highest score
                        best_idx = np.argmax(scores)
                        mask = mask_pred[best_idx]
                        score = scores[best_idx]
                        area = int(mask.sum())

                        if self.min_mask_region_area <= area <= self.max_mask_region_area:
                            masks.append({
                                'segmentation': mask,
                                'area': area,
                                'bbox': self._mask_to_bbox(mask),
                                'predicted_iou': float(score),
                                'stability_score': float(score),
                            })
                except Exception:
                    continue

        return masks

    def _mask_to_bbox(self, mask: np.ndarray) -> List[int]:
        """Convert binary mask to bounding box [x, y, w, h]."""
        rows = np.any(mask, axis=1)
        cols = np.any(mask, axis=0)
        if not rows.any() or not cols.any():
            return [0, 0, 0, 0]
        y1, y2 = np.where(rows)[0][[0, -1]]
        x1, x2 = np.where(cols)[0][[0, -1]]
        return [int(x1), int(y1), int(x2 - x1), int(y2 - y1)]

    def _overlaps_existing(self, new_mask: np.ndarray, existing_masks: List[Dict]) -> bool:
        """Check if new mask significantly overlaps with existing masks."""
        new_area = new_mask.sum()
        if new_area == 0:
            return True

        for m in existing_masks:
            existing_mask = m['segmentation']
            intersection = (new_mask & existing_mask).sum()
            iou = intersection / (new_area + existing_mask.sum() - intersection + 1e-6)
            if iou > 0.5:
                return True

        return False


class BunkSegmenter:
    """
    Bung hole detector using MobileSAMv2 with morphological post-processing.
    """

    def __init__(self, mobile_sam_path: str | None , yolo_path: str | None ):
        """
        Initialize segmenter using shared ModelManager.

        Args:
            mobile_sam_path: Ignored (kept for backward compatibility)
            yolo_path: Ignored (kept for backward compatibility)
        """
        # Get shared model manager
        self.model_manager = ModelManager.get_instance()
        self.device = self.model_manager.get_device()
        # self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        print(f"[BunkSegmenter] Using device: {self.device}")

        # # Load models
        # self._load_models(mobile_sam_path, yolo_path)

        # Get models from manager (loads if needed)
        self.sam_predictor = self.model_manager.get_sam_predictor()
        self.yolo_model = self.model_manager.get_yolo_model()

        self.conf_thresh = 0.3
        self.iou_thresh = 0.7
        self.min_area = 100
        self.max_area = 2100
        self.use_grid = True

        # Create mask generator
        self.mask_generator = MobileSAMv2AutomaticMaskGenerator(
            sam_predictor=self.sam_predictor,
            yolo_model=self.yolo_model,
            conf_threshold=self.conf_thresh,
            iou_threshold=self.iou_thresh,
            min_mask_region_area=self.min_area,
            max_mask_region_area=self.max_area,
            use_grid_points=self.use_grid,
            points_per_side=32
        )

        # Precompute gamma LUTs
        all_gamma_values = set(BRIGHT_GAMMA_VALUES + DARK_GAMMA_VALUES)
        self.gamma_luts = {}
        for gamma in all_gamma_values:
            inv_gamma = 1.0 / gamma
            self.gamma_luts[gamma] = np.array([
                ((i / 255.0) ** inv_gamma) * 255
                for i in range(256)
            ]).astype(np.uint8)

        print(f"[BunkSegmenter] Precomputed {len(self.gamma_luts)} gamma LUTs")

    def _determine_gamma_values(self, image: np.ndarray) -> List[float]:
        """
        Determine which gamma values to use based on image brightness.

        Args:
            image: Input RGB image

        Returns:
            List of gamma values to try
        """
        brightness = np.mean(image)

        if brightness > BRIGHTNESS_THRESHOLD:
            gamma_values = BRIGHT_GAMMA_VALUES
            print(f"[BunkSegmenter] Brightness={brightness:.1f} > {BRIGHTNESS_THRESHOLD} → using bright gammas")
        else:
            gamma_values = DARK_GAMMA_VALUES
            print(f"[BunkSegmenter] Brightness={brightness:.1f} <= {BRIGHTNESS_THRESHOLD} → using dark gammas")

        return gamma_values

    # =============================================================================
    # FILTER 1: Distance from Center
    # =============================================================================
    # Location: BunkSegmenter.py, method filter_by_distance_from_center (line ~630)
    # Replace the entire method with this instrumented version:

    def _rebuild_mask_generator(self):
        """Rebuild mask generator with current parameters (exact as in original)."""
        self.mask_generator = MobileSAMv2AutomaticMaskGenerator(
            sam_predictor=self.sam_predictor,
            yolo_model=self.yolo_model,
            conf_threshold=self.conf_thresh,
            iou_threshold=self.iou_thresh,
            min_mask_region_area=self.min_area,
            max_mask_region_area=self.max_area,
            use_grid_points=self.use_grid,
            points_per_side=32,
        )


    def filter_by_distance_from_center(self, components: List[Tuple[Tuple[float, float], np.ndarray]]) -> List[
        Tuple[Tuple[float, float], np.ndarray]]:
        """
        Filter components by distance from bung center.
        Keep components whose distance from center is within 15% of average distance.
        """
        if len(components) < 2:
            print(f"[FILTER: Distance] Skipping - only {len(components)} component(s)")
            return components

        print(f"\n{'=' * 70}")
        print(f"FILTER 1: DISTANCE FROM CENTER")
        print(f"{'=' * 70}")
        print(f"Input: {len(components)} components")

        # Reference center (in annular coordinates)
        reference_center = (BUNG_RADIUS, BUNG_RADIUS)
        print(f"Reference center: {reference_center}")

        # Extract centers
        centers = [center for center, contour in components]
        print(f"\nComponent centers:")
        for i, (cx, cy) in enumerate(centers):
            print(f"  Component {i}: ({cx:.1f}, {cy:.1f})")

        # Calculate distances from reference center
        distances = []
        for i, (cx, cy) in enumerate(centers):
            dist = np.sqrt((cx - reference_center[0]) ** 2 + (cy - reference_center[1]) ** 2)
            distances.append(dist)
            print(f"  Distance {i}: {dist:.2f}")

        # Calculate average and max deviation
        avg_distance = np.mean(distances)
        max_deviation = avg_distance * 0.15  # 15%

        lower_bound = avg_distance - max_deviation
        upper_bound = avg_distance + max_deviation

        print(f"\nStatistics:")
        print(f"  Average distance: {avg_distance:.2f}")
        print(f"  Max deviation (15%): {max_deviation:.2f}")
        print(f"  Valid range: [{lower_bound:.2f}, {upper_bound:.2f}]")

        # Filter components within range
        filtered = []
        kept_indices = []
        dropped_indices = []

        print(f"\nFiltering:")
        for i, (center, contour) in enumerate(components):
            dist = distances[i]
            deviation = abs(dist - avg_distance)
            within_range = deviation <= max_deviation

            if within_range:
                filtered.append((center, contour))
                kept_indices.append(i)
                print(f"  ✓ Component {i}: dist={dist:.2f}, deviation={deviation:.2f} <= {max_deviation:.2f} → KEEP")
            else:
                dropped_indices.append(i)
                print(f"  ✗ Component {i}: dist={dist:.2f}, deviation={deviation:.2f} > {max_deviation:.2f} → DROP")

        print(f"\nResult:")
        print(f"  Kept: {len(filtered)} components {kept_indices}")
        print(f"  Dropped: {len(dropped_indices)} components {dropped_indices}")
        print(f"{'=' * 70}\n")

        return filtered

    # =============================================================================
    # FILTER 2: Angle Subtended (4 holes - 90° pairs)
    # =============================================================================
    # Location: BunkSegmenter.py, method filter_by_angle_subtended_to_center_4_holes (line ~670)
    # Replace the entire method with this instrumented version:

    def filter_by_angle_subtended_to_center_4_holes(self, components: List[Tuple[Tuple[float, float], np.ndarray]]) -> \
    Tuple[int, List[Tuple[Tuple[float, float], np.ndarray]]]:
        """
        Filter components by checking if angles subtended at center are ~90° (for 4 holes).
        """
        if len(components) < 2:
            print(f"[FILTER: Angle 4-holes] Skipping - only {len(components)} component(s)")
            return len(components), components

        print(f"\n{'=' * 70}")
        print(f"FILTER 2: ANGLE SUBTENDED (4 HOLES - 90° pairs)")
        print(f"{'=' * 70}")
        print(f"Input: {len(components)} components")

        # Extract centers
        centers = [center for center, contour in components]
        print(f"\nComponent centers:")
        for i, (cx, cy) in enumerate(centers):
            print(f"  Component {i}: ({cx:.1f}, {cy:.1f})")

        # Order clockwise around BUNG_CENTER (in annular coordinates)
        reference_center = (BUNG_RADIUS, BUNG_RADIUS)
        print(f"\nReference center: {reference_center}")

        # Calculate angles for ordering
        angles = []
        for i, (cx, cy) in enumerate(centers):
            angle = np.arctan2(cy - reference_center[1], cx - reference_center[0])
            angles.append(np.degrees(angle))
            print(f"  Component {i} angle from center: {np.degrees(angle):.1f}°")

        clockwise_order = order_centers_clockwise(centers, reference_center)
        print(f"\nClockwise order: {clockwise_order}")
        print(f"Ordered components:")
        for pos, idx in enumerate(clockwise_order):
            print(f"  Position {pos}: Component {idx} at ({centers[idx][0]:.1f}, {centers[idx][1]:.1f})")

        # Form pairs of adjacent components and check angles
        confirmed_indices = set()
        n = len(clockwise_order)

        print(f"\nChecking adjacent pairs (looking for 90° ± 10°):")
        for i in range(n):
            idx1 = clockwise_order[i]
            idx2 = clockwise_order[(i + 1) % n]  # Wrap around

            p1 = centers[idx1]
            p2 = centers[idx2]

            angle = calculate_angle_between_points(p1, p2, reference_center)

            # Check if angle is approximately 90° (±10°)
            is_valid = 80 <= angle <= 100
            status = "✓ VALID" if is_valid else "✗ INVALID"

            print(f"  Pair ({idx1} → {idx2}): angle = {angle:.1f}° [{status}]")
            print(f"    Point 1: ({p1[0]:.1f}, {p1[1]:.1f})")
            print(f"    Point 2: ({p2[0]:.1f}, {p2[1]:.1f})")
            print(f"    Valid range: [80°, 100°]")

            if is_valid:
                confirmed_indices.add(idx1)
                confirmed_indices.add(idx2)
                print(f"    → Added components {idx1} and {idx2} to confirmed set")
            else:
                print(f"    → Angle {angle:.1f}° outside valid range")

        # Build filtered list
        filtered = [components[i] for i in sorted(confirmed_indices)]
        dropped_indices = [i for i in range(len(components)) if i not in confirmed_indices]

        print(f"\nResult:")
        print(f"  Confirmed indices: {sorted(confirmed_indices)}")
        print(f"  Dropped indices: {dropped_indices}")
        print(f"  Kept: {len(filtered)} components")
        print(f"{'=' * 70}\n")

        return len(filtered), filtered

    # =============================================================================
    # FILTER 3: Distance Between Pairs (4 holes)
    # =============================================================================
    # Location: BunkSegmenter.py, method filter_by_distance_between_pairs_4_holes (line ~723)
    # Replace the entire method with this instrumented version:

    def filter_by_distance_between_pairs_4_holes(self, components: List[Tuple[Tuple[float, float], np.ndarray]]) -> \
    Tuple[int, List[Tuple[Tuple[float, float], np.ndarray]]]:
        """
        Filter components by checking if distances between adjacent pairs are uniform.
        """
        if len(components) < 3:
            print(f"[FILTER: Pair Distance] Skipping - only {len(components)} component(s)")
            return len(components), components

        print(f"\n{'=' * 70}")
        print(f"FILTER 3: DISTANCE BETWEEN PAIRS (4 HOLES)")
        print(f"{'=' * 70}")
        print(f"Input: {len(components)} components")

        # Extract centers
        centers = [center for center, contour in components]
        print(f"\nComponent centers:")
        for i, (cx, cy) in enumerate(centers):
            print(f"  Component {i}: ({cx:.1f}, {cy:.1f})")

        # Order clockwise
        reference_center = (BUNG_RADIUS, BUNG_RADIUS)
        clockwise_order = order_centers_clockwise(centers, reference_center)

        print(f"\nClockwise order: {clockwise_order}")

        # Calculate adjacent distances
        distances = calculate_adjacent_distances(centers, clockwise_order)

        if not distances:
            print(f"No distances calculated")
            print(f"{'=' * 70}\n")
            return len(components), components

        print(f"\nAdjacent distances (between consecutive components):")
        for i, dist in enumerate(distances):
            idx1 = clockwise_order[i]
            idx2 = clockwise_order[(i + 1) % len(clockwise_order)]
            print(f"  Distance {i} (Component {idx1} → {idx2}): {dist:.2f}")

        # Calculate median and deviation threshold
        median_dist = np.median(distances)
        max_deviation = 0.15  # 15%

        lower_bound = median_dist * (1 - max_deviation)
        upper_bound = median_dist * (1 + max_deviation)

        print(f"\nStatistics:")
        print(f"  Median distance: {median_dist:.2f}")
        print(f"  Max deviation: {max_deviation * 100:.0f}%")
        print(f"  Valid range: [{lower_bound:.2f}, {upper_bound:.2f}]")

        # Identify pairs that are within tolerance
        valid_pairs = set()

        print(f"\nChecking pairs:")
        for i in range(len(distances)):
            dist = distances[i]
            idx1 = clockwise_order[i]
            idx2 = clockwise_order[(i + 1) % len(clockwise_order)]

            is_valid = lower_bound <= dist <= upper_bound
            status = "✓ VALID" if is_valid else "✗ INVALID"

            print(f"  Pair ({idx1} → {idx2}): dist = {dist:.2f} [{status}]")
            print(f"    Valid range: [{lower_bound:.2f}, {upper_bound:.2f}]")

            if is_valid:
                valid_pairs.add(idx1)
                valid_pairs.add(idx2)
                print(f"    → Added components {idx1} and {idx2} to valid set")
            else:
                deviation = min(abs(dist - lower_bound), abs(dist - upper_bound))
                print(f"    → Distance off by {deviation:.2f}")

        # Build filtered list
        filtered = [components[i] for i in sorted(valid_pairs)]
        dropped_indices = [i for i in range(len(components)) if i not in valid_pairs]

        print(f"\nResult:")
        print(f"  Valid indices: {sorted(valid_pairs)}")
        print(f"  Dropped indices: {dropped_indices}")
        print(f"  Kept: {len(filtered)} components")
        print(f"{'=' * 70}\n")

        return len(filtered), filtered

    # =============================================================================
    # FILTER 4: Angle Subtended (2 holes - 180° pairs)
    # =============================================================================
    # Location: BunkSegmenter.py, method filter_by_angle_subtended_to_center_2_holes (line ~782)
    # Replace the entire method with this instrumented version:

    def filter_by_angle_subtended_to_center_2_holes(self, components: List[Tuple[Tuple[float, float], np.ndarray]]) -> \
    Tuple[int, List[Tuple[Tuple[float, float], np.ndarray]]]:
        """
        Filter components by checking if angles subtended at center are ~180° (for 2 holes).
        """
        if len(components) < 2:
            print(f"[FILTER: Angle 2-holes] Skipping - only {len(components)} component(s)")
            return len(components), components

        print(f"\n{'=' * 70}")
        print(f"FILTER 4: ANGLE SUBTENDED (2 HOLES - 180° pairs)")
        print(f"{'=' * 70}")
        print(f"Input: {len(components)} components")

        # Extract centers
        centers = [center for center, contour in components]
        print(f"\nComponent centers:")
        for i, (cx, cy) in enumerate(centers):
            print(f"  Component {i}: ({cx:.1f}, {cy:.1f})")

        # Order clockwise around BUNG_CENTER
        reference_center = (BUNG_RADIUS, BUNG_RADIUS)
        print(f"\nReference center: {reference_center}")

        clockwise_order = order_centers_clockwise(centers, reference_center)
        print(f"Clockwise order: {clockwise_order}")

        # Form pairs of adjacent components and check angles
        confirmed_indices = set()
        n = len(clockwise_order)

        print(f"\nChecking adjacent pairs (looking for 180° ± 15°):")
        for i in range(n):
            idx1 = clockwise_order[i]
            idx2 = clockwise_order[(i + 1) % n]  # Wrap around

            p1 = centers[idx1]
            p2 = centers[idx2]

            angle = calculate_angle_between_points(p1, p2, reference_center)

            # Check if angle is approximately 180° (±15°)
            is_valid = 165 <= angle <= 195
            status = "✓ VALID" if is_valid else "✗ INVALID"

            print(f"  Pair ({idx1} → {idx2}): angle = {angle:.1f}° [{status}]")
            print(f"    Point 1: ({p1[0]:.1f}, {p1[1]:.1f})")
            print(f"    Point 2: ({p2[0]:.1f}, {p2[1]:.1f})")
            print(f"    Valid range: [165°, 195°]")

            if is_valid:
                confirmed_indices.add(idx1)
                confirmed_indices.add(idx2)
                print(f"    → Added components {idx1} and {idx2} to confirmed set")
            else:
                deviation = min(abs(angle - 165), abs(angle - 195))
                print(f"    → Angle off by {deviation:.1f}°")

        # Build filtered list
        filtered = [components[i] for i in sorted(confirmed_indices)]
        dropped_indices = [i for i in range(len(components)) if i not in confirmed_indices]

        print(f"\nResult:")
        print(f"  Confirmed indices: {sorted(confirmed_indices)}")
        print(f"  Dropped indices: {dropped_indices}")
        print(f"  Kept: {len(filtered)} components")
        print(f"{'=' * 70}\n")

        return len(filtered), filtered

    def filter_by_best_equidistant_subset(
            self,
            components: List[Tuple[Tuple[float, float], np.ndarray]],
            expected_count: int
    ) -> Tuple[Union[int, bool], List[Tuple[Tuple[float, float], np.ndarray]]]:
        print(f"\n{'=' * 70}")
        print(f"FILTER: BEST EQUIDISTANT SUBSET")
        print(f"{'=' * 70}")
        print(f"Input: {len(components)} components")
        print(f"Target: {expected_count} components")

        # CRITICAL: For 4-hole patterns, ALWAYS validate geometry even if count matches
        # We need to ensure it's a proper square, not just 4 random points
        if len(components) < expected_count:
            print(f"Not enough components ({len(components)} < {expected_count})")
            print(f"{'=' * 70}\n")
            return False, components

        # If we have more components than needed, or if we need to validate
        # geometry (e.g., for 4-hole square), continue to geometry check
        if len(components) > expected_count:
            print(f"More components than needed, selecting best {expected_count}")
        else:
            print(f"Count matches, but validating geometry...")

        centers = [center for center, contour in components]
        reference_center = (BUNG_RADIUS, BUNG_RADIUS)

        best_indices, best_score, diagnostics = find_best_equidistant_subset_euclidean(
            centers,
            expected_count,
            reference_center,
            tolerance=EQUIDISTANT_DISTANCE_DEVIATION_THRESHOLD
        )

        if best_indices is None:
            print(f"\n❌ GEOMETRY CHECK FAILED!")
            print(f"Reason: No valid subset found meeting all criteria:")
            print(f"  - Distance uniformity: ≤10% deviation")
            print(f"  - Interior angles: all ≥70°")
            if expected_count == 4:
                print(f"  - Square constraints: angles 70-110°, std ≤15°, mean ≈90°")
            print(f"{'=' * 70}\n")
            return False, components

        print(f"\n✓ GEOMETRY CHECK PASSED!")
        print(f"\nBest subset found:")
        print(f"  Indices: {best_indices}")
        print(f"  Score: {best_score:.4f}")

        # Print diagnostics only if available
        if diagnostics:
            if 'distances' in diagnostics:
                print(f"  Distances: {[f'{d:.1f}' for d in diagnostics['distances']]}")
                print(f"  Median distance: {diagnostics['median_distance']:.1f}")
                print(f"  Max distance deviation: {diagnostics['max_distance_deviation']:.1%}")

            if 'interior_angles' in diagnostics:
                print(f"  Interior angles: {[f'{a:.1f}°' for a in diagnostics['interior_angles']]}")
                print(f"  Min interior angle: {diagnostics['min_interior_angle']:.1f}°")
                angles_std = np.std(diagnostics['interior_angles'])
                angles_mean = np.mean(diagnostics['interior_angles'])
                print(f"  Interior angles std: {angles_std:.1f}°")
                print(f"  Interior angles mean: {angles_mean:.1f}°")

            if 'angles' in diagnostics:
                print(f"  Angles from center: {[f'{a:.1f}°' for a in diagnostics['angles']]}")
                print(f"  Expected angle: {diagnostics['expected_angle']:.1f}°")
                print(f"  Max angle deviation: {diagnostics['max_angle_deviation']:.1%}")
        else:
            print(f"  (No detailed diagnostics available)")

        filtered = [components[i] for i in best_indices]
        dropped = [i for i in range(len(components)) if i not in best_indices]

        print(f"\nResult:")
        print(f"  ✓ VALIDATED: {len(filtered)} components pass all geometry checks")
        print(f"  Kept: {best_indices}")
        print(f"  Dropped: {dropped}")
        print(f"{'=' * 70}\n")

        return len(filtered), filtered

    def _preprocess_image(self, image: np.ndarray, gamma: float) -> np.ndarray:
        """
        Apply preprocessing pipeline to image.

        Steps:
        1. Extract annular region
        2. Background normalization
        3. Gamma correction
        4. Bilateral filter (masked)
        5. Contrast normalization + RGB→Gray
        6. Final bilateral filter (masked)
        7. Gray→RGB conversion

        Args:
            image: Input RGB image
            gamma: Gamma correction value

        Returns:
            Preprocessed RGB image ready for SAM
        """
        # Step 1: Extract annular region
        annular_image, annular_mask = extract_annular_region(
            image,
            center=BUNG_CENTER,
            outer_radius=BUNG_RADIUS,
            inner_radius=BUNG_INNER_RADIUS,
            fill_color=0
        )

        # Step 2: Background normalization
        bg_normalized = pixBackgroundNorm_masked(
            annular_image, annular_mask, sx=10, sy=10
        )

        # Step 3: Bilateral filter (masked) - FIRST
        bilateral1 = apply_bilateral_filter_masked(
            bg_normalized, annular_mask, d=21, sigmaColor=30, sigmaSpace=30
        )

        # Step 4: Gamma correction - SECOND
        gamma_lut = self.gamma_luts[gamma]
        gamma_corrected = gamma_lut[bilateral1]

        # Step 5: Contrast normalization (with RGB→Gray conversion)
        gray_image = cv2.cvtColor(gamma_corrected, cv2.COLOR_RGB2GRAY) if gamma_corrected.ndim == 3 else gamma_corrected
        contrast_normalized = pixContrastNorm_masked(gray_image, annular_mask, sx=15, sy=15)

        # Step 6: Final bilateral filter (masked)
        bilateral2 = apply_bilateral_filter_masked(
            contrast_normalized, annular_mask, d=15, sigmaColor=30, sigmaSpace=40
        )

        # Step 7: Gray→RGB conversion for SAM
        rgb_image = cv2.cvtColor(bilateral2, cv2.COLOR_GRAY2RGB)

        return rgb_image

    def _post_process_masks(self, masks: List[Dict[str, Any]],
                            image_shape: Tuple[int, int],
                            expected_count: Optional[int] = None,
                            model_type: Optional[str] = None) -> Tuple[int, List[Tuple[Tuple[float, float], np.ndarray]]]:
        """
        Post-process SAM masks using morphological operations.

        Steps:
        a) Create white image, draw masks in black
        b) Overlapping masks join together
        c) Crop from center with radius-1, paste into square with padding
        d) Apply morphological opening
        e) Find connected components
        f) Filter components by aspect ratio, area, and area ratio
        g) Return count and list of (center, contour) tuples

        Args:
            masks: List of mask dictionaries from SAM
            image_shape: Shape of the annular region (H, W)
            expected_count: Expected number of holes (for cascade filtering)
            model_type: Model type ("DOST" or "DOSTPLUS") for cascade filtering
            masks: List of mask dictionaries from SAM
            image_shape: Shape of the annular region (H, W)
            expected_count: Expected number of holes (for cascade filtering)
            model_type: Model type ("DOST" or "DOSTPLUS") for cascade filtering

        Returns:
            Tuple of (count, list of (center, contour) tuples)
        """
        h, w = image_shape

        # Step a & b: Create white image, draw masks in black (overlapping joins)
        white_image = np.ones((h, w), dtype=np.uint8) * 255

        for mask_data in masks:
            mask = mask_data['segmentation']
            # Draw mask in black
            white_image[mask > 0] = 0

        # Step c: Crop from center with radius-1, paste into square
        crop_radius = BUNG_RADIUS - CROP_MARGIN

        # Create square white background
        square_image = np.ones((SQUARE_SIZE, SQUARE_SIZE), dtype=np.uint8) * 255

        # Create circular mask for cropping
        center_x, center_y = BUNG_RADIUS, BUNG_RADIUS  # Center in annular coordinate system
        crop_mask = np.zeros((h, w), dtype=np.uint8)
        cv2.circle(crop_mask, (center_x, center_y), crop_radius, 255, -1)

        # Apply crop mask
        cropped = white_image.copy()
        cropped[crop_mask == 0] = 255  # Set outside to white

        # Extract the circular region
        x1 = max(0, center_x - crop_radius)
        y1 = max(0, center_y - crop_radius)
        x2 = min(w, center_x + crop_radius)
        y2 = min(h, center_y + crop_radius)

        cropped_region = cropped[y1:y2, x1:x2]

        # Paste into center of square
        paste_x = (SQUARE_SIZE - cropped_region.shape[1]) // 2
        paste_y = (SQUARE_SIZE - cropped_region.shape[0]) // 2
        square_image[paste_y:paste_y + cropped_region.shape[0],
        paste_x:paste_x + cropped_region.shape[1]] = cropped_region

        # Step d: Apply morphological opening
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (MORPH_KERNEL_SIZE, MORPH_KERNEL_SIZE))
        opened = cv2.morphologyEx(square_image, cv2.MORPH_OPEN, kernel)

        # Invert for connected components (components should be white on black)
        inverted = cv2.bitwise_not(opened)

        # Step e: Find connected components
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(inverted, connectivity=8)

        # Step f: Filter components
        valid_components = []

        for i in range(1, num_labels):  # Skip background (label 0)
            x, y, w_comp, h_comp, area = stats[i]

            # Filter 1: Aspect ratio
            smaller_dim = min(w_comp, h_comp)
            larger_dim = max(w_comp, h_comp)

            if larger_dim == 0:
                continue

            aspect_ratio = smaller_dim / larger_dim

            if aspect_ratio < ASPECT_RATIO_THRESHOLD:
                continue

            # Filter 2: Minimum area
            if area < MIN_COMPONENT_AREA:
                continue

            # Filter 3: Area ratio
            bbox_area = w_comp * h_comp
            if bbox_area == 0:
                continue

            area_ratio = area / bbox_area

            if area_ratio < AREA_RATIO_THRESHOLD:
                continue

            # Component passed all filters
            # Extract contour
            component_mask = (labels == i).astype(np.uint8) * 255
            contours, _ = cv2.findContours(component_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            if len(contours) > 0:
                contour = max(contours, key=cv2.contourArea)  # Take largest contour

                # Calculate center (in square coordinate system)
                M = cv2.moments(contour)
                if M['m00'] != 0:
                    cx = M['m10'] / M['m00']
                    cy = M['m01'] / M['m00']
                else:
                    cx, cy = centroids[i]

                # Transform back to annular coordinate system
                # Account for paste offset and crop offset
                annular_cx = cx - paste_x + x1
                annular_cy = cy - paste_y + y1

                # Transform contour back to annular coordinates
                annular_contour = contour.copy()
                annular_contour[:, :, 0] = annular_contour[:, :, 0] - paste_x + x1
                annular_contour[:, :, 1] = annular_contour[:, :, 1] - paste_y + y1

                valid_components.append(((annular_cx, annular_cy), annular_contour))

        # Step g: Return count and list
        count = len(valid_components)

        print(f"[BunkSegmenter] Post-processing: {len(masks)} SAM masks → {count} valid components")

        # ===== DEBUG: CASCADE START =====
        print(f"\n{'#'*70}")
        print(f"CASCADE FILTERING START")
        print(f"{'#'*70}")
        print(f"Model type: {model_type}")
        print(f"Expected count: {expected_count}")
        print(f"Current count: {count}")
        print(f"Cascade will be triggered: {expected_count is not None and count != expected_count}")
        print(f"{'#'*70}\n")
        # ================================

        # ===== CASCADE FILTERING =====
        # Note: We now allow entry even if count == expected_count to verify geometry for 4-hole patterns
        if expected_count is not None:
            # We always check pattern quality if count >= 4, even if it matches expected count

            found_definitive_pattern = False

            # FIRST: Check if this is a 4-hole square pattern (strict geometry)
            # We run this if we have at least 4 components, regardless of expectation,
            # to validate or find the best 4.
            if count >= 4:
                print(f"[BunkSegmenter] Checking for 4-hole square pattern (strict geometry)...")
                test_count, test_components = self.filter_by_best_equidistant_subset(valid_components, 4)
                print(f"[BunkSegmenter] 4-hole check returned: count={test_count}")

                if test_count == 4:
                    # Found valid 4-hole square pattern
                    count, valid_components = 4, test_components
                    found_definitive_pattern = True
                    if expected_count == 4:
                        print(f"[BunkSegmenter] ✓ Found expected 4-hole square pattern!")
                    else:
                        print(f"[BunkSegmenter] ✗ Found 4-hole square pattern but expected {expected_count} holes")
                        print(f"[BunkSegmenter] Stopping cascade - definitive geometry detected")
                else:
                    # Strict 4-hole geometry check FAILED
                    # This means NO valid 4-point square exists in this image
                    print(f"[BunkSegmenter] Strict 4-hole check failed (irregular shape).")

                    # CRITICAL: If we're checking for DOST (4 holes), the fact that
                    # no valid square exists from ANY combination means definitive failure
                    if expected_count == 4:
                        print(f"[BunkSegmenter] REJECTING: No valid 4-hole square found (geometry irregular).")
                        count = 0
                        valid_components = []
                        found_definitive_pattern = True  # Stop fallbacks

                    # CRITICAL: If we're checking for DOSTPLUS (2 holes), the fact that
                    # we have 4+ components but they don't form a square means irregular DOST
                    elif expected_count == 2:
                        print(
                            f"[BunkSegmenter] REJECTING: Found {count} irregular components (not a valid DOST pattern).")
                        count = 0
                        valid_components = []
                        found_definitive_pattern = True  # Stop fallbacks

                    # If expected_count is something else (e.g., 3, 5), we don't know what to do
                    # Leave count as-is and let other filters try

            # Try expected pattern if no definitive pattern found yet
            # Only run this if we haven't already failed the definitive check above
            if not found_definitive_pattern and expected_count >= 3 and count > expected_count:
                print(f"[BunkSegmenter] Trying equidistant subset filter for {expected_count} holes")
                count, valid_components = self.filter_by_best_equidistant_subset(
                    valid_components,
                    expected_count
                )
                if count == expected_count:
                    print(f"[BunkSegmenter] ✓ Reached target count after equidistant subset filter!")
                    found_definitive_pattern = True

            # FALLBACK: Model-specific filters - ONLY if no definitive pattern found
            # And only if we haven't already rejected the result (count != 0)
            if not found_definitive_pattern and count != 0 and count != expected_count and model_type == "DOST" and expected_count == 4:
                print(f"[BunkSegmenter] Applying DOST fallback filters (target: 4 holes)")

                # Filter 1: Distance from center
                if count != 4:
                    valid_components = self.filter_by_distance_from_center(valid_components)
                    count = len(valid_components)
                    if count == 4:
                        print(f"[BunkSegmenter] ✓ Reached target count after distance filter!")

                # Filter 2: Angle subtended (90° pairs)
                if count != 4:
                    count, valid_components = self.filter_by_angle_subtended_to_center_4_holes(valid_components)
                    if count == 4:
                        print(f"[BunkSegmenter] ✓ Reached target count after angle filter!")

                # Filter 3: Distance between pairs
                if count != 4:
                    count, valid_components = self.filter_by_distance_between_pairs_4_holes(valid_components)
                    if count == 4:
                        print(f"[BunkSegmenter] ✓ Reached target count after pair distance filter!")

            elif not found_definitive_pattern and count != 0 and count != expected_count and model_type == "DOSTPLUS" and expected_count == 2:
                print(f"[BunkSegmenter] Applying DOSTPLUS fallback filters (target: 2 holes)")

                # Filter 1: Distance from center
                if count != 2:
                    valid_components = self.filter_by_distance_from_center(valid_components)
                    count = len(valid_components)
                    if count == 2:
                        print(f"[BunkSegmenter] ✓ Reached target count after distance filter!")

                # Filter 2: Angle subtended (180° pairs)
                if count != 2:
                    count, valid_components = self.filter_by_angle_subtended_to_center_2_holes(valid_components)
                    if count == 2:
                        print(f"[BunkSegmenter] ✓ Reached target count after angle filter!")

            print(f"[BunkSegmenter] Final count after cascade filtering: {count}")

        # ===== DEBUG: CASCADE END =====
        print(f"\n{'#'*70}")
        print(f"CASCADE FILTERING END")
        print(f"{'#'*70}")
        print(f"Final count: {count}")
        print(f"Target reached: {count == expected_count if expected_count else 'N/A'}")
        print(f"{'#'*70}\n")
        # ==============================

        return count, valid_components

    def segment_batch_from_unprocessed_image(self, image: np.ndarray, expected_count: Optional[int] = None,
                     model_type: Optional[str] = None) -> Tuple[int, List[Tuple[Tuple[float, float], np.ndarray]], float]:
        """
        Process image with all appropriate gamma values and return best result.

        Args:
        Args:
            image: Input RGB image
            expected_count: Expected number of holes (for early termination)
            model_type: Model type ("DOST" or "DOSTPLUS") for cascade filtering
        Args:
            image: Input RGB image
            expected_count: Expected number of holes (for early termination)
            model_type: Model type ("DOST" or "DOSTPLUS") for cascade filtering
        Args:
            image: Input RGB image
            expected_count: Expected number of holes (for early termination)
            model_type: Model type ("DOST" or "DOSTPLUS") for cascade filtering
        Args:
            image: Input RGB image
            expected_count: Expected number of holes (for early termination)
            model_type: Model type ("DOST" or "DOSTPLUS") for cascade filtering
        """
        # Determine gamma values based on brightness
        gamma_values = self._determine_gamma_values(image)

        print(f"[BunkSegmenter] Processing with {len(gamma_values)} gamma values: {gamma_values}")

        best_count = 0
        best_components = []
        best_gamma = gamma_values[0]

        for gamma in gamma_values:
            print(f"\n[BunkSegmenter] --- Trying gamma={gamma} ---")

            # Preprocess
            preprocessed = self._preprocess_image(image, gamma)

            # Run SAM
            with torch.inference_mode():
                masks = self.mask_generator.generate(preprocessed)

            print(f"[BunkSegmenter] SAM detected {len(masks)} masks")

            # Post-process
            # Post-process
            count, components = self._post_process_masks(
                masks,
                (BUNG_RADIUS * 2, BUNG_RADIUS * 2),
                expected_count=expected_count,
                model_type=model_type
            )

            print(f"[BunkSegmenter] Final count: {count}")

            # Update best if this is better
            if count > best_count:
                best_count = count
                best_components = components
                best_gamma = gamma
                print(f"[BunkSegmenter] ★ New best: count={count}, gamma={gamma}")

            # Early termination if we found the expected count
            if expected_count is not None and count == expected_count:
                print(f"[BunkSegmenter] ✓ Found expected count {expected_count}, stopping early!")
                break

        print(f"\n[BunkSegmenter] === WINNER: gamma={best_gamma}, count={best_count} ===")

        return best_count, best_components, best_gamma

    def segment_holes_batch_from_preprocessed(self, preprocessed_images: List[np.ndarray],
                                             gamma_values: List[float],
                                             expected_count: Optional[int] = None,
                                             model_type: Optional[str] = None) -> Tuple[int, List[Tuple[Tuple[float, float], np.ndarray]], float]:
        """
        NEW OPTIMIZED METHOD - Process multiple preprocessed images in a single torch context.

        This method implements batch processing optimization:
        1. Single torch.inference_mode() for all gammas
        2. Early termination when count matches expected (SIMPLE - no definitive check)
        3. GPU cache clearing at end
        4. Returns best result

        CRITICAL: This uses the EXACT same early termination logic as v0.6:
        - Terminates as soon as count == expected_count
        - NO "definitive pattern" check (that was breaking DOSTPLUS!)

        Args:
            preprocessed_images: List of preprocessed images (one per gamma)
            gamma_values: List of gamma values corresponding to each image
            expected_count: Expected number of holes
            model_type: "DOST" or "DOSTPLUS" for cascade filtering

        Returns:
            Tuple of (best_count, best_components, best_gamma)
        """
        if len(preprocessed_images) != len(gamma_values):
            raise ValueError(f"Mismatch: {len(preprocessed_images)} images vs {len(gamma_values)} gammas")

        print(f"[BunkSegmenter] Batch processing {len(preprocessed_images)} preprocessed images")

        best_count = 0
        best_components = []
        best_gamma = gamma_values[0]

        # SINGLE torch.inference_mode() for ALL gammas (key optimization)
        with torch.inference_mode():
            for idx, (preprocessed, gamma) in enumerate(zip(preprocessed_images, gamma_values)):
                print(f"\n[BunkSegmenter] --- Batch {idx+1}/{len(preprocessed_images)}: gamma={gamma} ---")

                # Run SAM
                masks = self.mask_generator.generate(preprocessed)
                print(f"[BunkSegmenter] SAM detected {len(masks)} masks")

                # Post-process with cascade filtering
                # NOTE: _post_process_masks returns 2 values (count, components) - NOT 3!
                count, components = self._post_process_masks(
                    masks,
                    (BUNG_RADIUS * 2, BUNG_RADIUS * 2),
                    expected_count=expected_count,
                    model_type=model_type
                )

                print(f"[BunkSegmenter] Count: {count}")

                # Update best if this is better
                if count > best_count:
                    best_count = count
                    best_components = components
                    best_gamma = gamma
                    print(f"[BunkSegmenter] ★ New best: count={count}, gamma={gamma}")

                # SIMPLE early termination: count == expected_count (NO definitive check!)
                # This is the EXACT logic from v0.6 that was working!
                if expected_count is not None and count == expected_count:
                    print(f"[BunkSegmenter] ✓ EARLY TERMINATION: Found expected count {expected_count}!")
                    print(f"[BunkSegmenter] Skipping remaining {len(preprocessed_images) - idx - 1} gammas")
                    break

        # Clear CUDA cache (key optimization)
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            print("[BunkSegmenter] GPU cache cleared")

        print(f"\n[BunkSegmenter] === WINNER: gamma={best_gamma}, count={best_count} ===")

        return best_count, best_components, best_gamma

def paint_bung_masks_on_image(
        original_image: np.ndarray,
        components: List[Tuple[Tuple[float, float], np.ndarray]],
        color: Tuple[int, int, int] = (0, 255, 0)
) -> np.ndarray:
    """
    Paint bung component masks on original image.

    Args:
        original_image: Original unprocessed camera image
        components: List of (center, contour) tuples (in annular coordinates)
        color: RGB color tuple (default: green)

    Returns:
        Image with masks painted
    """
    result = original_image.copy()

    if not components:
        return result

    # Translation offset: Annular region offset from full image
    # Annular region center is at BUNG_CENTER with outer_radius=BUNG_RADIUS
    # So annular region top-left corner is at:
    offset_x = BUNG_CENTER[0] - BUNG_RADIUS
    offset_y = BUNG_CENTER[1] - BUNG_RADIUS

    # Create overlay
    overlay = np.zeros_like(result)

    for center, contour in components:
        if len(contour) > 0:
            # Translate contour coordinates to full image
            translated_contour = contour.copy()
            translated_contour[:, :, 0] += offset_x
            translated_contour[:, :, 1] += offset_y

            # Fill mask
            cv2.drawContours(overlay, [translated_contour], -1, color, thickness=cv2.FILLED)

    # Blend with alpha
    alpha = 0.5
    mask_present = overlay.sum(axis=2) > 0

    for c_idx in range(3):
        result[:, :, c_idx] = np.where(
            mask_present,
            result[:, :, c_idx] * (1 - alpha) + overlay[:, :, c_idx] * alpha,
            result[:, :, c_idx]
        )

    # Draw contours
    for center, contour in components:
        if len(contour) > 0:
            # Translate contour coordinates
            translated_contour = contour.copy()
            translated_contour[:, :, 0] += offset_x
            translated_contour[:, :, 1] += offset_y

            cv2.drawContours(result, [translated_contour], -1, color, thickness=2)

            # Draw center point (also translated)
            cx, cy = int(center[0] + offset_x), int(center[1] + offset_y)
            cv2.circle(result, (cx, cy), 3, (255, 0, 0), -1)

    # Add count text
    text = f"COUNT: {len(components)}"
    cv2.putText(result, text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)

    return result