# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
VisionCheckWorker: the vision checks run in a separate process, so that a check that hangs (inside torch / MobileSAM,
where no flag can stop it) can be stopped by stopping the process.

  camera server (ConverterScalerAndProcessor)                worker process (_workerMain)
    keeps the cycle's state (QR code, earlier pictures,         loads the models once
    bearing ring) in ImageProcessor                             per check: takes the state sent with the job, runs
    sends a job: picture state, pictures, that state    --->    CameraProcessorServer.assessPictures (the same code
    waits at most <vendor>.vision.check.timeout.sec      <---   as in the camera server), sends back the verdict, the
    applies the state the check changed                         pictures and the state it changed

  No answer in time: the check is NOT OK ("check timed out after N s"), the worker process is stopped and a new one
  started (it loads the models again, about 10-30 s; until it is ready a check is NOT OK, "vision check worker
  starting"). The worker process dying is handled the same way. The cycle's state stays in the camera server, so a
  stopped check loses nothing of the cycle: the retake of the same check has the earlier pictures and the bearing ring.

Started from CameraServer() when <vendor>.vision.check.in.worker.process = True. The process is started with
multiprocessing "spawn" (as MainProgram starts the servers), daemon: it ends with the camera server.
"""

import multiprocessing
import threading
import time
from typing import Any, Dict, Optional, Tuple

READY_TIMEOUT_SECS = 600.0        # loading the models on a cold start can take a while
STOP_TIMEOUT_SECS = 3.0

_worker = None
_workerLock = threading.Lock()


def getVisionCheckWorker() -> "VisionCheckWorker":
    """The camera server's one worker (started on first use)."""
    global _worker
    with _workerLock:
        if _worker is None:
            _worker = VisionCheckWorker()
            _worker.start()
        return _worker


def _log(level: str, message: str, kind=None) -> None:
    try:
        from logutils.SlaveLoggers import logBoth
        from logutils.Logger import MessageType
        logBoth(level, "camera.VisionCheckWorker", message, kind or MessageType.GENERAL)
    except Exception:
        print(message, flush=True)


def _workerMain(conn) -> None:
    """The worker process: load the models, then run one job at a time."""
    try:
        from logutils.LogSettings import setLogProcess
        setLogProcess("cameraserver")
    except Exception:
        pass
    import camera.CameraProcessorServer as cps
    cps.ImageProcessor.fetchQRCodeInChecks = False        # the camera server sends the QR code with the job
    try:
        problems = cps.verifyVisionDependencies(loadModels=True)
    except Exception as e:
        problems = [f"vision dependencies could not be checked: {e}"]
    if not problems:
        _warmUp(cps)
    conn.send(("ready", problems))
    while True:
        try:
            message = conn.recv()
        except (EOFError, OSError):
            return                                      # the camera server is gone
        if not message or message[0] == "stop":
            return
        if message[0] == "ping":
            conn.send(("pong", message[1]))
            continue
        _, jobId, state, images, snapshot = message
        cps.ImageProcessor.restoreState(snapshot)
        sentPictures = dict(cps.ImageProcessor.currentPictures)
        allok, processed, original, error = False, None, None, None
        try:
            allok, processed, original = cps.assessPictures(state, images)
        except Exception as e:
            error = str(e) or type(e).__name__
        changes = {name: getattr(cps.ImageProcessor, name) for name in cps.ImageProcessor._STATE_ATTRIBUTES}
        changes["currentPictures"] = {key: picture for key, picture in cps.ImageProcessor.currentPictures.items()
                                      if picture is not sentPictures.get(key)}
        try:
            conn.send(("result", jobId, bool(allok), processed, original, changes, error))
        except (EOFError, OSError, BrokenPipeError):
            return


def _warmUp(cps) -> None:
    """Run each model-based check once on a blank picture before reporting ready. The first inference on a GPU
    is much slower than the later ones (kernel set-up); without this, the first real check after every (re)start
    could exceed the timeout, which would stop the worker again - and the next one would be cold again."""
    import numpy as np
    from statemachine.StateMachine import MachineState as MS
    blank = np.zeros((720, 1280, 3), dtype=np.uint8)
    t0 = time.monotonic()
    for state in (MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING,
                  MS.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER,
                  MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS,
                  MS.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK):
        snapshot = cps.ImageProcessor.snapshotState()
        try:
            cps.assessPictures(state, [blank] * 6)
        except Exception:
            pass
        cps.ImageProcessor.restoreState(snapshot)
    _log('logInfo', f"Vision check worker: warm-up of the model-based checks took {time.monotonic() - t0:.1f} s "
                    f"(the NOT OK results logged during it are on blank pictures, not parts)")


class VisionCheckWorker:
    """The camera server's side of the worker process. assess() is called by the one assessment thread
    (ConverterScalerAndProcessor); it is serialised anyway."""

    def __init__(self, target=None):
        # target: the worker process's main function (default _workerMain; the dry runs pass one that wraps it)
        self._target = target or _workerMain
        self._lock = threading.Lock()
        self._process = None
        self._conn = None
        self._ready = threading.Event()
        self._problems: list = []
        self._jobSeq: int = 0
        self.starts: int = 0
        self.timeouts: int = 0
        self.lastStopReason: str = ""

    # ------------------------------------------------------------------------------------------------ process
    def start(self) -> None:
        context = multiprocessing.get_context("spawn")
        parentConn, childConn = context.Pipe()
        process = context.Process(target=self._target, args=(childConn,), name="VisionCheckWorker", daemon=True)
        process.start()
        childConn.close()
        self._process, self._conn = process, parentConn
        self._ready.clear()
        self._problems = []
        self.starts += 1
        _log('logInfo', f"Vision check worker process started (pid {process.pid}); loading the models")
        threading.Thread(target=self._waitUntilReady, args=(process, parentConn), name="Vision check worker start",
                         daemon=True).start()

    def _waitUntilReady(self, process, conn) -> None:
        try:
            if conn.poll(READY_TIMEOUT_SECS):
                kind, problems = conn.recv()
                if process is not self._process:
                    return
                self._problems = list(problems or [])
                self._ready.set()
                if self._problems:
                    _log('logCritical', "Vision check worker: missing vision dependencies - every check is NOT OK:\n  - "
                         + "\n  - ".join(self._problems))
                else:
                    _log('logInfo', f"Vision check worker ready (pid {process.pid})")
                return
        except Exception as e:
            _log('logCritical', f"Vision check worker did not start: {e}")
        if process is self._process:
            with self._lock:
                self._restart("did not report ready")

    def stop(self) -> None:
        process, conn = self._process, self._conn
        self._process, self._conn = None, None
        self._ready.clear()
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
        if process is not None and process.is_alive():
            process.terminate()
            process.join(STOP_TIMEOUT_SECS)
            if process.is_alive():
                process.kill()
                process.join(STOP_TIMEOUT_SECS)

    def _restart(self, reason: str) -> None:
        self.lastStopReason = reason
        _log('logCritical', f"Vision check worker stopped ({reason}); starting a new one")
        self.stop()
        self.start()

    @property
    def ready(self) -> bool:
        return self._ready.is_set() and self._process is not None and self._process.is_alive()

    def waitUntilReady(self, timeout: float) -> bool:
        return self._ready.wait(timeout) and self.ready

    @property
    def pid(self) -> Optional[int]:
        return self._process.pid if self._process is not None else None

    # ------------------------------------------------------------------------------------------------ checks
    def assess(self, state, images: list, timeout: float) -> Tuple[bool, Any, Any, Optional[str]]:
        """(allok, processed image, original image, reason): reason None when the check gave the verdict."""
        from camera.CameraProcessorServer import ImageProcessor
        fallback = images[0] if images else None
        if not self.ready:
            if self._process is None or not self._process.is_alive():
                with self._lock:
                    self._restart("worker process not running")
            return False, fallback, fallback, "vision check worker starting"
        if self._problems:
            return False, fallback, fallback, "vision dependencies missing"
        with self._lock:
            self._jobSeq += 1
            jobId = self._jobSeq
            try:
                self._conn.send(("assess", jobId, state, images, ImageProcessor.snapshotState()))
            except (EOFError, OSError, BrokenPipeError, AttributeError) as e:
                self._restart(f"could not send the check ({e})")
                return False, fallback, fallback, "vision check worker stopped; restarting"
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                try:
                    if remaining <= 0 or not self._conn.poll(remaining):
                        self.timeouts += 1
                        reason = f"check timed out after {timeout:g} s"
                        self._restart(f"{getattr(state, 'name', state)}: {reason}")
                        return False, fallback, fallback, reason
                    message = self._conn.recv()
                except (EOFError, OSError, BrokenPipeError):
                    self._restart(f"{getattr(state, 'name', state)}: worker process stopped during the check")
                    return False, fallback, fallback, "vision check worker stopped during the check; restarting"
                if message and message[0] == "result" and message[1] == jobId:
                    break
            _, _, allok, processed, original, changes, error = message
        ImageProcessor.applyStateChanges(changes)
        if error:
            _log('logCritical', f"{getattr(state, 'name', state)}: the check raised {error}")
            return False, fallback, fallback, f"check failed ({error})"
        return allok, processed, original, None
