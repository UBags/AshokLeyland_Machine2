"""
Batch Hexagon Nut Detection using MobileSAMv2 (PARALLEL - 2 WORKERS)
Non-GUI version for processing directories of images.

SAFE PARALLEL PROCESSING:
- Uses multiprocessing (not threading) for true parallelism
- Each worker process has its own model copy (1.2 GB each)
- 2 workers = 2.4 GB GPU memory (fits comfortably in 4 GB)
- Processes 2 images simultaneously
- Expected speedup: 2× faster than sequential

Memory usage: 2.4 GB / 4.0 GB (safe margin)

Usage:
    python IdentifyNutsInBatch.py <input_directory>
"""

import os
import sys
import glob
import time
import warnings
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
import argparse
from multiprocessing import Process, Queue, Value, Lock
import queue

# Project utilities
from BaseUtils import get_project_root

# Import ModelManager for shared model access
from camera.ModelManager import ModelManager

PRINT_DETAIL: bool = False

# --- Add MobileSAM to path ---
_PROJECT_ROOT = get_project_root()
_MOBILESAM_DIR = os.path.join(_PROJECT_ROOT, "models", "MobileSAM")
if os.path.exists(_MOBILESAM_DIR) and _MOBILESAM_DIR not in sys.path:
    sys.path.append(_MOBILESAM_DIR)

# Suppress annoying warnings from external libraries
warnings.filterwarnings("ignore", category=FutureWarning, module="timm")
warnings.filterwarnings("ignore", category=UserWarning, message="Overwriting .* in registry")

import numpy as np
import torch
import cv2

# Import image normalization methods from shared module
from utils.ImageNormalisationWithMask import (
    rgb2gray,
    ensure_float32,
    create_annular_mask,
    extract_annular_region,
    pixBackgroundNorm_masked,
    pixGammaCorrection_masked
)

# ANSI color codes for terminal output
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    BOLD = '\033[1m'


# =============================================================================
# OPTIMIZED MobileSAMv2 Mask Generator
# =============================================================================

class MobileSAMv2AutomaticMaskGenerator:
    """MobileSAMv2 Automatic Mask Generator (OPTIMIZED)."""

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
        self.predictor = sam_predictor
        self.yolo_model = yolo_model
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.min_mask_region_area = min_mask_region_area
        self.max_mask_region_area = max_mask_region_area
        self.use_grid_points = use_grid_points
        self.points_per_side = points_per_side

    def generate(self, image: np.ndarray) -> List[Dict[str, Any]]:
        """Generate masks for all objects in the image."""
        masks = []
        self.predictor.set_image(image)

        try:
            results = self.yolo_model.predict(
                image,
                conf=self.conf_threshold,
                iou=self.iou_threshold,
                verbose=False
            )

            boxes = []
            if results and len(results) > 0:
                result = results[0]
                if hasattr(result, 'boxes') and result.boxes is not None:
                    boxes_data = result.boxes.xyxy.cpu().numpy()
                    boxes = [box for box in boxes_data]

            for box in boxes:
                x1, y1, x2, y2 = box
                box_prompt = np.array([x1, y1, x2, y2])

                try:
                    pred_masks, scores, _ = self.predictor.predict(
                        box=box_prompt,
                        multimask_output=False
                    )

                    if len(scores) > 0:
                        mask = pred_masks[0]
                        area = mask.sum()

                        if self.min_mask_region_area <= area <= self.max_mask_region_area:
                            masks.append({
                                'segmentation': mask,
                                'area': int(area),
                                'bbox': self._mask_to_bbox(mask),
                                'predicted_iou': float(scores[0]),
                                'stability_score': float(scores[0]),
                            })
                except:
                    pass

        except:
            pass

        if self.use_grid_points and len(masks) < 5:
            grid_masks = self._generate_grid_masks(image)
            for gm in grid_masks:
                if not self._overlaps_existing(gm['segmentation'], masks):
                    area = gm['area']
                    if self.min_mask_region_area <= area <= self.max_mask_region_area:
                        masks.append(gm)

        return masks

    def _generate_grid_masks(self, image: np.ndarray) -> List[Dict[str, Any]]:
        """Generate masks using grid point prompts (OPTIMIZED)."""
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
                except:
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
# Worker Process Function (runs in separate process)
# =============================================================================

