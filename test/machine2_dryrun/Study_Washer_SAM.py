"""
Study_Washer_SAM - the washer and the split pin legs by MobileSAM masks + a fitted washer profile (study, not a test;
CHANGES 180). Uddi's design of 2026-11-21:
  1. the hub's holes (FindHubHoles.findHoles, as production: YOLO + MobileSAM round the hub-check centre);
  2. where the spindle centre lies relative to them, for DOST and DOSTPLUS: the castle nut found by a MobileSAM prompt
     (point on the nut + box) and its circle; the washer centre from the fitted profile; both printed against the
     hole circle's centre, with per-model means / sd at the end;
  3. MobileSAM prompts that give the washer mask: several prompt sets are tried on every picture, every candidate mask
     printed (area, SAM score, nut covered, leak beyond the washer, IoU with the expected ring) and one picked;
  4. the washer outline fitted to that mask (circle + its 12 scallops + perspective terms, robust, so the angles
     hidden by the eye and legs are completed by the fit);
  5. the split pin's eye (the largest bright, non-blue object just outside the outline), the half of the washer
     opposite it, the leg-free area of that half from the completed outline, the pixels of it covered (not in the
     washer mask) = the legs, and the share covered; legs standing out past the outline (objects that cross it);
  6. per model and label: the ranges, and the best joint cut-off (share covered, standing out) with its errors.

Labels: OK\\ and NOT_OK\\ sub-folders of each set. Pictures with outlines only (no text) to --out.

Run on the laptop (GPU, models with the YOLO weights), from the project root:
    python test/machine2_dryrun/Study_Washer_SAM.py --set DOST=test/machine2_dryrun/splitpin_images
        --set DOSTPLUS=C:\\Temp\\splitpin_dostplus_labelled [--hub DOSTPLUS=C:\\Temp\\splitpin_dostplus\\Hub.png]
        [--limit N] [--out folder] [--holes sam|morphology] [--sam models/MobileSAM/weights/mobile_sam.pt]
    > washer_study.log 2>&1
Without --set: DOST = this folder's splitpin_images. Upload washer_study.log (and a few pictures from --out).
"""
import io, contextlib, math, os, pathlib, sys, time
from collections import OrderedDict

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
os.chdir(ROOT)
os.environ.setdefault("COSTHETA_PLC_VENDOR", "allenbradley" if "machine1" in str(HERE) else "siemens")
import types
if sys.platform != 'win32':
    sys.modules.setdefault('winsound', types.SimpleNamespace(PlaySound=lambda *a, **k: None, SND_FILENAME=0,
                                                             SND_ASYNC=0, Beep=lambda *a: None))
import cv2
import numpy as np

EXT = (".png", ".jpg", ".jpeg", ".bmp")
NB = 360
T = np.deg2rad(np.arange(NB) + 0.5)
CROP_HALF = 120                       # SAM sees a 240 x 240 crop round the estimated centre
# starting values (sandbox colour study of 2026-11-21: washer outline r 45.5-46.2 DOST / 44.3-44.8 DOSTPLUS, nut r
# about 28 / 31); printed and re-measured here
R_WASHER = {"DOST": 46.0, "DOSTPLUS": 44.6}
R_NUT = {"DOST": 28.0, "DOSTPLUS": 30.0}
# which steps of analyse() run (CHANGES 195): the washer prompts and the outline fit always; the rest only for the
# washer / legs study of 180 - 181. Study_Red_Legs_UI turns them off - it needs the washer and its outline only.
STEPS = {
    "nut": True,            # 3 MobileSAM prompts for the nut (the spindle centre); off: the centre = holes + offset
    "legs_study": True,     # eye, coverage, pin prompts, leg grid, YOLO boxes, blue share - the 180 study's leg steps
    "no_colour": False,     # the washer prompts' bright points by grey >= 130 instead of V >= 130 and S <= 110 (CHANGES 200)
}


def arg(name, default=None, many=False):
    vals = [sys.argv[i + 1] for i, a in enumerate(sys.argv[:-1]) if a == name]
    return vals if many else (vals[-1] if vals else default)


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------------------------------------------- models
def samPredictor():
    from mobile_sam import SamPredictor, sam_model_registry
    path = arg("--sam")
    if path is None:
        try:
            from camera.ModelManager import ModelManager
            return SamPredictor(ModelManager.get_instance().get_sam_model()), "ModelManager"
        except Exception as e:
            log(f"MODELS ModelManager's MobileSAM not loaded ({e}); trying models/MobileSAM/weights/mobile_sam.pt directly")
            path = str(ROOT / "models" / "MobileSAM" / "weights" / "mobile_sam.pt")
    import torch
    m = sam_model_registry["vit_t"](checkpoint=path)
    m.to("cuda" if torch.cuda.is_available() else "cpu").eval()
    return SamPredictor(m), path


_YOLO = {"model": None, "tried": False}


def yoloModel():
    """ModelManager's YOLO (ObjectAwareModel, the boxes production gives MobileSAM); None where the weights are missing."""
    if not _YOLO["tried"]:
        _YOLO["tried"] = True
        try:
            from camera.ModelManager import ModelManager
            _YOLO["model"] = ModelManager.get_instance().get_yolo_model()
            log("MODELS YOLO loaded (ObjectAwareModel)")
        except Exception as e:
            log(f"MODELS YOLO not available ({e}): the YOLO-box legs step is skipped")
    return _YOLO["model"]


# ---------------------------------------------------------------------------------------------------- geometry
def polarOutermost(mask, cx, cy, rlo, rhi):
    P = cv2.warpPolar(mask.astype(np.uint8) * 255, (int(rhi) + 2, NB), (cx, cy), int(rhi) + 2,
                      cv2.WARP_POLAR_LINEAR | cv2.INTER_NEAREST) > 127
    rout = np.full(NB, np.nan); rin = np.full(NB, np.nan)
    for a in range(NB):
        idx = np.flatnonzero(P[a, int(rlo):int(rhi)])
        if idx.size:
            rin[a] = rlo + idx[0]; rout[a] = rlo + idx[-1]
    return rin, rout


def robustFit(r, harmonics, iters=8, k=2.0):
    X = np.column_stack([np.ones(NB)] + [f(h * T) for h in harmonics for f in (np.cos, np.sin)])
    ok = np.isfinite(r); w = ok.copy()
    coef = None
    for _ in range(iters):
        if w.sum() < X.shape[1] + 10:
            return None, w, None
        coef, *_ = np.linalg.lstsq(X[w], r[w], rcond=None)
        res = r - X @ coef
        s = 1.4826 * np.median(np.abs(res[w])) + 0.4
        w = ok & (np.abs(res) <= k * s)
    names = ["c"] + [f"{p}{h}" for h in harmonics for p in "ab"]
    return X @ coef, w, dict(zip(names, coef))


