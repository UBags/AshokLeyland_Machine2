import logutils.SlaveLoggers
from camera.ONVIFCam import *
from camera.PhotoCamera import *
from camera.VideoCamera import *
from processors import GenericQueueProcessor
from processors.image.Bytes2ImageAndScaler import *
from utils.CosThetaFileUtils import *
from utils.RedisUtils import *
from BaseUtils import *
from logutils import SlaveLoggers
from logutils.SlaveLoggers import *

import warnings
warnings.filterwarnings('ignore', '.*h264.*', )

from Configuration import *
CosThetaConfigurator.getInstance()

# CONSTANTS
CAM_ID = "camid"
ACTION = "action"


camId = None
doWhat = "record"

logSource = getFullyQualifiedName(__file__)

# Recording parameters
proj_root = get_project_root()
picRecordingFolder : str = None
picRecordingFile : str = None
pictureWriter : ImageWriter = None
converterAndScalerForPics : Bytes2ImageAndScaling = None
camera_onvif : ONVIFCam = None
averageTimePerPicture : float = 0.05
photoCamera : PhotoCamForRecordingInLoop = None
pictureProcessor : GenericImageProcessor = None

# **************************************************************************************************

def populatePicRecordingParams():
    global picRecordingFolder, picRecordingFile
    picRecordingFolder = CosThetaConfigurator.getInstance().getPicturesFolderForRecording(camId)
    picRecordingFolder = f"{proj_root}/{picRecordingFolder}"
    createDirectory(picRecordingFolder)
    picRecordingFile = f"{picRecordingFolder}/{getCurrentTime()}.mp4"
    SlaveConsoleLogger.getInstance().logTakeNote(logSource, f"Recording to {picRecordingFile}", Logger.SUCCESS)
    SlaveFileLogger.getInstance().logTakeNote(logSource, f"Recording to {picRecordingFile}", Logger.SUCCESS)

def populateCameraAndScalerAndWriter():
    global pictureWriter, converterAndScalerForPics, camera_onvif, picRecordingFile, camId
    pictureFPS = round(1 / CosThetaConfigurator.getInstance().getSleepSecondsForPictureRecording(), 2)
    pictureWriter = ImageWriter(name=f"PictureWriter{camId}", path=picRecordingFile, fps=pictureFPS,
                                debugWrite=True, monitorRedisQueueForStopping=True)
    converterAndScalerForPics = Bytes2ImageAndScaling(name=f"PicsConverterAndScaler{camId}",
                                                      consumer=pictureWriter, monitorRedisQueueForStopping=True)
    camera_onvif = ONVIFCam(IP=CosThetaConfigurator.getInstance().getCameraIP(camId),
                            port=CosThetaConfigurator.getInstance().getCameraPort(camId),
                            uid=CosThetaConfigurator.getInstance().getCameraUid(camId),
                            password=CosThetaConfigurator.getInstance().getCameraPwd(camId),
                            wsdl_dir=CosThetaConfigurator.getInstance().getWsdlDir(),
                            name=f"Cam_{camId}")


def getAveragePictureTime():
    global averageTimePerPicture, camera_onvif
    delayCount = 0
    currTime = time.time() * 1000
    r = 50
    cutoff = 70
    for l in range(r):
        startTime = time.time() * 1000
        pic = camera_onvif.getRawPicture((l % 2) + 1)
        endTime = time.time() * 1000
        duration = endTime - startTime
        if duration > cutoff:
            delayCount += 1
            SlaveConsoleLogger.getInstance().logConsiderAction(ONVIFCam.logSource, f"{l} : {type(pic)} - {duration}", Logger.RISK)
            SlaveFileLogger.getInstance().logConsiderAction(ONVIFCam.logSource, f"{l} : {type(pic)} - {duration}", Logger.RISK)
            # printBoldRed(f"{l} : {type(pic)} - {duration}")
    SlaveConsoleLogger.getInstance().logConsiderAction(ONVIFCam.logSource, f"{delayCount} pics delayed", Logger.RISK)
    SlaveFileLogger.getInstance().logConsiderAction(ONVIFCam.logSource, f"{delayCount} pics delayed", Logger.RISK)
    # printBoldRed(f"{delayCount} pics delayed")
    endT = time.time() * 1000
    SlaveConsoleLogger.getInstance().logConsiderAction(ONVIFCam.logSource, f"Ending - got {r} frames @ {round((endT - currTime) / r, 2)} ms per pic", Logger.SUCCESS)
    SlaveFileLogger.getInstance().logConsiderAction(ONVIFCam.logSource, f"Ending - got {r} frames @ {round((endT - currTime) / r, 2)} ms per pic", Logger.SUCCESS)
    averageTime = round((endT - currTime) / r, 2)
    # printBoldGreen(f"Ending - got {r} frames @ {averageTime} ms per pic")
    averageTimePerPicture = averageTime / 1000

