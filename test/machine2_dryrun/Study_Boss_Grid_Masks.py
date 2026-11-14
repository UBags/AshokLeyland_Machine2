"""
Study_Boss_Grid_Masks v1.0 - finding the 2 machined bosses of the knuckle when the camera may have moved a little.

For each boss a 4 x 4 grid of points is laid over the place where the boss is expected, with the spacing of the 4-point
prompt used so far (2 x 2 points on the diagonals 26 px from the centre = 36.8 px apart), so the grid covers about
+-55 px round the expected centre. Every pair of neighbouring points, across or down (12 + 12 = 24 pairs), is given to
MobileSAM as a 2-point prompt; each prompt gives 3 masks (small to large): 72 masks per boss, all from one image
embedding and one batched decoder call per boss.

Which mask is the boss - the boss's own signature, measured on the 13 Nov pictures: going out from its centre, a dark
bore with the fixture's mounting pin in it (grey 5-48 out to about 14 px), then the bright machined ring (129-140 at
20-32 px); on the empty fixture the seat and pin are there but no bright ring. For every mask:
  - its shape: area, centroid, perimeter, equivalent diameter, circularity, enclosing circle and fill, ellipse, holes;
  - DARK CORE: inside the mask's outline (holes filled), the dark blob nearest the mask's centre (pixels darker than
    min(70, ring grey - 40)): its centroid ("pin centre"), area, equivalent radius, mean grey;
  - RING: mean grey of the mask's pixels outside the dark core, and the contrast ring - core;
  - concentricity: distance from the dark core's centroid to the mask's centroid;
  - duplicates: masks with IoU >= 0.85 form one group; "votes" = how many of the 72 masks are in the group.
A mask is a boss CANDIDATE when (limits are arguments of judge(), printed in the summary):
  radius (enclosing circle) 30-60, circularity >= 0.75, fill >= 0.70, ring grey >= 80, contrast >= 40,
  dark core radius 6-22, concentricity <= 8.
SCORE = votes x circularity x fill x min(contrast, 120) / 120 x (1 - concentricity / 20). The BEST is the candidate
group with the highest score; none -> no boss in that region (empty fixture, or the camera moved more than the grid).

Output (default folder <pictures>/boss_grid_masks/):
  boss_grid_masks.csv      every mask: picture, boss, prompt pair (row/col of both points, across/down), mask index
                           0-2, SAM score, the measures above, group, votes, candidate (and why not), score, best
  boss_grid_summary.csv    per picture and boss: the best mask - centroid, pin centre, radius, area, ring, core,
                           contrast, votes, score, number of candidates; and the type from the two bosses
                           (area x1000 / distance^2: DOST < 34.5 <= DOSTPLUS)
  boss_grid_masks.xlsx     both tables as two sheets (needs xlsxwriter, in requirements.txt; else CSV only)
  <picture>_bossgrid.png   with --pictures: grid points (white), the best mask (green) and its pin centre (red),
                           shapes only

Run:
  python test/machine2_dryrun/Study_Boss_Grid_Masks.py [--folder <pictures>] [--out <folder>] [--pictures]
          [--sam models/MobileSAM/weights/mobile_sam.pt] [--centre fixed|spindle]
  --centre fixed (default): the grids sit at the bosses' usual places in the picture (444,198) and (842,198) - nothing
  else in the picture is needed. --centre spindle: the spindle is found first (one point prompt) and the grids are
  placed from it.
"""

import argparse
import csv
import math
import os
import sys
import time
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import Study_Knuckle_Segmentation as KS  # noqa: E402  (MobileSAM loading, picture loading, mask measures)

import torch  # noqa: E402

SPACING = 2 * 26 / math.sqrt(2)        # 36.77 px: the spacing of the 2 x 2 prompt used so far
GRID_N = 4
BOSS_FIXED = {"BOSS_L": (KS.REF_SPINDLE[0] + KS.BOSS_CENTRES["BOSS_L"][0], KS.REF_SPINDLE[1] + KS.BOSS_CENTRES["BOSS_L"][1]),
              "BOSS_R": (KS.REF_SPINDLE[0] + KS.BOSS_CENTRES["BOSS_R"][0], KS.REF_SPINDLE[1] + KS.BOSS_CENTRES["BOSS_R"][1])}
DEDUPE_IOU = 0.85
LIMITS = dict(radius=(30.0, 60.0), circularity_min=0.75, fill_min=0.70, ring_min=80.0, contrast_min=40.0,
              core_radius=(6.0, 22.0), concentricity_max=8.0)


