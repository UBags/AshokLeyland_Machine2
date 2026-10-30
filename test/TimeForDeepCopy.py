import cv2
import time
import copy

image = cv2.imread("C:/Temp/TrialImages/2024-04-09-21-06-46-880.png", cv2.IMREAD_COLOR)
sTime = time.time()
repeats = 100
for i in range(repeats):
    x = copy.deepcopy(image)
eTime = time.time()
print(f"{(eTime-sTime) * 1000/repeats} ms")
