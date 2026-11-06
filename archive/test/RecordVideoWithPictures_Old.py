import logutils.SlaveLoggers
from camera.ONVIFCam import *
from camera.PhotoCamera import *
from camera.VideoCamera import *
from processors.image.Bytes2ImageAndScaler import *
from utils.CosThetaFileUtils import *
from utils.RedisUtils import *
from BaseUtils import *
from logutils.SlaveLoggers import *

import warnings
warnings.filterwarnings('ignore', '.*h264.*', )

from Configuration import *
CosThetaConfigurator.getInstance()

print(getCurrentTime())
camId = 1
proj_root=get_project_root()
picRecordingFolder = CosThetaConfigurator.getInstance().getPicturesFolderForRecording(camId)
picRecordingFolder = f"{proj_root}/{picRecordingFolder}"
createDirectory(picRecordingFolder)
picRecordingFile = f"{picRecordingFolder}/{getCurrentTime()}.mp4"
print(picRecordingFile)
pictureFPS = round((1 / CosThetaConfigurator.getInstance().getSleepSecondsForPictureRecording()) * 4, 2)
print(pictureFPS)
pictureWriter = ImageWriter(name=f"PictureWriter{camId}", path=picRecordingFile, fps=pictureFPS,
                                 debugWrite=True)
converterAndScalerForPics = Bytes2ImageAndScaling(name=f"PicsConverterAndScaler{camId}",
                                                       consumer=pictureWriter)
camera2 = ONVIFCam(IP=CosThetaConfigurator.getInstance().getCameraIP(camId),
                        port=CosThetaConfigurator.getInstance().getCameraPort(camId),
                        uid=CosThetaConfigurator.getInstance().getCameraUid(camId),
                        password=CosThetaConfigurator.getInstance().getCameraPwd(camId),
                        wsdl_dir=CosThetaConfigurator.getInstance().getWsdlDir(),
                        name=f"{camId}_Cam2")
# camera2 = ONVIFCam(IP="192.168.1.150",
#                         port=CosThetaConfigurator.getInstance().getCameraPort(camId),
#                         uid=CosThetaConfigurator.getInstance().getCameraUid(camId),
#                         password=CosThetaConfigurator.getInstance().getCameraPwd(camId),
#                         wsdl_dir=CosThetaConfigurator.getInstance().getWsdlDir(),
#                         name=f"{camId}_Cam2")
delayCount = 0
currTime = time.time()*1000
r = 50
cutoff = 70
for l in range(r):
    startTime = time.time()*1000
    pic = camera2.getRawPicture((l % 2) + 1)
    endTime = time.time()*1000
    duration = endTime-startTime
    if duration > cutoff:
        delayCount += 1
        # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource, f"{l} : {type(pic)} - {duration}", Logger.RISK)
        # printBoldRed(f"{l} : {type(pic)} - {duration}")
    else:
        # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource, f"{l} : {type(pic)} - {duration}", Logger.SUCCESS)
        # printBoldGreen(f"{l} : {type(pic)}")
        pass
printBoldRed(f"{delayCount} pics delayed")
endT = time.time()*1000
# SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource, f"Ending - got {r} frames @ {round((endT - currTime) / r, 2)} ms per pic", Logger.SUCCESS)
averageTime = round((endT - currTime) / r, 2)
printBoldGreen(f"Ending - got {r} frames @ {averageTime} ms per pic")
averageTime = averageTime/1000
# averageTime = max(0.04, averageTime)
# averageTime1 = 0.047
# averageTime2 = 0.047
# averageTime3 = 0.053
# averageTime4 = 0.059
# averageTime = 100/1000

