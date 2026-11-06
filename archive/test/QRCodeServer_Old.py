import copy
import re
import os.path

from os import environ
from uuid import uuid4
from time import sleep
from datetime import datetime
import sys
import time
import platform
import serial
from PySide6.QtCore import QThread, QThreadPool
from serial import *
import queue
import threading
import time
import logging
import traceback
from Configuration import *

from utils.CosThetaPrintUtils import printBoldBlue, printBoldRed, printBoldGreen

qrCodePort : str = None
qrCodeBaudRate : int = 19200
qrCodeRegexPattern : str = None
numberOfCharactersToCheckInQRCode : int = 0

qrCodeSerialConnection : Serial = None
qrCodeScannerConnected : bool = False
qrCodeScannerTimeout : float = 0.05  # sets the read timeout. Else, read blocks.

regexPattern = None

qrCodeRLock = threading.RLock()
qrCodeRLockTimeout : int = 1  # milliseconds
retryCount : int = 0
criticalLoggingCount : int = 0

sendQRCode : bool = False
currentQRCodeValue : str = ''
currentDisplayString : str = ''

logger = None
shutdownThreads = False

def connectToQRCodeScanner():
    global qrCodePort, qrCodeBaudRate, qrCodeRegexPattern, numberOfCharactersToCheckInQRCode, qrCodeSerialConnection, qrCodeScannerConnected, qrCodeScannerTimeout, qrCodeRLock, qrCodeRLockTimeout
    global sendQRCode, currentQRCodeValue, currentDisplayString, retryCount, criticalLoggingCount, shutdownThreads, logger, regexPattern
    rLockAcquired = False
    try:
        if qrCodePort is None:
            qrCodePort = CosThetaConfigurator.getInstance().getQRCodePort()
            qrCodeBaudRate = CosThetaConfigurator.getInstance().getQRCodeBaudRate()
            qrCodeRegexPattern = CosThetaConfigurator.getInstance().getQRCodeRegexPatterns()
            numberOfCharactersToCheckInQRCode = CosThetaConfigurator.getInstance().getNumberOfCharactersToCheckInQRCode()
            print(f"regexPattern is {qrCodeRegexPattern}, numberOfChars is {numberOfCharactersToCheckInQRCode}")
            tempPattern = re.escape(qrCodeRegexPattern)
            regexPattern = re.compile(tempPattern)
        if (retryCount % 400 == 0):
            printBoldRed("Trying to connect back to QR Code Scanner")
            pass
        retryCount += 1
        if retryCount > 1000:
            retryCount = 1
        rLockAcquired = qrCodeRLock.acquire(timeout=qrCodeRLockTimeout)
        if rLockAcquired:
            # This is checked again because the renew connection thread may have, in the meanwhile, reconnected
            if not qrCodeScannerConnected:
                if qrCodeSerialConnection is not None:
                    qrCodeSerialConnection.close()
                qrCodeSerialConnection = serial.Serial(port=qrCodePort, baudrate=qrCodeBaudRate,
                                                       timeout=qrCodeScannerTimeout,
                                                       parity=serial.PARITY_NONE,
                                                       stopbits=serial.STOPBITS_ONE,
                                                       bytesize=serial.EIGHTBITS)
                qrCodeScannerConnected = True
                printBoldBlue(f'In QRCodeServer, connection is {qrCodeSerialConnection}')
                printBoldBlue(f'In QRCodeServer, connection status is {qrCodeScannerConnected}')
                criticalLoggingCount = 0
    except Exception as e:
        qrCodeScannerConnected = False
        try:
            if qrCodeSerialConnection is not None:
                qrCodeSerialConnection.close()
        except:
            pass
        if criticalLoggingCount < 4:
            printBoldRed(f'In QRCodeServer: Unable to connect to QR Code Scanner')
            criticalLoggingCount += 1
            if criticalLoggingCount > 1000:
                criticalLoggingCount = 0
    finally:
        if rLockAcquired:
            try:
                qrCodeRLock.release()
            except:
                pass

