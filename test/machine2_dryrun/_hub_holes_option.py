"""
--holes sam|morphology for the dry runs whose checks find the hub's holes (cap, bung, split pin; CHANGES 164).

  sam (default)  as in production: camera/FindHubHoles.findHolesWithSAM (CHANGES 169: YOLO + MobileSAM in the wide
                 annulus round the hub-check centre; needs models/MobileSAM with the YOLO weights - the laptop). A dry
                 run has no hub check in its cycle: the real checkHub() is run once on the picture itself and its
                 centre used (FindHubHoles.hubCentreFromPicture), as production uses the state-5 hub check's
  sam_slow       FindHubHoles.findHolesWithSAM_Slow (CHANGES 164 - 166)
  morphology     the dark-blob finder of CHANGES 152 - 163 (FindHubHoles.findHolesWithMorphology) with the offsets that
                 were measured with it (below), so that a run without the YOLO weights (the sandbox) checks the rest of
                 the check as before. The offsets now in the properties belong to the SAM centres.
"""
import sys

# offsets measured with the dark-blob finder (CHANGES 152, 153, 163): (dx, dy) per model
MORPHOLOGY_OFFSETS = {
    "cap": {"DOST": (0.2, -0.1), "DOSTPLUS": (-0.6, -5.3)},
    "bung": {"DOST": (4.9, -13.7), "DOSTPLUS": (4.2, -16.0)},
    "splitpin": {"DOST": (1.8, 1.6), "DOSTPLUS": (1.8, 1.6)},
}


def holesMethod(argv=None) -> str:
    argv = sys.argv if argv is None else argv
    return argv[argv.index("--holes") + 1] if "--holes" in argv and argv.index("--holes") + 1 < len(argv) else "sam"


def applyHolesOption(method: str | None = None) -> str:
    """Sets FindHubHoles.METHOD (and, for morphology, the offsets measured with it); returns the method."""
    method = (method or holesMethod()).strip().lower()
    from camera.FindHubHoles import FindHubHoles
    FindHubHoles.METHOD = method
    if method == "morphology":
        from camera.CheckCap import CheckCap
        import camera.BungSegmenter as BS
        from camera.CheckSplitPinAndWasher import CheckSplitPinAndWasher as SP
        o = MORPHOLOGY_OFFSETS
        CheckCap.SEATING_HUB_DISTANCE_OFFSET_CAP_DOST, CheckCap.SEATING_HUB_DISTANCE_OFFSET_CAP_DOSTPLUS = \
            o["cap"]["DOST"], o["cap"]["DOSTPLUS"]
        BS.SEATING_HUB_OFFSET_DOST, BS.SEATING_HUB_OFFSET_DOSTPLUS = o["bung"]["DOST"], o["bung"]["DOSTPLUS"]
        SP.HUB_OFFSET_DOST, SP.HUB_OFFSET_DOSTPLUS = o["splitpin"]["DOST"], o["splitpin"]["DOSTPLUS"]
        print("Hub holes: dark-blob finder (--holes morphology) with the offsets measured with it - not the production "
              "method", flush=True)
    else:
        FindHubHoles.hubCentreFromPicture = staticmethod(hubCheckOnPicture)
        print(f"Hub holes: YOLO + MobileSAM (FindHubHoles.{'findHolesWithSAM_Slow' if method == 'sam_slow' else 'findHolesWithSAM'}"
              "), as in production; the hub centre from the real checkHub() on each picture", flush=True)
    return method


QR = {"DOST": "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$",
      "DOSTPLUS": "8201206$400112VA1C$22881$15.10.2026$C5211702$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$"}
_hubCheckCache = {}


def hubCheckOnPicture(anImage, model):
    """The real checkHub() on this picture (dry runs: no hub check in their cycle): its hub centre (holes or bolts) or
    None when it did not pass. Kept for the last 12 pictures (the split pin check gets each picture twice)."""
    import zlib
    import numpy as np
    from camera.CheckHubAndBottomBearing import CheckHubAndBottomBearing as CH
    key = (zlib.crc32(np.ascontiguousarray(anImage[::4, ::4]).tobytes()), anImage.shape, model)
    if key not in _hubCheckCache:
        _, ok, _ = CH.checkHubAndBottomBearing(anImage.copy(), QR["DOSTPLUS" if model == "DOSTPLUS" else "DOST"])
        _hubCheckCache[key] = dict(CH.lastHubCentre) if ok and CH.lastHubCentre else None
        while len(_hubCheckCache) > 12:
            _hubCheckCache.pop(next(iter(_hubCheckCache)))
    return _hubCheckCache[key]


class HubHolesRecorder:
    """Records every FindHubHoles.findHoles result while installed, so the dry-run windows can show the hub's holes the
    check used (draw: outlines only - a ring round each hole and a cross at the circle's centre, blue; text: the
    FindHubHoles.describe lines for the list and the terminal)."""

    def __init__(self):
        import threading
        from camera.FindHubHoles import FindHubHoles
        self._lock = threading.Lock()
        self._results = []
        original = FindHubHoles.findHoles

        def recording(anImage, model, hubCentre=None):
            result = original(anImage, model, hubCentre)
            with self._lock:
                self._results.append(dict(result))
            return result

        FindHubHoles.findHoles = staticmethod(recording)
        self._describe = FindHubHoles.describe

    def clear(self):
        with self._lock:
            self._results.clear()

    def take(self):
        """The distinct results since the last clear / take (the same circle found twice counted once)."""
        with self._lock:
            results, self._results = self._results, []
        seen, out = set(), []
        for r in results:
            key = (round(r["cx"], 1), round(r["cy"], 1)) if r.get("found") else ("not found", r.get("reason"))
            if key not in seen:
                seen.add(key)
                out.append(r)
        return out

    def text(self, results) -> str:
        return " | ".join(self._describe(r) for r in results) or "hub holes not looked for"

    @staticmethod
    def draw(image, results, colour=(255, 0, 0)):
        """A copy of the picture with each found hole ringed and the hole circle's centre crossed (no text)."""
        import cv2
        out = image.copy()
        for r in results:
            if not r.get("found"):
                continue
            ring = 14 if r.get("count") == 5 else 10
            for x, y in r.get("points") or []:
                cv2.circle(out, (int(round(x)), int(round(y))), ring + 4, colour, 1)
            cv2.drawMarker(out, (int(round(r["cx"])), int(round(r["cy"]))), colour, cv2.MARKER_CROSS, 14, 1)
        return out
