"""
Tkinter GUI for Image Normalization Pipeline
Uses utils.ImageNormalisationWithMask module
"""

import tkinter as tk
from tkinter import filedialog, ttk
from PIL import Image, ImageTk
import numpy as np
import cv2
import glob
import os
from pathlib import Path

# Import the normalization functions
import sys
sys.path.append('utils')
from utils.ImageNormalisationWithMask import (
    rgb2gray, create_annular_mask,
    pixBackgroundNorm_masked, pixContrastNorm_masked,
    pixGammaCorrection_masked, pixUnsharpMaskingGray_masked,
    pixRankFilterGray_masked
)


class ImageNormalizationApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Image Normalization Pipeline - Hub Inspection")
        self.root.geometry("1400x900")

        # State variables
        self.image_files = []
        self.current_index = 0
        self.current_image = None
        self.current_image_path = None

        # Processing parameters
        self.center = (635, 360)
        self.outer_radius = 230
        self.inner_radius = 120

        # Pipeline steps and tracing
        self.pipeline_steps = []
        self.current_trace_step = 0
        self.bgsx = 15
        self.bgsy = 15
        self.cnsx = 10
        self.cnmindiff = 30
        self.gm = 3.0
        self.hw = 5
        self.fract = 2.5
        self.step_names = [
            "Original",
            f"Step 1: Background Norm (sx={self.bgsx}, sy={self.bgsy})",
            f"Step 2: Contrast Norm (sx={self.cnsx}, mindiff={self.cnmindiff})",
            f"Step 3: Gamma {self.gm} (brighten)",
            f"Step 4: Unsharp (hw={self.hw}, fract={self.fract})",
            "Step 5: Rank Filter 95% (size=31)"
        ]

        self.setup_ui()

    def setup_ui(self):
        """Setup the user interface"""

        # ===== TOP CONTROL PANEL =====
        control_frame = tk.Frame(self.root, bg='lightgray', height=80)
        control_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=5)
        control_frame.pack_propagate(False)

        # File selection buttons
        btn_open = tk.Button(
            control_frame,
            text="📁 Choose Folder",
            command=self.choose_folder,
            font=('Arial', 11, 'bold'),
            bg='#4CAF50',
            fg='white',
            width=15,
            height=2
        )
        btn_open.pack(side=tk.LEFT, padx=5, pady=10)

        btn_open_image = tk.Button(
            control_frame,
            text="📄 Choose Image",
            command=self.choose_image,
            font=('Arial', 11, 'bold'),
            bg='#4CAF50',
            fg='white',
            width=15,
            height=2
        )
        btn_open_image.pack(side=tk.LEFT, padx=5, pady=10)

        # Navigation buttons
        nav_frame = tk.Frame(control_frame, bg='lightgray')
        nav_frame.pack(side=tk.LEFT, padx=20)

        btn_prev = tk.Button(
            nav_frame,
            text="◄ Previous",
            command=self.previous_image,
            font=('Arial', 11),
            bg='#2196F3',
            fg='white',
            width=12
        )
        btn_prev.pack(side=tk.LEFT, padx=2)

        btn_next = tk.Button(
            nav_frame,
            text="Next ►",
            command=self.next_image,
            font=('Arial', 11),
            bg='#2196F3',
            fg='white',
            width=12
        )
        btn_next.pack(side=tk.LEFT, padx=2)

        # File info label
        self.info_label = tk.Label(
            control_frame,
            text="No folder selected",
            font=('Arial', 10),
            bg='lightgray',
            fg='black'
        )
        self.info_label.pack(side=tk.LEFT, padx=20)

        # Trace navigation buttons
        trace_frame = tk.Frame(control_frame, bg='lightgray')
        trace_frame.pack(side=tk.RIGHT, padx=5)

        btn_prev_step = tk.Button(
            trace_frame,
            text="◄ Prev Step",
            command=self.previous_trace_step,
            font=('Arial', 10),
            bg='#FF9800',
            fg='white',
            width=12
        )
        btn_prev_step.pack(side=tk.LEFT, padx=2)

        btn_next_step = tk.Button(
            trace_frame,
            text="Next Step ►",
            command=self.next_trace_step,
            font=('Arial', 10),
            bg='#FF9800',
            fg='white',
            width=12
        )
        btn_next_step.pack(side=tk.LEFT, padx=2)

        # Process button
        btn_process = tk.Button(
            control_frame,
            text="▶ Process Image",
            command=self.process_image,
            font=('Arial', 12, 'bold'),
            bg='#9C27B0',
            fg='white',
            width=15,
            height=2
        )
        btn_process.pack(side=tk.RIGHT, padx=5, pady=10)

        # ===== MAIN IMAGE DISPLAY AREA =====
        display_frame = tk.Frame(self.root, bg='white')
        display_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=5)

        # Left side - Original with Annular
        left_frame = tk.LabelFrame(
            display_frame,
            text="Original Image (with Annular Mask)",
            font=('Arial', 11, 'bold'),
            bg='white'
        )
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

        self.canvas_left = tk.Canvas(left_frame, bg='gray80', highlightthickness=0)
        self.canvas_left.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Right side - Processed Result
        self.right_frame = tk.LabelFrame(
            display_frame,
            text="Processed Result (Click Process, then use Prev/Next Step to trace)",
            font=('Arial', 11, 'bold'),
            bg='white'
        )
        self.right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5)

        self.canvas_right = tk.Canvas(self.right_frame, bg='gray80', highlightthickness=0)
        self.canvas_right.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # ===== STATUS BAR =====
        status_frame = tk.Frame(self.root, bg='#263238', height=30)
        status_frame.pack(side=tk.BOTTOM, fill=tk.X)
        status_frame.pack_propagate(False)

        self.status_label = tk.Label(
            status_frame,
            text="Ready. Please select a folder containing images.",
            font=('Arial', 9),
            bg='#263238',
            fg='white',
            anchor='w'
        )
        self.status_label.pack(side=tk.LEFT, padx=10, fill=tk.X, expand=True)

    def choose_folder(self):
        """Open folder dialog and load image files"""
        folder_path = filedialog.askdirectory(title="Select Folder with Images")

        if not folder_path:
            return

        # Find all image files
        extensions = ['*.png', '*.jpg', '*.jpeg', '*.bmp', '*.tiff']
        self.image_files = []

        for ext in extensions:
            self.image_files.extend(glob.glob(os.path.join(folder_path, ext)))
            self.image_files.extend(glob.glob(os.path.join(folder_path, ext.upper())))

        self.image_files.sort()

        if not self.image_files:
            self.status_label.config(text="No image files found in selected folder!")
            return

        self.current_index = 0
        self.update_info_label()
        self.load_current_image()

        self.status_label.config(
            text=f"Loaded {len(self.image_files)} images from {os.path.basename(folder_path)}"
        )

    def choose_image(self):
        """Open file dialog to select a single image"""
        file_path = filedialog.askopenfilename(
            title="Select an Image",
            filetypes=[
                ("Image files", "*.png *.jpg *.jpeg *.bmp *.tiff"),
                ("PNG files", "*.png"),
                ("JPEG files", "*.jpg *.jpeg"),
                ("All files", "*.*")
            ]
        )

        if not file_path:
            return

        # Set this as the only image in the list
        self.image_files = [file_path]
        self.current_index = 0
        self.update_info_label()
        self.load_current_image()

        self.status_label.config(text=f"Loaded single image: {os.path.basename(file_path)}")

    def update_info_label(self):
        """Update the file information label"""
        if not self.image_files:
            self.info_label.config(text="No folder selected")
            return

        total = len(self.image_files)
        filename = os.path.basename(self.image_files[self.current_index])
        self.info_label.config(
            text=f"Image {self.current_index + 1} of {total}: {filename}"
        )

    def next_image(self):
        """Load next image in the list"""
        if not self.image_files:
            self.status_label.config(text="No images loaded!")
            return

        # Move to next image (with wraparound)
        self.current_index = (self.current_index + 1) % len(self.image_files)
        self.update_info_label()
        self.load_current_image()
        self.status_label.config(text=f"Moved to next image: {os.path.basename(self.image_files[self.current_index])}")

    def previous_image(self):
        """Load previous image in the list"""
        if not self.image_files:
            self.status_label.config(text="No images loaded!")
            return

        # Move to previous image (with wraparound)
        self.current_index = (self.current_index - 1) % len(self.image_files)
        self.update_info_label()
        self.load_current_image()
        self.status_label.config(text=f"Moved to previous image: {os.path.basename(self.image_files[self.current_index])}")

    def load_current_image(self):
        """Load and display the current image (convert BGR to grayscale)"""
        if not self.image_files:
            return

        try:
            self.current_image_path = self.image_files[self.current_index]

            # Load image with OpenCV (BGR format)
            bgr_image = cv2.imread(self.current_image_path)

            if bgr_image is None:
                raise ValueError(f"Failed to load image: {self.current_image_path}")

            # Convert BGR to Grayscale
            self.current_image = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2GRAY)

            # Display original with annular mask overlay
            self.display_original_with_mask()

            # Clear right canvas and reset trace
            self.current_trace_step = 0
            self.pipeline_steps = []
            self.canvas_right.delete("all")
            self.canvas_right.create_text(
                self.canvas_right.winfo_width() // 2,
                self.canvas_right.winfo_height() // 2,
                text="Click 'Process Image' to see results",
                font=('Arial', 14),
                fill='gray'
            )

            self.status_label.config(text=f"Loaded (grayscale): {os.path.basename(self.current_image_path)}")

        except Exception as e:
            self.status_label.config(text=f"Error loading image: {str(e)}")
            import traceback
            traceback.print_exc()

    def display_original_with_mask(self):
        """Display original image with red annular mask overlay"""
        if self.current_image is None:
            return

        try:
            # Image is already grayscale from load_current_image
            gray = self.current_image.copy()
            mask = create_annular_mask(gray.shape, self.center, self.outer_radius, self.inner_radius)

            # Create RGB overlay for display
            display_img = np.stack([gray]*3, axis=2).astype(np.uint8)

            # Add red overlay on mask
            # display_img[mask, 2] = np.minimum(display_img[mask, 2] + 100, 255)

            self.display_image_on_canvas(self.canvas_left, display_img)

        except Exception as e:
            self.status_label.config(text=f"Error displaying image: {str(e)}")
            import traceback
            traceback.print_exc()

    def display_image_on_canvas(self, canvas, image_array):
        """Display numpy array on a canvas, resized to fit"""
        try:
            # Convert to PIL Image
            if image_array.dtype != np.uint8:
                image_array = (image_array * 255).astype(np.uint8) if image_array.max() <= 1.0 else image_array.astype(
                    np.uint8)

            # Swap R and B channels if 3-channel image (BGR to RGB for PIL)
            if image_array.ndim == 3 and image_array.shape[2] == 3:
                image_array = cv2.cvtColor(image_array, cv2.COLOR_BGR2RGB)

            pil_image = Image.fromarray(image_array)

            # Get canvas size
            canvas.update()
            canvas_width = canvas.winfo_width()
            canvas_height = canvas.winfo_height()

            if canvas_width < 10 or canvas_height < 10:
                canvas_width = 600
                canvas_height = 600

            # Resize maintaining aspect ratio
            img_width, img_height = pil_image.size
            scale = min(canvas_width / img_width, canvas_height / img_height) * 0.95

            new_width = int(img_width * scale)
            new_height = int(img_height * scale)

            pil_image = pil_image.resize((new_width, new_height), Image.Resampling.LANCZOS)

            # Convert to PhotoImage
            photo = ImageTk.PhotoImage(pil_image)

            # Display on canvas
            canvas.delete("all")
            canvas.create_image(
                canvas_width // 2,
                canvas_height // 2,
                image=photo,
                anchor=tk.CENTER
            )

            # Keep reference to prevent garbage collection
            canvas.image = photo

        except Exception as e:
            print(f"Error displaying image: {str(e)}")

    def process_image(self):
        """Run the complete processing pipeline"""
        if self.current_image is None:
            self.status_label.config(text="No image loaded!")
            return

        self.status_label.config(text="Processing pipeline...")
        self.root.update()

        try:
            # Image is already grayscale
            gray = self.current_image.copy()
            mask = create_annular_mask(gray.shape, self.center, self.outer_radius, self.inner_radius)

            # Store all pipeline steps
            self.pipeline_steps = [gray.copy()]

            self.bgsx = 25
            self.bgsy = 25

            # Step 1: Background Normalization
            step1 = pixBackgroundNorm_masked(gray, mask, sx=self.bgsx, sy=self.bgsy, bgval=200)
            self.pipeline_steps.append(step1.copy())

            self.cnsx = 25
            self.cnmindiff = 25
            # Step 2: Contrast Normalization
            step2 = pixContrastNorm_masked(step1, mask, sx=self.cnsx, sy=self.cnmindiff, mindiff=40)
            self.pipeline_steps.append(step2.copy())

            # Step 3: Gamma Correction (3.0 = brighten)
            self.gm = 4.0
            step3 = pixGammaCorrection_masked(step2, mask, gamma=self.gm)
            self.pipeline_steps.append(step3.copy())

            # Step 4: Unsharp Masking
            self.hw = 10
            self.fract = 4.0
            step4 = pixUnsharpMaskingGray_masked(step3, mask, halfwidth=self.hw, fract=self.fract)
            self.pipeline_steps.append(step4.copy())

            # Step 5: Rank Filter (95%)
            self.rank = 0.95
            final_result = pixRankFilterGray_masked(step4, mask, size=31, rank=self.rank)
            self.pipeline_steps.append(final_result.copy())

            # Set trace to final step and display it
            self.current_trace_step = len(self.pipeline_steps) - 1
            self.display_trace_step()

            self.status_label.config(
                text=f"✓ Processing complete! Use ◄ Prev Step / Next Step ► to trace through pipeline. Currently showing: {self.step_names[self.current_trace_step]}"
            )

        except Exception as e:
            self.status_label.config(text=f"Error processing image: {str(e)}")
            import traceback
            traceback.print_exc()

    def previous_trace_step(self):
        """Show previous step in the pipeline trace"""
        if not self.pipeline_steps:
            self.status_label.config(text="Process an image first before tracing!")
            return

        # Move to previous step (with wraparound)
        self.current_trace_step = (self.current_trace_step - 1) % len(self.pipeline_steps)
        self.display_trace_step()

        self.status_label.config(
            text=f"Trace: {self.step_names[self.current_trace_step]} (Step {self.current_trace_step + 1}/{len(self.pipeline_steps)})"
        )

    def next_trace_step(self):
        """Show next step in the pipeline trace"""
        if not self.pipeline_steps:
            self.status_label.config(text="Process an image first before tracing!")
            return

        # Move to next step (with wraparound)
        self.current_trace_step = (self.current_trace_step + 1) % len(self.pipeline_steps)
        self.display_trace_step()

        self.status_label.config(
            text=f"Trace: {self.step_names[self.current_trace_step]} (Step {self.current_trace_step + 1}/{len(self.pipeline_steps)})"
        )

    def display_trace_step(self):
        """Display the current trace step in the right panel"""
        if not self.pipeline_steps or self.current_trace_step >= len(self.pipeline_steps):
            return

        try:
            # Update right panel title
            self.right_frame.config(text=f"Pipeline Trace: {self.step_names[self.current_trace_step]}")

            gray = self.current_image.copy()
            mask = create_annular_mask(gray.shape, self.center, self.outer_radius, self.inner_radius)

            # Get current step image
            step_img = self.pipeline_steps[self.current_trace_step]

            # Create display with red mask overlay
            display_result = np.stack([step_img]*3, axis=2)
            # display_result[mask, 2] = np.minimum(display_result[mask, 2] + 80, 255)

            self.display_image_on_canvas(self.canvas_right, display_result)

        except Exception as e:
            self.status_label.config(text=f"Error displaying trace step: {str(e)}")
            import traceback
            traceback.print_exc()

    def show_trace_window(self):
        """Deprecated - replaced by next/prev trace step buttons"""
        pass


def main():
    root = tk.Tk()
    app = ImageNormalizationApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()