def isQRCodeValid(qrCode : str):
    global qrCodePort, qrCodeSerialConnection, qrCodeRegexPattern, numberOfCharactersToCheckInQRCode, qrCodeScannerConnected, qrCodeScannerTimeout, qrCodeRLock, qrCodeRLockTimeout
    global sendQRCode, currentQRCodeValue, currentDisplayString, retryCount, criticalLoggingCount, shutdownThreads, logger, regexPattern
    if qrCode is not None and qrCode != "":
        printBoldGreen(f"Evaluating {qrCode} in isQRCodeValid()")
    else:
        printBoldRed(f"{qrCode = }")
    if (qrCode is None) or (qrCode == ""):
        return False
    copyOfQRCode = copy.deepcopy(qrCode)
    if regexPattern == None:
        tempPattern = re.escape(qrCodeRegexPattern)
        regexPattern = re.compile(tempPattern)
    first54Chars = copyOfQRCode[:numberOfCharactersToCheckInQRCode]
    matches = regexPattern.match(first54Chars)
    qrCodeIsValid = False
    if matches:
        qrCodeIsValid = True
    return qrCodeIsValid

def evaluateQRCode(qrCode):
    global qrCodePort, qrCodeSerialConnection, qrCodeRegexPattern, numberOfCharactersToCheckInQRCode, qrCodeScannerConnected, qrCodeScannerTimeout, qrCodeRLock, qrCodeRLockTimeout
    global sendQRCode, currentQRCodeValue, currentDisplayString, retryCount, criticalLoggingCount, shutdownThreads, logger, regexPattern
    displayString : str = ""
    if (qrCode is None) or (qrCode == ""):
        displayString = ""
    else:
        if (qrCode is not None) and (qrCode != ""):
            printBoldGreen(f"Evaluating {qrCode} in evaluateQRCode()")
        copyOfQRCode = (qrCode[:] + '.')[:-1]
        if regexPattern == None:
            tempPattern = re.escape(qrCodeRegexPattern)
            regexPattern = re.compile(tempPattern)
        first54Chars = (copyOfQRCode[:numberOfCharactersToCheckInQRCode] + '.')[:-1]
        results = regexPattern.match(first54Chars)
        # qrCodeIsValid = False
        if results:
            # qrCodeIsValid = True
            displayString = (copyOfQRCode[:31] + " " + copyOfQRCode[49:52] + copyOfQRCode[53:54] + ".")[:-1]
            # print(f"DisplayString is {displayString}")
            displayString = displayString.replace('$', ' ')
        else:
            displayString = ""
    currentDisplayString = displayString

def createDisplayString(qrCode):
    global qrCodePort, qrCodeSerialConnection, qrCodeScannerConnected, qrCodeScannerTimeout, qrCodeRLock, qrCodeRLockTimeout
    global sendQRCode, currentQRCodeValue, currentDisplayString, retryCount, criticalLoggingCount, shutdownThreads, logger, regexPattern
    if (qrCode is None) or (qrCode == ""):
        currentDisplayString = ""
    else:
        print(f"In createDisplayString({qrCode}")
        copyOfQRCode = (qrCode[:] + '.')[:-1]
        displayString = (copyOfQRCode[:31] + " " + copyOfQRCode[49:52] + copyOfQRCode[53:54] + ".")[:-1]
        currentDisplayString = displayString.replace("$", " ")
    return

