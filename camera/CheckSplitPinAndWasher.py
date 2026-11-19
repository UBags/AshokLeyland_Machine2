"""
CheckSplitPinAndWasher - state 20: the plate washer must cover the nut and both legs of the split pin must be turned
round the nut (CHANGES 162; before, a placeholder: OK whenever a QR code was known).

Each leg of the split pin is marked blue. Looking down on the nut, a pin that locks the nut has both legs bent round
and lying on the washer; a leg that is straight, half bent, standing up or bent outwards ends beyond the washer's
edge. So:

  1. Washer: the largest bright, unsaturated region (V >= WASHER_V_MIN, S <= WASHER_S_MAX) within SEARCH_RADIUS of
     CENTRE, closed with a WASHER_CLOSE_KERNEL square. A circle fitted to its outline gives the washer's centre; the
     95th percentile of the outline's distance from it is the tip radius of the scalloped edge.
     - no such region, or smaller than WASHER_AREA_MIN pixels             -> NOT OK, washer not found (no washer)
     - tip radius outside WASHER_TIP_RADIUS_MIN .. WASHER_TIP_RADIUS_MAX  -> NOT OK, washer outline not round (the
       washer is out of place, or something pale - an unsplit pin - lies across it)
     - the region seen round less than WASHER_COVER_MIN of the circle    -> NOT OK, the washer does not cover the nut
  2. Legs: blue marker pixels (hue MARKER_HUE_MIN .. MARKER_HUE_MAX, S >= MARKER_S_MIN, V >= MARKER_V_MIN)
     within MARKER_SEARCH_RADIUS of the washer's centre, opened with a 2 x 2 square.
     - fewer than MARKER_PIXELS_MIN                                        -> NOT OK, split pin legs not found
  3. Turned round: the distance of every marker pixel from the washer (the region with its holes filled).
     - more than LEG_OUTSIDE_PIXELS_MAX marker pixels farther than LEG_OUTSIDE_DISTANCE px -> NOT OK, a leg ends
       outside the washer (not turned round the nut)

The picture returned has outlines only: the washer outline (green OK / red NOT OK) and, when NOT OK because of a
leg, the outline of the marker beyond the washer (red). Values and the reason go to the log.

Measured on 19 OK and 60 NOT OK pictures of 2026-11-19 (Machine 2, 1280 x 720): every OK picture passes; 58 of the 60
NOT OK pictures are NOT OK. The two that pass (113431189, 113432153) are the same scene as the OK picture 113433320
taken 1 - 2 s later: their difference is camera noise (mean 2.0 grey levels on a blurred crop, as between two frames
of a still part).
"""

import time
from typing import Dict, Tuple

import cv2
import numpy as np

from utils.RedisUtils import DOST
from BaseUtils import getFullyQualifiedName
from Configuration import CosThetaConfigurator
from logutils.SlaveLoggers import logBoth
from logutils.Logger import MessageType

import warnings
warnings.filterwarnings('ignore', '.*h264.*', )

CosThetaConfigurator.getInstance()

GREEN = (0, 255, 0)
RED = (0, 0, 255)


def _filled(mask: np.ndarray) -> np.ndarray:
    """mask with its holes filled (flood fill of the background from the corner)."""
    m = mask.astype(np.uint8)
    flood = m.copy()
    cv2.floodFill(flood, np.zeros((m.shape[0] + 2, m.shape[1] + 2), np.uint8), (0, 0), 1)
    return (m | (1 - flood)).astype(bool)


