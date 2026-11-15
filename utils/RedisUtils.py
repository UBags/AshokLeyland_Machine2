from utils.SuppressedExceptions import noteSuppressed   # logs exceptions that are caught and ignored
import os
import time
from typing import Literal, Union, Tuple, Optional

import numpy as np
import cv2
import json
import redis.client
from redis import Redis, ConnectionPool, ConnectionError

# From statemachine
from statemachine.StateMachine import MachineState

# From utils
from utils.CosThetaPrintUtils import (
    printBoldRed,
    printBoldGreen,
    printBoldBlue,
    printBold,
    printPlain
)

# From BaseUtils
from BaseUtils import (
    getFullyQualifiedName,
    getCurrentTimeInMS,
    getPostgresDatetimeFromString,
)

# From Configuration
from Configuration import CosThetaConfigurator

CosThetaConfigurator.getInstance()
logSource = getFullyQualifiedName(__file__)

# Various states
EMPTY_IMAGE_STRING : str = ""
BYPASSED_RESULT : str = "Bypassed"          # result of a step skipped by the model's Machine Setting (frontend BYPASSED_TEXT)
UNKNOWN : str = "Unknown"
INVALID_STATE : str  = "Invalid State"
INVALID_RESULT : str  = "Invalid Result"
TAKE_NEXT_PICTURE : str  = "Take Next Picture"
HANDSHAKE : str  = "Handshake"
ALIVE : str  = "Alive"
DEAD : str = "Dead"
ABORT : str = "Abort"
END_OF_CYCLE : str = "End Of Cycle"      # IOServer -> CameraServer: the cycle is over (completed or aborted)
DOST : str = "DOST"
DOSTPLUS : str = "DOSTPLUS"
GARUDA : str = "GARUDA"

# Various keys
commonStreamKey: str = "genericstream"
succesKeyString: str = "successStatus"
timeKeyString: str = "timestamp"
producerKeyString: str = "producer"
originalImageKeyString: str = "originalImage"
processedImageKeyString: str = "processedImage"
resultKeyString: str = "result"
genericdataKeyString: str = "data"
idKeyString: str = "id"
qrCodeKeyString: str = "qrCode"
qrCodeStatusKeyString: str = "qrCodeStatus"
qrCodeDisplayKeyString: str = "qrCodeDisplay"
componentQRCodeKeyString: str = "componentQRCode"
currentMachineStateKeyString: str = "currentMachineState"
actionKeyString : str = "action"
move2StateKeyString : str = "moveToState"
valueKeyString : str = "value"
knucklePictureKeyString : str = "knucklePicture"
knuckleCheckResultKeyString : str = "knuckleCheckResult"
knuckleDatetimeKeyString : str = "knuckleDatetime"
hubAndBottomBearingPictureKeyString : str = "hubAndBottomBearingPicture"
hubAndBottomBearingCheckResultKeyString : str = "hubAndBottomBearingCheckResult"
hubAndBottomBearingDatetimeKeyString : str = "hubAndBottomBearingDatetime"
topBearingPictureKeyString : str = "topBearingPicture"
topBearingCheckResultKeyString : str = "topBearingCheckResult"
topBearingDatetimeKeyString : str = "topBearingDatetime"
nutAndPlateWasherPictureKeyString : str = "nutAndPlateWasherPicture"
nutAndPlateWasherCheckResultKeyString : str = "nutAndPlateWasherCheckResult"
nutAndPlateWasherDatetimeKeyString : str = "nutAndPlateWasherDatetime"
tighteningTorque1ValueKeyString : str = "tight1Value"
tighteningTorque1ResultKeyString : str = "tight1Result"
tighteningTorque1DatetimeKeyString : str = "tighteningTorque1Datetime"
componentPressBunkPictureKeyString : str = "componentPressBunkPicture"
componentPressBunkCheckResultKeyString : str = "componentPressBunkCheckResult"
componentPressBunkCheckDatetimeKeyString : str = "componentPressBunkCheckDatetime"
componentPressDoneResultKeyString : str = "componentPressDoneResult"
componentPressDoneDatetimeKeyString : str = "componentPressDoneDatetime"
noBunkPictureKeyString : str = "noBunkPicture"
noBunkCheckResultKeyString : str = "noBunkCheckResult"
noBunkCheckDatetimeKeyString : str = "noBunkCheckDatetime"
tighteningTorque2ValueKeyString : str = "tight2Value"
tighteningTorque2ResultKeyString : str = "tight2Result"
tighteningTorque2DatetimeKeyString : str = "tighteningTorque2Datetime"
splitPinAndWasherPictureKeyString : str = "splitpinAndWasherPicture"
splitPinAndWasherCheckResultKeyString : str = "splitPinAndWasherCheckResult"
splitPinAndWasherCheckDatetimeKeyString : str = "splitPinAndWasherCheckDatetime"
capPictureKeyString : str = "capPicture"
capCheckResultKeyString : str = "capCheckResult"
capCheckDatetimeKeyString : str = "capCheckDatetime"
capPressBunkPictureKeyString : str = "capPressBunkPicture"
capPressBunkCheckResultKeyString : str = "capPressBunkCheckResult"
capPressBunkCheckDatetimeKeyString : str = "capPressBunkCheckDatetime"
capPressDoneResultKeyString : str = "pressDoneResult"
capPressDoneDatetimeKeyString : str = "capPressDatetime"
freeRotationTorque1ValueKeyString : str = "rotation1Value"
freeRotationTorque1ResultKeyString : str = "rotation1Result"
freeRotationTorque1DatetimeKeyString : str = "rotationTorque1Datetime"
overallResultKeyString : str = "overallResult"
tighteningTorque1RotationDoneKeyString : str = "torque1RotationDone"
tighteningTorque2RotationDoneKeyString : str = "torque2RotationDone"
abortedKeyString : str = "aborted"                  # cycle record: aborted ("1" / "0")
abortReasonKeyString : str = "abortReason"          # cycle record: why it was aborted
bypassedKeyString : str = "bypassedPressAndTightening2"   # cycle record: bung / press / second tightening bypassed
rotationDoneKeyString : str = "rotationDone"           # io2fe: with a Station 2 torque value
# io2camera -> camera2io / camera2fe: the IOServer's id for one picture request, echoed in the answers, so that an
# answer that comes too late (after the IOServer stopped waiting for it) is not taken for a later request's result
requestIdKeyString : str = "requestId"
reasonKeyString : str = "reason"                       # camera2fe: why a result is NOT OK (shown on the screen)
feServerStatusKeyString : str = "festatus"             # hb2io: the frontend's status
ACTIVE_CAMERA_REQUEST_KEY : str = "camera:activeRequest"   # the request whose answer the frontend is to show
FRONTEND_NOTICE_PREFIX : str = "feNotice:"             # IOServer -> frontend: messages shown flashing, top right
# IOServer -> frontend: every IOServer state change (CHANGES 108, state sync phase A). A stream with one producer (the
# IOServer) and one consumer (the frontend), capped, plus a key holding the latest snapshot for a frontend that starts
# (or restarts) in the middle of a cycle.
IO_STATE_STREAM : str = "io2festate.q"
IO_STATE_KEY : str = "io:state"
IO_STATE_STREAM_MAXLEN : int = 500


def encodeOptionalBool(value) -> str:
    """True / False / None -> "True" / "False" / "" (Redis stream fields are strings)."""
    return "" if value is None else ("True" if bool(value) else "False")


def decodeOptionalBool(value):
    """"True" / "False" / "" (str or bytes) -> True / False / None."""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    value = (value or "").strip().lower()
    return True if value == "true" else False if value == "false" else None
connectionStatusKeyString : str = "connectionStatus"

cameraServerStatusKeyString : str = "camerastatus"
ioServerStatusKeyString : str = "iostatus"
qrCodeServerStatusKeyString : str = "qrcodestatus"
dbServerStatusKeyString : str = "dbstatus"
alarmStatusKeyString : str = "alarmstatus"
emergencyStatusKeyString : str = "emergencystatus"

# Various commands
ok : str = CosThetaConfigurator.getInstance().getOkCommand()
notok : str = CosThetaConfigurator.getInstance().getNotOKCommand()

startCommand : str = CosThetaConfigurator.getInstance().getStartCommand()
stopCommand : str = CosThetaConfigurator.getInstance().getStopCommand()
exitCommand : str = CosThetaConfigurator.getInstance().getExitCommand()
requestCommand : str = CosThetaConfigurator.getInstance().getRequestCommand()
updateValueCommand : str = CosThetaConfigurator.getInstance().getUpdateValueCommand()
takePictureCommand : str = CosThetaConfigurator.getInstance().getTakePictureCommand()
readQRCodeCommand : str = CosThetaConfigurator.getInstance().getReadQRCodeCommand()
noActionCommand : str = CosThetaConfigurator.getInstance().getNoActionCommand()
moveAheadToNextComponent : str = CosThetaConfigurator.getInstance().getMoveAheadToNextComponentCommand()
# beepCommand = CosThetaConfigurator.getInstance().getDiscInterlockMessage()

stoppedResponse : str = CosThetaConfigurator.getInstance().getStoppedResponse()
rejectResponse : str = CosThetaConfigurator.getInstance().getRejectResponse()

# Various queues

io2feq : str = CosThetaConfigurator.getInstance().getIOToFrontEndQueue()
io2cameraq : str = CosThetaConfigurator.getInstance().getIOToCameraQueue()
io2qrcodeq : str = CosThetaConfigurator.getInstance().getIOToQRCodeQueue()
io2qrcodeabortq : str = CosThetaConfigurator.getInstance().getIOToQRCodeAbortQueue()
io2hbq : str = CosThetaConfigurator.getInstance().getIOToHeartbeatQueue()
qrcode2ioq : str = CosThetaConfigurator.getInstance().getQRCodeToIOQueue()
qrcode2feq : str = CosThetaConfigurator.getInstance().getQRCodeToFrontendQueue()
qrcode2hbq : str = CosThetaConfigurator.getInstance().getQRCodeToHeartbeatQueue()
qrcode2cameraq : str = CosThetaConfigurator.getInstance().getQRCodeToCameraQueue()
fe2ioq : str = CosThetaConfigurator.getInstance().getFrontendToIOQueue()
fe2dbq : str = CosThetaConfigurator.getInstance().getFrontendToDatabaseQueue()
fe2hbq : str = CosThetaConfigurator.getInstance().getFrontendToHeartbeatQueue()
camera2ioq : str = CosThetaConfigurator.getInstance().getCameraToIOQueue()
camera2feq : str = CosThetaConfigurator.getInstance().getCameraToFrontendQueue()
camera2hbq : str = CosThetaConfigurator.getInstance().getCameraToHeartbeatQueue()
db2hbq : str = CosThetaConfigurator.getInstance().getDatabaseToHeartbeatQueue()
hb2feq : str = CosThetaConfigurator.getInstance().getHeartbeatToFrontendQueue()
hb2ioq : str = CosThetaConfigurator.getInstance().getHeartbeatToIOQueue()
alarmq : str = CosThetaConfigurator.getInstance().getAlarmQueue()
emergencyq : str = CosThetaConfigurator.getInstance().getEmergencyQueue()
stopQ : str = CosThetaConfigurator.getInstance().getStopCommandQueue()
stoppedResponseQ : str = CosThetaConfigurator.getInstance().getStoppedResponseQueue()

# Empty Image

blankWhiteImage = 255 * np.ones(shape=(720, 1280, 3), dtype=np.uint8)
blankWhiteImage = cv2.putText(blankWhiteImage, "Picture Not Received", (500, 300), cv2.FONT_HERSHEY_SIMPLEX,
                        1, (255,0,0), 2, cv2.LINE_AA)

ActionType = Literal[updateValueCommand, takePictureCommand, readQRCodeCommand, noActionCommand, moveAheadToNextComponent, ABORT, END_OF_CYCLE]
ResultType = Literal[ok,notok]
ConnectionStatusType = Literal[DEAD, ALIVE]
ComponentName = Literal[DOST, DOSTPLUS, GARUDA]

# Redis Connection For Clearing Key Queues
redisHostname: str = CosThetaConfigurator.getInstance().getRedisHost()
redisPort: int = CosThetaConfigurator.getInstance().getRedisPort()
redisPoolForClearingKeyQueues = redis.ConnectionPool(host=redisHostname, port=redisPort, max_connections=3)

# ***********************************************************************************

def validate_image(image: Union[np.ndarray, None]) -> bool:
    """Validate that an image is a valid NumPy array for OpenCV."""
    if image is None:
        # printBoldRed("Image is None")
        return False
    if not isinstance(image, np.ndarray):
        # printBoldRed(f"Image is not a NumPy array, got {type(image)}")
        return False
    if image.size == 0:
        # printBoldRed(f"Image is empty: {image.shape}")
        return False
    if image.dtype != np.uint8:
        # printBoldRed(f"Invalid image dtype: {image.dtype}, expected np.uint8")
        return False
    return True

# ***********************************************************************************

def sendImageWithResult(redisConnection: redis.client.Redis, anImage: Union[np.ndarray, None], queueName: str = commonStreamKey,
                        aProducer: str = "Producer", result: ResultType = ok, trackTime: bool = False, handshake : bool = False):
    # print(f'Image shape is {anImage.shape} and anImage dtype is {anImage.dtype}')
    # printBoldBlue(f"Redis connection is of type : {type(redisConnectionLeftCameraPicture)}")
    if redisConnection is None:
        printBoldRed("Redis connection is None. Cannot send image.")
        # raise Exception("Redis Connection is None")
        return False
    if anImage is None:
        printBoldRed("Image is None. Cannot send image.")
        return False
    if queueName is None:
        printBoldRed("Queue Name is None. Cannot send image.")
        return False
    if trackTime:
        time1 : float = time.time()
    buffer = None
    try:
        if isinstance(anImage, np.ndarray) and anImage is not None:
            retval, buffer = cv2.imencode('.png', anImage)
    except Exception:
        # raise e
        return False
    imageAsMessage = ""
    if buffer is not None:
        imageAsMessage = np.array(buffer).tobytes()
    # printBoldBlue(f"Encoded startingImage is of type : {type(message)}")
    data = {
        timeKeyString: getCurrentTimeInMS(),
        producerKeyString: aProducer,
        originalImageKeyString: imageAsMessage,
        resultKeyString: result if not handshake else HANDSHAKE
    }
    try:
        redisConnection.xadd(queueName, data)
        if trackTime:
            time2 : float = time.time()
            timeTaken = round((time2 - time1) * 1000, 2)
            # printPlain(f'{queueName} : Producer sent anImage in {timeTaken} ms')
        return True
    except ConnectionError:
        # raise e1
        # return False
        noteSuppressed()
    return False

# ***********************************************************************************

def sendData(redisConnection: redis.client.Redis, data: dict, queueName: str = commonStreamKey,
             aProducer: str = "Producer", trackTime: bool = False):
    if redisConnection is None:
        printBoldRed("Redis connection is None. Cannot send data.")
        # raise Exception("Redis Connection is None")
        return False
    if data is None:
        printBoldRed("Data is None. Cannot send data.")
        return False
    if queueName is None:
        printBoldRed("Queue Name is None. Cannot send data.")
        return False
    else:
        # printLight(f"Queue Name is {queueName}")
        pass
    if trackTime:
        time1 : float = time.time()
    data[timeKeyString] = getCurrentTimeInMS()
    data[producerKeyString] = aProducer
    try:
        redisConnection.xadd(queueName, data)
        if trackTime:
            time2 : float = time.time()
            timeTaken = round((time2 - time1) * 1000, 2)
            # printPlain(f'{queueName} : Producer sent data in {timeTaken} ms')
        if ("hb" not in queueName) and ("alarmQ" not in queueName) and ("logging" not in queueName):
            # printPlain(f'{queueName} : Producer sent {data = }')
            pass
        return True
    except ConnectionError as e1:
        printBoldRed(f"Error {e1} while sending data from {aProducer}")
        # raise e1
    return False

# ***********************************************************************************

def sendStopCommand(redisConnection: redis.client.Redis, aProducer : str = "Producer"):
    # printBoldRed(f"Sending stop command from process {os.getpid()}")
    sendData(redisConnection=redisConnection, data={genericdataKeyString: stopCommand}, queueName=stopQ, aProducer=aProducer)

def sendStoppedResponse(redisConnection: redis.client.Redis, aProducer: str = "Producer"):
    sendData(redisConnection=redisConnection, data={genericdataKeyString: stoppedResponse}, queueName=stoppedResponseQ, aProducer=aProducer)


# ***********************************************************************************

sleep_ms: int = 5000

