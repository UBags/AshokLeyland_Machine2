"""
_legs_profile - the exact washer profile and the split pin leg profile (Uddi, 2026-11-28 01:06 - 02:17; CHANGES 222),
for Study_Legs_Profile_UI. Study only: production (camera/CheckSplitPinAndWasher.py) is not touched.

Input: one picture's washer geometry from Study_Washer_SAM.analyse (the MobileSAM washer mask and its fitted outline
`fo` - 360 radii round the centre used `cc`, harmonics 1, 2, 12, 24) and the inner edge from _legs_length.ring (a
circle round the outline's centroid). All coordinates below are in the 240 x 240 crop of analyse().

1. Outer outline, scallop by scallop (refineOutline; Uddi 01:06 / 01:51):
   - the fitted outline split into its 12 scallops at its 12 corners (valleys: the 12-fold phase of the outline, then
     the smallest radius within 8 deg of each expected corner);
   - each whole scallop, corner to corner, moved along the outline's normal from -SHIFT to +SHIFT px in STEP px; at each
     shift the grey GAP px inside minus the grey GAP px outside along the moved curve; the shift with the most points
     at least CONTRAST_MIN brighter inside (+ the median contrast / 255) taken - the edge between the bright split pin
     washer and the darker plate washer under it;
   - a scallop is found when at least GOOD_SHARE of its points pass at that shift and at most OBSCURED_SHARE of its
     points lie on the rough legs (step 3); otherwise it is placed from the corners of the adjacent scallops: its shift
     runs linearly from the one corner's shift to the other's;
   - a corner's shift = the mean of the found scallops on either side (none found: from the nearest corners that have
     one); a found scallop's shift goes to its corners' shifts over BLEND deg, so the outline has no steps.
2. Pin head (findHead; Uddi 01:06: its angle to the horizontal is fixed by the hole drilled in the knuckle): the head
   lies on the axis HEAD_AXIS (deg, image angles, y down) at one of its two ends - 117.8 +- 0.8 / 295.5 +- 2.1 deg on the
   139 DOST pictures of 2026-11-24 with a pin. At each end, within HEAD_TOL deg: pixels HEAD_BAND px past the outline
   with V >= HEAD_V_MIN, S <= HEAD_S_MAX (heads look golden under warm light) and R - G < RG_PAINT (not paint); the end
   with the larger connected piece is the head, if that piece has at least HEAD_AREA_MIN px (no pin: 1 - 4 px; the
   smallest heads 23 - 36). No head -> no split pin; the legs are not looked for.
3. Rough legs (roughLegs, only to tell which scallops a leg hides): R - G (5 x 5 blur) in +-ROUGH_SECTOR deg round the
   legs axis (the head's end + 180), inner edge .. outline + ROUGH_REACH px, Otsu there (not below RG_PAINT), opened
   3 x 3, pieces >= ROUGH_AREA_MIN px.
4. Leg profile (findLegs; Uddi 01:51, changed 02:03 and 02:11), +-SECTOR deg round the point where the legs come out
   (the legs axis); each Otsu over the pixels of its own region only; R - G and R - B with negatives counted as 0:
   (a) on the washer (inner edge .. refined outer outline):
       grey -> Otsu, 1 = dark  AND  R - G -> Otsu, 1 = bright;
       OR  R - G above a second Otsu taken over the R - G values above the first (strong paint: pale legs, where the leg
       is not darker than the washer);
   (b) beyond it (outline + GAP_B .. outline + OUT px):
       max(R - B, 0) -> Otsu, 1 = bright  AND  R - G -> Otsu (not below RG_FLOOR_B), 1 = bright;
       only pieces reaching within TOUCH_B px of region (b)'s inner edge are kept (a leg outside comes out of the
       washer; the white highlight where a leg crosses the washer's edge can break its first px).
   Since 02:51 (Uddi): (a)'s grey Otsu x A_GREY_FACTOR (0.8) and R-G Otsu x A_RG_FACTOR (1.2), the strong R-G Otsu over
   the values above the adjusted one; then inside each area the grey refinement (refineInGrey): grey, relative gamma
   round the area's mean (above: GAMMA_HIGH 1.5, brighter, <= 255; below: GAMMA_LOW 0.6, darker, >= 0), Otsu of the
   result - (a) x A_REFINE_FACTOR (0.8), the dark pixels; (b) x B_REFINE_FACTOR (1.2), the light pixels.
   The leg profile = (a) OR (b).
Measured on the 150 DOST pictures of 2026-11-24 (38 OK / 112 NOT OK, permanent marker; sandbox: dark-blob hole finder,
MobileSAM on the CPU): see CHANGES 222.
"""
import math

