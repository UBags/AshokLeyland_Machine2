import time

from processors.image.Bytes2ImageAndScaler import *
from processors.image.ImageWriter import *
from processors.image.GenericImageProcessor import *
from processors.image.ImageDisplayer import *
from logutils.SlaveLoggers import *
from utils.RedisUtils import *
from processors.GenericQueueProcessor import *
from camera.ONVIFCam import *
from utils.CosThetaPrintUtils import *

import warnings

warnings.filterwarnings('ignore', '.*h264.*', )

from Configuration import *
CosThetaConfigurator.getInstance()

class PhotoCamForRecordingInParallelRun(GenericQueueProcessor):
    logSource = getFullyQualifiedName(__file__)
    calcDelays : bool = CosThetaConfigurator.getInstance().getCalculateDelayWhenUsingPictures()

    def __init__(self, name: str, cameraId : int, camera: ONVIFCam,
                 consumer: Union[Bytes2ImageAndScaling, ImageDisplayer, GenericImageProcessor, ImageWriter] = None,
                 profileToken: int = 1, timeout: int = 1,
                 sleepTime: float = CosThetaConfigurator.getInstance().getSleepSecondsForPictureRecording(),
                 blocking: bool = True, monitorRedisQueueForStopping: bool = True, max_size: int = 32, **kwargs):
        GenericQueueProcessor.__init__(self, name=name, consumer=consumer, sleepTime=sleepTime, blocking=blocking,
                                       timeout=timeout,
                                       monitorRedisQueueForStopping=monitorRedisQueueForStopping, max_size=max_size, **kwargs)
        PhotoCamForRecordingInParallelRun.logSource = getFullyQualifiedName(__file__, __class__)
        self.cameraId = cameraId
        if camera is None:
            SlaveFileLogger.getInstance().logTakeAction(PhotoCamForRecordingInParallelRun.logSource,
                                                        f"In PhotoCamForRecordingInParallelRun.{self.name}, camera instance is None",
                                                        Logger.PROBLEM)
            SlaveConsoleLogger.getInstance().logTakeAction(PhotoCamForRecordingInParallelRun.logSource,
                                                           f"In PhotoCamForRecordingInParallelRun.{self.name}, camera instance is None",
                                                           Logger.PROBLEM)
            raise Exception(f"Camera object is None in {PhotoCamForRecordingInParallelRun.logSource}.{self.name}")
        if consumer is None:
            SlaveFileLogger.getInstance().logConsiderAction(PhotoCamForRecordingInParallelRun.logSource,
                                                            f"In {PhotoCamForRecordingInParallelRun.logSource}.{self.name}, consumer instance is None",
                                                            Logger.ISSUE)
            SlaveConsoleLogger.getInstance().logConsiderAction(PhotoCamForRecordingInParallelRun.logSource,
                                                            f"In {PhotoCamForRecordingInParallelRun.logSource}.{self.name}, consumer instance is None",
                                                            Logger.ISSUE)
        self.camera = camera
        self.profileToken = profileToken
        self.pictureCounter = 0
        self.noneCounter = 0
        self.greenCount = 0
        self.blueCount = 0
        self.yellowCount = 0
        self.redCount = 0
        self.counterForDebuggingPictures = CosThetaConfigurator.getCounterForDebuggingPictures()

    def triggerPicture(self):
        while (not self.stopped):
            # startingTime = time.time()
            self.addItem("TakeNext")
            # endingTime = time.time()
            # wait_time = max(0.0, self.sleepTime - (endingTime - startingTime))
            # if (wait_time > 0.0):
            #     time.sleep(wait_time)
            time.sleep(self.sleepTime)

    def preWorkLoop(self):
        self.photoTriggeringThread = threading.Thread(name=f'Photo Triggering Thread - {PhotoCamForRecordingInParallelRun.logSource}.{self.name}',
                                                      target=self.triggerPicture,
                                                      args=(), daemon=True)
        self.photoTriggeringThread.start()

    def processItem(self, item: Any) -> Any:
        if PhotoCamForRecordingInParallelRun.calcDelays:
            startTime = time.time()
        nextRawPicture = self.camera.getRawPicture(self.profileToken)
        if PhotoCamForRecordingInParallelRun.calcDelays:
            endTime = time.time()
            duration = round((endTime - startTime)*1000,0)
            if duration > 300:
                self.redCount += 1
                printBoldRed(f"{self.name} : Got picture in {duration} ms")
            elif duration > 200 :
                self.yellowCount += 1
                printBoldYellow(f"{self.name} : Got picture in {duration} ms")
            elif duration > 120:
                self.blueCount += 1
                printBoldBlue(f"{self.name} : Got picture in {duration} ms")
            else:
                self.greenCount += 1
                # printBoldGreen(f"{self.name} : Got picture in {duration} ms")
        if nextRawPicture is None:
            self.noneCounter += 1
            SlaveFileLogger.getInstance().logTakeAction(PhotoCamForRecordingInParallelRun.logSource,
                                                        f"{self.pictureCounter} : Picture taken in PhotoCamForRecordingInParallelRun.{self.name} is None",
                                                        Logger.PROBLEM)
            SlaveConsoleLogger.getInstance().logTakeAction(PhotoCamForRecordingInParallelRun.logSource,
                                                           f"{self.pictureCounter} : Picture taken in PhotoCamForRecordingInParallelRun.{self.name} is None",
                                                           Logger.PROBLEM)
        else:
            self.pictureCounter += 1
            if (self.pictureCounter % self.counterForDebuggingPictures == 0):
                SlaveFileLogger.getInstance().logTakeAction(PhotoCamForRecordingInParallelRun.logSource,
                                                            f"{self.pictureCounter} : Picture taken in PhotoCamForRecordingInParallelRun.{self.name}",
                                                            Logger.SUCCESS)
                SlaveConsoleLogger.getInstance().logTakeAction(PhotoCamForRecordingInParallelRun.logSource,
                                                               f"{self.pictureCounter} : Picture taken in PhotoCamForRecordingInParallelRun.{self.name}",
                                                               Logger.SUCCESS)
        return nextRawPicture

    def postWorkLoop(self):
        self.photoTriggeringThread.join()
        self.camera.releaseResources()
        SlaveFileLogger.getInstance().logTakeAction(PhotoCamForRecordingInParallelRun.logSource,
                                                    f"Reached after photoTriggeringThread.join() in PhotoCamForRecordingInParallelRun.{self.name}",
                                                    Logger.SUCCESS)
        SlaveConsoleLogger.getInstance().logTakeAction(PhotoCamForRecordingInParallelRun.logSource,
                                                       f"Reached after photoTriggeringThread.join() in PhotoCamForRecordingInParallelRun.{self.name}",
                                                       Logger.SUCCESS)
        SlaveConsoleLogger.getInstance().logTakeAction(PhotoCamForRecordingInParallelRun.logSource,
                                                       f"Recorded {self.pictureCounter} frames in PhotoCamForRecordingInParallelRun.{self.name}; another {self.noneCounter} pictures were None",
                                                       Logger.SUCCESS)
        SlaveFileLogger.getInstance().logTakeAction(PhotoCamForRecordingInParallelRun.logSource,
                                                       f"Recorded {self.pictureCounter} frames in PhotoCamForRecordingInParallelRun.{self.name}; another {self.noneCounter} pictures were None",
                                                    Logger.SUCCESS)
        if PhotoCamForRecordingInParallelRun.calcDelays:
            SlaveConsoleLogger.getInstance().logTakeAction(PhotoCamForRecordingInParallelRun.logSource,
                                                           f"In PhotoCamForRecordingInParallelRun.{self.name}: Green count = {self.greenCount}, Blue count = {self.blueCount}, Yellow count = {self.yellowCount}, Red count = {self.redCount}",
                                                           Logger.SUCCESS)
            SlaveFileLogger.getInstance().logTakeAction(PhotoCamForRecordingInParallelRun.logSource,
                                                           f"In PhotoCamForRecordingInParallelRun.{self.name}: Green count = {self.greenCount}, Blue count = {self.blueCount}, Yellow count = {self.yellowCount}, Red count = {self.redCount}",
                                                        Logger.SUCCESS)
        if self.monitorRedisQueueForStopping:
            sendStoppedResponse(self.redisConnectionForMonitoringStop, f"{PhotoCamForRecordingInParallelRun.logSource}.{self.name}")

