from utils.SuppressedExceptions import noteSuppressed   # logs exceptions that are caught and ignored
import copy
import Crypto
from Crypto import Cipher
from Crypto.Cipher import AES
# from easygui import passwordbox
from frontend.SimplePopups import *
from datetime import *
import os
import time
# import logging
import subprocess
import sys
import signal

from logutils.SlaveLoggers import *
from logutils.Logger import *
from BaseUtils import *
from Configuration import *
CosThetaConfigurator.getInstance()

# ***********************************************************************************************************
import winreg
import re

class WindowsRegistry:
    """Class WindowsRegistry makes it easy to manipulate Windows registry.

    Methods
    -------
    query_value(full_path : str)
        Check value for existing.

    get_value(full_path : str)
        Get value's data.

    set_value(full_path : str, value : str, value_type='REG_SZ' : str)
        Create a new value with data or set data to an existing value.

    delete_value(full_path : str)
        Delete an existing value.

    query_key(full_path : str)
        Check key for existing.

    delete_key(full_path : str)
        Delete an existing key(only without subkeys).


    Examples:
        WindowsRegistry.set_value('HKCU/Software/Microsoft/Windows/CurrentVersion/Run', 'Program', r'"c:\\Dir1\\program.exe"')
        WindowsRegistry.delete_value('HKEY_CURRENT_USER/Software/Microsoft/Windows/CurrentVersion/Run/Program')
    """
    # @staticmethod
    # def parse_data(full_path):
    #     full_path = re.sub(r'/', r'\\', full_path)
    #     hive = re.sub(r'\\.*$', '', full_path)
    #     if not hive:
    #         raise ValueError('Invalid \'full_path\' param.')
    #     if len(hive) <= 4:
    #         if hive == 'HKLM':
    #             hive = 'HKEY_LOCAL_MACHINE'
    #         elif hive == 'HKCU':
    #             hive = 'HKEY_CURRENT_USER'
    #         elif hive == 'HKCR':
    #             hive = 'HKEY_CLASSES_ROOT'
    #         elif hive == 'HKU':
    #             hive = 'HKEY_USERS'
    #     reg_key = re.sub(r'^[A-Z_]*\\', '', full_path)
    #     reg_key = re.sub(r'\\[^\\]+$', '', reg_key)
    #     reg_value = re.sub(r'^.*\\', '', full_path)
    #
    #     return hive, reg_key, reg_value

    windowsRegistryKey = r'Software\CosTheta\Program'
    base = winreg.HKEY_CURRENT_USER

    @staticmethod
    def query_value(full_path):
        value_list = WindowsRegistry.parse_data(full_path)
        try:
            # with winreg.OpenKey(getattr(winreg, value_list[0]), value_list[1], 0, winreg.KEY_READ) as opened_key:
            #     # opened_key = winreg.OpenKey(getattr(winreg, value_list[0]), value_list[1], 0, winreg.KEY_READ)
            #     winreg.QueryValueEx(opened_key, value_list[2])
            #     return True
            with winreg.OpenKey(WindowsRegistry.base, WindowsRegistry.windowsRegistryKey, 0, winreg.KEY_READ) as opened_key:
                winreg.QueryValueEx(opened_key, value_list[2])
                return True

        except (Exception, Error):
            # print(e)
            return False

    @staticmethod
    def get_value(full_path):
        value_list = WindowsRegistry.parse_data(full_path)
        try:
            with winreg.OpenKey(getattr(winreg, value_list[0]), value_list[1], 0, winreg.KEY_READ) as opened_key:
                value_of_value, value_type = winreg.QueryValueEx(opened_key, value_list[2])
                # winreg.CloseKey(opened_key)
            return value_of_value
        except (Exception, Error):
            # print(e)
            return None

    @staticmethod
    def set_value(full_path, value, value_type='REG_SZ'):
        value_list = WindowsRegistry.parse_data(full_path)
        try:
            winreg.CreateKey(getattr(winreg, value_list[0]), value_list[1])
            with winreg.OpenKey(getattr(winreg, value_list[0]), value_list[1], 0, winreg.KEY_WRITE) as opened_key:
                winreg.SetValueEx(opened_key, value_list[2], 0, getattr(winreg, value_type), value)
                # winreg.CloseKey(opened_key)
            return True
        except (Exception, Error):
            # print(e)
            return False

    @staticmethod
    def delete_value(full_path):
        value_list = WindowsRegistry.parse_data(full_path)
        try:
            with winreg.OpenKey(getattr(winreg, value_list[0]), value_list[1], 0, winreg.KEY_WRITE) as opened_key:
                winreg.DeleteValue(opened_key, value_list[2])
                # winreg.CloseKey(opened_key)
            return True
        except (Exception, Error):
            # print(e)
            return False

    @staticmethod
    def query_key(full_path):
        value_list = WindowsRegistry.parse_data(full_path)
        print(value_list)
        try:
            with winreg.OpenKey(getattr(winreg, value_list[0]), value_list[1] + '\\' + value_list[2], 0, winreg.KEY_READ):
                pass
            return True
        except (Exception, Error):
            # print(e)
            return False

    @staticmethod
    def delete_key(full_path):
        value_list = WindowsRegistry.parse_data(full_path)
        print(value_list)
        try:
            print(getattr(winreg, value_list[0]))
            print(value_list[1] + '\\' + value_list[2])
            winreg.DeleteKey(getattr(winreg, value_list[0]), value_list[1] + '\\' + value_list[2])
            return True
        except (Exception, Error) as e:
            print(e)
            return False

# ***********************************************************************************************************

inProductionRun : bool = (CosThetaConfigurator.getInstance().getApplicationMode() != 'TEST')
# logger :logging.Logger = None

logSource = getFullyQualifiedName(__file__)

# redisConnection = None
# clientRedisConnected = False
# hostname = CosThetaConfigurator.getInstance().getRedisHost()
# port = CosThetaConfigurator.getInstance().getRedisPort()

key1 = "DeepHIVETechNOLO"
key2 = "cOStHETAtECHNOLO"
key3 = "eKdUmArbitBawAAl"
key4 = "faceBoOkGooGLeiB"

certDirectory = "Internal"
certFileName = "c.bin"
currentDateFileName = "cd.bin"
startingDateFileName = "sd.bin"
houmouLimitsFileName = "houmou_L.bin" # Hours of Usage and Months of Usage Limits
houFileName = "hou.bin" # Actual hours of usage

currentWorkingDirectory = get_project_root()
encryptionDirectory = f"{currentWorkingDirectory}/{certDirectory}"
encryptedCertFile = f"{encryptionDirectory}/{certFileName}"
encryptedCurrentDateFile = f"{encryptionDirectory}/{currentDateFileName}"
encryptedStartingDateFile = f"{encryptionDirectory}/{startingDateFileName}"
encryptedLimitsFileName = f"{encryptionDirectory}/{houmouLimitsFileName}"
encryptedHoUFileName = f"{encryptionDirectory}/{houFileName}"

startingTime = None
currentSessionsStartingTime : float = 0.0
mouLimit = 0
houLimit = 0


# def connectToRedis():
#     global redisConnection, clientRedisConnected, hostname, port
#     try:
#         redisConnection = Redis(hostname, port, retry_on_timeout=True)
#         clientRedisConnected = True
#     except:
#         clientRedisConnected = False
#         redisConnection = None

currentProcessPIDs : list = []

def setRegistryValue(value):
    global windowsRegistryKey
    try:
        keyExists = WindowsRegistry.query_key(full_path=windowsRegistryKey)
        if not keyExists:
            print("Key not found")
            operationResult = WindowsRegistry.set_value(full_path=windowsRegistryKey, value=value, value_type='REG_BINARY')
            return operationResult
        else:
            print("Key found")
            try:
                value_list = WindowsRegistry.parse_data(full_path=windowsRegistryKey)
                winreg.CreateKey(getattr(winreg, value_list[0]), value_list[1])
                with winreg.OpenKey(getattr(winreg, value_list[0]), value_list[1], 0, winreg.KEY_WRITE) as opened_key:
                    winreg.SetValueEx(opened_key, value_list[2], 0, getattr(winreg, "REG_BINARY"), value)
                return True
            except (Exception, Error) as e:
                print(e)
                return False
    except Exception:
        return False

def getRegistryValue():
    global windowsRegistryKey
    try:
        return WindowsRegistry.get_value(full_path=windowsRegistryKey)
    except Exception:
        return ""

def stopProcesses(process_pids: list):
    time.sleep(4)
    for pid in process_pids:
        try:
            if psutil.pid_exists(pid):
                os.kill(pid, signal.SIGTERM)
        except Exception:
            noteSuppressed()
    printBoldGreen("All processes shut down. Closing terminal window.")
    try:
        os.system("taskkill /f /im cmd.exe")
    except Exception:
        noteSuppressed()
    sys.exit(0)


