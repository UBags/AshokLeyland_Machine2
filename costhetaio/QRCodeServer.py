import re
import crc

from os import path
from os import environ
from uuid import uuid4
from time import sleep
from datetime import datetime
import sys
import time
import platform
import copy
from typing import *
from multiprocessing.pool import ThreadPool

import serial
from serial import *
import queue
import threading
from threading import Thread, Lock
import traceback

from utils.CosThetaPrintUtils import *
from utils.CosThetaFileUtils import *
from utils.QRCodeHelper import *
from persistence.Persistence import *

from logutils.SlaveLoggers import *
from logutils.Logger import *
from logutils.AbstractSlaveLogger import *
from utils.RedisUtils import *
import warnings
# warnings.filterwarnings('ignore', '.*h264.*', )

from Configuration import *
CosThetaConfigurator.getInstance()

class QRCodeServer():

    stateLock = threading.Lock()
    heartbeatLock = threading.Lock()
    readingLock = threading.Lock()
    abortLock = threading.Lock()

    # CONSTANTS
    INVALID_STATE : str = "INVALID_STATE"
    SLEEPTIME_BETWEEN_INVALID_QRCODE_READS = 0.05  # sleep time in milliseconds, when the current state of the application requires QRCode to be read
    LOG_CONNECTION_PROBLEM_EVERY_N_SECS = CosThetaConfigurator.getInstance().getLogDisconnectionsAfterNSecs()

    logSource = getFullyQualifiedName(__file__)

    def __init__(self, mode : str):
        try:
            QRCodeServer.logSource = getFullyQualifiedName(__file__, __class__)
            self.mode : str = mode
            setDatabaseName(self.mode)
            self.name : str = "QRCodeServer"
            self.hostname : str = CosThetaConfigurator.getInstance().getRedisHost()
            self.port : int = CosThetaConfigurator.getInstance().getRedisPort()
            # cString = r'\d{7}\$[A-Z]\d{1}[A-Z]\d{5}\$\d{4}\$\d{2}.\d{2}.\d{4}\$[A-Z]\d{5}\$[A-Z]{3}\$[A-Z]{2}\d{3}\s\d{1}.\d{1}\s[A-Z]'
            try:
                self.regexPatterns : list[str] = CosThetaConfigurator.getInstance().getQRCodeRegexPatterns()
            except Exception as e:
                printBoldRed(f"Got exception {e} while reading regexPatterns for QrCode")
            # self.extractionIndicesList : list = CosThetaConfigurator.getInstance().getQRCodeExtractionPatterns()
            printBoldBlue(f"Registered {len(self.regexPatterns)} patterns for recognition")
            # printBoldBlue(f"Got the following regex patterns:")
            # for p in self.regexPatterns:
            #     printBoldBlue(p)
            self.displayString : str = ""
            self.currentQRCodeValue : str = ""

            self.redisConnectionQRCodeRequest : Union[Redis,None] = None
            self.redisConnectionQRCodeRequestConnected : bool = False
            self.redisConnectionQRCodeResponse : Union[Redis,None] = None
            self.redisConnectionQRCodeResponseConnected : bool = False
            self.heartbeatRedisConnection : Union[Redis,None] = None
            self.heartbeatRedisConnected : bool = False
            self.abortReadRedisConnection : Union[Redis,None] = None
            self.abortReadRedisConnected : bool = False

            self.connectToRedisForQRCode(True)
            self.connectToRedisForHeartbeat(True)

            self.qrCodePort : str = CosThetaConfigurator.getInstance().getQRCodePort()
            self.qrCodeScannerTimeout = 0.05  # sets the read timeout. Else, read blocks.
            self.qrCodeBaudRate : int = CosThetaConfigurator.getInstance().getQRCodeBaudRate()
            self.qrCodeSerialConnection : Union[Serial,None] = None
            self.partMappings = CosThetaConfigurator.getQRCodePartMappingPatterns()
            # print(partMappings)

            self.stopped : bool = False
            self.abortRead : bool = False
            self.readInProgress : bool = False
            self.connectionInProgress : bool = False
            self.connected : bool = False
            self.connectionAttempts : int = 0

            self.connect()

            SlaveConsoleLogger.getInstance().logTakeNote(QRCodeServer.logSource,
                                                         f'Reached after attempting connection in QRCodeServer',
                                                         Logger.SUCCESS)
            SlaveFileLogger.getInstance().logTakeNote(QRCodeServer.logSource,
                                                      f'Reached after attempting connection in QRCodeServer',
                                                      Logger.SUCCESS)
            self.launchConnectionUpdateThread()
            self.launchMonitorStopCommandThread()
            self.launchHeartbeatCommunicationThread()
            self.launchQRCodeRequestMonitoringThread()
            self.launchMonitorAbortReadCommandThread()
            SlaveConsoleLogger.getInstance().logTakeNote(QRCodeServer.logSource,
                                                         f'Reached end of initialisation in QRCodeServer; connection status is {self.connected}',
                                                         Logger.SUCCESS)
            SlaveFileLogger.getInstance().logTakeNote(QRCodeServer.logSource,
                                                      f'Reached end of initialisation in QRCodeServer; connection status is {self.connected}',
                                                      Logger.SUCCESS)
            printBoldBlue("********************")
            printBoldBlue("Created QRCodeServer")
            printBoldBlue("********************")
        except Exception as e:
            # printBoldRed(f"Could not connect to QRCodeServer due to {e}")
            SlaveConsoleLogger.getInstance().logTakeAction(QRCodeServer.logSource,
                                                           f'Could not successfully do initialisation of QRCodeServer due to {e}',
                                                           Logger.PROBLEM)
            SlaveFileLogger.getInstance().logTakeAction(QRCodeServer.logSource,
                                                        f'Could not successfully do initialisation of QRCodeServer due to {e}',
                                                        Logger.PROBLEM)

    def connect(self):
        # print(f"Entered connect()")
        if self.connectionInProgress:
            return
        self.connectionInProgress = True
        if self.connected:
            if self.qrCodeSerialConnection is not None:
                try:
                    self.qrCodeSerialConnection.close()
                    self.qrCodeSerialConnection = None
                except:
                    self.qrCodeSerialConnection = None
        self.connectionAttempts += 1
        self.connected = False
        acquired = False
        try:
            acquired = QRCodeServer.stateLock.acquire()
            if acquired:
                # print(f"Attempting connection - attempt {self.connectionAttempts}")
                self.qrCodeSerialConnection = serial.Serial(port=self.qrCodePort, baudrate=self.qrCodeBaudRate,
                                                            timeout=self.qrCodeScannerTimeout,
                                                            parity=serial.PARITY_NONE,
                                                            stopbits=serial.STOPBITS_ONE,
                                                            bytesize=serial.EIGHTBITS)
                try:
                    self.qrCodeSerialConnection.flush()
                    self.connected = True
                    printBoldBlue(f"QRCodeServer has connected at attempt {self.connectionAttempts} !")
                except:
                    self.connected = False
                QRCodeServer.stateLock.release()
                acquired = False
                self.connectionInProgress = False
                if self.connected:
                    self.connectionAttempts = 0
                    SlaveConsoleLogger.getInstance().logTakeNote(QRCodeServer.logSource,
                                                                   f'QRCode Server - {self.name} is now connected',
                                                                   Logger.SUCCESS)
                    SlaveFileLogger.getInstance().logTakeNote(QRCodeServer.logSource,
                                                                f'QRCode Server - {self.name} is now connected',
                                                                Logger.SUCCESS)
                else:
                    if (self.connectionAttempts == 1) or ((self.connectionAttempts % QRCodeServer.LOG_CONNECTION_PROBLEM_EVERY_N_SECS) == 0):
                        printBoldRed(f"Could not connect at attempt {self.connectionAttempts}")
                        SlaveConsoleLogger.getInstance().logTakeAction(QRCodeServer.logSource,
                                                                       f'QRCode Server - {self.name} is NOT connected',
                                                                       Logger.PROBLEM)
                        SlaveFileLogger.getInstance().logTakeAction(QRCodeServer.logSource,
                                                                    f'QRCode Server - {self.name} is NOT connected',
                                                                    Logger.PROBLEM)
            if acquired:
                # printBoldBlue("Released stateLock in QRCodeServer in connect()")
                QRCodeServer.stateLock.release()
                acquired = False
        except Exception as e:
            self.connected = False
            if (self.connectionAttempts == 1) or (
                    (self.connectionAttempts % QRCodeServer.LOG_CONNECTION_PROBLEM_EVERY_N_SECS) == 0):
                SlaveConsoleLogger.getInstance().logTakeAction(QRCodeServer.logSource,
                                                      f'Could not connect() to QRCode Server in {self.name} due to {e}', Logger.PROBLEM)
                SlaveFileLogger.getInstance().logTakeAction(QRCodeServer.logSource,
                                                               f'Could not connect() to QRCode Server in {self.name} due to {e}',
                                                               Logger.PROBLEM)
        if acquired:
            # printBoldBlue("Released stateLock in QRCode Server in connect()")
            QRCodeServer.stateLock.release()

        # if not self.connected:
        #     if (self.connectionAttempts == 1) or (
        #             (self.connectionAttempts % QRCodeServer.LOG_CONNECTION_PROBLEM_EVERY_N_SECS) == 0):
        #         printBoldRed("QRCodeServer is NOT connected !")
        self.connectionInProgress = False

    def connectToRedisForQRCode(self, forceRenew = False):
        if forceRenew:
            self.redisConnectionQRCodeRequest = None
            self.redisConnectionQRCodeRequestConnected = False
            self.redisConnectionQRCodeResponse = None
            self.redisConnectionQRCodeResponseConnected = False
            self.abortReadRedisConnection = None
            self.abortReadRedisConnected = False
        if not self.redisConnectionQRCodeRequestConnected:
            try:
                self.redisConnectionQRCodeRequest = Redis(self.hostname, self.port, retry_on_timeout=True)
                self.redisConnectionQRCodeRequestConnected = True
                # printLight(f'Redis Connection is {self.redisConnectionQRCodeRequest}')
                # SlaveConsoleLogger.getInstance().logDebug(QRCodeServer.logSource,
                #                                           f'Redis Connection is {self.redisConnectionQRCodeRequestConnected}',
                #                                           Logger.SUCCESS)
                SlaveFileLogger.getInstance().logDebug(QRCodeServer.logSource,
                                                          f'Redis Connection is {self.redisConnectionQRCodeRequest}', Logger.SUCCESS)
            except:
                self.redisConnectionQRCodeRequestConnected = False
                # printBoldRed(f'Could not create Redis connection in {MasterFileLogger.logSource}')
                SlaveConsoleLogger.getInstance().logTakeAction(QRCodeServer.logSource,
                                                             f'Could not get Redis Connection', Logger.PROBLEM)
                SlaveFileLogger.getInstance().logTakeAction(QRCodeServer.logSource,
                                                             f'Could not get Redis Connection', Logger.PROBLEM)
                # pass
        if not self.redisConnectionQRCodeResponseConnected:
            try:
                self.redisConnectionQRCodeResponse = Redis(self.hostname, self.port, retry_on_timeout=True)
                self.redisConnectionQRCodeResponseConnected = True
                # printLight(f'Redis Connection is {self.redisConnectionQRCodeResponse}')
                # SlaveConsoleLogger.getInstance().logDebug(QRCodeServer.logSource,
                #                                           f'Redis Connection is {self.redisConnectionQRCodeResponse}',
                #                                           Logger.SUCCESS)
                SlaveFileLogger.getInstance().logDebug(QRCodeServer.logSource,
                                                       f'Redis Connection is {self.redisConnectionQRCodeResponse}',
                                                       Logger.SUCCESS)
                # sendDataFromQRCodeServerToIOServer(self.redisConnectionQRCodeResponse, qrCodeStatus = notok, handshake=True)
            except:
                self.redisConnectionQRCodeResponseConnected = False
                # printBoldRed(f'Could not create Redis connection in {MasterFileLogger.logSource}')
                SlaveConsoleLogger.getInstance().logTakeAction(QRCodeServer.logSource,
                                                               f'Could not get Redis Connection', Logger.PROBLEM)
                SlaveFileLogger.getInstance().logTakeAction(QRCodeServer.logSource,
                                                            f'Could not get Redis Connection', Logger.PROBLEM)
                # pass
        if not self.abortReadRedisConnected:
            try:
                self.abortReadRedisConnection = Redis(self.hostname, self.port, retry_on_timeout=True)
                self.abortReadRedisConnected = True
                SlaveFileLogger.getInstance().logDebug(QRCodeServer.logSource,
                                                       f'Redis Connection is {self.redisConnectionQRCodeResponse}',
                                                       Logger.SUCCESS)
                # sendDataFromQRCodeServerToIOServer(self.redisConnectionQRCodeResponse, qrCodeStatus = notok, handshake=True)
            except:
                self.abortReadRedisConnected = False
                SlaveConsoleLogger.getInstance().logTakeAction(QRCodeServer.logSource,
                                                               f'Could not get Redis Connection', Logger.PROBLEM)
                SlaveFileLogger.getInstance().logTakeAction(QRCodeServer.logSource,
                                                            f'Could not get Redis Connection', Logger.PROBLEM)
                # pass

    def connectToRedisForHeartbeat(self, forceRenew = False):
        if forceRenew:
            self.heartbeatRedisConnection = None
            self.heartbeatRedisConnected = False
        if not self.heartbeatRedisConnected:
            try:
                self.heartbeatRedisConnection = Redis(self.hostname, self.port, retry_on_timeout=True)
                self.heartbeatRedisConnected = True
                # printLight(f'Heartbeat Redis Connection is {self.heartbeatRedisConnection}')
                SlaveConsoleLogger.getInstance().logDebug(QRCodeServer.logSource,
                                                             f'Heartbeat Redis Connection is {self.heartbeatRedisConnection}', Logger.SUCCESS)
                SlaveFileLogger.getInstance().logDebug(QRCodeServer.logSource,
                                                          f'Heartbeat Redis Connection is {self.heartbeatRedisConnection}',
                                                          Logger.SUCCESS)
            except:
                self.heartbeatRedisConnected = False
                # printBoldRed(f'Could not create Heartbeat Redis connection in {IOServer.logSource}')
                SlaveConsoleLogger.getInstance().logTakeAction(QRCodeServer.logSource,
                                                             f'Could not get Heartbeat Redis Connection', Logger.PROBLEM)
                SlaveFileLogger.getInstance().logTakeAction(QRCodeServer.logSource,
                                                             f'Could not get Heartbeat Redis Connection', Logger.PROBLEM)


    def updateConnectionStatus(self):
        if self.connectionInProgress:
            return self.connected
        if self.qrCodeSerialConnection is not None:
            try:
                n = self.qrCodeSerialConnection.in_waiting
                self.connected = True
            except:
                self.connected = False
        else:
            self.connected = False

    # Monitors the connection
    def launchConnectionUpdateThread(self):
        objRef = self
        def continuousConnectionUpdate():
            while not objRef.stopped:
                # printBoldBlue("Updating QrCodeServer status")
                time.sleep(1.0)
                if not objRef.connectionInProgress:
                    objRef.updateConnectionStatus()
                    if not objRef.connected:
                        objRef.connect()

        self.connectionUpdateThread = threading.Thread(name=f'QRCodeServer Connection Update Thread - QRCodeServer.{self.name}',
                                                      target=continuousConnectionUpdate,
                                                      args=(), daemon = True)
        self.connectionUpdateThread.start()
        printLight("Started connection update thread in QRCodeServer!")

    # Monitors for Abort Read command from the IO Server
    def launchMonitorAbortReadCommandThread(self):
        objRef = self
        def lookForAbort():
            while not objRef.stopped:
                acquired = QRCodeServer.abortLock.acquire()
                if acquired:
                    try:
                        if objRef.abortReadRedisConnected:
                            abortNow = readAbortDataInQRCodeServerFromIOServer(objRef.abortReadRedisConnection)
                            if abortNow:
                                # printBoldRed(f"Got abort read command in QRCodeServer")
                                objRef.abortQRCodeRead()
                        else:
                            objRef.connectToRedisForQRCode()
                    except:
                        pass
                    QRCodeServer.abortLock.release()
                if not objRef.stopped:
                    time.sleep(0.15)
        self.monitorAbortReadThread = threading.Thread(name=f'Monitor Abort Read Thread in QRCodeServer - QrCodeServer.{self.name}',
                                                      target=lookForAbort,
                                                      args=(), daemon = True)
        self.monitorAbortReadThread.start()
        printLight("Started abort read monitoring thread in QRCodeServer!")

    # Monitors for Stop command from the front end
    def launchMonitorStopCommandThread(self):
        objRef = self
        def lookForStop():
            while not objRef.stopped:
                acquired = QRCodeServer.heartbeatLock.acquire()
                if acquired:
                    try:
                        if objRef.heartbeatRedisConnected:
                            _, stopNow = getStopCommandFromQueue(objRef.heartbeatRedisConnection)
                            if stopNow:
                                # printBoldRed(f"Got stop command in process {os.getpid()}")
                                objRef.stopped = True
                                sendStoppedResponse(objRef.heartbeatRedisConnection, aProducer=QRCodeServer.logSource)
                        else:
                            objRef.connectToRedisForHeartbeat()
                    except:
                        pass
                    QRCodeServer.heartbeatLock.release()
                if not objRef.stopped:
                    time.sleep(5.0)
            try:
                if self.qrCodeSerialConnection is not None:
                    try:
                        self.qrCodeSerialConnection.close()
                        self.qrCodeSerialConnection = None
                    except:
                        self.qrCodeSerialConnection = None
            except:
                pass

        self.monitorStopThread = threading.Thread(name=f'Monitor Stop Thread in QRCodeServer - QrCodeServer.{self.name}',
                                                      target=lookForStop,
                                                      args=(), daemon = True)
        self.monitorStopThread.start()
        printLight("Started stop monitoring thread in QRCodeServer!")

    def communicateHeartbeat(self):
        sleepTime = CosThetaConfigurator.getInstance().getQRCodeConnectionStatusSleepInterval()
        while not self.stopped:
            acquired = QRCodeServer.heartbeatLock.acquire()
            if acquired:
                if self.heartbeatRedisConnected:
                    try:
                        sendHeartbeatFromQRCodeServerToHeartbeatServer(redisConnection=self.heartbeatRedisConnection, status=ALIVE if self.connected else DEAD)
                        # if not self.connected:
                        #     # printBoldRed(f"Heartbeat communication - QRCode Module is not connected")
                        #     SlaveConsoleLogger.getInstance().logTakeAction(QRCodeServer.logSource,
                        #                                                    f"Heartbeat communication - QRCodeServer Module is not connected",
                        #                                                    Logger.PROBLEM)
                        #     SlaveFileLogger.getInstance().logTakeAction(QRCodeServer.logSource,
                        #                                               f"Heartbeat communication - QRCodeServer is not connected",
                        #                                               Logger.PROBLEM)
                    except Exception as e:
                        printBoldRed(f"Exception in communicateHeartBeat in QRCodeServer - {e}")
                        self.connectToRedisForHeartbeat(True)
                else:
                    self.connectToRedisForHeartbeat(True)
                QRCodeServer.heartbeatLock.release()
            time.sleep(sleepTime)

    def launchHeartbeatCommunicationThread(self):
        self.heartbeatUpdateThread = threading.Thread(
            name=f'QR Code Heartbeat Update Thread - QRCodeServer.{self.name}',
            target=self.communicateHeartbeat,
            args=(), daemon = True)
        self.heartbeatUpdateThread.start()
        printLight("Started heartbeat thread in QRCodeServer!")

    def monitorGetQRCodeQueue(self):
        sleepTime : float = CosThetaConfigurator.getInstance().getQRCodeRequestSleepInterval()
        printLight(f"Monitoring Read QR Code Queue every {sleepTime} secs")
        while not self.stopped:
            if self.redisConnectionQRCodeRequestConnected:
                try:
                    timeOfMessage, receivedRequest = readDataInQRCodeServerFromIOServer(redisConnection=self.redisConnectionQRCodeRequest)
                    if receivedRequest :
                        printBoldGreen("Received request to scan QR Code in QRCodeServer!")
                        # if self.readInProgress:
                        #     printBoldRed("Since readInProgress is True, setting abortRead to True")
                        self.abortQRCodeRead()
                        time.sleep(0.4)
                        # SlaveConsoleLogger.getInstance().logTakeAction(QRCodeServer.logSource,
                        #                                                f"QRCodeServer - Received request to get QR Code",
                        #                                                Logger.SUCCESS)
                        # SlaveFileLogger.getInstance().logTakeAction(QRCodeServer.logSource,
                        #                                             f"QRCodeServer - Received request to get QR Code",
                        #                                             Logger.SUCCESS)
                        self.resetQRCodeValues()
                        properQRCodeFound : bool = self.getProperQRCode(printQRCode=False)
                        if properQRCodeFound:
                            printBoldGreen(f"Found proper QR Code {self.currentQRCodeValue}")
                            sendDataFromQRCodeServerToIOServer(redisConnection=self.redisConnectionQRCodeResponse)
                            sendDataFromQRCodeServerToFEServer(redisConnection=self.redisConnectionQRCodeResponse, qrCode=self.currentQRCodeValue, displayString=self.displayString)
                            sendComponentQRCodeFromQRCodeServerToCameraServer(redisConnection=self.redisConnectionQRCodeResponse, componentQRCode=self.currentQRCodeValue)
                        # print(f"Reached here after sending results - \n {self.currentQRCodeValue = }\n {self.displayString = }")
                    else:
                        # printBoldRed("No request received for scanning QRCode")
                        pass
                except Exception as e:
                    printBoldRed(f"Exception in monitorGetQRCodeQueue() in QRCodeServer - {e}")
                    self.connectToRedisForQRCode(forceRenew=True)
            time.sleep(sleepTime)

    def launchQRCodeRequestMonitoringThread(self):
        self.qrCodeQueueMonitoringThread = threading.Thread(
            name=f'QR Code Queue Monitoring Update Thread - QRCodeServer.{self.name}',
            target=self.monitorGetQRCodeQueue,
            args=(), daemon = True)
        self.qrCodeQueueMonitoringThread.start()
        printLight("Started QRCode Request monitoring thread in QRCodeServer!")

    # DO NOT CALL THIS METHOD FROM AN EXTERNAL CLASS or APPLICATION
    def _readDataFromQRCodeScanner(self, printQRCode : bool = False) -> str | None:
        full_string = []
        if self.connected and (self.qrCodeSerialConnection is not None):
            try:
                full_string.clear()
                originalData : str = self.qrCodeSerialConnection.read(1).decode("utf-8")
                while len(originalData) > 0:
                    data = originalData
                    full_string.append(data)
                    # print(f'Current full_string = {full_string}')
                    originalData = self.qrCodeSerialConnection.read(1).decode("utf-8")

            except Exception as e:
                SlaveConsoleLogger.getInstance().logTakeAction(QRCodeServer.logSource,
                                                               f"Reading data from QR Code Scanner failed - {e}",
                                                               Logger.PROBLEM)
                SlaveFileLogger.getInstance().logTakeAction(QRCodeServer.logSource,
                                                            f"Reading data from QR Code Scanner failed - {e}",
                                                            Logger.PROBLEM)
                self.connected = False
        bytes_as_str : str = ""
        if len(full_string) > 0:
            bytes_as_str = ''.join(n for n in full_string)
        else:
            bytes_as_str = ""
        return bytes_as_str

    def getProperQRCode(self, printQRCode : bool = False) -> bool:
        printLight(f"Entered getProperQRCode()")
        properQRCodeFound : bool = False
        while (not properQRCodeFound) and (not self.stopped) and (not self.abortRead):
            self.readInProgress = True
            try:
                qrCode = self._readDataFromQRCodeScanner(printQRCode=printQRCode)
                if (qrCode is None) or (qrCode == ""):
                    pass
                else:
                    self.currentQRCodeValue = qrCode
                    if printQRCode and (self.currentQRCodeValue is not None) and (self.currentQRCodeValue != ""):
                        print(f"The current QR Code read is {self.currentQRCodeValue}")
                    # print(f"About to call evaluateQRCode()")
                    _, properQRCodeFound = self.evaluateQRCode(qrCode, printQRCode=printQRCode)
            except:
                pass
            try:
                time.sleep(QRCodeServer.SLEEPTIME_BETWEEN_INVALID_QRCODE_READS)
            except:
                pass
        if self.abortRead:
            printBoldRed(f"Aborted a read in QRCode Server")
        self.abortRead = False
        self.readInProgress = False
        return properQRCodeFound
        # printLight(f"Exiting getProperQRCode()")

    def evaluateQRCode(self, qrCode : str, printQRCode : bool = False) -> tuple[str | None, bool]:
        self.displayString = ""
        qrCodeIsValid : bool = False
        if (qrCode is not None) and (qrCode != ""):
            printBoldGreen(f"Evaluating {qrCode}")
            copyOfQRCode = copy.deepcopy(qrCode)
            currentIndex = -1
            results: Match[str] = None
            for index, regexPattern in enumerate(self.regexPatterns):
                # printBoldYellow(f"{index + 1}. Exploring match with pattern {regexPattern}")
                try:
                    results = re.match(regexPattern, copyOfQRCode)
                    # printBoldYellow(f"Result of match is {results}")
                except Exception as e:
                    printBoldRed(f"Encountered exception {e} during regex match")
                qrCodeIsValid = (results is not None)
                if qrCodeIsValid :
                    if printQRCode:
                        printBlue(f"Found match with {regexPattern}")
                    currentIndex = index
                    break
                else:
                    if printQRCode:
                        printRed(f"Found no match with {regexPattern}")
            if qrCodeIsValid:
                # extractionIndices = self.extractionIndicesList[currentIndex]
                # tempDisplayString = ""
                # for i in range(0, len(extractionIndices), 2):
                #     pair = extractionIndices[i:i + 2]
                #     tempDisplayString = tempDisplayString + copyOfQRCode[pair[0]:pair[1]]
                # tempDisplayString = (tempDisplayString + ".")[: -1]
                # self.displayString = tempDisplayString.replace('$', ' ')
                self.createDisplayString(qrCode=qrCode)
                if printQRCode:
                    print(f"DisplayString is {self.displayString}")
            else:
                if printQRCode:
                    printBoldRed("Invalid QR Code")
                SlaveConsoleLogger.getInstance().logInfo(QRCodeServer.logSource,
                                                               f"QRCodeServer - Received invalid QRCode {qrCode}",
                                                               Logger.SUCCESS)
                SlaveFileLogger.getInstance().logTakeAction(QRCodeServer.logSource,
                                                            f"QRCodeServer - Received invalid QRCode {qrCode}",
                                                            Logger.SUCCESS)
                self.displayString = ""
        return self.displayString, qrCodeIsValid

    # def checkOnlyValidityOfQRCodePattern(self, qrCode : str, printQRCode : bool = False) -> bool:
    #     if (qrCode is None) or (qrCode == ""):
    #         return False
    #     # printBoldGreen(f"Evaluating {qrCode}")
    #     copyOfQRCode = copy.deepcopy(qrCode)
    #     qrCodeIsValid : bool = False
    #     for regexPattern in self.regexPatterns:
    #         qrCodeMatches : Match[str] = re.search(regexPattern, copyOfQRCode)
    #         qrCodeIsValid = True if (qrCodeMatches is not None) else False
    #         if qrCodeIsValid:
    #             break
    #     if qrCodeIsValid and printQRCode:
    #         print(f"{qrCodeIsValid = }")
    #     return qrCodeIsValid

    # def createDisplayString(self, qrCode : str, printQRCode : bool = False) -> str | None:
    #     if (qrCode is None) or (qrCode == ""):
    #         self.displayString = ""
    #     else:
    #         copyOfQRCode = copy.deepcopy(qrCode)
    #         copyOfQRCode = (copyOfQRCode[:] + '.')[:-1]
    #         self.displayString = (copyOfQRCode[:32] + " " + copyOfQRCode[50:53] + copyOfQRCode[54:55] + ".")[:-1]
    #         self.displayString = self.displayString.replace("$", " ")
    #     return self.displayString

    def createDisplayString(self, qrCode : str, printQRCode : bool = False) -> str | None:
        if (qrCode is None) or (qrCode == ""):
            self.displayString = ""
        else:
            duplicateExists = checkIfQRCodeExists(qr_code=qrCode, db_name=getDatabaseName())
            copyOfQRCode = copy.deepcopy(qrCode)
            tempDisplayValue : str = ""
            remainingQrCode : str = copy.deepcopy(qrCode)
            for key, value in partMappings.items():
                if copyOfQRCode.startswith(key):
                    tempDisplayValue = f"{value}"
                    remainingQrCode = copyOfQRCode.removeprefix(f"{key}$")
                    break
            remainingParts = remainingQrCode.split("$")
            suffix = f" : {remainingParts[0]} : {remainingParts[1]}"
            tempDisplayValue = f"{tempDisplayValue}{suffix}"
            self.displayString = f"{tempDisplayValue}"
            if duplicateExists:
                self.displayString = f"{self.displayString} - DUPLICATE"
        return self.displayString

    def resetQRCodeValues(self):
        self.displayString = ""
        self.currentQRCodeValue = ""
        try:
            self.qrCodeSerialConnection.reset_input_buffer()
        except:
            pass

    def abortQRCodeRead(self):
        printBoldRed("abortQRCodeRead() called")
        if self.readInProgress:
            self.abortRead = True
            self.resetQRCodeValues()

    def getCurrentQRCode(self):
        return self.currentQRCodeValue

    def getCurrentQRCodeDisplayString(self):
        return self.displayString

    def getQRCodeConnectionStatus(self):
        return self.connected

    def getQRCodeScannerStatus(self):
        return self.connected

    def getConnection(self):
        return self.qrCodeSerialConnection

    def shutdown(self):
        self.stopped = True

    def crc16(self, data: bytes) -> int:
        crc = 0xFFFF  # Initial value for CRC-16
        for byte in data:
            crc ^= byte  # XOR byte into least significant byte of crc
            for _ in range(8):  # Process each bit
                if (crc & 0x0001):  # Check if the last bit is set
                    crc = (crc >> 1) ^ 0xA001  # Polynomial 0xA001
                else:
                    crc >>= 1
        return crc

    def crc8(self, data: bytes) -> int:
        crc = 0x00  # Initial value for CRC-8
        for byte in data:
            crc ^= byte  # XOR byte into CRC
            for _ in range(8):
                if (crc & 0x80):  # Check for the most significant bit
                    crc = (crc << 1) ^ 0x31  # Polynomial 0x31
                else:
                    crc <<= 1
                crc &= 0xFF  # Keep it to 8 bits
        return crc

    def addHexStrings(self, hex_str1: str, hex_str2: str) -> str:
        # Ensure the strings are stripped of leading/trailing whitespace
        hex_str1 = hex_str1[-4:].strip()
        hex_str2 = hex_str2[-4:].strip()

        # Convert hexadecimal strings to integers
        int1 = int(hex_str1, 16)
        int2 = int(hex_str2, 16)

        # Add the integers
        sum_int = int1 + int2

        # Convert the sum back to a hexadecimal string
        # We use upper() to convert it to uppercase and [2:] to remove '0x'
        result_hex = hex(sum_int).upper()[2:]  # Remove '0x' prefix

        return result_hex

    def sysconCheck(self):

        # Added to check connection with Syscon
        # requestSequence : str = "01030BB80002460A"
        # requestSequence: str = "01060BBA0000CFDC"
        # requestAsBytes = bytes.fromhex(requestSequence)
        # qrCodeSerialConnection.write(requestAsBytes)
        # printBoldGreen(f"After writing {requestSequence}")
        #     Adding code for Syscon sensor
        # while True:
        #     # Read data from the serial port (specify how many bytes you want to read)
        #     if qrCodeSerialConnection.in_waiting > 0:  # Check if data is waiting in the buffer
        #         data = qrCodeSerialConnection.read(qrCodeSerialConnection.in_waiting)  # Read all available data
        #         hex_data = data.hex()  # Convert bytes to hex string
        #         print("Received HEX:", hex_data)
        #         crc_value = crc16(data)
        #         print(f'CRC-16 Value: {crc_value:04X}')
        #         addedCRC = addHexStrings(hex_data, hex(crc_value))
        #         print(f"The final CRC value is {addedCRC}")
        #         # crc_value = crc8(data)
        #         # print(f'CRC-8 Value: {crc_value:02X}')
        #         break
        # time.sleep(50)
        pass

    def printValues(self):
        print(f'The current QR Code string is {self.currentQRCodeValue}')
        print(f'The display string is {self.displayString}')

