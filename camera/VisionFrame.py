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
    centre of every later check, for the type T found:
        place = S + scale * R(rotation) * D[T]          D[T] = P[T] - TYPE_SPINDLE[T]

P[T] is the check's centre for type T at the reference camera position (13 Nov 2026) and TYPE_SPINDLE[T] the mean
spindle of that type (LHS and RHS), so D[T] is the centre's displacement from the spindle for that type. The DOST
features sit 4-5 px higher in the picture than the DOSTPLUS ones (measured on the hub, top bearing and nut pictures
of the same parts, CHANGES 102), hence one displacement per type. With no camera movement, place = the side's own
usual spindle + D[T]. The hub, bearing, nut, bung and cap all sit on the spindle, so their centres follow it.

The reference values are vision.<vendor>.frame.* keys in ApplicationConfiguration.properties (read below through
VisionConfig; CENTRES is the table of them). With a frame,
applyToChecks() writes the placed centres into the checks' constants before each check; with no frame (no knuckle
located in this cycle - Machine 1, the regions knuckle method, or the camera server restarted during a cycle) it puts
back the values from the properties file, as before. The frame is part of the cycle's state (ImageProcessor.visionFrame)
and is dropped at the end of a cycle (completed or aborted), at a new cycle's knuckle check and at a knuckle retake.

