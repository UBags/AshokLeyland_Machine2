"""
DryRun_Bung_Frame - do the bung checks follow the knuckle frame when the camera moves? (CHANGES 126)

Takes bung pictures from the 13 Nov set (reference camera position) and runs the real bung check (CheckBung.checkBung,
the cap-press check; the component-press check uses the same BungSegmenter code) twice for each:
  1. the picture as taken, with the frame of an unmoved camera (the type's nominal pins);
  2. the picture moved as a knocked camera would move it (shift, turn, zoom about the pin midpoint), with the frame
     built from the pins moved the same way - what the knuckle check would hand over in that cycle.
The verdict must be the same, the hole-pattern centre must move with the picture (within 3 px of where the move puts
it) and the seating numbers that depend on the placed positions and oval (offset from the seated position, share of
the face outside the reference oval) must stay close: offset within 3 px, outside within 4 points (CHANGES 153: with
the oval centred on the face by the hub's holes, MobileSAM's face mask, which changes when the picture moves, moved
B12's share 0.9 -> 3.7 %; with the fixed position 4.4 -> 4.7 %); the seated position must move with the picture
within 2.5 px (the hub-hole finder on a resampled picture: A08 at 3 % zoom 1.9 px, part of it the offset, which is not
scaled with the zoom). The face roundness is printed only: MobileSAM's face mask is not
translation-invariant (its outline changes when the picture moves by a few pixels, 2026-11-17: up to 0.056 on C05,
0.031 on B12 for a 15 / -10 px shift), whatever the frame does. The disc centre, the seated positions and (since CHANGES 126) the reference ovals come
from VisionFrame.applyToChecks, as in the camera server.

    python test/machine2_dryrun/DryRun_Bung_Frame.py [--sam <mobile_sam.pt>] [--no-yolo] [--pictures B12,A08,C05]

--fixed-ovals: the reference ovals not scaled / turned with the frame (as before CHANGES 126), for comparison.
--no-yolo: YOLO's boxes left out (the dense point grid alone finds the holes); for a machine without the YOLO weights
(Claude's sandbox). With YOLO (the laptop) leave it off. Needs MobileSAM; no Redis.
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
os.environ.setdefault("COSTHETA_PLC_VENDOR", "siemens" if HERE.name.startswith("machine2") else "allenbradley")

import cv2
import numpy as np

QR = {"DOST": "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$",
      "DOSTPLUS": "8201206$400102VA1C$22881$15.10.2026$C5211702$MEK$40cR4-B$1$SPDL ASSY-KNURH$SNPL-MAT$"}
SIDE = {"DOST": "LHS", "DOSTPLUS": "RHS"}
# 13 Nov labels (CHANGES 100 / the DOSTPLUS run of 2026-11-17): seated DOSTPLUS, seated DOST, DOSTPLUS not seated
DEFAULT_PICTURES = {"B12": ("DOSTPLUS", True), "A08": ("DOST", True), "C05": ("DOSTPLUS", False)}
MOVES = [(15.0, -10.0, 0.0, 1.0), (-12.0, 8.0, 1.5, 1.0), (0.0, 0.0, 0.0, 1.03)]   # dx, dy, degrees, zoom

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f"   [{detail}]" if detail != "" else ""), flush=True)


def move_matrix(dx, dy, deg, zoom, about):
    m = cv2.getRotationMatrix2D(about, -deg, zoom)          # cv2: positive = anticlockwise; frame: y-down angle
    m[0, 2] += dx
    m[1, 2] += dy
    return m


def apply(m, p):
    return float(m[0, 0] * p[0] + m[0, 1] * p[1] + m[0, 2]), float(m[1, 0] * p[0] + m[1, 1] * p[1] + m[1, 2])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sam", default=str(ROOT / "models" / "MobileSAM" / "weights" / "mobile_sam.pt"))
    ap.add_argument("--no-yolo", action="store_true")
    ap.add_argument("--pictures", default=",".join(DEFAULT_PICTURES))
    ap.add_argument("--fixed-ovals", action="store_true",
                    help="reference ovals not scaled / turned (as before CHANGES 126), for comparison")
    ap.add_argument("--moves", default="", help="e.g. 0,0,0,1.03;15,-10,0,1 (default: the three MOVES)")
    ap.add_argument("--folder", default=str(ROOT / "test" / "machine2_dryrun" / "bung_images_20261113"))
    args = ap.parse_args()

    import torch
    _load = torch.load
    torch.load = lambda f, *a, **k: _load(f, *a, **dict(k, weights_only=False))
    import camera.ModelManager as MMmod
    mm = MMmod.ModelManager.get_instance()
    if args.no_yolo:
        from mobile_sam import sam_model_registry, SamPredictor

        class NoBoxes:
            def predict(self, *a, **k):
                return []

            def to(self, *a, **k):
                return self
        mm.sam_model = sam_model_registry["vit_t"](checkpoint=args.sam).to(device=mm.device).eval()
        mm.sam_predictor = SamPredictor(mm.sam_model)
        mm.yolo_model = NoBoxes()
    import camera.BungSegmenter as BS
    import camera.CheckBung as CB
    import camera.VisionFrame as VF
    if args.fixed_ovals:
        VF._OVAL_TARGETS = {}
    moves = [tuple(float(v) for v in m.split(",")) for m in args.moves.split(";") if m.strip()] or MOVES
    seatings = []
    _seat = BS.check_bung_seating

    def recording(*a, **k):
        r = _seat(*a, **k)
        seatings.append(r)
        return r
    BS.check_bung_seating = recording
    CB.BS_MODULE.check_bung_seating = recording

    def frame_for(model, m=None):
        nom = VF.nominalPins()[(model, SIDE[model])]
        half = nom["distance"] / 2.0
        a = math.radians(nom["angle"])
        mid = nom["pin_mid"]
        left = (mid[0] - half * math.cos(a), mid[1] - half * math.sin(a))
        right = (mid[0] + half * math.cos(a), mid[1] + half * math.sin(a))
        if m is not None:
            left, right = apply(m, left), apply(m, right)
        return VF.buildFrame(model, SIDE[model], (left, right), None)

    def run(img, model):
        seatings.clear()
        t = time.monotonic()
        _, ok, gamma = CB.CheckBung.checkBung(img, QR[model])        # BGR, as the camera server passes it
        s = seatings[-1] if seatings else {}
        return bool(ok), s, time.monotonic() - t

    for name in [p.strip() for p in args.pictures.split(",") if p.strip()]:
        model, seated = DEFAULT_PICTURES.get(name, ("DOSTPLUS", True))
        path = os.path.join(args.folder, f"{name}.jpg")
        img = cv2.imread(path)
        if img is None:
            check(f"{name}: picture read", False, path)
            continue
        if img.shape[:2] != (720, 1280):
            img = cv2.resize(img, (1280, 720))
        base_frame = frame_for(model)
        VF.applyToChecks(base_frame)
        ok0, s0, t0 = run(img, model)
        check(f"{name} ({model}, {'seated' if seated else 'not seated'}) as taken: {'OK' if ok0 else 'NOT OK'} as expected"
              f" ({t0:.1f} s)", ok0 == seated, {k: s0.get(k) for k in ("hole_centre", "offset", "outside", "roundness")})
        pin_mid = VF.nominalPins()[(model, SIDE[model])]["pin_mid"]
        for mv in moves:
            m = move_matrix(mv[0], mv[1], mv[2], mv[3], pin_mid)
            moved = cv2.warpAffine(img, m, (img.shape[1], img.shape[0]), flags=cv2.INTER_LINEAR,
                                   borderMode=cv2.BORDER_REPLICATE)
            frame = frame_for(model, m)
            VF.applyToChecks(frame)
            ok1, s1, t1 = run(moved, model)
            detail = {"shift": frame["cameraShift"], "rot": frame["rotation"], "scale": frame["scale"],
                      "disc": tuple(BS.OUTER_DISC_CENTER), "oval": tuple(BS.SEATING_REFERENCE_OVAL_DOSTPLUS if model ==
                                                                     "DOSTPLUS" else BS.SEATING_REFERENCE_OVAL_DOST)}
            check(f"{name} moved {mv}: same verdict ({'OK' if ok1 else 'NOT OK'}, {t1:.1f} s)", ok1 == ok0, detail)
            if s0.get("hole_centre") and s1.get("hole_centre"):
                want = apply(m, s0["hole_centre"])
                err = math.hypot(s1["hole_centre"][0] - want[0], s1["hole_centre"][1] - want[1])
                check(f"{name} moved {mv}: hole-pattern centre moved with the picture (off by {err:.1f} px)", err <= 3.0)
                if s0.get("outside") is not None and s1.get("outside") is not None:
                    d_off = abs(s1["offset"] - s0["offset"])
                    d_out = abs(s1["outside"] - s0["outside"])
                    d_round = abs((s1.get("roundness") or 0) - (s0.get("roundness") or 0))
                    check(f"{name} moved {mv}: seating numbers kept (offset {s0['offset']:.1f} -> {s1['offset']:.1f} px, "
                          f"outside {s0['outside']:.1%} -> {s1['outside']:.1%}; roundness {s0.get('roundness') or 0:.3f}"
                          f" -> {s1.get('roundness') or 0:.3f}, change {d_round:.3f}, reported only)",
                          d_off <= 3.0 and d_out <= 0.04)
                    # the seated position (and the reference oval on it) moved with the picture: since CHANGES 153 it is
                    # the hub's hole circle in the picture + offset (the frame's fixed position with source "fixed")
                    if s0.get("seated_at") is not None and s1.get("seated_at") is not None:
                        wantAt = apply(m, s0["seated_at"])
                        errAt = math.hypot(s1["seated_at"][0] - wantAt[0], s1["seated_at"][1] - wantAt[1])
                        check(f"{name} moved {mv}: seated position moved with the picture (off by {errAt:.1f} px)",
                              errAt <= 2.5)
        VF.applyToChecks(None)
    print(f"\n{sum(RESULTS)}/{len(RESULTS)} checks passed", flush=True)
    sys.exit(0 if all(RESULTS) else 1)


if __name__ == "__main__":
    main()