# RETURNS timestamp, producer, and ndarray
def getImage(redisConnection: redis.client.Redis, queueName: str = commonStreamKey, block : int = sleep_ms, trackTime: bool = False):
    # Note this is not in a 'while not done' loop. This ensures that this function call does not block
    # Calling programs will need to implement a loop, if needed. Or, run this as an aync call
    if redisConnection is None:
        printBoldRed("Redis connection is None. Cannot receive data.")
        # raise Exception("Redis Connection is None")
        return None, None, None
    if queueName is None:
        printBoldRed("Queue Name is None. Cannot receive data.")
        return None, None, None
    last_id = 0
    decodedImage = None
    timeOfMessage = None
    aProducer = None
    try:
        resp = redisConnection.xread(
            {queueName: last_id}, count=1, block=block
        )
        if resp:
            if trackTime:
                time1 : float = time.time()
            key, messages = resp[0]
            last_id, data = messages[0]
            keys = data.keys()
            newData = {}
            for key in keys:
                value = data[key]
                newData[key.decode("utf-8")] = value
            timeOfMessage = newData[timeKeyString].decode('utf-8')
            aProducer = newData[producerKeyString].decode('utf-8')
            decodedImage = blankWhiteImage
            try:
                if newData[originalImageKeyString] is not None:
                    decodedImage = cv2.imdecode(np.frombuffer(newData[originalImageKeyString], np.uint8), 1)
            except Exception:
                printBoldRed("Facing problem in decoding image from byte stream")
                pass
            redisConnection.xdel(queueName, last_id)
            # printBoldBlue(f"{timeOfMessage = }; {aProducer = }; {decodedImage.shape}")
            if trackTime:
                time2 : float = time.time()
                timeTaken = round((time2 - time1) * 1000, 2)
                # printPlain(f'{queueName} : REDIS ID --> {last_id} got from redis queue in {timeTaken} ms')
                # printPlain(f'{queueName} : Consumer received data in {timeTaken} ms')
            # if isinstance(decodedImage, np.ndarray):
            #     printBoldBlue(f'Decoded startingImage has shape {decodedImage.shape} and type {decodedImage.dtype}')
    except ConnectionError as e1:
        printBoldRed(f"Error while receiving startingImage : {e1}")
        # raise e1
        # return None, None, None
    return timeOfMessage, aProducer, decodedImage

# RETURNS timestamp, producer, result, and ndarray
def getImageWithResult(redisConnection: redis.client.Redis, queueName: str = commonStreamKey, block : int = sleep_ms, trackTime: bool = False):
    # Note this is not in a 'while not done' loop. This ensures that this function call does not block
    # Calling programs will need to implement a loop, if needed. Or, run this as an aync call
    if redisConnection is None:
        printBoldRed("Redis connection is None. Cannot receive data.")
        # raise Exception("Redis Connection is None")
        return None, None, None, None
    if queueName is None:
        printBoldRed("Queue Name is None. Cannot receive data.")
        return None, None, None, None
    last_id = 0
    decodedImage = None
    decodedResult = None
    timeOfMessage = None
    aProducer = None
    try:
        redisConnection.xtrim(queueName, 1)
        resp = redisConnection.xread(
            {queueName: last_id}, count=1, block=block
        )
        if resp:
            if trackTime:
                time1 : float = time.time()
            key, messages = resp[0]
            last_id, data = messages[0]
            keys = data.keys()
            newData = {}
            for key in keys:
                value = data[key]
                newData[key.decode("utf-8")] = value
            decodedResult = newData[resultKeyString].decode('utf-8')
            timeOfMessage = newData[timeKeyString].decode('utf-8')
            aProducer = newData[producerKeyString].decode('utf-8')
            decodedImage = blankWhiteImage
            if decodedResult != HANDSHAKE:
                try:
                    if newData[originalImageKeyString] is not None:
                        decodedImage = cv2.imdecode(np.frombuffer(newData[originalImageKeyString], np.uint8), 1)
                except Exception:
                    printBoldRed("Facing problem in decoding image from byte stream")
                    pass
            redisConnection.xdel(queueName, last_id)
            # printBoldBlue(f"{decodedResult = }; {timeOfMessage = }; {aProducer = }; {decodedImage.shape}")
            if trackTime:
                time2 : float = time.time()
                timeTaken = round((time2 - time1) * 1000, 2)
                # printPlain(f'{queueName} : REDIS ID --> {last_id} got from redis queue in {timeTaken} ms')
                # printPlain(f'{queueName} : Consumer received data in {timeTaken} ms')
            # if isinstance(decodedImage, np.ndarray):
            #     printBoldBlue(f'Decoded startingImage has shape {decodedImage.shape} and type {decodedImage.dtype}')
    except ConnectionError as e1:
        printBoldRed(f"Error while receiving image with result : {e1}")
        # raise e1
    if decodedResult == HANDSHAKE:
        return timeOfMessage, aProducer, None, None
    return timeOfMessage, aProducer, decodedResult, decodedImage

# RETURNS timestamp, and data as a dictionary
def getData(redisConnection: redis.client.Redis, queueName: str = commonStreamKey, trackTime: bool = False, block : int = sleep_ms):
    if redisConnection is None:
        printBoldRed("Redis connection is None. Cannot receive data.")
        # raise Exception("Redis Connection is None")
        return None, None
    if queueName is None:
        printBoldRed("Queue Name is None. Cannot receive data.")
        return None, None
    last_id = 0
    # done: bool = False
    newData = {}
    timeOfMessage = None
    # while not done:
    try:
        resp = redisConnection.xread(
            {queueName: last_id}, count=1, block=block
        )
        if resp:
            if trackTime:
                time1 = time.time()
            key, messages = resp[0]
            last_id, data = messages[0]
            keys = data.keys()
            for key in keys:
                value = data[key].decode('utf-8')
                newData[key.decode("utf-8")] = value
            timeOfMessage = newData[timeKeyString]
            redisConnection.xdel(queueName, last_id)
            if trackTime:
                time2 = time.time()
                timeTaken = round((time2 - time1) * 1000, 2)
                # printPlain(f'{queueName} : Consumer received data in {timeTaken} ms')
                # printPlain(f'{queueName} : REDIS ID --> {last_id}; data --> {data} got from redis queue in {timeTaken} ms')
            # done = True
    except ConnectionError as e1:
        printBoldRed(f"Error while receiving data : {e1}")
        # raise e1
        pass
    return timeOfMessage, newData

# RETURNS timestamp and boolean
def getStopCommandFromQueue(redisConnection: redis.client.Redis, block : int = 1):
    # Note this is not in a 'while not done' loop. This ensures that this function call does not block
    # Calling programs will need to implement a loop, if needed. Or, run this as an aync call
    if redisConnection is None:
        printBoldRed("Redis connection is None. Cannot receive data.")
        # raise Exception("Redis Connection is None")
        return None, None
    # printBoldRed(f"{getGeneralLoggingMessage(getFullyQualifiedName(__file__), 'getStopCommand() invoked')}")
    last_id = 0
    newData = {}
    timeOfMessage = None
    try:
        resp = redisConnection.xread({stopQ: last_id}, count=1, block=block)
        if resp:
            key, messages = resp[0]
            last_id, data = messages[0]
            keys = data.keys()
            for key in keys:
                value = data[key]
                newData[key.decode("utf-8")] = value
            timeOfMessage = newData[timeKeyString].decode("utf-8")
    except ConnectionError as e1:
        printBoldRed(f"Error while receiving stop command : {e1}")
        # raise e1
        return None, None
    stopNow = None
    try:
        stopNow = newData[genericdataKeyString].decode('utf-8')
    except Exception:
        noteSuppressed()
    # printBoldRed(f"{getGeneralLoggingMessage(getFullyQualifiedName(__file__), 'in getStopCommand()')} {stopNow =}")
    if stopNow is not None:
        if stopNow == stopCommand:
            # printBoldRed(f"Got stop command in process {os.getpid()}")
            return timeOfMessage, True
    return timeOfMessage, False

# RETURNS timestamp, sender of the stopped response, and the stopped response
def getStoppedResponseFromQueue(redisConnection: redis.client.Redis, block : int = 1):
    # Note that there is no 'while not done' loop in this code. This ensures that this doesn't block in the calling program.
    # The calling program will need to implement a loop to call this functions
    if redisConnection is None:
        printBoldRed("Redis connection is None. Cannot receive data.")
        # raise Exception("Redis Connection is None")
        return None, "", ""
    last_id = 0
    newData = {}
    timeOfMessage = None

    try:
        resp = redisConnection.xread({stoppedResponseQ: last_id}, count=1, block=block)
        if resp:
            key, messages = resp[0]
            last_id, data = messages[0]
            keys = data.keys()
            for key in keys:
                value = data[key]
                newData[key.decode("utf-8")] = value
            timeOfMessage = newData[timeKeyString].decode("utf-8")
            redisConnection.xdel(stoppedResponseQ, last_id)
    except ConnectionError as e1:
        printBoldRed(f"Error while receiving data : {e1}")
        return timeOfMessage, "", ""
        # raise e1
    producer = None
    stoppedNow = None
    try:
        producer = newData[producerKeyString].decode('utf-8')
        stoppedNow = newData[genericdataKeyString].decode('utf-8')
    except Exception:
        noteSuppressed()
    if (producer is not None) and (stoppedNow is not None):
        if stoppedNow == stoppedResponse:
            return timeOfMessage, producer, stoppedResponse
    return timeOfMessage, "", ""


def getMessageCount(redisConnection: redis.client.Redis, queueName: str = commonStreamKey):
    if redisConnection is None:
        printBoldRed("Redis connection is None. Cannot get count.")
        return -1
    if queueName is None:
        printBoldRed("Queue Name is None. Cannot receive data.")
        return -1
    try:
        numberOfEntries = redisConnection.xlen(queueName)
    except ConnectionError as e1:
        printBoldRed(f"Error while getting message count : {e1}")
        return -1
    return numberOfEntries

# RETURNS timestamp and boolean
def getTakeNextPictureCommandFromCameraId(redisConnection: redis.client.Redis, cameraId : int = 1, block : int = 1):
    # Note this is not in a 'while not done' loop. This ensures that this function call does not block
    # Calling programs will need to implement a loop, if needed. Or, run this as an aync call
    if redisConnection is None:
        printBoldRed("Redis connection is None. Cannot receive data.")
        # raise Exception("Redis Connection is None")
        return None, False
    last_id = 0
    newData = {}
    timeOfMessage = None
    cameraQ = CosThetaConfigurator.getInstance().getCameraTakePicQueue(cameraId=cameraId)
    try:
        redisConnection.xtrim(cameraQ, 1)
        resp = redisConnection.xread({cameraQ: last_id}, count=1, block=block)
        if resp:
            key, messages = resp[0]
            last_id, data = messages[0]
            keys = data.keys()
            for key in keys:
                value = data[key]
                newData[key.decode("utf-8")] = value.decode("utf-8")
            timeOfMessage = newData[timeKeyString]
            redisConnection.xdel(cameraQ, last_id)
    except ConnectionError as e1:
        printBoldRed(f"Error while getting next picture command : {e1}")
        # raise e1
        return None, False
    try:
        if (newData[genericdataKeyString] is not None) and (newData[genericdataKeyString] == TAKE_NEXT_PICTURE):
            return timeOfMessage, True
    except Exception:
        return timeOfMessage, False
    return timeOfMessage, False

# RETURNS timestamp and boolean
def getRequestResultCommandFromIOServer(redisConnection: redis.client.Redis, requestResultQ : str, block : int = 500):
    # Note this is not in a 'while not done' loop. This ensures that this function call does not block
    # Calling programs will need to implement a loop, if needed. Or, run this as an aync call
    if redisConnection is None:
        printBoldRed("Redis connection is None. Cannot receive data.")
        # raise Exception("Redis Connection is None")
        return None, False
    last_id = 0
    newData = {}
    timeOfMessage = None
    try:
        redisConnection.xtrim(requestResultQ, 1)
        resp = redisConnection.xread({requestResultQ: last_id}, count=1, block=block)
        if resp:
            key, messages = resp[0]
            last_id, data = messages[0]
            keys = data.keys()
            for key in keys:
                value = data[key]
                newData[key.decode("utf-8")] = value.decode("utf-8")
            timeOfMessage = newData[timeKeyString]
            redisConnection.xdel(requestResultQ, last_id)
    except ConnectionError as e1:
        printBoldRed(f"Error while receiving request-result command : {e1}")
        # raise e1
        return None, False
    try:
        if (newData[genericdataKeyString] is not None) and (newData[genericdataKeyString] == HANDSHAKE):
            return None, False
    except Exception:
        noteSuppressed()
    try:
        if (newData[genericdataKeyString] is not None) and (newData[genericdataKeyString] == requestCommand):
            return timeOfMessage, True
    except Exception:
        return timeOfMessage, False
    return timeOfMessage, False

def getTakeNextPictureCommandFromCameraQ(redisConnection: redis.client.Redis, cameraQ : str, block : int = 1):
    # Note this is not in a 'while not done' loop. This ensures that this function call does not block
    # Calling programs will need to implement a loop, if needed. Or, run this as an aync call
    if redisConnection is None:
        printBoldRed("Redis connection is None. Cannot receive data.")
        # raise Exception("Redis Connection is None")
        return None, False
    last_id = 0
    newData = {}
    timeOfMessage = None
    try:
        redisConnection.xtrim(cameraQ, 1)
        # printBold(f"After trimming {cameraQ} to 1")
        resp = redisConnection.xread({cameraQ: last_id}, count=1, block=block)
        # printBold(f"Got resp in {cameraQ} as {resp}")
        if resp:
            key, messages = resp[0]
            last_id, data = messages[0]
            keys = data.keys()
            for key in keys:
                value = data[key]
                newData[key.decode("utf-8")] = value.decode("utf-8")
            timeOfMessage = newData[timeKeyString]
            redisConnection.xdel(cameraQ, last_id)
    except ConnectionError as e1:
        printBoldRed(f"Error while getting next picture command : {e1}")
        # raise e1
        return None, False
    try:
        if (newData[genericdataKeyString] is not None) and (newData[genericdataKeyString] == HANDSHAKE):
            return None, False
    except Exception:
        noteSuppressed()
    try:
        if (newData[genericdataKeyString] is not None) and (newData[genericdataKeyString] == TAKE_NEXT_PICTURE):
            return timeOfMessage, True
    except Exception:
        return timeOfMessage, False
    return timeOfMessage, False


# ********************************** RECORDING COMMANDS START - RARELY USED *************************

def sendRecordingStart(redisConnection: redis.client.Redis, cameraId: int = 1):
    queueName = CosThetaConfigurator.getInstance().getRecordingQueue(cameraId=cameraId)
    sendData(redisConnection, {genericdataKeyString: startCommand}, queueName=queueName)

def sendRecordingStop(redisConnection: redis.client.Redis, cameraId: int = 1):
    queueName = CosThetaConfigurator.getInstance().getRecordingQueue(cameraId=cameraId)
    sendData(redisConnection, {genericdataKeyString: stopCommand}, queueName=queueName)

def sendRecordingExit(redisConnection: redis.client.Redis, cameraId: int = 1):
    queueName = CosThetaConfigurator.getInstance().getRecordingQueue(cameraId=cameraId)
    sendData(redisConnection, {genericdataKeyString: exitCommand}, queueName=queueName)

def getRecordingCommand(redisConnection: redis.client.Redis, recordingQueueName: str, block : int = 2):
    if redisConnection is None:
        printBoldRed("Redis connection is None. Cannot receive data.")
        raise Exception("Redis Connection is None")
    last_id = 0
    newData = {}
    try:
        resp = redisConnection.xread({recordingQueueName: last_id}, count=1, block=block)
        if resp:
            key, messages = resp[0]
            last_id, data = messages[0]
            keys = data.keys()
            for key in keys:
                value = data[key]
                newData[key.decode("utf-8")] = value.decode("utf-8")
        else:
            return ""
    except ConnectionError as e1:
        # printBoldRed(f"Error while receiving data : {e1}")
        raise e1
    return newData[genericdataKeyString]

# ********************************** RECORDING COMMANDS END *************************

def getDiscInterlockMessage(redisConnection: redis.client.Redis, block : int = 1):
    # Note this is not in a 'while not done' loop. This ensures that this function call does not block
    # Calling programs will need to implement a loop, if needed. Or, run this as an aync call
    if redisConnection is None:
        printBoldRed("Redis connection is None. Cannot receive data.")
        raise Exception("Redis Connection is None")
    # printBoldRed(f"{getGeneralLoggingMessage(getFullyQualifiedName(__file__), 'getStopCommand() invoked')}")
    last_id = 0
    newData = {}
    timeOfMessage = None
    producer = None
    connAlarmQ = CosThetaConfigurator.getInstance().getConnectionAlarmQueue()
    try:
        resp = redisConnection.xread({connAlarmQ: last_id}, count=1, block=block)
        if resp:
            key, messages = resp[0]
            last_id, data = messages[0]
            keys = data.keys()
            for key in keys:
                value = data[key]
                newData[key.decode("utf-8")] = value
            timeOfMessage = newData[timeKeyString].decode("utf-8")
            producer = newData[producerKeyString].decode("utf-8")
            # xtrim removed, because trimming the queue leads to a delay as well as missing timely updates
            # redisConnection.xtrim(connAlarmQ, 0)
            redisConnection.xdel(connAlarmQ, last_id)
    except ConnectionError as e1:
        # SlaveFileLogger.getInstance().logTakeAction(logSource,
        #                                             f"Could not get stop command due to {e1}",
        #                                             Logger.PROBLEM)
        # SlaveConsoleLogger.getInstance().logTakeAction(logSource,
        #                                             f"Could not get stop command due to {e1}",
        #                                             Logger.PROBLEM)
        # printBoldRed(f"Error while receiving data : {e1}")
        raise e1
    currentStatus = False
    try:
        currentStatus = newData[genericdataKeyString].decode('utf-8')
    except Exception:
        noteSuppressed()
    # printBoldRed(f"{getGeneralLoggingMessage(getFullyQualifiedName(__file__), 'in getStopCommand()')} {stopNow =}")
    if currentStatus is not None:
        if currentStatus == ok:
            # printBoldRed(f"Got stop command in process {os.getpid()}")
            return timeOfMessage, producer, False
    return timeOfMessage, producer, True

