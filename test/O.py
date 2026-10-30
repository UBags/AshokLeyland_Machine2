from datetime import datetime
from utils.CosThetaFileUtils import *

A = [[1,2,3], [2,3,4],[3,4,5]]
print(A[2][2])
print(int(round(0.33,0)))
print(int(round(0.67,0)))
print(datetime.now().strftime("%Y-%m-%d"))

basePath = "C:/Geltec Data - 20 April 2024 730 pm/Right Disc/"
filesInDirectory = getAllFilesInDirectory(basePath)
for file in filesInDirectory:
    print(file)
