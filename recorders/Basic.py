import cv2
import time

def showVideo(video_source : int = 0, reportFrameCaptureRate : bool = False):
    video = cv2.VideoCapture(video_source) # 0 = First connected camera / inbuilt camera, 1 = next, and so on
    totalTime = 0
    totalFrames = 0
    while True:
        starttime = time.time()*1000
        isTrue, frame = video.read()
        endtime = time.time() * 1000
        cv2.imshow('Webcam', frame)
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


# checkReleaseAndCaptureTimings(1)
# stressTestLogitech(1, 3000)
# showVideo(1)

from pygrabber.dshow_graph import FilterGraph

graph = FilterGraph()
print(graph.get_input_devices())

# index = -1
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