import random
import sys
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
from utils.CosThetaPrintUtils import *
from utils.RedisUtils import *

from Configuration import CosThetaConfigurator
from statemachine.StateMachine import MachineState, MachineStateMachine
from processors.GenericQueueProcessor import GenericQueueProcessor
from utils.QRCodeHelper import getModel_LHSRHS_AndTonnage
from persistence.Persistence import getMachineSettings, getDatabaseName, setDatabaseName

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
    PLC_PC_Station2TorqueValue: str = CosThetaConfigurator.getInstance().getPlcPcStation2TorqueValueTagName()

    # ==================== Station 3 Free Rotation Done Tag (Step 6) ====================
    PLC_PC_Station3RotationDone: str = CosThetaConfigurator.getInstance().getPlcPcStation3RotationDoneTagName()

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

    # ==================== Rotation Settings Tags (PC writes to PLC) ====================
    PC_PLC_NoOfRotation1CCW: str = CosThetaConfigurator.getInstance().getPcPlcNoOfRotation1CCWTagName()
    PC_PLC_NoOfRotation1CW: str = CosThetaConfigurator.getInstance().getPcPlcNoOfRotation1CWTagName()
    PC_PLC_NoOfRotation2CCW: str = CosThetaConfigurator.getInstance().getPcPlcNoOfRotation2CCWTagName()
    PC_PLC_NoOfRotation2CW: str = CosThetaConfigurator.getInstance().getPcPlcNoOfRotation2CWTagName()
    PC_PLC_LH_RH_Selection: str = CosThetaConfigurator.getInstance().getPcPlcLHRHSelectionTagName()
    PC_PLC_RotationUnitRPM: str = CosThetaConfigurator.getInstance().getPcPlcRotationUnitRPMTagName()

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
    NO_CONNECTION: int = -1986754392
    CONSECUTIVE_CONNECTION_DOWN_COUNT: int = 0

    logSource = getFullyQualifiedName(__file__)
    LAST_CONNECTION_STATUS_UPDATED: bool = False
    LOG_CONNECTION_PROBLEM_EVERY_N_SECS = CosThetaConfigurator.getInstance().getLogDisconnectionsAfterNSecs()

    def __init__(self, plc_ip: Union[str, None] = None, mode : str = "TEST", mockInstance: bool = False):
        # Class variables
        IOServer.logSource = getFullyQualifiedName(__file__, __class__)
        self.machineState = MachineStateMachine()  # Instance of MachineStateMachine
        self.shutdown = False

        self.mode = mode
        setDatabaseName(mode = self.mode, createDB=False)

        self.read_interval = CosThetaConfigurator.getInstance().getPlcReadInterval()  # 200 ms
        self.write_loop_interval = CosThetaConfigurator.getInstance().getPlcWriteInterval()  # 100 ms
        self.CONNECTION_CHECK_INTERVAL = CosThetaConfigurator.getInstance().getPlcConnectionCheckInterval()  # seconds

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

        # Database connection pool for reading machine settings
        self.dbConnectionPool: Union[ThreadedConnectionPool, None] = None
        self._initDatabaseConnectionPool()

        # Cycle time tracking
        self._cycleTimestamps: Dict[int, float] = {}
        self._cycleInProgress: bool = False

        # Current QR Code for this cycle
        self.currentQRCode: str = ""

        # Initialize logging
        self.logger = logging.getLogger("pycomm3")
        self.logger.setLevel(logging.CRITICAL)

        # Start PLC connection and threads
        self.connectionInProgress: bool = False
        self.connectionAttempts: int = 0
        self.connectPLC()

        time.sleep(2)
        self.resetAll()

        time.sleep(3)
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

    def writeRotationSettingsToPLC(self, qrCode: str) -> bool:
        """
        Write rotation settings to PLC based on QR code (LHS vs RHS).

        Args:
            qrCode: The scanned QR code string

        Returns:
            True if settings were written successfully, False otherwise
        """
        if not qrCode:
            printBoldRed("Cannot write rotation settings: QR code is empty")
            return False

        try:
            # Parse QR code to get LHS/RHS designation
            modelName, lhs_rhs, tonnage = getModel_LHSRHS_AndTonnage(qrCode)

            if lhs_rhs == "UNKNOWN":
                printBoldRed(f"Cannot determine LHS/RHS from QR code: {qrCode}")
                return False

            # Get machine settings from database
            settings = getMachineSettings(getDatabaseName())
            if not settings:
                printBoldRed("Failed to get machine settings from database")
                return False

            noOfRotation1CCW_db = settings.get('NoOfRotation1CCW', 5)
            noOfRotation1CW_db = settings.get('NoOfRotation1CW', 5)
            noOfRotation2CCW_db = settings.get('NoOfRotation2CCW', 5)
            noOfRotation2CW_db = settings.get('NoOfRotation2CW', 5)
            rotationUnitRPM = settings.get('RotationUnitRPM', 60)

            # Determine values based on LHS/RHS
            if lhs_rhs.upper() == "LHS":
                noOfRotation1CCW = 0
                noOfRotation1CW = noOfRotation1CW_db
                noOfRotation2CCW = 0
                noOfRotation2CW = noOfRotation2CW_db
                lhRhSelection = 1
                printBoldGreen(f"LHS detected - Setting CW rotations: R1CW={noOfRotation1CW}, R2CW={noOfRotation2CW}, lhRhSelection={lhRhSelection}")
            elif lhs_rhs.upper() == "RHS":
                noOfRotation1CCW = noOfRotation1CCW_db
                noOfRotation1CW = 0
                noOfRotation2CCW = noOfRotation2CCW_db
                noOfRotation2CW = 0
                lhRhSelection = 2
                printBoldGreen(
                    f"RHS detected - Setting CCW rotations: R1CCW={noOfRotation1CCW}, R2CCW={noOfRotation2CCW}, lhRhSelection={lhRhSelection}")
            else:
                printBoldRed(f"Unknown LHS/RHS value: {lhs_rhs}")
                return False

            # Write all values to PLC
            success = True
            success = success and self.write_int(IOServer.PC_PLC_NoOfRotation1CCW, noOfRotation1CCW)
            success = success and self.write_int(IOServer.PC_PLC_NoOfRotation1CW, noOfRotation1CW)
            success = success and self.write_int(IOServer.PC_PLC_NoOfRotation2CCW, noOfRotation2CCW)
            success = success and self.write_int(IOServer.PC_PLC_NoOfRotation2CW, noOfRotation2CW)
            success = success and self.write_int(IOServer.PC_PLC_LH_RH_Selection, lhRhSelection)
            success = success and self.write_int(IOServer.PC_PLC_RotationUnitRPM, rotationUnitRPM)

            if success:
                printBoldGreen(f"Successfully wrote rotation settings to PLC for {lhs_rhs}")
            else:
                printBoldRed("Failed to write some rotation settings to PLC")

            return success

        except Exception as e:
            printBoldRed(f"Exception writing rotation settings to PLC: {e}")
            return False

    # ==================== Cycle Time Tracking ====================

    def _recordStateEntry(self):
        """Record the timestamp when entering the current state for cycle time tracking."""
        currentStateValue = MachineState.getMachineStateAsInt(self.machineState.getCurrentState())
        self._cycleTimestamps[currentStateValue] = time.time()

        # If entering state 1 and we have data from previous states, compute cycle times
        if currentStateValue == 1 and self._cycleInProgress:
            self._computeAndLogCycleTimes()
            self._cycleTimestamps = {}
            self._cycleInProgress = False
        elif currentStateValue > 1:
            self._cycleInProgress = True

    def _computeAndLogCycleTimes(self):
        """Compute and log cycle times for machine operations at end of cycle."""
        ts = self._cycleTimestamps

        def getTimeDiff(startState: int, endState: int) -> float:
            if startState in ts and endState in ts:
                return ts[endState] - ts[startState]
            return 0.0

        # Calculate individual times
        T1 = getTimeDiff(3, 4)  # State 3 to 4: Knuckle check
        T2 = getTimeDiff(5, 6)  # State 5 to 6: Hub and bottom bearing
        T3 = getTimeDiff(7, 8)  # State 7 to 8: Top bearing
        T4 = getTimeDiff(9, 10)  # State 9 to 10: Nut and plate washer
        T5 = getTimeDiff(10, 13)  # State 10 to 13: Torque 1 + free rotations
        T6 = getTimeDiff(14, 15)  # State 14 to 15: Bunk for component press
        T7 = getTimeDiff(15, 16)  # State 15 to 16: Component press
        T8 = getTimeDiff(17, 18)  # State 17 to 18: No bunk check
        T9 = getTimeDiff(18, 20)  # State 18 to 20: Torque 2
        T10 = getTimeDiff(21, 22)  # State 21 to 22: Split pin and washer
        T11 = getTimeDiff(23, 24)  # State 23 to 24: Cap
        T12 = getTimeDiff(25, 26)  # State 25 to 26: Bunk for cap press
        T13 = getTimeDiff(26, 29)  # State 26 to 29: Cap press + free rotation torque

        # Calculate totals
        sumOfTimes = T1 + T2 + T3 + T4 + T5 + T6 + T7 + T8 + T9 + T10 + T11 + T12 + T13
        sumPlusExtraT7 = sumOfTimes + T7  # Add another T7 as per requirement
        fixedTime = 11  # 3 * 3.5 seconds rounded
        totalMachineTime = sumPlusExtraT7 + fixedTime

        # Build the log message
        cycleTimeMsg = (
            f"\n{'=' * 60}\n"
            f"CYCLE TIME BREAKDOWN (Machine Operations)\n"
            f"{'=' * 60}\n"
            f"T1  (State 3→4  : Knuckle check)              : {T1:.3f} sec\n"
            f"T2  (State 5→6  : Hub & bottom bearing)       : {T2:.3f} sec\n"
            f"T3  (State 7→8  : Top bearing)                : {T3:.3f} sec\n"
            f"T4  (State 9→10 : Nut & plate washer)         : {T4:.3f} sec\n"
            f"T5  (State 10→13: Torque 1 + free rotations)  : {T5:.3f} sec\n"
            f"T6  (State 14→15: Bunk for component press)   : {T6:.3f} sec\n"
            f"T7  (State 15→16: Component press)            : {T7:.3f} sec\n"
            f"T8  (State 17→18: No bunk check)              : {T8:.3f} sec\n"
            f"T9  (State 18→20: Torque 2)                   : {T9:.3f} sec\n"
            f"T10 (State 21→22: Split pin & washer)         : {T10:.3f} sec\n"
            f"T11 (State 23→24: Cap)                        : {T11:.3f} sec\n"
            f"T12 (State 25→26: Bunk for cap press)         : {T12:.3f} sec\n"
            f"T13 (State 26→29: Cap press + rotation torque): {T13:.3f} sec\n"
            f"{'-' * 60}\n"
            f"Sum of T1-T13                                 : {sumOfTimes:.3f} sec\n"
            f"Plus extra T7                                 : +{T7:.3f} sec\n"
            f"Fixed time (3 x 3.5 sec rounded)              : +{fixedTime} sec\n"
            f"{'=' * 60}\n"
            f"TOTAL MACHINE TIME                            : {totalMachineTime:.3f} sec\n"
            f"{'=' * 60}\n"
        )

        # Print to terminal
        printBoldYellow(cycleTimeMsg)

        # Log as ERROR level (VERY IMPORTANT DATA)
        SlaveConsoleLogger.getInstance().logTakeAction(IOServer.logSource, cycleTimeMsg, Logger.PROBLEM)
        SlaveFileLogger.getInstance().logTakeAction(IOServer.logSource, cycleTimeMsg, Logger.PROBLEM)

    def getCurrentTagsToBeRead(self) -> Tuple[list[str] | None, list[Type[bool | float] | None] | None]:
        """
        Get the PLC tags to be read based on current machine state.

        State to Excel Step Mapping:
        - States 1-2: QR Code (Step 0)
        - States 3-4: Knuckle (Step 1)
        - States 5-6: Hub (Step 2) - uses PLC_PC_CheckHub
        - States 7-8: Hub and Second Bearing (Step 3) - uses PLC_PC_CheckHubAndSecondBearing
        - States 9-10: Nut and Plate Washer (Step 4)
        - States 11-12: Tightening Torque 1 (Step 5) - uses PLC_PC_Station2TorqueValueSet
        - State 13: Free Rotations Done (Step 6)
        - States 14-15: Bunk for Component Press (Step 7) - uses PLC_PC_CheckBunk
        - State 16: Component Press Done (Step 8)
        - States 17-18: No Bunk (Step 9)
        - States 19-20: Tightening Torque 2 (Step 10) - uses PLC_PC_Station2TorqueValueSet (reused)
        - States 21-22: Split Pin and Washer (Step 11)
        - States 23-24: Cap (Step 12)
        - States 25-26: Bunk for Cap Press (Step 13) - uses PLC_PC_CheckBunk (reused)
        - State 27: Cap Press Done (Step 14)
        - States 28-29: Free Rotation Torque (Step 15)
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

        # Step 6: Free Rotations Done (Station 3)
        if currentState == MachineState.READ_FREE_ROTATIONS_DONE:
            return [IOServer.PLC_PC_Station3RotationDone], [bool]

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
                        pass
                if self.clientForWrites is not None:
                    try:
                        self.clientForWrites.close()
                    except:
                        pass
                self.clientForReads = None
                self.clientForWrites = None

            # Use MockPLC if mockInstance is True
            if self.mockInstance:
                from MockPLC import MockPLC
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
                pass
            try:
                self.clientForWrites.close()
            except:
                pass
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
                    objRef.connectPLC(forceRenew=True)

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
                    pass

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
                            sendEmergencyAbortFromIOServerToFEServer(objRef.emergencyRedisConnection)
                            objRef.resetAll()
                            objRef._cycleTimestamps = {}  # Clear timestamps on abort
                            objRef._cycleInProgress = False
                            objRef.machineState.setCurrentState(MachineState.READ_QR_CODE)
                            objRef.write_bool(IOServer.PLC_PC_CheckQRCode, True)
                except Exception as e:
                    printBoldRed(f"Exception {e} encountered in heartbeat")
                    objRef.connectToRedisForHeartbeatAndEmergencyMonitoring(forceRenew=True)
                try:
                    time.sleep(heartbeatGap)
                except:
                    pass

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
                    sendAbortFromIOServerToQRCodeServer(objRef.emergencyRedisConnection)
                    time.sleep(0.5)
                    clearKeyCommunicationQueuesOnAbort()
                    time.sleep(0.2)
                    sendEmergencyAbortFromIOServerToFEServer(objRef.emergencyRedisConnection)
                    objRef.resetTagToFalse(IOServer.PLC_PC_EmergencyAbort)
                    objRef.resetAll()
                    objRef._cycleTimestamps = {}  # Clear timestamps on abort
                    objRef._cycleInProgress = False
                    objRef.machineState.setCurrentState(MachineState.READ_QR_CODE)
                    objRef.write_bool(IOServer.PLC_PC_CheckQRCode, True)
                try:
                    time.sleep(1.0)
                except:
                    pass

        emergencyMonitoringThread = threading.Thread(name=f"Emergency Monitoring Thread", target=monitorEmergency,
                                                     args=(), daemon=True)
        emergencyMonitoringThread.start()

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
                    pass
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
                            and not objRef.connectionInProgress):
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
                tagsToBeWritten = objRef.getCurrentTagsToBeWritten()
                if (
                        objRef.clientForWrites is not None) and objRef.clientForWrites.connected and not objRef.connectionInProgress:
                    if tagsToBeWritten is not None and len(tagsToBeWritten) > 0:
                        if written_TagsToBeWritten == 0:
                            written_TagsToBeWritten += 1
                        if self.machineState.getCurrentState() == MachineState.WRITE_QR_CODE:
                            success, qrCode = readDataInIOServerFromQRCodeServer(self.redisConnectionForWrites)
                            if success:
                                objRef.currentQRCode = qrCode  # Store the QR code for this cycle
                                printBoldGreen(f"Received QR Code: {qrCode}")
                                objRef.perform_write_state_action(tags=tagsToBeWritten, result=True)
                                # Write rotation settings to PLC based on LHS/RHS
                                objRef.writeRotationSettingsToPLC(qrCode)
                                printBold(f"Transition from state {objRef.machineState.getCurrentState().name}")
                                objRef.machineState.incrementState()
                                printBold(f"        ...to state {objRef.machineState.getCurrentState().name}")
                                objRef._recordStateEntry()
                        else:
                            previousMachineState, evaluation = readDataInIOServerFromCameraServer(
                                self.redisConnectionForWrites)
                            if evaluation:
                                objRef.perform_write_state_action(tags=tagsToBeWritten, result=True)
                                printBold(f"Transition from state {objRef.machineState.getCurrentState().name}")
                                objRef.machineState.incrementState()
                                printBold(f"        ...to state {objRef.machineState.getCurrentState().name}")
                                objRef._recordStateEntry()
                            else:
                                if previousMachineState != MachineState.INVALID_STATE:
                                    objRef.perform_write_state_action(tags=tagsToBeWritten, result=False)
                                    printBoldRed(
                                        f"Did not move ahead from state {objRef.machineState.getCurrentState().name}")
                                    printBold(f"Transition from state {objRef.machineState.getCurrentState().name}")
                                    objRef.machineState.decrementState()
                                    printBold(f"        ...to state {objRef.machineState.getCurrentState().name}")
                                    objRef._recordStateEntry()
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
            if valueRead:
                successfullySent: bool = False
                try:
                    # Step 0: QR Code
                    if currentState == MachineState.READ_QR_CODE:
                        successfullySent = sendDataFromIOServerToQRCodeServer(self.redisConnectionForReads)

                    # Step 1: Knuckle
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads,
                                                                              currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE)

                    # Step 2: Hub (HUB_AND_BOTTOM_BEARING state)
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads,
                                                                              currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING)

                    # Step 3: Hub and Second Bearing (TOP_BEARING state)
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads,
                                                                              currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING)

                    # Step 4: Nut and Plate Washer
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads,
                                                                              currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER)

                    # Step 5: Tightening Torque 1 Done
                    elif currentState == MachineState.READ_TIGHTENING_TORQUE_1_DONE:
                        successfullySent = True

                    # Step 6: Free Rotations Done
                    elif currentState == MachineState.READ_FREE_ROTATIONS_DONE:
                        successfullySent = sendDataFromIOServerToFEServer(self.redisConnectionForReads,
                                                                          currentMachineState=MachineState.READ_FREE_ROTATIONS_DONE)
                        # Reset Station 3 tags - prepare for eventual use in State 29
                        updateRequest: dict = {
                            "sleepTimeForDelay": TIME_WAIT_BEFORE_RESETTING_TORQUE3_VALUES_AFTER_READING_THEM,
                            "tag1": {"tag": IOServer.PLC_PC_Station3TorqueValueSet, "type": "bool", "value": False},
                            "tag2": {"tag": IOServer.PLC_PC_Station3TorqueValue, "type": "float", "value": 0.0},
                        }
                        print(f"Sending request {updateRequest} to UpdateTagsProcessor")
                        addTagUpdateRequest(updateRequest)

                    # Step 7: Bunk for Component Press
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads,
                                                                              currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS)

                    # Step 8: Component Press Done
                    elif currentState == MachineState.READ_COMPONENT_PRESS_DONE:
                        successfullySent = sendDataFromIOServerToFEServer(self.redisConnectionForReads,
                                                                          currentMachineState=MachineState.READ_COMPONENT_PRESS_DONE)

                    # Step 9: No Bunk
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads,
                                                                              currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK)

                    # Step 10: Tightening Torque 2 Done
                    elif currentState == MachineState.READ_TIGHTENING_TORQUE_2_DONE:
                        successfullySent = True

                    # Step 11: Split Pin and Washer
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads,
                                                                              currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER)

                    # Step 12: Cap
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads,
                                                                              currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP)

                    # Step 13: Bunk for Cap Press
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads,
                                                                              currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS)

                    # Step 14: Cap Press Done
                    elif currentState == MachineState.READ_CAP_PRESS_DONE:
                        successfullySent = sendDataFromIOServerToFEServer(self.redisConnectionForReads,
                                                                          currentMachineState=MachineState.READ_CAP_PRESS_DONE)

                    # Step 15: Free Rotation Torque 1 Done
                    elif currentState == MachineState.READ_FREE_ROTATION_TORQUE_1_DONE:
                        successfullySent = True

                    if successfullySent:
                        printBold(f"Transition from state {self.machineState.getCurrentState().name}")
                        self.machineState.incrementState()
                        printBold(f"        ...to state {self.machineState.getCurrentState().name}")
                        self._recordStateEntry()

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
                        successfullySent = sendDataFromIOServerToFEServer(self.redisConnectionForReads,
                                                                          currentMachineState=MachineState.READ_TIGHTENING_TORQUE_1,
                                                                          value=valueRead)
                        # Reset Station 2 torque tags after reading (so Step 10 doesn't auto-trigger)
                        if successfullySent:
                            updateRequest : dict = {
                                "sleepTimeForDelay": TIME_WAIT_BEFORE_RESETTING_TORQUE2_VALUES_AFTER_READING_THEM,  # Seconds to wait before writing tags
                                "tag1": {"tag": IOServer.PLC_PC_Station2TorqueValueSet, "type": "bool", "value": False},
                                "tag2": {"tag": IOServer.PLC_PC_Station2TorqueValue, "type": "float", "value": 0.0},
                            }
                            print(f"Sending request {updateRequest} to UpdateTagsProcessor")
                            addTagUpdateRequest(updateRequest)
                            # Transition to next state after successfully reading torque
                            printBold(f"Transition from state {self.machineState.getCurrentState().name}")
                            self.machineState.incrementState()
                            printBold(f"        ...to state {self.machineState.getCurrentState().name}")
                            self._recordStateEntry()
                    # Step 10B: Tightening Torque 2 Value
                    elif currentState == MachineState.READ_TIGHTENING_TORQUE_2:
                        printBoldGreen(f"Read Station 2 Torque Value (2nd time): {valueRead}")
                        successfullySent = sendDataFromIOServerToFEServer(self.redisConnectionForReads,
                                                                          currentMachineState=MachineState.READ_TIGHTENING_TORQUE_2,
                                                                          value=valueRead)
                        if successfullySent:
                            updateRequest : dict = {
                                "sleepTimeForDelay": TIME_WAIT_BEFORE_RESETTING_TORQUE2_VALUES_AFTER_READING_THEM,  # Seconds to wait before writing tags
                                "tag1": {"tag": IOServer.PLC_PC_Station2TorqueValueSet, "type": "bool", "value": False},
                                "tag2": {"tag": IOServer.PLC_PC_Station2TorqueValue, "type": "float", "value": 0.0},
                            }
                            print(f"Sending request {updateRequest} to UpdateTagsProcessor")
                            addTagUpdateRequest(updateRequest)
                            # Transition to next state after successfully reading torque
                            printBold(f"Transition from state {self.machineState.getCurrentState().name}")
                            self.machineState.incrementState()
                            printBold(f"        ...to state {self.machineState.getCurrentState().name}")
                            self._recordStateEntry()

                    # Step 15B: Free Rotation Torque 1 Value - FINAL STATE
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
                            self.machineState.incrementState()
                            printBold(f"        ...to state {self.machineState.getCurrentState().name}")
                            self._recordStateEntry()

                except Exception as e:
                    printBoldRed(f"Exception {e} encountered in current_read_state - 3")

                returnValue = successfullySent

        if self.machineState.getCurrentState() == MachineState.READ_QR_CODE:
            self.resetAll()
            self.write_bool(IOServer.PLC_PC_CheckQRCode, True)

        return returnValue

    def perform_write_state_action(self, tags: list[str], result: bool = True) -> bool:
        """Perform the write action to PLC tags."""
        print("Entered perform_write_state_action()")
        success1 = self.write_bool(tags[0], result)
        printBoldYellow(f"Wrote {result} to {tags[0]}")
        time.sleep(IOServer.PLC_SLEEPTIME_BETWEEN_OK_AND_DONE)
        success2 = self.write_bool(tags[1], True)
        printBoldYellow(f"Wrote True to {tags[1]}")

        if success1 and success2:
            self.logger.info(f"Performed action for, and wrote to tags {tags}")
        else:
            self.logger.info(f"Could not write to tags {tags}")
            printBoldRed(f"Could not write to tags {tags}")

        return result

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
            pass
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
            pass
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
            pass
        return value

    def read_array_slice(self, arrayName: str, index: int, numberOfElements: int) -> Any:
        value = []
        try:
            with self.readingLock:
                value = self.clientForReads.read(f"{arrayName}[{index}]" + "{" + f"{numberOfElements}" + "}")
        except:
            pass
        return value

    # ==================== Write functions ====================

    def write_bool(self, tag: str, value: bool, debug: bool = True) -> bool:
        """Write a boolean to a tag to the PLC."""
        if tag is None:
            return False
        if (self.clientForWrites is None) or (not self.clientForWrites.connected):
            return False
        try:
            with self.writingLock:
                self.clientForWrites.write(tag, value)
            if debug:
                printBoldYellow(f"Wrote to bool tag {tag}: {value} in write_bool()")
            return True
        except Exception as e:
            printBoldRed(f"Error writing bool tag {tag}: {e}")
            return False

    def write_float(self, tag: str, value: float, debug: bool = True) -> bool:
        """Write a float to a tag to the PLC."""
        if tag is None:
            return False
        if (self.clientForWrites is None) or (not self.clientForWrites.connected):
            return False
        try:
            with self.writingLock:
                self.clientForWrites.write(tag, value)
            if debug:
                self.logger.info(f"Wrote float tag {tag}: {value}")
                printBoldYellow(f"Wrote to float tag {tag}: {value} in write_float()")
            return True
        except Exception as e:
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
        if (self.clientForWrites is None) or (not self.clientForWrites.connected):
            return False
        try:
            with self.writingLock:
                self.clientForWrites.write(tag, value)
            if debug:
                self.logger.info(f"Wrote integer tag {tag}: {value}")
                printBoldGreen(f"Wrote integer tag {tag}: {value}")
            return True
        except Exception as e:
            self.logger.error(f"Error writing integer tag {tag}: {e}")
            printBoldRed(f"Error writing integer tag {tag}: {e}")
            return False

    def resetTagToFalse(self, tag: str, debug: bool = False) -> bool:
        """Reset a tag to False."""
        return self.write_bool(tag=tag, value=False, debug=debug)

    def resetTagToZero(self, tag: str, debug: bool = False) -> bool:
        """Reset a tag to 0.0."""
        return self.write_float(tag=tag, value=0.0, debug=debug)

    def resetAll(self, debug: bool = False) -> bool:
        """Reset all tags to False if bool, or to 0.0 if float."""

        # Reset current QR Code
        self.currentQRCode = ""

        # Step 0: QR Code
        self.resetTagToFalse(IOServer.PLC_PC_CheckQRCode)
        self.resetTagToFalse(IOServer.PC_PLC_QRCodeCheckOK)
        self.resetTagToFalse(IOServer.PC_PLC_QRCodeCheckDone)

        # Step 1: Knuckle
        self.resetTagToFalse(IOServer.PLC_PC_CheckKnuckle)
        self.resetTagToFalse(IOServer.PC_PLC_KnuckleCheckOK)
        self.resetTagToFalse(IOServer.PC_PLC_KnuckleCheckDone)

        # Step 2: Hub
        self.resetTagToFalse(IOServer.PLC_PC_CheckHub)
        self.resetTagToFalse(IOServer.PC_PLC_HubCheckOK)
        self.resetTagToFalse(IOServer.PC_PLC_HubCheckDone)

        # Step 3: Hub and Second Bearing
        self.resetTagToFalse(IOServer.PLC_PC_CheckHubAndSecondBearing)
        self.resetTagToFalse(IOServer.PC_PLC_HubAndSecondBearingCheckOK)
        self.resetTagToFalse(IOServer.PC_PLC_HubAndSecondBearingCheckDone)

        # Step 4: Nut and Plate Washer
        self.resetTagToFalse(IOServer.PLC_PC_CheckNutAndPlateWasher)
        self.resetTagToFalse(IOServer.PC_PLC_NutAndPlateWasherCheckOK)
        self.resetTagToFalse(IOServer.PC_PLC_NutAndPlateWasherCheckDone)

        # Step 5 & 10: Station 2 Torque
        self.resetTagToFalse(IOServer.PLC_PC_Station2TorqueValueSet)
        self.resetTagToZero(IOServer.PLC_PC_Station2TorqueValue)

        # Step 6: Station 3 Rotation Done
        self.resetTagToFalse(IOServer.PLC_PC_Station3RotationDone)

        # Step 7: No Cap Bunk
        self.resetTagToFalse(IOServer.PLC_PC_CheckNoCapBunk)
        self.resetTagToFalse(IOServer.PC_PLC_NoCapBunkCheckOK)
        self.resetTagToFalse(IOServer.PC_PLC_NoCapBunkCheckDone)

        # Step 8: Component Press Done
        self.resetTagToFalse(IOServer.PLC_PC_ComponentPressDone)

        # Step 9: No Bunk
        self.resetTagToFalse(IOServer.PLC_PC_CheckNoBunk)
        self.resetTagToFalse(IOServer.PC_PLC_NoBunkCheckOK)
        self.resetTagToFalse(IOServer.PC_PLC_NoBunkCheckDone)

        # Step 11: Split Pin and Washer
        self.resetTagToFalse(IOServer.PLC_PC_CheckSplitPinAndWasher)
        self.resetTagToFalse(IOServer.PC_PLC_SplitPinAndWasherCheckOK)
        self.resetTagToFalse(IOServer.PC_PLC_SplitPinAndWasherCheckDone)

        # Step 12: Cap
        self.resetTagToFalse(IOServer.PLC_PC_CheckCap)
        self.resetTagToFalse(IOServer.PC_PLP_CapCheckOK)
        self.resetTagToFalse(IOServer.PC_PLC_CapCheckDone)

        # Step 13: Bunk (used for both bunk checks)
        self.resetTagToFalse(IOServer.PLC_PC_CheckBunk)
        self.resetTagToFalse(IOServer.PC_PLC_BunkCheckOK)
        self.resetTagToFalse(IOServer.PC_PLC_BunkCheckDone)

        # Step 14: Cap Press Done
        self.resetTagToFalse(IOServer.PLC_PC_CapPressDone)

        # Step 15: Station 3 Torque
        self.resetTagToFalse(IOServer.PLC_PC_Station3TorqueValueSet)
        self.resetTagToZero(IOServer.PLC_PC_Station3TorqueValue)

        # Emergency Abort
        self.resetTagToFalse(IOServer.PLC_PC_EmergencyAbort)

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

        # Step 6: Station 3 Rotation Done
        tag_values[IOServer.PLC_PC_Station3RotationDone] = self.read_bool(IOServer.PLC_PC_Station3RotationDone,
                                                                          debug=False)

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

        # Add current machine state
        tag_values["_currentMachineState"] = str(self.machineState.getCurrentState())

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
                    pass  # Silently ignore errors to not disrupt main operation

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
                pass
        if self.clientForReads and self.clientForReads.connected:
            try:
                self.clientForReads.close()
            except:
                pass
            try:
                self.clientForWrites.close()
            except:
                pass
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