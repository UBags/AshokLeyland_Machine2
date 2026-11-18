"""
DryRun_Bung_Hub_Holes - the bung's seated position from the hub's holes in the bung picture itself (CHANGES 153).

The seating step (BungSegmenter.check_bung_seating) compares the bung's hole-pattern centre with the seated position;
since CHANGES 153 that is the circle through the hub's 4 (DOST) / 5 (DOSTPLUS) holes round the bung in the same picture
(CheckCap.hubHoles, the cap check's method) plus vision.bung.seating.hub.offset.<model>. The hole-pattern centres of the
60 pictures of bung_images_20261113/ are read from hole_centres.json there (found by the production hole step with the
point grid alone, CHANGES 153), so the hole step (YOLO, slow on a CPU) is not run here; the seating step is run for real
(MobileSAM face mask).
  H1 the hub's holes are found on every picture labelled good (user, 2026-11-18) with the model's count, never with the
     other model's count;
  S1 every bung labelled good whose hole pattern was found and that is not in DISPUTED: SEATED;
  S2 every bung labelled bad whose hole pattern was found: NOT SEATED (bad without a hole pattern are NOT OK already);
  S3 the hub's holes hidden on a seated picture: NOT SEATED, "holes ... not found";
  S4 the picture moved 12 / -9 px (camera or part): the seated position moves with it, the seating offset stays (1 px);
  S5 position.source=fixed: the fixed seated position is used, as before CHANGES 153.
The DISPUTED pictures (labelled good, measured like the bad ones: bung 18 - 42 px off the hub centre) are printed, not
judged. Machine 1 (seating off): skipped.

    python test/machine1_dryrun/DryRun_Bung_Hub_Holes.py [--sam <mobile_sam.pt>]
"""
import argparse
import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("COSTHETA_PLC_VENDOR", "siemens" if HERE.name.startswith("machine2") else "allenbradley")

