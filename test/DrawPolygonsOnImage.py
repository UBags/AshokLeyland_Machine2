import cv2
from Configuration import *
from utils.CosThetaFileUtils import *

# rightDiscEntryExclusionPolygonPoints = CosThetaConfigurator.getInstance().getRightDiscExclusionEntryPolygonPoints()
# rightDiscExitExclusionPolygonPoints = CosThetaConfigurator.getInstance().getRightDiscExclusionExitPolygonPoints()
# rightDiscInnerPolygonPoints = CosThetaConfigurator.getInstance().getRightDiscInnerPolygonPoints()
# rightDiscMiddlePolygonPoints = CosThetaConfigurator.getInstance().getRightDiscMiddlePolygonPoints()
# rightDiscOuterPolygonPoints = CosThetaConfigurator.getInstance().getRightDiscOuterPolygonPoints()


def drawPolygonsOnLeftDiscImages(cust_name : str, med_name : str, inputDirectory : str, outputDirectory : str = "Polygons/"):
    filesInDirectory = listFilesInDirectory(inputDirectory)
    outputDirectory = f"{inputDirectory}{outputDirectory}"
    leftDiscEntryExclusionPolygonPoints = CosThetaConfigurator.getInstance().getLeftDiscExclusionEntryPolygonPoints(cust_name=cust_name, med_name=med_name)
    leftDiscExitExclusionPolygonPoints = CosThetaConfigurator.getInstance().getLeftDiscExclusionExitPolygonPoints(cust_name=cust_name, med_name=med_name)
    leftDiscInnerPolygonPoints = CosThetaConfigurator.getInstance().getLeftDiscInnerPolygonPoints(cust_name=cust_name, med_name=med_name)
    leftDiscMiddlePolygonPoints = CosThetaConfigurator.getInstance().getLeftDiscMiddlePolygonPoints(cust_name=cust_name, med_name=med_name)
    leftDiscOuterPolygonPoints = CosThetaConfigurator.getInstance().getLeftDiscOuterPolygonPoints(cust_name=cust_name, med_name=med_name)
    # print(leftDiscEntryExclusionPolygonPoints)
    leftDiscEntryExclusionPolygonPoints = leftDiscEntryExclusionPolygonPoints.reshape((-1, 1, 2))
    leftDiscExitExclusionPolygonPoints = leftDiscExitExclusionPolygonPoints.reshape((-1, 1, 2))
    leftDiscInnerPolygonPoints = leftDiscInnerPolygonPoints.reshape((-1, 1, 2))
    leftDiscMiddlePolygonPoints = leftDiscMiddlePolygonPoints.reshape((-1, 1, 2))
    leftDiscOuterPolygonPoints = leftDiscOuterPolygonPoints.reshape((-1, 1, 2))
    # print(leftDiscEntryExclusionPolygonPoints)
    for file in filesInDirectory:
        inputFile = f"{inputDirectory}{file}"
        outputFile = f"{outputDirectory}{file}"
        im = cv2.imread(inputFile)
        print(im.shape)
        im = cv2.polylines(im, [leftDiscEntryExclusionPolygonPoints],
                              isClosed=True, color=(0,0,255), thickness=1)
        im = cv2.polylines(im, [leftDiscExitExclusionPolygonPoints],
                              isClosed=True, color=(0,0,255), thickness=1)
        im = cv2.polylines(im, [leftDiscInnerPolygonPoints],
                              isClosed=True, color=(255,0,0), thickness=1)
        im = cv2.polylines(im, [leftDiscMiddlePolygonPoints],
                              isClosed=True, color=(255,0,0), thickness=1)
        im = cv2.polylines(im, [leftDiscOuterPolygonPoints],
                              isClosed=True, color=(255,0,0), thickness=1)
        saveFileWithFullPath(im,outputFile)


def drawPolygonsOnRightDiscImages(cust_name : str, med_name : str, inputDirectory : str, outputDirectory : str = "Polygons/"):
    filesInDirectory = listFilesInDirectory(inputDirectory)
    outputDirectory = f"{inputDirectory}{outputDirectory}"
    rightDiscEntryExclusionPolygonPoints = CosThetaConfigurator.getInstance().getRightDiscExclusionEntryPolygonPoints(cust_name=cust_name, med_name=med_name)
    rightDiscExitExclusionPolygonPoints = CosThetaConfigurator.getInstance().getRightDiscExclusionExitPolygonPoints(cust_name=cust_name, med_name=med_name)
    rightDiscInnerPolygonPoints = CosThetaConfigurator.getInstance().getRightDiscInnerPolygonPoints(cust_name=cust_name, med_name=med_name)
    rightDiscMiddlePolygonPoints = CosThetaConfigurator.getInstance().getRightDiscMiddlePolygonPoints(cust_name=cust_name, med_name=med_name)
    rightDiscOuterPolygonPoints = CosThetaConfigurator.getInstance().getRightDiscOuterPolygonPoints(cust_name=cust_name, med_name=med_name)
    rightDiscEntryExclusionPolygonPoints = rightDiscEntryExclusionPolygonPoints.reshape((-1, 1, 2))
    rightDiscExitExclusionPolygonPoints = rightDiscExitExclusionPolygonPoints.reshape((-1, 1, 2))
    rightDiscInnerPolygonPoints = rightDiscInnerPolygonPoints.reshape((-1, 1, 2))
    rightDiscMiddlePolygonPoints = rightDiscMiddlePolygonPoints.reshape((-1, 1, 2))
    rightDiscOuterPolygonPoints = rightDiscOuterPolygonPoints.reshape((-1, 1, 2))
    for file in filesInDirectory:
        inputFile = f"{inputDirectory}{file}"
        outputFile = f"{outputDirectory}{file}"
        im = cv2.imread(inputFile)
        print(im.shape)
        im = cv2.polylines(im, [rightDiscEntryExclusionPolygonPoints],
                              isClosed=True, color=(0,0,255), thickness=1)
        im = cv2.polylines(im, [rightDiscExitExclusionPolygonPoints],
                              isClosed=True, color=(0,0,255), thickness=1)
        im = cv2.polylines(im, [rightDiscInnerPolygonPoints],
                              isClosed=True, color=(255,0,0), thickness=1)
        im = cv2.polylines(im, [rightDiscMiddlePolygonPoints],
                              isClosed=True, color=(255,0,0), thickness=1)
        im = cv2.polylines(im, [rightDiscOuterPolygonPoints],
                              isClosed=True, color=(255,0,0), thickness=1)
        saveFileWithFullPath(im,outputFile)


# drawPolygonsOnLeftDiscImages(cust_name="GSK", med_name="BECADEXAMIN", inputDirectory="C:/Temp/TrialImages/Left Disc/")
# drawPolygonsOnRightDiscImages(cust_name="GSK", med_name="BECADEXAMIN", inputDirectory="C:/Temp/TrialImages/Right Disc/")
# drawPolygonsOnLeftDiscImages(cust_name="HERBALIFE", med_name="OMEGA3", inputDirectory="C:/Temp/TrialImages/Left Disc/")
drawPolygonsOnRightDiscImages(cust_name="HERBALIFE", med_name="OMEGA3", inputDirectory="C:/Temp/TrialImages/Right Disc/")