import time

import numpy as np
import cv2
from PIL import Image as im
import os
import math
import copy
from ImageProcessingFunctions import *

kernel_33 = np.ones((3, 3), np.uint8)
kernel_73 = np.ones((7, 3), np.uint8)
kernel_44 = np.ones((4, 4), np.uint8)
kernel_55 = np.ones((5, 5), np.uint8)
kernel_77 = np.ones((7, 7), np.uint8)
kernel_51 = np.ones((5, 1), np.uint8)

def perpendicular(a):
    b = np.empty_like(a)
    b[0] = -a[1]
    b[1] = a[0]
    return b

# End of functions to figure out if a line intersects a rectangle
# *************************************************************

subCounter : int = 1
def writeImageWithRollingCounter(image, fileName, outputDir : str = "C:/Temp/"):
    global subCounter
    currCounter = subCounter
    subCounter += 1
    # if not debugWithWrites:
    #     return
    try:
        os.makedirs(outputDir)
    except:
        # print("Dir already exists")
        pass
    filename = f"{outputDir}{currCounter}-{fileName}.png"
    if os.path.isfile(filename):
        # print(f"Deleting file {filename}")
        os.remove(filename)
    cv2.imwrite(filename, image)

# def getWhiteBlobsOfInterest(cropped, inputPolygonPoints):
#     # print(f'Current write debug level in getWhiteBlobsOfIntereest is {writeDebugLevel}')
#
#     pts = inputPolygonPoints - inputPolygonPoints.min(axis=0)
#     # print(f'pts before resizing = {pts}')
#     # pts = (pts * RESIZING_FACTOR).astype(int)
#     # print(f'pts after resizing = {pts}')
#
#     mask = np.zeros(cropped.shape[:2], np.uint8)
#     # showNDArray(mask, 'maskOriginal')
#
#     cv2.drawContours(mask, [pts], -1, (255, 255, 255), -1, cv2.LINE_AA)
#     # showNDArray(mask, 'withContours')
#
#     # showImage(cropped, 'Cropped')
#
#     dst = cv2.bitwise_and(cropped, cropped, mask=mask)
#     # showImage(dst, 'First')
#     bg = np.ones_like(cropped, np.uint8) * 255
#     cv2.bitwise_not(bg, bg, mask=mask)
#     # showNDArray(bg, 'bg')
#     croppedImage = bg + dst
#
#     croppedImageCopy = croppedImage.copy()
#     writeImage(croppedImageCopy, 'CroppedImage')
#     # showImage(croppedImageCopy, 'original')
#
#     offset = 10
#     b_ch, g_ch, r_ch = getChannelsAsInt(croppedImageCopy)
#     # print(np.average(croppedImageCopy))
#
#     # croppedImageCopy[np.where((b_ch < 72) & (g_ch < 72) & (r_ch < 72))] = (0,0,0)
#     # croppedImageCopy[np.where(
#     #     (((b_ch < 48) & (g_ch < 48)) | ((b_ch < 48) & (r_ch < 48)) | ((r_ch < 48) & (g_ch < 48))) & (
#     #                 abs(r_ch - b_ch) > 14))] = (0, 0, 0)
#     # croppedImageCopy[np.where((r_ch > b_ch + offset) & (r_ch > g_ch + offset) & (r_ch >= 20))] = (0, 0, 0)
#     # showImage(croppedImageCopy, 'croppedImage')
#
#     # remove whites
#     # croppedImageCopy[np.where((r_ch > 90) | (b_ch > 80) | (g_ch > 90))] = (255, 255, 255)
#     croppedImageCopy[np.logical_or.reduce([(r_ch > 90), (b_ch > 80), (g_ch > 90)])] = (255, 255, 255)
#     writeImage(croppedImageCopy, 'CroppedImage')
#     b_ch, g_ch, r_ch = getChannelsAsInt(croppedImageCopy)
#     # croppedImageCopy[np.where(b_ch < 32)] = (0, 0, 0)
#     rgDiff = r_ch - g_ch
#     gbDiff = g_ch - b_ch
#     allSatisfied = np.logical_or.reduce([np.logical_and.reduce([rgDiff > 0, gbDiff > 7, r_ch > 48]),
#                                          np.logical_and.reduce([rgDiff > 6, r_ch > 54]) ,
#                                          rgDiff > 16,
#                                          np.logical_and.reduce([rgDiff > 8, r_ch > 40]),
#                                          np.logical_and.reduce([np.logical_and.reduce([rgDiff > 3, gbDiff > 5]),
#                                                                 r_ch > 48])])
#     # print(moreRed.shape, reasonablyRed.shape, bothSatisfied.shape)
#     # print(moreRed[182,503], reasonablyRed[182,503], bothSatisfied[182,503])
#     # for i in range(176,192):
#     #     print(f"r={r_ch[558,i]}, g={g_ch[558,i]}, b={b_ch[558,i]}, rgDiff={rgDiff[558,i]}, gbDiff={gbDiff[558,i]}, rgDiff>16={rgDiffGreaterThan16[558, i]},rgDiff>3={rgDiffGreaterThan3[558,i]}, gbDiff>5={gbDiffGreaterThan5[558,i]}, r>48={rchGreaterThan48[558,i]}, diffsOK={diffsAreOK[558,i]}, rgGradOK={rgGradientOK[558,i]}, moreRed={moreRed[558,i]}, allSatisfied={allSatisfied[558,i]}")
#     croppedImageCopy[allSatisfied] = (0, 0, 255)
#     # showImage(croppedImageCopy, 'croppedImage', False)
#     writeImage(croppedImageCopy, 'CroppedImage')
#     b_ch, g_ch, r_ch = getChannelsAsInt(croppedImageCopy)
#     croppedImageCopy[np.where(b_ch < 32)] = (0, 0, 0)
#     showImage(croppedImageCopy, 'croppedImage', False)
#     writeImage(croppedImageCopy, 'CroppedImage')
#     capsules = np.where((r_ch - g_ch) > 14 | (((r_ch - g_ch) > 7) & ((g_ch - b_ch) >= 3)))
#     croppedImageCopy[capsules] = (0,0,255)
#     showImage(croppedImageCopy, 'croppedImage1', False)
#     croppedImageCopy[not capsules] = (0, 0, 0)
#     showImage(croppedImageCopy, 'croppedImage2', False)
#
#
#     non_black_pixels = np.where(
#         (croppedImageCopy[:, :, 0] >= 2) & (croppedImageCopy[:, :, 1] >= 2) & (croppedImageCopy[:, :, 2] >= 2))
#     gray_pixels = np.where(
#         (croppedImageCopy[:, :, 0] >= 72) & (croppedImageCopy[:, :, 1] >= 72) & (croppedImageCopy[:, :, 2] >= 72))
#     # croppedImageCopy[np.where((croppedImageCopy == [0, 0, 0]).all(axis=2))] = [255, 255, 255]
#     croppedImageCopy[non_black_pixels] = [255, 255, 255]
#     croppedImageCopy[gray_pixels] = [255, 255, 255]
#     # showImage(croppedImageCopy, 'croppedImage')
#     # showImage(mask, 'Mask', SHOW_INTERIM_IMAGES)
#     # showImage(dst, 'Destination', SHOW_INTERIM_IMAGES)
#     # showImage(croppedImage, 'Cropped Polygon', SHOW_INTERIM_IMAGES)
#     # showImage(croppedImageCopy, 'Cropped Filtered', SHOW_MAIN_IMAGES)
#     # writeImage(outputDir=outputDir, fileName=str(counter) + f'_{subCounter}_Mask.png',
#     #            startingImage=mask, debugLevel=WRITE_INTERIM_IMAGES)
#     # writeImage(outputDir=outputDir, fileName=str(counter) + f'_{subCounter}_Dest.png',
#     #            startingImage=dst, debugLevel=WRITE_INTERIM_IMAGES)
#     # writeImage(outputDir=outputDir, fileName=str(counter) + f'_{subCounter}_CroppedPolygon.png',
#     #            startingImage=croppedImage, debugLevel=WRITE_INTERIM_IMAGES)
#     # writeImage(outputDir=outputDir, fileName=str(counter) + f'_{subCounter}_CroppedFiltered.png',
#     #            startingImage=croppedImageCopy, debugLevel=WRITE_METHOD_FINAL_IMAGES)
#     cv2.destroyAllWindows()
#     return croppedImageCopy

