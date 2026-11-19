from utils.SuppressedExceptions import noteSuppressed   # logs exceptions that are caught and ignored
import signal
import sys
import typing

from frontend.SimplePopups import showApplicationStartingScreen, chooseMode, showErrorMessage, showMessage, \
    flashSimpleMessage, login
from persistence.Persistence import setDatabaseName, makeFoldersForImages, getDataSubFolder
import os
import redis
import time
from redis import Redis
from utils.RedisUtils import checkRedisHealth, clearQueues, getStopCommandFromQueue, sendStopCommand, \
    databaseServerStoppedSince, _PROCESS_STARTED_AT as _MAIN_PROGRAM_STARTED_AT
from utils.ShutdownWait import waitForDatabaseServer
from Configuration import CosThetaConfigurator
from utils.CosThetaPrintUtils import printBoldBlue


# # Python 3.10 compatibility patch
# if sys.version_info < (3, 11):
#     try:
#         from typing_extensions import Self
#
#         typing.Self = Self
#         print("✓ typing.Self patched")
#     except ImportError:
#         typing.Self = typing.TypeVar('Self')

# Python 3.10 compatibility patch
if sys.version_info < (3, 11):
    typing.Self = typing.TypeVar('Self')
    print("✓ typing.Self patched")


import multiprocessing
import sys
from multiprocessing import Process

from utils.IPUtils import checkIP

# Set spawn method explicitly for Windows compatibility
if "win32" in sys.platform:
    multiprocessing.set_start_method('spawn', force=True)
else:
    multiprocessing.set_start_method('fork', force=True)

import psutil
import threading
from datetime import datetime
CosThetaConfigurator.getInstance()

from utils.CosThetaPrintUtils import printBoldYellow
from persistence.PostgresBackupUtility import doBackup
from utils.CosThetaPrintUtils import printBoldGreen, printBoldRed   # also mirrored to the log file (CHANGES 110)
from utils.PictureRetention import startPictureRetentionThread
# from VerifyIntegrity import *

hostname = CosThetaConfigurator.getInstance().getRedisHost()
port = CosThetaConfigurator.getInstance().getRedisPort()
redisConnection : Redis | None = None

tryAgain : bool = False
try:
    redisConnection = Redis(hostname, port, retry_on_timeout=True)
    redisConnection.ping()  # Test connection
    printBoldGreen("Connected successfully to Redis in MainProgram")
except Exception as e:
    printBoldRed(f"Failed to connect to Redis in MainProgram : {e}")
    redisConnection = None
    printBoldBlue("Waiting for a short while for other components to come up")
    tryAgain = True
    time.sleep(10)

if tryAgain:
    try:
        redisConnection = Redis(hostname, port, retry_on_timeout=True)
        redisConnection.ping()  # Test connection
        tryAgain = False
        printBoldGreen("Connected successfully to Redis in MainProgram")
    except Exception as e:
        printBoldRed(f"Failed to connect to Redis in MainProgram : {e}")
        redisConnection = None
        printBoldBlue("Waiting for a short while more for other components to come up")
        tryAgain = True
        time.sleep(10)

if tryAgain:
    try:
        redisConnection = Redis(hostname, port, retry_on_timeout=True)
        redisConnection.ping()  # Test connection
        tryAgain = False
        printBoldGreen("Connected successfully to Redis in MainProgram")
    except Exception as e:
        printBoldRed(f"Failed to connect to Redis in MainProgram : {e}")
        redisConnection = None
        printBoldRed("Redis is an important component for the machine. Contact the manufacturer - CosTheta Technologies for a resolution")
        sys.exit(1)

# Redis is NOT flushed here, at module level (as in production). On Windows every server process is started with
# 'spawn', which re-imports this module in the new process, so a flush here ran again in each server as it started
# and deleted what the servers already running had put in Redis (queued log messages, heartbeats, cycle data).
# main() clears Redis once, before any server is started (clearQueues flushes the db and trims the queues).

