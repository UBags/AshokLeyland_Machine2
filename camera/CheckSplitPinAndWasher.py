import copy
from threading import Thread
from queue import *
import cv2

import logutils.SlaveLoggers
from utils.CosThetaFileUtils import *
from utils.RedisUtils import *
from BaseUtils import *
from logutils import SlaveLoggers
from logutils.SlaveLoggers import *
from concurrent.futures import ThreadPoolExecutor, wait

import warnings
warnings.filterwarnings('ignore', '.*h264.*', )

from Configuration import *
CosThetaConfigurator.getInstance()

class CheckSplitPinAndWasher():

    @staticmethod
    def checkSplitPinAndWasher(anImage: np.ndarray, currentPictures : Dict [str, np.ndarray | None] = None,
                               componentQRCode : str = DOST, gamma: float = 2.0) -> Tuple[np.ndarray | None, bool]:
        printBoldBlue(f"CheckSplitPinAndWasher.checkSplitPinAndWasher() called with anImage = {anImage.shape if anImage is not None else 'None'}")
        # printBoldBlue(f"{countNonNoneValues(currentPictures) = }, and {componentQRCode = }")
        if componentQRCode is None:
            return anImage, False
        return anImage, True