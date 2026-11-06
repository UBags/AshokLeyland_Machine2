import sys
import time
import threading
import os
from typing import Union
from datetime import datetime

import numpy as np
import psycopg2
from psycopg2 import Error
import pprint

from redis import Redis

# From persistence package
import persistence
from persistence.Persistence import (setDatabaseName, makeFoldersForImages, getDatabaseName, getSchemaName, getDataSubFolder, checkConnection, checkIfDatabaseExists, checkIfQRCodeExists, insertData, getFolderForKnuckleImages, getFolderForHubAndBottomBearingImages, getFolderForTopBearingImages, getFolderForNutAndPlateWasherImages, getFolderForSplitPinAndWasherImages, getFolderForCapImages, getFolderForBunkAndNoBunkImages)

# From utils package
from utils.QRCodeHelper import getModel_LHSRHS_AndTonnage
from utils.CosThetaFileUtils import (
    getFileNameForSaving,
    saveFileWithFullPath,
)
from utils.RedisUtils import (
    readCycleSettingsForQRCode,
    readDataInDatabaseServerFromFEServer,
    acknowledgeRecordInDatabaseServer,
    fe2dbq,
    sendHeartbeatFromDBServerToHeartbeatServer,
    getStopCommandFromQueue,
    validate_image,
    ALIVE,
    DEAD,
)

# From BaseUtils
from BaseUtils import getFullyQualifiedName, getCurrentTime

from utils.CosThetaPrintUtils import (printBoldBlue,
    printBlue,
    printBoldGreen,
    printBoldRed,
    printBoldYellow
)

# From logutils
from logutils.SlaveLoggers import (
    SlaveFileLogger,
    SlaveConsoleLogger,
    Logger,
)

# From Configuration
from Configuration import CosThetaConfigurator
CosThetaConfigurator.getInstance()  # needs to be called to ensure that configurations are properly loaded

