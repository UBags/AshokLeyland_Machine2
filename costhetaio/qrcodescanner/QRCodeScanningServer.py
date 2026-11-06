### Understanding the Requirement

from utils.SuppressedExceptions import noteSuppressed   # logs exceptions that are caught and ignored
import re
import copy
from typing import Union, Any
from threading import Thread, Lock
import time
import sys
from time import sleep

import serial
from serial import Serial
from redis import Redis

from processors.GenericQueueProcessor import GenericQueueProcessor
from utils.CosThetaPrintUtils import *  # Assuming this has printBoldRed, etc., but minimized use in favor of logging
from utils.CosThetaFileUtils import *
from utils.QRCodeHelper import *
from persistence.Persistence import *
from logutils.SlaveLoggers import *
from logutils.Logger import *
from utils.RedisUtils import *
from Configuration import CosThetaConfigurator

# Placeholder constants (define these in a constants module if not already)
qrCodeKey = "qrCode"
GetAndValidateQRCodeCommand = "GetAndValidateQRCode"

class MonitorGetQRCodeQueue(GenericQueueProcessor):
    """
    Monitors a Redis queue for QR code scan requests and forwards them to the consumer.
    Continuously re-adds a monitor command to its own queue for polling.
    """
    logSource = getFullyQualifiedName(__file__)
    monitorQCommand: str = "monitorQ"

    def __init__(self, name: str, consumer: Union[Thread, "GenericQueueProcessor", Any, None] = None, timeout: int = 1,
                 sleepTime: float = CosThetaConfigurator.getInstance().getQRCodeRequestSleepInterval(),
                 blocking: bool = True, monitorRedisQueueForStopping: bool = True, max_size: int = 32, **kwargs):
        super().__init__(name=name, consumer=consumer, sleepTime=sleepTime, blocking=blocking, timeout=timeout,
                         monitorRedisQueueForStopping=monitorRedisQueueForStopping, max_size=max_size, **kwargs)
        MonitorGetQRCodeQueue.logSource = getFullyQualifiedName(__file__, __class__)
        if consumer is None:
            SlaveFileLogger.getInstance().logConsiderAction(MonitorGetQRCodeQueue.logSource,
                                                            f"In {MonitorGetQRCodeQueue.logSource}.{self.name}, consumer instance is None",
                                                            Logger.ISSUE)
            SlaveConsoleLogger.getInstance().logConsiderAction(MonitorGetQRCodeQueue.logSource,
                                                               f"In {MonitorGetQRCodeQueue.logSource}.{self.name}, consumer instance is None",
                                                               Logger.ISSUE)
            raise Exception("Consumer is needed for MonitorGetQRCodeQueue")
        self.redisConnectionQ: Union[Redis, None] = None
        self.clientRedisConnectedQ: bool = False
        self.connectToRedis()
        self.addItem(MonitorGetQRCodeQueue.monitorQCommand)

    def connectToRedis(self, forceRenew: bool = False):
        """Establishes or renews the Redis connection for monitoring the queue."""
        if forceRenew:
            self.redisConnectionQ = None
            self.clientRedisConnectedQ = False
        if not self.clientRedisConnectedQ:
            try:
                self.redisConnectionQ = Redis(GenericQueueProcessor.redisHost, GenericQueueProcessor.redisPort,
                                              retry_on_timeout=True)
                self.clientRedisConnectedQ = True
            except Exception as e:
                self.clientRedisConnectedQ = False
                SlaveConsoleLogger.getInstance().logTakeAction(MonitorGetQRCodeQueue.logSource, f'Could not get Redis Connection: {e}', Logger.PROBLEM)
                SlaveFileLogger.getInstance().logTakeAction(MonitorGetQRCodeQueue.logSource, f'Could not get Redis Connection: {e}', Logger.PROBLEM)

    def getItem(self, blocking: bool = True) -> Union[Any, None]:
        return super().getItem(blocking)

    def preWorkLoop(self):
        pass

    def postWorkLoop(self):
        if self.monitorRedisQueueForStopping:
            sendStoppedResponse(self.redisConnectionForMonitoringStop, f"{MonitorGetQRCodeQueue.logSource}.{self.name}")

    def processItem(self, item: Any) -> Any:
        """Processes the monitor command by polling Redis and forwarding requests."""
        if not self.getStopped():
            self.addItem(MonitorGetQRCodeQueue.monitorQCommand)
        if item != MonitorGetQRCodeQueue.monitorQCommand:
            return None
        timeOfMessage: Union[str, float, None] = None
        getNextQRCode: bool = False
        try:
            timeOfMessage, getNextQRCode = readDataInQRCodeServerFromIOServer(redisConnection=self.redisConnectionQ, block=500)
        except Exception as e:
            SlaveConsoleLogger.getInstance().logTakeAction(MonitorGetQRCodeQueue.logSource, f'Error reading from Redis: {e}', Logger.PROBLEM)
            self.connectToRedis(forceRenew=True)
        if getNextQRCode:
            if isinstance(self.consumer, GetAndValidateQRCode):
                if self.consumer.isReadInProgress():
                    self.consumer.abortQRCodeRead()
                    time.sleep(4 * self.consumer.getSleepTimeBetweenReads())

            timeOfMessage = float(timeOfMessage) if timeOfMessage else time.time()
            messageToBeSentToConsumer = {timeKeyString: timeOfMessage, actionKeyString: GetAndValidateQRCodeCommand}
            return messageToBeSentToConsumer
        return None

