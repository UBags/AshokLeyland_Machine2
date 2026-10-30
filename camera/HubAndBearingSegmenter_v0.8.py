"""Hub and Bearing Segmenter - Complete Standalone VersionNO DEPENDENCIES on SAMSegmentation_v0_99.pyAll required functionality extracted and included here."""
import warnings

import numpy as np
import torch
import cv2
from typing import List, Dict, Any, Tuple, Optional
import sys
import os

from BaseUtils import get_project_root
from camera.ModelManager import ModelManager
# Import normalization methods
# Import image normalization methods from shared module
from utils.ImageNormalisationWithMask import (
    rgb2gray,
    ensure_float32,
    create_annular_mask,
    extract_annular_region,
    pixBackgroundNorm_masked,
    pixContrastNorm_masked
)

MAJOR_MINOR_AXIS_CUTOFF_IF_LESS_THAN_10 = 1.3
MAJOR_MINOR_AXIS_CUTOFF_IF_LESS_THAN_20 = 1.2
MAJOR_MINOR_AXIS_CUTOFF_IF_LESS_THAN_40 = 1.15
MAJOR_MINOR_AXIS_CUTOFF_FOR_OTHERS = 1.10

# These are applied based on the median radius/major_axis of each group
# Percentages (12-15% range):
GROUPING_RADIUS_VARIANCE_PERCENTAGE_LESS_THAN_10 = 0.25  # 20%
GROUPING_RADIUS_VARIANCE_PERCENTAGE_LESS_THAN_20 = 0.20  # 20%
GROUPING_RADIUS_VARIANCE_PERCENTAGE_LESS_THAN_40 = 0.15  # 15%
GROUPING_RADIUS_VARIANCE_PERCENTAGE_MORE_THAN_40 = 0.10  # 10%

# Area ratio thresholds for filtering ill-shaped masks
AREA_RATIO_THRESHOLD_IF_LESS_THAN_10 = 0.5
AREA_RATIO_THRESHOLD_IF_LESS_THAN_20 = 0.55
AREA_RATIO_THRESHOLD_IF_LESS_THAN_40 = 0.6
AREA_RATIO_THRESHOLD_FOR_OTHERS = 0.65

# Model-specific distance configurations for hole counting
# Format: (distance1, distance2, tolerance_percentage)
MODEL_DISTANCE_CONFIG = {
    "DOST": {
        "distances": [170, 135],
        "tolerance": 0.10,
        "configurations": [
            {"count": 4, "spacing_min": 215, "spacing_max": 245},
            {"count": 4, "spacing_min": 175, "spacing_max": 200}
        ]
    },
    "DOSTPLUS": {
        "distances": [175, 140],
        "tolerance": 0.10,
        "configurations": [
            {"count": 5, "spacing_min": 190, "spacing_max": 210},
            {"count": 5, "spacing_min": 145, "spacing_max": 170}
        ]
    }
}

# Combined list of all configurations for model-agnostic matching
ALL_SPACING_CONFIGURATIONS = [
    {"count": 5, "spacing_min": 190, "spacing_max": 210, "model": "DOSTPLUS"},
    {"count": 5, "spacing_min": 145, "spacing_max": 170, "model": "DOSTPLUS"},
    {"count": 4, "spacing_min": 215, "spacing_max": 245, "model": "DOST"},
    {"count": 4, "spacing_min": 175, "spacing_max": 200, "model": "DOST"},
]

PERCENTAGE_SPREAD_FOR_GROUPING_FROM_CENTER = 0.15  # 12.5%

# Equispacing tolerance for circular arrangement validation
EQUISPACING_STRICT_TOLERANCE = 0.10  # 10% - strict pass criteria
EQUISPACING_RELAXED_TOLERANCE = 0.20  # 20% - fallback if no strict pass

# =============================================================================# EXTRACTED FROM SAMSegmentation_v0_99.py# =============================================================================# GROUPING_ALGORITHM

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


# =============================================================================
# EDGE REASSIGNMENT HELPERS (v0.95) - Fixes "bridge group" problem
# =============================================================================

def _compute_median_of_list(values: List[float]) -> float:
    """Compute median of a list of values."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n % 2 == 1:
        return sorted_vals[n // 2]
    else:
        return (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2.0


# def _would_element_fit_in_group(element: float, group_values: List[float], max_deviation: float) -> bool:
#     """Check if adding element to group_values would still satisfy the deviation constraint."""
#     if not group_values:
#         return True
#
#     test_values = sorted(group_values + [element])
#     median = _compute_median_of_list(test_values)
#
#     if median <= 0:
#         return all(v == median for v in test_values)
#
#     lower = median * (1 - max_deviation)
#     upper = median * (1 + max_deviation)
#
#     return all(lower - 1e-9 <= v <= upper + 1e-9 for v in test_values)

def _would_element_fit_in_group(element: float, group_values: List[float], max_deviation: float) -> bool:
    """Check if element is within tolerance of the group's current median."""
    if not group_values:
        return True

    # Use CURRENT median (don't recalculate with new element)
    current_median = _compute_median_of_list(group_values)

    if current_median <= 0:
        return element == current_median

    lower = current_median * (1 - max_deviation)
    upper = current_median * (1 + max_deviation)

    return lower - 1e-9 <= element <= upper + 1e-9


def _distance_to_group(element: float, group_values: List[float]) -> float:
    """Calculate how far element is from group's median (as a ratio)."""
    if not group_values:
        return float('inf')
    median = _compute_median_of_list(group_values)
    if median <= 0:
        return float('inf') if element != median else 0.0
    return abs(element - median) / median


