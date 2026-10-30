from utils.RedisUtils import *
import cv2

trialRedisConnection = Redis("localhost", 6379)

sendEmergencyAbortFromIOServerToFEServer(trialRedisConnection)
