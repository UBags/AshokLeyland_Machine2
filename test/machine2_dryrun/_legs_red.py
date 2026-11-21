"""
_legs_red - the split pin legs by their red marking (Uddi, 2026-11-21 15:02; CHANGES 192), for Study_Red_Legs_UI.
The blue / not-washer-colour method stays in _legs_length.measure (= measureBlue).
  (a) the washer mask: the ring between the fitted outline and the inner edge, as _legs_length.ring() finds it
      (centroid of the outline, inner edge from the light-share jump after Otsu and the closings);
  (b) the pin legs: the red paint - hue <= RED_HUE_LOW or >= RED_HUE_HIGH (OpenCV 0 - 180), saturation >= RED_S_MIN,
      brightness >= RED_V_MIN, opened 2 x 2 (the maroon on the 2026-11-21 pictures: hue 150 - 180, S 55 - 170, V 25 -
      180; blue paint is hue 100 - 130, the washer and the seal have no saturation);
  (c) the washer mask binarised: red legs black, the rest of the ring white;
  (d) the black share on the legs half of the ring (area proportion), and the black length across the whole ring laid
      flat (degrees with at least RED_COLUMN_SHARE of their ring pixels black / 360);
  plus how far the red reaches past the outline on the legs half (px; a leg standing out reaches 20 - 40, an
  overhanging leg tip a few) and the washer present check (outline radius) of _legs_length.
Legs half: the 180 deg with the most red on the ring and just past the outline (the legs carry the paint); too little
red (< RED_SIDE_MIN px) -> opposite the pin head (the largest bright, unpainted blob just outside the outline).
"""
import math

import cv2
import numpy as np

import _legs_length as L

RED_HUE_LOW = 15                    # hue <= this ...
RED_HUE_HIGH = 155                  # ... or >= this (OpenCV hue 0 - 180)
RED_S_MIN = 35                      # the window's dropdown (20 .. 70)
RED_V_MIN = 30                      # the window's dropdown (15 .. 60)
RED_SIDE_MIN = 20                   # px of red on / just outside the washer to take the legs side from the red
RED_COLUMN_SHARE = 0.25             # a degree of the flat ring counts as black when this share of its pixels is red
REACH_MAX = 45                      # px past the outline looked at for the reach


def redPaint(crop: np.ndarray) -> np.ndarray:
    """The red marking on a BGR picture (bool)."""
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV).astype(int)
    H, S, V = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    red = ((H <= RED_HUE_LOW) | (H >= RED_HUE_HIGH)) & (S >= RED_S_MIN) & (V >= RED_V_MIN)
    return cv2.morphologyEx(red.astype(np.uint8), cv2.MORPH_OPEN, np.ones((2, 2), np.uint8)).astype(bool)


