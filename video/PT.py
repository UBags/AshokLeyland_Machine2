# The core logic of processing images to count tablets is in this program

import time
from copy import deepcopy

import cv2
import glob
from PIL import Image as im
from numpy import *
from operator import *
import pandas as pd
from sklearn.cluster import DBSCAN
from itertools import combinations
from monitorAllConnections.HeartbeatAndAlarmServer import *

REQUIRED_NUMBER_OF_TABLETS = 30
RESIZING_FACTOR = 1
TYPICAL_DISTANCE_BETWEEN_CAPSULES = 45
CURRENT_TABLET_SIZE_MEAN = 150
CURRENT_TABLET_SIZE_STDDEV = 35

SHOW_INTERIM_IMAGES = 1
SHOW_END_OF_MAJOR_METHOD_IMAGES = 2
SHOW_MAIN_IMAGES = 3
SHOW_FINAL_IMAGES = 4
SHOW_NO_IMAGES = 5
currentShowImageDebugLevel = 5

WRITE_FRAME_NUMBER = False
SHOW_FRAME_NUMBER = True
SHOW_TABLET_COUNT = False
SHOW_POLYGONS = False
SHOW_CLUSTER_COUNTS = False
BEEP_ON_RED = True

def setCurrentShowImageDebugLevel(currShowImageDebugLevel):
	global currentShowImageDebugLevel
	currentShowImageDebugLevel = currShowImageDebugLevel


WRITE_INTERIM_IMAGES = 1
WRITE_MAIN_IMAGES = 2
WRITE_METHOD_FINAL_IMAGES = 3
WRITE_EVENTUAL_IMAGES = 4
WRITE_NO_IMAGES = 5
currentWriteImageDebugLevel = 4

def setCurrentWriteImageDebugLevel(currWriteImageDebugLevel):
	global currentWriteImageDebugLevel
	currentWriteImageDebugLevel = currWriteImageDebugLevel


inputDir = '/home/uddipan/PycharmProjects/GeltecCapsules/Input'
outputDir = '/home/uddipan/PycharmProjects/GeltecCapsules/Output'
# path = inputDir + "/frame-30-09-2022-08-40-23.jpg"
path = inputDir + "/*.jpg"
# path = inputDir + "/frame-31-10-2022-14-19-49.jpg"
# path = inputDir + "/frame-31-10-2022-16-17-49.jpg"
# path = inputDir + "/frame-31-10-2022-16-18-42.jpg"

counter = 1
subCounter = 1

kernel_33 = np.ones((3, 3), np.uint8)
kernel_73 = np.ones((7, 3), np.uint8)
kernel_44 = np.ones((4, 4), np.uint8)
kernel_55 = np.ones((5, 5), np.uint8)
kernel_77 = np.ones((7, 7), np.uint8)
kernel_51 = np.ones((5, 1), np.uint8)

# *************************************************************
# Functions to figure out if a line intersects a rectangle
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


# DOES NOT WORK. Use the method 'doesLineIntersectSetOfRectangles' below this
def doesLineIntersectRectangle(lineCoords, rectangle):
	([x1, y1], [x2, y2]) = lineCoords
	(x, y, w, h) = rectangle
	p1Line1 = array([x1, y1])
	p2Line1 = array([x2, y2])
	
	p1RectLine1 = array([x, y])
	p2RectLine1 = array([x + w, y])
	
	p1RectLine2 = array([x + w, y])
	p2RectLine2 = array([x + w, y + h])
	
	p1RectLine3 = array([x + w, y + h])
	p2RectLine3 = array([x, y + h])
	
	p1RectLine4 = array([x, y + h])
	p2RectLine4 = array([x, y])
	
	intersection1 = get_intersection(p1Line1, p2Line1, p1RectLine1, p2RectLine1)
	intersection2 = get_intersection(p1Line1, p2Line1, p1RectLine2, p2RectLine2)
	intersection3 = get_intersection(p1Line1, p2Line1, p1RectLine3, p2RectLine3)
	intersection4 = get_intersection(p1Line1, p2Line1, p1RectLine4, p2RectLine4)
	
	if (isPointInsideRectangle(intersection1, (rectangle)) or
			isPointInsideRectangle(intersection2, (rectangle)) or
			isPointInsideRectangle(intersection3, (rectangle)) or
			isPointInsideRectangle(intersection4, (rectangle))):
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
	# distances = np.around(distances, 2)
	# print(distances)
	# intersects = False
	# initialSign = None
	# for distance in distances:
	#     if initialSign == None:
	#         initialSign = distance / abs(distance)
	#     else:
	#         currentSign = distance / abs(distance)
	#         if currentSign != initialSign:
	#             intersects = True
	#             break
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

# print(doesLineIntersectSetOfRectangles([(2,2),(3,3)],[(6,0,1,1),(1,5,2,2)]))

def calculateAreaForContourOfFloatPoints(listOfFloatTuples):
	area = cv2.contourArea(np.around(np.array([[pt] for pt in listOfFloatTuples])).astype(np.int32))
	return area


def showImage(image, title='img', debugLevel=SHOW_INTERIM_IMAGES):
	if (debugLevel >= currentShowImageDebugLevel):
		cv2.imshow(title, image)
		cv2.waitKey(0)


def getCVImageFromNdarray(ndarr):
	pic = im.fromarray(ndarr.astype(np.uint8))
	pic = np.array(pic)
	return pic


def showNDArray(array, title='img', debugLevel=SHOW_INTERIM_IMAGES):
	image = getCVImageFromNdarray(array)
	showImage(image, title, debugLevel)


def writeImage(outputDir, fileName, image, debugLevel=WRITE_INTERIM_IMAGES):
	global subCounter
	try:
		os.makedirs(outputDir)
	except:
		pass
	if debugLevel >= currentWriteImageDebugLevel:
		# print(f'Writing to {outputDir + fileName}')
		cv2.imwrite(outputDir + '/' + fileName, image)
		subCounter += 1


def listFiles(inputPath: str, recursive=False) -> list:
	files = []
	for file in glob.iglob(inputPath, recursive=recursive):
		files.append(file)
	return files


