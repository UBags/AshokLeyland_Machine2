"""
_legs_filtered - the split pin legs on the washer by an edge-preserving filter, Otsu and morphology (Uddi, 2026-11-21
18:26; CHANGES 196), for Study_Red_Legs_UI (pipeline "bilateral" or "guided"):
  1. the washer mask (ring) and the legs side as _legs_red.measureRed finds them;
  2. the picture to grey; the filter once on the whole washer mask, using only the grey values inside it (CHANGES 199):
       bilateral - utils.ImageNormalisationWithMask.bilateral_filter_masked (the outside filled with the inside mean,
                   cv2.bilateralFilter, the result kept inside the mask), d = -1, sigmaColor 50, sigmaSpace 15;
       guided    - a guided filter (He, Sun, Tang) with the grey picture as its own guide, the box means weighted by
                   the half's mask so nothing outside it is used. cv2.ximgproc.guidedFilter is the same filter but is
                   in opencv-contrib only, not in the opencv-python 4.11 the project uses (requirements.txt) - so it is
                   written out here with cv2.boxFilter (radius GUIDED_RADIUS, eps GUIDED_EPS on the 0 - 255 scale);
  3. Otsu + OTSU_ADJUST on each half separately (legs half, pin head half): light = washer (white), dark = legs (black);
  4. two openings of that binary picture with circular kernels (OPEN_1, OPEN_2; 4, 4);
  5. two closings with circular kernels (CLOSE_1, CLOSE_2; 4, 4), inside the ring (CHANGES 198: this order);
  6. the values from that picture as in the red method: black share on the legs half, black length across the whole
     ring (and on the legs half). The reach past the outline stays the red one (the filter works inside the ring only).
"""
import cv2
import numpy as np

import _legs_red as LR

BILATERAL_D = -1
SIGMA_COLOR = 100.0                 # the window's text boxes (CHANGES 197); both filters use them
SIGMA_SPACE = 20.0
# the guided filter has two parameters, a radius and eps (the variance below which it smooths); from the bilateral's:
# radius = round(1.5 x sigmaSpace) (the window cv2.bilateralFilter uses with d = -1), eps = sigmaColor^2
GUIDED_RADIUS = None                # None: from SIGMA_SPACE
GUIDED_EPS = None                   # None: from SIGMA_COLOR
OTSU_ADJUST = 0                     # added to each half's Otsu value (the window's "Otsu +" dropdown; CHANGES 198)
OPEN_1, OPEN_2 = 4, 4               # px, circular kernels of the two openings
CLOSE_1, CLOSE_2 = 4, 4             # px, circular kernels of the two closings after Otsu
COLUMN_SHARE = 0.25                 # a degree of the flat ring counts as black when this share of its pixels is black


def _disc(k: int):
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (max(1, int(k)), max(1, int(k))))


