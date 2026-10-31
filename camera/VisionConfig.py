"""
VisionConfig - tunable parameters of the visual checks, read from ApplicationConfiguration.properties.

Every parameter is declared where it is used, with its default equal to the value that was hard-coded before:

    NUT_DETECTION_RADIUS = vInt("vision.nut.detection.radius", 80)

* Key present and valid  -> the configured value is used.
* Key missing            -> the default is used (identical behaviour to the hard-coded version).
* Key present but invalid (wrong type / wrong number of elements) -> the default is used and a warning is printed.

Keys are written here without the machine prefix ("vision.nut.detection.radius"); the value is read from the
property for THIS machine: allenbradley.vision.<name> (Machine 1) or siemens.vision.<name> (Machine 2), chosen by
the PLC vendor (CosThetaConfigurator.getVisionKeyPrefix / getVisionValue). Values:
    int / float / bool / str      ->  vision.x=80  |  0.55  |  True  |  text
    tuple of numbers (e.g. x,y)   ->  vision.x=632,360            (element count must match the default)
    list of numbers               ->  vision.x=7.75,6.5,4.0,2.0,1.0
    list of tuples (polygons)     ->  vision.x=10,20;30,40;50,60   (';' between points, ',' within a point)

The element type of tuples/lists follows the default (an int default stays int, a float default stays float).
Every parameter read is recorded; describeVisionParameters() lists them and flags the ones that differ from the
built-in defaults (CameraProcessorServer logs this at start-up so a site re-tune is always visible in the logs).
"""

from typing import Any, Dict, List, Sequence, Tuple

_registry: Dict[str, Dict[str, Any]] = {}   # key -> {"default", "value", "source"}


def machinePrefix() -> str:
    """'allenbradley.' or 'siemens.' (the properties prefix of this machine's vision keys); '' standalone."""
    try:
        from Configuration import CosThetaConfigurator
        return CosThetaConfigurator.getVisionKeyPrefix()
    except Exception:
        return ""


def _raw(key: str):
    try:
        from Configuration import CosThetaConfigurator
        value = CosThetaConfigurator.getVisionValue(key, default=None)      # <vendor>.<key>
    except Exception:
        return None     # running standalone without Configuration: defaults apply
    if value is None:
        return None
    value = str(value).strip()
    return value if value != "" else None


def _record(key: str, default: Any, value: Any, source: str) -> Any:
    if key in _registry and _registry[key]["default"] != default:
        print(f"[VisionConfig] WARNING: key {key} is declared twice with different defaults "
              f"({_registry[key]['default']!r} vs {default!r})")
    _registry[key] = {"default": default, "value": value, "source": source}
    return value


def _warn(key: str, raw: str, default: Any, reason: str) -> None:
    print(f"[VisionConfig] WARNING: invalid value {raw!r} for {key} ({reason}); using default {default!r}")


def _convert_scalar(text: str, like: Any):
    if isinstance(like, bool):
        low = text.strip().lower()
        if low in ("true", "yes", "1", "on"):
            return True
        if low in ("false", "no", "0", "off"):
            return False
        raise ValueError("expected True/False")
    if isinstance(like, int):
        return int(text.strip())
    if isinstance(like, float):
        return float(text.strip())
    return text


def vInt(key: str, default: int) -> int:
    raw = _raw(key)
    if raw is None:
        return _record(key, default, default, "default")
    try:
        return _record(key, default, int(raw), "config")
    except ValueError as e:
        _warn(key, raw, default, str(e))
        return _record(key, default, default, "default (invalid config)")


def vFloat(key: str, default: float) -> float:
    raw = _raw(key)
    if raw is None:
        return _record(key, default, default, "default")
    try:
        return _record(key, default, float(raw), "config")
    except ValueError as e:
        _warn(key, raw, default, str(e))
        return _record(key, default, default, "default (invalid config)")


def vBool(key: str, default: bool) -> bool:
    raw = _raw(key)
    if raw is None:
        return _record(key, default, default, "default")
    try:
        return _record(key, default, _convert_scalar(raw, default), "config")
    except ValueError as e:
        _warn(key, raw, default, str(e))
        return _record(key, default, default, "default (invalid config)")


def vStr(key: str, default: str) -> str:
    raw = _raw(key)
    if raw is None:
        return _record(key, default, default, "default")
    return _record(key, default, raw, "config")


def _parse_seq(text: str, like: Sequence) -> List:
    parts = [p for p in text.split(",")]
    if len(like) > 0:
        return [_convert_scalar(p, like[min(i, len(like) - 1)]) for i, p in enumerate(parts)]
    return [float(p) for p in parts]


def vTuple(key: str, default: Tuple) -> Tuple:
    """Fixed-length tuple (e.g. an (x, y) centre or (x, y, w, h) box). Element count must match the default."""
    raw = _raw(key)
    if raw is None:
        return _record(key, default, default, "default")
    try:
        value = tuple(_parse_seq(raw, default))
        if len(value) != len(default):
            raise ValueError(f"expected {len(default)} values, got {len(value)}")
        return _record(key, default, value, "config")
    except ValueError as e:
        _warn(key, raw, default, str(e))
        return _record(key, default, default, "default (invalid config)")


def vList(key: str, default: List) -> List:
    """Variable-length list of numbers (e.g. a gamma sequence). Element type follows the first default element."""
    raw = _raw(key)
    if raw is None:
        return _record(key, default, list(default), "default")
    try:
        like = default[:1] if len(default) else [0.0]
        value = [_convert_scalar(p, like[0]) for p in raw.split(",")]
        if len(value) == 0:
            raise ValueError("empty list")
        return _record(key, default, value, "config")
    except ValueError as e:
        _warn(key, raw, default, str(e))
        return _record(key, default, list(default), "default (invalid config)")


def vPoints(key: str, default: List) -> List:
    """List of points/tuples, e.g. a polygon: "x1,y1;x2,y2;x3,y3". Point arity follows the default."""
    raw = _raw(key)
    if raw is None:
        return _record(key, default, default, "default")
    try:
        like = default[0] if len(default) else (0, 0)
        value = []
        for chunk in raw.split(";"):
            pt = tuple(_parse_seq(chunk, like))
            if len(pt) != len(like):
                raise ValueError(f"each point needs {len(like)} values")
            value.append(pt if isinstance(like, tuple) else list(pt))
        if isinstance(default, tuple):
            value = tuple(value)
        return _record(key, default, value, "config")
    except ValueError as e:
        _warn(key, raw, default, str(e))
        return _record(key, default, default, "default (invalid config)")


def describeVisionParameters(onlyChanged: bool = False) -> List[str]:
    prefix = machinePrefix()
    lines = []
    for key in sorted(_registry):
        entry = _registry[key]
        changed = entry["value"] != entry["default"] and not (
            isinstance(entry["default"], (tuple, list)) and list(entry["value"]) == list(entry["default"]))
        if onlyChanged and not changed:
            continue
        flag = "  <-- differs from built-in default" if changed else ""
        lines.append(f"{prefix}{key} = {entry['value']!r} [{entry['source']}]{flag}")
    return lines


def getRegistry() -> Dict[str, Dict[str, Any]]:
    return dict(_registry)