def ransacCircle(xs, ys, tol=1.5, tries=300, seed=0):
    """circle through most contour points (castle slots and the pin break the nut's outline)."""
    rng = np.random.default_rng(seed); n = len(xs); best = None
    if n < 10:
        return None
    for _ in range(tries):
        i = rng.choice(n, 3, replace=False)
        try:
            cx, cy, r, _ = circleFit(xs[i], ys[i])
        except Exception:
            continue
        if not (5 < r < 80):
            continue
        inl = np.abs(np.hypot(xs - cx, ys - cy) - r) <= tol
        if best is None or inl.sum() > best[0]:
            best = (int(inl.sum()), inl)
    cx, cy, r, sd = circleFit(xs[best[1]], ys[best[1]])
    return cx, cy, r, sd, best[0] / n


def circleFit(xs, ys):
    A = np.column_stack([xs, ys, np.ones_like(xs)])
    b = xs ** 2 + ys ** 2
    c, *_ = np.linalg.lstsq(A, b, rcond=None)
    cx, cy = c[0] / 2, c[1] / 2
    r = math.sqrt(max(c[2] + cx ** 2 + cy ** 2, 0))
    res = np.hypot(xs - cx, ys - cy) - r
    return cx, cy, r, float(np.std(res))


# ---------------------------------------------------------------------------------------------------- one picture
def analyse(im, model, pred, holesResult, offset):
    out = OrderedDict()
    H, W = im.shape[:2]
    hx, hy = holesResult["cx"], holesResult["cy"]
    ex, ey = hx + offset[0], hy + offset[1]                 # estimated washer / spindle centre (current offset)
    x0, y0 = int(round(ex)) - CROP_HALF, int(round(ey)) - CROP_HALF
    crop = np.ascontiguousarray(cv2.cvtColor(im[y0:y0 + 2 * CROP_HALF, x0:x0 + 2 * CROP_HALF], cv2.COLOR_BGR2RGB))
    c = np.array([ex - x0, ey - y0])
    # geometry for the pictures (full-picture coordinates where noted), returned whatever step fails
    base = dict(x0=x0, y0=y0, hx=hx, hy=hy, hr=holesResult.get("radius"), hpts=holesResult.get("points"), ex=ex, ey=ey,
                cc=c, nut=None, prof=None)
    hsv = cv2.cvtColor(im[y0:y0 + 2 * CROP_HALF, x0:x0 + 2 * CROP_HALF], cv2.COLOR_BGR2HSV).astype(int)
    Hh, Ss, Vv = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    yy, xx = np.mgrid[:2 * CROP_HALF, :2 * CROP_HALF]
    Rw, Rn = R_WASHER[model], R_NUT[model]
    rmid = (Rw + Rn) / 2
    t0 = time.perf_counter()
    import torch
    with torch.inference_mode():
        pred.set_image(crop)
        out["t_embed"] = time.perf_counter() - t0

        # ---- the nut (spindle centre): point on the nut + box round it
        t1 = time.perf_counter()
        nutCands = []
        for name, kw in ([] if not STEPS["nut"] else (("N1 point+box", dict(point_coords=np.array([c]), point_labels=np.array([1]),
                                                box=np.array([*(c - Rn - 8), *(c + Rn + 8)]))),
                         ("N2 point", dict(point_coords=np.array([c]), point_labels=np.array([1]))),
                         ("N3 point+ringneg", dict(point_coords=np.array([c] + [c + rmid * np.array([math.cos(a), math.sin(a)]) for a in np.deg2rad(np.arange(0, 360, 45))]),
                                                   point_labels=np.array([1] + [0] * 8))))):
            masks, scores, _ = pred.predict(multimask_output=True, **kw)
            for i, (m, s) in enumerate(zip(masks, scores)):
                ys, xs = np.nonzero(m)
                if len(xs) < 50:
                    continue
                cnts, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
                cnt = max(cnts, key=cv2.contourArea)
                p = cnt[:, 0, :].astype(float)
                rc = ransacCircle(p[:, 0], p[:, 1])
                if rc is None:
                    continue
                fx, fy, fr, fsd, share = rc
                area = float(m.sum())
                circ = 4 * math.pi * cv2.contourArea(cnt) / max(cv2.arcLength(cnt, True) ** 2, 1)
                nutCands.append(dict(name=name, i=i, score=float(s), area=area, cx=fx + x0, cy=fy + y0, r=fr, sd=fsd, circ=circ, share=share, mask=m))
        # the nut: area near pi Rn^2, a circle of radius near Rn through most of its outline (RANSAC share), centre
        # within 8 px of the estimate
        okN = [n for n in nutCands if 0.5 * math.pi * Rn ** 2 < n["area"] < 1.7 * math.pi * Rn ** 2 and abs(n["r"] - Rn) < 8
               and math.hypot(n["cx"] - x0 - c[0], n["cy"] - y0 - c[1]) < 8]
        nut = max(okN, key=lambda n: (n["share"], -n["sd"])) if okN else None
        for n in nutCands:
            log(f"   NUTCAND {n['name']:16s} m{n['i']} score {n['score']:.3f} area {n['area']:6.0f} circle ({n['cx']:.1f}, {n['cy']:.1f}) r {n['r']:.1f} sd {n['sd']:.2f} on circle {n['share']:.2f} circularity {n['circ']:.3f}"
                + ("  <- picked" if n is nut else ""))
        out["t_nut"] = time.perf_counter() - t1
        if nut:
            base["nut"] = (nut["cx"], nut["cy"], nut["r"]); base["nutMask"] = nut["mask"]
            out.update(nut_cx=nut["cx"], nut_cy=nut["cy"], nut_r=nut["r"], nut_sd=nut["sd"], nut_dx=nut["cx"] - hx, nut_dy=nut["cy"] - hy)
            cc = np.array([nut["cx"] - x0, nut["cy"] - y0]); Rn_used = nut["r"]
        else:
            cc = c; Rn_used = Rn
        out["centre_used"] = "nut" if nut else "holes+offset"
        base["cc"] = cc

        # ---- the washer: several prompt sets
        t2 = time.perf_counter()
        d = np.hypot(xx - cc[0], yy - cc[1])
        ring = (d >= Rn_used + 1) & (d <= Rw)
        if STEPS.get("no_colour"):                              # grey only (CHANGES 200): no hue / saturation
            bright = cv2.cvtColor(im[y0:y0 + 2 * CROP_HALF, x0:x0 + 2 * CROP_HALF], cv2.COLOR_BGR2GRAY) >= 130
        else:
            bright = (Vv >= 130) & (Ss <= 110)
        ang8 = np.deg2rad(np.arange(0, 360, 45) + 22.5); ang12 = np.deg2rad(np.arange(0, 360, 30) + 15)
        ringPts = lambda angs, r: [cc + r * np.array([math.cos(a), math.sin(a)]) for a in angs]
        brightPts = [p for p in ringPts(np.deg2rad(np.arange(0, 360, 15)), rmid)
                     if bright[int(round(p[1])), int(round(p[0]))]][:12]
        sealNeg = ringPts(np.deg2rad(np.arange(0, 360, 90) + 45), Rw + 14)
        box = np.array([*(cc - Rw - 6), *(cc + Rw + 6)])
        sets = OrderedDict()
        sets["W1 box"] = dict(box=box)
        sets["W2 box+ring8+nutneg"] = dict(box=box, point_coords=np.array(ringPts(ang8, rmid) + [cc]), point_labels=np.array([1] * 8 + [0]))
        sets["W3 ring12+nutneg"] = dict(point_coords=np.array(ringPts(ang12, rmid) + [cc]), point_labels=np.array([1] * 12 + [0]))
        if len(brightPts) >= 3:
            sets["W4 box+brightring+nutneg+sealneg"] = dict(box=box, point_coords=np.array(brightPts + [cc] + sealNeg),
                                                            point_labels=np.array([1] * len(brightPts) + [0] * (1 + len(sealNeg))))
        sets["W5 box+ring8+nutneg+sealneg"] = dict(box=box, point_coords=np.array(ringPts(ang8, rmid) + [cc] + sealNeg),
                                                   point_labels=np.array([1] * 8 + [0] * (1 + len(sealNeg))))
        cands = []
        for name, kw in sets.items():
            masks, scores, _ = pred.predict(multimask_output=True, **kw)
            for i, (m, s) in enumerate(zip(masks, scores)):
                area = float(m.sum())
                if area < 100:
                    continue
                nutCov = float(m[d < Rn_used - 4].mean()) if (d < Rn_used - 4).any() else 0.0
                leak = float(m[d > Rw + 10].sum() / area)
                disc = d <= Rw
                # SAM gives the washer either as a ring or as one disc with the nut: compare with the matching shape
                iou = float((m & (disc if nutCov >= 0.5 else ring)).sum() / max((m | (disc if nutCov >= 0.5 else ring)).sum(), 1))
                brightIn = float(bright[m].mean())
                cands.append(dict(name=name, i=i, score=float(s), area=area, nutCov=nutCov, leak=leak, iou=iou, brightIn=brightIn, mask=m))
        okW = [w for w in cands if w["leak"] < 0.05]
        wash = max(okW, key=lambda w: w["iou"] + 0.1 * w["score"]) if okW else None
        for w in cands:
            log(f"   SAMW {w['name']:34s} m{w['i']} score {w['score']:.3f} area {w['area']:6.0f} nut covered {w['nutCov']:.2f} leak {w['leak']:.2f} ring IoU {w['iou']:.3f} bright share {w['brightIn']:.2f}"
                + ("  <- picked" if w is wash else ""))
        out["t_washer"] = time.perf_counter() - t2
    out["n_prompt_sets"] = len(sets)
    if wash is None:
        out["fail"] = "no washer mask with leak < 0.05"
        return out, dict(base)
    out.update(wash_set=wash["name"], wash_m=wash["i"], wash_area=wash["area"], wash_iou=wash["iou"], wash_score=wash["score"],
               wash_shape="disc" if wash["nutCov"] >= 0.5 else "ring")
    M = wash["mask"]
    M = cv2.morphologyEx(M.astype(np.uint8), cv2.MORPH_OPEN, np.ones((2, 2), np.uint8)).astype(bool)

    # ---- profile fit round the centre used
    rin, rout = polarOutermost(M, cc[0], cc[1], Rn_used * 0.7, Rw + 12)
    fo, wo, co = robustFit(rout, [1, 2, 12, 24])
    if fo is None:
        out["fail"] = "profile fit failed (too few angles)"
        return out, dict(base, M=M)
    a12 = math.hypot(co["a12"], co["b12"]); a6 = None
    # 6-fold (hex nut) check on the raw outline
    rr = rout.copy(); m6 = np.isfinite(rr)
    if m6.sum() > 60:
        X6 = np.column_stack([np.ones(m6.sum()), np.cos(6 * T[m6]), np.sin(6 * T[m6])]); c6, *_ = np.linalg.lstsq(X6, rr[m6], rcond=None)
        a6 = math.hypot(c6[1], c6[2])
    out.update(prof_r=co["c"], prof_a12=a12, prof_a6=a6, prof_inliers=int(wo.sum()),
               prof_dx=co["a1"], prof_dy=co["b1"],                                  # washer centre - centre used
               prof_cx=cc[0] + co["a1"] + x0, prof_cy=cc[1] + co["b1"] + y0)
    out["prof_vs_holes_dx"] = out["prof_cx"] - hx; out["prof_vs_holes_dy"] = out["prof_cy"] - hy
    base["prof"] = (out["prof_cx"], out["prof_cy"])
    if not STEPS["legs_study"]:                                 # the washer and its outline only (CHANGES 195)
        return out, dict(base, M=M, fo=fo, eye=None, Rn=Rn_used)

    # ---- eye, legs half, coverage, standing out
    angle = (np.degrees(np.arctan2(yy - cc[1], xx - cc[0])) % 360)
    ai = np.clip(angle.astype(int), 0, NB - 1)
    FO = fo[ai]
    band = (d >= FO + 3) & (d <= FO + 22)
    blue = (Hh >= 100) & (Hh <= 130) & (Ss >= 60)
    eyeCand = band & (Vv >= 110) & ~blue
    n, lab, st, _ = cv2.connectedComponentsWithStats(eyeCand.astype(np.uint8), 8)
    comps = sorted([(st[k, cv2.CC_STAT_AREA], k) for k in range(1, n)], reverse=True)
    if not comps:
        out["fail"] = "no eye found (no bright object just outside the washer outline)"
        return out, dict(base, M=M, fo=fo)
    k = comps[0][1]; ys, xs = np.nonzero(lab == k)
    eye = math.degrees(math.atan2(ys.mean() - cc[1], xs.mean() - cc[0])) % 360
    out.update(eye_angle=eye, eye_area=int(comps[0][0]), eye2_area=int(comps[1][0]) if len(comps) > 1 else 0)
    dl = np.abs(((angle - (eye + 180)) + 180) % 360 - 180)
    region = (d >= Rn_used + 1) & (d <= FO - 1)
    legsHalf = region & (dl <= 90); eyeHalf = region & (dl > 90)
    # covered = in the completed ring but not bright washer (SAM's disc includes what lies on the washer, so the
    # mask cannot show the legs); cov_mask = not in the mask, for comparison
    washerColour = (Vv >= 130) & (Ss <= 110)
    occ = ~washerColour
    out.update(legs_area=int(legsHalf.sum()), cov_legs=float(occ[legsHalf].mean()), cov_eye=float(occ[eyeHalf].mean()),
               cov_all=float(occ[region].mean()), cov_mask_legs=float((~M)[legsHalf].mean()))
    # occluders: not washer, not the dark seal, from the nut to past the outline; those that cross the outline stand out
    occl = occ & (Vv >= 60) & (d >= Rn_used) & (d <= FO + 20) & ~((d > FO) & M)
    n2, lab2, st2, _ = cv2.connectedComponentsWithStats(occl.astype(np.uint8), 8)
    standLegs = standEye = 0; reachLegs = 0.0
    standMask = np.zeros_like(occl)
    for k in range(1, n2):
        comp = lab2 == k
        if not (comp & (d <= FO - 3)).any():
            continue
        beyond = comp & (d > FO + 4)
        nb = int(beyond.sum())
        if nb == 0:
            continue
        side = float(np.median(dl[comp]))
        if side <= 90:
            standLegs += nb; reachLegs = max(reachLegs, float((d - FO)[beyond].max()))
            standMask |= beyond
        else:
            standEye += nb
    out.update(stand_legs=standLegs, stand_eye=standEye, reach_legs=reachLegs)
    # ---- the split pin itself by MobileSAM: a point on the eye (+ negatives on the nut and on bright washer away
    # from the pin) -> one object, eye + shank + legs, whatever the paint (Uddi, 2026-11-21: unpainted legs are OK too)
    t3 = time.perf_counter()
    ey_, ex_ = ys.mean(), xs.mean()
    eyePt = np.array([ex_, ey_])
    washPts = [cc + rmid * np.array([math.cos(math.radians(a)), math.sin(math.radians(a))]) for a in (eye + 60, eye - 60)]
    legsDir = np.array([math.cos(math.radians(eye + 180)), math.sin(math.radians(eye + 180))])
    pinSets = OrderedDict()
    pinSets["P1 eye"] = dict(point_coords=np.array([eyePt]), point_labels=np.array([1]))
    pinSets["P2 eye+nutneg"] = dict(point_coords=np.array([eyePt, cc]), point_labels=np.array([1, 0]))
    pinSets["P3 eye+nutneg+washneg"] = dict(point_coords=np.array([eyePt, cc] + washPts), point_labels=np.array([1, 0, 0, 0]))
    pinSets["P4 eye+legspoint+nutneg"] = dict(point_coords=np.array([eyePt, cc + legsDir * rmid, cc]), point_labels=np.array([1, 1, 0]))
    pinCands = []
    import torch
    with torch.inference_mode():
        for name, kw in pinSets.items():
            masks, scores, _ = pred.predict(multimask_output=True, **kw)
            for i, (m, sc) in enumerate(zip(masks, scores)):
                a = int(m.sum())
                if a < 30:
                    continue
                inLegs = int((m & legsHalf).sum()); beyondLegs = int((m & (d > FO + 4) & (dl <= 90)).sum())
                nutPart = float(m[d < Rn_used - 4].mean()) if (d < Rn_used - 4).any() else 0.0
                washPart = float((m & region).sum() / max(region.sum(), 1))
                reach = float((d - FO)[m & (dl <= 90)].max()) if (m & (dl <= 90)).any() else 0.0
                pinCands.append(dict(name=name, i=i, score=float(sc), area=a, inLegs=inLegs, beyondLegs=beyondLegs,
                                     nutPart=nutPart, washPart=washPart, reach=reach, hasEye=bool(m[int(ey_), int(ex_)]), mask=m))
    # the pin: contains the eye, not the nut, not the washer (a mask over half the ring is the washer), smallest
    # such mask that reaches into the legs half; else the largest that contains the eye
    okP = [q for q in pinCands if q["hasEye"] and q["nutPart"] < 0.3 and q["washPart"] < 0.5]
    withLegs = [q for q in okP if q["inLegs"] > 30]
    pin = max(withLegs, key=lambda q: (q["score"], -q["area"])) if withLegs else (max(okP, key=lambda q: q["area"]) if okP else None)
    for q in pinCands:
        log(f"   PINCAND {q['name']:24s} m{q['i']} score {q['score']:.3f} area {q['area']:5d} eye {'Y' if q['hasEye'] else 'n'} in legs half {q['inLegs']:5d} "
            f"beyond outline (legs side) {q['beyondLegs']:4d} reach {q['reach']:5.1f} nut {q['nutPart']:.2f} washer ring {q['washPart']:.2f}"
            + ("  <- picked" if q is pin else ""))
    # ---- the legs by a grid of single-point prompts over the legs half of the ring: masks that are small, elongated
    # (or thin), not the nut, inside or crossing the outline = leg pieces; their union = the legs (no colour used)
    t4 = time.perf_counter()
    legPieces = []; legsUnion = np.zeros_like(region)
    radii = [Rn_used + 0.25 * (Rw - Rn_used), (Rn_used + Rw) / 2, Rw - 0.2 * (Rw - Rn_used)]
    angs = [eye + 180 + a for a in range(-80, 81, 20)]
    with torch.inference_mode():
        for rr_ in radii:
            for a in angs:
                pt = cc + rr_ * np.array([math.cos(math.radians(a)), math.sin(math.radians(a))])
                masks, scores, _ = pred.predict(point_coords=np.array([pt]), point_labels=np.array([1]), multimask_output=True)
                for i, (m, sc) in enumerate(zip(masks, scores)):
                    ar = int(m.sum())
                    if ar < 25 or ar > 1500:
                        continue
                    cnts, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
                    cnt = max(cnts, key=cv2.contourArea)
                    (_, _), (w_, h_), _ = cv2.minAreaRect(cnt)
                    aspect = max(w_, h_) / max(min(w_, h_), 1)
                    thin = ar / max(cv2.arcLength(cnt, True), 1)            # ~ half the width of a strip
                    nutP = float(m[d < Rn_used - 2].mean()) if (d < Rn_used - 2).any() else 0.0
                    inside = float((m & (d <= FO)).sum() / ar)
                    beyond = int((m & (d > FO + 4)).sum())
                    farOut = int((m & (d > FO + 30)).sum())
                    keep = (aspect >= 2.5 or thin <= 3.0) and nutP < 0.2 and inside >= 0.3 and farOut <= 5
                    legPieces.append(dict(r=rr_, a=a % 360, i=i, score=float(sc), area=ar, aspect=aspect, thin=thin, nut=nutP,
                                          inside=inside, beyond=beyond, keep=keep))
                    if keep:
                        legsUnion |= m
    for q in legPieces:
        if q["keep"]:
            log(f"   LEGPIECE r {q['r']:.0f} angle {q['a']:.0f} m{q['i']} score {q['score']:.3f} area {q['area']:4d} aspect {q['aspect']:.1f} "
                f"thin {q['thin']:.1f} nut {q['nut']:.2f} inside {q['inside']:.2f} beyond {q['beyond']}")
    log(f"   LEGS {sum(q['keep'] for q in legPieces)} of {len(legPieces)} masks kept; union {int(legsUnion.sum())} px")
    out.update(legs_union=int(legsUnion.sum()), legs_cov=float((legsUnion & legsHalf).sum() / max(int(legsHalf.sum()), 1)),
               legs_beyond=int((legsUnion & (d > FO + 4)).sum()),
               legs_reach=float((d - FO)[legsUnion].max()) if legsUnion.any() else 0.0, t_legs=time.perf_counter() - t4)
    # ---- the legs from YOLO boxes (production's way of prompting MobileSAM): every box on the crop, its SAM mask,
    # what it looks like; leg-like masks (small, elongated or thin, not the nut, inside / crossing the outline) united
    t5 = time.perf_counter()
    ylegs = np.zeros_like(region); nBoxes = 0
    yolo = yoloModel()
    if yolo is not None:
        conf = float(arg("--yolo-conf", "0.05"))
        try:
            res = yolo.predict(crop, conf=conf, iou=0.7, verbose=False)
            boxes = res[0].boxes.xyxy.cpu().numpy() if len(res) and res[0].boxes is not None else np.zeros((0, 4))
            confs = res[0].boxes.conf.cpu().numpy() if len(boxes) else np.zeros(0)
        except Exception as e:
            log(f"   YOLO failed: {e}"); boxes, confs = np.zeros((0, 4)), np.zeros(0)
        nBoxes = len(boxes)
        with torch.inference_mode():
            for bi, (bx, bconf) in enumerate(zip(boxes, confs)):
                bxc, byc = (bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2
                bd = math.hypot(bxc - cc[0], byc - cc[1]); bang = math.degrees(math.atan2(byc - cc[1], bxc - cc[0])) % 360
                bdl = abs(((bang - (eye + 180)) + 180) % 360 - 180)
                if bd > Rw + 40:
                    log(f"   YOLOBOX {bi:2d} conf {bconf:.2f} box ({bx[0]:.0f},{bx[1]:.0f})-({bx[2]:.0f},{bx[3]:.0f}) centre r {bd:.0f} - far from the washer, skipped")
                    continue
                masks, scores, _ = pred.predict(box=bx, multimask_output=True)
                for i, (m, sc) in enumerate(zip(masks, scores)):
                    ar = int(m.sum())
                    if ar < 20:
                        continue
                    cnts, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
                    cnt = max(cnts, key=cv2.contourArea)
                    (_, _), (w_, h_), _ = cv2.minAreaRect(cnt)
                    aspect = max(w_, h_) / max(min(w_, h_), 1); thin = ar / max(cv2.arcLength(cnt, True), 1)
                    nutP = float(m[d < Rn_used - 2].mean()) if (d < Rn_used - 2).any() else 0.0
                    inside = float((m & (d <= FO)).sum() / ar); inLegsHalf = float((m & (dl <= 90)).sum() / ar)
                    beyond = int((m & (d > FO + 4)).sum()); hasEye = bool(m[int(ey_), int(ex_)])
                    kind = ("eye" if hasEye and ar < 800 else "washer/disc" if ar > 2500 else "nut" if nutP > 0.5 else
                            "leg" if (aspect >= 2.5 or thin <= 3.0) and nutP < 0.2 and inside >= 0.3 and inLegsHalf >= 0.5 else "other")
                    if kind == "leg":
                        ylegs |= m
                    log(f"   YOLOBOX {bi:2d} conf {bconf:.2f} box ({bx[0]:.0f},{bx[1]:.0f})-({bx[2]:.0f},{bx[3]:.0f}) r {bd:.0f} angle {bang:.0f} "
                        f"(from legs axis {bdl:.0f}) m{i} score {sc:.3f} area {ar:5d} aspect {aspect:.1f} thin {thin:.1f} nut {nutP:.2f} "
                        f"inside {inside:.2f} legs half {inLegsHalf:.2f} beyond {beyond:4d} eye {'Y' if hasEye else 'n'} -> {kind}")
        out.update(yolo_boxes=nBoxes, ylegs_px=int(ylegs.sum()),
                   ylegs_cov=float((ylegs & legsHalf).sum() / max(int(legsHalf.sum()), 1)),
                   ylegs_beyond=int((ylegs & (d > FO + 4)).sum()),
                   ylegs_reach=float((d - FO)[ylegs].max()) if ylegs.any() else 0.0, t_yolo=time.perf_counter() - t5)
    out["t_pin"] = time.perf_counter() - t3
    if pin is not None:
        out.update(pin_set=pin["name"], pin_area=pin["area"], pin_cov_legs=pin["inLegs"] / max(int(legsHalf.sum()), 1),
                   pin_beyond_legs=pin["beyondLegs"], pin_reach=pin["reach"], pin_score=pin["score"])
    # paint on the legs half (blue share B/(B+G+R) >= 0.55, B >= 20) - information only
    rgb = crop.astype(float); s = rgb.sum(axis=2) + 1
    blueShare = (rgb[..., 2] / s >= 0.55) & (rgb[..., 2] >= 20)
    out["blue_legs_px"] = int((blueShare & legsHalf).sum())
    return out, dict(base, M=M, fo=fo, eye=eye, Rn=Rn_used, occ=occ & legsHalf, stand=standMask,
                     pin=pin["mask"] if pin is not None else None, legs=legsUnion | ylegs)


def drawPicture(im, g):
    """Outlines only (no text): washer mask contour blue, fitted outline green, nut circle orange, eye direction yellow,
    covered pixels of the legs half red outline, parts standing out magenta outline."""
    x0, y0 = g["x0"], g["y0"]
    c = im[y0:y0 + 2 * CROP_HALF, x0:x0 + 2 * CROP_HALF].copy()
    cnts, _ = cv2.findContours(g["M"].astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    cv2.drawContours(c, cnts, -1, (255, 128, 0), 1)
    cc = g["cc"]; fo = g["fo"]
    pts = np.column_stack([cc[0] + fo * np.cos(T), cc[1] + fo * np.sin(T)]).round().astype(np.int32)
    cv2.polylines(c, [pts], True, (0, 255, 0), 1)
    if "Rn" in g:
        cv2.circle(c, (int(round(cc[0])), int(round(cc[1]))), int(round(g["Rn"])), (0, 165, 255), 1)
    if "eye" in g:
        th = math.radians(g["eye"])
        cv2.line(c, (int(cc[0]), int(cc[1])), (int(cc[0] + 75 * math.cos(th)), int(cc[1] + 75 * math.sin(th))), (0, 255, 255), 1)
        if g.get("legs") is not None:
            cs, _ = cv2.findContours(g["legs"].astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
            cv2.drawContours(c, cs, -1, (0, 255, 255), 1)
        if g.get("pin") is not None:
            cs, _ = cv2.findContours(g["pin"].astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
            cv2.drawContours(c, cs, -1, (255, 255, 255), 1)
        for m, col in ((g["occ"], (0, 0, 255)), (g["stand"], (255, 0, 255))):
            cs, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
            cv2.drawContours(c, cs, -1, col, 1)
    return cv2.resize(c, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)


def drawFull(im, g, half=175, scale=2):
    """The picture round the washer with everything drawn - outlines and markers only, no text (CLAUDE.md):
      blue        the hub's holes (small circles), their circle (thin) and its centre (cross)
      grey        the estimated centre = hole circle centre + the split pin offset (small cross)
      orange      the nut circle (MobileSAM) and its centre (cross) = the spindle centre
      light blue  the washer mask from MobileSAM (contour)
      green       the fitted washer outline (12 scallops) and its centre (cross)
      purple      the nut-edge circle used as the inner edge of the washer ring
      yellow      the eye direction (line) and the two edges of the legs half (thin lines); leg masks (contours)
      red         pixels of the legs half that are not bright washer (contours)
      magenta     legs standing out past the outline (contours)
      white       the split pin mask from the eye prompt (contour)"""
    out = im.copy()
    x0, y0 = g["x0"], g["y0"]
    ip = lambda x, y: (int(round(x)), int(round(y)))

    def cross(x, y, col, size=6, th=1):
        cv2.line(out, ip(x - size, y), ip(x + size, y), col, th); cv2.line(out, ip(x, y - size), ip(x, y + size), col, th)

    def contours(mask, col):
        if mask is None:
            return
        cs, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
        cs = [cnt + np.array([[x0, y0]]) for cnt in cs]
        cv2.drawContours(out, cs, -1, col, 1)

    if g.get("hpts") is not None:
        for px, py in g["hpts"]:
            cv2.circle(out, ip(px, py), 9, (255, 0, 0), 1)
    if g.get("hr"):
        cv2.circle(out, ip(g["hx"], g["hy"]), int(round(g["hr"])), (255, 0, 0), 1)
    cross(g["hx"], g["hy"], (255, 0, 0), 8, 2)
    cross(g["ex"], g["ey"], (160, 160, 160), 4, 1)
    contours(g.get("M"), (255, 200, 120))
    cc = g["cc"]; cx, cy = cc[0] + x0, cc[1] + y0
    if g.get("nut"):
        nx, ny, nr = g["nut"]
        cv2.circle(out, ip(nx, ny), int(round(nr)), (0, 165, 255), 1); cross(nx, ny, (0, 165, 255), 6, 2)
    if g.get("fo") is not None:
        fo = g["fo"]
        pts = np.column_stack([cx + fo * np.cos(T), cy + fo * np.sin(T)]).round().astype(np.int32)
        cv2.polylines(out, [pts], True, (0, 255, 0), 1)
    if g.get("prof"):
        cross(g["prof"][0], g["prof"][1], (0, 255, 0), 6, 2)
    if g.get("Rn"):
        cv2.circle(out, ip(cx, cy), int(round(g["Rn"])), (200, 0, 200), 1)
    if g.get("eye") is not None:
        th = math.radians(g["eye"])
        cv2.line(out, ip(cx, cy), ip(cx + 80 * math.cos(th), cy + 80 * math.sin(th)), (0, 255, 255), 2)
        for a in (g["eye"] + 90, g["eye"] - 90):
            t_ = math.radians(a)
            cv2.line(out, ip(cx, cy), ip(cx + 70 * math.cos(t_), cy + 70 * math.sin(t_)), (0, 200, 200), 1)
        contours(g.get("legs"), (0, 255, 255))
        contours(g.get("pin"), (255, 255, 255))
        contours(g.get("occ"), (0, 0, 255))
        contours(g.get("stand"), (255, 0, 255))
    X0, Y0 = int(round(cx)) - half, int(round(cy)) - half
    view = out[max(Y0, 0):Y0 + 2 * half, max(X0, 0):X0 + 2 * half]
    return cv2.resize(view, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)


# ---------------------------------------------------------------------------------------------------- shared
def picturesIn(folder):
    """[(path, label)] from OK\\ / NOT_OK\\ if there are any, else the folder itself (no label)."""
    folder = pathlib.Path(folder); items = []
    for sub, lab in (("OK", "OK"), ("NOT_OK", "NOT OK")):
        if (folder / sub).is_dir():
            items += [(p, lab) for p in sorted((folder / sub).iterdir()) if p.suffix.lower() in EXT]
    if not items:
        items = [(p, "") for p in sorted(folder.iterdir()) if p.suffix.lower() in EXT]
    return items


def setUp():
    """Hole finder (--holes), MobileSAM predictor; returns (pred, FindHubHoles, split pin class, method, source)."""
    from _hub_holes_option import applyHolesOption
    method = applyHolesOption()
    from camera.FindHubHoles import FindHubHoles
    from camera.CheckSplitPinAndWasher import CheckSplitPinAndWasher as SP
    import camera.CheckSplitPinAndWasher as SPM
    SPM.logBoth = lambda *a, **k: None
    pred, src = samPredictor()
    log(f"MODELS MobileSAM from {src}; holes method {method}")
    log(f"START R_WASHER {R_WASHER} R_NUT {R_NUT} offsets DOST {tuple(SP.HUB_OFFSET_DOST)} DOSTPLUS {tuple(SP.HUB_OFFSET_DOSTPLUS)}")
    return pred, FindHubHoles, SP, method, src


def processPicture(path, label, model, pred, FindHubHoles, SP):
    """One picture: holes, then analyse(); (row, geometry or None, the picture as checked or None). Logs everything."""
    im = cv2.imread(str(path))
    if im is None:
        log(f"PICTURE {pathlib.Path(path).name} unreadable")
        return dict(model=model, label=label, name=pathlib.Path(path).name, fail="unreadable"), None, None
    im = cv2.resize(im, (1280, 720))
    name = pathlib.Path(path).name
    offset = tuple(SP.HUB_OFFSET_DOSTPLUS if model == "DOSTPLUS" else SP.HUB_OFFSET_DOST)
    t0 = time.perf_counter()
    with contextlib.redirect_stdout(io.StringIO()):
        holes = FindHubHoles.findHoles(im, model, check="splitpin")   # refined as the split pin check (CHANGES 183)
    th = time.perf_counter() - t0
    log(f"PICTURE {model} {label or '-':6s} {name}")
    log(f"   HOLES {FindHubHoles.describe(holes)}  ({th:.2f} s)")
    if not holes.get("found"):
        log("   RESULT holes not found")
        return dict(model=model, label=label, name=name, fail="holes not found", t_holes=th), None, im
    t1 = time.perf_counter()
    r, g = analyse(im, model, pred, holes, offset)
    r.update(model=model, label=label, name=name, holes_cx=holes["cx"], holes_cy=holes["cy"], holes_r=holes["radius"],
             t_holes=th, t_rest=time.perf_counter() - t1)
    log("   RESULT " + ", ".join(f"{k} {v:.3f}" if isinstance(v, float) else f"{k} {v}" for k, v in r.items()
                             if k not in ("model", "label", "name")))
    return r, g, im


# ---------------------------------------------------------------------------------------------------- main
def main():
    sets = OrderedDict()
    for s in arg("--set", many=True) or []:
        k, v = s.split("=", 1); sets[k.strip().upper()] = pathlib.Path(v)
    if not sets:
        sets["DOST"] = HERE / "splitpin_images"
    hubs = {k.strip().upper(): v for k, v in (h.split("=", 1) for h in (arg("--hub", many=True) or []))}
    limit = int(arg("--limit", "0"))
    outDir = pathlib.Path(arg("--out", str(HERE / "washer_study_results")))
    outDir.mkdir(parents=True, exist_ok=True)

    from _hub_holes_option import setHubPicture
    pred, FindHubHoles, SP, method, src = setUp()
    log(f"SETS {dict((k, str(v)) for k, v in sets.items())}; hubs {hubs}; pictures to {outDir}")

    rows = []
    for model, folder in sets.items():
        log(setHubPicture(hubs.get(model)) + f" (model {model})")
        items = picturesIn(folder)
        if limit:
            items = items[:limit] if not any(l for _, l in items) else \
                [x for x in items if x[1] == "OK"][:limit] + [x for x in items if x[1] == "NOT OK"][:limit]
        log(f"SET {model}: {len(items)} pictures from {folder}")
        for p, lab in items:
            r, g, im = processPicture(p, lab, model, pred, FindHubHoles, SP)
            rows.append(r)
            if g is not None:
                cv2.imwrite(str(outDir / f"{model}_{lab.replace(' ', '_') or 'nolabel'}_{p.stem}.png"), drawFull(im, g))
    summary(rows)


def rng(v):
    v = [x for x in v if x is not None and np.isfinite(x)]
    if not v:
        return "-"
    return f"{min(v):.2f} .. {max(v):.2f} (median {np.median(v):.2f}, sd {np.std(v):.2f}, n {len(v)})"


def summary(rows):
    log("\n================ SUMMARY ================")
    fails = [r for r in rows if "fail" in r]
    for r in fails:
        log(f"FAIL {r['model']} {r['label']} {r['name']}: {r['fail']}")
    for model in sorted({r["model"] for r in rows}):
        for lab in ("OK", "NOT OK", ""):
            R = [r for r in rows if r["model"] == model and r["label"] == lab and "fail" not in r]
            if not R:
                continue
            log(f"\n{model} {lab or 'unlabelled'}: {len(R)} pictures")
            for k in ("nut_dx", "nut_dy", "nut_r", "nut_sd", "prof_vs_holes_dx", "prof_vs_holes_dy", "prof_r", "prof_a12", "prof_a6",
                      "prof_inliers", "wash_area", "wash_iou", "eye_angle", "eye_area", "legs_area", "cov_legs", "cov_mask_legs", "cov_eye",
                      "stand_legs", "reach_legs", "stand_eye", "blue_legs_px", "pin_area", "pin_cov_legs", "pin_beyond_legs", "pin_reach", "pin_score", "t_pin",
                      "legs_union", "legs_cov", "legs_beyond", "legs_reach", "t_legs",
                      "yolo_boxes", "ylegs_px", "ylegs_cov", "ylegs_beyond", "ylegs_reach", "t_yolo", "t_holes", "t_embed", "t_nut", "t_washer", "t_rest"):
                log(f"   {k:18s} {rng([r.get(k) for r in R])}")
            sets = {}
            for r in R:
                sets[r.get("wash_set")] = sets.get(r.get("wash_set"), 0) + 1
            log(f"   washer prompt set picked: {sets}")
            ps = {}
            for r in R:
                ps[r.get("pin_set")] = ps.get(r.get("pin_set"), 0) + 1
            log(f"   pin prompt set picked: {ps}")
    # spindle centre vs the hole circle, per model (all labels)
    for model in sorted({r["model"] for r in rows}):
        R = [r for r in rows if r["model"] == model and "nut_dx" in r]
        if R:
            log(f"\nSPINDLE {model}: nut centre - hole circle centre = ({np.mean([r['nut_dx'] for r in R]):.2f}, {np.mean([r['nut_dy'] for r in R]):.2f}) "
                f"sd ({np.std([r['nut_dx'] for r in R]):.2f}, {np.std([r['nut_dy'] for r in R]):.2f}), n {len(R)}")
        R = [r for r in rows if r["model"] == model and "prof_vs_holes_dx" in r]
        if R:
            log(f"WASHER {model}: washer profile centre - hole circle centre = ({np.mean([r['prof_vs_holes_dx'] for r in R]):.2f}, "
                f"{np.mean([r['prof_vs_holes_dy'] for r in R]):.2f}) sd ({np.std([r['prof_vs_holes_dx'] for r in R]):.2f}, "
                f"{np.std([r['prof_vs_holes_dy'] for r in R]):.2f}), n {len(R)}")
    # cut-offs: OK = cov_legs >= c and stand_legs <= s, per model and both together
    for scope in sorted({r["model"] for r in rows}) + ["ALL"]:
        R = [r for r in rows if r["label"] in ("OK", "NOT OK") and "cov_legs" in r and (scope == "ALL" or r["model"] == scope)]
        if not R or len({r["label"] for r in R}) < 2:
            continue
        best = None
        for c in np.arange(0.05, 0.70, 0.01):
            for s in sorted({0, 1, 2, 3, 5, 8, 10, 15, 20, 30, 40, 60, 80, 100, 150, 200, 300, 500, 10 ** 6} | {r["stand_legs"] for r in R}):
                e1 = [r for r in R if r["label"] == "OK" and not (r["cov_legs"] >= c and r["stand_legs"] <= s)]
                e2 = [r for r in R if r["label"] == "NOT OK" and (r["cov_legs"] >= c and r["stand_legs"] <= s)]
                key = (len(e1) + len(e2), len(e1))
                if best is None or key < best[0]:
                    best = (key, round(float(c), 2), s, e1, e2)
        (tot, _), c, s, e1, e2 = best
        log(f"\nCUT {scope}: OK = cov_legs >= {c} and stand_legs <= {s}: {len(R) - tot} of {len(R)} right; "
            f"OK called NOT OK {len(e1)}, NOT OK called OK {len(e2)}")
        for r in e1:
            log(f"   OK called NOT OK: {r['model']} {r['name']} cov_legs {r['cov_legs']:.3f} stand_legs {r['stand_legs']}")
        for r in e2:
            log(f"   NOT OK called OK: {r['model']} {r['name']} cov_legs {r['cov_legs']:.3f} stand_legs {r['stand_legs']}")
        for s2 in (0, 5, 20, 50):
            for c2 in (0.2, 0.3, 0.4):
                ok = sum(1 for r in R if r["label"] == "OK" and r["cov_legs"] >= c2 and r["stand_legs"] <= s2)
                nok = sum(1 for r in R if r["label"] == "NOT OK" and r["cov_legs"] >= c2 and r["stand_legs"] <= s2)
                log(f"   table cov >= {c2} stand <= {s2:3d}: OK passed {ok}/{sum(1 for r in R if r['label'] == 'OK')}, NOT OK passed {nok}/{sum(1 for r in R if r['label'] == 'NOT OK')}")
    for scope in sorted({r["model"] for r in rows}) + ["ALL"]:
        R = [r for r in rows if r["label"] in ("OK", "NOT OK") and "pin_cov_legs" in r and (scope == "ALL" or r["model"] == scope)]
        if not R or len({r["label"] for r in R}) < 2:
            continue
        best = None
        for c in np.arange(0.02, 0.60, 0.01):
            for b in sorted({0, 2, 5, 10, 20, 40, 80, 150, 300, 10 ** 6} | {r["pin_beyond_legs"] for r in R}):
                e1 = [r for r in R if r["label"] == "OK" and not (r["pin_cov_legs"] >= c and r["pin_beyond_legs"] <= b)]
                e2 = [r for r in R if r["label"] == "NOT OK" and (r["pin_cov_legs"] >= c and r["pin_beyond_legs"] <= b)]
                key = (len(e1) + len(e2), len(e1))
                if best is None or key < best[0]:
                    best = (key, round(float(c), 2), b, e1, e2)
        (tot, _), c, b, e1, e2 = best
        log(f"\nPINCUT {scope}: OK = pin_cov_legs >= {c} and pin_beyond_legs <= {b}: {len(R) - tot} of {len(R)} right; "
            f"OK called NOT OK {len(e1)}, NOT OK called OK {len(e2)}")
        for r in e1 + e2:
            log(f"   {'OK called NOT OK' if r in e1 else 'NOT OK called OK'}: {r['model']} {r['name']} pin_cov_legs {r['pin_cov_legs']:.3f} "
                f"pin_beyond_legs {r['pin_beyond_legs']} pin_reach {r['pin_reach']:.1f}")
    for covK, beyK, tag in (("legs_cov", "legs_beyond", "LEGSCUT"), ("ylegs_cov", "ylegs_beyond", "YOLOLEGSCUT")):
      for scope in sorted({r["model"] for r in rows}) + ["ALL"]:
        R = [r for r in rows if r["label"] in ("OK", "NOT OK") and covK in r and (scope == "ALL" or r["model"] == scope)]
        if not R or len({r["label"] for r in R}) < 2:
            continue
        best = None
        for c in np.arange(0.0, 0.60, 0.01):
            for b in sorted({0, 2, 5, 10, 20, 40, 80, 150, 300, 10 ** 6} | {r[beyK] for r in R}):
                e1 = [r for r in R if r["label"] == "OK" and not (r[covK] >= c and r[beyK] <= b)]
                e2 = [r for r in R if r["label"] == "NOT OK" and (r[covK] >= c and r[beyK] <= b)]
                key = (len(e1) + len(e2), len(e1))
                if best is None or key < best[0]:
                    best = (key, round(float(c), 2), b, e1, e2)
        (tot, _), c, b, e1, e2 = best
        log(f"\n{tag} {scope}: OK = {covK} >= {c} and {beyK} <= {b}: {len(R) - tot} of {len(R)} right; "
            f"OK called NOT OK {len(e1)}, NOT OK called OK {len(e2)}")
        for r in e1 + e2:
            log(f"   {'OK called NOT OK' if r in e1 else 'NOT OK called OK'}: {r['model']} {r['name']} {covK} {r[covK]:.3f} {beyK} {r[beyK]}")
    log("\nDONE")


if __name__ == "__main__":
    main()