# to be used after calling cropAndResizeImage()

def getHistogramsWithOtsu(original, inputPolygonPoints):
    croppedImage = cropImageToExactPolygonShape(original, inputPolygonPoints)
    croppedImageCopy = croppedImage.copy()
    writeImageWithRollingCounter(croppedImageCopy, 'CroppedImage')
    showImage(croppedImageCopy, 'original')

    gray_image = cv2.cvtColor(croppedImageCopy, cv2.COLOR_BGR2GRAY)
    opened = morphopen(gray_image, kernel_33)
    # opened = cv2.GaussianBlur(opened,(7,7),0)
    writeImageWithRollingCounter(opened, 'CroppedImage')
    # ret2, otsuImage = cv2.threshold(opened, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    binarized = cv2.adaptiveThreshold(opened, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 61, 61)
    binarized = morphopen(binarized, kernel_55)
    # binarized = 255 - binarized
    # croppedImageCopy = enhance_rg_suppress_b(croppedImageCopy, clipLimit=5.0)
    writeImageWithRollingCounter(binarized, 'CroppedImage')
    # print(polygonPoints1280x720)
    mask = (binarized / 254).astype(np.uint8).astype(bool)
    print(mask.shape, croppedImageCopy.shape)
    croppedImageCopy[mask] = (255,255,255)
    writeImageWithRollingCounter(croppedImageCopy, 'CroppedImage')

    binarized = 255 - binarized
    numLabels, labels, stats, centroids = cv2.connectedComponentsWithStats(binarized, 4)
    areas = []
    coords = []
    emptyWhiteImage = np.ones((croppedImageCopy.shape[0]*4,croppedImageCopy.shape[1]*4,3), np.uint8)
    emptyWhiteImage = (emptyWhiteImage * 255).astype(np.uint8)
    font = cv2.FONT_HERSHEY_SIMPLEX
    fontScale = 1
    color = (0, 0, 0)
    thickness = 1
    counter = 0
    for i in range(1, numLabels):
        x = stats[i, cv2.CC_STAT_LEFT]
        y = stats[i, cv2.CC_STAT_TOP]
        w = stats[i, cv2.CC_STAT_WIDTH]
        h = stats[i, cv2.CC_STAT_HEIGHT]
        area = stats[i, cv2.CC_STAT_AREA]
        areas.append(area)
        keepComponent = (w < 25) & (h < 35) & (w > 3) & (h > 7) & (x > 5) & (y > 5)
        if keepComponent:
            # construct a mask for the current connected component and
            # then take the bitwise OR with the mask
            componentMask = (labels == i).astype(np.uint8) * 255
            # retainedMask = cv2.bitwise_or(retainedMask, componentMask)
            startingCoord = (x,y)
            endingCoord = (x+w, y+h)
            coords.append([startingCoord, endingCoord])
            emptyWhiteImage = cv2.putText(emptyWhiteImage, f"{counter}", (4*x + 2*w, 4*y + 2*h), font, fontScale, color, thickness, cv2.LINE_AA)
            counter += 1

    # calculate histograms
    histograms = []
    for i, aCoord in enumerate(coords):
        imageSlice = croppedImageCopy[aCoord[0][1]:aCoord[1][1],aCoord[0][0]:aCoord[1][0],:]
        b,g,r = getChannelsAsInt(imageSlice)
        r_mean = r[r != 255].mean()
        g_mean = g[g != 255].mean()
        b_mean = b[b != 255].mean()
        histograms.append([r_mean, g_mean, b_mean])
        op = f'{round(r_mean, 1)}, {round(g_mean, 1)}, {round(b_mean, 1)}'
        result = "No"
        if (r_mean - g_mean) > 5.5:
            result = "Yes"
        else:
            if (g_mean - b_mean) > 7:
                result = "Yes"
        # writeImage(imageSlice, op)
        print(f"{i} : {op} - {result}")
    writeImageWithRollingCounter(emptyWhiteImage, "CroppedImage")
    # offset = 10
    # b_ch, g_ch, r_ch = getChannelsAsInt(croppedImageCopy)
    # # remove whites
    # croppedImageCopy[np.logical_or.reduce([np.logical_and.reduce([np.logical_or.reduce([(r_ch > 85), (b_ch > 90), (g_ch > 95)]),
    #                                                                r_ch - g_ch < 7]),
    #                                         r_ch > 168])]= (255, 255, 255)
    # writeImage(croppedImageCopy, 'CroppedImage')
    # # croppedImageCopy = get_kmeans_segmented_image(croppedImageCopy, no_of_segments=3, mask_cluster = None)
    # # writeImage(croppedImageCopy, 'CroppedImage')
    # b_ch, g_ch, r_ch = getChannelsAsInt(croppedImageCopy)
    # rgDiff = r_ch - g_ch
    # gbDiff = g_ch - b_ch
    # # croppedImageCopy[np.where(b_ch < 32)] = (0, 0, 0)
    # allSatisfied = np.logical_or.reduce([np.logical_and.reduce([rgDiff > 0, gbDiff > 7, r_ch > 48]),
    #                                      np.logical_and.reduce([rgDiff > 6, r_ch > 54]) ,
    #                                      rgDiff > 16,
    #                                      np.logical_and.reduce([rgDiff > 8, r_ch > 40]),
    #                                      np.logical_and.reduce([np.logical_and.reduce([rgDiff > 3, gbDiff > 5]),
    #                                                             r_ch > 48])])
    # # print(moreRed.shape, reasonablyRed.shape, bothSatisfied.shape)
    # # print(moreRed[182,503], reasonablyRed[182,503], bothSatisfied[182,503])
    # # for i in range(176,192):
    # #     print(f"r={r_ch[558,i]}, g={g_ch[558,i]}, b={b_ch[558,i]}, rgDiff={rgDiff[558,i]}, gbDiff={gbDiff[558,i]}, rgDiff>16={rgDiffGreaterThan16[558, i]},rgDiff>3={rgDiffGreaterThan3[558,i]}, gbDiff>5={gbDiffGreaterThan5[558,i]}, r>48={rchGreaterThan48[558,i]}, diffsOK={diffsAreOK[558,i]}, rgGradOK={rgGradientOK[558,i]}, moreRed={moreRed[558,i]}, allSatisfied={allSatisfied[558,i]}")
    # croppedImageCopy[allSatisfied] = (0, 0, 255)
    # # showImage(croppedImageCopy, 'croppedImage', False)
    # writeImage(croppedImageCopy, 'CroppedImage')
    # b_ch, g_ch, r_ch = getChannelsAsInt(croppedImageCopy)
    # croppedImageCopy[np.where(b_ch < 32)] = (0, 0, 0)
    # showImage(croppedImageCopy, 'croppedImage', False)
    # writeImage(croppedImageCopy, 'CroppedImage')
    # capsules = np.where((r_ch - g_ch) > 14 | (((r_ch - g_ch) > 7) & ((g_ch - b_ch) >= 3)))
    # croppedImageCopy[capsules] = (0,0,255)
    # showImage(croppedImageCopy, 'croppedImage1', False)
    # croppedImageCopy[not capsules] = (0, 0, 0)
    # showImage(croppedImageCopy, 'croppedImage2', False)
    #
    #
    # non_black_pixels = np.where(
    #     (croppedImageCopy[:, :, 0] >= 2) & (croppedImageCopy[:, :, 1] >= 2) & (croppedImageCopy[:, :, 2] >= 2))
    # gray_pixels = np.where(
    #     (croppedImageCopy[:, :, 0] >= 72) & (croppedImageCopy[:, :, 1] >= 72) & (croppedImageCopy[:, :, 2] >= 72))
    # # croppedImageCopy[np.where((croppedImageCopy == [0, 0, 0]).all(axis=2))] = [255, 255, 255]
    # croppedImageCopy[non_black_pixels] = [255, 255, 255]
    # croppedImageCopy[gray_pixels] = [255, 255, 255]
    # # showImage(croppedImageCopy, 'croppedImage')
    # # showImage(mask, 'Mask', SHOW_INTERIM_IMAGES)
    # # showImage(dst, 'Destination', SHOW_INTERIM_IMAGES)
    # # showImage(croppedImage, 'Cropped Polygon', SHOW_INTERIM_IMAGES)
    # # showImage(croppedImageCopy, 'Cropped Filtered', SHOW_MAIN_IMAGES)
    # # writeImage(outputDir=outputDir, fileName=str(counter) + f'_{subCounter}_Mask.png',
    # #            startingImage=mask, debugLevel=WRITE_INTERIM_IMAGES)
    # # writeImage(outputDir=outputDir, fileName=str(counter) + f'_{subCounter}_Dest.png',
    # #            startingImage=dst, debugLevel=WRITE_INTERIM_IMAGES)
    # # writeImage(outputDir=outputDir, fileName=str(counter) + f'_{subCounter}_CroppedPolygon.png',
    # #            startingImage=croppedImage, debugLevel=WRITE_INTERIM_IMAGES)
    # # writeImage(outputDir=outputDir, fileName=str(counter) + f'_{subCounter}_CroppedFiltered.png',
    # #            startingImage=croppedImageCopy, debugLevel=WRITE_METHOD_FINAL_IMAGES)
    # cv2.destroyAllWindows()
    # return croppedImageCopy

