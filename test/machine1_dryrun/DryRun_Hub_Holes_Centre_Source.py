"""
DryRun_Hub_Holes_Centre_Source - where the hub holes' wide annulus is centred (CHANGES 182): with
vision.hubholes.centre.source=picture the centre comes from the picture being checked (pass 1: checkHub()'s steps round
hub.segment.center, any equally spaced set of the model's count; holes by the hole rule, else the bolts + the bolts
offset), the cycle's hub-check centre only when pass 1 finds no set; =hubcheck as before. checkHub()'s candidate sets
are stood in (a ring of holes at r 149 and / or bolts at r 190 round (645.3, 347.3)), so no models are needed.

C1 holes only -> the holes' circle centre
C2 bolts only (the holes split over checkHub()'s distance groups by an off centre) -> bolts centre + bolts offset
C3 holes and bolts, the bolts more evenly spaced -> the holes (they pass the hole rule)
C4 a hole-radius set with too much mask area -> none
C5 neither -> none
C6 source picture: the picture's centre, not the given hub-check centre
C7 source picture, none found: the given hub-check centre, with the reason
C8 source hubcheck: the given centre, as before

Run from the project root:   python test/machine2_dryrun/DryRun_Hub_Holes_Centre_Source.py
"""
import pathlib
import os, sys, types, math, numpy as np
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
os.environ.setdefault("COSTHETA_PLC_VENDOR", "allenbradley" if "machine1" in str(pathlib.Path(__file__).parent) else "siemens")
sys.modules.setdefault("winsound", types.SimpleNamespace(PlaySound=lambda *a, **k: None, SND_FILENAME=0, SND_ASYNC=0, Beep=lambda *a: None))
from camera.FindHubHoles import FindHubHoles as F
import camera.CheckHubAndBottomBearing as CH
CH.determine_smart_gamma_order = lambda rgb: [1, 3]
class Seg:
    def preprocess_image(self, rgb, **k): return rgb, None
F._engine = staticmethod(lambda: Seg())
def ring(cx, cy, r, n, area, x0, y0, phase=0):
    return [dict(shape_info=dict(center=(cx - x0 + r * math.cos(phase + 2 * math.pi * i / n), cy - y0 + r * math.sin(phase + 2 * math.pi * i / n))), area=area) for i in range(n)]
SCEN = {}
def fakeSets(seg, pre, n):
    x0 = max(0, seg.annular_center[0] - seg.annular_outer_radius); y0 = max(0, seg.annular_center[1] - seg.annular_outer_radius)
    masks, cands = [], []
    for gid, (r, area, var) in enumerate(SCEN["sets"]):
        start = len(masks); masks += ring(645.3, 347.3, r, n, area, x0, y0)
        cands.append(dict(group_id=gid, indices=list(range(start, start + n)), distance_variance=var, K=n))
    return cands, masks, 0
F._candidateSets = staticmethod(fakeSets)
im = np.zeros((720, 1280, 3), np.uint8)
ok = 0
def check(name, cond, info):
    global ok; ok += bool(cond); print(("PASS " if cond else "FAIL ") + name, info)
for i, (name, sets, expect) in enumerate([
        ("holes only", [(149.0, 600, 1.0)], ("holes", 645.3, 347.3)),
        ("bolts only (holes split by the off centre)", [(190.0, 1975, 1.0)], ("bolts", 645.3 - 0.7, 347.3 + 4.4)),
        ("holes and bolts: holes by the smaller area", [(190.0, 1975, 0.5), (149.0, 600, 1.0)], ("holes", 645.3, 347.3)),
        ("holes set too large in area ", [(149.0, 99999, 1.0)], (None,)),
        ("neither", [(120.0, 2500, 1.0)], (None,))]):
    SCEN["sets"] = sets; F._cache.clear(); im[0, 0] = i            # a new picture each time (cache key)
    r = F._centreFromPicture(im.copy(), "DOST")
    if expect[0] is None:
        check(name, not r["found"], r.get("reason", "")[:100])
    else:
        check(name, r["found"] and r["kind"] == expect[0] and abs(r["cx"] - expect[1]) < 0.05 and abs(r["cy"] - expect[2]) < 0.05,
              {k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()})
# _hubCheckCentre: picture first, the given centre only as the fallback
SCEN["sets"] = [(190.0, 1975, 1.0)]; F._cache.clear(); im[0, 0] = 50
c, src = F._hubCheckCentre(im.copy(), "DOST", {"cx": 644.3, "cy": 334.5, "count": 4, "kind": "bolts"})
check("source picture: the picture's centre, not the given one", abs(c[1] - 351.7) < 0.05, (c, src))
SCEN["sets"] = [(120.0, 2500, 1.0)]; F._cache.clear(); im[0, 0] = 51
c, src = F._hubCheckCentre(im.copy(), "DOST", {"cx": 644.3, "cy": 334.5, "count": 4, "kind": "bolts"})
check("source picture, none found: the given hub-check centre", c == (644.3, 334.5) and "this picture" in src, (c, src[:90]))
F.CENTRE_SOURCE = "hubcheck"; F._cache.clear(); im[0, 0] = 52
c, src = F._hubCheckCentre(im.copy(), "DOST", {"cx": 644.3, "cy": 334.5, "count": 4, "kind": "bolts"})
check("source hubcheck: the given centre (as before)", c == (644.3, 334.5) and src.startswith("the hub check (given)"), (c, src))
print(f"{ok}/8")
print("RESULT:", "PASS" if ok == 8 else "FAIL")
sys.exit(0 if ok == 8 else 1)
