"""
Study_SplitPin_Steps_UI - the split pin legs step by step, with the steps' values and choice boxes to do or bypass a
step (Uddi, 2026-11-28 12:10; CHANGES 229). Study only: production and the properties file are not touched.

Screen (CHANGES 230): top 25 % the values and choice boxes; bottom 75 % split into the text (left 25 %) and the four
pictures (right 75 %).
  top left      the original picture round the washer (+- VIEW_HALF px round its centre, x3: the whole split pin in view);
  top right     the steps over the split pin washer (region A);
  bottom left   the steps over the plate washer / seal beyond it (region B);
  bottom right  the result: the red masks of A and B on the whole picture ("Final zoomed" shows the washer only).

Load (or a new picture) runs, without stopping, everything up to the two 150 deg regions (120 before CHANGES 238): the hub's holes and the
MobileSAM washer outline (Study_Washer_SAM), the inner edge (_legs_length.ring), the outer outline scallop by scallop
and the pin head on its fixed axis (_legs_profile, CHANGES 222); the legs axis = the head's end + 180 deg.
  region A: +- sector deg round the legs axis, inner edge .. outer outline (on the split pin washer);
  region B: +- sector deg, outline + from px .. outline + to px (on the plate washer / seal).
Since CHANGES 241: region B starts with a morphological open of the picture inside the region only ("Open first",
kernel 5 x 5, both changeable; the erosion and the dilation use only the region's pixels) - so B's steps are region,
open, then 1 - 7 below; regions A and B follow the drawn scallop outline itself (A inside its polygon, B outside it from
"px past outline" 0 = on the outline, to 30 px); Next Step / Back pass over the steps of a mask whose "in the AND" box
is unticked (channel, bilateral and Otsu of that mask) and over the open when it is off.
Then "Next Step" goes through, for A and then for B:
  0  the region (light fill on the original);
  1  channel 1 as a grey picture inside the region (its source chosen in the box; A: grey, dark = mask);
  2  optional bilateral filter (check box; d, sigmaColor, sigmaSpace; utils/ImageNormalisationWithMask.
     bilateral_filter_masked - only the region's own pixels);
  3  Otsu over the region x the factor (%, 50 - 300 since CHANGES 242; was 150) -> mask 1 (blue); "Absolute" > 0
     (CHANGES 242) = that fixed threshold instead, Otsu, factor and minimum not used; since CHANGES 233 a raw Otsu value below the
     channel's "Otsu min" (0 = off; default 14 for region B's channel 2, else 0) leaves the mask empty;
  4  channel 2 as a grey picture (A: clamped 2R - G - B);
  5  optional bilateral filter;
  6  Otsu x the factor -> mask 2 (green), shown over mask 1 (blue) on the original;
  7  mask 3 = mask 1 AND mask 2 (each "in the AND" box can leave one out), painted red on the original;
and last the result - since CHANGES 232 first the bridges ("Bridge leg ends"), added pixels outlined magenta in "Final
zoomed": "closest points" (default) - for each A piece and B piece whose closest pixels are at most 19 px apart, a 5 px
wide bar between those pixels; "leg direction" - from each A piece's outer end along the direction of its last 12 px, a
19 x 5 rectangle kept only up to the first B pixel it meets, and likewise from each B piece's inner end towards A. Either
way nothing is drawn where no piece of the other region is within reach. Since CHANGES 231 the A and B masks joined first: a white copy of the washer area, the masks
black, an open with a 5 x 5 kernel (on white a closing of the black: masks broken at the scallops' edge join), the black
= the red mask shown ("Join the masks" box, kernel spin box). "Back" goes one step back, "Run all" to the result, "Reset" to step 0. Changing a value or a box
redraws the step shown. The text box: each step's values (Otsu value, factor, threshold, pixels).
Channel sources: grey (dark = 1), grey (bright = 1), 2R - G - B (bright = 1), max(R - B, 0) (bright = 1), R (bright = 1),
R - G (bright = 1), 2B - G - R (bright = 1), B + G - 2R (bright = 1); all but grey and R clamped to 0 .. 255.
--marker blue (CHANGES 236, 237; fluorescent blue marker): in both regions Otsu 1 left out (its "in the AND" box
unticked, mask 3 = mask 2) and channel 2 = B + G - 2R (2B - G - R in 236); everything else as for red. Default --marker red.
Since CHANGES 240 "Mask 1 in the AND" starts unticked for both markers (mask 3 = mask 2; tick it to AND mask 1 in);
source B + 2G - 3R added.
Defaults: A - grey (dark), 2R - G - B; B - max(R - B, 0), 2R - G - B (the last choices of CHANGES 223 - 225); sector +- 75 (CHANGES 238; was +- 60)
deg, B from 1 to 30 px; since CHANGES 231 (Uddi 12:56), for A and B: bilateral 1 on (d 9, sigmaColor 75, sigmaSpace 50),
Otsu 1 100 %; bilateral 2 on (d 9, sigmaColor 75, sigmaSpace 75), Otsu 2 70 %.

Run from the project root (laptop: GPU and the YOLO weights):
    python test\\machine2_dryrun\\Study_SplitPin_Steps_UI.py [<folder>] [--model DOST] [--holes morphology] [--sam <pt>]
"""
import pathlib
import sys
import threading
import traceback

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import Study_Washer_SAM as S          # sets the paths, the vendor and cv2 / numpy                    # noqa: E402
S.STEPS["nut"] = S.STEPS["legs_study"] = False
import cv2                                                                                     # noqa: E402
import numpy as np                                                                             # noqa: E402
import _legs_length as LL                                                                      # noqa: E402
import _legs_profile as LP                                                                     # noqa: E402
from Study_Legs_Profile_UI import picturesIn                                                   # noqa: E402

try:
    from utils.ImageNormalisationWithMask import bilateral_filter_masked
except Exception:                                   # the same steps, if the project module cannot be imported
    def bilateral_filter_masked(image, mask, d=9, sigmaColor=75.0, sigmaSpace=75.0):
        g = image.copy()
        if mask.sum() > 0:
            g[~mask] = int(np.mean(image[mask]))
        f = cv2.bilateralFilter(g, d, sigmaColor, sigmaSpace)
        out = image.copy()
        out[mask] = f[mask]
        return out