def paintCapsulesInRightDiscAsRed(originalCroppedAndResizedImage, inputPolygonPoints, display : bool = True):
    originalImage = cropImageToExactPolygonShape(originalCroppedAndResizedImage, inputPolygonPoints)
    # writeImage(originalImage, 'CroppedImage')
    croppedImage = copy.deepcopy(originalImage)
    b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
    croppedImage[np.logical_and.reduce([r_ch > 90, r_ch - g_ch < 7, r_ch < 250])] = (255, 255, 255)
    croppedImage[r_ch > 180] = (255, 255, 255)
    if display:
        showImage(croppedImage, wait = True)
    # writeImage(croppedImage, 'CroppedImage')
    b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
    croppedImage[np.logical_or.reduce([g_ch < 18, b_ch < 10])] = (255, 255, 255)
    # writeImage(croppedImage, 'CroppedImage')
    if display:
        showImage(croppedImage, wait = True)

    b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
    rgDiff = r_ch - g_ch
    gbDiff = g_ch - b_ch
    croppedImage[np.logical_and.reduce([rgDiff >= 12, r_ch >= 40])] = (0,0,255)
    # croppedImage[np.logical_and.reduce([r_ch - g_ch > 10, r_ch <= 40, g_ch > b_ch - 2])] = (0, 0, 255)
    croppedImage[np.logical_and.reduce([rgDiff >= 8, gbDiff >= 6, r_ch >= 56])] = (0, 0, 255)
    # croppedImage[np.logical_and.reduce([rgDiff >= -4, gbDiff >= 6, r_ch >= 64])] = (0, 0, 255)
    croppedImage[np.logical_and.reduce([rgDiff >= 9, gbDiff >= 7, r_ch < 40])] = (0, 0, 255)
    # croppedImage[np.logical_and.reduce([rgDiff >= 8, gbDiff >= 7, r_ch > 40, r_ch < 80])] = (0, 0, 255)
    b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
    croppedImage[r_ch != 255] = (255, 255, 255)
    if display:
        showImage(croppedImage, wait = True)
    return croppedImage

