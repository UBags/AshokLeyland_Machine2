import time

import numpy as np
import cv2
from PIL import Image as im
import os
import math
import copy

def showImage(image, title='img', wait : bool = False):
    cv2.imshow(title, image)
    if wait:
        cv2.waitKey(0)

subCounter : int = 1
def writeImage(image, fileName, outputDir : str = "C:/Temp/", useSubCounter : bool = True):
    global subCounter
    try:
        os.makedirs(outputDir)
    except:
        # print("Dir already exists")
        pass
    if useSubCounter:
        filename = f"{outputDir}{subCounter}-{fileName}.png"
    else:
        filename = f"{outputDir}{fileName}.png"
    if os.path.isfile(filename):
        # print(f"Deleting file {filename}")
        os.remove(filename)
    cv2.imwrite(filename, image)
    subCounter += 1

kernel_12 = np.ones((1, 2), np.uint8)
kernel_21 = np.ones((2, 1), np.uint8)
kernel_33 = np.ones((3, 3), np.uint8)
kernel_73 = np.ones((7, 3), np.uint8)
kernel_44 = np.ones((4, 4), np.uint8)
kernel_55 = np.ones((5, 5), np.uint8)
kernel_55_elliptical = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(5,5))
kernel_77 = np.ones((7, 7), np.uint8)
kernel_51 = np.ones((5, 1), np.uint8)

def perpendicular(a):
    b = np.empty_like(a)
    b[0] = -a[1]
    b[1] = a[0]
    return b


# line segment a given by endpoints a1, a2
# line segment b given by endpoints b1, b2
def seg_intersection(a1, a2, b1, b2):
    da = a2 - a1
    db = b2 - b1
    dp = a1 - b1
    dap = perpendicular(da)
    denom = np.dot(dap, db)
    num = np.dot(dap, dp)
    try:
        intersectionCoords = (num / denom.astype(float)) * db + b1
    except:
        intersectionCoords = [np.NaN, np.NaN]
    return intersectionCoords

def get_intersection(a1, a2, b1, b2):
    """
    Returns the point of intersection of the lines passing through a2,a1 and b2,b1.
    a1: [x, y] a point on the first line
    a2: [x, y] another point on the first line
    b1: [x, y] a point on the second line
    b2: [x, y] another point on the second line
    """
    s = np.vstack([a1, a2, b1, b2])  # s for stacked
    h = np.hstack((s, np.ones((4, 1))))  # h for homogeneous
    l1 = np.cross(h[0], h[1])  # get first line
    l2 = np.cross(h[2], h[3])  # get second line
    x, y, z = np.cross(l1, l2)  # point of intersection
    if z == 0:  # lines are parallel
        return (float('nan'), float('nan'))
    return (x / z, y / z)


def isPointInsideRectangle(point, rectangle):
    (x, y, w, h) = rectangle
    if (x <= point[0] <= x + w) and (y <= point[1] <= y + h):
        return True
    return False


# line is provided as a list of 2 points e.g. [(2,3),(4,6)]
# rectangles is a list of rectangles e.g. [(2,3,4,6), (3,8,7,6), ((4,5),(6,7),(2,3),(8,7)]
def doesLineIntersectSetOfRectangles(line, rectangles):
    # np.cross(p2 - p1, p3 - p1) / norm(p2 - p1)
    P1, P2 = line
    P1 = np.asarray(P1)
    P2 = np.asarray(P2)

    vertices = []
    for rect in rectangles:
        x, y, w, h = rect
        if isinstance(x, int):
            vertices.append((x, y))
            vertices.append((x + w, y))
            vertices.append((x + w, y + h))
            vertices.append((x, y + h))
        else:
            if isinstance(x, tuple):
                vertices.append(x)
                vertices.append(y)
                vertices.append(w)
                vertices.append(h)
    P3 = np.asarray(vertices)

    distances = np.cross(P2 - P1, P3 - P1) / np.linalg.norm(P2 - P1)
    distances = np.around(distances, 2)
    # print(distances)
    intersects = False
    initialSign = None
    for distance in distances:
        if initialSign == None:
            initialSign = distance / abs(distance)
        else:
            currentSign = distance / abs(distance)
            if currentSign != initialSign:
                intersects = True
                break
    return intersects


