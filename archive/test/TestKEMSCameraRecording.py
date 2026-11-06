import ffmpeg
from pathlib import *
from test.TestKEMSCamera import *
import cv2

projectRoot = None
def get_project_root() -> Path:
    global projectRoot
    if projectRoot == None:
        projectRoot =str(Path(__file__).parent.parent.parent)
        projectRoot = projectRoot.replace("\\","/")
    print(f'Project root is {projectRoot}')
    return projectRoot

def getCurrentTime():
    msTime = datetime.now().strftime('%Y-%m-%d-%H-%M-%S.%f')
    (dt, ms) = msTime.split('.')
    ms = "{:03d}".format(int(ms) // 1000)
    currentTime = f'{dt}-{ms}'
    return currentTime

def createDirectory(sourceDirectory : str):
    # sourceDirectory should be either path to a file OR end in '/'
    # print(f"Called with {sourceDirectory}")
    import os
    if sourceDirectory.endswith("/"):
        try:
            targetDirectory = sourceDirectory[:-1]
            # print(f"{targetDirectory = }")
            os.makedirs(f"{targetDirectory}", exist_ok=True)
        except Exception as e:
            print(e)
            pass
        return
    parts = os.path.split(sourceDirectory)
    destinationDirectory = parts[0]
    # print(f"{parts[0] = }")
    if (not destinationDirectory.startswith("/")) and (not ":/" in destinationDirectory):
        destinationDirectory = get_project_root() + "/" + destinationDirectory
    # print(f"{destinationDirectory = }")
    if not destinationDirectory.endswith("/"):
        destinationDirectory += "/"
    if not "." in parts[1]:
        destinationDirectory = destinationDirectory + parts[1] + "/"
    # printBoldGreen(f"Creating {destinationDirectory}")
    try:
        os.makedirs(destinationDirectory, exist_ok=True)
    except Exception as e:
        print(e)
        pass

print(getCurrentTime())
proj_root=get_project_root()
picRecordingFolder = "1/pictures"
picRecordingFolder = f"{proj_root}/{picRecordingFolder}/"
createDirectory(picRecordingFolder)
picRecordingFile = f"{picRecordingFolder}{getCurrentTime()}.mp4"
print(picRecordingFile)
# pictureFPS = 14
# print(pictureFPS)
camera2 = ONVIFCam(IP="169.254.29.130", port=80, uid="admin", password="abcd1234", wsdl_dir="D:/KEMS_CotterPin_GC_QRC_T/Lib/site-packages/wsdl", name=f"{1}_Cam2")
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
    else:
        pass

print(f"{delayCount} pics delayed")
endT = time.time()*1000
averageTime = round((endT - currTime) / r, 2)
print(f"Ending - got {r} frames @ {averageTime} ms per pic")
averageTime = averageTime/1000

process = (
    ffmpeg
        .input('pipe:', format='rawvideo', pix_fmt='rgb24', s='{}x{}'.format(1280, 720))
        .output(picRecordingFile, pix_fmt='yuv420p', vcodec='libx264', r=14, crf=37, loglevel="quiet")
        .overwrite_output()
        .run_async(pipe_stdin=True)
)


print(f"{getCurrentTime()} - STARTING RECORDING")
for i in range(200):
    pic = camera2.getPicture((i%2) + 1)
    process.stdin.write(
        cv2.cvtColor(pic, cv2.COLOR_BGR2RGB)
            .astype(np.uint8)
            .tobytes()
    )

process.stdin.close()
process.wait()
camera2.releaseResources()
print(f"{getCurrentTime()} - ENDING RECORDING")
exit(0)
