"""
TestNutAndWasherDifferentiation - Analyze nut vs washer differentiation using gamma correction.

This program compares Top Bearing and Nut images using gamma-corrected annular masks
to determine optimal differentiation metrics.
"""
import os
from typing import Tuple
import numpy as np
import cv2
import matplotlib.pyplot as plt
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk, ImageDraw, ImageFont

# Global flag to control visualization
visualize_difference: bool = True


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


def create_annular_mask(shape: Tuple[int, int], center: Tuple[int, int],
                        outer_radius: int, inner_radius: int) -> np.ndarray:
    """
    Create an annular (ring) mask.

    Args:
        shape: (height, width) of image
        center: (cx, cy) center coordinates
        outer_radius: Outer radius of ring
        inner_radius: Inner radius of ring

    Returns:
        Boolean mask (True inside annular region)
    """
    h, w = shape
    cx, cy = center

    y, x = np.ogrid[:h, :w]
    dist = np.sqrt((x - cx)**2 + (y - cy)**2)

    mask = (dist <= outer_radius) & (dist >= inner_radius)
    return mask


class GammaLUT:
    """
    Gamma correction with precomputed lookup table for gamma = 3.0.

    Uses standard convention: output = 255 × (input/255)^(1/gamma)
    For gamma = 3.0: power = 1/3.0 ≈ 0.333, which BRIGHTENS the image.
    """

    def __init__(self, gamma: float = 3.0):
        """Precompute gamma LUT for specified gamma value."""
        self.gamma = gamma

        # Build LUT: output = 255 * (input/255)^(1/gamma)
        # For gamma = 3.0, power = 1/3.0 = 0.333 (brightens)
        power = 1.0 / gamma
        self.lut = np.array([((i / 255.0) ** power) * 255.0
                            for i in range(256)], dtype=np.uint8)

        print(f"Gamma LUT built for gamma = {gamma}")
        print(f"  Power applied: 1/{gamma} = {power:.4f}")
        print(f"  Effect: BRIGHTENS (gamma > 1.0)")
        print(f"  Sample transformations:")
        print(f"    Input 0 → Output {self.lut[0]}")
        print(f"    Input 50 → Output {self.lut[50]}")
        print(f"    Input 100 → Output {self.lut[100]}")
        print(f"    Input 150 → Output {self.lut[150]}")
        print(f"    Input 200 → Output {self.lut[200]}")
        print(f"    Input 255 → Output {self.lut[255]}")

    def apply(self, pixels: np.ndarray) -> np.ndarray:
        """
        Apply gamma correction to pixel values using LUT.

        Args:
            pixels: Array of pixel values (any shape)

        Returns:
            Gamma-corrected pixels (same shape as input)
        """
        # Ensure uint8 for LUT indexing
        if pixels.dtype != np.uint8:
            pixels = ensure_float32(pixels).astype(np.uint8)

        # Apply LUT
        return self.lut[pixels]