class DataPersistence():
    logSource = getFullyQualifiedName(__file__)
    IDLE_POLL_SECS : float = 1.0          # was a fixed 5 s sleep between single reads
    MAX_INSERT_ATTEMPTS : int = 3         # after this, the record is written to disk (FailedRecords/) and removed from the queue

    def __init__(self, mode: str, username: str | None, **kwargs) -> None:
        super().__init__()
        from logutils.LogSettings import setLogProcess
        setLogProcess("dbserver")         # per-server log level, crash capture, live configuration
        self.mode : str = mode
        self.username : str = username
        DataPersistence.logSource = getFullyQualifiedName(__file__, __class__)
        setDatabaseName(self.mode)
        makeFoldersForImages(self.mode)
        # printBoldBlue(f"{getCurrentMode() = }, {getDatabaseName() = }, {getDataSubFolder() = }")
        # print(f"{getFolderForKnuckleImages() = }")
        self.kwargs = kwargs
        self.stopped : bool = False
        self.stopRequested : bool = False
        self.failedAttempts : dict = {}
        self.hostname : str = CosThetaConfigurator.getInstance().getRedisHost()
        self.port : int = CosThetaConfigurator.getInstance().getRedisPort()
        # printBoldYellow(f"Reached 1 in DataPersistence")
        self.redisConnection : Union[Redis, None] = None
        self.clientRedisConnected : bool = False
        self.heartbeatRedisConnection : Union[Redis, None] = None
        self.heartbeatRedisConnected : bool = False
        # printBoldYellow(f"Reached 2 in DataPersistence")
        self.connectToRedis(True)
        # printBoldYellow(f"Reached 3 in DataPersistence")
        self.startPersistenceAndHeartbeatThreads()
        self.currentModelName : str | None = None
        SlaveFileLogger.getInstance().logInfo(DataPersistence.logSource,
                                              f"Started DB Server",
                                              Logger.GENERAL)
        SlaveConsoleLogger.getInstance().logInfo(DataPersistence.logSource,
                                                 f"Started DB Server",
                                                 Logger.GENERAL)
        # printBoldYellow(f"Reached 6 in DataPersistence")
        printBoldBlue(f"*****************")
        printBlue(f"Started DB Server")
        printBoldBlue(f"*****************")
        # printBoldYellow(f"Reached 7 in DataPersistence")

        # printAllVariables()

    # def stop(self) -> None:
    #     self.stopped = True
    #     # printBoldRed("Stopping DB Server")
    #     print("DEBUG: Stop flag set to True")
    #
    #     # Add thread joining
    #     if hasattr(self, 'persistence_thread') and self.persistence_thread:
    #         print("DEBUG: Waiting for persistence thread to finish...")
    #         self.persistence_thread.join(timeout=5)
    #         print("DEBUG: Persistence thread finished")

    def stop(self) -> None:
        import sys

        print("=" * 60)
        print("DEBUG: stop() called")
        print(f"DEBUG: Current stopped state: {self.stopped}")
        self.stopped = True
        print("DEBUG: Stop flag set to True")

        # List all active threads
        print(f"DEBUG: Active threads before join: {threading.active_count()}")
        for t in threading.enumerate():
            print(f"  - Thread: {t.name}, daemon={t.daemon}, alive={t.is_alive()}")

        # Join the main work thread
        if hasattr(self, 'threadForDoingMainWork') and self.threadForDoingMainWork:
            print(
                f"DEBUG: Waiting for threadForDoingMainWork to finish (alive={self.threadForDoingMainWork.is_alive()})...")
            self.threadForDoingMainWork.join(timeout=5)
            if self.threadForDoingMainWork.is_alive():
                print("WARNING: threadForDoingMainWork did not terminate within timeout!")
            else:
                print("DEBUG: threadForDoingMainWork finished")
        else:
            print("DEBUG: threadForDoingMainWork not found")

        # Join the heartbeat thread
        if hasattr(self, 'heartbeatThread') and self.heartbeatThread:
            print(f"DEBUG: Waiting for heartbeatThread to finish (alive={self.heartbeatThread.is_alive()})...")
            self.heartbeatThread.join(timeout=5)
            if self.heartbeatThread.is_alive():
                print("WARNING: heartbeatThread did not terminate within timeout!")
            else:
                print("DEBUG: heartbeatThread finished")
        else:
            print("DEBUG: heartbeatThread not found")

        # List remaining active threads
        print(f"DEBUG: Active threads after join: {threading.active_count()}")
        for t in threading.enumerate():
            print(f"  - Thread: {t.name}, daemon={t.daemon}, alive={t.is_alive()}")
        print("=" * 60)

    def connectToRedis(self, forceRenew=False) -> None:
        if forceRenew:
            self.redisConnection = None
            self.clientRedisConnected = False
            self.heartbeatRedisConnection = None
            self.heartbeatRedisConnected = False
        if not self.clientRedisConnected:
            try:
                self.redisConnection = Redis(self.hostname, self.port, retry_on_timeout=True)
                self.clientRedisConnected = True
                SlaveFileLogger.getInstance().logTakeNote(DataPersistence.logSource,
                                                          f"Redis Connection in {DataPersistence.logSource} is {self.redisConnection} in process {os.getpid()}",
                                                          Logger.SUCCESS)
                SlaveConsoleLogger.getInstance().logTakeAction(DataPersistence.logSource,
                                                               f"Redis Connection in {DataPersistence.logSource} is {self.redisConnection} in process {os.getpid()}",
                                                               Logger.SUCCESS)
            except Exception:
                self.clientRedisConnected = False
                SlaveFileLogger.getInstance().logTakeAction(DataPersistence.logSource,
                                                            f"Could not get Redis Connection in {DataPersistence.logSource} in process {os.getpid()}",
                                                            Logger.PROBLEM)
                SlaveConsoleLogger.getInstance().logTakeAction(DataPersistence.logSource,
                                                               f"Could not get Redis Connection in {DataPersistence.logSource} in process {os.getpid()}",
                                                               Logger.PROBLEM)
        if not self.heartbeatRedisConnected:
            try:
                self.heartbeatRedisConnection = Redis(self.hostname, self.port, retry_on_timeout=True)
                self.heartbeatRedisConnected = True
                SlaveFileLogger.getInstance().logTakeNote(DataPersistence.logSource,
                                                          f"Redis Connection in {DataPersistence.logSource} is {self.heartbeatRedisConnection} in process {os.getpid()}",
                                                          Logger.SUCCESS)
                SlaveConsoleLogger.getInstance().logTakeAction(DataPersistence.logSource,
                                                               f"Redis Connection in {DataPersistence.logSource} is {self.heartbeatRedisConnection} in process {os.getpid()}",
                                                               Logger.SUCCESS)
            except Exception:
                self.heartbeatRedisConnected = False
                SlaveFileLogger.getInstance().logTakeAction(DataPersistence.logSource,
                                                            f"Could not get Redis Connection for heartneat in {DataPersistence.logSource} in process {os.getpid()}",
                                                            Logger.PROBLEM)
                SlaveConsoleLogger.getInstance().logTakeAction(DataPersistence.logSource,
                                                               f"Could not get Redis Connection for heartneat in {DataPersistence.logSource} in process {os.getpid()}",
                                                               Logger.PROBLEM)

    def saveKnuckleImage(self, image: Union[np.ndarray, None]):
        if validate_image(image):
            fName = getFileNameForSaving()
            if self.currentModelName is not None:
                fName = f"{getFolderForKnuckleImages()}{self.currentModelName}/{fName}"
            else:
                fName = f"{getFolderForKnuckleImages()}{fName}"
            # printBoldYellow(f"Trying to save image to path {fName}")
            saveFileWithFullPath(image, fName)
            return fName
        return ""

    def saveHubAndBottomBearingImage(self, image: Union[np.ndarray, None]):
        if validate_image(image):
            fName = getFileNameForSaving()
            if self.currentModelName is not None:
                fName = f"{getFolderForHubAndBottomBearingImages()}{self.currentModelName}/{fName}"
            else:
                fName = f"{getFolderForHubAndBottomBearingImages()}{fName}"
            saveFileWithFullPath(image, fName)
            return fName
        return ""

    def saveTopBearingImage(self, image: Union[np.ndarray, None]):
        if validate_image(image):
            fName = getFileNameForSaving()
            if self.currentModelName is not None:
                fName = f"{getFolderForTopBearingImages()}{self.currentModelName}/{fName}"
            else:
                fName = f"{getFolderForTopBearingImages()}{fName}"
            saveFileWithFullPath(image, fName)
            return fName
        return ""

    def saveNutAndPlateWasherImage(self, image: Union[np.ndarray, None]):
        if validate_image(image):
            fName = getFileNameForSaving()
            if self.currentModelName is not None:
                fName = f"{getFolderForNutAndPlateWasherImages()}{self.currentModelName}/{fName}"
            else:
                fName = f"{getFolderForNutAndPlateWasherImages()}{fName}"
            saveFileWithFullPath(image, fName)
            return fName
        return ""

    def saveSplitPinAndWasherImages(self, image: Union[np.ndarray, None]):
        if validate_image(image):
            fName = getFileNameForSaving()
            if self.currentModelName is not None:
                fName = f"{getFolderForSplitPinAndWasherImages()}{self.currentModelName}/{fName}"
            else:
                fName = f"{getFolderForSplitPinAndWasherImages()}{fName}"
            saveFileWithFullPath(image, fName)
            return fName
        return ""

    def saveCapImage(self, image: Union[np.ndarray, None]):
        if validate_image(image):
            fName = getFileNameForSaving()
            if self.currentModelName is not None:
                fName = f"{getFolderForCapImages()}{self.currentModelName}/{fName}"
            else:
                fName = f"{getFolderForCapImages()}{fName}"
            saveFileWithFullPath(image, fName)
            return fName
        return ""

    def saveBunkAndNoBunkImage(self, image: Union[np.ndarray, None]):
        if validate_image(image):
            fName = getFileNameForSaving()
            if self.currentModelName is not None:
                fName = f"{getFolderForBunkAndNoBunkImages()}{self.currentModelName}/{fName}"
            else:
                fName = f"{getFolderForBunkAndNoBunkImages()}{fName}"
            saveFileWithFullPath(image, fName)
            return fName
        return ""

    def doWork(self) -> None:
        SlaveFileLogger.getInstance().logTakeNote(DataPersistence.logSource,
                                                  f"In DBServer, starting doWork",
                                                  Logger.GENERAL)
        SlaveConsoleLogger.getInstance().logTakeNote(DataPersistence.logSource,
                                                     f"In DBServer, starting doWork",
                                                     Logger.SUCCESS)
        # printBoldYellow(f"Starting doWork() in DataPersistence")
        savedRecords : int = 0
        hadData : bool = False
        while not self.stopped:
            # Pending records are processed back-to-back (FIFO); the loop only idles when fe2db.q is empty.
            if not hadData:
                if self.stopRequested:
                    printBoldRed("DBServer: fe2db.q drained after stop command; stopping")
                    self.stopped = True
                    break
                time.sleep(DataPersistence.IDLE_POLL_SECS)
            if self.stopped:
                print("DEBUG: doWork detected stop flag, exiting")
                break
            hadData = False
            messageId = None
            try:
                genuineDataReceived = False
                try:
                    (genuineDataReceived, timeOfMessage, qrCode,
                            knucklePicture, knuckleCheckResult, knuckleCheckDatetime,
                            hubAndBottomBearingPicture, hubAndBottomBearingCheckResult, hubAndBottomBearingCheckDatetime,
                            topBearingPicture, topBearingCheckResult, topBearingCheckDatetime,
                            nutAndPlateWasherPicture, nutAndPlateWasherCheckResult, nutAndPlateWasherCheckDatetime,
                            tighteningTorque1, tighteningTorque1Result, tighteningTorque1Datetime,
                            componentPressBunkPicture, componentPressBunkCheckResult, componentPressBunkCheckDatetime,
                            componentPressDone, componentPressDoneDatetime,
                            noBunkPicture, noBunkCheckResult, noBunkCheckDatetime,
                            tighteningTorque2, tighteningTorque2Result, tighteningTorque2Datetime,
                            splitPinAndWasherPicture, splitPinAndWasherCheckResult, splitPinAndWasherCheckDatetime,
                            capPicture, capCheckResult, capCheckDatetime,
                            capPressBunkPicture, capPressBunkCheckResult, capPressBunkCheckDatetime,
                            capPressDone, capPressDoneDatetime,
                            freeRotationTorque1, freeRotationTorque1Result, freeRotationTorque1Datetime,
                            overallResult, tighteningTorque1RotationDone, tighteningTorque2RotationDone,
                            messageId) = readDataInDatabaseServerFromFEServer(
                                redisConnection=self.redisConnection, deleteAfterRead=False, returnMessageId=True,
                                returnRotationDone=True)
                except Exception as e:
                    printBoldYellow(f"Encountered exception {e} in readDataInDatabaseServerFromFEServer()")

                # printBoldYellow(f"Read data in doWork() of DataPersistence")

                if genuineDataReceived:
                    hadData = True
                    model, lhs_rhs, tonnage = getModel_LHSRHS_AndTonnage(qrCode)
                    self.currentModelName = model
                    # printBoldGreen(f"Successfully fetched data from the queue in doWork()")
                    knucklePictureFile : str = self.saveKnuckleImage(knucklePicture)
                    hubAndBottomBearingPictureFile : str  = self.saveHubAndBottomBearingImage(hubAndBottomBearingPicture)
                    topBearingPictureFile : str  = self.saveTopBearingImage(topBearingPicture)
                    nutAndPlateWasherPictureFile : str  = self.saveNutAndPlateWasherImage(nutAndPlateWasherPicture)
                    bunkForComponentPressFile : str  = self.saveBunkAndNoBunkImage(componentPressBunkPicture)
                    noBunkFile : str  = self.saveBunkAndNoBunkImage(noBunkPicture)
                    splitPinAndWasherFile : str = self.saveSplitPinAndWasherImages(splitPinAndWasherPicture)
                    capPictureFile = self.saveCapImage(capPicture)
                    bunkCapPressFile : str  = self.saveBunkAndNoBunkImage(capPressBunkPicture)
                    SlaveFileLogger.getInstance().logInfo(DataPersistence.logSource,
                                                          f"Finished savings images",
                                                          Logger.SUCCESS)
                    # tStamp = getYMDHMSmFormatFromTimeDotTime(timeOfMessage)[:-4]
                    tStamp = datetime.fromtimestamp(float(timeOfMessage)).strftime('%Y-%m-%d %H:%M:%S')
                    from logutils.LogSettings import setLocalCycleContext
                    setLocalCycleContext(qrCode)      # this record's QR code on every log line while storing it
                    qrCodeExists = checkIfQRCodeExists(qr_code=qrCode, db_name=getDatabaseName())

                    # print("=" * 80)
                    # print("DEBUG: About to call insertData()")
                    # print(f"db_name = {getDatabaseName()}")
                    # print(f"qr_code = {qrCode}")
                    # print(f"model_name = {model}")
                    # print(f"component_assembly_start_datetime = {tStamp}")
                    # print(f"check_knuckle_result = {knuckleCheckResult}")
                    # print(f"check_knuckle_datetime = {knuckleCheckDatetime}")
                    # print("=" * 80)

                    # machine settings the IOServer wrote to the PLC for this component (state 2); None if not published
                    cycleSettings = readCycleSettingsForQRCode(self.redisConnection, qrCode)
                    try:
                        success = insertData(
                            db_name = getDatabaseName(),
                            qr_code = qrCode,
                            model_name = model,
                            lhs_rhs= lhs_rhs,
                            model_tonnage = tonnage,
                            component_assembly_start_datetime = tStamp,
                            check_knuckle_imagefile = knucklePictureFile,
                            check_knuckle_result = knuckleCheckResult,
                            check_knuckle_datetime = knuckleCheckDatetime,
                            check_hub_and_bottom_bearing_imagefile = hubAndBottomBearingPictureFile,
                            check_hub_and_bottom_bearing_result = hubAndBottomBearingCheckResult,
                            check_hub_and_bottom_bearing_datetime = hubAndBottomBearingCheckDatetime,
                            check_top_bearing_imagefile = topBearingPictureFile,
                            check_top_bearing_result = topBearingCheckResult,
                            check_top_bearing_datetime = topBearingCheckDatetime,
                            check_nut_and_platewasher_imagefile = nutAndPlateWasherPictureFile,
                            check_nut_and_platewasher_result = nutAndPlateWasherCheckResult,
                            check_nut_and_platewasher_datetime = nutAndPlateWasherCheckDatetime,
                            nut_tightening_torque_1 = float(tighteningTorque1),
                            nut_tightening_torque_1_result = tighteningTorque1Result,
                            nut_tightening_torque_1_datetime = tighteningTorque1Datetime,
                            check_bunk_for_component_press_imagefile= bunkForComponentPressFile,
                            check_bunk_for_component_press_result= componentPressBunkCheckResult,
                            check_bunk_for_component_press_datetime= componentPressBunkCheckDatetime,
                            component_press_done = componentPressDone,
                            component_press_datetime = componentPressDoneDatetime,
                            check_no_bunk_imagefile= noBunkFile,
                            check_no_bunk_result= noBunkCheckResult,
                            check_no_bunk_datetime= noBunkCheckDatetime,
                            nut_tightening_torque_2 = float(tighteningTorque2),
                            nut_tightening_torque_2_result = tighteningTorque2Result,
                            nut_tightening_torque_2_datetime = tighteningTorque2Datetime,
                            check_splitpin_and_washer_imagefile = splitPinAndWasherFile,
                            check_splitpin_and_washer_result = splitPinAndWasherCheckResult,
                            check_splitpin_and_washer_datetime = splitPinAndWasherCheckDatetime,
                            check_cap_imagefile = capPictureFile,
                            check_cap_result = capCheckResult,
                            check_cap_datetime = capCheckDatetime,
                            check_bunk_cap_press_imagefile= bunkCapPressFile,
                            check_bunk_cap_press_result= capPressBunkCheckResult,
                            check_bunk_cap_press_datetime= capPressBunkCheckDatetime,
                            cap_press_done = capPressDone,
                            cap_press_datetime = capPressDoneDatetime,
                            free_rotation_torque_1 = float(freeRotationTorque1),
                            free_rotation_torque_1_result = freeRotationTorque1Result,
                            free_rotation_torque_1_datetime = freeRotationTorque1Datetime,
                            ok_notok_result = overallResult,
                            username = self.username,
                            remarks = "DUPLICATE" if qrCodeExists else "",
                            setting_rotation_rpm = cycleSettings.get("rotation_rpm"),
                            setting_tightening_torque1_setpoint = cycleSettings.get("tightening_torque1_setpoint"),
                            setting_tightening_torque2_setpoint = cycleSettings.get("tightening_torque2_setpoint"),
                            setting_no_of_rotation1_ccw = cycleSettings.get("no_of_rotation1_ccw"),
                            setting_no_of_rotation1_cw = cycleSettings.get("no_of_rotation1_cw"),
                            nut_tightening_torque_1_rotation_done = tighteningTorque1RotationDone,
                            nut_tightening_torque_2_rotation_done = tighteningTorque2RotationDone)
                        if success:
                            acknowledgeRecordInDatabaseServer(self.redisConnection, messageId)
                            self.failedAttempts.pop(messageId, None)
                            savedRecords += 1
                            printBoldGreen(f"Inserted record number {savedRecords} successfully in database")
                            SlaveFileLogger.getInstance().logInfo(DataPersistence.logSource,
                                                                  f"Inserted record number {savedRecords} successfully in database",
                                                                  Logger.SUCCESS)
                        else:
                            printBoldYellow(f"Record not inserted for QR code {qrCode}")
                            SlaveFileLogger.getInstance().logTakeAction(DataPersistence.logSource,
                                                                  f"Failed to insert record in database",
                                                                  Logger.ISSUE)
                            self.handleFailedInsert(messageId, qrCode, "insertData() returned False")
                        self.currentModelName = None
                    except (Exception, Error) as error:
                        self.handleFailedInsert(messageId, qrCode, f"{error}")
                        printBoldRed(f"Error {error} while inserting data in data_table {id} in scheme {getSchemaName()} in database {getDatabaseName()} due to {error}")
                        SlaveFileLogger.getInstance().logTakeNote(DataPersistence.logSource,
                                                                  f"Error {error} while inserting data in data_table {id} in scheme {getSchemaName()} in database {getDatabaseName()} due to {error}",
                                                                  Logger.RISK)
                        SlaveConsoleLogger.getInstance().logTakeNote(DataPersistence.logSource,
                                                                     f"Error {error} while inserting data in data_table {id} in schema {getSchemaName()} in database {getDatabaseName()} due to {error}",
                                                                     Logger.RISK)
                else:
                    # printBoldYellow(f"No data received in doWork() - waiting...")
                    pass

                if (savedRecords % 50 == 0):
                    SlaveConsoleLogger.getInstance().logInfo(DataPersistence.logSource,
                                                             f"Saved {savedRecords} records overall since the last start",
                                                             Logger.SUCCESS)
                else:
                    # print(f"Got None in {id}")
                    pass

            except (Exception, Error) as error1:
                printBoldRed(f"Error in DBServer in process {os.getpid()} due to {error1}")
                SlaveFileLogger.getInstance().logTakeAction(DataPersistence.logSource,
                                                            f"Error in DBServer in process {os.getpid()} due to {error1}",
                                                            Logger.PROBLEM)
                SlaveConsoleLogger.getInstance().logTakeAction(DataPersistence.logSource,
                                                               f"Error in DBServer in process {os.getpid()} due to {error1}",
                                                               Logger.PROBLEM)
            try:
                _, shallStop = getStopCommandFromQueue(self.redisConnection)
                if shallStop and not self.stopRequested:
                    printBoldRed(f"Got stop command in process {os.getpid()} in DBServer; draining fe2db.q first")
                    self.stopRequested = True
            except Exception:
                self.connectToRedis(True)
        printBoldRed(f"In DBServer, stopping doWork")
        print("DEBUG: Exiting doWork loop")

    def handleFailedInsert(self, messageId, qrCode: str, reason: str) -> None:
        """A failed insert leaves the record on fe2db.q to be retried. After MAX_INSERT_ATTEMPTS it is written as JSON to
        <basefolder><Test|Production>/FailedRecords/ (images are already on disk) and removed, so one bad record cannot
        block the queue and nothing is silently lost."""
        if messageId is None:
            return
        attempts = self.failedAttempts.get(messageId, 0) + 1
        self.failedAttempts[messageId] = attempts
        if attempts < DataPersistence.MAX_INSERT_ATTEMPTS:
            printBoldYellow(f"Insert attempt {attempts} failed for QR {qrCode}; will retry ({reason})")
            return
        try:
            import json
            folder = f"{CosThetaConfigurator.getInstance().getBaseFolderForImages()}{getDataSubFolder()}FailedRecords/"
            os.makedirs(folder, exist_ok=True)
            entries = self.redisConnection.xrange(fe2dbq, min=messageId, max=messageId, count=1)
            record = {}
            if entries:
                for k, v in entries[0][1].items():
                    key = k.decode("utf-8") if isinstance(k, bytes) else str(k)
                    if isinstance(v, bytes) and v[:4] == b"\x89PNG":
                        record[key] = f"<png image, {len(v)} bytes - saved with the cycle images>"
                    else:
                        record[key] = v.decode("utf-8", errors="replace") if isinstance(v, bytes) else str(v)
            record["_failureReason"] = reason
            record["_attempts"] = attempts
            safeQR = "".join(ch if ch.isalnum() else "_" for ch in (qrCode or "noQR"))[:60]
            fileName = f"{folder}{datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}_{safeQR}.json"
            with open(fileName, "w", encoding="utf-8") as f:
                json.dump(record, f, indent=2)
            acknowledgeRecordInDatabaseServer(self.redisConnection, messageId)
            self.failedAttempts.pop(messageId, None)
            msg = f"Record for QR {qrCode} could not be stored after {attempts} attempts; written to {fileName}"
            printBoldRed(msg)
            SlaveFileLogger.getInstance().logTakeAction(DataPersistence.logSource, msg, Logger.PROBLEM)
            SlaveConsoleLogger.getInstance().logTakeAction(DataPersistence.logSource, msg, Logger.PROBLEM)
        except Exception as e:
            printBoldRed(f"Could not write failed record for QR {qrCode} to disk: {e}; it stays on {fe2dbq}")

    def sendHeartbeat(self) -> None:
        sleepTime = CosThetaConfigurator.getInstance().getDatabaseConnectionStatusSleepInterval()
        while not self.stopped:
            try:
                connected = checkConnection(db_name=getDatabaseName())
                if connected:
                    # printBoldGreen(f"Connected. Sending heartbeat with OK")
                    if self.heartbeatRedisConnected:
                        sendHeartbeatFromDBServerToHeartbeatServer(self.heartbeatRedisConnection, ALIVE)
                    else:
                        self.connectToRedis(forceRenew=True)
                else:
                    # printBoldRed(f"Not Connected. Sending heartbeat with Not OK")
                    if self.heartbeatRedisConnected:
                        sendHeartbeatFromDBServerToHeartbeatServer(self.heartbeatRedisConnection, DEAD)
                    else:
                        self.connectToRedis(forceRenew=True)
            except Exception:
                SlaveFileLogger.getInstance().logTakeAction(DataPersistence.logSource,
                                                            f"Could not send heartbeat from DBServer",
                                                            Logger.PROBLEM)
                SlaveConsoleLogger.getInstance().logTakeAction(DataPersistence.logSource,
                                                               f"Could not send heartbeat from DBServer",
                                                               Logger.PROBLEM)
                printBoldRed(f"Could not send heartbeat from DBServer")
            try:
                _, shallStop = getStopCommandFromQueue(self.heartbeatRedisConnection)
                if shallStop and not self.stopRequested:
                    # Do not stop here: the main work thread drains fe2db.q first, then sets self.stopped
                    printBoldRed(f"Got stop command in process {os.getpid()} in DBServer (heartbeat thread)")
                    self.stopRequested = True
            except Exception:
                self.connectToRedis(True)
            # time.sleep(sleepTime)
            for _ in range(int(sleepTime * 10)):  # Check every 0.1 seconds
                if self.stopped:
                    break
                time.sleep(0.1)

    DAY_CHECK_INTERVAL_SECS: float = 60.0

    def watchForNewDay(self) -> None:
        """Images are saved in a folder per day (<base>/<mode>/<step>/<YYYY-MM-DD>/). The folders were worked out once,
        when the DB server started, so on a PC that stays on, every image after midnight went into the start day's
        folder. This thread wakes up every DAY_CHECK_INTERVAL_SECS (60 s), keeps the date of its last wake-up, and
        when the date has changed since then it makes the new day's folders (makeFoldersForImages) - the images saved
        from then on go there."""
        lastDate = datetime.now().date()
        while not self.stopped:
            for _ in range(int(DataPersistence.DAY_CHECK_INTERVAL_SECS * 2)):
                if self.stopped:
                    return
                time.sleep(0.5)
            try:
                today = datetime.now().date()
                if today != lastDate:
                    makeFoldersForImages(self.mode)
                    message = (f"New day ({lastDate} -> {today}): images are now saved under "
                               f"{getFolderForKnuckleImages()} and the other {today} folders")
                    printBoldGreen(message)
                    SlaveFileLogger.getInstance().logTakeNote(DataPersistence.logSource, message, Logger.SUCCESS)
                    lastDate = today
            except Exception as e:
                printBoldRed(f"Could not make the image folders of the new day: {e}")      # tried again next minute

    def startPersistenceAndHeartbeatThreads(self) -> None:
        self.dayWatchThread = threading.Thread(name='Image folder day change', target=self.watchForNewDay,
                                               args=(), daemon=True)
        self.dayWatchThread.start()
        self.threadForDoingMainWork = threading.Thread(
            name=f'Thread for saving images and updating the database',
            target=self.doWork,
            args=(), daemon=True)
        self.heartbeatThread = threading.Thread(name=f'Heartbeat - {DataPersistence.logSource}',
                                                target=self.sendHeartbeat,
                                                args=(), daemon=True)
        self.threadForDoingMainWork.start()
        # printBoldYellow(f"Reached 4 in DataPersistence")
        self.heartbeatThread.start()
        # printBoldYellow(f"Reached 5 in DataPersistence")

    def printData(self) -> None:
        try:
            print(checkConnection(db_name=getDatabaseName()))
            print(checkIfDatabaseExists("ashok_leyland_production"))
            print(getCurrentTime())
            print(f"{getFolderForKnuckleImages() = }")
            print(f"{getFolderForHubAndBottomBearingImages() = }")
            print(f"{getFolderForTopBearingImages() = }")
            print(f"{getFolderForNutAndPlateWasherImages() = }")
            print(f"{getFolderForSplitPinAndWasherImages() = }")
            print(f"{getFolderForCapImages() = }")
            print(f"{getFolderForBunkAndNoBunkImages() = }")
        except (Exception, Error) as error:
            SlaveFileLogger.getInstance().logTakeNote(DataPersistence.logSource,
                                                      f"Error {error} while executing printData()",
                                                      Logger.RISK)
            SlaveConsoleLogger.getInstance().logTakeNote(DataPersistence.logSource,
                                                         f"Error {error} while executing printData()",
                                                         Logger.RISK)


def startDBServer(mode: str = "Test", username: str | None = "admin") -> None:
    dbServer = DataPersistence(mode=mode, username=username)
    dbServer.threadForDoingMainWork.join()
    sys.exit(0)

# startDBServer()