def createPolygon(listOfPoints: list) -> list:
	polygon = np.array(listOfPoints).reshape((-1, 1, 2)).astype(np.int32)
	return polygon


def dilate(image, kernel, showInterim=False):
	dilatedImage = cv2.dilate(image, kernel)
	if showInterim:
		showImage(dilatedImage, f'Dilated with {kernel.shape}')
	return dilatedImage



def erode(image, kernel, showInterim=False):
	erodedImage = cv2.erode(image, kernel)
	if showInterim:
		showImage(erodedImage, f'Eroded with {kernel.shape}')
	return erodedImage


kernel_33 = np.ones((3, 3), np.uint8)

def morphclose(image, kernel, showInterim=False):
	dilated = dilate(image, kernel, showInterim)
	eroded = erode(dilated, kernel, showInterim)
	return eroded


def morphopen(image, kernel):
	eroded = erode(image, kernel)
	dilated = dilate(eroded, kernel)
	return dilated


def cropAndResizeImage(image, inputPolygonPoints, showDebugLevel=currentShowImageDebugLevel,
                       writeDebugLevel=currentWriteImageDebugLevel):
	global subCounter
	# print(f'Current write debug level in cropAndResizeImage is {writeDebugLevel}')
	subCounter = 0
	cImgDebugLevel = currentShowImageDebugLevel
	cWriteDebugLevel = currentWriteImageDebugLevel
	setCurrentShowImageDebugLevel(showDebugLevel)
	setCurrentWriteImageDebugLevel(writeDebugLevel)
	showImage(image, 'Original', SHOW_INTERIM_IMAGES)
	writeImage(outputDir=outputDir, fileName=str(counter) + f'_{subCounter}_Original.png',
	           image=image, debugLevel=WRITE_INTERIM_IMAGES)
	originalCroppingRectangle = cv2.boundingRect(inputPolygonPoints)
	x, y, w, h = originalCroppingRectangle
	cropped = image[y:y + h, x:x + w].copy()
	showImage(cropped, 'Cropped', SHOW_INTERIM_IMAGES)
	writeImage(outputDir=outputDir, fileName=str(counter) + f'_{subCounter}_Cropped.png',
	           image=cropped, debugLevel=WRITE_INTERIM_IMAGES)
	width = int(cropped.shape[1] * RESIZING_FACTOR)
	height = int(cropped.shape[0] * RESIZING_FACTOR)
	dim = (width, height)
	# print(dim)
	# resize anImage
	resized = cv2.resize(cropped, dim, interpolation=cv2.INTER_AREA)
	showImage(resized, 'Cropped_Resized', SHOW_INTERIM_IMAGES)
	writeImage(outputDir=outputDir, fileName=str(counter) + f'_{subCounter}_Cropped_Resized.png',
	           image=resized, debugLevel=WRITE_INTERIM_IMAGES)
	setCurrentShowImageDebugLevel(cImgDebugLevel)
	setCurrentWriteImageDebugLevel(cWriteDebugLevel)
	return resized, originalCroppingRectangle

def getWhiteBlobsOfInterest(cropped, inputPolygonPoints, showDebugLevel=currentShowImageDebugLevel,
                            writeDebugLevel=currentWriteImageDebugLevel):
	# print(f'Current write debug level in getWhiteBlobsOfIntereest is {writeDebugLevel}')
	cImgDebugLevel = currentShowImageDebugLevel
	cWriteDebugLevel = currentWriteImageDebugLevel
	setCurrentShowImageDebugLevel(showDebugLevel)
	setCurrentWriteImageDebugLevel(writeDebugLevel)
	pts = inputPolygonPoints - inputPolygonPoints.min(axis=0)
	# print(f'pts before resizing = {pts}')
	pts = (pts * RESIZING_FACTOR).astype(int)
	# print(f'pts after resizing = {pts}')
	
	mask = np.zeros(cropped.shape[:2], np.uint8)
	cv2.drawContours(mask, [pts], -1, (255, 255, 255), -1, cv2.LINE_AA)
	
	dst = cv2.bitwise_and(cropped, cropped, mask=mask)
	bg = np.ones_like(cropped, np.uint8) * 255
	cv2.bitwise_not(bg, bg, mask=mask)
	croppedImage = bg + dst
	
	croppedImageCopy = croppedImage.copy()
	offset = 10
	b_ch, g_ch, r_ch = cv2.split(croppedImageCopy)
	# print(np.average(croppedImageCopy))

	# croppedImageCopy[np.where((b_ch < 72) & (g_ch < 72) & (r_ch < 72))] = (0,0,0)
	croppedImageCopy[np.where((((b_ch < 48) & (g_ch < 48)) | ((b_ch < 48) & (r_ch < 48)) | ((r_ch < 48) & (g_ch < 48))) & (abs(r_ch - b_ch) > 14))] = (0, 0, 0)
	# croppedImageCopy[np.where((r_ch > b_ch + offset) & (r_ch > g_ch + offset) & (r_ch >= 20))] = (0, 0, 0)

	non_black_pixels = np.where(
		(croppedImageCopy[:, :, 0] >= 2) & (croppedImageCopy[:, :, 1] >= 2) & (croppedImageCopy[:, :, 2] >= 2))
	gray_pixels = np.where(
		(croppedImageCopy[:, :, 0] >= 72) & (croppedImageCopy[:, :, 1] >= 72) & (croppedImageCopy[:, :, 2] >= 72))
	# croppedImageCopy[np.where((croppedImageCopy == [0, 0, 0]).all(axis=2))] = [255, 255, 255]
	croppedImageCopy[non_black_pixels] = [255, 255, 255]
	croppedImageCopy[gray_pixels] = [255, 255, 255]
	showImage(mask, 'Mask', SHOW_INTERIM_IMAGES)
	showImage(dst, 'Destination', SHOW_INTERIM_IMAGES)
	showImage(croppedImage, 'Cropped Polygon', SHOW_INTERIM_IMAGES)
	showImage(croppedImageCopy, 'Cropped Filtered', SHOW_MAIN_IMAGES)
	writeImage(outputDir=outputDir, fileName=str(counter) + f'_{subCounter}_Mask.png',
	           image=mask, debugLevel=WRITE_INTERIM_IMAGES)
	writeImage(outputDir=outputDir, fileName=str(counter) + f'_{subCounter}_Dest.png',
	           image=dst, debugLevel=WRITE_INTERIM_IMAGES)
	writeImage(outputDir=outputDir, fileName=str(counter) + f'_{subCounter}_CroppedPolygon.png',
	           image=croppedImage, debugLevel=WRITE_INTERIM_IMAGES)
	writeImage(outputDir=outputDir, fileName=str(counter) + f'_{subCounter}_CroppedFiltered.png',
	           image=croppedImageCopy, debugLevel=WRITE_METHOD_FINAL_IMAGES)

	setCurrentShowImageDebugLevel(cImgDebugLevel)
	setCurrentWriteImageDebugLevel(cWriteDebugLevel)
	return croppedImageCopy