def paintCapsulesInLeftDiscAsRed(originalCroppedAndResizedImage, inputPolygonPoints, display : bool = True):
    originalImage = cropImageToExactPolygonShape(originalCroppedAndResizedImage, inputPolygonPoints)
    # writeImage(originalImage, 'CroppedImage')
    croppedImage = copy.deepcopy(originalImage)
    b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
    croppedImage[r_ch - g_ch > 25] = (0, 0, 255)
    b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
    croppedImage[np.logical_and.reduce([r_ch > 120, g_ch > 10, b_ch > 10])] = (255, 255, 255)
    croppedImage[np.logical_and.reduce([r_ch > 100, r_ch - g_ch < 7, r_ch < 250])] = (255, 255, 255)
    croppedImage[np.logical_and.reduce([r_ch - g_ch < 2, g_ch - b_ch < 5])] = (255, 255, 255)
    croppedImage[np.logical_and.reduce([r_ch <= g_ch, r_ch > 10])] = (255, 255, 255)
    if display:
        showImage(croppedImage, wait = True)
    # writeImage(croppedImage, 'CroppedImage')
    b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
    croppedImage[np.logical_and.reduce([r_ch < 60, np.logical_or.reduce([g_ch < 18, b_ch < 10])])] = (255, 255, 255)
    # writeImage(croppedImage, 'CroppedImage')
    if display:
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
    b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
    croppedImage[np.logical_and.reduce([rgDiff >= 4, gbDiff >= 4, r_ch < 90])] = (0, 0, 255)
    croppedImage[r_ch != 255] = (255, 255, 255)
    if display:
        showImage(croppedImage, wait = True)
    return croppedImage