def doesLineDivideSetOfRectanglesInAcceptableRatio(line, rectangles, minRatio=0.25):
    # np.cross(p2 - p1, p3 - p1) / norm(p2 - p1)
    P1, P2 = line
    P1 = np.asarray(P1)
    P2 = np.asarray(P2)

    vertices = []
    for rect in rectangles:
        x, y, w, h = rect
        if isinstance(x, int):
            vertices.append((x, y))
            vertices.append((x + w, y))
            vertices.append((x + w, y + h))
            vertices.append((x, y + h))
        else:
            if isinstance(x, tuple):
                vertices.append(x)
                vertices.append(y)
                vertices.append(w)
                vertices.append(h)
    P3 = np.asarray(vertices)

    distances = np.cross(P2 - P1, P3 - P1) / np.linalg.norm(P2 - P1)
    positives = 0
    negatives = 0
    for distance in distances:
        if distance >= 0:
            positives += 1
        else:
            negatives += 1
    if ((positives / (positives + negatives) > minRatio) and
            (negatives / (positives + negatives) > minRatio)):
        return True
    return False


def recalculateInspectionLineForCroppedRectangle(originalInspectionLine, rectangle):
    linePt1 = originalInspectionLine[0]
    linePt2 = originalInspectionLine[1]

    x, y, w, h = rectangle
    rectPt1 = [x, y]
    rectPt2 = [x + w, y]
    rectPt3 = [x + w, y + h]
    rectPt4 = [x, y + h]

    lineIntersectsRectangle = doesLineIntersectSetOfRectangles(originalInspectionLine, [rectangle])

    endPoints = []
    if lineIntersectsRectangle:
        intersectionPt_1 = get_intersection(linePt1, linePt2, rectPt1, rectPt2)
        intersectionPt_2 = get_intersection(linePt1, linePt2, rectPt2, rectPt3)
        intersectionPt_3 = get_intersection(linePt1, linePt2, rectPt3, rectPt4)
        intersectionPt_4 = get_intersection(linePt1, linePt2, rectPt1, rectPt4)
        if isPointInsideRectangle(intersectionPt_1, rectangle):
            endPoints.append([intersectionPt_1[0] - rectPt1[0], 0])
        if isPointInsideRectangle(intersectionPt_2, rectangle):
            endPoints.append([w, intersectionPt_2[1] - rectPt2[1]])
        if isPointInsideRectangle(intersectionPt_3, rectangle):
            endPoints.append([intersectionPt_3[0] - rectPt4[0], h])
        if isPointInsideRectangle(intersectionPt_4, rectangle):
            endPoints.append([0, intersectionPt_4[1] - rectPt1[1]])
    else:
        intersectionPt_1 = get_intersection(linePt1, linePt2, rectPt1, rectPt2)
        if intersectionPt_1[0] < rectPt1[0]:
            endPoints.append([0, 0])
            endPoints.append([0, h])
        else:
            endPoints.append([w, 0])
            endPoints.append([w, h])
    return endPoints


# End of functions to figure out if a line intersects a rectangle
# *************************************************************

def calculateAreaForContourOfFloatPoints(listOfFloatTuples):
    area = cv2.contourArea(np.around(np.array([[pt] for pt in listOfFloatTuples])).astype(np.int32))
    return area

def getCVImageFromNdarray(ndarr):
    pic = im.fromarray(ndarr.astype(np.uint8))
    pic = np.array(pic)
    return pic

def showNDArray(array, title='img', wait : bool = False):
    image = getCVImageFromNdarray(array)
    cv2.imshow(title, image)          # was missing: the helper showed nothing (CHANGES 110)
    if wait:
        cv2.waitKey(0)

def dilate(image, kernel):
    # expands whites
    dilatedImage = cv2.dilate(image, kernel)
    return dilatedImage

def erode(image, kernel):
    # expands blacks
    erodedImage = cv2.erode(image, kernel)
    return erodedImage

def morphclose(image, kernel):
    dilated = dilate(image, kernel)
    eroded = erode(dilated, kernel)
    return eroded

def morphopen(image, kernel):
    eroded = erode(image, kernel)
    dilated = dilate(eroded, kernel)
    return dilated

def createPolygon(listOfPoints: list) -> list:
    polygon = np.array(listOfPoints).reshape((-1, 1, 2)).astype(np.int32)
    return polygon

def getChannelsAsInt(image : np.ndarray):
    b_ch, g_ch, r_ch = cv2.split(image)
    b = b_ch.astype(np.int32)
    g = g_ch.astype(np.int32)
    r = r_ch.astype(np.int32)
    return b, g, r

