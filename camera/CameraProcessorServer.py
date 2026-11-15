from utils.SuppressedExceptions import noteSuppressed   # logs exceptions that are caught and ignored
import time
from threading import Thread
import queue  # noqa: F401 - was "from queue import *"; nothing used from it, imported for its import-time effects
import cv2
import numpy as np
import sys

import logutils.SlaveLoggers
import inspect
import os
import threading
from redis import Redis
from typing import Any, Dict, List, Optional, Tuple, Union
from camera.RTSPCam import (
    ALIVE, DEAD, RTSPCam, actionKeyString, capPictureKeyString, capPressBunkPictureKeyString,
    componentPressBunkPictureKeyString, currentMachineStateKeyString, hubAndBottomBearingPictureKeyString,
    knucklePictureKeyString, noBunkPictureKeyString, notok, nutAndPlateWasherPictureKeyString, ok,
    requestIdKeyString, splitPinAndWasherPictureKeyString, takePictureCommand, timeKeyString,
    topBearingPictureKeyString)
from BaseUtils import getFullyQualifiedName, profile
from Configuration import CosThetaConfigurator
from logutils.Logger import Logger
from statemachine.StateMachine import MachineState
from utils.RedisUtils import (
    readComponentQRCodeInCameraServerFromQRCodeServer, readDataInCameraServerFromIOServer,
    sendAlarmSignalToHeartbeatServer, sendDataFromCameraServerToFEServer, sendDataFromCameraServerToIOServer,
    sendHeartbeatFromCameraServerToHeartbeatServer, sendStoppedResponse, setFrontendNotice)
from processors.GenericQueueProcessor import GenericQueueProcessor
from utils.CosThetaFileUtils import getFileNameForSaving, saveFileWithFullPath
import utils.RedisUtils  # noqa: F401 - was "from utils.RedisUtils import *"; nothing used from it, imported for its import-time effects
import BaseUtils  # noqa: F401 - was "from BaseUtils import *"; nothing used from it, imported for its import-time effects
from logutils import SlaveLoggers
from logutils.SlaveLoggers import *
from logutils.SlaveLoggers import logBoth
from logutils.Logger import MessageType
from utils.QRCodeHelper import getModel_LHSRHS_AndTonnage
# from processors.image.ImageSaver import *
from concurrent.futures import ThreadPoolExecutor, wait

from camera.CheckKnuckle import CheckKnuckle
from camera.CheckHubAndBottomBearing import CheckHubAndBottomBearing
from camera.CheckTopBearing import CheckTopBearing
from camera.CheckNutAndPlateWasher import CheckNutAndPlateWasher
from camera.CheckSplitPinAndWasher import CheckSplitPinAndWasher
from camera.CheckCap import CheckCap
from camera.CheckNoCapBunk import CheckNoCapBunk
from camera.CheckNoBunk import CheckNoBunk
from camera.CheckBunk import CheckBunk
from camera import VisionFrame

import warnings
warnings.filterwarnings('ignore', '.*h264.*', )

import Configuration  # noqa: F401 - was "from Configuration import *"; nothing used from it, imported for its import-time effects
CosThetaConfigurator.getInstance()

# CONSTANTS
FIRST_PICTURE : str = "firstPicture"
SECOND_PICTURE : str = "secondPicture"
THIRD_PICTURE : str = "thirdPicture"
NO_PICTURE_REASON : str = "noPictureReason"     # set by TakePictures when it has no (new) picture: the check is NOT OK

# GLOBAL VARIABLES
logSource = getFullyQualifiedName(__file__)

# The camera server's work threads (MonitorGetPicQueue, TakePictures, ConverterScalerAndProcessor), registered by
# CameraServer(): the heartbeat is DEAD while one of them is not running or not going round its loop (WorkerHealth)
from utils.WorkerHealth import WorkerWatch
cameraWorkers = WorkerWatch("Camera server")

# *******************************Start of the main Threaded Classes*********************************

# *******************************MonitorGetPicQueue start*******************************************
class MonitorGetPicQueue(GenericQueueProcessor):

    # NOTE: This is a thread that is constantly monitoring the io2cameraq for a (takePicture+currentMachineState) message
    # How it works is like this:
    # 1. In init(), I put MonitorGetPicQueue.monitorQCommand into the python queue
    # 2. That ensures that in the doWork() loop, the non-blocking getItem() always gets a non-None item
    # 3. This non-None item in turn ensures that this thread always peeps into the non-blocking
    #    Redis call to io2cameraq
    #    in every loop of the thread to see if there is an item in that Redis queue

    logSource = getFullyQualifiedName(__file__)
    monitorQCommand : str = "monitorQ"

    def __init__(self, name: str, consumer: Thread = None, timeout: int = 1,
                 # sleepTime: float = CosThetaConfigurator.getInstance().getSleepTimeForMonitoringTakePicQ(),
                 sleepTime: float = 0.1,
                 blocking: bool = True, monitorRedisQueueForStopping: bool = True, max_size: int = 32, **kwargs):
        GenericQueueProcessor.__init__(self, name=name, consumer=consumer, sleepTime=sleepTime, blocking=blocking,
                                       timeout=timeout,
                                       monitorRedisQueueForStopping=monitorRedisQueueForStopping, max_size=max_size,
                                       **kwargs)
        if consumer is None:
            logBoth('logWarning', MonitorGetPicQueue.logSource,
                    f"In {MonitorGetPicQueue.logSource}.{self.name}, consumer instance is None",
                    Logger.ISSUE)
            raise Exception("Consumer is needed for MonitorGetPicQueue")
        MonitorGetPicQueue.logSource = getFullyQualifiedName(__file__, __class__)
        self.redisConnectionQ = None
        self.clientRedisConnectedQ = False
        self.connectToRedis()
        self.addItem(MonitorGetPicQueue.monitorQCommand)

    def connectToRedis(self, forceRenew = False):
        if forceRenew:
            self.redisConnectionQ = None
            self.clientRedisConnectedQ = False
        if not self.clientRedisConnectedQ:
            try:
                self.redisConnectionQ = Redis(GenericQueueProcessor.redisHost, GenericQueueProcessor.redisPort,
                                              retry_on_timeout=True)
                self.clientRedisConnectedQ = True
                logBoth('logInfo', MonitorGetPicQueue.logSource, 'Redis connection established', Logger.SUCCESS)
            except Exception:
                self.clientRedisConnectedQ = False
                logBoth('logCritical', MonitorGetPicQueue.logSource, 'Could not get Redis Connection', Logger.PROBLEM)
                # pass

    def getItem(self, blocking=True) -> Union[Any, None]:

        return super().getItem()

    def preWorkLoop(self):
        return

    def processItem(self, item: Any) -> Any:
        # print(f"In MonitorGetPic, got item {item}")
        if not self.getStopped():
            # See NOTE above to understand wht this line is there
            self.addItem(MonitorGetPicQueue.monitorQCommand)
        timeOfMessage : Union[str, float, None] = None
        currentMachineState : MachineState = MachineState.INVALID_STATE
        takePicture : bool = False
        requestId = None
        try:
            timeOfMessage, currentMachineState, takePicture, requestId = readDataInCameraServerFromIOServer(
                self.redisConnectionQ, block=500, withRequestId=True)
        except Exception as e:
            logBoth('logWarning', MonitorGetPicQueue.logSource, f"Exception reading camera queue: {e}", Logger.ISSUE)
            self.connectToRedis()
        if (not takePicture) and currentMachineState == MachineState.READ_QR_CODE:
            # End-of-cycle message from the IOServer (cycle completed or aborted): drop what was kept for the cycle -
            # the QR code, the earlier pictures and the top bearing profile
            ImageProcessor.endOfCycle()
            logBoth('logInfo', MonitorGetPicQueue.logSource,
                    "End of cycle from the IOServer: QR code, pictures and top bearing profile of the cycle dropped",
                    Logger.SUCCESS)
            return None
        if takePicture:
            timeOfMessage = float(timeOfMessage)
            logBoth('logInfo', MonitorGetPicQueue.logSource, f"Got takePicture command in MonitorGetPicQueue", Logger.SUCCESS)
            messageToBeSentToConsumer = {timeKeyString: timeOfMessage, currentMachineStateKeyString : currentMachineState,
                    actionKeyString: takePictureCommand, requestIdKeyString: requestId}
            logBoth('logDebug', MonitorGetPicQueue.logSource, f"In MonitorGetPic, sending message {messageToBeSentToConsumer}", Logger.GENERAL)
            return messageToBeSentToConsumer
        else:
            # printBoldRed(f"Got no command in MonitorGetPicQueue")
            pass
        return None

    def postWorkLoop(self):
        # printBoldBlue(f"Finished thread {self.name}")
        if self.monitorRedisQueueForStopping:
            sendStoppedResponse(self.redisConnectionForMonitoringStop, f"{MonitorGetPicQueue.logSource}.{self.name}")

# *******************************MonitorGetPicQueue end*********************************************

# from utils.CosThetaFileUtils import *
#
# class ImageFeederForTesting():
#
#     def __init__(self):
#         self.indexTable=[]
#         self.currentIndex = 0
#         self.basePath = "C:/PythonProjects/Geltec_New/Images/Right Disc All/"
#         self.filesInDirectory = getAllFilesInDirectory(self.basePath)
#         self.nFilesInDirectory = len(self.filesInDirectory)
#
#     def getNextImage(self):
#         if self.currentIndex >= self.nFilesInDirectory:
#             return None
#         imagePath = self.basePath + self.filesInDirectory[self.currentIndex]
#         image = cv2.imread(imagePath)
#         self.currentIndex += 1
#         if (self.nFilesInDirectory - self.currentIndex) < 4:
#             self.currentIndex = 0
#         return image

# *******************************Results Tracking start*******************************************

INVALID_RESULT: Union[str, bool] = "Invalid Result"  # Example value

# Define the type for each inner tuple
ResultPair = Tuple[Union[str, bool], Optional[np.ndarray]]

# This list stores the evaluation result (True or False), and the image (None | np.ndarray)
resultsFromPictureAssessment : List[ResultPair] = [(INVALID_RESULT, None), (INVALID_RESULT, None), (INVALID_RESULT, None)]
# This list stores the evaluation result (True or False)
resultsStatus : list[bool] = [False, False, False]
resultsLock = threading.Lock()

def resetResults():
    global resultsFromPictureAssessment, resultsStatus, resultsLock
    # printBold("Entered reset results")
    acquired = resultsLock.acquire()
    if acquired:
        try:
            resultsFromPictureAssessment = [[INVALID_RESULT, None],[INVALID_RESULT, None],[INVALID_RESULT, None]]
            resultsStatus = [False, False, False]
        except Exception:
            noteSuppressed()
        resultsLock.release()
    # printBold("Exiting reset results")

