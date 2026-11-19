"""
DryRun_Hub_Holes_SAM - the hub's holes as the cap, bung and split pin checks now find them (camera/FindHubHoles.py,
CHANGES 164: YOLO + MobileSAM with checkHub()'s steps in a thin annulus round the hub centre) against the dark-blob
finder they used before (findHolesWithMorphology), on the stored pictures:

  hub        hub_images/<MODEL>_NN.jpg
  splitpin   splitpin_images/OK, NOT_OK (DOST)
  cap        cap_images/<MODEL>_cap_NN, <MODEL>_skewed_NN
  bung       bung_images_20261113 (model from hole_centres.json)

Per picture: holes found or not (SAM, and morphology), the SAM circle (centre, radius, spread, gap, gamma, seed) and
its centre's difference from the morphology circle's. Per set and model: the mean difference (morphology - SAM) and the
offsets that keep every check's target where it was:

  cap       vision.cap.seating.hub.distance.offset.cap.<model>   + mean(morphology - SAM)
  bung      vision.bung.seating.hub.offset.<model>               + mean(morphology - SAM)
  splitpin  vision.splitpin.hub.offset.<model>                   + mean(morphology - SAM)

(the SAM masks are the holes' outlines on the disc face; the dark blobs are the shadowed inside of each hole, seen a
few px off by the camera's slight angle - so the two centres differ by a steady amount per model).

Seed (the annulus centre): --seed none (default) = as without this cycle's hub check: one pass over checkHub()'s own
annulus first (holes or bolts), its centre then used; --seed morphology = the morphology circle's centre (faster);
--seed hubcheck = the hub pictures only: the real checkHub() on the picture, its centre (holes or bolts) as the seed,
as in production - and per model and kind the mean of SAM holes - hub check centre, i.e. the value for
vision.hubholes.sam.seed.bolts.offset.<model> (kind bolts; kind holes should be about 0). --wrong-model also asks for the other model's holes
(must not be found). --sets hub,splitpin,cap,bung, --limit N per set.

Models: ModelManager's (models/MobileSAM: MobileSAM and the object-aware YOLO), as in production - on the laptop.
--sam FILE loads MobileSAM from that file; --tiled-yolo replaces YOLO by boxes of 30 px every 14 px over the annulus
(a sandbox stand-in that knows nothing about holes; slow, about 15 s per gamma on a CPU).

Exit code 0 when SAM found the holes on every picture where morphology found them (and, with --wrong-model, never the
other model's). Run from the project root:
    python test/machine2_dryrun/DryRun_Hub_Holes_SAM.py [--sets ...] [--seed none|morphology] [--wrong-model]
"""
import argparse
import json
import os
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["COSTHETA_PLC_VENDOR"] = "siemens" if HERE.name.startswith("machine2") else "allenbradley"
DATA = HERE if (HERE / "splitpin_images").is_dir() else ROOT / "test" / "machine2_dryrun"


