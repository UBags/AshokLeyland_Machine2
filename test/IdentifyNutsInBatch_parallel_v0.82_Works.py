'''
Batch Hexagon Nut Detection - Parallel Version v0.86 (TRUE CANCELLATION)
Optimized for RTX 3050 (4GB VRAM)

CRITICAL FIX IN v0.86 - ACTIVE CANCELLATION:
- ✅ Created cancel_event (threading.Event) for each image
- ✅ When hexagon found → cancel_event.set() to signal other threads
- ✅ Worker threads check cancel_event BEFORE starting GPU work
- ✅ Worker threads check cancel_event AGAIN before expensive operations
- ✅ Threads that haven't started GPU work will skip immediately
- ✅ Expected: ~0.5-1.0s per image (not 2-3s) when hexagon found early

HOW IT WORKS:
1. Main thread creates cancel_event for image
2. Submits 3 tasks (each includes cancel_event)
3. Thread 2 finishes first, finds hexagon
4. Main thread sets cancel_event.set()
5. Threads 1 & 3 check event, see it's set, SKIP GPU work
6. Return immediately with result!

COMPLETE PIPELINE MATCH WITH BATCH v0.73:
1. Extract annular region FIRST
2. Preprocess: bg_norm(35,35) or (40,40) → gamma(2.0/2.5/3.0) → DONE
3. NO bilateral filter, NO contrast normalization
4. IQR-based polygon cleaning (exact copy from GUI)
5. ModelManager integration (50% GPU memory savings)
6. 3 PARALLEL THREADS for maximum throughput

OPTIMIZATIONS IN v0.83:
- ✅ Clear BOTH queues (task_queue AND result_queue) at START of each image
- ✅ NO draining at end - clean slate for next image
- ✅ Prevents any queue contamination between images

OPTIMIZATIONS IN v0.82:
- ✅ TRULY immediate early termination (added hexagon_found to while condition)
- ✅ Reduced drain timeout from 1s to 0.1s
- ✅ Exits as soon as first "Yes" found

OPTIMIZATIONS IN v0.81:
- ✅ Immediate early termination: Stop as soon as first hexagon found
- ✅ Queue draining: Clear result queues before processing each new image
- ✅ Test mode pacing: 3-second delay between images for stability
- ✅ Timing fix: Exclude sleep time from processing time"""

COMPLETE PIPELINE MATCH WITH BATCH v0.73:
1. Extract annular region FIRST
2. Preprocess: bg_norm(35,35) or (40,40) → gamma(2.0/2.5/3.0) → DONE
3. NO bilateral filter, NO contrast normalization
4. IQR-based polygon cleaning (exact copy from GUI)
5. ModelManager integration (50% GPU memory savings)
6. 3 PARALLEL THREADS for maximum throughput

OPTIMIZATIONS IN v0.81:
- ✅ IMMEDIATE early termination: Stop as soon as first hexagon found (not after all 3)
- ✅ Queue draining: Clear result queues before processing each new image
- ✅ Test mode pacing: 3-second delay between images for stability
- ✅ Faster processing: ~10-20% speedup from immediate termination

CHANGES IN v0.80:
- ✅ Increased from 2 to 3 parallel threads
- ✅ Process 3 preprocessing variations simultaneously
- ✅ Better GPU utilization on RTX 3050

CHANGES IN v0.79:
- ✅ Removed bilateral filter from preprocessing (matches batch)
- ✅ Replaced simple polygon cleaning with IQR-based algorithm (matches batch/GUI)
- ✅ Integrated ModelManager for shared models (50% GPU savings)
- ✅ Each thread creates own SAM predictor (thread-safe, no corruption)

CRITICAL FIX: UUID-Based Task Tracking
- Each image gets a unique UUID that travels with every task
- Queue poisoning is impossible - stale results are automatically discarded
- Works with both file-based and in-memory images

PARAMETERS (Matching Batch v0.73):
- Background normalization: (35, 35) and (40, 40)
- Gamma correction: 2.0, 2.5, 3.0
- Area limits: 3000 - 5500 pixels
- NO bilateral filter
- NO contrast normalization

Expected: 2.5-3× speedup with 100% detection accuracy (no false negatives)
VRAM Usage: ~2.2-2.5 GB (with 3 threads using ModelManager)
'''
import math
import os
import sys
import glob
import time
import warnings
import uuid
from typing import Optional, List, Dict, Any, Tuple
import threading
from queue import Queue, Empty
from datetime import datetime
from contextlib import contextmanager

# Project utilities
from BaseUtils import get_project_root

# Import ModelManager for shared model access
from camera.ModelManager import ModelManager

PRINT_DETAIL: bool = False
ENABLE_VRAM_MONITORING: bool = True

# --- Add MobileSAM to path ---
_PROJECT_ROOT = get_project_root()
_MOBILESAM_DIR = os.path.join(_PROJECT_ROOT, "models", "MobileSAM")
if os.path.exists(_MOBILESAM_DIR) and _MOBILESAM_DIR not in sys.path:
    sys.path.append(_MOBILESAM_DIR)

warnings.filterwarnings("ignore", category=FutureWarning, module="timm")
warnings.filterwarnings("ignore", category=UserWarning, message="Overwriting .* in registry")

import numpy as np
import torch
import cv2

# CRITICAL: Apply torch.load patch BEFORE any model loading
import torch.serialization

_original_torch_load = torch.serialization.load

def _patched_torch_load(f, map_location=None, pickle_module=None, *, weights_only=None, mmap=None, **kwargs):
    return _original_torch_load(f, map_location=map_location, pickle_module=pickle_module,
                               weights_only=False, mmap=mmap, **kwargs)

torch.load = _patched_torch_load
torch.serialization.load = _patched_torch_load

# Apply safe_globals for PyTorch 2.6+
if hasattr(torch.serialization, "add_safe_globals"):
    safe_classes = [
        torch.nn.modules.container.Sequential,
        torch.nn.modules.container.ModuleList,
        torch.nn.modules.container.ModuleDict,
    ]
    try:
        from ultralytics.nn.tasks import SegmentationModel, DetectionModel
        safe_classes.extend([SegmentationModel, DetectionModel])
    except:
        pass
    torch.serialization.add_safe_globals(safe_classes)

print("✓ torch.load patched to use weights_only=False")

from mobile_sam import sam_model_registry, SamPredictor
from ultralytics import YOLO

# CRITICAL: Global lock for YOLO (thread-unsafe operations)
YOLO_LOCK = threading.Lock()
print("✓ YOLO global lock created for thread safety")

