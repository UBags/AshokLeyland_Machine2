"""
BungSegmenter.py - bung hole detection (both bung checks: CheckNoCapBung, state 13; CheckBung, state 24) and the bung
seating step.

PIPELINE (per gamma, gammas 2.0, 7.75, 4.0, 6.5, 1.0 until a definite answer - CheckBung / CheckNoCapBung):
1. Extract the outer disc (bung.outer.disc.center, radius bung.outer.disc.radius; the centre follows the knuckle frame)
2. Bilateral filter (d=21, sigma_color=30, sigma_space=30), gamma correction, bilateral filter (21, 30, 50)
3. Masks (MobileSAM): one per YOLO box, plus one per point of a grid every hole.grid.step px over the inner disc
   (always; since CHANGES 115 - before, a sparse 120 px grid only when YOLO gave fewer than 5 masks, which put no
   point on the hole ring, so a hole without a YOLO box was lost and the result depended on the bung's angle)
4. Size filter before any merging: each mask cut to the inner disc (bung.inner.disc.radius), kept if min.mask.area -
   max.mask.area px remain
5. One mask per blob (overlap > 15% of the smaller): the mask found most often, never a union
6. Shape filter (box 0.5-1.5 x expected.radius.of.hole, fill >= 40%)
7. Hole pattern: DOST 4 in a square; DOSTPLUS the best pair at dostplus.hole.pair.distance (midpoint nearest the disc
   centre, not 3 corners of a square with a third candidate); a wrong bung only on a real 4-hole square
8. One INFO line per gamma ("[Bung] ...": candidates, what was dropped and why, the decision)
SEATING (check_bung_seating, after the pattern passed; bung.seating.check): hole-pattern centre near the seated
position, round top-face mask, the mask a clean oval, inside the model's reference oval.
"""

import warnings
import numpy as np
import torch
import cv2
from typing import List, Dict, Any, Tuple, Optional, Union
from itertools import combinations
import sys
import os

from BaseUtils import get_project_root, getFullyQualifiedName
from camera.ModelManager import ModelManager
from camera.VisionConfig import vInt, vFloat, vTuple, vBool, vStr, expectOneOf
import threading
from logutils.SlaveLoggers import logBoth
from logutils.Logger import MessageType

# Suppress warnings
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

# =============================================================================
# PRINT CONTROL FLAGS
# =============================================================================
PRINT_PREPROCESSING = False
PRINT_SAM_SEGMENTATION = False
PRINT_MASK_FILTERING = False
PRINT_GEOMETRIC_VALIDATION = False
PRINT_BATCH_PROCESSING = False

# =============================================================================
# CONFIGURATION CONSTANTS (V0.7 PIPELINE)
# =============================================================================

# Two-stage cropping parameters
OUTER_DISC_CENTER_DOST = (630, 350)  # Center in full image
OUTER_DISC_CENTER_DOSTPLUS = (630, 350)
OUTER_DISC_CENTER = OUTER_DISC_CENTER_DOST
OUTER_DISC_RADIUS_DOST = 250
OUTER_DISC_RADIUS_DOSTPLUS = 250
OUTER_DISC_RADIUS = OUTER_DISC_RADIUS_DOST

INNER_DISC_RADIUS_DOST = 110  # Radius from center of outer disc
INNER_DISC_RADIUS_DOSTPLUS = 110
INNER_DISC_RADIUS = INNER_DISC_RADIUS_DOST

# Gamma values - FIXED SEQUENCE (no brightness-based selection)
# not configurable: _preprocess_image needs a LUT for every gamma of get_gamma_sequence_for_model (CHANGES 154)
GAMMA_SEQUENCE = [7.75, 6.5, 4.0, 2.0, 1.0]

# Area filtering (applied to masks in inner disc)
MIN_MASK_AREA_DOST = 325
MIN_MASK_AREA_DOSTPLUS = 325
MIN_MASK_AREA = MIN_MASK_AREA_DOST
MAX_MASK_AREA_DOST = 1200
# Point prompts every HOLE_GRID_STEP px inside the hole search disc (inner disc), always added to the YOLO box masks.
MAX_MASK_AREA_DOSTPLUS = 1200
MAX_MASK_AREA = MAX_MASK_AREA_DOST
# A hole is about 36 px across on Machine 2 (area about 1000 px), so a 15 px grid puts about 4 points in every hole
# wherever the bung is turned; the sparse 120 px grid used before put none on the hole ring.
HOLE_GRID_STEP_DOST = 15
# The grid covers only the band where hole pixels can be: hole centres sit 40-66 px from the disc centre on the 13 Nov
HOLE_GRID_STEP_DOSTPLUS = 15
HOLE_GRID_STEP = HOLE_GRID_STEP_DOST
# pictures (a hole is about 36 px across; a bung off centre moves them more) - about 110 points instead of 220
HOLE_GRID_RADIUS_MIN_DOST = 20
HOLE_GRID_RADIUS_MIN_DOSTPLUS = 20
HOLE_GRID_RADIUS_MIN = HOLE_GRID_RADIUS_MIN_DOST
HOLE_GRID_RADIUS_MAX_DOST = 90
# Masks found more than once (the same hole from several points): IoU above this counts as the same mask
HOLE_GRID_RADIUS_MAX_DOSTPLUS = 90
HOLE_GRID_RADIUS_MAX = HOLE_GRID_RADIUS_MAX_DOST
HOLE_DUPLICATE_IOU_DOST = 0.5
# Masks overlapping by more than this fraction of the smaller one belong to the same blob; one mask is kept per blob
HOLE_DUPLICATE_IOU_DOSTPLUS = 0.5
HOLE_DUPLICATE_IOU = HOLE_DUPLICATE_IOU_DOST
HOLE_BLOB_OVERLAP_DOST = 0.15

# Geometric validation thresholds (retained from v1)
HOLE_BLOB_OVERLAP_DOSTPLUS = 0.15
HOLE_BLOB_OVERLAP = HOLE_BLOB_OVERLAP_DOST
EQUIDISTANT_DISTANCE_DEVIATION_THRESHOLD = 0.25  # 20% deviation  # UNUSED by live code
MINIMUM_INTERIOR_ANGLE = 65.0  # degrees  # UNUSED by live code
ALLOWED_STD_DEVIATION_IN_ANGLES = 20.0  # degrees  # UNUSED by live code
# Expected radius of hole for morphological filtering
EXPECTED_RADIUS_OF_HOLE_DOST = 30
EXPECTED_RADIUS_OF_HOLE_DOSTPLUS = 30
EXPECTED_RADIUS_OF_HOLE = EXPECTED_RADIUS_OF_HOLE_DOST
SQUARE_SIDE_LENGTH_TOLERANCE_DOST = 0.2  # DOST 4-hole square: max relative side-length deviation (the value actually used; EQUIDISTANT_DISTANCE_DEVIATION_THRESHOLD is not referenced)
SQUARE_SIDE_LENGTH_TOLERANCE_DOSTPLUS = 0.2
SQUARE_MIN_INTERIOR_ANGLE_DOST = 65.0  # DOST 4-hole square: min interior angle, degrees (MINIMUM_INTERIOR_ANGLE is not referenced)
SQUARE_MIN_INTERIOR_ANGLE_DOSTPLUS = 65.0
SQUARE_MAX_INTERIOR_ANGLE_DOST = 115.0  # DOST 4-hole square: max interior angle, degrees
SQUARE_MAX_INTERIOR_ANGLE_DOSTPLUS = 115.0
SQUARE_MAX_ANGLE_STD_DOST = 20.0  # DOST 4-hole square: max std of interior angles and max |mean - 90|, degrees (ALLOWED_STD_DEVIATION_IN_ANGLES is not referenced)
SQUARE_MAX_ANGLE_STD_DOSTPLUS = 20.0
# 3 holes found, one in the middle of the other two (e.g. the bung's centre punch mark taken for a hole): the middle one
# is ignored and the other two are treated as the 2 holes, when the angle at the middle hole is within THREE_HOLE_MAX_BEND
# of a straight line AND its distances to the other two differ by at most THREE_HOLE_MAX_DISTANCE_DIFFERENCE of their mean
THREE_HOLE_MAX_BEND_DOST = 10.0  # degrees
THREE_HOLE_MAX_BEND_DOSTPLUS = 10.0
THREE_HOLE_MAX_BEND = THREE_HOLE_MAX_BEND_DOST
THREE_HOLE_MAX_DISTANCE_DIFFERENCE_DOST = 0.25  # fraction of the mean distance
THREE_HOLE_MAX_DISTANCE_DIFFERENCE_DOSTPLUS = 0.25
THREE_HOLE_MAX_DISTANCE_DIFFERENCE = THREE_HOLE_MAX_DISTANCE_DIFFERENCE_DOST

# Seating (after the hole pattern passes; off = the check as before, Machine 1). A bung that is tilted or not pushed home
# shows its side wall: its hole pattern is away from the seated position and its top-face mask (MobileSAM, prompted at
# the hole-pattern centre) is oval. Seated when the hole-pattern centre is within seating.hole.centre.offset.max of the
# model's seated position AND the face mask's roundness (minor / major axis) reaches the model's minimum.
SEATING_CHECK_DOST = False
SEATING_CHECK_DOSTPLUS = False
SEATING_CHECK = SEATING_CHECK_DOST
SEATING_SEATED_CENTRE_DOST = (630, 350)       # full image px
SEATING_SEATED_CENTRE_DOSTPLUS = (630, 350)
SEATING_HOLE_CENTRE_OFFSET_MAX_DOST = 15.0       # px
SEATING_HOLE_CENTRE_OFFSET_MAX_DOSTPLUS = 15.0
SEATING_HOLE_CENTRE_OFFSET_MAX = SEATING_HOLE_CENTRE_OFFSET_MAX_DOST
SEATING_ROUNDNESS_MIN_DOST = 0.952
SEATING_ROUNDNESS_MIN_DOSTPLUS = 0.96
SEATING_FACE_RADIUS_MIN_DOST = 100.0                    # face mask, px
SEATING_FACE_RADIUS_MIN_DOSTPLUS = 100.0
SEATING_FACE_RADIUS_MIN = SEATING_FACE_RADIUS_MIN_DOST
SEATING_FACE_RADIUS_MAX_DOST = 145.0
# The face mask must be one clean oval: its area (holes filled) / the area of the ellipse fitted to its outline within
SEATING_FACE_RADIUS_MAX_DOSTPLUS = 145.0
SEATING_FACE_RADIUS_MAX = SEATING_FACE_RADIUS_MAX_DOST
# ellipse.fit.area.ratio.min..max (a mask that took in part of the side wall or the hub, or lost a piece to glare, fails).
SEATING_FIT_AREA_RATIO_MIN_DOST = 0.98
SEATING_FIT_AREA_RATIO_MIN_DOSTPLUS = 0.98
SEATING_FIT_AREA_RATIO_MIN = SEATING_FIT_AREA_RATIO_MIN_DOST
SEATING_FIT_AREA_RATIO_MAX_DOST = 1.02
# The face mask must lie inside the model's reference oval (cv2 rotated box: width, height, angle in degrees) centred on
SEATING_FIT_AREA_RATIO_MAX_DOSTPLUS = 1.02
SEATING_FIT_AREA_RATIO_MAX = SEATING_FIT_AREA_RATIO_MAX_DOST
# the model's seated position: at most outside.reference.oval.max of the mask outside it. A bung lying flat but off
# centre, or tilted (its face oval and shifted), has part of its face outside. Reference ovals = the seated faces of
# 13 Nov (mean axes) + 3 px all round.
SEATING_REFERENCE_OVAL_DOST = (241, 249, 122)
SEATING_REFERENCE_OVAL_DOSTPLUS = (232, 230, 0)
SEATING_OUTSIDE_REFERENCE_OVAL_MAX_DOST = 0.02
# Where the seated position comes from (CHANGES 153):
SEATING_OUTSIDE_REFERENCE_OVAL_MAX_DOSTPLUS = 0.02
SEATING_OUTSIDE_REFERENCE_OVAL_MAX = SEATING_OUTSIDE_REFERENCE_OVAL_MAX_DOST
#   "hubholes" - the circle through the hub's holes round the bung in the bung picture itself (FindHubHoles.findHoles, the
#                cap check's method of CHANGES 152: dark blobs at r 128 - 168, 4 / 5 evenly spaced, circle r 140 - 160)
#                plus seating.hub.offset.<model>: hub and bung in one picture, so where the part sits on the fixture and
#                any camera move cancel. The hub's holes not found (a bung lying over them) -> NOT seated.
#   "fixed"    - seating.seated.centre.<model> (placed by the knuckle frame), as before.
# The reference oval is centred on the seated position either way.
SEATING_POSITION_SOURCE_DOST = "hubholes"
SEATING_POSITION_SOURCE_DOSTPLUS = "hubholes"
SEATING_POSITION_SOURCE = SEATING_POSITION_SOURCE_DOST
SEATING_HUB_OFFSET_DOST = (0.0, 0.0)            # px added to the hub centre
SEATING_HUB_OFFSET_DOSTPLUS = (0.0, 0.0)