# *****************************************

def startQRCodeServer(mode : str):
    qrCodeServer = QRCodeServer(mode = mode)

# *****************************************

# if __name__ == "__main__":
#
#     testRedisConnection = Redis(CosThetaConfigurator.getInstance().getRedisHost(), CosThetaConfigurator.getInstance().getRedisPort(), retry_on_timeout=True)
#     testRedisConnection.flushdb()
#
#     qrCodeServer = QRCodeServer("Test")
#
#     printBoldYellow("Starting sleep")
#     time.sleep(5)
#     printBoldYellow("Ending sleep")
#
#     # TESTING NORMAL FUNCTIONING
#     for i in range(6):
#         qrCodeServer.resetQRCodeValues()
#         printBoldGreen("Sending read request")
#         sendDataFromIOServerToQRCodeServer(testRedisConnection)
#         printBoldYellow("Starting sleep")
#         time.sleep(10)
#         printBoldYellow("Ending sleep")
#         qrCodeServer.printValues()
#     sendStopCommand(testRedisConnection, aProducer="Tester")

    # TESTING RECONNECTION

    # qrCodeServer = QRCodeServer()
    # printBoldBlue("Sending read request")
    # sendDataFromIOServerToQRCodeServer(testRedisConnection, aProducer="Tester")
    # time.sleep(10)
    # qrCodeServer.printValues()
    # # Disconnect it, then connect back after 15-20 secs
    # time.sleep(45)
    # printBoldBlue("Sending read request")
    # sendDataFromIOServerToQRCodeServer(testRedisConnection, aProducer="Tester")
    # time.sleep(10)
    # qrCodeServer.printValues()
    # printBoldBlue("Sending read request")
    # sendDataFromIOServerToQRCodeServer(testRedisConnection, aProducer="Tester")
    # time.sleep(5)
    # qrCodeServer.printValues()
    # sendStopCommand(testRedisConnection, aProducer="Tester")