# def markCapsules(original, inputPolygonPoints):
#     originalImage = cropImageToExactPolygonShape(original, inputPolygonPoints)
#     writeImageWithRollingCounter(originalImage, 'CroppedImage')
#     croppedImage = copy.deepcopy(originalImage)
#     b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
#     croppedImage[np.logical_and.reduce([r_ch > 90, r_ch - g_ch < 7, r_ch < 250])] = (255, 255, 255)
#     croppedImage[r_ch > 180] = (255, 255, 255)
#     writeImageWithRollingCounter(croppedImage, 'CroppedImage')
#     b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
#     croppedImage[np.logical_or.reduce([g_ch < 18, b_ch < 10])] = (255, 255, 255)
#     writeImageWithRollingCounter(croppedImage, 'CroppedImage')
#
#     b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
#     rgDiff = r_ch - g_ch
#     gbDiff = g_ch - b_ch
#     croppedImage[np.logical_and.reduce([rgDiff >= 12, r_ch >= 40])] = (0,0,255)
#     # croppedImage[np.logical_and.reduce([r_ch - g_ch > 10, r_ch <= 40, g_ch > b_ch - 2])] = (0, 0, 255)
#     croppedImage[np.logical_and.reduce([rgDiff >= 8, gbDiff >= 6, r_ch >= 56])] = (0, 0, 255)
#     # croppedImage[np.logical_and.reduce([rgDiff >= -4, gbDiff >= 6, r_ch >= 64])] = (0, 0, 255)
#     croppedImage[np.logical_and.reduce([rgDiff >= 9, gbDiff >= 7, r_ch < 40])] = (0, 0, 255)
#     # croppedImage[np.logical_and.reduce([rgDiff >= 8, gbDiff >= 7, r_ch > 40, r_ch < 80])] = (0, 0, 255)
#     writeImageWithRollingCounter(croppedImage, 'CroppedImage')

    # isRed = np.logical_and.reduce([r_ch - g_ch > 1, r_ch - g_ch < 6, g_ch - b_ch >= 10])
    # croppedImage[isRed] = (0, 0, 255)
    # writeImage(croppedImage, 'CroppedImage')

    # b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
    # croppedImage[g_ch < b_ch] = (255,255,255)
    # writeImage(croppedImage, 'CroppedImage')
    # b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
    #
    #
    # croppedImageCopy = croppedImage.copy()
    # croppedImageCopy = cv2.cvtColor(croppedImageCopy, cv2.COLOR_BGR2GRAY)
    # croppedImageCopy[croppedImageCopy != 255] = 0
    # binarized = 255 - croppedImageCopy
    # writeImage(binarized, 'CroppedImage')
    # numLabels, labels, stats, centroids = cv2.connectedComponentsWithStats(binarized, 4)
    # areas = []
    # coords = []
    # emptyWhiteImage = np.ones((croppedImageCopy.shape[0]*4,croppedImageCopy.shape[1]*4,3), np.uint8)
    # emptyWhiteImage = (emptyWhiteImage * 255).astype(np.uint8)
    # font = cv2.FONT_HERSHEY_SIMPLEX
    # fontScale = 1
    # color = (0, 0, 0)
    # thickness = 1
    # counter = 0
    # for i in range(1, numLabels):
    #     x = stats[i, cv2.CC_STAT_LEFT]
    #     y = stats[i, cv2.CC_STAT_TOP]
    #     w = stats[i, cv2.CC_STAT_WIDTH]
    #     h = stats[i, cv2.CC_STAT_HEIGHT]
    #     area = stats[i, cv2.CC_STAT_AREA]
    #     areas.append(area)
    #     keepComponent = (w < 25) & (h < 35) & (w > 3) & (h > 7) & (x > 5) & (y > 5)
    #     if keepComponent:
    #         # construct a mask for the current connected component and
    #         # then take the bitwise OR with the mask
    #         componentMask = (labels == i).astype(np.uint8) * 255
    #         # retainedMask = cv2.bitwise_or(retainedMask, componentMask)
    #         startingCoord = (x,y)
    #         endingCoord = (x+w, y+h)
    #         coords.append([startingCoord, endingCoord])
    #         emptyWhiteImage = cv2.putText(emptyWhiteImage, f"{counter}", (4*x + 2*w, 4*y + 2*h), font, fontScale, color, thickness, cv2.LINE_AA)
    #         counter += 1
    #
    # # calculate histograms
    # histograms = []
    # for i, aCoord in enumerate(coords):
    #     imageSlice = croppedImage[aCoord[0][1]:aCoord[1][1],aCoord[0][0]:aCoord[1][0],:]
    #     b,g,r = getChannelsAsInt(imageSlice)
    #     r_mean = r[r != 255].mean()
    #     g_mean = g[g != 255].mean()
    #     b_mean = b[b != 255].mean()
    #     histograms.append([r_mean, g_mean, b_mean])
    #     op = f'{round(r_mean,1)}, {round(g_mean,1)}, {round(b_mean,1)}'
    #     result = "No"
    #     if (r_mean - g_mean) > 5.5:
    #         result = "Yes"
    #     else:
    #         if (g_mean - b_mean) > 7:
    #             result = "Yes"
    #     # writeImage(imageSlice, op)
    #     print(f"{i} : {op} - {result}")
    # writeImage(emptyWhiteImage, "CroppedImage")