# ********************************** COMMUNICATION OUTWARD FROM IO SERVER *************************

def sendDataFromIOServerToFEServer(redisConnection : redis.client.Redis, currentMachineState : MachineState, moveToState : MachineState = MachineState.INVALID_STATE, result : ResultType = ok, action : ActionType = updateValueCommand, value : Union[str, float] = -1., aProducer : str = "IOServer", handshake : bool = False, rotationDone : Optional[bool] = None):
    # rotationDone: with a Station 2 torque value, PLC_PC_Station2RotationDuringTighteningDone (None = not provided)
    data = {currentMachineStateKeyString: currentMachineState, move2StateKeyString: moveToState, actionKeyString: action, valueKeyString: value, resultKeyString : result}
    if rotationDone is not None:
        data[rotationDoneKeyString] = encodeOptionalBool(rotationDone)
    if not handshake:
        return sendData(redisConnection=redisConnection, data=data, queueName=io2feq, aProducer=f"{aProducer}")
    else:
        return sendData(redisConnection=redisConnection, data={genericdataKeyString: HANDSHAKE}, queueName=io2feq, aProducer=f"{aProducer}")

def sendDataFromIOServerToCameraServer(redisConnection : redis.client.Redis, currentMachineState : MachineState, action : ActionType = takePictureCommand, aProducer : str = "IOServer", handshake : bool = False, requestId : Optional[str] = None):
    # requestId: echoed by the camera server in its answers (camera2io, camera2fe)
    data = {currentMachineStateKeyString: currentMachineState, actionKeyString: action}
    if requestId:
        data[requestIdKeyString] = requestId
    if not handshake:
        return sendData(redisConnection=redisConnection, data=data, queueName=io2cameraq, aProducer=f"{aProducer}")
    else:
        return sendData(redisConnection=redisConnection, data={genericdataKeyString: HANDSHAKE}, queueName=io2cameraq, aProducer=f"{aProducer}")

def sendDataFromIOServerToQRCodeServer(redisConnection : redis.client.Redis, currentMachineState : MachineState = MachineState.READ_QR_CODE, action : ActionType = readQRCodeCommand, aProducer : str = "IOServer", handshake : bool = False):
    data = {currentMachineStateKeyString: currentMachineState, actionKeyString: action}
    return sendData(redisConnection=redisConnection, data=data, queueName=io2qrcodeq, aProducer=f"{aProducer}")

def sendEndOfCycleFromIOServerToCameraServer(redisConnection : redis.client.Redis, aborted : bool = False, aProducer : str = "IOServer"):
    """The cycle is over (completed, or aborted: emergency button, heartbeat loss, frontend). The camera server then
    drops what it kept for the cycle (the top bearing profile). Read by readDataInCameraServerFromIOServer(), which
    returns (time, MachineState.READ_QR_CODE, False) for it."""
    data = {currentMachineStateKeyString: MachineState.READ_QR_CODE, actionKeyString: END_OF_CYCLE,
            genericdataKeyString: "aborted" if aborted else "completed"}
    return sendData(redisConnection=redisConnection, data=data, queueName=io2cameraq, aProducer=f"{aProducer}")

def sendAbortFromIOServerToQRCodeServer(redisConnection : redis.client.Redis, currentMachineState : MachineState = MachineState.READ_QR_CODE, action : ActionType = ABORT, aProducer : str = "IOServer", handshake : bool = False):
    # printBoldBlue(f"Inside sendAbortFromIOServerToQRCodeServer()...")
    data = {currentMachineStateKeyString: currentMachineState, actionKeyString: action}
    # printBoldBlue(f"...where about to send data {data}")
    return sendData(redisConnection=redisConnection, data=data, queueName=io2qrcodeabortq, aProducer=f"{aProducer}")

def sendHeartbeatFromIOServerToHeartbeatServer(redisConnection : redis.client.Redis, status : ConnectionStatusType, aProducer : str = "IOServer"):
    return sendData(redisConnection=redisConnection, data={connectionStatusKeyString: status}, queueName=io2hbq, aProducer=f"{aProducer}")

def sendEmergencyAbortFromIOServerToFEServer(redisConnection : redis.client.Redis, status : ResultType = ok, aProducer : str = "IOServer"):
    return sendData(redisConnection=redisConnection, data={emergencyStatusKeyString: status}, queueName=emergencyq, aProducer=f"{aProducer}")

# ********************************** COMMUNICATION INWARD INTO IO SERVER *************************

def readDataInIOServerFromCameraServer(redisConnection: redis.client.Redis, block : int = 500,
                                       withRequestId : bool = False):
    # Note this is not in a 'while not done' loop. This ensures that this function call does not block
    # Calling programs will need to implement a loop, if needed. Or, run this as an aync call
    # Returns (state, result); withRequestId: (state, result, requestId) - requestId None if the answer has none.
    # Messages are read oldest first; only the message read is deleted (a message queued behind it is kept).
    invalid = (MachineState.INVALID_STATE, False, None) if withRequestId else (MachineState.INVALID_STATE, False)
    if redisConnection is None:
        printBoldRed("Redis connection is None in readDataInIOServerFromCameraServer(). Cannot receive data.")
        # raise Exception("Redis Connection is None in readDataInIOServerFromCameraServer()")
        return invalid
    last_id = 0
    newData = {}
    timeOfMessage = None
    try:
        resp = redisConnection.xread({camera2ioq: last_id}, count=1, block=block)
        if resp:
            key, messages = resp[0]
            last_id, data = messages[0]
            keys = data.keys()
            for key in keys:
                value = data[key]
                newData[key.decode("utf-8")] = value.decode("utf-8")
            timeOfMessage = newData[timeKeyString]
            redisConnection.xdel(camera2ioq, last_id)
            # printBoldGreen(f"Got data = {newData}")
    except Exception as e1:
        printBoldRed(f"Got exception - {e1}")
        return invalid
    try:
        if (newData[genericdataKeyString] is not None) and (newData[genericdataKeyString] == HANDSHAKE):
            return invalid
    except Exception:
        noteSuppressed()

    try:
        if (newData[currentMachineStateKeyString] is not None) and (newData[resultKeyString] is not None):
            currentMachineState = MachineState.getMachineStateFromString(newData[currentMachineStateKeyString])
            result = (newData[resultKeyString] == ok)
            # print(f"In readDataInIOServerFromCameraServer(), {currentMachineState = } and {result = }")
            if withRequestId:
                return currentMachineState, result, (newData.get(requestIdKeyString) or None)
            return currentMachineState, result
    except Exception:
        return invalid

    return invalid

def readDataInIOServerFromQRCodeServer(redisConnection: redis.client.Redis, block : int = 500) -> Tuple[bool, str]:
    # Note this is not in a 'while not done' loop. This ensures that this function call does not block
    # Calling programs will need to implement a loop, if needed. Or, run this as an aync call
    # Returns a tuple: (qrCodeIsOK: bool, qrCode: str)
    if redisConnection is None:
        printBoldRed("Redis connection is None in readDataInIOServerFromCameraServer(). Cannot receive data.")
        # raise Exception("Redis Connection is None in readDataInIOServerFromQRCodeServer()")
        return False, ""
    last_id = 0
    newData = {}
    timeOfMessage = None
    try:
        redisConnection.xtrim(qrcode2ioq, 1)
        resp = redisConnection.xread({qrcode2ioq: last_id}, count=1, block=block)
        if resp:
            key, messages = resp[0]
            last_id, data = messages[0]
            keys = data.keys()
            for key in keys:
                value = data[key]
                newData[key.decode("utf-8")] = value.decode("utf-8")
            timeOfMessage = newData[timeKeyString]
            redisConnection.xdel(qrcode2ioq, last_id)
            redisConnection.xtrim(qrcode2ioq, 0)
            # printBoldGreen(f"Got data = {newData}")
    except Exception as e1:
        printBoldRed(f"Got exception - {e1}")
        return False, ""
    try:
        if (newData[genericdataKeyString] is not None) and (newData[genericdataKeyString] == HANDSHAKE):
            return False, ""
    except Exception:
        noteSuppressed()

    try:
        if (newData[qrCodeStatusKeyString] is not None):
            qrCodeIsOK = (newData[qrCodeStatusKeyString] == ok)
            qrCode = newData.get(qrCodeKeyString, "")
            return qrCodeIsOK, qrCode
    except Exception:
        return False, ""

    return False, ""

def readDataInIOServerFromFEServer(redisConnection: redis.client.Redis, block : int = 500):
    # Note this is not in a 'while not done' loop. This ensures that this function call does not block
    # Calling programs will need to implement a loop, if needed. Or, run this as an aync call
    if redisConnection is None:
        printBoldRed("Redis connection is None in readDataInIOServerFromFEServer(). Cannot receive data.")
        # raise Exception("Redis Connection is None in readDataInIOServerFromQRCodeServer()")
        return False
    last_id = 0
    newData = {}
    timeOfMessage = None
    try:
        redisConnection.xtrim(fe2ioq, 1)
        resp = redisConnection.xread({fe2ioq: last_id}, count=1, block=block)
        if resp:
            key, messages = resp[0]
            last_id, data = messages[0]
            keys = data.keys()
            for key in keys:
                value = data[key]
                newData[key.decode("utf-8")] = value.decode("utf-8")
            timeOfMessage = newData[timeKeyString]
            redisConnection.xdel(fe2ioq, last_id)
            redisConnection.xtrim(fe2ioq, 0)
            # printBoldGreen(f"Got data = {newData}")
    except Exception as e1:
        printBoldRed(f"Got exception - {e1}")
        return False
    try:
        if (newData[genericdataKeyString] is not None) and (newData[genericdataKeyString] == HANDSHAKE):
            return False
    except Exception:
        noteSuppressed()

    try:
        if (newData[connectionStatusKeyString] is not None):
            connStatus = (newData[connectionStatusKeyString] == ok)
            return connStatus
    except Exception:
        return False

    return False

def readCombinedHeartbeatInIOServerFromHeartbeatServer(redisConnection: redis.client.Redis, block : int = 500):
    # Note this is not in a 'while not done' loop. This ensures that this function call does not block
    # Calling programs will need to implement a loop, if needed. Or, run this as an aync call
    if redisConnection is None:
        printBoldRed("Redis connection is None in readCombinedHeartbeatInIOServerFromHeartbeatServer(). Cannot receive data.")
        return False
        # raise Exception("Redis Connection is None in readDataInCameraServerFromIOServer()")
    last_id = 0
    newData = {}
    timeOfMessage = None
    try:
        redisConnection.xtrim(hb2ioq, 1)
        resp = redisConnection.xread({hb2ioq: last_id}, count=1, block=block)
        if resp:
            key, messages = resp[0]
            last_id, data = messages[0]
            keys = data.keys()
            for key in keys:
                value = data[key]
                newData[key.decode("utf-8")] = value.decode("utf-8")
            timeOfMessage = newData[timeKeyString]
            redisConnection.xdel(hb2ioq, last_id)
            redisConnection.xtrim(hb2ioq, 0)
            # printBoldGreen(f"Got data = {newData}")
    except Exception as e1:
        printBoldRed(f"Got exception - {e1}")
        return False
    try:
        if (newData[connectionStatusKeyString] is not None) and (newData[connectionStatusKeyString] == ALIVE):
            return True
    except Exception:
        return False
    return False


def readServerStatusesInIOServerFromHeartbeatServer(redisConnection: redis.client.Redis, block : int = 500) -> Optional[dict]:
    """The newest status message from the heartbeat server: {"combined", "camera", "qrcode", "io", "db", "fe": bool}
    (True = alive; a server the message does not name is left out). None if no message arrived within `block` ms
    or it could not be read. Older messages are dropped (only the newest status matters)."""
    if redisConnection is None:
        return None
    try:
        resp = redisConnection.xrevrange(hb2ioq, count=1)
        if not resp:
            resp = redisConnection.xread({hb2ioq: "$"}, count=1, block=block)
            if not resp:
                return None
            _, messages = resp[0]
            lastId, data = messages[-1]
        else:
            lastId, data = resp[0]
        olderAndThis = [m[0] for m in redisConnection.xrange(hb2ioq, max=lastId)]
        if olderAndThis:
            redisConnection.xdel(hb2ioq, *olderAndThis)       # this message and the older ones: done with
        newData = {k.decode("utf-8"): v.decode("utf-8") for k, v in data.items()}
    except Exception as e1:
        printBoldRed(f"Got exception in readServerStatusesInIOServerFromHeartbeatServer - {e1}")
        return None
    if newData.get(genericdataKeyString) == HANDSHAKE:
        return None
    statuses = {"combined": newData.get(connectionStatusKeyString) == ALIVE}
    for name, key in (("camera", cameraServerStatusKeyString), ("qrcode", qrCodeServerStatusKeyString),
                      ("io", ioServerStatusKeyString), ("db", dbServerStatusKeyString),
                      ("fe", feServerStatusKeyString), ("redis", redisServerStatusKeyString)):
        if key in newData:
            statuses[name] = (newData[key] == ALIVE)
    return statuses


REDIS_HEALTH_CHECK_STREAM : str = "redis.healthcheck.q"     # used by nothing else: written and read back to test Redis
redisServerStatusKeyString : str = "redisstatus"              # hb2io / hb2fe: Redis write / read-back check result


def checkRedisHealth(redisConnection: redis.client.Redis) -> Tuple[bool, str]:
    """Write a unique entry to a stream nothing else uses, read it back by its id, compare, delete it.
    (True, "") if Redis accepted the write and returned the same data; else (False, why) - not reachable, writes
    refused (out of memory, persistence error, read-only replica), or different data read back. Catches the cases a
    PING does not: a Redis that answers but no longer stores."""
    if redisConnection is None:
        return False, "no connection"
    token = f"{time.time():.6f}-{os.getpid()}"
    try:
        entryId = redisConnection.xadd(REDIS_HEALTH_CHECK_STREAM, {"token": token}, maxlen=10, approximate=False)
        found = redisConnection.xrange(REDIS_HEALTH_CHECK_STREAM, min=entryId, max=entryId)
        redisConnection.xdel(REDIS_HEALTH_CHECK_STREAM, entryId)
        if not found or found[0][1].get(b"token", b"").decode("utf-8") != token:
            return False, f"wrote {token}, read back {found!r}"
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def setActiveCameraRequest(redisConnection: redis.client.Redis, requestId: Optional[str]) -> None:
    """IOServer: the picture request whose answer the frontend is to show (None: any). Read by
    readDataInFEServerFromCameraServer, which drops results of other requests."""
    try:
        if requestId:
            redisConnection.set(ACTIVE_CAMERA_REQUEST_KEY, requestId, ex=24 * 3600)
        else:
            redisConnection.delete(ACTIVE_CAMERA_REQUEST_KEY)
    except Exception:
        noteSuppressed()


# Frontend notices: short messages the IOServer (or the camera server) shows on the frontend, flashing, top right.
# One Redis key per notice (FRONTEND_NOTICE_PREFIX + name), with an expiry; the frontend shows the ones present, in
# FRONTEND_NOTICE_ORDER. Redis is the inter-process link here, as for every other IOServer -> frontend message.
FRONTEND_NOTICE_ORDER = ("abort", "heartbeat", "db", "camera", "check", "settings")


def setFrontendNotice(redisConnection: redis.client.Redis, name: str, text: str, ttlSecs: float = 3600) -> bool:
    try:
        redisConnection.set(f"{FRONTEND_NOTICE_PREFIX}{name}", text, px=max(1, int(ttlSecs * 1000)))
        return True
    except Exception:
        noteSuppressed()
        return False


def clearFrontendNotice(redisConnection: redis.client.Redis, name: str) -> None:
    try:
        redisConnection.delete(f"{FRONTEND_NOTICE_PREFIX}{name}")
    except Exception:
        noteSuppressed()


def readFrontendNotices(redisConnection: redis.client.Redis) -> Optional[list]:
    """The notices to show, in FRONTEND_NOTICE_ORDER; None if Redis could not be read."""
    try:
        values = redisConnection.mget([f"{FRONTEND_NOTICE_PREFIX}{n}" for n in FRONTEND_NOTICE_ORDER])
    except Exception:
        return None
    return [v.decode("utf-8") for v in values if v]


def publishIOServerState(redisConnection: redis.client.Redis, snapshot: dict) -> bool:
    """IOServer: one state change (snapshot: pid, seq, cycleId, qr, old / new state as int and name, reason, time) on
    IO_STATE_STREAM, and the same snapshot in IO_STATE_KEY. Never raises; False when Redis did not take it."""
    try:
        text = json.dumps(snapshot)
        redisConnection.xadd(IO_STATE_STREAM, {"snapshot": text}, maxlen=IO_STATE_STREAM_MAXLEN, approximate=True)
        redisConnection.set(IO_STATE_KEY, text, ex=24 * 3600)
        return True
    except Exception:
        noteSuppressed()
        return False