# camera3 = ONVIFCam(IP=CosThetaConfigurator.getInstance().getCameraIP(camId),
#                         port=CosThetaConfigurator.getInstance().getCameraPort(camId),
#                         uid=CosThetaConfigurator.getInstance().getCameraUidAlternate(camId),
#                         password=CosThetaConfigurator.getInstance().getCameraPwdAlternate(camId),
#                         wsdl_dir=CosThetaConfigurator.getInstance().getWsdlDir(),
#                         name=f"{camId}_Cam3")
# camera3 = ONVIFCam(IP="192.168.1.150",
#                         port=CosThetaConfigurator.getInstance().getCameraPort(camId),
#                         uid=CosThetaConfigurator.getInstance().getCameraUidAlternate(camId),
#                         password=CosThetaConfigurator.getInstance().getCameraPwdAlternate(camId),
#                         wsdl_dir=CosThetaConfigurator.getInstance().getWsdlDir(),
#                         name=f"{camId}_Cam3")
# camera4 = ONVIFCam(IP=CosThetaConfigurator.getInstance().getCameraIP(camId),
#                         port=CosThetaConfigurator.getInstance().getCameraPort(camId),
#                         uid=CosThetaConfigurator.getInstance().getCameraUid(camId),
#                         password=CosThetaConfigurator.getInstance().getCameraPwd(camId),
#                         wsdl_dir=CosThetaConfigurator.getInstance().getWsdlDir(),
#                         name=f"{camId}_Cam4")
# camera5 = ONVIFCam(IP=CosThetaConfigurator.getInstance().getCameraIP(camId),
#                         port=CosThetaConfigurator.getInstance().getCameraPort(camId),
#                         uid=CosThetaConfigurator.getInstance().getCameraUidAlternate(camId),
#                         password=CosThetaConfigurator.getInstance().getCameraPwdAlternate(camId),
#                         wsdl_dir=CosThetaConfigurator.getInstance().getWsdlDir(),
#                         name=f"{camId}_Cam5")
photoCamera1 = PhotoCamForRecordingInLoop(name=f"PhotoCamera_{camId}_1_1", cameraId=camId, camera=camera2,
                                          consumer=converterAndScalerForPics, profileToken=1, sleepTime=averageTime, monitorRedisQueueForStopping=False)
# photoCamera2 = PhotoCam(name=f"PhotoCamera_{camId}_2_1", camera=camera3,
#                              consumer=converterAndScalerForPics, profileToken=1, sleepTime=4*averageTime1, monitorRedisQueue=False)
# photoCamera3 = PhotoCam(name=f"PhotoCamera_{camId}_1_2", camera=camera4,
#                              consumer=converterAndScalerForPics, profileToken=2, sleepTime=4*averageTime1, monitorRedisQueue=False)
# photoCamera3 = PhotoCamForRecordingInLoop(name=f"PhotoCamera_{camId}_1_2", camera=camera3,
#                              consumer=converterAndScalerForPics, profileToken=2, sleepTime=averageTime, monitorRedisQueue=False)
# photoCamera4 = PhotoCam(name=f"PhotoCamera_{camId}_2_2", camera=camera5,
#                              consumer=converterAndScalerForPics, profileToken=2, sleepTime=4*averageTime1, monitorRedisQueue=False)

pictureWriter.start()
converterAndScalerForPics.start()
photoCamera1.start()
# time.sleep(CosThetaConfigurator.getInstance().getSleepSecondsForPictureRecording() * 0.23)
# time.sleep(averageTime1)
# photoCamera2.start()
# time.sleep(CosThetaConfigurator.getInstance().getSleepSecondsForPictureRecording() * 0.23)
# time.sleep(averageTime/2)
# photoCamera3.start()
# time.sleep(CosThetaConfigurator.getInstance().getSleepSecondsForPictureRecording() * 0.23)
# time.sleep(averageTime1)
# photoCamera4.start()
print(f"{getCurrentTime()} - STARTING RECORDING")
time.sleep(20)
photoCamera1.stop()
# photoCamera2.stop()
# photoCamera3.stop()
# photoCamera4.stop()
converterAndScalerForPics.stop()
pictureWriter.stop()
photoCamera1.join()
# photoCamera2.join()
# photoCamera3.join()
# photoCamera4.join()
converterAndScalerForPics.join()
pictureWriter.join()
camera2.releaseResources()
# camera3.releaseResources()
# camera4.releaseResources()
# camera5.releaseResources()
logutils.SlaveLoggers.stopSlaveLoggingThreads()
print(f"{getCurrentTime()} - ENDING RECORDING")
exit(0)