import cv2
import numpy as np

NB = 360
T = np.deg2rad(np.arange(NB) + 0.5)

# 1. outer outline
SHIFT = 15.0               # px either way along the normal
STEP = 0.5                 # px between the shifts tried
GAP = 2.0                  # px inside / outside the curve where the grey is read
CONTRAST_MIN = 25          # grey levels: a point is "bright inside, dark outside" from this
GOOD_SHARE = 0.75          # share of a scallop's points that must pass at its best shift
OBSCURED_SHARE = 0.25      # a scallop with more of its points than this on the rough legs is placed from its neighbours
BLEND = 6                  # deg from each corner over which a found scallop's shift goes to the corner's shift
CORNER_SEARCH = 8          # deg either side of an expected corner where the smallest radius is taken
# 2. pin head
HEAD_AXIS = 116.7          # deg (image angles, y down); the other end is + 180
HEAD_TOL = 12              # deg either side of each end
HEAD_BAND = (4, 30)        # px past the outline
HEAD_V_MIN = 120
HEAD_S_MAX = 100
HEAD_AREA_MIN = 15         # px
RG_PAINT = 10              # R - G from which a pixel counts as paint
# 3. rough legs
ROUGH_SECTOR = 80          # deg either side of the legs axis
ROUGH_REACH = 45           # px past the outline
ROUGH_AREA_MIN = 25        # px
# 4. leg profile
SECTOR = 60                # deg either side of the legs axis
GAP_B = 3                  # px past the outline where region (b) starts
OUT = 30                   # px past the outline where region (b) ends
RG_FLOOR_B = 8             # region (b): the R - G threshold is never below this
TOUCH_B = 8                # px: a region (b) piece must reach within this of region (b)'s inner edge
A_GREY_FACTOR = 0.8        # (a) the grey Otsu value x this (towards zero by 20 %; Uddi 02:51)
A_RG_FACTOR = 1.2          # (a) the R-G Otsu value x this (away from zero by 20 %)
GAMMA_HIGH = 1.5           # grey refinement: above the mask's mean, mean * (v / mean) ** GAMMA_HIGH (brighter, <= 255)
GAMMA_LOW = 0.6            # below the mean, mean * (v / mean) ** (1 / GAMMA_LOW) (darker, >= 0)
A_REFINE_FACTOR = 0.8      # (a) refinement: Otsu x this, the dark pixels are the legs
B_REFINE_FACTOR = 1.2      # (b) refinement: Otsu x this, the light pixels are the legs
REFINE_MIN_PX = 10         # a mask with fewer pixels is not refined


def _bilinear(img, x, y):
    return cv2.remap(img, x.astype(np.float32).reshape(1, -1), y.astype(np.float32).reshape(1, -1),
                     cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE).ravel()


