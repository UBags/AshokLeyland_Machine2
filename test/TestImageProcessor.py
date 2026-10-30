import copy
import os
import threading
import time
from threading import Thread
from queue import *
import cv2
import numpy as np

from utils.RedisUtils import *
from processors.GenericQueueProcessor import *
from utils.CosThetaFileUtils import *
from BaseUtils import *
from video.ImageProcessingFunctions import *
from concurrent.futures import ThreadPoolExecutor, wait
from frontend.frontendutils.CosThetaImageUtils import *

import warnings
warnings.filterwarnings('ignore', '.*h264.*', )

from Configuration import *
CosThetaConfigurator.getInstance()

SHOW_RED_CAPSULES_IMAGE = False
PRINT_ONLY_MIN_MAX_AREAS = True

# CONSTANTS
CAM_ID = "camid"
TIME_OF_MESSAGE="timeOfMessage"
MESSAGE="Message"
PICTURE="Picture"
FASTEST_POSSIBLE_TIME_BETWEEN_PICS = CosThetaConfigurator.getInstance().getFastestPossibleTimeBetweenTakePicRequests()
MAX_ALLOWED_DURATION_FOR_PICTURES = FASTEST_POSSIBLE_TIME_BETWEEN_PICS
MAX_MULTIPLE_ALLOWED_FOR_PIC_DELAY = CosThetaConfigurator.getInstance().getMaxAllowedMultipleForPicDelay()
MAX_ALLOWED_TIME_FOR_TAKING_PICTURE = CosThetaConfigurator.getInstance().getMaxAllowedTimeForTakingPicture()
CUTOFF_TIME_THAT_TRIGGERS_CAMERA_CONNECTION_RENEWAL = CosThetaConfigurator.getInstance().getCutoffTimeThatTriggersCameraConnectionRenewal()

# GLOBAL VARIABLES
camId = None
logSource = getFullyQualifiedName(__file__)
averageTimeBetweenTakePicRequests = FASTEST_POSSIBLE_TIME_BETWEEN_PICS
takeNextPicTimeDurations = [averageTimeBetweenTakePicRequests for i in range(10)]
unservicedRequestTimes = []
previousTakeNextPicTime : float = 0.
currentTakeNextPicTime : float = 0.
latestTimeTakePicMessageGenerated_Temp : float = 0

resultsFromPictureAssessment = [[INVALID_RESULT, None], [INVALID_RESULT, None], [INVALID_RESULT, None]]
resultsStatus = [False, False, False]
resultsLock = threading.Lock()

from enum import Enum

class POSITION(Enum):
    OUTER = 1
    MIDDLE = 2
    INNER = 3