def readIOServerStates(redisConnection: redis.client.Redis, maxCount: int = 200) -> Optional[list]:
    """Frontend: the IOServer state snapshots waiting on IO_STATE_STREAM, oldest first, deleted once read (one
    consumer). None if Redis could not be read; an entry that cannot be decoded is skipped."""
    try:
        entries = redisConnection.xrange(IO_STATE_STREAM, "-", "+", count=maxCount)
        if not entries:
            return []
        redisConnection.xdel(IO_STATE_STREAM, *[entryId for entryId, _ in entries])
    except Exception:
        return None
    snapshots = []
    for _, fields in entries:
        try:
            raw = fields.get(b"snapshot", fields.get("snapshot"))
            snapshots.append(json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw))
        except Exception:
            noteSuppressed()
    return snapshots


def readIOServerStateSnapshot(redisConnection: redis.client.Redis) -> Optional[dict]:
    """The IOServer's latest state snapshot (IO_STATE_KEY), or None (none yet, or Redis not readable)."""
    try:
        raw = redisConnection.get(IO_STATE_KEY)
        return json.loads(raw.decode("utf-8")) if raw else None
    except Exception:
        return None


# ********************************** COMMUNICATION OUTWARD FROM FE SERVER *************************

def sendDataFromFEServerToDatabaseServer(redisConnection : redis.client.Redis,
                                         qrCode : str,
                                         knucklePicture : Union[np.ndarray, None], knuckleResult : ResultType, knuckleDatetime : str,
                                         hubAndBottomBearingPicture : Union[np.ndarray, None], hubAndBottomBearingResult : ResultType, hubAndBottomBearingDatetime : str,
                                         topBearingPicture : Union[np.ndarray, None], topBearingResult : ResultType, topBearingDatetime : str,
                                         nutAndPlateWasherPicture : Union[np.ndarray, None], nutAndPlateWasherResult : ResultType, nutAndPlateWasherDatetime : str,
                                         tighteningTorque1 : Union[str, float], tighteningTorque1Result : ResultType, tighteningTorque1Datetime : str,
                                         componentPressBunkCheckingPicture: Union[np.ndarray, None], componentPressBunkCheckingResult : ResultType, componentPressBunkCheckDatetime : str,
                                         componentPressDone :  ResultType, componentPressDoneDatetime : str,
                                         noBunkCheckingPicture: Union[np.ndarray, None], noBunkCheckingResult : ResultType, noBunkCheckDatetime : str,
                                         tighteningTorque2 : Union[str, float], tighteningTorque2Result : ResultType, tighteningTorque2Datetime : str,
                                         splitPinAndWasherPicture: Union[np.ndarray, None], splitPinAndWasherResult : ResultType, splitPinAndWasherDatetime : str,
                                         capCheckingPicture: Union[np.ndarray, None], capCheckingResult : ResultType, capCheckingDatetime : str,
                                         bunkCheckingPicture: Union[np.ndarray, None], capPressBunkCheckingResult : ResultType, capPressBunkCheckDatetime : str,
                                         pressDone: ResultType, capPressDoneDatetime : str,
                                         freeRotationTorque1: Union[str, float], freeRotationTorque1Result: ResultType, freeRotationTorque1Datetime : str,
                                         overallResult : ResultType,
                                         aProducer : str = "FEServer",
                                         tighteningTorque1RotationDone : Optional[bool] = None,
                                         tighteningTorque2RotationDone : Optional[bool] = None,
                                         aborted : bool = False, abortReason : str = "",
                                         bypassedPressAndTightening2 : bool = False):

    # print("Reached here - 1")
    if (qrCode is None) or (qrCode == ""):
        return

    knucklePictureAsMessage = bytes(EMPTY_IMAGE_STRING, "utf-8")
    try:
        if validate_image(knucklePicture):
            retval, buffer = cv2.imencode('.png', knucklePicture)
            if buffer is not None:
                knucklePictureAsMessage = np.array(buffer).tobytes()
    except Exception as e:
        printBoldRed(f"Problem in getting knuckle picture - {e}")
        pass

    # print("Reached here - 2")

    hubAndBottomBearingPictureAsMessage = bytes(EMPTY_IMAGE_STRING, "utf-8")
    try:
        if validate_image(hubAndBottomBearingPicture):
            retval, buffer = cv2.imencode('.png', hubAndBottomBearingPicture)
            if buffer is not None:
                hubAndBottomBearingPictureAsMessage = np.array(buffer).tobytes()
    except Exception as e:
        printBoldRed(f"Problem in getting hub and first bearing picture - {e}")
        pass

    # print("Reached here - 3")

    topBearingPictureAsMessage = bytes(EMPTY_IMAGE_STRING, "utf-8")
    try:
        if validate_image(topBearingPicture):
            retval, buffer = cv2.imencode('.png', topBearingPicture)
            if buffer is not None:
                topBearingPictureAsMessage = np.array(buffer).tobytes()
    except Exception as e:
        printBoldRed(f"Problem in getting second bearing picture - {e}")
        pass

    # print("Reached here - 4")

    nutAndPlateWasherPictureAsMessage = bytes(EMPTY_IMAGE_STRING, "utf-8")
    try:
        if validate_image(nutAndPlateWasherPicture):
            retval, buffer = cv2.imencode('.png', nutAndPlateWasherPicture)
            if buffer is not None:
                nutAndPlateWasherPictureAsMessage = np.array(buffer).tobytes()
    except Exception as e:
        printBoldRed(f"Problem in getting nut and plate washer picture - {e}")
        pass

    # print("Reached here - 5")

    componentPressBunkCheckingPictureAsMessage = bytes(EMPTY_IMAGE_STRING, "utf-8")
    try:
        if validate_image(componentPressBunkCheckingPicture):
            retval, buffer = cv2.imencode('.png', componentPressBunkCheckingPicture)
            if buffer is not None:
                componentPressBunkCheckingPictureAsMessage = np.array(buffer).tobytes()
    except Exception as e:
        printBoldRed(f"Problem in getting bunk check picture before component press - {e}")
        pass

    noBunkCheckingPictureAsMessage = bytes(EMPTY_IMAGE_STRING, "utf-8")
    try:
        if validate_image(noBunkCheckingPicture):
            retval, buffer = cv2.imencode('.png', noBunkCheckingPicture)
            if buffer is not None:
                noBunkCheckingPictureAsMessage = np.array(buffer).tobytes()
    except Exception as e:
        printBoldRed(f"Problem in getting no bunk check picture after component press - {e}")
        pass

    splitPinAndWasherPictureAsMessage = bytes(EMPTY_IMAGE_STRING, "utf-8")
    try:
        if validate_image(splitPinAndWasherPicture):
            retval, buffer = cv2.imencode('.png', splitPinAndWasherPicture)
            if buffer is not None:
                splitPinAndWasherPictureAsMessage = np.array(buffer).tobytes()
    except Exception as e:
        printBoldRed(f"Problem in getting split pin and washer picture - {e}")
        pass

    # print("Reached here - 6")

    capCheckingPictureAsMessage = bytes(EMPTY_IMAGE_STRING, "utf-8")
    try:
        if validate_image(capCheckingPicture):
            retval, buffer = cv2.imencode('.png', capCheckingPicture)
            if buffer is not None:
                capCheckingPictureAsMessage = np.array(buffer).tobytes()
    except Exception as e:
        printBoldRed(f"Problem in getting cap check picture - {e}")
        pass

    # print("Reached here - 7")

    capPressBunkCheckingPictureAsMessage = bytes(EMPTY_IMAGE_STRING, "utf-8")
    try:
        if validate_image(bunkCheckingPicture):
            retval, buffer = cv2.imencode('.png', bunkCheckingPicture)
            if buffer is not None:
                capPressBunkCheckingPictureAsMessage = np.array(buffer).tobytes()
    except Exception as e:
        printBoldRed(f"Problem in getting bunk check picture before cap press - {e}")
        pass

    # print("Reached here - 8")

    data = {
        qrCodeKeyString : qrCode,
        knucklePictureKeyString : knucklePictureAsMessage, knuckleCheckResultKeyString : knuckleResult,
        knuckleDatetimeKeyString: knuckleDatetime,
        hubAndBottomBearingPictureKeyString : hubAndBottomBearingPictureAsMessage, hubAndBottomBearingCheckResultKeyString : hubAndBottomBearingResult,
        hubAndBottomBearingDatetimeKeyString : hubAndBottomBearingDatetime,
        topBearingPictureKeyString : topBearingPictureAsMessage, topBearingCheckResultKeyString : topBearingResult,
        topBearingDatetimeKeyString : topBearingDatetime,
        nutAndPlateWasherPictureKeyString : nutAndPlateWasherPictureAsMessage, nutAndPlateWasherCheckResultKeyString : nutAndPlateWasherResult,
        nutAndPlateWasherDatetimeKeyString : nutAndPlateWasherDatetime,
        tighteningTorque1ValueKeyString : str(tighteningTorque1), tighteningTorque1ResultKeyString : tighteningTorque1Result,
        tighteningTorque1DatetimeKeyString : tighteningTorque1Datetime,
        componentPressBunkPictureKeyString: componentPressBunkCheckingPictureAsMessage, componentPressBunkCheckResultKeyString: componentPressBunkCheckingResult,
        componentPressBunkCheckDatetimeKeyString : componentPressBunkCheckDatetime,
        componentPressDoneResultKeyString : componentPressDone,
        componentPressDoneDatetimeKeyString : componentPressDoneDatetime,
        noBunkPictureKeyString : noBunkCheckingPictureAsMessage, noBunkCheckResultKeyString : noBunkCheckingResult,
        noBunkCheckDatetimeKeyString : noBunkCheckDatetime,
        tighteningTorque2ValueKeyString : str(tighteningTorque2), tighteningTorque2ResultKeyString : tighteningTorque2Result,
        tighteningTorque2DatetimeKeyString: tighteningTorque2Datetime,
        splitPinAndWasherPictureKeyString : splitPinAndWasherPictureAsMessage, splitPinAndWasherCheckResultKeyString : splitPinAndWasherResult,
        splitPinAndWasherCheckDatetimeKeyString: splitPinAndWasherDatetime,
        capPictureKeyString : capCheckingPictureAsMessage, capCheckResultKeyString : capCheckingResult,
        capCheckDatetimeKeyString: capCheckingDatetime,
        capPressBunkPictureKeyString : capPressBunkCheckingPictureAsMessage, capPressBunkCheckResultKeyString : capPressBunkCheckingResult,
        capPressBunkCheckDatetimeKeyString: capPressBunkCheckDatetime,
        capPressDoneResultKeyString : pressDone,
        capPressDoneDatetimeKeyString: capPressDoneDatetime,
        freeRotationTorque1ValueKeyString: str(freeRotationTorque1), freeRotationTorque1ResultKeyString: freeRotationTorque1Result,
        freeRotationTorque1DatetimeKeyString: freeRotationTorque1Datetime,
        overallResultKeyString : overallResult,
        tighteningTorque1RotationDoneKeyString : encodeOptionalBool(tighteningTorque1RotationDone),
        tighteningTorque2RotationDoneKeyString : encodeOptionalBool(tighteningTorque2RotationDone),
        abortedKeyString : "1" if aborted else "0",
        abortReasonKeyString : str(abortReason or "") if aborted else "",
        bypassedKeyString : "1" if bypassedPressAndTightening2 else "0",
    }
    # print(f"size of data is {sys.getsizeof(data)}")
    # print(f"{data = }")
    return sendData(redisConnection=redisConnection, data=data, queueName=fe2dbq, aProducer=f"{aProducer}")

def sendAbortFromFEServerToIOServer(redisConnection : redis.client.Redis, reason : str = "manual abort in the frontend",
                                    aProducer : str = "FEServer"):
    """Frontend -> IOServer: abort the current cycle (the IOServer resets its PLC handshake tags and goes back to
    READ_QR_CODE). Used by the frontend's manual abort, and by test tools that play the frontend."""
    return sendData(redisConnection=redisConnection, data={actionKeyString: ABORT, genericdataKeyString: reason},
                    queueName=fe2ioq, aProducer=f"{aProducer}")

FRONTEND_ABORT_REQUEST : str = "FrontendAbortRequest"   # fe2io.q action: the frontend's password-protected abort


def sendFrontendAbortRequestFromFEServerToIOServer(redisConnection : redis.client.Redis,
                                                   reason : str = "manual abort (Ctrl+Alt+Z) in the frontend",
                                                   aProducer : str = "FEServer"):
    """Frontend -> IOServer, the frontend's own (password-protected) abort (2026-11-08): the IOServer tries to set
    PC_PLC_CycleAborted True on the PLC (3 attempts); only if that succeeds does it abort its cycle and reset the PLC
    handshake. Without the tag's address, or if the write fails, it logs and does nothing else (the abort stays in the
    frontend)."""
    return sendData(redisConnection=redisConnection,
                    data={actionKeyString: FRONTEND_ABORT_REQUEST, genericdataKeyString: reason},
                    queueName=fe2ioq, aProducer=f"{aProducer}")


def readAbortInIOServerFromFEServer(redisConnection: redis.client.Redis, block : int = 500, withKind : bool = False):
    """IOServer: (True, reason) if the frontend has asked for the cycle to be aborted, else (False, "").
    withKind=True: (kind, reason), kind "abort" (ABORT: abort at once, as test tools send), "request"
    (FRONTEND_ABORT_REQUEST: the frontend's own abort, see sendFrontendAbortRequestFromFEServerToIOServer) or None.
    Reads and removes EVERY message waiting in the frontend -> IOServer queue (an abort must never be lost behind
    another message); waits up to `block` ms when the queue is empty."""
    nothing = (None, "") if withKind else (False, "")
    if redisConnection is None:
        return nothing
    try:
        resp = redisConnection.xread({fe2ioq: 0}, count=100, block=block)
        if not resp:
            return nothing
        _, messages = resp[0]
        kind, reason = None, ""
        for entryId, data in messages:
            decoded = {k.decode("utf-8"): v.decode("utf-8", "replace") for k, v in data.items()}
            action = decoded.get(actionKeyString)
            if action == ABORT:
                kind, reason = "abort", decoded.get(genericdataKeyString, "")
            elif action == FRONTEND_ABORT_REQUEST and kind != "abort":
                kind, reason = "request", decoded.get(genericdataKeyString, "")
            redisConnection.xdel(fe2ioq, entryId)
        if withKind:
            return kind, reason
        return kind == "abort", (reason if kind == "abort" else "")
    except Exception as e:
        printBoldRed(f"readAbortInIOServerFromFEServer(): {e}")
        return nothing

def sendDataFromFEServerToIOServer(redisConnection : redis.client.Redis, connectionStatus : ResultType, aProducer : str = "FEServer", handshake : bool = False):
    if not handshake:
        return sendData(redisConnection=redisConnection, data={connectionStatusKeyString : connectionStatus}, queueName=fe2ioq, aProducer=f"{aProducer}")
    else:
        return sendData(redisConnection=redisConnection, data={genericdataKeyString: HANDSHAKE}, queueName=fe2ioq, aProducer=f"{aProducer}")

def sendHeartbeatFromFEServerToHeartbeatServer(redisConnection : redis.client.Redis, status : ConnectionStatusType = ok, aProducer : str = "FEServer"):
    return sendData(redisConnection=redisConnection, data={connectionStatusKeyString: status}, queueName=fe2hbq, aProducer=f"{aProducer}")

# ********************************** COMMUNICATION INWARD INTO FE SERVER *************************

# returns 3 things - actual qrcode, qrcode display string, qrcode status
def readDataInFEServerFromQRCodeServer(redisConnection: redis.client.Redis, block : int = 500):
    # Note this is not in a 'while not done' loop. This ensures that this function call does not block
    # Calling programs will need to implement a loop, if needed. Or, run this as an aync call
    if redisConnection is None:
        printBoldRed("Redis connection is None in readDataInFEServerFromQRCodeServer(). Cannot receive data.")
        # raise Exception("Redis Connection is None in readDataInFEServerFromQRCodeServer()")
        return None, None, False
    last_id = 0
    newData = {}
    timeOfMessage = None
    try:
        redisConnection.xtrim(qrcode2feq, 1)
        resp = redisConnection.xread({qrcode2feq: last_id}, count=1, block=block)
        if resp:
            key, messages = resp[0]
            last_id, data = messages[0]
            keys = data.keys()
            for key in keys:
                value = data[key]
                newData[key.decode("utf-8")] = value.decode("utf-8")
            timeOfMessage = newData[timeKeyString]
            redisConnection.xdel(qrcode2feq, last_id)
            redisConnection.xtrim(qrcode2feq, 0)
            # printBoldGreen(f"Got data = {newData}")
    except Exception as e1:
        printBoldRed(f"Got exception - {e1}")
        return None, None, False
    try:
        if (newData[genericdataKeyString] is not None) and (newData[genericdataKeyString] == HANDSHAKE):
            return None, None, False
    except Exception:
        noteSuppressed()

    try:
        if (newData[qrCodeKeyString] is not None) and (newData[qrCodeDisplayKeyString] is not None):
            validQRCode = (newData[qrCodeStatusKeyString] == ok)
            actualQRCodeValue = newData[qrCodeKeyString]
            qrCodeDisplayString = newData[qrCodeDisplayKeyString]
            return actualQRCodeValue, qrCodeDisplayString, validQRCode
    except Exception:
        return None, None, False

    return None, None, False

