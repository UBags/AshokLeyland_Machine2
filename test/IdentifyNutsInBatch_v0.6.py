"""
Batch Hexagon Nut Detection using MobileSAMv2
Non-GUI version for processing directories of images.

Uses batch preprocessing with 6 variations (early termination):
- Annular region extraction (center=632,360, outer_radius=80)
- Background normalization with 2 variations: (sx, sy) and (sx-5, sy-5)
- Gamma correction with 3 variations: 2.0, 2.5, 3.0
- Creates 6 combinations (2 bg × 3 gamma) and exits on first successful detection
- MobileSAMv2 segmentation
- Hexagon detection with polygon cleaning

Uses ModelManager singleton to share models with other checkers (saves GPU memory).

OPTIMIZED: Uses SAMSegmentation's efficient grid point strategy (conditional + subsampled).

Usage:
    python IdentifyNutsInBatch.py <input_directory>
"""

import os
import sys
import glob
import time
import warnings
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
import argparse

# Project utilities
from BaseUtils import get_project_root

# Import ModelManager for shared model access
from camera.ModelManager import ModelManager

PRINT_DETAIL: bool = False

# --- Add MobileSAM to path ---
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

# Import image normalization methods from shared module
from utils.ImageNormalisationWithMask import (
    rgb2gray,
    ensure_float32,
    create_annular_mask,
    extract_annular_region,
    pixBackgroundNorm_masked,
    pixGammaCorrection_masked
)

# ANSI color codes for terminal output
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    BOLD = '\033[1m'


# =============================================================================
# OPTIMIZED MobileSAMv2 Mask Generator (from SAMSegmentation)
# =============================================================================