def addAResult(anImage : np.ndarray, aResult : bool, index: int):
    global resultsFromPictureAssessment, resultsStatus, resultsLock
    # acquired = resultsLock.acquire()
    # if acquired:
    #     try:
    #         resultsFromPictureAssessment[index] = (aResult, anImage)
    #         resultsStatus[index] = aResult
    #     except:
    #         pass
    #     resultsLock.release()
    with resultsLock:
        try:
            resultsFromPictureAssessment[index] = (aResult, anImage)
            resultsStatus[index] = aResult
        except Exception:
            noteSuppressed()


# *******************************Results Tracking End*********************************************

# *******************************TakePictures start*********************************************

class TakePictures(GenericQueueProcessor):

    logSource = getFullyQualifiedName(__file__)

    def __init__(self, name: str, consumer: GenericQueueProcessor = None, timeout : int = 1, sleepTime : float = 0.1,
                 monitorRedisQueueForStopping : bool = True, max_size=32, **kwargs):

        GenericQueueProcessor.__init__(self, name=name, consumer=consumer, timeout=timeout, sleepTime=sleepTime, blocking=True, monitorRedisQueueForStopping=monitorRedisQueueForStopping, max_size=max_size, **kwargs)
        TakePictures.logSource = getFullyQualifiedName(__file__, __class__)
        self.cameraID = CosThetaConfigurator.getInstance().getCameraId()
        self.cameraIP = CosThetaConfigurator.getInstance().getCameraIP()
        self.cameraPort = CosThetaConfigurator.getInstance().getCameraPort()
        self.cameraUID = CosThetaConfigurator.getInstance().getCameraUid()
        self.cameraPwd = CosThetaConfigurator.getInstance().getCameraPwd()
        self.gapBetweenPictures = CosThetaConfigurator.getInstance().getTimeBetweenPicturesInMs() * 1.0 / 1000
        self.camera = RTSPCam(IP=self.cameraIP, port=self.cameraPort, uid=self.cameraUID, password=self.cameraPwd, name=self.cameraID)
        self.redisConnectionForHeartbeat = None
        self.clientRedisConnectedForHeartbeat = False
        self.connectToRedisForHeartbeat()
        self.launchThreadForHeartbeat()
        CosThetaConfigurator.addReloadListener(self._applyLiveConfiguration)

    def _applyLiveConfiguration(self, changedKeys) -> None:
        """Live configuration: the gap between pictures and the missing-frame limit apply at once; a change to the
        camera connection (IP, port, user, password, id) needs a restart of the camera server."""
        prefix = CosThetaConfigurator.getVisionKeyPrefix()
        gap = CosThetaConfigurator.getInstance().getTimeBetweenPicturesInMs() * 1.0 / 1000
        if gap != self.gapBetweenPictures:
            self.gapBetweenPictures = gap
            logBoth('logWarning', logSource, f"Configuration applied while running: gap between pictures = {gap} s", MessageType.RISK)
        try:
            limit = CosThetaConfigurator.getInstance().getMaxAllowedNoneFrameCountsInCamera()
            if getattr(self.camera, "maxAllowedNoneFrameCountsInCamera", limit) != limit:
                self.camera.maxAllowedNoneFrameCountsInCamera = limit
                logBoth('logWarning', logSource, f"Configuration applied while running: max allowed missing frames = {limit}", MessageType.RISK)
        except Exception:
            noteSuppressed()
        connection = [k for k in changedKeys if k.startswith(f"{prefix}vision.camera.")]
        if connection:
            logBoth('logError', logSource, f"Camera connection settings changed ({', '.join(connection)}): restart the "
                    f"camera server for them to take effect", MessageType.ISSUE)

    def connectToRedisForHeartbeat(self, forceRenew=False):
        if forceRenew:
            self.redisConnectionForHeartbeat = None
            self.clientRedisConnectedForHeartbeat = False
        if not self.clientRedisConnectedForHeartbeat:
            try:
                self.redisConnectionForHeartbeat = Redis(GenericQueueProcessor.redisHost, GenericQueueProcessor.redisPort, retry_on_timeout=True)
                self.clientRedisConnectedForHeartbeat = True
                logBoth('logInfo', TakePictures.logSource, 'Heartbeat Redis connection established', Logger.SUCCESS)
            except Exception as e:
                self.clientRedisConnectedForHeartbeat = False
                self.redisConnectionForHeartbeat = None
                logBoth('logWarning', TakePictures.logSource,
                        f'Could not get Redis Connection for heartbeat in {TakePictures.logSource} due to {e}',
                        Logger.RISK)

    def addItem(self, item: Any) -> Union[Any, None]:
        if isinstance(item, dict):
            if item[actionKeyString] == takePictureCommand:
                return super().addItem(item)

    def preWorkLoop(self):
        return

    def processItem(self, item : Any) -> Any :
        logBoth('logDebug', TakePictures.logSource, f"In TakePictures, about to process item. Camera status is {self.camera.connected}", Logger.GENERAL)
        if not self.camera.connected:
            # no pictures, but the answer must still name the picture state the IOServer asked for: it then gets a
            # NOT OK for that check (and the PLC its NOT OK handshake) instead of waiting for an answer that never comes
            logBoth('logWarning', TakePictures.logSource, "Camera not connected: no pictures; the check will be NOT OK",
                    Logger.RISK)
            return {timeKeyString: item[timeKeyString] if isinstance(item, dict) else None, FIRST_PICTURE: None,
                    SECOND_PICTURE: None, THIRD_PICTURE: None,
                    currentMachineStateKeyString: self._requestedState(item), requestIdKeyString: self._requestId(item),
                    NO_PICTURE_REASON: "camera not connected"}
        if item is not None:
            try:
                # Each picture must be a frame that arrived after the request (and after the previous picture):
                # a stalled stream keeps its last frame, which shows the part where it was before (CHANGES 84)
                maxWait = CosThetaConfigurator.getInstance().getCameraFrameMaxWait()
                requestedAt = time.monotonic()
                frameTime = requestedAt
                stalled: list = []
                firstPic: Union[None | np.ndarray] = None
                try:
                    got, firstPic, frameTime = self.camera.getFrameAfter(requestedAt, maxWait)
                    if not got:
                        stalled.append("first")
                        frameTime = requestedAt
                except Exception as e1:
                    logBoth('logWarning', TakePictures.logSource,
                            f"Exception getting first frame: {e1}", Logger.RISK)

                logBoth('logDebug', TakePictures.logSource, f"Got first picture with shape {firstPic.shape if isinstance(firstPic, np.ndarray) else 'None'}", Logger.GENERAL)

                time.sleep(self.gapBetweenPictures)
                secondPic : Union[None | np.ndarray] = None
                try:
                    got, secondPic, t2 = self.camera.getFrameAfter(frameTime, maxWait)
                    if got:
                        frameTime = t2
                    else:
                        stalled.append("second")
                except Exception as e2:
                    logBoth('logWarning', TakePictures.logSource,
                            f"Exception getting second frame: {e2}", Logger.RISK)

                logBoth('logDebug', TakePictures.logSource, f"Got second picture with shape {secondPic.shape if isinstance(secondPic, np.ndarray) else 'None'}", Logger.GENERAL)
                time.sleep(self.gapBetweenPictures)
                thirdPic: Union[None | np.ndarray] = None
                try:
                    got, thirdPic, _ = self.camera.getFrameAfter(frameTime, maxWait)
                    if not got:
                        stalled.append("third")
                except Exception as e3:
                    logBoth('logWarning', TakePictures.logSource,
                            f"Exception getting third frame: {e3}", Logger.RISK)

                logBoth('logDebug', TakePictures.logSource, f"Got third picture with shape {thirdPic.shape if isinstance(thirdPic, np.ndarray) else 'None'}", Logger.GENERAL)

                # if isinstance(image, np.ndarray):
                #     if (image.shape[1] == self.targetWidth) and (image.shape[0] == self.targetHeight):
                #         reduced_image = image
                #     else:
                #         reduced_image = cv2.resize(image, (self.targetWidth, self.targetHeight), interpolation=cv2.INTER_LANCZOS4)
                # elif isinstance(image, bytes):
                #     img_array = np.asarray(bytearray(image), dtype=np.uint8)
                #     img = cv2.imdecode(img_array, -1)
                #     if (img.shape[1] == self.targetWidth) and (img.shape[0] == self.targetHeight):
                #         reduced_image = img
                #     else:
                #         reduced_image = cv2.resize(img, (self.targetWidth, self.targetHeight), interpolation=cv2.INTER_AREA)
                answer = {timeKeyString : item[timeKeyString], FIRST_PICTURE : firstPic, SECOND_PICTURE : secondPic, THIRD_PICTURE : thirdPic, currentMachineStateKeyString : item[currentMachineStateKeyString],
                        requestIdKeyString: self._requestId(item)}
                if stalled:
                    reason = f"camera stream stalled: no new frame in {maxWait:g} s ({', '.join(stalled)} picture)"
                    logBoth('logCritical', TakePictures.logSource, f"{reason}; the check is NOT OK", Logger.PROBLEM)
                    answer[NO_PICTURE_REASON] = reason
                return answer
            except Exception as e:
                if self.debugOtherExceptions:
                    logBoth('logCritical', TakePictures.logSource,
                            f"Could not take pictures in TakePictures.{self.name} due to exception {e}",
                            Logger.CRITICAL)
        else:
            if self.debugOtherExceptions:
                logBoth('logWarning', TakePictures.logSource,
                        f"Could not take pictures in TakePictures.{self.name} as item is None",
                        Logger.RISK)
        return {timeKeyString: None, FIRST_PICTURE: None, SECOND_PICTURE: None,
                THIRD_PICTURE: None, currentMachineStateKeyString: self._requestedState(item),
                requestIdKeyString: self._requestId(item)}

    @staticmethod
    def _requestId(item):
        """The IOServer's id of the picture request (echoed in the answers), or None."""
        return item.get(requestIdKeyString) if isinstance(item, dict) else None

    @staticmethod
    def _requestedState(item) -> MachineState:
        """The picture state of a take-picture request (INVALID_STATE if the request has none)."""
        try:
            return item[currentMachineStateKeyString] if isinstance(item, dict) else MachineState.INVALID_STATE
        except Exception:
            return MachineState.INVALID_STATE

    def postWorkLoop(self):
        if self.monitorRedisQueueForStopping:
            sendStoppedResponse(self.redisConnectionForMonitoringStop, f"{ConverterScalerAndProcessor.logSource}.{self.name}")

    def launchThreadForHeartbeat(self):
        objRef = self
        def reportHeartbeat():
            cameraHeartbeatGap = CosThetaConfigurator.getInstance().getCameraConnectionStatusSleepInterval()
            while not objRef.stopped:
                # print(f"Sending CameraServer heartbeat")
                try:
                    # if objRef.camera is None or objRef.alternateCamera is None:
                    if objRef.camera is None:
                        sendHeartbeatFromCameraServerToHeartbeatServer(redisConnection=objRef.redisConnectionForHeartbeat, status=DEAD)
                    elif not objRef.camera.connected:
                        sendHeartbeatFromCameraServerToHeartbeatServer(redisConnection=objRef.redisConnectionForHeartbeat, status=DEAD)
                    elif cameraWorkers.problemsLogged(
                            log=lambda m: logBoth('logCritical', TakePictures.logSource, m, Logger.PROBLEM)):
                        # a work thread died or is stuck: the camera server cannot answer pictures
                        sendHeartbeatFromCameraServerToHeartbeatServer(redisConnection=objRef.redisConnectionForHeartbeat, status=DEAD)
                    else:
                        sendHeartbeatFromCameraServerToHeartbeatServer(redisConnection=objRef.redisConnectionForHeartbeat, status=ALIVE)
                except Exception:
                    objRef.connectToRedisForHeartbeat(forceRenew=True)
                try:
                    time.sleep(cameraHeartbeatGap)
                except Exception:
                    noteSuppressed()
        heartbeatThread = threading.Thread(name=f"Camera Heartbeat Thread", target=reportHeartbeat, args=(), daemon = True)
        heartbeatThread.start()

