# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
VisionCheckWorker: the vision checks run in a separate process, so that a check that hangs (inside torch / MobileSAM,
where no flag can stop it) can be stopped by stopping the process.

  camera server (ConverterScalerAndProcessor)                worker process (_workerMain)
    keeps the cycle's state (QR code, earlier pictures,         loads the models once
    bearing ring) in ImageProcessor                             per check: takes the state sent with the job, runs
    sends a job: picture state, pictures, that state    --->    CameraProcessorServer.assessPictures (the same code
    waits at most vision.<vendor>.check.timeout.sec      <---   as in the camera server), sends back the verdict, the
    applies the state the check changed                         pictures and the state it changed

  No answer in time: the check is NOT OK ("check timed out after N s"), the worker process is stopped and a new one
  started (it loads the models again, about 10-30 s; until it is ready a check is NOT OK, "vision check worker
  starting"). The worker process dying is handled the same way. The cycle's state stays in the camera server, so a
  stopped check loses nothing of the cycle: the retake of the same check has the earlier pictures and the bearing ring.

Started from CameraServer() (the only way the vision checks run since 2026-11-15). The process is started with
multiprocessing "spawn" (as MainProgram starts the servers), daemon: it ends with the camera server.
"""

import multiprocessing
import threading
import time
from typing import Any, Optional, Tuple

from Configuration import CosThetaConfigurator

# loading the models on a cold start can take a while
READY_TIMEOUT_SECS = CosThetaConfigurator.getTuning("camera.worker.ready.timeout.sec", 600.0, minimum=10.0)
STOP_TIMEOUT_SECS = CosThetaConfigurator.getTuning("camera.worker.stop.timeout.sec", 3.0, minimum=0.5)
# this many timed-out checks in a row: the checks are too slow on this PC (alarm)
TIMEOUTS_IN_A_ROW_FOR_ALARM = CosThetaConfigurator.getTuning("camera.worker.timeouts.in.a.row.for.alarm", 3, minimum=1)
# A worker that does not reach "ready" (dies while starting, e.g. a missing DLL) is started again after a growing pause
# - 1, 2, 4 ... RESTART_BACKOFF_MAX_SECS s - instead of at once, which spawned processes in a tight loop (CHANGES 110).
# A restart after a worker that had been ready (a timed-out check, a crash during a check) stays immediate.
RESTART_BACKOFF_BASE_SECS = CosThetaConfigurator.getTuning("camera.worker.restart.backoff.base.sec", 1.0, minimum=0.1)
RESTART_BACKOFF_MAX_SECS = CosThetaConfigurator.getTuning("camera.worker.restart.backoff.max.sec", 60.0, minimum=0.1)

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
    cps.CheckNutAndPlateWasher.GUARD_DONE_BY_CALLER = True   # the camera server runs the bearing-ring guard (assess)
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
        checkStart = time.monotonic()
        try:
            allok, processed, original = cps.assessPictures(state, images)
        except Exception as e:
            error = str(e) or type(e).__name__
        checkSecs = time.monotonic() - checkStart
        changes = {name: getattr(cps.ImageProcessor, name) for name in cps.ImageProcessor._STATE_ATTRIBUTES}
        changes["currentPictures"] = {key: picture for key, picture in cps.ImageProcessor.currentPictures.items()
                                      if picture is not sentPictures.get(key)}
        try:
            conn.send(("result", jobId, bool(allok), processed, original, changes, error, checkSecs))
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
    # The blank pictures are not parts: never saved as trial pictures (save.all.pictures.because.its.in.trial.mode).
    # Before CHANGES 202 every worker start - camera server start and every restart after a timed-out check - wrote
    # a black picture into HubAndBottomBearing, NutAndPlateWasher, BungForComponentPress and NoBung.
    saveTrialPictures = cps.ImageProcessor.saveTrialPictures
    cps.ImageProcessor.saveTrialPictures = False
    try:
        for state in (MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING,
                      MS.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER,
                      MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_COMPONENT_PRESS,
                      MS.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNG):
            snapshot = cps.ImageProcessor.snapshotState()
            try:
                cps.assessPictures(state, [blank] * 6)
            except Exception:
                pass
            cps.ImageProcessor.restoreState(snapshot)
    finally:
        cps.ImageProcessor.saveTrialPictures = saveTrialPictures
    _log('logInfo', f"Vision check worker: warm-up of the model-based checks took {time.monotonic() - t0:.1f} s "
                    f"(the NOT OK results logged during it are on blank pictures, not parts)")


# Longest wait for the bearing-ring guard after the worker's verdict (it normally finishes during the check)
GUARD_MAX_WAIT_SECS = CosThetaConfigurator.getTuning("camera.bearing.ring.guard.max.wait.sec", 5.0, minimum=0.0)


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
        self.timeoutsInARow: int = 0       # reset by a check that gives its verdict in time
        self.lastStopReason: str = ""
        self.lastCheckSecs = None              # the last check's time in the worker, and the transfer around it
        self.lastTransferSecs = None
        self.failedStarts: int = 0             # starts in a row that did not reach ready (reset when one does)
        self.restartPending: bool = False      # a start is scheduled after a pause (back-off)

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
                self.failedStarts = 0
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
                self.failedStarts += 1
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

    def restartDelay(self) -> float:
        """The pause before the next start: 0 unless the last starts did not reach ready."""
        if self.failedStarts <= 0:
            return 0.0
        return min(RESTART_BACKOFF_BASE_SECS * 2 ** (self.failedStarts - 1), RESTART_BACKOFF_MAX_SECS)

    def _restart(self, reason: str) -> None:
        self.lastStopReason = reason
        self.stop()
        if self.restartPending:
            return                          # a start is already scheduled (the checks are NOT OK until it is ready)
        delay = self.restartDelay()
        if delay <= 0:
            _log('logCritical', f"Vision check worker stopped ({reason}); starting a new one")
            self.start()
            return
        _log('logCritical', f"Vision check worker stopped ({reason}); {self.failedStarts} start(s) in a row did not "
                            f"reach ready - starting a new one in {delay:g} s")
        self.restartPending = True
        timer = threading.Timer(delay, self._delayedStart)
        timer.daemon = True
        timer.start()

    def _delayedStart(self) -> None:
        with self._lock:
            self.restartPending = False
            if self._process is None:
                self.start()

    @property
    def ready(self) -> bool:
        return self._ready.is_set() and self._process is not None and self._process.is_alive()

    def waitUntilReady(self, timeout: float) -> bool:
        return self._ready.wait(timeout) and self.ready

    @property
    def pid(self) -> Optional[int]:
        return self._process.pid if self._process is not None else None

    # pictures to the worker and the result back taking longer than this: warned
    TRANSFER_WARNING_SECS = CosThetaConfigurator.getTuning("camera.worker.transfer.warning.sec", 1.0, minimum=0.0)

    def _logTimes(self, state, totalSecs: float, checkSecs) -> None:
        """Each check's time in the worker and the time to send the pictures there and the result back (the rest of
        the round trip), so that the plant PC's logs show both (CHANGES 85). Measured here: about 0.01 s for the
        three 1280 x 720 pictures (sent as six, the same three twice: pickled once each)."""
        self.lastCheckSecs, self.lastTransferSecs = checkSecs, (totalSecs - checkSecs) if checkSecs is not None else None
        name = getattr(state, 'name', state)
        if checkSecs is None:
            return
        transfer = totalSecs - checkSecs
        level = 'logWarning' if transfer > VisionCheckWorker.TRANSFER_WARNING_SECS else 'logInfo'
        _log(level, f"{name}: check {checkSecs:.2f} s in the worker, transfer {transfer:.2f} s"
                    + (" - slow transfer to the worker process" if level == 'logWarning' else ""))

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
            guard = self._startBearingRingGuard(state, images)    # runs here while the worker runs the check
            sentAt = time.monotonic()
            deadline = sentAt + timeout
            while True:
                remaining = deadline - time.monotonic()
                try:
                    if remaining <= 0 or not self._conn.poll(remaining):
                        self.timeouts += 1
                        self.timeoutsInARow += 1
                        reason = f"check timed out after {timeout:g} s"
                        if self.timeoutsInARow >= TIMEOUTS_IN_A_ROW_FOR_ALARM:
                            # every check times out (e.g. no GPU / a GPU fault on this PC): restarting the worker
                            # does not help; say so clearly instead of only "timed out" after every reload
                            reason = (f"{reason} ({self.timeoutsInARow} in a row: vision checks too slow on this PC - "
                                      f"check the GPU, then restart the application)")
                            _log('logCritical', f"Vision checks: {self.timeoutsInARow} timed out in a row (limit "
                                                f"{timeout:g} s). The checks are too slow on this PC; check the GPU "
                                                f"(nvidia-smi) and restart the application")
                        self._restart(f"{getattr(state, 'name', state)}: {reason}")
                        return False, fallback, fallback, reason
                    message = self._conn.recv()
                except (EOFError, OSError, BrokenPipeError):
                    self._restart(f"{getattr(state, 'name', state)}: worker process stopped during the check")
                    return False, fallback, fallback, "vision check worker stopped during the check; restarting"
                if message and message[0] == "result" and message[1] == jobId:
                    break
            _, _, allok, processed, original, changes, error = message[:7]
            self.timeoutsInARow = 0
            self._logTimes(state, time.monotonic() - sentAt, message[7] if len(message) > 7 else None)
        ImageProcessor.applyStateChanges(changes)
        if error:
            _log('logCritical', f"{getattr(state, 'name', state)}: the check raised {error}")
            return False, fallback, fallback, f"check failed ({error})"
        if guard is not None:
            notOk, annotated, reason = self._finishBearingRingGuard(guard, images[0])
            if notOk:                       # a bearing-sized ring, or the guard could not decide
                return False, (annotated if annotated is not None else processed), original, reason
        return allok, processed, original, None

    # ------------------------------------------------------------------------------------------------ guard
    @staticmethod
    def _startBearingRingGuard(state, images: list):
        """Nut and plate washer check only, guard on: the top bearing ring finder on a copy of the picture, in a thread of
        the camera server, while the worker process runs the nut check (two processes: truly in parallel). None when
        not applicable."""
        from camera.CameraProcessorServer import CheckNutAndPlateWasher, CheckTopBearing
        from statemachine.StateMachine import MachineState
        if (state != MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER or not images
                or images[0] is None or not CheckNutAndPlateWasher.BEARING_RING_GUARD):
            return None
        picture = images[0].copy()
        box = {}

        def run():
            started = time.monotonic()
            try:
                box["ring"] = CheckTopBearing.findRing(picture)
            except Exception as e:
                box["error"] = str(e) or type(e).__name__
            box["secs"] = time.monotonic() - started

        thread = threading.Thread(target=run, name="Bearing-ring guard", daemon=True)
        thread.start()
        return thread, box

    @staticmethod
    def _finishBearingRingGuard(guard, picture) -> Tuple[bool, Any, str]:
        """Wait for the guard (it normally finished while the worker ran the check) and decide: (NOT OK, marked picture
        or None, reason). NOT OK when the ring is bearing-sized, and when the guard did not answer in time or failed."""
        from camera.CameraProcessorServer import CheckNutAndPlateWasher
        thread, box = guard
        waitedFrom = time.monotonic()
        thread.join(GUARD_MAX_WAIT_SECS)
        waited = time.monotonic() - waitedFrom
        # The guard could not decide: NOT OK (user, 2026-11-15) - an uncovered bearing must not pass because the guard
        # was slow or failed. The picture is returned unmarked; the reason goes to the frontend notice and the log.
        if thread.is_alive():
            reason = f"bearing-ring guard: no answer in {GUARD_MAX_WAIT_SECS:g} s after the check"
            _log('logError', f"Bearing-ring guard: no answer in {GUARD_MAX_WAIT_SECS:g} s after the check - NOT OK")
            return True, None, reason
        if "error" in box:
            reason = f"bearing-ring guard: ring finder failed ({box['error']})"
            _log('logError', f"Bearing-ring guard: ring finder failed ({box['error']}) - NOT OK")
            return True, None, reason
        visible, annotated, reason = CheckNutAndPlateWasher.bearingRingVerdict(picture, box.get("ring"))
        _log('logError' if visible else 'logInfo',
             f"Bearing-ring guard (camera server): {reason or 'no ring in the picture'} - ring finder {box['secs']:.2f} s, "
             f"waited {waited:.2f} s after the check")
        return visible, annotated, reason
