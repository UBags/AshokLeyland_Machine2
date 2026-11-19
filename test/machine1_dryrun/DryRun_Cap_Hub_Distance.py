"""
DryRun_Cap_Hub_Distance - the cap seating step's hub-centre distance (CHANGES 143).

  The hub check's winning group (4 / 5 holes or bolts) gives the hub's centre (least-squares circle through the mask
  centres, in full-picture pixels; circle radius >= vision.hub.centre.bolt.min.distance = bolts, else holes); the camera
  server keeps it with the cycle (ImageProcessor.hubCentre); the cap check finds the cap mask by the frozen method
  (camera/CapMaskFinder.py) and needs the stage-2 ellipse centre within vision.cap.seating.hub.distance.max of the
  hub centre + the offset for holes / bolts.

Checks
  H1 4 holes round a known centre (r 147, the segmenter's cropped picture): centre back in full-picture pixels, 'holes'
  H2 5 bolts (r 182): 'bolts'
  H3 the segmenter's crop clipped at the picture's edge: centre still right
  H4 1.5 px noise on the 5 mask centres: centre within 1.5 px
  H5 fewer than 3 centres: None
  S1 the hub branch of assessPictures: a hub check that passes sets ImageProcessor.hubCentre to its centre; one that
     fails leaves None (the check itself stood in - it needs the YOLO weights)
  S2 hubCentre in the state sent to the worker, in the state file (written and read back), dropped at the end of a cycle
  C0 the production cap mask (CapMaskFinder.findCap) equals the frozen window's (DryRun_Cap_Centre_Mask_UI) on the
     same boxes: same mask area, same stage-2 ellipse centre - on every cap picture
  C0b the same cap outline in the cut-out disc and in the full picture gives the same ellipse (20 ragged synthetic
     ellipses, shifted as the disc is cut): the cause of a 0.7 px difference on DOST_cap_08 on the laptop (CHANGES 144)
  C1 the rule: hub centre at the cap centre + (3, 0) px -> within; + (8, 0) -> NOT within; the offset of the hub kind
     and the model is used (per model since CHANGES 147); no hub centre -> not applied (and said so); switched off -> not applied
  C2 checkCap with the seating step (Machine 2): a seated cap with the hub centre + the configured DOST holes offset on its
     centre OK; 10 px further away NOT OK (cap not seated); without a hub centre as before
MobileSAM weights needed (models/MobileSAM/weights/mobile_sam.pt or --sam). The YOLO model comes from ModelManager when
its weights are there (the laptop); otherwise stand-in boxes (squares of half-side 80 - 120 px round the disc centre
and 20 px either side) are used and the run says so - C0 then proves the port, not YOLO.
Machine 1 (seating off): H, S and C0 run; C1 / C2 skipped.

Run from the project root:   python test/machine1_dryrun/DryRun_Cap_Hub_Distance.py [--sam <mobile_sam.pt>]
"""
import argparse
import importlib.util
import os
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["COSTHETA_PLC_VENDOR"] = "siemens" if HERE.name.startswith("machine2") else "allenbradley"

QR = {"DOST": "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$",
      "DOSTPLUS": "8201206$400112VA1C$22881$15.10.2026$C5211702$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$"}


class _Boxes:
    def __init__(self, xyxy):
        import torch
        self.xyxy = torch.tensor(xyxy, dtype=torch.float)


class _Result:
    def __init__(self, xyxy):
        self.boxes = _Boxes(xyxy)


class StandInYolo:
    """Squares of half-side 80 - 120 px round the picture's centre and 20 px either side (the picture is the cut disc)."""
    def predict(self, img, conf=0.25, iou=0.7, verbose=False):
        h, w = img.shape[:2]
        cx, cy = w / 2.0, h / 2.0
        boxes = [[cx + dx - r, cy + dy - r, cx + dx + r, cy + dy + r]
                 for r in (80, 90, 100, 110, 120) for dx, dy in ((0, 0), (20, 0), (-20, 0), (0, 20), (0, -20))]
        return [_Result(boxes)]