def _otsu(vals) -> float:
    v = np.clip(np.asarray(vals), 0, 255).astype(np.uint8).reshape(-1, 1)
    if v.size == 0:
        return 255.0
    t, _ = cv2.threshold(v, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return float(t)


def _polar(shape, cc):
    h, w = shape[:2]
    yy, xx = np.mgrid[:h, :w]
    d = np.hypot(xx - cc[0], yy - cc[1])
    a = np.degrees(np.arctan2(yy - cc[1], xx - cc[0])) % 360
    return yy, xx, d, a


def _rel(a, axis):
    return np.abs(((a - axis) + 180) % 360 - 180)


def corners(fo) -> list:
    """The 12 corners (valleys) of the fitted outline, as degree indices."""
    f = np.where(np.isfinite(fo), fo, np.nanmedian(fo))
    fs = np.convolve(np.r_[f[-5:], f, f[:5]], np.ones(5) / 5, "same")[5:-5]
    ph = math.atan2(np.sum(fs * np.sin(12 * T)), np.sum(fs * np.cos(12 * T)))
    v = []
    for k in range(12):
        a0 = (math.degrees((ph + math.pi) / 12) + 30 * k) % 360
        idx = [(int(a0) - CORNER_SEARCH + j) % NB for j in range(2 * CORNER_SEARCH + 1)]
        v.append(idx[int(np.argmin(fs[idx]))])
    return sorted(set(v))


def findHead(crop, cc, fr) -> dict:
    """The pin head at one end of the fixed axis; dict(found, end, area, mask)."""
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV).astype(int)
    bl = cv2.GaussianBlur(crop, (5, 5), 0).astype(int)
    rg = bl[..., 2] - bl[..., 1]
    _, _, d, a = _polar(crop.shape, cc)
    FR = fr[np.clip(a.astype(int), 0, NB - 1)]
    cand = ((d >= FR + HEAD_BAND[0]) & (d <= FR + HEAD_BAND[1]) & (hsv[..., 2] >= HEAD_V_MIN) & (hsv[..., 1] <= HEAD_S_MAX)
            & (rg < RG_PAINT))
    best = (HEAD_AXIS, 0, None)
    for end in (HEAD_AXIS % 360, (HEAD_AXIS + 180) % 360):
        n, lab, st, _ = cv2.connectedComponentsWithStats((cand & (_rel(a, end) <= HEAD_TOL)).astype(np.uint8), 8)
        if n > 1:
            k = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
            if st[k, cv2.CC_STAT_AREA] > best[1]:
                best = (end, int(st[k, cv2.CC_STAT_AREA]), lab == k)
    return dict(found=best[1] >= HEAD_AREA_MIN, end=best[0], area=best[1], mask=best[2])


