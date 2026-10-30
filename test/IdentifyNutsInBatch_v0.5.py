"""
Batch Hexagon Nut Detection using MobileSAMv2
Non-GUI version for processing directories of images.

Uses the same default pipeline as the GUI version:
- Annular region extraction (center=632,360, radius=80)
- Background normalization
- Bilateral filter (disabled by default)
- Gamma correction (1.0 by default)
- Contrast normalization (disabled by default)
- Final bilateral filter
- MobileSAMv2 segmentation
- Hexagon detection with polygon cleaning

Usage:
    python batch_process_nuts.py <input_directory>
"""

import os
import sys
import glob
import time
import warnings
from pathlib import Path
from typing import Optional, List, Dict, Any
import argparse

# Project utilities
from BaseUtils import get_project_root

PRINT_DETAIL : bool = False

# --- FIX 1: Add MobileSAM to path if not installed via pip ---
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
    pixContrastNorm_masked
)

print("=" * 70)
print("Alternative Fix: Monkeypatching torch.load for trusted checkpoints")
print("=" * 70)

# Save the original torch.load (from the serialization module)
import torch.serialization
_original_torch_load = torch.serialization.load
print(f"Captured original torch.load from: {_original_torch_load.__module__}")


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

# Apply the monkeypatch at BOTH levels
torch.load = _patched_torch_load
torch.serialization.load = _patched_torch_load  # Also patch at serialization module level
print("✓ torch.load patched to use weights_only=False")
print("✓ torch.serialization.load also patched")
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



# ANSI color codes for terminal output
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    BOLD = '\033[1m'



# =============================================================================
# Custom MobileSAMv2 Mask Generator (from GUI version)
# =============================================================================

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


