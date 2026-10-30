from typing import Any

import cv2
import os
from utils.CosThetaFileUtils import *

def extractFrames(inputFile : str, reduce : bool = False, reductiontarget : tuple = (320,240), numberToBeExtracted : int = 1000000):
    video = cv2.VideoCapture(inputFile)

    fps = video.get(cv2.CAP_PROP_FPS)
    print('frames per second = ',fps)

    parts = os.path.split(inputFile)
    print(parts[0], parts[1])
    dirPath = parts[0].replace("\\", "/")
    if reduce:
        destinationDirectory = f"{dirPath}/reduced/frames/"
    else:
        destinationDirectory = f"{dirPath}/frames/"
    createDirectory(destinationDirectory)
    fileName : str = parts[1]
    lastDotIndex = fileName.rfind(".")
    fileName = fileName[:lastDotIndex]
    print(f"Files will be saved with prefix {fileName}")
    # font = cv2.FONT_HERSHEY_SIMPLEX
    # coord
    # coord = (700, 80)
    # fontScale
    # fontScale = 1
    # Blue color in BGR
    # color = (255, 0, 0)
    # Line thickness of 2 px
    # thickness = 2
    # Using cv2.putText() method
    ret = True
    n = 1
    while ret and (n <= numberToBeExtracted):
        ret, frame = video.read()
        # startingImage = cv2.putText(frame, f"{n}", coord, font,
        #                     fontScale, color, thickness, cv2.LINE_AA)
        saveFilePath = f"{destinationDirectory}{fileName}-{n}.png"
        # startingImage = cv2.putText(frame, f"{n}", coord, font,
        #                     fontScale, color, thickness, cv2.LINE_AA)
        print(saveFilePath)
        if frame is not None:
            if reduce:
                frame  = cv2.resize(frame, (reductiontarget[0], reductiontarget[1]), interpolation=cv2.INTER_LANCZOS4)
            saveFileWithFullPath(frame, saveFilePath)
        n += 1
    video.release()
    cv2.destroyAllWindows()
    return destinationDirectory, fileName





# extractFrames(r"C:\PythonProjects\Geltec\1\pictures\2024-03-27-18-30-50-002.mp4", reduce=True, reductiontarget=(1280,720), endingNumber=20)
extractFrames(r"D:/Left Disc/2024-04-08-17-56-57-124.mp4", reduce=False)