class GetAndValidateQRCode(GenericQueueProcessor):
    """
    Reads QR codes from the serial scanner, validates them against regex patterns,
    and forwards valid ones to the consumer. Supports aborting the read process.
    """
    logSource = getFullyQualifiedName(__file__)

    def __init__(self, name: str, consumer: Union[Thread, "GenericQueueProcessor", Any] = None,
                 parent: "QRCodeScanningServer" = None, timeout: int = 1, sleepTime: float = 0.1,
                 blocking: bool = True, monitorRedisQueueForStopping: bool = True, max_size: int = 32, **kwargs):
        super().__init__(name, consumer, timeout, sleepTime, blocking, monitorRedisQueueForStopping, max_size, **kwargs)
        GetAndValidateQRCode.logSource = getFullyQualifiedName(__file__, __class__)
        if parent is None:
            raise Exception("Parent QRCodeScanningServer is required for serial access in GetAndValidateQRCode()")
        self.parent = parent
        try:
            self.regexPatterns: list[str] = CosThetaConfigurator.getInstance().getQRCodeRegexPatterns()
        except Exception as e:
            SlaveConsoleLogger.getInstance().logTakeAction(GetAndValidateQRCode.logSource, f"Exception while reading regex patterns: {e}", Logger.PROBLEM)
        self.currentQRCodeValue: str = ""
        self.abortRead: bool = False
        self.readInProgress: bool = False

    def isReadInProgress(self) -> bool:
        return self.readInProgress

    def getSleepTimeBetweenReads(self) -> float:
        return self.sleepTime

    def evaluateQRCode(self, qrCode: str, printQRCode: bool = False) -> bool:
        """Validates the QR code against configured regex patterns."""
        qrCodeIsValid: bool = False
        if qrCode and qrCode != "":
            copyOfQRCode = copy.deepcopy(qrCode)
            for index, regexPattern in enumerate(self.regexPatterns):
                try:
                    # The pattern matches the START of the code: a real code goes on after it (e.g. "...$1$SPDL
                    # ASSY-KNULH$SNPL-MAT$"). re.fullmatch (CHANGES 84) rejected every real code - back to re.match
                    # (CHANGES 85).
                    results = re.match(regexPattern, copyOfQRCode.strip())
                    if results:
                        qrCodeIsValid = True
                        if printQRCode:
                            SlaveConsoleLogger.getInstance().logInfo(GetAndValidateQRCode.logSource, f"Found match with {regexPattern}", Logger.SUCCESS)
                        break
                except Exception as e:
                    SlaveConsoleLogger.getInstance().logTakeAction(GetAndValidateQRCode.logSource, f"Exception during regex match: {e}", Logger.PROBLEM)
            if qrCodeIsValid:
                SlaveConsoleLogger.getInstance().logInfo(GetAndValidateQRCode.logSource, f"Validated QRCode {qrCode}", Logger.SUCCESS)
                SlaveFileLogger.getInstance().logInfo(GetAndValidateQRCode.logSource, f"Validated QRCode {qrCode}", Logger.SUCCESS)
            else:
                SlaveConsoleLogger.getInstance().logInfo(GetAndValidateQRCode.logSource, f"Invalid QRCode {qrCode}", Logger.PROBLEM)
                SlaveFileLogger.getInstance().logInfo(GetAndValidateQRCode.logSource, f"Invalid QRCode {qrCode}", Logger.PROBLEM)
        return qrCodeIsValid

    def resetQRCodeValues(self):
        """Resets the current QR code value and clears the serial input buffer."""
        self.currentQRCodeValue = ""
        if self.parent.qrCodeSerialConnection:
            try:
                self.parent.qrCodeSerialConnection.reset_input_buffer()
            except Exception as e:
                SlaveConsoleLogger.getInstance().logTakeAction(GetAndValidateQRCode.logSource, f"Failed to reset input buffer: {e}", Logger.PROBLEM)
                SlaveFileLogger.getInstance().logTakeAction(GetAndValidateQRCode.logSource, f"Failed to reset input buffer: {e}", Logger.PROBLEM)

    def abortQRCodeRead(self):
        """Sets the flag to abort the current read process."""
        if self.readInProgress:
            printBoldRed(f"Aborting an existing read by setting {self.abortRead = } in abortQRCodeRead() of GetAndValidateQRCode")
            self.abortRead = True
            SlaveConsoleLogger.getInstance().logInfo(GetAndValidateQRCode.logSource, "Aborting QR code read", Logger.SUCCESS)
            SlaveFileLogger.getInstance().logInfo(GetAndValidateQRCode.logSource, "Aborting QR code read", Logger.SUCCESS)

    def preWorkLoop(self):
        pass

    def postWorkLoop(self):
        if self.monitorRedisQueueForStopping:
            sendStoppedResponse(self.redisConnectionForMonitoringStop, f"{GetAndValidateQRCode.logSource}.{self.name}")

    def processItem(self, item: Any) -> Any:
        """Processes a scan request by reading and validating QR codes until valid or aborted."""
        timeOfMessage = item.get(timeKeyString)
        action = item.get(actionKeyString)
        if action != GetAndValidateQRCodeCommand:
            return None
        self.resetQRCodeValues()
        self.abortRead = False
        self.readInProgress = True
        messageToConsumer : Union[dict, None] = None
        while not self.abortRead and not self.getStopped():
            self.lastLoopAt = time.monotonic()        # waiting for a scan is going round the loop (WorkerHealth)
            try:
                if self.parent.getQRCodeConnectionStatus():
                    data = self.parent.qrCodeSerialConnection.readline()
                    if (data is not None) and len(data) > 0:
                        printLight(f"Got QR Code : {data} ")
                    if data:
                        qrCode = data.decode('utf-8').strip()
                        if self.evaluateQRCode(qrCode):
                            self.currentQRCodeValue = qrCode
                            messageToConsumer = {timeKeyString: timeOfMessage, qrCodeKey: qrCode}
                            self.readInProgress = False
                            break
                else:
                    SlaveConsoleLogger.getInstance().logTakeAction(GetAndValidateQRCode.logSource, "Scanner not connected; retrying", Logger.ISSUE)
            except Exception as e:
                SlaveConsoleLogger.getInstance().logTakeAction(GetAndValidateQRCode.logSource, f"Error reading from serial: {e}", Logger.PROBLEM)
                SlaveFileLogger.getInstance().logTakeAction(GetAndValidateQRCode.logSource, f"Error reading from serial: {e}", Logger.PROBLEM)
            sleep(self.sleepTime)
        if self.abortRead:
            printBoldRed(f"Came out of read loop in processItem() of GetAndValidateQRCode because {self.abortRead = }")
        self.abortRead = False
        self.readInProgress = False
        return messageToConsumer

