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
recordVideo = CosThetaConfigurator.getInstance().getRecordVideo()
recordPictures = CosThetaConfigurator.getInstance().getRecordPictures()
if recordPictures:
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
    camera3 = ONVIFCam(IP=CosThetaConfigurator.getInstance().getCameraIP(camId),
                            port=CosThetaConfigurator.getInstance().getCameraPort(camId),
                            uid=CosThetaConfigurator.getInstance().getCameraUidAlternate(camId),
                            password=CosThetaConfigurator.getInstance().getCameraPwdAlternate(camId),
                            wsdl_dir=CosThetaConfigurator.getInstance().getWsdlDir(),
                            name=f"{camId}_Cam3")
    camera4 = ONVIFCam(IP=CosThetaConfigurator.getInstance().getCameraIP(camId),
                            port=CosThetaConfigurator.getInstance().getCameraPort(camId),
                            uid=CosThetaConfigurator.getInstance().getCameraUid(camId),
                            password=CosThetaConfigurator.getInstance().getCameraPwd(camId),
                            wsdl_dir=CosThetaConfigurator.getInstance().getWsdlDir(),
                            name=f"{camId}_Cam4")
    camera5 = ONVIFCam(IP=CosThetaConfigurator.getInstance().getCameraIP(camId),
                            port=CosThetaConfigurator.getInstance().getCameraPort(camId),
                            uid=CosThetaConfigurator.getInstance().getCameraUidAlternate(camId),
                            password=CosThetaConfigurator.getInstance().getCameraPwdAlternate(camId),
                            wsdl_dir=CosThetaConfigurator.getInstance().getWsdlDir(),
                            name=f"{camId}_Cam5")
    photoCamera1 = PhotoCamForRecordingInParallelRun(name=f"PhotoCamera_{camId}_1_1", cameraId = camId, camera=camera2,
                                                     consumer=converterAndScalerForPics, profileToken=1, monitorRedisQueueForStopping=False)
    photoCamera2 = PhotoCamForRecordingInParallelRun(name=f"PhotoCamera_{camId}_2_1", cameraId = camId, camera=camera3,
                                                     consumer=converterAndScalerForPics, profileToken=1, monitorRedisQueueForStopping=False)
    photoCamera3 = PhotoCamForRecordingInParallelRun(name=f"PhotoCamera_{camId}_1_2", cameraId = camId, camera=camera4,
                                                     consumer=converterAndScalerForPics, profileToken=2, monitorRedisQueueForStopping=False)
    photoCamera4 = PhotoCamForRecordingInParallelRun(name=f"PhotoCamera_{camId}_2_2", cameraId = camId, camera=camera5,
                                                     consumer=converterAndScalerForPics, profileToken=2, monitorRedisQueueForStopping=False)
    pictureWriter.start()
    converterAndScalerForPics.start()
    photoCamera1.start()
    time.sleep(CosThetaConfigurator.getInstance().getSleepSecondsForPictureRecording() * 0.23)
    photoCamera2.start()
    time.sleep(CosThetaConfigurator.getInstance().getSleepSecondsForPictureRecording() * 0.23)
    photoCamera3.start()
    time.sleep(CosThetaConfigurator.getInstance().getSleepSecondsForPictureRecording() * 0.23)
    photoCamera4.start()

if recordVideo:
    videoRecordingFolder = CosThetaConfigurator.getInstance().getVideosFolderForRecording(camId)
    videoRecordingFolder = f"{proj_root}/{videoRecordingFolder}"
    createDirectory(videoRecordingFolder)
    videoRecordingFile = f"{videoRecordingFolder}/{getCurrentTime()}.mp4"
    videoFPS = CosThetaConfigurator.getInstance().getVideoRecordingFPS()
    videoWriter = ImageWriter(name=f"VideoWriter{camId}", path = videoRecordingFile, fps = videoFPS, debugWrite=True)
    converterAndScalerForVideo = Bytes2ImageAndScaling(name=f"VideosConverterAndScaler{camId}",
                                                            consumer=videoWriter)
    camera1 = ONVIFCam(IP=CosThetaConfigurator.getInstance().getCameraIP(camId),
                            port=CosThetaConfigurator.getInstance().getCameraPort(camId),
                            uid=CosThetaConfigurator.getInstance().getCameraUid(camId),
                            password=CosThetaConfigurator.getInstance().getCameraPwd(camId),
                            wsdl_dir=CosThetaConfigurator.getInstance().getWsdlDir(),
                            name=f"{camId}_Cam1")
    videoCamera1 = VideoCam(name=f"VideoCamera_{camId}", camera=camera1, consumer=converterAndScalerForVideo,
                            monitorRedisQueueForStopping=False)
    videoWriter.start()
    converterAndScalerForVideo.start()
    videoCamera1.start()

print(getCurrentTime())
time.sleep(60)
if recordPictures:
    photoCamera1.stop()
    photoCamera2.stop()
    photoCamera3.stop()
    photoCamera4.stop()
    converterAndScalerForPics.stop()
    pictureWriter.stop()

if recordVideo:
    videoCamera1.stop()
    converterAndScalerForVideo.stop()
    videoWriter.stop()

if recordPictures:
    photoCamera1.join()
    photoCamera2.join()
    photoCamera3.join()
    photoCamera4.join()
    pictureWriter.join()
    converterAndScalerForPics.join()
    camera2.releaseResources()
    camera3.releaseResources()
    camera4.releaseResources()
    camera5.releaseResources()

if recordVideo:
    videoCamera1.join()
    converterAndScalerForVideo.join()
    videoWriter.join()
    camera1.releaseResources()

logutils.SlaveLoggers.stopSlaveLoggingThreads()
print(getCurrentTime())
exit(0)
