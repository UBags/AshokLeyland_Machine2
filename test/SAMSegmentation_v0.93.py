"""
MobileSAMv2 Segmentation Viewer v1.0 (Fix)
A GUI application for visualizing MobileSAMv2 segmentation masks.
Optimized for 4GB GPU (RTX 3050) - Much faster than SAM2!

MobileSAMv2 Architecture:
- Object-Aware Prompt Sampler: YOLOv8 for detecting objects and generating box prompts
- TinyViT Encoder: Lightweight image encoder (38MB vs 2.4GB for SAM)

Requirements:
    pip install torch torchvision
    pip install opencv-python pillow numpy
    pip install ultralytics  (for YOLOv8)
    cd MobileSAM && pip install -e .

Required Files:
    - MobileSAM/weights/mobile_sam.pt (TinyViT encoder)
    - MobileSAM/MobileSAMv2/weight/ObjectAwareModel.pt (YOLOv8 detector)
"""

import os
import sys
import time
import glob
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
import traceback
import warnings
# Project utilities
from BaseUtils import get_project_root

# --- FIX 1: Add MobileSAM to path if not installed via pip ---
# This fixes "unresolved reference" errors if the folder exists locally but isn't installed.
# Uses project root to locate the models directory
_PROJECT_ROOT = get_project_root()
_MOBILESAM_DIR = os.path.join(_PROJECT_ROOT, "models", "MobileSAM")
if os.path.exists(_MOBILESAM_DIR) and _MOBILESAM_DIR not in sys.path:
    sys.path.append(_MOBILESAM_DIR)
    print(f"Added MobileSAM to path: {_MOBILESAM_DIR}")

# Suppress annoying warnings from external libraries
warnings.filterwarnings("ignore", category=FutureWarning, module="timm")
warnings.filterwarnings("ignore", category=UserWarning, message="Overwriting .* in registry")

import numpy as np
import torch
import cv2
from PIL import Image, ImageTk
import tkinter as tk
from tkinter import filedialog, ttk, messagebox
from scipy.ndimage import gaussian_filter
from scipy.interpolate import RectBivariateSpline

print("=" * 70)
print("Alternative Fix: Monkeypatching torch.load for trusted checkpoints")
print("=" * 70)

# Save the original torch.load
_original_torch_load = torch.load

MAJOR_MINOR_AXIS_CUTOFF_IF_LESS_THAN_10 = 1.5
MAJOR_MINOR_AXIS_CUTOFF_IF_LESS_THAN_20 = 1.45
MAJOR_MINOR_AXIS_CUTOFF_IF_LESS_THAN_40 = 1.25
MAJOR_MINOR_AXIS_CUTOFF_FOR_OTHERS = 1.15

# These are applied based on the median radius/major_axis of each group
# Percentages (12-15% range):
GROUPING_RADIUS_VARIANCE_PERCENTAGE_LESS_THAN_10 = 0.30   # 30%
GROUPING_RADIUS_VARIANCE_PERCENTAGE_LESS_THAN_20 = 0.25   # 25%
GROUPING_RADIUS_VARIANCE_PERCENTAGE_LESS_THAN_40 = 0.20   # 20%
GROUPING_RADIUS_VARIANCE_PERCENTAGE_MORE_THAN_40 = 0.15   # 15%

# Area ratio thresholds for filtering ill-shaped masks
AREA_RATIO_THRESHOLD_IF_LESS_THAN_10 = 0.80
AREA_RATIO_THRESHOLD_IF_LESS_THAN_20 = 0.85
AREA_RATIO_THRESHOLD_IF_LESS_THAN_40 = 0.90
AREA_RATIO_THRESHOLD_FOR_OTHERS = 0.925

# Model-specific distance configurations for hole counting
# Format: (distance1, distance2, tolerance_percentage)
MODEL_DISTANCE_CONFIG = {
    "DOST": {
        "distances": [170, 135],
        "tolerance": 0.10  # 10%
    },
    "DOSTPLUS": {
        "distances": [175, 140],
        "tolerance": 0.10  # 10%
    }
}

PERCENTAGE_SPREAD_FOR_GROUPING_FROM_CENTER = 0.25 # 20%

# Equispacing tolerance for circular arrangement validation
EQUISPACING_STRICT_TOLERANCE = 0.10  # 10% - strict pass criteria
EQUISPACING_RELAXED_TOLERANCE = 0.20  # 20% - fallback if no strict pass

# =============================================================================
# GROUPING ALGORITHM (replaces Agglomerative Clustering)
# =============================================================================

def _compute_median(data, start, end):
    """Compute median of sorted data[start:end+1]."""
    length = end - start + 1
    mid_idx = start + length // 2
    if length % 2 == 1:
        return data[mid_idx]
    else:
        return (data[mid_idx - 1] + data[mid_idx]) / 2.0


def _is_valid_group(data, start, end, max_allowed_deviation):
    """Check if all elements in data[start:end+1] are within ±max_allowed_deviation of median."""
    if start > end:
        return False
    m = _compute_median(data, start, end)
    if m <= 0:
        return all(x == m for x in data[start:end + 1])
    lower = m * (1 - max_allowed_deviation)
    upper = m * (1 + max_allowed_deviation)
    return (data[start] >= lower - 1e-9) and (data[end] <= upper + 1e-9)


def _group_left_to_right(data, max_allowed_deviation):
    """Greedy grouping from left to right."""
    n = len(data)
    groups = []
    medians = []
    i = 0
    while i < n:
        end = i
        j = i + 1
        while j <= n:
            if _is_valid_group(data, i, j - 1, max_allowed_deviation):
                end = j - 1
                j += 1
            else:
                break
        group = data[i:end + 1]
        groups.append((i, end))  # Store indices into sorted array
        medians.append(_compute_median(data, i, end))
        i = end + 1
    return groups, medians


def _group_right_to_left(data, max_allowed_deviation):
    """Greedy grouping from right to left."""
    n = len(data)
    groups = []
    medians = []
    pos = n
    while pos > 0:
        end = pos - 1
        start = end
        while start > 0 and _is_valid_group(data, start - 1, end, max_allowed_deviation):
            start -= 1
        groups.append((start, end))  # Store indices into sorted array
        medians.append(_compute_median(data, start, end))
        pos = start
    groups.reverse()
    medians.reverse()
    return groups, medians


def _separation_score(medians):
    """Score grouping by separation between consecutive group medians."""
    if len(medians) < 2:
        return (float('inf'), float('inf'))
    diffs = [medians[i + 1] - medians[i] for i in range(len(medians) - 1)]
    return (min(diffs), sum(diffs))


def group_numbers_with_indices(index_value_pairs, max_allowed_deviation):
    """
    Group (index, value) pairs by value, maintaining original indices.

    Args:
        index_value_pairs: List of (original_index, value) tuples
        max_allowed_deviation: Maximum allowed deviation from median (e.g., 0.2 for 20%)

    Returns:
        List of lists, where each inner list contains original indices belonging to that group
    """
    if not index_value_pairs:
        return []

    # Sort by value, keeping track of original indices
    sorted_pairs = sorted(index_value_pairs, key=lambda x: x[1])
    sorted_values = [v for _, v in sorted_pairs]
    sorted_indices = [i for i, _ in sorted_pairs]

    # Run both directions
    groups_l, medians_l = _group_left_to_right(sorted_values, max_allowed_deviation)
    groups_r, medians_r = _group_right_to_left(sorted_values, max_allowed_deviation)

    # Choose the better one for separation
    score_l = _separation_score(medians_l)
    score_r = _separation_score(medians_r)

    if score_l >= score_r:
        group_ranges, medians = groups_l, medians_l
    else:
        group_ranges, medians = groups_r, medians_r

    # Convert sorted array index ranges back to original indices
    result_groups = []
    for start, end in group_ranges:
        original_indices = [sorted_indices[i] for i in range(start, end + 1)]
        result_groups.append(original_indices)

    return result_groups, medians


def get_max_group_count_for_model(
        masks: List[Dict[str, Any]],
        groups: Dict[int, List[int]],
        qr_code: str,
        center_x: float,
        center_y: float
) -> Tuple[int, str, Optional[int]]:
    """
    Find the maximum group size at model-specific distances.

    Args:
        masks: List of mask dictionaries with 'shape_info' containing 'center'
        groups: Final groupings dict (group_id -> list of mask indices)
        qr_code: QR code string to determine the model
        center_x: X coordinate of the disc center
        center_y: Y coordinate of the disc center

    Returns:
        Tuple of (max_count, model_name, matching_group_id)
        Returns (0, model_name, None) if no matching groups found
        Returns (0, "UNKNOWN", None) if model not recognized
    """
    from utils.QRCodeHelper import getModel_LHSRHS_AndTonnage

    # Get model name from QR code
    model_name, lhs_rhs, tonnage = getModel_LHSRHS_AndTonnage(qr_code)

    print(f"\n" + "=" * 60)
    print(f"GET MAX GROUP COUNT FOR MODEL")
    print(f"  QR Code: {qr_code}")
    print(f"  Model: {model_name}, Side: {lhs_rhs}, Tonnage: {tonnage}")
    print("=" * 60)

    # Check if model is supported
    if model_name not in MODEL_DISTANCE_CONFIG:
        print(f"  WARNING: Model '{model_name}' not in configuration. Supported: {list(MODEL_DISTANCE_CONFIG.keys())}")
        return (0, model_name, None)

    config = MODEL_DISTANCE_CONFIG[model_name]
    target_distances = config["distances"]
    tolerance = config["tolerance"]

    print(f"  Target distances: {target_distances}")
    print(f"  Tolerance: ±{tolerance * 100:.0f}%")

    # Calculate median distance for each group
    group_stats = []

    for group_id, mask_indices in groups.items():
        if len(mask_indices) == 0:
            continue

        # Calculate distances for all masks in this group
        distances = []
        for mask_idx in mask_indices:
            if mask_idx < len(masks):
                shape_info = masks[mask_idx].get('shape_info', {})
                mask_center = shape_info.get('center')

                if mask_center is not None:
                    cx, cy = mask_center
                    distance = np.sqrt((cx - center_x) ** 2 + (cy - center_y) ** 2)
                    distances.append(distance)

        if distances:
            median_distance = np.median(distances)
            group_stats.append({
                'group_id': group_id,
                'count': len(mask_indices),
                'median_distance': median_distance,
                'distances': distances
            })

    # Find groups matching target distances
    matching_groups = []

    for target_dist in target_distances:
        lower_bound = target_dist * (1 - tolerance)
        upper_bound = target_dist * (1 + tolerance)

        print(f"\n  Checking for distance {target_dist} (range: {lower_bound:.1f} - {upper_bound:.1f}):")

        for gs in group_stats:
            if lower_bound <= gs['median_distance'] <= upper_bound:
                matching_groups.append(gs)
                print(f"    ✓ Group {gs['group_id']}: count={gs['count']}, median_dist={gs['median_distance']:.2f}")

    if not matching_groups:
        print(f"\n  No groups found at target distances!")
        return (0, model_name, None)

    # Find the maximum group size among matching groups
    max_group = max(matching_groups, key=lambda x: x['count'])

    print(
        f"\n  RESULT: Max group count = {max_group['count']} (Group {max_group['group_id']} at distance {max_group['median_distance']:.2f})")
    print("=" * 60 + "\n")

    return (max_group['count'], model_name, max_group['group_id'])

