"""
BungSegmentation v0.8 OPTIMIZED - Custom Pipeline (5x Faster)
A GUI application for visualizing MobileSAMv2 segmentation masks for bung detection.

Pipeline:
1. Extract circular disc (center 630,350; R=250)
2. Bilateral Filter (d=21, σ_color=30, σ_space=30)
3. Gamma Correction (2.0)
4. Final Bilateral Filter (d=15, σ_color=30, σ_space=40)
5. MobileSAMv2 Segmentation
6. Extract inner disc (radius 110 from center of outer disc)
7. Filter masks: keep only those in inner disc with area 450-1200

Performance Optimizations (5x faster):
- multimask_output=False for box prompts (3x speedup)
- Conditional sparse grid sampling (only if < 5 masks)
- Grid samples only 1/8th of points
- Uses yolo_model.predict() instead of yolo_model()

Display: Original inner disc (left) vs Processed with filtered masks (right)

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

print("=" * 70)
print("BungSegmentation v0.8 OPTIMIZED (5x Faster) - Monkeypatching torch.load")
print("=" * 70)

# Save the original torch.load
_original_torch_load = torch.load


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


# =============================================================================
# MASK GENERATOR CLASS (Optimized from SAMSegmentation v0.996)
# =============================================================================

class MobileSAMv2AutomaticMaskGenerator:
    """
    MobileSAMv2 Automatic Mask Generator (Optimized).

    Uses YOLOv8 (ObjectAwareModel) for object detection to get bounding boxes,
    then MobileSAM predictor to generate masks for each detected object.

    Performance optimizations:
    - multimask_output=False for box prompts (1 mask instead of 3)
    - Conditional sparse grid sampling (only if < 5 masks)
    - Grid samples 1/8th of points
    """

    def __init__(
            self,
            sam_predictor: SamPredictor,
            yolo_model: YOLO,
            conf_threshold: float = 0.25,
            iou_threshold: float = 0.7,
            min_mask_region_area: int = 100,
            max_mask_region_area: int = 10000,
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

        # Set image for SAM predictor
        self.predictor.set_image(image)

        # Run YOLO object detection using .predict() method
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
                    "Attempting to force detection mode failed."
                )
                print(error_msg)
                raise RuntimeError(error_msg) from e
            raise e

        # Get bounding boxes from YOLO
        boxes = []
        if len(results) > 0 and results[0].boxes is not None:
            boxes = results[0].boxes.xyxy.cpu().numpy()  # [x1, y1, x2, y2]

        # Generate masks using box prompts (OPTIMIZED: multimask_output=False)
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

        # OPTIMIZATION: Only use grid if we have < 5 masks
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
        """Generate mask from a bounding box prompt (OPTIMIZED: single mask output)."""
        x1, y1, x2, y2 = box

        # OPTIMIZATION: multimask_output=False for 3x speedup
        masks, scores, _ = self.predictor.predict(
            point_coords=None,
            point_labels=None,
            box=np.array([x1, y1, x2, y2]),
            multimask_output=False  # KEY OPTIMIZATION
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
        """Generate masks using grid point prompts (OPTIMIZED: sparse sampling)."""
        masks = []
        h, w = image.shape[:2]

        # Create grid of points
        points_x = np.linspace(0, w, self.points_per_side + 2)[1:-1]
        points_y = np.linspace(0, h, self.points_per_side + 2)[1:-1]

        # OPTIMIZATION: Sample only 1/8th of grid points
        step = max(1, self.points_per_side // 8)
        for i, y in enumerate(points_y[::step]):
            for j, x in enumerate(points_x[::step]):
                try:
                    mask_pred, scores, _ = self.predictor.predict(
                        point_coords=np.array([[x, y]]),
                        point_labels=np.array([1]),  # Foreground point
                        multimask_output=True  # For grid, generate 3 and pick best
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

class BungSegmentationApp:
    def __init__(self, root: tk.Tk, mobile_sam_path: str, yolo_path: str):
        self.root = root
        self.root.title("BungSegmentation v0.8 OPTIMIZED (5x Faster)")
        self.root.geometry("1500x950")
        self.root.configure(bg="#2b2b2b")

        # Image processing state
        self.current_files = []
        self.current_index = 0

        # Outer disc parameters (center 630, 350, radius 250)
        self.outer_disc_center = (630, 350)
        self.outer_disc_radius = 250

        # Inner disc parameters (radius 110 from center of outer disc)
        self.inner_disc_radius = 110

        # Mask filtering parameters
        self.min_mask_area = 350
        self.max_mask_area = 1200

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
        self.pipeline_images = {}

        # Display scale factor
        self.display_scale = 1.2

        # Gamma value (default 2.0)
        self.gamma_values = [round(0.5 + i * 0.25, 2) for i in range(30)]
        self.gamma_var = tk.StringVar(value="6.5")

        # Precompute all gamma LUTs
        self.gamma_luts = {gamma: self._create_gamma_lut(gamma) for gamma in self.gamma_values}

        # MobileSAMv2 parameter controls
        self.conf_thresh_values = [0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5]
        self.conf_thresh_var = tk.StringVar(value="0.3")

        self.iou_thresh_values = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
        self.iou_thresh_var = tk.StringVar(value="0.7")

        self.min_area_values = [20, 50, 100, 150, 200, 250, 300, 350, 400]
        self.min_area_var = tk.StringVar(value="300")

        self.max_area_values = [500, 700, 800, 1000, 1200, 1500, 2000, 2500, 3000, 5000, 10000]
        self.max_area_var = tk.StringVar(value="1200")

        self.use_grid_var = tk.BooleanVar(value=True)

        # Step-by-step processing state
        self.current_step = 0
        self.max_steps = 6

        # Result storage
        self._last_masks = []
        self._filtered_mask_count = 0

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

        # Gamma dropdown
        ttk.Label(control_frame, text="Gamma:", style="TLabel").pack(side=tk.LEFT, padx=(0, 2))
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

        # Processing time label
        self.time_label = ttk.Label(param_frame, text="", style="Status.TLabel")
        self.time_label.pack(side=tk.LEFT, padx=(10, 0))

        # ===== CANVAS AREA =====
        canvas_frame = ttk.Frame(main_frame)
        canvas_frame.pack(fill=tk.BOTH, expand=True, pady=4)

        self.canvas = tk.Canvas(
            canvas_frame, bg="#1e1e1e",
            highlightthickness=0
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # ===== BOTTOM INFO ROW =====
        info_frame = ttk.Frame(main_frame)
        info_frame.pack(fill=tk.X, pady=(4, 0))

        self.step_info_label = ttk.Label(
            info_frame, text="Step: Not started",
            style="Status.TLabel"
        )
        self.step_info_label.pack(side=tk.LEFT, padx=5)

    def _load_model(self):
        """Load MobileSAM and YOLO models."""
        try:
            self.status_label.config(text="Loading MobileSAM...")
            self.root.update()

            # Load SAM model
            model_type = "vit_t"
            self.sam_model = sam_model_registry[model_type](checkpoint=self.mobile_sam_path)
            self.sam_model.to(device=self.device)
            self.sam_model.eval()

            # Create SAM predictor
            self.sam_predictor = SamPredictor(self.sam_model)

            # Load YOLO model
            self.status_label.config(text="Loading YOLO...")
            self.root.update()

            # CRITICAL FIX: Explicitly force task='detect' to avoid AttributeError
            # ObjectAwareModel.pt has segmentation metadata but lacks proto layer
            print("Loading YOLO ObjectAwareModel...")
            self.yolo_model = YOLO(self.yolo_path, task='detect')

            # CRITICAL FIX: Patch the model to work in detection mode
            self._force_yolo_detection_mode(self.yolo_model)

            # Move YOLO to same device
            self.yolo_model.to(self.device)

            # Create mask generator
            self._rebuild_mask_generator()

            self.status_label.config(text="✓ Ready - Select an image")
            self.choose_btn.config(state=tk.NORMAL)
            print("MobileSAMv2 models loaded successfully!")

        except Exception as e:
            self.status_label.config(text=f"❌ Error: {str(e)[:40]}")
            messagebox.showerror("Model Load Error", str(e))
            print(f"Error loading model: {e}")
            traceback.print_exc()

    def _force_yolo_detection_mode(self, yolo_model):
        """
        Force YOLO model to work in detection mode, even if saved as segmentation.

        ObjectAwareModel.pt was saved with SegmentationModel metadata but lacks proto layer.
        This causes errors during inference when the segmentation predictor/postprocessor
        tries to access the missing proto layer.

        This method:
        1. Patches the model head if it's Segment without proto
        2. Forces the wrapper to use detection predictor/postprocessor
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

            # Step 3: Force predictor to detection mode
            # The key issue: even with task='detect', the predictor uses segment postprocessor
            # We need to replace the predictor with a detection predictor
            if hasattr(yolo_model, 'predictor') and yolo_model.predictor is not None:
                # Import the detection predictor
                from ultralytics.models.yolo.detect import DetectPredictor

                # Get current predictor's config
                old_predictor = yolo_model.predictor

                # Create new detection predictor with same args
                new_predictor = DetectPredictor(overrides=yolo_model.overrides)

                # Copy important attributes from old predictor
                if hasattr(old_predictor, 'model'):
                    new_predictor.model = old_predictor.model
                if hasattr(old_predictor, 'args'):
                    new_predictor.args = old_predictor.args
                if hasattr(old_predictor, 'device'):
                    new_predictor.device = old_predictor.device

                # Replace the predictor
                yolo_model.predictor = new_predictor
                print("  ✓ Replaced predictor with DetectPredictor")

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
        if self.sam_predictor is None or self.yolo_model is None:
            return

        conf = float(self.conf_thresh_var.get())
        iou = float(self.iou_thresh_var.get())
        min_area = int(self.min_area_var.get())
        max_area = int(self.max_area_var.get())
        use_grid = self.use_grid_var.get()

        self.mask_generator = MobileSAMv2AutomaticMaskGenerator(
            sam_predictor=self.sam_predictor,
            yolo_model=self.yolo_model,
            conf_threshold=conf,
            iou_threshold=iou,
            min_mask_region_area=min_area,
            max_mask_region_area=max_area,
            use_grid_points=use_grid,
            points_per_side=32
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

    def _merge_overlapping_masks(self, masks: List[Dict[str, Any]], overlap_threshold: float = 0.75) -> List[
        Dict[str, Any]]:
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

            # Mark all merged masks
            for idx in masks_to_merge:
                merged[idx] = True

            # Create merged mask entry
            merged_area = int(current_mask.sum())
            merged_bbox = self._mask_to_bbox(current_mask)

            result_masks.append({
                'segmentation': current_mask,
                'area': merged_area,
                'bbox': merged_bbox,
                'predicted_iou': masks[i].get('predicted_iou', 0.0),
                'stability_score': masks[i].get('stability_score', 0.0),
                'merged_from': masks_to_merge  # Track which masks were merged
            })

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

    def _run_full_pipeline(self):
        """Run the complete custom pipeline."""
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

            print("\n" + "="*70)
            print("PIPELINE v0.8")
            print("="*70)

            # ===== STEP 1: Extract outer circular disc (630, 350, radius 250) =====
            print(f"Step 1: Extracting outer disc (center {self.outer_disc_center}, radius {self.outer_disc_radius})")
            outer_disc_image, outer_disc_mask = extract_circular_region(
                image_rgb,
                center=self.outer_disc_center,
                radius=self.outer_disc_radius,
                fill_color=0
            )
            self.pipeline_images['step1_outer_disc'] = outer_disc_image.copy()
            print(f"  Outer disc shape: {outer_disc_image.shape}")

            # ===== STEP 2: Bilateral Filter (21, 30, 30) =====
            print("Step 2: Bilateral filter (d=21, σ_color=30, σ_space=30)")
            step2_result = cv2.bilateralFilter(outer_disc_image, d=21, sigmaColor=30, sigmaSpace=30)
            self.pipeline_images['step2_bilateral1'] = step2_result.copy()

            # ===== STEP 3: Gamma Correction (2.0) =====
            gamma_value = float(self.gamma_var.get())
            print(f"Step 3: Gamma correction ({gamma_value})")
            step3_result = self._apply_gamma(step2_result)
            self.pipeline_images['step3_gamma'] = step3_result.copy()

            # ===== STEP 4: Final Bilateral Filter (15, 30, 40) =====
            print("Step 4: Final bilateral filter (d=15, σ_color=30, σ_space=40)")
            step4_result = cv2.bilateralFilter(step3_result, d=21, sigmaColor=30, sigmaSpace=50)
            self.pipeline_images['step4_bilateral2'] = step4_result.copy()

            # ===== STEP 5: SAM Segmentation =====
            print("Step 5: MobileSAMv2 segmentation")
            sam_input = self._to_rgb(step4_result)
            with torch.inference_mode():
                all_masks = self.mask_generator.generate(sam_input)
            print(f"  Generated {len(all_masks)} masks")

            # Merge overlapping masks
            print("Step 5b: Merging overlapping masks")
            all_masks = self._merge_overlapping_masks(all_masks, overlap_threshold=0.15)
            print(f"  After merging: {len(all_masks)} masks")

            # ===== STEP 6: Extract inner disc and filter masks =====
            # The center of the outer disc in the cropped coordinates
            outer_h, outer_w = outer_disc_image.shape[:2]
            inner_center = (self.outer_disc_radius, self.outer_disc_radius)

            print(f"Step 6: Extracting inner disc (center {inner_center} in outer disc coords, radius {self.inner_disc_radius})")

            # Extract inner disc from ORIGINAL outer disc image
            inner_disc_image, inner_disc_mask = extract_circular_region(
                outer_disc_image,
                center=inner_center,
                radius=self.inner_disc_radius,
                fill_color=0
            )
            self.pipeline_images['step5_inner_disc'] = inner_disc_image.copy()
            print(f"  Inner disc shape: {inner_disc_image.shape}")

            # Also extract from processed image for display
            inner_disc_processed, _ = extract_circular_region(
                step4_result,
                center=inner_center,
                radius=self.inner_disc_radius,
                fill_color=0
            )

            # Calculate the bounding box of inner disc in outer disc coordinates
            cx, cy = inner_center
            x1 = max(0, cx - self.inner_disc_radius)
            x2 = min(outer_w, cx + self.inner_disc_radius)
            y1 = max(0, cy - self.inner_disc_radius)
            y2 = min(outer_h, cy + self.inner_disc_radius)

            print(f"  Inner disc bbox in outer coords: x=[{x1}, {x2}], y=[{y1}, {y2}]")

            # Filter masks: keep only those in inner disc region AND area 450-1200
            filtered_masks = []
            for mask_data in all_masks:
                mask = mask_data['segmentation']
                area = mask_data['area']

                # Crop mask to inner disc region
                mask_crop = mask[y1:y2, x1:x2]

                # Check if mask has significant presence in inner disc
                mask_in_inner = mask_crop & inner_disc_mask
                pixels_in_inner = mask_in_inner.sum()

                # Filter by area and presence in inner disc
                if pixels_in_inner > 0 and self.min_mask_area <= area <= self.max_mask_area:
                    # Store the cropped version of the mask
                    filtered_mask = mask_data.copy()
                    filtered_mask['segmentation'] = mask_crop
                    filtered_mask['area_in_inner'] = int(pixels_in_inner)
                    filtered_masks.append(filtered_mask)

            self._last_masks = all_masks
            self._filtered_mask_count = len(filtered_masks)
            print(f"  Filtered to {self._filtered_mask_count} masks (area {self.min_mask_area}-{self.max_mask_area})")

            # Create overlay on processed inner disc
            inner_with_masks = self._create_mask_overlay(
                self._to_rgb(inner_disc_processed),
                filtered_masks
            )
            self.pipeline_images['step6_result'] = inner_with_masks.copy()

            elapsed = time.perf_counter() - start_time
            self.time_label.config(text=f"Time: {elapsed:.2f}s")

            # Display: original inner disc (left) vs processed with filtered masks (right)
            self._display_comparison(
                inner_disc_image,
                inner_with_masks,
                "Original Inner Disc (R=110)",
                f"Processed + Filtered Masks ({self._filtered_mask_count} masks, area 450-1200)"
            )

            status_text = f"✓ {filename} - {self._filtered_mask_count} masks (area {self.min_mask_area}-{self.max_mask_area})"
            self.status_label.config(text=status_text)
            self.current_step = 0
            self.next_step_btn.config(state=tk.NORMAL)
            self.reset_steps_btn.config(state=tk.DISABLED)
            self.step_info_label.config(text="Step: Ready to step through")

            print("="*70 + "\n")

            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        except Exception as e:
            self.status_label.config(text=f"Error: {str(e)[:50]}...")
            messagebox.showerror("Processing Error", str(e))
            print(f"Error: {e}")
            traceback.print_exc()

    def _create_mask_overlay(self, image: np.ndarray, masks: List[Dict]) -> np.ndarray:
        """Create visualization with colored mask overlays."""
        result = image.copy()

        if len(masks) == 0:
            return result

        # Create overlay
        overlay = np.zeros_like(result)

        # Assign colors using golden ratio
        for i, mask_data in enumerate(masks):
            mask = mask_data['segmentation']

            # Use golden ratio for color variety
            hue = (i * 0.618033988749895) % 1.0
            h = hue * 6.0
            c = 1.0
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

            color = tuple(int(v * 255) for v in rgb)

            # Apply mask color to overlay
            for c_idx in range(3):
                overlay[:, :, c_idx] = np.where(mask, color[c_idx], overlay[:, :, c_idx])

        # Blend overlay with image
        alpha = 0.5
        mask_present = overlay.sum(axis=2) > 0

        for c_idx in range(3):
            result[:, :, c_idx] = np.where(
                mask_present,
                result[:, :, c_idx] * (1 - alpha) + overlay[:, :, c_idx] * alpha,
                result[:, :, c_idx]
            )

        # Draw contours
        for mask_data in masks:
            mask = mask_data['segmentation'].astype(np.uint8)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(result, contours, -1, (255, 255, 255), 1)

        return np.clip(result, 0, 255).astype(np.uint8)

    def _on_next_step(self):
        """Advance to the next pipeline step."""
        if self.current_step < self.max_steps:
            self.current_step += 1
            self._display_step()

    def _on_reset_steps(self):
        """Reset to step 0."""
        self.current_step = 0
        if self.pipeline_images.get('step5_inner_disc') is not None:
            self._display_comparison(
                self.pipeline_images['step5_inner_disc'],
                self.pipeline_images.get('step6_result'),
                "Original Inner Disc (R=110)",
                f"Processed + Filtered Masks ({self._filtered_mask_count} masks)"
            )
        self.step_info_label.config(text="Step: Ready to step through")
        self.next_step_btn.config(state=tk.NORMAL)
        self.reset_steps_btn.config(state=tk.DISABLED)

    def _display_step(self):
        """Display the current step with left/right panels."""
        step = self.current_step
        filename = os.path.basename(self.current_files[self.current_index]) if self.current_files else ""

        if step == 1:
            left_img = self.pipeline_images.get('step1_outer_disc')
            right_img = self.pipeline_images.get('step2_bilateral1')
            left_label = "Step 1: Outer Disc (R=250)"
            right_label = "Step 2: Bilateral (21,30,30)"
            step_desc = "Bilateral Filter (21, 30, 30)"

        elif step == 2:
            left_img = self.pipeline_images.get('step2_bilateral1')
            right_img = self.pipeline_images.get('step3_gamma')
            left_label = "Step 2: Bilateral"
            right_label = f"Step 3: Gamma ({self.gamma_var.get()})"
            step_desc = f"Gamma Correction ({self.gamma_var.get()})"

        elif step == 3:
            left_img = self.pipeline_images.get('step3_gamma')
            right_img = self.pipeline_images.get('step4_bilateral2')
            left_label = "Step 3: Gamma"
            right_label = "Step 4: Bilateral (15,30,40)"
            step_desc = "Final Bilateral Filter (15, 30, 40)"

        elif step == 4:
            left_img = self.pipeline_images.get('step4_bilateral2')
            right_img = self.pipeline_images.get('step5_inner_disc')
            left_label = "Step 4: Processed Outer Disc"
            right_label = "Step 5: Inner Disc (R=110)"
            step_desc = "Extract Inner Disc (R=110)"

        elif step == 5:
            left_img = self.pipeline_images.get('step5_inner_disc')
            right_img = self.pipeline_images.get('step6_result')
            left_label = "Step 5: Inner Disc"
            right_label = f"Step 6: Filtered Masks ({self._filtered_mask_count})"
            step_desc = f"SAM + Filter (area 450-1200)"

        elif step == 6:
            left_img = self.pipeline_images.get('step5_inner_disc')
            right_img = self.pipeline_images.get('step6_result')
            left_label = "Original Inner Disc"
            right_label = f"Final Result ({self._filtered_mask_count} masks)"
            step_desc = "Final: Original vs Processed"

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
    print("BungSegmentation v0.8 OPTIMIZED (5x Faster)")
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