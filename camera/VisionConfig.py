"""
VisionConfig - tunable parameters of the visual checks, read from ApplicationConfiguration.properties.

Every parameter is declared where it is used, with its default equal to the value that was hard-coded before:

    NUT_DETECTION_RADIUS = vInt("vision.nut.detection.radius", 80)

* Key present and valid  -> the configured value is used.
* Key missing            -> the default is used (identical behaviour to the hard-coded version) and logged.
* Key present but invalid (wrong type / wrong number of elements) -> the default is used and a warning is logged.
  (Logged once per process and key, printed and in the log file - CosThetaConfigurator.reportConfigurationProblem,
  CHANGES 157; a valid value logs nothing. expectOneOf() does the same for a text value outside its choices.)

Keys are written here without the machine ("vision.nut.detection.radius", "vision.dost.cap.radius"); the value is
read from the property for THIS machine, the vendor inserted after "vision." (CHANGES 213, utils/VisionKeyNames.py):
vision.allenbradley.<rest> (Machine 1) or vision.siemens.<rest> (Machine 2), chosen by the PLC vendor
(CosThetaConfigurator.visionPropertyKey / getVisionValue). A value of one model has the model next:
vision.<vendor>.dost.<check>... / vision.<vendor>.dostplus.<check>...; a value both models use has none. Values:
    int / float / bool / str      ->  vision.x=80  |  0.55  |  True  |  text
    tuple of numbers (e.g. x,y)   ->  vision.x=632,360            (element count must match the default)
    list of numbers               ->  vision.x=7.75,6.5,4.0,2.0,1.0
    list of tuples (polygons)     ->  vision.x=10,20;30,40;50,60   (';' between points, ',' within a point)

The element type of tuples/lists follows the default (an int default stays int, a float default stays float).
Every parameter read is recorded; describeVisionParameters() lists them and flags the ones that differ from the
built-in defaults (CameraProcessorServer logs this at start-up so a site re-tune is always visible in the logs).
"""

import sys
from typing import Any, Dict, List, Sequence, Tuple

_registry: Dict[str, Dict[str, Any]] = {}   # key -> {"default", "value", "source"}
_modules = set()                              # modules that declared parameters (re-bound on a live reload)


def machinePrefix() -> str:
    """'allenbradley.' or 'siemens.' (this machine's vendor); '' standalone. Since CHANGES 213 not a prefix of the
    property name - use propertyName()."""
    try:
        from Configuration import CosThetaConfigurator
        return CosThetaConfigurator.getVisionKeyPrefix()
    except Exception:
        return ""


def propertyName(key: str) -> str:
    """This machine's property for the code key: 'vision.dost.cap.radius' -> 'vision.siemens.dost.cap.radius'; the
    key itself standalone (CHANGES 213)."""
    try:
        from Configuration import CosThetaConfigurator
        return CosThetaConfigurator.visionPropertyKey(key)
    except Exception:
        return key


def _noteCaller() -> None:
    try:
        _modules.add(sys._getframe(3).f_globals.get("__name__"))     # the module that called vInt/vFloat/...
    except Exception:
        pass


def _report(key: str, message: str) -> None:
    try:
        from Configuration import CosThetaConfigurator
        CosThetaConfigurator.reportConfigurationProblem(propertyName(key), message)
    except Exception:
        print(f"[VisionConfig] WARNING: {message}")


def _raw(key: str):
    _noteCaller()
    try:
        from Configuration import CosThetaConfigurator
        value = CosThetaConfigurator.getVisionValue(key, default=None)      # <vendor>.<key>
    except Exception:
        return None     # running standalone without Configuration: defaults apply
    value = None if value is None else str(value).strip()
    if not value:
        _report(key, f"{propertyName(key)} not found in the properties file; the default is used")
        return None
    return value


def _record(key: str, default: Any, value: Any, source: str) -> Any:
    if key in _registry and _registry[key]["default"] != default:
        print(f"[VisionConfig] WARNING: key {key} is declared twice with different defaults "
              f"({_registry[key]['default']!r} vs {default!r})")
    _registry[key] = {"default": default, "value": value, "source": source}
    return value


def _warn(key: str, raw: str, default: Any, reason: str) -> None:
    _report(key, f"invalid value {raw!r} for {propertyName(key)} ({reason}); using default {default!r}")


