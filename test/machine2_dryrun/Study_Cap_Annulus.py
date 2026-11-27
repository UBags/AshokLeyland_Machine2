"""
Study_Cap_Annulus - a simpler cap seating measure (Uddi, 2026-11-27), study only: production is not changed.
Without a window; the same measure in a window: Study_Cap_Annulus_UI.py. The measure is described in _cap_annulus.py:
steps 1 - 2 (R-B, brightness r 25), hub centre from the hub's holes, cap mask (the production cap mask's steps - YOLO
boxes as MobileSAM prompts on a disc round the hub centre - or 24 points round the hub centre), ellipse, and the sum over 12 diameters of longer / shorter length in the annulus between the cap outline and
the hole circle (12 = centred).

Run on one folder at a time (all pictures in it, sub-folders not searched):
    python test\\machine2_dryrun\\Study_Cap_Annulus.py <folder> [--model DOST|DOSTPLUS|name] [--mask boxes|points|annulus]
                                     [--save] [--holes-gamma 1.0] [--mask-gamma 2.0] [--floor 1.0] [--sam <mobile_sam.pt>] [--holes morphology]
  --mask       : annulus (default since CHANGES 210) = a mask of only the annulus (prompts 5 px inside the hole circle,
                 avoiding the holes), an ANNULUS line per picture; boxes = the production cap mask's steps on a disc
                 round the hub centre; points = 24 points.
  --model name : DOSTPLUS if the file name contains it, else DOST (default DOST).
  --conf / --stability / --iou / --merge / --yolo-iou / --max-det / --multimask : the boxes method's YOLO
                 confidence, MobileSAM minimums, merge overlap, YOLO NMS IoU and maximum detections, 3 masks per box
                 (defaults since CHANGES 210: conf 0.05, iou 0.95, max_det 300, multimask on, pred_iou 0.15, stability
                 0.25, box_nms 0.90; --production: the cap check's); --overdetect: conf 0.10, NMS 0.80, max det 1000, 3 masks per box,
                 predicted IoU 0.72, stability 0.78, merge 0.88.
  --holes-gamma / --mask-gamma : gamma of the picture for the hole finder / for MobileSAM's masks (every method).
  --save       : marked pictures (outlines only, no values) and the masks in the hole circle (coloured, largest
                 painted first) to <folder>/cap_annulus_results/.
  --sam        : MobileSAM from this file (else through ModelManager, as production).
  --holes morphology : the old dark-blob hole finder (where the YOLO weights are missing - the sandbox).
Writes <folder>/cap_annulus_results/annulus_<time>.csv and prints a line per picture and a summary.
"""
import argparse
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _cap_annulus as A                                                     # noqa: E402

import cv2                                                                    # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Cap seating by the annulus (study)")
    ap.add_argument("folder")
    ap.add_argument("--model", default="DOST", choices=["DOST", "DOSTPLUS", "name"])
    ap.add_argument("--mask", default=A.STUDY_METHOD, choices=list(A.MASK_METHODS))
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--conf", type=float, default=None, help="YOLO confidence (boxes; else the properties value)")
    ap.add_argument("--stability", type=float, default=None, help="MobileSAM stability minimum (boxes)")
    ap.add_argument("--iou", type=float, default=None, help="MobileSAM predicted IoU minimum (boxes)")
    ap.add_argument("--merge", type=float, default=None, help="box overlap above which masks merge (boxes; 1 = none)")
    ap.add_argument("--yolo-iou", type=float, default=None, help="YOLO NMS IoU (boxes; production 0.7)")
    ap.add_argument("--max-det", type=int, default=None, help="YOLO maximum detections (boxes; production 300)")
    ap.add_argument("--multimask", action="store_true", help="3 MobileSAM masks per box (boxes; production 1)")
    ap.add_argument("--overdetect", action="store_true", help="Uddi's over-detection values (then the options above)")
    ap.add_argument("--production", action="store_true", help="the cap check's mask values (then the options above)")
    ap.add_argument("--holes-gamma", type=float, default=A.STUDY_HOLES_GAMMA, help="gamma of the picture for the hole finder (1 = none)")
    ap.add_argument("--mask-gamma", type=float, default=A.STUDY_MASK_GAMMA,
                    help="gamma of the picture given to MobileSAM for the masks (production 2.0)")
    ap.add_argument("--floor", type=float, default=1.0, help="px; the shorter length is at least this in a ratio")
    ap.add_argument("--sam", default=None)
    ap.add_argument("--holes", default=None, choices=["morphology"])
    args = ap.parse_args()
    A.configure(args.sam, args.holes)
    if args.production:
        A.setMaskParameters(**A.MASK_DEFAULTS)
    if args.overdetect:
        A.setMaskParameters(**A.MASK_OVERDETECT)
    mp = A.setMaskParameters(args.conf, args.stability, args.iou, args.merge, args.yolo_iou, args.max_det,
                             True if args.multimask else None)
    folder = pathlib.Path(args.folder)
    files = A.pictures(folder)
    if not files:
        sys.exit(f"No pictures in {folder}")
    resultsDir = folder / "cap_annulus_results"
    resultsDir.mkdir(exist_ok=True)
    csvPath = resultsDir / f"annulus_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    rows = []
    print(f"Study_Cap_Annulus on {len(files)} pictures in {folder} (model {args.model}, mask {args.mask} {mp}, hole finder {A.holeFinder()}, "
          f"gamma holes {args.holes_gamma} / masks {args.mask_gamma}, floor {args.floor} px)")
    print(f"{'file':<36} {'cap':<9} {'R-B':>6} {'bright':>6} {'hub':>15} {'R':>6} {'ell-hub':>12} "
          f"{'ellipse':>8} {'mask':>8}  note")
    for p in files:
        img = cv2.imread(str(p))
        if img is None:
            print(f"{p.name:<36} could not read")
            rows.append(dict(file=p.name, note="could not read"))
            continue
        res = A.measure(img, A.modelFor(args.model, p.name), args.floor, args.mask, args.holes_gamma, args.mask_gamma)
        res["file"] = p.name
        rows.append(res)
        if args.mask == "annulus":
            print(A.annulusLine(p.name, res), flush=True)
        if args.save:
            cv2.imwrite(str(resultsDir / f"{p.stem}_annulus.jpg"), A.marked(img, res))
            if res.get("masksPicture") is not None:
                cv2.imwrite(str(resultsDir / f"{p.stem}_masks.jpg"), res["masksPicture"])
        hub = f"({res['hub_x']}, {res['hub_y']})" if res["hub_x"] else "-"
        off = f"{res['ell_minus_hub_dx']} / {res['ell_minus_hub_dy']}" if res["ell_cx"] else "-"
        print(f"{p.name:<36} {res['cap_found']:<9} {float(res['rb_silver']):6.1f} {float(res['brightness']):6.1f} "
              f"{hub:>15} {res['hole_r'] or '-':>6} {off:>12} {res['score_ellipse'] or '-':>8} "
              f"{res['score_mask'] or '-':>8}  {res['seconds']} s{'  ' + res['note'] if res['note'] else ''}")
    A.writeCsv(csvPath, rows)
    print()
    print("\n".join(A.summaryLines(rows)))
    print(f"written: {csvPath}")


if __name__ == "__main__":
    main()
