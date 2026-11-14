# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
VisionFrame - where this cycle's later vision checks look, worked out from the knuckle picture (Machine 2).

The knuckle check (state 3, camera/KnuckleLocator.py) finds the spindle, the two machined bosses with the fixture pins
in them, and the lower arm. From them it builds the cycle's FRAME:

    camera movement (from the pins, which do not move with the part):
        rotation  = pin-line angle - its usual angle for this type / side
        scale     = pin distance / its usual distance for this type / side
        T_cam(p)  = pin midpoint + scale * R(rotation) * (p - usual pin midpoint)
    anchor S   = T_cam(usual spindle of this type / side): where the pins put the spindle. The spindle mask is the
                 cross-check (it agrees within +-1 px on the 13 Nov pictures; more than ANCHOR_AGREEMENT_PX apart is
                 logged). The pins are used, not the mask, because the mask's centre moves 2-3 px with a zoom or a
                 re-sampled picture, while the two pins' midpoint stays within 0.5 px; the knuckle sits on the pins, so
                 the part cannot be off them.
    centre of every later check:
        place(P) = S + scale * R(rotation) * (P - REF_SPINDLE)

P is the check's centre at the reference camera position (13 Nov 2026, the positions in docs/vision_positions_machine2.md)
and REF_SPINDLE the spindle position those centres were set with. With no camera movement and the spindle at
REF_SPINDLE, place(P) = P. The hub, bearing, nut, bung and cap all sit on the spindle, so their centres follow it.

The reference centres are in CENTRES below (in the code, not in ApplicationConfiguration.properties). With a frame,
applyToChecks() writes the placed centres into the checks' constants before each check; with no frame (no knuckle
located in this cycle - Machine 1, the regions knuckle method, or the camera server restarted during a cycle) it puts
back the values from the properties file, as before. The frame is part of the cycle's state (ImageProcessor.visionFrame)
and is dropped at the end of a cycle (completed or aborted), at a new cycle's knuckle check and at a knuckle retake.

