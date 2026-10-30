# """
# Hub Surface Restoration Application
# Removes scribbles from concentric metallic hubs using Polar-Domain Filtering.
#
# Features:
# - File browser to select images from a directory
# - Next/Previous navigation through images
# - Side-by-side display of original (cropped) and processed images
# - Multi-pass MASK ACCUMULATION for better scribble detection
# - Tunable detection parameters
# """
#
# import cv2
# import numpy as np
# import tkinter as tk
# from tkinter import filedialog, ttk
# from PIL import Image, ImageTk
# import os
# import time
#
#
# class HubRestorationApp:
#     def __init__(self, root):
#         self.root = root
#         self.root.title("Hub Surface Restoration - Scribble Removal")
#         self.root.geometry("1400x850")
#         self.root.configure(bg='#2b2b2b')
#
#         # State variables
#         self.image_files = []
#         self.current_index = 0
#
#         # Processing parameters
#         self.center = (650, 345)
#         self.max_radius = 220
#         self.min_radius = 130
#
#         # Tunable detection parameters
#         self.intensity_threshold = tk.IntVar(value=25)
#         self.color_sensitivity = tk.IntVar(value=118)
#         self.min_aspect_ratio = tk.DoubleVar(value=1.5)
#         self.use_multipass = tk.BooleanVar(value=True)
#
#         # Image references
#         self.left_photo = None
#         self.right_photo = None
#
#         self.setup_ui()
#         self.bind_keys()
#
#     def setup_ui(self):
#         """Set up the user interface."""
#         style = ttk.Style()
#         style.theme_use('clam')
#
#         style.configure('TFrame', background='#2b2b2b')
#         style.configure('TButton', padding=10, font=('Helvetica', 10))
#         style.configure('TLabel', background='#2b2b2b', foreground='white', font=('Helvetica', 10))
#         style.configure('Header.TLabel', font=('Helvetica', 12, 'bold'))
#         style.configure('TLabelframe', background='#2b2b2b', foreground='white')
#         style.configure('TLabelframe.Label', background='#2b2b2b', foreground='white', font=('Helvetica', 11, 'bold'))
#         style.configure('TScale', background='#2b2b2b')
#
#         # === TOP PANEL ===
#         top_frame = ttk.Frame(self.root, padding=10)
#         top_frame.pack(side=tk.TOP, fill=tk.X)
#
#         ttk.Button(top_frame, text="📁 Open File", command=self.open_file, width=15).pack(side=tk.LEFT, padx=5)
#         ttk.Separator(top_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=15)
#         ttk.Button(top_frame, text="◀ Previous", command=self.previous_image, width=12).pack(side=tk.LEFT, padx=5)
#         ttk.Button(top_frame, text="Next ▶", command=self.next_image, width=12).pack(side=tk.LEFT, padx=5)
#         ttk.Separator(top_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=15)
#         ttk.Button(top_frame, text="💾 Save Result", command=self.save_result, width=12).pack(side=tk.LEFT, padx=5)
#
#         shortcuts_label = ttk.Label(top_frame, text="[← → to navigate, Ctrl+S to save]",
#                                      font=('Helvetica', 9), foreground='#888')
#         shortcuts_label.pack(side=tk.LEFT, padx=20)
#
#         self.counter_label = ttk.Label(top_frame, text="No files loaded", style='Header.TLabel')
#         self.counter_label.pack(side=tk.RIGHT, padx=20)
#
#         # === PARAMETER PANEL ===
#         param_frame = ttk.LabelFrame(self.root, text="Detection Parameters", padding=10)
#         param_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=5)
#
#         ttk.Label(param_frame, text="Intensity:").pack(side=tk.LEFT, padx=5)
#         ttk.Label(param_frame, text="Less").pack(side=tk.LEFT)
#         ttk.Scale(param_frame, from_=40, to=10, variable=self.intensity_threshold,
#                   orient=tk.HORIZONTAL, length=120).pack(side=tk.LEFT, padx=5)
#         ttk.Label(param_frame, text="More").pack(side=tk.LEFT, padx=(0, 15))
#
#         ttk.Label(param_frame, text="Blue:").pack(side=tk.LEFT, padx=5)
#         ttk.Label(param_frame, text="Less").pack(side=tk.LEFT)
#         ttk.Scale(param_frame, from_=125, to=105, variable=self.color_sensitivity,
#                   orient=tk.HORIZONTAL, length=120).pack(side=tk.LEFT, padx=5)
#         ttk.Label(param_frame, text="More").pack(side=tk.LEFT, padx=(0, 15))
#
#         ttk.Button(param_frame, text="🔄 Reprocess", command=self.reprocess_current, width=12).pack(side=tk.LEFT, padx=15)
#
#         ttk.Checkbutton(param_frame, text="Multi-pass (4 orientations)",
#                         variable=self.use_multipass).pack(side=tk.LEFT, padx=10)
#
#         # === MIDDLE PANEL ===
#         middle_frame = ttk.Frame(self.root, padding=10)
#         middle_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
#
#         middle_frame.columnconfigure(0, weight=1)
#         middle_frame.columnconfigure(1, weight=1)
#         middle_frame.rowconfigure(0, weight=1)
#
#         left_labelframe = ttk.LabelFrame(middle_frame, text="Original (Cropped Hub Region)", padding=5)
#         left_labelframe.grid(row=0, column=0, sticky='nsew', padx=5, pady=5)
#         self.left_canvas = tk.Canvas(left_labelframe, bg='#1a1a1a', highlightthickness=1, highlightbackground='#444')
#         self.left_canvas.pack(fill=tk.BOTH, expand=True)
#
#         right_labelframe = ttk.LabelFrame(middle_frame, text="Processed (Scribbles Removed)", padding=5)
#         right_labelframe.grid(row=0, column=1, sticky='nsew', padx=5, pady=5)
#         self.right_canvas = tk.Canvas(right_labelframe, bg='#1a1a1a', highlightthickness=1, highlightbackground='#444')
#         self.right_canvas.pack(fill=tk.BOTH, expand=True)
#
#         # === BOTTOM PANEL ===
#         bottom_frame = ttk.Frame(self.root, padding=10)
#         bottom_frame.pack(side=tk.BOTTOM, fill=tk.X)
#
#         self.filename_label = ttk.Label(bottom_frame, text="File: None", anchor='w')
#         self.filename_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
#
#         self.time_label = ttk.Label(bottom_frame, text="Processing time: N/A", anchor='e')
#         self.time_label.pack(side=tk.RIGHT)
#
#     def bind_keys(self):
#         self.root.bind('<Left>', lambda e: self.previous_image())
#         self.root.bind('<Right>', lambda e: self.next_image())
#         self.root.bind('<Control-o>', lambda e: self.open_file())
#         self.root.bind('<Control-s>', lambda e: self.save_result())
#
#     def reprocess_current(self):
#         if self.image_files:
#             self.process_current_image()
#
#     def save_result(self):
#         if not self.image_files or not hasattr(self, 'last_restored'):
#             return
#
#         current_file = self.image_files[self.current_index]
#         base_name = os.path.splitext(os.path.basename(current_file))[0]
#         default_name = f"{base_name}_restored.png"
#
#         save_path = filedialog.asksaveasfilename(
#             defaultextension=".png",
#             initialfile=default_name,
#             filetypes=[("PNG files", "*.png"), ("JPEG files", "*.jpg"), ("All files", "*.*")]
#         )
#
#         if save_path:
#             cv2.imwrite(save_path, self.last_restored)
#             self.filename_label.config(text=f"Saved: {os.path.basename(save_path)}")
#
#     def open_file(self):
#         file_path = filedialog.askopenfilename(
#             title="Select an image file",
#             filetypes=[
#                 ("Image files", "*.png *.jpg *.jpeg *.bmp *.tiff *.tif"),
#                 ("All files", "*.*")
#             ]
#         )
#
#         if not file_path:
#             return
#
#         file_path = os.path.normpath(file_path)
#         directory = os.path.dirname(file_path)
#         selected_filename = os.path.basename(file_path)
#
#         valid_extensions = {'.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.tif'}
#         all_files = []
#
#         for f in os.listdir(directory):
#             ext = os.path.splitext(f)[1].lower()
#             if ext in valid_extensions:
#                 full_path = os.path.normpath(os.path.join(directory, f))
#                 all_files.append(full_path)
#
#         self.image_files = sorted(all_files, key=lambda x: os.path.basename(x).lower())
#
#         if not self.image_files:
#             self.filename_label.config(text="No image files found in directory")
#             return
#
#         # Find index of selected file
#         self.current_index = 0
#         for i, fpath in enumerate(self.image_files):
#             if os.path.basename(fpath) == selected_filename:
#                 self.current_index = i
#                 break
#
#         self.process_current_image()
#
#     def previous_image(self):
#         if self.image_files and self.current_index > 0:
#             self.current_index -= 1
#             self.process_current_image()
#
#     def next_image(self):
#         if self.image_files and self.current_index < len(self.image_files) - 1:
#             self.current_index += 1
#             self.process_current_image()
#
#     def process_current_image(self):
#         if not self.image_files:
#             return
#
#         file_path = self.image_files[self.current_index]
#         filename = os.path.basename(file_path)
#
#         self.counter_label.config(text=f"{self.current_index + 1} / {len(self.image_files)}")
#
#         start_time = time.perf_counter()
#
#         try:
#             cropped_original, cropped_result = self.restore_hub_surface(file_path)
#             self.last_restored = cropped_result.copy()
#
#             elapsed_time = time.perf_counter() - start_time
#
#             self.display_image(cropped_original, self.left_canvas, 'left')
#             self.display_image(cropped_result, self.right_canvas, 'right')
#
#             self.filename_label.config(text=f"File: {filename}")
#             self.time_label.config(text=f"Processing time: {elapsed_time * 1000:.1f} ms")
#
#         except Exception as e:
#             self.filename_label.config(text=f"Error: {filename} - {str(e)}")
#             self.time_label.config(text="Processing time: N/A")
#             import traceback
#             traceback.print_exc()
#
#     def display_image(self, cv_image, canvas, side):
#         rgb_image = cv2.cvtColor(cv_image, cv2.COLOR_BGR2RGB)
#
#         canvas.update_idletasks()
#         canvas_width = canvas.winfo_width()
#         canvas_height = canvas.winfo_height()
#
#         if canvas_width <= 1:
#             canvas_width = 600
#         if canvas_height <= 1:
#             canvas_height = 600
#
#         img_h, img_w = rgb_image.shape[:2]
#         scale = min((canvas_width - 20) / img_w, (canvas_height - 20) / img_h)
#
#         new_w = int(img_w * scale)
#         new_h = int(img_h * scale)
#
#         resized = cv2.resize(rgb_image, (new_w, new_h), interpolation=cv2.INTER_AREA)
#
#         pil_image = Image.fromarray(resized)
#         photo = ImageTk.PhotoImage(pil_image)
#
#         canvas.delete("all")
#         x_pos = canvas_width // 2
#         y_pos = canvas_height // 2
#         canvas.create_image(x_pos, y_pos, image=photo, anchor=tk.CENTER)
#
#         if side == 'left':
#             self.left_photo = photo
#         else:
#             self.right_photo = photo
#
#     def rotate_image(self, img, angle, flags=cv2.INTER_LINEAR):
#         """Rotate image around the hub center point."""
#         M = cv2.getRotationMatrix2D(self.center, angle, 1.0)
#         return cv2.warpAffine(img, M, (img.shape[1], img.shape[0]),
#                               flags=flags, borderMode=cv2.BORDER_REPLICATE)
#
#     def detect_scribble_mask(self, img):
#         """
#         Detect scribbles and return a binary mask (without inpainting).
#         Uses polar domain analysis for robust groove-aware detection.
#         """
#         intensity_thresh = self.intensity_threshold.get()
#         color_thresh = self.color_sensitivity.get()
#         min_aspect = self.min_aspect_ratio.get()
#
#         # Polar transform - unwraps concentric grooves to horizontal lines
#         polar_height = self.max_radius
#         polar_width = int(2 * np.pi * self.max_radius)
#         polar_img = cv2.warpPolar(img, (polar_width, polar_height), self.center,
#                                    self.max_radius, cv2.WARP_POLAR_LINEAR)
#
#         gray = cv2.cvtColor(polar_img, cv2.COLOR_BGR2GRAY)
#
#         # === CHROMATIC MASK (Blue/Yellow via Lab color space) ===
#         polar_lab = cv2.cvtColor(polar_img, cv2.COLOR_BGR2Lab)
#         _, _, b_channel = cv2.split(polar_lab)
#         _, blue_mask = cv2.threshold(b_channel, color_thresh, 255, cv2.THRESH_BINARY_INV)
#         _, yellow_mask = cv2.threshold(b_channel, 140, 255, cv2.THRESH_BINARY)
#         color_mask = cv2.bitwise_or(blue_mask, yellow_mask)
#
#         # === STRUCTURAL MASK (Intensity differences from horizontal background) ===
#         # Grooves are horizontal in polar space - use horizontal morphology
#         h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (31, 1))
#         bg = cv2.morphologyEx(gray, cv2.MORPH_OPEN, h_kernel)
#         bg = cv2.morphologyEx(bg, cv2.MORPH_CLOSE, h_kernel)
#         diff = cv2.absdiff(gray, bg)
#         _, struct_mask = cv2.threshold(diff, intensity_thresh, 255, cv2.THRESH_BINARY)
#
#         # === SHAPE FILTERING (Keep elongated structures, reject circular spots) ===
#         num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(struct_mask, connectivity=8)
#         filtered_struct = np.zeros_like(struct_mask)
#
#         for i in range(1, num_labels):
#             area = stats[i, cv2.CC_STAT_AREA]
#             w = stats[i, cv2.CC_STAT_WIDTH]
#             h = stats[i, cv2.CC_STAT_HEIGHT]
#             if area < 30:
#                 continue
#             aspect = max(w, h) / (min(w, h) + 1)
#             # Keep if elongated OR large enough
#             if aspect > min_aspect or area > 200:
#                 filtered_struct[labels == i] = 255
#
#         # Combine masks in polar domain
#         polar_mask = cv2.bitwise_or(color_mask, filtered_struct)
#         polar_mask[0:self.min_radius, :] = 0  # Exclude center bearing region
#
#         # Small dilation for better coverage
#         kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
#         polar_mask = cv2.dilate(polar_mask, kernel, iterations=1)
#
#         # Convert mask back to Cartesian coordinates
#         cartesian_mask = cv2.warpPolar(polar_mask, (img.shape[1], img.shape[0]),
#                                         self.center, self.max_radius,
#                                         cv2.WARP_INVERSE_MAP | cv2.WARP_POLAR_LINEAR)
#         cartesian_mask = cv2.threshold(cartesian_mask, 127, 255, cv2.THRESH_BINARY)[1]
#
#         return cartesian_mask
#
#     def restore_hub_surface(self, image_path):
#         """
#         Remove scribbles from concentric metallic hubs.
#
#         Uses MASK ACCUMULATION approach (inspired by Grok's suggestion):
#         1. For each rotation angle (0°, 45°, 90°, 135°):
#            - Rotate image to that angle
#            - Detect scribbles (mask) at that orientation
#            - Rotate mask back to original orientation
#            - Accumulate (OR) into combined mask
#         2. Inpaint ONCE using the accumulated mask
#
#         Benefits over sequential process-rotate-process:
#         - Avoids compounding interpolation artifacts
#         - Single inpainting pass = cleaner result
#         - Each angle catches scribbles at different orientations relative to grooves
#         """
#         img = cv2.imread(image_path)
#         if img is None:
#             raise ValueError(f"Could not load image: {image_path}")
#
#         # Ensure even dimensions
#         if img.shape[0] % 2 != 0:
#             img = img[:-1, :]
#         if img.shape[1] % 2 != 0:
#             img = img[:, :-1]
#
#         cropped_original = self.create_circular_crop(img, self.center, self.max_radius)
#
#         h, w = img.shape[:2]
#         use_multipass = self.use_multipass.get()
#
#         if use_multipass:
#             # Multi-pass MASK ACCUMULATION
#             accumulated_mask = np.zeros((h, w), dtype=np.uint8)
#             rotation_angles = [0, 45, 90, 135]
#
#             for angle in rotation_angles:
#                 if angle == 0:
#                     rotated_img = img
#                 else:
#                     rotated_img = self.rotate_image(img, angle, flags=cv2.INTER_LINEAR)
#
#                 # Detect scribbles at this orientation
#                 mask = self.detect_scribble_mask(rotated_img)
#
#                 if angle != 0:
#                     # Rotate mask back to original orientation
#                     # Use NEAREST interpolation for binary mask
#                     mask = self.rotate_image(mask, -angle, flags=cv2.INTER_NEAREST)
#
#                 # Accumulate masks (OR operation)
#                 accumulated_mask = cv2.bitwise_or(accumulated_mask, mask)
#
#             final_mask = accumulated_mask
#         else:
#             # Single pass
#             final_mask = self.detect_scribble_mask(img)
#
#         # Limit to annular region
#         annular = np.zeros((h, w), dtype=np.uint8)
#         cv2.circle(annular, self.center, self.max_radius, 255, -1)
#         cv2.circle(annular, self.center, self.min_radius, 0, -1)
#         final_mask = cv2.bitwise_and(final_mask, annular)
#
#         # Final dilation for smooth inpainting
#         kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
#         final_mask = cv2.dilate(final_mask, kernel, iterations=1)
#
#         # Single inpainting pass on original image
#         restored = cv2.inpaint(img, final_mask, 5, cv2.INPAINT_TELEA)
#
#         # Blend only the annular region
#         result = img.copy()
#         result[annular > 0] = restored[annular > 0]
#
#         cropped_result = self.create_circular_crop(result, self.center, self.max_radius)
#
#         return cropped_original, cropped_result
#
#     def create_circular_crop(self, img, center, radius, padding=10):
#         """Create a square crop centered on the hub with circular region visible."""
#         x, y = center
#         crop_size = radius + padding
#
#         x1 = max(0, x - crop_size)
#         y1 = max(0, y - crop_size)
#         x2 = min(img.shape[1], x + crop_size)
#         y2 = min(img.shape[0], y + crop_size)
#
#         cropped = img[y1:y2, x1:x2].copy()
#
#         mask = np.zeros(cropped.shape[:2], dtype=np.uint8)
#         center_in_crop = (x - x1, y - y1)
#         cv2.circle(mask, center_in_crop, radius, 255, -1)
#
#         result = np.full_like(cropped, 40)  # Dark gray background
#         result[mask > 0] = cropped[mask > 0]
#
#         return result
#
#
# def main():
#     root = tk.Tk()
#     app = HubRestorationApp(root)
#     root.mainloop()
#
#
# if __name__ == "__main__":
#     main()