def exitProgram():
    hostname = CosThetaConfigurator.getInstance().getRedisHost()
    port = CosThetaConfigurator.getInstance().getRedisPort()
    redisConnection = Redis(hostname, port, retry_on_timeout=True)
    sendStopCommand(redisConnection, aProducer="VerifyIntegrity")
    sys.exit(0)

def run(cmd):
  try:
    return (subprocess.run(cmd, shell=True, capture_output=True, check=True, encoding="utf-8")
            .stdout
            .strip())
  except Exception:
    return None

def verifyExistenceAndTamperingOfAllFiles():
    global logSource, currentProcessPIDs
    encryptedCertFileExists = os.path.isfile(encryptedCertFile)
    encryptedCurrentDateFileExists = os.path.isfile(encryptedCurrentDateFile)
    encryptedStartingDateFileExists = os.path.isfile(encryptedStartingDateFile)
    encryptedLimitsFileNameExists = os.path.isfile(encryptedLimitsFileName)
    encryptedHoUFileNameExists = os.path.isfile(encryptedHoUFileName)
    if not encryptedCertFileExists and not encryptedCurrentDateFileExists and not encryptedStartingDateFileExists and not encryptedLimitsFileNameExists and not encryptedHoUFileNameExists:
        SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                       "None of the certificate files exist. Certificates needed to be created to start a clean installation.",
                                                       Logger.ISSUE)
        SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                       "None of the certificate files exist. Certificates needed to be created to start a clean installation.",
                                                       Logger.ISSUE)
        return False
    if not (encryptedCertFileExists and encryptedCurrentDateFileExists and encryptedStartingDateFileExists and encryptedLimitsFileNameExists and encryptedHoUFileNameExists):
        problemStatement = ""
        if not encryptedCertFileExists:
            problemStatement = f"{problemStatement}{encryptedCertFile} not found. "
        if not encryptedCurrentDateFileExists:
            problemStatement = f"{problemStatement}{encryptedCurrentDateFile} not found. "
        if not encryptedStartingDateFileExists:
            problemStatement = f"{problemStatement}{encryptedStartingDateFile} not found. "
        if not encryptedLimitsFileNameExists:
            problemStatement = f"{problemStatement}{encryptedLimitsFileName} not found. "
        if not encryptedHoUFileNameExists:
            problemStatement = f"{problemStatement}{encryptedHoUFileName} not found. "
        SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                       f"Attempt to tamper with the verification files of the system.\n{problemStatement} Invoking DAF. Exiting the program.",
                                                       Logger.PROBLEM)
        SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                       f"Attempt to tamper with the verification files of the system.\n{problemStatement} Invoking DAF. Exiting the program.",
                                                       Logger.PROBLEM)
        deleteAllFiles()
        stopProcesses(currentProcessPIDs)
        exitProgram()
    else:
        return True

def deleteAllFiles():
    try:
        encryptedCertFileExists = os.path.isfile(encryptedCertFile)
        encryptedCurrentDateFileExists = os.path.isfile(encryptedCurrentDateFile)
        encryptedStartingDateFileExists = os.path.isfile(encryptedStartingDateFile)
        encryptedLimitsFileNameExists = os.path.isfile(encryptedLimitsFileName)
        encryptedHoUFileNameExists = os.path.isfile(encryptedHoUFileName)
        if encryptedCertFileExists:
            os.remove(encryptedCertFile)
        if encryptedCurrentDateFileExists:
            os.remove(encryptedCurrentDateFile)
        if encryptedStartingDateFileExists:
            os.remove(encryptedStartingDateFile)
        if encryptedLimitsFileNameExists:
            os.remove(encryptedLimitsFileName)
        if encryptedHoUFileNameExists:
            os.remove(encryptedHoUFileName)
        # SlaveConsoleLogger.getInstance().logTakeNote(logSource,
        #                                                f"Successfully invoked DAF",
        #                                                Logger.GENERAL)
        # SlaveFileLogger.getInstance().logTakeNote(logSource,
        #                                                f"Successfully invoked DAF",
        #                                                Logger.GENERAL)
    except Exception as e:
        SlaveConsoleLogger.getInstance().logConsiderAction(logSource,
                                                       f"Could not invoke DAF due to {e}",
                                                       Logger.ISSUE)
        SlaveFileLogger.getInstance().logConsiderAction(logSource,
                                                       f"Could not invoke DAF due to {e}",
                                                       Logger.ISSUE)

def getCurrentSessionsStartingTime():
    global currentSessionsStartingTime
    # if currentSessionsStartingTime == 0.0:
    currentSessionsStartingTime = time.time()
    return currentSessionsStartingTime

getCurrentSessionsStartingTime()

def getCurrentSessionsDuration():
    global currentSessionsStartingTime
    currentTime = time.time()
    duration_ms = round((currentTime - currentSessionsStartingTime)*1000,0)
    duration_hours = round(duration_ms / (1000*3600), 2)
    getCurrentSessionsStartingTime()
    return duration_hours