Radii and sizes are not changed (the scale is within 0.1 % on the 13 Nov pictures).
"""

import math
import sys
from typing import Any, Dict, Optional, Tuple

REF_SPINDLE: Tuple[float, float] = (642.2, 359.1)

# The pins (dark core = bore + fixture pin, inside each machined boss) per type and side at the reference camera position:
# distance (px), angle of the line left pin -> right pin (deg), pin midpoint, spindle (user's laptop run, 13 Nov pictures).
NOMINAL_PINS: Dict[Tuple[str, str], Dict[str, Any]] = {
    ("DOST", "LHS"): dict(distance=400.35, angle=0.65, pin_mid=(644.15, 194.93), spindle=(643.2, 359.8)),
    ("DOST", "RHS"): dict(distance=400.17, angle=0.68, pin_mid=(643.75, 192.95), spindle=(641.5, 357.1)),
    ("DOSTPLUS", "LHS"): dict(distance=394.26, angle=0.69, pin_mid=(643.70, 199.65), spindle=(642.1, 359.1)),
    ("DOSTPLUS", "RHS"): dict(distance=394.64, angle=0.77, pin_mid=(643.62, 200.00), spindle=(642.3, 360.3)),
}

# The spindle mask is expected within this of where the pins put the spindle (spread measured: +-1 px); further away
# it is noted in the frame (spindleCheck) and logged; the pins' spindle is the anchor either way.
ANCHOR_AGREEMENT_PX = 6.0

# The later checks' centres at the reference camera position (13 Nov 2026), with the spindle at REF_SPINDLE.
# name: (module, class or None, attributes, reference (x, y)). Attributes: one name holding an (x, y) tuple, or two
# names holding x and y.
CENTRES: Dict[str, Tuple[str, Optional[str], Tuple[str, ...], Tuple[float, float]]] = {
    "hub.segment.center": ("camera.HubAndBearingSegmenter", None, ("HUB_SEGMENT_CENTER_X", "HUB_SEGMENT_CENTER_Y"),
                           (645, 334)),
    "hub.centre.check": ("camera.CheckHubAndBottomBearing", None, ("HUB_CENTRE_CHECK_X", "HUB_CENTRE_CHECK_Y"),
                         (644, 340)),
    "topbearing.annular.center": ("camera.CheckTopBearing", "CheckTopBearing", ("ANNULAR_CENTER_X", "ANNULAR_CENTER_Y"),
                                  (642, 345)),
    "nutwasher.nut.detection.center": ("camera.CheckNutAndPlateWasher", "CheckNutAndPlateWasher",
                                       ("NUT_DETECTION_CENTER",), (644, 344)),
    "nobunk.nut.detection.center": ("camera.CheckNoBunk", "CheckNoBunk", ("NUT_DETECTION_CENTER",), (644, 344)),
    "bunk.outer.disc.center": ("camera.BunkSegmenter", None, ("OUTER_DISC_CENTER",), (646, 328)),
    "bunk.seating.seated.centre.dost": ("camera.BunkSegmenter", None, ("SEATING_SEATED_CENTRE_DOST",), (646, 325)),
    "bunk.seating.seated.centre.dostplus": ("camera.BunkSegmenter", None, ("SEATING_SEATED_CENTRE_DOSTPLUS",),
                                            (647, 330)),
    "cap.center": ("camera.CheckCap", "CheckCap", ("CENTER",), (644, 340)),
}


def _log(level: str, message: str) -> None:
    try:
        from logutils.SlaveLoggers import logBoth
        from logutils.Logger import MessageType
        logBoth(level, "camera.VisionFrame", message, MessageType.GENERAL if level == 'logInfo' else MessageType.RISK)
    except Exception:
        print(message, flush=True)


def _rotate(dx: float, dy: float, degrees: float, scale: float) -> Tuple[float, float]:
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    return scale * (c * dx - s * dy), scale * (s * dx + c * dy)


# ------------------------------------------------------------------------------------------------------------- frame
def buildFrame(modelType: str, side: str, pins: Tuple[Tuple[float, float], Tuple[float, float]],
               spindleMask: Optional[Tuple[float, float]]) -> Optional[Dict[str, Any]]:
    """The frame from the two pin centres (left, right), the type / side and the spindle mask centre (None if no
    usable spindle mask). None if the type / side has no reference."""
    nom = NOMINAL_PINS.get((modelType, side))
    if nom is None or pins is None:
        return None
    (lx, ly), (rx, ry) = pins
    distance = math.hypot(rx - lx, ry - ly)
    angle = math.degrees(math.atan2(ry - ly, rx - lx))
    rotation = angle - nom["angle"]
    scale = distance / nom["distance"]
    mid = ((lx + rx) / 2.0, (ly + ry) / 2.0)

    def camera(p):
        dx, dy = _rotate(p[0] - nom["pin_mid"][0], p[1] - nom["pin_mid"][1], rotation, scale)
        return mid[0] + dx, mid[1] + dy

    anchor = camera(nom["spindle"])
    cameraShift = tuple(a - b for a, b in zip(camera(REF_SPINDLE), REF_SPINDLE))
    if spindleMask is None:
        source, maskOffset = "pins (spindle mask not usable)", None
    else:
        maskOffset = (round(spindleMask[0] - anchor[0], 2), round(spindleMask[1] - anchor[1], 2))
        apart = math.hypot(*maskOffset)
        source = "pins, spindle mask %.1f px away%s" % (apart, "" if apart <= ANCHOR_AGREEMENT_PX
                                                          else " - MORE THAN %g px: check the part" % ANCHOR_AGREEMENT_PX)
    return dict(type=modelType, side=side, anchor=(round(anchor[0], 2), round(anchor[1], 2)), anchorSource=source,
                spindleMaskOffset=maskOffset,
                rotation=round(rotation, 3), scale=round(scale, 5),
                cameraShift=(round(cameraShift[0], 2), round(cameraShift[1], 2)),
                pinMid=(round(mid[0], 2), round(mid[1], 2)), pinDistance=round(distance, 2),
                pinAngle=round(angle, 3), refSpindle=REF_SPINDLE)


def place(frame: Dict[str, Any], point: Tuple[float, float]) -> Tuple[float, float]:
    """A point at the reference camera position (spindle at REF_SPINDLE) -> this cycle's picture."""
    dx, dy = _rotate(point[0] - REF_SPINDLE[0], point[1] - REF_SPINDLE[1], frame["rotation"], frame["scale"])
    return frame["anchor"][0] + dx, frame["anchor"][1] + dy


def placedCentres(frame: Optional[Dict[str, Any]]) -> Dict[str, Tuple[int, int]]:
    """Every later check's centre for this frame (rounded to whole pixels, as in the properties)."""
    if not frame:
        return {}
    return {name: tuple(int(round(v)) for v in place(frame, ref)) for name, (_, _, _, ref) in CENTRES.items()}


