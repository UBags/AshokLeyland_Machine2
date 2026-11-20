"""
DryRun_Hub_Holes_Refine - the hub's holes from the wide annulus refined for the split pin check (CHANGES 183): with
check="splitpin" (vision.hubholes.refine.checks) each hole of the set found is re-segmented by a MobileSAM box prompt
(FindHubHoles._refine) and the circle fitted again and gated; the cap and bung checks keep the unrefined holes.
checkHub()'s candidate sets and the box prompt are stood in (a ring of holes round (645.3, 347.3)), so no models are
needed.

R1 refine off -> the unrefined set, as before
R2 refine on -> the refined circle; unrefined centre, radius and the move kept for the log line
R3 the first holes set gives no outline mask -> the next holes set
R4 a hole moves more than sam.refine.move.max -> rejected (not found here: one set, two gammas)
R5 the refined circle's radius outside holes.circle.radius -> rejected
R6 refineFor (switch set to splitpin): "splitpin" yes; "cap", "bung", none no; findHoles passes the check on
R6b the default since CHANGES 184: refine.checks none - nothing refined
R7 the result cache keeps refined and unrefined apart (same picture)
R8 describe() names the refining (refined (moved .. px; unrefined ...))

Run from the project root:   python test/machine2_dryrun/DryRun_Hub_Holes_Refine.py
"""
import math
import os
import pathlib
import sys
import types

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
os.environ.setdefault("COSTHETA_PLC_VENDOR", "allenbradley" if "machine1" in str(pathlib.Path(__file__).parent) else "siemens")
sys.modules.setdefault("winsound", types.SimpleNamespace(PlaySound=lambda *a, **k: None, SND_FILENAME=0, SND_ASYNC=0, Beep=lambda *a: None))
from camera.FindHubHoles import FindHubHoles as F
import camera.CheckHubAndBottomBearing as CH

CH.determine_smart_gamma_order = lambda rgb: [1, 3]


class Seg:
    def preprocess_image(self, rgb, **k):
        return rgb, None


F._engine = staticmethod(lambda: Seg())
CX, CY = 645.3, 347.3


def ring(r, n, area, x0, y0, dy=0.0):
    return [dict(shape_info=dict(center=(CX - x0 + r * math.cos(2 * math.pi * i / n),
                                         CY + dy - y0 + r * math.sin(2 * math.pi * i / n))), area=area) for i in range(n)]


SCEN = {}


def fakeSets(seg, pre, n):
    x0 = max(0, seg.annular_center[0] - seg.annular_outer_radius); y0 = max(0, seg.annular_center[1] - seg.annular_outer_radius)
    masks, cands = [], []
    for gid, (r, area, var, dy) in enumerate(SCEN["sets"]):
        start = len(masks); masks += ring(r, n, area, x0, y0, dy)
        cands.append(dict(group_id=gid, indices=list(range(start, start + n)), distance_variance=var, K=n))
    return cands, masks, 0


def fakeRefine(rgb, points, model):
    """The box prompt stood in: SCEN['refine'] = callable(points, call number) -> refined points or None."""
    SCEN["calls"] += 1
    return SCEN["refine"](points, SCEN["calls"])


F._candidateSets = staticmethod(fakeSets)
F._refine = staticmethod(fakeRefine)
im = np.zeros((720, 1280, 3), np.uint8)
ok, total = 0, 0


def check(name, cond, info):
    global ok, total
    total += 1; ok += bool(cond); print(("PASS " if cond else "FAIL ") + name, info)


def run(sets, refine, refineFn=None, tag=0):
    SCEN.update(sets=sets, refine=refineFn or (lambda p, c: p), calls=0)
    F._cache.clear(); im[0, 0] = tag
    return F._holesInWideAnnulus(im.copy(), "DOST", (CX, CY), refine=refine)


def shifted(dy):
    return lambda p, c: [(x, y + dy) for x, y in p]


lower = [(148.0, 600, 1.0, 1.8)]                         # the darker lower part: centre 1.8 px low
r = run(lower, False)
check("R1 refine off: the unrefined set", r["found"] and abs(r["cy"] - (CY + 1.8)) < 0.05 and not r.get("refined"),
      (round(r["cx"], 2), round(r["cy"], 2)))
r = run(lower, True, shifted(-1.8))
check("R2 refine on: the refined circle, unrefined kept", r["found"] and r.get("refined") and abs(r["cy"] - CY) < 0.05
      and abs(r["unrefined"][1] - (CY + 1.8)) < 0.05 and abs(r["refineMoved"] - 1.8) < 0.05,
      (round(r["cy"], 2), tuple(round(v, 2) for v in r["unrefined"]), round(r["refineMoved"], 2)))
two = [(148.0, 600, 1.0, 1.8), (149.0, 650, 2.0, 0.0)]  # sorted by area: the 600 set first
r = run(two, True, lambda p, c: None if c == 1 else p)
check("R3 first set no outline mask -> the next holes set", r["found"] and abs(r["cy"] - CY) < 0.05 and SCEN["calls"] == 2,
      (round(r["cy"], 2), SCEN["calls"]))
r = run(lower, True, shifted(-7.0))
check("R4 a hole moved > move.max -> rejected", not r["found"] and "moved 7.0 px" in r["reason"], r["reason"][:120])
r = run([(136.5, 600, 1.0, 0.0)], True, lambda p, c: [(CX + (x - CX) * 133.0 / 136.5, CY + (y - CY) * 133.0 / 136.5)
                                             for x, y in p])   # r 136.5 -> 133 (moved 3.5 px)
check("R5 refined radius outside holes.circle.radius -> rejected", not r["found"] and "refined circle r" in r["reason"],
      r["reason"][:120])
default = F.REFINE_CHECKS
F.REFINE_CHECKS = "splitpin"                              # the switch as when on (off by default since CHANGES 184)
seen = []
original = F.findHolesWithSAM
F.findHolesWithSAM = staticmethod(lambda img, model, hub=None, refine=False: seen.append(refine) or dict(found=False))
for c in ("splitpin", "cap", "bung", None):
    F.findHoles(im, "DOST", check=c)
F.findHolesWithSAM = original
check("R6 refineFor: splitpin only; findHoles passes it on",
      [F.refineFor(c) for c in ("splitpin", " SplitPin ", "cap", "bung", None)] == [True, True, False, False, False]
      and seen == [True, False, False, False], seen)
F.REFINE_CHECKS = default
check("R6b default (CHANGES 184): no check refined", default.strip() == "none" and not F.refineFor("splitpin"), repr(default))
F._hubCheckCentre = staticmethod(lambda img, model, hub: ((CX, CY), "stand-in"))
SCEN.update(sets=lower, refine=shifted(-1.8), calls=0); F._cache.clear(); im[0, 0] = 99
a = F.findHolesWithSAM(im.copy(), "DOST", None, refine=False)
b = F.findHolesWithSAM(im.copy(), "DOST", None, refine=True)
check("R7 cache keeps refined and unrefined apart", abs(a["cy"] - (CY + 1.8)) < 0.05 and abs(b["cy"] - CY) < 0.05
      and b.get("refined") and not a.get("refined"), (round(a["cy"], 2), round(b["cy"], 2)))
text = F.describe(b)
check("R8 describe names the refining", "refined (moved 1.8 px; unrefined (" in text and "unrefined" not in F.describe(a), text)
print(f"{ok}/{total}")
print("RESULT:", "PASS" if ok == total else "FAIL")
sys.exit(0 if ok == total else 1)
