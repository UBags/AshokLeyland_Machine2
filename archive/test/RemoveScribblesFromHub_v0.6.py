"""
Hub Surface Restoration Application v0.7
Removes scribbles from concentric metallic hubs using Polar-Domain Filtering.

Updates v0.7:
- More aggressive detection defaults
- Separate aggressiveness controls for Blue/Yellow, Black, and White
- Added dilation control for each mode
- Added inpaint radius control
"""

import cv2
import numpy as np
import tkinter as tk
from tkinter import filedialog, ttk, Toplevel
from PIL import Image, ImageTk
import os
import time


class HubRestorationApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Hub Surface Restoration v0.7 - Scribble Removal")
        self.root.geometry("1500x950")
        self.root.configure(bg='#2b2b2b')

        # State variables
        self.image_files = []
        self.current_index = 0
        self.last_restored = None

        # Processing parameters (Geometry)
        self.center = (650, 345)
        self.max_radius = 220
        self.min_radius = 130

        # ============================================
        # BLUE/YELLOW Detection Parameters
        # ============================================
        # Color threshold: LOWER = more aggressive (catches more blue)
        # Range: 80-150, Default was 110, now 100 for more aggressive
        self.color_threshold = tk.IntVar(value=100)

        # Aspect ratio for color: LOWER = more aggressive (keeps more blobs)
        # Range: 1.0-4.0, Default was 2.0, now 1.5 for more aggressive
        self.color_min_aspect = tk.DoubleVar(value=1.5)

        # Dilation for color mask: HIGHER = more aggressive
        # Range: 1-5, Default 2
        self.color_dilation = tk.IntVar(value=2)

        # ============================================
        # BLACK Detection Parameters
        # ============================================
        # Black threshold: HIGHER = more aggressive (catches lighter blacks)
        # Range: 30-120, Default was 60, now 80 for more aggressive
        self.black_threshold = tk.IntVar(value=80)

        # Intensity diff for black: LOWER = more aggressive
        # Range: 5-40, Default was 25, now 15 for more aggressive
        self.black_intensity_diff = tk.IntVar(value=15)

        # Aspect ratio for black: LOWER = more aggressive
        # Range: 1.0-4.0, Default was 2.0, now 1.3 for more aggressive
        self.black_min_aspect = tk.DoubleVar(value=1.3)

        # Dilation for black mask
        self.black_dilation = tk.IntVar(value=2)

        # ============================================
        # WHITE Detection Parameters
        # ============================================
        # White threshold: LOWER = more aggressive (catches darker whites)
        # Range: 140-230, Default was 200, now 170 for more aggressive
        self.white_threshold = tk.IntVar(value=170)

        # Intensity diff for white: LOWER = more aggressive
        # Range: 5-40, Default was 25, now 15 for more aggressive
        self.white_intensity_diff = tk.IntVar(value=15)

        # Aspect ratio for white: LOWER = more aggressive
        # Range: 1.0-4.0, Default was 2.0, now 1.3 for more aggressive
        self.white_min_aspect = tk.DoubleVar(value=1.3)

        # Dilation for white mask
        self.white_dilation = tk.IntVar(value=2)

        # ============================================
        # Global Parameters
        # ============================================
        self.inpaint_radius = tk.IntVar(value=5)  # Higher = more aggressive blending

        # Image references
        self.left_photo = None
        self.right_photo = None

        # Debug visualization storage
        self.debug_steps = []  # List of (step_name, image) tuples
        self.debug_window = None

        self.setup_ui()
        self.bind_keys()

    def setup_ui(self):
        """Set up the user interface."""
        style = ttk.Style()
        style.theme_use('clam')

        style.configure('TFrame', background='#2b2b2b')
        style.configure('TButton', padding=10, font=('Helvetica', 10))
        style.configure('TLabel', background='#2b2b2b', foreground='white', font=('Helvetica', 9))
        style.configure('Header.TLabel', font=('Helvetica', 12, 'bold'))
        style.configure('TLabelframe', background='#2b2b2b', foreground='white')
        style.configure('TLabelframe.Label', background='#2b2b2b', foreground='white', font=('Helvetica', 10, 'bold'))
        style.configure('TScale', background='#2b2b2b')
        style.configure('TNotebook', background='#2b2b2b', borderwidth=0)
        style.configure('TNotebook.Tab', background='#444', foreground='white', padding=[10, 5])
        style.map('TNotebook.Tab', background=[('selected', '#666')], foreground=[('selected', 'white')])
        style.configure('Aggr.TLabelframe.Label', foreground='#ffcc00')

        # === TOP PANEL ===
        top_frame = ttk.Frame(self.root, padding=10)
        top_frame.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(top_frame, text="📁 Open File", command=self.open_file, width=15).pack(side=tk.LEFT, padx=5)
        ttk.Separator(top_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=15)
        ttk.Button(top_frame, text="◀ Prev", command=self.previous_image, width=10).pack(side=tk.LEFT, padx=5)
        ttk.Button(top_frame, text="Next ▶", command=self.next_image, width=10).pack(side=tk.LEFT, padx=5)
        ttk.Separator(top_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=15)
        ttk.Button(top_frame, text="🔄 Reprocess", command=self.reprocess_current, width=12).pack(side=tk.LEFT, padx=5)
        ttk.Button(top_frame, text="💾 Save", command=self.save_result, width=10).pack(side=tk.LEFT, padx=5)
        ttk.Separator(top_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=15)
        ttk.Button(top_frame, text="🔍 Show Steps", command=self.show_debug_window, width=12).pack(side=tk.LEFT, padx=5)

        self.counter_label = ttk.Label(top_frame, text="No files loaded", style='Header.TLabel')
        self.counter_label.pack(side=tk.RIGHT, padx=20)

        # === PARAMETER NOTEBOOK ===
        param_frame = ttk.Frame(self.root, padding=5)
        param_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=5)

        notebook = ttk.Notebook(param_frame)
        notebook.pack(fill=tk.BOTH, expand=True)

        # ========== Tab 1: Blue/Yellow ==========
        tab_color = ttk.Frame(notebook)
        notebook.add(tab_color, text='🔵 Blue/Yellow')

        row = 0
        ttk.Label(tab_color, text="Color Threshold:", foreground='#88ccff').grid(row=row, column=0, padx=5, pady=3, sticky='e')
        ttk.Scale(tab_color, from_=150, to=80, variable=self.color_threshold, orient=tk.HORIZONTAL, length=120).grid(row=row, column=1, padx=5)
        ttk.Label(tab_color, text="← More Aggressive", foreground='#888').grid(row=row, column=2, padx=5, sticky='w')

        ttk.Label(tab_color, text="Min Aspect Ratio:").grid(row=row, column=3, padx=15, pady=3, sticky='e')
        ttk.Scale(tab_color, from_=4.0, to=1.0, variable=self.color_min_aspect, orient=tk.HORIZONTAL, length=120).grid(row=row, column=4, padx=5)
        ttk.Label(tab_color, text="← More Aggressive", foreground='#888').grid(row=row, column=5, padx=5, sticky='w')

        ttk.Label(tab_color, text="Dilation:").grid(row=row, column=6, padx=15, pady=3, sticky='e')
        ttk.Scale(tab_color, from_=1, to=5, variable=self.color_dilation, orient=tk.HORIZONTAL, length=80).grid(row=row, column=7, padx=5)

        # ========== Tab 2: Black ==========
        tab_black = ttk.Frame(notebook)
        notebook.add(tab_black, text='⚫ Black')

        row = 0
        ttk.Label(tab_black, text="Black Threshold:", foreground='#aaaaaa').grid(row=row, column=0, padx=5, pady=3, sticky='e')
        ttk.Scale(tab_black, from_=30, to=120, variable=self.black_threshold, orient=tk.HORIZONTAL, length=120).grid(row=row, column=1, padx=5)
        ttk.Label(tab_black, text="More Aggressive →", foreground='#888').grid(row=row, column=2, padx=5, sticky='w')

        ttk.Label(tab_black, text="Intensity Diff:").grid(row=row, column=3, padx=15, pady=3, sticky='e')
        ttk.Scale(tab_black, from_=40, to=5, variable=self.black_intensity_diff, orient=tk.HORIZONTAL, length=120).grid(row=row, column=4, padx=5)
        ttk.Label(tab_black, text="← More Aggressive", foreground='#888').grid(row=row, column=5, padx=5, sticky='w')

        row = 1
        ttk.Label(tab_black, text="Min Aspect Ratio:").grid(row=row, column=0, padx=5, pady=3, sticky='e')
        ttk.Scale(tab_black, from_=4.0, to=1.0, variable=self.black_min_aspect, orient=tk.HORIZONTAL, length=120).grid(row=row, column=1, padx=5)
        ttk.Label(tab_black, text="← More Aggressive", foreground='#888').grid(row=row, column=2, padx=5, sticky='w')

        ttk.Label(tab_black, text="Dilation:").grid(row=row, column=3, padx=15, pady=3, sticky='e')
        ttk.Scale(tab_black, from_=1, to=5, variable=self.black_dilation, orient=tk.HORIZONTAL, length=80).grid(row=row, column=4, padx=5)

        # ========== Tab 3: White ==========
        tab_white = ttk.Frame(notebook)
        notebook.add(tab_white, text='⚪ White')

        row = 0
        ttk.Label(tab_white, text="White Threshold:", foreground='#ffffff').grid(row=row, column=0, padx=5, pady=3, sticky='e')
        ttk.Scale(tab_white, from_=230, to=140, variable=self.white_threshold, orient=tk.HORIZONTAL, length=120).grid(row=row, column=1, padx=5)
        ttk.Label(tab_white, text="← More Aggressive", foreground='#888').grid(row=row, column=2, padx=5, sticky='w')

        ttk.Label(tab_white, text="Intensity Diff:").grid(row=row, column=3, padx=15, pady=3, sticky='e')
        ttk.Scale(tab_white, from_=40, to=5, variable=self.white_intensity_diff, orient=tk.HORIZONTAL, length=120).grid(row=row, column=4, padx=5)
        ttk.Label(tab_white, text="← More Aggressive", foreground='#888').grid(row=row, column=5, padx=5, sticky='w')

        row = 1
        ttk.Label(tab_white, text="Min Aspect Ratio:").grid(row=row, column=0, padx=5, pady=3, sticky='e')
        ttk.Scale(tab_white, from_=4.0, to=1.0, variable=self.white_min_aspect, orient=tk.HORIZONTAL, length=120).grid(row=row, column=1, padx=5)
        ttk.Label(tab_white, text="← More Aggressive", foreground='#888').grid(row=row, column=2, padx=5, sticky='w')

        ttk.Label(tab_white, text="Dilation:").grid(row=row, column=3, padx=15, pady=3, sticky='e')
        ttk.Scale(tab_white, from_=1, to=5, variable=self.white_dilation, orient=tk.HORIZONTAL, length=80).grid(row=row, column=4, padx=5)

        # ========== Tab 4: Global ==========
        tab_global = ttk.Frame(notebook)
        notebook.add(tab_global, text='⚙️ Global')

        ttk.Label(tab_global, text="Inpaint Radius:").grid(row=0, column=0, padx=5, pady=3, sticky='e')
        ttk.Scale(tab_global, from_=3, to=10, variable=self.inpaint_radius, orient=tk.HORIZONTAL, length=120).grid(row=0, column=1, padx=5)
        ttk.Label(tab_global, text="Higher = More blending", foreground='#888').grid(row=0, column=2, padx=5, sticky='w')

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
        self.root.bind('<F5>', lambda e: self.reprocess_current())

    def show_debug_window(self):
        """Show a window with step-by-step visualization of the processing."""
        if not self.debug_steps:
            return

        # Close existing debug window if open
        if self.debug_window is not None:
            try:
                self.debug_window.destroy()
            except:
                pass

        self.debug_window = Toplevel(self.root)
        self.debug_window.title("Processing Steps Visualization")
        self.debug_window.geometry("1400x800")
        self.debug_window.configure(bg='#2b2b2b')

        # Create main frame with canvas and scrollbar
        main_frame = ttk.Frame(self.debug_window)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Canvas for scrolling
        canvas = tk.Canvas(main_frame, bg='#2b2b2b', highlightthickness=0)
        scrollbar_v = ttk.Scrollbar(main_frame, orient=tk.VERTICAL, command=canvas.yview)
        scrollbar_h = ttk.Scrollbar(main_frame, orient=tk.HORIZONTAL, command=canvas.xview)

        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar_v.set, xscrollcommand=scrollbar_h.set)

        # Grid layout
        scrollbar_v.pack(side=tk.RIGHT, fill=tk.Y)
        scrollbar_h.pack(side=tk.BOTTOM, fill=tk.X)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Calculate grid layout - 4 columns
        cols = 4
        thumb_size = 300

        # Store photo references to prevent garbage collection
        self.debug_photos = []

        for idx, (step_name, step_img) in enumerate(self.debug_steps):
            row = idx // cols
            col = idx % cols

            # Create frame for each step
            step_frame = ttk.Frame(scrollable_frame)
            step_frame.grid(row=row, column=col, padx=5, pady=5, sticky='n')

            # Label
            label = ttk.Label(step_frame, text=f"{idx+1}. {step_name}",
                            font=('Helvetica', 9, 'bold'), foreground='white', background='#2b2b2b')
            label.pack(pady=(0, 3))

            # Resize image for thumbnail
            h, w = step_img.shape[:2]
            scale = min(thumb_size / w, thumb_size / h)
            new_w, new_h = int(w * scale), int(h * scale)
            resized = cv2.resize(step_img, (new_w, new_h), interpolation=cv2.INTER_AREA)

            # Convert to PhotoImage
            rgb_img = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(rgb_img)
            photo = ImageTk.PhotoImage(pil_img)
            self.debug_photos.append(photo)

            # Image label
            img_label = ttk.Label(step_frame, image=photo)
            img_label.pack()

        # Enable mouse wheel scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # Info label at bottom
        info_label = ttk.Label(self.debug_window,
                              text=f"Total steps: {len(self.debug_steps)} | Scroll to see all",
                              font=('Helvetica', 10), foreground='#888', background='#2b2b2b')
        info_label.pack(side=tk.BOTTOM, pady=5)

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
        """Apply gamma correction. gamma > 1 brightens, gamma < 1 darkens."""
        if gamma == 1.0:
            return image
        invGamma = 1.0 / gamma
        table = np.array([((i / 255.0) ** invGamma) * 255
                          for i in np.arange(0, 256)]).astype("uint8")
        return cv2.LUT(image, table)

    def apply_bilateral(self, img, iterations=1):
        """Apply bilateral filter for noise/texture reduction."""
        x, y = self.center
        r = self.max_radius + 40
        x1, y1 = max(0, x - r), max(0, y - r)
        x2, y2 = min(img.shape[1], x + r), min(img.shape[0], y + r)

        roi = img[y1:y2, x1:x2].copy()
        for _ in range(iterations):
            roi = cv2.bilateralFilter(roi, 21, 30, 30)

        result = img.copy()
        result[y1:y2, x1:x2] = roi
        return result

    def detect_and_clean_step(self, img, mode='color'):
        """
        Single step: Warp Polar -> Detect -> Inpaint -> Warp Cartesian
        Mode: 'color' (Blue/Yellow), 'black', 'white'
        """
        # Get parameters based on mode
        if mode == 'color':
            color_thresh = self.color_threshold.get()
            min_aspect = self.color_min_aspect.get()
            dilation_iter = self.color_dilation.get()
        elif mode == 'black':
            black_thresh = self.black_threshold.get()
            intensity_thresh = self.black_intensity_diff.get()
            min_aspect = self.black_min_aspect.get()
            dilation_iter = self.black_dilation.get()
        elif mode == 'white':
            white_thresh = self.white_threshold.get()
            intensity_thresh = self.white_intensity_diff.get()
            min_aspect = self.white_min_aspect.get()
            dilation_iter = self.white_dilation.get()

        inpaint_rad = self.inpaint_radius.get()

        # 1. Warp to Polar
        polar_height = self.max_radius
        polar_width = int(2 * np.pi * self.max_radius)
        polar_img = cv2.warpPolar(img, (polar_width, polar_height), self.center,
                                  self.max_radius, cv2.WARP_POLAR_LINEAR)

        # 2. Create Masks based on mode
        mask = np.zeros(polar_img.shape[:2], dtype=np.uint8)
        gray = cv2.cvtColor(polar_img, cv2.COLOR_BGR2GRAY)

        if mode == 'color':
            # Blue/Yellow detection using Lab b-channel
            polar_lab = cv2.cvtColor(polar_img, cv2.COLOR_BGR2Lab)
            _, _, b_channel = cv2.split(polar_lab)
            _, blue_mask = cv2.threshold(b_channel, color_thresh, 255, cv2.THRESH_BINARY_INV)
            _, yellow_mask = cv2.threshold(b_channel, 140, 255, cv2.THRESH_BINARY)
            mask = cv2.bitwise_or(blue_mask, yellow_mask)

        elif mode == 'black':
            # Dark scribbles - structural + threshold
            h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (31, 1))
            bg = cv2.morphologyEx(gray, cv2.MORPH_OPEN, h_kernel)
            bg = cv2.morphologyEx(bg, cv2.MORPH_CLOSE, h_kernel)
            diff = cv2.absdiff(gray, bg)
            _, struct_mask = cv2.threshold(diff, intensity_thresh, 255, cv2.THRESH_BINARY)

            _, black_thresh_mask = cv2.threshold(gray, black_thresh, 255, cv2.THRESH_BINARY_INV)
            mask = cv2.bitwise_and(black_thresh_mask, struct_mask)

        elif mode == 'white':
            # Light scribbles - structural + threshold
            h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (31, 1))
            bg = cv2.morphologyEx(gray, cv2.MORPH_OPEN, h_kernel)
            bg = cv2.morphologyEx(bg, cv2.MORPH_CLOSE, h_kernel)
            diff = cv2.absdiff(gray, bg)
            _, struct_mask = cv2.threshold(diff, intensity_thresh, 255, cv2.THRESH_BINARY)

            _, white_thresh_mask = cv2.threshold(gray, white_thresh, 255, cv2.THRESH_BINARY)
            mask = cv2.bitwise_and(white_thresh_mask, struct_mask)

        # 3. Exclude inner region (below min_radius in polar = top rows)
        mask[0:self.min_radius, :] = 0

        # 4. Bolt / Aspect Ratio Protection
        if cv2.countNonZero(mask) > 0:
            num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
            cleaned_mask = np.zeros_like(mask)

            for i in range(1, num_labels):
                area = stats[i, cv2.CC_STAT_AREA]
                w = stats[i, cv2.CC_STAT_WIDTH]
                h = stats[i, cv2.CC_STAT_HEIGHT]
                if area < 10:
                    continue

                aspect = max(w, h) / (min(w, h) + 1.0)

                # Keep if elongated (scribble-like) OR large area
                if aspect > min_aspect or area > 800:
                    cleaned_mask[labels == i] = 255

            mask = cleaned_mask

        # 5. Warp mask back to Cartesian
        cart_mask = cv2.warpPolar(mask, (img.shape[1], img.shape[0]),
                                  self.center, self.max_radius,
                                  cv2.WARP_INVERSE_MAP | cv2.WARP_POLAR_LINEAR)

        _, cart_mask = cv2.threshold(cart_mask, 127, 255, cv2.THRESH_BINARY)

        # 6. Constrain to annular region
        annular = np.zeros((img.shape[0], img.shape[1]), dtype=np.uint8)
        cv2.circle(annular, self.center, self.max_radius, 255, -1)
        cv2.circle(annular, self.center, self.min_radius, 0, -1)
        cart_mask = cv2.bitwise_and(cart_mask, annular)

        # 7. Dilate mask (aggressiveness)
        if dilation_iter > 0:
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
            cart_mask = cv2.dilate(cart_mask, kernel, iterations=dilation_iter)

        # 8. Inpaint
        if cv2.countNonZero(cart_mask) > 0:
            img = cv2.inpaint(img, cart_mask, inpaint_rad, cv2.INPAINT_TELEA)

        return img

    def run_rotation_sequence(self, img, mode, mode_name=""):
        """
        Executes sequence: Polar removal at 9 angles (0°, 10°, 20°, ... 80°)
        then rotate back to original orientation.
        """
        current_img = img.copy()
        rotation_step = 10  # degrees
        num_rotations = 9

        for i in range(num_rotations):
            angle = i * rotation_step

            # Detect and clean at current angle
            current_img = self.detect_and_clean_step(current_img, mode)

            # Capture debug step
            self.debug_steps.append((f"{mode_name} @ {angle}°",
                                    self.create_circular_crop(current_img, self.center, self.max_radius)))

            # Rotate by 10° for next pass (except on last iteration)
            if i < num_rotations - 1:
                current_img = self.rotate_image(current_img, rotation_step)

        # Rotate back to original orientation: -(num_rotations-1)*step = -80°
        total_rotation = (num_rotations - 1) * rotation_step
        current_img = self.rotate_image(current_img, -total_rotation)

        return current_img

    def restore_hub_surface(self, image_path):
        """
        Sequential Recipe:
        a) Bilateral
        b) Gamma(3.0) - brighten
        c) Bilateral
        d-j) Blues/Yellows rotation sequence
        k) Blacks rotation sequence
        l) Gamma(1/3) - restore
        m) Gamma(0.5) - darken for white detection
        n) Whites rotation sequence
        o) Gamma(2.0) - restore
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

        # Clear debug steps
        self.debug_steps = []

        # === THE RECIPE ===
        working = img.copy()

        # Capture original
        self.debug_steps.append(("Original", self.create_circular_crop(working, self.center, self.max_radius)))

        # a) Do bilateral
        working = self.apply_bilateral(working, iterations=1)
        self.debug_steps.append(("a) Bilateral #1", self.create_circular_crop(working, self.center, self.max_radius)))

        # b) Do Gamma 3.0 (brighten)
        working = self.adjust_gamma(working, 3.0)
        self.debug_steps.append(("b) Gamma 3.0 (brighten)", self.create_circular_crop(working, self.center, self.max_radius)))

        # c) Do another bilateral
        working = self.apply_bilateral(working, iterations=1)
        self.debug_steps.append(("c) Bilateral #2", self.create_circular_crop(working, self.center, self.max_radius)))

        # d-j) Blues & Yellows
        working = self.run_rotation_sequence(working, 'color', "Blue/Yellow")
        self.debug_steps.append(("After Blue/Yellow removal", self.create_circular_crop(working, self.center, self.max_radius)))

        # k) Blacks
        working = self.run_rotation_sequence(working, 'black', "Black")
        self.debug_steps.append(("After Black removal", self.create_circular_crop(working, self.center, self.max_radius)))

        # l) Gamma 1/3 (restore original brightness)
        working = self.adjust_gamma(working, 1.0 / 3.0)
        self.debug_steps.append(("l) Gamma 1/3 (restore)", self.create_circular_crop(working, self.center, self.max_radius)))

        # m) Gamma 0.5 (darken to find whites)
        working = self.adjust_gamma(working, 0.5)
        self.debug_steps.append(("m) Gamma 0.5 (darken)", self.create_circular_crop(working, self.center, self.max_radius)))

        # n) Whites
        working = self.run_rotation_sequence(working, 'white', "White")
        self.debug_steps.append(("After White removal", self.create_circular_crop(working, self.center, self.max_radius)))

        # o) Gamma 2.0 (restore brightness)
        working = self.adjust_gamma(working, 2.0)
        self.debug_steps.append(("o) Gamma 2.0 (restore)", self.create_circular_crop(working, self.center, self.max_radius)))

        # Final: Only replace hub area
        annular = np.zeros((img.shape[0], img.shape[1]), dtype=np.uint8)
        cv2.circle(annular, self.center, self.max_radius, 255, -1)
        cv2.circle(annular, self.center, self.min_radius, 0, -1)

        result = img.copy()
        result[annular > 0] = working[annular > 0]

        cropped_result = self.create_circular_crop(result, self.center, self.max_radius)
        self.debug_steps.append(("FINAL Result", cropped_result))

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

        result = np.full_like(cropped, 40)
        result[mask > 0] = cropped[mask > 0]

        return result


def main():
    root = tk.Tk()
    app = HubRestorationApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()