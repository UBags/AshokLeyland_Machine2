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
import math
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
import traceback
import warnings
from concurrent.futures import ThreadPoolExecutor

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

MAX_ALLOWED_CENTER_PERCENT_DEVIATION =25.0
PERCENTILE_CUT_OFF_FOR_BLACK_PIXELS = 25

# ---------------------------------------------------------------------------
# Diagnostic print verbosity flags  (set True to enable)
# ---------------------------------------------------------------------------
PRINT_IMAGE_TRANSFORMATION_DETAILS    : bool = False   # Steps 1-8: annular extraction, bg norm, gamma, bilateral, contrast, channel, invert, binarisation
PRINT_RADIAL_TRACING_DETAILS          : bool = False   # Radial ray-casting that produces list_a / list_b
PRINT_CIRCLE_FITTING_DETAILS          : bool = False   # RANSAC + Kasa circle fitting
PRINT_ANCHOR_SELECTION_DETAILS        : bool = False   # Which edge (inner/outer) is chosen as anchor
PRINT_THICKNESS_DETERMINATION_DETAILS : bool = False   # Thickness scanning and percentile selection
PRINT_WINNING_BRANCH_SELECTION_DETAILS: bool = False   # Branch scoring, ranking, winner selection

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
        self.annular_outer_radius = 120
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
        self.apply_bilateral = tk.BooleanVar(value=True)

        # Gamma-first vs Bilateral-first order toggle
        self.gamma_first = tk.BooleanVar(value=True)

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
        self.apply_contrast_norm = tk.BooleanVar(value=True)
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

        # Branch scoring mode
        self.scoring_mode_var = tk.StringVar(value="InlierRatio+ArcCov")

        # Step-by-step processing state
        self.current_step = 0
        self.max_steps = 9  # Steps 1-8 preprocessing + step 9 annular detection

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
            param_frame, text="Gamma→Bilateral",
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

        # Scoring mode dropdown
        ttk.Label(param_frame, text="Score:", style="TLabel").pack(side=tk.LEFT, padx=(0, 2))
        self.scoring_mode_dropdown = ttk.Combobox(
            param_frame, textvariable=self.scoring_mode_var,
            values=["InlierRatio+ArcCov", "InlierRatio+ArcCov+Area"],
            width=22, state="readonly"
        )
        self.scoring_mode_dropdown.pack(side=tk.LEFT, padx=(0, 12))

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

    def _fit_circle_ransac(self, points: list, n_iter: int = 500,
                            inlier_thresh: float = 2.0) -> Tuple[Tuple[float, float], float, float, int]:
        """
        Fit the best circle to a set of 2D points using RANSAC.

        Maximises the number of inlier points (within inlier_thresh pixels
        of the fitted circumference).

        Args:
            points: List of (x, y) tuples
            n_iter: Number of RANSAC iterations
            inlier_thresh: Max distance from circumference to count as inlier

        Returns:
            (center_x, center_y), radius, rmse, inlier_count
            where rmse = sqrt(mean(squared distances of inliers from circumference))
        """
        if len(points) < 3:
            # Not enough points — return centroid with radius 0
            if len(points) == 0:
                return (0.0, 0.0), 0.0, float('inf'), 0
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            return (np.mean(xs), np.mean(ys)), 0.0, float('inf'), 0

        pts = np.array(points, dtype=np.float64)
        n = len(pts)
        best_center = (0.0, 0.0)
        best_radius = 0.0
        best_inliers = 0

        for _ in range(n_iter):
            # Pick 3 random points
            idx = np.random.choice(n, 3, replace=False)
            p1, p2, p3 = pts[idx[0]], pts[idx[1]], pts[idx[2]]

            # Fit circle through 3 points
            ax, ay = p1
            bx, by = p2
            ccx, cy = p3

            D = 2.0 * (ax * (by - cy) + bx * (cy - ay) + ccx * (ay - by))
            if abs(D) < 1e-10:
                continue  # Degenerate (collinear)

            ux = ((ax * ax + ay * ay) * (by - cy) +
                  (bx * bx + by * by) * (cy - ay) +
                  (ccx * ccx + cy * cy) * (ay - by)) / D
            uy = ((ax * ax + ay * ay) * (ccx - bx) +
                  (bx * bx + by * by) * (ax - ccx) +
                  (ccx * ccx + cy * cy) * (bx - ax)) / D

            r = math.sqrt((ax - ux) ** 2 + (ay - uy) ** 2)

            if r < 1.0 or r > 500.0:
                continue  # Implausible radius

            # Count inliers
            dists = np.sqrt((pts[:, 0] - ux) ** 2 + (pts[:, 1] - uy) ** 2)
            inliers = np.sum(np.abs(dists - r) <= inlier_thresh)

            if inliers > best_inliers:
                best_inliers = inliers
                best_center = (ux, uy)
                best_radius = r

        # Refine with all inliers using least-squares
        rmse = float('inf')
        final_inlier_count = best_inliers
        if best_inliers >= 3:
            dists = np.sqrt((pts[:, 0] - best_center[0]) ** 2 +
                            (pts[:, 1] - best_center[1]) ** 2)
            inlier_mask = np.abs(dists - best_radius) <= inlier_thresh
            inlier_pts = pts[inlier_mask]

            if len(inlier_pts) >= 3:
                # Algebraic circle fit on inliers (Kasa method)
                A = np.column_stack([
                    2 * inlier_pts[:, 0],
                    2 * inlier_pts[:, 1],
                    np.ones(len(inlier_pts))
                ])
                b_vec = inlier_pts[:, 0] ** 2 + inlier_pts[:, 1] ** 2
                result, _, _, _ = np.linalg.lstsq(A, b_vec, rcond=None)
                cx_fit, cy_fit = result[0], result[1]
                r_fit = math.sqrt(result[2] + cx_fit ** 2 + cy_fit ** 2)
                best_center = (cx_fit, cy_fit)
                best_radius = r_fit

            # Recount inliers after refinement
            all_dists = np.sqrt((pts[:, 0] - best_center[0]) ** 2 +
                                (pts[:, 1] - best_center[1]) ** 2)
            final_inlier_count = int(np.sum(np.abs(all_dists - best_radius) <= inlier_thresh))

            # Compute RMSE over ALL input points (not just inliers)
            residuals = all_dists - best_radius
            rmse = float(np.sqrt(np.mean(residuals ** 2)))

        if PRINT_CIRCLE_FITTING_DETAILS:
            print(f"[RANSAC] Fit: center=({best_center[0]:.1f},{best_center[1]:.1f}), "
                  f"r={best_radius:.1f}, inliers={final_inlier_count}/{n}, rmse={rmse:.3f}")

        return best_center, best_radius, rmse, final_inlier_count

    # =========================================================================
    # Arc coverage helper
    # =========================================================================

    @staticmethod
    def _compute_arc_coverage(points: list, center: Tuple[float, float],
                              n_bins: int = 36) -> float:
        """
        Compute what fraction of the full 360° arc is covered by *points*
        when viewed from *center*.

        Divides the circle into *n_bins* equal angular sectors (default 36
        = 10° each).  Returns the fraction of bins that contain at least
        one point, i.e. a value in [0.0, 1.0].
        """
        if len(points) < 1 or n_bins < 1:
            return 0.0

        cx, cy = center
        occupied = set()
        for (px, py) in points:
            angle = math.atan2(py - cy, px - cx)      # −π … +π
            # Map to [0, 2π)
            if angle < 0:
                angle += 2.0 * math.pi
            bin_idx = int(angle / (2.0 * math.pi) * n_bins) % n_bins
            occupied.add(bin_idx)

        return len(occupied) / n_bins

    # =========================================================================
    # Branch processing (for parallel gamma + centre sweep)
    # =========================================================================

    def _run_branch(self, bg_normalized: np.ndarray, annular_mask: np.ndarray,
                    original_annular: np.ndarray, gamma_value: float,
                    det_cx: int, det_cy: int, prompt_r: int,
                    trace_distance: int, cfg: Dict[str, Any]) -> Dict[str, Any]:
        """
        Run one branch of the pipeline from the gamma step onwards.

        Everything before gamma (steps 1-2) is shared.  This method applies
        gamma -> remaining steps -> radial detection and returns a result dict
        so the caller can pick the branch with the maximum area.

        All tkinter variable values are passed via the *cfg* dict so that
        no tkinter objects are accessed from worker threads (which would
        deadlock on Windows).
        """
        branch_images: Dict[str, Any] = {}

        # Unpack pre-read config (plain Python values, NOT tk vars)
        gamma_first = cfg['gamma_first']
        alter_gamma = cfg['alter_gamma']
        apply_bilateral = cfg['apply_bilateral']
        apply_contrast_norm = cfg['apply_contrast_norm']
        cn_sx = cfg['cn_sx']
        cn_sy = cfg['cn_sy']
        channel_choice = cfg['channel_choice']
        invert = cfg['invert']

        gamma_lut = self.gamma_luts[gamma_value]

        # --- Step 3 & 4: Bilateral + Gamma (order depends on toggle) ---
        if gamma_first:
            # Gamma first
            if alter_gamma:
                step3_result = gamma_lut[bg_normalized]
                branch_images['step3'] = step3_result
                branch_images['step3_name'] = f"Gamma {gamma_value}"
            else:
                step3_result = bg_normalized.copy()
                branch_images['step3'] = step3_result
                branch_images['step3_name'] = "Gamma (skipped)"

            if apply_bilateral:
                step4_result = cv2.bilateralFilter(step3_result, d=21, sigmaColor=30, sigmaSpace=30)
                branch_images['step4'] = step4_result
                branch_images['step4_name'] = "Bilateral (21,30,30)"
            else:
                step4_result = step3_result.copy()
                branch_images['step4'] = step4_result
                branch_images['step4_name'] = "Bilateral (skipped)"
        else:
            # Bilateral first
            if apply_bilateral:
                step3_result = cv2.bilateralFilter(bg_normalized, d=21, sigmaColor=30, sigmaSpace=30)
                branch_images['step3'] = step3_result
                branch_images['step3_name'] = "Bilateral (21,30,30)"
            else:
                step3_result = bg_normalized.copy()
                branch_images['step3'] = step3_result
                branch_images['step3_name'] = "Bilateral (skipped)"

            if alter_gamma:
                step4_result = gamma_lut[step3_result]
                branch_images['step4'] = step4_result
                branch_images['step4_name'] = f"Gamma {gamma_value}"
            else:
                step4_result = step3_result.copy()
                branch_images['step4'] = step4_result
                branch_images['step4_name'] = "Gamma (skipped)"

        if PRINT_IMAGE_TRANSFORMATION_DETAILS:
            print(f"[Branch g={gamma_value} c=({det_cx},{det_cy})] "
                  f"Step3: {branch_images['step3_name']}  |  Step4: {branch_images['step4_name']}")

        # --- Step 5: Contrast Normalisation ---
        if apply_contrast_norm:
            gray_image = self._to_gray(step4_result)
            step5_result = pixContrastNorm_masked(gray_image, annular_mask, sx=cn_sx, sy=cn_sy)
            branch_images['step5'] = step5_result
            branch_images['step5_name'] = f"ContrastNorm (sx={cn_sx}, sy={cn_sy})"
        else:
            step5_result = self._to_gray(step4_result) if step4_result.ndim == 3 else step4_result.copy()
            branch_images['step5'] = step5_result
            branch_images['step5_name'] = "ContrastNorm (skipped)"

        # --- Step 6: Final Bilateral ---
        step6_result = cv2.bilateralFilter(step5_result, d=15, sigmaColor=30, sigmaSpace=40)
        branch_images['step6'] = step6_result
        branch_images['step6_name'] = "Bilateral (15,30,40)"

        if PRINT_IMAGE_TRANSFORMATION_DETAILS:
            print(f"[Branch g={gamma_value} c=({det_cx},{det_cy})] "
                  f"Step5: {branch_images['step5_name']}  |  Step6: {branch_images['step6_name']}")

        # --- Step 7: Use Channel ---
        if channel_choice != "All channels":
            step6_rgb = self._to_rgb(step6_result)
            channel_map = {"Red": 0, "Green": 1, "Blue": 2}
            ch_idx = channel_map[channel_choice]
            single_ch = step6_rgb[:, :, ch_idx]
            step7_result = np.stack([single_ch, single_ch, single_ch], axis=-1)
            branch_images['step7'] = step7_result
            branch_images['step7_name'] = f"Channel: {channel_choice}"
        else:
            step7_result = step6_result.copy()
            branch_images['step7'] = step7_result
            branch_images['step7_name'] = "Channel: All (skipped)"

        # --- Step 8: Invert channels ---
        if invert:
            step8_result = (255 - step7_result).astype(np.uint8)
            branch_images['step8'] = step8_result
            branch_images['step8_name'] = "Invert"
        else:
            step8_result = step7_result.copy()
            branch_images['step8'] = step8_result
            branch_images['step8_name'] = "Invert (skipped)"

        if PRINT_IMAGE_TRANSFORMATION_DETAILS:
            print(f"[Branch g={gamma_value} c=({det_cx},{det_cy})] "
                  f"Step7: {branch_images['step7_name']}  |  Step8: {branch_images['step8_name']}")

        # =================================================================
        # Step 9: Annular ring detection via radial tracing
        # =================================================================
        image_for_detection = self._to_rgb(step8_result)
        h_det, w_det = image_for_detection.shape[:2]
        cx, cy = det_cx, det_cy

        gray_det = cv2.cvtColor(image_for_detection, cv2.COLOR_RGB2GRAY)

        # Morphological open (circular 5x5 kernel) BEFORE binarisation
        morph_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        gray_det = cv2.morphologyEx(gray_det, cv2.MORPH_OPEN, morph_kernel)

        detection_radius = 60
        det_mask = np.zeros((h_det, w_det), dtype=np.uint8)
        cv2.circle(det_mask, (cx, cy), detection_radius, 255, -1)

        masked_pixels = gray_det[(det_mask == 255) & (gray_det < 250)]
        if len(masked_pixels) > 0:
            threshold_value = int(np.percentile(masked_pixels, PERCENTILE_CUT_OFF_FOR_BLACK_PIXELS))
        else:
            threshold_value = 128

        binary_img = np.zeros_like(gray_det)
        binary_img[gray_det >= threshold_value] = 255
        binary_img[gray_det < threshold_value] = 0

        # Morphological open (circular 5x5 kernel) AFTER binarisation
        binary_img = cv2.morphologyEx(binary_img, cv2.MORPH_OPEN, morph_kernel)

        branch_images['step9_binary'] = binary_img.copy()
        branch_images['step9_name'] = f"Binary (thresh={threshold_value})"

        if PRINT_IMAGE_TRANSFORMATION_DETAILS:
            white_pct = np.sum(binary_img == 255) / max(binary_img.size, 1) * 100
            print(f"[Branch g={gamma_value} c=({det_cx},{det_cy})] "
                  f"Binarisation: thresh={threshold_value}, white={white_pct:.1f}%")

        # --- Radial tracing with modified start and guard ---
        list_a = []
        list_b = []

        # Separate validation ranges for inner vs outer edge.
        # The outer edge is always further from the centre than the inner
        # edge, so it needs a wider ceiling.  With prompt_r sitting roughly
        # in the middle of the ring, the inner edge is at ~prompt_r-5 and
        # the outer edge at ~prompt_r+10..15, hence the 1.60 multiplier.
        min_valid_dist_inner = prompt_r * 0.65
        max_valid_dist_inner = prompt_r * 1.20
        min_valid_dist_outer = prompt_r * 0.80
        max_valid_dist_outer = prompt_r * 1.60

        # Modification 1: Start scanning from prompt_r - 18
        scan_start = max(1, prompt_r - 18)
        # Modification 2: Guard — ignore black pixels closer than prompt_r - 15
        guard_distance = prompt_r - 15

        for angle_deg in range(0, 360, 2):
            angle_rad = math.radians(angle_deg)
            cos_a = math.cos(angle_rad)
            sin_a = math.sin(angle_rad)

            found_a = None
            found_b = None
            dist_a = 0
            dist_b = 0

            for dist in range(scan_start, trace_distance + 1):
                px = int(round(cx + dist * cos_a))
                py = int(round(cy + dist * sin_a))

                if px < 0 or px >= w_det or py < 0 or py >= h_det:
                    break

                pixel_val = binary_img[py, px]

                if found_a is None and pixel_val == 0:
                    if dist < guard_distance:
                        continue
                    found_a = (px, py)
                    dist_a = dist
                elif found_a is not None and found_b is None and pixel_val == 255:
                    found_b = (px, py)
                    dist_b = dist
                    break

            if found_a is not None:
                if min_valid_dist_inner <= dist_a <= max_valid_dist_inner:
                    list_a.append(found_a)

            if found_b is not None:
                if min_valid_dist_outer <= dist_b <= max_valid_dist_outer:
                    list_b.append(found_b)

        if PRINT_RADIAL_TRACING_DETAILS:
            print(f"[Branch g={gamma_value} c=({cx},{cy})] Radial tracing: "
                  f"list_a(inner)={len(list_a)} pts, list_b(outer)={len(list_b)} pts, "
                  f"scan_start={scan_start}, guard={guard_distance}, "
                  f"innerRange=[{min_valid_dist_inner:.0f},{max_valid_dist_inner:.0f}], "
                  f"outerRange=[{min_valid_dist_outer:.0f},{max_valid_dist_outer:.0f}]")

        # =====================================================================
        # Fit circles via RANSAC then refine using anchor + thickness
        # =====================================================================
        center_a, radius_a, rmse_a, inliers_a = self._fit_circle_ransac(list_a)
        center_b, radius_b, rmse_b, inliers_b = self._fit_circle_ransac(list_b)

        # --- Compute inlier ratios ---
        inlier_ratio_a = (inliers_a / len(list_a)) if len(list_a) > 0 else 0.0
        inlier_ratio_b = (inliers_b / len(list_b)) if len(list_b) > 0 else 0.0

        # --- Compute arc coverage ---
        arc_cov_a = self._compute_arc_coverage(list_a, center_a)
        arc_cov_b = self._compute_arc_coverage(list_b, center_b)

        if PRINT_CIRCLE_FITTING_DETAILS:
            print(f"[Branch g={gamma_value} c=({cx},{cy})] "
                  f"CircleA(inner): c=({center_a[0]:.1f},{center_a[1]:.1f}) r={radius_a:.1f} "
                  f"rmse={rmse_a:.3f} inlRat={inlier_ratio_a:.2f} arcCov={arc_cov_a:.2f}  |  "
                  f"CircleB(outer): c=({center_b[0]:.1f},{center_b[1]:.1f}) r={radius_b:.1f} "
                  f"rmse={rmse_b:.3f} inlRat={inlier_ratio_b:.2f} arcCov={arc_cov_b:.2f}")

        # =================================================================
        # Calculate average G value from original annular region
        # (moved here to be just after circles are determined)
        # =================================================================
        avg_inlier_ratio = (inlier_ratio_a + inlier_ratio_b) / 2.0
        avg_arc_coverage = (arc_cov_a + arc_cov_b) / 2.0

        # Calculate score_A early for validation
        score_A = 0.4 * avg_inlier_ratio + 0.6 * avg_arc_coverage

        # Calculate average G value in the detected annular region
        # Use the two circles to create a temporary mask on the original image
        temp_mask = np.zeros((h_det, w_det), dtype=np.uint8)
        cA_int = (int(round(center_a[0])), int(round(center_a[1])))
        cB_int = (int(round(center_b[0])), int(round(center_b[1])))
        rA_int = int(round(radius_a))
        rB_int = int(round(radius_b))

        # Create annular mask using both circles
        if rA_int > 0 and rB_int > 0 and rA_int != rB_int:
            r_inner_temp = min(rA_int, rB_int)
            r_outer_temp = max(rA_int, rB_int)
            # Use center_a for the mask
            cv2.circle(temp_mask, cA_int, r_outer_temp, 255, -1)
            inner_hole_temp = np.zeros((h_det, w_det), dtype=np.uint8)
            cv2.circle(inner_hole_temp, cA_int, r_inner_temp, 255, -1)
            temp_mask = temp_mask - inner_hole_temp

            ring_pixels_temp = original_annular[temp_mask > 0]
            if len(ring_pixels_temp) > 0:
                average_G = float(np.mean(ring_pixels_temp[:, 1]))  # Green channel
            else:
                average_G = 0.0
        else:
            average_G = 0.0

        if PRINT_CIRCLE_FITTING_DETAILS:
            print(f"[Branch g={gamma_value} c=({cx},{cy})] "
                  f"Early metrics: score_A={score_A:.3f}, average_G={average_G:.1f}")

        # Decide which edge was found better using the same quality metrics
        # as branch scoring: weighted combination of inlier ratio and arc
        # coverage.  Higher = better.
        edge_score_a = 0.4 * inlier_ratio_a + 0.6 * arc_cov_a
        edge_score_b = 0.4 * inlier_ratio_b + 0.6 * arc_cov_b

        # "inner" means ListA / circle_a is better; "outer" means ListB / circle_b
        if edge_score_a >= edge_score_b:
            anchor_edge = "inner"
            anchor_center = center_a
            anchor_radius = radius_a
            anchor_rmse = rmse_a
        else:
            anchor_edge = "outer"
            anchor_center = center_b
            anchor_radius = radius_b
            anchor_rmse = rmse_b

        if PRINT_ANCHOR_SELECTION_DETAILS:
            print(f"[Branch g={gamma_value} c=({cx},{cy})] Anchor edge: {anchor_edge} "
                  f"(edgeScore_a={edge_score_a:.3f} [{len(list_a)}pts], "
                  f"edgeScore_b={edge_score_b:.3f} [{len(list_b)}pts])")

        # -----------------------------------------------------------------
        # Determine annulus thickness by scanning from the anchor circle
        # -----------------------------------------------------------------
        acx, acy = anchor_center
        a_r = anchor_radius
        list_c = []  # thickness measurements

        for angle_deg in range(0, 360, 1):
            angle_rad = math.radians(angle_deg)
            cos_a = math.cos(angle_rad)
            sin_a = math.sin(angle_rad)

            # CoordinateA: where the anchor circle's circumference is
            coord_a_x = acx + a_r * cos_a
            coord_a_y = acy + a_r * sin_a

            # Traverse from CoordinateA inward or outward to find 3
            # consecutive white pixels on the binary image.
            # If anchor is OUTER edge → traverse inward  (subtract)
            # If anchor is INNER edge → traverse outward (add)
            direction = 1 if anchor_edge == "inner" else -1

            consec_white = 0
            first_white_dist = None

            for step in range(1, 60):   # max 60px search
                sx = int(round(coord_a_x + direction * step * cos_a))
                sy = int(round(coord_a_y + direction * step * sin_a))

                if sx < 0 or sx >= w_det or sy < 0 or sy >= h_det:
                    break

                if binary_img[sy, sx] == 255:
                    consec_white += 1
                    if consec_white == 1:
                        first_white_dist = step
                    if consec_white >= 3:
                        # first_white_dist is the thickness for this ray
                        list_c.append(first_white_dist)
                        break
                else:
                    consec_white = 0
                    first_white_dist = None

        if PRINT_THICKNESS_DETERMINATION_DETAILS:
            print(f"[Branch g={gamma_value} c=({cx},{cy})] ListC (thickness samples): {len(list_c)}")

        # Determine thickness from percentiles, pick closest to 14
        TARGET_THICKNESS = 14
        if len(list_c) >= 5:
            arr_c = np.array(list_c, dtype=np.float64)
            percentiles = [25, 40, 50, 60, 75, 90]
            pct_values = [float(np.percentile(arr_c, p)) for p in percentiles]
            # Pick the percentile value closest to TARGET_THICKNESS
            best_pct_idx = int(np.argmin([abs(v - TARGET_THICKNESS) for v in pct_values]))
            thickness = pct_values[best_pct_idx]
            chosen_pct = percentiles[best_pct_idx]
            if PRINT_THICKNESS_DETERMINATION_DETAILS:
                print(f"[Branch g={gamma_value} c=({cx},{cy})] Thickness percentiles: "
                      f"{dict(zip(percentiles, [f'{v:.1f}' for v in pct_values]))}")
                print(f"[Branch g={gamma_value} c=({cx},{cy})] Chosen thickness={thickness:.1f} "
                      f"(p{chosen_pct}, target={TARGET_THICKNESS})")
        else:
            # Fallback: not enough thickness samples, use 14
            thickness = float(TARGET_THICKNESS)
            chosen_pct = -1
            if PRINT_THICKNESS_DETERMINATION_DETAILS:
                print(f"[Branch g={gamma_value} c=({cx},{cy})] Too few thickness samples "
                      f"({len(list_c)}), defaulting to {TARGET_THICKNESS}")

        # Construct the final two circles from anchor + thickness
        final_center = anchor_center
        if anchor_edge == "inner":
            r_inner_final = anchor_radius
            r_outer_final = anchor_radius + thickness
        else:
            r_outer_final = anchor_radius
            r_inner_final = anchor_radius - thickness

        # Ensure inner < outer
        if r_inner_final > r_outer_final:
            r_inner_final, r_outer_final = r_outer_final, r_inner_final

        # Compute area early (before validation) so it can be used in scoring
        fcx = int(round(final_center[0]))
        fcy = int(round(final_center[1]))
        ri = int(round(r_inner_final))
        ro = int(round(r_outer_final))

        # Calculate preliminary area for score_B calculation
        prelim_valid = (r_inner_final > 0 and r_outer_final > r_inner_final
                        and len(list_a) >= 3 and len(list_b) >= 3)

        if prelim_valid:
            annular_ring_mask = np.zeros((h_det, w_det), dtype=np.uint8)
            cv2.circle(annular_ring_mask, (fcx, fcy), ro, 255, -1)
            inner_hole = np.zeros((h_det, w_det), dtype=np.uint8)
            cv2.circle(inner_hole, (fcx, fcy), ri, 255, -1)
            annular_ring_mask = annular_ring_mask - inner_hole
            ring_bool = annular_ring_mask > 0
            area_pixels = int(np.sum(ring_bool))
        else:
            ring_bool = None
            area_pixels = 0

        # Calculate score_B now that we have area_pixels
        TARGET_THICK_FOR_SCORE = 15
        ideal_area = math.pi * ((prompt_r + TARGET_THICK_FOR_SCORE) ** 2 - prompt_r ** 2)
        area_score = min(area_pixels / ideal_area, 1.0) if ideal_area > 0 else 0.0
        score_B = 0.3 * avg_inlier_ratio + 0.4 * avg_arc_coverage + 0.3 * area_score

        # =================================================================
        # NEW VALIDATION RULE: Only valid if min(score_A, score_B) > 0.65 AND average_G > 77
        # =================================================================
        lower_score = min(score_A, score_B)
        validation_passed = (lower_score > 0.65) and (average_G > 77)

        # Final validity includes both old checks and new validation rule
        annular_valid = prelim_valid and validation_passed
        validation_reason = ""

        if not prelim_valid:
            validation_reason = "Basic geometry check failed"
        elif not validation_passed:
            if lower_score <= 0.65 and average_G <= 77:
                validation_reason = f"Failed: lower_score={lower_score:.3f}<=0.65 AND avg_G={average_G:.1f}<=77"
            elif lower_score <= 0.65:
                validation_reason = f"Failed: lower_score={lower_score:.3f}<=0.65"
            elif average_G <= 77:
                validation_reason = f"Failed: avg_G={average_G:.1f}<=77"

        if PRINT_THICKNESS_DETERMINATION_DETAILS:
            print(f"[Branch g={gamma_value} c=({cx},{cy})] Final: anchor={anchor_edge}, "
                  f"center=({final_center[0]:.1f},{final_center[1]:.1f}), "
                  f"r_in={r_inner_final:.1f}, r_out={r_outer_final:.1f}, "
                  f"thickness={thickness:.1f}, score_A={score_A:.3f}, score_B={score_B:.3f}, "
                  f"lower_score={lower_score:.3f}, avg_G={average_G:.1f}, valid={annular_valid}")
            if not annular_valid:
                print(f"[Branch g={gamma_value} c=({cx},{cy})] Validation: {validation_reason}")

        # =================================================================
        # Branch quality scores
        #
        # Mode A — "InlierRatio+ArcCov":
        #   0.4 * avg_inlier_ratio + 0.6 * avg_arc_coverage
        #
        # Mode B — "InlierRatio+ArcCov+Area":
        #   0.3 * avg_inlier_ratio + 0.4 * avg_arc_coverage + 0.3 * area_score
        #   area_score = detected_area / ideal_area  (clamped to [0, 1])
        #   ideal_area = π * ((prompt_r + target_thick)² − prompt_r²)
        #
        # Note: avg_inlier_ratio, avg_arc_coverage, score_A, score_B, and area_score
        #       are already calculated earlier for validation purposes.
        # =================================================================
        scoring_mode = cfg['scoring_mode']

        if scoring_mode == "InlierRatio+ArcCov+Area":
            branch_score = score_B
        else:
            branch_score = score_A

        if not annular_valid:
            branch_score = 0.0

        if PRINT_WINNING_BRANCH_SELECTION_DETAILS:
            print(f"[Branch g={gamma_value} c=({cx},{cy})] "
                  f"ScoreA={score_A:.3f} ScoreB={score_B:.3f} "
                  f"(avgInlR={avg_inlier_ratio:.2f}, avgArcCov={avg_arc_coverage:.2f}, "
                  f"areaSc={area_score:.2f}) → using={scoring_mode} → {branch_score:.3f}")

        # Draw result overlay
        result_overlay = original_annular.copy()

        if annular_valid:
            overlay_color = np.array([0, 220, 0], dtype=np.float32)
            alpha_overlay = 0.40
            result_float = result_overlay.astype(np.float32)
            result_float[ring_bool] = (
                result_float[ring_bool] * (1 - alpha_overlay) +
                overlay_color * alpha_overlay
            )
            result_overlay = np.clip(result_float, 0, 255).astype(np.uint8)

            result_bgr = cv2.cvtColor(result_overlay, cv2.COLOR_RGB2BGR)

            # Draw inner circle in BLUE, outer circle in CYAN
            cv2.circle(result_bgr, (fcx, fcy), ri, (255, 0, 0), 2)
            cv2.circle(result_bgr, (fcx, fcy), ro, (255, 255, 0), 2)
            # Centre dot in RED
            cv2.circle(result_bgr, (fcx, fcy), 4, (0, 0, 255), -1)

            area_text = f"Area={area_pixels}px  R_in={ri} R_out={ro}"
            center_text = f"C=({fcx},{fcy}) thick={thickness:.1f} anchor={anchor_edge}"
            score_text = (f"scA={score_A:.2f} scB={score_B:.2f} lower={min(score_A, score_B):.2f} "
                          f"inlR={avg_inlier_ratio:.2f} arcCov={avg_arc_coverage:.2f} "
                          f"areaSc={area_score:.2f}")
            gamma_text = f"gamma={gamma_value} ctr=({cx},{cy}) avg_G={average_G:.1f}"

            cv2.putText(result_bgr, area_text, (10, h_det - 70),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            cv2.putText(result_bgr, center_text, (10, h_det - 50),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            cv2.putText(result_bgr, score_text, (10, h_det - 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
            cv2.putText(result_bgr, gamma_text, (10, h_det - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 1)
            cv2.putText(result_bgr, "VALID", (w_det - 80, 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

            result_overlay = cv2.cvtColor(result_bgr, cv2.COLOR_BGR2RGB)
        else:
            result_bgr = cv2.cvtColor(result_overlay, cv2.COLOR_RGB2BGR)
            # DON'T draw the circles when invalid per new requirements
            # Only show debugging info in text

            fail_text = f"INVALID: {validation_reason}"
            score_text = f"scA={score_A:.2f} scB={score_B:.2f} lower={min(score_A, score_B):.2f}"
            avg_text = f"avg_G={average_G:.1f} listA={len(list_a)} listB={len(list_b)}"
            gamma_text = f"gamma={gamma_value} ctr=({cx},{cy})"

            cv2.putText(result_bgr, fail_text, (10, h_det - 70),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
            cv2.putText(result_bgr, score_text, (10, h_det - 50),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
            cv2.putText(result_bgr, avg_text, (10, h_det - 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
            cv2.putText(result_bgr, gamma_text, (10, h_det - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 1)
            cv2.putText(result_bgr, "INVALID", (w_det - 100, 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

            result_overlay = cv2.cvtColor(result_bgr, cv2.COLOR_BGR2RGB)
            area_pixels = 0

        branch_images['step9_result'] = result_overlay.copy()

        return {
            'area': area_pixels,
            'score': branch_score,
            'score_A': score_A,
            'score_B': score_B,
            'area_score': area_score,
            'inlier_ratio_a': inlier_ratio_a,
            'inlier_ratio_b': inlier_ratio_b,
            'arc_cov_a': arc_cov_a,
            'arc_cov_b': arc_cov_b,
            'avg_inlier_ratio': avg_inlier_ratio,
            'avg_arc_coverage': avg_arc_coverage,
            'gamma': gamma_value,
            'center': (cx, cy),
            'valid': annular_valid,
            'validation_reason': validation_reason,
            'average_G': average_G,
            'lower_score': min(score_A, score_B),
            'list_a_count': len(list_a),
            'list_b_count': len(list_b),
            'center_a': center_a,
            'center_b': center_b,
            'radius_a': radius_a,
            'radius_b': radius_b,
            'rmse_a': rmse_a,
            'rmse_b': rmse_b,
            'anchor_edge': anchor_edge,
            'thickness': thickness,
            'final_center': final_center,
            'r_inner_final': r_inner_final,
            'r_outer_final': r_outer_final,
            'center_dist_pct': 0.0,  # concentric by construction
            'threshold': threshold_value,
            'pipeline_images': branch_images,
        }

    # =========================================================================
    # Main pipeline
    # =========================================================================

    def _run_full_pipeline(self):
        """
        Run the complete preprocessing + annular ring detection pipeline.

        Steps 1-2 are shared (single branch).  Then the pipeline forks into
        parallel branches: 5 gamma values x 2 centre offsets = 10 branches.
        The branch with the maximum detected area is selected as the winner.
        """
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

            # =================================================================
            # SHARED PREFIX  —  Step 1 & 2 (single branch)
            # =================================================================

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

            if PRINT_IMAGE_TRANSFORMATION_DETAILS:
                h_ann, w_ann = annular_image.shape[:2]
                print(f"[Pipeline] Step 1: Annular extraction — "
                      f"center={self.annular_center}, outerR={self.annular_outer_radius}, "
                      f"innerR={inner_rad}, crop={w_ann}x{h_ann}")

            # Step 2: Background Normalisation
            bg_sx = int(self.bg_norm_sx_var.get())
            bg_sy = int(self.bg_norm_sy_var.get())
            bg_normalized = pixBackgroundNorm_masked(
                annular_image, annular_mask, sx=bg_sx, sy=bg_sy
            )
            self.pipeline_images['step2_bg_norm'] = bg_normalized

            if PRINT_IMAGE_TRANSFORMATION_DETAILS:
                print(f"[Pipeline] Step 2: Background normalisation — sx={bg_sx}, sy={bg_sy}")

            # =================================================================
            # PRE-READ all tkinter variables into plain Python values so that
            # worker threads never touch tkinter (which deadlocks on Windows).
            # =================================================================
            cfg = {
                'gamma_first':          self.gamma_first.get(),
                'alter_gamma':          self.alter_gamma.get(),
                'apply_bilateral':      self.apply_bilateral.get(),
                'apply_contrast_norm':  self.apply_contrast_norm.get(),
                'cn_sx':                int(self.contrast_norm_sx_var.get()),
                'cn_sy':                int(self.contrast_norm_sy_var.get()),
                'channel_choice':       self.use_channel_var.get(),
                'invert':               self.invert_channels_var.get(),
                'scoring_mode':         self.scoring_mode_var.get(),
            }

            prompt_r = int(self.prompt_radius_var.get())
            detection_radius = 60
            trace_distance = max(detection_radius, self.annular_outer_radius)

            # Image centre for detection (centre of cropped annular image)
            h_det, w_det = annular_image.shape[:2]
            base_cx = w_det // 2
            base_cy = h_det // 2

            gamma_candidates = [3.0, 4.0, 5.0, 6.0, 6.5]
            centre_candidates = [
                (base_cx, base_cy),
                (base_cx, base_cy - 10),
            ]

            original_annular_copy = annular_image.copy()

            n_branches = len(gamma_candidates) * len(centre_candidates)
            if PRINT_WINNING_BRANCH_SELECTION_DETAILS:
                print(f"[Pipeline] Launching {len(gamma_candidates)}x{len(centre_candidates)} "
                      f"= {n_branches} parallel branches")

            # -----------------------------------------------------------------
            # Disable OpenCV's internal thread pool so that multiple Python
            # threads calling bilateralFilter / cvtColor etc. do not deadlock
            # against each other on Windows.
            # -----------------------------------------------------------------
            prev_cv_threads = cv2.getNumThreads()
            cv2.setNumThreads(1)

            futures = []
            with ThreadPoolExecutor(max_workers=n_branches) as executor:
                for gamma_val in gamma_candidates:
                    for (c_x, c_y) in centre_candidates:
                        future = executor.submit(
                            self._run_branch,
                            bg_normalized, annular_mask, original_annular_copy,
                            gamma_val, c_x, c_y, prompt_r, trace_distance, cfg
                        )
                        futures.append(future)

            # Restore OpenCV thread count
            cv2.setNumThreads(prev_cv_threads)

            # Collect results and find the branch with maximum area
            all_results = []
            for future in futures:
                result = future.result()
                all_results.append(result)

            # Sort by quality score descending; pick the best
            all_results.sort(key=lambda r: r['score'], reverse=True)
            best = all_results[0]

            # Log all branch results
            scoring_mode = cfg['scoring_mode']
            if PRINT_WINNING_BRANCH_SELECTION_DETAILS:
                print(f"[Pipeline] Branch results (sorted by score, mode={scoring_mode}):")
                for i, r in enumerate(all_results):
                    tag = " <<<< WINNER" if i == 0 else ""
                    val_info = f" [{r.get('validation_reason', '')}]" if not r['valid'] else ""
                    print(f"  gamma={r['gamma']}, ctr={r['center']}, "
                          f"scA={r['score_A']:.3f}, scB={r['score_B']:.3f}, lower={r.get('lower_score', 0):.3f}, "
                          f"avg_G={r.get('average_G', 0):.1f}, "
                          f"area={r['area']}, areaSc={r['area_score']:.2f}, valid={r['valid']}{val_info}, "
                          f"anchor={r['anchor_edge']}, thick={r['thickness']:.1f}, "
                          f"inlR_a={r['inlier_ratio_a']:.2f}, inlR_b={r['inlier_ratio_b']:.2f}, "
                          f"arcCov_a={r['arc_cov_a']:.2f}, arcCov_b={r['arc_cov_b']:.2f}, "
                          f"listA={r['list_a_count']}, listB={r['list_b_count']}{tag}")

            # Merge winning branch pipeline images into self.pipeline_images
            self.pipeline_images.update(best['pipeline_images'])

            # Build status message
            area_pixels = best['area']
            list_a_count = best['list_a_count']
            list_b_count = best['list_b_count']
            result_overlay = best['pipeline_images']['step9_result']

            if best['valid']:
                r_inner = int(round(best['r_inner_final']))
                r_outer = int(round(best['r_outer_final']))
                status_msg = (f"✓ {filename} — Annular ring detected "
                              f"(R_in={r_inner}, R_out={r_outer}, area={area_pixels}px, "
                              f"scA={best['score_A']:.3f} scB={best['score_B']:.3f}, "
                              f"gamma={best['gamma']}, ctr={best['center']}, "
                              f"anchor={best['anchor_edge']}, thick={best['thickness']:.1f})")
            else:
                status_msg = (f"✗ {filename} — Annular ring INVALID "
                              f"(best gamma={best['gamma']}, ctr={best['center']}, "
                              f"scA={best['score_A']:.3f} scB={best['score_B']:.3f})")

            end_time = time.perf_counter()
            processing_time = end_time - start_time

            # =================================================================
            # Display: original (left) vs winning branch result (right)
            # =================================================================
            original_annular = self.pipeline_images['step1_annular']

            self.current_step = 0
            self._display_comparison(
                original_annular,
                result_overlay,
                "Original",
                f"Annular Ring Detection (g={best['gamma']}, ctr={best['center']})"
            )

            # Update labels
            h, w = annular_image.shape[:2]
            self.info_label.config(
                text=f"Original: {original_size[0]}x{original_size[1]} -> Annular: {w}x{h}"
            )
            self.time_label.config(text=f"⏱ {processing_time:.2f}s")
            self.mask_label.config(
                text=f"Area: {area_pixels}px | ListA: {list_a_count} | ListB: {list_b_count} "
                     f"| scA={best['score_A']:.3f} scB={best['score_B']:.3f} "
                     f"areaSc={best['area_score']:.2f} "
                     f"| g={best['gamma']} anchor={best['anchor_edge']} thick={best['thickness']:.1f}"
            )
            self.status_label.config(text=status_msg)
            self.step_info_label.config(
                text=f"Step 9/9: Annular Detection (g={best['gamma']}, {best['anchor_edge']}, "
                     f"thick={best['thickness']:.1f}, scA={best['score_A']:.3f} scB={best['score_B']:.3f})"
            )

            # Enable navigation
            self.next_step_btn.config(state=tk.NORMAL)
            self.reset_steps_btn.config(state=tk.DISABLED)

            # =================================================================
            # Always print: average R, G, B within the winning annular ring
            # =================================================================
            if best['valid']:
                fc = best['final_center']
                ri_f = int(round(best['r_inner_final']))
                ro_f = int(round(best['r_outer_final']))
                fcx_f = int(round(fc[0]))
                fcy_f = int(round(fc[1]))

                h_orig, w_orig = original_annular.shape[:2]
                ring_mask_final = np.zeros((h_orig, w_orig), dtype=np.uint8)
                cv2.circle(ring_mask_final, (fcx_f, fcy_f), ro_f, 255, -1)
                inner_hole_final = np.zeros((h_orig, w_orig), dtype=np.uint8)
                cv2.circle(inner_hole_final, (fcx_f, fcy_f), ri_f, 255, -1)
                ring_mask_final = ring_mask_final - inner_hole_final

                ring_pixels = original_annular[ring_mask_final > 0]
                if len(ring_pixels) > 0:
                    avg_r = float(np.mean(ring_pixels[:, 0]))
                    avg_g = float(np.mean(ring_pixels[:, 1]))
                    avg_b = float(np.mean(ring_pixels[:, 2]))
                    print(f"{filename} : [Result]Annular ring avg colour: "
                          f"R={avg_r:.1f}  G={avg_g:.1f}  B={avg_b:.1f}  "
                          f"(area={area_pixels}px, g={best['gamma']}, "
                          f"scA={best['score_A']:.3f}, scB={best['score_B']:.3f})")
                else:
                    print(f"{filename} : [Result] Annular ring mask empty — cannot compute avg RGB")
            else:
                validation_reason = best.get('validation_reason', 'Unknown reason')
                avg_g_val = best.get('average_G', 0.0)
                lower_sc = best.get('lower_score', 0.0)
                print(f"{filename} : [Result] Annular ring INVALID — {validation_reason} "
                      f"(avg_G={avg_g_val:.1f}, lower_score={lower_sc:.3f}, "
                      f"scA={best['score_A']:.3f}, scB={best['score_B']:.3f}, "
                      f"g={best['gamma']}, ctr={best['center']})")

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
        original = self.pipeline_images.get('step1_annular')
        result = self.pipeline_images.get('step9_result')
        if original is not None and result is not None:
            self._display_comparison(
                original,
                result,
                "Original",
                "Annular Ring Detection"
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
            # Annular ring detection result
            left_img = self.pipeline_images.get('step9_binary')
            right_img = self.pipeline_images.get('step9_result')
            left_label = f"Step 9: {self.pipeline_images.get('step9_name', 'Binary')}"
            right_label = "Step 9: Annular Ring Detection"
            step_desc = "Annular Ring Detection"

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