beepedForThisBottlePair = False
beepNow = True


def binarizeAndInvert(croppedImage, showDebugLevel=currentShowImageDebugLevel,
                      writeDebugLevel=currentWriteImageDebugLevel):
	# print(f'Current write debug level in binarizeAndInvert is {writeDebugLevel}')
	cImgDebugLevel = currentShowImageDebugLevel
	cWriteDebugLevel = currentWriteImageDebugLevel
	setCurrentShowImageDebugLevel(showDebugLevel)
	setCurrentWriteImageDebugLevel(writeDebugLevel)
	gray = cv2.cvtColor(croppedImage, cv2.COLOR_BGR2GRAY)
	th, inverted_binarized = cv2.threshold(gray, 84, 255, cv2.THRESH_BINARY_INV)
	inverted_binarized = morphclose(inverted_binarized, kernel_73)
	inverted_binarized = morphopen(inverted_binarized, kernel_33)
	inverted_binarized = morphopen(inverted_binarized, kernel_51)
	# opened = open(inverted_binarized, kernel_33)
	# opened = open(opened, kernel_44)
	showImage(gray, 'Grayed Image', SHOW_INTERIM_IMAGES)
	showImage(inverted_binarized, 'Inverted Binarized Image', SHOW_INTERIM_IMAGES)
	writeImage(outputDir=outputDir, fileName=str(counter) + f'_{subCounter}_InvertedBinarized.png',
	           image=inverted_binarized, debugLevel=WRITE_METHOD_FINAL_IMAGES)
	# showImage(opened, 'After close', SHOW_MAIN_IMAGES)
	# return opened
	setCurrentShowImageDebugLevel(cImgDebugLevel)
	setCurrentWriteImageDebugLevel(cWriteDebugLevel)
	return inverted_binarized

