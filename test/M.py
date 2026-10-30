import random
import time
from datetime import datetime
from time import sleep
import numpy as np

timeStarted = datetime.now()
print((':'.join(str(datetime.now() - timeStarted).split(':')[:3])).split('.')[0])

for i in range(10):
    x = random.random()
    print(x)

a = [0 for i in range(10)]
time1 = time.time()
print(time1)
for i in range(1000):
    print(i)
    a = a[1:] + ["INVALID RESULT"]
time2 = time.time()
print(time2)
print(f"{(time2 - time1)/10000} ms")
print(a)

a = {"a" : 25, "b" : 36}
print(type(a), a)

x = 10
arr = [x for i in range(10)]
print(arr)

y = [1, 7, 14]
y = np.array(y, dtype=float)
print(float(np.mean(y)))

print(max(12.6, 8, 9, 145))

import cv2
startTime = time.time()
repeats = 100
for i in range(repeats):
    image = cv2.imread("D:/Left Disc/frames/2024-04-08-17-56-57-124-1.png")
endTime = time.time()
print(f"{(endTime-startTime)*1000/repeats} ms")