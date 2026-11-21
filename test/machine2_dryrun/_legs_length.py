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
OTSU_ADJUST = 0                     # added to Otsu's threshold (the window's dropdown, -50 .. +50; CHANGES 187, 188)
CLOSE_KERNEL = 4                    # px, circular closing of the binarised (light) picture after Otsu (dropdown 3 / 5 / 7 / 9; CHANGES 188)
INNER_SEARCH = 4.0                  # px either side of the estimated inner edge
INNER_STEP = 0.5                    # px between the circles tried
DARK_COLUMN_SHARE = 0.25
# further refinement of the inner edge (Uddi, 2026-11-21 18:42; CHANGES 197): from the inner edge found, the grey values
# on the circle are split into dark / light by Otsu (on those values), the dark-light changes counted; the radius grows
# by 1 px until a circle has a long unbroken run of light (the washer itself, past the dark notches next to the nut)
REFINE_INNER = False                # the window's "Further refine inner boundary of washer" check box
REFINE_INNER_STEPS = 12             # px, how far out it looks at most
REFINE_LIGHT_RUN = 0.25             # share of the circle the longest light run must reach
REFINE_SAMPLES = 720                # points on each circle            # a degree of the flat ring counts as dark when this share of its pixels is dark


CLOSE_KERNEL_2 = 4                  # px, the second circular closing (dropdown 3 / 4; CHANGES 191)