def measureRed(g: dict, row: dict, model: str) -> dict:
    """(a) - (d) on one picture's geometry g (Study_Washer_SAM.analyse + 'crop') and its values row. Returns
    dict(washerPresent, washerReason, side, axis, eyeMask, ring, red, black, blackShareLegs, blackLength,
    blackLengthLegs, reach, reachMask)."""
    out = dict(washerPresent=False, washerReason="", side=None, axis=None, eyeMask=None, ring={}, red=None,
               black=None, blackShareLegs=None, blackLength=None, blackLengthLegs=None, reach=0.0, reachMask=None)
    if g is None or g.get("M") is None or g.get("fo") is None or row.get("prof_r") is None:
        out["washerReason"] = row.get("fail") or "no washer outline"
        return out
    r = float(row["prof_r"])
    out["washerPresent"] = L.WASHER_R_RANGE[0] <= r <= L.WASHER_R_RANGE[1]
    out["washerReason"] = (f"outline r {r:.1f} px in {L.WASHER_R_RANGE[0]:g} - {L.WASHER_R_RANGE[1]:g}" if out["washerPresent"]
                           else f"outline r {r:.1f} px outside {L.WASHER_R_RANGE[0]:g} - {L.WASHER_R_RANGE[1]:g} (not a washer: hex nut?)")
    crop = g["crop"]
    h, w = crop.shape[:2]
    red = redPaint(crop)
    out["red"] = red
    R = L.ring(g, None)                                         # (a) the washer ring
    out["ring"] = R
    if not R.get("found"):
        out["washerReason"] += f"; ring not found ({R.get('reason')})"
        return out
    cx, cy = R["centroid"]
    yy, xx = np.mgrid[:h, :w]
    d = np.hypot(xx - cx, yy - cy)
    ang = np.degrees(np.arctan2(yy - cy, xx - cx)) % 360
    fo = g["fo"]
    c0x, c0y = (float(v) for v in g["cc"])                     # the outline radii are round the centre used
    a0 = (np.degrees(np.arctan2(yy - c0y, xx - c0x)) % 360).astype(int)
    FO = fo[np.clip(a0, 0, L.NB - 1)]
    d0 = np.hypot(xx - c0x, yy - c0y)
    ringMask = R["ringMask"]
    # the legs side: the half with the most red on the ring and just past the outline; else opposite the pin head
    nearRed = red & (ringMask | ((d0 > FO) & (d0 <= FO + REACH_MAX)))
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV).astype(int)
    Hh, Ss, Vv = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    paint = ((Hh >= 100) & (Hh <= 130) & (Ss >= 60)) | red
    band = (d0 >= FO + 3) & (d0 <= FO + 22)
    n, lab, st, _ = cv2.connectedComponentsWithStats((band & (Vv >= 110) & ~paint).astype(np.uint8), 8)
    comps = sorted([(st[k, cv2.CC_STAT_AREA], k) for k in range(1, n)], reverse=True)
    if int(nearRed.sum()) >= RED_SIDE_MIN:
        counts = np.bincount(ang[nearRed].astype(int) % 360, minlength=360).astype(float)
        window = np.convolve(np.r_[counts, counts], np.ones(181), "valid")[:360]
        out.update(side=f"most red ({int(nearRed.sum())} px)", axis=(int(np.argmax(window)) + 90) % 360)
        if comps and comps[0][0] >= L.EYE_MIN_AREA:             # the pin head drawn, for information
            out["eyeMask"] = lab == comps[0][1]
    elif comps and comps[0][0] >= L.EYE_MIN_AREA:
        eyeMask = lab == comps[0][1]
        ys, xs = np.nonzero(eyeMask)
        eye = math.degrees(math.atan2(ys.mean() - cy, xs.mean() - cx)) % 360
        out.update(eyeMask=eyeMask, side="opposite the pin head (little red)", axis=(eye + 180) % 360)
    else:
        out.update(side="no red, no pin head", axis=0.0)
    rel = np.abs(((ang - out["axis"]) + 180) % 360 - 180)
    legsHalf = ringMask & (rel <= 90)
    # (c) the ring binarised: red legs black, the rest white
    black = ringMask & red
    out["black"] = black
    # (d) the black share on the legs half; the black length across the whole ring (and the legs half)
    out["blackShareLegs"] = float((black & legsHalf).sum() / max(int(legsHalf.sum()), 1))
    out["blackShare"] = float(black.sum() / max(int(ringMask.sum()), 1))
    start = (out["axis"] + 180) % 360                           # columns 90 - 270 = the legs half
    col = ((ang - start) % 360).astype(int)
    nRing = np.bincount(col[ringMask], minlength=360).astype(float)
    nBlack = np.bincount(col[black], minlength=360).astype(float)
    blackCol = (nRing > 0) & (nBlack >= RED_COLUMN_SHARE * np.maximum(nRing, 1))
    out["blackLength"] = float(blackCol.sum() / 360.0)
    out["blackLengthLegs"] = float(blackCol[90:270].sum() / 180.0)
    out["flat"] = blackCol
    # the reach: red past the outline on the legs half, in pieces that touch the outline; the furthest px past it
    relAll = np.abs(((ang - out["axis"]) + 180) % 360 - 180)
    past = red & (d0 > FO + 1) & (d0 <= FO + REACH_MAX) & (relAll <= 100)
    n2, lab2, _, _ = cv2.connectedComponentsWithStats(past.astype(np.uint8), 8)
    keep = np.zeros((h, w), bool)
    for k in range(1, n2):
        comp = lab2 == k
        if (comp & (d0 <= FO + 4)).any():
            keep |= comp
    out["reachMask"] = keep
    out["reach"] = float((d0 - FO)[keep].max()) if keep.any() else 0.0
    return out