def filter_masks_by_area_ratio(masks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Filter out ill-shaped masks by comparing actual mask area to expected ellipse/circle area.

    Args:
        masks: List of mask dictionaries with 'shape_info' and 'area' already set

    Returns:
        Filtered list of masks (only well-shaped circles and ellipses)
    """
    import math

    filtered_masks = []
    excluded_count = 0

    print("\n" + "=" * 60)
    print("AREA RATIO FILTER - Excluding ill-shaped masks")
    print("=" * 60)

    for idx, mask_data in enumerate(masks):
        shape_info = mask_data.get('shape_info', {})
        shape_type = shape_info.get('type', 'other')
        actual_area = mask_data.get('area', 0)

        # Skip 'other' shapes - they're already excluded from grouping
        if shape_type == 'other':
            filtered_masks.append(mask_data)
            continue

        # Calculate expected area
        if shape_type == 'circle':
            radius = shape_info.get('radius', 0)
            expected_area = math.pi * radius * radius
            size_metric = radius
        elif shape_type == 'ellipse':
            major_axis = shape_info.get('major_axis', 0)
            minor_axis = shape_info.get('minor_axis', 0)
            expected_area = math.pi * major_axis * minor_axis
            size_metric = major_axis
        else:
            filtered_masks.append(mask_data)
            continue

        # Calculate area ratio
        if expected_area > 0:
            area_ratio = actual_area / expected_area
        else:
            area_ratio = 0

        # Determine threshold based on size
        if size_metric < 10:
            threshold = AREA_RATIO_THRESHOLD_IF_LESS_THAN_10
        elif size_metric < 20:
            threshold = AREA_RATIO_THRESHOLD_IF_LESS_THAN_20
        elif size_metric < 40:
            threshold = AREA_RATIO_THRESHOLD_IF_LESS_THAN_40
        else:
            threshold = AREA_RATIO_THRESHOLD_FOR_OTHERS

        # Apply filter
        if area_ratio >= threshold:
            filtered_masks.append(mask_data)
        else:
            excluded_count += 1
            print(f"  Excluded {shape_type} (idx={idx}): size={size_metric:.2f}, "
                  f"actual_area={actual_area}, expected_area={expected_area:.1f}, "
                  f"ratio={area_ratio:.3f} < threshold={threshold}")

    print(f"\nArea ratio filter: kept {len(filtered_masks)}, excluded {excluded_count}")
    print("=" * 60 + "\n")

    return filtered_masks


def _patched_torch_load(f, map_location=None, pickle_module=None, *, weights_only=None, mmap=None, **kwargs):
    """
    Patched torch.load that forces weights_only=False for trusted checkpoints.

    WARNING: This disables PyTorch's security feature. Only use if you trust
    the source of your model checkpoints (YOLO, MobileSAM, etc.)
    """
    # Force weights_only=False for all loads
    # This bypasses the PyTorch 2.6+ security feature
    return _original_torch_load(
        f,
        map_location=map_location,
        pickle_module=pickle_module,
        weights_only=False,  # ⚠ SECURITY: Trusting all checkpoints
        mmap=mmap,
        **kwargs
    )

def filter_small_groups(
        groups: Dict[int, List[int]],
        min_size: int = 3
) -> Dict[int, List[int]]:
    """
    Remove groups with fewer than min_size masks.

    Args:
        groups: Dictionary mapping group_id to list of mask indices
        min_size: Minimum number of masks required (default 3)

    Returns:
        Filtered dictionary of groups
    """
    print("\n" + "=" * 60)
    print(f"SIZE FILTER - Removing groups with < {min_size} masks")
    print("=" * 60)

    filtered_groups = {}
    removed_count = 0

    for group_id, mask_indices in groups.items():
        if len(mask_indices) >= min_size:
            filtered_groups[group_id] = mask_indices
            print(f"  ✓ Group {group_id}: {len(mask_indices)} masks - KEPT")
        else:
            removed_count += 1
            print(f"  ✗ Group {group_id}: {len(mask_indices)} masks - REMOVED")

    print(f"\nSize filter: {len(groups)} -> {len(filtered_groups)} groups (removed {removed_count})")
    print("=" * 60 + "\n")

    return filtered_groups

def filter_subgroups_by_center_envelope(
        masks: List[Dict[str, Any]],
        groups: Dict[int, List[int]],
        center_x: float,
        center_y: float
) -> Dict[int, List[int]]:
    """
    Filter out sub-groups where the disc center is not within the convex hull
    of the mask centers. This removes groups that are on one side of the center.

    Args:
        masks: List of mask dictionaries with 'shape_info' containing 'center'
        groups: Dictionary mapping group_id to list of mask indices
        center_x: X coordinate of the disc center
        center_y: Y coordinate of the disc center

    Returns:
        Filtered dictionary of groups
    """
    from scipy.spatial import ConvexHull

    print("\n" + "=" * 60)
    print("ENVELOPE FILTER - Checking if center is within mask envelope")
    print(f"  Disc center: ({center_x:.1f}, {center_y:.1f})")
    print("=" * 60)

    filtered_groups = {}

    for group_id, mask_indices in groups.items():
        if len(mask_indices) < 3:
            # Need at least 3 points to form a convex hull
            # For small groups, check if center is "surrounded" by checking angles
            if len(mask_indices) <= 2:
                print(f"  Group {group_id}: Only {len(mask_indices)} masks, skipping envelope check")
                filtered_groups[group_id] = mask_indices
                continue

        # Get centers of all masks in this group
        centers = []
        for mask_idx in mask_indices:
            if mask_idx < len(masks):
                shape_info = masks[mask_idx].get('shape_info', {})
                mask_center = shape_info.get('center')
                if mask_center is not None:
                    centers.append(mask_center)

        if len(centers) < 3:
            print(f"  Group {group_id}: Not enough valid centers ({len(centers)}), keeping group")
            filtered_groups[group_id] = mask_indices
            continue

        centers_array = np.array(centers)

        try:
            # Compute convex hull
            hull = ConvexHull(centers_array)

            # Check if disc center is inside the convex hull
            # Use the point-in-polygon test with hull vertices
            hull_points = centers_array[hull.vertices]

            if _point_in_convex_hull(center_x, center_y, hull_points):
                print(f"  ✓ Group {group_id}: Center IS inside envelope ({len(mask_indices)} masks)")
                filtered_groups[group_id] = mask_indices
            else:
                print(f"  ✗ Group {group_id}: Center is OUTSIDE envelope ({len(mask_indices)} masks) - REMOVED")

        except Exception as e:
            # If hull computation fails (e.g., collinear points), keep the group
            print(f"  Group {group_id}: Hull computation failed ({e}), keeping group")
            filtered_groups[group_id] = mask_indices

    print(f"\nEnvelope filter: {len(groups)} -> {len(filtered_groups)} groups")
    print("=" * 60 + "\n")

    return filtered_groups


def _point_in_convex_hull(px: float, py: float, hull_points: np.ndarray) -> bool:
    """
    Check if a point (px, py) is inside a convex hull defined by hull_points.
    Uses the cross-product method - point is inside if it's on the same side
    of all edges.
    """
    n = len(hull_points)

    for i in range(n):
        x1, y1 = hull_points[i]
        x2, y2 = hull_points[(i + 1) % n]

        # Cross product of edge vector and point vector
        cross = (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)

        # First edge determines the expected sign
        if i == 0:
            sign = cross >= 0
        else:
            if (cross >= 0) != sign:
                return False

    return True


def get_clockwise_order(
        masks: List[Dict[str, Any]],
        mask_indices: List[int],
        center_x: float,
        center_y: float
) -> List[int]:
    """
    Order mask indices in clockwise sequence around the center.

    Args:
        masks: List of mask dictionaries
        mask_indices: Indices of masks in this group
        center_x, center_y: Center coordinates

    Returns:
        List of mask indices ordered clockwise
    """
    # Calculate angle from center for each mask
    angles = []
    for mask_idx in mask_indices:
        if mask_idx < len(masks):
            shape_info = masks[mask_idx].get('shape_info', {})
            mask_center = shape_info.get('center')
            if mask_center is not None:
                cx, cy = mask_center
                angle = np.arctan2(cy - center_y, cx - center_x)
                angles.append((mask_idx, angle))

    # Sort by angle (clockwise = descending angle, or ascending negative angle)
    angles.sort(key=lambda x: -x[1])

    return [idx for idx, _ in angles]


def calculate_equispacing_score(
        masks: List[Dict[str, Any]],
        mask_indices: List[int],
        center_x: float,
        center_y: float
) -> Tuple[float, float, List[float]]:
    """
    Calculate how equispaced the masks are in a circular arrangement.

    Args:
        masks: List of mask dictionaries
        mask_indices: Indices of masks in this group (should be in clockwise order)
        center_x, center_y: Center coordinates

    Returns:
        Tuple of (max_deviation_ratio, mean_deviation_ratio, distances)
        where deviation_ratio = |distance - median| / median
    """
    if len(mask_indices) < 2:
        return (0.0, 0.0, [])

    # Get ordered mask centers
    ordered_indices = get_clockwise_order(masks, mask_indices, center_x, center_y)

    centers = []
    for mask_idx in ordered_indices:
        if mask_idx < len(masks):
            shape_info = masks[mask_idx].get('shape_info', {})
            mask_center = shape_info.get('center')
            if mask_center is not None:
                centers.append(mask_center)

    if len(centers) < 2:
        return (0.0, 0.0, [])

    # Calculate distances between consecutive masks (circular)
    distances = []
    n = len(centers)
    for i in range(n):
        x1, y1 = centers[i]
        x2, y2 = centers[(i + 1) % n]
        dist = np.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
        distances.append(dist)

    if not distances:
        return (0.0, 0.0, [])

    median_dist = np.median(distances)

    if median_dist == 0:
        return (float('inf'), float('inf'), distances)

    # Calculate deviation ratios
    deviations = [abs(d - median_dist) / median_dist for d in distances]
    max_deviation = max(deviations)
    mean_deviation = np.mean(deviations)

    return (max_deviation, mean_deviation, distances)




def try_remove_outlier_mask(
        masks: List[Dict[str, Any]],
        mask_indices: List[int],
        center_x: float,
        center_y: float,
        strict_tolerance: float = EQUISPACING_STRICT_TOLERANCE,
        relaxed_tolerance: float = EQUISPACING_RELAXED_TOLERANCE,
        min_masks: int = 4
) -> Tuple[List[int], float, List[float], int]:
    """
    Iteratively remove outlier masks to improve equispacing until criteria are met
    or minimum mask count is reached.

    Args:
        masks: List of mask dictionaries
        mask_indices: Original list of mask indices
        center_x, center_y: Center coordinates
        strict_tolerance: Target tolerance to achieve
        relaxed_tolerance: Fallback tolerance
        min_masks: Minimum number of masks to keep (default 4)

    Returns:
        Tuple of (best_indices, best_max_deviation, best_distances, num_removed)
    """
    if len(mask_indices) < min_masks:
        # Not enough masks to remove any
        max_dev, mean_dev, distances = calculate_equispacing_score(
            masks, mask_indices, center_x, center_y
        )
        return (mask_indices, max_dev, distances, 0)

    current_indices = list(mask_indices)
    total_removed = 0

    while len(current_indices) >= min_masks:
        # Calculate current score
        max_dev, mean_dev, distances = calculate_equispacing_score(
            masks, current_indices, center_x, center_y
        )

        # Check if we've achieved the target
        if max_dev <= strict_tolerance:
            return (current_indices, max_dev, distances, total_removed)

        # If we can't remove any more, return current state
        if len(current_indices) <= min_masks:
            return (current_indices, max_dev, distances, total_removed)

        # Get clockwise ordered indices
        ordered_indices = get_clockwise_order(masks, current_indices, center_x, center_y)

        # Get centers for distance calculation
        centers = []
        for mask_idx in ordered_indices:
            if mask_idx < len(masks):
                shape_info = masks[mask_idx].get('shape_info', {})
                mask_center = shape_info.get('center')
                if mask_center is not None:
                    centers.append((mask_idx, mask_center))

        if len(centers) < min_masks:
            return (current_indices, max_dev, distances, total_removed)

        # Calculate distances between consecutive masks
        n = len(centers)
        edge_distances = []
        for i in range(n):
            x1, y1 = centers[i][1]
            x2, y2 = centers[(i + 1) % n][1]
            dist = np.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
            edge_distances.append(dist)

        median_dist = np.median(edge_distances)

        # Find the edge with maximum deviation
        deviations = [(i, abs(d - median_dist) / median_dist if median_dist > 0 else 0)
                      for i, d in enumerate(edge_distances)]
        worst_edge_idx, worst_deviation = max(deviations, key=lambda x: x[1])

        # The worst edge is between centers[worst_edge_idx] and centers[(worst_edge_idx + 1) % n]
        mask_at_start = centers[worst_edge_idx][0]
        mask_at_end = centers[(worst_edge_idx + 1) % n][0]

        # Try Option A: Remove mask_at_start
        indices_option_a = [idx for idx in ordered_indices if idx != mask_at_start]
        max_dev_a, mean_dev_a, distances_a = calculate_equispacing_score(
            masks, indices_option_a, center_x, center_y
        )

        # Try Option B: Remove mask_at_end
        indices_option_b = [idx for idx in ordered_indices if idx != mask_at_end]
        max_dev_b, mean_dev_b, distances_b = calculate_equispacing_score(
            masks, indices_option_b, center_x, center_y
        )

        # Choose the better option (lower max deviation)
        if max_dev_a <= max_dev_b:
            current_indices = indices_option_a
            removed_mask = mask_at_start
        else:
            current_indices = indices_option_b
            removed_mask = mask_at_end

        total_removed += 1

        # Log the removal
        new_max_dev = min(max_dev_a, max_dev_b)
        print(f"      Iteration {total_removed}: Removed mask {removed_mask}, "
              f"now {len(current_indices)} masks, max_dev={new_max_dev:.3f}")

    # Final calculation
    max_dev, mean_dev, distances = calculate_equispacing_score(
        masks, current_indices, center_x, center_y
    )

    return (current_indices, max_dev, distances, total_removed)


def filter_subgroups_by_equispacing(
        masks: List[Dict[str, Any]],
        groups: Dict[int, List[int]],
        center_x: float,
        center_y: float,
        strict_tolerance: float = EQUISPACING_STRICT_TOLERANCE,
        relaxed_tolerance: float = EQUISPACING_RELAXED_TOLERANCE
) -> Tuple[Dict[int, List[int]], Optional[int]]:
    """
    Filter sub-groups by checking if masks are equispaced in circular arrangement.

    Rules:
    1. Keep groups where ALL inter-mask distances are within ±strict_tolerance of median
    2. If a group fails, try removing the outlier mask and re-check
    3. If no groups pass strict test, choose the one with lowest max deviation,
       provided max deviation < relaxed_tolerance

    Args:
        masks: List of mask dictionaries
        groups: Dictionary mapping group_id to list of mask indices
        center_x, center_y: Center coordinates
        strict_tolerance: Strict pass threshold (default 10%)
        relaxed_tolerance: Fallback threshold (default 20%)

    Returns:
        Tuple of (filtered_groups, winning_group_id)
        winning_group_id is the single best group if we had to fall back to relaxed criteria
    """
    print("\n" + "=" * 60)
    print("EQUISPACING FILTER - Checking circular arrangement")
    print(f"  Strict tolerance: ±{strict_tolerance * 100:.0f}%")
    print(f"  Relaxed tolerance: ±{relaxed_tolerance * 100:.0f}%")
    print("=" * 60)

    group_scores = []
    strict_pass_groups = {}

    for group_id, mask_indices in groups.items():
        if len(mask_indices) < 3:
            print(f"  Group {group_id}: Only {len(mask_indices)} masks, need ≥3 for equispacing check")
            continue

        max_dev, mean_dev, distances = calculate_equispacing_score(
            masks, mask_indices, center_x, center_y
        )

        distances_str = [f"{d:.1f}" for d in distances]
        print(f"  Group {group_id}: {len(mask_indices)} masks, "
              f"max_dev={max_dev:.3f}, mean_dev={mean_dev:.3f}")
        print(f"    Distances: {distances_str}")

        # Check strict criteria
        if max_dev <= strict_tolerance:
            print(f"    ✓ PASSES strict criteria (max_dev {max_dev:.3f} ≤ {strict_tolerance})")
            strict_pass_groups[group_id] = mask_indices
            group_scores.append({
                'group_id': group_id,
                'mask_indices': mask_indices,
                'max_deviation': max_dev,
                'mean_deviation': mean_dev,
                'distances': distances,
                'count': len(mask_indices),
                'outlier_removed': False
            })
        else:
            # Try removing outliers iteratively
            if len(mask_indices) >= 4:
                print(f"    → Trying to remove outliers iteratively...")
                improved_indices, improved_max_dev, improved_distances, removed_count = try_remove_outlier_mask(
                    masks, mask_indices, center_x, center_y,
                    strict_tolerance, relaxed_tolerance, min_masks=4
                )

                improved_distances_str = [f"{d:.1f}" for d in improved_distances]
                print(f"    → Final result: Removed {removed_count} outlier(s), "
                      f"{len(improved_indices)} masks remain, max_dev={improved_max_dev:.3f}")
                print(f"      Final distances: {improved_distances_str}")
                if improved_max_dev <= strict_tolerance:
                    print(f"    ✓ NOW PASSES strict criteria after outlier removal!")
                    strict_pass_groups[group_id] = improved_indices
                    group_scores.append({
                        'group_id': group_id,
                        'mask_indices': improved_indices,
                        'max_deviation': improved_max_dev,
                        'mean_deviation': np.mean(
                            [abs(d - np.median(improved_distances)) / np.median(improved_distances) for d in
                             improved_distances]) if improved_distances else 0,
                        'distances': improved_distances,
                        'count': len(improved_indices),
                        'outlier_removed': True
                    })
                # if improved_max_dev <= strict_tolerance:
                #     print(f"    ✓ NOW PASSES strict criteria after outlier removal!")
                #     strict_pass_groups[group_id] = improved_indices
                #     group_scores.append({
                #         'group_id': group_id,
                #         'mask_indices': improved_indices,
                #         'max_deviation': improved_max_dev,
                #         'mean_deviation': np.mean([abs(d - np.median(improved_distances)) / np.median(improved_distances) for d in improved_distances]) if improved_distances else 0,
                #         'distances': improved_distances,
                #         'count': len(improved_indices),
                #         'outlier_removed': True
                #     })
                elif improved_max_dev <= relaxed_tolerance:
                    print(
                        f"    ~ Within relaxed criteria after outlier removal (max_dev {improved_max_dev:.3f} ≤ {relaxed_tolerance})")
                    group_scores.append({
                        'group_id': group_id,
                        'mask_indices': improved_indices,
                        'max_deviation': improved_max_dev,
                        'mean_deviation': np.mean(
                            [abs(d - np.median(improved_distances)) / np.median(improved_distances) for d in
                             improved_distances]) if improved_distances else 0,
                        'distances': improved_distances,
                        'count': len(improved_indices),
                        'outlier_removed': True
                    })
                else:
                    print(
                        f"    ✗ Still FAILS after outlier removal (max_dev {improved_max_dev:.3f} > {relaxed_tolerance})")
                    # Still add to scores for potential fallback
                    group_scores.append({
                        'group_id': group_id,
                        'mask_indices': improved_indices,
                        'max_deviation': improved_max_dev,
                        'mean_deviation': np.mean(
                            [abs(d - np.median(improved_distances)) / np.median(improved_distances) for d in
                             improved_distances]) if improved_distances else 0,
                        'distances': improved_distances,
                        'count': len(improved_indices),
                        'outlier_removed': True
                    })
            else:
                if max_dev <= relaxed_tolerance:
                    print(f"    ~ Within relaxed criteria (max_dev {max_dev:.3f} ≤ {relaxed_tolerance})")
                else:
                    print(f"    ✗ FAILS both criteria (max_dev {max_dev:.3f} > {relaxed_tolerance})")

                group_scores.append({
                    'group_id': group_id,
                    'mask_indices': mask_indices,
                    'max_deviation': max_dev,
                    'mean_deviation': mean_dev,
                    'distances': distances,
                    'count': len(mask_indices),
                    'outlier_removed': False
                })

    # Decision logic
    if strict_pass_groups:
        print(f"\n  {len(strict_pass_groups)} groups passed strict criteria")
        print("=" * 60 + "\n")
        return (strict_pass_groups, None)

    # No strict passes - find best relaxed candidate
    relaxed_candidates = [gs for gs in group_scores if gs['max_deviation'] <= relaxed_tolerance]

    if not relaxed_candidates:
        print(f"\n  WARNING: No groups passed even relaxed criteria!")
        # Last resort: return the best group even if it doesn't meet criteria
        if group_scores:
            best = min(group_scores, key=lambda x: (x['max_deviation'], -x['count']))
            print(f"  → Returning best available: Group {best['group_id']} "
                  f"(max_dev={best['max_deviation']:.3f}, count={best['count']})")
            print("=" * 60 + "\n")
            return ({best['group_id']: best['mask_indices']}, best['group_id'])
        print("=" * 60 + "\n")
        return ({}, None)

    # Choose the one with lowest max deviation (most equispaced)
    # Tie-breaker: more masks
    best = min(relaxed_candidates, key=lambda x: (x['max_deviation'], -x['count']))

    print(f"\n  Fallback: Selected Group {best['group_id']} as best "
          f"(max_dev={best['max_deviation']:.3f}, count={best['count']})")
    print("=" * 60 + "\n")

    return ({best['group_id']: best['mask_indices']}, best['group_id'])

def select_winning_group(
        groups: Dict[int, List[int]]
) -> Tuple[Optional[int], int]:
    """
    Select the winning group (the one with most masks).

    Args:
        groups: Filtered groups dictionary

    Returns:
        Tuple of (winning_group_id, count)
    """
    if not groups:
        return (None, 0)

    # Find group with maximum count
    winning_id = max(groups.keys(), key=lambda gid: len(groups[gid]))
    winning_count = len(groups[winning_id])

    return (winning_id, winning_count)

# Apply the monkeypatch
torch.load = _patched_torch_load
print("✓ torch.load patched to use weights_only=False")
print("⚠ WARNING: This disables PyTorch security features.")
print("  Only use with trusted model files!")
print("=" * 70)

# --- FIX 2: PyTorch 2.6+ Security Allowlist ---
# Fixes "Weights only load failed" by allowing necessary PyTorch and Ultralytics classes.
# PyTorch 2.6+ changed default to weights_only=True for security, requiring explicit allowlisting.
try:
    if hasattr(torch.serialization, "add_safe_globals"):
        print("Applying PyTorch 2.6+ safe_globals allowlist...")

        # Standard PyTorch container classes (always needed)
        safe_classes = [
            torch.nn.modules.container.Sequential,
            torch.nn.modules.container.ModuleList,
            torch.nn.modules.container.ModuleDict,
        ]

        # Try to import and add Ultralytics model classes
        try:
            from ultralytics.nn.tasks import (
                SegmentationModel,
                DetectionModel,
                ClassificationModel,
                PoseModel,
                OBBModel
            )

            safe_classes.extend([
                SegmentationModel,
                DetectionModel,
                ClassificationModel,
                PoseModel,
                OBBModel,
            ])
            print("✓ Added Ultralytics model classes to safe globals")
        except ImportError as e:
            # Some ultralytics versions might not have all classes
            print(f"⚠ Could not import all Ultralytics classes: {e}")
            try:
                # Try minimal import for older versions
                from ultralytics.nn.tasks import SegmentationModel, DetectionModel

                safe_classes.extend([SegmentationModel, DetectionModel])
                print("✓ Added basic Ultralytics classes to safe globals")
            except ImportError as e2:
                print(f"⚠ Warning: Could not import Ultralytics task classes: {e2}")

        # Apply the allowlist
        torch.serialization.add_safe_globals(safe_classes)
        print(f"✓ Safe globals configured ({len(safe_classes)} classes allowlisted)")

except (ImportError, AttributeError) as e:
    print(f"Note: PyTorch 2.6+ safe_globals not available or error: {e}")

# MobileSAM imports
try:
    from mobile_sam import sam_model_registry, SamPredictor
except ImportError:
    # Fallback message if path append didn't work
    print("Error: Could not import mobile_sam. Make sure the 'MobileSAM' folder is in this directory.")
    sam_model_registry = None
    SamPredictor = None

# Ultralytics for YOLOv8 object detection
from ultralytics import YOLO


# =============================================================================
# NORMALISATION FUNCTIONS
# =============================================================================

def rgb2gray(rgb: np.ndarray) -> np.ndarray:
    """Standard RGB → grayscale conversion."""
    if rgb.ndim == 2:
        return rgb
    return np.dot(rgb[..., :3], [0.299, 0.587, 0.114])


def ensure_float32(image: np.ndarray) -> np.ndarray:
    """Convert image to float32 in [0, 255] range."""
    img = image.copy()
    if img.dtype == np.uint8:
        return img.astype(np.float32)
    elif img.max() <= 1.0:
        return (img * 255.0).astype(np.float32)
    return img.astype(np.float32)


def create_annular_mask(shape: Tuple[int, int], center: Tuple[int, int],
                        outer_radius: int, inner_radius: int) -> np.ndarray:
    """
    Create an annular (ring) mask.

    Args:
        shape: (height, width) of image
        center: (cx, cy) center coordinates
        outer_radius: Outer radius of ring
        inner_radius: Inner radius of ring

    Returns:
        Boolean mask (True inside annular region)
    """
    h, w = shape
    cx, cy = center

    y, x = np.ogrid[:h, :w]
    dist = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)

    mask = (dist <= outer_radius) & (dist >= inner_radius)
    return mask


def extract_annular_region(image: np.ndarray, center: Tuple[int, int],
                           outer_radius: int, inner_radius: int,
                           fill_color: int = 0) -> Tuple[np.ndarray, np.ndarray]:
    """
    Extract annular region from image, painting outside as fill_color.

    Returns:
        Tuple of (cropped_image, mask)
    """
    h, w = image.shape[:2]

    # Create annular mask on full image
    mask = create_annular_mask((h, w), center, outer_radius, inner_radius)

    # Apply mask to image
    if image.ndim == 3:
        result = image.copy()
        for c in range(3):
            result[:, :, c] = np.where(mask, image[:, :, c], fill_color)
    else:
        result = np.where(mask, image, fill_color)

    # Crop to bounding box of outer circle
    cx, cy = center
    x1 = max(0, cx - outer_radius)
    x2 = min(w, cx + outer_radius)
    y1 = max(0, cy - outer_radius)
    y2 = min(h, cy + outer_radius)

    cropped_image = result[y1:y2, x1:x2]
    cropped_mask = mask[y1:y2, x1:x2]

    return cropped_image, cropped_mask


def pixBackgroundNorm_masked(
        image: np.ndarray,
        mask: np.ndarray,
        sx: int = 10,
        sy: int = 10,
        thresh: int = 100,
        mincount: int = 50,
        bgval: int = 200,
        smoothx: int = 2,
        smoothy: int = 2
) -> np.ndarray:
    """Background normalization with mask support."""
    gray = rgb2gray(image)
    gray = ensure_float32(gray)
    h, w = gray.shape

    nx = (w + sx - 1) // sx
    ny = (h + sy - 1) // sy

    bg_map = np.zeros((ny, nx), dtype=np.float32)

    for j in range(ny):
        y_start = j * sy
        y_end = min((j + 1) * sy, h)

        for i in range(nx):
            x_start = i * sx
            x_end = min((i + 1) * sx, w)

            tile = gray[y_start:y_end, x_start:x_end]
            tile_mask = mask[y_start:y_end, x_start:x_end]

            masked_pixels = tile[tile_mask]

            if len(masked_pixels) > 0:
                bright_pixels = masked_pixels[masked_pixels > thresh]

                if len(bright_pixels) >= mincount:
                    bg_map[j, i] = np.mean(bright_pixels)
                else:
                    bg_map[j, i] = np.mean(masked_pixels)
            else:
                bg_map[j, i] = 200.0

    if smoothx > 0 or smoothy > 0:
        sigma_x = smoothx / 2.0
        sigma_y = smoothy / 2.0
        bg_map = gaussian_filter(bg_map, sigma=(sigma_y, sigma_x))

    x_coords = (np.arange(nx) + 0.5) * sx
    y_coords = (np.arange(ny) + 0.5) * sy

    interp = RectBivariateSpline(y_coords, x_coords, bg_map, kx=1, ky=1)

    x_full = np.arange(w)
    y_full = np.arange(h)
    bg_full = interp(y_full, x_full)

    bg_full = np.maximum(bg_full, 1.0)
    normalized = gray * bgval / bg_full
    normalized = np.clip(normalized, 0, 255)

    result = gray.copy()
    result[mask] = normalized[mask]

    return result.astype(np.uint8)


def pixContrastNorm_masked(
        image: np.ndarray,
        mask: np.ndarray,
        sx: int = 10,
        sy: int = 10,
        mindiff: int = 50,
        smoothx: int = 2,
        smoothy: int = 2
) -> np.ndarray:
    """Contrast normalization with mask support."""
    gray = rgb2gray(image)
    gray = ensure_float32(gray)
    h, w = gray.shape

    nx = (w + sx - 1) // sx
    ny = (h + sy - 1) // sy

    mean_map = np.zeros((ny, nx), dtype=np.float32)
    std_map = np.zeros((ny, nx), dtype=np.float32)

    for j in range(ny):
        y_start = j * sy
        y_end = min((j + 1) * sy, h)

        for i in range(nx):
            x_start = i * sx
            x_end = min((i + 1) * sx, w)

            tile = gray[y_start:y_end, x_start:x_end]
            tile_mask = mask[y_start:y_end, x_start:x_end]

            masked_pixels = tile[tile_mask]

            if len(masked_pixels) > 0:
                mean_map[j, i] = np.mean(masked_pixels)
                std_map[j, i] = np.std(masked_pixels)
            else:
                mean_map[j, i] = 128.0
                std_map[j, i] = mindiff

    if smoothx > 0 or smoothy > 0:
        sigma_x = smoothx / 2.0
        sigma_y = smoothy / 2.0
        mean_map = gaussian_filter(mean_map, sigma=(sigma_y, sigma_x))
        std_map = gaussian_filter(std_map, sigma=(sigma_y, sigma_x))

    x_coords = (np.arange(nx) + 0.5) * sx
    y_coords = (np.arange(ny) + 0.5) * sy

    mean_interp = RectBivariateSpline(y_coords, x_coords, mean_map, kx=1, ky=1)
    std_interp = RectBivariateSpline(y_coords, x_coords, std_map, kx=1, ky=1)

    x_full = np.arange(w)
    y_full = np.arange(h)
    mean_full = mean_interp(y_full, x_full)
    std_full = std_interp(y_full, x_full)

    std_full = np.maximum(std_full, mindiff)
    normalized = (gray - mean_full) / std_full * 64.0 + 128.0
    normalized = np.clip(normalized, 0, 255)

    result = gray.copy()
    result[mask] = normalized[mask]

    return result.astype(np.uint8)


# =============================================================================
# MobileSAMv2 Automatic Mask Generator (using YOLOv8 + MobileSAM)
# =============================================================================

class MobileSAMv2AutomaticMaskGenerator:
    """
    MobileSAMv2 Automatic Mask Generator.

    Uses YOLOv8 (ObjectAwareModel) for object detection to get bounding boxes,
    then MobileSAM sam_predictor to generate masks for each detected object.
    """

    def __init__(
            self,
            sam_predictor: SamPredictor,
            yolo_model: YOLO,
            conf_threshold: float = 0.25,
            iou_threshold: float = 0.7,
            min_mask_region_area: int = 100,
            max_mask_region_area: int = 1500,
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

        # Generate masks using box prompts
        if len(boxes) > 0:
            for box in boxes:
                try:
                    mask_data = self._predict_mask_from_box(box)
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

    def _predict_mask_from_box(self, box: np.ndarray) -> Optional[Dict[str, Any]]:
        """Generate mask from a bounding box prompt."""
        x1, y1, x2, y2 = box

        # Predict mask using box prompt
        masks, scores, _ = self.predictor.predict(
            point_coords=None,
            point_labels=None,
            box=np.array([x1, y1, x2, y2]),
            multimask_output=False
        )

        if masks is None or len(masks) == 0:
            return None

        mask = masks[0]  # Take the first (and only) mask
        score = scores[0] if scores is not None else 0.0

        # Calculate area
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


# =============================================================================
# GUI APPLICATION
# =============================================================================

class MobileSAMv2SegmentationApp:
    def __init__(self, root: tk.Tk, mobile_sam_path: str, yolo_path: str):
        self.root = root
        self.root.title("MobileSAMv2 Segmentation Viewer v1.0")
        self.root.geometry("1500x950")
        self.root.configure(bg="#2b2b2b")

        # Image processing state
        self.current_files: List[str] = []
        self.current_index: int = 0

        # Annular disc parameters (centered at 635, 345 with diameter 500, inner radius 120)
        self.annular_center = (635, 345)
        self.annular_outer_radius = 250  # diameter 500 / 2
        self.annular_inner_radius = 90

        # Model state
        self.mobile_sam_path = mobile_sam_path
        self.yolo_path = yolo_path
        self.sam_model = None
        self.sam_predictor = None
        self.yolo_model = None
        self.mask_generator = None
        self.device = "cuda" if torch.cuda.is_available() else "cpu"

        # Store current displayed image references
        self.left_photo = None
        self.right_photo = None

        # Store pipeline images
        self.current_annular_mask = None
        self.pipeline_images = {}

        # Display scale factor
        self.display_scale = 0.90

        # Gamma alteration toggle state
        self.alter_gamma = tk.BooleanVar(value=True)

        # Bilateral filter toggle
        self.apply_bilateral = tk.BooleanVar(value=True)

        # Gamma-first vs Bilateral-first order toggle
        self.gamma_first = tk.BooleanVar(value=False)

        # MobileSAMv2 parameter controls
        self.conf_thresh_values = [0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5]
        self.conf_thresh_var = tk.StringVar(value="0.3")

        self.iou_thresh_values = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
        self.iou_thresh_var = tk.StringVar(value="0.7")

        self.min_area_values = [50, 100, 150, 250, 300, 350, 400, 450, 500]
        self.min_area_var = tk.StringVar(value="100")

        self.max_area_values = [700, 800, 1000, 1100, 1200, 1300, 1400, 1500, 1600, 1700,
                                1800, 1900, 2000, 2100, 2200, 2300, 2400, 2500, 2600, 2700,
                                2800, 2900, 3000]
        self.max_area_var = tk.StringVar(value="2100")

        self.use_grid_var = tk.BooleanVar(value=True)
        self.remove_inner_region = tk.BooleanVar(value=False)

        # Gamma value selection
        self.gamma_values = [round(0.5 + i * 0.25, 2) for i in range(25)]
        self.gamma_var = tk.StringVar(value="4.0")

        # Precompute all gamma LUTs
        self.gamma_luts = {gamma: self._create_gamma_lut(gamma) for gamma in self.gamma_values}

        # Background Normalisation parameters
        self.bg_norm_sx_values = [10, 15, 20, 25, 30, 35, 40]
        self.bg_norm_sx_var = tk.StringVar(value="10")
        self.bg_norm_sy_values = [10, 15, 20, 25, 30, 35, 40]
        self.bg_norm_sy_var = tk.StringVar(value="10")

        # Contrast Normalisation parameters
        self.apply_contrast_norm = tk.BooleanVar(value=False)
        self.contrast_norm_sx_values = [10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60]
        self.contrast_norm_sx_var = tk.StringVar(value="15")
        self.contrast_norm_sy_values = [10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60]
        self.contrast_norm_sy_var = tk.StringVar(value="15")

        # Step-by-step processing state
        self.current_step = 0
        self.max_steps = 7

        # Setup UI
        self._setup_ui()

        # Load model after UI is ready
        self.root.after(100, self._load_model)

    def _setup_ui(self):
        """Setup the user interface."""
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("TButton", padding=8, font=('Helvetica', 9))
        style.configure("TLabel", background="#2b2b2b", foreground="white", font=('Helvetica', 9))
        style.configure("Header.TLabel", font=('Helvetica', 11, 'bold'))
        style.configure("Status.TLabel", font=('Helvetica', 10))

        # Main container
        main_frame = ttk.Frame(self.root, padding="8")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # ===== TOP CONTROL ROW 1: File/Navigation =====
        control_frame = ttk.Frame(main_frame)
        control_frame.pack(fill=tk.X, pady=(0, 4))

        self.choose_btn = ttk.Button(
            control_frame, text="📂 Choose File/Folder",
            command=self._on_choose, state=tk.DISABLED
        )
        self.choose_btn.pack(side=tk.LEFT, padx=(5, 8))

        self.prev_btn = ttk.Button(
            control_frame, text="← Prev",
            command=self._on_prev, state=tk.DISABLED
        )
        self.prev_btn.pack(side=tk.LEFT, padx=(0, 4))

        self.next_btn = ttk.Button(
            control_frame, text="Next →",
            command=self._on_next, state=tk.DISABLED
        )
        self.next_btn.pack(side=tk.LEFT, padx=(0, 4))

        self.rerun_btn = ttk.Button(
            control_frame, text="🔄 Re-run",
            command=self._on_rerun, state=tk.DISABLED
        )
        self.rerun_btn.pack(side=tk.LEFT, padx=(0, 12))

        # Next Step button
        self.next_step_btn = ttk.Button(
            control_frame, text="▶ Next Step",
            command=self._on_next_step, state=tk.DISABLED
        )
        self.next_step_btn.pack(side=tk.LEFT, padx=(0, 4))

        self.reset_steps_btn = ttk.Button(
            control_frame, text="⏮ Reset",
            command=self._on_reset_steps, state=tk.DISABLED
        )
        self.reset_steps_btn.pack(side=tk.LEFT, padx=(0, 12))

        # Alter Gamma toggle
        self.gamma_btn = ttk.Checkbutton(
            control_frame, text="Gamma",
            variable=self.alter_gamma, style="TCheckbutton"
        )
        self.gamma_btn.pack(side=tk.LEFT, padx=(0, 2))

        self.gamma_dropdown = ttk.Combobox(
            control_frame, textvariable=self.gamma_var,
            values=self.gamma_values, width=4, state="readonly"
        )
        self.gamma_dropdown.pack(side=tk.LEFT, padx=(0, 10))

        # File counter label
        self.counter_label = ttk.Label(control_frame, text="", style="Status.TLabel")
        self.counter_label.pack(side=tk.LEFT, padx=(10, 10))

        # Status label
        self.status_label = ttk.Label(
            control_frame, text="Loading model...",
            style="Header.TLabel", anchor="w"
        )
        self.status_label.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        # ===== TOP CONTROL ROW 2: Parameters =====
        param_frame = ttk.Frame(main_frame)
        param_frame.pack(fill=tk.X, pady=(0, 4))

        # Bilateral filter checkbox
        self.bilateral_btn = ttk.Checkbutton(
            param_frame, text="Bilateral",
            variable=self.apply_bilateral, style="TCheckbutton"
        )
        self.bilateral_btn.pack(side=tk.LEFT, padx=(5, 8))

        # Order toggle button
        self.order_toggle_btn = ttk.Button(
            param_frame, text="Bilateral→Gamma",
            command=self._toggle_processing_order, width=14
        )
        self.order_toggle_btn.pack(side=tk.LEFT, padx=(0, 12))

        # Confidence threshold (replaces IoU for YOLO)
        ttk.Label(param_frame, text="Conf:", style="TLabel").pack(side=tk.LEFT, padx=(0, 2))
        self.conf_dropdown = ttk.Combobox(
            param_frame, textvariable=self.conf_thresh_var,
            values=self.conf_thresh_values, width=4, state="readonly"
        )
        self.conf_dropdown.pack(side=tk.LEFT, padx=(0, 8))

        # IoU threshold for NMS
        ttk.Label(param_frame, text="IoU:", style="TLabel").pack(side=tk.LEFT, padx=(0, 2))
        self.iou_dropdown = ttk.Combobox(
            param_frame, textvariable=self.iou_thresh_var,
            values=self.iou_thresh_values, width=4, state="readonly"
        )
        self.iou_dropdown.pack(side=tk.LEFT, padx=(0, 8))

        # Min area
        ttk.Label(param_frame, text="Area:", style="TLabel").pack(side=tk.LEFT, padx=(0, 2))
        self.min_area_dropdown = ttk.Combobox(
            param_frame, textvariable=self.min_area_var,
            values=self.min_area_values, width=4, state="readonly"
        )
        self.min_area_dropdown.pack(side=tk.LEFT, padx=(0, 8))

        # Max area
        ttk.Label(param_frame, text="Max:", style="TLabel").pack(side=tk.LEFT, padx=(0, 2))
        self.max_area_dropdown = ttk.Combobox(
            param_frame, textvariable=self.max_area_var,
            values=self.max_area_values, width=5, state="readonly"
        )
        self.max_area_dropdown.pack(side=tk.LEFT, padx=(0, 8))

        # Use grid points checkbox
        self.grid_btn = ttk.Checkbutton(
            param_frame, text="Grid+",
            variable=self.use_grid_var, style="TCheckbutton"
        )
        self.grid_btn.pack(side=tk.LEFT, padx=(0, 8))

        # Remove inner region checkbox (create annular masks)
        self.annular_btn = ttk.Checkbutton(
            param_frame, text="Remove inner region",
            variable=self.remove_inner_region, style="TCheckbutton"
        )
        self.annular_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.param_info_label = ttk.Label(
            param_frame, text="", style="Status.TLabel"
        )
        self.param_info_label.pack(side=tk.LEFT, padx=(10, 0))

        # ===== TOP CONTROL ROW 3: Normalisation Parameters =====
        norm_frame = ttk.Frame(main_frame)
        norm_frame.pack(fill=tk.X, pady=(0, 8))

        # Background Normalisation
        ttk.Label(norm_frame, text="BgNorm:", style="TLabel").pack(side=tk.LEFT, padx=(5, 2))
        ttk.Label(norm_frame, text="sx:", style="TLabel").pack(side=tk.LEFT, padx=(0, 1))
        self.bg_norm_sx_dropdown = ttk.Combobox(
            norm_frame, textvariable=self.bg_norm_sx_var,
            values=self.bg_norm_sx_values, width=3, state="readonly"
        )
        self.bg_norm_sx_dropdown.pack(side=tk.LEFT, padx=(0, 4))

        ttk.Label(norm_frame, text="sy:", style="TLabel").pack(side=tk.LEFT, padx=(0, 1))
        self.bg_norm_sy_dropdown = ttk.Combobox(
            norm_frame, textvariable=self.bg_norm_sy_var,
            values=self.bg_norm_sy_values, width=3, state="readonly"
        )
        self.bg_norm_sy_dropdown.pack(side=tk.LEFT, padx=(0, 15))

        # Separator
        ttk.Separator(norm_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        # Contrast Normalisation
        self.contrast_norm_btn = ttk.Checkbutton(
            norm_frame, text="ContrastNorm:",
            variable=self.apply_contrast_norm, style="TCheckbutton"
        )
        self.contrast_norm_btn.pack(side=tk.LEFT, padx=(0, 4))

        ttk.Label(norm_frame, text="sx:", style="TLabel").pack(side=tk.LEFT, padx=(0, 1))
        self.contrast_norm_sx_dropdown = ttk.Combobox(
            norm_frame, textvariable=self.contrast_norm_sx_var,
            values=self.contrast_norm_sx_values, width=3, state="readonly"
        )
        self.contrast_norm_sx_dropdown.pack(side=tk.LEFT, padx=(0, 4))

        ttk.Label(norm_frame, text="sy:", style="TLabel").pack(side=tk.LEFT, padx=(0, 1))
        self.contrast_norm_sy_dropdown = ttk.Combobox(
            norm_frame, textvariable=self.contrast_norm_sy_var,
            values=self.contrast_norm_sy_values, width=3, state="readonly"
        )
        self.contrast_norm_sy_dropdown.pack(side=tk.LEFT, padx=(0, 15))

        # Step info label
        self.step_info_label = ttk.Label(
            norm_frame, text="Step: -", style="Header.TLabel"
        )
        self.step_info_label.pack(side=tk.LEFT, padx=(15, 0))

        # ===== CANVAS (Image Display Area) =====
        self.canvas_frame = ttk.Frame(main_frame)
        self.canvas_frame.pack(fill=tk.BOTH, expand=True)

        self.canvas_width = int(1380 * 0.95)
        self.canvas_height = int(600 * 0.95)

        self.canvas = tk.Canvas(
            self.canvas_frame,
            width=self.canvas_width,
            height=self.canvas_height,
            bg="#1a1a1a",
            highlightthickness=1,
            highlightbackground="#555"
        )
        self.canvas.pack(expand=True)

        # Placeholder text
        self.canvas.create_text(
            self.canvas_width // 2, self.canvas_height // 2,
            text="Select a file or folder to begin",
            fill="#888", font=('Helvetica', 14), tags="placeholder"
        )

        # ===== BOTTOM STATUS FRAME =====
        status_frame = ttk.Frame(main_frame)
        status_frame.pack(fill=tk.X, pady=(8, 0))

        left_status = ttk.Frame(status_frame)
        left_status.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.info_label = ttk.Label(left_status, text="", style="Status.TLabel")
        self.info_label.pack(side=tk.LEFT, padx=(5, 15))

        self.sequence_label = ttk.Label(left_status, text="", style="Status.TLabel")
        self.sequence_label.pack(side=tk.LEFT, padx=(0, 15))

        right_status = ttk.Frame(status_frame)
        right_status.pack(side=tk.RIGHT)

        self.mask_label = ttk.Label(right_status, text="", style="Status.TLabel")
        self.mask_label.pack(side=tk.LEFT, padx=(0, 15))

        self.time_label = ttk.Label(right_status, text="", style="Status.TLabel")
        self.time_label.pack(side=tk.LEFT, padx=(0, 15))

        self.gpu_label = ttk.Label(right_status, text="", style="Status.TLabel")
        self.gpu_label.pack(side=tk.LEFT, padx=(0, 8))
        self._update_gpu_info()

        self.progress = ttk.Progressbar(
            status_frame, mode='indeterminate', length=150
        )
        self.progress.pack(side=tk.RIGHT, padx=8)

    def _update_gpu_info(self):
        """Update GPU memory info."""
        if torch.cuda.is_available():
            allocated = torch.cuda.memory_allocated() / 1024 ** 3
            total = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
            self.gpu_label.config(text=f"GPU: {allocated:.2f}GB / {total:.1f}GB")
        else:
            self.gpu_label.config(text="GPU: N/A (CPU)")

    def _load_model(self):
        """Load the MobileSAMv2 models (MobileSAM + YOLO)."""
        self.progress.start()
        self.status_label.config(text="Loading MobileSAMv2 models...")
        self.root.update()

        try:
            # Validate paths
            if not os.path.exists(self.mobile_sam_path):
                raise FileNotFoundError(f"MobileSAM weights not found: {self.mobile_sam_path}")
            if not os.path.exists(self.yolo_path):
                raise FileNotFoundError(f"YOLO weights not found: {self.yolo_path}")

            print(f"Loading MobileSAM from: {self.mobile_sam_path}")
            print(f"Loading YOLO from: {self.yolo_path}")
            print(f"Device: {self.device}")

            # Enable TF32 for faster computation on Ampere GPUs
            if self.device == "cuda":
                if torch.cuda.get_device_properties(0).major >= 8:
                    torch.backends.cuda.matmul.allow_tf32 = True
                    torch.backends.cudnn.allow_tf32 = True

            # Load MobileSAM
            print("Loading MobileSAM TinyViT encoder...")
            self.sam_model = sam_model_registry["vit_t"](checkpoint=self.mobile_sam_path)
            self.sam_model.to(self.device)
            self.sam_model.eval()

            # Create SAM sam_predictor
            self.sam_predictor = SamPredictor(self.sam_model)

            # Load YOLO model
            print("Loading YOLO ObjectAwareModel...")
            # FIXED: Explicitly force task='detect' to avoid AttributeError in newer ultralytics versions
            # where it misidentifies ObjectAwareModel.pt as a segmentation model but finds no proto layer.
            self.yolo_model = YOLO(self.yolo_path, task='detect')

            # CRITICAL FIX: Patch the model to work in detection mode
            # ObjectAwareModel.pt has segmentation metadata but lacks proto layer
            self._force_yolo_detection_mode(self.yolo_model)

            # Move YOLO to same device
            self.yolo_model.to(self.device)

            # Create mask generator
            self._rebuild_mask_generator()

            self.progress.stop()
            self.progress.pack_forget()
            self.status_label.config(text="✓ MobileSAMv2 loaded - Ready (Much faster!)")
            self.choose_btn.config(state=tk.NORMAL)
            self._update_gpu_info()
            print("MobileSAMv2 models loaded successfully!")

        except Exception as e:
            self.progress.stop()
            self.status_label.config(text=f"Error: {str(e)[:50]}...")
            messagebox.showerror("Model Load Error", str(e))
            print(f"Error loading model: {e}")
            traceback.print_exc()

    def _force_yolo_detection_mode(self, yolo_model):
        """
        Force YOLO model to work in detection mode, even if saved as segmentation.

        ObjectAwareModel.pt was saved with SegmentationModel metadata but lacks proto layer.
        This causes errors during inference when the segmentation sam_predictor/postprocessor
        tries to access the missing proto layer.

        This method:
        1. Patches the model head if it's Segment without proto
        2. Forces the wrapper to use detection sam_predictor/postprocessor
        3. Patches the overrides dict to reflect detection task
        """
        try:
            print("  Forcing YOLO to detection mode...")

            # Step 1: Patch the model head
            if hasattr(yolo_model, 'model') and hasattr(yolo_model.model, 'model'):
                model_layers = yolo_model.model.model

                # Find the head (usually the last layer)
                head = None
                head_index = None
                for i, layer in enumerate(model_layers):
                    if hasattr(layer, '__class__'):
                        class_name = layer.__class__.__name__
                        if 'Segment' in class_name or 'Detect' in class_name:
                            head = layer
                            head_index = i
                            break

                if head and 'Segment' in head.__class__.__name__ and not hasattr(head, 'proto'):
                    print(f"  ⚠ Found Segment head without proto at layer {head_index}")

                    # SOLUTION: Import and use the Detect class's forward method directly
                    # Segment inherits from Detect, so we can just use the parent forward
                    from ultralytics.nn.modules.head import Detect

                    # Get the Detect class forward method
                    detect_forward = Detect.forward

                    # Bind it to our head instance
                    # This effectively makes the Segment head behave like a Detect head
                    import types
                    head.forward = types.MethodType(detect_forward, head)
                    head._patched_for_detection = True

                    print("  ✓ Replaced forward with Detect.forward()")

            # Step 2: Force the task type in overrides
            if hasattr(yolo_model, 'overrides'):
                yolo_model.overrides['task'] = 'detect'
                print("  ✓ Set task='detect' in overrides")

            # Step 3: Force sam_predictor to detection mode
            # The key issue: even with task='detect', the sam_predictor uses segment postprocessor
            # We need to replace the sam_predictor with a detection sam_predictor
            if hasattr(yolo_model, 'sam_predictor') and yolo_model.sam_predictor is not None:
                # Import the detection sam_predictor
                from ultralytics.models.yolo.detect import DetectPredictor

                # Get current sam_predictor's config
                old_predictor = yolo_model.sam_predictor

                # Create new detection sam_predictor with same args
                new_predictor = DetectPredictor(overrides=yolo_model.overrides)

                # Copy important attributes from old sam_predictor
                if hasattr(old_predictor, 'model'):
                    new_predictor.model = old_predictor.model
                if hasattr(old_predictor, 'args'):
                    new_predictor.args = old_predictor.args
                if hasattr(old_predictor, 'device'):
                    new_predictor.device = old_predictor.device

                # Replace the sam_predictor
                yolo_model.sam_predictor = new_predictor
                print("  ✓ Replaced sam_predictor with DetectPredictor")

            # Step 4: Ensure the model knows it's a detection task
            if hasattr(yolo_model, 'task'):
                yolo_model.task = 'detect'
                print("  ✓ Set model.task='detect'")

            # Step 5: Patch the ckpt metadata if present
            if hasattr(yolo_model, 'ckpt') and yolo_model.ckpt:
                if isinstance(yolo_model.ckpt, dict) and 'train_args' in yolo_model.ckpt:
                    if hasattr(yolo_model.ckpt['train_args'], 'task'):
                        yolo_model.ckpt['train_args'].task = 'detect'
                        print("  ✓ Updated checkpoint metadata task")

            print("  ✓ Successfully forced detection mode")

        except Exception as e:
            print(f"  ⚠ Warning during YOLO patching: {e}")
            print("  Continuing anyway...")
            traceback.print_exc()

    def _rebuild_mask_generator(self):
        """Rebuild the mask generator with current parameters."""
        if self.sam_predictor is None or self.yolo_model is None:
            return

        self.mask_generator = MobileSAMv2AutomaticMaskGenerator(
            sam_predictor=self.sam_predictor,
            yolo_model=self.yolo_model,
            conf_threshold=float(self.conf_thresh_var.get()),
            iou_threshold=float(self.iou_thresh_var.get()),
            min_mask_region_area=int(self.min_area_var.get()),
            max_mask_region_area=int(self.max_area_var.get()),
            use_grid_points=self.use_grid_var.get(),
        )

        self.param_info_label.config(
            text=f"Conf={self.conf_thresh_var.get()}, IoU={self.iou_thresh_var.get()}"
        )

    def _on_choose(self):
        """Handle file/folder selection."""
        choice = messagebox.askquestion(
            "Select Type",
            "Select a folder?\n\nYes = Folder (batch)\nNo = Single File",
            icon='question'
        )

        if choice == 'yes':
            folder_path = filedialog.askdirectory(title="Select Folder with Images")
            if folder_path:
                self._load_folder(folder_path)
        else:
            file_path = filedialog.askopenfilename(
                title="Select Image",
                filetypes=[
                    ("Image files", "*.png *.jpg *.jpeg *.PNG *.JPG *.JPEG"),
                    ("All files", "*.*")
                ]
            )
            if file_path:
                self._load_file(file_path)

    def _load_folder(self, folder_path: str):
        """Load all images from a folder."""
        extensions = ['*.png', '*.jpg', '*.jpeg', '*.PNG', '*.JPG', '*.JPEG']
        self.current_files = []
        for ext in extensions:
            self.current_files.extend(glob.glob(os.path.join(folder_path, ext)))
        self.current_files = sorted(list(set(self.current_files)))

        if not self.current_files:
            messagebox.showinfo("No Images", "No image files found.")
            return

        self.current_index = 0
        self._update_counter()
        self._run_full_pipeline()

    def _load_file(self, file_path: str):
        """Load a single file."""
        file_path = os.path.normpath(file_path)
        folder_path = os.path.dirname(file_path)

        extensions = ['*.png', '*.jpg', '*.jpeg', '*.PNG', '*.JPG', '*.JPEG']
        self.current_files = []
        for ext in extensions:
            self.current_files.extend(glob.glob(os.path.join(folder_path, ext)))
        self.current_files = sorted([os.path.normpath(f) for f in set(self.current_files)])

        self.current_index = 0
        for i, f in enumerate(self.current_files):
            if f == file_path:
                self.current_index = i
                break

        self._update_counter()
        self._run_full_pipeline()

    def _update_counter(self):
        """Update the file counter display."""
        total = len(self.current_files)
        current = self.current_index + 1
        self.counter_label.config(text=f"Image {current}/{total}")

        self.next_btn.config(state=tk.NORMAL if self.current_index < total - 1 else tk.DISABLED)
        self.prev_btn.config(state=tk.NORMAL if self.current_index > 0 else tk.DISABLED)
        if total > 0:
            self.rerun_btn.config(state=tk.NORMAL)

    def _on_next(self):
        """Go to next image and run full pipeline."""
        if self.current_index < len(self.current_files) - 1:
            self.current_index += 1
            self._update_counter()
            self._run_full_pipeline()

    def _on_prev(self):
        """Go to previous image and run full pipeline."""
        if self.current_index > 0:
            self.current_index -= 1
            self._update_counter()
            self._run_full_pipeline()

    def _on_rerun(self):
        """Re-run full pipeline on current image."""
        if self.current_files:
            self._rebuild_mask_generator()
            self._run_full_pipeline()

    def _create_gamma_lut(self, gamma: float) -> np.ndarray:
        """Create a lookup table for gamma correction."""
        inv_gamma = 1.0 / gamma
        lut = np.array([((i / 255.0) ** inv_gamma) * 255
                        for i in range(256)], dtype=np.uint8)
        return lut

    def _toggle_processing_order(self):
        """Toggle between Gamma-first and Bilateral-first."""
        current = self.gamma_first.get()
        self.gamma_first.set(not current)
        if self.gamma_first.get():
            self.order_toggle_btn.config(text="Gamma→Bilateral")
        else:
            self.order_toggle_btn.config(text="Bilateral→Gamma")

    def _apply_gamma(self, image: np.ndarray) -> np.ndarray:
        """Apply gamma correction using precomputed LUT."""
        gamma = float(self.gamma_var.get())
        return self.gamma_luts[gamma][image]

    def _apply_bilateral_filter(self, image: np.ndarray, d: int = 21,
                                sigmaColor: int = 30, sigmaSpace: int = 30) -> np.ndarray:
        """Apply bilateral filter."""
        return cv2.bilateralFilter(image, d=d, sigmaColor=sigmaColor, sigmaSpace=sigmaSpace)

    def _to_rgb(self, image: np.ndarray) -> np.ndarray:
        """Convert grayscale to RGB if needed."""
        if image.ndim == 2:
            return cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
        return image

    def _to_gray(self, image: np.ndarray) -> np.ndarray:
        """Convert RGB to grayscale if needed."""
        if image.ndim == 3:
            return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        return image

    def _run_full_pipeline(self):
        """Run the complete pipeline and show original vs final result."""
        if not self.current_files:
            return

        file_path = self.current_files[self.current_index]
        filename = os.path.basename(file_path)

        self.status_label.config(text=f"Processing: {filename}")
        self.time_label.config(text="Processing...")
        self.root.update()

        try:
            # Rebuild mask generator with current parameter values
            # This ensures conf, iou, min_area, and grid settings are up to date
            self._rebuild_mask_generator()

            start_time = time.perf_counter()

            if torch.cuda.is_available():
                torch.cuda.empty_cache()

            # Load image
            image_bgr = cv2.imread(file_path)
            if image_bgr is None:
                raise ValueError(f"Could not load image: {file_path}")

            image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
            original_size = (image_rgb.shape[1], image_rgb.shape[0])

            # Step 1: Extract region (full disk or annular based on checkbox)
            # Use inner_radius=0 for full disk, actual inner_radius for annular
            inner_rad = self.annular_inner_radius if self.remove_inner_region.get() else 0

            annular_image, annular_mask = extract_annular_region(
                image_rgb,
                center=self.annular_center,
                outer_radius=self.annular_outer_radius,
                inner_radius=inner_rad,
                fill_color=0
            )

            self.current_annular_mask = annular_mask
            self.pipeline_images = {
                'step1_annular': annular_image.copy(),
                'mask': annular_mask,
                'original': annular_image.copy()
            }

            # Step 2: Background Normalisation
            bg_sx = int(self.bg_norm_sx_var.get())
            bg_sy = int(self.bg_norm_sy_var.get())
            bg_normalized = pixBackgroundNorm_masked(
                annular_image, annular_mask, sx=bg_sx, sy=bg_sy
            )
            self.pipeline_images['step2_bg_norm'] = bg_normalized

            # Step 3 & 4: Bilateral + Gamma (order depends on toggle)
            if self.gamma_first.get():
                # Gamma first
                if self.alter_gamma.get():
                    step3_result = self._apply_gamma(bg_normalized)
                    self.pipeline_images['step3'] = step3_result
                    self.pipeline_images['step3_name'] = f"Gamma {self.gamma_var.get()}"
                else:
                    step3_result = bg_normalized.copy()
                    self.pipeline_images['step3'] = step3_result
                    self.pipeline_images['step3_name'] = "Gamma (skipped)"

                # Bilateral second
                if self.apply_bilateral.get():
                    step4_result = self._apply_bilateral_filter(step3_result, d=21, sigmaColor=30, sigmaSpace=30)
                    self.pipeline_images['step4'] = step4_result
                    self.pipeline_images['step4_name'] = "Bilateral (21,30,30)"
                else:
                    step4_result = step3_result.copy()
                    self.pipeline_images['step4'] = step4_result
                    self.pipeline_images['step4_name'] = "Bilateral (skipped)"
            else:
                # Bilateral first
                if self.apply_bilateral.get():
                    step3_result = self._apply_bilateral_filter(bg_normalized, d=21, sigmaColor=30, sigmaSpace=30)
                    self.pipeline_images['step3'] = step3_result
                    self.pipeline_images['step3_name'] = "Bilateral (21,30,30)"
                else:
                    step3_result = bg_normalized.copy()
                    self.pipeline_images['step3'] = step3_result
                    self.pipeline_images['step3_name'] = "Bilateral (skipped)"

                # Gamma second
                if self.alter_gamma.get():
                    step4_result = self._apply_gamma(step3_result)
                    self.pipeline_images['step4'] = step4_result
                    self.pipeline_images['step4_name'] = f"Gamma {self.gamma_var.get()}"
                else:
                    step4_result = step3_result.copy()
                    self.pipeline_images['step4'] = step4_result
                    self.pipeline_images['step4_name'] = "Gamma (skipped)"

            # Step 5: Contrast Normalisation (optional)
            if self.apply_contrast_norm.get():
                cn_sx = int(self.contrast_norm_sx_var.get())
                cn_sy = int(self.contrast_norm_sy_var.get())
                gray_image = self._to_gray(step4_result)
                step5_result = pixContrastNorm_masked(gray_image, annular_mask, sx=cn_sx, sy=cn_sy)
                self.pipeline_images['step5'] = step5_result
                self.pipeline_images['step5_name'] = f"ContrastNorm (sx={cn_sx}, sy={cn_sy})"
            else:
                step5_result = self._to_gray(step4_result) if step4_result.ndim == 3 else step4_result.copy()
                self.pipeline_images['step5'] = step5_result
                self.pipeline_images['step5_name'] = "ContrastNorm (skipped)"

            # Step 6: Final Bilateral (21, 30, 40)
            step6_result = self._apply_bilateral_filter(step5_result, d=21, sigmaColor=30, sigmaSpace=40)
            self.pipeline_images['step6'] = step6_result
            self.pipeline_images['step6_name'] = "Bilateral (21,30,40)"

            # Step 7: MobileSAMv2 Segmentation
            image_for_sam = self._to_rgb(step6_result)

            with torch.inference_mode():
                masks = self.mask_generator.generate(image_for_sam)

            # Create visualization
            result_image = self._create_mask_overlay(image_for_sam, masks)
            self.pipeline_images['step7_sam'] = result_image

            end_time = time.perf_counter()
            processing_time = end_time - start_time

            # Update display - show original annular vs final SAM result
            self.current_step = 0
            self._display_comparison(
                self.pipeline_images['original'],
                result_image,
                "Original (Annular)",
                "MobileSAMv2 Segmentation"
            )

            # Update labels
            h, w = annular_image.shape[:2]
            self.info_label.config(
                text=f"Original: {original_size[0]}×{original_size[1]} → Annular: {w}×{h}"
            )
            self.time_label.config(text=f"⏱ {processing_time:.2f}s")
            self.mask_label.config(text=f"Masks: {len(masks)}")
            self.status_label.config(text=f"✓ {filename} - Complete")
            self.step_info_label.config(text=f"Step 7/7: MobileSAMv2")

            # Enable navigation
            self.next_step_btn.config(state=tk.NORMAL)
            self.reset_steps_btn.config(state=tk.DISABLED)

            self._update_gpu_info()

            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        except Exception as e:
            self.status_label.config(text=f"Error: {str(e)[:50]}...")
            messagebox.showerror("Processing Error", str(e))
            print(f"Error: {e}")
            traceback.print_exc()

    def _on_next_step(self):
        """Advance to the next pipeline step."""
        if self.current_step < self.max_steps:
            self.current_step += 1
            self._display_step()

    def _on_reset_steps(self):
        """Reset to step 0 (before stepping through)."""
        self.current_step = 0
        # Show the original comparison view
        if self.pipeline_images.get('original') is not None:
            self._display_comparison(
                self.pipeline_images['original'],
                self.pipeline_images.get('step7_sam'),
                "Original (Annular)",
                "MobileSAMv2 Segmentation"
            )
        self.step_info_label.config(text="Step: Ready to step through")
        self.next_step_btn.config(state=tk.NORMAL)
        self.reset_steps_btn.config(state=tk.DISABLED)

    def _display_step(self):
        """Display the current step with left/right panels."""
        step = self.current_step
        filename = os.path.basename(self.current_files[self.current_index]) if self.current_files else ""

        if step == 1:
            left_img = self.pipeline_images.get('step1_annular')
            right_img = None
            left_label = "Step 1: Annular Disc"
            right_label = ""
            step_desc = "Annular Disc (center=635,345, R_out=250, R_in=120)"

        elif step == 2:
            left_img = self.pipeline_images.get('step1_annular')
            right_img = self.pipeline_images.get('step2_bg_norm')
            left_label = "Step 1: Annular Disc"
            bg_sx = self.bg_norm_sx_var.get()
            bg_sy = self.bg_norm_sy_var.get()
            right_label = f"Step 2: BgNorm (sx={bg_sx}, sy={bg_sy})"
            step_desc = f"Background Normalisation (sx={bg_sx}, sy={bg_sy})"

        elif step == 3:
            left_img = self.pipeline_images.get('step2_bg_norm')
            right_img = self.pipeline_images.get('step3')
            left_label = "Step 2: BgNorm"
            right_label = f"Step 3: {self.pipeline_images.get('step3_name', '')}"
            step_desc = self.pipeline_images.get('step3_name', '')

        elif step == 4:
            left_img = self.pipeline_images.get('step3')
            right_img = self.pipeline_images.get('step4')
            left_label = f"Step 3: {self.pipeline_images.get('step3_name', '')}"
            right_label = f"Step 4: {self.pipeline_images.get('step4_name', '')}"
            step_desc = self.pipeline_images.get('step4_name', '')

        elif step == 5:
            left_img = self.pipeline_images.get('step4')
            right_img = self.pipeline_images.get('step5')
            left_label = f"Step 4: {self.pipeline_images.get('step4_name', '')}"
            right_label = f"Step 5: {self.pipeline_images.get('step5_name', '')}"
            step_desc = self.pipeline_images.get('step5_name', '')

        elif step == 6:
            left_img = self.pipeline_images.get('step5')
            right_img = self.pipeline_images.get('step6')
            left_label = f"Step 5: {self.pipeline_images.get('step5_name', '')}"
            right_label = f"Step 6: {self.pipeline_images.get('step6_name', '')}"
            step_desc = self.pipeline_images.get('step6_name', '')

        elif step == 7:
            left_img = self.pipeline_images.get('step6')
            right_img = self.pipeline_images.get('step7_sam')
            left_label = f"Step 6: {self.pipeline_images.get('step6_name', '')}"
            right_label = "Step 7: MobileSAMv2"
            step_desc = "MobileSAMv2 Segmentation"

        else:
            return

        # Display images
        self._display_left_right(left_img, right_img, left_label, right_label)

        # Update step info
        self.step_info_label.config(text=f"Step {step}/{self.max_steps}: {step_desc}")
        self.status_label.config(text=f"✓ {filename} - Step {step}/{self.max_steps}")

        # Update button states
        self.next_step_btn.config(state=tk.NORMAL if step < self.max_steps else tk.DISABLED)
        self.reset_steps_btn.config(state=tk.NORMAL)  # Enable reset once stepping has started

    def _display_comparison(self, left_img: np.ndarray, right_img: np.ndarray,
                            left_label: str, right_label: str):
        """Display two images side by side for comparison."""
        self._display_left_right(left_img, right_img, left_label, right_label)

    def _display_left_right(self, left_img: np.ndarray, right_img: np.ndarray,
                            left_label: str, right_label: str):
        """Display two images side by side with labels."""
        self.canvas.delete("all")

        if left_img is None:
            return

        # Convert to RGB if grayscale
        left_rgb = self._to_rgb(left_img)

        # Scale images
        h, w = left_rgb.shape[:2]
        new_w = int(w * self.display_scale)
        new_h = int(h * self.display_scale)

        left_scaled = cv2.resize(left_rgb, (new_w, new_h), interpolation=cv2.INTER_AREA)

        # Create PhotoImage for left
        pil_left = Image.fromarray(left_scaled)
        self.left_photo = ImageTk.PhotoImage(pil_left)

        # Calculate positions
        gap = 20
        canvas_w = self.canvas.winfo_width()
        canvas_h = self.canvas.winfo_height()

        if right_img is not None:
            right_rgb = self._to_rgb(right_img)
            right_scaled = cv2.resize(right_rgb, (new_w, new_h), interpolation=cv2.INTER_AREA)
            pil_right = Image.fromarray(right_scaled)
            self.right_photo = ImageTk.PhotoImage(pil_right)

            total_width = new_w * 2 + gap
            x_start = max(0, (canvas_w - total_width) // 2)
            y = max(20, (canvas_h - new_h) // 2)

            # Draw left image
            self.canvas.create_image(x_start, y, anchor=tk.NW, image=self.left_photo)
            self.canvas.create_text(x_start + new_w // 2, y - 8,
                                    text=left_label, fill="#aaa", font=('Helvetica', 9))

            # Draw right image
            x_right = x_start + new_w + gap
            self.canvas.create_image(x_right, y, anchor=tk.NW, image=self.right_photo)
            self.canvas.create_text(x_right + new_w // 2, y - 8,
                                    text=right_label, fill="#aaa", font=('Helvetica', 9))
        else:
            # Single image centered
            x = max(0, (canvas_w - new_w) // 2)
            y = max(20, (canvas_h - new_h) // 2)
            self.canvas.create_image(x, y, anchor=tk.NW, image=self.left_photo)
            self.canvas.create_text(x + new_w // 2, y - 8,
                                    text=left_label, fill="#aaa", font=('Helvetica', 9))

    def _classify_mask_shape(self, mask: np.ndarray) -> Dict[str, Any]:
        """
        Classify a mask as circle or ellipse using OpenCV fitEllipse.

        Args:
            mask: Binary mask (H, W)

        Returns:
            Dictionary with shape classification info:
            {
                'type': 'circle' or 'ellipse' or 'other',
                'radius': float (for circles) or None,
                'major_axis': float (for ellipses) or None,
                'minor_axis': float (for ellipses) or None,
                'aspect_ratio': float,
                'center': (x, y) or None
            }
        """
        # Convert to uint8 for OpenCV
        mask_uint8 = mask.astype(np.uint8) * 255

        # Find contours
        contours, _ = cv2.findContours(mask_uint8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if not contours:
            return {'type': 'other', 'radius': None, 'major_axis': None,
                    'minor_axis': None, 'aspect_ratio': 0, 'center': None}

        # Get the largest contour (main object)
        largest_contour = max(contours, key=cv2.contourArea)

        # Need at least 5 points to fit an ellipse
        if len(largest_contour) < 5:
            return {'type': 'other', 'radius': None, 'major_axis': None,
                    'minor_axis': None, 'aspect_ratio': 0, 'center': None}

        try:
            # Fit ellipse to contour
            (cx, cy), (minor, major), angle = cv2.fitEllipse(largest_contour)

            # Ensure major >= minor (OpenCV can return them in either order)
            if minor > major:
                major, minor = minor, major

            # Avoid division by zero
            if major == 0:
                return {'type': 'other', 'radius': None, 'major_axis': None,
                        'minor_axis': None, 'aspect_ratio': 0, 'center': None}

            # Calculate aspect ratio (minor/major)
            aspect_ratio = minor / major

            # Calculate average radius for circles
            avg_radius = (major + minor) / 4.0  # Diameter to radius

            # Classify based on aspect ratio and radius
            # Different thresholds based on size
            if avg_radius < 10:
                # Smaller objects: stricter ellipse criteria
                is_ellipse = (major / minor) > MAJOR_MINOR_AXIS_CUTOFF_IF_LESS_THAN_10
            elif avg_radius < 20:
                # Medium objects
                is_ellipse = (major / minor) > MAJOR_MINOR_AXIS_CUTOFF_IF_LESS_THAN_20
            elif avg_radius < 40:
                # Medium objects
                is_ellipse = (major / minor) > MAJOR_MINOR_AXIS_CUTOFF_IF_LESS_THAN_40
            else:
                # Larger objects
                is_ellipse = (major / minor) > MAJOR_MINOR_AXIS_CUTOFF_FOR_OTHERS

            if is_ellipse:
                return {
                    'type': 'ellipse',
                    'radius': None,
                    'major_axis': major / 2.0,  # Convert diameter to radius
                    'minor_axis': minor / 2.0,
                    'aspect_ratio': aspect_ratio,
                    'center': (cx, cy)
                }
            else:
                return {
                    'type': 'circle',
                    'radius': avg_radius,
                    'major_axis': None,
                    'minor_axis': None,
                    'aspect_ratio': aspect_ratio,
                    'center': (cx, cy)
                }

        except Exception as e:
            # If fitEllipse fails, mark as other
            return {'type': 'other', 'radius': None, 'major_axis': None,
                    'minor_axis': None, 'aspect_ratio': 0, 'center': None}

    def _group_masks_by_size(self, masks: List[Dict[str, Any]]) -> Dict[int, List[int]]:
        """
        Group masks by shape type and size using median-based grouping algorithm.

        Args:
            masks: List of mask dictionaries with 'shape_info' added

        Returns:
            Dictionary mapping group_id to list of mask indices
        """
        groups = {}
        group_id = 0

        # Separate circles and ellipses
        circles = []  # (mask_index, radius)
        ellipses = []  # (mask_index, major_axis)
        others = []

        for idx, mask_data in enumerate(masks):
            shape_info = mask_data.get('shape_info', {})
            shape_type = shape_info.get('type', 'other')

            if shape_type == 'circle':
                circles.append((idx, shape_info['radius']))
            elif shape_type == 'ellipse':
                ellipses.append((idx, shape_info['major_axis']))
            else:
                others.append(idx)

        # Group circles
        if len(circles) > 0:
            circle_radii = [r for _, r in circles]

            print("\n" + "=" * 60)
            print("CIRCLES - Radii in ascending order:")
            sorted_radii = sorted(circle_radii)
            print(f"  {sorted_radii}")
            print(f"  Count: {len(sorted_radii)}")

            if len(circles) == 1:
                groups[group_id] = [circles[0][0]]
                group_id += 1
                print("\nCIRCLE GROUPINGS:")
                print(f"  Group 0: [radius={circle_radii[0]:.2f}]")
            else:
                # Determine percentage threshold based on median radius
                median_radius = np.median(circle_radii)
                if median_radius < 10:
                    percentage = GROUPING_RADIUS_VARIANCE_PERCENTAGE_LESS_THAN_10
                elif median_radius < 20:
                    percentage = GROUPING_RADIUS_VARIANCE_PERCENTAGE_LESS_THAN_20
                elif median_radius < 40:
                    percentage = GROUPING_RADIUS_VARIANCE_PERCENTAGE_LESS_THAN_40
                else:
                    percentage = GROUPING_RADIUS_VARIANCE_PERCENTAGE_MORE_THAN_40

                print(f"  Median radius: {median_radius:.2f}")
                print(f"  Percentage threshold: {percentage * 100:.1f}%")

                # Use custom grouping algorithm
                circle_groups, circle_medians = group_numbers_with_indices(circles, percentage)

                print(f"\nCIRCLE GROUPINGS (Found {len(circle_groups)} groups):")
                for i, grp_indices in enumerate(circle_groups):
                    radii_in_group = [masks[idx]['shape_info']['radius'] for idx in grp_indices]
                    radii_sorted = sorted(radii_in_group)
                    print(f"  Group {i}: {len(grp_indices)} circles, radii={radii_sorted}")

                # Map to output groups
                for grp_indices in circle_groups:
                    groups[group_id] = grp_indices
                    group_id += 1

        # Group ellipses
        if len(ellipses) > 0:
            ellipse_major = [m for _, m in ellipses]

            print("\n" + "=" * 60)
            print("ELLIPSES - Major axes in ascending order:")
            sorted_major = sorted(ellipse_major)
            print(f"  {sorted_major}")
            print(f"  Count: {len(sorted_major)}")

            if len(ellipses) == 1:
                groups[group_id] = [ellipses[0][0]]
                group_id += 1
                print("\nELLIPSE GROUPINGS:")
                print(f"  Group 0: [major_axis={ellipse_major[0]:.2f}]")
            else:
                # Determine percentage threshold based on median major axis
                median_major = np.median(ellipse_major)
                if median_major < 10:
                    percentage = GROUPING_RADIUS_VARIANCE_PERCENTAGE_LESS_THAN_10
                elif median_major < 20:
                    percentage = GROUPING_RADIUS_VARIANCE_PERCENTAGE_LESS_THAN_20
                elif median_major < 40:
                    percentage = GROUPING_RADIUS_VARIANCE_PERCENTAGE_LESS_THAN_40
                else:
                    percentage = GROUPING_RADIUS_VARIANCE_PERCENTAGE_MORE_THAN_40

                print(f"  Median major axis: {median_major:.2f}")
                print(f"  Percentage threshold: {percentage * 100:.1f}%")

                # Use custom grouping algorithm
                ellipse_groups, ellipse_medians = group_numbers_with_indices(ellipses, percentage)

                print(f"\nELLIPSE GROUPINGS (Found {len(ellipse_groups)} groups):")
                for i, grp_indices in enumerate(ellipse_groups):
                    major_in_group = [masks[idx]['shape_info']['major_axis'] for idx in grp_indices]
                    major_sorted = sorted(major_in_group)
                    print(f"  Group {i}: {len(grp_indices)} ellipses, major_axes={major_sorted}")
                print("=" * 60 + "\n")

                # Map to output groups
                for grp_indices in ellipse_groups:
                    groups[group_id] = grp_indices
                    group_id += 1

        # Add 'other' shapes as individual groups
        for idx in others:
            groups[group_id] = [idx]
            group_id += 1

        return groups

    def get_final_count(self) -> int:
        """
        Get the final hole count from the last processed image.

        Returns:
            The count of holes in the winning group, or 0 if not processed
        """
        return getattr(self, '_winning_count', 0)

    def get_winning_group_id(self) -> Optional[int]:
        """
        Get the ID of the winning group from the last processed image.

        Returns:
            The winning group ID, or None if not processed
        """
        return getattr(self, '_winning_group_id', None)

    def get_hole_count_for_qr(self, masks: List[Dict[str, Any]],
                              groups: Dict[int, List[int]],
                              qr_code: str) -> Tuple[int, str, Optional[int]]:
        """
        Get the maximum hole count at model-specific distances.

        Args:
            masks: List of mask dictionaries
            groups: Final groupings from _subgroup_by_distance_from_center
            qr_code: QR code string

        Returns:
            Tuple of (max_count, model_name, matching_group_id)
        """
        # Center coordinates (same as used in _subgroup_by_distance_from_center)
        center_x = self.annular_outer_radius
        center_y = self.annular_outer_radius

        return get_max_group_count_for_model(masks, groups, qr_code, center_x, center_y)

    def _subgroup_by_distance_from_center(self, masks: List[Dict[str, Any]],
                                          groups: Dict[int, List[int]]) -> Dict[int, List[int]]:
        """
        Further subdivide each size-based group by distance from center.

        Args:
            masks: List of mask dictionaries with 'shape_info' added
            groups: Dictionary from _group_masks_by_size (group_id -> mask_indices)

        Returns:
            New dictionary with sub-groups (each sub-group gets unique ID and color)
        """
        final_groups = {}
        final_group_id = 0

        # Center coordinates for distance calculation
        center_x = self.annular_outer_radius
        center_y = self.annular_outer_radius

        for group_id, mask_indices in groups.items():
            if len(mask_indices) == 0:
                continue

            if len(mask_indices) == 1:
                final_groups[final_group_id] = mask_indices
                final_group_id += 1
                continue

            # Calculate distance from center for each mask in this group
            index_distance_pairs = []
            for mask_idx in mask_indices:
                shape_info = masks[mask_idx]['shape_info']
                mask_center = shape_info.get('center')

                if mask_center is None:
                    distance = 0.0
                else:
                    cx, cy = mask_center
                    distance = np.sqrt((cx - center_x) ** 2 + (cy - center_y) ** 2)

                index_distance_pairs.append((mask_idx, distance))

            # Use custom grouping algorithm
            sub_groups, sub_medians = group_numbers_with_indices(
                index_distance_pairs,
                PERCENTAGE_SPREAD_FOR_GROUPING_FROM_CENTER
            )

            # ===== DEBUG PRINT =====
            shape_type = masks[mask_indices[0]]['shape_info']['type']
            size_value = masks[mask_indices[0]]['shape_info'].get('radius') or \
                         masks[mask_indices[0]]['shape_info'].get('major_axis')

            distances = [d for _, d in index_distance_pairs]
            median_distance = np.median(distances)

            print(f"\n--- Sub-grouping by distance (Size group {group_id}, {shape_type}, size≈{size_value:.1f}) ---")
            print(f"  Distances from center: {sorted(distances)}")
            print(f"  Median distance: {median_distance:.2f}")
            print(f"  Percentage threshold: {PERCENTAGE_SPREAD_FOR_GROUPING_FROM_CENTER * 100:.1f}%")
            print(f"  Found {len(sub_groups)} sub-groups:")
            # ===== END DEBUG =====

            # Map sub-groups to final groups
            for sub_grp_indices in sub_groups:
                distances_in_subgroup = [
                    np.sqrt((masks[idx]['shape_info']['center'][0] - center_x) ** 2 +
                            (masks[idx]['shape_info']['center'][1] - center_y) ** 2)
                    if masks[idx]['shape_info'].get('center') else 0.0
                    for idx in sub_grp_indices
                ]

                # ===== DEBUG PRINT =====
                print(f"    Sub-group {final_group_id}: {len(sub_grp_indices)} masks, "
                      f"distances={sorted(distances_in_subgroup)}")
                # ===== END DEBUG =====

                final_groups[final_group_id] = sub_grp_indices
                final_group_id += 1

        return final_groups

    def _create_mask_overlay(self, image: np.ndarray, masks: List[Dict[str, Any]]) -> np.ndarray:
        """Create mask overlay visualization with shape-based grouping and coloring."""
        print(f"\n[DEBUG] Total masks received: {len(masks)}")

        result = image.copy().astype(np.float32)

        if not masks:
            return result.astype(np.uint8)

        # Step 1: Classify all masks as circle/ellipse/other
        for mask_data in masks:
            shape_info = self._classify_mask_shape(mask_data['segmentation'])
            mask_data['shape_info'] = shape_info

        # Step 1.5: Filter out ill-shaped masks based on area ratio
        masks = filter_masks_by_area_ratio(masks)

        # Update result image size check after filtering
        if not masks:
            return result.astype(np.uint8)

        # Step 2: Group masks by shape and size
        size_groups = self._group_masks_by_size(masks)

        # Step 3: Sub-group by distance from center
        groups = self._subgroup_by_distance_from_center(masks, size_groups)

        # DEBUG: Verify all masks are in groups
        all_grouped_indices = set()
        for grp_indices in groups.values():
            all_grouped_indices.update(grp_indices)
        print(f"[DEBUG] Masks in groups: {len(all_grouped_indices)} / {len(masks)}")
        print(f"[DEBUG] Missing indices: {set(range(len(masks))) - all_grouped_indices}")

        # Center coordinates for filtering
        center_x = self.annular_outer_radius
        center_y = self.annular_outer_radius

        # Step 3.5a: Filter out small groups (< 3 masks)
        groups = filter_small_groups(groups, min_size=3)

        # Step 3.5b: Filter by center envelope
        groups = filter_subgroups_by_center_envelope(masks, groups, center_x, center_y)

        # Step 3.5c: Filter by equispacing
        groups, fallback_winner = filter_subgroups_by_equispacing(
            masks, groups, center_x, center_y
        )

        # Step 3.5d: Select winning group
        winning_group_id, winning_count = select_winning_group(groups)

        print(f"\n[RESULT] Winning group: {winning_group_id}, Count: {winning_count}")

        # Store for external access
        self._last_masks = masks
        self._last_groups = groups
        self._winning_group_id = winning_group_id
        self._winning_count = winning_count

        # If no groups remain, return original image
        if not groups:
            print("[WARNING] No groups passed all filters!")
            return result.astype(np.uint8)

        # Step 4: Generate colors for each group
        # Use bright green for winning group, dimmer colors for others
        group_colors = {}
        WINNING_COLOR = (0, 255, 0)  # Bright green

        for i, group_id in enumerate(sorted(groups.keys())):
            if group_id == winning_group_id:
                group_colors[group_id] = WINNING_COLOR
            else:
                # Use golden ratio for other colors (dimmer)
                hue = (i * 0.618033988749895) % 1.0
                h = hue * 6.0
                c = 0.5  # Dimmer
                x = c * (1 - abs(h % 2 - 1))

                if h < 1:
                    rgb = (c, x, 0)
                elif h < 2:
                    rgb = (x, c, 0)
                elif h < 3:
                    rgb = (0, c, x)
                elif h < 4:
                    rgb = (0, x, c)
                elif h < 5:
                    rgb = (x, 0, c)
                else:
                    rgb = (c, 0, x)

                group_colors[group_id] = tuple(int((v + 0.2) * 150) for v in rgb)

        # Step 5: Create mask-to-group mapping (only for winning groups)
        mask_to_group = {}
        for group_id, mask_indices in groups.items():
            for mask_idx in mask_indices:
                mask_to_group[mask_idx] = group_id

        # Step 6: Render masks with group colors
        overlay = np.zeros_like(result)

        # Only render masks that are in the filtered groups
        for orig_idx, mask_data in enumerate(masks):
            if orig_idx not in mask_to_group:
                continue  # Skip masks not in winning groups

            mask = mask_data['segmentation']
            group_id = mask_to_group[orig_idx]
            color = group_colors[group_id]

            for c_idx in range(3):
                overlay[:, :, c_idx] = np.where(mask, color[c_idx], overlay[:, :, c_idx])

        # Step 7: Blend overlay with original image
        alpha = 0.5
        mask_present = overlay.sum(axis=2) > 0

        for c_idx in range(3):
            result[:, :, c_idx] = np.where(
                mask_present,
                result[:, :, c_idx] * (1 - alpha) + overlay[:, :, c_idx] * alpha,
                result[:, :, c_idx]
            )

        # Step 8: Draw contours with group colors
        for orig_idx, mask_data in enumerate(masks):
            if orig_idx not in mask_to_group:
                continue  # Skip masks not in winning groups

            mask = mask_data['segmentation'].astype(np.uint8)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            group_id = mask_to_group[orig_idx]
            color = group_colors[group_id]

            cv2.drawContours(result, contours, -1, color, 2)

        # Step 9: Add count text to image
        if winning_count > 0:
            text = f"COUNT: {winning_count}"
            font = cv2.FONT_HERSHEY_SIMPLEX
            font_scale = 1.0
            thickness = 2
            text_size = cv2.getTextSize(text, font, font_scale, thickness)[0]
            text_x = 10
            text_y = 30
            cv2.putText(result, text, (text_x, text_y), font, font_scale, (0, 255, 0), thickness)

        return np.clip(result, 0, 255).astype(np.uint8)

def find_mobilesam_paths():
    """Find MobileSAM checkpoint and YOLO paths using project root."""
    # Get the MobileSAM directory from project root
    mobilesam_base = os.path.join(_PROJECT_ROOT, "models", "MobileSAM")

    # Primary paths based on project structure
    mobile_sam_paths = [
        os.path.join(mobilesam_base, "weights", "mobile_sam.pt"),
        # Fallback paths for other configurations
        "weights/mobile_sam.pt",
        "../MobileSAM/weights/mobile_sam.pt",
        "C:/MobileSAM/weights/mobile_sam.pt",
    ]

    # Common locations for YOLO ObjectAwareModel
    yolo_paths = [
        os.path.join(mobilesam_base, "MobileSAMv2", "weight", "ObjectAwareModel.pt"),
        # Fallback paths for other configurations
        "MobileSAMv2/weight/ObjectAwareModel.pt",
        "../MobileSAM/MobileSAMv2/weight/ObjectAwareModel.pt",
        "C:/MobileSAM/MobileSAMv2/weight/ObjectAwareModel.pt",
    ]

    mobile_sam_path = None
    for path in mobile_sam_paths:
        if os.path.exists(path):
            mobile_sam_path = path
            break

    yolo_path = None
    for path in yolo_paths:
        if os.path.exists(path):
            yolo_path = path
            break

    # Allow command line override
    if len(sys.argv) > 1:
        mobile_sam_path = sys.argv[1]
    if len(sys.argv) > 2:
        yolo_path = sys.argv[2]

    if mobile_sam_path is None:
        raise FileNotFoundError(
            "Could not find mobile_sam.pt. Please ensure it's in:\n"
            "  MobileSAM/weights/mobile_sam.pt\n"
            "Or pass the path as first argument."
        )

    if yolo_path is None:
        raise FileNotFoundError(
            "Could not find ObjectAwareModel.pt. Please ensure it's in:\n"
            "  MobileSAM/MobileSAMv2/weight/ObjectAwareModel.pt\n"
            "Or pass the path as second argument."
        )

    return mobile_sam_path, yolo_path


def main():
    """Main entry point."""
    print("=" * 60)
    print("MobileSAMv2 Segmentation Viewer v1.0")
    print("Much faster than SAM2 - Optimized for 4GB GPUs!")
    print("=" * 60)

    if torch.cuda.is_available():
        gpu_name = torch.cuda.get_device_name(0)
        gpu_mem = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
        print(f"GPU: {gpu_name} ({gpu_mem:.1f}GB)")
    else:
        print("WARNING: CUDA not available, using CPU")

    try:
        mobile_sam_path, yolo_path = find_mobilesam_paths()
        print(f"MobileSAM: {mobile_sam_path}")
        print(f"YOLO: {yolo_path}")
    except FileNotFoundError as e:
        print(f"\nERROR: {e}")
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Model Not Found", str(e))
        root.destroy()
        sys.exit(1)

    print("=" * 60)
    print("Starting application...")

    root = tk.Tk()
    app = MobileSAMv2SegmentationApp(root, mobile_sam_path, yolo_path)
    root.mainloop()


if __name__ == "__main__":
    main()