def withinLimits(frame: Dict[str, Any], maxShift: float, maxRotation: float, maxZoomChange: float) -> Tuple[bool, str]:
    """Camera movement small enough for the later checks (their search areas are 80-250 px; a bigger move means
    the camera was knocked and must be re-set)."""
    shift = math.hypot(*frame["cameraShift"])
    why = []
    if shift > maxShift:
        why.append(f"camera moved {shift:.1f} px (limit {maxShift:g})")
    if abs(frame["rotation"]) > maxRotation:
        why.append(f"camera turned {frame['rotation']:+.2f} deg (limit {maxRotation:g})")
    if abs(frame["scale"] - 1.0) > maxZoomChange:
        why.append(f"zoom x{frame['scale']:.4f} (limit +-{maxZoomChange:g})")
    return (not why), ", ".join(why)


def describe(frame: Optional[Dict[str, Any]]) -> str:
    if not frame:
        return "no frame (centres from the properties file)"
    centres = placedCentres(frame)
    parts = [f"{name} {xy}" for name, xy in centres.items()]
    return (f"{frame['type']} {frame['side']}: anchor {frame['anchor']} from {frame['anchorSource']}; camera shift "
            f"{frame['cameraShift']}, rotation {frame['rotation']:+.2f} deg, scale {frame['scale']:.4f}; centres: "
            + ", ".join(parts))


# ------------------------------------------------------------------------------------------- applying to the checks
_lastApplied: Optional[Tuple] = None


def _owner(moduleName: str, className: Optional[str]):
    module = sys.modules.get(moduleName)
    if module is None:
        return None
    return getattr(module, className, None) if className else module


def _propertiesValue(name: str, attributes: Tuple[str, ...], current):
    """The value the properties file gives (VisionConfig's registry: the latest read, live reloads included)."""
    try:
        from camera.VisionConfig import getRegistry
        registry = getRegistry()
    except Exception:
        registry = {}
    if len(attributes) == 2:
        values = []
        for axis, attr in zip(("x", "y"), attributes):
            entry = registry.get(f"vision.{name}.{axis}")
            values.append(entry["value"] if entry else current[len(values)])
        return tuple(values)
    entry = registry.get(f"vision.{name}")
    return tuple(entry["value"]) if entry else current


def applyToChecks(frame: Optional[Dict[str, Any]]) -> Dict[str, Tuple]:
    """Write this cycle's centres into the checks' constants (with a frame), or the properties file's values (without
    one). Only modules already imported are changed (a module imported later reads the properties; applyToChecks is
    called again before every check). Returns name -> (x, y) written."""
    global _lastApplied
    placed = placedCentres(frame)
    written = {}
    for name, (moduleName, className, attributes, _ref) in CENTRES.items():
        owner = _owner(moduleName, className)
        if owner is None:
            continue
        if len(attributes) == 2:
            current = (getattr(owner, attributes[0], None), getattr(owner, attributes[1], None))
        else:
            current = tuple(getattr(owner, attributes[0], ()) or ())
        value = placed[name] if frame else _propertiesValue(name, attributes, current)
        if len(attributes) == 2:
            setattr(owner, attributes[0], int(value[0]))
            setattr(owner, attributes[1], int(value[1]))
        else:
            setattr(owner, attributes[0], (int(value[0]), int(value[1])))
        written[name] = (int(value[0]), int(value[1]))
    # the hub segmenter keeps its annular centre in its (single) instance too
    hubModule = sys.modules.get("camera.HubAndBearingSegmenter")
    if hubModule is not None and "hub.segment.center" in written:
        segmenterClass = getattr(hubModule, "HubAndBearingSegmenter", None)
        instance = getattr(segmenterClass, "_instance", None) if segmenterClass is not None else None
        if instance is not None and hasattr(instance, "annular_center"):
            instance.annular_center = written["hub.segment.center"]
    key = (tuple(sorted(written.items())), bool(frame))
    if key != _lastApplied:
        _lastApplied = key
        if frame:
            _log('logInfo', "Vision centres from the knuckle picture: " + describe(frame))
        elif written:
            _log('logInfo', "Vision centres from the properties file (no knuckle frame in this cycle): "
                 + ", ".join(f"{n} {xy}" for n, xy in written.items()))
    return written
