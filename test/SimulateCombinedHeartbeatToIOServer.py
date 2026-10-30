import time
from utils.RedisUtils import *

trialRedisConnection = Redis("localhost", 6379)
clearQueues(trialRedisConnection)

while True:
    try:
        sendCombinedHeartbeatFromHeartbeatServerToIOServer(trialRedisConnection, combinedConnectionStatus=ALIVE)
        time.sleep(2.0)
        print(f"Sent Heartbeat to IO server")
    except Exception as e:
        printBoldRed(f"Exception is {e}")