def createPhotoCam(consumer : Union[Bytes2ImageAndScaling, ImageWriter, ImageDisplayer, GenericImageProcessor]):
    global averageTimePerPicture, photoCamera, converterAndScalerForPics
    photoCamera = PhotoCamForRecordingInLoop(name=f"PhotoCamera_{camId}_1_1", cameraId = camId, camera=camera_onvif,
                                             consumer=consumer, profileToken=1, sleepTime=averageTimePerPicture,
                                             monitorRedisQueueForStopping=True)

def startRecordingThreads():
    pictureWriter.start()
    converterAndScalerForPics.start()
    photoCamera.start()

def stopAndJoinThreads():
    photoCamera.stop()
    converterAndScalerForPics.stop()
    pictureWriter.stop()
    photoCamera.join()
    converterAndScalerForPics.join()
    pictureWriter.join()

# **************************************************************************************************

def releaseResources():
    camera_onvif.releaseResources()

# **************************************************************************************************


argParams = getCommandLineArgumentsAsDictionary()

try:
    camId = int(argParams[CAM_ID])
except Exception:
    SlaveConsoleLogger.getInstance().logTakeAction(logSource, f"Cannot start CameraServer as no camid provided. Usage : python CameraServer camid=1 [or camid=2]", Logger.PROBLEM)
    SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                 f"Cannot start CameraServer as no camid provided. Usage : python CameraServer camid=1 [or camid=2]",
                                                 Logger.PROBLEM)
    sys.exit(0)

# **************************************************************************************************

try:
    populatePicRecordingParams()
    populateCameraAndScalerAndWriter()
    getAveragePictureTime()
    createPhotoCam(converterAndScalerForPics)
    startRecordingThreads()
    recordingTime = CosThetaConfigurator.getInstance().getRecordingDuration()
    SlaveConsoleLogger.getInstance().logTakeNote(logSource, f"Started recording in file {picRecordingFile}", Logger.SUCCESS)
    SlaveFileLogger.getInstance().logTakeNote(logSource, f"Started recording in file {picRecordingFile}", Logger.SUCCESS)
    time.sleep(recordingTime)
    stopAndJoinThreads()
    SlaveConsoleLogger.getInstance().logTakeNote(logSource, f"Ended recording in file {picRecordingFile}", Logger.SUCCESS)
    SlaveFileLogger.getInstance().logTakeNote(logSource, f"Ended recording in file {picRecordingFile}", Logger.SUCCESS)
    releaseResources()
    logutils.SlaveLoggers.stopSlaveLoggingThreads()
    exit(0)
except Exception as e:
    SlaveConsoleLogger.getInstance().logTakeAction(logSource, f"Could not record in {picRecordingFile} with {camId=} due to {e}", Logger.SUCCESS)
    SlaveFileLogger.getInstance().logTakeAction(logSource, f"Could not record in {picRecordingFile} with {camId=} due to {e}", Logger.SUCCESS)
    if not photoCamera.stopped:
        photoCamera.stop()
        photoCamera.join()
    if not converterAndScalerForPics.stopped:
        converterAndScalerForPics.stop()
        converterAndScalerForPics.join()
    if not pictureWriter.stopped:
        pictureWriter.stop()
        pictureWriter.join()

