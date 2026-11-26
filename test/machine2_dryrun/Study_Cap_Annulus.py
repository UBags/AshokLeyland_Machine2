"""
Study_Cap_Annulus - a simpler cap seating measure (Uddi, 2026-11-27), study only: production is not changed.
Without a window; the same measure in a window: Study_Cap_Annulus_UI.py. The measure is described in _cap_annulus.py:
steps 1 - 2 (R-B, brightness r 25), hub centre from the hub's holes, cap mask by MobileSAM from 24 points round the hub
centre, ellipse, and the sum over 12 diameters of longer / shorter length in the annulus between the cap outline and
the hole circle (12 = centred).

Run on one folder at a time (all pictures in it, sub-folders not searched):
    python test\\machine2_dryrun\\Study_Cap_Annulus.py <folder> [--model DOST|DOSTPLUS|name] [--save] [--gamma 1.0]
                                                          [--floor 1.0] [--sam <mobile_sam.pt>] [--holes morphology]
  --model name : DOSTPLUS if the file name contains it, else DOST (default DOST).
  --save       : marked pictures (outlines only, no values) to <folder>/cap_annulus_results/.
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
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--gamma", type=float, default=1.0, help="gamma of the picture given to MobileSAM (1 = none)")
    ap.add_argument("--floor", type=float, default=1.0, help="px; the shorter length is at least this in a ratio")
    ap.add_argument("--sam", default=None)
    ap.add_argument("--holes", default=None, choices=["morphology"])
    args = ap.parse_args()
    A.configure(args.sam, args.holes)
    folder = pathlib.Path(args.folder)
    files = A.pictures(folder)
    if not files:
        sys.exit(f"No pictures in {folder}")
    resultsDir = folder / "cap_annulus_results"
    resultsDir.mkdir(exist_ok=True)
    csvPath = resultsDir / f"annulus_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    rows = []
    print(f"Study_Cap_Annulus on {len(files)} pictures in {folder} (model {args.model}, hole finder {A.holeFinder()}, "
          f"gamma {args.gamma}, floor {args.floor} px)")
    print(f"{'file':<36} {'cap':<9} {'R-B':>6} {'bright':>6} {'hub':>15} {'R':>6} {'ell-hub':>12} "
          f"{'ellipse':>8} {'mask':>8}  note")
    for p in files:
        img = cv2.imread(str(p))
        if img is None:
            print(f"{p.name:<36} could not read")
            rows.append(dict(file=p.name, note="could not read"))
            continue
        res = A.measure(img, A.modelFor(args.model, p.name), args.gamma, args.floor)
        res["file"] = p.name
        rows.append(res)
        if args.save:
            cv2.imwrite(str(resultsDir / f"{p.stem}_annulus.jpg"), A.marked(img, res))
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
