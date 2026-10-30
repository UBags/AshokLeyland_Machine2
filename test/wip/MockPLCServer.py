"""
MockPLC IOServer - A version of IOServer that uses simulated PLC tags.
Use this for testing without actual PLC hardware.

This creates a global MockPLC instance that can be controlled via:
1. The TestIOServerWithMockPLC.py GUI
2. Direct API calls

Usage:
    from MockPLCIOServer import startMockIOServer, getMockPLC

    # Start the mock IOServer
    startMockIOServer()

    # Get the mock PLC to set tag values
    plc = getMockPLC()
    plc.write("PLC_PC_CheckQRCode", True)
"""

import threading
import time
import logging
from typing import Any, Optional, Tuple, Type, Union
from redis import Redis

from logutils import Logger
from logutils.SlaveLoggers import SlaveConsoleLogger, SlaveFileLogger
from utils.CosThetaPrintUtils import *
from utils.RedisUtils import *

from Configuration import CosThetaConfigurator
from statemachine.StateMachine import MachineState, MachineStateMachine


# =============================================================================
# MOCK PLC CLASS
# =============================================================================

class MockPLC:
    """Simulates PLC tag read/write operations using an in-memory dictionary."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.tags = {}
        self.tag_lock = threading.Lock()
        self.connected = True
        self._initialize_tags()

    def _initialize_tags(self):
        """Initialize all tags to default values."""
        defaults = {
            # QR Code (Step 0)
            "PLC_PC_CheckQRCode": False,
            "PC_PLC_QRCodeCheckOK": False,
            "PC_PLC_QRCodeCheckDone": False,
            # Knuckle (Step 1)
            "PLC_PC_CheckKnuckle": False,
            "PC_PLC_KnuckleCheckOK": False,
            "PC_PLC_KnuckleCheckDone": False,
            # Hub (Step 2)
            "PLC_PC_CheckHub": False,
            "PC_PLC_HubCheckOK": False,
            "PC_PLC_HubCheckDone": False,
            # Hub + 2nd Bearing (Step 3)
            "PLC_PC_CheckHubAndSecondBearing": False,
            "PC_PLC_HubAndSecondBearingCheckOK": False,
            "PC_PLC_HubAndSecondBearingCheckDone": False,
            # Nut + Plate Washer (Step 4)
            "PLC_PC_CheckNutAndPlateWasher": False,
            "PC_PLC_NutAndPlateWasherCheckOK": False,
            "PC_PLC_NutAndPlateWasherCheckDone": False,
            # Station 2 Torque (Step 5 and 10)
            "PLC_PC_Station2TorqueValueSet": False,
            "PLC_PC_Station2TorqueValue": 0.0,
            # Station 3 Rotation (Step 6)
            "PLC_PC_Station3RotationDone": False,
            # No Cap Bung (Step 7)
            "PLC_PC_CheckNoCapBunk": False,
            "PC_PLC_NoCapBunkCheckOK": False,
            "PC_PLC_NoCapBunkCheckDone": False,
            # Component Press (Step 8)
            "PLC_PC_ComponentPressDone": False,
            # No Bung (Step 9)
            "PLC_PC_CheckNoBunk": False,
            "PC_PLC_NoBunkCheckOK": False,
            "PC_PLC_NoBunkCheckDone": False,
            # Split Pin + Washer (Step 11)
            "PLC_PC_CheckSplitPinAndWasher": False,
            "PC_PLC_SplitPinAndWasherCheckOK": False,
            "PC_PLC_SplitPinAndWasherCheckDone": False,
            # Cap (Step 12)
            "PLC_PC_CheckCap": False,
            "PC_PLC_CapCheckOK": False,
            "PC_PLC_CapCheckDone": False,
            # Bung (Step 13)
            "PLC_PC_CheckBunk": False,
            "PC_PLC_BunkCheckOK": False,
            "PC_PLC_BunkCheckDone": False,
            # Cap Press (Step 14)
            "PLC_PC_CapPressDone": False,
            # Station 3 Torque (Step 15)
            "PLC_PC_Station3TorqueValueSet": False,
            "PLC_PC_Station3TorqueValue": 0.0,
            # System
            "PLC_PC_EmergencyAbort": False,
            "PLC_PC_WatchTag": 1234,
            "PC_PLC_ConnectionStatus": False,
        }
        with self.tag_lock:
            self.tags = defaults.copy()

    def read(self, tag_name):
        """Read a tag value."""
        with self.tag_lock:
            value = self.tags.get(tag_name)
        return _MockTagResult(value)

    def write(self, tag_name, value):
        """Write a tag value."""
        with self.tag_lock:
            self.tags[tag_name] = value

    def open(self):
        self.connected = True

    def close(self):
        self.connected = False

    def reset_all(self):
        self._initialize_tags()

    def get_all_tags(self):
        with self.tag_lock:
            return self.tags.copy()


class _MockTagResult:
    """Mimics pycomm3 tag read result."""

    def __init__(self, value):
        self.value = value


def getMockPLC():
    """Get the global MockPLC instance."""
    return MockPLC()


# =============================================================================
# MOCK IOSERVER CLASS
# =============================================================================

class MockIOServer:
    """IOServer that uses MockPLC instead of real PLC."""

    # Tag names from Configuration
    PLC_PC_CheckQRCode: str = CosThetaConfigurator.getInstance().getPlcPcCheckQRCodeTagName()
    PC_PLC_QRCodeCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcQRCodeOKTagName()
    PC_PLC_QRCodeCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcQRCodeDoneTagName()

    PLC_PC_CheckKnuckle: str = CosThetaConfigurator.getInstance().getPlcPcCheckKnuckleTagName()
    PC_PLC_KnuckleCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcKnuckleCheckOKTagName()
    PC_PLC_KnuckleCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcKnuckleCheckDoneTagName()

    PLC_PC_CheckHub: str = CosThetaConfigurator.getInstance().getPlcPcCheckHubTagName()
    PC_PLC_HubCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcHubCheckOKTagName()
    PC_PLC_HubCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcHubCheckDoneTagName()

    PLC_PC_CheckHubAndSecondBearing: str = CosThetaConfigurator.getInstance().getPlcPcCheckHubAndSecondBearingTagName()
    PC_PLC_HubAndSecondBearingCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcHubAndSecondBearingCheckOKTagName()
    PC_PLC_HubAndSecondBearingCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcHubAndSecondBearingCheckDoneTagName()

    PLC_PC_CheckNutAndPlateWasher: str = CosThetaConfigurator.getInstance().getPlcPcCheckNutAndPlateWasherTagName()
    PC_PLC_NutAndPlateWasherCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcNutAndPlateWasherOKTagName()
    PC_PLC_NutAndPlateWasherCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcNutAndPlateWasherDoneTagName()

    PLC_PC_Station2TorqueValueSet: str = CosThetaConfigurator.getInstance().getPlcPcStation2TorqueValueSetTagName()
    PLC_PC_Station2TorqueValue: str = CosThetaConfigurator.getInstance().getPlcPcStation2TorqueValueTagName()

    PLC_PC_Station3RotationDone: str = CosThetaConfigurator.getInstance().getPlcPcStation3RotationDoneTagName()

    PLC_PC_CheckNoCapBung: str = CosThetaConfigurator.getInstance().getPlcPcCheckNoCapBunkTagName()
    PC_PLC_NoCapBungCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcNoCapBunkCheckOKTagName()
    PC_PLC_NoCapBungCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcNoCapBunkCheckDoneTagName()

    PLC_PC_ComponentPressDone: str = CosThetaConfigurator.getInstance().getPlcPcComponentPressDoneTagName()

    PLC_PC_CheckNoBung: str = CosThetaConfigurator.getInstance().getPlcPcCheckNoBunkTagName()
    PC_PLC_NoBungCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcNoBunkCheckOKTagName()
    PC_PLC_NoBungCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcNoBunkCheckDoneTagName()

    PLC_PC_CheckSplitPinAndWasher: str = CosThetaConfigurator.getInstance().getPlcPcCheckSplitPinAndWasherTagName()
    PC_PLC_SplitPinAndWasherCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcSplitPinAndWasherCheckOKTagName()
    PC_PLC_SplitPinAndWasherCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcSplitPinAndWasherCheckDoneTagName()

    PLC_PC_CheckCap: str = CosThetaConfigurator.getInstance().getPlcPcCheckCapTagName()
    PC_PLP_CapCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcCapCheckOKTagName()
    PC_PLC_CapCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcCapCheckDoneTagName()

    PLC_PC_CheckBung: str = CosThetaConfigurator.getInstance().getPlcPcCheckBunkTagName()
    PC_PLC_BungCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcBunkCheckOKTagName()
    PC_PLC_BungCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcBunkCheckDoneTagName()

    PLC_PC_CapPressDone: str = CosThetaConfigurator.getInstance().getPlcPcCapPressDoneTagName()

    PLC_PC_Station3TorqueValueSet: str = CosThetaConfigurator.getInstance().getPlcPcStation3TorqueValueSetTagName()
    PLC_PC_Station3TorqueValue: str = CosThetaConfigurator.getInstance().getPlcPcStation3TorqueValueTagName()

    PLC_PC_EmergencyAbort: str = CosThetaConfigurator.getInstance().getPlcPcEmergencyAbortTagName()
    PLC_PC_WatchTag: str = CosThetaConfigurator.getInstance().getPlcPcWatchTagName()
    PC_PLC_ConnectionStatusTag: str = CosThetaConfigurator.getInstance().getPcPlcConnectionStatusTagName()

    PLC_SLEEPTIME_BETWEEN_OK_AND_DONE: float = CosThetaConfigurator.getInstance().getPlcSleepTimeBetweenOKAndDone()

    FALSE_FLOAT_VALUE: float = -1234.
    FALSE_INT_VALUE: int = -1234
    WATCH_TAG_VALUE: int = 1234
    CONSECUTIVE_CONNECTION_DOWN_COUNT: int = 0
    LAST_CONNECTION_STATUS_UPDATED: bool = False

    logSource = "MockIOServer"

    def __init__(self):
        self.machineState = MachineStateMachine()
        self.shutdown = False

        self.read_interval = CosThetaConfigurator.getInstance().getPlcReadInterval()
        self.write_loop_interval = CosThetaConfigurator.getInstance().getPlcWriteInterval()

        self.redisHostname = CosThetaConfigurator.getInstance().getRedisHost()
        self.redisPort = CosThetaConfigurator.getInstance().getRedisPort()

        # Redis connections
        self.redisConnectionForReads = None
        self.redisConnectionForWrites = None
        self.heartbeatRedisConnection = None
        self.combinedHeartbeatRedisConnection = None
        self.emergencyRedisConnection = None

        self._connect_redis()

        # Use MockPLC instead of real PLC
        self.mockPLC = getMockPLC()

        self.readingLock = threading.Lock()
        self.writingLock = threading.Lock()

        self.logger = logging.getLogger("MockIOServer")
        self.logger.setLevel(logging.INFO)

        time.sleep(1)
        self._start_threads()

        printBoldBlue("*" * 40)
        printBlue("Started MOCK IO Server (No PLC Required)")
        printBoldBlue("*" * 40)

    def _connect_redis(self):
        try:
            self.redisConnectionForReads = Redis(self.redisHostname, self.redisPort, retry_on_timeout=True)
            self.redisConnectionForWrites = Redis(self.redisHostname, self.redisPort, retry_on_timeout=True)
            self.heartbeatRedisConnection = Redis(self.redisHostname, self.redisPort, retry_on_timeout=True)
            self.combinedHeartbeatRedisConnection = Redis(self.redisHostname, self.redisPort, retry_on_timeout=True)
            self.emergencyRedisConnection = Redis(self.redisHostname, self.redisPort, retry_on_timeout=True)
            printBoldGreen("Redis connections established")
        except Exception as e:
            printBoldRed(f"Redis connection error: {e}")

    def getCurrentTagsToBeRead(self):
        currentState = self.machineState.getCurrentState()

        if currentState == MachineState.READ_QR_CODE:
            return [MockIOServer.PLC_PC_CheckQRCode], [bool]
        if currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE:
            return [MockIOServer.PLC_PC_CheckKnuckle], [bool]
        if currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING:
            return [MockIOServer.PLC_PC_CheckHub], [bool]
        if currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING:
            return [MockIOServer.PLC_PC_CheckHubAndSecondBearing], [bool]
        if currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER:
            return [MockIOServer.PLC_PC_CheckNutAndPlateWasher], [bool]
        if currentState == MachineState.READ_TIGHTENING_TORQUE_1_DONE:
            return [MockIOServer.PLC_PC_Station2TorqueValueSet], [bool]
        if currentState == MachineState.READ_TIGHTENING_TORQUE_1:
            return [MockIOServer.PLC_PC_Station2TorqueValue], [float]
        if currentState == MachineState.READ_FREE_ROTATIONS_DONE:
            return [MockIOServer.PLC_PC_Station3RotationDone], [bool]
        if currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS:
            return [MockIOServer.PLC_PC_CheckNoCapBung], [bool]
        if currentState == MachineState.READ_COMPONENT_PRESS_DONE:
            return [MockIOServer.PLC_PC_ComponentPressDone], [bool]
        if currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK:
            return [MockIOServer.PLC_PC_CheckNoBung], [bool]
        if currentState == MachineState.READ_TIGHTENING_TORQUE_2_DONE:
            return [MockIOServer.PLC_PC_Station2TorqueValueSet], [bool]
        if currentState == MachineState.READ_TIGHTENING_TORQUE_2:
            return [MockIOServer.PLC_PC_Station2TorqueValue], [float]
        if currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER:
            return [MockIOServer.PLC_PC_CheckSplitPinAndWasher], [bool]
        if currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP:
            return [MockIOServer.PLC_PC_CheckCap], [bool]
        if currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS:
            return [MockIOServer.PLC_PC_CheckBung], [bool]
        if currentState == MachineState.READ_CAP_PRESS_DONE:
            return [MockIOServer.PLC_PC_CapPressDone], [bool]
        if currentState == MachineState.READ_FREE_ROTATION_TORQUE_1_DONE:
            return [MockIOServer.PLC_PC_Station3TorqueValueSet], [bool]
        if currentState == MachineState.READ_FREE_ROTATION_TORQUE_1:
            return [MockIOServer.PLC_PC_Station3TorqueValue], [float]

        return None, None

    def getCurrentTagsToBeWritten(self):
        currentState = self.machineState.getCurrentState()

        if currentState == MachineState.WRITE_QR_CODE:
            return [MockIOServer.PC_PLC_QRCodeCheckOK, MockIOServer.PC_PLC_QRCodeCheckDone,
                    MockIOServer.PLC_PC_CheckQRCode]
        if currentState == MachineState.WRITE_RESULT_OF_CHECKING_KNUCKLE:
            return [MockIOServer.PC_PLC_KnuckleCheckOK, MockIOServer.PC_PLC_KnuckleCheckDone,
                    MockIOServer.PLC_PC_CheckKnuckle]
        if currentState == MachineState.WRITE_RESULT_OF_CHECKING_HUB_AND_BOTTOM_BEARING:
            return [MockIOServer.PC_PLC_HubCheckOK, MockIOServer.PC_PLC_HubCheckDone, MockIOServer.PLC_PC_CheckHub]
        if currentState == MachineState.WRITE_RESULT_OF_CHECKING_TOP_BEARING:
            return [MockIOServer.PC_PLC_HubAndSecondBearingCheckOK, MockIOServer.PC_PLC_HubAndSecondBearingCheckDone,
                    MockIOServer.PLC_PC_CheckHubAndSecondBearing]
        if currentState == MachineState.WRITE_RESULT_OF_CHECKING_NUT_AND_PLATEWASHER:
            return [MockIOServer.PC_PLC_NutAndPlateWasherCheckOK, MockIOServer.PC_PLC_NutAndPlateWasherCheckDone,
                    MockIOServer.PLC_PC_CheckNutAndPlateWasher]
        if currentState == MachineState.WRITE_RESULT_OF_CHECKING_BUNK_FOR_COMPONENT_PRESS:
            return [MockIOServer.PC_PLC_NoCapBungCheckOK, MockIOServer.PC_PLC_NoCapBungCheckDone,
                    MockIOServer.PLC_PC_CheckNoCapBung]
        if currentState == MachineState.WRITE_RESULT_OF_CHECKING_NO_BUNK:
            return [MockIOServer.PC_PLC_NoBungCheckOK, MockIOServer.PC_PLC_NoBungCheckDone,
                    MockIOServer.PLC_PC_CheckNoBung]
        if currentState == MachineState.WRITE_RESULT_OF_CHECKING_SPLITPIN_AND_WASHER:
            return [MockIOServer.PC_PLC_SplitPinAndWasherCheckOK, MockIOServer.PC_PLC_SplitPinAndWasherCheckDone,
                    MockIOServer.PLC_PC_CheckSplitPinAndWasher]
        if currentState == MachineState.WRITE_RESULT_OF_CHECKING_CAP:
            return [MockIOServer.PC_PLP_CapCheckOK, MockIOServer.PC_PLC_CapCheckDone, MockIOServer.PLC_PC_CheckCap]
        if currentState == MachineState.WRITE_RESULT_OF_CHECKING_BUNK_FOR_CAP_PRESS:
            return [MockIOServer.PC_PLC_BungCheckOK, MockIOServer.PC_PLC_BungCheckDone, MockIOServer.PLC_PC_CheckBung]

        return None

    def read_bool(self, tag, expected_value=True, debug=False):
        result = self.mockPLC.read(tag)
        return result.value if result.value is not None else not expected_value

    def read_float(self, tag, debug=False):
        result = self.mockPLC.read(tag)
        return result.value if result.value is not None else MockIOServer.FALSE_FLOAT_VALUE

    def read_int(self, tag, debug=False):
        result = self.mockPLC.read(tag)
        return int(result.value) if result.value is not None else MockIOServer.FALSE_INT_VALUE

    def write_bool(self, tag, value, debug=False):
        self.mockPLC.write(tag, value)
        if debug:
            printBoldGreen(f"Wrote bool tag {tag}: {value}")
        return True

    def write_float(self, tag, value, debug=False):
        self.mockPLC.write(tag, value)
        return True

    def resetTagToFalse(self, tag, debug=False):
        return self.write_bool(tag, False, debug)

    def resetTagToZero(self, tag, debug=False):
        return self.write_float(tag, 0.0, debug)

    def resetAll(self, debug=False):
        self.mockPLC.reset_all()

    def process_current_read_state(self, valueRead):
        if valueRead is None:
            return False

        returnValue = False
        currentState = self.machineState.getCurrentState()

        if type(valueRead) == bool:
            if valueRead:
                successfullySent = False
                try:
                    if currentState == MachineState.READ_QR_CODE:
                        successfullySent = sendDataFromIOServerToQRCodeServer(self.redisConnectionForReads)
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads,
                                                                              currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE)
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads,
                                                                              currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING)
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads,
                                                                              currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING)
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads,
                                                                              currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER)
                    elif currentState == MachineState.READ_TIGHTENING_TORQUE_1_DONE:
                        successfullySent = True
                    elif currentState == MachineState.READ_FREE_ROTATIONS_DONE:
                        successfullySent = sendDataFromIOServerToFEServer(self.redisConnectionForReads,
                                                                          currentMachineState=MachineState.READ_FREE_ROTATIONS_DONE)
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads,
                                                                              currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS)
                    elif currentState == MachineState.READ_COMPONENT_PRESS_DONE:
                        successfullySent = sendDataFromIOServerToFEServer(self.redisConnectionForReads,
                                                                          currentMachineState=MachineState.READ_COMPONENT_PRESS_DONE)
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads,
                                                                              currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK)
                    elif currentState == MachineState.READ_TIGHTENING_TORQUE_2_DONE:
                        successfullySent = True
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads,
                                                                              currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER)
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads,
                                                                              currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP)
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads,
                                                                              currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS)
                    elif currentState == MachineState.READ_CAP_PRESS_DONE:
                        successfullySent = sendDataFromIOServerToFEServer(self.redisConnectionForReads,
                                                                          currentMachineState=MachineState.READ_CAP_PRESS_DONE)
                    elif currentState == MachineState.READ_FREE_ROTATION_TORQUE_1_DONE:
                        successfullySent = True

                    if successfullySent:
                        printBold(f"Transition from state {self.machineState.getCurrentState()}")
                        self.machineState.incrementState()
                        printBold(f"        ...to state {self.machineState.getCurrentState()}")
                except Exception as e:
                    printBoldRed(f"Exception {e} in process_current_read_state")

                returnValue = valueRead

        elif type(valueRead) == float:
            if valueRead != MockIOServer.FALSE_FLOAT_VALUE:
                successfullySent = False
                try:
                    if currentState == MachineState.READ_TIGHTENING_TORQUE_1:
                        successfullySent = sendDataFromIOServerToFEServer(self.redisConnectionForReads,
                                                                          currentMachineState=MachineState.READ_TIGHTENING_TORQUE_1,
                                                                          value=valueRead)
                    elif currentState == MachineState.READ_TIGHTENING_TORQUE_2:
                        successfullySent = sendDataFromIOServerToFEServer(self.redisConnectionForReads,
                                                                          currentMachineState=MachineState.READ_TIGHTENING_TORQUE_2,
                                                                          value=valueRead)
                    elif currentState == MachineState.READ_FREE_ROTATION_TORQUE_1:
                        successfullySent = sendDataFromIOServerToFEServer(self.redisConnectionForReads,
                                                                          currentMachineState=MachineState.READ_FREE_ROTATION_TORQUE_1,
                                                                          value=valueRead,
                                                                          moveToState=MachineState.READ_QR_CODE,
                                                                          action=moveAheadToNextComponent)

                    if successfullySent:
                        printBold(f"Transition from state {self.machineState.getCurrentState()}")
                        self.machineState.incrementState()
                        printBold(f"        ...to state {self.machineState.getCurrentState()}")
                except Exception as e:
                    printBoldRed(f"Exception {e} in process_current_read_state")

                returnValue = successfullySent

        if self.machineState.getCurrentState() == MachineState.READ_QR_CODE:
            self.resetAll()
            self.write_bool(MockIOServer.PLC_PC_CheckQRCode, True)

        return returnValue

    def perform_write_state_action(self, tags, result=True):
        self.write_bool(tags[0], result)
        time.sleep(MockIOServer.PLC_SLEEPTIME_BETWEEN_OK_AND_DONE)
        self.write_bool(tags[1], True)
        return result

    def _start_threads(self):
        # Read loop thread
        def read_loop():
            while not self.shutdown:
                try:
                    tagsToBeRead, valueTypes = self.getCurrentTagsToBeRead()
                    if tagsToBeRead is not None:
                        if valueTypes[0] == bool:
                            valueRead = self.read_bool(tagsToBeRead[0])
                            if valueRead:
                                self.process_current_read_state(valueRead)
                        elif valueTypes[0] == float:
                            valueRead = self.read_float(tagsToBeRead[0])
                            if valueRead != MockIOServer.FALSE_FLOAT_VALUE:
                                self.process_current_read_state(valueRead)
                except Exception as e:
                    printBoldRed(f"Read loop error: {e}")
                time.sleep(self.read_interval)

        # Write loop thread
        def write_loop():
            while not self.shutdown:
                try:
                    tagsToBeWritten = self.getCurrentTagsToBeWritten()
                    if tagsToBeWritten is not None:
                        if self.machineState.getCurrentState() == MachineState.WRITE_QR_CODE:
                            success = readDataInIOServerFromQRCodeServer(self.redisConnectionForWrites)
                            if success:
                                self.perform_write_state_action(tags=tagsToBeWritten, result=True)
                                printBold(f"Transition from state {self.machineState.getCurrentState()}")
                                self.machineState.incrementState()
                                printBold(f"        ...to state {self.machineState.getCurrentState()}")
                        else:
                            previousMachineState, evaluation = readDataInIOServerFromCameraServer(
                                self.redisConnectionForWrites)
                            if evaluation:
                                self.perform_write_state_action(tags=tagsToBeWritten, result=True)
                                printBold(f"Transition from state {self.machineState.getCurrentState()}")
                                self.machineState.incrementState()
                                printBold(f"        ...to state {self.machineState.getCurrentState()}")
                            elif previousMachineState != MachineState.INVALID_STATE:
                                self.perform_write_state_action(tags=tagsToBeWritten, result=False)
                                printBoldRed(f"NotOK received - staying at state {self.machineState.getCurrentState()}")
                                self.machineState.decrementState()
                except Exception as e:
                    pass
                time.sleep(self.write_loop_interval)

        # Heartbeat thread
        def heartbeat_loop():
            while not self.shutdown:
                try:
                    sendHeartbeatFromIOServerToHeartbeatServer(self.heartbeatRedisConnection, status=ALIVE)
                except:
                    pass
                time.sleep(1)

        threading.Thread(target=read_loop, daemon=True, name="MockIO-ReadLoop").start()
        threading.Thread(target=write_loop, daemon=True, name="MockIO-WriteLoop").start()
        threading.Thread(target=heartbeat_loop, daemon=True, name="MockIO-Heartbeat").start()

        printLight("Started MockIOServer threads")


# =============================================================================
# STARTUP FUNCTION
# =============================================================================

_mock_io_server = None


def startMockIOServer():
    global _mock_io_server
    _mock_io_server = MockIOServer()
    return _mock_io_server


def getMockIOServer():
    return _mock_io_server


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Starting MockPLC IOServer")
    print("Use TestIOServerWithMockPLC.py for interactive testing")
    print("=" * 60)

    io = startMockIOServer()

    # Keep running
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        io.shutdown = True