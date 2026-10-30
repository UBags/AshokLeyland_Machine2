import random
import threading
import time
import logging
from typing import Any, List, Optional
from pycomm3 import LogixDriver
from pycomm3 import DINT, SHORT_STRING, Struct, STRING, REAL
from pycomm3.logger import configure_default_logger, LOG_VERBOSE

from logutils import Logger
from logutils.SlaveLoggers import SlaveConsoleLogger, SlaveFileLogger
from utils.CosThetaPrintUtils import *
from utils.RedisUtils import *

from Configuration import CosThetaConfigurator
from statemachine.StateMachine import MachineState, MachineStateMachine


class IOServer:

    PLC_PC_CheckQRCode: str = CosThetaConfigurator.getInstance().getPlcPcCheckQRCodeTagName()
    PC_PLC_QRCodeCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcQRCodeOKTagName()
    PC_PLC_QRCodeCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcQRCodeDoneTagName()

    PLC_PC_CheckKnuckle: str = CosThetaConfigurator.getInstance().getPlcPcCheckKnuckleTagName()
    PC_PLC_KnuckleCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcKnuckleCheckOKTagName()
    PC_PLC_KnuckleCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcKnuckleCheckDoneTagName()

    PLC_PC_CheckHubAndFirstBearing: str = CosThetaConfigurator.getInstance().getPlcPcCheckHubAndFirstBearingTagName()
    PC_PLC_HubAndFirstBearingCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcHubAndFirstBearingCheckOKTagName()
    PC_PLC_HubAndFirstBearingCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcHubAmdFirstBearingCheckDoneTagName()

    PLC_PC_CheckSecondBearing: str = CosThetaConfigurator.getInstance().getPlcPcCheckSecondBearingTagName()
    PC_PLC_SecondBearingCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcSecondBearingCheckOKTagName()
    PC_PLC_SecondBearingCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcSecondBearingCheckDoneTagName()

    PLC_PC_CheckNutAndPlateWasher: str = CosThetaConfigurator.getInstance().getPlcPcCheckNutAndPlateWasherTagName()
    PC_PLC_NutAndPlateWasherCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcNutAndPlateWasherOKTagName()
    PC_PLC_NutAndPlateWasherCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcNutAndPlateWasherDoneTagName()

    PLC_PC_Station2TigtheningTorqueDone: str = CosThetaConfigurator.getInstance().getPlcPcTorqueTighteningDoneTagName()
    PLC_PC_Station2TorqueIsWithinRange: str = CosThetaConfigurator.getInstance().getPlcPcStation2TorqueIsWithinRangeTagName()
    PLC_PC_Station2TorqueValue: str = CosThetaConfigurator.getInstance().getPlcPcStation2TorqueValueTagName()

    PLC_PC_Station3RotationTorqueDone: str = CosThetaConfigurator.getInstance().getPlcPcTorqueRotationDoneTagName()
    PLC_PC_Station3TorqueIsWithinRange: str = CosThetaConfigurator.getInstance().getPlcPcStation3TorqueIsWithinRangeTagName()
    PLC_PC_Station3TorqueValue: str = CosThetaConfigurator.getInstance().getPlcPcStation3TorqueValueTagName()

    PLC_PC_CheckSplitPinAndWasher: str = CosThetaConfigurator.getInstance().getPlcPcCheckSplitPinAndWasherTagName()
    PC_PLC_SplitPinAndWasherCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcSplitPinAndWasherCheckOKTagName()
    PC_PLC_SplitPinAndWasherCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcSplitPinAndWasherCheckDoneTagName()

    PLC_PC_CheckCap: str = CosThetaConfigurator.getInstance().getPlcPcCheckCapTagName()
    PC_PLC_CapCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcCapCheckOKTagName()
    PC_PLC_CapCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcCapCheckDoneTagName()

    PLC_PC_CheckBung: str = CosThetaConfigurator.getInstance().getPlcPcCheckBunkTagName()
    PC_PLC_BungCheckOK: str = CosThetaConfigurator.getInstance().getPcPlcBunkCheckOKTagName()
    PC_PLC_BungCheckDone: str = CosThetaConfigurator.getInstance().getPcPlcBunkCheckDoneTagName()

    PLC_PC_CapPressDone: str = CosThetaConfigurator.getInstance().getPlcPcCapPressDoneTagName()

    PLC_PC_EmergencyAbort: str = CosThetaConfigurator.getInstance().getPlcPcEmergencyAbortTagName()
    PLC_PC_WatchTag: str = CosThetaConfigurator.getInstance().getPlcPcWatchTagName()
    PC_PLC_ConnectionStatusTag: str = CosThetaConfigurator.getInstance().getPcPlcConnectionStatusTagName()

    PLC_IP:str = CosThetaConfigurator.getInstance().getPlcIP()
    PLC_DEBUG:bool = CosThetaConfigurator.getInstance().getPlcDebug()
    PLC_SLEEPTIME_BETWEEN_OK_AND_DONE : float = CosThetaConfigurator.getInstance().getPlcSleepTimeBetweenOKAndDone()

    FALSE_FLOAT_VALUE : float = -1234.
    FALSE_INT_VALUE : int = -1234

    WATCH_TAG_VALUE : int = 1234

    NO_CONNECTION : int = -1986754392
    CONSECUTIVE_CONNECTION_DOWN_COUNT : int = 0

    logSource = getFullyQualifiedName(__file__)
    LAST_CONNECTION_STATUS_UPDATED : bool = False
    LOG_CONNECTION_PROBLEM_EVERY_N_SECS = CosThetaConfigurator.getInstance().getLogDisconnectionsAfterNSecs()

    def __init__(self, plc_ip: Union[str, None] = None):
        # Class variables
        IOServer.logSource = getFullyQualifiedName(__file__, __class__)
        self.machineState = MachineStateMachine()  # Instance of MachineStateMachine
        self.shutdown = False
        # self.read_interval = 0.1  # 100 ms

        self.read_interval = CosThetaConfigurator.getInstance().getPlcReadInterval()  # 200 ms
        self.write_loop_interval = CosThetaConfigurator.getInstance().getPlcWriteInterval()  # 100 ms
        self.CONNECTION_CHECK_INTERVAL = CosThetaConfigurator.getInstance().getPlcConnectionCheckInterval()  # seconds

        self.redisHostname: str = CosThetaConfigurator.getInstance().getRedisHost()
        self.redisPort: int = CosThetaConfigurator.getInstance().getRedisPort()

        self.redisConnectionForReads : Union[Redis,None] = None
        self.clientRedisConnectedForReads : bool = False
        self.redisConnectionForWrites : Union[Redis,None] = None
        self.clientRedisConnectedForWrites : bool = False
        self.heartbeatRedisConnection: Union[Redis,None] = None
        self.heartbeatRedisConnected: bool = False
        self.combinedHeartbeatRedisConnection: Union[Redis,None] = None
        self.combinedHeartbeatRedisConnected: bool = False
        self.emergencyRedisConnection: Union[Redis,None] = None
        self.emergencyRedisConnected: bool = False

        self.connectToRedis(True)
        self.connectToRedisForHeartbeatAndEmergencyMonitoring(True)


        # self.torque_read_attempts = 3  # Number of reads for torque stabilization

        self.plc_ip = plc_ip if plc_ip is not None else IOServer.PLC_IP

        self.clientForReads : Union[LogixDriver, None] = None
        self.clientForWrites : Union[LogixDriver, None]= None

        self.readingLock = threading.Lock()
        self.writingLock = threading.Lock()

        # Initialize logging
        self.logger = logging.getLogger("pycomm3")
        self.logger.setLevel(logging.CRITICAL)
        # logging.basicConfig(level=logging.CRITICAL, logger = "pycomm3")

        # Start PLC connection and threads
        self.connectionInProgress : bool = False
        self.connectionAttempts : int = 0
        self.connectPLC()


        # configure_default_logger(level=LOG_VERBOSE, filename='pycomm3.log')
        time.sleep(3)
        self.startThreads()
        printBoldBlue(f"*****************")
        printBlue(f"Started IO Server")
        printBoldBlue(f"*****************")

    def connectToRedis(self, forceRenew = False):
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
                                                             f'Could not get Redis Connection for Reads', Logger.PROBLEM)
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
                                                            f'Could not get Redis Connection for Writes', Logger.PROBLEM)
                # pass

    def connectToRedisForHeartbeatAndEmergencyMonitoring(self, forceRenew = False):
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
                # printLight(f'Heartbeat Redis Connection is {self.heartbeatRedisConnection}')
                SlaveConsoleLogger.getInstance().logDebug(IOServer.logSource,
                                                             f'Heartbeat Redis Connection is {self.heartbeatRedisConnection}', Logger.SUCCESS)
                SlaveFileLogger.getInstance().logDebug(IOServer.logSource,
                                                          f'Heartbeat Redis Connection is {self.heartbeatRedisConnection}',
                                                          Logger.SUCCESS)
            except:
                self.heartbeatRedisConnected = False
                # printBoldRed(f'Could not create Heartbeat Redis connection in {IOServer.logSource}')
                SlaveConsoleLogger.getInstance().logTakeAction(IOServer.logSource,
                                                             f'Could not get Heartbeat Redis Connection', Logger.PROBLEM)
                SlaveFileLogger.getInstance().logTakeAction(IOServer.logSource,
                                                             f'Could not get Heartbeat Redis Connection', Logger.PROBLEM)

        if not self.combinedHeartbeatRedisConnected:
            try:
                self.combinedHeartbeatRedisConnection = Redis(self.redisHostname, self.redisPort, retry_on_timeout=True)
                self.combinedHeartbeatRedisConnected = True
                # printLight(f'Heartbeat Redis Connection is {self.heartbeatRedisConnection}')
                SlaveConsoleLogger.getInstance().logDebug(IOServer.logSource,
                                                             f'Combined Heartbeat Redis Connection is {self.combinedHeartbeatRedisConnection}', Logger.SUCCESS)
                SlaveFileLogger.getInstance().logDebug(IOServer.logSource,
                                                          f'Combined Heartbeat Redis Connection is {self.combinedHeartbeatRedisConnection}',
                                                          Logger.SUCCESS)
            except:
                self.combinedHeartbeatRedisConnected = False
                # printBoldRed(f'Could not create Combined Heartbeat Redis connection in {IOServer.logSource}')
                SlaveConsoleLogger.getInstance().logTakeAction(IOServer.logSource,
                                                             f'Could not get Heartbeat Redis Connection', Logger.PROBLEM)
                SlaveFileLogger.getInstance().logTakeAction(IOServer.logSource,
                                                            f'Could not get Heartbeat Redis Connection', Logger.PROBLEM)

        if not self.emergencyRedisConnected:
            try:
                self.emergencyRedisConnection = Redis(self.redisHostname, self.redisPort, retry_on_timeout=True)
                self.emergencyRedisConnected = True
                SlaveConsoleLogger.getInstance().logDebug(IOServer.logSource,
                                                             f'Emergency Redis Connection is {self.emergencyRedisConnection}', Logger.SUCCESS)
                SlaveFileLogger.getInstance().logDebug(IOServer.logSource,
                                                          f'Emergency Redis Connection is {self.emergencyRedisConnection}',
                                                          Logger.SUCCESS)
            except:
                self.emergencyRedisConnected = False
                SlaveConsoleLogger.getInstance().logTakeAction(IOServer.logSource,
                                                             f'Could not get Emergency Redis Connection', Logger.PROBLEM)
                SlaveFileLogger.getInstance().logTakeAction(IOServer.logSource,
                                                             f'Could not get Emergency Redis Connection', Logger.PROBLEM)

    def getCurrentTagsToBeRead(self) -> Tuple[list[str] | None, list[Type[bool | float] | None] | None]:

        # One can have more than 1 tag read in one state. BUT, ALL TAGS MUST BE OF THE SAME TYPE
        # If more than 1 tag is there, for bool values, the AND of these values is the final value
        # If more than 1 tag is there, for float values, the last of these values is the final value

        if self.machineState.getCurrentState() == MachineState.READ_QR_CODE:
            return [IOServer.PLC_PC_CheckQRCode], [bool]
        if self.machineState.getCurrentState() == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE:
            return [IOServer.PLC_PC_CheckKnuckle], [bool]
        if self.machineState.getCurrentState() == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_FIRST_BEARING:
            return [IOServer.PLC_PC_CheckHubAndFirstBearing], [bool]
        if self.machineState.getCurrentState() == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SECOND_BEARING:
            return [IOServer.PLC_PC_CheckSecondBearing], [bool]
        if self.machineState.getCurrentState() == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER:
            return [IOServer.PLC_PC_CheckNutAndPlateWasher], [bool]

        if self.machineState.getCurrentState() == MachineState.READ_TIGHTENING_TORQUE_1_DONE:
            return [IOServer.PLC_PC_Station2TigtheningTorqueDone, IOServer.PLC_PC_Station2TorqueIsWithinRange], [bool, bool]
        if self.machineState.getCurrentState() == MachineState.READ_TIGHTENING_TORQUE_1:
            return [IOServer.PLC_PC_Station2TorqueValue], [float]
        if self.machineState.getCurrentState() == MachineState.READ_ROTATION_TORQUE_1_DONE:
            return [IOServer.PLC_PC_Station3RotationTorqueDone, IOServer.PLC_PC_Station3TorqueIsWithinRange], [bool, bool]
        if self.machineState.getCurrentState() == MachineState.READ_ROTATION_TORQUE_1:
            return [IOServer.PLC_PC_Station3TorqueValue], [float]

        if self.machineState.getCurrentState() == MachineState.READ_TIGHTENING_TORQUE_2_DONE:
            return [IOServer.PLC_PC_Station2TigtheningTorqueDone, IOServer.PLC_PC_Station2TorqueIsWithinRange], [bool, bool]
        if self.machineState.getCurrentState() == MachineState.READ_TIGHTENING_TORQUE_2:
            return [IOServer.PLC_PC_Station2TorqueValue], [float]
        if self.machineState.getCurrentState() == MachineState.READ_ROTATION_TORQUE_2_DONE:
            return [IOServer.PLC_PC_Station3RotationTorqueDone, IOServer.PLC_PC_Station3TorqueIsWithinRange], [bool, bool]
        if self.machineState.getCurrentState() == MachineState.READ_ROTATION_TORQUE_2:
            return [IOServer.PLC_PC_Station3TorqueValue], [float]

        if self.machineState.getCurrentState() == MachineState.READ_TIGHTENING_TORQUE_3_DONE:
            return [IOServer.PLC_PC_Station2TigtheningTorqueDone, IOServer.PLC_PC_Station2TorqueIsWithinRange], [bool, bool]
        if self.machineState.getCurrentState() == MachineState.READ_TIGHTENING_TORQUE_3:
            return [IOServer.PLC_PC_Station2TorqueValue], [float]
        if self.machineState.getCurrentState() == MachineState.READ_ROTATION_TORQUE_3_DONE:
            return [IOServer.PLC_PC_Station3RotationTorqueDone, IOServer.PLC_PC_Station3TorqueIsWithinRange], [bool, bool]
        if self.machineState.getCurrentState() == MachineState.READ_ROTATION_TORQUE_3:
            return [IOServer.PLC_PC_Station3TorqueValue], [float]

        if self.machineState.getCurrentState() == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER:
            return [IOServer.PLC_PC_CheckSplitPinAndWasher], [bool]
        if self.machineState.getCurrentState() == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP:
            return [IOServer.PLC_PC_CheckCap], [bool]
        if self.machineState.getCurrentState() == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNG:
            return [IOServer.PLC_PC_CheckBung], [bool]
        if self.machineState.getCurrentState() == MachineState.READ_PRESS_RESULT_COMPLETED_SUCCESSFULLY:
            return [IOServer.PLC_PC_CapPressDone], [bool]

        return None, None

    def getCurrentTagsToBeWritten(self) -> list[str] | None:

        # NOTE the order of the tags. The OK tag is populated first, and then the Done tag is populated.
        # This order ensures that when the PLC reads the Done tag, it ALWAYS has the CORRECT RESULT of ImageProcessing

        # NOTE A VERY IMPORTANT THING. The 3rd tag is the trigger tag eg CheckKunckle, CheckBearing which is to be reset to False
        # in the same operation, after the first 2 are populated

        if self.machineState.getCurrentState() == MachineState.WRITE_QR_CODE:
            return [IOServer.PC_PLC_QRCodeCheckOK, IOServer.PC_PLC_QRCodeCheckDone, IOServer.PLC_PC_CheckQRCode]
        if self.machineState.getCurrentState() == MachineState.WRITE_RESULT_OF_CHECKING_KNUCKLE:
            return [IOServer.PC_PLC_KnuckleCheckOK, IOServer.PC_PLC_KnuckleCheckDone, IOServer.PLC_PC_CheckKnuckle]
        if self.machineState.getCurrentState() == MachineState.WRITE_RESULT_OF_CHECKING_HUB_AND_FIRST_BEARING:
            return [IOServer.PC_PLC_HubAndFirstBearingCheckOK, IOServer.PC_PLC_HubAndFirstBearingCheckDone, IOServer.PLC_PC_CheckHubAndFirstBearing]
        if self.machineState.getCurrentState() == MachineState.WRITE_RESULT_OF_CHECKING_SECOND_BEARING:
            return [IOServer.PC_PLC_SecondBearingCheckOK, IOServer.PC_PLC_SecondBearingCheckDone, IOServer.PLC_PC_CheckSecondBearing]
        if self.machineState.getCurrentState() == MachineState.WRITE_RESULT_OF_CHECKING_NUT_AND_PLATEWASHER:
            return [IOServer.PC_PLC_NutAndPlateWasherCheckOK, IOServer.PC_PLC_NutAndPlateWasherCheckDone, IOServer.PLC_PC_CheckNutAndPlateWasher]

        if self.machineState.getCurrentState() == MachineState.WRITE_RESULT_OF_CHECKING_SPLITPIN_AND_WASHER:
            return [IOServer.PC_PLC_SplitPinAndWasherCheckOK, IOServer.PC_PLC_SplitPinAndWasherCheckDone, IOServer.PLC_PC_CheckSplitPinAndWasher]
        if self.machineState.getCurrentState() == MachineState.WRITE_RESULT_OF_CHECKING_CAP:
            return [IOServer.PC_PLC_CapCheckOK, IOServer.PC_PLC_CapCheckDone, IOServer.PLC_PC_CheckCap]
        if self.machineState.getCurrentState() == MachineState.WRITE_RESULT_OF_CHECKING_BUNG:
            return [IOServer.PC_PLC_BungCheckOK, IOServer.PC_PLC_BungCheckDone, IOServer.PLC_PC_CheckBung]

        return None

    def connectPLC(self, forceRenew = False) -> bool | None:
        """Connect to the PLC using pycomm3."""
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

            self.clientForReads = LogixDriver(f"{self.plc_ip}") # read from configuration file
            self.clientForReads.open()
            # self.logger.info("Connected to PLC for Reads at %s", self.plc_ip)
            printBoldGreen(f"Connected to PLC for Reads at {self.plc_ip}")

            self.clientForWrites = LogixDriver(f"{self.plc_ip}") # read from configuration file
            self.clientForWrites.open()
            # self.logger.info("Connected to PLC for Writes at %s", self.plc_ip)
            printBoldGreen(f"Connected to PLC for Writes at {self.plc_ip}")
            self.connectionInProgress = False
            self.connectionAttempts = 0
            return True
        except Exception as e:
            # self.logger.error("PLC connection error: %s", e)
            if (self.connectionAttempts == 1) or ((self.connectionAttempts % IOServer.LOG_CONNECTION_PROBLEM_EVERY_N_SECS) % 5) == 0:
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

                tryToConnect : bool = False
                # printLight(f"About to check connection in IOServer")
                if ((not objRef.clientForReads) or (not objRef.clientForReads.connected) or
                        (not objRef.clientForWrites) or (not objRef.clientForWrites.connected)):
                    # objRef.logger.warning(f"PLC connection lost in connection check")
                    # printBoldRed(f"PLC connection lost in connection check. Reconnecting.")
                    tryToConnect = True
                else:
                    try:
                        # printLight(f"About to read PLC_PC_CheckQRCode")
                        readValue : int = 0
                        readValue = self.read_int(IOServer.PLC_PC_WatchTag)  # Test read
                        # objRef.logger.debug(f"PLC connection alive")
                        if readValue != IOServer.WATCH_TAG_VALUE:
                            raise Exception(f"Could not read tag {IOServer.PLC_PC_WatchTag} from PLC")
                        # printBoldGreen(f"PLC connection alive")
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
                                                      args=(), daemon = True)
        self.connectionCheckThread.start()

    def launchHeartbeatThread(self):
        objRef = self
        def reportHeartbeat():
            ioHeartbeatGap = CosThetaConfigurator.getInstance().getIOConnectionStatusSleepInterval()
            while not objRef.shutdown:
                try:
                    if objRef.clientForReads is None or objRef.clientForWrites is None:
                        sendHeartbeatFromIOServerToHeartbeatServer(redisConnection=objRef.heartbeatRedisConnection, status=DEAD)
                    elif not objRef.clientForReads.connected or not objRef.clientForWrites.connected:
                        sendHeartbeatFromIOServerToHeartbeatServer(redisConnection=objRef.heartbeatRedisConnection, status=DEAD)
                    else:
                        sendHeartbeatFromIOServerToHeartbeatServer(redisConnection=objRef.heartbeatRedisConnection, status=ALIVE)
                except Exception as e:
                    printBoldRed(f"Exception {e} encountered in heartbeat")
                    objRef.connectToRedisForHeartbeatAndEmergencyMonitoring(forceRenew=True)
                try:
                    time.sleep(ioHeartbeatGap)
                except:
                    pass
        heartbeatThread = threading.Thread(name=f"IO Heartbeat Thread", target=reportHeartbeat, args=(), daemon = True)
        heartbeatThread.start()

    def launchThreadForUpdatingCombinedHeartbeat(self):
        objRef = self
        def updateHeartbeat():
            heartbeatGap = CosThetaConfigurator.getInstance().getCombinedConnectionStatusUpdateInterval()
            # printBoldBlue(f"{heartbeatGap = }")
            while not objRef.shutdown:
                try:
                    currentStatus = readCombinedHeartbeatInIOServerFromHeartbeatServer(objRef.combinedHeartbeatRedisConnection)
                    # printBoldBlue(f"{currentStatus = }")
                    if currentStatus:
                        IOServer.CONSECUTIVE_CONNECTION_DOWN_COUNT = 0
                        statusToBeSent = not IOServer.LAST_CONNECTION_STATUS_UPDATED
                        # printBoldBlue(f"Combined Heartbeat : {statusToBeSent = } to tag {IOServer.PC_PLC_ConnectionStatusTag = }")
                        objRef.write_bool(IOServer.PC_PLC_ConnectionStatusTag, statusToBeSent, debug = False)
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
                # printBoldBlue(f"About to sleep")
                try:
                    time.sleep(heartbeatGap)
                except:
                    pass
        combinedHeartbeatThread = threading.Thread(name=f"Combined Heartbeat Update Thread", target=updateHeartbeat, args=(), daemon = True)
        combinedHeartbeatThread.start()

    def launchEmergencyMonitoringThread(self):
        objRef = self
        def monitorEmergency():
            # ioHeartbeatGap = CosThetaConfigurator.getInstance().getCameraConnectionStatusSleepInterval()
            while not objRef.shutdown:
                emergencyActivated : bool = False
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
        emergencyMonitoringThread = threading.Thread(name=f"Emergency Monitoring Thread", target=monitorEmergency, args=(), daemon = True)
        emergencyMonitoringThread.start()

    def launchMonitorStopCommandThread(self):
        objRef = self
        def lookForStop():
            while not objRef.shutdown:
                # acquired = IOServer.heartbeatLock.acquire()
                # if acquired:
                try:
                    if objRef.heartbeatRedisConnected:
                        _, stopNow = getStopCommandFromQueue(objRef.heartbeatRedisConnection)
                        if stopNow:
                            # printBoldRed(f"Got stop command in process {os.getpid()}")
                            objRef.shutdown = True
                            sendStoppedResponse(objRef.heartbeatRedisConnection, aProducer=IOServer.logSource)
                    else:
                        objRef.connectToRedisForHeartbeatAndEmergencyMonitoring(False)
                except:
                    pass
                    # QRCodeServer.heartbeatLock.release()
                if not objRef.shutdown:
                    time.sleep(2)
        self.monitorStopThread = threading.Thread(name=f'Monitor Stop Thread in IOServer - IOserver',
                                                      target=lookForStop,
                                                      args=(), daemon = True)
        self.monitorStopThread.start()
        # printLight("Started stop monitoring thread in QRCodeServer!")

    def launchReadLoopThread(self):
        objRef = self

        def readLoop():
            """Read loop to read the input for current machine states"""
            # wroteTagName : bool = False
            written_TagsToBeRead : int = 0
            while not objRef.shutdown:

                # NOTE: One can have more than 1 tag read in one state. BUT, ALL TAGS MUST BE OF THE SAME TYPE
                # If more than 1 tag is there, for bool values, the AND of these values is the final value
                # If more than 1 tag is there, for float values, the last of these values is the final value

                try:
                    valueRead : bool = False
                    goAheadToProcessing : bool = False
                    allok : bool = True
                    typeOfValues : str = "nothing"
                    valuesRead : list = []  # This is either 1 tag or 2 tags. If it's 2 tags, then the first value
                                            # indicates whether the value has been set or not.
                                            # IF the first value is False, then don't call process_current_read_state,
                                            # ELSE call process_current_read_state with the second value
                    finalValue : float = IOServer.FALSE_FLOAT_VALUE

                    if (objRef.clientForReads is not None) and objRef.clientForReads.connected and not objRef.connectionInProgress:
                        tagsToBeRead, valueTypes = objRef.getCurrentTagsToBeRead()
                        # if not wroteTagName:
                        # printLight(f"About to read tags {tagsToBeRead} of type {valueTypes}")
                        if tagsToBeRead is not None:
                            if written_TagsToBeRead == 0:
                                # printBlue(f"Looking to read {tagsToBeRead} of type {valueTypes}")
                                written_TagsToBeRead += 1
                            if len(tagsToBeRead) == 1:
                                goAheadToProcessing = True
                                if valueTypes[0] == bool:
                                    typeOfValues = "bool"
                                    valueRead = objRef.read_bool(tagsToBeRead[0])
                                    allok = allok and valueRead
                                    # if allok:
                                    #     printLight(f"Got {type(valueRead)} value {valueRead} for tag {tagsToBeRead[0]} in state {objRef.machineState.getCurrentState()}")
                                elif valueTypes[0] == float:
                                    typeOfValues = "float"
                                    finalValue = objRef.read_float(tagsToBeRead[0])
                                    # if finalValue != IOServer.FALSE_FLOAT_VALUE:
                                    #     printLight(f"Got {type(finalValue)} value {finalValue} for tag {tagsToBeRead[0]} in state {objRef.machineState.getCurrentState()}")
                            else:
                                typeOfValues = "bool"
                                valueRead = objRef.read_bool(tagsToBeRead[0])
                                allok = allok and valueRead
                                if allok:
                                    # printLight(
                                    #     f"Got {type(valueRead)} value {valueRead} for tag {tagsToBeRead[0]} in state {objRef.machineState.getCurrentState()}")
                                    goAheadToProcessing = True
                                    valueRead = objRef.read_bool(tagsToBeRead[1])
                                    allok = allok and valueRead
                                    # printLight(
                                    #     f"Got {type(valueRead)} value {valueRead} for tag {tagsToBeRead[1]} in state {objRef.machineState.getCurrentState()}")



                            # for index, aValueType in enumerate(valueTypes):
                            #     if aValueType == bool:
                            #         typeOfValues = "bool"
                            #         valueRead = objRef.read_bool(tagsToBeRead[index])
                            #         allok = allok and valueRead
                            #         # if valueRead:
                            #         printLight(f"Got {type(valueRead)} value {valueRead} for tag {tagsToBeRead[index]} in state {objRef.machineState.getCurrentState()}")
                            #     elif aValueType == float:
                            #         typeOfValues = "float"
                            #         finalValue = objRef.read_float(tagsToBeRead[index])
                            #         # if finalValue != IOServer.FALSE_FLOAT_VALUE:
                            #         printLight(f"Got {type(finalValue)} value {finalValue} for tag {tagsToBeRead[index]} in state {objRef.machineState.getCurrentState()}")
                    else:
                        # objRef.logger.fatal(f"PLC connection lost in read loop")
                        # printBoldRed(f"PLC connection lost in read loop")
                        pass

                    # NOTE: process_current_read_state() is called only if a bool value or a float value is found,
                    #       else it is skipped

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
                                                      args=(), daemon = True)
        self.continuousReadLoop.start()

    def launchWriteTagsLoopThread(self):
        objRef = self
        def writeTagsLoop():
            """Main do loop to take action wherever something required
               The actual action is taken by the function perform_action"""

            # NOTE the order of the tags. The OK tag is populated first, and then the Done tag is populated.
            # This order ensures that when the PLC reads the Done tag, it ALWAYS has the CORRECT RESULT of ImageProcessing

            # NOTE A VERY IMPORTANT THING. The 3rd tag is the trigger tag eg CheckKunckle, CheckBearing which is to be reset to False
            # in the same operation, after the first 2 are populated

            written_TagsToBeWritten : int = 0
            while not objRef.shutdown:
                tagsToBeWritten = objRef.getCurrentTagsToBeWritten()
                if (objRef.clientForWrites is not None) and objRef.clientForWrites.connected and not objRef.connectionInProgress:
                    if tagsToBeWritten is not None and len(tagsToBeWritten) > 0:
                        if written_TagsToBeWritten == 0:
                            # printBlue(f"Tags to be written = {tagsToBeWritten}")
                            written_TagsToBeWritten += 1
                        if self.machineState.getCurrentState() == MachineState.WRITE_QR_CODE:
                            success = readDataInIOServerFromQRCodeServer(self.redisConnectionForWrites)
                            if success:
                                objRef.perform_write_state_action(tags=tagsToBeWritten, result=True)
                                printBold(f"Transition from state {objRef.machineState.getCurrentState()}")
                                objRef.machineState.incrementState()
                                printBold(f"        ...to state {objRef.machineState.getCurrentState()}")
                        else:
                            previousMachineState, evaluation = readDataInIOServerFromCameraServer(self.redisConnectionForWrites)
                            # printBoldBlue(f"In IOServer, received {previousMachineState = } and {evaluation = } from Camera")
                            if evaluation:
                                objRef.perform_write_state_action(tags=tagsToBeWritten, result=True)
                                printBold(f"Transition from state {objRef.machineState.getCurrentState()}")
                                objRef.machineState.incrementState()
                                printBold(f"        ...to state {objRef.machineState.getCurrentState()}")
                            else:
                                if previousMachineState != MachineState.INVALID_STATE:
                                    objRef.perform_write_state_action(tags=tagsToBeWritten, result=False)
                                    printBoldRed(f"Did not move ahead from state {objRef.machineState.getCurrentState()}")
                                    printBold(f"Transition from state {objRef.machineState.getCurrentState()}")
                                    objRef.machineState.decrementState()
                                    printBold(f"        ...to state {objRef.machineState.getCurrentState()}")
                                # pass
                time.sleep(objRef.write_loop_interval)

        self.continuousDoLoop = threading.Thread(name=f'Continuous Do Loop in IOServer',
                                                      target=writeTagsLoop,
                                                      args=(), daemon = True)
        self.continuousDoLoop.start()

    def process_current_read_state(self, valueRead : bool | float | None):
        # printYellow(f"Called process_current_read_state() with {valueRead = }, in machine state {MachineState.getMachineStateFromString(self.machineState.getCurrentState())}")
        if valueRead is None:
            return False

        returnValue : bool = False

        if type(valueRead) == bool:
            if valueRead:
                # If there are more than one bool type, then the result is an AND of all the boolean values
                # If there are more than one float type, then the result is the last of those values
                successfullySent : bool = False
                try:
                    if self.machineState.getCurrentState() == MachineState.READ_QR_CODE:
                        successfullySent = sendDataFromIOServerToQRCodeServer(self.redisConnectionForReads)
                    elif self.machineState.getCurrentState() == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads, currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE)
                    elif self.machineState.getCurrentState() == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_FIRST_BEARING:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads, currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_FIRST_BEARING)
                    elif self.machineState.getCurrentState() == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SECOND_BEARING:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads, currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SECOND_BEARING)
                    elif self.machineState.getCurrentState() == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads, currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER)
                    elif self.machineState.getCurrentState() == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads, currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER)
                    elif self.machineState.getCurrentState() == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads, currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP)
                    elif self.machineState.getCurrentState() == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNG:
                        successfullySent = sendDataFromIOServerToCameraServer(self.redisConnectionForReads, currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNG)
                    elif self.machineState.getCurrentState() == MachineState.READ_PRESS_RESULT_COMPLETED_SUCCESSFULLY:
                        successfullySent = sendDataFromIOServerToFEServer(self.redisConnectionForReads, currentMachineState=MachineState.READ_PRESS_RESULT_COMPLETED_SUCCESSFULLY,
                                                                          moveToState=MachineState.READ_QR_CODE, action=moveAheadToNextComponent)
                    elif self.machineState.getCurrentState() == MachineState.READ_TIGHTENING_TORQUE_1_DONE:
                        successfullySent = True
                    elif self.machineState.getCurrentState() == MachineState.READ_TIGHTENING_TORQUE_2_DONE:
                        successfullySent = True
                    elif self.machineState.getCurrentState() == MachineState.READ_TIGHTENING_TORQUE_3_DONE:
                        successfullySent = True
                    elif self.machineState.getCurrentState() == MachineState.READ_ROTATION_TORQUE_1_DONE:
                        successfullySent = True
                    elif self.machineState.getCurrentState() == MachineState.READ_ROTATION_TORQUE_2_DONE:
                        successfullySent = True
                    elif self.machineState.getCurrentState() == MachineState.READ_ROTATION_TORQUE_3_DONE:
                        successfullySent = True

                    if successfullySent:
                        printBold(f"Transition from state {self.machineState.getCurrentState()}")
                        self.machineState.incrementState()
                        printBold(f"        ...to state {self.machineState.getCurrentState()}")
                except Exception as e:
                    printBoldRed(f"Exception {e} encountered in current_read_state - 1")
                # returnValue = successfullySent
                returnValue = valueRead
            else: # if valueRead is False
                success : bool = True
                if self.machineState.getCurrentState() in (MachineState.READ_ROTATION_TORQUE_1_DONE,
                        MachineState.READ_ROTATION_TORQUE_2_DONE, MachineState.READ_ROTATION_TORQUE_3_DONE):
                    success = success and self.write_bool(IOServer.PLC_PC_Station2TigtheningTorqueDone, False)
                    success = success and self.write_bool(IOServer.PLC_PC_Station2TorqueIsWithinRange, False)
                    success = success and self.write_bool(IOServer.PLC_PC_Station3RotationTorqueDone, False)
                    success = success and self.write_bool(IOServer.PLC_PC_Station3TorqueIsWithinRange, False)
                    if success:
                        printBold(f"Transition from state {self.machineState.getCurrentState()}")
                        self.machineState.decrementState(2)
                        printBold(f"        ...to state {self.machineState.getCurrentState()}")
                        newState = self.machineState.getCurrentState()
                        try:
                            sendDataFromIOServerToFEServer(self.redisConnectionForReads,
                                                           currentMachineState=MachineState.READ_TIGHTENING_TORQUE_1,
                                                           moveToState=newState, result=notok)
                        except Exception as e:
                            printBoldRed(f"Exception {e} encountered in current_read_state - 2")
                returnValue = valueRead
        elif type(valueRead) == float:
            if valueRead == IOServer.FALSE_FLOAT_VALUE:
                printBold(f"Failed to read proper value of float in state {self.machineState.getCurrentState()}")
                returnValue = False
            else:
                successfullySent : bool = False
                try:
                    if self.machineState.getCurrentState() == MachineState.READ_TIGHTENING_TORQUE_1:
                        successfullySent = sendDataFromIOServerToFEServer(self.redisConnectionForReads, currentMachineState=MachineState.READ_TIGHTENING_TORQUE_1, value=valueRead)
                    elif self.machineState.getCurrentState() == MachineState.READ_ROTATION_TORQUE_1:
                        successfullySent = sendDataFromIOServerToFEServer(self.redisConnectionForReads, currentMachineState=MachineState.READ_ROTATION_TORQUE_1, value=valueRead)
                    elif self.machineState.getCurrentState() == MachineState.READ_TIGHTENING_TORQUE_2:
                        successfullySent = sendDataFromIOServerToFEServer(self.redisConnectionForReads, currentMachineState=MachineState.READ_TIGHTENING_TORQUE_2, value=valueRead)
                    elif self.machineState.getCurrentState() == MachineState.READ_ROTATION_TORQUE_2:
                        successfullySent = sendDataFromIOServerToFEServer(self.redisConnectionForReads, currentMachineState=MachineState.READ_ROTATION_TORQUE_2, value=valueRead)
                    elif self.machineState.getCurrentState() == MachineState.READ_TIGHTENING_TORQUE_3:
                        successfullySent = sendDataFromIOServerToFEServer(self.redisConnectionForReads, currentMachineState=MachineState.READ_TIGHTENING_TORQUE_3, value=valueRead)
                    elif self.machineState.getCurrentState() == MachineState.READ_ROTATION_TORQUE_3:
                        successfullySent = sendDataFromIOServerToFEServer(self.redisConnectionForReads, currentMachineState=MachineState.READ_ROTATION_TORQUE_3, value=valueRead)

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

    def perform_write_state_action(self, tags: list[str], result : bool = True) -> bool:
        """Placeholder for performing actions (e.g., take picture, analyze)."""
        # NOTE : There will ALWAYS be 3 tags in the list - Tag 1 is for OK, Tag 2 is for Done, and Tag 3 is for the trigger tag that caused the action - in THAT ORDER
        # ALWAYS write result to the OK tag first, and then TRUE to the Done tag, and then FALSE to the trigger tag

        # TESTING ROUTINE

        # aRandNumber = random.randint(2,3)
        # printLight(f"                    ---- Do loop is about to sleep for {aRandNumber} secs")
        # time.sleep(float(aRandNumber))
        # aRandNumber = random.randint(1,20)
        # if aRandNumber == 1:
        #     result = False
        # else:
        #     result = True

        success1 = self.write_bool(tags[0], result)
        # printBoldGreen(f"Wrote value {result} to {tags[0]}")
        time.sleep(IOServer.PLC_SLEEPTIME_BETWEEN_OK_AND_DONE)
        success2 = self.write_bool(tags[1], True)
        # printBoldGreen(f"Wrote value True to {tags[1]}")
        # success3 = self.write_bool(tags[2], False)
        # printBoldGreen(f"Wrote value False to {tags[2]}")

        # if success1 and success2 and success3:
        if success1 and success2:
            self.logger.info(f"Performed action for, and wrote to tags {tags}")
            # printBoldBlue(f"Performed action for, and wrote to tags {tags}")
        else:
            self.logger.info(f"Could not write to tags {tags}")
            printBoldRed(f"Could not write to tags {tags}")

        return result

    def startThreads(self):
        """Start the read and connection check threads."""
        # self.logger.info("Starting read, connection, and do threads in IO Server")
        printLight("Starting read, connection, and do threads in IO Server")

        self.launchConnectionMonitoringThread()
        # self.logger.info("Started connection monitoring thread in IOServer")
        printLight("Started connection monitoring thread in IOServer")

        self.launchReadLoopThread()
        # self.logger.info("Started read loop thread in IOServer")
        printLight("Started read loop thread in IOServer")

        self.launchWriteTagsLoopThread()
        # self.logger.info("Started do loop thread in IOServer")
        printLight("Started do loop thread in IOServer")

        self.launchHeartbeatThread()
        # self.logger.info("Started heartbeat thread in IOServer")
        printLight("Started heartbeat thread in IOServer")

        self.launchEmergencyMonitoringThread()
        # self.logger.info("Started emergency monitoring thread in IOServer")
        printLight("Started emergency monitoring thread in IOServer")

        self.launchMonitorStopCommandThread()
        # self.logger.info("Started monitorStop thread in IOServer")
        printLight("Started monitorStop thread in IOServer")

        self.launchThreadForUpdatingCombinedHeartbeat()
        # self.logger.info("Started combinedHeartbeat update thread in IOServer")
        printLight("Started combinedHeartbeat update thread in IOServer")

        # self.logger.info("Started read, connection, and do threads")
        printLight("Started read tag, connection monitoring, and write tag threads")

    # Read functions
    # Returns True or False if a bool value has been read. If no value is read, it returns -1.
    # Ensure you are reading a BOOLEAN TAG in this function
    def read_bool(self, tag: str, expected_value: bool = True, debug: bool = False) -> Optional[bool]:
        """Read a boolean tag from the PLC."""
        if tag is None:
            return False
        if tag == "":
            return False
        if (self.clientForReads is None) or (not self.clientForReads.connected):
            return False
        try:
            value : bool = False
            with self.readingLock:
                value = self.clientForReads.read(tag).value
            if value is None:
                self.logger.error("Error reading bool tag %s", tag)
                printBoldRed(f"Error reading bool tag {tag}")
                return not expected_value
            if debug and value == expected_value:
                self.logger.info("Read bool tag %s: %s", tag, value)
                # printBoldGreen(f"Read bool tag {tag}: {value}")
            return value
        except Exception as e:
            self.logger.error("Error reading bool tag %s: %s", tag, e)
            printBoldRed(f"Error reading bool tag {tag}: {e}")
            # return not expected_value
            return not expected_value

    def read_float(self, tag: str, debug: bool = False) -> Optional[float]:
        """Read a float tag from the PLC."""
        if tag is None:
            return IOServer.FALSE_FLOAT_VALUE
        if tag == "":
            return IOServer.FALSE_FLOAT_VALUE
        if (self.clientForReads is None) or (not self.clientForReads.connected):
            return IOServer.FALSE_FLOAT_VALUE
        try:
            value : float = IOServer.FALSE_FLOAT_VALUE
            with self.readingLock:
                value = self.clientForReads.read(tag).value
            if value is None:
                # self.logger.error("Error reading float tag %s", tag)
                printBoldRed(f"Error reading float tag {tag}")
                return IOServer.FALSE_FLOAT_VALUE
            # self.logger.info("Read float tag %s: %f", tag, value)
            # printBoldGreen(f"Read float tag {tag}: {value}")
            return value
        except Exception as e:
            # self.logger.error("Error reading float tag %s: %s", tag, e)
            printBoldRed(f"Error reading float tag {tag}: {e}")
            return IOServer.FALSE_FLOAT_VALUE

    def read_int(self, tag: str, debug: bool = False) -> Optional[int]:
        """Read an integer tag from the PLC."""
        if tag is None:
            return IOServer.FALSE_INT_VALUE
        if tag == "":
            return IOServer.FALSE_INT_VALUE
        if (self.clientForReads is None) or (not self.clientForReads.connected):
            return IOServer.FALSE_INT_VALUE
        try:
            value: int = IOServer.FALSE_INT_VALUE
            if tag is None:
                self.logger.error("Error reading integer tag: tag is None")
                printBoldRed("Error reading integer tag: tag is None")
                return IOServer.FALSE_INT_VALUE
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

    def read_single(self, aTag : str) -> Any:
        if aTag is None:
            return IOServer.FALSE_FLOAT_VALUE
        if aTag == "":
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

    def read_multiple(self, listOfTags : list) -> Any:
        if listOfTags is None:
            return None
        if len(listOfTags) == 0:
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

    def read_array(self, arrayName : str, numberOfElements : int) -> Any:
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

    def read_array_slice(self, arrayName : str, index : int, numberOfElements : int) -> Any:
        value = []
        try:
            with self.readingLock:
                value = self.clientForReads.read(f"{arrayName}[{index}]" + "{" + f"{numberOfElements}" + "}")
        except:
            pass
        return value

    # def read_strings(arrayName : str, index : int, numberOfElements : int):
    #     with LogixDriver(plcIP) as plc:
    #         return plc.read('STRING1', 'STRING_ARY1[2]{2}')

    # Write functions
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
                # self.logger.info("Wrote bool tag %s: %s", tag, value)
                printBoldGreen(f"Wrote bool tag {tag}: {value}")
            return True
        except Exception as e:
            # self.logger.error("Error writing bool tag %s: %s", tag, e)
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
        """Reset a tag to False."""
        return self.write_float(tag=tag, value=0.0, debug=debug)

    def resetAll(self, debug: bool = False) -> bool:
        """Reset all tags to False if bool, or to 0.0, if float"""

        self.resetTagToFalse(IOServer.PLC_PC_CheckQRCode)
        self.resetTagToFalse(IOServer.PC_PLC_QRCodeCheckOK)
        self.resetTagToFalse(IOServer.PC_PLC_QRCodeCheckDone)

        self.resetTagToFalse(IOServer.PLC_PC_CheckKnuckle)
        self.resetTagToFalse(IOServer.PC_PLC_KnuckleCheckOK)
        self.resetTagToFalse(IOServer.PC_PLC_KnuckleCheckDone)

        self.resetTagToFalse(IOServer.PLC_PC_CheckHubAndFirstBearing)
        self.resetTagToFalse(IOServer.PC_PLC_HubAndFirstBearingCheckOK)
        self.resetTagToFalse(IOServer.PC_PLC_HubAndFirstBearingCheckDone)

        self.resetTagToFalse(IOServer.PLC_PC_CheckSecondBearing)
        self.resetTagToFalse(IOServer.PC_PLC_SecondBearingCheckOK)
        self.resetTagToFalse(IOServer.PC_PLC_SecondBearingCheckDone)

        self.resetTagToFalse(IOServer.PLC_PC_CheckNutAndPlateWasher)
        self.resetTagToFalse(IOServer.PC_PLC_NutAndPlateWasherCheckOK)
        self.resetTagToFalse(IOServer.PC_PLC_NutAndPlateWasherCheckDone)

        self.resetTagToFalse(IOServer.PLC_PC_Station2TigtheningTorqueDone)
        self.resetTagToFalse(IOServer.PLC_PC_Station2TorqueIsWithinRange)
        self.resetTagToZero(IOServer.PLC_PC_Station2TorqueValue)

        self.resetTagToFalse(IOServer.PLC_PC_Station3RotationTorqueDone)
        self.resetTagToFalse(IOServer.PLC_PC_Station3TorqueIsWithinRange)
        self.resetTagToZero(IOServer.PLC_PC_Station3TorqueValue)

        self.resetTagToFalse(IOServer.PLC_PC_CheckSplitPinAndWasher)
        self.resetTagToFalse(IOServer.PC_PLC_SplitPinAndWasherCheckOK)
        self.resetTagToFalse(IOServer.PC_PLC_SplitPinAndWasherCheckDone)

        self.resetTagToFalse(IOServer.PLC_PC_CheckCap)
        self.resetTagToFalse(IOServer.PC_PLC_CapCheckOK)
        self.resetTagToFalse(IOServer.PC_PLC_CapCheckDone)

        self.resetTagToFalse(IOServer.PLC_PC_CheckBung)
        self.resetTagToFalse(IOServer.PC_PLC_BungCheckOK)
        self.resetTagToFalse(IOServer.PC_PLC_BungCheckDone)

        self.resetTagToFalse(IOServer.PLC_PC_CapPressDone)
        self.resetTagToFalse(IOServer.PLC_PC_EmergencyAbort)

    def get_tag_address(self, tag: str) -> int:
        """Placeholder for mapping tags to addresses (not needed for pycomm3)."""
        return 0  # Not used with pycomm3

    def find_pids(self):

        # PIDs are structures, the data_type attribute will be a dict with data type definition.
        # For tag types of 'atomic' the data type will a string, we need to skip those first.
        # Then we can just look for tags whose data type name matches 'PID'
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

