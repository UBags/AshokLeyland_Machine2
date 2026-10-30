# import multiprocessing as mp
import subprocess
import sys
import threading

from PySide6.QtWidgets import QMainWindow, QGroupBox, QHBoxLayout, QMessageBox
from PySide6 import QtCore
from PySide6.QtCore import QThread, Signal, Slot, QRunnable, QThreadPool
from PySide6.QtCore import QTimer
from PySide6.QtGui import QResizeEvent, QMouseEvent, QKeyEvent, QAction
from PySide6.QtWidgets import QSizePolicy

from costheta.camera.ONVIF_Camera import *
from costheta.devices.CameraConsumer import *
from costheta.devices.PLCServer import *
from costheta.devices.QRCodeServer import *
from costheta.devices.TorqueWrenchServer import *
from costheta.frontend.CosThetaMonitorDimensions import *
from costheta.frontend.CosThetaStylesheets import modeLabelStylesheet, okLabelStylesheet_mirrored, \
    actionAwaitedStylesheet, notokLabelStylesheet_mirrored
from costheta.frontend.widgets.CosThetaInitialSplashScreen import *
from costheta.frontend.widgets.CosThetaMidStreamSplashScreen import *
from costheta.image.processing.SplitPinAndGreaseCupProcessor import *
from costheta.usb.HIDControllerBoard import HIDUSBRelayController
from costheta.utils.Upload2GoogleDrive import *
from costheta.db.CosThetaDatabaseServer import *

app = getAppInstance()
populateMonitorDimensions()  # needs to be called to ensure that a QtGui context is there for some calls to work
CosThetaConfigurator.getInstance()  # needs to be called to ensure that configurations are properly loaded and logging is also initialised
initialiseLogging()
logger = logging.getLogger(getFullyQualifiedName(__file__))


# def printMemoryUsage():
#     memUsed = process_memory()
#     printBoldBlue(memUsed)

