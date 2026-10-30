'''
Batch Hexagon Nut Detection - SEQUENTIAL Version v1.0 (REVISED)
Optimized for RTX 3050 (4GB VRAM)

NEW IN THIS REVISION:
- ✅ Hough Lines-based hexagon detection (from SAMSegmentation_v2_FindNut.py)
- ✅ Center of mass calculation with 25-pixel disc exclusion
- ✅ Robust hexagon validation with side length and angle checks

SEQUENTIAL PROCESSING WITH IMMEDIATE EARLY STOP:
- ✅ Process ONE variation at a time (not 3 in parallel)
- ✅ Submit variation → Wait for result → If YES: DONE!
- ✅ Gamma order: 2.5, 3.0, 2.0 (most likely first)
- ✅ True early termination: ~0.5s when first variation finds hexagon
- ✅ Uses 3 threads but only 1 active at a time (sequential submission)

EXPECTED PERFORMANCE:
- First variation finds: ~0.5s
- Second variation finds: ~1.0s
- Third variation finds: ~1.5s
- Average: ~0.7s per image (vs 2-3s in parallel)
'''

import os
import sys
import glob
import time
import math
import warnings
import uuid
from typing import Optional, List, Dict, Any, Tuple
import threading
from queue import Queue, Empty
from datetime import datetime
from contextlib import contextmanager

# Project utilities
from BaseUtils import get_project_root

# Import ModelManager for shared model access
from camera.ModelManager import ModelManager

# ============================================================================
# DEBUG FLAGS - Enable/disable different types of debug output
# ============================================================================
DEBUG_PREPROCESSING: bool = False    # Annular extraction, bg_norm, gamma
DEBUG_MASK_GENERATION: bool = False  # YOLO boxes, SAM masks, area filtering
DEBUG_HEXAGON_DETECTION: bool = False # Hough lines, vertices, aspect ratio
DEBUG_CENTER_OF_MASS: bool = False   # COM calculation, disc exclusion, validation
DEBUG_WORKFLOW: bool = False         # Sequential submission, early termination
DEBUG_SAVE_IMAGES: bool = False      # Save annular/preprocessed images to C:/temp

# Legacy flag for backward compatibility (enables all debug)
PRINT_DETAIL: bool = False

# Auto-enable all if PRINT_DETAIL is True
if PRINT_DETAIL:
    DEBUG_PREPROCESSING = True
    DEBUG_MASK_GENERATION = True
    DEBUG_HEXAGON_DETECTION = True
    DEBUG_CENTER_OF_MASS = True
    DEBUG_WORKFLOW = True
    DEBUG_SAVE_IMAGES = True

ENABLE_VRAM_MONITORING: bool = True

# --- Add MobileSAM to path ---
_PROJECT_ROOT = get_project_root()
_MOBILESAM_DIR = os.path.join(_PROJECT_ROOT, "models", "MobileSAM")
if os.path.exists(_MOBILESAM_DIR) and _MOBILESAM_DIR not in sys.path:
    sys.path.append(_MOBILESAM_DIR)

warnings.filterwarnings("ignore", category=FutureWarning, module="timm")
warnings.filterwarnings("ignore", category=UserWarning, message="Overwriting .* in registry")

import numpy as np
import torch
import cv2
from sklearn.cluster import KMeans

# CRITICAL: Apply torch.load patch BEFORE any model loading
import torch.serialization

_original_torch_load = torch.serialization.load

def _patched_torch_load(f, map_location=None, pickle_module=None, *, weights_only=None, mmap=None, **kwargs):
    return _original_torch_load(f, map_location=map_location, pickle_module=pickle_module,
                               weights_only=False, mmap=mmap, **kwargs)

torch.load = _patched_torch_load
torch.serialization.load = _patched_torch_load

# Apply safe_globals for PyTorch 2.6+
if hasattr(torch.serialization, "add_safe_globals"):
    safe_classes = [
        torch.nn.modules.container.Sequential,
        torch.nn.modules.container.ModuleList,
        torch.nn.modules.container.ModuleDict,
    ]
    try:
        from ultralytics.nn.tasks import SegmentationModel, DetectionModel
        safe_classes.extend([SegmentationModel, DetectionModel])
    except:
        pass
    torch.serialization.add_safe_globals(safe_classes)

print("✓ torch.load patched to use weights_only=False")

from mobile_sam import sam_model_registry, SamPredictor
from ultralytics import YOLO

# CRITICAL: Global lock for YOLO (thread-unsafe operations)
YOLO_LOCK = threading.Lock()
print("✓ YOLO global lock created for thread safety")

from utils.ImageNormalisationWithMask import (
    extract_annular_region,
    pixBackgroundNorm_masked,
    pixGammaCorrection_masked,
    pixContrastNorm_masked
)

"""
Enhanced Hexagon Symmetry Scoring System
==========================================

Based on visual analysis of good vs bad hexagon detections, this module
provides a comprehensive symmetry score that combines multiple geometric metrics.

Analysis of Sample Images:
---------------------------
GOOD HEXAGONS (Images 1-4): Near perfect, regular hexagons
- Regular edge lengths (low variance)
- Vertices evenly spaced around center
- Angles close to 120° internally
- Good fit to ideal hexagon
- Center properly aligned

ACCEPTABLE HEXAGONS (Images 5-6): Slightly irregular but still acceptable
- Some edge length variation
- Minor vertex spacing irregularities
- Still maintains hexagonal structure

BAD HEXAGONS (Images 7-8): Poor fits
- Highly irregular edges
- Poor vertex distribution
- Significant deviation from ideal hexagon
- Asymmetric structure

The scoring system should distinguish these categories clearly.
"""

import numpy as np
import cv2
from typing import Tuple, Dict, List


def calculate_edge_regularity_score(vertices: np.ndarray) -> float:
    """
    Calculate how regular the edge lengths are.

    Perfect hexagon: all edges equal length -> score = 1.0
    Irregular: high variance in edge lengths -> score closer to 0.0

    Args:
        vertices: (N, 2) or (N, 1, 2) array of hexagon vertices

    Returns:
        Score from 0 to 1 (1 = perfect regularity)
    """
    if vertices.shape[-1] == 2 and len(vertices.shape) == 3:
        vertices = vertices.reshape(-1, 2)

    n = len(vertices)
    if n < 3:
        return 0.0

    # Calculate edge lengths
    edge_lengths = []
    for i in range(n):
        p1 = vertices[i]
        p2 = vertices[(i + 1) % n]
        length = np.linalg.norm(p2 - p1)
        edge_lengths.append(length)

    edge_lengths = np.array(edge_lengths)

    if len(edge_lengths) == 0 or np.mean(edge_lengths) == 0:
        return 0.0

    # Coefficient of variation (std/mean)
    cv = np.std(edge_lengths) / np.mean(edge_lengths)

    # Convert to score: low CV = high score
    # Use exponential decay: score = exp(-k * cv)
    # k=10 means CV=0.1 gives score~0.37, CV=0.05 gives score~0.60
    # score = np.exp(-10 * cv)
    score = 1 - cv

    return float(np.clip(score, 0, 1))


def calculate_angular_regularity_score(vertices: np.ndarray, center: Tuple[float, float]) -> float:
    """
    Calculate how evenly vertices are distributed angularly around the center.

    Perfect hexagon: vertices at 0°, 60°, 120°, 180°, 240°, 300° -> score = 1.0
    Irregular: uneven angular distribution -> score closer to 0.0

    Args:
        vertices: (N, 2) or (N, 1, 2) array of hexagon vertices
        center: (cx, cy) center point

    Returns:
        Score from 0 to 1 (1 = perfect angular spacing)
    """
    if vertices.shape[-1] == 2 and len(vertices.shape) == 3:
        vertices = vertices.reshape(-1, 2)

    n = len(vertices)
    if n < 3:
        return 0.0

    cx, cy = center

    # Calculate angles from center to each vertex
    angles = []
    for v in vertices:
        angle = np.arctan2(v[1] - cy, v[0] - cx)
        angles.append(angle)

    angles = np.array(angles)
    angles = np.sort(angles)  # Sort in ascending order

    # Calculate angular differences (should all be 360/n degrees for regular polygon)
    angular_diffs = []
    for i in range(n):
        diff = angles[(i + 1) % n] - angles[i]
        # Handle wraparound at 2π
        if diff < 0:
            diff += 2 * np.pi
        angular_diffs.append(diff)

    angular_diffs = np.array(angular_diffs)

    # Expected angular spacing for regular n-gon
    expected_spacing = 2 * np.pi / n

    # Calculate coefficient of variation of angular differences
    if np.mean(angular_diffs) == 0:
        return 0.0

    cv = np.std(angular_diffs) / expected_spacing

    # Convert to score with exponential decay
    # score = np.exp(-8 * cv)
    score = 1 - cv

    return float(np.clip(score, 0, 1))


def calculate_internal_angle_score(vertices: np.ndarray) -> float:
    """
    Calculate how close internal angles are to ideal hexagon (120°).

    Perfect hexagon: all internal angles = 120° -> score = 1.0
    Irregular: angles deviate from 120° -> score closer to 0.0

    Args:
        vertices: (N, 2) or (N, 1, 2) array of hexagon vertices

    Returns:
        Score from 0 to 1 (1 = all angles are 120°)
    """
    if vertices.shape[-1] == 2 and len(vertices.shape) == 3:
        vertices = vertices.reshape(-1, 2)

    n = len(vertices)
    if n < 3:
        return 0.0

    # Calculate internal angles
    angles = []
    for i in range(n):
        p1 = vertices[(i - 1) % n]
        p2 = vertices[i]
        p3 = vertices[(i + 1) % n]

        # Vectors from p2 to p1 and p2 to p3
        v1 = p1 - p2
        v2 = p3 - p2

        # Calculate angle
        cos_angle = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-10)
        cos_angle = np.clip(cos_angle, -1, 1)
        angle_rad = np.arccos(cos_angle)
        angle_deg = np.degrees(angle_rad)
        angles.append(angle_deg)

    angles = np.array(angles)

    # For hexagon, ideal internal angle is 120°
    ideal_angle = 120.0

    # Calculate mean absolute deviation from ideal
    # mad = np.mean(np.abs(angles - ideal_angle))
    cv = np.std(angles) / ideal_angle

    # Convert to score: smaller deviation = higher score
    # Use exponential decay: score = exp(-k * mad / ideal_angle)
    # score = np.exp(-0.1 * mad)  # k=0.1 means 10° deviation gives score~0.37
    score = 1 - cv

    return float(np.clip(score, 0, 1))


def calculate_radial_distance_score(vertices: np.ndarray, center: Tuple[float, float]) -> float:
    """
    Calculate how consistent the radial distances from center to vertices are.

    Perfect hexagon: all vertices equidistant from center -> score = 1.0
    Irregular: varying distances -> score closer to 0.0

    Args:
        vertices: (N, 2) or (N, 1, 2) array of hexagon vertices
        center: (cx, cy) center point

    Returns:
        Score from 0 to 1 (1 = all vertices same distance from center)
    """
    if vertices.shape[-1] == 2 and len(vertices.shape) == 3:
        vertices = vertices.reshape(-1, 2)

    n = len(vertices)
    if n < 3:
        return 0.0

    cx, cy = center

    # Calculate distances from center to each vertex
    distances = []
    for v in vertices:
        dist = np.linalg.norm(v - np.array([cx, cy]))
        distances.append(dist)

    distances = np.array(distances)

    if np.mean(distances) == 0:
        return 0.0

    # Coefficient of variation
    cv = np.std(distances) / np.mean(distances)

    # Convert to score
    # score = np.exp(-10 * cv)
    score = 1 - cv

    return float(np.clip(score, 0, 1))


def calculate_center_alignment_score(vertices: np.ndarray,
                                     center: Tuple[float, float],
                                     reference_center: Tuple[float, float] = (72, 64)) -> float:
    """
    Calculate how well the hexagon center aligns with the reference center.

    Perfect alignment: center at (72, 64) -> score = 1.0
    Far from center: -> score closer to 0.0

    Args:
        vertices: (N, 2) or (N, 1, 2) array of hexagon vertices
        center: (cx, cy) computed center of mass
        reference_center: (rx, ry) reference center point

    Returns:
        Score from 0 to 1 (1 = perfect alignment)
    """
    cx, cy = center
    rx, ry = reference_center

    # Distance from computed center to reference center
    distance = np.sqrt((cx - rx) ** 2 + (cy - ry) ** 2)

    # Convert to score: use exponential decay
    # distance = 0 -> score = 1.0
    # distance = 10 -> score ~ 0.37
    # distance = 20 -> score ~ 0.14
    score = np.exp(-distance / 10.0)

    return float(np.clip(score, 0, 1))