def expectOneOf(key: str, value: Any, choices: Sequence[str]) -> bool:
    """True when the text value (stripped, any case) is one of choices; otherwise logged once (CHANGES 157). For the
    vision keys that choose a method (e.g. vision.cap.seating.hub.source)."""
    if str(value).strip().lower() in tuple(str(c).lower() for c in choices):
        return True
    _report(key, f"{propertyName(key)}={value!r} is not one of {', '.join(map(str, choices))}")
    return False


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
    lines = []
    for key in sorted(_registry):
        entry = _registry[key]
        changed = entry["value"] != entry["default"] and not (
            isinstance(entry["default"], (tuple, list)) and list(entry["value"]) == list(entry["default"]))
        if onlyChanged and not changed:
            continue
        flag = "  <-- differs from built-in default" if changed else ""
        lines.append(f"{propertyName(key)} = {entry['value']!r} [{entry['source']}]{flag}")
    return lines


def getRegistry() -> Dict[str, Dict[str, Any]]:
    return dict(_registry)


# ------------------------------------------------------------------ live reload
# When ApplicationConfiguration.properties changes, every module-level or class-level constant declared as
#     NAME = vInt("vision.x", default)        (or vFloat / vBool / vStr / vTuple / vList / vPoints)
# is re-evaluated and re-bound in its module / class, so the next picture is checked with the new value - no restart.
# Not refreshed: copies made with "from module import NAME" in another module, and values derived from these
# constants at import time (computed once). The camera server log lists every value changed.
_FUNCS = {"vInt": vInt, "vFloat": vFloat, "vBool": vBool, "vStr": vStr, "vTuple": vTuple, "vList": vList,
          "vPoints": vPoints}


def _declarations(module):
    """(owner object, attribute name, function name, key) for each NAME = vX("key", ...) at module / class level."""
    import ast
    import inspect
    try:
        tree = ast.parse(inspect.getsource(module))
    except Exception:
        return []
    found = []

    def scan(body, owner):
        for node in body:
            if isinstance(node, ast.ClassDef):
                inner = getattr(owner, node.name, None)
                if inner is not None:
                    scan(node.body, inner)
                continue
            value = getattr(node, "value", None)
            targets = node.targets if isinstance(node, ast.Assign) else ([node.target] if isinstance(node, ast.AnnAssign) else [])
            if (isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id in _FUNCS
                    and value.args and isinstance(value.args[0], ast.Constant) and isinstance(value.args[0].value, str)):
                for t in targets:
                    if isinstance(t, ast.Name):
                        found.append((owner, t.id, value.func.id, value.args[0].value))
    scan(tree.body, module)
    return found


def reloadVisionParameters(changedKeys=None) -> List[str]:
    """Re-bind the vision constants to the values now in the properties file. Returns 'module.NAME: old -> new'."""
    vendorPart = f"vision.{machinePrefix()}"          # this machine's properties: vision.<vendor>.... (CHANGES 213)
    if changedKeys is not None and not any(k.startswith(vendorPart) for k in changedKeys):
        return []
    changes = []
    for name in sorted(m for m in _modules if m):
        module = sys.modules.get(name)
        if module is None:
            continue
        for owner, attr, fn, key in _declarations(module):
            if key not in _registry:
                continue
            old = getattr(owner, attr, None)
            new = _FUNCS[fn](key, _registry[key]["default"])
            if new != old:
                setattr(owner, attr, new)
                changes.append(f"{getattr(owner, '__name__', name)}.{attr} [{propertyName(key)}]: {old!r} -> {new!r}")
    if changes:
        message = "Vision parameters changed while running:\n  " + "\n  ".join(changes)
        print(message)
        try:
            from logutils.SlaveLoggers import SlaveFileLogger
            from logutils.Logger import Logger
            from logutils.LogSettings import getLogProcess
            if getLogProcess():
                SlaveFileLogger.getInstance().logTakeAction("camera.VisionConfig", message, Logger.ISSUE)
        except Exception:
            pass
    return changes


try:
    from Configuration import CosThetaConfigurator as _Cfg
    _Cfg.addReloadListener(reloadVisionParameters)
except Exception:
    pass
