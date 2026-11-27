"""
_bung_hubcentre_study - DOSTPLUS bung check placed on the hub centre (Uddi, 2026-11-27, CHANGES 215). A study: used only
by DryRun_Bung_Check_UI.py; production (camera/BungSegmenter.py, CheckBung, CheckNoCapBung) is not changed. DOST is not
studied: its check runs exactly as in production.

For a DOSTPLUS picture:
  1. The hub's 5 holes in this picture FIRST (FindHubHoles.findHoles, as production's seating step finds them; the
     window's "Hub picture..." centre is its fallback, as the cycle's hub check is in production): centre H, radius R
     of the circle through them. Not found -> NOT OK.
  2. The bung's holes by the production code (CheckBung / CheckNoCapBung, seating off), with
       - the segmented disc = the hub-hole circle (centre H, radius R) instead of r vision.<vendor>.dostplus.bung.outer.
         disc.radius (250) round the fixed vision.<vendor>.dostplus.bung.outer.disc.center (643, 334): the bung is always
         inside the hub-hole circle;
       - the hole-search disc = r vision.<vendor>.dostplus.bung.inner.disc.radius (135) round H.
  3. Seating, measured here (the production step's measures, with the reference oval moved):
       - hole-pattern centre to the seated position (H + vision.<vendor>.dostplus.bung.seating.hub.offset, as production);
       - the top-face mask (MobileSAM, prompted at the hole-pattern centre, the smallest mask of face radius
         seating.face.radius.min - max): roundness (minor / major axis), area / fitted-ellipse area;
       - the share of the face outside the reference oval (vision.<vendor>.dostplus.bung.seating.reference.oval) centred
         on H itself (Uddi: on the hub centre; the seated face sits ~17 px off H - the camera angle - so this share is
         larger than production's and its limit is to be recalibrated). The share with the oval at production's seated
         position is printed beside it.
     Verdict: Uddi's rule of 2026-11-27 (STUDY_RULE_NOT_OK / STUDY_RULE_OK, CHANGES 216; from his 10 good / 5 bad
     plant pictures): stage 1 - any -> NOT OK; stage 2 - any -> OK; else NOT OK. No hole pattern or no top-face mask -> NOT OK.
  4. Measure, then decide (Uddi): every value per picture on a "STUDY" line; with pictures in subfolders named OK and
     NOT_OK (or GOOD / BAD) the summary at the end gives, per value, the OK and NOT OK ranges, the worst OK and the closest
     NOT OK, and a limit midway between them where they separate.
"""
import math
import time

import cv2
import numpy as np

# Uddi's rule of 2026-11-27 17:38 (CHANGES 216), from his DOSTPLUS plant pictures (10 good / 5 bad):
#   NOT OK if offset > 26 px or roundness < 0.945 or outside (oval on H) > 11 % or outside (oval at H + offset) > 12 %;
#   else OK if offset < 18 px or roundness > 0.955 or outside (oval on H) < 7 % or outside (oval at H + offset) < 7 %;
#   else NOT OK.
# (label, values key, operator, limit) - edit here to try other limits.
STUDY_RULE_NOT_OK = (("hole centre to seated position", "offset", ">", 26.0),
                     ("face roundness", "roundness", "<", 0.945),
                     ("face outside the oval on H", "outside", ">", 0.11),
                     ("face outside the oval at H + offset", "outside_at_seated", ">", 0.12))
STUDY_RULE_OK = (("hole centre to seated position", "offset", "<", 18.0),
                 ("face roundness", "roundness", ">", 0.955),
                 ("face outside the oval on H", "outside", "<", 0.07),
                 ("face outside the oval at H + offset", "outside_at_seated", "<", 0.07))
STUDY_LIMITS = {"NOT OK if any": [f"{k} {op} {v:g}" for _, k, op, v in STUDY_RULE_NOT_OK],
                "else OK if any": [f"{k} {op} {v:g}" for _, k, op, v in STUDY_RULE_OK]}
# per value: (key, label, kind) - kind "max": OK when value <= limit; "min": OK when value >= limit; "range": both sides
VALUES = (("offset", "hole centre to seated position (px)", "max"),
          ("roundness", "face roundness", "min"),
          ("outside", "face outside the oval on H", "max"),
          ("outside_at_seated", "face outside the oval on H + offset", "max"),
          ("fit_ratio", "face / fitted ellipse (info)", "range"),
          ("face_radius", "face radius (px, info)", "range"))
OK_FOLDERS = ("ok", "good")
NOT_OK_FOLDERS = ("not_ok", "notok", "not ok", "bad", "nok")