class ImageProcessor():

    logSource = getFullyQualifiedName(__file__)

    font = cv2.FONT_HERSHEY_SIMPLEX
    # fontScale = 0.7
    fontScale = 1.1
    greencolor = (0, 255, 0)
    redcolor = (0, 0, 255)
    bluecolor = (255, 0, 0)
    thickness = 2

    # cust_name : str = "GSK"
    # med_name : str = "BECADEXAMIN"

    cust_name : str = "HERBALIFE"
    med_name : str = "OMEGA3"

    # RIGHT_DISC_OUTER_MIN_AREA = 15
    # RIGHT_DISC_OUTER_MAX_AREA = 282.5
    # RIGHT_DISC_MIDDLE_MIN_AREA = 9  # NOT USED. INSTEAD, REJECTION/ACCEPTANCE OF MIN IS NOW BASED ON AVERAGE NEIGHBOUR SIZES
    # RIGHT_DISC_MIDDLE_MAX_AREA = 293
    # RIGHT_DISC_INNER_MIN_AREA = 21  # NOT USED. INSTEAD, REJECTION/ACCEPTANCE OF MIN IS NOW BASED ON AVERAGE NEIGHBOUR SIZES
    # RIGHT_DISC_INNER_MAX_AREA = 277.5
    #
    # LEFT_DISC_OUTER_MIN_AREA = 8
    # LEFT_DISC_OUTER_MAX_AREA = 295
    # LEFT_DISC_MIDDLE_MIN_AREA = 40  # NOT USED. INSTEAD, REJECTION/ACCEPTANCE OF MIN IS NOW BASED ON AVERAGE NEIGHBOUR SIZES
    # LEFT_DISC_MIDDLE_MAX_AREA = 295
    # LEFT_DISC_INNER_MIN_AREA = 72.5  # NOT USED. INSTEAD, REJECTION/ACCEPTANCE OF MIN IS NOW BASED ON AVERAGE NEIGHBOUR SIZES
    # LEFT_DISC_INNER_MAX_AREA = 295

    BASE_MIN = CosThetaConfigurator.getInstance().getBaseMinAreaSize(cust_name=cust_name, med_name=med_name)
    FASTEST_GAP_BETWEEN_TAKEPIC_AND_BOTTLERELEASE = CosThetaConfigurator.getInstance().getFastestPossibleTimeBetweenTakePicAndBottleRelease()

    rightDiscEntryExclusionPolygonPoints = CosThetaConfigurator.getInstance().getRightDiscExclusionEntryPolygonPoints(cust_name=cust_name, med_name=med_name)
    rightDiscExitExclusionPolygonPoints = CosThetaConfigurator.getInstance().getRightDiscExclusionExitPolygonPoints(cust_name=cust_name, med_name=med_name)
    rightDiscInnerPolygonPoints = CosThetaConfigurator.getInstance().getRightDiscInnerPolygonPoints(cust_name=cust_name, med_name=med_name)
    rightDiscMiddlePolygonPoints = CosThetaConfigurator.getInstance().getRightDiscMiddlePolygonPoints(cust_name=cust_name, med_name=med_name)
    rightDiscOuterPolygonPoints = CosThetaConfigurator.getInstance().getRightDiscOuterPolygonPoints(cust_name=cust_name, med_name=med_name)
    rightDiscDesiredOuterCount = CosThetaConfigurator.getInstance().getRightDiscDesiredOuterCount(cust_name=cust_name, med_name=med_name)
    rightDiscDesiredMiddleCount = CosThetaConfigurator.getInstance().getRightDiscDesiredMiddleCount(cust_name=cust_name, med_name=med_name)
    rightDiscDesiredInnerCount = CosThetaConfigurator.getInstance().getRightDiscDesiredInnerCount(cust_name=cust_name, med_name=med_name)
    rightDiscNeighbouringDistance = CosThetaConfigurator.getInstance().getNeighbouringDistanceForRightDisc(cust_name=cust_name, med_name=med_name)
    rightDiscMinAreaFraction = CosThetaConfigurator.getInstance().getMinAreaFractionForRightDisc(cust_name=cust_name, med_name=med_name)
    rightDiscInnerCoordinates = CosThetaConfigurator.getInstance().getRightDiscInnerCoordinates(cust_name=cust_name, med_name=med_name)
    rightDiscMiddleCoordinates = CosThetaConfigurator.getInstance().getRightDiscMiddleCoordinates(cust_name=cust_name, med_name=med_name)
    rightDiscOuterCoordinates = CosThetaConfigurator.getInstance().getRightDiscOuterCoordinates(cust_name=cust_name, med_name=med_name)

    leftDiscEntryExclusionPolygonPoints = CosThetaConfigurator.getInstance().getLeftDiscExclusionEntryPolygonPoints(cust_name=cust_name, med_name=med_name)
    leftDiscExitExclusionPolygonPoints = CosThetaConfigurator.getInstance().getLeftDiscExclusionExitPolygonPoints(cust_name=cust_name, med_name=med_name)
    leftDiscInnerPolygonPoints = CosThetaConfigurator.getInstance().getLeftDiscInnerPolygonPoints(cust_name=cust_name, med_name=med_name)
    leftDiscMiddlePolygonPoints = CosThetaConfigurator.getInstance().getLeftDiscMiddlePolygonPoints(cust_name=cust_name, med_name=med_name)
    leftDiscOuterPolygonPoints = CosThetaConfigurator.getInstance().getLeftDiscOuterPolygonPoints(cust_name=cust_name, med_name=med_name)
    leftDiscDesiredOuterCount = CosThetaConfigurator.getInstance().getLeftDiscDesiredOuterCount(cust_name=cust_name, med_name=med_name)
    leftDiscDesiredMiddleCount = CosThetaConfigurator.getInstance().getLeftDiscDesiredMiddleCount(cust_name=cust_name, med_name=med_name)
    leftDiscDesiredInnerCount = CosThetaConfigurator.getInstance().getLeftDiscDesiredInnerCount(cust_name=cust_name, med_name=med_name)
    leftDiscNeighbouringDistance = CosThetaConfigurator.getInstance().getNeighbouringDistanceForLeftDisc(cust_name=cust_name, med_name=med_name)
    leftDiscMinAreaFraction = CosThetaConfigurator.getInstance().getMinAreaFractionForLeftDisc(cust_name=cust_name, med_name=med_name)
    leftDiscInnerCoordinates = CosThetaConfigurator.getInstance().getLeftDiscInnerCoordinates(cust_name=cust_name, med_name=med_name)
    leftDiscMiddleCoordinates = CosThetaConfigurator.getInstance().getLeftDiscMiddleCoordinates(cust_name=cust_name, med_name=med_name)
    leftDiscOuterCoordinates = CosThetaConfigurator.getInstance().getLeftDiscOuterCoordinates(cust_name=cust_name, med_name=med_name)

    overallResultCoordinateFractionOfDimension = (0.65, 0.15)
    overallResultCoordinates = None
    TEXT_TO_BE_WRITTEN_ON_IMAGES_WITH_INVALID_RESULT = "Invalid Image"

    # def setCustomerAndMedicine(self, cust_name : str, med_name: str):
    #     ImageProcessor.cust_name = cust_name
    #     ImageProcessor.med_name = med_name

    def __init__(self, pictureResultIndex : int = 1, cameraId : int = 1):
        ImageProcessor.logSource = getFullyQualifiedName(__file__, __class__)
        if cameraId not in [1,2]:
            raise Exception("Invalid camera queue")
        self.pictureResultIndex = pictureResultIndex
        self.cameraId = cameraId
        # self.pool = ThreadPoolExecutor(max_workers=4)
        # self.saveQueue = CosThetaConfigurator.getInstance().getSavePictureQueue(self.cameraId)
        # self.frontendQueue = CosThetaConfigurator.getInstance().getCameraResultQueue(self.cameraId)
        # self.actuatorQueue = CosThetaConfigurator.getInstance().getActuatorActionQueue(self.cameraId)
        if self.cameraId == 2:
            self.entryExclusionPolygonPoints = ImageProcessor.rightDiscEntryExclusionPolygonPoints
            self.exitExclusionPolygonPoints = ImageProcessor.rightDiscExitExclusionPolygonPoints
            self.innerPolygonPoints = ImageProcessor.rightDiscInnerPolygonPoints
            self.middlePolygonPoints = ImageProcessor.rightDiscMiddlePolygonPoints
            self.outerPolygonPoints = ImageProcessor.rightDiscOuterPolygonPoints
            self.desiredOuterCount = ImageProcessor.rightDiscDesiredOuterCount
            self.desiredMiddleCount = ImageProcessor.rightDiscDesiredMiddleCount
            self.desiredInnerCount = ImageProcessor.rightDiscDesiredInnerCount
            self.neighbouringDistance = ImageProcessor.rightDiscNeighbouringDistance
            self.minAreaFraction = ImageProcessor.rightDiscMinAreaFraction
            self.outerMinArea = CosThetaConfigurator.getInstance().getRightDiscOuterMinArea(cust_name=ImageProcessor.cust_name, med_name=ImageProcessor.med_name)
            self.outerMaxArea = CosThetaConfigurator.getInstance().getRightDiscOuterMaxArea(cust_name=ImageProcessor.cust_name, med_name=ImageProcessor.med_name)
            self.middleMinArea = CosThetaConfigurator.getInstance().getRightDiscMiddleMinArea(cust_name=ImageProcessor.cust_name, med_name=ImageProcessor.med_name)
            self.middleMaxArea = CosThetaConfigurator.getInstance().getRightDiscMiddleMaxArea(cust_name=ImageProcessor.cust_name, med_name=ImageProcessor.med_name)
            self.innerMinArea = CosThetaConfigurator.getInstance().getRightDiscInnerMinArea(cust_name=ImageProcessor.cust_name, med_name=ImageProcessor.med_name)
            self.innerMaxArea = CosThetaConfigurator.getInstance().getRightDiscInnerMaxArea(cust_name=ImageProcessor.cust_name, med_name=ImageProcessor.med_name)
            self.innerCoordinates = ImageProcessor.rightDiscInnerCoordinates
            self.middleCoordinates = ImageProcessor.rightDiscMiddleCoordinates
            self.outerCoordinates = ImageProcessor.rightDiscOuterCoordinates
        elif cameraId == 1:
            self.entryExclusionPolygonPoints = ImageProcessor.leftDiscEntryExclusionPolygonPoints
            self.exitExclusionPolygonPoints = ImageProcessor.leftDiscExitExclusionPolygonPoints
            self.innerPolygonPoints = ImageProcessor.leftDiscInnerPolygonPoints
            self.middlePolygonPoints = ImageProcessor.leftDiscMiddlePolygonPoints
            self.outerPolygonPoints = ImageProcessor.leftDiscOuterPolygonPoints
            self.desiredOuterCount = ImageProcessor.leftDiscDesiredOuterCount
            self.desiredMiddleCount = ImageProcessor.leftDiscDesiredMiddleCount
            self.desiredInnerCount = ImageProcessor.leftDiscDesiredInnerCount
            self.neighbouringDistance = ImageProcessor.leftDiscNeighbouringDistance
            self.minAreaFraction = ImageProcessor.leftDiscMinAreaFraction
            self.outerMinArea = CosThetaConfigurator.getInstance().getLeftDiscOuterMinArea(cust_name=ImageProcessor.cust_name, med_name=ImageProcessor.med_name)
            self.outerMaxArea = CosThetaConfigurator.getInstance().getLeftDiscOuterMaxArea(cust_name=ImageProcessor.cust_name, med_name=ImageProcessor.med_name)
            self.middleMinArea = CosThetaConfigurator.getInstance().getLeftDiscMiddleMinArea(cust_name=ImageProcessor.cust_name, med_name=ImageProcessor.med_name)
            self.middleMaxArea = CosThetaConfigurator.getInstance().getLeftDiscMiddleMaxArea(cust_name=ImageProcessor.cust_name, med_name=ImageProcessor.med_name)
            self.innerMinArea = CosThetaConfigurator.getInstance().getLeftDiscInnerMinArea(cust_name=ImageProcessor.cust_name, med_name=ImageProcessor.med_name)
            self.innerMaxArea = CosThetaConfigurator.getInstance().getLeftDiscInnerMaxArea(cust_name=ImageProcessor.cust_name, med_name=ImageProcessor.med_name)
            self.innerCoordinates = ImageProcessor.leftDiscInnerCoordinates
            self.middleCoordinates = ImageProcessor.leftDiscMiddleCoordinates
            self.outerCoordinates = ImageProcessor.leftDiscOuterCoordinates
        self.hostname = CosThetaConfigurator.getInstance().getRedisHost()
        self.port = CosThetaConfigurator.getInstance().getRedisPort()
        self.redisConnection = None
        self.clientRedisConnected = False
        self.connectToRedis()
        self.timeOfReceivingDiscInput = 0

        self.currentCopyOfImage = None
        self.imageWritingLock = threading.Lock()
        # self.setDebug(debugAddItem=True, debugGetItem=True, debugProcessItem=True, debugWork=True, debugOtherExceptions=True)

    def writeEntryExclusionDataOnImage(self, result : str):
        self.currentCopyOfImage = cv2.putText(self.currentCopyOfImage, f"EntryExclusion:{result}", (30, 40), ImageProcessor.font,
                                              ImageProcessor.fontScale, ImageProcessor.greencolor if result == ok else ImageProcessor.redcolor, ImageProcessor.thickness, cv2.LINE_AA)

    def writeExitExclusionDataOnImage(self, result : str):
        self.currentCopyOfImage = cv2.putText(self.currentCopyOfImage, f"; ExitExclusion:{result}", (325, 40), ImageProcessor.font,
                                              ImageProcessor.fontScale, ImageProcessor.greencolor if result == ok else ImageProcessor.redcolor, ImageProcessor.thickness, cv2.LINE_AA)

    def writeInnerMaxAndMinAreaDataOnImage(self, max:int, min:int):
        self.currentCopyOfImage = cv2.putText(self.currentCopyOfImage, f"Inner: MaxArea = {max}; MinArea = {min}; ", (30, 90), ImageProcessor.font,
                                              ImageProcessor.fontScale, ImageProcessor.bluecolor, ImageProcessor.thickness, cv2.LINE_AA)

    def writeInnerExcludedAreasDataOnImage(self, areasAndFraction : list):
        for i, areaAndFraction in enumerate(areasAndFraction):
            self.currentCopyOfImage = cv2.putText(self.currentCopyOfImage, f"Area = {areaAndFraction[0]}; Fraction = {areaAndFraction[1]}; ", (500 + i*350, 90), ImageProcessor.font,
                                                  ImageProcessor.fontScale, ImageProcessor.redcolor, ImageProcessor.thickness, cv2.LINE_AA)

    def writeMiddleMaxAndMinAreaDataOnImage(self, max:int, min:int):
        self.currentCopyOfImage = cv2.putText(self.currentCopyOfImage, f"Middle: MaxArea = {max}; MinArea = {min}; ", (30, 140), ImageProcessor.font,
                                              ImageProcessor.fontScale, ImageProcessor.bluecolor, ImageProcessor.thickness, cv2.LINE_AA)

    def writeMiddleExcludedAreasDataOnImage(self, areasAndFraction : list):
        for i, areaAndFraction in enumerate(areasAndFraction):
            self.currentCopyOfImage = cv2.putText(self.currentCopyOfImage, f"Area = {areaAndFraction[0]}; Fraction = {areaAndFraction[1]}; ", (500 + i*350, 140), ImageProcessor.font,
                                                  ImageProcessor.fontScale, ImageProcessor.redcolor, ImageProcessor.thickness, cv2.LINE_AA)

    def writeOuterMaxAndMinAreaDataOnImage(self, max:int, min:int):
        self.currentCopyOfImage = cv2.putText(self.currentCopyOfImage, f"Outer: MaxArea = {max}; MinArea = {min}; ", (30, 190), ImageProcessor.font,
                                              ImageProcessor.fontScale, ImageProcessor.bluecolor, ImageProcessor.thickness, cv2.LINE_AA)

    def writeOuterExcludedAreasDataOnImage(self, areasAndFraction : list):
        for i, areaAndFraction in enumerate(areasAndFraction):
            self.currentCopyOfImage = cv2.putText(self.currentCopyOfImage, f"Area = {areaAndFraction[0]}; Fraction = {areaAndFraction[1]}; ", (500 + i*350, 190), ImageProcessor.font,
                                                  ImageProcessor.fontScale, ImageProcessor.redcolor, ImageProcessor.thickness, cv2.LINE_AA)

    def writeCountsDataOnImage(self, inner : int, middle : int, outer : int):
        self.currentCopyOfImage = cv2.putText(self.currentCopyOfImage,
                                              f"Inner = {inner}; Middle = {middle}; Outer = {outer}",
                                              (30, 240), ImageProcessor.font,
                                              ImageProcessor.fontScale, ImageProcessor.bluecolor if (inner == 60) and (middle == 60) and (outer == 60) else ImageProcessor.redcolor,
                                              ImageProcessor.thickness, cv2.LINE_AA)

    def connectToRedis(self, forceRenew = False):
        if forceRenew:
            self.redisConnection = None
            self.clientRedisConnected = False
        if not self.clientRedisConnected:
            try:
                self.redisConnection = Redis(self.hostname, self.port, retry_on_timeout=True)
                self.clientRedisConnected = True
            except:
                self.clientRedisConnected = False
                printBoldRed(f'Could not create Redis connection in ImageProcessor')
                # pass

    def checkExclusionAreaClear(self, image: np.ndarray):
        if image is None:
            return False
        # print("Reached 1 in checkExclusionAreaClear()")
        try:
            workingImage = copy.deepcopy(image)
            workingImage = reduceSize(workingImage, reductionfactor=2.0)
            exclusionPolygonPoints = copy.deepcopy(self.entryExclusionPolygonPoints)
            exclusionPolygonPoints = (exclusionPolygonPoints / 2.0).astype(np.int32)
            cropped, _ = cropToRectangleAndResizeImage(workingImage, exclusionPolygonPoints)
            croppedImage = cropImageToExactPolygonShape(cropped, exclusionPolygonPoints)
            croppedImage = cv2.cvtColor(croppedImage, cv2.COLOR_BGR2GRAY)
            _, croppedImage = cv2.threshold(croppedImage, 164, 255, cv2.THRESH_BINARY_INV)
            croppedImage = dilate(croppedImage, kernel_33)
            _, croppedImage = cv2.threshold(croppedImage, 192, 255, cv2.THRESH_BINARY)
            # showImageWithRGBValues("Exit Exclusion", croppedImage)
            # cv2.waitKey(0)
            contours, hierarchy = cv2.findContours(croppedImage, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
            if len(contours) > 0:
                # print("Reached 2 in checkExclusionAreaClear()")
                with self.imageWritingLock:
                    self.writeEntryExclusionDataOnImage(notok)
                return False
            with self.imageWritingLock:
                # print("Reached 3 in checkExclusionAreaClear()")
                self.writeEntryExclusionDataOnImage(ok)
            # printBold("Entry exclusion is OK")
            exclusionPolygonPoints = copy.deepcopy(self.exitExclusionPolygonPoints)
            if not PRINT_ONLY_MIN_MAX_AREAS:
                print(exclusionPolygonPoints)
            exclusionPolygonPoints = (exclusionPolygonPoints / 2.0).astype(np.int32)
            cropped, _ = cropToRectangleAndResizeImage(workingImage, exclusionPolygonPoints)
            croppedImage = cropImageToExactPolygonShape(cropped, exclusionPolygonPoints)
            croppedImage = cv2.cvtColor(croppedImage, cv2.COLOR_BGR2GRAY)
            _, croppedImage = cv2.threshold(croppedImage, 148, 255, cv2.THRESH_BINARY_INV)
            croppedImage = dilate(croppedImage, kernel_33)
            _, croppedImage = cv2.threshold(croppedImage, 192, 255, cv2.THRESH_BINARY)
            # showImageWithRGBValues("Exit Exclusion", croppedImage)
            # cv2.waitKey(0)
            contours, hierarchy = cv2.findContours(croppedImage, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
            if len(contours) > 0:
                with self.imageWritingLock:
                    # print("Reached 4 in checkExclusionAreaClear()")
                    self.writeExitExclusionDataOnImage(notok)
                return False
            with self.imageWritingLock:
                # print("Reached 5 in checkExclusionAreaClear()")
                self.writeExitExclusionDataOnImage(ok)
            return True
        except:
            return False

    '''
    # For GSK-BECADEXAMIN
    def getCapsuleCountAndAreas(self, image : np.ndarray, whichPosition: POSITION):
        if image is None:
            printBoldRed(f"As picture is None, returning 0,[]")
            return 0,[]

        try:
            minArea = self.innerMinArea if whichPosition == POSITION.INNER else self.middleMinArea if whichPosition == POSITION.MIDDLE else self.outerMinArea
            maxArea = self.innerMaxArea if whichPosition == POSITION.INNER else self.middleMaxArea if whichPosition == POSITION.MIDDLE else self.outerMaxArea
            somePolygonPoints = self.innerPolygonPoints if whichPosition == POSITION.INNER else self.middlePolygonPoints if whichPosition == POSITION.MIDDLE else self.outerPolygonPoints
            imageCopy = copy.deepcopy(image)
            cropped, _ = cropToRectangleAndResizeImage(imageCopy, somePolygonPoints)
            croppedImage = cropImageToExactPolygonShape(cropped, somePolygonPoints)
            b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
            # croppedImage[np.logical_and.reduce([r_ch - g_ch >= 10, r_ch < 180])] = (0, 0, 255)
            # croppedImage[np.logical_and.reduce([r_ch - g_ch >= 12, r_ch >= 180])] = (0, 0, 255)
            # croppedImage[np.logical_and.reduce([r_ch - g_ch >= 8, g_ch - b_ch >= 4, r_ch < 128])] = (0, 0, 255)
            # croppedImage[np.logical_and.reduce([r_ch - g_ch >= 6, g_ch - b_ch >= 6, r_ch < 128])] = (0, 0, 255)
            # croppedImage[np.logical_and.reduce([r_ch - g_ch >= 4, g_ch - b_ch >= 8, r_ch < 144])] = (0, 0, 255)
            croppedImage[np.logical_and.reduce([r_ch - g_ch >= 10, r_ch < 180])] = (0, 0, 255)
            croppedImage[np.logical_and.reduce([r_ch - g_ch >= 12, r_ch >= 180])] = (0, 0, 255)
            croppedImage[np.logical_and.reduce([r_ch - g_ch >= 10, g_ch - b_ch >= 3, r_ch >= 180])] = (0, 0, 255)
            croppedImage[np.logical_and.reduce([r_ch - g_ch >= 8, g_ch - b_ch >= 6, r_ch < 224])] = (0, 0, 255)
            croppedImage[np.logical_and.reduce([r_ch - g_ch >= 8, g_ch - b_ch >= 4, r_ch < 128])] = (0, 0, 255)
            croppedImage[np.logical_and.reduce([r_ch - g_ch >= 6, g_ch - b_ch >= 6, r_ch < 128])] = (0, 0, 255)
            croppedImage[np.logical_and.reduce([r_ch - g_ch >= 4, g_ch - b_ch >= 8, r_ch < 144])] = (0, 0, 255)
            b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
            # NOTE, NOTE, NOTE : This line has been added. If there's a problem, delete it !!
            croppedImage[np.logical_and.reduce([r_ch - g_ch >= 8, g_ch - b_ch >= 2, r_ch < 208])] = (
            0, 0, 255)  # This line has been added. If there's a problem, delete it !!
            croppedImage[np.logical_and.reduce([r_ch - g_ch < 2])] = (255, 255, 255)
            croppedImage[np.logical_and.reduce([r_ch - b_ch < 5])] = (255, 255, 255)
            b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
            croppedImage[r_ch != 255] = (255, 255, 255)
            croppedImage[np.logical_and.reduce([r_ch == 255, g_ch == 0])] = (0, 0, 0)
            blackAndWhiteImage = cv2.cvtColor(croppedImage, cv2.COLOR_BGR2GRAY)
            blackAndWhiteImage = 255 - blackAndWhiteImage
            blackAndWhiteImage[blackAndWhiteImage != 255] = 0
            blackAndWhiteImage = cv2.copyMakeBorder(blackAndWhiteImage, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=0)
            blackAndWhiteImage = morphclose(blackAndWhiteImage, kernel_55)
            blackAndWhiteImage = morphopen(blackAndWhiteImage, kernel_33)
            blackAndWhiteImage = morphclose(blackAndWhiteImage, kernel_33)
            blackAndWhiteImage[blackAndWhiteImage != 255] = 0
            contours, hierarchy = cv2.findContours(blackAndWhiteImage, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
            nTablets = 0
            rawCoordinateAndAreaData = []
            currentMinArea = 100000
            currentMaxArea = -100000
            whichSet = "Outer" if whichPosition == POSITION.OUTER else "Middle" if whichPosition == POSITION.MIDDLE else "Inner"
            for contour in contours:
                area = cv2.contourArea(contour)
                currentMaxArea = max(currentMaxArea, area)
                currentMinArea = min(currentMinArea, area)
                # eliminate the ones that are larger than max area. That leaves out the min area issue
                if area > maxArea:
                    if not PRINT_ONLY_MIN_MAX_AREAS:
                        printBoldRed(f"Eliminating a capsule from {whichSet} because {area = } > {maxArea = }")
                    continue
                x, y, w, h = cv2.boundingRect(contour)
                xCentre = x + w // 2
                yCentre = y + h // 2
                centre = (xCentre, yCentre)
                rawCoordinateAndAreaData.append([centre, area])
                if area < minArea:
                    continue
                nTablets += 1
            if not PRINT_ONLY_MIN_MAX_AREAS:
                printLight(f"               Initial tablet count : {nTablets}")
            if whichPosition == POSITION.INNER:
                with self.imageWritingLock:
                    self.writeInnerMaxAndMinAreaDataOnImage(currentMaxArea, currentMinArea)
            elif whichPosition == POSITION.MIDDLE:
                with self.imageWritingLock:
                    self.writeMiddleMaxAndMinAreaDataOnImage(currentMaxArea, currentMinArea)
            if whichPosition == POSITION.OUTER:
                with self.imageWritingLock:
                    self.writeOuterMaxAndMinAreaDataOnImage(currentMaxArea, currentMinArea)
            # printLight(f"{whichPosition}:                Reached here - 2")
            finalCoordinateAndAreaData = []
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
                    distance = math.sqrt((x1 - x2) * (x1 - x2) + (y1 - y2) * (y1 - y2))
                    if distance <= self.neighbouringDistance:
                        surroundingAreas.append(area2)
                if len(surroundingAreas) > 0:
                    surroundingAreas = np.array(surroundingAreas)
                    averageSurroundingArea = np.mean(surroundingAreas)
                else:
                    averageSurroundingArea = 0
                if averageSurroundingArea > 0:  # since this is for a loner coordinate, eliminate this coordinate
                    if whichPosition == POSITION.INNER or whichPosition == POSITION.MIDDLE:
                        if (area1 > averageSurroundingArea * self.minAreaFraction) and (area1 > ImageProcessor.BASE_MIN):
                            finalCoordinateAndAreaData.append(entry)
                            areas.append(area1)
                        else:
                            if not PRINT_ONLY_MIN_MAX_AREAS:
                                printBoldRed(f"Excluding area {area1 = } as its fraction is less than {self.minAreaFraction = }")
                            excludedAreasAndFractions.append([area1, round(area1 / averageSurroundingArea, 3)])
                    else:
                        if (area1 > minArea):
                            finalCoordinateAndAreaData.append(entry)
                            areas.append(area1)
                        else:
                            if not PRINT_ONLY_MIN_MAX_AREAS:
                                printBoldRed(f"Excluding area {area1 = } as it is less than {minArea = }")
                            excludedAreasAndFractions.append([area1, round(area1 / minArea, 3)])

            # printLight(f"{whichPosition}:                Reached here - 3")
            # printLight(f"               Excluded Areas = {excludedAreasAndFractions}")
            if whichPosition == POSITION.INNER:
                with self.imageWritingLock:
                    self.writeInnerExcludedAreasDataOnImage(excludedAreasAndFractions)
            elif whichPosition == POSITION.MIDDLE:
                with self.imageWritingLock:
                    self.writeMiddleExcludedAreasDataOnImage(excludedAreasAndFractions)
            if whichPosition == POSITION.OUTER:
                with self.imageWritingLock:
                    self.writeOuterExcludedAreasDataOnImage(excludedAreasAndFractions)
            printGreen(f"In {whichSet}, MaxArea = {currentMaxArea}, minArea = {currentMinArea}")
            nTablets = len(finalCoordinateAndAreaData)
            if not PRINT_ONLY_MIN_MAX_AREAS:
                printBold(f"                Picture is not None, returning {nTablets}, {areas}")
            return nTablets, areas
        except Exception as e:
            printBoldRed(f"             Encountered exception {e}")
            return 0,[]
    '''

    # For HERBALIFE-OMEGA3
    # OLD METHOD - BASED ON RGB VALUES AND DIFFERENCES ETC FOR THE LEFT DISC
    '''
    def getCapsuleCountAndAreas(self, image : np.ndarray, whichDisk : str, whichPosition: POSITION):
        if image is None:
            printBoldRed(f"As picture is None, returning 0,[]")
            return 0,[]

        try:

            minArea = self.innerMinArea if whichPosition == POSITION.INNER else self.middleMinArea if whichPosition == POSITION.MIDDLE else self.outerMinArea
            maxArea = self.innerMaxArea if whichPosition == POSITION.INNER else self.middleMaxArea if whichPosition == POSITION.MIDDLE else self.outerMaxArea
            somePolygonPoints = self.innerPolygonPoints if whichPosition == POSITION.INNER else self.middlePolygonPoints if whichPosition == POSITION.MIDDLE else self.outerPolygonPoints
            imageCopy = copy.deepcopy(image)
            cropped, _ = cropToRectangleAndResizeImage(imageCopy, somePolygonPoints)
            croppedImage = cropImageToExactPolygonShape(cropped, somePolygonPoints)
            croppedCopy = copy.deepcopy(croppedImage)

            b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)

            # Begin - This part works perfectly for the right disc for Herbalife
            if whichDisk.upper() == "RIGHT":
                croppedImage[np.logical_and.reduce([r_ch - g_ch <= -4])] = (255, 255, 255)
                b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
                croppedImage[np.logical_and.reduce([r_ch - g_ch >= -3, g_ch - b_ch >= 16, b_ch < 240])] = (0, 0, 255)
                croppedImage[np.logical_and.reduce([r_ch - g_ch >= -2, g_ch - b_ch >= 13, b_ch < 240])] = (0, 0, 255)
                croppedImage[np.logical_and.reduce([r_ch - g_ch >= -1, g_ch - b_ch >= 13, b_ch < 240])] = (0, 0, 255)
                croppedImage[np.logical_and.reduce([r_ch - g_ch >= 0, g_ch - b_ch >= 12, b_ch < 240])] = (0, 0, 255)
                croppedImage[np.logical_and.reduce([r_ch - g_ch >= 1, g_ch - b_ch >= 11, b_ch < 240])] = (0, 0, 255)
                croppedImage[np.logical_and.reduce([r_ch - g_ch >= 2, g_ch - b_ch >= 10, b_ch < 240])] = (0, 0, 255)
                croppedImage[np.logical_and.reduce([r_ch - g_ch >= 3, g_ch - b_ch >= 9, b_ch < 240])] = (0, 0, 255)
                croppedImage[np.logical_and.reduce([r_ch - g_ch >= 4, g_ch - b_ch >= 9, b_ch < 240])] = (0, 0, 255)
                croppedImage[np.logical_and.reduce([r_ch - g_ch >= 5, g_ch - b_ch >= 8, b_ch < 240])] = (0, 0, 255)
                croppedImage[np.logical_and.reduce([r_ch - g_ch >= 6, g_ch - b_ch >= 6, b_ch < 240])] = (0, 0, 255)
            # End - This part works perfectly for the right disc for Herbalife


            # Begin - This is the experiment for the left disc for Herbalife
            if whichDisk.upper() == "LEFT":

                croppedImage[np.logical_and.reduce([r_ch > 24, g_ch - b_ch >= 18, r_ch < 224])] = (0, 0, 255)
                b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)

                croppedImage[np.logical_and.reduce([r_ch -g_ch <= -4])] = (255, 255, 255)
                b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)

                # croppedImage[np.logical_and.reduce([r_ch - g_ch >= -3, g_ch - b_ch >= 18, b_ch < 240, r_ch < 245])] = (0, 0, 255)
                # croppedImage[np.logical_and.reduce([r_ch - g_ch >= -2, g_ch - b_ch >= 18, b_ch < 240, r_ch < 245])] = (0, 0, 255)
                # croppedImage[np.logical_and.reduce([r_ch - g_ch >= -1, g_ch - b_ch >= 17, b_ch < 240, r_ch < 245])] = (0, 0, 255)
                # croppedImage[np.logical_and.reduce([r_ch - g_ch >= 0, g_ch - b_ch >= 16, b_ch < 240, r_ch < 245])] = (0, 0, 255)
                # croppedImage[np.logical_and.reduce([r_ch - g_ch >= 1, g_ch - b_ch >= 15, b_ch < 240, r_ch < 245])] = (0, 0, 255)

                # croppedImage[np.logical_and.reduce([r_ch - g_ch >= -3, g_ch - b_ch >= 16, b_ch < 240, r_ch < 245])] = (0, 0, 255)
                croppedImage[np.logical_and.reduce([r_ch - g_ch >= -3, g_ch - b_ch >= 13, b_ch < 240, r_ch < 245])] = (0, 0, 255)
                croppedImage[np.logical_and.reduce([r_ch - g_ch >= -1, g_ch - b_ch >= 11, b_ch < 240, r_ch < 245])] = (0, 0, 255)
                b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)

                croppedImage[np.logical_and.reduce([r_ch - g_ch >= 0, g_ch - b_ch >= 13, b_ch < 240, r_ch < 245])] = (0, 0, 255)
                croppedImage[np.logical_and.reduce([r_ch - g_ch >= 1, g_ch - b_ch >= 12, b_ch < 240, r_ch < 245])] = (0, 0, 255)

                b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
                croppedImage[np.logical_and.reduce([r_ch <= g_ch - 1, g_ch <= b_ch + 1, b_ch <= 240, r_ch < 245])] = (255, 255, 255)
                croppedImage[np.logical_and.reduce([r_ch == g_ch, g_ch - b_ch >= 10, r_ch > 140])] = (255, 255, 255)
                b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)

                # self.currentCopyOfImage[np.logical_and.reduce([r_ch >= g_ch + 3, r_ch < 245])] = (0, 0, 255)
                croppedImage[np.logical_and.reduce([r_ch >= g_ch + 1, g_ch - b_ch >= 12, r_ch < 245])] = (0, 0, 255)
                croppedImage[np.logical_and.reduce([r_ch >= g_ch - 1, g_ch - b_ch >= 14, r_ch <= 172])] = (0, 0, 255)
                b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)

                croppedImage[np.logical_and.reduce([r_ch >= g_ch - 1, g_ch - b_ch >= 9, r_ch <= 128])] = (0, 0, 255)
                croppedImage[np.logical_and.reduce([r_ch >= g_ch - 1, g_ch - b_ch >= 11, r_ch <= 172])] = (0, 0, 255)
                croppedImage[np.logical_and.reduce([r_ch >= g_ch - 1, g_ch - b_ch >= 13, r_ch <= 212])] = (0, 0, 255)

            # End - This is the experiment for the left disc for Herbalife

            if SHOW_RED_CAPSULES_IMAGE:
                combined_image = np.hstack((croppedCopy, croppedImage))
                showImageWithRGBValues(f"Capsules{combined_image}", combined_image)
                cv2.waitKey(0)

            b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
            croppedImage[np.logical_and.reduce([r_ch < 255, b_ch != 0])] = (255, 255, 255)
            positionID = 1 if whichPosition == POSITION.INNER else 2 if whichPosition == POSITION.MIDDLE else 3
            b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
            croppedImage[r_ch != 255] = (255, 255, 255)
            croppedImage[np.logical_and.reduce([r_ch == 255, g_ch == 0])] = (0, 0, 0)
            redImageCopy = copy.deepcopy(croppedCopy)
            blackAndWhiteImage = cv2.cvtColor(croppedImage, cv2.COLOR_BGR2GRAY)
            blackAndWhiteImage = 255 - blackAndWhiteImage
            blackAndWhiteImage[blackAndWhiteImage != 255] = 0
            blackAndWhiteImage = cv2.copyMakeBorder(blackAndWhiteImage, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=0)
            # showImageWithRGBValues(f"Before opening and closing", blackAndWhiteImage)
            # cv2.waitKey(0)
            # nowTime = time.time()
            # blackAndWhiteImage1 = morphclose(blackAndWhiteImage, kernel_55)
            # afterProcessingTime = time.time()
            # print(f"Time taken to run kernel_55 is {afterProcessingTime - nowTime} secs")
            # showImageWithRGBValues(f"After kernel_55", blackAndWhiteImage1)
            # cv2.waitKey(0)
            # nowTime = time.time()

            # Fill the black spots inside the white background of capsules with white
            num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(blackAndWhiteImage)
            for label in range(1, num_labels):  # Skip background (label 0)
                # Extract each component and work on it
                component_mask = np.zeros_like(blackAndWhiteImage)
                component_mask[labels == label] = 255

                component_image = cv2.bitwise_and(blackAndWhiteImage, component_mask)
                contours, _ = cv2.findContours(component_image, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)

                # Prepare a mask to track filled regions for this component
                # filled_mask = np.zeros(component_image.shape, dtype=np.uint8)

                for contour in contours:
                    # if not contours:
                    #     break  # No more contours to fill in this component
                    # random_index = np.random.randint(0, len(contours))
                    # contour = contours[random_index]
                    x, y, w, h = cv2.boundingRect(contour)
                    if max(w, h) > 200:
                        continue
                    cv2.fillConvexPoly(blackAndWhiteImage, contour, 255)
                    # cv2.fillConvexPoly(filled_mask, contour, 255)

            # afterProcessingTime = time.time()
            # print(f"Time taken to process is {afterProcessingTime - nowTime} secs")
            # nowTime = time.time()
            # blackAndWhiteImage = morphclose(blackAndWhiteImage, kernel_55)
            # afterProcessingTime = time.time()
            # print(f"Time taken to run kernel_55 is {afterProcessingTime - nowTime} secs")
            # showImageWithRGBValues(f"After kernel_55", blackAndWhiteImage)
            # cv2.waitKey(0)
            if whichDisk.upper() == "LEFT":
                blackAndWhiteImage = morphclose(blackAndWhiteImage, kernel_55)
            else:
                blackAndWhiteImage = morphclose(blackAndWhiteImage, kernel_33)
            blackAndWhiteImage = morphopen(blackAndWhiteImage, kernel_33)
            # showImageWithRGBValues(f"After kernel_33", blackAndWhiteImage)
            # cv2.waitKey(0)
            blackAndWhiteImage = morphclose(blackAndWhiteImage, kernel_33)
            # showImageWithRGBValues(f"After second kernel_33", blackAndWhiteImage)
            # cv2.waitKey(0)
            blackAndWhiteImage[blackAndWhiteImage != 255] = 0
            if SHOW_RED_CAPSULES_IMAGE:
                he, wi = blackAndWhiteImage.shape
                bwImage = blackAndWhiteImage[10:he-10, 10:wi-10].copy()
                bwImage = cv2.cvtColor(bwImage, cv2.COLOR_GRAY2BGR)
                combined_image = np.hstack((croppedCopy, redImageCopy, bwImage))
                showImageWithRGBValues(f"Capsules{positionID}", combined_image)
                cv2.waitKey(0)

            contours, hierarchy = cv2.findContours(blackAndWhiteImage, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
            printBoldGreen(f"Found {len(contours)} contours in the image")
            nTablets = 0
            rawCoordinateAndAreaData = []
            currentMinArea = 100000
            currentMaxArea = -100000
            whichSet = "Outer" if whichPosition == POSITION.OUTER else "Middle" if whichPosition == POSITION.MIDDLE else "Inner"
            for contour in contours:
                area = cv2.contourArea(contour)
                currentMaxArea = max(currentMaxArea, area)
                currentMinArea = min(currentMinArea, area)
                # eliminate the ones that are larger than max area. That leaves out the min area issue
                if area > maxArea:
                    # if not PRINT_ONLY_MIN_MAX_AREAS:
                    #     printBoldRed(f"Eliminating a capsule from {whichSet} because {area = } > {maxArea = }")
                    printBoldRed(f"Eliminating a capsule from {whichSet} because {area = } > {maxArea = }")
                    continue
                x, y, w, h = cv2.boundingRect(contour)
                xCentre = x + w // 2
                yCentre = y + h // 2
                centre = (xCentre, yCentre)
                rawCoordinateAndAreaData.append([centre, area])
                if area < minArea:
                    printBoldRed(f"Eliminating a capsule from {whichSet} because {area = } < {minArea = }")
                    continue
                nTablets += 1
                # print(f"Tablet count is {nTablets}")
            print(f"{nTablets = } in position {whichSet}")
            print(f"{len(rawCoordinateAndAreaData)} values of Raw Coordinate and Area Data are {rawCoordinateAndAreaData}")
            if not PRINT_ONLY_MIN_MAX_AREAS:
                printLight(f"               Initial tablet count : {nTablets}")
            if whichPosition == POSITION.INNER:
                with self.imageWritingLock:
                    self.writeInnerMaxAndMinAreaDataOnImage(currentMaxArea, currentMinArea)
            elif whichPosition == POSITION.MIDDLE:
                with self.imageWritingLock:
                    self.writeMiddleMaxAndMinAreaDataOnImage(currentMaxArea, currentMinArea)
            if whichPosition == POSITION.OUTER:
                with self.imageWritingLock:
                    self.writeOuterMaxAndMinAreaDataOnImage(currentMaxArea, currentMinArea)
            # printLight(f"{whichPosition}:                Reached here - 2")
            finalCoordinateAndAreaData = []
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
                    distance = math.sqrt((x1 - x2) * (x1 - x2) + (y1 - y2) * (y1 - y2))
                    if distance <= self.neighbouringDistance:
                        surroundingAreas.append(area2)
                if len(surroundingAreas) > 0:
                    surroundingAreas = np.array(surroundingAreas)
                    averageSurroundingArea = np.mean(surroundingAreas)
                else:
                    averageSurroundingArea = 0
                if averageSurroundingArea > 0:  # since this is for a loner coordinate, eliminate this coordinate
                    if whichPosition == POSITION.INNER or whichPosition == POSITION.MIDDLE:
                        # if (area1 > averageSurroundingArea * self.minAreaFraction) and (area1 > ImageProcessor.BASE_MIN):
                        if (area1 > averageSurroundingArea * self.minAreaFraction) and (area1 > minArea):
                            finalCoordinateAndAreaData.append(entry)
                            areas.append(area1)
                        else:
                            try:
                                currentFraction = area1 / max(1,averageSurroundingArea)
                            except:
                                pass
                            if not PRINT_ONLY_MIN_MAX_AREAS:
                                printBoldRed(f"Excluding area {area1 = } as its fraction is less than {self.minAreaFraction = }")
                            printBoldRed(
                                f"Excluding area {area1 = } as its {currentFraction = } is either less than {self.minAreaFraction = } or less than {minArea}")
                            excludedAreasAndFractions.append([area1, round(area1 / averageSurroundingArea, 3)])
                    else:
                        if (area1 > minArea):
                            finalCoordinateAndAreaData.append(entry)
                            areas.append(area1)
                        else:
                            # if not PRINT_ONLY_MIN_MAX_AREAS:
                            #     printBoldRed(f"Excluding area {area1 = } as it is less than {minArea = }")
                            printBoldRed(f"Excluding area {area1 = } as it is less than {minArea = }")
                            excludedAreasAndFractions.append([area1, round(area1 / minArea, 3)])

            # printLight(f"{whichPosition}:                Reached here - 3")
            # printLight(f"               Excluded Areas = {excludedAreasAndFractions}")
            if whichPosition == POSITION.INNER:
                with self.imageWritingLock:
                    self.writeInnerExcludedAreasDataOnImage(excludedAreasAndFractions)
            elif whichPosition == POSITION.MIDDLE:
                with self.imageWritingLock:
                    self.writeMiddleExcludedAreasDataOnImage(excludedAreasAndFractions)
            if whichPosition == POSITION.OUTER:
                with self.imageWritingLock:
                    self.writeOuterExcludedAreasDataOnImage(excludedAreasAndFractions)
            printGreen(f"In {whichSet}, MaxArea = {currentMaxArea}, minArea = {currentMinArea}")
            nTablets = len(finalCoordinateAndAreaData)
            if not PRINT_ONLY_MIN_MAX_AREAS:
                printBold(f"                Picture is not None, returning {nTablets}, {areas}")
            printBold(f"                Picture is not None, returning {nTablets}, {areas} for pisition {whichSet}")
            cv2.destroyAllWindows()
            return nTablets, areas
        except Exception as e:
            printBoldRed(f"             Encountered exception {e}")
            cv2.destroyAllWindows()
            return 0,[]
'''

    # *******************************************************************************************************

    # For HERBALIFE-OMEGA3
    # NEW METHOD - BASED ON STATS FOR THE LEFT DISC AND ON RGB VALUES & DIFFERENCES FOR THE RIGHT DISC
    def getCapsuleCountAndAreas(self, image : np.ndarray, whichDisk : str, whichPosition: POSITION):
        if image is None:
            printBoldRed(f"As picture is None, returning 0,[]")
            return 0,[]

        try:

            minArea = self.innerMinArea if whichPosition == POSITION.INNER else self.middleMinArea if whichPosition == POSITION.MIDDLE else self.outerMinArea
            maxArea = self.innerMaxArea if whichPosition == POSITION.INNER else self.middleMaxArea if whichPosition == POSITION.MIDDLE else self.outerMaxArea
            somePolygonPoints = self.innerPolygonPoints if whichPosition == POSITION.INNER else self.middlePolygonPoints if whichPosition == POSITION.MIDDLE else self.outerPolygonPoints
            imageCopy = copy.deepcopy(image)
            cropped, _ = cropToRectangleAndResizeImage(imageCopy, somePolygonPoints)
            croppedImage = cropImageToExactPolygonShape(cropped, somePolygonPoints)
            croppedCopy = copy.deepcopy(croppedImage)

            '''
            Note that this part is replicated below to create a consolidated image of the original + red cimage + final image for counting
            '''

            b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)

            # Begin - This part works perfectly for the right disc for Herbalife
            if whichDisk.upper() == "RIGHT":
                croppedImage[np.logical_and.reduce([r_ch - g_ch <= -4])] = (255, 255, 255)
                b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
                croppedImage[np.logical_and.reduce([r_ch - g_ch >= -3, g_ch - b_ch >= 16, b_ch < 240])] = (0, 0, 255)
                croppedImage[np.logical_and.reduce([r_ch - g_ch >= -2, g_ch - b_ch >= 13, b_ch < 240])] = (0, 0, 255)
                croppedImage[np.logical_and.reduce([r_ch - g_ch >= -1, g_ch - b_ch >= 13, b_ch < 240])] = (0, 0, 255)
                croppedImage[np.logical_and.reduce([r_ch - g_ch >= 0, g_ch - b_ch >= 12, b_ch < 240])] = (0, 0, 255)
                croppedImage[np.logical_and.reduce([r_ch - g_ch >= 1, g_ch - b_ch >= 11, b_ch < 240])] = (0, 0, 255)
                croppedImage[np.logical_and.reduce([r_ch - g_ch >= 2, g_ch - b_ch >= 10, b_ch < 240])] = (0, 0, 255)
                croppedImage[np.logical_and.reduce([r_ch - g_ch >= 3, g_ch - b_ch >= 9, b_ch < 240])] = (0, 0, 255)
                croppedImage[np.logical_and.reduce([r_ch - g_ch >= 4, g_ch - b_ch >= 9, b_ch < 240])] = (0, 0, 255)
                croppedImage[np.logical_and.reduce([r_ch - g_ch >= 5, g_ch - b_ch >= 8, b_ch < 240])] = (0, 0, 255)
                croppedImage[np.logical_and.reduce([r_ch - g_ch >= 6, g_ch - b_ch >= 6, b_ch < 240])] = (0, 0, 255)
            # End - This part works perfectly for the right disc for Herbalife

                if SHOW_RED_CAPSULES_IMAGE:
                    combined_image = np.hstack((croppedCopy, croppedImage))
                    showImageWithRGBValues(f"Capsules{combined_image}", combined_image)
                    cv2.waitKey(0)

                b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
                croppedImage[np.logical_and.reduce([r_ch < 255, b_ch != 0])] = (255, 255, 255)
                positionID = 1 if whichPosition == POSITION.INNER else 2 if whichPosition == POSITION.MIDDLE else 3
                b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
                croppedImage[r_ch != 255] = (255, 255, 255)
                croppedImage[np.logical_and.reduce([r_ch == 255, g_ch == 0])] = (0, 0, 0)
                redImageCopy = copy.deepcopy(croppedCopy)
                blackAndWhiteImage = cv2.cvtColor(croppedImage, cv2.COLOR_BGR2GRAY)
                blackAndWhiteImage = 255 - blackAndWhiteImage
                blackAndWhiteImage[blackAndWhiteImage != 255] = 0
                blackAndWhiteImage = cv2.copyMakeBorder(blackAndWhiteImage, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=0)
                # showImageWithRGBValues(f"Before opening and closing", blackAndWhiteImage)
                # cv2.waitKey(0)
                # nowTime = time.time()
                # blackAndWhiteImage1 = morphclose(blackAndWhiteImage, kernel_55)
                # afterProcessingTime = time.time()
                # print(f"Time taken to run kernel_55 is {afterProcessingTime - nowTime} secs")
                # showImageWithRGBValues(f"After kernel_55", blackAndWhiteImage1)
                # cv2.waitKey(0)
                # nowTime = time.time()

                # Fill the black spots inside the white background of capsules with white
                num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(blackAndWhiteImage)
                for label in range(1, num_labels):  # Skip background (label 0)
                    # Extract each component and work on it
                    component_mask = np.zeros_like(blackAndWhiteImage)
                    component_mask[labels == label] = 255

                    component_image = cv2.bitwise_and(blackAndWhiteImage, component_mask)
                    contours, _ = cv2.findContours(component_image, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)

                    # Prepare a mask to track filled regions for this component
                    # filled_mask = np.zeros(component_image.shape, dtype=np.uint8)

                    for contour in contours:
                        # if not contours:
                        #     break  # No more contours to fill in this component
                        # random_index = np.random.randint(0, len(contours))
                        # contour = contours[random_index]
                        x, y, w, h = cv2.boundingRect(contour)
                        if max(w, h) > 200:
                            continue
                        cv2.fillConvexPoly(blackAndWhiteImage, contour, 255)
                        # cv2.fillConvexPoly(filled_mask, contour, 255)

                # afterProcessingTime = time.time()
                # print(f"Time taken to process is {afterProcessingTime - nowTime} secs")
                # nowTime = time.time()
                # blackAndWhiteImage = morphclose(blackAndWhiteImage, kernel_55)
                # afterProcessingTime = time.time()
                # print(f"Time taken to run kernel_55 is {afterProcessingTime - nowTime} secs")
                # showImageWithRGBValues(f"After kernel_55", blackAndWhiteImage)
                # cv2.waitKey(0)
                if whichDisk.upper() == "LEFT":
                    blackAndWhiteImage = morphclose(blackAndWhiteImage, kernel_55)
                else:
                    blackAndWhiteImage = morphclose(blackAndWhiteImage, kernel_33)
                blackAndWhiteImage = morphopen(blackAndWhiteImage, kernel_33)
                # showImageWithRGBValues(f"After kernel_33", blackAndWhiteImage)
                # cv2.waitKey(0)
                blackAndWhiteImage = morphclose(blackAndWhiteImage, kernel_33)
                # showImageWithRGBValues(f"After second kernel_33", blackAndWhiteImage)
                # cv2.waitKey(0)
                blackAndWhiteImage[blackAndWhiteImage != 255] = 0
                if SHOW_RED_CAPSULES_IMAGE:
                    he, wi = blackAndWhiteImage.shape
                    bwImage = blackAndWhiteImage[10:he-10, 10:wi-10].copy()
                    bwImage = cv2.cvtColor(bwImage, cv2.COLOR_GRAY2BGR)
                    combined_image = np.hstack((croppedCopy, redImageCopy, bwImage))
                    showImageWithRGBValues(f"Capsules{positionID}", combined_image)
                    cv2.waitKey(0)

                contours, hierarchy = cv2.findContours(blackAndWhiteImage, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
                printBoldGreen(f"Found {len(contours)} contours in the image")
                nTablets = 0
                rawCoordinateAndAreaData = []
                currentMinArea = 100000
                currentMaxArea = -100000
                whichSet = "Outer" if whichPosition == POSITION.OUTER else "Middle" if whichPosition == POSITION.MIDDLE else "Inner"
                for contour in contours:
                    area = cv2.contourArea(contour)
                    currentMaxArea = max(currentMaxArea, area)
                    currentMinArea = min(currentMinArea, area)
                    # eliminate the ones that are larger than max area. That leaves out the min area issue
                    if area > maxArea:
                        # if not PRINT_ONLY_MIN_MAX_AREAS:
                        #     printBoldRed(f"Eliminating a capsule from {whichSet} because {area = } > {maxArea = }")
                        printBoldRed(f"Eliminating a capsule from {whichSet} because {area = } > {maxArea = }")
                        continue
                    x, y, w, h = cv2.boundingRect(contour)
                    xCentre = x + w // 2
                    yCentre = y + h // 2
                    centre = (xCentre, yCentre)
                    rawCoordinateAndAreaData.append([centre, area])
                    if area < minArea:
                        printBoldRed(f"Eliminating a capsule from {whichSet} because {area = } < {minArea = }")
                        continue
                    nTablets += 1
                    # print(f"Tablet count is {nTablets}")
                print(f"{nTablets = } in position {whichSet}")
                print(f"{len(rawCoordinateAndAreaData)} values of Raw Coordinate and Area Data are {rawCoordinateAndAreaData}")
                if not PRINT_ONLY_MIN_MAX_AREAS:
                    printLight(f"               Initial tablet count : {nTablets}")
                if whichPosition == POSITION.INNER:
                    with self.imageWritingLock:
                        self.writeInnerMaxAndMinAreaDataOnImage(currentMaxArea, currentMinArea)
                elif whichPosition == POSITION.MIDDLE:
                    with self.imageWritingLock:
                        self.writeMiddleMaxAndMinAreaDataOnImage(currentMaxArea, currentMinArea)
                if whichPosition == POSITION.OUTER:
                    with self.imageWritingLock:
                        self.writeOuterMaxAndMinAreaDataOnImage(currentMaxArea, currentMinArea)
                # printLight(f"{whichPosition}:                Reached here - 2")
                finalCoordinateAndAreaData = []
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
                        distance = math.sqrt((x1 - x2) * (x1 - x2) + (y1 - y2) * (y1 - y2))
                        if distance <= self.neighbouringDistance:
                            surroundingAreas.append(area2)
                    if len(surroundingAreas) > 0:
                        surroundingAreas = np.array(surroundingAreas)
                        averageSurroundingArea = np.mean(surroundingAreas)
                    else:
                        averageSurroundingArea = 0
                    if averageSurroundingArea > 0:  # since this is for a loner coordinate, eliminate this coordinate
                        if whichPosition == POSITION.INNER or whichPosition == POSITION.MIDDLE:
                            # if (area1 > averageSurroundingArea * self.minAreaFraction) and (area1 > ImageProcessor.BASE_MIN):
                            if (area1 > averageSurroundingArea * self.minAreaFraction) and (area1 > minArea):
                                finalCoordinateAndAreaData.append(entry)
                                areas.append(area1)
                            else:
                                try:
                                    currentFraction = area1 / max(1,averageSurroundingArea)
                                except:
                                    pass
                                if not PRINT_ONLY_MIN_MAX_AREAS:
                                    printBoldRed(f"Excluding area {area1 = } as its fraction is less than {self.minAreaFraction = }")
                                printBoldRed(
                                    f"Excluding area {area1 = } as its {currentFraction = } is either less than {self.minAreaFraction = } or less than {minArea}")
                                excludedAreasAndFractions.append([area1, round(area1 / averageSurroundingArea, 3)])
                        else:
                            if (area1 > minArea):
                                finalCoordinateAndAreaData.append(entry)
                                areas.append(area1)
                            else:
                                # if not PRINT_ONLY_MIN_MAX_AREAS:
                                #     printBoldRed(f"Excluding area {area1 = } as it is less than {minArea = }")
                                printBoldRed(f"Excluding area {area1 = } as it is less than {minArea = }")
                                excludedAreasAndFractions.append([area1, round(area1 / minArea, 3)])

                # printLight(f"{whichPosition}:                Reached here - 3")
                # printLight(f"               Excluded Areas = {excludedAreasAndFractions}")
                if whichPosition == POSITION.INNER:
                    with self.imageWritingLock:
                        self.writeInnerExcludedAreasDataOnImage(excludedAreasAndFractions)
                elif whichPosition == POSITION.MIDDLE:
                    with self.imageWritingLock:
                        self.writeMiddleExcludedAreasDataOnImage(excludedAreasAndFractions)
                if whichPosition == POSITION.OUTER:
                    with self.imageWritingLock:
                        self.writeOuterExcludedAreasDataOnImage(excludedAreasAndFractions)
                printGreen(f"In {whichSet}, MaxArea = {currentMaxArea}, minArea = {currentMinArea}")
                nTablets = len(finalCoordinateAndAreaData)
                if not PRINT_ONLY_MIN_MAX_AREAS:
                    printBold(f"                Picture is not None, returning {nTablets}, {areas}")
                printBold(f"                Picture is not None, returning {nTablets}, {areas} for pisition {whichSet}")
                cv2.destroyAllWindows()
                return nTablets, areas

            # Begin - This is the experiment for the left disc for Herbalife
            if whichDisk.upper() == "LEFT":

                # croppedCopy[np.logical_and.reduce([r_ch > 245, g_ch > 245, b_ch > 240])] = (255, 255, 255)
                # mask = copy.deepcopy(croppedCopy)
                # mask = cv2.cvtColor(mask, cv2.COLOR_BGR2GRAY)
                # mask[mask != 255] = 0
                # mask = morphclose(mask, kernel_33)
                # mask = morphopen(mask, kernel_55)
                # mask = 255-mask
                # showImageWithRGBValues("Mask", mask)
                # cv2.waitKey(0)
                # croppedImage[np.logical_and.reduce([r_ch > 245, g_ch > 245, b_ch > 240])] = (255,255,255)

                mask = copy.deepcopy(croppedCopy)
                mask = cv2.cvtColor(mask, cv2.COLOR_BGR2GRAY)
                ret, mask = cv2.threshold(mask, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                # mask = cv2.cvtColor(mask, cv2.COLOR_BGR2GRAY)
                mask[mask != 255] = 0
                mask = morphopen(mask, kernel_55)
                mask = morphclose(mask, kernel_33)
                mask = 255-mask
                boolean_mask = mask < 127
                startTime = time.time()
                croppedImage[boolean_mask] = (255, 255, 255)
                endTime = time.time()
                print(f"Whitening image time = {1000*(endTime - startTime)} ms")

                num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(mask)
                nTablets = 0
                for label in range(1, num_labels):  # Skip background (label 0)
                    x = stats[label, cv2.CC_STAT_LEFT]
                    y = stats[label, cv2.CC_STAT_TOP]
                    w = stats[label, cv2.CC_STAT_WIDTH]
                    h = stats[label, cv2.CC_STAT_HEIGHT]
                    area = stats[label, cv2.CC_STAT_AREA]

                    if area < 64:
                        continue

                    region = croppedImage[y:y + h, x:x + w]
                    b_ch, g_ch, r_ch = getChannelsAsInt(region)

                    rg_abs = np.abs(r_ch - g_ch)
                    gb_abs = np.abs(g_ch - b_ch)
                    rb_abs = np.abs(r_ch - b_ch)

                    rg = r_ch - g_ch
                    gb = g_ch - b_ch
                    rb = r_ch - b_ch

                    # Improved Masking and Handling of Empty Regions
                    rg_mask = rg_abs != 255  # Use 255 for uint8 images
                    gb_mask = gb_abs != 255
                    rb_mask = rb_abs != 255

                    # Handle potential empty arrays due to masking:
                    if np.any(rg_mask):
                        avg_rg_abs = np.mean(rg_abs[rg_mask])
                        std_rg_abs = np.std(rg_abs[rg_mask])
                        avg_rg = np.mean(rg[rg_mask])
                        std_rg = np.std(rg[rg_mask])
                        r = np.mean(r_ch[rg_mask])
                    else:
                        avg_rg_abs, std_rg_abs, avg_rg, std_rg, r = np.nan, np.nan, np.nan, np.nan, np.nan

                    if np.any(gb_mask):
                        avg_gb_abs = np.mean(gb_abs[gb_mask])
                        std_gb_abs = np.std(gb_abs[gb_mask])
                        avg_gb = np.mean(gb[gb_mask])
                        std_gb = np.std(gb[gb_mask])
                        g = np.mean(g_ch[rg_mask])
                    else:
                        avg_gb_abs, std_gb_abs, avg_gb, std_gb, g = np.nan, np.nan, np.nan, np.nan, np.nan

                    if np.any(rb_mask):
                        avg_rb_abs = np.mean(rb_abs[rb_mask])
                        std_rb_abs = np.std(rb_abs[rb_mask])
                        avg_rb = np.mean(rb[rb_mask])
                        std_rb = np.std(rb[rb_mask])
                        b = np.mean(b_ch[rg_mask])
                    else:
                        avg_rb_abs, std_rb_abs, avg_rb, std_rb, b = np.nan, np.nan, np.nan, np.nan, np.nan

                    # results[label] = {
                    #     'avg_rg': avg_rg, 'std_rg': std_rg,
                    #     'avg_gb': avg_gb, 'std_gb': std_gb,
                    #     'avg_rb': avg_rb, 'std_rb': std_rb
                    # }

                    isCapsule = True
                    allAvgLessThan5_5 = ((avg_rg_abs < 5.5) and (avg_gb_abs < 5.5) and (avg_rb_abs < 5.5))
                    allStdDevLessThan4 = ((std_rg_abs < 4) and (std_gb_abs < 4) and (std_rb_abs < 4))
                    any2AvgLessThan6 = ((avg_rg_abs < 6) and (avg_gb_abs < 6)) or ((avg_rg_abs < 6) and (avg_rb_abs < 6)) or ((avg_rb_abs < 6) and (avg_gb_abs < 6))
                    any2StdDevLessThan6 = ((std_rg_abs < 6) and (std_gb_abs < 6)) or ((std_rg_abs < 6) and (std_rb_abs < 6)) or ((std_rb_abs < 6) and (std_gb_abs < 6))
                    any2StdDevLessThan2_25 = ((std_rg_abs < 2.25) and (std_gb_abs < 2.25)) or ((std_rg_abs < 2.25) and (std_rb_abs < 2.25)) or ((std_rb_abs < 2.25) and (std_gb_abs < 2.25))
                    try:
                        stdRG2AvgRG_GreaterThanAtLeast1Other = (std_rg_abs / avg_rg_abs > 1.01 * std_gb_abs / avg_gb_abs) or (std_rg_abs / avg_rg_abs > 1.01 * std_rb_abs / avg_rb_abs)
                    except:
                        stdRG2AvgRG_GreaterThanAtLeast1Other = True

                    try:
                        stdRB2AvgRB_Or_stdGB2AvgGB_LessThan_Point8 = (std_rg_abs / avg_rg_abs < 0.8) or (std_rg_abs / avg_rg_abs < 0.8)
                    except:
                        stdRB2AvgRB_Or_stdGB2AvgGB_LessThan_Point8 = True

                        # isCapsule = False if ((avg_gb < 2) or ((avg_rg < 0.4) and (std_rg < 0.5) and (avg_gb < 7.5) and (avg_rb < 7.5)) or (any2AvgLessThan6 and any2StdDevLessThan2_5) or (any2AvgLessThan6 and any2StdDevLessThan6 and stdRG2AvgRB_GreaterThanAtLeast1Other) or ((avg_rg < 1.5) and (std_rg < 1) and (abs(std_gb / avg_gb - std_rb / avg_rb) <= 0.06) and (avg_gb < 7.5) and (avg_rb < 7.5) and ((std_gb / avg_gb < 1) or (std_rb / avg_rb < 1)))) else True
                    isCapsule = False if ((avg_gb_abs < 2) or (allAvgLessThan5_5 and allStdDevLessThan4 and avg_rg <= 0.06) or ((avg_rg_abs < 0.4) and (std_rg_abs < 0.5) and (avg_gb_abs < 7.5) and (avg_rb_abs < 7.5) and (std_rg_abs / avg_rg_abs < 1.25)) or (any2AvgLessThan6 and any2StdDevLessThan2_25) or (any2AvgLessThan6 and any2StdDevLessThan6 and stdRG2AvgRG_GreaterThanAtLeast1Other and stdRB2AvgRB_Or_stdGB2AvgGB_LessThan_Point8)) else True
                    # print(f"[RGB] {r:.0f} , {g:.0f} , {b:.0f}; "
                    #       f"[Avg] {avg_rg:.2f} , {avg_gb:.2f} , {avg_rb:.2f}; "
                    #       f"[Std] {std_rg:.2f} , {std_gb:.2f} , {std_rb:.2f}; "
                    #       f"[Avg-Abs] {avg_rg_abs:.2f} , {avg_gb_abs:.2f} , {avg_rb_abs:.2f}; "
                    #       f"[Std-Abs] {std_rg_abs:.2f} , {std_gb_abs:.2f} , {std_rb_abs:.2f}; "
                    #       f"[Div] {(std_rg_abs / avg_rg_abs if avg_rg_abs != 0 else 0):.2f} , "
                    #       f"{(std_gb_abs / avg_gb_abs if avg_gb_abs != 0 else 0):.2f} , "
                    #       f"{(std_rb_abs / avg_rb_abs if avg_rb_abs != 0 else 0):.2f} - {'Green' if isCapsule else 'Red'}")
                    if isCapsule:
                        # cv2.rectangle(croppedImageCopy, (x, y), (x + w, y + h), (0, 255, 0), 2)
                        nTablets += 1
                    else:
                        # cv2.rectangle(croppedImageCopy, (x, y), (x + w, y + h), (0, 0, 255), 2)
                        pass
                print(f"Number of tablets = {nTablets}")
                # showImageWithRGBValues("Image", croppedImageCopy)
                # cv2.waitKey(0)
                cv2.destroyAllWindows()
                return nTablets, []

            # End - This is the experiment for the left disc for Herbalife
            '''
            End of the part that needs to be ensured that it is replicated exactly below
            '''

        except Exception as e:
            printBoldRed(f"             Encountered exception {e}")
            cv2.destroyAllWindows()
            return 0,[]





    # *******************************************************************************************************

    def processCounts(self, anImage : Union[np.ndarray, str], whichDisk : str, positions=None):
        # print(f"Entered process Counts")
        if isinstance(anImage, str):
            dir1, file1 = os.path.split(anImage)
            printBold(f"Processing file {file1}")
            anImage = cv2.imread(anImage)
        self.currentCopyOfImage = copy.deepcopy(anImage)

        '''
        Transforming the whole image so that we can see the final transformed image along with the original. We will see
        3 images - original, red capsules, and B & W capsules
        '''

        b_ch, g_ch, r_ch = getChannelsAsInt(self.currentCopyOfImage)

        # Begin - This part works perfectly for the right disc for Herbalife
        if whichDisk.upper() == "RIGHT":
            print(f"For display, using the routine for disc {whichDisk}")
            self.currentCopyOfImage[np.logical_and.reduce([r_ch - g_ch <= -4])] = (255, 255, 255)
            b_ch, g_ch, r_ch = getChannelsAsInt(self.currentCopyOfImage)
            self.currentCopyOfImage[np.logical_and.reduce([r_ch - g_ch >= -3, g_ch - b_ch >= 13, b_ch < 240])] = (0, 0, 255)
            self.currentCopyOfImage[np.logical_and.reduce([r_ch - g_ch >= -2, g_ch - b_ch >= 13, b_ch < 240])] = (0, 0, 255)
            self.currentCopyOfImage[np.logical_and.reduce([r_ch - g_ch >= -1, g_ch - b_ch >= 12, b_ch < 240])] = (0, 0, 255)
            self.currentCopyOfImage[np.logical_and.reduce([r_ch - g_ch >= 0, g_ch - b_ch >= 12, b_ch < 240])] = (0, 0, 255)
            self.currentCopyOfImage[np.logical_and.reduce([r_ch - g_ch >= 1, g_ch - b_ch >= 11, b_ch < 240])] = (0, 0, 255)
            self.currentCopyOfImage[np.logical_and.reduce([r_ch - g_ch >= 2, g_ch - b_ch >= 10, b_ch < 240])] = (0, 0, 255)
            self.currentCopyOfImage[np.logical_and.reduce([r_ch - g_ch >= 3, g_ch - b_ch >= 9, b_ch < 240])] = (0, 0, 255)
            self.currentCopyOfImage[np.logical_and.reduce([r_ch - g_ch >= 4, g_ch - b_ch >= 9, b_ch < 240])] = (0, 0, 255)
            self.currentCopyOfImage[np.logical_and.reduce([r_ch - g_ch >= 5, g_ch - b_ch >= 8, b_ch < 240])] = (0, 0, 255)
            self.currentCopyOfImage[np.logical_and.reduce([r_ch - g_ch >= 6, g_ch - b_ch >= 6, b_ch < 240])] = (0, 0, 255)
        # End - This part works perfectly for the right disc for Herbalife

        # Begin - This is the experiment for the left disc for Herbalife
        if whichDisk.upper() == "LEFT":

            self.currentCopyOfImage[np.logical_and.reduce([r_ch > 24, g_ch - b_ch >= 18, r_ch < 224])] = (0, 0, 255)
            b_ch, g_ch, r_ch = getChannelsAsInt(self.currentCopyOfImage)

            self.currentCopyOfImage[np.logical_and.reduce([r_ch - g_ch <= -4])] = (255, 255, 255)
            b_ch, g_ch, r_ch = getChannelsAsInt(self.currentCopyOfImage)

            # croppedImage[np.logical_and.reduce([r_ch - g_ch >= -3, g_ch - b_ch >= 18, b_ch < 240, r_ch < 245])] = (0, 0, 255)
            # croppedImage[np.logical_and.reduce([r_ch - g_ch >= -2, g_ch - b_ch >= 18, b_ch < 240, r_ch < 245])] = (0, 0, 255)
            # croppedImage[np.logical_and.reduce([r_ch - g_ch >= -1, g_ch - b_ch >= 17, b_ch < 240, r_ch < 245])] = (0, 0, 255)
            # croppedImage[np.logical_and.reduce([r_ch - g_ch >= 0, g_ch - b_ch >= 16, b_ch < 240, r_ch < 245])] = (0, 0, 255)
            # croppedImage[np.logical_and.reduce([r_ch - g_ch >= 1, g_ch - b_ch >= 15, b_ch < 240, r_ch < 245])] = (0, 0, 255)

            # croppedImage[np.logical_and.reduce([r_ch - g_ch >= -3, g_ch - b_ch >= 16, b_ch < 240, r_ch < 245])] = (0, 0, 255)
            self.currentCopyOfImage[np.logical_and.reduce([r_ch - g_ch >= -3, g_ch - b_ch >= 13, b_ch < 240, r_ch < 245])] = (
            0, 0, 255)
            self.currentCopyOfImage[np.logical_and.reduce([r_ch - g_ch >= -1, g_ch - b_ch >= 11, b_ch < 240, r_ch < 245])] = (
            0, 0, 255)
            b_ch, g_ch, r_ch = getChannelsAsInt(self.currentCopyOfImage)

            self.currentCopyOfImage[np.logical_and.reduce([r_ch - g_ch >= 0, g_ch - b_ch >= 13, b_ch < 240, r_ch < 245])] = (
            0, 0, 255)
            self.currentCopyOfImage[np.logical_and.reduce([r_ch - g_ch >= 1, g_ch - b_ch >= 12, b_ch < 240, r_ch < 245])] = (
            0, 0, 255)

            b_ch, g_ch, r_ch = getChannelsAsInt(self.currentCopyOfImage)
            self.currentCopyOfImage[np.logical_and.reduce([r_ch <= g_ch - 1, g_ch <= b_ch + 1, b_ch <= 240, r_ch < 245])] = (
            255, 255, 255)
            self.currentCopyOfImage[np.logical_and.reduce([r_ch == g_ch, g_ch - b_ch >= 10, r_ch > 140])] = (255, 255, 255)
            b_ch, g_ch, r_ch = getChannelsAsInt(self.currentCopyOfImage)

            # self.currentCopyOfImage[np.logical_and.reduce([r_ch >= g_ch + 3, r_ch < 245])] = (0, 0, 255)
            self.currentCopyOfImage[np.logical_and.reduce([r_ch >= g_ch + 1, g_ch - b_ch >= 12, r_ch < 245])] = (0, 0, 255)
            self.currentCopyOfImage[np.logical_and.reduce([r_ch >= g_ch - 1, g_ch - b_ch >= 14, r_ch <= 172])] = (0, 0, 255)
            b_ch, g_ch, r_ch = getChannelsAsInt(self.currentCopyOfImage)

            self.currentCopyOfImage[np.logical_and.reduce([r_ch >= g_ch - 1, g_ch - b_ch >= 9, r_ch <= 128])] = (0, 0, 255)
            self.currentCopyOfImage[np.logical_and.reduce([r_ch >= g_ch - 1, g_ch - b_ch >= 11, r_ch <= 172])] = (0, 0, 255)
            self.currentCopyOfImage[np.logical_and.reduce([r_ch >= g_ch - 1, g_ch - b_ch >= 13, r_ch <= 212])] = (0, 0, 255)

            # self.currentCopyOfImage[np.logical_and.reduce([r_ch >= g_ch - 1, g_ch - b_ch >= 6, r_ch <= 140])] = (0, 0, 255)

        # End - This is the experiment for the left disc for Herbalife

        b_ch, g_ch, r_ch = getChannelsAsInt(self.currentCopyOfImage)
        self.currentCopyOfImage[np.logical_and.reduce([r_ch < 255, b_ch != 0])] = (255, 255, 255)
        b_ch, g_ch, r_ch = getChannelsAsInt(self.currentCopyOfImage)
        self.currentCopyOfImage[r_ch != 255] = (255, 255, 255)
        self.currentCopyOfImage[np.logical_and.reduce([r_ch == 255, g_ch == 0])] = (0, 0, 0)
        redImageCopy = copy.deepcopy(self.currentCopyOfImage)
        blackAndWhiteImage = cv2.cvtColor(self.currentCopyOfImage, cv2.COLOR_BGR2GRAY)
        blackAndWhiteImage = 255 - blackAndWhiteImage
        blackAndWhiteImage[blackAndWhiteImage != 255] = 0
        blackAndWhiteImage = cv2.copyMakeBorder(blackAndWhiteImage, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=0)

        # Fill the black spots inside the white background of capsules with white
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(blackAndWhiteImage)
        for label in range(1, num_labels):  # Skip background (label 0)
            # Extract each component and work on it
            component_mask = np.zeros_like(blackAndWhiteImage)
            component_mask[labels == label] = 255

            component_image = cv2.bitwise_and(blackAndWhiteImage, component_mask)
            contours, _ = cv2.findContours(component_image, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)

            # Prepare a mask to track filled regions for this component
            # filled_mask = np.zeros(component_image.shape, dtype=np.uint8)

            for contour in contours:
                # if not contours:
                #     break  # No more contours to fill in this component
                # random_index = np.random.randint(0, len(contours))
                # contour = contours[random_index]
                x, y, w, h = cv2.boundingRect(contour)
                if max(w, h) > 200:
                    continue
                cv2.fillConvexPoly(blackAndWhiteImage, contour, 255)
                # cv2.fillConvexPoly(filled_mask, contour, 255)

        if whichDisk.upper() == "LEFT":
            blackAndWhiteImage = morphclose(blackAndWhiteImage, kernel_55)
        else:
            blackAndWhiteImage = morphclose(blackAndWhiteImage, kernel_33)
        blackAndWhiteImage = morphopen(blackAndWhiteImage, kernel_33)
        blackAndWhiteImage = morphclose(blackAndWhiteImage, kernel_33)
        blackAndWhiteImage[blackAndWhiteImage != 255] = 0

        he, wi = blackAndWhiteImage.shape
        bwImage = blackAndWhiteImage[10:he - 10, 10:wi - 10].copy()
        bwImage = cv2.cvtColor(bwImage, cv2.COLOR_GRAY2BGR)
        self.currentCopyOfImage = np.hstack((anImage, redImageCopy, bwImage))

        '''
        End of transformation
        '''

        if positions is None:
            positions = [POSITION.INNER, POSITION.MIDDLE, POSITION.OUTER]
        global resultsFromPictureAssessment, resultsLock, resultsStatus
        counts = []
        # print("Reached 1")
        try:
            if ImageProcessor.overallResultCoordinates is None and anImage is not None:
                # print(type(anImage.shape[1]))
                ImageProcessor.overallResultCoordinates = (int(ImageProcessor.overallResultCoordinateFractionOfDimension[0] * anImage.shape[1]),
                        int(ImageProcessor.overallResultCoordinateFractionOfDimension[1] * anImage.shape[0]))
                print(f"Overall Result will be printed at {ImageProcessor.overallResultCoordinates}")
        except Exception as e:
            printBoldRed(e)
        # print("Reached 2")
        # Removed from this testing routine to accomodate pictures from Production that already have OK, Not OK written on them
        okImage = self.checkExclusionAreaClear(anImage)
        okImage = True
        # print(f"Exclusion is {okImage}")
        ownCopyOfResults = None
        if not okImage:
            # print(f"resultsLock about to be acquired by {self.name}")
            imCopy = copy.deepcopy(anImage)
            # printBoldGreen(f"Reached here - 2 in {self.name}")
            try:
                imCopy = cv2.putText(imCopy, ImageProcessor.TEXT_TO_BE_WRITTEN_ON_IMAGES_WITH_INVALID_RESULT, ImageProcessor.overallResultCoordinates,
                                     ImageProcessor.font,
                                     2 * ImageProcessor.fontScale, ImageProcessor.redcolor,
                                     ImageProcessor.thickness,
                                     cv2.LINE_AA)
            except Exception as e1:
                printBoldRed(e1)
            acquired = resultsLock.acquire()
            if acquired:
                # print(f"resultsLock acquired by {self.name}")
                # printBoldRed(f"About to return INVALID_RESULT because of exclusion region")
                try:
                    resultsFromPictureAssessment[self.pictureResultIndex] = [INVALID_RESULT, imCopy]
                    resultsStatus[self.pictureResultIndex] = True
                    ownCopyOfResults = copy.deepcopy(resultsStatus)
                except Exception as e2:
                    printBoldRed(e2)
                resultsLock.release()
                # print(f"resultsLock released by {self.name}")
        else:
            try:
                with ThreadPoolExecutor(max_workers=3) as executor:
                    # futures = [executor.submit(self.getCapsuleCountAndAreas, anImage, position) for position in positions]
                    # wait(futures)
                    # for future in futures:
                    #     count = 0
                    #     # count, areas = future.result(timeout=1.0)
                    #     try:
                    #         # count, areas = future.result(timeout=0.6)
                    #         count, areas = future.result() # removed timeout to ensure we can see the images at leisure
                    #     except Exception as e3:
                    #         printBoldRed(e3)
                    #     counts.append(count)
                    for position in positions:
                        count = 0
                        areas = 0
                        count, areas = self.getCapsuleCountAndAreas(image=anImage, whichDisk=whichDisk, whichPosition=position)
                        counts.append(count)
                    printBoldBlue(f"         Counts are {counts}")
                    countTotal = 0
                    for aCount in counts:
                        countTotal += aCount
                    if countTotal == 180:
                        printGreen(f"OK - {countTotal}")
                    else:
                        printRed(f"Not OK - {countTotal}")
            except Exception as e:
                printBoldRed(e)
                pass
            # printBoldGreen(f"Reached here - 1 in {self.name} with counts length as {len(counts)}")
            innerOK = counts[0] == self.desiredInnerCount
            middleOK = counts[1] == self.desiredMiddleCount
            outerOK = counts[2] == self.desiredOuterCount
            self.writeCountsDataOnImage(counts[0], counts[1], counts[2])
            overallOK = innerOK and middleOK and outerOK
            # printBold(f"{overallOK = }")
            imCopy = copy.deepcopy(anImage)
            # printBoldGreen(f"Reached here - 2 in {self.name}")
            try:
                if innerOK:
                    imCopy = cv2.putText(imCopy, ok, (self.innerCoordinates[0], self.innerCoordinates[1]), ImageProcessor.font,
                                    ImageProcessor.fontScale, ImageProcessor.greencolor, ImageProcessor.thickness, cv2.LINE_AA)
                else:
                    imCopy = cv2.putText(imCopy, notok, (self.innerCoordinates[0], self.innerCoordinates[1]), ImageProcessor.font,
                                    ImageProcessor.fontScale, ImageProcessor.redcolor, ImageProcessor.thickness, cv2.LINE_AA)
                if middleOK:
                    imCopy = cv2.putText(imCopy, ok, (self.middleCoordinates[0], self.middleCoordinates[1]), ImageProcessor.font,
                                    ImageProcessor.fontScale, ImageProcessor.greencolor, ImageProcessor.thickness, cv2.LINE_AA)
                else:
                    imCopy = cv2.putText(imCopy, notok, (self.middleCoordinates[0], self.middleCoordinates[1]), ImageProcessor.font,
                                    ImageProcessor.fontScale, ImageProcessor.redcolor, ImageProcessor.thickness, cv2.LINE_AA)
                if outerOK:
                    imCopy = cv2.putText(imCopy, ok, (self.outerCoordinates[0], self.outerCoordinates[1]), ImageProcessor.font,
                                    ImageProcessor.fontScale, ImageProcessor.greencolor, ImageProcessor.thickness, cv2.LINE_AA)
                else:
                    imCopy = cv2.putText(imCopy, notok, (self.outerCoordinates[0], self.outerCoordinates[1]), ImageProcessor.font,
                                    ImageProcessor.fontScale, ImageProcessor.redcolor, ImageProcessor.thickness, cv2.LINE_AA)
                if overallOK:
                    imCopy = cv2.putText(imCopy, ok, ImageProcessor.overallResultCoordinates,
                                         ImageProcessor.font,
                                         2 * ImageProcessor.fontScale, ImageProcessor.greencolor, ImageProcessor.thickness,
                                         cv2.LINE_AA)
                else:
                    imCopy = cv2.putText(imCopy, notok, ImageProcessor.overallResultCoordinates,
                                         ImageProcessor.font,
                                         2 * ImageProcessor.fontScale, ImageProcessor.redcolor, ImageProcessor.thickness,
                                         cv2.LINE_AA)
            except:
                pass
            # print(f"resultsLock about to be acquired by {self.name}")
            acquired = resultsLock.acquire()
            if acquired:
                # print(f"resultsLock acquired by {self.name}")
                try:
                    resultsFromPictureAssessment[self.pictureResultIndex] = [ok if overallOK else notok, imCopy]
                    resultsStatus[self.pictureResultIndex] = True
                    ownCopyOfResults = copy.deepcopy(resultsStatus)
                except:
                    pass
                resultsLock.release()
            # print(f"resultsLock released by {self.name}")
        # print(f"resultsLock about to be acquired by {self.name}")
        # with resultsLock:
        # print(f"resultsLock acquired by {self.name}")
        # printLight(f"Finished if-else block in processCounts(); {ownCopyOfResults = }")
        sendStatus = True
        for individualPicsResult in ownCopyOfResults:
            sendStatus = sendStatus and individualPicsResult
        if sendStatus:
            ok_result_count = 0
            notok_result_count = 0
            invalid_result_count = 0
            for individualResult in resultsFromPictureAssessment:
                if individualResult[0] == ok:
                    ok_result_count += 1
                elif individualResult[0] == INVALID_RESULT:
                    invalid_result_count += 1
                else:
                    notok_result_count += 1
            if ok_result_count >= 2:
                finalResult = ok
            elif notok_result_count > invalid_result_count:
                finalResult = notok
            else:
                finalResult = INVALID_RESULT
            printBold(f"{finalResult = }")
            finalPicture = resultsFromPictureAssessment[0][1]  # initialisation
            if finalResult == ok:
                for individualResult in resultsFromPictureAssessment:
                    if individualResult[0] == ok:
                        finalPicture = individualResult[1]
                        break
            elif finalResult == notok:
                for individualResult in resultsFromPictureAssessment:
                    if individualResult[0] == notok:
                        finalPicture = individualResult[1]
                        break
            elif finalResult == INVALID_RESULT:
                for individualResult in resultsFromPictureAssessment:
                    if individualResult[0] == INVALID_RESULT:
                        finalPicture = individualResult[1]
                        break
            # printBold(f"{finalPicture.shape if finalPicture is not None else 'None'}")
            resultsFromPictureAssessment = [[INVALID_RESULT, None], [INVALID_RESULT, None],
                                            [INVALID_RESULT, None]]
            resultsStatus = [False, False, False]
            gap = time.time() - self.timeOfReceivingDiscInput
            printBoldBlue(f"Time to process and get result is {gap * 1000} ms")
            # printBold(f"Preparing to send data to FE and IOS")
            if (time.time() - self.timeOfReceivingDiscInput) < ImageProcessor.FASTEST_GAP_BETWEEN_TAKEPIC_AND_BOTTLERELEASE:
                try:
                    # sendImageWithResult(redisConnection=self.redisConnection, anImage=finalPicture, queueName=self.saveQueue, result=finalResult)
                    # sendImageWithResult(redisConnection=self.redisConnection, anImage=finalPicture, queueName=self.frontendQueue, result=finalResult)
                    # sendCameraResultToIOServer(redisConnection=self.redisConnection, result=finalResult, cameraId=self.cameraId, aProducer= f"Camera_{self.name}")
                    pass
                except:
                    pass
            else:
                try:
                    # sendImageWithResult(redisConnection=self.redisConnection, anImage=anImage, queueName=self.saveQueue, result=INVALID_RESULT)
                    # sendImageWithResult(redisConnection=self.redisConnection, anImage=anImage, queueName=self.frontendQueue, result=INVALID_RESULT)
                    pass
                except:
                    pass
        return overallOK
        # return None


# For left disc images
# if __name__ == '__main__':
#     inputDirectory = "C:/Temp/TrialImages/Left Disc/"
#     targetDirectory = "C:/Temp/TrialImages/Left Disc/Result/"
#     # inputDirectory = "C:/Costheta/Geltec/HerbaLife pics/Left Disc/"
#     # targetDirectory = "C:/Costheta/Geltec/HerbaLife pics/Left Disc/Result/"
#     # inputDirectory = "C:/PythonProjects/Geltec_New/Images/Left Disc Slugs/"
#     # targetDirectory = "C:/PythonProjects/Geltec_New/Images/Left Disc Slugs/Result/"
#     os.makedirs(targetDirectory, exist_ok=True)
#     imageProcessor = ImageProcessor(cameraId=1)
#     filesInDirectory = listFiles(inputDirectory)
#     print(filesInDirectory)
#     totalCount = 0
#     totalOkCount = 0
#     for file in filesInDirectory:
#         filename, file_extension = os.path.splitext(file)
#         resultfilename = f"{targetDirectory}{filename}-result{file_extension}"
#         overallOK = imageProcessor.processCounts(f"{inputDirectory}{file}", whichDisk="LEFT")
#         totalCount += 1
#         if overallOK:
#             totalOkCount += 1
#         cv2.imwrite(resultfilename, imageProcessor.currentCopyOfImage)
#     print(f"{totalCount = }; {totalOkCount = }")

#  For right disc images
# if __name__ == '__main__':
#     inputDirectory = "C:/Temp/TrialImages/Right Disc/"
#     targetDirectory = "C:/Temp/TrialImages/Right Disc/Result/"
#     # inputDirectory = "C:/PythonProjects/Geltec_New/Images/Right Disc Slugs/"
#     # targetDirectory = "C:/PythonProjects/Geltec_New/Images/Right Disc Slugs/Result/"
#
#     os.makedirs(targetDirectory, exist_ok=True)
#     imageProcessor = ImageProcessor(cameraId=2) # Right Disc
#     # imageProcessor.setCustomerAndMedicine("HERBALIFE", "OMEGA3")
#     filesInDirectory = listFiles(inputDirectory)
#     print(filesInDirectory)
#     totalCount = 0
#     totalOkCount = 0
#     for file in filesInDirectory:
#         filename, file_extension = os.path.splitext(file)
#         resultfilename = f"{targetDirectory}{filename}-result{file_extension}"
#         overallOK = imageProcessor.processCounts(f"{inputDirectory}{file}", whichDisk="RIGHT")
#         totalCount += 1
#         if overallOK:
#             totalOkCount += 1
#         cv2.imwrite(resultfilename, imageProcessor.currentCopyOfImage)
#     print(f"{totalCount = }; {totalOkCount = }")


# Routine to replace "OK" and "Not OK" in the result images
# if __name__ == '__main__':
#     inputDirectory = "C:/Temp/TrialImages/Left Disc/"
#     targetDirectory = "C:/Temp/TrialImages/Left Disc/Result/"
#     # inputDirectory = "C:/Temp/TrialImages/Right Disc/Newest Images/"
#     # targetDirectory = "C:/Temp/TrialImages/Right Disc/Result/"
#     # inputDirectory = "C:/PythonProjects/Geltec_New/Images/Right Disc Slugs/"
#     # targetDirectory = "C:/PythonProjects/Geltec_New/Images/Right Disc Slugs/Result/"
#
#     os.makedirs(targetDirectory, exist_ok=True)
#     filesInDirectory = listFiles(inputDirectory)
#     print(filesInDirectory)
#     totalCount = 0
#     totalOkCount = 0
#     for file in filesInDirectory:
#         print(f"Processing {file}")
#         filename, file_extension = os.path.splitext(file)
#         resultfilename = f"{targetDirectory}{filename}{file_extension}"
#         image = cv2.imread(f"{inputDirectory}{file}")
#         totalCount += 1
#         b_ch, g_ch, r_ch = getChannelsAsInt(image)
#         image[np.logical_and.reduce([r_ch >= 240, np.abs(r_ch - g_ch) > 50, np.abs(r_ch - b_ch) > 50])] = (255, 255, 255)
#         image[np.logical_and.reduce([g_ch >= 240, np.abs(g_ch - r_ch) > 50, np.abs(g_ch - b_ch) > 50])] = (255, 255, 255)
#         cv2.imwrite(resultfilename, image)
#     print(f"{totalCount = }")


# Testing the NEW STATS BASED METHOD ON the left disc
if __name__ == '__main__':

    inputDirectory = "C:/Temp/TrialImages/Left Disc/"
    targetDirectory = "C:/Temp/TrialImages/Left Disc/Result/"
    os.makedirs(targetDirectory, exist_ok=True)
    filesInDirectory = listFiles(inputDirectory)
    print(filesInDirectory)
    totalCount = 0
    totalOkCount = 0
    leftDiscInnerPolygonPoints = CosThetaConfigurator.getInstance().getLeftDiscInnerPolygonPoints(cust_name="HERBALIFE", med_name="OMEGA3")
    # print(leftDiscInnerPolygonPoints)
    leftDiscMiddlePolygonPoints = CosThetaConfigurator.getInstance().getLeftDiscMiddlePolygonPoints(cust_name="HERBALIFE", med_name="OMEGA3")
    # print(leftDiscMiddlePolygonPoints)
    leftDiscOuterPolygonPoints = CosThetaConfigurator.getInstance().getLeftDiscOuterPolygonPoints(cust_name="HERBALIFE", med_name="OMEGA3")
    # print(leftDiscOuterPolygonPoints)
    polygonPoints = {"Inner" : leftDiscInnerPolygonPoints, "Middle" : leftDiscMiddlePolygonPoints, "Outer" : leftDiscOuterPolygonPoints}
    showImages = False
    showMask = False
    printEachCapsuleDetail = True
    printGreens = False # Put value to false to print details of only reds
    printDroppedArea = False
    showCapsuleByCapsule = False
    okCount = 0
    notokCount = 0
    notokimages = []
    for file in filesInDirectory:
        # print(f"Processing {file}")
        filename, file_extension = os.path.splitext(file)
        resultfilename = f"{targetDirectory}{filename}-result{file_extension}"
        image = cv2.imread(f"{inputDirectory}{file}")
        if image is not None:
            if showImages:
                showImageWithRGBValues("Current image", image)
            ok = True
            for key, polygon in polygonPoints.items():
                # print(polygon)
                imageCopy = copy.deepcopy(image)
                cropped, _ = cropToRectangleAndResizeImage(imageCopy, polygon)
                croppedImage = cropImageToExactPolygonShape(cropped, polygon)
                croppedCopy = copy.deepcopy(croppedImage)
                b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
                # croppedCopy[np.logical_and.reduce([r_ch > 242, g_ch > 242, b_ch > 230])] = (255, 255, 255)
                mask = copy.deepcopy(croppedCopy)
                mask = cv2.cvtColor(mask, cv2.COLOR_BGR2GRAY)
                ret, mask1 = cv2.threshold(mask, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                # mask = cv2.cvtColor(mask, cv2.COLOR_BGR2GRAY)
                mask[mask <= (ret + 10)] = 0
                mask[mask != 0] = 255
                mask = morphopen(mask, kernel_55)
                mask = morphclose(mask, kernel_33)
                mask = morphopen(mask, kernel_55)
                mask = 255-mask
                # showImageWithRGBValues("Mask", mask)
                # cv2.waitKey(0)
                # boolean_mask = mask < 127
                # nowTime = time.time()
                # croppedImage[np.logical_and.reduce([r_ch > 245, g_ch > 245, b_ch > 240])] = (255,255,255)
                # afterTime = time.time()
                # print(f"Time taken = {(afterTime - nowTime)*1000} ms")
                # showImageWithRGBValues("Image", croppedImage)
                # cv2.waitKey(0)

                if showMask:
                    showImageWithRGBValues("Local Copy", mask)
                    cv2.waitKey(0)
                boolean_mask = mask < 127
                croppedImage[boolean_mask] = (255, 255, 255)

                '''
                num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(mask)
                croppedImageCopy = copy.deepcopy(croppedImage)
                nTablets = 0
                minRequiredArea = 84
                rawCoordinateAndAreaData = []

                for label in range(1, num_labels):  # Skip background (label 0)
                    x = stats[label, cv2.CC_STAT_LEFT]
                    y = stats[label, cv2.CC_STAT_TOP]
                    w = stats[label, cv2.CC_STAT_WIDTH]
                    h = stats[label, cv2.CC_STAT_HEIGHT]
                    area = stats[label, cv2.CC_STAT_AREA]

                    if area < minRequiredArea:
                        if printDroppedArea:
                            printBoldBlue(f"Dropped at [{x}, {y}] because area is less than {minRequiredArea}")
                        continue

                    region = croppedImage[y:y + h, x:x + w]
                    b_ch, g_ch, r_ch = getChannelsAsInt(region)

                    rg_abs = np.abs(r_ch - g_ch)
                    gb_abs = np.abs(g_ch - b_ch)
                    rb_abs = np.abs(r_ch - b_ch)

                    rg = r_ch - g_ch
                    gb = g_ch - b_ch
                    rb = r_ch - b_ch

                    # Improved Masking and Handling of Empty Regions
                    # rg_mask = rg_abs != 255  # Use 255 for uint8 images
                    # gb_mask = gb_abs != 255
                    # rb_mask = rb_abs != 255

                    rg_mask = r_ch <= 230  # Remove whitish pixels for calculation of mean and std
                    gb_mask = g_ch <= 230 # Remove whitish pixels for calculation of mean and std
                    rb_mask = b_ch <= 220 # Remove whitish pixels for calculation of mean and std

                    common_mask = np.logical_and.reduce([rg_mask, gb_mask, rb_mask])
                    # Handle potential empty arrays due to masking:
                    # if np.any(rg_mask):
                    #     avg_rg_abs = np.mean(rg_abs[rg_mask])
                    #     std_rg_abs = np.std(rg_abs[rg_mask])
                    #     avg_rg = np.mean(rg[rg_mask])
                    #     std_rg = np.std(rg[rg_mask])
                    #     r = np.mean(r_ch[rg_mask])
                    # else:
                    #     avg_rg_abs, std_rg_abs, avg_rg, std_rg, r = np.nan, np.nan, np.nan, np.nan, np.nan
                    #
                    # if np.any(gb_mask):
                    #     avg_gb_abs = np.mean(gb_abs[gb_mask])
                    #     std_gb_abs = np.std(gb_abs[gb_mask])
                    #     avg_gb = np.mean(gb[gb_mask])
                    #     std_gb = np.std(gb[gb_mask])
                    #     g = np.mean(g_ch[rg_mask])
                    # else:
                    #     avg_gb_abs, std_gb_abs, avg_gb, std_gb, g = np.nan, np.nan, np.nan, np.nan, np.nan
                    #
                    # if np.any(rb_mask):
                    #     avg_rb_abs = np.mean(rb_abs[rb_mask])
                    #     std_rb_abs = np.std(rb_abs[rb_mask])
                    #     avg_rb = np.mean(rb[rb_mask])
                    #     std_rb = np.std(rb[rb_mask])
                    #     b = np.mean(b_ch[rg_mask])
                    # else:
                    #     avg_rb_abs, std_rb_abs, avg_rb, std_rb, b = np.nan, np.nan, np.nan, np.nan, np.nan

                    if np.any(common_mask):
                        avg_rg_abs = np.mean(rg_abs[common_mask])
                        std_rg_abs = np.std(rg_abs[common_mask])
                        avg_rg = np.mean(rg[common_mask])
                        std_rg = np.std(rg[common_mask])
                        r = np.mean(r_ch[common_mask])
                    else:
                        avg_rg_abs, std_rg_abs, avg_rg, std_rg, r = np.nan, np.nan, np.nan, np.nan, np.nan

                    if np.any(common_mask):
                        avg_gb_abs = np.mean(gb_abs[common_mask])
                        std_gb_abs = np.std(gb_abs[common_mask])
                        avg_gb = np.mean(gb[common_mask])
                        std_gb = np.std(gb[common_mask])
                        g = np.mean(g_ch[common_mask])
                    else:
                        avg_gb_abs, std_gb_abs, avg_gb, std_gb, g = np.nan, np.nan, np.nan, np.nan, np.nan

                    if np.any(common_mask):
                        avg_rb_abs = np.mean(rb_abs[common_mask])
                        std_rb_abs = np.std(rb_abs[common_mask])
                        avg_rb = np.mean(rb[common_mask])
                        std_rb = np.std(rb[common_mask])
                        b = np.mean(b_ch[common_mask])
                    else:
                        avg_rb_abs, std_rb_abs, avg_rb, std_rb, b = np.nan, np.nan, np.nan, np.nan, np.nan


                    # results[label] = {
                    #     'avg_rg': avg_rg, 'std_rg': std_rg,
                    #     'avg_gb': avg_gb, 'std_gb': std_gb,
                    #     'avg_rb': avg_rb, 'std_rb': std_rb
                    # }

                    # isCapsule = True
                    # any2AbsAvgLessThan2_5 = ((avg_rg_abs < 2.5) and (avg_gb_abs < 2.5)) or ((avg_gb_abs < 2.5) and (avg_rb_abs < 2.5)) or ((avg_rg_abs < 2.5) and (avg_rb_abs < 2.5))
                    # GB_RB_AvgLessThan2_5 = (avg_gb_abs < 2.5) and (avg_rb_abs < 2.5)
                    # any2AbsStdDevLessThan2_5 = ((std_rg_abs < 2.5) and (std_gb_abs < 2.5)) or ((std_gb_abs < 2.5) and (std_rb_abs < 2.5)) or ((std_rg_abs < 2.5) and (std_rb_abs < 2.5))
                    # GB_RB_AbsStdDevLessThan2_5 = (std_gb_abs < 2.5) and (std_rb_abs < 2.5)
                    # any2AvgLessThan6 = ((avg_rg_abs < 6) and (avg_gb_abs < 6)) or ((avg_rg_abs < 6) and (avg_rb_abs < 6)) or ((avg_rb_abs < 6) and (avg_gb_abs < 6))
                    # any2StdDevLessThan6 = ((std_rg_abs < 6) and (std_gb_abs < 6)) or ((std_rg_abs < 6) and (std_rb_abs < 6)) or ((std_rb_abs < 6) and (std_gb_abs < 6))
                    # any2StdDevLessThan2_25 = ((std_rg_abs < 2.25) and (std_gb_abs < 2.25)) or ((std_rg_abs < 2.25) and (std_rb_abs < 2.25)) or ((std_rb_abs < 2.25) and (std_gb_abs < 2.25))
                    rg_div = std_rg_abs / avg_rg_abs if avg_rg_abs != 0. else std_rg_abs / 0.01
                    gb_div = std_gb_abs / avg_gb_abs if avg_gb_abs != 0 else std_gb_abs / 0.01
                    rb_div = std_rb_abs / avg_rb_abs if avg_rb_abs != 0 else std_rb_abs / 0.01
                    # try:
                    #     stdRG2AvgRG_GreaterThanAtLeast1Other = (rg_div > 1.01 * gb_div) or (rg_div > 1.01 * rb_div)
                    # except:
                    #     stdRG2AvgRG_GreaterThanAtLeast1Other = True
                    #
                    # try:
                    #     stdRB2AvgRB_Or_stdGB2AvgGB_LessThan_Point8 = (rg_div < 0.8) or (gb_div < 0.8)
                    # except:
                    #     stdRB2AvgRB_Or_stdGB2AvgGB_LessThan_Point8 = True
                    # GB_RB_AvgLessThan10 = (avg_gb < 10) and (avg_rb < 10)
                    # GB_RB_AbsAvgLessThan12 = (avg_gb_abs < 12) and (avg_rb_abs < 12)
                    # GB_RB_AbsStdDevLessThan6 = (std_gb_abs < 6) and (std_rb_abs < 6)
                    # rg_div_meets_not_capsule_criteria = False
                    # if avg_rg < 0 and rg_div < 4.5:
                    #     rg_div_meets_not_capsule_criteria = True
                    # elif rg_div < 1.2:
                    #     rg_div_meets_not_capsule_criteria = True

                    # isCapsule = False if ((avg_gb < 2) or ((avg_rg < 0.4) and (std_rg < 0.5) and (avg_gb < 7.5) and (avg_rb < 7.5)) or (any2AvgLessThan6 and any2StdDevLessThan2_5) or (any2AvgLessThan6 and any2StdDevLessThan6 and stdRG2AvgRB_GreaterThanAtLeast1Other) or ((avg_rg < 1.5) and (std_rg < 1) and (abs(std_gb / avg_gb - std_rb / avg_rb) <= 0.06) and (avg_gb < 7.5) and (avg_rb < 7.5) and ((std_gb / avg_gb < 1) or (std_rb / avg_rb < 1)))) else True
                    # isCapsule = False if ((r <= b) or (avg_gb_abs < 2) or (allAvgLessThan5_5 and allStdDevLessThan4 and avg_rg <= 0.06) or ((avg_rg_abs < 0.4) and (std_rg_abs < 0.5) and (avg_gb_abs < 7.5) and (avg_rb_abs < 7.5) and (std_rg_abs / avg_rg_abs < 1.25)) or (any2AvgLessThan6 and any2StdDevLessThan2_25) or (any2AvgLessThan6 and any2StdDevLessThan6 and stdRG2AvgRG_GreaterThanAtLeast1Other and stdRB2AvgRB_Or_stdGB2AvgGB_LessThan_Point8)) else True
                    # isCapsule = False if ((r <= b + 1) or (avg_gb_abs < 2) or (allAvgLessThan5_5 and allStdDevLessThan4 and avg_rg <= 0.06) or
                    #                       ((avg_rg_abs < 0.4) and (std_rg_abs < 0.5) and (avg_gb_abs < 7.5) and (avg_rb_abs < 7.5) and (rg_div < 1.25))) else True
                    # isCapsule = False if ((r <= b) or (avg_gb_abs < 2) or ((g - b) < 2) or (any2AbsAvgLessThan2_5 and any2AbsStdDevLessThan2_5 and (r - g) <= 0.06)) else True
                    # isCapsule = False if ((r <= b) or (avg_gb_abs < 2) or ((g - b) < 2) or (GB_RB_AvgLessThan2_5 and GB_RB_AbsStdDevLessThan2_5 and (r - g) <= 0.06)) else True
                    # isCapsule = False if ((r <= b + 5) or (avg_gb_abs < 5) or (g - b) < 4) or (GB_RB_AvgLessThan10 and GB_RB_AvgLessThan10 and (r - g) <= 3) else True
                    # isCapsule = False if ((r <= b + 5) or (avg_gb_abs < 5) or ((g - b) < 5) or GB_RB_AbsStdDevLessThan6) else True
                    # isCapsule = False if ((rg_div > 5) or
                    #                       ((r <= g + 2) and ((g <= b) or (r <= b))) or
                    #                       (avg_gb_abs <= 5) or
                    #                       (rg_div_meets_not_capsule_criteria and (avg_rb_abs <= 7.5) and (avg_gb_abs <= 7.5) and (std_rb_abs <= 6))) else True

                    isCapsule = False
                    # if (avg_gb > 15) and (avg_rb > 15) and (std_gb > 8) and (std_rb > 8):
                    if ((r <= g + 2) and ((g <= b) or (r <= b))) or ((avg_rg < 0.5) and (std_rg < 0.25) and ((std_gb < 5) or (std_rb < 5))):
                        isCapsule = False
                    elif ((avg_gb >= 12) or (avg_rb >= 12)) or ((avg_gb >= 10) and (avg_rb >= 10) and (std_gb_abs > 3.75) and (std_rb_abs > 3.75) and (rg_div > 0.3)) or ((avg_gb >= 8) and (avg_rb >= 8) and (std_gb_abs > 4) and (std_rb_abs > 4) and (rg_div > 0.3)) or ((avg_gb >= 6) and (avg_rb >= 6) and (std_gb_abs > 4.5) and (std_rb_abs > 4.5) and (rg_div > 0.4)) or ((r <= 180) and (avg_gb >= 5) and (avg_rb >= 5) and (std_gb_abs > 4.5) and (std_rb_abs > 4.5) and (rg_div > 0.4)):
                        isCapsule = True
                    else:
                        isCapsule = False
                    reason = ""
                    if not isCapsule:
                        # if (r <= b + 5):
                        #     reason = "(r <= b + 5)"
                        # elif (avg_gb_abs < 5):
                        #     reason = "(avg_gb_abs < 5)"
                        # elif ((g - b) < 5):
                        #     reason = "((g - b) < 5)"
                        # # elif (GB_RB_AvgLessThan2_5 and GB_RB_AbsStdDevLessThan2_5 and (r - g) <= 0.06):
                        #     reason = f"{GB_RB_AvgLessThan2_5 = }, {GB_RB_AbsStdDevLessThan2_5 = }, {(r - g) = }"
                        # elif (any2AbsAvgLessThan2_5 and any2AbsStdDevLessThan2_5 and (r - g) <= 0.06):
                        #     reason = f"{any2AbsAvgLessThan2_5 = }, {any2AbsStdDevLessThan2_5 = }, {(r - g) = }"
                        # elif (GB_RB_AvgLessThan10 and GB_RB_AvgLessThan10 and (r - g) <= 3):
                        #     reason = f"{GB_RB_AvgLessThan10 = }, {GB_RB_AvgLessThan10 = }, {(r - g) = }"
                        # elif GB_RB_AbsStdDevLessThan6:
                        #     reason = f"{GB_RB_AbsStdDevLessThan6 = }"
                        # if (rg_div > 5):
                        #     reason = f"{rg_div = }"
                        # elif avg_gb_abs < 5:
                        #     reason = f"{avg_gb_abs = }"
                        # elif (rg_div_meets_not_capsule_criteria and (avg_rb_abs <= 7.5) and (avg_gb_abs <= 7.5) and (std_rb_abs <= 6)):
                        #     reason = f"{rg_div_meets_not_capsule_criteria = }, {avg_rb_abs = } and {avg_gb_abs = } and {std_rb_abs = }"
                        # else:
                        #     reason = f"{(r-g) = } and ({(g -b) = } or {(r - b) = })"
                        # reason = f"{avg_gb = } and {avg_rb = } and {std_gb = } and {std_rb = }"
                        if ((r <= g + 2) and ((g <= b) or (r <= b))):
                            reason = f"{(r - g - 2) = } and ({(g <= b) = } or {(r <= b) = }))"
                        elif ((avg_rg < 0.5) and (std_rg < 0.25) and (std_gb < 5) and (std_rb < 5)):
                            reason = f"((avg_rg < 0.5) and (std_rg < 0.25) and (std_gb < 5) and (std_rb < 5))"
                        else:
                            reason = f"{avg_gb = } and {avg_rb = }"
                    if printEachCapsuleDetail:
                        if isCapsule:
                            if (r <= b):
                                printGreen(f"It's green, but (r <= b)")
                            if printGreens:
                                printGreen(f"[RGB] {r:.0f} , {g:.0f} , {b:.0f}; "
                                      f"[Avg] {avg_rg:.2f} , {avg_gb:.2f} , {avg_rb:.2f}; "
                                      f"[Std] {std_rg:.2f} , {std_gb:.2f} , {std_rb:.2f}; "
                                      f"[Avg-Abs] {avg_rg_abs:.2f} , {avg_gb_abs:.2f} , {avg_rb_abs:.2f}; "
                                      f"[Std-Abs] {std_rg_abs:.2f} , {std_gb_abs:.2f} , {std_rb_abs:.2f}; "
                                      f"[Div] {rg_div:.2f} , "
                                      f"{gb_div:.2f} , "
                                      f"{rb_div:.2f} - {'Green' if isCapsule else 'Red'}")
                        else:
                            printBoldRed(f"[RGB] {r:.0f} , {g:.0f} , {b:.0f}; "
                                  f"[Avg] {avg_rg:.2f} , {avg_gb:.2f} , {avg_rb:.2f}; "
                                  f"[Std] {std_rg:.2f} , {std_gb:.2f} , {std_rb:.2f}; "
                                  f"[Avg-Abs] {avg_rg_abs:.2f} , {avg_gb_abs:.2f} , {avg_rb_abs:.2f}; "
                                  f"[Std-Abs] {std_rg_abs:.2f} , {std_gb_abs:.2f} , {std_rb_abs:.2f}; "
                                  f"[Div] {rg_div:.2f} , "
                                  f"{gb_div:.2f} , "
                                  f"{rb_div:.2f} - {'Green' if isCapsule else 'Red'} - {reason}")

                    if showCapsuleByCapsule:
                        localCopy = copy.deepcopy(croppedImage)
                    if isCapsule:
                        cv2.rectangle(croppedImageCopy, (x, y), (x + w, y + h), (0, 255, 0), 2)
                        if showCapsuleByCapsule:
                            cv2.rectangle(localCopy, (x, y), (x + w, y + h), (0, 255, 0), 2)
                        nTablets += 1
                        xCentre = x + w // 2
                        yCentre = y + h // 2
                        centre = (xCentre, yCentre)
                        rawCoordinateAndAreaData.append([centre, area])
                    else:
                        cv2.rectangle(croppedImageCopy, (x, y), (x + w, y + h), (0, 0, 255), 2)
                        if showCapsuleByCapsule:
                            cv2.rectangle(localCopy, (x, y), (x + w, y + h), (0, 0, 255), 2)
                    if showCapsuleByCapsule:
                        showImageWithRGBValues("Local Copy", localCopy)
                        cv2.waitKey(0)

                finalCoordinateAndAreaData = []
                if nTablets == 60: # check area ratio fidelity
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
                            distance = math.sqrt((x1 - x2) * (x1 - x2) + (y1 - y2) * (y1 - y2))
                            if distance <= 70:
                            # if distance <= self.neighbouringDistance:
                                surroundingAreas.append(area2)
                        if len(surroundingAreas) > 0:
                            surroundingAreas = np.array(surroundingAreas)
                            averageSurroundingArea = np.mean(surroundingAreas)
                        else:
                            averageSurroundingArea = 0
                        if averageSurroundingArea > 0:
                            if (area1 > averageSurroundingArea * 0.40):
                                finalCoordinateAndAreaData.append(entry)

                    nTablets = len(finalCoordinateAndAreaData)

                ok = ok and (nTablets == 60)
                # if nTablets == 60:
                #     printBoldGreen(f"Number of tablets = {nTablets}")
                # else:
                #     printBoldRed(f"Number of tablets = {nTablets}")

                if showImages:
                    showImageWithRGBValues("Image", croppedImageCopy)
                    cv2.waitKey(0)
            cv2.destroyAllWindows()
            '''
                croppedImageCopy = copy.deepcopy(croppedImage)
                num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(mask)
                nTablets = 0
                rawCoordinateAndAreaData = []

                for label in range(1, num_labels):  # Skip background (label 0)
                    x = stats[label, cv2.CC_STAT_LEFT]
                    y = stats[label, cv2.CC_STAT_TOP]
                    w = stats[label, cv2.CC_STAT_WIDTH]
                    h = stats[label, cv2.CC_STAT_HEIGHT]
                    area = stats[label, cv2.CC_STAT_AREA]

                    if (area < 42) or (area > 570):
                        continue

                    region = croppedImage[y:y + h, x:x + w]
                    b_ch, g_ch, r_ch = getChannelsAsInt(region)

                    rg_abs = np.abs(r_ch - g_ch)
                    gb_abs = np.abs(g_ch - b_ch)
                    rb_abs = np.abs(r_ch - b_ch)

                    rg = r_ch - g_ch
                    gb = g_ch - b_ch
                    rb = r_ch - b_ch

                    # Improved Masking and Handling of Empty Regions
                    # rg_mask = rg_abs != 255  # Use 255 for uint8 images
                    # gb_mask = gb_abs != 255
                    # rb_mask = rb_abs != 255

                    rg_mask = np.logical_and.reduce([r_ch != 255, g_ch !=255])
                    gb_mask = np.logical_and.reduce([r_ch != 255, g_ch !=255])
                    rb_mask = np.logical_and.reduce([r_ch != 255, g_ch !=255])

                    # Handle potential empty arrays due to masking:
                    if np.any(rg_mask):
                        avg_rg_abs = np.mean(rg_abs[rg_mask])
                        std_rg_abs = np.std(rg_abs[rg_mask])
                        avg_rg = np.mean(rg[rg_mask])
                        std_rg = np.std(rg[rg_mask])
                        r = np.mean(r_ch[rg_mask])
                    else:
                        avg_rg_abs, std_rg_abs, avg_rg, std_rg, r = np.nan, np.nan, np.nan, np.nan, np.nan

                    if np.any(gb_mask):
                        avg_gb_abs = np.mean(gb_abs[gb_mask])
                        std_gb_abs = np.std(gb_abs[gb_mask])
                        avg_gb = np.mean(gb[gb_mask])
                        std_gb = np.std(gb[gb_mask])
                        g = np.mean(g_ch[rg_mask])
                    else:
                        avg_gb_abs, std_gb_abs, avg_gb, std_gb, g = np.nan, np.nan, np.nan, np.nan, np.nan

                    if np.any(rb_mask):
                        avg_rb_abs = np.mean(rb_abs[rb_mask])
                        std_rb_abs = np.std(rb_abs[rb_mask])
                        avg_rb = np.mean(rb[rb_mask])
                        std_rb = np.std(rb[rb_mask])
                        b = np.mean(b_ch[rg_mask])
                    else:
                        avg_rb_abs, std_rb_abs, avg_rb, std_rb, b = np.nan, np.nan, np.nan, np.nan, np.nan

                    isCapsule = True
                    any2AbsAvgLessThan2_5 = ((avg_rg_abs < 2.5) and (avg_gb_abs < 2.5)) or ((avg_gb_abs < 2.5) and (avg_rb_abs < 2.5)) or ((avg_rg_abs < 2.5) and (avg_rb_abs < 2.5))
                    any2AbsStdDevLessThan2_5 = ((std_rg_abs < 2.5) and (std_gb_abs < 2.5)) or ((std_gb_abs < 2.5) and (std_rb_abs < 2.5)) or ((std_rg_abs < 2.5) and (std_rb_abs < 2.5))
                    any2AvgLessThan6 = ((avg_rg_abs < 6) and (avg_gb_abs < 6)) or ((avg_rg_abs < 6) and (avg_rb_abs < 6)) or ((avg_rb_abs < 6) and (avg_gb_abs < 6))
                    any2StdDevLessThan6 = ((std_rg_abs < 6) and (std_gb_abs < 6)) or ((std_rg_abs < 6) and (std_rb_abs < 6)) or ((std_rb_abs < 6) and (std_gb_abs < 6))
                    any2StdDevLessThan2_25 = ((std_rg_abs < 2.25) and (std_gb_abs < 2.25)) or ((std_rg_abs < 2.25) and (std_rb_abs < 2.25)) or ((std_rb_abs < 2.25) and (std_gb_abs < 2.25))
                    gb_rb_AbsStdDevLessThan2 = (std_gb_abs <= 2) and (std_rb_abs <= 2) and (avg_gb < 7) and (avg_rb < 7)
                    rg_div = std_rg_abs / avg_rg_abs if avg_rg_abs != 0. else std_rg_abs / 0.01
                    gb_div = std_gb_abs / avg_gb_abs if avg_gb_abs != 0 else std_gb_abs / 0.01
                    rb_div = std_rb_abs / avg_rb_abs if avg_rb_abs != 0 else std_rb_abs / 0.01
                    bg_avg_div_rg_avg = avg_gb_abs / avg_rg_abs if avg_rg_abs != 0 else avg_gb_abs / 0.01
                    rb_avg_div_rg_avg = avg_rb_abs / avg_rg_abs if avg_rg_abs != 0 else avg_rb_abs / 0.01
                    try:
                        stdRG2AvgRG_GreaterThanAtLeast1Other = (rg_div > 1.01 * gb_div) or (rg_div > 1.01 * rb_div)
                    except:
                        stdRG2AvgRG_GreaterThanAtLeast1Other = True

                    try:
                        stdRB2AvgRB_Or_stdGB2AvgGB_LessThan_Point8 = (rg_div < 0.8) or (gb_div < 0.8)
                    except:
                        stdRB2AvgRB_Or_stdGB2AvgGB_LessThan_Point8 = True

                    # isCapsule = False if ((avg_gb < 2) or ((avg_rg < 0.4) and (std_rg < 0.5) and (avg_gb < 7.5) and (avg_rb < 7.5)) or (any2AvgLessThan6 and any2StdDevLessThan2_5) or (any2AvgLessThan6 and any2StdDevLessThan6 and stdRG2AvgRB_GreaterThanAtLeast1Other) or ((avg_rg < 1.5) and (std_rg < 1) and (abs(std_gb / avg_gb - std_rb / avg_rb) <= 0.06) and (avg_gb < 7.5) and (avg_rb < 7.5) and ((std_gb / avg_gb < 1) or (std_rb / avg_rb < 1)))) else True
                    # isCapsule = False if ((r <= b) or (avg_gb_abs < 2) or (allAvgLessThan5_5 and allStdDevLessThan4 and avg_rg <= 0.06) or ((avg_rg_abs < 0.4) and (std_rg_abs < 0.5) and (avg_gb_abs < 7.5) and (avg_rb_abs < 7.5) and (std_rg_abs / avg_rg_abs < 1.25)) or (any2AvgLessThan6 and any2StdDevLessThan2_25) or (any2AvgLessThan6 and any2StdDevLessThan6 and stdRG2AvgRG_GreaterThanAtLeast1Other and stdRB2AvgRB_Or_stdGB2AvgGB_LessThan_Point8)) else True
                    # isCapsule = False if ((r <= b + 1) or (avg_gb_abs < 2) or (allAvgLessThan5_5 and allStdDevLessThan4 and avg_rg <= 0.06) or
                    #                       ((avg_rg_abs < 0.4) and (std_rg_abs < 0.5) and (avg_gb_abs < 7.5) and (avg_rb_abs < 7.5) and (rg_div < 1.25))) else True
                    # isCapsule = False if ((r <= b + 1) or (avg_gb_abs < 2) or ((g - b) < 2) or (any2AbsAvgLessThan2_5 and any2AbsStdDevLessThan2_5 and (r - g) <= 0.06) or (bg_avg_div_rg_avg > 15 and rb_avg_div_rg_avg > 15)) else True
                    isCapsule = False if ((r <= b + 1) or
                                          (avg_gb_abs < 3) or
                                          (avg_rb_abs < 3.25) or
                                          ((g - b) < 2) or
                                          (any2AbsAvgLessThan2_5 and any2AbsStdDevLessThan2_5 and (r - g) <= 0.06) or
                                          (gb_rb_AbsStdDevLessThan2)) else True
                    if printEachCapsuleDetail:
                        if isCapsule:
                            if printGreens:
                                printBoldGreen(f"[RGB] {r:.0f} , {g:.0f} , {b:.0f}; "
                                      f"[Avg] {avg_rg:.2f} , {avg_gb:.2f} , {avg_rb:.2f}; "
                                      f"[Std] {std_rg:.2f} , {std_gb:.2f} , {std_rb:.2f}; "
                                      f"[Avg-Abs] {avg_rg_abs:.2f} , {avg_gb_abs:.2f} , {avg_rb_abs:.2f}; "
                                      f"[Std-Abs] {std_rg_abs:.2f} , {std_gb_abs:.2f} , {std_rb_abs:.2f}; "
                                      f"[Div] {(std_rg_abs / avg_rg_abs if avg_rg_abs != 0 else 0):.2f} , "
                                      f"{(std_gb_abs / avg_gb_abs if avg_gb_abs != 0 else 0):.2f} , "
                                      f"{(std_rb_abs / avg_rb_abs if avg_rb_abs != 0 else 0):.2f}; "
                                      f"{bg_avg_div_rg_avg:.2f}, {rb_avg_div_rg_avg:.2f} - {'Green' if isCapsule else 'Red'}")
                        else:
                            printBoldRed(f"[RGB] {r:.0f} , {g:.0f} , {b:.0f}; "
                                           f"[Avg] {avg_rg:.2f} , {avg_gb:.2f} , {avg_rb:.2f}; "
                                           f"[Std] {std_rg:.2f} , {std_gb:.2f} , {std_rb:.2f}; "
                                           f"[Avg-Abs] {avg_rg_abs:.2f} , {avg_gb_abs:.2f} , {avg_rb_abs:.2f}; "
                                           f"[Std-Abs] {std_rg_abs:.2f} , {std_gb_abs:.2f} , {std_rb_abs:.2f}; "
                                           f"[Div] {(std_rg_abs / avg_rg_abs if avg_rg_abs != 0 else 0):.2f} , "
                                           f"{(std_gb_abs / avg_gb_abs if avg_gb_abs != 0 else 0):.2f} , "
                                           f"{(std_rb_abs / avg_rb_abs if avg_rb_abs != 0 else 0):.2f}; "
                                           f"{bg_avg_div_rg_avg:.2f}, {rb_avg_div_rg_avg:.2f} - {'Green' if isCapsule else 'Red'}")
                    if isCapsule:
                        nTablets += 1
                        xCentre = x + w // 2
                        yCentre = y + h // 2
                        centre = (xCentre, yCentre)
                        rectangle = [(x, y), (x + w, y + h)]
                        rawCoordinateAndAreaData.append([centre, area, rectangle])

                finalCoordinateAndAreaData = []
                typicalDistanceBetweenCapsules = 55
                minRequiredRatio = 0.55
                if nTablets == 60:
                    printGreen(f"Initial number of tablets = {nTablets}")
                else:
                    printRed(f"Initial number of tablets = {nTablets}")
                if nTablets >= 60: # check area ratio fidelity
                    for mainIndex, entry in enumerate(rawCoordinateAndAreaData):
                        surroundingAreas = []
                        x1 = entry[0][0]
                        y1 = entry[0][1]
                        area1 = entry[1]
                        rectangle1 = entry[2]
                        for subIndex, aData in enumerate(rawCoordinateAndAreaData):
                            if mainIndex == subIndex:
                                continue
                            x2 = aData[0][0]
                            y2 = aData[0][1]
                            area2 = aData[1]
                            distance = math.sqrt((x1 - x2) * (x1 - x2) + (y1 - y2) * (y1 - y2))
                            if distance <= typicalDistanceBetweenCapsules:
                            # if distance <= self.neighbouringDistance:
                                surroundingAreas.append(area2)
                        showGreen = True
                        if len(surroundingAreas) > 0:
                            surroundingAreas = np.array(surroundingAreas)
                            averageSurroundingArea = np.mean(surroundingAreas)
                        else:
                            averageSurroundingArea = 0
                        if averageSurroundingArea > 0: # if there's no surrounding area, it's a lone tablet...therefore, drop it
                            if (area1 > averageSurroundingArea * minRequiredRatio):
                                finalCoordinateAndAreaData.append(entry)
                                showGreen = True
                            else:
                                showGreen = False
                                try:
                                    printRed(f"Dropped a capsule because of surrounding area constraint - {(area1 / averageSurroundingArea):.3f}")
                                except:
                                    pass

                        else:
                            showGreen = False
                            printRed(f"Dropped a capsule because it's a loner capsule")
                        if showCapsuleByCapsule:
                            localCopy = copy.deepcopy(croppedImage)
                            if showGreen:
                                cv2.rectangle(croppedImageCopy, rectangle1[0], rectangle1[1], (0, 255, 0), 2)
                                cv2.rectangle(localCopy, rectangle1[0], rectangle1[1], (0, 255, 0), 2)
                            else:
                                cv2.rectangle(croppedImageCopy, rectangle1[0], rectangle1[1], (0, 0, 255), 2)
                                cv2.rectangle(localCopy, rectangle1[0], rectangle1[1], (0, 0, 255), 2)
                            showImageWithRGBValues("Local Copy", localCopy)
                            cv2.waitKey(0)

                    nTablets = len(finalCoordinateAndAreaData)

                if showImages:
                    showImageWithRGBValues("Image", croppedImageCopy)
                    cv2.waitKey(0)
                if (nTablets == 60):
                    printGreen(f"{file} : Number of tablets = {nTablets}")
                else:
                    printRed(f"{file} : Number of tablets = {nTablets}")
                cv2.destroyAllWindows()
                ok = ok and (nTablets == 60)
            if ok:
                okCount += 1
                printBoldGreen(f"{file} IS OK !!")
            else:
                notokCount += 1
                notokimages.append(file)

            # overallOK = imageProcessor.processCounts(f"{inputDirectory}{file}", whichDisk="LEFT")
            # totalCount += 1
            # if overallOK:
            #     totalOkCount += 1
            # cv2.imwrite(resultfilename, imageProcessor.currentCopyOfImage)
    print(f"{okCount = }; {notokCount = }")
    print(f"Not Ok images are {notokimages}")

# Testing the TRADITIONAL RGB BASED METHOD ON THE LEFT DISC
# if __name__ == '__main__':
#
#     inputDirectory = "C:/Temp/TrialImages/Left Disc/"
#     targetDirectory = "C:/Temp/TrialImages/Left Disc/Result/"
#     os.makedirs(targetDirectory, exist_ok=True)
#     filesInDirectory = listFiles(inputDirectory)
#     print(filesInDirectory)
#     totalCount = 0
#     totalOkCount = 0
#     leftDiscInnerPolygonPoints = CosThetaConfigurator.getInstance().getLeftDiscInnerPolygonPoints(cust_name="HERBALIFE", med_name="OMEGA3")
#     # print(leftDiscInnerPolygonPoints)
#     leftDiscMiddlePolygonPoints = CosThetaConfigurator.getInstance().getLeftDiscMiddlePolygonPoints(cust_name="HERBALIFE", med_name="OMEGA3")
#     # print(leftDiscMiddlePolygonPoints)
#     leftDiscOuterPolygonPoints = CosThetaConfigurator.getInstance().getLeftDiscOuterPolygonPoints(cust_name="HERBALIFE", med_name="OMEGA3")
#     # print(leftDiscOuterPolygonPoints)
#     polygonPoints = {"Inner" : leftDiscInnerPolygonPoints, "Middle" : leftDiscMiddlePolygonPoints, "Outer" : leftDiscOuterPolygonPoints}
#     showImages = False
#     okCount = 0
#     notokCount = 0
#     for file in filesInDirectory:
#         # print(f"Processing {file}")
#         filename, file_extension = os.path.splitext(file)
#         resultfilename = f"{targetDirectory}{filename}-result{file_extension}"
#         image = cv2.imread(f"{inputDirectory}{file}")
#         if image is not None:
#             if showImages:
#                 showImageWithRGBValues(window_name="Current image", image=image)
#             ok = True
#             for key, polygon in polygonPoints.items():
#                 # print(polygon)
#                 imageCopy = copy.deepcopy(image)
#                 cropped, _ = cropToRectangleAndResizeImage(imageCopy, polygon)
#                 croppedImage = cropImageToExactPolygonShape(cropped, polygon)
#
#                 mask = copy.deepcopy(croppedImage)
#                 mask = cv2.cvtColor(mask, cv2.COLOR_BGR2GRAY)
#                 ret, mask1 = cv2.threshold(mask, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
#                 mask[mask <= (ret + 5)] = 0
#                 mask[mask != 0] = 255
#                 mask = morphopen(mask, kernel_55)
#                 mask = morphclose(mask, kernel_33)
#                 boolean_mask = mask > 127
#                 croppedImage[boolean_mask] = (255, 255, 255)
#                 croppedCopy = copy.deepcopy(croppedImage)
#
#                 b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
#                 croppedImage[np.logical_and.reduce([r_ch - g_ch <= -4])] = (255, 255, 255)
#                 croppedImage[np.logical_and.reduce([b_ch >= 245])] = (255, 255, 255)
#
#                 b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
#                 croppedImage[np.logical_and.reduce([r_ch - g_ch >= -3, g_ch - b_ch >= 14, g_ch < 240, r_ch < 182])] = (0, 0, 255)
#                 croppedImage[np.logical_and.reduce([r_ch - g_ch >= -3, g_ch - b_ch >= 16, g_ch < 240, r_ch >= 182])] = (0, 0, 255)
#                 croppedImage[np.logical_and.reduce([r_ch - g_ch >= -2, g_ch - b_ch >= 15, g_ch < 240, r_ch < 182])] = (0, 0, 255)
#                 croppedImage[np.logical_and.reduce([r_ch - g_ch >= -2, g_ch - b_ch >= 17, g_ch < 240, r_ch >= 182])] = (0, 0, 255)
#                 croppedImage[np.logical_and.reduce([r_ch - g_ch >= -1, g_ch - b_ch >= 16, g_ch < 240, r_ch < 182])] = (0, 0, 255)
#                 croppedImage[np.logical_and.reduce([r_ch - g_ch >= -1, g_ch - b_ch >= 18, g_ch < 240, r_ch >= 182])] = (0, 0, 255)
#                 croppedImage[np.logical_and.reduce([r_ch - g_ch >= -1, g_ch - b_ch >= 9, r_ch >= 235])] = (0, 0, 255)
#                 b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
#
#                 croppedImage[np.logical_and.reduce([r_ch - g_ch >= 0, g_ch - b_ch >= 17, g_ch < 240, r_ch < 182])] = (0, 0, 255)
#                 croppedImage[np.logical_and.reduce([r_ch - g_ch >= 0, g_ch - b_ch >= 19, g_ch < 240, r_ch >= 182])] = (0, 0, 255)
#                 croppedImage[np.logical_and.reduce([r_ch - g_ch >= 1, g_ch - b_ch >= 12, g_ch < 240])] = (0, 0, 255)
#                 croppedImage[np.logical_and.reduce([r_ch - g_ch >= 2, g_ch - b_ch >= 13, g_ch < 240])] = (0, 0, 255)
#                 b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
#
#                 croppedImage[np.logical_and.reduce([r_ch - g_ch >= 3, g_ch - b_ch >= 13, g_ch < 240])] = (0, 0, 255)
#                 croppedImage[np.logical_and.reduce([r_ch - g_ch >= 4, g_ch - b_ch >= 14, g_ch < 240])] = (0, 0, 255)
#                 croppedImage[np.logical_and.reduce([r_ch - g_ch >= 5, g_ch - b_ch >= 14, g_ch < 240])] = (0, 0, 255)
#                 croppedImage[np.logical_and.reduce([r_ch - g_ch >= 6, g_ch - b_ch >= 15, g_ch < 240])] = (0, 0, 255)
#
#                 b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
#                 croppedImage[np.logical_and.reduce([r_ch < 255, b_ch != 0])] = (255, 255, 255)
#
#                 b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
#                 croppedImage[r_ch != 255] = (255, 255, 255)
#                 croppedImage[np.logical_and.reduce([r_ch == 255, g_ch == 0])] = (0, 0, 0)
#                 redImageCopy = copy.deepcopy(croppedCopy)
#                 blackAndWhiteImage = cv2.cvtColor(croppedImage, cv2.COLOR_BGR2GRAY)
#                 blackAndWhiteImage = 255 - blackAndWhiteImage
#                 blackAndWhiteImage[blackAndWhiteImage != 255] = 0
#                 border : int = 10
#                 blackAndWhiteImage = cv2.copyMakeBorder(blackAndWhiteImage, border, border, border, border, cv2.BORDER_CONSTANT,
#                                                         value=0)
#
#                 if showImages:
#                     he, wi = blackAndWhiteImage.shape
#                     blackAndWhiteOriginalCopy = copy.deepcopy(blackAndWhiteImage)
#                     blackAndWhiteOriginalCopy = blackAndWhiteOriginalCopy[border:he - border, border:wi - border].copy()
#                     blackAndWhiteOriginalCopy = cv2.cvtColor(blackAndWhiteOriginalCopy, cv2.COLOR_GRAY2BGR)
#
#                     # combined_image = np.hstack((croppedCopy, croppedImage))
#                     # showImageWithRGBValues(f"Capsules{combined_image}", combined_image)
#                     # showImageWithRGBValues(f"Capsules - B&W image", blackAndWhiteImage)
#                     # cv2.waitKey(0)
#
#                 # Fill the black spots inside the white background of capsules with white
#                 blackAndWhiteImage = morphclose(blackAndWhiteImage, kernel_55_elliptical)
#                 # blackAndWhiteImage = morphopen(blackAndWhiteImage, kernel_33)
#                 num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(blackAndWhiteImage)
#                 component_mask = np.zeros_like(blackAndWhiteImage)
#                 for label in range(1, num_labels):  # Skip background (label 0)
#
#                     # x = stats[label, cv2.CC_STAT_LEFT]
#                     # y = stats[label, cv2.CC_STAT_TOP]
#                     w = stats[label, cv2.CC_STAT_WIDTH]
#                     h = stats[label, cv2.CC_STAT_HEIGHT]
#                     area = stats[label, cv2.CC_STAT_AREA]
#                     if area <= 9 or (w == 1) or (h == 1): # drop areas of 3x3 and 4x2 and very thin slices
#                         continue
#
#                     # Extract each component and work on it
#                     local_mask = np.zeros_like(blackAndWhiteImage)
#                     local_mask[labels == label] = 255
#
#                     # component_image = cv2.bitwise_and(blackAndWhiteImage, component_mask)
#                     # contours, _ = cv2.findContours(component_image, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
#
#                     contours, _ = cv2.findContours(local_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
#                     for contour in contours:
#                         x, y, w, h = cv2.boundingRect(contour)
#                         if max(w, h) > 150:
#                             continue
#                         cv2.fillConvexPoly(component_mask, contour, 255)
#
#                 # blackAndWhiteImage = morphopen(blackAndWhiteImage, kernel_33)
#                 # blackAndWhiteImage = morphclose(blackAndWhiteImage, kernel_55)
#                 # blackAndWhiteImage = morphclose(component_mask, kernel_55)
#                 # blackAndWhiteImage = morphopen(blackAndWhiteImage, kernel_33)
#                 # blackAndWhiteImage = morphclose(blackAndWhiteImage, kernel_33)
#                 blackAndWhiteImage = copy.deepcopy(component_mask)
#                 blackAndWhiteImage[blackAndWhiteImage != 255] = 0
#                 blackAndWhiteImage = morphclose(component_mask, kernel_55_elliptical)
#
#                 contours, hierarchy = cv2.findContours(blackAndWhiteImage, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
#                 # printBoldGreen(f"Found {len(contours)} contours in the image")
#                 nTablets = 0
#                 rawCoordinateAndAreaData = []
#                 currentMinArea = 100000
#                 currentMaxArea = -100000
#                 maxArea = 750
#                 minArea = 45
#                 for contour in contours:
#                     area = cv2.contourArea(contour)
#                     currentMaxArea = max(currentMaxArea, area)
#                     currentMinArea = min(currentMinArea, area)
#                     # eliminate the ones that are larger than max area. That leaves out the min area issue
#                     if area > maxArea:
#                         # if not PRINT_ONLY_MIN_MAX_AREAS:
#                         #     printBoldRed(f"Eliminating a capsule from {whichSet} because {area = } > {maxArea = }")
#                         # printBoldRed(f"Eliminating a capsule because {area = } > {maxArea = }")
#                         continue
#                     x, y, w, h = cv2.boundingRect(contour)
#                     xCentre = x + w // 2
#                     yCentre = y + h // 2
#                     centre = (xCentre, yCentre)
#                     rawCoordinateAndAreaData.append([centre, area])
#                     if area < minArea:
#                         # printBoldRed(f"Eliminating a capsule because {area = } < {minArea = }")
#                         continue
#                     nTablets += 1
#                     # print(f"Tablet count is {nTablets}")
#
#                 # print(
#                 #     f"{len(rawCoordinateAndAreaData)} values of Raw Coordinate and Area Data are {rawCoordinateAndAreaData}")
#                 finalCoordinateAndAreaData = []
#                 if nTablets == 60: # check area ration fidelity
#                     for mainIndex, entry in enumerate(rawCoordinateAndAreaData):
#                         surroundingAreas = []
#                         x1 = entry[0][0]
#                         y1 = entry[0][1]
#                         area1 = entry[1]
#                         for subIndex, aData in enumerate(rawCoordinateAndAreaData):
#                             if mainIndex == subIndex:
#                                 continue
#                             x2 = aData[0][0]
#                             y2 = aData[0][1]
#                             area2 = aData[1]
#                             distance = math.sqrt((x1 - x2) * (x1 - x2) + (y1 - y2) * (y1 - y2))
#                             if distance <= 70:
#                             # if distance <= self.neighbouringDistance:
#                                 surroundingAreas.append(area2)
#                         if len(surroundingAreas) > 0:
#                             surroundingAreas = np.array(surroundingAreas)
#                             averageSurroundingArea = np.mean(surroundingAreas)
#                         else:
#                             averageSurroundingArea = 0
#                         if averageSurroundingArea > 0:
#                             if (area1 > averageSurroundingArea * 0.25):
#                                 finalCoordinateAndAreaData.append(entry)
#                             else:
#                                 printBoldRed(f"Dropping an area because ratio < 0.25")
#
#                     nTablets = len(finalCoordinateAndAreaData)
#
#                 ok = ok and (nTablets == 60)
#
#                 if (nTablets == 60):
#                     printBoldGreen(f"{file} : {nTablets = }, {currentMinArea = }, {currentMaxArea = }")
#                 else:
#                     printBoldRed(f"{file} : {nTablets = }, {currentMinArea = }, {currentMaxArea = }")
#
#                 if showImages:
#                     he, wi = blackAndWhiteImage.shape
#                     bwImage = blackAndWhiteImage[border:he - border, border:wi - border].copy()
#                     bwImage = cv2.cvtColor(bwImage, cv2.COLOR_GRAY2BGR)
#                     combined_image = np.hstack((croppedCopy, blackAndWhiteOriginalCopy, bwImage))
#                     showImageWithRGBValues(f"Capsules", combined_image, numberOfStitchedImagesInXDirection=3, numberOfStitchedImagesInYDirection=1)
#                     cv2.waitKey(0)
#
#                 # if nTablets == 60:
#                 #     printBoldGreen(f"Number of tablets = {nTablets}")
#                 # else:
#                 #     printBoldRed(f"Number of tablets = {nTablets}")
#
#             cv2.destroyAllWindows()
#             if ok:
#                 okCount += 1
#             else:
#                 notokCount += 1
#
#             # overallOK = imageProcessor.processCounts(f"{inputDirectory}{file}", whichDisk="LEFT")
#             # totalCount += 1
#             # if overallOK:
#             #     totalOkCount += 1
#             # cv2.imwrite(resultfilename, imageProcessor.currentCopyOfImage)
#     print(f"{okCount = }; {notokCount = }")