def pictures(sets, limit):
    out = []
    if "hub" in sets:
        out += [("hub", p, "DOSTPLUS" if p.name.startswith("DOSTPLUS") else "DOST")
                for p in sorted((DATA / "hub_images").glob("DOST*.jpg"))][:limit]
    if "splitpin" in sets:
        out += [("splitpin", p, "DOST") for sub in ("OK", "NOT_OK")
                for p in sorted((DATA / "splitpin_images" / sub).glob("*.jpg"))][:limit]
    if "cap" in sets:
        out += [("cap", p, "DOSTPLUS" if p.name.startswith("DOSTPLUS") else "DOST")
                for p in sorted((DATA / "cap_images").glob("DOST*.jpg"))][:limit]
    if "bung" in sets:
        info = json.loads((DATA / "bung_images_20261113" / "hole_centres.json").read_text())
        out += [("bung", DATA / "bung_images_20261113" / f"{k}.jpg", v["model"]) for k, v in sorted(info.items())][:limit]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", default="hub,splitpin,cap,bung")
    ap.add_argument("--limit", type=int, default=10 ** 6)
    ap.add_argument("--seed", choices=("none", "morphology", "hubcheck"), default="none")
    ap.add_argument("--wrong-model", action="store_true")
    ap.add_argument("--sam", default=None)
    ap.add_argument("--tiled-yolo", action="store_true")
    args = ap.parse_args()

    import cv2
    import numpy as np
    import camera.ModelManager  # noqa: F401 - its import sets the warning filters (as in production)
    from camera.FindHubHoles import FindHubHoles as F
    import camera.CheckCap as CC
    import camera.BungSegmenter as BS
    from camera.CheckSplitPinAndWasher import CheckSplitPinAndWasher as SP
    sys.path.insert(0, str(HERE))
    from _hub_holes_option import MORPHOLOGY_OFFSETS

    if args.sam:
        import torch
        from mobile_sam import sam_model_registry
        samModel = sam_model_registry["vit_t"](checkpoint=args.sam)
        samModel.to("cuda" if torch.cuda.is_available() else "cpu").eval()
        F.samModelProvider = staticmethod(lambda: samModel)
    if args.tiled_yolo:
        import torch

        class _Boxes:
            def __init__(self, b):
                self.xyxy = torch.tensor(b, dtype=torch.float32).reshape(-1, 4)

        class _Result:
            def __init__(self, b):
                self.boxes = _Boxes(b)

        class TiledBoxYolo:
            def predict(self, img, **kwargs):
                g = img if img.ndim == 2 else img.max(axis=2)
                h, w = g.shape
                b = [[x - 15, y - 15, x + 15, y + 15] for y in range(15, h - 15, 14) for x in range(15, w - 15, 14)
                     if g[y, x] > 0]
                return [_Result(b)]

        F.yoloModelProvider = staticmethod(lambda: TiledBoxYolo())

    sets = [s.strip() for s in args.sets.split(",") if s.strip()]
    if args.seed == "hubcheck":
        sets = ["hub"]
    QR = {"DOST": "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$",
          "DOSTPLUS": "8201206$400112VA1C$22881$15.10.2026$C5211702$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$"}
    rows, failures, seedRows = [], [], []
    for kind, path, model in pictures(sets, args.limit):
        image = cv2.imread(str(path))
        if image is None:
            continue
        image = cv2.resize(image, (1280, 720))
        morph = F.findHolesWithMorphology(image, model)
        seed = dict(cx=morph["cx"], cy=morph["cy"], count=morph["count"], kind="morphology holes") \
            if args.seed == "morphology" and morph["found"] else None
        if args.seed == "hubcheck":
            from camera.CheckHubAndBottomBearing import CheckHubAndBottomBearing as CH
            _, hubOk, _ = CH.checkHubAndBottomBearing(image.copy(), QR[model])
            seed = CH.lastHubCentre if hubOk else None
            if seed is None:
                failures.append(f"{path.name}: the hub check did not pass / gave no centre")
        F._cache.clear()
        t0 = time.perf_counter()
        sam = F.findHolesWithSAM(image, model, seed)
        secs = time.perf_counter() - t0
        line = f"{kind:8s} {path.name[-28:]:28s} {model:8s} "
        if sam["found"]:
            line += (f"SAM ({sam['cx']:.1f}, {sam['cy']:.1f}) r {sam['radius']:.1f} spread {sam['spread']:.1f} gap "
                     f"{sam['gap']:.0f} gamma {sam['gamma']} seed d "
                     f"{np.hypot(sam['cx'] - sam['seed'][0], sam['cy'] - sam['seed'][1]):.1f}")
        else:
            line += f"SAM NOT FOUND ({sam['reason']})"
        if morph["found"]:
            line += f" | morph ({morph['cx']:.1f}, {morph['cy']:.1f})"
            if sam["found"]:
                d = (morph["cx"] - sam["cx"], morph["cy"] - sam["cy"])
                line += f" morph-SAM ({d[0]:+.1f}, {d[1]:+.1f})"
                rows.append((kind, model, d))
            else:
                failures.append(f"{path.name}: SAM did not find the holes that morphology found")
        else:
            line += " | morph NOT FOUND"
        if args.seed == "hubcheck" and seed is not None and sam["found"]:
            e = (sam["cx"] - float(seed["cx"]), sam["cy"] - float(seed["cy"]))
            line += (f" | hub check {seed.get('kind')} ({seed['cx']:.1f}, {seed['cy']:.1f}): SAM - hub check "
                     f"({e[0]:+.1f}, {e[1]:+.1f})")
            seedRows.append((model, seed.get("kind"), e))
        line += f"  {secs:.1f} s  [seed: {sam.get('seedFrom')}]"
        if args.wrong_model:
            other = "DOST" if model == "DOSTPLUS" else "DOSTPLUS"
            F._cache.clear()
            wrong = F.findHolesWithSAM(image, other, seed)
            line += f"  as {other}: {'FOUND (wrong)' if wrong['found'] else 'not found'}"
            if wrong["found"]:
                failures.append(f"{path.name}: {other} holes found on a {model} hub")
        print(line, flush=True)

    print()
    current = {("cap", "DOST"): CC.CheckCap.SEATING_HUB_DISTANCE_OFFSET_CAP_DOST,
               ("cap", "DOSTPLUS"): CC.CheckCap.SEATING_HUB_DISTANCE_OFFSET_CAP_DOSTPLUS,
               ("bung", "DOST"): BS.SEATING_HUB_OFFSET_DOST, ("bung", "DOSTPLUS"): BS.SEATING_HUB_OFFSET_DOSTPLUS,
               ("splitpin", "DOST"): SP.HUB_OFFSET_DOST, ("splitpin", "DOSTPLUS"): SP.HUB_OFFSET_DOSTPLUS}
    keys = {"cap": "vision.cap.seating.hub.distance.offset.cap", "bung": "vision.bung.seating.hub.offset",
            "splitpin": "vision.splitpin.hub.offset"}
    for kind in ("hub", "splitpin", "cap", "bung"):
        for model in ("DOST", "DOSTPLUS"):
            d = np.array([r[2] for r in rows if r[0] == kind and r[1] == model])
            if not len(d):
                continue
            m, sd = d.mean(axis=0), d.std(axis=0)
            text = (f"{kind:8s} {model:8s} {len(d):3d} pictures: morphology - SAM mean ({m[0]:+.2f}, {m[1]:+.2f}) sd "
                    f"({sd[0]:.2f}, {sd[1]:.2f})")
            if (kind, model) in current:
                # the offsets were measured against the morphology centres (MORPHOLOGY_OFFSETS); the SAM centre sits
                # mean(morphology - SAM) away, so the SAM offset is that base + the mean - never the live value + it
                # (the live value is already a SAM offset; CHANGES 166)
                base, now = MORPHOLOGY_OFFSETS[kind][model], current[(kind, model)]
                text += (f"  ->  {keys[kind]}.{model.lower()} = {base[0] + m[0]:.1f},{base[1] + m[1]:.1f} "
                         f"(morphology offset {base[0]:g},{base[1]:g} + mean; now {float(now[0]):g},{float(now[1]):g})")
            print(text)
    for model in ("DOST", "DOSTPLUS"):
        for hubKind in ("holes", "bolts"):
            e = np.array([r[2] for r in seedRows if r[0] == model and r[1] == hubKind])
            if len(e):
                m, sd = e.mean(axis=0), e.std(axis=0)
                print(f"hub check {model:8s} {hubKind:5s} {len(e):3d} pictures: SAM holes - hub check centre ({m[0]:+.2f}, "
                      f"{m[1]:+.2f}) sd ({sd[0]:.2f}, {sd[1]:.2f})"
                      + (f"  ->  vision.hubholes.sam.seed.bolts.offset.{model.lower()} = {m[0]:.1f},{m[1]:.1f}"
                         if hubKind == "bolts" else ""))
    for f in failures:
        print("FAIL:", f)
    print("RESULT:", "PASS" if not failures else f"{len(failures)} FAILURE(S)", f"({len(rows)} pictures compared)")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