SOURCES = ("grey (dark = 1)", "grey (bright = 1)", "2R - G - B (bright = 1)", "max(R - B, 0) (bright = 1)",
           "R (bright = 1)", "R - G (bright = 1)", "2B - G - R (bright = 1)",
           "B + G - 2R (bright = 1)", "B - R (bright = 1)", "2B - 2R (bright = 1)", "2B + G - 3R (bright = 1)",
           "B + 2G - 3R (bright = 1)")
# Steps per region (CHANGES 241: region B starts with the open; a step whose mask is out of the AND, or the open when
# off, is skipped by Next Step / Back)
STEPS = {"A": ("region", "chan1", "filt1", "otsu1", "chan2", "filt2", "otsu2", "mask3"),
         "B": ("region", "open", "chan1", "filt1", "otsu1", "chan2", "filt2", "otsu2", "mask3")}
STEP_TITLES = dict(region="region", open="open in the mask", chan1="channel 1", filt1="bilateral 1",
                   otsu1="Otsu 1 -> mask 1", chan2="channel 2", filt2="bilateral 2", otsu2="Otsu 2 -> mask 2",
                   mask3="mask 3 = mask 1 AND mask 2")
OPEN_KERNEL = 5                      # px: region B's first step, a morphological open inside the mask only (CHANGES 241)
STEP_NAMES = ("region", "channel 1", "bilateral 1", "Otsu 1 -> mask 1", "channel 2", "bilateral 2",
              "Otsu 2 -> mask 2", "mask 3 = mask 1 AND mask 2")
MASK1_COLOUR, MASK2_COLOUR, MASK3_COLOUR = (255, 90, 0), (0, 220, 0), (0, 0, 255)
REGION_COLOUR = (255, 255, 0)
SCALLOP_COLOUR, INNER_COLOUR = (139, 0, 0), (0, 255, 255)
# Uddi's values of 2026-11-28 12:56 (CHANGES 231), the same for regions A and B
DEFAULTS = dict(bil1=True, d1=9, sc1=75.0, ss1=50.0, pct1=100.0, bil2=True, d2=9, sc2=75.0, ss2=75.0, pct2=70.0)
# Otsu minimum (Uddi 16:27, CHANGES 233): a raw Otsu value below it -> the mask is empty (0 = off). Defaults from the
# 24 Nov pictures: only region B's channel 2 (2R - G - B) separates - OK 1 - 24 (median 4), NOT OK 4 - 50 (median 21)
OTSU_MIN = {"A": (0, 0), "B": (0, 14)}
# Marker colour (CHANGES 236, Uddi 19:02): red = the steps above; blue (fluorescent blue marker) = Otsu 1 left out of
# the AND (mask 3 = mask 2) and channel 2 = clamped B + G - 2R in both regions (CHANGES 237; 2B - G - R in 236). --marker blue|red, and the Marker box.
MARKER_PRESETS = {
    "red": dict(A=("grey (dark = 1)", "2R - G - B (bright = 1)"), B=("max(R - B, 0) (bright = 1)", "2R - G - B (bright = 1)"),
                and1=False),      # CHANGES 240: mask 1 out of the AND by default (was True)
    "blue": dict(A=("grey (dark = 1)", "B + G - 2R (bright = 1)"), B=("max(R - B, 0) (bright = 1)", "B + G - 2R (bright = 1)"),
                 and1=False),
}
BRIDGE_LENGTH, BRIDGE_WIDTH = 19, 5   # px: the directed bridge at a leg's end (CHANGES 232)
BRIDGE_END_PX = 12                    # px: the last part of a leg whose main axis gives the bridge's direction
BRIDGE_MIN_AREA = 10                  # px: smaller pieces get no bridge
SECTOR = 75                          # deg either side of the legs axis: 150 deg (Uddi 20:31, CHANGES 238; was 60 = 120 deg)
JOIN_KERNEL = 5                      # px: the open on the white copy with the masks black (CHANGES 231)
VIEW_HALF = 85                       # px round the washer centre in the three washer panels (head and straight legs in view)


def view(img, geo):
    cx, cy = (int(round(v)) for v in geo["cc"])
    h, w = img.shape[:2]
    x0, y0 = max(0, min(w - 2 * VIEW_HALF, cx - VIEW_HALF)), max(0, min(h - 2 * VIEW_HALF, cy - VIEW_HALF))
    return cv2.resize(img[y0:y0 + 2 * VIEW_HALF, x0:x0 + 2 * VIEW_HALF], None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)


def channel(crop: np.ndarray, source: str):
    """(uint8 grey picture, dark = 1?) for a source of SOURCES."""
    B, G, R = (crop[..., k].astype(float) for k in range(3))
    if source.startswith("grey"):
        return cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), "dark" in source
    if source.startswith("2R"):
        v = 2 * R - G - B
    elif source.startswith("2B - G - R"):            # CHANGES 236 (kept; not the preset since CHANGES 237)
        v = 2 * B - G - R
    elif source.startswith("B + G"):                 # fluorescent blue marker, seen cyan (CHANGES 237)
        v = B + G - 2 * R
    elif source.startswith("B - R"):                 # CHANGES 238: candidates compared with B + G - 2R
        v = B - R
    elif source.startswith("2B - 2R"):
        v = 2 * B - 2 * R
    elif source.startswith("2B + G"):
        v = 2 * B + G - 3 * R
    elif source.startswith("B + 2G"):                # CHANGES 240
        v = B + 2 * G - 3 * R
    elif source.startswith("max(R - B"):
        v = R - B
    elif source.startswith("R - G"):
        v = R - G
    else:
        v = R
    return np.clip(v, 0, 255).astype(np.uint8), False