def getCurrentTime():
    msTime = datetime.now().strftime('%Y-%m-%d-%H-%M-%S.%f')
    (dt, ms) = msTime.split('.')
    ms = "{:03d}".format(int(ms) // 1000)
    currentTime = f'{dt}-{ms}'
    return currentTime

def printWithTime(*args):
    currentTime = f'{getCurrentTime()} :'
    print(currentTime, *args, end = "")

def printBoldRed(*args, includeTime = True):
    # print(Fore.RED + Style.BRIGHT, end="")
    print(CosThetaColors.CRED, end="")
    print(CosThetaColors.CBOLD, end="")
    if includeTime:
        printWithTime(*args)
    else:
        print(*args, end = "")
    print(CosThetaColors.CEND)
    # print(Style.RESET_ALL,end="")

def printBoldGreen(*args, includeTime = True):
    # print(Fore.GREEN + Style.BRIGHT, end="")
    print(CosThetaColors.CGREEN, end="")
    print(CosThetaColors.CBOLD, end="")
    if includeTime:
        printWithTime(*args)
    else:
        print(*args, end = "")
    print(CosThetaColors.CEND)
    # print(Style.RESET_ALL,end="")

def getGUID():
    if str(sys.platform).lower() == 'darwin':
        return run(
            "ioreg -d2 -c IOPlatformExpertDevice | awk -F\\\" '/IOPlatformUUID/{print $(NF-1)}'",
        )

    if str(sys.platform).lower() == 'win32' or str(sys.platform).lower() == 'cygwin' or str(sys.platform).lower() == 'msys':
        return (run('wmic csproduct get uuid').split('\n')[2]
                .strip())

    if str(sys.platform).lower().startswith('linux'):
        return run('cat /var/lib/dbus/machine-id') or run('cat /etc/machine-id')

    if str(sys.platform).lower().startswith('openbsd') or str(sys.platform).lower().startswith('freebsd'):
        return run('cat /etc/hostid') or run('kenv -q smbios.system.uuid')
'''
def getMacID():
    from uuid import getnode as get_mac
    # get the mac address
    mac = get_mac()
    # print(hex(mac))
    # reduce the complexity
    # macstring performs the clearest form of mac address and it is the correct form of valid address
    # here the for loop arranges the valid mac address in orderly formats
    macString = ''.join(("%012X" % mac)[i:i + 2] for i in range(0, 12, 2))
    # print('[' + macString + ']')
    return macString
'''

# *******************************************************

commonMCString = "ACDB654BCD32981CABD09BC45326718ADCB7632985423871098CDB5B7C8D0A"

def getGUIDEncryptionKey():
    guid = getGUID()
    encryptionString = copy.deepcopy(guid[1] + guid[2] + guid[4] + guid[9] +
                                     guid[20] + guid[22] + guid[21] + guid[25] +
                                     guid[10] + guid[3] + guid[14] + guid[17] +
                                     guid[7] + guid[31] + guid[29] + guid[12])
    # print(encryptionString)
    return encryptionString

'''
def encryptMACID():
    # global logger
    global commonMCString
    macID = getGUID()
    macID1 = copy.deepcopy(macID[:3])
    macID2 = copy.deepcopy(macID[3:6])
    macID3 = copy.deepcopy(macID[6:9])
    macID4 = copy.deepcopy(macID[9:])
    stringToBeEncoded = copy.deepcopy(
                        macID4 + macID3 + macID2 + macID2 + \
                        macID2 + macID4 + macID3 + macID1 + \
                        macID1 + macID3 + macID2 + macID4 + \
                        macID2 + macID1 + macID3 + macID4 + \
                        macID3 + macID1 + macID2 + macID4 + \
                        macID4 + macID1 + macID2 + macID3 + \
                        macID2 + macID4 + macID1 + macID3 + \
                        macID1 + macID3 + macID4 + macID2 + \
                        macID3 + macID1 + macID4 + macID4 + \
                        macID2 + macID1 + macID4 + macID4 + \
                        macID4 + macID3 + macID1 + macID1 + \
                        macID4 + macID2 + macID1 + macID1 + \
                        macID2 + macID3 + macID1 + macID1 + \
                        macID1 + macID3 + macID2 + macID2 + \
                        macID2 + macID1 + macID3 + macID3 + \
                        macID3 + macID2 + macID2 + macID4 + \
                        macID4 + macID2 + macID2 + macID3 + \
                        macID1 + macID3 + macID2 + macID4 + \
                        macID2 + macID1 + macID3 + macID4 + \
                        macID3 + macID1 + macID2 + macID4 + \
                        macID4 + macID1 + macID2 + macID3 + \
                        macID2 + macID4 + macID1 + macID3 + \
                        macID1 + macID3 + macID4 + macID2 + \
                        macID3 + macID1 + macID4 + macID4 + \
                        macID2 + macID1 + macID4 + macID4 + \
                        macID4 + macID3 + macID1 + macID1 + \
                        macID4 + macID2 + macID1 + macID1 + \
                        macID2 + macID3 + macID1 + macID1 + \
                        macID4 + macID3 + macID2 + macID2 + \
                        macID2 + macID4 + macID3 + macID1 + \
                        macID1 + macID3 + macID2 + macID4 + \
                        macID2 + macID1 + macID3 + macID4 + \
                        macID1 + macID2[0])
    keyString = getGUIDEncryptionKey()
    key = keyString.encode('utf-8')
    cipher = Crypto.Cipher.AES.new(key, AES.MODE_EAX)
    # stringToBeEncoded = stringToBeEncoded.decode('utf-8')
    # print(stringToBeEncoded)
    stringToBeEncoded = f"{commonMCString}{stringToBeEncoded}{commonMCString[::-1]}"
    # print(stringToBeEncoded)
    stringToBeEncoded = stringToBeEncoded.encode('utf-8')
    # print(stringToBeEncoded)
    ciphertext, tag = cipher.encrypt_and_digest(stringToBeEncoded)
    # currentWorkingDirectory = get_project_root()
    # currentWorkingDirectory = get_project_root()
    # logging.debug(f"Current working directory is {currentWorkingDirectory}")
    # printBoldRed(f"Current working directory is {currentWorkingDirectory}")
    encryptionDirectory = f"{currentWorkingDirectory}/{certDirectory}"
    encryptedFile = f"{encryptionDirectory}/{certFileName}"
    # try:
    #     os.makedirs(encryptionDirectory)
    # except:
    #     pass
    try:
        file_out = open(encryptedFile, "wb")
        [file_out.write(x) for x in (cipher.nonce, tag, ciphertext)]
        file_out.close()
        logging.error(f"Successfully created {encryptedFile}")
        # printBoldBlue("Certificate created")
        return True
    except Exception as e:
        logging.critical(f"Could not create {encryptedFile} due to {e}")
        return False
'''

def encryptGUID():
    # global logger
    global commonMCString
    guid = getGUID()
    guid1 = copy.deepcopy(guid[:3])
    guid2 = copy.deepcopy(guid[3:6])
    guid3 = copy.deepcopy(guid[9:12])
    guid4 = copy.deepcopy(guid[14:17])
    stringToBeEncoded = copy.deepcopy(
                        guid4 + guid3 + guid2 + guid2 + \
                        guid2 + guid4 + guid3 + guid1 + \
                        guid1 + guid3 + guid2 + guid4 + \
                        guid2 + guid1 + guid3 + guid4 + \
                        guid3 + guid1 + guid2 + guid4 + \
                        guid4 + guid1 + guid2 + guid3 + \
                        guid2 + guid4 + guid1 + guid3 + \
                        guid1 + guid3 + guid4 + guid2 + \
                        guid3 + guid1 + guid4 + guid4 + \
                        guid2 + guid1 + guid3 + guid4 + \
                        guid3 + guid1 + guid2 + guid4 + \
                        guid4 + guid1 + guid2 + guid3 + \
                        guid2 + guid4 + guid1 + guid3 + \
                        guid1 + guid3 + guid4 + guid2 + \
                        guid3 + guid1 + guid4 + guid4 + \
                        guid2 + guid1 + guid4 + guid4 + \
                        guid2 + guid1 + guid4 + guid4 + \
                        guid4 + guid3 + guid1 + guid1 + \
                        guid4 + guid2 + guid1 + guid1 + \
                        guid2 + guid3 + guid1 + guid1 + \
                        guid1 + guid3 + guid2 + guid2 + \
                        guid2 + guid1 + guid3 + guid3 + \
                        guid3 + guid2 + guid2 + guid4 + \
                        guid4 + guid2 + guid2 + guid3 + \
                        guid1 + guid3 + guid2 + guid4 + \
                        guid4 + guid3 + guid1 + guid1 + \
                        guid4 + guid2 + guid1 + guid1 + \
                        guid2 + guid3 + guid1 + guid1 + \
                        guid4 + guid3 + guid2 + guid2 + \
                        guid2 + guid4 + guid3 + guid1 + \
                        guid1 + guid3 + guid2 + guid4 + \
                        guid2 + guid1 + guid3 + guid4 + \
                        guid1 + guid2[0])
    keyString = getGUIDEncryptionKey()
    key = keyString.encode('utf-8')
    cipher = Crypto.Cipher.AES.new(key, AES.MODE_EAX)
    # stringToBeEncoded = stringToBeEncoded.decode('utf-8')
    # print(stringToBeEncoded)
    stringToBeEncoded = f"{commonMCString}{stringToBeEncoded}{commonMCString[::-1]}"
    # print(stringToBeEncoded)
    stringToBeEncoded = stringToBeEncoded.encode('utf-8')
    # print(stringToBeEncoded)
    ciphertext, tag = cipher.encrypt_and_digest(stringToBeEncoded)
    # currentWorkingDirectory = get_project_root()
    # currentWorkingDirectory = get_project_root()
    # logging.debug(f"Current working directory is {currentWorkingDirectory}")
    # printBoldRed(f"Current working directory is {currentWorkingDirectory}")
    encryptionDirectory = f"{currentWorkingDirectory}/{certDirectory}"
    encryptedFile = f"{encryptionDirectory}/{certFileName}"
    # try:
    #     os.makedirs(encryptionDirectory)
    # except:
    #     pass
    try:
        file_out = open(encryptedFile, "wb")
        [file_out.write(x) for x in (cipher.nonce, tag, ciphertext)]
        file_out.close()
        SlaveConsoleLogger.getInstance().logDebug(logSource,
                                                       f"Successfully created {encryptedFile}",
                                                       Logger.SUCCESS)
        SlaveFileLogger.getInstance().logInfo(logSource,
                                                       f"Successfully created {encryptedFile}",
                                                       Logger.SUCCESS)
        # printBoldBlue("Certificate created")
        return True
    except Exception as e:
        SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                       f"Could not create {encryptedFile} due to {e}",
                                                       Logger.PROBLEM)
        SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                       f"Could not create {encryptedFile} due to {e}",
                                                       Logger.PROBLEM)
        return False

