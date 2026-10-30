import numpy as np
from PySide6 import QtCore
from PySide6.QtWidgets import QWidget, QLabel, QFrame, QVBoxLayout
from PySide6.QtGui import *
from multimethod import multimethod
import cv2

# from multidispatch import dispatch
from frontend.CosThetaMonitorDimensions import getAppInstance, populateMonitorDimensions
from frontend.CosThetaStylesheets import okLabelStylesheet, notokLabelStylesheet, noResultLabelStylesheet, outcomeLabelStylesheet, whiteBackgroundStylesheet
from frontend.widgets.CosTheta_OK_NotOK_Total_Container import *
from frontend.frontendutils.CosThetaLabelUtils import *
from frontend.frontendutils.CosThetaImageUtils import *
from Configuration import *
from utils import RedisUtils
from utils.RedisUtils import *

app = getAppInstance()
populateMonitorDimensions() # needs to be called to ensure that a QtGui context is there for some calls to work

class CosThetaOutcomeContainerWithOKNotokTotal(QWidget):

    spacingBetweenWidgets = 5
    OK_STYLESHEET = okLabelStylesheet.format(QColor(Qt.GlobalColor.white).name())
    NOT_OK_STYLESHEET = notokLabelStylesheet.format(QColor(Qt.GlobalColor.white).name())
    TOTAL_STYLESHEET = outcomeLabelStylesheet.format(QColor(Qt.GlobalColor.white).name())
    RESULT_AWAITED_STYLESHEET = noResultLabelStylesheet.format(QColor(Qt.GlobalColor.white).name())
    WHITE_BG_STYLESHEET = whiteBackgroundStylesheet.format(QColor(Qt.GlobalColor.black).name())

    okResult = CosThetaConfigurator.getInstance().getOkCommand()
    notokResult = CosThetaConfigurator.getInstance().getNotOKCommand()
    invalidResult = INVALID_RESULT

    def __init__(self, cust_name : str, med_name : str, title, width, height, startingImage, todaysOKCount : int = 0, todaysTotalCount : int = 0, labelHeight = 50., fg_color = Qt.GlobalColor.black, default_status ="Result Awaited"):
        super().__init__()
        self.setAcceptDrops(False)
        self.setObjectName(f"{title}_OutcomeContainerWidget")
        self.cust_name = cust_name
        self.med_name = med_name
        self.width = width
        self.height = height
        self.labelHeight = labelHeight
        self.imageHeight = self.height - 3 * self.labelHeight - 6 * CosThetaOutcomeContainerWithOKNotokTotal.spacingBetweenWidgets
        self.default_status = default_status
        self.currentStatus = self.default_status

        # CosThetaOutcomeContainer.logger.debug(f"Creating OutcomeContainer {self.objectName()}")
        labelFontSize = int(CosThetaConfigurator.getInstance().getInitialFontsize() * 1.5)
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
        self.labelFont.setPointSize(labelFontSize)
        self.title_label = QLabel(title)  # Container's title
        self.title_label.setFont(self.labelFont)
        self.title_label.resize(self.width, int(labelHeight))
        # self.outcome_container_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # self.outcome_container_label.setStyleSheet("border-style: outset; border-width: 3px; border-radius: 5px; "
        #                                            "border-color: beige; font: bold; min-width: 10em; padding: 2px; "
        #                                            "background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1, stop: 0 #E1E1E1, "
        #                                            "stop: 0.4 #DDDDDD, stop: 0.5 #D8D8D8, stop: 1.0 #D3D3D3);"
        #                                            "background-color: {}; color: {}".format(QColor(bg_color).name(), QColor(fg_color).name())) # Set the background and foreground color of the container's label
        self.title_label.setStyleSheet(outcomeLabelStylesheet.format(QColor(fg_color).name())) # Set the background and foreground color of the container's label
        self.title_label.setFixedHeight(int(labelHeight))
        self.title_label.setFixedWidth(self.width)
        self.title_label.setScaledContents(True)
        # CosThetaOutcomeContainer.logger.debug(f"Creating outcome container label {self.objectName()}")

        self.image_frame = QFrame()  # Main container to hold all TaskWidget objects
        self.image_frame.setObjectName(f"{title}_ImageFrame")
        self.image_and_result_frame_vboxlayout = QVBoxLayout()
        self.image_and_result_frame_vboxlayout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.starting_pixmap = getPixmapImage(startingImage)
        self.currentPixmap = self.starting_pixmap
        self.starting_image_label = QLabel()  # Image label
        # printPlain("Before setPixmap() - 4")
        self.starting_image_label.setPixmap(self.starting_pixmap)
        # printPlain("After setPixmap() - 4")
        self.targetWidth = self.starting_pixmap.width()
        self.targetHeight = self.starting_pixmap.height()
        # print(self.targetWidth, self.targetHeight)
        self.image_and_result_frame_vboxlayout.addWidget(self.starting_image_label)
        self.image_and_result_frame_vboxlayout.setContentsMargins(0, 0, 0, 0)

        # self.result_label = QLabel()  # Image label
        # self.result_label.setPixmap(resultPixmap)
        self.result_label = createResultLabel(result = self.currentStatus, width = self.width, labelHeight = self.labelHeight)  # Label
        self.image_and_result_frame_vboxlayout.addWidget(self.result_label)
        self.image_frame.setLayout(self.image_and_result_frame_vboxlayout)

        self.count_display = CosTheta_OK_NotOK_TotalContainer(cust_name=self.cust_name, med_name=self.med_name, name=title, width=width-5, height=int(self.labelHeight), todaysOKCount=todaysOKCount, todaysTotalCount=todaysTotalCount, labelHeight=self.labelHeight)

        # CosThetaOutcomeContainer.logger.debug(f"Creating QFrame {self.holding_frame.objectName()}")
        # Main layout for container class
        self.outcome_container_vboxlayout = QVBoxLayout()
        self.outcome_container_vboxlayout.setContentsMargins(0, 0, 0, 0)
        self.outcome_container_vboxlayout.setSpacing(0)  # No space between widgets
        self.outcome_container_vboxlayout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.outcome_container_vboxlayout.addWidget(self.title_label)
        self.outcome_container_vboxlayout.addSpacing(CosThetaOutcomeContainerWithOKNotokTotal.spacingBetweenWidgets)
        self.outcome_container_vboxlayout.addWidget(self.image_frame)
        self.outcome_container_vboxlayout.addSpacing(CosThetaOutcomeContainerWithOKNotokTotal.spacingBetweenWidgets)
        self.outcome_container_vboxlayout.addWidget(self.count_display)

        self.setLayout(self.outcome_container_vboxlayout)
        self.setMaximumSize(self.width, self.height)
        # self.setStyleSheet("background-color: rgb(255,0,0); margin:5px; border:1px solid rgb(0, 255, 0); ")
        # self.setStyleSheet("border:1px solid rgb(255, 0, 0); ")
        # CosThetaOutcomeContainer.logger.debug(f"Finished creating OutcomeContainer {self.objectName()}")
        self.update()

    def sizeHint(self):
        return QSize(self.width, self.height)

    def getCurrentStatus(self):
        return self.currentStatus

    def getCurrentPixmap(self):
        return self.currentPixmap

    def getResult(self, result):
        return self.result_label.text()

    def setResult(self, result):
        self.result_label.setText(result)
        self.currentStatus = result
        # printPlain(f"About to do comparison of result with value {result.lower()}")
        if result.lower() == CosThetaOutcomeContainerWithOKNotokTotal.okResult.lower():
            # CosThetaOutcomeContainer.logger.debug(f"Creating OK result label")
            self.result_label.setStyleSheet(okLabelStylesheet.format(
                QColor(Qt.GlobalColor.white).name()))  # Set the background and foreground color of the container's label
            self.count_display.incrementOKCount()
        elif result.lower() == CosThetaOutcomeContainerWithOKNotokTotal.notokResult.lower():
            # CosThetaOutcomeContainer.logger.debug(f"Creating Not OK result label")
            self.result_label.setStyleSheet(notokLabelStylesheet.format(
                QColor(Qt.GlobalColor.white).name()))  # Set the background and foreground color of the container's label
            self.count_display.incrementNotOKCount()
        else:
            # CosThetaOutcomeContainer.logger.debug(f"Creating label with {result}")
            self.result_label.setStyleSheet(noResultLabelStylesheet.format(
                QColor(Qt.GlobalColor.white).name()))  # Set the background and foreground color of the container's label
        self.update()

    def setImage(self, image):
        # printPlain("Before setPixmap() - 7")
        if isinstance(image, np.ndarray):
            image = cv2.resize(image, (self.targetWidth, self.targetHeight), cv2.INTER_AREA)
        pix = getPixmapImage(image)
        self.starting_image_label.setPixmap(pix)
        self.currentPixmap = pix
        # printPlain("After setPixmap() - 7")
        self.update()

    def setImageAndResult(self, image, result):
        # printBoldRed("In setImageAndResult - with ", startingImage.width, 'x', startingImage.height, result)
        # printPlain("Before setPixmap() - 8")
        self.setResult(result)
        # printPlain("After setPixmap() - 8")
        # printPlain("Before setPixmap() - 9")
        self.setImage(image)
        # self.starting_image_label.setPixmap(getPixmapImage(image))
        # printPlain("After setPixmap() - 9")
        self.update()

    def setDefault(self):
        self.setImageAndResult(self.starting_pixmap, self.default_status)

    def displayDefault(self):
        self.setDefault()

    def goToDefault(self):
        self.setDefault()

    def setTextAndResult(self, text: str, result : str):
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
        # printPlain("Reached here - 1")
        image = createImage(text, imageDimensions=(self.width - 7, self.imageHeight),
                    fontColor=QColor(Qt.GlobalColor.darkBlue), replaceChar=['=', ' '], backgroundColor=backgroundcolor)
        # printPlain("Reached here - 2")
        self.setImageAndResult(image, result)

    #
    # def setBackgroundAndResult(self, ndarrayimage, result : str, alpha = 0.8):
    #     if result.upper() == "OK":
    #         # backgroundcolor = QColor(60, 210, 45)
    #         # backgroundcolor = QColor(175, 215, 240)
    #         backgroundcolor = QColor(225, 220, 245)
    #     elif (result.upper() == "NOT OK") or (result.upper() == "NOTOK"):
    #         # backgroundcolor = QColor(210, 45, 45)
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
    #         height = self.imageHeight
    #         qPixmap = convertNDArrayToPixmap(ndarrayimage)
    #         pixmap = qPixmap.scaled(width, height, aspectMode = Qt.IgnoreAspectRatio, mode = Qt.SmoothTransformation)
    #     self.setImageAndResult(pixmap, result)

    def getImageWidth(self):
        return self.width - 7

    def getImageHeight(self):
        return self.imageHeight