def grid_points(cx: float, cy: float) -> List[Tuple[int, int, float, float]]:
    """(row, col, x, y) of the 4 x 4 grid centred on cx, cy."""
    offs = [(i - (GRID_N - 1) / 2.0) * SPACING for i in range(GRID_N)]
    return [(r, c, cx + offs[c], cy + offs[r]) for r in range(GRID_N) for c in range(GRID_N)]


def grid_pairs() -> List[Tuple[Tuple[int, int], Tuple[int, int], str]]:
    """The 24 pairs of neighbouring grid points: ((r, c), (r, c+1), 'across') and ((r, c), (r+1, c), 'down')."""
    pairs = []
    for r in range(GRID_N):
        for c in range(GRID_N - 1):
            pairs.append(((r, c), (r, c + 1), "across"))
    for r in range(GRID_N - 1):
        for c in range(GRID_N):
            pairs.append(((r, c), (r + 1, c), "down"))
    return pairs


def dark_core(mask: np.ndarray, grey: np.ndarray, cx: float, cy: float) -> Dict:
    """The dark blob (bore + pin) inside the mask's outline nearest its centre, and the ring round it."""
    filled = KS.fill_holes(mask)
    ring_vals = grey[mask]
    ring0 = float(np.median(ring_vals)) if ring_vals.size else 0.0
    thr = min(70.0, ring0 - 40.0)
    out = dict(core_x=float("nan"), core_y=float("nan"), core_area=0, core_radius=0.0, core_grey=float("nan"),
               ring_grey=float(ring_vals.mean()) if ring_vals.size else 0.0, contrast=0.0,
               concentricity=float("nan"), core_threshold=round(thr, 1))
    if thr <= 0:
        return out
    dark = (filled & (grey < thr)).astype(np.uint8)
    n, lab, stats, cents = cv2.connectedComponentsWithStats(dark, connectivity=8)
    if n <= 1:
        return out
    best, best_d = None, 1e9
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < 20:
            continue
        d = math.hypot(cents[i][0] - cx, cents[i][1] - cy)
        if d < best_d:
            best, best_d = i, d
    if best is None:
        return out
    core = lab == best
    ring = mask & ~core
    out.update(core_x=float(cents[best][0]), core_y=float(cents[best][1]), core_area=int(core.sum()),
               core_radius=math.sqrt(core.sum() / math.pi), core_grey=float(grey[core].mean()),
               concentricity=best_d)
    if ring.any():
        out["ring_grey"] = float(grey[ring].mean())
    out["contrast"] = out["ring_grey"] - out["core_grey"]
    return out


def judge(m: Dict, limits: Dict = LIMITS) -> Tuple[bool, str]:
    why = []
    if not (limits["radius"][0] <= m["radius"] <= limits["radius"][1]):
        why.append("radius")
    if m["circularity"] < limits["circularity_min"]:
        why.append("circularity")
    if m["round_fill"] < limits["fill_min"]:
        why.append("fill")
    if m["ring_grey"] < limits["ring_min"]:
        why.append("ring dark")
    if not (m["contrast"] >= limits["contrast_min"]):
        why.append("contrast")
    if not (limits["core_radius"][0] <= m["core_radius"] <= limits["core_radius"][1]):
        why.append("core size")
    if not (m["concentricity"] <= limits["concentricity_max"]):
        why.append("off-centre core")
    return (not why), ", ".join(why)


def bbox_of(mask: np.ndarray, pad: int = 2) -> Tuple[int, int, int, int]:
    ys = np.flatnonzero(mask.any(axis=1))
    xs = np.flatnonzero(mask.any(axis=0))
    return (max(int(xs[0]) - pad, 0), max(int(ys[0]) - pad, 0),
            min(int(xs[-1]) + pad + 1, mask.shape[1]), min(int(ys[-1]) + pad + 1, mask.shape[0]))


def measure(mask: np.ndarray, grey: np.ndarray, idx: int, score: float) -> Dict:
    """KS.describe + dark_core on the mask's own box (same values as on the whole picture, much faster)."""
    x0, y0, x1, y1 = bbox_of(mask)
    sub, g = mask[y0:y1, x0:x1], grey[y0:y1, x0:x1]
    d = KS.describe(sub, g, idx, score)
    d.update(dark_core(d["mask"], g, d["cx"], d["cy"]))
    for k in ("cx", "circle_x", "ellipse_x", "core_x"):
        d[k] += x0
    for k in ("cy", "circle_y", "ellipse_y", "core_y"):
        d[k] += y0
    bx, by, bw, bh = d["bbox"]
    d["bbox"] = (bx + x0, by + y0, bw, bh)
    d["box"] = (x0, y0, x1, y1)
    return d


