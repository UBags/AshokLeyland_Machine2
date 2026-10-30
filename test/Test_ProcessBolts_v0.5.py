import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk
import cv2
import numpy as np
import os
import time
from pathlib import Path
from itertools import combinations


class BoltDetectorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Bolt/Hole Detector - DOST/DOSTPLUS")
        self.root.geometry("1400x850")

        # Variables
        self.current_folder = None
        self.image_files = []
        self.current_index = 0
        self.kernel1_size = tk.IntVar(value=3)
        self.kernel2_size = tk.IntVar(value=33)
        self.kernel3_size = tk.IntVar(value=15)
        self.processing_time = tk.StringVar(value="Processing time: N/A")
        self.see_progress = tk.BooleanVar(value=False)
        self.do_hist_opt = tk.BooleanVar(value=False)
        self.do_hist_eq = tk.BooleanVar(value=False)
        self.gamma_value = tk.DoubleVar(value=4.0)
        self.do_clahe = tk.BooleanVar(value=False)
        self.do_unsharp = tk.BooleanVar(value=False)
        self.bilateral2_sigma_color = tk.IntVar(value=30)
        self.bilateral2_sigma_space = tk.IntVar(value=40)
        self.bilateral3_sigma_color = tk.IntVar(value=30)
        self.bilateral3_sigma_space = tk.IntVar(value=30)

        # Step tracking for progress mode
        self.step_images = []  # List of (step_name, image) tuples
        self.current_step = 0
        self.step_info = tk.StringVar(value="")

        # Setup UI
        self.setup_ui()

    def setup_ui(self):
        # Top frame for controls
        control_frame = ttk.Frame(self.root, padding="10")
        control_frame.pack(fill=tk.X)

        # File selection button
        ttk.Button(control_frame, text="Select Image", command=self.select_image).pack(side=tk.LEFT, padx=5)

        # Folder selection button
        ttk.Button(control_frame, text="Select Folder", command=self.select_folder).pack(side=tk.LEFT, padx=5)

        # Next button
        ttk.Button(control_frame, text="Next Image", command=self.next_image).pack(side=tk.LEFT, padx=5)

        # Previous button
        ttk.Button(control_frame, text="Previous Image", command=self.prev_image).pack(side=tk.LEFT, padx=5)

        # Kernel 1 size dropdown
        ttk.Label(control_frame, text="Kernel 1:").pack(side=tk.LEFT, padx=(20, 5))
        kernel1_dropdown = ttk.Combobox(control_frame, textvariable=self.kernel1_size,
                                        values=[3, 5, 7, 9, 11, 13, 15], width=5, state="readonly")
        kernel1_dropdown.pack(side=tk.LEFT, padx=5)
        kernel1_dropdown.bind("<<ComboboxSelected>>", self.on_kernel_change)

        # Kernel 2 size dropdown
        ttk.Label(control_frame, text="Kernel 2:").pack(side=tk.LEFT, padx=(10, 5))
        kernel2_dropdown = ttk.Combobox(control_frame, textvariable=self.kernel2_size,
                                        values=list(range(7, 32, 2)), width=5, state="readonly")
        kernel2_dropdown.pack(side=tk.LEFT, padx=5)
        kernel2_dropdown.bind("<<ComboboxSelected>>", self.on_kernel_change)

        # Kernel 3 size dropdown (for erosion)
        ttk.Label(control_frame, text="Kernel 3:").pack(side=tk.LEFT, padx=(10, 5))
        kernel3_dropdown = ttk.Combobox(control_frame, textvariable=self.kernel3_size,
                                        values=list(range(7, 22, 2)), width=5, state="readonly")
        kernel3_dropdown.pack(side=tk.LEFT, padx=5)
        kernel3_dropdown.bind("<<ComboboxSelected>>", self.on_kernel_change)

        # Second row for progress controls
        progress_frame = ttk.Frame(self.root, padding="5")
        progress_frame.pack(fill=tk.X)

        # See Progress checkbox
        self.progress_checkbox = ttk.Checkbutton(
            progress_frame,
            text="See Progress after each step",
            variable=self.see_progress,
            command=self.on_progress_toggle
        )
        self.progress_checkbox.pack(side=tk.LEFT, padx=5)

        # Show Next Step button (initially disabled)
        self.next_step_btn = ttk.Button(
            progress_frame,
            text="Show Next Step",
            command=self.show_next_step,
            state=tk.DISABLED
        )
        self.next_step_btn.pack(side=tk.LEFT, padx=5)

        # Show Previous Step button (initially disabled)
        self.prev_step_btn = ttk.Button(
            progress_frame,
            text="Show Previous Step",
            command=self.show_prev_step,
            state=tk.DISABLED
        )
        self.prev_step_btn.pack(side=tk.LEFT, padx=5)

        # Reset to Final button
        self.reset_btn = ttk.Button(
            progress_frame,
            text="Show Final Result",
            command=self.show_final_result,
            state=tk.DISABLED
        )
        self.reset_btn.pack(side=tk.LEFT, padx=5)

        # Step info label
        ttk.Label(progress_frame, textvariable=self.step_info, font=('Arial', 9, 'italic')).pack(side=tk.LEFT, padx=20)

        # Third row for histogram controls
        hist_frame = ttk.Frame(self.root, padding="5")
        hist_frame.pack(fill=tk.X)

        # Histogram Optimisation checkbox
        self.hist_opt_checkbox = ttk.Checkbutton(
            hist_frame,
            text="Do Histogram Optimisation",
            variable=self.do_hist_opt,
            command=self.on_param_change
        )
        self.hist_opt_checkbox.pack(side=tk.LEFT, padx=5)

        # Histogram Equalisation checkbox
        self.hist_eq_checkbox = ttk.Checkbutton(
            hist_frame,
            text="Do Histogram Equalisation",
            variable=self.do_hist_eq,
            command=self.on_param_change
        )
        self.hist_eq_checkbox.pack(side=tk.LEFT, padx=20)

        # Gamma dropdown
        ttk.Label(hist_frame, text="Gamma:").pack(side=tk.LEFT, padx=(20, 5))
        gamma_values = [round(0.5 + i * 0.25, 2) for i in range(23)]  # 0.5 to 6.0 in steps of 0.25
        gamma_dropdown = ttk.Combobox(hist_frame, textvariable=self.gamma_value,
                                      values=gamma_values, width=5, state="readonly")
        gamma_dropdown.pack(side=tk.LEFT, padx=5)
        gamma_dropdown.set(4.0)
        gamma_dropdown.bind("<<ComboboxSelected>>", self.on_param_change)

        # CLAHE checkbox
        self.clahe_checkbox = ttk.Checkbutton(
            hist_frame,
            text="Do CLAHE",
            variable=self.do_clahe,
            command=self.on_param_change
        )
        self.clahe_checkbox.pack(side=tk.LEFT, padx=(20, 5))

        # Unsharp Mask checkbox
        self.unsharp_checkbox = ttk.Checkbutton(
            hist_frame,
            text="Do Unsharp Mask",
            variable=self.do_unsharp,
            command=self.on_param_change
        )
        self.unsharp_checkbox.pack(side=tk.LEFT, padx=5)

        # Fourth row for bilateral filter controls
        bilateral_frame = ttk.Frame(self.root, padding="5")
        bilateral_frame.pack(fill=tk.X)

        sigma_values = list(range(30, 110, 10))  # 30 to 100 in steps of 10

        # Second bilateral filter controls
        ttk.Label(bilateral_frame, text="Bilateral 2 -").pack(side=tk.LEFT, padx=5)
        ttk.Label(bilateral_frame, text="σColor:").pack(side=tk.LEFT, padx=(5, 2))
        bilateral2_color_dropdown = ttk.Combobox(bilateral_frame, textvariable=self.bilateral2_sigma_color,
                                                 values=sigma_values, width=4, state="readonly")
        bilateral2_color_dropdown.pack(side=tk.LEFT, padx=2)
        bilateral2_color_dropdown.bind("<<ComboboxSelected>>", self.on_param_change)

        ttk.Label(bilateral_frame, text="σSpace:").pack(side=tk.LEFT, padx=(5, 2))
        bilateral2_space_dropdown = ttk.Combobox(bilateral_frame, textvariable=self.bilateral2_sigma_space,
                                                 values=sigma_values, width=4, state="readonly")
        bilateral2_space_dropdown.pack(side=tk.LEFT, padx=2)
        bilateral2_space_dropdown.bind("<<ComboboxSelected>>", self.on_param_change)

        # Separator
        ttk.Label(bilateral_frame, text="  |  ").pack(side=tk.LEFT, padx=5)

        # Third bilateral filter controls
        ttk.Label(bilateral_frame, text="Bilateral 3 -").pack(side=tk.LEFT, padx=5)
        ttk.Label(bilateral_frame, text="σColor:").pack(side=tk.LEFT, padx=(5, 2))
        bilateral3_color_dropdown = ttk.Combobox(bilateral_frame, textvariable=self.bilateral3_sigma_color,
                                                 values=sigma_values, width=4, state="readonly")
        bilateral3_color_dropdown.pack(side=tk.LEFT, padx=2)
        bilateral3_color_dropdown.bind("<<ComboboxSelected>>", self.on_param_change)

        ttk.Label(bilateral_frame, text="σSpace:").pack(side=tk.LEFT, padx=(5, 2))
        bilateral3_space_dropdown = ttk.Combobox(bilateral_frame, textvariable=self.bilateral3_sigma_space,
                                                 values=sigma_values, width=4, state="readonly")
        bilateral3_space_dropdown.pack(side=tk.LEFT, padx=2)
        bilateral3_space_dropdown.bind("<<ComboboxSelected>>", self.on_param_change)

        # File info frame
        file_frame = ttk.Frame(self.root, padding="5")
        file_frame.pack(fill=tk.X)

        # Current file label
        self.file_label = ttk.Label(file_frame, text="No file selected", font=('Arial', 10, 'bold'))
        self.file_label.pack(side=tk.LEFT, padx=10)

        # Image index label
        self.index_label = ttk.Label(file_frame, text="")
        self.index_label.pack(side=tk.RIGHT, padx=10)

        # Main frame for images
        self.image_frame = ttk.Frame(self.root, padding="10")
        self.image_frame.pack(fill=tk.BOTH, expand=True)

        # Left panel - Original annular disc
        left_frame = ttk.LabelFrame(self.image_frame, text="Original Annular Disc", padding="5")
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

        self.left_label_var = tk.StringVar(value="Original Annular Disc")
        self.left_frame_widget = left_frame

        self.left_canvas = tk.Canvas(left_frame, bg='black')
        self.left_canvas.pack(fill=tk.BOTH, expand=True)

        # Right panel - Processed result
        right_frame = ttk.LabelFrame(self.image_frame, text="Processed Result (After Dilation)", padding="5")
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5)

        self.right_label_var = tk.StringVar(value="Processed Result (After Dilation)")
        self.right_frame_widget = right_frame

        self.right_canvas = tk.Canvas(right_frame, bg='black')
        self.right_canvas.pack(fill=tk.BOTH, expand=True)

        # Bottom frame for processing time
        bottom_frame = ttk.Frame(self.root, padding="10")
        bottom_frame.pack(fill=tk.X)

        ttk.Label(bottom_frame, textvariable=self.processing_time, font=('Arial', 10)).pack(side=tk.LEFT)

        # Store image references to prevent garbage collection
        self.left_photo = None
        self.right_photo = None
        self.current_image_path = None

    def select_image(self):
        """Select a single image file and set its folder as current folder"""
        file_path = filedialog.askopenfilename(
            title="Select Image",
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp *.tiff")]
        )
        if file_path:
            self.current_folder = os.path.dirname(file_path)
            self.load_folder_images()
            # Find index of selected file
            filename = os.path.basename(file_path)
            if filename in self.image_files:
                self.current_index = self.image_files.index(filename)
            self.process_current_image()

    def select_folder(self):
        """Select a folder containing images"""
        folder_path = filedialog.askdirectory(title="Select Folder")
        if folder_path:
            self.current_folder = folder_path
            self.load_folder_images()
            if self.image_files:
                self.current_index = 0
                self.process_current_image()
            else:
                messagebox.showwarning("No Images", "No image files found in the selected folder.")

    def load_folder_images(self):
        """Load all image files from current folder and sort alphanumerically"""
        if self.current_folder:
            all_files = os.listdir(self.current_folder)
            image_extensions = {'.png', '.jpg', '.jpeg', '.bmp', '.tiff'}
            self.image_files = sorted([f for f in all_files
                                       if os.path.splitext(f)[1].lower() in image_extensions])

    def next_image(self):
        """Go to next image in folder"""
        if self.image_files and self.current_index < len(self.image_files) - 1:
            self.current_index += 1
            self.process_current_image()
        elif self.image_files:
            messagebox.showinfo("End", "This is the last image in the folder.")

    def prev_image(self):
        """Go to previous image in folder"""
        if self.image_files and self.current_index > 0:
            self.current_index -= 1
            self.process_current_image()
        elif self.image_files:
            messagebox.showinfo("Start", "This is the first image in the folder.")

    def on_kernel_change(self, event=None):
        """Re-process image when kernel size changes"""
        if self.current_image_path:
            self.process_current_image()

    def on_progress_toggle(self):
        """Handle progress checkbox toggle"""
        if self.current_image_path:
            self.process_current_image()

    def on_param_change(self, event=None):
        """Handle any parameter change"""
        if self.current_image_path:
            self.process_current_image()

    def update_step_buttons(self):
        """Update the state of step navigation buttons"""
        if self.see_progress.get() and self.step_images:
            self.next_step_btn.config(state=tk.NORMAL if self.current_step < len(self.step_images) - 1 else tk.DISABLED)
            self.prev_step_btn.config(state=tk.NORMAL if self.current_step > 0 else tk.DISABLED)
            self.reset_btn.config(state=tk.NORMAL)
            self.step_info.set(f"Step {self.current_step + 1} of {len(self.step_images)}")
        else:
            self.next_step_btn.config(state=tk.DISABLED)
            self.prev_step_btn.config(state=tk.DISABLED)
            self.reset_btn.config(state=tk.DISABLED)
            self.step_info.set("")

    def show_next_step(self):
        """Show the next processing step"""
        if self.current_step < len(self.step_images) - 1:
            self.current_step += 1
            self.display_current_step()
            self.update_step_buttons()

    def show_prev_step(self):
        """Show the previous processing step"""
        if self.current_step > 0:
            self.current_step -= 1
            self.display_current_step()
            self.update_step_buttons()

    def show_final_result(self):
        """Show the final result (last step)"""
        if self.step_images:
            self.current_step = len(self.step_images) - 1
            self.display_current_step()
            self.update_step_buttons()

    def display_current_step(self):
        """Display the current step's before/after images"""
        if not self.step_images or self.current_step >= len(self.step_images):
            return

        step_name, after_img = self.step_images[self.current_step]

        # Get the "before" image (previous step's result, or original for first step)
        if self.current_step == 0:
            before_img = self.step_images[0][1]  # First step shows same image
            before_name = "Original"
        else:
            before_name, before_img = self.step_images[self.current_step - 1]

        # Update frame labels
        self.left_frame_widget.config(text=f"Before: {before_name}")
        self.right_frame_widget.config(text=f"After: {step_name}")

        # Display images
        self.display_images(before_img, after_img)

    def process_current_image(self):
        """Process the current image and display results"""
        if not self.image_files or not self.current_folder:
            return

        filename = self.image_files[self.current_index]
        self.current_image_path = os.path.join(self.current_folder, filename)

        # Update labels
        self.file_label.config(text=f"File: {filename}")
        self.index_label.config(text=f"Image {self.current_index + 1} of {len(self.image_files)}")

        # Load image
        img = cv2.imread(self.current_image_path)
        if img is None:
            messagebox.showerror("Error", f"Could not load image: {filename}")
            return

        # Start timing after loading
        start_time = time.time()

        # Process image
        if self.see_progress.get():
            self.step_images = self.process_image_with_steps(img)
            self.current_step = 0
            self.display_current_step()
            self.update_step_buttons()
        else:
            annular, processed = self.process_image(img)
            # Reset frame labels
            self.left_frame_widget.config(text="Original Annular Disc")
            self.right_frame_widget.config(text="Processed Result (After Dilation)")
            # Display images
            self.display_images(annular, processed)
            self.update_step_buttons()

        # End timing
        elapsed_time = time.time() - start_time
        self.processing_time.set(f"Processing time: {elapsed_time * 1000:.2f} ms")

    def process_image(self, img):
        """Process image and return only final result"""
        center = (635, 360)
        outer_radius = 220
        inner_radius = 110  # diameter 220 / 2

        # Create masks
        outer_mask = np.zeros(img.shape[:2], dtype=np.uint8)
        cv2.circle(outer_mask, center, outer_radius, 255, -1)

        inner_mask = np.zeros(img.shape[:2], dtype=np.uint8)
        cv2.circle(inner_mask, center, inner_radius, 255, -1)

        annular_mask = cv2.subtract(outer_mask, inner_mask)

        # Crop to bounding box
        x1, y1 = center[0] - outer_radius, center[1] - outer_radius
        x2, y2 = center[0] + outer_radius, center[1] + outer_radius
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(img.shape[1], x2), min(img.shape[0], y2)

        cropped = img[y1:y2, x1:x2].copy()
        annular_mask_cropped = annular_mask[y1:y2, x1:x2]

        annular_img = cv2.bitwise_and(cropped, cropped, mask=annular_mask_cropped)
        gray = cv2.cvtColor(annular_img, cv2.COLOR_BGR2GRAY)

        annular_display = annular_img.copy()

        # Processing pipeline - start with grayscale
        current = gray.copy()

        # c) Histogram optimization (conditional)
        if self.do_hist_opt.get():
            non_zero_mask = current > 0
            if np.any(non_zero_mask):
                min_val = current[non_zero_mask].min()
                max_val = current[non_zero_mask].max()
                if max_val > min_val:
                    optimized = np.zeros_like(current)
                    optimized[non_zero_mask] = ((current[non_zero_mask].astype(float) - min_val) /
                                                (max_val - min_val) * 255).astype(np.uint8)
                    current = optimized

        # d) Histogram equalization (conditional)
        if self.do_hist_eq.get():
            current = cv2.equalizeHist(current)
            current = cv2.bitwise_and(current, current, mask=annular_mask_cropped)

        # e) Bilateral filter 15, 30, 30
        step1 = cv2.bilateralFilter(current, 15, 30, 30)

        # f) Gamma correction (using 1/gamma to brighten)
        gamma = self.gamma_value.get()
        normalized = step1.astype(np.float32) / 255.0
        gamma_corrected = np.power(normalized, 1.0 / gamma)
        step2 = (gamma_corrected * 255).astype(np.uint8)

        # g) Second bilateral filter with configurable sigma values
        sigma_color_2 = self.bilateral2_sigma_color.get()
        sigma_space_2 = self.bilateral2_sigma_space.get()
        step3 = cv2.bilateralFilter(step2, 9, sigma_color_2, sigma_space_2)

        # h) Third bilateral filter with configurable sigma values
        sigma_color_3 = self.bilateral3_sigma_color.get()
        sigma_space_3 = self.bilateral3_sigma_space.get()
        step4 = cv2.bilateralFilter(step3, 5, sigma_color_3, sigma_space_3)

        # i) CLAHE (conditional)
        current_step = step4
        if self.do_clahe.get():
            clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
            current_step = clahe.apply(current_step)
            current_step = cv2.bitwise_and(current_step, current_step, mask=annular_mask_cropped)

        # j) Unsharp mask (conditional)
        if self.do_unsharp.get():
            gaussian = cv2.GaussianBlur(current_step, (0, 0), 3)
            current_step = cv2.addWeighted(current_step, 1.5, gaussian, -0.5, 0)
            current_step = cv2.bitwise_and(current_step, current_step, mask=annular_mask_cropped)

        # k) Canny edges
        edges = cv2.Canny(current_step, 50, 150)

        # l) First dilation with kernel1
        kernel1_size = self.kernel1_size.get()
        kernel1 = np.ones((kernel1_size, kernel1_size), np.uint8)
        dilated1 = cv2.dilate(edges, kernel1, iterations=1)
        dilated1 = cv2.bitwise_and(dilated1, dilated1, mask=annular_mask_cropped)

        # l2) Remove annular disc edges (outer and inner ring edges)
        # a) Create black-filled annular disc shape
        base_shape = np.zeros_like(gray)
        # b) The annular mask already represents the shape - use it to get edges
        # c) Get Canny edges of the annular mask
        base_edges = cv2.Canny(annular_mask_cropped, 50, 150)
        # d) Dilate by kernel1 + 2
        base_kernel_size = kernel1_size + 2
        base_kernel = np.ones((base_kernel_size, base_kernel_size), np.uint8)
        base_edges_dilated = cv2.dilate(base_edges, base_kernel, iterations=1)
        # e) Remove overlapping pixels: where both are white, make black
        dilated1[base_edges_dilated == 255] = 0

        # m) Filter connected components by size
        img_height, img_width = dilated1.shape[:2]
        max_comp_height = img_height * 0.25
        max_comp_width = img_width * 0.25

        # Find connected components
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(dilated1, connectivity=8)

        # Create filtered output
        filtered = np.zeros_like(dilated1)
        for i in range(1, num_labels):  # Skip background (label 0)
            x, y, w, h, area = stats[i]
            # Keep component if both height and width are within limits
            if h <= max_comp_height and w <= max_comp_width:
                filtered[labels == i] = 255

        # n) Second dilation with kernel2
        kernel2_size = self.kernel2_size.get()
        kernel2 = np.ones((kernel2_size, kernel2_size), np.uint8)
        dilated2 = cv2.dilate(filtered, kernel2, iterations=1)
        dilated2 = cv2.bitwise_and(dilated2, dilated2, mask=annular_mask_cropped)

        # o) Erosion with kernel3
        kernel3_size = self.kernel3_size.get()
        kernel3 = np.ones((kernel3_size, kernel3_size), np.uint8)
        eroded = cv2.erode(dilated2, kernel3, iterations=1)
        eroded = cv2.bitwise_and(eroded, eroded, mask=annular_mask_cropped)

        # p) Find 5 largest connected components
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(eroded, connectivity=8)

        if num_labels <= 1:
            # No components found
            dilated_bgr = cv2.cvtColor(eroded, cv2.COLOR_GRAY2BGR)
            return annular_display, dilated_bgr

        # Get areas and sort (skip background label 0)
        component_data = []
        for i in range(1, num_labels):
            area = stats[i, cv2.CC_STAT_AREA]
            centroid = centroids[i]
            component_data.append((i, area, centroid))

        # Sort by area descending and keep top 5
        component_data.sort(key=lambda x: x[1], reverse=True)
        top_5 = component_data[:5]

        if len(top_5) < 4:
            # Not enough blobs, return what we have
            result = np.zeros_like(eroded)
            for label_id, area, centroid in top_5:
                result[labels == label_id] = 255
            dilated_bgr = cv2.cvtColor(result, cv2.COLOR_GRAY2BGR)
            return annular_display, dilated_bgr

        # q) Find equidistant blobs (4 or 5)
        # Calculate image center
        img_center = (eroded.shape[1] / 2, eroded.shape[0] / 2)

        # Calculate angles from center for each blob
        blob_angles = []
        for label_id, area, centroid in top_5:
            dx = centroid[0] - img_center[0]
            dy = centroid[1] - img_center[1]
            angle = np.arctan2(dy, dx)  # in radians
            blob_angles.append((label_id, angle, centroid, area))

        # Sort by angle
        blob_angles.sort(key=lambda x: x[1])

        # Function to calculate angular spacing variance
        def calc_spacing_variance(angles, expected_spacing):
            n = len(angles)
            spacings = []
            for i in range(n):
                diff = angles[(i + 1) % n] - angles[i]
                if diff < 0:
                    diff += 2 * np.pi
                spacings.append(diff)
            # Calculate variance from expected spacing
            variance = sum((s - expected_spacing) ** 2 for s in spacings) / n
            return variance

        # Try 5 equidistant (72° = 2π/5)
        angles_5 = [b[1] for b in blob_angles]
        variance_5 = calc_spacing_variance(angles_5, 2 * np.pi / 5)

        # Try all combinations of 4 from 5 (90° = 2π/4)
        best_4_variance = float('inf')
        best_4_indices = None

        for combo in combinations(range(len(blob_angles)), 4):
            angles_4 = sorted([blob_angles[i][1] for i in combo])
            variance_4 = calc_spacing_variance(angles_4, 2 * np.pi / 4)
            if variance_4 < best_4_variance:
                best_4_variance = variance_4
                best_4_indices = combo

        # Choose configuration with lower variance
        result = np.zeros_like(eroded)
        if variance_5 < best_4_variance:
            # Use all 5 blobs
            for label_id, angle, centroid, area in blob_angles:
                result[labels == label_id] = 255
        else:
            # Use best 4 blobs
            for i in best_4_indices:
                label_id = blob_angles[i][0]
                result[labels == label_id] = 255

        dilated_bgr = cv2.cvtColor(result, cv2.COLOR_GRAY2BGR)

        return annular_display, dilated_bgr

    def process_image_with_steps(self, img):
        """Process image and return all intermediate steps"""
        steps = []

        center = (635, 360)
        outer_radius = 220
        inner_radius = 110  # diameter 220 / 2

        # Create masks
        outer_mask = np.zeros(img.shape[:2], dtype=np.uint8)
        cv2.circle(outer_mask, center, outer_radius, 255, -1)

        inner_mask = np.zeros(img.shape[:2], dtype=np.uint8)
        cv2.circle(inner_mask, center, inner_radius, 255, -1)

        annular_mask = cv2.subtract(outer_mask, inner_mask)

        # Crop to bounding box
        x1, y1 = center[0] - outer_radius, center[1] - outer_radius
        x2, y2 = center[0] + outer_radius, center[1] + outer_radius
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(img.shape[1], x2), min(img.shape[0], y2)

        cropped = img[y1:y2, x1:x2].copy()
        annular_mask_cropped = annular_mask[y1:y2, x1:x2]

        # a) & b) Annular disc
        annular_img = cv2.bitwise_and(cropped, cropped, mask=annular_mask_cropped)
        steps.append(("a,b) Annular Disc (outer=220, inner=110)", annular_img.copy()))

        # Convert to grayscale
        gray = cv2.cvtColor(annular_img, cv2.COLOR_BGR2GRAY)
        gray_bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        steps.append(("Grayscale", gray_bgr.copy()))

        # Start with grayscale
        current = gray.copy()

        # c) Histogram optimization (conditional)
        if self.do_hist_opt.get():
            non_zero_mask = current > 0
            if np.any(non_zero_mask):
                min_val = current[non_zero_mask].min()
                max_val = current[non_zero_mask].max()
                if max_val > min_val:
                    optimized = np.zeros_like(current)
                    optimized[non_zero_mask] = ((current[non_zero_mask].astype(float) - min_val) /
                                                (max_val - min_val) * 255).astype(np.uint8)
                    current = optimized
            steps.append(("c) Histogram Optimization", cv2.cvtColor(current, cv2.COLOR_GRAY2BGR)))

        # d) Histogram equalization (conditional)
        if self.do_hist_eq.get():
            current = cv2.equalizeHist(current)
            current = cv2.bitwise_and(current, current, mask=annular_mask_cropped)
            steps.append(("d) Histogram Equalization", cv2.cvtColor(current, cv2.COLOR_GRAY2BGR)))

        # e) Bilateral filter 15, 30, 30
        step1 = cv2.bilateralFilter(current, 15, 30, 30)
        steps.append(("e) Bilateral Filter (15,30,30)", cv2.cvtColor(step1, cv2.COLOR_GRAY2BGR)))

        # f) Gamma correction (using 1/gamma to brighten)
        gamma = self.gamma_value.get()
        normalized = step1.astype(np.float32) / 255.0
        gamma_corrected = np.power(normalized, 1.0 / gamma)
        step2 = (gamma_corrected * 255).astype(np.uint8)
        steps.append((f"f) Gamma {gamma} (brighten)", cv2.cvtColor(step2, cv2.COLOR_GRAY2BGR)))

        # g) Second bilateral filter with configurable sigma values
        sigma_color_2 = self.bilateral2_sigma_color.get()
        sigma_space_2 = self.bilateral2_sigma_space.get()
        step3 = cv2.bilateralFilter(step2, 9, sigma_color_2, sigma_space_2)
        steps.append(
            (f"g) Bilateral Filter (9,{sigma_color_2},{sigma_space_2})", cv2.cvtColor(step3, cv2.COLOR_GRAY2BGR)))

        # h) Third bilateral filter with configurable sigma values
        sigma_color_3 = self.bilateral3_sigma_color.get()
        sigma_space_3 = self.bilateral3_sigma_space.get()
        step4 = cv2.bilateralFilter(step3, 5, sigma_color_3, sigma_space_3)
        steps.append(
            (f"h) Bilateral Filter (5,{sigma_color_3},{sigma_space_3})", cv2.cvtColor(step4, cv2.COLOR_GRAY2BGR)))

        # i) CLAHE (conditional)
        current_step = step4
        if self.do_clahe.get():
            clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
            current_step = clahe.apply(current_step)
            current_step = cv2.bitwise_and(current_step, current_step, mask=annular_mask_cropped)
            steps.append(("i) CLAHE Contrast", cv2.cvtColor(current_step, cv2.COLOR_GRAY2BGR)))

        # j) Unsharp mask (conditional)
        if self.do_unsharp.get():
            gaussian = cv2.GaussianBlur(current_step, (0, 0), 3)
            current_step = cv2.addWeighted(current_step, 1.5, gaussian, -0.5, 0)
            current_step = cv2.bitwise_and(current_step, current_step, mask=annular_mask_cropped)
            steps.append(("j) Unsharp Mask", cv2.cvtColor(current_step, cv2.COLOR_GRAY2BGR)))

        # k) Canny edges
        edges = cv2.Canny(current_step, 50, 150)
        steps.append(("k) Canny Edges", cv2.cvtColor(edges, cv2.COLOR_GRAY2BGR)))

        # l) First dilation with kernel1
        kernel1_size = self.kernel1_size.get()
        kernel1 = np.ones((kernel1_size, kernel1_size), np.uint8)
        dilated1 = cv2.dilate(edges, kernel1, iterations=1)
        dilated1 = cv2.bitwise_and(dilated1, dilated1, mask=annular_mask_cropped)
        steps.append((f"l) Dilation 1 (kernel={kernel1_size})", cv2.cvtColor(dilated1, cv2.COLOR_GRAY2BGR)))

        # l2) Remove annular disc edges (outer and inner ring edges)
        # Get Canny edges of the annular mask
        base_edges = cv2.Canny(annular_mask_cropped, 50, 150)
        # Dilate by kernel1 + 2
        base_kernel_size = kernel1_size + 2
        base_kernel = np.ones((base_kernel_size, base_kernel_size), np.uint8)
        base_edges_dilated = cv2.dilate(base_edges, base_kernel, iterations=1)
        # Remove overlapping pixels
        dilated1[base_edges_dilated == 255] = 0
        steps.append((f"l2) Remove Ring Edges (base_k={base_kernel_size})", cv2.cvtColor(dilated1, cv2.COLOR_GRAY2BGR)))

        # m) Filter connected components by size
        img_height, img_width = dilated1.shape[:2]
        max_comp_height = img_height * 0.25
        max_comp_width = img_width * 0.25

        # Find connected components
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(dilated1, connectivity=8)

        # Create filtered output
        filtered = np.zeros_like(dilated1)
        for i in range(1, num_labels):  # Skip background (label 0)
            x, y, w, h, area = stats[i]
            # Keep component if both height and width are within limits
            if h <= max_comp_height and w <= max_comp_width:
                filtered[labels == i] = 255
        steps.append(("m) Filter Components (h,w < 25%)", cv2.cvtColor(filtered, cv2.COLOR_GRAY2BGR)))

        # n) Second dilation with kernel2
        kernel2_size = self.kernel2_size.get()
        kernel2 = np.ones((kernel2_size, kernel2_size), np.uint8)
        dilated2 = cv2.dilate(filtered, kernel2, iterations=1)
        dilated2 = cv2.bitwise_and(dilated2, dilated2, mask=annular_mask_cropped)
        steps.append((f"n) Dilation 2 (kernel={kernel2_size})", cv2.cvtColor(dilated2, cv2.COLOR_GRAY2BGR)))

        # o) Erosion with kernel3
        kernel3_size = self.kernel3_size.get()
        kernel3 = np.ones((kernel3_size, kernel3_size), np.uint8)
        eroded = cv2.erode(dilated2, kernel3, iterations=1)
        eroded = cv2.bitwise_and(eroded, eroded, mask=annular_mask_cropped)
        steps.append((f"o) Erosion (kernel={kernel3_size})", cv2.cvtColor(eroded, cv2.COLOR_GRAY2BGR)))

        # p) Find 5 largest connected components
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(eroded, connectivity=8)

        if num_labels > 1:
            # Get areas and sort (skip background label 0)
            component_data = []
            for i in range(1, num_labels):
                area = stats[i, cv2.CC_STAT_AREA]
                centroid = centroids[i]
                component_data.append((i, area, centroid))

            # Sort by area descending and keep top 5
            component_data.sort(key=lambda x: x[1], reverse=True)
            top_5 = component_data[:5]

            # Create image with top 5
            top_5_img = np.zeros_like(eroded)
            for label_id, area, centroid in top_5:
                top_5_img[labels == label_id] = 255
            steps.append(("p) Top 5 Largest Blobs", cv2.cvtColor(top_5_img, cv2.COLOR_GRAY2BGR)))

            if len(top_5) >= 4:
                # q) Find equidistant blobs (4 or 5)
                img_center = (eroded.shape[1] / 2, eroded.shape[0] / 2)

                # Calculate angles from center for each blob
                blob_angles = []
                for label_id, area, centroid in top_5:
                    dx = centroid[0] - img_center[0]
                    dy = centroid[1] - img_center[1]
                    angle = np.arctan2(dy, dx)
                    blob_angles.append((label_id, angle, centroid, area))

                # Sort by angle
                blob_angles.sort(key=lambda x: x[1])

                # Function to calculate angular spacing variance
                def calc_spacing_variance(angles, expected_spacing):
                    n = len(angles)
                    spacings = []
                    for i in range(n):
                        diff = angles[(i + 1) % n] - angles[i]
                        if diff < 0:
                            diff += 2 * np.pi
                        spacings.append(diff)
                    variance = sum((s - expected_spacing) ** 2 for s in spacings) / n
                    return variance

                # Try 5 equidistant (72° = 2π/5)
                angles_5 = [b[1] for b in blob_angles]
                variance_5 = calc_spacing_variance(angles_5, 2 * np.pi / 5)

                # Try all combinations of 4 from 5 (90° = 2π/4)
                best_4_variance = float('inf')
                best_4_indices = None

                for combo in combinations(range(len(blob_angles)), 4):
                    angles_4 = sorted([blob_angles[i][1] for i in combo])
                    variance_4 = calc_spacing_variance(angles_4, 2 * np.pi / 4)
                    if variance_4 < best_4_variance:
                        best_4_variance = variance_4
                        best_4_indices = combo

                # Choose configuration with lower variance
                result = np.zeros_like(eroded)
                if variance_5 < best_4_variance:
                    config = "5 equidistant"
                    for label_id, angle, centroid, area in blob_angles:
                        result[labels == label_id] = 255
                else:
                    config = "4 equidistant"
                    for i in best_4_indices:
                        label_id = blob_angles[i][0]
                        result[labels == label_id] = 255

                steps.append((f"q) {config} Blobs", cv2.cvtColor(result, cv2.COLOR_GRAY2BGR)))

        return steps

    def display_images(self, left_img, right_img):
        """Display images on the canvases"""
        # Get canvas sizes
        self.root.update_idletasks()
        left_width = self.left_canvas.winfo_width()
        left_height = self.left_canvas.winfo_height()
        right_width = self.right_canvas.winfo_width()
        right_height = self.right_canvas.winfo_height()

        # Minimum size
        left_width = max(left_width, 400)
        left_height = max(left_height, 400)
        right_width = max(right_width, 400)
        right_height = max(right_height, 400)

        # Convert and resize left image
        left_rgb = cv2.cvtColor(left_img, cv2.COLOR_BGR2RGB)
        left_pil = Image.fromarray(left_rgb)
        left_pil = self.resize_image(left_pil, left_width, left_height)
        self.left_photo = ImageTk.PhotoImage(left_pil)

        # Convert and resize right image
        right_rgb = cv2.cvtColor(right_img, cv2.COLOR_BGR2RGB)
        right_pil = Image.fromarray(right_rgb)
        right_pil = self.resize_image(right_pil, right_width, right_height)
        self.right_photo = ImageTk.PhotoImage(right_pil)

        # Clear canvases
        self.left_canvas.delete("all")
        self.right_canvas.delete("all")

        # Display images centered
        self.left_canvas.create_image(left_width // 2, left_height // 2,
                                      image=self.left_photo, anchor=tk.CENTER)
        self.right_canvas.create_image(right_width // 2, right_height // 2,
                                       image=self.right_photo, anchor=tk.CENTER)

    def resize_image(self, pil_img, max_width, max_height):
        """Resize image to fit within max dimensions while maintaining aspect ratio"""
        width, height = pil_img.size
        ratio = min(max_width / width, max_height / height)
        new_width = int(width * ratio)
        new_height = int(height * ratio)
        return pil_img.resize((new_width, new_height), Image.Resampling.LANCZOS)


def main():
    root = tk.Tk()
    app = BoltDetectorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()