class HexagonNutDetector:
    """Batch processor for hexagon nut detection using MobileSAMv2."""

    def __init__(self, mobile_sam_path: str, yolo_path: str):
        """
        Initialize the detector with model paths.

        Args:
            mobile_sam_path: Path to mobile_sam.pt
            yolo_path: Path to ObjectAwareModel.pt
        """
        self.mobile_sam_path = mobile_sam_path
        self.yolo_path = yolo_path
        self.device = "cuda" if torch.cuda.is_available() else "cpu"

        # Default parameters (same as GUI defaults)
        self.annular_center = (632, 360)
        self.annular_outer_radius = 80
        self.annular_inner_radius = 0

        # Processing parameters
        self.alter_gamma = True
        self.gamma_value = 2.0
        self.apply_bilateral = False  # Disabled by default
        self.gamma_first = False  # Bilateral→Gamma order
        self.apply_contrast_norm = False  # Disabled by default

        # Background normalization parameters
        self.bg_norm_sx = 10
        self.bg_norm_sy = 10

        # Contrast normalization parameters
        self.contrast_norm_sx = 15
        self.contrast_norm_sy = 15

        # SAM parameters
        self.conf_thresh = 0.3
        self.iou_thresh = 0.7
        self.min_area = 3800
        self.max_area = 5200
        self.use_grid = True

        # Models (loaded later)
        self.sam_model = None
        self.sam_predictor = None
        self.yolo_model = None
        self.mask_generator = None

        # Precompute gamma LUT
        self.gamma_lut = self._create_gamma_lut(self.gamma_value)

        # Mask information storage
        self.last_mask_area = None
        self.last_mask_dimensions = None
        self.last_mask_vertices = None

    def _create_gamma_lut(self, gamma: float) -> np.ndarray:
        """Create a lookup table for gamma correction."""
        inv_gamma = 1.0 / gamma
        lut = np.array([((i / 255.0) ** inv_gamma) * 255
                        for i in range(256)], dtype=np.uint8)
        return lut

    def _apply_gamma(self, image: np.ndarray) -> np.ndarray:
        """Apply gamma correction using precomputed LUT."""
        return self.gamma_lut[image]

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

    def load_models(self):
        """Load MobileSAM and YOLO models."""
        print("\n" + "=" * 70)
        print("Loading MobileSAMv2 Models...")
        print("=" * 70)

        try:
            # Import after path is set
            from mobile_sam import sam_model_registry, SamPredictor
            from ultralytics import YOLO
            import torch.serialization  # Ensure serialization module is imported

            # Load MobileSAM
            print(f"Loading MobileSAM from: {self.mobile_sam_path}")
            self.sam_model = sam_model_registry['vit_t'](checkpoint=self.mobile_sam_path)
            self.sam_model.to(self.device)
            self.sam_model.eval()

            # Create SAM sam_predictor
            self.sam_predictor = SamPredictor(self.sam_model)

            # Load YOLO model
            print(f"Loading YOLO from: {self.yolo_path}")
            # Verify torch.load patch is active
            print(f"  torch.load is: {torch.load.__name__}")
            print(f"  torch.serialization.load is: {torch.serialization.load.__name__}")
            self.yolo_model = YOLO(self.yolo_path, task='detect')

            # Patch YOLO for detection mode
            self._force_yolo_detection_mode(self.yolo_model)

            # Move YOLO to device
            self.yolo_model.to(self.device)

            # Create mask generator
            self._rebuild_mask_generator()

            print(f"✓ Models loaded successfully on {self.device}")
            print("=" * 70 + "\n")

        except Exception as e:
            print(f"ERROR loading models: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)

    def _force_yolo_detection_mode(self, yolo_model):
        """Force YOLO model to work in detection mode."""
        try:
            print("  Forcing YOLO to detection mode...")

            # Step 1: Patch the model head
            if hasattr(yolo_model, 'model') and hasattr(yolo_model.model, 'model'):
                model_layers = yolo_model.model.model

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

                    from ultralytics.nn.modules.head import Detect
                    import types

                    detect_forward = Detect.forward
                    head.forward = types.MethodType(detect_forward, head)
                    head._patched_for_detection = True
                    print("  ✓ Replaced forward with Detect.forward()")

            # Step 2: Force the task type in overrides
            if hasattr(yolo_model, 'overrides'):
                yolo_model.overrides['task'] = 'detect'
                print("  ✓ Set task='detect' in overrides")

            # Step 3: Force sam_predictor to detection mode
            if hasattr(yolo_model, 'sam_predictor') and yolo_model.sam_predictor is not None:
                from ultralytics.models.yolo.detect import DetectPredictor
                old_predictor = yolo_model.sam_predictor
                new_predictor = DetectPredictor(overrides=yolo_model.overrides)

                if hasattr(old_predictor, 'model'):
                    new_predictor.model = old_predictor.model
                if hasattr(old_predictor, 'args'):
                    new_predictor.args = old_predictor.args
                if hasattr(old_predictor, 'device'):
                    new_predictor.device = old_predictor.device

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
            import traceback
            traceback.print_exc()

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
            points_per_side=32,
        )

    def _compute_edge_lengths(self, vertices):
        """Compute length of each edge in the polygon."""
        if len(vertices.shape) == 3:
            vertices = vertices.reshape(-1, 2)

        n = len(vertices)
        edge_lengths = []

        for i in range(n):
            p1 = vertices[i]
            p2 = vertices[(i + 1) % n]
            length = np.linalg.norm(p2 - p1)
            edge_lengths.append(length)

        return edge_lengths

    def _remove_small_edges(self, vertices, threshold_ratio=0.15):
        """
        Remove small edges and merge their corners.

        An edge is considered small if it's less than threshold_ratio * median_edge_length.
        When a small edge is found, merge its two corners into one.

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
        Detect if mask contains a hexagonal nut at center.

        Args:
            mask_data: Mask dictionary
            center_x: Center X coordinate
            center_y: Center Y coordinate
            idx: Mask index for logging

        Returns:
            bool: True if hexagon detected, False otherwise
        """
        mask = mask_data['segmentation'].astype(np.uint8)

        # Check if mask overlaps with center
        if not mask[int(center_y), int(center_x)]:
            return False

        # Find contours
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if not contours:
            return False

        # Get largest contour
        contour = max(contours, key=cv2.contourArea)

        if PRINT_DETAIL:
            print(f"  Evaluating Mask {idx}:")

        # Initial approximation
        epsilon = 0.02 * cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, epsilon, True)

        if PRINT_DETAIL:
            print(f"    Initial approximation: {len(approx)} vertices")

        # Clean the polygon
        cleaned_vertices = self._clean_polygon(approx, edge_threshold=0.15, corner_threshold=0.2)

        num_vertices = len(cleaned_vertices)
        if PRINT_DETAIL:
            print(f"    Final vertex count after cleaning: {num_vertices}")

        # Check if approximately hexagonal (4-8 vertices is OK)
        if 5 <= num_vertices <= 9:
            # Check aspect ratio
            x, y, w, h = cv2.boundingRect(contour)
            aspect_ratio = float(w) / h if h > 0 else 0

            if PRINT_DETAIL:
                print(f"    Aspect ratio: {aspect_ratio:.2f}")

            # Hexagons should have aspect ratio close to 1
            if 0.7 < aspect_ratio < 1.3:
                # Capture mask information before returning
                self.last_mask_area = mask_data.get('area', 0)
                # Use bounding box dimensions from the contour (already computed as x, y, w, h on line 797)
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
                print(f"  ✗ Mask {idx} has {num_vertices} vertices (need 4-8)")

        return False

    def preprocess_image(self, image_rgb: np.ndarray) -> np.ndarray:
        """
        Apply the same preprocessing pipeline as GUI.

        Args:
            image_rgb: Input RGB image

        Returns:
            Preprocessed image ready for SAM
        """

        # Step 1: Extract annular region
        annular_image, annular_mask = extract_annular_region(
            image_rgb,
            center=self.annular_center,
            outer_radius=self.annular_outer_radius,
            inner_radius=self.annular_inner_radius,
            fill_color=0
        )

        # Step 2: Background Normalization
        bg_normalized = pixBackgroundNorm_masked(
            annular_image, annular_mask, sx=self.bg_norm_sx, sy=self.bg_norm_sy
        )

        # Step 3 & 4: Bilateral + Gamma (order depends on gamma_first)
        if self.gamma_first:
            # Gamma first
            if self.alter_gamma:
                step3_result = self._apply_gamma(bg_normalized)
            else:
                step3_result = bg_normalized.copy()

            # Bilateral second
            if self.apply_bilateral:
                step4_result = self._apply_bilateral_filter(step3_result, d=21, sigmaColor=30, sigmaSpace=30)
            else:
                step4_result = step3_result.copy()
        else:
            # Bilateral first
            if self.apply_bilateral:
                step3_result = self._apply_bilateral_filter(bg_normalized, d=21, sigmaColor=30, sigmaSpace=30)
            else:
                step3_result = bg_normalized.copy()

            # Gamma second
            if self.alter_gamma:
                step4_result = self._apply_gamma(step3_result)
            else:
                step4_result = step3_result.copy()

        # Step 5: Contrast Normalization (optional)
        if self.apply_contrast_norm:
            gray_image = self._to_gray(step4_result)
            step5_result = pixContrastNorm_masked(gray_image, annular_mask,
                                                  sx=self.contrast_norm_sx, sy=self.contrast_norm_sy)
        else:
            step5_result = self._to_gray(step4_result) if step4_result.ndim == 3 else step4_result.copy()

        # Step 6: Final Bilateral
        step6_result = self._apply_bilateral_filter(step5_result, d=15, sigmaColor=30, sigmaSpace=40)

        # Convert to RGB for SAM
        image_for_sam = self._to_rgb(step6_result)

        return image_for_sam

    def process_image(self, file_path: str) -> tuple:
        """
        Process a single image file.

        Args:
            file_path: Path to image file

        Returns:
            tuple: (success: bool, detected: bool, message: str)
        """
        try:

            if PRINT_DETAIL:
                print(f"\n{'='*70}")
                print(f"PROCESSING: {os.path.basename(file_path)}")
                print(f"{'='*70}")

            # Load image
            image_bgr = cv2.imread(file_path)
            if image_bgr is None:
                return False, False, "Failed to load image"

            # DEBUG: Check loaded image
            if PRINT_DETAIL:
                print(f"[IMAGE LOADED]")
                print(f"  File: {file_path}")
                print(f"  Shape: {image_bgr.shape}")
                print(f"  Checksum: {image_bgr.sum()}")  # Simple checksum
                print(f"  Mean: {image_bgr.mean():.2f}")

            image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
            if PRINT_DETAIL:
                print(f"  RGB Mean: {image_rgb.mean():.2f}")

            # Preprocess
            image_for_sam = self.preprocess_image(image_rgb)

            # DEBUG: Check preprocessed image
            if PRINT_DETAIL:
                print(f"[PREPROCESSED IMAGE FOR SAM]")
                print(f"  Shape: {image_for_sam.shape}")
                print(f"  Dtype: {image_for_sam.dtype}")
                print(f"  Min/Max: {image_for_sam.min()}/{image_for_sam.max()}")
                print(f"  Mean: {image_for_sam.mean():.2f}")
                print(f"  Checksum: {image_for_sam.sum()}")

            # Generate masks
            if PRINT_DETAIL:
                print(f"\n[GENERATING MASKS]")
                print(f"  SAM parameters:")
                print(f"    conf_threshold: {self.conf_thresh}")
                print(f"    iou_threshold: {self.iou_thresh}")
                print(f"    min_mask_region_area: {self.min_area}")
                print(f"    max_mask_region_area: {self.max_area}")
                print(f"    use_grid_points: {self.use_grid}")

            with torch.inference_mode():
                masks = self.mask_generator.generate(image_for_sam)

            if PRINT_DETAIL:
                print(f"\n[DEBUG] Total masks received: {len(masks)}")
                print("\n" + "=" * 70)
                print("HEXAGON NUT DETECTION PIPELINE")
                print("=" * 70)

            if not masks:
                return True, False, "No masks generated", None, None, None

            # Center coordinates (same as GUI)
            center_x = self.annular_outer_radius
            center_y = self.annular_outer_radius

            if PRINT_DETAIL:
                print(f"\n[Step 1] Finding masks overlapping with center ({center_x}, {center_y})")

            # Find masks overlapping with center (same logic as GUI)
            center_masks = []
            for idx, mask_data in enumerate(masks):
                mask = mask_data['segmentation']
                # Check if mask contains the center point
                if mask[int(center_y), int(center_x)]:
                    center_masks.append((idx, mask_data))
                    if PRINT_DETAIL:
                        print(f"  Mask {idx}: overlaps with center (area={mask_data.get('area', 0)})")

            if PRINT_DETAIL:
                print(f"\n[Result] Found {len(center_masks)} masks overlapping with center")

            if not center_masks:
                if PRINT_DETAIL:
                    print("[WARNING] No masks overlap with center!")
                return True, False, "No center masks", None, None, None

            # Capture info from first center mask regardless of whether it's a hexagon
            idx, mask_data = center_masks[0]
            mask = mask_data['segmentation']
            self.last_mask_area = mask_data.get('area', 0)

            # Get bounding box dimensions of the mask (not full image dimensions)
            contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if contours:
                x, y, w, h = cv2.boundingRect(max(contours, key=cv2.contourArea))
                self.last_mask_dimensions = (h, w)  # (h, w) of bounding box

                # Get vertex count from polygon approximation
                contour = max(contours, key=cv2.contourArea)
                epsilon = 0.02 * cv2.arcLength(contour, True)
                approx = cv2.approxPolyDP(contour, epsilon, True)
                self.last_mask_vertices = len(approx)
            else:
                self.last_mask_dimensions = (0, 0)
                self.last_mask_vertices = 0

            # Identify hexagonal masks (same logic as GUI)
            if PRINT_DETAIL:
                print(f"\n[Step 2] Identifying hexagonal masks")
            hexagon_found = False

            for idx, mask_data in center_masks:
                if self._detect_hexagon(mask_data, center_x, center_y, idx):
                    hexagon_found = True
                    if PRINT_DETAIL:
                        print(f"\n[SUCCESS] Hexagonal nut detected")
                    break  # Found the nut, stop searching

            if not hexagon_found:
                if PRINT_DETAIL:
                    print(f"\n[Result] No hexagonal nut detected")

            return True, hexagon_found, "OK", self.last_mask_area, self.last_mask_dimensions, self.last_mask_vertices

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
                print(
                    f"{filename}: {Colors.GREEN}{Colors.BOLD}YES{Colors.RESET}; Area={mask_area}, Dims={dims_str}, Vertices={mask_verts}; {file_elapsed:.2f}s")
                detected_count += 1
            else:
                print(f"{filename}: {Colors.RED}NO{Colors.RESET}; {file_elapsed:.2f}s")

        elapsed = time.time() - batch_start_time

        # Summary
        print("=" * 70)
        print(f"\n{Colors.BOLD}Summary:{Colors.RESET}")
        print(f"  Total images:     {total}")
        print(f"  Hexagons detected: {Colors.GREEN}{detected_count}{Colors.RESET}")
        print(f"  Not detected:     {total - detected_count - error_count}")
        print(f"  Errors:           {Colors.RED}{error_count}{Colors.RESET}")
        print(f"  Processing time:  {elapsed:.2f}s ({elapsed / total:.2f}s per image)")
        print()


