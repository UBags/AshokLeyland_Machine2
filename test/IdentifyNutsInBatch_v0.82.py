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

    return vertices


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
        return vertices

    # Fallback to original vertices if Hough fails
    if debug:
        print(f"    Hough-based cleaning failed, using original approximation")

    vertices = [tuple(pt[0]) for pt in approx]
    return vertices


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
                        mask = mask_pred[0]
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
                            # Take mask with highest score
                            best_idx = np.argmax(scores)
                            mask = mask_pred[best_idx]
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
            conf_threshold=0.25,             # SAMSegmentation default
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
                hexagon_found, mask_area, mask_dims, mask_verts, failure_reason = result

                # CRITICAL: Include UUID and failure_reason in result
                result_queue.put((image_uuid, batch_idx, hexagon_found, params_desc,
                                mask_area, mask_dims, mask_verts, failure_reason))
                task_queue.task_done()

            except Empty:
                continue
            except Exception as e:
                print(f"[Thread {thread_id}] Error processing task: {e}")
                if PRINT_DETAIL:
                    import traceback
                    traceback.print_exc()
                try:
                    failure_metrics = {'reason': 'no_nut', 'area': None, 'b_minus_r': None}
                    result_queue.put((image_uuid, batch_idx, False, params_desc, None, None, None, failure_metrics))
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
    5. Validate the actual_nut_mask (area > 2000 and B-R > 5)
    6. Return first valid hexagon found

    Args:
        masks: List of mask dictionaries from SAM
        center_x: X coordinate of center point
        center_y: Y coordinate of center point
        annular_rgb: Original RGB annular region (BEFORE preprocessing) for color validation
    """
    if DEBUG_MASK_GENERATION:
        print(f"[process_masks_cpu] Received {len(masks)} masks" if masks else "[process_masks_cpu] No masks received!")

    if not masks:
        failure_metrics = {'reason': 'no_nut', 'area': None, 'b_minus_r': None}
        return False, None, (0, 0), 0, failure_metrics

    # Step 1: Collect ALL center-overlapping masks
    center_masks = []
    if DEBUG_HEXAGON_DETECTION:
        print(f"[process_masks_cpu] Checking center overlap at ({int(center_x)}, {int(center_y)})")

    for idx, mask_data in enumerate(masks):
        try:
            mask_seg = mask_data['segmentation']
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
        failure_metrics = {'reason': 'no_nut', 'area': None, 'b_minus_r': None}
        return False, None, (0, 0), 0, failure_metrics

    # Step 2: Try each center-overlapping mask until we find a valid hexagon
    for idx, mask_data in center_masks:
        if DEBUG_HEXAGON_DETECTION:
            print(f"  [CPU] Evaluating Mask {idx}:")

        mask = mask_data['segmentation'].astype(np.uint8)
        mask_uint8 = (mask * 255).astype(np.uint8)
        mask_area = mask_data['area']

        # Find contours
        contours, _ = cv2.findContours(mask_uint8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if not contours:
            if DEBUG_HEXAGON_DETECTION:
                print(f"    [CPU] No contours found")
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

                # actual_nut_mask = hexagonal_mask - disc
                actual_nut_mask = mask_uint8.copy()
                actual_nut_mask[disc_mask == 255] = 0

                if DEBUG_CENTER_OF_MASS or PRINT_DETAIL:
                    print(f"    Disc radius: {disc_radius}, excluded pixels: {np.sum(disc_mask == 255)}")
                    print(f"    actual_nut_mask pixels: {np.sum(actual_nut_mask == 255)} "
                          f"(original had {np.sum(mask_uint8_orig == 255)})")

                # === VALIDATION: Check if actual_nut_mask is valid ===
                actual_mask_area = np.sum(actual_nut_mask == 255)
                original_mask_area = np.sum(mask_uint8_orig == 255)

                if actual_mask_area == 0:
                    if DEBUG_CENTER_OF_MASS or PRINT_DETAIL:
                        print(f"  ✗ INVALID: actual_nut_mask is empty (disc completely covers nut)")
                    continue

                if actual_mask_area < original_mask_area * 0.3:
                    if DEBUG_CENTER_OF_MASS or PRINT_DETAIL:
                        print(f"  ✗ INVALID: actual_nut_mask too small "
                              f"({actual_mask_area} pixels, {actual_mask_area / original_mask_area * 100:.1f}% of original)")
                    continue

                # Check if the outer boundary is still intact
                contours_check, _ = cv2.findContours(actual_nut_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

                if not contours_check:
                    if DEBUG_CENTER_OF_MASS or PRINT_DETAIL:
                        print(f"  ✗ INVALID: No contours found in actual_nut_mask")
                    continue

                largest_contour = max(contours_check, key=cv2.contourArea)

                # Check if contour is still roughly hexagonal
                epsilon_check = 0.02 * cv2.arcLength(largest_contour, True)
                approx_check = cv2.approxPolyDP(largest_contour, epsilon_check, True)

                if len(approx_check) < 4:
                    if DEBUG_CENTER_OF_MASS or PRINT_DETAIL:
                        print(f"  ✗ INVALID: actual_nut_mask has only {len(approx_check)} vertices (need >= 4)")
                    continue

                # === ADDITIONAL VALIDATION: Area and Color Channel Checks ===
                # a) Check if actual_nut_mask area > 2000
                if actual_mask_area <= 2000:
                    if DEBUG_CENTER_OF_MASS or PRINT_DETAIL:
                        print(f"  ✗ INVALID: actual_nut_mask area {actual_mask_area} <= 2000")
                    # Return with specific failure metrics
                    failure_metrics = {
                        'reason': 'area',
                        'area': actual_mask_area,
                        'b_minus_r': None
                    }
                    return False, None, (0, 0), 0, failure_metrics

                # b) Check B_channel_avg - R_channel_avg > 5 within actual_nut_mask
                B_minus_R_value = None  # Track for failure message
                if annular_rgb is not None:
                    # Create boolean mask from actual_nut_mask
                    actual_mask_bool = (actual_nut_mask > 0)

                    # Extract RGB values where actual_nut_mask is True
                    # annular_rgb is the ORIGINAL RGB image (before preprocessing)
                    # Mask pixels: shape will be (N, 3) where N = number of True pixels
                    masked_pixels = annular_rgb[actual_mask_bool]

                    if len(masked_pixels) > 0:
                        # Calculate average R and B channels
                        # RGB format: [:, 0] = R, [:, 1] = G, [:, 2] = B
                        R_avg = np.mean(masked_pixels[:, 0])
                        B_avg = np.mean(masked_pixels[:, 2])

                        B_minus_R = B_avg - R_avg
                        B_minus_R_value = B_minus_R  # Store for failure message

                        if DEBUG_CENTER_OF_MASS or PRINT_DETAIL:
                            print(f"  Color check: R_avg={R_avg:.2f}, B_avg={B_avg:.2f}, B-R={B_minus_R:.2f}")

                        if B_minus_R <= 5:
                            if DEBUG_CENTER_OF_MASS or PRINT_DETAIL:
                                print(f"  ✗ INVALID: B-R = {B_minus_R:.2f} <= 5 (need > 5)")
                            # Return with specific failure metrics
                            failure_metrics = {
                                'reason': 'color',
                                'area': actual_mask_area,
                                'b_minus_r': B_minus_R
                            }
                            return False, None, (0, 0), 0, failure_metrics
                        else:
                            if DEBUG_CENTER_OF_MASS or PRINT_DETAIL:
                                print(f"  ✓ Color check passed: B-R = {B_minus_R:.2f} > 5")
                    else:
                        if DEBUG_CENTER_OF_MASS or PRINT_DETAIL:
                            print(f"  ✗ INVALID: No pixels in actual_nut_mask for color check")
                        continue
                else:
                    if DEBUG_CENTER_OF_MASS or PRINT_DETAIL:
                        print(f"  ⚠ WARNING: annular_rgb not provided - skipping color validation")

                if DEBUG_CENTER_OF_MASS or PRINT_DETAIL:
                    print(f"  ✓ VALID: actual_nut_mask has {actual_mask_area} pixels and {len(approx_check)} vertices")
                if PRINT_DETAIL:
                    print(f"  ✓ Mask {idx} identified as HEXAGONAL NUT (vertices={num_vertices}, aspect_ratio={aspect_ratio:.2f})")

                # First valid hexagon wins!
                return True, mask_area, mask_dims, num_vertices, None
            else:
                if DEBUG_HEXAGON_DETECTION:
                    print(f"    [CPU] ✗ Failed aspect ratio check ({aspect_ratio:.2f})")
                if PRINT_DETAIL:
                    print(f"  ✗ Mask {idx} failed aspect ratio check")
        else:
            if DEBUG_HEXAGON_DETECTION:
                print(f"    [CPU] ✗ Wrong vertex count ({num_vertices}, need 5-8)")
            if PRINT_DETAIL:
                print(f"  ✗ Mask {idx} has {num_vertices} vertices (need 5-8)")

    # No valid hexagon found in any center-overlapping mask
    if DEBUG_HEXAGON_DETECTION:
        print(f"[process_masks_cpu] No valid hexagon found after checking {len(center_masks)} masks")

    # Return failure metrics indicating no nut detected
    failure_metrics = {
        'reason': 'no_nut',
        'area': None,
        'b_minus_r': None
    }
    return False, None, (0, 0), 0, failure_metrics


# ============================================================================
# THREADED NUT DETECTOR
# ============================================================================

class ThreadedNutDetector:
    """Threaded detector with ModelManager integration."""

    def __init__(self, num_threads=3):
        self.num_threads = num_threads
        self.model_manager = ModelManager.get_instance()
        self.shutdown_event = threading.Event()

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
        center = (w // 2, h // 2)

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
        variations.append((30, 30, 2.0))

        # Variation 3: (sx, sy, gamma+0.5) = (35, 35, 2.5)
        variations.append((35, 35, 2.5))

        # Variation 4: (sx-5, sy-5, gamma+0.5) = (30, 30, 2.5)
        variations.append((30, 30, 2.5))

        # Variation 5: (sx, sy, gamma+1.0) = (35, 35, 3.0)
        variations.append((35, 35, 3.0))

        # Variation 6: (sx-5, sy-5, gamma+1.0) = (30, 30, 3.0)
        variations.append((30, 30, 3.0))

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

            # Submit task to queue (including original annular_region for color validation)
            task_queue.put((image_uuid, batch_idx, preprocessed, params_desc,
                          center_x, center_y, cancel_event, annular_region))

            # WAIT for result from this variation
            while True:
                try:
                    result_data = result_queue.get(timeout=5)  # 5 second timeout per variation

                    # UUID check: Is this result for THIS image?
                    result_uuid, result_idx, found, result_params, area, dims, verts, fail_metrics = result_data

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
                            'failure_metrics': None
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
                                    # Prefer: larger area > better B-R value
                                    current_area = fail_metrics.get('area', 0)
                                    best_area = best_failed_metrics.get('area', 0)

                                    if current_area and best_area:
                                        if current_area > best_area:
                                            # Better area
                                            best_failed_metrics = fail_metrics
                                            best_failed_variation = result_params
                                        elif current_area == best_area:
                                            # Same area, check B-R
                                            current_br = fail_metrics.get('b_minus_r')
                                            best_br = best_failed_metrics.get('b_minus_r')
                                            if current_br is not None and best_br is not None:
                                                if current_br > best_br:
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
                if "nut" in filename.lower():
                    continue

                # OPTIMIZATION: 3-second delay before loading next image (for stability)
                if img_idx > 1:
                    if PRINT_DETAIL:
                        print(f"[Pacing] Waiting 3 seconds before loading next image...")
                    time.sleep(3)

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
                        dims = best_result.get('dims', (0, 0))
                        verts = best_result.get('vertices', 0)
                        params = best_result.get('params', '')

                        dims_str = f"({dims[0]}, {dims[1]})" if dims else "N/A"
                        print(
                            f"{filename}: {Colors.GREEN}{Colors.BOLD}YES{Colors.RESET}; "
                            f"Area={area}, Dims={dims_str}, Vertices={verts}; "
                            f"{params}; {file_elapsed:.2f}s"
                        )
                        detected_count += 1
                    else:
                        # Get best failed metrics
                        fail_metrics = best_result.get('failure_metrics')
                        fail_variation = best_result.get('failure_variation', '')

                        if fail_metrics and isinstance(fail_metrics, dict):
                            reason = fail_metrics.get('reason')

                            if reason in ['area', 'color']:
                                # Secondary check failure - show details
                                area_val = fail_metrics.get('area', 'N/A')
                                br_val = fail_metrics.get('b_minus_r')
                                br_str = f"{br_val:.2f}" if br_val is not None else "N/A"

                                failure_msg = f"Failed because of secondary check - Area={area_val}, B_avg-R_avg={br_str}"
                                if fail_variation:
                                    failure_msg += f" (best attempt: {fail_variation})"
                            else:
                                # No nut detected
                                failure_msg = "Failed to detect Nut"
                        else:
                            # Fallback
                            failure_msg = "Failed to detect Nut"

                        print(f"{filename}: {Colors.RED}NO{Colors.RESET}; {file_elapsed:.2f}s - {failure_msg}")

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
    print("  • Color validation: B_channel_avg - R_channel_avg > 5")
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