def calcPercentageAreaPerBoundingBox(original, inputPolygonPoints):
    originalImage = cropImageToExactPolygonShape(original, inputPolygonPoints)
    writeImageWithRollingCounter(originalImage, 'CroppedImage')
    croppedImage = copy.deepcopy(originalImage)
    b_ch, g_ch, r_ch = getChannelsAsInt(croppedImage)
    croppedImage[np.logical_and.reduce([r_ch > 90, r_ch - g_ch < 7, r_ch < 250])] = (255, 255, 255)
    croppedImage[r_ch > 180] = (255, 255, 255)
    writeImageWithRollingCounter(croppedImage, 'CroppedImage')

    croppedImageCopy = croppedImage.copy()
    croppedImageCopy = cv2.cvtColor(croppedImageCopy, cv2.COLOR_BGR2GRAY)
    croppedImageCopy[croppedImageCopy != 255] = 0
    binarized = 255 - croppedImageCopy
    writeImageWithRollingCounter(binarized, 'CroppedImage')
    numLabels, labels, stats, centroids = cv2.connectedComponentsWithStats(binarized, 4)
    emptyWhiteImage = np.ones((croppedImageCopy.shape[0]*4,croppedImageCopy.shape[1]*4,3), np.uint8)
    emptyWhiteImage = (emptyWhiteImage * 255).astype(np.uint8)
    font = cv2.FONT_HERSHEY_SIMPLEX
    fontScale = 1
    color = (0, 0, 0)
    thickness = 1
    counter = 0
    percentageAreasCovered = []
    for i in range(1, numLabels):
        x = stats[i, cv2.CC_STAT_LEFT]
        y = stats[i, cv2.CC_STAT_TOP]
        w = stats[i, cv2.CC_STAT_WIDTH]
        h = stats[i, cv2.CC_STAT_HEIGHT]
        area = stats[i, cv2.CC_STAT_AREA]
        keepComponent = (w < 25) & (h < 35) & (w > 3) & (h > 7) & (x > 5) & (y > 5)
        if keepComponent:
            # construct a mask for the current connected component and
            # then take the bitwise OR with the mask
            # componentMask = (labels == i).astype(np.uint8) * 255
            # retainedMask = cv2.bitwise_or(retainedMask, componentMask)
            # startingCoord = (x,y)
            # endingCoord = (x+w, y+h)
            # imageSlice = binarized[y:y+h, x:x+w]
            # areaCovered = np.sum(imageSlice) / 255
            # percentageAreaCovered = int(round(areaCovered / area * 100, 0))
            percentageAreaCovered = int(round(area / (w*h) * 100, 0))
            percentageAreasCovered.append(percentageAreaCovered)
            emptyWhiteImage = cv2.putText(emptyWhiteImage, f"{percentageAreaCovered}", (4*x + 2*w, 4*y + 2*h), font, fontScale, color, thickness, cv2.LINE_AA)
            # emptyWhiteImage = cv2.putText(emptyWhiteImage, f"{int(areaCovered)}", (4 * x + 2 * w, 4 * y + 2 * h),
            #                               font, fontScale, color, thickness, cv2.LINE_AA)
            counter += 1
    writeImageWithRollingCounter(emptyWhiteImage, "CroppedImage")