class QRCodeServer(object):
    logger = None

    # ****************************************************

    def _populateLogger(self, currentClass):
        global qrCodePort, qrCodeSerialConnection, qrCodeScannerConnected, qrCodeScannerTimeout, qrCodeRLock, qrCodeRLockTimeout
        global sendQRCode, currentQRCodeValue, currentDisplayString, retryCount, criticalLoggingCount, shutdownThreads, logger
        # if QRCodeServer.logger is None:
        #     QRCodeServer.logger = logging.getLogger(getFullyQualifiedName(__file__, currentClass))

    def __init__(self, parent = None):
        global qrCodePort, qrCodeSerialConnection, qrCodeScannerConnected, qrCodeScannerTimeout, qrCodeRLock, qrCodeRLockTimeout
        global sendQRCode, currentQRCodeValue, currentDisplayString, retryCount, criticalLoggingCount, shutdownThreads, logger

        super(QRCodeServer, self).__init__()
        QRCodeServer._populateLogger(self, __class__)
        # CosThetaConfigurator.getInstance()  # needs to be called to ensure that configurations are properly loaded and logging is also initialised
        self.valid = False
        self.threadPool = QThreadPool()
        connectToQRCodeScanner()
        try:
            # QtTest.QTest.qWait(3000)
            time.sleep(3)
        except:
            pass
        self.keepConnectionAlive = KeepConnectionAlive()
        self.keepConnectionAlive.start()

        printBoldBlue("*****************************************")
        printBoldBlue("Created QRCodeServer")
        printBoldBlue("*****************************************")

        # The way to get QRCode is:
            # qrCodeServer = QRCodeServer()
            # qrCodeServer.getQRCode()
            # print(f'QRCode is {qrCodeServer.getCurrentQRCode()}')
            # print(f'Display String is {qrCodeServer.getCurrentQRCodeDisplayString()}')

    def shutdown(self):
        global qrCodePort, qrCodeSerialConnection, qrCodeScannerConnected, qrCodeScannerTimeout, qrCodeRLock, qrCodeRLockTimeout
        global sendQRCode, currentQRCodeValue, currentDisplayString, retryCount, criticalLoggingCount, shutdownThreads, logger

        sendQRCode = False
        try:
            # QtTest.QTest.qWait(3000)
            time.sleep(3)
        except:
            pass
        shutdownThreads = True
        try:
            # QtTest.QTest.qWait(10000)
            time.sleep(10)
        except:
            pass

    # ********************************************************************

    # DO NOT CALL THIS METHOD FROM AN EXTERNAL CLASS or APPLICATION
    couldntConnect = 0

    def _getDataFromQRCodeScanner(self):
        # print("Entered _getDataFromQRCodeScanner()")
        global qrCodePort, qrCodeSerialConnection, qrCodeScannerConnected, qrCodeScannerTimeout, qrCodeRLock, qrCodeRLockTimeout
        global sendQRCode, currentQRCodeValue, currentDisplayString, retryCount, criticalLoggingCount, shutdownThreads, logger

        full_string = []
        rLockAcquired = False
        # self.resetQRCodeValues()
        # print(f'In _getDataFromQRCodeScanner()')

        try:
            rLockAcquired = qrCodeRLock.acquire(qrCodeRLockTimeout)
            if rLockAcquired:
                if qrCodeScannerConnected and (qrCodeSerialConnection is not None):
                    try:
                        full_string.clear()
                        originalData = qrCodeSerialConnection.read(1).decode("utf-8")
                        while len(originalData) > 0:
                            data = originalData
                            full_string.append(data)
                            # logger.debug(f'Current full_string = {full_string}')
                            print(f'Current full_string = {full_string}')
                            originalData = qrCodeSerialConnection.read(1).decode("utf-8")
                            QRCodeServer.couldntConnect = 0
                    except:
                        qrCodeScannerConnected = False
                        if qrCodeSerialConnection is not None:
                            qrCodeSerialConnection.close()
        except Exception as e:
            if QRCodeServer.couldntConnect < 3:
                QRCodeServer.couldntConnect += 1
                # logger.critical(f'Fail no {QRCodeServer.couldntConnect} : Error {e} while getting QR Code')
                print(f'Fail no {QRCodeServer.couldntConnect} : Error {e} while getting QR Code')
                if QRCodeServer.couldntConnect > 1000:
                    QRCodeServer.couldntConnect = 0
        finally:
            if rLockAcquired:
                try:
                    qrCodeRLock.release()
                except:
                    pass
        if len(full_string) > 0:
            byte_str = ''.join(n for n in full_string)
        else:
            byte_str = ""
        currentQRCodeValue = byte_str
        if (currentQRCodeValue is not None) and (currentQRCodeValue != ""):
            print(f"Current QR Code Value = {currentQRCodeValue}")
        return currentQRCodeValue

    # DO NOT CALL THIS METHOD FROM AN EXTERNAL CLASS or APPLICATION
    STATE_SLEEP_TIME = 0.05  # sleep time in milliseconds, when the current state of the application requires QRCode to be read

    # def _populateQRCodeValues(self):
    #     global qrCodePort, qrCodeSerialConnection, qrCodeScannerConnected, qrCodeScannerTimeout, qrCodeRLock, qrCodeRLockTimeout
    #     global sendQRCode, currentQRCodeValue, currentDisplayString, retryCount, criticalLoggingCount, shutdownThreads, logger
    #
    #     print(f'In _populateQRCodeValues()')
    #
    #     while sendQRCode:
    #         try:
    #             if not qrCodeScannerConnected:
    #                 connectToQRCodeScanner()
    #             rawData = self._getDataFromQRCodeScanner()
    #             if rawData is None:
    #                 pass
    #             else:
    #                 print(f'Raw QR Code received is {rawData} in _populateQRCodeValues()')
    #                 rawDataCopy = (rawData + '.')[:-1]
    #                 print(rawDataCopy)
    #                 self.evaluateQRCode(rawDataCopy)
    #                 printBoldGreen(f"{sendQRCode}, {currentQRCodeValue}, {currentDisplayString}")
    #         except:
    #             pass
    #         try:
    #             # QtTest.QTest.qWait(QRCodeServer.STATE_SLEEP_TIME)
    #             time.sleep(QRCodeServer.STATE_SLEEP_TIME)
    #         except:
    #             pass

    def resetQRCodeValues(self):
        global qrCodePort, qrCodeSerialConnection, qrCodeScannerConnected, qrCodeScannerTimeout, qrCodeRLock, qrCodeRLockTimeout
        global sendQRCode, currentQRCodeValue, currentDisplayString, retryCount, criticalLoggingCount, shutdownThreads, logger

        self.valid = False
        currentQRCodeValue = ""
        currentDisplayString = ""

    def abortQRCodeRead(self):
        global qrCodePort, qrCodeSerialConnection, qrCodeScannerConnected, qrCodeScannerTimeout, qrCodeRLock, qrCodeRLockTimeout
        global sendQRCode, currentQRCodeValue, currentDisplayString, retryCount, criticalLoggingCount, shutdownThreads, logger

        sendQRCode = False
        self.resetQRCodeValues()
        try:
            qrCodeSerialConnection.reset_input_buffer()
        except:
            pass

    def setSendQRCode(self):
        global qrCodePort, qrCodeSerialConnection, qrCodeScannerConnected, qrCodeScannerTimeout, qrCodeRLock, qrCodeRLockTimeout
        global sendQRCode, currentQRCodeValue, currentDisplayString, retryCount, criticalLoggingCount, shutdownThreads, logger

        self.resetQRCodeValues()
        try:
            qrCodeSerialConnection.reset_input_buffer()
        except:
            pass
        sendQRCode = True

    def getCurrentQRCode(self):
        global qrCodePort, qrCodeSerialConnection, qrCodeScannerConnected, qrCodeScannerTimeout, qrCodeRLock, qrCodeRLockTimeout
        global sendQRCode, currentQRCodeValue, currentDisplayString, retryCount, criticalLoggingCount, shutdownThreads, logger

        return currentQRCodeValue

    def getCurrentQRCodeDisplayString(self):
        global qrCodePort, qrCodeSerialConnection, qrCodeScannerConnected, qrCodeScannerTimeout, qrCodeRLock, qrCodeRLockTimeout
        global sendQRCode, currentQRCodeValue, currentDisplayString, retryCount, criticalLoggingCount, shutdownThreads, logger

        return currentDisplayString

    def setLogLevel(self, logLevel):
        # logger.setLevel(logLevel)
        pass

    def getQRCodeConnectionStatus(self):
        global qrCodePort, qrCodeSerialConnection, qrCodeScannerConnected, qrCodeScannerTimeout, qrCodeRLock, qrCodeRLockTimeout
        global sendQRCode, currentQRCodeValue, currentDisplayString, retryCount, criticalLoggingCount, shutdownThreads, logger

        return qrCodeScannerConnected

    def getQRCodeScannerStatus(self):
        global qrCodePort, qrCodeSerialConnection, qrCodeScannerConnected, qrCodeScannerTimeout, qrCodeRLock, qrCodeRLockTimeout
        global sendQRCode, currentQRCodeValue, currentDisplayString, retryCount, criticalLoggingCount, shutdownThreads, logger

        return qrCodeScannerConnected

    def getConnection(self):
        global qrCodePort, qrCodeSerialConnection, qrCodeScannerConnected, qrCodeScannerTimeout, qrCodeRLock, qrCodeRLockTimeout
        global sendQRCode, currentQRCodeValue, currentDisplayString, retryCount, criticalLoggingCount, shutdownThreads, logger

        return qrCodeSerialConnection


    # This is the method that should be called by external applications
    # External applications need to first connect a receving method to the Signal gotQRCode,
    # and then call getQRCode()
    '''
    def getQRCode(self):
        global qrCodePort, qrCodeSerialConnection, qrCodeScannerConnected, qrCodeScannerTimeout, qrCodeMutex, qrCodeMutexTimeout
        global sendQRCode, currentQRCodeValue, currentDisplayString, retryCount, criticalLoggingCount, shutdownThreads, logger

        print("Reached - 1")
        self.resetQRCodeValues()
        # print("Reached - 2")
        qrCodeSerialConnection.reset_input_buffer()
        # print("Reached - 3")
        sendQRCode = True
        # print("Reached - 4")
        rwAttribute = hasattr(self, 'readingWorker')
        if not rwAttribute:
            self.readingWorker = ReadingWorker(self)
        self.readingThread = QThread()
        # print("Reached - 5")
        self.readingThread.started.connect(self.readingWorker.read)
        print("Reached - 6")
        self.readingThread.finished.connect(self.readingThread.deleteLater)
        # print("Reached - 7")
        self.readingWorker.moveToThread(self.readingThread)
        # print("Reached - 8")
        self.readingThread.start()
        print("Reached - 9")
        self.readingThread.wait()
        print("Reached - 10")
    '''

    def getQRCode(self):
        global qrCodePort, qrCodeSerialConnection, qrCodeScannerConnected, qrCodeScannerTimeout, qrCodeRLock, qrCodeRLockTimeout
        global sendQRCode, currentQRCodeValue, currentDisplayString, retryCount, criticalLoggingCount, shutdownThreads, logger

        self.resetQRCodeValues()
        # while not qrCodeScannerConnected:
        #     try:
        #         time.sleep(0.5)
        #     except:
        #         pass
        if qrCodeScannerConnected and (qrCodeSerialConnection is not None):
            qrCodeSerialConnection.reset_input_buffer()
        sendQRCode = True
        self.readingWorkerThread = ReadingWorkerThread(self, regexPattern, numberOfCharactersToCheckInQRCode)
        self.readingWorkerThread.start()
        # self.readingWorkerThread.join()
        self.readingWorkerThread.wait()
        print("currentQRCodeValue : ", currentQRCodeValue)
        print("currentDisplayString : ", currentDisplayString)
        createDisplayString(currentQRCodeValue)
        # print("Printing : ", currentQRCodeValue)
        # print("Printing : ", currentDisplayString)

    def printValues(self):
        print(f'The QR Code is {currentQRCodeValue}')
        print(f'The display string is {currentDisplayString}')