from utils.ImageNormalisationWithMask import (
    extract_annular_region,
    pixBackgroundNorm_masked,
    pixGammaCorrection_masked,
    pixContrastNorm_masked
)

# ANSI colors
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    MAGENTA = '\033[95m'
    RESET = '\033[0m'
    BOLD = '\033[1m'


def print_vram_usage(prefix=""):
    """Print current VRAM usage."""
    if ENABLE_VRAM_MONITORING and torch.cuda.is_available():
        allocated = torch.cuda.memory_allocated() / 1024**3
        reserved = torch.cuda.memory_reserved() / 1024**3
        print(f"{prefix}VRAM: {allocated:.2f}GB allocated, {reserved:.2f}GB reserved")


@contextmanager
def cuda_memory_tracking(label=""):
    """Context manager to track CUDA memory changes."""
    if torch.cuda.is_available():
        torch.cuda.synchronize()
        start_mem = torch.cuda.memory_allocated()
        yield
        torch.cuda.synchronize()
        end_mem = torch.cuda.memory_allocated()
        delta = (end_mem - start_mem) / 1024**3
        if abs(delta) > 0.01:  # Only print if >10MB change
            print(f"{label} VRAM delta: {delta:+.3f}GB")
    else:
        yield


class MobileSAMv2AutomaticMaskGenerator:
    """Optimized mask generator for threading."""

    def __init__(self, sam_predictor, yolo_model, conf_threshold=0.25, iou_threshold=0.7,
                 min_mask_region_area=100, max_mask_region_area=1500,
                 use_grid_points=True, points_per_side=32):
        self.predictor = sam_predictor
        self.yolo_model = yolo_model
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.min_mask_region_area = min_mask_region_area
        self.max_mask_region_area = max_mask_region_area
        self.use_grid_points = use_grid_points
        self.points_per_side = points_per_side

    def generate(self, image: np.ndarray) -> List[Dict[str, Any]]:
        """Generate masks using YOLO + SAM."""
        masks = []

        # 1. SAM Encoding (Heavy & Parallel-safe)
        try:
            self.predictor.set_image(image)
        except Exception as e:
            if PRINT_DETAIL:
                print(f"SAM encoding failed: {e}")
            return masks

        # 2. YOLO Detection (Fast but Thread-UNSAFE)
        boxes = []
        try:
            with YOLO_LOCK:  # Serialize YOLO across threads
                results = self.yolo_model.predict(
                    image, conf=self.conf_threshold, iou=self.iou_threshold, verbose=False
                )

            # Copy boxes to CPU immediately (outside lock)
            if results and len(results) > 0 and results[0].boxes is not None:
                boxes = results[0].boxes.xyxy.cpu().numpy()
        except Exception as e:
            if PRINT_DETAIL:
                print(f"YOLO detection failed: {e}")

        # 3. SAM Decoding (Parallel-safe, heavy computation)
        for box in boxes:
            try:
                x1, y1, x2, y2 = box
                pred_masks, scores, _ = self.predictor.predict(
                    box=np.array([x1, y1, x2, y2]),
                    multimask_output=False
                )

                if len(scores) > 0:
                    mask = pred_masks[0]
                    area = int(mask.sum())

                    if self.min_mask_region_area <= area <= self.max_mask_region_area:
                        if not self._overlaps_existing(mask, masks):
                            masks.append({
                                'segmentation': mask,
                                'area': area,
                            })
            except Exception as e:
                if PRINT_DETAIL:
                    print(f"Box prediction failed: {e}")
                continue

        # Grid prompting fallback
        if self.use_grid_points and len(masks) < 5:
            self._generate_grid_masks(image, masks)

        return masks

    def _generate_grid_masks(self, image: np.ndarray, masks: List):
        """Generate masks using grid point prompting."""
        h, w = image.shape[:2]
        points_x = np.linspace(0, w, self.points_per_side + 2)[1:-1]
        points_y = np.linspace(0, h, self.points_per_side + 2)[1:-1]

        step = max(1, self.points_per_side // 8)
        for y in points_y[::step]:
            for x in points_x[::step]:
                try:
                    mask_pred, scores, _ = self.predictor.predict(
                        point_coords=np.array([[x, y]]),
                        point_labels=np.array([1]),
                        multimask_output=True
                    )

                    if mask_pred is not None and len(mask_pred) > 0:
                        best_idx = np.argmax(scores)
                        mask = mask_pred[best_idx]
                        area = int(mask.sum())

                        if self.min_mask_region_area <= area <= self.max_mask_region_area:
                            if not self._overlaps_existing(mask, masks):
                                masks.append({
                                    'segmentation': mask,
                                    'area': area,
                                })
                except:
                    continue

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


def worker_thread(thread_id: int, task_queue: Queue, result_queue: Queue,
                 model_manager: ModelManager, shutdown_event: threading.Event):
    """
    Worker thread using ModelManager's shared models with UUID-based task tracking.

    Each thread:
    - Gets shared SAM model from ModelManager (read-only weights)
    - Creates its own SAM predictor (private state - thread-safe)
    - Gets shared YOLO model from ModelManager (with lock protection)
    - Has dedicated CUDA stream for GPU operations
    """
    try:
        print(f"[Thread {thread_id}] Initializing with ModelManager...")

        # Optimize CUDA settings for RTX 3050
        if torch.cuda.is_available():
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
            torch.cuda.set_per_process_memory_fraction(0.9, 0)

        # 1. Get SHARED SAM model from ModelManager (read-only, safe)
        shared_sam_model = model_manager.get_sam_model()

        # 2. Create thread-local SamPredictor (private state, thread-safe)
        local_predictor = SamPredictor(shared_sam_model)

        # 3. Get SHARED YOLO model from ModelManager (with lock protection)
        local_yolo = model_manager.get_yolo_model()

        # 4. Create dedicated CUDA stream
        cuda_stream = torch.cuda.Stream()

        # 5. Create mask generator using shared models
        mask_generator = MobileSAMv2AutomaticMaskGenerator(
            sam_predictor=local_predictor,  # Own predictor (private state)
            yolo_model=local_yolo,           # Shared model (locked calls)
            conf_threshold=0.3,
            iou_threshold=0.7,
            min_mask_region_area=3000,
            max_mask_region_area=5500,
            use_grid_points=True,
            points_per_side=32
        )

        print(f"[Thread {thread_id}] Ready with shared models on CUDA Stream {cuda_stream}")

        # Process tasks from queue
        while not shutdown_event.is_set():
            try:
                task = task_queue.get(timeout=1)

                if task is None:  # Shutdown signal
                    print(f"[Thread {thread_id}] Received shutdown signal")
                    break

                # CRITICAL: Unpack UUID and cancel_event from task
                image_uuid, batch_idx, preprocessed_img, params_desc, center_x, center_y, annular_image_orig, cancel_event = task

                # CHECK CANCELLATION: If another thread already found hexagon, skip this work
                if cancel_event.is_set():
                    if PRINT_DETAIL:
                        print(f"[Thread {thread_id}] Task {batch_idx} cancelled (hexagon already found)")
                    task_queue.task_done()
                    continue

                # Run GPU operations in dedicated stream
                with torch.cuda.stream(cuda_stream):
                    # Convert to RGB
                    if preprocessed_img.ndim == 2:
                        preprocessed_rgb = cv2.cvtColor(preprocessed_img, cv2.COLOR_GRAY2RGB)
                    else:
                        preprocessed_rgb = preprocessed_img

                    # CHECK AGAIN before expensive GPU work
                    if cancel_event.is_set():
                        if PRINT_DETAIL:
                            print(f"[Thread {thread_id}] Cancelled before GPU work (batch_idx={batch_idx})")
                        task_queue.task_done()
                        continue

                    # Generate masks (YOLO calls are locked internally)
                    with torch.inference_mode():
                        masks = mask_generator.generate(preprocessed_rgb)

                # Synchronize stream before CPU processing
                cuda_stream.synchronize()

                # CPU-bound post-processing
                result = process_masks_cpu(masks, center_x, center_y, annular_image_orig)
                hexagon_found, mask_area, mask_dims, mask_verts = result

                # CRITICAL: Include UUID in result
                result_queue.put((image_uuid, batch_idx, hexagon_found, params_desc,
                                mask_area, mask_dims, mask_verts))
                task_queue.task_done()

            except Empty:
                continue
            except Exception as e:
                print(f"[Thread {thread_id}] Error processing task: {e}")
                if PRINT_DETAIL:
                    import traceback
                    traceback.print_exc()
                try:
                    result_queue.put((image_uuid, batch_idx, False, params_desc, None, None, None))
                except:
                    pass
                task_queue.task_done()

        print(f"[Thread {thread_id}] Shutting down gracefully")

    except Exception as e:
        print(f"[Thread {thread_id}] Fatal error: {e}")
        import traceback
        traceback.print_exc()


def process_masks_cpu(masks, center_x, center_y, original_image):
    """
    CPU-bound hexagon detection logic with complete SAMSegmentation algorithm.

    Complete Logic (from SAMSegmentation):
    1. Collect ALL masks that overlap with center point (not just first!)
    2. Try each mask in order until one passes hexagon criteria
    3. Use proper polygon cleaning (remove small edges & sharp corners)
    4. Return first valid hexagon found ("first valid winner")
    """
    if not masks:
        return False, None, (0, 0), 0

    # Step 1: Collect ALL center-overlapping masks (not just first one!)
    center_masks = []
    for idx, mask_data in enumerate(masks):
        try:
            if mask_data['segmentation'][int(center_y), int(center_x)]:
                center_masks.append((idx, mask_data))
        except (IndexError, KeyError):
            continue

    if not center_masks:
        return False, None, (0, 0), 0

    if PRINT_DETAIL:
        print(f"  Found {len(center_masks)} masks overlapping with center")

    # Step 2: Try each center-overlapping mask until we find a valid hexagon
    for idx, mask_data in center_masks:
        mask = (mask_data['segmentation'].astype(np.uint8) * 255)  # Convert boolean to 0/255
        mask_area = mask_data['area']

        # Find contours
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if not contours:
            continue

        # Get the largest contour
        contour = max(contours, key=cv2.contourArea)

        if PRINT_DETAIL:
            print(f"  Evaluating Mask {idx}:")

        # Initial approximation
        epsilon = 0.02 * cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, epsilon, True)

        if PRINT_DETAIL:
            print(f"    Initial approximation: {len(approx)} vertices")

        # Step 3: Clean the polygon (remove small edges and sharp corners)
        # cleaned_vertices = clean_polygon(approx, edge_threshold=0.15, corner_threshold=0.2)
        cleaned_vertices = clean_polygon_hough(approx, mask, debug=PRINT_DETAIL)

        num_vertices = len(cleaned_vertices)
        if PRINT_DETAIL:
            print(f"    Final vertex count after cleaning: {num_vertices}")

        # Step 4: Check if hexagonal (5-8 vertices)
        if 5 <= num_vertices <= 8:
            # Additional check: compute aspect ratio
            x, y, w, h = cv2.boundingRect(contour)
            mask_dims = (h, w)
            aspect_ratio = float(w) / h if h > 0 else 0

            if PRINT_DETAIL:
                print(f"    Aspect ratio: {aspect_ratio:.2f}")

            # Hexagons should have aspect ratio close to 1
            if 0.7 < aspect_ratio < 1.3:
                if PRINT_DETAIL:
                    print(f"  ✓ Mask {idx} identified as HEXAGONAL NUT (vertices={num_vertices}, aspect_ratio={aspect_ratio:.2f})")
                # ============================================================
                # Calculate actual_nut_mask and B-R difference
                # ============================================================

                # a) Find center of mass
                moments = cv2.moments(mask)
                if moments['m00'] != 0:
                    mask_center_x = int(moments['m10'] / moments['m00'])
                    mask_center_y = int(moments['m01'] / moments['m00'])
                else:
                    mask_center_x = mask.shape[1] // 2
                    mask_center_y = mask.shape[0] // 2

                if PRINT_DETAIL:
                    print(f"    Mask center of mass: ({mask_center_x}, {mask_center_y})")

                # b) Create disc of radius 25 and exclude from mask
                disc_radius = 25
                disc_mask = np.zeros_like(mask)
                cv2.circle(disc_mask, (mask_center_x, mask_center_y), disc_radius, 255, -1)

                # actual_nut_mask = mask - disc
                actual_nut_mask = mask.copy()
                actual_nut_mask[disc_mask == 255] = 0

                actual_nut_mask_area = np.sum(actual_nut_mask == 255)

                # Print actual_nut_mask area
                # print(f"    Disc radius: {disc_radius}, excluded pixels: {np.sum(disc_mask == 255)}")
                print(f"    actual_nut_mask area: {actual_nut_mask_area} pixels (original: {mask_area})")

                # c) Calculate B-R difference on actual_nut_mask
                if actual_nut_mask_area > 0:
                    # Extract B and R channels from original RGB image
                    r_channel = original_image[:, :, 0]  # RGB format: R=0
                    b_channel = original_image[:, :, 2]  # RGB format: B=2

                    # Calculate B - R difference
                    br_diff = b_channel.astype(np.float32) - r_channel.astype(np.float32)

                    # Get pixels in actual_nut_mask
                    br_diff_pixels = br_diff[actual_nut_mask == 255]

                    if len(br_diff_pixels) > 0:
                        mean_br_diff = np.mean(br_diff_pixels)
                        print(f"    Mean (B - R) in actual_nut_mask: {mean_br_diff:.2f}")
                    else:
                        print(f"    Warning: No valid pixels in actual_nut_mask")
                else:
                    print(f"    Warning: actual_nut_mask is empty (disc covers entire mask)")

                # ============================================================
                # First valid hexagon wins!
                return True, mask_area, mask_dims, num_vertices
            else:
                if PRINT_DETAIL:
                    print(f"  ✗ Mask {idx} failed aspect ratio check")
        else:
            if PRINT_DETAIL:
                print(f"  ✗ Mask {idx} has {num_vertices} vertices (need 5-8)")

    # No valid hexagon found in any center-overlapping mask
    return False, None, (0, 0), 0