def iou(a: Dict, b: Dict) -> float:
    """IoU of two measured masks, on the union of their boxes (0 when the boxes do not meet)."""
    ax0, ay0, ax1, ay1 = a["box"]
    bx0, by0, bx1, by1 = b["box"]
    if ax1 <= bx0 or bx1 <= ax0 or ay1 <= by0 or by1 <= ay0:
        return 0.0
    x0, y0, x1, y1 = min(ax0, bx0), min(ay0, by0), max(ax1, bx1), max(ay1, by1)
    ma = np.zeros((y1 - y0, x1 - x0), bool)
    mb = np.zeros_like(ma)
    ma[ay0 - y0:ay1 - y0, ax0 - x0:ax1 - x0] = a["mask"]
    mb[by0 - y0:by1 - y0, bx0 - x0:bx1 - x0] = b["mask"]
    inter = np.logical_and(ma, mb).sum()
    return float(inter) / float(np.logical_or(ma, mb).sum() or 1)


class BossGridStudy:
    def __init__(self, sam_path: str):
        self.study = KS.KnuckleMaskStudy(sam_path)
        self.predictor = self.study.predictor
        self.device = self.study.device

    def spindle(self, image_rgb: np.ndarray, grey: np.ndarray) -> Optional[Tuple[float, float]]:
        ex, ey = KS.RULES["spindle_expected"]
        ms, sc, _ = self.predictor.predict(point_coords=np.array([[ex, ey]]), point_labels=np.array([1]),
                                           multimask_output=True)
        for m in ms:
            if m.any():
                d = KS.describe(m, grey, 0)
                if KS.SPINDLE_R_OK[0] <= d["radius"] <= KS.SPINDLE_R_OK[1] and d["round_fill"] >= 0.75:
                    return d["circle_x"], d["circle_y"]
        return None

    def run(self, image_rgb: np.ndarray, centre_mode: str = "fixed") -> Tuple[List[Dict], Dict, Dict]:
        grey = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
        t0 = time.time()
        with torch.no_grad():
            self.predictor.set_image(image_rgb)
            t_embed = time.time() - t0
            centres = dict(BOSS_FIXED)
            if centre_mode == "spindle":
                sp = self.spindle(image_rgb, grey)
                if sp:
                    centres = {k: (sp[0] + KS.BOSS_CENTRES[k][0], sp[1] + KS.BOSS_CENTRES[k][1]) for k in centres}
            rows: List[Dict] = []
            grids = {}
            for boss, (cx, cy) in centres.items():
                pts = {(r, c): (x, y) for r, c, x, y in grid_points(cx, cy)}
                grids[boss] = list(pts.values())
                pairs = grid_pairs()
                coords = np.array([[pts[a], pts[b]] for a, b, _ in pairs], dtype=np.float32)
                tc = torch.as_tensor(self.predictor.transform.apply_coords(coords, image_rgb.shape[:2]),
                                     dtype=torch.float, device=self.device)
                tl = torch.ones(tc.shape[:2], dtype=torch.int, device=self.device)
                masks_t, iou_t, _ = self.predictor.predict_torch(tc, tl, multimask_output=True)
                masks = masks_t.cpu().numpy()
                scores = iou_t.cpu().numpy()
                for p, (a, b, orient) in enumerate(pairs):
                    for k in range(masks.shape[1]):
                        m = masks[p, k]
                        if not m.any():
                            continue
                        d = measure(m, grey, len(rows), float(scores[p, k]))
                        d.update(boss=boss, pair=f"r{a[0]}c{a[1]}-r{b[0]}c{b[1]}", orient=orient, mask_index=k,
                                 grid_cx=cx, grid_cy=cy)
                        ok, why = judge(d)
                        d.update(candidate=ok, why_not=why)
                        rows.append(d)
        secs = time.time() - t0
        # duplicates -> groups and votes, per boss
        summary = {}
        for boss in centres:
            bm = [r for r in rows if r["boss"] == boss]
            groups: List[List[Dict]] = []
            for r in sorted(bm, key=lambda r: -r["iou"]):
                for g in groups:
                    if iou(g[0], r) >= DEDUPE_IOU:
                        g.append(r)
                        break
                else:
                    groups.append([r])
            for gi, g in enumerate(groups):
                for r in g:
                    r["group"] = gi
                    r["votes"] = len(g)
            for r in bm:
                r["score"] = (r["votes"] * r["circularity"] * r["round_fill"] * min(max(r["contrast"], 0.0), 120.0)
                              / 120.0 * max(0.0, 1.0 - (r["concentricity"] if r["concentricity"] == r["concentricity"]
                                                         else 20.0) / 20.0)) if r["candidate"] else 0.0
                r["best"] = False
            cands = [r for r in bm if r["candidate"]]
            if cands:
                best = max(cands, key=lambda r: (r["score"], r["iou"]))
                best["best"] = True
                summary[boss] = dict(best=best, candidates=len(cands),
                                     candidate_groups=len({r["group"] for r in cands}), masks=len(bm))
            else:
                summary[boss] = dict(best=None, candidates=0, candidate_groups=0, masks=len(bm))
        bl, br = summary["BOSS_L"]["best"], summary["BOSS_R"]["best"]
        info = dict(time=secs, embed=t_embed, grids=grids, centres=centres)
        if bl and br:
            dist = math.hypot(br["core_x"] - bl["core_x"], br["core_y"] - bl["core_y"])
            ratio = (bl["area"] + br["area"]) / 2.0 * 1000.0 / dist ** 2
            info.update(pin_distance=dist, angle=math.degrees(math.atan2(br["core_y"] - bl["core_y"],
                                                                          br["core_x"] - bl["core_x"])),
                        area_ratio=ratio, type="DOSTPLUS" if ratio >= KS.TYPE_AREA_RATIO else "DOST",
                        pin_mid=((bl["core_x"] + br["core_x"]) / 2.0, (bl["core_y"] + br["core_y"]) / 2.0))
        return rows, summary, info


