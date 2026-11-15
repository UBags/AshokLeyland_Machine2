import queue  # noqa: F401 - was "from queue import *"; nothing used from it, imported for its import-time effects

import numpy as np
from typing import Tuple
from utils.RedisUtils import DOST
from BaseUtils import getFullyQualifiedName
from Configuration import CosThetaConfigurator
from typing import Dict
from logutils.SlaveLoggers import logBoth
from logutils.Logger import MessageType

import warnings
warnings.filterwarnings('ignore', '.*h264.*', )

import Configuration  # noqa: F401 - was "from Configuration import *"; nothing used from it, imported for its import-time effects
CosThetaConfigurator.getInstance()

class CheckSplitPinAndWasher():

    @staticmethod
    def checkSplitPinAndWasher(anImage: np.ndarray, currentPictures : Dict [str, np.ndarray | None] = None,
                               componentQRCode : str = DOST, gamma: float = 2.0) -> Tuple[np.ndarray | None, bool]:
        _src = getFullyQualifiedName(__file__, CheckSplitPinAndWasher)
        logBoth('logDebug', _src, f"CheckSplitPinAndWasher.checkSplitPinAndWasher() called with anImage = {anImage.shape if anImage is not None else 'None'}", MessageType.GENERAL)
        # logBoth('logDebug', _src, f"{countNonNoneValues(currentPictures) = }, and {componentQRCode = }", MessageType.GENERAL)
        if componentQRCode is None:
            return anImage, False
        return anImage, True