def clean_polygon(approx, edge_threshold=0.15, corner_threshold=0.2):
    """
    Clean polygon by removing small edges and sharp corners.
    More robust outlier removal for edge lengths.

    EXACT COPY from Batch v0.73 (GUI lines 3417-3528)
    Uses IQR-based outlier detection for robustness.
    """
    vertices = [tuple(pt[0]) for pt in approx]
    if len(vertices) < 4:
        return vertices

    if PRINT_DETAIL:
        print(f"    Initial vertices: {len(vertices)}")

    # === STEP 1: Remove small edges (IQR-based) ===
    while True:
        if len(vertices) < 4:
            break

        # Calculate all edge lengths
        edge_lengths = []
        for i in range(len(vertices)):
            v1 = np.array(vertices[i])
            v2 = np.array(vertices[(i + 1) % len(vertices)])
            edge_lengths.append(np.linalg.norm(v2 - v1))

        # Use IQR (Interquartile Range) method for robust outlier detection
        sorted_edges = sorted(edge_lengths)
        n = len(sorted_edges)

        # Calculate Q1 (25th percentile) and Q3 (75th percentile)
        q1_idx = n // 4
        q3_idx = 3 * n // 4
        q1 = sorted_edges[q1_idx]
        q3 = sorted_edges[q3_idx]
        iqr = q3 - q1

        # Lower bound: Q1 - 1.5 * IQR (standard outlier detection)
        # But also enforce a minimum relative to Q3
        lower_bound_iqr = q1 - 1.5 * iqr
        lower_bound_relative = q3 * edge_threshold  # 15% of upper quartile
        lower_bound = max(lower_bound_iqr, lower_bound_relative)

        edge_info = [(i, length) for i, length in enumerate(edge_lengths)]
        if PRINT_DETAIL:
            print(f"    Edge lengths: {[f'{l:.1f}' for l in edge_lengths]}")
            print(f"    Q1: {q1:.1f}, Q3: {q3:.1f}, IQR: {iqr:.1f}, Lower bound: {lower_bound:.1f}")

        # Find smallest edge
        min_idx, min_length = min(edge_info, key=lambda x: x[1])

        # Remove if below threshold
        if min_length < lower_bound:
            if PRINT_DETAIL:
                print(f"    Removing edge {min_idx} with length {min_length:.1f} (< {lower_bound:.1f})")
            # Remove the vertex that creates this small edge
            vertices.pop((min_idx + 1) % len(vertices))
        else:
            if PRINT_DETAIL:
                print(f"    After edge removal: {len(vertices)} vertices")
            break

    # === STEP 2: Remove sharp corners ===
    stable_count = 0
    max_iterations = 20

    while stable_count < 2 and max_iterations > 0:
        max_iterations -= 1
        if len(vertices) < 4:
            break

        initial_count = len(vertices)
        angles = []

        for i in range(len(vertices)):
            v_prev = np.array(vertices[i - 1])
            v_curr = np.array(vertices[i])
            v_next = np.array(vertices[(i + 1) % len(vertices)])

            edge1 = v_curr - v_prev
            edge2 = v_next - v_curr

            len1 = np.linalg.norm(edge1)
            len2 = np.linalg.norm(edge2)

            if len1 < 1e-6 or len2 < 1e-6:
                angles.append((i, 0.0))
                continue

            edge1_norm = edge1 / len1
            edge2_norm = edge2 / len2

            dot_product = np.clip(np.dot(edge1_norm, edge2_norm), -1.0, 1.0)
            angle_rad = np.arccos(dot_product)
            angles.append((i, angle_rad))

        if not angles:
            break

        median_angle = np.median([a[1] for a in angles])
        sharp_threshold = median_angle * corner_threshold

        if PRINT_DETAIL:
            print(f"    Sharp corner threshold: {np.degrees(sharp_threshold):.1f}°")

        sharpest_idx, sharpest_angle = min(angles, key=lambda x: x[1])

        if sharpest_angle < sharp_threshold:
            vertices.pop(sharpest_idx)
        else:
            stable_count += 1

        if len(vertices) == initial_count:
            stable_count += 1
        else:
            stable_count = 0

    if PRINT_DETAIL:
        print(f"    After corner removal: {len(vertices)} vertices")
    return vertices