MASK_COLS = ["picture", "boss", "pair", "orient", "mask_index", "iou", "area", "cx", "cy", "perimeter", "eq_diameter",
             "circularity", "circle_x", "circle_y", "radius", "round_fill", "ellipse_x", "ellipse_y", "ellipse_major",
             "ellipse_minor", "ellipse_ratio", "ellipse_angle", "holes", "holes_area", "rect_fill", "grey",
             "core_x", "core_y", "core_area", "core_radius", "core_grey", "core_threshold", "ring_grey", "contrast",
             "concentricity", "group", "votes", "candidate", "why_not", "score", "best", "grid_cx", "grid_cy", "bbox"]
SUM_COLS = ["picture", "boss", "found", "pin_x", "pin_y", "cx", "cy", "radius", "area", "eq_diameter", "circularity",
            "round_fill", "ring_grey", "core_grey", "contrast", "core_radius", "concentricity", "votes", "score",
            "candidates", "candidate_groups", "masks", "pair", "mask_index", "type", "area_ratio", "pin_distance",
            "angle", "pin_mid_x", "pin_mid_y", "seconds"]


def _v(x):
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (float, np.floating)):
        return None if x != x else round(float(x), 3)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, tuple):
        return " ".join(str(i) for i in x)
    return x


def summary_rows(name: str, summary: Dict, info: Dict) -> List[Dict]:
    out = []
    for boss, s in summary.items():
        b = s["best"]
        row = dict(picture=name, boss=boss, found=b is not None, candidates=s["candidates"],
                   candidate_groups=s["candidate_groups"], masks=s["masks"], seconds=info["time"],
                   type=info.get("type", ""), area_ratio=info.get("area_ratio"), pin_distance=info.get("pin_distance"),
                   angle=info.get("angle"), pin_mid_x=info.get("pin_mid", (None, None))[0],
                   pin_mid_y=info.get("pin_mid", (None, None))[1])
        if b:
            row.update(pin_x=b["core_x"], pin_y=b["core_y"], cx=b["cx"], cy=b["cy"], radius=b["radius"],
                       area=b["area"], eq_diameter=b["eq_diameter"], circularity=b["circularity"],
                       round_fill=b["round_fill"], ring_grey=b["ring_grey"], core_grey=b["core_grey"],
                       contrast=b["contrast"], core_radius=b["core_radius"], concentricity=b["concentricity"],
                       votes=b["votes"], score=b["score"], pair=b["pair"], mask_index=b["mask_index"])
        out.append({k: _v(row.get(k)) for k in SUM_COLS})
    return out


