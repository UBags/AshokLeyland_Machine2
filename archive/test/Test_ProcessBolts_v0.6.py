import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk
import cv2
import numpy as np
import os
import time
from pathlib import Path
from itertools import combinations


def find_equidistant_blobs(blob_data, img_center, top_n=6, min_angle_deg=65, max_angle_deg=115):
    """
    Find equidistant blobs using 3 robust algorithms with voting.
    Includes validation to reject solutions with invalid adjacent angles.

    Args:
        blob_data: List of (label_id, area, centroid) tuples
        img_center: (x, y) center of the annular ring
        top_n: Number of top blobs to consider (5 or 6)
        min_angle_deg: Minimum allowed angle between adjacent blobs (degrees)
        max_angle_deg: Maximum allowed angle between adjacent blobs (degrees)

    Returns:
        (chosen_count, chosen_indices) - 4 or 5, and the indices of chosen blobs
    """
    if len(blob_data) < 4:
        return len(blob_data), list(range(len(blob_data)))

    # Limit to top_n blobs
    candidates = blob_data[:min(top_n, len(blob_data))]

    # Use the known center of the annular ring (passed as img_center)
    # (Previously calculated from blob centroids - removed as it was unreliable)
    component_center = img_center

    # Calculate angles and distances from center for each blob
    blob_info = []
    for idx, (label_id, area, centroid) in enumerate(candidates):
        dx = centroid[0] - component_center[0]
        dy = centroid[1] - component_center[1]
        angle = np.arctan2(dy, dx)
        radius = np.sqrt(dx ** 2 + dy ** 2)
        blob_info.append({
            'idx': idx,
            'label_id': label_id,
            'centroid': centroid,
            'angle': angle,
            'radius': radius,
            'area': area
        })

    # Sort by angle for processing
    blob_info.sort(key=lambda x: x['angle'])

    # Generate all possible combinations for 4 and 5 blobs
    n = len(blob_info)
    combos_4 = list(combinations(range(n), 4)) if n >= 4 else []
    combos_5 = list(combinations(range(n), 5)) if n >= 5 else []

    # Angle validation thresholds (in radians) - configurable via parameters
    MIN_ANGLE = min_angle_deg * np.pi / 180
    MAX_ANGLE = max_angle_deg * np.pi / 180

    def validate_angles(indices):
        """
        Check if all adjacent angles are within valid range [50°, 105°].
        Returns True if valid, False if any angle violates the constraint.
        """
        if len(indices) < 2:
            return True

        # Sort indices by angle
        sorted_indices = sorted(indices, key=lambda i: blob_info[i]['angle'])
        angles = [blob_info[i]['angle'] for i in sorted_indices]

        # Calculate angular spacing between adjacent blobs
        for i in range(len(angles)):
            diff = angles[(i + 1) % len(angles)] - angles[i]
            if diff < 0:
                diff += 2 * np.pi

            # Reject if angle is outside valid range [50°, 105°]
            if diff < MIN_ANGLE or diff > MAX_ANGLE:
                return False

        return True

    # ==================== ALGORITHM 1: Angular Variance ====================
    def calc_angular_variance(indices, expected_n):
        """Calculate variance of angular spacings vs expected uniform spacing"""
        angles = sorted([blob_info[i]['angle'] for i in indices])
        expected_spacing = 2 * np.pi / expected_n
        spacings = []
        for i in range(len(angles)):
            diff = angles[(i + 1) % len(angles)] - angles[i]
            if diff < 0:
                diff += 2 * np.pi
            spacings.append(diff)
        variance = sum((s - expected_spacing) ** 2 for s in spacings) / len(spacings)
        return variance

    # ==================== ALGORITHM 2: Chord Length Consistency ====================
    def calc_chord_variance(indices):
        """Calculate variance of chord lengths between adjacent blobs (sorted by angle)"""
        sorted_indices = sorted(indices, key=lambda i: blob_info[i]['angle'])
        chords = []
        for i in range(len(sorted_indices)):
            p1 = blob_info[sorted_indices[i]]['centroid']
            p2 = blob_info[sorted_indices[(i + 1) % len(sorted_indices)]]['centroid']
            chord = np.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)
            chords.append(chord)
        if len(chords) == 0:
            return float('inf')
        mean_chord = np.mean(chords)
        variance = np.var(chords) / (mean_chord ** 2 + 1e-10)  # Normalized variance
        return variance

    # ==================== ALGORITHM 3: Radius Consistency + Angular Fit ====================
    def calc_radius_angular_score(indices, expected_n):
        """
        Combined score: penalize radius inconsistency and angular deviation.
        Points on a circle should have same radius AND uniform angular spacing.
        """
        radii = [blob_info[i]['radius'] for i in indices]
        mean_radius = np.mean(radii)
        radius_variance = np.var(radii) / (mean_radius ** 2 + 1e-10)

        # Angular variance
        angular_var = calc_angular_variance(indices, expected_n)
        norm_angular_var = angular_var / ((2 * np.pi / expected_n) ** 2)

        # Combined score (weighted sum)
        score = radius_variance + norm_angular_var
        return score

    # ==================== Find all valid combinations sorted by score ====================
    # For 4 blobs - collect all combinations with their scores
    valid_4_combos = []
    for combo in combos_4:
        if validate_angles(combo):
            angular_score = calc_angular_variance(combo, 4)
            chord_score = calc_chord_variance(combo)
            combined_score = calc_radius_angular_score(combo, 4)
            # Use angular score as primary, combined as tiebreaker
            valid_4_combos.append((angular_score, combined_score, combo))
    valid_4_combos.sort(key=lambda x: (x[0], x[1]))  # Sort by scores

    # For 5 blobs - collect all combinations with their scores
    valid_5_combos = []
    for combo in combos_5:
        if validate_angles(combo):
            angular_score = calc_angular_variance(combo, 5)
            chord_score = calc_chord_variance(combo)
            combined_score = calc_radius_angular_score(combo, 5)
            valid_5_combos.append((angular_score, combined_score, combo))
    valid_5_combos.sort(key=lambda x: (x[0], x[1]))  # Sort by scores

    # Get best valid combinations (if any)
    best_4_angular = valid_4_combos[0] if valid_4_combos else (float('inf'), float('inf'), None)
    best_5_angular = valid_5_combos[0] if valid_5_combos else (float('inf'), float('inf'), None)

    # ==================== VOTING with validation ====================
    # Only vote for configurations that have valid combinations
    votes = []
    vote_combos = []

    # Algorithm 1: Angular Variance
    if best_4_angular[2] is not None and best_5_angular[2] is not None:
        norm_var_4 = best_4_angular[0] / ((2 * np.pi / 4) ** 2)
        norm_var_5 = best_5_angular[0] / ((2 * np.pi / 5) ** 2)
        if norm_var_4 < norm_var_5:
            votes.append(4)
            vote_combos.append(best_4_angular[2])
        else:
            votes.append(5)
            vote_combos.append(best_5_angular[2])
    elif best_4_angular[2] is not None:
        votes.append(4)
        vote_combos.append(best_4_angular[2])
    elif best_5_angular[2] is not None:
        votes.append(5)
        vote_combos.append(best_5_angular[2])

    # Algorithm 2: Chord Length (recalculate for valid combos only)
    best_4_chord = (float('inf'), None)
    for combo_tuple in valid_4_combos:
        combo = combo_tuple[2]
        var = calc_chord_variance(combo)
        if var < best_4_chord[0]:
            best_4_chord = (var, combo)

    best_5_chord = (float('inf'), None)
    for combo_tuple in valid_5_combos:
        combo = combo_tuple[2]
        var = calc_chord_variance(combo)
        if var < best_5_chord[0]:
            best_5_chord = (var, combo)

    if best_4_chord[1] is not None and best_5_chord[1] is not None:
        if best_4_chord[0] < best_5_chord[0]:
            votes.append(4)
            vote_combos.append(best_4_chord[1])
        else:
            votes.append(5)
            vote_combos.append(best_5_chord[1])
    elif best_4_chord[1] is not None:
        votes.append(4)
        vote_combos.append(best_4_chord[1])
    elif best_5_chord[1] is not None:
        votes.append(5)
        vote_combos.append(best_5_chord[1])

    # Algorithm 3: Combined Radius + Angular
    best_4_combined = (float('inf'), None)
    for combo_tuple in valid_4_combos:
        combo = combo_tuple[2]
        score = calc_radius_angular_score(combo, 4)
        if score < best_4_combined[0]:
            best_4_combined = (score, combo)

    best_5_combined = (float('inf'), None)
    for combo_tuple in valid_5_combos:
        combo = combo_tuple[2]
        score = calc_radius_angular_score(combo, 5)
        if score < best_5_combined[0]:
            best_5_combined = (score, combo)

    if best_4_combined[1] is not None and best_5_combined[1] is not None:
        if best_4_combined[0] < best_5_combined[0]:
            votes.append(4)
            vote_combos.append(best_4_combined[1])
        else:
            votes.append(5)
            vote_combos.append(best_5_combined[1])
    elif best_4_combined[1] is not None:
        votes.append(4)
        vote_combos.append(best_4_combined[1])
    elif best_5_combined[1] is not None:
        votes.append(5)
        vote_combos.append(best_5_combined[1])

    # ==================== FINAL DECISION ====================
    if not votes:
        # No valid combinations found - fallback to returning top blobs without validation
        # This shouldn't happen normally, but provides a safety net
        return min(len(candidates), 5), list(range(min(len(candidates), 5)))

    count_4 = votes.count(4)
    count_5 = votes.count(5)

    # Helper function to calculate total blob area for a combination
    def calc_total_area(combo):
        return sum(blob_info[i]['area'] for i in combo)

    # Threshold for considering scores "very close" (within 10%)
    CLOSE_THRESHOLD = 0.10

    if count_4 > count_5:
        chosen_count = 4
        # Find the best 4-combo from the voters, preferring larger blobs when close
        best_combos = []
        for i, v in enumerate(votes):
            if v == 4:
                combo = vote_combos[i]
                score = calc_angular_variance(combo, 4)
                area = calc_total_area(combo)
                best_combos.append((score, area, combo))

        if best_combos:
            best_combos.sort(key=lambda x: x[0])  # Sort by score
            best_score = best_combos[0][0]
            # Find all combos with scores very close to the best
            close_combos = [c for c in best_combos if c[0] <= best_score * (1 + CLOSE_THRESHOLD) or best_score == 0]
            # Among close combos, choose the one with largest total blob area
            close_combos.sort(key=lambda x: x[1], reverse=True)  # Sort by area descending
            best_combo = close_combos[0][2]
        else:
            best_combo = None
        chosen_indices = [blob_info[i]['idx'] for i in best_combo] if best_combo else []
    else:
        chosen_count = 5
        # Find the best 5-combo from the voters, preferring larger blobs when close
        best_combos = []
        for i, v in enumerate(votes):
            if v == 5:
                combo = vote_combos[i]
                score = calc_angular_variance(combo, 5)
                area = calc_total_area(combo)
                best_combos.append((score, area, combo))

        if best_combos:
            best_combos.sort(key=lambda x: x[0])  # Sort by score
            best_score = best_combos[0][0]
            # Find all combos with scores very close to the best
            close_combos = [c for c in best_combos if c[0] <= best_score * (1 + CLOSE_THRESHOLD) or best_score == 0]
            # Among close combos, choose the one with largest total blob area
            close_combos.sort(key=lambda x: x[1], reverse=True)  # Sort by area descending
            best_combo = close_combos[0][2]
        else:
            best_combo = None
        chosen_indices = [blob_info[i]['idx'] for i in best_combo] if best_combo else []

    return chosen_count, chosen_indices