def startTheLoggingServer():
    from logutils.LogSettings import setLogProcess
    setLogProcess("logging")      # per-server logging settings, crash capture, live configuration
    from logutils.CentralLoggers import startLoggers
    startLoggers()

def startTheHeartbeatServer():
    from logutils.LogSettings import setLogProcess
    setLogProcess("heartbeatserver")      # per-server logging settings, crash capture, live configuration
    from monitorAllConnections.HeartbeatAndAlarmServer import startHeartbeatAndAlarmServer
    startHeartbeatAndAlarmServer()

def startTheDBServer(mode : str, username : str):
    from logutils.LogSettings import setLogProcess
    setLogProcess("dbserver")      # per-server logging settings, crash capture, live configuration
    from persistence.DBServer import startDBServer
    startDBServer(mode=mode, username=username)

def startTheQRCodeServer(mode : str):
    from logutils.LogSettings import setLogProcess
    setLogProcess("qrcodeserver")      # per-server logging settings, crash capture, live configuration
    from costhetaio.qrcodescanner.QRCodeScanningServer import startQRCodeServer
    startQRCodeServer(mode=mode)

def startTheIOServer(mode : str):
    from logutils.LogSettings import setLogProcess
    setLogProcess("ioserver")      # per-server logging settings, crash capture, live configuration
    # One IOServer for both PLCs; plc.vendor in ApplicationConfiguration.properties chooses its PLCDataIO:
    # allenbradley (pycomm3) or siemens (pyS7, S7-1515)
    from costhetaio.commonplc.PLCIOServer import startIOServer
    startIOServer(mode = mode)

def startTheCameraServer(mode : str, stateFile : str | None = None):
    """stateFile: None at the start of the application; the cycle state file when the supervisor restarts the camera
    server, so that it carries on with the cycle in progress (camera/CameraStateFile.py)."""
    from logutils.LogSettings import setLogProcess
    setLogProcess("cameraserver")      # per-server logging settings, crash capture, live configuration
    from camera.CameraProcessorServer import startCameraServer
    startCameraServer(mode=mode, stateFile=stateFile)

def startTheFrontendServer(mode : str, username : str, role : str):
    from logutils.LogSettings import setLogProcess
    setLogProcess("frontend")      # per-server logging settings, crash capture, live configuration
    from frontend.AshokLeylandFrontEnd import startFrontEnd
    startFrontEnd(mode=mode,username=username, role=role)

# def startTheWebServiceEndpoints():
#     from endpoints.AshokLeylandWebService import startWebService
#     startWebService()

# def startTheMIService():
#     from endpoints.AshokLeyland_MI_Light import startMIServer
#     startMIServer()

def checkIPs():
    cameraIP = CosThetaConfigurator.getInstance().getCameraIP()
    plcIP = CosThetaConfigurator.getInstance().getPlcIP()
    cameraReachable = checkIP(cameraIP)
    plcReachable = checkIP(plcIP)

    if cameraReachable:
        printBoldGreen(f"Camera is reachable at IP {cameraIP}. OK.")
    else:
        printBoldRed(f"Camera is not reachable at IP {cameraIP}. Please check power to the camera and RJ45 connection from the camera to the system.")

    if plcReachable:
        printBoldGreen(f"I/O Module - PLC is reachable at IP {plcIP}. OK.")
    else:
        printBoldRed(f"I/O Module - PLC is not reachable at IP {plcIP}. Please check power to the PLC and RJ45 connection from the PLc to the system.")
        # raise Exception("IOModule not reachable")

# printBoldBlue(f"MainProgram process is {os.getpid()}")
# os.system(f"start /B start /wait cmd.exe @cmd /k python MainSubProgram.py {currentMode} {currentBatchNumber} {username} {rolename}")

# The DB server's multiprocessing.Process as it is now (the supervisor may have restarted it); set in main()
_databaseServerProcess = lambda: None