Radii and sizes are not changed (the scale is within 0.1 % on the 13 Nov pictures), except the bung seating reference
ovals (CHANGES 126): with a frame each is scaled by the frame's zoom and turned by its rotation, because the seating
test is the one size test with a narrow margin (seated faces up to 3.9 % outside against a 6 % limit; an unscaled oval
would reject them from about 1 % zoom, while the frame accepts up to 5 %).
"""

import math
import sys
from typing import Any, Dict, Optional, Tuple

from camera.VisionConfig import vFloat, vTuple

# ------------------------------------------------------------------------------------------------ reference values
# Per machine: vision.<vendor>.frame.* in ApplicationConfiguration.properties (2026-11-15; before, in this file). Machine 2
# values measured on the 13 Nov 2026 pictures. Machine 1 runs the regions knuckle method and does not use these; its
# vision.allenbradley.frame.* values are the Machine 2 ones until they are measured on Machine 1 pictures.
# The values are read where they are used (nominalPins(), typeSpindle(), centres()), so a live reload applies.

# Mean spindle of the knuckles of all types: only used to state the camera shift (and its limit)
REF_SPINDLE = (642.2, 359.1)
# Mean usual spindle per type (LHS and RHS of the nominal pins below)
TYPE_SPINDLE_DOST = (642.35, 358.45)
TYPE_SPINDLE_DOSTPLUS = (642.2, 359.7)

# The pins (dark core = bore + fixture pin, inside each machined boss) per type and side at the reference camera position:
# pin distance (px), angle of the line left pin -> right pin (deg), pin midpoint x, y, spindle x, y.
NOMINAL_PINS_DOST_LHS = (400.35, 0.65, 644.15, 194.93, 643.2, 359.8)
NOMINAL_PINS_DOST_RHS = (400.17, 0.68, 643.75, 192.95, 641.5, 357.1)
NOMINAL_PINS_DOSTPLUS_LHS = (394.26, 0.69, 643.7, 199.65, 642.1, 359.1)
NOMINAL_PINS_DOSTPLUS_RHS = (394.64, 0.77, 643.62, 200.0, 642.3, 360.3)

# The spindle mask is expected within this of where the pins put the spindle (spread measured: +-1 px); further away
# it is noted in the frame (spindleCheck) and logged; the pins' spindle is the anchor either way.
ANCHOR_AGREEMENT_PX = 6.0

# The later checks' centres per type at the reference camera position.
# How the Machine 2 values were set (13 Nov pictures, same parts through the stations; each type's mean over its parts,
# every part weighted equally - DOST had two parts, DOSTPLUS one):
#   hub inner bore (MobileSAM)  DOST (644.0, 335.5)  DOSTPLUS (643.4, 339.4)   DOST - DOSTPLUS (+0.6, -3.9)
#   top bearing ring            DOST (641.8, 338.3)  DOSTPLUS (641.6, 343.2)                   (+0.2, -4.9)
#   nut ring                    DOST (644.3, 334.1)  DOSTPLUS (643.4, 338.5)                   (+0.9, -4.4)
#   seated bung face            DOST (646, 325)      DOSTPLUS (647, 330)                       (-1.0, -5.0)
# The single centres used before (the properties values) were set on both types' pictures; each is split by half the
# type difference of its feature: DOST = centre + diff / 2, DOSTPLUS = centre - diff / 2 (the hub difference for the
# hub centre check and the cap; the bung difference for the bung disc). The seated bung positions are per type already
# (the same value under both types).
CENTRE_HUB_SEGMENT_DOST = (645.3, 332.05)
CENTRE_HUB_SEGMENT_DOSTPLUS = (644.7, 335.95)
CENTRE_HUB_CHECK_DOST = (644.3, 338.05)
CENTRE_HUB_CHECK_DOSTPLUS = (643.7, 341.95)
CENTRE_TOP_BEARING_DOST = (642.1, 342.55)
CENTRE_TOP_BEARING_DOSTPLUS = (641.9, 347.45)
CENTRE_NUT_DOST = (644.45, 341.8)
CENTRE_NUT_DOSTPLUS = (643.55, 346.2)
CENTRE_NOBUNG_NUT_DOST = (644.45, 341.8)
CENTRE_NOBUNG_NUT_DOSTPLUS = (643.55, 346.2)
CENTRE_BUNG_DISC_DOST = (645.5, 325.5)
CENTRE_BUNG_DISC_DOSTPLUS = (646.5, 330.5)
CENTRE_BUNG_SEATED_DOST = (646.0, 325.0)
CENTRE_BUNG_SEATED_DOSTPLUS = (647.0, 330.0)
CENTRE_CAP_DOST = (644.3, 338.05)
CENTRE_CAP_DOSTPLUS = (643.7, 341.95)

# name: (module, class or None, attributes, (DOST constant, DOSTPLUS constant)). Attributes: one name holding an (x, y)
# tuple, or two names holding x and y.
_TARGETS = {
    "hub.segment.center": ("camera.HubAndBearingSegmenter", None, ("HUB_SEGMENT_CENTER_X", "HUB_SEGMENT_CENTER_Y"),
                           ("CENTRE_HUB_SEGMENT_DOST", "CENTRE_HUB_SEGMENT_DOSTPLUS")),
    "hub.centre.check": ("camera.CheckHubAndBottomBearing", None, ("HUB_CENTRE_CHECK_X", "HUB_CENTRE_CHECK_Y"),
                         ("CENTRE_HUB_CHECK_DOST", "CENTRE_HUB_CHECK_DOSTPLUS")),
    "topbearing.annular.center": ("camera.CheckTopBearing", "CheckTopBearing", ("ANNULAR_CENTER_X", "ANNULAR_CENTER_Y"),
                                  ("CENTRE_TOP_BEARING_DOST", "CENTRE_TOP_BEARING_DOSTPLUS")),
    "nutwasher.nut.detection.center": ("camera.CheckNutAndPlateWasher", "CheckNutAndPlateWasher",
                                       ("NUT_DETECTION_CENTER",), ("CENTRE_NUT_DOST", "CENTRE_NUT_DOSTPLUS")),
    "nobung.nut.detection.center": ("camera.CheckNoBung", "CheckNoBung", ("NUT_DETECTION_CENTER",),
                                    ("CENTRE_NOBUNG_NUT_DOST", "CENTRE_NOBUNG_NUT_DOSTPLUS")),
    # per model since CHANGES 215 (vision.<vendor>.<model>.bung.outer.disc.center; BungSegmenter.useModel copies the
    # checked model's into OUTER_DISC_CENTER), placed like the seated centres below
    "dost.bung.outer.disc.center": ("camera.BungSegmenter", None, ("OUTER_DISC_CENTER_DOST",),
                                    ("CENTRE_BUNG_DISC_DOST", "CENTRE_BUNG_DISC_DOST")),
    "dostplus.bung.outer.disc.center": ("camera.BungSegmenter", None, ("OUTER_DISC_CENTER_DOSTPLUS",),
                                        ("CENTRE_BUNG_DISC_DOSTPLUS", "CENTRE_BUNG_DISC_DOSTPLUS")),
    "dost.bung.seating.seated.centre": ("camera.BungSegmenter", None, ("SEATING_SEATED_CENTRE_DOST",),
                                        ("CENTRE_BUNG_SEATED_DOST", "CENTRE_BUNG_SEATED_DOST")),
    "dostplus.bung.seating.seated.centre": ("camera.BungSegmenter", None, ("SEATING_SEATED_CENTRE_DOSTPLUS",),
                                            ("CENTRE_BUNG_SEATED_DOSTPLUS", "CENTRE_BUNG_SEATED_DOSTPLUS")),
    "cap.center": ("camera.CheckCap", "CheckCap", ("CENTER",), ("CENTRE_CAP_DOST", "CENTRE_CAP_DOSTPLUS")),
}

# Sizes that follow the camera (CHANGES 126): the bung seating reference ovals (width, height, angle in degrees; cv2
# rotated box), centred on the placed seated positions. name (the BungSegmenter key after "vision."): (module, class or
# None, attribute). Their values come from the properties file (BungSegmenter's own keys).
_OVAL_TARGETS = {
    "dost.bung.seating.reference.oval": ("camera.BungSegmenter", None, "SEATING_REFERENCE_OVAL_DOST"),
    "dostplus.bung.seating.reference.oval": ("camera.BungSegmenter", None, "SEATING_REFERENCE_OVAL_DOSTPLUS"),
}
_ovalFirstSeen: Dict[str, Tuple[float, float, float]] = {}


def _here():
    return sys.modules[__name__]


def nominalPins() -> Dict[Tuple[str, str], Dict[str, Any]]:
    """(type, side) -> dict(distance, angle, pin_mid, spindle), from the current values."""
    out = {}
    for (t, sd) in (("DOST", "LHS"), ("DOST", "RHS"), ("DOSTPLUS", "LHS"), ("DOSTPLUS", "RHS")):
        v = getattr(_here(), f"NOMINAL_PINS_{t}_{sd}")
        out[(t, sd)] = dict(distance=float(v[0]), angle=float(v[1]), pin_mid=(float(v[2]), float(v[3])),
                            spindle=(float(v[4]), float(v[5])))
    return out


def typeSpindle() -> Dict[str, Tuple[float, float]]:
    return {"DOST": tuple(float(x) for x in TYPE_SPINDLE_DOST), "DOSTPLUS": tuple(float(x) for x in TYPE_SPINDLE_DOSTPLUS)}


def centres() -> Dict[str, Tuple[str, Optional[str], Tuple[str, ...], Dict[str, Tuple[float, float]]]]:
    """name -> (module, class or None, attributes, {type: reference (x, y)}), from the current values."""
    out = {}
    for name, (module, cls, attrs, (dost, dostplus)) in _TARGETS.items():
        out[name] = (module, cls, attrs, {"DOST": tuple(float(x) for x in getattr(_here(), dost)),
                                          "DOSTPLUS": tuple(float(x) for x in getattr(_here(), dostplus))})
    return out


# As read at import (kept for the tools and dry runs that look at them); the code uses the functions above.
NOMINAL_PINS = nominalPins()
TYPE_SPINDLE = typeSpindle()
CENTRES = centres()


def reference(name: str, modelType: str) -> Tuple[float, float]:
    """The centre of a check for a type at the reference camera position."""
    return centres()[name][3][modelType]


def displacement(name: str, modelType: str) -> Tuple[float, float]:
    """The centre's displacement from the spindle for a type (reference centre - that type's mean spindle)."""
    ref, sp = reference(name, modelType), typeSpindle()[modelType]
    return ref[0] - sp[0], ref[1] - sp[1]


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
    nom = nominalPins().get((modelType, side))
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
    refSpindle = (float(REF_SPINDLE[0]), float(REF_SPINDLE[1]))
    cameraShift = tuple(a - b for a, b in zip(camera(refSpindle), refSpindle))
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
                pinAngle=round(angle, 3), refSpindle=refSpindle)


def place(frame: Dict[str, Any], point: Tuple[float, float]) -> Tuple[float, float]:
    """A point at the reference camera position, given for the frame's type (its spindle at TYPE_SPINDLE) -> this
    cycle's picture."""
    sp = typeSpindle()[frame["type"]]
    dx, dy = _rotate(point[0] - sp[0], point[1] - sp[1], frame["rotation"], frame["scale"])
    return frame["anchor"][0] + dx, frame["anchor"][1] + dy