class QRCodeDispatcher(GenericQueueProcessor):
    """
    Dispatches valid QR codes to required servers via Redis after generating a display string.
    Checks for duplicates in the database.
    """
    logSource = getFullyQualifiedName(__file__)

    def __init__(self, name: str, mode : str, timeout: int = 1, sleepTime: float = 0.25, blocking: bool = True,
                 monitorRedisQueueForStopping: bool = True, max_size: int = 32, **kwargs):
        super().__init__(name, consumer=None, timeout=timeout, sleepTime=sleepTime, blocking=blocking,
                         monitorRedisQueueForStopping=monitorRedisQueueForStopping, max_size=max_size, **kwargs)
        QRCodeDispatcher.logSource = getFullyQualifiedName(__file__, __class__)
        self.mode : str = mode
        self.redisConnectionQRCodeResponse: Union[Redis, None] = None
        self.redisConnectionQRCodeResponseConnected: bool = False
        self.partMappings = CosThetaConfigurator.getInstance().getQRCodePartMappingPatterns()
        printLight(f"Got part mappings : {self.partMappings}")
        self.currentQRCodeValue: str = ""
        self.displayString: str = ""
        self.connectToRedisForSendingQRCode()

    def connectToRedisForSendingQRCode(self, forceRenew: bool = False):
        """Establishes or renews the Redis connection for dispatching QR codes."""
        if forceRenew:
            self.redisConnectionQRCodeResponse = None
            self.redisConnectionQRCodeResponseConnected = False
        if not self.redisConnectionQRCodeResponseConnected:
            try:
                self.redisConnectionQRCodeResponse = Redis(GenericQueueProcessor.redisHost, GenericQueueProcessor.redisPort, retry_on_timeout=True)
                self.redisConnectionQRCodeResponseConnected = True
                SlaveFileLogger.getInstance().logDebug(QRCodeDispatcher.logSource,
                                                       f'Redis Connection established: {self.redisConnectionQRCodeResponse}',
                                                       Logger.SUCCESS)
            except Exception as e:
                self.redisConnectionQRCodeResponseConnected = False
                SlaveConsoleLogger.getInstance().logTakeAction(QRCodeDispatcher.logSource, f'Could not get Redis Connection: {e}', Logger.PROBLEM)
                SlaveFileLogger.getInstance().logTakeAction(QRCodeDispatcher.logSource, f'Could not get Redis Connection: {e}', Logger.PROBLEM)

    def createDisplayString(self, qrCode: str, printQRCode: bool = False) -> Union[str, None]:
        """Generates a human-readable display string for the QR code, checking for duplicates."""
        if (qrCode is None) or qrCode == "":
            self.displayString = ""
            return None
        # the database name only: creating the database / schema / tables / seed rows is done once, by MainProgram at
        # start (before 2026-11-06 it was re-run here on every scan)
        setDatabaseName(self.mode, createDB=False)
        duplicateExists = checkIfQRCodeExists(qr_code=qrCode, db_name=getDatabaseName())
        copyOfQRCode = copy.deepcopy(qrCode)
        tempDisplayValue: str = ""
        remainingQrCode: str = copy.deepcopy(qrCode)
        for key, value in self.partMappings.items():
            if copyOfQRCode.startswith(key):
                tempDisplayValue = f"{value}"
                remainingQrCode = copyOfQRCode.removeprefix(f"{key}$")
                break
        remainingParts = remainingQrCode.split("$")
        if len(remainingParts) >= 2:
            suffix = f" : {remainingParts[0]} : {remainingParts[1]}"
            tempDisplayValue = f"{tempDisplayValue}{suffix}"
        self.displayString = tempDisplayValue
        if duplicateExists:
            self.displayString = f"{self.displayString} - DUPLICATE"
        return self.displayString

    def getCurrentQRCode(self) -> str:
        return self.currentQRCodeValue

    def getCurrentQRCodeDisplayString(self) -> str:
        return self.displayString

    def printValues(self):
        printBold(f"The current QR Code is {self.currentQRCodeValue}")
        printBold(f"The current display string is {self.displayString}")

    def preWorkLoop(self):
        pass

    def postWorkLoop(self):
        if self.monitorRedisQueueForStopping:
            sendStoppedResponse(self.redisConnectionForMonitoringStop, f"{QRCodeDispatcher.logSource}.{self.name}")

    def processItem(self, item: Any) -> Any:
        """Processes a valid QR code by generating display string and dispatching via Redis."""
        qrCode = item.get(qrCodeKey)
        if not qrCode:
            return None
        self.currentQRCodeValue = qrCode
        self.createDisplayString(qrCode)
        try:
            if not self.redisConnectionQRCodeResponseConnected:
                self.connectToRedisForSendingQRCode(forceRenew=True)
            try:
                self.printValues()
                sendDataFromQRCodeServerToIOServer(redisConnection=self.redisConnectionQRCodeResponse, qrCode=self.currentQRCodeValue)
                sendDataFromQRCodeServerToFEServer(redisConnection=self.redisConnectionQRCodeResponse, qrCode=self.currentQRCodeValue, displayString=self.displayString)
                sendComponentQRCodeFromQRCodeServerToCameraServer(redisConnection=self.redisConnectionQRCodeResponse, componentQRCode=self.currentQRCodeValue)
                SlaveConsoleLogger.getInstance().logInfo(QRCodeDispatcher.logSource, f"Dispatched QRCode {qrCode}", Logger.SUCCESS)
                SlaveFileLogger.getInstance().logInfo(QRCodeDispatcher.logSource, f"Dispatched QRCode {qrCode}", Logger.SUCCESS)
            except Exception:
                SlaveConsoleLogger.getInstance().logTakeAction(QRCodeDispatcher.logSource, f"Could not dispatch QRCode {qrCode}", Logger.PROBLEM)
                SlaveFileLogger.getInstance().logTakeAction(QRCodeDispatcher.logSource, f"Could not dispatch QRCode {qrCode}", Logger.PROBLEM)
        except Exception as e:
            SlaveConsoleLogger.getInstance().logTakeAction(self.logSource, f'Failed to dispatch QRCode {qrCode}: {e}', Logger.PROBLEM)
            SlaveFileLogger.getInstance().logTakeAction(self.logSource, f'Failed to dispatch QRCode {qrCode}: {e}', Logger.PROBLEM)
        return None