def decryptAndVerifyIntegrity():
    # global logger
    try:
        guid = getGUID()
        # print(guid)
        guid1 = copy.deepcopy(guid[:3])
        guid2 = copy.deepcopy(guid[3:6])
        guid3 = copy.deepcopy(guid[9:12])
        guid4 = copy.deepcopy(guid[14:17])
        stringToBeChecked = copy.deepcopy(
            guid4 + guid3 + guid2 + guid2 + \
            guid2 + guid4 + guid3 + guid1 + \
            guid1 + guid3 + guid2 + guid4 + \
            guid2 + guid1 + guid3 + guid4 + \
            guid3 + guid1 + guid2 + guid4 + \
            guid4 + guid1 + guid2 + guid3 + \
            guid2 + guid4 + guid1 + guid3 + \
            guid1 + guid3 + guid4 + guid2 + \
            guid3 + guid1 + guid4 + guid4 + \
            guid2 + guid1 + guid3 + guid4 + \
            guid3 + guid1 + guid2 + guid4 + \
            guid4 + guid1 + guid2 + guid3 + \
            guid2 + guid4 + guid1 + guid3 + \
            guid1 + guid3 + guid4 + guid2 + \
            guid3 + guid1 + guid4 + guid4 + \
            guid2 + guid1 + guid4 + guid4 + \
            guid2 + guid1 + guid4 + guid4 + \
            guid4 + guid3 + guid1 + guid1 + \
            guid4 + guid2 + guid1 + guid1 + \
            guid2 + guid3 + guid1 + guid1 + \
            guid1 + guid3 + guid2 + guid2 + \
            guid2 + guid1 + guid3 + guid3 + \
            guid3 + guid2 + guid2 + guid4 + \
            guid4 + guid2 + guid2 + guid3 + \
            guid1 + guid3 + guid2 + guid4 + \
            guid4 + guid3 + guid1 + guid1 + \
            guid4 + guid2 + guid1 + guid1 + \
            guid2 + guid3 + guid1 + guid1 + \
            guid4 + guid3 + guid2 + guid2 + \
            guid2 + guid4 + guid3 + guid1 + \
            guid1 + guid3 + guid2 + guid4 + \
            guid2 + guid1 + guid3 + guid4 + \
            guid1 + guid2[0])
        # stringToBeChecked = stringToBeChecked.decode('utf-8')
        keyString = getGUIDEncryptionKey()
        key = keyString.encode('utf-8')
        # currentWorkingDirectory = str(os.path.dirname(__file__))
        currentWorkingDirectory = get_project_root()
        encryptionDirectory = f"{currentWorkingDirectory}/{certDirectory}"
        encryptedFile = f"{encryptionDirectory}/{certFileName}"
        # try:
        #     os.makedirs(encryptionDirectory)
        # except Exception as e:
        #     # print(e)
        #     pass
        nonce = None
        try:
            file_in = open(encryptedFile, "rb")
            nonce, tag, ciphertext = [file_in.read(x) for x in (16, 16, -1)]
            file_in.close()
        except Exception as e:
            SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                           f"Exception encountered while verifying integrity : {e}",
                                                           Logger.PROBLEM)
            SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                        f"Exception encountered while verifying integrity : {e}",
                                                        Logger.PROBLEM)
            pass
        if nonce is not None:
            cipher = Crypto.Cipher.AES.new(key, AES.MODE_EAX, nonce)
            data = cipher.decrypt_and_verify(ciphertext, tag)
            data = data.decode('utf-8')
            # print(f"{data =}")
            data = data.replace(commonMCString,"")
            data = data.replace(commonMCString[::-1],"")
            # printBoldBlue(f"In decryptAndVerifyIntegrity(), found that {data = }")
            # print(stringToBeChecked == str(data))
            if (stringToBeChecked != str(data)) :
                # NOTE : DO NOT EXIT THE PROGRAM IN THIS METHOD
                SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                               "Invalid certificate. Integrity verification failed. Contact CosTheta Technologies to validate the certificate for further use",
                                                               Logger.PROBLEM)
                SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                            "Invalid certificate. Integrity verification failed. Contact CosTheta Technologies to validate the certificate for further use",
                                                            Logger.PROBLEM)
                deleteAllFiles()
            return (stringToBeChecked == str(data))
        else:
            # NOTE : DO NOT EXIT THE PROGRAM IN THIS METHOD
            SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                           "Invalid certificate. Integrity verification failed. Contact CosTheta Technologies to validate the certificate for further use",
                                                           Logger.PROBLEM)
            SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                        "Invalid certificate. Integrity verification failed. Contact CosTheta Technologies to validate the certificate for further use",
                                                        Logger.PROBLEM)
            deleteAllFiles()
            return False

    except Exception as e:
        SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                       f"Invalid certificate. Integrity verification failed. Contact CosTheta Technologies to validate the certificate for further use : {e}",
                                                       Logger.PROBLEM)
        SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                       f"Invalid certificate. Integrity verification failed. Contact CosTheta Technologies to validate the certificate for further use : {e}",
                                                       Logger.PROBLEM)
        deleteAllFiles()
        return False

'''
def decryptAndVerifyMACIntegrity():
    # global logger
    try:
        macID = getGUID()
        # print(macID)
        macID1 = copy.deepcopy(macID[:3])
        macID2 = copy.deepcopy(macID[3:6])
        macID3 = copy.deepcopy(macID[6:9])
        macID4 = copy.deepcopy(macID[9:])
        stringToBeChecked = copy.deepcopy(
            macID4 + macID3 + macID2 + macID2 + \
            macID2 + macID4 + macID3 + macID1 + \
            macID1 + macID3 + macID2 + macID4 + \
            macID2 + macID1 + macID3 + macID4 + \
            macID3 + macID1 + macID2 + macID4 + \
            macID4 + macID1 + macID2 + macID3 + \
            macID2 + macID4 + macID1 + macID3 + \
            macID1 + macID3 + macID4 + macID2 + \
            macID3 + macID1 + macID4 + macID4 + \
            macID2 + macID1 + macID4 + macID4 + \
            macID4 + macID3 + macID1 + macID1 + \
            macID4 + macID2 + macID1 + macID1 + \
            macID2 + macID3 + macID1 + macID1 + \
            macID1 + macID3 + macID2 + macID2 + \
            macID2 + macID1 + macID3 + macID3 + \
            macID3 + macID2 + macID2 + macID4 + \
            macID4 + macID2 + macID2 + macID3 + \
            macID1 + macID3 + macID2 + macID4 + \
            macID2 + macID1 + macID3 + macID4 + \
            macID3 + macID1 + macID2 + macID4 + \
            macID4 + macID1 + macID2 + macID3 + \
            macID2 + macID4 + macID1 + macID3 + \
            macID1 + macID3 + macID4 + macID2 + \
            macID3 + macID1 + macID4 + macID4 + \
            macID2 + macID1 + macID4 + macID4 + \
            macID4 + macID3 + macID1 + macID1 + \
            macID4 + macID2 + macID1 + macID1 + \
            macID2 + macID3 + macID1 + macID1 + \
            macID4 + macID3 + macID2 + macID2 + \
            macID2 + macID4 + macID3 + macID1 + \
            macID1 + macID3 + macID2 + macID4 + \
            macID2 + macID1 + macID3 + macID4 + \
            macID1 + macID2[0])
        # stringToBeChecked = stringToBeChecked.decode('utf-8')
        keyString = getGUIDEncryptionKey()
        key = keyString.encode('utf-8')
        # currentWorkingDirectory = str(os.path.dirname(__file__))
        currentWorkingDirectory = get_project_root()
        encryptionDirectory = f"{currentWorkingDirectory}/{certDirectory}"
        encryptedFile = f"{encryptionDirectory}/{certFileName}"
        # try:
        #     os.makedirs(encryptionDirectory)
        # except Exception as e:
        #     # print(e)
        #     pass
        try:
            file_in = open(encryptedFile, "rb")
            nonce, tag, ciphertext = [file_in.read(x) for x in (16, 16, -1)]
            file_in.close()
        except Exception as e:
            # print(e)
            pass
        cipher = Crypto.Cipher.AES.new(key, AES.MODE_EAX, nonce)
        data = cipher.decrypt_and_verify(ciphertext, tag)
        data = data.decode('utf-8')
        # print(f"{data =}")
        data = data.replace(commonMCString,"")
        data = data.replace(commonMCString[::-1],"")
        # printBoldBlue(f"In decryptAndVerifyIntegrity(), found that {data = }")
        # print(stringToBeChecked == str(data))
        if (stringToBeChecked != str(data)) :
            printBoldRed("Invalid certificate. Contact CosTheta Technologies to validate the certificate for further use.")
            logging.critical(f"Invalid certificate. Contact CosTheta Technologies to validate the certificate for further use.")
        return (stringToBeChecked == str(data))
    except Exception as e:
        printBoldRed(f"Encountered {e} while verifying integrity")
        logging.critical(f"Encountered {e} while verifying integrity")
        # print(False)
        return False

'''

# *******************************************************

commonCurrentDataString = "A7-65-94-26-87-18:53-00-87-00-16-00-32-17 : 00-00-00"