# returns 5 things - timeOfMessage, original image as an ndarray, processed image as an ndarray,
#                    the result of the evaluation (decodedResult) as an ok or notok, and the current machine state for which the evaluation was done
# IF decodedResult IS None, it is interpreted as if there is no message in the queue and therefore, the result should be ignored

def readDataInFEServerFromCameraServer(redisConnection: redis.client.Redis, block : int = 500,
                                       filterStale : bool = True):
    # filterStale: a result carrying a request id other than the active one (ACTIVE_CAMERA_REQUEST_KEY, set by the
    # IOServer) is deleted and not returned - it answers a request the IOServer stopped waiting for
    # Note this is not in a 'while not done' loop. This ensures that this function call does not block
    # Calling programs will need to implement a loop, if needed. Or, run this as an aync call

    decodedOriginalImage = None
    decodedProcessedImage = None
    decodedResult = None
    timeOfMessage = None
    aProducer = None
    currentMachineState = MachineState.INVALID_STATE

    if redisConnection is None:
        printBoldRed("Redis connection is None in readDataInFEServerFromCameraServer(). Cannot receive data.")
        # raise Exception("Redis Connection is None")
        return timeOfMessage, decodedOriginalImage, decodedProcessedImage, decodedResult, currentMachineState

    last_id = 0
    try:
        resp = redisConnection.xread(
            {camera2feq: last_id}, count=1, block=block
        )
        if resp:
            key, messages = resp[0]
            last_id, data = messages[0]
            keys = data.keys()
            newData = {}
            for key in keys:
                value = data[key]
                # NOTE : Since this message carries image bytes, DO NOT apply .decode('utf-8') to all the data
                newData[key.decode("utf-8")] = value

            requestId = (newData.get(requestIdKeyString) or b"").decode('utf-8') or None
            if requestId is not None and filterStale:
                active = redisConnection.get(ACTIVE_CAMERA_REQUEST_KEY)
                active = active.decode('utf-8') if active is not None else None
                if active is not None and active != requestId:
                    # an answer to a picture request the IOServer no longer waits for (it timed out): not shown
                    redisConnection.xdel(camera2feq, last_id)
                    printBoldRed(f"Camera result for request {requestId} ignored (the active request is {active})")
                    return None, None, None, None, MachineState.INVALID_STATE
            decodedResult = newData[resultKeyString].decode('utf-8')
            timeOfMessage = newData[timeKeyString].decode('utf-8')
            currentMachineState = newData[currentMachineStateKeyString].decode('utf-8')
            currentMachineState = MachineState.getMachineStateFromString(currentMachineState)
            aProducer = newData[producerKeyString].decode('utf-8')
            decodedOriginalImage = blankWhiteImage
            decodedProcessedImage = blankWhiteImage
            if decodedResult != HANDSHAKE:
                try:
                    if newData[originalImageKeyString] is not None:
                        decodedOriginalImage = cv2.imdecode(np.frombuffer(newData[originalImageKeyString], np.uint8), 1)
                except Exception:
                    printBoldRed("Facing problem in decoding original image from byte stream in readDataInFEServerFromCameraServer()")
                    pass
                try:
                    if newData[processedImageKeyString] is not None:
                        decodedProcessedImage = cv2.imdecode(np.frombuffer(newData[processedImageKeyString], np.uint8), 1)
                except Exception:
                    printBoldRed(
                        "Facing problem in decoding processed image from byte stream in readDataInFEServerFromCameraServer()")
                    pass
            redisConnection.xdel(camera2feq, last_id)
            # printBoldBlue(f"{decodedResult = }; {timeOfMessage = }; {aProducer = }; {decodedImage.shape}")
            # if isinstance(decodedImage, np.ndarray):
            #     printBoldBlue(f'Decoded startingImage has shape {decodedImage.shape} and type {decodedImage.dtype}')
    except Exception as e1:
        if last_id != 0:            # a malformed message must not block the queue: drop it
            try:
                redisConnection.xdel(camera2feq, last_id)
            except Exception:
                noteSuppressed()
        printBoldRed(f"Could not read a camera result in readDataInFEServerFromCameraServer(): {e1}")
        return timeOfMessage, None, None, None, MachineState.INVALID_STATE
    if decodedResult == HANDSHAKE:
        return timeOfMessage, None, None, None, MachineState.INVALID_STATE
    return timeOfMessage, decodedOriginalImage, decodedProcessedImage, decodedResult, currentMachineState

# returns 4 things - cameraServerStatus, qrCodeServerStatus, ioServerStatus, dbServerStatus as booleans, in that order
lastReportedRedisStatus : dict = {"alive": True}     # the Redis check result last received by this process (frontend)


def readHeartbeatsInFEServerFromHeartbeatServer(redisConnection: redis.client.Redis, block : int = 500,
                                                withReceived : bool = False):
    # Note this is not in a 'while not done' loop. This ensures that this function call does not block
    # Calling programs will need to implement a loop, if needed. Or, run this as an aync call
    # block=None: do not wait at all (for calls made in the GUI thread).
    # withReceived=True: a 5th value, True if a status message was read (False = nothing new; the 4 statuses are then
    # False only because nothing was received, not because a server is down).
    if redisConnection is None:
        printBoldRed("Redis connection is None in readDataInFEServerFromHeartbeatServer(). Cannot receive data.")
        # raise Exception("Redis Connection is None")
        return (False, False, False, False, False) if withReceived else (False, False, False, False)
    last_id = 0
    cameraServerStatus = False
    qrCodeServerStatus = False
    ioServerStatus = False
    dbServerStatus = False
    newData = {}

    try:
        redisConnection.xtrim(hb2feq, 1)
        resp = redisConnection.xread({hb2feq: last_id}, count=1, block=block)
        if resp:
            key, messages = resp[0]
            last_id, data = messages[0]
            keys = data.keys()
            for key in keys:
                value = data[key]
                newData[key.decode("utf-8")] = value.decode('utf-8')
            timeOfMessage = newData[timeKeyString]
            redisConnection.xdel(hb2feq, last_id)
            redisConnection.xtrim(hb2feq, 0)
    except ConnectionError:
        newData = {}

    received = cameraServerStatusKeyString in newData
    if redisServerStatusKeyString in newData:            # Redis write / read-back check (heartbeat server, every 60 s)
        lastReportedRedisStatus["alive"] = (newData[redisServerStatusKeyString] == ALIVE)
    try:
        cameraServerStatus = (newData[cameraServerStatusKeyString] == ALIVE)
        qrCodeServerStatus = (newData[qrCodeServerStatusKeyString] == ALIVE)
        ioServerStatus = (newData[ioServerStatusKeyString] == ALIVE)
        dbServerStatus = (newData[dbServerStatusKeyString] == ALIVE)
    except Exception:
        noteSuppressed()
    statuses = (cameraServerStatus, qrCodeServerStatus, ioServerStatus, dbServerStatus)
    return statuses + (received,) if withReceived else statuses


def _withRotation(withRotationDone: bool, result: tuple, rotationDone):
    return result + (rotationDone,) if withRotationDone else result


def readDataInFEServerFromIOServer(redisConnection: redis.client.Redis, block : int = 500, withRotationDone : bool = False):
    # returns 5 things - currentMachineState, moveToState, action, value, result
    # withRotationDone=True: a 6th, rotationDone (True / False, or None if the message does not carry it)

    # If 'moveToState' != -1, it implies that this message is ONLY for changing the currentMachineState of the machine to the new state.
    # Ignore the other values.

    # If 'moveToState' == -1, and action == updateValueCommand, it implies that this message is to update the result of the outcome
    # container for the current state with 'value'

    # Note this is not in a 'while not done' loop. This ensures that this function call does not block
    # Calling programs will need to implement a loop, if needed. Or, run this as an aync call
    if redisConnection is None:
        printBoldRed("Redis connection is None in readDataInFEServerFromIOServer(). Cannot receive data.")
        # raise Exception("Redis Connection is None")
        return _withRotation(withRotationDone, (MachineState.INVALID_STATE, MachineState.INVALID_STATE, None, None, notok), None)

    last_id = 0
    newData = {}
    timeOfMessage = None
    try:
        redisConnection.xtrim(io2feq, 1)
        resp = redisConnection.xread({io2feq: last_id}, count=1, block=block)
        if resp:
            key, messages = resp[0]
            last_id, data = messages[0]
            keys = data.keys()
            for key in keys:
                value = data[key]
                newData[key.decode("utf-8")] = value.decode("utf-8")
            timeOfMessage = newData[timeKeyString]
            redisConnection.xdel(io2feq, last_id)
            redisConnection.xtrim(io2feq, 0)
            # printBoldGreen(f"Got data = {newData}")
    except Exception as e1:
        printBoldRed(f"Got exception - {e1}")
        return _withRotation(withRotationDone, (MachineState.INVALID_STATE, MachineState.INVALID_STATE, None, None, notok), None)

    try:
        if (newData[genericdataKeyString] is not None) and (newData[genericdataKeyString] == HANDSHAKE):
            return _withRotation(withRotationDone, (MachineState.INVALID_STATE, MachineState.INVALID_STATE, None, None, notok), None)
    except Exception:
        noteSuppressed()

    try:
        if (newData[currentMachineStateKeyString] is not None):
            currentMachineState = MachineState.getMachineStateFromString(newData[currentMachineStateKeyString])
            moveToState = MachineState.getMachineStateFromString(newData[move2StateKeyString])
            action = newData[actionKeyString]
            value = newData[valueKeyString]
            result = newData[resultKeyString]
            try:
                value = float(value)
            except Exception:
                noteSuppressed()

            return _withRotation(withRotationDone, (currentMachineState, moveToState, action, value, result),
                                 decodeOptionalBool(newData.get(rotationDoneKeyString)))
    except Exception:
        return _withRotation(withRotationDone, (MachineState.INVALID_STATE, MachineState.INVALID_STATE, None, None, notok), None)

    return _withRotation(withRotationDone, (MachineState.INVALID_STATE, MachineState.INVALID_STATE, None, None, notok), None)

# returns a boolean value indicating that emergency button has been pressed
def readEmergencyAbortInFEServerFromIOServer(redisConnection: redis.client.Redis, block : int = 500):
    # Note this is not in a 'while not done' loop. This ensures that this function call does not block
    # Calling programs will need to implement a loop, if needed. Or, run this as an aync call
    if redisConnection is None:
        printBoldRed("Redis connection is None in readEmergencyAbortInCameraServerFromIOServer(). Cannot receive data.")
        # raise Exception("Redis Connection is None in readDataInCameraServerFromIOServer()")
        return False
    last_id = 0
    newData = {}
    timeOfMessage = None

    try:
        redisConnection.xtrim(emergencyq, 1)
        resp = redisConnection.xread({emergencyq: last_id}, count=1, block=block)
        if resp:
            key, messages = resp[0]
            last_id, data = messages[0]
            keys = data.keys()
            for key in keys:
                value = data[key]
                newData[key.decode("utf-8")] = value.decode("utf-8")
            timeOfMessage = newData[timeKeyString]
            try:
                redisConnection.xdel(emergencyq, last_id)
                redisConnection.xtrim(emergencyq, 0)
            except Exception:
                noteSuppressed()
            # printBoldGreen(f"Got data = {newData}")
    except Exception as e1:
        printBoldRed(f"Got exception - {e1}")
        return False
    try:
        if (newData[genericdataKeyString] is not None) and (newData[genericdataKeyString] == HANDSHAKE):
            return False
    except Exception:
        noteSuppressed()

    try:
        if (newData[emergencyStatusKeyString] is not None) and (newData[emergencyStatusKeyString] == ok):
            return True
    except Exception:
        return False

    return False

# ********************************** COMMUNICATION OUTWARD FROM QRCODE SERVER *************************

def sendDataFromQRCodeServerToFEServer(redisConnection : redis.client.Redis, qrCode : str, displayString : str, qrCodeStatus : ResultType = ok, aProducer : str = "QRCodeServer", handshake : bool = False):
    data = {qrCodeKeyString: qrCode, qrCodeDisplayKeyString : displayString, qrCodeStatusKeyString : qrCodeStatus}
    if not handshake:
        return sendData(redisConnection=redisConnection, data=data, queueName=qrcode2feq, aProducer=f"{aProducer}")
    else:
        return sendData(redisConnection=redisConnection, data={genericdataKeyString: HANDSHAKE}, queueName=qrcode2feq, aProducer=f"{aProducer}")

def sendDataFromQRCodeServerToIOServer(redisConnection : redis.client.Redis, qrCode : str = "", qrCodeStatus : ResultType = ok, aProducer : str = "QRCodeServer", handshake : bool = False):
    data = {qrCodeStatusKeyString: qrCodeStatus, qrCodeKeyString: qrCode}
    if not handshake:
        return sendData(redisConnection=redisConnection, data=data, queueName=qrcode2ioq, aProducer=f"{aProducer}")
    else:
        return sendData(redisConnection=redisConnection, data={genericdataKeyString: HANDSHAKE}, queueName=qrcode2ioq, aProducer=f"{aProducer}")

def sendComponentQRCodeFromQRCodeServerToCameraServer(redisConnection : redis.client.Redis, componentQRCode : str, aProducer : str = "QRCodeServer"):
    rValue =  sendData(redisConnection=redisConnection, data={componentQRCodeKeyString: componentQRCode}, queueName=qrcode2cameraq, aProducer=f"{aProducer}")
    return rValue

def sendHeartbeatFromQRCodeServerToHeartbeatServer(redisConnection : redis.client.Redis, status : ConnectionStatusType, aProducer : str = "QRCodeServer"):
    return sendData(redisConnection=redisConnection, data={connectionStatusKeyString: status}, queueName=qrcode2hbq, aProducer=f"{aProducer}")

# ********************************** COMMUNICATION INWARD INTO QRCODE SERVER *************************

# returns 2 values - timeOfMessage in milliseconds and a boolean whether the request is to read a newwidgets QR Code
def readDataInQRCodeServerFromIOServer(redisConnection: redis.client.Redis, block : int = 500):
    # Note this is not in a 'while not done' loop. This ensures that this function call does not block
    # Calling programs will need to implement a loop, if needed. Or, run this as an aync call
    if redisConnection is None:
        printBoldRed("Redis connection is None in readDataInQRCodeServerFromIOServer(). Cannot receive data.")
        # raise Exception("Redis Connection is None in readDataInQRCodeServerFromIOServer()")
        return None, False
    last_id = 0
    newData = {}
    timeOfMessage = None
    try:
        redisConnection.xtrim(io2qrcodeq, 1)
        resp = redisConnection.xread({io2qrcodeq: last_id}, count=1, block=block)
        if resp:
            key, messages = resp[0]
            last_id, data = messages[0]
            keys = data.keys()
            for key in keys:
                value = data[key]
                newData[key.decode("utf-8")] = value.decode("utf-8")
            timeOfMessage = newData[timeKeyString]
            redisConnection.xdel(io2qrcodeq, last_id)
            redisConnection.xtrim(io2qrcodeq, 0)
            # printBoldGreen(f"Got data = {newData}")
    except Exception as e1:
        printBoldRed(f"Got exception - {e1}")
        return None, False

    try:
        if (newData[genericdataKeyString] is not None) and (newData[genericdataKeyString] == HANDSHAKE):
            return None, False
    except Exception:
        noteSuppressed()

    try:
        if (newData[actionKeyString] is not None) and (newData[actionKeyString] == readQRCodeCommand):
            return timeOfMessage, True
    except Exception:
        return None, False

    return None, False


# returns 1 values - a boolean, whether to abort a read or not
def readAbortDataInQRCodeServerFromIOServer(redisConnection: redis.client.Redis, block : int = 500):
    # Note this is not in a 'while not done' loop. This ensures that this function call does not block
    # Calling programs will need to implement a loop, if needed. Or, run this as an aync call
    if redisConnection is None:
        printBoldRed("Redis connection is None in readAbortDataInQRCodeServerFromIOServer(). Cannot receive data.")
        # raise Exception("Redis Connection is None in readDataInQRCodeServerFromIOServer()")
        return False
    last_id = 0
    newData = {}
    timeOfMessage = None
    try:
        redisConnection.xtrim(io2qrcodeabortq, 1)
        resp = redisConnection.xread({io2qrcodeabortq: last_id}, count=1, block=block)
        if resp:
            key, messages = resp[0]
            last_id, data = messages[0]
            keys = data.keys()
            for key in keys:
                value = data[key]
                newData[key.decode("utf-8")] = value.decode("utf-8")
            timeOfMessage = newData[timeKeyString]
            try:
                redisConnection.xdel(io2qrcodeabortq, last_id)
                redisConnection.xtrim(io2qrcodeabortq, 0)
            except Exception:
                noteSuppressed()
            # printBoldGreen(f"Got data = {newData}")
    except Exception as e1:
        printBoldRed(f"Got exception - {e1}")
        return False

    try:
        if (newData[genericdataKeyString] is not None) and (newData[genericdataKeyString] == HANDSHAKE):
            return False
    except Exception:
        noteSuppressed()

    try:
        if (newData[actionKeyString] is not None) and (newData[actionKeyString] == ABORT):
            return True
    except Exception:
        noteSuppressed()

    return False

