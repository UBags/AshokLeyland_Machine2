"""
CheckNutAndPlateWasher - Detects presence of Nut and Washer using HexagonNutDetector and color analysis.

New Detection Method (Washer Detection):
    1. Use HexagonNutDetector to detect nut and get nut mask
    2. If check.washers.presence.in.nutandplatewasher=False, return nut detection result
    3. If check.washers.presence.in.nutandplatewasher=True:
       - Extract annular regions from both nut and top bearing images
       - Create bearing mask in annular space
       - Exclude nut-occluded region from bearing mask
       - Calculate gamma-corrected R and R-B differences on non-overlapping region
       - Apply thresholds: diff_R > WASHER_THRESHOLD_R AND diff_RB > WASHER_THRESHOLD_RB

Returns:
    - Annotated image with nut in green, non-overlapping bearing in blue
    - True if washer present, False otherwise
"""

import cv2
import numpy as np
from typing import Dict, Tuple, Optional, Any
import sys
import os

# Add project root to path for imports
try:
    from BaseUtils import get_project_root
    _PROJECT_ROOT = get_project_root()
    if _PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT)
except:
    pass

from camera.HexagonNutDetector import NutDetector
from camera.CheckTopBearing import CheckTopBearing
from camera.VisionConfig import vInt, vFloat, vBool, vTuple

# Try to import production modules, fall back to mocks for standalone testing
try:
    from utils.RedisUtils import *
    from BaseUtils import *
    from Configuration import *
    from utils.QRCodeHelper import getModel_LHSRHS_AndTonnage, UNKNOWN
    from logutils.SlaveLoggers import logBoth
    from logutils.Logger import MessageType

    CosThetaConfigurator.getInstance()
except ImportError:
    # Standalone testing mode - define mocks
    DOST = "DOST"
    DOSTPLUS = "DOSTPLUS"
    hubAndBottomBearingPictureKeyString = "hubAndBottomBearingPicture"
    topBearingPictureKeyString = "topBearingPicture"

    def logBoth(level, source, msg, messageType=None):
        colours = {'logInfo': '\033[1;32m', 'logError': '\033[1;31m', 'logWarning': '\033[1;33m', 'logDebug': '\033[1;34m', 'logCritical': '\033[1;31m'}
        reset = '\033[0m'
        colour = colours.get(level, '')
        print(f"{colour}{msg}{reset}")

    class MessageType:
        GENERAL = 1
        SUCCESS = 2
        RISK = 3
        ISSUE = 4
        PROBLEM = 5

    def saveImage(img, name):
        pass

    def getModel_LHSRHS_AndTonnage(qrCode):
        return "DOST", "LHS", "Unknown"

    UNKNOWN = "Unknown"

    # Mock CosThetaConfigurator for standalone testing
    class CosThetaConfigurator:
        @classmethod
        def getInstance(cls):
            return cls

        @classmethod
        def getCheckWashersPresenceInNutAndPlateWasher(cls) -> bool:
            return True  # Default for standalone testing