def calculate_thickness_variation_at_vertices(
        vertices: np.ndarray,
        center: Tuple[float, float],
        contour: np.ndarray,
        r_inner: float = 25.0
) -> float:
    """
    Calculate thickness variation along rays from center to vertices.

    Measures how consistent the nut thickness is when measured from the
    inner disc (r_inner) to the outer boundary along each vertex direction.

    Perfect hexagon: uniform thickness at all vertices -> score = 1.0
    Irregular: varying thickness -> score closer to 0.0

    Args:
        vertices: (N, 2) or (N, 1, 2) array of hexagon vertices
        center: (cx, cy) center point
        contour: Contour points of the nut boundary
        r_inner: Inner radius to exclude (disc_radius, typically 25)

    Returns:
        Score from 0 to 1 (1 = uniform thickness at all vertices)
    """
    if vertices.shape[-1] == 2 and len(vertices.shape) == 3:
        vertices = vertices.reshape(-1, 2)

    n = len(vertices)
    if n < 3:
        return 0.0

    cx, cy = center

    # Normalize contour shape
    if contour.ndim == 3 and contour.shape[1] == 1:
        contour = contour[:, 0, :]  # Convert (N, 1, 2) to (N, 2)

    thicknesses = []

    for vertex in vertices:
        # Calculate angle from center to vertex
        dx = vertex[0] - cx
        dy = vertex[1] - cy
        angle = np.arctan2(dy, dx)

        # Calculate outer radius (from center to boundary along this angle)
        r_outer = compute_radius_at_angle(contour, cx, cy, angle)

        if r_outer < r_inner:
            continue  # Skip if outer is inside inner

        # Thickness = outer_radius - inner_radius
        thickness = r_outer - r_inner
        thicknesses.append(thickness)

    if len(thicknesses) < 2:
        return 0.0

    thicknesses = np.array(thicknesses)

    if np.mean(thicknesses) == 0:
        return 0.0

    # Calculate coefficient of variation
    stddev = np.std(thicknesses)
    mean = np.mean(thicknesses)
    cv = stddev / mean

    # Convert to score: cv = 0 -> score = 1.0
    score = 1 - cv

    return float(np.clip(score, 0, 1))


def calculate_thickness_variation_at_perpendiculars(
        vertices: np.ndarray,
        center: Tuple[float, float],
        contour: np.ndarray,
        r_inner: float = 25.0
) -> float:
    """
    Calculate thickness variation along perpendiculars from center to edges.

    For each edge, finds the perpendicular from center to edge, then measures
    thickness from inner disc to outer boundary along that perpendicular direction.

    Perfect hexagon: uniform thickness at all perpendiculars -> score = 1.0
    Irregular: varying thickness -> score closer to 0.0

    Args:
        vertices: (N, 2) or (N, 1, 2) array of hexagon vertices
        center: (cx, cy) center point
        contour: Contour points of the nut boundary
        r_inner: Inner radius to exclude (disc_radius, typically 25)

    Returns:
        Score from 0 to 1 (1 = uniform thickness at all perpendiculars)
    """
    if vertices.shape[-1] == 2 and len(vertices.shape) == 3:
        vertices = vertices.reshape(-1, 2)

    n = len(vertices)
    if n < 3:
        return 0.0

    cx, cy = center

    # Normalize contour shape
    if contour.ndim == 3 and contour.shape[1] == 1:
        contour = contour[:, 0, :]  # Convert (N, 1, 2) to (N, 2)

    thicknesses = []

    for i in range(n):
        # Get edge endpoints
        p1 = vertices[i]
        p2 = vertices[(i + 1) % n]

        # Edge vector
        edge_vec = p2 - p1
        edge_length = np.linalg.norm(edge_vec)

        if edge_length < 1e-6:
            continue  # Skip degenerate edges

        # Normalize edge vector
        edge_unit = edge_vec / edge_length

        # Vector from p1 to center
        center_vec = np.array([cx, cy]) - p1

        # Project center_vec onto edge
        t = np.dot(center_vec, edge_unit)
        t = np.clip(t, 0, edge_length)

        # Closest point on edge
        closest_point = p1 + t * edge_unit

        # Perpendicular vector from edge to center
        perp_vec = np.array([cx, cy]) - closest_point
        perp_length = np.linalg.norm(perp_vec)

        if perp_length < 1e-6:
            continue  # Skip if center is on the edge

        # Normalize perpendicular vector (points from edge toward center)
        perp_unit = perp_vec / perp_length

        # We want to measure from center outward, so negate the direction
        outward_direction = -perp_unit

        # Calculate angle of outward perpendicular
        angle = np.arctan2(outward_direction[1], outward_direction[0])

        # Calculate outer radius along this perpendicular direction
        r_outer = compute_radius_at_angle(contour, cx, cy, angle)

        if r_outer < r_inner:
            continue  # Skip if outer is inside inner

        # Thickness = outer_radius - inner_radius
        thickness = r_outer - r_inner
        thicknesses.append(thickness)

    if len(thicknesses) < 2:
        return 0.0

    thicknesses = np.array(thicknesses)

    if np.mean(thicknesses) == 0:
        return 0.0

    # Calculate coefficient of variation
    stddev = np.std(thicknesses)
    mean = np.mean(thicknesses)
    cv = stddev / mean

    # Convert to score: cv = 0 -> score = 1.0
    score = 1 - cv

    return float(np.clip(score, 0, 1))

def calculate_composite_symmetry_score(
        vertices: np.ndarray,
        center: Tuple[float, float],
        thickness_symmetry_score: float = None,
        reference_center: Tuple[float, float] = (72, 64),
        weights: Dict[str, float] = None,
        debug: bool = False
) -> Tuple[float, Dict[str, float]]:
    """
    Calculate comprehensive symmetry score combining multiple metrics.

    Args:
        vertices: (N, 2) or (N, 1, 2) array of hexagon vertices
        center: (cx, cy) center of mass
        thickness_symmetry_score: Optional pre-computed thickness symmetry (0-1)
        reference_center: Reference center point for alignment check
        weights: Optional dict of weights for each metric
        debug: Print detailed breakdown

    Returns:
        composite_score: Overall symmetry score (0-1)
        component_scores: Dict of individual metric scores
    """
    # Default weights (can be tuned based on importance)
    if weights is None:
        weights = {
            'edge_regularity': 0.25,
            'angular_regularity': 0.25,
            'internal_angles': 0.15,
            'radial_distance': 0.15,
            'center_alignment': 0.10,
            'thickness_symmetry': 0.10
        }

    # Calculate individual scores
    component_scores = {
        'edge_regularity': calculate_edge_regularity_score(vertices),
        'angular_regularity': calculate_angular_regularity_score(vertices, center),
        'internal_angles': calculate_internal_angle_score(vertices),
        'radial_distance': calculate_radial_distance_score(vertices, center),
        'center_alignment': calculate_center_alignment_score(vertices, center, reference_center)
    }

    # Add thickness symmetry if provided
    if thickness_symmetry_score is not None:
        component_scores['thickness_symmetry'] = thickness_symmetry_score
    else:
        # Redistribute thickness weight to other metrics
        total_other = sum(v for k, v in weights.items() if k != 'thickness_symmetry')
        weights = {k: v / total_other for k, v in weights.items() if k != 'thickness_symmetry'}

    # Calculate weighted composite score
    composite_score = sum(
        component_scores.get(metric, 0) * weight
        for metric, weight in weights.items()
        if metric in component_scores
    )

    if debug:
        print(f"\n{'=' * 60}")
        print(f"SYMMETRY SCORE BREAKDOWN:")
        print(f"{'=' * 60}")
        for metric, score in component_scores.items():
            weight = weights.get(metric, 0)
            contribution = score * weight
            print(f"  {metric:20s}: {score:.3f} (weight: {weight:.2f}, contrib: {contribution:.3f})")
        print(f"{'-' * 60}")
        print(f"  {'COMPOSITE SCORE':20s}: {composite_score:.3f}")
        print(f"{'=' * 60}\n")

    return composite_score, component_scores


def calculate_perpendicular_distance_score(vertices: np.ndarray, center: Tuple[float, float]) -> float:
    """
    Calculate how consistent the perpendicular distances from center to edges are.

    Perfect hexagon: all perpendicular distances equal -> score = 1.0
    Irregular: varying perpendicular distances -> score closer to 0.0

    Args:
        vertices: (N, 2) or (N, 1, 2) array of hexagon vertices
        center: (cx, cy) center point

    Returns:
        Score from 0 to 1 (1 = all perpendicular distances equal)
    """
    if vertices.shape[-1] == 2 and len(vertices.shape) == 3:
        vertices = vertices.reshape(-1, 2)

    n = len(vertices)
    if n < 3:
        return 0.0

    cx, cy = center

    # Calculate perpendicular distance from center to each edge
    perpendicular_distances = []

    for i in range(n):
        # Get edge endpoints
        p1 = vertices[i]
        p2 = vertices[(i + 1) % n]

        # Edge vector
        edge_vec = p2 - p1
        edge_length = np.linalg.norm(edge_vec)

        if edge_length < 1e-6:
            continue  # Skip degenerate edges

        # Normalize edge vector
        edge_unit = edge_vec / edge_length

        # Vector from p1 to center
        center_vec = np.array([cx, cy]) - p1

        # Project center_vec onto edge to find closest point on edge
        t = np.dot(center_vec, edge_unit)
        t = np.clip(t, 0, edge_length)  # Clamp to edge bounds

        # Closest point on edge
        closest_point = p1 + t * edge_unit

        # Perpendicular distance from center to edge
        perp_dist = np.linalg.norm(np.array([cx, cy]) - closest_point)
        perpendicular_distances.append(perp_dist)

    if len(perpendicular_distances) == 0:
        return 0.0

    perpendicular_distances = np.array(perpendicular_distances)

    if np.mean(perpendicular_distances) == 0:
        return 0.0

    # Coefficient of variation
    cv = np.std(perpendicular_distances) / np.mean(perpendicular_distances)

    # Convert to score
    score = 1 - cv

    return float(np.clip(score, 0, 1))


def calculate_angles_between_perpendiculars_score(vertices: np.ndarray, center: Tuple[float, float]) -> float:
    """
    Calculate how consistent the angles between adjacent perpendiculars are.

    Perfect hexagon: all angles equal (60° for hexagon) -> score = 1.0
    Irregular: varying angles -> score closer to 0.0

    Args:
        vertices: (N, 2) or (N, 1, 2) array of hexagon vertices
        center: (cx, cy) center point

    Returns:
        Score from 0 to 1 (1 = all angles between perpendiculars equal)
    """
    if vertices.shape[-1] == 2 and len(vertices.shape) == 3:
        vertices = vertices.reshape(-1, 2)

    n = len(vertices)
    if n < 3:
        return 0.0

    cx, cy = center

    # Calculate perpendicular vectors from center to each edge
    perpendicular_vectors = []

    for i in range(n):
        # Get edge endpoints
        p1 = vertices[i]
        p2 = vertices[(i + 1) % n]

        # Edge vector
        edge_vec = p2 - p1
        edge_length = np.linalg.norm(edge_vec)

        if edge_length < 1e-6:
            continue  # Skip degenerate edges

        # Normalize edge vector
        edge_unit = edge_vec / edge_length

        # Vector from p1 to center
        center_vec = np.array([cx, cy]) - p1

        # Project center_vec onto edge
        t = np.dot(center_vec, edge_unit)
        t = np.clip(t, 0, edge_length)

        # Closest point on edge
        closest_point = p1 + t * edge_unit

        # Perpendicular vector from edge to center
        perp_vec = np.array([cx, cy]) - closest_point
        perp_length = np.linalg.norm(perp_vec)

        if perp_length < 1e-6:
            continue  # Skip if center is on the edge

        # Normalize perpendicular vector
        perp_unit = perp_vec / perp_length
        perpendicular_vectors.append(perp_unit)

    if len(perpendicular_vectors) < 2:
        return 0.0

    # Calculate angles between consecutive perpendicular vectors
    angles = []

    for i in range(len(perpendicular_vectors)):
        vec1 = perpendicular_vectors[i]
        vec2 = perpendicular_vectors[(i + 1) % len(perpendicular_vectors)]

        # Calculate angle between vectors using dot product
        dot_product = np.dot(vec1, vec2)
        dot_product = np.clip(dot_product, -1.0, 1.0)  # Handle numerical errors
        angle_rad = np.arccos(dot_product)
        angle_deg = np.degrees(angle_rad)

        angles.append(angle_deg)

    if len(angles) == 0:
        return 0.0

    angles = np.array(angles)

    if np.mean(angles) == 0:
        return 0.0

    # Coefficient of variation
    cv = np.std(angles) / np.mean(angles)

    # Convert to score
    score = 1 - cv

    return float(np.clip(score, 0, 1))