class TestNutAndWasherDifferentiation:
    """
    Test nut and washer differentiation using gamma-corrected annular masks.

    Compares Top Bearing and Nut images by analyzing two annular regions with different
    radii after gamma correction (gamma = 3.0 for brightening).
    """

    def __init__(self, directory: str):
        """
        Initialize the test.

        Args:
            directory: Directory containing Top Bearing and Nut images
        """
        self.directory = directory

        # Minimum difference tracking
        self.nut_diff_min = float('inf')
        self.washer_diff_min = float('inf')

        # Lists to store all differences
        self.nut_diff_list = []
        self.washer_diff_list = []

        # Alternate method tracking (color channels)
        self.nut_diff_min_alt = float('inf')
        self.washer_diff_min_alt = float('inf')
        self.nut_diff_list_alt = []
        self.washer_diff_list_alt = []

        # Geometry parameters
        self.centre = (634, 349)
        self.radius1 = 26
        self.radius2 = 34
        self.radius3 = 49

        # Initialize gamma LUT for gamma = 3.0 (brightening)
        self.gamma_lut = GammaLUT(gamma=1.5)

        # Visualization state
        self.root = None
        self.continue_inner = False
        self.continue_outer = False
        self.waiting_for_user = False

        print(f"\nTestNutAndWasherDifferentiation initialized:")
        print(f"  Directory: {directory}")
        print(f"  Centre: {self.centre}")
        print(f"  Radius 1 (inner): {self.radius1}")
        print(f"  Radius 2 (middle): {self.radius2}")
        print(f"  Radius 3 (outer): {self.radius3}")
        print(f"  Mask 1: Annulus between radius {self.radius1} and {self.radius2}")
        print(f"  Mask 2: Annulus between radius {self.radius2} and {self.radius3}")

    def process_image_pair(self, tb_file: str, nut_file: str) -> Tuple[float, float]:
        """
        Process a Top Bearing and Nut image pair.

        Args:
            tb_file: Path to Top Bearing image
            nut_file: Path to Nut image

        Returns:
            Tuple of (nut_diff, washer_diff):
                - nut_diff = nut_mask1_avg - tb_mask1_avg
                - washer_diff = tb_mask2_avg - nut_mask2_avg
        """
        # Load images
        tb_img = cv2.imread(tb_file, cv2.IMREAD_GRAYSCALE)
        nut_img = cv2.imread(nut_file, cv2.IMREAD_GRAYSCALE)

        if tb_img is None:
            raise ValueError(f"Failed to load Top Bearing image: {tb_file}")
        if nut_img is None:
            raise ValueError(f"Failed to load Nut image: {nut_file}")

        # Create masks
        # Mask 1: Annulus between radius1 and radius2
        mask1 = create_annular_mask(tb_img.shape, self.centre, self.radius2, self.radius1)

        # Mask 2: Annulus between radius2 and radius3
        mask2 = create_annular_mask(tb_img.shape, self.centre, self.radius3, self.radius2)

        # Extract pixels where mask > 0
        tb_mask1_pixels = tb_img[mask1]
        tb_mask2_pixels = tb_img[mask2]
        nut_mask1_pixels = nut_img[mask1]
        nut_mask2_pixels = nut_img[mask2]

        # Apply bilateral filter (9, 50, 50)
        tb_img_bilateral = cv2.bilateralFilter(tb_img, 5, 75, 75)
        nut_img_bilateral = cv2.bilateralFilter(nut_img, 5, 75, 75)

        # Apply CLAHE (default parameters)
        clahe = cv2.createCLAHE()
        tb_img_clahe = clahe.apply(tb_img_bilateral)
        nut_img_clahe = clahe.apply(nut_img_bilateral)

        # Extract pixels from preprocessed images
        tb_mask1_pixels = tb_img_clahe[mask1]
        tb_mask2_pixels = tb_img_clahe[mask2]
        nut_mask1_pixels = nut_img_clahe[mask1]
        nut_mask2_pixels = nut_img_clahe[mask2]

        # Apply gamma transformation
        tb_mask1_gamma = self.gamma_lut.apply(tb_mask1_pixels)
        tb_mask2_gamma = self.gamma_lut.apply(tb_mask2_pixels)
        nut_mask1_gamma = self.gamma_lut.apply(nut_mask1_pixels)
        nut_mask2_gamma = self.gamma_lut.apply(nut_mask2_pixels)

        # Calculate averages
        tb_mask1_avg = np.mean(tb_mask1_gamma)
        tb_mask2_avg = np.mean(tb_mask2_gamma)
        nut_mask1_avg = np.mean(nut_mask1_gamma)
        nut_mask2_avg = np.mean(nut_mask2_gamma)

        # Calculate differences (note the order as specified)
        nut_diff = nut_mask1_avg - tb_mask1_avg
        washer_diff = tb_mask2_avg - nut_mask2_avg

        return nut_diff, washer_diff

    def process_image_pair_alternate(self, tb_file: str, nut_file: str) -> Tuple[float, float]:
        """
        Process a Top Bearing and Nut image pair using color channels.

        For inner annulus: uses blue channel
        For outer annulus: uses red channel
        No bilateral filter, no CLAHE, no gamma correction.

        Args:
            tb_file: Path to Top Bearing image
            nut_file: Path to Nut image

        Returns:
            Tuple of (nut_diff, washer_diff):
                - nut_diff = nut_mask1_avg - tb_mask1_avg (blue channel, inner annulus)
                - washer_diff = tb_mask2_avg - nut_mask2_avg (red channel, outer annulus)
        """
        # Load images as color (BGR format in OpenCV)
        tb_img = cv2.imread(tb_file, cv2.IMREAD_COLOR)
        nut_img = cv2.imread(nut_file, cv2.IMREAD_COLOR)

        if tb_img is None:
            raise ValueError(f"Failed to load Top Bearing image: {tb_file}")
        if nut_img is None:
            raise ValueError(f"Failed to load Nut image: {nut_file}")

        # Create masks (use height, width from color image)
        # Mask 1: Annulus between radius1 and radius2 (inner annulus)
        mask1 = create_annular_mask(tb_img.shape[:2], self.centre, self.radius2, self.radius1)

        # Mask 2: Annulus between radius2 and radius3 (outer annulus)
        mask2 = create_annular_mask(tb_img.shape[:2], self.centre, self.radius3, self.radius2)

        # For inner annulus (mask1): Extract BLUE channel
        # OpenCV uses BGR format, so blue is channel 0
        tb_blue = tb_img[:, :, 0]
        nut_blue = nut_img[:, :, 0]

        tb_mask1_pixels = tb_blue[mask1]
        nut_mask1_pixels = nut_blue[mask1]

        # Calculate averages for blue channel in inner annulus
        tb_mask1_avg = np.mean(tb_mask1_pixels)
        nut_mask1_avg = np.mean(nut_mask1_pixels)

        # For outer annulus (mask2): Extract RED channel
        # OpenCV uses BGR format, so red is channel 2
        tb_red = tb_img[:, :, 2]
        nut_red = nut_img[:, :, 2]

        tb_mask2_pixels = tb_red[mask2]
        nut_mask2_pixels = nut_red[mask2]

        # Calculate averages for red channel in outer annulus
        tb_mask2_avg = np.mean(tb_mask2_pixels)
        nut_mask2_avg = np.mean(nut_mask2_pixels)

        # Calculate differences (note the order as specified)
        nut_diff = nut_mask1_avg - tb_mask1_avg
        washer_diff = tb_mask2_avg - nut_mask2_avg

        return nut_diff, washer_diff

    def create_visualization_image(self, img_gray: np.ndarray, processed: bool = False,
                                   mask1: np.ndarray = None, mask2: np.ndarray = None,
                                   mask1_avg: float = None, mask2_avg: float = None,
                                   title: str = "") -> Image.Image:
        """
        Create a visualization image - either raw or processed with gamma-corrected annular regions.

        Args:
            img_gray: Original grayscale image
            processed: If True, show gamma-corrected with masks; if False, show raw cropped
            mask1: First annular mask (only used if processed=True)
            mask2: Second annular mask (only used if processed=True)
            mask1_avg: Average value in mask1 after gamma correction (only used if processed=True)
            mask2_avg: Average value in mask2 after gamma correction (only used if processed=True)
            title: Title for the image

        Returns:
            PIL Image with visualization (600x600 pixels)
        """
        # Crop 200x200 around center
        cx, cy = self.centre
        crop_size = 200
        half_size = crop_size // 2

        x1 = max(0, cx - half_size)
        x2 = min(img_gray.shape[1], cx + half_size)
        y1 = max(0, cy - half_size)
        y2 = min(img_gray.shape[0], cy + half_size)

        cropped = img_gray[y1:y2, x1:x2]

        # Calculate center position in cropped image
        crop_cx = cx - x1
        crop_cy = cy - y1

        if not processed:
            # Show raw unprocessed image
            # Convert to RGB
            vis_img = cv2.cvtColor(cropped, cv2.COLOR_GRAY2RGB)

            # Zoom 3X using INTER_NEAREST to maintain sharpness
            zoom_factor = 3
            zoomed_h, zoomed_w = cropped.shape[0] * zoom_factor, cropped.shape[1] * zoom_factor
            vis_img = cv2.resize(vis_img, (zoomed_w, zoomed_h), interpolation=cv2.INTER_NEAREST)

            # Convert to PIL Image
            pil_img = Image.fromarray(vis_img)

            # Draw title
            draw = ImageDraw.Draw(pil_img)
            try:
                font = ImageFont.truetype("arial.ttf", 20)
            except:
                font = ImageFont.load_default()

            draw.text((10, 10), title, fill=(255, 255, 0), font=font)

        else:
            # Show processed image with gamma correction and circle boundaries
            # Apply gamma correction to the entire image
            img_gamma = self.gamma_lut.apply(img_gray)
            cropped_gamma = img_gamma[y1:y2, x1:x2]

            # Convert to RGB
            vis_img_gamma = cv2.cvtColor(cropped_gamma, cv2.COLOR_GRAY2RGB)

            # Zoom 3X BEFORE drawing circles
            zoom_factor = 3
            zoomed_h, zoomed_w = cropped_gamma.shape[0] * zoom_factor, cropped_gamma.shape[1] * zoom_factor
            vis_img_gamma = cv2.resize(vis_img_gamma, (zoomed_w, zoomed_h), interpolation=cv2.INTER_NEAREST)

            # Scale center and radii for zoomed image
            zoomed_cx = crop_cx * zoom_factor
            zoomed_cy = crop_cy * zoom_factor
            zoomed_radius1 = self.radius1 * zoom_factor
            zoomed_radius2 = self.radius2 * zoom_factor
            zoomed_radius3 = self.radius3 * zoom_factor

            # Draw circle boundaries (not filled)
            # Mask1 boundaries: radius1 (inner) and radius2 (outer) in RED
            cv2.circle(vis_img_gamma, (zoomed_cx, zoomed_cy), zoomed_radius1, (255, 0, 0), 2)  # Inner circle - RED
            cv2.circle(vis_img_gamma, (zoomed_cx, zoomed_cy), zoomed_radius2, (255, 0, 0), 2)  # Middle circle - RED

            # Mask2 boundaries: radius2 (inner) and radius3 (outer) in BLUE
            # (radius2 already drawn above, so just draw radius3)
            cv2.circle(vis_img_gamma, (zoomed_cx, zoomed_cy), zoomed_radius3, (0, 0, 255), 2)  # Outer circle - BLUE

            # Convert to PIL Image
            pil_img = Image.fromarray(vis_img_gamma)

            # Draw text on image
            draw = ImageDraw.Draw(pil_img)

            try:
                font = ImageFont.truetype("arial.ttf", 20)
                font_small = ImageFont.truetype("arial.ttf", 16)
            except:
                font = ImageFont.load_default()
                font_small = ImageFont.load_default()

            # Draw title
            draw.text((10, 10), title, fill=(255, 255, 0), font=font)

            # Draw averages
            draw.text((10, 40), f"Mask1 Avg (Red): {mask1_avg:.2f}", fill=(255, 100, 100), font=font_small)
            draw.text((10, 65), f"Mask2 Avg (Blue): {mask2_avg:.2f}", fill=(100, 100, 255), font=font_small)

        return pil_img

    def visualize_pair(self, tb_file: str, nut_file: str,
                       tb_mask1_avg: float, tb_mask2_avg: float,
                       nut_mask1_avg: float, nut_mask2_avg: float,
                       nut_diff: float, washer_diff: float,
                       tb_idx: int, tb_total: int, nut_idx: int, nut_total: int):
        """
        Show visualization window for the current image pair.

        Args:
            tb_file: Path to Top Bearing image
            nut_file: Path to Nut image
            tb_mask1_avg, tb_mask2_avg: TB mask averages
            nut_mask1_avg, nut_mask2_avg: Nut mask averages
            nut_diff: Nut difference value
            washer_diff: Washer difference value
            tb_idx, tb_total: TB file index and total
            nut_idx, nut_total: Nut file index and total
        """
        # Load images
        tb_img = cv2.imread(tb_file, cv2.IMREAD_GRAYSCALE)
        nut_img = cv2.imread(nut_file, cv2.IMREAD_GRAYSCALE)

        # Store for processing later
        self.current_tb_img = tb_img
        self.current_nut_img = nut_img
        self.current_tb_file = tb_file
        self.current_nut_file = nut_file
        self.current_tb_mask1_avg = tb_mask1_avg
        self.current_tb_mask2_avg = tb_mask2_avg
        self.current_nut_mask1_avg = nut_mask1_avg
        self.current_nut_mask2_avg = nut_mask2_avg
        self.current_nut_diff = nut_diff
        self.current_washer_diff = washer_diff
        self.current_tb_idx = tb_idx
        self.current_tb_total = tb_total
        self.current_nut_idx = nut_idx
        self.current_nut_total = nut_total

        # Create masks
        self.current_mask1 = create_annular_mask(tb_img.shape, self.centre, self.radius2, self.radius1)
        self.current_mask2 = create_annular_mask(tb_img.shape, self.centre, self.radius3, self.radius2)

        # Create or update tkinter window
        if self.root is None:
            self.root = tk.Tk()
            self.root.title("Nut and Washer Differentiation Visualization")
            self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

            # Create main frame
            main_frame = ttk.Frame(self.root, padding="10")
            main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

            # Create image labels
            self.tb_label = ttk.Label(main_frame)
            self.tb_label.grid(row=0, column=0, padx=10, pady=10)

            self.nut_label = ttk.Label(main_frame)
            self.nut_label.grid(row=0, column=1, padx=10, pady=10)

            # Create info label
            self.info_label = ttk.Label(main_frame, text="", font=("Arial", 12))
            self.info_label.grid(row=1, column=0, columnspan=2, pady=10)

            # Create button frame
            button_frame = ttk.Frame(main_frame)
            button_frame.grid(row=2, column=0, columnspan=2, pady=10)

            # Create Process button
            self.process_btn = ttk.Button(
                button_frame,
                text="Process",
                command=self.on_process
            )
            self.process_btn.grid(row=0, column=0, padx=10)

            # Create navigation buttons (initially disabled)
            self.next_inner_btn = ttk.Button(
                button_frame,
                text="Next in Inner Loop",
                command=self.on_next_inner,
                state=tk.DISABLED
            )
            self.next_inner_btn.grid(row=0, column=1, padx=10)

            self.next_outer_btn = ttk.Button(
                button_frame,
                text="Next in Outer Loop",
                command=self.on_next_outer,
                state=tk.DISABLED
            )
            self.next_outer_btn.grid(row=0, column=2, padx=10)

            self.processed_state = False

        # Reset processed state for new pair
        self.processed_state = False
        self.process_btn.config(state=tk.NORMAL)
        self.next_inner_btn.config(state=tk.DISABLED)
        self.next_outer_btn.config(state=tk.DISABLED)

        # Show raw unprocessed images
        self.update_display()

        # Reset flags and wait for user interaction
        self.continue_inner = False
        self.continue_outer = False
        self.waiting_for_user = True

        # Wait for user to click a button
        while self.waiting_for_user:
            self.root.update()
            self.root.update_idletasks()

    def update_display(self):
        """Update the display with current images (raw or processed)."""
        # Create visualization images based on processed state
        if not self.processed_state:
            # Show raw images
            tb_vis = self.create_visualization_image(
                self.current_tb_img,
                processed=False,
                title=f"Top Bearing ({self.current_tb_idx + 1}/{self.current_tb_total})"
            )
            nut_vis = self.create_visualization_image(
                self.current_nut_img,
                processed=False,
                title=f"Nut ({self.current_nut_idx + 1}/{self.current_nut_total})"
            )
            info_text = (
                f"TB File: {os.path.basename(self.current_tb_file)}\n"
                f"Nut File: {os.path.basename(self.current_nut_file)}\n"
                f"Press 'Process' to apply gamma correction and show masks"
            )
        else:
            # Show processed images
            tb_vis = self.create_visualization_image(
                self.current_tb_img,
                processed=True,
                mask1=self.current_mask1,
                mask2=self.current_mask2,
                mask1_avg=self.current_tb_mask1_avg,
                mask2_avg=self.current_tb_mask2_avg,
                title=f"Top Bearing ({self.current_tb_idx + 1}/{self.current_tb_total})"
            )
            nut_vis = self.create_visualization_image(
                self.current_nut_img,
                processed=True,
                mask1=self.current_mask1,
                mask2=self.current_mask2,
                mask1_avg=self.current_nut_mask1_avg,
                mask2_avg=self.current_nut_mask2_avg,
                title=f"Nut ({self.current_nut_idx + 1}/{self.current_nut_total})"
            )
            info_text = (
                f"Nut Difference: {self.current_nut_diff:.4f} | "
                f"Washer Difference: {self.current_washer_diff:.4f}\n"
                f"TB File: {os.path.basename(self.current_tb_file)}\n"
                f"Nut File: {os.path.basename(self.current_nut_file)}"
            )

        # Convert to PhotoImage
        self.tb_photo = ImageTk.PhotoImage(tb_vis)
        self.nut_photo = ImageTk.PhotoImage(nut_vis)

        # Update labels
        self.tb_label.configure(image=self.tb_photo)
        self.nut_label.configure(image=self.nut_photo)

        # Update info text
        self.info_label.configure(text=info_text)

    def on_process(self):
        """Handler for 'Process' button."""
        self.processed_state = True
        self.update_display()

        # Enable navigation buttons, disable process button
        self.process_btn.config(state=tk.DISABLED)
        self.next_inner_btn.config(state=tk.NORMAL)
        self.next_outer_btn.config(state=tk.NORMAL)

    def on_next_inner(self):
        """Handler for 'Next in Inner Loop' button."""
        self.continue_inner = True
        self.continue_outer = False
        self.waiting_for_user = False

    def on_next_outer(self):
        """Handler for 'Next in Outer Loop' button."""
        self.continue_inner = False
        self.continue_outer = True
        self.waiting_for_user = False

    def on_closing(self):
        """Handler for window close button."""
        if self.root:
            self.root.destroy()
            self.root = None
        self.waiting_for_user = False
        # Set flags to stop processing
        self.continue_inner = False
        self.continue_outer = False

    def print_statistics(self):
        """
        Print statistical analysis of all collected differences.
        """
        print("\n" + "=" * 80)
        print("STATISTICAL ANALYSIS")
        print("=" * 80)

        # Convert lists to numpy arrays for easier calculation
        nut_diffs = np.array(self.nut_diff_list)
        washer_diffs = np.array(self.washer_diff_list)

        print(f"\nTotal comparisons: {len(nut_diffs)}")

        # Analysis for nut differences
        print("\n" + "-" * 80)
        print("NUT DIFFERENCES (nut_mask1_avg - tb_mask1_avg)")
        print("-" * 80)

        nut_positive_count = np.sum(nut_diffs > 0)
        nut_positive_pct = (nut_positive_count / len(nut_diffs)) * 100

        print(f"a) Percentage of values > 0: {nut_positive_pct:.2f}% ({nut_positive_count}/{len(nut_diffs)})")
        print(f"b) 0.1th percentile difference: {np.percentile(nut_diffs, 0.1):.4f}")
        print(f"c) 0.5th percentile difference: {np.percentile(nut_diffs, 0.5):.4f}")
        print(f"d) 1st percentile difference: {np.percentile(nut_diffs, 1):.4f}")
        print(f"e) 2nd percentile difference: {np.percentile(nut_diffs, 2):.4f}")
        print(f"f) 5th percentile difference: {np.percentile(nut_diffs, 5):.4f}")
        print(f"g) 10th percentile difference: {np.percentile(nut_diffs, 10):.4f}")

        # Analysis for washer differences
        print("\n" + "-" * 80)
        print("WASHER DIFFERENCES (tb_mask2_avg - nut_mask2_avg)")
        print("-" * 80)

        washer_positive_count = np.sum(washer_diffs > 0)
        washer_positive_pct = (washer_positive_count / len(washer_diffs)) * 100

        print(f"a) Percentage of values > 0: {washer_positive_pct:.2f}% ({washer_positive_count}/{len(washer_diffs)})")
        print(f"b) 0.1th percentile difference: {np.percentile(washer_diffs, 0.1):.4f}")
        print(f"c) 0.5th percentile difference: {np.percentile(washer_diffs, 0.5):.4f}")
        print(f"d) 1st percentile difference: {np.percentile(washer_diffs, 1):.4f}")
        print(f"e) 2nd percentile difference: {np.percentile(washer_diffs, 2):.4f}")
        print(f"f) 5th percentile difference: {np.percentile(washer_diffs, 5):.4f}")
        print(f"g) 10th percentile difference: {np.percentile(washer_diffs, 10):.4f}")

        # Analysis for alternate method (color channels)
        if len(self.nut_diff_list_alt) > 0:
            print("\n" + "=" * 80)
            print("ALTERNATE METHOD ANALYSIS (Color Channels)")
            print("=" * 80)

            nut_diffs_alt = np.array(self.nut_diff_list_alt)
            washer_diffs_alt = np.array(self.washer_diff_list_alt)

            print(f"\nTotal comparisons: {len(nut_diffs_alt)}")

            # Analysis for nut differences (blue channel)
            print("\n" + "-" * 80)
            print("NUT DIFFERENCES - BLUE CHANNEL (nut_mask1_avg - tb_mask1_avg)")
            print("-" * 80)

            nut_positive_count_alt = np.sum(nut_diffs_alt > 0)
            nut_positive_pct_alt = (nut_positive_count_alt / len(nut_diffs_alt)) * 100

            print(f"a) Percentage of values > 0: {nut_positive_pct_alt:.2f}% ({nut_positive_count_alt}/{len(nut_diffs_alt)})")
            print(f"b) 0.1th percentile difference: {np.percentile(nut_diffs_alt, 0.1):.4f}")
            print(f"c) 0.5th percentile difference: {np.percentile(nut_diffs_alt, 0.5):.4f}")
            print(f"d) 1st percentile difference: {np.percentile(nut_diffs_alt, 1):.4f}")
            print(f"e) 2nd percentile difference: {np.percentile(nut_diffs_alt, 2):.4f}")
            print(f"f) 5th percentile difference: {np.percentile(nut_diffs_alt, 5):.4f}")
            print(f"g) 10th percentile difference: {np.percentile(nut_diffs_alt, 10):.4f}")

            # Analysis for washer differences (red channel)
            print("\n" + "-" * 80)
            print("WASHER DIFFERENCES - RED CHANNEL (tb_mask2_avg - nut_mask2_avg)")
            print("-" * 80)

            washer_positive_count_alt = np.sum(washer_diffs_alt > 0)
            washer_positive_pct_alt = (washer_positive_count_alt / len(washer_diffs_alt)) * 100

            print(f"a) Percentage of values > 0: {washer_positive_pct_alt:.2f}% ({washer_positive_count_alt}/{len(washer_diffs_alt)})")
            print(f"b) 0.1th percentile difference: {np.percentile(washer_diffs_alt, 0.1):.4f}")
            print(f"c) 0.5th percentile difference: {np.percentile(washer_diffs_alt, 0.5):.4f}")
            print(f"d) 1st percentile difference: {np.percentile(washer_diffs_alt, 1):.4f}")
            print(f"e) 2nd percentile difference: {np.percentile(washer_diffs_alt, 2):.4f}")
            print(f"f) 5th percentile difference: {np.percentile(washer_diffs_alt, 5):.4f}")
            print(f"g) 10th percentile difference: {np.percentile(washer_diffs_alt, 10):.4f}")

        print("\n" + "=" * 80)

    def run_analysis(self):
        """
        Run the full analysis on all Top Bearing and Nut image pairs.
        """
        # Get list of Top Bearing files
        all_files = os.listdir(self.directory)
        tb_files = [f for f in all_files if "Top Bearing" in f]
        nut_files = [f for f in all_files if "Nut" in f]

        # Convert to full paths
        tb_files = [os.path.join(self.directory, f) for f in tb_files]
        nut_files = [os.path.join(self.directory, f) for f in nut_files]

        print(f"\nFound {len(tb_files)} Top Bearing files")
        print(f"Found {len(nut_files)} Nut files")

        if len(tb_files) == 0:
            print("ERROR: No Top Bearing files found!")
            return
        if len(nut_files) == 0:
            print("ERROR: No Nut files found!")
            return

        print(f"\nStarting analysis with {len(nut_files)} × {len(tb_files)} = {len(nut_files) * len(tb_files)} comparisons...")
        print("=" * 80)

        # Nested loops: outer = nut_files, inner = tb_files
        for nut_idx, nut_file in enumerate(nut_files):
            print(f"\nProcessing Nut file {nut_idx + 1}/{len(nut_files)}: {os.path.basename(nut_file)}")

            for tb_idx, tb_file in enumerate(tb_files):
                try:
                    # Process the pair - need to get all values for visualization
                    tb_img = cv2.imread(tb_file, cv2.IMREAD_GRAYSCALE)
                    nut_img = cv2.imread(nut_file, cv2.IMREAD_GRAYSCALE)

                    if tb_img is None or nut_img is None:
                        print(f"  ERROR: Failed to load images")
                        continue

                    # Create masks
                    mask1 = create_annular_mask(tb_img.shape, self.centre, self.radius2, self.radius1)
                    mask2 = create_annular_mask(tb_img.shape, self.centre, self.radius3, self.radius2)

                    # Extract pixels
                    tb_mask1_pixels = tb_img[mask1]
                    tb_mask2_pixels = tb_img[mask2]
                    nut_mask1_pixels = nut_img[mask1]
                    nut_mask2_pixels = nut_img[mask2]

                    # Apply gamma transformation
                    tb_mask1_gamma = self.gamma_lut.apply(tb_mask1_pixels)
                    tb_mask2_gamma = self.gamma_lut.apply(tb_mask2_pixels)
                    nut_mask1_gamma = self.gamma_lut.apply(nut_mask1_pixels)
                    nut_mask2_gamma = self.gamma_lut.apply(nut_mask2_pixels)

                    # Calculate averages
                    tb_mask1_avg = np.mean(tb_mask1_gamma)
                    tb_mask2_avg = np.mean(tb_mask2_gamma)
                    nut_mask1_avg = np.mean(nut_mask1_gamma)
                    nut_mask2_avg = np.mean(nut_mask2_gamma)

                    # Calculate differences
                    nut_diff = nut_mask1_avg - tb_mask1_avg
                    washer_diff = tb_mask2_avg - nut_mask2_avg

                    # Store all differences
                    self.nut_diff_list.append(nut_diff)
                    self.washer_diff_list.append(washer_diff)

                    # Update minimum differences
                    if nut_diff <= self.nut_diff_min:
                        self.nut_diff_min = nut_diff

                    if washer_diff < self.washer_diff_min:
                        self.washer_diff_min = washer_diff

                    # Also process with alternate method (color channels)
                    try:
                        nut_diff_alt, washer_diff_alt = self.process_image_pair_alternate(tb_file, nut_file)

                        # Store alternate differences
                        self.nut_diff_list_alt.append(nut_diff_alt)
                        self.washer_diff_list_alt.append(washer_diff_alt)

                        # Update alternate minimum differences
                        if nut_diff_alt <= self.nut_diff_min_alt:
                            self.nut_diff_min_alt = nut_diff_alt

                        if washer_diff_alt < self.washer_diff_min_alt:
                            self.washer_diff_min_alt = washer_diff_alt
                    except Exception as e_alt:
                        print(f"  WARNING: Alternate method failed: {e_alt}")

                    # Visualize if enabled
                    if visualize_difference:
                        self.visualize_pair(
                            tb_file, nut_file,
                            tb_mask1_avg, tb_mask2_avg,
                            nut_mask1_avg, nut_mask2_avg,
                            nut_diff, washer_diff,
                            tb_idx, len(tb_files),
                            nut_idx, len(nut_files)
                        )

                        # Check if user wants to skip to next outer loop
                        if self.continue_outer:
                            print(f"  Skipping to next outer loop...")
                            break

                    # Optional: Print progress for each comparison
                    # print(f"  vs TB {tb_idx + 1}/{len(tb_files)}: nut_diff={nut_diff:.2f}, washer_diff={washer_diff:.2f}")

                except Exception as e:
                    print(f"  ERROR processing pair: {e}")
                    import traceback
                    traceback.print_exc()

            # Print current minimums at end of each outer loop iteration
            print(f"  Current nut_diff_min: {self.nut_diff_min:.4f}")
            print(f"  Current washer_diff_min: {self.washer_diff_min:.4f}")
            print(f"  Current nut_diff_min_alt (blue): {self.nut_diff_min_alt:.4f}")
            print(f"  Current washer_diff_min_alt (red): {self.washer_diff_min_alt:.4f}")

        # Clean up visualization window
        if self.root is not None:
            self.root.destroy()
            self.root = None

        print("\n" + "=" * 80)
        print("ANALYSIS COMPLETE")
        print("=" * 80)
        print(f"Final nut_diff_min: {self.nut_diff_min:.4f}")
        print(f"Final washer_diff_min: {self.washer_diff_min:.4f}")
        print(f"Final nut_diff_min_alt (blue channel): {self.nut_diff_min_alt:.4f}")
        print(f"Final washer_diff_min_alt (red channel): {self.washer_diff_min_alt:.4f}")

        # Perform statistical analysis
        self.print_statistics()