# ********************************** COMMUNICATION OUTWARD FROM CAMERA SERVER *************************

def sendDataFromCameraServerToFEServer(redisConnection : redis.client.Redis, originalImage : Union[np.ndarray, None], processedImage : Union[np.ndarray, None], result : ResultType, currentMachineState : MachineState, aProducer : str = "CameraServer", handshake : bool = False, requestId : Optional[str] = None, reason : Optional[str] = None):
    # requestId: the IOServer's picture request this answers (the frontend shows it only while it is the active request)
    # reason: why the result is NOT OK when it is not the check's own verdict (camera down, check timed out, ...)
    originalImageAsMessage = ""
    try:
        if validate_image(originalImage):
            retval, buffer = cv2.imencode('.png', originalImage)
            if buffer is not None:
                originalImageAsMessage = np.array(buffer).tobytes()
    except Exception:
        noteSuppressed()

    processedImageAsMessage = ""
    try:
        if validate_image(processedImage):
            retval, buffer = cv2.imencode('.png', processedImage)
            if buffer is not None:
                processedImageAsMessage = np.array(buffer).tobytes()
    except Exception:
        noteSuppressed()

    data = {originalImageKeyString: originalImageAsMessage, processedImageKeyString: processedImageAsMessage, resultKeyString : result, currentMachineStateKeyString : currentMachineState}
    if requestId:
        data[requestIdKeyString] = requestId
    if reason:
        data[reasonKeyString] = reason
    if not handshake:
        return sendData(redisConnection=redisConnection, data=data, queueName=camera2feq, aProducer=f"{aProducer}")
    else:
        return sendData(redisConnection=redisConnection, data={genericdataKeyString: HANDSHAKE}, queueName=camera2feq, aProducer=f"{aProducer}")

def sendDataFromCameraServerToIOServer(redisConnection : redis.client.Redis, result : ResultType, currentMachineState : MachineState, aProducer : str = "CameraServer", handshake : bool = False, requestId : Optional[str] = None):
    data = {resultKeyString: result, currentMachineStateKeyString : currentMachineState}
    if requestId:
        data[requestIdKeyString] = requestId
    if not handshake:
        return sendData(redisConnection=redisConnection, data=data, queueName=camera2ioq, aProducer=f"{aProducer}")
    else:
        return sendData(redisConnection=redisConnection, data={genericdataKeyString: HANDSHAKE}, queueName=camera2ioq, aProducer=f"{aProducer}")

def sendHeartbeatFromCameraServerToHeartbeatServer(redisConnection : redis.client.Redis, status : ConnectionStatusType, aProducer : str = "CameraServer"):
    return sendData(redisConnection=redisConnection, data={connectionStatusKeyString: status}, queueName=camera2hbq, aProducer=f"{aProducer}")

# ********************************** COMMUNICATION INWARD INTO CAMERA SERVER *************************

_lastCameraRequestId : dict = {"value": None}     # request id of the last message read by the camera server

# returns 3 values - 1. timeOfMessage in milliseconds, 2. what state the machine is in, so that the appropriate
# routine can be called for evaluation of the picture, and 3. a boolean, whether the request is to take and evaluate a newwidgets picture
def readDataInCameraServerFromIOServer(redisConnection: redis.client.Redis, block : int = 500,
                                       withRequestId : bool = False):
    # Note this is not in a 'while not done' loop. This ensures that this function call does not block
    # Calling programs will need to implement a loop, if needed. Or, run this as an aync call
    # withRequestId: a 4th value, the IOServer's request id (None if the message has none)
    if withRequestId:
        result = readDataInCameraServerFromIOServer(redisConnection, block=block, withRequestId=False)
        return (*result, _lastCameraRequestId.get("value"))
    _lastCameraRequestId["value"] = None
    if redisConnection is None:
        printBoldRed("Redis connection is None in readDataInCameraServerFromIOServer(). Cannot receive data.")
        # raise Exception("Redis Connection is None in readDataInCameraServerFromIOServer()")
        return None, None, False
    last_id = 0
    newData = {}
    timeOfMessage = None
    try:
        redisConnection.xtrim(io2cameraq, 1)
        resp = redisConnection.xread({io2cameraq: last_id}, count=1, block=block)
        if resp:
            key, messages = resp[0]
            last_id, data = messages[0]
            keys = data.keys()
            for key in keys:
                value = data[key]
                newData[key.decode("utf-8")] = value.decode("utf-8")
            timeOfMessage = newData[timeKeyString]
            redisConnection.xdel(io2cameraq, last_id)
            redisConnection.xtrim(io2cameraq, 0)
            # printBoldGreen(f"Got data = {newData}")
    except Exception as e1:
        printBoldRed(f"Got exception - {e1}")
        return None, None, False
    try:
        if (newData[genericdataKeyString] is not None) and (newData[genericdataKeyString] == HANDSHAKE):
            return None, None, False
    except Exception:
        noteSuppressed()

    try:
        if newData.get(actionKeyString) == END_OF_CYCLE:          # the cycle is over: no picture, state READ_QR_CODE
            return timeOfMessage, MachineState.READ_QR_CODE, False
    except Exception:
        noteSuppressed()

    try:
        if (newData[actionKeyString] is not None) and (newData[actionKeyString] == takePictureCommand):
            _lastCameraRequestId["value"] = newData.get(requestIdKeyString) or None
            currentMachineState = MachineState.getMachineStateFromString(newData[currentMachineStateKeyString])
            # printLight(f"Current state is {currentMachineState} and type is {type(currentMachineState)}")
            return timeOfMessage, currentMachineState, True
    except Exception:
        return None, None, False

    return None, None, False

# data = {currentMachineStateKeyString: currentMachineState, move2StateKeyString: moveToState, actionKeyString: action, valueKeyString: value}

# returns component qrCode and a boolean value indicating that a valid component name has been found
def readComponentQRCodeInCameraServerFromQRCodeServer(redisConnection: redis.client.Redis, block : int = 500) -> Tuple[str | None, bool]:
    # Note this is not in a 'while not done' loop. This ensures that this function call does not block
    # Calling programs will need to implement a loop, if needed. Or, run this as an aync call
    if redisConnection is None:
        printBoldRed("Redis connection is None in readDataInCameraServerFromFEServer(). Cannot receive data.")
        # raise Exception("Redis Connection is None in readDataInCameraServerFromIOServer()")
        return None, False
    last_id = 0
    newData = {}
    timeOfMessage = None

    try:
        # print(f"In readDataInCameraServerFromFEServer()")
        redisConnection.xtrim(qrcode2cameraq, 1)
        resp = redisConnection.xread({qrcode2cameraq: last_id}, count=1, block=block)
        if resp:
            key, messages = resp[0]
            last_id, data = messages[0]
            keys = data.keys()
            for key in keys:
                value = data[key]
                newData[key.decode("utf-8")] = value.decode("utf-8")
            timeOfMessage = newData[timeKeyString]
            redisConnection.xdel(qrcode2cameraq, last_id)
            redisConnection.xtrim(qrcode2cameraq, 0)
            # printBoldGreen(f"Got data = {newData}")
    except Exception as e1:
        printBoldRed(f"Got exception - {e1}")
        return None, False
    try:
        if (newData[genericdataKeyString] is not None) and (newData[genericdataKeyString] == HANDSHAKE):
            return None, False
    except Exception:
        noteSuppressed()

    try:
        if (newData[componentQRCodeKeyString] is not None):
            return newData[componentQRCodeKeyString], True
    except Exception:
        return None, False

    return None, False


# ********************************** COMMUNICATION OUTWARD FROM HEARTBEAT SERVER *************************

def sendHeartbeatsFromHeartbeatServerToFEServer(redisConnection : redis.client.Redis,
                                                qrCodeServerStatus : ConnectionStatusType,
                                                cameraServerStatus : ConnectionStatusType,
                                                ioServerStatus : ConnectionStatusType,
                                                dbServerStatus : ConnectionStatusType,
                                                aProducer : str = "HeartbeatServer", handshake : bool = False,
                                                redisStatus : Optional[ConnectionStatusType] = None):
    data = {cameraServerStatusKeyString: cameraServerStatus, qrCodeServerStatusKeyString : qrCodeServerStatus,
            ioServerStatusKeyString : ioServerStatus, dbServerStatusKeyString : dbServerStatus}
    if redisStatus is not None:
        data[redisServerStatusKeyString] = redisStatus
    if not handshake:
        return sendData(redisConnection=redisConnection, data=data, queueName=hb2feq, aProducer=f"{aProducer}")
    else:
        return sendData(redisConnection=redisConnection, data={genericdataKeyString: HANDSHAKE}, queueName=hb2feq, aProducer=f"{aProducer}")

def sendCombinedHeartbeatFromHeartbeatServerToIOServer(redisConnection : redis.client.Redis,
                                                combinedConnectionStatus : ConnectionStatusType,
                                                aProducer : str = "HeartbeatServer", handshake : bool = False,
                                                serverStatuses : Optional[dict] = None):
    # serverStatuses: {"camera", "qrcode", "io", "db", "fe": ALIVE / DEAD} - each server's status, for the IOServer's
    # rules per server (readServerStatusesInIOServerFromHeartbeatServer)
    data = {connectionStatusKeyString: combinedConnectionStatus}
    if serverStatuses:
        for name, key in (("camera", cameraServerStatusKeyString), ("qrcode", qrCodeServerStatusKeyString),
                          ("io", ioServerStatusKeyString), ("db", dbServerStatusKeyString),
                          ("fe", feServerStatusKeyString), ("redis", redisServerStatusKeyString)):
            if name in serverStatuses:
                data[key] = serverStatuses[name]
    if not handshake:
        return sendData(redisConnection=redisConnection, data=data, queueName=hb2ioq, aProducer=f"{aProducer}")
    else:
        return sendData(redisConnection=redisConnection, data={genericdataKeyString: HANDSHAKE}, queueName=hb2ioq, aProducer=f"{aProducer}")

# ********************************** COMMUNICATION INWARD INTO HEARTBEAT SERVER *************************

def readDataInHeartbeatServerFromIOServer(redisConnection: redis.client.Redis, block : int = 500):
    # Note this is not in a 'while not done' loop. This ensures that this function call does not block
    # Calling programs will need to implement a loop, if needed. Or, run this as an aync call
    if redisConnection is None:
        printBoldRed("Redis connection is None in readDataInHeartbeatServerFromIOServer(). Cannot receive data.")
        return False
        # raise Exception("Redis Connection is None in readDataInCameraServerFromIOServer()")
    last_id = 0
    newData = {}
    timeOfMessage = None
    try:
        redisConnection.xtrim(io2hbq, 1)
        resp = redisConnection.xread({io2hbq: last_id}, count=1, block=block)
        if resp:
            key, messages = resp[0]
            last_id, data = messages[0]
            keys = data.keys()
            for key in keys:
                value = data[key]
                newData[key.decode("utf-8")] = value.decode("utf-8")
            timeOfMessage = newData[timeKeyString]
            redisConnection.xdel(io2hbq, last_id)
            redisConnection.xtrim(io2hbq, 0)
            # printBoldGreen(f"Got data = {newData}")
    except Exception as e1:
        printBoldRed(f"Got exception - {e1}")
        return False
    try:
        if (newData[connectionStatusKeyString] is not None) and (newData[connectionStatusKeyString] == ALIVE):
            return True
    except Exception:
        return False
    return False

def readDataInHeartbeatServerFromQRCodeServer(redisConnection: redis.client.Redis, block : int = 500):
    # Note this is not in a 'while not done' loop. This ensures that this function call does not block
    # Calling programs will need to implement a loop, if needed. Or, run this as an aync call
    if redisConnection is None:
        printBoldRed("Redis connection is None in readDataInHeartbeatServerFromQRCodeServer(). Cannot receive data.")
        return False
        # raise Exception("Redis Connection is None in readDataInHeartbeatServerFromQRCodeServer()")
    last_id = 0
    newData = {}
    timeOfMessage = None
    try:
        redisConnection.xtrim(qrcode2hbq, 1)
        resp = redisConnection.xread({qrcode2hbq: last_id}, count=1, block=block)
        if resp:
            key, messages = resp[0]
            last_id, data = messages[0]
            keys = data.keys()
            for key in keys:
                value = data[key]
                newData[key.decode("utf-8")] = value.decode("utf-8")
            timeOfMessage = newData[timeKeyString]
            redisConnection.xdel(qrcode2hbq, last_id)
            redisConnection.xtrim(qrcode2hbq, 0)
            # printBoldGreen(f"Got data = {newData}")
    except Exception as e1:
        printBoldRed(f"Got exception - {e1}")
        return False
    try:
        if (newData[connectionStatusKeyString] is not None) and (newData[connectionStatusKeyString] == ALIVE):
            return True
    except Exception:
        return False
    return False

def readDataInHeartbeatServerFromFEServer(redisConnection: redis.client.Redis, block : int = 500):
    # Note this is not in a 'while not done' loop. This ensures that this function call does not block
    # Calling programs will need to implement a loop, if needed. Or, run this as an aync call
    if redisConnection is None:
        printBoldRed("Redis connection is None in readDataInHeartbeatServerFromFEServer(). Cannot receive data.")
        return False
        # raise Exception("Redis Connection is None in readDataInHeartbeatServerFromFEServer()")
    last_id = 0
    newData = {}
    timeOfMessage = None
    try:
        redisConnection.xtrim(fe2hbq, 1)
        resp = redisConnection.xread({fe2hbq: last_id}, count=1, block=block)
        if resp:
            key, messages = resp[0]
            last_id, data = messages[0]
            keys = data.keys()
            for key in keys:
                value = data[key]
                newData[key.decode("utf-8")] = value.decode("utf-8")
            timeOfMessage = newData[timeKeyString]
            redisConnection.xdel(fe2hbq, last_id)
            redisConnection.xtrim(fe2hbq, 0)
            # printBoldGreen(f"Got data = {newData}")
    except Exception as e1:
        printBoldRed(f"Got exception - {e1}")
        return False
    try:
        if (newData[connectionStatusKeyString] is not None) and (newData[connectionStatusKeyString] == ALIVE):
            return True
    except Exception:
        return False
    return False

def readDataInHeartbeatServerFromDatabaseServer(redisConnection: redis.client.Redis, block : int = 500):
    # Note this is not in a 'while not done' loop. This ensures that this function call does not block
    # Calling programs will need to implement a loop, if needed. Or, run this as an aync call
    if redisConnection is None:
        printBoldRed("Redis connection is None in readDataInHeartbeatServerFromDatabaseServer(). Cannot receive data.")
        return False
        # raise Exception("Redis Connection is None in readDataInHeartbeatServerFromDatabaseServer()")
    last_id = 0
    newData = {}
    timeOfMessage = None
    try:
        redisConnection.xtrim(db2hbq, 1)
        resp = redisConnection.xread({db2hbq: last_id}, count=1, block=block)
        if resp:
            key, messages = resp[0]
            last_id, data = messages[0]
            keys = data.keys()
            for key in keys:
                value = data[key]
                newData[key.decode("utf-8")] = value.decode("utf-8")
            timeOfMessage = newData[timeKeyString]
            redisConnection.xdel(db2hbq, last_id)
            redisConnection.xtrim(db2hbq, 0)
            # printBoldGreen(f"Got data = {newData}")
    except Exception as e1:
        printBoldRed(f"Got exception - {e1}")
        return False
    try:
        if (newData[connectionStatusKeyString] is not None) and (newData[connectionStatusKeyString] == ALIVE):
            return True
    except Exception:
        return False
    return False

def readDataInHeartbeatServerFromCameraServer(redisConnection: redis.client.Redis, block : int = 500):
    # Note this is not in a 'while not done' loop. This ensures that this function call does not block
    # Calling programs will need to implement a loop, if needed. Or, run this as an aync call
    if redisConnection is None:
        printBoldRed("Redis connection is None in readDataInHeartbeatServerFromCameraServer(). Cannot receive data.")
        return False
        # raise Exception("Redis Connection is None in readDataInHeartbeatServerFromCameraServer()")
    last_id = 0
    newData = {}
    timeOfMessage = None
    try:
        redisConnection.xtrim(camera2hbq, 1)
        resp = redisConnection.xread({camera2hbq: last_id}, count=1, block=block)
        if resp:
            key, messages = resp[0]
            last_id, data = messages[0]
            keys = data.keys()
            for key in keys:
                value = data[key]
                newData[key.decode("utf-8")] = value.decode("utf-8")
            timeOfMessage = newData[timeKeyString]
            redisConnection.xdel(camera2hbq, last_id)
            redisConnection.xtrim(camera2hbq, 0)
            # printBoldGreen(f"Got data = {newData}")
    except Exception as e1:
        printBoldRed(f"Got exception - {e1}")
        return False
    try:
        if (newData[connectionStatusKeyString] is not None) and (newData[connectionStatusKeyString] == ALIVE):
            return True
    except Exception:
        return False
    return False