def encryptAndStoreCurrentDate():
    import Crypto
    from Crypto.Cipher import AES
    import os
    global key1
    global commonCurrentDataString
    global currentDateFileName
    # global logger
    now = datetime.now()
    date_time = now.strftime("%Y_%m_%d_%H_%M_%S")
    stringToBeEncoded = f"{commonCurrentDataString}{date_time}{commonCurrentDataString[::-1]}"
    keyString = copy.deepcopy(key1[12:] + key1[:4] + key1[8:12] + key1[4:8])
    newKey = keyString.encode('utf-8')
    cipher = Crypto.Cipher.AES.new(newKey, AES.MODE_EAX)
    ciphertext, tag = cipher.encrypt_and_digest(stringToBeEncoded.encode('utf-8'))
    # currentWorkingDirectory = str(os.path.dirname(__file__))
    currentWorkingDirectory = get_project_root()
    # logging.debug(f"Current working directory is {currentWorkingDirectory}")
    encryptionDirectory = f"{currentWorkingDirectory}/{certDirectory}"
    encryptedFile = f"{encryptionDirectory}/{currentDateFileName}"
    # try:
    #     os.makedirs(encryptionDirectory)
    # except:
    #     pass
    try:
        file_out = open(encryptedFile, "wb")
        [file_out.write(x) for x in (cipher.nonce, tag, ciphertext)]
        file_out.close()
        SlaveConsoleLogger.getInstance().logInfo(logSource,
                                                       f"Successfully wrote to {encryptedFile}",
                                                       Logger.GENERAL)
        SlaveFileLogger.getInstance().logInfo(logSource,
                                                    f"Successfully wrote to {encryptedFile}",
                                                    Logger.GENERAL)
    except Exception as e:
        SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                       f"Couldn't write to {encryptedFile} due to {e}",
                                                       Logger.PROBLEM)
        SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                    f"Couldn't write to {encryptedFile} due to {e}",
                                                    Logger.PROBLEM)
        pass

def decryptAndVerifyCurrentDateIntegrity():
    import Crypto
    from Crypto.Cipher import AES
    import os
    global key1
    global commonCurrentDataString
    global currentProcessPIDs
    # global logger
    try:
        keyString = copy.deepcopy(key1[12:] + key1[:4] + key1[8:12] + key1[4:8])
        newKey = keyString.encode('utf-8')
        currentWorkingDirectory = get_project_root()
        encryptionDirectory = f"{currentWorkingDirectory}/{certDirectory}"
        encryptedFile = f"{encryptionDirectory}/{currentDateFileName}"
        # try:
        #     os.makedirs(encryptionDirectory)
        # except:
        #     pass

        nonce = None
        try:
            file_in = open(encryptedFile, "rb")
            nonce, tag, ciphertext = [file_in.read(x) for x in (16, 16, -1)]
            file_in.close()
        except Exception as e1:
            SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                           f"Date integrity credentials not passed. Encountered : {e1}. Invoking DAF. Exiting Program.",
                                                           Logger.PROBLEM)
            SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                        f"Date integrity credentials not passed. Encountered : {e1}. Invoking DAF. Exiting Program.",
                                                        Logger.PROBLEM)
            deleteAllFiles()
            stopProcesses(currentProcessPIDs)
            exitProgram()
            # return False
        if nonce is not None:
            cipher = Crypto.Cipher.AES.new(newKey, AES.MODE_EAX, nonce)
            data = cipher.decrypt_and_verify(ciphertext, tag)
            data = data.decode('utf-8')
            data = data.replace(commonCurrentDataString, "")
            data = data.replace(commonCurrentDataString[::-1], "")
            previousUseTime = datetime.strptime(data, "%Y_%m_%d_%H_%M_%S")
            # printBoldBlue(f"Determined that previous use time was {previousUseTime}")
            currentUseTime = datetime.now()
            # print(currentUseTime)
            # print(currentUseTime > previousUseTime)
            if (currentUseTime < previousUseTime):
                SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                               f"Date integrity credentials invalid : {previousUseTime} / {currentUseTime}. Attempt to run system by changing system date to previous date. Invoking DAF. Program Exiting.",
                                                               Logger.PROBLEM)
                SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                            f"Date integrity credentials invalid : {previousUseTime} / {currentUseTime}. Attempt to run system by changing system date to previous date. Invoking DAF. Program Exiting.",
                                                            Logger.PROBLEM)
                deleteAllFiles()
                stopProcesses(currentProcessPIDs)
                exitProgram()
            else:
                SlaveConsoleLogger.getInstance().logInfo(logSource,
                                                               "Date validity OK",
                                                               Logger.GENERAL)
                SlaveFileLogger.getInstance().logInfo(logSource,
                                                            "Date validity OK",
                                                            Logger.GENERAL)
            return (currentUseTime >= previousUseTime)
    except Exception as e:
        SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                       f"Date integrity credentials not passed. Encountered : {e}. Invoking DAF. Program Exiting.",
                                                       Logger.PROBLEM)
        SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                    f"Date integrity credentials not passed. Encountered : {e}. Invoking DAF. Program Exiting.",
                                                    Logger.PROBLEM)
        deleteAllFiles()
        stopProcesses(currentProcessPIDs)
        exitProgram()
    return False


# def updateAndEncryptCurrentDate():
#     import Crypto
#     from Crypto import Cipher
#     from Crypto.Cipher import AES
#     import os
#     global key1
#     global commonCurrentDataString
#     now = datetime.now()
#     date_time = now.strftime("%Y_%m_%d_%H_%M_%S")
#     stringToBeEncoded = f"{commonCurrentDataString}{date_time}{commonCurrentDataString[::-1]}"
#     keyString = copy.deepcopy(key1[12:] + key1[4:8] + key1[:4] + key1[8:12])
#     newKey = keyString.encode('utf-8')
#     cipher = Crypto.Cipher.AES.new(newKey, AES.MODE_EAX)
#     ciphertext, tag = cipher.encrypt_and_digest(stringToBeEncoded.encode('utf-8'))
#     # currentWorkingDirectory = str(os.path.dirname(__file__))
#     currentWorkingDirectory = get_project_root()
#     # logging.debug(f"Current working directory is {currentWorkingDirectory}")
#     encryptionDirectory = f"{currentWorkingDirectory}/{certDirectory}"
#     encryptedFile = f"{encryptionDirectory}/{dateFileName}"
#     try:
#         os.makedirs(encryptionDirectory)
#     except:
#         pass
#     try:
#         file_out = open(encryptedFile, "wb")
#         [file_out.write(x) for x in (cipher.nonce, tag, ciphertext)]
#         file_out.close()
#     except:
#         # logging.error("Couldn't write to {dateFileName}")
#         pass

# *******************************************************

commonStartingTimeString = "23-09-76-76-23-65-23-18-17-21 : 01-02-03"

def encryptAndStoreStartingDate():
    import Crypto
    from Crypto.Cipher import AES
    import os
    global key2
    global startingTime
    # global logger
    startingTime = datetime.now()
    date_time = startingTime.strftime("%Y_%m_%d")
    stringToBeEncoded = f"{commonStartingTimeString}{date_time}{commonStartingTimeString[::-1]}"
    keyString = copy.deepcopy(key2[4:8] + key2[8:12] + key2[:4] + key2[12:])
    newKey = keyString.encode('utf-8')
    cipher = Crypto.Cipher.AES.new(newKey, AES.MODE_EAX)
    ciphertext, tag = cipher.encrypt_and_digest(stringToBeEncoded.encode('utf-8'))
    # currentWorkingDirectory = str(os.path.dirname(__file__))
    currentWorkingDirectory = get_project_root()
    # logging.debug(f"Current working directory is {currentWorkingDirectory}")
    encryptionDirectory = f"{currentWorkingDirectory}/{certDirectory}"
    encryptedFile = f"{encryptionDirectory}/{startingDateFileName}"
    # try:
    #     os.makedirs(encryptionDirectory)
    # except:
    #     pass

    try:
        file_out = open(encryptedFile, "wb")
        [file_out.write(x) for x in (cipher.nonce, tag, ciphertext)]
        file_out.close()
        SlaveConsoleLogger.getInstance().logInfo(logSource,
                                                       f"Successfully created file {encryptedFile}",
                                                       Logger.PROBLEM)
        SlaveFileLogger.getInstance().logInfo(logSource,
                                                    f"Successfully created file {encryptedFile}",
                                                    Logger.PROBLEM)
    except Exception as e:
        SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                       f"Could not create file {encryptedFile} due to {e}",
                                                       Logger.PROBLEM)
        SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                    f"Could not create file {encryptedFile} due to {e}",
                                                    Logger.PROBLEM)
        pass

