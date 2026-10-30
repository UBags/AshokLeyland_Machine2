from processors.image.Bytes2ImageAndScaler import *
from processors.image.ImageWriter import *
from processors.image.GenericImageProcessor import *
from processors.image.ImageDisplayer import *
from logutils.SlaveLoggers import *
from utils.RedisUtils import *
from processors.GenericQueueProcessor import *
from camera.ONVIFCam import *

import warnings

warnings.filterwarnings('ignore', '.*h264.*', )

from Configuration import *

CosThetaConfigurator.getInstance()


class VideoCam(GenericQueueProcessor):
    logSource = getFullyQualifiedName(__file__)

    def __init__(self, name: str, camera: ONVIFCam,
                 consumer: Union[Bytes2ImageAndScaling, ImageDisplayer, GenericImageProcessor, ImageWriter] = None,
                 timeout: int = 1, sleepTime: float = 1 / CosThetaConfigurator.getInstance().getVideoRecordingFPS(),
                 blocking: bool = True, monitorRedisQueueForStopping: bool = True, max_size: int = 1024, **kwargs):
        GenericQueueProcessor.__init__(self, name=name, consumer=consumer, sleepTime=sleepTime, blocking=blocking,
                                       timeout=timeout,
                                       monitorRedisQueueForStopping=monitorRedisQueueForStopping, max_size=max_size, **kwargs)
        VideoCam.logSource = getFullyQualifiedName(__file__, __class__)
        self.name = name
        if camera is None:
            SlaveFileLogger.getInstance().logTakeAction(VideoCam.logSource,
                                                        f"In VideoCam.{self.name}, camera instance is None",
                                                        Logger.PROBLEM)
            SlaveConsoleLogger.getInstance().logTakeAction(VideoCam.logSource,
                                                           f"In VideoCam.{self.name}, camera instance is None",
                                                           Logger.PROBLEM)
            raise Exception(f"Camera object is None in {VideoCam.logSource}.{self.name}")
        if consumer is None:
            SlaveFileLogger.getInstance().logConsiderAction(VideoCam.logSource,
                                                            f"In {VideoCam.logSource}.{self.name}, consumer instance is None",
                                                            Logger.ISSUE)
            SlaveConsoleLogger.getInstance().logTakeAction(VideoCam.logSource,
                                                           f"In VideoCam.{self.name}, consumer instance is None",
                                                           Logger.ISSUE)
        self.camera = camera
        self.frameCounter = 0
        self.cam2FrameCounter = 0
        self.consecutiveNoneCounter = 0
        self.totalNoneCounter = 0
        self.counterForDebuggingFrames = CosThetaConfigurator.getInstance().getCounterForDebuggingPictures()
        self.numberOfFramesAfterWhichToRenewCam2 = CosThetaConfigurator.getInstance().getNumberOfFramesAfterWhichToRenewCam2()

    def triggerFrame(self):
        while (not self.stopped):
            startingTime = time.time()
            self.addItem("TakeNext")
            endingTime = time.time()
            wait_time = max(0.0, self.sleepTime - (endingTime - startingTime))
            if (wait_time > 0.0):
                time.sleep(wait_time)

    def preWorkLoop(self):
        self.frameTriggeringThread = threading.Thread(name=f'Frame Triggering Thread - VideoCam.{self.name}',
                                                      target=self.triggerFrame,
                                                      args=(), daemon=True)
        self.frameTriggeringThread.start()

    def processItem(self, item: Any) -> Any:
        returnValue, nextFrame = self.camera.getFrame(source=1)
        if nextFrame is None:
            self.totalNoneCounter += 1
            self.consecutiveNoneCounter += 1
            if self.consecutiveNoneCounter == 3:
                self.camera.launchRenewThread(source=1)
                self.consecutiveNoneCounter = 0
            returnValue, nextFrame = self.camera.getFrame(source=2)
            self.cam2FrameCounter += 1
            if (self.cam2FrameCounter % self.numberOfFramesAfterWhichToRenewCam2) == 0:
                self.launchRenewThreadForCam2()
        else:
            self.consecutiveNoneCounter = 0
        if nextFrame is None:
            SlaveFileLogger.getInstance().logTakeNote(VideoCam.logSource,
                                                        f"{self.frameCounter} : Frame taken in VideoCam.{self.name} is None",
                                                        Logger.PROBLEM)
            SlaveConsoleLogger.getInstance().logTakeNote(VideoCam.logSource,
                                                           f"{self.frameCounter} : Frame taken in VideoCam.{self.name} is None",
                                                           Logger.PROBLEM)
        else:
            self.frameCounter += 1
            if (self.frameCounter % self.counterForDebuggingFrames == 0):
                SlaveFileLogger.getInstance().logTakeNote(VideoCam.logSource,
                                                            f"{self.frameCounter} : Frame taken in VideoCam.{self.name}",
                                                            Logger.SUCCESS)
                SlaveConsoleLogger.getInstance().logTakeNote(VideoCam.logSource,
                                                               f"{self.frameCounter} : Frame taken in VideoCam.{self.name}",
                                                               Logger.SUCCESS)
        return nextFrame

    def postWorkLoop(self):
        self.frameTriggeringThread.join()
        self.camera.releaseResources()
        SlaveFileLogger.getInstance().logTakeAction(VideoCam.logSource,
                                                    f"Reached after photoTriggeringThread.join() in VideoCam.{self.name}",
                                                    Logger.SUCCESS)
        SlaveConsoleLogger.getInstance().logTakeAction(VideoCam.logSource,
                                                       f"Reached after photoTriggeringThread.join() in VideoCam.{self.name}",
                                                       Logger.SUCCESS)
        SlaveConsoleLogger.getInstance().logTakeAction(VideoCam.logSource,
                                                       f"Recorded {self.frameCounter} frames in VideoCam.{self.name}. Of these, cam2 recorded {self.cam2FrameCounter} frames",
                                                       Logger.SUCCESS)
        SlaveFileLogger.getInstance().logTakeAction(VideoCam.logSource,
                                                       f"Recorded {self.frameCounter} frames in VideoCam.{self.name}. Of these, cam2 recorded {self.cam2FrameCounter} frames",
                                                       Logger.SUCCESS)
        SlaveConsoleLogger.getInstance().logTakeAction(VideoCam.logSource,
                                                       f"Total {self.totalNoneCounter} 'None' frames in VideoCam.{self.name}",
                                                       Logger.SUCCESS)
        SlaveFileLogger.getInstance().logTakeAction(VideoCam.logSource,
                                                       f"Total {self.totalNoneCounter} 'None' frames in VideoCam.{self.name}",
                                                       Logger.SUCCESS)
        if self.monitorRedisQueueForStopping:
            sendStoppedResponse(self.redisConnectionForMonitoringStop, f"{VideoCam.logSource}.{self.name}")

    def renewCam2(self):
        while not self.camera.cam1Available:
            time.sleep(0.2)
        self.camera.launchRenewThread(source=2)
        return

    def launchRenewThreadForCam2(self):
        self.renewalThread = threading.Thread(name=f'Cam2 Renewal Thread - VideoCam.{self.name}',
                                              target=self.renewCam2,
                                              args=(), daemon=True)
        self.renewalThread.start()
        self.renewalThread.join()
        return