def interpret_symmetry_score(score: float) -> str:
    """Provide human-readable interpretation of symmetry score."""
    if score >= 0.85:
        return "EXCELLENT"
    elif score >= 0.70:
        return "GOOD"
    elif score >= 0.55:
        return "ACCEPTABLE"
    elif score >= 0.40:
        return "MARGINAL"
    else:
        return "POOR"

# ============================================================================
# RECOMMENDED THRESHOLDS BASED ON IMAGE ANALYSIS
# ============================================================================

SYMMETRY_THRESHOLDS = {
    'reject': 0.55,      # Below this: reject (corresponds to Images 7-8)
    'warning': 0.70,     # Below this: warning (corresponds to Images 5-6)
    'good': 0.85,        # Above this: excellent (corresponds to Images 1-4)
}

# ============================================================================
# HUB AND BOTTOM BEARING COLOR ANALYSIS
# ============================================================================

class HubBottomBearingAnalyzer:
    """
    Analyzer for comparing nut detections against hub and bottom bearing reference images.
    Integrated version for use within IdentifyNutsInBatch.
    """

    def __init__(self):
        self.reference_filenames = []
        self.cropped_references = []
        self.comparison_results = []
        self.ready = False

    def load_and_crop_references(self, input_dir, center_x, center_y, outer_radius, inner_radius=0):
        """
        Load all "hub and bottom bearing" images from directory and crop them.

        Args:
            input_dir: Directory containing reference images
            center_x, center_y: Center coordinates for cropping
            outer_radius, inner_radius: Radii for annular region

        Returns:
            Number of reference images loaded and cropped
        """
        import glob

        self.reference_filenames = []
        self.cropped_references = []

        # Find all image files
        extensions = ['*.jpg', '*.jpeg', '*.png', '*.bmp']
        all_files = []
        for ext in extensions:
            all_files.extend(glob.glob(os.path.join(input_dir, ext)))

        # Filter for "hub and bottom bearing" images
        for filepath in sorted(all_files):
            filename = os.path.basename(filepath)
            if "hub and bottom bearing" in filename.lower():
                # Load image
                img_bgr = cv2.imread(filepath)
                if img_bgr is None:
                    continue

                # Convert to RGB
                img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)

                # Crop to annular region
                center = (center_x, center_y)
                cropped, _ = extract_annular_region(img_rgb, center=center,
                                                   outer_radius=outer_radius,
                                                   inner_radius=inner_radius)

                self.reference_filenames.append(filename)
                self.cropped_references.append(cropped)

        if len(self.cropped_references) > 0:
            self.ready = True

        return len(self.cropped_references)

    def compare_nut_to_references(self, nut_filename, nut_mask, nut_annular_rgb):
        """
        Compare detected nut against all reference images.

        Args:
            nut_filename: Name of nut image file
            nut_mask: Binary mask of actual_nut_mask (uint8, 0 or 255)
            nut_annular_rgb: RGB annular region image

        Returns:
            List of (diff_r, diff_g, diff_b) tuples, one per reference image
        """
        if not self.ready or not self.cropped_references:
            return []

        # Calculate nut's RGB averages
        mask_bool = (nut_mask > 0)
        num_nut_pixels = np.sum(mask_bool)

        if num_nut_pixels == 0:
            return []

        nut_masked_pixels = nut_annular_rgb[mask_bool]
        nut_r_avg = float(np.mean(nut_masked_pixels[:, 0]))
        nut_g_avg = float(np.mean(nut_masked_pixels[:, 1]))
        nut_b_avg = float(np.mean(nut_masked_pixels[:, 2]))

        # Calculate differences for each reference
        differences = []

        for ref_img in self.cropped_references:
            # Check dimensions match
            if mask_bool.shape != ref_img.shape[:2]:
                differences.append((np.nan, np.nan, np.nan))
                continue

            # Calculate reference RGB averages within same mask
            ref_masked_pixels = ref_img[mask_bool]

            if len(ref_masked_pixels) == 0:
                differences.append((np.nan, np.nan, np.nan))
                continue

            ref_r_avg = float(np.mean(ref_masked_pixels[:, 0]))
            ref_g_avg = float(np.mean(ref_masked_pixels[:, 1]))
            ref_b_avg = float(np.mean(ref_masked_pixels[:, 2]))

            # Calculate differences: Nut - Reference
            diff_r = nut_r_avg - ref_r_avg
            diff_g = nut_g_avg - ref_g_avg
            diff_b = nut_b_avg - ref_b_avg

            differences.append((diff_r, diff_g, diff_b))

        # Store complete result
        result = {
            'nut_filename': nut_filename,
            'nut_r_avg': nut_r_avg,
            'nut_g_avg': nut_g_avg,
            'nut_b_avg': nut_b_avg,
            'nut_mask_pixels': num_nut_pixels,
            'differences': differences
        }

        self.comparison_results.append(result)

        return differences

    def save_results_to_csv(self, output_filepath):
        """
        Save all comparison results to CSV.

        CSV Format:
        - Row 1: Headers
        - Each row: nut_filename, nut_r_avg, nut_g_avg, nut_b_avg, nut_mask_pixels,
                    ref0_filename, ref0_diff_r, ref0_diff_g, ref0_diff_b, ...
        """
        if not self.comparison_results:
            print("No comparison results to save")
            return

        import csv

        # Build headers
        headers = ['nut_filename', 'nut_r_avg', 'nut_g_avg', 'nut_b_avg', 'nut_mask_pixels']

        for i, ref_name in enumerate(self.reference_filenames):
            headers.extend([
                f'ref{i}_filename',
                f'ref{i}_diff_r',
                f'ref{i}_diff_g',
                f'ref{i}_diff_b'
            ])

        # Write CSV
        with open(output_filepath, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(headers)

            for result in self.comparison_results:
                row = [
                    result['nut_filename'],
                    f"{result['nut_r_avg']:.2f}",
                    f"{result['nut_g_avg']:.2f}",
                    f"{result['nut_b_avg']:.2f}",
                    result['nut_mask_pixels']
                ]

                for i, (diff_r, diff_g, diff_b) in enumerate(result['differences']):
                    row.extend([
                        self.reference_filenames[i],
                        f"{diff_r:.2f}" if not np.isnan(diff_r) else "NaN",
                        f"{diff_g:.2f}" if not np.isnan(diff_g) else "NaN",
                        f"{diff_b:.2f}" if not np.isnan(diff_b) else "NaN"
                    ])

                writer.writerow(row)

        print(f"{Colors.GREEN}✓ Saved {len(self.comparison_results)} color analysis results to {output_filepath}{Colors.RESET}")


# ANSI colors
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    MAGENTA = '\033[95m'
    RESET = '\033[0m'
    BOLD = '\033[1m'


def print_vram_usage(prefix=""):
    """Print current VRAM usage."""
    if ENABLE_VRAM_MONITORING and torch.cuda.is_available():
        allocated = torch.cuda.memory_allocated() / 1024**3
        reserved = torch.cuda.memory_reserved() / 1024**3
        print(f"{prefix}VRAM: {allocated:.2f}GB allocated, {reserved:.2f}GB reserved")


@contextmanager
def cuda_memory_tracking(label=""):
    """Context manager to track CUDA memory changes."""
    if torch.cuda.is_available():
        torch.cuda.synchronize()
        start_mem = torch.cuda.memory_allocated()
        yield
        torch.cuda.synchronize()
        end_mem = torch.cuda.memory_allocated()
        delta = (end_mem - start_mem) / 1024**3
        if abs(delta) > 0.01:  # Only print if >10MB change
            print(f"{label} VRAM delta: {delta:+.3f}GB")
    else:
        yield


# ============================================================================
# HOUGH LINES-BASED HEXAGON DETECTION (from SAMSegmentation_v2_FindNut.py)
# ============================================================================

def normalize_angle(angle):
    """Normalize angle to [0, 180) degrees."""
    while angle < 0:
        angle += 180
    while angle >= 180:
        angle -= 180
    return angle


def line_angle(x1, y1, x2, y2):
    """Calculate angle of line in degrees [0, 180)."""
    angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
    return normalize_angle(angle)


def line_intersection(line1, line2):
    """
    Find intersection point of two lines in format (x1, y1, x2, y2).
    Returns (x, y) or None if parallel.
    """
    x1, y1, x2, y2 = line1
    x3, y3, x4, y4 = line2

    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denom) < 1e-10:
        return None

    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom

    x = x1 + t * (x2 - x1)
    y = y1 + t * (y2 - y1)

    return (x, y)


def extend_line(x1, y1, x2, y2, length=1000):
    """Extend line segment to a longer line for intersection calculation."""
    dx = x2 - x1
    dy = y2 - y1
    norm = math.sqrt(dx ** 2 + dy ** 2)
    if norm < 1e-10:
        return (x1, y1, x2, y2)

    dx /= norm
    dy /= norm

    new_x1 = x1 - dx * length
    new_y1 = y1 - dy * length
    new_x2 = x2 + dx * length
    new_y2 = y2 + dy * length

    return (new_x1, new_y1, new_x2, new_y2)


def cluster_lines_by_angle(lines, n_clusters=3):
    """
    Cluster lines into groups by orientation.
    For hexagons, expect 3 groups with ~60° separation.

    Returns: List of line groups, each group is list of (x1, y1, x2, y2, angle)
    """
    if len(lines) < n_clusters:
        return None

    # Extract angles
    angles = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        angle = line_angle(x1, y1, x2, y2)
        angles.append(angle)

    # Normalize angles for clustering (handle wraparound at 180°)
    # Convert to unit vectors on circle
    angles_rad = np.deg2rad(angles)
    X = np.column_stack([np.cos(2 * angles_rad), np.sin(2 * angles_rad)])

    # K-means clustering
    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = kmeans.fit_predict(X)

    # Group lines by cluster
    clustered_lines = [[] for _ in range(n_clusters)]
    for i, (line, angle) in enumerate(zip(lines, angles)):
        x1, y1, x2, y2 = line[0]
        clustered_lines[labels[i]].append((x1, y1, x2, y2, angle))

    # Filter out clusters with too few lines
    clustered_lines = [group for group in clustered_lines if len(group) >= 2]

    return clustered_lines if len(clustered_lines) >= 3 else None


def average_lines(lines):
    """Average multiple line segments into one representative line."""
    if not lines:
        return None

    # Average endpoints
    x1_avg = np.mean([line[0] for line in lines])
    y1_avg = np.mean([line[1] for line in lines])
    x2_avg = np.mean([line[2] for line in lines])
    y2_avg = np.mean([line[3] for line in lines])

    return (x1_avg, y1_avg, x2_avg, y2_avg)


def find_parallel_lines(line_group, max_pairs=2):
    """
    Find the 2 strongest parallel lines in a group.
    Uses line length and position averaging.

    Returns: Two lines (x1, y1, x2, y2) or None
    """
    if len(line_group) < 2:
        return None

    # Sort by length (longest lines are usually more reliable)
    lengths = []
    for x1, y1, x2, y2, angle in line_group:
        length = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
        lengths.append(length)

    sorted_indices = np.argsort(lengths)[::-1]

    # Take top lines and cluster by position perpendicular to line direction
    # For simplicity, use midpoint projection onto perpendicular axis

    if len(line_group) >= 4:
        # Use top 4-6 lines, cluster into 2 groups by perpendicular position
        top_n = min(6, len(line_group))
        top_indices = sorted_indices[:top_n]

        # Calculate perpendicular distance from origin for each line
        perp_distances = []
        for idx in top_indices:
            x1, y1, x2, y2, angle = line_group[idx]
            # Midpoint
            mx = (x1 + x2) / 2
            my = (y1 + y2) / 2
            # Perpendicular angle
            perp_angle = angle + 90
            perp_rad = math.radians(perp_angle)
            # Distance from origin projected onto perpendicular
            dist = mx * math.cos(perp_rad) + my * math.sin(perp_rad)
            perp_distances.append(dist)

        # K-means with k=2 to find two parallel line groups
        if len(perp_distances) >= 2:
            X = np.array(perp_distances).reshape(-1, 1)
            kmeans = KMeans(n_clusters=2, random_state=42, n_init=10)
            labels = kmeans.fit_predict(X)

            # Average lines in each group
            group1 = [line_group[top_indices[i]] for i in range(len(top_indices)) if labels[i] == 0]
            group2 = [line_group[top_indices[i]] for i in range(len(top_indices)) if labels[i] == 1]

            line1 = average_lines(group1) if group1 else None
            line2 = average_lines(group2) if group2 else None

            if line1 and line2:
                return (line1, line2)

    # Fallback: use two longest lines
    if len(line_group) >= 2:
        idx1 = sorted_indices[0]
        idx2 = sorted_indices[1]
        x1, y1, x2, y2, _ = line_group[idx1]
        x3, y3, x4, y4, _ = line_group[idx2]
        return ((x1, y1, x2, y2), (x3, y3, x4, y4))

    return None