def worker_process(worker_id: int, task_queue: Queue, result_queue: Queue,
                   mobile_sam_path: str, yolo_path: str):
    """
    Worker process that loads its own model copy and processes preprocessing variations.

    Each worker has its own:
    - SAM model (~600 MB GPU)
    - YOLO model (~600 MB GPU)
    - Total: ~1.2 GB per worker

    Args:
        worker_id: Unique worker identifier
        task_queue: Queue to receive (preprocessed_img, params_desc, center_x, center_y)
        result_queue: Queue to send results
        mobile_sam_path: Path to MobileSAM checkpoint
        yolo_path: Path to YOLO checkpoint
    """
    try:
        # Import here to ensure clean process state
        from mobile_sam import sam_model_registry, SamPredictor
        from ultralytics import YOLO

        print(f"[Worker {worker_id}] Initializing...")

        # Load models for this worker
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        # Load SAM
        sam_model = sam_model_registry["vit_t"](checkpoint=mobile_sam_path)
        sam_model.to(device=device)
        sam_model.eval()
        sam_predictor = SamPredictor(sam_model)

        # Load YOLO
        yolo_model = YOLO(yolo_path, task='detect')
        yolo_model.to(device)

        # Create mask generator
        mask_generator = MobileSAMv2AutomaticMaskGenerator(
            sam_predictor=sam_predictor,
            yolo_model=yolo_model,
            conf_threshold=0.3,
            iou_threshold=0.7,
            min_mask_region_area=3800,
            max_mask_region_area=5200,
            use_grid_points=True,
            points_per_side=32
        )

        print(f"[Worker {worker_id}] Ready (GPU memory: ~1.2 GB)")

        # Process variations from queue
        while True:
            try:
                # Get task from queue (with timeout to allow checking for shutdown)
                task = task_queue.get(timeout=1)

                if task is None:  # Poison pill to shut down
                    print(f"[Worker {worker_id}] Shutting down...")
                    break

                # Task is (batch_idx, preprocessed_img, params_desc, center_x, center_y)
                batch_idx, preprocessed_img, params_desc, center_x, center_y = task

                # Process the variation
                result = process_single_variation(
                    batch_idx, preprocessed_img, params_desc,
                    center_x, center_y, mask_generator, worker_id
                )

                # Send result back
                result_queue.put(result)

                # Clear GPU cache
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

            except queue.Empty:
                continue
            except Exception as e:
                print(f"[Worker {worker_id}] Error: {e}")
                result_queue.put((batch_idx, False, None, None, None, None))

    except Exception as e:
        print(f"[Worker {worker_id}] Fatal error during initialization: {e}")
        import traceback
        traceback.print_exc()


def process_single_variation(batch_idx: int, preprocessed_img: np.ndarray,
                            params_desc: str, center_x: int, center_y: int,
                            mask_generator, worker_id: int):
    """
    Process a single preprocessing variation.

    Returns:
        tuple: (batch_idx, hexagon_found, params_desc, mask_area, mask_dims, mask_verts)
    """
    try:
        # Convert to RGB
        if preprocessed_img.ndim == 2:
            preprocessed_rgb = cv2.cvtColor(preprocessed_img, cv2.COLOR_GRAY2RGB)
        else:
            preprocessed_rgb = preprocessed_img

        # Generate masks
        with torch.inference_mode():
            masks = mask_generator.generate(preprocessed_rgb)

        if not masks:
            return (batch_idx, False, params_desc, None, None, None)

        # Find center-overlapping masks
        center_masks = []
        for idx, mask_data in enumerate(masks):
            mask = mask_data['segmentation']
            if mask[int(center_y), int(center_x)]:
                center_masks.append((idx, mask_data))

        if not center_masks:
            return (batch_idx, False, params_desc, None, None, None)

        # Get mask info
        idx, mask_data = center_masks[0]
        mask = mask_data['segmentation']
        mask_area = mask_data.get('area', 0)

        contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if contours:
            x, y, w, h = cv2.boundingRect(max(contours, key=cv2.contourArea))
            mask_dimensions = (h, w)

            contour = max(contours, key=cv2.contourArea)
            epsilon = 0.02 * cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, epsilon, True)

            # Clean polygon
            cleaned_vertices = clean_polygon(approx)
            num_vertices = len(cleaned_vertices)

            # Check if hexagonal
            if 5 <= num_vertices <= 8:
                aspect_ratio = float(w) / h if h > 0 else 0
                if 0.7 < aspect_ratio < 1.3:
                    # Hexagon found!
                    return (batch_idx, True, params_desc, mask_area, mask_dimensions, num_vertices)

            mask_vertices = num_vertices
        else:
            mask_dimensions = (0, 0)
            mask_vertices = 0

        return (batch_idx, False, params_desc, mask_area, mask_dimensions, mask_vertices)

    except Exception as e:
        return (batch_idx, False, params_desc, None, None, None)