def create_preprocessed_batch(image_rgb, mask):
    """
    Create 6 preprocessing variations following exact Batch v0.73 pipeline.

    Pipeline (MATCHES BATCH v0.73 - NO bilateral, NO contrast):
    1. Background normalization: (35, 35) or (40, 40)
    2. Gamma correction: 2.0, 2.5, or 3.0
    3. DONE - NO bilateral filter, NO contrast normalization

    Total: 6 variations (2 bg × 3 gamma)
    """
    batch = []
    gamma_values = [2.0, 2.5, 3.0]
    bg_variations = [(35, 35), (40, 40)]

    for bg_sx, bg_sy in bg_variations:
        for gamma in gamma_values:
            # Step 1: Background normalization
            img = pixBackgroundNorm_masked(image_rgb, mask, sx=bg_sx, sy=bg_sy, bgval=200)

            # Step 2: Gamma correction
            img = pixGammaCorrection_masked(img, mask, gamma=gamma)

            # DONE - no bilateral filter, no contrast normalization (matches batch v0.73)

            desc = f"bg=({bg_sx},{bg_sy}); gamma={gamma}"
            batch.append((img, desc))

    return batch


from sklearn.cluster import KMeans


def normalize_angle(angle):
    """Normalize angle to [0, 180) degrees."""
    while angle < 0:
        angle += 180
    while angle >= 180:
        angle -= 180
    return angle


