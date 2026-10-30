
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

logSource = getFullyQualifiedName(__file__)

print(getCurrentTime())
camId = 2
proj_root = get_project_root()
videoRecordingFolder = CosThetaConfigurator.getInstance().getVideosFolderForRecording(camId)
videoRecordingFolder = f"{proj_root}/{videoRecordingFolder}"
createDirectory(videoRecordingFolder)
printBoldBlue(f"Video recording folder is {videoRecordingFolder}")

videoRecordingFile = f"{videoRecordingFolder}{getCurrentTime()}.mp4"
printBoldBlue(f"Recording to {videoRecordingFile}")

# videoFPS = CosThetaConfigurator.getInstance().getVideoRecordingFPS()
videoFPS = 20
timeBetweenFrames = 1.0/videoFPS
print(f"{timeBetweenFrames = }")

videoWriter = ImageWriter(name=f"VideoWriter{camId}", path=videoRecordingFile, fps=videoFPS, width=1920, height = 1080, debugWrite=True)
videoWriter.start()

# cam1 = ONVIFCam("192.168.1.100", 80, "admin", "abcd1234", "C:/CosTheta/GelTec/onvif/ver10/")
printBoldBlue(f"Attempting to connect to camera at IP {CosThetaConfigurator.getInstance().getCameraIP(camId)}")
camera1 = ONVIFCam(IP=CosThetaConfigurator.getInstance().getCameraIP(camId),
                        port=CosThetaConfigurator.getInstance().getCameraPort(camId),
                        uid=CosThetaConfigurator.getInstance().getCameraUid(camId),
                        password=CosThetaConfigurator.getInstance().getCameraPwd(camId),
                        wsdl_dir=CosThetaConfigurator.getInstance().getWsdlDir(),
                        name=f"{camId}_Cam1")
printBoldGreen(camera1)

if camera1.connected:
    camera1.getRawPicture()
    camera1.getRawPicture()
    for i in range(2):
        try:
            camera1.getFrame()
        except Exception as e:
            SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                           f'Could not getFrame due to {e}', Logger.PROBLEM)
            SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                        f'Could not getFrame() due to {e}', Logger.PROBLEM)
if camera1.connected:
    printBoldGreen(f"Ready to process images in Camera {camId}")
    SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                f'Ready to process images in Camera {camId}',
                                                Logger.SUCCESS)
    SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                   f'Ready to process images in Camera {camId}',
                                                   Logger.SUCCESS)
else:
    printBoldRed(f"Camera {camId} is NOT available for processing images")
    SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                f'Camera {camId} is NOT available for processing images. Unable to process images in Camera {camId}',
                                                Logger.PROBLEM)
    SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                   f'Camera {camId} is NOT available for processing images. Unable to process images in Camera {camId}',
                                                   Logger.PROBLEM)

timeOfRecording = 48 # secs
numberOfFrames = timeOfRecording * videoFPS
pic = None
noneCount = 0

printBoldBlue(f"Starting Recording")
startT = time.time()*1000
gotFirstFrame = False

for k in range(numberOfFrames):
    if k % 20 == 0:
        printLight(f"Getting frame {k}")
    startTime = time.time()
    _, pic = camera1.getFrame()

    if pic is None:
        # printBoldRed(f"None pic at frame {k}")
        pass
    else:
        # printBoldGreen(f"Frame is of type {type(pic)} and its dimensions are {pic.shape}")
        if not gotFirstFrame:
            printBoldGreen(f"Got first frame at number: {k}")
            gotFirstFrame = True
        videoWriter.addItem(pic)
        cv2.imshow("Recording these Frames", pic)
        if cv2.waitKey(1) == ord('q'):
            break
    endTime = time.time()
    duration = endTime-startTime
    st = (timeBetweenFrames - duration)
    if st > 0:
        time.sleep(st)
    if pic is not None:
        pass
    else:
        noneCount += 1

printBoldBlue(f"End of Recording")
printBoldRed(f"{noneCount} pics were None")
endT = time.time()*1000
totalTime = endT - startT
printBoldGreen(f"Ending - got {numberOfFrames} frames @ {round(totalTime / numberOfFrames, 2)} ms per pic")
videoWriter.stop()
videoWriter.join()
printBoldGreen(f"After the join()'s")

camera1.releaseResources()
logutils.SlaveLoggers.stopSlaveLoggingThreads()
printBoldGreen(f"End of program")
cv2.destroyAllWindows()
exit(0)
