from camera.PhotoCamera import *
from camera.VideoCamera import *
from old.VideoCameraForRecording_Old import *
from processors.image.Bytes2ImageAndScaler import *
from utils.CosThetaFileUtils import *
from logutils.SlaveLoggers import *
# from VerifyIntegrity import *

import warnings
warnings.filterwarnings('ignore', '.*h264.*', )

from Configuration import *
CosThetaConfigurator.getInstance()

# verifyIntegrity()

'''
0) Install Hikvision Tools Manager
1) Install SADP and set password as admin and admin. Enable 'Hik-vision Connect' and 'port'
2) Double-click on SADP row to open a browser and see the camera output
3) Check maikl for documeht 'Hikvision PZTZ settings'


a) Go to Configurations -> Network -> Integration Protocol. Enable ONVIF. Then, create a newwidgets user with admin and abcd1234
b) Go to that part which is creating a problem and overwrite the throws clause with 'returns xmlvalue'
c) Clone the github in a directory and install on the machine using 'python setup.py install'. 
   This will ensure that the wsdls are installed in the correct directory and available to all the calls 
d) When you create a project, ensure that the venv has both options ticked for 'inherit global site-packages' and 'make available to all projects'   
'''

class MainRecorder():

    startCommand = CosThetaConfigurator.getInstance().getStartCommand()
    stopCommand = CosThetaConfigurator.getInstance().getStopCommand()
    exitCommand = CosThetaConfigurator.getInstance().getExitCommand()

    logSource = getFullyQualifiedName(__file__)

    def __init__(self, camId : int = 1):
        super().__init__(self)
        MainRecorder.logSource = getFullyQualifiedName(__file__, __class__)
        self.camId = camId
        self.proj_root = get_project_root()
        self.recordingStarted = False
        self.exitProgram = False
        self.recordingQueue = CosThetaConfigurator.getInstance().getRecordingQueue(self.camId)
        self.redisConnection = None
        self.clientRedisConnected = False
        self.connectToRedis(forceRenew=True)
        self.launchLookForStartRecordingCommand()

    def doRecording(self):
        self.recordVideo = CosThetaConfigurator.getInstance().getRecordVideo()
        self.recordPictures = CosThetaConfigurator.getInstance().getRecordPictures()
        if self.recordPictures:
            self.picRecordingFolder = CosThetaConfigurator.getInstance().getPicturesFolderForRecording(self.camId)
            self.picRecordingFolder = f"{self.proj_root}/{self.picRecordingFolder}"
            createDirectory(self.picRecordingFolder)
            self.picRecordingFile = f"{self.picRecordingFolder}/{getCurrentTime()}.mp4"
            self.pictureFPS = round((1 / CosThetaConfigurator.getInstance().getSleepSecondsForPictureRecording()) * 4,2)
            self.pictureWriter = ImageWriter(name=f"PictureWriter{self.camId}", path = self.picRecordingFile, fps = self.pictureFPS, debugWrite=True)
            self.converterAndScalerForPics = Bytes2ImageAndScaling(name=f"PicsConverterAndScaler{self.camId}", consumer=self.pictureWriter)
            self.camera2 = ONVIFCam(IP=CosThetaConfigurator.getInstance().getCameraIP(self.camId),
                                    port=CosThetaConfigurator.getInstance().getCameraPort(self.camId),
                                    uid=CosThetaConfigurator.getInstance().getCameraUid(self.camId),
                                    password=CosThetaConfigurator.getInstance().getCameraPwd(self.camId),
                                    wsdl_dir=CosThetaConfigurator.getInstance().getWsdlDir(),
                                    name=f"{self.camId}_Cam2")
            self.camera3 = ONVIFCam(IP=CosThetaConfigurator.getInstance().getCameraIP(self.camId),
                                    port=CosThetaConfigurator.getInstance().getCameraPort(self.camId),
                                    uid=CosThetaConfigurator.getInstance().getCameraUidAlternate(self.camId),
                                    password=CosThetaConfigurator.getInstance().getCameraPwdAlternate(self.camId),
                                    wsdl_dir=CosThetaConfigurator.getInstance().getWsdlDir(),
                                    name=f"{self.camId}_Cam3")
            self.camera4 = ONVIFCam(IP=CosThetaConfigurator.getInstance().getCameraIP(self.camId),
                                    port=CosThetaConfigurator.getInstance().getCameraPort(self.camId),
                                    uid=CosThetaConfigurator.getInstance().getCameraUid(self.camId),
                                    password=CosThetaConfigurator.getInstance().getCameraPwd(self.camId),
                                    wsdl_dir=CosThetaConfigurator.getInstance().getWsdlDir(),
                                    name=f"{self.camId}_Cam4")
            self.camera5 = ONVIFCam(IP=CosThetaConfigurator.getInstance().getCameraIP(self.camId),
                                    port=CosThetaConfigurator.getInstance().getCameraPort(self.camId),
                                    uid=CosThetaConfigurator.getInstance().getCameraUidAlternate(self.camId),
                                    password=CosThetaConfigurator.getInstance().getCameraPwdAlternate(self.camId),
                                    wsdl_dir=CosThetaConfigurator.getInstance().getWsdlDir(),
                                    name=f"{self.camId}_Cam5")
            self.photoCamera1 = PhotoCamForRecordingInParallelRun(name=f"PhotoCamera_{self.camId}_1_1", cameraId = self.camId, camera=self.camera2, consumer=self.converterAndScalerForPics, profileToken=1, monitorRedisQueueForStopping=False)
            self.photoCamera2 = PhotoCamForRecordingInParallelRun(name=f"PhotoCamera_{self.camId}_2_1", cameraId = self.camId, camera=self.camera3, consumer=self.converterAndScalerForPics, profileToken=1, monitorRedisQueueForStopping=False)
            self.photoCamera3 = PhotoCamForRecordingInParallelRun(name=f"PhotoCamera_{self.camId}_1_2", cameraId = self.camId, camera=self.camera4, consumer=self.converterAndScalerForPics, profileToken=2, monitorRedisQueueForStopping=False)
            self.photoCamera4 = PhotoCamForRecordingInParallelRun(name=f"PhotoCamera_{self.camId}_2_2", cameraId = self.camId, camera=self.camera5, consumer=self.converterAndScalerForPics, profileToken=2, monitorRedisQueueForStopping=False)
            self.pictureWriter.start()
            self.converterAndScalerForPics.start()
            self.photoCamera1.start()
            time.sleep(CosThetaConfigurator.getInstance().getSleepSecondsForPictureRecording() * 0.23)
            self.photoCamera2.start()
            time.sleep(CosThetaConfigurator.getInstance().getSleepSecondsForPictureRecording() * 0.23)
            self.photoCamera3.start()
            time.sleep(CosThetaConfigurator.getInstance().getSleepSecondsForPictureRecording() * 0.23)
            self.photoCamera4.start()

        if self.recordVideo:
            self.videoRecordingFolder = CosThetaConfigurator.getInstance().getVideosFolderForRecording(self.camId)
            self.videoRecordingFolder = f"{self.proj_root}/{self.videoRecordingFolder}"
            createDirectory(self.videoRecordingFolder)
            self.videoRecordingFile = f"{self.videoRecordingFolder}/{getCurrentTime()}.mp4"
            self.videoFPS = CosThetaConfigurator.getInstance().getVideoRecordingFPS()
            self.videoWriter = ImageWriter(name=f"VideoWriter{self.camId}", path = self.videoRecordingFile, fps = self.videoFPS, debugWrite=True)
            self.converterAndScalerForVideo = Bytes2ImageAndScaling(name=f"VideosConverterAndScaler{self.camId}",
                                                                   consumer=self.videoWriter)
            self.camera1 = ONVIFCam(IP=CosThetaConfigurator.getInstance().getCameraIP(self.camId),
                                    port=CosThetaConfigurator.getInstance().getCameraPort(self.camId),
                                    uid=CosThetaConfigurator.getInstance().getCameraUid(self.camId),
                                    password=CosThetaConfigurator.getInstance().getCameraPwd(self.camId),
                                    wsdl_dir=CosThetaConfigurator.getInstance().getWsdlDir(),
                                    name=f"{self.camId}_Cam1")
            self.videoCamera1 = VideoCam(name=f"VideoCamera_{self.camId}", camera=self.camera1, consumer=self.converterAndScalerForVideo,
                                         monitorRedisQueueForStopping=False)
            self.videoWriter.start()
            self.converterAndScalerForVideo.start()
            self.videoCamera1.start()

        self.recordingStarted = True
        if not self.exitProgram:
            self.launchLookForStopRecordingCommand()

    def stopRecording(self):
        if self.recordPictures:
            self.photoCamera1.stop()
            self.photoCamera2.stop()
            self.photoCamera3.stop()
            self.photoCamera4.stop()
            self.converterAndScalerForPics.stop()
            self.pictureWriter.stop()

        if self.recordVideo:
            self.videoWriter.stop()
            self.converterAndScalerForVideo.stop()
            self.videoCamera1.stop()

        if self.recordPictures:
            self.photoCamera1.join()
            self.photoCamera2.join()
            self.photoCamera3.join()
            self.photoCamera4.join()
            self.converterAndScalerForPics.join()
            self.pictureWriter.join()
            self.camera2.releaseResources()
            self.camera3.releaseResources()
            self.camera4.releaseResources()
            self.camera5.releaseResources()

        if self.recordVideo:
            self.videoWriter.join()
            self.converterAndScalerForVideo.join()
            self.videoCamera1.join()
            self.camera1.releaseResources()

        self.recordingStarted = False
        if not self.exitProgram:
            self.launchLookForStartRecordingCommand()

    def connectToRedis(self, forceRenew = False):
        if forceRenew:
            self.redisConnection = None
            self.clientRedisConnected = False
        if not self.clientRedisConnected:
            try:
                self.redisConnection = Redis(GenericQueueProcessor.redisHost, GenericQueueProcessor.redisPort, retry_on_timeout=True)
                self.clientRedisConnected = True
            except:
                self.clientRedisConnected = False
                SlaveFileLogger.getInstance().logTakeAction(MainRecorder.logSource,
                                                            f'Could not get Redis Connection in {MainRecorder.logSource}',
                                                          Logger.ISSUE)
                SlaveConsoleLogger.getInstance().logTakeAction(MainRecorder.logSource,
                                                               f'Could not get Redis Connection in {MainRecorder.logSource}',
                                                               Logger.ISSUE)

    def lookForStartRecordingCommand(self):
        done = False
        while not done:
            command = getRecordingCommand(self.redisConnection, self.recordingQueue)
            time.sleep(0.1)
            if command == MainRecorder.startCommand:
                self.recordingStarted = True
                self.doRecording()
                done = True
            elif command == MainRecorder.exitCommand:
                self.exitProgram = True
                if self.recordingStarted:
                    self.stopRecording()
                self.exit()
                done = True
        return

    def launchLookForStartRecordingCommand(self):
        self.llfsrc = threading.Thread(name=f'Recording Command Thread 1 - MainRecorder.{self.name}',
                                       target=self.lookForStartRecordingCommand,
                                       args=(), daemon=True)
        self.llfsrc.start()
        self.llfsrc.join()
        return

    def lookForStopRecordingCommand(self):
        done = False
        while not done:
            command = getRecordingCommand(self.redisConnection, self.recordingQueue)
            time.sleep(0.1)
            if command == MainRecorder.stopCommand:
                if self.recordingStarted:
                    self.stopRecording()
                done = True
            elif command == MainRecorder.exitCommand:
                self.exitProgram = True
                if self.recordingStarted:
                    self.stopRecording()
                self.exit()
                done = True
        return

    def launchLookForStopRecordingCommand(self):
        self.llfsrc = threading.Thread(name=f'Recording Command Thread 1 - MainRecorder.{self.name}',
                                       target=self.lookForStopRecordingCommand,
                                       args=(), daemon=True)
        self.llfsrc.start()
        self.llfsrc.join()
        return

    def exit(self):
        cv2.destroyAllWindows()
        stopSlaveLoggingThreads()
        exit(0)

# if __name__ == '__main__':
#     passedArgs = getCommandLineArgumentsAsDictionary()
#     if len(passedArgs.keys()) > 0:
#         cameraId = 1
#         try:
#             cameraId = int(passedArgs["cameraId"])
#         except:
#             cameraId = 1
#         mainRecorder = MainRecorder(cameraId)