import threading

import ffmpeg
import operator
from pprint import pprint
from datetime import datetime
import cv2
import numpy as np
import socket
from urllib.parse import urlparse, parse_qs
import time
from datetime import datetime
import time
import redis

import argparse
import imutils
import cv2
from threading import Thread, ThreadError
import sys
from queue import Queue
import time
from operator import not_
from collections.abc import *
from typing import *
import copy

from logutils.SlaveLoggers import *
from logutils.Logger import *
from logutils.AbstractSlaveLogger import *
from utils.RedisUtils import *
from utils.CosThetaPrintUtils import *
from utils.IPUtils import *

import warnings
warnings.filterwarnings('ignore', '.*h264.*', )

from Configuration import *
CosThetaConfigurator.getInstance()

'''
0) Install Hikvision Tools Manager
1) *****Install SADP***** and set password as admin and admin. Enable 'Hik-vision Connect' and 'port'
2) Double-click on SADP row to open a browser and see the camera output
3) Check maikl for documeht 'Hikvision PZTZ settings'


a) Go to Configurations -> Network -> Integration Protocol. Enable ONVIF. Then, create a newwidgets user with admin and abcd1234
b) Go to that part which is creating a problem and overwrite the throws clause with 'returns xmlvalue'
c) Clone the github in a directory and install on the machine using 'python setup.py install'. 
   This will ensure that the wsdls are installed in the correct directory and available to all the calls 
d) When you create a project, ensure that the venv has both options ticked for 'inherit global site-packages' and 'make available to all projects'   
'''