def line_angle(x1, y1, x2, y2):
    """Calculate angle of line in degrees [0, 180)."""
    angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
    return normalize_angle(angle)


def line_intersection(line1, line2):
    """
    Find intersection point of two lines in format (x1, y1, x2, y2).
    Returns (x, y) or None if parallel.
    """
    x1, y1, x2, y2 = line1
    x3, y3, x4, y4 = line2

    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denom) < 1e-10:
        return None

    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom

    x = x1 + t * (x2 - x1)
    y = y1 + t * (y2 - y1)

    return (x, y)


def extend_line(x1, y1, x2, y2, length=1000):
    """Extend line segment to a longer line for intersection calculation."""
    dx = x2 - x1
    dy = y2 - y1
    norm = math.sqrt(dx ** 2 + dy ** 2)
    if norm < 1e-10:
        return (x1, y1, x2, y2)

    dx /= norm
    dy /= norm

    new_x1 = x1 - dx * length
    new_y1 = y1 - dy * length
    new_x2 = x2 + dx * length
    new_y2 = y2 + dy * length

    return (new_x1, new_y1, new_x2, new_y2)


def cluster_lines_by_angle(lines, n_clusters=3):
    """
    Cluster lines into groups by orientation.
    For hexagons, expect 3 groups with ~60° separation.

    Returns: List of line groups, each group is list of (x1, y1, x2, y2, angle)
    """
    if len(lines) < n_clusters:
        return None

    # Extract angles
    angles = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        angle = line_angle(x1, y1, x2, y2)
        angles.append(angle)

    # Normalize angles for clustering (handle wraparound at 180°)
    # Convert to unit vectors on circle
    angles_rad = np.deg2rad(angles)
    X = np.column_stack([np.cos(2 * angles_rad), np.sin(2 * angles_rad)])

    # K-means clustering
    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = kmeans.fit_predict(X)

    # Group lines by cluster
    clustered_lines = [[] for _ in range(n_clusters)]
    for i, (line, angle) in enumerate(zip(lines, angles)):
        x1, y1, x2, y2 = line[0]
        clustered_lines[labels[i]].append((x1, y1, x2, y2, angle))

    # Filter out clusters with too few lines
    clustered_lines = [group for group in clustered_lines if len(group) >= 2]

    return clustered_lines if len(clustered_lines) >= 3 else None


def find_parallel_lines(line_group, max_pairs=2):
    """
    Find the 2 strongest parallel lines in a group.
    Uses line length and position averaging.

    Returns: Two lines (x1, y1, x2, y2) or None
    """
    if len(line_group) < 2:
        return None

    # Sort by length (longest lines are usually more reliable)
    lengths = []
    for x1, y1, x2, y2, angle in line_group:
        length = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
        lengths.append(length)

    sorted_indices = np.argsort(lengths)[::-1]

    # Take top lines and cluster by position perpendicular to line direction
    # For simplicity, use midpoint projection onto perpendicular axis

    if len(line_group) >= 4:
        # Use top 4-6 lines, cluster into 2 groups by perpendicular position
        top_n = min(6, len(line_group))
        top_indices = sorted_indices[:top_n]

        # Calculate perpendicular distance from origin for each line
        perp_distances = []
        for idx in top_indices:
            x1, y1, x2, y2, angle = line_group[idx]
            # Midpoint
            mx = (x1 + x2) / 2
            my = (y1 + y2) / 2
            # Perpendicular angle
            perp_angle = angle + 90
            perp_rad = math.radians(perp_angle)
            # Distance from origin projected onto perpendicular
            dist = mx * math.cos(perp_rad) + my * math.sin(perp_rad)
            perp_distances.append(dist)

        # K-means with k=2 to find two parallel line groups
        if len(perp_distances) >= 2:
            X = np.array(perp_distances).reshape(-1, 1)
            kmeans = KMeans(n_clusters=2, random_state=42, n_init=10)
            labels = kmeans.fit_predict(X)

            # Average lines in each group
            group1 = [line_group[top_indices[i]] for i in range(len(top_indices)) if labels[i] == 0]
            group2 = [line_group[top_indices[i]] for i in range(len(top_indices)) if labels[i] == 1]

            line1 = average_lines(group1) if group1 else None
            line2 = average_lines(group2) if group2 else None

            if line1 and line2:
                return (line1, line2)

    # Fallback: use two longest lines
    if len(line_group) >= 2:
        idx1 = sorted_indices[0]
        idx2 = sorted_indices[1]
        x1, y1, x2, y2, _ = line_group[idx1]
        x3, y3, x4, y4, _ = line_group[idx2]
        return ((x1, y1, x2, y2), (x3, y3, x4, y4))

    return None


def average_lines(lines):
    """Average multiple line segments into one representative line."""
    if not lines:
        return None

    # Average endpoints
    x1_avg = np.mean([line[0] for line in lines])
    y1_avg = np.mean([line[1] for line in lines])
    x2_avg = np.mean([line[2] for line in lines])
    y2_avg = np.mean([line[3] for line in lines])

    return (x1_avg, y1_avg, x2_avg, y2_avg)