def ring(g: dict, axis=None, otsuAdjust=None, closeKernel=None, closeKernel2=None) -> dict:
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
    # (d) Otsu inside the outline, + the adjustment (OTSU_ADJUST, the window's dropdown)
    grey = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    otsu, _ = cv2.threshold(grey[outerMask].reshape(-1, 1), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    adjust = OTSU_ADJUST if otsuAdjust is None else otsuAdjust
    thr = float(otsu) + float(adjust)
    light = (grey > thr) & outerMask
    # closing with a circular kernel: small dark gaps in the light washer (shadows, scallop valleys) closed
    k = int(CLOSE_KERNEL if closeKernel is None else closeKernel)
    if k > 1:
        light = cv2.morphologyEx(light.astype(np.uint8), cv2.MORPH_CLOSE,
                                 cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))).astype(bool) & outerMask
    # then a second circular closing (CHANGES 189; its kernel from a dropdown since 191)
    k2 = int(CLOSE_KERNEL_2 if closeKernel2 is None else closeKernel2)
    if k2 > 1:
        light = cv2.morphologyEx(light.astype(np.uint8), cv2.MORPH_CLOSE,
                                 cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k2, k2))).astype(bool) & outerMask
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
    # (e') further refinement of the inner edge (REFINE_INNER)
    refineLog = []
    rFirst = rInner
    if REFINE_INNER:
        tt = np.linspace(0, 2 * np.pi, REFINE_SAMPLES, endpoint=False)
        chosen = None
        for step in range(0, REFINE_INNER_STEPS + 1):
            rr_ = rInner + step
            xs_ = np.clip(np.round(cx + rr_ * np.cos(tt)).astype(int), 0, w - 1)
            ys_ = np.clip(np.round(cy + rr_ * np.sin(tt)).astype(int), 0, h - 1)
            vals = grey[ys_, xs_].astype(np.uint8)
            onOutline = outerMask[ys_, xs_]
            if onOutline.mean() < 0.5:                          # past the outline: stop
                break
            t_, _ = cv2.threshold(vals.reshape(-1, 1), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            lit = vals > t_
            changes = int(np.count_nonzero(lit != np.roll(lit, 1)))
            # the longest circular run of light
            if lit.all():
                run = len(lit)
            elif not lit.any():
                run = 0
            else:
                k0 = int(np.argmin(lit))                        # start just after a dark sample
                seq = np.roll(lit, -k0)
                best = cur = 0
                for v in seq:
                    cur = cur + 1 if v else 0
                    best = max(best, cur)
                run = best
            share = run / float(len(lit))
            refineLog.append((round(float(rr_), 1), int(t_), changes, round(share, 3)))
            if share >= REFINE_LIGHT_RUN:
                chosen = rr_
                break
        if chosen is not None:
            rInner = float(chosen)
    # (e) the ring: inside the outline, outside the inner edge
    ringMask = outerMask & (d >= rInner)
    out.update(rInnerJump=rFirst, refineLog=refineLog, refined=REFINE_INNER and rInner != rFirst)
    out.update(found=True, centroid=(cx, cy), rTips=rTips, rValleys=rValleys, rEstimate=rEst, rInner=rInner,
               jump=jump, otsu=float(otsu), adjust=float(adjust), threshold=thr, close=k, close2=k2, ringMask=ringMask, light=light,
               dark=ringMask & ~light)
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


def measure(g: dict, row: dict, model: str, colour: str = "none", otsuAdjust=None, closeKernel=None,
            closeKernel2=None) -> dict:
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
    # (d) the imprint over the washer: since CHANGES 187 the dark pixels of the Otsu-binarised ring (colour "none"; the
    # blue marker with "blue" / "both"), on the legs half, pieces that start at the ring's inner edge (the spindle)
    R = ring(g, out["axis"], otsuAdjust, closeKernel, closeKernel2)
    out["ring"] = R
    if R.get("found"):
        ringD = np.hypot(xx - R["centroid"][0], yy - R["centroid"][1])
        inRing = R["ringMask"]
        evRing = {"none": R["dark"], "blue": blue & inRing, "both": R["dark"] | (blue & inRing)}[colour]
        spindle = ringD <= R["rInner"] + 4
        out["innerEdge"] = R["rInner"]
    else:                                                       # no ring: as before (not washer-coloured, over)
        evRing = {"none": ~washer, "blue": blue, "both": ~washer | blue}[colour] & over
        spindle = d <= Rin + 5
    m = cv2.morphologyEx((evRing & legsHalf).astype(np.uint8), cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    n, lab, _, _ = cv2.connectedComponentsWithStats(m, 8)
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


def _zoom(img, g, scale, half):
    cx, cy = (int(round(v)) for v in g["cc"])
    x0, y0 = max(0, cx - half), max(0, cy - half)
    return cv2.resize(img[y0:cy + half, x0:cx + half], None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST), x0, y0


def _skeletonOn(big, skel, x0, y0, half, g, scale, colour):
    cx, cy = (int(round(v)) for v in g["cc"])
    ys, xs = np.nonzero(skel[y0:cy + half, x0:cx + half])
    for y, x in zip(ys, xs):
        cv2.rectangle(big, (x * scale, y * scale), (x * scale + scale - 1, y * scale + scale - 1), colour, -1)


def _text(big, lines, size=0.6):
    y = 22
    for t in lines:
        cv2.putText(big, t, (6, y), cv2.FONT_HERSHEY_SIMPLEX, size, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(big, t, (6, y), cv2.FONT_HERSHEY_SIMPLEX, size, (255, 255, 255), 1, cv2.LINE_AA)
        y += 22


def drawn(g: dict, res: dict, scale: int = 3, half: int = 75) -> np.ndarray:
    """2 x 2 grid (Uddi, 2026-11-21 12:01), each the washer enlarged:
      (1) the washer mask alone, light green on black;
      (2) the Otsu-binarised washer mask - light white, dark black, everything outside the ring black;
      (3) the legs' lines (skeleton, red) on that binarised picture;
      (4) the picture with the washer mask light green, the pin head and the legs' lines red, the lengths and the
          dark share / length written on it."""
    crop = g["crop"]
    h, w = crop.shape[:2]
    R = res.get("ring") or {}
    green = np.array([144, 238, 144], np.uint8)
    M = R["ringMask"] if R.get("found") else (g["M"].astype(bool) if g.get("M") is not None else np.zeros((h, w), bool))
    p1 = np.zeros_like(crop); p1[M] = green
    p2 = np.zeros_like(crop)
    if R.get("found"):
        p2[R["light"] & M] = (255, 255, 255)
    p3 = p2.copy()
    p4 = crop.copy(); p4[M] = (0.55 * p4[M] + 0.45 * green).astype(np.uint8)
    if res.get("eyeMask") is not None:
        p4[res["eyeMask"]] = (0, 0, 255)
    tiles = []
    for p in (p1, p2, p3, p4):
        big, x0, y0 = _zoom(p, g, scale, half)
        tiles.append(big)
    if res.get("legsSkeleton") is not None:
        for t in (2, 3):
            _skeletonOn(tiles[t], res["legsSkeleton"], x0, y0, half, g, scale, (0, 0, 255))
    _text(tiles[0], ([f"Otsu (original) {R['otsu']:.0f}"] if R.get("found") else []) + ["(1) washer mask"] + ([f"inner edge r {R['rInner']:.1f}, outline tips r {R['rTips']:.1f}"]
                                          if R.get("found") else ["ring not found: MobileSAM mask"])
                     + ([] if res.get("washerPresent") else ["washer: NOT found"]))
    _text(tiles[1], ["(2) Otsu inside the mask"] + ([f"Otsu {R['otsu']:.0f} {R['adjust']:+.0f} = {R['threshold']:.0f}, close {R['close']} + {R['close2']}"]
                                                    if R.get("found") else []))
    _text(tiles[2], ["(3) legs' lines"] + [f"leg {i + 1}: {L:.0f} px" for i, L in enumerate(res.get("legs", []))]
                     + [f"total: {res.get('total', 0.0):.0f} px"])
    lines = ["(4) result"] + [f"total legs: {res.get('total', 0.0):.0f} px"]
    if R.get("found"):
        lines += [f"dark share: {R['darkShare']:.2f} (legs half {R.get('darkShareLegs', 0):.2f})",
                  f"dark length: {R['darkLength']:.2f} (legs half {R.get('darkLengthLegs', 0):.2f})"]
    if not res.get("washerPresent"):
        lines.append("washer: NOT found")
    _text(tiles[3], lines)
    hh = max(t.shape[0] for t in tiles); ww = max(t.shape[1] for t in tiles)
    tiles = [cv2.copyMakeBorder(t, 0, hh - t.shape[0], 0, ww - t.shape[1], cv2.BORDER_CONSTANT, value=(40, 40, 40)) for t in tiles]
    sep = np.full((hh, 4, 3), 90, np.uint8)
    top = np.hstack([tiles[0], sep, tiles[1]]); bottom = np.hstack([tiles[2], sep, tiles[3]])
    return np.vstack([top, np.full((4, top.shape[1], 3), 90, np.uint8), bottom])


# the blue-marker / not-washer-colour method under its own name (CHANGES 192: the red marking is in _legs_red)
measureBlue = measure
