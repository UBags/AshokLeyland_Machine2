import random
import sys
import threading
import time
import logging
from typing import Any, List, Optional, Tuple, Type, Union
from pycomm3 import LogixDriver
from pycomm3 import DINT, SHORT_STRING, Struct, STRING, REAL
from pycomm3.logger import configure_default_logger, LOG_VERBOSE

from logutils import Logger
from logutils.SlaveLoggers import SlaveConsoleLogger, SlaveFileLogger
from utils.CosThetaPrintUtils import *
from utils.RedisUtils import *

from Configuration import CosThetaConfigurator
from statemachine.StateMachine import MachineState, MachineStateMachine

TIME_WAIT_BEFORE_RESETTING_TORQUE2_VALUES_AFTER_READING_THEM : float = 1.5

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

    # ==================== No Cap Bung Tags (Step 7) ====================
    PLC_PC_CheckNoCapBung: str = CosThetaConfigurator.getInstance().getPlcPcCheckNoCapBunkTagName()
    PC_PLC_NoCapBungCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcNoCapBunkCheckOKTagName()
    PC_PLC_NoCapBungCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcNoCapBunkCheckDoneTagName()

    # ==================== Component Press Done Tag (Step 8) ====================
    PLC_PC_ComponentPressDone: str = CosThetaConfigurator.getInstance().getPlcPcComponentPressDoneTagName()

    # ==================== No Bung Tags (Step 9) ====================
    PLC_PC_CheckNoBung: str = CosThetaConfigurator.getInstance().getPlcPcCheckNoBunkTagName()
    PC_PLC_NoBungCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcNoBunkCheckOKTagName()
    PC_PLC_NoBungCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcNoBunkCheckDoneTagName()

    # ==================== Split Pin and Washer Tags (Step 11) ====================
    PLC_PC_CheckSplitPinAndWasher: str = CosThetaConfigurator.getInstance().getPlcPcCheckSplitPinAndWasherTagName()
    PC_PLC_SplitPinAndWasherCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcSplitPinAndWasherCheckOKTagName()
    PC_PLC_SplitPinAndWasherCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcSplitPinAndWasherCheckDoneTagName()

    # ==================== Cap Tags (Step 12) ====================
    PLC_PC_CheckCap: str = CosThetaConfigurator.getInstance().getPlcPcCheckCapTagName()
    PC_PLP_CapCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcCapCheckOKTagName()
    PC_PLC_CapCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcCapCheckDoneTagName()

    # ==================== Bung Tags (Step 13) ====================
    PLC_PC_CheckBung: str = CosThetaConfigurator.getInstance().getPlcPcCheckBunkTagName()
    PC_PLC_BungCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcBunkCheckOKTagName()
    PC_PLC_BungCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcBunkCheckDoneTagName()

    # ==================== Cap Press Done Tag (Step 14) ====================
    PLC_PC_CapPressDone: str = CosThetaConfigurator.getInstance().getPlcPcCapPressDoneTagName()

    # ==================== Station 3 Free Rotation Torque Tags (Step 15) ====================
    PLC_PC_Station3TorqueValueSet: str = CosThetaConfigurator.getInstance().getPlcPcStation3TorqueValueSetTagName()
    PLC_PC_Station3TorqueValue: str = CosThetaConfigurator.getInstance().getPlcPcStation3TorqueValueTagName()

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

    def __init__(self, plc_ip: Union[str, None] = None, mockInstance : bool = False):
        # Class variables
        IOServer.logSource = getFullyQualifiedName(__file__, __class__)
        self.machineState = MachineStateMachine()  # Instance of MachineStateMachine
        self.shutdown = False

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
        - States 14-15: Bung for Component Press (Step 7) - uses PLC_PC_CheckBunk
        - State 16: Component Press Done (Step 8)
        - States 17-18: No Bung (Step 9)
        - States 19-20: Tightening Torque 2 (Step 10) - uses PLC_PC_Station2TorqueValueSet (reused)
        - States 21-22: Split Pin and Washer (Step 11)
        - States 23-24: Cap (Step 12)
        - States 25-26: Bung for Cap Press (Step 13) - uses PLC_PC_CheckBunk (reused)
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

        # Step 7: No Cap Bung for Component Press
        if currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS:
            return [IOServer.PLC_PC_CheckNoCapBung], [bool]

        # Step 8: Component Press Done
        if currentState == MachineState.READ_COMPONENT_PRESS_DONE:
            return [IOServer.PLC_PC_ComponentPressDone], [bool]

        # Step 9: No Bung
        if currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK:
            return [IOServer.PLC_PC_CheckNoBung], [bool]

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

        # Step 13: Bung for Cap Press - reuses CheckBunk
        if currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS:
            return [IOServer.PLC_PC_CheckBung], [bool]

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

        # Step 7: No Cap Bung for Component Press
        if currentState == MachineState.WRITE_RESULT_OF_CHECKING_BUNK_FOR_COMPONENT_PRESS:
            return [IOServer.PC_PLC_NoCapBungCheckOK, IOServer.PC_PLC_NoCapBungCheckDone,
                    IOServer.PLC_PC_CheckNoCapBung]

        # Step 9: No Bung
        if currentState == MachineState.WRITE_RESULT_OF_CHECKING_NO_BUNK:
            return [IOServer.PC_PLC_NoBungCheckOK, IOServer.PC_PLC_NoBungCheckDone, IOServer.PLC_PC_CheckNoBung]

        # Step 11: Split Pin and Washer
        if currentState == MachineState.WRITE_RESULT_OF_CHECKING_SPLITPIN_AND_WASHER:
            return [IOServer.PC_PLC_SplitPinAndWasherCheckOK, IOServer.PC_PLC_SplitPinAndWasherCheckDone,
                    IOServer.PLC_PC_CheckSplitPinAndWasher]

        # Step 12: Cap
        if currentState == MachineState.WRITE_RESULT_OF_CHECKING_CAP:
            return [IOServer.PC_PLP_CapCheckOK, IOServer.PC_PLC_CapCheckDone, IOServer.PLC_PC_CheckCap]

        # Step 13: Bung for Cap Press - reuses CheckBunk
        if currentState == MachineState.WRITE_RESULT_OF_CHECKING_BUNK_FOR_CAP_PRESS:
            return [IOServer.PC_PLC_BungCheckOK, IOServer.PC_PLC_BungCheckDone, IOServer.PLC_PC_CheckBung]

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

        self.heartbeatThread = threading.Thread(name=f"IO Heartbeat Thread", target=reportHeartbeat, args=(), daemon=True)
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
                            success = readDataInIOServerFromQRCodeServer(self.redisConnectionForWrites)
                            if success:
                                objRef.perform_write_state_action(tags=tagsToBeWritten, result=True)
                                printBold(f"Transition from state {objRef.machineState.getCurrentState()}")
                                objRef.machineState.incrementState()
                                printBold(f"        ...to state {objRef.machineState.getCurrentState()}")
                        else:
                            previousMachineState, evaluation = readDataInIOServerFromCameraServer(
                                self.redisConnectionForWrites)
                            if evaluation:
                                objRef.perform_write_state_action(tags=tagsToBeWritten, result=True)
                                printBold(f"Transition from state {objRef.machineState.getCurrentState()}")
                                objRef.machineState.incrementState()
                                printBold(f"        ...to state {objRef.machineState.getCurrentState()}")
                            else:
                                if previousMachineState != MachineState.INVALID_STATE:
                                    objRef.perform_write_state_action(tags=tagsToBeWritten, result=False)
                                    printBoldRed(
                                        f"Did not move ahead from state {objRef.machineState.getCurrentState()}")
                                    printBold(f"Transition from state {objRef.machineState.getCurrentState()}")
                                    objRef.machineState.decrementState()
                                    printBold(f"        ...to state {objRef.machineState.getCurrentState()}")
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

                    # Step 7: Bung for Component Press
                    elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads,
                                                                              currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS)

                    # Step 8: Component Press Done
                    elif currentState == MachineState.READ_COMPONENT_PRESS_DONE:
                        successfullySent = sendDataFromIOServerToFEServer(self.redisConnectionForReads,
                                                                          currentMachineState=MachineState.READ_COMPONENT_PRESS_DONE)

                    # Step 9: No Bung
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

                    # Step 13: Bung for Cap Press
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
                        printBold(f"Transition from state {self.machineState.getCurrentState()}")
                        self.machineState.incrementState()
                        printBold(f"        ...to state {self.machineState.getCurrentState()}")

                except Exception as e:
                    printBoldRed(f"Exception {e} encountered in current_read_state - 1")

                returnValue = valueRead
            else:
                returnValue = valueRead

        elif type(valueRead) == float:
            if valueRead == IOServer.FALSE_FLOAT_VALUE:
                printBold(f"Failed to read proper value of float in state {self.machineState.getCurrentState()}")
                returnValue = False
            else:
                successfullySent: bool = False
                try:
                    # Step 5B: Tightening Torque 1 Value
                    if currentState == MachineState.READ_TIGHTENING_TORQUE_1:
                        successfullySent = sendDataFromIOServerToFEServer(self.redisConnectionForReads,
                                                                          currentMachineState=MachineState.READ_TIGHTENING_TORQUE_1,
                                                                          value=valueRead)
                        # Reset Station 2 torque tags after reading (so Step 10 doesn't auto-trigger)
                        time.sleep(TIME_WAIT_BEFORE_RESETTING_TORQUE2_VALUES_AFTER_READING_THEM)
                        if successfullySent:
                            self.resetTagToFalse(IOServer.PLC_PC_Station2TorqueValueSet)
                            self.resetTagToZero(IOServer.PLC_PC_Station2TorqueValue)

                    # Step 10B: Tightening Torque 2 Value
                    elif currentState == MachineState.READ_TIGHTENING_TORQUE_2:
                        successfullySent = sendDataFromIOServerToFEServer(self.redisConnectionForReads,
                                                                          currentMachineState=MachineState.READ_TIGHTENING_TORQUE_2,
                                                                          value=valueRead)

                    # Step 15B: Free Rotation Torque 1 Value - FINAL STATE
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
                    printBoldRed(f"Exception {e} encountered in current_read_state - 3")

                returnValue = successfullySent

        if self.machineState.getCurrentState() == MachineState.READ_QR_CODE:
            self.resetAll()
            self.write_bool(IOServer.PLC_PC_CheckQRCode, True)

        return returnValue

    def perform_write_state_action(self, tags: list[str], result: bool = True) -> bool:
        """Perform the write action to PLC tags."""
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

        printLight("Started read tag, connection monitoring, and write tag threads")

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

    def write_bool(self, tag: str, value: bool, debug: bool = False) -> bool:
        """Write a boolean to a tag to the PLC."""
        if tag is None:
            return False
        if (self.clientForWrites is None) or (not self.clientForWrites.connected):
            return False
        try:
            with self.writingLock:
                self.clientForWrites.write(tag, value)
            if debug:
                printBoldGreen(f"Wrote bool tag {tag}: {value}")
            return True
        except Exception as e:
            printBoldRed(f"Error writing bool tag {tag}: {e}")
            return False

    def write_float(self, tag: str, value: float, debug: bool = False) -> bool:
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
                printBoldGreen(f"Wrote float tag {tag}: {value}")
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

        # Step 7: No Cap Bung
        self.resetTagToFalse(IOServer.PLC_PC_CheckNoCapBung)
        self.resetTagToFalse(IOServer.PC_PLC_NoCapBungCheckOK)
        self.resetTagToFalse(IOServer.PC_PLC_NoCapBungCheckDone)

        # Step 8: Component Press Done
        self.resetTagToFalse(IOServer.PLC_PC_ComponentPressDone)

        # Step 9: No Bung
        self.resetTagToFalse(IOServer.PLC_PC_CheckNoBung)
        self.resetTagToFalse(IOServer.PC_PLC_NoBungCheckOK)
        self.resetTagToFalse(IOServer.PC_PLC_NoBungCheckDone)

        # Step 11: Split Pin and Washer
        self.resetTagToFalse(IOServer.PLC_PC_CheckSplitPinAndWasher)
        self.resetTagToFalse(IOServer.PC_PLC_SplitPinAndWasherCheckOK)
        self.resetTagToFalse(IOServer.PC_PLC_SplitPinAndWasherCheckDone)

        # Step 12: Cap
        self.resetTagToFalse(IOServer.PLC_PC_CheckCap)
        self.resetTagToFalse(IOServer.PC_PLP_CapCheckOK)
        self.resetTagToFalse(IOServer.PC_PLC_CapCheckDone)

        # Step 13: Bung (used for both bung checks)
        self.resetTagToFalse(IOServer.PLC_PC_CheckBung)
        self.resetTagToFalse(IOServer.PC_PLC_BungCheckOK)
        self.resetTagToFalse(IOServer.PC_PLC_BungCheckDone)

        # Step 14: Cap Press Done
        self.resetTagToFalse(IOServer.PLC_PC_CapPressDone)

        # Step 15: Station 3 Torque
        self.resetTagToFalse(IOServer.PLC_PC_Station3TorqueValueSet)
        self.resetTagToZero(IOServer.PLC_PC_Station3TorqueValue)

        # Emergency Abort
        self.resetTagToFalse(IOServer.PLC_PC_EmergencyAbort)

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

        # Step 7: No Cap Bung
        tag_values[IOServer.PLC_PC_CheckNoCapBung] = self.read_bool(IOServer.PLC_PC_CheckNoCapBung, debug=False)
        tag_values[IOServer.PC_PLC_NoCapBungCheckOK] = self.read_bool(IOServer.PC_PLC_NoCapBungCheckOK, debug=False)
        tag_values[IOServer.PC_PLC_NoCapBungCheckDone] = self.read_bool(IOServer.PC_PLC_NoCapBungCheckDone, debug=False)

        # Step 8: Component Press Done
        tag_values[IOServer.PLC_PC_ComponentPressDone] = self.read_bool(IOServer.PLC_PC_ComponentPressDone, debug=False)

        # Step 9: No Bung
        tag_values[IOServer.PLC_PC_CheckNoBung] = self.read_bool(IOServer.PLC_PC_CheckNoBung, debug=False)
        tag_values[IOServer.PC_PLC_NoBungCheckOK] = self.read_bool(IOServer.PC_PLC_NoBungCheckOK, debug=False)
        tag_values[IOServer.PC_PLC_NoBungCheckDone] = self.read_bool(IOServer.PC_PLC_NoBungCheckDone, debug=False)

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

        # Step 13: Bung
        tag_values[IOServer.PLC_PC_CheckBung] = self.read_bool(IOServer.PLC_PC_CheckBung, debug=False)
        tag_values[IOServer.PC_PLC_BungCheckOK] = self.read_bool(IOServer.PC_PLC_BungCheckOK, debug=False)
        tag_values[IOServer.PC_PLC_BungCheckDone] = self.read_bool(IOServer.PC_PLC_BungCheckDone, debug=False)

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

def startIOServer():
    io = IOServer()
    printBoldBlue(f"*****************")
    printBlue(f"Started IO Server")
    printBoldBlue(f"*****************")
    io.heartbeatThread.join()
    io.monitorStopThread.join()
    sys.exit(0)

# *********************************************************************

if __name__ == "__main__":
    startIOServer()