class PhotoCamForRecordingInLoop(GenericQueueProcessor):
    logSource = getFullyQualifiedName(__file__)
    calcDelays : bool = CosThetaConfigurator.getInstance().getCalculateDelayWhenUsingPictures()
    # printBold(f"CalculateDelay is {calcDelays}")
    cutoff = 80 # milliseconds

    def __init__(self, name: str, cameraId : int, camera: ONVIFCam,
                 consumer: Union[Bytes2ImageAndScaling, ImageDisplayer, GenericImageProcessor, ImageWriter] = None,
                 profileToken: int = 1, timeout: int = 1,
                 sleepTime: float = CosThetaConfigurator.getInstance().getSleepSecondsForPictureRecording(),
                 blocking: bool = True, monitorRedisQueueForStopping: bool = True, max_size: int = 32, **kwargs):
        GenericQueueProcessor.__init__(self, name=name, consumer=consumer, sleepTime=sleepTime, blocking=blocking,
                                       timeout=timeout,
                                       monitorRedisQueueForStopping=monitorRedisQueueForStopping, max_size=max_size, **kwargs)
        PhotoCamForRecordingInLoop.logSource = getFullyQualifiedName(__file__, __class__)
        self.cameraId = cameraId
        if camera is None:
            SlaveFileLogger.getInstance().logTakeAction(PhotoCamForRecordingInLoop.logSource,
                                                        f"In PhotoCamForRecordingInLoop.{self.name}, camera instance is None",
                                                        Logger.PROBLEM)
            SlaveConsoleLogger.getInstance().logTakeAction(PhotoCamForRecordingInLoop.logSource,
                                                           f"In PhotoCamForRecordingInLoop.{self.name}, camera instance is None",
                                                           Logger.PROBLEM)
            raise Exception(f"Camera object is None in {PhotoCamForRecordingInLoop.logSource}.{self.name}")
        if consumer is None:
            SlaveFileLogger.getInstance().logConsiderAction(PhotoCamForRecordingInLoop.logSource,
                                                            f"In {PhotoCamForRecordingInLoop.logSource}.{self.name}, consumer instance is None",
                                                            Logger.ISSUE)
            SlaveConsoleLogger.getInstance().logConsiderAction(PhotoCamForRecordingInLoop.logSource,
                                                            f"In {PhotoCamForRecordingInLoop.logSource}.{self.name}, consumer instance is None",
                                                            Logger.ISSUE)
        self.camera = camera
        self.profileToken = profileToken
        self.pictureCounter = 0
        self.noneCounter = 0
        self.greenCount = 0
        self.blueCount = 0
        self.yellowCount = 0
        self.redCount = 0
        self.counterForDebuggingPictures = CosThetaConfigurator.getCounterForDebuggingPictures()
        self.consecutiveCount_Source2 = 0
        self.consecutiveCount_Source1 = 0


    def preWorkLoop(self):
        self.addItem("takeNextPic")

    def processItem(self, item: Any) -> Any:
        if PhotoCamForRecordingInLoop.calcDelays:
            startTime = time.time()
        self.currentCam = None
        startTime = time.time() * 1000
        pic = None
        if self.camera.cam2Available:
            # _, pic = camera1.getTimedFrame(timeDuration=timeBetweenFrames, source= 2)
            _, pic = self.camera.getFrame(source=2)
            self.consecutiveCount_Source2 += 1
            if pic is None:
                self.camera.cam2Available = False
                self.camera.launchRenewThread(2)
                self.consecutiveCount_Source2 = 0
            else:
                self.currentCam = self.camera.cam2
        elif self.camera.cam1Available:
            # _, pic = camera1.getTimedFrame(timeDuration=timeBetweenFrames, source=1)
            _, pic = self.camera.getFrame(source=1)
            self.consecutiveCount_Source1 += 1
            if pic is None:
                self.camera.cam1Available = False
                self.camera.launchRenewThread(1)
                self.consecutiveCount_Source1 = 0
            else:
                self.currentCam = self.camera.cam1Process
        endTime = time.time() * 1000
        # nextRawPicture = self.camera.getRawPicture(self.profileToken)
        nextRawPicture = pic
        if PhotoCamForRecordingInLoop.calcDelays:
            endTime = time.time()
            duration =  round((endTime - startTime)*1000,0)
            if duration > 300:
                self.redCount += 1
                printBoldRed(f"{self.name} : Got picture in {duration} ms")
            elif duration > 200 :
                self.yellowCount += 1
                printBoldYellow(f"{self.name} : Got picture in {duration} ms")
            elif duration > 120:
                self.blueCount += 1
                printBoldBlue(f"{self.name} : Got picture in {duration} ms")
            else:
                self.greenCount += 1
                # printBoldGreen(f"{self.name} : Got picture in {duration} ms")
        if nextRawPicture is None:
            self.noneCounter += 1
            SlaveFileLogger.getInstance().logTakeAction(PhotoCamForRecordingInLoop.logSource,
                                                        f"{self.pictureCounter} : Picture taken in PhotoCamForRecordingInLoop.{self.name} is None",
                                                        Logger.PROBLEM)
            SlaveConsoleLogger.getInstance().logTakeAction(PhotoCamForRecordingInLoop.logSource,
                                                           f"{self.pictureCounter} : Picture taken in PhotoCamForRecordingInLoop.{self.name} is None",
                                                           Logger.PROBLEM)
        else:
            self.pictureCounter += 1
            if (self.pictureCounter % self.counterForDebuggingPictures == 0):
                SlaveFileLogger.getInstance().logTakeAction(PhotoCamForRecordingInLoop.logSource,
                                                            f"{self.pictureCounter} : Picture taken in PhotoCamForRecordingInLoop.{self.name}",
                                                            Logger.SUCCESS)
                SlaveConsoleLogger.getInstance().logTakeAction(PhotoCamForRecordingInLoop.logSource,
                                                               f"{self.pictureCounter} : Picture taken in PhotoCamForRecordingInLoop.{self.name}",
                                                               Logger.SUCCESS)
        duration = endTime - startTime
        if (duration > PhotoCamForRecordingInLoop.cutoff):
            if (duration > 1.5 * PhotoCamForRecordingInLoop.cutoff):
                if self.currentCam == self.camera.cam1Process:
                    self.camera.cam1Available = False
                    self.camera.launchRenewThread(1)
                    self.consecutiveCount_Source1 = 0
                elif self.currentCam == self.camera.cam2:
                    self.camera.cam2Available = False
                    self.camera.launchRenewThread(2)
                    self.consecutiveCount_Source2 = 0

        if self.consecutiveCount_Source2 >= 1973 and self.camera.cam2Available and self.camera.cam1Available:
            self.camera.cam2Available = False
            self.camera.launchRenewThread(2)
            self.consecutiveCount_Source2 = 0
        if self.consecutiveCount_Source1 >= 1471 and self.camera.cam1Available and self.camera.cam2Available:
            self.camera.cam1Available = False
            self.camera.launchRenewThread(1)
            self.consecutiveCount_Source1 = 0
        # time.sleep(0.005)
        self.addItem("takeNextPic")
        return nextRawPicture

    def postWorkLoop(self):
        self.camera.releaseResources()
        SlaveFileLogger.getInstance().logTakeAction(PhotoCamForRecordingInLoop.logSource,
                                                    f"Reached after finishing processItem() loop in PhotoCamForRecordingInLoop.{self.name}",
                                                    Logger.SUCCESS)
        SlaveConsoleLogger.getInstance().logTakeAction(PhotoCamForRecordingInLoop.logSource,
                                                       f"Reached after finishing processItem() loop in PhotoCamForRecordingInLoop.{self.name}",
                                                       Logger.SUCCESS)
        SlaveConsoleLogger.getInstance().logTakeAction(PhotoCamForRecordingInLoop.logSource,
                                                       f"Recorded {self.pictureCounter} frames in PhotoCamForRecordingInLoop.{self.name}; another {self.noneCounter} pictures were None",
                                                       Logger.SUCCESS)
        SlaveFileLogger.getInstance().logTakeAction(PhotoCamForRecordingInLoop.logSource,
                                                       f"Recorded {self.pictureCounter} frames in PhotoCamForRecordingInLoop.{self.name}; another {self.noneCounter} pictures were None",
                                                    Logger.SUCCESS)
        if PhotoCamForRecordingInLoop.calcDelays:
            SlaveConsoleLogger.getInstance().logTakeAction(PhotoCamForRecordingInLoop.logSource,
                                                           f"In PhotoCam.{self.name}: Green count = {self.greenCount}, Blue count = {self.blueCount}, Yellow count = {self.yellowCount}, Red count = {self.redCount}",
                                                           Logger.SUCCESS)
            SlaveFileLogger.getInstance().logTakeAction(PhotoCamForRecordingInLoop.logSource,
                                                           f"In PhotoCam.{self.name}: Green count = {self.greenCount}, Blue count = {self.blueCount}, Yellow count = {self.yellowCount}, Red count = {self.redCount}",
                                                        Logger.SUCCESS)
        if self.monitorRedisQueueForStopping:
            sendStoppedResponse(self.redisConnectionForMonitoringStop, f"{PhotoCamForRecordingInLoop.logSource}.{self.name}")