def stopProcesses(currentProcessPIDs: list, endingSplashScreenBeingShown : bool = True, exitAfterwards : bool = True):
    """Send the stop command, then end every server process. exitAfterwards: sys.exit() at the end (the callers in
    the main thread). monitorStop passes False: it runs in a thread, where sys.exit() only ends the thread (SystemExit
    is not an Exception, so its except did not catch it either) - and the database backup after it never ran."""
    global redisConnection
    if endingSplashScreenBeingShown:
        time.sleep(CosThetaConfigurator.getInstance().getEndScreenTime() + 2)
    sendStopCommand(redisConnection, aProducer="MainProgram")
    # Wait before ending the servers (CHANGES 157; a fixed 6 s in CHANGES 155, 2 s before): the DB server drains fe2db.q
    # after the stop command - a failed insert then goes to FailedRecords/ at once - and sends its stopped response.
    # At least mainprogram.stop.min.wait.sec (the other servers act on the stop), at most mainprogram.db.drain.wait.max.sec
    # (one record with PostgreSQL hanging: duplicate check and insert, each up to the 5 s connect timeout, plus the
    # statement timeout and the pictures); a record still on the queue then is lost (the next start flushes Redis).
    outcome, waited = waitForDatabaseServer(
        isDrained=lambda: databaseServerStoppedSince(redisConnection, _MAIN_PROGRAM_STARTED_AT),
        process=_databaseServerProcess,
        minimumWait=CosThetaConfigurator.getTuning("mainprogram.stop.min.wait.sec", 2.0, minimum=0.0),
        maximumWait=CosThetaConfigurator.getTuning("mainprogram.db.drain.wait.max.sec", 20.0, minimum=0.0))
    message = {"drained": f"DB server drained fe2db.q after the stop command ({waited:.1f} s)",
               "not running": f"DB server not running at the stop ({waited:.1f} s): records left on fe2db.q are lost",
               "timed out": f"DB server did not report fe2db.q drained within {waited:.1f} s: records left on it are lost"}[outcome]
    (printBoldGreen if outcome == "drained" else printBoldRed)(message)
    for pid in currentProcessPIDs:
        try:
            if psutil.pid_exists(pid):
                os.kill(pid, signal.SIGTERM)
                # printBoldBlue(f"Closed process {pid}")
                # p = psutil.Process(pid)
                # p.terminate()
        except Exception:
            noteSuppressed()
    # Close the Redis connection (once, after every process was ended; it was closed inside the loop)
    try:
        if redisConnection:
            redisConnection.close()
    except Exception:
        noteSuppressed()
    printBoldGreen("All processes shut down. Closing terminal window.")
    if exitAfterwards:
        sys.exit(1)

def monitorStop(redisConnectionForMonitoringStop: redis.client.Redis, currentProcesses: list, startingTime: float):
    global houLimit, mouLimit
    while True:
        try:
            _, shallStop = getStopCommandFromQueue(redisConnectionForMonitoringStop)
            if shallStop:
                # printBoldRed("Got stop signal in MainProgram")
                # time.sleep(CosThetaConfigurator.getInstance().getEndScreenTime() + 2)
                # for pid in currentProcesses:
                #     try:
                #         if psutil.pid_exists(pid):
                #             os.kill(pid, signal.SIGTERM)
                #             # p = psutil.Process(pid)
                #             # p.terminate()
                #     except:
                #         pass
                # printBoldGreen("All processes shut down.")
                stopProcesses(currentProcesses, exitAfterwards=False)
                break
        except Exception as e:
            printBoldRed(e)
            pass
        # if mouLimit < 12 and houLimit < 4800:
        #     currentTime = time.time()
        #     if (currentTime - lastUpdateOfStartingTime) > 60:
        #         # nHours = round((currentTime - lastUpdateOfStartingTime) / 3600,2)
        #         lastUpdateOfStartingTime = currentTime
        #         valid = False
        #         try:
        #             valid = updateAndEncryptHoU(False)
        #             # print(f"updateAndEncryptHoU(False) executed with result as {valid}")
        #         except:
        #             pass
        #         if not valid:
        #             stopProcesses(currentProcesses)
        #             break
        #         try:
        #             encryptAndStoreCurrentDate()
        #             # print(f"encryptAndStoreCurrentDate() executed")
        #         except:
        #             pass

        # allKeys = redisConnectionForMonitoringStop.scan_iter()
        # if allKeys is not None:
        #     printBoldYellow(f"The keys in queue are:")
        #     for aKey in allKeys:
        #         aKeyAsString = aKey.decode("utf-8")
        #         printBoldYellow(f"{aKeyAsString} : {redisConnectionForMonitoringStop.get(aKeyAsString)}")

        # printRedisQueueLengths(redis_connection=redisConnectionForMonitoringStop)

        time.sleep(10)
    # Database backup at shutdown (it never ran before 2026-11-07: see stopProcesses)
    try:
        printBoldBlue("Backing up database...")
        doBackup()
    except Exception as e:
        printBoldRed(f"Could not backup database at shutdown due to {e}")
    # try:
    #     updateAndEncryptHoU(False)
    # except:
    #     pass