def kmeans_preprocess_image(image):
    """Reshape the startingImage to a 2D array of pixels and 3 color values (RGB) and convert to float."""
    pixel_values = image.reshape((-1, 3))
    return np.float32(pixel_values)

def perform_kmeans_clustering(pixel_values, k=3):
    """Perform k-means clustering on the pixel values."""
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 100, 0.2)
    compactness, labels, centers = cv2.kmeans(pixel_values, k, None, criteria, 10, cv2.KMEANS_RANDOM_CENTERS)
    return compactness, labels, np.uint8(centers)

def create_kmeans_segmented_image(originalImage, pixel_values, labels, centers):
    """Create a segmented startingImage using the cluster centroids."""
    segmented_image = centers[labels.flatten()]
    return segmented_image.reshape(originalImage.shape)

def create_kmeans_masked_image(originalImage, labels, cluster_to_disable):
    """Create a masked startingImage by disabling a specific cluster."""
    masked_image = np.copy(originalImage).reshape((-1, 3))
    masked_image[labels.flatten() == cluster_to_disable] = [0, 0, 0]
    return masked_image.reshape(originalImage.shape)

def get_kmeans_segmented_image(image, no_of_segments = 3, mask_cluster : int = None):
    pixel_values = kmeans_preprocess_image(image)
    compactness, labels, centers = perform_kmeans_clustering(pixel_values, no_of_segments)
    segmented_image = create_kmeans_segmented_image(image, pixel_values, labels, centers)
    if mask_cluster == None:
        return segmented_image
    elif mask_cluster < no_of_segments:
        return create_kmeans_masked_image(image, labels, mask_cluster)
    else:
        return segmented_image

def determine_gamma(rgbimage):
    hsv = cv2.cvtColor(rgbimage, cv2.COLOR_RGB2HSV)
    hue, sat, val = cv2.split(hsv)

    mid = 0.5
    val1 = val[val < 99]
    mean = np.mean(val1)
    gamma = math.log(mid*255)/math.log(mean)
    if gamma < 1:
        return gamma
    if gamma < 1.25:
        gamma = (gamma - 1)*3 + 1
        return gamma
    if gamma < 1.5:
        gamma = (gamma - 1)*2 + 1
        return gamma
    return gamma

def apply_gamma(image, gamma=1.0):
	# build a lookup table mapping the pixel values [0, 255] to
	# their adjusted gamma values
	invGamma = 1.0 / gamma
	table = np.array([((i / 255.0) ** invGamma) * 255
		for i in np.arange(0, 256)]).astype("uint8")
	# apply gamma correction using the lookup table
	return cv2.LUT(image, table)

def get_rgbcolor_equalized_image(image, clipLimit = 8.0, tileGridSize = (8,8)):
    #  histogram equalisation
    image_gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    clahe = cv2.createCLAHE(clipLimit=clipLimit, tileGridSize=tileGridSize)
    image_gray_equalized = clahe.apply(image_gray)
    image_gray[image_gray == 0] = 1
    pixel_ratio = image_gray_equalized / image_gray
    r_ch, g_ch, b_ch = cv2.split(image)
    r_ch = r_ch.astype(np.int32)
    g_ch = g_ch.astype(np.int32)
    b_ch = b_ch.astype(np.int32)
    r_ch = r_ch * pixel_ratio
    g_ch = g_ch * pixel_ratio
    b_ch = b_ch * pixel_ratio
    r_ch[r_ch > 255] = 255
    r_ch[r_ch < 0] = 0
    g_ch[g_ch > 255] = 255
    g_ch[g_ch < 0] = 0
    b_ch[b_ch > 255] = 255
    b_ch[b_ch < 0] = 0
    r_ch = r_ch.astype(np.uint8)
    g_ch = g_ch.astype(np.uint8)
    b_ch = b_ch.astype(np.uint8)
    image_Equalized = cv2.merge([b_ch,g_ch,r_ch])
    image_Equalized = cv2.cvtColor(image_Equalized, cv2.COLOR_BGR2RGB)
    return image_Equalized

def apply_brightness_contrast(input_img, brightness = 0, contrast = 0):
    if brightness != 0:
        if brightness > 0:
            shadow = brightness
            highlight = 255
        else:
            shadow = 0
            highlight = 255 + brightness
            alpha_b = (highlight - shadow)/255
            gamma_b = shadow
            buf = cv2.addWeighted(input_img, alpha_b, input_img, 0, gamma_b)
    else:
        buf = input_img.copy()

    if contrast != 0:
        f = 131*(contrast + 127)/(127*(131-contrast))
        alpha_c = f
        gamma_c = 127*(1-f)
        buf = cv2.addWeighted(buf, alpha_c, buf, 0, gamma_c)

    return buf