class QRCodeScanningServer:
    """
    Manages the serial connection to the QR code scanner, monitors for aborts/stops/heartbeats,
    and coordinates with the GetAndValidateQRCode processor for aborts.
    """
    stateLock = Lock()
    heartbeatLock = Lock()
    abortLock = Lock()

    INVALID_STATE: str = "INVALID_STATE"
    # SLEEPTIME_BETWEEN_INVALID_QRCODE_READS = 0.05
    LOG_CONNECTION_PROBLEM_EVERY_N_SECS = CosThetaConfigurator.getInstance().getLogDisconnectionsAfterNSecs()

    logSource = getFullyQualifiedName(__file__)

    def __init__(self, mode: str):
        QRCodeScanningServer.logSource = getFullyQualifiedName(__file__, __class__)
        self.mode: str = mode
        setDatabaseName(self.mode, createDB=False)      # MainProgram creates the databases before it starts the servers
        self.name: str = "QRCodeServer"
        self.hostname: str = CosThetaConfigurator.getInstance().getRedisHost()
        self.port: int = CosThetaConfigurator.getInstance().getRedisPort()

        self.heartbeatRedisConnection: Union[Redis, None] = None
        self.heartbeatRedisConnected: bool = False
        self.abortReadRedisConnection: Union[Redis, None] = None
        self.abortReadRedisConnected: bool = False

        self.connectToRedisForTrackingAbort(True)
        self.connectToRedisForHeartbeat(True)

        self.qrCodePort: str = CosThetaConfigurator.getInstance().getQRCodePort()
        self.qrCodeScannerTimeout = 0.1
        self.qrCodeBaudRate: int = CosThetaConfigurator.getInstance().getQRCodeBaudRate()
        self.qrCodeSerialConnection: Union[Serial, None] = None

        self.stopped: bool = False
        self.connectionInProgress: bool = False
        self.connected: bool = False
        self.connectionAttempts: int = 0

        self.getAndValidateQRCodeObject: Union[GetAndValidateQRCode, None] = None  # Set after instantiation
        # the reading / dispatching threads, for the heartbeat (DEAD while one is not running or stuck: WorkerHealth)
        from utils.WorkerHealth import WorkerWatch
        self.workers = WorkerWatch("QR code server")

        self.connect()

        SlaveConsoleLogger.getInstance().logTakeNote(QRCodeScanningServer.logSource, 'Reached after attempting connection', Logger.SUCCESS)
        SlaveFileLogger.getInstance().logTakeNote(QRCodeScanningServer.logSource, 'Reached after attempting connection', Logger.SUCCESS)
        self.launchConnectionUpdateThread()
        self.launchMonitorStopCommandThread()
        self.launchHeartbeatCommunicationThread()
        self.launchMonitorAbortReadCommandThread()
        SlaveConsoleLogger.getInstance().logTakeNote(QRCodeScanningServer.logSource,
                                                     f'Reached end of initialization; connection status: {self.connected}',
                                                     Logger.SUCCESS)
        SlaveFileLogger.getInstance().logTakeNote(QRCodeScanningServer.logSource,
                                                  f'Reached end of initialization; connection status: {self.connected}',
                                                  Logger.SUCCESS)

    def connect(self):
        """Attempts to establish a serial connection to the QR code scanner."""
        if self.connectionInProgress:
            return
        self.connectionInProgress = True
        if self.connected and self.qrCodeSerialConnection:
            try:
                self.qrCodeSerialConnection.close()
            except Exception:
                noteSuppressed()
            self.qrCodeSerialConnection = None
        self.connectionAttempts += 1
        self.connected = False
        with self.stateLock:
            try:
                self.qrCodeSerialConnection = serial.Serial(port=self.qrCodePort, baudrate=self.qrCodeBaudRate,
                                                            timeout=self.qrCodeScannerTimeout, parity=serial.PARITY_NONE,
                                                            stopbits=serial.STOPBITS_ONE, bytesize=serial.EIGHTBITS)
                self.qrCodeSerialConnection.flush()
                self.connected = True
                SlaveConsoleLogger.getInstance().logTakeNote(QRCodeScanningServer.logSource, f'Connected at attempt {self.connectionAttempts}', Logger.SUCCESS)
                SlaveFileLogger.getInstance().logTakeNote(QRCodeScanningServer.logSource, f'Connected at attempt {self.connectionAttempts}', Logger.SUCCESS)
                self.connectionAttempts = 0
            except Exception as e:
                self.connected = False
                if self.connectionAttempts == 1 or (self.connectionAttempts % self.LOG_CONNECTION_PROBLEM_EVERY_N_SECS == 0):
                    SlaveConsoleLogger.getInstance().logTakeAction(QRCodeScanningServer.logSource, f'Failed to connect: {e}', Logger.PROBLEM)
                    SlaveFileLogger.getInstance().logTakeAction(QRCodeScanningServer.logSource, f'Failed to connect: {e}', Logger.PROBLEM)
        self.connectionInProgress = False

    def connectToRedisForTrackingAbort(self, forceRenew: bool = False):
        """Establishes Redis connection for tracking abort commands."""
        if forceRenew:
            self.abortReadRedisConnection = None
            self.abortReadRedisConnected = False
        if not self.abortReadRedisConnected:
            try:
                self.abortReadRedisConnection = Redis(self.hostname, self.port, retry_on_timeout=True)
                self.abortReadRedisConnected = True
                SlaveFileLogger.getInstance().logDebug(QRCodeScanningServer.logSource, 'Abort Redis Connection established', Logger.SUCCESS)
            except Exception as e:
                self.abortReadRedisConnected = False
                SlaveConsoleLogger.getInstance().logTakeAction(QRCodeScanningServer.logSource, f'Could not get Abort Redis Connection: {e}', Logger.PROBLEM)
                SlaveFileLogger.getInstance().logTakeAction(QRCodeScanningServer.logSource, f'Could not get Abort Redis Connection: {e}', Logger.PROBLEM)

    def connectToRedisForHeartbeat(self, forceRenew: bool = False):
        """Establishes Redis connection for heartbeats."""
        if forceRenew:
            self.heartbeatRedisConnection = None
            self.heartbeatRedisConnected = False
        if not self.heartbeatRedisConnected:
            try:
                self.heartbeatRedisConnection = Redis(self.hostname, self.port, retry_on_timeout=True)
                self.heartbeatRedisConnected = True
                SlaveConsoleLogger.getInstance().logDebug(QRCodeScanningServer.logSource, 'Heartbeat Redis Connection established', Logger.SUCCESS)
                SlaveFileLogger.getInstance().logDebug(QRCodeScanningServer.logSource, 'Heartbeat Redis Connection established', Logger.SUCCESS)
            except Exception as e:
                self.heartbeatRedisConnected = False
                SlaveConsoleLogger.getInstance().logTakeAction(QRCodeScanningServer.logSource, f'Could not get Heartbeat Redis Connection: {e}', Logger.PROBLEM)
                SlaveFileLogger.getInstance().logTakeAction(QRCodeScanningServer.logSource, f'Could not get Heartbeat Redis Connection: {e}', Logger.PROBLEM)

    def updateConnectionStatus(self):
        """Checks the current serial connection status."""
        if self.connectionInProgress or not self.qrCodeSerialConnection:
            return
        try:
            _ = self.qrCodeSerialConnection.in_waiting
            self.connected = True
        except Exception:
            self.connected = False

    def launchConnectionUpdateThread(self):
        objRef : QRCodeScanningServer = self
        """Launches a thread to periodically update and reconnect the serial connection."""
        def continuousConnectionUpdate():
            while not objRef.stopped:
                sleep(1.0)
                if not objRef.connectionInProgress:
                    objRef.updateConnectionStatus()
                    if not objRef.connected:
                        objRef.connect()
        self.connectionUpdateThread = Thread(name=f'QRCodeServer Connection Update Thread - {self.name}',
                                             target=continuousConnectionUpdate, daemon=True)
        self.connectionUpdateThread.start()

    def launchMonitorAbortReadCommandThread(self):
        objRef : QRCodeScanningServer = self
        """Launches a thread to monitor Redis for abort commands."""
        def lookForAbort():
            while not objRef.stopped:
                with objRef.abortLock:
                    try:
                        if objRef.abortReadRedisConnected:
                            # printBoldYellow(f"Checking for abort message in QRCodeServer")
                            abortNow = readAbortDataInQRCodeServerFromIOServer(objRef.abortReadRedisConnection)
                            if abortNow:
                                # printBoldYellow(f"Got {abortNow = }")
                                objRef.abortQRCodeRead()
                        else:
                            objRef.connectToRedisForTrackingAbort()
                    except Exception as e:
                        SlaveConsoleLogger.getInstance().logTakeAction(QRCodeScanningServer.logSource, f'Error monitoring abort: {e}', Logger.PROBLEM)
                if not objRef.stopped:
                    sleep(0.15)
        self.monitorAbortReadThread = Thread(name=f'Monitor Abort Read Thread - {self.name}',
                                             target=lookForAbort, daemon=True)
        self.monitorAbortReadThread.start()

    def launchMonitorStopCommandThread(self):
        objRef : QRCodeScanningServer = self
        """Launches a thread to monitor Redis for stop commands."""
        def lookForStop():
            while not objRef.stopped:
                with objRef.heartbeatLock:
                    try:
                        if objRef.heartbeatRedisConnected:
                            _, stopNow = getStopCommandFromQueue(objRef.heartbeatRedisConnection)
                            if stopNow:
                                objRef.stopped = True
                        else:
                            objRef.connectToRedisForHeartbeat()
                    except Exception as e:
                        SlaveConsoleLogger.getInstance().logTakeAction(QRCodeScanningServer.logSource, f'Error monitoring stop: {e}', Logger.PROBLEM)
                if not objRef.stopped:
                    sleep(5.0)
            if objRef.qrCodeSerialConnection:
                try:
                    objRef.qrCodeSerialConnection.close()
                except Exception:
                    noteSuppressed()
                objRef.qrCodeSerialConnection = None
        self.monitorStopThread = Thread(name=f'Monitor Stop Thread - {self.name}',
                                        target=lookForStop, daemon=True)
        self.monitorStopThread.start()

    def communicateHeartbeat(self):
        """Sends periodic heartbeats indicating scanner connection status."""
        sleepTime = CosThetaConfigurator.getInstance().getQRCodeConnectionStatusSleepInterval()
        while not self.stopped:
            with self.heartbeatLock:
                if self.heartbeatRedisConnected:
                    try:
                        working = self.connected and not self.workers.problemsLogged(
                            log=lambda m: SlaveConsoleLogger.getInstance().logTakeAction(QRCodeScanningServer.logSource, m, Logger.PROBLEM))
                        sendHeartbeatFromQRCodeServerToHeartbeatServer(redisConnection=self.heartbeatRedisConnection,
                                                                       status=ALIVE if working else DEAD)
                    except Exception as e:
                        SlaveConsoleLogger.getInstance().logTakeAction(QRCodeScanningServer.logSource, f'Heartbeat send failed: {e}', Logger.PROBLEM)
                        self.connectToRedisForHeartbeat(True)
                else:
                    self.connectToRedisForHeartbeat(True)
            sleep(sleepTime)

    def launchHeartbeatCommunicationThread(self):
        """Launches a thread for sending heartbeats."""
        self.heartbeatUpdateThread = Thread(name=f'QR Code Heartbeat Update Thread - {self.name}',
                                            target=self.communicateHeartbeat, daemon=True)
        self.heartbeatUpdateThread.start()

    def abortQRCodeRead(self):
        """Forwards the abort command to the GetAndValidateQRCode processor if active."""
        if self.getAndValidateQRCodeObject:
            self.getAndValidateQRCodeObject.abortQRCodeRead()

    def getQRCodeConnectionStatus(self) -> bool:
        return self.connected

    def getQRCodeScannerStatus(self) -> bool:
        return self.connected

    def getConnection(self) -> Union[Serial, None]:
        return self.qrCodeSerialConnection

    def shutdown(self):
        """Initiates shutdown by setting the stopped flag."""
        self.stopped = True
        SlaveConsoleLogger.getInstance().logInfo(QRCodeScanningServer.logSource, "Shutting down QRCodeScanningServer", Logger.SUCCESS)
        SlaveFileLogger.getInstance().logInfo(QRCodeScanningServer.logSource, "Shutting down QRCodeScanningServer", Logger.SUCCESS)