def printRedisQueueLengths(redis_connection):
    """
    Check the number of messages in all Redis list queues.

    Args:
        redis_connection: Redis connection object (e.g., redis.Redis)

    Returns:
        dict: Dictionary mapping queue names to their message counts
    """
    queue_lengths = {}
    try:
        # Iterate over all keys in Redis
        for key in redis_connection.scan_iter():
            key_str = key.decode("utf-8")
            # Check the type of the key
            key_type = redis_connection.type(key).decode("utf-8")

            if key_type == "list":
                # Get the length of the list (number of messages in the queue)
                length = redis_connection.llen(key)
                if length > 0:
                    queue_lengths[key_str] = length
                # printBoldYellow(f"Queue {key_str}: {length} messages")
            elif key_type == "string":
                value = redis_connection.get(key)
                # printBoldYellow(f"String key {key_str}: {value}")
            elif key_type == "hash":
                value = redis_connection.hgetall(key)
                # printBoldYellow(f"Hash key {key_str}: {value}")
                # # Log non-list keys for debugging (optional)
            elif key_type == "stream":
                # Get the length of the stream (number of entries)
                length = redis_connection.xlen(key)
                if length > 0:
                    queue_lengths[key_str] = length
                    printBoldYellow(f"Stream Queue {key_str}: {length} entries")
            else:
                printBoldRed(f"Key {key_str}: Type {key_type} (not a queue)")

        if not queue_lengths or len(queue_lengths) == 0:
            printBoldYellow("No list queues found in Redis")
        else:
            printBoldYellow(f"Total messages in redis = {len(queue_lengths)}")

        return queue_lengths

    except redis.exceptions.ResponseError as e:
        printBoldRed(f"Redis error: {e}")
        return {}
    except Exception as e:
        printBoldRed(f"Unexpected error: {e}")
        return {}

def showTrialPictureSavingNotice() -> bool:
    """With save.all.pictures.because.its.in.trial.mode=True the camera server ALSO saves every picture of the 2-of-3
    checks (knuckle by regions, split pin, cap) under picture.saving.dir.for.mode.trial: say so at start-up for 2 s, in
    the same style as the other start-up messages (user, 2026-11-15). Returns whether it was shown."""
    configurator = CosThetaConfigurator.getInstance()
    if not configurator.getSaveAllPicturesBecauseItsInTrialMode():
        return False
    folder = os.path.abspath(configurator.getMainPictureSavingDirForModeTrial())
    message = f"Images will ALSO be saved in {folder}"
    printBoldYellow(message)
    flashSimpleMessage(message, auto_close_duration=2, line_width=70)
    return True


