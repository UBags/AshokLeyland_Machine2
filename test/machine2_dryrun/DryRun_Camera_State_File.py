"""
DryRun_Camera_State_File - the camera server's cycle state kept in a file across a camera server restart
(camera/CameraStateFile.py, user 2026-11-15). Automatic checks:

  C1 save and load: QR code, model, side, bearing ring, knuckle frame and the earlier pictures come back identical
     (pictures as PNG: pixel-identical);
  C2 a file older than camera.state.file.max.age.sec is not used; a missing or broken file is not used;
  C3 ImageProcessor: persistState after a check, then a NEW PROCESS (as the supervisor's restart) started with the file
     takes the cycle's state: QR code, pictures, bearing ring, and the knuckle frame applied to the checks' centres;
  C4 the end of a cycle writes an empty state: a restart after it starts with nothing;
  C5 MainProgram: the camera server started with None, restarted with the state file; CameraServer writes the file
     and reads it only when given its path; ConverterScalerAndProcessor writes it after every check.
No Redis, models, camera or PLC needed.

Run from the project root:   python test/machine2_dryrun/DryRun_Camera_State_File.py
"""
import inspect
import multiprocessing
import os
import pathlib
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("COSTHETA_PLC_VENDOR", "siemens" if HERE.name.startswith("machine2") else "allenbradley")


def restartedCameraServer(path, conn):
    """What a camera server restarted with the state file does first (CameraServer: loadPersistedState)."""
    import camera.CameraProcessorServer as cps
    import camera.CheckCap, camera.CheckTopBearing                 # noqa: F401 - modules whose centres the frame sets
    IP = cps.ImageProcessor
    taken = IP.loadPersistedState(path)
    pictures = {k: (None if v is None else (v.shape, int(v.sum() % 1000003))) for k, v in IP.currentPictures.items()}
    from camera.CheckCap import CheckCap
    from camera.CheckTopBearing import CheckTopBearing
    conn.send(dict(taken=taken, qr=IP.currentComponentQRCodeValue, model=IP.currentModelName, side=IP.currentSide,
                   ring=IP.bearingCentreAndRadii, ringQR=IP.bearingProfileQRCode, frame=IP.visionFrame,
                   pictures=pictures, capCentre=tuple(CheckCap.CENTER),
                   bearingCentre=(CheckTopBearing.ANNULAR_CENTER_X, CheckTopBearing.ANNULAR_CENTER_Y)))


