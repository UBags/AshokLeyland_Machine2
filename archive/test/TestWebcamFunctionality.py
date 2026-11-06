# to see list of video sources, use v4l2-ctl --list-devices
# lsusb shows the names of all the devices connected t

import cv2
import time
import threading
from threading import Thread
import ffmpeg
from datetime import datetime
import numpy as np
from pathlib import *

def getCurrentTime():
    msTime = datetime.now().strftime('%Y-%m-%d-%H-%M-%S.%f')
    (dt, ms) = msTime.split('.')
    ms = "{:03d}".format(int(ms) // 1000)
    currentTime = f'{dt}-{ms}'
    return currentTime

def showVideo(video_source : int = 0, framename : str = 'Webcam', reportFrameCaptureRate : bool = False):
    video = cv2.VideoCapture(video_source) # 0 = First connected camera / inbuilt camera, 1 = next, and so on
    cv2.namedWindow(framename, cv2.WINDOW_NORMAL)
    totalTime = 0
    totalFrames = 0
    while True:
        starttime = time.time()*1000
        isTrue, frame = video.read()
        endtime = time.time() * 1000
        cv2.imshow(framename, frame)
        # print(frame.shape[1], frame.shape[0])
        if isTrue:
            totalTime += endtime - starttime
            totalFrames += 1
        key = cv2.waitKey(5)
        if (key%256 == 27):
            break
    if reportFrameCaptureRate:
        print(f"Received {totalFrames} frames @ {round(totalTime / totalFrames, 2)} ms per frame")
    video.release()
    cv2.destroyAllWindows()

def checkReleaseAndCaptureTimings(video_source : int = 0, nTimes : int = 100):
    totalTimeCapture = 0
    totalTimeRelease = 0
    for i in range(nTimes):
        print(i)
        starttime = time.time()*1000
        video = cv2.VideoCapture(video_source)
        endtime = time.time()*1000
        totalTimeCapture += endtime - starttime
        for j in range(5):
            isTrue, frame = video.read()
        starttime = time.time()*1000
        video.release()
        cv2.destroyAllWindows()
        endtime = time.time()*1000
        totalTimeRelease += endtime - starttime
    print(f"Video capturing @ {round(totalTimeCapture / nTimes, 2)} ms per capture")
    print(f"Video release @ {round(totalTimeRelease / nTimes, 2)} ms per release")

def stressTestLogitech(video_source : int = 0, nTimes : int = 1):
    totalTimeCapture = 0
    totalTimeRelease = 0
    okFrames = 0
    repeats = 100
    beginningTime = time.time()*1000
    for i in range(nTimes):
        print(i)
        starttime = time.time() * 1000
        video = cv2.VideoCapture(video_source)
        endtime = time.time() * 1000
        totalTimeCapture += endtime - starttime
        for j in range(repeats):
            isTrue, frame = video.read()
            if isTrue:
                okFrames += 1
                # print(frame.shape)
        starttime = time.time() * 1000
        video.release()
        cv2.destroyAllWindows()
        endtime = time.time() * 1000
        totalTimeRelease += endtime - starttime
    duration = time.time()*1000 - beginningTime - totalTimeCapture - totalTimeRelease
    print(f"Number of OK frames : {okFrames}, %age wise = {round(okFrames * 100 / (nTimes * repeats), 2)}%, received @ {round(duration / (nTimes * repeats), 2)} ms per picture frame")
    print(f"Video capturing     : @ {round(totalTimeCapture / nTimes, 2)} ms per connection capture")
    print(f"Video release       : @ {round(totalTimeRelease / nTimes, 2)} ms per connection release")


def captureVideo(video_source : int = 0, dimensions : tuple = (640, 480), recordingTime :int = 30, reportFrameCaptureRate : bool = False):
    # On Windows, 0 = First connected camera / inbuilt camera, 1 = next, and so on
    # On Linux, find videos sources with v4l2-ctl --list-devices
    # On Linux, install GSTREAMER and use cv2.CAP_GSTREAMER; on Windows, install DirectShow and use cv2.CAP_DSHOW
    # on Linux, use uvcdynctrl -f to get available resolutions and frame rates
    video = cv2.VideoCapture(video_source, cv2.CAP_V4L2)
    video.set(cv2.CAP_PROP_FRAME_WIDTH, dimensions[0])
    video.set(cv2.CAP_PROP_FRAME_WIDTH, dimensions[1])
    video.set(cv2.CAP_PROP_FPS, 30)

    totalTime = 0
    totalFrames = 0
    path = f"{getCurrentTime()}.mp4"
    process = (
        ffmpeg
        .input('pipe:', format='rawvideo', pix_fmt='rgb24', s='{}x{}'.format(dimensions[0], dimensions[1]))
        .output(path, pix_fmt='yuv420p', vcodec='libx264', bitrate="8M", r=25, crf=15, loglevel="quiet")
        .overwrite_output()
        .run_async(pipe_stdin=True)
    )
    # process = (
    #     ffmpeg
    #     .input('pipe:', format='rawvideo', pix_fmt='rgb24', s='{}x{}'.format(dimensions[0], dimensions[1]))
    #     .output(path, pix_fmt='yuv420p', vcodec='h264_amf', bitrate="8M", r=20, crf=15, loglevel="quiet", preset='fast')
    #     .overwrite_output()
    #     .run_async(pipe_stdin=True)
    # )
    print(f"Starting recording to : {Path(path).absolute()}")
    videostartat = time.time()
    timealreadyreported = 0
    while True:
        starttime = time.time()*1000
        isTrue, frame = video.read()
        endtime = time.time() * 1000
        timetobereported = int(time.time()-videostartat)
        if timetobereported == timealreadyreported:
            pass
        else:
            timealreadyreported = timetobereported
            print(f"Seconds elapsed = {timealreadyreported}")
        if isTrue:
            # print(frame.shape)
            totalTime += endtime - starttime
            totalFrames += 1
            cv2.imshow("Recording frames", frame)
            if frame is not None:
                frame = cv2.resize(frame, (dimensions[0], dimensions[1]), cv2.INTER_LANCZOS4)
                process.stdin.write(
                    cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    .astype(np.uint8)
                    .tobytes()
                )
        if timetobereported == recordingTime:
            break
        key = cv2.waitKey(1)
        if (key % 256 == 27):
            break
    print("Ended the recording")
    process.stdin.write('q'.encode("GBK"))  # Simulate user pressing 'q' key
    process.communicate()
    # process.stdin.close()
    process.wait()
    if reportFrameCaptureRate:
        print(f"Received {totalFrames} frames @ {round(totalTime / totalFrames, 2)} ms per frame")
    video.release()
    cv2.destroyAllWindows()

# print(cv2.getBuildInformation())
# Simultaneous stress test
# ------------------------
# stress1 = threading.Thread(target=stressTestLogitech, args=('/dev/video0',20))
# stress2 = threading.Thread(target=stressTestLogitech, args=('/dev/video2',20))
#
# stress1.start()
# stress2.start()
# stress1.join()
# stress2.join()


# Test Video Capture
# ------------------
captureVideo('/dev/video2',recordingTime = 30, reportFrameCaptureRate=True)

# capture1 = threading.Thread(target=captureVideo, args=('/dev/video0',True))
# capture2 = threading.Thread(target=captureVideo, args=('/dev/video2',True))
#
# capture1.start()
# capture2.start()
# capture1.join()
# capture2.join()


# checkReleaseAndCaptureTimings('/dev/video0')
# stressTestLogitech('/dev/video2', 3)
# showVideo('/dev/video2', reportFrameCaptureRate = True)
exit(0)

# For Windows
# from pygrabber.dshow_graph import FilterGraph
#
# graph = FilterGraph()
# print(graph.get_input_devices())

# index = 0
# arr = []
# while True:
#     print("About to call VideoCapture()")
#     cap = cv2.VideoCapture(index)
#     print("Finished calling VideoCapture()")
#     try:
#         if cap.getBackendName()=="MSMF":
#             arr.append(index)
#     except:
#         break
#     cap.release()
#     index += 1
#
# print(arr)

# import device
# import cv2
#
#
# def select_camera(last_index):
#     number = 0
#     hint = "Select a camera (0 to " + str(last_index) + "): "
#     try:
#         number = int(input(hint))
#         # select = int(select)
#     except Exception:
#         print("It's not a number!")
#         return select_camera(last_index)
#
#     if number > last_index:
#         print("Invalid number! Retry!")
#         return select_camera(last_index)
#
#     return number
#
#
# def open_camera(index):
#     cap = cv2.VideoCapture(index)
#     return cap
#
#
# def main():
#     # print OpenCV version
#     print("OpenCV version: " + cv2.__version__)
#
#     # Get camera list
#     device_list = device.getDeviceList()
#     index = 0
#
#     for camera in device_list:
#         print(str(index) + ': ' + camera[0] + ' ' + str(camera[1]))
#         index += 1
#
#     last_index = index - 1
#
#     if last_index < 0:
#         print("No device is connected")
#         return
#
#     # Select a camera
#     camera_number = select_camera(last_index)
#
#     # Open camera
#     cap = open_camera(camera_number)
#
#     if cap.isOpened():
#         width = cap.get(3)  # Frame Width
#         height = cap.get(4)  # Frame Height
#         print('Default width: ' + str(width) + ', height: ' + str(height))
#
#         while True:
#
#             ret, frame = cap.read()
#             cv2.imshow("frame", frame)
#
#             # key: 'ESC'
#             key = cv2.waitKey(20)
#             if key == 27:
#                 break
#
#         cap.release()
#         cv2.destroyAllWindows()
#
#
# if __name__ == "__main__":
#     main()