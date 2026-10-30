from time import sleep
from threading import Thread
import time

from utils.RedisUtils import *

# q1 = "Q1"
# q2 = "Q2"
# q3 = "Q3"
# cycles = 30
#
# # ENCODING AND PUTTING A 1280 X 720 IMAGE INTO A QUEUE TAKES 125 ms
# # RETRIEVING AND DECODING A 1280 X 720 IMAGE FROM A QUEUE TAKES 40 ms
#
# # PUTTING A DATA PACKET INTO A QUEUE TAKES 2-3 ms
# # RETRIEVING A DATA PACKET FROM A QUEUE TAKES 2-3 ms
#
# def putImageIntoQueue(redisConnection, queueName : str, imageFilepath : str):
#     printPlain(imageFilepath)
#     anImage = cv2.imread(imageFilepath)
#     for i in range(cycles):
#         sendImage(redisConnection, anImage=anImage, queueName=queueName, trackTime=True)
#         sleep(0.25)
#
# def getImageFromQueue(redisConnection, queueName : str):
#     for i in range(cycles):
#         getImage(redisConnection, queueName=queueName, trackTime=True)
#         sleep(0.010)
#
# def putDataIntoQueue(redisConnection, queueName : str, data : dict = {"1" : "arbitData1", "2" : "arbitData2"}):
#     for i in range(cycles):
#         sendData(redisConnection, data=data, queueName=queueName, trackTime=True)
#         sleep(1)
#
# def getDataFromQueue(redisConnection, queueName : str):
#     for i in range(cycles):
#         getData(redisConnection, queueName=queueName, trackTime=True)
#         sleep(0.025)
#
# def tryRedisImage(imageFilepath : str = "C:/Temp/Rhea.jpg"):
#     r = Redis("localhost", 6379, retry_on_timeout=True)
#     r.xtrim(commonStreamKey, 0)
#     sleep(0.25)
#     t1 = Thread(target = putImageIntoQueue, args=(r, q1, imageFilepath))
#     t2 = Thread(target = getImageFromQueue, args=(r, q1))
#     t3 = Thread(target = putImageIntoQueue, args=(r, q2, imageFilepath))
#     t4 = Thread(target = getImageFromQueue, args=(r, q2))
#     t5 = Thread(target = putImageIntoQueue, args=(r, q3, imageFilepath))
#     t6 = Thread(target = getImageFromQueue, args=(r, q3))
#     t2.start()
#     t4.start()
#     t6.start()
#     t1.start()
#     t3.start()
#     t5.start()
#     t1.join()
#     t2.join()
#     t3.join()
#     t4.join()
#     t5.join()
#     t6.join()
#
# def tryRedisData(data : dict = {"1" : "arbitData1", "2" : "arbitData2"}):
#     r = Redis("localhost", 6379, retry_on_timeout=True)
#     r.xtrim(commonStreamKey, 0)
#     sleep(0.25)
#     t1 = Thread(target = putDataIntoQueue, args=(r, q1, data))
#     t2 = Thread(target = getDataFromQueue, args=(r, q1))
#     t3 = Thread(target = putDataIntoQueue, args=(r, q2, data))
#     t4 = Thread(target = getDataFromQueue, args=(r,q2))
#     t5 = Thread(target = putDataIntoQueue, args=(r, q3, data))
#     t6 = Thread(target = getDataFromQueue, args=(r,q3))
#     t2.start()
#     t4.start()
#     t6.start()
#     t1.start()
#     t3.start()
#     t5.start()
#     t1.join()
#     t2.join()
#     t3.join()
#     t4.join()
#     t5.join()
#     t6.join()
#
# # tryRedisData()
# tryRedisImage(imageFilepath = "C:/Temp/2.jpg")
redisConnection = Redis("localhost", 6379, retry_on_timeout=True)
# sendEmergencyAbortFromIOServerToFEServer(redisConnection)
redisConnection.flushdb()