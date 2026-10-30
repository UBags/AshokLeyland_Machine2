# Since each camera runs in its own process, we need to tabulate results in only one of this
import threading
from typing import Union
import numpy as np
from Configuration import *
CosThetaConfigurator.getInstance()
from threading import Lock

RESULT : str = "result"
FINAL_IMAGE : str = "finalImage"

ok = CosThetaConfigurator.getInstance().getOkCommand()
notok = CosThetaConfigurator.getInstance().getNotOKCommand()
invalidResult = CosThetaConfigurator.getInstance().getInvalidResult()

previousResultsOfCamera = []
currentResultsOfCamera = []

lock = threading.Lock()

def appendToResults(result : str, image : np.ndarray):
    acquired = False
    try:
        acquired = lock.acquire()
        currentResultsOfCamera.append({RESULT : result, FINAL_IMAGE : image})
        if acquired:
            lock.release()
            acquired = False
    except:
        if acquired:
            lock.release()
            acquired = False

def clearResults():
    acquired = False
    try:
        acquired = lock.acquire()
        previousResultsOfCamera.clear()
        for item in currentResultsOfCamera:
            previousResultsOfCamera.append(item)
        currentResultsOfCamera.clear()
        if acquired:
            lock.release()
            acquired = False
    except:
        if acquired:
            lock.release()

def getFinalResult():
    acquired = False
    finalResult = invalidResult
    finalImage = None
    try:
        acquired = lock.acquire()
        okCount = 0
        notOKCount = 0
        invalidResultCount = 0
        for item in currentResultsOfCamera:
            if item[RESULT] == ok:
                okCount += 1
            elif item[RESULT] == notok:
                notOKCount += 1
            else:
                invalidResultCount += 1
        maxcount = max(okCount, notOKCount, invalidResultCount)
        if (okCount == maxcount) and (okCount > 1):
            finalResult = ok
            for item in currentResultsOfCamera:
                if item[RESULT] == ok:
                    finalImage = item[FINAL_IMAGE]
                    break
        elif (notOKCount == maxcount) and (notOKCount > 1):
            finalResult = notok
            for item in currentResultsOfCamera:
                if item[RESULT] == notok:
                    finalImage = item[FINAL_IMAGE]
                    break
        else:
            finalResult = invalidResult
            finalImage = None
        if acquired:
            lock.release()
            acquired = False
    except:
        if acquired:
            lock.release()
    return finalResult, finalImage