# DOSTPLUS on the hub centre (CHANGES 220; Uddi's study of 2026-11-27, CHANGES 215 - 219, frozen). method:
#   "fixed"     - as before (CHANGES 215): segmented disc round outer.disc.center, seating by check_bung_seating;
#   "hubcentre" - the hub's 5 holes in the bung picture FIRST (FindHubHoles.findHoles; not found -> NOT OK), centre H,
#                 radius R; the segmented disc = that hub-hole circle (the bung is always inside it), the hole-search
#                 disc = inner.disc.radius round H; seating (seating.check) by check_bung_seating_on_hub_centre: the
#                 hole-pattern centre must lie in an egg-shaped region round H (hub offset not used): half ellipses with
#                 one east-west half-axis and separate north / south half-axes (picture y down = south; the good bungs
#                 are seen north of H - the camera's optical centre lies south of the hub),
#                 e = (dx / east_west)^2 + (dy / north or south)^2; the top-face mask's roundness and its share outside
#                 seating.reference.oval centred on H. Two stages: NOT OK if e > region.notok.above or roundness <
#                 roundness.notok.below or outside > outside.notok.above; else OK if e < region.ok.below or roundness >
#                 roundness.ok.above or outside < outside.ok.below; else NOT OK.
# Code defaults = the Machine 2 values of the study except the method (Machine 1: fixed).
DOSTPLUS_BUNG_METHOD = "fixed"
DOSTPLUS_HUBCENTRE_REGION = (27.0, 33.0, 11.0)  # east-west, north, south px
DOSTPLUS_HUBCENTRE_REGION_NOTOK_ABOVE = 1.0
DOSTPLUS_HUBCENTRE_REGION_OK_BELOW = 0.7
DOSTPLUS_HUBCENTRE_ROUNDNESS_NOTOK_BELOW = 0.945
DOSTPLUS_HUBCENTRE_ROUNDNESS_OK_ABOVE = 0.955
DOSTPLUS_HUBCENTRE_OUTSIDE_NOTOK_ABOVE = 0.11
DOSTPLUS_HUBCENTRE_OUTSIDE_OK_BELOW = 0.07

# Per model (CHANGES 215, Uddi 2026-11-27): every bung value is vision.<vendor>.dost.bung.* / vision.<vendor>.dostplus.bung.*
# (X_DOST / X_DOSTPLUS above). The checks use the plain names (X); useModel(model), called by CheckBung / CheckNoCapBung
# once the model is known from the QR code, points them at that model's values (and the mask generator's grid). Before
# the first call they hold DOST's. Live reloads re-bind X_DOST / X_DOSTPLUS; the next useModel takes them.
PER_MODEL_NAMES = (
    "OUTER_DISC_CENTER",
    "OUTER_DISC_RADIUS",
    "INNER_DISC_RADIUS",
    "MIN_MASK_AREA",
    "MAX_MASK_AREA",
    "HOLE_GRID_STEP",
    "HOLE_GRID_RADIUS_MIN",
    "HOLE_GRID_RADIUS_MAX",
    "HOLE_DUPLICATE_IOU",
    "HOLE_BLOB_OVERLAP",
    "EXPECTED_RADIUS_OF_HOLE",
    "SQUARE_SIDE_LENGTH_TOLERANCE",
    "SQUARE_MIN_INTERIOR_ANGLE",
    "SQUARE_MAX_INTERIOR_ANGLE",
    "SQUARE_MAX_ANGLE_STD",
    "THREE_HOLE_MAX_BEND",
    "THREE_HOLE_MAX_DISTANCE_DIFFERENCE",
    "SEATING_CHECK",
    "SEATING_HOLE_CENTRE_OFFSET_MAX",
    "SEATING_FACE_RADIUS_MIN",
    "SEATING_FACE_RADIUS_MAX",
    "SEATING_FIT_AREA_RATIO_MIN",
    "SEATING_FIT_AREA_RATIO_MAX",
    "SEATING_OUTSIDE_REFERENCE_OVAL_MAX",
    "SEATING_POSITION_SOURCE",
)


def useModel(model_type: str) -> None:
    """Point the plain bung constants at this model's values (DOSTPLUS or else DOST) and set the mask generator's
    hole grid from them."""
    suffix = "DOSTPLUS" if model_type == "DOSTPLUS" else "DOST"
    here = globals()
    for name in PER_MODEL_NAMES:
        here[name] = here[f"{name}_{suffix}"]
    _applyGrid()


def _applyGrid() -> None:
    """The mask generator's hole grid from the plain constants (in the segmented disc's coordinates)."""
    segmenter = BungSegmenter._instance
    generator = getattr(segmenter, "mask_generator", None) if segmenter is not None else None
    if generator is not None:
        generator.min_mask_region_area = MIN_MASK_AREA
        generator.max_mask_region_area = MAX_MASK_AREA
        generator.grid_center = (OUTER_DISC_RADIUS, OUTER_DISC_RADIUS)
        generator.grid_radius = min(INNER_DISC_RADIUS, HOLE_GRID_RADIUS_MAX)
        generator.grid_step = HOLE_GRID_STEP
        generator.grid_inner_radius = HOLE_GRID_RADIUS_MIN

def get_gamma_sequence_for_model(expected_count: int) -> List[float]:
    """
    Get appropriate gamma sequence based on expected count.

    Args:
        expected_count: Expected number of holes (2 or 4)

    Returns:
        List of gamma values to try in order
    """
    if expected_count == 2:
        # DOSTPLUS: Different sequence
        return [2.0, 7.75, 4.0, 6.5, 1.0]
    else:
        # DOST or other: Original sequence
        return [2.0, 7.75, 4.0, 6.5, 1.0]

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def create_circular_mask(shape: Tuple[int, int], center: Tuple[int, int],
                        radius: int) -> np.ndarray:
    """
    Create a circular mask.

    Args:
        shape: (height, width) of image
        center: (cx, cy) center coordinates
        radius: Radius of circle

    Returns:
        Boolean mask (True inside circular region)
    """
    h, w = shape
    cx, cy = center

    y, x = np.ogrid[:h, :w]
    dist = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)

    mask = dist <= radius
    return mask


def extract_circular_region(image: np.ndarray, center: Tuple[int, int],
                            radius: int, fill_color: int = 0) -> Tuple[np.ndarray, np.ndarray]:
    """
    Extract circular region from image, painting outside as fill_color.

    Returns:
        Tuple of (cropped_image, mask)
    """
    h, w = image.shape[:2]

    # Create circular mask on full image
    mask = create_circular_mask((h, w), center, radius)

    # Apply mask to image
    if image.ndim == 3:
        result = image.copy()
        for c in range(3):
            result[:, :, c] = np.where(mask, image[:, :, c], fill_color)
    else:
        result = np.where(mask, image, fill_color)

    # Crop to bounding box of circle
    cx, cy = center
    x1 = max(0, cx - radius)
    x2 = min(w, cx + radius)
    y1 = max(0, cy - radius)
    y2 = min(h, cy + radius)

    cropped_image = result[y1:y2, x1:x2]
    cropped_mask = mask[y1:y2, x1:x2]

    return cropped_image, cropped_mask


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


def drop_middle_of_three_in_line(
        components: List[Tuple[Tuple[float, float], np.ndarray]]
) -> Tuple[List[Tuple[Tuple[float, float], np.ndarray]], Optional[str]]:
    """
    3 holes, one in the middle of the other two (more or less on the line between them, more or less equally far from
    both): return the outer two and a description; otherwise return the components unchanged and None.

    The middle hole is the one that is not in the farthest-apart pair. Straightness: 180 deg minus the angle at the
    middle hole <= THREE_HOLE_MAX_BEND. Equal distances: |d1 - d2| / mean(d1, d2) <= THREE_HOLE_MAX_DISTANCE_DIFFERENCE.
    """
    if len(components) != 3:
        return components, None
    pts = [np.array(c[0], dtype=float) for c in components]
    pairs = [(0, 1, 2), (0, 2, 1), (1, 2, 0)]              # (outer, outer, middle)
    a, b, m = max(pairs, key=lambda t: np.linalg.norm(pts[t[0]] - pts[t[1]]))
    v1, v2 = pts[a] - pts[m], pts[b] - pts[m]
    d1, d2 = float(np.linalg.norm(v1)), float(np.linalg.norm(v2))
    if d1 == 0 or d2 == 0:
        return components, None
    angle = float(np.degrees(np.arccos(np.clip(np.dot(v1, v2) / (d1 * d2), -1.0, 1.0))))
    bend = 180.0 - angle
    difference = abs(d1 - d2) / ((d1 + d2) / 2.0)
    text = (f"3 holes: middle one at ({pts[m][0]:.1f}, {pts[m][1]:.1f}), bend {bend:.1f} deg (max {THREE_HOLE_MAX_BEND}), "
            f"distances {d1:.1f} / {d2:.1f} px, difference {difference:.1%} (max {THREE_HOLE_MAX_DISTANCE_DIFFERENCE:.0%})")
    if bend <= THREE_HOLE_MAX_BEND and difference <= THREE_HOLE_MAX_DISTANCE_DIFFERENCE:
        return [components[a], components[b]], text + " - middle one ignored, treated as 2 holes"
    return components, text + " - kept as 3 holes"


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


def compute_polygon_centroid(points: List[Tuple[float, float]]) -> Tuple[float, float]:
    """
    Compute the centroid (geometric center) of a polygon.

    This is robust - the centroid of a polygon is ALWAYS inside a convex polygon
    and provides a reliable reference for angular sorting.

    Args:
        points: List of (x, y) tuples

    Returns:
        (cx, cy) centroid coordinates
    """
    if not points:
        return (0.0, 0.0)

    x_coords = [p[0] for p in points]
    y_coords = [p[1] for p in points]

    cx = sum(x_coords) / len(points)
    cy = sum(y_coords) / len(points)

    return (cx, cy)


def order_points_counterclockwise(points: List[Tuple[float, float]]) -> List[int]:
    """
    Order points in counterclockwise direction using polygon centroid.

    ROBUST: Uses the polygon's own centroid as reference, which is guaranteed
    to be a good reference point regardless of external center location.

    Args:
        points: List of (x, y) tuples

    Returns:
        List of indices in counterclockwise order
    """
    if len(points) < 2:
        return list(range(len(points)))

    # Use polygon's own centroid as reference
    centroid = compute_polygon_centroid(points)
    cx, cy = centroid

    # Calculate angles from centroid to each point
    angles_with_indices = []
    for i, (px, py) in enumerate(points):
        angle = np.arctan2(py - cy, px - cx)
        angles_with_indices.append((angle, i))

    # Sort by angle (counterclockwise)
    angles_with_indices.sort(key=lambda x: x[0])

    # Return indices in order
    return [idx for _, idx in angles_with_indices]


def signed_polygon_area(points: List[Tuple[float, float]]) -> float:
    """
    Calculate signed area of polygon using shoelace formula.

    Positive area = counterclockwise vertices
    Negative area = clockwise vertices

    Args:
        points: List of (x, y) tuples in order

    Returns:
        Signed area (positive for CCW, negative for CW)
    """
    n = len(points)
    if n < 3:
        return 0.0

    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += points[i][0] * points[j][1]
        area -= points[j][0] * points[i][1]

    return area / 2.0


def ensure_counterclockwise_order(points: List[Tuple[float, float]],
                                  indices: List[int]) -> List[int]:
    """
    Ensure points are in counterclockwise order.

    Uses signed area to detect orientation and reverses if needed.

    Args:
        points: Original list of all points
        indices: Indices of selected points

    Returns:
        Indices in counterclockwise order
    """
    if len(indices) < 3:
        return indices

    # Extract points in current order
    ordered_points = [points[i] for i in indices]

    # Check orientation using signed area
    signed_area = signed_polygon_area(ordered_points)

    # If clockwise (negative area), reverse
    if signed_area < 0:
        return list(reversed(indices))

    return indices


def order_polygon_vertices_robust(points: List[Tuple[float, float]],
                                  indices: List[int]) -> List[int]:
    """
    ROBUST polygon vertex ordering that works even when center is outside polygon.

    Method:
    1. Extract subset of points
    2. Calculate their centroid (always inside convex hull)
    3. Sort by angle from centroid
    4. Verify orientation using signed area

    Args:
        points: All points (superset)
        indices: Indices of points to order

    Returns:
        Indices in counterclockwise order
    """
    if len(indices) < 2:
        return indices

    # Extract subset
    subset_points = [points[i] for i in indices]

    # Method 1: Order using subset's centroid
    ordered_indices = order_points_counterclockwise(subset_points)

    # Map back to original indices
    result_indices = [indices[i] for i in ordered_indices]

    # Method 2: Verify and correct orientation using signed area
    result_indices = ensure_counterclockwise_order(points, result_indices)

    return result_indices

# =============================================================================
# ENHANCED find_best_equidistant_subset_euclidean WITH ROBUST ORDERING
# =============================================================================