def labelOf(path) -> str:
    """'OK' / 'NOT OK' from the picture's folder name, '' when unlabelled."""
    name = path.parent.name.strip().lower()
    if name in NOT_OK_FOLDERS:
        return "NOT OK"
    if name in OK_FOLDERS:
        return "OK"
    return ""


class DostplusHubCentreStudy:
    """One instance per window. begin() before the production check, end() after it (always, in a finally)."""

    def __init__(self, BS):
        self.BS = BS
        self.hub = None
        self.saved = None
        self.rows = []          # (label, file, dict of values or None, verdict, reason)
        self.seconds = {}

    # ---------------------------------------------------------------- 1. hub centre first, discs placed on it
    def begin(self, bgr) -> tuple:
        """Find the hub's holes; place the segmented disc and the hole-search disc on the hub centre (DOSTPLUS values,
        which CheckBung's useModel then takes); production's seating step switched off. -> (found, reason)."""
        from camera.FindHubHoles import FindHubHoles
        BS = self.BS
        t = time.perf_counter()
        self.hub = FindHubHoles.findHoles(bgr, "DOSTPLUS", None, check="bung")
        self.seconds = {"hub holes": time.perf_counter() - t}
        if not self.hub.get("found"):
            return False, f"the hub's 5 holes not found ({self.hub.get('reason')})"
        self.saved = {name: getattr(BS, name) for name in
                      ("OUTER_DISC_CENTER_DOSTPLUS", "OUTER_DISC_RADIUS_DOSTPLUS", "SEATING_CHECK_DOSTPLUS")}
        BS.OUTER_DISC_CENTER_DOSTPLUS = (int(round(self.hub["cx"])), int(round(self.hub["cy"])))
        BS.OUTER_DISC_RADIUS_DOSTPLUS = int(round(self.hub["radius"]))
        BS.SEATING_CHECK_DOSTPLUS = False
        return True, ""

    def end(self) -> None:
        if self.saved:
            for name, value in self.saved.items():
                setattr(self.BS, name, value)
        self.saved = None
        self.BS.useModel("DOSTPLUS")

    @property
    def centre(self):
        return (float(self.hub["cx"]), float(self.hub["cy"])) if self.hub and self.hub.get("found") else None

    # ---------------------------------------------------------------- 3. seating, oval on the hub centre
    def seating(self, rgb, components) -> dict:
        """The production seating measures with the reference oval centred on the hub centre. Call between begin and
        end (the hole components are in the inner-disc coordinates of the placed disc)."""
        BS = self.BS
        t = time.perf_counter()
        hx, hy = self.centre
        dx, dy = BS.SEATING_HUB_OFFSET_DOSTPLUS
        seated = (hx + float(dx), hy + float(dy))
        centre = BS.hole_pattern_centre(components)
        out = dict(hub=(hx, hy), seated_at=seated, hole_centre=centre, offset=None, roundness=None, fit_ratio=None,
                   outside=None, outside_at_seated=None, face_radius=None, face_contour=None)
        if centre is None:
            return out
        out["offset"] = float(math.hypot(centre[0] - seated[0], centre[1] - seated[1]))
        predictor = BS._seating_predictor()
        predictor.set_image(rgb)
        import torch
        with torch.inference_mode():
            masks, _, _ = predictor.predict(point_coords=np.array([centre], dtype=np.float32),
                                            point_labels=np.array([1]), multimask_output=True)
        faces = []
        for mask in masks:
            contours, _ = cv2.findContours(mask.astype(np.uint8) * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
            if not contours:
                continue
            c = max(contours, key=cv2.contourArea)
            if len(c) < 5:
                continue
            ellipse = cv2.fitEllipse(c)
            (_, _), (a, b), _ = ellipse
            r = (a + b) / 4.0
            if BS.SEATING_FACE_RADIUS_MIN_DOSTPLUS <= r <= BS.SEATING_FACE_RADIUS_MAX_DOSTPLUS and max(a, b) > 0:
                faces.append((r, min(a, b) / max(a, b), c, ellipse))
        if faces:
            r, roundness, contour, ellipse = min(faces, key=lambda f: f[0])
            h, w = rgb.shape[:2]
            face = np.zeros((h, w), np.uint8)
            cv2.drawContours(face, [contour], -1, 1, thickness=cv2.FILLED)
            area = int(face.sum())
            (_, _), (a, b), _ = ellipse
            oval = BS.SEATING_REFERENCE_OVAL_DOSTPLUS

            def outsideOf(at):
                ref = np.zeros((h, w), np.uint8)
                cv2.ellipse(ref, ((float(at[0]), float(at[1])), (float(oval[0]), float(oval[1])), float(oval[2])), 1, -1)
                return float((face & (1 - ref)).sum()) / area if area else 1.0

            out.update(face_radius=float(r), roundness=float(roundness), face_contour=contour,
                       fit_ratio=float(area / (math.pi * a * b / 4.0)) if a > 0 and b > 0 else 0.0,
                       outside=outsideOf((hx, hy)), outside_at_seated=outsideOf(seated))
        self.seconds["seating"] = time.perf_counter() - t
        return out

    @staticmethod
    def verdict(values) -> tuple:
        """(seated, reason) by Uddi's rule (STUDY_RULE_NOT_OK, STUDY_RULE_OK)."""
        if values.get("hole_centre") is None:
            return False, "no hole pattern"
        if values.get("roundness") is None:
            return False, "no top-face mask of face radius"

        def hits(rule):
            out = []
            for label, key, op, limit in rule:
                v = float(values[key])
                if (v > limit) if op == ">" else (v < limit):
                    shown = f"{v:.1%}" if key.startswith("outside") else f"{v:.3g}"
                    lim = f"{limit:.0%}" if key.startswith("outside") else f"{limit:g}"
                    out.append(f"{label} {shown} {op} {lim}")
            return out
        bad = hits(STUDY_RULE_NOT_OK)
        if bad:
            return False, "stage 1 (any -> NOT OK): " + "; ".join(bad)
        good = hits(STUDY_RULE_OK)
        if good:
            return True, "stage 2 (any -> OK): " + "; ".join(good)
        return False, "stage 3: no stage-1 and no stage-2 condition met"

    @staticmethod
    def line(values) -> str:
        def f(key, fmt):
            v = values.get(key)
            return "-" if v is None else format(v, fmt)
        hub = values.get("hub")
        return (f"hub ({hub[0]:.1f}, {hub[1]:.1f}) " if hub else "") + (
            f"offset {f('offset', '.1f')} px, roundness {f('roundness', '.3f')}, fit {f('fit_ratio', '.3f')}, "
            f"outside on H {f('outside', '.1%')} (on H + offset {f('outside_at_seated', '.1%')}), "
            f"face r {f('face_radius', '.1f')}")

    # ---------------------------------------------------------------- 4. summary over labelled pictures
    def record(self, label, name, values, verdict, reason):
        self.rows.append((label, name, values, verdict, reason))

    def summaryLines(self) -> list:
        if not self.rows:
            return []
        lines = [f"STUDY SUMMARY - DOSTPLUS on the hub centre, {len(self.rows)} picture(s); limits {STUDY_LIMITS}"]
        labelled = [r for r in self.rows if r[0]]
        if labelled:
            wrong = [r for r in labelled if (r[3] == "OK") != (r[0] == "OK")]
            lines.append(f"  with the study limits: {len(labelled) - len(wrong)} of {len(labelled)} labelled pictures as "
                         f"labelled" + ("; wrong: " + ", ".join(f"{r[1]} ({r[0]} -> {r[3]}: {r[4]})" for r in wrong)
                                        if wrong else ""))
        for key, label, kind in VALUES:
            ok = [r[2][key] for r in self.rows if r[0] == "OK" and r[2] and r[2].get(key) is not None]
            bad = [r[2][key] for r in self.rows if r[0] == "NOT OK" and r[2] and r[2].get(key) is not None]
            if not ok and not bad:
                continue
            text = f"  {label}: OK {len(ok)} [{min(ok):.3f} - {max(ok):.3f}]" if ok else f"  {label}: OK none"
            text += f", NOT OK {len(bad)} [{min(bad):.3f} - {max(bad):.3f}]" if bad else ", NOT OK none with a value"
            if ok and bad and kind == "max":
                text += (f"; worst OK {max(ok):.3f}, closest NOT OK {min(bad):.3f} -> "
                         + (f"limit {(max(ok) + min(bad)) / 2:.3f}" if max(ok) < min(bad) else "OVERLAP"))
            elif ok and bad and kind == "min":
                text += (f"; worst OK {min(ok):.3f}, closest NOT OK {max(bad):.3f} -> "
                         + (f"limit {(min(ok) + max(bad)) / 2:.3f}" if min(ok) > max(bad) else "OVERLAP"))
            lines.append(text)
        stages = {}
        for r in self.rows:
            if r[0] == "NOT OK" and (r[2] is None or r[2].get("roundness") is None):
                stages[r[4]] = stages.get(r[4], 0) + 1
        for reason, n in stages.items():
            lines.append(f"  NOT OK pictures stopped before the seating values: {n} x {reason}")
        return lines