if __name__ == "__main__":
    # Example usage
    directory = "C:/AshokLeylandImages/DOSTPLUS/LHS"  # CHANGE THIS to your actual directory

    # Control visualization (set to False to run without GUI)
    visualize_difference = False  # Uncomment to disable visualization

    # Check if directory exists
    if not os.path.exists(directory):
        print(f"ERROR: Directory does not exist: {directory}")
        print("Please update the 'directory' variable to point to your image folder.")
    else:
        # Create instance and run analysis
        test = TestNutAndWasherDifferentiation(directory)
        test.run_analysis()

# "C:/AshokLeylandImages/DOST/LHS"

# ==================================

# 9, 50, 50; gamma = 1.5
#
# --------------------------------------------------------------------------------
# NUT DIFFERENCES (nut_mask1_avg - tb_mask1_avg)
# --------------------------------------------------------------------------------
# a) Percentage of values > 0: 100.00% (36818/36818)
# b) 1st percentile difference: 27.5125
# c) 5th percentile difference: 42.9396
# d) 10th percentile difference: 53.3173
#
# --------------------------------------------------------------------------------
# WASHER DIFFERENCES (tb_mask2_avg - nut_mask2_avg)
# --------------------------------------------------------------------------------
# a) Percentage of values > 0: 98.94% (36429/36818)
# b) 1st percentile difference: -0.3009
# c) 5th percentile difference: 10.4615
# d) 10th percentile difference: 16.9179
#
# ================================================================================
#
# 15, 50, 50; gamma = 1.75
#
# --------------------------------------------------------------------------------
# NUT DIFFERENCES (nut_mask1_avg - tb_mask1_avg)
# --------------------------------------------------------------------------------
# a) Percentage of values > 0: 100.00% (36818/36818)
# b) 1st percentile difference: 25.7998
# c) 5th percentile difference: 40.4813
# d) 10th percentile difference: 50.5118
#
# --------------------------------------------------------------------------------
# WASHER DIFFERENCES (tb_mask2_avg - nut_mask2_avg)
# --------------------------------------------------------------------------------
# a) Percentage of values > 0: 98.82% (36382/36818)
# b) 1st percentile difference: -1.0327
# c) 5th percentile difference: 9.6014
# d) 10th percentile difference: 15.8249
#
# ================================================================================
#
#
# 9, 30, 30; gamma = 2.5
#
#
# --------------------------------------------------------------------------------
# NUT DIFFERENCES (nut_mask1_avg - tb_mask1_avg)
# --------------------------------------------------------------------------------
# a) Percentage of values > 0: 100.00% (36818/36818)
# b) 1st percentile difference: 21.1343
# c) 5th percentile difference: 34.0050
# d) 10th percentile difference: 42.5416
#
# --------------------------------------------------------------------------------
# WASHER DIFFERENCES (tb_mask2_avg - nut_mask2_avg)
# --------------------------------------------------------------------------------
# a) Percentage of values > 0: 98.47% (36254/36818)
# b) 1st percentile difference: -2.4296
# c) 5th percentile difference: 7.0145
# d) 10th percentile difference: 12.7555
#
# ================================================================================
#
#
# 9, 75, 75; gamma = 1.5
#
#
# --------------------------------------------------------------------------------
# NUT DIFFERENCES (nut_mask1_avg - tb_mask1_avg)
# --------------------------------------------------------------------------------
# a) Percentage of values > 0: 100.00% (36818/36818)
# b) 1st percentile difference: 27.5125
# c) 5th percentile difference: 42.9396
# d) 10th percentile difference: 53.3173
#
# --------------------------------------------------------------------------------
# WASHER DIFFERENCES (tb_mask2_avg - nut_mask2_avg)
# --------------------------------------------------------------------------------
# a) Percentage of values > 0: 98.94% (36429/36818)
# b) 1st percentile difference: -0.3009
# c) 5th percentile difference: 10.4615
# d) 10th percentile difference: 16.9179
#
# ================================================================================
#
# 5, 75, 75; gamma = 1.5
#
# --------------------------------------------------------------------------------
# NUT DIFFERENCES (nut_mask1_avg - tb_mask1_avg)
# --------------------------------------------------------------------------------
# a) Percentage of values > 0: 100.00% (36818/36818)
# b) 1st percentile difference: 27.5125
# c) 5th percentile difference: 42.9396
# d) 10th percentile difference: 53.3173
#
# --------------------------------------------------------------------------------
# WASHER DIFFERENCES (tb_mask2_avg - nut_mask2_avg)
# --------------------------------------------------------------------------------
# a) Percentage of values > 0: 98.94% (36429/36818)
# b) 1st percentile difference: -0.3009
# c) 5th percentile difference: 10.4615
# d) 10th percentile difference: 16.9179
#
# ================================================================================
#
# ================================================================================
# STATISTICAL ANALYSIS
# ================================================================================
#
# Total comparisons: 36818
#
# --------------------------------------------------------------------------------
# NUT DIFFERENCES (nut_mask1_avg - tb_mask1_avg)
# --------------------------------------------------------------------------------
# a) Percentage of values > 0: 100.00% (36818/36818)
# b) 0.1th percentile difference: 15.7094
# c) 0.5th percentile difference: 23.8733
# d) 1st percentile difference: 27.5125
# e) 2nd percentile difference: 33.6028
# f) 5th percentile difference: 42.9396
# g) 10th percentile difference: 53.3173
#
# --------------------------------------------------------------------------------
# WASHER DIFFERENCES (tb_mask2_avg - nut_mask2_avg)
# --------------------------------------------------------------------------------
# a) Percentage of values > 0: 98.94% (36429/36818)
# b) 0.1th percentile difference: -11.1749
# c) 0.5th percentile difference: -3.8162
# d) 1st percentile difference: -0.3009
# e) 2nd percentile difference: 4.0004
# f) 5th percentile difference: 10.4615
# g) 10th percentile difference: 16.9179
#
# ================================================================================
# ALTERNATE METHOD ANALYSIS (Color Channels)
# ================================================================================
#
# Total comparisons: 36818
#
# --------------------------------------------------------------------------------
# NUT DIFFERENCES - BLUE CHANNEL (nut_mask1_avg - tb_mask1_avg)
# --------------------------------------------------------------------------------
# a) Percentage of values > 0: 100.00% (36818/36818)
# b) 0.1th percentile difference: 23.8938
# c) 0.5th percentile difference: 36.8398
# d) 1st percentile difference: 45.5892
# e) 2nd percentile difference: 52.4907
# f) 5th percentile difference: 64.0180
# g) 10th percentile difference: 74.6524
#
# --------------------------------------------------------------------------------
# WASHER DIFFERENCES - RED CHANNEL (tb_mask2_avg - nut_mask2_avg)
# --------------------------------------------------------------------------------
# a) Percentage of values > 0: 99.90% (36780/36818)
# b) 0.1th percentile difference: -0.1845
# c) 0.5th percentile difference: 5.7695
# d) 1st percentile difference: 8.9197
# e) 2nd percentile difference: 12.6219
# f) 5th percentile difference: 18.9468
# g) 10th percentile difference: 25.6252
#
# ================================================================================