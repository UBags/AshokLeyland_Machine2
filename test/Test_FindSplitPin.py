"""
SplitPinAnalyser.py  –  v3

Pipeline changes:
  - inner_radius += 5 (from detected value)
  - Blur step removed
  - XOR and subsequent step removed
  - Connected-components cleanup on split Otsu: removes small white noise
  - Split-pin detection:
      * PIN PRESENT  : small white blob(s) exist near the inner edge after CC cleanup
      * PIN LOCKED   : the pin blob has high circularity (closed loop = turned legs)

Directory structure expected:
    <root>/<YYYY-MM-DD>/DOST/*.png   (and/or DOSTPLUS)
"""

import os, sys, re, math
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime
from typing import List, Tuple, Optional, Dict, Any

import math
import cv2
import numpy as np
from PIL import Image, ImageTk

# ── path helpers ──────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in [_HERE, os.path.join(_HERE, "camera"), os.path.join(_HERE, "utils")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from ImageNormalisationWithMask import extract_annular_region
    HAS_NORM = True
except ImportError:
    HAS_NORM = False

try:
    from camera.HexagonNutDetector import NutDetector as _NutDetector
    _NUT_DETECTOR = _NutDetector()   # singleton – loads models once
    HAS_NUT = True
except Exception as _e:
    print(f"[WARN] HexagonNutDetector not available: {_e}")
    HAS_NUT = False

# ── Directories ───────────────────────────────────────────────────────────────
NUT_DIR   = r"C:\AshokLeylandImages\4-NutAndPlateWasher"
SPLIT_DIR = r"C:\AshokLeylandImages\5-SplitPinAndWasher"

# ── Geometry defaults / NutDetector search params ────────────────────────────
# The detector scans within a coarse window; true centre is returned by detector.
NUT_SEARCH_CENTER = (632, 360)
NUT_SEARCH_RADIUS =  80          # outer_radius passed to detect_nut_in_image

CTB_CENTER  = (632, 360)         # fallback if detection fails
CTB_OUTER_R = 120
CTB_INNER_R =   0
OUTER_EXTRA =  15    # added to detected outer_r
INNER_EXTRA =   5    # added to detected inner_r

# ── Detection thresholds ──────────────────────────────────────────────────────
# Connected-components: components smaller than this fraction of the annulus
# area are treated as noise and removed.
CC_MIN_AREA_FRAC   = 0.005   # < 0.5% of annulus area → noise
# The largest component is the washer ring; any remaining components after
# removing noise are considered split-pin candidates.
# A pin blob is considered "locked" if its circularity exceeds this threshold.
# Circularity = 4π·area / perimeter²  (1.0 = perfect circle)
PIN_CIRCULARITY_LOCKED = 0.35   # bent/looped legs are reasonably round
R_MEAN_FIXED  = 41.6          # fixed mean washer radius (px)
K_VALID_RANGE = (11.5, 12.5)  # k outside this → washer not found

# ── Colour palette ────────────────────────────────────────────────────────────
BG      = "#1a1a2e"
PANEL   = "#16213e"
ACCENT  = "#e94560"
TEXT    = "#eaeaea"
SUBTEXT = "#8892a4"
BTN_BG  = "#0f3460"
BTN_HOV = "#e94560"
BORDER  = "#2a2a4a"

FONT_TITLE = ("Courier New", 13, "bold")
FONT_LABEL = ("Courier New",  9)
FONT_BTN   = ("Courier New", 10, "bold")
FONT_STEP  = ("Courier New",  8)

IMG_W,  IMG_H  = 380, 300
STEP_W, STEP_H = 180, 140

FILENAME_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d+)\.png$")

# ─────────────────────────────────────────────────────────────────────────────
# File helpers
# ─────────────────────────────────────────────────────────────────────────────

def parse_ts(filename: str) -> Optional[datetime]:
    m = FILENAME_RE.match(os.path.basename(filename))
    if not m:
        return None
    y, mo, d, h, mi, s, ms = (int(x) for x in m.groups())
    return datetime(y, mo, d, h, mi, s, ms * 1000)


def collect_files(root: str) -> List[Tuple[str, datetime]]:
    result = []
    if not os.path.isdir(root):
        return result
    for date_dir in sorted(os.listdir(root)):
        dp = os.path.join(root, date_dir)
        if not os.path.isdir(dp):
            continue
        for model_dir in sorted(os.listdir(dp)):
            mp = os.path.join(dp, model_dir)
            if not os.path.isdir(mp):
                continue
            for fname in sorted(os.listdir(mp)):
                full = os.path.join(mp, fname)
                ts = parse_ts(fname)
                if ts is not None:
                    result.append((full, ts))
    result.sort(key=lambda x: x[1])
    return result


def pair_files(
    nut_files:   List[Tuple[str, datetime]],
    split_files: List[Tuple[str, datetime]],
    max_gap_ms:  float = 2000.0,
) -> List[Tuple[str, str]]:
    used = set()
    pairs = []
    for nut_path, nut_ts in nut_files:
        best_idx, best_gap = None, float("inf")
        for i, (sp_path, sp_ts) in enumerate(split_files):
            if i in used:
                continue
            gap = (sp_ts - nut_ts).total_seconds() * 1000
            if 0 <= gap <= max_gap_ms and gap < best_gap:
                best_gap, best_idx = gap, i
        if best_idx is not None:
            used.add(best_idx)
            pairs.append((nut_path, split_files[best_idx][0]))
    return pairs

