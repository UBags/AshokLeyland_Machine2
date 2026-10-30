"""
TopBearingSegmenter v1.00
A GUI application for segmenting the top bearing annular ring using
MobileSAM SamPredictor with positive + negative point prompts.

Uses 4 positive points at radius R on the bearing ring (left, right, top, bottom)
and 1 negative point at center to produce an annular mask.

Left panel: Positive mask (bearing ring)
Right panel: Negative mask (everything except bearing)

Requirements:
    pip install torch torchvision
    pip install opencv-python pillow numpy
    cd MobileSAM && pip install -e .

Required Files:
    - MobileSAM/weights/mobile_sam.pt (TinyViT encoder)
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

# Import image normalization methods from shared module
from utils.ImageNormalisationWithMask import (
    rgb2gray,
    ensure_float32,
    create_annular_mask,
    extract_annular_region,
    pixBackgroundNorm_masked,
    pixContrastNorm_masked)

print("=" * 70)
print("Monkeypatching torch.load for trusted checkpoints")
print("=" * 70)

# Save the original torch.load
_original_torch_load = torch.load


def _patched_torch_load(f, map_location=None, pickle_module=None, *, weights_only=None, mmap=None, **kwargs):
    """Patched torch.load that forces weights_only=False for trusted checkpoints."""
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


# =============================================================================
# GUI APPLICATION
# =============================================================================

class TopBearingSegmenterApp:
    def __init__(self, root: tk.Tk, mobile_sam_path: str):
        self.root = root
        self.root.title("TopBearingSegmenter v1.00 — Point-Prompted SAM")
        self.root.geometry("1500x950")
        self.root.configure(bg="#2b2b2b")

        # Image processing state
        self.current_files: List[str] = []
        self.current_index: int = 0

        # Annular disc parameters
        self.annular_center = (632, 360)
        self.annular_outer_radius = 250
        self.annular_inner_radius = 0
        self.annular_cx_var = tk.StringVar(value=str(self.annular_center[0]))
        self.annular_cy_var = tk.StringVar(value=str(self.annular_center[1]))
        self.annular_inner_r_var = tk.StringVar(value=str(self.annular_inner_radius))
        self.annular_outer_r_var = tk.StringVar(value=str(self.annular_outer_radius))

        # Model state
        self.mobile_sam_path = mobile_sam_path
        self.sam_model = None
        self.sam_predictor = None
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
        self.apply_bilateral = tk.BooleanVar(value=False)

        # Gamma-first vs Bilateral-first order toggle
        self.gamma_first = tk.BooleanVar(value=False)

        # Gamma value selection
        self.gamma_values = [round(0.5 + i * 0.25, 2) for i in range(25)]
        self.gamma_var = tk.StringVar(value="4.0")

        # Precompute all gamma LUTs
        self.gamma_luts = {gamma: self._create_gamma_lut(gamma) for gamma in self.gamma_values}

        # Background Normalisation parameters
        self.bg_norm_sx_values = [10, 15, 20, 25, 30, 35, 40]
        self.bg_norm_sx_var = tk.StringVar(value="40")
        self.bg_norm_sy_values = [10, 15, 20, 25, 30, 35, 40]
        self.bg_norm_sy_var = tk.StringVar(value="40")

        # Contrast Normalisation parameters
        self.apply_contrast_norm = tk.BooleanVar(value=False)
        self.contrast_norm_sx_values = [10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60]
        self.contrast_norm_sx_var = tk.StringVar(value="10")
        self.contrast_norm_sy_values = [10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60]
        self.contrast_norm_sy_var = tk.StringVar(value="10")

        # Use Channel / Invert parameters
        self.use_channel_var = tk.StringVar(value="Blue")
        self.invert_channels_var = tk.BooleanVar(value=True)

        # Remove inner region checkbox
        self.remove_inner_region = tk.BooleanVar(value=False)

        # Point prompt radius for bearing ring
        self.prompt_radius_values = list(range(5, 121, 1))
        self.prompt_radius_var = tk.StringVar(value="40")

        # Step-by-step processing state
        self.current_step = 0
        self.max_steps = 12  # Steps 1-8 preprocessing + steps 9-12 individual SAM masks

        # Store SAM result masks
        self._positive_mask_overlay = None
        self._negative_mask_overlay = None

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

        # Prompt Radius
        ttk.Label(param_frame, text="Prompt R:", style="TLabel").pack(side=tk.LEFT, padx=(0, 2))
        self.prompt_radius_dropdown = ttk.Combobox(
            param_frame, textvariable=self.prompt_radius_var,
            values=self.prompt_radius_values, width=4, state="readonly"
        )
        self.prompt_radius_dropdown.pack(side=tk.LEFT, padx=(0, 12))

        # Remove inner region checkbox (create annular masks)
        self.annular_btn = ttk.Checkbutton(
            param_frame, text="Remove inner region",
            variable=self.remove_inner_region, style="TCheckbutton"
        )
        self.annular_btn.pack(side=tk.LEFT, padx=(0, 8))

        # Annular parameter text boxes
        ttk.Label(param_frame, text="CX:", style="TLabel").pack(side=tk.LEFT, padx=(4, 1))
        self.annular_cx_entry = ttk.Entry(param_frame, textvariable=self.annular_cx_var, width=5)
        self.annular_cx_entry.pack(side=tk.LEFT, padx=(0, 4))

        ttk.Label(param_frame, text="CY:", style="TLabel").pack(side=tk.LEFT, padx=(0, 1))
        self.annular_cy_entry = ttk.Entry(param_frame, textvariable=self.annular_cy_var, width=5)
        self.annular_cy_entry.pack(side=tk.LEFT, padx=(0, 4))

        ttk.Label(param_frame, text="InR:", style="TLabel").pack(side=tk.LEFT, padx=(0, 1))
        self.annular_inner_r_entry = ttk.Entry(param_frame, textvariable=self.annular_inner_r_var, width=5)
        self.annular_inner_r_entry.pack(side=tk.LEFT, padx=(0, 4))

        ttk.Label(param_frame, text="OutR:", style="TLabel").pack(side=tk.LEFT, padx=(0, 1))
        self.annular_outer_r_entry = ttk.Entry(param_frame, textvariable=self.annular_outer_r_var, width=5)
        self.annular_outer_r_entry.pack(side=tk.LEFT, padx=(0, 4))

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

        # Use Channel dropdown
        ttk.Label(norm_frame, text="Channel:", style="TLabel").pack(side=tk.LEFT, padx=(0, 1))
        self.use_channel_dropdown = ttk.Combobox(
            norm_frame, textvariable=self.use_channel_var,
            values=["All channels", "Red", "Green", "Blue"], width=11, state="readonly"
        )
        self.use_channel_dropdown.pack(side=tk.LEFT, padx=(0, 10))

        # Invert channels checkbox
        self.invert_channels_btn = ttk.Checkbutton(
            norm_frame, text="Invert",
            variable=self.invert_channels_var, style="TCheckbutton"
        )
        self.invert_channels_btn.pack(side=tk.LEFT, padx=(0, 15))

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

    # =========================================================================
    # Model loading
    # =========================================================================

    def _update_gpu_info(self):
        """Update GPU memory info."""
        if torch.cuda.is_available():
            allocated = torch.cuda.memory_allocated() / 1024 ** 3
            total = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
            self.gpu_label.config(text=f"GPU: {allocated:.2f}GB / {total:.1f}GB")
        else:
            self.gpu_label.config(text="GPU: N/A (CPU)")

    def _load_model(self):
        """Load the MobileSAM model (no YOLO needed for point prompting)."""
        self.progress.start()
        self.status_label.config(text="Loading MobileSAM model...")
        self.root.update()

        try:
            if not os.path.exists(self.mobile_sam_path):
                raise FileNotFoundError(f"MobileSAM weights not found: {self.mobile_sam_path}")

            print(f"Loading MobileSAM from: {self.mobile_sam_path}")
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

            # Create SAM predictor
            self.sam_predictor = SamPredictor(self.sam_model)

            self.progress.stop()
            self.progress.pack_forget()
            self.status_label.config(text="✓ MobileSAM loaded — Ready for point prompting")
            self.choose_btn.config(state=tk.NORMAL)
            self._update_gpu_info()
            print("MobileSAM model loaded successfully!")

        except Exception as e:
            self.progress.stop()
            self.status_label.config(text=f"Error: {str(e)[:50]}...")
            messagebox.showerror("Model Load Error", str(e))
            print(f"Error loading model: {e}")
            traceback.print_exc()

    # =========================================================================
    # File handling
    # =========================================================================

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
            self._run_full_pipeline()

    # =========================================================================
    # Image processing helpers
    # =========================================================================

    def _sync_annular_params(self):
        """Read annular parameters from text boxes and update instance variables."""
        try:
            cx = int(self.annular_cx_var.get())
            cy = int(self.annular_cy_var.get())
            self.annular_center = (cx, cy)
            self.annular_inner_radius = int(self.annular_inner_r_var.get())
            self.annular_outer_radius = int(self.annular_outer_r_var.get())
        except ValueError:
            print("[WARNING] Invalid annular parameters, keeping previous values")

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

    # =========================================================================
    # Main pipeline
    # =========================================================================

    def _run_full_pipeline(self):
        """Run the complete preprocessing + SAM point-prompt pipeline."""
        if not self.current_files:
            return

        self._sync_annular_params()

        file_path = self.current_files[self.current_index]
        filename = os.path.basename(file_path)

        self.status_label.config(text=f"Processing: {filename}")
        self.time_label.config(text="Processing...")
        self.root.update()

        try:
            start_time = time.perf_counter()

            if torch.cuda.is_available():
                torch.cuda.empty_cache()

            # Load image
            image_bgr = cv2.imread(file_path)
            if image_bgr is None:
                raise ValueError(f"Could not load image: {file_path}")

            image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
            original_size = (image_rgb.shape[1], image_rgb.shape[0])

            # Step 1: Extract annular region
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

            # Step 6: Final Bilateral (15, 30, 40)
            step6_result = self._apply_bilateral_filter(step5_result, d=15, sigmaColor=30, sigmaSpace=40)
            self.pipeline_images['step6'] = step6_result
            self.pipeline_images['step6_name'] = "Bilateral (15,30,40)"

            # Step 7: Use Channel (optional)
            channel_choice = self.use_channel_var.get()
            if channel_choice != "All channels":
                step6_rgb = self._to_rgb(step6_result)
                channel_map = {"Red": 0, "Green": 1, "Blue": 2}
                ch_idx = channel_map[channel_choice]
                single_ch = step6_rgb[:, :, ch_idx]
                step7_result = np.stack([single_ch, single_ch, single_ch], axis=-1)
                self.pipeline_images['step7'] = step7_result
                self.pipeline_images['step7_name'] = f"Channel: {channel_choice}"
            else:
                step7_result = step6_result.copy()
                self.pipeline_images['step7'] = step7_result
                self.pipeline_images['step7_name'] = "Channel: All (skipped)"

            # Step 8: Invert channels (optional)
            if self.invert_channels_var.get():
                step8_result = (255 - step7_result).astype(np.uint8)
                self.pipeline_images['step8'] = step8_result
                self.pipeline_images['step8_name'] = "Invert"
            else:
                step8_result = step7_result.copy()
                self.pipeline_images['step8'] = step8_result
                self.pipeline_images['step8_name'] = "Invert (skipped)"

            # Step 9–12: SAM Point-Prompted Segmentation (4 separate masks)
            image_for_sam = self._to_rgb(step8_result)

            # Compute prompt points: center of the cropped annular image
            h, w = image_for_sam.shape[:2]
            cx = w // 2
            cy = h // 2
            r = int(self.prompt_radius_var.get())

            # Negative point: inside the bearing ring, slightly above center
            neg_x, neg_y = cx, cy + r - 20

            # 4 positive points (one per SAM call) + shared negative point
            positive_points = [
                ("Left",   [cx - r, cy]),
                ("Right",  [cx + r, cy]),
                ("Top",    [cx, cy - r]),
                ("Bottom", [cx, cy + r]),
            ]

            print(f"[SAM] Point prompts: center=({cx},{cy}), radius={r}")
            print(f"[SAM] Negative point: ({neg_x},{neg_y})")

            individual_masks = []  # List of (name, bool_mask, score, point_coords, point_labels)
            mask_colors = [
                (0, 255, 0),     # Green  — Left
                (0, 180, 255),   # Cyan   — Right
                (255, 200, 0),   # Yellow — Top
                (255, 0, 255),   # Magenta — Bottom
            ]

            with torch.inference_mode():
                self.sam_predictor.set_image(image_for_sam)

                for i, (name, pos_pt) in enumerate(positive_points):
                    pt_coords = np.array([
                        pos_pt,            # positive point
                        [neg_x, neg_y],    # negative point
                    ], dtype=np.float32)
                    pt_labels = np.array([1, 0], dtype=np.int32)

                    masks_out, scores_out, logits_out = self.sam_predictor.predict(
                        point_coords=pt_coords,
                        point_labels=pt_labels,
                        multimask_output=True
                    )

                    best_idx = scores_out.argmax()
                    best_mask = masks_out[best_idx]
                    best_score = scores_out[best_idx]

                    print(f"[SAM] {name}: scores={scores_out}, best={best_idx} (score={best_score:.4f})")

                    individual_masks.append((name, best_mask, best_score, pt_coords, pt_labels))

            # Create individual mask overlays (steps 9–12)
            for i, (name, mask, score, pt_coords, pt_labels) in enumerate(individual_masks):
                overlay = self._create_mask_overlay(
                    image_for_sam, mask, pt_coords, pt_labels,
                    mask_color=mask_colors[i], alpha=0.45, title=name
                )
                self.pipeline_images[f'step{9+i}_mask_{name.lower()}'] = overlay
                self.pipeline_images[f'step{9+i}_name'] = f"{name} (score={score:.3f})"

            # Create combined positive overlay (all 4 masks, different colors)
            combined_positive = self._create_combined_mask_overlay(
                image_for_sam, individual_masks, mask_colors,
                neg_point=(neg_x, neg_y), alpha=0.40
            )
            self._positive_mask_overlay = combined_positive

            # Create negative overlay (inverse of union of all 4 masks)
            union_mask = np.zeros((h, w), dtype=bool)
            for _, mask, _, _, _ in individual_masks:
                union_mask |= mask

            # Collect all prompt points for drawing on negative overlay
            all_coords = []
            all_labels = []
            for _, _, _, pt_c, pt_l in individual_masks:
                for c, l in zip(pt_c, pt_l):
                    all_coords.append(c)
                    all_labels.append(l)
            all_coords = np.array(all_coords, dtype=np.float32)
            all_labels = np.array(all_labels, dtype=np.int32)

            negative_mask = ~union_mask
            negative_overlay = self._create_mask_overlay(
                image_for_sam, negative_mask, all_coords, all_labels,
                mask_color=(255, 0, 0), alpha=0.45, title="NEGATIVE"
            )
            self._negative_mask_overlay = negative_overlay

            end_time = time.perf_counter()
            processing_time = end_time - start_time

            # Display: combined positive masks (left) vs negative mask (right)
            self.current_step = 0
            avg_score = np.mean([s for _, _, s, _, _ in individual_masks])
            self._display_comparison(
                combined_positive,
                negative_overlay,
                f"4 Positive Masks (avg={avg_score:.3f})",
                "Negative Mask (union inverse)"
            )

            # Update labels
            h, w = annular_image.shape[:2]
            self.info_label.config(
                text=f"Original: {original_size[0]}×{original_size[1]} → Annular: {w}×{h}"
            )
            self.time_label.config(text=f"⏱ {processing_time:.2f}s")
            union_pixels = int(union_mask.sum())
            total_pixels = union_mask.shape[0] * union_mask.shape[1]
            scores_str = ", ".join([f"{n[0]}={s:.2f}" for n, _, s, _, _ in individual_masks])
            self.mask_label.config(text=f"Union: {union_pixels}px ({100*union_pixels/total_pixels:.1f}%) | {scores_str}")
            self.status_label.config(text=f"✓ {filename} — Complete (4 masks, avg={avg_score:.3f})")
            self.step_info_label.config(text=f"Step 12/12: SAM Result")

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

    # =========================================================================
    # Mask overlay creation
    # =========================================================================

    def _create_mask_overlay(self, image: np.ndarray, mask: np.ndarray,
                              point_coords: np.ndarray, point_labels: np.ndarray,
                              mask_color: Tuple[int, int, int] = (0, 255, 0),
                              alpha: float = 0.45, title: str = "") -> np.ndarray:
        """
        Create an overlay of the mask on the image with prompt points drawn.

        Args:
            image: RGB image (H, W, 3)
            mask: Boolean mask (H, W)
            point_coords: Nx2 array of point coordinates
            point_labels: N array of labels (1=positive, 0=negative)
            mask_color: RGB tuple for mask tint
            alpha: Transparency of mask overlay
            title: Label for printing

        Returns:
            RGB image with mask overlay and prompt points drawn
        """
        result = image.copy().astype(np.float32)

        # Apply colored mask overlay
        color_overlay = np.zeros_like(result)
        color_overlay[mask] = mask_color
        result[mask] = result[mask] * (1 - alpha) + color_overlay[mask] * alpha
        result = np.clip(result, 0, 255).astype(np.uint8)

        # Draw contour of the mask
        mask_uint8 = (mask.astype(np.uint8)) * 255
        contours, _ = cv2.findContours(mask_uint8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        # Convert to BGR for cv2 drawing, then back
        result_bgr = cv2.cvtColor(result, cv2.COLOR_RGB2BGR)
        cv2.drawContours(result_bgr, contours, -1, (255, 255, 255), 2)

        # Draw prompt points
        for i, (pt, label) in enumerate(zip(point_coords, point_labels)):
            x, y = int(pt[0]), int(pt[1])
            if label == 1:
                # Positive point: green circle with white border
                cv2.circle(result_bgr, (x, y), 6, (255, 255, 255), -1)
                cv2.circle(result_bgr, (x, y), 5, (0, 255, 0), -1)
            else:
                # Negative point: red circle with white border
                cv2.circle(result_bgr, (x, y), 6, (255, 255, 255), -1)
                cv2.circle(result_bgr, (x, y), 5, (0, 0, 255), -1)

        result = cv2.cvtColor(result_bgr, cv2.COLOR_BGR2RGB)
        return result

    def _create_combined_mask_overlay(self, image: np.ndarray,
                                       individual_masks: list,
                                       mask_colors: list,
                                       neg_point: Tuple[int, int],
                                       alpha: float = 0.40) -> np.ndarray:
        """
        Create an overlay showing all 4 positive masks in different colors,
        with all prompt points drawn.

        Args:
            image: RGB image (H, W, 3)
            individual_masks: List of (name, bool_mask, score, pt_coords, pt_labels)
            mask_colors: List of RGB tuples, one per mask
            neg_point: (x, y) of the shared negative point
            alpha: Transparency of mask overlay

        Returns:
            RGB image with all masks overlaid and prompt points drawn
        """
        result = image.copy().astype(np.float32)

        # Apply each mask with its own color
        for i, (name, mask, score, _, _) in enumerate(individual_masks):
            color = mask_colors[i]
            color_overlay = np.zeros_like(result)
            color_overlay[mask] = color
            result[mask] = result[mask] * (1 - alpha) + color_overlay[mask] * alpha

        result = np.clip(result, 0, 255).astype(np.uint8)

        # Draw contours for each mask
        result_bgr = cv2.cvtColor(result, cv2.COLOR_RGB2BGR)
        for i, (name, mask, score, _, _) in enumerate(individual_masks):
            mask_uint8 = (mask.astype(np.uint8)) * 255
            contours, _ = cv2.findContours(mask_uint8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            # Contour color matches mask color (BGR order)
            bgr_color = (mask_colors[i][2], mask_colors[i][1], mask_colors[i][0])
            cv2.drawContours(result_bgr, contours, -1, bgr_color, 2)

        # Draw positive prompt points
        for i, (name, _, _, pt_coords, pt_labels) in enumerate(individual_masks):
            pos_pt = pt_coords[0]  # First point is the positive one
            x, y = int(pos_pt[0]), int(pos_pt[1])
            bgr_color = (mask_colors[i][2], mask_colors[i][1], mask_colors[i][0])
            cv2.circle(result_bgr, (x, y), 7, (255, 255, 255), -1)
            cv2.circle(result_bgr, (x, y), 5, bgr_color, -1)
            # Label the point
            cv2.putText(result_bgr, name[0], (x + 8, y + 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)

        # Draw negative point (red)
        nx, ny = int(neg_point[0]), int(neg_point[1])
        cv2.circle(result_bgr, (nx, ny), 7, (255, 255, 255), -1)
        cv2.circle(result_bgr, (nx, ny), 5, (0, 0, 255), -1)

        result = cv2.cvtColor(result_bgr, cv2.COLOR_BGR2RGB)
        return result
    # =========================================================================

    def _on_next_step(self):
        """Advance to the next pipeline step."""
        if self.current_step < self.max_steps:
            self.current_step += 1
            self._display_step()

    def _on_reset_steps(self):
        """Reset to step 0 (before stepping through)."""
        self.current_step = 0
        if self._positive_mask_overlay is not None and self._negative_mask_overlay is not None:
            self._display_comparison(
                self._positive_mask_overlay,
                self._negative_mask_overlay,
                "4 Positive Masks (Combined)",
                "Negative Mask (union inverse)"
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
            step_desc = "Annular Disc"

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
            right_img = self.pipeline_images.get('step7')
            left_label = f"Step 6: {self.pipeline_images.get('step6_name', '')}"
            right_label = f"Step 7: {self.pipeline_images.get('step7_name', '')}"
            step_desc = self.pipeline_images.get('step7_name', '')

        elif step == 8:
            left_img = self.pipeline_images.get('step7')
            right_img = self.pipeline_images.get('step8')
            left_label = f"Step 7: {self.pipeline_images.get('step7_name', '')}"
            right_label = f"Step 8: {self.pipeline_images.get('step8_name', '')}"
            step_desc = self.pipeline_images.get('step8_name', '')

        elif step == 9:
            # Left mask
            left_img = self.pipeline_images.get('step8')
            right_img = self.pipeline_images.get('step9_mask_left')
            left_label = f"Step 8: {self.pipeline_images.get('step8_name', 'Preprocessed')}"
            right_label = f"Step 9: {self.pipeline_images.get('step9_name', 'Left Mask')}"
            step_desc = self.pipeline_images.get('step9_name', 'Left Mask')

        elif step == 10:
            # Right mask
            left_img = self.pipeline_images.get('step9_mask_left')
            right_img = self.pipeline_images.get('step10_mask_right')
            left_label = f"Step 9: {self.pipeline_images.get('step9_name', 'Left Mask')}"
            right_label = f"Step 10: {self.pipeline_images.get('step10_name', 'Right Mask')}"
            step_desc = self.pipeline_images.get('step10_name', 'Right Mask')

        elif step == 11:
            # Top mask
            left_img = self.pipeline_images.get('step10_mask_right')
            right_img = self.pipeline_images.get('step11_mask_top')
            left_label = f"Step 10: {self.pipeline_images.get('step10_name', 'Right Mask')}"
            right_label = f"Step 11: {self.pipeline_images.get('step11_name', 'Top Mask')}"
            step_desc = self.pipeline_images.get('step11_name', 'Top Mask')

        elif step == 12:
            # Bottom mask
            left_img = self.pipeline_images.get('step11_mask_top')
            right_img = self.pipeline_images.get('step12_mask_bottom')
            left_label = f"Step 11: {self.pipeline_images.get('step11_name', 'Top Mask')}"
            right_label = f"Step 12: {self.pipeline_images.get('step12_name', 'Bottom Mask')}"
            step_desc = self.pipeline_images.get('step12_name', 'Bottom Mask')

        else:
            return

        # Display images
        self._display_left_right(left_img, right_img, left_label, right_label)

        # Update step info
        self.step_info_label.config(text=f"Step {step}/{self.max_steps}: {step_desc}")
        self.status_label.config(text=f"✓ {filename} — Step {step}/{self.max_steps}")

        # Update button states
        self.next_step_btn.config(state=tk.NORMAL if step < self.max_steps else tk.DISABLED)
        self.reset_steps_btn.config(state=tk.NORMAL)

    # =========================================================================
    # Display helpers
    # =========================================================================

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


# =============================================================================
# PATH RESOLUTION
# =============================================================================

def find_mobilesam_path():
    """Find MobileSAM checkpoint path using project root."""
    mobilesam_base = os.path.join(_PROJECT_ROOT, "models", "MobileSAM")

    mobile_sam_paths = [
        os.path.join(mobilesam_base, "weights", "mobile_sam.pt"),
        "weights/mobile_sam.pt",
        "../MobileSAM/weights/mobile_sam.pt",
        "C:/MobileSAM/weights/mobile_sam.pt",
    ]

    mobile_sam_path = None
    for path in mobile_sam_paths:
        if os.path.exists(path):
            mobile_sam_path = path
            break

    # Allow command line override
    if len(sys.argv) > 1:
        mobile_sam_path = sys.argv[1]

    if mobile_sam_path is None:
        raise FileNotFoundError(
            "Could not find mobile_sam.pt. Please ensure it's in:\n"
            "  MobileSAM/weights/mobile_sam.pt\n"
            "Or pass the path as first argument."
        )

    return mobile_sam_path


# =============================================================================
# MAIN
# =============================================================================

def main():
    """Main entry point."""
    print("=" * 60)
    print("TopBearingSegmenter v1.00")
    print("Point-Prompted SAM for bearing ring segmentation")
    print("=" * 60)

    if torch.cuda.is_available():
        gpu_name = torch.cuda.get_device_name(0)
        gpu_mem = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
        print(f"GPU: {gpu_name} ({gpu_mem:.1f}GB)")
    else:
        print("WARNING: CUDA not available, using CPU")

    try:
        mobile_sam_path = find_mobilesam_path()
        print(f"MobileSAM: {mobile_sam_path}")
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
    app = TopBearingSegmenterApp(root, mobile_sam_path)
    root.mainloop()


if __name__ == "__main__":
    main()