def decryptAndGetStartingDate():
    import Crypto
    from Crypto.Cipher import AES
    import os
    global key2
    global startingTime
    global currentProcessPIDs
    try:
        keyString = copy.deepcopy(key2[4:8] + key2[8:12] + key2[:4] + key2[12:])
        newKey = keyString.encode('utf-8')
        currentWorkingDirectory = get_project_root()
        encryptionDirectory = f"{currentWorkingDirectory}/{certDirectory}"
        encryptedFile = f"{encryptionDirectory}/{startingDateFileName}"
        # try:
        #     os.makedirs(encryptionDirectory)
        # except:
        #     pass
        nonce = None
        try:
            file_in = open(encryptedFile, "rb")
            nonce, tag, ciphertext = [file_in.read(x) for x in (16, 16, -1)]
            file_in.close()
        except Exception as e:
            printBoldRed(f"Integrity violated - {e}. Problem in figuring out starting date. Invoking DAF.")
            deleteAllFiles()
            return False
        cipher = Crypto.Cipher.AES.new(newKey, AES.MODE_EAX, nonce)
        data = cipher.decrypt_and_verify(ciphertext, tag)
        data = data.decode('utf-8')
        data = data.replace(commonStartingTimeString,"")
        data = data.replace(commonStartingTimeString[::-1],"")
        startingTime = datetime.strptime(data, "%Y_%m_%d")
        SlaveConsoleLogger.getInstance().logInfo(logSource,
                                                       "Successfully got st d",
                                                       Logger.PROBLEM)
        SlaveFileLogger.getInstance().logInfo(logSource,
                                                    "Successfully got st d",
                                                    Logger.PROBLEM)
        # printBoldBlue(f"Got starting date as {startingTime = }")
        return True
    except Exception as e:
        SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                       f"Integrity violated - {e}. Problem in figuring out st d. Invoking DAF. Exiting Program.",
                                                       Logger.PROBLEM)
        SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                    f"Integrity violated - {e}. Problem in figuring out st d. Invoking DAF. Exiting Program.",
                                                    Logger.PROBLEM)
        deleteAllFiles()
        stopProcesses(currentProcessPIDs)
        exitProgram()
    return False

firstTime = True
def updateAndEncryptHoU(initialisation : bool = False):
    import Crypto
    from Crypto.Cipher import AES
    import os
    global key4
    global startingTime
    global commonHouString
    global firstTime
    # global logger
    houValid = False
    newActualHoU = 0.0
    if initialisation:
        newActualHoU = 0.0
    else:
        actualHoU = decryptHoU()
        # if not inProductionRun:
        # printBoldGreen(f"In updateAndEncryptHoU, {actualHoU = }")
        if isinstance(actualHoU, float):
            newActualHoU = actualHoU + getCurrentSessionsDuration()
            if newActualHoU >= houLimit:
                SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                               f"Certificate of use expired; {houLimit} : {newActualHoU}. Contact CosTheta Technologies. Invoking DAF. Preparing to exit the program.",
                                                               Logger.PROBLEM)
                SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                            f"Certificate of use expired; {houLimit} : {newActualHoU}. Contact CosTheta Technologies. Invoking DAF. Preparing to exit the program.",
                                                            Logger.PROBLEM)

                deleteAllFiles()
                return False
        else:
            return False
        # printBoldGreen(f"In updateAndEncryptHoU, {newActualHoU = }")
            # logging.shutdown()
            # sys.exit(0)
            # houValid = False
    houValid = newActualHoU <= houLimit
    stringToBeEncoded = f"{commonHouString}{newActualHoU}{commonHouString}"
    # print(f"Encoding actual hou as {newActualHoU}")
    keyString = copy.deepcopy(key4[8:12] + key4[4:8] + key4[12:] + key4[:4])
    newKey = keyString.encode('utf-8')
    cipher = Crypto.Cipher.AES.new(newKey, AES.MODE_EAX)
    ciphertext, tag = cipher.encrypt_and_digest(stringToBeEncoded.encode('utf-8'))
    # currentWorkingDirectory = str(os.path.dirname(__file__))
    currentWorkingDirectory = get_project_root()
    # logging.debug(f"Current working directory is {currentWorkingDirectory}")
    encryptionDirectory = f"{currentWorkingDirectory}/{certDirectory}"
    encryptedFile = f"{encryptionDirectory}/{houFileName}"
    # try:
    #     os.makedirs(encryptionDirectory)
    # except:
    #     pass
    try:
        file_out = open(encryptedFile, "wb")
        [file_out.write(x) for x in (cipher.nonce, tag, ciphertext)]
        file_out.close()
        if firstTime:
            SlaveConsoleLogger.getInstance().logInfo(logSource,
                                                           f"Successfully created {encryptedFile}",
                                                           Logger.GENERAL)
            SlaveFileLogger.getInstance().logInfo(logSource,
                                                        f"Successfully created {encryptedFile}",
                                                        Logger.GENERAL)
            firstTime = False
    except Exception as e:
        SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                       f"Could not create {encryptedFile} due to {e}",
                                                       Logger.PROBLEM)
        SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                    f"Could not create {encryptedFile} due to {e}",
                                                    Logger.PROBLEM)
        pass
    return houValid



# *******************************************************

commonMouLimitString = "C2.456GZORF298UYHBSVYH"
commonHouLimitString = "DJY71.344.65U5OP0.762U"

def encryptAndStoreMoUAndHoULimits():
    import Crypto
    from Crypto.Cipher import AES
    import os
    global key3
    global mouLimit, houLimit
    # global logger

    # mouLimit = passwordbox('MOU')
    mouLimit = getJustPassword(textToShow="Enter MoU")
    try:
        mouLimit = float(mouLimit)
    except Exception:
        mouLimit = 1
    mouLimit = round(mouLimit, 2)
    # houLimit = passwordbox('HOU')
    houLimit = getJustPassword(textToShow="Enter HoU")
    try:
        houLimit = float(houLimit)
    except Exception:
        houLimit = 250
    houLimit = round(houLimit, 2)
    stringToBeEncoded = f"{mouLimit}{commonMouLimitString}{commonHouLimitString[::-1]}_{houLimit}{commonHouLimitString}{commonMouLimitString[::-1]}"
    keyString = copy.deepcopy(key3[4:8] + key3[8:12] + key3[:4] + key3[12:])
    newKey = keyString.encode('utf-8')
    cipher = Crypto.Cipher.AES.new(newKey, AES.MODE_EAX)
    ciphertext, tag = cipher.encrypt_and_digest(stringToBeEncoded.encode('utf-8'))
    # currentWorkingDirectory = str(os.path.dirname(__file__))
    currentWorkingDirectory = get_project_root()
    # logging.debug(f"Current working directory is {currentWorkingDirectory}")
    encryptionDirectory = f"{currentWorkingDirectory}/{certDirectory}"
    encryptedFile = f"{encryptionDirectory}/{houmouLimitsFileName}"
    # try:
    #     os.makedirs(encryptionDirectory)
    # except:
    #     pass
    try:
        file_out = open(encryptedFile, "wb")
        [file_out.write(x) for x in (cipher.nonce, tag, ciphertext)]
        file_out.close()
        SlaveConsoleLogger.getInstance().logInfo(logSource,
                                                       f"Successfully created {encryptedFile}",
                                                       Logger.GENERAL)
        SlaveFileLogger.getInstance().logInfo(logSource,
                                                    f"Successfully created {encryptedFile}",
                                                    Logger.GENERAL)
    except Exception as e:
        SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                       f"Could not create {encryptedFile} due to {e}",
                                                       Logger.PROBLEM)
        SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                    f"Could not create {encryptedFile} due to {e}",
                                                    Logger.PROBLEM)
        pass

def decryptMoUandHoULimits():
    import Crypto
    from Crypto.Cipher import AES
    import os
    global key3
    global mouLimit, houLimit
    global currentProcessPIDs
    try:
        keyString = copy.deepcopy(key3[4:8] + key3[8:12] + key3[:4] + key3[12:])
        newKey = keyString.encode('utf-8')
        currentWorkingDirectory = get_project_root()
        encryptionDirectory = f"{currentWorkingDirectory}/{certDirectory}"
        encryptedFile = f"{encryptionDirectory}/{houmouLimitsFileName}"
        # try:
        #     os.makedirs(encryptionDirectory)
        # except:
        #     pass
        nonce = None
        try:
            file_in = open(encryptedFile, "rb")
            nonce, tag, ciphertext = [file_in.read(x) for x in (16, 16, -1)]
            file_in.close()
        except Exception as e:
            SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                           f"Integrity violated - {e}. Problem in determining mouL and houL, because of tampering. Invoking DAF. Exiting Program.",
                                                           Logger.PROBLEM)
            SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                        f"Integrity violated - {e}. Problem in determining mouL and houL, because of tampering. Invoking DAF. Exiting Program.",
                                                        Logger.PROBLEM)
            deleteAllFiles()
            stopProcesses(currentProcessPIDs)
            exitProgram()
        mouLimit = 0.0
        houLimit = 0.0
        if nonce is not None:
            cipher = Crypto.Cipher.AES.new(newKey, AES.MODE_EAX, nonce)
            data = cipher.decrypt_and_verify(ciphertext, tag)
            data = data.decode('utf-8')
            # print(f"data in mouhou limits is {data}")
            data = data.replace(commonMouLimitString, "")
            data = data.replace(commonHouLimitString, "")
            data = data.replace(commonMouLimitString[::-1], "")
            data = data.replace(commonHouLimitString[::-1], "")
            # print(f"data in mouhou limits is {data}")
            data = data.split("_")
            # print(f"data in mouhou limits is {data}")
            try:
                mouLimit = float(data[0])
                houLimit = float(data[1])
                SlaveConsoleLogger.getInstance().logInfo(logSource, "Successfully found mouL, houL",
                                                         Logger.GENERAL)
                SlaveFileLogger.getInstance().logInfo(logSource, "Successfully found mouL, houL",
                                                      Logger.GENERAL)
            except Exception:
                noteSuppressed()
            # printBoldBlue(f"Found {mouLimit = }, {houLimit = }")
            return True
    except Exception as e:
        SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                       f"Integrity violated - {e}. Problem in determining mouL and houL, because of tampering. Invoking DAF. Exiting Program.",
                                                       Logger.PROBLEM)
        SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                    f"Integrity violated - {e}. Problem in determining mouL and houL, because of tampering. Invoking DAF. Exiting Program.",
                                                    Logger.PROBLEM)
        deleteAllFiles()
        stopProcesses(currentProcessPIDs)
        exitProgram()
    return False

