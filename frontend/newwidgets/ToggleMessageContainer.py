from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtGui import QFont, QFontMetrics, QColor
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSizePolicy, QWidget, QLabel
from frontend.FrontendI18n import tr

class ToggleMessageContainer(QWidget):

    def __init__(self, okText : str, notokText : str, okStyleSheet : str, notokStyleSheet : str, font : QFont, okforegroundColor : QColor, notokforegroundColor : QColor, minimumWidth : int, labelHeight : int, name = '', forceUseOfFontSize : int = 0) :
        super().__init__()

        # super().resize(minimumWidth, labelHeight)
        self._okText = okText
        self._notokText = notokText
        self._okStyleSheet = okStyleSheet
        self._notokStyleSheet = notokStyleSheet
        self._okforegroundColor = okforegroundColor
        self._notokforegroundColor = notokforegroundColor
        self._status = True
        # print(f"{minimumWidth = }")
        self._minimumWidth = minimumWidth
        # self.setMinimumWidth(self._minimumWidth)
        self.setObjectName(f"ToggleMessageContainer_{name}")

        labelFont = QFont(font)         # own copy: the four status tiles were given one shared QFont
        okTextLen = len(okText)
        notokTextLen = len(notokText)
        if okTextLen > notokTextLen:
            sampleText = okText + "    "
        else:
            sampleText = notokText + "    "
        labelFontSize = labelFont.pointSize()
        if forceUseOfFontSize == 0:
            metrics = QFontMetrics(labelFont)
            labelTextRect = metrics.boundingRect(0, 0, 0, 0,
                                                 Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextExpandTabs,
                                                 sampleText)
            textWidth = labelTextRect.width()
            textHeight = labelTextRect.height()
            reductionFactor = min(super().geometry().width() * 1.0 / textWidth,
                                  labelHeight * 1.0 / textHeight) * 0.95
            fontSize = int(reductionFactor * labelFontSize)
            # print(f"{super().geometry().width() = }, {reductionFactor = }, {labelFontSize = }, {fontSize = }")
            labelFont.setPointSize(fontSize)
            # print(f"For {okText}, set font size as {fontSize}")
            self.fontSize = fontSize
        else:
            labelFont.setPointSize(forceUseOfFontSize)
            self.fontSize = forceUseOfFontSize

        self._font = labelFont

        self._container_label = QLabel(tr(self._notokText)) # Container's title
        self._container_label.setStyleSheet(self._notokStyleSheet.format(self._notokforegroundColor.name()))
        self._container_label.setContentsMargins(0, 0, 0, 0)
        self._container_label.setFont(self._font)
        self._container_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._container_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # container_frame = QFrame() # Main container to hold the object
        # container_frame.setObjectName(f"ToggleMessage_ContainerFrame_{name}")

        # frame_v_box = QVBoxLayout()
        # frame_v_box.insertWidget(-1, self._container_label)
        # frame_v_box.setSpacing(0)
        # frame_v_box.setAlignment(Qt.AlignmentFlag.AlignTop)
        # frame_v_box.setContentsMargins(0, 0, 0, 0)
        # container_frame.setLayout(frame_v_box)

        message_h_box_layout = QHBoxLayout()
        self.setLayout(message_h_box_layout)
        message_h_box_layout.setSpacing(0)
        message_h_box_layout.setContentsMargins(0, 0, 0, 0)
        message_h_box_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # message_h_box_layout.insertWidget(-1, container_frame)
        message_h_box_layout.addWidget(self._container_label, 1)

    def label(self):
        return self._container_label

    def setFixedPointSize(self, pointSize: int):
        """Fixed font size for this tile (set once after the first render, the same for all the status tiles)."""
        self.fontSize = pointSize
        font = QFont(self._font)
        font.setPointSize(pointSize)
        self._font = font
        self._container_label.setFont(font)

    def updateStatus(self, currentStatus : bool):
        self._status = currentStatus
        if self._status:
            self._container_label.setText(tr(self._okText))      # English kept; shown in the chosen language
            self._container_label.setStyleSheet(self._okStyleSheet.format(self._okforegroundColor.name()))
        else:
            self._container_label.setText(tr(self._notokText))
            self._container_label.setStyleSheet(self._notokStyleSheet.format(self._notokforegroundColor.name()))
        # KEMSFrontEnd.logger.debug(f"About to update self widget in ToggleMessageContainer.updateStatus() in {super().objectName()}")
        self.update()

    def retranslate(self) -> None:
        """The status text again in the language now chosen (CHANGES 156)."""
        self.updateStatus(getattr(self, "_status", False))

    def setFont(self, newFont : QFont):
        self._font = newFont
        self._container_label.setFont(newFont)
        self._font.setPointSize(self.fontSize)

    def setAlignment(self, newAlignment):
        self._container_label.setAlignment(newAlignment)

    def setOKText(self, newOkText):
        self._okText = newOkText

    def setNotOKText(self, newNotOkText):
        self._notokText = newNotOkText

    def setWordWrap(self, value = True):
        self._container_label.setWordWrap(value)

    def setMinimumHeight(self, minHeight):
        self._container_label.setMinimumHeight(minHeight)