polygonPoints640x480 = createPolygon([[282, 466], [262, 300], [205, 225], [275, 0], [360, 0], [405, 36], [429, 64], [438, 107], [443, 128],  [452, 145], [464, 158], [485, 163], [519, 272], [531, 471]])
# bottomPolygonPoints = createPolygon(
#     [[390, 325], [290, 275], [185, 255], [10, 310], [10, 475], [210, 475], [360, 455]])
polygonPoints1920x1080 = createPolygon([[837, 1064], [821, 715], [653, 471], [803, 176], [994, 0], [1097, 0], [1316, 227], [1323, 259], [1344, 298], [1365, 331], [1375, 341], [1451, 357], [1570, 680], [1585, 1055]])
polygonPoints320x240 = createPolygon([[140, 238], [135, 173], [106, 109], [149, 0], [180, 0], [218, 39], [218, 51], [219, 55], [223, 65],  [227, 74], [241, 79], [260, 150], [261, 235]])
polygonPoints1280x720 = createPolygon([[564, 932], [516, 463], [432,328], [651, 0], [720, 0], [810, 72], [877, 151], [877, 162], [881, 172], [891, 194], [910, 225], [933, 230], [993, 295], [1049, 477], [1054, 709]])
# polygonPoints1280x720_1 = createPolygon([[565, 931], [525, 599], [411, 451], [551, 1], [719, 1], [811, 73], [874, 152], [878, 163], [881, 173], [892, 195], [911, 226], [933, 234], [993, 294], [1049, 476], [1054, 709]])

