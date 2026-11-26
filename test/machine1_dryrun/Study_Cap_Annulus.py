"""
Study_Cap_Annulus - a simpler cap seating measure (Uddi, 2026-11-27), study only: production is not changed.

Per cap picture:
  1. Steps 1 - 2 of CheckCap (production helpers, round vision.cap.center): R-B in the brass and silver circles,
     brightness within r 25 -> which cap (brass / silver / none) and whether it is the model's cap. The brightening
     step (needs the hub / top bearing / nut pictures of the same part) is not run.
  2. Hub centre: the hub's holes in this picture by the production routine (FindHubHoles.findHoles, check "cap") ->
     centre H and radius R of the circle through the hole centres.
  3. Cap mask: MobileSAM with 24 positive points round H (r 30 / 60 / 90 px x angles 0, 45, ..., 315), one mask
     (multimask off, as for several points); the part of the mask connected to H is kept. Its outline (largest outer
     contour) and the two-stage ellipse fitted to it (CapMaskFinder.fitCapEllipse, the production cap ellipse fit).
  4. Annulus = between the cap outline and the hole circle. Diameters through H at 15, 30, ..., 180 deg (12); on each,
     the length inside the annulus on the + side and on the - side (R - distance from H to the cap outline along
     that ray, 0 if the outline is outside the circle). Ratio = longer / shorter (shorter floored at --floor px).
     Score = sum of the 12 ratios: 12.0 for a cap centred on the hole circle.
     Computed twice: with the fitted ellipse as the outline ("ellipse") and with the mask's own outline ("mask").

Run on one folder at a time (all pictures in it, sub-folders not searched):
    python test\\machine2_dryrun\\Study_Cap_Annulus.py <folder> [--model DOST|DOSTPLUS|name] [--save] [--gamma 1.0]
                                                          [--floor 1.0] [--sam <mobile_sam.pt>] [--holes morphology]
  --model name : DOSTPLUS if the file name contains it, else DOST (default DOST).
  --save       : marked pictures (outlines only, no values) to <folder>/cap_annulus_results/.
  --sam        : MobileSAM from this file (else through ModelManager, as production).
  --holes morphology : the old dark-blob hole finder (where the YOLO weights are missing - the sandbox).
Writes <folder>/cap_annulus_results/annulus_<time>.csv (per picture and the 12 ratios of each outline) and prints a
line per picture and a summary. Run it on the OK and on the NOT OK folder, then compare the two summaries / csv files.
"""
import argparse
import math
import os
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("COSTHETA_PLC_VENDOR", "siemens" if HERE.name.startswith("machine2") else "allenbradley")
MOBILESAM_DIR = ROOT / "models" / "MobileSAM"
if MOBILESAM_DIR.exists() and str(MOBILESAM_DIR) not in sys.path:
    sys.path.append(str(MOBILESAM_DIR))

ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
ap.add_argument("folder")
ap.add_argument("--model", default="DOST", choices=["DOST", "DOSTPLUS", "name"])
ap.add_argument("--save", action="store_true")
ap.add_argument("--gamma", type=float, default=1.0, help="gamma applied to the picture given to MobileSAM (1 = none)")
ap.add_argument("--floor", type=float, default=1.0, help="px; the shorter length is at least this in a ratio")
ap.add_argument("--sam", default=None)
ap.add_argument("--holes", default=None, choices=["morphology"])
args = ap.parse_args()

import cv2
import numpy as np

import camera.CheckCap as CheckCapModule
from camera.CheckCap import CheckCap
from camera import CapMaskFinder
from camera.FindHubHoles import FindHubHoles

CheckCapModule.logBoth = lambda *a, **k: None          # the study prints its own lines
if args.holes == "morphology":
    FindHubHoles.METHOD = "morphology"

PROMPT_RADII = (30, 60, 90)
PROMPT_ANGLES = tuple(range(0, 360, 45))
DIAMETER_ANGLES = tuple(range(15, 181, 15))            # 15, 30, ..., 180: 12 diameters
EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp")

