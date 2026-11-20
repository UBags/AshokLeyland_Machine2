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

--hub-image PATH (CHANGES 177; "Hub picture..." in the windows): the hub picture of the same part (state 5) - the real
checkHub() on it gives the hub centre, as production's hub check does; without it, checkHub() on each picture itself.
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
              "), as in production; the hub centre from the real checkHub() on the hub picture (--hub-image / "
              "'Hub picture...'), else on each picture", flush=True)
    installHubPictureOption()
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
        try:
            _, ok, _ = CH.checkHubAndBottomBearing(anImage.copy(), QR["DOSTPLUS" if model == "DOSTPLUS" else "DOST"])
            _hubCheckCache[key] = dict(CH.lastHubCentre) if ok and CH.lastHubCentre else None
        except Exception as e:        # e.g. no YOLO weights (the sandbox): no hub centre, said so
            print(f"Hub check on the picture failed: {e}", flush=True)
            _hubCheckCache[key] = None
        while len(_hubCheckCache) > 12:
            _hubCheckCache.pop(next(iter(_hubCheckCache)))
    return _hubCheckCache[key]


# --------------------------------------------------------------------------------------------------------------------
# A hub picture as the hub centre (CHANGES 177): production takes the centre from this cycle's hub check (state 5) on
# the hub picture; a dry run has no cycle. With a hub picture chosen (--hub-image PATH, or "Hub picture..." in the
# windows) the real checkHub() runs once on it per model and its centre is passed to FindHubHoles.findHoles as the
# hub check's centre ("from the hub check (given)" in the HUBHOLES lines); the hub check failing on it means no centre
# -> holes not found -> the check NOT OK, as in production. Without a hub picture: checkHub() on each checked picture
# itself, as before (CHANGES 171). Only the SAM methods use the centre; morphology ignores it.
HUB_PICTURE = {"path": None, "image": None}
_hubPictureCentres = {}
_NO_CENTRE = {"cx": float("nan"), "cy": float("nan"), "count": 0, "kind": "none - the hub check failed on the hub picture"}


def hubImageFromArgs(argv=None):
    argv = sys.argv if argv is None else argv
    return argv[argv.index("--hub-image") + 1] if "--hub-image" in argv and argv.index("--hub-image") + 1 < len(argv) else None


def setHubPicture(path) -> str:
    """Use this picture's hub check as the hub centre (None: back to the picture itself). Returns a line to show."""
    import cv2
    _hubPictureCentres.clear()
    if not path:
        HUB_PICTURE.update(path=None, image=None)
        return "Hub centre: the hub check on each checked picture itself"
    image = cv2.imread(str(path))
    if image is None:
        HUB_PICTURE.update(path=None, image=None)
        return f"Hub picture could not be read: {path} - hub centre from each checked picture itself"
    HUB_PICTURE.update(path=str(path), image=image)
    return f"Hub centre: the hub check on {path}"


def hubCentreFromHubPicture(model):
    """The hub check's centre on the chosen hub picture for this model (cached), _NO_CENTRE when it failed, None when
    no hub picture is chosen."""
    if HUB_PICTURE["image"] is None:
        return None
    key = "DOSTPLUS" if model == "DOSTPLUS" else "DOST"
    if key not in _hubPictureCentres:
        centre = hubCheckOnPicture(HUB_PICTURE["image"], key)
        _hubPictureCentres[key] = dict(centre) if centre else dict(_NO_CENTRE)
        c = _hubPictureCentres[key]
        print(f"HUBPICTURE {key}: " + (f"centre ({c['cx']:.1f}, {c['cy']:.1f}) from {c.get('count', '?')} "
                                       f"{c.get('kind', '?')} on {HUB_PICTURE['path']}" if centre else
                                       f"the hub check did not pass on {HUB_PICTURE['path']} - no hub centre"), flush=True)
    return _hubPictureCentres[key]


def installHubPictureOption():
    """Wrap FindHubHoles.findHoles (once): with a hub picture chosen, its hub check's centre is given to the finder."""
    from camera.FindHubHoles import FindHubHoles
    if getattr(FindHubHoles.findHoles, "_hubPictureWrapper", False):
        return
    original = FindHubHoles.findHoles

    def findHolesWithHubPicture(anImage, model, hubCentre=None):
        if hubCentre is None:
            hubCentre = hubCentreFromHubPicture(model)
        return original(anImage, model, hubCentre)

    findHolesWithHubPicture._hubPictureWrapper = True
    FindHubHoles.findHoles = staticmethod(findHolesWithHubPicture)
    path = hubImageFromArgs()
    if path:
        print(setHubPicture(path), flush=True)
        if HUB_PICTURE["image"] is None:      # a hub picture asked for on the command line must be used, not skipped
            sys.exit(f"--hub-image {path}: the picture could not be read - stopped (check the path)")


def addHubPictureControl(bar, root, report=print):
    """'Hub picture...' and 'No hub picture' buttons and a label, packed into a window's button bar."""
    import tkinter as tk
    from tkinter import filedialog, ttk
    shown = tk.StringVar(master=root, value=("hub: " + pathlibName(HUB_PICTURE["path"])) if HUB_PICTURE["path"]
                         else "hub: each picture itself")

    def choose():
        f = filedialog.askopenfilename(title="Hub picture of the same part (state 5)", parent=root,
                                       filetypes=[("Pictures", "*.png *.jpg *.jpeg *.bmp")])
        if f:
            line = setHubPicture(f)
            shown.set("hub: " + pathlibName(HUB_PICTURE["path"]) if HUB_PICTURE["path"] else "hub: each picture itself")
            report(line)

    def clear():
        report(setHubPicture(None))
        shown.set("hub: each picture itself")

    ttk.Button(bar, text="Hub picture...", command=choose).pack(side="left", padx=(8, 2))
    ttk.Button(bar, text="No hub picture", command=clear).pack(side="left", padx=2)
    ttk.Label(bar, textvariable=shown, width=26).pack(side="left", padx=4)
    return shown


def pathlibName(path) -> str:
    import pathlib
    return pathlib.Path(path).name if path else ""


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