# ─────────────────────────────────────────────────────────────────────────────
# Geometry detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_nut_geometry(nut_bgr: np.ndarray) -> Dict[str, Any]:
    """
    Use HexagonNutDetector to find the true nut centre and radius.

    hex_center is in annular-crop space (origin = top-left of the
    2*NUT_SEARCH_RADIUS square).  Convert to original image space:
        orig_cx = hex_center[0] + (NUT_SEARCH_CENTER[0] - NUT_SEARCH_RADIUS)
        orig_cy = hex_center[1] + (NUT_SEARCH_CENTER[1] - NUT_SEARCH_RADIUS)

    outer_r is derived from minEnclosingCircle of the returned nut mask.
    inner_r uses the 25-px disc exclusion radius NutDetector applies internally.
    """
    if HAS_NUT:
        try:
            image_rgb = cv2.cvtColor(nut_bgr, cv2.COLOR_BGR2RGB)
            found, result = _NUT_DETECTOR.detect_nut_in_image(
                image_rgb=image_rgb,
                center=NUT_SEARCH_CENTER,
                outer_radius=NUT_SEARCH_RADIUS,
                inner_radius=0,
                image_id="nut_geo"
            )
            if found and result.get("hex_center"):
                offset_x = NUT_SEARCH_CENTER[0] - NUT_SEARCH_RADIUS
                offset_y = NUT_SEARCH_CENTER[1] - NUT_SEARCH_RADIUS
                hcx, hcy = result["hex_center"]
                cx = int(round(hcx + offset_x))
                cy = int(round(hcy + offset_y))

                # outer_r from nut mask bounding circle
                nut_mask = result.get("mask")
                if nut_mask is not None and nut_mask.any():
                    pts = cv2.findNonZero(nut_mask.astype(np.uint8))
                    if pts is not None:
                        (_, _), enc_r = cv2.minEnclosingCircle(pts)
                        outer_r = int(math.ceil(enc_r)) + OUTER_EXTRA
                    else:
                        outer_r = NUT_SEARCH_RADIUS + OUTER_EXTRA
                else:
                    outer_r = NUT_SEARCH_RADIUS + OUTER_EXTRA

                # inner_r: NutDetector excludes a 25-px disc around the bolt
                inner_r = 25 + INNER_EXTRA

                print(f"[INFO] NutDetector: centre=({cx},{cy}) "
                      f"outer_r={outer_r} inner_r={inner_r}")
                return {"center": (cx, cy), "inner_radius": inner_r,
                        "outer_radius": outer_r, "source": "NutDetector"}
        except Exception as e:
            print(f"[WARN] NutDetector geometry failed: {e}")

    print("[WARN] Geometry detection failed – using defaults")
    return {
        "center":       CTB_CENTER,
        "inner_radius": CTB_INNER_R + INNER_EXTRA,
        "outer_radius": CTB_OUTER_R + OUTER_EXTRA,
        "source":       "defaults",
    }

# ─────────────────────────────────────────────────────────────────────────────
# Image helpers
# ─────────────────────────────────────────────────────────────────────────────

def _annular_crop(bgr: np.ndarray, cx: int, cy: int,
                  outer_r: int, inner_r: int) -> Tuple[np.ndarray, np.ndarray]:
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    if HAS_NORM:
        crop, mask = extract_annular_region(
            rgb, center=(cx, cy),
            outer_radius=outer_r, inner_radius=inner_r, fill_color=0)
    else:
        h, w = rgb.shape[:2]
        yy, xx = np.ogrid[:h, :w]
        dist = np.sqrt((xx - cx)**2 + (yy - cy)**2)
        m_full = (dist <= outer_r) & (dist >= inner_r)
        result = rgb.copy()
        for c in range(3):
            result[:, :, c] = np.where(m_full, rgb[:, :, c], 0)
        x1, x2 = max(0, cx - outer_r), min(w, cx + outer_r)
        y1, y2 = max(0, cy - outer_r), min(h, cy + outer_r)
        crop = result[y1:y2, x1:x2]
        mask = m_full[y1:y2, x1:x2]
    return crop, mask


def _to_gray(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img, cv2.COLOR_RGB2GRAY) if img.ndim == 3 else img


def _apply_mask_gray(gray: np.ndarray, mask: np.ndarray) -> np.ndarray:
    out = gray.copy()
    out[~mask] = 0
    return out


def _otsu_masked(gray: np.ndarray, mask: np.ndarray) -> np.ndarray:
    px = gray[mask]
    if px.size > 0 and int(px.max()) > 0:
        _, out = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    else:
        out = np.zeros_like(gray)
    return _apply_mask_gray(out, mask)

# ─────────────────────────────────────────────────────────────────────────────
# Connected-components cleanup + split-pin analysis
# ─────────────────────────────────────────────────────────────────────────────

def _component_circularity(area: float, perimeter: float) -> float:
    if perimeter < 1.0:
        return 0.0
    return (4.0 * math.pi * area) / (perimeter ** 2)



def _cc_filter(
    binary:  np.ndarray,
    mask:    np.ndarray,
    outer_r: int,
) -> np.ndarray:
    """
    Four-stage connected-components filter.

    Stage 1  – Remove components with area < 20 pixels.
    Stage 1b – Remove components that touch the outer-radius boundary.
    Stage 2  – Dilate (kernel 3×3), remove components < 250 px in dilated image.
    Stage 3  – AND stage-1b result with stage-2 mask.
    """
    h, w = binary.shape[:2]
    crop_cx, crop_cy = w // 2, h // 2

    # ── Stage 1: area < 20 ───────────────────────────────────────────────────
    n1, lbl1, stats1, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    stage1 = np.zeros_like(binary)
    for lab in range(1, n1):
        if stats1[lab, cv2.CC_STAT_AREA] >= 20:
            stage1[lbl1 == lab] = 255

    # ── Stage 1b: remove components touching outer radius ───────────────────
    # A pixel at (r, c) touches the outer radius boundary if its distance from
    # the crop centre is >= outer_r - 2  (a 2-pixel tolerance band).
    # The largest component (washer ring) is always kept regardless.
    yy, xx = np.ogrid[:h, :w]
    dist_map = np.sqrt((xx - crop_cx)**2 + (yy - crop_cy)**2)
    outer_band = dist_map >= (outer_r - 2)

    n1b, lbl1b, stats1b, _ = cv2.connectedComponentsWithStats(stage1, connectivity=8)

    # Find the largest foreground component — never remove it
    largest_lab = max(
        range(1, n1b),
        key=lambda lab: stats1b[lab, cv2.CC_STAT_AREA],
        default=0,
    )

    stage1b = np.zeros_like(binary)
    for lab in range(1, n1b):
        comp_pixels = (lbl1b == lab)
        touches_outer = np.any(comp_pixels & outer_band)
        if lab == largest_lab or not touches_outer:
            stage1b[comp_pixels] = 255

    # ── Stage 2: dilate + remove < 250 ──────────────────────────────────────
    kernel  = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    dilated = cv2.dilate(stage1b, kernel, iterations=1)
    dilated = _apply_mask_gray(dilated, mask)

    n2, lbl2, stats2, _ = cv2.connectedComponentsWithStats(dilated, connectivity=8)
    stage2_mask = np.zeros_like(binary)
    for lab in range(1, n2):
        if stats2[lab, cv2.CC_STAT_AREA] >= 250:
            stage2_mask[lbl2 == lab] = 255

    # ── Stage 3: AND ────────────────────────────────────────────────────────
    filtered = cv2.bitwise_and(stage1b, stage2_mask)
    return filtered