# *******************************************************

commonHouString = "2769458BDCA27810876336845190345"

# def updateAndEncryptHoU(initialisation : bool = False):
#     import Crypto
#     from Crypto import Cipher
#     from Crypto.Cipher import AES
#     import os
#     global key4
#     global startingTime
#     global commonHouString
#     # global logger
#     houValid = True
#     if initialisation:
#         newActualHoU = 0.0
#     else:
#         actualHoU = decryptHoU()
#         # if not inProductionRun:
#         # printBoldGreen(f"In updateAndEncryptHoU, {actualHoU = }")
#         newActualHoU = actualHoU + getCurrentSessionsDuration()
#         # printBoldGreen(f"In updateAndEncryptHoU, {newActualHoU = }")
#         if newActualHoU >= houLimit:
#             printBoldRed("Certificate of use expired. Contact CosTheta Technologies.")
#             houValid = False
#     stringToBeEncoded = f"{commonHouString}{newActualHoU}{commonHouString}"
#     # print(f"Encoding actual hou as {newActualHoU}")
#     keyString = copy.deepcopy(key4[8:12] + key4[12:] + key4[4:8] + key4[:4])
#     newKey = keyString.encode('utf-8')
#     cipher = Crypto.Cipher.AES.new(newKey, AES.MODE_EAX)
#     ciphertext, tag = cipher.encrypt_and_digest(stringToBeEncoded.encode('utf-8'))
#     # currentWorkingDirectory = str(os.path.dirname(__file__))
#     currentWorkingDirectory = get_project_root()
#     # logging.debug(f"Current working directory is {currentWorkingDirectory}")
#     encryptionDirectory = f"{currentWorkingDirectory}/{certDirectory}"
#     encryptedFile = f"{encryptionDirectory}/{houFileName}"
#     try:
#         os.makedirs(encryptionDirectory)
#     except:
#         pass
#     try:
#         file_out = open(encryptedFile, "wb")
#         [file_out.write(x) for x in (cipher.nonce, tag, ciphertext)]
#         file_out.close()
#         logging.error(f"Successfuly created {encryptedFile}")
#     except:
#         # logging.error("Couldn't write to {dateFileName}")
#         logging.critical(f"Could not create {encryptedFile} due to {e}")
#         pass
#     return houValid

def decryptHoU():
    import Crypto
    from Crypto.Cipher import AES
    import os
    global key4
    global mouLimit, houLimit
    global commonHouString
    # global logger
    try:
        keyString = copy.deepcopy(key4[8:12] + key4[4:8] + key4[12:] + key4[:4])
        newKey = keyString.encode('utf-8')
        currentWorkingDirectory = get_project_root()
        encryptionDirectory = f"{currentWorkingDirectory}/{certDirectory}"
        encryptedFile = f"{encryptionDirectory}/{houFileName}"
        # try:
        #     os.makedirs(encryptionDirectory)
        # except:
        #     pass
        nonce = None
        try:
            file_in = open(encryptedFile, "rb")
            nonce, tag, ciphertext = [file_in.read(x) for x in (16, 16, -1)]
            file_in.close()
        except Exception as e:
            SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                           f"Integrity violated - {e}. Problem in determining hou. Invoking DAF. Preparing to exit program.",
                                                           Logger.PROBLEM)
            SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                        f"Integrity violated - {e}. Problem in determining hou. Invoking DAF. Preparing to exit program.",
                                                        Logger.PROBLEM)
            deleteAllFiles()
            return False
        if nonce is not None:
            cipher = Crypto.Cipher.AES.new(newKey, AES.MODE_EAX, nonce)
            data = cipher.decrypt_and_verify(ciphertext, tag)
            data = data.decode('utf-8')
            data = data.replace(commonHouString, "")
            data = data.replace(commonHouString[::-1], "")
            # print(data)
            try:
                actualHou = float(data)
                SlaveConsoleLogger.getInstance().logInfo(logSource,
                                                               "Successfully found HoU",
                                                               Logger.GENERAL)
                SlaveFileLogger.getInstance().logInfo(logSource,
                                                            "Successfully found HoU",
                                                            Logger.GENERAL)
            except Exception as e:
                SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                               f"HoU undetermined due to {e}",
                                                               Logger.PROBLEM)
                SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                            f"HoU undetermined due to {e}",
                                                            Logger.PROBLEM)
                actualHou = False
            return actualHou
    except Exception as e:
        SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                       f"Integrity violated - {e}. Problem in determining hou. Invoking DAF. Preparing to exit program",
                                                       Logger.PROBLEM)
        SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                    f"Integrity violated - {e}. Problem in determining hou. Invoking DAF. Preparing to exit program",
                                                    Logger.PROBLEM)
        deleteAllFiles()
        return False
    return False

# ******************************************************

def checkUserAndPassword():
    # from getpass import getpass
    pwd1 = getJustPassword(textToShow="Enter Certificate Data 1")
    # if pwd1 == "TreesArePastMastersAtProducingO2AndCO2":
    if pwd1 == "Certificate1":
        SlaveConsoleLogger.getInstance().logInfo(logSource,
                                                       "Certificate 1 OK",
                                                       Logger.GENERAL)
        SlaveFileLogger.getInstance().logInfo(logSource,
                                                    "Certificate 1 OK",
                                                    Logger.GENERAL)
    else:
        SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                       "Certificate 1 Not OK. Program will exit shortly",
                                                       Logger.PROBLEM)
        SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                    "Certificate 1 Not OK. Program will exit shortly",
                                                    Logger.PROBLEM)
        return False
    pwd2 = getJustPassword(textToShow="Enter Certificate Data 2")
    # if pwd2 == "AxialAndRadialCenteringIsADifficultTaskAndNotDoneEasily":
    if pwd2 == "Certificate2":
        SlaveConsoleLogger.getInstance().logInfo(logSource,
                                                       "Certificate 2 OK",
                                                       Logger.GENERAL)
        SlaveFileLogger.getInstance().logInfo(logSource,
                                                    "Certificate 2 OK",
                                                    Logger.GENERAL)
        encryptAndStoreCurrentDate()
        return True
    else:
        SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                       "Certificate 2 Not OK. Program will exit shortly",
                                                       Logger.PROBLEM)
        SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                    "Certificate 2 Not OK. Program will exit shortly",
                                                    Logger.PROBLEM)
        return False
    # return False