def validate_final_angles(chosen_blobs, img_center, min_angle_deg, max_angle_deg):
    """
    Validate that the chosen blobs have valid angular spacings.

    Args:
        chosen_blobs: List of (label_id, area, centroid) tuples for chosen blobs
        img_center: (x, y) center of the annular ring
        min_angle_deg: Minimum allowed angle between adjacent blobs (degrees)
        max_angle_deg: Maximum allowed angle between adjacent blobs (degrees)

    Returns:
        (is_valid, min_spacing_deg, max_spacing_deg) - validation result and actual spacings
    """
    if len(chosen_blobs) < 2:
        return True, 0, 0

    # Calculate angles from center for each blob (using center of gravity from centroid)
    angles = []
    for label_id, area, centroid in chosen_blobs:
        dx = centroid[0] - img_center[0]
        dy = centroid[1] - img_center[1]
        angle = np.arctan2(dy, dx)
        angles.append(angle)

    # Sort angles
    angles.sort()

    # Calculate angular spacings between adjacent blobs
    spacings = []
    for i in range(len(angles)):
        next_i = (i + 1) % len(angles)
        spacing = angles[next_i] - angles[i]
        if spacing < 0:
            spacing += 2 * np.pi  # Handle wrap-around
        spacings.append(spacing)

    # Convert to degrees
    spacings_deg = [s * 180 / np.pi for s in spacings]
    min_spacing_deg = min(spacings_deg)
    max_spacing_deg = max(spacings_deg)

    # Check if any spacing violates the constraints
    is_valid = (min_spacing_deg >= min_angle_deg) and (max_spacing_deg <= max_angle_deg)

    return is_valid, min_spacing_deg, max_spacing_deg


