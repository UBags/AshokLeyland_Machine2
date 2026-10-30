# The MAIN program which is coordinating the whole jing-bang

import os

import imutils
from threading import Thread
import sys
import time

import video.PT
from PT import *
from operator import not_
from collections.abc import *
from video.QC import *
from processors.image.WebcamInputStream import *
from processors.image.GenericImageProcessor import *
from video.IW import *
from video.QSH import *
from video.PT import *

inputDir = '/home/uddipan/PycharmProjects/GeltecCapsules/Input'
outputDir = '/home/uddipan/PycharmProjects/GeltecCapsules/Output'
# inputVideoPath = inputDir + "/Video 1 - Copy.mp4"

# inputVideoPath = inputDir + "/output-31-10-2022-17-18-45.mp4"
# outputVideoPath = outputDir + "/Test Output Video - 1.mp4"

inputVideoPath = inputDir + "/output-31-10-2022-17-21-17.mp4"
outputVideoPath = outputDir + "/Test Output Video - 2.mp4"

playbackFPS = 10 #fps

class ParallelProcessor():

	def __init__(self, inputPathOrSource, outputFilePath: str, fps = 10, writeDebugLevel = video.PT.WRITE_NO_IMAGES, width = 640, height = 480, resizeRatio = 1.0, windowName = 'Frames'):
		threading.Thread.__init__(self)
		print(f"{datetime.now().strftime('%H:%M:%S.%f')} - [INFO] starting video file thread...")
		try:
			os.makedirs(os.path.dirname(outputFilePath))
		except:
			pass
		
		self.topPolygonPoints = createPolygon(
			[[6, 90], [180, 80], [335, 150], [420, 240], [390, 325], [290, 275], [185, 255], [10, 310]])
		self.bottomPolygonPoints = createPolygon(
			[[390, 325], [290, 275], [185, 255], [10, 310], [10,475], [210, 475], [360, 455]])

		# self.topPolygonPoints = createPolygon(
		# 	[[8, 69], [166, 57], [272, 92], [345, 150], [380, 205], [360, 280], [319, 242], [217, 212], [113, 227], [7, 270]])
		# self.bottomPolygonPoints = createPolygon(
		# 	[[319, 242], [217, 212], [113, 227], [7, 270], [10,470], [129, 466], [241, 398], [313, 388]])

		print(f'Current write debug level in PP is {writeDebugLevel}')
		'''
		# self.topPolygonPoints = createPolygon(
		# 	[[5,303], [159,197], [293,151], [438,152], [421,250], [336,250], [158,330], [64,417]])
		# self.bottomPolygonPoints = createPolygon(
		# 	[[421, 250], [336, 250], [158, 330], [64,417], [217,472], [330,406], [416,393]])
		# self.topPolygonPoints = createPolygon(
		# 	[[137, 478], [220, 478], [305, 345], [430, 280], [385, 215], [262, 297], [177, 392]])
		# self.bottomPolygonPoints = createPolygon(
		# 	[[220, 478], [325, 478], [375, 410], [470, 357], [430, 280], [305, 345]])
		# self.topPolygonPoints = createPolygon(
		# 	[[140, 475], [220, 475], [310, 350], [400, 295], [365, 235], [265, 300], [180, 395]])
		# self.bottomPolygonPoints = createPolygon(
		# 	[[220, 475], [330, 475], [380, 415], [440, 375], [400, 295], [310, 350]])
		'''
		self.windowName = windowName
		self.iw = ImageWriter(path = outputFilePath, fps = fps, width = width, height = height)
		self.iw.start()
		time.sleep(1.0)
		self.id = ImageDisplayer(consumer=self.iw, resizeRatio=resizeRatio)
		self.id.start()
		time.sleep(1.0)
		# self.ip = GenericImageProcessor(transform=processTabletCountFromImage_ChangeTabletColours, topPolygonPoints = self.topPolygonPoints, bottomPolygonPoints = self.bottomPolygonPoints, transformedImageConsumer=self.id, writeDebugLevel=writeDebugLevel)
		self.ip = GenericImageProcessor(transform=processTabletCountFromImage_WritePassFail, topPolygonPoints = self.topPolygonPoints, bottomPolygonPoints = self.bottomPolygonPoints, transformedImageConsumer=self.id, writeDebugLevel=writeDebugLevel)
		self.ip.start()
		time.sleep(1.0)
		# self.makeDisplayWindow(self.windowName)
		# time.sleep(1.0)
		self.iis = WebcamInputStream(path = inputPathOrSource, imageTransformingConsumer=self.ip, fps = fps)
		self.qsh = QuitSignalHolder(self.iis, self.ip, self.id, self.iw)
		self.id.addQuitSignalHolder(self.qsh)
		self.iis.start()
		# time.sleep(1.0)

	def makeDisplayWindow(self, name = 'Frames'):
		cv2.namedWindow(name)
		cv2.moveWindow(name, 40, 30)

ParallelProcessor(inputVideoPath, outputVideoPath, fps = 18, writeDebugLevel=video.PT.WRITE_NO_IMAGES)