def verifyMonthsAndHoursOfOperation():
    global startingTime, mouLimit, houLimit, currentProcessPIDs
    # global logger
    gotStartingDate = decryptAndGetStartingDate()
    if not gotStartingDate:
        SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                       "Integrity violated. Problem in figuring out st t. Invoking DAF. Exiting Program.",
                                                       Logger.PROBLEM)
        SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                    "Integrity violated. Problem in figuring out st t. Invoking DAF. Exiting Program.",
                                                    Logger.PROBLEM)
        deleteAllFiles()
        stopProcesses(currentProcessPIDs)
        exitProgram()
        # return False
    # print(startingTime)
    decryptMoUandHoULimits()
    currentTime = datetime.now()
    if startingTime is not None:
        nSeconds = (currentTime - startingTime).total_seconds()
        nDays = nSeconds / (3600 * 24)
        # printBoldGreen(f"Actual MoU is {nDays / 30}, while {mouLimit = }")
    else:
        SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                       "Integrity violated. Problem in figuring out st t. Invoking DAF. Exiting Program.",
                                                       Logger.PROBLEM)
        SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                    "Integrity violated. Problem in figuring out st t. Invoking DAF. Exiting Program.",
                                                    Logger.PROBLEM)
        deleteAllFiles()
        stopProcesses(currentProcessPIDs)
        exitProgram()
        # return False
    # print(f"{nDays = } {mouLimit * 30 = }")
    daysValidity = (nDays < (mouLimit * 30))
    if not daysValidity:
        SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                       f"Period of validity expired - m [{nDays} : {mouLimit * 30}]. Invoking DAF. Exiting Program.",
                                                       Logger.PROBLEM)
        SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                    f"Period of validity expired - m [{nDays} : {mouLimit * 30}]. Invoking DAF. Exiting Program.",
                                                    Logger.PROBLEM)
        deleteAllFiles()
        stopProcesses(currentProcessPIDs)
        exitProgram()
    actualHou = decryptHoU()
    # printBoldGreen(f"Actual HoU is {actualHou = }, while {houLimit = }")
    houValidity = actualHou < houLimit
    if houValidity:
        SlaveConsoleLogger.getInstance().logInfo(logSource,
                                                       "HoU, MoU, and st d validity OK",
                                                       Logger.GENERAL)
        SlaveFileLogger.getInstance().logInfo(logSource,
                                                    "HoU, MoU, and st d validity OK",
                                                    Logger.GENERAL)
        return True
    else:
        SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                       f"Period of validity expired - h [{actualHou} : {houLimit}]. Invoking DAF. Exiting Program",
                                                       Logger.PROBLEM)
        SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                    f"Period of validity expired -  [{actualHou} : {houLimit}]. Invoking DAF. Exiting Program",
                                                    Logger.PROBLEM)
        deleteAllFiles()
        stopProcesses(currentProcessPIDs)
        exitProgram()

    return False

def verifyIntegrity(processPIDs : list = []):
    # global logger
    # logger = logging.getLogger(__name__)
    global currentProcessPIDs
    currentProcessPIDs = processPIDs

    global encryptionDirectory
    createDirectory(f"{encryptionDirectory}/")

    # connectToRedis()

    loggingDirectory = CosThetaConfigurator.getInstance().getLoggingDirectory()
    loggingDirectory = f"{get_project_root()}/{loggingDirectory}/"
    createDirectory(loggingDirectory)


    # loggingFile = f'{loggingDirectory}logfile.log'
    # logging.basicConfig(
    #     level=logging.DEBUG,
    #     format='%(asctime)s : %(levelname)s : %(name)s : %(message)s', handlers=[logging.FileHandler(loggingFile), logging.StreamHandler(sys.stdout)]
    # )


    # set log level
    # logging.setLevel(logging.DEBUG)

    # define file handler and set formatter

    # file_handler = logging.FileHandler(loggingFile)
    # formatter = logging.Formatter('%(asctime)s : %(levelname)s : %(name)s : %(message)s')
    # file_handler.setFormatter(formatter)


    # add file handler to logger
    # logging.addHandler(file_handler)

    if verifyExistenceAndTamperingOfAllFiles():
        getCurrentSessionsStartingTime()
        # print(currentSessionsStartingTime)
        # makeCertificateIfNotThere = CosThetaConfigurator.getVerifyIntegrity()
        # print(makeCertificateIfNotThere)
        validUser = decryptAndVerifyIntegrity()
        # print(validUser)
        if validUser:
            SlaveConsoleLogger.getInstance().logTakeNote(logSource,
                                                           "Certificate and Licence check OK.",
                                                           Logger.SUCCESS)
            SlaveFileLogger.getInstance().logTakeNote(logSource,
                                                        "Certificate and Licence check OK.",
                                                        Logger.SUCCESS)
            validDate = decryptAndVerifyCurrentDateIntegrity()
            if validDate:
                periodValidity = verifyMonthsAndHoursOfOperation()
                if not periodValidity:
                    SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                                   "Period of operation expired. Invoking DAF. Exiting Program",
                                                                   Logger.PROBLEM)
                    SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                                "Period of operation expired. Invoking DAF. Exiting Program",
                                                                Logger.PROBLEM)
                    deleteAllFiles()
                    stopProcesses(currentProcessPIDs)
                    exitProgram()
                encryptAndStoreCurrentDate()
                houValid = updateAndEncryptHoU()
                if not houValid:
                    SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                                   "Period of validity expired. Invoking DAF. Exiting Program",
                                                                   Logger.PROBLEM)
                    SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                                "Period of validity expired. Invoking DAF. Exiting Program",
                                                                Logger.PROBLEM)
                    deleteAllFiles()
                    stopProcesses(currentProcessPIDs)
                    exitProgram()
                # if not inProductionRun:
                SlaveConsoleLogger.getInstance().logTakeNote(logSource,
                                                               "MoU / HoU check OK.",
                                                               Logger.SUCCESS)
                SlaveFileLogger.getInstance().logTakeNote(logSource,
                                                            "MoU / HoU check OK.",
                                                            Logger.SUCCESS)
                return True
            else:
                SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                               "Attempt to use system by changing dates on system. Invoking DAF. Exiting.",
                                                               Logger.PROBLEM)
                SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                            "Attempt to use system by changing dates on system. Invoking DAF. Exiting.",
                                                            Logger.PROBLEM)
                deleteAllFiles()
                stopProcesses(currentProcessPIDs)
                exitProgram()
        else:
            SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                           "Invalid user. Invoking DAF. Exiting.",
                                                           Logger.PROBLEM)
            SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                        "Invalid user. Invoking DAF. Exiting.",
                                                        Logger.PROBLEM)
            deleteAllFiles()
            stopProcesses(currentProcessPIDs)
            exitProgram()
    else:
        # if not makeCertificateIfNotThere:
        #     printBoldRed("Invalid certificate. Unable to create. Check configuration. Exiting. Contact CosTheta Technologies.")
        #     exitProgram()
        validUser = checkUserAndPassword()
        if not validUser:
            # logging.error(f"Program cannot be used. Invalid passwords.")
            # print(f"Program cannot be used. Invalid certificate credentials entered. Contact CosTheta Technologies.")
            SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                           "Program cannot be used. Invalid certificate credentials entered. Contact CosTheta Technologies. Invoking DAF. Exiting Program.",
                                                           Logger.PROBLEM)
            SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                        "Program cannot be used. Invalid certificate credentials entered. Contact CosTheta Technologies. Invoking DAF. Exiting Program.",
                                                        Logger.PROBLEM)
            deleteAllFiles()
            stopProcesses(currentProcessPIDs)
            # exit() # commented out for nuitka to function
            exitProgram()
            # return
        madeCertificate = encryptGUID()
        if not madeCertificate:
            # logging.error(f"Program cannot be used. A valid licence and certificate could not be made.")
            SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                           "Program cannot be used. A valid licence and certificate could not be made. Invoking DAF. Exiting.",
                                                           Logger.PROBLEM)
            SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                        "Program cannot be used. A valid licence and certificate could not be made. Invoking DAF. Exiting.",
                                                        Logger.PROBLEM)
            deleteAllFiles()
            stopProcesses(currentProcessPIDs)
            exitProgram()
        encryptAndStoreCurrentDate()
        encryptAndStoreStartingDate()
        encryptAndStoreMoUAndHoULimits()
        updateAndEncryptHoU(True)
        SlaveConsoleLogger.getInstance().logTakeAction(logSource,
                                                       "All credentials created.",
                                                       Logger.SUCCESS)
        SlaveFileLogger.getInstance().logTakeAction(logSource,
                                                    "All credentials created.",
                                                    Logger.SUCCESS)
        return True
        # if validUser:
        #     # logging.debug(f"Program validated for use. A valid licence and certificate has been created.")
        #     print(f"Program validated for use. A valid licence and certificate has been created.")
        # else:
        #     # logging.error(
        #         f"Unable to create a valid licence and certificate. Program cannot be used. A valid licence and user is needed for its usage.")
        #     print(
        #         f"Unable to create a valid licence and certificate. Program cannot be used. A valid licence and user is needed for its usage.")
        # exit() # commented out for nuitka to function
    # return False


    # printBoldRed("Credentials are invalid. Contact CosTheta Technologies to buy valid credentials. Invoking DAF. Exiting.")
    # logging.critical(
    #     "Credentials are invalid. Contact CosTheta Technologies to buy valid credentials. Invoking DAF. Exiting.")
    # deleteAllFiles()
    # exitProgram()

# verifyIntegrity()

# print(setRegistryValue(b'AWESDRTGYHJYDREWD'))
# print(getRegistryValue())
# print(WindowsRegistry.delete_key(full_path=windowsRegistryKey))