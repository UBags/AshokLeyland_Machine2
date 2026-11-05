from utils.SuppressedExceptions import noteSuppressed   # logs exceptions that are caught and ignored
import random
import sys
import contextlib
import threading
import time
import logging
from typing import Any, Dict, List, Optional, Tuple, Type, Union
from pycomm3 import LogixDriver
from pycomm3 import DINT, SHORT_STRING, Struct, STRING, REAL
from pycomm3.logger import configure_default_logger, LOG_VERBOSE
from psycopg2.pool import ThreadedConnectionPool

from logutils import Logger
from logutils.SlaveLoggers import SlaveConsoleLogger, SlaveFileLogger
from logutils.LogSettings import setLogProcess, publishCycleContext, auditPLC, auditConnectionToggle
from utils.CosThetaPrintUtils import *
from utils.RedisUtils import *

from Configuration import CosThetaConfigurator
from statemachine.StateMachine import MachineState, MachineStateMachine
from processors.GenericQueueProcessor import GenericQueueProcessor
from utils.QRCodeHelper import getModel_LHSRHS_AndTonnage
from persistence.Persistence import getDatabaseName, setDatabaseName, getMachineSettings

TIME_WAIT_BEFORE_RESETTING_TORQUE2_VALUES_AFTER_READING_THEM: float = 1.5
TIME_WAIT_BEFORE_RESETTING_TORQUE3_VALUES_AFTER_READING_THEM: float = 2.0

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
        - "bool": Writes boolean value using IOServer.write_bool()
        - "float": Writes float value using IOServer.write_float()

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

    def __init__(self, ioServerInstance: "IOServer", name: str = "UpdateTagsToDefaultProcessor"):
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
                print(f"About to call write_bool() or write_float()")
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