class CheckNutAndPlateWasher:
    """
    Checks for the presence of Nut and Washer using HexagonNutDetector and color analysis.
    """

    # Washer detection parameters (configurable)
    WASHER_THRESHOLD_R = vFloat("vision.nutwasher.washer.threshold.r", 0.55)     # 1% FPR - R channel threshold
    WASHER_THRESHOLD_RB = vFloat("vision.nutwasher.washer.threshold.rb", 5.57)   # 1% FPR - R-B difference threshold
    WASHER_GAMMA = vFloat("vision.nutwasher.washer.gamma", 3.0)                  # Gamma correction value
    # Washer comparison area: the bearing ring's measured edges moved this many px into the race (outer edge inward,
    # inner edge outward), so that every pixel compared is on the race; then the nut's mask is removed from it
    WASHER_MASK_EDGE_MARGIN = vFloat("vision.nutwasher.washer.mask.edge.margin", 1.0)

    # Nut detection parameters
    NUT_DETECTION_CENTER = vTuple("vision.nutwasher.nut.detection.center", (632, 360))
    NUT_DETECTION_RADIUS = vInt("vision.nutwasher.nut.detection.radius", 80)

    # Accepted nut (the outline found by the nut detector): outer diameter (circle of the outline's area), px; the
    # width (outer radius minus the bore radius) is only reported, not limited. A shape outside these ranges
    # is not a nut (a bearing ring is about 92-102 px across).
    NUT_OUTER_DIAMETER_MIN = vFloat("vision.nutwasher.nut.outer.diameter.min", 64.0)
    NUT_OUTER_DIAMETER_MAX = vFloat("vision.nutwasher.nut.outer.diameter.max", 84.0)
    # and its mean diameter (outline and bore averaged round the nut) within this range (nuts 64-70, a whole bearing
    # 78-87, a disc the size of a bearing's bore 54-58)
    NUT_MEAN_DIAMETER_MIN = vFloat("vision.nutwasher.nut.mean.diameter.min", 60.0)
    NUT_MEAN_DIAMETER_MAX = vFloat("vision.nutwasher.nut.mean.diameter.max", 72.0)

    # Fallback when the nut detector accepts no hexagon: the top bearing ring finder (CheckTopBearing.findRing) on
    # the nut picture; a ring of a nut's size is accepted as the nut. Nuts: outer diameter 75-82 px, mean diameter
    # 64-68; bearings 92-102 / 78-87; washer only: no ring.
    NUT_RING_FALLBACK = vBool("vision.nutwasher.nut.ring.fallback", True)
    NUT_RING_OUTER_DIAMETER_MIN = vFloat("vision.nutwasher.nut.ring.outer.diameter.min", 70.0)
    NUT_RING_OUTER_DIAMETER_MAX = vFloat("vision.nutwasher.nut.ring.outer.diameter.max", 86.0)
    NUT_RING_MEAN_DIAMETER_MAX = vFloat("vision.nutwasher.nut.ring.mean.diameter.max", 74.0)

    # Singleton nut detector
    _nut_detector: Optional[NutDetector] = None
    _detector_initialized = False

    @classmethod
    def _get_nut_detector(cls) -> NutDetector:
        """Get or create singleton NutDetector instance."""
        _src = getFullyQualifiedName(__file__, cls)
        if not cls._detector_initialized or cls._nut_detector is None:
            logBoth('logDebug', _src, "[CheckNutAndPlateWasher] Initializing NutDetector...", MessageType.GENERAL)
            cls._nut_detector = NutDetector()
            cls._detector_initialized = True
            logBoth('logInfo', _src, "[CheckNutAndPlateWasher] NutDetector initialized", MessageType.SUCCESS)
        return cls._nut_detector

    @classmethod
    def _create_gamma_lut(cls, gamma: float) -> np.ndarray:
        """Create gamma correction lookup table."""
        inv_gamma = 1.0 / gamma
        return np.array([((i / 255.0) ** inv_gamma) * 255 for i in range(256)]).astype(np.uint8)

    @classmethod
    def _extract_annular_region(cls, image: np.ndarray, center: Tuple[int, int],
                               outer_radius: int) -> Tuple[np.ndarray, np.ndarray]:
        """
        Extract annular region from image.

        Returns:
            Tuple of (annular_image, annular_mask)
        """
        h, w = image.shape[:2]
        cx, cy = center

        # Create output image of size 2*outer_radius x 2*outer_radius
        size = 2 * outer_radius
        annular_image = np.zeros((size, size, 3), dtype=image.dtype)
        annular_mask = np.zeros((size, size), dtype=np.uint8)

        # Calculate crop region in original image
        x1 = max(0, cx - outer_radius)
        y1 = max(0, cy - outer_radius)
        x2 = min(w, cx + outer_radius)
        y2 = min(h, cy + outer_radius)

        # Calculate paste region in annular image
        paste_x1 = outer_radius - (cx - x1)
        paste_y1 = outer_radius - (cy - y1)
        paste_x2 = paste_x1 + (x2 - x1)
        paste_y2 = paste_y1 + (y2 - y1)

        # Copy image region
        annular_image[paste_y1:paste_y2, paste_x1:paste_x2] = image[y1:y2, x1:x2]

        # Create circular mask
        center_annular = (outer_radius, outer_radius)
        cv2.circle(annular_mask, center_annular, outer_radius, 255, -1)

        return annular_image, annular_mask

    @classmethod
    def _create_bearing_mask_in_annular_space(cls, bearing_geometry: Dict[str, Any],
                                              nut_annular_center: Tuple[int, int],
                                              nut_annular_radius: int,
                                              annular_image_shape: Tuple[int, int]) -> np.ndarray:
        """
        The area compared for the washer (before the nut is removed), in the nut search area's coordinates: the bearing
        ring found by the top bearing check, narrowed so that every pixel is on the bearing race - its outer edge moved
        WASHER_MASK_EDGE_MARGIN px inward and its inner edge the same outward (2 px narrower than the race for a margin
        of 1). Built from the ring's measured edges (inner_profile / outer_profile, every 2 degrees round
        center_precise) when the geometry has them, otherwise from its circles (precise radii when known).

        Args:
            bearing_geometry: the top bearing check's geometry ('center', 'inner_radius', 'outer_radius'; and
                              'center_precise', 'inner_profile', 'outer_profile', '*_radius_precise' when present)
            nut_annular_center: centre of the nut search area (picture coordinates)
            nut_annular_radius: radius of the nut search area
            annular_image_shape: (h, w) of the nut search area

        Returns:
            Mask (0 / 255) of the narrowed bearing ring in the nut search area
        """
        _src = getFullyQualifiedName(__file__, cls)
        crop_offset = np.array([nut_annular_center[0] - nut_annular_radius,
                                nut_annular_center[1] - nut_annular_radius], dtype=np.float64)
        margin = float(CheckNutAndPlateWasher.WASHER_MASK_EDGE_MARGIN)
        shift = 4                                   # sub-pixel drawing: coordinates in 1/16 px
        scale = float(1 << shift)
        h, w = annular_image_shape
        bearing_mask = np.zeros((h, w), dtype=np.uint8)
        centre = bearing_geometry.get("center_precise") or bearing_geometry["center"]
        c = np.asarray(centre, dtype=np.float64)
        outer_profile = bearing_geometry.get("outer_profile")
        inner_profile = bearing_geometry.get("inner_profile")

        if outer_profile and inner_profile:
            # Each pixel decides for itself: its distance from the ring centre must be at least the inner edge + margin
            # and at most the outer edge - margin, both edges taken at the pixel's angle (interpolated between the
            # measured rays, every 2 degrees)
            inner = np.asarray(inner_profile, dtype=np.float64) - c
            outer = np.asarray(outer_profile, dtype=np.float64) - c
            angles = np.mod(np.arctan2(inner[:, 1], inner[:, 0]), 2 * np.pi)
            order = np.argsort(angles)
            angles = angles[order]
            r_inner = np.hypot(inner[:, 0], inner[:, 1])[order]
            r_outer = np.hypot(outer[:, 0], outer[:, 1])[order]
            ring_angles = np.r_[angles[-1] - 2 * np.pi, angles, angles[0] + 2 * np.pi]
            ring_inner = np.r_[r_inner[-1], r_inner, r_inner[0]]
            ring_outer = np.r_[r_outer[-1], r_outer, r_outer[0]]
            ys, xs = np.mgrid[0:h, 0:w]
            dx = xs + crop_offset[0] - c[0]
            dy = ys + crop_offset[1] - c[1]
            r = np.hypot(dx, dy)
            a = np.mod(np.arctan2(dy, dx), 2 * np.pi)
            inside = (r >= np.interp(a, ring_angles, ring_inner) + margin) & (r <= np.interp(a, ring_angles, ring_outer) - margin)
            bearing_mask[inside] = 255
            source = "edge profile"
        else:
            r_out = float(bearing_geometry.get("outer_radius_precise", bearing_geometry["outer_radius"])) - margin
            r_in = float(bearing_geometry.get("inner_radius_precise", bearing_geometry["inner_radius"])) + margin
            q = np.round((c - crop_offset) * scale).astype(int)
            cv2.circle(bearing_mask, (int(q[0]), int(q[1])), int(round(r_out * scale)), 255, -1, cv2.LINE_8, shift)
            cv2.circle(bearing_mask, (int(q[0]), int(q[1])), int(round(r_in * scale)), 0, -1, cv2.LINE_8, shift)
            source = "circles"

        logBoth('logDebug', _src,
                f"[Bearing Mask] from the ring's {source}, edges moved {margin:.1f} px into the race: "
                f"{int(np.sum(bearing_mask == 255))} px", MessageType.GENERAL)
        return bearing_mask

    @classmethod
    def _create_non_overlapping_mask(cls, bearing_mask: np.ndarray,
                                     nut_mask: np.ndarray) -> Tuple[np.ndarray, int, int, int]:
        """
        Create bearing mask with nut region excluded.

        Returns:
            Tuple of (non_overlap_mask, original_area, nut_area, final_area)
        """
        _src = getFullyQualifiedName(__file__, cls)

        original_area = np.sum(bearing_mask == 255)
        nut_area = np.sum(nut_mask == 255)

        # Exclude nut from bearing
        non_overlap_mask = bearing_mask.copy()
        non_overlap_mask[nut_mask == 255] = 0

        final_area = np.sum(non_overlap_mask == 255)
        overlap_removed = original_area - final_area

        logBoth('logDebug', _src,
                f"[Mask Creation] Bearing: {original_area}px, Nut: {nut_area}px, "
                f"Overlap: {overlap_removed}px, Final: {final_area}px",
                MessageType.GENERAL)

        return non_overlap_mask, original_area, nut_area, final_area

    @classmethod
    def _calculate_color_differences(cls, bearing_annular_rgb: np.ndarray,
                                     nut_annular_rgb: np.ndarray,
                                     non_overlap_mask: np.ndarray,
                                     gamma: float) -> Tuple[float, float]:
        """
        Calculate gamma-corrected R and R-B differences.

        Returns:
            Tuple of (diff_R, diff_RB)
        """
        _src = getFullyQualifiedName(__file__, cls)

        gamma_lut = cls._create_gamma_lut(gamma)

        # Extract and gamma-correct channels
        bearing_red = gamma_lut[bearing_annular_rgb[:, :, 0]]
        bearing_blue = gamma_lut[bearing_annular_rgb[:, :, 2]]
        nut_red = gamma_lut[nut_annular_rgb[:, :, 0]]
        nut_blue = gamma_lut[nut_annular_rgb[:, :, 2]]

        # Calculate R-B
        bearing_rb = bearing_red.astype(np.float32) - bearing_blue.astype(np.float32)
        nut_rb = nut_red.astype(np.float32) - nut_blue.astype(np.float32)

        # Get masked pixels only
        mask_bool = non_overlap_mask == 255

        if np.sum(mask_bool) == 0:
            logBoth('logError', _src, "[Color Calc] ERROR: Empty mask!", MessageType.ISSUE)
            return 0.0, 0.0

        # Calculate averages
        bearing_r_avg = float(np.mean(bearing_red[mask_bool]))
        nut_r_avg = float(np.mean(nut_red[mask_bool]))
        bearing_rb_avg = float(np.mean(bearing_rb[mask_bool]))
        nut_rb_avg = float(np.mean(nut_rb[mask_bool]))

        diff_R = bearing_r_avg - nut_r_avg
        diff_RB = bearing_rb_avg - nut_rb_avg

        logBoth('logDebug', _src, f"[Color Results] diff_R={diff_R:.2f}, diff_RB={diff_RB:.2f}", MessageType.GENERAL)

        return diff_R, diff_RB

    @classmethod
    def _create_annotated_image(cls, original_image: np.ndarray,
                                nut_mask_full: np.ndarray,
                                bearing_mask_annular: Optional[np.ndarray],
                                nut_annular_center: Tuple[int, int],
                                nut_annular_radius: int,
                                washer_present: bool,
                                hex_center_original: Tuple[int, int],
                                bearing_geometry: Optional[Dict[str, Any]] = None) -> np.ndarray:
        """
        Create annotated image with nut in green and non-overlapping bearing in blue.

        Args:
            original_image: Original BGR image
            nut_mask_full: Nut mask in original image space
            bearing_mask_annular: Non-overlapping bearing mask in annular space (can be None)
            nut_annular_center: Center used for nut extraction
            nut_annular_radius: Radius used for nut extraction
            washer_present: Detection result
            hex_center_original: Nut centre in original image space for 240x240 crop output.
        """
        annotated = original_image.copy()

        # Draw bearing annulus in blue, centred on bearing centre relocated by the
        # same delta that separates hex_center_original from nut_annular_center
        # printBoldBlue(f"{hex_center_original = }")
        # printBoldBlue(f"{nut_annular_center = }")
        # printBoldBlue(f"{bearing_geometry = }")
        if bearing_geometry is not None:
            # delta_x = hex_center_original[0] - nut_annular_center[0]
            # delta_y = hex_center_original[1] - nut_annular_center[1]
            # print(f"{delta_x = }; {delta_y = }")
            # bearing_cx = int(round(bearing_geometry["center"][0] + delta_x))
            # bearing_cy = int(round(bearing_geometry["center"][1] + delta_y))
            # print(f"{bearing_cx = }; {bearing_cy = }")
            bearing_cx = int(round(bearing_geometry["center"][0]))
            bearing_cy = int(round(bearing_geometry["center"][1]))
            inner_r = int(bearing_geometry["inner_radius"])
            outer_r = int(bearing_geometry["outer_radius"])
            # print(f"{inner_r = }; {outer_r = }")

            # Fill annulus with light blue (BGR: 255, 191, 0 is amber — light blue is 255, 200, 150... use (200, 150, 50) in BGR)
            annulus_mask = np.zeros(original_image.shape[:2], dtype=np.uint8)
            cv2.circle(annulus_mask, (bearing_cx, bearing_cy), outer_r, 255, -1)
            cv2.circle(annulus_mask, (bearing_cx, bearing_cy), inner_r, 0, -1)
            light_blue_bgr = (200, 150, 50)  # light blue in BGR
            annotated[annulus_mask == 255] = (
                annotated[annulus_mask == 255] * 0.6 +
                np.array(light_blue_bgr) * 0.4
            ).astype(np.uint8)

            # Draw inner and outer circle outlines in solid blue
            cv2.circle(annotated, (bearing_cx, bearing_cy), inner_r, (255, 100, 0), 2)
            cv2.circle(annotated, (bearing_cx, bearing_cy), outer_r, (255, 100, 0), 2)

        # Draw nut in green
        if np.sum(nut_mask_full) > 0:
            contours, _ = cv2.findContours(nut_mask_full, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(annotated, contours, -1, (0, 255, 0), 2)  # Green

        # Add text result
        # result_text = "WASHER PRESENT" if washer_present else "WASHER MISSING"
        # color = (0, 255, 0) if washer_present else (0, 0, 255)
        # cv2.putText(annotated, result_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, color, 2)

        # Crop 240x240 centred on the nut centre
        # cx, cy = hex_center_original
        cx, cy = bearing_geometry["center"]
        half = 120
        h, w = annotated.shape[:2]
        x1 = max(cx - half, 0)
        y1 = max(cy - half, 0)
        x2 = min(cx + half, w)
        y2 = min(cy + half, h)
        cropped = annotated[y1:y2, x1:x2]
        # Pad to exactly 240x240 if near edge
        result = np.zeros((240, 240, 3), dtype=np.uint8)
        ch, cw = cropped.shape[:2]
        result[:ch, :cw] = cropped
        return result

    @staticmethod
    def _nut_from_ring(anImage: np.ndarray, detector_result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """
        Fallback when the nut detector accepts no hexagon: find a ring on the nut picture with the top bearing ring
        finder (CheckTopBearing.findRing) and accept it as the nut if it has a nut's size (outer diameter within
        NUT_RING_OUTER_DIAMETER_MIN-MAX, mean diameter at most NUT_RING_MEAN_DIAMETER_MAX). Returns a result like the
        nut detector's (mask = the ring's disc, 2 px wider, in the nut search area; annular_rgb; hex_center = the ring
        centre in the search area; annotated_original), or None.
        """
        _src = getFullyQualifiedName(__file__, CheckNutAndPlateWasher)
        failure = (detector_result or {}).get('failure_metrics') or {}
        why = failure.get('detail') or failure.get('reason') or 'no hexagon'
        ring = CheckTopBearing.findRing(anImage)
        if ring is None:
            logBoth('logError', _src, f"[Step 1] Nut detector: {why}; ring fallback: no ring found", MessageType.ISSUE)
            return None
        outer_d, mean_d = ring['outer_diameter'], ring['mean_diameter']
        if not (CheckNutAndPlateWasher.NUT_RING_OUTER_DIAMETER_MIN <= outer_d <= CheckNutAndPlateWasher.NUT_RING_OUTER_DIAMETER_MAX
                and mean_d <= CheckNutAndPlateWasher.NUT_RING_MEAN_DIAMETER_MAX):
            logBoth('logError', _src,
                    f"[Step 1] Nut detector: {why}; ring fallback: ring is not a nut's size: outer diameter {outer_d:.1f} px "
                    f"(accepted {CheckNutAndPlateWasher.NUT_RING_OUTER_DIAMETER_MIN:.0f}-"
                    f"{CheckNutAndPlateWasher.NUT_RING_OUTER_DIAMETER_MAX:.0f}), mean diameter {mean_d:.1f} px (accepted <= "
                    f"{CheckNutAndPlateWasher.NUT_RING_MEAN_DIAMETER_MAX:.0f})", MessageType.ISSUE)
            return None
        radius = CheckNutAndPlateWasher.NUT_DETECTION_RADIUS
        ncx, ncy = CheckNutAndPlateWasher.NUT_DETECTION_CENTER
        annular_bgr, _ = CheckNutAndPlateWasher._extract_annular_region(anImage, (ncx, ncy), radius)
        cx = int(round(ring['center_precise'][0] - (ncx - radius)))
        cy = int(round(ring['center_precise'][1] - (ncy - radius)))
        mask = np.zeros((2 * radius, 2 * radius), dtype=np.uint8)
        cv2.circle(mask, (cx, cy), int(round(ring['outer_radius_precise'])) + 2, 255, -1)
        annotated = anImage.copy()
        cv2.circle(annotated, (int(round(ring['center_precise'][0])), int(round(ring['center_precise'][1]))),
                   int(round(ring['outer_radius_precise'])), (0, 255, 0), 2)
        logBoth('logInfo', _src,
                f"[Step 1] Nut detector: {why}; nut accepted by the ring fallback: ring outer diameter {outer_d:.1f} px, "
                f"mean diameter {mean_d:.1f} px, centre ({ring['center_precise'][0]:.1f}, {ring['center_precise'][1]:.1f})",
                MessageType.SUCCESS)
        return {'mask': mask, 'annular_rgb': cv2.cvtColor(annular_bgr, cv2.COLOR_BGR2RGB), 'hex_center': (cx, cy),
                'annotated_original': annotated, 'composite_score': 0.0, 'from_ring': True, 'ring': ring}

    @staticmethod
    def _measure_nut(nut_mask: np.ndarray, annular_rgb: np.ndarray,
                     hex_center: Tuple[float, float]) -> Optional[Tuple[float, float, float]]:
        """
        Size of the nut found (all in the nut search area's coordinates, px):
          outer diameter = diameter of the circle with the area of the nut's outline (outer contour of nut_mask);
          bore radius    = the radius, between 0.6 and 0.9 of the outer radius round hex_center, where the step from
                           the dark bore to the bright nut face is strongest (median over every 2 degrees); the nut
                           face's inner edge is at 0.72-0.77, the collar of the shaft end inside the bore at 0.42-0.47;
          width          = outer radius - bore radius;
          mean diameter  = along every 2 degrees from hex_center, the outline's radius plus the bore edge's radius
                           (found within +/- 4 px of the bore circle), averaged round the nut.
        Returns (outer diameter, bore radius, width, mean diameter), or None if the mask has no outline.
        """
        contours, _ = cv2.findContours((nut_mask > 0).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if not contours:
            return None
        outline = max(contours, key=cv2.contourArea)
        outer_radius = float(np.sqrt(cv2.contourArea(outline) / np.pi))
        gray = cv2.GaussianBlur(cv2.cvtColor(annular_rgb, cv2.COLOR_RGB2GRAY).astype(np.float32), (3, 3), 0)
        radii = np.arange(0.6 * outer_radius, 0.9 * outer_radius, 0.5, dtype=np.float32)
        if len(radii) == 0:
            return None
        cx, cy = float(hex_center[0]), float(hex_center[1])
        scores = CheckTopBearing._ring_edge_scores(gray, np.full(len(radii), cx, np.float32),
                                                   np.full(len(radii), cy, np.float32), radii, False)
        bore_radius = float(radii[int(np.argmax(scores))])

        # mean diameter: outline radius and bore edge radius along every 2 degrees
        filled = np.zeros(nut_mask.shape[:2], np.uint8)
        cv2.drawContours(filled, [outline], -1, 1, -1)
        angles, bore_edges, _, _, _ = CheckTopBearing._ring_profile(gray, cx, cy, bore_radius, outer_radius)
        steps = np.arange(0.0, 2.0 * outer_radius, 0.25, dtype=np.float32)
        outline_radii = []
        for angle in np.radians(angles):
            xs = np.clip(np.round(cx + steps * np.cos(angle)).astype(int), 0, filled.shape[1] - 1)
            ys = np.clip(np.round(cy + steps * np.sin(angle)).astype(int), 0, filled.shape[0] - 1)
            inside = np.nonzero(filled[ys, xs])[0]
            outline_radii.append(float(steps[inside.max()]) if len(inside) else 0.0)
        mean_diameter = float(np.mean(np.asarray(outline_radii) + bore_edges))
        return 2.0 * outer_radius, bore_radius, outer_radius - bore_radius, mean_diameter

    @staticmethod
    def checkNutAndPlateWasher(
            anImage: np.ndarray,
            currentPictures: Dict[str, np.ndarray | None] = None,
            componentQRCode: str | None = DOST,
            gamma: float = 2.0,
            bearing_geometry: Optional[Dict[str, Any]] = None
    ) -> Tuple[np.ndarray | None, bool]:
        """
        Detect nut and washer using HexagonNutDetector and color analysis.

        Args:
            anImage: Current camera image (nut image, BGR format)
            currentPictures: Dict containing 'topBearingPicture'
            componentQRCode: Component QR code
            gamma: Unused (kept for compatibility)
            bearing_geometry: Dict with 'center', 'inner_radius', 'outer_radius' from CheckTopBearing.
                              Required when washer check is enabled.

        Returns:
            Tuple of (annotated_image, washer_present)
        """
        _src = getFullyQualifiedName(__file__, CheckNutAndPlateWasher)

        logBoth('logDebug', _src, "="*80, MessageType.GENERAL)
        logBoth('logDebug', _src, "[CheckNutAndPlateWasher] DETECTION START", MessageType.GENERAL)
        logBoth('logDebug', _src, "="*80, MessageType.GENERAL)

        # =====================================================================
        # VALIDATION
        # =====================================================================
        if anImage is None:
            logBoth('logError', _src, "[CheckNutAndPlateWasher] anImage is None", MessageType.ISSUE)
            return anImage, False

        if currentPictures is None:
            logBoth('logError', _src, "[CheckNutAndPlateWasher] currentPictures is None", MessageType.ISSUE)
            return anImage, False

        if componentQRCode is None:
            logBoth('logError', _src, "[CheckNutAndPlateWasher] componentQRCode is None", MessageType.ISSUE)
            return anImage, False

        # Get component type
        try:
            modelName, lhs_rhs, _ = getModel_LHSRHS_AndTonnage(componentQRCode)
            if modelName == UNKNOWN:
                modelName = "DOST"
        except:
            modelName = "DOST"
            lhs_rhs = "LHS"

        # =====================================================================
        # STEP 1: DETECT NUT
        # =====================================================================
        logBoth('logDebug', _src, "[Step 1] Detecting nut using HexagonNutDetector...", MessageType.GENERAL)

        detector = CheckNutAndPlateWasher._get_nut_detector()
        image_rgb = cv2.cvtColor(anImage, cv2.COLOR_BGR2RGB)

        nut_found, nut_result = detector.detect_nut_in_image(
            image_rgb=image_rgb,
            center=CheckNutAndPlateWasher.NUT_DETECTION_CENTER,
            outer_radius=CheckNutAndPlateWasher.NUT_DETECTION_RADIUS,
            inner_radius=0,
            image_id="nut_check"
        )

        nut_from_ring = False
        if not nut_found and CheckNutAndPlateWasher.NUT_RING_FALLBACK:
            # no hexagon accepted: a ring of a nut's size found by the top bearing ring finder is accepted as the nut
            from_ring = CheckNutAndPlateWasher._nut_from_ring(anImage, nut_result)
            if from_ring is not None:
                nut_found, nut_result, nut_from_ring = True, from_ring, True

        if not nut_found:
            logBoth('logError', _src, "[Step 1] Nut NOT detected", MessageType.ISSUE)
            # Return annotated image from detector (or original)
            _cx, _cy = CheckNutAndPlateWasher.NUT_DETECTION_CENTER
            _img = nut_result.get('annotated_original', anImage)
            _h, _w = _img.shape[:2]
            _x1, _y1 = max(_cx - 120, 0), max(_cy - 120, 0)
            _x2, _y2 = min(_cx + 120, _w), min(_cy + 120, _h)
            _cropped = _img[_y1:_y2, _x1:_x2]
            annotated = np.zeros((240, 240, 3), dtype=np.uint8)
            annotated[:_cropped.shape[0], :_cropped.shape[1]] = _cropped
            return annotated, False

        logBoth('logInfo', _src, "[Step 1] Nut DETECTED", MessageType.SUCCESS)

        # Compute hex_center in original image space (used for 240x240 crop in all paths)
        _hcx, _hcy = nut_result.get('hex_center', (CheckNutAndPlateWasher.NUT_DETECTION_RADIUS,
                                                    CheckNutAndPlateWasher.NUT_DETECTION_RADIUS))
        _offset_x = CheckNutAndPlateWasher.NUT_DETECTION_CENTER[0] - CheckNutAndPlateWasher.NUT_DETECTION_RADIUS
        _offset_y = CheckNutAndPlateWasher.NUT_DETECTION_CENTER[1] - CheckNutAndPlateWasher.NUT_DETECTION_RADIUS
        hex_center_original = (_hcx + _offset_x, _hcy + _offset_y)

        def _crop240(img: np.ndarray) -> np.ndarray:
            cx, cy = hex_center_original
            h, w = img.shape[:2]
            x1, y1 = max(cx - 120, 0), max(cy - 120, 0)
            x2, y2 = min(cx + 120, w), min(cy + 120, h)
            cropped = img[y1:y2, x1:x2]
            result = np.zeros((240, 240, 3), dtype=np.uint8)
            ch, cw = cropped.shape[:2]
            result[:ch, :cw] = cropped
            return result

        # =====================================================================
        # CHECK CONFIGURATION: Should we check washer presence?
        # =====================================================================
        # The nut must have a nut's size: outer diameter and width within the configured ranges (a nut accepted by
        # the ring fallback was sized by the ring ranges)
        nut_size = None if nut_from_ring else CheckNutAndPlateWasher._measure_nut(nut_result['mask'],
                                                                                 nut_result['annular_rgb'], (_hcx, _hcy))
        if (not nut_from_ring) and (nut_size is None or not (
                CheckNutAndPlateWasher.NUT_OUTER_DIAMETER_MIN <= nut_size[0] <= CheckNutAndPlateWasher.NUT_OUTER_DIAMETER_MAX
                and CheckNutAndPlateWasher.NUT_MEAN_DIAMETER_MIN <= nut_size[3] <= CheckNutAndPlateWasher.NUT_MEAN_DIAMETER_MAX)):
            size_text = ("no outline" if nut_size is None else
                         f"outer diameter {nut_size[0]:.1f} px (accepted {CheckNutAndPlateWasher.NUT_OUTER_DIAMETER_MIN:.0f}-"
                         f"{CheckNutAndPlateWasher.NUT_OUTER_DIAMETER_MAX:.0f}), mean "
                         f"diameter {nut_size[3]:.1f} px (accepted {CheckNutAndPlateWasher.NUT_MEAN_DIAMETER_MIN:.0f}-"
                         f"{CheckNutAndPlateWasher.NUT_MEAN_DIAMETER_MAX:.0f})")
            logBoth('logError', _src, f"[Step 1] Shape found is not a nut's size: {size_text}", MessageType.ISSUE)
            annotated = _crop240(nut_result.get('annotated_original', anImage))
            return annotated, False
        if not nut_from_ring:
            logBoth('logInfo', _src, f"[Step 1] Nut size: outer diameter {nut_size[0]:.1f} px, bore radius "
                                     f"{nut_size[1]:.1f} px, width {nut_size[2]:.1f} px, mean diameter {nut_size[3]:.1f} px",
                    MessageType.SUCCESS)

        check_washer = CosThetaConfigurator.getInstance().getCheckWashersPresenceInNutAndPlateWasher()

        if not check_washer:
            logBoth('logDebug', _src, "[Config] Washer check DISABLED - returning nut detection result", MessageType.GENERAL)
            logBoth('logInfo', _src, "[Result] Nut found - RETURNING TRUE (washer check skipped)", MessageType.SUCCESS)
            # Return nut detection result with green outline
            annotated = _crop240(nut_result.get('annotated_original', anImage))
            return annotated, True  # Nut found = True

        logBoth('logDebug', _src, "[Config] Washer check ENABLED - proceeding with washer detection", MessageType.GENERAL)

        # =====================================================================
        # STEP 2: GET PREREQUISITES FOR WASHER CHECK
        # =====================================================================

        # Get top bearing reference image
        top_bearing_image = currentPictures.get(topBearingPictureKeyString, None)
        if top_bearing_image is None:
            logBoth('logError', _src, "[CheckNutAndPlateWasher] Top Bearing image missing - cannot check washer", MessageType.ISSUE)
            annotated = _crop240(nut_result.get('annotated_original', anImage))
            return annotated, False

        # Get bearing geometry from parameter
        if bearing_geometry is None:
            logBoth('logError', _src, "[CheckNutAndPlateWasher] Bearing geometry not available - cannot check washer", MessageType.ISSUE)
            annotated = _crop240(nut_result.get('annotated_original', anImage))
            return annotated, False

        logBoth('logDebug', _src, f"[CheckNutAndPlateWasher] Bearing geometry: {bearing_geometry}", MessageType.GENERAL)

        nut_mask = nut_result['mask']  # Annular space
        nut_annular_rgb = nut_result['annular_rgb']  # RGB format

        # =====================================================================
        # STEP 3: EXTRACT BEARING ANNULAR REGION
        # =====================================================================
        logBoth('logDebug', _src, "[Step 3] Extracting bearing annular region...", MessageType.GENERAL)

        # Cut the top bearing picture exactly where the nut picture was cut (the nut detection centre), so that one
        # mask (the bearing ring, placed in the nut crop's coordinates in step 4) picks the same picture area in both
        # crops. (Cutting it round the bearing centre shifted the area compared in the bearing picture by the
        # distance between the bearing centre and the nut detection centre.)
        bearing_center = tuple(bearing_geometry["center"])
        bearing_annular_bgr, _ = CheckNutAndPlateWasher._extract_annular_region(
            top_bearing_image,
            center=tuple(CheckNutAndPlateWasher.NUT_DETECTION_CENTER),
            outer_radius=CheckNutAndPlateWasher.NUT_DETECTION_RADIUS
        )
        bearing_annular_rgb = cv2.cvtColor(bearing_annular_bgr, cv2.COLOR_BGR2RGB)

        logBoth('logInfo', _src, f"[Step 3] Extracted the top bearing picture's region at the nut detection centre "
                                 f"{tuple(CheckNutAndPlateWasher.NUT_DETECTION_CENTER)} (bearing centre {bearing_center})",
                MessageType.SUCCESS)

        # =====================================================================
        # STEP 4: CREATE MASKS
        # =====================================================================
        logBoth('logDebug', _src, "[Step 4] Creating bearing mask in annular space...", MessageType.GENERAL)

        bearing_mask = CheckNutAndPlateWasher._create_bearing_mask_in_annular_space(
            bearing_geometry,
            CheckNutAndPlateWasher.NUT_DETECTION_CENTER,
            CheckNutAndPlateWasher.NUT_DETECTION_RADIUS,
            nut_annular_rgb.shape[:2]
        )

        non_overlap_mask, orig_area, nut_area, final_area = CheckNutAndPlateWasher._create_non_overlapping_mask(
            bearing_mask, nut_mask
        )

        if final_area < 500:
            logBoth('logWarning', _src, f"[Step 4] WARNING: Very small bearing area ({final_area}px)", MessageType.RISK)

        # =====================================================================
        # STEP 5: CALCULATE COLOR DIFFERENCES
        # =====================================================================
        logBoth('logDebug', _src, f"[Step 5] Calculating color differences (gamma={CheckNutAndPlateWasher.WASHER_GAMMA})...", MessageType.GENERAL)

        diff_R, diff_RB = CheckNutAndPlateWasher._calculate_color_differences(
            bearing_annular_rgb,
            nut_annular_rgb,
            non_overlap_mask,
            CheckNutAndPlateWasher.WASHER_GAMMA
        )

        # =====================================================================
        # STEP 6: APPLY THRESHOLDS
        # =====================================================================
        logBoth('logDebug', _src, "="*80, MessageType.GENERAL)
        logBoth('logDebug', _src, "[Step 6] DECISION", MessageType.GENERAL)
        logBoth('logDebug', _src, "="*80, MessageType.GENERAL)
        logBoth('logDebug', _src,
                f"Thresholds: diff_R > {CheckNutAndPlateWasher.WASHER_THRESHOLD_R:.2f} AND "
                f"diff_RB > {CheckNutAndPlateWasher.WASHER_THRESHOLD_RB:.2f}",
                MessageType.GENERAL)
        logBoth('logDebug', _src,
                f"Measured:   diff_R = {diff_R:.2f} AND diff_RB = {diff_RB:.2f}",
                MessageType.GENERAL)

        washer_present = (diff_R > CheckNutAndPlateWasher.WASHER_THRESHOLD_R) and (diff_RB > CheckNutAndPlateWasher.WASHER_THRESHOLD_RB)

        if washer_present:
            logBoth('logInfo', _src, "✓ WASHER PRESENT - Top Bearing Detected", MessageType.SUCCESS)
        else:
            logBoth('logError', _src, "✗ WASHER MISSING - Nut Only", MessageType.ISSUE)
            if diff_R <= CheckNutAndPlateWasher.WASHER_THRESHOLD_R:
                logBoth('logError', _src, f"  Reason: R channel failed ({diff_R:.2f} <= {CheckNutAndPlateWasher.WASHER_THRESHOLD_R:.2f})", MessageType.ISSUE)
            if diff_RB <= CheckNutAndPlateWasher.WASHER_THRESHOLD_RB:
                logBoth('logError', _src, f"  Reason: R-B failed ({diff_RB:.2f} <= {CheckNutAndPlateWasher.WASHER_THRESHOLD_RB:.2f})", MessageType.ISSUE)

        logBoth('logDebug', _src, "="*80, MessageType.GENERAL)

        # =====================================================================
        # STEP 7: CREATE ANNOTATED IMAGE
        # =====================================================================
        # Create full-size nut mask
        crop_offset_x = CheckNutAndPlateWasher.NUT_DETECTION_CENTER[0] - CheckNutAndPlateWasher.NUT_DETECTION_RADIUS
        crop_offset_y = CheckNutAndPlateWasher.NUT_DETECTION_CENTER[1] - CheckNutAndPlateWasher.NUT_DETECTION_RADIUS

        nut_mask_full = np.zeros((anImage.shape[0], anImage.shape[1]), dtype=np.uint8)
        nut_mask_full[crop_offset_y:crop_offset_y + nut_mask.shape[0],
                     crop_offset_x:crop_offset_x + nut_mask.shape[1]] = nut_mask

        annotated = CheckNutAndPlateWasher._create_annotated_image(
            anImage,
            nut_mask_full,
            non_overlap_mask,
            CheckNutAndPlateWasher.NUT_DETECTION_CENTER,
            CheckNutAndPlateWasher.NUT_DETECTION_RADIUS,
            washer_present,
            hex_center_original,
            bearing_geometry=bearing_geometry
        )

        return annotated, washer_present