def _refine_groupings(
        sorted_values: List[float],
        group_ranges: List[Tuple[int, int]],
        max_deviation: float
) -> List[Tuple[int, int]]:
    """
    Refine groupings by reassigning edge elements that fit better in adjacent groups.

    This handles the "bridge group" problem where two unrelated values get grouped
    together because the greedy sweep didn't look ahead.

    Example: [9.55, 10.23, 10.38, 10.91] [12.29, 16.54] [16.58, 16.90, 16.95]
         ->  [9.55, 10.23, 10.38, 10.91] [12.29] [16.54, 16.58, 16.90, 16.95]
    """
    if len(group_ranges) < 2:
        return group_ranges

    # Convert ranges to lists of indices for easier manipulation
    groups = [list(range(start, end + 1)) for start, end in group_ranges]

    changed = True
    max_iterations = 10
    iteration = 0

    while changed and iteration < max_iterations:
        changed = False
        iteration += 1

        # Pass 1: Check if rightmost element of each group fits better in next group
        i = 0
        while i < len(groups) - 1:
            if len(groups[i]) < 2:
                i += 1
                continue

            current_group = groups[i]
            next_group = groups[i + 1]

            rightmost_idx = current_group[-1]
            rightmost_val = sorted_values[rightmost_idx]

            current_vals_without = [sorted_values[idx] for idx in current_group[:-1]]
            next_vals = [sorted_values[idx] for idx in next_group]

            dist_to_current = _distance_to_group(rightmost_val, current_vals_without)
            dist_to_next = _distance_to_group(rightmost_val, next_vals)

            if dist_to_next < dist_to_current:
                if _would_element_fit_in_group(rightmost_val, next_vals, max_deviation):
                    groups[i] = current_group[:-1]
                    groups[i + 1] = [rightmost_idx] + next_group
                    changed = True
                    continue

            i += 1

        # Pass 2: Check if leftmost element of each group fits better in previous group
        i = len(groups) - 1
        while i > 0:
            if len(groups[i]) < 2:
                i -= 1
                continue

            current_group = groups[i]
            prev_group = groups[i - 1]

            leftmost_idx = current_group[0]
            leftmost_val = sorted_values[leftmost_idx]

            current_vals_without = [sorted_values[idx] for idx in current_group[1:]]
            prev_vals = [sorted_values[idx] for idx in prev_group]

            dist_to_current = _distance_to_group(leftmost_val, current_vals_without)
            dist_to_prev = _distance_to_group(leftmost_val, prev_vals)

            if dist_to_prev < dist_to_current:
                if _would_element_fit_in_group(leftmost_val, prev_vals, max_deviation):
                    groups[i] = current_group[1:]
                    groups[i - 1] = prev_group + [leftmost_idx]
                    changed = True
                    continue

            i -= 1

        # Pass 3: Merge orphan groups (size 1) with best neighbor if possible
        i = 0
        while i < len(groups):
            if len(groups[i]) != 1:
                i += 1
                continue

            orphan_idx = groups[i][0]
            orphan_val = sorted_values[orphan_idx]

            best_target = None
            best_dist = float('inf')

            if i > 0:
                prev_vals = [sorted_values[idx] for idx in groups[i - 1]]
                if _would_element_fit_in_group(orphan_val, prev_vals, max_deviation):
                    dist = _distance_to_group(orphan_val, prev_vals)
                    if dist < best_dist:
                        best_dist = dist
                        best_target = i - 1

            if i < len(groups) - 1:
                next_vals = [sorted_values[idx] for idx in groups[i + 1]]
                if _would_element_fit_in_group(orphan_val, next_vals, max_deviation):
                    dist = _distance_to_group(orphan_val, next_vals)
                    if dist < best_dist:
                        best_dist = dist
                        best_target = i + 1

            if best_target is not None:
                if best_target < i:
                    groups[best_target] = groups[best_target] + [orphan_idx]
                else:
                    groups[best_target] = [orphan_idx] + groups[best_target]

                groups.pop(i)
                changed = True
                continue

            i += 1

    # Remove empty groups and convert back to ranges
    result_ranges = []
    for g in groups:
        if g:
            result_ranges.append((min(g), max(g)))

    return result_ranges


# =============================================================================
# GROUPING WITH REFINEMENT (v0.95)
# =============================================================================

def group_numbers_with_indices(index_value_pairs, max_allowed_deviation):
    """
    Group (index, value) pairs by value, maintaining original indices.

    v0.95: Now includes edge reassignment refinement to fix "bridge group" problem.

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
        group_ranges = groups_l
    else:
        group_ranges = groups_r

    # === NEW v0.95: Refine groupings to fix bridge groups ===
    group_ranges = _refine_groupings(sorted_values, group_ranges, max_allowed_deviation)

    # Convert sorted array index ranges back to original indices and compute medians
    result_groups = []
    medians = []
    for start, end in group_ranges:
        original_indices = [sorted_indices[i] for i in range(start, end + 1)]
        result_groups.append(original_indices)

        group_values = sorted_values[start:end + 1]
        medians.append(_compute_median_of_list(group_values))

    return result_groups, medians


# HELPER_FUNCTIONS
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



# FILTER_MASKS_BY_AREA_RATIO

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


# FILTER_VALID_SHAPES
def filter_valid_shapes_only(masks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Filter to keep only circles and ellipses, dropping 'other' shapes.

    v0.96: New step - consolidate all valid shapes before distance grouping.

    Args:
        masks: List of mask dictionaries with 'shape_info' already set

    Returns:
        Filtered list containing only circles and ellipses
    """
    filtered_masks = []
    excluded_count = 0

    print("\n" + "=" * 60)
    print("SHAPE FILTER - Keeping only circles and ellipses")
    print("=" * 60)

    for idx, mask_data in enumerate(masks):
        shape_info = mask_data.get('shape_info', {})
        shape_type = shape_info.get('type', 'other')

        if shape_type in ('circle', 'ellipse'):
            filtered_masks.append(mask_data)
        else:
            excluded_count += 1
            print(f"  Excluded 'other' shape (idx={idx})")

    print(f"\nShape filter: kept {len(filtered_masks)} (circles + ellipses), excluded {excluded_count}")
    print("=" * 60 + "\n")

    return filtered_masks

# FILTER_SMALL_GROUPS
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