def main():
    # global showSplash
    from logutils.LogSettings import setLogProcess
    setLogProcess("mainprogram")

    printBoldBlue("Clearing up queues. Please wait...")

    try:
        clearQueues(redisConnection)
        printBoldGreen("Cleared up all queues")
    except Exception as e:
        printBoldRed(f"Couldn't clear queues because of {e}")
        printBoldRed("Queues have to work properly for the application to work. Hence, exiting...")
        sys.exit(0)

    # Start-up check of Redis beyond the PING above: it must also store data (write to a stream nothing else uses and
    # read it back). The heartbeat server repeats this every redis.health.check.interval.sec while running.
    redisHealthy, why = checkRedisHealth(redisConnection)
    if not redisHealthy:
        printBoldRed(f"Redis answers but does not store data ({why}).")
        printBoldRed("Redis is an important component for the machine. Contact the manufacturer - CosTheta Technologies for a resolution")
        sys.exit(1)
    printBoldGreen("Redis check passed: data written and read back")

    currentProcessPIDs = []

    # printBoldBlue(f"MainProgram process is {os.getpid()}")

    P1 = Process(target=startTheLoggingServer, args=())
    P1.start()
    time.sleep(3)
    printBoldBlue("Started Loggers")
    currentProcessPIDs.append(P1.pid)

    printBoldBlue(f"Logger process is {P1.pid}")

    # old pictures are deleted in the background (trial > 1 month, Test / Production > 2 months; CHANGES 110)
    startPictureRetentionThread()

    printBoldBlue("Verifying Integrity...")
    # verifyIntegrity(processPIDs = currentProcessPIDs)

    showApplicationStartingScreen("Starting Hub and Disc Processing Application", auto_close_duration=1)

    printBoldBlue("Backing up database...")
    try:
        doBackup()
    except Exception as e:
        printBoldRed(f"Could not backup database due to {e}")

    printBoldBlue("Checking connection to the Camera and IO module. Please wait...")
    checkIPs()

    # time.sleep(1)cd
    currentMode = "Test"
    printBoldBlue("Choosing mode (TEST or PRODUCTION). Default mode is TEST...")

    submitted, currentMode = chooseMode()

    if not submitted:
        showErrorMessage("Shutting down system, as no mode was selected")
        try:
            stopProcesses(currentProcessPIDs, endingSplashScreenBeingShown=False)
            os.system("taskkill /f /im cmd.exe")
        except Exception:
            noteSuppressed()
        sys.exit(0)

    printBoldBlue("Ensuring databases and file folders are created and available for saving data...")
    # Depending on mode, first do Test and then Production; else, vice-versa. Ensures that database name is properly populated for getting
    # the correct login ids populated in the login() dropdown
    if (currentMode.upper() == "TEST") or (currentMode.upper().startswith("TEST")):
        setDatabaseName(mode="Production")
        makeFoldersForImages(mode_data_subfolder=getDataSubFolder())
    else:
        setDatabaseName(mode="Test")
        makeFoldersForImages(mode_data_subfolder=getDataSubFolder())

    setDatabaseName(mode=currentMode)
    makeFoldersForImages(mode_data_subfolder=getDataSubFolder())

    printBoldBlue("Initiating login...")
    loginTries = 0
    loginSubmitted = False
    successfullyLoggedIn = False
    currentUser = None
    password = ''
    currentRole = None
    while (not successfullyLoggedIn) and (loginTries < 5):
        submitted, successfullyLoggedIn, currentUser, password, currentRole = login(currentMode)
        if not successfullyLoggedIn:
            loginTries += 1
            printBoldRed(f"Consecutive {loginTries} unsuccessful login attempts")
            if submitted:
                showErrorMessage("Login cancelled. Exiting the program.")
                stopProcesses(currentProcessPIDs, endingSplashScreenBeingShown=False)
                try:
                    os.system("taskkill /f /im cmd.exe")
                except Exception:
                    noteSuppressed()
                sys.exit(0)

    if not successfullyLoggedIn:
        showErrorMessage("5 unsuccessful login tries. Exiting the program")
        stopProcesses(currentProcessPIDs, endingSplashScreenBeingShown=False)
        try:
            os.system("taskkill /f /im cmd.exe")
        except Exception:
            noteSuppressed()
        sys.exit(0)

    # if currentRole == allowed_roles[2] and currentMode.upper() == "TEST":
    #     showErrorMessage(f"Only Administrator and Supervisor can run the system in TEST mode.\nUser {currentUser}'s role is {currentRole}\n. System will exit in 5 secs.", auto_close_duration=5)
    #     try:
    #         dbName = getDatabaseName()
    #     except:
    #         pass
    #     stopProcesses(currentProcessPIDs, endingSplashScreenBeingShown=False)
    #     try:
    #         os.system("taskkill /f /im cmd.exe")
    #     except:
    #         pass
    #     sys.exit(0)

    printBoldGreen(f"User {currentUser} logged in successfully into {currentMode}")
    showMessage(f"User {currentUser} logged in successfully into {currentMode}")

    # At this stage, the user has successfully logged in
    message: str = f"Running application in {currentMode} mode"
    flashSimpleMessage(message, auto_close_duration=2)
    printBoldBlue(message)
    showTrialPictureSavingNotice()

    # showMessage("Starting cameras, IO module, and other componenets", auto_close_duration=4)
    showApplicationStartingScreen("Starting Camera Servers, IO Server, DB Server, and Logging Server", auto_close_duration=1)

    try:
        P2 = Process(target=startTheCameraServer, args=(currentMode, None))     # None: start with no cycle state
        P2.start()
        time.sleep(1)
        printBoldBlue("Started Camera")
        currentProcessPIDs.append(P2.pid)
        printBoldBlue(f"Camera process is {P2.pid}")
    except Exception as e:
        printBoldRed("Could not start Camera Server")
        printBoldRed(f"Exception {e} while starting CameraServer")

    try:
        P3 = Process(target=startTheQRCodeServer, args=(currentMode,))
        P3.start()
        time.sleep(1)
        printBoldBlue("Started QR Code Server")
        currentProcessPIDs.append(P3.pid)
        printBoldBlue(f"QR Code process 1 is {P3.pid}")
    except Exception:
        printBoldRed("Could not start QR Code Server")

    try:
        P4 = Process(target=startTheIOServer, args=(currentMode,))
        P4.start()
        time.sleep(1)
        printBoldBlue("Started IO Server")
        currentProcessPIDs.append(P4.pid)
        printBoldBlue(f"IO process is {P4.pid}")
    except Exception:
        printBoldRed("Could not start IO Server")

    # The following message is needed to get rid of the previous pop-up from line 303
    # flashSimpleMessage("Done starting Camera Server, QRCode Server, IO Server, and Database Server", auto_close_duration=1)
    flashSimpleMessage("Done starting Camera Server, QRCode Server, and IO Server", auto_close_duration=1)

    try:
        P6 = Process(target=startTheFrontendServer, args=(currentMode, currentUser, currentRole))
        P6.start()
        printBoldBlue("Started Frontend server")
        time.sleep(1)
        currentProcessPIDs.append(P6.pid)
        printBoldBlue(f"Frontend process is {P6.pid}")
    except Exception:
        printBoldRed("Could not start Front End Server")

    try:
        P7 = Process(target=startTheHeartbeatServer, args=())
        P7.start()
        printBoldBlue("Started Heartbeat Monitoring Service")
        currentProcessPIDs.append(P7.pid)
        printBoldBlue(f"Heartbeat Monitoring process is {P7.pid}")
    except Exception:
        printBoldRed("Could not start the HeartbeatServer")

    try:
        P5 = Process(target=startTheDBServer, args=(currentMode, currentUser,))
        P5.start()
        time.sleep(1)
        printBoldBlue("Started DB Server")
        currentProcessPIDs.append(P5.pid)
        printBoldBlue(f"DB process is {P5.pid}")
    except Exception:
        printBoldRed("Could not start DB Server")

    # try:
    #     P8 = Process(target=startTheWebServiceEndpoints, args=())
    #     P8.start()
    #     # printBoldBlue("Started WebService")
    #     time.sleep(1)
    #     currentProcessPIDs.append(P8.pid)
    #     # printBoldBlue(f"Web Service process is {P7.pid}")
    # except:
    #     printBoldRed("Could not start Web Service Endpoints")

    # try:
    #     P9 = Process(target=startTheMIService, args=())
    #     P9.start()
    #     # printBoldBlue("Started MI Service")
    #     time.sleep(1)
    #     currentProcessPIDs.append(P9.pid)
    #     # printBoldBlue(f"MI Service process is {P8.pid}")
    # except:
    #     printBoldRed("Could not start the MI Service")


    redisConnectionForMonitoringStop = Redis(CosThetaConfigurator.getInstance().getRedisHost(),
                                             CosThetaConfigurator.getInstance().getRedisPort(), retry_on_timeout=True)

    # Restart a server process that dies (not after a stop command): utils/ProcessSupervisor.py
    if CosThetaConfigurator.getInstance().getSupervisorRestartDeadProcesses():
        from utils.ProcessSupervisor import ProcessSupervisor
        supervisorRedis = Redis(CosThetaConfigurator.getInstance().getRedisHost(),
                                CosThetaConfigurator.getInstance().getRedisPort(), socket_timeout=2.0)

        def stopRequested() -> bool:
            try:
                return bool(getStopCommandFromQueue(supervisorRedis, block=1)[1])
            except Exception:
                return False

        def startProcess(target, args):
            process = Process(target=target, args=args)
            process.start()
            return process

        supervisor = ProcessSupervisor(processFactory=startProcess, isStopping=stopRequested,
                                       pidList=currentProcessPIDs,
                                       checkInterval=CosThetaConfigurator.getInstance().getSupervisorCheckInterval(),
                                       maxRestartsPerHour=CosThetaConfigurator.getInstance().getSupervisorMaxRestartsPerHour(),
                                       log=lambda message: printBoldRed(f"Supervisor: {message}"))
        known = locals()
        from camera.CameraStateFile import defaultPath as cameraStateFile
        # a restarted camera server is given the cycle state file, so that it carries on with the cycle in progress
        restartArgs = {"Camera server": (currentMode, cameraStateFile())}
        for name, var, target, args in (("Logging server", "P1", startTheLoggingServer, ()),
                                        ("Camera server", "P2", startTheCameraServer, (currentMode, None)),
                                        ("QR code server", "P3", startTheQRCodeServer, (currentMode,)),
                                        ("IO server", "P4", startTheIOServer, (currentMode,)),
                                        ("DB server", "P5", startTheDBServer, (currentMode, currentUser,)),
                                        ("Frontend", "P6", startTheFrontendServer, (currentMode, currentUser, currentRole)),
                                        ("Heartbeat server", "P7", startTheHeartbeatServer, ())):
            if known.get(var) is not None:
                supervisor.add(name, target, args, known[var], restartArgs=restartArgs.get(name))
        supervisor.start()
        printBoldBlue("Process supervisor started: a server process that dies is restarted")

    global _databaseServerProcess
    _startedDBServer = locals().get("P5")
    _supervisor = locals().get("supervisor")
    _databaseServerProcess = ((lambda: _supervisor.process("DB server")) if _supervisor is not None
                              else (lambda: _startedDBServer))

    startingTime: float = time.time()

    stopMonitoringThread = threading.Thread(name="Stop Monitoring Thread", target=monitorStop,
                                            args=(redisConnectionForMonitoringStop, currentProcessPIDs, startingTime),
                                            daemon=True)
    stopMonitoringThread.start()
    stopMonitoringThread.join()

    # printBoldBlue(f"Main Program shutting down gracefully at {datetime.now().strftime('%Y-%m-%d-%H-%M-%S.%f')}")
    printBoldBlue(f"Main Program shutting down at {datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}")
    try:
        os.system("taskkill /f /im cmd.exe")
    except Exception:
        noteSuppressed()
    time.sleep(3)
    sys.exit(0)

if __name__ == '__main__':
    main()
    sys.exit(0)