def sort_vertices_by_angle(vertices):
    """Sort vertices by angle around centroid (counterclockwise)."""
    centroid = np.mean(vertices, axis=0)

    def angle_from_centroid(vertex):
        return math.atan2(vertex[1] - centroid[1], vertex[0] - centroid[0])

    sorted_vertices = sorted(vertices, key=angle_from_centroid)
    return sorted_vertices


def validate_hexagon(vertices, debug=False):
    """
    Validate that vertices form a reasonable hexagon.

    Checks:
    1. Side length variance < 30% (relaxed for worn nuts)
    2. Interior angles approximately 120° (±30°)
    3. Vertices not too close together

    Returns: True if valid hexagon
    """
    if len(vertices) != 6:
        return False

    # Calculate side lengths
    side_lengths = []
    for i in range(6):
        v1 = np.array(vertices[i])
        v2 = np.array(vertices[(i + 1) % 6])
        length = np.linalg.norm(v2 - v1)
        side_lengths.append(length)

    # Check side length variance
    mean_length = np.mean(side_lengths)
    std_length = np.std(side_lengths)
    cv = std_length / mean_length if mean_length > 0 else float('inf')

    if cv > 0.3:  # Coefficient of variation > 30%
        if debug:
            print(f"    Hexagon validation failed: Side length variance too high (CV={cv:.2f})")
        return False

    # Calculate interior angles
    angles = []
    for i in range(6):
        v_prev = np.array(vertices[i - 1])
        v_curr = np.array(vertices[i])
        v_next = np.array(vertices[(i + 1) % 6])

        edge1 = v_curr - v_prev
        edge2 = v_next - v_curr

        len1 = np.linalg.norm(edge1)
        len2 = np.linalg.norm(edge2)

        if len1 < 1e-6 or len2 < 1e-6:
            continue

        edge1_norm = edge1 / len1
        edge2_norm = edge2 / len2

        dot_product = np.clip(np.dot(edge1_norm, edge2_norm), -1.0, 1.0)
        angle_rad = np.arccos(dot_product)
        angles.append(math.degrees(angle_rad))

    # Check angles are approximately 120° (±30°)
    if angles:
        mean_angle = np.mean(angles)
        if abs(mean_angle - 120) > 30:
            if debug:
                print(f"    Hexagon validation failed: Mean angle {mean_angle:.1f}° not close to 120°")
            return False

    if debug:
        print(f"    Hexagon validation passed: CV={cv:.2f}, mean_angle={np.mean(angles):.1f}°")

    return True


def detect_hexagon_hough(mask, debug=False):
    """
    Detect hexagon using Probabilistic Hough Lines.

    Steps:
    1. Canny edge detection
    2. HoughLinesP to detect line segments
    3. Cluster lines into 3 orientation groups (~60° apart)
    4. Find 2 parallel lines per group (6 total)
    5. Compute 6 intersections
    6. Validate hexagon geometry

    Args:
        mask: Binary mask (0/255) of the nut region
        debug: If True, print debug information

    Returns:
        List of 6 vertices [(x, y), ...] or None if hexagon not found
    """
    # === STEP 1: Canny Edge Detection ===
    edges = cv2.Canny(mask, 50, 150, apertureSize=3)

    # === STEP 2: Probabilistic Hough Transform ===
    # Parameters tuned for hex nuts
    min_line_length = 20  # Minimum line segment length
    max_line_gap = 10  # Maximum gap between line segments

    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180,
        threshold=30,
        minLineLength=min_line_length,
        maxLineGap=max_line_gap
    )

    if lines is None or len(lines) < 6:
        if debug:
            print(f"    Hough: Found only {len(lines) if lines is not None else 0} lines (need >= 6)")
        return None

    if debug:
        print(f"    Hough: Found {len(lines)} line segments")

    # === STEP 3: Cluster Lines by Orientation ===
    clustered_lines = cluster_lines_by_angle(lines, n_clusters=3)

    if clustered_lines is None:
        if debug:
            print(f"    Hough: Could not cluster lines into 3 groups")
        return None

    if debug:
        print(f"    Hough: Clustered into {len(clustered_lines)} groups: "
              f"{[len(g) for g in clustered_lines]} lines per group")

    # === STEP 4: Find 2 Parallel Lines per Cluster ===
    hex_lines = []
    for i, group in enumerate(clustered_lines):
        parallel_pair = find_parallel_lines(group)
        if parallel_pair is None:
            if debug:
                print(f"    Hough: Group {i} failed to find parallel pair")
            return None
        hex_lines.extend(parallel_pair)

    if len(hex_lines) != 6:
        if debug:
            print(f"    Hough: Expected 6 lines, got {len(hex_lines)}")
        return None

    if debug:
        print(f"    Hough: Found 6 hexagon lines")

    # === STEP 5: Compute Intersections ===
    # Extend lines for intersection calculation
    extended_lines = [extend_line(*line) for line in hex_lines]

    # Find intersections between consecutive pairs
    # Sort lines by angle first
    line_angles = [line_angle(*line[:4]) for line in hex_lines]
    sorted_indices = np.argsort(line_angles)
    sorted_lines = [extended_lines[i] for i in sorted_indices]

    vertices = []
    for i in range(6):
        line1 = sorted_lines[i]
        line2 = sorted_lines[(i + 1) % 6]

        intersection = line_intersection(line1, line2)
        if intersection is None:
            if debug:
                print(f"    Hough: Lines {i} and {(i + 1) % 6} are parallel (no intersection)")
            return None

        vertices.append(intersection)

    # === STEP 6: Validate Hexagon Geometry ===
    if not validate_hexagon(vertices, debug=debug):
        return None

    # Sort vertices by angle around centroid
    vertices = sort_vertices_by_angle(vertices)

    # Convert to numpy array before returning
    return np.array(vertices)


def clean_polygon_hough(approx, mask, debug=False):
    """
    Clean polygon using Hough Lines-based hexagon detection.

    This replaces the IQR-based method with a more robust approach.

    Args:
        approx: Initial polygon approximation from cv2.approxPolyDP
        mask: Binary mask of the nut region
        debug: Print debug information

    Returns:
        List of cleaned vertices [(x, y), ...]
    """
    # Try Hough-based detection
    vertices = detect_hexagon_hough(mask, debug=debug)

    if vertices is not None:
        if debug:
            print(f"    Hough-based cleaning: Found {len(vertices)} vertices")
        # Convert to numpy array before returning
        return np.array(vertices)

    # Fallback to original vertices if Hough fails
    if debug:
        print(f"    Hough-based cleaning failed, using original approximation")

    vertices = [tuple(pt[0]) for pt in approx]
    # Convert to numpy array before returning
    return np.array(vertices)



# ============================================================================
# HEXAGONALITY SCORING (Fourier Analysis for Nut vs Non-Nut)
# ============================================================================

def ray_segment_intersection(cx, cy, dx, dy, px1, py1, px2, py2):
    """
    Compute intersection of ray from (cx, cy) in direction (dx, dy) with segment from (px1, py1) to (px2, py2).
    Returns t > 0 if intersects, else None.
    """
    # Vector from point1 to point2
    sx = px2 - px1
    sy = py2 - py1
    # Vector from point1 to ray origin
    ox = cx - px1
    oy = cy - py1

    # Denominator
    denom = dx * sy - dy * sx
    if abs(denom) < 1e-6:
        return None  # Parallel

    # Parameters
    t = (ox * sy - oy * sx) / denom
    u = (ox * dy - oy * dx) / denom

    if t > 0 and 0 <= u <= 1:
        return t
    return None


def compute_radius_at_angle(contour, cx, cy, theta):
    """
    Compute the distance from center to the boundary along the ray at angle theta (in radians).
    Contour is np.array of shape (N, 1, 2) or (N, 2) with points [x, y].
    Assumes contour is closed and center is inside.
    """
    # Normalize contour shape
    if contour.ndim == 3 and contour.shape[1] == 1:
        contour = contour[:, 0, :]  # Convert (N, 1, 2) to (N, 2)

    dx = np.cos(theta)
    dy = np.sin(theta)
    min_t = np.inf
    N = len(contour)

    for i in range(N):
        px1, py1 = contour[i]
        px2, py2 = contour[(i + 1) % N]
        t = ray_segment_intersection(cx, cy, dx, dy, px1, py1, px2, py2)
        if t is not None and t < min_t:
            min_t = t

    if min_t == np.inf:
        # No intersection - return 0
        return 0.0

    return min_t


def compute_thickness_symmetry_score(contour, center, r_inner, debug=False):
    """
    Compute thickness symmetry score by measuring pattern repeatability.

    For a real hexagonal nut: thickness pattern repeats every 60° (6-fold symmetry).
    For a non-nut (spurious/offset): thickness varies irregularly without repetition.

    Method: Check autocorrelation at 60° lag (and multiples: 120°, 180°)
    High autocorrelation = repeating pattern = likely real nut

    Parameters:
    - contour: np.array of shape (N, 1, 2) or (N, 2) with [x, y] points
    - center: tuple (cx, cy) the center of mass
    - r_inner: float, the radius of the inner circle (disc_radius = 25)
    - debug: bool, print debug information

    Returns:
    - score: float (0 to 1). Higher = more repeating/symmetric pattern.
    - thickness_array: np.array of thickness values for diagnostic purposes
    """
    cx, cy = center

    # Sample every 5 degrees for 72 samples (360/5 = 72)
    angles_deg = np.arange(0, 360, 5)
    angles_rad = np.deg2rad(angles_deg)
    thickness = np.zeros(len(angles_rad))

    # Compute thickness at each angle
    for i, theta in enumerate(angles_rad):
        try:
            r_outer = compute_radius_at_angle(contour, cx, cy, theta)
            t = max(r_outer - r_inner, 0)
            thickness[i] = t
        except Exception as e:
            thickness[i] = 0

    # Check if thickness values are all the same or all zero (invalid)
    if np.std(thickness) == 0 or np.max(thickness) == 0:
        return 0.0, thickness

    # Normalize thickness to have mean=0, std=1 for autocorrelation
    thickness_normalized = (thickness - np.mean(thickness)) / np.std(thickness)

    # Calculate autocorrelation at key lags for hexagonal symmetry:
    # Lag 12 samples = 60° (primary hexagonal repeat)
    # Lag 24 samples = 120° (secondary)
    # Lag 36 samples = 180° (tertiary)

    lag_60 = 12   # 60° / 5° per sample = 12 samples
    lag_120 = 24  # 120° / 5° = 24 samples
    lag_180 = 36  # 180° / 5° = 36 samples

    autocorr_scores = []

    for lag in [lag_60, lag_120, lag_180]:
        # Circular autocorrelation (wrap around)
        shifted = np.roll(thickness_normalized, lag)
        autocorr = np.corrcoef(thickness_normalized, shifted)[0, 1]
        if not np.isnan(autocorr):
            autocorr_scores.append(autocorr)

    # Symmetry score is the average of autocorrelations at 60°, 120°, 180° lags
    # For a perfect hexagon, these should all be high (~0.7-1.0)
    if len(autocorr_scores) > 0:
        score = np.mean(autocorr_scores)
        # Clip to [0, 1] range (can be negative for anti-correlated irregular patterns)
        score = max(0.0, min(1.0, score))
    else:
        score = 0.0

    if debug:
        print(f"    [Thickness Symmetry] Score: {score:.3f}")
        print(f"      Autocorrelations: 60°={autocorr_scores[0]:.3f}, 120°={autocorr_scores[1]:.3f}, 180°={autocorr_scores[2]:.3f}" if len(autocorr_scores)==3 else "")
        print(f"      Thickness range: [{thickness.min():.1f}, {thickness.max():.1f}], mean: {thickness.mean():.1f}, std: {thickness.std():.1f}")

    return score, thickness