if args.sam:
    _sam = {}

    def _loadSam():
        if "m" not in _sam:
            import torch
            from mobile_sam import sam_model_registry
            m = sam_model_registry["vit_t"](checkpoint=args.sam)
            m.to("cuda" if torch.cuda.is_available() else "cpu").eval()
            _sam["m"] = m
        return _sam["m"]

    CheckCap.samModelProvider = staticmethod(_loadSam)
    FindHubHoles.samModelProvider = staticmethod(_loadSam)


def modelOf(path: pathlib.Path) -> str:
    if args.model != "name":
        return args.model
    return "DOSTPLUS" if "DOSTPLUS" in path.name.upper() else "DOST"


def steps12(img: np.ndarray, model: str):
    """CheckCap steps 1 - 2 with the production helpers: (cap found, right cap, R-B brass, R-B silver, brightness)."""
    rbBrass = CheckCap._compute_rb_difference(img, CheckCap._get_circular_mask(img.shape, CheckCap.RADIUS_DOSTPLUS))
    rbSilver = CheckCap._compute_rb_difference(img, CheckCap._get_circular_mask(img.shape, CheckCap.RADIUS_DOST))
    bright = float(np.mean(img[CheckCap._get_circular_mask(img.shape, CheckCap.SILVER_BRIGHTNESS_RADIUS) > 0]))
    if rbBrass > CheckCap.RB_THRESHOLD_DOSTPLUS:
        found = "DOSTPLUS"
    elif rbSilver < CheckCap.RB_THRESHOLD_DOST and bright >= CheckCap.SILVER_BRIGHTNESS_MIN:
        found = "DOST"
    else:
        found = None
    return found, found == model, rbBrass, rbSilver, bright


def promptPoints(hx: float, hy: float) -> np.ndarray:
    return np.array([[hx + r * math.cos(math.radians(a)), hy + r * math.sin(math.radians(a))]
                     for r in PROMPT_RADII for a in PROMPT_ANGLES], dtype=np.float32)


def capMask(img: np.ndarray, hx: float, hy: float):
    """(mask connected to H, MobileSAM score) or (None, reason)."""
    import torch
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    if args.gamma != 1.0:
        rgb = CapMaskFinder.applyGamma(rgb, args.gamma)
    predictor = CheckCap._sam_predictor()
    predictor.set_image(rgb)
    pts = promptPoints(hx, hy)
    with torch.inference_mode():
        masks, scores, _ = predictor.predict(point_coords=pts, point_labels=np.ones(len(pts)), multimask_output=False)
    mask = np.asarray(masks[0]).astype(np.uint8)
    n, labels = cv2.connectedComponents(mask)
    ix, iy = int(round(hx)), int(round(hy))
    if n < 2 or not (0 <= iy < mask.shape[0] and 0 <= ix < mask.shape[1]) or labels[iy, ix] == 0:
        return None, "mask does not cover the hub centre"
    return labels == labels[iy, ix], float(scores[0])


def rayToEllipse(hx, hy, phi, e):
    """Distance from H along direction phi (rad) to the ellipse e = (cx, cy, a, b, angle deg); None if no crossing."""
    cx, cy, a, b, ang = e
    c, s = math.cos(math.radians(ang)), math.sin(math.radians(ang))
    px, py = hx - cx, hy - cy
    px, py = c * px + s * py, -s * px + c * py
    vx, vy = math.cos(phi), math.sin(phi)
    vx, vy = c * vx + s * vy, -s * vx + c * vy
    A = vx * vx / (a * a) + vy * vy / (b * b)
    B = 2 * (px * vx / (a * a) + py * vy / (b * b))
    C = px * px / (a * a) + py * py / (b * b) - 1
    disc = B * B - 4 * A * C
    if disc < 0:
        return None
    t = (-B + math.sqrt(disc)) / (2 * A)
    return t if t > 0 else None


def rayToMask(hx, hy, phi, mask, limit):
    """Distance from H along phi to where the mask ends (first step outside it, 0.25 px steps), up to limit."""
    h, w = mask.shape
    t = 0.0
    while t <= limit:
        x, y = int(round(hx + t * math.cos(phi))), int(round(hy + t * math.sin(phi)))
        if not (0 <= x < w and 0 <= y < h) or not mask[y, x]:
            return t
        t += 0.25
    return limit


