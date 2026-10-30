import random
import threading

from redis import *
from utils.RedisUtils import *
import cv2
import random

redisConnection = Redis("127.0.0.1", 6379)

def stop():
    sendStopCommand(redisConnection)

def sendOKDataToDBServer1():
    image = cv2.imread("D:/Left Disc/frames/2024-04-08-17-56-57-124-1.png")
    printBold()
    sendImageWithResult(redisConnection=redisConnection, anImage=image, queueName="save1.q", result=ok)
    printBold()

def sendNotOKDataToDBServer1():
    image = cv2.imread("D:/Left Disc/frames/2024-04-08-17-56-57-124-2.png")
    printBold()
    sendImageWithResult(redisConnection=redisConnection, anImage=image, queueName="save1.q", result=notok)
    printBold()

def sendIndeterminateDataToDBServer1():
    image = cv2.imread("D:/Left Disc/frames/2024-04-08-17-56-57-124-3.png")
    printBold()
    sendImageWithResult(redisConnection=redisConnection, anImage=image, queueName="save1.q", result=INVALID_RESULT)
    printBold()

def sendOKDataToDBServer2():
    image = cv2.imread("D:/Left Disc/frames/2024-04-08-17-56-57-124-4.png")
    printBold()
    sendImageWithResult(redisConnection=redisConnection, anImage=image, queueName="save2.q", result=ok)
    printBold()

def sendNotOKDataToDBServer2():
    image = cv2.imread("D:/Left Disc/frames/2024-04-08-17-56-57-124-6.png")
    printBold()
    sendImageWithResult(redisConnection=redisConnection, anImage=image, queueName="save2.q", result=notok)
    printBold()

def sendIndeterminateDataToDBServer2():
    image = cv2.imread("D:/Left Disc/frames/2024-04-08-17-56-57-124-7.png")
    printBold()
    sendImageWithResult(redisConnection=redisConnection, anImage=image, queueName="save2.q", result=INVALID_RESULT)
    printBold()

def sendDataToFrontEnd1():
    for j in range(1):
        for i in range(10,110):
            image = cv2.imread(f"C:/Temp/{i}-CroppedImage.png")
            x = random.random()
            printBold()
            if x < 0.33:
                sendImageWithResult(redisConnection=redisConnection, anImage=image, queueName="cam1.result", result=ok)
            elif x < 0.67:
                sendImageWithResult(redisConnection=redisConnection, anImage=image, queueName="cam1.result", result=notok)
            else:
                sendImageWithResult(redisConnection=redisConnection, anImage=image, queueName="cam1.result", result=INVALID_RESULT)
            printBold()
            time.sleep(1.3)

def sendDataToFrontEnd2():
    for j in range(1):
        for i in range(111,211):
            image = cv2.imread(f"C:/Temp/{i}-CroppedImage.png")
            x = random.random()
            printBold()
            if x < 0.33:
                sendImageWithResult(redisConnection=redisConnection, anImage=image, queueName="cam2.result", result=ok)
            elif x < 0.67:
                sendImageWithResult(redisConnection=redisConnection, anImage=image, queueName="cam2.result", result=notok)
            else:
                sendImageWithResult(redisConnection=redisConnection, anImage=image, queueName="cam2.result", result=INVALID_RESULT)
            printBold()
            time.sleep(1.3)

def sendTakePicMessageToCamera2(i):
    # printBold()
    # NOTE: In ApplicationConfiguration.properties camera2 has IP 192.168.1.100
    sendTakeNextPictureCommandToCamera(redisConnection=redisConnection, cameraId=2)
    printBold(i)

def sendTLActions():
    sendTowerlampActionToIOServer(redisConnection=redisConnection, result=INVALID_RESULT, cameraId=2)
    printBold()
    time.sleep(1)
    sendTowerlampActionToIOServer(redisConnection=redisConnection, result=INVALID_RESULT, cameraId=1)
    printBold()
    time.sleep(1)
    sendTowerlampActionToIOServer(redisConnection=redisConnection, result=ok, cameraId=2)
    printBold()
    time.sleep(1)
    sendTowerlampActionToIOServer(redisConnection=redisConnection, result=ok, cameraId=1)
    printBold()
    time.sleep(1)
    sendTowerlampActionToIOServer(redisConnection=redisConnection, result=notok, cameraId=2)
    printBold()
    time.sleep(1)
    sendTowerlampActionToIOServer(redisConnection=redisConnection, result=notok, cameraId=1)
    printBold()

# sendOKDataToDBServer1()
# sendNotOKDataToDBServer1()
# sendIndeterminateDataToDBServer1()
# sendOKDataToDBServer2()
# sendNotOKDataToDBServer2()
# sendIndeterminateDataToDBServer2()


# thread1 = threading.Thread(name="1", target=sendDataToFrontEnd1, args=())
# thread2 = threading.Thread(name="2", target=sendDataToFrontEnd2, args=())
# thread1.start()
# thread2.start()
# thread1.join()
# thread2.join()


for i in range(3):
    for j in range(5):
        sendTakePicMessageToCamera2(i)
        time.sleep(2.55)
    time.sleep(5)
    # sendStopCommand(redisConnection)

# for i in range(3):
#     for j in range(5):
#         sendMessageToDiscInterlockAndConnectionAlarm(redisConnection)
#         time.sleep(1)
#     time.sleep(5)

# sendTLActions()