class RTSPCam():

    logSource = getFullyQualifiedName(__file__)
    LOG_CONNECTION_PROBLEM_EVERY_N_SECS = CosThetaConfigurator.getInstance().getLogDisconnectionsAfterNSecs()

    def __init__(self, IP : str, port : int, uid : str, password : str, name : str = "aCamera"):
        try:
            self.IP = IP
            self.port = port
            self.uid = uid
            self.password = password
            self.name = name
            # self.authorisation = HTTPDigestAuth(self.uid, self.password)
            self.connectionAttempts : int = 0

            self.videoUri1 = f"rtsp://{self.uid}:{self.password}@{self.IP}/cam/realmonitor?channel=1&subtype=0"
            self.camProcess = None
            self.camAvailable = False

            self.stopped = False
            self.stopFrameUpdateThread = False
            self.maxAllowedNoneFrameCountsInCamera = CosThetaConfigurator.getInstance().getMaxAllowedNoneFrameCountsInCamera()

            self.latestCamFrame: Union[None | np.ndarray] = None

            self.width = 0
            self.height = 0

            RTSPCam.logSource = getFullyQualifiedName(__file__, __class__)
            self.connected = False
            self.connectionInProgress = False
            self.connectionMonitoringThreadStarted : bool = False
            # printLight("Reached 1")
            self.reconnect()

        except Exception as e:
            SlaveFileLogger.getInstance().logTakeAction(RTSPCam.logSource,
                                                      f"In RTSPCam.{self.name}, unable to create RTSPCamera due to {e}",
                                                      Logger.PROBLEM)
            SlaveConsoleLogger.getInstance().logTakeAction(RTSPCam.logSource,
                                                         f"In RTSPCam.{self.name}, unable to create RTSPCamera due to {e}",
                                                         Logger.PROBLEM)
            # raise e

    def stop(self):
        self.stopped = True
        self.stopFrameUpdateThread = True
        self.releaseResources()

    def reconnect(self):
        # printLight(f"Entered reconnect")
        if self.connectionInProgress:
            print(f"As {self.connectionInProgress = }, already attempting to reconnect. Hence, exiting")
            return
        # else:
        #     # printBoldBlue(f"{self.attemptingToReconnect = }")
        #     pass
        self.connectionInProgress = True
        self.connectionAttempts += 1
        if (self.connectionAttempts == 1) or ((self.connectionAttempts % RTSPCam.LOG_CONNECTION_PROBLEM_EVERY_N_SECS) == 0):
            # printLight(f"Trying to create camera with {self.IP}, {self.port}, {self.uid}, {self.password}")
            SlaveFileLogger.getInstance().logTakeNote(RTSPCam.logSource,
                                                      f"Trying to create camera with {self.IP}, {self.port}, {self.uid}, {self.password}",
                                                      Logger.INFO)
            SlaveConsoleLogger.getInstance().logTakeNote(RTSPCam.logSource,
                                                         f"Trying to create camera with {self.IP}, {self.port}, {self.uid}, {self.password}",
                                                         Logger.INFO)
            # printBoldGreen(f"Reconnecting to camera at IP {self.IP}")
        camIPAvailable = checkIP(self.IP)
        # printLight(f"Pinged IP {self.IP}; it is {'reachable' if camIPAvailable else 'not reachable'}")
        if not camIPAvailable:
            try:
                if self.camProcess is not None:
                    self.camProcess.kill()
                    self.camProcess = None
                    self.stopFrameUpdateThread = True
            except:
                self.camProcess = None
                self.stopFrameUpdateThread = True
            self.connected = False
            self.camAvailable = False
            if (self.connectionAttempts == 1) or (
                    (self.connectionAttempts % RTSPCam.LOG_CONNECTION_PROBLEM_EVERY_N_SECS) == 0):
                SlaveFileLogger.getInstance().logTakeAction(RTSPCam.logSource,
                                                      f"In RTSPCam.{self.name}, could not connect to camera at IP {self.IP} - IP not reachable",
                                                      Logger.PROBLEM)
                SlaveConsoleLogger.getInstance().logTakeAction(RTSPCam.logSource,
                                                             f"In RTSPCam.{self.name}, could not connect to camera at IP {self.IP} - IP not reachable",
                                                             Logger.PROBLEM)
            self.connectionInProgress = False
            # if (self.connectionAttempts == 1) or (
            #         (self.connectionAttempts % RTSPCam.LOG_CONNECTION_PROBLEM_EVERY_N_SECS) == 0):
            #     printBoldRed(f"No camera available at IP {self.IP} - IP not reachable - exiting reconnect() after setting {self.connectionInProgress = }")
            # raise Exception(f"No camera available at IP {self.IP} - IP not reachable")
        else:
            try:
                # printBoldBlue(f"As IP {self.IP} is reachable, attempting to connect.")
                try:
                    if self.camProcess is not None:
                        printBoldRed(f"About to kill camProcess() as it is not None")
                        self.camProcess.kill()
                        self.camProcess = None
                        self.stopFrameUpdateThread = True
                except:
                    self.camProcess = None
                    self.stopFrameUpdateThread = True
                self.connected = False
                self.camAvailable = False
                # SlaveFileLogger.getInstance().logDebug(RTSPCam.logSource,
                #                                           f"In RTSPCam.{self.name}, about to create stream at IP {self.IP}",
                #                                           Logger.SUCCESS)
                # SlaveConsoleLogger.getInstance().logDebug(RTSPCam.logSource,
                #                                              f"In RTSPCam.{self.name}, about to create stream at IP {self.IP}",
                #                                              Logger.SUCCESS)
                if not self.stopped:
                    self.renewCam()
                    # printBoldBlue(f"In RTSPCam.{self.name}, about to launch frame update thread")
                    # self.connected = True
                    # printBoldBlue(f"In RTSPCam.{self.name}, created camera at IP {self.IP}")
                if self.connected:
                    SlaveFileLogger.getInstance().logDebug(RTSPCam.logSource,
                                                              f"In RTSPCam.{self.name}, created camera and connected to camera at IP {self.IP}",
                                                              Logger.SUCCESS)
                    SlaveConsoleLogger.getInstance().logDebug(RTSPCam.logSource,
                                                                 f"In RTSPCam.{self.name}, created camera  and connected to camera at IP {self.IP}",
                                                                 Logger.SUCCESS)
                    printBoldGreen(f"Connected to camera at {self.IP}")
            except Exception as e:
                printBoldRed(f"Could not create camera due to {e}")
                self.camera = None
                try:
                    if self.camProcess is not None:
                        self.camProcess.kill()
                        self.camProcess = None
                        self.stopFrameUpdateThread = True
                except:
                    self.camProcess = None
                    self.stopFrameUpdateThread = True
                self.connected = False
                self.camAvailable = False
                if (self.connectionAttempts == 1) or (
                        (self.connectionAttempts % RTSPCam.LOG_CONNECTION_PROBLEM_EVERY_N_SECS) == 0):
                    SlaveFileLogger.getInstance().logTakeAction(RTSPCam.logSource,
                                                          f"In RTSPCam.{self.name}, could not connect to camera at IP {self.IP}, though IP is reachable, due to {str(e)}",
                                                          Logger.PROBLEM)
                    SlaveConsoleLogger.getInstance().logTakeAction(RTSPCam.logSource,
                                                                 f"In RTSPCam.{self.name}, could not connect to camera at IP {self.IP}, though IP is reachable, due to {str(e)}",
                                                                 Logger.PROBLEM)
                # printBoldRed(f"No camera available at IP {self.IP}, though IP is reachable")
                self.connectionInProgress = False
                if (self.connectionAttempts == 1) or (
                        (self.connectionAttempts % RTSPCam.LOG_CONNECTION_PROBLEM_EVERY_N_SECS) == 0):
                    printBoldRed(f"Could not create camera though {self.IP} is reachable - exiting reconnect() after setting {self.connectionInProgress = }")
                return
                # raise Exception(f"No camera available at IP {self.IP}, though IP is reachable")
        if self.connected:
            self.stopFrameUpdateThread = False
            self.launchFrameUpdateThread()
        if not self.connectionMonitoringThreadStarted:
            # printBoldBlue(f"In RTSPCam.{self.name}, about to launch camera connection monitoring thread")
            self.launchMonitorCameraConnectionThread()
        self.connectionInProgress = False
        # printBoldGreen(
        #     f"Exiting reconnect() after setting {self.attemptingToReconnect = }")

    def renewCam(self) -> None:
        # printBoldYellow(f"Inside renewCam()")
        self.camAvailable = False
        # print(f"Setting {self.camAvailable = } in renewCam()")
        if self.camProcess is not None:
            try:
                self.camProcess.kill()
                self.camProcess = None
                self.stopFrameUpdateThread = True
            except:
                self.camProcess = None
                self.stopFrameUpdateThread = True
                # pass
        try:
            self.camProcess = self.startFFMPEG_RTSP_Process()
            if self.camProcess is not None:
                self.camAvailable = True
                self.stopFrameUpdateThread = False
                self.connected = True
                self.connectionAttempts = 0
        except:
            self.camProcess = None
            self.camAvailable = False
            self.stopFrameUpdateThread = True
            self.connected = False
            # print(f"Setting {self.camAvailable = } in renewCam()")
            pass
        # printBoldYellow(f"Exiting renewCam() with {self.camAvailable = }")
        return None

    def startFFMPEG_RTSP_Process(self):
        try:
            # args = {
            #     "rtsp_transport": "tcp",
            #     "fflags": "nobuffer",
            #     "fflags": "discardcorrupt",
            #     "fflags": "flush_packets",
            #     "flags": "low_delay",
            #     "thread_queue_size": "2"
            #     # "loglevel": "quiet",
            #     # "nostats": ""
            # }
            # printBoldBlue(f"In startFFMPEG_RTSP_Process()")
            args = {
                "rtsp_transport": "tcp",
                "fflags": "nobuffer",
                "flags": "low_delay"
            }
            probe = ffmpeg.probe(self.videoUri1)
            cap_info = next(x for x in probe['streams'] if x['codec_type'] == 'video')
            # print("fps: {}".format(cap_info['r_frame_rate']))
            self.width = cap_info['width']
            self.height = cap_info['height']
            up, down = str(cap_info['r_frame_rate']).split('/')
            self.fps = eval(up) / eval(down)
            self.timeRequiredBetweenFrames = 1.0 / self.fps
            printBoldBlue(f"fps: {self.fps}")
            cameraProcess = (
                ffmpeg
                .input(self.videoUri1, **args)
                # .output('pipe:', format='rawvideo', pix_fmt='rgb24')
                # .output('pipe:', format='rawvideo', pix_fmt='yuv420p')
                .output(
                    'pipe:',
                    format='rawvideo',
                    pix_fmt='bgr24',
                    fflags='nobuffer',
                    analyzeduration='1',
                    loglevel='error'  # <-- This silences ffmpeg progress
                )
                .overwrite_output()
                # .run_async(pipe_stdout=True, pipe_stderr=subprocess.DEVNULL)
                .run_async(pipe_stdout=True)
            )
            SlaveFileLogger.getInstance().logTakeNote(RTSPCam.logSource,
                                                      f"In RTSPCam.{self.name}, got FFMPEG process",
                                                      Logger.SUCCESS)
            SlaveConsoleLogger.getInstance().logTakeNote(RTSPCam.logSource,
                                                      f"In RTSPCam.{self.name}, got FFMPEG process",
                                                      Logger.SUCCESS)
            # printBoldGreen(f"Created FFMPEG process at IP {self.IP}")
            return cameraProcess
        except Exception as e:
            SlaveFileLogger.getInstance().logTakeAction(RTSPCam.logSource,
                                                      f"In RTSPCam.{self.name}, unable to create FFMPEG process due to {e}",
                                                      Logger.PROBLEM)
            SlaveConsoleLogger.getInstance().logTakeAction(RTSPCam.logSource,
                                                         f"In RTSPCam.{self.name}, unable to create FFMPEG process due to {e}",
                                                         Logger.PROBLEM)
            printBoldRed(f"Could not create FFMPEG process at IP {self.IP} due to {e}")

    def launchFrameUpdateThread(self):
        objRef = self
        def updateFrame():
            # printBoldYellow(f"Inside updateFrame of launchFrameUpdateThread(): {objRef.stopped = }; {objRef.camAvailable = }; {objRef.connected = }")
            consecutiveCount = 0
            frameCount = 0
            while (not objRef.stopped) and objRef.camAvailable and objRef.connected and (not objRef.stopFrameUpdateThread):
                # printBoldYellow(f"Loop {frameCount+1}")
                # startTime = time.time()
                try:
                    in_bytes = objRef.camProcess.stdout.read(objRef.width * objRef.height * 3)
                    if not in_bytes:
                        consecutiveCount += 1
                        if consecutiveCount > self. maxAllowedNoneFrameCountsInCamera:
                            objRef.stopFrameUpdateThread = True
                            # break
                    else:
                        consecutiveCount = 0
                        frameCount += 1
                        # ndarray
                        # in_frame = (
                        frame : Union [None | np.ndarray] = (
                            np
                            .frombuffer(in_bytes, np.uint8)
                            .reshape([objRef.height, objRef.width, 3])
                        )
                        # frame = cv2.resize(in_frame, (1280, 720))
                        # frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR) # removed because pix_fmt changed to bgr24
                        if frame is not None:
                            # objRef.latestCamFrame = copy.deepcopy(frame)
                            objRef.latestCamFrame = frame
                        else:
                            objRef.latestCamFrame = None
                        if frameCount % 4000 == 0:
                            # printBoldGreen(f"Got frame {frameCount}")
                            SlaveConsoleLogger.getInstance().logConsiderAction(RTSPCam.logSource,
                                                                         f"In RTSPCam.{self.name}, got frame {frameCount}",
                                                                         Logger.SUCCESS)
                        if frameCount % 1000 == 0:
                            # printBoldGreen(f"Got frame {frameCount}")
                            SlaveFileLogger.getInstance().logDebug(RTSPCam.logSource,
                                                                         f"In RTSPCam.{self.name}, got frame {frameCount}",
                                                                         Logger.SUCCESS)
                        # time.sleep(0.02)
                        # time.sleep() is not needed because this loop takes 40 ms to execute, which brings it to the next frame anyhow
                        # cv2.imshow(frameName, frame)
                        # if cv2.waitKey(1) == ord('q'):
                            #     break
                        # print(f"Time taken = {(endTime - startTime) * 1000} ms")
                except:
                    consecutiveCount += 1
                    if consecutiveCount > self.maxAllowedNoneFrameCountsInCamera:
                        objRef.stopFrameUpdateThread = True
                        # break

                # NOTE: Somehow, making this thread sleep doesn't work. It makes the camera process hang.
                # NOTE: On the other hand, not making this thread sleep ensure perfect working.
                # NOTE: Not sure why this happens, but this is the observation. So, let's stick with it

                # endTime = time.time()
                # timeTaken = endTime - startTime
                # sleepTime = self.timeRequiredBetweenFrames - timeTaken
                # if sleepTime > 0:
                #     try:
                #         time.sleep(sleepTime)
                #     except:
                #         pass

            objRef.camAvailable = False
            objRef.connected = False
            # printBoldRed(f"Exiting launchFrameUpdateThread(): {objRef.stopped = }; {objRef.camAvailable = }; {objRef.connected = }")
            # printBoldRed(f"Setting {objRef.camAvailable = } in launchFrameUpdateThread()")
            # printBoldRed(f"Setting {objRef.connected = } in launchFrameUpdateThread()")
            # printBoldRed(f"Setting {objRef.camProcess = } in launchFrameUpdateThread()")
            if objRef.camProcess is not None:
                try:
                    objRef.camProcess.kill()
                    objRef.camProcess = None
                except:
                    objRef.camProcess = None
        updateThread = threading.Thread(name=f"Frame update thread for camera at {self.IP}", target=updateFrame, args=(), daemon = True)
        # printBold("About to start frame update thread")
        SlaveFileLogger.getInstance().logTakeNote(RTSPCam.logSource,
                                                    f"In RTSPCam.{self.name}, about to start frame update thread",
                                                    Logger.SUCCESS)
        # SlaveConsoleLogger.getInstance().logTakeNote(RTSPCam.logSource,
        #                                             f"In RTSPCam.{self.name}, about to start frame update thread",
        #                                             Logger.SUCCESS)
        updateThread.start()

    def launchMonitorCameraConnectionThread(self):
        objRef = self
        def monitorAndReEstablishConnection():
            # printBoldYellow(f"Inside launchMonitorCameraConnectionThread() : {objRef.stopped = }; {objRef.camAvailable = }; {objRef.connected = }")
            while (not objRef.stopped):
                startTime = time.time()
                if not objRef.connectionInProgress:
                    if not objRef.connected:
                        try:
                            objRef.releaseResources()
                        except:
                            pass
                        objRef.reconnect()
                endTime = time.time()
                sleepTime = 1.0 - (endTime - startTime)
                if sleepTime > 0:
                    time.sleep(sleepTime)
            # printBoldRed(f"Exiting launchMonitorCameraConnectionThread() : {objRef.stopped = }; {objRef.camAvailable = }; {objRef.connected = }")

        connectionMonitoringThread = threading.Thread(name=f"Monitoring and Re-establishing camera connection at {self.IP}", target=monitorAndReEstablishConnection, args=(), daemon = True)
        # printBold("About to start camera connection monitoring thread")
        if (objRef.connectionAttempts == 1) or (
                (objRef.connectionAttempts % RTSPCam.LOG_CONNECTION_PROBLEM_EVERY_N_SECS) == 0):
            SlaveFileLogger.getInstance().logTakeNote(RTSPCam.logSource,
                                                        f"In RTSPCam.{self.name}, about to start camera connection monitoring thread",
                                                        Logger.INFO)
            SlaveConsoleLogger.getInstance().logTakeNote(RTSPCam.logSource,
                                                        f"In RTSPCam.{self.name}, about to start camera connection monitoring thread",
                                                        Logger.INFO)
        self.connectionMonitoringThreadStarted = True
        connectionMonitoringThread.start()

    def getLatestFrame(self) -> Tuple[bool, Union[None, np.ndarray]]:
        if not self.camAvailable:
            # printBoldRed(f"No frame returned because {self.camAvailable = }")
            return False, None
        if self.latestCamFrame is not None:
            frameCopy = copy.deepcopy(self.latestCamFrame)
            return True, frameCopy
        return False, None

    def isConnected(self) -> bool:
        if self.connected:
            return True
        return False

    def releaseResources(self):
        if hasattr(self,"camProcess"):
            if self.camProcess is not None:
                try:
                    self.camProcess.kill()
                except:
                    pass
            self.camProcess = None
        self.camAvailable = False
        self.connected = False
        self.stopFrameUpdateThread = True
        if (self.connectionAttempts == 1) or (
                (self.connectionAttempts % RTSPCam.LOG_CONNECTION_PROBLEM_EVERY_N_SECS) == 0):
            SlaveFileLogger.getInstance().logTakeNote(RTSPCam.logSource,
                                                        f"In RTSPCam.{self.name}, killed frame update thread",
                                                        Logger.INFO)
            # SlaveConsoleLogger.getInstance().logTakeNote(RTSPCam.logSource,
            #                                             f"In RTSPCam.{self.name}, killed frame update thread",
            #                                             Logger.INFO)
        # printBoldBlue(f"Setting {self.camAvailable = } and {self.connected = } in releaseResources()")


# TESTING RTSPCam

# printBoldGreen(f"Starting")
# # camera = RTSPCam("192.168.1.100", 80, "admin", "abcd1234", "Camera 1")
# camera = RTSPCam("192.168.1.64", 80, "admin", "Leyland9753", "Camera 1")
# printBoldGreen(camera)
# startTime = time.time()
# repeats=100000
# try:
#     # for i in range(repeats):
#     while True:
#         try:
#             frameGot, actualFrame  = camera.getLatestFrame()
#             if actualFrame is not None:
#                 # print(actualFrame.shape)
#                 cv2.imshow("Frame", actualFrame)
#                 if cv2.waitKey(1) == ord('q'):
#                     break
#         except:
#             pass
#         time.sleep(0.05)
# except Exception as e:
#     printBoldRed(e)
#
# printBoldRed(f"About to call camera.releaseResources()")
# camera.stop()
# endTime = time.time()
# print(f"{(endTime - startTime)*1000/repeats} ms")
