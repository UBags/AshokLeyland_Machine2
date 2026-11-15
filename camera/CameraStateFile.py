# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
CameraStateFile - the camera server's cycle state kept in a file, so that a camera server restarted by MainProgram's
supervisor in the middle of a cycle carries on with that cycle (user, 2026-11-15).

What is kept: everything ImageProcessor keeps for the cycle (ImageProcessor._STATE_ATTRIBUTES: QR code, model, side,
top bearing ring, knuckle frame) and the earlier pictures of the component (ImageProcessor.currentPictures, as PNG).
The camera server writes the file after every check and at the end of every cycle (completed or aborted: the state is
then empty). It is read only when the camera server is started with the file's path: MainProgram passes None at its own
start (a new run starts clean) and the path when the supervisor restarts the camera server.

File: <project root>/Temp/camera_server_state.pkl, written to a temporary file first and then renamed, so a crash
while writing never leaves half a file. A file older than camera.state.file.max.age.sec (default 3600) is not used.
"""

import os
import pickle
import threading
import time
from typing import Any, Dict, Optional, Tuple

import cv2
import numpy as np

FILE_NAME = "camera_server_state.pkl"
FORMAT_VERSION = 1

_lock = threading.Lock()
_pngCache: Dict[str, Tuple[int, bytes]] = {}      # picture key -> (id of the array, PNG bytes): each picture encoded once


def defaultPath() -> str:
    """<project root>/Temp/camera_server_state.pkl (the folder is made if needed)."""
    try:
        from BaseUtils import get_project_root
        root = str(get_project_root())
    except Exception:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    folder = os.path.join(root, "Temp")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, FILE_NAME)


def _encode(key: str, picture) -> Optional[bytes]:
    if picture is None:
        _pngCache.pop(key, None)
        return None
    cached = _pngCache.get(key)
    if cached is not None and cached[0] == id(picture):
        return cached[1]
    ok, buffer = cv2.imencode(".png", picture, [cv2.IMWRITE_PNG_COMPRESSION, 1])
    if not ok:
        return None
    data = buffer.tobytes()
    _pngCache[key] = (id(picture), data)
    return data


def save(path: Optional[str], attributes: Dict[str, Any], pictures: Dict[str, Any]) -> bool:
    """Write the state (attributes: picklable values; pictures: key -> BGR array or None). False if not written."""
    if not path:
        return False
    with _lock:
        try:
            payload = dict(version=FORMAT_VERSION, savedAt=time.time(), pid=os.getpid(), attributes=dict(attributes),
                           pictures={key: _encode(key, picture) for key, picture in pictures.items()})
            temporary = f"{path}.tmp"
            with open(temporary, "wb") as f:
                pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(temporary, path)
            return True
        except Exception:
            return False


def load(path: Optional[str], maxAgeSecs: float) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], str]:
    """(attributes, pictures, why) - attributes / pictures None when nothing usable (why says so)."""
    if not path:
        return None, None, "no state file given"
    if not os.path.isfile(path):
        return None, None, f"no state file at {path}"
    try:
        with open(path, "rb") as f:
            payload = pickle.load(f)
    except Exception as e:
        return None, None, f"state file {path} unreadable ({e})"
    if not isinstance(payload, dict) or payload.get("version") != FORMAT_VERSION:
        return None, None, f"state file {path} has another format"
    age = time.time() - float(payload.get("savedAt", 0))
    if maxAgeSecs > 0 and age > maxAgeSecs:
        return None, None, f"state file {path} is {age:.0f} s old (limit {maxAgeSecs:g} s)"
    pictures = {}
    for key, data in (payload.get("pictures") or {}).items():
        pictures[key] = None if data is None else cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    return dict(payload.get("attributes") or {}), pictures, f"state file {path} ({age:.0f} s old)"