class CircleBoxYolo:
    """Sandbox stand-in for the object-aware YOLO, for the production cap paths (CHANGES 152), as YOLO puts a box round
    the cap: a brass cap (mean R-B within the brass radius of the disc's centre above the brass limit) gets boxes 3 / 6 / 9 px round the brass cap circle
    (CheckCap.brassCapAndBore on the disc put back in a full-size picture); a silver cap StandInYolo's squares (those
    match the laptop's YOLO runs on silver; on brass they make MobileSAM take the bore round DOSTPLUS_cap_03 / 04 / 08)."""
    def predict(self, img, conf=0.25, iou=0.7, verbose=False):
        import cv2
        import numpy as np
        from camera.CheckCap import CheckCap
        from camera import CapMaskFinder as CM
        bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        h, w = bgr.shape[:2]
        yy, xx = np.mgrid[:h, :w]
        inner = np.hypot(xx - w / 2.0, yy - h / 2.0) < float(CheckCap.RADIUS_DOSTPLUS)
        rb = bgr[..., 2].astype(float) - bgr[..., 0]
        if rb[inner].mean() <= CheckCap.RB_THRESHOLD_DOSTPLUS:
            return StandInYolo().predict(img)
        x0 = max(int(round(CheckCap.CENTER[0])) - int(CM.DISC_RADIUS), 0)
        y0 = max(int(round(CheckCap.CENTER[1])) - int(CM.DISC_RADIUS), 0)
        full = np.zeros((720, 1280, 3), np.uint8)
        # the disc comes brightened by CM.GAMMA: undone for the brass edge fit (as the cap check sees the picture)
        undo = np.array([((i / 255.0) ** float(CM.GAMMA)) * 255 for i in range(256)]).clip(0, 255).astype(np.uint8)
        full[y0:y0 + bgr.shape[0], x0:x0 + bgr.shape[1]] = cv2.LUT(bgr, undo)
        cap, _ = CheckCap.brassCapAndBore(full)
        if cap is None:
            return StandInYolo().predict(img)
        cx, cy, r = cap["cx"] - x0, cap["cy"] - y0, cap["radius"]
        return [_Result([[cx - r - d, cy - r - d, cx + r + d, cy + r + d] for d in (3, 6, 9)])]

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sam", default=str(ROOT / "models" / "MobileSAM" / "weights" / "mobile_sam.pt"))
    ap.add_argument("--holes", choices=("sam", "morphology"), default="sam")
    args = ap.parse_args()
    import cv2
    import numpy as np
    import camera.ModelManager  # noqa: F401 - its import sets the timm / registry warning filters (as in production)
    import camera.CameraProcessorServer as cps
    from camera.CheckCap import CheckCap
    from camera.FindHubHoles import FindHubHoles
    from _hub_holes_option import applyHolesOption
    applyHolesOption(args.holes)
    from camera.CheckHubAndBottomBearing import CheckHubAndBottomBearing as CH, HUB_CENTRE_BOLT_MIN_DISTANCE
    from camera import CapMaskFinder as CM
    from statemachine.StateMachine import MachineState as MS
    results = []

    def check(name, ok, detail=""):
        results.append(bool(ok))
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   [{detail}]"), flush=True)

    print(f"Machine: {os.environ['COSTHETA_PLC_VENDOR']}; bolts from circle r >= {HUB_CENTRE_BOLT_MIN_DISTANCE}; "
          f"distance rule {'on' if CheckCap.SEATING_HUB_DISTANCE_CHECK else 'off'}, max {CheckCap.SEATING_HUB_DISTANCE_MAX} px")

    # ---- H: the hub centre from the winning group ------------------------------------------------------------------
    def group(centre, r, n, annular, R, noise=0.0, seed=1):
        rng = np.random.default_rng(seed)
        x0, y0 = max(0, int(annular[0]) - R), max(0, int(annular[1]) - R)
        a = np.deg2rad(np.arange(n) * 360.0 / n + 17)
        pts = np.c_[centre[0] + r * np.cos(a), centre[1] + r * np.sin(a)] + rng.normal(0, noise, (n, 2)) * (noise > 0)
        return [(np.zeros((0, 1, 2), np.int32), (float(x - x0), float(y - y0))) for x, y in pts]

    true = (645.3, 332.1)
    h = CH.hubCentreFromGroup(group(true, 147, 4, (635, 345), 250), (635, 345), 250)
    check("H1 4 holes: centre and kind", h and abs(h["cx"] - true[0]) < 0.01 and abs(h["cy"] - true[1]) < 0.01
          and h["kind"] == "holes" and h["count"] == 4 and abs(h["radius"] - 147) < 0.01, h)
    h = CH.hubCentreFromGroup(group(true, 182, 5, (635, 345), 250), (635, 345), 250)
    check("H2 5 bolts: centre and kind", h and abs(h["cx"] - true[0]) < 0.01 and h["kind"] == "bolts" and h["count"] == 5, h)
    edge = (180.0, 200.0)
    h = CH.hubCentreFromGroup(group(edge, 147, 4, (190, 210), 250), (190, 210), 250)
    check("H3 crop clipped at the picture's edge", h and abs(h["cx"] - edge[0]) < 0.01 and abs(h["cy"] - edge[1]) < 0.01, h)
    h = CH.hubCentreFromGroup(group(true, 147, 5, (635, 345), 250, noise=1.5), (635, 345), 250)
    check(f"H4 1.5 px noise on 5 centres: centre within 1.5 px", h and np.hypot(h["cx"] - true[0], h["cy"] - true[1]) < 1.5,
          h and round(float(np.hypot(h["cx"] - true[0], h["cy"] - true[1])), 2))
    check("H5 fewer than 3 centres: None", CH.hubCentreFromGroup(group(true, 147, 2, (635, 345), 250), (635, 345), 250) is None)

    # ---- H6 - H7: the dark-blob finder (FindHubHoles.findHolesWithMorphology since CHANGES 164; hub source "cap", CHANGES 152)
    # Machine 2 pictures, round this machine's cap.center: only where the cap seating runs (Machine 2)
    if not CheckCap.SEATING_CHECK:
        print("Seating is off for this machine (vision.cap.seating.check=False): H6 / H7 skipped.")
    else:
        data0 = HERE if (HERE / "cap_images").is_dir() else HERE.parent / "machine2_dryrun"
        rd0 = lambda p: (lambda im: cv2.resize(im, (1280, 720)) if im.shape[:2] != (720, 1280) else im)(cv2.imread(str(p)))
        misses, wrong, radii = [], [], []
        pics = sorted(data0.glob("cap_images/*.jpg")) + sorted(data0.glob("hub_images/*.jpg"))
        for f in pics:
            model = "DOSTPLUS" if f.name.startswith("DOSTPLUS") else "DOST"
            img = rd0(f)
            hh = FindHubHoles.findHolesWithMorphology(img, model)
            if hh["found"]:
                radii.append(round(hh["radius"], 1))
            else:
                misses.append((f.name, hh["reason"]))
            if FindHubHoles.findHolesWithMorphology(img, "DOST" if model == "DOSTPLUS" else "DOSTPLUS")["found"]:
                wrong.append(f.name)
        check(f"H6 the hub's holes found on all {len(pics)} cap and hub pictures (4 on DOST, 5 on DOSTPLUS), never with "
              f"the other model's count; circle r {min(radii, default=0):.1f} - {max(radii, default=0):.1f}",
              pics and not misses and not wrong, (misses, wrong))

        def synthetic(shift=(0.0, 0.0), boltsDark=False, holes=True):
            rng = np.random.default_rng(7)               # a textured face (a flat grey one has no darkest 6 %)
            img = np.repeat(cv2.GaussianBlur(rng.normal(120, 14, (720, 1280)).clip(0, 255).astype(np.uint8), (0, 0), 2)[..., None],
                            3, axis=2)
            cx, cy = 644 + shift[0], 338 + shift[1]
            for k in range(4):
                a = np.deg2rad(20 + 90 * k)
                if holes:
                    cv2.circle(img, (int(round(cx + 150 * np.cos(a))), int(round(cy + 150 * np.sin(a)))), 12, (25, 25, 25), -1)
                b = np.deg2rad(65 + 90 * k)
                cv2.circle(img, (int(round(cx + 183 * np.cos(b))), int(round(cy + 183 * np.sin(b)))), 18,
                           (20, 20, 20) if boltsDark else (235, 235, 235), -1)
            return img, (cx, cy)
        img, c = synthetic()
        hA = FindHubHoles.findHolesWithMorphology(img, "DOST")
        img, c2 = synthetic(shift=(12.0, -9.0))
        hB = FindHubHoles.findHolesWithMorphology(img, "DOST")
        img, _ = synthetic(boltsDark=True, holes=False)
        hC = FindHubHoles.findHolesWithMorphology(img, "DOST")
        check("H7 holes (dark, r 150) found, not the bolts (bright, r 183): centre within 1 px; moved 12 / -9 px with the "
              "picture; dark discs at the bolts' radius alone are never taken for the holes",
              hA["found"] and np.hypot(hA["cx"] - c[0], hA["cy"] - c[1]) < 1.0 and abs(hA["radius"] - 150) < 1.5
              and hB["found"] and np.hypot(hB["cx"] - c2[0], hB["cy"] - c2[1]) < 1.0 and not hC["found"],
              (hA.get("cx"), hA.get("cy"), hA.get("radius"), hB.get("cx"), hB.get("cy"), hC["found"], hC["reason"]))

    # ---- S: the cycle's state ----------------------------------------------------------------------------------------
    IP = cps.ImageProcessor
    IP.stateFile = None
    IP.endOfCycle()
    IP.setCurrentComponentName(qrCodeValue=QR["DOST"])
    picture = np.full((720, 1280, 3), 120, np.uint8)
    hubValue = dict(cx=645.3, cy=332.1, radius=147.0, kind="holes", count=4, spread=0.4)
    real = CH.checkHubAndBottomBearing

    def stand(ok):
        def f(img, qr, use_smart_gamma=True, **k):
            CH.lastHubCentre = dict(hubValue) if ok else None
            return img, ok, 2 if ok else None
        return staticmethod(f)
    try:
        CH.checkHubAndBottomBearing = stand(True)
        okA, _, _ = cps.assessPictures(MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING, [picture] * 6)
        afterOk = IP.hubCentre
        CH.checkHubAndBottomBearing = stand(False)
        okB, _, _ = cps.assessPictures(MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING, [picture] * 6)
        afterNo = IP.hubCentre
    finally:
        CH.checkHubAndBottomBearing = real
    check("S1 hub check OK -> ImageProcessor.hubCentre set; NOT OK -> None",
          okA and afterOk == hubValue and not okB and afterNo is None, (okA, afterOk, okB, afterNo))
    IP.hubCentre = dict(hubValue)
    snap = IP.snapshotState()
    with tempfile.TemporaryDirectory() as d:
        IP.stateFile = os.path.join(d, "state.pkl")
        written = IP.persistState()
        IP.hubCentre = None
        loaded = IP.loadPersistedState(IP.stateFile)
        restored = IP.hubCentre
        IP.endOfCycle()
        cleared = IP.hubCentre
        IP.stateFile = None
    check("S2 hubCentre sent to the worker, kept in the state file, dropped at the end of a cycle",
          snap.get("hubCentre") == hubValue and written and loaded and restored == hubValue and cleared is None,
          (snap.get("hubCentre"), written, loaded, restored, cleared))

    # ---- C0b: the ellipse does not depend on where the outline lies in the picture ---------------------------------
    rng = np.random.default_rng(3)
    differ = []
    for k in range(20):
        small = np.zeros((321, 321), np.uint8)
        cv2.ellipse(small, (160 + int(rng.integers(-15, 15)), 160 + int(rng.integers(-15, 15))),
                    (int(rng.uniform(80, 110)), int(rng.uniform(78, 105))), rng.uniform(0, 180), 0, 360, 1, -1)
        speckle = (rng.random(small.shape) < 0.02).astype(np.uint8)
        small = cv2.morphologyEx(np.maximum(small, cv2.dilate(speckle, None) * small.max()), cv2.MORPH_OPEN, None)
        big = np.zeros((720, 1280), np.uint8)
        big[180:501, 484:805] = small
        a_, b_ = CM.fitCapEllipse(small, 2.5, 300), CM.fitCapEllipse(big, 2.5, 300)
        d = float(np.hypot(a_["e2"][0] + 484 - b_["e2"][0], a_["e2"][1] + 180 - b_["e2"][1]))
        if d > 1e-6:
            differ.append((k, round(d, 3)))
    check("C0b same outline in the disc and in the full picture: same stage-2 ellipse (20 synthetic outlines)",
          not differ, differ)

    # ---- C: the cap ---------------------------------------------------------------------------------------------------
    if not os.path.isfile(args.sam):
        print(f"MobileSAM weights not found at {args.sam}: give --sam (C checks not run)")
        return 1 if not all(results) else 2
    import torch
    load = torch.load
    torch.load = lambda f, *a, **k: load(f, *a, **{**k, "weights_only": False})
    from mobile_sam import sam_model_registry
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = sam_model_registry["vit_t"](checkpoint=args.sam).to(device).eval()
    CheckCap.samModelProvider = lambda: model
    try:
        yolo = camera.ModelManager.ModelManager.get_instance().get_yolo_model()
        if yolo is None:
            raise RuntimeError("no YOLO model")
        yoloNote = "YOLO from ModelManager"
    except Exception as e:
        yolo, yoloNote = StandInYolo(), f"stand-in YOLO boxes (no YOLO model here: {e})"
    CheckCap.yoloModelProvider = lambda: yolo
    print(f"MobileSAM on {device}; {yoloNote}")
    data = HERE if (HERE / "cap_images").is_dir() else HERE.parent / "machine2_dryrun"
    rd = lambda p: (lambda im: cv2.resize(im, (1280, 720)) if im.shape[:2] != (720, 1280) else im)(cv2.imread(str(p)))
    caps = sorted(data.glob("cap_images/*.jpg"))

    spec = importlib.util.spec_from_file_location("capwin", str(HERE / "DryRun_Cap_Centre_Mask_UI.py"))
    win = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(win)
    seg = win.HubCentreSegmenter(predictor=CheckCap._sam_predictor(), yolo=yolo)
    centre = (float(CheckCap.CENTER[0]), float(CheckCap.CENTER[1]))
    settings = win.wholeSettings("yolo", 64, CM.STABILITY_MIN, CM.IOU_MIN, CM.MERGE_IOU, CM.YOLO_CONFIDENCE,
                                 CM.DISC_RADIUS, minArea=CM.AREA_MIN, maxArea=CM.AREA_MAX,
                                 ellipseTolerance=CM.ELLIPSE_TOLERANCE, ellipseTries=CM.ELLIPSE_TRIES)
    bad, found = [], {}
    with tempfile.TemporaryDirectory() as d:
        csvPath = pathlib.Path(d) / "w.csv"
        for f in caps:
            cap = CM.findCap(rd(f), centre, CheckCap._sam_predictor(), yolo)
            found[f.name] = cap
            win.processWhole(seg, f, centre, CM.GAMMA, settings, csvPath)
            import csv
            row = list(csv.DictReader(open(csvPath)))[-1]
            wa = row.get("first_area") or None
            wc = (float(row["ell2_cx"]), float(row["ell2_cy"])) if row.get("ell2_cx") else None
            pc = cap["centre"]
            same = ((wa is None and not cap["found"]) or
                    (wa is not None and cap["area"] == int(float(wa)) and pc is not None and wc is not None
                     and abs(pc[0] - wc[0]) < 0.05 and abs(pc[1] - wc[1]) < 0.05))
            if not same:
                bad.append((f.name, wa, cap["area"], wc, pc))
            print(f"      {f.name}: {CM.describe(cap)}")
    check(f"C0 production cap mask = the frozen window's, {len(caps)} cap pictures", caps and not bad, bad)

    if not CheckCap.SEATING_CHECK:
        print("Seating is off for this machine (vision.cap.seating.check=False): C1 / C2 skipped.")
        return 0 if all(results) else 1
    seatedName = "DOST_cap_01.jpg"
    seatedPic = rd(data / "cap_images" / seatedName)
    cc = found[seatedName]["centre"]
    names = ("SEATING_HUB_DISTANCE_OFFSET_HOLES_DOST", "SEATING_HUB_DISTANCE_OFFSET_BOLTS_DOST",
             "SEATING_HUB_DISTANCE_OFFSET_HOLES_DOSTPLUS", "SEATING_HUB_DISTANCE_OFFSET_BOLTS_DOSTPLUS")
    saved = (CheckCap.SEATING_HUB_DISTANCE_CHECK,) + tuple(getattr(CheckCap, n) for n in names)
    savedSource = CheckCap.SEATING_HUB_SOURCE
    try:
        CheckCap.SEATING_HUB_SOURCE = "hubcheck"            # C1 / C2: the hub check's centre (CHANGES 143)
        CheckCap.SEATING_HUB_DISTANCE_CHECK = True
        for n in names:
            setattr(CheckCap, n, (0.0, 0.0))
        near = CheckCap.hubCentreDistance(seatedPic, dict(cx=cc[0] - 3, cy=cc[1], kind="holes", count=4), "DOST")
        far = CheckCap.hubCentreDistance(seatedPic, dict(cx=cc[0] - 8, cy=cc[1], kind="holes", count=4), "DOST")
        CheckCap.SEATING_HUB_DISTANCE_OFFSET_BOLTS_DOST = (8.0, 0.0)
        CheckCap.SEATING_HUB_DISTANCE_OFFSET_HOLES_DOSTPLUS = (8.0, 0.0)
        bolts = CheckCap.hubCentreDistance(seatedPic, dict(cx=cc[0] - 8, cy=cc[1], kind="bolts", count=5), "DOST")
        holes = CheckCap.hubCentreDistance(seatedPic, dict(cx=cc[0] - 8, cy=cc[1], kind="holes", count=4), "DOST")
        holesPlus = CheckCap.hubCentreDistance(seatedPic, dict(cx=cc[0] - 8, cy=cc[1], kind="holes", count=5), "DOSTPLUS")
        boltsPlus = CheckCap.hubCentreDistance(seatedPic, dict(cx=cc[0] - 8, cy=cc[1], kind="bolts", count=5), "DOSTPLUS")
        none = CheckCap.hubCentreDistance(seatedPic, None, "DOST")
        CheckCap.SEATING_HUB_DISTANCE_CHECK = False
        off = CheckCap.hubCentreDistance(seatedPic, dict(cx=0.0, cy=0.0, kind="holes", count=4), "DOST")
    finally:
        CheckCap.SEATING_HUB_DISTANCE_CHECK = saved[0]
        CheckCap.SEATING_HUB_SOURCE = savedSource
        for n, v in zip(names, saved[1:]):
            setattr(CheckCap, n, v)
    check("C1 3 px within, 8 px not; the offset of the hub kind and the model (DOST bolts, DOSTPLUS holes); no hub centre / off: not applied",
          near["applied"] and near["ok"] and abs(near["distance"] - 3) < 0.01 and far["applied"] and not far["ok"]
          and bolts["ok"] and abs(bolts["distance"]) < 0.01 and not holes["ok"]
          and holesPlus["ok"] and abs(holesPlus["distance"]) < 0.01 and not boltsPlus["ok"]
          and not none["applied"] and none["ok"] and "no hub centre" in none["reason"]
          and not off["applied"] and off["ok"],
          [(r.get("applied"), r.get("ok"), r.get("distance")) for r in (near, far, bolts, holes, holesPlus, boltsPlus, none, off)])
    print(f"      {near['reason']}")

    def full(hub):
        IP.endOfCycle()
        IP.setCurrentComponentName(qrCodeValue=QR["DOST"])
        IP.currentPictures[cps.hubAndBottomBearingPictureKeyString] = rd(sorted(data.glob("hub_images/DOST_0*.jpg"))[0])
        IP.currentPictures[cps.topBearingPictureKeyString] = rd(sorted(data.glob("top_bearing_images/DOST_bearing_0*.jpg"))[0])
        IP.currentPictures[cps.nutAndPlateWasherPictureKeyString] = rd(sorted(data.glob("nut_washer_images/DOST_nut_0*.jpg"))[0])
        IP.hubCentre = hub
        ok, _, _ = cps.assessPictures(MS.READ_TAKE_PICTURE_FOR_CHECKING_CAP, [seatedPic] * 6)
        return ok
    ox, oy = CheckCap.hubOffset("holes", "DOST")       # the configured offset: hub + offset lands on the cap centre
    try:
        CheckCap.SEATING_HUB_SOURCE = "hubcheck"
        okOn = full(dict(cx=cc[0] - ox, cy=cc[1] - oy, kind="holes", count=4))
        okFar = full(dict(cx=cc[0] - ox - 10, cy=cc[1] - oy, kind="holes", count=4))
        okNone = full(None)
    finally:
        CheckCap.SEATING_HUB_SOURCE = savedSource
    check("C2 checkCap (camera server path, hub source hubcheck): hub centre on the cap OK, 10 px away NOT OK, none as "
          "before (OK)", okOn and not okFar and okNone, (okOn, okFar, okNone))

    # ---- C3 / C4: hub source "cap" (CHANGES 152), as configured for this machine ------------------------------------
    if str(CheckCap.SEATING_HUB_SOURCE).strip().lower() != "cap" or not CheckCap.SEATING_HUB_DISTANCE_CHECK:
        print("Hub source is not 'cap' or the distance rule is off on this machine: C3 / C4 skipped.")
    else:
        rows, wrong3 = [], []
        expect = {f"DOST_cap_0{i}.jpg": True for i in range(1, 10)}
        expect.update({"DOST_skewed_04.jpg": False, "DOST_skewed_05.jpg": False, "DOST_skewed_06.jpg": False})
        for name, seated in sorted(expect.items()):
            r = CheckCap.hubCentreDistance(rd(data / "cap_images" / name), None, "DOST")
            rows.append((name, r["ok"], None if r["distance"] is None else round(r["distance"], 2)))
            if not r["applied"] or r["ok"] != seated or (r["hub"] or {}).get("kind") != "cap holes":
                wrong3.append((name, r["ok"], r["distance"], r["reason"][:120]))
        print(f"      {rows}")
        print(f"      {CheckCap.hubCentreDistance(seatedPic, None, 'DOST')['reason']}")
        check(f"C3 DOST, hub from the holes in the cap picture (offset {CheckCap.hubOffset('cap holes', 'DOST')}, max "
              f"{CheckCap.SEATING_HUB_DISTANCE_MAX}): 9 seated caps within, pushed-aside skewed 04 - 06 not", not wrong3, wrong3)
        covered = seatedPic.copy()
        yy, xx = np.mgrid[:720, :1280]
        ring = np.hypot(xx - CheckCap.CENTER[0], yy - CheckCap.CENTER[1])
        covered[(ring > 120) & (ring < 175)] = 140                      # the holes hidden (e.g. a cap lying on them)
        okSeated = full(None)
        IP.endOfCycle()
        IP.setCurrentComponentName(qrCodeValue=QR["DOST"])
        IP.currentPictures[cps.hubAndBottomBearingPictureKeyString] = rd(sorted(data.glob("hub_images/DOST_0*.jpg"))[0])
        IP.currentPictures[cps.topBearingPictureKeyString] = rd(sorted(data.glob("top_bearing_images/DOST_bearing_0*.jpg"))[0])
        IP.currentPictures[cps.nutAndPlateWasherPictureKeyString] = rd(sorted(data.glob("nut_washer_images/DOST_nut_0*.jpg"))[0])
        IP.hubCentre = None
        okCovered, _, _ = cps.assessPictures(MS.READ_TAKE_PICTURE_FOR_CHECKING_CAP, [covered] * 6)
        hidden = CheckCap.hubCentreDistance(covered, None, "DOST")
        check("C4 checkCap (camera server path, hub source cap, no hub check centre): seated DOST_cap_01 OK; the same "
              "picture with the holes hidden NOT OK (holes not found)",
              okSeated and not okCovered and hidden["applied"] and not hidden["ok"] and "holes" in hidden["reason"],
              (okSeated, okCovered, hidden["reason"][:140]))
    IP.endOfCycle()
    print(f"{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
