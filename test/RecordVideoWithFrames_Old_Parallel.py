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
videoFPS = CosThetaConfigurator.getInstance().getVideoRecordingFPS()
videoWriter = ImageWriter(name=f"VideoWriter{camId}", path = videoRecordingFile, fps = videoFPS, debugWrite=True)
converterAndScalerForVideo = Bytes2ImageAndScaling(name=f"VideosConverterAndScaler{camId}",
                                                        consumer=videoWriter)
videoWriter.start()
converterAndScalerForVideo.start()
cam1 = ONVIFCam("192.168.1.100", 80, "admin", "abcd1234", "C:/CosTheta/GelTec/onvif/ver10/")
printBoldGreen(cam1)
cam2 = ONVIFCam("192.168.1.100", 80, "costheta", "abcd1234", "C:/CosTheta/GelTec/onvif/ver10/")
printBoldGreen(cam2)
cam1ConsecutiveCount_Source2 = 0
cam1ConsecutiveCount_Source1 = 0
cam2ConsecutiveCount_Source2 = 0
cam2ConsecutiveCount_Source1 = 0
delayCount = 0
noneCount = 0
currTime = time.time()*1000
r = 100
cutoff = 38
printBoldBlue(f"Start of Recording")
for k in range(r):
    pic = None
    if cam1ConsecutiveCount_Source2 == 1009:
        cam1.cam2Available = False
        cam1.launchRenewThread(2)
        cam1ConsecutiveCount_Source2 = 0
    if cam1ConsecutiveCount_Source1 == 1471:
        cam1.cam1Available = False
        cam1.launchRenewThread(1)
        cam1ConsecutiveCount_Source1 = 0
    if cam2ConsecutiveCount_Source2 == 1471:
        cam2.cam2Available = False
        cam2.launchRenewThread(2)
        cam2ConsecutiveCount_Source2 = 0
    if cam2ConsecutiveCount_Source1 == 1009:
        cam2.cam1Available = False
        cam2.launchRenewThread(1)
        cam2ConsecutiveCount_Source1 = 0
    currentCam = None
    startTime = time.time()*1000
    if k % 2 == 0:
        if cam1.cam2Available:
            _, pic = cam1.getFrame(2)
            cam1ConsecutiveCount_Source2 += 1
            if pic is None:
                cam1.cam2Available = False
                cam1.launchRenewThread(2)
                cam1ConsecutiveCount_Source2 = 0
            else:
                currentCam = cam1.cam2
        elif cam2.cam2Available:
            _, pic = cam2.getFrame(2)
            cam2ConsecutiveCount_Source2 += 1
            if pic is None:
                cam2.cam2Available = False
                cam2.launchRenewThread(2)
                cam2ConsecutiveCount_Source2 = 0
            else:
                currentCam = cam2.cam2
        elif cam1.cam1Available:
            _, pic = cam1.getFrame(1)
            cam1ConsecutiveCount_Source1 += 1
            if pic is None:
                cam1.cam1Available = False
                cam1.launchRenewThread(1)
                cam1ConsecutiveCount_Source1 = 0
            else:
                currentCam = cam1.cam1Process
        else:
            _, pic = cam2.getFrame(1)
            cam2ConsecutiveCount_Source1 += 1
            if pic is None:
                cam2.cam1Available = False
                cam2.launchRenewThread(1)
                cam2ConsecutiveCount_Source1 = 0
            else:
                currentCam = cam2.cam1Process
    else:
        # if cam2.cam2Available:
        #     _, pic = cam2.getFrame(2)
        # else:
        #     _, pic = cam1.getFrame(2)
        if cam2.cam2Available:
            _, pic = cam2.getFrame(2)
            cam2ConsecutiveCount_Source2 += 1
            if pic is None:
                cam2.cam2Available = False
                cam2.launchRenewThread(2)
                cam2ConsecutiveCount_Source2 = 0
            else:
                currentCam = cam2.cam2
        elif cam1.cam2Available:
            _, pic = cam1.getFrame(2)
            cam1ConsecutiveCount_Source2 += 1
            if pic is None:
                cam1.cam2Available = False
                cam1.launchRenewThread(2)
                cam1ConsecutiveCount_Source2 = 0
            else:
                currentCam = cam1.cam2
        elif cam2.cam1Available:
            _, pic = cam2.getFrame(1)
            cam2ConsecutiveCount_Source1 += 1
            if pic is None:
                cam2.cam1Available = False
                cam2.launchRenewThread(1)
                cam2ConsecutiveCount_Source1 = 0
            else:
                currentCam = cam2.cam1Process
        else:
            _, pic = cam1.getFrame(1)
            cam1ConsecutiveCount_Source1 += 1
            if pic is None:
                cam1.cam1Available = False
                cam1.launchRenewThread(1)
                cam1ConsecutiveCount_Source1 = 0
            else:
                currentCam = cam1.cam1Process
    if pic is not None:
        converterAndScalerForVideo.addItem(pic)
    endTime = time.time()*1000
    duration = endTime-startTime
    if (duration > cutoff):
        delayCount += 1
        printBoldRed(f"{k} : {type(pic)} - {duration}")
        if currentCam == cam1.cam1Process:
            cam1.cam1Available = False
            cam1.launchRenewThread(1)
            cam1ConsecutiveCount_Source1 = 0
        elif currentCam == cam1.cam2:
            cam1.cam2Available = False
            cam1.launchRenewThread(2)
            cam1ConsecutiveCount_Source2 = 0
        elif currentCam == cam2.cam1Process:
            cam2.cam1Available = False
            cam2.launchRenewThread(1)
            cam2ConsecutiveCount_Source1 = 0
        elif currentCam == cam2.cam2:
            cam2.cam2Available = False
            cam2.launchRenewThread(2)
            cam2ConsecutiveCount_Source2 = 0
    else:
        # printBoldGreen(f"{k} : {type(pic)}")
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
printBoldGreen(f"Ending - got {r} frames @ {round((endT - currTime) / r, 2)} ms per pic")
converterAndScalerForVideo.stop()
videoWriter.stop()
converterAndScalerForVideo.join()
videoWriter.join()
printBoldGreen(f"After the join()'s")
cam1.releaseResources()
cam2.releaseResources()
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