DISPUTED = ("A05", "C14", "C16")
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   [{detail}]"), flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sam", default=str(ROOT / "models" / "MobileSAM" / "weights" / "mobile_sam.pt"))
    args = ap.parse_args()
    import cv2
    import numpy as np
    import torch
    _load = torch.load
    torch.load = lambda f, *a, **k: _load(f, *a, **dict(k, weights_only=False))
    import camera.ModelManager as MMmod
    from mobile_sam import sam_model_registry, SamPredictor
    import camera.BungSegmenter as BS
    from camera.CheckCap import CheckCap
    if not BS.SEATING_CHECK:
        print("Bung seating is off for this machine (vision.bung.seating.check=False): nothing to check here.")
        return 0
    mm = MMmod.ModelManager.get_instance()
    mm.sam_model = sam_model_registry["vit_t"](checkpoint=args.sam).to(device=mm.device).eval()
    mm.sam_predictor = SamPredictor(mm.sam_model)
    data = HERE.parent / "machine2_dryrun" / "bung_images_20261113"
    fixture = json.loads((data / "hole_centres.json").read_text())
    ox = BS.OUTER_DISC_CENTER[0] - BS.INNER_DISC_RADIUS
    oy = BS.OUTER_DISC_CENTER[1] - BS.INNER_DISC_RADIUS
    print(f"source {BS.SEATING_POSITION_SOURCE}; offsets DOST {BS.SEATING_HUB_OFFSET_DOST} DOSTPLUS "
          f"{BS.SEATING_HUB_OFFSET_DOSTPLUS}; hole centre within {BS.SEATING_HOLE_CENTRE_OFFSET_MAX} px")

    def seat(rgb, centre, model):
        return BS.check_bung_seating(rgb, [((centre[0] - ox, centre[1] - oy), np.zeros((0, 1, 2), np.int32))], model)

    pictures = {}
    for name, info in fixture.items():
        bgr = cv2.imread(str(data / f"{name}.jpg"))
        pictures[name] = (bgr, cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), info)

    miss, wrong = [], []
    for name, (bgr, rgb, info) in pictures.items():
        if info["user_label"] != "good":
            continue
        other = "DOST" if info["model"] == "DOSTPLUS" else "DOSTPLUS"
        if not CheckCap.hubHoles(bgr, info["model"])["found"]:
            miss.append(name)
        if CheckCap.hubHoles(bgr, other)["found"]:
            wrong.append(name)
    check("H1 the hub's holes found on every picture labelled good, never with the other model's count",
          not miss and not wrong, (miss, wrong))

    seatedBad, notSeatedGood, seen = [], [], {}
    for name, (bgr, rgb, info) in sorted(pictures.items()):
        if info["hole_centre"] is None:
            continue
        s = seat(rgb, info["hole_centre"], info["model"])
        seen[name] = s
        tag = "disputed" if name in DISPUTED else info["user_label"]
        print(f"      {name} {info['model']:8s} {tag:8s} {'SEATED' if s['seated'] else 'NOT SEATED'}: {s['reason'][:200]}",
              flush=True)
        if name in DISPUTED:
            continue
        if info["user_label"] == "good" and not s["seated"]:
            notSeatedGood.append(name)
        if info["user_label"] == "bad" and s["seated"]:
            seatedBad.append(name)
    good = [n for n, (_, _, i) in pictures.items() if i["user_label"] == "good" and i["hole_centre"] and n not in DISPUTED]
    bad = [n for n, (_, _, i) in pictures.items() if i["user_label"] == "bad" and i["hole_centre"]]
    check(f"S1 {len(good)} good bungs with a hole pattern SEATED", good and not notSeatedGood, notSeatedGood)
    check(f"S2 {len(bad)} bad bungs with a hole pattern NOT SEATED", bad and not seatedBad, seatedBad)
    print(f"      disputed (labelled good): " + "; ".join(
        f"{n} {'SEATED' if seen[n]['seated'] else 'NOT SEATED'} off {seen[n]['offset']}" if n in seen else f"{n} no hole pattern"
        for n in DISPUTED))

    name = "B12"
    bgr, rgb, info = pictures[name]
    hidden = rgb.copy()
    yy, xx = np.mgrid[:720, :1280]
    ring = np.hypot(xx - CheckCap.CENTER[0], yy - CheckCap.CENTER[1])
    hidden[(ring > 120) & (ring < 175)] = (150, 150, 150)
    s = seat(hidden, info["hole_centre"], info["model"])
    check("S3 the hub's holes hidden: NOT SEATED, holes not found", not s["seated"] and "holes" in s["reason"]
          and "not found" in s["reason"], s["reason"][:160])

    m = np.float32([[1, 0, 12], [0, 1, -9]])
    moved = cv2.warpAffine(rgb, m, (1280, 720), borderMode=cv2.BORDER_REPLICATE)
    a = seat(rgb, info["hole_centre"], info["model"])
    b = seat(moved, (info["hole_centre"][0] + 12, info["hole_centre"][1] - 9), info["model"])
    ok = (a["seated_at"] and b["seated_at"] and abs(b["seated_at"][0] - a["seated_at"][0] - 12) < 1.0
          and abs(b["seated_at"][1] - a["seated_at"][1] + 9) < 1.0 and abs(b["offset"] - a["offset"]) < 1.0
          and a["seated"] and b["seated"])
    check("S4 picture moved 12 / -9 px: the seated position moves with it, the offset stays, SEATED", ok,
          (a["seated_at"], b["seated_at"], a["offset"], b["offset"]))

    saved = BS.SEATING_POSITION_SOURCE
    try:
        BS.SEATING_POSITION_SOURCE = "fixed"
        s = seat(rgb, info["hole_centre"], info["model"])
    finally:
        BS.SEATING_POSITION_SOURCE = saved
    fixedAt = BS.SEATING_SEATED_CENTRE_DOSTPLUS
    check("S5 position.source=fixed: the fixed seated position", s["seated_at"] == (float(fixedAt[0]), float(fixedAt[1]))
          and "fixed" in s["reason"], (s["seated_at"], s["reason"][:120]))
    print(f"RESULT {sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
