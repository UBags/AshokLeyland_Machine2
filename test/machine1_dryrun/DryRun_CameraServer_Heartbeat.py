"""
DryRun_CameraServer_Heartbeat - the camera server's heartbeat (camera2hb.q) is sent from start-up.

Found on the Machine 2 PC (2026-11-05): the camera streamed at 25 frames/s, but the heartbeat server showed Cam=False,
because TakePictures started its heartbeat thread only inside its live-configuration listener (i.e. never, unless the
properties file was changed; and one more thread at every change). Checked here with the real TakePictures and a
stand-in camera:
  - heartbeats arrive on camera2hb.q after start-up, without any configuration change;
  - camera connected -> Alive; camera not connected -> Dead;
  - a configuration reload does not start another heartbeat thread.
torch / MobileSAM / YOLO / ffmpeg are not needed: where missing, they are replaced by stand-ins (TakePictures does not
use them). Needs Redis.

Run from the project root:   python test/machine1_dryrun/DryRun_CameraServer_Heartbeat.py
"""
import pathlib
HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
import sys, types, time, threading, os
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("COSTHETA_PLC_VENDOR", "siemens" if HERE.name.startswith("machine2") else "allenbradley")
# heavy model / video libraries are not used by TakePictures: any that this sandbox lacks (and all their submodules)
# are supplied as stand-ins that answer every attribute
from unittest import mock
import importlib.abc, importlib.machinery, importlib.util
STUB_ROOTS = {"torch", "torchvision", "ultralytics", "mobile_sam", "ffmpeg", "imutils", "timm", "sklearn", "scipy"}
MISSING = set()
for m in STUB_ROOTS:
    if importlib.util.find_spec(m) is None:
        MISSING.add(m)
class _StubLoader(importlib.abc.Loader):
    def create_module(self, spec):
        mm = mock.MagicMock(name=spec.name); mm.__spec__ = spec; mm.__path__ = []; mm.__name__ = spec.name
        return mm
    def exec_module(self, module): pass
class _StubFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name.split(".")[0] in MISSING:
            return importlib.machinery.ModuleSpec(name, _StubLoader(), is_package=True)
        return None
sys.meta_path.insert(0, _StubFinder())
print("stand-ins for:", sorted(MISSING), flush=True)
import camera.RTSPCam as R
class FakeCam:
    def __init__(self, **kw): self.connected = True; self.maxAllowedNoneFrameCountsInCamera = 10
R.RTSPCam = FakeCam
import camera.CameraProcessorServer as CPS
CPS.RTSPCam = FakeCam
from utils.RedisUtils import *
from Configuration import CosThetaConfigurator as C
r = Redis('localhost', 6379); r.delete(camera2hbq)
results = []
def check(n, ok, d=""):
    results.append(ok); print(("PASS  " if ok else "FAIL  ") + n + ("" if ok else f"   [{d}]"), flush=True)
tp = CPS.TakePictures(name="TakePictures", monitorRedisQueueForStopping=False)
time.sleep(1.6)
msgs = r.xrange(camera2hbq)
vals = [m[1].get(b'connectionStatus') or m[1] for m in msgs]
check("heartbeats sent after start-up, without any configuration change", len(msgs) >= 2, f"{len(msgs)} messages")
check("camera connected -> heartbeat ALIVE", bool(vals) and all(str(v).upper().find('ALIVE') >= 0 for v in vals[-2:]), str(vals[-2:]))
tp.camera.connected = False; r.delete(camera2hbq); time.sleep(1.2)
vals = [str(m[1]) for m in r.xrange(camera2hbq)]
check("camera not connected -> heartbeat DEAD", bool(vals) and all('DEAD' in v.upper() for v in vals[-2:]), str(vals[-2:]))
before = sum(1 for t in threading.enumerate() if t.name == "Camera Heartbeat Thread")
tp._applyLiveConfiguration(["vision.siemens.time.between.pictures.ms"])
time.sleep(0.3)
after = sum(1 for t in threading.enumerate() if t.name == "Camera Heartbeat Thread")
check(f"configuration reload does not start another heartbeat thread ({before} -> {after})", before == 1 and after == 1)
print(f"RESULT {sum(results)}/{len(results)}"); os._exit(0 if all(results) else 1)