class MobileSAMv2AutomaticMaskGenerator:
    """
    MobileSAMv2 Automatic Mask Generator (OPTIMIZED).

    Uses YOLOv8 (ObjectAwareModel) for object detection to get bounding boxes,
    then MobileSAM sam_predictor to generate masks for each detected object.

    OPTIMIZATION: Uses grid points only if <5 masks found, and subsamples grid by step=4.
    """

    def __init__(
            self,
            sam_predictor,
            yolo_model,
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

            boxes = []
            if results and len(results) > 0:
                result = results[0]
                if hasattr(result, 'boxes') and result.boxes is not None:
                    boxes_data = result.boxes.xyxy.cpu().numpy()
                    boxes = [box for box in boxes_data]

            # Generate masks for each detected object
            for box in boxes:
                x1, y1, x2, y2 = box
                box_prompt = np.array([x1, y1, x2, y2])

                # Predict mask using box prompt
                try:
                    pred_masks, scores, _ = self.predictor.predict(
                        box=box_prompt,
                        multimask_output=False  # OPTIMIZATION: Single mask per box
                    )

                    # Take best mask
                    if len(scores) > 0:
                        mask = pred_masks[0]  # Single mask
                        area = mask.sum()

                        # Filter by area
                        if self.min_mask_region_area <= area <= self.max_mask_region_area:
                            masks.append({
                                'segmentation': mask,
                                'area': int(area),
                                'bbox': self._mask_to_bbox(mask),
                                'predicted_iou': float(scores[0]),
                                'stability_score': float(scores[0]),
                            })
                except Exception as e:
                    if PRINT_DETAIL:
                        print(f"Warning: Failed to generate mask for box {box}: {e}")

        except Exception as e:
            print(f"Warning: YOLO detection failed: {e}")

        # OPTIMIZATION: Only use grid points if fewer than 5 masks found
        if self.use_grid_points and len(masks) < 5:
            grid_masks = self._generate_grid_masks(image)
            # Filter out grid masks that overlap significantly with existing masks
            for gm in grid_masks:
                if not self._overlaps_existing(gm['segmentation'], masks):
                    area = gm['area']
                    if self.min_mask_region_area <= area <= self.max_mask_region_area:
                        masks.append(gm)

        return masks

    def _generate_grid_masks(self, image: np.ndarray) -> List[Dict[str, Any]]:
        """
        Generate masks using grid point prompts (OPTIMIZED - subsampled grid).

        Uses step=points_per_side//8 to subsample grid points.
        With points_per_side=32, only tests 8×8=64 points instead of 32×32=1024.
        """
        masks = []
        h, w = image.shape[:2]

        # Create grid of points
        points_x = np.linspace(0, w, self.points_per_side + 2)[1:-1]
        points_y = np.linspace(0, h, self.points_per_side + 2)[1:-1]

        # OPTIMIZATION: Sample a subset of grid points
        step = max(1, self.points_per_side // 8)  # Step=4 for points_per_side=32
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
                except:
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


class HexagonNutDetector:
    """Batch processor for hexagon nut detection using MobileSAMv2."""

    def __init__(self):
        """
        Initialize the detector using ModelManager for shared models.
        """
        # Get ModelManager singleton
        self.model_manager = ModelManager.get_instance()
        self.device = self.model_manager.get_device()

        # Annular region parameters (same as SAMSegmentation)
        self.annular_center = (632, 360)
        self.annular_outer_radius = 80
        self.annular_inner_radius = 0

        # Batch processing parameters (matching SAMSegmentation defaults)
        self.batch_bg_sx = 15  # Base background normalization sx
        self.batch_bg_sy = 15  # Base background normalization sy
        self.batch_gamma_base = 2.0  # Base gamma value (variations: 2.0, 2.5, 3.0)

        # SAM parameters (same as SAMSegmentation)
        self.conf_thresh = 0.3
        self.iou_thresh = 0.7
        self.min_area = 3800
        self.max_area = 5200
        self.use_grid = True

        # Models (obtained from ModelManager)
        self.sam_predictor = None
        self.yolo_model = None
        self.mask_generator = None

        # Mask information storage
        self.last_mask_area = None
        self.last_mask_dimensions = None
        self.last_mask_vertices = None
        self.last_successful_params = None  # Store which batch variation succeeded

    def _to_rgb(self, image: np.ndarray) -> np.ndarray:
        """Convert grayscale to RGB if needed."""
        if image.ndim == 2:
            return cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
        return image

    def load_models(self):
        """Load models from ModelManager (shared singleton)."""
        print("\n" + "=" * 70)
        print("Loading MobileSAMv2 Models from ModelManager...")
        print("=" * 70)

        try:
            # Load models via ModelManager (ensures only one copy in GPU memory)
            self.model_manager.load_models()

            # Get references to shared models
            self.sam_predictor = self.model_manager.get_sam_predictor()
            self.yolo_model = self.model_manager.get_yolo_model()

            # Create mask generator
            self._rebuild_mask_generator()

            print(f"✓ Models loaded successfully from ModelManager on {self.device}")
            print("=" * 70 + "\n")

        except Exception as e:
            print(f"ERROR loading models: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)

    def _rebuild_mask_generator(self):
        """Rebuild mask generator with current parameters."""
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

    def _compute_edge_lengths(self, vertices):
        """Compute edge lengths of polygon."""
        if len(vertices.shape) == 3:
            vertices = vertices.reshape(-1, 2)

        n = len(vertices)
        edge_lengths = []
        for i in range(n):
            v1 = vertices[i]
            v2 = vertices[(i + 1) % n]
            length = np.linalg.norm(v2 - v1)
            edge_lengths.append(length)

        return np.array(edge_lengths)

    def _remove_small_edges(self, vertices, threshold_ratio=0.15):
        """
        Remove small edges from polygon by merging corners.

        Args:
            vertices: Polygon vertices (N, 1, 2) or (N, 2)
            threshold_ratio: Small edge threshold as ratio of median length (default: 0.15)

        Returns:
            Cleaned vertices array
        """
        # Reshape if needed
        if len(vertices.shape) == 3:
            vertices = vertices.reshape(-1, 2)

        if len(vertices) < 4:
            return vertices

        # Compute edge lengths
        edge_lengths = self._compute_edge_lengths(vertices)
        median_length = np.median(edge_lengths)
        threshold = threshold_ratio * median_length

        if PRINT_DETAIL:
            print(f"    Edge lengths: {[f'{l:.1f}' for l in edge_lengths]}")
            print(f"    Median edge length: {median_length:.1f}, Threshold: {threshold:.1f}")

        # Iteratively remove small edges
        cleaned = vertices.copy()
        max_iterations = 10
        iteration = 0

        while iteration < max_iterations:
            edge_lengths = self._compute_edge_lengths(cleaned)

            if len(edge_lengths) < 4:
                break

            median_length = np.median(edge_lengths)
            threshold = threshold_ratio * median_length

            # Find smallest edge
            min_idx = np.argmin(edge_lengths)
            min_length = edge_lengths[min_idx]

            if min_length >= threshold:
                break  # No more small edges

            # Remove this edge by merging its two corners
            n = len(cleaned)
            idx1 = min_idx
            idx2 = (min_idx + 1) % n

            # Merge to midpoint
            midpoint = (cleaned[idx1] + cleaned[idx2]) / 2.0

            # Create new vertex list
            new_vertices = []
            for i in range(n):
                if i == idx1:
                    new_vertices.append(midpoint)
                elif i == idx2:
                    continue  # Skip
                else:
                    new_vertices.append(cleaned[i])

            cleaned = np.array(new_vertices)
            iteration += 1
            if PRINT_DETAIL:
                print(f"    Removed edge {min_idx} (length {min_length:.1f}), vertices: {len(cleaned)}")

        if PRINT_DETAIL:
            print(f"    After edge removal: {len(cleaned)} vertices")
        return cleaned.reshape(-1, 1, 2)

    def _remove_sharp_corners(self, vertices, min_edge_ratio=0.2):
        """
        Remove corners formed by at least one very small edge.

        A corner is removed if either of its adjacent edges is < min_edge_ratio * median_edge.

        Args:
            vertices: Polygon vertices (N, 1, 2) or (N, 2)
            min_edge_ratio: Minimum edge ratio to keep corner (default: 0.2)

        Returns:
            Cleaned vertices array
        """
        # Reshape if needed
        if len(vertices.shape) == 3:
            vertices = vertices.reshape(-1, 2)

        if len(vertices) < 4:
            return vertices

        edge_lengths = self._compute_edge_lengths(vertices)
        median_length = np.median(edge_lengths)
        threshold = min_edge_ratio * median_length

        if PRINT_DETAIL:
            print(f"    Sharp corner threshold: {threshold:.1f}")

        # Mark corners to keep
        n = len(vertices)
        keep = [True] * n

        for i in range(n):
            edge_before = edge_lengths[i - 1]
            edge_after = edge_lengths[i]

            # If either adjacent edge is very small, remove this corner
            if edge_before < threshold or edge_after < threshold:
                keep[i] = False
                if PRINT_DETAIL:
                    print(f"    Removing corner {i} (edges: {edge_before:.1f}, {edge_after:.1f})")

        # Create cleaned vertices
        cleaned = np.array([vertices[i] for i in range(n) if keep[i]])

        if len(cleaned) < 3:
            if PRINT_DETAIL:
                print(f"    Warning: Too few vertices after corner removal, keeping original")
            return vertices.reshape(-1, 1, 2)

        if PRINT_DETAIL:
            print(f"    After corner removal: {len(cleaned)} vertices")
        return cleaned.reshape(-1, 1, 2)

    def _clean_polygon(self, vertices, edge_threshold=0.15, corner_threshold=0.2):
        """
        Clean polygon by removing small edges and sharp corners.

        Process:
        1. Remove small edges (merge corners)
        2. Remove sharp corners (formed by small edges)

        Args:
            vertices: Polygon vertices
            edge_threshold: Small edge threshold ratio (default: 0.15)
            corner_threshold: Sharp corner threshold ratio (default: 0.2)

        Returns:
            Cleaned vertices
        """
        if PRINT_DETAIL:
            print(f"    Initial vertices: {len(vertices)}")

        # Step 1: Remove small edges
        cleaned = self._remove_small_edges(vertices, edge_threshold)

        # Step 2: Remove sharp corners
        cleaned = self._remove_sharp_corners(cleaned, corner_threshold)

        return cleaned

    def _detect_hexagon(self, mask_data, center_x, center_y, idx):
        """
        Detect if a mask represents a hexagonal nut.

        Args:
            mask_data: Mask dictionary
            center_x, center_y: Center coordinates
            idx: Mask index (for logging)

        Returns:
            True if hexagon detected, False otherwise
        """
        mask = mask_data['segmentation'].astype(np.uint8)

        # Find contours
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if not contours:
            return False

        # Get the largest contour
        contour = max(contours, key=cv2.contourArea)

        if PRINT_DETAIL:
            print(f"  Evaluating Mask {idx}:")

        # Initial approximation
        epsilon = 0.02 * cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, epsilon, True)

        if PRINT_DETAIL:
            print(f"    Initial approximation: {len(approx)} vertices")

        # Clean the polygon: remove small edges and sharp corners
        cleaned_vertices = self._clean_polygon(approx, edge_threshold=0.15, corner_threshold=0.2)

        num_vertices = len(cleaned_vertices)
        if PRINT_DETAIL:
            print(f"    Final vertex count after cleaning: {num_vertices}")

        # Check if approximately hexagonal (5-8 vertices is OK)
        if 5 <= num_vertices <= 8:
            # Additional check: compute aspect ratio
            x, y, w, h = cv2.boundingRect(contour)
            aspect_ratio = float(w) / h if h > 0 else 0

            if PRINT_DETAIL:
                print(f"    Aspect ratio: {aspect_ratio:.2f}")

            # Hexagons should have aspect ratio close to 1
            if 0.7 < aspect_ratio < 1.3:
                # Capture mask information before returning
                self.last_mask_area = mask_data.get('area', 0)
                # Use bounding box dimensions from the contour (already computed as x, y, w, h)
                self.last_mask_dimensions = (h, w)  # (h, w) of bounding box
                self.last_mask_vertices = num_vertices

                if PRINT_DETAIL:
                    print(
                        f"  ✓ Mask {idx} identified as HEXAGONAL NUT (vertices={num_vertices}, aspect_ratio={aspect_ratio:.2f})")
                return True
            else:
                if PRINT_DETAIL:
                    print(f"  ✗ Mask {idx} failed aspect ratio check")
        else:
            if PRINT_DETAIL:
                print(f"  ✗ Mask {idx} has {num_vertices} vertices (need 5-8)")

        return False

    def _create_preprocessed_batch(self, image_rgb: np.ndarray,
                                   sx: int, sy: int, gamma_base: float,
                                   mask: np.ndarray) -> List[Tuple[np.ndarray, str]]:
        """
        Create batch of 6 preprocessed images with parameter variations.

        Args:
            image_rgb: Input RGB image (annular region already extracted)
            sx, sy: Background normalization window sizes (base values)
            gamma_base: Gamma correction base value (2.0)
            mask: Annular mask for preprocessing

        Returns:
            List of (preprocessed_image, description) tuples
        """
        batch = []

        # 3 gamma variations: 2.0, 2.5, 3.0
        gamma_values = [gamma_base, gamma_base + 0.5, gamma_base + 1.0]

        # 2 background norm variations: (sx, sy) and (sx-5, sy-5)
        bg_variations = [(sx, sy), (sx - 5, sy - 5)]

        # Create all 6 combinations
        for bg_sx, bg_sy in bg_variations:
            for gamma in gamma_values:
                # Background normalization
                img = pixBackgroundNorm_masked(image_rgb, mask, sx=bg_sx, sy=bg_sy, bgval=200)
                # Gamma correction
                img = pixGammaCorrection_masked(img, mask, gamma=gamma)
                # Description
                desc = f"bg=({bg_sx},{bg_sy}); gamma={gamma}"
                batch.append((img, desc))

        return batch

    def process_image(self, file_path: str) -> Tuple[bool, bool, str, Any, Any, Any]:
        """
        Process a single image file with batch preprocessing (6 variations with early termination).

        Args:
            file_path: Path to image file

        Returns:
            tuple: (success: bool, detected: bool, message: str, mask_area, mask_dims, mask_verts)
        """
        try:

            if PRINT_DETAIL:
                print(f"\n{'=' * 70}")
                print(f"PROCESSING: {os.path.basename(file_path)}")
                print(f"{'=' * 70}")

            # Load image
            image_bgr = cv2.imread(file_path)
            if image_bgr is None:
                return False, False, "Failed to load image", None, None, None

            # DEBUG: Check loaded image
            if PRINT_DETAIL:
                print(f"[IMAGE LOADED]")
                print(f"  File: {file_path}")
                print(f"  Shape: {image_bgr.shape}")
                print(f"  Mean: {image_bgr.mean():.2f}")

            image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)

            # Step 1: Extract annular region (same for all batch variations)
            annular_image, annular_mask = extract_annular_region(
                image_rgb,
                center=self.annular_center,
                outer_radius=self.annular_outer_radius,
                inner_radius=self.annular_inner_radius,
                fill_color=0
            )

            # Create batch of 6 preprocessed variations
            if PRINT_DETAIL:
                print(f"\n[BATCH SETUP] Creating 6 preprocessing variations")

            batch = self._create_preprocessed_batch(
                annular_image,
                sx=self.batch_bg_sx,
                sy=self.batch_bg_sy,
                gamma_base=self.batch_gamma_base,
                mask=annular_mask
            )

            # Try each variation with early termination
            for batch_idx, (preprocessed_img, params_desc) in enumerate(batch):
                if PRINT_DETAIL:
                    print(f"\n{'=' * 70}")
                    print(f"[Batch {batch_idx + 1}/6] Trying: {params_desc}")
                    print(f"{'=' * 70}")

                # Convert preprocessed image to RGB for segmentation
                if preprocessed_img.ndim == 2:
                    preprocessed_rgb = cv2.cvtColor(preprocessed_img, cv2.COLOR_GRAY2RGB)
                else:
                    preprocessed_rgb = preprocessed_img

                # DEBUG: Check preprocessed image
                if PRINT_DETAIL:
                    print(f"[PREPROCESSED IMAGE FOR SAM]")
                    print(f"  Shape: {preprocessed_rgb.shape}")
                    print(f"  Min/Max: {preprocessed_rgb.min()}/{preprocessed_rgb.max()}")
                    print(f"  Mean: {preprocessed_rgb.mean():.2f}")

                # Generate masks for this variation
                if PRINT_DETAIL:
                    print(f"\n[GENERATING MASKS]")

                try:
                    with torch.inference_mode():
                        masks = self.mask_generator.generate(preprocessed_rgb)

                    if not masks:
                        if PRINT_DETAIL:
                            print(f"[Batch {batch_idx + 1}] No masks generated - trying next variation")
                        continue

                    if PRINT_DETAIL:
                        print(f"[Batch {batch_idx + 1}] Generated {len(masks)} masks")

                except Exception as e:
                    if PRINT_DETAIL:
                        print(f"[Batch {batch_idx + 1}] Segmentation failed: {e}")
                    continue

                # Center coordinates
                center_x = self.annular_outer_radius
                center_y = self.annular_outer_radius

                if PRINT_DETAIL:
                    print(f"[Step 1] Finding masks overlapping with center ({center_x}, {center_y})")

                # Find masks overlapping with center
                center_masks = []
                for idx, mask_data in enumerate(masks):
                    mask = mask_data['segmentation']
                    if mask[int(center_y), int(center_x)]:
                        center_masks.append((idx, mask_data))
                        if PRINT_DETAIL:
                            print(f"  Mask {idx}: overlaps with center (area={mask_data.get('area', 0)})")

                if PRINT_DETAIL:
                    print(f"[Result] Found {len(center_masks)} masks overlapping with center")

                if not center_masks:
                    if PRINT_DETAIL:
                        print(f"[Batch {batch_idx + 1}] No center-overlapping masks - trying next variation")
                    continue

                # Capture info from first center mask
                idx, mask_data = center_masks[0]
                mask = mask_data['segmentation']
                self.last_mask_area = mask_data.get('area', 0)

                # Get bounding box dimensions
                contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                if contours:
                    x, y, w, h = cv2.boundingRect(max(contours, key=cv2.contourArea))
                    self.last_mask_dimensions = (h, w)

                    contour = max(contours, key=cv2.contourArea)
                    epsilon = 0.02 * cv2.arcLength(contour, True)
                    approx = cv2.approxPolyDP(contour, epsilon, True)
                    self.last_mask_vertices = len(approx)
                else:
                    self.last_mask_dimensions = (0, 0)
                    self.last_mask_vertices = 0

                # Try to identify hexagonal mask
                if PRINT_DETAIL:
                    print(f"[Step 2] Identifying hexagonal masks")

                hexagon_found = False
                for idx, mask_data in center_masks:
                    if self._detect_hexagon(mask_data, center_x, center_y, idx):
                        hexagon_found = True
                        self.last_successful_params = params_desc  # Store successful variation
                        if PRINT_DETAIL:
                            print(f"\n[EARLY TERMINATION] Hexagonal nut found in batch {batch_idx + 1}!")
                            print(f"  Successful parameters: {params_desc}")
                        break

                # If hexagon found, return immediately (early termination)
                if hexagon_found:
                    return True, True, "OK", self.last_mask_area, self.last_mask_dimensions, self.last_mask_vertices

            # If we get here, no hexagon found in any of the 6 variations
            if PRINT_DETAIL:
                print(f"\n[BATCH COMPLETE] No hexagonal nut found in any of the 6 variations")

            return True, False, "No hexagon in any batch", self.last_mask_area, self.last_mask_dimensions, self.last_mask_vertices

        except Exception as e:
            return False, False, str(e), None, None, None

    def process_directory(self, input_dir: str):
        """
        Process all images in a directory.

        Args:
            input_dir: Path to directory containing images
        """
        # Find all image files
        image_extensions = ['*.jpg', '*.jpeg', '*.png', '*.bmp', '*.tif', '*.tiff']
        image_files = []
        for ext in image_extensions:
            image_files.extend(glob.glob(os.path.join(input_dir, ext)))
            image_files.extend(glob.glob(os.path.join(input_dir, ext.upper())))

        # Remove duplicates (Windows is case-insensitive, so *.png and *.PNG match same files)
        image_files = list(set(image_files))

        if not image_files:
            print(f"{Colors.RED}No image files found in: {input_dir}{Colors.RESET}")
            return

        image_files.sort()
        total = len(image_files)

        print(f"\n{Colors.BOLD}Processing {total} images from: {input_dir}{Colors.RESET}")
        print("=" * 70)

        detected_count = 0
        error_count = 0

        batch_start_time = time.time()

        for i, file_path in enumerate(image_files, 1):
            filename = os.path.basename(file_path)

            # Time individual file processing
            file_start_time = time.time()

            # Process image
            success, detected, message, mask_area, mask_dims, mask_verts = self.process_image(file_path)

            file_elapsed = time.time() - file_start_time

            if not success:
                print(f"{filename}: {Colors.RED}ERROR{Colors.RESET} ({message}); {file_elapsed:.2f}s")
                error_count += 1
            elif detected:
                dims_str = f"({mask_dims[0]}, {mask_dims[1]})" if mask_dims else "N/A"
                params_str = self.last_successful_params if self.last_successful_params else "N/A"
                print(
                    f"{filename}: {Colors.GREEN}{Colors.BOLD}YES{Colors.RESET}; "
                    f"Area={mask_area}, Dims={dims_str}, Vertices={mask_verts}; "
                    f"{params_str}; {file_elapsed:.2f}s"
                )
                detected_count += 1
            else:
                print(f"{filename}: {Colors.RED}NO{Colors.RESET}; {file_elapsed:.2f}s")

        elapsed = time.time() - batch_start_time

        # Summary
        print("=" * 70)
        print(f"\n{Colors.BOLD}Summary:{Colors.RESET}")
        print(f"  Total images:      {total}")
        print(f"  Hexagons detected: {Colors.GREEN}{detected_count}{Colors.RESET}")
        print(f"  Not detected:      {total - detected_count - error_count}")
        print(f"  Errors:            {Colors.RED}{error_count}{Colors.RESET}")
        print(f"  Processing time:   {elapsed:.2f}s ({elapsed / total:.2f}s per image)")
        print()


def main():
    """Main entry point."""
    print("=" * 70)
    print("Batch Hexagon Nut Detection - MobileSAMv2 (OPTIMIZED)")
    print("Using 6-variation batch preprocessing with early termination")
    print("Using ModelManager for shared model access (saves GPU memory)")
    print("Using SAMSegmentation's efficient grid strategy (16× faster!)")
    print("=" * 70)

    # You can use command line argument or hardcode the directory
    input_dir = "C:/AshokLeylandImages/DOSTPLUS/LHS"

    if torch.cuda.is_available():
        gpu_name = torch.cuda.get_device_name(0)
        gpu_mem = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
        print(f"GPU: {gpu_name} ({gpu_mem:.1f}GB)")
    else:
        print("WARNING: CUDA not available, using CPU (will be slow)")

    try:
        # Create detector (uses ModelManager internally)
        detector = HexagonNutDetector()

        # Load models from ModelManager
        detector.load_models()

        # Process directory
        detector.process_directory(input_dir)

    except Exception as e:
        print(f"\n{Colors.RED}ERROR: {e}{Colors.RESET}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()