def create_preprocessed_batch(image_rgb, mask, batch_bg_sx, batch_bg_sy, batch_gamma_base):
    """Create batch of 6 preprocessed images."""
    batch = []
    gamma_values = [batch_gamma_base, batch_gamma_base + 0.5, batch_gamma_base + 1.0]
    bg_variations = [(batch_bg_sx, batch_bg_sy), (batch_bg_sx - 5, batch_bg_sy - 5)]

    for bg_sx, bg_sy in bg_variations:
        for gamma in gamma_values:
            img = pixBackgroundNorm_masked(image_rgb, mask, sx=bg_sx, sy=bg_sy, bgval=200)
            img = pixGammaCorrection_masked(img, mask, gamma=gamma)
            desc = f"bg=({bg_sx},{bg_sy}); gamma={gamma}"
            batch.append((img, desc))

    return batch


def clean_polygon(vertices, edge_threshold=0.15, corner_threshold=0.2):
    """Simplified polygon cleaning."""
    if len(vertices.shape) == 3:
        vertices = vertices.reshape(-1, 2)

    # Just return simplified vertices for now
    # (Full implementation from HexagonNutDetector can be added if needed)
    return vertices.reshape(-1, 1, 2)


# =============================================================================
# Main Batch Processor
# =============================================================================