# ============================================================================
# MOBILESAM MASK GENERATOR
# ============================================================================

class MobileSAMv2AutomaticMaskGenerator:
    """Optimized mask generator for threading."""

    def __init__(
            self,
            sam_predictor: SamPredictor,
            yolo_model: YOLO,
            conf_threshold: float = 0.3,
            iou_threshold: float = 0.7,
            min_mask_region_area: int = 3000,
            max_mask_region_area: int = 5500,
            use_grid_points: bool = True,
            points_per_side: int = 32
    ):
        self.sam = sam_predictor
        self.yolo = yolo_model
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.min_mask_region_area = min_mask_region_area
        self.max_mask_region_area = max_mask_region_area
        self.use_grid_points = use_grid_points
        self.points_per_side = points_per_side

    def generate(self, image: np.ndarray) -> List[Dict[str, Any]]:
        """Generate masks using YOLO + MobileSAM - MATCHES SAMSegmentation approach."""
        if DEBUG_MASK_GENERATION:
            print(f"[MaskGen] Starting generation - image shape: {image.shape}")

        masks = []

        # Set image for SAM predictor (once)
        self.sam.set_image(image)

        # YOLO detection (LOCKED - thread-unsafe)
        with YOLO_LOCK:
            yolo_results = self.yolo(
                image,
                verbose=False,
                conf=self.conf_threshold,
                iou=self.iou_threshold
            )

        # Extract bounding boxes
        boxes = []
        if yolo_results and len(yolo_results) > 0:
            result = yolo_results[0]
            if hasattr(result, 'boxes') and result.boxes is not None:
                boxes = result.boxes.xyxy.cpu().numpy()

        if DEBUG_MASK_GENERATION:
            print(f"[MaskGen] YOLO found {len(boxes)} boxes")

        # Generate masks from YOLO boxes (one box at a time)
        box_masks_added = 0
        box_masks_filtered = 0
        if len(boxes) > 0:
            for box in boxes:
                try:
                    x1, y1, x2, y2 = box
                    # Call SAM with ONE box at a time
                    mask_pred, scores, _ = self.sam.predict(
                        point_coords=None,
                        point_labels=None,
                        box=np.array([x1, y1, x2, y2]),
                        multimask_output=False
                    )

                    if mask_pred is not None and len(mask_pred) > 0:
                        # Convert to numpy array if it's a list
                        if isinstance(mask_pred, list):
                            mask_pred = np.array(mask_pred)
                        if isinstance(scores, list):
                            scores = np.array(scores)

                        mask = mask_pred[0]
                        # Ensure mask itself is a numpy array (handle nested structures)
                        if isinstance(mask, list):
                            mask = np.array(mask)
                        # Squeeze out any singleton dimensions
                        mask = np.squeeze(mask)

                        score = scores[0] if scores is not None else 0.0
                        area = int(mask.sum())

                        if self.min_mask_region_area <= area <= self.max_mask_region_area:
                            masks.append({
                                'segmentation': mask.astype(bool),
                                'area': area,
                                'bbox': self._mask_to_bbox(mask),
                                'predicted_iou': float(score),
                                'stability_score': float(score),
                                'crop_box': [0, 0, image.shape[1], image.shape[0]]
                            })
                            box_masks_added += 1
                        else:
                            box_masks_filtered += 1
                            if DEBUG_MASK_GENERATION and box_masks_filtered <= 3:
                                print(f"[MaskGen]   Box mask FILTERED: area={area} (need {self.min_mask_region_area}-{self.max_mask_region_area})")
                except Exception as e:
                    if DEBUG_MASK_GENERATION:
                        print(f"[MaskGen] Failed to generate mask for box {box}: {e}")

        if DEBUG_MASK_GENERATION:
            print(f"[MaskGen] Box masks: {box_masks_added} added, {box_masks_filtered} filtered")

        # Grid-based point prompts if needed (and not too many boxes)
        if self.use_grid_points and len(masks) < 5:
            h, w = image.shape[:2]
            # Create grid of points (sparse sampling like SAMSegmentation)
            points_x = np.linspace(0, w, self.points_per_side + 2)[1:-1]
            points_y = np.linspace(0, h, self.points_per_side + 2)[1:-1]

            # Sample subset of grid points (every 4th point)
            step = max(1, self.points_per_side // 8)
            grid_count = 0
            for i, y in enumerate(points_y[::step]):
                for j, x in enumerate(points_x[::step]):
                    try:
                        # Call SAM with ONE point at a time
                        mask_pred, scores, _ = self.sam.predict(
                            point_coords=np.array([[x, y]]),
                            point_labels=np.array([1]),  # Foreground
                            multimask_output=True
                        )

                        if mask_pred is not None and len(mask_pred) > 0:
                            # Convert to numpy array if it's a list
                            if isinstance(mask_pred, list):
                                mask_pred = np.array(mask_pred)
                            if isinstance(scores, list):
                                scores = np.array(scores)

                            # Take mask with highest score
                            best_idx = np.argmax(scores)
                            mask = mask_pred[best_idx]
                            # Ensure mask itself is a numpy array (handle nested structures)
                            if isinstance(mask, list):
                                mask = np.array(mask)
                            # Squeeze out any singleton dimensions
                            mask = np.squeeze(mask)

                            score = scores[best_idx]
                            area = int(mask.sum())

                            if self.min_mask_region_area <= area <= self.max_mask_region_area:
                                # Check if it overlaps with existing masks
                                if not self._overlaps_existing(mask, masks):
                                    masks.append({
                                        'segmentation': mask.astype(bool),
                                        'area': area,
                                        'bbox': self._mask_to_bbox(mask),
                                        'predicted_iou': float(score),
                                        'stability_score': float(score),
                                        'crop_box': [0, 0, image.shape[1], image.shape[0]]
                                    })
                                    grid_count += 1
                    except Exception as e:
                        continue  # Skip failed points

            if DEBUG_MASK_GENERATION:
                print(f"[MaskGen] Added {grid_count} grid-based masks")

        if DEBUG_MASK_GENERATION:
            print(f"[MaskGen] Final: {len(masks)} masks generated")
        return masks

    def _overlaps_existing(self, new_mask: np.ndarray, existing_masks: List[Dict[str, Any]]) -> bool:
        """Check if new mask significantly overlaps with existing masks."""
        if not existing_masks:
            return False

        new_mask_bool = new_mask.astype(bool)
        new_area = new_mask_bool.sum()

        for existing in existing_masks:
            existing_mask = existing['segmentation']
            overlap = np.logical_and(new_mask_bool, existing_mask).sum()
            # If >50% overlap, consider it duplicate
            if overlap > 0.5 * new_area:
                return True

        return False

    def _generate_grid_points(self, width: int, height: int) -> List[List[float]]:
        """Generate grid of points."""
        points = []
        step_x = width / (self.points_per_side + 1)
        step_y = height / (self.points_per_side + 1)

        for i in range(1, self.points_per_side + 1):
            for j in range(1, self.points_per_side + 1):
                x = i * step_x
                y = j * step_y
                points.append([x, y])

        return points

    def _mask_to_bbox(self, mask: np.ndarray) -> List[int]:
        """Convert mask to bounding box [x, y, w, h]."""
        rows = np.any(mask, axis=1)
        cols = np.any(mask, axis=0)

        if not np.any(rows) or not np.any(cols):
            return [0, 0, 0, 0]

        y_min, y_max = np.where(rows)[0][[0, -1]]
        x_min, x_max = np.where(cols)[0][[0, -1]]

        return [int(x_min), int(y_min), int(x_max - x_min), int(y_max - y_min)]


# ============================================================================
# WORKER THREAD
# ============================================================================

def worker_thread(thread_id, task_queue, result_queue, model_manager, shutdown_event):
    """
    Worker thread for processing image variations.
    Each thread creates its own SAM predictor but shares YOLO model.
    """
    try:
        # Get shared YOLO model from ModelManager
        local_yolo = model_manager.get_yolo_model()
        sam_model = model_manager.get_sam_model()

        # Create CUDA stream for this thread
        cuda_stream = torch.cuda.Stream()

        # Create thread-local SAM predictor (private state)
        # CRITICAL: Each thread needs its own predictor to avoid state corruption
        from mobile_sam import SamPredictor
        local_predictor = SamPredictor(sam_model)

        print(f"[Thread {thread_id}] Initialized with shared models")

        # Create mask generator using shared models
        mask_generator = MobileSAMv2AutomaticMaskGenerator(
            sam_predictor=local_predictor,  # Own predictor (private state)
            yolo_model=local_yolo,           # Shared model (locked calls)
            conf_threshold=0.3,             # SAMSegmentation default
            iou_threshold=0.7,               # SAMSegmentation default
            min_mask_region_area=3000,       # For hexagonal nut detection
            max_mask_region_area=5500,       # For hexagonal nut detection
            use_grid_points=True,
            points_per_side=32
        )

        print(f"[Thread {thread_id}] Ready with shared models on CUDA Stream {cuda_stream}")

        # Process tasks from queue
        while not shutdown_event.is_set():
            try:
                task = task_queue.get(timeout=1)

                if task is None:  # Shutdown signal
                    print(f"[Thread {thread_id}] Received shutdown signal")
                    break

                # CRITICAL: Unpack UUID and cancel_event from task
                image_uuid, batch_idx, preprocessed_img, params_desc, center_x, center_y, cancel_event, annular_rgb = task

                # CHECK CANCELLATION: If another thread already found hexagon, skip this work
                if cancel_event.is_set():
                    if PRINT_DETAIL:
                        print(f"[Thread {thread_id}] Task {batch_idx} cancelled (hexagon already found)")
                    task_queue.task_done()
                    continue

                # Run GPU operations in dedicated stream
                with torch.cuda.stream(cuda_stream):
                    # Convert to RGB
                    if preprocessed_img.ndim == 2:
                        preprocessed_rgb = cv2.cvtColor(preprocessed_img, cv2.COLOR_GRAY2RGB)
                    else:
                        preprocessed_rgb = preprocessed_img

                    if DEBUG_MASK_GENERATION:
                        print(f"[Thread {thread_id}] Converting image for SAM: shape={preprocessed_rgb.shape}, dtype={preprocessed_rgb.dtype}")

                    # CHECK AGAIN before expensive GPU work
                    if cancel_event.is_set():
                        if DEBUG_WORKFLOW:
                            print(f"[Thread {thread_id}] Cancelled before GPU work (batch_idx={batch_idx})")
                        task_queue.task_done()
                        continue

                    # Generate masks (YOLO calls are locked internally)
                    if DEBUG_MASK_GENERATION:
                        print(f"[Thread {thread_id}] Calling mask_generator.generate()...")
                    with torch.inference_mode():
                        masks = mask_generator.generate(preprocessed_rgb)

                    if DEBUG_MASK_GENERATION:
                        print(f"[Thread {thread_id}] Received {len(masks)} masks from generator")

                # Synchronize stream before CPU processing
                cuda_stream.synchronize()

                # CPU-bound post-processing with Hough lines detection
                result = process_masks_cpu(masks, center_x, center_y, annular_rgb)
                hexagon_found, mask_area, mask_dims, mask_verts, failure_reason, actual_mask, \
                    orig_mask_area, hex_center, center_dist, symmetry_score, \
                    composite_score, quality_rating = result

                # CRITICAL: Include UUID and failure_reason in result
                result_queue.put((image_uuid, batch_idx, hexagon_found, params_desc,
                                mask_area, mask_dims, mask_verts, failure_reason, actual_mask,
                                orig_mask_area, hex_center, center_dist, symmetry_score,
                                composite_score, quality_rating))
                task_queue.task_done()

            except Empty:
                continue
            except Exception as e:
                print(f"[Thread {thread_id}] Error processing task: {e}")
                if PRINT_DETAIL:
                    import traceback
                    traceback.print_exc()
                try:
                    failure_metrics = {'reason': 'no_nut', 'area': None}
                    result_queue.put((image_uuid, batch_idx, False, params_desc, None, None, None, failure_metrics, None, 0, (0, 0), 0.0, 0.0))
                except:
                    pass
                task_queue.task_done()

        print(f"[Thread {thread_id}] Shutting down gracefully")

    except Exception as e:
        print(f"[Thread {thread_id}] Fatal error: {e}")
        import traceback
        traceback.print_exc()


def process_masks_cpu(masks, center_x, center_y, annular_rgb=None):
    """
    CPU-bound hexagon detection logic with Hough lines and center of mass processing.

    Complete Logic (REVISED):
    1. Collect ALL masks that overlap with center point
    2. Try each mask in order until one passes hexagon criteria
    3. Use Hough lines-based polygon detection
    4. Calculate center of mass and exclude 25-pixel disc
    5. Validate the actual_nut_mask (area > 2000)
    6. Return first valid hexagon found

    Args:
        masks: List of mask dictionaries from SAM
        center_x: X coordinate of center point
        center_y: Y coordinate of center point
        annular_rgb: Original RGB annular region (BEFORE preprocessing)
    """
    if DEBUG_MASK_GENERATION:
        print(f"[process_masks_cpu] Received {len(masks)} masks" if masks else "[process_masks_cpu] No masks received!")

    if not masks:
        failure_metrics = {
            'reason': 'no_sam_masks',
            'detail': 'SAM did not generate any masks from YOLO boxes or grid points',
            'total_masks': 0,
            'yolo_boxes': 0,
            'grid_masks': 0
        }
        return False, None, (0, 0), 0, failure_metrics, None, 0, (0, 0), 0.0, 0.0, 0.0, "NONE"

    # Step 1: Collect ALL center-overlapping masks
    center_masks = []
    if DEBUG_HEXAGON_DETECTION:
        print(f"[process_masks_cpu] Checking center overlap at ({int(center_x)}, {int(center_y)})")

    for idx, mask_data in enumerate(masks):
        try:
            mask_seg = mask_data['segmentation']
            # Safety check: ensure mask_seg is a numpy array
            if isinstance(mask_seg, list):
                mask_seg = np.array(mask_seg)
            mask_seg = np.squeeze(mask_seg)  # Remove singleton dimensions

            bbox = mask_data.get('bbox', [0,0,0,0])
            area = mask_data.get('area', 0)

            # Check bounds
            if int(center_y) >= mask_seg.shape[0] or int(center_x) >= mask_seg.shape[1]:
                if DEBUG_HEXAGON_DETECTION:
                    print(f"  [CPU] Mask {idx}: CENTER OUT OF BOUNDS! center=({int(center_x)},{int(center_y)}), mask_shape={mask_seg.shape}")
                continue

            overlaps = mask_seg[int(center_y), int(center_x)]
            if DEBUG_HEXAGON_DETECTION:
                print(f"  [CPU] Mask {idx}: bbox={bbox}, area={area}, overlaps_center={overlaps}")

            if overlaps:
                center_masks.append((idx, mask_data))
                if DEBUG_HEXAGON_DETECTION:
                    print(f"    ✓ Overlaps center!")
        except (IndexError, KeyError) as e:
            if DEBUG_HEXAGON_DETECTION:
                print(f"  [CPU] Mask {idx} error checking center: {e}")
            continue

    if DEBUG_HEXAGON_DETECTION:
        print(f"[process_masks_cpu] Found {len(center_masks)} center-overlapping masks")

    if not center_masks:
        failure_metrics = {
            'reason': 'no_center_overlap',
            'detail': f'None of {len(masks)} SAM masks overlap with center point ({int(center_x)}, {int(center_y)})',
            'total_masks': len(masks),
            'center_overlapping_masks': 0,
            'center_coords': (int(center_x), int(center_y))
        }
        return False, None, (0, 0), 0, failure_metrics, None, 0, (0, 0), 0.0, 0.0, 0.0, "NONE"

    # Step 2: Try each center-overlapping mask until we find a valid hexagon
    failed_attempts = []  # Track all failures with reasons

    for idx, mask_data in center_masks:
        if DEBUG_HEXAGON_DETECTION:
            print(f"  [CPU] Evaluating Mask {idx}:")

        mask_raw = mask_data['segmentation']

        # Safety check: ensure it's a numpy array
        if isinstance(mask_raw, list):
            mask_raw = np.array(mask_raw)
        mask_raw = np.squeeze(mask_raw)

        mask = mask_raw.astype(np.uint8)
        mask_uint8 = (mask * 255).astype(np.uint8)
        mask_area = mask_data['area']

        # Find contours
        contours, _ = cv2.findContours(mask_uint8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if not contours:
            if DEBUG_HEXAGON_DETECTION:
                print(f"    [CPU] No contours found")
            failed_attempts.append({
                'mask_idx': idx,
                'reason': 'no_contours',
                'area': mask_area
            })
            continue

        # Get the largest contour
        contour = max(contours, key=cv2.contourArea)
        if DEBUG_HEXAGON_DETECTION:
            print(f"    [CPU] Found contour with area {cv2.contourArea(contour)}")

        if PRINT_DETAIL:
            print(f"  Evaluating Mask {idx}:")

        # Initial approximation
        epsilon = 0.02 * cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, epsilon, True)

        if DEBUG_HEXAGON_DETECTION:
            print(f"    [CPU] Initial approximation: {len(approx)} vertices")

        # Step 3: Clean the polygon using Hough lines
        cleaned_vertices = clean_polygon_hough(approx, mask_uint8, debug=PRINT_DETAIL)

        num_vertices = len(cleaned_vertices)
        if DEBUG_HEXAGON_DETECTION:
            print(f"    [CPU] After Hough cleaning: {num_vertices} vertices")

        if PRINT_DETAIL:
            print(f"    Final vertex count after Hough cleaning: {num_vertices}")

        # Step 4: Check if hexagonal (5-8 vertices, or exactly 6 from Hough)
        if 5 <= num_vertices <= 8:
            # Additional check: compute aspect ratio
            x, y, w, h = cv2.boundingRect(contour)
            mask_dims = (h, w)
            aspect_ratio = float(w) / h if h > 0 else 0

            if DEBUG_HEXAGON_DETECTION:
                print(f"    [CPU] Aspect ratio: {aspect_ratio:.2f} (need 0.7-1.3)")

            if PRINT_DETAIL:
                print(f"    Aspect ratio: {aspect_ratio:.2f}")

            # Hexagons should have aspect ratio close to 1
            if 0.7 < aspect_ratio < 1.3:
                if DEBUG_HEXAGON_DETECTION:
                    print(f"    [CPU] ✓ Passed aspect ratio check, processing actual_nut_mask...")
                # === NEW: Process actual_nut_mask ===
                # a) Find center of mass
                moments = cv2.moments(mask_uint8)
                if moments['m00'] != 0:
                    com_x = int(moments['m10'] / moments['m00'])
                    com_y = int(moments['m01'] / moments['m00'])
                else:
                    # Fallback to image center
                    h_img, w_img = mask_uint8.shape
                    com_x = w_img // 2
                    com_y = h_img // 2

                if DEBUG_CENTER_OF_MASS or PRINT_DETAIL:
                    print(f"    Center of mass: ({com_x}, {com_y})")

                # b) Create disc of radius 25 and exclude from mask
                disc_radius = 25
                disc_mask = np.zeros_like(mask_uint8)
                cv2.circle(disc_mask, (com_x, com_y), disc_radius, 255, -1)

                # Save original for comparison
                mask_uint8_orig = mask_uint8.copy()

                # Calculate original hexagon area (from contour and from mask)
                contour_area = cv2.contourArea(contour)
                original_mask_area = np.sum(mask_uint8_orig == 255)
                disc_area = np.sum(disc_mask == 255)

                # actual_nut_mask = hexagonal_mask - disc
                actual_nut_mask = mask_uint8.copy()
                actual_nut_mask[disc_mask == 255] = 0

                # Check if hexagon boundary intersects disc
                boundary_mask = np.zeros_like(mask_uint8)
                cv2.drawContours(boundary_mask, [contour], -1, 255, thickness=2)
                boundary_in_disc = np.logical_and(boundary_mask == 255, disc_mask == 255)

                if np.any(boundary_in_disc):
                    # Boundary intersects - set area to 0 (invalid)
                    actual_mask_area = 0
                else:
                    actual_mask_area = np.sum(actual_nut_mask == 255)

                # === VALIDATION: Check if actual_nut_mask is valid ===

                if actual_mask_area == 0:
                    if DEBUG_CENTER_OF_MASS or PRINT_DETAIL:
                        print(f"  ✗ INVALID: actual_nut_mask is empty (disc intersects boundary or covers nut)")
                    failed_attempts.append({
                        'mask_idx': idx,
                        'reason': 'disc_intersection',
                        'detail': 'Center disc (r=25) intersects hexagon boundary or covers entire nut',
                        'original_area': original_mask_area,
                        'disc_area': disc_area,
                        'com': (com_x, com_y)
                    })
                    continue

                if actual_mask_area < original_mask_area * 0.3:
                    if DEBUG_CENTER_OF_MASS or PRINT_DETAIL:
                        print(f"  ✗ INVALID: actual_nut_mask too small "
                              f"({actual_mask_area} pixels, {actual_mask_area / original_mask_area * 100:.1f}% of original)")
                    failed_attempts.append({
                        'mask_idx': idx,
                        'reason': 'mask_too_small',
                        'detail': 'After disc exclusion, mask <30% of original',
                        'actual_area': actual_mask_area,
                        'original_area': original_mask_area,
                        'percentage': actual_mask_area / original_mask_area * 100,
                        'required': '≥30%'
                    })
                    continue

                # Check if the outer boundary is still intact
                contours_check, _ = cv2.findContours(actual_nut_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

                if not contours_check:
                    if DEBUG_CENTER_OF_MASS or PRINT_DETAIL:
                        print(f"  ✗ INVALID: No contours found in actual_nut_mask")
                    failed_attempts.append({
                        'mask_idx': idx,
                        'reason': 'no_contours_after_disc',
                        'detail': 'No valid contours remain after disc exclusion',
                        'actual_area': actual_mask_area
                    })
                    continue

                largest_contour = max(contours_check, key=cv2.contourArea)

                # Check if contour is still roughly hexagonal
                epsilon_check = 0.02 * cv2.arcLength(largest_contour, True)
                approx_check = cv2.approxPolyDP(largest_contour, epsilon_check, True)

                if len(approx_check) < 4:
                    if DEBUG_CENTER_OF_MASS or PRINT_DETAIL:
                        print(f"  ✗ INVALID: actual_nut_mask has only {len(approx_check)} vertices (need >= 4)")
                    failed_attempts.append({
                        'mask_idx': idx,
                        'reason': 'insufficient_vertices_after_disc',
                        'detail': 'Hexagon boundary destroyed by disc exclusion',
                        'vertices_remaining': len(approx_check),
                        'required': '≥4',
                        'actual_area': actual_mask_area
                    })
                    continue

                # === ADDITIONAL VALIDATION: Area ===
                # a) Check if actual_nut_mask area > 2000
                if actual_mask_area <= 2000:
                    if DEBUG_CENTER_OF_MASS or PRINT_DETAIL:
                        print(f"  ✗ INVALID: actual_nut_mask area {actual_mask_area} <= 2000")
                    # Track this failure
                    failed_attempts.append({
                        'mask_idx': idx,
                        'reason': 'area_threshold',
                        'detail': 'actual_nut_mask area too small',
                        'actual_area': actual_mask_area,
                        'required': '>2000',
                        'vertices': num_vertices,
                        'aspect_ratio': aspect_ratio
                    })
                    # Return with specific failure metrics (best attempt so far)
                    failure_metrics = {
                        'reason': 'area_threshold',
                        'detail': 'Hexagon found but actual_nut_mask area ≤ 2000 pixels',
                        'area': actual_mask_area,
                        'vertices': num_vertices,
                        'aspect_ratio': aspect_ratio,
                        'all_failed_attempts': len(failed_attempts)
                    }
                    return False, None, (0, 0), 0, failure_metrics, None, 0, (0, 0), 0.0, 0.0, 0.0, "NONE"

                # === THICKNESS SYMMETRY SCORING: Measure pattern repeatability ===
                # Calculate how consistently the thickness pattern repeats
                symmetry_score, thickness_values = compute_thickness_symmetry_score(
                    contour=contour,
                    center=(com_x, com_y),
                    r_inner=disc_radius,
                    debug=False  # Set to True for detailed debug
                )

                # Store for later output
                thickness_symmetry_score = symmetry_score

                # === COMPREHENSIVE SYMMETRY SCORING ===
                # Calculate composite score combining multiple geometric metrics
                composite_score, component_scores = calculate_composite_symmetry_score(
                    vertices=cleaned_vertices,
                    center=(com_x, com_y),
                    thickness_symmetry_score=thickness_symmetry_score,
                    reference_center=(72, 64),
                    debug=False  # Set True to see detailed breakdown
                )

                # Get quality rating
                quality_rating = interpret_symmetry_score(composite_score)

                # ALWAYS print symmetry score (not just in PRINT_DETAIL mode)
                # print(f"  [SYMMETRY] Composite Score: {composite_score:.3f} ({quality_rating})")

                # Print scoring breakdown if detail mode enabled
                if PRINT_DETAIL:
                    print(f"\n{'='*60}")
                    print(f"  [SYMMETRY ANALYSIS - DETAILED]")
                    print(f"{'='*60}")
                    print(f"  Thickness Symmetry:    {thickness_symmetry_score:.3f}")
                    print(f"  Composite Score:       {composite_score:.3f}")
                    print(f"  Quality Rating:        {quality_rating}")
                    print(f"  Component Scores:")
                    for metric, score in component_scores.items():
                        print(f"    {metric:22s}: {score:.3f}")
                    print(f"{'='*60}\n")

                # ===================================================================
                # SYMMETRY-BASED REJECTION - COMMENTED OUT
                # ===================================================================
                # To re-enable symmetry rejection, uncomment the following block:

                # DECISION LOGIC: Reject poor quality hexagons
                SYMMETRY_REJECT_THRESHOLD = 0.55
                SYMMETRY_WARNING_THRESHOLD = 0.70

                # REJECTION DISABLED - Comment out to re-enable
                # if composite_score < SYMMETRY_REJECT_THRESHOLD:
                #     if DEBUG_HEXAGON_DETECTION or PRINT_DETAIL:
                #         print(f"  ✗ REJECTED: Low symmetry score {composite_score:.3f} ({quality_rating})")
                #     # Track this failure
                #     failed_attempts.append({
                #         'mask_idx': idx,
                #         'reason': 'poor_symmetry',
                #         'composite_score': composite_score,
                #         'quality_rating': quality_rating,
                #         'actual_area': actual_mask_area,
                #         'vertices': num_vertices,
                #         'aspect_ratio': aspect_ratio
                #     })
                #     failure_metrics = {
                #         'reason': 'poor_symmetry',
                #         'detail': f'Hexagon found but symmetry score {composite_score:.3f} < {SYMMETRY_REJECT_THRESHOLD}',
                #         'composite_score': composite_score,
                #         'quality_rating': quality_rating,
                #         'threshold': SYMMETRY_REJECT_THRESHOLD,
                #         'component_scores': component_scores,
                #         'actual_area': actual_mask_area,
                #         'vertices': num_vertices,
                #         'all_failed_attempts': len(failed_attempts)
                #     }
                #     return False, None, (0, 0), 0, failure_metrics, None, 0, (0, 0), 0.0, 0.0, 0.0, "NONE"

                # Optional: Warning for marginal cases (still shows warning even when rejection disabled)
                if composite_score < SYMMETRY_WARNING_THRESHOLD:
                    if DEBUG_HEXAGON_DETECTION or PRINT_DETAIL:
                        if composite_score < SYMMETRY_REJECT_THRESHOLD:
                            print(f"  ⚠ INFO: Low symmetry score {composite_score:.3f} ({quality_rating}) - would be rejected if threshold enabled")
                        else:
                            print(f"  ⚠ WARNING: Marginal symmetry score {composite_score:.3f} ({quality_rating})")
                # ===================================================================

                # Calculate distance from hexagon center to image center
                h_img, w_img = mask_uint8.shape
                img_center_x = 72
                img_center_y = 64
                center_distance = np.sqrt((com_x - img_center_x)**2 + (com_y - img_center_y)**2)

                if DEBUG_CENTER_OF_MASS or PRINT_DETAIL:
                    print(f"  ✓ VALID: actual_nut_mask has {actual_mask_area} pixels and {len(approx_check)} vertices")
                if PRINT_DETAIL:
                    print(f"  ✓ Mask {idx} identified as HEXAGONAL NUT (vertices={num_vertices}, aspect_ratio={aspect_ratio:.2f})")

                # Return with additional metrics
                return True, actual_mask_area, mask_dims, num_vertices, None, actual_nut_mask, \
                       original_mask_area, (com_x, com_y), center_distance, thickness_symmetry_score, \
                       composite_score, quality_rating
            else:
                if DEBUG_HEXAGON_DETECTION:
                    print(f"    [CPU] ✗ Failed aspect ratio check ({aspect_ratio:.2f})")
                if PRINT_DETAIL:
                    print(f"  ✗ Mask {idx} failed aspect ratio check")
                failed_attempts.append({
                    'mask_idx': idx,
                    'reason': 'aspect_ratio',
                    'aspect_ratio': aspect_ratio,
                    'vertices': num_vertices,
                    'area': mask_area,
                    'required_range': '0.7-1.3'
                })
        else:
            if DEBUG_HEXAGON_DETECTION:
                print(f"    [CPU] ✗ Wrong vertex count ({num_vertices}, need 5-8)")
            if PRINT_DETAIL:
                print(f"  ✗ Mask {idx} has {num_vertices} vertices (need 5-8)")
            failed_attempts.append({
                'mask_idx': idx,
                'reason': 'wrong_vertex_count',
                'vertices': num_vertices,
                'area': mask_area
            })

    # No valid hexagon found in any center-overlapping mask
    if DEBUG_HEXAGON_DETECTION:
        print(f"[process_masks_cpu] No valid hexagon found after checking {len(center_masks)} masks")
        print(f"[process_masks_cpu] Failed attempts: {len(failed_attempts)}")
        for attempt in failed_attempts:
            print(f"  - Mask {attempt['mask_idx']}: {attempt['reason']}")

    # Summarize failure reasons
    failure_summary = {}
    for attempt in failed_attempts:
        reason = attempt['reason']
        failure_summary[reason] = failure_summary.get(reason, 0) + 1

    # Return failure metrics indicating no nut detected with detailed breakdown
    failure_metrics = {
        'reason': 'no_valid_hexagon',
        'detail': f'Checked {len(center_masks)} center-overlapping masks, none passed all validation',
        'total_masks': len(masks),
        'center_overlapping': len(center_masks),
        'failed_attempts': len(failed_attempts),
        'failure_breakdown': failure_summary,
        'all_failures': failed_attempts if PRINT_DETAIL else None
    }
    return False, None, (0, 0), 0, failure_metrics, None, 0, (0, 0), 0.0, 0.0, 0.0, "NONE"


# ============================================================================
# THREADED NUT DETECTOR
# ============================================================================

class ThreadedNutDetector:
    """Threaded detector with ModelManager integration."""

    def __init__(self, num_threads=3):
        self.num_threads = num_threads
        self.model_manager = ModelManager.get_instance()
        self.shutdown_event = threading.Event()

        # Color analysis
        self.color_analyzer = HubBottomBearingAnalyzer()

        # Load models in ModelManager
        print(f"\n{Colors.CYAN}Loading models via ModelManager...{Colors.RESET}")
        self.model_manager.load_models()
        print_vram_usage("After ModelManager init | ")

    def process_single_image(
            self,
            image_rgb: np.ndarray,
            image_id: str,
            task_queue: Queue,
            result_queue: Queue
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Process a single image with SEQUENTIAL variation submission and IMMEDIATE early termination.

        Returns: (hexagon_found, best_result_dict)
        """
        # Generate unique UUID for this image
        image_uuid = str(uuid.uuid4())

        # Clear BOTH queues at START (prevents contamination from previous image)
        while not task_queue.empty():
            try:
                task_queue.get_nowait()
                task_queue.task_done()
            except Empty:
                break

        while not result_queue.empty():
            try:
                result_queue.get_nowait()
            except Empty:
                break

        # CRITICAL: Create cancel_event for this image (shared across all variations)
        cancel_event = threading.Event()

        # Extract annular region - MATCH SAMSegmentation defaults
        outer_radius = 80  # SAMSegmentation default
        inner_radius = 0   # SAMSegmentation default (full disk, not annular)

        # Calculate center coordinates (assume image center)
        h, w = image_rgb.shape[:2]
        center = (632, 360)

        annular_region, annular_mask = extract_annular_region(
            image_rgb,
            center=center,
            outer_radius=outer_radius,
            inner_radius=inner_radius
        )

        # DEBUG: Save annular region to see what was extracted
        if DEBUG_SAVE_IMAGES:
            debug_annular_filename = f"DEBUG_annular_{image_id.replace(' ', '_')}.png"
            debug_annular_path = os.path.join("C:/temp", debug_annular_filename)
            try:
                os.makedirs("C:/temp", exist_ok=True)
                cv2.imwrite(debug_annular_path, cv2.cvtColor(annular_region, cv2.COLOR_RGB2BGR))
                if DEBUG_PREPROCESSING:
                    print(f"[DEBUG] Saved annular region to: {debug_annular_path}")
                    print(f"[DEBUG] Annular region shape: {annular_region.shape}, dtype: {annular_region.dtype}")
                    print(f"[DEBUG] Annular mask shape: {annular_mask.shape}, dtype: {annular_mask.dtype}")
                    print(f"[DEBUG] Annular mask True pixels: {np.sum(annular_mask)}")
            except Exception as e:
                if DEBUG_PREPROCESSING:
                    print(f"[DEBUG] Failed to save annular region: {e}")

        # Center coordinates in the annular region
        center_x = outer_radius
        center_y = outer_radius

        # Create preprocessing variations (6 total) - EXACT MATCH to SAMSegmentation
        # Base values: sx=35, sy=35, gamma=2.0
        variations = []

        # SAMSegmentation variations (from _create_preprocessed_batch):
        # Variation 1: (sx, sy, gamma) = (35, 35, 2.0)
        variations.append((35, 35, 2.0))

        # Variation 2: (sx-5, sy-5, gamma) = (30, 30, 2.0)
        variations.append((40, 40, 2.0))

        # Variation 3: (sx, sy, gamma+0.5) = (35, 35, 2.5)
        variations.append((35, 35, 2.5))

        # Variation 4: (sx-5, sy-5, gamma+0.5) = (30, 30, 2.5)
        variations.append((40, 40, 2.5))

        # Variation 5: (sx, sy, gamma+1.0) = (35, 35, 3.0)
        variations.append((35, 35, 3.0))

        # Variation 6: (sx-5, sy-5, gamma+1.0) = (30, 30, 3.0)
        variations.append((40, 40, 3.0))

        # SEQUENTIAL PROCESSING: Submit ONE variation at a time
        best_result = {}
        hexagon_found = False

        # Track best failed attempt across all variations
        best_failed_metrics = None
        best_failed_variation = None

        for batch_idx, (sx, sy, gamma) in enumerate(variations):
            # CHECK: If hexagon already found, stop immediately
            if hexagon_found:
                break

            # Preprocess this variation
            preprocessed = pixBackgroundNorm_masked(annular_region, annular_mask, sx=sx, sy=sy, bgval=200)
            preprocessed = pixGammaCorrection_masked(preprocessed, annular_mask, gamma=gamma)
            preprocessed = cv2.bilateralFilter(preprocessed, d=15,
                                               sigmaColor=30, sigmaSpace=40)
            params_desc = f"bg={sx}, gamma={gamma}"

            # DEBUG: Save the first preprocessed image to see what it looks like
            if DEBUG_SAVE_IMAGES and batch_idx == 0:
                debug_filename = f"DEBUG_preprocessed_{image_id.replace(' ', '_')}.png"
                debug_path = os.path.join("C:/temp", debug_filename)
                try:
                    # Ensure directory exists
                    os.makedirs("C:/temp", exist_ok=True)
                    # Save preprocessed image
                    if preprocessed.ndim == 3:
                        cv2.imwrite(debug_path, cv2.cvtColor(preprocessed, cv2.COLOR_RGB2BGR))
                    else:
                        cv2.imwrite(debug_path, preprocessed)
                    if DEBUG_PREPROCESSING:
                        print(f"[DEBUG] Saved preprocessed image to: {debug_path}")
                        print(f"[DEBUG] Preprocessed shape: {preprocessed.shape}, dtype: {preprocessed.dtype}")
                        print(f"[DEBUG] Preprocessed min: {preprocessed.min()}, max: {preprocessed.max()}, mean: {preprocessed.mean():.2f}")
                except Exception as e:
                    if DEBUG_PREPROCESSING:
                        print(f"[DEBUG] Failed to save preprocessed image: {e}")

            if DEBUG_WORKFLOW:
                print(f"[Sequential] Submitting variation {batch_idx + 1}/6: {params_desc}")

            # Submit task to queue
            task_queue.put((image_uuid, batch_idx, preprocessed, params_desc,
                          center_x, center_y, cancel_event, annular_region))

            # WAIT for result from this variation
            while True:
                try:
                    result_data = result_queue.get(timeout=5)  # 5 second timeout per variation

                    # UUID check: Is this result for THIS image?
                    result_uuid, result_idx, found, result_params, area, dims, verts, fail_metrics, actual_mask, \
                        orig_area, hex_center, center_dist, sym_score, composite_score, quality_rating = result_data

                    if result_uuid != image_uuid:
                        if PRINT_DETAIL:
                            print(f"[Sequential] Discarding stale result (UUID mismatch)")
                        continue  # Stale result from previous image - discard

                    # Got valid result for this variation
                    if found:
                        # HEXAGON FOUND! Set cancel_event to stop other threads
                        cancel_event.set()
                        best_result = {
                            'area': area,
                            'dims': dims,
                            'vertices': verts,
                            'params': result_params,
                            'failure_metrics': None,
                            'mask': actual_mask,
                            'annular_rgb': annular_region,
                            'orig_area': orig_area,
                            'hex_center': hex_center,
                            'center_dist': center_dist,
                            'sym_score': sym_score,
                            'composite_score': composite_score,
                            'quality_rating': quality_rating
                        }
                        hexagon_found = True
                        if DEBUG_WORKFLOW:
                            print(f"[Sequential] ✓ Hexagon found in variation {batch_idx + 1}! Stopping search.")
                        break  # Exit wait loop

                    else:
                        # This variation didn't find hexagon
                        # Track the best failed attempt (prioritize secondary check failures)
                        if fail_metrics and isinstance(fail_metrics, dict):
                            reason = fail_metrics.get('reason')

                            # Priority: secondary check failures > no_nut
                            if reason in ['area', 'color']:
                                # This is a secondary check failure - track it
                                if best_failed_metrics is None:
                                    # First secondary check failure
                                    best_failed_metrics = fail_metrics
                                    best_failed_variation = result_params
                                else:
                                    # Compare with existing best
                                    # Prefer: larger area
                                    current_area = fail_metrics.get('area', 0)
                                    best_area = best_failed_metrics.get('area', 0)

                                    if current_area and best_area:
                                        if current_area > best_area:
                                            # Better area
                                            best_failed_metrics = fail_metrics
                                            best_failed_variation = result_params
                            elif reason == 'no_nut' and best_failed_metrics is None:
                                # Only store no_nut if we haven't seen any secondary failures
                                best_failed_metrics = fail_metrics
                                best_failed_variation = result_params

                        if DEBUG_WORKFLOW:
                            print(f"[Sequential] Variation {batch_idx + 1} found nothing, continuing...")
                        break  # Exit wait loop, move to next variation

                except Empty:
                    # Timeout - variation failed
                    if PRINT_DETAIL:
                        print(f"[Sequential] Variation {batch_idx + 1} timed out")
                    break  # Exit wait loop, move to next variation

        # Store best failed metrics in result
        if not hexagon_found and best_failed_metrics:
            best_result['failure_metrics'] = best_failed_metrics
            best_result['failure_variation'] = best_failed_variation

        return hexagon_found, best_result

    def process_directory(self, input_dir: str):
        """Process all images in directory."""
        # Find all images
        image_files = []
        for ext in ['*.jpg', '*.jpeg', '*.png', '*.bmp']:
            image_files.extend(glob.glob(os.path.join(input_dir, ext)))

        if not image_files:
            print(f"{Colors.RED}No images found in {input_dir}{Colors.RESET}")
            return

        total = len(image_files)

        print(f"\n{Colors.BOLD}Processing {total} images with {self.num_threads} threads{Colors.RESET}")
        print(f"Pipeline: bg_norm(35/40) → gamma(2.0/2.5/3.0)")
        print(f"Detection: Hough lines + Center of mass with 25px disc exclusion")
        print(f"Strategy: Sequential submission with immediate early termination")
        print(f"Expected VRAM: ~1.5-1.8 GB (safe for RTX 3050)")
        print("=" * 70)

        # Load and crop hub and bottom bearing reference images for color analysis
        print(f"\n{Colors.CYAN}Loading hub and bottom bearing reference images...{Colors.RESET}")
        center_x = 320  # These should match your typical image center
        center_y = 240
        outer_radius = 80
        inner_radius = 0

        num_refs = self.color_analyzer.load_and_crop_references(
            input_dir, center_x, center_y, outer_radius, inner_radius
        )

        if num_refs > 0:
            print(f"{Colors.GREEN}✓ Loaded and cropped {num_refs} reference images{Colors.RESET}")
        else:
            print(f"{Colors.YELLOW}No hub and bottom bearing images found - color analysis disabled{Colors.RESET}")
        print("=" * 70)

        # Create queues
        task_queue = Queue()
        result_queue = Queue()

        # Start worker threads with ModelManager
        threads = []
        try:
            for i in range(self.num_threads):
                t = threading.Thread(
                    target=worker_thread,
                    args=(i, task_queue, result_queue, self.model_manager,
                          self.shutdown_event),
                    daemon=True
                )
                t.start()
                threads.append(t)

            time.sleep(2)  # Let threads initialize
            print_vram_usage("After thread initialization | ")

            # Process each image
            detected_count = 0
            error_count = 0
            batch_start = time.time()

            for img_idx, file_path in enumerate(image_files, 1):
                filename = os.path.basename(file_path)
                # if "nut" not in filename.lower():
                #     continue
                if "top" not in filename.lower():
                    continue
                # # if "nut" in filename.lower():
                #     continue

                # OPTIMIZATION: 3-second delay before loading next image (for stability)
                if img_idx > 1:
                    if PRINT_DETAIL:
                        print(f"[Pacing] Waiting 3 seconds before loading next image...")
                    time.sleep(1)

                # START TIMING: From file load to result
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

                    # Process using UUID-based tracking
                    hexagon_found, best_result = self.process_single_image(
                        image_rgb,
                        image_id=filename,
                        task_queue=task_queue,
                        result_queue=result_queue
                    )

                    file_elapsed = time.time() - file_start

                    # Print result
                    if hexagon_found:
                        area = best_result.get('area')
                        orig_area = best_result.get('orig_area', 0)
                        hex_center = best_result.get('hex_center', (0, 0))
                        center_dist = best_result.get('center_dist', 0.0)
                        sym_score = best_result.get('sym_score', 0.0)
                        composite_score = best_result.get('composite_score', 0.0)
                        quality_rating = best_result.get('quality_rating', 'UNKNOWN')

                        # New compact format output
                        print(
                            f"{filename}: {Colors.GREEN}YES{Colors.RESET} (area check), "
                            f"orig_area={orig_area}, actual_area={area}, "
                            f"center=({hex_center[0]},{hex_center[1]}), dist={center_dist:.1f}, "
                            f"symmetry={composite_score:.3f} ({quality_rating}); {file_elapsed:.2f}s"
                        )
                        detected_count += 1

                        # Color analysis: compare nut to references
                        if self.color_analyzer.ready:
                            nut_mask = best_result.get('mask')
                            nut_annular_rgb = best_result.get('annular_rgb')

                            if nut_mask is not None and nut_annular_rgb is not None:
                                try:
                                    differences = self.color_analyzer.compare_nut_to_references(
                                        filename, nut_mask, nut_annular_rgb
                                    )
                                except Exception as e:
                                    if DEBUG_WORKFLOW:
                                        print(f"  {Colors.YELLOW}Warning: Color analysis failed: {e}{Colors.RESET}")
                    else:
                        # Failure - use compact format
                        fail_metrics = best_result.get('failure_metrics')

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
                    if PRINT_DETAIL:
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
            if detected_count > 0:
                print(f"  Speedup estimate:  {self.num_threads * 0.7:.1f}× (accounting for YOLO lock)")
            print_vram_usage("  Final ")
            print("=" * 70)

            # Save color analysis results to CSV
            if self.color_analyzer.ready and self.color_analyzer.comparison_results:
                output_csv = os.path.join(input_dir, "color_analysis_results.csv")
                self.color_analyzer.save_results_to_csv(output_csv)

        finally:
            # ALWAYS cleanup threads
            print(f"\n{Colors.YELLOW}Shutting down worker threads...{Colors.RESET}")

            # Signal shutdown
            self.shutdown_event.set()

            # Send None signals
            for _ in range(self.num_threads):
                task_queue.put(None)

            # Wait for threads with timeout
            for i, t in enumerate(threads):
                t.join(timeout=5)
                if t.is_alive():
                    print(f"{Colors.YELLOW}Warning: Thread {i} didn't shutdown cleanly{Colors.RESET}")

            # Clear CUDA cache
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                print(f"{Colors.GREEN}✓ CUDA cache cleared{Colors.RESET}")


def main():
    print("=" * 70)
    print("Batch Hexagon Nut Detection - REVISED with Hough Lines")
    print("=" * 70)
    print("Pipeline (EXACT MATCH to SAMSegmentation):")
    print("  • Annular region: outer_radius=80, inner_radius=0")
    print("  • bg_norm variations: (35,35) and (30,30)")
    print("  • gamma variations: 2.0, 2.5, 3.0")
    print("  • 6 total variations matching SAMSegmentation batch mode")
    print("Detection:")
    print("  • Hough lines-based hexagon identification")
    print("  • Center of mass calculation")
    print("  • 25-pixel disc exclusion for actual_nut_mask")
    print("  • Hexagon validation (side lengths, angles)")
    print("  • Area validation: actual_nut_mask area > 2000 pixels")
    print("Architecture:")
    print("  • Sequential variation submission")
    print("  • Immediate early termination")
    print("  • UUID-based task tracking")
    print("  • ModelManager for shared models")
    print("Parameters:")
    print("  • Total variations: 6")
    print("  •   1. (35,35,2.0)  2. (30,30,2.0)")
    print("  •   3. (35,35,2.5)  4. (30,30,2.5)")
    print("  •   5. (35,35,3.0)  6. (30,30,3.0)")
    print("  • Area limits: 3000-5500 pixels")
    print("  • Vertices: 5-8 (or exactly 6 from Hough)")
    print("  • Aspect ratio: 0.7-1.3")
    print("  • Disc exclusion radius: 25 pixels")
    print("Debug Flags (set at top of file):")
    print(f"  • DEBUG_PREPROCESSING: {DEBUG_PREPROCESSING} (annular, bg_norm, gamma)")
    print(f"  • DEBUG_MASK_GENERATION: {DEBUG_MASK_GENERATION} (YOLO, SAM, filtering)")
    print(f"  • DEBUG_HEXAGON_DETECTION: {DEBUG_HEXAGON_DETECTION} (Hough, vertices, aspect)")
    print(f"  • DEBUG_CENTER_OF_MASS: {DEBUG_CENTER_OF_MASS} (COM, disc, validation)")
    print(f"  • DEBUG_WORKFLOW: {DEBUG_WORKFLOW} (sequential, early termination)")
    print(f"  • DEBUG_SAVE_IMAGES: {DEBUG_SAVE_IMAGES} (save to C:/temp)")
    print("Expected:")
    print("  • VRAM: ~1.5-1.8 GB")
    print("  • Processing: 0.5-1.5s per image")
    print("  • Accuracy: Should match SAMSegmentation")
    print("=" * 70)

    input_dir = "C:/AshokLeylandImages/DOSTPLUS/LHS"
    # input_dir = "C:/RHS_Images/NutAndPlateWasher"

    # Verify input directory
    if not os.path.exists(input_dir):
        print(f"{Colors.RED}ERROR: Input directory not found: {input_dir}{Colors.RESET}")
        print(f"Please update the path in main()")
        return

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
        detector = ThreadedNutDetector(num_threads=3)
        detector.process_directory(input_dir)
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Interrupted by user{Colors.RESET}")
    except Exception as e:
        print(f"\n{Colors.RED}FATAL ERROR: {e}{Colors.RESET}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()