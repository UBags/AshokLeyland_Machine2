from PySide6.QtWidgets import QVBoxLayout
# from PySide6.QtCore.Qt import *
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QWidget, QLabel, QFrame
from pythonlangutil.overload import Overload, signature

from PySide6.QtGui import Qt
import utils.CosThetaFileUtils  # noqa: F401 - was "from utils.CosThetaFileUtils import *"; nothing used from it, imported for its import-time effects


class CosTheta_N_MessageContainer(QWidget):

    logger = None

    def __init__(self, messageList : list[str], styleSheetList : list[str], fontList : list[QFont], foregroundColorList : list[QColor], minimumWidth, initialisationState = 1) :

        super().__init__()
        if (messageList is None) or (styleSheetList is None) or (fontList is None) or (foregroundColorList is None):
            raise Exception(f"Unable to proceed. One or more of messageList={messageList}, styleSheetList={styleSheetList}, fontList={fontList}, foregroundList={foregroundColorList} are None")
        nMessages = len(messageList)
        nStyleSheets = len(styleSheetList)
        nFonts = len(fontList)
        nForegroundColors = len(foregroundColorList)
        if (nMessages != nStyleSheets) or (nMessages != nFonts) or (nMessages != nForegroundColors) or (nStyleSheets != nFonts) or (nStyleSheets != nForegroundColors) or (nFonts != nForegroundColors):
            raise Exception(
                f"Unable to proceed. One or more of message no(s) = {nMessages}, styleSheetList no(s) = {nStyleSheets}, fontList no(s) = {nFonts}, foregroundList no(s) = {nForegroundColors} are not consistent. All input lists should have the same length.")
        badData = ''
        # for font in fontList:
        #     if type(font) != QFont:
        #         badData.append(f'All fonts must be of type QFont. Found at least one entry of type {type(font)}')
        #         break
        # for color in foregroundColorList:
        #     if type(color) != QColor:
        #         badData.append(f'All colors must be of type QColor. Found at least one entry of type {type(color)}')
        #         break
        if len(badData) != 0:
            raise Exception(
                "Unable to proceed. Please ensure that fonts are of type QFont and foregroundColors are of type QColor")
        if initialisationState <= 0 or initialisationState > nMessages:
            raise Exception(
                f"Unable to proceed. Initialisation state has to be {'1' if (nMessages == 1) else f'a number between 1 - {nMessages}'}")
        self._messageList = messageList
        self._styleSheetList = styleSheetList
        self._fontList = fontList
        self._foregroundColorList = foregroundColorList
        self._possibleStates = []
        for i in range(nMessages):
            self._possibleStates.append(i+1)
        self._minimumWidth = minimumWidth
        self.setMinimumWidth(self._minimumWidth)
        self._currentState = initialisationState
        self.setObjectName(f"{nMessages}_MessageContainer")

        self._container_label = QLabel(self._messageList[initialisationState - 1]) # Container's title
        self._container_label.setStyleSheet(self._styleSheetList[initialisationState - 1].format(self._foregroundColorList[initialisationState - 1]))
        self._container_label.setFont(self._fontList[initialisationState - 1])
        # This centers the text in the label, and doesn't affect the size (width, height) of the label
        self._container_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        container_frame = QFrame() # Main container to hold the object
        container_frame.setObjectName(f"{nMessages}_Message_ContainerFrame")

        frame_v_box = QVBoxLayout()
        frame_v_box.insertWidget(-1, self._container_label)
        frame_v_box.setSpacing(0)
        # If one adds Qt.AlignmentFlag.AlignCenter, it minimises the size (width, height) of the label. Hence, avoid completely !
        frame_v_box.setAlignment(Qt.AlignmentFlag.AlignTop)
        frame_v_box.setContentsMargins(0, 0, 0, 0)
        container_frame.setLayout(frame_v_box)

        message_v_box = QVBoxLayout()
        message_v_box.insertWidget(-1, container_frame)
        message_v_box.setSpacing(0)
        # If one adds Qt.AlignmentFlag.AlignCenter, it minimises the size (width, height) of the label. Hence, avoid completely !
        message_v_box.setAlignment(Qt.AlignmentFlag.AlignTop)
        message_v_box.setContentsMargins(0, 0, 0, 0)
        self.setLayout(message_v_box)

    def updateState(self, newState : int, args=()):
        self.checkStateValidity(newState)
        self._currentState = newState
        self._container_label.setText(self._messageList[newState - 1].format(*args)) # Container's title
        self._container_label.setStyleSheet(self._styleSheetList[newState - 1].format(self._foregroundColorList[newState - 1]))
        self._container_label.setFont(self._fontList[newState - 1])
        self.update()

    def setCurrentState(self, newState : int, args=()):
        self.updateState(self, newState, args)

    def getCurrentState(self) -> int:
        return self._currentState

    # *****************

    def setMessage(self, state: int, message : str):
        self.checkStateValidity(state)
        self._messageList[state - 1] = message

    def getMessage(self, state : int) -> str :
        self.checkStateValidity(state)
        return self._messageList[state - 1]

    def setCurrentMessage(self, message : str):
        self._messageList[self._currentState - 1] = message

    def getCurrentMessage(self) -> str:
        return self._messageList[self._currentState - 1]

    def getCurrentText(self, args=()) -> str:
        return self._messageList[self._currentState - 1].format(*args)

    # *****************

    @Overload
    @signature("int","str")
    def setStyleSheet(self, state : int, styleSheet : str):
        self.checkStateValidity(state)
        self._styleSheetList[state - 1] = styleSheet

    def getStyleSheet(self, state : int) -> str:
        return self._styleSheetList[state - 1]

    def setCurrentStylesheet(self, styleSheet : str):
        self._styleSheetList[self._currentState - 1] = styleSheet

    def getCurrentStylesheet(self) -> str:
        return self._styleSheetList[self._currentState - 1]

    def getCurrentStyle(self) -> str:
        return self._styleSheetList[self._currentState - 1].format(self.getCurrentFont())

    # *****************

    @Overload
    @signature("int","QFont")
    def setFont(self, state : int, newFont : QFont):
        self.checkStateValidity(state)
        self._fontList[state - 1] = newFont

    def getFont(self, state : int) -> QFont:
        self.checkStateValidity(state)
        return self._fontList[state - 1]

    def setCurrentFont(self, newFont : QFont):
        self._fontList[self._currentState - 1] = newFont

    def getCurrentFont(self) -> QFont:
        return self._fontList[self._currentState - 1]

    # *****************

    @Overload
    @signature("int","QFont")
    def setForegroundColor(self, state : int, newColor : QColor):
        self.checkStateValidity(state)
        self._foregroundColorList[state - 1] = newColor

    def getForegroundColor(self, state : int) -> QColor:
        self.checkStateValidity(state)
        return self._foregroundColorList[state - 1]

    def setCurrentForegroundColor(self, newColor : QColor):
        self._foregroundColorList[self._currentState - 1] = newColor

    def getCurrentForegroundColor(self):
        return self._foregroundColorList[self._currentState - 1]

    # *****************

    def setAlignment(self, newAlignment):
        self._container_label.setAlignment(newAlignment)

    def setWordWrap(self, value = True):
        self._container_label.setWordWrap(value)

    def setMinimumHeight(self, minHeight):
        self._container_label.setMinimumHeight(minHeight)

    # *****************

    def checkStateValidity(self, state : int):
        if state in self._possibleStates:
            return True
        raise Exception(
            f"Unable to proceed. Illegal current state {state}; allowed states are {self._possibleStates}")