def filterContoursAndIdentifyCapsules(image, originalCropWidth, originalCropHeight,
                                      mean=CURRENT_TABLET_SIZE_MEAN * RESIZING_FACTOR * RESIZING_FACTOR,
                                      stdDev=CURRENT_TABLET_SIZE_STDDEV * RESIZING_FACTOR * RESIZING_FACTOR,
                                      showDebugLevel=currentShowImageDebugLevel,
                                      writeDebugLevel=currentWriteImageDebugLevel):
	
	# print(f'Current write debug level in filterContoursAndIdentifyCapsules is {writeDebugLevel}')
	cImgDebugLevel = currentShowImageDebugLevel
	cWriteDebugLevel = currentWriteImageDebugLevel
	setCurrentShowImageDebugLevel(showDebugLevel)
	setCurrentWriteImageDebugLevel(writeDebugLevel)
	mask = np.zeros(image.shape[:2], np.uint8)
	noChangeMask = mask.copy()
	showImage(mask, title='ProcCntr Mask', debugLevel=SHOW_INTERIM_IMAGES)
	writeImage(outputDir=outputDir, fileName=str(counter) + f'_{subCounter}_NoChangeMask.png',
	           image=mask, debugLevel=WRITE_INTERIM_IMAGES)
	shortlistedContours = []
	contours, hierarchy = cv2.findContours(image, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
	# upperLimit = mean + 4.5 * stdDev
	# lowerLimit = mean - 3.75 * stdDev
	# upperLimit = 330
	upperLimit = 380
	# lowerLimit = 3.5
	lowerLimit = 6
	# print(lowerLimit, upperLimit)
	# mask = np.zeros(anImage.shape[:2], dtype=anImage.dtype)
	# print(f"Counter - {counter}")
	# print(f"lowerLimit = {lowerLimit}, upperLimit = {upperLimit}")
	xValues = []
	yValues = []
	for contour in contours:
		area = cv2.contourArea(contour)
		# print(area)
		if (area > lowerLimit) and (area < upperLimit):
			shortlistedContours.append(contour)
			x, y, w, h = cv2.boundingRect(contour)
			xValues.append(x + w / 2)
			yValues.append(y + h / 2)
			# rectangles.append((rect))
			# print(area, end=", ")
		else:
			# print(f"Dropped a contour with area {area}")
			pass
	df = pd.DataFrame()
	df['X'] = xValues
	df['Y'] = yValues
	global beepNow
	global beepedForThisBottlePair
	try:
		df['dbscan'] = DBSCAN(eps=TYPICAL_DISTANCE_BETWEEN_CAPSULES * RESIZING_FACTOR, min_samples=5).fit_predict(
			df[['X', 'Y']])
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
	
	# print(f"Number of clusters found initially = {numberOfClusters}")

	if numberOfClusters >= 2:
		# print(df)
		centroids = []
		clusters = pd.Series([df[cluster_labels == n] for n in unique_labels])
		for cluster in clusters:
			# print(cluster)
			centroid_of_cluster = np.mean(cluster, axis=0)
			# print(centroid_of_cluster)
			# print(centroid_of_cluster.X, centroid_of_cluster.Y)
			if (np.isnan(centroid_of_cluster.X) or np.isnan(centroid_of_cluster.Y)):
				pass
			else:
				centroids.append((centroid_of_cluster.X, centroid_of_cluster.Y))
		
		pairs = combinations(centroids, 2)
		maxdistance = 0
		for pair in pairs:
			pt1 = pair[0]
			pt2 = pair[1]
			dist = math.sqrt((pt1[0] - pt2[0])*(pt1[0] - pt2[0]) + (pt1[1] - pt2[1])*(pt1[1] - pt2[1]))
			maxdistance = max(dist, maxdistance)
		
		if maxdistance < originalCropWidth * 0.45:
			numberOfClusters = 1
		
		# print(f'Centroids are {centroids}, max distance = {maxdistance}, number of clusters = {numberOfClusters}')

	# print(f"Frame:{counter} - Number of clusters = {numberOfClusters}")
	goodForInspection = (numberOfClusters <= 1)
	if goodForInspection:
		if len(shortlistedContours) > 0:
			for contour in shortlistedContours:
				cv2.drawContours(mask, [contour], -1, (255, 255, 255), -1, cv2.LINE_AA)
	else:
		beepedForThisBottlePair = False
		beepNow = False
	croppedImageCapsulesMask = mask.copy()
	showImage(croppedImageCapsulesMask, title='And-ing Mask', debugLevel=SHOW_INTERIM_IMAGES)
	writeImage(outputDir=outputDir, fileName=str(counter) + f'_{subCounter}_After anding mask.png',
	           image=croppedImageCapsulesMask, debugLevel=WRITE_INTERIM_IMAGES)
	dim = (originalCropWidth, originalCropHeight)
	# resize anImage and original mask
	originalImageCapsulesMask = cv2.resize(croppedImageCapsulesMask, dim, interpolation=cv2.INTER_AREA)
	noChangeMask = cv2.resize(noChangeMask, dim, interpolation=cv2.INTER_AREA)
	showImage(originalImageCapsulesMask, 'Resized_Final_Mask', SHOW_INTERIM_IMAGES)
	writeImage(outputDir=outputDir, fileName=str(counter) + f'_{subCounter}_Resized_Final_Mask.png',
	           image=originalImageCapsulesMask, debugLevel=WRITE_METHOD_FINAL_IMAGES)
	setCurrentShowImageDebugLevel(cImgDebugLevel)
	setCurrentWriteImageDebugLevel(cWriteDebugLevel)
	return originalImageCapsulesMask, noChangeMask, len(shortlistedContours), goodForInspection, numberOfClusters

############ Declaring some global variables #############
upperBottomRight = None
upperTopLeft = None
upperMid = None

lowerBottomRight = None
lowerTopLeft = None
lowerMid = None
############ Finished declaring some global variables #############

def processTabletCountFromImageFile(inputFile: str, inputPolygonPointsTop: list, inputPolygonPointsBottom: list,
                                    showDebugLevel=currentShowImageDebugLevel,
                                    writeDebugLevel=currentWriteImageDebugLevel):

	global counter
	original = cv2.imread(inputFile)
	print(f"Read file {inputFile}")
	fileName = os.path.splitext(os.path.basename(inputFile))[0]
	
	if SHOW_TABLET_COUNT:
		global upperBottomRight
		global lowerBottomRight
		
		if upperBottomRight is None:
			upperBottomRight = tuple(min(inputPolygonPointsTop, key=lambda pt: (-pt[0][1], -pt[0][0]))[0])
		if lowerBottomRight is None:
			lowerBottomRight = tuple(min(inputPolygonPointsBottom, key=lambda pt: (-pt[0][1], -pt[0][0]))[0])

	original_copy = original.copy()

	class CapsulesProcessor(threading.Thread):
		
		def __init__(self, image, polygonPoints, name=None):
			super(CapsulesProcessor, self).__init__(name=name)
			self.image = image
			self.polygonPoints = polygonPoints
			return
		
		def doProcessing(self, image, polygonPoints):
			self.croppedResizedImage, self.originalCroppingRectangle = cropAndResizeImage(image, polygonPoints,
			                                                                    showDebugLevel=showDebugLevel,
			                                                                    writeDebugLevel=writeDebugLevel)
			self.X, self.Y, self.W, self.H = self.originalCroppingRectangle
			self.whiteBlobsOfInterestInImage = getWhiteBlobsOfInterest(self.croppedResizedImage, polygonPoints,
			                                                      showDebugLevel=showDebugLevel,
			                                                      writeDebugLevel=writeDebugLevel)
			self.binarizedInvertedImage = binarizeAndInvert(self.whiteBlobsOfInterestInImage, showDebugLevel=showDebugLevel,
			                                           writeDebugLevel=writeDebugLevel)
			self.capsulesMask, self.capsulesNoChangeMask, self.tabletsInImage, self.goodForInspection, self.nClusters = filterContoursAndIdentifyCapsules(
				self.binarizedInvertedImage, self.W, self.H, showDebugLevel=showDebugLevel, writeDebugLevel=writeDebugLevel)
		
		def run(self) -> None:
			self.doProcessing(self.image, self.polygonPoints)
	
	topCapsulesProcessor = CapsulesProcessor(original_copy, inputPolygonPointsTop)
	bottomCapsulesProcessor = CapsulesProcessor(original_copy, inputPolygonPointsBottom)
	topCapsulesProcessor.start()
	bottomCapsulesProcessor.start()
	topCapsulesProcessor.join()
	bottomCapsulesProcessor.join()
	finalTopCapsulesMask, topCapsulesNoChangeMask, tabletsInTopImage, topIsGoodForInspection, topClusters = \
		topCapsulesProcessor.capsulesMask, topCapsulesProcessor.capsulesNoChangeMask, topCapsulesProcessor.tabletsInImage, topCapsulesProcessor.goodForInspection, topCapsulesProcessor.nClusters
	finalBottomCapsulesMask, bottomCapsulesNoChangeMask, tabletsInBottomImage, bottomIsGoodForInspection, bottomClusters = \
		bottomCapsulesProcessor.capsulesMask, bottomCapsulesProcessor.capsulesNoChangeMask, bottomCapsulesProcessor.tabletsInImage, bottomCapsulesProcessor.goodForInspection, bottomCapsulesProcessor.nClusters
	
	print(f"topIsGoodForInspection = {topIsGoodForInspection} and bottomIsGoodForInspection = {bottomIsGoodForInspection}")

	if topIsGoodForInspection and bottomIsGoodForInspection:
		if tabletsInTopImage == REQUIRED_NUMBER_OF_TABLETS:
			original_copy[topCapsulesProcessor.Y:topCapsulesProcessor.Y + topCapsulesProcessor.H,
			topCapsulesProcessor.X:topCapsulesProcessor.X + topCapsulesProcessor.W][finalTopCapsulesMask == 255] = (
				0, 255, 0)
		else:
			original_copy[topCapsulesProcessor.Y:topCapsulesProcessor.Y + topCapsulesProcessor.H,
			topCapsulesProcessor.X:topCapsulesProcessor.X + topCapsulesProcessor.W][finalTopCapsulesMask == 255] = (
				0, 0, 255)
		
		if tabletsInBottomImage == REQUIRED_NUMBER_OF_TABLETS:
			original_copy[bottomCapsulesProcessor.Y:bottomCapsulesProcessor.Y + bottomCapsulesProcessor.H,
			bottomCapsulesProcessor.X:bottomCapsulesProcessor.X + bottomCapsulesProcessor.W][
				finalBottomCapsulesMask == 255] = (0, 255, 0)
		else:
			original_copy[bottomCapsulesProcessor.Y:bottomCapsulesProcessor.Y + bottomCapsulesProcessor.H,
			bottomCapsulesProcessor.X:bottomCapsulesProcessor.X + bottomCapsulesProcessor.W][
				finalBottomCapsulesMask == 255] = (0, 0, 255)
	else:
		pass
	
	if SHOW_TABLET_COUNT:
		original_copy = cv2.putText(original_copy, str(tabletsInTopImage), upperBottomRight, cv2.FONT_HERSHEY_SIMPLEX, 1,
		                            (255, 0, 0), 2, cv2.LINE_AA, False)
		original_copy = cv2.putText(original_copy, str(tabletsInBottomImage), lowerBottomRight, cv2.FONT_HERSHEY_SIMPLEX,
		                            1, (255, 0, 0), 2, cv2.LINE_AA, False)
	
	if SHOW_POLYGONS:
		original_copy = cv2.polylines(original_copy, [inputPolygonPointsTop], True, (255, 0, 0), thickness=1)
		original_copy = cv2.polylines(original_copy, [inputPolygonPointsBottom], True, (0, 0, 255), thickness=1)
	
	writeImage(outputDir=outputDir, fileName=fileName + '_CroppedPolygonTop.png',
	           image=topCapsulesProcessor.whiteBlobsOfInterestInImage, debugLevel=WRITE_MAIN_IMAGES)
	writeImage(outputDir=outputDir, fileName=fileName + '_CroppedPolygonBottom.png',
	           image=bottomCapsulesProcessor.whiteBlobsOfInterestInImage, debugLevel=WRITE_MAIN_IMAGES)
	writeImage(outputDir=outputDir, fileName=fileName + '_BinarizedCroppedPolygonTop.png',
	           image=topCapsulesProcessor.binarizedInvertedImage, debugLevel=WRITE_INTERIM_IMAGES)
	writeImage(outputDir=outputDir, fileName=fileName + '_BinarizedCroppedPolygonBottom.png',
	           image=bottomCapsulesProcessor.binarizedInvertedImage, debugLevel=WRITE_INTERIM_IMAGES)
	writeImage(outputDir=outputDir, fileName=fileName + '_FilteredTopImage.png', image=finalTopCapsulesMask,
	           debugLevel=WRITE_MAIN_IMAGES)
	writeImage(outputDir=outputDir, fileName=fileName + '_FilteredBottomImage.png', image=finalBottomCapsulesMask,
	           debugLevel=WRITE_MAIN_IMAGES)
	# writeImage(outputDir=outputDir, fileName=str(counter) + '_FinalImage.png', anImage=original_copy,
	#            debugLevel=WRITE_EVENTUAL_IMAGES)
	# print(os.path.splitext(os.path.basename(inputFile)))
	writeImage(outputDir=outputDir, fileName=fileName + '_FinalImage.png', image=original_copy,
	           debugLevel=WRITE_EVENTUAL_IMAGES)
	counter += 1
	# print(f"The number of tablets in top anImage is {tabletsInTopImage} and in bottom anImage is {tabletsInBottomImage}")
	# cv2.destroyAllWindows()
	return original_copy

def processTabletCountFromImage_ChangeTabletColours(inputImage, inputPolygonPointsTop: ndarray, inputPolygonPointsBottom: ndarray,
                                showDebugLevel=SHOW_NO_IMAGES, writeDebugLevel=WRITE_NO_IMAGES):
	# print(f'inputImage = {inputImage.shape}')
	# print(f'topPolygon = {inputPolygonPointsTop}')
	# print(f'bottomPolygon = {inputPolygonPointsBottom}')
	if inputImage is None:
		return None
	
	global counter
	if SHOW_FRAME_NUMBER:
		print(f'--------------Frame number is {counter}; Write debug level is {writeDebugLevel}----------------------')

	global beepedForThisBottlePair
	
	if SHOW_TABLET_COUNT:
		global upperBottomRight
		global lowerBottomRight
		
		if upperBottomRight is None:
			upperBottomRight = tuple(min(inputPolygonPointsTop, key=lambda pt: (-pt[0][1], -pt[0][0]))[0])
		if lowerBottomRight is None:
			lowerBottomRight = tuple(min(inputPolygonPointsBottom, key=lambda pt: (-pt[0][1], -pt[0][0]))[0])
	original_copy = inputImage.copy()
	
	class CapsulesProcessor(threading.Thread):
		
		def __init__(self, image, polygonPoints, name=None):
			super(CapsulesProcessor, self).__init__(name=name)
			self.image = image
			self.polygonPoints = polygonPoints
			return
		
		def doProcessing(self, image, polygonPoints):
			self.croppedResizedImage, self.originalCroppingRectangle = cropAndResizeImage(image, polygonPoints,
			                                                                    showDebugLevel=showDebugLevel,
			                                                                    writeDebugLevel=writeDebugLevel)
			self.X, self.Y, self.W, self.H = self.originalCroppingRectangle
			self.whiteBlobsOfInterestInImage = getWhiteBlobsOfInterest(self.croppedResizedImage, polygonPoints,
			                                                      showDebugLevel=showDebugLevel,
			                                                      writeDebugLevel=writeDebugLevel)
			self.binarizedInvertedImage = binarizeAndInvert(self.whiteBlobsOfInterestInImage, showDebugLevel=showDebugLevel,
			                                           writeDebugLevel=writeDebugLevel)
			self.capsulesMask, self.capsulesNoChangeMask, self.tabletsInImage, self.goodForInspection, self.nClusters = filterContoursAndIdentifyCapsules(
				self.binarizedInvertedImage, self.W, self.H, showDebugLevel=showDebugLevel, writeDebugLevel=writeDebugLevel)
		
		def run(self) -> None:
			self.doProcessing(self.image, self.polygonPoints)
	
	topCapsulesProcessor = CapsulesProcessor(inputImage, inputPolygonPointsTop)
	bottomCapsulesProcessor = CapsulesProcessor(original_copy, inputPolygonPointsBottom)
	topCapsulesProcessor.start()
	bottomCapsulesProcessor.start()
	topCapsulesProcessor.join()
	bottomCapsulesProcessor.join()
	finalTopCapsulesMask, topCapsulesNoChangeMask, tabletsInTopImage, topIsGoodForInspection , topClusters = \
		topCapsulesProcessor.capsulesMask, topCapsulesProcessor.capsulesNoChangeMask, topCapsulesProcessor.tabletsInImage, topCapsulesProcessor.goodForInspection, topCapsulesProcessor.nClusters
	finalBottomCapsulesMask, bottomCapsulesNoChangeMask, tabletsInBottomImage, bottomIsGoodForInspection, bottomClusters = \
		bottomCapsulesProcessor.capsulesMask, bottomCapsulesProcessor.capsulesNoChangeMask, bottomCapsulesProcessor.tabletsInImage, bottomCapsulesProcessor.goodForInspection, bottomCapsulesProcessor.nClusters
	
	global beepNow
	if topIsGoodForInspection and bottomIsGoodForInspection:
		if tabletsInTopImage == REQUIRED_NUMBER_OF_TABLETS:
			original_copy[topCapsulesProcessor.Y:topCapsulesProcessor.Y + topCapsulesProcessor.H,
			topCapsulesProcessor.X:topCapsulesProcessor.X + topCapsulesProcessor.W][finalTopCapsulesMask == 255] = (
			0, 255, 0)
		else:
			original_copy[topCapsulesProcessor.Y:topCapsulesProcessor.Y + topCapsulesProcessor.H,
			topCapsulesProcessor.X:topCapsulesProcessor.X + topCapsulesProcessor.W][finalTopCapsulesMask == 255] = (
			0, 0, 255)
			if not beepedForThisBottlePair:
				beepNow = True
		
		if tabletsInBottomImage == REQUIRED_NUMBER_OF_TABLETS:
			original_copy[bottomCapsulesProcessor.Y:bottomCapsulesProcessor.Y + bottomCapsulesProcessor.H,
			bottomCapsulesProcessor.X:bottomCapsulesProcessor.X + bottomCapsulesProcessor.W][
				finalBottomCapsulesMask == 255] = (0, 255, 0)
		else:
			original_copy[bottomCapsulesProcessor.Y:bottomCapsulesProcessor.Y + bottomCapsulesProcessor.H,
			bottomCapsulesProcessor.X:bottomCapsulesProcessor.X + bottomCapsulesProcessor.W][
				finalBottomCapsulesMask == 255] = (0, 0, 255)
			if not beepedForThisBottlePair:
				beepNow = True
	else:
		pass
	
	if beepNow:
		HeartbeatAndAlarmServer(BEEP_ON_RED).start()
		beepedForThisBottlePair = True
		setBeepNow(False)
	
	if SHOW_TABLET_COUNT:
		original_copy = cv2.putText(original_copy, str(tabletsInTopImage), upperBottomRight, cv2.FONT_HERSHEY_SIMPLEX, 1,
		                            (255, 0, 0), 2, cv2.LINE_AA, False)
		
		original_copy = cv2.putText(original_copy, str(tabletsInBottomImage), lowerBottomRight, cv2.FONT_HERSHEY_SIMPLEX,
		                            1,
		                            (255, 0, 0), 2, cv2.LINE_AA, False)
	
	if SHOW_POLYGONS:
		original_copy = cv2.polylines(original_copy, [inputPolygonPointsTop], True, (255, 0, 0), thickness=1)
		original_copy = cv2.polylines(original_copy, [inputPolygonPointsBottom], True, (0, 0, 255), thickness=1)

	if SHOW_CLUSTER_COUNTS:
		original_copy = cv2.putText(original_copy, ', ' + str(topClusters), (upperBottomRight[0] + 60, upperBottomRight[1]), cv2.FONT_HERSHEY_SIMPLEX, 1,
		                            (255, 0, 0), 2, cv2.LINE_AA, False)
		
		original_copy = cv2.putText(original_copy, ', ' + str(bottomClusters), (lowerBottomRight[0] + 60, lowerBottomRight[1]), cv2.FONT_HERSHEY_SIMPLEX,
		                            1,
		                            (255, 0, 0), 2, cv2.LINE_AA, False)
	
	if SHOW_FRAME_NUMBER:
		original_copy = cv2.putText(original_copy, str(counter), (20,25), cv2.FONT_HERSHEY_SIMPLEX, 1,
		                            (0, 0, 0), 3, cv2.LINE_AA, False)

	writeImage(outputDir=outputDir, fileName=str(counter) + f'_{subCounter}_Final_Image.png',
	           image=original_copy, debugLevel=WRITE_EVENTUAL_IMAGES)
	
	counter += 1
	return original_copy

topCountInLastFewFrames = []
bottomCountInLastFewFrames = []
frameNumberFromWhereCountingStarted = -1
canBeep = False
badDataFrameCount = 0

def processTabletCountFromImage_WritePassFail(inputImage, inputPolygonPointsTop: ndarray, inputPolygonPointsBottom: ndarray,
                                showDebugLevel=SHOW_NO_IMAGES, writeDebugLevel=WRITE_NO_IMAGES):
	# print(f'inputImage = {inputImage.shape}')
	# print(f'topPolygon = {inputPolygonPointsTop}')
	# print(f'bottomPolygon = {inputPolygonPointsBottom}')
	
	global counter
	counter += 1
	if WRITE_FRAME_NUMBER:
		print(f'--------------Frame number is {counter}; Write debug level is {writeDebugLevel}----------------------')

	if inputImage is None:
		return None

	global frameNumberFromWhereCountingStarted
	global canBeep
	global badDataFrameCount

	if SHOW_TABLET_COUNT:
		global upperBottomRight
		global upperTopLeft
		global upperMid
		global lowerBottomRight
		global lowerTopLeft
		global lowerMid
		
		if upperBottomRight is None:
			upperBottomRight = tuple(min(inputPolygonPointsTop, key=lambda pt: (-pt[0][1], -pt[0][0]))[0])
			upperTopLeft = tuple(min(inputPolygonPointsTop, key=lambda pt: (pt[0][1],  pt[0][0]))[0])
			upperMid = ((upperBottomRight[0] + upperTopLeft[0]) / 2 - 30, (upperBottomRight[1] + upperTopLeft[1]) / 2 - 30)
			print(f'upperBottomRight = {upperBottomRight}; upperTopLeft = {upperTopLeft}; upperMid = {upperMid}')
		if lowerBottomRight is None:
			# lowerBottomRight = tuple(min(inputPolygonPointsBottom, key=lambda pt: (-pt[0][0], -pt[0][1]))[0])
			lowerBottomRight = (350, 450)
			lowerTopLeft = tuple(min(inputPolygonPointsBottom, key=lambda pt: (pt[0][1],  pt[0][0]))[0])
			lowerMid = ((lowerBottomRight[0] + lowerTopLeft[0]) / 2 - 30, (lowerBottomRight[1] + lowerTopLeft[1]) / 2 - 30)
			print(f'lowerBottomRight = {lowerBottomRight}; lowerTopLeft = {lowerTopLeft}; lowerMid = {lowerMid}')

	original_copy = inputImage.copy()
	
	class CapsulesProcessor(threading.Thread):
		
		def __init__(self, image, polygonPoints, name=None):
			super(CapsulesProcessor, self).__init__(name=name)
			self.image = image
			self.polygonPoints = polygonPoints
			return
		
		def doProcessing(self, image, polygonPoints):
			self.croppedResizedImage, self.originalCroppingRectangle = cropAndResizeImage(image, polygonPoints,
			                                                                              showDebugLevel=showDebugLevel,
			                                                                              writeDebugLevel=writeDebugLevel)
			self.X, self.Y, self.W, self.H = self.originalCroppingRectangle
			self.whiteBlobsOfInterestInImage = getWhiteBlobsOfInterest(self.croppedResizedImage, polygonPoints,
			                                                           showDebugLevel=showDebugLevel,
			                                                           writeDebugLevel=writeDebugLevel)
			self.binarizedInvertedImage = binarizeAndInvert(self.whiteBlobsOfInterestInImage,
			                                                showDebugLevel=showDebugLevel,
			                                                writeDebugLevel=writeDebugLevel)
			self.capsulesMask, self.capsulesNoChangeMask, self.tabletsInImage, self.goodForInspection, self.nClusters = filterContoursAndIdentifyCapsules(
				self.binarizedInvertedImage, self.W, self.H, showDebugLevel=showDebugLevel,
				writeDebugLevel=writeDebugLevel)
		
		def run(self) -> None:
			self.doProcessing(self.image, self.polygonPoints)
	
	topCapsulesProcessor = CapsulesProcessor(inputImage, inputPolygonPointsTop)
	bottomCapsulesProcessor = CapsulesProcessor(original_copy, inputPolygonPointsBottom)
	topCapsulesProcessor.start()
	bottomCapsulesProcessor.start()
	topCapsulesProcessor.join()
	bottomCapsulesProcessor.join()
	finalTopCapsulesMask, topCapsulesNoChangeMask, tabletsInTopImage, topIsGoodForInspection, topClusters = \
		topCapsulesProcessor.capsulesMask, topCapsulesProcessor.capsulesNoChangeMask, topCapsulesProcessor.tabletsInImage, topCapsulesProcessor.goodForInspection, topCapsulesProcessor.nClusters
	finalBottomCapsulesMask, bottomCapsulesNoChangeMask, tabletsInBottomImage, bottomIsGoodForInspection, bottomClusters = \
		bottomCapsulesProcessor.capsulesMask, bottomCapsulesProcessor.capsulesNoChangeMask, bottomCapsulesProcessor.tabletsInImage, bottomCapsulesProcessor.goodForInspection, bottomCapsulesProcessor.nClusters

	if (topClusters != 1) or (bottomClusters != 1):
		if (((counter - frameNumberFromWhereCountingStarted) <= 10) & (frameNumberFromWhereCountingStarted != -1) & (badDataFrameCount < 2)):
			topCountInLastFewFrames.append(1 if (tabletsInTopImage == REQUIRED_NUMBER_OF_TABLETS) else 0)
			bottomCountInLastFewFrames.append(1 if (tabletsInBottomImage == REQUIRED_NUMBER_OF_TABLETS) else 0)
			badDataFrameCount += 1
		else:
			topCountInLastFewFrames.clear()
			bottomCountInLastFewFrames.clear()
			frameNumberFromWhereCountingStarted = -1
			badDataFrameCount = 0
	else:
		if frameNumberFromWhereCountingStarted == -1:
			frameNumberFromWhereCountingStarted = counter
			canBeep = True
		topCountInLastFewFrames.append(1 if (tabletsInTopImage == REQUIRED_NUMBER_OF_TABLETS) else 0)
		bottomCountInLastFewFrames.append(1 if (tabletsInBottomImage == REQUIRED_NUMBER_OF_TABLETS) else 0)

	# print(f'{frameNumberFromWhereCountingStarted}, {topCountInLastFewFrames}, {bottomCountInLastFewFrames}')
	
	if frameNumberFromWhereCountingStarted != -1:
		if 7 <= (counter - frameNumberFromWhereCountingStarted) <= 11:
			topCopy = deepcopy(topCountInLastFewFrames)
			bottomCopy = deepcopy(bottomCountInLastFewFrames)
			topCopy = topCopy[2:7]
			bottomCopy = bottomCopy[2:7]
			if len(topCopy) > 0:
				topAvg = sum(topCopy) / len(topCopy)
			else:
				topAvg = 0
			if len(bottomCopy) > 0:
				bottomAvg = sum(bottomCopy) / len(bottomCopy)
			else:
				bottomAvg = 0
			if (topAvg >= 0.6):
				drawGreenText(original_copy, "OK", (110,140))
			else:
				drawRedText(original_copy, "Not OK", (110, 140))
			if (bottomAvg >= 0.6):
				drawGreenText(original_copy, "OK", (110, 340))
			else:
				drawRedText(original_copy, "Not OK", (110, 340))

			if ((topAvg < 0.6) or (bottomAvg < 0.6)) and canBeep:
				HeartbeatAndAlarmServer(BEEP_ON_RED).start()
				canBeep = False
	
	if SHOW_TABLET_COUNT:
		original_copy = cv2.putText(original_copy, str(tabletsInTopImage), upperBottomRight, cv2.FONT_HERSHEY_SIMPLEX, 1,
		                            (255, 0, 0), 2, cv2.LINE_AA, False)
		
		original_copy = cv2.putText(original_copy, str(tabletsInBottomImage), lowerBottomRight, cv2.FONT_HERSHEY_SIMPLEX,
		                            1,
		                            (255, 0, 0), 2, cv2.LINE_AA, False)
	
	if SHOW_POLYGONS:
		original_copy = cv2.polylines(original_copy, [inputPolygonPointsTop], True, (255, 0, 0), thickness=1)
		original_copy = cv2.polylines(original_copy, [inputPolygonPointsBottom], True, (0, 0, 255), thickness=1)
	
	if SHOW_CLUSTER_COUNTS:
		original_copy = cv2.putText(original_copy, ', ' + str(topClusters), (upperBottomRight[0] + 60, upperBottomRight[1]),
		                            cv2.FONT_HERSHEY_SIMPLEX, 1,
		                            (255, 0, 0), 2, cv2.LINE_AA, False)
		
		original_copy = cv2.putText(original_copy, ', ' + str(bottomClusters),
		                            (lowerBottomRight[0] + 60, lowerBottomRight[1]), cv2.FONT_HERSHEY_SIMPLEX,
		                            1,
		                            (255, 0, 0), 2, cv2.LINE_AA, False)
	
	if SHOW_FRAME_NUMBER:
		original_copy = cv2.putText(original_copy, str(counter), (20, 25), cv2.FONT_HERSHEY_SIMPLEX, 1,
		                            (0, 0, 0), 3, cv2.LINE_AA, False)
	
	writeImage(outputDir=outputDir, fileName=str(counter) + f'_{subCounter}_Final_Image.png',
	           image=original_copy, debugLevel=WRITE_EVENTUAL_IMAGES)
	
	return original_copy

def drawRedText(img, text, pos=(0, 0),
            font=cv2.FONT_HERSHEY_PLAIN, font_scale=3, font_thickness=4, text_color=(255, 255, 255), text_color_bg=(22, 22, 204)):
    x, y = pos
    text_size, _ = cv2.getTextSize(text, font, font_scale, font_thickness)
    text_w, text_h = text_size
    cv2.rectangle(img, (x - 4, y - 4), (int(x + text_w + 4), int(y + text_h + 4)), text_color_bg, -1)
    cv2.putText(img, text, (x, int(y + text_h + font_scale - 1)), font, font_scale, text_color, font_thickness)
    return text_size

def drawGreenText(img, text, pos=(0, 0),
            font=cv2.FONT_HERSHEY_PLAIN, font_scale=3, font_thickness=4, text_color=(255, 255, 255), text_color_bg=(22, 204, 22)):
    x, y = pos
    text_size, _ = cv2.getTextSize(text, font, font_scale, font_thickness)
    text_w, text_h = text_size
    cv2.rectangle(img, (x - 4, y - 4), (int(x + text_w + 4), int(y + text_h + 4)), text_color_bg, -1)
    cv2.putText(img, text, (x, int(y + text_h + font_scale - 1)), font, font_scale, text_color, font_thickness)
    return text_size

def setBeepNow(value: bool):
	global beepNow
	beepNow = value
	return


if __name__ == '__main__':

	'''
	topPolygonPoints = createPolygon(
		[[140, 475], [220, 475], [310, 350], [400, 295], [365, 235], [265, 300], [180, 395]])
	bottomPolygonPoints = createPolygon(
		[[220, 475], [330, 475], [380, 415], [440, 375], [400, 295], [310, 350]])
	'''
	
	topPolygonPoints = createPolygon(
		[[8, 69], [166, 57], [272, 92], [345, 150], [380, 205], [360, 280], [319, 242], [217, 212], [113, 227],
		 [7, 270]])
	bottomPolygonPoints = createPolygon(
		[[319,242], [217,212], [113, 227], [7,270], [10,470], [129,466], [241,398], [313,388]])

	# print(type(topPolygonPoints))
	
	# inspectionLinePt1 = (245, 313)
	# inspectionLinePt2 = (377, 409)
	# inspectionLine = [inspectionLinePt1, inspectionLinePt2]
	
	inputFiles = listFiles(path, False)
	currentTime = time.time_ns()
	print(f"The current time is {currentTime}")
	iterations = 1
	for file in inputFiles:
		print(f"========================================================")
		print(f"Calculating results for file {file}")
		for i in range(iterations):
			processTabletCountFromImageFile(file, topPolygonPoints, bottomPolygonPoints)
		counter += 1
	finishTime = time.time_ns()
	print(
		f"The time taken per iteration is {(finishTime - currentTime) / (iterations * 1000000 * (1 if len(inputFiles) == 0 else len(inputFiles)))} milliseconds")
