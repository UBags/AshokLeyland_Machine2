# Doesn't work.
# PROBLEM: The problem is that the getFrames() method of 2 logins returns frames that are not in sync with each other.
# ************************************************************************

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
proj_root = get_project_root()
videoRecordingFolder = CosThetaConfigurator.getInstance().getVideosFolderForRecording(camId)
videoRecordingFolder = f"{proj_root}/{videoRecordingFolder}"
createDirectory(videoRecordingFolder)
videoRecordingFile = f"{videoRecordingFolder}/{getCurrentTime()}.mp4"
print(f"Recording to {videoRecordingFile}")
videoFPS = CosThetaConfigurator.getInstance().getVideoRecordingFPS()
timeBetweenFrames = 1.0/videoFPS
timeBetweenPictures = 1.0 / CosThetaConfigurator.getInstance().getPictureRecordingFPS()
print(f"{timeBetweenFrames = }, {timeBetweenPictures = }")
videoWriter = ImageWriter(name=f"VideoWriter{camId}", path=videoRecordingFile, fps=videoFPS,
                                 debugWrite=True)
converterAndScalerForVideo = Bytes2ImageAndScaling(name=f"VideosConverterAndScaler{camId}",
                                                        consumer=videoWriter)
videoWriter.start()
converterAndScalerForVideo.start()
# cam1 = ONVIFCam("192.168.1.100", 80, "admin", "abcd1234", "C:/CosTheta/GelTec/onvif/ver10/")
camera1 = ONVIFCam(IP=CosThetaConfigurator.getInstance().getCameraIP(camId),
                        port=CosThetaConfigurator.getInstance().getCameraPort(camId),
                        uid=CosThetaConfigurator.getInstance().getCameraUid(camId),
                        password=CosThetaConfigurator.getInstance().getCameraPwd(camId),
                        wsdl_dir=CosThetaConfigurator.getInstance().getWsdlDir(),
                        name=f"{camId}_Cam1")
camera2 = ONVIFCam(IP=CosThetaConfigurator.getInstance().getCameraIP(camId),
                        port=CosThetaConfigurator.getInstance().getCameraPort(camId),
                        uid=CosThetaConfigurator.getInstance().getCameraUidAlternate(camId),
                        password=CosThetaConfigurator.getInstance().getCameraPwdAlternate(camId),
                        wsdl_dir=CosThetaConfigurator.getInstance().getWsdlDir(),
                        name=f"{camId}_Cam2")
camera2.getRawPicture(source = 1)
camera2.getRawPicture(source = 2)
camera1.getRawPicture(source = 1)
camera1.getRawPicture(source = 2)
printBoldGreen(camera1)
delayCount = 0
noneCount = 0
currTime = time.time()*1000
numberOfFrames = 2000
cutoff = 80
totalTime = 0
printBoldBlue(f"Start of Recording")
cam1ConsecutiveCount_Source2 = 0
cam1ConsecutiveCount_Source1 = 0
for k in range(numberOfFrames):
    if k % 5 == 0:
        print(f"{k}")
    pic = None
    if cam1ConsecutiveCount_Source2 >= 1973 and camera1.cam2Available and camera1.cam1Available:
        camera1.cam2Available = False
        camera1.launchRenewThread(2)
        cam1ConsecutiveCount_Source2 = 0
    if cam1ConsecutiveCount_Source1 >= 1471 and camera1.cam1Available and camera1.cam2Available:
        camera1.cam1Available = False
        camera1.launchRenewThread(1)
        cam1ConsecutiveCount_Source1 = 0
    currentCam = None
    startTime = time.time()*1000
    if camera1.cam2Available:
        # _, pic = camera1.getTimedFrame(timeDuration=timeBetweenFrames, source= 2)
        _, pic = camera1.getFrame(source=2)
        cam1ConsecutiveCount_Source2 += 1
        if pic is None:
            camera1.cam2Available = False
            camera1.launchRenewThread(2)
            cam1ConsecutiveCount_Source2 = 0
        else:
            currentCam = camera1.cam2
    elif camera1.cam1Available:
        # _, pic = camera1.getTimedFrame(timeDuration=timeBetweenFrames, source=1)
        _, pic = camera1.getFrame(source=1)
        cam1ConsecutiveCount_Source1 += 1
        if pic is None:
            camera1.cam1Available = False
            camera1.launchRenewThread(1)
            cam1ConsecutiveCount_Source1 = 0
        else:
            currentCam = camera1.cam1Process

    # Code to switch to source 1 as primary source - source 2 is better
    # if camera1.cam1Available:
    #     # _, pic = camera1.getTimedFrame(timeDuration=timeBetweenFrames, source=1)
    #     _, pic = camera1.getFrame(source=1)
    #     consecutiveCount_Source1 += 1
    #     if pic is None:
    #         camera1.cam1Available = False
    #         camera1.launchRenewThread(1)
    #         consecutiveCount_Source1 = 0
    #     else:
    #         currentCam = camera1.cam1
    # elif camera1.cam2Available:
    #     # _, pic = camera1.getTimedFrame(timeDuration=timeBetweenFrames, source= 2)
    #     _, pic = camera1.getFrame(source=2)
    #     consecutiveCount_Source2 += 1
    #     if pic is None:
    #         camera1.cam2Available = False
    #         camera1.launchRenewThread(2)
    #         consecutiveCount_Source2 = 0
    #     else:
    #         currentCam = camera1.cam2
    endTime = time.time()*1000
    duration = endTime-startTime
    if pic is None:
        printBoldRed(f"{k} : {type(pic)} - {duration}")
        pic = camera2.getTimedPicture(timeDuration=(timeBetweenPictures + 0.04), source = 1)
        endTime = time.time()*1000
        duration = endTime-startTime
    if pic is None:
        printBoldRed(f"{k} : {type(pic)} - {duration}")
        pic = camera1.getRawPicture(source=1)
    if pic is not None:
        converterAndScalerForVideo.addItem(pic)
    endTime = time.time()*1000
    duration = endTime-startTime
    totalTime += duration
    if (duration > cutoff):
        delayCount += 1
        printBoldRed(f"{k} : {type(pic)} - {duration}")
        if (duration > 1.5*cutoff):
            if currentCam == camera1.cam1Process:
                camera1.cam1Available = False
                camera1.launchRenewThread(1)
                cam1ConsecutiveCount_Source1 = 0
            elif currentCam == camera1.cam2:
                camera1.cam2Available = False
                camera1.launchRenewThread(2)
                cam1ConsecutiveCount_Source2 = 0
    else:
        if k % 100 == 0:
            printBoldGreen(f"{k} : {type(pic)} - {duration}")
        pass
    # reduced_image = cv2.resize(pic, (320, 240), interpolation=cv2.INTER_AREA)
    # reduced_image = pic
    st = (cutoff - duration)/1000
    if st > 0:
        time.sleep(st)
    if pic is not None:
        # printBoldGreen(f" - {reduced_image.shape[1]} x {reduced_image.shape[0]}")
        pass
    else:
        noneCount += 1
        # printBoldRed(f"{k} : None")