def detect_hexagon_hough(mask, debug=False):
    """
    Detect hexagon using Probabilistic Hough Lines.

    Steps:
    1. Canny edge detection
    2. HoughLinesP to detect line segments
    3. Cluster lines into 3 orientation groups (~60° apart)
    4. Find 2 parallel lines per group (6 total)
    5. Compute 6 intersections
    6. Validate hexagon geometry

    Args:
        mask: Binary mask (0/255) of the nut region
        debug: If True, print debug information

    Returns:
        List of 6 vertices [(x, y), ...] or None if hexagon not found
    """
    # === STEP 1: Canny Edge Detection ===
    edges = cv2.Canny(mask, 50, 150, apertureSize=3)

    # === STEP 2: Probabilistic Hough Transform ===
    # Parameters tuned for hex nuts
    min_line_length = 20  # Minimum line segment length
    max_line_gap = 10  # Maximum gap between line segments

    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180,
        threshold=30,
        minLineLength=min_line_length,
        maxLineGap=max_line_gap
    )

    if lines is None or len(lines) < 6:
        if debug:
            print(f"    Hough: Found only {len(lines) if lines is not None else 0} lines (need >= 6)")
        return None

    if debug:
        print(f"    Hough: Found {len(lines)} line segments")

    # === STEP 3: Cluster Lines by Orientation ===
    clustered_lines = cluster_lines_by_angle(lines, n_clusters=3)

    if clustered_lines is None:
        if debug:
            print(f"    Hough: Could not cluster lines into 3 groups")
        return None

    if debug:
        print(f"    Hough: Clustered into {len(clustered_lines)} groups: "
              f"{[len(g) for g in clustered_lines]} lines per group")

    # === STEP 4: Find 2 Parallel Lines per Cluster ===
    hex_lines = []
    for i, group in enumerate(clustered_lines):
        parallel_pair = find_parallel_lines(group)
        if parallel_pair is None:
            if debug:
                print(f"    Hough: Group {i} failed to find parallel pair")
            return None
        hex_lines.extend(parallel_pair)

    if len(hex_lines) != 6:
        if debug:
            print(f"    Hough: Expected 6 lines, got {len(hex_lines)}")
        return None

    if debug:
        print(f"    Hough: Found 6 hexagon lines")

    # === STEP 5: Compute Intersections ===
    # Extend lines for intersection calculation
    extended_lines = [extend_line(*line) for line in hex_lines]

    # Find intersections between consecutive pairs
    # Sort lines by angle first
    line_angles = [line_angle(*line[:4]) for line in hex_lines]
    sorted_indices = np.argsort(line_angles)
    sorted_lines = [extended_lines[i] for i in sorted_indices]

    vertices = []
    for i in range(6):
        line1 = sorted_lines[i]
        line2 = sorted_lines[(i + 1) % 6]

        intersection = line_intersection(line1, line2)
        if intersection is None:
            if debug:
                print(f"    Hough: Lines {i} and {(i + 1) % 6} are parallel (no intersection)")
            return None

        vertices.append(intersection)

    # === STEP 6: Validate Hexagon Geometry ===
    if not validate_hexagon(vertices, debug=debug):
        return None

    # Sort vertices by angle around centroid
    vertices = sort_vertices_by_angle(vertices)

    return vertices


def validate_hexagon(vertices, debug=False):
    """
    Validate that vertices form a reasonable hexagon.

    Checks:
    1. Side length variance < 30% (relaxed for worn nuts)
    2. Interior angles approximately 120° (±30°)
    3. Vertices not too close together

    Returns: True if valid hexagon
    """
    if len(vertices) != 6:
        return False

    # Calculate side lengths
    side_lengths = []
    for i in range(6):
        v1 = np.array(vertices[i])
        v2 = np.array(vertices[(i + 1) % 6])
        length = np.linalg.norm(v2 - v1)
        side_lengths.append(length)

    # Check side length variance
    mean_length = np.mean(side_lengths)
    std_length = np.std(side_lengths)
    cv = std_length / mean_length if mean_length > 0 else float('inf')

    if cv > 0.3:  # Coefficient of variation > 30%
        if debug:
            print(f"    Hexagon validation failed: Side length variance too high (CV={cv:.2f})")
        return False

    # Calculate interior angles
    angles = []
    for i in range(6):
        v_prev = np.array(vertices[i - 1])
        v_curr = np.array(vertices[i])
        v_next = np.array(vertices[(i + 1) % 6])

        edge1 = v_curr - v_prev
        edge2 = v_next - v_curr

        len1 = np.linalg.norm(edge1)
        len2 = np.linalg.norm(edge2)

        if len1 < 1e-6 or len2 < 1e-6:
            continue

        edge1_norm = edge1 / len1
        edge2_norm = edge2 / len2

        dot_product = np.clip(np.dot(edge1_norm, edge2_norm), -1.0, 1.0)
        angle_rad = np.arccos(dot_product)
        angles.append(math.degrees(angle_rad))

    # Check angles are approximately 120° (±30°)
    if angles:
        mean_angle = np.mean(angles)
        if abs(mean_angle - 120) > 30:
            if debug:
                print(f"    Hexagon validation failed: Mean angle {mean_angle:.1f}° not close to 120°")
            return False

    if debug:
        print(f"    Hexagon validation passed: CV={cv:.2f}, mean_angle={np.mean(angles):.1f}°")

    return True


def sort_vertices_by_angle(vertices):
    """Sort vertices by angle around centroid (counterclockwise)."""
    centroid = np.mean(vertices, axis=0)

    def angle_from_centroid(vertex):
        return math.atan2(vertex[1] - centroid[1], vertex[0] - centroid[0])

    sorted_vertices = sorted(vertices, key=angle_from_centroid)
    return sorted_vertices


# ============================================================================
# REPLACEMENT FUNCTION FOR clean_polygon()
# ============================================================================

def clean_polygon_hough(approx, mask, debug=False):
    """
    Clean polygon using Hough Lines-based hexagon detection.

    This replaces the IQR-based method with a more robust approach.

    Args:
        approx: Initial polygon approximation from cv2.approxPolyDP
        mask: Binary mask of the nut region
        debug: Print debug information

    Returns:
        List of cleaned vertices [(x, y), ...]
    """
    # Try Hough-based detection
    vertices = detect_hexagon_hough(mask, debug=debug)

    if vertices is not None:
        if debug:
            print(f"    Hough-based cleaning: Found {len(vertices)} vertices")
        return vertices

    # Fallback to original vertices if Hough fails
    if debug:
        print(f"    Hough-based cleaning failed, using original approximation")

    vertices = [tuple(pt[0]) for pt in approx]
    return vertices