# FILTER_SUBGROUPS_BY_CENTER_ENVELOPE
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


# =============================================================================
# SPLIT-GAP DETECTION HELPERS (v0.94)
# =============================================================================

def detect_split_gaps(
        distances: List[float],
        expected_spacing: float,
        small_ratio_threshold: float = 0.65,
        sum_tolerance: float = 0.20
) -> List[Tuple[int, int, float]]:
    """
    Detect consecutive pairs of small distances that represent split gaps.

    When a false-positive mask appears between two correct positions, it creates
    two small consecutive distances that sum to approximately the expected spacing.

    Example: [125.8, 118.6] where 125.8 + 118.6 = 244.4 ≈ expected 233

    Args:
        distances: List of distances between consecutive masks (circular)
        expected_spacing: The expected distance between properly-spaced masks
        small_ratio_threshold: Distances below this ratio of expected are "small"
        sum_tolerance: How close the sum must be to expected_spacing (as ratio)

    Returns:
        List of (index1, index2, combined_distance) for each detected split gap
    """
    n = len(distances)
    small_threshold = expected_spacing * small_ratio_threshold

    split_gaps = []
    visited = set()

    for i in range(n):
        if i in visited:
            continue

        if distances[i] < small_threshold:
            next_i = (i + 1) % n

            if next_i not in visited and distances[next_i] < small_threshold:
                combined = distances[i] + distances[next_i]
                relative_error = abs(combined - expected_spacing) / expected_spacing

                if relative_error <= sum_tolerance:
                    split_gaps.append((i, next_i, combined))
                    visited.add(i)
                    visited.add(next_i)

    return split_gaps


