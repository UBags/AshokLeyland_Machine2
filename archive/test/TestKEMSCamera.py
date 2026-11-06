# # import requests
# # import zeep
# # from zeep import Client
# # from requests import Session
# # from requests.auth import HTTPBasicAuth, HTTPDigestAuth
# # from zeep.transports import Transport
# # from zeep.exceptions import Fault
# # import operator
# # import onvif2
# # from onvif2 import client
# # from onvif2.client import ONVIFCamera, ONVIFService
# # from pprint import pprint
# # from datetime import datetime
# # import pytz
# # import urllib
# # from urllib import *
# # import cv2
# # import numpy as np
# # import socket
# # from urllib.parse import urlparse, parse_qs
# # import time
# # import zeep.helpers as helpers
# # from datetime import datetime
# # import time
# # import redis
# #
# # import argparse
# # import imutils
# # import cv2
# # from threading import Thread
# # import sys
# # from queue import Queue
# # import time
# # from operator import not_
# # from collections.abc import *
# # from typing import *
#
# import requests
# import threading
# from threading import Thread
# from datetime import datetime
# import zeep
# from requests.auth import HTTPBasicAuth, HTTPDigestAuth
# import operator
#
# from zeep import Client
# from requests import Session
# from zeep.transports import Transport
# from zeep.exceptions import Fault
# import operator
# from onvif import ONVIFCamera, ONVIFService
# import urllib
# import socket
# from urllib.parse import urlparse, parse_qs
# import time
# import cv2
# import numpy as np
# import traceback
#
# import warnings
# warnings.filterwarnings('ignore', '.*h264.*', )
#
# class ONVIFCam():
#
#     def __init__(self, IP : str, port : int, uid : str, password : str, wsdl_dir : str, name : str = "aCamera"):
#         try:
#             self.IP = IP
#             self.port = port
#             self.uid = uid
#             self.password = password
#             self.wsdl_dir = wsdl_dir
#             self.name = name
#             self.authorisation = HTTPDigestAuth(self.uid, self.password)
#
#             self.viableMediaProfiles = []
#             self.hdConfigurations = []
#             self.ptzPresets = []
#
#             self.camera = None
#             self.media_service = None
#             self.configurations = None
#             self.profiles = None
#
#             self.imageUri1 = None
#             self.request1 = None
#             self.url_response1 = None
#             self.imageUri2 = None
#             self.request2 = None
#             self.url_response2 = None
#
#             self.streamSetup1 = None
#             self.streamSetup2 = None
#             self.video1 = None
#             self.video2 = None
#             self.videoUri1 = None
#             self.videoUri2 = None
#             self.cam1 = None
#             self.cam2 = None
#             self.cam1Available = True
#             self.cam2Available = True
#
#             self.config = None
#             self.config_dict = None
#             self.metadataConfig = None
#             self.metadataConfig_dict = None
#
#             self.eventService = None
#             self.eventProperties = None
#
#             self.ptz_supported : bool = False
#             self.ptzService = None
#             self.ptzConfigs = None
#             self.aPTZConfig = None
#             self.ptzConfigOptions = None
#             self.ptzNodes = None
#             self.ptzServiceCapabilities = None
#             self.definedPtzPresets = None
#
#             self.camera = ONVIFCamera(self.IP, self.port, self.uid, self.password, self.wsdl_dir)
#             # printBoldBlue(f"ONVIF camera object is {self.camera}")
#             self.configureAndCreateCameraParameters()
#         except Exception as e:
#             raise e
#
#     def getIP_Path_Params_Query_Fragments(self, url):
#         parsed = urlparse(url)
#         params = parse_qs(parsed.query)
#         path_components = list(filter(bool, parsed.path.split('/')))
#         IP = parsed.scheme + '://' + parsed.netloc
#         Path = parsed.path
#         Query = parsed.query
#         Fragments = parse_qs(parsed.fragment)
#         # print(path_components)
#         # print(parsed.netloc)
#         # print(socket.gethostbyname(parsed.netloc))
#         return IP, Path, params, Query, Fragments
#
#     def closeClient(self, client3):
#         client3.transport.session.close()
#
#     def createMediaService(self):
#         try:
#             # printBoldBlue("Entered createMediaService()")
#             self.camera.devicemgmt.GetServices(False)
#             self.media_service = self.camera.create_media_service()
#         except Exception as e:
#             raise e
#
#
#     def getConfig(self):
#         try:
#             self.config = self.camera.media.GetMetadataConfigurations()[0]
#             self.config.Analytics = True
#             # self.config_dict = helpers.serialize_object(self.config, dict)
#         except Exception as e:
#             raise e
#
#     def getMetadataConfig(self):
#         try:
#             self.metadataConfig = self.camera.media.create_type('SetMetadataConfiguration')
#             self.metadataConfig.Configuration = self.config
#             self.metadataConfig.ForcePersistence = True
#             # self.metadataConfig_dict = helpers.serialize_object(self.metadataConfig, dict)
#             self.camera.media.SetMetadataConfiguration(self.metadataConfig)
#         except Exception as e:
#             raise e
#
#
#     def getProfiles(self):
#         try:
#             self.profiles = self.media_service.GetProfiles()
#             for profile in self.profiles:
#                 self.viableMediaProfiles.append(profile)
#         except Exception as e:
#             raise e
#
#     def getSnapShotDetails(self):
#         try:
#             # in this loop, set imageUri1, request1, url_response1 and imageUri2, request2, url_response2
#             for i in range(1,3):
#                 attr_name = f"imageUri{i}"
#                 setattr(self, attr_name, self.media_service.GetSnapshotUri(self.viableMediaProfiles[i-1].token))
#                 currentURI = getattr(self, attr_name).Uri
#                 # printBoldBlue(f"Snapshot{i} : {currentURI}")
#                 IP, _, _, _, _ = self.getIP_Path_Params_Query_Fragments(getattr(self, attr_name).Uri)
#                 password_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
#                 password_mgr.add_password(None, self.IP, self.uid, self.password)
#                 handler1 = urllib.request.HTTPBasicAuthHandler(password_mgr)
#                 handler2 = urllib.request.HTTPDigestAuthHandler(password_mgr)
#                 opener = urllib.request.build_opener(handler1, handler2)
#                 urllib.request.install_opener(opener)
#                 timeout = 10
#                 socket.setdefaulttimeout(timeout)
#                 attr_name1 = f"request{i}"
#                 setattr(self, attr_name1, urllib.request.Request(getattr(self, attr_name).Uri))
#                 requestStr = getattr(self, attr_name1)
#                 # printBoldBlue(f"requestStr : {requestStr}")
#                 attr_name2 = f"url_response{i}"
#                 setattr(self, attr_name2, urllib.request.urlopen(getattr(self, attr_name1)))
#                 responseObj = getattr(self, attr_name2)
#                 # printBoldBlue(f"requestStr : {type(responseObj)}")
#         except Exception as e:
#             raise e
#
#     def getHDConfigurations(self):
#         try:
#             ## get video info , 'h265' or 'h264', 'width' 'height' 'gop' ....
#             self.configurations = self.media_service.GetVideoEncoderConfigurations()
#
#             for configuration in self.configurations:
#                 if configuration['Encoding'].lower() == 'h264' or configuration['Encoding'].lower() == 'h265':
#                     width = configuration['Resolution']['Width']
#                     height = configuration['Resolution']['Height']
#                     currentEncoding = configuration['Encoding']
#                     dic = {'token': configuration['token'],
#                            'encoding': configuration['Encoding'],
#                            'ratio': "{}*{}".format(width, height),
#                            'fps': configuration['RateControl']['FrameRateLimit'],
#                            'bitrate': configuration['RateControl']['BitrateLimit'],
#                            'gop': configuration[currentEncoding]['GovLength'],
#                            # 'profile': configuration['Profile'],
#                            'name': configuration['Name'],
#                            'quality': configuration['Quality']}
#                     self.hdConfigurations.append(dic)
#                 else:
#                     dic = {'token': configuration['Name'], 'encoding': configuration['Encoding']}
#         except Exception as e:
#             raise e
#
#
#     #  ***********************************************************************
#
#     def getVideoStreamDetails(self):
#         try:
#             self.streamSetup1 = {
#                 'StreamSetup': {
#                     'Stream': 'RTP-Unicast',
#                     'Transport': {
#                         'Protocol': 'TCP'
#                     }
#                 },
#                 'ProfileToken': self.viableMediaProfiles[0].token
#             }
#
#             self.streamSetup2 = {
#                 'StreamSetup': {
#                     'Stream': 'RTP-Unicast',
#                     'Transport': {
#                         'Protocol': 'TCP'
#                     }
#                 },
#                 'ProfileToken': self.viableMediaProfiles[1].token
#             }
#
#             self.video1 = self.camera.media.GetStreamUri(self.streamSetup1)
#             self.video2 = self.camera.media.GetStreamUri(self.streamSetup2)
#             self.videoUri1 = self.video1.Uri.replace('rtsp://', 'rtsp://{}:{}@'.format(self.uid,self.password), 1)
#             self.videoUri2 = self.video2.Uri.replace('rtsp://', 'rtsp://{}:{}@'.format(self.uid, self.password), 1)
#             self.renewCam(1)
#             self.renewCam(2)
#         except Exception as e:
#             raise e
#
#     def renewCam(self, source : int = 1) -> None:
#         if (source == 2):
#             self.cam2Available = False
#             if self.cam2 is not None:
#                 try:
#                     self.cam2.release()
#                 except:
#                     pass
#             try:
#                 self.cam2 = cv2.VideoCapture(self.videoUri2)
#                 self.cam2Available = True
#             except:
#                 pass
#             return None
#         else:
#             self.cam1Available = False
#             if self.cam1 is not None:
#                 try:
#                     self.cam1.release()
#                 except:
#                     pass
#             try:
#                 self.cam1 = cv2.VideoCapture(self.videoUri1)
#                 self.cam1Available = True
#             except:
#                 pass
#             return None
#         return None
#
#     def launchRenewThread(self, source : int = 1):
#         self.renewalThread = threading.Thread(name=f'Cam Renewal Thread - VideoCam.{self.name}',
#                                                       target=self.renewCam,
#                                                       args=(source,), daemon=True)
#         self.renewalThread.start()
#         self.renewalThread.join()
#         return
#
#     def getFrame(self, source : int = 1):
#         if not self.cam2Available and not self.cam1Available:
#             self.getVideoStreamDetails()
#         if source == 2:
#             if (self.cam2 is None) or (not self.cam2Available):
#                 return False, None
#             if self.cam2Available:
#                 ret, frame = self.cam2.read()
#                 return ret, frame
#             else:
#                 return False, None
#         else:
#             if (self.cam1 is None) or (not self.cam1Available):
#                 return False, None
#             if self.cam1Available:
#                 ret, frame = self.cam1.read()
#                 return ret, frame
#             else:
#                 return False, None
#         return False, None
#
#     #  ********************************************************************
#
#     # *********************************************************************
#     def getPicture(self, source : int = 1):
#         if (source == 2):
#             try:
#                 self.refreshPictureResponseURL(source)
#                 img_array = np.asarray(bytearray(self.url_response2.read()), dtype=np.uint8)
#                 img = cv2.imdecode(img_array, -1)  # 'Load it as it is'
#                 return img
#             except:
#                 self.configureAndCreateCameraParameters()
#         else:
#             try:
#                 self.refreshPictureResponseURL(source)
#                 img_array = np.asarray(bytearray(self.url_response1.read()), dtype=np.uint8)
#                 img = cv2.imdecode(img_array, -1)  # 'Load it as it is'
#                 return img
#             except:
#                 self.configureAndCreateCameraParameters()
#         return None
#
#     def refreshPictureResponseURL(self, source = 1):
#         try:
#             attr_name1 = f"request{source}"
#             attr_name2 = f"url_response{source}"
#             setattr(self, attr_name2, urllib.request.urlopen(getattr(self, attr_name1)))
#         except:
#             pass
#
#     # **********************
#
#     # **********************
#
#     def getPicture_Alternate(self, source : int = 1):
#         try:
#             rawPic = self.getRawPicture(source)
#             img_array = np.asarray(bytearray(rawPic), dtype=np.uint8)
#             img = cv2.imdecode(img_array, -1)
#             return img
#         except:
#             return None
#         return None
#
#     def getRawPicture(self, source : int = 1):
#
#         if (source == 2):
#             try:
#                 url_response = requests.get(self.imageUri2.Uri, auth=self.authorisation)
#                 content = url_response.content
#                 return content
#             except:
#                 self.configureAndCreateCameraParameters()
#         else:
#             try:
#                 url_response = requests.get(self.imageUri1.Uri, auth=self.authorisation)
#                 content = url_response.content
#                 return content
#             except:
#                 self.configureAndCreateCameraParameters()
#         return None
#
#     # **********************
#
#     def releaseResources(self):
#         if hasattr(self,"cam1"):
#             if self.cam1 is not None:
#                 try:
#                     self.cam1.release()
#                     self.cam1 = None
#                     self.cam1Available = False
#                 except:
#                     pass
#         if hasattr(self,"cam2"):
#             if self.cam2 is not None:
#                 try:
#                     self.cam2.release()
#                     self.cam2 = None
#                     self.cam2Available = False
#                 except:
#                     pass
#
#     def configureAndCreateCameraParameters(self):
#         '''
#         A media profile consists of configuration entities such as video/audio
#         source configuration, video/audio encoder configuration,
#         or PTZ configuration. This use case describes how to change one
#         configuration entity which has been already added to the media profile.
#         '''
#
#         # if debugCV2Info:
#         #     printBlue(cv2.getBuildInformation())
#
#         self.createMediaService()
#         # printBoldBlue("Finished media service, and now entering config")
#         self.getConfig()
#         # printBoldBlue("Finished config, and now entering metadataconfig")
#         self.getMetadataConfig()
#         self.getProfiles()
#         # printBoldBlue("Reached getSnapShotDetails()")
#         self.getSnapShotDetails()
#         self.getHDConfigurations()
#         self.getVideoStreamDetails()
#
# cam = ONVIFCam("169.254.29.130", 80, "admin", "abcd1234", "D:/KEMS_CotterPin_GC_QRC_T/Lib/site-packages/wsdl")
# print(cam)
#
# print(f"Starting")
# currTime = time.time()*1000
# r = 1000
# cutoff = 100
# greenCount = 0
# blueCount = 0
# yellowCount = 0
# redCount = 0
# greenCutoff = 50
# blueCutoff = 75
# yellowCutoff = 100
# for l in range(r):
#     startTime = time.time()*1000
#     pic = cam.getRawPicture((l % 2) + 1)
#     endTime = time.time()*1000
#     duration = endTime-startTime
#     if duration > yellowCutoff:
#         redCount += 1
#         print(f"{l} : {type(pic)} - {duration}")
#     elif duration > blueCutoff:
#         yellowCount += 1
#         print(f"{l} : {type(pic)} - {duration}")
#     elif duration > greenCutoff:
#         blueCount += 1
#         print(f"{l} : {type(pic)} - {duration}")
#     else:
#         greenCount += 1
# print(f"Green count: {greenCount}; Blue count : {blueCount}; Yellow count : {yellowCount}; Red count : {redCount}")
# endT = time.time()*1000
# print(f"Ending - got {r} frames @ {round((endT - currTime) / r, 2)} ms per pic")
#
