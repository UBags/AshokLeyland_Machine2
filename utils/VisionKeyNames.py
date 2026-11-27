# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
VisionKeyNames - the names of the vision keys (CHANGES 213, Uddi 2026-11-27).

Every vision key names its machine first and, where the value belongs to one model, the model next:

    vision.<vendor>.<check>...            a value both models use           vision.siemens.cap.center
    vision.<vendor>.<model>.<check>...    a value of one model (dost / dostplus)   vision.siemens.dost.cap.radius

<vendor> = siemens (Machine 2) or allenbradley (Machine 1). In the code a key is written without the vendor
("vision.dost.cap.radius"); VisionConfig / CosThetaConfigurator insert this machine's vendor after "vision."
(propertyKey). So the text in the code, minus the vendor, is the text in the properties file - one name per value.

Before CHANGES 213 the keys were "<vendor>.vision.<check>...", with the model (if any) at the end or in the middle
("siemens.vision.cap.radius.dost", "siemens.vision.frame.nominal.pins.dost.lhs") or only implied by the cap's colour
(silver = the DOST cap, brass = the DOSTPLUS cap) or by the method that uses it (the brass bore, the silver hub rule, the
annulus). Those names are no longer read; a properties file that still has them is reported once at start
("[Configuration] ... old-style vision keys ...") and is converted by this module:

    python utils/VisionKeyNames.py <file.properties> [...]      (in place; the original kept as <file>.before213)

newCodeKey(old) is the one rule from an old name (without vendor) to the new one; it leaves a new name unchanged.
"""

import pathlib
import re
import sys

VENDORS = ("siemens", "allenbradley")
MODELS = ("dost", "dostplus")
# The cap's colour names its model: the silver cap is DOST's, the brass cap DOSTPLUS's (cap keys only)
CAP_COLOUR_MODEL = {"silver": "dost", "brass": "dostplus"}
# Cap keys whose model is implied by the only method that reads them (checked in CheckCap / CapAnnulus, CHANGES 213)
CAP_IMPLIED_MODEL = {
    "seating.bore": "dostplus",            # brassCapAndBore: the bore round the brass cap (CHANGES 128)
    "seating.face.box.scale": "dost",      # silverFaceBySegmenter (CHANGES 129)
    "seating.hub.ring.radius": "dost",     # hubCircle / hubCentreVisible: the silver cap's hub rule (CHANGES 129)
    "seating.hub.box.half": "dost",
    "seating.hub.radius": "dost",
    "seating.hub.centre.inset": "dost",
    "seating.hub.dark": "dost",
    "seating.hub.visible.max": "dost",
    "annulus": "dost",                     # CapAnnulus: the silver cap's seating (CHANGES 212)
}

_OLD_PROPERTY = re.compile(r"(?<![\w.])(siemens|allenbradley)\.(vision(?:\.[A-Za-z0-9_]+)+)")
_CODE_KEY = re.compile(r"(?<![\w.<])(vision(?:\.[A-Za-z0-9_]+)+)")
_PLACEHOLDER = re.compile(r"<(vendor|machine)>\.vision((?:\.[A-Za-z0-9_*]+|\.<model>)*)")


def newCodeKey(key: str) -> str:
    """Old code key ("vision.cap.radius.dost") -> new ("vision.dost.cap.radius"); a new key, a key of no model and any
    text not starting with "vision." are returned unchanged."""
    parts = str(key).split(".")
    if len(parts) < 2 or parts[0] != "vision" or parts[1] in VENDORS or parts[1] in MODELS:
        return key
    rest = parts[1:]
    models = [p for p in rest if p in MODELS]
    if len(models) > 1:
        raise ValueError(f"{key}: more than one model in the name")
    if models:
        rest.remove(models[0])
        return ".".join(["vision", models[0]] + rest)
    if rest[0] == "cap":
        colours = [p for p in rest if p in CAP_COLOUR_MODEL]
        if len(colours) > 1:
            raise ValueError(f"{key}: more than one cap colour in the name")
        if colours:
            rest.remove(colours[0])
            return ".".join(["vision", CAP_COLOUR_MODEL[colours[0]]] + rest)
        tail = ".".join(rest[1:])
        for prefix, model in CAP_IMPLIED_MODEL.items():
            if tail == prefix or tail.startswith(prefix + "."):
                return ".".join(["vision", model] + rest)
    return key


def propertyKey(codeKey: str, vendor: str) -> str:
    """Code key ("vision.dost.cap.radius") -> this machine's property ("vision.siemens.dost.cap.radius")."""
    if not str(codeKey).startswith("vision."):
        raise ValueError(f"{codeKey}: a vision key starts with 'vision.'")
    return f"vision.{vendor}.{codeKey[len('vision.'):]}"


def newPropertyKey(prop: str) -> str:
    """Old property ("siemens.vision.cap.radius.dost") -> new ("vision.siemens.dost.cap.radius"); anything else
    unchanged."""
    m = _OLD_PROPERTY.fullmatch(str(prop).strip())
    return propertyKey(newCodeKey(m.group(2)), m.group(1)) if m else prop


def isOldPropertyKey(prop: str) -> bool:
    return _OLD_PROPERTY.fullmatch(str(prop).strip()) is not None


def _placeholder(m) -> str:
    """"<vendor>.vision.cap.radius.dost" (docs and comments) -> "vision.<vendor>.dost.cap.radius"; a trailing ".*" and
    a ".<model>" placeholder are kept ("vision.<vendor>.<model>.bung...")."""
    who, rest = m.group(1), m.group(2)
    model = ""
    if rest.endswith(".<model>"):
        rest, model = rest[:-len(".<model>")], "<model>."
    core = rest.rstrip("*").rstrip(".")
    star = rest[len(core):]
    tail = newCodeKey("vision" + core)[len("vision"):] if core else ""
    return f"vision.<{who}>." + model + tail.lstrip(".") + star if model else f"vision.<{who}>{tail}{star}"


def renameInText(text: str) -> str:
    """Every old vision key in a text (properties, code, docs) by its new name: "<vendor>.vision.x..." first, then the
    vendor-free "vision.x...", then the placeholders "<vendor>.vision.x..." / "<machine>.vision.x..." of the docs
    (a new name, or one of no model, is left as it is)."""
    text = _OLD_PROPERTY.sub(lambda m: propertyKey(newCodeKey(m.group(2)), m.group(1)), text)
    text = _CODE_KEY.sub(lambda m: newCodeKey(m.group(1)), text)
    return _PLACEHOLDER.sub(_placeholder, text)


def convertPropertiesFile(path) -> int:
    """Renames the old vision keys of a properties file in place (original kept as <file>.before213). -> keys renamed."""
    path = pathlib.Path(path)
    text = path.read_text(encoding="utf-8")
    count = sum(1 for line in text.splitlines()
                if "=" in line and not line.lstrip().startswith("#") and isOldPropertyKey(line.split("=", 1)[0]))
    new = renameInText(text)
    if new != text:
        backup = path.with_name(path.name + ".before213")
        if not backup.exists():
            backup.write_text(text, encoding="utf-8")
        path.write_text(new, encoding="utf-8")
    return count


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    for name in sys.argv[1:]:
        print(f"{name}: {convertPropertiesFile(name)} vision keys renamed")