# fileName = "D:/GelTec/Videos/frames/2024-03-22-09-57-12-761 - frames numbered-6.png"
# startingImage = 13
# endingImage = 13
# startTime = time.time()
# for i in range(startingImage,endingImage+1):
#     fileName = f"D:/GelTec/Videos/reduced/frames/2024-03-22-09-47-47-670-{i}.png"
#     startingImage = cv2.imread(fileName)
#     # cropped, _ = cropAndResizeImage(startingImage, polygonPoints1920x1080)
#     # getWhiteBlobsOfInterest(cropped, polygonPoints1920x1080)
#     cropped, _ = cropAndResizeImage(startingImage, polygonPoints1280x720)
#     # getWhiteBlobsOfInterest(cropped, polygonPoints640x480)
#     # markRedsHandCoded(cropped, polygonPoints1280x720)
#     calcPercentageAreaPerBoundingBox(cropped, polygonPoints1280x720)
# endTime = time.time()
# print(f"{round((endTime - startTime) * 1000 / (endingImage + 1 - startingImage),1)} ms")
#
# fileName = f"D:/GelTec/Videos/reduced/frames/2024-03-22-09-47-47-670-1.png"
# startingImage = cv2.imread(fileName)
# roiImage, _ = cropAndResizeImage(startingImage, polygonPoints1280x720, wait=True)
# roiImage = cropImage(roiImage, polygonPoints1280x720, wait=True)