printBoldBlue(f"End of Recording")
printBoldRed(f"{delayCount} pics delayed")
printBoldRed(f"{noneCount} pics were None")
endT = time.time()*1000
printBoldGreen(f"Ending - got {numberOfFrames} frames @ {round(totalTime / numberOfFrames, 2)} ms per pic")
converterAndScalerForVideo.stop()
videoWriter.stop()
converterAndScalerForVideo.join()
videoWriter.join()
printBoldGreen(f"After the join()'s")
camera1.releaseResources()
logutils.SlaveLoggers.stopSlaveLoggingThreads()
printBoldGreen(f"End of program")
exit(0)
# camera1 = ONVIFCam(IP=CosThetaConfigurator.getInstance().getCameraIP(camId),
#                         port=CosThetaConfigurator.getInstance().getCameraPort(camId),
#                         uid=CosThetaConfigurator.getInstance().getCameraUid(camId),
#                         password=CosThetaConfigurator.getInstance().getCameraPwd(camId),
#                         wsdl_dir=CosThetaConfigurator.getInstance().getWsdlDir(),
#                         name=f"Cam1_{camId}_1")
# camera6 = ONVIFCam(IP=CosThetaConfigurator.getInstance().getCameraIP(camId),
#                         port=CosThetaConfigurator.getInstance().getCameraPort(camId),
#                         uid=CosThetaConfigurator.getInstance().getCameraUidAlternate(camId),
#                         password=CosThetaConfigurator.getInstance().getCameraPwdAlternate(camId),
#                         wsdl_dir=CosThetaConfigurator.getInstance().getWsdlDir(),
#                         name=f"Cam1_{camId}_2")
# videoCamera1 = VideoCam(name=f"VideoCamera_{camId}_1", camera=camera1, consumer=converterAndScalerForVideo, sleepTime = 2 / CosThetaConfigurator.getInstance().getVideoRecordingFPS(), monitorRedisQueue=False)
# videoCamera2 = VideoCam(name=f"VideoCamera_{camId}_2", camera=camera6, consumer=converterAndScalerForVideo, sleepTime = 2 / CosThetaConfigurator.getInstance().getVideoRecordingFPS(), monitorRedisQueue=False)
# videoWriter.start()
# converterAndScalerForVideo.start()
# videoCamera1.start()
# time.sleep(1 / CosThetaConfigurator.getInstance().getVideoRecordingFPS())
# videoCamera2.start()
# print(getCurrentTime())
# time.sleep(20)
# videoCamera1.stop()
# videoCamera2.stop()
# converterAndScalerForVideo.stop()
# videoWriter.stop()
# videoCamera1.join()
# videoCamera2.join()
# converterAndScalerForVideo.join()
# videoWriter.join()
# print(getCurrentTime())
# exit(0)
