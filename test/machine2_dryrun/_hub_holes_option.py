"""
--holes sam|morphology for the dry runs whose checks find the hub's holes (cap, bung, split pin; CHANGES 164).

  sam (default)  as in production: camera/FindHubHoles.findHolesWithSAM (YOLO + MobileSAM; needs models/MobileSAM with
                 the YOLO weights - the laptop)
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
        print("Hub holes: YOLO + MobileSAM (FindHubHoles.findHolesWithSAM), as in production", flush=True)
    return method