# ***************************************

class KeepConnectionAlive(QThread):
    SLEEP_TIME = 5

    def __init__(self):
        super(KeepConnectionAlive, self).__init__()

    def run(self):
        self.renewConnection()

    def renewConnection(self):
        global qrCodePort, qrCodeSerialConnection, qrCodeScannerConnected, qrCodeScannerTimeout, qrCodeRLock, qrCodeRLockTimeout
        global sendQRCode, currentQRCodeValue, currentDisplayString, retryCount, criticalLoggingCount, shutdownThreads, logger

        counter = 0
        connected = qrCodeScannerConnected
        printBoldGreen(f"Checking connection")
        while not shutdownThreads:
            rLockAcquired = False
            counter += 1
            if counter > 500:
                counter = 1
            try:
                if qrCodeScannerConnected and (qrCodeSerialConnection is not None):
                    rLockAcquired = qrCodeRLock.acquire(qrCodeRLockTimeout)
                    if rLockAcquired:
                        try:
                            n = qrCodeSerialConnection.in_waiting
                        except:
                            connected = False
                    printBoldGreen("Connection is alive")
            except Exception as e:
                # qrCodeScannerConnected = False
                # if counter < 4:
                #     traceback.print_stack()
                # print(e)
                pass
            finally:
                if rLockAcquired:
                    try:
                        qrCodeRLock.release()
                    except:
                        pass

            if (not qrCodeScannerConnected) or (qrCodeSerialConnection is None) or (not connected):
                connectToQRCodeScanner()

            try:
                # QtTest.QTest.qWait(KeepConnectionAlive.SLEEP_TIME)
                time.sleep(KeepConnectionAlive.SLEEP_TIME)
            except:
                None