"""
Hub Surface Restoration Application
Removes scribbles from concentric metallic hubs using Polar-Domain Filtering.

Updates:
- Added Gamma Correction for better White detection.
- Added Black Level thresholding for Dark detection.
- Added "Bolt Rejection" logic (Aspect Ratio filtering) to prevent bolt blurring.
- Integrated Bilateral Filtering for texture suppression.
"""

import cv2
import numpy as np
import tkinter as tk
from tkinter import filedialog, ttk
from PIL import Image, ImageTk
import os
import time


class HubRestorationApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Hub Surface Restoration - Scribble Removal")
        self.root.geometry("1400x900")
        self.root.configure(bg='#2b2b2b')

        # State variables
        self.image_files = []
        self.current_index = 0
        self.last_restored = None

        # Processing parameters (Geometry)
        self.center = (650, 345)
        self.max_radius = 220
        self.min_radius = 130

        # Tunable detection parameters
        self.intensity_threshold = tk.IntVar(value=30)
        self.color_sensitivity = tk.IntVar(value=110)

        # New Parameters for Blacks/Whites
        self.gamma_value = tk.DoubleVar(value=1.0)
        self.white_sensitivity = tk.IntVar(value=200)
        self.black_sensitivity = tk.IntVar(value=60)

        # Shape/Structure Parameters
        self.min_aspect_ratio = tk.DoubleVar(value=2.0)  # Increased default to avoid bolts
        self.use_multipass = tk.BooleanVar(value=True)
        self.use_bilateral = tk.BooleanVar(value=True)  # User suggestion

        # Image references
        self.left_photo = None
        self.right_photo = None

        self.setup_ui()
        self.bind_keys()

    def setup_ui(self):
        """Set up the user interface."""
        style = ttk.Style()
        style.theme_use('clam')

        style.configure('TFrame', background='#2b2b2b')
        style.configure('TButton', padding=10, font=('Helvetica', 10))
        style.configure('TLabel', background='#2b2b2b', foreground='white', font=('Helvetica', 10))
        style.configure('Header.TLabel', font=('Helvetica', 12, 'bold'))
        style.configure('TLabelframe', background='#2b2b2b', foreground='white')
        style.configure('TLabelframe.Label', background='#2b2b2b', foreground='white', font=('Helvetica', 11, 'bold'))
        style.configure('TScale', background='#2b2b2b')
        style.configure('TNotebook', background='#2b2b2b', borderwidth=0)
        style.configure('TNotebook.Tab', background='#444', foreground='white', padding=[10, 5])
        style.map('TNotebook.Tab', background=[('selected', '#666')], foreground=[('selected', 'white')])

        # === TOP PANEL ===
        top_frame = ttk.Frame(self.root, padding=10)
        top_frame.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(top_frame, text="📁 Open File", command=self.open_file, width=15).pack(side=tk.LEFT, padx=5)
        ttk.Separator(top_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=15)
        ttk.Button(top_frame, text="◀ Previous", command=self.previous_image, width=12).pack(side=tk.LEFT, padx=5)
        ttk.Button(top_frame, text="Next ▶", command=self.next_image, width=12).pack(side=tk.LEFT, padx=5)
        ttk.Separator(top_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=15)
        ttk.Button(top_frame, text="💾 Save Result", command=self.save_result, width=12).pack(side=tk.LEFT, padx=5)

        shortcuts_label = ttk.Label(top_frame, text="[← → to navigate]",
                                    font=('Helvetica', 9), foreground='#888')
        shortcuts_label.pack(side=tk.LEFT, padx=20)

        self.counter_label = ttk.Label(top_frame, text="No files loaded", style='Header.TLabel')
        self.counter_label.pack(side=tk.RIGHT, padx=20)

        # === PARAMETER NOTEBOOK ===
        param_frame = ttk.LabelFrame(self.root, text="Detection Settings", padding=5)
        param_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=5)

        notebook = ttk.Notebook(param_frame)
        notebook.pack(fill=tk.BOTH, expand=True)

        # Tab 1: Basic Color & Structure
        tab1 = ttk.Frame(notebook)
        notebook.add(tab1, text='Color & Structure')

        ttk.Label(tab1, text="Color Sens (Blue/Yel):").grid(row=0, column=0, padx=5, pady=5)
        ttk.Scale(tab1, from_=150, to=80, variable=self.color_sensitivity, orient=tk.HORIZONTAL, length=150).grid(row=0,
                                                                                                                  column=1,
                                                                                                                  padx=5)

        ttk.Label(tab1, text="Struct Intensity Diff:").grid(row=0, column=2, padx=5, pady=5)
        ttk.Scale(tab1, from_=60, to=5, variable=self.intensity_threshold, orient=tk.HORIZONTAL, length=150).grid(row=0,
                                                                                                                  column=3,
                                                                                                                  padx=5)

        # Tab 2: Blacks & Whites (New)
        tab2 = ttk.Frame(notebook)
        notebook.add(tab2, text='Blacks & Whites')

        ttk.Label(tab2, text="Gamma (Contrast):").grid(row=0, column=0, padx=5, pady=5)
        val_label = ttk.Label(tab2, text="1.0")
        val_label.grid(row=0, column=2, padx=5)

        def update_gamma_label(val): val_label.config(text=f"{float(val):.1f}")

        g_scale = ttk.Scale(tab2, from_=0.1, to=4.0, variable=self.gamma_value, orient=tk.HORIZONTAL, length=150,
                            command=update_gamma_label)
        g_scale.grid(row=0, column=1, padx=5)

        ttk.Label(tab2, text="White Threshold:").grid(row=1, column=0, padx=5, pady=5)
        ttk.Scale(tab2, from_=100, to=255, variable=self.white_sensitivity, orient=tk.HORIZONTAL, length=150).grid(
            row=1, column=1, padx=5)

        ttk.Label(tab2, text="Black Threshold:").grid(row=1, column=3, padx=5, pady=5)
        ttk.Scale(tab2, from_=0, to=150, variable=self.black_sensitivity, orient=tk.HORIZONTAL, length=150).grid(row=1,
                                                                                                                 column=4,
                                                                                                                 padx=5)

        # Tab 3: Shape & Advanced
        tab3 = ttk.Frame(notebook)
        notebook.add(tab3, text='Shape & Advanced')

        ttk.Label(tab3, text="Min Aspect Ratio:").grid(row=0, column=0, padx=5, pady=5)
        ttk.Scale(tab3, from_=1.0, to=5.0, variable=self.min_aspect_ratio, orient=tk.HORIZONTAL, length=150).grid(row=0,
                                                                                                                  column=1,
                                                                                                                  padx=5)
        ttk.Label(tab3, text="(Higher = Protect Bolts)").grid(row=0, column=2, padx=5)

        ttk.Checkbutton(tab3, text="Use Multi-pass (4 Angles)", variable=self.use_multipass).grid(row=1, column=0,
                                                                                                  padx=5, pady=5)
        ttk.Checkbutton(tab3, text="Use Bilateral Pre-filter", variable=self.use_bilateral).grid(row=1, column=1,
                                                                                                 padx=5, pady=5)

        # Reprocess Button (Global)
        ttk.Button(param_frame, text="🔄 Reprocess Image", command=self.reprocess_current).pack(side=tk.RIGHT, pady=5,
                                                                                               padx=10)

        # === MIDDLE PANEL ===
        middle_frame = ttk.Frame(self.root, padding=10)
        middle_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        middle_frame.columnconfigure(0, weight=1)
        middle_frame.columnconfigure(1, weight=1)
        middle_frame.rowconfigure(0, weight=1)

        left_labelframe = ttk.LabelFrame(middle_frame, text="Original (Cropped)", padding=5)
        left_labelframe.grid(row=0, column=0, sticky='nsew', padx=5, pady=5)
        self.left_canvas = tk.Canvas(left_labelframe, bg='#1a1a1a', highlightthickness=1, highlightbackground='#444')
        self.left_canvas.pack(fill=tk.BOTH, expand=True)

        right_labelframe = ttk.LabelFrame(middle_frame, text="Processed (Scribbles Removed)", padding=5)
        right_labelframe.grid(row=0, column=1, sticky='nsew', padx=5, pady=5)
        self.right_canvas = tk.Canvas(right_labelframe, bg='#1a1a1a', highlightthickness=1, highlightbackground='#444')
        self.right_canvas.pack(fill=tk.BOTH, expand=True)

        # === BOTTOM PANEL ===
        bottom_frame = ttk.Frame(self.root, padding=10)
        bottom_frame.pack(side=tk.BOTTOM, fill=tk.X)

        self.filename_label = ttk.Label(bottom_frame, text="File: None", anchor='w')
        self.filename_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.time_label = ttk.Label(bottom_frame, text="Processing time: N/A", anchor='e')
        self.time_label.pack(side=tk.RIGHT)

    def bind_keys(self):
        self.root.bind('<Left>', lambda e: self.previous_image())
        self.root.bind('<Right>', lambda e: self.next_image())
        self.root.bind('<Control-o>', lambda e: self.open_file())
        self.root.bind('<Control-s>', lambda e: self.save_result())

    def reprocess_current(self):
        if self.image_files:
            self.process_current_image()

    def save_result(self):
        if not self.image_files or self.last_restored is None:
            return

        current_file = self.image_files[self.current_index]
        base_name = os.path.splitext(os.path.basename(current_file))[0]
        default_name = f"{base_name}_restored.png"

        save_path = filedialog.asksaveasfilename(
            defaultextension=".png",
            initialfile=default_name,
            filetypes=[("PNG files", "*.png"), ("JPEG files", "*.jpg"), ("All files", "*.*")]
        )

        if save_path:
            cv2.imwrite(save_path, self.last_restored)
            self.filename_label.config(text=f"Saved: {os.path.basename(save_path)}")

    def open_file(self):
        file_path = filedialog.askopenfilename(
            title="Select an image file",
            filetypes=[
                ("Image files", "*.png *.jpg *.jpeg *.bmp *.tiff *.tif"),
                ("All files", "*.*")
            ]
        )

        if not file_path:
            return

        file_path = os.path.normpath(file_path)
        directory = os.path.dirname(file_path)
        selected_filename = os.path.basename(file_path)

        valid_extensions = {'.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.tif'}
        all_files = []

        for f in os.listdir(directory):
            ext = os.path.splitext(f)[1].lower()
            if ext in valid_extensions:
                full_path = os.path.normpath(os.path.join(directory, f))
                all_files.append(full_path)

        self.image_files = sorted(all_files, key=lambda x: os.path.basename(x).lower())

        if not self.image_files:
            self.filename_label.config(text="No image files found in directory")
            return

        # Find index of selected file
        self.current_index = 0
        for i, fpath in enumerate(self.image_files):
            if os.path.basename(fpath) == selected_filename:
                self.current_index = i
                break

        self.process_current_image()

    def previous_image(self):
        if self.image_files and self.current_index > 0:
            self.current_index -= 1
            self.process_current_image()

    def next_image(self):
        if self.image_files and self.current_index < len(self.image_files) - 1:
            self.current_index += 1
            self.process_current_image()

    def process_current_image(self):
        if not self.image_files:
            return

        file_path = self.image_files[self.current_index]
        filename = os.path.basename(file_path)

        self.counter_label.config(text=f"{self.current_index + 1} / {len(self.image_files)}")

        start_time = time.perf_counter()

        try:
            cropped_original, cropped_result = self.restore_hub_surface(file_path)
            self.last_restored = cropped_result.copy()

            elapsed_time = time.perf_counter() - start_time

            self.display_image(cropped_original, self.left_canvas, 'left')
            self.display_image(cropped_result, self.right_canvas, 'right')

            self.filename_label.config(text=f"File: {filename}")
            self.time_label.config(text=f"Processing time: {elapsed_time * 1000:.1f} ms")

        except Exception as e:
            self.filename_label.config(text=f"Error: {filename} - {str(e)}")
            self.time_label.config(text="Processing time: N/A")
            import traceback
            traceback.print_exc()

    def display_image(self, cv_image, canvas, side):
        rgb_image = cv2.cvtColor(cv_image, cv2.COLOR_BGR2RGB)

        canvas.update_idletasks()
        canvas_width = canvas.winfo_width()
        canvas_height = canvas.winfo_height()

        if canvas_width <= 1:
            canvas_width = 600
        if canvas_height <= 1:
            canvas_height = 600

        img_h, img_w = rgb_image.shape[:2]
        scale = min((canvas_width - 20) / img_w, (canvas_height - 20) / img_h)

        new_w = int(img_w * scale)
        new_h = int(img_h * scale)

        resized = cv2.resize(rgb_image, (new_w, new_h), interpolation=cv2.INTER_AREA)

        pil_image = Image.fromarray(resized)
        photo = ImageTk.PhotoImage(pil_image)

        canvas.delete("all")
        x_pos = canvas_width // 2
        y_pos = canvas_height // 2
        canvas.create_image(x_pos, y_pos, image=photo, anchor=tk.CENTER)

        if side == 'left':
            self.left_photo = photo
        else:
            self.right_photo = photo

    def rotate_image(self, img, angle, flags=cv2.INTER_LINEAR):
        """Rotate image around the hub center point."""
        M = cv2.getRotationMatrix2D(self.center, angle, 1.0)
        return cv2.warpAffine(img, M, (img.shape[1], img.shape[0]),
                              flags=flags, borderMode=cv2.BORDER_REPLICATE)

    def adjust_gamma(self, image, gamma=1.0):
        """Apply gamma correction to handle blacks/whites better."""
        invGamma = 1.0 / gamma
        table = np.array([((i / 255.0) ** invGamma) * 255
                          for i in np.arange(0, 256)]).astype("uint8")
        return cv2.LUT(image, table)

    def detect_scribble_mask(self, img):
        """
        Detect scribbles and return a binary mask (without inpainting).
        Uses polar domain analysis for robust groove-aware detection.
        """
        intensity_thresh = self.intensity_threshold.get()
        color_thresh = self.color_sensitivity.get()
        min_aspect = self.min_aspect_ratio.get()
        gamma = self.gamma_value.get()
        white_thresh = self.white_sensitivity.get()
        black_thresh = self.black_sensitivity.get()
        use_bilateral = self.use_bilateral.get()

        # Polar transform - unwraps concentric grooves to horizontal lines
        polar_height = self.max_radius
        polar_width = int(2 * np.pi * self.max_radius)
        polar_img = cv2.warpPolar(img, (polar_width, polar_height), self.center,
                                  self.max_radius, cv2.WARP_POLAR_LINEAR)

        # Apply Gamma Correction (User Suggestion: helps isolate whites/blacks)
        # Note: If gamma > 1.0, midtones become darker. Whites stand out.
        polar_img_gamma = self.adjust_gamma(polar_img, gamma)
        gray = cv2.cvtColor(polar_img_gamma, cv2.COLOR_BGR2GRAY)

        # === 1. CHROMATIC MASK (Blue/Yellow) ===
        # Use Lab on the gamma-corrected image for better saturation distinction
        polar_lab = cv2.cvtColor(polar_img_gamma, cv2.COLOR_BGR2Lab)
        _, _, b_channel = cv2.split(polar_lab)
        _, blue_mask = cv2.threshold(b_channel, color_thresh, 255, cv2.THRESH_BINARY_INV)
        _, yellow_mask = cv2.threshold(b_channel, 140, 255, cv2.THRESH_BINARY)
        color_mask = cv2.bitwise_or(blue_mask, yellow_mask)

        # === 2. STRUCTURAL MASK (General scribbles vs Grooves) ===

        # Optional: Bilateral Filter (User Suggestion)
        # Flattens texture (grooves) to make scribbles pop in difference image
        src_for_struct = gray
        if use_bilateral:
            # Apply bilateral filter 3 times as requested
            blurred = gray.copy()
            for _ in range(3):
                blurred = cv2.bilateralFilter(blurred, 21, 30, 30)
            src_for_struct = blurred

        # Background estimation (Horizontal morphological open/close)
        h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (31, 1))
        bg = cv2.morphologyEx(src_for_struct, cv2.MORPH_OPEN, h_kernel)
        bg = cv2.morphologyEx(bg, cv2.MORPH_CLOSE, h_kernel)
        diff = cv2.absdiff(gray, bg)
        _, struct_mask = cv2.threshold(diff, intensity_thresh, 255, cv2.THRESH_BINARY)

        # === 3. WHITE & BLACK SPECIFIC MASKS ===
        # White: High intensity in Gamma Corrected image
        _, white_mask = cv2.threshold(gray, white_thresh, 255, cv2.THRESH_BINARY)

        # Black: Low intensity (inverse threshold)
        # We use the raw gray (without gamma or with, depending on preference)
        # Usually checking raw intensity is safer for absolute blacks
        raw_gray = cv2.cvtColor(polar_img, cv2.COLOR_BGR2GRAY)
        _, black_mask = cv2.threshold(raw_gray, black_thresh, 255, cv2.THRESH_BINARY_INV)

        # Combine all masks
        combined_mask = cv2.bitwise_or(color_mask, struct_mask)
        combined_mask = cv2.bitwise_or(combined_mask, white_mask)
        combined_mask = cv2.bitwise_or(combined_mask, black_mask)

        # === 4. BOLT REJECTION & SHAPE FILTERING ===
        # This is critical to prevent "Blur around bolts"
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(combined_mask, connectivity=8)
        final_polar_mask = np.zeros_like(combined_mask)

        for i in range(1, num_labels):
            area = stats[i, cv2.CC_STAT_AREA]
            w = stats[i, cv2.CC_STAT_WIDTH]
            h = stats[i, cv2.CC_STAT_HEIGHT]

            # Avoid tiny noise
            if area < 20:
                continue

            # Aspect ratio calculation
            # Scribbles are usually elongated (lines). Bolts are compact (blobs).
            aspect = max(w, h) / (min(w, h) + 1.0)

            # BOLT LOGIC:
            # If object is "fat" (low aspect ratio) AND reasonably large, it's likely a bolt.
            # We only keep it if it's elongated (aspect > min_aspect).
            # Exception: Very large complex scribbles might be blobs, but bolts are regular.

            is_scribble = False

            if aspect > min_aspect:
                is_scribble = True
            elif area > 1000:
                # Very large blobs might be huge scribbles, keep them
                is_scribble = True

            if is_scribble:
                final_polar_mask[labels == i] = 255

        # Mask refinement
        final_polar_mask[0:self.min_radius, :] = 0  # Exclude center bearing region

        # Small dilation
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        final_polar_mask = cv2.dilate(final_polar_mask, kernel, iterations=1)

        # Convert mask back to Cartesian coordinates
        cartesian_mask = cv2.warpPolar(final_polar_mask, (img.shape[1], img.shape[0]),
                                       self.center, self.max_radius,
                                       cv2.WARP_INVERSE_MAP | cv2.WARP_POLAR_LINEAR)
        cartesian_mask = cv2.threshold(cartesian_mask, 127, 255, cv2.THRESH_BINARY)[1]

        return cartesian_mask

    def restore_hub_surface(self, image_path):
        """
        Remove scribbles from concentric metallic hubs using accumulated masking.
        """
        img = cv2.imread(image_path)
        if img is None:
            raise ValueError(f"Could not load image: {image_path}")

        # Ensure even dimensions
        if img.shape[0] % 2 != 0:
            img = img[:-1, :]
        if img.shape[1] % 2 != 0:
            img = img[:, :-1]

        cropped_original = self.create_circular_crop(img, self.center, self.max_radius)

        h, w = img.shape[:2]
        use_multipass = self.use_multipass.get()

        if use_multipass:
            # Multi-pass MASK ACCUMULATION
            accumulated_mask = np.zeros((h, w), dtype=np.uint8)
            rotation_angles = [0, 45, 90, 135]

            for angle in rotation_angles:
                if angle == 0:
                    rotated_img = img
                else:
                    rotated_img = self.rotate_image(img, angle, flags=cv2.INTER_LINEAR)

                # Detect scribbles at this orientation
                mask = self.detect_scribble_mask(rotated_img)

                if angle != 0:
                    # Rotate mask back to original orientation
                    mask = self.rotate_image(mask, -angle, flags=cv2.INTER_NEAREST)

                # Accumulate masks (OR operation)
                accumulated_mask = cv2.bitwise_or(accumulated_mask, mask)

            final_mask = accumulated_mask
        else:
            # Single pass
            final_mask = self.detect_scribble_mask(img)

        # Limit to annular region
        annular = np.zeros((h, w), dtype=np.uint8)
        cv2.circle(annular, self.center, self.max_radius, 255, -1)
        cv2.circle(annular, self.center, self.min_radius, 0, -1)
        final_mask = cv2.bitwise_and(final_mask, annular)

        # Final dilation for smooth inpainting
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        final_mask = cv2.dilate(final_mask, kernel, iterations=1)

        # Single inpainting pass on original image
        restored = cv2.inpaint(img, final_mask, 5, cv2.INPAINT_TELEA)

        # Blend only the annular region
        result = img.copy()
        result[annular > 0] = restored[annular > 0]

        cropped_result = self.create_circular_crop(result, self.center, self.max_radius)

        return cropped_original, cropped_result

    def create_circular_crop(self, img, center, radius, padding=10):
        """Create a square crop centered on the hub with circular region visible."""
        x, y = center
        crop_size = radius + padding

        x1 = max(0, x - crop_size)
        y1 = max(0, y - crop_size)
        x2 = min(img.shape[1], x + crop_size)
        y2 = min(img.shape[0], y + crop_size)

        cropped = img[y1:y2, x1:x2].copy()

        mask = np.zeros(cropped.shape[:2], dtype=np.uint8)
        center_in_crop = (x - x1, y - y1)
        cv2.circle(mask, center_in_crop, radius, 255, -1)

        result = np.full_like(cropped, 40)  # Dark gray background
        result[mask > 0] = cropped[mask > 0]

        return result


def main():
    root = tk.Tk()
    app = HubRestorationApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()