def fill_sector_gaps(top_blobs, all_blobs, img_center, gap_threshold_deg=95):
    """
    Check if top_blobs have any angular sector > gap_threshold empty.
    If so, add the largest blob from the removed blobs that falls in that sector.

    Args:
        top_blobs: List of (label_id, area, centroid) tuples - the selected top N blobs
        all_blobs: List of all (label_id, area, centroid) tuples sorted by area descending
        img_center: (x, y) center of the annular ring
        gap_threshold_deg: Maximum allowed empty sector in degrees (default 95)

    Returns:
        Updated list of blobs with gap-filling blobs added
    """
    if len(top_blobs) < 2:
        return top_blobs

    # Use the known center of the annular ring (passed as img_center)
    # (Previously calculated from blob centroids using median - removed as unreliable)
    component_center = img_center

    # Calculate angles from center for each top blob
    def get_angle(centroid):
        dx = centroid[0] - component_center[0]
        dy = centroid[1] - component_center[1]
        return np.arctan2(dy, dx)

    # Get angles for top blobs and sort
    blob_angles = [(b, get_angle(b[2])) for b in top_blobs]
    blob_angles.sort(key=lambda x: x[1])

    gap_threshold_rad = gap_threshold_deg * np.pi / 180

    # Find gaps > threshold
    gaps = []
    n = len(blob_angles)
    for i in range(n):
        angle1 = blob_angles[i][1]
        angle2 = blob_angles[(i + 1) % n][1]

        # Calculate angular gap
        gap = angle2 - angle1
        if gap < 0:
            gap += 2 * np.pi

        if gap > gap_threshold_rad:
            # Record gap: (start_angle, end_angle, gap_size)
            gaps.append((angle1, angle2, gap))

    if not gaps:
        return top_blobs

    # Get the removed blobs (those not in top_blobs)
    top_blob_ids = set(b[0] for b in top_blobs)
    removed_blobs = [b for b in all_blobs if b[0] not in top_blob_ids]

    if not removed_blobs:
        return top_blobs

    # For each gap, find the largest removed blob that falls within it
    result_blobs = list(top_blobs)

    for gap_start, gap_end, gap_size in gaps:
        # Find blobs in this gap
        blobs_in_gap = []
        for blob in removed_blobs:
            blob_angle = get_angle(blob[2])

            # Check if blob_angle is within [gap_start, gap_end]
            # Handle wrap-around
            if gap_end < gap_start:
                # Gap crosses the -π/π boundary
                in_gap = blob_angle > gap_start or blob_angle < gap_end
            else:
                in_gap = gap_start < blob_angle < gap_end

            if in_gap:
                blobs_in_gap.append(blob)

        if blobs_in_gap:
            # Add the largest blob from this gap (already sorted by area)
            largest_in_gap = max(blobs_in_gap, key=lambda b: b[1])
            if largest_in_gap not in result_blobs:
                result_blobs.append(largest_in_gap)

    return result_blobs


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
        self.kernel3_size = tk.IntVar(value=21)
        self.top_n_blobs = tk.IntVar(value=6)
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
        self.use_bilateral3 = tk.BooleanVar(value=False)

        # Angle thresholds for equidistant blob detection (degrees)
        # Default values for DOST (4 blobs at 90°)
        self.min_angle_deg = 65
        self.max_angle_deg = 115

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

        # Run Batch button
        ttk.Button(control_frame, text="Run Batch", command=self.run_batch).pack(side=tk.LEFT, padx=5)

        # Kernel 1 size dropdown
        ttk.Label(control_frame, text="Kernel 1:").pack(side=tk.LEFT, padx=(20, 5))
        kernel1_dropdown = ttk.Combobox(control_frame, textvariable=self.kernel1_size,
                                        values=[3, 5, 7, 9, 11, 13, 15], width=5, state="readonly")
        kernel1_dropdown.pack(side=tk.LEFT, padx=5)
        kernel1_dropdown.bind("<<ComboboxSelected>>", self.on_kernel_change)

        # Kernel 2 size dropdown
        ttk.Label(control_frame, text="Kernel 2:").pack(side=tk.LEFT, padx=(10, 5))
        kernel2_dropdown = ttk.Combobox(control_frame, textvariable=self.kernel2_size,
                                        values=list(range(7, 36, 2)), width=5, state="readonly")
        kernel2_dropdown.pack(side=tk.LEFT, padx=5)
        kernel2_dropdown.bind("<<ComboboxSelected>>", self.on_kernel_change)

        # Kernel 3 size dropdown (for erosion)
        ttk.Label(control_frame, text="Kernel 3:").pack(side=tk.LEFT, padx=(10, 5))
        kernel3_dropdown = ttk.Combobox(control_frame, textvariable=self.kernel3_size,
                                        values=list(range(7, 22, 2)), width=5, state="readonly")
        kernel3_dropdown.pack(side=tk.LEFT, padx=5)
        kernel3_dropdown.bind("<<ComboboxSelected>>", self.on_kernel_change)

        # Top N blobs dropdown
        ttk.Label(control_frame, text="Top N:").pack(side=tk.LEFT, padx=(10, 5))
        top_n_dropdown = ttk.Combobox(control_frame, textvariable=self.top_n_blobs,
                                      values=[5, 6], width=3, state="readonly")
        top_n_dropdown.pack(side=tk.LEFT, padx=5)
        top_n_dropdown.bind("<<ComboboxSelected>>", self.on_kernel_change)

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

        # Third bilateral filter controls with checkbox
        use_bilateral3_cb = ttk.Checkbutton(bilateral_frame, text="Use Bilateral 3",
                                            variable=self.use_bilateral3, command=self.on_param_change)
        use_bilateral3_cb.pack(side=tk.LEFT, padx=5)
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

    def run_batch(self):
        """Run batch processing on all images in folder (excluding Knuckle images)"""
        if not self.current_folder:
            messagebox.showwarning("No Folder", "Please select a folder first.")
            return

        # Check if DOSTPLUS in path
        is_dostplus = "DOSTPLUS" in self.current_folder.upper()
        expected_blobs = 5 if is_dostplus else 4

        # Set top_n and angle thresholds based on folder type
        if is_dostplus:
            self.top_n_blobs.set(6)
            # DOSTPLUS: 5 blobs at 72° spacing
            self.min_angle_deg = 50
            self.max_angle_deg = 95
            # DOSTPLUS-specific settings
            self.gamma_value.set(4.0)
            self.use_bilateral3.set(False)
        else:
            self.top_n_blobs.set(5)
            # DOST: 4 blobs at 90° spacing
            self.min_angle_deg = 65
            self.max_angle_deg = 115
            # DOST-specific settings
            self.gamma_value.set(5.0)
            self.use_bilateral3.set(True)

        # Filter out images with "Knuckle" or "Bung" in name
        batch_files = [f for f in self.image_files if (("Knuckle" not in f) and ("Bung" not in f))]

        if not batch_files:
            messagebox.showwarning("No Images", "No non-Knuckle images found in the selected folder.")
            return

        # Statistics
        total_images = len(batch_files)
        successful = 0
        unsuccessful = 0
        unsuccessful_files = []

        # ANSI color codes for terminal
        GREEN = '\033[92m'
        RED = '\033[91m'
        RESET = '\033[0m'
        CYAN = '\033[96m'
        YELLOW = '\033[93m'

        print(f"\n{CYAN}{'=' * 60}")
        print(f"BATCH PROCESSING: {self.current_folder}")
        print(f"Expected blobs: {expected_blobs} ({'DOSTPLUS' if is_dostplus else 'DOST'})")
        print(f"Angle range: {self.min_angle_deg}° - {self.max_angle_deg}°")
        print(f"Gamma: {self.gamma_value.get()}, Bilateral3: {self.use_bilateral3.get()}")
        print(f"Total images to process: {total_images}")
        print(f"{'=' * 60}{RESET}\n")

        # Process each image
        for i, filename in enumerate(batch_files):
            file_path = os.path.join(self.current_folder, filename)
            img = cv2.imread(file_path)

            if img is None:
                print(f"{RED}{filename}: Failed to load image{RESET}")
                unsuccessful += 1
                unsuccessful_files.append(filename)
                continue

            # Process the image
            try:
                annular, processed, blob_count, is_valid = self.process_image(img)

                # Check success: correct blob count AND valid angles
                if blob_count == expected_blobs and is_valid:
                    print(f"{GREEN}Successful : {filename} : Number of bolts found = {blob_count}{RESET}")
                    successful += 1
                else:
                    reason = ""
                    if blob_count != expected_blobs:
                        reason = f"count={blob_count}"
                    if not is_valid:
                        reason += " angles_invalid" if reason else "angles_invalid"
                    print(f"{RED}Unsuccessful : {filename} : Number of bolts found = {blob_count} ({reason}){RESET}")
                    unsuccessful += 1
                    unsuccessful_files.append(filename)

            except Exception as e:
                print(f"{RED}{filename}: Error - {str(e)}{RESET}")
                unsuccessful += 1
                unsuccessful_files.append(filename)

        # Print final statistics
        print(f"\n{CYAN}{'=' * 60}")
        print(f"BATCH PROCESSING COMPLETE")
        print(f"{'=' * 60}{RESET}")
        print(f"\n{YELLOW}STATISTICS:{RESET}")
        print(f"  a) Number of images tested: {total_images}")
        print(f"  b) {GREEN}Successful: {successful}{RESET}")
        print(f"  c) {RED}Unsuccessful: {unsuccessful}{RESET}")

        if unsuccessful_files:
            print(f"\n{YELLOW}d) List of unsuccessful images:{RESET}")
            for f in unsuccessful_files:
                print(f"     {RED}- {f}{RESET}")
        else:
            print(f"\n{GREEN}All images processed successfully!{RESET}")

        print(f"\n{CYAN}{'=' * 60}{RESET}\n")

        # Show summary in message box
        summary = f"Batch Processing Complete\n\n"
        summary += f"Total images: {total_images}\n"
        summary += f"Successful: {successful}\n"
        summary += f"Unsuccessful: {unsuccessful}\n"
        if unsuccessful_files:
            summary += f"\nUnsuccessful files:\n" + "\n".join(f"- {f}" for f in unsuccessful_files[:10])
            if len(unsuccessful_files) > 10:
                summary += f"\n... and {len(unsuccessful_files) - 10} more"

        messagebox.showinfo("Batch Complete", summary)

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
            # Get blob count from last step name (extract number from "q) X Equidistant Blobs")
            if self.step_images:
                last_step_name = self.step_images[-1][0]
                if "Equidistant" in last_step_name:
                    try:
                        blob_count = int(last_step_name.split(")")[1].split()[0])
                        print(f"{filename}: {blob_count}")
                    except:
                        print(f"{filename}: Unknown")
        else:
            annular, processed, blob_count, is_valid = self.process_image(img)
            # Print filename, blob count, and validation status
            valid_str = "valid" if is_valid else "INVALID"
            print(f"{filename}: {blob_count} ({valid_str})")
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
        inner_radius = 130  # diameter 260 / 2

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

        # h) Third bilateral filter with configurable sigma values (conditional)
        if self.use_bilateral3.get():
            sigma_color_3 = self.bilateral3_sigma_color.get()
            sigma_space_3 = self.bilateral3_sigma_space.get()
            step4 = cv2.bilateralFilter(step3, 5, sigma_color_3, sigma_space_3)
        else:
            step4 = step3

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
        # d) Dilate by kernel1 + 10
        base_kernel_size = kernel1_size + 10
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

        # m2) Remove components touching the edge
        # 1) Make a copy and dilate with kernel=7
        pre_kernel_33 = filtered.copy()
        edge_check_kernel = np.ones((7, 7), np.uint8)
        dilated_for_edge_check = cv2.dilate(pre_kernel_33, edge_check_kernel, iterations=1)

        # 2) Find connected components and drop those touching the image edge
        num_labels_edge, labels_edge, stats_edge, _ = cv2.connectedComponentsWithStats(dilated_for_edge_check,
                                                                                       connectivity=8)

        # Create mask of components NOT touching the edge
        non_edge_mask = np.zeros_like(dilated_for_edge_check)
        for i in range(1, num_labels_edge):  # Skip background
            x, y, w, h, area = stats_edge[i]
            # Check if component touches any edge
            touches_left = (x == 0)
            touches_top = (y == 0)
            touches_right = (x + w >= img_width)
            touches_bottom = (y + h >= img_height)

            if not (touches_left or touches_top or touches_right or touches_bottom):
                # Component doesn't touch edge, keep it
                non_edge_mask[labels_edge == i] = 255

        # 3) AND with original pre_kernel_33 to get new filtered
        filtered = cv2.bitwise_and(pre_kernel_33, non_edge_mask)

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

        # p) Find top N largest connected components
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(eroded, connectivity=8)

        if num_labels <= 1:
            # No components found
            dilated_bgr = cv2.cvtColor(eroded, cv2.COLOR_GRAY2BGR)
            return annular_display, dilated_bgr, 0, False

        # Get areas and sort (skip background label 0)
        # Note: centroids from connectedComponentsWithStats are center of gravity (center of mass),
        # not bounding box centers - this is the geographical/weighted center of each blob
        component_data = []
        for i in range(1, num_labels):
            area = stats[i, cv2.CC_STAT_AREA]
            centroid = centroids[i]  # Center of gravity (center of mass)
            component_data.append((i, area, centroid))

        # Sort by area descending
        component_data.sort(key=lambda x: x[1], reverse=True)

        # Get top N blobs setting
        top_n = self.top_n_blobs.get()
        top_blobs = component_data[:top_n]

        if len(top_blobs) < 4:
            # Not enough blobs, return what we have
            result = np.zeros_like(eroded)
            for label_id, area, centroid in top_blobs:
                result[labels == label_id] = 255
            dilated_bgr = cv2.cvtColor(result, cv2.COLOR_GRAY2BGR)
            return annular_display, dilated_bgr, len(top_blobs), False

        # p2) Fill sector gaps - add back blobs from empty sectors > 95°
        img_center = (eroded.shape[1] / 2, eroded.shape[0] / 2)
        top_blobs = fill_sector_gaps(top_blobs, component_data, img_center, gap_threshold_deg=95)

        # q) Find equidistant blobs (4 or 5) using robust voting algorithm
        chosen_count, chosen_indices = find_equidistant_blobs(top_blobs, img_center, len(top_blobs),
                                                              self.min_angle_deg, self.max_angle_deg)

        # Get chosen blobs for validation
        chosen_blobs = [top_blobs[idx] for idx in chosen_indices]

        # Validate final angles
        is_valid, min_spacing, max_spacing = validate_final_angles(
            chosen_blobs, img_center, self.min_angle_deg, self.max_angle_deg)

        # Create result with chosen blobs
        result = np.zeros_like(eroded)
        for idx in chosen_indices:
            label_id = top_blobs[idx][0]
            result[labels == label_id] = 255

        dilated_bgr = cv2.cvtColor(result, cv2.COLOR_GRAY2BGR)

        return annular_display, dilated_bgr, chosen_count, is_valid

    def process_image_with_steps(self, img):
        """Process image and return all intermediate steps"""
        steps = []

        center = (635, 360)
        outer_radius = 220
        inner_radius = 130  # diameter 260 / 2

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
        steps.append(("a,b) Annular Disc (outer=220, inner=130)", annular_img.copy()))

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

        # h) Third bilateral filter with configurable sigma values (conditional)
        if self.use_bilateral3.get():
            sigma_color_3 = self.bilateral3_sigma_color.get()
            sigma_space_3 = self.bilateral3_sigma_space.get()
            step4 = cv2.bilateralFilter(step3, 5, sigma_color_3, sigma_space_3)
            steps.append(
                (f"h) Bilateral Filter (5,{sigma_color_3},{sigma_space_3})", cv2.cvtColor(step4, cv2.COLOR_GRAY2BGR)))
        else:
            step4 = step3

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
        # Dilate by kernel1 + 10
        base_kernel_size = kernel1_size + 10
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

        # m2) Remove components touching the edge
        # 1) Make a copy and dilate with kernel=7
        pre_kernel_33 = filtered.copy()
        edge_check_kernel = np.ones((7, 7), np.uint8)
        dilated_for_edge_check = cv2.dilate(pre_kernel_33, edge_check_kernel, iterations=1)

        # 2) Find connected components and drop those touching the image edge
        num_labels_edge, labels_edge, stats_edge, _ = cv2.connectedComponentsWithStats(dilated_for_edge_check,
                                                                                       connectivity=8)

        # Create mask of components NOT touching the edge
        non_edge_mask = np.zeros_like(dilated_for_edge_check)
        for i in range(1, num_labels_edge):  # Skip background
            x, y, w, h, area = stats_edge[i]
            # Check if component touches any edge
            touches_left = (x == 0)
            touches_top = (y == 0)
            touches_right = (x + w >= img_width)
            touches_bottom = (y + h >= img_height)

            if not (touches_left or touches_top or touches_right or touches_bottom):
                # Component doesn't touch edge, keep it
                non_edge_mask[labels_edge == i] = 255

        # 3) AND with original pre_kernel_33 to get new filtered
        filtered = cv2.bitwise_and(pre_kernel_33, non_edge_mask)
        steps.append(("m2) Remove Edge-Touching", cv2.cvtColor(filtered, cv2.COLOR_GRAY2BGR)))

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

        # p) Find top N largest connected components
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(eroded, connectivity=8)

        if num_labels > 1:
            # Get areas and sort (skip background label 0)
            # Note: centroids from connectedComponentsWithStats are center of gravity (center of mass),
            # not bounding box centers - this is the geographical/weighted center of each blob
            component_data = []
            for i in range(1, num_labels):
                area = stats[i, cv2.CC_STAT_AREA]
                centroid = centroids[i]  # Center of gravity (center of mass)
                component_data.append((i, area, centroid))

            # Sort by area descending
            component_data.sort(key=lambda x: x[1], reverse=True)

            # Get top N blobs setting
            top_n = self.top_n_blobs.get()
            top_blobs = component_data[:top_n]

            # Create image with top N
            top_n_img = np.zeros_like(eroded)
            for label_id, area, centroid in top_blobs:
                top_n_img[labels == label_id] = 255
            steps.append((f"p) Top {top_n} Largest Blobs", cv2.cvtColor(top_n_img, cv2.COLOR_GRAY2BGR)))

            if len(top_blobs) >= 4:
                # p2) Fill sector gaps - add back blobs from empty sectors > 95°
                img_center = (eroded.shape[1] / 2, eroded.shape[0] / 2)
                top_blobs_filled = fill_sector_gaps(top_blobs, component_data, img_center, gap_threshold_deg=95)

                # Show gap-filled blobs if different from original
                if len(top_blobs_filled) > len(top_blobs):
                    gap_filled_img = np.zeros_like(eroded)
                    for label_id, area, centroid in top_blobs_filled:
                        gap_filled_img[labels == label_id] = 255
                    steps.append((f"p2) Gap Filled ({len(top_blobs_filled)} blobs)",
                                  cv2.cvtColor(gap_filled_img, cv2.COLOR_GRAY2BGR)))

                # q) Find equidistant blobs (4 or 5) using robust voting algorithm
                chosen_count, chosen_indices = find_equidistant_blobs(top_blobs_filled, img_center,
                                                                      len(top_blobs_filled),
                                                                      self.min_angle_deg, self.max_angle_deg)

                # Create result with chosen blobs
                result = np.zeros_like(eroded)
                for idx in chosen_indices:
                    label_id = top_blobs_filled[idx][0]
                    result[labels == label_id] = 255

                steps.append((f"q) {chosen_count} Equidistant Blobs (voted)", cv2.cvtColor(result, cv2.COLOR_GRAY2BGR)))

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


# def main():
#     root = tk.Tk()
#     app = BoltDetectorApp(root)
#     root.mainloop()
#
#
# if __name__ == "__main__":
#     main()