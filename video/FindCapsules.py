import copy
import csv

from ExtractFramesFromVideo import *
import time
from ProcessFrames import *
# import pandas as pd
from sklearn import *
from sklearn.cluster import *
from itertools import combinations
from concurrent.futures import ThreadPoolExecutor, wait

rightDiscExclusionPolygon = createPolygon([[7, 343], [104, 278], [76, 224], [241, 126], [241, 126],
                                           [307, 4], [402, 7], [387, 42], [227, 85], [8, 127]])
rightDiscOuterPolygon = createPolygon([[3, 341], [93, 280], [214, 418], [380, 521], [613, 548],
                                       [731, 575], [726, 596], [682, 608], [669, 611], [655, 613],
                                       [633, 631], [394, 636], [181,557], [59,455], [6,394]])

def countCapsulesRightDisc(image, somePolygonPoints):
    imageCopy = copy.deepcopy(image)
    cropped, _ = cropToRectangleAndResizeImage(imageCopy, somePolygonPoints)
    croppedImage = cropImageToExactPolygonShape(cropped, somePolygonPoints)
    b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
    croppedImage[r_ch - g_ch > 25] = (0, 0, 255)
    showImage(croppedImage, wait = True)
    b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
    croppedImage[np.logical_and.reduce([r_ch > 120, g_ch > 10, b_ch > 10])] = (255, 255, 255)
    croppedImage[np.logical_and.reduce([r_ch > 100, r_ch - g_ch < 7, r_ch < 250])] = (255, 255, 255)
    croppedImage[np.logical_and.reduce([r_ch - g_ch < 2, g_ch - b_ch < 5])] = (255, 255, 255)
    croppedImage[np.logical_and.reduce([r_ch <= g_ch, r_ch > 10])] = (255, 255, 255)
    showImage(croppedImage, wait = True)
    b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
    croppedImage[np.logical_and.reduce([r_ch < 60, np.logical_or.reduce([g_ch < 18, b_ch < 10])])] = (255, 255, 255)
    showImage(croppedImage, wait = True)
    b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
    rgDiff = r_ch - g_ch
    gbDiff = g_ch - b_ch
    croppedImage[np.logical_and.reduce([rgDiff >= 10, r_ch >= 30])] = (0,0,255)
    # croppedImage[np.logical_and.reduce([r_ch - g_ch > 10, r_ch <= 40, g_ch > b_ch - 2])] = (0, 0, 255)
    croppedImage[np.logical_and.reduce([rgDiff >= 8, gbDiff >= 6, r_ch >= 56])] = (0, 0, 255)
    # croppedImage[np.logical_and.reduce([rgDiff >= -4, gbDiff >= 6, r_ch >= 64])] = (0, 0, 255)
    croppedImage[np.logical_and.reduce([rgDiff >= 9, gbDiff >= 7, r_ch < 40])] = (0, 0, 255)
    # croppedImage[np.logical_and.reduce([rgDiff >= 8, gbDiff >= 7, r_ch > 40, r_ch < 80])] = (0, 0, 255)
    showImage(croppedImage, wait = True)
    b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
    croppedImage[np.logical_and.reduce([rgDiff >= 4, gbDiff >= 4, r_ch < 90])] = (0, 0, 255)
    croppedImage[r_ch != 255] = (255, 255, 255)
    showImage(croppedImage, wait = True)
    blackAndWhiteImage = cv2.cvtColor(croppedImage, cv2.COLOR_BGR2GRAY)
    blackAndWhiteImage = 255 - blackAndWhiteImage
    blackAndWhiteImage = morphclose(blackAndWhiteImage, kernel_55)
    blackAndWhiteImage = morphopen(blackAndWhiteImage, kernel_33)
    blackAndWhiteImage = morphclose(blackAndWhiteImage, kernel_33)
    # writeImageWithRollingCounter(blackAndWhiteImage, "CapsulesAsBlack")

    '''
    shortlistedContours = []
    contours, hierarchy = cv2.findContours(blackAndWhiteImage, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    upperLimitArea = 0
    # lowerLimitArea = 3.5
    lowerLimitArea = 1000000
    highestHeight2WidthRatio = 0
    lowestHeight2WidthRatio = 100
    xValues = []
    yValues = []
    nTablets = 0
    for contour in contours:
        area = cv2.contourArea(contour)
        if id == 1:
            if area < RIGHT_DISC_OUTER_MIN_AREA:
                continue
            if area > RIGHT_DISC_OUTER_MAX_AREA:
                continue
        if id == 2:
            if area < RIGHT_DISC_MIDDLE_MIN_AREA:
                continue
            if area > RIGHT_DISC_MIDDLE_MAX_AREA:
                continue
        if id == 3:
            if area < RIGHT_DISC_INNER_MIN_AREA:
                continue
            if area > RIGHT_DISC_INNER_MAX_AREA:
                continue
        upperLimitArea = max(area, upperLimitArea)
        lowerLimitArea = min(area, lowerLimitArea)
        # print(area)
        # if (area > lowerLimitArea) and (area < upperLimitArea):
        #     shortlistedContours.append(contour)
        x, y, w, h = cv2.boundingRect(contour)
        h2wRatio = h*1.0 / w
        highestHeight2WidthRatio = max(highestHeight2WidthRatio, h2wRatio)
        lowestHeight2WidthRatio = min(lowestHeight2WidthRatio, h2wRatio)
        xValues.append(x + w / 2)
        yValues.append(y + h / 2)
        nTablets += 1
        # rectangles.append((rect))
        # print(area, end=", ")
        # else:
        #     # print(f"Dropped a contour with area {area}")
        #     pass
    '''