def roughLegs(crop, cc, fr, rInner, centroid, legsAxis) -> np.ndarray:
    bl = cv2.GaussianBlur(crop, (5, 5), 0).astype(float)
    rg = bl[..., 2] - bl[..., 1]
    yy, xx, d, a = _polar(crop.shape, cc)
    FR = fr[np.clip(a.astype(int), 0, NB - 1)]
    din = np.hypot(xx - centroid[0], yy - centroid[1])
    roi = (_rel(a, legsAxis) <= ROUGH_SECTOR) & (din >= rInner) & (d <= FR + ROUGH_REACH)
    thr = max(_otsu(rg[roi]), RG_PAINT)
    m = cv2.morphologyEx((roi & (rg > thr)).astype(np.uint8), cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
    keep = np.zeros(m.shape, bool)
    for k in range(1, n):
        if st[k, cv2.CC_STAT_AREA] >= ROUGH_AREA_MIN:
            keep |= lab == k
    return keep


def refineOutline(crop, cc, fo, legsRough=None) -> dict:
    """Step 1. dict(pts (360 x 2, crop coordinates), fr (radii round cc), segs, corners, cornerShift, found (per deg))."""
    grey = cv2.GaussianBlur(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), (3, 3), 0).astype(np.float32)
    cx, cy = cc
    f = np.where(np.isfinite(fo), fo, np.nanmedian(fo))
    xs, ys = cx + f * np.cos(T), cy + f * np.sin(T)
    dx = np.gradient(np.r_[xs[-1], xs, xs[0]])[1:-1]
    dy = np.gradient(np.r_[ys[-1], ys, ys[0]])[1:-1]
    L = np.hypot(dx, dy) + 1e-9
    nx, ny = dy / L, -dx / L
    s_ = np.sign(nx * (xs - cx) + ny * (ys - cy))
    nx, ny = nx * s_, ny * s_
    onLegs = np.zeros(NB, bool)
    if legsRough is not None:
        lr = cv2.dilate(legsRough.astype(np.uint8), np.ones((5, 5), np.uint8))
        h, w = lr.shape
        onLegs = lr[np.clip(np.round(ys).astype(int), 0, h - 1), np.clip(np.round(xs).astype(int), 0, w - 1)] > 0
    V = corners(fo)
    n = len(V)
    shifts = np.arange(-SHIFT, SHIFT + STEP / 2, STEP)
    segs = []
    for i in range(n):
        a, b = V[i], V[(i + 1) % n]
        idx = np.arange(a, b if b > a else b + NB) % NB
        best = None
        for s in shifts:
            px, py = xs[idx] + s * nx[idx], ys[idx] + s * ny[idx]
            c = (_bilinear(grey, px - GAP * nx[idx], py - GAP * ny[idx])
                 - _bilinear(grey, px + GAP * nx[idx], py + GAP * ny[idx]))
            share = float(np.mean(c >= CONTRAST_MIN))
            score = share + float(np.median(c)) / 255.0
            if best is None or score > best[0]:
                best = (score, float(s), share, float(np.median(c)))
        obsc = float(onLegs[idx].mean())
        segs.append(dict(idx=idx, shift=best[1], share=best[2], contrast=best[3], obscured=obsc,
                         found=best[2] >= GOOD_SHARE and obsc <= OBSCURED_SHARE))
    cs = np.full(n, np.nan)
    for i in range(n):                                   # corner i = start of scallop i = end of scallop i - 1
        v = [segs[j % n]["shift"] for j in (i - 1, i) if segs[j % n]["found"]]
        if v:
            cs[i] = float(np.mean(v))
    if np.isnan(cs).all():
        cs[:] = 0.0
    filled = cs.copy()
    for i in range(n):
        if np.isnan(cs[i]):
            for k in range(1, n):
                l, r = cs[(i - k) % n], cs[(i + k) % n]
                if not (np.isnan(l) and np.isnan(r)):
                    filled[i] = float(np.nanmean([l, r]))
                    break
    cs = filled
    per = np.zeros(NB)
    found = np.zeros(NB, bool)
    for i, sg in enumerate(segs):
        idx = sg["idx"]
        m = len(idx)
        c0, c1 = cs[i], cs[(i + 1) % n]
        if sg["found"]:
            k = max(min(BLEND, m // 2), 1)
            sh = np.full(m, sg["shift"])
            sh = c0 + (sh - c0) * np.clip(np.arange(m) / k, 0, 1)
            sh = c1 + (sh - c1) * np.clip((m - 1 - np.arange(m)) / k, 0, 1)
        else:
            sh = c0 + (c1 - c0) * (np.arange(m) / max(m - 1, 1))
        per[idx] = sh
        found[idx] = sg["found"]
    pts = np.column_stack([xs + per * nx, ys + per * ny])
    # radii round cc per whole degree (for the region masks)
    ang = (np.degrees(np.arctan2(pts[:, 1] - cy, pts[:, 0] - cx)) % 360)
    rad = np.hypot(pts[:, 0] - cx, pts[:, 1] - cy)
    order = np.argsort(ang)
    fr = np.interp(np.arange(NB) + 0.5, ang[order], rad[order], period=360)
    return dict(pts=pts, fr=fr, segs=segs, corners=V, cornerShift=cs, found=found)


def relativeGamma(vals: np.ndarray, mean: float) -> np.ndarray:
    """Uddi 02:51: above the mean brighter (gamma GAMMA_HIGH, clamped to 255), below it darker (gamma GAMMA_LOW,
    clamped to 0), both relative to the mean: mean * (v / mean) ** GAMMA_HIGH above, mean * (v / mean) ** (1 / GAMMA_LOW)
    below (read so that 0.6 darkens: mean * (v / mean) ** 0.6 would move those pixels up towards the mean)."""
    m = max(float(mean), 1.0)
    r = np.asarray(vals, float) / m
    out = np.where(r >= 1, m * r ** GAMMA_HIGH, m * r ** (1.0 / GAMMA_LOW))
    return np.clip(out, 0, 255)


def refineInGrey(grey: np.ndarray, area: np.ndarray, factor: float, dark: bool):
    """Steps (iv) / (v): inside the leg area, grey -> relative gamma round the area's mean -> Otsu x factor; the dark
    (iv) or light (v) pixels are the legs. (legs mask, threshold or None when the area is too small to refine)."""
    if int(area.sum()) < REFINE_MIN_PX:
        return area.copy(), None
    vals = grey[area]
    enh = relativeGamma(vals, float(vals.mean()))
    t = _otsu(enh) * factor
    keep = (enh <= t) if dark else (enh > t)
    out = np.zeros(area.shape, bool)
    out[area] = keep
    return out, t


def findLegs(crop, cc, fr, rInner, centroid, legsAxis) -> dict:
    """Step 4. Every mask is returned for the window's mask views."""
    B, G, R = (crop[..., k].astype(float) for k in range(3))
    grey = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).astype(float)
    rg = np.clip(R - G, 0, 255)
    rb = np.clip(R - B, 0, 255)
    yy, xx, d, a = _polar(crop.shape, cc)
    FR = fr[np.clip(a.astype(int), 0, NB - 1)]
    sector = _rel(a, legsAxis) <= SECTOR
    din = np.hypot(xx - centroid[0], yy - centroid[1])
    regA = sector & (din >= rInner) & (d <= FR)
    regB = sector & (d > FR + GAP_B) & (d <= FR + OUT)
    tAgreyOtsu, tArgOtsu = _otsu(grey[regA]), _otsu(rg[regA])
    tAgrey, tArg = tAgreyOtsu * A_GREY_FACTOR, tArgOtsu * A_RG_FACTOR
    aDark, aRG = regA & (grey <= tAgrey), regA & (rg > tArg)
    upper = rg[regA & (rg > tArg)]
    tAstrong = _otsu(upper) if upper.size >= 10 else 255.0
    aStrong = regA & (rg > tAstrong)
    areaA = (aDark & aRG) | aStrong
    legsA, tArefine = refineInGrey(grey, areaA, A_REFINE_FACTOR, dark=True)
    tBrb, tBrgOtsu = _otsu(rb[regB]), _otsu(rg[regB])
    tBrg = max(tBrgOtsu, RG_FLOOR_B)
    bRB, bRG = regB & (rb > tBrb), regB & (rg > tBrg)
    raw = bRB & bRG
    n, lab = cv2.connectedComponents(raw.astype(np.uint8), connectivity=8)
    start = regB & (d <= FR + GAP_B + TOUCH_B)
    areaB = np.zeros(raw.shape, bool)
    for k in range(1, n):
        comp = lab == k
        if (comp & start).any():
            areaB |= comp
    legsB, tBrefine = refineInGrey(grey, areaB, B_REFINE_FACTOR, dark=False)
    return dict(axis=legsAxis, regA=regA, regB=regB, aDark=aDark, aRG=aRG, aStrong=aStrong, bRB=bRB, bRG=bRG, bRaw=raw,
                areaA=areaA, areaB=areaB, legsA=legsA, legsB=legsB, legs=legsA | legsB,
                tA_grey=tAgrey, tA_rg=tArg, tA_grey_otsu=tAgreyOtsu, tA_rg_otsu=tArgOtsu, tA_refine=tArefine, tB_refine=tBrefine, tA_strong=tAstrong, tB_rb=tBrb, tB_rg=tBrg, tB_rg_otsu=tBrgOtsu,
                pxA=int(legsA.sum()), pxB=int(legsB.sum()))


def profile(crop, cc, fo, rInner, centroid) -> dict:
    """Steps 1 - 4 on one picture: dict(outline, head, legs (None without a head))."""
    head0 = findHead(crop, cc, fo)
    rough = roughLegs(crop, cc, fo, rInner, centroid, (head0["end"] + 180) % 360) if head0["found"] else None
    outline = refineOutline(crop, cc, fo, rough)
    head = findHead(crop, cc, outline["fr"])
    legs = findLegs(crop, cc, outline["fr"], rInner, centroid, (head["end"] + 180) % 360) if head["found"] else None
    return dict(outline=outline, head=head, legs=legs, rough=rough)


def summaryLine(res: dict) -> str:
    o, h, L = res["outline"], res["head"], res["legs"]
    placed = sum(not s["found"] for s in o["segs"])
    shifts = " ".join(f"{s['shift']:+.1f}{'' if s['found'] else '*'}" for s in o["segs"])
    line = f"scallops found {len(o['segs']) - placed} placed {placed} (shifts {shifts}; * = placed)"
    line += f"; head {'at %.1f deg, %d px' % (h['end'], h['area']) if h['found'] else 'not found (%d px)' % h['area']}"
    if L is not None:
        f = lambda v: "-" if v is None else f"{v:.0f}"
        line += (f"; legs on washer {L['pxA']} of {int(L['areaA'].sum())} px (Otsu grey {L['tA_grey_otsu']:.0f} -> {L['tA_grey']:.0f},"
                 f" R-G {L['tA_rg_otsu']:.0f} -> {L['tA_rg']:.0f}, strong {L['tA_strong']:.0f}, refine {f(L['tA_refine'])}),"
                 f" outside {L['pxB']} of {int(L['areaB'].sum())} px (Otsu R-B {L['tB_rb']:.0f}, R-G {L['tB_rg_otsu']:.0f} -> {L['tB_rg']:.0f},"
                 f" refine {f(L['tB_refine'])})")
    return line