def write_outputs(out_dir: str, mask_rows: List[Dict], sum_rows: List[Dict]) -> List[str]:
    os.makedirs(out_dir, exist_ok=True)
    files = []
    for fname, cols, rows in (("boss_grid_masks.csv", MASK_COLS, mask_rows),
                              ("boss_grid_summary.csv", SUM_COLS, sum_rows)):
        p = os.path.join(out_dir, fname)
        with open(p, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
        files.append(p)
    try:
        import xlsxwriter
        p = os.path.join(out_dir, "boss_grid_masks.xlsx")
        wb = xlsxwriter.Workbook(p)
        bold = wb.add_format({"bold": True})
        for sheet, cols, rows in (("summary", SUM_COLS, sum_rows), ("masks", MASK_COLS, mask_rows)):
            ws = wb.add_worksheet(sheet)
            ws.write_row(0, 0, cols, bold)
            for i, r in enumerate(rows, start=1):
                ws.write_row(i, 0, [r.get(c) for c in cols])
            ws.freeze_panes(1, 0)
            ws.autofilter(0, 0, len(rows), len(cols) - 1)
        wb.close()
        files.append(p)
    except ImportError:
        print("xlsxwriter not installed: CSV only")
    return files


def draw(image_rgb: np.ndarray, summary: Dict, info: Dict) -> np.ndarray:
    out = image_rgb.copy()
    for pts in info["grids"].values():
        for x, y in pts:
            cv2.circle(out, (int(round(x)), int(round(y))), 3, (255, 255, 255), -1)
    for s in summary.values():
        b = s["best"]
        if b:
            cs, _ = cv2.findContours(b["mask"].astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE,
                                     offset=(b["box"][0], b["box"][1]))
            cv2.drawContours(out, cs, -1, (0, 255, 0), 2)
            cv2.circle(out, (int(round(b["core_x"])), int(round(b["core_y"]))), 3, (255, 0, 0), -1)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--folder", default=KS.DEFAULT_FOLDER, help="folder of knuckle pictures (or one picture)")
    ap.add_argument("--out", help="output folder (default <folder>/boss_grid_masks)")
    ap.add_argument("--sam", default=KS.DEFAULT_SAM, help="MobileSAM weights")
    ap.add_argument("--centre", choices=["fixed", "spindle"], default="fixed", help="where the grids are placed")
    ap.add_argument("--pictures", action="store_true", help="also save the marked pictures")
    args = ap.parse_args()
    if KS.sam_model_registry is None:
        sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(args.sam))))
        from mobile_sam import sam_model_registry, SamPredictor, SamAutomaticMaskGenerator
        KS.sam_model_registry, KS.SamPredictor, KS.SamAutomaticMaskGenerator = \
            sam_model_registry, SamPredictor, SamAutomaticMaskGenerator
    out_dir = args.out or os.path.join(args.folder if os.path.isdir(args.folder) else os.path.dirname(args.folder),
                                       "boss_grid_masks")
    st = BossGridStudy(args.sam)
    print(f"MobileSAM on {st.device}; grid {GRID_N} x {GRID_N}, spacing {SPACING:.1f} px, {len(grid_pairs())} pairs x "
          f"3 masks per boss; grids at {args.centre}; limits {LIMITS}")
    st.run(np.zeros((KS.IMAGE_SIZE[1], KS.IMAGE_SIZE[0], 3), np.uint8))      # warm-up (CUDA set-up)
    all_masks, all_sum = [], []
    for path in KS.list_pictures(args.folder):
        name = os.path.basename(path)
        image = KS.load_picture(path)
        rows, summary, info = st.run(image, args.centre)
        for r in rows:
            r["picture"] = name
            all_masks.append({c: _v(r.get(c)) for c in MASK_COLS})
        all_sum += summary_rows(name, summary, info)
        parts = []
        for boss, s in summary.items():
            b = s["best"]
            parts.append(f"{boss} " + (f"pin {b['core_x']:.1f},{b['core_y']:.1f} r {b['radius']:.1f} area {b['area']} "
                                       f"ring {b['ring_grey']:.0f} core {b['core_grey']:.0f} votes {b['votes']} "
                                       f"({s['candidate_groups']} cand. groups)" if b else "none"))
        typ = (f"; {info['type']} (ratio {info['area_ratio']:.1f}), pins {info['pin_distance']:.1f} px apart, "
               f"angle {info['angle']:+.2f}") if "type" in info else ""
        print(f"{name}: {info['time']:.2f} s (embedding {info['embed']:.2f}); " + "; ".join(parts) + typ, flush=True)
        if args.pictures:
            os.makedirs(out_dir, exist_ok=True)
            cv2.imwrite(os.path.join(out_dir, os.path.splitext(name)[0] + "_bossgrid.png"),
                        cv2.cvtColor(draw(image, summary, info), cv2.COLOR_RGB2BGR))
    for f in write_outputs(out_dir, all_masks, all_sum):
        print("written", f)


if __name__ == "__main__":
    main()
