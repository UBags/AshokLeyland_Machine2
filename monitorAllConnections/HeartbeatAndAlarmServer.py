from utils.SuppressedExceptions import noteSuppressed   # logs exceptions that are caught and ignored
import threading
import os
import winsound
from logutils.SlaveLoggers import *
import time
from BaseUtils import *
# printBoldGreen is used by the Redis health check ("storing data again"); it came from no star import, so the
# check thread died with NameError the first time Redis recovered (CHANGES 85)
from utils.CosThetaPrintUtils import printBoldGreen, printBoldRed
import sys

from Configuration import *

from utils.RedisUtils import (readAlarmInHeartbeatServer, sendCombinedHeartbeatFromHeartbeatServerToIOServer, sendHeartbeatsFromHeartbeatServerToFEServer, ALIVE, DEAD, readAllHeartbeatsInHeartbeatServer, checkRedisHealth)

CosThetaConfigurator.getInstance()

from processors.GenericQueueProcessor import *

class HeartbeatAndAlarmServer(GenericQueueProcessor):

    logSource = getFullyQualifiedName(__file__)

    beepMessage = CosThetaConfigurator.getInstance().getHeartbeatBeepMessage()
    windows = CosThetaConfigurator.getInstance().isWindows()
    linux = CosThetaConfigurator.getInstance().isLinux()
    mac = CosThetaConfigurator.getInstance().isMac()
    wavDirectory = get_project_root() +"/wavs/"
    disconnectedSoundFile = f"{wavDirectory}Siren.wav"
    connectedSoundFile = f"{wavDirectory}System is ready.wav"
    badComponentSoundFile = f"{wavDirectory}BadComponent.wav"

    MINIMUM_CONSECUTIVE_DOWNS_NEEDED_FOR_ALARM: int = CosThetaConfigurator.getInstance().getMinimumContinuousDisconnectionsNeededToSendAlarm()

    def __init__(self, name: str = "HeartbeatServerAndAlarm", consumer: Any = None, timeout : int = 1, sleepTime : float = 0.00, max_size=8, **kwargs):

        GenericQueueProcessor.__init__(self, name=name, consumer=consumer, sleepTime=sleepTime, timeout=timeout,
                                       monitorRedisQueueForStopping=True, max_size=max_size, **kwargs)
        HeartbeatAndAlarmServer.logSource = getFullyQualifiedName(__file__, __class__)

        # self.connAlarmQ = CosThetaConfigurator.getInstance().getSoundQueue()
        self.redisConnectionQ = None
        self.clientRedisConnectedQ = False
        self.connectToRedis()
        logBoth('logInfo', HeartbeatAndAlarmServer.logSource, "Started Heartbeat and Alarm Server", Logger.GENERAL)

        self.qrCodeServerConnectionStatus : bool = False
        self.cameraServerConnectionStatus: bool = False
        self.ioServerConnectionStatus: bool = False
        self.databaseServerConnectionStatus: bool = False
        self.frontendServerConnectionStatus: bool = False

        self.consecutiveQRCodeServerConnectionDown : int = 0
        self.consecutiveCameraServerConnectionStatusDown : int = 0
        self.consecutiveIOServerConnectionStatusDown : int = 0
        self.consecutiveDatabaseServerConnectionStatusDown : int = 0
        self.consecutiveFrontendServerConnectionStatusDown : int = 0

        # Redis health: written and read back every redis.health.check.interval.sec (60 s; every 10 s while it fails)
        self.redisHealthy: bool = True
        self.redisHealthReason: str = ""
        self.redisChecks: int = 0
        self.launchRedisHealthCheckThread()
        self.launchConnectionAlarmMonitoringThread()
        self.launchThreadForBadComponentAlarm()
        logBoth('logCritical', HeartbeatAndAlarmServer.logSource, "************", Logger.SUCCESS)
        logBoth('logCritical', HeartbeatAndAlarmServer.logSource, "Started Connection and Alarm Monitoring Thread", Logger.SUCCESS)
        logBoth('logCritical', HeartbeatAndAlarmServer.logSource, "************", Logger.SUCCESS)


        self.gapBetweenConnectionProblemAlarms : float = CosThetaConfigurator.getInstance().getGapBetweenConnectionAlarms() # secs
        self.alarmSoundedAtLeastOnceForCurrentDisconnection : bool = False
        self.lastTimeAlarmSoundedInSecs = time.time()
        # print(HeartbeatServerAndAlarm.disconnectedSoundFile)

    def connectToRedis(self, forceRenew = False):
        if forceRenew:
            self.redisConnectionQ = None
            self.clientRedisConnectedQ = False
        if not self.clientRedisConnectedQ:
            try:
                self.redisConnectionQ = Redis(GenericQueueProcessor.redisHost, GenericQueueProcessor.redisPort, retry_on_timeout=True)
                self.clientRedisConnectedQ = True
            except Exception:
                self.clientRedisConnectedQ = False
                logBoth('logTakeAction', HeartbeatAndAlarmServer.logSource, 'Could not get Redis Connection', Logger.PROBLEM)
                # pass


    def addItem(self, item: Any) -> Union[Any, None]:
        return super().addItem(HeartbeatAndAlarmServer.beepMessage)

    def clearQueue(self):
        self.processingQueue.queue.clear()

    def launchRedisHealthCheckThread(self):
        """Redis is very stable, so it is checked only every redis.health.check.interval.sec (60 s): an entry is
        written to a stream nothing else uses (redis.healthcheck.q) and read back (checkRedisHealth). A failure -
        Redis not reachable, or reachable but no longer storing (out of memory, persistence error) - is reported in
        the status messages to the IOServer (no new cycle starts) and the frontend (notice), sounds the siren, and is
        printed (the central log goes through Redis too). While it fails, it is checked every 10 s."""
        objRef = self

        def check():
            interval = CosThetaConfigurator.getInstance().getRedisHealthCheckInterval()
            while not objRef.stopped:
                healthy, why = checkRedisHealth(objRef.redisConnectionQ)
                objRef.redisChecks += 1
                if healthy != objRef.redisHealthy:
                    objRef.redisHealthy, objRef.redisHealthReason = healthy, why
                    if healthy:
                        printBoldGreen("Redis check: storing data again")
                    else:
                        printBoldRed(f"Redis check FAILED: {why}")
                        objRef.addItem(HeartbeatAndAlarmServer.beepMessage)
                    try:
                        logBoth('logInfo' if healthy else 'logCritical', HeartbeatAndAlarmServer.logSource,
                                "Redis check: OK again" if healthy else f"Redis check FAILED: {why}",
                                Logger.SUCCESS if healthy else Logger.PROBLEM)
                    except Exception:
                        noteSuppressed()
                if not healthy:
                    objRef.connectToRedis(forceRenew=True)
                wait = interval if healthy else min(10.0, interval)
                end = time.monotonic() + wait
                while not objRef.stopped and time.monotonic() < end:
                    time.sleep(0.5)

        threading.Thread(name="Redis Health Check Thread", target=check, daemon=True).start()

    def launchConnectionAlarmMonitoringThread(self):
        objRef = self

        def monitorConnections():
            monitoringTimeGap = CosThetaConfigurator.getInstance().getCombinedHeartbeatConnectionStatusSleepInterval()
            logBoth('logInfo', HeartbeatAndAlarmServer.logSource,
                    f'Heartbeat monitoring interval: {monitoringTimeGap}s', Logger.GENERAL)

            counter: int = 0
            while not objRef.stopped:
                counter += 1
                startTime = time.monotonic()
                try:
                    # One multi-stream read instead of 5 sequential reads (each of which could block 0.5 s when a
                    # server was down, so one dead server delayed the status of all the others). Waits at most the
                    # monitoring interval, and only when no server has sent a heartbeat.
                    statuses = readAllHeartbeatsInHeartbeatServer(
                        objRef.redisConnectionQ,
                        block=int(monitoringTimeGap * 1000)
                    )

                    objRef.qrCodeServerConnectionStatus = statuses['qrcode']
                    objRef.cameraServerConnectionStatus = statuses['camera']
                    objRef.ioServerConnectionStatus = statuses['io']
                    objRef.databaseServerConnectionStatus = statuses['db']
                    objRef.frontendServerConnectionStatus = statuses['fe']

                    # ── update consecutive-down counters ─────────────────────────
                    def _update(current_status, counter_attr):
                        if not current_status:
                            setattr(objRef, counter_attr, getattr(objRef, counter_attr) + 1)
                        else:
                            setattr(objRef, counter_attr, 0)

                    _update(objRef.qrCodeServerConnectionStatus, 'consecutiveQRCodeServerConnectionDown')
                    _update(objRef.cameraServerConnectionStatus, 'consecutiveCameraServerConnectionStatusDown')
                    _update(objRef.ioServerConnectionStatus, 'consecutiveIOServerConnectionStatusDown')
                    _update(objRef.databaseServerConnectionStatus, 'consecutiveDatabaseServerConnectionStatusDown')
                    _update(objRef.frontendServerConnectionStatus, 'consecutiveFrontendServerConnectionStatusDown')

                    _MIN_ = HeartbeatAndAlarmServer.MINIMUM_CONSECUTIVE_DOWNS_NEEDED_FOR_ALARM

                    maxDisconnectionNumber = max(
                        objRef.consecutiveQRCodeServerConnectionDown,
                        objRef.consecutiveCameraServerConnectionStatusDown,
                        objRef.consecutiveIOServerConnectionStatusDown,
                        objRef.consecutiveDatabaseServerConnectionStatusDown,
                        objRef.consecutiveFrontendServerConnectionStatusDown,
                    )
                    if maxDisconnectionNumber > 10 and maxDisconnectionNumber % 10 == 0:
                        logBoth('logInfo', HeartbeatAndAlarmServer.logSource,
                                f"consecutiveDown: QR={objRef.consecutiveQRCodeServerConnectionDown}, "
                                f"Cam={objRef.consecutiveCameraServerConnectionStatusDown}, "
                                f"IO={objRef.consecutiveIOServerConnectionStatusDown}, "
                                f"DB={objRef.consecutiveDatabaseServerConnectionStatusDown}, "
                                f"FE={objRef.consecutiveFrontendServerConnectionStatusDown}",
                                Logger.GENERAL)

                    # ── send aggregated status to FE and IO ───────────────────────
                    sendHeartbeatsFromHeartbeatServerToFEServer(
                        redisConnection=objRef.redisConnectionQ,
                        cameraServerStatus=ALIVE if objRef.consecutiveCameraServerConnectionStatusDown < _MIN_ else DEAD,
                        qrCodeServerStatus=ALIVE if objRef.consecutiveQRCodeServerConnectionDown < _MIN_ else DEAD,
                        ioServerStatus=ALIVE if objRef.consecutiveIOServerConnectionStatusDown < _MIN_ else DEAD,
                        dbServerStatus=ALIVE if objRef.consecutiveDatabaseServerConnectionStatusDown < _MIN_ else DEAD,
                        redisStatus=ALIVE if objRef.redisHealthy else DEAD,
                    )

                    allok = all([
                        objRef.consecutiveQRCodeServerConnectionDown < _MIN_,
                        objRef.consecutiveCameraServerConnectionStatusDown < _MIN_,
                        objRef.consecutiveIOServerConnectionStatusDown < _MIN_,
                        objRef.consecutiveDatabaseServerConnectionStatusDown < _MIN_,
                        objRef.consecutiveFrontendServerConnectionStatusDown < _MIN_,
                        objRef.redisHealthy,
                    ])

                    # the combined status and each server's own: the IOServer acts per server (camera down -> the
                    # picture is NOT OK, DB down -> next cycle held, frontend down -> abort; QR code: nothing)
                    sendCombinedHeartbeatFromHeartbeatServerToIOServer(
                        redisConnection=objRef.redisConnectionQ,
                        combinedConnectionStatus=ALIVE if allok else DEAD,
                        serverStatuses={
                            "camera": ALIVE if objRef.consecutiveCameraServerConnectionStatusDown < _MIN_ else DEAD,
                            "qrcode": ALIVE if objRef.consecutiveQRCodeServerConnectionDown < _MIN_ else DEAD,
                            "io": ALIVE if objRef.consecutiveIOServerConnectionStatusDown < _MIN_ else DEAD,
                            "db": ALIVE if objRef.consecutiveDatabaseServerConnectionStatusDown < _MIN_ else DEAD,
                            "fe": ALIVE if objRef.consecutiveFrontendServerConnectionStatusDown < _MIN_ else DEAD,
                            "redis": ALIVE if objRef.redisHealthy else DEAD,
                        },
                    )

                    objRef.clearQueue()

                    if not allok:
                        if not objRef.alarmSoundedAtLeastOnceForCurrentDisconnection:
                            objRef.addItem(HeartbeatAndAlarmServer.beepMessage)
                            objRef.alarmSoundedAtLeastOnceForCurrentDisconnection = True
                            objRef.lastTimeAlarmSoundedInSecs = time.time()
                        else:
                            if time.time() - objRef.lastTimeAlarmSoundedInSecs > objRef.gapBetweenConnectionProblemAlarms:
                                objRef.addItem(HeartbeatAndAlarmServer.beepMessage)
                                objRef.lastTimeAlarmSoundedInSecs = time.time()
                        if maxDisconnectionNumber > 1 and maxDisconnectionNumber % 3 == 0:
                            logBoth('logTakeAction', HeartbeatAndAlarmServer.logSource,
                                    f'Connections NOT all OK: '
                                    f'QR={objRef.qrCodeServerConnectionStatus}, '
                                    f'Cam={objRef.cameraServerConnectionStatus}, '
                                    f'IO={objRef.ioServerConnectionStatus}, '
                                    f'DB={objRef.databaseServerConnectionStatus}, '
                                    f'FE={objRef.frontendServerConnectionStatus}',
                                    Logger.PROBLEM)
                    else:
                        objRef.alarmSoundedAtLeastOnceForCurrentDisconnection = False
                        if counter % 50 == 0:
                            logBoth('logTakeNote', HeartbeatAndAlarmServer.logSource,
                                    'All connections are ok', Logger.SUCCESS)

                except Exception as e:
                    logBoth('logTakeAction', HeartbeatAndAlarmServer.logSource,
                            f'Problem in monitorConnections: {e}', Logger.PROBLEM)
                    objRef.connectToRedis()

                # ── precise sleep: subtract actual elapsed time ───────────────────
                elapsed = time.monotonic() - startTime
                sleepTime = monitoringTimeGap - elapsed
                if sleepTime > 0:
                    time.sleep(sleepTime)

        monitoringThread = threading.Thread(
            name="Connection Alarm Monitoring Thread",
            target=monitorConnections,
            args=(),
            daemon=True,
        )
        monitoringThread.start()

    def launchThreadForBadComponentAlarm(self):
        objRef = self
        def sniffForAlarm():
            while not objRef.stopped:
                try:
                    alarmSignalReceived = readAlarmInHeartbeatServer(objRef.redisConnectionQ)
                    if alarmSignalReceived:
                        if HeartbeatAndAlarmServer.linux:
                            os.system(
                                '( speaker-test -t sine -f 1500 >/dev/null) & pid=$! ; sleep 0.4s ; kill -9 $pid ')
                        #       check sudo apt install beep
                        #       Then, use os.system("beep -f 1500 -l 400")
                        elif HeartbeatAndAlarmServer.windows:
                            # for i in range(CosThetaConfigurator.getInstance().getBeeperRepeats()):
                            #     winsound.Beep(1500, 500)
                            #     time.sleep(0.5)
                            winsound.PlaySound(HeartbeatAndAlarmServer.badComponentSoundFile, winsound.SND_FILENAME)

                except Exception:
                    objRef.connectToRedis(forceRenew=True)
                try:
                    time.sleep(1.0)
                except Exception:
                    noteSuppressed()
        monitorAlarmThread = threading.Thread(name="Alarm Monitoring Thread", target=sniffForAlarm, args=(), daemon = True)
        monitorAlarmThread.start()

    def preWorkLoop(self):
        return

    def processItem(self, item : Any) -> Any :
        if HeartbeatAndAlarmServer.linux:
            os.system('( speaker-test -t sine -f 1500 >/dev/null) & pid=$! ; sleep 0.4s ; kill -9 $pid ')
        #       check sudo apt install beep
        #       Then, use os.system("beep -f 1500 -l 400")
        elif HeartbeatAndAlarmServer.windows:
            # for i in range(CosThetaConfigurator.getInstance().getBeeperRepeats()):
            #     winsound.Beep(1500, 500)
            #     time.sleep(0.5)
            winsound.PlaySound(HeartbeatAndAlarmServer.disconnectedSoundFile, winsound.SND_FILENAME)
        return None

    def postWorkLoop(self):
        return

def startHeartbeatAndAlarmServer():
    hdServer=HeartbeatAndAlarmServer()
    hdServer.start()
    logBoth('logInfo', HeartbeatAndAlarmServer.logSource, "Started HeartbeatAndAlarmServer", Logger.GENERAL)
    hdServer.join()
    sys.exit(0)

# testRedisConnection = Redis(CosThetaConfigurator.getInstance().getRedisHost(), CosThetaConfigurator.getInstance().getRedisPort(), retry_on_timeout=True)
# testRedisConnection.flushdb()
# clearQueues(testRedisConnection)
# startHeartbeatServerAndAlarm()