def readAlarmInHeartbeatServer(redisConnection: redis.client.Redis, block : int = 500):
    # Note this is not in a 'while not done' loop. This ensures that this function call does not block
    # Calling programs will need to implement a loop, if needed. Or, run this as an aync call
    if redisConnection is None:
        printBoldRed("Redis connection is None in readAlarmInHeartbeatServer(). Cannot receive data.")
        return False
        # raise Exception("Redis Connection is None in readDataInCameraServerFromIOServer()")
    last_id = 0
    newData = {}
    timeOfMessage = None
    try:
        redisConnection.xtrim(alarmq, 1)
        resp = redisConnection.xread({alarmq: last_id}, count=1, block=block)
        if resp:
            key, messages = resp[0]
            last_id, data = messages[0]
            keys = data.keys()
            for key in keys:
                value = data[key]
                newData[key.decode("utf-8")] = value.decode("utf-8")
            timeOfMessage = newData[timeKeyString]
            redisConnection.xdel(alarmq, last_id)
            redisConnection.xtrim(alarmq, 0)
            # printBoldGreen(f"Got data = {newData}")
    except Exception as e1:
        printBoldRed(f"Got exception - {e1}")
        return False
    try:
        if (newData[alarmStatusKeyString] is not None) and (newData[alarmStatusKeyString] == ok):
            return True
    except Exception:
        return False
    return False

def readAllHeartbeatsInHeartbeatServer(redisConnection: redis.client.Redis, block: int = 500) -> dict:
    """
    Heartbeat server: read the latest heartbeat of all 5 servers (IO, QR code, camera, DB, frontend) in 3 Redis
    round trips, instead of 5 separate reads (15 round trips, each read blocking up to 0.5 s when its server was down,
    so one dead server delayed the status of every other one).

      1. each heartbeat stream is trimmed to its latest entry (one pipeline);
      2. one XREAD over the 5 streams: it returns at once with every stream that has an entry, and waits up to
         `block` ms only when none has;
      3. the entries read are deleted (one pipeline).
    A stream with no entry, or whose latest entry is not ALIVE, is False for this cycle.

    Taken from the production code, with three changes:
      - the trim is exact. redis-py trims approximately ('MAXLEN ~ 1') by default, and Redis then trims only whole
        internal nodes, so a short stream is not trimmed at all and XREAD returned the OLDEST heartbeat since the last
        check, not the latest (the old per-server reads behave the same way);
      - count=1 (XREAD's COUNT is per stream, so 1 already returns one entry from each stream);
      - a heartbeat that arrives between the XREAD and the delete is kept for the next cycle instead of being trimmed
        away (it is recent, and trimming it could count a live server as down).

    Returns {'io': bool, 'qrcode': bool, 'camera': bool, 'db': bool, 'fe': bool}.
    """
    queueToKey = {io2hbq: 'io', qrcode2hbq: 'qrcode', camera2hbq: 'camera', db2hbq: 'db', fe2hbq: 'fe'}
    result = {k: False for k in queueToKey.values()}
    if redisConnection is None:
        printBoldRed("Redis connection is None in readAllHeartbeatsInHeartbeatServer(). Cannot receive data.")
        return result
    try:
        pipe = redisConnection.pipeline(transaction=False)
        for q in queueToKey:
            pipe.xtrim(q, 1, approximate=False)     # exact: redis-py's default '~' trim leaves a short stream untouched
        pipe.execute()
        resp = redisConnection.xread({q: 0 for q in queueToKey}, count=1, block=block)
    except Exception as e:
        printBoldRed(f"readAllHeartbeatsInHeartbeatServer(): {e}")
        return result
    if not resp:
        return result
    pipe = redisConnection.pipeline(transaction=False)
    for streamName, messages in resp:
        q = streamName.decode("utf-8") if isinstance(streamName, bytes) else streamName
        if q not in queueToKey or not messages:
            continue
        entryId, raw = messages[0]
        status = raw.get(connectionStatusKeyString.encode("utf-8"), raw.get(connectionStatusKeyString))
        if isinstance(status, bytes):
            status = status.decode("utf-8", "replace")
        result[queueToKey[q]] = (status == ALIVE)
        pipe.xdel(q, entryId)
    try:
        pipe.execute()
    except Exception as e:
        printBoldRed(f"readAllHeartbeatsInHeartbeatServer(): could not delete the heartbeats read: {e}")
    return result

# ********************************** COMMUNICATION INWARD INTO DATABASE SERVER *************************

def readDataInDatabaseServerFromFEServer(redisConnection : redis.client.Redis, block : int = 500, printStatements : bool = False,
                                         deleteAfterRead : bool = True, returnMessageId : bool = False,
                                         returnRotationDone : bool = False, returnAbort : bool = False,
                                         returnBypassed : bool = False):
    # Reads the OLDEST pending cycle record on fe2db.q (FIFO). The queue is never trimmed here, so records that arrive
    # while the DB server is busy are kept, not discarded.
    #   deleteAfterRead=False + returnMessageId=True: the caller deletes the record with
    #   acknowledgeRecordInDatabaseServer() only after it has been stored, so a failed insert is retried, not lost.
    #   With returnMessageId=True the message id (or None) is appended as the last element of the returned tuple.
    #   With returnRotationDone=True, tighteningTorque1RotationDone and tighteningTorque2RotationDone (True / False /
    #   None) are appended after overallResult (before the message id).
    #   With returnAbort=True, aborted (bool) and abortReason (str) are appended after those (before the message id).
    #   With returnBypassed=True, bypassedPressAndTightening2 (bool) is appended after those (before the message id).

    # Note this is not in a 'while not done' loop. This ensures that this function call does not block
    # Calling programs will need to implement a loop, if needed. Or, run this as an aync call

    messageId = None
    genuineDataReceived : bool = False
    qrCode : str = ""
    knucklePicture: Union[np.ndarray, None] = None
    knuckleCheckResult : str = notok
    knuckleCheckDatetime : str = '1970-01-01 00:00:00'
    hubAndBottomBearingPicture: Union[np.ndarray, None] = None
    hubAndBottomBearingCheckResult : str = notok
    hubAndBottomBearingCheckDatetime : str = '1970-01-01 00:00:00'
    topBearingPicture: Union[np.ndarray, None] = None
    topBearingCheckResult : str = notok
    topBearingCheckDatetime : str = '1970-01-01 00:00:00'
    nutAndPlateWasherPicture: Union[np.ndarray, None] = None
    nutAndPlateWasherCheckResult : str = notok
    nutAndPlateWasherCheckDatetime : str = '1970-01-01 00:00:00'
    tighteningTorque1: float = 0.
    tighteningTorque1Result: str = notok
    tighteningTorque1Datetime : str = '1970-01-01 00:00:00'
    componentPressBunkPicture: Union[np.ndarray, None] = None
    componentPressBunkCheckResult : str = notok
    componentPressBunkCheckDatetime : str = '1970-01-01 00:00:00'
    componentPressDone : str = notok
    componentPressDoneDatetime : str = '1970-01-01 00:00:00'
    noBunkPicture: Union[np.ndarray, None] = None
    noBunkCheckResult : str = notok
    noBunkCheckDatetime : str = '1970-01-01 00:00:00'
    tighteningTorque2: float = 0.
    tighteningTorque2Result: str = notok
    tighteningTorque2Datetime : str = '1970-01-01 00:00:00'
    splitPinAndWasherPicture: Union[np.ndarray, None] = None
    splitPinAndWasherCheckResult : str = notok
    splitPinAndWasherCheckDatetime : str = '1970-01-01 00:00:00'
    capPicture: Union[np.ndarray, None] = None
    capCheckResult : str = notok
    capCheckDatetime : str = '1970-01-01 00:00:00'
    capPressBunkPicture: Union[np.ndarray, None] = None
    capPressBunkCheckResult : str = notok
    capPressBunkCheckDatetime : str = '1970-01-01 00:00:00'
    capPressDone : str = notok
    capPressDoneDatetime : str = '1970-01-01 00:00:00'
    freeRotationTorque1: float = 0.
    freeRotationTorque1Result: str = notok
    freeRotationTorque1Datetime : str = '1970-01-01 00:00:00'
    overallResult: str = notok
    tighteningTorque1RotationDone = None
    tighteningTorque2RotationDone = None
    aborted = False
    abortReason = ""
    bypassed = False
    timeOfMessage = None
    aProducer = None

    if redisConnection is None:
        printBoldRed("Redis connection is None in readDataInDatabaseServerFromFEServer(). Cannot receive data.")
        # raise Exception("Redis Connection is None")
        _result = (genuineDataReceived, timeOfMessage, qrCode,
                    knucklePicture, knuckleCheckResult, knuckleCheckDatetime,
                    hubAndBottomBearingPicture, hubAndBottomBearingCheckResult, hubAndBottomBearingCheckDatetime,
                    topBearingPicture, topBearingCheckResult, topBearingCheckDatetime,
                    nutAndPlateWasherPicture, nutAndPlateWasherCheckResult, nutAndPlateWasherCheckDatetime,
                    tighteningTorque1, tighteningTorque1Result, tighteningTorque1Datetime,
                    componentPressBunkPicture, componentPressBunkCheckResult, componentPressBunkCheckDatetime,
                    componentPressDone, componentPressDoneDatetime,
                    noBunkPicture, noBunkCheckResult, noBunkCheckDatetime,
                    tighteningTorque2, tighteningTorque2Result, tighteningTorque2Datetime,
                    splitPinAndWasherPicture, splitPinAndWasherCheckResult, splitPinAndWasherCheckDatetime,
                    capPicture, capCheckResult, capCheckDatetime,
                    capPressBunkPicture, capPressBunkCheckResult, capPressBunkCheckDatetime,
                    capPressDone, capPressDoneDatetime,
                    freeRotationTorque1, freeRotationTorque1Result, freeRotationTorque1Datetime,
                    overallResult)
        if returnRotationDone:
            _result = _result + (tighteningTorque1RotationDone, tighteningTorque2RotationDone)
        if returnAbort:
            _result = _result + (aborted, abortReason)
        if returnBypassed:
            _result = _result + (bypassed,)
        return (_result + (messageId,)) if returnMessageId else _result

    last_id = 0

    try:
        resp = redisConnection.xread(
            {fe2dbq: last_id}, count=1, block=block
        )
        if resp:
            genuineDataReceived = True
            key, messages = resp[0]
            last_id, data = messages[0]
            keys = data.keys()
            newData = {}
            for key in keys:
                value = data[key]
                # NOTE : Since this message carries image bytes, DO NOT indiscriminately use .decode('utf-8') on the values
                newData[key.decode("utf-8")] = value

            timeOfMessage = newData[timeKeyString].decode('utf-8')
            aProducer = newData[producerKeyString].decode('utf-8')

            try:
                qrCode = str(newData[qrCodeKeyString].decode('utf-8'))
            except Exception:
                qrCode = UNKNOWN

            try:
                knucklePicture = cv2.imdecode(np.frombuffer(newData[knucklePictureKeyString], np.uint8), 1)
                if printStatements:
                    print(knucklePicture.shape)
            except Exception:
                printBoldRed("Couldn't get knucklePicture")
                knucklePicture = None

            try:
                if knucklePicture is not None:
                    knuckleCheckResult = str(newData[knuckleCheckResultKeyString].decode('utf-8'))
            except Exception:
                knuckleCheckResult = notok

            try:
                knuckleCheckDatetime = str(newData[knuckleDatetimeKeyString].decode('utf-8'))
            except Exception:
                printBoldRed("Couldn't get knuckleCheckDatetime")
                knuckleCheckDatetime = '1970-01-01 00:00:00'

            try:
                hubAndBottomBearingPicture = cv2.imdecode(np.frombuffer(newData[hubAndBottomBearingPictureKeyString], np.uint8), 1)
                if printStatements:
                    print(hubAndBottomBearingPicture.shape)
            except Exception:
                printBoldRed("Couldn't get hubAndBottomBearingPicture")
                hubAndBottomBearingPicture = None

            try:
                if hubAndBottomBearingPicture is not None:
                    hubAndBottomBearingCheckResult = str(newData[hubAndBottomBearingCheckResultKeyString].decode('utf-8'))
            except Exception:
                hubAndBottomBearingCheckResult = notok

            try:
                hubAndBottomBearingCheckDatetime = str(newData[hubAndBottomBearingDatetimeKeyString].decode('utf-8'))
            except Exception:
                printBoldRed("Couldn't get hubAndBottomBearingCheckDatetime")
                hubAndBottomBearingCheckDatetime = '1970-01-01 00:00:00'

            try:
                topBearingPicture = cv2.imdecode(np.frombuffer(newData[topBearingPictureKeyString], np.uint8), 1)
                if printStatements:
                    print(topBearingPicture.shape)
            except Exception:
                printBoldRed("Couldn't get topBearingPicture")
                topBearingPicture = None

            try:
                if topBearingPicture is not None:
                    topBearingCheckResult = str(newData[topBearingCheckResultKeyString].decode('utf-8'))
            except Exception:
                topBearingCheckResult = notok

            try:
                topBearingCheckDatetime = str(newData[topBearingDatetimeKeyString].decode('utf-8'))
            except Exception:
                printBoldRed("Couldn't get topBearingCheckDatetime")
                topBearingCheckDatetime = '1970-01-01 00:00:00'

            try:
                nutAndPlateWasherPicture = cv2.imdecode(np.frombuffer(newData[nutAndPlateWasherPictureKeyString], np.uint8), 1)
                if printStatements:
                    print(nutAndPlateWasherPicture.shape)
            except Exception:
                printBoldRed("Couldn't get nutAndPlateWasherPicture")
                nutAndPlateWasherPicture = None

            try:
                if nutAndPlateWasherPicture is not None:
                    nutAndPlateWasherCheckResult = str(newData[nutAndPlateWasherCheckResultKeyString].decode('utf-8'))
            except Exception:
                nutAndPlateWasherCheckResult = notok

            try:
                nutAndPlateWasherCheckDatetime = str(newData[nutAndPlateWasherDatetimeKeyString].decode('utf-8'))
            except Exception:
                printBoldRed("Couldn't get nutAndPlateWasherCheckDatetime")
                nutAndPlateWasherCheckDatetime = '1970-01-01 00:00:00'

            try:
                tighteningTorque1 = float(newData[tighteningTorque1ValueKeyString].decode('utf-8'))
            except Exception:
                tighteningTorque1 = 0.0

            try:
                tighteningTorque1Result = str(newData[tighteningTorque1ResultKeyString].decode('utf-8'))
            except Exception:
                tighteningTorque1Result = notok

            try:
                tighteningTorque1Datetime = str(newData[tighteningTorque1DatetimeKeyString].decode('utf-8'))
            except Exception:
                printBoldRed("Couldn't get tighteningTorque1CheckDatetime")
                tighteningTorque1Datetime = '1970-01-01 00:00:00'

            try:
                componentPressBunkPicture = cv2.imdecode(np.frombuffer(newData[componentPressBunkPictureKeyString], np.uint8), 1)
                if printStatements:
                    print(componentPressBunkPicture.shape)
            except Exception:
                componentPressBunkPicture = None

            try:
                sentResult = str(newData[componentPressBunkCheckResultKeyString].decode('utf-8'))
                # without a picture only 'Bypassed' (Machine Setting: step skipped) is taken; else NOT OK
                if componentPressBunkPicture is not None or sentResult == BYPASSED_RESULT:
                    componentPressBunkCheckResult = sentResult
            except Exception:
                componentPressBunkCheckResult = notok

            try:
                componentPressBunkCheckDatetime = str(newData[componentPressBunkCheckDatetimeKeyString].decode('utf-8'))
            except Exception:
                printBoldRed("Couldn't get componentPressBunkCheckDatetime")
                componentPressBunkCheckDatetime = '1970-01-01 00:00:00'

            try:
                componentPressDone = str(newData[componentPressDoneResultKeyString].decode('utf-8'))
            except Exception:
                componentPressDone = notok

            try:
                componentPressDoneDatetime = str(newData[componentPressDoneDatetimeKeyString].decode('utf-8'))
            except Exception:
                printBoldRed("Couldn't get componentPressDatetime")
                componentPressDoneDatetime = '1970-01-01 00:00:00'

            try:
                noBunkPicture = cv2.imdecode(np.frombuffer(newData[noBunkPictureKeyString], np.uint8), 1)
                if printStatements:
                    print(noBunkPicture.shape)
            except Exception:
                noBunkPicture = None

            try:
                sentResult = str(newData[noBunkCheckResultKeyString].decode('utf-8'))
                # without a picture only 'Bypassed' (Machine Setting: step skipped) is taken; else NOT OK
                if noBunkPicture is not None or sentResult == BYPASSED_RESULT:
                    noBunkCheckResult = sentResult
            except Exception:
                noBunkCheckResult = notok

            try:
                noBunkCheckDatetime = str(newData[noBunkCheckDatetimeKeyString].decode('utf-8'))
            except Exception:
                printBoldRed("Couldn't get noBunkCheckDatetime")
                noBunkCheckDatetime = '1970-01-01 00:00:00'

            try:
                tighteningTorque2 = float(newData[tighteningTorque2ValueKeyString].decode('utf-8'))
            except Exception:
                tighteningTorque2 = 0.0

            try:
                tighteningTorque2Result = str(newData[tighteningTorque2ResultKeyString].decode('utf-8'))
            except Exception:
                tighteningTorque2Result = notok

            try:
                tighteningTorque2Datetime = str(newData[tighteningTorque2DatetimeKeyString].decode('utf-8'))
            except Exception:
                printBoldRed("Couldn't get tighteningTorque2Datetime")
                tighteningTorque2Datetime = '1970-01-01 00:00:00'

            try:
                splitPinAndWasherPicture = cv2.imdecode(np.frombuffer(newData[splitPinAndWasherPictureKeyString], np.uint8), 1)
                if printStatements:
                    print(splitPinAndWasherPicture.shape)
            except Exception:
                splitPinAndWasherPicture = None

            try:
                if splitPinAndWasherPicture is not None:
                    splitPinAndWasherCheckResult = str(newData[splitPinAndWasherCheckResultKeyString].decode('utf-8'))
            except Exception:
                splitPinAndWasherCheckResult = notok

            try:
                splitPinAndWasherCheckDatetime = str(newData[splitPinAndWasherCheckDatetimeKeyString].decode('utf-8'))
            except Exception:
                printBoldRed("Couldn't get splitPinAndWasherCheckDatetime")
                splitPinAndWasherCheckDatetime = '1970-01-01 00:00:00'

            try:
                capPicture = cv2.imdecode(np.frombuffer(newData[capPictureKeyString], np.uint8), 1)
                if printStatements:
                    print(capPicture.shape)
            except Exception:
                capPicture = None

            try:
                if capPicture is not None:
                    capCheckResult = str(newData[capCheckResultKeyString].decode('utf-8'))
            except Exception:
                capCheckResult = notok

            try:
                capCheckDatetime = str(newData[capCheckDatetimeKeyString].decode('utf-8'))
            except Exception:
                printBoldRed("Couldn't get capCheckDatetime")
                capCheckDatetime = '1970-01-01 00:00:00'

            try:
                capPressBunkPicture = cv2.imdecode(np.frombuffer(newData[capPressBunkPictureKeyString], np.uint8), 1)
                if printStatements:
                    print(capPressBunkPicture.shape)
            except Exception:
                capPressBunkPicture = None

            try:
                if capPressBunkPicture is not None:
                    capPressBunkCheckResult = str(newData[capPressBunkCheckResultKeyString].decode('utf-8'))
            except Exception:
                capPressBunkCheckResult = notok

            try:
                capPressBunkCheckDatetime = str(newData[capPressBunkCheckDatetimeKeyString].decode('utf-8'))
            except Exception:
                printBoldRed("Couldn't get capPressBunkCheckDatetime")
                capPressBunkCheckDatetime = '1970-01-01 00:00:00'

            try:
                capPressDone = str(newData[capPressDoneResultKeyString].decode('utf-8'))
            except Exception:
                capPressDone = notok

            try:
                capPressDoneDatetime = str(newData[capPressDoneDatetimeKeyString].decode('utf-8'))
            except Exception:
                printBoldRed("Couldn't get capPressDoneDatetime")
                capPressDoneDatetime = '1970-01-01 00:00:00'

            try:
                freeRotationTorque1 = float(newData[freeRotationTorque1ValueKeyString].decode('utf-8'))
            except Exception:
                freeRotationTorque1 = 0.0

            try:
                freeRotationTorque1Result = str(newData[freeRotationTorque1ResultKeyString].decode('utf-8'))
            except Exception:
                freeRotationTorque1Result = notok

            try:
                freeRotationTorque1Datetime = str(newData[freeRotationTorque1DatetimeKeyString].decode('utf-8'))
            except Exception:
                printBoldRed("Couldn't get freeRotationTorque1Datetime")
                freeRotationTorque1Datetime = '1970-01-01 00:00:00'

            try:
                overallResult = str(newData[overallResultKeyString].decode('utf-8'))
            except Exception:
                overallResult = notok

            tighteningTorque1RotationDone = decodeOptionalBool(newData.get(tighteningTorque1RotationDoneKeyString))
            tighteningTorque2RotationDone = decodeOptionalBool(newData.get(tighteningTorque2RotationDoneKeyString))
            aborted = newData.get(abortedKeyString, b"0").decode("utf-8") == "1"     # absent (older sender): not aborted
            abortReason = newData.get(abortReasonKeyString, b"").decode("utf-8")
            bypassed = newData.get(bypassedKeyString, b"0").decode("utf-8") == "1"       # absent: not bypassed

            if deleteAfterRead:
                redisConnection.xdel(fe2dbq, last_id)
            messageId = last_id

    except ConnectionError as e1:
        printBoldRed(f"There was a connection error - {e1}")
        _result = (genuineDataReceived, timeOfMessage, qrCode,
                    knucklePicture, knuckleCheckResult, knuckleCheckDatetime,
                    hubAndBottomBearingPicture, hubAndBottomBearingCheckResult, hubAndBottomBearingCheckDatetime,
                    topBearingPicture, topBearingCheckResult, topBearingCheckDatetime,
                    nutAndPlateWasherPicture, nutAndPlateWasherCheckResult, nutAndPlateWasherCheckDatetime,
                    tighteningTorque1, tighteningTorque1Result, tighteningTorque1Datetime,
                    componentPressBunkPicture, componentPressBunkCheckResult, componentPressBunkCheckDatetime,
                    componentPressDone, componentPressDoneDatetime,
                    noBunkPicture, noBunkCheckResult, noBunkCheckDatetime,
                    tighteningTorque2, tighteningTorque2Result, tighteningTorque2Datetime,
                    splitPinAndWasherPicture, splitPinAndWasherCheckResult, splitPinAndWasherCheckDatetime,
                    capPicture, capCheckResult, capCheckDatetime,
                    capPressBunkPicture, capPressBunkCheckResult, capPressBunkCheckDatetime,
                    capPressDone, capPressDoneDatetime,
                    freeRotationTorque1, freeRotationTorque1Result, freeRotationTorque1Datetime,
                    overallResult)
        if returnRotationDone:
            _result = _result + (tighteningTorque1RotationDone, tighteningTorque2RotationDone)
        if returnAbort:
            _result = _result + (aborted, abortReason)
        if returnBypassed:
            _result = _result + (bypassed,)
        return (_result + (messageId,)) if returnMessageId else _result

    _result = (genuineDataReceived, timeOfMessage, qrCode,
            knucklePicture, knuckleCheckResult, knuckleCheckDatetime,
            hubAndBottomBearingPicture, hubAndBottomBearingCheckResult, hubAndBottomBearingCheckDatetime,
            topBearingPicture, topBearingCheckResult, topBearingCheckDatetime,
            nutAndPlateWasherPicture, nutAndPlateWasherCheckResult, nutAndPlateWasherCheckDatetime,
            tighteningTorque1, tighteningTorque1Result, tighteningTorque1Datetime,
            componentPressBunkPicture, componentPressBunkCheckResult, componentPressBunkCheckDatetime,
            componentPressDone, componentPressDoneDatetime,
            noBunkPicture, noBunkCheckResult, noBunkCheckDatetime,
            tighteningTorque2, tighteningTorque2Result, tighteningTorque2Datetime,
            splitPinAndWasherPicture, splitPinAndWasherCheckResult, splitPinAndWasherCheckDatetime,
            capPicture, capCheckResult, capCheckDatetime,
            capPressBunkPicture, capPressBunkCheckResult, capPressBunkCheckDatetime,
            capPressDone, capPressDoneDatetime,
            freeRotationTorque1, freeRotationTorque1Result, freeRotationTorque1Datetime,
            overallResult)
    if returnRotationDone:
        _result = _result + (tighteningTorque1RotationDone, tighteningTorque2RotationDone)
    if returnAbort:
        _result = _result + (aborted, abortReason)
    if returnBypassed:
        _result = _result + (bypassed,)
    return (_result + (messageId,)) if returnMessageId else _result


