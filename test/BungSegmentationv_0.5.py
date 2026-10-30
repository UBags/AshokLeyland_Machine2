"""
BungSegmentation v0.5
A GUI application for visualizing MobileSAMv2 segmentation masks for bung detection.
Optimized for 4GB GPU (RTX 3050)

Pipeline:
1. Disc Extraction (center 635,325; R=75)
2. Background Normalization
3. Bilateral (d=21, 30, 40)
4. Gamma (Configurable)
5. Bilateral smoothing (d=21, 30, 40)
6. Contrast Normalization
7. Optional Final Bilateral smoothing (9, 30, 30)
8. MobileSAMv2 Segmentation

Output: Count of masks with radius <= 20

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

# --- Add MobileSAM to path if not installed via pip ---
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
print("BungSegmentation v0.5 - Monkeypatching torch.load for trusted checkpoints")
print("=" * 70)

# Save the original torch.load
_original_torch_load = torch.load

# Small mask threshold
SMALL_MASK_RADIUS_THRESHOLD = 20


def _patched_torch_load(f, map_location=None, pickle_module=None, *, weights_only=None, mmap=None, **kwargs):
    """
    Patched torch.load that forces weights_only=False for trusted checkpoints.
    """
    return _original_torch_load(
        f,
        map_location=map_location,
        pickle_module=pickle_module,
        weights_only=False,
        mmap=mmap,
        **kwargs
    )


# Apply the monkeypatch
torch.load = _patched_torch_load
print("✓ torch.load patched to use weights_only=False")
print("⚠ WARNING: This disables PyTorch security features.")
print("  Only use with trusted model files!")
print("=" * 70)

# --- PyTorch 2.6+ Security Allowlist ---
try:
    if hasattr(torch.serialization, "add_safe_globals"):
        print("Applying PyTorch 2.6+ safe_globals allowlist...")

        safe_classes = [
            torch.nn.modules.container.Sequential,
            torch.nn.modules.container.ModuleList,
            torch.nn.modules.container.ModuleDict,
        ]

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
            print(f"⚠ Could not import all Ultralytics classes: {e}")
            try:
                from ultralytics.nn.tasks import SegmentationModel, DetectionModel
                safe_classes.extend([SegmentationModel, DetectionModel])
                print("✓ Added basic Ultralytics classes to safe globals")
            except ImportError as e2:
                print(f"⚠ Warning: Could not import Ultralytics task classes: {e2}")

        torch.serialization.add_safe_globals(safe_classes)
        print(f"✓ Safe globals configured ({len(safe_classes)} classes allowlisted)")

except (ImportError, AttributeError) as e:
    print(f"Note: PyTorch 2.6+ safe_globals not available or error: {e}")

# MobileSAM imports
try:
    from mobile_sam import sam_model_registry, SamPredictor
except ImportError:
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
            if "'Segment' object has no attribute 'proto'" in str(e):
                error_msg = (
                    "YOLO Compatibility Error:\n"
                    "The loaded ObjectAwareModel.pt seems incompatible with this version of 'ultralytics'.\n"
                    "Try downgrading ultralytics: pip install ultralytics==8.0.200"
                )
                print(error_msg)
                raise RuntimeError(error_msg) from e
            raise e

        # Get bounding boxes from YOLO
        boxes = []
        if len(results) > 0 and results[0].boxes is not None:
            boxes = results[0].boxes.xyxy.cpu().numpy()

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
            for gm in grid_masks:
                if not self._overlaps_existing(gm['segmentation'], masks):
                    area = gm['area']
                    if self.min_mask_region_area <= area <= self.max_mask_region_area:
                        masks.append(gm)

        return masks

    def _predict_mask_from_box(self, box: np.ndarray) -> Optional[Dict[str, Any]]:
        """Generate mask from a bounding box prompt."""
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

        points_x = np.linspace(0, w, self.points_per_side + 2)[1:-1]
        points_y = np.linspace(0, h, self.points_per_side + 2)[1:-1]

        step = max(1, self.points_per_side // 8)
        for i, y in enumerate(points_y[::step]):
            for j, x in enumerate(points_x[::step]):
                try:
                    mask_pred, scores, _ = self.predictor.predict(
                        point_coords=np.array([[x, y]]),
                        point_labels=np.array([1]),
                        multimask_output=True
                    )

                    if mask_pred is not None and len(mask_pred) > 0:
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

class BungSegmentationApp:
    def __init__(self, root: tk.Tk, mobile_sam_path: str, yolo_path: str):
        self.root = root
        self.root.title("BungSegmentation v0.5")
        self.root.geometry("1500x950")
        self.root.configure(bg="#2b2b2b")

        # Image processing state
        self.current_files = []
        self.current_index = 0

        # Disc parameters (center at 635, 325 with radius 85)
        self.disc_center = (635, 320)
        self.disc_radius = 85

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
        self.current_disc_mask = None
        self.pipeline_images = {}

        # Display scale factor
        self.display_scale = 1.5  # Larger scale for small disc

        # Gamma toggle and value
        self.alter_gamma = tk.BooleanVar(value=True)
        self.gamma_values = [round(0.5 + i * 0.25, 2) for i in range(25)]
        self.gamma_var = tk.StringVar(value="2.5")

        # Precompute all gamma LUTs
        self.gamma_luts = {gamma: self._create_gamma_lut(gamma) for gamma in self.gamma_values}

        # Final bilateral toggle
        self.apply_final_bilateral = tk.BooleanVar(value=False)

        # MobileSAMv2 parameter controls
        self.conf_thresh_values = [0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5]
        self.conf_thresh_var = tk.StringVar(value="0.3")

        self.iou_thresh_values = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
        self.iou_thresh_var = tk.StringVar(value="0.7")

        self.min_area_values = [20, 50, 100, 150, 200, 250, 300, 350, 400]
        self.min_area_var = tk.StringVar(value="50")

        self.max_area_values = [500, 700, 800, 1000, 1200, 1500, 2000, 2500, 3000]
        self.max_area_var = tk.StringVar(value="1500")

        self.use_grid_var = tk.BooleanVar(value=True)

        # Background Normalisation parameters
        self.bg_norm_sx_values = [5, 10, 15, 20, 25, 30]
        self.bg_norm_sx_var = tk.StringVar(value="10")
        self.bg_norm_sy_values = [5, 10, 15, 20, 25, 30]
        self.bg_norm_sy_var = tk.StringVar(value="10")

        # Contrast Normalisation parameters
        self.contrast_norm_sx_values = [5, 10, 15, 20, 25, 30, 35, 40]
        self.contrast_norm_sx_var = tk.StringVar(value="15")
        self.contrast_norm_sy_values = [5, 10, 15, 20, 25, 30, 35, 40]
        self.contrast_norm_sy_var = tk.StringVar(value="15")

        # Step-by-step processing state
        self.current_step = 0
        self.max_steps = 8

        # Result storage
        self._last_masks = []
        self._small_mask_count = 0

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

        # Gamma toggle
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

        # Final bilateral toggle
        self.final_bilateral_btn = ttk.Checkbutton(
            control_frame, text="Final Bilateral",
            variable=self.apply_final_bilateral, style="TCheckbutton"
        )
        self.final_bilateral_btn.pack(side=tk.LEFT, padx=(0, 10))

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

        # Confidence threshold
        ttk.Label(param_frame, text="Conf:", style="TLabel").pack(side=tk.LEFT, padx=(5, 2))
        self.conf_dropdown = ttk.Combobox(
            param_frame, textvariable=self.conf_thresh_var,
            values=self.conf_thresh_values, width=4, state="readonly"
        )
        self.conf_dropdown.pack(side=tk.LEFT, padx=(0, 8))

        # IoU threshold
        ttk.Label(param_frame, text="IoU:", style="TLabel").pack(side=tk.LEFT, padx=(0, 2))
        self.iou_dropdown = ttk.Combobox(
            param_frame, textvariable=self.iou_thresh_var,
            values=self.iou_thresh_values, width=4, state="readonly"
        )
        self.iou_dropdown.pack(side=tk.LEFT, padx=(0, 8))

        # Min Area
        ttk.Label(param_frame, text="MinArea:", style="TLabel").pack(side=tk.LEFT, padx=(0, 2))
        self.min_area_dropdown = ttk.Combobox(
            param_frame, textvariable=self.min_area_var,
            values=self.min_area_values, width=5, state="readonly"
        )
        self.min_area_dropdown.pack(side=tk.LEFT, padx=(0, 8))

        # Max Area
        ttk.Label(param_frame, text="MaxArea:", style="TLabel").pack(side=tk.LEFT, padx=(0, 2))
        self.max_area_dropdown = ttk.Combobox(
            param_frame, textvariable=self.max_area_var,
            values=self.max_area_values, width=5, state="readonly"
        )
        self.max_area_dropdown.pack(side=tk.LEFT, padx=(0, 8))

        # Use Grid checkbox
        self.use_grid_btn = ttk.Checkbutton(
            param_frame, text="Use Grid",
            variable=self.use_grid_var, style="TCheckbutton"
        )
        self.use_grid_btn.pack(side=tk.LEFT, padx=(0, 12))

        # BgNorm params
        ttk.Label(param_frame, text="BgSx:", style="TLabel").pack(side=tk.LEFT, padx=(0, 2))
        self.bg_sx_dropdown = ttk.Combobox(
            param_frame, textvariable=self.bg_norm_sx_var,
            values=self.bg_norm_sx_values, width=3, state="readonly"
        )
        self.bg_sx_dropdown.pack(side=tk.LEFT, padx=(0, 4))

        ttk.Label(param_frame, text="BgSy:", style="TLabel").pack(side=tk.LEFT, padx=(0, 2))
        self.bg_sy_dropdown = ttk.Combobox(
            param_frame, textvariable=self.bg_norm_sy_var,
            values=self.bg_norm_sy_values, width=3, state="readonly"
        )
        self.bg_sy_dropdown.pack(side=tk.LEFT, padx=(0, 8))

        # ContrastNorm params
        ttk.Label(param_frame, text="CnSx:", style="TLabel").pack(side=tk.LEFT, padx=(0, 2))
        self.cn_sx_dropdown = ttk.Combobox(
            param_frame, textvariable=self.contrast_norm_sx_var,
            values=self.contrast_norm_sx_values, width=3, state="readonly"
        )
        self.cn_sx_dropdown.pack(side=tk.LEFT, padx=(0, 4))

        ttk.Label(param_frame, text="CnSy:", style="TLabel").pack(side=tk.LEFT, padx=(0, 2))
        self.cn_sy_dropdown = ttk.Combobox(
            param_frame, textvariable=self.contrast_norm_sy_var,
            values=self.contrast_norm_sy_values, width=3, state="readonly"
        )
        self.cn_sy_dropdown.pack(side=tk.LEFT, padx=(0, 8))

        # ===== IMAGE DISPLAY =====
        canvas_frame = ttk.Frame(main_frame)
        canvas_frame.pack(fill=tk.BOTH, expand=True, pady=(4, 4))

        self.canvas = tk.Canvas(canvas_frame, bg="#1e1e1e", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # ===== BOTTOM STATUS =====
        status_frame = ttk.Frame(main_frame)
        status_frame.pack(fill=tk.X)

        self.step_info_label = ttk.Label(
            status_frame, text="Step: Ready",
            style="Status.TLabel"
        )
        self.step_info_label.pack(side=tk.LEFT, padx=5)

        self.time_label = ttk.Label(
            status_frame, text="",
            style="Status.TLabel"
        )
        self.time_label.pack(side=tk.RIGHT, padx=5)

    def _load_model(self):
        """Load MobileSAM model."""
        self.status_label.config(text="Loading MobileSAM model...")
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
            self.sam_model.to(device=self.device)
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

            self._rebuild_mask_generator()

            self.status_label.config(text="✓ Model loaded! Choose a file or folder.")
            self.choose_btn.config(state=tk.NORMAL)

            if torch.cuda.is_available():
                mem_used = torch.cuda.memory_allocated() / 1024 ** 2
                print(f"GPU Memory used: {mem_used:.1f}MB")

            print("MobileSAMv2 models loaded successfully!")

        except Exception as e:
            error_msg = f"Failed to load model: {e}"
            self.status_label.config(text=error_msg)
            messagebox.showerror("Model Load Error", error_msg)
            print(f"Error: {e}")
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
        """Rebuild mask generator with current parameters."""
        self.mask_generator = MobileSAMv2AutomaticMaskGenerator(
            sam_predictor=self.sam_predictor,
            yolo_model=self.yolo_model,
            conf_threshold=float(self.conf_thresh_var.get()),
            iou_threshold=float(self.iou_thresh_var.get()),
            min_mask_region_area=int(self.min_area_var.get()),
            max_mask_region_area=int(self.max_area_var.get()),
            use_grid_points=self.use_grid_var.get(),
            points_per_side=32,
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
                    ("Image files", "*.png *.jpg *.jpeg *.PNG *.JPG *.JPEG *.bmp *.tiff"),
                    ("All files", "*.*")
                ]
            )
            if file_path:
                self._load_file(file_path)

    def _load_folder(self, folder_path: str):
        """Load all images from a folder."""
        extensions = ['*.png', '*.jpg', '*.jpeg', '*.PNG', '*.JPG', '*.JPEG', '*.bmp', '*.tiff']
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
        """Load a single file and all other images in the same folder."""
        file_path = os.path.normpath(file_path)
        folder_path = os.path.dirname(file_path)

        # Load all images from the same folder
        extensions = ['*.png', '*.jpg', '*.jpeg', '*.PNG', '*.JPG', '*.JPEG', '*.bmp', '*.tiff']
        self.current_files = []
        for ext in extensions:
            self.current_files.extend(glob.glob(os.path.join(folder_path, ext)))
        self.current_files = sorted([os.path.normpath(f) for f in set(self.current_files)])

        # Find the index of the selected file
        self.current_index = 0
        for i, f in enumerate(self.current_files):
            if f == file_path:
                self.current_index = i
                break

        self._update_counter()
        self._run_full_pipeline()

    def _update_counter(self):
        """Update file counter display."""
        total = len(self.current_files)
        current = self.current_index + 1 if total > 0 else 0
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

    def _apply_gamma(self, image: np.ndarray) -> np.ndarray:
        """Apply gamma correction using precomputed LUT."""
        gamma = float(self.gamma_var.get())
        return self.gamma_luts[gamma][image]

    def _apply_bilateral_filter(self, image: np.ndarray, d: int = 21,
                                sigmaColor: int = 30, sigmaSpace: int = 40) -> np.ndarray:
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
            self._rebuild_mask_generator()

            start_time = time.perf_counter()

            if torch.cuda.is_available():
                torch.cuda.empty_cache()

            # Load image
            image_bgr = cv2.imread(file_path)
            if image_bgr is None:
                raise ValueError(f"Could not load image: {file_path}")

            image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)

            # Step 1: Extract circular disc region
            disc_image, disc_mask = extract_circular_region(
                image_rgb,
                center=self.disc_center,
                radius=self.disc_radius,
                fill_color=0
            )

            # Step 1b: Morphological closing with circular kernel of 10
            # kernel_size = 10
            # kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
            # disc_image_closed = cv2.morphologyEx(disc_image, cv2.MORPH_CLOSE, kernel)
            disc_image_closed1 = self._apply_bilateral_filter(disc_image, d=21, sigmaColor=30, sigmaSpace=40)
            disc_image_closed2 = self._apply_bilateral_filter(disc_image_closed1, d=9, sigmaColor=40, sigmaSpace=40)
            kernel_size = 13
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
            disc_image_closed = cv2.morphologyEx(disc_image_closed2, cv2.MORPH_CLOSE, kernel)

            self.current_disc_mask = disc_mask
            self.pipeline_images = {
                'step1_disc': disc_image_closed.copy(),
                'mask': disc_mask,
                'original': disc_image.copy(),
                'original_unclosed': disc_image.copy()
            }

            # Step 2: Background Normalisation
            bg_sx = int(self.bg_norm_sx_var.get())
            bg_sy = int(self.bg_norm_sy_var.get())
            bg_normalized = pixBackgroundNorm_masked(
                disc_image_closed, disc_mask, sx=bg_sx, sy=bg_sy
            )
            self.pipeline_images['step2_bg_norm'] = bg_normalized

            # Step 3: Bilateral (d=21, 30, 40)
            step3_result = self._apply_bilateral_filter(bg_normalized, d=21, sigmaColor=30, sigmaSpace=40)
            self.pipeline_images['step3'] = step3_result
            self.pipeline_images['step3_name'] = "Bilateral (21,30,40)"

            # Step 4: Gamma (Configurable)
            if self.alter_gamma.get():
                step4_result = self._apply_gamma(step3_result)
                self.pipeline_images['step4'] = step4_result
                self.pipeline_images['step4_name'] = f"Gamma {self.gamma_var.get()}"
            else:
                step4_result = step3_result.copy()
                self.pipeline_images['step4'] = step4_result
                self.pipeline_images['step4_name'] = "Gamma (skipped)"

            # Step 5: Bilateral smoothing (d=21, 30, 40)
            step5_result = self._apply_bilateral_filter(step4_result, d=21, sigmaColor=30, sigmaSpace=40)
            self.pipeline_images['step5'] = step5_result
            self.pipeline_images['step5_name'] = "Bilateral (21,30,40)"

            # Step 6: Contrast Normalization
            cn_sx = int(self.contrast_norm_sx_var.get())
            cn_sy = int(self.contrast_norm_sy_var.get())
            gray_image = self._to_gray(step5_result)
            step6_result = pixContrastNorm_masked(gray_image, disc_mask, sx=cn_sx, sy=cn_sy)
            self.pipeline_images['step6'] = step6_result
            self.pipeline_images['step6_name'] = f"ContrastNorm (sx={cn_sx}, sy={cn_sy})"

            # Step 7: Optional Final Bilateral (9, 30, 30)
            if self.apply_final_bilateral.get():
                step7_result = cv2.bilateralFilter(step6_result, d=9, sigmaColor=30, sigmaSpace=30)
                self.pipeline_images['step7'] = step7_result
                self.pipeline_images['step7_name'] = "Final Bilateral (9,30,30)"
            else:
                step7_result = step6_result.copy()
                self.pipeline_images['step7'] = step7_result
                self.pipeline_images['step7_name'] = "Final Bilateral (skipped)"

            # Step 8: MobileSAMv2 Segmentation
            sam_input = self._to_rgb(step7_result)
            with torch.inference_mode():
                masks = self.mask_generator.generate(sam_input)

            # Create visualization with count overlay
            sam_result = self._create_mask_overlay(sam_input, masks)
            self.pipeline_images['step8_sam'] = sam_result

            elapsed = time.perf_counter() - start_time
            self.time_label.config(text=f"Time: {elapsed:.2f}s")

            # Display final comparison
            self._display_comparison(
                self.pipeline_images['original'],
                sam_result,
                "Original (Disc)",
                f"MobileSAMv2 - Small Count: {self._small_mask_count}"
            )

            self.status_label.config(text=f"✓ {filename} - Small masks (r≤20): {self._small_mask_count}")
            self.current_step = 0
            self.next_step_btn.config(state=tk.NORMAL)
            self.reset_steps_btn.config(state=tk.DISABLED)
            self.step_info_label.config(text="Step: Ready to step through")

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
        """Reset to step 0."""
        self.current_step = 0
        if self.pipeline_images.get('original') is not None:
            self._display_comparison(
                self.pipeline_images['original'],
                self.pipeline_images.get('step8_sam'),
                "Original (Disc)",
                f"MobileSAMv2 - Small Count: {self._small_mask_count}"
            )
        self.step_info_label.config(text="Step: Ready to step through")
        self.next_step_btn.config(state=tk.NORMAL)
        self.reset_steps_btn.config(state=tk.DISABLED)

    def _display_step(self):
        """Display the current step with left/right panels."""
        step = self.current_step
        filename = os.path.basename(self.current_files[self.current_index]) if self.current_files else ""

        if step == 1:
            left_img = self.pipeline_images.get('original_unclosed')
            right_img = self.pipeline_images.get('step1_disc')
            left_label = "Step 1a: Disc Extraction"
            right_label = "Step 1b: Morph Close (k=10)"
            step_desc = f"Disc Extraction + Morph Close (center={self.disc_center}, R={self.disc_radius})"

        elif step == 2:
            left_img = self.pipeline_images.get('step1_disc')
            right_img = self.pipeline_images.get('step2_bg_norm')
            left_label = "Step 1: Disc"
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
            right_img = self.pipeline_images.get('step7')
            left_label = f"Step 6: {self.pipeline_images.get('step6_name', '')}"
            right_label = f"Step 7: {self.pipeline_images.get('step7_name', '')}"
            step_desc = self.pipeline_images.get('step7_name', '')

        elif step == 8:
            left_img = self.pipeline_images.get('step7')
            right_img = self.pipeline_images.get('step8_sam')
            left_label = f"Step 7: {self.pipeline_images.get('step7_name', '')}"
            right_label = f"Step 8: MobileSAMv2 (Small: {self._small_mask_count})"
            step_desc = "MobileSAMv2 Segmentation"

        else:
            return

        self._display_left_right(left_img, right_img, left_label, right_label)

        self.step_info_label.config(text=f"Step {step}/{self.max_steps}: {step_desc}")
        self.status_label.config(text=f"✓ {filename} - Step {step}/{self.max_steps}")

        self.next_step_btn.config(state=tk.NORMAL if step < self.max_steps else tk.DISABLED)
        self.reset_steps_btn.config(state=tk.NORMAL)

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

    def _get_mask_size(self, mask: np.ndarray) -> float:
        """
        Get the effective radius of a mask.
        Uses max(width, height) / 2 as the radius approximation.

        Args:
            mask: Binary mask (H, W)

        Returns:
            Effective radius (max dimension / 2)
        """
        mask_uint8 = mask.astype(np.uint8) * 255

        # Find contours
        contours, _ = cv2.findContours(mask_uint8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if not contours:
            return 0.0

        # Get the largest contour
        largest_contour = max(contours, key=cv2.contourArea)

        # Get bounding rect
        x, y, w, h = cv2.boundingRect(largest_contour)

        # Return max(w, h) / 2 as radius
        return max(w, h) / 2.0

    def _merge_overlapping_masks(self, masks: List[Dict[str, Any]], overlap_threshold: float = 0.75) -> List[Dict[str, Any]]:
        """
        Merge masks that have more than overlap_threshold overlap.

        Args:
            masks: List of mask dictionaries with 'segmentation' key
            overlap_threshold: If overlap ratio > this value, merge the masks (default 0.75 = 75%)

        Returns:
            List of merged mask dictionaries
        """
        if len(masks) <= 1:
            return masks

        # Calculate overlap matrix
        n = len(masks)
        merged = [False] * n
        result_masks = []

        for i in range(n):
            if merged[i]:
                continue

            # Start with mask i
            current_mask = masks[i]['segmentation'].copy()
            current_area = masks[i]['area']
            masks_to_merge = [i]

            # Find all masks that overlap > threshold with current group
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

                    # Check overlap ratio relative to smaller mask
                    min_area = min(area_i, area_j)
                    if min_area > 0:
                        overlap_ratio = intersection / min_area

                        if overlap_ratio > overlap_threshold:
                            # Merge mask j into current
                            current_mask = current_mask | mask_j
                            masks_to_merge.append(j)
                            changed = True
                            print(f"  Merging mask {j} into group starting at {i} (overlap: {overlap_ratio:.2f})")

            # Mark all merged masks
            for idx in masks_to_merge:
                merged[idx] = idx

            # Create merged mask entry
            merged_area = int(current_mask.sum())
            merged_bbox = self._mask_to_bbox(current_mask)

            result_masks.append({
                'segmentation': current_mask,
                'area': merged_area,
                'bbox': merged_bbox,
                'predicted_iou': masks[i].get('predicted_iou', 0.0),
                'stability_score': masks[i].get('stability_score', 0.0),
                'merged_from': masks_to_merge
            })

        print(f"  Merged {n} masks into {len(result_masks)} masks")
        return result_masks

    def _mask_to_bbox(self, mask: np.ndarray) -> List[int]:
        """Convert binary mask to bounding box [x, y, w, h]."""
        rows = np.any(mask, axis=1)
        cols = np.any(mask, axis=0)
        if not rows.any() or not cols.any():
            return [0, 0, 0, 0]
        y1, y2 = np.where(rows)[0][[0, -1]]
        x1, x2 = np.where(cols)[0][[0, -1]]
        return [int(x1), int(y1), int(x2 - x1), int(y2 - y1)]

    def _create_mask_overlay(self, image: np.ndarray, masks: List[Dict[str, Any]]) -> np.ndarray:
        """
        Create mask overlay visualization.
        Shows ALL masks in different colors.
        Displays count of masks with radius <= 20.

        Args:
            image: Input image
            masks: List of mask dictionaries from SAM

        Returns:
            Visualization image with overlay and count text
        """
        print(f"\n[DEBUG] Total masks received: {len(masks)}")

        result = image.copy().astype(np.float32)

        if not masks:
            self._last_masks = []
            self._small_mask_count = 0
            return result.astype(np.uint8)

        # Merge overlapping masks (>75% overlap)
        print(f"\n[MERGE] Checking for overlapping masks...")
        masks = self._merge_overlapping_masks(masks, overlap_threshold=0.75)
        print(f"[DEBUG] After merging: {len(masks)} masks")

        # Calculate size for each mask
        small_count = 0
        for i, mask_data in enumerate(masks):
            radius = self._get_mask_size(mask_data['segmentation'])
            mask_data['radius'] = radius
            if radius <= SMALL_MASK_RADIUS_THRESHOLD:
                small_count += 1
                print(f"  Mask {i}: radius={radius:.2f} <= {SMALL_MASK_RADIUS_THRESHOLD} (SMALL)")
            else:
                print(f"  Mask {i}: radius={radius:.2f} > {SMALL_MASK_RADIUS_THRESHOLD}")

        self._last_masks = masks
        self._small_mask_count = small_count

        print(f"\n[RESULT] Small masks (radius <= {SMALL_MASK_RADIUS_THRESHOLD}): {small_count}")

        # Generate colors for each mask using golden ratio
        colors = []
        for i in range(len(masks)):
            hue = (i * 0.618033988749895) % 1.0
            h = hue * 6.0
            c = 0.8
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

            colors.append(tuple(int((v + 0.2) * 200) for v in rgb))

        # Create overlay
        overlay = np.zeros_like(result)

        for idx, mask_data in enumerate(masks):
            mask = mask_data['segmentation']
            color = colors[idx]

            for c_idx in range(3):
                overlay[:, :, c_idx] = np.where(mask, color[c_idx], overlay[:, :, c_idx])

        # Blend overlay with original image
        alpha = 0.5
        mask_present = overlay.sum(axis=2) > 0

        for c_idx in range(3):
            result[:, :, c_idx] = np.where(
                mask_present,
                result[:, :, c_idx] * (1 - alpha) + overlay[:, :, c_idx] * alpha,
                result[:, :, c_idx]
            )

        # Draw contours
        for idx, mask_data in enumerate(masks):
            mask = mask_data['segmentation'].astype(np.uint8)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            color = colors[idx]
            cv2.drawContours(result, contours, -1, color, 2)

        # Add count text to image
        result_uint8 = np.clip(result, 0, 255).astype(np.uint8)

        text = f"SMALL COUNT (r<={SMALL_MASK_RADIUS_THRESHOLD}): {small_count}"
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.6
        thickness = 2
        text_size = cv2.getTextSize(text, font, font_scale, thickness)[0]

        # Position text at top
        text_x = 5
        text_y = 20

        # Draw black background for text
        cv2.rectangle(
            result_uint8,
            (text_x - 2, text_y - text_size[1] - 4),
            (text_x + text_size[0] + 2, text_y + 4),
            (0, 0, 0),
            -1
        )

        # Draw text in green
        cv2.putText(result_uint8, text, (text_x, text_y), font, font_scale, (0, 255, 0), thickness)

        return result_uint8

    def get_small_mask_count(self) -> int:
        """
        Get the count of small masks from the last processed image.

        Returns:
            Count of masks with radius <= 20
        """
        return self._small_mask_count

    def checkBung(self, image: np.ndarray) -> Tuple[np.ndarray, int]:
        """
        Main API method for checking bung.

        This method processes an image and returns the segmentation result
        with small mask count.

        Args:
            image: RGB image as numpy array (should be the disc region)

        Returns:
            Tuple of:
                - result_image: Visualization with all masks colored and count overlay
                - small_count: Number of masks with radius <= 20

        Example:
            segmenter = BungSegmentation(root)
            result_img, count = segmenter.checkBung(disc_image)
            print(f"Detected {count} small features")
        """
        print("\n" + "=" * 70)
        print("checkBung()")
        print("=" * 70)

        # Run SAM segmentation
        with torch.inference_mode():
            masks = self.mask_generator.generate(image)

        # Create visualization
        result_image = self._create_mask_overlay(image, masks)

        # Get count
        small_count = self.get_small_mask_count()

        print(f"\n[checkBung] Result: {small_count} small features detected (radius <= {SMALL_MASK_RADIUS_THRESHOLD})")
        print("=" * 70 + "\n")

        return result_image, small_count


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
    print("BungSegmentation v0.5")
    print("Optimized for 4GB GPUs - Small feature detection")
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
    app = BungSegmentationApp(root, mobile_sam_path, yolo_path)
    root.mainloop()


if __name__ == "__main__":
    main()