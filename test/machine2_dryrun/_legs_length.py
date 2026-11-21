"""
_legs_length - the split pin legs measured as a length over the washer (Uddi, 2026-11-21), for Study_Legs_Length_UI:
  (b) the washer: MobileSAM's mask and the fitted outline (Study_Washer_SAM.analyse); present = outline radius in
      WASHER_R_RANGE (hex nut 42.1 - 42.8 px, washers 45.6 - 47.9 on the 157 pictures of 2026-11-20 / 21);
  (c) the pin head (eye): the largest bright, unpainted object just outside the outline (as analyse); the legs lie on
      the opposite half. No eye -> the half of the washer with the most non-washer pixels on it;
  (d) the legs' imprint over the washer: pixels inside the completed outline, beyond the washer's inner edge (the
      dark ring where the washer meets the nut is left out), on the legs half, that are not washer-coloured (no blue
      needed; --legs-colour blue / both use the blue marker B - R >= 25, B - G >= 15); only pieces that start at the
      spindle (touch the inner edge) are kept;
  (e) holes inside those pieces filled, then thinned (Zhang-Suen) to a one-pixel line; each connected line is a leg,
      its length the sum of its pixel steps (1 straight, sqrt 2 diagonal);
  (f) the total of the legs' lengths, against a cut-off still to be chosen from the runs.
For comparison (information only) the length of what sticks out past the outline on the legs half (bright, or blue).
No production code is used or changed beyond the hole finder the study already calls.
"""
import math

import cv2
import numpy as np

NB = 360
WASHER_R_RANGE = (44.0, 50.0)       # px, fitted outline radius of a washer (the hex nut: 42.1 - 42.8)
EYE_MIN_AREA = 15                   # px, smallest eye blob taken
NUT_R = {"DOST": 28.0, "DOSTPLUS": 30.0}
# the washer ring (Uddi, 2026-11-21 11:06): width from the outline's outermost limits (tips) and from its innermost
# limits (valleys); Otsu on the inside of the outline, lowered; the inner edge searched round the estimate
RING_WIDTH_FROM_TIPS = 19.0         # px
RING_WIDTH_FROM_VALLEYS = 11.0      # px
OTSU_LOWER_BY = 10                  # grey levels below Otsu's threshold still count as light
INNER_SEARCH = 4.0                  # px either side of the estimated inner edge
INNER_STEP = 0.5                    # px between the circles tried
DARK_COLUMN_SHARE = 0.25            # a degree of the flat ring counts as dark when this share of its pixels is dark