def placeCentre(frame: Dict[str, Any], name: str) -> Tuple[float, float]:
    """A check's centre in this cycle's picture: anchor + scale * R(rotation) * displacement for the frame's type."""
    return place(frame, reference(name, frame["type"]))


def placeOval(frame: Dict[str, Any], oval: Tuple[float, float, float]) -> Tuple[float, float, float]:
    """A reference oval (width, height, angle) at the reference camera position -> this cycle's picture: both axes
    times the frame's scale, the angle plus its rotation (both in picture coordinates, y down, as cv2 uses)."""
    scale = float(frame["scale"])
    return (round(float(oval[0]) * scale, 2), round(float(oval[1]) * scale, 2),
            round(float(oval[2]) + float(frame["rotation"]), 3))


def placedCentres(frame: Optional[Dict[str, Any]]) -> Dict[str, Tuple[int, int]]:
    """Every later check's centre for this frame (rounded to whole pixels, as in the properties)."""
    if not frame:
        return {}
    return {name: tuple(int(round(v)) for v in placeCentre(frame, name)) for name in _TARGETS}


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
    for name, (moduleName, className, attributes, _ref) in _TARGETS.items():
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
    # the bung reference ovals: scaled and turned with the frame, the properties values without one
    for name, (moduleName, className, attribute) in _OVAL_TARGETS.items():
        owner = _owner(moduleName, className)
        if owner is None:
            continue
        current = tuple(getattr(owner, attribute, ()) or ())
        _ovalFirstSeen.setdefault(name, current)              # (never a placed value: used only without a registry)
        base = _propertiesValue(name, (attribute,), _ovalFirstSeen[name])
        value = placeOval(frame, base) if frame else tuple(base)
        setattr(owner, attribute, value)
        written[name] = value
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