# polygonPointsRightDisc = createPolygon([[12, 317], [96, 401], [143, 465], [312, 557], [580, 604],
#                                         [624, 602], [836, 527], [899, 488], [753, 118], [549, 60],
#                                         [431, 7], [308, 9], [145, 104], [9, 204]])
#
# polygonPointsLeftDisc = createPolygon([[878, 27], [582, 56], [385, 260], [462, 363], [471, 508],
#                                         [679, 572], [882, 593], [950, 490], [955, 442], [954, 407],
#                                         [976, 380]])

polygonPoints = createPolygon([[12, 317], [96, 401], [143,465], [312, 557], [580, 604], [624, 602], [836, 527], [899, 488], [753, 118], [549, 60], [431, 7], [308, 9], [145, 104], [9, 204]])

bottomPolygonPoints = createPolygon([[114, 452], [313, 569], [565,617], [578, 606], [655, 614], [763, 570], [849, 521], [890, 498], [854, 413], [800, 453],
                                     [552, 485], [368, 428], [175, 307], [101, 404]])

middlePolygonPoints = createPolygon([[145, 264], [350, 415], [512, 474], [710, 484], [855, 422], [880, 400], [838, 291], [739, 332], [594, 331], [418, 281],
                                     [246, 154]])

topPolygonPoints = createPolygon([[246, 91], [547, 308], [742, 327], [830, 287], [774, 131], [632, 126], [422, 5]])

polygons = [bottomPolygonPoints, middlePolygonPoints, topPolygonPoints]

