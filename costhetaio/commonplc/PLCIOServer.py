# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
PLCIOServer: the IOServer for every PLC make - the 28-state machine, the PLC handshakes (OK, pause, Done), torque
reads and delayed tag resets, cycle settings written in state 2, the end-of-cycle / abort / start-up reset with
verified writes, PLC restart detection, emergency abort, heartbeats, Redis messages, tag publishing and the audit
trail.

It talks to the PLC only through two PLCDataIO objects (costhetaio/commonplc/PLCDataIO.py), one for reads and one for
writes, made by createPLCDataIO() for plc.vendor: AllenBradleyPLCDataIO (pycomm3, Machine 1), SiemensPLCDataIO (pyS7,
Machine 2) or MockPLCDataIO (mockInstance=True). Nothing here depends on the PLC make; the tag names and settings
come from the vendor's keys of ApplicationConfiguration.properties (Configuration picks them by plc.vendor).
"""

from utils.SuppressedExceptions import noteSuppressed   # logs exceptions that are caught and ignored
import sys
import contextlib
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Type, Union
from psycopg2.pool import ThreadedConnectionPool

from logutils import Logger
from logutils.SlaveLoggers import SlaveConsoleLogger, SlaveFileLogger
from logutils.LogSettings import setLogProcess, publishCycleContext, auditPLC, auditConnectionToggle
from utils.CosThetaPrintUtils import (
    printBlue, printBold, printBoldBlue, printBoldGreen, printBoldRed, printBoldYellow, printLight)
import json
import os
from redis import Redis
from utils.RedisUtils import (
    ALIVE, DEAD, clearFrontendNotice, clearKeyCommunicationQueuesOnAbort, getStopCommandFromQueue,
    io2cameraq, moveAheadToNextComponent, notok, publishCycleSettingsForQRCode, publishIOServerState,
    readAbortInIOServerFromFEServer, readDataInIOServerFromCameraServer, readDataInIOServerFromQRCodeServer,
    readServerStatusesInIOServerFromHeartbeatServer, redisHostname, redisPort, requestIdKeyString,
    sendAbortFromIOServerToQRCodeServer, sendDataFromCameraServerToFEServer, sendDataFromIOServerToCameraServer,
    sendDataFromIOServerToFEServer, sendDataFromIOServerToQRCodeServer, sendEmergencyAbortFromIOServerToFEServer,
    sendEndOfCycleFromIOServerToCameraServer, sendHeartbeatFromIOServerToHeartbeatServer, sendStoppedResponse,
    setActiveCameraRequest, setFrontendNotice)
from BaseUtils import getFullyQualifiedName

from Configuration import CosThetaConfigurator
from statemachine.StateMachine import MachineState, MachineStateMachine
from processors.GenericQueueProcessor import GenericQueueProcessor
from utils.QRCodeHelper import getModel_LHSRHS_AndTonnage
from persistence.Persistence import getDatabaseName, setDatabaseName, getMachineSettings
from costhetaio.commonplc.PLCDataIO import PLCDataIO
from costhetaio.commonplc.PLCDataIOFactory import createPLCDataIO, plcDataIOClass, plcVendor


# ==================== Global Reference for UpdateTagsToDefaultProcessor ====================
# This global reference allows other threads to add items to the processor's queue.
# Set after IOServer instantiation in startIOServer()
_updateTagsProcessor: Optional["UpdateTagsToDefaultProcessor"] = None


def getUpdateTagsProcessor() -> Optional["UpdateTagsToDefaultProcessor"]:
    """
    Get the global UpdateTagsToDefaultProcessor instance.

    Returns:
        The global processor instance, or None if not yet initialized.
    """
    global _updateTagsProcessor
    return _updateTagsProcessor


def addTagUpdateRequest(request: Dict[str, Any]) -> bool:
    """
    Add a tag update request to the UpdateTagsToDefaultProcessor queue.

    This is a convenience function for other threads to schedule delayed tag writes.

    Args:
        request: A dictionary in the following format:
            {
                "sleepTimeForDelay": <float>,  # Seconds to wait before writing tags
                "tag1": {"tag": <str>, "type": <"bool"|"float">, "value": <bool|float>},
                "tag2": {"tag": <str>, "type": <"bool"|"float">, "value": <bool|float>},
                ...  # Can have any number of tagN entries
            }

    Returns:
        True if request was added successfully, False otherwise.

    Examples:
        # Example 1: Reset a single bool tag after 1.5 seconds
        addTagUpdateRequest({
            "sleepTimeForDelay": 1.5,
            "tag1": {"tag": "PLC_PC_CheckQRCode", "type": "bool", "value": False}
        })

        # Example 2: Reset multiple tags after 2 seconds
        addTagUpdateRequest({
            "sleepTimeForDelay": 2.0,
            "tag1": {"tag": "PLC_PC_Station2TorqueValueSet", "type": "bool", "value": False},
            "tag2": {"tag": "PLC_PC_Station2TorqueValue", "type": "float", "value": 0.0}
        })

        # Example 3: Set multiple bool tags to True after 0.5 seconds
        addTagUpdateRequest({
            "sleepTimeForDelay": 0.5,
            "tag1": {"tag": "PC_PLC_KnuckleCheckOK", "type": "bool", "value": True},
            "tag2": {"tag": "PC_PLC_KnuckleCheckDone", "type": "bool", "value": True},
            "tag3": {"tag": "PLC_PC_CheckKnuckle", "type": "bool", "value": False}
        })
    """
    global _updateTagsProcessor
    if _updateTagsProcessor is not None:
        result = _updateTagsProcessor.addItem(request)
        print(f"Added item {request} in " )
        return result is not None
    return False



class _FileLogAdapter:
    """The IOServer's self.logger: messages go to the central log file at the matching level (before, they went to
    pycomm3's logger, which is set to CRITICAL with no handler, so they were lost). pycomm3 itself stays silent."""

    def __init__(self, source: str):
        self.source = source

    def _log(self, level: int, msg, *args) -> None:
        try:
            text = (str(msg) % args) if args else str(msg)
        except Exception:
            text = f"{msg} {args}"
        try:
            from logutils.AbstractSlaveLogger import callerOverride
            frame = sys._getframe(2)
            callerOverride.value = (frame.f_code.co_name, frame.f_lineno)
            try:
                SlaveFileLogger.getInstance().logMessage(level, self.source, text,
                                                         Logger.PROBLEM if level >= Logger.CONSIDERACTION else Logger.GENERAL)
            finally:
                callerOverride.value = None
        except Exception:
            noteSuppressed()

    def debug(self, msg, *args, **kw): self._log(Logger.DEBUG, msg, *args)
    def info(self, msg, *args, **kw): self._log(Logger.INFO, msg, *args)
    def warning(self, msg, *args, **kw): self._log(Logger.TAKENOTE, msg, *args)
    warn = warning
    def error(self, msg, *args, **kw): self._log(Logger.CONSIDERACTION, msg, *args)
    def critical(self, msg, *args, **kw): self._log(Logger.TAKEACTION, msg, *args)
    fatal = critical
    exception = error

class UpdateTagsToDefaultProcessor(GenericQueueProcessor):
    """
    A processor that handles delayed tag write operations to the PLC.

    This processor runs in a separate thread and processes queued tag update requests.
    Each request specifies a delay time and one or more tags to write.

    Queue Item Format:
        {
            "sleepTimeForDelay": <float>,  # Seconds to wait before writing tags
            "tag1": {"tag": <str>, "type": <"bool"|"float">, "value": <bool|float>},
            "tag2": {"tag": <str>, "type": <"bool"|"float">, "value": <bool|float>},
            ...  # Additional tags as needed (tag3, tag4, etc.)
        }

    Supported Types:
        - "bool": Writes boolean value using PLCIOServer.write_bool()
        - "float": Writes float value using PLCIOServer.write_float()

    Usage Examples:
        # Get the processor instance
        processor = getUpdateTagsProcessor()

        # Or use the convenience function
        addTagUpdateRequest({
            "sleepTimeForDelay": 1.0,
            "tag1": {"tag": "MyTag", "type": "bool", "value": False}
        })
    """

    logSource = getFullyQualifiedName(__file__)

    def __init__(self, ioServerInstance: "PLCIOServer", name: str = "UpdateTagsToDefaultProcessor"):
        """
        Initialize the UpdateTagsToDefaultProcessor.

        Args:
            ioServerInstance: Reference to the IOServer instance for writing tags.
            name: Name for this processor thread.
        """
        super().__init__(
            name=name,
            consumer=None,
            timeout=1,
            sleepTime=0.25,  # 250 ms sleep time
            blocking=False,  # Non-blocking to allow periodic wake-up
            monitorRedisQueueForStopping=False,
            max_size=64
        )
        UpdateTagsToDefaultProcessor.logSource = getFullyQualifiedName(__file__, __class__)
        self.ioServer = ioServerInstance

    def preWorkLoop(self):
        """No pre-work needed."""
        pass

    def postWorkLoop(self):
        """No post-work needed."""
        pass

    def processItem(self, item: Dict[str, Any]) -> Any:
        """
        Process a tag update request.

        Waits for the specified delay time, then writes each tag with its value.

        Args:
            item: Dictionary containing sleepTimeForDelay and tag entries.

        Returns:
            True if all tags were written successfully, False otherwise.
        """
        if item is None:
            return False

        try:
            # Get the sleep delay time
            sleepTimeForDelay = item.get("sleepTimeForDelay", 0.0)
            printLight(f"UpdateTagProcessor sleeping for {sleepTimeForDelay} secs")
            if sleepTimeForDelay > 0:
                time.sleep(sleepTimeForDelay)

            allSuccess = True

            # Process all tag entries (tag1, tag2, tag3, etc.)
            for key, tagInfo in item.items():
                if key == "sleepTimeForDelay":
                    continue

                if not isinstance(tagInfo, dict):
                    continue

                tagName = tagInfo.get("tag")
                tagType = tagInfo.get("type")
                tagValue = tagInfo.get("value")

                print(f"Got tag as {tagName}, which is of type {tagType}, which needs to assigned value {tagValue}")
                if tagName is None or tagType is None or tagValue is None:
                    printBoldRed(f"UpdateTagsToDefaultProcessor: Invalid tag entry {key}: {tagInfo}")
                    allSuccess = False
                    continue

                success = False
                print("About to call write_bool() or write_float()")
                if tagType == "bool":
                    success = self.ioServer.write_bool(tagName, bool(tagValue))
                elif tagType == "float":
                    success = self.ioServer.write_float(tagName, float(tagValue))
                else:
                    printBoldRed(f"UpdateTagsToDefaultProcessor: Unknown type '{tagType}' for tag '{tagName}'")
                    allSuccess = False
                    continue

                if success:
                    printLight(f"UpdateTagsToDefaultProcessor: Wrote {tagValue} to {tagName}")
                else:
                    printBoldRed(f"UpdateTagsToDefaultProcessor: Failed to write {tagValue} to {tagName}")
                    allSuccess = False

            return allSuccess

        except Exception as e:
            printBoldRed(f"UpdateTagsToDefaultProcessor: Exception processing item: {e}")
            SlaveFileLogger.getInstance().logTakeAction(
                UpdateTagsToDefaultProcessor.logSource,
                f"Exception processing tag update request: {e}",
                Logger.PROBLEM
            )
            return False


class DelayedJobScheduler:
    """ONE long-lived thread that runs jobs at a given time (instead of a new thread per job). Jobs have a key; a
    newer job with the same key replaces the pending one, and a pending job can be cancelled by its key.
    Jobs run one after the other in this thread, in order of their due time."""

    def __init__(self, name: str):
        self._cond = threading.Condition()
        self._heap: list = []                 # (dueTime, sequence, key)
        self._jobs: Dict[str, tuple] = {}     # key -> (sequence, function, info)
        self._sequence = 0
        self._thread = threading.Thread(name=name, target=self._run, daemon=True)
        self._thread.start()

    def schedule(self, key: str, delaySecs: float, function, info=None):
        """Run function() after delaySecs; returns the info of a pending job with this key that it replaced."""
        import heapq
        with self._cond:
            self._sequence += 1
            replaced = self._jobs.get(key)
            self._jobs[key] = (self._sequence, function, info)
            heapq.heappush(self._heap, (time.monotonic() + max(0.0, delaySecs), self._sequence, key))
            self._cond.notify()
        return replaced[2] if replaced else None

    def cancel(self, key: str):
        """Cancel the pending job with this key; returns its info (None if there was none)."""
        with self._cond:
            job = self._jobs.pop(key, None)
        return job[2] if job else None

    def runNow(self, key: str) -> bool:
        """Run the pending job with this key now, in the caller's thread, instead of at its time. False if none."""
        with self._cond:
            job = self._jobs.pop(key, None)
        if job is None:
            return False
        try:
            job[1]()
        except Exception as e:
            printBoldRed(f"Delayed job {key} failed: {e}")
        return True

    def pendingInfos(self) -> list:
        """The info of every pending job."""
        with self._cond:
            return [job[2] for job in self._jobs.values()]

    def cancelAll(self) -> list:
        with self._cond:
            infos = [job[2] for job in self._jobs.values()]
            self._jobs.clear()
        return infos

    def pending(self) -> int:
        with self._cond:
            return len(self._jobs)

    def _run(self):
        import heapq
        while True:
            with self._cond:
                while True:
                    while self._heap and self._jobs.get(self._heap[0][2], (None,))[0] != self._heap[0][1]:
                        heapq.heappop(self._heap)          # cancelled or replaced
                    if not self._heap:
                        self._cond.wait()
                        continue
                    due, sequence, key = self._heap[0]
                    wait = due - time.monotonic()
                    if wait > 0:
                        self._cond.wait(wait)
                        continue
                    heapq.heappop(self._heap)
                    _, function, _ = self._jobs.pop(key)
                    break
            try:
                function()
            except Exception as e:
                printBoldRed(f"Delayed job {key} failed: {e}")