class CheckSplitPinAndWasher():

    CENTRE = (644, 345)                  # nominal centre of the nut in the 1280 x 720 picture
    SEARCH_RADIUS = 100                  # the washer is looked for within this distance of CENTRE
    WASHER_V_MIN = 160
    WASHER_S_MAX = 70
    WASHER_CLOSE_KERNEL = 9
    WASHER_AREA_MIN = 2500               # pixels of the bright washer region (OK pictures: 3,689 - 4,640)
    WASHER_TIP_RADIUS_MIN = 44.0         # tip radius of the scalloped edge (OK pictures: 49.2 - 51.4 px)
    WASHER_TIP_RADIUS_MAX = 56.0
    WASHER_COVER_MIN = 0.9               # share of the circle round which the washer region is seen
    MARKER_HUE_MIN = 100
    MARKER_HUE_MAX = 130
    MARKER_S_MIN = 90
    MARKER_V_MIN = 55
    MARKER_SEARCH_RADIUS = 120
    MARKER_PIXELS_MIN = 100              # OK pictures: 204 and more
    LEG_OUTSIDE_DISTANCE = 8.0           # px beyond the washer's own edge
    LEG_OUTSIDE_PIXELS_MAX = 2

    @staticmethod
    def measure(anImage: np.ndarray) -> Dict:
        """Every value of the check on a BGR picture (1280 x 720). 'reason' is '' when OK. Masks under '_masks'."""
        C = CheckSplitPinAndWasher
        result = {"ok": False, "reason": ""}
        hsv = cv2.cvtColor(anImage, cv2.COLOR_BGR2HSV)
        H, S, V = (hsv[..., i].astype(np.int16) for i in range(3))
        h, w = H.shape
        yy, xx = np.mgrid[:h, :w]
        r0 = np.hypot(xx - C.CENTRE[0], yy - C.CENTRE[1])
        bright = ((V >= C.WASHER_V_MIN) & (S <= C.WASHER_S_MAX) & (r0 <= C.SEARCH_RADIUS)).astype(np.uint8)
        k = max(1, int(C.WASHER_CLOSE_KERNEL))
        closed = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
        n, labels, stats, _ = cv2.connectedComponentsWithStats(closed)
        if n < 2:
            result.update(washerArea=0, reason="washer not found (no bright washer round the nut)")
            return result
        label = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        region = labels == label
        area = int(stats[label, cv2.CC_STAT_AREA])
        result["washerArea"] = area
        contours, _ = cv2.findContours(region.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        outline = max(contours, key=cv2.contourArea)
        result["_masks"] = {"outline": outline}
        if area < C.WASHER_AREA_MIN:
            result["reason"] = f"washer not found (bright region {area} px < {C.WASHER_AREA_MIN})"
            return result
        points = outline.reshape(-1, 2).astype(np.float64)
        solution = np.linalg.lstsq(np.c_[2 * points, np.ones(len(points))], (points ** 2).sum(1), rcond=None)[0]
        cx, cy = float(solution[0]), float(solution[1])
        tipRadius = float(np.percentile(np.hypot(points[:, 0] - cx, points[:, 1] - cy), 95))
        result.update(centre=(round(cx, 1), round(cy, 1)), tipRadius=round(tipRadius, 1))
        if not (C.WASHER_TIP_RADIUS_MIN <= tipRadius <= C.WASHER_TIP_RADIUS_MAX):
            result["reason"] = (f"washer outline not round (tip radius {tipRadius:.1f} px outside "
                                f"{C.WASHER_TIP_RADIUS_MIN} - {C.WASHER_TIP_RADIUS_MAX}; washer out of place or "
                                f"something lying across it)")
            return result
        rw = np.hypot(xx - cx, yy - cy)
        angles = np.degrees(np.arctan2(yy[region] - cy, xx[region] - cx))
        cover = float(len(np.unique(np.floor(angles).astype(int))) / 360.0)
        result["cover"] = round(cover, 3)
        if cover < C.WASHER_COVER_MIN:
            result["reason"] = f"washer does not cover the nut (seen round {cover:.0%} < {C.WASHER_COVER_MIN:.0%})"
            return result
        marker = ((H >= C.MARKER_HUE_MIN) & (H <= C.MARKER_HUE_MAX) & (S >= C.MARKER_S_MIN) & (V >= C.MARKER_V_MIN)
                  & (rw <= C.MARKER_SEARCH_RADIUS)).astype(np.uint8)
        marker = cv2.morphologyEx(marker, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8)) > 0
        markerPixels = int(marker.sum())
        result["markerPixels"] = markerPixels
        if markerPixels < C.MARKER_PIXELS_MIN:
            result["reason"] = f"split pin legs not found ({markerPixels} blue px < {C.MARKER_PIXELS_MIN})"
            return result
        washer = _filled(region)
        distance = cv2.distanceTransform((~washer).astype(np.uint8), cv2.DIST_L2, 5)
        outside = marker & (distance > C.LEG_OUTSIDE_DISTANCE)
        outsidePixels = int(outside.sum())
        result["outsidePixels"] = outsidePixels
        result["outsideMax"] = round(float(distance[marker].max()), 1)
        result["_masks"]["outside"] = outside
        if outsidePixels > C.LEG_OUTSIDE_PIXELS_MAX:
            result["reason"] = (f"split pin leg not turned round the nut ({outsidePixels} blue px more than "
                                f"{C.LEG_OUTSIDE_DISTANCE} px outside the washer; at most "
                                f"{C.LEG_OUTSIDE_PIXELS_MAX} allowed)")
            return result
        result["ok"] = True
        return result

    @staticmethod
    def markedPicture(anImage: np.ndarray, result: Dict) -> np.ndarray:
        """Outlines only: the washer (green OK / red NOT OK) and the marker beyond the washer (red)."""
        marked = anImage.copy()
        masks = result.get("_masks", {})
        colour = GREEN if result.get("ok") else RED
        if "outline" in masks:
            cv2.drawContours(marked, [masks["outline"]], -1, colour, 2)
        outside = masks.get("outside")
        if outside is not None and not result.get("ok") and outside.any():
            grown = cv2.dilate(outside.astype(np.uint8), np.ones((5, 5), np.uint8))
            contours, _ = cv2.findContours(grown, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
            cv2.drawContours(marked, contours, -1, RED, 2)
        if not masks:
            cv2.rectangle(marked, (2, 2), (marked.shape[1] - 3, marked.shape[0] - 3), RED, 4)
        return marked

    @staticmethod
    def summary(result: Dict) -> str:
        keys = ("washerArea", "centre", "tipRadius", "cover", "markerPixels", "outsidePixels", "outsideMax")
        return ", ".join(f"{k} {result[k]}" for k in keys if k in result)

    @staticmethod
    def checkSplitPinAndWasher(anImage: np.ndarray, currentPictures: Dict[str, np.ndarray | None] = None,
                               componentQRCode: str = DOST, gamma: float = 2.0) -> Tuple[np.ndarray | None, bool]:
        _src = getFullyQualifiedName(__file__, CheckSplitPinAndWasher)
        if componentQRCode is None:
            logBoth('logError', _src, "CheckSplitPinAndWasher: NOT OK - no QR code for the component", MessageType.ISSUE)
            return anImage, False
        if anImage is None:
            logBoth('logError', _src, "CheckSplitPinAndWasher: NOT OK - no picture", MessageType.ISSUE)
            return anImage, False
        started = time.perf_counter()
        result = CheckSplitPinAndWasher.measure(anImage)
        marked = CheckSplitPinAndWasher.markedPicture(anImage, result)
        elapsed = time.perf_counter() - started
        values = CheckSplitPinAndWasher.summary(result)
        if result["ok"]:
            logBoth('logInfo', _src, f"CheckSplitPinAndWasher: OK in {elapsed:.3f} s - {values}", MessageType.SUCCESS)
        else:
            logBoth('logError', _src, f"CheckSplitPinAndWasher: NOT OK in {elapsed:.3f} s - {result['reason']}; {values}",
                    MessageType.ISSUE)
        return marked, bool(result["ok"])
