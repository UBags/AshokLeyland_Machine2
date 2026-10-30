from PySide6 import QtCore
from PySide6.QtWidgets import QWidget, QLabel, QFrame, QVBoxLayout
from PySide6.QtGui import *
from multimethod import multimethod
# from multidispatch import dispatch
from frontend.CosThetaMonitorDimensions import getAppInstance, populateMonitorDimensions
from frontend.CosThetaStylesheets import okLabelStylesheet, notokLabelStylesheet, noResultLabelStylesheet, \
    outcomeLabelStylesheet
from frontend.frontendutils.CosThetaLabelUtils import *
from frontend.frontendutils.CosThetaImageUtils import *
from Configuration import *
import cv2
from typing import Union

app = getAppInstance()
populateMonitorDimensions() # needs to be called to ensure that a QtGui context is there for some calls to work

class CosTheta_TwoOutcomesContainer(QWidget):

    spacingBetweenWidgets = 5
    logger = None
    OK_STYLESHEET = okLabelStylesheet.format(QColor(Qt.GlobalColor.white).name())
    NOT_OK_STYLESHEET = notokLabelStylesheet.format(QColor(Qt.GlobalColor.white).name())
    RESULT_AWAITED_STYLESHEET = noResultLabelStylesheet.format(QColor(Qt.GlobalColor.white).name())

    def __init__(self, title : str, width : int, height : int, imageUpper : Union[QPixmap, QImage, ndarray, str], imageLower : Union[QPixmap, QImage, ndarray, str], labelHeight : Union[float, int] = 50., bg_color : QColor = Qt.GlobalColor.black, fg_color : QColor = Qt.GlobalColor.white, default_status : str ="Result Awaited"):
        super().__init__()
        self.setAcceptDrops(False)
        self.setObjectName(f"{title}_OutcomeContainerWidget")
        self.width = width
        self.height = height
        self.labelHeight = labelHeight
        self.imageHeight = int((self.height - 2 * self.labelHeight -
                                 7 * CosTheta_TwoOutcomesContainer.spacingBetweenWidgets) // 2)
        self.default_status = default_status
        self.currentStatus = self.default_status

        # CosTheta_TwoOutcomesContainer.logger.debug(f"Creating OutcomeContainer {self.objectName()}")
        labelFontSize = CosThetaConfigurator.getInstance().getInitialFontsize()
        self.labelFont = QFont(CosThetaConfigurator.getInstance().getFontFace(), labelFontSize)
        self.labelFont.setWeight(QFont.Weight.Bold)
        # metrics = QFontMetrics(self.labelFont)
        # labelTextRect = metrics.boundingRect(0, 0, 0, 0,
        #                                 Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextExpandTabs,
        #                                 title)
        # textWidth = labelTextRect.width()
        # textHeight = labelTextRect.height()
        # reductionFactor = min(width * 1.0 / textWidth, labelHeight * 1.0 / textHeight) * 0.90
        # fontSize = int(reductionFactor * labelFontSize)
        # self.labelFont.setPointSize(fontSize)
        # printBold(f"Resized font to {fontSize} for {title}")
        # CosThetaOutcomeContainer.logger.debug(
        #     f"Resized font {CosThetaConfigurator.getInstance().getFontFace()} to {labelFontSize}")
        self.labelFont.setPointSize(13)
        self.title_label = QLabel(title)  # Container's title
        self.title_label.setFont(self.labelFont)
        self.title_label.resize(self.width, int(0.7 * labelHeight))
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setStyleSheet(outcomeLabelStylesheet.format(QColor(fg_color).name())) # Set the background and foreground color of the container's label
        self.title_label.setFixedHeight(int(0.7 * labelHeight))
        self.title_label.setFixedWidth(self.width)
        self.title_label.setScaledContents(True)
        # CosTheta_TwoOutcomesContainer.logger.debug(f"Creating outcome container label {self.objectName()}")

        self.image_frame = QFrame()  # Main container to hold all TaskWidget objects
        self.image_frame.setObjectName(f"{title}_OutcomeContainerFrame")
        self.image_frame_vboxlayout = QVBoxLayout()
        self.image_frame_vboxlayout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

        if isinstance(imageUpper, str):
            bkgrndcolor = QColor(206, 206, 206)
            imageUpper = createImage(imageUpper, imageDimensions=(int(self.width - 7), self.imageHeight),
                                fontColor=QColor(Qt.GlobalColor.darkBlue), replaceChar=['=', ' '], backgroundColor=bkgrndcolor)
        self.default_pixmap_upper = getPixmapImage(imageUpper)
        self.currentPixmapUpper = self.default_pixmap_upper
        if isinstance(imageLower, str):
            bkgrndcolor = QColor(206, 206, 206)
            imageLower = createImage(imageLower, imageDimensions=(int(self.width - 7), self.imageHeight),
                                fontColor=QColor(Qt.GlobalColor.darkBlue), replaceChar=['=', ' '], backgroundColor=bkgrndcolor)
        self.default_pixmap_lower = getPixmapImage(imageLower)
        self.currentPixmapLower = self.default_pixmap_lower
        self.upperStatus = self.default_status
        self.image_label_upper = QLabel()  # Image label - upper
        self.image_label_upper.setPixmap(self.default_pixmap_upper)
        self.lowerStatus = self.default_status
        self.image_label_lower = QLabel()  # Image label - lower
        self.image_label_lower.setPixmap(self.default_pixmap_lower)
        self.image_frame_vboxlayout.setContentsMargins(0, 0, 0, 0)
        self.image_frame_vboxlayout.addWidget(self.image_label_upper)
        self.image_frame_vboxlayout.addSpacing(CosTheta_TwoOutcomesContainer.spacingBetweenWidgets)
        self.image_frame_vboxlayout.addWidget(self.image_label_lower)
        self.result_label = createResultLabel(result = self.currentStatus, width = self.width, labelHeight = self.labelHeight)  # Label
        # self.holding_frame_hboxlayout.addSpacing(CosTheta_TwoOutcomesContainer.spacingBetweenWidgets - 3)
        self.image_frame_vboxlayout.addWidget(self.result_label)
        self.image_frame.setLayout(self.image_frame_vboxlayout)

        # CosTheta_TwoOutcomesContainer.logger.debug(f"Creating QFrame {self.holding_frame.objectName()}")
        # Main layout for container class
        self.outcome_container_vboxlayout = QVBoxLayout()
        self.outcome_container_vboxlayout.setContentsMargins(0, 0, 0, 0)
        self.outcome_container_vboxlayout.setSpacing(0)  # No space between widgets
        self.outcome_container_vboxlayout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.outcome_container_vboxlayout.addWidget(self.title_label)
        self.outcome_container_vboxlayout.addSpacing(CosTheta_TwoOutcomesContainer.spacingBetweenWidgets)
        self.outcome_container_vboxlayout.addWidget(self.image_frame)
        # self.container_vboxlayout.addWidget(self.result_label)

        self.setLayout(self.outcome_container_vboxlayout)
        self.setMaximumSize(self.width, self.height)
        # self.setStyleSheet("background-color: rgb(255,0,0); margin:5px; border:1px solid rgb(0, 255, 0); ")
        # self.setStyleSheet("border:1px solid rgb(255, 0, 0); ")
        # CosTheta_TwoOutcomesContainer.logger.debug(f"Finished creating OutcomeContainer {self.objectName()}")
        self.update()

    def sizeHint(self):
        return QSize(self.width, self.height)

    @multimethod
    def getImage(self, result : str):
        if result.upper() == 'OK':
            # CosTheta_TwoOutcomesContainer.logger.debug(f"Creating OK result startingImage")
            result_image = createGreenImage(result, imageDimensions=(self.width, self.labelHeight))
        elif result.upper() == 'NOT OK' or result.upper() == 'NOT\bOK':
            # CosTheta_TwoOutcomesContainer.logger.debug(f"Creating Not OK result startingImage")
            result_image = createRedImage(result, imageDimensions=(self.width, self.labelHeight))
        else:
            # CosTheta_TwoOutcomesContainer.logger.debug(f"Creating blue startingImage with {result}")
            result_image = createBlueImage(result, imageDimensions=(self.width, self.labelHeight))
        return getPixmapImage(result_image)

    @multimethod
    def getImage(self, result : float):
        # CosTheta_TwoOutcomesContainer.logger.debug(f"Creating blue startingImage with value {result}")
        result = str(result)
        result_image = createBlueImage(result, imageDimensions=(self.width, self.labelHeight))
        return getPixmapImage(result_image)

    @multimethod
    def getImage(self, result : str, width : float, height : float):
        if result.upper() == 'OK':
            # CosTheta_TwoOutcomesContainer.logger.debug(f"Creating OK result startingImage")
            result_image = createGreenImage(result, imageDimensions=(width, height))
        elif result.upper() == 'NOT OK' or result.upper() == 'NOT\bOK':
            # CosTheta_TwoOutcomesContainer.logger.debug(f"Creating Not OK result startingImage")
            result_image = createRedImage(result, imageDimensions=(width, height))
        else:
            # CosTheta_TwoOutcomesContainer.logger.debug(f"Creating blue startingImage with {result}")
            result_image = createBlueImage(result, imageDimensions=(width, height))
        return getPixmapImage(result_image)

    @multimethod
    def getImage(self, result : int, width : float, height : float):
        # CosTheta_TwoOutcomesContainer.logger.debug(f"Creating blue startingImage with {result}")
        result = str(result)
        result_image = createBlueImage(result, imageDimensions=(width, height))
        return getPixmapImage(result_image)

    def getCurrentStatus(self):
        return self.currentStatus

    def getCurrentPixmapUpper(self):
        return self.currentPixmapUpper

    def getCurrentPixmapLower(self):
        return self.currentPixmapLower

    def getResult(self, result):
        return self.result_label.text()

    def setUpperStatus(self, status):
        self.upperStatus = status

    def setLowerStatus(self, status):
        self.lowerStatus = status

    # Private method. Do not call from external class. Let internal methods call this only.
    def _setResult(self, result : str):
        # self.result_label.setPixmap(self.getResultImage(result))
        self.result_label.setText(result)
        self.currentStatus = result
        # printPlain(f"About to do comparison of result with value {result.lower()}")
        if result.lower() == 'ok':
            # CosTheta_TwoOutcomesContainer.logger.debug(f"Creating OK result label")
            self.result_label.setStyleSheet(okLabelStylesheet.format(
                QColor(Qt.GlobalColor.white).name()))  # Set the background and foreground color of the container's label
        elif (result.lower() == 'not ok') or (result.lower() == 'notok'):
            # CosTheta_TwoOutcomesContainer.logger.debug(f"Creating Not OK result label")
            self.result_label.setStyleSheet(notokLabelStylesheet.format(
                QColor(Qt.GlobalColor.white).name()))  # Set the background and foreground color of the container's label
        else:
            # CosTheta_TwoOutcomesContainer.logger.debug(f"Creating label with {result}")
            self.result_label.setStyleSheet(noResultLabelStylesheet.format(
                QColor(Qt.GlobalColor.white).name()))  # Set the background and foreground color of the container's label
        self.update()

    def calculateAndSetResult(self):
        if (self.upperStatus.lower() == "ok") and (self.lowerStatus.lower() == "ok"):
            self._setResult("OK")
        elif ((self.upperStatus.lower() == "not ok") or (self.upperStatus.lower() == "notok")) and (
                (self.lowerStatus.lower() == "not ok") or (self.lowerStatus.lower() == "notok")):
            self._setResult("Not OK")
        else:
            self._setResult(self.default_status)

    def setUpperImage(self, image):
        # printPlain("Before setPixmap() - 7")
        pixTemp = getPixmapImage(image)
        pix = pixTemp.scaled(self.width - 7, self.imageHeight, aspectMode = Qt.IgnoreAspectRatio, mode = Qt.SmoothTransformation)
        self.image_label_upper.setPixmap(pix)
        self.currentPixmapUpper = pix
        # printPlain("After setPixmap() - 7")
        self.update()

    def setUpperText(self, text):
        bkgrndcolor = QColor(206, 206, 206)
        image = createImage(text, imageDimensions=(int(self.width - 7), self.imageHeight),
                    fontColor=QColor(Qt.GlobalColor.darkBlue), replaceChar=['=', ' '], backgroundColor=bkgrndcolor)
        self.setUpperImage(image)

    def setLowerImage(self, image):
        # printPlain("Before setPixmap() - 7")
        pixTemp = getPixmapImage(image)
        pix = pixTemp.scaled(self.width - 7, self.imageHeight, aspectMode = Qt.IgnoreAspectRatio, mode = Qt.SmoothTransformation)
        self.image_label_lower.setPixmap(pix)
        self.currentPixmapLower = pix
        # printPlain("After setPixmap() - 7")
        self.update()

    def setLowerText(self, text):
        bkgrndcolor = QColor(206, 206, 206)
        image = createImage(text, imageDimensions=(int(self.width - 7), self.imageHeight),
                    fontColor=QColor(Qt.GlobalColor.darkBlue), replaceChar=['=', ' '], backgroundColor=bkgrndcolor)
        self.setLowerImage(image)

    def setUpperImageAndResult(self, image, result):
        self.setUpperImageAndStatus(image, result)

    def setUpperImageAndStatus(self, image, status):
        self.setUpperImage(image)
        self.setUpperStatus(status)
        if (self.upperStatus.lower() == "ok") and (self.lowerStatus.lower() == "ok"):
            self._setResult("ok")
        elif ((self.upperStatus.lower() == "not ok") or (self.upperStatus.lower() == "notok")) and (
                (self.lowerStatus.lower() == "not ok") or (self.lowerStatus.lower() == "notok")):
            self._setResult("not ok")
        else:
            self._setResult(self.default_status)
        self.update()

    def setUpperTextAndResult(self, text: str, result : str):
        self.setUpperTextAndStatus(text, result)

    def setUpperTextAndStatus(self, text: str, result : str):
        # printBoldRed("In setTextAndResult - with ", text, result)
        if result.upper() == "OK":
            # backgroundcolor = QColor(60, 210, 45)
            # backgroundcolor = QColor(175, 215, 240)
            backgroundcolor = QColor(225, 220, 245)
        elif (result.upper() == "NOT OK") or (result.upper() == "NOTOK"):
            # backgroundcolor = QColor(210, 45, 45)
            backgroundcolor = QColor(255, 185, 185)
            result = "Not OK"
        else:
            backgroundcolor = QColor(206, 206, 206)
        image = createImage(text, imageDimensions=(self.width - 7, self.imageHeight),
                            fontColor=QColor(Qt.GlobalColor.darkBlue), replaceChar=['=', ' '], backgroundColor=backgroundcolor)
        self.setUpperImageAndStatus(image, result)

    def setLowerImageAndStatus(self, image, status):
        self.setLowerImage(image)
        self.setLowerStatus(status)
        if (self.upperStatus.lower() == "ok") and (self.lowerStatus.lower() == "ok"):
            self._setResult("OK")
        elif ((self.upperStatus.lower() == "not ok") or (self.upperStatus.lower() == "notok")) and (
                (self.lowerStatus.lower() == "not ok") or (self.lowerStatus.lower() == "notok")):
            self._setResult("Not OK")
        else:
            self._setResult(self.default_status)
        self.update()

    def setLowerImageAndResult(self, image, result):
        self.setLowerImageAndStatus(image, result)

    def setLowerTextAndResult(self, text: str, result : str):
        self.setLowerTextAndStatus(text, result)

    def setLowerTextAndStatus(self, text: str, result : str):
        if result.upper() == "OK":
            backgroundcolor = QColor(225, 220, 245)
        elif (result.upper() == "NOT OK") or (result.upper() == "NOTOK"):
            backgroundcolor = QColor(255, 185, 185)
            result = "Not OK"
        else:
            backgroundcolor = QColor(206, 206, 206)
        image = createImage(text, imageDimensions=(self.width - 7, self.imageHeight),
                            fontColor=QColor(Qt.GlobalColor.darkBlue), replaceChar=['=', ' '], backgroundColor=backgroundcolor)
        self.setLowerImageAndStatus(image, result)

    # def setBackgroundAndResult(self, ndarrayimage, result : str, alpha = 0.8):
    #     if result.upper() == "OK":
    #         backgroundcolor = QColor(225, 220, 245)
    #     elif (result.upper() == "NOT OK") or (result.upper() == "NOTOK"):
    #         backgroundcolor = QColor(255, 185, 185)
    #         result = "Not OK"
    #     else:
    #         backgroundcolor = QColor(206, 206, 206)
    #     # overlayImage = createImage(" ", imageDimensions=(self.width - 7, self.height - 2 * self.labelHeight - 2 * CosThetaOutcomeContainer.spacingBetweenWidgets - 18 * CosThetaOutcomeContainer.spacingBetweenWidgets),
    #     #             fontColor=QColor(Qt.GlobalColor.darkBlue), replaceChar=['=', ' '], backgroundColor=backgroundcolor)
    #     # requiredWidth =  int(self.width - 7)
    #     # requiredHeight = int(self.height - 2 * self.labelHeight - 2 * CosThetaOutcomeContainer.spacingBetweenWidgets - 18 * CosThetaOutcomeContainer.spacingBetweenWidgets)
    #     # resizedImage = cv2.resize(startingImage, (requiredWidth, requiredHeight), interpolation=cv2.INTER_AREA)
    #     # # alpha = 0.8
    #     # beta = 1 - alpha
    #     # blendedImage = cv2.addWeighted(resizedImage, alpha, overlayImage, beta, 0.0)
    #     if ndarrayimage is not None:
    #         width = self.width - 7
    #         height = self.height - 2 * self.labelHeight - 5 * CosTheta_TwoOutcomesContainer.spacingBetweenWidgets
    #         qPixmap = convertNDArrayToPixmap(ndarrayimage)
    #         pixmap = qPixmap.scaled(width, height, QtCore.Qt.KeepAspectRatio)
    #     self.setUpperImageAndStatus(pixmap, result)

    def setUpperDefault(self):
        self.setUpperImageAndStatus(self.default_pixmap_upper, self.default_status)

    def setLowerDefault(self):
        self.setLowerImageAndStatus(self.default_pixmap_lower, self.default_status)

    def setDefault(self):
        self.setUpperDefault()
        self.setLowerDefault()

    def displayDefault(self):
        self.setDefault()

    def goToDefault(self):
        self.setDefault()

    def getImageWidth(self):
        return self.width - 7

    def getImageHeight(self):
        return self.imageHeight