def otsu(vals) -> float:
    v = np.asarray(vals, np.uint8).reshape(-1, 1)
    if v.size == 0:
        return 0.0
    t, _ = cv2.threshold(v, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return float(t)


def geometry(path, label, model, models):
    """Everything up to the two regions' axis: dict, or dict(note=...) when a step fails."""
    pred, FindHubHoles, SP = models
    r, g, im = S.processPicture(path, label, model, pred, FindHubHoles, SP)
    out = dict(name=pathlib.Path(path).name, label=label, im=im, note="")
    if g is None or g.get("fo") is None:
        out["note"] = r.get("fail") or "no washer outline"
        return out
    x0, y0 = g["x0"], g["y0"]
    crop = im[y0:y0 + 2 * S.CROP_HALF, x0:x0 + 2 * S.CROP_HALF].copy()
    g["crop"] = crop
    R = LL.ring(g, None)
    if not R.get("found"):
        out["note"] = f"inner edge not found ({R.get('reason')})"
        return out
    cc = tuple(g["cc"])
    head0 = LP.findHead(crop, cc, g["fo"])
    rough = LP.roughLegs(crop, cc, g["fo"], R["rInner"], R["centroid"], (head0["end"] + 180) % 360) if head0["found"] else None
    outline = LP.refineOutline(crop, cc, g["fo"], rough)
    head = LP.findHead(crop, cc, outline["fr"])
    out.update(crop=crop, x0=x0, y0=y0, cc=cc, rInner=R["rInner"], centroid=R["centroid"], outline=outline, head=head,
               axis=(head["end"] + 180) % 360)
    if not head["found"]:
        out["note"] = "no pin head (no split pin?) - the legs axis is taken opposite the end tried first"
    return out


def regions(geo, sector, fromPx, toPx):
    """(region A, region B). Since CHANGES 241 both follow the drawn scallop outline itself (the polygon of
    outline["pts"]): A = inside it, B = outside it, from fromPx to toPx px from it (fromPx 0 = on the outline)."""
    crop, cc = geo["crop"], geo["cc"]
    h, w = crop.shape[:2]
    yy, xx = np.mgrid[:h, :w]
    a = np.degrees(np.arctan2(yy - cc[1], xx - cc[0])) % 360
    rel = np.abs(((a - geo["axis"]) + 180) % 360 - 180)
    sec = rel <= sector
    inside = np.zeros((h, w), np.uint8)
    cv2.fillPoly(inside, [np.round(geo["outline"]["pts"]).astype(np.int32)], 1)
    dist = cv2.distanceTransform((1 - inside).astype(np.uint8), cv2.DIST_L2, 5)     # px from the outline, outside it
    din = np.hypot(xx - geo["centroid"][0], yy - geo["centroid"][1])
    out = inside == 0
    return sec & (din >= geo["rInner"]) & (inside == 1), sec & out & (dist > fromPx - 0.5) & (dist <= toPx)


def maskedOpen(crop, mask, k):
    """Morphological open (k x k) of each colour channel using only the mask's pixels: the erosion sees pixels outside
    the mask as 255 (they never lower a minimum), the dilation sees them as 0 (they never raise a maximum); outside
    the mask the picture is unchanged (CHANGES 241)."""
    if k < 2 or not mask.any():
        return crop.copy()
    kernel = np.ones((int(k), int(k)), np.uint8)
    out = crop.copy()
    for c in range(crop.shape[2]):
        ch = crop[..., c].copy()
        ch[~mask] = 255
        er = cv2.erode(ch, kernel)
        er[~mask] = 0
        di = cv2.dilate(er, kernel)
        out[..., c][mask] = di[mask]
    return out


def runRegion(crop, region, p):
    """Steps 1 - 7 on one region with the values p; every intermediate picture and value."""
    res = dict(region=region, px=int(region.sum()))
    if p.get("open"):                                  # region B's first step (CHANGES 241)
        crop = maskedOpen(crop, region, int(p.get("openK", OPEN_KERNEL)))
    res["opened"] = crop
    for k in (1, 2):
        g, dark = channel(crop, p[f"src{k}"])
        res[f"chan{k}"] = g
        if p[f"bil{k}"] and region.any():
            g = bilateral_filter_masked(g, region, int(p[f"d{k}"]), float(p[f"sc{k}"]), float(p[f"ss{k}"]))
        res[f"filt{k}"] = g
        t0 = otsu(g[region]) if region.any() else 0.0
        absolute = p.get(f"abs{k}", 0) > 0                 # CHANGES 242: a fixed threshold overrides Otsu, % and minimum
        t = float(p[f"abs{k}"]) if absolute else t0 * p[f"pct{k}"] / 100.0
        below = (not absolute) and p.get(f"min{k}", 0) > 0 and t0 < p[f"min{k}"]
        m = np.zeros_like(region) if below else region & ((g <= t) if dark else (g > t))
        res.update({f"otsu{k}": t0, f"thr{k}": t, f"mask{k}": m, f"dark{k}": dark, f"below{k}": below, f"abs{k}": absolute})
    use1, use2 = p["and1"], p["and2"]
    m3 = (res["mask1"] if use1 else region) & (res["mask2"] if use2 else region)
    if not use1 and not use2:
        m3 = np.zeros_like(region)
    res["mask3"] = m3
    return res


def radialPastOutline(geo):
    """Per pixel of the crop: its distance from the washer centre minus the refined outline's radius in its direction
    (> 0 beyond the washer)."""
    crop, cc = geo["crop"], geo["cc"]
    h, w = crop.shape[:2]
    yy, xx = np.mgrid[:h, :w]
    d = np.hypot(xx - cc[0], yy - cc[1])
    a = np.degrees(np.arctan2(yy - cc[1], xx - cc[0])) % 360
    return d - geo["outline"]["fr"][np.clip(a.astype(int), 0, 359)]


def bridges(fromMask: np.ndarray, toMask: np.ndarray, past: np.ndarray, outward: bool, length: int, width: int):
    """Uddi 14:42, built so that nothing is invented: for each piece of fromMask, its end towards the other region (the
    outermost pixels when outward, else the innermost), the direction of its last BRIDGE_END_PX px (main axis, pointing
    away from the rest of the piece); a length x width rectangle from that end along it, kept only up to the first pixel
    of toMask it meets - no toMask pixel in it, no bridge. (mask of the bridges, [(end x, y, angle deg, px)])."""
    out = np.zeros(fromMask.shape, bool)
    info = []
    n, lab = cv2.connectedComponents(fromMask.astype(np.uint8), connectivity=8)
    for k in range(1, n):
        ys, xs = np.nonzero(lab == k)
        if len(xs) < BRIDGE_MIN_AREA:
            continue
        r = past[ys, xs]
        i0 = int(np.argmax(r) if outward else np.argmin(r))
        ex, ey = float(xs[i0]), float(ys[i0])
        near = np.hypot(xs - ex, ys - ey) <= BRIDGE_END_PX
        px, py = xs[near].astype(float), ys[near].astype(float)
        if len(px) < 3:
            continue
        P = np.array([px.mean(), py.mean()])
        cov = np.cov(np.vstack([px - P[0], py - P[1]]))
        evals, evecs = np.linalg.eigh(cov)
        axis = evecs[:, int(np.argmax(evals))]
        away = np.array([ex, ey]) - np.array([xs.mean(), ys.mean()])     # from the rest of the piece to its end
        if np.dot(axis, away) < 0:
            axis = -axis
        start = np.array([ex, ey])
        centre = start + axis * length / 2.0
        ang = float(np.degrees(np.arctan2(axis[1], axis[0])))
        box = cv2.boxPoints(((float(centre[0]), float(centre[1])), (float(length), float(width)), ang))
        rect = np.zeros(fromMask.shape, np.uint8)
        cv2.fillPoly(rect, [np.round(box).astype(np.int32)], 1)
        rect = rect.astype(bool)
        hit = rect & toMask
        if not hit.any():
            continue
        hy, hx = np.nonzero(hit)
        tHit = float(((hx - start[0]) * axis[0] + (hy - start[1]) * axis[1]).min())
        ry, rx = np.nonzero(rect)
        t = (rx - start[0]) * axis[0] + (ry - start[1]) * axis[1]
        keep = t <= tHit + 1
        out[ry[keep], rx[keep]] = True
        info.append((ex, ey, ang % 360, int(keep.sum())))
    return out, info


def bridgesClosest(maskA: np.ndarray, maskB: np.ndarray, length: int, width: int):
    """CHANGES 232, the default: for each piece of maskA and each piece of maskB whose closest pixels are at most
    length px apart, a bar width px wide between those two pixels. Only real pieces are joined: nothing is drawn
    where no piece of the other region is within reach. (mask of the bars, [(ax, ay, bx, by, gap px)])."""
    out = np.zeros(maskA.shape, np.uint8)
    info = []
    nA, labA = cv2.connectedComponents(maskA.astype(np.uint8), connectivity=8)
    nB, labB = cv2.connectedComponents(maskB.astype(np.uint8), connectivity=8)
    piecesB = [np.column_stack(np.nonzero(labB == j)[::-1]).astype(float) for j in range(1, nB)]
    for i in range(1, nA):
        a = np.column_stack(np.nonzero(labA == i)[::-1]).astype(float)
        if len(a) < BRIDGE_MIN_AREA:
            continue
        for b in piecesB:
            if len(b) < BRIDGE_MIN_AREA:
                continue
            d = np.hypot(a[:, None, 0] - b[None, :, 0], a[:, None, 1] - b[None, :, 1])
            k = int(np.argmin(d))
            ia, ib = divmod(k, d.shape[1])
            gap = float(d[ia, ib])
            if gap > length or gap < 1.5:
                continue
            pa, pb = tuple(int(v) for v in a[ia]), tuple(int(v) for v in b[ib])
            cv2.line(out, pa, pb, 1, max(int(width), 1))
            info.append((pa[0], pa[1], pb[0], pb[1], gap))
    return out.astype(bool), info


def joinMasks(mask: np.ndarray, k: int) -> np.ndarray:
    """Uddi 12:56: a white copy of the washer area, the masks' pixels black, an open with a k x k kernel - on white that
    is a closing of the black, so masks broken at the scallops' edge join; the black is the final mask."""
    white = np.full(mask.shape, 255, np.uint8)
    white[mask] = 0
    opened = cv2.morphologyEx(white, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
    return opened == 0


def fill(img, mask, colour, alpha=0.45):
    if mask is None or not mask.any():
        return img
    over = img.copy()
    over[mask] = colour
    out = cv2.addWeighted(over, alpha, img, 1 - alpha, 0)
    for c in cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)[0]:
        cv2.polylines(out, [c], True, colour, 1)
    return out


def greyInRegion(crop, grey, region):
    """The grey picture inside the region, the original dimmed outside it."""
    out = (crop * 0.35).astype(np.uint8)
    out[region] = cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR)[region]
    return out