# *******************************TakePictures end***************************************************

# *******************************ConverterAndScaler start*******************************************

class ConverterScalerAndProcessor(GenericQueueProcessor):

    logSource = getFullyQualifiedName(__file__)

    def __init__(self, name: str, consumer: GenericQueueProcessor = None, timeout : int = 1, sleepTime : float = 0.1,
                 monitorRedisQueueForStopping : bool = True, max_size=32, **kwargs):
        GenericQueueProcessor.__init__(self, name=name, consumer=consumer, timeout=timeout, sleepTime=sleepTime,
                                       blocking=True, monitorRedisQueueForStopping=monitorRedisQueueForStopping,
                                       max_size=max_size, **kwargs)

        ConverterScalerAndProcessor.logSource = getFullyQualifiedName(__file__, __class__)
        self.redisConnection : Union[Redis,None] = None
        self.clientRedisConnected : bool = False
        self.connectToRedis()
        # reportMessageCount(self.redisConnection)
        # clearQueues(self.redisConnection)
        # reportMessageCount(self.redisConnection)

    def connectToRedis(self, forceRenew = False):
        if forceRenew:
            self.redisConnection = None
            self.clientRedisConnected = False
        if not self.clientRedisConnected:
            try:
                self.redisConnection = Redis(GenericQueueProcessor.redisHost, GenericQueueProcessor.redisPort,
                                             retry_on_timeout=True)
                self.clientRedisConnected = True
                logBoth('logInfo', ConverterScalerAndProcessor.logSource, 'Redis connection established', Logger.SUCCESS)
            except Exception:
                self.clientRedisConnected = False
                logBoth('logCritical', ConverterScalerAndProcessor.logSource, 'Could not get Redis Connection', Logger.PROBLEM)
                # pass


    def addItem(self, item: Any) -> Union[Any, None]:
        # if isinstance(item, dict) and (isinstance(item[FIRST_PICTURE], np.ndarray | None) or isinstance(item[SECOND_PICTURE], np.ndarray | None) or isinstance(item[THIRD_PICTURE], np.ndarray | None)):
        if isinstance(item, dict):
            return super().addItem(item)

    def preWorkLoop(self):
        return

    def runAssessment(self, currentMachineState: MachineState, imagesToBeProcessed: list):
        """assessPictures with a deadline (<vendor>.vision.check.timeout.sec, read at each check):
        (allok, processed image, original image, reason). reason is None when the check itself gave the verdict, else
        why the result is NOT OK without one (check timed out, worker not ready, ...).
        The check runs in the vision check worker process (models loaded there); one that times out is stopped with
        the process, which is started again. (The in-thread mode, whose stuck threads could not be stopped, was removed
        on 2026-11-15: process only.)"""
        timeout = CosThetaConfigurator.getInstance().getVisionCheckTimeout()
        from camera.VisionCheckWorker import getVisionCheckWorker
        return getVisionCheckWorker().assess(currentMachineState, imagesToBeProcessed, timeout)

    def getTargetWidthAndHeight(self, currentMachineState : MachineState):
        currentMachineStateAsInt : int = MachineState.getMachineStateAsInt(currentMachineState)
        targetWidth, targetHeight = CosThetaConfigurator.getInstance().getTargetWidthAndHeight(
            currentMachineState=currentMachineStateAsInt)
        return targetWidth, targetHeight

    def processItem(self, item : Any) -> Any :
        global resultsFromPictureAssessment, resultsStatus, resultsLock
        # printBoldGreen(f"Inside processItem() of ConverterScalerAndProcessor got item {item}")
        resetResults()
        if item is not None:

            currentMachineState = item[currentMachineStateKeyString]
            targetWidth, targetHeight = self.getTargetWidthAndHeight(currentMachineState=currentMachineState)

            firstPic = item[FIRST_PICTURE]
            secondPic = item[SECOND_PICTURE]
            thirdPic = item[THIRD_PICTURE]

            modified_FirstPic: Union[np.ndarray, None] = None
            modified_SecondPic: Union[np.ndarray, None] = None
            modified_ThirdPic: Union[np.ndarray, None] = None

            try:
                if (firstPic is not None) and isinstance(firstPic, np.ndarray):
                    if (firstPic.shape[1] == targetWidth) and (firstPic.shape[0] == targetHeight):
                        modified_FirstPic = firstPic
                    else:
                        modified_FirstPic = cv2.resize(firstPic, (targetWidth, targetHeight),
                                                       interpolation=cv2.INTER_LANCZOS4)
                elif (firstPic is not None) and isinstance(firstPic, bytes):
                    img_array = np.asarray(bytearray(firstPic), dtype=np.uint8)
                    img = cv2.imdecode(img_array, -1)
                    if (img.shape[1] == targetWidth) and (img.shape[0] == targetHeight):
                        modified_FirstPic = img
                    else:
                        modified_FirstPic = cv2.resize(img, (targetWidth, targetHeight), interpolation=cv2.INTER_AREA)
            except Exception as e:
                modified_FirstPic = None
                if self.debugOtherExceptions:
                    logBoth('logWarning', ConverterScalerAndProcessor.logSource,
                            f"Could not get the first pic due to exception {e}",
                            Logger.RISK)

            try:
                if (secondPic is not None) and isinstance(secondPic, np.ndarray):
                    if (secondPic.shape[1] == targetWidth) and (secondPic.shape[0] == targetHeight):
                        modified_SecondPic = secondPic
                    else:
                        modified_SecondPic = cv2.resize(secondPic, (targetWidth, targetHeight),
                                                        interpolation=cv2.INTER_LANCZOS4)
                elif (secondPic is not None) and isinstance(secondPic, bytes):
                    img_array = np.asarray(bytearray(secondPic), dtype=np.uint8)
                    img = cv2.imdecode(img_array, -1)
                    if (img.shape[1] == targetWidth) and (img.shape[0] == targetHeight):
                        modified_SecondPic = img
                    else:
                        modified_SecondPic = cv2.resize(img, (targetWidth, targetHeight), interpolation=cv2.INTER_AREA)
            except Exception as e:
                modified_SecondPic = None
                if self.debugOtherExceptions:
                    logBoth('logWarning', ConverterScalerAndProcessor.logSource,
                            f"Could not get the second pic due to exception {e}",
                            Logger.RISK)

            try:
                if (thirdPic is not None) and isinstance(thirdPic, np.ndarray):
                    if (thirdPic.shape[1] == targetWidth) and (thirdPic.shape[0] == targetHeight):
                        modified_ThirdPic = thirdPic
                    else:
                        modified_ThirdPic = cv2.resize(thirdPic, (targetWidth, targetHeight),
                                                       interpolation=cv2.INTER_LANCZOS4)
                elif (thirdPic is not None) and isinstance(thirdPic, bytes):
                    img_array = np.asarray(bytearray(thirdPic), dtype=np.uint8)
                    img = cv2.imdecode(img_array, -1)
                    if (img.shape[1] == targetWidth) and (img.shape[0] == targetHeight):
                        modified_ThirdPic = img
                    else:
                        modified_ThirdPic = cv2.resize(img, (targetWidth, targetHeight), interpolation=cv2.INTER_AREA)
            except Exception as e:
                modified_ThirdPic = None
                if self.debugOtherExceptions:
                    logBoth('logWarning', ConverterScalerAndProcessor.logSource,
                            f"Could not get the third pic due to exception {e}",
                            Logger.RISK)

            imagesToBeProcessed = [
                modified_FirstPic, modified_FirstPic,
                modified_SecondPic, modified_SecondPic,
                modified_ThirdPic, modified_ThirdPic
            ]

            requestId = item.get(requestIdKeyString) if isinstance(item, dict) else None
            noPictureReason = item.get(NO_PICTURE_REASON)
            if noPictureReason is None and all(pic is None for pic in imagesToBeProcessed):
                noPictureReason = "no picture from the camera"
            if noPictureReason:
                # no (new) picture: NOT OK without running a check on it (a check on an old frame could pass)
                allok, chosenProcessedImage, chosenOriginalImage, reason = False, None, None, noPictureReason
            else:
                if currentMachineState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE:
                    ImageProcessor.prepareForKnuckleCheck()
                allok, chosenProcessedImage, chosenOriginalImage, reason = self.runAssessment(currentMachineState,
                                                                                              imagesToBeProcessed)
                ImageProcessor.persistState()        # a restarted camera server carries on from here
            if allok:
                logBoth('logInfo', ConverterScalerAndProcessor.logSource, f"Final result: OK", Logger.SUCCESS)
            else:
                logBoth('logInfo', ConverterScalerAndProcessor.logSource,
                        f"Final result: FAILED{f' ({reason})' if reason else ''}", Logger.ISSUE)
                sendAlarmSignalToHeartbeatServer(redisConnection=self.redisConnection)
                if reason:
                    chosenProcessedImage = _withReason(chosenProcessedImage if chosenProcessedImage is not None
                                                       else imagesToBeProcessed[0], reason)
                    setFrontendNotice(self.redisConnection, "check", f"Picture NOT OK: {reason}", ttlSecs=15)

            sendDataFromCameraServerToFEServer(redisConnection=self.redisConnection, originalImage=chosenOriginalImage,
                                               processedImage=chosenProcessedImage, result=ok if allok else notok,
                                               currentMachineState=currentMachineState, requestId=requestId,
                                               reason=reason)
            sendDataFromCameraServerToIOServer(redisConnection=self.redisConnection, result=ok if allok else notok,
                                               currentMachineState=currentMachineState, requestId=requestId)
        else:
            if self.debugOtherExceptions:
                logBoth('logWarning', ConverterScalerAndProcessor.logSource,
                        f"Did not reduce {'None startingImage' if item is None else type(item)} in ConverterScalerAndProcessor.{self.name}",
                        Logger.RISK)
            requestId = item.get(requestIdKeyString) if isinstance(item, dict) else None
            sendDataFromCameraServerToFEServer(redisConnection=self.redisConnection, originalImage=None,
                                               processedImage=None, result=notok,
                                               currentMachineState=MachineState.INVALID_STATE, requestId=requestId)
            sendDataFromCameraServerToIOServer(redisConnection=self.redisConnection, result=notok,
                                               currentMachineState=MachineState.INVALID_STATE, requestId=requestId)

        return None

    def postWorkLoop(self):
        # printBoldBlue(f"Finished thread {self.name}")
        if self.monitorRedisQueueForStopping:
            sendStoppedResponse(self.redisConnectionForMonitoringStop, f"{ConverterScalerAndProcessor.logSource}.{self.name}")

