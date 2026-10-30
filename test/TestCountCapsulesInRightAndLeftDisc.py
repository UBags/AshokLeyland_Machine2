import cv2
import numpy as np
import time
from video.ImageProcessingFunctions import *
import copy
from typing import *
from utils.CosThetaFileUtils import *
import pandas as pd

from Configuration import *
CosThetaConfigurator.getInstance()

def makePolygon(listOfPoints: list) -> list:
    polygon = np.array(listOfPoints).reshape((-1, 1, 2)).astype(np.int32)
    return polygon

def timeReduceSize(imagePath: str, reductionfactor : float = 2.0):
    image = cv2.imread(imagePath)
    startTime = time.time()
    repeats = 100
    for i in range(repeats):
        newImage = cv2.resize(image, (int(image.shape[1]//reductionfactor), int(image.shape[0]//reductionfactor)), cv2.INTER_LANCZOS4)
    endTime = time.time()
    print(f"Time taken to reduce by factor of {reductionfactor} is {(endTime - startTime)*1000/repeats} ms")

def checkExclusionAreaClear(image: np.ndarray, exclusionAreaPolygonPoints_Left : Any, exclusionAreaPolygonPoints_Right : Any, show : bool = False):
    workingImage = copy.deepcopy(image)
    workingImage = reduceSize(workingImage, reductionfactor=2.0)
    exclusionPolygonPoints = copy.deepcopy(exclusionAreaPolygonPoints_Left)
    exclusionPolygonPoints = (exclusionPolygonPoints / 2.0).astype(np.int32)
    cropped, _ = cropToRectangleAndResizeImage(workingImage, exclusionPolygonPoints)
    croppedImage = cropImageToExactPolygonShape(cropped, exclusionPolygonPoints)
    if show:
        showImage(croppedImage, wait=True)
    croppedImage = cv2.cvtColor(croppedImage, cv2.COLOR_BGR2GRAY)
    if show:
        showImage(croppedImage, wait=True)
    _, croppedImage = cv2.threshold(croppedImage,164,255, cv2.THRESH_BINARY_INV)
    if show:
        showImage(croppedImage, wait=True)
    croppedImage = dilate(croppedImage, kernel_33)
    if show:
        showImage(croppedImage, wait=True)
    _, croppedImage = cv2.threshold(croppedImage,192,255, cv2.THRESH_BINARY)
    if show:
        showImage(croppedImage, wait=True)
    contours, hierarchy = cv2.findContours(croppedImage, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    # print(len(contours))
    # showImage(croppedImage, wait=True)
    if len(contours) > 0:
        return False
    exclusionPolygonPoints = copy.deepcopy(exclusionAreaPolygonPoints_Right)
    exclusionPolygonPoints = (exclusionPolygonPoints / 2.0).astype(np.int32)
    cropped, _ = cropToRectangleAndResizeImage(workingImage, exclusionPolygonPoints)
    croppedImage = cropImageToExactPolygonShape(cropped, exclusionPolygonPoints)
    if show:
        showImage(croppedImage, wait=True)
    croppedImage = cv2.cvtColor(croppedImage, cv2.COLOR_BGR2GRAY)
    if show:
        showImage(croppedImage, wait=True)
    _, croppedImage = cv2.threshold(croppedImage,148,255, cv2.THRESH_BINARY_INV)
    if show:
        showImage(croppedImage, wait=True)
    croppedImage = dilate(croppedImage, kernel_33)
    if show:
        showImage(croppedImage, wait=True)
    _, croppedImage = cv2.threshold(croppedImage,192,255, cv2.THRESH_BINARY)
    if show:
        showImage(croppedImage, wait=True)
    contours, hierarchy = cv2.findContours(croppedImage, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if len(contours) > 0:
        return False
    return True

'''
# timeReduceSize("D:/Right Disc/frames/2024-04-08-17-18-09-593-3.png", reductionfactor=2.0)
# polygon = makePolygon([[7, 343], [104, 278], [76, 224], [241, 126], [241, 126],
#                                         [307, 4], [402, 7], [387, 42], [227, 85], [8, 127]])
# print(polygon)
# polygon = (polygon / 2).astype(np.uint8)
# print(polygon)
'''


from enum import Enum

class POSITION(Enum):
    OUTER = 1
    MIDDLE = 2
    INNER = 3

cust_name : str = "GSK"
med_name : str = "BECADEXAMIN"

rightDiscEntryExclusionPolygonPoints = CosThetaConfigurator.getInstance().getRightDiscExclusionEntryPolygonPoints(cust_name=cust_name, med_name=med_name)
rightDiscExitExclusionPolygonPoints = CosThetaConfigurator.getInstance().getRightDiscExclusionExitPolygonPoints(cust_name=cust_name, med_name=med_name)
rightDiscInnerPolygonPoints = CosThetaConfigurator.getInstance().getRightDiscInnerPolygonPoints(cust_name=cust_name, med_name=med_name)
rightDiscMiddlePolygonPoints = CosThetaConfigurator.getInstance().getRightDiscMiddlePolygonPoints(cust_name=cust_name, med_name=med_name)
rightDiscOuterPolygonPoints = CosThetaConfigurator.getInstance().getRightDiscOuterPolygonPoints(cust_name=cust_name, med_name=med_name)

# rightDiscEntryExclusionPolygonPoints = createPolygon([[7, 343], [115, 275], [80, 220], [250, 120], [235, 85], [365, 5], [315, 10], [7, 125]])
# rightDiscExitExclusionPolygonPoints = createPolygon([[780, 560], [770, 490], [755, 500], [755, 395], [730, 400], [735, 265], [665, 275], [665, 400], [630, 410], [625, 550], [570, 550], [570, 645]])
# rightDiscOuterPolygonPoints = createPolygon([[3, 341], [80, 280], [196, 412], [350, 515], [613, 548], [731, 575], [726, 596], [682, 608], [669, 611], [655, 613], [633, 631], [394, 636], [181, 557], [59, 455], [3, 394]])
# rightDiscMiddlePolygonPoints = createPolygon([[80, 225], [180, 370], [315, 485], [485, 535], [705, 535], [705, 440], [670, 450], [570, 450], [465, 420], [355, 360], [292, 305], [195, 170]])
# rightDiscInnerPolygonPoints = createPolygon([[245, 110], [327, 235], [415, 320], [515, 378], [715, 395], [700, 280], [570, 255], [452, 157], [378, 38]])

RIGHT_DISC_OUTER_MIN_AREA = CosThetaConfigurator.getInstance().getRightDiscOuterMinArea(cust_name=cust_name, med_name=med_name)
RIGHT_DISC_OUTER_MAX_AREA = CosThetaConfigurator.getInstance().getRightDiscOuterMaxArea(cust_name=cust_name, med_name=med_name)
RIGHT_DISC_MIDDLE_MIN_AREA = CosThetaConfigurator.getInstance().getRightDiscMiddleMinArea(cust_name=cust_name, med_name=med_name)  # NOT USED. INSTEAD, REJECTION/ACCEPTANCE OF MIN IS NOW BASED ON AVERAGE NEIGHBOUR SIZES
RIGHT_DISC_MIDDLE_MAX_AREA = CosThetaConfigurator.getInstance().getRightDiscMiddleMaxArea(cust_name=cust_name, med_name=med_name)
RIGHT_DISC_INNER_MIN_AREA = CosThetaConfigurator.getInstance().getRightDiscInnerMinArea(cust_name=cust_name, med_name=med_name)  # NOT USED. INSTEAD, REJECTION/ACCEPTANCE OF MIN IS NOW BASED ON AVERAGE NEIGHBOUR SIZES
RIGHT_DISC_INNER_MAX_AREA = CosThetaConfigurator.getInstance().getRightDiscInnerMaxArea(cust_name=cust_name, med_name=med_name)

LEFT_DISC_OUTER_MIN_AREA = CosThetaConfigurator.getInstance().getLeftDiscOuterMinArea(cust_name=cust_name, med_name=med_name)
LEFT_DISC_OUTER_MAX_AREA = CosThetaConfigurator.getInstance().getLeftDiscOuterMaxArea(cust_name=cust_name, med_name=med_name)
LEFT_DISC_MIDDLE_MIN_AREA = CosThetaConfigurator.getInstance().getLeftDiscMiddleMinArea(cust_name=cust_name, med_name=med_name)  # NOT USED. INSTEAD, REJECTION/ACCEPTANCE OF MIN IS NOW BASED ON AVERAGE NEIGHBOUR SIZES
LEFT_DISC_MIDDLE_MAX_AREA = CosThetaConfigurator.getInstance().getLeftDiscMiddleMaxArea(cust_name=cust_name, med_name=med_name)
LEFT_DISC_INNER_MIN_AREA = CosThetaConfigurator.getInstance().getLeftDiscInnerMinArea(cust_name=cust_name, med_name=med_name)  # NOT USED. INSTEAD, REJECTION/ACCEPTANCE OF MIN IS NOW BASED ON AVERAGE NEIGHBOUR SIZES
LEFT_DISC_INNER_MAX_AREA = CosThetaConfigurator.getInstance().getLeftDiscInnerMaxArea(cust_name=cust_name, med_name=med_name)

BASE_MIN = 21

leftDiscEntryExclusionPolygonPoints = CosThetaConfigurator.getInstance().getLeftDiscExclusionEntryPolygonPoints(cust_name=cust_name, med_name=med_name)
leftDiscExitExclusionPolygonPoints = CosThetaConfigurator.getInstance().getLeftDiscExclusionExitPolygonPoints(cust_name=cust_name, med_name=med_name)
leftDiscOuterPolygonPoints = CosThetaConfigurator.getInstance().getLeftDiscOuterPolygonPoints(cust_name=cust_name, med_name=med_name)
leftDiscMiddlePolygonPoints = CosThetaConfigurator.getInstance().getLeftDiscMiddlePolygonPoints(cust_name=cust_name, med_name=med_name)
leftDiscInnerPolygonPoints = CosThetaConfigurator.getInstance().getLeftDiscInnerPolygonPoints(cust_name=cust_name, med_name=med_name)

minAreaFraction = 0.15


neighbouringDistance = CosThetaConfigurator.getInstance().getNeighbouringDistanceForRightDisc(cust_name=cust_name, med_name=med_name)
minAreaFractionForRightDisc = CosThetaConfigurator.getInstance().getMinAreaFractionForRightDisc(cust_name=cust_name, med_name=med_name)
minAreaFractionForLeftDisc = CosThetaConfigurator.getInstance().getMinAreaFractionForLeftDisc(cust_name=cust_name, med_name=med_name)

def modifyImage(image : np.ndarray, show : bool = False):
    croppedImage = copy.deepcopy(image)
    b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
    croppedImage[np.logical_and.reduce([r_ch - g_ch >= 10, r_ch < 180])] = (0, 0, 255)
    if show:
        showImage(croppedImage, wait = True)
    croppedImage[np.logical_and.reduce([r_ch - g_ch >= 12, r_ch >= 180])] = (0, 0, 255)
    if show:
        showImage(croppedImage, wait = True)
    croppedImage[np.logical_and.reduce([r_ch - g_ch >= 10, g_ch - b_ch >= 3, r_ch >= 180])] = (0, 0, 255)
    if show:
        showImage(croppedImage, wait = True)
    croppedImage[np.logical_and.reduce([r_ch - g_ch >= 8, g_ch - b_ch >= 6, r_ch < 224])] = (0, 0, 255)
    if show:
        showImage(croppedImage, wait = True)
    croppedImage[np.logical_and.reduce([r_ch - g_ch >= 8, g_ch - b_ch >= 4, r_ch < 128])] = (0, 0, 255)
    if show:
        showImage(croppedImage, wait = True)
    croppedImage[np.logical_and.reduce([r_ch - g_ch >= 6, g_ch - b_ch >= 6, r_ch < 128])] = (0, 0, 255)
    if show:
        showImage(croppedImage, wait = True)
    croppedImage[np.logical_and.reduce([r_ch - g_ch >= 4, g_ch - b_ch >= 8, r_ch < 144])] = (0, 0, 255)
    if show:
        showImage(croppedImage, wait = True)
    b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
    croppedImage[np.logical_and.reduce([r_ch - g_ch < 2])] = (255, 255, 255)
    croppedImage[np.logical_and.reduce([r_ch - b_ch < 5])] = (255, 255, 255)
    if show:
        showImage(croppedImage, wait = True)
    b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
    croppedImage[r_ch != 255] = (255, 255, 255)
    if show:
        showImage(croppedImage, wait = True)
    croppedImage[np.logical_and.reduce([r_ch == 255, g_ch == 0])] = (0, 0, 0)
    if show:
        showImage(croppedImage, wait = True)
    return croppedImage

def getCapsuleCountAndAreasRightDisc(image, whichOne : POSITION, show: bool = False):

    # print(image.shape)
    minArea = RIGHT_DISC_INNER_MIN_AREA if whichOne == POSITION.INNER else RIGHT_DISC_MIDDLE_MIN_AREA if whichOne == POSITION.MIDDLE else RIGHT_DISC_OUTER_MIN_AREA
    maxArea = RIGHT_DISC_MIDDLE_MAX_AREA if whichOne == POSITION.INNER else RIGHT_DISC_MIDDLE_MAX_AREA if whichOne == POSITION.MIDDLE else RIGHT_DISC_OUTER_MAX_AREA
    somePolygonPoints = rightDiscInnerPolygonPoints if whichOne == POSITION.INNER else rightDiscMiddlePolygonPoints if whichOne == POSITION.MIDDLE else rightDiscOuterPolygonPoints
    # print(somePolygonPoints)
    imageCopy = copy.deepcopy(image)
    cropped, _ = cropToRectangleAndResizeImage(imageCopy, somePolygonPoints)
    croppedImage = cropImageToExactPolygonShape(cropped, somePolygonPoints)
    if show:
        showImage(croppedImage, wait = True)
    croppedImage = modifyImage(croppedImage, show)
    blackAndWhiteImage = cv2.cvtColor(croppedImage, cv2.COLOR_BGR2GRAY)
    blackAndWhiteImage = 255 - blackAndWhiteImage
    blackAndWhiteImage[blackAndWhiteImage != 255] = 0
    blackAndWhiteImage = cv2.copyMakeBorder(blackAndWhiteImage, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=0)
    if show:
        showImage(blackAndWhiteImage, wait = True)
    blackAndWhiteImage = morphclose(blackAndWhiteImage, kernel_55)
    if show:
        showImage(blackAndWhiteImage, wait = True)
    blackAndWhiteImage = morphopen(blackAndWhiteImage, kernel_33)
    if show:
        showImage(blackAndWhiteImage, wait = True)
    blackAndWhiteImage = morphclose(blackAndWhiteImage, kernel_33)
    if show:
        showImage(blackAndWhiteImage, wait = True)
    if show:
        showImage(blackAndWhiteImage, wait = True)
    blackAndWhiteImage[blackAndWhiteImage != 255] = 0
    if show:
        showImage(blackAndWhiteImage, wait = True)
    # print(np.mean(blackAndWhiteImage))
    contours, hierarchy = cv2.findContours(blackAndWhiteImage, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    # showImage(blackAndWhiteImage, wait = True)
    # print(np.mean(blackAndWhiteImage))
    # blackAndWhiteImage = cv2.cvtColor(blackAndWhiteImage, cv2.COLOR_GRAY2BGR)
    # cv2.drawContours(blackAndWhiteImage, contours, -1, (0,255,0),1)
    # showImage(blackAndWhiteImage, wait = True)
    nTablets = 0
    rawCoordinateAndAreaData=[]
    for contour in contours:
        area = cv2.contourArea(contour)
        # eliminate the ones that are larger than max area. That leaves out the min area issue
        if area > maxArea:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        xCentre = x + w // 2
        yCentre = y + h // 2
        centre = (xCentre, yCentre)
        rawCoordinateAndAreaData.append([centre, area])
        if area < minArea:
            continue
        nTablets += 1
    finalCoordinateAndAreaData=[]
    areas = []
    for mainIndex, entry in enumerate(rawCoordinateAndAreaData):
        surroundingAreas = []
        x1 = entry[0][0]
        y1 = entry[0][1]
        area1 = entry[1]
        for subIndex, aData in enumerate(rawCoordinateAndAreaData):
            if mainIndex == subIndex:
                continue
            x2 = aData[0][0]
            y2 = aData[0][1]
            area2 = aData[1]
            distance = math.sqrt((x1-x2)*(x1-x2) + (y1-y2)*(y1-y2))
            if distance <= neighbouringDistance:
                surroundingAreas.append(area2)
        if len(surroundingAreas) > 0:
            surroundingAreas = np.array(surroundingAreas)
            averageSurroundingArea = np.mean(surroundingAreas)
        else:
            averageSurroundingArea = 0
        if averageSurroundingArea > 0: # since this is for a loner coordinate, eliminate this coordinate
            if (area1 > averageSurroundingArea * minAreaFractionForRightDisc) or (area1 > BASE_MIN):
                finalCoordinateAndAreaData.append(entry)
                areas.append(area1)
    nTablets = len(finalCoordinateAndAreaData)
    return nTablets, areas

def getCapsuleCountAndAreasLeftDisc(image, whichOne : POSITION, show: bool = False):

    minArea = LEFT_DISC_INNER_MIN_AREA if whichOne == POSITION.INNER else LEFT_DISC_MIDDLE_MIN_AREA if whichOne == POSITION.MIDDLE else LEFT_DISC_OUTER_MIN_AREA
    maxArea = LEFT_DISC_MIDDLE_MAX_AREA if whichOne == POSITION.INNER else LEFT_DISC_MIDDLE_MAX_AREA if whichOne == POSITION.MIDDLE else LEFT_DISC_OUTER_MAX_AREA
    somePolygonPoints = leftDiscInnerPolygonPoints if whichOne == POSITION.INNER else leftDiscMiddlePolygonPoints if whichOne == POSITION.MIDDLE else leftDiscOuterPolygonPoints
    print(somePolygonPoints)
    imageCopy = copy.deepcopy(image)
    cropped, _ = cropToRectangleAndResizeImage(imageCopy, somePolygonPoints)
    croppedImage = cropImageToExactPolygonShape(cropped, somePolygonPoints)
    if show:
        showImage(croppedImage, wait = True)
    croppedImage = modifyImage(croppedImage, show)
    blackAndWhiteImage = cv2.cvtColor(croppedImage, cv2.COLOR_BGR2GRAY)
    blackAndWhiteImage = 255 - blackAndWhiteImage
    blackAndWhiteImage[blackAndWhiteImage != 255] = 0
    blackAndWhiteImage = cv2.copyMakeBorder(blackAndWhiteImage, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=0)
    if show:
        showImage(blackAndWhiteImage, wait = True)
    blackAndWhiteImage = morphclose(blackAndWhiteImage, kernel_55)
    if show:
        showImage(blackAndWhiteImage, wait = True)
    blackAndWhiteImage = morphopen(blackAndWhiteImage, kernel_33)
    if show:
        showImage(blackAndWhiteImage, wait = True)
    blackAndWhiteImage = morphclose(blackAndWhiteImage, kernel_33)
    if show:
        showImage(blackAndWhiteImage, wait = True)
    blackAndWhiteImage[blackAndWhiteImage != 255] = 0
    if show:
        showImage(blackAndWhiteImage, wait = True)
    # print(np.mean(blackAndWhiteImage))
    contours, hierarchy = cv2.findContours(blackAndWhiteImage, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    # showImage(blackAndWhiteImage, wait = True)
    # print(np.mean(blackAndWhiteImage))
    # blackAndWhiteImage = cv2.cvtColor(blackAndWhiteImage, cv2.COLOR_GRAY2BGR)
    # cv2.drawContours(blackAndWhiteImage, contours, -1, (0,255,0),1)
    # showImage(blackAndWhiteImage, wait = True)
    nTablets = 0
    rawCoordinateAndAreaData=[]
    for contour in contours:
        area = cv2.contourArea(contour)
        # eliminate the ones that are larger than max area. That leaves out the min area issue
        if area > maxArea:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        xCentre = x + w // 2
        yCentre = y + h // 2
        centre = (xCentre, yCentre)
        rawCoordinateAndAreaData.append([centre, area])
        if area < minArea:
            continue
        nTablets += 1
    finalCoordinateAndAreaData=[]
    areas = []
    excludedAreasAndFractions = []
    for mainIndex, entry in enumerate(rawCoordinateAndAreaData):
        surroundingAreas = []
        x1 = entry[0][0]
        y1 = entry[0][1]
        area1 = entry[1]
        for subIndex, aData in enumerate(rawCoordinateAndAreaData):
            if mainIndex == subIndex:
                continue
            x2 = aData[0][0]
            y2 = aData[0][1]
            area2 = aData[1]
            distance = math.sqrt((x1-x2)*(x1-x2) + (y1-y2)*(y1-y2))
            if distance <= neighbouringDistance:
                surroundingAreas.append(area2)
        if len(surroundingAreas) > 0:
            surroundingAreas = np.array(surroundingAreas)
            averageSurroundingArea = np.mean(surroundingAreas)
        else:
            averageSurroundingArea = 0
        if averageSurroundingArea > 0: # since this is for a loner coordinate, eliminate this coordinate
            # if (area1 > averageSurroundingArea * minAreaFractionForLeftDisc) or (area1 > BASE_MIN):
            #     finalCoordinateAndAreaData.append(entry)
            #     areas.append(area1)
            # else:
            #     print(f"Excluded area {area1}, when average is {averageSurroundingArea}")
            if whichOne == POSITION.INNER or whichOne == POSITION.MIDDLE:
                if (area1 > averageSurroundingArea * minAreaFraction) or (area1 > BASE_MIN):
                    finalCoordinateAndAreaData.append(entry)
                    areas.append(area1)
                else:
                    excludedAreasAndFractions.append([area1, round(area1 / averageSurroundingArea, 3)])
            else:
                if (area1 > minArea):
                    finalCoordinateAndAreaData.append(entry)
                    areas.append(area1)
                else:
                    excludedAreasAndFractions.append([area1, round(area1 / minArea, 3)])
    print(excludedAreasAndFractions)
    nTablets = len(finalCoordinateAndAreaData)
    return nTablets, areas

# *********************************************RIGHT DISC CHECKS BEGIN************************************************

# Check area exclusion process per image for right disc
# image = cv2.imread("C:/Geltec Data - 19 April - Evening/19 April 2024/TrialImages/Right Disc/2024-04-16-20-53-40-4357033-2024-04-16-20-53-40-514.png")
# repeats = 1
# startTime = time.time()
# for i in range(repeats):
#     print(checkExclusionAreaClear(image, rightDiscEntryExclusionPolygonPoints, rightDiscExitExclusionPolygonPoints, show = True))
# endTime = time.time()
# print({(endTime - startTime)*1000/repeats}, "ms")


# # Check area exclusion process for all images in a directory for right disc
# filesInDirectory = listFiles("C:/Geltec Data - 19 April - Evening/19 April 2024/TrialImages/Right Disc/")
# for file in filesInDirectory:
#     image = cv2.imread(f"C:/Geltec Data - 19 April - Evening/19 April 2024/TrialImages/Right Disc/{file}")
#     print(f"{file} : {checkExclusionAreaClear(image, rightDiscEntryExclusionPolygonPoints, rightDiscExitExclusionPolygonPoints)}")

# Check counting process per image for right disc - outer polygon
# image = cv2.imread("C:/Geltec Data - 19 April - Evening/19 April 2024/TrialImages/Right Disc/2024-04-19-18-32-26-941565-2024-04-19-18-32-27-221.png")
# startTime = time.time()
# repeats = 1
# for i in range(repeats):
#     # cv2.blur(image, (3,3))
#     print(getCapsuleCountAndAreasRightDisc(image, POSITION.OUTER, show = True))
# endTime = time.time()
# print(f"{(endTime - startTime) * 1000 / repeats} ms")

# Check counting process per image for right disc - middle polygon
# image = cv2.imread("C:/Geltec Data - 19 April - Evening/19 April 2024/TrialImages/Right Disc/2024-04-19-18-54-43-5862677-2024-04-19-18-54-43-914.png")
# startTime = time.time()
# repeats = 1
# for i in range(repeats):
#     # cv2.blur(image, (3,3))
#     print(getCapsuleCountAndAreasRightDisc(image, POSITION.MIDDLE, show = True))
# endTime = time.time()
# print(f"{(endTime - startTime) * 1000 / repeats} ms")

# Check counting process per image for right disc - inner polygon
# image = cv2.imread("C:/Geltec Data - 19 April - Evening/19 April 2024/TrialImages/Right Disc/2024-04-19-18-32-26-941565-2024-04-19-18-32-27-221.png")
# startTime = time.time()
# repeats = 1
# for i in range(repeats):
#     # cv2.blur(image, (3,3))
#     print(getCapsuleCountAndAreasRightDisc(image, POSITION.INNER, show = True))
# endTime = time.time()
# print(f"{(endTime - startTime) * 1000 / repeats} ms")


# Check counts and get areas data for all images in a directory for right disc - outer polygon
filesInDirectory = listFiles("D:/Right Disc/frames/")
columnNames = []
for i in range(72):
    columnNames.append(f"{i}")
df = pd.DataFrame(columns=columnNames)
for index, file in enumerate(filesInDirectory):
    image = cv2.imread(f"D:/Right Disc/frames/{file}")
    okImage = checkExclusionAreaClear(image, rightDiscEntryExclusionPolygonPoints, rightDiscExitExclusionPolygonPoints)
    if okImage:
        count, areas = getCapsuleCountAndAreasRightDisc(image, POSITION.OUTER)
        l = len(areas)
        if l < 70:
            for i in range(70-l):
                areas.append(0)
        else:
            areas = areas[:70]
        areas.insert(0, f"{file}")
        areas.append(count)
        num_rows = len(df)
        print(num_rows, len(areas))
        df.loc[num_rows] = areas
        print(f"{file} : {count}")
df.to_csv('D:/Right Disc/areas-outer.csv', header=False, index=False)

# # Check counts and get areas data for all images in a directory for right disc - middle polygon
filesInDirectory = listFiles("D:/Right Disc/frames/")
columnNames = []
for i in range(72):
    columnNames.append(f"{i}")
df = pd.DataFrame(columns=columnNames)
for index, file in enumerate(filesInDirectory):
    image = cv2.imread(f"D:/Right Disc/frames/{file}")
    okImage = checkExclusionAreaClear(image, rightDiscEntryExclusionPolygonPoints, rightDiscExitExclusionPolygonPoints)
    if okImage:
        count, areas = getCapsuleCountAndAreasRightDisc(image, POSITION.MIDDLE)
        l = len(areas)
        if l < 70:
            for i in range(70-l):
                areas.append(0)
        else:
            areas = areas[:70]
        areas.insert(0, f"{file}")
        areas.append(count)
        num_rows = len(df)
        print(num_rows, len(areas))
        df.loc[num_rows] = areas
        print(f"{file} : {count}")
df.to_csv('D:/Right Disc/areas-middle.csv', header=False, index=False)

# Check counts and get areas data for all images in a directory for right disc - inner polygon
filesInDirectory = listFiles("C:/PythonProjects/Geltec_New/Images/Right Disc All/")
columnNames = []
for i in range(72):
    columnNames.append(f"{i}")
df = pd.DataFrame(columns=columnNames)
for index, file in enumerate(filesInDirectory):
    image = cv2.imread(f"C:/PythonProjects/Geltec_New/Images/Right Disc All/{file}")
    okImage = checkExclusionAreaClear(image, rightDiscEntryExclusionPolygonPoints, rightDiscExitExclusionPolygonPoints)
    if okImage:
        count, areas = getCapsuleCountAndAreasRightDisc(image, POSITION.INNER)
        l = len(areas)
        if l < 70:
            for i in range(70-l):
                areas.append(0)
        else:
            areas = areas[:70]
        areas.insert(0, f"{file}")
        areas.append(count)
        num_rows = len(df)
        print(num_rows, len(areas))
        df.loc[num_rows] = areas
        print(f"{file} : {count}")
df.to_csv('C:/PythonProjects/Geltec_New/Images/Right Disc All/areas-inner.csv', header=False, index=False)

# *********************************************RIGHT DISC CHECKS END************************************************

# *********************************************LEFT DISC CHECKS BEGIN************************************************

# Check area exclusion process per image for left disc
# image = cv2.imread("C:/Geltec Data - 19 April/Left Disc with 1by25 exposure/2024-04-08-17-56-57-124-51.png")
# repeats = 1
# startTime = time.time()
# for i in range(repeats):
#     print(checkExclusionAreaClear(image, leftDiscEntryExclusionPolygonPoints, leftDiscExitExclusionPolygonPoints, show = True))
# endTime = time.time()
# print({(endTime - startTime)*1000/repeats}, "ms")


'''
# # Check area exclusion process for all images in a directory for left disc
# filesInDirectory = listFiles("C:/Geltec Data - 19 April/Left Disc with 1by25 exposure/")
# for file in filesInDirectory:
#     image = cv2.imread(f"D:/Left Disc/frames/{file}")
#     print(f"{file} : {checkExclusionAreaClear(image, leftDiscEntryExclusionPolygonPoints, leftDiscExitExclusionPolygonPoints)}")
'''

# Check counting process per image for left disc - outer polygon
# image = cv2.imread("C:/Geltec Data - 19 April/Left Disc with 1by25 exposure/2024-04-18-18-46-30-4251137-2024-04-18-18-46-30-610.png")
# startTime = time.time()
# repeats = 1
# for i in range(repeats):
#     # cv2.blur(image, (3,3))
#     print(getCapsuleCountAndAreasLeftDisc(image, POSITION.OUTER, show = True))
# endTime = time.time()
# print(f"{(endTime - startTime) * 1000 / repeats} ms")

# Check counting process per image for left disc - middle polygon
# image = cv2.imread("C:/Geltec Data - 20 April 2024 730 pm/Left Disc/2024-04-20-17-30-57-7981362-2024-04-20-17-30-58-001.png")
# startTime = time.time()
# repeats = 1
# for i in range(repeats):
#     # cv2.blur(image, (3,3))
#     print(getCapsuleCountAndAreasLeftDisc(image, POSITION.MIDDLE, show = True))
# endTime = time.time()
# print(f"{(endTime - startTime) * 1000 / repeats} ms")

# Check counting process per image for left disc - inner polygon
# image = cv2.imread("C:/Geltec Data - 19 April/Left Disc with 1by25 exposure/2024-04-18-17-34-05-8511696-2024-04-18-17-34-06-019.png")
# startTime = time.time()
# repeats = 1
# for i in range(repeats):
#     # cv2.blur(image, (3,3))
#     print(getCapsuleCountAndAreasLeftDisc(image, POSITION.INNER, show = True))
# endTime = time.time()
# print(f"{(endTime - startTime) * 1000 / repeats} ms")


# Check counts and get areas data for all images in a directyory for left disc - outer polygon
# filesInDirectory = listFiles("C:/Geltec Data - 19 April/Left Disc with 1by25 exposure/")
# columnNames = []
# for i in range(72):
#     columnNames.append(f"{i}")
# df = pd.DataFrame(columns=columnNames)
# for index, file in enumerate(filesInDirectory):
#     image = cv2.imread(f"D:/Left Disc/frames/{file}")
#     okImage = checkExclusionAreaClear(image, leftDiscEntryExclusionPolygonPoints, leftDiscExitExclusionPolygonPoints)
#     if okImage:
#         count, areas = getCapsuleCountAndAreasLeftDisc(image, POSITION.OUTER)
#         l = len(areas)
#         if l < 70:
#             for i in range(70-l):
#                 areas.append(0)
#         else:
#             areas = areas[:70]
#         areas.insert(0, f"{file}")
#         areas.append(count)
#         num_rows = len(df)
#         print(num_rows, len(areas))
#         df.loc[num_rows] = areas
#         print(f"{file} : {count}")
# df.to_csv('D:/Left Disc/areas-outer.csv', header=False, index=False)

# # Check counts and get areas data for all images in a directyory for left disc - middle polygon
# filesInDirectory = listFiles("C:/Geltec Data - 19 April/Left Disc with 1by25 exposure/")
# columnNames = []
# for i in range(72):
#     columnNames.append(f"{i}")
# df = pd.DataFrame(columns=columnNames)
# for index, file in enumerate(filesInDirectory):
#     image = cv2.imread(f"D:/Left Disc/frames/{file}")
#     okImage = checkExclusionAreaClear(image, leftDiscEntryExclusionPolygonPoints, leftDiscExitExclusionPolygonPoints)
#     if okImage:
#         count, areas = getCapsuleCountAndAreasLeftDisc(image, POSITION.MIDDLE)
#         l = len(areas)
#         if l < 70:
#             for i in range(70-l):
#                 areas.append(0)
#         else:
#             areas = areas[:70]
#         areas.insert(0, f"{file}")
#         areas.append(count)
#         num_rows = len(df)
#         print(num_rows, len(areas))
#         df.loc[num_rows] = areas
#         print(f"{file} : {count}")
# df.to_csv('D:/Left Disc/areas-middle.csv', header=False, index=False)

# Check counts and get areas data for all images in a directory for left disc - inner polygon
# filesInDirectory = listFiles("C:/Geltec Data - 19 April/Left Disc with 1by25 exposure/")
# columnNames = []
# for i in range(72):
#     columnNames.append(f"{i}")
# df = pd.DataFrame(columns=columnNames)
# for index, file in enumerate(filesInDirectory):
#     image = cv2.imread(f"D:/Left Disc/frames/{file}")
#     okImage = checkExclusionAreaClear(image, leftDiscEntryExclusionPolygonPoints, leftDiscExitExclusionPolygonPoints)
#     if okImage:
#         count, areas = getCapsuleCountAndAreasLeftDisc(image, POSITION.INNER)
#         l = len(areas)
#         if l < 70:
#             for i in range(70-l):
#                 areas.append(0)
#         else:
#             areas = areas[:70]
#         areas.insert(0, f"{file}")
#         areas.append(count)
#         num_rows = len(df)
#         print(num_rows, len(areas))
#         df.loc[num_rows] = areas
#         print(f"{file} : {count}")
# df.to_csv('D:/Left Disc/areas-inner.csv', header=False, index=False)

# *********************************************LEFT DISC CHECKS END************************************************
# from concurrent.futures import ThreadPoolExecutor, wait
# pool1 = ThreadPoolExecutor(max_workers=3)
#
# def processCounts(anImage, positions=[POSITION.INNER, POSITION.MIDDLE, POSITION.OUTER]):
#     with pool1:
#         startTime = time.time()
#         for i in range(50):
#             futures = [pool1.submit(getCapsuleCountAndAreasLeftDisc, anImage, position) for position in positions]
#             wait(futures)
#             for future in futures:
#                 count, areas = future.result(timeout=0.3)
#                 print(count, areas)
#         endTime = time.time()
#         print((endTime - startTime)*1000/50)
#
# image = cv2.imread("D:/Left Disc/frames/2024-04-08-17-56-57-124-173.png")
# processCounts(image)