def outlines(img, geo):
    o = geo["outline"]
    cv2.polylines(img, [np.round(o["pts"]).astype(np.int32)], True, SCALLOP_COLOUR, 1, cv2.LINE_AA)
    cx, cy = geo["centroid"]
    cv2.circle(img, (int(round(cx)), int(round(cy))), int(round(geo["rInner"])), INNER_COLOUR, 1, cv2.LINE_AA)
    if geo["head"]["found"]:
        for c in cv2.findContours(geo["head"]["mask"].astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)[0]:
            cv2.polylines(img, [c], True, (255, 255, 255), 1)
    return img


def stepSkipped(tag, key, p):
    """True when Next Step / Back pass over this step (CHANGES 241)."""
    if key in ("chan1", "filt1", "otsu1"):
        return not p["and1"]
    if key in ("chan2", "filt2", "otsu2"):
        return not p["and2"]
    if key == "open":
        return not p.get("open")
    return False


def stepPicture(crop, res, key, p):
    if key == "region":
        return fill(crop, res["region"], REGION_COLOUR, 0.25)
    if key == "open":
        out = crop.copy()
        out[res["region"]] = res["opened"][res["region"]]
        return out
    if key in ("chan1", "filt1", "chan2", "filt2"):
        return greyInRegion(crop, res[key], res["region"])
    if key == "otsu1":
        return fill(crop, res["mask1"], MASK1_COLOUR)
    if key == "otsu2":
        base = fill(crop, res["mask1"], MASK1_COLOUR, 0.35) if p["and1"] else crop
        return fill(base, res["mask2"], MASK2_COLOUR, 0.35)
    return fill(crop, res["mask3"], MASK3_COLOUR, 0.55)