# *******************************ConverterAndScaler end*********************************************

def assessPictures(currentMachineState: MachineState, imagesToBeProcessed: list) -> Tuple[bool, Any, Any]:
    """The vision check of one picture state on its (resized) pictures: (allok, processed image, original image).
    Uses and updates the cycle's state kept in ImageProcessor (earlier pictures, bearing ring, QR code). Runs in the
    camera server's assessment thread or in the vision check worker process (camera/VisionCheckWorker.py)."""
    resetResults()
    allok = False
    chosenProcessedImage = None
    chosenOriginalImage = None
    methodToBeExecuted = None
    try:
        methodToBeExecuted = ImageProcessor.methodMapping[currentMachineState]
    except Exception as e:
        logBoth('logWarning', ConverterScalerAndProcessor.logSource,
                f"Exception getting methodMapping for state {currentMachineState}: {e}",
                Logger.RISK)

    # Set flags based on currentMachineState directly (not dependent on methodMapping)
    # This ensures batch processing works even when methodMapping entry is None
    is_hub_and_bearing = (
                currentMachineState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING)
    is_nut_and_washer = (
                currentMachineState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER)
    is_nocap_bunk = (
                currentMachineState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS)
    is_bunk = (currentMachineState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS)
    is_no_bunk = (currentMachineState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK)
    is_top_bearing = (
                currentMachineState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING)
    # knuckle by segmentation (Machine 2): one MobileSAM run on one picture instead of three pictures and a vote
    is_knuckle_segmentation = (currentMachineState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE
                               and str(CheckKnuckle.METHOD).strip().lower() == "segmentation")
    # every check looks where this cycle's frame says (the knuckle check's frame), or where the properties say
    VisionFrame.applyToChecks(ImageProcessor.visionFrame)

    # Defensive defaults — overwritten by all branches below
    allok = False
    chosenProcessedImage = None
    chosenOriginalImage = None

    # Enter this block if we have a method to execute OR if it's a batch processing state
    if methodToBeExecuted is not None or is_hub_and_bearing or is_nut_and_washer or is_nocap_bunk or is_bunk or is_no_bunk or is_top_bearing:
        # (the knuckle check's start of a new cycle - QR code, earlier pictures, bearing ring - is handled before the
        # assessment, in ImageProcessor.prepareForKnuckleCheck, so that a knuckle retake keeps the cycle's QR code)

        try:
            if is_knuckle_segmentation:
                test_image = imagesToBeProcessed[0]
                ImageProcessor.setVisionFrame(None)
                try:
                    ImageProcessor.currentPictures[knucklePictureKeyString] = test_image
                    chosenProcessedImage, allok, frame = CheckKnuckle.checkKnuckleBySegmentation(
                        test_image, ImageProcessor.currentComponentQRCodeValue)
                    chosenOriginalImage = test_image
                    # the later checks of this cycle look where this frame says (kept only with OK)
                    ImageProcessor.setVisionFrame(frame if allok else None)
                except Exception as e:
                    logBoth('logWarning', ConverterScalerAndProcessor.logSource,
                            f"CheckKnuckle (segmentation) exception: {e}", Logger.ISSUE)
                    allok = False
                    chosenProcessedImage = test_image
                    chosenOriginalImage = test_image

            elif is_hub_and_bearing:
                # NEW: Use batch processing instead of threading
                logBoth('logDebug', ConverterScalerAndProcessor.logSource, "Using batch processing for CheckHubAndBottomBearing", Logger.GENERAL)

                try:
                    # Use the first image (they're all duplicates in imagesToBeProcessed)
                    test_image = imagesToBeProcessed[0]

                    ImageProcessor.currentPictures[hubAndBottomBearingPictureKeyString] = test_image

                    chosenProcessedImage, allok, winning_gamma = \
                        CheckHubAndBottomBearing.checkHubAndBottomBearing(
                            test_image,
                            ImageProcessor.currentComponentQRCodeValue,
                            use_smart_gamma=True  # Enable smart ordering
                        )

                    chosenOriginalImage = test_image

                    if allok:
                        logBoth('logInfo', ConverterScalerAndProcessor.logSource,
                                f"CheckHubAndBottomBearing PASSED with gamma={winning_gamma}", Logger.SUCCESS)
                    else:
                        logBoth('logInfo', ConverterScalerAndProcessor.logSource,
                                "CheckHubAndBottomBearing FAILED", Logger.ISSUE)

                except Exception as e:
                    logBoth('logWarning', ConverterScalerAndProcessor.logSource,
                            f"CheckHubAndBottomBearing exception: {e}", Logger.ISSUE)
                    import traceback
                    traceback.print_exc()
                    allok = False
                    chosenProcessedImage = imagesToBeProcessed[0]
                    chosenOriginalImage = imagesToBeProcessed[0]

            elif is_nut_and_washer:
                # Use batch processing for CheckNutAndPlateWasher
                logBoth('logDebug', ConverterScalerAndProcessor.logSource, "Using batch processing for CheckNutAndPlateWasher", Logger.GENERAL)

                try:
                    test_image = imagesToBeProcessed[0]  # Just need one image

                    ImageProcessor.currentPictures[nutAndPlateWasherPictureKeyString] = test_image

                    chosenProcessedImage, allok = CheckNutAndPlateWasher.checkNutAndPlateWasher(
                        test_image,
                        ImageProcessor.currentPictures,
                        ImageProcessor.currentComponentQRCodeValue,
                        bearing_geometry=ImageProcessor.getBearingProfile()
                    ) # Note: gamma not passed, relies on default

                    chosenOriginalImage = test_image

                    if allok:
                        logBoth('logInfo', ConverterScalerAndProcessor.logSource,
                                "CheckNutAndPlateWasher PASSED", Logger.SUCCESS)
                    else:
                        logBoth('logInfo', ConverterScalerAndProcessor.logSource,
                                "CheckNutAndPlateWasher FAILED", Logger.ISSUE)

                except Exception as e:
                    logBoth('logWarning', ConverterScalerAndProcessor.logSource,
                            f"CheckNutAndPlateWasher exception: {e}", Logger.ISSUE)
                    import traceback
                    traceback.print_exc()
                    allok = False
                    chosenProcessedImage = imagesToBeProcessed[0]
                    chosenOriginalImage = imagesToBeProcessed[0]

            elif is_nocap_bunk:
                # NEW: Use batch processing for CheckNoCapBunk
                logBoth('logDebug', ConverterScalerAndProcessor.logSource, "Using batch processing for CheckNoCapBunk", Logger.GENERAL)

                try:
                    # Use the first image (they're all duplicates in imagesToBeProcessed)
                    test_image = imagesToBeProcessed[0]

                    ImageProcessor.currentPictures[componentPressBunkPictureKeyString] = test_image

                    chosenProcessedImage, allok, winning_gamma = CheckNoCapBunk.checkNoCapBunk(
                        test_image,
                        ImageProcessor.currentComponentQRCodeValue,
                        bearing_geometry=ImageProcessor.getBearingProfile()
                    )

                    chosenOriginalImage = test_image

                    if allok:
                        logBoth('logInfo', ConverterScalerAndProcessor.logSource,
                                f"CheckNoCapBunk PASSED with gamma={winning_gamma}", Logger.SUCCESS)
                    else:
                        logBoth('logInfo', ConverterScalerAndProcessor.logSource,
                                "CheckNoCapBunk FAILED", Logger.ISSUE)

                except Exception as e:
                    logBoth('logWarning', ConverterScalerAndProcessor.logSource,
                            f"CheckNoCapBunk exception: {e}", Logger.ISSUE)
                    import traceback
                    traceback.print_exc()
                    allok = False
                    chosenProcessedImage = imagesToBeProcessed[0]
                    chosenOriginalImage = imagesToBeProcessed[0]

            elif is_bunk:
                # NEW: Use batch processing for CheckBunk
                logBoth('logDebug', ConverterScalerAndProcessor.logSource, "Using batch processing for CheckBunk", Logger.GENERAL)

                try:
                    # Use the first image (they're all duplicates in imagesToBeProcessed)
                    test_image = imagesToBeProcessed[0]

                    ImageProcessor.currentPictures[capPressBunkPictureKeyString] = test_image

                    chosenProcessedImage, allok, winning_gamma = CheckBunk.checkBunk(
                        test_image,
                        ImageProcessor.currentComponentQRCodeValue,
                        bearing_geometry=ImageProcessor.getBearingProfile()
                    )

                    chosenOriginalImage = test_image

                    if allok:
                        logBoth('logInfo', ConverterScalerAndProcessor.logSource,
                                f"CheckBunk PASSED with gamma={winning_gamma}", Logger.SUCCESS)
                    else:
                        logBoth('logInfo', ConverterScalerAndProcessor.logSource,
                                "CheckBunk FAILED", Logger.ISSUE)

                except Exception as e:
                    logBoth('logWarning', ConverterScalerAndProcessor.logSource,
                            f"CheckBunk exception: {e}", Logger.ISSUE)
                    import traceback
                    traceback.print_exc()
                    allok = False
                    chosenProcessedImage = imagesToBeProcessed[0]
                    chosenOriginalImage = imagesToBeProcessed[0]

            elif is_no_bunk:
                # Use batch processing for CheckNoBunk (avoids ThreadPoolExecutor timeout issues)
                logBoth('logDebug', ConverterScalerAndProcessor.logSource, "Using batch processing for CheckNoBunk", Logger.GENERAL)

                try:
                    test_image = imagesToBeProcessed[0]

                    ImageProcessor.currentPictures[noBunkPictureKeyString] = test_image

                    chosenProcessedImage, allok = CheckNoBunk.checkNoBunk(
                        anImage=test_image,
                        currentPictures=ImageProcessor.currentPictures,
                        componentQRCode=ImageProcessor.currentComponentQRCodeValue
                    )

                    chosenOriginalImage = test_image

                    if allok:
                        logBoth('logInfo', ConverterScalerAndProcessor.logSource,
                                "CheckNoBunk PASSED", Logger.SUCCESS)
                    else:
                        logBoth('logInfo', ConverterScalerAndProcessor.logSource,
                                "CheckNoBunk FAILED", Logger.ISSUE)

                except Exception as e:
                    logBoth('logWarning', ConverterScalerAndProcessor.logSource,
                            f"CheckNoBunk exception: {e}", Logger.ISSUE)
                    import traceback
                    traceback.print_exc()
                    allok = False
                    chosenProcessedImage = imagesToBeProcessed[0]
                    chosenOriginalImage = imagesToBeProcessed[0]

            elif is_top_bearing:
                logBoth('logDebug', ConverterScalerAndProcessor.logSource, "Using batch processing for CheckTopBearing", Logger.GENERAL)
                try:
                    test_image = imagesToBeProcessed[0]

                    ImageProcessor.currentPictures[topBearingPictureKeyString] = test_image

                    annotated_img, allok, geometry = CheckTopBearing.checkTopBearing(
                        anImage=test_image,
                        currentPictures=ImageProcessor.currentPictures,
                        componentQRCode=ImageProcessor.currentComponentQRCodeValue
                    )

                    chosenProcessedImage = annotated_img if annotated_img is not None else test_image
                    chosenOriginalImage = test_image

                    # The ring found (with its edge profile) is kept for the nut / washer check; a NOT OK
                    # result (also a retake) removes any ring kept earlier in the cycle
                    ImageProcessor.saveBearingProfile(geometry if allok else None)
                    if allok:
                        logBoth('logInfo', ConverterScalerAndProcessor.logSource,
                                f"CheckTopBearing PASSED, ring centre {geometry['center']}, radii "
                                f"{geometry['inner_radius']}-{geometry['outer_radius']}, outer diameter "
                                f"{geometry.get('outer_diameter')}, width {geometry.get('width')}", Logger.SUCCESS)
                    else:
                        logBoth('logInfo', ConverterScalerAndProcessor.logSource,
                                "CheckTopBearing FAILED", Logger.ISSUE)

                except Exception as e:
                    logBoth('logWarning', ConverterScalerAndProcessor.logSource,
                            f"CheckTopBearing exception: {e}", Logger.ISSUE)
                    ImageProcessor.saveBearingProfile(None)
                    import traceback
                    traceback.print_exc()
                    allok = False
                    chosenProcessedImage = imagesToBeProcessed[0]
                    chosenOriginalImage = imagesToBeProcessed[0]

            elif currentMachineState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP:
                # Cap (user, 2026-11-15): steps 1-3 (which cap, right cap, brightening; no MobileSAM) on each of the
                # three pictures; with 2 of 3 passing, the whole check including the seating step (MobileSAM) once, on
                # the first picture that passed - its verdict is the result. One MobileSAM run per cap check (it was
                # six in parallel threads, which did not finish in the 5 s vote time without a GPU).
                allok, chosenProcessedImage, chosenOriginalImage = _assessCap(imagesToBeProcessed)

            else:
                # Original threading logic for other checks (non-hub-bearing)
                # not "with ThreadPoolExecutor(...)": leaving that block waits for every thread, so a stuck one would
                # hold the pipeline whatever the timeouts below; the executor is shut down without waiting instead
                executor = ThreadPoolExecutor(max_workers=6)
                try:
                    futures = [executor.submit(methodToBeExecuted.__func__, ImageProcessor, anImage, index,
                                               gamma=index + 1)
                               for index, anImage in enumerate(imagesToBeProcessed)]

                    wait(futures, timeout=5.0)

                    for index, future in enumerate(futures):
                        if not future.done():
                            logBoth('logWarning', ConverterScalerAndProcessor.logSource,
                                    f"Picture {index // 2 + 1} not assessed in time: counted as NOT OK", Logger.ISSUE)
                            addAResult(anImage=imagesToBeProcessed[index], aResult=False, index=index // 2)
                            continue
                        processedImage, evaluation = future.result(timeout=0)
                        if processedImage is not None:
                            logBoth('logDebug', ConverterScalerAndProcessor.logSource,
                                    f"Shape of processedImage = {processedImage.shape}, evaluation result is {evaluation}",
                                    Logger.GENERAL)
                        else:
                            logBoth('logWarning', ConverterScalerAndProcessor.logSource,
                                    "processedImage is None", Logger.ISSUE)
                        addAResult(anImage=processedImage, aResult=evaluation, index=index // 2)
                finally:
                    executor.shutdown(wait=False, cancel_futures=True)

        except Exception as e:
            logBoth('logCritical', ConverterScalerAndProcessor.logSource,
                    f'Could not complete ThreadPoolExecutor actions due to {e} in CameraProcessorServer',
                    Logger.PROBLEM)
            pass

    is_cap = currentMachineState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP
    if not is_cap and not is_knuckle_segmentation and not is_hub_and_bearing and not is_bunk and not is_nocap_bunk and not is_nut_and_washer and not is_no_bunk and not is_top_bearing:
        allok = False
        # Original voting mechanism
        okcount: int = 0
        for result in resultsStatus:
            if result:
                okcount += 1

        chosenProcessedImage = None
        chosenOriginalImage = None

        allok = (okcount >= 2)

        for index, aPair in enumerate(resultsFromPictureAssessment):
            if aPair[0] == allok:
                chosenProcessedImage = aPair[1]
                chosenOriginalImage = imagesToBeProcessed[index * 2]  # Map back to original (0→0, 1→2, 2→4)
                break
    return allok, chosenProcessedImage, chosenOriginalImage


def _assessCap(imagesToBeProcessed: list):
    """The cap check on the camera server's three pictures (each sent twice: index 0, 2, 4): steps 1-3 on each; when at
    least 2 of 3 pass, the whole check (with the seating step) once on the first that passed, and its verdict decides.
    Returns (allok, processed image, original image)."""
    pictures = [p for p in imagesToBeProcessed[0::2] if p is not None] or [p for p in imagesToBeProcessed if p is not None]
    if not pictures:
        return False, None, None
    IP = ImageProcessor
    if IP.saveTrialPictures:                  # as the 2-of-3 checks do (save.all.pictures.because.its.in.trial.mode)
        try:
            savingDir = f"{IP.mainDirForSavingPictures}Cap/"
            for index, picture in enumerate(pictures):
                saveFileWithFullPath(picture, f"{savingDir}{getFileNameForSaving(useExtension=False, useNanoSec=True)}-{index * 2}.png")
        except Exception as e:
            logBoth('logWarning', logSource, f"Could not save the trial cap pictures: {e}", Logger.RISK)
    IP.currentPictures[capPictureKeyString] = pictures[0]
    passed, firstFailed = [], None
    for index, picture in enumerate(pictures):
        processed, ok = CheckCap.checkCap(anImage=picture, currentPictures=IP.currentPictures,
                                          componentQRCode=IP.currentComponentQRCodeValue, runSeating=False)
        if ok:
            passed.append(index)
        elif firstFailed is None:
            firstFailed = (processed, picture)
    needed = 2 if len(pictures) >= 3 else len(pictures)
    logBoth('logInfo', logSource, f"CheckCap: steps 1-3 passed on {len(passed)} of {len(pictures)} pictures "
            f"({needed} needed)", Logger.SUCCESS if len(passed) >= needed else Logger.ISSUE)
    if len(passed) < needed:
        processed, picture = firstFailed if firstFailed is not None else (pictures[0], pictures[0])
        return False, processed if processed is not None else picture, picture
    picture = pictures[passed[0]]
    IP.currentPictures[capPictureKeyString] = picture
    processed, ok = CheckCap.checkCap(anImage=picture, currentPictures=IP.currentPictures,
                                      componentQRCode=IP.currentComponentQRCodeValue, runSeating=True)
    logBoth('logInfo', logSource, f"CheckCap: whole check (with seating) on picture {passed[0] + 1}: "
            f"{'OK' if ok else 'NOT OK'}", Logger.SUCCESS if ok else Logger.ISSUE)
    return bool(ok), processed if processed is not None else picture, picture


def _withReason(anImage, reason: str):
    """A copy of the picture marked NOT OK for the frontend: a red border, no text (user, 2026-11-15: no values or
    words on the pictures). The reason goes to the log here, and to the frontend as the answer's reason / notice."""
    logBoth('logError', logSource, f"Picture NOT OK: {reason}", Logger.ISSUE)
    try:
        if not isinstance(anImage, np.ndarray):
            anImage = np.full((720, 1280, 3), 255, dtype=np.uint8)
        marked = anImage.copy()
        h, w = marked.shape[:2]
        cv2.rectangle(marked, (4, 4), (w - 5, h - 5), (0, 0, 255), 8)
        return marked
    except Exception:
        return anImage


# *******************************ImageProcessor begin***********************************************

class ImageProcessor():

    logSource = getFullyQualifiedName(__file__)

    font = cv2.FONT_HERSHEY_SIMPLEX
    fontScale = 1
    greencolor = (0, 255, 0)
    redcolor = (0, 0, 255)
    thickness = 2

    saveTrialPictures : bool = CosThetaConfigurator.getInstance().getSaveAllPicturesBecauseItsInTrialMode()

    mainDirForSavingPictures : str = CosThetaConfigurator.getInstance().getMainPictureSavingDirForModeTrial()

    currentPictures : Dict[str, np.ndarray | None] = {knucklePictureKeyString : None,
                                                      hubAndBottomBearingPictureKeyString : None,
                                                      topBearingPictureKeyString : None,
                                                      nutAndPlateWasherPictureKeyString : None,
                                                      splitPinAndWasherPictureKeyString : None,
                                                      capPictureKeyString : None,
                                                      componentPressBunkPictureKeyString : None,
                                                      noBunkPictureKeyString : None,
                                                      capPressBunkPictureKeyString : None}

    currentComponentQRCodeValue: str | None = None
    currentModelName: str | None = None
    currentSide : str | None = None
    bearingCentreAndRadii: Dict[str, Any] | None = None      # top bearing ring and edge profile, for the nut / washer check
    bearingProfileQRCode: str | None = None                  # component it was kept for
    # Where this cycle's later checks look (camera/VisionFrame.py): built by the knuckle check (segmentation method)
    # from the spindle, the bosses' pins and the lower arm; None = the centres in the properties file. Dropped at the
    # end of a cycle (completed or aborted), at a new cycle's knuckle check and at a knuckle retake.
    visionFrame: Dict[str, Any] | None = None

    # this lock is needed because 3 threads simultaneously call this function, and only 1 message is in the queue
    getQRCodeLock = threading.Lock()
    # checkKnuckle fetches the QR code itself when none is set (standalone use). The camera server fetches it before
    # the knuckle check (prepareForKnuckleCheck) and turns this off, so a check never waits on the QR queue.
    fetchQRCodeInChecks: bool = True
    _STATE_ATTRIBUTES = ("currentComponentQRCodeValue", "currentModelName", "currentSide", "bearingCentreAndRadii",
                         "bearingProfileQRCode", "visionFrame")

    @classmethod
    def snapshotState(cls) -> Dict[str, Any]:
        """The cycle's state the checks use and update: earlier pictures, QR code / model / side, bearing ring."""
        state = {name: getattr(ImageProcessor, name) for name in ImageProcessor._STATE_ATTRIBUTES}
        state["currentPictures"] = dict(ImageProcessor.currentPictures)
        return state

    @classmethod
    def restoreState(cls, state: Dict[str, Any]) -> None:
        for name in ImageProcessor._STATE_ATTRIBUTES:
            if name in state:
                setattr(ImageProcessor, name, state[name])
        if "currentPictures" in state:
            ImageProcessor.currentPictures.clear()
            ImageProcessor.currentPictures.update(state["currentPictures"])
        VisionFrame.applyToChecks(ImageProcessor.visionFrame)

    @classmethod
    def applyStateChanges(cls, changes: Dict[str, Any]) -> None:
        """State changed by a check in the worker process: attributes, and the pictures it set."""
        for name in ImageProcessor._STATE_ATTRIBUTES:
            if name in changes:
                setattr(ImageProcessor, name, changes[name])
        ImageProcessor.currentPictures.update(changes.get("currentPictures", {}))
        VisionFrame.applyToChecks(ImageProcessor.visionFrame)

    @classmethod
    def setVisionFrame(cls, frame: Dict[str, Any] | None) -> None:
        """This cycle's frame (None: drop it); the checks' centres follow at once."""
        ImageProcessor.visionFrame = frame
        VisionFrame.applyToChecks(frame)

    # The cycle's state kept in a file (camera/CameraStateFile.py), so that a camera server restarted during a cycle
    # carries on with it (user, 2026-11-15). Written after every check and at the end of every cycle; read only when
    # the camera server is started with the file's path (MainProgram's supervisor restart).
    stateFile: str | None = None

    @classmethod
    def persistState(cls) -> bool:
        if not ImageProcessor.stateFile:
            return False
        from camera import CameraStateFile
        attributes = {name: getattr(ImageProcessor, name) for name in ImageProcessor._STATE_ATTRIBUTES}
        written = CameraStateFile.save(ImageProcessor.stateFile, attributes, dict(ImageProcessor.currentPictures))
        if not written:
            logBoth('logWarning', ImageProcessor.logSource, f"Could not write the cycle state to {ImageProcessor.stateFile}",
                    Logger.RISK)
        return written

    @classmethod
    def loadPersistedState(cls, path: str | None) -> bool:
        """At a restart: the cycle's state from the file (QR code, model, side, bearing ring, knuckle frame, earlier
        pictures); True if one was taken."""
        from camera import CameraStateFile
        attributes, pictures, why = CameraStateFile.load(path, CosThetaConfigurator.getInstance().getCameraStateFileMaxAge())
        if attributes is None:
            logBoth('logInfo', ImageProcessor.logSource, f"Camera server restarted: no cycle state taken ({why})",
                    Logger.GENERAL)
            return False
        state = {name: attributes.get(name) for name in ImageProcessor._STATE_ATTRIBUTES}
        state["currentPictures"] = {key: pictures.get(key) for key in ImageProcessor.currentPictures}
        ImageProcessor.restoreState(state)          # applies the knuckle frame to the checks too
        kept = [key for key, picture in state["currentPictures"].items() if picture is not None]
        logBoth('logCritical', ImageProcessor.logSource,
                f"Camera server restarted: cycle state taken from {why} - QR code {ImageProcessor.currentComponentQRCodeValue}, "
                f"pictures {kept}, bearing ring {'yes' if ImageProcessor.bearingCentreAndRadii else 'no'}, knuckle frame "
                f"{'yes' if ImageProcessor.visionFrame else 'no'}", Logger.SUCCESS)
        return True

    @classmethod
    def endOfCycle(cls) -> None:
        """The IOServer's end of cycle (completed or aborted): nothing of the cycle is kept."""
        ImageProcessor.currentComponentQRCodeValue = None
        ImageProcessor.currentModelName = None
        ImageProcessor.currentSide = None
        ImageProcessor.clearCurrentPictures()
        ImageProcessor.clearBearingCentreAndRadii()
        ImageProcessor.setVisionFrame(None)
        ImageProcessor.persistState()

    @classmethod
    def prepareForKnuckleCheck(cls) -> None:
        """Before a knuckle check. A QR code sent by the QR code server since the last knuckle check means a new
        cycle: the previous cycle's QR code, pictures and bearing ring are dropped and the new QR code is used. No new
        QR code: a retake of the knuckle in the same cycle - the cycle's QR code is kept (before 2026-11-06 it was
        dropped at every knuckle check, so a retake after a NOT OK waited 5 s per picture for a QR code that had
        already been read, and was NOT OK). No QR code at all: wait up to 5 s for one, as before."""
        try:
            ImageProcessor.connectToRedis()
            newCode, received = None, False
            for _ in range(20):             # the newest QR code queued (a re-scan can leave an earlier one)
                code, got = readComponentQRCodeInCameraServerFromQRCodeServer(ImageProcessor.redisConnectionQ, block=50)
                if not got:
                    break
                newCode, received = code, True
        except Exception as e:
            newCode, received = None, False
            logBoth('logWarning', ImageProcessor.logSource, f"Could not read the QR code queue: {e}", Logger.RISK)
        if received:
            ImageProcessor.endOfCycle()
            ImageProcessor.setCurrentComponentName(qrCodeValue=newCode)
            logBoth('logInfo', ImageProcessor.logSource, f"New cycle at the knuckle check: QR code {newCode}",
                    Logger.SUCCESS)
        elif ImageProcessor.currentComponentQRCodeValue is not None:
            ImageProcessor.currentPictures[knucklePictureKeyString] = None
            ImageProcessor.setVisionFrame(None)      # the retake builds it again
            logBoth('logInfo', ImageProcessor.logSource,
                    f"Knuckle retake: QR code {ImageProcessor.currentComponentQRCodeValue} kept", Logger.SUCCESS)
        else:
            ImageProcessor.endOfCycle()
            with ImageProcessor.getQRCodeLock:
                ImageProcessor.fetchAndPopulateCurrentComponentQRCode()

    redisConnectionQ = None
    clientRedisConnectedQ = False

    @classmethod
    def connectToRedis(cls, forceRenew = False):
        if forceRenew:
            ImageProcessor.redisConnectionQ = None
            ImageProcessor.clientRedisConnectedQ = False
        if not ImageProcessor.clientRedisConnectedQ:
            try:
                ImageProcessor.redisConnectionQ = Redis(GenericQueueProcessor.redisHost, GenericQueueProcessor.redisPort,
                                              retry_on_timeout=True)
                ImageProcessor.clientRedisConnectedQ = True
                logBoth('logInfo', ImageProcessor.logSource, 'Redis connection established', Logger.SUCCESS)
            except Exception:
                ImageProcessor.clientRedisConnectedQ = False
                logBoth('logCritical', ImageProcessor.logSource, 'Could not get Redis Connection', Logger.PROBLEM)
                # pass


    @classmethod
    def getCurrentPictures(cls):
        return ImageProcessor.currentPictures

    @classmethod
    def clearCurrentPictures(cls):
        try:
            ImageProcessor.currentPictures[knucklePictureKeyString] = None
            ImageProcessor.currentPictures[hubAndBottomBearingPictureKeyString] = None
            ImageProcessor.currentPictures[topBearingPictureKeyString] = None
            ImageProcessor.currentPictures[nutAndPlateWasherPictureKeyString] = None
            ImageProcessor.currentPictures[splitPinAndWasherPictureKeyString] = None
            ImageProcessor.currentPictures[capPictureKeyString] = None
            ImageProcessor.currentPictures[componentPressBunkPictureKeyString] = None
            ImageProcessor.currentPictures[noBunkPictureKeyString] = None
            ImageProcessor.currentPictures[capPressBunkPictureKeyString] = None
        except Exception as e:
            logBoth('logWarning', ImageProcessor.logSource,
                    f"Exception in clearCurrentPictures: {e}", Logger.RISK)

    @classmethod
    def clearBearingCentreAndRadii(cls):
        try:
            ImageProcessor.bearingCentreAndRadii = None
            ImageProcessor.bearingProfileQRCode = None
        except Exception as e:
            logBoth('logWarning', ImageProcessor.logSource,
                    f"Exception in clearBearingCentreAndRadii: {e}", Logger.RISK)

    @classmethod
    def saveBearingProfile(cls, geometry: Dict[str, Any] | None):
        """Keep the top bearing ring (centre, radii, outer diameter, width, edge profile) of the current component for
        the nut / washer check; None drops it. Dropped also at the end of the cycle (end-of-cycle message from the
        IOServer: completed or aborted) and when the next cycle's knuckle check starts."""
        ImageProcessor.bearingCentreAndRadii = geometry
        ImageProcessor.bearingProfileQRCode = None if geometry is None else ImageProcessor.currentComponentQRCodeValue

    @classmethod
    def getBearingProfile(cls) -> Dict[str, Any] | None:
        """The top bearing ring kept in this cycle, or None (none kept, dropped at the end of a cycle or on an abort,
        or kept for another component)."""
        profile, savedFor = ImageProcessor.bearingCentreAndRadii, ImageProcessor.bearingProfileQRCode
        current = ImageProcessor.currentComponentQRCodeValue
        if profile is not None and savedFor is not None and current is not None and savedFor != current:
            profile = None
        if profile is None:
            logBoth('logWarning', ImageProcessor.logSource,
                    "No top bearing profile kept for the current component", Logger.RISK)
        return profile

    @classmethod
    def setCurrentComponentName(cls, qrCodeValue : str | None = None):
        ImageProcessor.currentComponentQRCodeValue = qrCodeValue
        model, side, tonnage  = getModel_LHSRHS_AndTonnage(qrCodeValue)
        ImageProcessor.currentModelName = model
        ImageProcessor.currentSide = side

    @classmethod
    def fetchAndPopulateCurrentComponentQRCode(cls):
        try:
            ImageProcessor.connectToRedis()
        except Exception as e:
            logBoth('logWarning', ImageProcessor.logSource,
                    f"Could not connect to Redis in fetchAndPopulateCurrentComponentQRCode: {e}",
                    Logger.RISK)
            return

        received: bool = False
        startTime = time.time()
        maxPeriodToSeekComponentName = 5  # secs
        while (not received) and ((time.time() - startTime) < maxPeriodToSeekComponentName) and (
                ImageProcessor.currentComponentQRCodeValue is None):
            try:
                comp_QRCode, received = readComponentQRCodeInCameraServerFromQRCodeServer(
                    ImageProcessor.redisConnectionQ)
                if received:
                    ImageProcessor.setCurrentComponentName(qrCodeValue=comp_QRCode)
            except Exception as e:
                logBoth('logWarning', ImageProcessor.logSource,
                        f"Exception reading QR code: {e}", Logger.RISK)
            time.sleep(0.15)

    @classmethod
    def checkKnuckle(cls, anImage: np.ndarray, index: int = -1, gamma : float = 2.0) -> Tuple[np.ndarray, bool]:

        try:
            logBoth('logDebug', ImageProcessor.logSource, "Entered checkKnuckle() of class ImageProcessor", Logger.GENERAL)
            if ImageProcessor.fetchQRCodeInChecks:
                with ImageProcessor.getQRCodeLock:
                    if ImageProcessor.currentComponentQRCodeValue is None:
                        ImageProcessor.fetchAndPopulateCurrentComponentQRCode()

            if ImageProcessor.saveTrialPictures:
                currentMethodName = f"{inspect.currentframe().f_code.co_name}"
                currentMethodName = currentMethodName.replace("check", "")
                savingDir = f"{ImageProcessor.mainDirForSavingPictures}{currentMethodName}/"
                fileNameForSaving = f"{savingDir}{getFileNameForSaving(useExtension=False, useNanoSec=True)}"
                if index >= 0:
                    fileNameForSaving = f"{fileNameForSaving}-{index}.png"
                else:
                    fileNameForSaving = f"{fileNameForSaving}.png"
                # printLight(f"Saving image to {fileNameForSaving}")
                saveFileWithFullPath(anImage, fileNameForSaving)

            if index == 0:
                ImageProcessor.currentPictures[knucklePictureKeyString] = anImage

            return CheckKnuckle.checkKnuckle(anImage=anImage, currentPictures=ImageProcessor.currentPictures,
                                             componentQRCode=ImageProcessor.currentComponentQRCodeValue, gamma=gamma)
        except Exception as e:
            logBoth('logCritical', ImageProcessor.logSource,
                    f"Exception in ImageProcessor.checkKnuckle: {e}",
                    Logger.PROBLEM)
            return anImage, False

    @classmethod
    def checkTopBearing(cls, anImage: np.ndarray, index: int = -1, gamma: float = 2.0) -> Tuple[np.ndarray, bool]:
        try:
            if ImageProcessor.saveTrialPictures:
                currentMethodName = f"{inspect.currentframe().f_code.co_name}"
                currentMethodName = currentMethodName.replace("check", "")
                savingDir = f"{ImageProcessor.mainDirForSavingPictures}{currentMethodName}/"
                fileNameForSaving = f"{savingDir}{getFileNameForSaving(useExtension=False, useNanoSec=True)}"
                if index >= 0:
                    fileNameForSaving = f"{fileNameForSaving}-{index}.png"
                else:
                    fileNameForSaving = f"{fileNameForSaving}.png"
                # printLight(f"Saving image to {fileNameForSaving}")
                saveFileWithFullPath(anImage, fileNameForSaving)
            if index == 0:
                ImageProcessor.currentPictures[topBearingPictureKeyString] = anImage

            # Unpack the 3-tuple return value
            annotated_img, is_present, geometry = CheckTopBearing.checkTopBearing(
                anImage=anImage,
                currentPictures=ImageProcessor.currentPictures,
                componentQRCode=ImageProcessor.currentComponentQRCodeValue,
                gamma=gamma
            )

            # Keep the ring (with its edge profile) for the nut / washer check; NOT OK removes any ring kept earlier
            ImageProcessor.saveBearingProfile(geometry if is_present else None)

            return annotated_img, is_present

        except Exception as e:
            logBoth('logCritical', ImageProcessor.logSource,
                    f"Exception in ImageProcessor.checkTopBearing: {e}",
                    Logger.PROBLEM)
            return anImage, False

    @classmethod
    def checkSplitPinAndWasher(cls, anImage: np.ndarray, index: int = -1, gamma : float = 2.0) -> Tuple[np.ndarray, bool]:
        try:
            if ImageProcessor.saveTrialPictures:
                currentMethodName = f"{inspect.currentframe().f_code.co_name}"
                currentMethodName = currentMethodName.replace("check", "")
                savingDir = f"{ImageProcessor.mainDirForSavingPictures}{currentMethodName}/"
                fileNameForSaving = f"{savingDir}{getFileNameForSaving(useExtension=False, useNanoSec=True)}"
                if index >= 0:
                    fileNameForSaving = f"{fileNameForSaving}-{index}.png"
                else:
                    fileNameForSaving = f"{fileNameForSaving}.png"
                # printLight(f"Saving image to {fileNameForSaving}")
                saveFileWithFullPath(anImage, fileNameForSaving)
            if index == 0:
                ImageProcessor.currentPictures[splitPinAndWasherPictureKeyString] = anImage
            return CheckSplitPinAndWasher.checkSplitPinAndWasher(anImage=anImage,
                                                                 currentPictures=ImageProcessor.currentPictures,
                                                                 componentQRCode=ImageProcessor.currentComponentQRCodeValue,
                                                                 gamma=gamma)
        except Exception as e:
            logBoth('logCritical', ImageProcessor.logSource,
                    f"Exception in ImageProcessor.checkSplitPinAndWasher: {e}",
                    Logger.PROBLEM)
            return anImage, False

    @classmethod
    def checkCap(cls, anImage: np.ndarray, index: int = -1, gamma: float = 2.0) -> Tuple[np.ndarray, bool]:
        try:
            if ImageProcessor.saveTrialPictures:
                currentMethodName = f"{inspect.currentframe().f_code.co_name}"
                currentMethodName = currentMethodName.replace("check", "")
                savingDir = f"{ImageProcessor.mainDirForSavingPictures}{currentMethodName}/"
                fileNameForSaving = f"{savingDir}{getFileNameForSaving(useExtension=False, useNanoSec=True)}"
                if index >= 0:
                    fileNameForSaving = f"{fileNameForSaving}-{index}.png"
                else:
                    fileNameForSaving = f"{fileNameForSaving}.png"
                # printLight(f"Saving image to {fileNameForSaving}")
                saveFileWithFullPath(anImage, fileNameForSaving)
            if index == 0:
                ImageProcessor.currentPictures[capPictureKeyString] = anImage
            return CheckCap.checkCap(anImage=anImage,
                                     currentPictures=ImageProcessor.currentPictures,
                                     componentQRCode=ImageProcessor.currentComponentQRCodeValue,
                                     gamma=gamma)
        except Exception as e:
            logBoth('logCritical', ImageProcessor.logSource,
                    f"Exception in ImageProcessor.checkCap: {e}",
                    Logger.PROBLEM)
            return anImage, False

    @classmethod
    def checkNoBunk(cls, anImage: np.ndarray, index: int = -1, gamma : float = 2.0) -> Tuple[np.ndarray, bool]:
        try:
            if ImageProcessor.saveTrialPictures:
                currentMethodName = f"{inspect.currentframe().f_code.co_name}"
                currentMethodName = currentMethodName.replace("check", "")
                savingDir = f"{ImageProcessor.mainDirForSavingPictures}{currentMethodName}/"
                fileNameForSaving = f"{savingDir}{getFileNameForSaving(useExtension=False, useNanoSec=True)}"
                if index >= 0:
                    fileNameForSaving = f"{fileNameForSaving}-{index}.png"
                else:
                    fileNameForSaving = f"{fileNameForSaving}.png"
                # printLight(f"Saving image to {fileNameForSaving}")
                saveFileWithFullPath(anImage, fileNameForSaving)
            if index == 0:
                ImageProcessor.currentPictures[noBunkPictureKeyString] = anImage
            return CheckNoBunk.checkNoBunk(anImage=anImage,
                                           currentPictures=ImageProcessor.currentPictures,
                                           componentQRCode=ImageProcessor.currentComponentQRCodeValue,
                                           gamma=gamma)
        except Exception as e:
            logBoth('logCritical', ImageProcessor.logSource,
                    f"Exception in ImageProcessor.checkNoBunk: {e}",
                    Logger.PROBLEM)
            return anImage, False

    methodMapping: dict = {
        MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE: checkKnuckle,
        MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING: None,
        MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING: checkTopBearing,
        MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER: None,
        MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS: None,
        MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK: checkNoBunk,
        MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER: checkSplitPinAndWasher,
        MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP: checkCap,
        MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS: None
    }

    # NOTE: As there are 3 pics being taken, instantiate 3 ImageProcessors per cameraId (==Camera Server)
    # These are to be done with pictureResultIndex = 0, pictureResultIndex = 1, pictureResultIndex = 2
    def __init__(self, name: str, pictureResultIndex : int, consumer: GenericQueueProcessor = None,
                 timeout : int = 1, sleepTime: float = 3.0, monitorRedisQueueForStopping : bool = True, max_size=32, **kwargs):
        GenericQueueProcessor.__init__(self, name=name, consumer=consumer, timeout=timeout, sleepTime=sleepTime,
                                       blocking=True, monitorRedisQueueForStopping=monitorRedisQueueForStopping,
                                       max_size=max_size, **kwargs)
        ImageProcessor.logSource = getFullyQualifiedName(__file__, __class__)
        self.pictureResultIndex = pictureResultIndex

# *******************************ImageProcessor end*************************************************

# *******************************MonitorSendResultQueue start*******************************************

# This is NOT NEEDED FOR ASHOK LEYLAND, because THERE IS NO NEED TO WAIT FOR A REDIS MESSAGE FROM IOSERVER
# BEFORE SENDING THE RETURN RESULT TO THE IOSERVER. This was a need in GelTech, so this thread was needed.

# class MonitorSendResultQueue(GenericQueueProcessor):
#
#     logSource = getFullyQualifiedName(__file__)
#
#     def __init__(self, name: str,  cameraId : int, consumer: GenericQueueProcessor = None, timeout: int = 1,
#                  # sleepTime: float = CosThetaConfigurator.getInstance().getSleepTimeForMonitoringTakePicQ(),
#                  sleepTime: float = 0.0,
#                  blocking: bool = True, monitorRedisQueueForStopping: bool = True, max_size: int = 32, **kwargs):
#         GenericQueueProcessor.__init__(self, name=name, consumer=consumer, sleepTime=sleepTime, blocking=blocking,
#                                        timeout=timeout,
#                                        monitorRedisQueueForStopping=monitorRedisQueueForStopping, max_size=max_size, **kwargs)
#         MonitorSendResultQueue.logSource = getFullyQualifiedName(__file__, __class__)
#         self.cameraId = cameraId
#         self.cameraResultQ = CosThetaConfigurator.getInstance().getCameraRequestResultQueue(cameraId=self.cameraId)
#         self.redisConnectionQ = None
#         self.clientRedisConnectedQ = False
#         self.connectToRedis()
#         # print(f"CameraQ is {self.cameraQ}, which has {getMessageCount(self.redisConnectionQ, self.cameraResultQ)} messages")
#         self.FASTEST_GAP_BETWEEN_TAKEPIC_REQUESTS = CosThetaConfigurator.getInstance().getFastestPossibleTimeBetweenTakePicRequests()
#         self.addItem("monitorQ")
#         # printBold(f"Added item in __init__() to queue {self.cameraResultQ} in MonitorSendResultQueue")
#         # self.setDebug(debugInit=False, debugAddItem= True, debugGetItem= True, debugProcessItem= True, debugWork= False, debugStop= True, debugOtherExceptions= False)
#
#     def connectToRedis(self, forceRenew = False):
#         if forceRenew:
#             self.redisConnectionQ = None
#             self.clientRedisConnectedQ = False
#         if not self.clientRedisConnectedQ:
#             try:
#                 self.redisConnectionQ = Redis(GenericQueueProcessor.redisHost, GenericQueueProcessor.redisPort, retry_on_timeout=True)
#                 self.clientRedisConnectedQ = True
#                 # printLight(f'Redis Connection is {self.redisConnectionLeftCameraPicture}')
#                 # SlaveConsoleLogger.getInstance().logDebug(MonitorSendResultQueue.logSource,
#                 #                                              f'Redis Connection is {self.redisConnectionQ}', Logger.SUCCESS)
#                 SlaveFileLogger.getInstance().logDebug(MonitorSendResultQueue.logSource,
#                                                           f'Redis Connection is {self.redisConnectionQ}', Logger.SUCCESS)
#                 sendCameraResultToIOServer(redisConnection=self.redisConnectionQ, result=notok,
#                                            cameraId=self.cameraId, aProducer=f"Camera_{self.name}", handshake=True)
#             except:
#                 self.clientRedisConnectedQ = False
#                 SlaveConsoleLogger.getInstance().logTakeAction(MonitorSendResultQueue.logSource,
#                                                              f'Could not get Redis Connection', Logger.PROBLEM)
#                 SlaveFileLogger.getInstance().logTakeAction(MonitorSendResultQueue.logSource,
#                                                              f'Could not get Redis Connection', Logger.PROBLEM)
#                 # pass
#
#     def getItem(self, blocking=True) -> Union[Any, None]:
#         # printBoldBlue(f"Added item in MonitorSendResultQueue")
#         return super().getItem()
#
#     def preWorkLoop(self):
#         return
#
#     def processItem(self, item: Any) -> Any:
#         global finalResult
#         # printBoldBlue(f"Entered processItem() in MonitorSendResultQueue")
#         if not self.getStopped():
#             self.addItem("monitorQ")
#         timeOfMessage = None
#         gotCommand = None
#         try:
#             # printBoldBlue(f"Waiting for message in {self.cameraResultQ} in MonitorSendResultQueue")
#             timeOfMessage, gotCommand = getRequestResultCommandFromIOServer(self.redisConnectionQ, self.cameraResultQ, block=5000)
#             # printBold(f"Got {timeOfMessage}, {gotCommand} from Redis in MonitorSendResultQueue {self.cameraResultQ}")
#         except Exception as e:
#             printBoldRed(e)
#             self.connectToRedis()
#         if timeOfMessage is not None:
#             if gotCommand:
#                 # printBold(f"Got {timeOfMessage}, {gotCommand} from Redis in MonitorSendResultQueue {self.cameraResultQ}")
#                 # printBoldGreen(f"Got command in MonitorSendResultQueue")
#                 timeOfMessage = float(timeOfMessage)
#                 if (time.time() - timeOfMessage) > self.FASTEST_GAP_BETWEEN_TAKEPIC_REQUESTS:
#                     printBoldRed(f"Got {timeOfMessage}, {gotCommand} from Redis in MonitorSendResultQueue {self.cameraResultQ} - Delayed by {time.time() - timeOfMessage} secs. Hence, not sending result")
#                 else:
#                     currentTime = time.time()
#                     sendCameraResultToIOServer(redisConnection=self.redisConnectionQ, result=finalResult, cameraId=self.cameraId, aProducer=f"Camera_{self.name}")
#                     timeNow = time.time()
#                     duration = round((timeNow - currentTime) * 1000,2)
#                     # printBold(f"Got {timeOfMessage}, {gotCommand} from Redis in MonitorSendResultQueue {self.cameraResultQ}. Sent result {finalResult} in {duration} ms")
#                     resetResults()
#                     return None
#             else:
#                 # printBoldRed(f"Got no command in MonitorGetPicQueue {self.cameraQ}")
#                 pass
#         return None
#
#     def postWorkLoop(self):
#         # printBoldBlue(f"Finished thread {self.name}")
#         if self.monitorRedisQueueForStopping:
#             sendStoppedResponse(self.redisConnectionForMonitoringStop, f"{MonitorGetPicQueue.logSource}.{self.name}")

# *******************************MonitorSendResultQueue end*********************************************



# **************************************************************************************************

def verifyVisionDependencies(loadModels: bool = True) -> list:
    """Start-up check for everything the visual checks need. Several check modules guard their ML imports with
    try/except (so they can also run standalone); in production that turned a missing dependency or model file into
    "every check of that type fails" with no clear error. Returns a list of problems (empty = all good) and also
    loads the models, so the first cycle is not slowed by lazy loading."""
    problems = []
    import camera.CheckHubAndBottomBearing as _hub
    import camera.CheckNoCapBunk as _noCapBunk
    import camera.CheckBunk as _bunk
    for name, module in (("Hub & bottom bearing check (HubAndBearingSegmenter)", _hub),
                         ("Bunk-for-component-press check (BunkSegmenter)", _noCapBunk),
                         ("Bunk-for-cap-press check (BunkSegmenter)", _bunk)):
        if not getattr(module, "SAM_AVAILABLE", False):
            problems.append(f"{name}: segmentation engine could not be imported")
    try:
        import camera.ModelManager as _mm
        if not getattr(_mm, "MOBILESAM_AVAILABLE", False):
            problems.append("mobile_sam package could not be imported (is models/MobileSAM present?)")
        manager = _mm.ModelManager.get_instance()
        for label, path in (("MobileSAM weights", manager.mobile_sam_path), ("ObjectAwareModel (YOLO) weights", manager.yolo_path)):
            if not os.path.isfile(path):
                problems.append(f"{label} not found at {path}")
        if not problems and loadModels:       # with the worker process, the models are loaded there, not here
            manager.load_models()
    except Exception as e:
        problems.append(f"Model manager / model loading failed: {e}")
    return problems


def CameraServer(mode : str = "Test", stateFile: str | None = None):
    """stateFile: None at MainProgram's start (a new run starts with no cycle state); the state file's path when
    MainProgram's supervisor restarts the camera server (it carries on with the cycle in progress). Either way the
    camera server writes its cycle state to the default file (camera/CameraStateFile.py) from now on."""
    from camera import CameraStateFile
    if stateFile:
        ImageProcessor.loadPersistedState(stateFile)
    ImageProcessor.stateFile = CameraStateFile.defaultPath()
    if not stateFile:
        ImageProcessor.persistState()               # a new run: the file of an earlier run is replaced by an empty state
    # the models are loaded in the vision check worker process, not here
    problems = verifyVisionDependencies(loadModels=False)
    if problems:
        message = "Camera server NOT started - missing vision dependencies:\n  - " + "\n  - ".join(problems)
        logBoth('logCritical', logSource, message, MessageType.PROBLEM)
        # Exiting makes the failure visible at once: the heartbeat server marks the camera DEAD (red tile + siren)
        sys.exit(1)
    # the camera server fetches the QR code before the knuckle check itself (ImageProcessor.prepareForKnuckleCheck)
    ImageProcessor.fetchQRCodeInChecks = False
    # the vision checks run in their own process (models loaded there), stopped and started again when a check
    # takes longer than <vendor>.vision.check.timeout.sec; started now so that the models are ready for cycle 1
    from camera.VisionCheckWorker import getVisionCheckWorker
    getVisionCheckWorker()

    # Log which vision parameters were re-tuned in ApplicationConfiguration.properties (<vendor>.vision.* keys), so a site
    # re-tune is always visible in the logs. Purely informational - never stops the server.
    try:
        from camera.VisionConfig import describeVisionParameters, getRegistry
        retuned = describeVisionParameters(onlyChanged=True)
        if retuned:
            message = (f"Vision parameters: {len(retuned)} of {len(getRegistry())} differ from the built-in defaults:\n  "
                       + "\n  ".join(retuned))
            logBoth('logCritical', logSource, message, MessageType.ISSUE)
        else:
            message = f"Vision parameters: all {len(getRegistry())} at the built-in defaults"
            logBoth('logCritical', logSource, message, MessageType.SUCCESS)
    except Exception as e:
        logBoth('logError', logSource, f"Could not report vision parameters: {e}", MessageType.ISSUE)

    # imageWriter : GenericQueueProcessor = None
    # if mode.upper() == "TEST":
    #     imageWriter = ImageWriter(name="ImageWriter", savingDir=CosThetaConfigurator.getInstance().getMainPictureSavingDirForModeTrial())
    #     converterScalerAndProcessor = ConverterScalerAndProcessor(name="ConverterAndScaler", consumer=imageWriter)
    # else:
    try:
        converterScalerAndProcessor = ConverterScalerAndProcessor(name="Converter Scaler And Processor", consumer=None)
        # takePictures = TakePictures(name="TakePictures", cameraId=camId, consumer=converterScalerAndProcessor)
        takePictures = TakePictures(name="TakePictures", consumer=converterScalerAndProcessor)
        monitorGetPic = MonitorGetPicQueue(name="MonitorGetPicQueue", consumer=takePictures)
        cameraWorkers.add("picture requests (MonitorGetPicQueue)", monitorGetPic)
        cameraWorkers.add("taking pictures (TakePictures)", takePictures)
        cameraWorkers.add("assessment (ConverterScalerAndProcessor)", converterScalerAndProcessor)
        converterScalerAndProcessor.start()
        logBoth('logInfo', logSource, f"Started thread ConverterScalerAndProcessor", Logger.SUCCESS)
        takePictures.start()
        logBoth('logInfo', logSource, f"Started thread TakePictures", Logger.SUCCESS)
        monitorGetPic.start()
        logBoth('logInfo', logSource, f"Started thread MonitorGetPicQ", Logger.SUCCESS)
        logBoth('logCritical', logSource, f"************", Logger.SUCCESS)
        logBoth('logCritical', logSource, f"Started Camera Server", Logger.SUCCESS)
        logBoth('logCritical', logSource, f"************", Logger.SUCCESS)
        monitorGetPic.join()
        takePictures.join()
        converterScalerAndProcessor.join()
    except Exception as e:
        logBoth('logCritical', logSource, f"Got Exception {e} while declaring Processors in Camera Server", Logger.PROBLEM)
    logBoth('logInfo', logSource, "About to exit Camera Server", Logger.GENERAL)
    sys.exit(0)

# **************************************************************************************************

def startCameraServer(mode: str = "Test", stateFile: str | None = None):
    CameraServer(mode=mode, stateFile=stateFile)

# startCameraServer()