class ParallelHexagonNutDetector:
    """Parallel batch processor using 3 worker processes."""

    def __init__(self, num_workers: int = 3):
        """
        Initialize parallel detector.

        Args:
            num_workers: Number of parallel workers (default: 3)
                        Each worker uses ~1.2 GB GPU memory
                        Total: 3.6 GB (fits in 4.0 GB)
        """
        self.num_workers = num_workers
        self.model_manager = ModelManager.get_instance()

        # Get model paths
        self.mobile_sam_path = os.path.join(
            _PROJECT_ROOT, "models", "MobileSAM", "weights", "mobile_sam.pt"
        )
        self.yolo_path = os.path.join(
            _PROJECT_ROOT, "models", "MobileSAM", "MobileSAMv2", "weight", "ObjectAwareModel.pt"
        )

    def process_directory(self, input_dir: str):
        """Process all images in directory, processing 2 variations in parallel per image."""
        # Find all image files
        image_extensions = ['*.jpg', '*.jpeg', '*.png', '*.bmp', '*.tif', '*.tiff']
        image_files = []
        for ext in image_extensions:
            image_files.extend(glob.glob(os.path.join(input_dir, ext)))
            image_files.extend(glob.glob(os.path.join(input_dir, ext.upper())))

        image_files = list(set(image_files))
        image_files.sort()

        if not image_files:
            print(f"{Colors.RED}No image files found in: {input_dir}{Colors.RESET}")
            return

        total = len(image_files)

        print(f"\n{Colors.BOLD}Processing {total} images{Colors.RESET}")
        print(f"Strategy: Process 3 preprocessing variations in parallel per image")
        print(f"Expected GPU memory: {self.num_workers * 1.2:.1f} GB / 4.0 GB")
        print("=" * 70)

        # Create queues
        task_queue = Queue()
        result_queue = Queue()

        # Start worker processes (they stay alive for entire batch)
        workers = []
        for worker_id in range(self.num_workers):
            p = Process(
                target=worker_process,
                args=(worker_id, task_queue, result_queue, self.mobile_sam_path, self.yolo_path)
            )
            p.start()
            workers.append(p)

        # Wait for workers to initialize
        time.sleep(2)

        # Process each image
        detected_count = 0
        error_count = 0
        batch_start_time = time.time()

        for file_path in image_files:
            filename = os.path.basename(file_path)
            file_start_time = time.time()

            try:
                # Load image
                image_bgr = cv2.imread(file_path)
                if image_bgr is None:
                    print(f"{filename}: {Colors.RED}ERROR{Colors.RESET} (Failed to load); 0.00s")
                    error_count += 1
                    continue

                image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)

                # Extract annular region
                annular_image, annular_mask = extract_annular_region(
                    image_rgb,
                    center=(632, 360),
                    outer_radius=80,
                    inner_radius=0,
                    fill_color=0
                )

                # Create batch of 6 preprocessing variations
                batch = create_preprocessed_batch(
                    annular_image, annular_mask,
                    batch_bg_sx=15, batch_bg_sy=15, batch_gamma_base=2.0
                )

                center_x = 80
                center_y = 80

                # Process variations in groups of 3 (0-1-2, 3-4-5) with early termination
                hexagon_found = False
                last_mask_area = None
                last_mask_dimensions = (0, 0)
                last_mask_vertices = 0
                successful_params = None

                for group_start in range(0, 6, 3):  # 0, 3
                    # Submit 3 variations to workers
                    num_submitted = 0
                    for i in range(3):
                        batch_idx = group_start + i
                        if batch_idx < len(batch):
                            preprocessed_img, params_desc = batch[batch_idx]
                            task_queue.put((batch_idx, preprocessed_img, params_desc, center_x, center_y))
                            num_submitted += 1

                    # Collect results
                    results = []
                    for i in range(num_submitted):
                        result = result_queue.get()
                        results.append(result)

                    # Check if any variation found hexagon
                    for batch_idx, found, params_desc, mask_area, mask_dims, mask_verts in results:
                        # Update last known mask info
                        if mask_area is not None:
                            last_mask_area = mask_area
                            last_mask_dimensions = mask_dims
                            last_mask_vertices = mask_verts

                        if found:
                            hexagon_found = True
                            successful_params = params_desc
                            break

                    # Early termination if hexagon found
                    if hexagon_found:
                        break

                file_elapsed = time.time() - file_start_time

                # Print result
                if hexagon_found:
                    dims_str = f"({last_mask_dimensions[0]}, {last_mask_dimensions[1]})" if last_mask_dimensions else "N/A"
                    print(
                        f"{filename}: {Colors.GREEN}{Colors.BOLD}YES{Colors.RESET}; "
                        f"Area={last_mask_area}, Dims={dims_str}, Vertices={last_mask_vertices}; "
                        f"{successful_params}; {file_elapsed:.2f}s"
                    )
                    detected_count += 1
                else:
                    print(f"{filename}: {Colors.RED}NO{Colors.RESET}; {file_elapsed:.2f}s")

            except Exception as e:
                file_elapsed = time.time() - file_start_time
                print(f"{filename}: {Colors.RED}ERROR{Colors.RESET} ({e}); {file_elapsed:.2f}s")
                error_count += 1

        # Shutdown workers
        for _ in range(self.num_workers):
            task_queue.put(None)

        for p in workers:
            p.join()

        elapsed = time.time() - batch_start_time

        # Summary
        print("=" * 70)
        print(f"\n{Colors.BOLD}Summary:{Colors.RESET}")
        print(f"  Total images:      {total}")
        print(f"  Hexagons detected: {Colors.GREEN}{detected_count}{Colors.RESET}")
        print(f"  Not detected:      {total - detected_count - error_count}")
        print(f"  Errors:            {Colors.RED}{error_count}{Colors.RESET}")
        print(f"  Processing time:   {elapsed:.2f}s ({elapsed / total:.2f}s per image)")
        print(f"  Parallel speedup:  ~2-2.5× with {self.num_workers} workers")
        print()


def main():
    """Main entry point."""
    print("=" * 70)
    print("Batch Hexagon Nut Detection - MobileSAMv2 (PARALLEL - 3 WORKERS)")
    print("Using multiprocessing with 3 workers")
    print("Strategy: Process 3 preprocessing variations in parallel per image")
    print("Each worker: ~1.2 GB GPU | Total: ~3.6 GB / 4.0 GB")
    print("Expected speedup: 2-2.5× faster (early termination on round 1 or 2)")
    print("=" * 70)

    input_dir = "C:/AshokLeylandImages/DOSTPLUS/LHS"

    if torch.cuda.is_available():
        gpu_name = torch.cuda.get_device_name(0)
        gpu_mem = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
        print(f"GPU: {gpu_name} ({gpu_mem:.1f}GB)")
    else:
        print("WARNING: CUDA not available, using CPU (will be slow)")

    try:
        # Create parallel detector with 3 workers
        detector = ParallelHexagonNutDetector(num_workers=3)
        detector.process_directory(input_dir)

    except Exception as e:
        print(f"\n{Colors.RED}ERROR: {e}{Colors.RESET}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()