# import threading
#
# import ffmpeg
# import requests
# import zeep
# from zeep import Client
# from requests import Session
# from requests.auth import HTTPBasicAuth, HTTPDigestAuth
# from zeep.transports import Transport
# from zeep.exceptions import Fault
# import operator
# import onvif2
# from onvif2 import client
# from onvif2.client import ONVIFCamera, ONVIFService
# from pprint import pprint
# from datetime import datetime
# import pytz
# import urllib
# from urllib import *
# import cv2
# import numpy as np
# import socket
# from urllib.parse import urlparse, parse_qs
# import time
# import zeep.helpers as helpers
# from datetime import datetime
# import time
# import redis
#
# import argparse
# import imutils
# import cv2
# from threading import Thread, ThreadError
# # from multiprocessing.pool import ThreadPool
# import sys
# from queue import Queue
# import time
# from operator import not_
# from collections.abc import *
# from typing import *
# import copy
#
# from logutils.SlaveLoggers import *
# from logutils.Logger import *
# from logutils.AbstractSlaveLogger import *
# from utils.RedisUtils import *
# from utils.CosThetaPrintUtils import *
# from utils.IPUtils import *
#
# import warnings
# warnings.filterwarnings('ignore', '.*h264.*', )
#
# from Configuration import *
# CosThetaConfigurator.getInstance()
#
# '''
# 0) Install Hikvision Tools Manager
# 1) Install SADP and set password as admin and admin. Enable 'Hik-vision Connect' and 'port'
# 2) Double-click on SADP row to open a browser and see the camera output
# 3) Check maikl for documeht 'Hikvision PZTZ settings'
#
#
# a) Go to Configurations -> Network -> Integration Protocol. Enable ONVIF. Then, create a newwidgets user with admin and abcd1234
# b) Go to that part which is creating a problem and overwrite the throws clause with 'returns xmlvalue'
# c) Clone the github in a directory and install on the machine using 'python setup.py install'.
#    This will ensure that the wsdls are installed in the correct directory and available to all the calls
# d) When you create a project, ensure that the venv has both options ticked for 'inherit global site-packages' and 'make available to all projects'
# '''
#
# class ONVIFCam():
#
#     logSource = getFullyQualifiedName(__file__)
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
#
#             self.streamSetup1 = None
#             self.video1 = None
#             self.videoUri1 = None
#             self.cam1Process = None
#             self.cam1Available = False
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
#             self.stopped = False
#             self.latestCam1Frame = None
#
#             ONVIFCam.logSource = getFullyQualifiedName(__file__, __class__)
#             self.connected = False
#             self.reconnect()
#
#         except Exception as e:
#             SlaveFileLogger.getInstance().logTakeNote(ONVIFCam.logSource,
#                                                       f"In ONVIFCam.{self.name}, unable to create ONVIFCamera due to {e}",
#                                                       Logger.PROBLEM)
#             SlaveConsoleLogger.getInstance().logTakeNote(ONVIFCam.logSource,
#                                                          f"In ONVIFCam.{self.name}, unable to create ONVIFCamera due to {e}",
#                                                          Logger.PROBLEM)
#             # raise e
#
#     def stop(self):
#         self.stopped = True
#
#     def reconnect(self):
#         # printBoldBlue(f"Trying to create camera with {self.IP}, {self.port}, {self.uid}, {self.password}, {self.wsdl_dir}")
#         SlaveFileLogger.getInstance().logTakeNote(ONVIFCam.logSource,
#                                                   f"Reconnecting to camera at IP {self.IP}",
#                                                   Logger.PROBLEM)
#         SlaveConsoleLogger.getInstance().logTakeNote(ONVIFCam.logSource,
#                                                      f"Reconnecting to camera at IP {self.IP}",
#                                                      Logger.PROBLEM)
#         # printBoldGreen(f"Reconnecting to camera at IP {self.IP}")
#         camIPAvailable = checkIP(self.IP)
#         # print(f"IP {self.IP} is {camIPAvailable}")
#         if not camIPAvailable:
#             self.camera = None
#             try:
#                 if self.cam1Process is not None:
#                     self.cam1Process.kill()
#                     self.cam1Process = None
#             except:
#                 pass
#             # printBoldRed("Setting connection to False because camIP not available")
#             self.connected = False
#             self.cam1Available = False
#             # print(f"Setting {self.cam1Available = } in reconnect()")
#             SlaveFileLogger.getInstance().logTakeAction(ONVIFCam.logSource,
#                                                       f"In ONVIFCam.{self.name}, could not connect to camera at IP {self.IP} - IP not reachable",
#                                                       Logger.PROBLEM)
#             SlaveConsoleLogger.getInstance().logTakeAction(ONVIFCam.logSource,
#                                                          f"In ONVIFCam.{self.name}, could not connect to camera at IP {self.IP} - IP not reachable",
#                                                          Logger.PROBLEM)
#             # printBoldRed(f"No camera available at IP {self.IP} - IP not reachable")
#             raise Exception(f"No camera available at IP {self.IP} - IP not reachable")
#         try:
#             # printBoldBlue(f"In first try")
#             try:
#                 if self.cam1Process is not None:
#                     # printBoldBlue(f"Inside second try because self.cam1Process is None")
#                     self.cam1Process.kill()
#                     self.cam1Process = None
#             except:
#                 self.cam1Process = None
#             try:
#                 self.camera = ONVIFCamera(self.IP, self.port, self.uid, self.password, self.wsdl_dir)
#             except Exception as e:
#                 printBoldRed(f"Could not create {self.camera} due to {e}")
#             print(f"After creating {self.camera}")
#             SlaveFileLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                       f"In ONVIFCam.{self.name}, created camera at IP {self.IP}",
#                                                       Logger.SUCCESS)
#             SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                          f"In ONVIFCam.{self.name}, created camera at IP {self.IP}",
#                                                          Logger.SUCCESS)
#             # printBoldBlue(f"In ONVIFCam.{self.name}, created camera at IP {self.IP}")
#             self.configureAndCreateCameraParameters()
#             self.connected = True
#             if not self.stopped:
#                 self.renewCam()
#             if self.cam1Process is not None and self.cam1Available and not self.stopped:
#                 self.launchFrameUpdateThread()
#             SlaveFileLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                       f"In ONVIFCam.{self.name}, created camera and connected to camera at IP {self.IP}",
#                                                       Logger.SUCCESS)
#             SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                          f"In ONVIFCam.{self.name}, created camera  and connected to camera at IP {self.IP}",
#                                                          Logger.SUCCESS)
#             # printBoldGreen(f"Connected to camera at {self.IP}")
#         except Exception as e:
#             self.camera = None
#             try:
#                 if self.cam1Process is not None:
#                     self.cam1Process.kill()
#                     self.cam1Process = None
#             except:
#                 self.cam1Process = None
#             # printBoldRed("Setting connection to False in except block of reconnect")
#             self.connected = False
#             self.cam1Available = False
#             # print(f"Setting {self.cam1Available = } again in renewCam()")
#             SlaveFileLogger.getInstance().logTakeAction(ONVIFCam.logSource,
#                                                       f"In ONVIFCam.{self.name}, could not connect to camera at IP {self.IP}, though IP is reachable, due to {str(e)}",
#                                                       Logger.PROBLEM)
#             SlaveConsoleLogger.getInstance().logTakeAction(ONVIFCam.logSource,
#                                                          f"In ONVIFCam.{self.name}, could not connect to camera at IP {self.IP}, though IP is reachable, due to {str(e)}",
#                                                          Logger.PROBLEM)
#             # printBoldRed(f"No camera available at IP {self.IP}, though IP is reachable")
#             raise Exception(f"No camera available at IP {self.IP}, though IP is reachable")
#
#
#     def renewCam(self) -> None:
#         # printBoldYellow(f"Inside renewCam()")
#         self.cam1Available = False
#         # print(f"Setting {self.cam1Available = } in renewCam()")
#         if self.cam1Process is not None:
#             try:
#                 self.cam1Process.kill()
#                 self.cam1Process = None
#             except:
#                 pass
#         try:
#             self.cam1Process = self.startFFMPEG_RTSP_Process()
#             if self.cam1Process is not None:
#                 self.cam1Available = True
#         except:
#             self.cam1Process = None
#             self.cam1Available = False
#             # print(f"Setting {self.cam1Available = } in renewCam()")
#             pass
#         # printBoldYellow(f"Exiting renewCam() with {self.cam1Available = }")
#         return None
#
#     def startFFMPEG_RTSP_Process(self):
#         try:
#             args = {
#                 "rtsp_transport": "tcp",
#                 "fflags": "nobuffer",
#                 "fflags": "discardcorrupt",
#                 "fflags": "flush_packets",
#                 "flags": "low_delay",
#                 "thread_queue_size": "2"
#                 # "loglevel": "quiet",
#                 # "nostats": ""
#             }
#             probe = ffmpeg.probe(self.videoUri1)
#             cap_info = next(x for x in probe['streams'] if x['codec_type'] == 'video')
#             # print("fps: {}".format(cap_info['r_frame_rate']))
#             self.width = cap_info['width']
#             self.height = cap_info['height']
#             up, down = str(cap_info['r_frame_rate']).split('/')
#             fps = eval(up) / eval(down)
#             # print("fps: {}".format(fps))
#             process1 = (
#                 ffmpeg
#                 .input(self.videoUri1, **args)
#                 # .output('pipe:', format='rawvideo', pix_fmt='rgb24')
#                 # .output('pipe:', format='rawvideo', pix_fmt='yuv420p')
#                 .output('pipe:', format='rawvideo', pix_fmt='bgr24', fflags='nobuffer', analyzeduration='1')
#                 .overwrite_output()
#                 .run_async(pipe_stdout=True)
#             )
#             SlaveFileLogger.getInstance().logTakeNote(ONVIFCam.logSource,
#                                                       f"In ONVIFCam.{self.name}, got FFMPEG process",
#                                                       Logger.SUCCESS)
#             return process1
#         except Exception as e:
#             SlaveFileLogger.getInstance().logTakeAction(ONVIFCam.logSource,
#                                                       f"In ONVIFCam.{self.name}, unable to create FFMPEG process due to {e}",
#                                                       Logger.PROBLEM)
#             SlaveConsoleLogger.getInstance().logTakeNote(ONVIFCam.logSource,
#                                                          f"In ONVIFCam.{self.name}, unable to create FFMPEG process due to {e}",
#                                                          Logger.PROBLEM)
#             printBoldRed(f"Cound not create FFMPEG process at IP {self.IP}")
#
#     def launchFrameUpdateThread(self):
#         objRef = self
#         def updateFrame():
#             # printBoldYellow(f"Inside updateFrame : {objRef.stopped = }; {objRef.cam1Available = }; {objRef.connected = }")
#             consecutiveCount = 0
#             frameCount = 0
#             while (not objRef.stopped) and objRef.cam1Available and objRef.connected:
#                 # printBoldYellow(f"Loop {frameCount+1}")
#                 try:
#                     in_bytes = objRef.cam1Process.stdout.read(objRef.width * objRef.height * 3)
#                     if not in_bytes:
#                         consecutiveCount += 1
#                         if consecutiveCount > 20:
#                             break
#                     else:
#                         consecutiveCount = 0
#                         frameCount += 1
#                         # ndarray
#                         # in_frame = (
#                         frame = (
#                             np
#                             .frombuffer(in_bytes, np.uint8)
#                             .reshape([objRef.height, objRef.width, 3])
#                         )
#                         # frame = cv2.resize(in_frame, (1280, 720))
#                         # frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR) # removed because pix_fmt changed to bgr24
#                         objRef.latestCam1Frame = copy.deepcopy(frame)
#                         if frameCount % 4000 == 0:
#                             # printBoldGreen(f"Got frame {frameCount}")
#                             SlaveConsoleLogger.getInstance().logConsiderAction(ONVIFCam.logSource,
#                                                                          f"In ONVIFCam.{self.name}, got frame {frameCount}",
#                                                                          Logger.SUCCESS)
#                         if frameCount % 1000 == 0:
#                             # printBoldGreen(f"Got frame {frameCount}")
#                             SlaveFileLogger.getInstance().logConsiderAction(ONVIFCam.logSource,
#                                                                          f"In ONVIFCam.{self.name}, got frame {frameCount}",
#                                                                          Logger.SUCCESS)
#                         # time.sleep(0.05)
#                         # cv2.imshow(frameName, frame)
#                         # if cv2.waitKey(1) == ord('q'):
#                             #     break
#                 except:
#                     consecutiveCount += 1
#                     if consecutiveCount > 20:
#                         break
#             objRef.cam1Available = False
#             objRef.connected = False
#             print(f"Setting {objRef.cam1Available = } in updateFrame()")
#             if objRef.cam1Process is not None:
#                 objRef.cam1Process.kill()
#         updateThread = threading.Thread(name=f"Frame update thread for camera at {self.IP}", target=updateFrame, args=(), daemon=True)
#         # printBold("About to start frame update thread")
#         SlaveFileLogger.getInstance().logTakeAction(ONVIFCam.logSource,
#                                                     f"In ONVIFCam.{self.name}, about to start frame update thread",
#                                                     Logger.SUCCESS)
#         updateThread.start()
#
#
#     def getHostName(self):
#         try:
#             self.camera.devicemgmt.GetHostname()
#             self.connected = True
#         except Exception as e:
#             self.connected = False
#             self.cam1Available = False
#             # print(f"Setting {self.cam1Available = } in getHostName()")
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
#     def printServicesFromURL(self, url='http://www.soapclient.com/xml/soapresponder.wsdl'):
#
#         try:
#             client1 = Client(wsdl=url)
#             # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#             #                                           f"In ONVIFCam.{self.name}, client is {client1}",
#             #                                           Logger.GENERAL)
#
#             for service in client1.wsdl.services.values():
#                 # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#                 #                                        f"In ONVIFCam.{self.name}, service is {service.name}",
#                 #                                        Logger.GENERAL)
#                 for port in service.ports.values():
#                     operations = sorted(
#                         port.binding._operations.values(),
#                         key=operator.attrgetter('name'))
#
#                     for operation in operations:
#                         # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#                         #                                        f"In ONVIFCam.{self.name}, method is {operation.name}, input is {operation.input.signature()}, output is {operation.output.signature()}",
#                         #                                        Logger.GENERAL)
#                         pass
#             client1.transport.session.close()
#         except:
#             pass
#
#     def printServiceFromWSDL(self, service=None):
#         try:
#             if service is None:
#                 return
#
#             interface = self.parseElements(service)
#             # pprint(interface)
#             # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#             #                                        f"In ONVIFCam.{self.name}, interface is {interface}",
#             #                                        Logger.GENERAL)
#             # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#             #                                        f"In ONVIFCam.{self.name}, service is {service.name}",
#             #                                        Logger.GENERAL)
#
#             for port in service.ports.values():
#                 operations = sorted(
#                     port.binding._operations.values(),
#                     key=operator.attrgetter('name'))
#
#                 for operation in operations:
#                     # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#                     #                                        f"In ONVIFCam.{self.name}, method is {operation.name}, input is {operation.input.signature()}, output is {operation.output.signature()}",
#                     #                                        Logger.GENERAL)
#                     pass
#         except:
#             pass
#     def parseElements(self, elements):
#         all_elements = {}
#         for name, element in elements:
#             all_elements[name] = {}
#             all_elements[name]['optional'] = element.is_optional
#             if hasattr(element.type, 'elements'):
#                 all_elements[name]['type'] = self.parseElements(self, element.type.elements)
#             else:
#                 all_elements[name]['type'] = str(element.type)
#
#         return all_elements
#
#     def prettyPrintWSDLFromURL(self, wsdl='http://www.soapclient.com/xml/soapresponder.wsdl'):
#         if wsdl == None:
#             return
#         # header = zeep.xsd.Element(
#         #     "Header",
#         #     zeep.xsd.ComplexType(
#         #         [
#         #             zeep.xsd.Element(
#         #                 "{http://www.w3.org/2005/08/addressing}Action", zeep.xsd.String()
#         #             ),
#         #             zeep.xsd.Element(
#         #                 "{http://www.w3.org/2005/08/addressing}To", zeep.xsd.String()
#         #             ),
#         #         ]
#         #     ),
#         # )
#         # set the header value from header element
#         # header_value = header(Action=method_url, To=service_url)
#         try:
#             client = Client(wsdl)
#             defaultHeader = {
#                 "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/51.0.2704.103 Safari/537.36"
#             }
#             client.set_default_soapheaders(headers=defaultHeader)
#             interface = {}
#             for service in client.wsdl.services.values():
#                 interface[service.name] = {}
#                 for port in service.ports.values():
#                     interface[service.name][port.name] = {}
#                     operations = {}
#                     for operation in port.binding._operations.values():
#                         operations[operation.name] = {}
#                         operations[operation.name]['input'] = {}
#                         elements = operation.input.body.type.elements
#                         operations[operation.name]['input'] = self.parseElements(elements)
#                         operations[operation.name]['output'] = {}
#                         elements = operation.output.body.type.elements
#                         operations[operation.name]['output'] = self.parseElements(elements)
#                     interface[service.name][port.name]['operations'] = operations
#             # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#             #                                        f"In ONVIFCam.{self.name}, interface is {interface}",
#             #                                        Logger.GENERAL)
#             client.transport.session.close()
#         except:
#             pass
#
#     def getZeepClientAfterBasicHttpAuthentication(self, wsdlURL, username, password):
#         try:
#             wsdl = wsdlURL
#             session = Session()
#             session.auth = HTTPBasicAuth(username, password)
#             client2 = Client(wsdl=wsdl, transport=Transport(session=session))
#             return client2
#         except Fault as fault:
#             parsed_fault_detail = client2.wsdl.types.deserialize(fault.detail[0])
#             return parsed_fault_detail
#
#     def closeClient(self, client3):
#         client3.transport.session.close()
#
#     def get_service(self, client4, translation):
#         if translation:
#             service_binding = client4.service._binding.name
#             service_address = client4.service._binding_options["address"]
#             return client4.create_service(
#                 service_binding,
#                 service_address.replace(*translation, 1))
#         else:
#             return client4.service
#
#     def createMediaService(self):
#         try:
#             # printBoldBlue("Entered createMediaService()")
#             # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#             #                                        f"Entering createMediaService(). In ONVIFCam.{self.name}, the camera is {self.camera} and is of type {type(self.camera)}, and hostname is {self.camera.devicemgmt.GetHostname()}",
#             #                                        Logger.GENERAL)
#             SlaveFileLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                    f"Entering createMediaService(). In ONVIFCam.{self.name}, the camera is {self.camera} and is of type {type(self.camera)}, and hostname is {self.camera.devicemgmt.GetHostname()}",
#                                                    Logger.GENERAL)
#             self.camera.devicemgmt.GetServices(False)
#             self.media_service = self.camera.create_media_service()
#             print(f"Media Service is {self.media_service}")
#             # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#             #                                        f"Exiting createMediaService().In ONVIFCam.{self.name}, Media service is {self.media_service} and is of type {type(self.media_service)}",
#             #                                        Logger.GENERAL)
#             SlaveFileLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                       f"Exiting createMediaService().In ONVIFCam.{self.name}, Media service is {self.media_service} and is of type {type(self.media_service)}",
#                                                       Logger.GENERAL)
#         except Exception as e:
#             SlaveConsoleLogger.getInstance().logTakeAction(ONVIFCam.logSource,
#                                                    f"Could not createMediaService() due to {e}",
#                                                    Logger.PROBLEM)
#             SlaveFileLogger.getInstance().logTakeAction(ONVIFCam.logSource,
#                                                       f"Could not createMediaService() due to {e}",
#                                                       Logger.PROBLEM)
#             raise e
#
#     def getConfig(self):
#         try:
#             # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#             #                                        f"Entering ONVIFCam.{self.name}.getConfig()",
#             #                                        Logger.GENERAL)
#             SlaveFileLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                    f"Entering ONVIFCam.{self.name}.getConfig()",
#                                                    Logger.GENERAL)
#             mdc = self.camera.media.GetMetadataConfigurations()
#             printLight(f"Configs are {self.config}")
#             self.config = self.camera.media.GetMetadataConfigurations()[0]
#             self.config.Analytics = True
#             self.config_dict = helpers.serialize_object(self.config, dict)
#             for i, aConfig in enumerate(list(self.config_dict.items())):
#                 # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#                 #                                        f"In ONVIFCam.{self.name}, Config {i} is {aConfig}",
#                 #                                        Logger.GENERAL)
#                 SlaveFileLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                           f"In ONVIFCam.{self.name}, Config {i} is {aConfig}",
#                                                           Logger.GENERAL)
#             # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#             #                                        f"Exiting ONVIFCam.{self.name}.getConfig()",
#             #                                        Logger.GENERAL)
#             SlaveFileLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                    f"Exiting ONVIFCam.{self.name}.getConfig()",
#                                                    Logger.GENERAL)
#         except Exception as e:
#             SlaveConsoleLogger.getInstance().logTakeAction(ONVIFCam.logSource,
#                                                            f"Could not getConfig() due to {e}",
#                                                            Logger.PROBLEM)
#             SlaveFileLogger.getInstance().logTakeAction(ONVIFCam.logSource,
#                                                         f"Could not getConfig() due to {e}",
#                                                         Logger.PROBLEM)
#             raise e
#
#     def getMetadataConfig(self):
#         try:
#             # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#             #                                        f"Entering ONVIFCam.{self.name}.getMetadataConfig()",
#             #                                        Logger.GENERAL)
#             SlaveFileLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                    f"Entering ONVIFCam.{self.name}.getMetadataConfig()",
#                                                    Logger.GENERAL)
#             self.metadataConfig = self.camera.media.create_type('SetMetadataConfiguration')
#             self.metadataConfig.Configuration = self.config
#             self.metadataConfig.ForcePersistence = True
#             self.metadataConfig_dict = helpers.serialize_object(self.metadataConfig, dict)
#             for i, aReq in enumerate(list(self.metadataConfig_dict.items())):
#                 # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#                 #                                        f"In ONVIFCam.{self.name}, Request {i} is {aReq}",
#                 #                                        Logger.GENERAL)
#                 SlaveFileLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                           f"In ONVIFCam.{self.name}, Request {i} is {aReq}",
#                                                           Logger.GENERAL)
#             self.camera.media.SetMetadataConfiguration(self.metadataConfig)
#             # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#             #                                        f"Exiting ONVIFCam.{self.name}.getMetadataConfig()",
#             #                                        Logger.GENERAL)
#             SlaveFileLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                    f"Exiting ONVIFCam.{self.name}.getMetadataConfig()",
#                                                    Logger.GENERAL)
#         except Exception as e:
#             SlaveConsoleLogger.getInstance().logTakeAction(ONVIFCam.logSource,
#                                                    f"Could not getMetadataConfig() due to {e}",
#                                                    Logger.PROBLEM)
#             SlaveFileLogger.getInstance().logTakeAction(ONVIFCam.logSource,
#                                                       f"Could not getMetadataConfig() due to {e}",
#                                                       Logger.PROBLEM)
#             raise e
#
#     def getProfiles(self):
#         try:
#             # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#             #                                        f"Entering ONVIFCam.{self.name}.getProfiles()",
#             #                                        Logger.GENERAL)
#             SlaveFileLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                    f"Entering ONVIFCam.{self.name}.getProfiles()",
#                                                    Logger.GENERAL)
#             print(f"Just before getting profiles from {self.media_service}")
#             self.profiles = self.media_service.GetProfiles()
#             print(f"Media Profiles are {self.profiles}")
#             for i, aProfile in enumerate(self.profiles):
#                 # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#                 #                                        f"In ONVIFCam.{self.name}, Profile {i} obtained through media service is {aProfile}",
#                 #                                        Logger.GENERAL)
#                 SlaveFileLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                           f"In ONVIFCam.{self.name}, Profile {i} obtained through media service is {aProfile}",
#                                                           Logger.GENERAL)
#             for profile in self.profiles:
#                 self.viableMediaProfiles.append(profile)
#             # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#             #                                        f"Exiting ONVIFCam.{self.name}.getProfiles()",
#             #                                        Logger.GENERAL)
#             SlaveFileLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                    f"Exiting ONVIFCam.{self.name}.getProfiles()",
#                                                    Logger.GENERAL)
#         except Exception as e:
#             SlaveConsoleLogger.getInstance().logTakeAction(ONVIFCam.logSource,
#                                                    f"Could not getProfiles() due to {e}",
#                                                    Logger.PROBLEM)
#             SlaveFileLogger.getInstance().logTakeAction(ONVIFCam.logSource,
#                                                       f"Could not getProfiles() due to {e}",
#                                                       Logger.PROBLEM)
#             raise e
#
#         # self.profilez = self.camera.media.GetProfiles()
#         # # print(f"profilez is of type {type(profilez)} and is ...")
#         # # print(profilez)
#         # # profilez_dict = helpers.serialize_object(profilez, dict)
#         # if printCameraDetails:
#         #     for i, profile in enumerate(self.profilez):
#         #         printLight(profile)
#         #         printLight(f'Profile {i} is {profile.token}:')
#         #         # print('profile %s' % profile.token)
#         #         if not hasattr(profile, 'MetadataConfiguration'):
#         #             self.camera.media.AddMetadataConfiguration({
#         #                 'ProfileToken': profile.token,
#         #                 'ConfigurationToken': self.config.token,
#         #             })
#         #         resp = self.camera.media.GetStreamUri({
#         #             'StreamSetup': {'Stream': 'RTP-Unicast', 'Transport': {'Protocol': 'RTSP'}},
#         #             'ProfileToken': profile.token,
#         #         })
#         #         # print(f"resp is of type {type(resp)} ans is {resp}")
#         #         printLight(f'Stream uri for profile {profile.token} is {resp.Uri}')
#         #         imageUri = self.camera.media.GetSnapshotUri(profile.token)
#         #         printLight(f'Snapshot uri for profile {profile.token} is {imageUri}')
#         #         # print('-------------------------------------')
#
#     def getEventService(self):
#         try:
#             # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#             #                                        f"Entering ONVIFCam.{self.name}.getEventService()",
#             #                                        Logger.GENERAL)
#             SlaveFileLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                    f"Entering ONVIFCam.{self.name}.getEventService()",
#                                                    Logger.GENERAL)
#             self.eventService = self.camera.create_events_service()
#             self.eventProperties = self.eventService.GetEventProperties()
#             # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#             #                                        f"In ONVIFCam.{self.name}, event properties are {self.eventProperties}",
#             #                                        Logger.GENERAL)
#             SlaveFileLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                       f"In ONVIFCam.{self.name}, event properties are {self.eventProperties}",
#                                                       Logger.GENERAL)
#             # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#             #                                        f"Exiting ONVIFCam.{self.name}.getEventService()",
#             #                                        Logger.GENERAL)
#             SlaveFileLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                    f"Exiting ONVIFCam.{self.name}.getEventService()",
#                                                    Logger.GENERAL)
#         except Exception as e:
#             SlaveConsoleLogger.getInstance().logTakeAction(ONVIFCam.logSource,
#                                                    f"Could not getEventService() due to {e}",
#                                                    Logger.PROBLEM)
#             SlaveFileLogger.getInstance().logTakeAction(ONVIFCam.logSource,
#                                                       f"Could not getEventService() due to {e}",
#                                                       Logger.PROBLEM)
#             raise e
#
#     def getSnapShotDetails(self):
#         try:
#             # in this loop, set imageUri1, request1, url_response1 and imageUri2, request2, url_response2
#             # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#             #                                        f"Entering ONVIFCam.{self.name}.getSnapshotDetails()",
#             #                                        Logger.GENERAL)
#             SlaveFileLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                    f"Entering ONVIFCam.{self.name}.getSnapsotDetails()",
#                                                    Logger.GENERAL)
#             for i in range(1,2):
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
#                 printBoldBlue(f"requestStr : {type(responseObj)}")
#             # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#             #                                        f"Exiting ONVIFCam.{self.name}.getSnapshotDetails()",
#             #                                        Logger.GENERAL)
#             SlaveFileLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                    f"Exiting ONVIFCam.{self.name}.getSnapsotDetails()",
#                                                    Logger.GENERAL)
#         except Exception as e:
#             SlaveConsoleLogger.getInstance().logTakeAction(ONVIFCam.logSource,
#                                                    f"Could not getSnapshotDetails() due to {e}",
#                                                    Logger.PROBLEM)
#             SlaveFileLogger.getInstance().logTakeAction(ONVIFCam.logSource,
#                                                       f"Could not getSnapshotDetails() due to {e}",
#                                                       Logger.PROBLEM)
#             printBoldRed(f"{e}")
#             raise e
#
#     def getHDConfigurations(self):
#         try:
#             ## get video info , 'h265' or 'h264', 'width' 'height' 'gop' ....
#             # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#             #                                        f"Entering ONVIFCam.{self.name}.getHDConfigurations()",
#             #                                        Logger.GENERAL)
#             SlaveFileLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                    f"Entering ONVIFCam.{self.name}.getHDConfigurations()",
#                                                    Logger.GENERAL)
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
#                 SlaveFileLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                        f"In ONVIFCam.{self.name}, HD configurations are {self.hdConfigurations}",
#                                                        Logger.GENERAL)
#                 # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#                 #                                        f"In ONVIFCam.{self.name}, HD configurations are {self.hdConfigurations}",
#                 #                                        Logger.GENERAL)
#             # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#             #                                        f"Exiting ONVIFCam.{self.name}.getHDConfigurations()",
#             #                                        Logger.GENERAL)
#             SlaveFileLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                    f"Exiting ONVIFCam.{self.name}.getHDConfigurations()",
#                                                    Logger.GENERAL)
#         except Exception as e:
#             SlaveConsoleLogger.getInstance().logTakeAction(ONVIFCam.logSource,
#                                                    f"Could not getHDConfigurations() due to {e}",
#                                                    Logger.PROBLEM)
#             SlaveFileLogger.getInstance().logTakeAction(ONVIFCam.logSource,
#                                                       f"Could not getHDConfigurations() due to {e}",
#                                                       Logger.PROBLEM)
#             raise e
#
#     def getVideoStreamDetails(self):
#         try:
#             # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#             #                                        f"Entering ONVIFCam.{self.name}.getVideoStreams()",
#             #                                        Logger.GENERAL)
#             SlaveFileLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                    f"Entering ONVIFCam.{self.name}.getVideoStreams()",
#                                                    Logger.GENERAL)
#             print(f"Media Profiles are {self.viableMediaProfiles}")
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
#             self.video1 = self.camera.media.GetStreamUri(self.streamSetup1)
#             self.videoUri1 = self.video1.Uri.replace('rtsp://', 'rtsp://{}:{}@'.format(self.uid,self.password), 1)
#             print(f"The video URI is {self.videoUri1}")
#             # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#             #                                        f"Exiting ONVIFCam.{self.name}.getVideoStreams()",
#             #                                        Logger.GENERAL)
#             SlaveFileLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                    f"Exiting ONVIFCam.{self.name}.getVideoStreams()",
#                                                    Logger.GENERAL)
#         except Exception as e:
#             SlaveConsoleLogger.getInstance().logTakeAction(ONVIFCam.logSource,
#                                                    f"Could not getVideoStreams() due to {e}",
#                                                    Logger.PROBLEM)
#             SlaveFileLogger.getInstance().logTakeAction(ONVIFCam.logSource,
#                                                       f"Could not getVideoStreams() due to {e}",
#                                                       Logger.PROBLEM)
#             raise e
#
#     def getFrame(self):
#         if not self.cam1Available:
#             print(f"{self.cam1Available = }")
#             return False, None
#         frameCopy = copy.deepcopy(self.latestCam1Frame)
#         return True, frameCopy
#
#     def getPicture(self):
#         if not self.connected:
#             printBoldRed(f"Returning None from getPicture() as connection is {self.connected}")
#             return None
#         try:
#             self.refreshPictureResponseURL()
#             img_array = np.asarray(bytearray(self.url_response1.read()), dtype=np.uint8)
#             img = cv2.imdecode(img_array, -1)  # 'Load it as it is'
#             return img
#         except:
#             pass
#         return None
#
#     def refreshPictureResponseURL(self):
#         try:
#             attr_name1 = f"request1"
#             attr_name2 = f"url_response1"
#             setattr(self, attr_name2, urllib.request.urlopen(getattr(self, attr_name1)))
#         except:
#             pass
#
#
#     def getPicture_Alternate(self):
#         if not self.connected:
#             return None
#         try:
#             rawPic = self.getRawPicture()
#             if rawPic is None:
#                 return None
#             img_array = np.asarray(bytearray(rawPic), dtype=np.uint8)
#             img = cv2.imdecode(img_array, -1)
#             return img
#         except:
#             pass
#         return None
#
#     def getRawPicture(self):
#         if not self.connected:
#             printBoldRed(f"Returning None from getRawPicture() as connection is {self.connected}")
#             return None
#         try:
#             url_response = requests.get(self.imageUri1.Uri, auth=self.authorisation)
#             content = url_response.content
#             return content
#         except:
#             pass
#         return None
#
#     # def getTimedPicture(self, timeDuration : float = 0.06, source : int = 1):
#     #     if self.pictureBeingFetched:
#     #         return None
#     #     objRef = self
#     #     self.pictureFetched = None
#     #     self.doneTimedPicture = False
#     #     def pic():
#     #         if objRef.pictureBeingFetched == True:
#     #             objRef.doneTimedPicture = True
#     #             return
#     #         objRef.pictureBeingFetched = True
#     #         # if (source == 2):
#     #         try:
#     #             # url_response = requests.get(objRef.imageUri2.Uri, auth=objRef.authorisation)
#     #             # content = url_response.content
#     #             objRef.pictureFetched = objRef.getPicture(source=source)
#     #             objRef.pictureBeingFetched = False
#     #             objRef.doneTimedPicture = True
#     #             return
#     #         except:
#     #             objRef.pictureFetched = None
#     #             objRef.pictureBeingFetched = False
#     #             objRef.doneTimedPicture = True
#     #             objRef.configureAndCreateCameraParameters()
#     #         return
#     #     task = threading.Thread(target=pic)
#     #     task.start()
#     #     startTime = time.time()
#     #     while not objRef.doneTimedPicture:
#     #         currentTime = time.time()
#     #         dur = currentTime - startTime
#     #         if dur < timeDuration:
#     #             time.sleep(0.005)
#     #         else:
#     #             printBoldRed(f"Breaking after {round(dur * 1000, 1)} ms")
#     #             break
#     #     return self.pictureFetched
#
#     # **********************
#
#     def releaseResources(self):
#         self.stop()
#         if hasattr(self,"cam1Process"):
#             if self.cam1Process is not None:
#                 try:
#                     self.cam1Process.kill()
#                 except:
#                     pass
#             self.cam1Process = None
#             self.cam1Available = False
#             # print(f"Setting {self.cam1Available = } in releaseResources()")
#
#     def getPTZService(self):
#         # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#         #                                        f"Entering ONVIFCam.{self.name}.getPTZService()",
#         #                                        Logger.GENERAL)
#         SlaveFileLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                f"Entering ONVIFCam.{self.name}.getPTZService()",
#                                                Logger.GENERAL)
#         try:
#             # Create ptz service
#             self.ptzService = self.camera.create_ptz_service()
#             # Get ptz configuration
#             self.ptzConfigs = self.ptzService.GetConfigurations()
#             # print(ptzConfigs)
#             self.aPTZConfig = self.ptzService.GetConfiguration('PTZToken')
#             SlaveFileLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                    f"In ONVIFCam.{self.name}, one of the PTZ configurations is {self.aPTZConfig}",
#                                                    Logger.GENERAL)
#             # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#             #                                        f"In ONVIFCam.{self.name}, one of the PTZ configurations is {self.aPTZConfig}",
#             #                                        Logger.GENERAL)
#
#             self.ptzConfigOptions = self.ptzService.GetConfigurationOptions('PTZToken')
#             SlaveFileLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                    f"In ONVIFCam.{self.name}, the configuration options are {self.ptzConfigOptions}",
#                                                    Logger.GENERAL)
#             # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#             #                                        f"In ONVIFCam.{self.name}, the configuration options are {self.ptzConfigOptions}",
#             #                                        Logger.GENERAL)
#
#             self.ptzNodes = self.ptzService.GetNodes()
#             SlaveFileLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                    f"In ONVIFCam.{self.name}, the PTZ Nodes are {self.ptzNodes}",
#                                                    Logger.GENERAL)
#             # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#             #                                        f"In ONVIFCam.{self.name}, the PTZ Nodes are {self.ptzNodes}",
#             #                                        Logger.GENERAL)
#
#             self.ptzServiceCapabilities = self.ptzService.GetServiceCapabilities()
#             SlaveFileLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                    f"In ONVIFCam.{self.name}, the Service Capabilities are {self.ptzServiceCapabilities}",
#                                                    Logger.GENERAL)
#             # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#             #                                        f"In ONVIFCam.{self.name}, the Service Capabilities are {self.ptzServiceCapabilities}",
#             #                                        Logger.GENERAL)
#
#             self.definedPtzPresets = self.ptzService.GetPresets(self.viableMediaProfiles[0].token)
#             for preset in self.definedPtzPresets:
#                 if (preset.PTZPosition.PanTilt.x in [0.0, 1.0]) and (preset.PTZPosition.PanTilt.y in [0.0, 1.0]):
#                     pass
#                 else:
#                     self.definedPtzPresets.append(preset)
#             SlaveFileLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                    f"In ONVIFCam.{self.name}, the main presets are {self.definedPtzPresets}",
#                                                    Logger.GENERAL)
#             # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#             #                                        f"In ONVIFCam.{self.name}, the main presets are {self.definedPtzPresets}",
#             #                                        Logger.GENERAL)
#
#             currentStatus = self.definedPtzPresets.GetStatus(self.viableMediaProfiles[0].token)
#             SlaveFileLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                    f"In ONVIFCam.{self.name}, the current PTZ status is {currentStatus}",
#                                                    Logger.GENERAL)
#             # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#             #                                        f"In ONVIFCam.{self.name}, the current PTZ status is {currentStatus}",
#             #                                        Logger.GENERAL)
#
#             # ptzService.GotoPreset(viableMediaProfiles[0].token, ptzPresets[0].Name)
#             #
#             #     print(media_service.to_dict(media_service))
#             #     streamURI = media_service.GetStreamUri({'StreamSetup': {'Stream': 'RTP-Unicast', 'Transport': 'RTSP'}})
#             #     print(f'Stream URI is {streamURI}')
#             #     ptz = mycam.create_ptz_service()
#             #     # print(ptz)
#             #
#             #     profiles = media_service.GetProfiles()
#             #     # print(profiles)
#             #     # print(f'no of profiles are {len(profiles)}')
#             #     # print(f'first profile is {profiles[0]}')
#             #
#             #     # for key in profiles[0]:
#             #     #     print(f'Value of {key} is {profiles[0][key]}')
#             #
#             #     # Use the first profile and Profiles have at least one
#             #     token = profiles[0].token
#             #
#             #     # Get all video encoder configurations
#             #     configurations_list = media_service.GetVideoEncoderConfigurations()
#             #     # print("-------------------------------------------------")
#             #     # print(f'Video Encoder Configurations are {configurations_list}')
#             #
#             #     # Use the first profile and Profiles have at least one
#             #     video_encoder_configuration = configurations_list[0]
#             #     # print("-------------------------------------------------")
#             #     # print(f'First Video Encoder Configuration is {video_encoder_configuration}')
#             #
#             #     # Get video encoder configuration options
#             #     options = media_service.GetVideoEncoderConfigurationOptions({'ProfileToken':token})
#             #     # print("-------------------------------------------------")
#             #     # print(f'Video encoder configuration of profile {token} is {options}')
#             #
#             #     # Setup stream configuration
#             #     video_encoder_configuration.Encoding = 'H264'
#             #     # print(f'H264 is {options.H264}')
#             #     # print(f'QualityRange is {options.QualityRange}')
#             #
#             #     resolutions = options.H264.ResolutionsAvailable
#             #     width = 0
#             #     height = 0
#             #     for resolution in resolutions:
#             #         if resolution.Width > width or resolution.Height > height:
#             #             width = resolution.Width
#             #             height = resolution.Height
#             #
#             #     # Setup Resolution
#             #     video_encoder_configuration.Resolution.Width = width
#             #     video_encoder_configuration.Resolution.Height = height
#             #     # Setup Quality
#             #     video_encoder_configuration.Quality = options.QualityRange.Max
#             #     # Setup FramRate
#             #     video_encoder_configuration.RateControl.FrameRateLimit = options.H264.FrameRateRange.Max
#             #     # Setup EncodingInterval
#             #     video_encoder_configuration.RateControl.EncodingInterval = options.H264.EncodingIntervalRange.Min
#             #     # Setup Bitrate
#             #     if (options.Extension.H264):
#             #         video_encoder_configuration.RateControl.BitrateLimit = options.Extension.H264.BitrateRange.Min
#             #
#             #     # print("-------------------------------------------------")
#             #     # print(f'Video width is {video_encoder_configuration.Resolution.Width}, height is {video_encoder_configuration.Resolution.Height},'
#             #     #       f'Quality is {video_encoder_configuration.Quality}, Frame rate is {video_encoder_configuration.RateControl.FrameRateLimit},'
#             #     #       f'encoding interval is {video_encoder_configuration.RateControl.EncodingInterval}, Bit rate limit is {video_encoder_configuration.RateControl.BitrateLimit}')
#             #
#             #     # Create request type instance
#             #     request = media_service.create_type('SetVideoEncoderConfiguration')
#             #     request.Configuration = video_encoder_configuration
#             #     # ForcePersistence is obsolete and should always be assumed to be True
#             #     request.ForcePersistence = True
#             #     # Set the video encoder configuration
#             #     media_service.SetVideoEncoderConfiguration(request)
#             #     # print("-------------------------------------------------")
#             #     # print(f'request object is {request}')
#             #
#             #     imaging = mycam.create_imaging_service()
#             #     # print("-------------------------------------------------")
#             #     # print(f'imaging object is {imaging}')
#             #     requestGetImaging = imaging.create_type('GetImagingSettings')
#             #     video_sources = media_service.GetVideoSources()
#             #     requestGetImaging.VideoSourceToken = video_sources[0].token
#             #     # print("-------------------------------------------------")
#             #     # print(f'requestGetImaging object is {requestGetImaging}')
#             #
#             #     print("-------------------------------------------------")
#             #     print(f'ptz object is {ptz}')
#             #     ptzConfig = ptz.GetConfiguration()
#             #     print("-------------------------------------------------")
#             #     print(f'ptzConfig object is {ptzConfig}')
#             #
#             #     responseGetImageSettings = imaging.GetImagingSettings(requestGetImaging)
#             #     print("-------------------------------------------------")
#             #     print(f'requestGetImageSettings object is {responseGetImageSettings}')
#             #
#             #     requestSetImaging = imaging.create_type('SetImagingSettings')
#             #     requestSetImaging.VideoSourceToken = video_sources[0].token
#             #     requestSetImaging.ImagingSettings = responseGetImageSettings
#             #     requestSetImaging.ImagingSettings.Brightness = 50
#             #     print("-------------------------------------------------")
#             #     print(f'requestSetImaging object is {requestSetImaging}')
#             #     imaging.SetImagingSettings(requestSetImaging)
#
#             #     print('----------------------------------')
#             #     request = ptzService.create_type('GotoPreset')
#             #     print(request)
#             #     request.ProfileToken = str(viableMediaProfiles[0].token)
#             #     request.PresetToken = str(ptzPresets[2].token)
#             #     print(request)
#             #     response = ptzService.GotoPreset(request)
#             #     print(response)
#
#             self.ptz_supported = True
#         except:
#             self.ptz_supported = False
#         # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource,
#         #                                        f"Exiting ONVIFCam.{self.name}.getPTZService()",
#         #                                        Logger.GENERAL)
#         SlaveFileLogger.getInstance().logDebug(ONVIFCam.logSource,
#                                                f"Exiting ONVIFCam.{self.name}.getPTZService()",
#                                                Logger.GENERAL)
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
#         try:
#             self.createMediaService()
#             printBoldBlue("Finished media service, and now entering config")
#             # self.getConfig()
#             # printBoldBlue("Finished config, and now entering metadataconfig")
#             # self.getMetadataConfig()
#             # printBoldBlue("Executed getMetadataConfig()")
#             self.getProfiles()
#             # printBoldBlue("Executed getProfiles()")
#             # self.getSnapShotDetails()
#             # printBoldBlue("Got snapshot details")
#             # self.getHDConfigurations()
#             # printBoldBlue("Got HD Configurations")
#             self.getVideoStreamDetails()
#             printBoldBlue("Got Video Stream Details")
#             self.getEventService()
#             try:
#                 self.getPTZService()
#             except:
#                 pass
#         except Exception as e:
#             printBoldRed(f"Encountered exception {e}")
#
#
# # cam = ONVIFCam("192.168.1.100", 80, "admin", "abcd1234", "C:\PythonProjects\Geltec\onvif\oer10")
# # printBoldGreen(cam)
# #
# # printBoldGreen(f"Starting")
# # currTime = time.time()*1000
# # r = 500
# # cutoff = 100
# # greenCount = 0
# # blueCount = 0
# # yellowCount = 0
# # redCount = 0
# # greenCutoff = 50
# # blueCutoff = 75
# # yellowCutoff = 100
# # for l in range(r):
# #     startTime = time.time()*1000
# #     pic = cam.getRawPicture((l % 2) + 1)
# #     endTime = time.time()*1000
# #     duration = endTime-startTime
# #     if duration > yellowCutoff:
# #         redCount += 1
# #         # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource, f"{l} : {type(pic)} - {duration}", Logger.RISK)
# #         printBoldRed(f"{l} : {type(pic)} - {duration}")
# #     elif duration > blueCutoff:
# #         yellowCount += 1
# #         # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource, f"{l} : {type(pic)} - {duration}", Logger.SUCCESS)
# #         printBoldYellow(f"{l} : {type(pic)} - {duration}")
# #     elif duration > greenCutoff:
# #         blueCount += 1
# #         # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource, f"{l} : {type(pic)} - {duration}", Logger.SUCCESS)
# #         printBoldBlue(f"{l} : {type(pic)} - {duration}")
# #     else:
# #         greenCount += 1
# #         # printBoldGreen(f"{l} : {type(pic)} - {duration}")
# # printBoldRed(f"Green count: {greenCount}; Blue count : {blueCount}; Yellow count : {yellowCount}; Red count : {redCount}")
# # endT = time.time()*1000
# # # SlaveConsoleLogger.getInstance().logDebug(ONVIFCam.logSource, f"Ending - got {r} frames @ {round((endT - currTime) / r, 2)} ms per pic", Logger.SUCCESS)
# # printBoldGreen(f"Ending - got {r} frames @ {round((endT - currTime) / r, 2)} ms per pic")
#
# # printBoldGreen(f"Starting")
# # for i in range(100):
# #     pic = cam.getPicture((i % 2) + 1)
# #     printBoldGreen(f"{i} : {type(pic)} - {pic.shape[1]} x {pic.shape[0]}")
# # printBoldGreen(f"Ending")
# #
# # printBoldGreen(f"Starting")
# # for j in range(100):
# #     pic = cam.getPicture_Alternate((j % 2) + 1)
# #     printBoldGreen(f"{j} : {type(pic)} - {pic.shape[1]} x {pic.shape[0]}")
# # printBoldGreen(f"Ending")
#
#
#
# # printBoldGreen(f"Starting")
# # cam1 = ONVIFCam("192.168.1.100", 80, "admin", "abcd1234", "C:/CosTheta/GelTec/onvif/ver10/")
# # printBoldGreen(cam1)
# # cam2 = ONVIFCam("192.168.1.100", 80, "costheta", "abcd1234", "C:/CosTheta/GelTec/onvif/ver10/")
# # printBoldGreen(cam2)
# # consecutiveCount_Source2 = 0
# # consecutiveCount_Source1 = 0
# # cam2ConsecutiveCount_Source2 = 0
# # cam2ConsecutiveCount_Source1 = 0
# # delayCount = 0
# # noneCount = 0
# # currTime = time.time()*1000
# # r = 100000
# # cutoff = 38
# # for k in range(r):
# #     pic = None
# #     if consecutiveCount_Source2 == 1009:
# #         cam1.cam2Available = False
# #         cam1.launchRenewThread(2)
# #         consecutiveCount_Source2 = 0
# #     if consecutiveCount_Source1 == 1471:
# #         cam1.cam1Available = False
# #         cam1.launchRenewThread(1)
# #         consecutiveCount_Source1 = 0
# #     if cam2ConsecutiveCount_Source2 == 1471:
# #         cam2.cam2Available = False
# #         cam2.launchRenewThread(2)
# #         cam2ConsecutiveCount_Source2 = 0
# #     if cam2ConsecutiveCount_Source1 == 1009:
# #         cam2.cam1Available = False
# #         cam1.launchRenewThread(1)
# #         cam2ConsecutiveCount_Source1 = 0
# #     currentCam = None
# #     startTime = time.time()*1000
# #     if k % 2 == 0:
# #         if cam1.cam2Available:
# #             _, pic = cam1.getFrame(2)
# #             consecutiveCount_Source2 += 1
# #             if pic is None:
# #                 cam1.cam2Available = False
# #                 cam1.launchRenewThread(2)
# #                 consecutiveCount_Source2 = 0
# #             else:
# #                 currentCam = cam1.cam2
# #         elif cam2.cam2Available:
# #             _, pic = cam2.getFrame(2)
# #             cam2ConsecutiveCount_Source2 += 1
# #             if pic is None:
# #                 cam2.cam2Available = False
# #                 cam2.launchRenewThread(2)
# #                 cam2ConsecutiveCount_Source2 = 0
# #             else:
# #                 currentCam = cam2.cam2
# #         elif cam1.cam1Available:
# #             _, pic = cam1.getFrame(1)
# #             consecutiveCount_Source1 += 1
# #             if pic is None:
# #                 cam1.cam1Available = False
# #                 cam1.launchRenewThread(1)
# #                 consecutiveCount_Source1 = 0
# #             else:
# #                 currentCam = cam1.cam1
# #         else:
# #             _, pic = cam2.getFrame(1)
# #             cam2ConsecutiveCount_Source1 += 1
# #             if pic is None:
# #                 cam2.cam1Available = False
# #                 cam2.launchRenewThread(1)
# #                 cam2ConsecutiveCount_Source1 = 0
# #             else:
# #                 currentCam = cam2.cam1
# #     else:
# #         # if cam2.cam2Available:
# #         #     _, pic = cam2.getFrame(2)
# #         # else:
# #         #     _, pic = cam1.getFrame(2)
# #         if cam2.cam2Available:
# #             _, pic = cam2.getFrame(2)
# #             cam2ConsecutiveCount_Source2 += 1
# #             if pic is None:
# #                 cam2.cam2Available = False
# #                 cam2.launchRenewThread(2)
# #                 cam2ConsecutiveCount_Source2 = 0
# #             else:
# #                 currentCam = cam2.cam2
# #         elif cam1.cam2Available:
# #             _, pic = cam1.getFrame(2)
# #             consecutiveCount_Source2 += 1
# #             if pic is None:
# #                 cam1.cam2Available = False
# #                 cam1.launchRenewThread(2)
# #                 consecutiveCount_Source2 = 0
# #             else:
# #                 currentCam = cam1.cam2
# #         elif cam2.cam1Available:
# #             _, pic = cam2.getFrame(1)
# #             cam2ConsecutiveCount_Source1 += 1
# #             if pic is None:
# #                 cam2.cam1Available = False
# #                 cam2.launchRenewThread(1)
# #                 cam2ConsecutiveCount_Source1 = 0
# #             else:
# #                 currentCam = cam2.cam1
# #         else:
# #             _, pic = cam1.getFrame(1)
# #             consecutiveCount_Source1 += 1
# #             if pic is None:
# #                 cam1.cam1Available = False
# #                 cam1.launchRenewThread(1)
# #                 consecutiveCount_Source1 = 0
# #             else:
# #                 currentCam = cam1.cam1
# #     endTime = time.time()*1000
# #     duration = endTime-startTime
# #     if (duration > cutoff):
# #         delayCount += 1
# #         printBoldRed(f"{k} : {type(pic)} - {duration}")
# #         if currentCam == cam1.cam1:
# #             cam1.cam1Available = False
# #             cam1.launchRenewThread(1)
# #             consecutiveCount_Source1 = 0
# #         elif currentCam == cam1.cam2:
# #             cam1.cam2Available = False
# #             cam1.launchRenewThread(2)
# #             consecutiveCount_Source2 = 0
# #         elif currentCam == cam2.cam1:
# #             cam2.cam1Available = False
# #             cam2.launchRenewThread(1)
# #             cam2ConsecutiveCount_Source1 = 0
# #         elif currentCam == cam2.cam2:
# #             cam2.cam2Available = False
# #             cam2.launchRenewThread(2)
# #             cam2ConsecutiveCount_Source2 = 0
# #     else:
# #         # printBoldGreen(f"{k} : {type(pic)}")
# #         pass
# #     # reduced_image = cv2.resize(pic, (320, 240), interpolation=cv2.INTER_AREA)
# #     reduced_image = pic
# #     st = (cutoff - duration)/1000
# #     if st > 0:
# #         time.sleep(st)
# #     if reduced_image is not None:
# #         # printBoldGreen(f" - {reduced_image.shape[1]} x {reduced_image.shape[0]}")
# #         pass
# #     else:
# #         noneCount += 1
# #         # printBoldRed(f"{k} : None")
# # printBoldRed(f"{delayCount} pics delayed")
# # printBoldRed(f"{noneCount} pics were None")
# # endT = time.time()*1000
# # printBoldGreen(f"Ending - got {r} frames @ {round((endT - currTime) / r, 2)} ms per pic")
#
# printBoldGreen(f"Starting")
# cam1 = ONVIFCam("192.168.1.100", 80, "admin", "abcd1234", "C:/PythonProjects/Ashok_Leyland/onvif/ver10/")
# printBoldGreen(cam1)
# startTime = time.time()
# repeats=10
# try:
#     for i in range(repeats):
#         x = cam1.getPicture()
#         print(x.shape)
#         # cv2.imshow("Image", x)
#         # printBold(x)
#         time.sleep(1)
# except Exception as e:
#     printBoldRed(e)
# endTime = time.time()
# print(f"{(endTime - startTime)*1000/repeats} ms")
