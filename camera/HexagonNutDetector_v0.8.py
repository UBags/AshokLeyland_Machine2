"""
HexagonNutDetector Class
A comprehensive detector for hexagonal nuts using MobileSAMv2 and YOLO.

Uses shared ModelManager for GPU memory efficiency.
"""

import os
import sys
import warnings
from typing import List, Dict, Any, Tuple, Optional

import numpy as np
import torch
import cv2

# Import image normalization methods
from utils.ImageNormalisationWithMask import (
    rgb2gray,
    ensure_float32,
    create_annular_mask,
    extract_annular_region,
    pixBackgroundNorm_masked,
    pixContrastNorm_masked
)

# Import shared model manager
from camera.ModelManager import ModelManager

# Debug print flag (set to True for detailed logging)
PRINT_DETAIL = False


# =============================================================================
# MOBILESAM AUTOMATIC MASK GENERATOR
# =============================================================================

class MobileSAMv2AutomaticMaskGenerator:
    """
    MobileSAMv2 Automatic Mask Generator.

    Uses YOLOv8 (ObjectAwareModel) for object detection to get bounding boxes,
    then MobileSAM predictor to generate masks for each detected object.
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

        # Set image for SAM predictor
        self.predictor.set_image(image)

        # Run YOLO object detection
        try:
            results = self.yolo_model.predict(
                image,
                conf=self.conf_threshold,
                iou=self.iou_threshold,
                verbose=False
            )

            if results and len(results) > 0:
                result = results[0]

                if hasattr(result, 'boxes') and result.boxes is not None:
                    boxes = result.boxes.xyxy.cpu().numpy()

                    for box in boxes:
                        try:
                            # Convert box to input format for SAM
                            box_input = box.reshape(1, 4)

                            # Generate mask for this box
                            mask_pred, scores, _ = self.predictor.predict(
                                box=box_input,
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
        except Exception:
            pass

        # Add grid-based masks if enabled
        if self.use_grid_points:
            grid_masks = self._generate_grid_masks(image)

            # Filter out duplicates
            for grid_mask in grid_masks:
                if not self._overlaps_existing(grid_mask['segmentation'], masks):
                    masks.append(grid_mask)

        return masks

    def _generate_grid_masks(self, image: np.ndarray) -> List[Dict[str, Any]]:
        """Generate masks using a grid of points."""
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


# =============================================================================
# HEXAGON NUT DETECTOR
# =============================================================================

class HexagonNutDetector:
    """
    Hexagon Nut Detector using MobileSAMv2 via shared ModelManager.

    Detects hexagonal nuts in images using:
    - Annular region extraction
    - Background normalization
    - Gamma correction
    - Bilateral filtering
    - MobileSAMv2 segmentation
    - Hexagon polygon detection
    """

    def __init__(self, mobile_sam_path: str = None, yolo_path: str = None, auto_load : bool = True):
        """
        Initialize the detector using shared ModelManager.

        Args:
            mobile_sam_path: Ignored (kept for backward compatibility)
            yolo_path: Ignored (kept for backward compatibility)
        """
        # Get shared model manager
        self.model_manager = ModelManager.get_instance()
        self.device = self.model_manager.get_device()

        print(f"[HexagonNutDetector] Using shared ModelManager on device: {self.device}")

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

        # Models (will be loaded from ModelManager)
        self.sam_model = None
        self.sam_predictor = None
        self.yolo_model = None
        self.mask_generator = None

        # Precompute gamma LUT
        self.gamma_lut = self._create_gamma_lut(self.gamma_value)

        if auto_load:
            self.load_models()

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
        """Load MobileSAM and YOLO models from shared ModelManager."""
        print("\n" + "=" * 70)
        print("Loading Models via ModelManager...")
        print("=" * 70)

        try:
            # Get models from shared ModelManager (loads if needed)
            self.sam_predictor = self.model_manager.get_sam_predictor()
            self.yolo_model = self.model_manager.get_yolo_model()
            self.sam_model = self.sam_predictor.model

            print(f"✓ Models loaded from ModelManager on {self.device}")

            # Create mask generator
            self._rebuild_mask_generator()

            print("=" * 70 + "\n")

        except Exception as e:
            print(f"ERROR loading models: {e}")
            import traceback
            traceback.print_exc()
            raise

    def _rebuild_mask_generator(self):
        """Rebuild mask generator with current parameters (exact as in original)."""
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

    # =========================================================================
    # POLYGON CLEANING HELPER METHODS
    # =========================================================================

    def _compute_edge_lengths(self, vertices: np.ndarray) -> List[float]:
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

    def _remove_small_edges(self, vertices: np.ndarray, threshold_ratio: float = 0.15) -> np.ndarray:
        """
        Remove small edges and merge their corners.

        Args:
            vertices: Polygon vertices (N, 1, 2) or (N, 2)
            threshold_ratio: Small edge threshold as ratio of median length

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

    def _remove_sharp_corners(self, vertices: np.ndarray, min_edge_ratio: float = 0.2) -> np.ndarray:
        """
        Remove corners formed by at least one very small edge.

        Args:
            vertices: Polygon vertices (N, 1, 2) or (N, 2)
            min_edge_ratio: Minimum edge ratio to keep corner

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
                if PRINT_DETAIL:
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

    def _clean_polygon(self, vertices: np.ndarray,
                      edge_threshold: float = 0.15,
                      corner_threshold: float = 0.2) -> np.ndarray:
        """
        Clean polygon by removing small edges and sharp corners.

        Args:
            vertices: Polygon vertices
            edge_threshold: Small edge threshold ratio
            corner_threshold: Sharp corner threshold ratio

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

    # =========================================================================
    # HEXAGON DETECTION
    # =========================================================================

    def _detect_hexagon(self, mask_data: Dict[str, Any],
                       center_x: int, center_y: int, idx: int) -> bool:
        """
        Detect if mask contains a hexagonal nut at center.

        Args:
            mask_data: Mask dictionary with 'segmentation', 'area', etc.
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

        # Check if approximately hexagonal (5-9 vertices is acceptable)
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
                # Use bounding box dimensions from the contour (already computed as x, y, w, h above)
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
                print(f"  ✗ Mask {idx} has {num_vertices} vertices (need 5-9)")

        return False

    def detect_hexagon(self, mask_data: Dict[str, Any],
                      center_x: int, center_y: int) -> Tuple[bool, Optional[Tuple], Optional[np.ndarray]]:
        """
        Detect if mask contains a hexagonal nut at center (public API).

        Args:
            mask_data: Mask dictionary with 'segmentation', 'area', etc.
            center_x: Center X coordinate
            center_y: Center Y coordinate

        Returns:
            Tuple of (is_hexagon: bool, center: Optional[Tuple], contour: Optional[np.ndarray])
        """
        mask = mask_data['segmentation'].astype(np.uint8)

        # Check if mask overlaps with center
        if not mask[int(center_y), int(center_x)]:
            return False, None, None

        # Find contours
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if not contours:
            return False, None, None

        # Get largest contour
        contour = max(contours, key=cv2.contourArea)

        # Initial approximation
        epsilon = 0.02 * cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, epsilon, True)

        # Clean the polygon
        cleaned_vertices = self._clean_polygon(approx, edge_threshold=0.15, corner_threshold=0.2)

        num_vertices = len(cleaned_vertices)

        # Check if approximately hexagonal (5-9 vertices is acceptable)
        if 5 <= num_vertices <= 9:
            # Check aspect ratio
            x, y, w, h = cv2.boundingRect(contour)
            aspect_ratio = float(w) / h if h > 0 else 0

            # Hexagons should have aspect ratio close to 1
            if 0.7 < aspect_ratio < 1.3:
                # Capture mask information
                self.last_mask_area = mask_data.get('area', 0)
                self.last_mask_dimensions = (h, w)  # (h, w) of bounding box
                self.last_mask_vertices = num_vertices

                # Calculate center
                M = cv2.moments(contour)
                if M["m00"] != 0:
                    cx = int(M["m10"] / M["m00"])
                    cy = int(M["m01"] / M["m00"])
                    center = (cx, cy)
                else:
                    center = (center_x, center_y)

                return True, center, contour

        return False, None, None

    # =========================================================================
    # IMAGE PREPROCESSING
    # =========================================================================

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

    # =========================================================================
    # MASK GENERATION
    # =========================================================================

    def generateMask(self, image_for_sam: np.ndarray) -> List[Dict[str, Any]]:
        """
        Generate masks using MobileSAMv2.

        Args:
            image_for_sam: Preprocessed RGB image

        Returns:
            List of mask dictionaries
        """
        with torch.inference_mode():
            masks = self.mask_generator.generate(image_for_sam)

        return masks

    # =========================================================================
    # MAIN PROCESSING METHOD
    # =========================================================================

    def process_image(self, file_path: str) -> Tuple[bool, bool, str, Optional[int], Optional[Tuple], Optional[int]]:
        """
        Process a single image file.

        Args:
            file_path: Path to image file

        Returns:
            tuple: (success: bool, detected: bool, message: str,
                   mask_area: Optional[int], mask_dimensions: Optional[Tuple],
                   mask_vertices: Optional[int])
        """
        try:
            # Load image
            image_bgr = cv2.imread(file_path)
            if image_bgr is None:
                return False, False, "Failed to load image", None, None, None

            # Convert BGR to RGB
            image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)

            # Preprocess
            image_for_sam = self.preprocess_image(image_rgb)

            # Generate masks
            with torch.inference_mode():
                masks = self.mask_generator.generate(image_for_sam)

            if not masks:
                return True, False, "No masks generated", None, None, None

            # Center coordinates (same as GUI)
            center_x = self.annular_outer_radius
            center_y = self.annular_outer_radius

            # Find masks overlapping with center (same logic as GUI)
            center_masks = []
            for idx, mask_data in enumerate(masks):
                mask = mask_data['segmentation']
                # Check if mask contains the center point
                if mask[int(center_y), int(center_x)]:
                    center_masks.append((idx, mask_data))

            if not center_masks:
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
            hexagon_found = False

            for idx, mask_data in center_masks:
                if self._detect_hexagon(mask_data, center_x, center_y, idx):
                    hexagon_found = True
                    break  # Found the nut, stop searching

            return True, hexagon_found, "OK", self.last_mask_area, self.last_mask_dimensions, self.last_mask_vertices

        except Exception as e:
            return False, False, str(e), None, None, None

    def processImage(self, image_rgb: np.ndarray) -> Tuple[
        bool, Optional[Tuple], Optional[np.ndarray], Optional[np.ndarray]]:
        """
        Process image and detect hexagonal nut.

        This is a convenience method that takes a BGR image directly.

        Args:
            image_rgb: Input BGR image from cv2.imread

        Returns:
            Tuple of (is_detected: bool, center: Optional[Tuple], contour: Optional[np.ndarray], mask: Optional[np.ndarray])
            - is_detected: True if hexagon detected, False otherwise
            - center: (cx, cy) coordinates of hexagon center if detected
            - contour: Contour points of hexagon if detected
            - mask: Binary segmentation mask if detected
        """
        # Convert BGR to RGB
        # image_rgb = cv2.cvtColor(image_rgb, cv2.COLOR_BGR2RGB)

        # Preprocess image
        image_for_sam = self.preprocess_image(image_rgb)

        # Generate masks
        masks = self.generateMask(image_for_sam)

        if not masks:
            return False, None, None, None

        # Center coordinates (relative to preprocessed image)
        center_x = self.annular_outer_radius
        center_y = self.annular_outer_radius

        # Find masks overlapping with center
        for mask_data in masks:
            mask = mask_data['segmentation']

            # Check if mask contains the center point
            if mask[int(center_y), int(center_x)]:
                # Try to detect hexagon in this mask
                is_hexagon, center, contour = self.detect_hexagon(
                    mask_data, center_x, center_y
                )

                if is_hexagon:
                    return True, center, contour, mask

        # No hexagon found
        return False, None, None, None

# =============================================================================
# EXAMPLE USAGE
# =============================================================================

# if __name__ == "__main__":
#     # Initialize detector (uses shared ModelManager)
#     detector = HexagonNutDetector()
#
#     # Load models
#     detector.load_models()
#
#     # Example 1: Process from file path
#     test_image_path = "path/to/test/image.jpg"
#     if os.path.exists(test_image_path):
#         success, detected, message, area, dims, verts = detector.process_image(test_image_path)
#
#         if success and detected:
#             print(f"✓ Hexagon detected!")
#             print(f"  Area: {area}")
#             print(f"  Dimensions: {dims}")
#             print(f"  Vertices: {verts}")
#         elif success:
#             print(f"✗ No hexagon detected: {message}")
#         else:
#             print(f"✗ Error: {message}")
#
#     # Example 2: Process from BGR image
#     test_image_path = "path/to/test/image.jpg"
#     if os.path.exists(test_image_path):
#         image_bgr = cv2.imread(test_image_path)
#
#         is_detected, center, contour = detector.processImage(image_bgr)
#
#         if is_detected:
#             print(f"✓ Hexagon detected!")
#             print(f"  Center: {center}")
#             print(f"  Contour points: {len(contour)}")
#         else:
#             print("✗ No hexagon detected")