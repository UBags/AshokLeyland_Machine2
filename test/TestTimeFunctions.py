from datetime import datetime
import time

def getCurrentTime():
    msTime = datetime.now().strftime('%Y-%m-%d-%H-%M-%S.%f')
    (dt, ms) = msTime.split('.')
    ms = "{:03d}".format(int(ms) // 1000)
    currentTime = f'{dt}-{ms}'
    return currentTime

def getCurrentTimeInMS():
    t = time.time()
    print(type(t))
    return t

print(getCurrentTimeInMS())