def change_contrast_brightness(rgbimg, clipLimit : float = 2.0):
  lab= cv2.cvtColor(rgbimg, cv2.COLOR_RGB2LAB)
  l_channel, a, b = cv2.split(lab)

  # Applying CLAHE to L-channel
  clahe = cv2.createCLAHE(clipLimit=clipLimit, tileGridSize=(8,8))
  new_l_channel = clahe.apply(l_channel)

  # merge the CLAHE enhanced L-channel with the a and b channel
  limg = cv2.merge((new_l_channel,a,b))

  # Converting startingImage from LAB Color model to BGR color spcae
  enhanced_img = cv2.cvtColor(limg, cv2.COLOR_LAB2RGB)
  return enhanced_img

def enhance_rg_suppress_b(rgbimg, clipLimit : float = 2.0):
  lab= cv2.cvtColor(rgbimg, cv2.COLOR_RGB2LAB)
  l_channel, a, b = cv2.split(lab)

  # Applying CLAHE to a-channel, which is R and G
  clahe = cv2.createCLAHE(clipLimit=clipLimit, tileGridSize=(8,8))
  new_a_channel = clahe.apply(a)

  # Applying CLAHE to b-channel, which is Y and B
  clahe = cv2.createCLAHE(clipLimit=0.75, tileGridSize=(8,8))
  new_b_channel = clahe.apply(b)

  # merge the CLAHE enhanced L-channel with the a and b channel
  limg = cv2.merge((l_channel,new_a_channel,new_b_channel))

  # Converting startingImage from LAB Color model to BGR color spcae
  enhanced_img = cv2.cvtColor(limg, cv2.COLOR_LAB2RGB)
  return enhanced_img

def getOtsuThreshold(a1DArray, numberOfBins = 52, adjustment = 12):
    counts, bin_edges = np.histogram(a1DArray, bins = numberOfBins)
    bin_centers = (bin_edges[1:] + bin_edges[:-1]) / 2

    # class probabilities for all possible thresholds
    weight1 = np.cumsum(counts)
    weight2 = np.cumsum(counts[::-1])[::-1]
    # class means for all possible thresholds
    mean1 = np.cumsum(counts * bin_centers) / weight1
    mean2 = (np.cumsum((counts * bin_centers)[::-1]) / weight2[::-1])[::-1]

    # Clip ends to align class 1 and class 2 variables:
    # The last value of ``weight1``/``mean1`` should pair with zero values in
    # ``weight2``/``mean2``, which do not exist.
    variance12 = weight1[:-1] * weight2[1:] * (mean1[:-1] - mean2[1:]) ** 2

    idx = np.argmax(variance12)
    threshold = bin_centers[idx]
    return min(max(0, int(threshold) - adjustment), 254)

def cropToRectangleAndResizeImage(image, inputPolygonPoints):
    # Note: inputPolygonPoints should be of type np.int32
    originalCroppingRectangle = cv2.boundingRect(inputPolygonPoints)
    x, y, w, h = originalCroppingRectangle
    cropped = image[y:y + h, x:x + w].copy()
    width = int(cropped.shape[1] * 1)
    height = int(cropped.shape[0] * 1)
    dim = (width, height)
    # resize anImage
    resized = cv2.resize(cropped, dim, interpolation=cv2.INTER_LANCZOS4)
    return resized, originalCroppingRectangle

def cropImageToExactPolygonShape(image, somePolygonPoints, wait : bool = True):
    pts = somePolygonPoints - somePolygonPoints.min(axis=0)
    mask = np.zeros(image.shape[:2], np.uint8)
    cv2.drawContours(mask, [pts], -1, (255, 255, 255), -1, cv2.LINE_AA)
    dst = cv2.bitwise_and(image, image, mask=mask)
    bg = np.ones_like(image, np.uint8) * 255
    cv2.bitwise_not(bg, bg, mask=mask)
    croppedImage = bg + dst
    return croppedImage

def reduceSize(image: np.ndarray, reductionfactor : float = 2.0):
    newImage = cv2.resize(image, (int(image.shape[1]//reductionfactor), int(image.shape[0]//reductionfactor)), cv2.INTER_LANCZOS4)
    return newImage