def find_best_equidistant_subset_euclidean_robust(
        points: List[Tuple[float, float]],
        n: int,
        center: Tuple[float, float],  # Still provided but only for angular spacing check
        tolerance: float = 0.2,
        min_interior_angle: float = 65.0,
        max_interior_angle: float = 115.0,
        allowed_angle_std: float = 20.0
) -> Tuple[Optional[List[int]], float, dict]:
    """
    ROBUST VERSION: Find best subset forming regular polygon.

    KEY IMPROVEMENT: Uses polygon's own centroid for vertex ordering,
    not the external reference center. This works even when center is
    outside the polygon.

    The reference center is still used for angular spacing validation,
    but NOT for vertex ordering.

    Args:
        points: List of (x, y) tuples
        n: Target number of points
        center: Reference center (only for angular spacing check)
        tolerance: Max allowed deviation in side lengths
        min_interior_angle: Minimum interior angle (degrees)
        max_interior_angle: Maximum interior angle (degrees)
        allowed_angle_std: Maximum std deviation of angles (degrees)

    Returns:
        Tuple of (best_indices, best_score, diagnostics) or (None, inf, {})
    """
    _src = __name__

    if len(points) < n:
        return None, float('inf'), {}

    best_indices = None
    best_score = float('inf')
    best_diagnostics = {}

    combo_number = 0

    for combo_indices in combinations(range(len(points)), n):
        combo_number += 1

        # ROBUST ORDERING: Use polygon's own centroid
        ordered_indices = order_polygon_vertices_robust(points, list(combo_indices))

        if PRINT_GEOMETRIC_VALIDATION:
            logBoth('logDebug', _src, f"\n--- Testing combination #{combo_number}: indices {list(combo_indices)} ---", MessageType.GENERAL)
            logBoth('logDebug', _src, f"  Original order: {list(combo_indices)}", MessageType.GENERAL)
            logBoth('logDebug', _src, f"  Ordered (CCW):  {ordered_indices}", MessageType.GENERAL)

        # Extract ordered points
        ordered_points = [points[i] for i in ordered_indices]

        # Verify we got the right number
        if len(ordered_points) != n:
            continue

        # =================================================================
        # 1. CHECK SIDE LENGTHS
        # =================================================================
        distances = []
        for i in range(n):
            p1 = ordered_points[i]
            p2 = ordered_points[(i + 1) % n]
            dist = np.sqrt((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2)
            distances.append(dist)

        if PRINT_GEOMETRIC_VALIDATION:
            logBoth('logDebug', _src, f"  Side lengths: {[f'{d:.2f}' for d in distances]}", MessageType.GENERAL)

        median_dist = np.median(distances)
        if median_dist == 0:
            if PRINT_GEOMETRIC_VALIDATION:
                logBoth('logDebug', _src, f"  ✗ REJECTED: Median distance is zero", MessageType.GENERAL)
            continue

        deviations = [abs(d - median_dist) / median_dist for d in distances]
        max_deviation = max(deviations)

        if PRINT_GEOMETRIC_VALIDATION:
            logBoth('logDebug', _src, f"  Median distance: {median_dist:.2f}", MessageType.GENERAL)
            logBoth('logDebug', _src, f"  Max deviation: {max_deviation:.1%} (threshold: {tolerance:.1%})", MessageType.GENERAL)

        if max_deviation > tolerance:
            if PRINT_GEOMETRIC_VALIDATION:
                logBoth('logDebug', _src, f"  ✗ REJECTED: Distance deviation {max_deviation:.1%} > {tolerance:.1%}", MessageType.GENERAL)
            continue

        if PRINT_GEOMETRIC_VALIDATION:
            logBoth('logDebug', _src, f"  ✓ Distance check passed", MessageType.GENERAL)

        # =================================================================
        # 2. CHECK INTERIOR ANGLES
        # =================================================================
        interior_angles = []
        for i in range(n):
            p_prev = ordered_points[(i - 1) % n]
            p_curr = ordered_points[i]
            p_next = ordered_points[(i + 1) % n]

            # Vectors from current to previous and next
            v1 = np.array([p_prev[0] - p_curr[0], p_prev[1] - p_curr[1]])
            v2 = np.array([p_next[0] - p_curr[0], p_next[1] - p_curr[1]])

            # Calculate angle using atan2(cross, dot)
            dot = v1[0] * v2[0] + v1[1] * v2[1]
            det = v1[0] * v2[1] - v1[1] * v2[0]
            angle_rad = np.arctan2(det, dot)

            angle_deg = np.degrees(angle_rad)
            if angle_deg < 0:
                angle_deg += 360

            # Interior angle should be < 180
            if angle_deg > 180:
                angle_deg = 360 - angle_deg

            interior_angles.append(angle_deg)

        if PRINT_GEOMETRIC_VALIDATION:
            logBoth('logDebug', _src, f"  Interior angles: {[f'{a:.1f}°' for a in interior_angles]}", MessageType.GENERAL)

        min_angle = min(interior_angles)
        max_angle = max(interior_angles)

        if PRINT_GEOMETRIC_VALIDATION:
            logBoth('logDebug', _src, f"  Min angle: {min_angle:.1f}° (threshold: ≥{min_interior_angle}°)", MessageType.GENERAL)
            logBoth('logDebug', _src, f"  Max angle: {max_angle:.1f}° (threshold: ≤{max_interior_angle}°)", MessageType.GENERAL)

        # Check minimum angle requirement
        if min_angle < min_interior_angle:
            if PRINT_GEOMETRIC_VALIDATION:
                logBoth('logDebug', _src, f"  ✗ REJECTED: Min angle {min_angle:.1f}° < {min_interior_angle}°", MessageType.GENERAL)
            continue

        # Check maximum angle requirement (for squares/rectangles)
        if max_angle > max_interior_angle:
            if PRINT_GEOMETRIC_VALIDATION:
                logBoth('logDebug', _src, f"  ✗ REJECTED: Max angle {max_angle:.1f}° > {max_interior_angle}°", MessageType.GENERAL)
            continue

        if PRINT_GEOMETRIC_VALIDATION:
            logBoth('logDebug', _src, f"  ✓ Angle range check passed", MessageType.GENERAL)

        # Special checks for Square (4 holes)
        if n == 4:
            if PRINT_GEOMETRIC_VALIDATION:
                logBoth('logDebug', _src, f"  [4-hole square checks]", MessageType.GENERAL)

            angle_std = np.std(interior_angles)
            if PRINT_GEOMETRIC_VALIDATION:
                logBoth('logDebug', _src, f"  Angle std dev: {angle_std:.1f}° (threshold: ≤{allowed_angle_std}°)", MessageType.GENERAL)
            if angle_std > allowed_angle_std:
                if PRINT_GEOMETRIC_VALIDATION:
                    logBoth('logDebug', _src, f"  ✗ REJECTED: Angle std {angle_std:.1f}° > {allowed_angle_std}°", MessageType.GENERAL)
                continue
            if PRINT_GEOMETRIC_VALIDATION:
                logBoth('logDebug', _src, f"  ✓ Angle uniformity check passed", MessageType.GENERAL)

            mean_angle = np.mean(interior_angles)
            if PRINT_GEOMETRIC_VALIDATION:
                logBoth('logDebug', _src, f"  Mean angle: {mean_angle:.1f}° (target: 90° ± {allowed_angle_std}°)", MessageType.GENERAL)
            if abs(mean_angle - 90.0) > allowed_angle_std:
                if PRINT_GEOMETRIC_VALIDATION:
                    logBoth('logDebug', _src, f"  ✗ REJECTED: Mean angle {mean_angle:.1f}° too far from 90°", MessageType.GENERAL)
                continue
            if PRINT_GEOMETRIC_VALIDATION:
                logBoth('logDebug', _src, f"  ✓ Mean angle check passed", MessageType.GENERAL)

        # =================================================================
        # 3. CHECK ANGULAR SPACING FROM REFERENCE CENTER (optional)
        # =================================================================
        # This uses the external reference center for validation
        angles_from_center = []
        for px, py in ordered_points:
            angle = np.arctan2(py - center[1], px - center[0])
            angles_from_center.append(angle)

        # Calculate angular spacing
        angles_between = []
        for i in range(n):
            a1 = angles_from_center[i]
            a2 = angles_from_center[(i + 1) % n]

            angle_diff = a2 - a1
            if angle_diff < 0:
                angle_diff += 2 * np.pi

            angles_between.append(np.degrees(angle_diff))

        expected_angle = 360.0 / n
        angle_deviations = [abs(a - expected_angle) / expected_angle
                            for a in angles_between]
        max_angle_deviation = max(angle_deviations)

        if PRINT_GEOMETRIC_VALIDATION:
            logBoth('logDebug', _src, f"  Angular spacing from ref center: {[f'{a:.1f}°' for a in angles_between]}", MessageType.GENERAL)
            logBoth('logDebug', _src, f"  Expected: {expected_angle:.1f}° each", MessageType.GENERAL)
            logBoth('logDebug', _src, f"  Max deviation: {max_angle_deviation:.1%}", MessageType.GENERAL)

        # =================================================================
        # 4. CALCULATE SCORE
        # =================================================================
        # Combine distance deviation and angular spacing
        score = (0.7 * max_deviation + 0.3 * max_angle_deviation)
        if PRINT_GEOMETRIC_VALIDATION:
            logBoth('logDebug', _src, f"  Combined score: {score:.4f} (lower is better)", MessageType.GENERAL)

        if score < best_score:
            if PRINT_GEOMETRIC_VALIDATION:
                logBoth('logDebug', _src, f"  ★ NEW BEST! (previous best: {best_score:.4f})", MessageType.GENERAL)
            best_score = score
            best_indices = ordered_indices

            best_diagnostics = {
                'distances': distances,
                'median_distance': median_dist,
                'max_distance_deviation': max_deviation,
                'angles': angles_between,
                'interior_angles': interior_angles,
                'min_interior_angle': min_angle,
                'max_interior_angle': max_angle,
                'expected_angle': expected_angle,
                'max_angle_deviation': max_angle_deviation,
                'score': score
            }
        else:
            if PRINT_GEOMETRIC_VALIDATION:
                logBoth('logDebug', _src, f"  Score not better than current best ({best_score:.4f})", MessageType.GENERAL)

    if PRINT_GEOMETRIC_VALIDATION:
        logBoth('logDebug', _src, f"\n{'=' * 70}", MessageType.GENERAL)
        if best_indices is None:
            logBoth('logDebug', _src, f"FINAL RESULT: No valid combination found after testing {combo_number} combinations", MessageType.GENERAL)
        else:
            logBoth('logDebug', _src, f"FINAL RESULT: Best combination found with score {best_score:.4f}", MessageType.GENERAL)
            logBoth('logDebug', _src, f"  Best indices (CCW ordered): {best_indices}", MessageType.GENERAL)

    return best_indices, best_score, best_diagnostics

# =============================================================================
# MOBILESAM MASK GENERATOR
# =============================================================================

class MobileSAMv2AutomaticMaskGenerator:
    """
    MobileSAMv2 Automatic Mask Generator (V0.7 optimized).

    Uses YOLOv8 for object detection + MobileSAM for segmentation.
    """

    def __init__(
            self,
            sam_predictor,
            yolo_model,
            conf_threshold: float = 0.3,
            iou_threshold: float = 0.7,
            min_mask_region_area: int = MIN_MASK_AREA,
            max_mask_region_area: int = MAX_MASK_AREA,
            use_grid_points: bool = True,
            points_per_side: int = 32,
            grid_center: Optional[Tuple[float, float]] = None,
            grid_radius: Optional[float] = None,
            grid_step: Optional[int] = None,
            grid_inner_radius: float = 0.0,
    ):
        """
        Args:
            sam_predictor: MobileSAM SamPredictor instance
            yolo_model: Loaded YOLO model for object detection
            conf_threshold: Confidence threshold for YOLO detections
            iou_threshold: IoU threshold for NMS
            min_mask_region_area: Minimum mask area to keep
            max_mask_region_area: Maximum mask area to keep
            use_grid_points: If True, also use grid points
            points_per_side: Number of points per side for grid (sparse grid, only when grid_step is None)
            grid_center, grid_radius, grid_step: when grid_step is set, a point prompt every grid_step px inside the
                disc (grid_center, grid_radius) of the image, ALWAYS added to the YOLO box masks (no sparse grid)
        """
        self.predictor = sam_predictor
        self.yolo_model = yolo_model
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.min_mask_region_area = min_mask_region_area
        self.max_mask_region_area = max_mask_region_area
        self.use_grid_points = use_grid_points
        self.points_per_side = points_per_side
        self.grid_center = grid_center
        self.grid_radius = grid_radius
        self.grid_step = grid_step
        self.grid_inner_radius = grid_inner_radius
        self.last_counts = {"yolo": 0, "grid": 0, "grid_points": 0, "grid_outside": 0}

    def generate(self, image: np.ndarray) -> List[Dict[str, Any]]:
        """
        Generate masks for all objects in the image.

        Args:
            image: RGB image as numpy array (H, W, 3)

        Returns:
            List of mask dictionaries with 'segmentation', 'area', 'bbox', etc.
        """
        masks = []

        # Set image for SAM predictor
        self.predictor.set_image(image)

        # Run YOLO object detection
        try:
            results = self.yolo_model.predict(
                image,
                conf=self.conf_threshold,
                iou=self.iou_threshold,
                verbose=False
            )
        except Exception as e:
            if PRINT_SAM_SEGMENTATION:
                logBoth('logDebug', __name__, f"YOLO prediction error: {e}", MessageType.GENERAL)
            results = []

        # Get bounding boxes from YOLO
        boxes = []
        if len(results) > 0 and results[0].boxes is not None:
            boxes = results[0].boxes.xyxy.cpu().numpy()

        if PRINT_SAM_SEGMENTATION:
            logBoth('logDebug', __name__, f"[YOLO] Detected {len(boxes)} boxes", MessageType.GENERAL)

        # Generate masks using box prompts
        if len(boxes) > 0:
            for box in boxes:
                try:
                    mask_data = self._generate_mask_from_box(image, box)
                    if mask_data is not None:
                        area = mask_data['area']
                        if self.min_mask_region_area <= area <= self.max_mask_region_area:
                            mask_data['source'] = 'yolo'
                            masks.append(mask_data)
                except Exception as e:
                    if PRINT_SAM_SEGMENTATION:
                        logBoth('logDebug', __name__, f"Warning: Failed to generate mask for box {box}: {e}", MessageType.GENERAL)
        self.last_counts = {"yolo": len(masks), "grid": 0, "grid_points": 0, "grid_outside": 0}

        # Dense grid of point prompts over the hole search disc: always, so that every hole gets prompts whatever
        # YOLO proposes (YOLO's boxes depend on the highlights, which move when the bung is turned)
        if self.grid_step:
            grid_masks, n_points = self._generate_disc_grid_masks(image)
            masks.extend(grid_masks)
            self.last_counts.update(grid=len(grid_masks), grid_points=n_points, grid_outside=n_points - len(grid_masks))
            return masks

        # Optionally add grid-based point prompts
        if self.use_grid_points and len(masks) < 5:
            grid_masks = self._generate_grid_masks(image)
            for gm in grid_masks:
                if not self._overlaps_existing(gm['segmentation'], masks):
                    area = gm['area']
                    if self.min_mask_region_area <= area <= self.max_mask_region_area:
                        masks.append(gm)

        return masks

    def _generate_mask_from_box(self, image: np.ndarray, box: np.ndarray) -> Optional[Dict[str, Any]]:
        """Generate a single mask from a bounding box."""
        x1, y1, x2, y2 = box

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

        # Sparse grid sampling (1/8th of points)
        step = max(1, (h // self.points_per_side) * 8)

        for y in range(0, h, step):
            for x in range(0, w, step):
                try:
                    point = np.array([[x, y]])
                    label = np.array([1])

                    pred_masks, scores, _ = self.predictor.predict(
                        point_coords=point,
                        point_labels=label,
                        multimask_output=False
                    )

                    if pred_masks is not None and len(pred_masks) > 0:
                        mask = pred_masks[0]
                        area = int(mask.sum())

                        if area > 0:
                            masks.append({
                                'segmentation': mask,
                                'area': area,
                                'bbox': self._mask_to_bbox(mask),
                                'predicted_iou': float(scores[0]) if scores is not None else 0.0,
                                'stability_score': float(scores[0]) if scores is not None else 0.0,
                            })
                except Exception:
                    continue

        return masks

    def disc_grid_points(self, shape: Tuple[int, int]) -> List[Tuple[int, int]]:
        """Point prompts every grid_step px in the band grid_inner_radius..grid_radius round grid_center, on a grid
        through the centre."""
        h, w = shape[:2]
        cx, cy = self.grid_center if self.grid_center is not None else (w / 2.0, h / 2.0)
        radius = self.grid_radius if self.grid_radius is not None else min(h, w) / 2.0
        step = int(self.grid_step)
        n = int(radius // step)
        points = []
        for j in range(-n, n + 1):
            for i in range(-n, n + 1):
                x, y = cx + i * step, cy + j * step
                r2 = (i * step) ** 2 + (j * step) ** 2
                if self.grid_inner_radius ** 2 <= r2 <= radius ** 2 and 0 <= x < w and 0 <= y < h:
                    points.append((int(round(x)), int(round(y))))
        return points

    def _generate_disc_grid_masks(self, image: np.ndarray, batch_size: int = 32) -> Tuple[List[Dict[str, Any]], int]:
        """One mask per point prompt of disc_grid_points (the image must already be set in the predictor), decoded in
        batches; masks outside min/max_mask_region_area are dropped at once. Returns (masks, number of points)."""
        points = self.disc_grid_points(image.shape)
        masks = []
        predictor = self.predictor
        for start in range(0, len(points), batch_size):
            chunk = points[start:start + batch_size]
            coords = predictor.transform.apply_coords(np.array(chunk, dtype=np.float64), image.shape[:2])
            coords_t = torch.as_tensor(coords, dtype=torch.float32, device=predictor.device)[:, None, :]
            labels_t = torch.ones((len(chunk), 1), dtype=torch.int, device=predictor.device)
            # Decode at the model's low resolution, estimate each mask's area there and bring up to full size only
            # the masks that can pass the area window (with 30 % slack; the exact area is checked at full size)
            model = predictor.model
            sparse, dense = model.prompt_encoder(points=(coords_t, labels_t), boxes=None, masks=None)
            low_res, iou_pred = model.mask_decoder(image_embeddings=predictor.features,
                                                   image_pe=model.prompt_encoder.get_dense_pe(),
                                                   sparse_prompt_embeddings=sparse, dense_prompt_embeddings=dense,
                                                   multimask_output=False)
            px_per_low = (image.shape[0] * image.shape[1]) / float(
                (low_res.shape[-2] * predictor.input_size[0] / getattr(predictor.model.image_encoder, "img_size", 1024)) *
                (low_res.shape[-1] * predictor.input_size[1] / getattr(predictor.model.image_encoder, "img_size", 1024)))
            low_areas = (low_res[:, 0] > model.mask_threshold).flatten(1).sum(1).float() * px_per_low
            maybe = ((low_areas >= 0.7 * self.min_mask_region_area) &
                     (low_areas <= 1.3 * self.max_mask_region_area)).nonzero().flatten()
            if len(maybe) == 0:
                continue
            pred = model.postprocess_masks(low_res[maybe], predictor.input_size, predictor.original_size)
            pred = pred[:, 0] > model.mask_threshold
            scores = iou_pred[maybe]
            chunk = [chunk[int(i)] for i in maybe.cpu().numpy()]
            areas = pred.flatten(1).sum(1)
            keep = ((areas >= self.min_mask_region_area) & (areas <= self.max_mask_region_area)).nonzero().flatten()
            if len(keep) == 0:
                continue
            kept_masks = pred[keep].cpu().numpy()
            kept_scores = scores[keep, 0].float().cpu().numpy()
            kept_areas = areas[keep].cpu().numpy()
            for k, idx in enumerate(keep.cpu().numpy()):
                mask = kept_masks[k]
                masks.append({
                    'segmentation': mask,
                    'area': int(kept_areas[k]),
                    'bbox': self._mask_to_bbox(mask),
                    'predicted_iou': float(kept_scores[k]),
                    'stability_score': float(kept_scores[k]),
                    'source': 'grid',
                    'point': chunk[int(idx)],
                })
        return masks, len(points)

    def _overlaps_existing(self, new_mask: np.ndarray, existing_masks: List[Dict], threshold: float = 0.5) -> bool:
        """Check if new mask overlaps significantly with existing masks."""
        for existing in existing_masks:
            intersection = (new_mask & existing['segmentation']).sum()
            union = (new_mask | existing['segmentation']).sum()

            if union > 0:
                iou = intersection / union
                if iou > threshold:
                    return True

        return False

    def _mask_to_bbox(self, mask: np.ndarray) -> List[int]:
        """Convert binary mask to bounding box [x, y, w, h]."""
        rows = np.any(mask, axis=1)
        cols = np.any(mask, axis=0)
        if not rows.any() or not cols.any():
            return [0, 0, 0, 0]
        y1, y2 = np.where(rows)[0][[0, -1]]
        x1, x2 = np.where(cols)[0][[0, -1]]
        return [int(x1), int(y1), int(x2 - x1 + 1), int(y2 - y1 + 1)]


# =============================================================================
# Bung SEGMENTER CLASS
# =============================================================================

class BungSegmenter:
    """
    Bung hole segmentation using MobileSAMv2 with V0.7 pipeline.

    Two-stage cropping + bilateral filtering + gamma correction + SAM + geometric validation.
    """

    _instance: Optional['BungSegmenter'] = None
    _initialized = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, mobile_sam_checkpoint: Optional[str] = None,
                 yolo_checkpoint: Optional[str] = None):
        """
        Initialize BungSegmenter.

        Args:
            mobile_sam_checkpoint: Path to mobile_sam.pt (optional, uses ModelManager)
            yolo_checkpoint: Path to ObjectAwareModel.pt (optional, uses ModelManager)
        """

        if BungSegmenter._initialized:
            return  # Already initialized

        _src = getFullyQualifiedName(__file__, BungSegmenter)

        if PRINT_PREPROCESSING:
            logBoth('logDebug', _src, "[BungSegmenter] Initializing with V0.7 pipeline...", MessageType.GENERAL)

        # Load models using ModelManager
        self.model_manager = ModelManager.get_instance()
        self.device = self.model_manager.get_device()

        # Load SAM
        if PRINT_PREPROCESSING:
            logBoth('logDebug', _src, "[BungSegmenter] Loading MobileSAM...", MessageType.GENERAL)
        self.sam_predictor = self.model_manager.get_sam_predictor()

        # Load YOLO
        if PRINT_PREPROCESSING:
            logBoth('logDebug', _src, "[BungSegmenter] Loading YOLO ObjectAwareModel...", MessageType.GENERAL)
        self.yolo_model = self.model_manager.get_yolo_model()

        # Create mask generator
        self.mask_generator = MobileSAMv2AutomaticMaskGenerator(
            sam_predictor=self.sam_predictor,
            yolo_model=self.yolo_model,
            conf_threshold=0.3,
            iou_threshold=0.7,
            min_mask_region_area=MIN_MASK_AREA,
            max_mask_region_area=MAX_MASK_AREA,
            use_grid_points=True,
            points_per_side=32,
            grid_center=(OUTER_DISC_RADIUS, OUTER_DISC_RADIUS),      # the hole search disc in the outer-disc crop
            grid_radius=min(INNER_DISC_RADIUS, HOLE_GRID_RADIUS_MAX),
            grid_step=HOLE_GRID_STEP,
            grid_inner_radius=HOLE_GRID_RADIUS_MIN
        )
        self.last_gamma_text = ""

        # Precompute gamma LUTs for all gamma values
        if PRINT_PREPROCESSING:
            logBoth('logDebug', _src, f"[BungSegmenter] Precomputing gamma LUTs for {GAMMA_SEQUENCE}...", MessageType.GENERAL)
        self.gamma_luts = {}
        for gamma in GAMMA_SEQUENCE:
            inv_gamma = 1.0 / gamma
            lut = np.array([((i / 255.0) ** inv_gamma) * 255
                            for i in range(256)], dtype=np.uint8)
            self.gamma_luts[gamma] = lut

        if PRINT_PREPROCESSING:
            logBoth('logDebug', _src, "[BungSegmenter] ✓ Initialization complete", MessageType.GENERAL)

        BungSegmenter._initialized = True

    @classmethod
    def get_instance(cls) -> 'BungSegmenter':
        """Get the singleton instance."""
        if cls._instance is None:
            cls._instance = BungSegmenter(None, None)
        return cls._instance

    def _preprocess_image(self, image: np.ndarray, gamma: float) -> np.ndarray:
        """
        V0.7 Preprocessing Pipeline:

        1. Extract outer disc (630, 350, R=250)
        2. Bilateral filter (21, 30, 30)
        3. Gamma correction
        4. Bilateral filter (21, 30, 50)

        Args:
            image: Input RGB image (full resolution)
            gamma: Gamma correction value

        Returns:
            Processed outer disc image ready for SAM
        """
        # Step 1: Extract outer disc
        outer_disc, outer_mask = extract_circular_region(
            image,
            center=OUTER_DISC_CENTER,
            radius=OUTER_DISC_RADIUS,
            fill_color=0
        )

        # Step 2: First bilateral filter
        bilateral1 = cv2.bilateralFilter(outer_disc, d=21, sigmaColor=30, sigmaSpace=30)

        # Step 3: Gamma correction
        gamma_lut = self.gamma_luts[gamma]
        gamma_corrected = gamma_lut[bilateral1]

        # Step 4: Second bilateral filter
        bilateral2 = cv2.bilateralFilter(gamma_corrected, d=21, sigmaColor=30, sigmaSpace=50)

        return bilateral2

    def _extract_and_filter_masks_in_inner_disc(
            self,
            all_masks: List[Dict[str, Any]],
            outer_disc_image: np.ndarray
    ) -> Tuple[List[Tuple[Tuple[float, float], np.ndarray]], int]:
        """
        Extract inner disc and filter masks by area.

        Args:
            all_masks: All masks from SAM (in outer disc coordinates)
            outer_disc_image: Outer disc image for reference

        Returns:
            Tuple of (filtered_components, count)
            filtered_components: List of ((cx, cy), contour) in inner disc coordinates
        """
        _src = getFullyQualifiedName(__file__, BungSegmenter)

        if PRINT_MASK_FILTERING:
            logBoth('logDebug', _src, f"\n{'=' * 70}", MessageType.GENERAL)
            logBoth('logDebug', _src, f"INNER DISC FILTERING", MessageType.GENERAL)
            logBoth('logDebug', _src, f"{'=' * 70}", MessageType.GENERAL)
            logBoth('logDebug', _src, f"Total SAM masks: {len(all_masks)}", MessageType.GENERAL)

        # Center of outer disc in cropped coordinates
        outer_h, outer_w = outer_disc_image.shape[:2]
        inner_center = (OUTER_DISC_RADIUS, OUTER_DISC_RADIUS)

        # Extract inner disc mask
        inner_disc_mask = create_circular_mask(
            (outer_h, outer_w),
            inner_center,
            INNER_DISC_RADIUS
        )

        # Calculate bounding box of inner disc
        cx, cy = inner_center
        x1 = max(0, cx - INNER_DISC_RADIUS)
        x2 = min(outer_w, cx + INNER_DISC_RADIUS)
        y1 = max(0, cy - INNER_DISC_RADIUS)
        y2 = min(outer_h, cy + INNER_DISC_RADIUS)

        if PRINT_MASK_FILTERING:
            logBoth('logDebug', _src, f"Inner disc center: {inner_center}", MessageType.GENERAL)
            logBoth('logDebug', _src, f"Inner disc radius: {INNER_DISC_RADIUS}", MessageType.GENERAL)
            logBoth('logDebug', _src, f"Inner disc bbox: x=[{x1}, {x2}], y=[{y1}, {y2}]", MessageType.GENERAL)
            logBoth('logDebug', _src, f"Area filter range: [{MIN_MASK_AREA}, {MAX_MASK_AREA}] pixels", MessageType.GENERAL)

        # Crop inner disc mask
        inner_disc_mask_crop = inner_disc_mask[y1:y2, x1:x2]

        # Filter masks
        valid_components = []
        dropped_masks = []

        if PRINT_MASK_FILTERING:
            logBoth('logDebug', _src, f"\n{'─' * 70}", MessageType.GENERAL)
            logBoth('logDebug', _src, f"MASK-BY-MASK FILTERING:", MessageType.GENERAL)
            logBoth('logDebug', _src, f"{'─' * 70}", MessageType.GENERAL)

        for idx, mask_data in enumerate(all_masks):
            mask = mask_data['segmentation']
            original_area = mask_data['area']

            # Crop mask to inner disc region
            mask_crop = mask[y1:y2, x1:x2]

            # Check presence in inner disc
            mask_in_inner = mask_crop & inner_disc_mask_crop
            pixels_in_inner = mask_in_inner.sum()

            if PRINT_MASK_FILTERING:
                logBoth('logDebug', _src, f"\nMask #{idx}:", MessageType.GENERAL)
                logBoth('logDebug', _src, f"  Original area: {original_area} pixels", MessageType.GENERAL)
                logBoth('logDebug', _src, f"  Pixels in inner disc: {pixels_in_inner}", MessageType.GENERAL)

            # Filter by area
            if pixels_in_inner < MIN_MASK_AREA:
                if PRINT_MASK_FILTERING:
                    logBoth('logDebug', _src, f"  ✗ DROPPED: Too small ({pixels_in_inner} < {MIN_MASK_AREA})", MessageType.GENERAL)
                dropped_masks.append({
                    'index': idx,
                    'reason': 'too_small',
                    'area': pixels_in_inner,
                    'threshold': MIN_MASK_AREA
                })
                continue

            if pixels_in_inner > MAX_MASK_AREA:
                if PRINT_MASK_FILTERING:
                    logBoth('logDebug', _src, f"  ✗ DROPPED: Too large ({pixels_in_inner} > {MAX_MASK_AREA})", MessageType.GENERAL)
                dropped_masks.append({
                    'index': idx,
                    'reason': 'too_large',
                    'area': pixels_in_inner,
                    'threshold': MAX_MASK_AREA
                })
                continue

            # Extract contour
            mask_uint8 = (mask_in_inner * 255).astype(np.uint8)
            contours, _ = cv2.findContours(mask_uint8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            if len(contours) > 0:
                contour = max(contours, key=cv2.contourArea)

                # Calculate center in inner disc coordinates
                M = cv2.moments(contour)
                if M['m00'] != 0:
                    cx_inner = M['m10'] / M['m00']
                    cy_inner = M['m01'] / M['m00']

                    if PRINT_MASK_FILTERING:
                        logBoth('logDebug', _src, f"  ✓ KEPT: area={pixels_in_inner}, center=({cx_inner:.1f}, {cy_inner:.1f})", MessageType.GENERAL)
                    valid_components.append(((cx_inner, cy_inner), contour))
                else:
                    if PRINT_MASK_FILTERING:
                        logBoth('logDebug', _src, f"  ✗ DROPPED: Invalid moments (M['m00']=0)", MessageType.GENERAL)
                    dropped_masks.append({
                        'index': idx,
                        'reason': 'invalid_moments',
                        'area': pixels_in_inner
                    })
            else:
                if PRINT_MASK_FILTERING:
                    logBoth('logDebug', _src, f"  ✗ DROPPED: No contours found", MessageType.GENERAL)
                dropped_masks.append({
                    'index': idx,
                    'reason': 'no_contours',
                    'area': pixels_in_inner
                })

        if PRINT_MASK_FILTERING:
            logBoth('logDebug', _src, f"\n{'=' * 70}", MessageType.GENERAL)
            logBoth('logDebug', _src, f"FILTERING SUMMARY", MessageType.GENERAL)
            logBoth('logDebug', _src, f"{'=' * 70}", MessageType.GENERAL)
            logBoth('logDebug', _src, f"Total masks: {len(all_masks)}", MessageType.GENERAL)
            logBoth('logDebug', _src, f"Kept: {len(valid_components)}", MessageType.GENERAL)
            logBoth('logDebug', _src, f"Dropped: {len(dropped_masks)}", MessageType.GENERAL)

            if dropped_masks:
                logBoth('logDebug', _src, f"\nDropped mask breakdown:", MessageType.GENERAL)
                reason_counts = {}
                for dm in dropped_masks:
                    reason = dm['reason']
                    reason_counts[reason] = reason_counts.get(reason, 0) + 1
                for reason, count in reason_counts.items():
                    logBoth('logDebug', _src, f"  {reason}: {count}", MessageType.GENERAL)

            logBoth('logDebug', _src, f"\nKept mask details:", MessageType.GENERAL)
            for i, (center, contour) in enumerate(valid_components):
                area = cv2.contourArea(contour)
                logBoth('logDebug', _src, f"  Component {i}: center=({center[0]:.1f}, {center[1]:.1f}), area={area:.0f}", MessageType.GENERAL)

            logBoth('logDebug', _src, f"{'=' * 70}\n", MessageType.GENERAL)

        return valid_components, len(valid_components)

    def _morphological_filter_components(
            self,
            components: List[Tuple[Tuple[float, float], np.ndarray]],
            inner_disc_shape: Tuple[int, int],
            dropped: Optional[List[Tuple[float, float, str]]] = None
    ) -> List[Tuple[Tuple[float, float], np.ndarray]]:
        """
        Apply morphological filtering to clean up components (dropped: if given, (x, y, why) per dropped component).

        Pipeline:
        1. Create black image
        2. Draw white masks with boundaries
        3. Morphological opening (ellipse kernel 5)
        4. Find connected components
        5. Filter by bounding box dimensions (50-150% of EXPECTED_RADIUS_OF_HOLE)
        6. Filter by area (≥40% of bbox area)

        Args:
            components: List of (center, contour) tuples
            inner_disc_shape: Shape of inner disc image (height, width)

        Returns:
            Filtered list of (center, contour) tuples
        """
        if not components:
            return components

        _src = getFullyQualifiedName(__file__, BungSegmenter)

        if PRINT_MASK_FILTERING:
            logBoth('logDebug', _src, f"\n{'=' * 70}", MessageType.GENERAL)
            logBoth('logDebug', _src, f"MORPHOLOGICAL FILTERING", MessageType.GENERAL)
            logBoth('logDebug', _src, f"{'=' * 70}", MessageType.GENERAL)
            logBoth('logDebug', _src, f"Input components: {len(components)}", MessageType.GENERAL)

        h, w = inner_disc_shape

        # Step 1: Create all-black image
        black_image = np.zeros((h, w), dtype=np.uint8)

        # Step 2: Draw masks in white (filled + boundary)
        for center, contour in components:
            cv2.drawContours(black_image, [contour], -1, 255, thickness=cv2.FILLED)
            cv2.drawContours(black_image, [contour], -1, 255, thickness=1)

        if PRINT_MASK_FILTERING:
            logBoth('logDebug', _src, f"Drew {len(components)} masks on black image", MessageType.GENERAL)

        # Step 3: Morphological opening with elliptical kernel 5
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        opened = cv2.morphologyEx(black_image, cv2.MORPH_OPEN, kernel)

        if PRINT_MASK_FILTERING:
            logBoth('logDebug', _src, f"Applied morphological opening (ellipse kernel 5)", MessageType.GENERAL)

        # Step 4: Find connected components
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
            opened, connectivity=8
        )

        if PRINT_MASK_FILTERING:
            logBoth('logDebug', _src, f"Found {num_labels - 1} connected components (excluding background)", MessageType.GENERAL)

        # Step 5 & 6: Filter by bounding box dimensions and area
        filtered_components = []

        min_dim = int(0.5 * EXPECTED_RADIUS_OF_HOLE)  # 50% of 30 = 15
        max_dim = int(1.5 * EXPECTED_RADIUS_OF_HOLE)  # 150% of 30 = 45

        if PRINT_MASK_FILTERING:
            logBoth('logDebug', _src, f"\nFiltering criteria:", MessageType.GENERAL)
            logBoth('logDebug', _src, f"  Bounding box dimensions: [{min_dim}, {max_dim}] pixels", MessageType.GENERAL)
            logBoth('logDebug', _src, f"  Min area ratio: 40% of bbox area", MessageType.GENERAL)
            logBoth('logDebug', _src, f"\nComponent-by-component:", MessageType.GENERAL)

        for label_id in range(1, num_labels):  # Skip 0 (background)
            # Get bounding box
            x, y, bbox_w, bbox_h, area = stats[label_id]

            if PRINT_MASK_FILTERING:
                logBoth('logDebug', _src, f"\n  Component {label_id}:", MessageType.GENERAL)
                logBoth('logDebug', _src, f"    Bbox: ({x}, {y}), size: {bbox_w}x{bbox_h}", MessageType.GENERAL)
                logBoth('logDebug', _src, f"    Area: {area}", MessageType.GENERAL)

            # Check dimension constraints
            where = (x + bbox_w / 2.0, y + bbox_h / 2.0)
            if bbox_w < min_dim or bbox_h < min_dim:
                if dropped is not None:
                    dropped.append((*where, f"{bbox_w}x{bbox_h} px, smaller than {min_dim}"))
                if PRINT_MASK_FILTERING:
                    logBoth('logDebug', _src, f"    ✗ DROPPED: Dimension too small (min={min_dim})", MessageType.GENERAL)
                continue

            if bbox_w > max_dim or bbox_h > max_dim:
                if dropped is not None:
                    dropped.append((*where, f"{bbox_w}x{bbox_h} px, larger than {max_dim}"))
                if PRINT_MASK_FILTERING:
                    logBoth('logDebug', _src, f"    ✗ DROPPED: Dimension too large (max={max_dim})", MessageType.GENERAL)
                continue

            # Check area constraint
            bbox_area = bbox_w * bbox_h
            min_area = 0.4 * bbox_area

            if area < min_area:
                if dropped is not None:
                    dropped.append((*where, f"fills {area / bbox_area:.0%} of its box, < 40%"))
                if PRINT_MASK_FILTERING:
                    logBoth('logDebug', _src, f"    ✗ DROPPED: Area {area} < 40% of bbox area ({min_area:.0f})", MessageType.GENERAL)
                continue

            # Extract contour for this component
            component_mask = (labels == label_id).astype(np.uint8) * 255
            contours, _ = cv2.findContours(component_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            if len(contours) > 0:
                contour = max(contours, key=cv2.contourArea)

                # Calculate centroid
                M = cv2.moments(contour)
                if M['m00'] != 0:
                    cx = M['m10'] / M['m00']
                    cy = M['m01'] / M['m00']

                    filtered_components.append(((cx, cy), contour))
                    if PRINT_MASK_FILTERING:
                        logBoth('logDebug', _src, f"    ✓ KEPT: center=({cx:.1f}, {cy:.1f})", MessageType.GENERAL)

        if PRINT_MASK_FILTERING:
            logBoth('logDebug', _src, f"\n{'=' * 70}", MessageType.GENERAL)
            logBoth('logDebug', _src, f"MORPHOLOGICAL FILTERING RESULT", MessageType.GENERAL)
            logBoth('logDebug', _src, f"{'=' * 70}", MessageType.GENERAL)
            logBoth('logDebug', _src, f"Input: {len(components)} components", MessageType.GENERAL)
            logBoth('logDebug', _src, f"Output: {len(filtered_components)} components", MessageType.GENERAL)
            logBoth('logDebug', _src, f"Dropped: {len(components) - len(filtered_components)} components", MessageType.GENERAL)
            logBoth('logDebug', _src, f"{'=' * 70}\n", MessageType.GENERAL)

        return filtered_components

    def _apply_cascade_geometric_validation(
            self,
            components: List[Tuple[Tuple[float, float], np.ndarray]],
            expected_count: int,
            model_type: str
    ) -> Tuple[int, List[Tuple[Tuple[float, float], np.ndarray]], bool]:
        """
        Hole pattern among the candidates (inner-disc coordinates).

        1. 3 candidates with one in the middle of the other two (the centre punch mark): the middle one is ignored.
        2. 4 or more candidates: the best 4 forming a square (side lengths and interior angles,
           find_best_equidistant_subset_euclidean_robust). Found = definitive: 4 holes (expected 4: OK; expected 2:
           a DOST bung, NOT OK). A wrong bung is decided only on a real square, never on a count of blobs.
        3. Expected 2, no square: of all pairs dostplus.hole.pair.distance.min..max apart, the pair whose midpoint is
           nearest the disc centre (the 2 holes are opposite each other about the bung centre), leaving out a pair
           that a third candidate makes into 3 corners of a square (3 of a DOST bung's 4 holes: its diagonal is as
           long as the DOSTPLUS pair). Found = definitive: 2 holes.
        Anything else: not definitive (the next gamma is tried).

        Returns:
            Tuple of (final_count, final_components, found_definitive_pattern); the decision in words is left in
            self.last_decision_text.
        """
        _src = getFullyQualifiedName(__file__, BungSegmenter)
        reference_center = (INNER_DISC_RADIUS, INNER_DISC_RADIUS)
        valid_components = list(components)
        count = len(valid_components)
        notes = []

        if count == 3:
            valid_components, three_hole_text = drop_middle_of_three_in_line(valid_components)
            count = len(valid_components)
            if three_hole_text:
                notes.append(three_hole_text)

        if count >= 4:
            centers = [center for center, contour in valid_components]
            # the limits passed explicitly: the defaults are bound at import (DOST's, and no live reload) - CHANGES 215
            best_indices, best_score, diagnostics = find_best_equidistant_subset_euclidean_robust(
                centers, 4, reference_center, tolerance=0.2,
                min_interior_angle=65.0, max_interior_angle=115.0,
                allowed_angle_std=20.0)
            if best_indices is not None:
                square = [valid_components[i] for i in best_indices]
                sides = self._square_sides([c for c, _ in square])
                if expected_count == 4:
                    text = f"4-hole square (sides {sides}) -> OK"
                else:
                    text = f"4-hole square (sides {sides}) = DOST bung, expected {expected_count} holes -> NOT OK"
                self.last_decision_text = "; ".join(notes + [text])
                return 4, square, True
            notes.append(f"no 4-hole square among {count}")

        if expected_count == 2 and count >= 2:
            best = None
            rejected_corner = []
            for i, j in combinations(range(count), 2):
                (x1, y1), (x2, y2) = valid_components[i][0], valid_components[j][0]
                distance = float(np.hypot(x2 - x1, y2 - y1))
                if not 75 <= distance <= 100:
                    continue
                corner = self._third_square_corner(valid_components, i, j)
                if corner is not None:
                    rejected_corner.append((i, j, corner))
                    continue
                mid_off = float(np.hypot((x1 + x2) / 2.0 - reference_center[0], (y1 + y2) / 2.0 - reference_center[1]))
                if best is None or mid_off < best[0]:
                    best = (mid_off, i, j, distance)
            if best is not None:
                mid_off, i, j, distance = best
                text = (f"2-hole pair {distance:.0f} px apart (range {75}-"
                        f"{100}), midpoint {mid_off:.0f} px from the disc centre -> OK")
                self.last_decision_text = "; ".join(notes + [text])
                return 2, [valid_components[i], valid_components[j]], True
            if rejected_corner:
                notes.append(f"{len(rejected_corner)} pair(s) in range left out: a third candidate makes 3 corners "
                             f"of a square (DOST holes?)")
            else:
                notes.append(f"no pair {75}-{100} px apart "
                             f"among {count}")
        elif count < expected_count:
            notes.append(f"{count} candidate(s), fewer than {expected_count}")

        self.last_decision_text = "; ".join(notes + ["no pattern -> next gamma"])
        if PRINT_GEOMETRIC_VALIDATION:
            logBoth('logDebug', _src, f"[Cascade] {self.last_decision_text}", MessageType.GENERAL)
        if expected_count == 4 and count >= 4:
            return 0, [], False
        if expected_count == 2:
            return 0, [], False
        return count, valid_components, False

    @staticmethod
    def _square_sides(centers: List[Tuple[float, float]]) -> str:
        order = order_polygon_vertices_robust(centers, list(range(len(centers))))
        pts = [centers[k] for k in order]
        sides = [np.hypot(pts[k][0] - pts[(k + 1) % 4][0], pts[k][1] - pts[(k + 1) % 4][1]) for k in range(4)]
        return "/".join(f"{s:.0f}" for s in sides)

    @staticmethod
    def _third_square_corner(components: List[Tuple[Tuple[float, float], np.ndarray]], i: int, j: int) -> Optional[int]:
        """Index of a candidate that makes candidates i and j (as the diagonal) into 3 corners of a square: its
        distances to i and j within square.side.length.tolerance of their mean and the angle at it within
        square.min/max.interior.angle. None if there is none."""
        a = np.array(components[i][0], dtype=np.float64)
        b = np.array(components[j][0], dtype=np.float64)
        for k, (c, _) in enumerate(components):
            if k in (i, j):
                continue
            c = np.array(c, dtype=np.float64)
            da, db = np.linalg.norm(a - c), np.linalg.norm(b - c)
            if da == 0 or db == 0:
                continue
            if abs(da - db) / ((da + db) / 2.0) > 0.2:
                continue
            cos_angle = np.dot(a - c, b - c) / (da * db)
            angle = float(np.degrees(np.arccos(np.clip(cos_angle, -1.0, 1.0))))
            if 65.0 <= angle <= 115.0:
                return k
        return None

    def _merge_overlapping_masks(self, masks: List[Dict[str, Any]],
                                 overlap_threshold: float = 0.15) -> List[Dict[str, Any]]:
        """
        Merge masks that overlap significantly.

        Args:
            masks: List of mask dictionaries
            overlap_threshold: Overlap ratio threshold for merging

        Returns:
            List of merged masks
        """
        if len(masks) <= 1:
            return masks

        _src = getFullyQualifiedName(__file__, BungSegmenter)

        if PRINT_MASK_FILTERING:
            logBoth('logDebug', _src, f"\n{'=' * 70}", MessageType.GENERAL)
            logBoth('logDebug', _src, f"MASK MERGING (threshold={overlap_threshold})", MessageType.GENERAL)
            logBoth('logDebug', _src, f"{'=' * 70}", MessageType.GENERAL)
            logBoth('logDebug', _src, f"Input masks: {len(masks)}", MessageType.GENERAL)

            # Log input masks
            for i, mask_data in enumerate(masks):
                logBoth('logDebug', _src, f"  Mask {i}: area={mask_data['area']}", MessageType.GENERAL)

        n = len(masks)
        merged = [False] * n
        result_masks = []
        merge_log = []

        for i in range(n):
            if merged[i]:
                continue

            # Start with mask i
            current_mask = masks[i]['segmentation'].copy()
            masks_to_merge = [i]

            # Find all masks that overlap > threshold
            changed = True
            while changed:
                changed = False
                for j in range(n):
                    if merged[j] or j in masks_to_merge:
                        continue

                    mask_j = masks[j]['segmentation']

                    # Calculate overlap
                    intersection = (current_mask & mask_j).sum()
                    area_i = current_mask.sum()
                    area_j = mask_j.sum()

                    min_area = min(area_i, area_j)
                    if min_area > 0:
                        overlap_ratio = intersection / min_area

                        if overlap_ratio > overlap_threshold:
                            # Merge
                            current_mask = current_mask | mask_j
                            masks_to_merge.append(j)
                            changed = True
                            if PRINT_MASK_FILTERING:
                                logBoth('logDebug', _src, f"  Merging mask {j} into group {masks_to_merge[0]} (overlap={overlap_ratio:.1%})", MessageType.GENERAL)

            # Mark merged
            for idx in masks_to_merge:
                merged[idx] = True

            merged_area = int(current_mask.sum())

            if len(masks_to_merge) > 1:
                merge_log.append({
                    'merged_from': masks_to_merge,
                    'final_area': merged_area
                })
                if PRINT_MASK_FILTERING:
                    logBoth('logDebug', _src, f"  ✓ Created merged mask from {masks_to_merge}: area={merged_area}", MessageType.GENERAL)

            result_masks.append({
                'segmentation': current_mask,
                'area': merged_area,
                'bbox': self._mask_to_bbox(current_mask),
                'predicted_iou': masks[i].get('predicted_iou', 0.0),
                'stability_score': masks[i].get('stability_score', 0.0),
                'merged_from': masks_to_merge
            })

        if PRINT_MASK_FILTERING:
            logBoth('logDebug', _src, f"\nMerge summary:", MessageType.GENERAL)
            logBoth('logDebug', _src, f"  Input masks: {len(masks)}", MessageType.GENERAL)
            logBoth('logDebug', _src, f"  Output masks: {len(result_masks)}", MessageType.GENERAL)
            logBoth('logDebug', _src, f"  Merge operations: {len(merge_log)}", MessageType.GENERAL)
            logBoth('logDebug', _src, f"{'=' * 70}\n", MessageType.GENERAL)

        return result_masks

    def _mask_to_bbox(self, mask: np.ndarray) -> List[int]:
        """Convert binary mask to bounding box [x, y, w, h]."""
        rows = np.any(mask, axis=1)
        cols = np.any(mask, axis=0)
        if not rows.any() or not cols.any():
            return [0, 0, 0, 0]
        y1, y2 = np.where(rows)[0][[0, -1]]
        x1, x2 = np.where(cols)[0][[0, -1]]
        return [int(x1), int(y1), int(x2 - x1 + 1), int(y2 - y1 + 1)]

    def _keep_masks_in_inner_disc(self, masks: List[Dict[str, Any]], shape: Tuple[int, int]
                                  ) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
        """Size filter BEFORE any merging: each mask is cut to the inner disc and kept only if its part inside is
        min.mask.area..max.mask.area px. Returns (kept masks, counts of kept / too small / too large)."""
        h, w = shape[:2]
        inner = create_circular_mask((h, w), (OUTER_DISC_RADIUS, OUTER_DISC_RADIUS), INNER_DISC_RADIUS)
        kept, counts = [], {"kept": 0, "too_small": 0, "too_large": 0}
        for mask_data in masks:
            cut = mask_data['segmentation'] & inner
            area = int(cut.sum())
            if area < MIN_MASK_AREA:
                counts["too_small"] += 1
            elif area > MAX_MASK_AREA:
                counts["too_large"] += 1
            else:
                kept.append(dict(mask_data, segmentation=cut, area=area))
                counts["kept"] += 1
        return kept, counts

    def _one_mask_per_blob(self, masks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Masks overlapping by more than HOLE_BLOB_OVERLAP of the smaller one are one blob; of each blob the mask
        found most often (most other masks with IoU > HOLE_DUPLICATE_IOU; then the higher SAM score) is kept, the
        others are dropped - never united, so a hole is not joined with a highlight or a chamfer next to it."""
        n = len(masks)
        if n <= 1:
            return [dict(m, votes=1) for m in masks]
        lo, hi = max(0, OUTER_DISC_RADIUS - INNER_DISC_RADIUS), OUTER_DISC_RADIUS + INNER_DISC_RADIUS + 1
        flat = np.stack([m['segmentation'][lo:hi, lo:hi].reshape(-1) for m in masks]).astype(np.float32)
        inter = flat @ flat.T
        areas = np.diag(inter).copy()
        smaller = np.minimum(areas[:, None], areas[None, :])
        union = areas[:, None] + areas[None, :] - inter
        overlap = np.where(smaller > 0, inter / np.maximum(smaller, 1), 0.0)
        iou = np.where(union > 0, inter / np.maximum(union, 1), 0.0)
        votes = (iou > HOLE_DUPLICATE_IOU).sum(axis=1)          # includes the mask itself
        seen = [False] * n
        result = []
        for i in range(n):
            if seen[i]:
                continue
            group, stack = [], [i]
            seen[i] = True
            while stack:
                a = stack.pop()
                group.append(a)
                for b in np.nonzero(overlap[a] > HOLE_BLOB_OVERLAP)[0]:
                    if not seen[b]:
                        seen[b] = True
                        stack.append(int(b))
            best = max(group, key=lambda g: (votes[g], masks[g].get('predicted_iou', 0.0)))
            result.append(dict(masks[best], votes=int(votes[best]), blob_size=len(group)))
        return result

    def _holes_in_preprocessed(self, preprocessed: np.ndarray, gamma: float, expected_count: int, model_type: str
                               ) -> Tuple[int, List[Tuple[Tuple[float, float], np.ndarray]], bool]:
        """One gamma: masks (YOLO boxes + dense grid) -> size filter in the inner disc -> one mask per blob ->
        contours -> shape filter -> hole pattern. Logs one line (the candidates, what was dropped and why, the
        decision) and leaves it in self.last_gamma_text."""
        _src = getFullyQualifiedName(__file__, BungSegmenter)
        masks = self.mask_generator.generate(preprocessed)
        gen = dict(self.mask_generator.last_counts)
        masks, size_counts = self._keep_masks_in_inner_disc(masks, preprocessed.shape)
        blobs = self._one_mask_per_blob(masks)
        components, _ = self._extract_and_filter_masks_in_inner_disc(blobs, preprocessed)
        dropped = []
        components = self._morphological_filter_components(components, preprocessed.shape[:2], dropped)
        candidates = list(components)
        self.last_decision_text = ""
        count, components, definitive = self._apply_cascade_geometric_validation(components, expected_count, model_type)

        ox = OUTER_DISC_CENTER[0] - INNER_DISC_RADIUS          # inner-disc -> full picture coordinates
        oy = OUTER_DISC_CENTER[1] - INNER_DISC_RADIUS

        def where(center):
            return f"({center[0] + ox:.0f},{center[1] + oy:.0f})"

        cand_text = ", ".join(
            f"{where(c)} d{np.hypot(c[0] - INNER_DISC_RADIUS, c[1] - INNER_DISC_RADIUS):.0f} a{cv2.contourArea(k):.0f}"
            for c, k in candidates) or "none"
        drop_text = ("; shape filter dropped " + ", ".join(f"({x + ox:.0f},{y + oy:.0f}) {why}" for x, y, why in dropped)
                     if dropped else "")
        self.last_gamma_text = (
            f"[Bung] {model_type} gamma {gamma}: masks {gen['yolo']} YOLO + {gen['grid']} grid (of {gen['grid_points']} "
            f"points every {HOLE_GRID_STEP} px, {gen['grid_outside']} masks outside {MIN_MASK_AREA}-{MAX_MASK_AREA} px); "
            f"part in r{INNER_DISC_RADIUS} {MIN_MASK_AREA}-{MAX_MASK_AREA} px: "
            f"{size_counts['kept']} kept, {size_counts['too_small']} too small, {size_counts['too_large']} too large; "
            f"{len(blobs)} blob(s){drop_text}; candidates {len(candidates)}: {cand_text} (d = px from the disc centre "
            f"{tuple(int(v) for v in OUTER_DISC_CENTER)}, a = area); {self.last_decision_text}")
        logBoth('logInfo', _src, self.last_gamma_text, MessageType.GENERAL)
        return count, components, definitive

    def segment_holes_batch_from_preprocessed(
        self,
        preprocessed_images: List[np.ndarray],
        gamma_values: List[float],
        expected_count: int,
        model_type: str
    ) -> Tuple[int, List[Tuple[Tuple[float, float], np.ndarray]], float, bool]:
        """
        Batch process preprocessed images with early termination.

        EARLY TERMINATION CONDITIONS:
        1. Found valid 4-hole square AND expected==4
        2. Found valid 2-hole pattern AND expected==2
        3. Found definitive mismatch (4-hole square when expected==2)
        4. Found definitive rejection (no valid geometry when expected)

        Args:
            preprocessed_images: List of preprocessed outer disc images
            gamma_values: Corresponding gamma values
            expected_count: Expected number of holes (2 or 4)
            model_type: "DOST" or "DOSTPLUS"

        Returns:
            Tuple of (best_count, best_components, best_gamma, found_definitive)
        """
        if len(preprocessed_images) != len(gamma_values):
            raise ValueError(f"Mismatch: {len(preprocessed_images)} images vs {len(gamma_values)} gammas")

        _src = getFullyQualifiedName(__file__, BungSegmenter)

        if PRINT_BATCH_PROCESSING:
            logBoth('logDebug', _src, f"[BungSegmenter] Batch processing {len(preprocessed_images)} gamma values", MessageType.GENERAL)
            logBoth('logDebug', _src, f"[BungSegmenter] Expected: {expected_count}, Model: {model_type}", MessageType.GENERAL)

        best_count = 0
        best_components = []
        best_gamma = gamma_values[0]
        found_definitive = False

        # Single torch.inference_mode() for all gammas
        with torch.inference_mode():
            for idx, (preprocessed, gamma) in enumerate(zip(preprocessed_images, gamma_values)):
                if PRINT_BATCH_PROCESSING:
                    logBoth('logDebug', _src, f"\n[BungSegmenter] --- Gamma {idx+1}/{len(preprocessed_images)}: {gamma} ---", MessageType.GENERAL)

                count, components, definitive = self._holes_in_preprocessed(
                    preprocessed, gamma, expected_count, model_type)

                # CRITICAL FIX: Update best BEFORE early termination check
                # Priority: definitive > count
                if definitive:
                    # Definitive result always wins
                    best_count = count
                    best_components = components
                    best_gamma = gamma
                    found_definitive = definitive
                    if PRINT_BATCH_PROCESSING:
                        logBoth('logDebug', _src, f"[BungSegmenter] ★ DEFINITIVE result: count={count}, gamma={gamma}", MessageType.GENERAL)
                elif count > best_count:
                    # Better count without definitive result
                    best_count = count
                    best_components = components
                    best_gamma = gamma
                    found_definitive = definitive
                    if PRINT_BATCH_PROCESSING:
                        logBoth('logDebug', _src, f"[BungSegmenter] ★ New best: count={count}, gamma={gamma}", MessageType.GENERAL)

                # EARLY TERMINATION (now happens AFTER updating best)
                if definitive:
                    # Any definitive result terminates early
                    if PRINT_BATCH_PROCESSING:
                        if count == expected_count:
                            logBoth('logDebug', _src, f"[BungSegmenter] ✓ EARLY TERMINATION: Found expected definitive pattern!", MessageType.GENERAL)
                        else:
                            logBoth('logDebug', _src, f"[BungSegmenter] ✓ EARLY TERMINATION: Definitive rejection!", MessageType.GENERAL)
                        logBoth('logDebug', _src, f"[BungSegmenter] Skipping remaining {len(preprocessed_images) - idx - 1} gammas", MessageType.GENERAL)
                    break

        # Clear GPU cache
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            if PRINT_BATCH_PROCESSING:
                logBoth('logDebug', _src, "[BungSegmenter] GPU cache cleared", MessageType.GENERAL)

        if PRINT_BATCH_PROCESSING:
            logBoth('logDebug', _src, f"\n[BungSegmenter] === WINNER: gamma={best_gamma}, count={best_count}, definitive={found_definitive} ===", MessageType.GENERAL)

        return best_count, best_components, best_gamma, found_definitive

    def segment_holes_single(
            self,
            preprocessed_image: np.ndarray,
            gamma: float,
            expected_count: int,
            model_type: str
    ) -> Tuple[int, List[Tuple[Tuple[float, float], np.ndarray]], bool]:
        """
        Process a single preprocessed image (single gamma) with early termination capability.

        This method is used for lazy evaluation - process one gamma at a time
        and stop as soon as a definitive pattern is found.

        Args:
            preprocessed_image: Single preprocessed outer disc image
            gamma: Gamma value used for this image
            expected_count: Expected number of holes (2 or 4)
            model_type: "DOST" or "DOSTPLUS"

        Returns:
            Tuple of (count, components, found_definitive)
            - count: Number of holes detected
            - components: List of (center, contour) tuples
            - found_definitive: True if definitive pattern found (valid geometry)
        """
        _src = getFullyQualifiedName(__file__, BungSegmenter)

        if PRINT_BATCH_PROCESSING:
            logBoth('logDebug', _src, f"[BungSegmenter] Processing single gamma {gamma}", MessageType.GENERAL)

        with torch.inference_mode():
            count, components, definitive = self._holes_in_preprocessed(
                preprocessed_image, gamma, expected_count, model_type)

            if PRINT_BATCH_PROCESSING:
                logBoth('logDebug', _src, f"[BungSegmenter] Result: count={count}, definitive={definitive}", MessageType.GENERAL)

        # Clear GPU cache
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        return count, components, definitive

_seatingLocal = threading.local()
seatingSamModelProvider = None      # tests: returns a MobileSAM model; production: ModelManager's shared model


def _seating_predictor():
    predictor = getattr(_seatingLocal, "predictor", None)
    if predictor is None:
        from mobile_sam import SamPredictor
        model = seatingSamModelProvider() if seatingSamModelProvider is not None else \
            ModelManager.get_instance().get_sam_model()
        predictor = SamPredictor(model)
        _seatingLocal.predictor = predictor
    return predictor


def hole_pattern_centre(components: List[Tuple[Tuple[float, float], np.ndarray]]) -> Optional[Tuple[float, float]]:
    """Centre of the holes found, in full-image coordinates (components are in inner-disc coordinates)."""
    if not components:
        return None
    ox = OUTER_DISC_CENTER[0] - INNER_DISC_RADIUS
    oy = OUTER_DISC_CENTER[1] - INNER_DISC_RADIUS
    pts = np.array([c for c, _ in components], dtype=np.float64)
    return float(pts[:, 0].mean() + ox), float(pts[:, 1].mean() + oy)


def seated_position(image_rgb: np.ndarray, model_type: str) -> Dict[str, Any]:
    """The model's seated position in this picture: dict(found, point, hub, reason). Source "hubholes": the hub's hole
    circle round the bung in the same picture (FindHubHoles.findHoles) + seating.hub.offset.<model>; "fixed":
    seating.seated.centre.<model>."""
    dostplus = model_type == "DOSTPLUS"
    expectOneOf(f"vision.{'dostplus' if dostplus else 'dost'}.bung.seating.position.source", SEATING_POSITION_SOURCE,
                ("hubholes", "fixed"))
    if str(SEATING_POSITION_SOURCE).strip().lower() != "hubholes":
        point = SEATING_SEATED_CENTRE_DOSTPLUS if dostplus else SEATING_SEATED_CENTRE_DOST
        return dict(found=True, point=(float(point[0]), float(point[1])), hub=None, reason="fixed seated position")
    from camera.FindHubHoles import FindHubHoles
    holes = FindHubHoles.findHoles(cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR), "DOSTPLUS" if dostplus else "DOST")
    if not holes["found"]:
        return dict(found=False, point=None, hub=holes,
                    reason=f"the hub's {holes['count']} holes round the bung not found ({holes['reason']})")
    dx, dy = SEATING_HUB_OFFSET_DOSTPLUS if dostplus else SEATING_HUB_OFFSET_DOST
    point = (float(holes["cx"]) + float(dx), float(holes["cy"]) + float(dy))
    return dict(found=True, point=point, hub=holes,
                reason=(f"hub from {holes['count']} holes ({holes['cx']:.1f}, {holes['cy']:.1f}) r {holes['radius']:.1f} "
                        f"spread {holes['spread']:.1f} gap {holes['gap']:.0f} deg ({holes.get('method')}), {model_type} offset "
                        f"({float(dx):g}, {float(dy):g})"))


def check_bung_seating(image_rgb: np.ndarray, components: List[Tuple[Tuple[float, float], np.ndarray]],
                       model_type: str) -> Dict[str, Any]:
    """Seating of a bung whose hole pattern passed. Seated when all four hold:
      1. the hole-pattern centre is within seating.hole.centre.offset.max of the model's seated position;
      2. the top-face mask (MobileSAM, prompted at the hole-pattern centre; the smallest mask whose fitted ellipse has
         a radius of seating.face.radius.min-max) has roundness (minor / major axis) >= the model's minimum;
      3. the face mask is one clean oval: its area (holes filled) / the fitted ellipse's area within
         seating.ellipse.fit.area.ratio.min..max;
      4. at most seating.outside.reference.oval.max of the face mask lies outside the model's reference oval (centred
         on the seated position).
    The seated position is seated_position()'s (CHANGES 153: from the hub's holes in this picture, or fixed).
    Returns dict(seated, reason, hole_centre, offset, roundness, face_radius, fit_ratio, outside, face_contour (full
    image), seated_at, hub)."""
    result = dict(seated=False, reason="", hole_centre=None, offset=None, roundness=None, face_radius=None,
                  fit_ratio=None, outside=None, face_contour=None, seated_at=None, hub=None)
    centre = hole_pattern_centre(components)
    if centre is None:
        result["reason"] = "no holes"
        return result
    dostplus = model_type == "DOSTPLUS"
    place = seated_position(image_rgb, model_type)
    result.update(hole_centre=centre, hub=place["hub"])
    if not place["found"]:
        result["reason"] = f"seated position unknown: {place['reason']}"
        return result
    seated_at = place["point"]
    result["seated_at"] = seated_at
    roundness_min = SEATING_ROUNDNESS_MIN_DOSTPLUS if dostplus else SEATING_ROUNDNESS_MIN_DOST
    reference_oval = SEATING_REFERENCE_OVAL_DOSTPLUS if dostplus else SEATING_REFERENCE_OVAL_DOST
    offset = float(np.hypot(centre[0] - seated_at[0], centre[1] - seated_at[1]))
    result.update(hole_centre=centre, offset=offset)
    predictor = _seating_predictor()
    predictor.set_image(image_rgb)
    with torch.inference_mode():
        masks, _, _ = predictor.predict(point_coords=np.array([centre], dtype=np.float32), point_labels=np.array([1]),
                                        multimask_output=True)
    faces = []
    for mask in masks:
        contours, _ = cv2.findContours(mask.astype(np.uint8) * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if not contours:
            continue
        c = max(contours, key=cv2.contourArea)
        if len(c) < 5:
            continue
        ellipse = cv2.fitEllipse(c)
        (_, _), (a, b), _ = ellipse
        r = (a + b) / 4.0
        if SEATING_FACE_RADIUS_MIN <= r <= SEATING_FACE_RADIUS_MAX and max(a, b) > 0:
            faces.append((r, min(a, b) / max(a, b), c, ellipse))
    position_ok = offset <= SEATING_HOLE_CENTRE_OFFSET_MAX
    text = (f"holes centre ({centre[0]:.0f},{centre[1]:.0f}) {offset:.1f} px from the seated position "
            f"({seated_at[0]:.1f},{seated_at[1]:.1f}; {place['reason']}) {'<=' if position_ok else '>'} "
            f"{SEATING_HOLE_CENTRE_OFFSET_MAX}")
    if not faces:
        result["reason"] = text + f"; no top-face mask of radius {SEATING_FACE_RADIUS_MIN:.0f}-{SEATING_FACE_RADIUS_MAX:.0f}"
        return result
    r, roundness, contour, ellipse = min(faces, key=lambda f: f[0])     # the top face (smallest of the face-sized masks)
    h, w = image_rgb.shape[:2]
    face = np.zeros((h, w), np.uint8)
    cv2.drawContours(face, [contour], -1, 1, thickness=cv2.FILLED)          # holes filled
    face_area = int(face.sum())
    (_, _), (a, b), _ = ellipse
    fit_ratio = face_area / (np.pi * a * b / 4.0) if a > 0 and b > 0 else 0.0
    reference = np.zeros((h, w), np.uint8)
    cv2.ellipse(reference, ((float(seated_at[0]), float(seated_at[1])),
                            (float(reference_oval[0]), float(reference_oval[1])), float(reference_oval[2])),
                1, thickness=-1)
    outside = float((face & (1 - reference)).sum()) / face_area if face_area else 1.0
    round_ok = roundness >= roundness_min
    fit_ok = SEATING_FIT_AREA_RATIO_MIN <= fit_ratio <= SEATING_FIT_AREA_RATIO_MAX
    outside_ok = outside <= SEATING_OUTSIDE_REFERENCE_OVAL_MAX
    result.update(roundness=float(roundness), face_radius=float(r), fit_ratio=float(fit_ratio), outside=outside,
                  face_contour=contour, seated=bool(position_ok and round_ok and fit_ok and outside_ok),
                  reason=text + f"; face roundness {roundness:.3f} {'>=' if round_ok else '<'} {roundness_min} "
                                f"(face r {r:.1f}); face / fitted ellipse {fit_ratio:.3f} "
                                f"{'within' if fit_ok else 'outside'} {SEATING_FIT_AREA_RATIO_MIN}-"
                                f"{SEATING_FIT_AREA_RATIO_MAX}; {outside:.1%} of the face outside the reference oval "
                                f"{'<=' if outside_ok else '>'} {SEATING_OUTSIDE_REFERENCE_OVAL_MAX:.0%}")
    return result


def onHubCentre(model_type: str) -> bool:
    """True when this model's bung is checked on the hub centre (DOSTPLUS, vision.dostplus.bung.method=hubcentre)."""
    if model_type != "DOSTPLUS":
        return False
    expectOneOf("vision.dostplus.bung.method", DOSTPLUS_BUNG_METHOD, ("fixed", "hubcentre"))
    return str(DOSTPLUS_BUNG_METHOD).strip().lower() == "hubcentre"


def placeOnHubCentre(image_bgr: np.ndarray, model_type: str) -> Dict[str, Any]:
    """Hub centre first (call after useModel): the hub's holes in this picture; found -> the segmented disc = the
    hub-hole circle (OUTER_DISC_CENTER / OUTER_DISC_RADIUS, the grid with them) until the next useModel. Returns
    FindHubHoles' result (found, cx, cy, radius, reason, ...)."""
    global OUTER_DISC_CENTER, OUTER_DISC_RADIUS
    from camera.FindHubHoles import FindHubHoles
    hub = FindHubHoles.findHoles(image_bgr, model_type, None, check="bung")
    if hub.get("found"):
        OUTER_DISC_CENTER = (int(round(hub["cx"])), int(round(hub["cy"])))
        OUTER_DISC_RADIUS = int(round(hub["radius"]))
        _applyGrid()
    return hub


def hub_region_value(dx: float, dy: float, region=None) -> float:
    """Where the hole-pattern centre (dx, dy from H, picture y down) lies in the egg-shaped region round H:
    (dx / east_west)^2 + (dy / north for dy < 0, else south)^2; <= 1 inside."""
    east_west, north, south = (float(v) for v in (DOSTPLUS_HUBCENTRE_REGION if region is None else region))
    return (dx / east_west) ** 2 + (dy / (north if dy < 0 else south)) ** 2


def check_bung_seating_on_hub_centre(image_rgb: np.ndarray, components: List[Tuple[Tuple[float, float], np.ndarray]],
                                     hub: Dict[str, Any], model_type: str = "DOSTPLUS") -> Dict[str, Any]:
    """Seating of a DOSTPLUS bung checked on the hub centre H (method hubcentre; call before the next useModel).
    Measures: dx, dy, e of the hole-pattern centre (hub_region_value); the top-face mask (MobileSAM prompted at the
    hole-pattern centre, the smallest mask of face radius seating.face.radius.min - max): roundness, area / fitted
    ellipse (info), its share outside seating.reference.oval centred on H. Verdict: the two stages in the comment at
    DOSTPLUS_BUNG_METHOD. Returns check_bung_seating's dict (seated_at = H, offset = |hole centre - H|) + dx, dy, region,
    region_axes."""
    hx, hy = float(hub["cx"]), float(hub["cy"])
    result = dict(seated=False, reason="", hole_centre=None, offset=None, roundness=None, face_radius=None,
                  fit_ratio=None, outside=None, face_contour=None, seated_at=(hx, hy), hub=hub, dx=None, dy=None,
                  region=None, region_axes=tuple(float(v) for v in DOSTPLUS_HUBCENTRE_REGION))
    centre = hole_pattern_centre(components)
    if centre is None:
        result["reason"] = "no holes"
        return result
    dx, dy = float(centre[0] - hx), float(centre[1] - hy)
    e = hub_region_value(dx, dy)
    result.update(hole_centre=centre, offset=float(np.hypot(dx, dy)), dx=dx, dy=dy, region=e)
    text = (f"hub ({hx:.1f}, {hy:.1f}) r {float(hub['radius']):.1f} ({hub.get('method')}); holes centre "
            f"({centre[0]:.0f},{centre[1]:.0f}) dx {dx:+.1f} dy {dy:+.1f} px (y down = south), region e {e:.2f} "
            f"(east-west / north / south {'/'.join(f'{float(v):g}' for v in DOSTPLUS_HUBCENTRE_REGION)})")
    predictor = _seating_predictor()
    predictor.set_image(image_rgb)
    with torch.inference_mode():
        masks, _, _ = predictor.predict(point_coords=np.array([centre], dtype=np.float32), point_labels=np.array([1]),
                                        multimask_output=True)
    faces = []
    for mask in masks:
        contours, _ = cv2.findContours(mask.astype(np.uint8) * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if not contours:
            continue
        c = max(contours, key=cv2.contourArea)
        if len(c) < 5:
            continue
        ellipse = cv2.fitEllipse(c)
        (_, _), (a, b), _ = ellipse
        r = (a + b) / 4.0
        if SEATING_FACE_RADIUS_MIN_DOSTPLUS <= r <= SEATING_FACE_RADIUS_MAX_DOSTPLUS and max(a, b) > 0:
            faces.append((r, min(a, b) / max(a, b), c, ellipse))
    if not faces:
        result["reason"] = text + (f"; no top-face mask of radius {SEATING_FACE_RADIUS_MIN_DOSTPLUS:.0f}-"
                                   f"{SEATING_FACE_RADIUS_MAX_DOSTPLUS:.0f}")
        return result
    r, roundness, contour, ellipse = min(faces, key=lambda f: f[0])     # the top face (smallest of the face-sized masks)
    h, w = image_rgb.shape[:2]
    face = np.zeros((h, w), np.uint8)
    cv2.drawContours(face, [contour], -1, 1, thickness=cv2.FILLED)          # holes filled
    face_area = int(face.sum())
    (_, _), (a, b), _ = ellipse
    fit_ratio = face_area / (np.pi * a * b / 4.0) if a > 0 and b > 0 else 0.0
    oval = SEATING_REFERENCE_OVAL_DOSTPLUS
    reference = np.zeros((h, w), np.uint8)
    cv2.ellipse(reference, ((hx, hy), (float(oval[0]), float(oval[1])), float(oval[2])), 1, thickness=-1)
    outside = float((face & (1 - reference)).sum()) / face_area if face_area else 1.0
    result.update(roundness=float(roundness), face_radius=float(r), fit_ratio=float(fit_ratio), outside=outside,
                  face_contour=contour)
    text += (f"; face roundness {roundness:.3f} (face r {r:.1f}), face / fitted ellipse {fit_ratio:.3f}, "
             f"{outside:.1%} of the face outside the reference oval on H")
    notOk = [why for hit, why in (
        (e > DOSTPLUS_HUBCENTRE_REGION_NOTOK_ABOVE, f"e {e:.2f} > {DOSTPLUS_HUBCENTRE_REGION_NOTOK_ABOVE:g}"),
        (roundness < DOSTPLUS_HUBCENTRE_ROUNDNESS_NOTOK_BELOW,
         f"roundness {roundness:.3f} < {DOSTPLUS_HUBCENTRE_ROUNDNESS_NOTOK_BELOW:g}"),
        (outside > DOSTPLUS_HUBCENTRE_OUTSIDE_NOTOK_ABOVE,
         f"outside {outside:.1%} > {DOSTPLUS_HUBCENTRE_OUTSIDE_NOTOK_ABOVE:.0%}")) if hit]
    ok = [why for hit, why in (
        (e < DOSTPLUS_HUBCENTRE_REGION_OK_BELOW, f"e {e:.2f} < {DOSTPLUS_HUBCENTRE_REGION_OK_BELOW:g}"),
        (roundness > DOSTPLUS_HUBCENTRE_ROUNDNESS_OK_ABOVE,
         f"roundness {roundness:.3f} > {DOSTPLUS_HUBCENTRE_ROUNDNESS_OK_ABOVE:g}"),
        (outside < DOSTPLUS_HUBCENTRE_OUTSIDE_OK_BELOW,
         f"outside {outside:.1%} < {DOSTPLUS_HUBCENTRE_OUTSIDE_OK_BELOW:.0%}")) if hit]
    if notOk:
        result["reason"] = text + "; stage 1 (any -> NOT OK): " + ", ".join(notOk)
    elif ok:
        result.update(seated=True, reason=text + "; stage 2 (any -> OK): " + ", ".join(ok))
    else:
        result["reason"] = text + "; stage 3: no stage-1 and no stage-2 condition met -> NOT OK"
    return result


def paint_bung_masks_on_image(
        original_image: np.ndarray,
        components: List[Tuple[Tuple[float, float], np.ndarray]],
        color: Tuple[int, int, int] = (0, 255, 0)
) -> np.ndarray:
    """
    Paint bung component masks on original image.

    Args:
        original_image: Original full-resolution image (RGB)
        components: List of (center, contour) tuples in inner disc coordinates
        color: RGB color tuple (default: green)

    Returns:
        Image with masks painted
    """
    result = original_image.copy()

    if not components:
        return result

    # Calculate offset from inner disc coords to full image coords
    # Outer disc bbox in full image
    outer_x1 = OUTER_DISC_CENTER[0] - OUTER_DISC_RADIUS
    outer_y1 = OUTER_DISC_CENTER[1] - OUTER_DISC_RADIUS

    # Inner disc bbox in outer disc coords
    inner_x1 = OUTER_DISC_RADIUS - INNER_DISC_RADIUS
    inner_y1 = OUTER_DISC_RADIUS - INNER_DISC_RADIUS

    # Total offset
    offset_x = outer_x1 + inner_x1
    offset_y = outer_y1 + inner_y1

    # Create overlay
    overlay = np.zeros_like(result)

    for center, contour in components:
        if len(contour) > 0:
            # Translate contour to full image coordinates
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
            translated_contour = contour.copy()
            translated_contour[:, :, 0] += offset_x
            translated_contour[:, :, 1] += offset_y

            cv2.drawContours(result, [translated_contour], -1, color, thickness=2)

            # Draw center point
            cx, cy = int(center[0] + offset_x), int(center[1] + offset_y)
            cv2.circle(result, (cx, cy), 3, (255, 0, 0), -1)

    # No text on the picture (the count is logged)

    return result