def stepLines(tag, res, upto, p):
    """The text of the region's steps up to (and with) index upto of STEPS[tag]; skipped steps say so."""
    if upto < 0:
        return []
    lines = []
    for i, key in enumerate(STEPS[tag][:upto + 1]):
        head = f"{tag}{i} {STEP_TITLES[key]}"
        if stepSkipped(tag, key, p):
            lines.append(f"{head}: skipped" + (" (mask %s not in the AND)" % key[-1] if key != "open" else " (open off)"))
        elif key == "region":
            lines.append(f"{head}: {res['px']} px")
        elif key == "open":
            lines.append(f"{head}: {int(p.get('openK', OPEN_KERNEL))} x {int(p.get('openK', OPEN_KERNEL))}, only the region's pixels")
        elif key in ("chan1", "chan2"):
            lines.append(f"{head}: {p['src' + key[-1]]}")
        elif key in ("filt1", "filt2"):
            k = key[-1]
            lines.append(f"{head}: " + (f"d {p['d' + k]}, sigmaColor {p['sc' + k]}, sigmaSpace {p['ss' + k]}" if p["bil" + k] else "bypassed"))
        elif key in ("otsu1", "otsu2"):
            k = int(key[-1])
            if res.get(f"abs{k}"):
                lines.append(f"{head}: absolute threshold {res[f'thr{k}']:.0f} (Otsu would be {res[f'otsu{k}']:.0f}; % and minimum not used) "
                             f"({'dark' if res[f'dark{k}'] else 'bright'} = 1) {int(res[f'mask{k}'].sum())} px")
            elif res.get(f"below{k}"):
                lines.append(f"{head}: {res[f'otsu{k}']:.0f} < minimum {p[f'min{k}']} -> mask {k} empty")
            else:
                lines.append(f"{head}: {res[f'otsu{k}']:.0f} x {p[f'pct{k}']:.2f} % = {res[f'thr{k}']:.1f} "
                             f"({'dark' if res[f'dark{k}'] else 'bright'} = 1) {int(res[f'mask{k}'].sum())} px")
        else:
            used = " AND ".join(n for n, u in (("mask 1", p["and1"]), ("mask 2", p["and2"])) if u) or "nothing"
            lines.append(f"{tag}{i} mask 3 = {used}: {int(res['mask3'].sum())} px")
    return lines