def _fill_inner_disk(binary: np.ndarray, inner_r: int) -> np.ndarray:
    """
    Fill the disk of radius (inner_r + 1) at the image centre with white.
    Returns a new uint8 image.
    """
    out = binary.copy()
    h, w = out.shape[:2]
    cx, cy = w // 2, h // 2
    cv2.circle(out, (cx, cy), inner_r + 1, 255, -1)
    return out


def _annotate(overlay: np.ndarray, h: int, text: str, ok: bool) -> None:
    """Draw a single-line status label at the bottom of the overlay."""
    colour = (255, 220, 0) if ok else (255, 60, 60)
    cv2.putText(overlay, text, (4, h - 6),
                cv2.FONT_HERSHEY_SIMPLEX, 0.28, colour, 1, cv2.LINE_AA)


def _fit_sine_to_outer_edge(
    binary:    np.ndarray,
    outer_r:   int,
    filename:  str = "",
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Fit a sine wave to the outer edge of the largest CC in `binary`.

    Method
    ------
    1. Find the largest CC; extract its outer contour points.
    2. Convert contour points to polar (θ, r) relative to crop centre.
    3. Keep only outer-portion points (r ≥ 0.85 × median r).
    4. Compute r_mean and displacements d(θ) = r − r_mean.
    5. Remove outliers via 1.5×IQR.
    6. Estimate dominant k from FFT of the displacement series.
    7. Fit  d(θ) = A·sin(k·θ + φ) + offset  via curve_fit (k free).
    8. Compute λ = 2π·r_mean / k.
    9. Draw cyan edge points + yellow fitted curve; print results.
    """
    from scipy.optimize import curve_fit

    h, w = binary.shape[:2]
    cx, cy = w // 2, h // 2

    fit_info: Dict[str, Any] = {
        "r_mean":        R_MEAN_FIXED,
        "n_valid":       0,
        "amplitude":     None,
        "frequency":     None,
        "phase":         None,
        "offset":        None,
        "lambda_px":     None,
        "success":       False,
        "washer_found":  False,
    }

    overlay = cv2.cvtColor(binary, cv2.COLOR_GRAY2RGB)
    cv2.circle(overlay, (cx, cy), outer_r, (60, 120, 200), 1)

    # ── Largest CC outer contour ──────────────────────────────────────────────
    n_lbl, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    if n_lbl < 2:
        _annotate(overlay, h, "Sine fit: no components", ok=False)
        print(f"[SineFit] {filename}  WASHER NOT FOUND — no components")
        return overlay, fit_info

    largest_lab = max(range(1, n_lbl),
                      key=lambda lab: stats[lab, cv2.CC_STAT_AREA])
    comp_mask = (labels == largest_lab).astype(np.uint8) * 255
    contours, _ = cv2.findContours(comp_mask, cv2.RETR_EXTERNAL,
                                    cv2.CHAIN_APPROX_NONE)
    if not contours:
        _annotate(overlay, h, "Sine fit: no contour", ok=False)
        print(f"[SineFit] {filename}  WASHER NOT FOUND — no contour")
        return overlay, fit_info

    cnt    = max(contours, key=len)
    pts_xy = cnt.reshape(-1, 2).astype(float)

    # ── Polar conversion, keep outer portion ──────────────────────────────────
    dx = pts_xy[:, 0] - cx
    dy = pts_xy[:, 1] - cy
    raw_thetas = np.arctan2(dy, dx)
    raw_radii  = np.sqrt(dx**2 + dy**2)

    r_est      = float(np.median(raw_radii))
    outer_mask = raw_radii >= (r_est * 0.85)
    thetas_all = raw_thetas[outer_mask]
    radii_all  = raw_radii[outer_mask]

    if len(thetas_all) < 20:
        _annotate(overlay, h, "Sine fit: too few edge pts", ok=False)
        print(f"[SineFit] {filename}  WASHER NOT FOUND — too few edge points")
        return overlay, fit_info

    # ── Fixed R_MEAN; displacements relative to it ────────────────────────────
    r_mean = R_MEAN_FIXED
    displacements = radii_all - r_mean

    # ── Outlier removal via 1.5×IQR ───────────────────────────────────────────
    q1, q3  = np.percentile(displacements, [25, 75])
    iqr     = q3 - q1
    valid   = np.abs(displacements) <= (1.5 * iqr + 1e-6)
    thetas_v = thetas_all[valid]
    disp_v   = displacements[valid]
    fit_info["n_valid"] = int(np.sum(valid))

    # Draw edge points (cyan)
    for t, r in zip(thetas_all[valid], radii_all[valid]):
        px = int(round(cx + r * math.cos(t)))
        py = int(round(cy + r * math.sin(t)))
        if 0 <= px < w and 0 <= py < h:
            overlay[py, px] = (0, 200, 200)

    # ── Estimate k from FFT ───────────────────────────────────────────────────
    n_fft      = 512
    theta_grid = np.linspace(-math.pi, math.pi, n_fft, endpoint=False)
    disp_grid  = np.interp(theta_grid,
                           np.sort(thetas_v),
                           disp_v[np.argsort(thetas_v)],
                           period=2 * math.pi)
    fft_vals   = np.fft.rfft(disp_grid)
    freqs_cyc  = np.fft.rfftfreq(n_fft, d=1.0 / n_fft)
    dominant_k = float(freqs_cyc[1 + int(np.argmax(np.abs(fft_vals[1:])))])
    dominant_k = max(0.5, dominant_k)

    # ── curve_fit with k free ─────────────────────────────────────────────────
    def sine_model(x, A, k, phi, off):
        return A * np.sin(k * x + phi) + off

    try:
        p0     = [float(np.std(disp_v)), dominant_k, 0.0, 0.0]
        bounds = ([-outer_r, max(0.5, dominant_k - 2.0), -math.pi, -outer_r],
                  [ outer_r,          dominant_k + 2.0,   math.pi,  outer_r])
        popt, _ = curve_fit(sine_model, thetas_v, disp_v,
                            p0=p0, bounds=bounds, maxfev=12000)
        A_fit, k_fit, phi_fit, off_fit = popt

        lam_px        = (2.0 * math.pi * r_mean) / k_fit if k_fit > 0 else float("inf")
        k_lo, k_hi    = K_VALID_RANGE
        washer_found  = k_lo <= k_fit <= k_hi

        fit_info.update({
            "amplitude":    float(A_fit),
            "frequency":    float(k_fit),
            "phase":        float(phi_fit),
            "offset":       float(off_fit),
            "lambda_px":    lam_px,
            "success":      True,
            "washer_found": washer_found,
        })

        # Draw fitted curve: yellow if washer found, red if not
        curve_colour = (255, 220, 0) if washer_found else (255, 60, 60)
        curve_pts = []
        for theta in np.linspace(-math.pi, math.pi, 1440, endpoint=False):
            r_fit = r_mean + A_fit * math.sin(k_fit * theta + phi_fit) + off_fit
            px = int(round(cx + r_fit * math.cos(theta)))
            py = int(round(cy + r_fit * math.sin(theta)))
            if 0 <= px < w and 0 <= py < h:
                curve_pts.append([px, py])
        if len(curve_pts) > 2:
            arr = np.array(curve_pts, dtype=np.int32).reshape(-1, 1, 2)
            cv2.polylines(overlay, [arr], isClosed=True,
                          color=curve_colour, thickness=2)

        if washer_found:
            label = (f"k={k_fit:.2f}  A={A_fit:.2f}px  "
                     f"phi={math.degrees(phi_fit):.0f}deg  "
                     f"lambda={lam_px:.1f}px  R_fixed={r_mean}px  WASHER OK")
            _annotate(overlay, h, label, ok=True)
            print(f"[SineFit] {filename}  "
                  f"k={k_fit:.2f}  A={A_fit:.2f}px  "
                  f"phi={math.degrees(phi_fit):.1f}deg  "
                  f"lambda={lam_px:.1f}px  R_fixed={r_mean}px  "
                  f"n_valid={fit_info['n_valid']}  WASHER OK")
        else:
            label = f"k={k_fit:.2f} OUTSIDE [11.5,12.5] — WASHER NOT FOUND"
            _annotate(overlay, h, label, ok=False)
            print(f"[SineFit] {filename}  "
                  f"k={k_fit:.2f}  WASHER NOT FOUND (k outside [{k_lo},{k_hi}])")

    except Exception as e:
        fit_info["error"] = str(e)
        _annotate(overlay, h, f"Sine fit failed: {e}", ok=False)
        print(f"[SineFit] {filename}  FAILED: {e}")

    return overlay, fit_info


def analyse_split_pin(
    otsu_img: np.ndarray,
    mask:     np.ndarray,
    inner_r:  int,
    outer_r:  int,
    filename: str = "",
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, Dict[str, Any]]:
    """
    Analyse the filtered Otsu image for split-pin presence and lock state.

    Returns
    -------
    cleaned      : binary after CC size filter
    ann_overlay  : RGB annotation overlay
    sine_overlay : RGB sine-wave fit overlay
    verdict      : result dict
    """
    h, w = otsu_img.shape[:2]
    annulus_area = float(np.count_nonzero(mask))
    min_area = max(10, int(annulus_area * CC_MIN_AREA_FRAC))

    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
        otsu_img, connectivity=8)

    fg   = [(lab, int(stats[lab, cv2.CC_STAT_AREA]))
            for lab in range(1, num_labels)]
    keep = [item for item in fg if item[1] >= min_area]

    cleaned = np.zeros_like(otsu_img)
    for lab, _ in keep:
        cleaned[labels == lab] = 255

    if not keep:
        verdict = {"pin_present": False, "pin_locked": False,
                   "details": "No components after CC cleanup"}
        ann  = _make_overlay(otsu_img, mask, [], [], inner_r, outer_r, verdict)
        sine_overlay, _ = _fit_sine_to_outer_edge(otsu_img, outer_r, filename)
        return cleaned, ann, sine_overlay, verdict

    keep_sorted = sorted(keep, key=lambda x: x[1], reverse=True)
    washer_lab  = keep_sorted[0][0]
    pin_labs    = keep_sorted[1:]

    pin_info = []
    crop_cx, crop_cy = w // 2, h // 2

    for lab, area in pin_labs:
        comp_mask = (labels == lab).astype(np.uint8) * 255
        contours_c, _ = cv2.findContours(comp_mask, cv2.RETR_EXTERNAL,
                                          cv2.CHAIN_APPROX_SIMPLE)
        if not contours_c:
            continue
        cnt   = max(contours_c, key=cv2.contourArea)
        perim = cv2.arcLength(cnt, closed=True)
        circ  = _component_circularity(area, perim)
        cx_c, cy_c = centroids[lab]
        dist_from_centre = math.sqrt((cx_c - crop_cx)**2 + (cy_c - crop_cy)**2)
        near_inner = dist_from_centre < (inner_r + 0.35 * (outer_r - inner_r))

        pin_info.append({
            "label":       lab,
            "area":        area,
            "circularity": circ,
            "centroid":    (cx_c, cy_c),
            "near_inner":  near_inner,
            "contour":     cnt,
            "locked":      circ >= PIN_CIRCULARITY_LOCKED,
        })

    pin_present = len(pin_info) > 0
    pin_locked  = pin_present and any(p["locked"] for p in pin_info)

    detail_parts = []
    for i, p in enumerate(pin_info):
        detail_parts.append(
            f"blob{i+1}: area={p['area']} circ={p['circularity']:.2f} "
            f"near_inner={'Y' if p['near_inner'] else 'N'} "
            f"locked={'Y' if p['locked'] else 'N'}"
        )
    details = " | ".join(detail_parts) if detail_parts else "no pin blobs"

    verdict = {
        "pin_present": pin_present,
        "pin_locked":  pin_locked,
        "details":     details,
        "pin_info":    pin_info,
        "washer_lab":  washer_lab,
    }

    ann = _make_overlay(otsu_img, mask, keep_sorted, pin_info,
                        inner_r, outer_r, verdict)
    sine_overlay, sine_info = _fit_sine_to_outer_edge(cleaned, outer_r, filename)
    verdict["sine_fit"] = sine_info

    return cleaned, ann, sine_overlay, verdict


def _make_overlay(
    otsu_img:    np.ndarray,
    mask:        np.ndarray,
    keep_sorted: list,
    pin_info:    list,
    inner_r:     int,
    outer_r:     int,
    verdict:     Dict[str, Any],
) -> np.ndarray:
    base = cv2.cvtColor(otsu_img, cv2.COLOR_GRAY2RGB).astype(np.float32) * 0.35
    ann  = base.astype(np.uint8)

    h, w = ann.shape[:2]
    cx, cy = w // 2, h // 2

    cv2.circle(ann, (cx, cy), outer_r, (60, 200, 255), 1)
    if inner_r > 0:
        cv2.circle(ann, (cx, cy), inner_r, (255, 200, 60), 1)

    for p in pin_info:
        colour = (60, 255, 80) if p["locked"] else (255, 160, 20)
        cv2.drawContours(ann, [p["contour"]], -1, colour, 2)
        ccx, ccy = int(p["centroid"][0]), int(p["centroid"][1])
        cv2.drawMarker(ann, (ccx, ccy), colour, cv2.MARKER_CROSS, 10, 1)
        cv2.putText(ann, f"{p['circularity']:.2f}",
                    (ccx + 4, ccy - 4), cv2.FONT_HERSHEY_SIMPLEX,
                    0.35, colour, 1, cv2.LINE_AA)

    pin_txt  = "PIN: PRESENT" if verdict["pin_present"] else "PIN: ABSENT"
    lock_txt = ("LOCKED" if verdict["pin_locked"] else
                "NOT LOCKED" if verdict["pin_present"] else "—")
    pin_col  = (60, 255, 80)  if verdict["pin_present"] else (255, 60, 60)
    lock_col = (60, 255, 80)  if verdict["pin_locked"]  else (255, 160, 20)

    banner_y = h - 28
    cv2.rectangle(ann, (0, banner_y), (w, h), (20, 20, 40), -1)
    cv2.putText(ann, pin_txt,  (4, banner_y + 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.38, pin_col,  1, cv2.LINE_AA)
    cv2.putText(ann, lock_txt, (4, banner_y + 24),
                cv2.FONT_HERSHEY_SIMPLEX, 0.38, lock_col, 1, cv2.LINE_AA)

    return ann

# ─────────────────────────────────────────────────────────────────────────────
# Full pipeline
# ─────────────────────────────────────────────────────────────────────────────

def run_pipeline(nut_path: str, split_path: str) -> Dict[str, Any]:
    steps: Dict[str, Any] = {}

    nut_bgr   = cv2.imread(nut_path)
    split_bgr = cv2.imread(split_path)
    if nut_bgr is None:
        raise FileNotFoundError(f"Cannot read: {nut_path}")
    if split_bgr is None:
        raise FileNotFoundError(f"Cannot read: {split_path}")

    steps["00_nut_orig"]   = cv2.cvtColor(nut_bgr,   cv2.COLOR_BGR2RGB)
    steps["00_split_orig"] = cv2.cvtColor(split_bgr, cv2.COLOR_BGR2RGB)

    # Geometry
    geo     = detect_nut_geometry(nut_bgr)
    cx, cy  = geo["center"]
    inner_r = geo["inner_radius"]
    outer_r = geo["outer_radius"]
    steps["_geo"] = geo

    # Annular extraction
    nut_crop,   nut_mask   = _annular_crop(nut_bgr,   cx, cy, outer_r, inner_r)
    split_crop, split_mask = _annular_crop(split_bgr, cx, cy, outer_r, inner_r)

    steps["01_nut_annular"]   = nut_crop.copy()
    steps["01_split_annular"] = split_crop.copy()

    # Grayscale
    nut_gray   = _apply_mask_gray(_to_gray(nut_crop),   nut_mask)
    split_gray = _apply_mask_gray(_to_gray(split_crop), split_mask)

    steps["02_nut_gray"]   = nut_gray.copy()
    steps["02_split_gray"] = split_gray.copy()

    # Bilateral  d=15, σC=20, σS=20
    nut_bil1   = _apply_mask_gray(cv2.bilateralFilter(nut_gray,   15, 20, 20), nut_mask)
    split_bil1 = _apply_mask_gray(cv2.bilateralFilter(split_gray, 15, 20, 20), split_mask)

    steps["03_nut_bil1"]   = nut_bil1.copy()
    steps["03_split_bil1"] = split_bil1.copy()

    # Sharpening via unsharp mask (amount=1.5, σ=1.0) before Otsu
    def _sharpen(gray: np.ndarray, m: np.ndarray) -> np.ndarray:
        blurred = cv2.GaussianBlur(gray, (0, 0), 1.0)
        sharp   = cv2.addWeighted(gray, 2.5, blurred, -1.5, 0)
        return _apply_mask_gray(sharp, m)

    nut_sharp   = _sharpen(nut_bil1,   nut_mask)
    split_sharp = _sharpen(split_bil1, split_mask)

    steps["04b_nut_sharp"]   = nut_sharp.copy()
    steps["04b_split_sharp"] = split_sharp.copy()

    # Otsu on sharpened image
    nut_otsu   = _otsu_masked(nut_sharp,   nut_mask)
    split_otsu = _otsu_masked(split_sharp, split_mask)

    steps["05_nut_otsu"]   = nut_otsu.copy()
    steps["05_split_otsu"] = split_otsu.copy()

    # CC filter: (1) remove <20  (2) remove touching outer_r  (3) dilate+250  (4) AND
    split_filtered = _cc_filter(split_otsu, split_mask, outer_r)
    steps["06_split_filtered"] = split_filtered.copy()

    # Fill inner disk up to inner_r + 1 with white
    split_filled = _fill_inner_disk(split_filtered, inner_r)
    steps["06b_split_filled"] = split_filled.copy()

    # Split-pin analysis + sine-wave fit (operate on filled image)
    cleaned, ann_overlay, sine_overlay, verdict = analyse_split_pin(
        split_filled, split_mask, inner_r, outer_r, split_path)

    steps["07_split_cleaned"]  = cleaned.copy()
    steps["07_split_analysis"] = ann_overlay.copy()
    steps["07_split_sine"]     = sine_overlay.copy()
    steps["_verdict"]          = verdict

    return steps

# ─────────────────────────────────────────────────────────────────────────────
# PIL / Tk helpers
# ─────────────────────────────────────────────────────────────────────────────

def to_pil(img: np.ndarray) -> Image.Image:
    if img.ndim == 2:
        return Image.fromarray(img, mode="L").convert("RGB")
    return Image.fromarray(img.astype(np.uint8))


def make_photo(img: np.ndarray, w: int, h: int) -> ImageTk.PhotoImage:
    return ImageTk.PhotoImage(to_pil(img).resize((w, h), Image.LANCZOS))

# ─────────────────────────────────────────────────────────────────────────────
# Pipeline step metadata
# ─────────────────────────────────────────────────────────────────────────────

STEP_META = [
    ("00_nut_orig",        "00_split_orig",      "Originals",
     "Nut+Washer",         "SplitPin+Washer"),
    ("01_nut_annular",     "01_split_annular",   "Annular Crop",
     "Nut annulus",        "Split annulus"),
    ("02_nut_gray",        "02_split_gray",      "Grayscale",
     "Nut gray",           "Split gray"),
    ("03_nut_bil1",        "03_split_bil1",      "Bilateral 15/20/20",
     "Nut bilateral",      "Split bilateral"),
    ("04b_nut_sharp",      "04b_split_sharp",    "Sharpen",
     "Nut sharpened",      "Split sharpened"),
    ("05_nut_otsu",        "05_split_otsu",      "Otsu (each)",
     "Nut Otsu",           "Split Otsu"),
    ("06_split_filtered",  None,                 "CC Filter",
     "Filtered (1+2+AND)", None),
    ("06b_split_filled",   None,                 "Inner Fill",
     "Inner disk filled",  None),
    ("07_split_cleaned",   "07_split_analysis",  "CC + Analysis",
     "Split cleaned",      "Pin analysis"),
    ("07_split_sine",      None,                 "Sine Fit",
     "Outer edge sine",    None),
]

# ─────────────────────────────────────────────────────────────────────────────
# GUI
# ─────────────────────────────────────────────────────────────────────────────

class SplitPinAnalyserApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Split Pin Analyser  v3")
        self.configure(bg=BG)
        self.resizable(True, True)

        self.pairs:   List[Tuple[str, str]] = []
        self.idx:     int = 0
        self.step:    int = 0
        self.steps:   Dict[str, Any] = {}
        self._photos: List[ImageTk.PhotoImage] = []

        self._build_ui()
        self._load_pairs()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Vertical scroll wrapper ───────────────────────────────────────────
        # All content lives in self._scroll_frame which sits inside a Canvas
        self._vscroll_canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        _vsb = ttk.Scrollbar(self, orient=tk.VERTICAL,
                             command=self._vscroll_canvas.yview)
        self._vscroll_canvas.configure(yscrollcommand=_vsb.set)
        _vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._vscroll_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._scroll_frame = tk.Frame(self._vscroll_canvas, bg=BG)
        self._scroll_win = self._vscroll_canvas.create_window(
            (0, 0), window=self._scroll_frame, anchor="nw")

        def _on_frame_configure(e):
            self._vscroll_canvas.configure(
                scrollregion=self._vscroll_canvas.bbox("all"))
        def _on_canvas_configure(e):
            self._vscroll_canvas.itemconfig(
                self._scroll_win, width=e.width)
        self._scroll_frame.bind("<Configure>", _on_frame_configure)
        self._vscroll_canvas.bind("<Configure>", _on_canvas_configure)

        # Mouse-wheel scrolling (Windows / Linux)
        def _on_mousewheel(e):
            self._vscroll_canvas.yview_scroll(int(-e.delta / 120), "units")
        self.bind_all("<MouseWheel>", _on_mousewheel)

        # Convenience alias — all widgets pack into _sf (scroll frame)
        _sf = self._scroll_frame

        # Top bar
        top = tk.Frame(_sf, bg=BG, pady=6)
        top.pack(fill=tk.X, padx=10)

        tk.Label(top, text="⬡  SPLIT PIN ANALYSER  v3",
                 font=("Courier New", 15, "bold"),
                 fg=ACCENT, bg=BG).pack(side=tk.LEFT)

        self.status_lbl = tk.Label(top, text="Loading…",
                                   font=FONT_LABEL, fg=SUBTEXT, bg=BG)
        self.status_lbl.pack(side=tk.RIGHT)

        self.geo_lbl = tk.Label(_sf, text="",
                                font=("Courier New", 8), fg=SUBTEXT, bg=BG)
        self.geo_lbl.pack(anchor=tk.W, padx=12)

        # Verdict banner (big, prominent)
        self.verdict_frame = tk.Frame(_sf, bg=BG)
        self.verdict_frame.pack(fill=tk.X, padx=10, pady=(0, 2))

        self.pin_lbl = tk.Label(self.verdict_frame, text="",
                                font=("Courier New", 14, "bold"),
                                bg=BG, fg=TEXT, width=22, anchor=tk.W)
        self.pin_lbl.pack(side=tk.LEFT, padx=(0, 20))

        self.lock_lbl = tk.Label(self.verdict_frame, text="",
                                 font=("Courier New", 14, "bold"),
                                 bg=BG, fg=TEXT, width=22, anchor=tk.W)
        self.lock_lbl.pack(side=tk.LEFT)

        self.detail_lbl = tk.Label(_sf, text="",
                                   font=("Courier New", 7), fg=SUBTEXT, bg=BG,
                                   wraplength=1100, justify=tk.LEFT)
        self.detail_lbl.pack(anchor=tk.W, padx=12)

        self.sine_lbl = tk.Label(_sf, text="",
                                 font=("Courier New", 9, "bold"), fg="#ffe040", bg=BG)
        self.sine_lbl.pack(anchor=tk.W, padx=12)

        # Main image panels
        main_row = tk.Frame(_sf, bg=BG)
        main_row.pack(fill=tk.X, padx=10, pady=(0, 4))

        panel_titles = ["Nut + Plate Washer", "Split Pin + Washer", "Pin Analysis"]
        self.img_panels    = []
        self.step_captions = []
        self.path_labels   = []   # full-path labels for panels 0 and 1
        for i, lbl in enumerate(panel_titles):
            col = tk.Frame(main_row, bg=PANEL,
                           highlightbackground=BORDER, highlightthickness=1)
            col.pack(side=tk.LEFT, expand=True, fill=tk.BOTH, padx=4)
            tk.Label(col, text=lbl, font=FONT_TITLE,
                     fg=ACCENT if i == 2 else TEXT,
                     bg=PANEL, pady=4).pack()
            cap = tk.Label(col, text="", font=FONT_STEP, fg=SUBTEXT, bg=PANEL)
            cap.pack()
            c = tk.Label(col, bg=PANEL)
            c.pack(pady=4)
            self.img_panels.append(c)
            self.step_captions.append(cap)
            # Full file path shown under image for panels 0 and 1
            if i < 2:
                pl = tk.Label(col, text="", font=("Courier New", 7),
                              fg=SUBTEXT, bg=PANEL, wraplength=380,
                              justify=tk.CENTER)
                pl.pack(pady=(0, 4))
                self.path_labels.append(pl)

        # Pipeline strip with scrollbar
        pipe_outer = tk.Frame(_sf, bg=PANEL,
                              highlightbackground=BORDER, highlightthickness=1)
        pipe_outer.pack(fill=tk.X, padx=10, pady=4)

        tk.Label(pipe_outer,
                 text="PIPELINE  — click a step to inspect  (scroll ▶)",
                 font=FONT_LABEL, fg=SUBTEXT, bg=PANEL, pady=2,
                 ).pack(anchor=tk.W, padx=6)

        scroll_frame = tk.Frame(pipe_outer, bg=PANEL)
        scroll_frame.pack(fill=tk.X, padx=4, pady=(0, 4))

        self.pipe_canvas = tk.Canvas(scroll_frame, bg=PANEL,
                                     highlightthickness=0, height=STEP_H + 80)
        hbar = ttk.Scrollbar(scroll_frame, orient=tk.HORIZONTAL,
                             command=self.pipe_canvas.xview)
        self.pipe_canvas.configure(xscrollcommand=hbar.set)
        hbar.pack(side=tk.BOTTOM, fill=tk.X)
        self.pipe_canvas.pack(side=tk.TOP, fill=tk.X, expand=True)
        self.pipe_canvas.bind("<MouseWheel>",
            lambda e: self.pipe_canvas.xview_scroll(int(-e.delta / 120), "units"))

        self.pipe_inner = tk.Frame(self.pipe_canvas, bg=PANEL)
        self.pipe_canvas.create_window((0, 0), window=self.pipe_inner, anchor="nw")
        self.pipe_inner.bind("<Configure>",
            lambda e: self.pipe_canvas.configure(
                scrollregion=self.pipe_canvas.bbox("all")))

        self.step_btns:   List[tk.Button] = []
        self.step_thumbs: List[List[tk.Label]] = []

        for si, (ka, kb, title, la, lb) in enumerate(STEP_META):
            frame = tk.Frame(self.pipe_inner, bg=PANEL,
                             highlightbackground=BORDER, highlightthickness=1)
            frame.pack(side=tk.LEFT, padx=3, pady=2, fill=tk.Y)

            btn = tk.Button(frame, text=f"▶ {title}",
                            font=FONT_STEP, fg=TEXT, bg=BTN_BG,
                            relief=tk.FLAT, bd=0, cursor="hand2",
                            command=lambda s=si: self._select_step(s))
            btn.pack(fill=tk.X, padx=2, pady=2)
            self.step_btns.append(btn)

            thumb_row = tk.Frame(frame, bg=PANEL)
            thumb_row.pack()
            thumbs = []
            for key, cap in [(ka, la), (kb, lb)]:
                if key is None:
                    continue
                sub = tk.Frame(thumb_row, bg=PANEL)
                sub.pack(side=tk.LEFT, padx=1)
                tk.Label(sub, text=cap, font=("Courier New", 7),
                         fg=SUBTEXT, bg=PANEL, wraplength=STEP_W).pack()
                img_lbl = tk.Label(sub, bg=PANEL)
                img_lbl.pack()
                thumbs.append(img_lbl)
            self.step_thumbs.append(thumbs)

        # Nav buttons
        nav = tk.Frame(_sf, bg=BG, pady=8)
        nav.pack()
        for txt, cmd in [("◀◀  PREV", self._prev), ("NEXT  ▶▶", self._next)]:
            tk.Button(nav, text=txt, font=FONT_BTN,
                      fg=TEXT, bg=BTN_BG, relief=tk.FLAT, bd=0,
                      padx=20, pady=6, cursor="hand2",
                      activebackground=BTN_HOV, activeforeground=TEXT,
                      command=cmd).pack(side=tk.LEFT, padx=12)

        self.pair_lbl = tk.Label(_sf, text="", font=FONT_LABEL,
                                 fg=SUBTEXT, bg=BG)
        self.pair_lbl.pack(pady=(0, 6))

    # ── Data ──────────────────────────────────────────────────────────────────

    def _load_pairs(self):
        self.status_lbl.config(text="Scanning directories…")
        self.update()
        nut_files   = collect_files(NUT_DIR)
        split_files = collect_files(SPLIT_DIR)
        self.pairs  = pair_files(nut_files, split_files)

        if not self.pairs:
            messagebox.showerror(
                "No pairs found",
                f"Nut dir  : {NUT_DIR}\n"
                f"Split dir: {SPLIT_DIR}\n\n"
                f"Found {len(nut_files)} nut files, "
                f"{len(split_files)} split files.")
            self.status_lbl.config(text="No pairs found")
            return

        self.idx  = 0
        self.step = 0
        self.status_lbl.config(text=f"{len(self.pairs)} pairs found")
        self._show_current()

    # ── Navigation ────────────────────────────────────────────────────────────

    def _prev(self):
        if not self.pairs:
            return
        self.idx  = (self.idx - 1) % len(self.pairs)
        self.step = 0
        self._show_current()

    def _next(self):
        if not self.pairs:
            return
        self.idx  = (self.idx + 1) % len(self.pairs)
        self.step = 0
        self._show_current()

    def _select_step(self, si: int):
        self.step = si
        self._highlight_step()
        self._update_main_panels()

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _show_current(self):
        if not self.pairs:
            return
        nut_path, split_path = self.pairs[self.idx]
        self.pair_lbl.config(
            text=f"Pair {self.idx+1} / {len(self.pairs)}")
        # Show full paths under the first two image panels
        if len(self.path_labels) >= 2:
            self.path_labels[0].config(text=nut_path)
            self.path_labels[1].config(text=split_path)
        self.status_lbl.config(text="Processing…")
        self.update()

        try:
            self.steps = run_pipeline(nut_path, split_path)
        except Exception as e:
            messagebox.showerror("Pipeline error", str(e))
            self.status_lbl.config(text=f"Error: {e}")
            return

        geo     = self.steps.get("_geo", {})
        cx, cy  = geo.get("center",       CTB_CENTER)
        inner_r = geo.get("inner_radius",  CTB_INNER_R + INNER_EXTRA)  # fallback
        outer_r = geo.get("outer_radius",  CTB_OUTER_R + OUTER_EXTRA)
        src     = self.steps.get("_geo", {}).get("source", "defaults")
        self.geo_lbl.config(
            text=f"Geometry ({src}):  centre=({cx},{cy})  "
                 f"inner_r={inner_r}  outer_r={outer_r}")

        # Update verdict labels
        verdict = self.steps.get("_verdict", {})
        pin_present = verdict.get("pin_present", False)
        pin_locked  = verdict.get("pin_locked",  False)
        details     = verdict.get("details",     "")

        pin_txt  = "✓ PIN PRESENT"  if pin_present else "✗ PIN ABSENT"
        lock_txt = ("✓ LOCKED"      if pin_locked   else
                    "⚠ NOT LOCKED"  if pin_present  else "—")

        pin_col  = "#3cff50" if pin_present else "#ff3c3c"
        lock_col = "#3cff50" if pin_locked  else ("#ffaa14" if pin_present else SUBTEXT)

        self.pin_lbl.config( text=pin_txt,  fg=pin_col)
        self.lock_lbl.config(text=lock_txt, fg=lock_col)
        self.detail_lbl.config(text=details)

        # Sine-wave lambda display
        sine_fit = verdict.get("sine_fit", {})
        if sine_fit.get("success"):
            k      = sine_fit["frequency"]     # fixed = SINE_K_FIXED
            r_mean = sine_fit["r_mean"]        # px
            A      = sine_fit["amplitude"]     # px
            lam    = sine_fit["lambda_px"]     # circumferential wavelength px
            phi    = math.degrees(sine_fit["phase"])
            washer_ok = sine_fit.get("washer_found", False)
            wstr = "WASHER OK" if washer_ok else "WASHER NOT FOUND"
            wcol = "#3cff50" if washer_ok else "#ff3c3c"
            self.sine_lbl.config(
                text=f"Sine fit:  k={k:.2f}  "
                     f"λ = {lam:.1f} px  |  "
                     f"A = {A:.2f} px  |  "
                     f"φ = {phi:.0f}°  |  "
                     f"R_fixed = {R_MEAN_FIXED} px  |  {wstr}",
                fg=wcol)
        else:
            err = sine_fit.get("error", "not run")
            self.sine_lbl.config(text=f"Sine fit: failed ({err})")

        self._fill_thumbnails()
        self._highlight_step()
        self._update_main_panels()
        self.status_lbl.config(text=f"{len(self.pairs)} pairs  |  ready")

    def _fill_thumbnails(self):
        self._photos.clear()
        for si, (ka, kb, _, _, _) in enumerate(STEP_META):
            t_idx = 0
            for key in [ka, kb]:
                if key is None:
                    continue
                img_data = self.steps.get(key)
                if img_data is not None and t_idx < len(self.step_thumbs[si]):
                    ph = make_photo(img_data, STEP_W, STEP_H)
                    self._photos.append(ph)
                    self.step_thumbs[si][t_idx].config(image=ph)
                t_idx += 1

    def _highlight_step(self):
        for si, btn in enumerate(self.step_btns):
            btn.config(bg=ACCENT if si == self.step else BTN_BG,
                       fg="#000000" if si == self.step else TEXT)

    def _update_main_panels(self):
        ka, kb, _, la, lb = STEP_META[self.step]

        def set_panel(pidx: int, key: Optional[str], caption: str):
            if key is None:
                ph = ImageTk.PhotoImage(Image.new("RGB", (IMG_W, IMG_H), PANEL))
                self._photos.append(ph)
                self.img_panels[pidx].config(image=ph)
                self.step_captions[pidx].config(text="")
                return
            img_data = self.steps.get(key)
            if img_data is None:
                return
            ph = make_photo(img_data, IMG_W, IMG_H)
            self._photos.append(ph)
            self.img_panels[pidx].config(image=ph)
            self.step_captions[pidx].config(text=caption)

        set_panel(0, ka,                    la or "")
        set_panel(1, kb,                    lb or "")
        set_panel(2, "07_split_sine",       "Outer edge sine fit")


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = SplitPinAnalyserApp()
    app.mainloop()