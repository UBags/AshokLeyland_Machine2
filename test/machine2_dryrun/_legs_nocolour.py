"""
_legs_nocolour - the split pin legs on the washer with no colour at all (Uddi, 2026-11-22 00:46; CHANGES 200), for
Study_NoColour_Legs_UI. Only grey values are used:
  1. the washer: MobileSAM's mask and the fitted outline (Study_Washer_SAM.analyse with STEPS no_colour: its bright
     prompt points by grey >= 130), washer present = outline radius (_legs_length.WASHER_R_RANGE), the ring (outline to
     inner edge) by _legs_length.ring (Otsu on grey, the light-share jump, optional further refinement);
  2. grey -> the guided (or bilateral) filter once on the whole ring, using only the grey values inside it;
  3. the legs side: opposite the pin head, the pin head = the largest blob of grey >= EYE_GREY just outside the outline
     (bright metal; no paint test); no pin head -> the half of the ring that is darkest after step 2 (lowest mean);
  4. Otsu + OTSU_ADJUST separately on the legs half and the pin head half: light = washer, dark = legs;
  5. two openings, two closings (circular kernels), inside the ring;
  6. black share on the legs half, black length across the whole ring (and the legs half);
  plus, for information, the reach past the outline by grey: pieces brighter than the seal (grey >= REACH_GREY) that
  touch the outline on the legs side, the furthest px past it - bright (unpainted) legs only; a dark painted leg on the
  dark seal is not seen without colour.
"""
import math

import cv2
import numpy as np

import _legs_length as L
import _legs_filtered as LF

EYE_GREY = 110                      # pin head: grey at least this, just outside the outline
REACH_GREY = 75                     # reach: past the outline, grey at least this (the seal is darker)
REACH_MAX = 45                      # px past the outline looked at