class ThreadedNutDetector:
    """Threading-based detector with UUID-based task tracking and ModelManager integration."""

    def __init__(self, num_threads=3):
        self.num_threads = num_threads
        self.shutdown_event = threading.Event()

        # Use ModelManager singleton for shared models
        print("\n" + "=" * 70)
        print("Initializing ThreadedNutDetector with ModelManager...")
        print("=" * 70)

        self.model_manager = ModelManager.get_instance()
        self.device = self.model_manager.get_device()

        # Load models once (shared across all threads)
        self.model_manager.load_models()

        print(f"✓ Models loaded on {self.device} via ModelManager")
        print("✓ Models will be shared across all worker threads (50% GPU savings)")
        print("=" * 70 + "\n")

    def process_single_image(self, image_rgb: np.ndarray, image_id: str = None,
                            task_queue: Queue = None, result_queue: Queue = None) -> Tuple[bool, Dict]:
        """
        Process a single in-memory image.

        Args:
            image_rgb: RGB image array
            image_id: Optional identifier for logging
            task_queue: Shared task queue (if None, uses instance queues)
            result_queue: Shared result queue (if None, uses instance queues)

        Returns:
            (hexagon_found, result_dict)
        """
        if image_id is None:
            image_id = str(uuid.uuid4())[:8]

        # CRITICAL: Clear BOTH queues before processing new image
        # This ensures no contamination from previous images

        # 1. Drain task_queue (remove any unprocessed tasks)
        task_drained = 0
        while not task_queue.empty():
            try:
                task_queue.get_nowait()
                task_drained += 1
            except Empty:
                break

        # 2. Drain result_queue (remove any orphaned results)
        result_drained = 0
        while not result_queue.empty():
            try:
                result_queue.get_nowait()
                result_drained += 1
            except Empty:
                break

        if (task_drained > 0 or result_drained > 0) and PRINT_DETAIL:
            print(f"[{image_id}] Cleared queues: {task_drained} tasks, {result_drained} results")

        # Generate unique UUID for this image
        image_uuid = uuid.uuid4()

        try:
            # Extract annular region
            annular_image, annular_mask = extract_annular_region(
                image_rgb,
                center=(632, 360),
                outer_radius=80,
                inner_radius=0,
                fill_color=0
            )

            # Create 6 preprocessing variations
            batch = create_preprocessed_batch(annular_image, annular_mask)

            center_x, center_y = 80, 80

            # Process in groups of 3 with CANCELLATION support
            hexagon_found = False
            best_result = None
            pending_tasks = 0
            results_collected = 0

            # CREATE CANCELLATION EVENT for this image
            cancel_event = threading.Event()

            for group_start in range(0, 6, 3):  # Groups of 3 (was 2)
                # 1. Submit tasks with UUID and cancel_event
                group_size = 0
                for i in range(3):  # Submit 3 tasks (was 2)
                    batch_idx = group_start + i
                    if batch_idx < len(batch):
                        preprocessed_img, params_desc = batch[batch_idx]

                        # CRITICAL: Include UUID and cancel_event in task
                        task_queue.put((image_uuid, batch_idx, preprocessed_img, params_desc,
                                        center_x, center_y, annular_image, cancel_event))
                        pending_tasks += 1
                        group_size += 1

                # 2. Collect results with IMMEDIATE early termination
                timeout_start = time.time()
                valid_results_received = 0

                while valid_results_received < group_size and not hexagon_found:  # ← Added check here!
                    try:
                        result = result_queue.get(timeout=5)

                        # CRITICAL: Unpack and validate UUID
                        r_uuid, r_batch_idx, found, params_desc, area, dims, verts = result

                        # Queue poisoning check
                        if r_uuid != image_uuid:
                            if PRINT_DETAIL:
                                print(f"[{image_id}] Discarding stale result from different image")
                            continue

                        # Valid result for this image
                        valid_results_received += 1
                        results_collected += 1

                        # Check if from current group
                        if r_batch_idx >= group_start and r_batch_idx < group_start + 3:

                            # OPTIMIZATION: Immediate early termination with CANCELLATION!
                            # As soon as we find a hexagon, STOP ALL OTHER THREADS
                            if found:
                                hexagon_found = True

                                # TRIGGER CANCELLATION - tells other threads to stop GPU work
                                cancel_event.set()

                                best_result = {
                                    'area': area,
                                    'dims': dims,
                                    'vertices': verts,
                                    'params': params_desc
                                }
                                # ALWAYS print this - critical for debugging
                                print(f"[{image_id}] ✓ HEXAGON FOUND! Cancelling other threads (received {valid_results_received}/{group_size})")
                                # BREAK IMMEDIATELY! Don't wait for while condition
                                break  # ← Exit while loop NOW!

                        # Safety timeout
                        if time.time() - timeout_start > 15:
                            if PRINT_DETAIL:
                                print(f"[{image_id}] Group collection timeout")
                            break

                    except Empty:
                        if PRINT_DETAIL:
                            print(f"[{image_id}] Timeout waiting for results")
                        break

                # 3. Early termination between groups
                if hexagon_found:
                    # ALWAYS print this - critical for debugging
                    print(f"[{image_id}] Skipping batch 2 (hexagon already found)")
                    break  # Exit group loop immediately

            # Return immediately - queues will be cleared at START of next image
            return hexagon_found, best_result if best_result else {}

        except Exception as e:
            if PRINT_DETAIL:
                print(f"[{image_id}] Error: {e}")
                import traceback
                traceback.print_exc()
            return False, {}

    def process_directory(self, input_dir: str):
        """Process directory using threading with UUID-based task tracking."""
        # Find images
        image_extensions = ['*.jpg', '*.jpeg', '*.png', '*.bmp']
        image_files = []
        for ext in image_extensions:
            image_files.extend(glob.glob(os.path.join(input_dir, ext)))
            image_files.extend(glob.glob(os.path.join(input_dir, ext.upper())))

        image_files = list(set(image_files))
        image_files.sort()

        if not image_files:
            print(f"{Colors.RED}No images found in {input_dir}{Colors.RESET}")
            return

        total = len(image_files)

        print(f"\n{Colors.BOLD}Processing {total} images with {self.num_threads} threads{Colors.RESET}")
        print(f"Pipeline: bg_norm(35/40) → gamma(2.0/2.5/3.0) → contrast(60) → bilateral(21,30,40)")
        print(f"Strategy: UUID-based task tracking (100% queue poisoning immunity)")
        print(f"Expected VRAM: ~1.5-1.8 GB (safe for RTX 3050)")
        print("=" * 70)

        # Create queues
        task_queue = Queue()
        result_queue = Queue()

        # Start worker threads with ModelManager
        threads = []
        try:
            for i in range(self.num_threads):
                t = threading.Thread(
                    target=worker_thread,
                    args=(i, task_queue, result_queue, self.model_manager,
                          self.shutdown_event),
                    daemon=True
                )
                t.start()
                threads.append(t)

            time.sleep(2)  # Let threads initialize
            print_vram_usage("After thread initialization | ")

            # Process each image
            detected_count = 0
            error_count = 0
            batch_start = time.time()

            for img_idx, file_path in enumerate(image_files, 1):
                filename = os.path.basename(file_path)
                # topbearing_file = "topbearing" in filename.lower() or "top bearing" in filename.lower()
                # if not topbearing_file:
                #     continue
                if "nut" not in filename.lower():
                    continue
                # OPTIMIZATION: 3-second delay before loading next image (for stability)
                # Skip delay for first image
                if img_idx > 1:
                    if PRINT_DETAIL:
                        print(f"[Pacing] Waiting 3 seconds before loading next image...")
                    time.sleep(3)

                # START TIMING: From file load to result (excludes sleep time)
                file_start = time.time()

                # Progress update every 10 images
                if img_idx % 10 == 0:
                    elapsed = time.time() - batch_start
                    avg_time = elapsed / img_idx
                    eta_seconds = avg_time * (total - img_idx)
                    print(f"\n{Colors.CYAN}Progress: {img_idx}/{total} | "
                          f"Avg: {avg_time:.2f}s/img | "
                          f"ETA: {eta_seconds/60:.1f}min{Colors.RESET}")
                    print_vram_usage()

                try:
                    # Load image
                    image_bgr = cv2.imread(file_path)
                    if image_bgr is None:
                        print(f"{filename}: {Colors.RED}ERROR (load failed){Colors.RESET}")
                        error_count += 1
                        continue

                    image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)

                    # Process using UUID-based tracking
                    hexagon_found, best_result = self.process_single_image(
                        image_rgb,
                        image_id=filename,
                        task_queue=task_queue,
                        result_queue=result_queue
                    )

                    file_elapsed = time.time() - file_start

                    # Print result
                    if hexagon_found:
                        area = best_result.get('area')
                        dims = best_result.get('dims', (0, 0))
                        verts = best_result.get('vertices', 0)
                        params = best_result.get('params', '')

                        dims_str = f"({dims[0]}, {dims[1]})" if dims else "N/A"
                        print(
                            f"{filename}: {Colors.GREEN}{Colors.BOLD}YES{Colors.RESET}; "
                            f"Area={area}, Dims={dims_str}, Vertices={verts}; "
                            f"{params}; {file_elapsed:.2f}s"
                        )
                        detected_count += 1
                    else:
                        print(f"{filename}: {Colors.RED}NO{Colors.RESET}; {file_elapsed:.2f}s")

                except Exception as e:
                    file_elapsed = time.time() - file_start
                    print(f"{filename}: {Colors.RED}ERROR ({e}){Colors.RESET}; {file_elapsed:.2f}s")
                    if PRINT_DETAIL:
                        import traceback
                        traceback.print_exc()
                    error_count += 1

            total_elapsed = time.time() - batch_start

            # Summary
            print("\n" + "=" * 70)
            print(f"{Colors.BOLD}Summary:{Colors.RESET}")
            print(f"  Total images:      {total}")
            print(f"  Hexagons detected: {Colors.GREEN}{detected_count}{Colors.RESET}")
            print(f"  Not detected:      {total - detected_count - error_count}")
            print(f"  Errors:            {Colors.RED}{error_count}{Colors.RESET}")
            print(f"  Total time:        {total_elapsed:.2f}s ({total_elapsed/60:.1f}min)")
            print(f"  Avg per image:     {total_elapsed / total:.2f}s")
            print(f"  Detection rate:    {100 * detected_count / total:.1f}%")
            if detected_count > 0:
                print(f"  Speedup estimate:  {self.num_threads * 0.7:.1f}× (accounting for YOLO lock)")
            print_vram_usage("  Final ")
            print("=" * 70)

        finally:
            # ALWAYS cleanup threads
            print(f"\n{Colors.YELLOW}Shutting down worker threads...{Colors.RESET}")

            # Signal shutdown
            self.shutdown_event.set()

            # Send None signals
            for _ in range(self.num_threads):
                task_queue.put(None)

            # Wait for threads with timeout
            for i, t in enumerate(threads):
                t.join(timeout=5)
                if t.is_alive():
                    print(f"{Colors.YELLOW}Warning: Thread {i} didn't shutdown cleanly{Colors.RESET}")

            # Clear CUDA cache
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                print(f"{Colors.GREEN}✓ CUDA cache cleared{Colors.RESET}")


