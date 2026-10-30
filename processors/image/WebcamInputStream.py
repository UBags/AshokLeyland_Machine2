# Image Input Stream captures the anImage stream from an input source. Default source is
# webcam, but can be changed to anything

from queue import *
from typing import *

from processors.image.GenericImageProcessor import *
from processors.image.ImageWriter import *
from processors.image.ImageDisplayer import *
from processors.image.Bytes2ImageAndScaler import *
from datetime import datetime
from processors.GenericQueueProcessor import *
from utils.RedisUtils import *
from BaseUtils import *
from logutils import SlaveLoggers
from logutils.SlaveLoggers import *

class WebcamInputStream(GenericQueueProcessor):

    logSource = getFullyQualifiedName(__file__)

    def __init__(self, name : str, source : Union[int, str], consumer: Union[GenericImageProcessor, ImageWriter, ImageDisplayer, Bytes2ImageAndScaling] = None, timeout :int = 1, sleepTime : float = 0.05, fps : int = 12, changeToBGR : bool = False, monitorRedisQueueForStopping : bool = True, max_size : int = 256, **kwargs):
        GenericQueueProcessor.__init__(self, name=name, consumer=consumer, timeout=timeout, sleepTime=sleepTime, blocking=True, monitorRedisQueueForStopping=monitorRedisQueueForStopping, max_size=max_size, **kwargs)
        WebcamInputStream.logSource = getFullyQualifiedName(__file__, __class__)
        try:
            self.fps = fps
            self.changeToBGR = changeToBGR
            self.stream = cv2.VideoCapture(source)
            self.connected = True
            self.setStreamProperties()
            SlaveFileLogger.getInstance().logTakeNote(WebcamInputStream.logSource,
                                                      f'Got video source {source} in {WebcamInputStream.logSource}',
                                                      Logger.SUCCESS)
            SlaveConsoleLogger.getInstance().logTakeNote(WebcamInputStream.logSource,
                                                      f'Got video source {source} in {WebcamInputStream.logSource}',
                                                      Logger.SUCCESS)
        except Exception as e:
            self.connected = False
            self.stream = None
            SlaveFileLogger.getInstance().logTakeAction(WebcamInputStream.logSource,
                                                      f'Could not get video source {source} in {WebcamInputStream.logSource} due to {e}',
                                                      Logger.PROBLEM)
            SlaveConsoleLogger.getInstance().logTakeAction(WebcamInputStream.logSource,
                                                      f'Could not get video source {source} in {WebcamInputStream.logSource} due to {e}',
                                                      Logger.PROBLEM)

    def setStreamProperties(self):
        if self.stream is not None:
            width = self.stream.get(cv2.CAP_PROP_FRAME_WIDTH),
            height = self.stream.get(cv2.CAP_PROP_FRAME_HEIGHT),
            inbuiltFPS = self.stream.get(cv2.CAP_PROP_FPS)
            self.streamProperties = {'width': width, 'height': height, 'inbuiltFPS': inbuiltFPS, 'fps': self.fps}

    def getStreamProperties(self):
        return self.streamProperties

    def isOpen(self):
        if self.stream is not None:
            return self.stream.isOpened()
        else:
            return False

    def addItem(self, item: Any) -> Union[Any, None]:
        return super().addItem(item)

    def preWorkLoop(self):
        return

    def processItem(self, item: Any) -> Any:
        try:
            frame = None
            if self.stream is not None:
                # read the next frame from the source
                (ret, frame) = self.stream.read()
                if frame is not None:
                    if self.changeToBGR:
                        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            return frame
        except Exception as e:
            SlaveFileLogger.getInstance().logConsiderAction(WebcamInputStream.logSource,
                                                      f"In ImageInputStream.{self.name}, could not get image due to {e}",
                                                      Logger.ISSUE)
            SlaveConsoleLogger.getInstance().logConsiderAction(WebcamInputStream.logSource,
                                                         f"In ImageInputStream.{self.name}, could not get image due to {e}",
                                                         Logger.ISSUE)
        return None

    def postWorkLoop(self):
        if hasattr(self, "stream"):
            if self.stream is not None:
                if self.stream.isOpened():
                    self.stream.release()

    def __del__(self):
        if hasattr(self, "stream"):
            if self.stream is not None:
                if self.stream.isOpened():
                    self.stream.release()