# *****************************************

# class ReadingWorker(QObject):
#
#     STATE_SLEEP_TIME = 0.05
#     def __init__(self, aParent: QRCodeServer):
#         super(ReadingWorker, self).__init__()
#         self.aParent = aParent
#
#     def read(self):
#         global qrCodePort, qrCodeSerialConnection, qrCodeScannerConnected, qrCodeScannerTimeout, qrCodeMutex, qrCodeMutexTimeout
#         global sendQRCode, currentQRCodeValue, currentDisplayString, retryCount, criticalLoggingCount, shutdownThreads, logger
#
#         while self.aParent.sendQRCode:
#             self.aParent._getDataFromQRCodeScanner()
#             try:
#                 time.sleep(ReadingWorker.STATE_SLEEP_TIME)
#             except:
#                 pass
#         # print("Finished reading")
#         return

# *****************************************

class ReadingWorkerThread(QThread):

    STATE_SLEEP_TIME = 0.05

    def __init__(self, aParent: QRCodeServer, regPattern, nChars):
        super(ReadingWorkerThread, self).__init__()
        self.aParent = aParent
        self.regPattern = regPattern
        self.nChars = nChars

    # def validateQRCode(self, a_qr_Code: str):
    #     # global regexPattern, qrCodeRegexPattern, numberOfCharactersToCheckInQRCode
    #
    #     # if a_qr_Code is not None and a_qr_Code != "":
    #     printBoldGreen(f"Evaluating {a_qr_Code} in validateQRCode()")
    #     # else:
    #     #     printBoldRed(f"{qr_Code = }")
    #     if (a_qr_Code is None) or (a_qr_Code == ""):
    #         return False
    #     copyOfQRCode = copy.deepcopy(a_qr_Code)
    #     if regexPattern == None:
    #         tempPattern = re.escape(qrCodeRegexPattern)
    #         regexPattern = re.compile(tempPattern)
    #     first54Chars = copyOfQRCode[:numberOfCharactersToCheckInQRCode]
    #     matches = regexPattern.match(first54Chars)
    #     qrCodeIsValid : bool = False
    #     if matches:
    #         qrCodeIsValid = True
    #     return qrCodeIsValid

    def run(self):
        global qrCodePort, qrCodeSerialConnection, qrCodeScannerConnected, qrCodeScannerTimeout, qrCodeRLock, qrCodeRLockTimeout
        global sendQRCode, currentQRCodeValue, currentDisplayString, retryCount, criticalLoggingCount, shutdownThreads, logger

        while sendQRCode:
            aQRCodeValue = self.aParent._getDataFromQRCodeScanner()
            if aQRCodeValue is not None and aQRCodeValue != "":
                print(f"In Reading Worker Thread, got value as {aQRCodeValue}")
            # sendQRCode = not (isQRCodeValid(currentQRCodeValue))
            if (aQRCodeValue is None) or (aQRCodeValue == ""):
                return False
            copyOfQRCode = copy.deepcopy(aQRCodeValue)
            first54Chars = copyOfQRCode[:numberOfCharactersToCheckInQRCode]
            matches = regexPattern.match(first54Chars)
            # if matches:
            #     sendQRCode = True
            # else:
            #     sendQRCode = False
            # if not sendQRCode:
            #     break
            # try:
            #     # QtTest.QTest.qWait(ReadingWorkerThread.STATE_SLEEP_TIME)
            #     time.sleep(ReadingWorkerThread.STATE_SLEEP_TIME)
            # except:
            #     pass
            if not matches:
                # sendQRCode = False
                currentQRCodeValue = ""
                createDisplayString("")
            else:
                # sendQRCode = True
                currentQRCodeValue = aQRCodeValue
                createDisplayString(aQRCodeValue)
        # print("Finished reading")


# *****************************************

if __name__ == "__main__":
    print("Starting QRCodeServer")
    qrCodeServer = QRCodeServer()
    while sendQRCode:
        qrCodeServer.getQRCode()
        print(f'QRCode is {qrCodeServer.getCurrentQRCode()}')
        print(f'Display String is {qrCodeServer.getCurrentQRCodeDisplayString()}')
    qrCodeServer.shutdown()
    sys.exit(0)