def measureNoColour(g: dict, row: dict, model: str, method: str = "guided") -> dict:
    out = dict(washerPresent=False, washerReason="", side=None, axis=None, eyeMask=None, ring={}, black=None,
               blackShareLegs=None, blackShare=None, blackLength=None, blackLengthLegs=None, reach=0.0, reachMask=None)
    if g is None or g.get("M") is None or g.get("fo") is None or row.get("prof_r") is None:
        out["washerReason"] = row.get("fail") or "no washer outline"
        return out
    r = float(row["prof_r"])
    out["washerPresent"] = L.WASHER_R_RANGE[0] <= r <= L.WASHER_R_RANGE[1]
    out["washerReason"] = (f"outline r {r:.1f} px in {L.WASHER_R_RANGE[0]:g} - {L.WASHER_R_RANGE[1]:g}" if out["washerPresent"]
                           else f"outline r {r:.1f} px outside {L.WASHER_R_RANGE[0]:g} - {L.WASHER_R_RANGE[1]:g} (not a washer)")
    crop = g["crop"]
    h, w = crop.shape[:2]
    grey = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    R = L.ring(g, None)
    out["ring"] = R
    if not R.get("found"):
        out["washerReason"] += f"; ring not found ({R.get('reason')})"
        return out
    ring = R["ringMask"]
    cx, cy = R["centroid"]
    yy, xx = np.mgrid[:h, :w]
    ang = np.degrees(np.arctan2(yy - cy, xx - cx)) % 360
    c0x, c0y = (float(v) for v in g["cc"])
    a0 = (np.degrees(np.arctan2(yy - c0y, xx - c0x)) % 360).astype(int)
    FO = g["fo"][np.clip(a0, 0, L.NB - 1)]
    d0 = np.hypot(xx - c0x, yy - c0y)
    # 2. the filter once on the whole ring
    f = LF.guidedInMask(grey, ring) if method == "guided" else LF.bilateralInMask(grey, ring)
    # 3. the legs side
    band = (d0 >= FO + 3) & (d0 <= FO + 22)
    n, lab, st, _ = cv2.connectedComponentsWithStats((band & (grey >= EYE_GREY)).astype(np.uint8), 8)
    comps = sorted([(st[k, cv2.CC_STAT_AREA], k) for k in range(1, n)], reverse=True)
    if comps and comps[0][0] >= L.EYE_MIN_AREA:
        eyeMask = lab == comps[0][1]
        ys, xs = np.nonzero(eyeMask)
        eye = math.degrees(math.atan2(ys.mean() - cy, xs.mean() - cx)) % 360
        out.update(eyeMask=eyeMask, side="opposite the pin head", axis=(eye + 180) % 360)
    else:
        sums = np.bincount(ang[ring].astype(int) % 360, weights=255.0 - f[ring].astype(float), minlength=360)
        window = np.convolve(np.r_[sums, sums], np.ones(181), "valid")[:360]
        out.update(side="darkest half (no pin head found)", axis=(int(np.argmax(window)) + 90) % 360)
    rel = np.abs(((ang - out["axis"]) + 180) % 360 - 180)
    legsHalf = ring & (rel <= 90)
    headHalf = ring & (rel > 90)
    # 4. Otsu + adjustment per half
    light = np.zeros((h, w), bool)
    otsus = []
    for half in (legsHalf, headHalf):
        if half.sum() < 10:
            otsus.append(None)
            continue
        t_, _ = cv2.threshold(f[half].reshape(-1, 1), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        otsus.append(float(t_))
        light |= (f > float(t_) + float(LF.OTSU_ADJUST)) & half
    # 5. open twice, close twice
    op = cv2.morphologyEx(light.astype(np.uint8), cv2.MORPH_OPEN, LF._disc(LF.OPEN_1))
    op = cv2.morphologyEx(op, cv2.MORPH_OPEN, LF._disc(LF.OPEN_2))
    light = cv2.morphologyEx(op, cv2.MORPH_CLOSE, LF._disc(LF.CLOSE_1))
    light = cv2.morphologyEx(light, cv2.MORPH_CLOSE, LF._disc(LF.CLOSE_2)).astype(bool) & ring
    black = ring & ~light
    # 6. the values
    out.update(black=black, grey=grey, filtered=f, opened=f, binaryLight=light, filterOtsus=otsus,
               filterOtsu=otsus[0] if otsus[0] is not None else (otsus[1] or 0.0), filterMethod=method)
    out["blackShareLegs"] = float((black & legsHalf).sum() / max(int(legsHalf.sum()), 1))
    out["blackShare"] = float(black.sum() / max(int(ring.sum()), 1))
    start = (out["axis"] + 180) % 360
    col = ((ang - start) % 360).astype(int)
    nRing = np.bincount(col[ring], minlength=360).astype(float)
    nBlack = np.bincount(col[black], minlength=360).astype(float)
    blackCol = (nRing > 0) & (nBlack >= LF.COLUMN_SHARE * np.maximum(nRing, 1))
    out["blackLength"] = float(blackCol.sum() / 360.0)
    out["blackLengthLegs"] = float(blackCol[90:270].sum() / 180.0)
    # reach past the outline by grey (information)
    past = (grey >= REACH_GREY) & (d0 > FO + 1) & (d0 <= FO + REACH_MAX) & (rel <= 100)
    if out.get("eyeMask") is not None:
        past &= ~out["eyeMask"]
    past = cv2.morphologyEx(past.astype(np.uint8), cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    n2, lab2, _, _ = cv2.connectedComponentsWithStats(past, 8)
    keep = np.zeros((h, w), bool)
    for k in range(1, n2):
        comp = lab2 == k
        if (comp & (d0 <= FO + 4)).any():
            keep |= comp
    out["reachMask"] = keep
    out["reach"] = float((d0 - FO)[keep].max()) if keep.any() else 0.0
    out["reachKind"] = "grey"
    return out


def drawnNoColour(g, res):
    """As _legs_filtered.drawnFiltered (the reach labelled grey)."""
    view = LF.drawnFiltered(g, res)
    return view