def estimate_expected_spacing(distances: List[float]) -> float:
    """
    Estimate expected spacing from the upper half of distances.

    Assumes at least half the gaps are "correct" (not split by false positives).
    """
    if not distances:
        return 0.0

    sorted_dist = sorted(distances, reverse=True)
    upper_count = max(1, len(sorted_dist) // 2)
    return np.median(sorted_dist[:upper_count])


def try_model_aware_collapse(
        masks: List[Dict[str, Any]],
        mask_indices: List[int],
        center_x: float,
        center_y: float,
        model_type: Optional[str] = None
) -> Tuple[Optional[List[int]], Optional[str], Optional[float]]:
    """
    Try to collapse mask chain to match known model configurations.

    Uses prior knowledge about DOST/DOSTPLUS expected counts and spacings
    to find the best subset of masks that form a valid circular arrangement.

    Args:
        masks: List of mask dictionaries
        mask_indices: List of mask indices to consider
        center_x, center_y: Center coordinates
        model_type: If specified, only try configurations for this model (DOST or DOSTPLUS)

    Returns:
        Tuple of (new_indices, matched_model, quality_score) or (None, None, None) if no match
    """
    from itertools import combinations

    # Get ordered masks and centers
    ordered_indices = get_clockwise_order(masks, mask_indices, center_x, center_y)

    centers = []
    for mask_idx in ordered_indices:
        if mask_idx < len(masks):
            shape_info = masks[mask_idx].get('shape_info', {})
            mask_center = shape_info.get('center')
            if mask_center is not None:
                centers.append((mask_idx, mask_center))

    n = len(centers)
    if n < 4:  # Minimum expected count
        return None, None, None

    # Calculate consecutive distances
    distances = []
    for i in range(n):
        x1, y1 = centers[i][1]
        x2, y2 = centers[(i + 1) % n][1]
        dist = np.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
        distances.append(dist)

    def get_arc_distance(start_idx: int, end_idx: int) -> float:
        """Get total distance from start to end going clockwise through centers list."""
        total = 0.0
        i = start_idx
        while i != end_idx:
            total += distances[i]
            i = (i + 1) % n
        return total

    best_result = None
    best_score = float('inf')
    best_model = None

    # Filter configurations based on model_type if specified
    if model_type and model_type in MODEL_DISTANCE_CONFIG:
        configs_to_try = [
            {"count": cfg["count"], "spacing_min": cfg["spacing_min"],
             "spacing_max": cfg["spacing_max"], "model": model_type}
            for cfg in MODEL_DISTANCE_CONFIG[model_type].get("configurations", [])
        ]
        print(f"      [Model-Aware] Using {model_type} configurations only")
    else:
        configs_to_try = ALL_SPACING_CONFIGURATIONS
        print(f"      [Model-Aware] Trying all configurations")

    for config in configs_to_try:
        expected_count = config["count"]
        spacing_min = config["spacing_min"]
        spacing_max = config["spacing_max"]
        model_name = config["model"]

        if n < expected_count:
            continue

        # Try all combinations of expected_count masks from n
        for selected_positions in combinations(range(n), expected_count):
            selected_positions = list(selected_positions)
            valid = True
            total_deviation = 0.0

            # Check if distances between consecutive selected masks are valid
            for i in range(expected_count):
                start_pos = selected_positions[i]
                end_pos = selected_positions[(i + 1) % expected_count]

                arc_dist = get_arc_distance(start_pos, end_pos)

                if not (spacing_min <= arc_dist <= spacing_max):
                    valid = False
                    break

                # Track deviation from center of range
                center_spacing = (spacing_min + spacing_max) / 2.0
                total_deviation += abs(arc_dist - center_spacing)

            if valid:
                # Calculate quality score (lower is better)
                avg_deviation = total_deviation / expected_count

                if avg_deviation < best_score:
                    best_score = avg_deviation
                    best_result = [centers[pos][0] for pos in selected_positions]
                    best_model = model_name

    if best_result:
        return best_result, best_model, best_score

    return None, None, None


# =============================================================================
# IMPROVED OUTLIER REMOVAL WITH SPLIT-GAP MERGING (v0.94)
# =============================================================================

def try_remove_outlier_mask(
        masks: List[Dict[str, Any]],
        mask_indices: List[int],
        center_x: float,
        center_y: float,
        strict_tolerance: float = EQUISPACING_STRICT_TOLERANCE,
        relaxed_tolerance: float = EQUISPACING_RELAXED_TOLERANCE,
        min_masks: int = 4,
        model_type: Optional[str] = None
) -> Tuple[List[int], float, List[float], int]:
    """
    Improved outlier removal with split-gap detection.

    PHASE 0 - Model-Aware Collapse:
      Uses prior knowledge about DOST/DOSTPLUS configurations to find best subset.

    PHASE 1 - Split Gap Merging:
      Detects pairs of consecutive small distances (d1, d2) where d1+d2 ≈ expected
      and removes the intermediate false-positive mask.

    PHASE 2 - Traditional Outlier Removal:
      If still failing after merging, falls back to removing worst-edge endpoints.

    Args:
        model_type: If specified, only try configurations for this model (DOST or DOSTPLUS)

    Returns:
        Tuple of (best_indices, best_max_deviation, best_distances, num_removed)
    """
    if len(mask_indices) < min_masks:
        max_dev, mean_dev, distances = calculate_equispacing_score(
            masks, mask_indices, center_x, center_y
        )
        return (mask_indices, max_dev, distances, 0)

    current_indices = list(mask_indices)
    total_removed = 0

    # =========================================================================
    # PHASE 0: Try model-aware collapse (uses prior knowledge)
    # =========================================================================
    if len(mask_indices) >= 4:
        collapsed_result, matched_model, score = try_model_aware_collapse(
            masks, mask_indices, center_x, center_y, model_type
        )

        if collapsed_result is not None:
            max_dev, mean_dev, new_distances = calculate_equispacing_score(
                masks, collapsed_result, center_x, center_y
            )

            removed_count = len(mask_indices) - len(collapsed_result)
            print(f"      [Model-Aware] Matched {matched_model} config: "
                  f"{len(collapsed_result)} masks, max_dev={max_dev:.3f}")
            print(f"      Distances: {[f'{d:.1f}' for d in new_distances]}")

            if max_dev <= strict_tolerance:
                print(f"      ✓ PASSES strict tolerance via model-aware collapse!")
                return (collapsed_result, max_dev, new_distances, removed_count)
            elif max_dev <= relaxed_tolerance:
                print(f"      ~ Passes relaxed tolerance via model-aware collapse")
                # Continue with this as our starting point
                current_indices = collapsed_result
                total_removed = removed_count

    # =========================================================================
    # PHASE 1: Merge split gaps (iteratively)
    # =========================================================================
    max_merge_iterations = 3

    for iteration in range(max_merge_iterations):
        if len(current_indices) < 4:
            break

        # Get ordered masks and centers
        ordered_indices = get_clockwise_order(masks, current_indices, center_x, center_y)

        centers = []
        for mask_idx in ordered_indices:
            if mask_idx < len(masks):
                shape_info = masks[mask_idx].get('shape_info', {})
                mask_center = shape_info.get('center')
                if mask_center is not None:
                    centers.append((mask_idx, mask_center))

        n = len(centers)
        if n < 4:
            break

        # Calculate distances
        distances = []
        for i in range(n):
            x1, y1 = centers[i][1]
            x2, y2 = centers[(i + 1) % n][1]
            dist = np.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
            distances.append(dist)

        # Estimate expected spacing and detect split gaps
        expected_spacing = estimate_expected_spacing(distances)
        split_gaps = detect_split_gaps(distances, expected_spacing)

        if not split_gaps:
            break

        # Remove intermediate masks for each split gap
        masks_to_remove = set()
        for dist_idx1, dist_idx2, combined in split_gaps:
            # The intermediate mask is at position (dist_idx1 + 1) % n
            intermediate_pos = (dist_idx1 + 1) % n
            intermediate_mask_idx = centers[intermediate_pos][0]
            masks_to_remove.add(intermediate_mask_idx)

            print(f"      [Merge] Removing mask {intermediate_mask_idx}: "
                  f"{distances[dist_idx1]:.1f} + {distances[dist_idx2]:.1f} = {combined:.1f} "
                  f"(expected: {expected_spacing:.1f})")

        current_indices = [idx for idx in ordered_indices if idx not in masks_to_remove]
        total_removed += len(masks_to_remove)

        # Check if we now pass
        max_dev, mean_dev, new_distances = calculate_equispacing_score(
            masks, current_indices, center_x, center_y
        )

        print(f"      After merge iteration {iteration + 1}: "
              f"{len(current_indices)} masks, max_dev={max_dev:.3f}")
        print(f"      Distances: {[f'{d:.1f}' for d in new_distances]}")

        if max_dev <= strict_tolerance:
            return (current_indices, max_dev, new_distances, total_removed)

    # =========================================================================
    # PHASE 2: Traditional outlier removal (if still needed)
    # =========================================================================
    while len(current_indices) >= min_masks:
        max_dev, mean_dev, distances = calculate_equispacing_score(
            masks, current_indices, center_x, center_y
        )

        if max_dev <= strict_tolerance:
            return (current_indices, max_dev, distances, total_removed)

        if len(current_indices) <= min_masks:
            return (current_indices, max_dev, distances, total_removed)

        # Get ordered indices and centers
        ordered_indices = get_clockwise_order(masks, current_indices, center_x, center_y)

        centers = []
        for mask_idx in ordered_indices:
            if mask_idx < len(masks):
                shape_info = masks[mask_idx].get('shape_info', {})
                mask_center = shape_info.get('center')
                if mask_center is not None:
                    centers.append((mask_idx, mask_center))

        if len(centers) < min_masks:
            return (current_indices, max_dev, distances, total_removed)

        # Calculate edge distances
        n = len(centers)
        edge_distances = []
        for i in range(n):
            x1, y1 = centers[i][1]
            x2, y2 = centers[(i + 1) % n][1]
            dist = np.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
            edge_distances.append(dist)

        median_dist = np.median(edge_distances)

        # Find worst edge
        deviations = [(i, abs(d - median_dist) / median_dist if median_dist > 0 else 0)
                      for i, d in enumerate(edge_distances)]
        worst_edge_idx, worst_deviation = max(deviations, key=lambda x: x[1])

        mask_at_start = centers[worst_edge_idx][0]
        mask_at_end = centers[(worst_edge_idx + 1) % n][0]

        # Try removing each endpoint
        indices_a = [idx for idx in ordered_indices if idx != mask_at_start]
        max_dev_a, _, _ = calculate_equispacing_score(masks, indices_a, center_x, center_y)

        indices_b = [idx for idx in ordered_indices if idx != mask_at_end]
        max_dev_b, _, _ = calculate_equispacing_score(masks, indices_b, center_x, center_y)

        # Choose better option
        if max_dev_a <= max_dev_b:
            current_indices = indices_a
            removed_mask = mask_at_start
        else:
            current_indices = indices_b
            removed_mask = mask_at_end

        total_removed += 1
        new_max_dev = min(max_dev_a, max_dev_b)
        print(f"      [Outlier] Removed mask {removed_mask}, "
              f"now {len(current_indices)} masks, max_dev={new_max_dev:.3f}")

    # Final calculation
    max_dev, mean_dev, distances = calculate_equispacing_score(
        masks, current_indices, center_x, center_y
    )

    return (current_indices, max_dev, distances, total_removed)


# FILTER_SUBGROUPS_BY_EQUISPACING
def filter_subgroups_by_equispacing(
        masks: List[Dict[str, Any]],
        groups: Dict[int, List[int]],
        center_x: float,
        center_y: float,
        strict_tolerance: float = EQUISPACING_STRICT_TOLERANCE,
        relaxed_tolerance: float = EQUISPACING_RELAXED_TOLERANCE,
        model_type: Optional[str] = None
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
        model_type: If specified, only try configurations for this model (DOST or DOSTPLUS)

    Returns:
        Tuple of (filtered_groups, winning_group_id)
        winning_group_id is the single best group if we had to fall back to relaxed criteria
    """
    print("\n" + "=" * 60)
    print("EQUISPACING FILTER - Checking circular arrangement")
    print(f"  Strict tolerance: ±{strict_tolerance * 100:.0f}%")
    print(f"  Relaxed tolerance: ±{relaxed_tolerance * 100:.0f}%")
    if model_type:
        print(f"  Model type: {model_type}")
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
                    strict_tolerance, relaxed_tolerance, min_masks=4,
                    model_type=model_type
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


# SELECT_WINNING_GROUP

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
# NOTE: The following functions are now imported from ImageNormalisationWithMask:
#   - rgb2gray
#   - ensure_float32
#   - create_annular_mask
#   - extract_annular_region
#   - pixBackgroundNorm_masked
#   - pixContrastNorm_masked
#
# This eliminates code duplication and ensures consistency across the project.
# =============================================================================


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
# HubAndBearingSegmenter CLASS
# =============================================================================

class HubAndBearingSegmenter:
    """
    Core segmentation engine for hub and bearing hole detection.
    No GUI - pure processing logic.
    Completely standalone - no dependencies on SAMSegmentation_v0_99.py
    """

    def __init__(self, mobile_sam_path: str, yolo_path: str):
        """
        Initialize the segmenter with SAM model paths.

        Args:
            mobile_sam_path: Path to mobile_sam.pt checkpoint
            yolo_path: Path to ObjectAwareModel.pt (YOLO detector)
        """
        self.model_manager = ModelManager.get_instance()
        self.device = self.model_manager.get_device()
        print(f"Using device: {self.device}")

        self.sam_predictor = self.model_manager.get_sam_predictor()
        self.yolo_model = self.model_manager.get_yolo_model()

        self.conf_thresh = 0.3
        self.iou_thresh = 0.7
        self.min_area = 100
        self.max_area = 3000
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

        # Annular region parameters (fixed for this application)
        self.annular_center = (635, 345)
        self.annular_outer_radius = 250
        self.annular_inner_radius = 120

        # Storage for last run results
        self._last_masks = None
        self._last_groups = None
        self._winning_group_id = None
        self._winning_count = 0

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

    def segment_holes_batch(self, preprocessed_images: List[np.ndarray],
                            model_type: Optional[str] = None) -> List[
        Tuple[int, List[List[Tuple[np.ndarray, Tuple[float, float]]]]]]:
        """
        Process multiple preprocessed images (different gammas) in a batch.

        Args:
            preprocessed_images: List of preprocessed images (different gamma values)
            model_type: "DOST" or "DOSTPLUS" for model-specific filtering

        Returns:
            List of (count, groups_with_tuples) for each image
            Each element is same format as segment_holes() return value
        """
        results = []

        # Center coordinates (same for all images)
        center_x = self.annular_outer_radius
        center_y = self.annular_outer_radius

        # Process each image with inference mode covering all
        with torch.inference_mode():
            for preprocessed_image in preprocessed_images:
                try:
                    # Convert grayscale to RGB for SAM
                    if preprocessed_image.ndim == 2:
                        rgb_image = cv2.cvtColor(preprocessed_image, cv2.COLOR_GRAY2RGB)
                    else:
                        rgb_image = preprocessed_image

                    # Run SAM segmentation
                    masks = self.mask_generator.generate(rgb_image)

                    if not masks:
                        results.append((0, []))
                        continue

                    # Step 1: Classify masks
                    for mask_data in masks:
                        shape_info = self._classify_mask_shape(mask_data['segmentation'])
                        mask_data['shape_info'] = shape_info

                    # Step 2: Filter by area ratio
                    masks = filter_masks_by_area_ratio(masks)
                    if not masks:
                        results.append((0, []))
                        continue

                    # Step 3: Keep only valid shapes
                    masks = filter_valid_shapes_only(masks)
                    if not masks:
                        results.append((0, []))
                        continue

                    # Step 4: Group by distance from center
                    distance_groups = self._group_by_distance_from_center(masks)

                    # Step 5: Sub-group by size
                    groups = self._subgroup_by_size(masks, distance_groups)

                    # Step 6: Filter small groups
                    groups = filter_small_groups(groups, min_size=3)

                    # Step 7: Filter by center envelope
                    groups = filter_subgroups_by_center_envelope(masks, groups, center_x, center_y)

                    # Step 8: Filter by equispacing
                    groups, _ = filter_subgroups_by_equispacing(
                        masks, groups, center_x, center_y, model_type=model_type
                    )

                    # Step 9: Select winning group
                    winning_group_id, winning_count = select_winning_group(groups)

                    if winning_count == 0 or not groups:
                        results.append((0, []))
                        continue

                    # Build output format: list of lists with (contour, center) tuples
                    result_groups = []
                    for group_id, mask_indices in groups.items():
                        if len(mask_indices) == winning_count:
                            group_tuples = []
                            for mask_idx in mask_indices:
                                mask_data = masks[mask_idx]
                                mask_binary = mask_data['segmentation'].astype(np.uint8)

                                # Get contour
                                contours, _ = cv2.findContours(mask_binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                                contour = contours[0] if contours else np.array([])

                                # Get center
                                center = mask_data['shape_info'].get('center', (0, 0))

                                group_tuples.append((contour, center))

                            result_groups.append(group_tuples)

                    results.append((winning_count, result_groups))

                except Exception as e:
                    print(f"[segment_holes_batch] Error processing image: {e}")
                    results.append((0, []))

        # Clear CUDA cache after processing entire batch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        return results

    def preprocess_image(self, image: np.ndarray, gamma: float = 2.0,
                        bg_sx: int = 10, bg_sy: int = 10,
                        cn_sx: int = 10, cn_sy: int = 10,
                        apply_bilateral: bool = True,
                        alter_gamma: bool = True) -> Tuple[np.ndarray, np.ndarray]:
        """
        Preprocess image through normalization pipeline.

        Args:
            image: Input RGB image
            gamma: Gamma correction value (1-6 for parallel processing)
            bg_sx, bg_sy: Background normalization tile sizes
            cn_sx, cn_sy: Contrast normalization tile sizes
            apply_bilateral: Whether to apply bilateral filter
            alter_gamma: Whether to apply gamma correction

        Returns:
            Tuple of (processed_image, annular_mask)
        """
        # Step 1: Extract annular region
        annular_image, annular_mask = extract_annular_region(
            image,
            center=self.annular_center,
            outer_radius=self.annular_outer_radius,
            inner_radius=self.annular_inner_radius,
            fill_color=0
        )

        # Step 2: Background normalization
        bg_normalized = pixBackgroundNorm_masked(
            annular_image, annular_mask, sx=bg_sx, sy=bg_sy
        )

        # Step 3: Optional gamma correction
        if alter_gamma:
            gamma_corrected = self._apply_gamma(bg_normalized, gamma)
        else:
            gamma_corrected = bg_normalized

        # Step 4: Optional bilateral filter
        if apply_bilateral:
            bilateral_filtered = cv2.bilateralFilter(
                gamma_corrected, d=21, sigmaColor=30, sigmaSpace=30
            )
        else:
            bilateral_filtered = gamma_corrected

        # Step 5: Convert to grayscale
        gray_image = cv2.cvtColor(bilateral_filtered, cv2.COLOR_RGB2GRAY) if bilateral_filtered.ndim == 3 else bilateral_filtered

        # Step 6: Contrast normalization
        contrast_normalized = pixContrastNorm_masked(
            gray_image, annular_mask, sx=cn_sx, sy=cn_sy
        )

        return contrast_normalized, annular_mask

    def _apply_gamma(self, image: np.ndarray, gamma: float) -> np.ndarray:
        """Apply gamma correction."""
        if image.dtype != np.uint8:
            image = np.clip(image, 0, 255).astype(np.uint8)

        # Build LUT
        inv_gamma = 1.0 / gamma
        table = np.array([((i / 255.0) ** inv_gamma) * 255 for i in range(256)]).astype(np.uint8)

        # Apply LUT
        return cv2.LUT(image, table)

    def segment_holes(self, preprocessed_image: np.ndarray,
                     model_type: Optional[str] = None) -> Tuple[int, List[List[Tuple[np.ndarray, Tuple[float, float]]]]]:
        """
        Run SAM segmentation and group holes.

        Args:
            preprocessed_image: Preprocessed grayscale image
            model_type: "DOST" or "DOSTPLUS" for model-specific filtering

        Returns:
            Tuple of:
                - count: Number of holes in winning group
                - list_of_lists: Groups of [(contour, center), ...] tuples
                  Only returns groups with exactly 'count' holes
        """
        # Convert grayscale to RGB for SAM
        if preprocessed_image.ndim == 2:
            rgb_image = cv2.cvtColor(preprocessed_image, cv2.COLOR_GRAY2RGB)
        else:
            rgb_image = preprocessed_image

        # Run SAM segmentation
        with torch.inference_mode():
            masks = self.mask_generator.generate(rgb_image)

        if not masks:
            return 0, []

        # Step 1: Classify masks
        for mask_data in masks:
            shape_info = self._classify_mask_shape(mask_data['segmentation'])
            mask_data['shape_info'] = shape_info

        # Step 2: Filter by area ratio
        masks = filter_masks_by_area_ratio(masks)
        if not masks:
            return 0, []

        # Step 3: Keep only valid shapes
        masks = filter_valid_shapes_only(masks)
        if not masks:
            return 0, []

        # Step 4: Group by distance from center
        distance_groups = self._group_by_distance_from_center(masks)

        # Step 5: Sub-group by size
        groups = self._subgroup_by_size(masks, distance_groups)

        # Center coordinates
        center_x = self.annular_outer_radius
        center_y = self.annular_outer_radius

        # Step 6: Filter small groups
        groups = filter_small_groups(groups, min_size=3)

        # Step 7: Filter by center envelope
        groups = filter_subgroups_by_center_envelope(masks, groups, center_x, center_y)

        # Step 8: Filter by equispacing
        groups, _ = filter_subgroups_by_equispacing(
            masks, groups, center_x, center_y, model_type=model_type
        )

        # Step 9: Select winning group
        winning_group_id, winning_count = select_winning_group(groups)

        # Store results
        self._last_masks = masks
        self._last_groups = groups
        self._winning_group_id = winning_group_id
        self._winning_count = winning_count

        # Clear cache again after processing
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        if winning_count == 0 or not groups:
            return 0, []

        # Build output format: list of lists with (contour, center) tuples
        result_groups = []
        for group_id, mask_indices in groups.items():
            if len(mask_indices) == winning_count:
                group_tuples = []
                for mask_idx in mask_indices:
                    mask_data = masks[mask_idx]
                    mask_binary = mask_data['segmentation'].astype(np.uint8)

                    # Get contour
                    contours, _ = cv2.findContours(mask_binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    contour = contours[0] if contours else np.array([])

                    # Get center
                    center = mask_data['shape_info'].get('center', (0, 0))

                    group_tuples.append((contour, center))

                result_groups.append(group_tuples)

        return winning_count, result_groups

    def _classify_mask_shape(self, mask: np.ndarray) -> Dict[str, Any]:
        """Classify mask shape using fitEllipse."""
        mask_uint8 = mask.astype(np.uint8) * 255
        contours, _ = cv2.findContours(mask_uint8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if not contours or len(contours[0]) < 5:
            return {'type': 'other', 'center': None}

        contour = contours[0]

        try:
            ellipse = cv2.fitEllipse(contour)
            center, axes, angle = ellipse
            major_axis = max(axes)
            minor_axis = min(axes)
            aspect_ratio = major_axis / minor_axis if minor_axis > 0 else float('inf')

            # Determine cutoff based on size
            if major_axis < 10:
                cutoff = MAJOR_MINOR_AXIS_CUTOFF_IF_LESS_THAN_10
            elif major_axis < 20:
                cutoff = MAJOR_MINOR_AXIS_CUTOFF_IF_LESS_THAN_20
            elif major_axis < 40:
                cutoff = MAJOR_MINOR_AXIS_CUTOFF_IF_LESS_THAN_40
            else:
                cutoff = MAJOR_MINOR_AXIS_CUTOFF_FOR_OTHERS

            if aspect_ratio <= cutoff:
                return {
                    'type': 'circle',
                    'radius': major_axis / 2.0,
                    'center': center,
                    'aspect_ratio': aspect_ratio
                }
            else:
                return {
                    'type': 'ellipse',
                    'major_axis': major_axis / 2.0,
                    'minor_axis': minor_axis / 2.0,
                    'center': center,
                    'aspect_ratio': aspect_ratio
                }
        except:
            return {'type': 'other', 'center': None}

    def _group_by_distance_from_center(self, masks: List[Dict]) -> Dict[int, List[int]]:
        """Group masks by distance from center."""
        if not masks:
            return {}

        # Calculate distances from center
        center_x = self.annular_outer_radius
        center_y = self.annular_outer_radius

        index_distance_pairs = []
        for mask_idx, mask_data in enumerate(masks):
            shape_info = mask_data['shape_info']
            mask_center = shape_info.get('center')

            if mask_center is None:
                continue

            cx, cy = mask_center
            dist = np.sqrt((cx - center_x)**2 + (cy - center_y)**2)
            index_distance_pairs.append((mask_idx, dist))

        if not index_distance_pairs:
            return {}

        # Group by distance (15% tolerance)
        grouped_indices, _ = group_numbers_with_indices(
            index_distance_pairs,
            PERCENTAGE_SPREAD_FOR_GROUPING_FROM_CENTER
        )

        # Convert to dictionary
        groups = {}
        for group_id, indices in enumerate(grouped_indices):
            groups[group_id] = indices

        return groups

    def _subgroup_by_size(self, masks: List[Dict], distance_groups: Dict) -> Dict[int, List[int]]:
        """Sub-group by size (radius/semi-major axis)."""
        final_groups = {}
        final_group_id = 0

        for group_id, mask_indices in distance_groups.items():
            if len(mask_indices) == 0:
                continue

            if len(mask_indices) == 1:
                final_groups[final_group_id] = mask_indices
                final_group_id += 1
                continue

            # Get size for each mask
            index_size_pairs = []
            for mask_idx in mask_indices:
                shape_info = masks[mask_idx]['shape_info']
                shape_type = shape_info.get('type', 'other')

                if shape_type == 'circle':
                    size = shape_info.get('radius', 0)
                elif shape_type == 'ellipse':
                    size = shape_info.get('major_axis', 0)
                else:
                    size = 0

                index_size_pairs.append((mask_idx, size))

            # Get all sizes for statistics
            sizes = [s for _, s in index_size_pairs]
            median_size = np.median(sizes)

            # Determine percentage threshold
            if median_size < 10:
                percentage = GROUPING_RADIUS_VARIANCE_PERCENTAGE_LESS_THAN_10
            elif median_size < 20:
                percentage = GROUPING_RADIUS_VARIANCE_PERCENTAGE_LESS_THAN_20
            elif median_size < 40:
                percentage = GROUPING_RADIUS_VARIANCE_PERCENTAGE_LESS_THAN_40
            else:
                percentage = GROUPING_RADIUS_VARIANCE_PERCENTAGE_MORE_THAN_40

            # Use custom grouping algorithm
            sub_groups, sub_medians = group_numbers_with_indices(
                index_size_pairs,
                percentage
            )

            # Map sub-groups to final groups
            for sub_grp_indices in sub_groups:
                final_groups[final_group_id] = sub_grp_indices
                final_group_id += 1

        return final_groups


# =============================================================================
# HELPER FUNCTIONS FOR CheckHubAndBottomBearing
# =============================================================================

def calculate_mask_metrics(contour: np.ndarray, center: Tuple[float, float]) -> Tuple[float, float]:
    """
    Calculate area and area_ratio for a mask.

    Args:
        contour: Mask contour
        center: Mask center (x, y)

    Returns:
        Tuple of (area, area_ratio)
        area_ratio = area / (pi * major_axis * minor_axis)
    """
    if len(contour) < 5:
        return 0.0, 0.0

    # Calculate area
    area = cv2.contourArea(contour)

    # Fit bounding box to get major/minor axes
    rect = cv2.minAreaRect(contour)
    (cx, cy), (w, h), angle = rect

    # Major and minor axes are half of width and height
    major_axis = max(w, h) / 2.0
    minor_axis = min(w, h) / 2.0

    # Calculate area ratio
    if major_axis > 0 and minor_axis > 0:
        theoretical_area = np.pi * major_axis * minor_axis
        area_ratio = area / theoretical_area if theoretical_area > 0 else 0.0
    else:
        area_ratio = 0.0

    return area, area_ratio


def order_centers_clockwise(centers: List[Tuple[float, float]]) -> List[int]:
    """
    Order centers in clockwise direction based on angle from centroid.

    Args:
        centers: List of (x, y) tuples

    Returns:
        List of indices in clockwise order
    """
    if len(centers) < 2:
        return list(range(len(centers)))

    # Calculate centroid
    centroid_x = np.mean([c[0] for c in centers])
    centroid_y = np.mean([c[1] for c in centers])

    # Calculate angles from centroid
    angles = []
    for cx, cy in centers:
        angle = np.arctan2(cy - centroid_y, cx - centroid_x)
        angles.append(angle)

    # Sort by angle (clockwise = descending angle)
    sorted_indices = sorted(range(len(angles)), key=lambda i: angles[i], reverse=False)

    return sorted_indices


def calculate_adjacent_distances(centers: List[Tuple[float, float]], clockwise_order: List[int]) -> List[float]:
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


def select_best_group(groups_with_tuples: List[List[Tuple[np.ndarray, Tuple[float, float]]]]) -> List[Tuple[np.ndarray, Tuple[float, float]]]:
    """
    Select the best group based on area_ratio and distance uniformity.

    Scoring logic:
    1. Order by avg_area_ratio (descending) → mask_order_1
    2. Order by sigma (ascending) → mask_order_2
    3. If top choice is same in both, choose that
    4. Otherwise: score = 0.5*avg_area_ratio - 0.5*sigma/avg_distance (highest wins)

    Args:
        groups_with_tuples: List of groups, each group is list of (contour, center) tuples

    Returns:
        Winning group (list of tuples)
    """
    if not groups_with_tuples:
        return []

    if len(groups_with_tuples) == 1:
        return groups_with_tuples[0]

    # Calculate metrics for each group
    group_metrics = []

    for group_tuples in groups_with_tuples:
        count = len(group_tuples)

        # Calculate area ratios
        area_ratios = []
        for contour, center in group_tuples:
            _, area_ratio = calculate_mask_metrics(contour, center)
            area_ratios.append(area_ratio)

        avg_area_ratio = np.mean(area_ratios)

        # Get centers and order clockwise
        centers = [center for _, center in group_tuples]
        clockwise_order = order_centers_clockwise(centers)

        # Calculate adjacent distances
        distances = calculate_adjacent_distances(centers, clockwise_order)

        if distances:
            avg_distance = np.mean(distances)
            sigma = np.std(distances)
        else:
            avg_distance = 0.0
            sigma = 0.0

        group_metrics.append({
            'group': group_tuples,
            'avg_area_ratio': avg_area_ratio,
            'sigma': sigma,
            'avg_distance': avg_distance
        })

    # Sort by avg_area_ratio (descending)
    mask_order_1 = sorted(group_metrics, key=lambda x: x['avg_area_ratio'], reverse=True)

    # Sort by sigma (ascending)
    mask_order_2 = sorted(group_metrics, key=lambda x: x['sigma'], reverse=False)

    # Check if top choice is same
    if mask_order_1[0]['group'] is mask_order_2[0]['group']:
        return mask_order_1[0]['group']

    # Calculate combined score
    for metrics in group_metrics:
        if metrics['avg_distance'] > 0:
            score = 0.5 * metrics['avg_area_ratio'] - 0.5 * (metrics['sigma'] / metrics['avg_distance'])
        else:
            score = 0.5 * metrics['avg_area_ratio']
        metrics['score'] = score

    # Select highest score
    winner = max(group_metrics, key=lambda x: x['score'])
    return winner['group']


def paint_masks_on_image(original_image: np.ndarray,
                         winning_group: List[Tuple[np.ndarray, Tuple[float, float]]],
                         color: Tuple[int, int, int] = (0, 255, 0)) -> np.ndarray:
    """
    Paint winning group masks on original image.

    Args:
        original_image: Original unprocessed camera image
        winning_group: List of (contour, center) tuples (in cropped/annular coordinates)
        color: RGB color tuple (default: green)

    Returns:
        Image with masks painted
    """
    result = original_image.copy()

    if not winning_group:
        return result

    # TRANSLATION OFFSET: Annular region offset from full image
    # Annular region center is at (635, 345) with outer_radius=250
    # So annular region top-left corner is at:
    offset_x = 635 - 250  # = 385
    offset_y = 345 - 250  # = 95

    # Create overlay
    overlay = np.zeros_like(result)

    for contour, center in winning_group:
        if len(contour) > 0:
            # Translate contour coordinates to full image
            translated_contour = contour.copy()
            translated_contour[:, :, 0] += offset_x  # Add x offset
            translated_contour[:, :, 1] += offset_y  # Add y offset

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
    for contour, center in winning_group:
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
    text = f"COUNT: {len(winning_group)}"
    cv2.putText(result, text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)

    return result