def find_mobilesam_paths():
    """Find MobileSAM checkpoint and YOLO paths using project root."""
    mobilesam_base = os.path.join(_PROJECT_ROOT, "models", "MobileSAM")

    mobile_sam_paths = [
        os.path.join(mobilesam_base, "weights", "mobile_sam.pt"),
        "weights/mobile_sam.pt",
        "../MobileSAM/weights/mobile_sam.pt",
        "C:/MobileSAM/weights/mobile_sam.pt",
    ]

    yolo_paths = [
        os.path.join(mobilesam_base, "MobileSAMv2", "weight", "ObjectAwareModel.pt"),
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

    if mobile_sam_path is None:
        raise FileNotFoundError(
            "Could not find mobile_sam.pt. Please ensure it's in:\n"
            "  MobileSAM/weights/mobile_sam.pt"
        )

    if yolo_path is None:
        raise FileNotFoundError(
            "Could not find ObjectAwareModel.pt. Please ensure it's in:\n"
            "  MobileSAM/MobileSAMv2/weight/ObjectAwareModel.pt"
        )

    return mobile_sam_path, yolo_path


def main():
#     """Main entry point."""
#     parser = argparse.ArgumentParser(
#         description='Batch process images for hexagon nut detection',
#         formatter_class=argparse.RawDescriptionHelpFormatter,
#         epilog="""
# Examples:
#   python batch_process_nuts.py /path/to/images
#   python batch_process_nuts.py ./test_images
#         """
#     )
#     parser.add_argument('input_dir', help='Directory containing images to process')
#
#     args = parser.parse_args()
#
#     # Check input directory
#     if not os.path.isdir(args.input_dir):
#         args.input_dir = "C:/AshokLeylandImages/DOSTPLUS/LHS"
#         # print(f"{Colors.RED}Error: {args.input_dir} is not a valid directory{Colors.RESET}")
#         # sys.exit(1)

    print("=" * 70)
    print("Batch Hexagon Nut Detection - MobileSAMv2")
    print("=" * 70)

    input_dir = "C:/AshokLeylandImages/DOSTPLUS/LHS"

    if torch.cuda.is_available():
        gpu_name = torch.cuda.get_device_name(0)
        gpu_mem = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
        print(f"GPU: {gpu_name} ({gpu_mem:.1f}GB)")
    else:
        print("WARNING: CUDA not available, using CPU (will be slow)")

    try:
        # Find model paths
        mobile_sam_path, yolo_path = find_mobilesam_paths()
        print(f"MobileSAM: {mobile_sam_path}")
        print(f"YOLO: {yolo_path}")

        # Create detector
        detector = HexagonNutDetector(mobile_sam_path, yolo_path)

        # Load models
        detector.load_models()

        # Process directory
        detector.process_directory(input_dir)

    except FileNotFoundError as e:
        print(f"\n{Colors.RED}ERROR: {e}{Colors.RESET}")
        sys.exit(1)
    except Exception as e:
        print(f"\n{Colors.RED}ERROR: {e}{Colors.RESET}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()