import time

from utils.RedisUtils import *
from redis import Redis
import cv2

repeats = 1

image1 = cv2.imread("C:/Temp/TrialImages/Knuckle/2025-06-06-23-55-41-483261-0.png")
print(image1.shape)

try:
    trialRedisConnection = Redis(host="localhost", port=6379, retry_on_timeout=True)
    # if trialRedisConnection.ping():
    #     print("Connected to Redis!")
    # else:
    #     print("Ping failed")
except Exception as e:
    print(f"Couldn't connect to Redis due to {e}")
    exit()

# trialRedisConnection.flushdb()
# printBoldBlue(f"Redis connection is {trialRedisConnection}")
# print(trialRedisConnection.ping())
trialRedisConnection.flushdb()

def sendData():
    start = time.time()
    for i in range(repeats):
        print(f"Sending data - {i}")
        print(sendDataFromFEServerToDatabaseServer(redisConnection = trialRedisConnection,
                                                   qrCode = "7204838$400112VA1D$2630$17.05.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNU LH$SNPL-MAT$",
                                                   knucklePicture  = image1, knuckleResult = notok,
                                                   hubAndBottomBearingPicture= image1, hubAndBottomBearingResult= notok,
                                                   topBearingPicture= image1, topBearingResult= notok,
                                                   nutAndPlateWasherPicture = image1, nutAndPlateWasherResult = notok,
                                                   tighteningTorque1 = 0.2, tighteningTorque1Result = ok,
                                                   rotationTorque1 = 0.2, rotationTorque1Result = ok,
                                                   tighteningTorque2 = 0.2, tighteningTorque2Result = ok,
                                                   rotationTorque2 = 0.2, rotationTorque2Result = ok,
                                                   tighteningTorque3 = 0.2, tighteningTorque3Result = ok,
                                                   rotationTorque3 = 0.2, rotationTorque3Result = ok,
                                                   splitPinAndWasherPicture = image1, splitPinAndWasherResult = ok,
                                                   capCheckingPicture = image1, capCheckingResult = ok,
                                                   bungCheckingPicture= image1, capPressBungCheckingResult= ok,
                                                   pressDone = ok, overallResult = notok))
    end = time.time()
    print(f"Time taken = {(end - start)*1000/repeats} ms")

def receiveData():
    start = time.time()
    for i in range(repeats):
        printBold(f"Receiving data - {i}")
        readDataInDatabaseServerFromFEServer(redisConnection=trialRedisConnection)
    end = time.time()
    print(f"Time taken = {(end - start)*1000/repeats} ms")

sendData()
# receiveData()