def checkExclusionAreaClear(image: np.ndarray, exclusionAreaPolygonPoints : Any):
    workingImage = copy.deepcopy(image)
    workingImage = reduceSize(workingImage, reductionfactor=2.0)
    exclusionPolygonPoints = copy.deepcopy(exclusionAreaPolygonPoints)
    exclusionPolygonPoints = (exclusionPolygonPoints / 2.0).astype(np.int32)
    cropped, _ = cropToRectangleAndResizeImage(workingImage, exclusionPolygonPoints)
    croppedImage = cropImageToExactPolygonShape(cropped, exclusionPolygonPoints)
    croppedImage = cv2.cvtColor(croppedImage, cv2.COLOR_BGR2GRAY)
    _, croppedImage = cv2.threshold(croppedImage,164,255, cv2.THRESH_BINARY_INV)
    croppedImage = dilate(croppedImage, kernel_33)
    _, croppedImage = cv2.threshold(croppedImage,192,255, cv2.THRESH_BINARY)
    # showImage(croppedImage, wait=True)
    contours, hierarchy = cv2.findContours(croppedImage, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    # print(len(contours))
    # showImage(croppedImage, wait=True)
    if len(contours) > 0:
        return False
    return True

def makeWhitePartsTransparent(image):
    h, w, c = image.shape
    # append Alpha channel -- required for BGRA (Blue, Green, Red, Alpha)
    image_bgra = np.concatenate([image, np.full((h, w, 1), 255, dtype=np.uint8)], axis=-1)
    # create a mask where white pixels ([255, 255, 255]) are True
    white = np.all(image == [255, 255, 255], axis=-1)
    # change the values of Alpha to 0 for all the white pixels
    image_bgra[white, -1] = 0
    # save the startingImage
    # cv2.imwrite('image_bgra.png', image_bgra)
    return image_bgra

def overlayTransparentImageOnAnother(background, foreground, x_offset=None, y_offset=None):
    bg_h, bg_w, bg_channels = background.shape
    fg_h, fg_w, fg_channels = foreground.shape

    assert bg_channels == 3, f'background image should have exactly 3 channels (RGB). found:{bg_channels}'
    assert fg_channels == 4, f'foreground image should have exactly 4 channels (RGBA). found:{fg_channels}'

    # center by default
    if x_offset is None: x_offset = (bg_w - fg_w) // 2
    if y_offset is None: y_offset = (bg_h - fg_h) // 2

    w = min(fg_w, bg_w, fg_w + x_offset, bg_w - x_offset)
    h = min(fg_h, bg_h, fg_h + y_offset, bg_h - y_offset)

    if w < 1 or h < 1: return

    # clip foreground and background images to the overlapping regions
    bg_x = max(0, x_offset)
    bg_y = max(0, y_offset)
    fg_x = max(0, x_offset * -1)
    fg_y = max(0, y_offset * -1)
    foreground = foreground[fg_y:fg_y + h, fg_x:fg_x + w]
    background_subsection = background[bg_y:bg_y + h, bg_x:bg_x + w]

    # separate alpha and color channels from the foreground startingImage
    foreground_colors = foreground[:, :, :3]
    alpha_channel = foreground[:, :, 3] / 255  # 0-255 => 0.0-1.0

    # construct an alpha_mask that matches the startingImage shape
    alpha_mask = np.dstack((alpha_channel, alpha_channel, alpha_channel))

    # combine the background with the overlay startingImage weighted by alpha
    composite = background_subsection * (1 - alpha_mask) + foreground_colors * alpha_mask

    # overwrite the section of the background startingImage that has been updated
    background[bg_y:bg_y + h, bg_x:bg_x + w] = composite

def showImage(image, title='img', wait : bool = False):
    cv2.imshow(title, image)
    if wait:
        cv2.waitKey(0)

TYPICAL_DISTANCE_BETWEEN_CAPSULES = 60
SHOW_POLYGONS = False
MIN_CUT_OFF_AREA_1 = 10
MAX_CUT_OFF_AREA_1 = 285
MIN_CUT_OFF_AREA_2 = 10
MAX_CUT_OFF_AREA_2 = 275
MIN_CUT_OFF_AREA_3 = 10
MAX_CUT_OFF_AREA_3 = 250
def countTablets(image, somePolygonPoints):
    if somePolygonPoints is polygons[0]:
        id = 1
    elif somePolygonPoints is polygons[1]:
        id = 2
    else:
        id = 3
    imageCopy = copy.deepcopy(image)
    cropped, _ = cropToRectangleAndResizeImage(imageCopy, somePolygonPoints, wait=False)
    croppedImage = cropImageToExactPolygonShape(cropped, somePolygonPoints)
    b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
    croppedImage[np.logical_and.reduce([r_ch > 90, r_ch - g_ch < 7, r_ch < 250])] = (255, 255, 255)
    croppedImage[r_ch > 180] = (255, 255, 255)
    b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
    croppedImage[np.logical_or.reduce([g_ch < 18, b_ch < 10])] = (255, 255, 255)
    b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
    rgDiff = r_ch - g_ch
    gbDiff = g_ch - b_ch
    croppedImage[np.logical_and.reduce([rgDiff >= 12, r_ch >= 40])] = (0,0,255)
    croppedImage[np.logical_and.reduce([rgDiff >= 8, gbDiff >= 6, r_ch >= 56])] = (0, 0, 255)
    croppedImage[np.logical_and.reduce([rgDiff >= 9, gbDiff >= 7, r_ch < 40])] = (0, 0, 255)
    b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
    croppedImage[r_ch != 255] = (255, 255, 255)
    b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
    croppedImage[np.logical_and.reduce([r_ch == 255, b_ch == 0, g_ch == 0])] = (0, 0, 0)
    blackAndWhiteImage = cv2.cvtColor(croppedImage, cv2.COLOR_BGR2GRAY)
    blackAndWhiteImage = 255 - blackAndWhiteImage
    blackAndWhiteImage = morphclose(blackAndWhiteImage, kernel_55)
    blackAndWhiteImage = morphopen(blackAndWhiteImage, kernel_33)
    blackAndWhiteImage = morphclose(blackAndWhiteImage, kernel_33)
    # writeImageWithRollingCounter(blackAndWhiteImage, "CapsulesAsBlack")

    shortlistedContours = []
    contours, hierarchy = cv2.findContours(blackAndWhiteImage, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    upperLimitArea = 0
    # lowerLimitArea = 3.5
    lowerLimitArea = 1000000
    highestHeight2WidthRatio = 0
    lowestHeight2WidthRatio = 100
    xValues = []
    yValues = []
    nTablets = 0
    for contour in contours:
        area = cv2.contourArea(contour)
        if id == 1:
            if area < MIN_CUT_OFF_AREA_1:
                continue
            if area > MAX_CUT_OFF_AREA_1:
                continue
        if id == 2:
            if area < MIN_CUT_OFF_AREA_2:
                continue
            if area > MAX_CUT_OFF_AREA_2:
                continue
        if id == 3:
            if area < MIN_CUT_OFF_AREA_3:
                continue
            if area > MAX_CUT_OFF_AREA_3:
                continue
        upperLimitArea = max(area, upperLimitArea)
        lowerLimitArea = min(area, lowerLimitArea)
        # print(area)
        # if (area > lowerLimitArea) and (area < upperLimitArea):
        #     shortlistedContours.append(contour)
        x, y, w, h = cv2.boundingRect(contour)
        h2wRatio = h*1.0 / w
        highestHeight2WidthRatio = max(highestHeight2WidthRatio, h2wRatio)
        lowestHeight2WidthRatio = min(lowestHeight2WidthRatio, h2wRatio)
        xValues.append(x + w / 2)
        yValues.append(y + h / 2)
        nTablets += 1
        # rectangles.append((rect))
        # print(area, end=", ")
        # else:
        #     # print(f"Dropped a contour with area {area}")
        #     pass

    '''
    df = pd.DataFrame()
    df['X'] = xValues
    df['Y'] = yValues
    # global beepNow
    # global beepedForThisBottlePair
    try:
        df['dbscan'] = DBSCAN(eps= TYPICAL_DISTANCE_BETWEEN_CAPSULES, min_samples=5).fit_predict(df[['X', 'Y']])
        # df['dbscan'] = DBSCAN(eps=TYPICAL_DISTANCE_BETWEEN_CAPSULES * RESIZING_FACTOR, min_samples=5, algorithm = 'ball_tree', metric = 'haversine').fit_predict(
        # 	df[['X', 'Y']])
        # print(df)
        cluster_labels = df['dbscan'].values
        # print(f'cluster_labels = {cluster_labels}')
        unique_labels = set(cluster_labels)
        # get the number of clusters
        numberOfClusters = len(unique_labels)
    # numberOfClusters = df['dbscan'].nunique()
    except:
        numberOfClusters = 0

    '''
    # print(f"Number of clusters found initially = {numberOfClusters}")
    # if numberOfClusters >= 2:
    #     # print(df)
    #     centroids = []
    #     clusters = pd.Series([df[cluster_labels == n] for n in unique_labels])
    #     for cluster in clusters:
    #         # print(cluster)
    #         centroid_of_cluster = np.mean(cluster, axis=0)
    #         # print(centroid_of_cluster)
    #         # print(centroid_of_cluster.X, centroid_of_cluster.Y)
    #         if (np.isnan(centroid_of_cluster.X) or np.isnan(centroid_of_cluster.Y)):
    #             pass
    #         else:
    #             centroids.append((centroid_of_cluster.X, centroid_of_cluster.Y))
    #
    #     pairs = combinations(centroids, 2)
    #     maxdistance = 0
    #     for pair in pairs:
    #         pt1 = pair[0]
    #         pt2 = pair[1]
    #         dist = math.sqrt((pt1[0] - pt2[0]) * (pt1[0] - pt2[0]) + (pt1[1] - pt2[1]) * (pt1[1] - pt2[1]))
    #         maxdistance = max(dist, maxdistance)
    #
    #     if maxdistance < originalCropWidth * 0.45:
    #         numberOfClusters = 1

    # print(f'Centroids are {centroids}, max distance = {maxdistance}, number of clusters = {numberOfClusters}')

    # print(f"Frame:{counter} - Number of clusters = {numberOfClusters}")
    # goodForInspection = (numberOfClusters <= 1)
    # if goodForInspection:
    #     if len(shortlistedContours) > 0:
    #         for contour in shortlistedContours:
    #             cv2.drawContours(mask, [contour], -1, (255, 255, 255), -1, cv2.LINE_AA)
    # else:
    #     beepedForThisBottlePair = False
    #     beepNow = False
    # print(f"{numberOfClusters = }, {upperLimitArea = }, {lowerLimitArea = }")
    # return numberOfClusters, nTablets, upperLimitArea, lowerLimitArea, highestHeight2WidthRatio, lowestHeight2WidthRatio
    return nTablets, upperLimitArea, lowerLimitArea, highestHeight2WidthRatio, lowestHeight2WidthRatio

def paintCapsulesAsRedInMultipleFrames(inputVideo : str, numberToBeExtracted : int = 20):
    numberToBeExtracted = max(1,numberToBeExtracted)
    # setDebugWithShow(False)
    sourceDirectory, fileNamePrefix = extractFrames(inputVideo, reduce=True, reductiontarget=(1280,720), numberToBeExtracted=numberToBeExtracted)
    totalTime = 0
    for i in range(1,numberToBeExtracted + 1):
        fileName = f"{sourceDirectory}{fileNamePrefix}-{i}.png"
        image = cv2.imread(fileName)
        startTime = time.time()
        cropped, _ = cropToRectangleAndResizeImage(image, polygonPoints, wait=False)
        capsulesImage = paintCapsulesInRightDiscAsRed(cropped, polygonPoints, display=False)
        endTime = time.time()
        totalTime += (endTime - startTime)
        capsulesImage = makeWhitePartsTransparent(capsulesImage)
        # showImage(capsulesImage, wait=True)
        overlayTransparentImageOnAnother(cropped, capsulesImage, x_offset=None, y_offset=None)
        writeImageWithRollingCounter(cropped, 'CroppedImage')
    print(f"Time per picture = {totalTime / numberToBeExtracted}")
    print("Done")

bottomXYText = (600, 500)
middleXYText = (646, 357)
topXYText = (635, 145)

def getClustersAndArealimitsAndNumberofcapsules(inputVideo : str, startingNumber : int = 1, endingNumber : int = 20):
    startingNumber = max(1, startingNumber)
    endingNumber = max(startingNumber, endingNumber)
    # setDebugWithShow(False)
    sourceDirectory, fileNamePrefix = extractFrames(inputVideo, reduce=True, reductiontarget=(1280,720), numberToBeExtracted=1)
    totalTime = 0
    font = cv2.FONT_HERSHEY_SIMPLEX
    coords = [bottomXYText, middleXYText, topXYText]
    fontScale = 1
    color = (255, 0, 0)
    thickness = 2
    csv_file = "c:\Temp\Data.csv"
    csvfile = open(csv_file, 'w')
    csvwriter = csv.writer(csvfile)
    for i in range(startingNumber, endingNumber + 1):
        fileName = f"{sourceDirectory}{fileNamePrefix}-{i}.png"
        image = cv2.imread(fileName)
        startTime = time.time()
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(countTablets, image, aPolygon) for aPolygon in polygons]
        wait(futures)
        endTime = time.time()
        totalTime += (endTime - startTime)
        for j, aFuture in enumerate(futures):
            # numberOfClusters, nTablets, upperLimitArea, lowerLimitArea = aFuture.result()
            nTablets, upperLimitArea, lowerLimitArea, highestHeight2WidthRatio, lowestHeight2WidthRatio = aFuture.result()
            fields = [j, nTablets, upperLimitArea, lowerLimitArea, highestHeight2WidthRatio, lowestHeight2WidthRatio]
            csvwriter.writerow(fields)
            # newImage = cv2.putText(startingImage, f"{numberOfClusters}, {nTablets}, {upperLimitArea}, {lowerLimitArea}", coords[i], font,
            #                     fontScale, color, thickness, cv2.LINE_AA)
            newImage = cv2.putText(image, f"{nTablets}, {upperLimitArea}, {lowerLimitArea}, {round(highestHeight2WidthRatio, 2)}, {round(lowestHeight2WidthRatio, 2)})", coords[j], font,
                                fontScale, color, thickness, cv2.LINE_AA)
        if SHOW_POLYGONS:
            newImage = cv2.polylines(newImage, [polygons[0]], True, (255, 0, 0), thickness=1)
            newImage = cv2.polylines(newImage, [polygons[1]], True, (0, 255, 0), thickness=1)
            newImage = cv2.polylines(newImage, [polygons[2]], True, (0, 0, 255), thickness=1)
        writeImageWithRollingCounter(newImage, 'CroppedImage')
    csvfile.close()
    print(f"Time per picture = {round(totalTime * 1000 / (endingNumber + 1 - startingNumber), 1)} ms")
    print("Done")

def processRightDisc(inputVideo : str, numberToBeExtracted : int = 20):
    numberToBeExtracted = max(1,numberToBeExtracted)
    # setDebugWithShow(False)
    sourceDirectory, fileNamePrefix = extractFrames(inputVideo, reduce=True, reductiontarget=(1280,720), numberToBeExtracted=numberToBeExtracted)
    totalTime = 0
    for i in range(1,numberToBeExtracted + 1):
        fileName = f"{sourceDirectory}{fileNamePrefix}-{i}.png"
        startTime = time.time()
        image = cv2.imread(fileName)
        cropped, _ = cropToRectangleAndResizeImage(image, polygonPointsRightDisc, wait=False)
        capsulesImage = paintCapsulesInRightDiscAsRed(cropped, polygonPointsRightDisc, display=False)
        endTime = time.time()
        totalTime += (endTime - startTime)
        capsulesImage = makeWhitePartsTransparent(capsulesImage)
        # showImage(capsulesImage, wait=True)
        overlayTransparentImageOnAnother(cropped, capsulesImage, x_offset=None, y_offset=None)
        writeImage(cropped, 'CroppedImage')
    print(f"Time per picture = {totalTime / numberToBeExtracted}")
    print("Done")

def processLeftDisc(inputVideo : str, numberToBeExtracted : int = 20):
    numberToBeExtracted = max(1,numberToBeExtracted)
    # setDebugWithShow(False)
    sourceDirectory, fileNamePrefix = extractFrames(inputVideo, reduce=True, reductiontarget=(1280,720), numberToBeExtracted=numberToBeExtracted)
    totalTime = 0
    for i in range(1,numberToBeExtracted + 1):
        fileName = f"{sourceDirectory}{fileNamePrefix}-{i}.png"
        startTime = time.time()
        image = cv2.imread(fileName)
        cropped, _ = cropToRectangleAndResizeImage(image, polygonPointsLeftDisc, wait=False)
        capsulesImage = paintCapsulesInLeftDiscAsRed(cropped, polygonPointsLeftDisc, display=False)
        endTime = time.time()
        totalTime += (endTime - startTime)
        capsulesImage = makeWhitePartsTransparent(capsulesImage)
        # showImage(capsulesImage, wait=True)
        overlayTransparentImageOnAnother(cropped, capsulesImage, x_offset=None, y_offset=None)
        cropped = cv2.resize(cropped, (cropped.shape[1]*2, cropped.shape[0]*2), cv2.INTER_LANCZOS4)
        writeImage(cropped, 'CroppedImage')
    print(f"Time per picture = {totalTime / numberToBeExtracted}")
    print("Done")

# processRightDisc(r"C:\PythonProjects\Geltec\1\pictures\2024-03-29-16-41-37-387.mp4", numberToBeExtracted=3000)
# processLeftDisc(r"C:\PythonProjects\Geltec\2\pictures\2024-03-29-16-30-43-631.mp4", numberToBeExtracted=3000)
# paintCapsulesAsRedInMultipleFrames(r"D:\GelTec\Videos\2024-03-27-18-30-50-002.mp4", endingNumber=800)
getClustersAndArealimitsAndNumberofcapsules(r"D:\GelTec\Videos\2024-03-27-18-30-50-002.mp4", startingNumber=1, endingNumber=700)