def acknowledgeRecordInDatabaseServer(redisConnection: redis.client.Redis, messageId) -> bool:
    """Delete a cycle record from fe2db.q once it has been stored in the database."""
    try:
        redisConnection.xdel(fe2dbq, messageId)
        return True
    except Exception as e:
        printBoldRed(f"Could not acknowledge record {messageId} on {fe2dbq}: {e}")
        return False

# ********************************** COMMUNICATION OUTWARD FROM DB SERVER *************************

def sendHeartbeatFromDBServerToHeartbeatServer(redisConnection : redis.client.Redis, status : ConnectionStatusType, aProducer : str = "DBServer"):
    return sendData(redisConnection=redisConnection, data={connectionStatusKeyString: status}, queueName=db2hbq, aProducer=f"{aProducer}")

# *********************************************** CLEAR QUEUES and GET MESSAGE COUNT ****************

def clearQueues(redisConnection: redis.client.Redis, reportCounts : bool = False):
    queues = [io2feq, io2cameraq, io2qrcodeq, io2qrcodeabortq, io2hbq, qrcode2ioq, qrcode2feq, qrcode2hbq, qrcode2cameraq,
              fe2ioq, fe2dbq, fe2hbq, camera2ioq, camera2feq, camera2hbq, db2hbq, hb2feq, hb2ioq,
              alarmq, emergencyq, stopQ, stoppedResponseQ, IO_STATE_STREAM]
    try:
        redisConnection.flushdb()
    except Exception as e:
        print(f"Encountered {e} while clearing queues")

    try:
        for queue in queues:
            # print(f"Initial Message Count: {getMessageCount(self.redisConnection, queue)} entries in queue {queue}")
            redisConnection.xtrim(queue,0)
    except Exception as e:
        print(f"Encountered {e} while trimming queues to 0")

    if reportCounts:
        reportMessageCount(redisConnection=redisConnection)

def reportMessageCount(redisConnection: redis.client.Redis):
    queues = [io2feq, io2cameraq, io2qrcodeq, io2hbq, qrcode2ioq, qrcode2feq, qrcode2hbq, qrcode2cameraq,
              fe2ioq, fe2dbq, fe2hbq, camera2ioq, camera2feq, camera2hbq, db2hbq, hb2feq, hb2ioq,
              alarmq, emergencyq, stopQ, stoppedResponseQ]
    try:
        for queue in queues:
            print(f"{getMessageCount(redisConnection, queue)} entries in queue {queue}")
    except Exception as e:
        print(f"Encountered {e} while reporting message count in queues")


# *********************************************** OTHER METHODS ***************************************

def sendAlarmSignalToHeartbeatServer(redisConnection : redis.client.Redis, status : ResultType = ok, aProducer : str = "CameraServer"):
    return sendData(redisConnection=redisConnection, data={alarmStatusKeyString: status}, queueName=alarmq, aProducer=f"{aProducer}")

def sendImage(redisConnection: redis.client.Redis, anImage: Union[np.ndarray, None], queueName: str = commonStreamKey,
              aProducer: str = "Producer", trackTime: bool = False):
    # print(f'Image shape is {anImage.shape} and anImage dtype is {anImage.dtype}')
    # printBoldBlue(f"Redis connection is of type : {type(redisConnectionLeftCameraPicture)}")
    if redisConnection is None:
        printBoldRed("Redis connection is None. Cannot send image.")
        raise Exception("Redis Connection is None")
    if anImage is None:
        printBoldRed("Image is None. Cannot send image.")
        return False
    if queueName is None:
        printBoldRed("Queue Name is None. Cannot send image.")
        return False
    if trackTime:
        time1 : float = time.time()
    buffer = None
    try:
        if isinstance(anImage, np.ndarray) and anImage is not None:
            retval, buffer = cv2.imencode('.png', anImage)
    except Exception as e:
        printBoldRed(f"Could not encode startingImage.  Cannot send startingImage : {e}")
        raise e
        # return False
    imageAsMessage = ""
    if buffer is not None:
        imageAsMessage = np.array(buffer).tobytes()
    # printBoldBlue(f"Encoded startingImage is of type : {type(message)}")
    data = {
        timeKeyString: getCurrentTimeInMS(),
        producerKeyString: aProducer,
        originalImageKeyString: imageAsMessage
    }
    try:
        redisConnection.xadd(queueName, data)
        if trackTime:
            time2 : float = time.time()
            timeTaken = round((time2 - time1) * 1000, 2)
            # printPlain(f'{queueName} : Producer sent anImage in {timeTaken} ms')
        return True
    except ConnectionError as e1:
        raise e1
    # return False

def sendOkImage(redisConnection: redis.client.Redis, anImage: Union[np.ndarray, None], queueName: str = commonStreamKey,
                aProducer: str = "Producer", trackTime: bool = False):
    return sendImageWithResult(redisConnection=redisConnection, anImage=anImage, queueName=queueName, aProducer=aProducer, result=ok, trackTime=trackTime)

def sendNotOkImage(redisConnection: redis.client.Redis, anImage: Union[np.ndarray, None], queueName: str = commonStreamKey,
                   aProducer: str = "Producer", trackTime: bool = False):
    return sendImageWithResult(redisConnection=redisConnection, anImage=anImage, queueName=queueName, aProducer=aProducer, result=notok, trackTime=trackTime)

def sendOkData(redisConnection: redis.client.Redis, queueName: str = commonStreamKey, aProducer : str = "Producer"):
    return sendData(redisConnection=redisConnection, data={resultKeyString: ok}, queueName=queueName, aProducer=aProducer)

def sendNotOkData(redisConnection: redis.client.Redis, queueName: str = commonStreamKey, aProducer : str = "Producer"):
    return sendData(redisConnection=redisConnection, data={resultKeyString: notok}, queueName=queueName, aProducer=aProducer)

def sendTakeNextPictureCommandToCamera(redisConnection: redis.client.Redis, cameraId: int = 1, aProducer : str = "IOServer", handshake : bool = False):
    queueName = CosThetaConfigurator.getInstance().getCameraTakePicQueue(cameraId=cameraId)
    if not handshake:
        return sendData(redisConnection=redisConnection, data={idKeyString: f"{cameraId}", genericdataKeyString: TAKE_NEXT_PICTURE}, queueName=queueName, aProducer=f"{aProducer}_{cameraId}")
    else:
        return sendData(redisConnection=redisConnection,
                 data={idKeyString: f"{cameraId}", genericdataKeyString: HANDSHAKE}, queueName=queueName,
                 aProducer=f"{aProducer}_{cameraId}")
    # printBold(f"Sending data to {queueName}")

def sendMessageToDiscInterlockAndConnectionAlarm(redisConnection: redis.client.Redis, status : ConnectionStatusType, aProducer : str = "IOServer"):
    queueName = CosThetaConfigurator.getInstance().getConnectionAlarmQueue()
    return sendData(redisConnection=redisConnection, data={genericdataKeyString: status}, queueName=queueName, aProducer=f"{aProducer}")

# ********************************** CYCLE SETTINGS USED (IOServer -> DBServer) *************************
# The IOServer publishes the machine settings it wrote to the PLC in state 2, keyed by the component's QR code; the
# DB server stores them with that component's record. A plain key (not a stream), kept for 2 days.

CYCLE_SETTINGS_KEY_PREFIX: str = "cycleSettings:"
CYCLE_SETTINGS_TTL_SECS: int = 2 * 24 * 3600


def publishCycleSettingsForQRCode(redisConnection: redis.client.Redis, qrCode: str, settings: dict) -> bool:
    if redisConnection is None or not qrCode:
        return False
    redisConnection.set(f"{CYCLE_SETTINGS_KEY_PREFIX}{qrCode}", json.dumps(settings), ex=CYCLE_SETTINGS_TTL_SECS)
    return True


def readCycleSettingsForQRCode(redisConnection: redis.client.Redis, qrCode: str) -> dict:
    """Settings the IOServer wrote for this QR code, or {} if none were published."""
    if redisConnection is None or not qrCode:
        return {}
    try:
        raw = redisConnection.get(f"{CYCLE_SETTINGS_KEY_PREFIX}{qrCode}")
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def clearKeyCommunicationQueuesOnAbort():
    redisConnection = redis.Redis(connection_pool=redisPoolForClearingKeyQueues, retry_on_timeout=True)
    queues = [io2cameraq, io2qrcodeq, qrcode2ioq, qrcode2feq, qrcode2cameraq,
              camera2ioq, camera2feq]
    for q in queues:
        try:
            redisConnection.xtrim(q,0)
        except Exception:
            noteSuppressed()

# ================================================================================

# TESTING CAMERASERVER

# trialRedisConnection = Redis("localhost", 6379)
# clearQueues(trialRedisConnection)
# sendDataFromIOServerToCameraServer(trialRedisConnection, currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE)
# print("===================")
# print(readDataInFEServerFromCameraServer(trialRedisConnection))
# print("===================")
# print(readDataInIOServerFromCameraServer(trialRedisConnection))
# print(readDataInFEServerFromIOServer(trialRedisConnection))

# ================================================================================