class PLCIOServer:
    # ==================== QR Code Tags (Step 0) ====================
    PLC_PC_CheckQRCode: str = CosThetaConfigurator.getInstance().getPlcPcCheckQRCodeTagName()
    PC_PLC_QRCodeCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcQRCodeOKTagName()
    PC_PLC_QRCodeCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcQRCodeDoneTagName()

    # ==================== Knuckle Tags (Step 1) ====================
    PLC_PC_CheckKnuckle: str = CosThetaConfigurator.getInstance().getPlcPcCheckKnuckleTagName()
    PC_PLC_KnuckleCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcKnuckleCheckOKTagName()
    PC_PLC_KnuckleCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcKnuckleCheckDoneTagName()

    # ==================== Hub Tags (Step 2) ====================
    PLC_PC_CheckHub: str = CosThetaConfigurator.getInstance().getPlcPcCheckHubTagName()
    PC_PLC_HubCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcHubCheckOKTagName()
    PC_PLC_HubCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcHubCheckDoneTagName()

    # ==================== Hub and Second Bearing Tags (Step 3) ====================
    PLC_PC_CheckHubAndSecondBearing: str = CosThetaConfigurator.getInstance().getPlcPcCheckHubAndSecondBearingTagName()
    PC_PLC_HubAndSecondBearingCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcHubAndSecondBearingCheckOKTagName()
    PC_PLC_HubAndSecondBearingCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcHubAndSecondBearingCheckDoneTagName()

    # ==================== Nut and Plate Washer Tags (Step 4) ====================
    PLC_PC_CheckNutAndPlateWasher: str = CosThetaConfigurator.getInstance().getPlcPcCheckNutAndPlateWasherTagName()
    PC_PLC_NutAndPlateWasherCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcNutAndPlateWasherOKTagName()
    PC_PLC_NutAndPlateWasherCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcNutAndPlateWasherDoneTagName()

    # ==================== Station 2 Torque (Tightening) Tags (Step 5 and 10) ====================
    PLC_PC_Station2TorqueValueSet: str = CosThetaConfigurator.getInstance().getPlcPcStation2TorqueValueSetTagName()
    # Read with each Station 2 torque value and logged; no action on it yet. Empty when the PLC does not provide it.
    PLC_PC_Station2RotationDuringTighteningDone: str = CosThetaConfigurator.getInstance().getPlcPcStation2RotationDuringTighteningDoneTagName()
    PLC_PC_Station2TorqueValue: str = CosThetaConfigurator.getInstance().getPlcPcStation2TorqueValueTagName()

    # ==================== No Cap Bunk Tags (Step 7) ====================
    PLC_PC_CheckNoCapBunk: str = CosThetaConfigurator.getInstance().getPlcPcCheckNoCapBunkTagName()
    PC_PLC_NoCapBunkCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcNoCapBunkCheckOKTagName()
    PC_PLC_NoCapBunkCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcNoCapBunkCheckDoneTagName()

    # ==================== Component Press Done Tag (Step 8) ====================
    PLC_PC_ComponentPressDone: str = CosThetaConfigurator.getInstance().getPlcPcComponentPressDoneTagName()

    # ==================== No Bunk Tags (Step 9) ====================
    PLC_PC_CheckNoBunk: str = CosThetaConfigurator.getInstance().getPlcPcCheckNoBunkTagName()
    PC_PLC_NoBunkCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcNoBunkCheckOKTagName()
    PC_PLC_NoBunkCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcNoBunkCheckDoneTagName()

    # ==================== Split Pin and Washer Tags (Step 11) ====================
    PLC_PC_CheckSplitPinAndWasher: str = CosThetaConfigurator.getInstance().getPlcPcCheckSplitPinAndWasherTagName()
    PC_PLC_SplitPinAndWasherCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcSplitPinAndWasherCheckOKTagName()
    PC_PLC_SplitPinAndWasherCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcSplitPinAndWasherCheckDoneTagName()

    # ==================== Cap Tags (Step 12) ====================
    PLC_PC_CheckCap: str = CosThetaConfigurator.getInstance().getPlcPcCheckCapTagName()
    PC_PLC_CapCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcCapCheckOKTagName()
    PC_PLP_CapCheckOK: str = PC_PLC_CapCheckOK      # the earlier, misspelt name (kept as an alias for older scripts)
    PC_PLC_CapCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcCapCheckDoneTagName()

    # ==================== Bunk Tags (Step 13) ====================
    PLC_PC_CheckBunk: str = CosThetaConfigurator.getInstance().getPlcPcCheckBunkTagName()
    PC_PLC_BunkCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcBunkCheckOKTagName()
    PC_PLC_BunkCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcBunkCheckDoneTagName()

    # ==================== Cap Press Done Tag (Step 14) ====================
    PLC_PC_CapPressDone: str = CosThetaConfigurator.getInstance().getPlcPcCapPressDoneTagName()

    # ==================== Station 3 Free Rotation Torque Tags (Step 15) ====================
    PLC_PC_Station3TorqueValueSet: str = CosThetaConfigurator.getInstance().getPlcPcStation3TorqueValueSetTagName()
    PLC_PC_Station3TorqueValue: str = CosThetaConfigurator.getInstance().getPlcPcStation3TorqueValueTagName()

    # ==================== Machine 2 cycle settings (PC writes to PLC in state 2) ====================
    PC_PLC_LH_RH_Selection: str = CosThetaConfigurator.getInstance().getPcPlcLHRHSelectionTagName()      # INT 1=LHS, 2=RHS
    PC_PLC_ModelSelection: str = CosThetaConfigurator.getInstance().getPcPlcModelSelectionTagName()       # INT 1=DOST, 2=DOSTPLUS
    PC_PLC_TighteningTorque1Setpoint: str = CosThetaConfigurator.getInstance().getPcPlcTighteningTorque1SetpointTagName()  # REAL Nm
    PC_PLC_TighteningTorque2Setpoint: str = CosThetaConfigurator.getInstance().getPcPlcTighteningTorque2SetpointTagName()  # REAL Nm
    PC_PLC_RotationRPM1: str = CosThetaConfigurator.getInstance().getPcPlcRotation1RPMTagName()           # INT rpm
    PC_PLC_RotationRPM2: str = CosThetaConfigurator.getInstance().getPcPlcRotation2RPMTagName()           # INT rpm
    # One rotation RPM and the two rotation counts (REAL), both PLCs. An empty tag name = not on this PLC, not written.
    PC_PLC_RotationRPM: str = CosThetaConfigurator.getInstance().getPcPlcRotationRPMTagName()             # REAL rpm
    PC_PLC_NoOfRotation1CCW: str = CosThetaConfigurator.getInstance().getPcPlcNoOfRotation1CCWTagName()   # REAL 1 / 2
    PC_PLC_NoOfRotation1CW: str = CosThetaConfigurator.getInstance().getPcPlcNoOfRotation1CWTagName()     # REAL 1 / 2

    LHS_CODE: int = 1
    RHS_CODE: int = 2
    MODEL_CODES: Dict[str, int] = {"DOST": 1, "DOSTPLUS": 2}

    # ==================== System Tags ====================
    PLC_PC_EmergencyAbort: str = CosThetaConfigurator.getInstance().getPlcPcEmergencyAbortTagName()
    PLC_PC_WatchTag: str = CosThetaConfigurator.getInstance().getPlcPcWatchTagName()
    PC_PLC_ConnectionStatusTag: str = CosThetaConfigurator.getInstance().getPcPlcConnectionStatusTagName()
    # Optional: True when the PC aborts a cycle itself (frontend abort, frontend / heartbeat server down, PLC restart),
    # False when the next QR code is accepted. Empty = not on this PLC (not written).
    PC_PLC_CycleAborted: str = CosThetaConfigurator.getInstance().getPcPlcCycleAbortedTagName()
    # Tags the PLC programme may not have yet: named in the properties, but the IOServer starts without their address
    # (Siemens: IOServerProperties.yaml) and writes them only where they exist; a write that cannot be made is logged.
    # True in state 2 = this component skips states 13-19 (bung, component press, bung removal, second tightening):
    # the model's Machine Setting "Bypass Component Press and Second Tightening"; the PLC branches on it
    PC_PLC_Skip_Second_Tightening: str = CosThetaConfigurator.getInstance().getPcPlcSkipSecondTighteningTagName()
    OPTIONAL_TAG_ATTRS = ("PC_PLC_CycleAborted", "PC_PLC_Skip_Second_Tightening")
    # states skipped by the bypass: after the Torque 1 value (state 12) straight to the split pin picture (state 20)
    SKIP_TO_STATE_AFTER_TORQUE_1 = MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER
    FRONTEND_ABORT_WRITE_ATTEMPTS: int = 3      # the frontend's abort: PC_PLC_CycleAborted = True, then abort

    # ==================== Configuration Values ====================
    PLC_IP: str = CosThetaConfigurator.getInstance().getPlcIP()
    PLC_DEBUG: bool = CosThetaConfigurator.getInstance().getPlcDebug()
    PLC_SLEEPTIME_BETWEEN_OK_AND_DONE: float = CosThetaConfigurator.getInstance().getPlcSleepTimeBetweenOKAndDone()

    # ==================== Constants ====================
    FALSE_FLOAT_VALUE: float = -1234.
    FALSE_INT_VALUE: int = -1234
    WATCH_TAG_VALUE: int = 1234
    SETTINGS_WRITE_ATTEMPTS: int = 3        # per cycle-setting tag, in state 2
    WRITE_RETRY_INTERVAL: float = 0.2       # s between write attempts
    VISION_RESULT_RESET_ATTEMPTS: int = 5          # attempts to set a vision check's OK / Done back to False
    RESET_WRITE_ATTEMPTS: int = 5           # per tag in resetForNextCycle(): write + read back until they agree
    RESET_FLOAT_TOLERANCE: float = 1e-3     # a REAL read back within this of the value written counts as written
    REFERENCE_READ_ATTEMPTS: int = 3        # reads of the reference tag after a reconnect before giving up for now
    NO_CONNECTION: int = -1986754392
    CONSECUTIVE_CONNECTION_DOWN_COUNT: int = 0

    logSource = getFullyQualifiedName(__file__)
    LAST_CONNECTION_STATUS_UPDATED: bool = False
    LOG_CONNECTION_PROBLEM_EVERY_N_SECS = CosThetaConfigurator.getInstance().getLogDisconnectionsAfterNSecs()

    def __init__(self, plc_ip: Union[str, None] = None, mode : str = "TEST", mockInstance: bool = False):
        # Class variables
        PLCIOServer.logSource = getFullyQualifiedName(__file__, __class__)
        setLogProcess("ioserver")          # per-server log level, crash capture, live configuration
        self.machineState = MachineStateMachine()  # Instance of MachineStateMachine
        self.machineState.stateChangeListener = self._onStateChange
        self._cycleEndRedisConnection = None  # end-of-cycle message to the camera server (_onStateChange)
        # State sync (CHANGES 108): every state change published for the frontend (utils/RedisUtils.publishIOServerState)
        self._stateRedisConnection = None
        self._stateSeq: int = 0
        self._statePublishFailing = False
        # Delayed resets of the torque and press tags (scheduleTagReset): one scheduler thread for all of them.
        # _awaitingTriggerReset: the former "trigger read False first" gate after a NOT OK - kept, no longer set
        self._resultResetScheduler = DelayedJobScheduler("PLC Handshake Reset Scheduler")
        self._awaitingTriggerReset: set = set()
        self._awaitingTriggerSince: Dict[MachineState, Optional[float]] = {}
        self._awaitingTriggerResetLock = threading.Lock()
        self._abortLock = threading.RLock()  # one abort at a time (PLC emergency, heartbeat loss, frontend)
        # the read and write loops, for the heartbeat (DEAD while one is not running or stuck: utils/WorkerHealth.py)
        from utils.WorkerHealth import WorkerWatch
        self.lastReadLoopAt: float = time.monotonic()
        self.lastWriteLoopAt: float = time.monotonic()
        self.lastEmergencyLoopAt: float = time.monotonic()
        self.lastToggleLoopAt: float = time.monotonic()
        self.workers = WorkerWatch("IOServer")
        self.workers.add("read loop", lambda: getattr(self, "continuousReadLoop", None), lastTick=lambda: self.lastReadLoopAt)
        self.workers.add("write loop", lambda: getattr(self, "continuousDoLoop", None), lastTick=lambda: self.lastWriteLoopAt)
        self.workers.add("emergency monitoring", lambda: getattr(self, "emergencyMonitoringThread", None),
                         lastTick=lambda: self.lastEmergencyLoopAt)
        self.workers.add("connection-status toggle", lambda: getattr(self, "connectionStatusToggleThread", None),
                         lastTick=lambda: self.lastToggleLoopAt)
        self.abortCount: int = 0
        self.lastAbortReason: str = ""
        self.cycleId: int = 0              # incremented at each accepted QR code (log context "[QR=... cycle=N]")
        self._lastAuditedRead = None
        self.shutdown = False

        self.mode = mode
        setDatabaseName(mode = self.mode, createDB=False)

        self.read_interval = CosThetaConfigurator.getInstance().getPlcReadInterval()  # 200 ms
        self.write_loop_interval = CosThetaConfigurator.getInstance().getPlcWriteInterval()  # 100 ms
        self.CONNECTION_CHECK_INTERVAL = CosThetaConfigurator.getInstance().getPlcConnectionCheckInterval()  # seconds
        CosThetaConfigurator.addReloadListener(self._applyLiveConfiguration)

        self.redisHostname: str = CosThetaConfigurator.getInstance().getRedisHost()
        self.redisPort: int = CosThetaConfigurator.getInstance().getRedisPort()

        self.redisConnectionForReads: Union[Redis, None] = None
        self.clientRedisConnectedForReads: bool = False
        self.redisConnectionForWrites: Union[Redis, None] = None
        self.clientRedisConnectedForWrites: bool = False
        self.heartbeatRedisConnection: Union[Redis, None] = None
        self.heartbeatRedisConnected: bool = False
        self.combinedHeartbeatRedisConnection: Union[Redis, None] = None
        self.combinedHeartbeatRedisConnected: bool = False
        self.emergencyRedisConnection: Union[Redis, None] = None
        self.emergencyRedisConnected: bool = False

        self.connectToRedis(True)
        self.connectToRedisForHeartbeatAndEmergencyMonitoring(True)

        self.plc_ip = plc_ip if plc_ip is not None else PLCIOServer.PLC_IP
        self.mockInstance = mockInstance

        # The PLC: one PLCDataIO for reads, one for writes (None while not connected). Each has its own lock.
        self.vendor: str = plcVendor()
        # e.g. Siemens addresses; the optional tags (OPTIONAL_TAG_ATTRS) may have none
        plcDataIOClass(self.vendor).checkTagConfiguration(PLCIOServer.requiredTagNames())
        self._tagsMissingOnPlc: set = set()             # Allen-Bradley: tags the PLC does not know (first connection)
        self._skipSecondTighteningThisCycle: bool = False   # written True to the PLC in this cycle's state 2
        self._optionalTagNoticeGiven: set = set()
        self.dataIOForReads: Optional[PLCDataIO] = None
        self.dataIOForWrites: Optional[PLCDataIO] = None
        self._tagsCheckedOnPlc: bool = False

        # PLC restart detection (see _verifyPlcAfterReconnect):
        #   _plcTrueTags   PLC_PC tags known to be True right now, in the order they became True (the last one is
        #                  the reference). Added when the read loop sees the PLC raise one (or when the PC arms
        #                  CheckQRCode); removed when the PC writes it False, or when the PLC is seen to drop it
        #                  while connected (_followReferenceTag).
        #   _plcCheckPending  set when the PLC connection is lost; until the PLC has been checked after the
        #                  reconnect, only the check itself (and the reset it may do) can write to the PLC, and the
        #                  read loop does nothing.
        #   _resetIncomplete  resetForNextCycle() could not write and verify every tag; redone after the reconnect.
        self._plcTrueTags: List[str] = []
        self._plcTrueLock = threading.Lock()
        self._plcCheckPending: bool = False
        self._resetIncomplete: bool = False
        self._privileged = threading.local()      # .on = this thread may write while _plcCheckPending
        self.plcRestartsDetected: int = 0
        #   _sideWrittenThisCycle  PC_PLC_LH_RH_Selection as written in state 2 of this cycle (1 / 2): the fallback
        #                  reference when no PLC-raised tag is True (between steps the PC has cleared every trigger)
        self._sideWrittenThisCycle: Optional[int] = None

        # Database connection pool for reading machine settings
        self.dbConnectionPool: Union[ThreadedConnectionPool, None] = None
        self._initDatabaseConnectionPool()

        # Cycle time tracking
        # _opTimestamps holds {key: [start_time, end_time]} for each measured operation.
        # Keys are the OP_* constants defined in _resetOpTimestamps().
        self._opTimestamps: Dict[str, List[Optional[float]]] = {}
        self._cycleInProgress: bool = False
        self._resetOpTimestamps()

        # The other servers' status (from the heartbeat server) and what the IOServer does about them
        # (_applyServerStatuses): camera down -> picture NOT OK; DB down -> next cycle held; frontend or heartbeat
        # server down -> cycle aborted, next cycle held; QR code server -> nothing.
        self._serverStatus: Dict[str, bool] = {}
        self._statusLock = threading.Lock()
        self._lastHeartbeatServerMessage: float = time.monotonic()
        # The rules apply once the heartbeat server has been heard from: an IOServer run on its own (commissioning,
        # the IOServer test windows) has no heartbeat server and keeps working as before. In production the heartbeat
        # server starts with the others (and is restarted by MainProgram's supervisor if it dies).
        self._heartbeatServerSeen: bool = False
        self._heartbeatServerDown: bool = False
        self._frontendDown: bool = False
        self._databaseDown: bool = False
        self._cameraDown: bool = False
        self._newCycleHold: Optional[str] = None      # why a new cycle may not start now (None = it may)
        self._heldReasonLogged: Optional[str] = None
        self._noticeRedisConnection = None            # frontend notices (setFrontendNotice), short timeouts
        # The picture request the write loop waits for: {"id", "pictureState", "writeState", "sentAt"}
        self._cameraRequest: Optional[Dict[str, Any]] = None
        self._cameraRequestSeq: int = 0
        self._writeStateSince: float = time.monotonic()
        self.cameraTimeouts: int = 0
        # The cycle settings last written to the PLC ({setting key: value}): the settings the PLC holds when a later
        # cycle's settings cannot be written (the cycle then goes ahead with them)
        self._lastWrittenSettings: Dict[str, float] = {}

        # Current QR Code for this cycle
        self.currentQRCode: str = ""
        self.lastStation2RotationDuringTighteningDone: Optional[bool] = None

        # The IOServer's messages go to the log file
        self.logger = _FileLogAdapter(PLCIOServer.logSource)

        # Start PLC connection and threads
        self.connectionInProgress: bool = False
        self.connectionAttempts: int = 0
        self.connectPLC()

        time.sleep(2)
        if not self._readsReady():
            self._plcCheckPending = True          # checked (and reset) as soon as the PLC is connected
        self.resetForNextCycle("IOServer start")  # every per-cycle tag to its default, then CheckQRCode = True

        # An IOServer started again (after a crash; MainProgram's supervisor restarts it) may find the frontend and the
        # camera server in the middle of a cycle the PLC side no longer has: both are told the cycle was aborted. A
        # restart is told from the first start by a Redis key the first start leaves (MainProgram clears Redis before
        # it starts the servers), so a fresh start leaves no stale abort for a frontend that starts after it.
        try:
            r = Redis(self.redisHostname, self.redisPort, socket_timeout=1.0, socket_connect_timeout=1.0)
            if not r.set("ioserver:started", str(time.time()), nx=True):
                printBoldRed("IOServer started again: the frontend and the camera server are told the cycle was aborted")
                self._audit("IOServer restarted: abort sent to the frontend and the camera server")
                sendEmergencyAbortFromIOServerToFEServer(r)
                sendEndOfCycleFromIOServerToCameraServer(r, aborted=True)
            setActiveCameraRequest(r, None)
        except Exception:
            noteSuppressed()
        # State sync: the state at start (the frontend learns this IOServer's pid and where it is)
        seq, published = self._publishState(None, self.machineState.getCurrentState(), reason="IOServer start")
        self._audit(f"STATE at start {self.machineState.getCurrentState().name}  (seq {seq}, "
                    f"{'published' if published else 'NOT published'})")

        time.sleep(3)
        # no cycle yet: clear the log context left by a previous run (until the first QR code is accepted)
        try:
            publishCycleContext(Redis(self.redisHostname, self.redisPort), None, None)
        except Exception:
            noteSuppressed()
        self.startThreads()

    def connectToRedis(self, forceRenew=False):
        if forceRenew:
            self.redisConnectionForReads = None
            self.clientRedisConnectedForReads = False
            self.redisConnectionForWrites = None
            self.clientRedisConnectedForWrites = False

        if not self.clientRedisConnectedForReads:
            try:
                self.redisConnectionForReads = Redis(self.redisHostname, self.redisPort,
                                                     retry_on_timeout=True)
                self.clientRedisConnectedForReads = True
            except Exception:
                self.clientRedisConnectedForReads = False
                SlaveConsoleLogger.getInstance().logTakeAction(PLCIOServer.logSource,
                                                               'Could not get Redis Connection for Reads',
                                                               Logger.PROBLEM)
                SlaveFileLogger.getInstance().logTakeAction(PLCIOServer.logSource,
                                                            'Could not get Redis Connection for Reads', Logger.PROBLEM)

        if not self.clientRedisConnectedForWrites:
            try:
                self.redisConnectionForWrites = Redis(self.redisHostname, self.redisPort,
                                                      retry_on_timeout=True)
                self.clientRedisConnectedForWrites = True
            except Exception:
                self.clientRedisConnectedForWrites = False
                SlaveConsoleLogger.getInstance().logTakeAction(PLCIOServer.logSource,
                                                               'Could not get Redis Connection for Writes',
                                                               Logger.PROBLEM)
                SlaveFileLogger.getInstance().logTakeAction(PLCIOServer.logSource,
                                                            'Could not get Redis Connection for Writes',
                                                            Logger.PROBLEM)

    def connectToRedisForHeartbeatAndEmergencyMonitoring(self, forceRenew=False):
        if forceRenew:
            self.heartbeatRedisConnection = None
            self.heartbeatRedisConnected = False
            self.combinedHeartbeatRedisConnection = None
            self.combinedHeartbeatRedisConnected = False
            self.emergencyRedisConnection = None
            self.emergencyRedisConnected = False

        if not self.heartbeatRedisConnected:
            try:
                self.heartbeatRedisConnection = Redis(self.redisHostname, self.redisPort, retry_on_timeout=True)
                self.heartbeatRedisConnected = True
                SlaveConsoleLogger.getInstance().logDebug(PLCIOServer.logSource,
                                                          f'Heartbeat Redis Connection is {self.heartbeatRedisConnection}',
                                                          Logger.SUCCESS)
                SlaveFileLogger.getInstance().logDebug(PLCIOServer.logSource,
                                                       f'Heartbeat Redis Connection is {self.heartbeatRedisConnection}',
                                                       Logger.SUCCESS)
            except Exception:
                self.heartbeatRedisConnected = False
                SlaveConsoleLogger.getInstance().logTakeAction(PLCIOServer.logSource,
                                                               'Could not get Heartbeat Redis Connection',
                                                               Logger.PROBLEM)
                SlaveFileLogger.getInstance().logTakeAction(PLCIOServer.logSource,
                                                            'Could not get Heartbeat Redis Connection', Logger.PROBLEM)

        if not self.combinedHeartbeatRedisConnected:
            try:
                self.combinedHeartbeatRedisConnection = Redis(self.redisHostname, self.redisPort, retry_on_timeout=True)
                self.combinedHeartbeatRedisConnected = True
                SlaveConsoleLogger.getInstance().logDebug(PLCIOServer.logSource,
                                                          f'Combined Heartbeat Redis Connection is {self.combinedHeartbeatRedisConnection}',
                                                          Logger.SUCCESS)
                SlaveFileLogger.getInstance().logDebug(PLCIOServer.logSource,
                                                       f'Combined Heartbeat Redis Connection is {self.combinedHeartbeatRedisConnection}',
                                                       Logger.SUCCESS)
            except Exception:
                self.combinedHeartbeatRedisConnected = False
                SlaveConsoleLogger.getInstance().logTakeAction(PLCIOServer.logSource,
                                                               'Could not get Heartbeat Redis Connection',
                                                               Logger.PROBLEM)
                SlaveFileLogger.getInstance().logTakeAction(PLCIOServer.logSource,
                                                            'Could not get Heartbeat Redis Connection', Logger.PROBLEM)

        if not self.emergencyRedisConnected:
            try:
                self.emergencyRedisConnection = Redis(self.redisHostname, self.redisPort, retry_on_timeout=True)
                self.emergencyRedisConnected = True
                SlaveConsoleLogger.getInstance().logDebug(PLCIOServer.logSource,
                                                          f'Emergency Redis Connection is {self.emergencyRedisConnection}',
                                                          Logger.SUCCESS)
                SlaveFileLogger.getInstance().logDebug(PLCIOServer.logSource,
                                                       f'Emergency Redis Connection is {self.emergencyRedisConnection}',
                                                       Logger.SUCCESS)
            except Exception:
                self.emergencyRedisConnected = False
                SlaveConsoleLogger.getInstance().logTakeAction(PLCIOServer.logSource,
                                                               'Could not get Emergency Redis Connection',
                                                               Logger.PROBLEM)
                SlaveFileLogger.getInstance().logTakeAction(PLCIOServer.logSource,
                                                            'Could not get Emergency Redis Connection', Logger.PROBLEM)

    def _initDatabaseConnectionPool(self):
        """Initialize a connection pool for database reads."""
        try:
            self.dbConnectionPool = ThreadedConnectionPool(
                minconn=1,
                maxconn=3,
                user="postgres",
                password="postgres",
                host="127.0.0.1",
                port="5432",
                database=getDatabaseName()
            )
            printBoldGreen(f"Database connection pool initialized for {getDatabaseName()}")
        except Exception as e:
            printBoldRed(f"Failed to initialize database connection pool: {e}")
            self.dbConnectionPool = None

    def writeCycleSettingsToPLC(self, qrCode: str) -> bool:
        """
        State 2 (WRITE_QR_CODE): write the cycle settings to the PLC. Only tags with a configured name are written
        (an empty tag name means the PLC does not have that setting).
          LH_RH_Selection             INT   1 = LHS, 2 = RHS              (from the QR code)
          ModelSelection              INT   1 = DOST, 2 = DOSTPLUS        (from the QR code; only if its tag is set)
        cycle.settings.source = database (both PLCs): the settings in force in machine_settings for the QR code's model
        (frontend: Machine Settings, per model), all REAL:
          TighteningTorque1/2Setpoint, RotationRPM, NoOfRotation1CCW, NoOfRotation1CW
        cycle.settings.source = properties (the earlier Allen-Bradley set-up, not used now):
          TighteningTorque1/2Setpoint REAL, RotationRPM1/2 INT          (properties; -1 = do not write)
        Each write is retried (SETTINGS_WRITE_ATTEMPTS). The side must be written: an unknown side / model or a side
        that cannot be written returns False (QR code NOT OK). The other settings rarely change and the PLC starts
        with acceptable values: one that cannot be written (or no machine settings from the database) is left as the
        PLC holds it, the failure is logged and shown on the frontend, and the cycle goes ahead (True).
        The caller writes QRCodeCheckOK / Done only AFTER this returns (OK = False if it returned False), so the PLC
        never sees Done before the settings are in place.
        The settings in force are published for the DB server (stored with the component's record): those written
        now, else the ones last written (None if not known).
        Returns True if the side (and, on the Allen-Bradley properties set-up, the model) was written.
        Bypass (Machine Setting "Bypass Component Press and Second Tightening", per model): PC_PLC_Skip_Second_Tightening
        written (True / False) after the side - see _writeSkipSecondTightening.
        """
        self._skipSecondTighteningThisCycle = False      # full run unless True is written below
        if not qrCode:
            printBoldRed("Cannot write cycle settings: QR code is empty")
            return False
        try:
            modelName, lhs_rhs, tonnage = getModel_LHSRHS_AndTonnage(qrCode)
            side = (lhs_rhs or "").strip().upper()
            model = (modelName or "").strip().upper()
            if side == "LHS":
                sideCode = PLCIOServer.LHS_CODE
            elif side == "RHS":
                sideCode = PLCIOServer.RHS_CODE
            else:
                printBoldRed(f"Cannot determine LHS/RHS from QR code: {qrCode}")
                return False
            writes = [(PLCIOServer.PC_PLC_LH_RH_Selection, sideCode, int)]
            if PLCIOServer.PC_PLC_ModelSelection:
                modelCode = PLCIOServer.MODEL_CODES.get(model)
                if modelCode is None:
                    printBoldRed(f"Unknown model '{modelName}' from QR code: {qrCode}")
                    return False
                writes.append((PLCIOServer.PC_PLC_ModelSelection, modelCode, int))

            config = CosThetaConfigurator.getInstance()
            used = {"rotation_rpm": None, "tightening_torque1_setpoint": None, "tightening_torque2_setpoint": None,
                    "no_of_rotation1_ccw": None, "no_of_rotation1_cw": None}
            keyOfTag = {}                   # settings tag -> its key in `used`
            problems = []                   # settings not written this cycle (the cycle goes ahead with the PLC's)
            bypassWanted = False            # the model's "Bypass Component Press and Second Tightening"
            if config.getCycleSettingsSource() == "database":
                settings = None
                try:
                    settings = getMachineSettings(getDatabaseName(), model_name=model)      # this model's settings
                except Exception as e:
                    problems.append(f"machine settings could not be read from the database ({e})")
                bypassWanted = bool(settings.get("bypass_press_and_tightening2")) if settings else False
                missing = [k for k in used if settings.get(k) is None] if settings else list(used)
                if missing and not problems:
                    problems.append(f"machine settings not available from the database ({', '.join(missing)})")
                if not missing:
                    for tag, key in ((PLCIOServer.PC_PLC_TighteningTorque1Setpoint, "tightening_torque1_setpoint"),
                                     (PLCIOServer.PC_PLC_TighteningTorque2Setpoint, "tightening_torque2_setpoint"),
                                     (PLCIOServer.PC_PLC_RotationRPM, "rotation_rpm"),
                                     (PLCIOServer.PC_PLC_NoOfRotation1CCW, "no_of_rotation1_ccw"),
                                     (PLCIOServer.PC_PLC_NoOfRotation1CW, "no_of_rotation1_cw")):
                        if tag:
                            writes.append((tag, float(settings[key]), float))
                            keyOfTag[tag] = key
            else:
                skip = CosThetaConfigurator.SETPOINT_NOT_TO_BE_WRITTEN
                for tag, value, key in ((PLCIOServer.PC_PLC_TighteningTorque1Setpoint, config.getTighteningTorque1Setpoint(), "tightening_torque1_setpoint"),
                                        (PLCIOServer.PC_PLC_TighteningTorque2Setpoint, config.getTighteningTorque2Setpoint(), "tightening_torque2_setpoint")):
                    if tag and value != skip:
                        writes.append((tag, float(value), float))
                        keyOfTag[tag] = key
                for tag, value in ((PLCIOServer.PC_PLC_RotationRPM1, config.getRotation1RPM()),
                                   (PLCIOServer.PC_PLC_RotationRPM2, config.getRotation2RPM())):
                    if tag and value != skip:
                        writes.append((tag, int(value), int))

            # The side must be written (it changes with every part): if it cannot be, the QR code is NOT OK. Any
            # other setting that cannot be written (3 attempts) is left as the PLC holds it - the settings rarely
            # change, and the PLC starts with acceptable values - and the cycle goes ahead (logged, shown).
            sideWritten = False
            for tag, value, kind in writes:
                writer = (lambda t, v: self.write_int(t, int(v))) if kind is int else \
                         (lambda t, v: self.write_float(t, float(v), debug=False))
                ok_ = False
                for attempt in range(PLCIOServer.SETTINGS_WRITE_ATTEMPTS):
                    # written and read back (as the resets are): a value the PLC does not hold as written (wrong
                    # width or address - section 22's LREAL case - or refused) counts as not written
                    if writer(tag, value) and self._settingReadBack(tag, value, kind):
                        ok_ = True
                        break
                    time.sleep(PLCIOServer.WRITE_RETRY_INTERVAL)
                key = keyOfTag.get(tag)
                if tag == PLCIOServer.PC_PLC_LH_RH_Selection:
                    sideWritten = ok_
                    if not ok_:
                        printBoldRed(f"Could not write the side {tag}={value} after "
                                     f"{PLCIOServer.SETTINGS_WRITE_ATTEMPTS} attempts: QR code NOT OK")
                        break
                elif ok_:
                    if key:
                        used[key] = float(value)
                        self._lastWrittenSettings[key] = float(value)
                else:
                    problems.append(f"{tag}={value} not written after {PLCIOServer.SETTINGS_WRITE_ATTEMPTS} attempts")
            for key in used:                # not written this cycle: the PLC holds the value last written, if known
                if used[key] is None and key in self._lastWrittenSettings:
                    used[key] = self._lastWrittenSettings[key]
            written = ", ".join(f"{tag}={value}" for tag, value, kind in writes)
            # the side written this cycle: the fallback reference for PLC restart detection (_verifyPlcAfterReconnect)
            self._sideWrittenThisCycle = sideCode if sideWritten else None
            if not sideWritten:
                printBoldRed(f"Failed to write the cycle settings to the PLC ({written})")
                return False
            if not self._writeSkipSecondTightening(bypassWanted, model):
                return False                # the PLC's sequence is not known: QR code NOT OK
            if problems:
                message = (f"Cycle settings NOT updated for {qrCode}: {'; '.join(problems)}. The cycle goes ahead with "
                           f"the settings the PLC holds")
                printBoldRed(message); self.logger.error(message); self._audit(message)
                self._notice("settings", "Machine settings not updated: previous settings used", ttlSecs=30)
            try:
                # bypass_press_and_tightening2 only when this cycle skips states 13-19 (the frontend marks those
                # tiles "Bypassed"); absent = full run
                publishCycleSettingsForQRCode(self.redisConnectionForWrites, qrCode,
                                              {**used, "bypass_press_and_tightening2": True}
                                              if self._skipSecondTighteningThisCycle else used)
            except Exception as e:
                printBoldRed(f"Could not publish the cycle settings for the DB record: {e}")
            printBoldGreen(f"Wrote cycle settings to PLC for {model} {side}: {written}")
            return True
        except Exception as e:
            printBoldRed(f"Exception writing cycle settings to PLC: {e}")
            return False

    def _writeSkipSecondTightening(self, wanted: bool, model: str) -> bool:
        """State 2: PC_PLC_Skip_Second_Tightening = wanted (written and read back, SETTINGS_WRITE_ATTEMPTS). The cycle
        skips states 13-19 only if True was written (_skipSecondTighteningThisCycle). No tag name, no address / not on
        the PLC: full run whatever the setting (logged when the setting asked for the bypass) - the PLC cannot skip
        without the tag. The tag exists but the write fails (True or False): False - the QR code is answered NOT OK,
        as the PLC may still hold the previous component's value and skip while the PC runs the full sequence."""
        tag = PLCIOServer.PC_PLC_Skip_Second_Tightening
        self._skipSecondTighteningThisCycle = False
        if not tag or not self._optionalTagWritable(tag):
            if wanted:
                message = (f"{model}: bypass of the component press and second tightening is set, but the PLC has no "
                           f"{tag or 'skip tag (pc.plc.skip.second.tightening.tagname)'}: full run")
                printBoldYellow(message); self.logger.warning(message); self._audit(message)
                self._notice("settings", f"Bypass not possible ({tag} not on the PLC): full run", ttlSecs=30)
            return True
        if self._writeVerified(tag, bool(wanted), attempts=PLCIOServer.SETTINGS_WRITE_ATTEMPTS):
            self._skipSecondTighteningThisCycle = bool(wanted)
            if wanted:
                message = f"{model}: component press and second tightening bypassed ({tag} = True)"
                printBoldYellow(message); self.logger.warning(message); self._audit(message)
            return True
        message = (f"Could not write {tag} = {bool(wanted)} ({PLCIOServer.SETTINGS_WRITE_ATTEMPTS} attempts): QR code "
                   f"NOT OK (the PLC's sequence for this component is not known)")
        printBoldRed(message); self.logger.error(message); self._audit(message)
        return False

    def _settingReadBack(self, tag: str, value, kind) -> bool:
        """A cycle setting just written: read it back from the PLC; True if it holds the value written (INT exactly,
        REAL / LREAL within RESET_FLOAT_TOLERANCE). Logged when it does not."""
        held = self._readIntOrNone(tag) if kind is int else self._readFloatOrNone(tag)
        if held is not None and (held == int(value) if kind is int
                                 else abs(held - float(value)) <= PLCIOServer.RESET_FLOAT_TOLERANCE):
            return True
        message = f"Cycle setting {tag}: wrote {value}, PLC holds {held}"
        printBoldRed(message); self.logger.error(message); self._audit(message)
        return False

    # ==================== Cycle Time Tracking ====================
    #
    # Strategy: record wall-clock timestamps at the precise moments that bound
    # pure machine/camera work, ignoring all manual-operator wait time.
    #
    # Each operation has a START (PLC trigger received → camera command sent)
    # and an END (Done tag written back to PLC, or PLC signals completion).
    #
    # _opTimestamps  : Dict[str, List[Optional[float]]]
    #   key  → OP_* constant string
    #   value → [start_time, end_time]   (None until recorded)

    # ── Operation key constants ────────────────────────────────────────────
    OP_KNUCKLE        = "T1_Knuckle"
    OP_HUB_BEARING    = "T2_HubAndBottomBearing"
    OP_TOP_BEARING    = "T3_TopBearing"
    OP_NUT_WASHER     = "T4_NutAndPlateWasher_to_Torque1Read"
    OP_NOCAP_BUNK     = "T5_NoCapBunk"
    OP_TORQUE2        = "T6_NoCapBunkStart_to_Torque2Done"
    OP_SPLITPIN       = "T7_SplitPinAndWasher"
    OP_CAP            = "T8_Cap"
    OP_BUNK_CAP_PRESS = "T9_BunkCapPress_to_Station3TorqueValueSet"

    ALL_OP_KEYS = [
        OP_KNUCKLE, OP_HUB_BEARING, OP_TOP_BEARING, OP_NUT_WASHER,
        OP_NOCAP_BUNK, OP_TORQUE2, OP_SPLITPIN, OP_CAP, OP_BUNK_CAP_PRESS,
    ]

    def _resetOpTimestamps(self) -> None:
        """Initialise / clear all operation timestamps to [None, None]."""
        self._opTimestamps = {key: [None, None] for key in PLCIOServer.ALL_OP_KEYS}
        self._cycleInProgress = False

    def _recordOpStart(self, key: str) -> None:
        """Record the start wall-clock time for the given operation key."""
        try:
            self._opTimestamps[key][0] = time.time()
            self._cycleInProgress = True
        except Exception as e:
            printBoldRed(f"_recordOpStart({key}) error: {e}")

    def _recordOpEnd(self, key: str) -> None:
        """Record the end wall-clock time for the given operation key."""
        try:
            self._opTimestamps[key][1] = time.time()
        except Exception as e:
            printBoldRed(f"_recordOpEnd({key}) error: {e}")

    def _computeAndLogCycleTimes(self) -> None:
        """
        Compute delta times for all 9 operations, sum them, add 8 sec fixed
        overhead, then log the result compulsorily at ERROR/PROBLEM level so
        it always appears in both console and file logs.
        """
        ts = self._opTimestamps

        def getDelta(key: str) -> float:
            """Return end-start for key, or 0.0 if either timestamp is missing."""
            pair = ts.get(key, [None, None])
            if pair[0] is not None and pair[1] is not None:
                delta = pair[1] - pair[0]
                return max(delta, 0.0)   # guard against clock skew
            return 0.0

        T1 = getDelta(PLCIOServer.OP_KNUCKLE)
        T2 = getDelta(PLCIOServer.OP_HUB_BEARING)
        T3 = getDelta(PLCIOServer.OP_TOP_BEARING)
        T4 = getDelta(PLCIOServer.OP_NUT_WASHER)
        T5 = getDelta(PLCIOServer.OP_NOCAP_BUNK)
        T6 = getDelta(PLCIOServer.OP_TORQUE2)
        T7 = getDelta(PLCIOServer.OP_SPLITPIN)
        T8 = getDelta(PLCIOServer.OP_CAP)
        T9 = getDelta(PLCIOServer.OP_BUNK_CAP_PRESS)

        sumOfDeltas   = T1 + T2 + T3 + T4 + T5 + T6 + T7 + T8 + T9
        fixedOverhead = 8   # seconds — fixed machine overhead per cycle
        totalMachineTime = sumOfDeltas + fixedOverhead

        cycleTimeMsg = (
            f"\n{'=' * 70}\n"
            f"MACHINE CYCLE TIME BREAKDOWN (pure machine/camera time only)\n"
            f"{'=' * 70}\n"
            f"T1  (Knuckle check           : PLC trigger → KnuckleCheckDone)          : {T1:.3f} sec\n"
            f"T2  (Hub & bottom bearing    : PLC trigger → HubCheckDone)               : {T2:.3f} sec\n"
            f"T3  (Top bearing             : PLC trigger → HubAndSecondBearingDone)    : {T3:.3f} sec\n"
            f"T4  (Nut & plate washer      : PLC trigger → Torque 1 read)              : {T4:.3f} sec\n"
            f"T5  (No-cap bunk             : PLC trigger → NoCapBunkCheckDone)         : {T5:.3f} sec\n"
            f"T6  (No-cap bunk → Torque2   : PLC trigger → Station2TorqueValueSet)     : {T6:.3f} sec\n"
            f"T7  (Split pin & washer      : PLC trigger → SplitPinAndWasherDone)      : {T7:.3f} sec\n"
            f"T8  (Cap                     : PLC trigger → CapCheckDone)               : {T8:.3f} sec\n"
            f"T9  (Bunk for cap press      : PLC trigger → Station3TorqueValueSet ready): {T9:.3f} sec\n"
            f"{'-' * 70}\n"
            f"Sum of T1 – T9                                                           : {sumOfDeltas:.3f} sec\n"
            f"Fixed overhead                                                           : +{fixedOverhead} sec\n"
            f"{'=' * 70}\n"
            f"TOTAL MACHINE TIME                                                       : {totalMachineTime:.3f} sec\n"
            f"{'=' * 70}\n"
        )

        # Always print to terminal
        printBoldYellow(cycleTimeMsg)

        # Log at PROBLEM level → guaranteed to appear in both console and file logs
        SlaveConsoleLogger.getInstance().logTakeAction(PLCIOServer.logSource, cycleTimeMsg, Logger.PROBLEM)
        SlaveFileLogger.getInstance().logTakeAction(PLCIOServer.logSource, cycleTimeMsg, Logger.PROBLEM)

    def getCurrentTagsToBeRead(self) -> Tuple[list[str] | None, list[Type[bool | float] | None] | None]:
        """
        Get the PLC tags to be read based on current machine state.

        Machine 2 state mapping (28 states; M1's READ_FREE_ROTATIONS_DONE removed):
        - States 1-2:   QR Code
        - States 3-4:   Knuckle
        - States 5-6:   Hub (with bottom bearing) - uses PLC_PC_CheckHub
        - States 7-8:   Top bearing - uses PLC_PC_CheckHubAndSecondBearing
        - States 9-10:  Nut and Plate Washer
        - States 11-12: Tightening Torque 1 - PLC_PC_Station2TorqueValueSet / PLC_PC_Station2TorqueValue
        - States 13-14: Bunk for Component Press - uses PLC_PC_CheckNoCapBunk
        - State 15:     Component Press Done
        - States 16-17: No Bunk
        - States 18-19: Tightening Torque 2 - Station 2 torque tags (reused)
        - States 20-21: Split Pin and Washer
        - States 22-23: Cap
        - States 24-25: Bunk for Cap Press - uses PLC_PC_CheckBunk
        - State 26:     Cap Press Done
        - States 27-28: Free Rotation Torque - PLC_PC_Station3TorqueValueSet / PLC_PC_Station3TorqueValue
        """
        currentState = self.machineState.getCurrentState()

        # Step 0: QR Code
        if currentState == MachineState.READ_QR_CODE:
            return [PLCIOServer.PLC_PC_CheckQRCode], [bool]

        # Step 1: Knuckle
        if currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE:
            return [PLCIOServer.PLC_PC_CheckKnuckle], [bool]

        # Step 2: Hub (maps to HUB_AND_BOTTOM_BEARING state)
        if currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING:
            return [PLCIOServer.PLC_PC_CheckHub], [bool]

        # Step 3: Hub and Second Bearing (maps to TOP_BEARING state)
        if currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING:
            return [PLCIOServer.PLC_PC_CheckHubAndSecondBearing], [bool]

        # Step 4: Nut and Plate Washer
        if currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER:
            return [PLCIOServer.PLC_PC_CheckNutAndPlateWasher], [bool]

        # Step 5: Tightening Torque 1 (Station 2)
        if currentState == MachineState.READ_TIGHTENING_TORQUE_1_DONE:
            return [PLCIOServer.PLC_PC_Station2TorqueValueSet], [bool]
        if currentState == MachineState.READ_TIGHTENING_TORQUE_1:
            return [PLCIOServer.PLC_PC_Station2TorqueValue], [float]

        # Step 7: No Cap Bunk for Component Press
        if currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS:
            return [PLCIOServer.PLC_PC_CheckNoCapBunk], [bool]

        # Step 8: Component Press Done
        if currentState == MachineState.READ_COMPONENT_PRESS_DONE:
            return [PLCIOServer.PLC_PC_ComponentPressDone], [bool]

        # Step 9: No Bunk
        if currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK:
            return [PLCIOServer.PLC_PC_CheckNoBunk], [bool]

        # Step 10: Tightening Torque 2 (Station 2 - second round, reuses same tags)
        if currentState == MachineState.READ_TIGHTENING_TORQUE_2_DONE:
            return [PLCIOServer.PLC_PC_Station2TorqueValueSet], [bool]
        if currentState == MachineState.READ_TIGHTENING_TORQUE_2:
            return [PLCIOServer.PLC_PC_Station2TorqueValue], [float]

        # Step 11: Split Pin and Washer
        if currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER:
            return [PLCIOServer.PLC_PC_CheckSplitPinAndWasher], [bool]

        # Step 12: Cap
        if currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP:
            return [PLCIOServer.PLC_PC_CheckCap], [bool]

        # Step 13: Bunk for Cap Press - reuses CheckBunk
        if currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS:
            return [PLCIOServer.PLC_PC_CheckBunk], [bool]

        # Step 14: Cap Press Done
        if currentState == MachineState.READ_CAP_PRESS_DONE:
            return [PLCIOServer.PLC_PC_CapPressDone], [bool]

        # Step 15: Free Rotation Torque (Station 3 - final reading)
        if currentState == MachineState.READ_FREE_ROTATION_TORQUE_1_DONE:
            return [PLCIOServer.PLC_PC_Station3TorqueValueSet], [bool]
        if currentState == MachineState.READ_FREE_ROTATION_TORQUE_1:
            return [PLCIOServer.PLC_PC_Station3TorqueValue], [float]

        return None, None

    def getCurrentTagsToBeWritten(self) -> list[str] | None:
        """
        Get the PLC tags to be written based on current machine state.
        NOTE the order of the tags: The OK tag is populated first, and then the Done tag is populated.
        This order ensures that when the PLC reads the Done tag, it ALWAYS has the CORRECT RESULT of ImageProcessing.
        NOTE: The 3rd tag is the trigger tag eg CheckKnuckle, CheckHub which is to be reset to False
        in the same operation, after the first 2 are populated.
        """
        currentState = self.machineState.getCurrentState()

        # Step 0: QR Code
        if currentState == MachineState.WRITE_QR_CODE:
            return [PLCIOServer.PC_PLC_QRCodeCheckOK, PLCIOServer.PC_PLC_QRCodeCheckDone, PLCIOServer.PLC_PC_CheckQRCode]

        # Step 1: Knuckle
        if currentState == MachineState.WRITE_RESULT_OF_CHECKING_KNUCKLE:
            return [PLCIOServer.PC_PLC_KnuckleCheckOK, PLCIOServer.PC_PLC_KnuckleCheckDone, PLCIOServer.PLC_PC_CheckKnuckle]

        # Step 2: Hub (maps to HUB_AND_BOTTOM_BEARING state)
        if currentState == MachineState.WRITE_RESULT_OF_CHECKING_HUB_AND_BOTTOM_BEARING:
            return [PLCIOServer.PC_PLC_HubCheckOK, PLCIOServer.PC_PLC_HubCheckDone, PLCIOServer.PLC_PC_CheckHub]

        # Step 3: Hub and Second Bearing (maps to TOP_BEARING state)
        if currentState == MachineState.WRITE_RESULT_OF_CHECKING_TOP_BEARING:
            return [PLCIOServer.PC_PLC_HubAndSecondBearingCheckOK, PLCIOServer.PC_PLC_HubAndSecondBearingCheckDone,
                    PLCIOServer.PLC_PC_CheckHubAndSecondBearing]

        # Step 4: Nut and Plate Washer
        if currentState == MachineState.WRITE_RESULT_OF_CHECKING_NUT_AND_PLATEWASHER:
            return [PLCIOServer.PC_PLC_NutAndPlateWasherCheckOK, PLCIOServer.PC_PLC_NutAndPlateWasherCheckDone,
                    PLCIOServer.PLC_PC_CheckNutAndPlateWasher]

        # Step 7: No Cap Bunk for Component Press
        if currentState == MachineState.WRITE_RESULT_OF_CHECKING_BUNK_FOR_COMPONENT_PRESS:
            return [PLCIOServer.PC_PLC_NoCapBunkCheckOK, PLCIOServer.PC_PLC_NoCapBunkCheckDone,
                    PLCIOServer.PLC_PC_CheckNoCapBunk]

        # Step 9: No Bunk
        if currentState == MachineState.WRITE_RESULT_OF_CHECKING_NO_BUNK:
            return [PLCIOServer.PC_PLC_NoBunkCheckOK, PLCIOServer.PC_PLC_NoBunkCheckDone, PLCIOServer.PLC_PC_CheckNoBunk]

        # Step 11: Split Pin and Washer
        if currentState == MachineState.WRITE_RESULT_OF_CHECKING_SPLITPIN_AND_WASHER:
            return [PLCIOServer.PC_PLC_SplitPinAndWasherCheckOK, PLCIOServer.PC_PLC_SplitPinAndWasherCheckDone,
                    PLCIOServer.PLC_PC_CheckSplitPinAndWasher]

        # Step 12: Cap
        if currentState == MachineState.WRITE_RESULT_OF_CHECKING_CAP:
            return [PLCIOServer.PC_PLC_CapCheckOK, PLCIOServer.PC_PLC_CapCheckDone, PLCIOServer.PLC_PC_CheckCap]

        # Step 13: Bunk for Cap Press - reuses CheckBunk
        if currentState == MachineState.WRITE_RESULT_OF_CHECKING_BUNK_FOR_CAP_PRESS:
            return [PLCIOServer.PC_PLC_BunkCheckOK, PLCIOServer.PC_PLC_BunkCheckDone, PLCIOServer.PLC_PC_CheckBunk]

        return None

    def connectPLC(self, forceRenew=False) -> bool | None:
        """Connect to the PLC: two PLCDataIO objects, one for reads and one for writes (createPLCDataIO: the configured
        vendor, or MockPLC when mockInstance). True if connected, False if not, None if a connection is already
        being made."""
        if self.connectionInProgress:
            return None
        self.connectionInProgress = True
        self.connectionAttempts += 1

        try:
            if forceRenew:
                self._closePLC()

            self.dataIOForReads = createPLCDataIO(self.plc_ip, "reads", self.vendor, mock=self.mockInstance)
            self.dataIOForReads.open()
            printBoldGreen(f"Connected to {self.dataIOForReads.describe()} for Reads")
            self.dataIOForWrites = createPLCDataIO(self.plc_ip, "writes", self.vendor, mock=self.mockInstance)
            self.dataIOForWrites.open()
            printBoldGreen(f"Connected to {self.dataIOForWrites.describe()} for Writes")

            self.connectionInProgress = False
            self.connectionAttempts = 0
            self._checkTagsOnPlc()
            return True
        except Exception as e:
            if (self.connectionAttempts == 1) or (
                    (self.connectionAttempts % PLCIOServer.LOG_CONNECTION_PROBLEM_EVERY_N_SECS) % 5) == 0:
                printBoldRed(f"PLC connection error: {e}")
            self._closePLC()
            self.connectionInProgress = False
            return False

    def _closePLC(self) -> None:
        """Close both PLC connections (errors ignored) and forget them."""
        for dataIO in (self.dataIOForReads, self.dataIOForWrites):
            if dataIO is not None:
                dataIO.close()
        self.dataIOForReads = None
        self.dataIOForWrites = None

    def _readsReady(self) -> bool:
        return self.dataIOForReads is not None and self.dataIOForReads.connected

    def _writesReady(self) -> bool:
        return self.dataIOForWrites is not None and self.dataIOForWrites.connected

    def _checkTagsOnPlc(self) -> None:
        """Once, after the first connection: warn about tags the PLC does not know (where the PLC can list its tags,
        i.e. Allen-Bradley; Siemens tags are checked against IOServerProperties.yaml before start)."""
        if self._tagsCheckedOnPlc or self.mockInstance:
            return
        self._tagsCheckedOnPlc = True
        try:
            missing = self.dataIOForReads.missingTags(PLCIOServer.tagNamesUsed().values())
        except Exception:
            noteSuppressed()
            return
        self._tagsMissingOnPlc = set(missing or ())
        optional = {getattr(PLCIOServer, a) for a in PLCIOServer.OPTIONAL_TAG_ATTRS}
        required = [t for t in (missing or ()) if t not in optional]
        if required:
            message = f"Tags not found on the PLC ({self.dataIOForReads.describe()}): {', '.join(required)}"
            printBoldRed(message)
            self.logger.error(message)
        for tag in (missing or ()):
            if tag in optional:
                self._optionalTagNotice(tag, "not on the PLC")

    def _optionalTagNotice(self, tag: str, why: str) -> None:
        """Log once that an optional tag cannot be written (no address / not on the PLC)."""
        if tag in self._optionalTagNoticeGiven:
            return
        self._optionalTagNoticeGiven.add(tag)
        message = f"Optional tag {tag}: {why}; it is not written (writes skipped, logged)"
        printBoldYellow(message)
        self.logger.warning(message)

    def _optionalTagWritable(self, tag: str) -> bool:
        """False if an optional tag is not named, or the PLC has no address for it (Siemens: none in
        IOServerProperties.yaml; Allen-Bradley: not in the controller's tag list). Unknown -> True (the write is then
        tried and a failure logged)."""
        if not tag:
            return False
        dio = self.dataIOForWrites
        # only a PLCDataIO whose tags have addresses of their own (Siemens) answers addressFor; the base class answers
        # None for every tag (Allen-Bradley, mock: the name is the address)
        if dio is not None and type(dio).addressFor is not PLCDataIO.addressFor:
            try:
                if dio.addressFor(tag) is None:
                    self._optionalTagNotice(tag, "no address in costhetaio/siemens/IOServerProperties.yaml")
                    return False
            except Exception:
                noteSuppressed()
        if tag in self._tagsMissingOnPlc:
            return False
        return True

    def handleFrontendAbortRequest(self, reason: str) -> bool:
        """The frontend's own abort (password-protected, 2026-11-08): try to set PC_PLC_CycleAborted True on the PLC
        (FRONTEND_ABORT_WRITE_ATTEMPTS attempts, each written and read back). Only if that succeeds is the cycle aborted
        (handshake tags reset, QR code server told, camera / QR queues cleared). Not named, no address, PLC not
        connected or the write fails: logged, nothing else done - the IOServer, the camera server and the machine
        carry on. Returns True if the cycle was aborted."""
        tag = PLCIOServer.PC_PLC_CycleAborted
        prefix = f"Frontend abort ({reason or 'no reason given'})"
        why = None
        if not tag:
            why = "no tag name (pc.plc.cycle.aborted.tagname is empty)"
        elif not self._optionalTagWritable(tag):
            why = f"no address for {tag} on this PLC"
        elif not self._plcConnected():
            why = "the PLC is not connected"
        elif not self._writeVerified(tag, True, attempts=PLCIOServer.FRONTEND_ABORT_WRITE_ATTEMPTS):
            why = f"{tag} = True could not be written ({PLCIOServer.FRONTEND_ABORT_WRITE_ATTEMPTS} attempts)"
        if why:
            message = f"{prefix}: {why}; the IOServer carries on with its cycle (the abort stays in the frontend)"
            printBoldYellow(message)
            self.logger.warning(message)
            self._audit(message)
            return False
        message = f"{prefix}: {tag} = True written; aborting the cycle"
        printBoldRed(message)
        self.logger.error(message)
        self._audit(message)
        self.abortCycle(f"abort from the frontend ({reason or 'no reason given'})",
                        notifyQRCodeServer=True, notifyFrontend=False, clearQueues=True, pcAbort=True)
        return True

    @classmethod
    def requiredTagNames(cls) -> Dict[str, str]:
        """tagNamesUsed() without the optional tags (OPTIONAL_TAG_ATTRS)."""
        return {a: n for a, n in cls.tagNamesUsed().items() if a not in cls.OPTIONAL_TAG_ATTRS}

    @classmethod
    def tagNamesUsed(cls) -> Dict[str, str]:
        """{attribute: tag name} for every PLC tag the IOServer reads or writes (empty names = not on this PLC, left
        out)."""
        return {a: getattr(cls, a) for a in vars(cls)
                if a[:6] in ("PLC_PC", "PC_PLC") and isinstance(getattr(cls, a), str) and getattr(cls, a)}

    def launchConnectionMonitoringThread(self):
        objRef = self

        def connectionCheckingLoop():
            """Loop to check PLC connection status."""
            while not objRef.shutdown:
                time.sleep(objRef.CONNECTION_CHECK_INTERVAL)

                tryToConnect: bool = False
                if not objRef._plcConnected():
                    tryToConnect = True
                else:
                    try:
                        readValue: int = 0
                        readValue = self.read_int(PLCIOServer.PLC_PC_WatchTag)
                        if readValue != PLCIOServer.WATCH_TAG_VALUE:
                            raise Exception(f"Could not read tag {PLCIOServer.PLC_PC_WatchTag} from PLC")
                    except Exception:
                        if (objRef.connectionAttempts == 1) or (
                                (objRef.connectionAttempts % PLCIOServer.LOG_CONNECTION_PROBLEM_EVERY_N_SECS) % 5) == 0:
                            objRef.logger.fatal("PLC connection test failed")
                            printBoldRed("PLC connection test failed. Reconnecting...")
                        tryToConnect = True

                try:
                    objRef._connectionCheckStep(tryToConnect)
                except Exception as e:              # the connection check must never stop
                    import traceback
                    message = f"Connection check: {e}"
                    printBoldRed(message); objRef.logger.error(f"{message}\n{traceback.format_exc()}")

        self.connectionCheckThread = threading.Thread(name='IOServer Connection Keep Alive Thread',
                                                      target=connectionCheckingLoop,
                                                      args=(), daemon=True)
        self.connectionCheckThread.start()

    def _connectionCheckStep(self, tryToConnect: bool) -> None:
        """One step of the connection check: reconnect (and check the PLC after the reconnect), or follow the
        reference tag."""
        objRef = self
        if tryToConnect:
            if not objRef._plcCheckPending:
                objRef._plcCheckPending = True
                message = (f"PLC connection lost in state {objRef.machineState.getCurrentState().name}; "
                           f"reference tag {objRef.referenceTag()}. Writes and reads on hold until the PLC is "
                           f"checked after the reconnect")
                printBoldRed(message)
                objRef.logger.error(message)
                objRef._audit(message)
            if objRef.connectPLC(forceRenew=True) is True:
                objRef._verifyPlcAfterReconnect()
        elif objRef._plcCheckPending:
            objRef._verifyPlcAfterReconnect()
        else:
            objRef._followReferenceTag()

    def launchHeartbeatThread(self):
        objRef = self

        def reportHeartbeat():
            ioHeartbeatGap = CosThetaConfigurator.getInstance().getIOConnectionStatusSleepInterval()
            while not objRef.shutdown:
                try:
                    if not objRef._plcConnected():
                        sendHeartbeatFromIOServerToHeartbeatServer(redisConnection=objRef.heartbeatRedisConnection,
                                                                   status=DEAD)
                    elif objRef.workers.problemsLogged(log=lambda m: (printBoldRed(m), objRef.logger.error(m))):
                        # the read or write loop died or is stuck: the IOServer is not answering the PLC
                        sendHeartbeatFromIOServerToHeartbeatServer(redisConnection=objRef.heartbeatRedisConnection,
                                                                   status=DEAD)
                    else:
                        sendHeartbeatFromIOServerToHeartbeatServer(redisConnection=objRef.heartbeatRedisConnection,
                                                                   status=ALIVE)
                except Exception as e:
                    printBoldRed(f"Exception {e} encountered in heartbeat")
                    objRef.connectToRedisForHeartbeatAndEmergencyMonitoring(forceRenew=True)
                try:
                    time.sleep(ioHeartbeatGap)
                except Exception:
                    noteSuppressed()

        self.heartbeatThread = threading.Thread(name="IO Heartbeat Thread", target=reportHeartbeat, args=(),
                                                daemon=True)
        self.heartbeatThread.start()

    def launchThreadForUpdatingCombinedHeartbeat(self):
        """The other servers' status, from the heartbeat server (one message every
        combinedheartbeat.connectionstatus.sleepinterval.sec with each server's ALIVE / DEAD), and the rules for a
        server that is down (_applyServerStatuses). PC_PLC_ConnectionStatus (the PC is alive) is toggled by a thread
        of its own (started here), whatever the other servers' status."""
        objRef = self

        def updateHeartbeat():
            heartbeatGap = CosThetaConfigurator.getInstance().getCombinedConnectionStatusUpdateInterval()
            while not objRef.shutdown:
                statuses = None
                try:
                    statuses = readServerStatusesInIOServerFromHeartbeatServer(
                        objRef.combinedHeartbeatRedisConnection, block=int(heartbeatGap * 1000))
                    if statuses is not None:
                        with objRef._statusLock:
                            objRef._serverStatus = statuses
                            objRef._lastHeartbeatServerMessage = time.monotonic()
                            if not objRef._heartbeatServerSeen:
                                objRef._heartbeatServerSeen = True
                                objRef._audit("First status message from the heartbeat server: the rules for servers "
                                              "that are down apply from now on")
                    objRef._applyServerStatuses()
                except Exception as e:
                    printBoldRed(f"Exception {e} encountered in heartbeat")
                    objRef.connectToRedisForHeartbeatAndEmergencyMonitoring(forceRenew=True)
                try:
                    time.sleep(heartbeatGap if statuses is not None else 0.1)
                except Exception:
                    noteSuppressed()

        combinedHeartbeatThread = threading.Thread(name="Combined Heartbeat Update Thread", target=updateHeartbeat,
                                                   args=(), daemon=True)
        combinedHeartbeatThread.start()

        def toggleConnectionStatus():
            """PC_PLC_ConnectionStatus (the PC is alive) toggled every combined.connection.status.sleepinterval.sec
            in a thread of its own. It used to be toggled by the status thread above, which also runs the aborts
            for a server that is down (abortCycle: about 1-4 s), so the toggle stopped for that long - long enough
            for a PLC that watches it to take the PC as dead."""
            while not objRef.shutdown:
                objRef.lastToggleLoopAt = time.monotonic()
                try:
                    statusToBeSent = not PLCIOServer.LAST_CONNECTION_STATUS_UPDATED
                    objRef.write_bool(PLCIOServer.PC_PLC_ConnectionStatusTag, statusToBeSent, debug=False)
                    PLCIOServer.LAST_CONNECTION_STATUS_UPDATED = statusToBeSent
                except Exception as e:
                    printBoldRed(f"Exception {e} while toggling {PLCIOServer.PC_PLC_ConnectionStatusTag}")
                try:
                    time.sleep(CosThetaConfigurator.getInstance().getCombinedConnectionStatusUpdateInterval())
                except Exception:
                    noteSuppressed()

        self.connectionStatusToggleThread = threading.Thread(name="PC Connection Status Toggle Thread",
                                                             target=toggleConnectionStatus, args=(), daemon=True)
        self.connectionStatusToggleThread.start()

    # ---------------------------------------------------------------- other servers down: what the IOServer does
    def _notice(self, name: str, text: Optional[str], ttlSecs: float = 15.0) -> None:
        """Show (text) or remove (None) a flashing message on the frontend (top right)."""
        try:
            if self._noticeRedisConnection is None:
                self._noticeRedisConnection = Redis(self.redisHostname, self.redisPort, socket_timeout=1.0,
                                                    socket_connect_timeout=1.0)
            if text:
                setFrontendNotice(self._noticeRedisConnection, name, text, ttlSecs)
            else:
                clearFrontendNotice(self._noticeRedisConnection, name)
        except Exception:
            noteSuppressed()

    def serverStatuses(self) -> Dict[str, Any]:
        """{"heartbeat": alive, "camera" / "qrcode" / "db" / "fe": alive or None (not known)}. Before the first message
        from the heartbeat server: alive, the others not known (no rule applies)."""
        with self._statusLock:
            statuses = dict(self._serverStatus)
            silence = time.monotonic() - self._lastHeartbeatServerMessage
            seen = self._heartbeatServerSeen
        hbAlive = (not seen) or silence <= CosThetaConfigurator.getInstance().getHeartbeatServerSilenceSecs()
        result = {"heartbeat": hbAlive}
        for name in ("camera", "qrcode", "db", "fe", "redis"):
            result[name] = statuses.get(name) if hbAlive else None
        return result

    def newCycleHoldReason(self) -> Optional[str]:
        """Why a new cycle may not start now (heartbeat server, frontend or database down), or None."""
        return self._newCycleHold

    def _applyServerStatuses(self) -> None:
        """The rules for a server that is down, applied on every status message (and when the heartbeat server
        falls silent):
          camera down         a picture the write loop waits for is answered NOT OK at once (write loop)
          QR code server down nothing (it reads nothing, so nothing is sent)
          database down       the cycle in progress is finished ("Continue till end of cycle" on the frontend);
                              no new cycle starts until it is back
          frontend down       the cycle in progress is aborted (it builds the cycle's record); no new cycle until
                              it is back
          heartbeat server    the cycle in progress is aborted (camera server and frontend told); no new cycle until
          down                it is back"""
        st = self.serverStatuses()
        state = self.machineState.getCurrentState()
        inCycle = state not in (MachineState.READ_QR_CODE, MachineState.WRITE_QR_CODE)

        hbDown = not st["heartbeat"]
        if hbDown and not self._heartbeatServerDown:
            self._heartbeatServerDown = True
            message = "Heartbeat server down (no status message received)"
            printBoldRed(message); self.logger.error(message); self._audit(message)
            if state != MachineState.READ_QR_CODE:
                self.abortCycle("heartbeat server down", notifyQRCodeServer=True, notifyFrontend=True,
                                clearQueues=True, pcAbort=True)
        elif not hbDown and self._heartbeatServerDown:
            self._heartbeatServerDown = False
            message = "Heartbeat server back"
            printBoldGreen(message); self.logger.info(message); self._audit(message)
        self._notice("heartbeat", "Heartbeat server down: cycle aborted; next cycle on hold" if hbDown else None,
                     ttlSecs=30)

        feDown = st["fe"] is False
        if feDown and not self._frontendDown:
            self._frontendDown = True
            message = "Frontend down (heartbeat server)"
            printBoldRed(message); self.logger.error(message); self._audit(message)
            if state != MachineState.READ_QR_CODE:
                self.abortCycle("frontend down", notifyQRCodeServer=True, notifyFrontend=False,
                                clearQueues=True, pcAbort=True)
        elif not feDown and self._frontendDown and st["fe"] is True:
            self._frontendDown = False
            message = "Frontend back"
            printBoldGreen(message); self.logger.info(message); self._audit(message)

        dbDown = st["db"] is False
        if dbDown != self._databaseDown and (dbDown or st["db"] is True):
            self._databaseDown = dbDown
            message = ("Database down: the cycle in progress is finished, the next one is held" if dbDown
                       else "Database back")
            (printBoldRed if dbDown else printBoldGreen)(message)
            (self.logger.error if dbDown else self.logger.info)(message)
            self._audit(message)
        self._notice("db", ("Database down: continue till end of cycle" if inCycle
                            else "Database down: next cycle on hold") if self._databaseDown else None, ttlSecs=30)

        camDown = st["camera"] is False
        if camDown != self._cameraDown and (camDown or st["camera"] is True):
            self._cameraDown = camDown
            message = "Camera server down: a picture is answered NOT OK" if camDown else "Camera server back"
            (printBoldRed if camDown else printBoldGreen)(message)
            (self.logger.error if camDown else self.logger.info)(message)
            self._audit(message)

        redisDown = st["redis"] is False
        if redisDown != getattr(self, "_redisDown", False) and (redisDown or st["redis"] is True):
            self._redisDown = redisDown
            message = ("Redis not storing data (heartbeat server's check): no new cycle until it is" if redisDown
                       else "Redis storing data again")
            (printBoldRed if redisDown else printBoldGreen)(message)
            (self.logger.error if redisDown else self.logger.info)(message)
            self._audit(message)
        hold = ("heartbeat server down" if hbDown else "frontend down" if self._frontendDown
                else "database down" if self._databaseDown
                else "Redis not storing data" if getattr(self, "_redisDown", False) else None)
        if hold != self._newCycleHold:
            self._newCycleHold = hold
            message = f"New cycles on hold: {hold}" if hold else "New cycles may start again"
            self._audit(message); self.logger.info(message)
            if hold is None:
                self._heldReasonLogged = None

    def launchEmergencyMonitoringThread(self):
        """PLC_PC_EmergencyAbort, read every 1 s; True -> abortCycle (which also sets the tag back to False).
        The emergency button is pushed and released. If the operator holds it down longer, the PLC keeps the tag True
        and the PC sees it True again after its own reset: the cycle is then aborted once per read (about every 1.7 s:
        1 s poll + the abort's 0.7 s) for as long as the button is held - several aborts one after the other. That is
        harmless (user, 2026-11-06): every abort resets the same tags and arms the QR code read again, the frontend acts
        only on the first (it is at the QR code for the later ones, which it ignores) and sends one abort record. A
        press shorter than the 1 s poll is caught only if the PLC holds the tag until the PC has reset it."""
        objRef = self

        def monitorEmergency():
            while not objRef.shutdown:
                objRef.lastEmergencyLoopAt = time.monotonic()
                emergencyActivated: bool = False
                if objRef._readsReady():
                    try:
                        emergencyActivated = objRef.read_bool(PLCIOServer.PLC_PC_EmergencyAbort)
                    except Exception as e:
                        printBoldRed(f"Exception {e} encountered in emergency monitoring")
                        objRef.connectToRedisForHeartbeatAndEmergencyMonitoring(forceRenew=True)
                if emergencyActivated:
                    printBoldRed(
                        "Combined Heartbeat - Emergency Button pressed in Machine : sendEmergencyAbortFromIOServerToFEServer")
                    try:
                        objRef.abortCycle("emergency button pressed on the machine (PLC)",
                                          notifyQRCodeServer=True, notifyFrontend=True, clearQueues=True)
                    except Exception as e:          # the emergency thread must never stop
                        import traceback
                        objRef.logger.error(f"Emergency abort failed: {e}\n{traceback.format_exc()}")
                try:
                    time.sleep(1.0)
                except Exception:
                    noteSuppressed()

        self.emergencyMonitoringThread = threading.Thread(name="Emergency Monitoring Thread", target=monitorEmergency,
                                                     args=(), daemon=True)
        self.emergencyMonitoringThread.start()

    def abortCycle(self, reason: str, notifyQRCodeServer: bool = True, notifyFrontend: bool = True,
                   clearQueues: bool = True, pcAbort: bool = False) -> None:
        """Abort the current cycle and go back to READ_QR_CODE, whatever state the cycle is in. Used for the emergency
        button on the machine (PLC), a lost server connection (heartbeat) and an abort sent by the frontend.
          notifyQRCodeServer: tell the QR code server to stop a scan in progress
          clearQueues:        drop the camera / QR code messages of the aborted cycle
          notifyFrontend:     tell the frontend (emergency-abort screen, abort record). False when the abort came
                              FROM the frontend, which has already done that itself.
          pcAbort:            the PC aborted the cycle itself (not the PLC's emergency button): PC_PLC_CycleAborted
                              (if the PLC has it) is set True after the reset, and False when the next QR code is
                              accepted; the reason is shown on the frontend
        The read and write loops only move the state on if it is still the state they started from
        (incrementStateFrom / decrementStateFrom), so a step that finishes after the abort cannot move the reset
        cycle on."""
        with self._abortLock:
            stateBefore = self.machineState.getCurrentState()
            printBoldRed(f"ABORTING CYCLE in state {stateBefore.name}: {reason}")
            self._audit(f"CYCLE ABORTED in state {stateBefore.name}: {reason}")
            try:
                self.logger.error(f"Cycle aborted in state {stateBefore.name}: {reason}")
            except Exception:
                noteSuppressed()
            redisConnection = self.emergencyRedisConnection
            if notifyQRCodeServer:
                try:
                    sendAbortFromIOServerToQRCodeServer(redisConnection)
                except Exception:
                    noteSuppressed()
                time.sleep(0.5)
            if clearQueues:
                try:
                    clearKeyCommunicationQueuesOnAbort()
                except Exception:
                    noteSuppressed()
                time.sleep(0.2)
            if notifyFrontend:
                try:
                    sendEmergencyAbortFromIOServerToFEServer(redisConnection)
                except Exception:
                    noteSuppressed()
            self.resetTagToFalse(PLCIOServer.PLC_PC_EmergencyAbort)
            # The state first, so that nothing of the aborted cycle is acted on any more, then the tags
            # the reason goes with this state change itself (CHANGES 112: a shared "next reason" could be taken by
            # another thread's change, now that the listener runs outside the state lock)
            self.machineState.setCurrentState(MachineState.READ_QR_CODE, reason=f"abort: {reason}")
            self._dropCameraRequest()
            self.resetForNextCycle(f"cycle aborted in state {stateBefore.name}: {reason}")
            if pcAbort and self._optionalTagWritable(PLCIOServer.PC_PLC_CycleAborted):
                with self._asPrivileged():
                    if not self._writeVerified(PLCIOServer.PC_PLC_CycleAborted, True):
                        self.logger.error(f"Could not set {PLCIOServer.PC_PLC_CycleAborted} = True")
            if pcAbort:
                self._notice("abort", f"Cycle aborted: {reason}", ttlSecs=20)
            self._resetOpTimestamps()  # Clear cycle timing on abort
            self.lastAbortReason = reason
            self.abortCount += 1

    def _visionTriggerTag(self, readState: MachineState) -> Optional[str]:
        """The PLC's Check (trigger) tag of a vision check, by its READ (take picture) state."""
        return {
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE: PLCIOServer.PLC_PC_CheckKnuckle,
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING: PLCIOServer.PLC_PC_CheckHub,
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING: PLCIOServer.PLC_PC_CheckHubAndSecondBearing,
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER: PLCIOServer.PLC_PC_CheckNutAndPlateWasher,
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS: PLCIOServer.PLC_PC_CheckNoCapBunk,
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK: PLCIOServer.PLC_PC_CheckNoBunk,
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER: PLCIOServer.PLC_PC_CheckSplitPinAndWasher,
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP: PLCIOServer.PLC_PC_CheckCap,
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS: PLCIOServer.PLC_PC_CheckBunk,
        }.get(readState)

    def _readBoolOrNone(self, tag: str) -> Optional[bool]:
        """Read a bool from the PLC; None if it could not be read (read_bool cannot tell a failure from a value)."""
        if not tag or not self._readsReady():
            return None
        try:
            return bool(self.dataIOForReads.read(tag))
        except Exception as e:
            printBoldRed(f"Could not read {tag}: {e}")
            return None

    def _writeFalseWithRetries(self, tag: str, why: str) -> bool:
        return self._writeWithRetries(tag, False, why)

    def _writeWithRetries(self, tag: str, value, why: str) -> bool:
        """Write a bool (or a float) to the PLC, up to VISION_RESULT_RESET_ATTEMPTS times."""
        for attempt in range(PLCIOServer.VISION_RESULT_RESET_ATTEMPTS):
            written = (self.write_bool(tag, value, debug=False) if isinstance(value, bool)
                       else self.write_float(tag, float(value), debug=False))
            if written:
                return True
            time.sleep(PLCIOServer.WRITE_RETRY_INTERVAL)
        printBoldRed(f"Could not set {tag} to {value} {why} ({PLCIOServer.VISION_RESULT_RESET_ATTEMPTS} attempts)")
        self.logger.error(f"Could not set {tag} to {value} {why}")
        return False

    def triggerIgnoredUntilReset(self, readState: MachineState) -> bool:
        with self._awaitingTriggerResetLock:
            return readState in self._awaitingTriggerReset

    def _setAwaitingTriggerReset(self, readState: Optional[MachineState], on: bool) -> None:
        if readState is None:
            return
        with self._awaitingTriggerResetLock:
            if on:
                self._awaitingTriggerReset.add(readState)
                self._awaitingTriggerSince[readState] = time.monotonic()
            else:
                self._awaitingTriggerReset.discard(readState)
                self._awaitingTriggerSince.pop(readState, None)

    TRIGGER_HELD_WARNING_SECS: float = 3.0      # trigger still True this long after a NOT OK -> logged once (PLC side)

    def _noteTriggerStillHeld(self, readState: MachineState, triggerTag: str) -> None:
        """The trigger is still True after a NOT OK: after TRIGGER_HELD_WARNING_SECS, say so once."""
        with self._awaitingTriggerResetLock:
            since = self._awaitingTriggerSince.get(readState)
            if since is None or time.monotonic() - since < PLCIOServer.TRIGGER_HELD_WARNING_SECS:
                return
            self._awaitingTriggerSince[readState] = None          # once per NOT OK
        message = (f"{triggerTag} still True {PLCIOServer.TRIGGER_HELD_WARNING_SECS:.0f} s after the PC cleared it with "
                   f"the NOT OK: no new picture until the PLC sets it False. The PLC program must raise it once per "
                   f"2HB press (and let the PC's False stand), not hold it")
        printBoldRed(message)
        self.logger.error(message)
        self._audit(message)

    # ---------------------------------------------------------------- PLC handshakes
    #   Check-Done-OK (vision checks, QR code): one sequence in perform_write_state_action, written before the state
    #       changes - OK, (0.05 s) Check False, Done True, (NOT OK 0.3 s / OK 0.4 s) Done False, then NOT OK: Check
    #       False again / OK: OK False; the plc.sleeptime.between.* settings.
    #   Set-Value (torques): after the value was read, Set = False and Value = 0.0 after plc.handshake.reset.delay.sec
    #       (Station 2: also RotationDuringTighteningDone = False) - a job of the ONE scheduler thread.
    #   Done (presses): after it was read, Done = False after plc.handshake.reset.delay.sec - also a scheduler job.
    # scheduleVisionResultReset / completePendingResultReset / the trigger gate below are kept but no longer used for
    # the Check-Done-OK handshake.

    def handshakeResetDelay(self) -> float:
        return CosThetaConfigurator.getInstance().getPlcHandshakeResetDelay()

    def scheduleTagReset(self, key: str, resets: list, why: str, delay: Optional[float] = None) -> None:
        """Queue one job: after the delay, write each (tag, value) of resets in order (bool or float, with retries)
        and log one line with the outcome of each. Tags with an empty name are left out. A pending job with the same
        key is replaced."""
        resets = [(tag, value) for tag, value in resets if tag]
        if not resets:
            return
        delay = self.handshakeResetDelay() if delay is None else delay
        objRef = self

        def reset():
            outcomes = []
            for tag, value in resets:
                if tag == PLCIOServer.PLC_PC_CheckQRCode and value is True:
                    done = objRef._writeVerified(tag, True)          # arming the QR read: written and read back
                else:
                    done = objRef._writeWithRetries(tag, value, why)
                outcomes.append(f"{tag} -> {value} ({'done' if done else 'FAILED'})")
            printLight(f"{delay} s {why}: " + ", ".join(outcomes))

        self._resultResetScheduler.schedule(key, delay, reset, info={"tags": [t for t, _ in resets], "why": why})

    def scheduleVisionResultReset(self, tags: list, checkState: MachineState, result: bool = True) -> None:
        """Not used any more (perform_write_state_action now writes the whole Check-Done-OK sequence before the state
        changes); kept. Was: rules (a) and (b), the delayed part, after the result of a Check-Done-OK operation. tags = [<Check>OK, <Check>Done, <Check> trigger];
        checkState = the operation's WRITE state.
          OK     (b): after the delay, Check, Done and OK = False (Done before OK, so the PLC never sees Done = True
                      with OK = False, which would read as a NOT OK).
          NOT OK (a): OK = False, Done = True, Check = False were written with the result; after the delay, Done =
                      False. For the QR
                      code, CheckQRCode is then set True again: the PC arms the QR read (as at the end of a cycle), so
                      the operator can scan again."""
        okTag = tags[0] if len(tags) > 0 else None
        doneTag = tags[1] if len(tags) > 1 else None
        checkTag = tags[2] if len(tags) > 2 else None
        if not okTag:
            return
        why = f"after the {'OK' if result else 'NOT OK'} of {checkState.name}"
        if result:
            resets = [(checkTag, False), (doneTag, False), (okTag, False)]
        else:
            resets = [(doneTag, False)]
            if checkState == MachineState.WRITE_QR_CODE and checkTag:
                resets.append((checkTag, True))
        self.scheduleTagReset(okTag, resets, why)

    def completePendingResultReset(self, tags: list) -> None:
        """A new result is about to be written for this operation: a reset still pending from its previous result is
        done NOW (not dropped), so that the PLC sees Done go False before the new result's Done = True."""
        okTag = tags[0] if tags else None
        if okTag and self._resultResetScheduler.runNow(okTag):
            printLight(f"Pending reset of {okTag} done at once: a new result follows")

    def cancelVisionResultReset(self, tags: list) -> None:
        """Kept for callers of the earlier name: same as completePendingResultReset (the pending reset is done now,
        not dropped)."""
        self.completePendingResultReset(tags)

    def launchFrontendAbortListenerThread(self):
        """Frontend -> IOServer. ABORT (sendAbortFromFEServerToIOServer, test tools): resets the cycle to READ_QR_CODE,
        like the emergency button on the machine. FRONTEND_ABORT_REQUEST (the frontend's password-protected abort):
        handleFrontendAbortRequest - the cycle is aborted only if PC_PLC_CycleAborted = True could be written."""
        objRef = self

        def listen():
            connection = None
            while not objRef.shutdown:
                try:
                    if connection is None:
                        connection = Redis(objRef.redisHostname, objRef.redisPort, retry_on_timeout=True)
                    kind, reason = readAbortInIOServerFromFEServer(connection, block=500, withKind=True)
                    if kind == "abort":                 # ABORT (test tools): abort at once
                        objRef.abortCycle(f"abort from the frontend ({reason or 'no reason given'})",
                                          notifyQRCodeServer=True, notifyFrontend=False, clearQueues=True,
                                          pcAbort=True)
                    elif kind == "request":             # the frontend's own abort: only if the PLC flag is written
                        objRef.handleFrontendAbortRequest(reason)
                except Exception as e:
                    printBoldRed(f"Exception {e} in the frontend abort listener")
                    connection = None
                    time.sleep(1.0)

        threading.Thread(name="Frontend Abort Listener Thread", target=listen, daemon=True).start()

    def launchMonitorStopCommandThread(self):
        objRef = self

        def lookForStop():
            while not objRef.shutdown:
                try:
                    if objRef.heartbeatRedisConnected:
                        _, stopNow = getStopCommandFromQueue(objRef.heartbeatRedisConnection)
                        if stopNow:
                            objRef.shutdown = True
                            sendStoppedResponse(objRef.heartbeatRedisConnection, aProducer=PLCIOServer.logSource)
                    else:
                        objRef.connectToRedisForHeartbeatAndEmergencyMonitoring(False)
                except Exception:
                    noteSuppressed()
                if not objRef.shutdown:
                    time.sleep(2)

        self.monitorStopThread = threading.Thread(name='Monitor Stop Thread in IOServer - IOserver',
                                                  target=lookForStop,
                                                  args=(), daemon=True)
        self.monitorStopThread.start()

    def launchReadLoopThread(self):
        objRef = self

        def readLoop():
            """Read loop to read the input for current machine states"""
            written_TagsToBeRead: int = 0
            writeCounter = 0
            while not objRef.shutdown:
                objRef.lastReadLoopAt = time.monotonic()
                try:
                    writeCounter += 1
                    valueRead: bool = False
                    goAheadToProcessing: bool = False
                    allok: bool = True
                    typeOfValues: str = "nothing"
                    finalValue: float = PLCIOServer.FALSE_FLOAT_VALUE

                    if (objRef._readsReady()
                            and not objRef.connectionInProgress and not objRef._plcCheckPending):
                        tagsToBeRead, valueTypes = objRef.getCurrentTagsToBeRead()
                        if tagsToBeRead is not None:
                            # if (writeCounter % 3) == 0:
                            #     printBoldBlue(f"Tags to be read are {tagsToBeRead} whose respective valueTypes are {valueTypes}")
                            if written_TagsToBeRead == 0:
                                written_TagsToBeRead += 1
                            if len(tagsToBeRead) == 1:
                                goAheadToProcessing = True
                                if valueTypes[0] == bool:
                                    typeOfValues = "bool"
                                    valueRead = objRef.read_bool(tagsToBeRead[0])
                                    allok = allok and valueRead
                                elif valueTypes[0] == float:
                                    typeOfValues = "float"
                                    finalValue = objRef.read_float(tagsToBeRead[0])
                            else:
                                typeOfValues = "bool"
                                valueRead = objRef.read_bool(tagsToBeRead[0])
                                allok = allok and valueRead
                                if allok:
                                    goAheadToProcessing = True
                                    valueRead = objRef.read_bool(tagsToBeRead[1])
                                    allok = allok and valueRead

                    if goAheadToProcessing:
                        objRef._auditRead(tagsToBeRead, allok if typeOfValues == "bool" else finalValue)
                        if typeOfValues == "bool" and allok:
                            objRef._notePlcTagTrue(tagsToBeRead)
                        if typeOfValues == "bool":
                            returnValue = objRef.process_current_read_state(allok)
                            if returnValue:
                                written_TagsToBeRead = 0
                        elif typeOfValues == "float":
                            returnValue = objRef.process_current_read_state(finalValue)
                            if returnValue:
                                written_TagsToBeRead = 0

                except Exception as e:
                    printBoldRed(f"Encountered exception {e}")

                time.sleep(objRef.read_interval)

        self.continuousReadLoop = threading.Thread(name='Continuous Read Loop in IOServer',
                                                   target=readLoop,
                                                   args=(), daemon=True)
        self.continuousReadLoop.start()

    def launchWriteTagsLoopThread(self):
        objRef = self

        def writeTagsLoop():
            """The write loop, restarted after an unexpected exception (logged) so that the IOServer never stops
            answering the PLC while it still sends ALIVE."""
            while not objRef.shutdown:
                try:
                    writeTagsLoopBody()
                except Exception as e:
                    import traceback
                    message = f"Write loop stopped by an exception ({e}); restarted"
                    printBoldRed(message)
                    objRef.logger.error(f"{message}\n{traceback.format_exc()}")
                    objRef._audit(message)
                    time.sleep(0.5)

        def writeTagsLoopBody():
            """Main do loop to take action wherever something required."""
            written_TagsToBeWritten: int = 0
            while not objRef.shutdown:
                objRef.lastWriteLoopAt = time.monotonic()
                stateOfTags = objRef.machineState.getCurrentState()
                tagsToBeWritten = objRef.getCurrentTagsToBeWritten()
                if objRef.machineState.getCurrentState() != stateOfTags:
                    continue            # state changed (abort) while the tags were looked up: look again
                if objRef._writesReady() and not objRef.connectionInProgress:
                    if tagsToBeWritten is not None and len(tagsToBeWritten) > 0:
                        if written_TagsToBeWritten == 0:
                            written_TagsToBeWritten += 1
                        stateBeforeWrite = stateOfTags
                        if stateBeforeWrite == MachineState.WRITE_QR_CODE:
                            hold = objRef.newCycleHoldReason()
                            if hold:
                                # waiting for a scan when a new cycle went on hold (database down): the QR code server
                                # stops reading and the IOServer goes back to READ_QR_CODE (CheckQRCode left as it is);
                                # the read is asked for again when the hold ends
                                message = f"Waiting for a QR code scan: new cycles on hold ({hold}); QR code read stopped"
                                printBoldRed(message); objRef.logger.error(message); objRef._audit(message)
                                try:
                                    sendAbortFromIOServerToQRCodeServer(self.redisConnectionForWrites)
                                except Exception:
                                    noteSuppressed()
                                objRef.machineState.decrementStateFrom(MachineState.WRITE_QR_CODE)
                                time.sleep(objRef.write_loop_interval)
                                continue
                            success, qrCode = readDataInIOServerFromQRCodeServer(self.redisConnectionForWrites)
                            if success and objRef.machineState.getCurrentState() != stateBeforeWrite:
                                printBoldRed("QR code received after the cycle was aborted; ignored")
                                success = False
                            if success:
                                objRef.currentQRCode = qrCode  # Store the QR code for this cycle
                                objRef.cycleId += 1
                                publishCycleContext(objRef.redisConnectionForWrites, qrCode, objRef.cycleId)
                                objRef._audit(f"CYCLE {objRef.cycleId} START: QR code accepted by the QR server: {qrCode}")
                                printBoldGreen(f"Received QR Code: {qrCode}")
                                if objRef._optionalTagWritable(PLCIOServer.PC_PLC_CycleAborted):  # new cycle: flag cleared
                                    objRef.write_bool(PLCIOServer.PC_PLC_CycleAborted, False, debug=False)
                                # 1. Populate the cycle settings (side, model, setpoints) FIRST ...
                                settingsWritten = objRef.writeCycleSettingsToPLC(qrCode)
                                # 2. ... then QRCodeCheckOK, then QRCodeCheckDone. If the settings could not all be
                                #    written, OK = False (like a NOT OK check): the cycle must not start without them.
                                if objRef.perform_write_state_action(tags=tagsToBeWritten, result=settingsWritten,
                                                                     expectedState=stateBeforeWrite):
                                    # OK: OK, trigger False, Done, (0.4 s) Done False written; NOT OK: the same with
                                    # OK False and 0.3 s, then CheckQRCode armed again for a re-scan
                                    printBold(f"Transition from state {objRef.machineState.getCurrentState().name}")
                                    if settingsWritten:
                                        objRef.machineState.incrementStateFrom(MachineState.WRITE_QR_CODE)
                                    else:
                                        printBoldRed("Cycle settings not written: QR code check reported NOT OK to the PLC")
                                        objRef.machineState.decrementStateFrom(MachineState.WRITE_QR_CODE)
                                    printBold(f"        ...to state {objRef.machineState.getCurrentState().name}")
                        else:
                            # stateBeforeWrite: the WRITE state we are about to act on (captured before the read)
                            previousMachineState, evaluation, answerId = readDataInIOServerFromCameraServer(
                                self.redisConnectionForWrites, withRequestId=True)
                            if objRef.machineState.getCurrentState() != stateBeforeWrite:
                                # the cycle was aborted while waiting for the camera: this answer is for the old cycle
                                printBoldRed(f"Camera answer for {stateBeforeWrite.name} received after the cycle was "
                                             f"aborted; ignored")
                                time.sleep(objRef.write_loop_interval)
                                continue
                            expectedPictureState = MachineState(int(stateBeforeWrite) - 1)
                            request = objRef._cameraRequest
                            if request is not None and request["writeState"] != stateBeforeWrite:
                                request = None
                            gotAnswer = answerId is not None or previousMachineState != MachineState.INVALID_STATE
                            if gotAnswer and answerId is not None and request is not None and answerId != request["id"]:
                                # an answer to an earlier request (it came after the IOServer stopped waiting for it)
                                message = (f"Camera answer to request {answerId} while waiting for {request['id']} "
                                           f"({expectedPictureState.name}); ignored")
                                printBoldRed(message)
                                objRef._audit(message)
                                time.sleep(objRef.write_loop_interval)
                                continue
                            if gotAnswer and previousMachineState not in (MachineState.INVALID_STATE, expectedPictureState):
                                # an answer for another picture (late, from an earlier request): not this check's result
                                message = (f"Camera answer for {previousMachineState.name} while waiting for "
                                           f"{expectedPictureState.name}; ignored")
                                printBoldRed(message)
                                objRef._audit(message)
                                time.sleep(objRef.write_loop_interval)
                                continue
                            noAnswerReason = None
                            if gotAnswer and answerId is not None and previousMachineState == MachineState.INVALID_STATE:
                                evaluation = False          # the camera answered this request, without a picture state
                            elif not gotAnswer:
                                # no answer yet: camera server down -> NOT OK at once; none within the timeout -> NOT OK
                                timeout = CosThetaConfigurator.getInstance().getPlcCameraAnswerTimeout()
                                waited = time.monotonic() - (request["sentAt"] if request is not None
                                                             else objRef._writeStateSince)
                                if objRef._cameraDown:
                                    noAnswerReason = "camera server down"
                                elif waited > timeout:
                                    noAnswerReason = f"no answer from the camera server in {timeout:g} s"
                                if noAnswerReason is None:
                                    time.sleep(objRef.write_loop_interval)
                                    continue
                                objRef._pictureNotOkWithoutAnswer(stateBeforeWrite, noAnswerReason)
                                evaluation = False
                            if evaluation:
                                if not objRef.perform_write_state_action(tags=tagsToBeWritten, result=True,
                                                                         expectedState=stateBeforeWrite):
                                    time.sleep(objRef.write_loop_interval)
                                    continue        # cycle aborted while writing: do not move on
                                # OK = True, trigger False, Done True, (0.4 s) Done False written: next state now
                                # ── Record op-END timestamps for Done-tag-based operations ──
                                if stateBeforeWrite == MachineState.WRITE_RESULT_OF_CHECKING_KNUCKLE:
                                    objRef._recordOpEnd(PLCIOServer.OP_KNUCKLE)
                                elif stateBeforeWrite == MachineState.WRITE_RESULT_OF_CHECKING_HUB_AND_BOTTOM_BEARING:
                                    objRef._recordOpEnd(PLCIOServer.OP_HUB_BEARING)
                                elif stateBeforeWrite == MachineState.WRITE_RESULT_OF_CHECKING_TOP_BEARING:
                                    objRef._recordOpEnd(PLCIOServer.OP_TOP_BEARING)
                                elif stateBeforeWrite == MachineState.WRITE_RESULT_OF_CHECKING_BUNK_FOR_COMPONENT_PRESS:
                                    objRef._recordOpEnd(PLCIOServer.OP_NOCAP_BUNK)
                                elif stateBeforeWrite == MachineState.WRITE_RESULT_OF_CHECKING_SPLITPIN_AND_WASHER:
                                    objRef._recordOpEnd(PLCIOServer.OP_SPLITPIN)
                                elif stateBeforeWrite == MachineState.WRITE_RESULT_OF_CHECKING_CAP:
                                    objRef._recordOpEnd(PLCIOServer.OP_CAP)
                                objRef._cameraRequest = None
                                printBold(f"Transition from state {objRef.machineState.getCurrentState().name}")
                                objRef.machineState.incrementStateFrom(stateBeforeWrite)
                                printBold(f"        ...to state {objRef.machineState.getCurrentState().name}")
                            else:
                                objRef._cameraRequest = None
                                if objRef.perform_write_state_action(tags=tagsToBeWritten, result=False,
                                                                     expectedState=stateBeforeWrite):
                                    # OK = False, trigger False, Done True, (0.3 s) Done False written: only now back
                                    # to the take-picture state, where trigger and Done are both False
                                    printBoldRed(
                                        f"Did not move ahead from state {objRef.machineState.getCurrentState().name}")
                                    printBold(f"Transition from state {objRef.machineState.getCurrentState().name}")
                                    objRef.machineState.decrementStateFrom(stateBeforeWrite)
                                    printBold(f"        ...to state {objRef.machineState.getCurrentState().name}")
                time.sleep(objRef.write_loop_interval)

        self.continuousDoLoop = threading.Thread(name='Continuous Do Loop in IOServer',
                                                 target=writeTagsLoop,
                                                 args=(), daemon=True)
        self.continuousDoLoop.start()

    def process_current_read_state(self, valueRead: bool | float | None):
        """Process the value read from PLC based on current machine state."""
        if valueRead is None:
            return False

        # printBoldGreen(f"Read value {valueRead}")
        returnValue: bool = False
        abortsBefore = self.abortCount          # an abort meanwhile does its own reset (see the end)
        currentState = self.machineState.getCurrentState()

        if type(valueRead) == bool:
            if valueRead:
                successfullySent: bool = False
                try:
                    # Step 0: QR Code - not while a new cycle is on hold (heartbeat server, frontend or database
                    # down): the QR code server is then not asked to read; CheckQRCode is left as it is
                    if currentState == MachineState.READ_QR_CODE:
                        hold = self.newCycleHoldReason()
                        if hold:
                            if self._heldReasonLogged != hold:
                                self._heldReasonLogged = hold
                                message = f"QR code read requested by the PLC, held: {hold}"
                                printBoldRed(message); self.logger.error(message); self._audit(message)
                            return False
                        successfullySent = sendDataFromIOServerToQRCodeServer(self.redisConnectionForReads)

                    # Step 1: Knuckle — START of T1
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE:
                        successfullySent = self._requestPicture(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE)
                        if successfullySent:
                            self._recordOpStart(PLCIOServer.OP_KNUCKLE)

                    # Step 2: Hub (HUB_AND_BOTTOM_BEARING state) — START of T2
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING:
                        successfullySent = self._requestPicture(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING)
                        if successfullySent:
                            self._recordOpStart(PLCIOServer.OP_HUB_BEARING)

                    # Step 3: Hub and Second Bearing (TOP_BEARING state) — START of T3
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING:
                        successfullySent = self._requestPicture(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING)
                        if successfullySent:
                            self._recordOpStart(PLCIOServer.OP_TOP_BEARING)

                    # Step 4: Nut and Plate Washer — START of T4
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER:
                        successfullySent = self._requestPicture(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER)
                        if successfullySent:
                            self._recordOpStart(PLCIOServer.OP_NUT_WASHER)

                    # Step 5: Tightening Torque 1 Done
                    elif currentState == MachineState.READ_TIGHTENING_TORQUE_1_DONE:
                        successfullySent = True

                    # Step 7: Bunk for Component Press — START of T5 and T6
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS:
                        successfullySent = self._requestPicture(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS)
                        if successfullySent:
                            self._recordOpStart(PLCIOServer.OP_NOCAP_BUNK)
                            self._recordOpStart(PLCIOServer.OP_TORQUE2)

                    # Step 8: Component Press Done
                    elif currentState == MachineState.READ_COMPONENT_PRESS_DONE:
                        successfullySent = sendDataFromIOServerToFEServer(self.redisConnectionForReads,
                                                                          currentMachineState=MachineState.READ_COMPONENT_PRESS_DONE)
                        if successfullySent:                        # rule (d)
                            self.scheduleTagReset(PLCIOServer.PLC_PC_ComponentPressDone,
                                                  [(PLCIOServer.PLC_PC_ComponentPressDone, False)],
                                                  "after the component press Done was read",
                                                  delay=CosThetaConfigurator.getInstance().getPlcComponentPressResetDelay())

                    # Step 9: No Bunk
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK:
                        successfullySent = self._requestPicture(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK)

                    # Step 10: Tightening Torque 2 Done — END of T6
                    elif currentState == MachineState.READ_TIGHTENING_TORQUE_2_DONE:
                        successfullySent = True
                        self._recordOpEnd(PLCIOServer.OP_TORQUE2)

                    # Step 11: Split Pin and Washer — START of T7
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER:
                        successfullySent = self._requestPicture(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER)
                        if successfullySent:
                            self._recordOpStart(PLCIOServer.OP_SPLITPIN)

                    # Step 12: Cap — START of T8
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP:
                        successfullySent = self._requestPicture(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP)
                        if successfullySent:
                            self._recordOpStart(PLCIOServer.OP_CAP)

                    # Step 13: Bunk for Cap Press — START of T9
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS:
                        successfullySent = self._requestPicture(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS)
                        if successfullySent:
                            self._recordOpStart(PLCIOServer.OP_BUNK_CAP_PRESS)

                    # Step 14: Cap Press Done
                    elif currentState == MachineState.READ_CAP_PRESS_DONE:
                        successfullySent = sendDataFromIOServerToFEServer(self.redisConnectionForReads,
                                                                          currentMachineState=MachineState.READ_CAP_PRESS_DONE)
                        if successfullySent:                        # rule (d)
                            self.scheduleTagReset(PLCIOServer.PLC_PC_CapPressDone,
                                                  [(PLCIOServer.PLC_PC_CapPressDone, False)],
                                                  "after the cap press Done was read", delay=CosThetaConfigurator.getInstance().getPlcComponentPressResetDelay())

                    # Step 15: Free Rotation Torque 1 Done — END of T9
                    elif currentState == MachineState.READ_FREE_ROTATION_TORQUE_1_DONE:
                        successfullySent = True
                        self._recordOpEnd(PLCIOServer.OP_BUNK_CAP_PRESS)

                    if successfullySent:
                        printBold(f"Transition from state {self.machineState.getCurrentState().name}")
                        self.machineState.incrementStateFrom(currentState)
                        printBold(f"        ...to state {self.machineState.getCurrentState().name}")

                except Exception as e:
                    printBoldRed(f"Exception {e} encountered in current_read_state - 1")

                returnValue = valueRead
            else:
                returnValue = valueRead

        elif type(valueRead) == float:
            if valueRead == PLCIOServer.FALSE_FLOAT_VALUE:
                printBold(f"Failed to read proper value of float in state {self.machineState.getCurrentState().name}")
                returnValue = False
            else:
                successfullySent: bool = False
                try:
                    # Step 5B: Tightening Torque 1 Value
                    if currentState == MachineState.READ_TIGHTENING_TORQUE_1:
                        printBoldGreen(f"Read Station 2 Torque Value (1st time): {valueRead}")
                        rotationDone = self._readStation2RotationDuringTighteningDone()
                        skip = self._skipSecondTighteningThisCycle
                        # moveToState tells the frontend where the cycle goes next when it is not the next state
                        successfullySent = sendDataFromIOServerToFEServer(self.redisConnectionForReads,
                                                                          currentMachineState=MachineState.READ_TIGHTENING_TORQUE_1,
                                                                          moveToState=(PLCIOServer.SKIP_TO_STATE_AFTER_TORQUE_1
                                                                                       if skip else MachineState.INVALID_STATE),
                                                                          value=valueRead,
                                                                          rotationDone=rotationDone)
                        if successfullySent:
                            self._recordOpEnd(PLCIOServer.OP_NUT_WASHER)   # END of T4 (M2: no separate free-rotation step)
                        # Reset Station 2 torque tags after reading (so Step 10 doesn't auto-trigger)
                        if successfullySent:
                            self._scheduleStation2TorqueReset(currentState)       # rule (c)
                            # Transition to next state after successfully reading torque; with the bypass of this
                            # cycle straight to the split pin picture (states 13-19 skipped, as the PLC does)
                            printBold(f"Transition from state {self.machineState.getCurrentState().name}")
                            jump = (int(PLCIOServer.SKIP_TO_STATE_AFTER_TORQUE_1) - int(currentState)) if skip else 1
                            self.machineState.incrementStateFrom(currentState, stateJumps=jump)
                            printBold(f"        ...to state {self.machineState.getCurrentState().name}"
                                      + ("  (component press and second tightening bypassed)" if skip else ""))
                    # Step 10B: Tightening Torque 2 Value
                    elif currentState == MachineState.READ_TIGHTENING_TORQUE_2:
                        printBoldGreen(f"Read Station 2 Torque Value (2nd time): {valueRead}")
                        rotationDone = self._readStation2RotationDuringTighteningDone()
                        successfullySent = sendDataFromIOServerToFEServer(self.redisConnectionForReads,
                                                                          currentMachineState=MachineState.READ_TIGHTENING_TORQUE_2,
                                                                          value=valueRead,
                                                                          rotationDone=rotationDone)
                        if successfullySent:
                            self._scheduleStation2TorqueReset(currentState)       # rule (c)
                            # Transition to next state after successfully reading torque
                            printBold(f"Transition from state {self.machineState.getCurrentState().name}")
                            self.machineState.incrementStateFrom(currentState)
                            printBold(f"        ...to state {self.machineState.getCurrentState().name}")

                    # Step 15B: Free Rotation Torque 1 Value - FINAL STATE — END of T9
                    elif currentState == MachineState.READ_FREE_ROTATION_TORQUE_1:
                        printBoldGreen(f"Read Station 3 Torque Value: {valueRead}")
                        successfullySent = sendDataFromIOServerToFEServer(self.redisConnectionForReads,
                                                                          currentMachineState=MachineState.READ_FREE_ROTATION_TORQUE_1,
                                                                          value=valueRead,
                                                                          moveToState=MachineState.READ_QR_CODE,
                                                                          action=moveAheadToNextComponent)
                        if successfullySent:
                            self.scheduleTagReset(PLCIOServer.PLC_PC_Station3TorqueValueSet,          # rule (c)
                                                  [(PLCIOServer.PLC_PC_Station3TorqueValueSet, False),
                                                   (PLCIOServer.PLC_PC_Station3TorqueValue, 0.0)],
                                                  "after the free rotation torque was read", delay=CosThetaConfigurator.getInstance().getPlcTorqueValuesResetDelay())
                            printBold(f"Transition from state {self.machineState.getCurrentState().name}")
                            self.machineState.incrementStateFrom(currentState)
                            printBold(f"        ...to state {self.machineState.getCurrentState().name}")

                except Exception as e:
                    printBoldRed(f"Exception {e} encountered in current_read_state - 3")

                returnValue = successfullySent

        if currentState != MachineState.READ_QR_CODE and self.machineState.getCurrentState() == MachineState.READ_QR_CODE:
            # Cycle just completed: log machine times, then reset for the next cycle. Under _abortLock, as an abort's
            # reset is (CHANGES 110): the two resets never run at the same time, and if an abort came in meanwhile
            # (it has reset everything and armed CheckQRCode itself) this reset is not done a second time.
            with self._abortLock:
                if self.abortCount != abortsBefore:
                    self._audit(f"Reset after {currentState.name} skipped: the cycle was aborted meanwhile (reset by the abort)")
                elif self.machineState.getCurrentState() == MachineState.READ_QR_CODE:
                    if self._cycleInProgress:
                        self._computeAndLogCycleTimes()
                        self._resetOpTimestamps()
                    completed = currentState == MachineState.READ_FREE_ROTATION_TORQUE_1
                    # a completed cycle leaves the resets still pending (e.g. the free rotation torque's) to run at
                    # their time
                    self.resetForNextCycle("cycle completed" if completed else
                                           f"back at READ_QR_CODE from {currentState.name}",
                                           keepPendingResets=completed)

        return returnValue

    # ---------------------------------------------------------------- picture requests (camera server)
    def _requestPicture(self, pictureState: MachineState) -> bool:
        """Ask the camera server for the picture of pictureState, with a new request id. The write loop accepts only
        the answer with this id (a late answer to an earlier request is ignored), and gives up after
        plc.camera.answer.timeout.sec (or at once when the camera server is down): the check is then NOT OK. The
        frontend shows only results of the active request (setActiveCameraRequest)."""
        self._cameraRequestSeq += 1
        # <pid>.<cycle>-<n>: the pid makes the ids of a restarted IOServer (cycle and n start from 0 again) differ from
        # the old one's, so that a late camera answer to the old IOServer's request can never match a new request
        requestId = f"{os.getpid()}.{self.cycleId}-{self._cameraRequestSeq}"
        setActiveCameraRequest(self.redisConnectionForReads, requestId)
        self._cameraRequest = {"id": requestId, "pictureState": pictureState,
                               "writeState": MachineState(int(pictureState) + 1), "sentAt": time.monotonic()}
        sent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads, currentMachineState=pictureState,
                                                  requestId=requestId)
        if not sent:
            self._cameraRequest = None
        return sent

    def _dropCameraRequest(self) -> None:
        """No answer is awaited any more (abort): the request is taken off the camera server's queue if it is still
        there, and the frontend shows no result of it."""
        request, self._cameraRequest = self._cameraRequest, None
        try:
            r = self.redisConnectionForWrites
            if request is not None:
                for messageId, fields in r.xrange(io2cameraq):
                    if fields.get(requestIdKeyString.encode()) == request["id"].encode():
                        r.xdel(io2cameraq, messageId)
            setActiveCameraRequest(r, None)
        except Exception:
            noteSuppressed()

    def _pictureNotOkWithoutAnswer(self, writeState: MachineState, reason: str) -> None:
        """The camera server gave no answer (down, or none within the timeout): the frontend is shown the check as
        NOT OK with the reason (a result of a new request id, so a late answer of the camera is not shown), the
        request is taken off the camera's queue, and the caller writes the NOT OK handshake to the PLC."""
        request = self._cameraRequest
        pictureState = MachineState(int(writeState) - 1)
        self.cameraTimeouts += 1
        message = f"{pictureState.name}: {reason}; reported NOT OK to the PLC"
        printBoldRed(message); self.logger.error(message); self._audit(message)
        self._dropCameraRequest()
        try:
            r = self.redisConnectionForWrites
            replacementId = f"{request['id'] if request else self.cycleId}:{'down' if 'down' in reason else 'timeout'}"
            setActiveCameraRequest(r, replacementId)
            sendDataFromCameraServerToFEServer(r, originalImage=None, processedImage=None, result=notok,
                                               currentMachineState=pictureState, aProducer="IOServer",
                                               requestId=replacementId, reason=reason)
        except Exception as e:
            printBoldRed(f"Could not send the NOT OK of {pictureState.name} to the frontend: {e}")
        self._notice("camera", f"Picture NOT OK: {reason}", ttlSecs=15)

    def _scheduleStation2TorqueReset(self, readState: MachineState) -> None:
        """Rule (c) for the Station 2 tightening torque (read twice per cycle, same tags): ValueSet = False,
        Value = 0.0 and RotationDuringTighteningDone = False (read with the value) after the delay."""
        self.scheduleTagReset(PLCIOServer.PLC_PC_Station2TorqueValueSet,
                              [(PLCIOServer.PLC_PC_Station2TorqueValueSet, False),
                               (PLCIOServer.PLC_PC_Station2TorqueValue, 0.0),
                               (PLCIOServer.PLC_PC_Station2RotationDuringTighteningDone, False)],
                              f"after the torque of {readState.name} was read", delay=CosThetaConfigurator.getInstance().getPlcTorqueValuesResetDelay())

    def _writeBoolUntilWritten(self, tag: str, value: bool, stateAtStart: MachineState) -> bool:
        """Write a bool, retrying until it is written. Gives up only on shutdown or if the cycle was reset meanwhile
        (emergency abort / heartbeat loss changed the state); returns False then.
        Each attempt checks the state and writes while holding _abortLock (abortCycle holds it for the whole abort),
        so a write of the old cycle can no longer land after an abort has reset the tags (e.g. Done=True left set
        for the next cycle). The lock is released between attempts, so an abort is never held up by the retries
        while the PLC is not reachable - at most by one write in progress."""
        attempts = 0
        while not self.shutdown:
            self.lastWriteLoopAt = time.monotonic()
            with self._abortLock:
                if self.machineState.getCurrentState() != stateAtStart:
                    printBoldRed(f"Cycle reset while writing {tag}; not written")
                    return False
                if self.write_bool(tag, value):
                    return True
            attempts += 1
            if attempts == 1 or attempts % 25 == 0:
                self.logger.error(f"Could not write {tag}={value} (attempt {attempts}); retrying")
                printBoldRed(f"Could not write {tag}={value} (attempt {attempts}); retrying")
            time.sleep(PLCIOServer.WRITE_RETRY_INTERVAL)
        return False

    def _clearTriggerWithNotOk(self, triggerTag: str, writeState: MachineState) -> bool:
        """The PLC's trigger = False, retried until written (False only if the cycle was reset meanwhile). Not used
        any more: perform_write_state_action writes the trigger itself, for OK and NOT OK."""
        if not self._writeBoolUntilWritten(triggerTag, False, writeState):
            return False
        printBoldYellow(f"Wrote False to {triggerTag}")
        return True

    def perform_write_state_action(self, tags: list[str], result: bool = True,
                                   expectedState: Optional[MachineState] = None) -> bool:
        """
        Result handshake of a Check-Done-OK operation (a vision check or the QR code); tags = [<Check>OK, <Check>Done,
        <Check> trigger]. Written in this order, each write retried until it succeeds (_writeBoolUntilWritten); the
        sequence stops (returns False) if the cycle is reset by an abort meanwhile:
          1. <Check>OK = result (True for OK, False for NOT OK)
          2. pause plc.sleeptime.between.ok.and.done (0.05 s)
          3. <Check> trigger = False
          4. <Check>Done = True
          5. pause - NOT OK: plc.sleeptime.between.done.and.re_read (0.3 s);
                     OK:     plc.sleeptime.between.done.and.reset_done (0.4 s)
          6. <Check>Done = False
          7. NOT OK: <Check> trigger = False again;  OK: <Check>OK = False
          QR code NOT OK (the cycle settings could not be written) only: 8. CheckQRCode = True again - the PC arms the
          QR read (as at the end of a cycle), so that the operator can scan again.
        The caller changes the machine state only after this returns True: OK -> the next state; NOT OK -> back to the
        take-picture state, where the read loop then finds the trigger and Done both False. Values the PLC needs with
        this step (the state-2 cycle settings) are written by the caller BEFORE calling this.
        Returns True if every write of the sequence was done.
        """
        stateAtStart = self.machineState.getCurrentState() if expectedState is None else expectedState
        self.completePendingResultReset(tags)   # a delayed reset still pending for this operation: done first
        okTag = tags[0]
        doneTag = tags[1]
        checkTag = tags[2] if len(tags) > 2 else None
        config = CosThetaConfigurator.getInstance()
        doneHold = (config.getPlcSleepTimeBetweenDoneAndResetDone() if result
                    else config.getPlcSleepTimeBetweenDoneAndReRead())

        def stillInState(what: str) -> bool:
            if self.machineState.getCurrentState() != stateAtStart:
                # aborted: the abort has reset the tags; nothing more is written for the old cycle
                printBoldRed(f"Cycle aborted before {what}; not written")
                return False
            return True

        def write(tag: str, value: bool) -> bool:
            if not self._writeBoolUntilWritten(tag, value, stateAtStart):
                self.logger.info(f"Could not write {tag}={value}; handshake of {stateAtStart.name} stopped")
                return False
            printBoldYellow(f"Wrote {value} to {tag}")
            return True

        if not stillInState(okTag) or not write(okTag, result):                                      # 1
            return False
        time.sleep(PLCIOServer.PLC_SLEEPTIME_BETWEEN_OK_AND_DONE)                                     # 2
        if checkTag and (not stillInState(checkTag) or not write(checkTag, False)):                   # 3
            return False
        if not stillInState(doneTag) or not write(doneTag, True):                                    # 4
            return False
        time.sleep(doneHold)                                                                          # 5
        if not stillInState(f"{doneTag}=False") or not write(doneTag, False):                         # 6
            return False
        if result:                                                                                    # 7 OK
            if not stillInState(f"{okTag}=False") or not write(okTag, False):
                return False
        elif checkTag and (not stillInState(f"{checkTag}=False") or not write(checkTag, False)):      # 7 NOT OK
            return False
        if (not result) and stateAtStart == MachineState.WRITE_QR_CODE and checkTag:                   # 8 (QR NOT OK)
            if not stillInState(f"{checkTag}=True") or not write(checkTag, True):
                return False
        self.logger.info(f"Handshake of {stateAtStart.name} written ({'OK' if result else 'NOT OK'}): {okTag}={result}, "
                         f"{checkTag}=False, {doneTag}=True, {doneHold} s, {doneTag}=False, "
                         f"{(okTag + '=False') if result else (str(checkTag) + '=False')}")
        return True

    def startThreads(self):
        """Start the read and connection check threads."""
        global _updateTagsProcessor

        printLight("Starting read, connection, and do threads in IO Server")

        self.launchConnectionMonitoringThread()
        printLight("Started connection monitoring thread in IOServer")

        self.launchReadLoopThread()
        printLight("Started read loop thread in IOServer")

        self.launchWriteTagsLoopThread()
        printLight("Started do loop thread in IOServer")

        self.launchEmergencyMonitoringThread()
        printLight("Started emergency monitoring thread in IOServer")

        self.launchFrontendAbortListenerThread()
        printLight("Started frontend abort listener thread in IOServer")

        self.launchMonitorStopCommandThread()
        printLight("Started monitorStop thread in IOServer")

        self.launchThreadForUpdatingCombinedHeartbeat()
        printLight("Started combinedHeartbeat update thread in IOServer")

        self.launchTagRequestListenerThread()
        printLight("Started tag request listener thread in IOServer")

        # Last: the heartbeat checks every work thread above (WorkerWatch); started before them it reported DEAD
        # ("thread not running") for the emergency monitoring and toggle threads at every start.
        self.launchHeartbeatThread()
        printLight("Started heartbeat thread in IOServer")

        # Start UpdateTagsToDefaultProcessor
        self.updateTagsProcessor = UpdateTagsToDefaultProcessor(ioServerInstance=self)
        self.updateTagsProcessor.start()
        _updateTagsProcessor = self.updateTagsProcessor  # Set global reference
        printLight("Started UpdateTagsToDefaultProcessor thread in IOServer")

        printLight("Started read tag, connection monitoring, write tag and update tag threads")

    # ==================== PLC audit trail (log.audit.plc) ====================
    # Every PC->PLC write (with its value, or the error), every PLC->PC signal the IOServer acts on (trigger raised,
    # value read) and every state change, with the cycle context [QR=... cycle=N]. Written whatever the log level.

    def _audit(self, text: str) -> None:
        try:
            if auditPLC():
                SlaveFileLogger.getInstance().logAudit(PLCIOServer.logSource, text)
        except Exception:
            noteSuppressed()

    def _auditWrite(self, tag: str, value, error: Optional[Exception] = None) -> None:
        if tag == PLCIOServer.PC_PLC_ConnectionStatusTag and error is None and not auditConnectionToggle():
            return
        if error is None:
            self._audit(f"PC->PLC {tag} = {value!r}  (state {self.machineState.getCurrentState().name})")
        else:
            self._audit(f"PC->PLC {tag} = {value!r} FAILED: {error}  (state {self.machineState.getCurrentState().name})")

    def _auditRead(self, tags, value) -> None:
        """PLC->PC: logged when a trigger is seen raised or a value is read (once per state, not every 0.2 s)."""
        if value is None or value is False or (isinstance(value, float) and value == PLCIOServer.FALSE_FLOAT_VALUE):
            self._lastAuditedRead = None
            return
        key = (self.machineState.getCurrentState(), tuple(tags or ()), value)
        if key == self._lastAuditedRead:
            return
        self._lastAuditedRead = key
        self._audit(f"PLC->PC {' & '.join(tags or ())} = {value!r}  (state {key[0].name})")

    def _auditStateChange(self, oldState, newState, seq: Optional[int] = None, published: Optional[bool] = None) -> None:
        extra = "" if seq is None else f"  (seq {seq}, {'published' if published else 'NOT published'})"
        self._audit(f"STATE {getattr(oldState, 'name', oldState)} -> {getattr(newState, 'name', newState)}{extra}")

    @staticmethod
    def _stateChangeReason(oldState, newState) -> str:
        """A short reason for the frontend's sync log (CHANGES 108), from the two states alone."""
        try:
            old, new = MachineState(int(oldState)), MachineState(int(newState))
        except Exception:
            return "IOServer start"
        if new == MachineState.READ_QR_CODE:
            return "cycle completed" if old == MachineState.READ_FREE_ROTATION_TORQUE_1 else "reset"
        if old.name.startswith("WRITE_") and int(new) == int(old) - 1:
            return "NOT OK: back to the check (retake)"
        if old.name.startswith("WRITE_"):
            return "OK: next step"
        if new.name.startswith("WRITE_"):
            return "PLC request: waiting for the answer"
        return "next step"

    def _publishState(self, oldState, newState, reason: Optional[str] = None) -> Tuple[int, bool]:
        """State sync (CHANGES 108): the state change on io2festate.q and in io:state, for the frontend to compare
        with its own state. Short Redis timeouts: a state change must never hang on Redis. The sequence number goes up
        even when the send fails, so the frontend sees the gap ("STATE-SYNC GAP"); a failure is logged once per
        outage."""
        self._stateSeq += 1
        snapshot = {"pid": os.getpid(), "seq": self._stateSeq, "cycleId": self.cycleId,
                    "qr": getattr(self, "currentQRCode", "") or "",
                    "old": None if oldState is None else int(oldState),
                    "oldName": getattr(oldState, "name", None), "new": int(newState),
                    "newName": getattr(newState, "name", str(newState)),
                    "reason": reason or self._stateChangeReason(oldState, newState), "time": time.time()}
        published = False
        try:
            if self._stateRedisConnection is None:
                self._stateRedisConnection = Redis(redisHostname, redisPort, socket_timeout=1.0,
                                                   socket_connect_timeout=1.0, retry_on_timeout=True)
            published = publishIOServerState(self._stateRedisConnection, snapshot)
        except Exception:
            noteSuppressed()
        if not published:
            self._stateRedisConnection = None
            if not self._statePublishFailing:
                self._statePublishFailing = True
                try:
                    self.logger.error(f"STATE-SYNC IOServer could not publish state {snapshot['newName']} "
                                      f"(seq {self._stateSeq}): Redis not reachable")
                except Exception:
                    noteSuppressed()
        elif self._statePublishFailing:
            self._statePublishFailing = False
            try:
                self.logger.info(f"STATE-SYNC IOServer publishes states again (seq {self._stateSeq})")
            except Exception:
                noteSuppressed()
        return self._stateSeq, published

    def _onStateChange(self, oldState, newState, reason: Optional[str] = None) -> None:
        """Every state change: audit trail; and when the machine goes back to READ_QR_CODE the cycle is over -
        completed (from the last state) or aborted (emergency button, heartbeat loss, frontend abort, from any other
        state) - so the camera server is told (end-of-cycle message on its queue, sent after an abort has cleared the
        queues) and drops what it kept for the cycle: the top bearing profile for the nut / washer check."""
        seq, published = self._publishState(oldState, newState, reason)
        self._auditStateChange(oldState, newState, seq, published)
        self._writeStateSince = time.monotonic()     # when the current state began (camera answer timeout)
        if newState == MachineState.READ_QR_CODE and oldState is not None and oldState != MachineState.READ_QR_CODE:
            completed = oldState == MachineState.READ_FREE_ROTATION_TORQUE_1
            how = "completed" if completed else f"aborted in {getattr(oldState, 'name', oldState)}"
            sent = False
            try:
                if self._cycleEndRedisConnection is None:      # short timeouts: a state change must never hang on Redis
                    self._cycleEndRedisConnection = Redis(redisHostname, redisPort, socket_timeout=1.0,
                                                          socket_connect_timeout=1.0, retry_on_timeout=True)
                sent = bool(sendEndOfCycleFromIOServerToCameraServer(self._cycleEndRedisConnection, aborted=not completed))
            except Exception:
                noteSuppressed()
            self._audit(f"End of cycle ({how}) {'sent' if sent else 'NOT sent (Redis not reachable)'} to the camera server")
            if not sent:
                try:
                    self.logger.error(f"Could not send the end of cycle ({how}) to the camera server")
                except Exception:
                    noteSuppressed()

    def _applyLiveConfiguration(self, changedKeys) -> None:
        """Properties that the IOServer caches, re-applied when ApplicationConfiguration.properties changes."""
        config = CosThetaConfigurator.getInstance()
        applied = []
        try:
            for attr, getter in (("read_interval", config.getPlcReadInterval),
                                 ("write_loop_interval", config.getPlcWriteInterval),
                                 ("CONNECTION_CHECK_INTERVAL", config.getPlcConnectionCheckInterval)):
                value = getter()
                if value != getattr(self, attr):
                    setattr(self, attr, value); applied.append(f"{attr}={value}")
            value = config.getPlcSleepTimeBetweenOKAndDone()
            if value != PLCIOServer.PLC_SLEEPTIME_BETWEEN_OK_AND_DONE:
                PLCIOServer.PLC_SLEEPTIME_BETWEEN_OK_AND_DONE = value; applied.append(f"PLC_SLEEPTIME_BETWEEN_OK_AND_DONE={value}")
            value = config.getLogDisconnectionsAfterNSecs()
            if value != PLCIOServer.LOG_CONNECTION_PROBLEM_EVERY_N_SECS:
                PLCIOServer.LOG_CONNECTION_PROBLEM_EVERY_N_SECS = value; applied.append(f"LOG_CONNECTION_PROBLEM_EVERY_N_SECS={value}")
        except Exception as e:
            self.logger.error(f"Could not apply the changed configuration: {e}")
        tagKeys = [k for k in changedKeys if k.endswith(".tagname") or k in ("plc.ip", "siemens.plc.ip", "plc.vendor")]
        if applied:
            self.logger.warning(f"Configuration applied while running: {', '.join(applied)}")
        if tagKeys:
            self.logger.critical(f"PLC tag names / IP / vendor changed in the properties file ({', '.join(tagKeys)}): "
                                 f"these take effect only after the IOServer is restarted")

    # ==================== Read functions ====================

    def read_bool(self, tag: str, expected_value: bool = True, debug: bool = False) -> Optional[bool]:
        """Read a boolean tag from the PLC. Not connected: False; read failed: not expected_value (use
        _readBoolOrNone to tell a failure from a value)."""
        if tag is None or tag == "":
            return False
        if not self._readsReady():
            return False
        try:
            value = self.dataIOForReads.read(tag)
            if debug and value == expected_value:
                self.logger.info("Read bool tag %s: %s", tag, value)
            return value
        except Exception as e:
            self.logger.error("Error reading bool tag %s: %s", tag, e)
            printBoldRed(f"Error reading bool tag {tag}: {e}")
            return not expected_value

    def read_float(self, tag: str, debug: bool = False) -> Optional[float]:
        """Read a float tag from the PLC (FALSE_FLOAT_VALUE if it cannot be read)."""
        if tag is None or tag == "":
            return PLCIOServer.FALSE_FLOAT_VALUE
        if not self._readsReady():
            return PLCIOServer.FALSE_FLOAT_VALUE
        try:
            return self.dataIOForReads.read(tag)
        except Exception as e:
            printBoldRed(f"Error reading float tag {tag}: {e}")
            return PLCIOServer.FALSE_FLOAT_VALUE

    def read_int(self, tag: str, debug: bool = False) -> Optional[int]:
        """Read an integer tag from the PLC (FALSE_INT_VALUE if it cannot be read)."""
        if tag is None or tag == "":
            return PLCIOServer.FALSE_INT_VALUE
        if not self._readsReady():
            return PLCIOServer.FALSE_INT_VALUE
        try:
            value = self.dataIOForReads.read(tag)
            if debug:
                self.logger.info("Read integer tag %s: %d", tag, value)
                printBoldGreen(f"Read integer tag {tag}: {value}")
            return int(value)
        except Exception as e:
            self.logger.error("Error reading integer tag %s: %s", tag, e)
            printBoldRed(f"Error reading integer tag {tag}: {e}")
            return PLCIOServer.FALSE_INT_VALUE

    def read_single(self, aTag: str) -> Any:
        """Read one tag of any type ("" if it cannot be read)."""
        if aTag is None or aTag == "":
            return PLCIOServer.FALSE_FLOAT_VALUE
        if not self._readsReady():
            return PLCIOServer.FALSE_FLOAT_VALUE
        value = ""
        try:
            value = self.dataIOForReads.read(aTag)
        except Exception:
            noteSuppressed()
        return value

    def read_multiple(self, listOfTags: list) -> Any:
        """Read several tags: {tag: value} ({} if any cannot be read)."""
        if listOfTags is None or len(listOfTags) == 0:
            return None
        if not self._readsReady():
            return None
        value = {}
        try:
            value = self.dataIOForReads.readMany(listOfTags)
        except Exception:
            noteSuppressed()
        return value

    def read_array(self, arrayName: str, numberOfElements: int) -> Any:
        """The first numberOfElements elements of an array tag (PLCs whose PLCDataIO has readArray: Allen-Bradley)."""
        if numberOfElements == 0:
            return None
        if not self._readsReady():
            return None
        if not hasattr(self.dataIOForReads, "readArray"):
            printBoldRed(f"read_array: not available on {self.dataIOForReads.describe()}")
            return None
        value = []
        try:
            value = self.dataIOForReads.readArray(arrayName, numberOfElements)
        except Exception:
            noteSuppressed()
        return value

    def read_array_slice(self, arrayName: str, index: int, numberOfElements: int) -> Any:
        """numberOfElements elements of an array tag from index (Allen-Bradley)."""
        value = []
        if not self._readsReady() or not hasattr(self.dataIOForReads, "readArraySlice"):
            return value
        try:
            value = self.dataIOForReads.readArraySlice(arrayName, index, numberOfElements)
        except Exception:
            noteSuppressed()
        return value

    # ==================== Write functions ====================

    def write_bool(self, tag: str, value: bool, debug: bool = True) -> bool:
        """Write a boolean to a tag to the PLC."""
        if tag is None:
            return False
        if self._writesBlocked():
            return False
        if not self._writesReady():
            return False
        try:
            self.dataIOForWrites.write(tag, value)
            self._auditWrite(tag, value)
            self._notePcWrite(tag, bool(value))
            if debug:
                printBoldYellow(f"Wrote to bool tag {tag}: {value} in write_bool()")
            return True
        except Exception as e:
            self._auditWrite(tag, value, error=e)
            printBoldRed(f"Error writing bool tag {tag}: {e}")
            return False

    def write_float(self, tag: str, value: float, debug: bool = True) -> bool:
        """Write a float to a tag to the PLC."""
        if tag is None:
            return False
        if self._writesBlocked():
            return False
        if not self._writesReady():
            return False
        try:
            self.dataIOForWrites.write(tag, value)
            self._auditWrite(tag, value)
            if debug:
                self.logger.info(f"Wrote float tag {tag}: {value}")
                printBoldYellow(f"Wrote to float tag {tag}: {value} in write_float()")
            return True
        except Exception as e:
            self._auditWrite(tag, value, error=e)
            self.logger.error(f"Error writing float tag {tag}: {e}")
            printBoldRed(f"Error writing float tag {tag}: {e}")
            return False

    def write_int(self, tag: str, value: int, debug: bool = False) -> bool:
        """Write an integer to a tag to the PLC."""
        if tag is None or not isinstance(value, int):
            if debug:
                self.logger.error(f"Invalid input for tag {tag}: value {value} must be an integer")
                printBoldRed(f"Invalid input for tag {tag}: value {value} must be an integer")
            return False
        if self._writesBlocked():
            return False
        if not self._writesReady():
            return False
        try:
            self.dataIOForWrites.write(tag, value)
            self._auditWrite(tag, value)
            if debug:
                self.logger.info(f"Wrote integer tag {tag}: {value}")
                printBoldGreen(f"Wrote integer tag {tag}: {value}")
            return True
        except Exception as e:
            self._auditWrite(tag, value, error=e)
            self.logger.error(f"Error writing integer tag {tag}: {e}")
            printBoldRed(f"Error writing integer tag {tag}: {e}")
            return False

    def resetTagToFalse(self, tag: str, debug: bool = False) -> bool:
        """Reset a tag to False."""
        return self.write_bool(tag=tag, value=False, debug=debug)

    def resetTagToZero(self, tag: str, debug: bool = False) -> bool:
        """Reset a tag to 0.0."""
        return self.write_float(tag=tag, value=0.0, debug=debug)

    def perCycleTags(self) -> List[Tuple[str, type]]:
        """Every per-cycle handshake tag with its type (bool -> False, float -> 0.0), in reset order. NOT included:
        the state-2 cycle settings (written fresh in every cycle), the PLC's watch tag and the PC's connection-status
        toggle. PLC_PC_CheckQRCode is included (set False here; resetForNextCycle() then arms it)."""
        tags = [
            (PLCIOServer.PLC_PC_CheckQRCode, bool), (PLCIOServer.PC_PLC_QRCodeCheckOK, bool), (PLCIOServer.PC_PLC_QRCodeCheckDone, bool),
            (PLCIOServer.PLC_PC_CheckKnuckle, bool), (PLCIOServer.PC_PLC_KnuckleCheckOK, bool), (PLCIOServer.PC_PLC_KnuckleCheckDone, bool),
            (PLCIOServer.PLC_PC_CheckHub, bool), (PLCIOServer.PC_PLC_HubCheckOK, bool), (PLCIOServer.PC_PLC_HubCheckDone, bool),
            (PLCIOServer.PLC_PC_CheckHubAndSecondBearing, bool), (PLCIOServer.PC_PLC_HubAndSecondBearingCheckOK, bool),
            (PLCIOServer.PC_PLC_HubAndSecondBearingCheckDone, bool),
            (PLCIOServer.PLC_PC_CheckNutAndPlateWasher, bool), (PLCIOServer.PC_PLC_NutAndPlateWasherCheckOK, bool),
            (PLCIOServer.PC_PLC_NutAndPlateWasherCheckDone, bool),
            (PLCIOServer.PLC_PC_Station2TorqueValueSet, bool), (PLCIOServer.PLC_PC_Station2TorqueValue, float),
            (PLCIOServer.PLC_PC_Station2RotationDuringTighteningDone, bool),
            (PLCIOServer.PLC_PC_CheckNoCapBunk, bool), (PLCIOServer.PC_PLC_NoCapBunkCheckOK, bool), (PLCIOServer.PC_PLC_NoCapBunkCheckDone, bool),
            (PLCIOServer.PLC_PC_ComponentPressDone, bool),
            (PLCIOServer.PLC_PC_CheckNoBunk, bool), (PLCIOServer.PC_PLC_NoBunkCheckOK, bool), (PLCIOServer.PC_PLC_NoBunkCheckDone, bool),
            (PLCIOServer.PLC_PC_CheckSplitPinAndWasher, bool), (PLCIOServer.PC_PLC_SplitPinAndWasherCheckOK, bool),
            (PLCIOServer.PC_PLC_SplitPinAndWasherCheckDone, bool),
            (PLCIOServer.PLC_PC_CheckCap, bool), (PLCIOServer.PC_PLC_CapCheckOK, bool), (PLCIOServer.PC_PLC_CapCheckDone, bool),
            (PLCIOServer.PLC_PC_CheckBunk, bool), (PLCIOServer.PC_PLC_BunkCheckOK, bool), (PLCIOServer.PC_PLC_BunkCheckDone, bool),
            (PLCIOServer.PLC_PC_CapPressDone, bool),
            (PLCIOServer.PLC_PC_Station3TorqueValueSet, bool), (PLCIOServer.PLC_PC_Station3TorqueValue, float),
            (PLCIOServer.PLC_PC_EmergencyAbort, bool),
        ]
        return [(t, k) for t, k in tags if t]          # an empty name = not on this PLC

    def resetAll(self, debug: bool = False, skip: Optional[set] = None) -> bool:
        """Set every per-cycle tag to its default (bool False, float 0.0), each written and read back
        (_writeVerified). Stops at the first tag that cannot be written because the PLC is not connected.
        Tags in skip are left to a reset that is still pending for them. Returns True only if every tag was written
        and verified; the tags that were not are logged."""
        self.currentQRCode = ""
        failed: List[str] = []
        for i, (tag, kind) in enumerate(self.perCycleTags()):
            if skip and tag in skip:
                continue
            if not self._writeVerified(tag, False if kind is bool else 0.0):
                failed.append(tag)
                if not self._plcConnected():
                    failed.extend(t for t, _ in self.perCycleTags()[i + 1:])
                    break
        if failed:
            message = f"Reset to defaults: {len(failed)} tag(s) not written / verified: {', '.join(failed)}"
            printBoldRed(message)
            self.logger.error(message)
            self._audit(message)
        return not failed

    def resetForNextCycle(self, reason: str, keepPendingResets: bool = False) -> bool:
        """The one routine for the end of a cycle, every abort and the IOServer start: cancel the pending handshake
        resets, set every per-cycle tag to its default (resetAll, written and read back), then arm the QR code read
        (PLC_PC_CheckQRCode = True, written and read back). The 6 state-2 settings are left as they are.
        keepPendingResets (a completed cycle): the pending handshake resets are not cancelled but run at their time
        (rules (a)-(d)), and resetAll leaves their tags to them; one that would touch CheckQRCode is done first.
        If anything could not be written and verified, the reset is marked incomplete and done again as soon as the
        PLC connection is back (_verifyPlcAfterReconnect). Returns True if everything was written and verified."""
        with self._asPrivileged():
            pendingTags = set()
            if keepPendingResets:
                for info in self._resultResetScheduler.pendingInfos():
                    pendingTags.update((info or {}).get("tags", []))
                if PLCIOServer.PLC_PC_CheckQRCode in pendingTags:     # must not undo the arming below
                    self._resultResetScheduler.runNow(PLCIOServer.PC_PLC_QRCodeCheckOK)
                    pendingTags.discard(PLCIOServer.PLC_PC_CheckQRCode)
            else:
                self._resultResetScheduler.cancelAll()
            with self._awaitingTriggerResetLock:
                self._awaitingTriggerReset.clear()
                self._awaitingTriggerSince.clear()
            self._sideWrittenThisCycle = None                 # written again in state 2 of the next cycle
            allReset = self.resetAll(skip=pendingTags)
            qrArmed = self._plcConnected() and self._writeVerified(PLCIOServer.PLC_PC_CheckQRCode, True)
            with self._plcTrueLock:
                self._plcTrueTags = [PLCIOServer.PLC_PC_CheckQRCode] if qrArmed else []
            self._resetIncomplete = not (allReset and qrArmed)
        if self._resetIncomplete:
            message = (f"Reset for the next cycle ({reason}) INCOMPLETE: tags reset {'yes' if allReset else 'NO'}, "
                       f"CheckQRCode armed {'yes' if qrArmed else 'NO'}; done again when the PLC connection is back")
            printBoldRed(message)
            self.logger.error(message)
        else:
            message = f"Reset for the next cycle ({reason}): all per-cycle tags at their defaults, CheckQRCode armed"
            printBoldGreen(message)
            self.logger.info(message)
        self._audit(message)
        return not self._resetIncomplete

    # ---------------------------------------------------------------- verified writes, PLC restart detection
    def _plcConnected(self) -> bool:
        return self._readsReady() and self._writesReady()

    @contextlib.contextmanager
    def _asPrivileged(self):
        """Writes from this thread go through even while _plcCheckPending (the check after a reconnect, and the
        reset or abort it starts)."""
        previous = getattr(self._privileged, "on", False)
        self._privileged.on = True
        try:
            yield
        finally:
            self._privileged.on = previous

    def _writesBlocked(self) -> bool:
        return bool(getattr(self, "_plcCheckPending", False)) and not getattr(self._privileged, "on", False)

    def _readIntOrNone(self, tag: str) -> Optional[int]:
        """Read an integer from the PLC; None if it could not be read."""
        if not tag or not self._readsReady():
            return None
        try:
            return int(self.dataIOForReads.read(tag))
        except Exception as e:
            printBoldRed(f"Could not read {tag}: {e}")
            return None

    def _readFloatOrNone(self, tag: str) -> Optional[float]:
        """Read a float from the PLC; None if it could not be read."""
        if not tag or not self._readsReady():
            return None
        try:
            return float(self.dataIOForReads.read(tag))
        except Exception as e:
            printBoldRed(f"Could not read {tag}: {e}")
            return None

    def _writeVerified(self, tag: str, value, attempts: int = None) -> bool:
        """Write a bool or float and read it back, until the PLC holds the value (up to RESET_WRITE_ATTEMPTS).
        Gives up at once if the PLC is not connected (the caller marks the work as incomplete)."""
        attempts = PLCIOServer.RESET_WRITE_ATTEMPTS if attempts is None else attempts
        isBool = isinstance(value, bool)
        lastSeen = None
        for attempt in range(attempts):
            if self.shutdown or not self._plcConnected():
                return False
            written = self.write_bool(tag, value, debug=False) if isBool else self.write_float(tag, float(value), debug=False)
            if written:
                lastSeen = self._readBoolOrNone(tag) if isBool else self._readFloatOrNone(tag)
                if lastSeen is not None and (lastSeen == value if isBool
                                             else abs(lastSeen - float(value)) <= PLCIOServer.RESET_FLOAT_TOLERANCE):
                    return True
            time.sleep(PLCIOServer.WRITE_RETRY_INTERVAL)
        printBoldRed(f"{tag}: wrote {value} {attempts} times, PLC holds {lastSeen}")
        return False

    def _notePlcTagTrue(self, tags) -> None:
        """The read loop saw these PLC_PC bools True (raised by the PLC): the newest becomes the reference."""
        with self._plcTrueLock:
            for tag in tags:
                if tag in self._plcPcTagNames():
                    if tag in self._plcTrueTags:
                        self._plcTrueTags.remove(tag)
                    self._plcTrueTags.append(tag)

    def _notePcWrite(self, tag: str, value: bool) -> None:
        """The PC wrote a PLC_PC bool: False removes it from the tags known to be True; True (arming CheckQRCode)
        adds it."""
        if tag not in self._plcPcTagNames():
            return
        with self._plcTrueLock:
            if tag in self._plcTrueTags:
                self._plcTrueTags.remove(tag)
            if value:
                self._plcTrueTags.append(tag)

    def _plcPcTagNames(self) -> set:
        names = getattr(self, "_plcPcNamesCache", None)
        if names is None:
            names = {getattr(PLCIOServer, a) for a in dir(PLCIOServer)
                     if a.startswith("PLC_PC_") and isinstance(getattr(PLCIOServer, a), str) and getattr(PLCIOServer, a)}
            names.discard(PLCIOServer.PLC_PC_WatchTag)
            self._plcPcNamesCache = names
        return names

    def referenceTag(self) -> Optional[str]:
        with self._plcTrueLock:
            return self._plcTrueTags[-1] if self._plcTrueTags else None

    def _followReferenceTag(self) -> None:
        """While connected (every connection check): if the PLC itself has set the reference tag back to False, it
        is no longer a reference; the one before it becomes the reference. Done only while connected, so that a
        tag found False AFTER a reconnect means the PLC restarted."""
        for _ in range(4):
            tag = self.referenceTag()
            if tag is None:
                return
            value = self._readBoolOrNone(tag)
            if value is not False:                 # True, or not readable now (the connection check handles that)
                return
            with self._plcTrueLock:
                if self._plcTrueTags and self._plcTrueTags[-1] == tag:
                    self._plcTrueTags.pop()
            self._audit(f"PLC set {tag} back to False itself; reference now {self.referenceTag()}")

    def _verifyPlcAfterReconnect(self) -> None:
        """After the PLC connection is back: did the PLC restart while it was lost?
        The last PLC_PC tag known to be True (the reference) is read. Still True -> the PLC kept its state: the
        cycle carries on in the same machine state. No tag known to be True during a cycle (between steps the PC has
        cleared every trigger with the result handshake): the fallback reference is PC_PLC_LH_RH_Selection, which the
        PC wrote in state 2 (1 / 2) and a PLC restart sets back to its start value (DB101 must not be retentive) - the
        value written -> the PLC kept its state. Otherwise (reference False, side changed, or nothing to check) -> the
        PLC restarted:
        a cycle in progress is aborted (QR code server, camera and frontend told, as for an emergency abort), every
        per-cycle tag is reset and CheckQRCode armed. A reset left incomplete when the connection was lost is done
        now. Until this check is done, only it can write to the PLC and the read loop does nothing."""
        with self._asPrivileged():
            state = self.machineState.getCurrentState()
            reference = self.referenceTag()
            if self._resetIncomplete and state in (MachineState.READ_QR_CODE, MachineState.WRITE_QR_CODE):
                # no cycle under way yet (an incomplete reset leaves the machine at the QR code); once a cycle has
                # started, the reference tag decides (below), so a running cycle is never reset without an abort
                self._audit("PLC connection back: completing the reset that was left incomplete")
                if self.resetForNextCycle("completing an incomplete reset after the PLC connection came back"):
                    self._plcCheckPending = False
                return
            value = None
            if reference is not None:
                for _ in range(PLCIOServer.REFERENCE_READ_ATTEMPTS):
                    value = self._readBoolOrNone(reference)
                    if value is not None:
                        break
                    time.sleep(PLCIOServer.WRITE_RETRY_INTERVAL)
                if value is None:
                    printBoldRed(f"PLC connection back, but {reference} cannot be read yet; checking again")
                    return                           # still pending; the connection check calls this again
            side = None
            if (reference is None and self._sideWrittenThisCycle is not None and PLCIOServer.PC_PLC_LH_RH_Selection
                    and state not in (MachineState.READ_QR_CODE, MachineState.WRITE_QR_CODE)):
                for _ in range(PLCIOServer.REFERENCE_READ_ATTEMPTS):
                    side = self._readIntOrNone(PLCIOServer.PC_PLC_LH_RH_Selection)
                    if side is not None:
                        break
                    time.sleep(PLCIOServer.WRITE_RETRY_INTERVAL)
                if side is None:
                    printBoldRed(f"PLC connection back, but {PLCIOServer.PC_PLC_LH_RH_Selection} cannot be read yet; "
                                 f"checking again")
                    return                           # still pending; the connection check calls this again
                if side == self._sideWrittenThisCycle:
                    reference, value = PLCIOServer.PC_PLC_LH_RH_Selection, True
            if value is True:
                message = (f"PLC connection back: {reference} is still "
                           f"{'True' if reference != PLCIOServer.PC_PLC_LH_RH_Selection else self._sideWrittenThisCycle}, "
                           f"so the PLC did not restart; carrying on in state {state.name}")
                printBoldGreen(message)
                self.logger.info(message)
                self._audit(message)
                self._plcCheckPending = False
                return
            self.plcRestartsDetected += 1
            why = (f"{reference} reads False" if reference is not None else
                   (f"{PLCIOServer.PC_PLC_LH_RH_Selection} reads {side}, not {self._sideWrittenThisCycle}" if side is not None
                    else "no PLC tag was known to be True"))
            reason = f"PLC restarted while the connection was lost ({why})"
            printBoldRed(f"{reason}; machine state was {state.name}")
            self.logger.error(f"{reason}; machine state was {state.name}")
            if state in (MachineState.READ_QR_CODE,):
                self.resetForNextCycle(reason)       # no cycle in progress: nothing to abort
            else:
                self.abortCycle(reason, pcAbort=True)   # QR server, queues, frontend, camera (end of cycle), reset
            if not self._resetIncomplete:
                self._plcCheckPending = False

    def _readStation2RotationDuringTighteningDone(self) -> Optional[bool]:
        """Read PLC_PC_Station2RotationDuringTighteningDone, which the PLC provides with the Station 2 torque value.
        Sent to the frontend with each torque value (shown as Rotation Done / Not Done, stored with the record).
        Returns None if this PLC does not provide it (empty tag name, Allen-Bradley)."""
        if not PLCIOServer.PLC_PC_Station2RotationDuringTighteningDone:
            return None
        value = self.read_bool(PLCIOServer.PLC_PC_Station2RotationDuringTighteningDone, debug=False)
        self.lastStation2RotationDuringTighteningDone = value
        printBoldGreen(f"Read Station 2 rotation-during-tightening done: {value}")
        return value

    def getCurrentQRCode(self) -> str:
        """Get the current QR code for this cycle."""
        return self.currentQRCode

    def get_tag_address(self, tag: str) -> Optional[str]:
        """The tag's address on the PLC where it has one apart from its name (Siemens DB address); else None."""
        return self.dataIOForReads.addressFor(tag) if self.dataIOForReads is not None else None

    def find_pids(self) -> list:
        """The PLC's PID tags (Allen-Bradley); [] where not applicable."""
        findPIDs = getattr(self.dataIOForReads, "findPIDs", None)
        return findPIDs() if (findPIDs and self._readsReady()) else []

    def find_attributes(self) -> None:
        """Print what the PLC defines: Allen-Bradley data types and their attributes; Siemens tag map."""
        for method in ("printDataTypeAttributes", "printTagMap"):
            if hasattr(self.dataIOForReads, method):
                getattr(self.dataIOForReads, method)()
                return

    def getAllTagValues(self) -> dict:
        """Read all PLC tags and return as a dictionary."""
        if not self._readsReady():
            return {}

        tag_values = {}

        # Step 0: QR Code
        tag_values[PLCIOServer.PLC_PC_CheckQRCode] = self.read_bool(PLCIOServer.PLC_PC_CheckQRCode, debug=False)
        tag_values[PLCIOServer.PC_PLC_QRCodeCheckOK] = self.read_bool(PLCIOServer.PC_PLC_QRCodeCheckOK, debug=False)
        tag_values[PLCIOServer.PC_PLC_QRCodeCheckDone] = self.read_bool(PLCIOServer.PC_PLC_QRCodeCheckDone, debug=False)

        # Step 1: Knuckle
        tag_values[PLCIOServer.PLC_PC_CheckKnuckle] = self.read_bool(PLCIOServer.PLC_PC_CheckKnuckle, debug=False)
        tag_values[PLCIOServer.PC_PLC_KnuckleCheckOK] = self.read_bool(PLCIOServer.PC_PLC_KnuckleCheckOK, debug=False)
        tag_values[PLCIOServer.PC_PLC_KnuckleCheckDone] = self.read_bool(PLCIOServer.PC_PLC_KnuckleCheckDone, debug=False)

        # Step 2: Hub
        tag_values[PLCIOServer.PLC_PC_CheckHub] = self.read_bool(PLCIOServer.PLC_PC_CheckHub, debug=False)
        tag_values[PLCIOServer.PC_PLC_HubCheckOK] = self.read_bool(PLCIOServer.PC_PLC_HubCheckOK, debug=False)
        tag_values[PLCIOServer.PC_PLC_HubCheckDone] = self.read_bool(PLCIOServer.PC_PLC_HubCheckDone, debug=False)

        # Step 3: Hub and Second Bearing
        tag_values[PLCIOServer.PLC_PC_CheckHubAndSecondBearing] = self.read_bool(PLCIOServer.PLC_PC_CheckHubAndSecondBearing,
                                                                              debug=False)
        tag_values[PLCIOServer.PC_PLC_HubAndSecondBearingCheckOK] = self.read_bool(
            PLCIOServer.PC_PLC_HubAndSecondBearingCheckOK, debug=False)
        tag_values[PLCIOServer.PC_PLC_HubAndSecondBearingCheckDone] = self.read_bool(
            PLCIOServer.PC_PLC_HubAndSecondBearingCheckDone, debug=False)

        # Step 4: Nut and Plate Washer
        tag_values[PLCIOServer.PLC_PC_CheckNutAndPlateWasher] = self.read_bool(PLCIOServer.PLC_PC_CheckNutAndPlateWasher,
                                                                            debug=False)
        tag_values[PLCIOServer.PC_PLC_NutAndPlateWasherCheckOK] = self.read_bool(PLCIOServer.PC_PLC_NutAndPlateWasherCheckOK,
                                                                              debug=False)
        tag_values[PLCIOServer.PC_PLC_NutAndPlateWasherCheckDone] = self.read_bool(
            PLCIOServer.PC_PLC_NutAndPlateWasherCheckDone, debug=False)

        # Step 5 & 10: Station 2 Torque
        tag_values[PLCIOServer.PLC_PC_Station2TorqueValueSet] = self.read_bool(PLCIOServer.PLC_PC_Station2TorqueValueSet,
                                                                            debug=False)
        tag_values[PLCIOServer.PLC_PC_Station2TorqueValue] = self.read_float(PLCIOServer.PLC_PC_Station2TorqueValue,
                                                                          debug=False)
        if PLCIOServer.PLC_PC_Station2RotationDuringTighteningDone:
            tag_values[PLCIOServer.PLC_PC_Station2RotationDuringTighteningDone] = self.read_bool(
                PLCIOServer.PLC_PC_Station2RotationDuringTighteningDone, debug=False)

        # Step 7: No Cap Bunk
        tag_values[PLCIOServer.PLC_PC_CheckNoCapBunk] = self.read_bool(PLCIOServer.PLC_PC_CheckNoCapBunk, debug=False)
        tag_values[PLCIOServer.PC_PLC_NoCapBunkCheckOK] = self.read_bool(PLCIOServer.PC_PLC_NoCapBunkCheckOK, debug=False)
        tag_values[PLCIOServer.PC_PLC_NoCapBunkCheckDone] = self.read_bool(PLCIOServer.PC_PLC_NoCapBunkCheckDone, debug=False)

        # Step 8: Component Press Done
        tag_values[PLCIOServer.PLC_PC_ComponentPressDone] = self.read_bool(PLCIOServer.PLC_PC_ComponentPressDone, debug=False)

        # Step 9: No Bunk
        tag_values[PLCIOServer.PLC_PC_CheckNoBunk] = self.read_bool(PLCIOServer.PLC_PC_CheckNoBunk, debug=False)
        tag_values[PLCIOServer.PC_PLC_NoBunkCheckOK] = self.read_bool(PLCIOServer.PC_PLC_NoBunkCheckOK, debug=False)
        tag_values[PLCIOServer.PC_PLC_NoBunkCheckDone] = self.read_bool(PLCIOServer.PC_PLC_NoBunkCheckDone, debug=False)

        # Step 11: Split Pin and Washer
        tag_values[PLCIOServer.PLC_PC_CheckSplitPinAndWasher] = self.read_bool(PLCIOServer.PLC_PC_CheckSplitPinAndWasher,
                                                                            debug=False)
        tag_values[PLCIOServer.PC_PLC_SplitPinAndWasherCheckOK] = self.read_bool(PLCIOServer.PC_PLC_SplitPinAndWasherCheckOK,
                                                                              debug=False)
        tag_values[PLCIOServer.PC_PLC_SplitPinAndWasherCheckDone] = self.read_bool(
            PLCIOServer.PC_PLC_SplitPinAndWasherCheckDone, debug=False)

        # Step 12: Cap
        tag_values[PLCIOServer.PLC_PC_CheckCap] = self.read_bool(PLCIOServer.PLC_PC_CheckCap, debug=False)
        tag_values[PLCIOServer.PC_PLC_CapCheckOK] = self.read_bool(PLCIOServer.PC_PLC_CapCheckOK, debug=False)
        tag_values[PLCIOServer.PC_PLC_CapCheckDone] = self.read_bool(PLCIOServer.PC_PLC_CapCheckDone, debug=False)

        # Step 13: Bunk
        tag_values[PLCIOServer.PLC_PC_CheckBunk] = self.read_bool(PLCIOServer.PLC_PC_CheckBunk, debug=False)
        tag_values[PLCIOServer.PC_PLC_BunkCheckOK] = self.read_bool(PLCIOServer.PC_PLC_BunkCheckOK, debug=False)
        tag_values[PLCIOServer.PC_PLC_BunkCheckDone] = self.read_bool(PLCIOServer.PC_PLC_BunkCheckDone, debug=False)

        # Step 14: Cap Press Done
        tag_values[PLCIOServer.PLC_PC_CapPressDone] = self.read_bool(PLCIOServer.PLC_PC_CapPressDone, debug=False)

        # Step 15: Station 3 Torque
        tag_values[PLCIOServer.PLC_PC_Station3TorqueValueSet] = self.read_bool(PLCIOServer.PLC_PC_Station3TorqueValueSet,
                                                                            debug=False)
        tag_values[PLCIOServer.PLC_PC_Station3TorqueValue] = self.read_float(PLCIOServer.PLC_PC_Station3TorqueValue,
                                                                          debug=False)

        # System tags
        tag_values[PLCIOServer.PLC_PC_EmergencyAbort] = self.read_bool(PLCIOServer.PLC_PC_EmergencyAbort, debug=False)
        tag_values[PLCIOServer.PLC_PC_WatchTag] = self.read_int(PLCIOServer.PLC_PC_WatchTag, debug=False)

        # Machine 2 cycle settings written by the PC in state 2
        for tag, reader in ((PLCIOServer.PC_PLC_LH_RH_Selection, self.read_int),
                            (PLCIOServer.PC_PLC_ModelSelection, self.read_int),
                            (PLCIOServer.PC_PLC_TighteningTorque1Setpoint, self.read_float),
                            (PLCIOServer.PC_PLC_TighteningTorque2Setpoint, self.read_float),
                            (PLCIOServer.PC_PLC_RotationRPM1, self.read_int),
                            (PLCIOServer.PC_PLC_RotationRPM2, self.read_int),
                            (PLCIOServer.PC_PLC_RotationRPM, self.read_float),
                            (PLCIOServer.PC_PLC_NoOfRotation1CCW, self.read_float),
                            (PLCIOServer.PC_PLC_NoOfRotation1CW, self.read_float)):
            if tag:                      # empty name = not on this PLC
                tag_values[tag] = reader(tag, debug=False)
        tag_values[PLCIOServer.PC_PLC_ConnectionStatusTag] = self.read_bool(PLCIOServer.PC_PLC_ConnectionStatusTag, debug=False)
        for tag in (PLCIOServer.PC_PLC_CycleAborted, PLCIOServer.PC_PLC_Skip_Second_Tightening):
            if tag:                              # optional: None where the PLC has no address for it
                tag_values[tag] = self.read_bool(tag, debug=False) if self._optionalTagWritable(tag) else None

        # Add current machine state (enum name: str(IntEnum) is just "1" on Python 3.11+)
        tag_values["_currentMachineState"] = self.machineState.getCurrentState().name

        return tag_values

    def launchTagRequestListenerThread(self):
        """Launch thread that listens for tag publish requests on Redis queue."""
        objRef = self

        PUBLISH_TAGS_QUEUE = "publishTags"
        RECEIVE_TAGS_QUEUE = "receiveTags"

        def listenForTagRequests():
            while not objRef.shutdown:
                try:
                    # Check for request in publishTags queue (blocking with 1 sec timeout)
                    result = objRef.redisConnectionForReads.blpop(PUBLISH_TAGS_QUEUE, timeout=1)

                    if result:
                        # Got a request - clear any remaining messages in the queue
                        while objRef.redisConnectionForReads.lpop(PUBLISH_TAGS_QUEUE):
                            pass

                        # Fetch all tag values
                        tag_values = objRef.getAllTagValues()

                        # Put response in receiveTags queue
                        objRef.redisConnectionForReads.rpush(RECEIVE_TAGS_QUEUE, json.dumps(tag_values))

                except Exception:
                    noteSuppressed()  # Silently ignore errors to not disrupt main operation

        self.tagRequestThread = threading.Thread(name='Tag Request Listener Thread',
                                                 target=listenForTagRequests,
                                                 args=(), daemon=True)
        self.tagRequestThread.start()

    def __del__(self):
        """Cleanup on object destruction."""
        self.shutdown = True
        # Stop the UpdateTagsToDefaultProcessor
        if hasattr(self, 'updateTagsProcessor') and self.updateTagsProcessor is not None:
            try:
                self.updateTagsProcessor.stop()
            except Exception:
                noteSuppressed()
        try:
            self._closePLC()
        except Exception:
            noteSuppressed()
        self.logger.info("IOServer shutdown")
        printBoldRed("IOServer shutdown")


# *********************************************************************

def startIOServer(mode : str = "TEST"):
    io = PLCIOServer(mode = mode)
    printBoldBlue("*****************")
    printBlue(f"Started IO Server ({io.dataIOForReads.describe() if io.dataIOForReads else io.vendor})")
    printBoldBlue("*****************")
    io.heartbeatThread.join()
    io.monitorStopThread.join()
    sys.exit(0)


# *********************************************************************

# if __name__ == "__main__":
#     startIOServer()