class KEMSFrontEnd(QMainWindow):
    labelHeight = 50
    logger = None

    qrCodeServer: QRCodeServer = None
    torqueWrenchServer: TorqueWrenchServer = None
    plcServer: PLCServer = None
    cameraConsumer: CameraConsumer = None

    STATE_START = 1
    successfullyGotQRCode = False
    STATE_QRCODE_DONE = 2
    successfullyGot60NmTorque = False
    STATE_60NM_DONE = 3
    successfullyGot60NmTorqueRotation = False
    STATE_REACHED_HOME_AFTER_60NM_ROTATION = 4
    successfullyGot30NmTorque = False
    STATE_30NM_DONE = 5
    successfullyGot30NmTorqueRotation = False
    STATE_REACHED_HOME_AFTER_30NM_ROTATION = 6
    successfullyGotSignalForVisualInspection = False
    STATE_READY_FOR_GREASECUP_AND_SPLITPIN_INSPECTION = 7
    successfullyGotVisualInspectionResult = False
    STATE_GREASECUP_AND_SPLITPIN_NOT_DONE = '8A'
    STATE_GREASECUP_AND_SPLITPIN_DONE = '8B'
    successfullyGotCapPressResult = False
    STATE_REACHED_HOME_AFTER_CAP_PRESS = 9
    currentState = STATE_START

    pictureHasBeenEvaluated = False

    currentQRCode = ""
    currentTorque60Nm = ""
    currentRotationAfter60Nm = ""
    currentTorque30Nm = ""
    currentRotationAfter30Nm = ""
    currentSplitPinAndGreaseCupResult = ""
    currentMainImageFile = ""

    currentGreaseCupResult = False
    currentGreaseCupImage = None
    successWindow = None
    pictureDisplayWindow = None

    shutdownThreads = False

    def populateLogger(self, currentClass):
        if KEMSFrontEnd.logger is None:
            KEMSFrontEnd.logger = logging.getLogger(getFullyQualifiedName(__file__, currentClass))

    def __init__(self):
        super().__init__()
        KEMSFrontEnd.populateLogger(self, __class__)
        # QThreadPool.globalInstance().setMaxThreadCount(50)
        self.initializeUI()
        splashScreenPath = get_project_root() + '/internal/images/SplashScreen.png'
        KEMSFrontEnd.logger.debug(splashScreenPath)
        splash = CosThetaInitialSplashScreen(splashScreenPath,
                                             CosThetaConfigurator.getInstance().getSplashScreenTime())
        splash.display()
        # splash.finish()
        KEMSFrontEnd.qrCodeServer = QRCodeServer()
        KEMSFrontEnd.torqueWrenchServer = TorqueWrenchServer()
        KEMSFrontEnd.plcServer = PLCServer(self)
        KEMSFrontEnd.cameraConsumer = CameraConsumer()
        karoConnection()
        self.updatePictureCountStatus()
        self.createConnectionStatusUpdateWorker()
        # self.spawnGoogleUploadProcess()
        # self.spawnGoogleUploadThread()
        # States.getInstance().sigEmergencyShutdown.connect(self.abortCurrentOperation)
        if CosThetaConfigurator.getInstance().getControlLightThroughUSB():
            self.lightRelayController = HIDUSBRelayController(controllerId=1, bus=CosThetaConfigurator.getInstance().getLightUSBRelayBus(),
                                                              address=CosThetaConfigurator.getInstance().getLightUSBRelayAddress(), doPrint=False)
        KEMSFrontEnd.logger.error(f"The project root is {get_project_root()}")
        self.createWorkers()
        self.resetAllDataPoints()
        # splash.finish()
        KEMSFrontEnd.successWindow = SuccessWindow()
        KEMSFrontEnd.pictureDisplayWindow = PictureDisplayWindow()
        self.goBackToStart()

    def createWorkers(self):
        self.createGetQRCodeWorker()
        self.createGet60NmTorqueWorker()
        self.createPost60NmRotationWorker()
        self.createGet30NmTorqueWorker()
        self.createPost30NmRotationWorker()
        self.createPictureToBeTakenWorker()
        self.createVisualInspectionWorker()
        self.createCapPressedWorker()

    def createConnectionStatusUpdateWorker(self):
        self.connStatusUpdateWorker = ConnectionStatusUpdateWorker(self)
        self.connStatusUpdateThread = QThread()
        self.connStatusUpdateThread.started.connect(self.connStatusUpdateWorker.process)
        self.connStatusUpdateWorker.doConnectionStatusUpdate.connect(self.updateAllConnectionStatus)
        self.connStatusUpdateThread.finished.connect(self.connStatusUpdateThread.deleteLater)
        self.connStatusUpdateWorker.moveToThread(self.connStatusUpdateThread)
        self.connStatusUpdateThread.start()

    # ********************************************
    def resetAllDataPoints(self):
        KEMSFrontEnd.currentQRCode = ""
        KEMSFrontEnd.currentTorque60Nm = ""
        KEMSFrontEnd.currentRotationAfter60Nm = ""
        KEMSFrontEnd.currentTorque30Nm = ""
        KEMSFrontEnd.currentRotationAfter30Nm = ""
        KEMSFrontEnd.currentTorque60Nm = ""
        KEMSFrontEnd.currentSplitPinAndGreaseCupResult = ""
        KEMSFrontEnd.currentMainImageFile = ""
        GetQRCodeWorker.seekQRCode = False
        Get60NmTorqueWorker.seek60NmTorque = False
        Post60NmRotationWorker.seek60NmRotation = False
        Get30NmTorqueWorker.seek30NmTorque = False
        Post30NmRotationWorker.seek30NmRotation = False
        PictureToBeTakenWorker.seekRequestForPicture = False
        VisualInspectionWorker.seekInspection = False
        CapPressedWorker.seekCapPress = False
        KEMSFrontEnd.pictureHasBeenEvaluated = False

    # ********************************************

    def updateInformationStatus(self, text):
        self.informationStatus.updateStatus(text)

    # ********************************************

    @Slot()
    def updateAllConnectionStatus(self):
        cameraConnection = KEMSFrontEnd.cameraConsumer.getCameraConnectionStatus()
        qrCodeConnection = KEMSFrontEnd.qrCodeServer.getQRCodeConnectionStatus()
        twConnection = KEMSFrontEnd.torqueWrenchServer.getTorqueWrenchConnectionStatus()
        plcConnection = KEMSFrontEnd.plcServer.getPLCConnectionStatus()
        self.cameraConnectionStatus.updateStatus(cameraConnection)
        self.qrcodeScannerConnectionStatus.updateStatus(qrCodeConnection)
        self.torquewrenchConnectionStatus.updateStatus(twConnection)
        self.plcConnectionStatus.updateStatus(plcConnection)

    # ********************************************
    def requestQRCode(self):
        GetQRCodeWorker.seekQRCode = True

    def createGetQRCodeWorker(self):
        self.getQRCodeWorker = GetQRCodeWorker()
        self.getQRCodeThread = QThread()
        self.getQRCodeWorker.moveToThread(self.getQRCodeThread)
        self.getQRCodeThread.started.connect(self.getQRCodeWorker.process)
        self.getQRCodeWorker.gotQRCode.connect(self.updateQRCodeDisplay)
        self.getQRCodeThread.finished.connect(self.getQRCodeThread.deleteLater)
        self.getQRCodeThread.start()

    @Slot()
    def updateQRCodeDisplay(self):
        qrCodeValue = KEMSFrontEnd.qrCodeServer.getCurrentQRCode()
        displayString = KEMSFrontEnd.qrCodeServer.getCurrentQRCodeDisplayString()
        # printBoldBlue(f"Full QRCode is {qrCodeValue}")
        # printBoldBlue(f"Display string is {displayString}")
        KEMSFrontEnd.currentQRCode = qrCodeValue
        self.resultsContainer.setQRCodeData(displayString, "OK")
        self.updateInformationStatus("Do 60Nm torque. Then, do post-60Nm rotation")
        # self.changeState(KEMSFrontEnd.STATE_QRCODE_DONE)
        self.changeState(KEMSFrontEnd.STATE_60NM_DONE)

    # ********************************************

    def request60NmTorque(self):
        Get60NmTorqueWorker.seek60NmTorque = True

    def createGet60NmTorqueWorker(self):
        self.get60NmTorqueWorker = Get60NmTorqueWorker()
        self.get60NmTorqueThread = QThread()
        self.get60NmTorqueThread.started.connect(self.get60NmTorqueWorker.process)
        self.get60NmTorqueWorker.got60NmTorque.connect(self.update60NmTorqueDisplay)
        self.get60NmTorqueThread.finished.connect(self.get60NmTorqueThread.deleteLater)
        self.get60NmTorqueWorker.moveToThread(self.get60NmTorqueThread)
        self.get60NmTorqueThread.start()

    @Slot()
    def update60NmTorqueDisplay(self):
        torqueReading = KEMSFrontEnd.torqueWrenchServer.getCurrentTorqueWrenchReading()
        displayString = KEMSFrontEnd.torqueWrenchServer.getCurrentTorqueWrenchDisplayString()
        # printBoldBlue(f'TorqueWrench Reading is {torqueReading}')
        # printBoldBlue(f'Display String is {displayString}')
        KEMSFrontEnd.currentTorque60Nm = str(torqueReading)
        self.resultsContainer.set60NmTorqueData(displayString, "OK")
        self.changeState(KEMSFrontEnd.STATE_60NM_DONE)

    # ********************************************

    def request60NmRotation(self):
        Post60NmRotationWorker.seek60NmRotation = True

    def createPost60NmRotationWorker(self):
        self.post60NmRotationWorker = Post60NmRotationWorker()
        self.post60NmRotationThread = QThread()
        self.post60NmRotationThread.started.connect(self.post60NmRotationWorker.process)
        self.post60NmRotationWorker.post60NmRotationStarted.connect(self.delayedUpdate60NmRotationDoneAndHomeReached)
        self.post60NmRotationThread.finished.connect(self.post60NmRotationThread.deleteLater)
        self.post60NmRotationWorker.moveToThread(self.post60NmRotationThread)
        self.post60NmRotationThread.start()

    @Slot()
    def delayedUpdate60NmRotationDoneAndHomeReached(self):
        self.updateInformationStatus("Post-60Nm rotation in progress")
        QTimer.singleShot(CosThetaConfigurator.getInstance().get60NmRotationDelay() * 1000, self.update60NmRotationDoneAndHomeReached)

    @Slot()
    def update60NmRotationDoneAndHomeReached(self):
        KEMSFrontEnd.currentRotationAfter60Nm = "OK"
        self.resultsContainer.setRotationAfter60NmData("Post-60Nm Rotation Done", "OK")
        self.updateInformationStatus("")
        self.changeState(KEMSFrontEnd.STATE_REACHED_HOME_AFTER_60NM_ROTATION)

    # ********************************************

    def request30NmTorque(self):
        Get30NmTorqueWorker.seek30NmTorque = True

    def createGet30NmTorqueWorker(self):
        self.get30NmTorqueWorker = Get30NmTorqueWorker()
        self.get30NmTorqueThread = QThread()
        self.get30NmTorqueThread.started.connect(self.get30NmTorqueWorker.process)
        self.get30NmTorqueWorker.got30NmTorque.connect(self.update30NmTorqueDisplay)
        self.get30NmTorqueThread.finished.connect(self.get30NmTorqueThread.deleteLater)
        self.get30NmTorqueWorker.moveToThread(self.get30NmTorqueThread)
        self.get30NmTorqueThread.start()

    @Slot()
    def update30NmTorqueDisplay(self):
        torqueReading = KEMSFrontEnd.torqueWrenchServer.getCurrentTorqueWrenchReading()
        displayString = KEMSFrontEnd.torqueWrenchServer.getCurrentTorqueWrenchDisplayString()
        print(f'TorqueWrench Reading is {torqueReading}')
        print(f'Display String is {displayString}')
        KEMSFrontEnd.currentTorque30Nm = str(torqueReading)
        self.resultsContainer.set30NmTorqueData(displayString, "OK")
        self.changeState(KEMSFrontEnd.STATE_30NM_DONE)

    # ********************************************

    def requestPost30NmRotation(self):
        Post30NmRotationWorker.seek30NmRotation = True

    def createPost30NmRotationWorker(self):
        self.post30NmRotationWorker = Post30NmRotationWorker()
        self.post30NmRotationThread = QThread()
        self.post30NmRotationThread.started.connect(self.post30NmRotationWorker.process)
        self.post30NmRotationWorker.post30NmRotationDoneAndHomeReached.connect(self.delayedUpdate30NmRotationDoneAndHomeReached)
        self.post30NmRotationThread.finished.connect(self.post30NmRotationThread.deleteLater)
        self.post30NmRotationWorker.moveToThread(self.post30NmRotationThread)
        self.post30NmRotationThread.start()

    @Slot()
    def delayedUpdate30NmRotationDoneAndHomeReached(self):
        self.updateInformationStatus("Post-30Nm rotation in progress")
        QTimer.singleShot(CosThetaConfigurator.getInstance().get30NmRotationDelay() * 1000, self.update30NmRotationDoneAndHomeReached)

    @Slot()
    def update30NmRotationDoneAndHomeReached(self):
        KEMSFrontEnd.currentRotationAfter30Nm = "OK"
        self.resultsContainer.setRotationAfter30NmData("Post-30Nm Rotation Done", "OK")
        self.changeState(KEMSFrontEnd.STATE_REACHED_HOME_AFTER_30NM_ROTATION)

    # ********************************************

    def requestPictureToBeTaken(self):
        self.updateInformationStatus("Lock split pin and take picture")
        PictureToBeTakenWorker.seekRequestForPicture = True

    def createPictureToBeTakenWorker(self):
        self.pictureToBeTakenWorker = PictureToBeTakenWorker()
        self.pictureToBeTakenThread = QThread()
        self.pictureToBeTakenThread.started.connect(self.pictureToBeTakenWorker.process)
        self.pictureToBeTakenWorker.pictureToBeTaken.connect(self.updatePictureToBeTaken)
        self.pictureToBeTakenThread.finished.connect(self.pictureToBeTakenThread.deleteLater)
        self.pictureToBeTakenWorker.moveToThread(self.pictureToBeTakenThread)
        self.pictureToBeTakenThread.start()

    @Slot()
    def updatePictureToBeTaken(self):
        self.changeState(KEMSFrontEnd.STATE_READY_FOR_GREASECUP_AND_SPLITPIN_INSPECTION)

    # ********************************************

    def requestForVisualInspection(self):
        VisualInspectionWorker.seekInspection = True

    def createVisualInspectionWorker(self):
        self.visualInspectionWorker = VisualInspectionWorker()
        self.visualInspectionThread = QThread()
        self.visualInspectionThread.started.connect(self.visualInspectionWorker.process)
        self.visualInspectionWorker.inspectionDone.connect(self.updateSplitPinAndGreaseCupDisplay)
        self.pictureToBeTakenThread.finished.connect(self.visualInspectionThread.deleteLater)
        self.visualInspectionWorker.moveToThread(self.visualInspectionThread)
        self.visualInspectionThread.start()

    # updateCounter = 0 # only for testing
    @Slot()
    def updateSplitPinAndGreaseCupDisplay(self):
        result = KEMSFrontEnd.cameraConsumer.currentVisualInspectionResult
        displayImage = KEMSFrontEnd.cameraConsumer.currentPixmapImage
        filename = KEMSFrontEnd.cameraConsumer.currentFilenameWhereImageSaved
        labelImage = KEMSFrontEnd.cameraConsumer.currentOriginalImage
        # KEMSFrontEnd.updateCounter += 1
        # if (KEMSFrontEnd.updateCounter == 1) or (KEMSFrontEnd.updateCounter == 2) or (KEMSFrontEnd.updateCounter == 3):
        #     result = "OK"
        # else:
        #     result = "Not OK"
        # if KEMSFrontEnd.updateCounter == 6:
        #     KEMSFrontEnd.updateCounter = 0
        if result.upper() == "OK":
            printBold("Result is OK")
            self.updateInformationStatus("Split Pin is OK. Do cap press.")
            try:
                if displayImage is not None:
                    self.resultsContainer.setSplitPinAndGreaseCupData(displayImage, "OK")
                else:
                    self.resultsContainer.setSplitPinAndGreaseCupData("Split Pin Inserted", "OK")
            except:
                self.resultsContainer.setSplitPinAndGreaseCupData("Split Pin Inserted", "OK")
        else:
            printBold("Result is Not OK")
            try:
                if displayImage is not None:
                    self.resultsContainer.setSplitPinAndGreaseCupData(displayImage, "Not OK")
                else:
                    self.resultsContainer.setSplitPinAndGreaseCupData("Split Pin Inserted", "Not OK")
            except:
                self.resultsContainer.setSplitPinAndGreaseCupData("Split Pin Inserted", "Not OK")
        if labelImage is not None:
            KEMSFrontEnd.pictureDisplayWindow.setPixmap(labelImage)
            # KEMSFrontEnd.pictureDisplayWindow.show()
        if result.upper() == "OK":
            self.changeState(KEMSFrontEnd.STATE_GREASECUP_AND_SPLITPIN_DONE)
            KEMSFrontEnd.currentSplitPinAndGreaseCupResult = "OK"
            KEMSFrontEnd.currentMainImageFile = filename
        else:
            self.changeState(KEMSFrontEnd.STATE_REACHED_HOME_AFTER_30NM_ROTATION)

    # ********************************************

    def requestCapPress(self):
        CapPressedWorker.seekCapPress = True

    def createCapPressedWorker(self):
        self.capPressedWorker = CapPressedWorker()
        self.capPressedThread = QThread()
        self.capPressedThread.started.connect(self.capPressedWorker.process)
        self.capPressedWorker.capPressed.connect(self.delayedUpdateAfterCapPressed)
        self.capPressedThread.finished.connect(self.capPressedThread.deleteLater)
        self.capPressedWorker.moveToThread(self.capPressedThread)
        self.capPressedThread.start()

    @Slot()
    def delayedUpdateAfterCapPressed(self):
        self.updateInformationStatus("Cap Pressing is in progress")
        QTimer.singleShot(CosThetaConfigurator.getInstance().getCapPressDelay() * 1000, self.updateAfterCapPressed)

    @Slot()
    def updateAfterCapPressed(self):
        KEMSFrontEnd.successWindow.show()
        QTimer.singleShot(6000, self.hideSuccessWindow)
        self.updateInformationStatus("Check Hub Rotation Torque. Then, scan QR Code of next component.")
        self.changeState(KEMSFrontEnd.STATE_REACHED_HOME_AFTER_CAP_PRESS)

    @Slot()
    def hideSuccessWindow(self):
        KEMSFrontEnd.successWindow.hide()

    # ********************************************

    def changeState(self, newState):
        KEMSFrontEnd.currentState = newState
        self.doCurrentStateActivity()

    def doCurrentStateActivity(self):
        if (KEMSFrontEnd.currentState == KEMSFrontEnd.STATE_START):
            self.requestQRCode()
        elif (KEMSFrontEnd.currentState == KEMSFrontEnd.STATE_QRCODE_DONE):
            self.request60NmTorque()
        elif (KEMSFrontEnd.currentState == KEMSFrontEnd.STATE_60NM_DONE):
            self.request60NmRotation()
        elif (KEMSFrontEnd.currentState == KEMSFrontEnd.STATE_REACHED_HOME_AFTER_60NM_ROTATION):
            self.request30NmTorque()
        elif (KEMSFrontEnd.currentState == KEMSFrontEnd.STATE_30NM_DONE):
            self.requestPost30NmRotation()
        elif (KEMSFrontEnd.currentState == KEMSFrontEnd.STATE_REACHED_HOME_AFTER_30NM_ROTATION):
            self.requestPictureToBeTaken()
        elif (KEMSFrontEnd.currentState == KEMSFrontEnd.STATE_READY_FOR_GREASECUP_AND_SPLITPIN_INSPECTION):
            self.requestForVisualInspection()
        elif (KEMSFrontEnd.currentState == KEMSFrontEnd.STATE_GREASECUP_AND_SPLITPIN_DONE):
            self.requestCapPress()
        elif (KEMSFrontEnd.currentState == KEMSFrontEnd.STATE_REACHED_HOME_AFTER_CAP_PRESS):
            self.insertDataInDatabase()
            self.goBackToStart()
            self.updatePictureCountStatus()

    def insertDataInDatabase(self):
        insertData(KEMSFrontEnd.currentQRCode, KEMSFrontEnd.currentTorque60Nm, KEMSFrontEnd.currentRotationAfter60Nm,
                   KEMSFrontEnd.currentTorque30Nm, KEMSFrontEnd.currentRotationAfter30Nm,
                   KEMSFrontEnd.currentSplitPinAndGreaseCupResult, KEMSFrontEnd.currentMainImageFile)
        self.resetAllDataPoints()

    def goBackToStart(self):
        printBoldBlue("Entered going back to start")
        self.resultsContainer.setInitialDefaultDisplay()
        self.changeState(KEMSFrontEnd.STATE_START)

    # ********************************************

    def initializeUI(self):
        """Initialize the window and display its contents to the screen."""
        KEMSFrontEnd.logger.debug(f"Monitor width is {getMonitorWidth()} and monitor height is {getMonitorHeight()}")
        self._width = getMonitorWidth()
        self._height = getMonitorHeight()
        self.setMinimumSize(self._width, self._height)
        KEMSFrontEnd.logger.info(f"Got monitor width as {self._width} and monitor height as {self._height}")
        self.setWindowFlags(Qt.WindowCloseButtonHint | Qt.WindowMaximizeButtonHint)
        self.setGeometry(0, 0, getMonitorWidth(), getMonitorHeight())
        self.showMaximized()
        self.setWindowTitle('Axle Wheel - QR Code, Torque, Torque Rotations, Split Pin')
        self.setupMenu()
        self.setupWidgets()
        KEMSFrontEnd.logger.info(f"Created menu and widgets")
        self.show()
        # self.memoryProfilingThread = threading.Thread(target=self.printMemoryUsage(), args=())
        # self.memoryProfilingThread.start()

    def setupWidgets(self):
        self.splashScreenPath = get_project_root() + '/internal/images/SplashScreen.png'
        KEMSFrontEnd.logger.debug(self.splashScreenPath)
        self._preConnectionMidstreamSplash = CosThetaMidstreamSplashScreen(path=self.splashScreenPath, message='Reconnecting and reinitialising camera.<br />Be patient and please wait...!',
                                                                           delayTime=max(CosThetaConfigurator.getInstance().getTimeTakenByCameraToRespondAfterPowerIsRestored(), 5))
        self._postConnectionMidstreamSplash = CosThetaMidstreamSplashScreen(path=self.splashScreenPath, message='Checking connection...and evaluating camera functionality.<br />Please be patient...!',
                                                                            delayTime=max(CosThetaConfigurator.getInstance().getSplashScreenTime() - CosThetaConfigurator.getInstance().getTimeTakenByCameraToRespondAfterPowerIsRestored(), 5))
        self.modeText = CosThetaConfigurator.getInstance().getApplicationMode() + " - Kunckle, Disc & Hub Sub-Assembly"
        # self._cameraConnectionText = "Camera {} is {}"
        labelFontSize = 10
        labelFont = QFont('Courier', pointSize=labelFontSize, weight=QFont.Weight.Bold)
        metrics = QFontMetrics(labelFont)
        labelTextRect = metrics.boundingRect(0, 0, 0, 0,
                                             Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextExpandTabs,
                                             self.modeText)
        textWidth = labelTextRect.width()
        textHeight = labelTextRect.height()
        reductionFactor = min(super().geometry().width() * 1.0 / textWidth,
                              KEMSFrontEnd.labelHeight * 1.0 / textHeight) * 0.90
        fontSize = int(reductionFactor * labelFontSize)
        labelFont.setPointSize(fontSize)

        self._mode_label = QLabel(self.modeText)  # Container's title
        self._mode_label.setFont(labelFont)
        self._mode_label.resize(self._width, KEMSFrontEnd.labelHeight)
        self._mode_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._mode_label.setStyleSheet(modeLabelStylesheet.format(
            QColor(Qt.GlobalColor.white).name()))  # Set the background and foreground color of the container's label
        self._mode_label.setFixedHeight(KEMSFrontEnd.labelHeight)
        self._mode_label.setFixedWidth(self._width)
        self._mode_label.setMinimumSize(QSize(self._width, KEMSFrontEnd.labelHeight))
        self._mode_label.setScaledContents(True)
        KEMSFrontEnd.logger.info(f"Created and resized mode label {self.modeText}")

        self._statusFont = QFont('Courier', pointSize=int(fontSize * 0.9), weight=QFont.Weight.Bold)
        # self._statusFont.setWeight(QFont.Weight.Bold)
        # self._statusFont.setStyle(QFont.StyleItalic)
        separatorWidth = 30
        # printBoldBlue(f"self.width is {self.width}")
        self.cameraConnectionStatus = ToggleMessageContainer(okText =f"Camera connected",
                                                             notokText = f"Camera not connected",
                                                             okStyleSheet = okLabelStylesheet, notokStyleSheet = notokLabelStylesheet,
                                                             font = self._statusFont, okforegroundColor = QColor(Qt.GlobalColor.white).name(),
                                                             notokforegroundColor = QColor(Qt.GlobalColor.white).name(), minimumWidth = int((self._width - 3 * separatorWidth) / 4), name='ConnectionStatus_Camera')
        self.qrcodeScannerConnectionStatus = ToggleMessageContainer(okText =f"QR Scanner connected",
                                                                    notokText = f"QR Scanner not connected",
                                                                    okStyleSheet = okLabelStylesheet, notokStyleSheet = notokLabelStylesheet,
                                                                    font = self._statusFont, okforegroundColor = QColor(Qt.GlobalColor.white).name(),
                                                                    notokforegroundColor = QColor(Qt.GlobalColor.white).name(), minimumWidth = int((self._width - 3 * separatorWidth) / 4), name='ConnectionStatus_QRCode')
        self.torquewrenchConnectionStatus = ToggleMessageContainer(okText =f"TorqueWrench connected",
                                                                   notokText = f"TorqueWrench not connected",
                                                                   okStyleSheet = okLabelStylesheet, notokStyleSheet = notokLabelStylesheet,
                                                                   font = self._statusFont, okforegroundColor = QColor(Qt.GlobalColor.white).name(),
                                                                   notokforegroundColor = QColor(Qt.GlobalColor.white).name(), minimumWidth = int((self._width - 3 * separatorWidth) / 4), name='ConnectionStatus_TorqueWrench')
        self.plcConnectionStatus = ToggleMessageContainer(okText =f"PLC connected",
                                                          notokText = f"PLC not connected",
                                                          okStyleSheet = okLabelStylesheet, notokStyleSheet = notokLabelStylesheet,
                                                          font = self._statusFont, okforegroundColor = QColor(Qt.GlobalColor.white).name(),
                                                          notokforegroundColor = QColor(Qt.GlobalColor.white).name(), minimumWidth = int((self._width - 3 * separatorWidth) / 4), name='ConnectionStatus_PLC')
        # self._pictureStatus = ToggleMessageContainer(okText =f"Picture successfully taken",
        #                                              notokText = f"Awaiting next picture",
        #                                              okStyleSheet = okLabelStylesheet, notokStyleSheet = actionAwaitedStylesheet,
        #                                              font = self._statusFont, okforegroundColor = QColor(Qt.GlobalColor.white).name(),
        #                                              notokforegroundColor = QColor(Qt.GlobalColor.black).name(), minimumWidth = int((self._width - separatorWidth) / 2), name='PictureStatus')
        self.cameraConnectionStatus.setMinimumHeight(KEMSFrontEnd.labelHeight)
        self.qrcodeScannerConnectionStatus.setMinimumHeight(KEMSFrontEnd.labelHeight)
        self.torquewrenchConnectionStatus.setMinimumHeight(KEMSFrontEnd.labelHeight)
        self.plcConnectionStatus.setMinimumHeight(KEMSFrontEnd.labelHeight)
        # self._pictureStatus.setMinimumHeight(KEMSFrontEnd.labelHeight)

        self._appExited = False

        results = QGroupBox("CONNECTION STATUS OF DEVICES")
        results_layout = QHBoxLayout()
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(0)  # No space between widgets
        results_layout.addWidget(self.qrcodeScannerConnectionStatus)
        results_layout.addWidget(CosThetaQVLine())
        results_layout.addWidget(self.torquewrenchConnectionStatus)
        results_layout.addWidget(CosThetaQVLine())
        results_layout.addWidget(self.cameraConnectionStatus)
        results_layout.addWidget(CosThetaQVLine())
        results_layout.addWidget(self.plcConnectionStatus)
        # results_layout.addSpacing(10)
        # results_layout.addStretch()
        # results_layout.addWidget(CosThetaQVLine())
        # results_layout.addStretch()
        # results_layout.addSpacing(10)
        # results_layout.addWidget(self._pictureStatus)
        results.setLayout(results_layout)

        self.resultsContainer = CosThetaResultsContainer()
        # self._imageLabel = CosThetaPixmapLabel()

        self._pictureCountFont = QFont('Courier', pointSize=int(fontSize * 0.75), weight=QFont.Weight.Bold)
        self._pictureCountText = 'Components handled on {} = {}'
        self.pictureCountStatus = SimpleMessageContainer(text = self._pictureCountText.format(datetime.now().strftime('%d-%m-%Y'), 0),
                                                         styleSheet = outcomeLabelStylesheet,
                                                         font = self._pictureCountFont, foregroundColor = QColor(Qt.GlobalColor.white).name(), minimumWidth = int((self._width - separatorWidth) / 2), name='PictureCountStatus')
        self.pictureCountStatus.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.pictureCountStatus.setMinimumHeight(KEMSFrontEnd.labelHeight)

        self._actionStatusFont = QFont('Courier', pointSize=int(fontSize * 0.45), weight=QFont.Weight.Bold)
        self.informationStatus = SimpleMessageContainer(text = "Scan QR Code",
            styleSheet = modeLabelStylesheet.format(QColor(Qt.GlobalColor.white).name()),
            font = QFont('Courier', pointSize=int(fontSize * 0.5), weight=QFont.Weight.Bold), foregroundColor = QColor(Qt.GlobalColor.white).name(), minimumWidth = int((self._width - separatorWidth) / 2), name='InformationStatus')
        self.informationStatus.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.informationStatus.setWordWrap(True)
        self.informationStatus.setMinimumHeight(KEMSFrontEnd.labelHeight)

        actions = QGroupBox("DATA / INFORMATION PANEL")
        actions_layout = QHBoxLayout()
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(0)  # No space between widgets
        # results_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter) # Kept, as illustration of what needs to be AVOIDED !!
        actions_layout.addWidget(self.pictureCountStatus)
        # results_layout.addSpacing(10)
        # results_layout.addStretch()
        actions_layout.addWidget(CosThetaQVLine())
        # results_layout.addStretch()
        # results_layout.addSpacing(10)
        actions_layout.addWidget(self.informationStatus)
        actions.setLayout(actions_layout)

        cMode = CosThetaConfigurator.getInstance().getApplicationMode()
        user_gb = QGroupBox(f"IN {cMode.upper()} MODE")
        overall_layout = QVBoxLayout()
        overall_layout.setContentsMargins(0, 0, 0, 0)
        overall_layout.setSpacing(0)  # No space between widgets
        overall_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        overall_layout.addWidget(self._mode_label)
        overall_layout.addWidget(CosThetaQHLine())
        overall_layout.addWidget(results)
        overall_layout.addWidget(CosThetaQHLine())
        # overall_layout.addWidget(self._imageLabel)
        overall_layout.addWidget(self.resultsContainer)
        overall_layout.addWidget(CosThetaQHLine())
        overall_layout.addWidget(actions)
        user_gb.setLayout(overall_layout)
        self.setCentralWidget(user_gb)
        KEMSFrontEnd.logger.debug("About to update MAIN WINDOW in setupWidgets()")
        self.update()

    def setupMenu(self):
        """Set up menubar."""
        # Create actions for mode menu
        exit_act = QAction(' Exit ', self)
        exit_act.setShortcut(' Ctrl+Alt+E ')
        exit_act.triggered.connect(self.goToClose)

        info_act = QAction(' About ', self)
        info_act.setShortcut(' Ctrl+Alt+A ')
        info_act.triggered.connect(self.goToInfo)

        # Create menubar
        menu_bar = self.menuBar()
        # For MacOS users, places menu bar in main window
        menu_bar.setNativeMenuBar(False)

        # Create mode menu and add actions
        mode_menu = menu_bar.addMenu(' Mode ')
        mode_menu.addAction(exit_act)

        info_menu = menu_bar.addMenu(' Information ')
        info_menu.addAction(info_act)
        self.update()

    # def mousePressEvent(self, mouseEvent : QMouseEvent):
    #     # if self._consumeAllKeyAndMouseEvents:
    #     #     mouseEvent.accept()
    #     # else:
    #     # printBoldBlue("Got mouse click")
    #     self.takePicture()

    # def keyPressEvent(self, keyEvent : QKeyEvent):
    #     # if self._consumeAllKeyAndMouseEvents:
    #     #     keyEvent.accept()
    #     # else:
    #     isModifier = (keyEvent.modifiers() != Qt.NoModifier)
    #     # if keyEvent.modifiers() not in (Qt.AltModifier, Qt.CTRL, Qt.SHIFT, Qt.META, Qt.MetaModifier, Qt.ShiftModifier, Qt.ControlModifier, Qt.Modifier):
    #     if not isModifier:
    #         keyPressed = keyEvent.text()
    #         key = keyEvent.key()
    #         if keyPressed.isalnum() or keyPressed.isspace() or key == Qt.Key_Enter or key == Qt.Key_Return:
    #             self.takePicture()
    #         else:
    #             keyEvent.ignore()
    #     else:
    #         keyEvent.ignore()

    # def takePicture(self):
    #     if not self._canTakePicture:
    #         KEMSFrontEnd.logger.debug(f"Cannot takePicture() as _canTakePicture is {self._canTakePicture}")
    #         return None
    #     acquiredSemaphore = self._pictureSemaphore.acquire(timeout = 0.1)
    #     KEMSFrontEnd.logger.debug("Entered takePicture()")
    #     if not acquiredSemaphore:
    #         KEMSFrontEnd.logger.debug("Could not acquire pictureSemaphore. Hence, exiting takePicture()")
    #         return None
    #     else:
    #         self._canTakePicture = False
    #         KEMSFrontEnd.logger.debug("Acquired pictureSemaphore.")
    #         KEMSFrontEnd.logger.debug("About to reset state of the light to True")
    #         if CosThetaConfigurator.getInstance().getControlLightThroughUSB():
    #             if not hasattr(self, 'lightRelayController'):
    #                 self.lightRelayController = HIDUSBRelayController(controllerId=1,
    #                                                                   bus=CosThetaConfigurator.getInstance().getLightUSBRelayBus(),
    #                                                                   address=CosThetaConfigurator.getInstance().getLightUSBRelayAddress(),
    #                                                                   doPrint=False)
    #             self.lightRelayController.setState(CosThetaConfigurator.getInstance().getLightUSBRelayNumberForLight(),
    #                                                True)
    #         time.sleep(CosThetaConfigurator.getInstance().getLightUSBTimeperiodToKeepRelayAtOnPosition())
    #         image, saveFilename = self._camera.takeNextPicture()
    #         self._pictureSemaphore.release()
    #         KEMSFrontEnd.logger.debug("Released pictureSemaphore after taking picture.")
    #         if image is not None:
    #             KEMSFrontEnd.logger.debug("Got image in takePicture().")
    #             # self.updatePictureTakenStatus()
    #             self.updatePictureCountStatus(self._camera.getCount())
    #             # KEMSFrontEnd.logger.debug(f'The image is {image}')
    #             # KEMSFrontEnd.logger.debug(f'The image type is {type(image)}')
    #             # self.updateImageLabel(image)
    #             KEMSFrontEnd.logger.debug("Updated label with image.")
    #         else:
    #             # self.updatePictureTakenStatus(False)
    #             KEMSFrontEnd.logger.debug("No image was returned by takeNextPicture()")
    #
    #         KEMSFrontEnd.logger.debug("About to reset state of the light to False")
    #         if CosThetaConfigurator.getInstance().getControlLightThroughUSB():
    #             if not hasattr(self, 'lightRelayController'):
    #                 self.lightRelayController = HIDUSBRelayController(controllerId=1,
    #                                                                   bus=CosThetaConfigurator.getInstance().getLightUSBRelayBus(),
    #                                                                   address=CosThetaConfigurator.getInstance().getLightUSBRelayAddress(),
    #                                                                   doPrint=False)
    #             self.lightRelayController.setState(CosThetaConfigurator.getInstance().getLightUSBRelayNumberForLight(), False)
    #     self.update()

    # def takeNext5Pictures(self):
    #     printBoldBlue("Inside take next 5 pictures")
    #     # try:
    #     #     if CosThetaConfigurator.getInstance().getControlLightThroughUSB():
    #     #         if not hasattr(self, 'lightRelayController'):
    #     #             self.lightRelayController = HIDUSBRelayController(controllerId=1,
    #     #                                                               bus=CosThetaConfigurator.getInstance().getLightUSBRelayBus(),
    #     #                                                               address=CosThetaConfigurator.getInstance().getLightUSBRelayAddress(),
    #     #                                                               doPrint=False)
    #     #         self.lightRelayController.setState(CosThetaConfigurator.getInstance().getLightUSBRelayNumberForLight(),
    #     #                                            True)
    #     # except:
    #     #     pass
    #     # time.sleep(CosThetaConfigurator.getInstance().getLightUSBTimeperiodToKeepRelayAtOnPosition())
    #     fiveImages = self._camera.takeNext3Pictures()
    #     printBoldBlue("Got 5 pictures and back into KEMSFrontEnd")
    #     # try:
    #     #     if CosThetaConfigurator.getInstance().getControlLightThroughUSB():
    #     #         if not hasattr(self, 'lightRelayController'):
    #     #             s133
    #     #             elf.lightRelayController = HIDUSBRelayController(controllerId=1,
    #     #                                                               bus=CosThetaConfigurator.getInstance().getLightUSBRelayBus(),
    #     #                                                               address=CosThetaConfigurator.getInstance().getLightUSBRelayAddress(),
    #     #                                                               doPrint=False)
    #     #         self.lightRelayController.setState(CosThetaConfigurator.getInstance().getLightUSBRelayNumberForLight(), False)
    #     # except:
    #     #     pass
    #     # self.update()
    #     printBoldBlue("About to return 5 images")
    #     return fiveImages

    def updatePictureCountStatus(self):
        self.pictureCountStatus.updateStatus(self._pictureCountText.format(datetime.now().strftime('%d-%m-%Y'),
                                                                           getTodaysRecordCount()))

    # def updateImageLabel(self, image):
    #     self._imageLabel.setImage(image)

    # def updatePictureTakenToAwaitedStatus(self):
    #     self.resetCanTakePicture()
    #     self._pictureStatus.updateStatus(False)

    # def updatePictureTakenStatus(self, status = True):
    #     # printMemoryUsage()
    #     self._pictureStatus.updateStatus(status)
    #     if status:
    #         self.timer = QTimer()
    #         self.timer.setInterval(CosThetaConfigurator.getInstance().getMinimumDelayBetweenPictures() * 1000)
    #         self.timer.timeout.connect(self.updatePictureTakenToAwaitedStatus)
    #         self.timer.start()
    #     else:
    #         self.resetCanTakePicture()

    def showReconnectDialog(self, message, title="Warning - Action Needed"):
        button = QMessageBox.critical(
            self,
            title,
            '<b><i>' + str(message) + '</i></b>',
            buttons=QMessageBox.Yes,
            defaultButton=QMessageBox.Yes,
        )
        return button

    # def createWaitDialog(self):
    #     self.dialog = QMessageBox(self)
    #     self.dialog.setWindowTitle("Warning!")
    #     self.dialog.setText("Establishing re-connection with the camera. Please wait for camera to get ready.")
    #     self.dialog.setStandardButtons(QMessageBox.Yes)
    #     self.dialog.setIcon(QMessageBox.Warning)
    #     button = self.dialog.exec_()
    #     if button == QMessageBox.Yes:
    #         print("Yes!")
    #     else:
    #         print("No!")
    #     self.dialog.show()
    #
    # def closeWaitDialog(self):
    #     self.dialog.close()

    # def waitForPreConnectCameraInitialisation(self):
    #     # self.setWindowFlag(QtCore.Qt.WindowCloseButtonHint, False)
    #     self._preConnectionMidstreamSplash.display()
    #     self._preConnectionMidstreamSplash.finish(self)
    #     # self.setWindowFlag(QtCore.Qt.WindowCloseButtonHint, True)

    # def waitForPostConnectCameraInitialisation(self):
    #     # self.setWindowFlag(QtCore.Qt.WindowCloseButtonHint, False)
    #     self._postConnectionMidstreamSplash.display()
    #     self._postConnectionMidstreamSplash.finish(self)
    #     # self.setWindowFlag(QtCore.Qt.WindowCloseButtonHint, True)

    # def abortCurrentOperation(self):
    #     States.getInstance().sigQRCodeAbort.emit()
    #     States.getInstance().sigTorqueWrenchAbort.emit()
    #     States.getInstance().sigPLCAbort.emit()
    #     # KEMSFrontEnd.qrCodeConsumer.abort()
    #     # KEMSFrontEnd.torquewrenchConsumer.abort()
    #     # KEMSFrontEnd.plcConsumer.abort()
    #     self.insertDataInDatabase()
    #     self.goBackToStart()

    # def resetCanTakePicture(self):
    #     self._canTakePicture = True

    def flagExiting(self):
        self._appExited = True

    def isExitFlagSet(self):
        return self._appExited

    def goToClose(self):
        KEMSFrontEnd.logger.debug("Entered goToClose()")
        ret = QMessageBox.question(self, 'Close Application', "Are you sure you want to exit the application ?",
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret == QMessageBox.No:
            return
        elif ret == QMessageBox.Yes:
            logging.shutdown()
            self.flagExiting()
            # self.googleUpdateThread.stopThread()
            # self.shutDownGoogleUploadProcess()
            self.close()

    def goToInfo(self):
        KEMSFrontEnd.logger.debug("Entered goToInfo()")
        QMessageBox.question(self, 'Information', "This application is built by CosTheta Technologies Pvt Ltd. " +
                             "All copyright belongs to CosTheta Technologies Pvt Ltd.\n\n" +
                             "This application does quality inspection and recording of \n" +
                             "a) insertion and locking of the split pin\n" +
                             "b) torque value applied to tighten the split pin, and \n" +
                             "c) rotations applied after each of 2 stages of torquing, and \n" +
                             "d) scanning and recording of QR code for Industry 4.0 requirements\n", QMessageBox.Ok)
        return

    def closeEvent(self, event):
        sp = event.spontaneous()
        KEMSFrontEnd.logger.debug("Entered closeEvent()")
        if sp:
            event.ignore()
            self.goToClose()
        else:
            event.accept()
            KEMSFrontEnd.qrCodeServer.shutdown()
            KEMSFrontEnd.torqueWrenchServer.shutdown()
            KEMSFrontEnd.plcServer.shutdown()
            KEMSFrontEnd.cameraConsumer.shutdown()
            self.close()
            logging.shutdown()
            self.flagExiting()
            # self.googleUpdateThread.stopThread()
            time.sleep(10)
            sys.exit(0)


# ***********************************************

class PictureDisplayWindow(QWidget):
    """
    This "window" is a QWidget. If it has no parent, it
    will appear as a free-floating window as we want.
    """
    def __init__(self):
        super().__init__()
        left = (getMonitorWidth() * 1) // 6
        top =  (getMonitorHeight() * 1) // 6
        width = (getMonitorWidth() * 2) // 3
        height = (getMonitorHeight() * 2) // 3
        self.setFixedWidth(width)
        self.setFixedHeight(height)
        self.setGeometry(left, top, width, height)
        layout = QVBoxLayout()
        self.label = QLabel()
        layout.addWidget(self.label)
        self.setLayout(layout)
        self.setWindowFlags(Qt.FramelessWindowHint)

    def setPixmap(self, pixmap):
        self.label.setPixmap(pixmap)

# ***********************************************

class SuccessWindow(QWidget):
    """
    This "window" is a QWidget. If it has no parent, it
    will appear as a free-floating window as we want.
    """
    def __init__(self):
        super().__init__()
        left = (getMonitorWidth() * 2) // 6
        top =  (getMonitorHeight() * 2) // 6
        width = (getMonitorWidth() * 1) // 3
        height = (getMonitorHeight() * 1) // 3
        self.setFixedWidth(width)
        self.setFixedHeight(height)
        self.setGeometry(left, top, width, height)
        layout = QVBoxLayout()
        self.label = QLabel()
        anImage = createImage(text="Operation complete", imageDimensions=((getMonitorWidth() // 3) - 5, (getMonitorHeight() // 3) - 5),
                                        fontColor=QColor(15, 15, 15), replaceChar=[' ',' '], backgroundColor = QColor(125, 210, 185))
        self.successImage = getPixmapImage(anImage)
        layout.addWidget(self.label)
        self.setLayout(layout)
        self.label.setPixmap(self.successImage)
        self.setWindowFlags(Qt.FramelessWindowHint)

    def setPixmap(self, pixmap):
        self.label.setPixmap(pixmap)

# ***********************************************

class ToggleMessageContainer(QWidget):

    def __init__(self, okText, notokText, okStyleSheet, notokStyleSheet, font, okforegroundColor, notokforegroundColor, minimumWidth, name = '') :
        super().__init__()

        super().resize(minimumWidth, KEMSFrontEnd.labelHeight)
        self._okText = okText
        self._notokText = notokText
        self._okStyleSheet = okStyleSheet
        self._notokStyleSheet = notokStyleSheet
        self._okforegroundColor = okforegroundColor
        self._notokforegroundColor = notokforegroundColor
        self._status = True
        self._minimumWidth = minimumWidth
        self.setMinimumWidth(self._minimumWidth)
        self.setObjectName(f"ToggleMessageContainer_{name}")

        labelFont = font
        okTextLen = len(okText)
        notokTextLen = len(notokText)
        if okTextLen > notokTextLen:
            sampleText = okText + "    "
        else:
            sampleText = notokText + "    "
        labelFontSize = labelFont.pointSize()
        metrics = QFontMetrics(labelFont)
        labelTextRect = metrics.boundingRect(0, 0, 0, 0,
                                             Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextExpandTabs,
                                             sampleText)
        textWidth = labelTextRect.width()
        textHeight = labelTextRect.height()
        reductionFactor = min(super().geometry().width() * 1.0 / textWidth,
                              KEMSFrontEnd.labelHeight * 1.0 / textHeight) * 0.90
        fontSize = int(reductionFactor * labelFontSize)
        labelFont.setPointSize(fontSize)
        self._font = labelFont

        self._container_label = QLabel(self._notokText) # Container's title
        self._container_label.setStyleSheet(self._notokStyleSheet.format(self._notokforegroundColor))
        self._container_label.setFont(self._font)
        self._container_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        container_frame = QFrame() # Main container to hold the object
        container_frame.setObjectName(f"ToggleMessage_ContainerFrame_{name}")

        frame_v_box = QVBoxLayout()
        frame_v_box.insertWidget(-1, self._container_label)
        frame_v_box.setSpacing(0)
        frame_v_box.setAlignment(Qt.AlignmentFlag.AlignTop)
        frame_v_box.setContentsMargins(0, 0, 0, 0)
        container_frame.setLayout(frame_v_box)

        message_v_box = QVBoxLayout()
        message_v_box.insertWidget(-1, container_frame)
        message_v_box.setSpacing(0)
        message_v_box.setAlignment(Qt.AlignmentFlag.AlignTop)
        message_v_box.setContentsMargins(0, 0, 0, 0)
        self.setLayout(message_v_box)

    def updateStatus(self, currentStatus : bool):
        self._status = currentStatus
        if self._status:
            self._container_label.setText(self._okText)
            self._container_label.setStyleSheet(self._okStyleSheet.format(self._okforegroundColor))
        else:
            self._container_label.setText(self._notokText)
            self._container_label.setStyleSheet(self._notokStyleSheet.format(self._notokforegroundColor))
        KEMSFrontEnd.logger.debug(f"About to update self widget in ToggleMessageContainer.updateStatus() in {super().objectName()}")
        self.update()

    def setFont(self, newFont):
        self._font = newFont
        self._container_label.setFont(newFont)

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

# ***********************************************


class SimpleMessageContainer(QWidget):

    def __init__(self, text, styleSheet, font, foregroundColor, minimumWidth, name = '') :

        super().__init__()
        self._text = text
        self._styleSheet = styleSheet
        self._font = font
        self._foregroundColor = foregroundColor
        self._minimumWidth = minimumWidth
        self.setMinimumWidth(self._minimumWidth)
        self.setObjectName(f"SimpleMessageContainer_{name}")

        self._container_label = QLabel(self._text) # Container's title
        self._container_label.setStyleSheet(self._styleSheet.format(self._foregroundColor))
        self._container_label.setFont(self._font)
        self._container_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        container_frame = QFrame() # Main container to hold the object
        container_frame.setObjectName(f"SimpleMessage_ContainerFrame_{name}")

        frame_v_box = QVBoxLayout()
        frame_v_box.insertWidget(-1, self._container_label)
        frame_v_box.setSpacing(0)
        frame_v_box.setAlignment(Qt.AlignmentFlag.AlignTop)
        frame_v_box.setContentsMargins(0, 0, 0, 0)
        container_frame.setLayout(frame_v_box)

        message_v_box = QVBoxLayout()
        message_v_box.insertWidget(-1, container_frame)
        message_v_box.setSpacing(0)
        message_v_box.setAlignment(Qt.AlignmentFlag.AlignTop)
        message_v_box.setContentsMargins(0, 0, 0, 0)
        self.setLayout(message_v_box)

    def updateStatus(self, currentStatus : str):
        self._text = currentStatus
        self._container_label.setText(self._text)
        KEMSFrontEnd.logger.debug(
            f"About to update self widget in SimpleMessageContainer.updateStatus() in {super().objectName()}")
        self.update()

    def setFont(self, newFont):
        self._font = newFont
        self._container_label.setFont(newFont)

    def setAlignment(self, newAlignment):
        self._container_label.setAlignment(newAlignment)

    def setWordWrap(self, value = True):
        self._container_label.setWordWrap(value)

    def setMinimumHeight(self, minHeight):
        self._container_label.setMinimumHeight(minHeight)

# ***********************************************


class CosThetaQHLine(QFrame):
    def __init__(self):
        super(CosThetaQHLine, self).__init__()
        self.setFixedHeight(20)
        self.setFrameShape(QFrame.HLine)
        self.setFrameShadow(QFrame.Sunken)

# ***********************************************


class CosThetaQVLine(QFrame):
    def __init__(self):
        super(CosThetaQVLine, self).__init__()
        self.setFixedWidth(20)
        self.setFrameShape(QFrame.VLine)
        self.setFrameShadow(QFrame.Sunken)

# ***********************************************


class CosThetaPixmapLabel(QLabel):

    logger = None

    def populateLogger(cls):
        if CosThetaPixmapLabel.logger is None:
            CosThetaPixmapLabel.logger = logging.getLogger(getFullyQualifiedName(__file__, cls))

    def __init__(self, max_enlargement=2.0):
        super(CosThetaPixmapLabel, self).__init__()
        CosThetaPixmapLabel.populateLogger(__class__)
        self.max_enlargement = max_enlargement
        self.pixmapWidth: int = 1
        self.pixmapHeight: int = 1
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(1, 1)
        self.__image = None

    def setImage(self, image) -> None:
        pixmapImage = getPixmapImage(image)
        # print("Before setPixmap() - 1")
        if pixmapImage:
            super(CosThetaPixmapLabel, self).setPixmap(pixmapImage)
            self.__image = QPixmap.toImage(pixmapImage)
            # print("After setPixmap() - 1")
            self.pixmapWidth = pixmapImage.width()
            self.pixmapHeight = pixmapImage.height()
            self.resize(self.sizeHint())
            self.update()

    def sizeHint(self):
        if self.__image:
            return self.__image.size() * self.max_enlargement
        else:
            return QSize(1, 1)

    def resizeEvent(self, a0: QResizeEvent) -> None:
        currPixmap = self.pixmap()
        if currPixmap is not None:
            scaled = currPixmap.scaled(a0.size(), Qt.KeepAspectRatio)
            self.setPixmap(scaled)
        super(CosThetaPixmapLabel, self).resizeEvent(a0)

# ***********************************************

class ConnectionStatusUpdateWorker(QObject):

    doConnectionStatusUpdate = Signal()

    def __init__(self, parent):
        super(ConnectionStatusUpdateWorker, self).__init__()
        self._parent = parent

    @Slot()
    def process(self):

        while not self._parent._appExited:
            self.doConnectionStatusUpdate.emit()
            # if self._parent.checkCameraConnection():
            try:
                time.sleep(CosThetaConfigurator.getInstance().getConnectionStatusSleepInterval())
            except:
                pass
            # else:
            #     time.sleep(max(CosThetaConfigurator.getInstance().getConnectionRetryIntervalIfDisconnected(),
            #                    CosThetaConfigurator.getInstance().getIntervalBetweenConnectionRetries() *
            #                    CosThetaConfigurator.getInstance().getConsecutiveConnectionRetries() + 2))

# ***********************************************

class GetQRCodeWorker(QObject):

    gotQRCode = Signal()
    seekQRCode = False

    def __init__(self):
        super(GetQRCodeWorker, self).__init__()

    @Slot()
    def process(self):
        while not KEMSFrontEnd.shutdownThreads:
            if (KEMSFrontEnd.currentState == KEMSFrontEnd.STATE_START) and (GetQRCodeWorker.seekQRCode):
                KEMSFrontEnd.qrCodeServer.getQRCode()
                self.gotQRCode.emit()
                GetQRCodeWorker.seekQRCode = False
            try:
                time.sleep(5)
            except:
                pass

# ***********************************************

class Get60NmTorqueWorker(QObject):

    got60NmTorque = Signal()
    seek60NmTorque = False

    def __init__(self):
        super(Get60NmTorqueWorker, self).__init__()

    @Slot()
    def process(self):
        while not KEMSFrontEnd.shutdownThreads:
            if (KEMSFrontEnd.currentState == KEMSFrontEnd.STATE_QRCODE_DONE) and (Get60NmTorqueWorker.seek60NmTorque):
                KEMSFrontEnd.torqueWrenchServer.getTorqueWrench60NmReading()
                self.got60NmTorque.emit()
                Get60NmTorqueWorker.seek60NmTorque = False
            try:
                time.sleep(3)
            except:
                pass


# ***********************************************

class Post60NmRotationWorker(QObject):

    post60NmRotationStarted = Signal()
    seek60NmRotation = False

    def __init__(self):
        super(Post60NmRotationWorker, self).__init__()

    @Slot()
    def process(self):
        while not KEMSFrontEnd.shutdownThreads:
            if (KEMSFrontEnd.currentState == KEMSFrontEnd.STATE_60NM_DONE) and (Post60NmRotationWorker.seek60NmRotation):
                KEMSFrontEnd.plcServer.sendOK()
                KEMSFrontEnd.plcServer.sendQRCodeAnd60NmOK()
                started = KEMSFrontEnd.plcServer.has60NmRotationStarted()
                if started:
                    # printBoldBlue("Emitting post60NmHomeReached")
                    self.post60NmRotationStarted.emit()
                    Post60NmRotationWorker.seek60NmRotation = False
            try:
                time.sleep(3)
            except:
                pass

# ***********************************************

class Get30NmTorqueWorker(QObject):

    got30NmTorque = Signal()
    seek30NmTorque = False

    def __init__(self):
        super(Get30NmTorqueWorker, self).__init__()

    @Slot()
    def process(self):
        while not KEMSFrontEnd.shutdownThreads:
            if (KEMSFrontEnd.currentState == KEMSFrontEnd.STATE_REACHED_HOME_AFTER_60NM_ROTATION) and (Get30NmTorqueWorker.seek30NmTorque):
                KEMSFrontEnd.torqueWrenchServer.getTorqueWrench30NmReading()
                # self._parent.torqueWrenchServer._setTorqueWrenchValues(30.1, "30.1")
                self.got30NmTorque.emit()
                Get30NmTorqueWorker.seek30NmTorque = False
            try:
                time.sleep(2)
            except:
                pass

# ***********************************************

class Post30NmRotationWorker(QObject):

    post30NmRotationDoneAndHomeReached = Signal()
    seek30NmRotation = False

    def __init__(self):
        super(Post30NmRotationWorker, self).__init__()

    @Slot()
    def process(self):
        while not KEMSFrontEnd.shutdownThreads:
            if (KEMSFrontEnd.currentState == KEMSFrontEnd.STATE_30NM_DONE) and (Post30NmRotationWorker.seek30NmRotation):
                KEMSFrontEnd.plcServer.sendOK()
                KEMSFrontEnd.plcServer.send30NmOK()
                started = KEMSFrontEnd.plcServer.hasPost30NmRotationStarted()
                if started:
                    self.post30NmRotationDoneAndHomeReached.emit()
                    Post30NmRotationWorker.seek30NmRotation = False
            try:
                time.sleep(3)
            except:
                pass

# ***********************************************

class PictureToBeTakenWorker(QObject):

    pictureToBeTaken = Signal()
    seekRequestForPicture = False

    def __init__(self):
        super(PictureToBeTakenWorker, self).__init__()

    @Slot()
    def process(self):
        while not KEMSFrontEnd.shutdownThreads:
            if (KEMSFrontEnd.currentState == KEMSFrontEnd.STATE_REACHED_HOME_AFTER_30NM_ROTATION) and (PictureToBeTakenWorker.seekRequestForPicture):
                if KEMSFrontEnd.pictureHasBeenEvaluated:
                    KEMSFrontEnd.plcServer.sendNotOK()
                buttonPressed = KEMSFrontEnd.plcServer.doesComponentNeedToBeCheckedForGreaseCupAndSplitPin()
                if buttonPressed:
                    self.pictureToBeTaken.emit()
                    PictureToBeTakenWorker.seekRequestForPicture = False
                    KEMSFrontEnd.pictureHasBeenEvaluated = True
            try:
                time.sleep(0.5)
            except:
                pass

# ***********************************************

# class RepeatPictureToBeTakenWorker(QObject):
#
#     repeatPictureToBeTaken = Signal()
#     seekRequestForPicture = False
#
#     def __init__(self):
#         super(RepeatPictureToBeTakenWorker, self).__init__()
#
#     @Slot()
#     def process(self):
#         while not KEMSFrontEnd.shutdownThreads:
#             if (KEMSFrontEnd.currentState == KEMSFrontEnd.STATE_REACHED_HOME_AFTER_30NM_ROTATION) and (PictureToBeTakenWorker.seekRequestForPicture):
#                 buttonPressed = KEMSFrontEnd.plcServer.doesComponentNeedToBeCheckedForGreaseCupAndSplitPin()
#                 if buttonPressed:
#                     self.pictureToBeTaken.emit()
#                     PictureToBeTakenWorker.seekRequestForPicture = False
#             try:
#                 time.sleep(0.5)
#             except:
#                 pass

# ***********************************************

class VisualInspectionWorker(QObject):

    inspectionDone = Signal()
    seekInspection = False

    def __init__(self):
        super(VisualInspectionWorker, self).__init__()

    @Slot()
    def process(self):
        while not KEMSFrontEnd.shutdownThreads:
            if (KEMSFrontEnd.currentState == KEMSFrontEnd.STATE_READY_FOR_GREASECUP_AND_SPLITPIN_INSPECTION) and (VisualInspectionWorker.seekInspection):
                if KEMSFrontEnd.pictureDisplayWindow.isVisible():
                    # KEMSFrontEnd.pictureDisplayWindow.hide()
                    pass
                printBoldBlue("Started visual inspection")
                inspectionResult = KEMSFrontEnd.cameraConsumer.sendRequestAndGetResponse()
                VisualInspectionWorker.seekInspection = False
                self.inspectionDone.emit()
            try:
                time.sleep(0.25)
            except:
                pass

# ***********************************************

class CapPressedWorker(QObject):

    capPressed = Signal()
    seekCapPress = False

    def __init__(self):
        super(CapPressedWorker, self).__init__()

    @Slot()
    def process(self):
        while not KEMSFrontEnd.shutdownThreads:
            if (KEMSFrontEnd.currentState == KEMSFrontEnd.STATE_GREASECUP_AND_SPLITPIN_DONE) and (CapPressedWorker.seekCapPress):
                KEMSFrontEnd.plcServer.sendCotterPinAndGreaseCupOK()
                try:
                    time.sleep(0.1)
                except:
                    pass
                KEMSFrontEnd.plcServer.sendOK()
                try:
                    time.sleep(0.1)
                except:
                    pass
                KEMSFrontEnd.plcServer.sendSwitchOffLight()
                if KEMSFrontEnd.cameraConsumer.currentOriginalImage is not None:
                    # KEMSFrontEnd.pictureDisplayWindow.show()
                    # QTimer.singleShot(3000, self.hidePictureDisplayWindow)
                    pass
                capPressed = KEMSFrontEnd.plcServer.hasGreaseCapPressingStarted()
                if capPressed:
                    self.capPressed.emit()
                CapPressedWorker.seekCapPress = False
            try:
                time.sleep(3)
            except:
                pass

    @Slot()
    def hidePictureDisplayWindow(self):
        if KEMSFrontEnd.pictureDisplayWindow.isVisible():
            KEMSFrontEnd.pictureDisplayWindow.hide()
        pass

# ***********************************************

def start():
    try:
        # anImage = createImage(text="Something", fontColor=QColor(64, 192, 64))
        logger.info("Sta    rted the main program")
        window = KEMSFrontEnd()
        app.exec()
        app.deleteLater()
        sys.exit()
    except Exception as e:
        logger.exception(e)

# ***********************************************

if __name__ == '__main__' :
    start()