def drawnRed(g: dict, res: dict, scale: int = 3, half: int = 75) -> np.ndarray:
    """2 x 2 grid, each the washer enlarged: (1) the washer mask, light green on black; (2) the washer mask binarised -
    red legs black, the rest of the ring white, outside black; (3) the red paint found, pure red on the picture (past the
    outline: magenta); (4) the picture with the washer mask light green and the legs red, and the values."""
    crop = g["crop"]
    h, w = crop.shape[:2]
    R = res.get("ring") or {}
    green = np.array([144, 238, 144], np.uint8)
    M = R["ringMask"] if R.get("found") else np.zeros((h, w), bool)
    red = res["red"] if res.get("red") is not None else np.zeros((h, w), bool)
    p1 = np.zeros_like(crop); p1[M] = green
    p2 = np.zeros_like(crop); p2[M] = (255, 255, 255)
    if res.get("black") is not None:
        p2[res["black"]] = (0, 0, 0)
    p3 = crop.copy(); p3[red] = (0, 0, 255)
    if res.get("reachMask") is not None:
        p3[res["reachMask"]] = (255, 0, 255)
    p4 = crop.copy(); p4[M] = (0.55 * p4[M] + 0.45 * green).astype(np.uint8)
    p4[red & (M | (res["reachMask"] if res.get("reachMask") is not None else False))] = (0, 0, 255)
    if res.get("eyeMask") is not None:
        p4[res["eyeMask"]] = (0, 200, 255)
    tiles = [L._zoom(p, g, scale, half)[0] for p in (p1, p2, p3, p4)]
    L._text(tiles[0], ["(1) washer mask"] + ([f"inner edge r {R['rInner']:.1f}, outline tips r {R['rTips']:.1f}"]
                                          if R.get("found") else ["ring not found"])
                     + ([] if res.get("washerPresent") else ["washer: NOT found"]))
    L._text(tiles[1], ["(2) legs black, washer white"]
            + ([f"black share, legs half: {res['blackShareLegs']:.3f}"] if res.get("blackShareLegs") is not None else []))
    L._text(tiles[2], ["(3) red paint found", f"reach past outline: {res.get('reach', 0):.0f} px"])
    lines = ["(4) result"]
    if res.get("blackShareLegs") is not None:
        lines += [f"black share, legs half: {res['blackShareLegs']:.3f}",
                  f"black length, whole: {res['blackLength']:.3f}",
                  f"reach past outline: {res.get('reach', 0):.0f} px"]
    if not res.get("washerPresent"):
        lines.append("washer: NOT found")
    L._text(tiles[3], lines)
    hh = max(t.shape[0] for t in tiles); ww = max(t.shape[1] for t in tiles)
    tiles = [cv2.copyMakeBorder(t, 0, hh - t.shape[0], 0, ww - t.shape[1], cv2.BORDER_CONSTANT, value=(40, 40, 40)) for t in tiles]
    sep = np.full((hh, 4, 3), 90, np.uint8)
    top = np.hstack([tiles[0], sep, tiles[1]]); bottom = np.hstack([tiles[2], sep, tiles[3]])
    return np.vstack([top, np.full((4, top.shape[1], 3), 90, np.uint8), bottom])