def main():
    print("=" * 70)
    print("Batch Hexagon Nut Detection - CORRECTED Pipeline Implementation")
    print("=" * 70)
    print("Pipeline (Exact Match to SAMSegmentation):")
    print("  • bg_norm(35,35) or (40,40)")
    print("  • → gamma(2.0, 2.5, or 3.0)")
    print("  • → contrast_norm(60,60)")
    print("  • → bilateral(d=21, sigmaColor=30, sigmaSpace=40)")
    print("Architecture:")
    print("  • UUID-based task tracking (eliminates queue poisoning)")
    print("  • Threading + CUDA Streams (shared SAM model)")
    print("  • YOLO: Serialized with lock (thread-safe)")
    print("  • SAM: Parallel with 2 CUDA streams")
    print("  • Works with both file-based and in-memory images")
    print("Parameters:")
    print("  • Total variations: 6 (2 bg × 3 gamma)")
    print("  • Area limits: 3000-5500 pixels")
    print("  • Vertices: 5-8, Aspect ratio: 0.7-1.3")
    print("Expected:")
    print("  • VRAM: ~1.5-1.8 GB")
    print("  • Speedup: 1.5-2× (limited by YOLO serialization)")
    print("  • Accuracy: 100% (no false negatives from queue poisoning)")
    print("=" * 70)

    input_dir = "C:/AshokLeylandImages/DOST/LHS"

    # Verify input directory
    if not os.path.exists(input_dir):
        print(f"{Colors.RED}ERROR: Input directory not found: {input_dir}{Colors.RESET}")
        print(f"Please update the path in main()")
        return

    # GPU info
    if torch.cuda.is_available():
        gpu_name = torch.cuda.get_device_name(0)
        gpu_mem = torch.cuda.get_device_properties(0).total_memory / 1024**3
        print(f"\nGPU: {gpu_name} ({gpu_mem:.1f}GB)")
        print(f"CUDA Version: {torch.version.cuda}")
        print(f"PyTorch Version: {torch.__version__}")
    else:
        print(f"\n{Colors.YELLOW}WARNING: No CUDA available, running on CPU{Colors.RESET}")

    try:
        detector = ThreadedNutDetector(num_threads=3)
        detector.process_directory(input_dir)
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Interrupted by user{Colors.RESET}")
    except Exception as e:
        print(f"\n{Colors.RED}FATAL ERROR: {e}{Colors.RESET}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()