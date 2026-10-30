# import zeep
# from zeep import Client
# from requests import Session
# from requests.auth import HTTPBasicAuth
# from zeep.transports import Transport
# from zeep.exceptions import Fault
# import operator
# import onvif
# from onvif2 import client
# from onvif2.client import ONVIFCamera, ONVIFService
# from pprint import pprint
# from datetime import datetime
# import pytz
# import urllib
# import cv2
# import numpy as np
# import socket
# from urllib.parse import urlparse, parse_qs
# import time
# import zeep.helpers as helpers
# from datetime import datetime
# import time
#
# import argparse
# import imutils
# import cv2
# from threading import Thread
# import sys
# from queue import Queue
# import time
# from operator import not_
# from collections.abc import *
# from typing import *
# # from video.QSH import *
#
#
# mainDebug = True
# whichStream_Debug = False
# debugCV2Info = False
#
# class StreamVideoCam(Thread):
#
#     debugInit = True
#     debugRenew = False
#     debugWait = False
#     debugNotAvailable = False
#     debugGetCam = False
#     debugWork = False
#     debugRecreate = True
#
#     def __init__(self, camera : ONVIFCamera, mediaService: ONVIFService, streamSetup : dict, name: str, max_size = 2048, **kwargs):
#         Thread.__init__(self)
#         if camera is None:
#             return # replace with raising Exception
#         if streamSetup is None:
#             return # replace with raising Exception
#         self.kwargs = kwargs
#         self.name = name
#         if self.debugInit:
#             print(f"{datetime.now().strftime('%H:%M:%S.%f')} - Initialising StreamVideoCam.{self.name} ")
#         self.stopped = False
#         self.started = False
#         self.camera = camera
#         if mediaService is None:
#             self.mediaService = self.camera.create_media_service()
#         else:
#             self.mediaService = mediaService
#         self.streamSetup = streamSetup
#         try:
#             videoUri = self.mediaService.GetStreamUri(self.streamSetup)
#             videoRTSP = videoUri.Uri
#             finalRTSP = videoRTSP.replace(
#                 'rtsp://', 'rtsp://{}:{}@'.format(
#                     'admin',
#                     'abcd1234'), 1)
#             if self.debugInit:
#                 print(f"{datetime.now().strftime('%H:%M:%S.%f')} - In StreamVideoCam.{self.name}.__init__(), Video URI is {finalRTSP}")
#             self.cam = cv2.VideoCapture(finalRTSP)         # the video camera object is stored in this
#             if self.cam is not None:
#                 self.gotCam = True     # signifies if the video camera object has been created or renewed
#             else:
#                 self.gotCam = False
#         except:
#             self.cam = None
#             self.gotCam = False
#         self.processingQueue = Queue(max_size)
#         if self.debugInit:
#             print(f"{datetime.now().strftime('%H:%M:%S.%f')} - In StreamVideoCam.{self.name}.__init__(), gotCam is {self.gotCam}, and cam is {self.cam}")
#
#     # def addQuitSignalHolder(self, qsh : QuitSignalHolder):
#     #     self.quitSignalHolder = qsh
#     #
#     def renew(self):
#         item = "Renew"
#         if self.stopped:
#             # stop addition of items if stopped
#             return
#         try:
#             self.gotCam = False
#             self.processingQueue.put_nowait(item)
#             if self.debugRenew:
#                 print(f"{datetime.now().strftime('%H:%M:%S.%f')} - Successfully put {item} in Queue of StreamVideoCam.{self.name} ")
#             return item
#         except:
#             return None  # lose the item, if the queue is full
#
#     def waitForRenew(self, block = True, timeout = 0):
#         # continue till the queue is emptied
#         if self.debugWait:
#             print(f"{datetime.now().strftime('%H:%M:%S.%f')} - In StreamVideoCam.{self.name}.waitForRenew()")
#         try:
#             item = self.processingQueue.get(block = block, timeout = timeout)
#             if self.debugWait:
#                 print(f"{datetime.now().strftime('%H:%M:%S.%f')} - StreamVideoCam.{self.name}.waitForRenew() returns {item if item is not None else 'None'}")
#             return item
#         except:
#             return None
#
#     def stop(self):
#         self.stopped = True
#
#     def isAvailable(self):
#         return self.gotCam
#
#     def setNotAvailable(self):
#         if self.debugNotAvailable:
#             print(f"{datetime.now().strftime('%H:%M:%S.%f')} - In StreamVideoCam.{self.name}.setNotAvailable()")
#         self.gotCam = False
#
#     def getCam(self, block = True, timeout = 1):
#         if not self.gotCam:
#             if self.debugGetCam:
#                 print(f"{datetime.now().strftime('%H:%M:%S.%f')} - No cam available in StreamVideoCam.{self.name}")
#             return None
#         return self.cam
#
#     def doWork(self, block = True, timeout = 5):
#         if self.debugWork:
#             print(f"{datetime.now().strftime('%H:%M:%S.%f')} - Entered StreamVideoCam.{self.name}.doWork()")
#         while (not self.stopped) or (self.processingQueue.qsize() > 0):
#             if self.debugWork:
#                 print(f"{datetime.now().strftime('%H:%M:%S.%f')} - About to call StreamVideoCam.{self.name}.waitForRenew()")
#             anItem = self.waitForRenew(block, timeout)
#             if self.debugWork:
#                 print(f"{datetime.now().strftime('%H:%M:%S.%f')} - anItem in StreamVideoCam.{self.name} is {anItem}")
#             if anItem is not None:
#                 try:
#                     if self.cam is not None:
#                         if self.debugWork:
#                             print(f"{datetime.now().strftime('%H:%M:%S.%f')} - Releasing previous cam resources in StreamVideoCam.{self.name}")
#                         self.cam.release()
#                     if self.mediaService is None:
#                         if self.camera is None:
#                             # create the camera object here; code to be included later
#                             pass
#                         self.mediaService = self.camera.create_media_service()
#                     videoUri = self.mediaService.GetStreamUri(self.streamSetup)
#                     videoRTSP = videoUri.Uri
#                     finalRTSP = videoRTSP.replace(
#                         'rtsp://', 'rtsp://{}:{}@'.format(
#                             'admin',
#                             'abcd1234'), 1)
#                     self.cam = cv2.VideoCapture(finalRTSP)  # the video camera object is stored in this
#                     if self.cam is not None:
#                         self.gotCam = True  # signifies that the video camera object has been created or renewed
#                     else:
#                         self.gotCam = False # signifies that the video camera object has not yet been created or renewed
#                     if self.debugRecreate:
#                         print(
#                             f"{datetime.now().strftime('%H:%M:%S.%f')} - In StreamVideoCam.{self.name}, recreated cam. gotCam is {self.gotCam}, and cam is {self.cam}")
#                 except:
#                     self.cam = None
#                     self.gotCam = False
#                     if self.debugRecreate:
#                         print(f"{datetime.now().strftime('%H:%M:%S.%f')} - In StreamVideoCam.{self.name}, could not create cam")
#                     time.sleep(5)
#                     self.renew()
#
#     def release(self):
#         self.gotCam = False
#         self.cam.release()
#
#     def run(self):
#         if self.debugInit:
#             print(f"{datetime.now().strftime('%H:%M:%S.%f')} - Entered StreamVideoCam.{self.name}.run()")
#         if not self.started:
#             self.started = True
#             self.stopped = False
#             self.doWork(timeout=5)
#
# def getCam(substream1 : StreamVideoCam, substream2 : StreamVideoCam):
#     if substream1 is not None:
#         currentCam = substream1.getCam()
#         if currentCam is not None:
#             if whichStream_Debug:
#                 print(f"{datetime.now().strftime('%H:%M:%S.%f')} - returning SUBSTREAM1")
#             return "SUB1", currentCam
#     if substream2 is not None:
#         currentCam = substream2.getCam()
#         if currentCam is not None:
#             if whichStream_Debug:
#                 print(f"{datetime.now().strftime('%H:%M:%S.%f')} - returning SUBSTREAM2")
#             return "SUB2", currentCam
#     if mainDebug:
#         print(f"{datetime.now().strftime('%H:%M:%S.%f')} - did not find any CAM")
#     return "None", None
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
# imageSizeReduction = 4
# printCameraDetails = False
# printProfiles = False
# printStreams = False
# printHDConfigurations = False
# printPTZConfig = False
# printPTZNode = False
# printPTZServiceCapabilities = False
# printPresets = False
# getSnapshotDetails = False
# printSnapshotUri = False
# printVideoUri = True
#
# def getIP_Path_Params_Query_Fragments(url):
#     parsed = urlparse(url)
#     # print(parsed)
#     params = parse_qs(parsed.query)
#     # print(params)
#     path_components = list(filter(bool, parsed.path.split('/')))
#     IP = parsed.scheme + '://' + parsed.netloc
#     Path = parsed.path
#     Query = parsed.query
#     Fragments = parse_qs(parsed.fragment)
#     return IP, Path, params, Query, Fragments
#     # print(path_components)
#     # print(parsed.netloc)
#     # print(socket.gethostbyname(parsed.netloc))
#
# def printServicesFromURL(url = 'http://www.soapclient.com/xml/soapresponder.wsdl'):
#
#     print('Entered printServicesFromURL()')
#
#     client = Client(wsdl=url)
#     print(client)
#
#     for service in client.wsdl.services.values():
#         print("service:", service.name)
#         for port in service.ports.values():
#             operations = sorted(
#                 port.binding._operations.values(),
#                 key=operator.attrgetter('name'))
#
#             for operation in operations:
#                 print("method :", operation.name)
#                 print("  input :", operation.input.signature())
#                 print("  output:", operation.output.signature())
#                 print
#         print
#     client.transport.session.close()
#
#
# def printServiceFromWSDL(service = None):
#
#     if service is None:
#         return
#
#     print('Entered printServicesFromWSDL()')
#
#     interface = parseElements(service)
#     pprint(interface)
#
#     # print("service:", service.name)
#     # for port in service.ports.values():
#     #     operations = sorted(
#     #         port.binding._operations.values(),
#     #         key=operator.attrgetter('name'))
#     #
#     #     for operation in operations:
#     #         print("method :", operation.name)
#     #         print("  input :", operation.input.signature())
#     #         print("  output:", operation.output.signature())
#     #         print
#
# def parseElements(elements):
#     all_elements = {}
#     for name, element in elements:
#         all_elements[name] = {}
#         all_elements[name]['optional'] = element.is_optional
#         if hasattr(element.type, 'elements'):
#             all_elements[name]['type'] = parseElements(
#                 element.type.elements)
#         else:
#             all_elements[name]['type'] = str(element.type)
#
#     return all_elements
#
# def prettyPrintWSDLFromURL(wsdl = 'http://www.soapclient.com/xml/soapresponder.wsdl'):
#     if wsdl == None:
#         return
#     # header = zeep.xsd.Element(
#     #     "Header",
#     #     zeep.xsd.ComplexType(
#     #         [
#     #             zeep.xsd.Element(
#     #                 "{http://www.w3.org/2005/08/addressing}Action", zeep.xsd.String()
#     #             ),
#     #             zeep.xsd.Element(
#     #                 "{http://www.w3.org/2005/08/addressing}To", zeep.xsd.String()
#     #             ),
#     #         ]
#     #     ),
#     # )
#     # set the header value from header element
#     # header_value = header(Action=method_url, To=service_url)
#     client = Client(wsdl)
#     defaultHeader = {
#         "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/51.0.2704.103 Safari/537.36"
#     }
#     client.set_default_soapheaders(headers = defaultHeader)
#     interface = {}
#     for service in client.wsdl.services.values():
#         interface[service.name] = {}
#         for port in service.ports.values():
#             interface[service.name][port.name] = {}
#             operations = {}
#             for operation in port.binding._operations.values():
#                 operations[operation.name] = {}
#                 operations[operation.name]['input'] = {}
#                 elements = operation.input.body.type.elements
#                 operations[operation.name]['input'] = parseElements(elements)
#                 operations[operation.name]['output'] = {}
#                 elements = operation.output.body.type.elements
#                 operations[operation.name]['output'] = parseElements(elements)
#             interface[service.name][port.name]['operations'] = operations
#     pprint(interface)
#     client.transport.session.close()
#
# def getZeepClientAfterBasicHttpAuthetication(wsdlURL, username, password):
#     try:
#       wsdl = wsdlURL
#       session = Session()
#       session.auth = HTTPBasicAuth(username, password)
#       client = Client(wsdl=wsdl, transport=Transport(session=session))
#       return client
#     except Fault as fault:
#       parsed_fault_detail = client.wsdl.types.deserialize(fault.detail[0])
#       return parsed_fault_detail
#
# def closeClient(client):
#     client.transport.session.close()
#
# def get_service(client, translation):
#   if translation:
#     service_binding = client.service._binding.name
#     service_address = client.service._binding_options["address"]
#     return client.create_service(
#       service_binding,
#       service_address.replace(*translation, 1))
#   else:
#     return client.service
#
# profile2StreamMapping = {}
# viableMediaProfiles = []
# hdConfigurations = []
# ptzPresets = []
#
# def media_profile_configuration():
#     '''
#     A media profile consists of configuration entities such as video/audio
#     source configuration, video/audio encoder configuration,
#     or PTZ configuration. This use case describes how to change one
#     configuration entity which has been already added to the media profile.
#     '''
#
#     # Create the media service
#     # mycam = ONVIFCamera('169.254.29.130', 80, 'admin', 'abcd1234', wsdl_dir = 'C:/Users/INTEL/AppData/Roaming/Python/Python310/site-packages/wsdl')
#
#     # printServicesFromURL()
#     # print("-------------------------")
#
#     # mycam = ONVIFCamera('169.254.29.130', 80, 'admin', 'abcd1234', wsdl_dir='D:\KEMS_CotterPin_GC_QRC_T\Lib\site-packages\wsdl') # for PTZ camera
#     # mycam = ONVIFCamera('169.254.91.212', 80, 'admin', 'abcd1234', wsdl_dir='D:\KEMS_CotterPin_GC_QRC_T\Lib\site-packages\wsdl')
#     mycam = ONVIFCamera('192.168.1.100', 80, 'admin', 'abcd1234',
#                         wsdl_dir='C:/CosTheta/GelTec/onvif/ver10/')
#
#     if debugCV2Info:
#         print(cv2.getBuildInformation())
#     # mycam = ONVIFCamera('169.254.29.130', 80, 'admin', 'abcd1234')
#     # mycam = ONVIFCamera('192.168.1.6', 80, 'admin', 'abcd1234',
#     #                     wsdl_dir='/home/costheta/costheta_env/Lib/site-packages/wsdl')
#     if printCameraDetails:
#         print(f'camera is {mycam} and is of type {type(mycam)}')
#     media_service = mycam.create_media_service()
#
#     print(f'media service is {media_service} and is of type {type(media_service)}')
#     print(f'camera is {mycam} and is of type {type(mycam)}')
#
#     if printCameraDetails:
#         print(f'media service is {media_service} and is of type {type(media_service)}')
#         print('====================================')
#
#     config = mycam.media.GetMetadataConfigurations()[0]
#     config.Analytics = True
#     config_dict = helpers.serialize_object(config, dict)
#
#     # print(f"config_dict is of type {type(config_dict)} and is ...")
#     # print(config_dict)
#     if printCameraDetails:
#         for i, aConfig in enumerate(list(config_dict.items())):
#             print(f'Config {i} is {aConfig}')
#         print('====================================')
#
#     req = mycam.media.create_type('SetMetadataConfiguration')
#     req.Configuration = config
#     req.ForcePersistence = True
#     req_dict = helpers.serialize_object(req, dict)
#     if printCameraDetails:
#         for i, aReq in enumerate(list(req_dict.items())):
#             print(f'Request {i} is {aReq}')
#         print('====================================')
#
#     mycam.media.SetMetadataConfiguration(req)
#     # print('====================================')
#     profilez = mycam.media.GetProfiles()
#     # print(f"profilez is of type {type(profilez)} and is ...")
#     # print(profilez)
#     # profilez_dict = helpers.serialize_object(profilez, dict)
#     if printCameraDetails:
#         for i, profile in enumerate(profilez):
#             print(f'Profile {i} is {profile.token}:')
#             # print('profile %s' % profile.token)
#             if not hasattr(profile, 'MetadataConfiguration'):
#                 mycam.media.AddMetadataConfiguration({
#                     'ProfileToken': profile.token,
#                     'ConfigurationToken': config.token,
#                 })
#             resp = mycam.media.GetStreamUri({
#                 'StreamSetup': {'Stream': 'RTP-Unicast', 'Transport': {'Protocol': 'RTSP'}},
#                 'ProfileToken': profile.token,
#             })
#             # print(f"resp is of type {type(resp)} ans is {resp}")
#             print(f'stream uri for this profile is {resp.Uri}')
#             # print('-------------------------------------')
#         print('====================================')
#
#     # Get Hostname
#     if printCameraDetails:
#         print('====================================')
#         resp = mycam.devicemgmt.GetHostname()
#         print('My camera`s hostname: ' + str(resp.Name))
#         print('====================================')
#
#     # printServiceFromWSDL(media_service)
#     # print("-------------------------")
#
#
#     # prettyPrintWSDLFromURL()
#     # print("-------------------------")
#
#     ## get the streamUri
#     # profiles = media_service.GetProfiles()
#     # printServiceFromWSDL(profiles)
#     # print("-------------------------"   )
#
#     # aProfile = media_service.GetProfile('PTZToken')
#     # print(aProfile)
#     # media2_service = mycam.create_media_service()
#     ## get the streamUri
#     profiles = media_service.GetProfiles()
#     if printCameraDetails:
#         print('====================================')
#         for i, aProfile in enumerate(profiles):
#             print(f'Profile {i} obtained through media service is {aProfile}:')
#             print('-------------------------------------')
#         print('====================================')
#
#     # print(f'profiles are {profiles}')
#
#     for profile in profiles:
#         # print(profile.token)
#         viableMediaProfiles.append(profile)
#         # o = media_service.create_type('GetStreamUri')
#         # print(f'Object is {o}')
#         # o.ProfileToken = profile.token
#         # # o.Protocol = 'RTSP'
#         # uri = media_service.GetStreamUri(o)
#         # # dic = {'token': profile.token,
#         # #        'rtsp': uri}
#         # profile2StreamMapping[profile.token] = uri
#         # print(dic)
#
#     '''
#     # print('====================================')
#     if printProfiles:
#         for i, aProfile in enumerate(viableMediaProfiles):
#             print(f"Media profile {i} is {aProfile}")
#             print("---------------------------------")
#         # print(f"The current media profiles are {viableMediaProfiles}")
#     # if printStreams:
#     #     print(f'Profile-2-Stream Mapping is {profile2StreamMapping}')
#     print('====================================')
#     '''
#
#     if getSnapshotDetails:
#         print(f'About to get snapshot uri for token {viableMediaProfiles[1].token}')
#         print(f'media_service is of type {type(media_service)}')
#         imageUri = media_service.GetSnapshotUri(viableMediaProfiles[1].token)
#         if printSnapshotUri:
#             print(f'snapshot uri is {imageUri}')
#
#         IP,_,_,_,_ = getIP_Path_Params_Query_Fragments(imageUri.Uri)
#         password_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
#         password_mgr.add_password(None, IP, 'admin', 'abcd1234')
#         handler1 = urllib.request.HTTPBasicAuthHandler(password_mgr)
#         handler2 = urllib.request.HTTPDigestAuthHandler(password_mgr)
#         opener = urllib.request.build_opener(handler1, handler2)
#         urllib.request.install_opener(opener)
#         timeout = 10
#         socket.setdefaulttimeout(timeout)
#
#         req = urllib.request.Request(imageUri.Uri)
#         url_response = urllib.request.urlopen(req)
#         img_array = np.asarray(bytearray(url_response.read()), dtype=np.uint8)
#         img = cv2.imdecode(img_array, -1) # 'Load it as it is'
#         # start_time = datetime.now()
#         start_time = time.time() * 1000
#         count = 1
#         for i in range(count):
#             reduced_image = cv2.resize(img, (img.shape[1] // imageSizeReduction, img.shape[0] // imageSizeReduction),
#                                       interpolation=cv2.INTER_AREA)
#         # end_time = datetime.now()
#         end_time = time.time() * 1000
#         print(f"Time taken to crunch anImage is {(end_time - start_time) / count} ms")
#         # titleDisplay = str(imageUri) + f' : {img.shape[0]} x {img.shape[1]}'
#         titleDisplay = f'{reduced_image.shape[0]} x {reduced_image.shape[1]}'
#         cv2.imshow(titleDisplay, reduced_image)
#         if cv2.waitKey() & 0xff == 27: quit()
#
#
#     ## get video info , 'h265' or 'h264', 'width' 'height' 'gop' ....
#     configurations = media_service.GetVideoEncoderConfigurations()
#     # print(configurations)
#
#
#     # **************************************************
#     for configuration in configurations:
#         if configuration['Encoding'].lower() == 'h264' or configuration['Encoding'].lower() == 'h265':
#             width = configuration['Resolution']['Width']
#             height = configuration['Resolution']['Height']
#             currentEncoding = configuration['Encoding']
#             dic = {'token': configuration['token'],
#                    'encoding': configuration['Encoding'],
#                    'ratio': "{}*{}".format(width, height),
#                    'fps': configuration['RateControl']['FrameRateLimit'],
#                    'bitrate': configuration['RateControl']['BitrateLimit'],
#                    'gop': configuration[currentEncoding]['GovLength'],
#                    # 'profile': configuration['Profile'],
#                    'name': configuration['Name'],
#                    'quality': configuration['Quality']}
#             hdConfigurations.append(dic)
#         else:
#             dic = {'token': configuration['Name'], 'encoding': configuration['Encoding']}
#
#         # print(dic)
#     if printHDConfigurations:
#         print(f"HD configurations are {hdConfigurations}")
#
#     if printCameraDetails:
#         # Get system date and time
#         dt = mycam.devicemgmt.GetSystemDateAndTime()
#         # print(dt)
#         tz = dt.TimeZone
#         year = dt.UTCDateTime.Date.Year
#         hour = dt.UTCDateTime.Time.Hour
#         # print(f'. Hour {hour} of year {year} in timezome {tz.TZ}')
#
#         # time_params = mycam.devicemgmt.create_type('SetSystemDateAndTime')
#         # print(time_params)
#         # time_params.DateTimeType = 'Manual'
#         # time_params.DaylightSavings = True
#         #
#         # tzone = pytz.timezone('Asia/Calcutta')
#         # aware = datetime.now(tzone)
#         # print('Timezone Aware:', aware)
#         # time_params.TimeZone = str(tzone)
#         #
#         # time_dict = {}
#         # hms = {}
#         # hms['Hour'] = aware.hour
#         # hms['Minute'] = aware.minute
#         # hms['Second'] = aware.second
#         # time_dict['Time'] = hms
#         # ymd = {}
#         # ymd['Year'] = aware.year
#         # ymd['Month'] = aware.month
#         # ymd['Day'] = aware.day
#         # time_dict['Date'] = ymd
#         # time_params.UTCDateTime = str(time_dict)
#         # mycam.devicemgmt.SetSystemDateAndTime(time_params)
#
#         # dt = mycam.devicemgmt.create_type('SetSystemDateAndTime')
#         # dt.DateTimeType = 'Manual'
#         # dt.DaylightSavings = True
#         # tzone = pytz.timezone('Asia/Calcutta')
#         # aware = datetime.now(tzone)
#         # print('Timezone Aware:', aware)
#         # tzo =  str(tzone)
#         # print(tzo)
#         # # dt.TimeZone.TZ = tzo
#         #
#         # dt.UTCDateTime.Time.Hour = aware.hour
#         # dt.UTCDateTime.Time.Minute = aware.minute
#         # dt.UTCDateTime.Time.Second = aware.second
#         # dt.UTCDateTime.Date.Year = aware.year
#         # dt.UTCDateTime.Date.Month = aware.month
#         # dt.UTCDateTime.Date.Day = aware.day
#         # mycam.devicemgmt.SetSystemDateAndTime(dt)
#         #
#         # dt = mycam.devicemgmt.GetSystemDateAndTime()
#         # tz = dt.TimeZone
#         # year = dt.UTCDateTime.Date.Year
#         # hour = dt.UTCDateTime.Time.Hour
#         # print(f'. Reset time is Hour {hour} of year {year} in timezome {tz}')
#
#     # event_service = mycam.create_events_service()
#     # print(event_service.GetEventProperties())
#
#     '''
#
#     # Create ptz service
#     ptz_service = mycam.create_ptz_service()
#     # Get ptz configuration
#     ptzConfigs = ptz_service.GetConfigurations()
#     # print(ptzConfigs)
#     aPTZConfig = ptz_service.GetConfiguration('PTZToken')
#     if printPTZConfig:
#         print(f"One of the PTZ configurations is {aPTZConfig}")
#
#     ptzConfigOptions = ptz_service.GetConfigurationOptions('PTZToken')
#     if printPTZConfig:
#         print(f'The configuration options are {ptzConfigOptions}')
#
#     ptzNodes = ptz_service.GetNodes()
#     if printPTZNode:
#         print(f'The PTZ Nodes are {ptzNodes}')
#
#     serviceCapabilities = ptz_service.GetServiceCapabilities()
#     if printPTZServiceCapabilities:
#         print(f'The Service Capabilities are {serviceCapabilities}')
#
#     definedPtzPresets = ptz_service.GetPresets(viableMediaProfiles[0].token)
#     for preset in definedPtzPresets:
#         if (preset.PTZPosition.PanTilt.x in [0.0,1.0]) and (preset.PTZPosition.PanTilt.y in [0.0,1.0]):
#             pass
#         else:
#             ptzPresets.append(preset)
#     if printPresets:
#         print(f'The main presets are {ptzPresets}')
#
#     currentStatus = ptz_service.GetStatus(viableMediaProfiles[0].token)
#     print(currentStatus)
#
#     '''
#     if getSnapshotDetails:
#         imageUri = media_service.GetSnapshotUri(viableMediaProfiles[0].token)
#         if printSnapshotUri:
#             print(f'Image URI is {imageUri}')
#
#         IP,_,_,_,_ = getIP_Path_Params_Query_Fragments(imageUri.Uri)
#         password_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
#         password_mgr.add_password(None, IP, 'admin', 'abcd1234')
#         handler1 = urllib.request.HTTPBasicAuthHandler(password_mgr)
#         handler2 = urllib.request.HTTPDigestAuthHandler(password_mgr)
#         opener = urllib.request.build_opener(handler1, handler2)
#         urllib.request.install_opener(opener)
#         timeout = 10
#         socket.setdefaulttimeout(timeout)
#
#         '''
#         print('----------------------------------')
#         request = ptz_service.create_type('GotoPreset')
#         print(request)
#         request.ProfileToken = str(viableMediaProfiles[0].token)
#         request.PresetToken = str(ptzPresets[2].token)
#         print(request)
#         response = ptz_service.GotoPreset(request)
#         print(response)
#
#         try:
#             time.sleep(8)
#         except Exception as e:
#             pass
#         '''
#
#         print("Just after sleeping")
#         req = urllib.request.Request(imageUri.Uri)
#         url_response = urllib.request.urlopen(req)
#         img_array = np.asarray(bytearray(url_response.read()), dtype=np.uint8)
#         img = cv2.imdecode(img_array, -1) # 'Load it as it is'
#         start_time = time.time() * 1000
#         count = 1
#         for i in range(count):
#             reduced_image = cv2.resize(img, (img.shape[1] // imageSizeReduction, img.shape[0] // imageSizeReduction),
#                                       interpolation=cv2.INTER_AREA)
#         end_time = time.time() * 1000
#         print(f"Time taken to crunch anImage is {(end_time - start_time) / count} ms")
#         titleDisplay = f'{reduced_image.shape[0]} x {reduced_image.shape[1]}'
#         cv2.imshow(titleDisplay, reduced_image)
#         if cv2.waitKey() & 0xff == 27: quit()
#
#
#     '''
#     '''
#     framerate = 16
#     timeBetweenFrames = 1 / framerate
#     consecutiveNoVideoCounter = 0
#     consecutiveNoVideoIncidents = 0
#     print(f'Time between frames is {timeBetweenFrames} sec')
#
#     streamSetup1 = {
#         'StreamSetup': {
#             'Stream': 'RTP-Unicast',
#             'Transport': {
#                 'Protocol': 'TCP'
#             }
#         },
#         'ProfileToken': viableMediaProfiles[0].token
#     }
#
#     streamSetup2 = {
#         'StreamSetup': {
#             'Stream': 'RTP-Unicast',
#             'Transport': {
#                 'Protocol': 'TCP'
#             }
#         },
#         'ProfileToken': viableMediaProfiles[1].token
#     }
#
#     # videoUri1 = media_service.GetStreamUri(streamSetup1)
#     # if printSnapshotUri:
#     #     print(f'Video URI 1 details are {videoUri1}')
#     # videoRTSP1 = videoUri1.Uri
#     # _input1 = videoRTSP1.replace(
#     #     'rtsp://', 'rtsp://{}:{}@'.format(
#     #         'admin',
#     #         'abcd1234'), 1)
#     # if printSnapshotUri:
#     #     print(f'Video URI 1 is {_input1}')
#     #
#     # videoUri2 = media_service.GetStreamUri(streamSetup2)
#     # if printSnapshotUri:
#     #     print(f'Video URI 2 details are {videoUri2}')
#     # videoRTSP2 = videoUri2.Uri
#     # _input2 = videoRTSP2.replace(
#     #     'rtsp://', 'rtsp://{}:{}@'.format(
#     #         'admin',
#     #         'abcd1234'), 1)
#     # if printSnapshotUri:
#     #     print(f'Video URI 2 is {_input2}')
#
#     substreamVideoCam2 = StreamVideoCam(mycam, media_service, streamSetup2, "SubStream2")
#     time.sleep(3)
#     substreamVideoCam1 = StreamVideoCam(mycam, media_service, streamSetup2, "SubStream1")
#     time.sleep(3)
#     substreamVideoCam2.start()
#     substreamVideoCam1.start()
#
#     consecutiveNoVideoSwitchTrigger = 3
#     counter = 5000
#     totalElapsedTime = 0
#     totalMajorWaitingTime = 0
#     noWaitingCount = 0
#     majorNoWaitCount = 0 # > 3 * timeframe
#     noVideoCount = 0
#     noVideoSub2Trigger = False
#     bothNotAvailableCount = 0
#     actualCount = 0
#     consecutive_SUBS1 = 0
#     consecutive_SUBS2 = 0
#     consecutive_SUBS_Trigger_For_SUBS1_Renew = 1000
#     consecutive_SUBS_Multiple_For_SUBS2_Renew = 100
#     consecutive_SUBS2_Trigger = 750
#     voluntarySwitchTrigger = 30
#     voluntarySwitch = False
#     voluntarySwitchReps = 3
#     previousStream = "SUB1"
#     for actualCount in range(counter):
#         start_time = time.time()
#         camType, currentCam = getCam(substreamVideoCam1, substreamVideoCam2)
#         if ((not voluntarySwitch) and (camType == "SUB1") and (previousStream == "SUB1")):
#             consecutive_SUBS1 += 1
#             if substreamVideoCam2.isAvailable() and substreamVideoCam1.isAvailable() and (consecutive_SUBS1 % consecutive_SUBS_Multiple_For_SUBS2_Renew == 0):
#                 noVideoSub2Trigger = False
#                 consecutive_SUBS2 = 0
#                 if mainDebug:
#                     print(f"{datetime.now().strftime('%H:%M:%S.%f')} - About to renew SUB2 due to either consecutive_SUBS_Multiple_For_SUBS2_Renew trigger")
#                 substreamVideoCam2.setNotAvailable()
#                 substreamVideoCam2.renew()
#         if ((not voluntarySwitch) and (camType == "SUB2") and (previousStream == "SUB2")):
#             consecutive_SUBS2 += 1
#         if ((not voluntarySwitch) and (camType == "SUB2") and (previousStream == "SUB1")):
#             consecutive_SUBS1 = 0
#             consecutive_SUBS2 += 1
#             if mainDebug:
#                 print(f"{datetime.now().strftime('%H:%M:%S.%f')} - Switching to SUB2")
#         if (camType == "SUB1" and previousStream == "SUB2"):
#             consecutive_SUBS1 += 1
#             if mainDebug:
#                 print(f"{datetime.now().strftime('%H:%M:%S.%f')} - Switching to SUB1")
#             if substreamVideoCam2.isAvailable() and substreamVideoCam1.isAvailable() and ((consecutive_SUBS2 > consecutive_SUBS2_Trigger) or (noVideoSub2Trigger)):
#                 noVideoSub2Trigger = False
#                 consecutive_SUBS2 = 0
#                 if mainDebug:
#                     print(f"{datetime.now().strftime('%H:%M:%S.%f')} - About to renew SUB2 due to either no_videos_trigger OR consecutive_sub2_trigger")
#                 substreamVideoCam2.setNotAvailable()
#                 substreamVideoCam2.renew()
#         if (camType == "None"):
#             bothNotAvailableCount += 1
#         previousStream = camType
#         ret = False
#         if currentCam is not None:
#             try:
#                 ret, frame = currentCam.read()
#             except:
#                 if camType == "SUB1":
#                     if substreamVideoCam1.isAvailable() and substreamVideoCam2.isAvailable():
#                         if mainDebug:
#                             print(f"{datetime.now().strftime('%H:%M:%S.%f')} - About to renew SUB1 as return value is False")
#                         substreamVideoCam1.setNotAvailable()
#                         substreamVideoCam1.renew()
#                 # else:
#                 #     if mainDebug:
#                 #         print(f"{datetime.now().strftime('%H:%M:%S.%f')} - About to renew SUB2")
#                 #     substreamVideoCam2.setNotAvailable()
#                 #     substreamVideoCam2.renew()
#             if ret == 1:
#                 consecutiveNoVideoCounter = 0
#                 if camType == "SUB1":
#                     try:
#                         reduced_image = cv2.resize(frame, (frame.shape[1] // 2, frame.shape[0] // 2),
#                                                   interpolation=cv2.INTER_AREA)
#                     except:
#                         pass
#                 elif camType == "SUB2":
#                     try:
#                         # reduced_image = cv2.resize(frame, (frame.shape[1] // 4, frame.shape[0] // 3),
#                         #                           interpolation=cv2.INTER_AREA)
#                         reduced_image = cv2.resize(frame, (frame.shape[1] // 2, frame.shape[0] // 2),
#                                                   interpolation=cv2.INTER_AREA)
#                     except:
#                         pass
#                 cv2.imshow('frame', reduced_image)
#             else:
#                 noVideoCount += 1
#                 print(f"{datetime.now().strftime('%H:%M:%S.%f')} - no video")
#                 consecutiveNoVideoCounter += 1
#                 if (consecutiveNoVideoCounter == consecutiveNoVideoSwitchTrigger) :
#                     consecutiveNoVideoIncidents += 1
#                     consecutiveNoVideoCounter = 0
#                     if camType == "SUB1":
#                         if substreamVideoCam1.isAvailable() and substreamVideoCam2.isAvailable():
#                             if mainDebug:
#                                 print(f"{datetime.now().strftime('%H:%M:%S.%f')} - About to renew SUB1 due to consecutive No Videos")
#                             substreamVideoCam1.setNotAvailable()
#                             substreamVideoCam1.renew()
#                     else:
#                         noVideoSub2Trigger = True
#         if (consecutive_SUBS1 > consecutive_SUBS_Trigger_For_SUBS1_Renew) :
#             consecutive_SUBS1 = 0
#             if substreamVideoCam1.isAvailable() and substreamVideoCam2.isAvailable():
#                 if mainDebug:
#                     print(f"{datetime.now().strftime('%H:%M:%S.%f')} - About to renew SUB1 due to consecutive_frames trigger")
#                 substreamVideoCam1.setNotAvailable()
#                 substreamVideoCam1.renew()
#         current_time = time.time()
#         elapsed_time = current_time - start_time
#         print(f"{datetime.now().strftime('%H:%M:%S.%f')} - {actualCount + 1} : {elapsed_time * 1000}")
#         wait_time = timeBetweenFrames - elapsed_time
#         if (wait_time > 0):
#             # print(wait_time)
#             time.sleep(wait_time)
#             totalElapsedTime = totalElapsedTime + elapsed_time
#         else:
#             noWaitingCount += 1
#             totalMajorWaitingTime += elapsed_time
#             # Precautionary measure - If no waiting time => cam might be slowing down. Better to renew
#             if (actualCount > 5) and (elapsed_time > 5 * timeBetweenFrames):
#                 print("Major no waiting time")
#                 majorNoWaitCount += 1
#                 if camType == "SUB1":
#                     if substreamVideoCam1.isAvailable() and substreamVideoCam2.isAvailable():
#                         if mainDebug:
#                             print(f"{datetime.now().strftime('%H:%M:%S.%f')} - About to renew SUB1 due to high waiting time")
#                         substreamVideoCam1.setNotAvailable()
#                         substreamVideoCam1.renew()
#             else:
#                 totalElapsedTime = totalElapsedTime + elapsed_time
#                 print("No waiting time")
#         if ret == 1:
#             if cv2.waitKey(1) & 0xFF == ord('q'):
#                 break
#     print(f"Average time to get and reduce = {totalElapsedTime * 1000 / max(1,(actualCount - majorNoWaitCount))} ms")
#     print(f"Total of no wait time incidents = {noWaitingCount}")
#     print(f"Total of major no wait time incidents = {majorNoWaitCount}")
#     print(f"Average waiting time for major waits = {totalMajorWaitingTime * 1000 / max(1,majorNoWaitCount)} ms")
#     print(f"Total of no video incidents = {noVideoCount}")
#     print(f"Total of consecutive no video incidents = {consecutiveNoVideoIncidents}")
#     print(f"Total of both cams not available incidents = {bothNotAvailableCount}")
#     substreamVideoCam1.release()
#     substreamVideoCam2.release()
#     substreamVideoCam1.stop()
#     substreamVideoCam2.stop()
#     cv2.destroyAllWindows()
#
#     '''
#     # As HTTP is a slow protocol, the time to get an anImage through the snapshotUri is 180 - 200 ms, which is too slow.
#     # So, abandon this method
#     framerate = 20
#     timeBetweenFrames = 1000 // framerate
#     while (True):
#         start_time = time.time() * 1000
#         # req = urllib.request.Request(imageUri.Uri)
#         url_response = urllib.request.urlopen(req)
#         img_array = np.asarray(bytearray(url_response.read()), dtype=np.uint8)
#         img = cv2.imdecode(img_array, -1)  # 'Load it as it is'
#         try:
#             reduced_image = cv2.resize(img, (img.shape[1] // 2, img.shape[0] // 2),
#                                       interpolation=cv2.INTER_AREA)
#             reduced_image = cv2.resize(img, (img.shape[1] // 2, img.shape[0] // 2),
#                                       interpolation=cv2.INTER_AREA)
#         except:
#             pass
#         current_time = time.time() * 1000
#         elapsed_time = current_time - start_time
#         print(elapsed_time)
#         # cv2.imshow('frame', reduced_image)
#         wait_time = timeBetweenFrames - elapsed_time
#         if (wait_time > 0):
#             # print(wait_time)
#             time.sleep(wait_time / 1000)
#         else:
#             # pass
#             print("No waiting time")
#         # if cv2.waitKey(1) & 0xff == ord('q'):
#         #     break
#     '''
#
# # ptz_service.GotoPreset(viableMediaProfiles[0].token, ptzPresets[0].Name)
#
#
#
# #     print(media_service.to_dict(media_service))
# #     streamURI = media_service.GetStreamUri({'StreamSetup': {'Stream': 'RTP-Unicast', 'Transport': 'RTSP'}})
# #     print(f'Stream URI is {streamURI}')
# #     ptz = mycam.create_ptz_service()
# #     # print(ptz)
# #
# #     profiles = media_service.GetProfiles()
# #     # print(profiles)
# #     # print(f'no of profiles are {len(profiles)}')
# #     # print(f'first profile is {profiles[0]}')
# #
# #     # for key in profiles[0]:
# #     #     print(f'Value of {key} is {profiles[0][key]}')
# #
# #     # Use the first profile and Profiles have at least one
# #     token = profiles[0].token
# #
# #     # Get all video encoder configurations
# #     configurations_list = media_service.GetVideoEncoderConfigurations()
# #     # print("-------------------------------------------------")
# #     # print(f'Video Encoder Configurations are {configurations_list}')
# #
# #     # Use the first profile and Profiles have at least one
# #     video_encoder_configuration = configurations_list[0]
# #     # print("-------------------------------------------------")
# #     # print(f'First Video Encoder Configuration is {video_encoder_configuration}')
# #
# #     # Get video encoder configuration options
# #     options = media_service.GetVideoEncoderConfigurationOptions({'ProfileToken':token})
# #     # print("-------------------------------------------------")
# #     # print(f'Video encoder configuration of profile {token} is {options}')
# #
# #     # Setup stream configuration
# #     video_encoder_configuration.Encoding = 'H264'
# #     # print(f'H264 is {options.H264}')
# #     # print(f'QualityRange is {options.QualityRange}')
# #
# #     resolutions = options.H264.ResolutionsAvailable
# #     width = 0
# #     height = 0
# #     for resolution in resolutions:
# #         if resolution.Width > width or resolution.Height > height:
# #             width = resolution.Width
# #             height = resolution.Height
# #
# #     # Setup Resolution
# #     video_encoder_configuration.Resolution.Width = width
# #     video_encoder_configuration.Resolution.Height = height
# #     # Setup Quality
# #     video_encoder_configuration.Quality = options.QualityRange.Max
# #     # Setup FramRate
# #     video_encoder_configuration.RateControl.FrameRateLimit = options.H264.FrameRateRange.Max
# #     # Setup EncodingInterval
# #     video_encoder_configuration.RateControl.EncodingInterval = options.H264.EncodingIntervalRange.Min
# #     # Setup Bitrate
# #     if (options.Extension.H264):
# #         video_encoder_configuration.RateControl.BitrateLimit = options.Extension.H264.BitrateRange.Min
# #
# #     # print("-------------------------------------------------")
# #     # print(f'Video width is {video_encoder_configuration.Resolution.Width}, height is {video_encoder_configuration.Resolution.Height},'
# #     #       f'Quality is {video_encoder_configuration.Quality}, Frame rate is {video_encoder_configuration.RateControl.FrameRateLimit},'
# #     #       f'encoding interval is {video_encoder_configuration.RateControl.EncodingInterval}, Bit rate limit is {video_encoder_configuration.RateControl.BitrateLimit}')
# #
# #     # Create request type instance
# #     request = media_service.create_type('SetVideoEncoderConfiguration')
# #     request.Configuration = video_encoder_configuration
# #     # ForcePersistence is obsolete and should always be assumed to be True
# #     request.ForcePersistence = True
# #     # Set the video encoder configuration
# #     media_service.SetVideoEncoderConfiguration(request)
# #     # print("-------------------------------------------------")
# #     # print(f'request object is {request}')
# #
# #     imaging = mycam.create_imaging_service()
# #     # print("-------------------------------------------------")
# #     # print(f'imaging object is {imaging}')
# #     requestGetImaging = imaging.create_type('GetImagingSettings')
# #     video_sources = media_service.GetVideoSources()
# #     requestGetImaging.VideoSourceToken = video_sources[0].token
# #     # print("-------------------------------------------------")
# #     # print(f'requestGetImaging object is {requestGetImaging}')
# #
# #     print("-------------------------------------------------")
# #     print(f'ptz object is {ptz}')
# #     ptzConfig = ptz.GetConfiguration()
# #     print("-------------------------------------------------")
# #     print(f'ptzConfig object is {ptzConfig}')
# #
# #     responseGetImageSettings = imaging.GetImagingSettings(requestGetImaging)
# #     print("-------------------------------------------------")
# #     print(f'requestGetImageSettings object is {responseGetImageSettings}')
# #
# #     requestSetImaging = imaging.create_type('SetImagingSettings')
# #     requestSetImaging.VideoSourceToken = video_sources[0].token
# #     requestSetImaging.ImagingSettings = responseGetImageSettings
# #     requestSetImaging.ImagingSettings.Brightness = 50
# #     print("-------------------------------------------------")
# #     print(f'requestSetImaging object is {requestSetImaging}')
# #     imaging.SetImagingSettings(requestSetImaging)
# #
# if __name__ == '__main__':
#     media_profile_configuration()