# ---------------------------------------------------------------------------------------------------- window
def main():
    from PySide6.QtCore import Qt, QObject, Signal
    from PySide6.QtGui import QImage, QPixmap
    from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QGridLayout,
                                   QGroupBox, QHBoxLayout, QLabel, QMainWindow, QPlainTextEdit, QPushButton, QScrollArea,
                                   QSizePolicy, QSpinBox, QVBoxLayout, QWidget, QAbstractSpinBox)

    def readable(wdg):
        """CHANGES 239: never squeezed below the width that shows its whole value (spin box) or its longest item
        (combo box, also in the open list)."""
        if isinstance(wdg, QComboBox):
            wdg.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
            fm = wdg.fontMetrics()
            longest = max((fm.horizontalAdvance(wdg.itemText(i)) for i in range(wdg.count())), default=40)
            wdg.setMinimumWidth(longest + 40)
            wdg.view().setMinimumWidth(longest + 40)
        elif isinstance(wdg, QAbstractSpinBox):
            wdg.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
            wdg.setMinimumWidth(wdg.sizeHint().width() + 8)
        return wdg

    class Picture(QLabel):
        """A picture that keeps its aspect when the panel is resized."""
        def __init__(self, title):
            super().__init__()
            self.setAlignment(Qt.AlignCenter)
            self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
            self.setStyleSheet("QLabel { background: black; }")   # the label only: its tooltip keeps its own colours (CHANGES 232)
            self.setToolTip(title)
            self.bgr = None

        def show_(self, bgr):
            self.bgr = bgr
            self._draw()

        def _draw(self):
            if self.bgr is None:
                self.clear()
                return
            rgb = cv2.cvtColor(self.bgr, cv2.COLOR_BGR2RGB)
            img = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format_RGB888).copy()
            self.setPixmap(QPixmap.fromImage(img).scaled(max(self.width(), 10), max(self.height(), 10), Qt.KeepAspectRatio,
                                                         Qt.SmoothTransformation))

        def resizeEvent(self, e):
            self._draw()
            super().resizeEvent(e)

    class Bridge(QObject):
        loaded = Signal(object)
        status = Signal(str)

    class Window(QMainWindow):
        def __init__(self, folder):
            super().__init__()
            self.setWindowTitle("Study_SplitPin_Steps_UI - the split pin legs step by step")
            self.items, self.geo, self.models, self.step, self.busy = [], None, None, -1, False
            self.bridge = Bridge()
            self.bridge.loaded.connect(self.onLoaded)
            self.bridge.status.connect(lambda s: self.status.setText(s))
            central = QWidget()
            lay = QVBoxLayout(central)

            # ---- top 25 %: values and choice boxes
            top = QWidget()
            tl = QVBoxLayout(top)
            tl.setContentsMargins(0, 0, 0, 0)
            row = QHBoxLayout()
            fb = QPushButton("Folder...")
            fb.clicked.connect(self.chooseFolder)
            self.pictureBox = QComboBox()
            self.pictureBox.setMinimumWidth(320)
            self.pictureBox.currentIndexChanged.connect(lambda i: self.load())
            prev, nxt = QPushButton("< Picture"), QPushButton("Picture >")
            prev.clicked.connect(lambda: self.pictureBox.setCurrentIndex(max(self.pictureBox.currentIndex() - 1, 0)))
            nxt.clicked.connect(lambda: self.pictureBox.setCurrentIndex(min(self.pictureBox.currentIndex() + 1, self.pictureBox.count() - 1)))
            self.model = QComboBox()
            self.model.addItems(["DOST", "DOSTPLUS"])
            self.model.setCurrentText(S.arg("--model", "DOST").upper())
            self.model.currentIndexChanged.connect(lambda i: self.load())
            self.nextButton, back, runAll, reset = QPushButton("Next Step"), QPushButton("Back"), QPushButton("Run all"), QPushButton("Reset")
            self.nextButton.clicked.connect(lambda: self.goto(self.step + 1, +1))
            back.clicked.connect(lambda: self.goto(self.step - 1, -1))
            runAll.clicked.connect(lambda: self.goto(len(self.sequence()) - 1))
            reset.clicked.connect(lambda: self.goto(0))
            self.outlinesBox = QCheckBox("Outlines on original")
            self.outlinesBox.setChecked(True)
            self.finalZoom = QCheckBox("Final zoomed")
            for wdg in (self.outlinesBox, self.finalZoom):
                wdg.stateChanged.connect(lambda _: self.redraw())
            self.stepLabel = QLabel("")
            for wdg in (fb, self.pictureBox, prev, nxt, QLabel("Model"), self.model, self.nextButton, back, runAll, reset,
                        self.outlinesBox, self.finalZoom, self.stepLabel):
                row.addWidget(wdg)
            row.addStretch(1)
            tl.addLayout(row)
            self.p = {"A": {}, "B": {}}
            self.widgets = {"A": {}, "B": {}}
            # CHANGES 239: both regions in one grid, a row per channel (were two boxes side by side, squeezed)
            regionsBox = QGroupBox("Regions")
            rg = QGridLayout(regionsBox)
            rg.setContentsMargins(6, 4, 6, 4)
            rg.setVerticalSpacing(2)
            marker = S.arg("--marker", "red").lower()
            preset = MARKER_PRESETS.get(marker, MARKER_PRESETS["red"])
            self.regionBox(rg, 0, "A", "A  split pin washer", preset["A"], preset["and1"])
            self.regionBox(rg, 2, "B", "B  plate washer / seal", preset["B"], preset["and1"])
            rg.setColumnStretch(rg.columnCount(), 1)
            boxes = QVBoxLayout()
            boxes.addWidget(regionsBox)
            row3 = QHBoxLayout()
            self.joinBox = QCheckBox("Join the masks before the result (white copy, masks black, open)")
            self.joinBox.setChecked(True)
            self.joinBox.stateChanged.connect(lambda _: self.redraw())
            self.joinKernel = QSpinBox()
            self.joinKernel.setRange(1, 15)
            self.joinKernel.setValue(JOIN_KERNEL)
            self.joinKernel.valueChanged.connect(lambda _: self.redraw())
            self.bridgeBox = QCheckBox("Bridge leg ends (directed rectangle, only up to the other region's mask)")
            self.bridgeBox.setChecked(True)
            self.bridgeBox.stateChanged.connect(lambda _: self.redraw())
            self.bridgeLength, self.bridgeWidth = QSpinBox(), QSpinBox()
            for s, v in ((self.bridgeLength, BRIDGE_LENGTH), (self.bridgeWidth, BRIDGE_WIDTH)):
                s.setRange(1, 60)
                s.setValue(v)
                s.valueChanged.connect(lambda _: self.redraw())
            self.bridgeMode = QComboBox()
            self.bridgeMode.addItems(["closest points", "leg direction"])
            self.bridgeMode.currentIndexChanged.connect(lambda _: self.redraw())
            for wdg in (self.joinKernel, self.bridgeLength, self.bridgeWidth, self.bridgeMode, self.model):
                readable(wdg)
            for wdg in (self.bridgeBox, self.bridgeMode, QLabel("length px"), self.bridgeLength, QLabel("width px"), self.bridgeWidth):
                row3.addWidget(wdg)
            for wdg in (self.joinBox, QLabel("kernel px"), self.joinKernel):
                row3.addWidget(wdg)
            row3.addStretch(1)
            tl.addLayout(row3)
            tl.addLayout(boxes)

            # ---- four pictures
            mid = QWidget()
            grid = QGridLayout(mid)
            grid.setContentsMargins(0, 0, 0, 0)
            self.pics = [Picture(t) for t in ("Original", "Split pin washer (A)", "Plate washer / seal (B)", "Result")]
            for i, pic in enumerate(self.pics):
                grid.addWidget(pic, i // 2, i % 2)

            # ---- the text (left of the pictures since CHANGES 230)
            self.text = QPlainTextEdit()
            self.text.setReadOnly(True)
            self.status = QLabel("")
            bottom = QWidget()
            bl = QVBoxLayout(bottom)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.addWidget(self.text, 1)
            bl.addWidget(self.status)
            # top 25 %: the values; bottom 75 %: the text (left 25 %) and the pictures (right 75 %) - CHANGES 230
            lower = QWidget()
            ll = QHBoxLayout(lower)
            ll.setContentsMargins(0, 0, 0, 0)
            ll.addWidget(bottom, 25)
            ll.addWidget(mid, 75)
            topScroll = QScrollArea()       # CHANGES 239: the values scroll if the top 25 % is too low for them
            topScroll.setWidgetResizable(True)
            topScroll.setWidget(top)
            lay.addWidget(topScroll, 25)
            lay.addWidget(lower, 75)
            self.setCentralWidget(central)
            if folder:
                self.setFolder(pathlib.Path(folder))

        def regionBox(self, g, row0, tag, title, sources, and1=True):
            """The region's two rows in the shared grid g from row row0 (CHANGES 239)."""
            w = self.widgets[tag]

            def spin(key, low, high, value, step=1.0, dec=0):
                s = QDoubleSpinBox() if dec else QSpinBox()
                if dec:
                    s.setDecimals(dec)
                s.setRange(low, high)
                s.setSingleStep(step)
                s.setValue(value)
                s.valueChanged.connect(lambda _: self.redraw(recompute=True))
                w[key] = s
                return readable(s)

            def check(key, text, value):
                c = QCheckBox(text)
                c.setChecked(value)
                c.stateChanged.connect(lambda _: self.redraw(recompute=True))
                w[key] = c
                return c

            head = QLabel(f"<b>{title}</b>")
            g.addWidget(head, row0, 0)
            g.addWidget(QLabel("Sector +- deg"), row0, 1)
            g.addWidget(spin("sector", 10, 120, SECTOR), row0, 2)
            if tag == "B":                      # B: outline + from px .. outline + to px; the open first (CHANGES 241)
                op = QWidget()
                ol = QHBoxLayout(op)
                ol.setContentsMargins(0, 0, 0, 0)
                ol.addWidget(check("open", "Open first, kernel", True))
                ol.addWidget(spin("openK", 1, 25, OPEN_KERNEL))
                g.addWidget(op, row0 + 1, 0)
                ft = QWidget()
                fl = QHBoxLayout(ft)
                fl.setContentsMargins(0, 0, 0, 0)
                fl.addWidget(QLabel("px past outline"))
                fl.addWidget(spin("from", 0, 20, 0))        # 0 = on the scallop outline (CHANGES 241; was 1)
                fl.addWidget(QLabel("to"))
                fl.addWidget(spin("to", 5, 80, 30))
                fl.addStretch(1)
                g.addWidget(ft, row0 + 1, 1, 1, 2)
            C = 3                               # first column of the channel values
            for k in (1, 2):
                r = row0 + k - 1
                src = QComboBox()
                src.addItems(SOURCES)
                src.setCurrentText(sources[k - 1])
                src.currentIndexChanged.connect(lambda _: self.redraw(recompute=True))
                w[f"src{k}"] = src
                g.addWidget(QLabel(f"Channel {k}"), r, C)
                g.addWidget(readable(src), r, C + 1)
                g.addWidget(check(f"bil{k}", "Bilateral", DEFAULTS[f"bil{k}"]), r, C + 2)
                g.addWidget(QLabel("d"), r, C + 3)
                g.addWidget(spin(f"d{k}", -1, 25, DEFAULTS[f"d{k}"]), r, C + 4)
                g.addWidget(QLabel("sigmaColor"), r, C + 5)
                g.addWidget(spin(f"sc{k}", 1, 300, DEFAULTS[f"sc{k}"], 5, 1), r, C + 6)
                g.addWidget(QLabel("sigmaSpace"), r, C + 7)
                g.addWidget(spin(f"ss{k}", 1, 300, DEFAULTS[f"ss{k}"], 5, 1), r, C + 8)
                g.addWidget(QLabel("Otsu %"), r, C + 9)
                g.addWidget(spin(f"pct{k}", 50, 300, DEFAULTS[f"pct{k}"], 0.05, 2), r, C + 10)
                g.addWidget(QLabel("Otsu min"), r, C + 11)
                g.addWidget(spin(f"min{k}", 0, 255, OTSU_MIN[tag][k - 1]), r, C + 12)
                g.addWidget(QLabel("Absolute"), r, C + 13)        # CHANGES 242: > 0 = this threshold, Otsu / % / min not used
                g.addWidget(spin(f"abs{k}", 0, 255, 0), r, C + 14)
                g.addWidget(check(f"and{k}", f"Mask {k} in AND", and1 if k == 1 else True), r, C + 15)

        def params(self, tag):
            p = {}
            for k, wdg in self.widgets[tag].items():
                if isinstance(wdg, QComboBox):
                    p[k] = wdg.currentText()
                elif isinstance(wdg, QCheckBox):
                    p[k] = wdg.isChecked()
                else:
                    p[k] = wdg.value()
            return p

        def chooseFolder(self):
            chosen = QFileDialog.getExistingDirectory(self, "Folder of split pin pictures", str(HERE))
            if chosen:
                self.setFolder(pathlib.Path(chosen))

        def setFolder(self, folder):
            self.items = picturesIn(folder)
            self.pictureBox.blockSignals(True)
            self.pictureBox.clear()
            self.pictureBox.addItems([f"{lab or '-'}  {p.name}" for p, lab in self.items])
            self.pictureBox.blockSignals(False)
            self.status.setText(f"{len(self.items)} pictures in {folder}")
            if self.items:
                self.load()

        def load(self):
            i = self.pictureBox.currentIndex()
            if self.busy or not (0 <= i < len(self.items)):
                return
            self.busy = True
            self.nextButton.setEnabled(False)
            p, lab = self.items[i]
            self.status.setText(f"{p.name}: holes, washer, outline, head ...")
            threading.Thread(target=self._load, args=(p, lab, self.model.currentText()), daemon=True).start()

        def _load(self, p, lab, model):
            try:
                if self.models is None:
                    self.bridge.status.emit("Loading the hole finder and MobileSAM ...")
                    self.models = S.setUp()[:3]
                self.bridge.loaded.emit(geometry(p, lab, model, self.models))
            except Exception as e:
                traceback.print_exc()
                self.bridge.loaded.emit(dict(name=p.name, label=lab, im=None, note=f"exception: {e}"))

        def onLoaded(self, geo):
            self.busy = False
            self.nextButton.setEnabled(True)
            self.geo = geo
            self.res = None
            self.status.setText(f"{geo['name']} ({geo['label'] or '-'}) {geo['note']}")
            self.goto(0)

        def compute(self):
            geo = self.geo
            pa, pb = self.params("A"), self.params("B")
            regA, _ = regions(geo, pa["sector"], 0, 0)
            _, regB = regions(geo, pb["sector"], pb["from"], pb["to"])
            self.res = dict(A=runRegion(geo["crop"], regA, pa), B=runRegion(geo["crop"], regB, pb), pa=pa, pb=pb)

        def sequence(self):
            """[(region, step key)] for A, then B, then ("R", "result") - CHANGES 241."""
            return [("A", k) for k in STEPS["A"]] + [("B", k) for k in STEPS["B"]] + [("R", "result")]

        def goto(self, step, direction=0):
            """Go to step; Next (+1) / Back (-1) pass over the steps of a mask out of the AND and the open when off."""
            if self.geo is None:
                return
            seq = self.sequence()
            step = int(np.clip(step, 0, len(seq) - 1))
            if direction:
                p = {"A": self.params("A"), "B": self.params("B")}
                while 0 <= step < len(seq) and seq[step][0] != "R" and stepSkipped(seq[step][0], seq[step][1], p[seq[step][0]]):
                    step += direction
                step = int(np.clip(step, 0, len(seq) - 1))
            self.step = step
            self.redraw(recompute=True)

        def redraw(self, recompute=False):
            geo = self.geo
            if geo is None:
                return
            if geo.get("crop") is None:
                for pic in self.pics:
                    pic.show_(None)
                if geo.get("im") is not None:
                    self.pics[0].show_(geo["im"])
                self.text.setPlainText(geo["note"])
                return
            if recompute or self.res is None:
                self.compute()
            crop = geo["crop"]
            orig = crop.copy()
            if self.outlinesBox.isChecked():
                outlines(orig, geo)
            self.pics[0].show_(view(orig, geo))
            seq = self.sequence()
            tagNow, keyNow = seq[self.step]
            nA, nB = len(STEPS["A"]), len(STEPS["B"])
            sA = self.step if tagNow == "A" else nA - 1          # index in STEPS["A"] shown / reached
            sB = self.step - nA if tagNow == "B" else (nB - 1 if tagNow == "R" else -1)
            atResult = tagNow == "R"
            self.pics[1].show_(view(stepPicture(crop, self.res["A"], STEPS["A"][sA], self.res["pa"]), geo))
            self.pics[2].show_(view(stepPicture(crop, self.res["B"], STEPS["B"][sB], self.res["pb"]) if sB >= 0 else crop, geo))
            if atResult:
                mA, mB = self.res["A"]["mask3"], self.res["B"]["mask3"]
                red = mA | mB
                self.redBefore = int(red.sum())
                self.bridgeInfo = None
                bridgeMask = np.zeros_like(red)
                if self.bridgeBox.isChecked():
                    past = radialPastOutline(geo)
                    L, W = self.bridgeLength.value(), self.bridgeWidth.value()
                    if self.bridgeMode.currentText() == "closest points":
                        bridgeMask, iC = bridgesClosest(mA, mB, L, W)
                        self.bridgeInfo = ("closest", iC, int((bridgeMask & ~(mA | mB)).sum()))
                    else:
                        bAB, iAB = bridges(mA, mB, past, True, L, W)       # washer legs' outer ends -> the seal's pieces
                        bBA, iBA = bridges(mB, mA, past, False, L, W)      # seal pieces' inner ends -> the washer's legs
                        bridgeMask = bAB | bBA
                        self.bridgeInfo = ("direction", (iAB, iBA), int((bridgeMask & ~(mA | mB)).sum()))
                    red = red | bridgeMask
                if self.joinBox.isChecked():
                    red = joinMasks(red, self.joinKernel.value())
                self.redAfter = int(red.sum())
                if self.finalZoom.isChecked():
                    final = fill(crop, red, MASK3_COLOUR, 0.55)
                    for c in cv2.findContours((bridgeMask & ~(mA | mB)).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)[0]:
                        cv2.polylines(final, [c], True, (255, 0, 255), 1)   # what the bridges added, magenta outline
                    final = view(final, geo)
                else:
                    full = np.zeros(geo["im"].shape[:2], bool)
                    full[geo["y0"]:geo["y0"] + crop.shape[0], geo["x0"]:geo["x0"] + crop.shape[1]] = red
                    final = fill(geo["im"], full, MASK3_COLOUR, 0.55)
                self.pics[3].show_(final)
            else:
                self.pics[3].show_(geo["im"])
            name = "result" if atResult else f"{tagNow}{sA if tagNow == 'A' else sB} {STEP_TITLES[keyNow]}"
            self.stepLabel.setText(f"Step: {name}")
            lines = [f"{geo['name']} ({geo['label'] or '-'})  head {'at %.0f deg' % geo['head']['end'] if geo['head']['found'] else 'not found'},"
                     f" legs axis {geo['axis']:.0f} deg; scallops placed from neighbours "
                     f"{sum(not s['found'] for s in geo['outline']['segs'])} of {len(geo['outline']['segs'])}"]
            lines += stepLines("A", self.res["A"], sA, self.res["pa"])
            lines += stepLines("B", self.res["B"], sB, self.res["pb"])
            if atResult:
                if self.bridgeInfo is not None:
                    mode, info, px = self.bridgeInfo
                    if mode == "closest":
                        lines.append(f"bridges (closest points, up to {self.bridgeLength.value()} px, {self.bridgeWidth.value()} px wide): "
                                     f"{len(info)}, {px} px added")
                        for ax, ay, bx, by, gap in info:
                            lines.append(f"   A ({ax}, {ay}) -> B ({bx}, {by}): gap {gap:.1f} px")
                    else:
                        iAB, iBA = info
                        lines.append(f"bridges (leg direction, {self.bridgeLength.value()} x {self.bridgeWidth.value()}): {len(iAB)} from the "
                                     f"washer's legs, {len(iBA)} from the seal's pieces, {px} px added")
                        for tag, inf in (("A->B", iAB), ("B->A", iBA)):
                            for ex, ey, ang, n in inf:
                                lines.append(f"   {tag} end ({ex:.0f}, {ey:.0f}) direction {ang:.0f} deg: {n} px")
                lines.append(f"result: A | B {self.redBefore} px; " + (f"joined (white copy, masks black, open {self.joinKernel.value()} x "
                             f"{self.joinKernel.value()}): {self.redAfter} px red" if self.joinBox.isChecked() else "not joined"))
            self.text.setPlainText("\n".join(lines))

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")       # CHANGES 239: spin boxes with the up / down arrows stacked at the right (Windows 11 style: side by side)
    try:
        from frontend.QtMessages import installQtMessageHandler   # Qt's own messages to the log, not the C stderr
        installQtMessageHandler()
    except Exception:
        pass
    flagsWithValue = {S.arg(f) for f in ("--model", "--holes", "--sam", "--marker")}
    folder = next((a for a in sys.argv[1:] if not a.startswith("--") and a not in flagsWithValue), None)
    w = Window(folder)
    w.resize(1600, 1000)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
