from BaseUtils import *
from utils.CosThetaFileUtils import *
from utils.CosThetaPrintUtils import *
import numpy as np

# a = getTimeDotTimeFromString("2024-04-19-18-31-36-729099")
# print(a)
#
# b = getTimeDotTimeFromString("2024-04-19-18-31-32-8024457")
# print(b)
#
# c = a - b
# print(c)

# directory = "C:/Geltec Data - 19 April - Evening/19 April 2024/TrialImages/Right Disc"
# directory = "C:/Geltec Data - 19 April - Evening/19 April 2024/TrialImages/Left Disc/"
# directory = "C:/Geltec Data - 19 April - Afternoon/Left Disc with 1by25 exposure"
# directory = "H:/Geltec - Left Disc Images/TrialImages/"
# directory = "H:/Geltec Images - 18 April 2024/Right Disc Images - 18 April 2024/"
# directory = "H:/Geltec Images - 18 April 2024/Left Disc Images - 18 April 2024/"
# directory = "C:/Geltec Data - 19 April - Afternoon/Right Disc with 1by25exposure/"
directory = "C:/Geltec Data - 20 April 2024 730 pm/Right Disc/"
filesInDirectory = listFiles(directory=directory)
tStampsInString = []
for aFile in filesInDirectory:
    times = aFile.strip().split("-")
    tStamp = f"{times[0]}-{times[1]}-{times[2]}-{times[3]}-{times[4]}-{times[5]}-{times[6]}"
    tStampsInString.append(tStamp)
tStampsInString = set(tStampsInString)
# tStampsInString = list(tStampsInString)
# print(tStampsInString)
tStampsAsFloat = []
for tStamp in tStampsInString:
    tStampsAsFloat.append(getTimeDotTimeFromString(tStamp))
tStampsAsFloat.sort()
tStampsAsArray = np.array(tStampsAsFloat)
# print(tStampsAsFloat)
tStampDiff = np.diff(tStampsAsArray)
print(tStampDiff)