if __name__ == "__main__":
    import numpy as np
    from camera import CameraStateFile as SF, VisionFrame
    import camera.CameraProcessorServer as cps
    from utils.RedisUtils import knucklePictureKeyString, hubAndBottomBearingPictureKeyString, topBearingPictureKeyString
    IP = cps.ImageProcessor
    RESULTS = []

    def check(name, ok, detail=""):
        RESULTS.append((name, bool(ok)))
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   [{detail}]"), flush=True)

    rng = np.random.default_rng(0)
    pics = {knucklePictureKeyString: rng.integers(0, 255, (720, 1280, 3), dtype=np.uint8),
            hubAndBottomBearingPictureKeyString: rng.integers(0, 255, (720, 1280, 3), dtype=np.uint8),
            topBearingPictureKeyString: None}
    nom = VisionFrame.NOMINAL_PINS[("DOST", "LHS")]
    lx, ly = nom["pin_mid"][0] - nom["distance"] / 2, nom["pin_mid"][1]
    frame = VisionFrame.buildFrame("DOST", "LHS", ((lx + 12, ly - 7), (lx + nom["distance"] + 12, ly - 7 + 4.5)), None)
    ring = {"center": (642, 340), "inner_radius": 37, "outer_radius": 52, "profile_angles_deg": list(range(0, 360, 2))}
    attrs = dict(currentComponentQRCodeValue="7204838$400112VA1D$TEST", currentModelName="DOST", currentSide="LHS",
                 bearingCentreAndRadii=ring, bearingProfileQRCode="7204838$400112VA1D$TEST", visionFrame=frame)
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, SF.FILE_NAME)

    # C1
    t = time.monotonic(); okSave = SF.save(path, attrs, pics); secs = time.monotonic() - t
    a, p, why = SF.load(path, 3600)
    check(f"C1 saved ({secs:.2f} s, {os.path.getsize(path) / 1e6:.1f} MB) and loaded: attributes identical", okSave and a == attrs, why)
    check("C1 pictures pixel-identical (PNG), None kept as None",
          p is not None and all((pics[k] is None and p[k] is None) or (p[k] is not None and np.array_equal(pics[k], p[k]))
                                for k in pics), why)
    t = time.monotonic(); SF.save(path, attrs, pics); again = time.monotonic() - t
    check(f"C1 a second save of the same pictures encodes nothing again ({again:.3f} s against {secs:.3f} s)", again < secs)
    check("C1 written through a temporary file (no .tmp left behind)", not os.path.exists(path + ".tmp"))

    # C2
    old = time.time() - 7200
    import pickle
    with open(path, "rb") as f:
        payload = pickle.load(f)
    payload["savedAt"] = old
    with open(path, "wb") as f:
        pickle.dump(payload, f)
    a, p, why = SF.load(path, 3600)
    check("C2 a file older than the limit is not used", a is None and "old" in why, why)
    a, p, why = SF.load(path, 0)
    check("C2 limit 0 = any age", a is not None, why)
    check("C2 no file: not used", SF.load(os.path.join(tmp, "none.pkl"), 3600)[0] is None)
    with open(os.path.join(tmp, "broken.pkl"), "wb") as f:
        f.write(b"not a pickle")
    check("C2 a broken file: not used", SF.load(os.path.join(tmp, "broken.pkl"), 3600)[0] is None)

    # C3 - the camera server's own state, written as after a check, taken by a new process
    IP.stateFile = path
    IP.currentComponentQRCodeValue, IP.currentModelName, IP.currentSide = attrs["currentComponentQRCodeValue"], "DOST", "LHS"
    IP.bearingCentreAndRadii, IP.bearingProfileQRCode = ring, attrs["bearingProfileQRCode"]
    IP.visionFrame = frame
    IP.currentPictures.update(pics)
    check("C3 persistState writes the file", IP.persistState() and os.path.isfile(path))
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe()
    proc = ctx.Process(target=restartedCameraServer, args=(path, child)); proc.start()
    got = parent.recv() if parent.poll(120) else {}
    proc.join(10)
    placed = VisionFrame.placedCentres(frame)
    check("C3 the restarted process takes the cycle state", got.get("taken") is True, str(got.get("taken")))
    check("C3 QR code, model, side, bearing ring kept", got.get("qr") == attrs["currentComponentQRCodeValue"]
          and got.get("model") == "DOST" and got.get("side") == "LHS" and got.get("ring") == ring
          and got.get("ringQR") == attrs["bearingProfileQRCode"], str({k: got.get(k) for k in ("qr", "model", "side")}))
    check("C3 the earlier pictures kept", got.get("pictures", {}).get(knucklePictureKeyString) is not None
          and got["pictures"].get(hubAndBottomBearingPictureKeyString) is not None
          and got["pictures"].get(topBearingPictureKeyString) is None, str(got.get("pictures")))
    check("C3 the knuckle frame kept and applied to the checks (cap and top bearing centres placed by it)",
          got.get("frame") == frame and got.get("capCentre") == placed["cap.center"]
          and got.get("bearingCentre") == placed["topbearing.annular.center"],
          f"{got.get('capCentre')} vs {placed['cap.center']}, {got.get('bearingCentre')} vs {placed['topbearing.annular.center']}")

    # C4
    IP.endOfCycle()
    parent, child = ctx.Pipe()
    proc = ctx.Process(target=restartedCameraServer, args=(path, child)); proc.start()
    got = parent.recv() if parent.poll(120) else {}
    proc.join(10)
    check("C4 after the end of a cycle the file holds an empty state: a restart starts with nothing",
          got.get("qr") is None and got.get("frame") is None and got.get("ring") is None
          and all(v is None for v in got.get("pictures", {"x": 1}).values()), str(got))

    # C5 - wiring
    import utils.ProcessSupervisor as PS
    srcMain = (ROOT / "MainProgram.py").read_text(encoding="utf-8")      # (importing it needs tkinter)
    check("C5 MainProgram starts the camera server with no state file (None)",
          "args=(currentMode, None)" in srcMain, "")
    check("C5 ... and the supervisor restarts it with the state file",
          'restartArgs = {"Camera server": (currentMode, cameraStateFile())}' in srcMain
          and "restartArgs=restartArgs.get(name)" in srcMain
          and 'entry["restartArgs"]' in inspect.getsource(PS.ProcessSupervisor.checkOnce))
    srcCS = inspect.getsource(cps.CameraServer)
    check("C5 CameraServer reads the file only when given it, then writes to the default file",
          "if stateFile:\n        ImageProcessor.loadPersistedState(stateFile)" in srcCS
          and "ImageProcessor.stateFile = CameraStateFile.defaultPath()" in srcCS)
    check("C5 the state is written after every check (ConverterScalerAndProcessor) and at the end of a cycle",
          "ImageProcessor.persistState()" in inspect.getsource(cps.ConverterScalerAndProcessor.processItem)
          and "ImageProcessor.persistState()" in inspect.getsource(cps.ImageProcessor.endOfCycle))
    check("C5 the default file is <project root>/Temp/camera_server_state.pkl",
          os.path.normpath(SF.defaultPath()) == os.path.normpath(os.path.join(str(ROOT), "Temp", SF.FILE_NAME)), SF.defaultPath())

    failed = [n for n, ok_ in RESULTS if not ok_]
    print(f"\nRESULT {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed" + (f"; FAILED: {failed}" if failed else ""), flush=True)
    os._exit(1 if failed else 0)