# *********************************************************************

# startIOServer()

# printLight(f"The attributes are :\n {io.find_attributes()}")
# printLight(f"The PIDs are :\n {io.find_pids()}")

# if __name__ == "__main__":
#     io = IOServer()
#     print(f"The attributes are :\n {io.find_attributes()}")
#     print(f"The PIDs are :\n {io.find_pids()}")

# import time
#
# from pycomm3 import LogixDriver
# from pycomm3 import DINT, SHORT_STRING, Struct, STRING, REAL
# from pycomm3.logger import configure_default_logger, LOG_VERBOSE
#
# plcIP = '192.168.1.1'
#
#
# def write_single(tag : str, value:int):
#     with LogixDriver(plcIP) as plc:
#         val = plc.write((tag, value))
#         print(f"Written {val}")
#         return val
#
# def write_multiple(tagValueDict : dict):
#     with LogixDriver(plcIP) as plc:
#         for key, value in tagValueDict:
#             plc.write((key, value))
#
# def write_structure(recipe_data : dict):
#     with LogixDriver(plcIP) as plc:
#         plc.write(('Example_Recipe', recipe_data))
#
# def tag_list_equal():
#     with LogixDriver(plcIP) as plc:
#         tag_list = plc.get_tag_list()
#         if {tag['tag_name']: tag for tag in tag_list} == plc.tags:
#             print('They are the same!')
#
#     with LogixDriver(plcIP, init_tags=False) as plc2:
#         plc2.get_tag_list()
#
#     if plc.tags == plc2.tags:
#         print('Calling get_tag_list() does the same thing.')
#     else:
#         print('Calling get_tag_list() does NOT do the same.')
#
#
# configure_default_logger(level=LOG_VERBOSE, filename='pycomm3.log')
# configure_default_logger(level=logging.CRITICAL, filename='pycomm3.log')
#
# print(DINT.encode(112233))
# # b'i\xb6\x01\x00'
# print(DINT.decode(b'\x12\x34\x56\x78'))
# # 2018915346
# print(SHORT_STRING.encode('Hello there!'))
# # b'\x0cHello there!'
# print(SHORT_STRING.decode(b'\x0eGeneral Kenobi'))
# # 'General Kenobi'
#
#
# MyStruct = Struct(DINT('code'), STRING('name'), REAL('value'))
# struct_values = {
#     'code': 80,
#     'name': 'my name',
#     'value': 123.45
# }
# print(MyStruct.encode(struct_values))
# # b'P\x00\x00\x00\x07\x00my namef\xe6\xf6B'
# print(find_pids())
# find_attributes()
# tag_list_equal()
# print(read_single('m_ControlOn'))
# # print(read_multiple())
# print(write_single('m_ControlOn', True))
# print(write_single('Blinker', True))
# for i in range(10):
#     print(read_single('Blinker'))
#     time.sleep(1)


# plc = LogixDriver(f"192.168.1.1")
# plc.open()
# cValue = plc.read("PLC_PC_CheckQRCode")
# print(cValue)