def bilateralInMask(grey: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """The project's bilateral_filter_masked on one half of the ring."""
    from utils.ImageNormalisationWithMask import bilateral_filter_masked
    return bilateral_filter_masked(grey, mask, d=BILATERAL_D, sigmaColor=float(SIGMA_COLOR), sigmaSpace=float(SIGMA_SPACE))


def guidedInMask(grey: np.ndarray, mask: np.ndarray, radius: int = None, eps: float = None) -> np.ndarray:
    """Guided filter, the grey picture its own guide, only the pixels of mask used (mask-weighted box means)."""
    r = int(radius if radius is not None else (GUIDED_RADIUS if GUIDED_RADIUS is not None else round(1.5 * float(SIGMA_SPACE))))
    e = float(eps if eps is not None else (GUIDED_EPS if GUIDED_EPS is not None else float(SIGMA_COLOR) ** 2))
    I = grey.astype(np.float64)
    m = mask.astype(np.float64)
    k = (2 * r + 1, 2 * r + 1)
    box = lambda x: cv2.boxFilter(x, -1, k, normalize=False, borderType=cv2.BORDER_CONSTANT)
    n = np.maximum(box(m), 1e-6)
    meanI = box(I * m) / n
    corrII = box(I * I * m) / n
    varI = np.maximum(corrII - meanI * meanI, 0.0)
    a = varI / (varI + e)                       # guide = input: cov(I, p) = var(I)
    b = meanI - a * meanI
    meanA = box(a * m) / n
    meanB = box(b * m) / n
    q = meanA * I + meanB
    out = grey.copy()
    out[mask] = np.clip(q[mask], 0, 255).astype(np.uint8)
    return out


def measureFiltered(g: dict, row: dict, model: str, method: str = "bilateral") -> dict:
    """Steps 1 - 6. Returns measureRed's dict with black / blackShareLegs / blackShare / blackLength / blackLengthLegs /
    flat taken from the filtered picture (the red ones kept as red_*), and grey, filtered, opened, binary."""
    res = LR.measureRed(g, row, model)
    R = res.get("ring") or {}
    if not R.get("found") or res.get("axis") is None:
        return res
    for k in ("black", "blackShareLegs", "blackShare", "blackLength", "blackLengthLegs"):
        res["red_" + k] = res.get(k)
    crop = g["crop"]
    h, w = crop.shape[:2]
    grey = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    ring = R["ringMask"]
    cx, cy = R["centroid"]
    yy, xx = np.mgrid[:h, :w]
    ang = np.degrees(np.arctan2(yy - cy, xx - cx)) % 360
    rel = np.abs(((ang - res["axis"]) + 180) % 360 - 180)
    legsHalf = ring & (rel <= 90)
    headHalf = ring & (rel > 90)
    # 2. the filter once on the whole washer mask, using only the grey values inside it (CHANGES 199)
    f = guidedInMask(grey, ring) if method == "guided" else bilateralInMask(grey, ring)
    # 3. Otsu + OTSU_ADJUST on each half separately (CHANGES 198): light = washer, dark = legs
    light = np.zeros((h, w), bool)
    otsus = []
    for half in (legsHalf, headHalf):
        if half.sum() < 10:
            otsus.append(None)
            continue
        t_, _ = cv2.threshold(f[half].reshape(-1, 1), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        otsus.append(float(t_))
        light |= (f > float(t_) + float(OTSU_ADJUST)) & half
    t = otsus[0] if otsus[0] is not None else (otsus[1] or 0.0)
    # 4. two openings of the binary picture (circular kernels), inside the ring
    op = cv2.morphologyEx(light.astype(np.uint8), cv2.MORPH_OPEN, _disc(OPEN_1))
    op = cv2.morphologyEx(op, cv2.MORPH_OPEN, _disc(OPEN_2))
    # 5. two closings (circular kernels), inside the ring
    light = cv2.morphologyEx(op, cv2.MORPH_CLOSE, _disc(CLOSE_1))
    light = cv2.morphologyEx(light, cv2.MORPH_CLOSE, _disc(CLOSE_2)).astype(bool) & ring
    op = f                                                  # tile (2) shows the filtered grey
    black = ring & ~light
    # 6. the values
    res.update(black=black, grey=grey, filtered=f, opened=op, binaryLight=light, filterOtsu=float(t), filterOtsus=otsus,
               filterMethod=method)
    res["blackShareLegs"] = float((black & legsHalf).sum() / max(int(legsHalf.sum()), 1))
    res["blackShare"] = float(black.sum() / max(int(ring.sum()), 1))
    start = (res["axis"] + 180) % 360
    col = ((ang - start) % 360).astype(int)
    nRing = np.bincount(col[ring], minlength=360).astype(float)
    nBlack = np.bincount(col[black], minlength=360).astype(float)
    blackCol = (nRing > 0) & (nBlack >= COLUMN_SHARE * np.maximum(nRing, 1))
    res["blackLength"] = float(blackCol.sum() / 360.0)
    res["blackLengthLegs"] = float(blackCol[90:270].sum() / 180.0)
    res["flat"] = blackCol
    return res


def drawnFiltered(g: dict, res: dict, scale: int = 3, half: int = 75) -> np.ndarray:
    """2 x 2 grid: (1) the washer mask; (2) the filtered grey ring (outside black); (3) the binary after Otsu and the
    closings - legs black, washer white; (4) the picture with the washer mask light green, the black on the legs half
    red, the values."""
    import _legs_length as L
    crop = g["crop"]
    h, w = crop.shape[:2]
    R = res.get("ring") or {}
    green = np.array([144, 238, 144], np.uint8)
    M = R["ringMask"] if R.get("found") else np.zeros((h, w), bool)
    p1 = np.zeros_like(crop); p1[M] = green
    p2 = np.zeros_like(crop)
    if res.get("opened") is not None:
        p2[M] = np.repeat(res["opened"][M][:, None], 3, axis=1)
    p3 = np.zeros_like(crop)
    if res.get("binaryLight") is not None:
        p3[res["binaryLight"]] = (255, 255, 255)
    p4 = crop.copy(); p4[M] = (0.55 * p4[M] + 0.45 * green).astype(np.uint8)
    if res.get("black") is not None:
        p4[res["black"]] = (0, 0, 255)
    tiles = [L._zoom(p, g, scale, half)[0] for p in (p1, p2, p3, p4)]
    L._text(tiles[0], ["(1) washer mask"] + ([f"inner r {R['rInner']:.1f}" + (f" (refined from {R['rInnerJump']:.1f})" if R.get("refined") else "")]
                                          if R.get("found") else []) + ([] if res.get("washerPresent") else ["washer: NOT found"]))
    L._text(tiles[1], [f"(2) {res.get('filterMethod', '?')} on the mask, sC {SIGMA_COLOR:g} sS {SIGMA_SPACE:g}"])
    os_ = res.get("filterOtsus") or [None, None]
    L._text(tiles[2], ["(3) Otsu per half " + " / ".join("-" if v is None else f"{v:.0f}" for v in os_) + f" {OTSU_ADJUST:+d}",
                       f"open {OPEN_1} + {OPEN_2}, close {CLOSE_1} + {CLOSE_2}"])
    lines = ["(4) result"]
    if res.get("blackShareLegs") is not None:
        lines += [f"black share, legs half: {res['blackShareLegs']:.3f}",
                  f"black length, whole: {res['blackLength']:.3f}",
                  f"reach past outline ({res.get('reachKind', 'red')}): {res.get('reach', 0):.0f} px"]
    L._text(tiles[3], lines)
    hh = max(t.shape[0] for t in tiles); ww = max(t.shape[1] for t in tiles)
    tiles = [cv2.copyMakeBorder(t, 0, hh - t.shape[0], 0, ww - t.shape[1], cv2.BORDER_CONSTANT, value=(40, 40, 40)) for t in tiles]
    sep = np.full((hh, 4, 3), 90, np.uint8)
    top = np.hstack([tiles[0], sep, tiles[1]]); bottom = np.hstack([tiles[2], sep, tiles[3]])
    return np.vstack([top, np.full((4, top.shape[1], 3), 90, np.uint8), bottom])