class IOServer:
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
    PC_PLP_CapCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcCapCheckOKTagName()
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
        IOServer.logSource = getFullyQualifiedName(__file__, __class__)
        setLogProcess("ioserver")          # per-server log level, crash capture, live configuration
        self.machineState = MachineStateMachine()  # Instance of MachineStateMachine
        self.machineState.stateChangeListener = self._onStateChange
        self._cycleEndRedisConnection = None  # end-of-cycle message to the camera server (_onStateChange)
        # Vision result reset: one scheduler thread for all checks; READ states that ignore their trigger until the
        # PC has reset it after a NOT OK (see scheduleVisionResultReset)
        self._resultResetScheduler = DelayedJobScheduler("Vision Result Reset Scheduler")
        self._awaitingTriggerReset: set = set()
        self._awaitingTriggerResetLock = threading.Lock()
        self._abortLock = threading.RLock()  # one abort at a time (PLC emergency, heartbeat loss, frontend)
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

        self.plc_ip = plc_ip if plc_ip is not None else IOServer.PLC_IP
        self.mockInstance = mockInstance

        self.clientForReads: Union[LogixDriver, None] = None
        self.clientForWrites: Union[LogixDriver, None] = None

        self.readingLock = threading.Lock()
        self.writingLock = threading.Lock()

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

        # Database connection pool for reading machine settings
        self.dbConnectionPool: Union[ThreadedConnectionPool, None] = None
        self._initDatabaseConnectionPool()

        # Cycle time tracking
        # _opTimestamps holds {key: [start_time, end_time]} for each measured operation.
        # Keys are the OP_* constants defined in _resetOpTimestamps().
        self._opTimestamps: Dict[str, List[Optional[float]]] = {}
        self._cycleInProgress: bool = False
        self._resetOpTimestamps()

        # Current QR Code for this cycle
        self.currentQRCode: str = ""
        self.lastStation2RotationDuringTighteningDone: Optional[bool] = None

        # Initialize logging: pycomm3's own logger stays silent; the IOServer's messages go to the log file
        logging.getLogger("pycomm3").setLevel(logging.CRITICAL)
        self.logger = _FileLogAdapter(IOServer.logSource)

        # Start PLC connection and threads
        self.connectionInProgress: bool = False
        self.connectionAttempts: int = 0
        self.connectPLC()

        time.sleep(2)
        if not (self.clientForReads and self.clientForReads.connected):
            self._plcCheckPending = True          # checked (and reset) as soon as the PLC is connected
        self.resetForNextCycle("IOServer start")  # every per-cycle tag to its default, then CheckQRCode = True

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
            except:
                self.clientRedisConnectedForReads = False
                SlaveConsoleLogger.getInstance().logTakeAction(IOServer.logSource,
                                                               f'Could not get Redis Connection for Reads',
                                                               Logger.PROBLEM)
                SlaveFileLogger.getInstance().logTakeAction(IOServer.logSource,
                                                            f'Could not get Redis Connection for Reads', Logger.PROBLEM)

        if not self.clientRedisConnectedForWrites:
            try:
                self.redisConnectionForWrites = Redis(self.redisHostname, self.redisPort,
                                                      retry_on_timeout=True)
                self.clientRedisConnectedForWrites = True
            except:
                self.clientRedisConnectedForWrites = False
                SlaveConsoleLogger.getInstance().logTakeAction(IOServer.logSource,
                                                               f'Could not get Redis Connection for Writes',
                                                               Logger.PROBLEM)
                SlaveFileLogger.getInstance().logTakeAction(IOServer.logSource,
                                                            f'Could not get Redis Connection for Writes',
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
                SlaveConsoleLogger.getInstance().logDebug(IOServer.logSource,
                                                          f'Heartbeat Redis Connection is {self.heartbeatRedisConnection}',
                                                          Logger.SUCCESS)
                SlaveFileLogger.getInstance().logDebug(IOServer.logSource,
                                                       f'Heartbeat Redis Connection is {self.heartbeatRedisConnection}',
                                                       Logger.SUCCESS)
            except:
                self.heartbeatRedisConnected = False
                SlaveConsoleLogger.getInstance().logTakeAction(IOServer.logSource,
                                                               f'Could not get Heartbeat Redis Connection',
                                                               Logger.PROBLEM)
                SlaveFileLogger.getInstance().logTakeAction(IOServer.logSource,
                                                            f'Could not get Heartbeat Redis Connection', Logger.PROBLEM)

        if not self.combinedHeartbeatRedisConnected:
            try:
                self.combinedHeartbeatRedisConnection = Redis(self.redisHostname, self.redisPort, retry_on_timeout=True)
                self.combinedHeartbeatRedisConnected = True
                SlaveConsoleLogger.getInstance().logDebug(IOServer.logSource,
                                                          f'Combined Heartbeat Redis Connection is {self.combinedHeartbeatRedisConnection}',
                                                          Logger.SUCCESS)
                SlaveFileLogger.getInstance().logDebug(IOServer.logSource,
                                                       f'Combined Heartbeat Redis Connection is {self.combinedHeartbeatRedisConnection}',
                                                       Logger.SUCCESS)
            except:
                self.combinedHeartbeatRedisConnected = False
                SlaveConsoleLogger.getInstance().logTakeAction(IOServer.logSource,
                                                               f'Could not get Heartbeat Redis Connection',
                                                               Logger.PROBLEM)
                SlaveFileLogger.getInstance().logTakeAction(IOServer.logSource,
                                                            f'Could not get Heartbeat Redis Connection', Logger.PROBLEM)

        if not self.emergencyRedisConnected:
            try:
                self.emergencyRedisConnection = Redis(self.redisHostname, self.redisPort, retry_on_timeout=True)
                self.emergencyRedisConnected = True
                SlaveConsoleLogger.getInstance().logDebug(IOServer.logSource,
                                                          f'Emergency Redis Connection is {self.emergencyRedisConnection}',
                                                          Logger.SUCCESS)
                SlaveFileLogger.getInstance().logDebug(IOServer.logSource,
                                                       f'Emergency Redis Connection is {self.emergencyRedisConnection}',
                                                       Logger.SUCCESS)
            except:
                self.emergencyRedisConnected = False
                SlaveConsoleLogger.getInstance().logTakeAction(IOServer.logSource,
                                                               f'Could not get Emergency Redis Connection',
                                                               Logger.PROBLEM)
                SlaveFileLogger.getInstance().logTakeAction(IOServer.logSource,
                                                            f'Could not get Emergency Redis Connection', Logger.PROBLEM)

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
        cycle.settings.source = database (both PLCs): the settings in force in machine_settings (frontend: Machine
        Settings), all REAL:
          TighteningTorque1/2Setpoint, RotationRPM, NoOfRotation1CCW, NoOfRotation1CW
        cycle.settings.source = properties (the earlier Allen-Bradley set-up, not used now):
          TighteningTorque1/2Setpoint REAL, RotationRPM1/2 INT          (properties; -1 = do not write)
        Every value is worked out BEFORE anything is written: if any is unavailable (unknown side/model, no machine
        settings in the database) nothing is written. Each write is retried (SETTINGS_WRITE_ATTEMPTS).
        The caller writes QRCodeCheckOK / Done only AFTER this returns (OK = False if it returned False), so the PLC
        never sees Done before the settings are in place.
        The settings written are published for the DB server (stored with the component's record).
        Returns True only if every tag that had to be written was written.
        """
        if not qrCode:
            printBoldRed("Cannot write cycle settings: QR code is empty")
            return False
        try:
            modelName, lhs_rhs, tonnage = getModel_LHSRHS_AndTonnage(qrCode)
            side = (lhs_rhs or "").strip().upper()
            model = (modelName or "").strip().upper()
            if side == "LHS":
                sideCode = IOServer.LHS_CODE
            elif side == "RHS":
                sideCode = IOServer.RHS_CODE
            else:
                printBoldRed(f"Cannot determine LHS/RHS from QR code: {qrCode}")
                return False
            writes = [(IOServer.PC_PLC_LH_RH_Selection, sideCode, int)]
            if IOServer.PC_PLC_ModelSelection:
                modelCode = IOServer.MODEL_CODES.get(model)
                if modelCode is None:
                    printBoldRed(f"Unknown model '{modelName}' from QR code: {qrCode}")
                    return False
                writes.append((IOServer.PC_PLC_ModelSelection, modelCode, int))

            config = CosThetaConfigurator.getInstance()
            used = {"rotation_rpm": None, "tightening_torque1_setpoint": None, "tightening_torque2_setpoint": None,
                    "no_of_rotation1_ccw": None, "no_of_rotation1_cw": None}
            if config.getCycleSettingsSource() == "database":
                settings = getMachineSettings(getDatabaseName())
                missing = [k for k in used if settings.get(k) is None] if settings else list(used)
                if missing:
                    printBoldRed(f"Cannot write cycle settings: machine settings not available from the database "
                                 f"({', '.join(missing)}); nothing written")
                    return False
                for tag, key in ((IOServer.PC_PLC_TighteningTorque1Setpoint, "tightening_torque1_setpoint"),
                                 (IOServer.PC_PLC_TighteningTorque2Setpoint, "tightening_torque2_setpoint"),
                                 (IOServer.PC_PLC_RotationRPM, "rotation_rpm"),
                                 (IOServer.PC_PLC_NoOfRotation1CCW, "no_of_rotation1_ccw"),
                                 (IOServer.PC_PLC_NoOfRotation1CW, "no_of_rotation1_cw")):
                    if tag:
                        writes.append((tag, float(settings[key]), float))
                        used[key] = float(settings[key])
            else:
                skip = CosThetaConfigurator.SETPOINT_NOT_TO_BE_WRITTEN
                for tag, value, key in ((IOServer.PC_PLC_TighteningTorque1Setpoint, config.getTighteningTorque1Setpoint(), "tightening_torque1_setpoint"),
                                        (IOServer.PC_PLC_TighteningTorque2Setpoint, config.getTighteningTorque2Setpoint(), "tightening_torque2_setpoint")):
                    if tag and value != skip:
                        writes.append((tag, float(value), float))
                        used[key] = float(value)
                for tag, value in ((IOServer.PC_PLC_RotationRPM1, config.getRotation1RPM()),
                                   (IOServer.PC_PLC_RotationRPM2, config.getRotation2RPM())):
                    if tag and value != skip:
                        writes.append((tag, int(value), int))

            success = True
            for tag, value, kind in writes:
                writer = (lambda t, v: self.write_int(t, int(v))) if kind is int else \
                         (lambda t, v: self.write_float(t, float(v), debug=False))
                ok_ = False
                for attempt in range(IOServer.SETTINGS_WRITE_ATTEMPTS):
                    if writer(tag, value):
                        ok_ = True
                        break
                    time.sleep(IOServer.WRITE_RETRY_INTERVAL)
                if not ok_:
                    printBoldRed(f"Could not write cycle setting {tag}={value} after "
                                 f"{IOServer.SETTINGS_WRITE_ATTEMPTS} attempts")
                success = success and ok_
            written = ", ".join(f"{tag}={value}" for tag, value, kind in writes)
            if success:
                try:
                    publishCycleSettingsForQRCode(self.redisConnectionForWrites, qrCode, used)
                except Exception as e:
                    printBoldRed(f"Could not publish the cycle settings for the DB record: {e}")
                printBoldGreen(f"Wrote cycle settings to PLC for {model} {side}: {written}")
            else:
                printBoldRed(f"Failed to write some cycle settings to PLC ({written})")
            return success
        except Exception as e:
            printBoldRed(f"Exception writing cycle settings to PLC: {e}")
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
        self._opTimestamps = {key: [None, None] for key in IOServer.ALL_OP_KEYS}
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

        T1 = getDelta(IOServer.OP_KNUCKLE)
        T2 = getDelta(IOServer.OP_HUB_BEARING)
        T3 = getDelta(IOServer.OP_TOP_BEARING)
        T4 = getDelta(IOServer.OP_NUT_WASHER)
        T5 = getDelta(IOServer.OP_NOCAP_BUNK)
        T6 = getDelta(IOServer.OP_TORQUE2)
        T7 = getDelta(IOServer.OP_SPLITPIN)
        T8 = getDelta(IOServer.OP_CAP)
        T9 = getDelta(IOServer.OP_BUNK_CAP_PRESS)

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
        SlaveConsoleLogger.getInstance().logTakeAction(IOServer.logSource, cycleTimeMsg, Logger.PROBLEM)
        SlaveFileLogger.getInstance().logTakeAction(IOServer.logSource, cycleTimeMsg, Logger.PROBLEM)

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
            return [IOServer.PLC_PC_CheckQRCode], [bool]

        # Step 1: Knuckle
        if currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE:
            return [IOServer.PLC_PC_CheckKnuckle], [bool]

        # Step 2: Hub (maps to HUB_AND_BOTTOM_BEARING state)
        if currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING:
            return [IOServer.PLC_PC_CheckHub], [bool]

        # Step 3: Hub and Second Bearing (maps to TOP_BEARING state)
        if currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING:
            return [IOServer.PLC_PC_CheckHubAndSecondBearing], [bool]

        # Step 4: Nut and Plate Washer
        if currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER:
            return [IOServer.PLC_PC_CheckNutAndPlateWasher], [bool]

        # Step 5: Tightening Torque 1 (Station 2)
        if currentState == MachineState.READ_TIGHTENING_TORQUE_1_DONE:
            return [IOServer.PLC_PC_Station2TorqueValueSet], [bool]
        if currentState == MachineState.READ_TIGHTENING_TORQUE_1:
            return [IOServer.PLC_PC_Station2TorqueValue], [float]

        # Step 7: No Cap Bunk for Component Press
        if currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS:
            return [IOServer.PLC_PC_CheckNoCapBunk], [bool]

        # Step 8: Component Press Done
        if currentState == MachineState.READ_COMPONENT_PRESS_DONE:
            return [IOServer.PLC_PC_ComponentPressDone], [bool]

        # Step 9: No Bunk
        if currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK:
            return [IOServer.PLC_PC_CheckNoBunk], [bool]

        # Step 10: Tightening Torque 2 (Station 2 - second round, reuses same tags)
        if currentState == MachineState.READ_TIGHTENING_TORQUE_2_DONE:
            return [IOServer.PLC_PC_Station2TorqueValueSet], [bool]
        if currentState == MachineState.READ_TIGHTENING_TORQUE_2:
            return [IOServer.PLC_PC_Station2TorqueValue], [float]

        # Step 11: Split Pin and Washer
        if currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER:
            return [IOServer.PLC_PC_CheckSplitPinAndWasher], [bool]

        # Step 12: Cap
        if currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP:
            return [IOServer.PLC_PC_CheckCap], [bool]

        # Step 13: Bunk for Cap Press - reuses CheckBunk
        if currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS:
            return [IOServer.PLC_PC_CheckBunk], [bool]

        # Step 14: Cap Press Done
        if currentState == MachineState.READ_CAP_PRESS_DONE:
            return [IOServer.PLC_PC_CapPressDone], [bool]

        # Step 15: Free Rotation Torque (Station 3 - final reading)
        if currentState == MachineState.READ_FREE_ROTATION_TORQUE_1_DONE:
            return [IOServer.PLC_PC_Station3TorqueValueSet], [bool]
        if currentState == MachineState.READ_FREE_ROTATION_TORQUE_1:
            return [IOServer.PLC_PC_Station3TorqueValue], [float]

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
            return [IOServer.PC_PLC_QRCodeCheckOK, IOServer.PC_PLC_QRCodeCheckDone, IOServer.PLC_PC_CheckQRCode]

        # Step 1: Knuckle
        if currentState == MachineState.WRITE_RESULT_OF_CHECKING_KNUCKLE:
            return [IOServer.PC_PLC_KnuckleCheckOK, IOServer.PC_PLC_KnuckleCheckDone, IOServer.PLC_PC_CheckKnuckle]

        # Step 2: Hub (maps to HUB_AND_BOTTOM_BEARING state)
        if currentState == MachineState.WRITE_RESULT_OF_CHECKING_HUB_AND_BOTTOM_BEARING:
            return [IOServer.PC_PLC_HubCheckOK, IOServer.PC_PLC_HubCheckDone, IOServer.PLC_PC_CheckHub]

        # Step 3: Hub and Second Bearing (maps to TOP_BEARING state)
        if currentState == MachineState.WRITE_RESULT_OF_CHECKING_TOP_BEARING:
            return [IOServer.PC_PLC_HubAndSecondBearingCheckOK, IOServer.PC_PLC_HubAndSecondBearingCheckDone,
                    IOServer.PLC_PC_CheckHubAndSecondBearing]

        # Step 4: Nut and Plate Washer
        if currentState == MachineState.WRITE_RESULT_OF_CHECKING_NUT_AND_PLATEWASHER:
            return [IOServer.PC_PLC_NutAndPlateWasherCheckOK, IOServer.PC_PLC_NutAndPlateWasherCheckDone,
                    IOServer.PLC_PC_CheckNutAndPlateWasher]

        # Step 7: No Cap Bunk for Component Press
        if currentState == MachineState.WRITE_RESULT_OF_CHECKING_BUNK_FOR_COMPONENT_PRESS:
            return [IOServer.PC_PLC_NoCapBunkCheckOK, IOServer.PC_PLC_NoCapBunkCheckDone,
                    IOServer.PLC_PC_CheckNoCapBunk]

        # Step 9: No Bunk
        if currentState == MachineState.WRITE_RESULT_OF_CHECKING_NO_BUNK:
            return [IOServer.PC_PLC_NoBunkCheckOK, IOServer.PC_PLC_NoBunkCheckDone, IOServer.PLC_PC_CheckNoBunk]

        # Step 11: Split Pin and Washer
        if currentState == MachineState.WRITE_RESULT_OF_CHECKING_SPLITPIN_AND_WASHER:
            return [IOServer.PC_PLC_SplitPinAndWasherCheckOK, IOServer.PC_PLC_SplitPinAndWasherCheckDone,
                    IOServer.PLC_PC_CheckSplitPinAndWasher]

        # Step 12: Cap
        if currentState == MachineState.WRITE_RESULT_OF_CHECKING_CAP:
            return [IOServer.PC_PLP_CapCheckOK, IOServer.PC_PLC_CapCheckDone, IOServer.PLC_PC_CheckCap]

        # Step 13: Bunk for Cap Press - reuses CheckBunk
        if currentState == MachineState.WRITE_RESULT_OF_CHECKING_BUNK_FOR_CAP_PRESS:
            return [IOServer.PC_PLC_BunkCheckOK, IOServer.PC_PLC_BunkCheckDone, IOServer.PLC_PC_CheckBunk]

        return None

    def connectPLC(self, forceRenew=False) -> bool | None:
        """Connect to the PLC using pycomm3 or MockPLC."""
        if self.connectionInProgress:
            return None
        self.connectionInProgress = True
        self.connectionAttempts += 1

        try:
            if forceRenew:
                if self.clientForReads is not None:
                    try:
                        self.clientForReads.close()
                    except:
                        noteSuppressed()
                if self.clientForWrites is not None:
                    try:
                        self.clientForWrites.close()
                    except:
                        noteSuppressed()
                self.clientForReads = None
                self.clientForWrites = None

            # Use MockPLC if mockInstance is True
            if self.mockInstance:
                from costhetaio.allenbradley.MockPLC import MockPLC
                self.clientForReads = MockPLC()
                self.clientForReads.open()
                printBoldGreen(f"Connected to MockPLC for Reads")
                self.clientForWrites = self.clientForReads  # Share same instance
                printBoldGreen(f"Connected to MockPLC for Writes (shared instance)")
            else:
                self.clientForReads = LogixDriver(f"{self.plc_ip}")
                self.clientForReads.open()
                printBoldGreen(f"Connected to PLC for Reads at {self.plc_ip}")
                self.clientForWrites = LogixDriver(f"{self.plc_ip}")
                self.clientForWrites.open()
                printBoldGreen(f"Connected to PLC for Writes at {self.plc_ip}")

            self.connectionInProgress = False
            self.connectionAttempts = 0
            return True
        except Exception as e:
            if (self.connectionAttempts == 1) or (
                    (self.connectionAttempts % IOServer.LOG_CONNECTION_PROBLEM_EVERY_N_SECS) % 5) == 0:
                printBoldRed(f"PLC connection error: {e}")
            try:
                self.clientForReads.close()
            except:
                noteSuppressed()
            try:
                self.clientForWrites.close()
            except:
                noteSuppressed()
            self.clientForReads = None
            self.clientForWrites = None
            self.connectionInProgress = False
            return False

    def launchConnectionMonitoringThread(self):
        objRef = self

        def connectionCheckingLoop():
            """Loop to check PLC connection status."""
            while not objRef.shutdown:
                time.sleep(objRef.CONNECTION_CHECK_INTERVAL)

                tryToConnect: bool = False
                if ((not objRef.clientForReads) or (not objRef.clientForReads.connected) or
                        (not objRef.clientForWrites) or (not objRef.clientForWrites.connected)):
                    tryToConnect = True
                else:
                    try:
                        readValue: int = 0
                        readValue = self.read_int(IOServer.PLC_PC_WatchTag)
                        if readValue != IOServer.WATCH_TAG_VALUE:
                            raise Exception(f"Could not read tag {IOServer.PLC_PC_WatchTag} from PLC")
                    except Exception:
                        if (objRef.connectionAttempts == 1) or (
                                (objRef.connectionAttempts % IOServer.LOG_CONNECTION_PROBLEM_EVERY_N_SECS) % 5) == 0:
                            objRef.logger.fatal(f"PLC connection test failed")
                            printBoldRed(f"PLC connection test failed. Reconnecting...")
                        tryToConnect = True

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

        self.connectionCheckThread = threading.Thread(name=f'IOServer Connection Keep Alive Thread',
                                                      target=connectionCheckingLoop,
                                                      args=(), daemon=True)
        self.connectionCheckThread.start()

    def launchHeartbeatThread(self):
        objRef = self

        def reportHeartbeat():
            ioHeartbeatGap = CosThetaConfigurator.getInstance().getIOConnectionStatusSleepInterval()
            while not objRef.shutdown:
                try:
                    if objRef.clientForReads is None or objRef.clientForWrites is None:
                        sendHeartbeatFromIOServerToHeartbeatServer(redisConnection=objRef.heartbeatRedisConnection,
                                                                   status=DEAD)
                    elif not objRef.clientForReads.connected or not objRef.clientForWrites.connected:
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
                except:
                    noteSuppressed()

        self.heartbeatThread = threading.Thread(name=f"IO Heartbeat Thread", target=reportHeartbeat, args=(),
                                                daemon=True)
        self.heartbeatThread.start()

    def launchThreadForUpdatingCombinedHeartbeat(self):
        objRef = self

        def updateHeartbeat():
            heartbeatGap = CosThetaConfigurator.getInstance().getCombinedConnectionStatusUpdateInterval()
            while not objRef.shutdown:
                try:

                    # WHILE TESTING PLC AND PC COMMUNICATION, currentStatus is set to True.
                    # IN PRODUCTION, set this back to readCombinedHeartbeatInIOServerFromHeartbeatServer(objRef.combinedHeartbeatRedisConnection)

                    # currentStatus = readCombinedHeartbeatInIOServerFromHeartbeatServer(
                    #     objRef.combinedHeartbeatRedisConnection)
                    currentStatus = True
                    if currentStatus:
                        IOServer.CONSECUTIVE_CONNECTION_DOWN_COUNT = 0
                        statusToBeSent = not IOServer.LAST_CONNECTION_STATUS_UPDATED
                        objRef.write_bool(IOServer.PC_PLC_ConnectionStatusTag, statusToBeSent, debug=False)
                        IOServer.LAST_CONNECTION_STATUS_UPDATED = statusToBeSent
                    else:
                        IOServer.CONSECUTIVE_CONNECTION_DOWN_COUNT += 1
                        if (IOServer.CONSECUTIVE_CONNECTION_DOWN_COUNT == 2):
                            printBoldRed(
                                f"Combined Heartbeat - connection problem somewhere : sendEmergencyAbortFromIOServerToFEServer")
                            objRef.abortCycle("connection to a server lost (combined heartbeat)",
                                              notifyQRCodeServer=False, notifyFrontend=True, clearQueues=False)
                except Exception as e:
                    printBoldRed(f"Exception {e} encountered in heartbeat")
                    objRef.connectToRedisForHeartbeatAndEmergencyMonitoring(forceRenew=True)
                try:
                    time.sleep(heartbeatGap)
                except:
                    noteSuppressed()

        combinedHeartbeatThread = threading.Thread(name=f"Combined Heartbeat Update Thread", target=updateHeartbeat,
                                                   args=(), daemon=True)
        combinedHeartbeatThread.start()

    def launchEmergencyMonitoringThread(self):
        objRef = self

        def monitorEmergency():
            while not objRef.shutdown:
                emergencyActivated: bool = False
                if (objRef.clientForReads is not None):
                    if objRef.clientForReads.connected:
                        try:
                            emergencyActivated = objRef.read_bool(IOServer.PLC_PC_EmergencyAbort)
                        except Exception as e:
                            printBoldRed(f"Exception {e} encountered in emergency monitoring")
                            objRef.connectToRedisForHeartbeatAndEmergencyMonitoring(forceRenew=True)
                if emergencyActivated:
                    printBoldRed(
                        f"Combined Heartbeat - Emergency Button pressed in Machine : sendEmergencyAbortFromIOServerToFEServer")
                    objRef.abortCycle("emergency button pressed on the machine (PLC)",
                                      notifyQRCodeServer=True, notifyFrontend=True, clearQueues=True)
                try:
                    time.sleep(1.0)
                except:
                    noteSuppressed()

        emergencyMonitoringThread = threading.Thread(name=f"Emergency Monitoring Thread", target=monitorEmergency,
                                                     args=(), daemon=True)
        emergencyMonitoringThread.start()

    def abortCycle(self, reason: str, notifyQRCodeServer: bool = True, notifyFrontend: bool = True,
                   clearQueues: bool = True) -> None:
        """Abort the current cycle and go back to READ_QR_CODE, whatever state the cycle is in. Used for the emergency
        button on the machine (PLC), a lost server connection (heartbeat) and an abort sent by the frontend.
          notifyQRCodeServer: tell the QR code server to stop a scan in progress
          clearQueues:        drop the camera / QR code messages of the aborted cycle
          notifyFrontend:     tell the frontend (emergency-abort screen, abort record). False when the abort came
                              FROM the frontend, which has already done that itself.
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
            self.resetTagToFalse(IOServer.PLC_PC_EmergencyAbort)
            # The state first, so that nothing of the aborted cycle is acted on any more, then the tags
            self.machineState.setCurrentState(MachineState.READ_QR_CODE)
            self.resetForNextCycle(f"cycle aborted in state {stateBefore.name}: {reason}")
            self._resetOpTimestamps()  # Clear cycle timing on abort
            self.lastAbortReason = reason
            self.abortCount += 1

    def _visionTriggerTag(self, readState: MachineState) -> Optional[str]:
        """The PLC's Check (trigger) tag of a vision check, by its READ (take picture) state."""
        return {
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE: IOServer.PLC_PC_CheckKnuckle,
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING: IOServer.PLC_PC_CheckHub,
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING: IOServer.PLC_PC_CheckHubAndSecondBearing,
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER: IOServer.PLC_PC_CheckNutAndPlateWasher,
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS: IOServer.PLC_PC_CheckNoCapBunk,
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK: IOServer.PLC_PC_CheckNoBunk,
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER: IOServer.PLC_PC_CheckSplitPinAndWasher,
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP: IOServer.PLC_PC_CheckCap,
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS: IOServer.PLC_PC_CheckBunk,
        }.get(readState)

    def _readBoolOrNone(self, tag: str) -> Optional[bool]:
        """Read a bool from the PLC; None if it could not be read (read_bool cannot tell a failure from a value)."""
        if not tag or self.clientForReads is None or not self.clientForReads.connected:
            return None
        try:
            with self.readingLock:
                value = self.clientForReads.read(tag).value
            return None if value is None else bool(value)
        except Exception as e:
            printBoldRed(f"Could not read {tag}: {e}")
            return None

    def _writeFalseWithRetries(self, tag: str, why: str) -> bool:
        for attempt in range(IOServer.VISION_RESULT_RESET_ATTEMPTS):
            if self.write_bool(tag, False):
                return True
            time.sleep(IOServer.WRITE_RETRY_INTERVAL)
        printBoldRed(f"Could not reset {tag} to False {why} ({IOServer.VISION_RESULT_RESET_ATTEMPTS} attempts)")
        self.logger.error(f"Could not reset {tag} to False {why}")
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
            else:
                self._awaitingTriggerReset.discard(readState)

    def scheduleVisionResultReset(self, tags: list, checkState: MachineState, result: bool = True) -> None:
        """Called after a vision check's result was written to the PLC (<Check>OK = True for OK / False for NOT OK,
        then <Check>Done = True). After plc.vision.result.reset.delay.sec seconds (read now, so a change in the
        properties applies to the next check) the IOServer:
          1. reads <Check>Done and <Check>OK back from the PLC;
          2. sets <Check>OK, then <Check>Done, to False;
          3. if Done was read as True and OK as False (NOT OK), also sets the PLC's <Check> trigger to False, so that
             the next press of the two-hand buttons (2HB) raises it again and the picture is retaken. (If Done is
             already False, or a read fails, the result the IOServer wrote decides.)
        After a NOT OK, the check's READ state ignores the trigger until step 3 has been done; otherwise the trigger
        still standing from the NOT OK would make the IOServer retake the picture at once, without a 2HB press.
        (A 2HB press during the delay itself is therefore not seen: press again once the delay is over.)
        All resets run in ONE scheduler thread (DelayedJobScheduler), not a thread per reset. Each write is retried
        (VISION_RESULT_RESET_ATTEMPTS). Not used for the QR code check.
        tags = the check's write tags as used by perform_write_state_action: [<Check>OK, <Check>Done];
        checkState = the check's WRITE (result) state."""
        delay = CosThetaConfigurator.getInstance().getPlcVisionResultResetDelay()
        okTag = tags[0] if len(tags) > 0 else None
        doneTag = tags[1] if len(tags) > 1 else None
        if not okTag:
            return
        readState = MachineState(int(checkState) - 1)
        triggerTag = self._visionTriggerTag(readState)
        objRef = self
        if not result:
            self._setAwaitingTriggerReset(readState, True)

        def reset():
            doneRead = objRef._readBoolOrNone(doneTag) if doneTag else None
            okRead = objRef._readBoolOrNone(okTag)
            # OK read is the result only while Done is still True; if Done is already False (the PLC or a cycle reset
            # cleared it) or a read failed, the result written by the IOServer is used.
            okResult = okRead if (doneRead is True and okRead is not None) else result
            why = f"after the {'OK' if result else 'NOT OK'} of {checkState.name}"
            okReset = objRef._writeFalseWithRetries(okTag, why)
            doneReset = objRef._writeFalseWithRetries(doneTag, why) if doneTag else False
            line = (f"{delay} s {why}: read {doneTag}={doneRead}, {okTag}={okRead}; {okTag} -> False "
                    f"({'done' if okReset else 'FAILED'}), {doneTag} -> False ({'done' if doneReset else 'FAILED'})")
            if not okResult and triggerTag:
                checkReset = objRef._writeFalseWithRetries(triggerTag, why)
                line += f"; NOT OK: {triggerTag} -> False ({'done' if checkReset else 'FAILED'}), waiting for the next 2HB"
            objRef._setAwaitingTriggerReset(readState, False)
            printLight(line)

        replaced = self._resultResetScheduler.schedule(okTag, delay, reset, info=readState)
        if replaced is not None and replaced != readState:
            self._setAwaitingTriggerReset(replaced, False)

    def cancelVisionResultReset(self, tags: list) -> None:
        """Cancel a pending OK/Done reset of this check (a new result is about to be written for it); its READ state
        no longer waits for the trigger reset."""
        okTag = tags[0] if tags else None
        if okTag:
            self._setAwaitingTriggerReset(self._resultResetScheduler.cancel(okTag), False)

    def launchFrontendAbortListenerThread(self):
        """Frontend -> IOServer: an abort sent by the frontend (manual abort, sendAbortFromFEServerToIOServer) resets
        the cycle to READ_QR_CODE, exactly like the emergency button on the machine."""
        objRef = self

        def listen():
            connection = None
            while not objRef.shutdown:
                try:
                    if connection is None:
                        connection = Redis(objRef.redisHostname, objRef.redisPort, retry_on_timeout=True)
                    abort, reason = readAbortInIOServerFromFEServer(connection, block=500)
                    if abort:
                        objRef.abortCycle(f"abort from the frontend ({reason or 'no reason given'})",
                                          notifyQRCodeServer=True, notifyFrontend=False, clearQueues=True)
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
                            sendStoppedResponse(objRef.heartbeatRedisConnection, aProducer=IOServer.logSource)
                    else:
                        objRef.connectToRedisForHeartbeatAndEmergencyMonitoring(False)
                except:
                    noteSuppressed()
                if not objRef.shutdown:
                    time.sleep(2)

        self.monitorStopThread = threading.Thread(name=f'Monitor Stop Thread in IOServer - IOserver',
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
                try:
                    writeCounter += 1
                    valueRead: bool = False
                    goAheadToProcessing: bool = False
                    allok: bool = True
                    typeOfValues: str = "nothing"
                    finalValue: float = IOServer.FALSE_FLOAT_VALUE

                    if ((objRef.clientForReads is not None) and objRef.clientForReads.connected
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

        self.continuousReadLoop = threading.Thread(name=f'Continuous Read Loop in IOServer',
                                                   target=readLoop,
                                                   args=(), daemon=True)
        self.continuousReadLoop.start()

    def launchWriteTagsLoopThread(self):
        objRef = self

        def writeTagsLoop():
            """Main do loop to take action wherever something required."""
            written_TagsToBeWritten: int = 0
            while not objRef.shutdown:
                stateOfTags = objRef.machineState.getCurrentState()
                tagsToBeWritten = objRef.getCurrentTagsToBeWritten()
                if objRef.machineState.getCurrentState() != stateOfTags:
                    continue            # state changed (abort) while the tags were looked up: look again
                if (
                        objRef.clientForWrites is not None) and objRef.clientForWrites.connected and not objRef.connectionInProgress:
                    if tagsToBeWritten is not None and len(tagsToBeWritten) > 0:
                        if written_TagsToBeWritten == 0:
                            written_TagsToBeWritten += 1
                        stateBeforeWrite = stateOfTags
                        if stateBeforeWrite == MachineState.WRITE_QR_CODE:
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
                                # 1. Populate the cycle settings (side, model, setpoints) FIRST ...
                                settingsWritten = objRef.writeCycleSettingsToPLC(qrCode)
                                # 2. ... then QRCodeCheckOK, then QRCodeCheckDone. If the settings could not all be
                                #    written, OK = False (like a NOT OK check): the cycle must not start without them.
                                if objRef.perform_write_state_action(tags=tagsToBeWritten, result=settingsWritten,
                                                                     expectedState=stateBeforeWrite):
                                    printBold(f"Transition from state {objRef.machineState.getCurrentState().name}")
                                    if settingsWritten:
                                        objRef.machineState.incrementStateFrom(MachineState.WRITE_QR_CODE)
                                    else:
                                        printBoldRed("Cycle settings not written: QR code check reported NOT OK to the PLC")
                                        objRef.machineState.decrementStateFrom(MachineState.WRITE_QR_CODE)
                                    printBold(f"        ...to state {objRef.machineState.getCurrentState().name}")
                        else:
                            # stateBeforeWrite: the WRITE state we are about to act on (captured before the read)
                            previousMachineState, evaluation = readDataInIOServerFromCameraServer(
                                self.redisConnectionForWrites)
                            if objRef.machineState.getCurrentState() != stateBeforeWrite:
                                # the cycle was aborted while waiting for the camera: this answer is for the old cycle
                                printBoldRed(f"Camera answer for {stateBeforeWrite.name} received after the cycle was "
                                             f"aborted; ignored")
                                time.sleep(objRef.write_loop_interval)
                                continue
                            if evaluation:
                                if not objRef.perform_write_state_action(tags=tagsToBeWritten, result=True,
                                                                         expectedState=stateBeforeWrite):
                                    time.sleep(objRef.write_loop_interval)
                                    continue        # cycle aborted while writing: do not move on
                                # set the check's OK and Done back to False after plc.vision.result.reset.delay.sec
                                objRef.scheduleVisionResultReset(tagsToBeWritten, stateBeforeWrite, result=True)
                                # ── Record op-END timestamps for Done-tag-based operations ──
                                if stateBeforeWrite == MachineState.WRITE_RESULT_OF_CHECKING_KNUCKLE:
                                    objRef._recordOpEnd(IOServer.OP_KNUCKLE)
                                elif stateBeforeWrite == MachineState.WRITE_RESULT_OF_CHECKING_HUB_AND_BOTTOM_BEARING:
                                    objRef._recordOpEnd(IOServer.OP_HUB_BEARING)
                                elif stateBeforeWrite == MachineState.WRITE_RESULT_OF_CHECKING_TOP_BEARING:
                                    objRef._recordOpEnd(IOServer.OP_TOP_BEARING)
                                elif stateBeforeWrite == MachineState.WRITE_RESULT_OF_CHECKING_BUNK_FOR_COMPONENT_PRESS:
                                    objRef._recordOpEnd(IOServer.OP_NOCAP_BUNK)
                                elif stateBeforeWrite == MachineState.WRITE_RESULT_OF_CHECKING_SPLITPIN_AND_WASHER:
                                    objRef._recordOpEnd(IOServer.OP_SPLITPIN)
                                elif stateBeforeWrite == MachineState.WRITE_RESULT_OF_CHECKING_CAP:
                                    objRef._recordOpEnd(IOServer.OP_CAP)
                                printBold(f"Transition from state {objRef.machineState.getCurrentState().name}")
                                objRef.machineState.incrementStateFrom(stateBeforeWrite)
                                printBold(f"        ...to state {objRef.machineState.getCurrentState().name}")
                            else:
                                if previousMachineState != MachineState.INVALID_STATE and \
                                        objRef.perform_write_state_action(tags=tagsToBeWritten, result=False,
                                                                          expectedState=stateBeforeWrite):
                                    # NOT OK too: OK (False) and Done back to False after the delay
                                    objRef.scheduleVisionResultReset(tagsToBeWritten, stateBeforeWrite, result=False)
                                    printBoldRed(
                                        f"Did not move ahead from state {objRef.machineState.getCurrentState().name}")
                                    printBold(f"Transition from state {objRef.machineState.getCurrentState().name}")
                                    objRef.machineState.decrementStateFrom(stateBeforeWrite)
                                    printBold(f"        ...to state {objRef.machineState.getCurrentState().name}")
                time.sleep(objRef.write_loop_interval)

        self.continuousDoLoop = threading.Thread(name=f'Continuous Do Loop in IOServer',
                                                 target=writeTagsLoop,
                                                 args=(), daemon=True)
        self.continuousDoLoop.start()

    def process_current_read_state(self, valueRead: bool | float | None):
        """Process the value read from PLC based on current machine state."""
        if valueRead is None:
            return False

        # printBoldGreen(f"Read value {valueRead}")
        returnValue: bool = False
        currentState = self.machineState.getCurrentState()

        if type(valueRead) == bool:
            if valueRead and self.triggerIgnoredUntilReset(currentState):
                # NOT OK earlier: this trigger is the one still standing from it; wait until the PC has reset it
                # (scheduleVisionResultReset) and the operator presses the two-hand buttons again
                return False
            if valueRead:
                successfullySent: bool = False
                try:
                    # Step 0: QR Code
                    if currentState == MachineState.READ_QR_CODE:
                        successfullySent = sendDataFromIOServerToQRCodeServer(self.redisConnectionForReads)

                    # Step 1: Knuckle — START of T1
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads,
                                                                              currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE)
                        if successfullySent:
                            self._recordOpStart(IOServer.OP_KNUCKLE)

                    # Step 2: Hub (HUB_AND_BOTTOM_BEARING state) — START of T2
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads,
                                                                              currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING)
                        if successfullySent:
                            self._recordOpStart(IOServer.OP_HUB_BEARING)

                    # Step 3: Hub and Second Bearing (TOP_BEARING state) — START of T3
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads,
                                                                              currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING)
                        if successfullySent:
                            self._recordOpStart(IOServer.OP_TOP_BEARING)

                    # Step 4: Nut and Plate Washer — START of T4
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads,
                                                                              currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER)
                        if successfullySent:
                            self._recordOpStart(IOServer.OP_NUT_WASHER)

                    # Step 5: Tightening Torque 1 Done
                    elif currentState == MachineState.READ_TIGHTENING_TORQUE_1_DONE:
                        successfullySent = True

                    # Step 7: Bunk for Component Press — START of T5 and T6
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads,
                                                                              currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS)
                        if successfullySent:
                            self._recordOpStart(IOServer.OP_NOCAP_BUNK)
                            self._recordOpStart(IOServer.OP_TORQUE2)

                    # Step 8: Component Press Done
                    elif currentState == MachineState.READ_COMPONENT_PRESS_DONE:
                        successfullySent = sendDataFromIOServerToFEServer(self.redisConnectionForReads,
                                                                          currentMachineState=MachineState.READ_COMPONENT_PRESS_DONE)

                    # Step 9: No Bunk
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads,
                                                                              currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK)

                    # Step 10: Tightening Torque 2 Done — END of T6
                    elif currentState == MachineState.READ_TIGHTENING_TORQUE_2_DONE:
                        successfullySent = True
                        self._recordOpEnd(IOServer.OP_TORQUE2)

                    # Step 11: Split Pin and Washer — START of T7
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads,
                                                                              currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER)
                        if successfullySent:
                            self._recordOpStart(IOServer.OP_SPLITPIN)

                    # Step 12: Cap — START of T8
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads,
                                                                              currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP)
                        if successfullySent:
                            self._recordOpStart(IOServer.OP_CAP)

                    # Step 13: Bunk for Cap Press — START of T9
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads,
                                                                              currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS)
                        if successfullySent:
                            self._recordOpStart(IOServer.OP_BUNK_CAP_PRESS)

                    # Step 14: Cap Press Done
                    elif currentState == MachineState.READ_CAP_PRESS_DONE:
                        successfullySent = sendDataFromIOServerToFEServer(self.redisConnectionForReads,
                                                                          currentMachineState=MachineState.READ_CAP_PRESS_DONE)

                    # Step 15: Free Rotation Torque 1 Done — END of T9
                    elif currentState == MachineState.READ_FREE_ROTATION_TORQUE_1_DONE:
                        successfullySent = True
                        self._recordOpEnd(IOServer.OP_BUNK_CAP_PRESS)

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
            if valueRead == IOServer.FALSE_FLOAT_VALUE:
                printBold(f"Failed to read proper value of float in state {self.machineState.getCurrentState().name}")
                returnValue = False
            else:
                successfullySent: bool = False
                try:
                    # Step 5B: Tightening Torque 1 Value
                    if currentState == MachineState.READ_TIGHTENING_TORQUE_1:
                        printBoldGreen(f"Read Station 2 Torque Value (1st time): {valueRead}")
                        rotationDone = self._readStation2RotationDuringTighteningDone()
                        successfullySent = sendDataFromIOServerToFEServer(self.redisConnectionForReads,
                                                                          currentMachineState=MachineState.READ_TIGHTENING_TORQUE_1,
                                                                          value=valueRead,
                                                                          rotationDone=rotationDone)
                        if successfullySent:
                            self._recordOpEnd(IOServer.OP_NUT_WASHER)   # END of T4 (M2: no separate free-rotation step)
                        # Reset Station 2 torque tags after reading (so Step 10 doesn't auto-trigger)
                        if successfullySent:
                            updateRequest : dict = {
                                "sleepTimeForDelay": TIME_WAIT_BEFORE_RESETTING_TORQUE2_VALUES_AFTER_READING_THEM,  # Seconds to wait before writing tags
                                "tag1": {"tag": IOServer.PLC_PC_Station2TorqueValueSet, "type": "bool", "value": False},
                                "tag2": {"tag": IOServer.PLC_PC_Station2TorqueValue, "type": "float", "value": 0.0},
                            }
                            if IOServer.PLC_PC_Station2RotationDuringTighteningDone:     # reset with the torque tags
                                updateRequest["tag3"] = {"tag": IOServer.PLC_PC_Station2RotationDuringTighteningDone,
                                                         "type": "bool", "value": False}
                            print(f"Sending request {updateRequest} to UpdateTagsProcessor")
                            addTagUpdateRequest(updateRequest)
                            # Transition to next state after successfully reading torque
                            printBold(f"Transition from state {self.machineState.getCurrentState().name}")
                            self.machineState.incrementStateFrom(currentState)
                            printBold(f"        ...to state {self.machineState.getCurrentState().name}")
                    # Step 10B: Tightening Torque 2 Value
                    elif currentState == MachineState.READ_TIGHTENING_TORQUE_2:
                        printBoldGreen(f"Read Station 2 Torque Value (2nd time): {valueRead}")
                        rotationDone = self._readStation2RotationDuringTighteningDone()
                        successfullySent = sendDataFromIOServerToFEServer(self.redisConnectionForReads,
                                                                          currentMachineState=MachineState.READ_TIGHTENING_TORQUE_2,
                                                                          value=valueRead,
                                                                          rotationDone=rotationDone)
                        if successfullySent:
                            updateRequest : dict = {
                                "sleepTimeForDelay": TIME_WAIT_BEFORE_RESETTING_TORQUE2_VALUES_AFTER_READING_THEM,  # Seconds to wait before writing tags
                                "tag1": {"tag": IOServer.PLC_PC_Station2TorqueValueSet, "type": "bool", "value": False},
                                "tag2": {"tag": IOServer.PLC_PC_Station2TorqueValue, "type": "float", "value": 0.0},
                            }
                            if IOServer.PLC_PC_Station2RotationDuringTighteningDone:     # reset with the torque tags
                                updateRequest["tag3"] = {"tag": IOServer.PLC_PC_Station2RotationDuringTighteningDone,
                                                         "type": "bool", "value": False}
                            print(f"Sending request {updateRequest} to UpdateTagsProcessor")
                            addTagUpdateRequest(updateRequest)
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
                            updateRequest: dict = {
                                "sleepTimeForDelay": TIME_WAIT_BEFORE_RESETTING_TORQUE3_VALUES_AFTER_READING_THEM,
                                # Seconds to wait before writing tags
                                "tag1": {"tag": IOServer.PLC_PC_Station3TorqueValueSet, "type": "bool", "value": False},
                                "tag2": {"tag": IOServer.PLC_PC_Station3TorqueValue, "type": "float", "value": 0.0},
                            }
                            print(f"Sending request {updateRequest} to UpdateTagsProcessor")
                            addTagUpdateRequest(updateRequest)
                            printBold(f"Transition from state {self.machineState.getCurrentState().name}")
                            self.machineState.incrementStateFrom(currentState)
                            printBold(f"        ...to state {self.machineState.getCurrentState().name}")

                except Exception as e:
                    printBoldRed(f"Exception {e} encountered in current_read_state - 3")

                returnValue = successfullySent

        if currentState != MachineState.READ_QR_CODE and self.machineState.getCurrentState() == MachineState.READ_QR_CODE:
            # Cycle just completed (or was aborted meanwhile): log machine times, then reset for the next cycle
            if self._cycleInProgress:
                self._computeAndLogCycleTimes()
                self._resetOpTimestamps()
            self.resetForNextCycle("cycle completed" if currentState == MachineState.READ_FREE_ROTATION_TORQUE_1
                                   else f"back at READ_QR_CODE from {currentState.name}")

        return returnValue

    def _writeBoolUntilWritten(self, tag: str, value: bool, stateAtStart: MachineState) -> bool:
        """Write a bool, retrying until it is written. Gives up only on shutdown or if the cycle was reset meanwhile
        (emergency abort / heartbeat loss changed the state); returns False then."""
        attempts = 0
        while not self.shutdown:
            if self.write_bool(tag, value):
                return True
            attempts += 1
            if self.machineState.getCurrentState() != stateAtStart:
                printBoldRed(f"Cycle reset while writing {tag}; not written")
                return False
            if attempts == 1 or attempts % 25 == 0:
                self.logger.error(f"Could not write {tag}={value} (attempt {attempts}); retrying")
                printBoldRed(f"Could not write {tag}={value} (attempt {attempts}); retrying")
            time.sleep(IOServer.WRITE_RETRY_INTERVAL)
        return False

    def perform_write_state_action(self, tags: list[str], result: bool = True,
                                   expectedState: Optional[MachineState] = None) -> bool:
        """
        Handshake of a WRITE state: tags = [<Check>OK, <Check>Done, ...].
        The result (OK tag) is written FIRST and must be written before Done is set: the PLC reads OK when it sees
        Done, so Done is never written unless OK was written. Each write is retried until it succeeds (or the cycle
        is reset by an abort). Any values the PLC needs with this step (e.g. the cycle settings in state 2) must be
        written by the caller BEFORE calling this.
        Returns True if both OK and Done were written.
        """
        stateAtStart = self.machineState.getCurrentState() if expectedState is None else expectedState
        self.cancelVisionResultReset(tags)      # a pending reset of this check must not clear the new result
        if self.machineState.getCurrentState() != stateAtStart:
            # the cycle was aborted after this step's answer was read: nothing is written for the old cycle
            printBoldRed(f"Cycle aborted before {tags[0]} was written; not written")
            return False
        if not self._writeBoolUntilWritten(tags[0], result, stateAtStart):
            self.logger.info(f"Could not write {tags[0]}; {tags[1]} NOT written")
            return False
        printBoldYellow(f"Wrote {result} to {tags[0]}")
        time.sleep(IOServer.PLC_SLEEPTIME_BETWEEN_OK_AND_DONE)
        if self.machineState.getCurrentState() != stateAtStart:
            # aborted between OK and Done: the abort has reset the tags; Done must not be set for the old cycle
            printBoldRed(f"Cycle aborted before {tags[1]} was written; not written")
            return False
        if not self._writeBoolUntilWritten(tags[1], True, stateAtStart):
            self.logger.info(f"Could not write {tags[1]}")
            return False
        printBoldYellow(f"Wrote True to {tags[1]}")
        self.logger.info(f"Performed action for, and wrote to tags {tags}")
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

        self.launchHeartbeatThread()
        printLight("Started heartbeat thread in IOServer")

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
                SlaveFileLogger.getInstance().logAudit(IOServer.logSource, text)
        except Exception:
            noteSuppressed()

    def _auditWrite(self, tag: str, value, error: Optional[Exception] = None) -> None:
        if tag == IOServer.PC_PLC_ConnectionStatusTag and error is None and not auditConnectionToggle():
            return
        if error is None:
            self._audit(f"PC->PLC {tag} = {value!r}  (state {self.machineState.getCurrentState().name})")
        else:
            self._audit(f"PC->PLC {tag} = {value!r} FAILED: {error}  (state {self.machineState.getCurrentState().name})")

    def _auditRead(self, tags, value) -> None:
        """PLC->PC: logged when a trigger is seen raised or a value is read (once per state, not every 0.2 s)."""
        if value is None or value is False or (isinstance(value, float) and value == IOServer.FALSE_FLOAT_VALUE):
            self._lastAuditedRead = None
            return
        key = (self.machineState.getCurrentState(), tuple(tags or ()), value)
        if key == self._lastAuditedRead:
            return
        self._lastAuditedRead = key
        self._audit(f"PLC->PC {' & '.join(tags or ())} = {value!r}  (state {key[0].name})")

    def _auditStateChange(self, oldState, newState) -> None:
        self._audit(f"STATE {getattr(oldState, 'name', oldState)} -> {getattr(newState, 'name', newState)}")

    def _onStateChange(self, oldState, newState) -> None:
        """Every state change: audit trail; and when the machine goes back to READ_QR_CODE the cycle is over -
        completed (from the last state) or aborted (emergency button, heartbeat loss, frontend abort, from any other
        state) - so the camera server is told (end-of-cycle message on its queue, sent after an abort has cleared the
        queues) and drops what it kept for the cycle: the top bearing profile for the nut / washer check."""
        self._auditStateChange(oldState, newState)
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
            if value != IOServer.PLC_SLEEPTIME_BETWEEN_OK_AND_DONE:
                IOServer.PLC_SLEEPTIME_BETWEEN_OK_AND_DONE = value; applied.append(f"PLC_SLEEPTIME_BETWEEN_OK_AND_DONE={value}")
            value = config.getLogDisconnectionsAfterNSecs()
            if value != IOServer.LOG_CONNECTION_PROBLEM_EVERY_N_SECS:
                IOServer.LOG_CONNECTION_PROBLEM_EVERY_N_SECS = value; applied.append(f"LOG_CONNECTION_PROBLEM_EVERY_N_SECS={value}")
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
        """Read a boolean tag from the PLC."""
        if tag is None or tag == "":
            return False
        if (self.clientForReads is None) or (not self.clientForReads.connected):
            return False
        try:
            value: bool = False
            with self.readingLock:
                value = self.clientForReads.read(tag).value
            if value is None:
                self.logger.error("Error reading bool tag %s", tag)
                printBoldRed(f"Error reading bool tag {tag}")
                return not expected_value
            if debug and value == expected_value:
                self.logger.info("Read bool tag %s: %s", tag, value)
            return value
        except Exception as e:
            self.logger.error("Error reading bool tag %s: %s", tag, e)
            printBoldRed(f"Error reading bool tag {tag}: {e}")
            return not expected_value

    def read_float(self, tag: str, debug: bool = False) -> Optional[float]:
        """Read a float tag from the PLC."""
        if tag is None or tag == "":
            return IOServer.FALSE_FLOAT_VALUE
        if (self.clientForReads is None) or (not self.clientForReads.connected):
            return IOServer.FALSE_FLOAT_VALUE
        try:
            value: float = IOServer.FALSE_FLOAT_VALUE
            with self.readingLock:
                value = self.clientForReads.read(tag).value
            if value is None:
                printBoldRed(f"Error reading float tag {tag}")
                return IOServer.FALSE_FLOAT_VALUE
            return value
        except Exception as e:
            printBoldRed(f"Error reading float tag {tag}: {e}")
            return IOServer.FALSE_FLOAT_VALUE

    def read_int(self, tag: str, debug: bool = False) -> Optional[int]:
        """Read an integer tag from the PLC."""
        if tag is None or tag == "":
            return IOServer.FALSE_INT_VALUE
        if (self.clientForReads is None) or (not self.clientForReads.connected):
            return IOServer.FALSE_INT_VALUE
        try:
            value: int = IOServer.FALSE_INT_VALUE
            with self.readingLock:
                value = self.clientForReads.read(tag).value
            if value is None:
                self.logger.error("Error reading integer tag %s: value is None", tag)
                printBoldRed(f"Error reading integer tag {tag}: value is None")
                return IOServer.FALSE_INT_VALUE
            if debug:
                self.logger.info("Read integer tag %s: %d", tag, value)
                printBoldGreen(f"Read integer tag {tag}: {value}")
            return int(value)
        except Exception as e:
            self.logger.error("Error reading integer tag %s: %s", tag, e)
            printBoldRed(f"Error reading integer tag {tag}: {e}")
            return IOServer.FALSE_INT_VALUE

    def read_single(self, aTag: str) -> Any:
        if aTag is None or aTag == "":
            return IOServer.FALSE_FLOAT_VALUE
        if (self.clientForReads is None) or (not self.clientForReads.connected):
            return IOServer.FALSE_FLOAT_VALUE
        value = ""
        try:
            with self.readingLock:
                value = self.clientForReads.read(aTag).value
        except:
            noteSuppressed()
        return value

    def read_multiple(self, listOfTags: list) -> Any:
        if listOfTags is None or len(listOfTags) == 0:
            return None
        if (self.clientForReads is None) or (not self.clientForReads.connected):
            return None
        value = []
        try:
            with self.readingLock:
                value = self.clientForReads.read(*listOfTags)
        except:
            noteSuppressed()
        return value

    def read_array(self, arrayName: str, numberOfElements: int) -> Any:
        if numberOfElements == 0:
            return None
        if (self.clientForReads is None) or (not self.clientForReads.connected):
            return None
        value = []
        try:
            with self.readingLock:
                value = self.clientForReads.read(f"{arrayName}" + "{" + f"{numberOfElements}" + "}")
        except:
            noteSuppressed()
        return value

    def read_array_slice(self, arrayName: str, index: int, numberOfElements: int) -> Any:
        value = []
        try:
            with self.readingLock:
                value = self.clientForReads.read(f"{arrayName}[{index}]" + "{" + f"{numberOfElements}" + "}")
        except:
            noteSuppressed()
        return value

    # ==================== Write functions ====================

    def write_bool(self, tag: str, value: bool, debug: bool = True) -> bool:
        """Write a boolean to a tag to the PLC."""
        if tag is None:
            return False
        if self._writesBlocked():
            return False
        if (self.clientForWrites is None) or (not self.clientForWrites.connected):
            return False
        try:
            with self.writingLock:
                self.clientForWrites.write(tag, value)
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
        if (self.clientForWrites is None) or (not self.clientForWrites.connected):
            return False
        try:
            with self.writingLock:
                self.clientForWrites.write(tag, value)
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
        if (self.clientForWrites is None) or (not self.clientForWrites.connected):
            return False
        try:
            with self.writingLock:
                self.clientForWrites.write(tag, value)
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
            (IOServer.PLC_PC_CheckQRCode, bool), (IOServer.PC_PLC_QRCodeCheckOK, bool), (IOServer.PC_PLC_QRCodeCheckDone, bool),
            (IOServer.PLC_PC_CheckKnuckle, bool), (IOServer.PC_PLC_KnuckleCheckOK, bool), (IOServer.PC_PLC_KnuckleCheckDone, bool),
            (IOServer.PLC_PC_CheckHub, bool), (IOServer.PC_PLC_HubCheckOK, bool), (IOServer.PC_PLC_HubCheckDone, bool),
            (IOServer.PLC_PC_CheckHubAndSecondBearing, bool), (IOServer.PC_PLC_HubAndSecondBearingCheckOK, bool),
            (IOServer.PC_PLC_HubAndSecondBearingCheckDone, bool),
            (IOServer.PLC_PC_CheckNutAndPlateWasher, bool), (IOServer.PC_PLC_NutAndPlateWasherCheckOK, bool),
            (IOServer.PC_PLC_NutAndPlateWasherCheckDone, bool),
            (IOServer.PLC_PC_Station2TorqueValueSet, bool), (IOServer.PLC_PC_Station2TorqueValue, float),
            (IOServer.PLC_PC_Station2RotationDuringTighteningDone, bool),
            (IOServer.PLC_PC_CheckNoCapBunk, bool), (IOServer.PC_PLC_NoCapBunkCheckOK, bool), (IOServer.PC_PLC_NoCapBunkCheckDone, bool),
            (IOServer.PLC_PC_ComponentPressDone, bool),
            (IOServer.PLC_PC_CheckNoBunk, bool), (IOServer.PC_PLC_NoBunkCheckOK, bool), (IOServer.PC_PLC_NoBunkCheckDone, bool),
            (IOServer.PLC_PC_CheckSplitPinAndWasher, bool), (IOServer.PC_PLC_SplitPinAndWasherCheckOK, bool),
            (IOServer.PC_PLC_SplitPinAndWasherCheckDone, bool),
            (IOServer.PLC_PC_CheckCap, bool), (IOServer.PC_PLP_CapCheckOK, bool), (IOServer.PC_PLC_CapCheckDone, bool),
            (IOServer.PLC_PC_CheckBunk, bool), (IOServer.PC_PLC_BunkCheckOK, bool), (IOServer.PC_PLC_BunkCheckDone, bool),
            (IOServer.PLC_PC_CapPressDone, bool),
            (IOServer.PLC_PC_Station3TorqueValueSet, bool), (IOServer.PLC_PC_Station3TorqueValue, float),
            (IOServer.PLC_PC_EmergencyAbort, bool),
        ]
        return [(t, k) for t, k in tags if t]          # an empty name = not on this PLC

    def resetAll(self, debug: bool = False) -> bool:
        """Set every per-cycle tag to its default (bool False, float 0.0), each written and read back
        (_writeVerified). Stops at the first tag that cannot be written because the PLC is not connected.
        Returns True only if every tag was written and verified; the tags that were not are logged."""
        self.currentQRCode = ""
        failed: List[str] = []
        for i, (tag, kind) in enumerate(self.perCycleTags()):
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

    def resetForNextCycle(self, reason: str) -> bool:
        """The one routine for the end of a cycle, every abort and the IOServer start: cancel pending vision-result
        resets, set every per-cycle tag to its default (resetAll, written and read back), then arm the QR code read
        (PLC_PC_CheckQRCode = True, written and read back). The 6 state-2 settings are left as they are.
        If anything could not be written and verified, the reset is marked incomplete and done again as soon as the
        PLC connection is back (_verifyPlcAfterReconnect). Returns True if everything was written and verified."""
        with self._asPrivileged():
            self._resultResetScheduler.cancelAll()
            with self._awaitingTriggerResetLock:
                self._awaitingTriggerReset.clear()
            allReset = self.resetAll()
            qrArmed = self._plcConnected() and self._writeVerified(IOServer.PLC_PC_CheckQRCode, True)
            with self._plcTrueLock:
                self._plcTrueTags = [IOServer.PLC_PC_CheckQRCode] if qrArmed else []
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
        return bool(self.clientForReads and self.clientForReads.connected and
                    self.clientForWrites and self.clientForWrites.connected)

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

    def _readFloatOrNone(self, tag: str) -> Optional[float]:
        """Read a float from the PLC; None if it could not be read."""
        if not tag or self.clientForReads is None or not self.clientForReads.connected:
            return None
        try:
            with self.readingLock:
                value = self.clientForReads.read(tag).value
            return None if value is None else float(value)
        except Exception as e:
            printBoldRed(f"Could not read {tag}: {e}")
            return None

    def _writeVerified(self, tag: str, value, attempts: int = None) -> bool:
        """Write a bool or float and read it back, until the PLC holds the value (up to RESET_WRITE_ATTEMPTS).
        Gives up at once if the PLC is not connected (the caller marks the work as incomplete)."""
        attempts = IOServer.RESET_WRITE_ATTEMPTS if attempts is None else attempts
        isBool = isinstance(value, bool)
        lastSeen = None
        for attempt in range(attempts):
            if self.shutdown or not self._plcConnected():
                return False
            written = self.write_bool(tag, value, debug=False) if isBool else self.write_float(tag, float(value), debug=False)
            if written:
                lastSeen = self._readBoolOrNone(tag) if isBool else self._readFloatOrNone(tag)
                if lastSeen is not None and (lastSeen == value if isBool
                                             else abs(lastSeen - float(value)) <= IOServer.RESET_FLOAT_TOLERANCE):
                    return True
            time.sleep(IOServer.WRITE_RETRY_INTERVAL)
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
            names = {getattr(IOServer, a) for a in dir(IOServer)
                     if a.startswith("PLC_PC_") and isinstance(getattr(IOServer, a), str) and getattr(IOServer, a)}
            names.discard(IOServer.PLC_PC_WatchTag)
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
        cycle carries on in the same machine state. False (or no tag was known to be True) -> the PLC restarted:
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
                for _ in range(IOServer.REFERENCE_READ_ATTEMPTS):
                    value = self._readBoolOrNone(reference)
                    if value is not None:
                        break
                    time.sleep(IOServer.WRITE_RETRY_INTERVAL)
                if value is None:
                    printBoldRed(f"PLC connection back, but {reference} cannot be read yet; checking again")
                    return                           # still pending; the connection check calls this again
            if value is True:
                message = (f"PLC connection back: {reference} is still True, so the PLC did not restart; "
                           f"carrying on in state {state.name}")
                printBoldGreen(message)
                self.logger.info(message)
                self._audit(message)
                self._plcCheckPending = False
                return
            self.plcRestartsDetected += 1
            why = (f"{reference} reads False" if reference is not None else "no PLC tag was known to be True")
            reason = f"PLC restarted while the connection was lost ({why})"
            printBoldRed(f"{reason}; machine state was {state.name}")
            self.logger.error(f"{reason}; machine state was {state.name}")
            if state in (MachineState.READ_QR_CODE,):
                self.resetForNextCycle(reason)       # no cycle in progress: nothing to abort
            else:
                self.abortCycle(reason)             # QR server, queues, frontend, camera (end of cycle), reset
            if not self._resetIncomplete:
                self._plcCheckPending = False

    def _readStation2RotationDuringTighteningDone(self) -> Optional[bool]:
        """Read PLC_PC_Station2RotationDuringTighteningDone, which the PLC provides with the Station 2 torque value.
        Sent to the frontend with each torque value (shown as Rotation Done / Not Done, stored with the record).
        Returns None if this PLC does not provide it (empty tag name, Allen-Bradley)."""
        if not IOServer.PLC_PC_Station2RotationDuringTighteningDone:
            return None
        value = self.read_bool(IOServer.PLC_PC_Station2RotationDuringTighteningDone, debug=False)
        self.lastStation2RotationDuringTighteningDone = value
        printBoldGreen(f"Read Station 2 rotation-during-tightening done: {value}")
        return value

    def getCurrentQRCode(self) -> str:
        """Get the current QR code for this cycle."""
        return self.currentQRCode

    def get_tag_address(self, tag: str) -> int:
        """Placeholder for mapping tags to addresses (not needed for pycomm3)."""
        return 0

    def find_pids(self):
        pid_tags = [
            tag
            for tag, _def in self.clientForReads.tags.items()
            if _def['data_type_name'] == 'PID'
        ]
        return pid_tags

    def find_attributes(self):
        for typ in self.clientForReads.data_types:
            with self.readingLock:
                print(f'{typ} attributes: ', self.clientForReads.data_types[typ]['attributes'])

    def getAllTagValues(self) -> dict:
        """Read all PLC tags and return as a dictionary."""
        if self.clientForReads is None or not self.clientForReads.connected:
            return {}

        tag_values = {}

        # Step 0: QR Code
        tag_values[IOServer.PLC_PC_CheckQRCode] = self.read_bool(IOServer.PLC_PC_CheckQRCode, debug=False)
        tag_values[IOServer.PC_PLC_QRCodeCheckOK] = self.read_bool(IOServer.PC_PLC_QRCodeCheckOK, debug=False)
        tag_values[IOServer.PC_PLC_QRCodeCheckDone] = self.read_bool(IOServer.PC_PLC_QRCodeCheckDone, debug=False)

        # Step 1: Knuckle
        tag_values[IOServer.PLC_PC_CheckKnuckle] = self.read_bool(IOServer.PLC_PC_CheckKnuckle, debug=False)
        tag_values[IOServer.PC_PLC_KnuckleCheckOK] = self.read_bool(IOServer.PC_PLC_KnuckleCheckOK, debug=False)
        tag_values[IOServer.PC_PLC_KnuckleCheckDone] = self.read_bool(IOServer.PC_PLC_KnuckleCheckDone, debug=False)

        # Step 2: Hub
        tag_values[IOServer.PLC_PC_CheckHub] = self.read_bool(IOServer.PLC_PC_CheckHub, debug=False)
        tag_values[IOServer.PC_PLC_HubCheckOK] = self.read_bool(IOServer.PC_PLC_HubCheckOK, debug=False)
        tag_values[IOServer.PC_PLC_HubCheckDone] = self.read_bool(IOServer.PC_PLC_HubCheckDone, debug=False)

        # Step 3: Hub and Second Bearing
        tag_values[IOServer.PLC_PC_CheckHubAndSecondBearing] = self.read_bool(IOServer.PLC_PC_CheckHubAndSecondBearing,
                                                                              debug=False)
        tag_values[IOServer.PC_PLC_HubAndSecondBearingCheckOK] = self.read_bool(
            IOServer.PC_PLC_HubAndSecondBearingCheckOK, debug=False)
        tag_values[IOServer.PC_PLC_HubAndSecondBearingCheckDone] = self.read_bool(
            IOServer.PC_PLC_HubAndSecondBearingCheckDone, debug=False)

        # Step 4: Nut and Plate Washer
        tag_values[IOServer.PLC_PC_CheckNutAndPlateWasher] = self.read_bool(IOServer.PLC_PC_CheckNutAndPlateWasher,
                                                                            debug=False)
        tag_values[IOServer.PC_PLC_NutAndPlateWasherCheckOK] = self.read_bool(IOServer.PC_PLC_NutAndPlateWasherCheckOK,
                                                                              debug=False)
        tag_values[IOServer.PC_PLC_NutAndPlateWasherCheckDone] = self.read_bool(
            IOServer.PC_PLC_NutAndPlateWasherCheckDone, debug=False)

        # Step 5 & 10: Station 2 Torque
        tag_values[IOServer.PLC_PC_Station2TorqueValueSet] = self.read_bool(IOServer.PLC_PC_Station2TorqueValueSet,
                                                                            debug=False)
        tag_values[IOServer.PLC_PC_Station2TorqueValue] = self.read_float(IOServer.PLC_PC_Station2TorqueValue,
                                                                          debug=False)
        if IOServer.PLC_PC_Station2RotationDuringTighteningDone:
            tag_values[IOServer.PLC_PC_Station2RotationDuringTighteningDone] = self.read_bool(
                IOServer.PLC_PC_Station2RotationDuringTighteningDone, debug=False)

        # Step 7: No Cap Bunk
        tag_values[IOServer.PLC_PC_CheckNoCapBunk] = self.read_bool(IOServer.PLC_PC_CheckNoCapBunk, debug=False)
        tag_values[IOServer.PC_PLC_NoCapBunkCheckOK] = self.read_bool(IOServer.PC_PLC_NoCapBunkCheckOK, debug=False)
        tag_values[IOServer.PC_PLC_NoCapBunkCheckDone] = self.read_bool(IOServer.PC_PLC_NoCapBunkCheckDone, debug=False)

        # Step 8: Component Press Done
        tag_values[IOServer.PLC_PC_ComponentPressDone] = self.read_bool(IOServer.PLC_PC_ComponentPressDone, debug=False)

        # Step 9: No Bunk
        tag_values[IOServer.PLC_PC_CheckNoBunk] = self.read_bool(IOServer.PLC_PC_CheckNoBunk, debug=False)
        tag_values[IOServer.PC_PLC_NoBunkCheckOK] = self.read_bool(IOServer.PC_PLC_NoBunkCheckOK, debug=False)
        tag_values[IOServer.PC_PLC_NoBunkCheckDone] = self.read_bool(IOServer.PC_PLC_NoBunkCheckDone, debug=False)

        # Step 11: Split Pin and Washer
        tag_values[IOServer.PLC_PC_CheckSplitPinAndWasher] = self.read_bool(IOServer.PLC_PC_CheckSplitPinAndWasher,
                                                                            debug=False)
        tag_values[IOServer.PC_PLC_SplitPinAndWasherCheckOK] = self.read_bool(IOServer.PC_PLC_SplitPinAndWasherCheckOK,
                                                                              debug=False)
        tag_values[IOServer.PC_PLC_SplitPinAndWasherCheckDone] = self.read_bool(
            IOServer.PC_PLC_SplitPinAndWasherCheckDone, debug=False)

        # Step 12: Cap
        tag_values[IOServer.PLC_PC_CheckCap] = self.read_bool(IOServer.PLC_PC_CheckCap, debug=False)
        tag_values[IOServer.PC_PLP_CapCheckOK] = self.read_bool(IOServer.PC_PLP_CapCheckOK, debug=False)
        tag_values[IOServer.PC_PLC_CapCheckDone] = self.read_bool(IOServer.PC_PLC_CapCheckDone, debug=False)

        # Step 13: Bunk
        tag_values[IOServer.PLC_PC_CheckBunk] = self.read_bool(IOServer.PLC_PC_CheckBunk, debug=False)
        tag_values[IOServer.PC_PLC_BunkCheckOK] = self.read_bool(IOServer.PC_PLC_BunkCheckOK, debug=False)
        tag_values[IOServer.PC_PLC_BunkCheckDone] = self.read_bool(IOServer.PC_PLC_BunkCheckDone, debug=False)

        # Step 14: Cap Press Done
        tag_values[IOServer.PLC_PC_CapPressDone] = self.read_bool(IOServer.PLC_PC_CapPressDone, debug=False)

        # Step 15: Station 3 Torque
        tag_values[IOServer.PLC_PC_Station3TorqueValueSet] = self.read_bool(IOServer.PLC_PC_Station3TorqueValueSet,
                                                                            debug=False)
        tag_values[IOServer.PLC_PC_Station3TorqueValue] = self.read_float(IOServer.PLC_PC_Station3TorqueValue,
                                                                          debug=False)

        # System tags
        tag_values[IOServer.PLC_PC_EmergencyAbort] = self.read_bool(IOServer.PLC_PC_EmergencyAbort, debug=False)
        tag_values[IOServer.PLC_PC_WatchTag] = self.read_int(IOServer.PLC_PC_WatchTag, debug=False)

        # Machine 2 cycle settings written by the PC in state 2
        for tag, reader in ((IOServer.PC_PLC_LH_RH_Selection, self.read_int),
                            (IOServer.PC_PLC_ModelSelection, self.read_int),
                            (IOServer.PC_PLC_TighteningTorque1Setpoint, self.read_float),
                            (IOServer.PC_PLC_TighteningTorque2Setpoint, self.read_float),
                            (IOServer.PC_PLC_RotationRPM1, self.read_int),
                            (IOServer.PC_PLC_RotationRPM2, self.read_int),
                            (IOServer.PC_PLC_RotationRPM, self.read_float),
                            (IOServer.PC_PLC_NoOfRotation1CCW, self.read_float),
                            (IOServer.PC_PLC_NoOfRotation1CW, self.read_float)):
            if tag:                      # empty name = not on this PLC
                tag_values[tag] = reader(tag, debug=False)
        tag_values[IOServer.PC_PLC_ConnectionStatusTag] = self.read_bool(IOServer.PC_PLC_ConnectionStatusTag, debug=False)

        # Add current machine state (enum name: str(IntEnum) is just "1" on Python 3.11+)
        tag_values["_currentMachineState"] = self.machineState.getCurrentState().name

        return tag_values

    def launchTagRequestListenerThread(self):
        """Launch thread that listens for tag publish requests on Redis queue."""
        import json
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

                except Exception as e:
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
            except:
                noteSuppressed()
        if self.clientForReads and self.clientForReads.connected:
            try:
                self.clientForReads.close()
            except:
                noteSuppressed()
            try:
                self.clientForWrites.close()
            except:
                noteSuppressed()
        self.logger.info(f"IOServer shutdown")
        printBoldRed(f"IOServer shutdown")


# *********************************************************************

def startIOServer(mode : str = "TEST"):
    io = IOServer(mode = mode)
    printBoldBlue(f"*****************")
    printBlue(f"Started IO Server")
    printBoldBlue(f"*****************")
    io.heartbeatThread.join()
    io.monitorStopThread.join()
    sys.exit(0)


# *********************************************************************

# if __name__ == "__main__":
#     startIOServer()