def startQRCodeServer(mode : str):
    server = QRCodeScanningServer(mode=mode)
    dispatcher = QRCodeDispatcher(name="QRCodeDispatcher", mode=mode)
    get_validate_qr_code = GetAndValidateQRCode(name="GetAndValidateQRCode", consumer=dispatcher, parent=server)
    monitor = MonitorGetQRCodeQueue(name="MonitorGetQRCodeQueue", consumer=get_validate_qr_code)

    # Link for abort handling
    server.getAndValidateQRCodeObject = get_validate_qr_code
    server.workers.add("scan requests (MonitorGetQRCodeQueue)", monitor)
    server.workers.add("reading the scanner (GetAndValidateQRCode)", get_validate_qr_code)
    server.workers.add("dispatching (QRCodeDispatcher)", dispatcher)

    dispatcher.start()
    get_validate_qr_code.start()
    monitor.start()

    printBoldBlue("********************")
    printBoldBlue("Started QRCodeServer")
    printBoldBlue("********************")

    monitor.join()
    get_validate_qr_code.join()
    dispatcher.join()

    sys.exit(0)

# if __name__ == "__main__":
#
#     testRedisConnection = Redis(CosThetaConfigurator.getInstance().getRedisHost(), CosThetaConfigurator.getInstance().getRedisPort(), retry_on_timeout=True)
#     testRedisConnection.flushdb()
#
#     serverThread = Thread(name=f'QR Code Server Thread',
#                           target=startQRCodeServer,
#                           args=("Test",),
#                           daemon=True)
#     serverThread.start()
#
#     # TESTING NORMAL FUNCTIONING
#     for i in range(6):
#         printBoldGreen("Sending read request")
#         sendDataFromIOServerToQRCodeServer(testRedisConnection)
#         for i in range(30):
#             print(i)
#             time.sleep(1)
#
#     sendStopCommand(testRedisConnection, aProducer="Tester")

    # def shutdown_handler(sig, frame):
    #     """Graceful shutdown handler for signals."""
    #     server.shutdown()
    #     # Assuming GenericQueueProcessor has a method to set stopped (or override run to check self.stopped)
    #     monitor.stopped = True
    #     get_validate.stopped = True
    #     dispatcher.stopped = True
    #     # Wait for threads to finish
    #     monitor.join(timeout=10)
    #     get_validate.join(timeout=10)
    #     dispatcher.join(timeout=10)
    #     sys.exit(0)
    #
    # signal.signal(signal.SIGINT, shutdown_handler)
    # signal.signal(signal.SIGTERM, shutdown_handler)

    # Keep main thread alive until stopped
    # while not server.stopped:
    #     sleep(1)