def score(dist, R):
    """dist(phi) -> distance to the outline. Returns (sum of ratios, [ratio per diameter], [(L+, L-) per diameter])."""
    ratios, lengths = [], []
    for deg in DIAMETER_ANGLES:
        sides = []
        for phi in (math.radians(deg), math.radians(deg + 180)):
            d = dist(phi)
            sides.append(0.0 if d is None else max(R - d, 0.0))
        longer, shorter = max(sides), min(sides)
        ratios.append(longer / max(shorter, args.floor))
        lengths.append(tuple(sides))
    return sum(ratios), ratios, lengths


def marked(img, hx, hy, R, pts, mask, e):
    """Outlines only: hole circle and hub centre (blue), prompt points (cyan), mask outline (green), ellipse (yellow),
    diameters (thin grey)."""
    out = img.copy()
    for deg in DIAMETER_ANGLES:
        phi = math.radians(deg)
        p1 = (int(round(hx + R * math.cos(phi))), int(round(hy + R * math.sin(phi))))
        p2 = (int(round(hx - R * math.cos(phi))), int(round(hy - R * math.sin(phi))))
        cv2.line(out, p1, p2, (160, 160, 160), 1)
    cv2.circle(out, (int(round(hx)), int(round(hy))), int(round(R)), (255, 128, 0), 1)
    cv2.drawMarker(out, (int(round(hx)), int(round(hy))), (255, 128, 0), cv2.MARKER_CROSS, 16, 2)
    for x, y in pts:
        cv2.circle(out, (int(round(x)), int(round(y))), 2, (255, 255, 0), -1)
    if mask is not None:
        cs, _ = cv2.findContours(mask.astype(np.uint8) * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        cv2.drawContours(out, cs, -1, (0, 255, 0), 1)
    if e is not None:
        cv2.drawContours(out, [CapMaskFinder.ellipseContour(e)], -1, (0, 255, 255), 2)
    return out


def main():
    folder = pathlib.Path(args.folder)
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONS)
    if not files:
        sys.exit(f"No pictures in {folder}")
    resultsDir = folder / "cap_annulus_results"
    resultsDir.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    csvPath = resultsDir / f"annulus_{stamp}.csv"
    header = ["file", "model", "cap_found", "right_cap", "rb_brass", "rb_silver", "brightness", "holes_found", "hub_x",
              "hub_y", "hole_r", "sam_score", "mask_area", "ell_cx", "ell_cy", "ell_a", "ell_b", "ell_minus_hub_dx",
              "ell_minus_hub_dy", "score_ellipse", "score_mask", "max_ratio_ellipse", "max_ratio_mask", "seconds",
              "note"] + [f"ell_{d}" for d in DIAMETER_ANGLES] + [f"mask_{d}" for d in DIAMETER_ANGLES]
    rows = []
    print(f"Study_Cap_Annulus on {len(files)} pictures in {folder} (model {args.model}, hole finder "
          f"{FindHubHoles.METHOD}, gamma {args.gamma}, floor {args.floor} px)")
    print(f"{'file':<36} {'cap':<9} {'R-B':>6} {'bright':>6} {'hub':>15} {'R':>6} {'ell-hub':>12} "
          f"{'ellipse':>8} {'mask':>8}  note")
    for p in files:
        t0 = time.perf_counter()
        img = cv2.imread(str(p))
        model = modelOf(p)
        row = dict.fromkeys(header, "")
        row.update(file=p.name, model=model)
        if img is None:
            row["note"] = "could not read"
            rows.append(row); print(f"{p.name:<36} could not read"); continue
        found, right, rbB, rbS, bright = steps12(img, model)
        row.update(cap_found=found or "none", right_cap=right, rb_brass=f"{rbB:.1f}", rb_silver=f"{rbS:.1f}",
                   brightness=f"{bright:.1f}")
        notes = [] if right else [f"steps 1-2 NOT OK (cap found {found or 'none'})"]
        holes = FindHubHoles.findHoles(img, model, None, check="cap")
        row["holes_found"] = bool(holes.get("found"))
        mask = e = None
        pts = np.zeros((0, 2))
        if not holes.get("found"):
            notes.append(f"hub holes not found ({holes.get('reason')})")
        else:
            hx, hy, R = float(holes["cx"]), float(holes["cy"]), float(holes["radius"])
            row.update(hub_x=f"{hx:.1f}", hub_y=f"{hy:.1f}", hole_r=f"{R:.1f}")
            pts = promptPoints(hx, hy)
            mask, samScore = capMask(img, hx, hy)
            if mask is None:
                notes.append(samScore)
            else:
                row.update(sam_score=f"{samScore:.3f}", mask_area=int(mask.sum()))
                fit = CapMaskFinder.fitCapEllipse(mask, float(CapMaskFinder.ELLIPSE_TOLERANCE),
                                                  int(CapMaskFinder.ELLIPSE_TRIES))
                if fit is None:
                    notes.append("no ellipse (outline too short)")
                else:
                    e = fit["e2"]
                    row.update(ell_cx=f"{e[0]:.1f}", ell_cy=f"{e[1]:.1f}", ell_a=f"{e[2]:.1f}", ell_b=f"{e[3]:.1f}",
                               ell_minus_hub_dx=f"{e[0] - hx:+.1f}", ell_minus_hub_dy=f"{e[1] - hy:+.1f}")
                    sE, rE, _ = score(lambda phi: rayToEllipse(hx, hy, phi, e), R)
                    row.update(score_ellipse=f"{sE:.3f}", max_ratio_ellipse=f"{max(rE):.3f}",
                               **{f"ell_{d}": f"{r:.3f}" for d, r in zip(DIAMETER_ANGLES, rE)})
                sM, rM, _ = score(lambda phi: rayToMask(hx, hy, phi, mask, R + 5), R)
                row.update(score_mask=f"{sM:.3f}", max_ratio_mask=f"{max(rM):.3f}",
                           **{f"mask_{d}": f"{r:.3f}" for d, r in zip(DIAMETER_ANGLES, rM)})
            if args.save:
                cv2.imwrite(str(resultsDir / f"{p.stem}_annulus.jpg"), marked(img, hx, hy, R, pts, mask, e))
        row["seconds"] = f"{time.perf_counter() - t0:.2f}"
        row["note"] = "; ".join(notes)
        rows.append(row)
        hub = f"({row['hub_x']}, {row['hub_y']})" if row["hub_x"] else "-"
        off = f"{row['ell_minus_hub_dx']} / {row['ell_minus_hub_dy']}" if row["ell_cx"] else "-"
        print(f"{p.name:<36} {row['cap_found']:<9} {rbS:6.1f} {bright:6.1f} {hub:>15} {row['hole_r'] or '-':>6} "
              f"{off:>12} {row['score_ellipse'] or '-':>8} {row['score_mask'] or '-':>8}  "
              f"{row['seconds']} s{'  ' + row['note'] if row['note'] else ''}")
    with open(csvPath, "w", newline="") as fh:
        import csv
        w = csv.DictWriter(fh, fieldnames=header)
        w.writeheader()
        w.writerows(rows)
    print("\nSummary (pictures with a score):")
    for key in ("score_ellipse", "score_mask", "max_ratio_ellipse", "max_ratio_mask"):
        v = np.array([float(r[key]) for r in rows if r[key] != ""])
        if len(v):
            print(f"  {key:<18} n {len(v):3d}  min {v.min():7.3f}  p10 {np.percentile(v, 10):7.3f}  median "
                  f"{np.median(v):7.3f}  p90 {np.percentile(v, 90):7.3f}  max {v.max():7.3f}")
    bad = [r["file"] for r in rows if r["note"]]
    print(f"  with a note (steps 1-2 NOT OK / no holes / no mask): {len(bad)}")
    print(f"written: {csvPath}")


if __name__ == "__main__":
    main()