def ring(g: dict, axis=None) -> dict:
    """(a) - (g): the washer ring between its fitted outline and its inner edge, the dark share in it and the dark
    length along it. dict(found, reason, centroid, rTips, rValleys, rEstimate, rInner, otsu, threshold, jumps,
    ringMask, light, darkShare, darkShareLegs, darkLength, darkLengthLegs, polar)."""
    out = dict(found=False, reason="", ringMask=None)
    crop = g["crop"]; fo = g["fo"]
    cx0, cy0 = (float(v) for v in g["cc"])
    h, w = crop.shape[:2]
    # (b) the outline as a polygon round the centre used, its centroid
    t = np.deg2rad(np.arange(NB) + 0.5)
    good = np.isfinite(fo)
    if good.sum() < 90:
        out["reason"] = "outline too short"
        return out
    pts = np.column_stack([cx0 + fo[good] * np.cos(t[good]), cy0 + fo[good] * np.sin(t[good])]).astype(np.float32)
    mo = cv2.moments(pts)
    if mo["m00"] == 0:
        out["reason"] = "outline has no area"
        return out
    cx, cy = mo["m10"] / mo["m00"], mo["m01"] / mo["m00"]
    outerMask = np.zeros((h, w), np.uint8)
    cv2.fillPoly(outerMask, [np.round(pts).astype(np.int32)], 1)
    outerMask = outerMask.astype(bool)
    # (c) the outline's limits round the centroid; inner edge estimate = the mean of the two widths
    rr = np.hypot(pts[:, 0] - cx, pts[:, 1] - cy)
    rTips, rValleys = float(np.percentile(rr, 98)), float(np.percentile(rr, 2))
    rEst = ((rTips - RING_WIDTH_FROM_TIPS) + (rValleys - RING_WIDTH_FROM_VALLEYS)) / 2
    # (d) Otsu inside the outline, lowered by OTSU_LOWER_BY
    grey = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    otsu, _ = cv2.threshold(grey[outerMask].reshape(-1, 1), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    thr = float(otsu) - OTSU_LOWER_BY
    light = (grey > thr) & outerMask
    # (d') circles round the estimate: the light share on each; the inner edge = the circle whose share jumps most
    # above the mean of the two next smaller circles
    yy, xx = np.mgrid[:h, :w]
    d = np.hypot(xx - cx, yy - cy)
    radii = np.arange(rEst - INNER_SEARCH - 2 * INNER_STEP, rEst + INNER_SEARCH + INNER_STEP / 2, INNER_STEP)
    share = []
    for r in radii:
        on = (np.abs(d - r) <= INNER_STEP / 2 + 0.25) & outerMask
        share.append(float(light[on].mean()) if on.any() else 0.0)
    jumps = [(share[i] - (share[i - 1] + share[i - 2]) / 2, float(radii[i])) for i in range(2, len(radii))]
    jump, rInner = max(jumps)
    # (e) the ring: inside the outline, outside the inner edge
    ringMask = outerMask & (d >= rInner)
    out.update(found=True, centroid=(cx, cy), rTips=rTips, rValleys=rValleys, rEstimate=rEst, rInner=rInner,
               jump=jump, otsu=float(otsu), threshold=thr, ringMask=ringMask, light=light)
    # (f) the dark share in the ring (and on the legs half)
    dark = ringMask & ~light
    ang = np.degrees(np.arctan2(yy - cy, xx - cx)) % 360
    out["darkShare"] = float(dark.sum() / max(int(ringMask.sum()), 1))
    if axis is not None:
        rel = np.abs(((ang - axis) + 180) % 360 - 180)
        half = ringMask & (rel <= 90)
        out["darkShareLegs"] = float((dark & half).sum() / max(int(half.sum()), 1))
    # (g) the ring laid flat (one column per degree, from the legs axis if known): a degree is dark when at least
    # DARK_COLUMN_SHARE of its ring pixels are dark; the dark length = dark degrees / 360 (legs half: / 180)
    start = 0.0 if axis is None else (axis + 180) % 360
    col = ((ang - start) % 360).astype(int)
    nRing = np.bincount(col[ringMask], minlength=360).astype(float)
    nDark = np.bincount(col[dark], minlength=360).astype(float)
    darkCol = (nRing > 0) & (nDark >= DARK_COLUMN_SHARE * np.maximum(nRing, 1))
    out["darkLength"] = float(darkCol.sum() / 360.0)
    out["polar"] = darkCol
    if axis is not None:
        out["darkLengthLegs"] = float(darkCol[90:270].sum() / 180.0)      # columns 90 - 270 = the legs half
    return out


def thin(img: np.ndarray) -> np.ndarray:
    """Zhang-Suen thinning of a 0 / 1 picture (no extra libraries)."""
    I = np.pad((img > 0).astype(np.uint8), 1)
    while True:
        changed = False
        for step in (0, 1):
            P2, P3, P4, P5, P6, P7, P8, P9 = (np.roll(np.roll(I, dy, 0), dx, 1) for dy, dx in
                                              ((1, 0), (1, -1), (0, -1), (-1, -1), (-1, 0), (-1, 1), (0, 1), (1, 1)))
            nb = [P2, P3, P4, P5, P6, P7, P8, P9]
            B = sum(n.astype(int) for n in nb)
            seq = nb + [P2]
            A = sum(((seq[i] == 0) & (seq[i + 1] == 1)).astype(int) for i in range(8))
            if step == 0:
                c = (P2 * P4 * P6 == 0) & (P4 * P6 * P8 == 0)
            else:
                c = (P2 * P4 * P8 == 0) & (P2 * P6 * P8 == 0)
            rm = (I == 1) & (B >= 2) & (B <= 6) & (A == 1) & c
            if rm.any():
                I[rm] = 0
                changed = True
        if not changed:
            return I[1:-1, 1:-1]


def lineLength(S: np.ndarray) -> float:
    """Length of a one-pixel line: straight steps 1, diagonal steps sqrt 2."""
    S = S.astype(np.uint8)
    straight = int((S[:, 1:] & S[:, :-1]).sum() + (S[1:, :] & S[:-1, :]).sum())
    diagonal = int((S[1:, 1:] & S[:-1, :-1]).sum() + (S[1:, :-1] & S[:-1, 1:]).sum())
    return float(straight + math.sqrt(2) * diagonal)


def measure(g: dict, row: dict, model: str, colour: str = "none") -> dict:
    """Everything of steps (b) - (f) on one picture's geometry g (Study_Washer_SAM.analyse, crop coordinates) and its
    values row. colour: "none" (default), "blue" or "both". Returns dict(washerPresent, washerR, washerReason, side,
    axis, eyeMask, legs (lengths, longest first), total, legsSkeleton, legsFill, innerEdge, outside, outsideBlue)."""
    out = dict(washerPresent=False, washerR=row.get("prof_r"), washerReason="", side=None, axis=None, eyeMask=None,
               legs=[], total=0.0, legsSkeleton=None, legsFill=None, innerEdge=None, outside=0.0, outsideBlue=0.0)
    if g is None or g.get("M") is None:
        out["washerReason"] = row.get("fail") or "no washer mask"
        return out
    if g.get("fo") is None or row.get("prof_r") is None:
        out["washerReason"] = row.get("fail") or "no outline fitted"
        return out
    r = float(row["prof_r"])
    out["washerPresent"] = WASHER_R_RANGE[0] <= r <= WASHER_R_RANGE[1]
    out["washerReason"] = (f"outline r {r:.1f} px in {WASHER_R_RANGE[0]:g} - {WASHER_R_RANGE[1]:g}" if out["washerPresent"]
                           else f"outline r {r:.1f} px outside {WASHER_R_RANGE[0]:g} - {WASHER_R_RANGE[1]:g} (not a washer: hex nut?)")
    crop = g["crop"]
    cx, cy = (float(v) for v in g["cc"])
    fo = g["fo"]
    Rn = g.get("Rn") or (g["nut"][2] if g.get("nut") else NUT_R.get(model, 28.0))
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV).astype(int)
    H, S, V = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    b = crop.astype(int)
    blue = ((b[..., 0] - b[..., 2]) >= 25) & ((b[..., 0] - b[..., 1]) >= 15)
    h, w = V.shape
    yy, xx = np.mgrid[:h, :w]
    d = np.hypot(xx - cx, yy - cy)
    ang = np.degrees(np.arctan2(yy - cy, xx - cx)) % 360
    FO = fo[np.clip(ang.astype(int), 0, NB - 1)]
    washer = (V >= 130) & (S <= 110)
    # the washer's inner edge: per 2 deg the first washer-coloured radius beyond the nut; the median round the circle
    firsts = []
    for a in range(0, 360, 2):
        t = math.radians(a + 0.5)
        for rr in np.arange(Rn - 4, Rn + 18, 0.5):
            x, y = int(round(cx + rr * math.cos(t))), int(round(cy + rr * math.sin(t)))
            if 0 <= x < w and 0 <= y < h and washer[y, x]:
                firsts.append(rr)
                break
    Rin = float(np.median(firsts)) if firsts else Rn + 4
    out["innerEdge"] = Rin
    over = (d >= Rin + 1.5) & (d <= FO - 2)                     # above the washer, inside the completed outline
    # (c) the pin head: the largest bright unpainted blob just outside the outline (as analyse)
    paint = (H >= 100) & (H <= 130) & (S >= 60)
    band = (d >= FO + 3) & (d <= FO + 22)
    n, lab, st, _ = cv2.connectedComponentsWithStats((band & (V >= 110) & ~paint).astype(np.uint8), 8)
    comps = sorted([(st[k, cv2.CC_STAT_AREA], k) for k in range(1, n)], reverse=True)
    if comps and comps[0][0] >= EYE_MIN_AREA:
        eyeMask = lab == comps[0][1]
        ys, xs = np.nonzero(eyeMask)
        eye = math.degrees(math.atan2(ys.mean() - cy, xs.mean() - cx)) % 360
        out.update(eyeMask=eyeMask, side="opposite the eye", axis=(eye + 180) % 360)
    else:                                                       # no eye: the half with most non-washer on it
        counts = np.bincount(ang[over & ~washer].astype(int) % 360, minlength=360).astype(float)
        window = np.convolve(np.r_[counts, counts], np.ones(181), "valid")[:360]
        out.update(side="most covered half (no eye found)", axis=(int(np.argmax(window)) + 90) % 360)
    rel = np.abs(((ang - out["axis"]) + 180) % 360 - 180)
    legsHalf = rel <= 90
    # (d) the imprint over the washer, pieces that start at the spindle
    ev = {"none": ~washer, "blue": blue, "both": ~washer | blue}[colour]
    m = cv2.morphologyEx((ev & over & legsHalf).astype(np.uint8), cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    n, lab, _, _ = cv2.connectedComponentsWithStats(m, 8)
    spindle = d <= Rin + 5
    keep = np.zeros_like(m)
    for k in range(1, n):
        comp = lab == k
        if (comp & spindle).any():
            keep |= comp.astype(np.uint8)
    # (e) holes filled, thinned, each line a leg
    ff = keep.copy()
    cv2.floodFill(ff, np.zeros((h + 2, w + 2), np.uint8), (0, 0), 1)
    fill = keep | (1 - ff)
    skel = thin(fill)
    n2, lab2, _, _ = cv2.connectedComponentsWithStats(skel.astype(np.uint8), 8)
    legs = sorted((lineLength(lab2 == k) for k in range(1, n2)), reverse=True)
    out.update(legs=[x for x in legs if x >= 3.0], legsSkeleton=skel.astype(bool), legsFill=fill.astype(bool))
    out["total"] = float(sum(out["legs"]))
    out["ring"] = ring(g, out["axis"])
    # information: what sticks out past the outline on the legs half (bright, or blue), as a length
    for key, evo in (("outside", V >= 75), ("outsideBlue", blue)):
        o = cv2.morphologyEx((evo & (d > FO + 2) & (d <= FO + 25) & legsHalf).astype(np.uint8), cv2.MORPH_OPEN,
                             np.ones((2, 2), np.uint8))
        n3, lab3, _, _ = cv2.connectedComponentsWithStats(o, 8)
        k3 = np.zeros_like(o)
        for k in range(1, n3):
            comp = lab3 == k
            if (comp & (d <= FO + 5)).any():
                k3 |= comp.astype(np.uint8)
        out[key] = lineLength(thin(k3))
    return out


def drawn(g: dict, res: dict, scale: int = 5, half: int = 75) -> np.ndarray:
    """The crop enlarged: the washer mask filled light green, the pin head red, the legs' lines red, and as text the
    length of each leg and the total (asked for on the picture, Uddi 2026-11-21)."""
    crop = g["crop"].copy()
    R = res.get("ring") or {}
    if R.get("found"):                                          # the ring between the outline and the inner edge
        M = R["ringMask"]
        crop[M] = (0.55 * crop[M] + 0.45 * np.array([144, 238, 144])).astype(np.uint8)   # light green (BGR)
    elif g.get("M") is not None:
        M = g["M"].astype(bool)
        crop[M] = (0.55 * crop[M] + 0.45 * np.array([144, 238, 144])).astype(np.uint8)   # light green (BGR)
    if res.get("eyeMask") is not None:
        crop[res["eyeMask"]] = (0, 0, 255)
    cx, cy = (int(round(v)) for v in g["cc"])                   # the washer and a margin round it
    x0, y0 = max(0, cx - half), max(0, cy - half)
    crop = crop[y0:cy + half, x0:cx + half]
    big = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    if res.get("legsSkeleton") is not None:
        ys, xs = np.nonzero(res["legsSkeleton"][y0:cy + half, x0:cx + half])
        for y, x in zip(ys, xs):
            cv2.rectangle(big, (x * scale, y * scale), (x * scale + scale - 1, y * scale + scale - 1), (0, 0, 255), -1)
    lines = [f"leg {i + 1}: {L:.0f} px" for i, L in enumerate(res.get("legs", []))]
    lines.append(f"total: {res.get('total', 0.0):.0f} px")
    if R.get("found"):
        lines.append(f"dark share: {R['darkShare']:.2f} (legs half {R.get('darkShareLegs', 0):.2f})")
        lines.append(f"dark length: {R['darkLength']:.2f} (legs half {R.get('darkLengthLegs', 0):.2f})")
    if not res.get("washerPresent"):
        lines.append("washer: NOT found")
    y = 28
    for t in lines:
        cv2.putText(big, t, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(big, t, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
        y += 30
    return big
