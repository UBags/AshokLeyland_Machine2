from utils.SuppressedExceptions import noteSuppressed   # logs exceptions that are caught and ignored
from datetime import datetime
import time
from typing import Union, Optional

import numpy as np
from numpy import ndarray

# PySide6 imports - QtCore
from PySide6.QtCore import (
    Qt,
    QObject,
    QEvent,
    QTimer,
    QThread,
    Signal,
    Slot,
)

# PySide6 imports - QtWidgets
from PySide6.QtWidgets import (
    QComboBox,
    QApplication,
    QMainWindow,
    QWidget,
    QLabel,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QGroupBox,
    QFrame,
    QSpacerItem,
    QSizePolicy, QMessageBox,
)

# PySide6 imports - QtGui
from PySide6.QtGui import (
    QColor,
    QPixmap,
    QImage,
    QFont,
    QFontMetrics, QAction, QKeyEvent,
)

from redis import Redis

from BaseUtils import getFullyQualifiedName
# From Configuration
from Configuration import (
    CosThetaConfigurator,
    get_project_root,
)

from frontend.CosThetaMonitorDimensions import (
    getAppInstance,
    populateMonitorDimensions
)

# From frontend widgets
from frontend.widgets.CosThetaSeparators import CosThetaQVLine
from frontend.CosThetaStylesheets import (
    modeLabelStylesheet,
    modeLabelStylesheet_with_no_qr_code,
    modeLabelStylesheet_with_qr_code,
    okLabelStylesheet, connectionOkLabelStylesheet,
    notokLabelStylesheet, outcomeLabelStylesheet, companyNameStylesheet, BYPASSED_TEXT,
)
from frontend.widgets.CosThetaInitialSplashScreen import CosThetaInitialSplashScreen
from frontend.widgets.CosThetaExitingSplashScreen import CosThetaExitingSplashScreen
from frontend.SimplePopups import (
    # NOTE: This import appears to be unused - verify and remove if not needed
    changePasswordForSelf, createAccount, changePassword, changeRole, activateUser, inactivateUser,
    createProductionReportByModelName, createProductionReportByModelNameAndDateLimits,
    createProductionReportOfTodayByModelName, createProductionReportByDateLimits, createProductionReportOfToday,
    getConfirmation, getJustPassword, showErrorMessage, updateMachineSettingsDialog, canChangeMachineSettings)
from frontend.frontendutils.CosThetaLabelUtils import largestFittingPointSize
from frontend.frontendutils.FrontEndLogger import (
    # NOTE: This import appears to be unused - verify and remove if not needed
    sendStopToAll)
from frontend.newwidgets.CosThetaResultsContainer import CosThetaResultsContainer
from frontend.FrontendI18n import (installIndicFonts, tr, trf, N_, bind, addListener, useSavedLanguage, setLanguage, currentLanguage,
                                   loadNote, LANGUAGES)
from frontend.FrontendNotices import translateNotice
from frontend.newwidgets.CosThetaSimpleMessageContainer import SimpleMessageContainer
from frontend.newwidgets.ToggleMessageContainer import ToggleMessageContainer

# From utils.RedisUtils
from utils.RedisUtils import (readCycleSettingsForQRCode, readFrontendNotices, readIOServerStates, readIOServerStateSnapshot, lastReportedRedisStatus, ok, notok, ALIVE, DEAD, sendHeartbeatFromFEServerToHeartbeatServer, sendDataFromFEServerToDatabaseServer, readDataInFEServerFromQRCodeServer, readDataInFEServerFromCameraServer, readDataInFEServerFromIOServer, readEmergencyAbortInFEServerFromIOServer, clearKeyCommunicationQueuesOnAbort, sendAbortFromFEServerToIOServer, sendFrontendAbortRequestFromFEServerToIOServer, getPostgresDatetimeFromString, sendAbortFromIOServerToQRCodeServer, blankWhiteImage, readHeartbeatsInFEServerFromHeartbeatServer)

# From persistence (setDatabaseName is defined here, not in RedisUtils)
from persistence.Persistence import (
    setDatabaseName,
    getComponentsProducedToday,
    PersistentDBConnection,
    getProductionCounts,
    getComponentsProducedThisWeek,
    getComponentsProducedThisMonth,
    getDatabaseName,
)

# From utils.CosThetaPrintUtils
from utils.StateSync import StateSyncMonitor     # state sync phase A (CHANGES 108): logging only
from logutils.SlaveLoggers import logBoth
from utils.CosThetaPrintUtils import (printBoldBlue, printBlue, printBoldRed, printBoldGreen, printYellow, printGreen, printRed, getCurrentTime)

from frontend.frontendutils.CosThetaImageUtils import (
    createImage,
    getPixmapImage,
    resize_pixmap
)

# From statemachine
from statemachine.StateMachine import (
    MachineState,
    MachineStateMachine,
)

app = getAppInstance()
installIndicFonts(('Courier', 'Segoe UI', CosThetaConfigurator.getInstance().getFontFace(), app.font().family()))
populateMonitorDimensions()  # needs to be called to ensure that a QtGui context is there for some calls to work

class AshokLeylandFrontEnd(QMainWindow):

    shutdownThreads = False
    logSource : str = getFullyQualifiedName(__file__)
    windowTitle = CosThetaConfigurator.getInstance().getWindowTitle()

    processUpdationRedisConnection: Union[Redis, None] = None
    processUpdationRedisConnected: bool = False
    connectionsUpdationRedisConnection: Union[Redis, None] = None
    connectionsUpdationRedisConnected: bool = False
    emergencyUpdationRedisConnection: Union[Redis, None] = None
    emergencyUpdationRedisConnected: bool = False
    heartbeatRedisConnection: Union[Redis, None] = None
    # no status for 3 heartbeat intervals -> all shown as down
    STATUS_STALE_AFTER_INTERVALS: int = CosThetaConfigurator.getTuning("frontend.status.stale.after.intervals", 3, minimum=1)
    lastConnectionStatusReceivedAt: float = 0.0     # time.monotonic() of the last status message
    heartbeatRedisConnected: bool = False
    databaseUpdationRedisConnection: Union[Redis, None] = None
    databaseUpdationRedisConnected: bool = False

    # connection status of the 4 connections
    qrCodeServerConnectionStatus : bool = False
    cameraServerConnectionStatus : bool = False
    ioServerConnectionStatus : bool = False
    dbServerConnectionStatus : bool = False

    # current state of the front-end
    machineState : MachineStateMachine = MachineStateMachine()

    # Variables for storing results
    defaultTime = "1970-01-01 00:00:00"

    currentQRCode: Union[str, None] = None
    currentQRCodeForDisplay: Union[str, None] = None
    currentComponentAssemblyStartDatetime: str = defaultTime

    originalKnuckleCheckImage: Union[np.ndarray, None] = None
    processedKnuckleCheckImage: Union[np.ndarray, None] = None
    currentKnuckleCheckResult: Union[str, None] = None
    currentKnuckleCheckDatetime: str = defaultTime

    originalHubAndBottomBearingCheckImage: Union[np.ndarray, None] = None
    processedHubAndBottomBearingCheckImage: Union[np.ndarray, None] = None
    currentHubAndBottomBearingCheckResult: Union[str, None] = None
    currentHubAndBottomBearingCheckDatetime: str = defaultTime

    originalTopBearingCheckImage: Union[np.ndarray, None] = None
    processedTopBearingCheckImage: Union[np.ndarray, None] = None
    currentTopBearingCheckResult: Union[str, None] = None
    currentTopBearingCheckDatetime: str = defaultTime

    originalNutAndPlateWasherCheckImage: Union[np.ndarray, None] = None
    processedNutAndPlateWasherCheckImage: Union[np.ndarray, None] = None
    currentNutAndPlateWasherCheckResult: Union[str, None] = None
    currentNutAndPlateWasherCheckDatetime: str = defaultTime

    # Machine Setting "Bypass Component Press and Second Tightening" in force for this component: the IOServer
    # answered Torque 1 with moveToState = the split pin picture (states 13-19 skipped)
    bypassedPressAndTightening2: bool = False

    currentTighteningTorque1: float = 0.0
    currentTighteningTorque1CheckResult: str = notok
    currentTighteningTorque1Datetime: str = defaultTime
    currentTighteningTorque1RotationDone: Optional[bool] = None     # PLC_PC_Station2RotationDuringTighteningDone


    originalBungForComponentPressCheckImage: Union[np.ndarray, None] = None
    processedBungForComponentPressCheckImage: Union[np.ndarray, None] = None
    currentBungForComponentPressCheckResult: Union[str, None] = None
    currentBungForComponentPressCheckDatetime: str = defaultTime

    currentComponentPressDone: str = notok
    currentComponentPressDatetime: str = defaultTime

    originalNoBungAfterComponentPressCheckImage: Union[np.ndarray, None] = None
    processedNoBungAfterComponentPressCheckImage: Union[np.ndarray, None] = None
    currentNoBungAfterComponentPressCheckResult: Union[str, None] = None
    currentNoBungAfterComponentPressCheckDatetime: str = defaultTime

    currentTighteningTorque2: float = 0.0
    currentTighteningTorque2CheckResult: str = notok
    currentTighteningTorque2Datetime: str = defaultTime
    currentTighteningTorque2RotationDone: Optional[bool] = None

    originalSplitPinAndWasherCheckImage: Union[np.ndarray, None] = None
    processedSplitPinAndWasherCheckImage: Union[np.ndarray, None] = None
    currentSplitPinAndWasherCheckResult: Union[str, None] = None
    currentSplitPinAndWasherCheckDatetime: str = defaultTime

    originalCapCheckImage: Union[np.ndarray, None] = None
    processedCapCheckImage: Union[np.ndarray, None] = None
    currentCapCheckResult: Union[str, None] = None
    currentCapCheckDatetime: str = defaultTime

    originalBungForCapPressCheckImage: Union[np.ndarray, None] = None
    processedBungForCapPressCheckImage: Union[np.ndarray, None] = None
    currentBungForCapPressCheckResult: Union[str, None] = None
    currentBungForCapPressCheckDatetime: str = defaultTime

    currentCapPressDone: str = notok
    currentCapPressDatetime: str = defaultTime

    currentRotationTorque1: float = 0.0
    currentRotationTorque1CheckResult: str = notok
    currentRotationTorque1Datetime: str = defaultTime

    # Variable for storing final result
    finalResult: str = notok

    emergencyButtonPressed : bool = False
    # an aborted cycle's tiles are cleared this soon after the "Aborted" box (as before CHANGES 123; user, 2026-11-17);
    # a completed cycle's stay frontend.free.rotation.torque.display.sec (5 s)
    ABORT_TILES_CLEAR_MSEC : int = CosThetaConfigurator.getTuning("frontend.abort.tiles.clear.msec", 750, minimum=0)
    abortReason : str = ""                  # why the cycle was aborted (emergency button / manual abort), for the record
    # the frontend's abort (Ctrl+Alt+Z): password frontend.abort.password in the properties file, read at each use

    # Variables for determining how much time to show large version of ok and notok images
    TIME_TO_SHOW_OK_IMAGE_MSEC : int = int(CosThetaConfigurator.getInstance().getTimeToDisplayOkImages() * 1000)
    TIME_TO_SHOW_NOT_OK_IMAGE_MSEC : int = int(CosThetaConfigurator.getInstance().getTimeToDisplayNotOkImages() * 1000)

    # Variable for storing general remarks
    remarks : str = ""
    CURRENT_WIDTH : int = 10
    CURRENT_HEIGHT: int = 10
    RESULTS_CONTAINER_TOP : int = 10

    def __init__(self, mode : str = 'Test', username : str = 'default', role : str = 'Operator'):
        super().__init__()
        from logutils.LogSettings import setLogProcess
        setLogProcess("frontend")         # per-server log level, crash capture, live configuration

        self.mode = mode
        setDatabaseName(mode=self.mode, createDB=False)
        self.username = username
        self.role = role
        AshokLeylandFrontEnd.logSource = getFullyQualifiedName(__file__, __class__)
        self.hostname = CosThetaConfigurator.getInstance().getRedisHost()
        self.port = CosThetaConfigurator.getInstance().getRedisPort()
        self.timeStarted = datetime.now()
        self.currentComponentStartTime = datetime.now()
        # set when the cycle ends (free-rotation torque, or an abort): the processing time then stays on screen,
        # frozen, until the next QR code is scanned (it showed 0 again at once)
        self.currentComponentEndTime = None
        self.componentTimeShown = False         # a cycle has started since the frontend started
        # end of a cycle (CHANGES 123): the record is sent and the next QR code armed at once, but the finished
        # cycle's values and pictures stay in the tiles for frontend.free.rotation.torque.display.sec; this is the
        # number of the clear still to come (None: nothing pending). A newer number, or a new QR code, cancels it.
        self.pendingTilesClear = None
        self.tilesClearCounter = 0
        self.timeStartedAsString = datetime.now().strftime("%H:%M:%S")

        # the main screen's language: the one chosen last time (Internal/frontend_language.txt; CHANGES 156)
        language = useSavedLanguage()
        printBlue(f"Frontend language: {language} - {loadNote(language)}")
        try:
            self.labelHeight = CosThetaConfigurator.getInstance().getLabelInitialHeight()
            self.initializeUI()
            self.splashScreenPath = f"{get_project_root()}/internalimages/SplashScreen.png"
            splash = CosThetaInitialSplashScreen(self.splashScreenPath,
                                                 CosThetaConfigurator.getInstance().getSplashScreenTime())
            splash.display()
            self.connectToRedis()
            self.showMaximized()
            QApplication.processEvents()
            self.resultsContainer.fitInstructionFont(self.machineState.getAllInstructions())
            self.calculateAndSetIdealFontForModeLabel()
            self.calculateAndSetIdealFontForQRCodeLabel()
            self.calculateAndSetIdealFontForDateTimeLabel()
            self.calculateAndSetIdealFontForCreatorLabel()
            self.calculateAndSetIdealFontForConnectionStatus()
            self.resultsContainer.calculateAndSetIdealFontsForTitlesAndResults()
            self.setInitialInstruction()
            self.setQRCodeContainerDisplay(displayString="")
            self.createWorkers()
            self.resetAllDataPoints()
            self.requestQRCode()
            AshokLeylandFrontEnd.CURRENT_WIDTH = self.width()
            AshokLeylandFrontEnd.CURRENT_HEIGHT = self.height()
            AshokLeylandFrontEnd.RESULTS_CONTAINER_TOP = self.resultsContainer.geometry().top()
            self.largeImageWindow: LargeImageWindow = LargeImageWindow(self)
            self.largeImageWindow.hide()
            self.installEventFilter(self)  # Add event filter for key press events
            self.update()
            printBoldBlue("*****************")
            printBlue("Started FE Server")
            printBoldBlue("*****************")
            self.setFixedWidth(AshokLeylandFrontEnd.CURRENT_WIDTH)
            addListener(self.onLanguageChanged)     # texts built at run time, and the font sizes, again
            self.setFixedHeight(AshokLeylandFrontEnd.CURRENT_HEIGHT)
        except Exception as e:
            print(e)

    def connectToRedis(self, forceRenew = False):
        if forceRenew:
            AshokLeylandFrontEnd.processUpdationRedisConnection = None
            AshokLeylandFrontEnd.processUpdationRedisConnected = False
            AshokLeylandFrontEnd.connectionsUpdationRedisConnection = None
            AshokLeylandFrontEnd.connectionsUpdationRedisConnected = False
            AshokLeylandFrontEnd.emergencyUpdationRedisConnection = None
            AshokLeylandFrontEnd.emergencyUpdationRedisConnected = False
            AshokLeylandFrontEnd.heartbeatRedisConnection = None
            AshokLeylandFrontEnd.heartbeatRedisConnected = False
            AshokLeylandFrontEnd.databaseUpdationRedisConnection = None
            AshokLeylandFrontEnd.databaseUpdationRedisConnected = False
        if not AshokLeylandFrontEnd.processUpdationRedisConnected:
            try:
                AshokLeylandFrontEnd.processUpdationRedisConnection = Redis(self.hostname, self.port, retry_on_timeout=True)
                AshokLeylandFrontEnd.processUpdationRedisConnected = True
            except Exception:
                AshokLeylandFrontEnd.processUpdationRedisConnected = False
                printBoldRed(f'Redis Connection status in frontend for getting pictures from camera server is {self.processUpdationRedisConnected}')

        if not AshokLeylandFrontEnd.connectionsUpdationRedisConnected:
            try:
                AshokLeylandFrontEnd.connectionsUpdationRedisConnection = Redis(self.hostname, self.port, retry_on_timeout=True)
                AshokLeylandFrontEnd.connectionsUpdationRedisConnected = True
            except Exception:
                AshokLeylandFrontEnd.connectionsUpdationRedisConnected = False
                printBoldRed(f'Redis Connection status in frontend for getting connection statuses from Heartbeat Server is {self.connectionsUpdationRedisConnected}')

        if not AshokLeylandFrontEnd.emergencyUpdationRedisConnected:
            try:
                AshokLeylandFrontEnd.emergencyUpdationRedisConnection = Redis(self.hostname, self.port, retry_on_timeout=True)
                AshokLeylandFrontEnd.emergencyUpdationRedisConnected = True
            except Exception:
                AshokLeylandFrontEnd.emergencyUpdationRedisConnected = False
                printBoldRed(f'Redis Connection status in frontend for getting emergency status from IO server is {self.emergencyUpdationRedisConnected}')

        if not AshokLeylandFrontEnd.heartbeatRedisConnected:
            try:
                AshokLeylandFrontEnd.heartbeatRedisConnection = Redis(self.hostname, self.port, retry_on_timeout=True)
                AshokLeylandFrontEnd.heartbeatRedisConnected = True
            except Exception:
                AshokLeylandFrontEnd.heartbeatRedisConnected = False
                printBoldRed(f'Redis Connection status for frontend heartbeat is {self.heartbeatRedisConnected}')
        if not AshokLeylandFrontEnd.databaseUpdationRedisConnected:
            try:
                AshokLeylandFrontEnd.databaseUpdationRedisConnection = Redis(self.hostname, self.port, retry_on_timeout=True)
                AshokLeylandFrontEnd.databaseUpdationRedisConnected = True
            except Exception:
                AshokLeylandFrontEnd.databaseUpdationRedisConnected = False
                printBoldRed(f'Redis Connection status in frontend for updating state is {self.databaseUpdationRedisConnected}')


    def initializeUI(self):
        """Initialize the window and display its contents to the screen."""

        bind(self, AshokLeylandFrontEnd.windowTitle, "setWindowTitle")

        self.setupWidgets()
        self.setupMenu()
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowMinMaxButtonsHint | Qt.WindowType.WindowCloseButtonHint)

    def setupWidgets(self):

        cMode = self.mode.upper()
        central_widget = QGroupBox(trf("IN {mode} MODE", mode=tr(cMode.upper())))
        self.centralGroupBox = central_widget
        self.setCentralWidget(central_widget)
        grid_layout = QGridLayout(central_widget)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setSpacing(0)  # No space between widgets

        # ********************************** START MODE GROUP BOX *********************************
        modeGroupBox = bind(QGroupBox("Mode"), "Mode", "setTitle")
        modeLayout = QHBoxLayout()
        modeLayout.setContentsMargins(0, 0, 0, 0)
        modeLayout.setSpacing(0)  # No space between widgets
        modeGroupBox.setLayout(modeLayout)

        self.modeText = trf("{mode} - Knuckle, Hub, and Disc Assembly", mode=tr(self.mode.upper()))

        self.modeLabel = QLabel(self.modeText)  # Container's title
        self.modeLabel.setContentsMargins(0,0,0,0)
        self.modeLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.modeLabel.setStyleSheet(modeLabelStylesheet.format(
            QColor(Qt.GlobalColor.white).name()))  # Set the background and foreground color of the container's label
        modeFontSize = CosThetaConfigurator.getInstance().getInitialFontsize()
        modeFont = QFont('Segoe UI', pointSize=modeFontSize, weight=QFont.Weight.Bold)
        self.modeLabel.setFont(modeFont)

        qrCodeDisplay = "QR Code : 34567 87654 90 765432 1234567 7654329876 5643219087 12346786876"
        self.qrCodeLabel = QLabel(qrCodeDisplay)  # Container's title
        self.qrCodeLabel.setContentsMargins(0,0,0,0)
        self.qrCodeLabel.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.qrCodeLabel.setStyleSheet(modeLabelStylesheet_with_no_qr_code.format(
            QColor(Qt.GlobalColor.black).name()))  # Set the background and foreground color of the container's label
        initialFontSize = CosThetaConfigurator.getInstance().getInitialFontsize()
        qrCodeLabelFont = QFont('Segoe UI', pointSize=initialFontSize, weight=QFont.Weight.Bold)
        self.qrCodeLabel.setFont(qrCodeLabelFont)

        modeLayout.addWidget(self.modeLabel, 1)
        modeLayout.addWidget(CosThetaQVLine(), 0)
        modeLayout.addWidget(self.qrCodeLabel, 1)

        # Language of the main screen (CHANGES 156): English / Kannada / Tamil / Hindi, each in its own script; the
        # choice is kept in Internal/frontend_language.txt for the next start
        self.languageSelector = QComboBox()
        for code, name in LANGUAGES:
            self.languageSelector.addItem(name, code)
        self.languageSelector.setCurrentIndex(max(0, self.languageSelector.findData(currentLanguage())))
        self.languageSelector.setFont(QFont('Segoe UI', pointSize=max(10, CosThetaConfigurator.getInstance().getInitialFontsize() - 2),
                                            weight=QFont.Weight.Bold))
        self.languageSelector.setToolTip("English / ಕನ್ನಡ / தமிழ் / हिन्दी")
        self.languageSelector.currentIndexChanged.connect(self.languageChosen)
        modeLayout.addWidget(CosThetaQVLine(), 0)
        modeLayout.addWidget(self.languageSelector, 0)

        # Notices from the IOServer / camera server (e.g. "Database down: continue till end of cycle"), top right,
        # flashing while there are any (FrontendNoticeWorker reads them; hidden when there are none)
        self.noticeLabel = QLabel("")
        self.noticeLabel.setContentsMargins(6, 0, 6, 0)
        self.noticeLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.noticeLabel.setWordWrap(True)
        self.noticeLabel.setMaximumWidth(max(420, int(AshokLeylandFrontEnd.CURRENT_WIDTH * 0.32)))
        self.noticeLabel.setFont(QFont('Segoe UI', pointSize=max(10, initialFontSize - 2), weight=QFont.Weight.Bold))
        self.noticeLabel.setVisible(False)
        self._noticeFlashOn = False
        self._noticeFlashTimer = QTimer(self)
        self._noticeFlashTimer.timeout.connect(self._flashNotice)
        modeLayout.addWidget(self.noticeLabel, 0)

        grid_layout.addWidget(modeGroupBox, 0, 0, 1, 2)

        # ********************************** END MODE GROUP BOX *********************************

        # self.setQRCodeContainerDisplay(displayString="")

        # ********************************** START CONNECTION GROUP BOX *********************************

        self._statusFont = QFont('Courier', pointSize=int(20), weight=QFont.Weight.Bold)
        self.cameraServerConnectionStatusContainer = ToggleMessageContainer(okText=N_("Camera connected"),
                                                                            notokText=N_("Camera not connected"),
                                                                            okStyleSheet = connectionOkLabelStylesheet, notokStyleSheet = notokLabelStylesheet,
                                                                            font = self._statusFont, okforegroundColor = QColor(Qt.GlobalColor.white),
                                                                            notokforegroundColor = QColor(Qt.GlobalColor.white),
                                                                            minimumWidth = 50,
                                                                            labelHeight = self.labelHeight, name='CAMERACONNECTION', forceUseOfFontSize=26)
        self.ioServerConnectionStatusContainer = ToggleMessageContainer(okText=N_("I/O Module connected"),
                                                                        notokText=N_("I/O Module not connected"),
                                                                        okStyleSheet = connectionOkLabelStylesheet, notokStyleSheet = notokLabelStylesheet,
                                                                        font = self._statusFont, okforegroundColor = QColor(Qt.GlobalColor.white),
                                                                        notokforegroundColor = QColor(Qt.GlobalColor.white),
                                                                        minimumWidth = 50,
                                                                        labelHeight = self.labelHeight, name='IOCONNECTION',
                                                                        forceUseOfFontSize=26)
        self.dbServerConnectionStatusContainer = ToggleMessageContainer(okText=N_("Database connected"),
                                                                        notokText=N_("Database not connected"),
                                                                        okStyleSheet = connectionOkLabelStylesheet, notokStyleSheet = notokLabelStylesheet,
                                                                        font = self._statusFont, okforegroundColor = QColor(Qt.GlobalColor.white),
                                                                        notokforegroundColor = QColor(Qt.GlobalColor.white),
                                                                        minimumWidth = 50,
                                                                        labelHeight = self.labelHeight, name='DBCONNECTION',
                                                                        forceUseOfFontSize=26)
        self.qrCodeServerConnectionStatusContainer = ToggleMessageContainer(okText=N_("QRCode Scanner connected"),
                                                                            notokText=N_("QRCode Scanner not connected"),
                                                                            okStyleSheet = connectionOkLabelStylesheet, notokStyleSheet = notokLabelStylesheet,
                                                                            font = self._statusFont, okforegroundColor = QColor(Qt.GlobalColor.white),
                                                                            notokforegroundColor = QColor(Qt.GlobalColor.white),
                                                                            minimumWidth = 50,
                                                                            labelHeight = self.labelHeight, name='QRCODE',
                                                                            forceUseOfFontSize=26)

        connectionStatusesGroupBox = bind(QGroupBox("CONNECTION STATUS OF DEVICES"), "CONNECTION STATUS OF DEVICES", "setTitle")
        connectionStatusLayout = QHBoxLayout()
        connectionStatusesGroupBox.setLayout(connectionStatusLayout)
        connectionStatusLayout.setContentsMargins(0, 0, 0, 0)
        connectionStatusLayout.setSpacing(0)  # No space between widgets

        connectionStatusLayout.addWidget(self.cameraServerConnectionStatusContainer, 1)
        connectionStatusLayout.addWidget(CosThetaQVLine(), 0)
        connectionStatusLayout.addWidget(self.qrCodeServerConnectionStatusContainer, 1)
        connectionStatusLayout.addWidget(CosThetaQVLine(), 0)

        connectionStatusLayout.addWidget(self.ioServerConnectionStatusContainer, 1)
        connectionStatusLayout.addWidget(CosThetaQVLine(), 0)
        connectionStatusLayout.addWidget(self.dbServerConnectionStatusContainer, 1)

        grid_layout.addWidget(connectionStatusesGroupBox, 1, 0, 1, 2)

        # ********************************** END CONNECTION GROUP BOX *********************************

        self.resultsContainer = CosThetaResultsContainer()

        grid_layout.addWidget(self.resultsContainer, 2, 0, 1, 2)

        # ********************************** START GENERAL INFO GROUP BOX *********************************

        self.dateTimeFont = QFont('Segoe UI', pointSize=int(12), weight=QFont.Weight.Bold)
        currentComponentProcessingStarted : bool = (AshokLeylandFrontEnd.currentQRCode is not None) and (AshokLeylandFrontEnd.currentQRCode != "")
        currentDatetimeText = self.dateTimeTextFor(
            0, 0, 0, (':'.join(str(datetime.now() - self.currentComponentStartTime).split(':')[:3])).split('.')[0]
            if currentComponentProcessingStarted else "0")
        # printLight(currentDatetimeText)
        self.dateTimeStatus = SimpleMessageContainer(
            text=currentDatetimeText,
            styleSheet = outcomeLabelStylesheet,
            font = self.dateTimeFont, foregroundColor = QColor(Qt.GlobalColor.white).name(),
            minimumWidth = 50, name='DateTimeStatus')
        self.dateTimeStatus.setAlignment(Qt.AlignmentFlag.AlignLeft)
        general_info_cell_frame_left = QFrame()
        general_info_layout_left = QHBoxLayout(general_info_cell_frame_left)
        general_info_layout_left.setContentsMargins(0, 0, 0, 0)
        general_info_layout_left.setSpacing(0)  # No space between widgets
        general_info_layout_left.setAlignment(Qt.AlignmentFlag.AlignCenter)
        general_info_layout_left.addWidget(self.dateTimeStatus)

        self.creatorString = tr("Created by CosTheta Technologies and Ashok Leyland")
        self.creatorFont = QFont('Segoe UI', pointSize=int(12), weight=QFont.Weight.Bold, italic=True)
        self.creatorFont.setItalic(True)
        self.creatorStatus = SimpleMessageContainer(text = self.creatorString,
                                                    styleSheet = companyNameStylesheet.format(QColor(Qt.GlobalColor.white).name()),
                                                    font = self.creatorFont, foregroundColor = QColor(Qt.GlobalColor.white).name(), minimumWidth = 50, name='Creator Status')
        self.creatorStatus.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.creatorStatus.setWordWrap(False)

        # --- Ashok Leyland logo ---
        self.ashokLeylandLogoLabel = QLabel()
        self.ashokLeylandLogoLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ashokLeylandLogoLabel.setContentsMargins(2, 1, 2, 1)
        self.ashokLeylandLogoLabel.setScaledContents(False)
        _al_logo_path = f"{get_project_root()}/internalimages/ashokleyland_logo.png"
        _al_pixmap = QPixmap(_al_logo_path)
        if not _al_pixmap.isNull():
            self.ashokLeylandLogoLabel.setPixmap(
                _al_pixmap.scaledToHeight(int(self.labelHeight * 0.85), Qt.TransformationMode.SmoothTransformation)
            )
        else:
            self.ashokLeylandLogoLabel.setText("AL")

        # --- CosTheta logo ---
        self.cosThetaLogoLabel = QLabel()
        self.cosThetaLogoLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cosThetaLogoLabel.setContentsMargins(2, 1, 2, 1)
        self.cosThetaLogoLabel.setScaledContents(False)
        _ct_logo_path = f"{get_project_root()}/internalimages/costheta_logo.png"
        _ct_pixmap = QPixmap(_ct_logo_path)
        if not _ct_pixmap.isNull():
            self.cosThetaLogoLabel.setPixmap(
                _ct_pixmap.scaledToHeight(int(self.labelHeight * 0.85), Qt.TransformationMode.SmoothTransformation)
            )
        else:
            self.cosThetaLogoLabel.setText("CT")

        general_info_cell_frame_right = QFrame()
        general_info_layout_right = QHBoxLayout(general_info_cell_frame_right)
        general_info_layout_right.setContentsMargins(0, 0, 0, 0)
        general_info_layout_right.setSpacing(4)
        general_info_layout_right.setAlignment(Qt.AlignmentFlag.AlignCenter)
        general_info_layout_right.addWidget(self.ashokLeylandLogoLabel)
        general_info_layout_right.addWidget(self.creatorStatus)
        general_info_layout_right.addWidget(self.cosThetaLogoLabel)

        generalInfoGroupBox = bind(QGroupBox("DATE / RUNNING DURATION PANEL"), "DATE / RUNNING DURATION PANEL", "setTitle")
        general_info_layout = QHBoxLayout()
        generalInfoGroupBox.setLayout(general_info_layout)
        general_info_layout.setContentsMargins(0, 0, 0, 0)
        general_info_layout.setSpacing(0)  # No space between widgets
        general_info_layout.addWidget(self.dateTimeStatus, 2)
        general_info_layout.addWidget(CosThetaQVLine(),0)
        general_info_layout.addWidget(general_info_cell_frame_right, 1)

        grid_layout.addWidget(generalInfoGroupBox, 3, 0, 1, 2)

        # ********************************** END GENERAL INFO GROUP BOX *********************************

        # ********************************** DEFINE VERTICAL SPACE OF EACH ROW *********************************

        grid_layout.setRowStretch(0, 1)  # Row 1
        grid_layout.setRowStretch(1, 1)  # Row 2
        grid_layout.setRowStretch(2, 7)  # Row 3 (7 times the stretch of others)
        grid_layout.setRowStretch(3, 1)  # Row 4
        # Add a vertical spacer to push rows and show stretch effect
        grid_layout.addItem(QSpacerItem(0, 0, QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding), 4, 0, 1, 2)

        self.update()

    # Call this after self.show(), then QApplication.processEvents(), then self.resultsContainer.calculateAndSetIdealFontForInstructionLabel()
    def calculateAndSetIdealFontForModeLabel(self):
        currentModeFont = self.modeLabel.font()
        currentFontPointSize = currentModeFont.pointSize()
        currentModeFont.setWeight(QFont.Weight.Bold)
        metrics = QFontMetrics(currentModeFont)
        labelTextRect = metrics.boundingRect(0, 0, 0, 0, Qt.AlignmentFlag.AlignLeft, f"       {self.modeLabel.text()}       ")
        textWidth = labelTextRect.width()
        textHeight = labelTextRect.height()
        modeLabelWidth = self.modeLabel.width()
        modeLabelHeight = self.modeLabel.height()
        reductionFactor = min(modeLabelWidth * 1.0 / textWidth,
                              modeLabelHeight * 1.0 / textHeight) * 0.95
        fontSize = int(reductionFactor * currentFontPointSize)
        currentModeFont.setPointSize(fontSize)
        self.modeLabel.setFont(currentModeFont)

    # Call this after self.show(), then QApplication.processEvents(), then self.resultsContainer.calculateAndSetIdealFontForInstructionLabel()
    def calculateAndSetIdealFontForQRCodeLabel(self):
        currentQRCodeFont = self.qrCodeLabel.font()
        currentFontPointSize = currentQRCodeFont.pointSize()
        currentQRCodeFont.setWeight(QFont.Weight.Bold)
        metrics = QFontMetrics(currentQRCodeFont)
        labelTextRect = metrics.boundingRect(0, 0, 0, 0, Qt.AlignmentFlag.AlignCenter, f"   {self.qrCodeLabel.text()}   ")
        textWidth = labelTextRect.width()
        textHeight = labelTextRect.height()
        qrCodeLabelWidth = self.qrCodeLabel.width()
        qrCodeLabelHeight = self.qrCodeLabel.height()
        reductionFactor = min(qrCodeLabelWidth * 1.0 / textWidth,
                              qrCodeLabelHeight * 1.0 / textHeight) * 0.95
        fontSize = int(reductionFactor * currentFontPointSize)
        currentQRCodeFont.setPointSize(fontSize)
        self.qrCodeLabel.setFont(currentQRCodeFont)

    def calculateAndSetIdealFontForDateTimeLabel(self):
        currentDateTimeFont = self.dateTimeStatus.font()
        currentFontPointSize = currentDateTimeFont.pointSize()
        currentDateTimeFont.setWeight(QFont.Weight.Bold)
        metrics = QFontMetrics(currentDateTimeFont)

        labelTextRect = metrics.boundingRect(0, 0, 0, 0, Qt.AlignmentFlag.AlignLeft, f"   {self.dateTimeStatus.getText()}   ")
        textWidth = labelTextRect.width()
        textHeight = labelTextRect.height()
        datetimeLabelWidth = self.dateTimeStatus.width()
        datetimeLabelHeight = self.dateTimeStatus.height()
        reductionFactor = min(datetimeLabelWidth * 1.0 / textWidth,
                              datetimeLabelHeight * 1.0 / textHeight) * 0.8
        fontSize = int(reductionFactor * currentFontPointSize)
        currentDateTimeFont.setPointSize(fontSize)
        self.dateTimeStatus.setFont(currentDateTimeFont)

    def calculateAndSetIdealFontForConnectionStatus(self):
        """One fixed size for the 4 connection status tiles: the largest at which the longest message,
        "QRCode Scanner not connected", fits its tile. Call after the window has been shown."""
        tiles = [self.cameraServerConnectionStatusContainer, self.qrCodeServerConnectionStatusContainer,
                 self.ioServerConnectionStatusContainer, self.dbServerConnectionStatusContainer]
        longest = max(((t, tr(text)) for t in tiles for text in (t._okText, t._notokText)), key=lambda x: len(x[1]))
        size = largestFittingPointSize(longest[0].label(), longest[1])
        for t in tiles:
            t.setFixedPointSize(size)
        return size

    def calculateAndSetIdealFontForCreatorLabel(self):
        currentCreatorFont = self.creatorStatus.font()
        currentFontPointSize = currentCreatorFont.pointSize()
        currentCreatorFont.setWeight(QFont.Weight.Bold)
        metrics = QFontMetrics(currentCreatorFont)

        labelTextRect = metrics.boundingRect(0, 0, 0, 0, Qt.AlignmentFlag.AlignLeft, f"   {self.creatorStatus.getText()}   ")
        textWidth = labelTextRect.width()
        textHeight = labelTextRect.height()
        creatorLabelWidth = self.creatorStatus.width()
        creatorLabelHeight = self.creatorStatus.height()
        reductionFactor = min(creatorLabelWidth * 1.0 / textWidth,
                              creatorLabelHeight * 1.0 / textHeight) * 0.8
        fontSize = int(reductionFactor * currentFontPointSize)
        currentCreatorFont.setPointSize(fontSize)
        self.creatorStatus.setFont(currentCreatorFont)

    def setupMenu(self):
        """Set up menubar."""
        # Create actions for mode menu
        changePassword_act = bind(QAction('Change Own Password', self), 'Change Own Password', fmt=' {} ')
        changePassword_act.setShortcut(' Ctrl+Alt+P ')
        changePassword_act.triggered.connect(lambda: self._runDialog(changePasswordForSelf, currentUser=self.username, currentRole=self.role))

        exit_act = bind(QAction('Exit', self), 'Exit', fmt=' {} ')
        exit_act.setShortcut(' Ctrl+Alt+E ')
        exit_act.triggered.connect(self.goToClose)

        addnewuser_act = bind(QAction('Add New User', self), 'Add New User', fmt=' {} ')
        addnewuser_act.setShortcut(' Ctrl+Alt+N ')
        addnewuser_act.triggered.connect(lambda: self._runDialog(createAccount, currentUser=self.username, currentRole=self.role))

        manageuserpasswords_act = bind(QAction('Manage Other Users Passwords', self), 'Manage Other Users Passwords', fmt=' {} ')
        manageuserpasswords_act.setShortcut(' Ctrl+Alt+M ')
        manageuserpasswords_act.triggered.connect(lambda: self._runDialog(changePassword, currentUser=self.username, currentRole=self.role))

        changerole_act = bind(QAction('Change User Role', self), 'Change User Role', fmt=' {} ')
        changerole_act.setShortcut(' Ctrl+Alt+R ')
        changerole_act.triggered.connect(lambda: self._runDialog(changeRole, currentUser=self.username, currentRole=self.role))

        activateuser_act = bind(QAction('Activate an Inactive User', self), 'Activate an Inactive User', fmt=' {} ')
        activateuser_act.setShortcut(' Ctrl+Alt+C ')
        activateuser_act.triggered.connect(lambda: self._runDialog(activateUser, currentUser=self.username, currentRole=self.role))

        inactivateuser_act = bind(QAction('Inactivate User', self), 'Inactivate User', fmt=' {} ')
        inactivateuser_act.setShortcut(' Ctrl+Alt+I ')
        inactivateuser_act.triggered.connect(lambda: self._runDialog(inactivateUser, currentUser=self.username, currentRole=self.role))

        createreportbymodelname_act = bind(QAction('Create Production Report By Model Name', self), 'Create Production Report By Model Name', fmt=' {} ')
        createreportbymodelname_act.setShortcut(' Ctrl+Alt+S ')
        createreportbymodelname_act.triggered.connect(lambda: self._runDialog(createProductionReportByModelName))

        createreportbymodelnameforperiod_act = bind(QAction('Create Production Report By Model Name For a Period', self), 'Create Production Report By Model Name For a Period', fmt=' {} ')
        createreportbymodelnameforperiod_act.setShortcut(' Ctrl+Alt+F ')
        createreportbymodelnameforperiod_act.triggered.connect(lambda: self._runDialog(createProductionReportByModelNameAndDateLimits))

        createreportbymodelnamefortoday_act = bind(QAction('Create Production Report By Model Name For Today', self), 'Create Production Report By Model Name For Today', fmt=' {} ')
        createreportbymodelnamefortoday_act.setShortcut(' Ctrl+Alt+A ')
        createreportbymodelnamefortoday_act.triggered.connect(lambda: self._runDialog(createProductionReportOfTodayByModelName))

        createreportforaperiodforallmodels_act = bind(QAction('Create Production Report for a Period for all Models', self), 'Create Production Report for a Period for all Models', fmt=' {} ')
        createreportforaperiodforallmodels_act.setShortcut(' Ctrl+Alt+D ')
        createreportforaperiodforallmodels_act.triggered.connect(lambda: self._runDialog(createProductionReportByDateLimits))

        createreportfortodayforallmodels_act = bind(QAction('Create Full Production Report for Today for all Models', self), 'Create Full Production Report for Today for all Models', fmt=' {} ')
        createreportfortodayforallmodels_act.setShortcut(' Ctrl+Alt+D ')
        createreportfortodayforallmodels_act.triggered.connect(lambda: self._runDialog(createProductionReportOfToday))

        info_act = bind(QAction('About', self), 'About', fmt=' {} ')
        info_act.setShortcut(' Ctrl+Alt+A ')
        info_act.triggered.connect(self.goToInfo)

        help_act = bind(QAction('Help', self), 'Help', fmt=' {} ')
        help_act.setShortcut(' Ctrl+Alt+H ')
        help_act.triggered.connect(self.goToHelp)

        # Add Manual Abort action
        manual_abort_act = bind(QAction('(Z)Manual Abort', self), '(Z)Manual Abort', fmt=' {} ')
        manual_abort_act.setShortcut('Ctrl+Alt+Z')
        manual_abort_act.triggered.connect(self.manualAbort)

        # Create menubar
        menu_bar = self.menuBar()
        # # For MacOS users, places menu bar in main window
        menu_bar.setNativeMenuBar(False)
        #
        # # Create Actions menu and add actions
        action_menu = bind(menu_bar.addMenu('Actions'), 'Actions', 'setTitle', fmt=' {} ')
        action_menu.addAction(exit_act)
        action_menu.addAction(changePassword_act)
        action_menu.addSeparator()
        action_menu.addAction(manual_abort_act)

        # Create Admin menu and add actions
        admin_menu = bind(menu_bar.addMenu('Administration'), 'Administration', 'setTitle', fmt=' {} ')
        admin_menu.addAction(addnewuser_act)
        admin_menu.addAction(manageuserpasswords_act)
        admin_menu.addAction(changerole_act)

        # Create Activate/Inactivate menu and add actions
        activate_inactivate_menu = bind(menu_bar.addMenu('Activate / Inactivate'), 'Activate / Inactivate', 'setTitle', fmt=' {} ')
        activate_inactivate_menu.addAction(activateuser_act)
        activate_inactivate_menu.addAction(inactivateuser_act)

        # Create Report menu and add actions
        send_report_menu = bind(menu_bar.addMenu('Report'), 'Report', 'setTitle', fmt=' {} ')
        send_report_menu.addAction(createreportbymodelname_act)
        send_report_menu.addAction(createreportbymodelnameforperiod_act)
        send_report_menu.addAction(createreportbymodelnamefortoday_act)
        send_report_menu.addSeparator()
        send_report_menu.addAction(createreportforaperiodforallmodels_act)
        send_report_menu.addAction(createreportfortodayforallmodels_act)
        send_report_menu.addSeparator()

        # Create Information menu and add actions
        info_menu = bind(menu_bar.addMenu('Information'), 'Information', 'setTitle', fmt=' {} ')
        info_menu.addAction(help_act)
        info_menu.addAction(info_act)

        # Machine Settings (Administrator / Supervisor): rotation RPM, torque setpoints, rotation counts written to the
        # PLC in state 2 (stored in machine_settings; used when cycle.settings.source=database)
        machine_settings_act = bind(QAction('Machine Settings', self), 'Machine Settings', fmt=' {} ')
        machine_settings_act.setShortcut(' Ctrl+Alt+U ')
        machine_settings_act.triggered.connect(lambda: self._runDialog(updateMachineSettingsDialog, currentUser=self.username, currentRole=self.role))
        machine_settings_menu = bind(menu_bar.addMenu('Machine Settings'), 'Machine Settings', 'setTitle', fmt=' {} ')
        machine_settings_menu.addAction(machine_settings_act)
        if not canChangeMachineSettings(self.role):
            machine_settings_act.setDisabled(True)

        if self.role == "Operator":
            addnewuser_act.setDisabled(True)
            manageuserpasswords_act.setDisabled(True)
            changerole_act.setDisabled(True)
            activateuser_act.setDisabled(True)
            inactivateuser_act.setDisabled(True)
        self.update()

    @Slot()
    def hideLargeImageWindow(self):
        self.largeImageWindow.hide()

    def stopAndCleanThread(self, thread : QThread):
        try:
            if thread and thread.isRunning():
                thread.quit()
                thread.wait()
                thread.deleteLater()
        except Exception:
            noteSuppressed()

    def goToClose(self):
        # print("Entered goToClose()")
        ret = QMessageBox.question(self, tr('Close Application'), tr("Are you sure you want to exit the application ?"),
                                   buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, defaultButton=QMessageBox.StandardButton.No)
        if ret == QMessageBox.StandardButton.No:
            return
        elif ret == QMessageBox.StandardButton.Yes:
            try:
                pass
            except Exception:
                noteSuppressed()

            AshokLeylandFrontEnd.shutdownThreads = True
            sendStopToAll()
            endSplash = CosThetaExitingSplashScreen(self.splashScreenPath,
                                                 CosThetaConfigurator.getInstance().getEndScreenTime())
            if self.largeImageWindow is not None:
                try:
                    self.largeImageWindow.close()
                except Exception as e:
                    printRed(f"Could not close largeImageWindow: {e}")
            endSplash.display()
            try:
                self.heartbeatWorker.stop()
                self.stopAndCleanThread(self.heartbeatThread)
            except Exception:
                noteSuppressed()
            self.stopAndCleanThread(self.timeDurationUpdateThread)
            self.stopAndCleanThread(self.getQRCodeThread)
            self.stopAndCleanThread(self.getPictureAndResultThread)
            self.stopAndCleanThread(self.getTorqueReadingAndResultThread)
            self.stopAndCleanThread(self.connectionStatusUpdateThread)
            self.stopAndCleanThread(self.emergencyAbortStatusUpdateThread)
            self.close()
        return

    def goToInfo(self):
        # print("Entered goToInfo()")
        # (the text described another CosTheta application - capsules and bottles - until CHANGES 156)
        QMessageBox.question(self, tr('Information'),
                             tr("Software Version: v 1.0\n\n"
                                "This application is built by CosTheta Technologies Pvt Ltd. "
                                "All copyright belongs to CosTheta Technologies Pvt Ltd.\n\n"
                                "This application inspects and records the assembly of the front axle knuckle, hub and "
                                "disc: every vision check, torque and press of each component, with its QR code.\n"),
                             QMessageBox.StandardButton.Ok)
        return

    def goToHelp(self):
        # print("Entered goToHelp()")
        QMessageBox.question(self, tr('Help about using the menu'),
                             tr("When any of the menu choices are being used, \n"
                                "the remaining menu items will be clickable.\n\n"
                                "However, clicking on any other menu item will not be acted upon \n"
                                "by the system till the currently-in-use menu action is completed \n"
                                "or cancelled.\n"), QMessageBox.StandardButton.Ok)
        return

    @Slot(int)
    def languageChosen(self, index: int) -> None:
        """The language dropdown of the main screen (CHANGES 156): switch and keep the choice for the next start."""
        code = self.languageSelector.itemData(index)
        if code and code != currentLanguage():
            setLanguage(code)               # bound texts change at once; onLanguageChanged does the rest
            printBlue(f"Frontend language changed to {code} - {loadNote(code)}")

    def onLanguageChanged(self) -> None:
        """Texts built at run time, shown again in the language now chosen, and the font sizes fitted to them."""
        try:
            self.centralGroupBox.setTitle(trf("IN {mode} MODE", mode=tr(self.mode.upper())))
            self.modeText = trf("{mode} - Knuckle, Hub, and Disc Assembly", mode=tr(self.mode.upper()))
            self.modeLabel.setText(self.modeText)
            self.creatorString = tr("Created by CosTheta Technologies and Ashok Leyland")
            self.creatorStatus.updateStatus(self.creatorString)
            self.setQRCodeContainerDisplay(displayString=getattr(self, "qrCodeDisplayString", ""))
            self.updateTimeDurationStatus(None)
            for tile in (self.cameraServerConnectionStatusContainer, self.qrCodeServerConnectionStatusContainer,
                         self.ioServerConnectionStatusContainer, self.dbServerConnectionStatusContainer):
                tile.retranslate()
            self.resultsContainer.retranslate()
            self.showNotices(getattr(self, "_remoteNotices", []))
            index = self.languageSelector.findData(currentLanguage())
            if index >= 0 and index != self.languageSelector.currentIndex():
                self.languageSelector.blockSignals(True)
                self.languageSelector.setCurrentIndex(index)
                self.languageSelector.blockSignals(False)
            QApplication.processEvents()
            self.calculateAndSetIdealFontForModeLabel()
            self.calculateAndSetIdealFontForDateTimeLabel()
            self.calculateAndSetIdealFontForCreatorLabel()
            self.calculateAndSetIdealFontForConnectionStatus()
            self.resultsContainer.calculateAndSetIdealFontsForTitlesAndResults()
            self.resultsContainer.fitInstructionFont(self.machineState.getAllInstructions())
        except Exception as e:
            printRed(f"Frontend: could not show the texts in the new language: {e}")

    def closeEvent(self, event):
        sp = event.spontaneous()
        if sp:
            event.ignore()
            self.goToClose()
        else:
            event.accept()
            try:
                pass
            except Exception:
                noteSuppressed()
            AshokLeylandFrontEnd.shutdownThreads = True
            sendStopToAll()
            endSplash = CosThetaExitingSplashScreen(self.splashScreenPath,
                                                 CosThetaConfigurator.getInstance().getEndScreenTime())
            if self.largeImageWindow is not None:
                try:
                    self.largeImageWindow.close()
                except Exception:
                    noteSuppressed()
            endSplash.display()
            try:
                self.heartbeatWorker.stop()
                self.stopAndCleanThread(self.heartbeatThread)
            except Exception:
                noteSuppressed()
            self.stopAndCleanThread(self.timeDurationUpdateThread)
            self.stopAndCleanThread(self.getQRCodeThread)
            self.stopAndCleanThread(self.getPictureAndResultThread)
            self.stopAndCleanThread(self.getTorqueReadingAndResultThread)
            self.stopAndCleanThread(self.connectionStatusUpdateThread)
            self.stopAndCleanThread(self.emergencyAbortStatusUpdateThread)
            self.close()

    # ********************************************

    def resetAllDataPoints(self):

        defaultTime = "1970-01-01 00:00:00"

        AshokLeylandFrontEnd.currentQRCode = None
        AshokLeylandFrontEnd.currentQRCodeForDisplay = None
        AshokLeylandFrontEnd.bypassedPressAndTightening2 = False
        AshokLeylandFrontEnd.currentComponentAssemblyStartDatetime = defaultTime

        AshokLeylandFrontEnd.originalKnuckleCheckImage = None
        AshokLeylandFrontEnd.processedKnuckleCheckImage = None
        AshokLeylandFrontEnd.currentKnuckleCheckResult = None
        AshokLeylandFrontEnd.currentKnuckleCheckDatetime = defaultTime

        AshokLeylandFrontEnd.originalHubAndBottomBearingCheckImage = None
        AshokLeylandFrontEnd.processedHubAndBottomBearingCheckImage = None
        AshokLeylandFrontEnd.currentHubAndBottomBearingCheckResult = None
        AshokLeylandFrontEnd.currentHubAndBottomBearingCheckDatetime = defaultTime

        AshokLeylandFrontEnd.originalTopBearingCheckImage = None
        AshokLeylandFrontEnd.processedTopBearingCheckImage = None
        AshokLeylandFrontEnd.currentTopBearingCheckResult = None
        AshokLeylandFrontEnd.currentTopBearingCheckDatetime = defaultTime

        AshokLeylandFrontEnd.originalNutAndPlateWasherCheckImage = None
        AshokLeylandFrontEnd.processedNutAndPlateWasherCheckImage = None
        AshokLeylandFrontEnd.currentNutAndPlateWasherCheckResult = None
        AshokLeylandFrontEnd.currentNutAndPlateWasherCheckDatetime = defaultTime

        AshokLeylandFrontEnd.currentTighteningTorque1 = 0.0
        AshokLeylandFrontEnd.currentTighteningTorque1CheckResult = notok
        AshokLeylandFrontEnd.currentTighteningTorque1Datetime = defaultTime
        AshokLeylandFrontEnd.currentTighteningTorque1RotationDone = None


        AshokLeylandFrontEnd.originalBungForComponentPressCheckImage = None
        AshokLeylandFrontEnd.processedBungForComponentPressCheckImage = None
        AshokLeylandFrontEnd.currentBungForComponentPressCheckResult = None
        AshokLeylandFrontEnd.currentBungForComponentPressCheckDatetime = defaultTime

        AshokLeylandFrontEnd.currentComponentPressDone = False
        AshokLeylandFrontEnd.currentComponentPressDatetime = defaultTime

        AshokLeylandFrontEnd.originalNoBungAfterComponentPressCheckImage = None
        AshokLeylandFrontEnd.processedNoBungAfterComponentPressCheckImage = None
        AshokLeylandFrontEnd.currentNoBungAfterComponentPressCheckResult = None
        AshokLeylandFrontEnd.currentNoBungAfterComponentPressCheckDatetime = defaultTime

        AshokLeylandFrontEnd.currentTighteningTorque2 = 0.0
        AshokLeylandFrontEnd.currentTighteningTorque2CheckResult = notok
        AshokLeylandFrontEnd.currentTighteningTorque2Datetime = defaultTime
        AshokLeylandFrontEnd.currentTighteningTorque2RotationDone = None

        AshokLeylandFrontEnd.originalSplitPinAndWasherCheckImage = None
        AshokLeylandFrontEnd.processedSplitPinAndWasherCheckImage = None
        AshokLeylandFrontEnd.currentSplitPinAndWasherCheckResult = None
        AshokLeylandFrontEnd.currentSplitPinAndWasherCheckDatetime = defaultTime

        AshokLeylandFrontEnd.originalCapCheckImage = None
        AshokLeylandFrontEnd.processedCapCheckImage = None
        AshokLeylandFrontEnd.currentCapCheckResult = None
        AshokLeylandFrontEnd.currentCapCheckDatetime = defaultTime

        AshokLeylandFrontEnd.originalBungForCapPressCheckImage = None
        AshokLeylandFrontEnd.processedBungForCapPressCheckImage = None
        AshokLeylandFrontEnd.currentBungForCapPressCheckResult = None
        AshokLeylandFrontEnd.currentBungForCapPressCheckDatetime = defaultTime

        AshokLeylandFrontEnd.currentCapPressDone = notok
        AshokLeylandFrontEnd.currentCapPressDatetime = defaultTime

        AshokLeylandFrontEnd.currentRotationTorque1 = 0.0
        AshokLeylandFrontEnd.currentRotationTorque1CheckResult = notok
        AshokLeylandFrontEnd.currentRotationTorque1Datetime = defaultTime

        # Variable for storing final result
        AshokLeylandFrontEnd.finalResult = notok

        # Variable for storing general remarks
        AshokLeylandFrontEnd.remarks = ""


    def getCurrentState(self):
        return AshokLeylandFrontEnd.machineState.getCurrentState()

    def setCurrentState(self, newState : MachineState):
        AshokLeylandFrontEnd.machineState.setCurrentState(newState=newState)
        printBoldBlue(f"The new state of the machine is {AshokLeylandFrontEnd.machineState.getCurrentState()}")

    def printStateOfAllSeekers(self):
        printGreen(f"{GetQRCodeWorker.seekQRCode = },\n {GetPictureAndResultWorker.seekPictureAndResult = },\n {GetTorqueReadingPlusCapPressAndResultWorker.seekTorqueReadingPlusCapPressAndResult = }")

    # ********************************************

    lastGuiTickAt: float = time.monotonic()     # set every second by a timer of the GUI thread (heartbeat)
    dialogsOpen: int = 0                        # FreeSimpleGUI dialogs open on the GUI thread (_runDialog)

    def _runDialog(self, dialog, *args, **kwargs):
        """Run a FreeSimpleGUI (tkinter) dialog of the menu. It runs its own event loop on the GUI thread, so the Qt
        timers - the GUI tick of the heartbeat among them - stop while it is open: counted here, the heartbeat stays
        ALIVE meanwhile (an administrator may take minutes in Machine Settings or a report; that is not a frozen
        screen, and must not abort the cycle as a frontend that is down would)."""
        AshokLeylandFrontEnd.dialogsOpen += 1
        try:
            return dialog(*args, **kwargs)
        finally:
            AshokLeylandFrontEnd.dialogsOpen -= 1
            AshokLeylandFrontEnd.lastGuiTickAt = time.monotonic()

    def _guiTick(self):
        AshokLeylandFrontEnd.lastGuiTickAt = time.monotonic()

    def createWorkers(self):
        # The heartbeat is sent from its own thread, so it went on while the GUI thread was frozen: it now says DEAD
        # when the GUI thread has not run this timer for heartbeat.work.loop.max.silence.sec (utils/WorkerHealth.py)
        AshokLeylandFrontEnd.lastGuiTickAt = time.monotonic()
        self._guiTickTimer = QTimer(self)
        self._guiTickTimer.timeout.connect(self._guiTick)
        self._guiTickTimer.start(1000)
        self.createTimeDurationUpdateWorker()
        self.createGetQRCodeWorker()
        self.createGetPictureAndResultWorker()
        self.createGetTorqueReadingPlusCapPressAndResultWorker()
        self.createConnectionStatusUpdateWorker()
        self.createEmergencyAbortUpdateWorker()
        self.createHeartbeatWorker()
        self.createFrontendNoticeWorker()
        self.createStateSyncWorker()

    # ********************************************

    def createStateSyncWorker(self):
        """State sync phase A (CHANGES 108): compares this frontend's state with the IOServer's, logs only."""
        self.stateSyncWorker = StateSyncWorker()
        self.stateSyncThread = QThread()
        self.stateSyncWorker.moveToThread(self.stateSyncThread)
        self.stateSyncThread.started.connect(self.stateSyncWorker.start)
        self.stateSyncThread.start()

    def createFrontendNoticeWorker(self):
        self.frontendNoticeWorker = FrontendNoticeWorker()
        self.frontendNoticeThread = QThread()
        self.frontendNoticeWorker.moveToThread(self.frontendNoticeThread)
        self.frontendNoticeWorker.gotNotices.connect(self.showNotices)
        self.frontendNoticeThread.started.connect(self.frontendNoticeWorker.start)
        self.frontendNoticeThread.start()

    @Slot(list)
    def showNotices(self, notices: list):
        """The notices to show now (empty: none): those read from Redis plus the frontend's own (Redis check failed,
        which cannot travel through Redis reliably). Shown together, flashing (red / white) every 0.5 s."""
        self._remoteNotices = list(notices)
        notices = list(getattr(self, "_localNotices", [])) + self._remoteNotices
        text = "\n".join(translateNotice(n) for n in notices)     # in the chosen language (CHANGES 156)
        if not notices:
            if self.noticeLabel.isVisible():
                self._noticeFlashTimer.stop()
                self.noticeLabel.setVisible(False)
                self.noticeLabel.setText("")
            return
        if text != self.noticeLabel.text():
            self.noticeLabel.setText(text)
        if not self.noticeLabel.isVisible():
            self.noticeLabel.setVisible(True)
            self._noticeFlashOn = False
            self._flashNotice()
            self._noticeFlashTimer.start(500)

    @Slot()
    def _flashNotice(self):
        self._noticeFlashOn = not self._noticeFlashOn
        if self._noticeFlashOn:
            self.noticeLabel.setStyleSheet("QLabel { background-color: #d00000; color: white; }")
        else:
            self.noticeLabel.setStyleSheet("QLabel { background-color: white; color: #d00000; }")

    # ********************************************

    def createHeartbeatWorker(self):
        self.heartbeatWorker = HeartbeatSenderWorker()
        self.heartbeatThread = QThread()
        self.heartbeatWorker.moveToThread(self.heartbeatThread)
        self.heartbeatThread.started.connect(self.heartbeatWorker.start)
        self.heartbeatThread.start()

    # ********************************************

    def createTimeDurationUpdateWorker(self):
        self.timeDurationUpdateWorker = TimeDurationUpdateWorker()
        self.timeDurationUpdateThread = QThread()
        self.timeDurationUpdateWorker.doTimeDurationUpdate.connect(self.updateTimeDurationStatus)
        self.timeDurationUpdateThread.finished.connect(self.timeDurationUpdateThread.deleteLater)
        self.timeDurationUpdateWorker.moveToThread(self.timeDurationUpdateThread)
        self.timeDurationUpdateThread.start()

    def componentProcessingTimeText(self) -> str:
        """Processing time of the current component: counting from the QR code scan; frozen at the end of the cycle
        (free-rotation torque or abort) until the next scan; 0 before the first scan."""
        if not self.componentTimeShown:
            return "0"
        end = self.currentComponentEndTime or datetime.now()
        return (':'.join(str(end - self.currentComponentStartTime).split(':')[:3])).split('.')[0]

    @Slot(object)
    def updateTimeDurationStatus(self, counts=None):
        # The production counts (today, week, month) are read by TimeDurationUpdateWorker in its own thread and come
        # with the signal (CHANGES 110): before, three database queries ran here, in the GUI thread, each on a new
        # connection - a slow or hung PostgreSQL froze the screen. None (not read) keeps the counts last shown.
        if counts is not None:
            self.productionCounts = counts
        _countToday, _countWeek, _countMonth = getattr(self, "productionCounts", (0, 0, 0))
        self.dateTimeStatus.updateStatus(self.dateTimeTextFor(_countToday, _countWeek, _countMonth,
                                                              self.componentProcessingTimeText()))

    def dateTimeTextFor(self, countToday, countWeek, countMonth, processingTime) -> str:
        """The date / running-time panel's text in the chosen language."""
        return trf("Today is {today}. Started: {started}; Running time: {running}    ; Components produced : This week - "
                   "{week}; This month - {month}\nUser: {user}; Current component processing time: {processing}    "
                   "; Components produced today - {todayCount}",
                   today=datetime.now().strftime('%d-%m-%Y %a'), started=self.timeStartedAsString,
                   running=(':'.join(str(datetime.now() - self.timeStarted).split(':')[:3])).split('.')[0],
                   week=countWeek, month=countMonth, user=f'{self.username} - {tr(self.role)}',
                   processing=processingTime, todayCount=countToday)

    # ********************************************

    def createConnectionStatusUpdateWorker(self):
        self.connectionStatusUpdateWorker = ConnectionStatusUpdateWorker()
        self.connectionStatusUpdateThread = QThread()
        self.connectionStatusUpdateWorker.doConnectionStatusUpdate.connect(self.updateConnectionStatus)
        self.connectionStatusUpdateThread.finished.connect(self.connectionStatusUpdateThread.deleteLater)
        self.connectionStatusUpdateWorker.moveToThread(self.connectionStatusUpdateThread)
        self.connectionStatusUpdateThread.start()

    @Slot()
    def updateConnectionStatus(self):
        # Runs in the GUI thread, so the read must not wait (block=None): a blocking read froze the screen for up to
        # 0.5 s each time no status message had arrived yet (as in production, which made these reads non-blocking).
        # No new message -> the tiles keep their last status; only when the heartbeat server has sent nothing for
        # STATUS_STALE_AFTER_INTERVALS of its intervals are all servers shown as down (the heartbeat server itself is gone).
        cameraServerStatus, qrCodeServerStatus, ioServerStatus, dbServerStatus, received = \
            readHeartbeatsInFEServerFromHeartbeatServer(AshokLeylandFrontEnd.connectionsUpdationRedisConnection,
                                                        block=None, withReceived=True)
        now = time.monotonic()
        if received:
            AshokLeylandFrontEnd.lastConnectionStatusReceivedAt = now
            local = [] if lastReportedRedisStatus["alive"] else ["Redis is not storing data: restart the PC"]
            if local != getattr(self, "_localNotices", []):
                self._localNotices = local
                self.showNotices(getattr(self, "_remoteNotices", []))
        else:
            staleAfter = AshokLeylandFrontEnd.STATUS_STALE_AFTER_INTERVALS * max(
                CosThetaConfigurator.getInstance().getCombinedHeartbeatConnectionStatusSleepInterval(),
                CosThetaConfigurator.getInstance().getGeneralConnectionStatusSleepInterval())
            if now - AshokLeylandFrontEnd.lastConnectionStatusReceivedAt < staleAfter:
                return
        AshokLeylandFrontEnd.cameraServerConnectionStatus = cameraServerStatus
        AshokLeylandFrontEnd.qrCodeServerConnectionStatus = qrCodeServerStatus
        AshokLeylandFrontEnd.ioServerConnectionStatus = ioServerStatus
        AshokLeylandFrontEnd.dbServerConnectionStatus = dbServerStatus
        self.cameraServerConnectionStatusContainer.updateStatus(AshokLeylandFrontEnd.cameraServerConnectionStatus)
        self.qrCodeServerConnectionStatusContainer.updateStatus(AshokLeylandFrontEnd.qrCodeServerConnectionStatus)
        self.ioServerConnectionStatusContainer.updateStatus(AshokLeylandFrontEnd.ioServerConnectionStatus)
        self.dbServerConnectionStatusContainer.updateStatus(AshokLeylandFrontEnd.dbServerConnectionStatus)

    # ********************************************

    def createEmergencyAbortUpdateWorker(self):
        self.emergencyAbortUpdateWorker = EmergencyStatusMonitoringWorker()
        self.emergencyAbortStatusUpdateThread = QThread()
        self.emergencyAbortUpdateWorker.doEmergencyPressedUpdate.connect(self.updateEmergencyAbortPressed)
        self.emergencyAbortStatusUpdateThread.finished.connect(self.emergencyAbortStatusUpdateThread.deleteLater)
        self.emergencyAbortUpdateWorker.moveToThread(self.emergencyAbortStatusUpdateThread)
        self.emergencyAbortStatusUpdateThread.start()

    @Slot()
    def updateEmergencyAbortPressed(self):
        # GUI thread: must not wait (block=None). With the default 0.5 s block the screen froze for 0.5 s at every
        # check, because normally there is no emergency message (as in production, which made this read non-blocking).
        emergencyPressed = readEmergencyAbortInFEServerFromIOServer(AshokLeylandFrontEnd.emergencyUpdationRedisConnection,
                                                                    block=None)
        if not emergencyPressed:
            return
        if self.getCurrentState() == MachineState.READ_QR_CODE:
            # waiting for the QR code: nothing in process. Shown, so the operator sees the button was acted on; no
            # record (the IOServer has reset the PLC handshake and is waiting for the QR code again). Since CHANGES 123
            # this also covers the seconds after a completed cycle while its values are still shown: that component
            # was already recorded as it finished.
            printYellow("Emergency abort while waiting for the QR code: shown, no record")
            self.showAbortWhileWaitingForQRCode()
            return
        AshokLeylandFrontEnd.emergencyButtonPressed = True
        AshokLeylandFrontEnd.abortReason = "emergency button pressed on the machine"
        printYellow("About to call doFinalProcessing() from updateEmergencyAbortPressed()")
        self.doFinalProcessing(clearTilesAfterMsec=AshokLeylandFrontEnd.ABORT_TILES_CLEAR_MSEC)

    def showAbortWhileWaitingForQRCode(self):
        """Emergency / manual abort while waiting for the QR code: the "Component Processing Aborted" screen (until the
        next QR code is scanned, as after any cycle); no record, the QR code read stays requested."""
        self.show()
        self.raise_()
        self.activateWindow()
        self.largeImageWindow.showEmergencyAbort()
        self.setQRCodeContainerDisplay(displayString="")
        self.requestQRCode()
        self.resultsContainer.setInstruction(self.machineState.getCurrentInstruction())

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            if event.modifiers() == (
                    Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier) and event.key() == Qt.Key.Key_Z:
                self.manualAbort()
                return True
        return super().eventFilter(obj, event)

    def manualAbort(self):
        # The frontend's own abort (2026-11-08): for when the machine's cycle was aborted and the frontend did not follow.
        # Password-protected (frontend.abort.password). It stays in the frontend: no message to the QR code or camera server,
        # no queues cleared. The IOServer is only asked to set PC_PLC_CycleAborted on the PLC; it aborts its own cycle
        # only if that write succeeds (no address yet: it logs and carries on).
        entered = self._runDialog(getJustPassword, textToShow=tr("Frontend abort (this screen only).\nEnter the password:"))
        if not entered:
            return                                          # cancelled
        if entered != CosThetaConfigurator.getInstance().getFrontendAbortPassword():
            printYellow("Frontend abort: wrong password, nothing done")
            self._runDialog(showErrorMessage, tr("Wrong password - nothing aborted"), auto_close_duration=3)
            return
        if self.getCurrentState() == MachineState.READ_QR_CODE:
            self.showAbortWhileWaitingForQRCode()       # nothing in process: shown, no record, nothing sent
            return
        sendFrontendAbortRequestFromFEServerToIOServer(AshokLeylandFrontEnd.processUpdationRedisConnection,
                                                       reason="manual abort (Ctrl+Alt+Z) in the frontend")
        AshokLeylandFrontEnd.emergencyButtonPressed = True
        AshokLeylandFrontEnd.abortReason = "manual abort (Ctrl+Alt+Z) in the frontend"
        printYellow("About to call doFinalProcessing() from manualAbort()")
        self.doFinalProcessing(clearTilesAfterMsec=AshokLeylandFrontEnd.ABORT_TILES_CLEAR_MSEC)

# ********************************************

    def requestQRCode(self):
        GetQRCodeWorker.seekQRCode = True

    def createGetQRCodeWorker(self):
        self.getQRCodeWorker = GetQRCodeWorker()
        self.getQRCodeThread = QThread()
        self.getQRCodeWorker.moveToThread(self.getQRCodeThread)
        self.getQRCodeWorker.gotQRCode.connect(self.updateQRCodeDisplay)
        self.getQRCodeThread.finished.connect(self.getQRCodeThread.deleteLater)
        self.getQRCodeThread.start()

    def setQRCodeContainerDisplay(self, displayString : str = ""):
        displayString = displayString.strip()
        if (displayString is None) or (displayString == ""):
            self.qrCodeLabel.setStyleSheet(modeLabelStylesheet_with_no_qr_code.format(
                QColor(Qt.GlobalColor.black).name()))
            self.qrCodeLabel.setText(tr("QR CODE : "))
        else:
            self.qrCodeLabel.setStyleSheet(modeLabelStylesheet_with_qr_code.format(
                QColor(Qt.GlobalColor.black).name()))
            self.qrCodeLabel.setText(f"{tr('QR CODE : ')}{displayString}")
        self.qrCodeDisplayString = displayString        # shown again in a new language

    @Slot()
    def updateQRCodeDisplay(self):
        displayString = AshokLeylandFrontEnd.currentQRCodeForDisplay
        if self.pendingTilesClear is not None:
            # the next component came before the last one's values were cleared: clear them now (the cleared tiles
            # must not wipe this component's results later)
            self.clearFinishedCycleTiles(self.pendingTilesClear)
        self.hideLargeImageWindow()
        self.setQRCodeContainerDisplay(displayString=displayString)
        self.setCurrentState(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE)
        self.requestPictureAndResult()
        self.resultsContainer.setInstruction(self.machineState.getCurrentInstruction())
        self.currentComponentStartTime = datetime.now()
        self.currentComponentEndTime = None
        self.componentTimeShown = True

    # ********************************************

    @Slot()
    def setInitialInstruction(self):
        self.resultsContainer.setInstruction(self.machineState.getCurrentInstruction())

    # ********************************************

    def requestPictureAndResult(self):
        GetPictureAndResultWorker.seekPictureAndResult = True

    def createGetPictureAndResultWorker(self):
        self.getPictureAndResultWorker = GetPictureAndResultWorker()
        self.getPictureAndResultThread = QThread()
        self.getPictureAndResultWorker.moveToThread(self.getPictureAndResultThread)
        self.getPictureAndResultWorker.gotPictureAndResult.connect(self.updatePictureContainer)
        self.getPictureAndResultThread.finished.connect(self.getPictureAndResultThread.deleteLater)
        self.getPictureAndResultThread.start()

    def showATemporaryLargeImage(self, anImage : Union[np.ndarray, None], result : str | None):
        # printBoldYellow(f"Result received is {result}")
        if anImage is None:
            return
        self.largeImageWindow.setPixmap(anyTypeOfImage=anImage, result=result)
        self.show()
        self.raise_()
        self.activateWindow()
        self.largeImageWindow.show()
        self.largeImageWindow.raise_()
        self.largeImageWindow.activateWindow()
        if f"{result}".lower() == ok.lower():   # was "(result is not None) or ...", which always chose the OK display time
            QTimer.singleShot(AshokLeylandFrontEnd.TIME_TO_SHOW_OK_IMAGE_MSEC, self.hideLargeImageWindow)
        else:
            QTimer.singleShot(AshokLeylandFrontEnd.TIME_TO_SHOW_NOT_OK_IMAGE_MSEC, self.hideLargeImageWindow)

    @Slot()
    def updatePictureContainer(self):
        currentState = AshokLeylandFrontEnd.machineState.getCurrentState()

        stateHandlerMap = {
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE: self._handleKnuckleCheck,
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING: self._handleHubAndBottomBearingCheck,
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING: self._handleTopBearingCheck,
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER: self._handleNutAndPlateWasherCheck,
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_COMPONENT_PRESS: self._handleBungForComponentPress,
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNG: self._handleNoBungAfterComponentPress,
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER: self._handleSplitPinAndWasherCheck,
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP: self._handleCapCheck,
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_CAP_PRESS: self._handleBungCheckForCapPress,
        }

        handler = stateHandlerMap.get(currentState)
        if handler:
            handler()

        self.resultsContainer.setInstruction(self.machineState.getCurrentInstruction())

    def _handleKnuckleCheck(self):
        self.showATemporaryLargeImage(
            anImage=AshokLeylandFrontEnd.processedKnuckleCheckImage,
            result=AshokLeylandFrontEnd.currentKnuckleCheckResult
        )
        self.resultsContainer.setKnuckleCheckData(
            value=AshokLeylandFrontEnd.processedKnuckleCheckImage,
            result=AshokLeylandFrontEnd.currentKnuckleCheckResult
        )
        if AshokLeylandFrontEnd.currentKnuckleCheckResult == ok:
            self._showBypassedStepsIfSet()
            self.setCurrentState(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING)
        else:
            self.setCurrentState(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE)
        self.requestPictureAndResult()

    def _showBypassedStepsIfSet(self):
        """The bypass the IOServer wrote to the PLC in state 2 (cycleSettings:<QR>, published before QR Done): the 4
        skipped tiles show "Bypassed" from the start. Display only - the cycle jumps on the IOServer's moveToState."""
        try:
            settings = readCycleSettingsForQRCode(AshokLeylandFrontEnd.processUpdationRedisConnection,
                                                  AshokLeylandFrontEnd.currentQRCode)
            if settings.get("bypass_press_and_tightening2"):
                self.resultsContainer.setPressAndTightening2Bypassed()
        except Exception:
            noteSuppressed()

    def _handleHubAndBottomBearingCheck(self):
        self.showATemporaryLargeImage(
            anImage=AshokLeylandFrontEnd.processedHubAndBottomBearingCheckImage,
            result=AshokLeylandFrontEnd.currentHubAndBottomBearingCheckResult
        )
        self.resultsContainer.setHubAndBottomBearingCheckData(
            value=AshokLeylandFrontEnd.processedHubAndBottomBearingCheckImage,
            result=AshokLeylandFrontEnd.currentHubAndBottomBearingCheckResult
        )
        if AshokLeylandFrontEnd.currentHubAndBottomBearingCheckResult == ok:
            self.setCurrentState(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING)
        else:
            self.setCurrentState(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING)
        self.requestPictureAndResult()

    def _handleTopBearingCheck(self):
        self.showATemporaryLargeImage(
            anImage=AshokLeylandFrontEnd.processedTopBearingCheckImage,
            result=AshokLeylandFrontEnd.currentTopBearingCheckResult
        )
        self.resultsContainer.setTopBearingCheckData(
            value=AshokLeylandFrontEnd.processedTopBearingCheckImage,
            result=AshokLeylandFrontEnd.currentTopBearingCheckResult
        )
        if AshokLeylandFrontEnd.currentTopBearingCheckResult == ok:
            self.setCurrentState(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER)
        else:
            self.setCurrentState(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING)
        self.requestPictureAndResult()

    def _handleNutAndPlateWasherCheck(self):
        self.showATemporaryLargeImage(
            anImage=AshokLeylandFrontEnd.processedNutAndPlateWasherCheckImage,
            result=AshokLeylandFrontEnd.currentNutAndPlateWasherCheckResult
        )
        self.resultsContainer.setNutAndPlateWasherCheckData(
            value=AshokLeylandFrontEnd.processedNutAndPlateWasherCheckImage,
            result=AshokLeylandFrontEnd.currentNutAndPlateWasherCheckResult
        )
        if AshokLeylandFrontEnd.currentNutAndPlateWasherCheckResult == ok:
            self.setCurrentState(MachineState.READ_TIGHTENING_TORQUE_1)
            self.requestTorqueReadingPlusCapPressAndResult()
        else:
            self.setCurrentState(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER)
            self.requestPictureAndResult()

    def _handleBungForComponentPress(self):
        self.showATemporaryLargeImage(
            anImage=AshokLeylandFrontEnd.processedBungForComponentPressCheckImage,
            result=AshokLeylandFrontEnd.currentBungForComponentPressCheckResult
        )
        self.resultsContainer.setBungForComponentPressData(
            value=AshokLeylandFrontEnd.processedBungForComponentPressCheckImage,
            result=AshokLeylandFrontEnd.currentBungForComponentPressCheckResult
        )
        if AshokLeylandFrontEnd.currentBungForComponentPressCheckResult == ok:
            self.setCurrentState(MachineState.READ_COMPONENT_PRESS_DONE)
            self.requestTorqueReadingPlusCapPressAndResult()
        else:
            self.setCurrentState(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_COMPONENT_PRESS)
            self.requestPictureAndResult()

    def _handleNoBungAfterComponentPress(self):
        self.showATemporaryLargeImage(
            anImage=AshokLeylandFrontEnd.processedNoBungAfterComponentPressCheckImage,
            result=AshokLeylandFrontEnd.currentNoBungAfterComponentPressCheckResult
        )
        self.resultsContainer.setNoBungAfterComponentPress(
            value=AshokLeylandFrontEnd.processedNoBungAfterComponentPressCheckImage,
            result=AshokLeylandFrontEnd.currentNoBungAfterComponentPressCheckResult
        )
        if AshokLeylandFrontEnd.currentNoBungAfterComponentPressCheckResult == ok:
            self.setCurrentState(MachineState.READ_TIGHTENING_TORQUE_2)
            self.requestTorqueReadingPlusCapPressAndResult()
        else:
            self.setCurrentState(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNG)
            self.requestPictureAndResult()

    def _handleSplitPinAndWasherCheck(self):
        self.showATemporaryLargeImage(
            anImage=AshokLeylandFrontEnd.processedSplitPinAndWasherCheckImage,
            result=AshokLeylandFrontEnd.currentSplitPinAndWasherCheckResult
        )
        self.resultsContainer.setSplitPinAndWasherCheckData(
            value=AshokLeylandFrontEnd.processedSplitPinAndWasherCheckImage,
            result=AshokLeylandFrontEnd.currentSplitPinAndWasherCheckResult
        )
        if AshokLeylandFrontEnd.currentSplitPinAndWasherCheckResult == ok:
            self.setCurrentState(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP)
        else:
            self.setCurrentState(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER)
        self.requestPictureAndResult()

    def _handleCapCheck(self):
        self.showATemporaryLargeImage(
            anImage=AshokLeylandFrontEnd.processedCapCheckImage,
            result=AshokLeylandFrontEnd.currentCapCheckResult
        )
        self.resultsContainer.setCapCheckData(
            value=AshokLeylandFrontEnd.processedCapCheckImage,
            result=AshokLeylandFrontEnd.currentCapCheckResult
        )
        if AshokLeylandFrontEnd.currentCapCheckResult == ok:
            self.setCurrentState(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_CAP_PRESS)
        else:
            self.setCurrentState(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP)
        self.requestPictureAndResult()

    def _handleBungCheckForCapPress(self):
        self.showATemporaryLargeImage(
            anImage=AshokLeylandFrontEnd.processedBungForCapPressCheckImage,
            result=AshokLeylandFrontEnd.currentBungForCapPressCheckResult
        )
        self.resultsContainer.setBungCheckData(
            value=AshokLeylandFrontEnd.processedBungForCapPressCheckImage,
            result=AshokLeylandFrontEnd.currentBungForCapPressCheckResult
        )
        if AshokLeylandFrontEnd.currentBungForCapPressCheckResult == ok:
            self.setCurrentState(MachineState.READ_CAP_PRESS_DONE)
            self.requestTorqueReadingPlusCapPressAndResult()
        else:
            self.setCurrentState(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_CAP_PRESS)
            self.requestPictureAndResult()

    # ********************************************

    def requestTorqueReadingPlusCapPressAndResult(self):
        GetTorqueReadingPlusCapPressAndResultWorker.seekTorqueReadingPlusCapPressAndResult = True

    def createGetTorqueReadingPlusCapPressAndResultWorker(self):
        self.getTorqueReadingAndResultWorker = GetTorqueReadingPlusCapPressAndResultWorker()
        self.getTorqueReadingAndResultThread = QThread()
        self.getTorqueReadingAndResultWorker.moveToThread(self.getTorqueReadingAndResultThread)
        self.getTorqueReadingAndResultWorker.gotTorqueReadingPlusCapPressAndResult.connect(self.updateTorqueAndCapPressContainer)
        self.getTorqueReadingAndResultThread.finished.connect(self.getTorqueReadingAndResultThread.deleteLater)
        self.getTorqueReadingAndResultThread.start()

    @Slot()
    def updateTorqueAndCapPressContainer(self):
        currentState = AshokLeylandFrontEnd.machineState.getCurrentState()

        stateHandlerMap = {
            MachineState.READ_TIGHTENING_TORQUE_1: self._handleTighteningTorque1,
            MachineState.READ_COMPONENT_PRESS_DONE: self._handleComponentPressDone,
            MachineState.READ_TIGHTENING_TORQUE_2: self._handleTighteningTorque2,
            MachineState.READ_CAP_PRESS_DONE: self._handleCapPressResult,
            MachineState.READ_FREE_ROTATION_TORQUE_1: self._handleRotationTorque1
        }

        handler = stateHandlerMap.get(currentState)
        if handler:
            handler()

        self.resultsContainer.setInstruction(self.machineState.getCurrentInstruction())

    def _handleTighteningTorque1(self):
        if AshokLeylandFrontEnd.currentTighteningTorque1CheckResult == ok:
            self.resultsContainer.setTighteningTorque1Data(
                value=f"{AshokLeylandFrontEnd.currentTighteningTorque1:.2f}", result=ok,
                rotationDone=AshokLeylandFrontEnd.currentTighteningTorque1RotationDone
            )
            if AshokLeylandFrontEnd.bypassedPressAndTightening2:
                # bung, component press, bung removal and second tightening skipped (as the IOServer and the PLC)
                self.resultsContainer.setPressAndTightening2Bypassed()
                self.setCurrentState(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER)
            else:
                # Machine 2: no separate free-rotation step; next is the bung check before component press
                self.setCurrentState(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_COMPONENT_PRESS)
            self.requestPictureAndResult()
        else:
            self.resultsContainer.setTighteningTorque1Default()
            self.setCurrentState(MachineState.READ_TIGHTENING_TORQUE_1)
            self.requestTorqueReadingPlusCapPressAndResult()

    def _handleComponentPressDone(self):
        if AshokLeylandFrontEnd.currentComponentPressDone == ok:
            self.resultsContainer.setComponentPressData(
                value=N_("Done"), result=ok
            )
            self.setCurrentState(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNG)
            self.requestPictureAndResult()
        else:
            self.resultsContainer.setComponentPressDefault()
            self.setCurrentState(MachineState.READ_COMPONENT_PRESS_DONE)
            self.requestTorqueReadingPlusCapPressAndResult()

    def _handleTighteningTorque2(self):
        if AshokLeylandFrontEnd.currentTighteningTorque2CheckResult == ok:
            self.resultsContainer.setTighteningTorque2Data(
                value=f"{AshokLeylandFrontEnd.currentTighteningTorque2:.2f}", result=ok,
                rotationDone=AshokLeylandFrontEnd.currentTighteningTorque2RotationDone
            )
            self.setCurrentState(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER)
            self.requestPictureAndResult()
        else:
            self.resultsContainer.setTighteningTorque2Default()
            self.setCurrentState(MachineState.READ_TIGHTENING_TORQUE_2)
            self.requestTorqueReadingPlusCapPressAndResult()

    def _handleCapPressResult(self):
        if AshokLeylandFrontEnd.currentCapPressDone == ok:
            self.resultsContainer.setCapPressResultData(value=N_("Done"), result=ok)
            self.setCurrentState(MachineState.READ_FREE_ROTATION_TORQUE_1)
        else:
            self.resultsContainer.setCapPressResultDefault()
            self.setCurrentState(MachineState.READ_CAP_PRESS_DONE)
        self.requestTorqueReadingPlusCapPressAndResult()

    def _handleRotationTorque1(self):
        if AshokLeylandFrontEnd.currentRotationTorque1CheckResult == ok:
            # Machine 2: own tile for the station-3 free-rotation torque
            self.resultsContainer.setFreeRotationTorqueData(
                value=f"{AshokLeylandFrontEnd.currentRotationTorque1:.2f}", result=ok)
        else:
            self.setCurrentState(MachineState.READ_FREE_ROTATION_TORQUE_1)
        printYellow("About to call doFinalProcessing() from normal path")
        # "Component Successfully Done" at once; the values and pictures stay in the tiles for
        # frontend.free.rotation.torque.display.sec (5 s) and are then cleared (CHANGES 123)
        self.doFinalProcessing()

    def doFinalProcessing(self, clearTilesAfterMsec: Optional[int] = None):
        """End of a cycle (completed, or aborted). At once: the result box ("Component Successfully Done" / "Not
        Successfully Done" / "Aborted"), the record to the DB server, and the frontend back at the QR code. The
        tiles keep the finished cycle's values and pictures for clearTilesAfterMsec (default
        frontend.free.rotation.torque.display.sec, 5 s; the aborts pass ABORT_TILES_CLEAR_MSEC, 0.75 s) and are cleared
        then - or earlier, when the next QR code comes.
        Before CHANGES 123 the result box waited for the display time and came together with the clearing."""
        printYellow("Entered doFinalProcessing()")
        if self.currentComponentEndTime is None:
            self.currentComponentEndTime = datetime.now()       # the processing time stops here (shown till next scan)

        bypassed = AshokLeylandFrontEnd.bypassedPressAndTightening2      # skipped steps are excluded, not failed
        checks = [
            AshokLeylandFrontEnd.currentKnuckleCheckResult == ok,
            AshokLeylandFrontEnd.currentHubAndBottomBearingCheckResult == ok,
            AshokLeylandFrontEnd.currentTopBearingCheckResult == ok,
            AshokLeylandFrontEnd.currentNutAndPlateWasherCheckResult == ok,
            AshokLeylandFrontEnd.currentTighteningTorque1CheckResult == ok,  # ADD
            bypassed or AshokLeylandFrontEnd.currentBungForComponentPressCheckResult == ok,
            bypassed or AshokLeylandFrontEnd.currentComponentPressDone == ok,
            bypassed or AshokLeylandFrontEnd.currentNoBungAfterComponentPressCheckResult == ok,
            bypassed or AshokLeylandFrontEnd.currentTighteningTorque2CheckResult == ok,  # ADD
            AshokLeylandFrontEnd.currentSplitPinAndWasherCheckResult == ok,
            AshokLeylandFrontEnd.currentCapCheckResult == ok,
            AshokLeylandFrontEnd.currentBungForCapPressCheckResult == ok,
            AshokLeylandFrontEnd.currentCapPressDone == ok,  # ADD
            AshokLeylandFrontEnd.currentRotationTorque1CheckResult == ok,
        ]

        theFinalResult = all(checks)
        AshokLeylandFrontEnd.finalResult = ok if theFinalResult else notok
        if not self.finishCycle():
            return                                  # the cycle had already ended (nothing new on the tiles)
        if clearTilesAfterMsec is None:
            clearTilesAfterMsec = int(CosThetaConfigurator.getInstance().getFrontEndFreeRotationTorqueDisplaySecs() * 1000)
        self.tilesClearCounter += 1
        number = self.tilesClearCounter
        self.pendingTilesClear = number
        QTimer.singleShot(max(0, int(clearTilesAfterMsec)), lambda: self.clearFinishedCycleTiles(number))

    @Slot()
    def clearFinishedCycleTiles(self, number: Optional[int] = None):
        """Clears the tiles and the QR code line of the finished cycle - only if this is still the clear pending
        (a later cycle end, or a QR code that already cleared them, makes an older timer do nothing)."""
        if number is not None and number != self.pendingTilesClear:
            return
        self.pendingTilesClear = None
        self.resultsContainer.setInProcessDefaultDisplay()
        if self.getCurrentState() == MachineState.READ_QR_CODE:
            self.setQRCodeContainerDisplay(displayString="")

    def okNotokResult(self, value):
        if value is None:
            return notok
        if str(value) == notok:
            return notok
        if isinstance(value, bool):
            if value:
                return ok
            else:
                return notok
        if str(value) == ok:
            return ok
        return notok

        # return value if ((value is not None) and (value)) else notok

    @Slot()
    def setInProcessDefaultDisplay(self):
        """End the cycle and clear the tiles at the same moment (the behaviour before CHANGES 123; kept for callers that
        want it - the normal path is doFinalProcessing: finishCycle now, clearFinishedCycleTiles later)."""
        if self.finishCycle():
            self.clearFinishedCycleTiles()

    def finishCycle(self) -> bool:
        """Result box, record to the DB server, data points reset, state back to the QR code and its read requested.
        The tiles and the QR code line are left as they are (cleared by clearFinishedCycleTiles). Returns False if the
        frontend was already waiting for the QR code (nothing done)."""
        printYellow(f"Entered finishCycle(), with {AshokLeylandFrontEnd.emergencyButtonPressed = }")

        if self.getCurrentState() == MachineState.READ_QR_CODE:
            AshokLeylandFrontEnd.emergencyButtonPressed = False
            AshokLeylandFrontEnd.abortReason = ""
            return False

        self.show()
        self.raise_()
        self.activateWindow()

        aborted, abortReason = AshokLeylandFrontEnd.emergencyButtonPressed, AshokLeylandFrontEnd.abortReason
        AshokLeylandFrontEnd.abortReason = ""
        if AshokLeylandFrontEnd.emergencyButtonPressed:
            self.largeImageWindow.showEmergencyAbort()
        elif AshokLeylandFrontEnd.finalResult == ok:          # finalResult is the string ok / notok; both are truthy
            self.largeImageWindow.showSuccess()
        else:
            self.largeImageWindow.showFailure()

        # NOTE : This is the place where the Emergency button pressed is reset to False (read above for the result
        #        box and for the record)
        AshokLeylandFrontEnd.emergencyButtonPressed = False

        # bypassed steps: result 'Bypassed', no datetime (NULL), no picture, torque 2 = -1.0, rotation done NULL
        bypassed = AshokLeylandFrontEnd.bypassedPressAndTightening2
        def stepResult(value):
            return BYPASSED_TEXT if bypassed else self.okNotokResult(value)
        def stepTime(value):
            return "" if bypassed else value

        sendDataFromFEServerToDatabaseServer(redisConnection=AshokLeylandFrontEnd.databaseUpdationRedisConnection,
                                             qrCode=AshokLeylandFrontEnd.currentQRCode,
                                             knucklePicture=AshokLeylandFrontEnd.originalKnuckleCheckImage,
                                             knuckleResult=self.okNotokResult(AshokLeylandFrontEnd.currentKnuckleCheckResult),
                                             knuckleDatetime=AshokLeylandFrontEnd.currentKnuckleCheckDatetime,
                                             hubAndBottomBearingPicture=AshokLeylandFrontEnd.originalHubAndBottomBearingCheckImage,
                                             hubAndBottomBearingResult=self.okNotokResult(AshokLeylandFrontEnd.currentHubAndBottomBearingCheckResult),
                                             hubAndBottomBearingDatetime=AshokLeylandFrontEnd.currentHubAndBottomBearingCheckDatetime,
                                             topBearingPicture=AshokLeylandFrontEnd.originalTopBearingCheckImage,
                                             topBearingResult=self.okNotokResult(AshokLeylandFrontEnd.currentTopBearingCheckResult),
                                             topBearingDatetime=AshokLeylandFrontEnd.currentTopBearingCheckDatetime,
                                             nutAndPlateWasherPicture=AshokLeylandFrontEnd.originalNutAndPlateWasherCheckImage,
                                             nutAndPlateWasherResult=self.okNotokResult(AshokLeylandFrontEnd.currentNutAndPlateWasherCheckResult),
                                             nutAndPlateWasherDatetime=AshokLeylandFrontEnd.currentNutAndPlateWasherCheckDatetime,
                                             tighteningTorque1=AshokLeylandFrontEnd.currentTighteningTorque1,
                                             tighteningTorque1Result=self.okNotokResult(AshokLeylandFrontEnd.currentTighteningTorque1CheckResult),
                                             tighteningTorque1Datetime=AshokLeylandFrontEnd.currentTighteningTorque1Datetime,
                                             componentPressBungCheckingPicture=None if bypassed else AshokLeylandFrontEnd.originalBungForComponentPressCheckImage,
                                             componentPressBungCheckingResult=stepResult(AshokLeylandFrontEnd.currentBungForComponentPressCheckResult),
                                             componentPressBungCheckDatetime=stepTime(AshokLeylandFrontEnd.currentBungForComponentPressCheckDatetime),
                                             componentPressDone=stepResult(AshokLeylandFrontEnd.currentComponentPressDone),
                                             componentPressDoneDatetime=stepTime(AshokLeylandFrontEnd.currentComponentPressDatetime),
                                             noBungCheckingPicture=None if bypassed else AshokLeylandFrontEnd.originalNoBungAfterComponentPressCheckImage,
                                             noBungCheckingResult=stepResult(AshokLeylandFrontEnd.currentNoBungAfterComponentPressCheckResult),
                                             noBungCheckDatetime=stepTime(AshokLeylandFrontEnd.currentNoBungAfterComponentPressCheckDatetime),
                                             tighteningTorque2=-1.0 if bypassed else AshokLeylandFrontEnd.currentTighteningTorque2,
                                             tighteningTorque2Result=stepResult(AshokLeylandFrontEnd.currentTighteningTorque2CheckResult),
                                             tighteningTorque2Datetime=stepTime(AshokLeylandFrontEnd.currentTighteningTorque2Datetime),
                                             splitPinAndWasherPicture=AshokLeylandFrontEnd.originalSplitPinAndWasherCheckImage,
                                             splitPinAndWasherResult=self.okNotokResult(AshokLeylandFrontEnd.currentSplitPinAndWasherCheckResult),
                                             splitPinAndWasherDatetime=AshokLeylandFrontEnd.currentSplitPinAndWasherCheckDatetime,
                                             capCheckingPicture=AshokLeylandFrontEnd.originalCapCheckImage,
                                             capCheckingResult=self.okNotokResult(AshokLeylandFrontEnd.currentCapCheckResult),
                                             capCheckingDatetime=AshokLeylandFrontEnd.currentCapCheckDatetime,
                                             bungCheckingPicture=AshokLeylandFrontEnd.originalBungForCapPressCheckImage,
                                             capPressBungCheckingResult=self.okNotokResult(AshokLeylandFrontEnd.currentBungForCapPressCheckResult),
                                             capPressBungCheckDatetime=AshokLeylandFrontEnd.currentBungForCapPressCheckDatetime,
                                             pressDone=self.okNotokResult(AshokLeylandFrontEnd.currentCapPressDone),
                                             capPressDoneDatetime=AshokLeylandFrontEnd.currentCapPressDatetime,
                                             freeRotationTorque1=AshokLeylandFrontEnd.currentRotationTorque1,
                                             freeRotationTorque1Result=self.okNotokResult(AshokLeylandFrontEnd.currentRotationTorque1CheckResult),
                                             freeRotationTorque1Datetime=AshokLeylandFrontEnd.currentRotationTorque1Datetime,
                                             overallResult=ok if AshokLeylandFrontEnd.finalResult == ok else notok,
                                             tighteningTorque1RotationDone=AshokLeylandFrontEnd.currentTighteningTorque1RotationDone,
                                             tighteningTorque2RotationDone=None if bypassed else AshokLeylandFrontEnd.currentTighteningTorque2RotationDone,
                                             aborted=aborted,
                                             abortReason=abortReason or "aborted",
                                             bypassedPressAndTightening2=bypassed
                                             )
        self.resetAllDataPoints()
        self.setCurrentState(MachineState.READ_QR_CODE)
        self.requestQRCode()
        self.resultsContainer.setInstruction(self.machineState.getCurrentInstruction())
        return True

    # ********************************************



# ****************************************************

class GetQRCodeWorker(QObject):
    gotQRCode = Signal()
    seekQRCode = False

    def __init__(self):
        super(GetQRCodeWorker, self).__init__()
        self.pollTimer = QTimer(self)
        self.pollTimer.timeout.connect(self.checkForQRCodeTrigger)
        self.pollTimer.start(500)  # Check every 1 sec

        self.qrCheckTimer = QTimer(self)
        self.qrCheckTimer.timeout.connect(self.tryReadQRCode)
        self.qrCheckTimer.setInterval(500)  # Faster retry until found
        self.qrCheckTimer.setSingleShot(False)

    @Slot()
    def checkForQRCodeTrigger(self):
        if (AshokLeylandFrontEnd.machineState.getCurrentState() == MachineState.READ_QR_CODE) and GetQRCodeWorker.seekQRCode:
            self.qrCheckTimer.start()  # Start the QR reading loop
            self.pollTimer.stop()      # Pause regular checking during active reading

    @Slot()
    def tryReadQRCode(self):
        qrCodeValue, qrCodeDisplayString, qrCodeFound = readDataInFEServerFromQRCodeServer(AshokLeylandFrontEnd.processUpdationRedisConnection)
        if qrCodeFound:
            AshokLeylandFrontEnd.currentQRCode = qrCodeValue
            AshokLeylandFrontEnd.currentQRCodeForDisplay = qrCodeDisplayString
            self.gotQRCode.emit()

            # Stop retrying, reset state
            GetQRCodeWorker.seekQRCode = False
            self.qrCheckTimer.stop()
            self.pollTimer.start(1000)  # Resume periodic polling

    def __del__(self):
        self.pollTimer.stop()
        self.pollTimer.deleteLater()
        self.qrCheckTimer.stop()
        self.qrCheckTimer.deleteLater()

# ****************************************************

class TimeDurationUpdateWorker(QObject):
    # (today, this week, this month) OK components, or None if they could not be read (CHANGES 110)
    doTimeDurationUpdate = Signal(object)

    def __init__(self):
        super(TimeDurationUpdateWorker, self).__init__()
        self.dbConnection = None          # kept open, made in the worker's thread at the first read
        self.countsUnavailableSince = None
        self.updateTimer = QTimer(self)
        self.updateTimer.timeout.connect(self.emitUpdateSignal)
        self.updateTimer.start(self._getIntervalMs())

    def _getIntervalMs(self) -> int:
        """Fetch the interval from CosThetaConfigurator and return it in milliseconds"""
        try:
            return int(CosThetaConfigurator.getInstance().getTimeDurationSleepInterval() * 1000)
        except Exception as e:
            print(f"[TimeDurationUpdateWorker] Failed to get interval, defaulting to 1000ms: {e}")
            return 1000  # Fallback to 1 second

    def _readCounts(self):
        """The production counts, read here in the worker's thread on one kept-open connection; None on a database
        error (logged once until the counts can be read again)."""
        try:
            if self.dbConnection is None:
                self.dbConnection = PersistentDBConnection()
            counts = getProductionCounts(self.dbConnection, getDatabaseName())
            if self.countsUnavailableSince is not None:
                printBoldGreen("Production counts read again from the database")
                self.countsUnavailableSince = None
            return counts
        except Exception as e:
            if self.countsUnavailableSince is None:
                self.countsUnavailableSince = time.monotonic()
                printBoldRed(f"Production counts could not be read from the database ({e}); the last counts stay shown")
            return None

    @Slot()
    def emitUpdateSignal(self):
        if not AshokLeylandFrontEnd.shutdownThreads:
            # printBoldGreen("Emitting doTimeDurationUpdate()")
            self.doTimeDurationUpdate.emit(self._readCounts())
        else:
            self.updateTimer.stop()
            # printBoldRed("Stopped TimeDurationUpdateWorker due to shutdown.")

    def __del__(self):
        self.updateTimer.stop()
        self.updateTimer.deleteLater()

def _applyLiveInterval(timer, intervalMs: int) -> None:
    """Live configuration: a changed interval in the properties file is applied to a running QTimer."""
    try:
        if intervalMs > 0 and timer.interval() != intervalMs:
            timer.setInterval(intervalMs)
    except Exception:
        noteSuppressed()

# ****************************************************

class ConnectionStatusUpdateWorker(QObject):
    doConnectionStatusUpdate = Signal()

    def __init__(self):
        super(ConnectionStatusUpdateWorker, self).__init__()
        self.updateTimer = QTimer(self)
        self.updateTimer.timeout.connect(self.emitUpdateSignal)
        self.updateTimer.start(self._getIntervalMs())

    def _getIntervalMs(self) -> int:
        """Fetch interval from config and return in milliseconds"""
        try:
            return int(CosThetaConfigurator.getInstance().getGeneralConnectionStatusSleepInterval() * 1000)
        except Exception as e:
            print(f"[ConnectionStatusUpdateWorker] Failed to get interval, defaulting to 1000ms: {e}")
            return 1000  # Default fallback

    @Slot()
    def emitUpdateSignal(self):
        if not AshokLeylandFrontEnd.shutdownThreads:
            # printBoldGreen("Emitting doConnectionStatusUpdate")
            self.doConnectionStatusUpdate.emit()
            _applyLiveInterval(self.updateTimer, self._getIntervalMs())
        else:
            self.updateTimer.stop()
            # printBoldRed("Stopped ConnectionStatusUpdateWorker due to shutdown.")

    def __del__(self):
        self.updateTimer.stop()
        self.updateTimer.deleteLater()

# ****************************************************

class EmergencyStatusMonitoringWorker(QObject):
    doEmergencyPressedUpdate = Signal()

    def __init__(self):
        super(EmergencyStatusMonitoringWorker, self).__init__()
        self.updateTimer = QTimer(self)
        self.updateTimer.timeout.connect(self.emitEmergencyStatus)
        self.updateTimer.start(self._getIntervalMs())  # Check every 1 second

    def _getIntervalMs(self) -> int:
        """Fetch interval from config and return in milliseconds"""
        try:
            return int(CosThetaConfigurator.getInstance().getFrontEndEmergencyConnectionStatusSleepInterval() * 1000)
        except Exception as e:
            print(f"[ConnectionStatusUpdateWorker] Failed to get interval, defaulting to 1000ms: {e}")
            return 1000  # Default fallback

    @Slot()
    def emitEmergencyStatus(self):
        if not AshokLeylandFrontEnd.shutdownThreads:
            # printBoldGreen("Emitting doEmergencyPressedUpdate")
            self.doEmergencyPressedUpdate.emit()
            _applyLiveInterval(self.updateTimer, self._getIntervalMs())
        else:
            self.updateTimer.stop()
            # printBoldRed("Stopped EmergencyStatusMonitoringWorker due to shutdown.")

    def __del__(self):
        self.updateTimer.stop()
        self.updateTimer.deleteLater()

# ****************************************************

class GetPictureAndResultWorker(QObject):

    gotPictureAndResult = Signal()
    seekPictureAndResult : bool = False

    validStates : list = [
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE,
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING,
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING,
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER,
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_COMPONENT_PRESS,
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNG,
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER,
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP,
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_CAP_PRESS
        ]

    def __init__(self):
        super(GetPictureAndResultWorker, self).__init__()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.checkForPictureAndResult)
        self.timer.start(500)  # match original polling interval

    @Slot()
    def checkForPictureAndResult(self):
        if AshokLeylandFrontEnd.shutdownThreads:
            self.timer.stop()
            return

        if not GetPictureAndResultWorker.seekPictureAndResult:
            return

        currentState = AshokLeylandFrontEnd.machineState.getCurrentState()

        if currentState not in GetPictureAndResultWorker.validStates:
            return

        try:
            timeOfMessage, decodedOriginalImage, decodedProcessedImage, decodedResult, currentMachineState = readDataInFEServerFromCameraServer(AshokLeylandFrontEnd.processUpdationRedisConnection)

            if decodedResult is not None:

                GetPictureAndResultWorker.seekPictureAndResult = False

                if decodedOriginalImage is not None:
                    if not isinstance(decodedOriginalImage, np.ndarray):
                        decodedOriginalImage = blankWhiteImage
                else:
                    decodedOriginalImage = blankWhiteImage

                if decodedProcessedImage is not None:
                    if not isinstance(decodedProcessedImage, np.ndarray):
                        decodedProcessedImage = blankWhiteImage
                else:
                    decodedProcessedImage = blankWhiteImage

                if currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE:
                    AshokLeylandFrontEnd.originalKnuckleCheckImage = decodedOriginalImage
                    AshokLeylandFrontEnd.processedKnuckleCheckImage = decodedProcessedImage
                    AshokLeylandFrontEnd.currentKnuckleCheckResult = decodedResult
                    AshokLeylandFrontEnd.currentKnuckleCheckDatetime = getPostgresDatetimeFromString(getCurrentTime())

                elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING:
                    AshokLeylandFrontEnd.originalHubAndBottomBearingCheckImage = decodedOriginalImage
                    AshokLeylandFrontEnd.processedHubAndBottomBearingCheckImage = decodedProcessedImage
                    AshokLeylandFrontEnd.currentHubAndBottomBearingCheckResult = decodedResult
                    AshokLeylandFrontEnd.currentHubAndBottomBearingCheckDatetime = getPostgresDatetimeFromString(getCurrentTime())

                elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING:
                    AshokLeylandFrontEnd.originalTopBearingCheckImage = decodedOriginalImage
                    AshokLeylandFrontEnd.processedTopBearingCheckImage = decodedProcessedImage
                    AshokLeylandFrontEnd.currentTopBearingCheckResult = decodedResult
                    AshokLeylandFrontEnd.currentTopBearingCheckDatetime = getPostgresDatetimeFromString(getCurrentTime())

                elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER:
                    AshokLeylandFrontEnd.originalNutAndPlateWasherCheckImage = decodedOriginalImage
                    AshokLeylandFrontEnd.processedNutAndPlateWasherCheckImage = decodedProcessedImage
                    AshokLeylandFrontEnd.currentNutAndPlateWasherCheckResult = decodedResult
                    AshokLeylandFrontEnd.currentNutAndPlateWasherCheckDatetime = getPostgresDatetimeFromString(getCurrentTime())

                elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_COMPONENT_PRESS:
                    AshokLeylandFrontEnd.originalBungForComponentPressCheckImage = decodedOriginalImage
                    AshokLeylandFrontEnd.processedBungForComponentPressCheckImage = decodedProcessedImage
                    AshokLeylandFrontEnd.currentBungForComponentPressCheckResult = decodedResult
                    AshokLeylandFrontEnd.currentBungForComponentPressCheckDatetime = getPostgresDatetimeFromString(getCurrentTime())

                elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNG:
                    AshokLeylandFrontEnd.originalNoBungAfterComponentPressCheckImage = decodedOriginalImage
                    AshokLeylandFrontEnd.processedNoBungAfterComponentPressCheckImage = decodedProcessedImage
                    AshokLeylandFrontEnd.currentNoBungAfterComponentPressCheckResult = decodedResult
                    AshokLeylandFrontEnd.currentNoBungAfterComponentPressCheckDatetime = getPostgresDatetimeFromString(getCurrentTime())

                elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER:
                    AshokLeylandFrontEnd.originalSplitPinAndWasherCheckImage = decodedOriginalImage
                    AshokLeylandFrontEnd.processedSplitPinAndWasherCheckImage = decodedProcessedImage
                    AshokLeylandFrontEnd.currentSplitPinAndWasherCheckResult = decodedResult
                    AshokLeylandFrontEnd.currentSplitPinAndWasherCheckDatetime = getPostgresDatetimeFromString(getCurrentTime())

                elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP:
                    AshokLeylandFrontEnd.originalCapCheckImage = decodedOriginalImage
                    AshokLeylandFrontEnd.processedCapCheckImage = decodedProcessedImage
                    AshokLeylandFrontEnd.currentCapCheckResult = decodedResult
                    AshokLeylandFrontEnd.currentCapCheckDatetime = getPostgresDatetimeFromString(getCurrentTime())

                elif currentState == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_CAP_PRESS:
                    AshokLeylandFrontEnd.originalBungForCapPressCheckImage = decodedOriginalImage
                    AshokLeylandFrontEnd.processedBungForCapPressCheckImage = decodedProcessedImage
                    AshokLeylandFrontEnd.currentBungForCapPressCheckResult = decodedResult
                    AshokLeylandFrontEnd.currentBungForCapPressCheckDatetime = getPostgresDatetimeFromString(getCurrentTime())

                self.gotPictureAndResult.emit()

        except Exception as e:
            printBoldRed(f"[GetPictureAndResultWorker] Exception: {e}")

    def __del__(self):
        self.timer.stop()
        self.timer.deleteLater()

# ****************************************************

class GetTorqueReadingPlusCapPressAndResultWorker(QObject):
    gotTorqueReadingPlusCapPressAndResult = Signal()
    seekTorqueReadingPlusCapPressAndResult = False

    def __init__(self):
        super(GetTorqueReadingPlusCapPressAndResultWorker, self).__init__()
        self.validStates = {
            MachineState.READ_TIGHTENING_TORQUE_1: self._handleTighteningTorque1,
            MachineState.READ_COMPONENT_PRESS_DONE: self._handleComponentPress,
            MachineState.READ_TIGHTENING_TORQUE_2: self._handleTighteningTorque2,
            MachineState.READ_CAP_PRESS_DONE: self._handleCapPress,
            MachineState.READ_FREE_ROTATION_TORQUE_1: self._handleRotationTorque1
        }

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.checkForTorqueAndResult)
        self.timer.start(500)  # match original polling interval

    @Slot()
    def checkForTorqueAndResult(self):
        if AshokLeylandFrontEnd.shutdownThreads:
            self.timer.stop()
            return

        if not GetTorqueReadingPlusCapPressAndResultWorker.seekTorqueReadingPlusCapPressAndResult:
            return

        currentState = AshokLeylandFrontEnd.machineState.getCurrentState()

        if currentState not in self.validStates:
            return

        currentMachineState, moveToState, action, value, result, rotationDone = readDataInFEServerFromIOServer(
            AshokLeylandFrontEnd.processUpdationRedisConnection, withRotationDone=True
        )

        if currentMachineState == MachineState.INVALID_STATE:
            return

        # Validate that the incoming message's state matches the expected state
        # This prevents processing stale/leftover data from a different state
        if currentMachineState != currentState:
            printBoldRed(f"[GetTorqueReadingPlusCapPressAndResultWorker] State mismatch: expected {currentState}, got {currentMachineState}. Ignoring message.")
            return

        GetTorqueReadingPlusCapPressAndResultWorker.seekTorqueReadingPlusCapPressAndResult = False

        try:
            handler = self.validStates[currentState]
            self.lastRotationDone = rotationDone       # with the Station 2 torque values (None if not provided)
            self.lastMoveToState = moveToState         # Torque 1: the split pin picture = bypass of states 13-19
            handler(value, result)
            self.gotTorqueReadingPlusCapPressAndResult.emit()
        except Exception as e:
            printBoldRed(f"[GetTorqueReadingPlusCapPressAndResultWorker] Error handling state {currentState}: {e}")

    # --- State Handlers ---

    def _handleTighteningTorque1(self, value, result):
        if result == ok:
            AshokLeylandFrontEnd.currentTighteningTorque1 = round(float(value), 2)
            AshokLeylandFrontEnd.currentTighteningTorque1CheckResult = ok
        else:
            AshokLeylandFrontEnd.currentTighteningTorque1 = 0.0
            AshokLeylandFrontEnd.currentTighteningTorque1CheckResult = notok
        AshokLeylandFrontEnd.currentTighteningTorque1Datetime = getPostgresDatetimeFromString(getCurrentTime())
        AshokLeylandFrontEnd.currentTighteningTorque1RotationDone = getattr(self, "lastRotationDone", None)
        AshokLeylandFrontEnd.bypassedPressAndTightening2 = (
            result == ok and getattr(self, "lastMoveToState", None) == MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER)

    def _handleComponentPress(self, value, result):
        if result == ok:
            AshokLeylandFrontEnd.currentComponentPressDone = ok
        else:
            AshokLeylandFrontEnd.currentComponentPressDone = notok
        AshokLeylandFrontEnd.currentComponentPressDatetime = getPostgresDatetimeFromString(getCurrentTime())

    def _handleTighteningTorque2(self, value, result):
        if result == ok:
            AshokLeylandFrontEnd.currentTighteningTorque2 = round(float(value), 2)
            AshokLeylandFrontEnd.currentTighteningTorque2CheckResult = ok
        else:
            AshokLeylandFrontEnd.currentTighteningTorque2 = 0.0
            AshokLeylandFrontEnd.currentTighteningTorque2CheckResult = notok
        AshokLeylandFrontEnd.currentTighteningTorque2Datetime = getPostgresDatetimeFromString(getCurrentTime())
        AshokLeylandFrontEnd.currentTighteningTorque2RotationDone = getattr(self, "lastRotationDone", None)

    def _handleCapPress(self, value, result):
        if result == ok:
            AshokLeylandFrontEnd.currentCapPressDone = ok
        else:
            AshokLeylandFrontEnd.currentCapPressDone = notok
        AshokLeylandFrontEnd.currentCapPressDatetime = getPostgresDatetimeFromString(getCurrentTime())

    def _handleRotationTorque1(self, value, result):
        if result == ok:
            AshokLeylandFrontEnd.currentRotationTorque1 = round(float(value), 2)
            AshokLeylandFrontEnd.currentRotationTorque1CheckResult = ok
        else:
            AshokLeylandFrontEnd.currentRotationTorque1 = 0.0
            AshokLeylandFrontEnd.currentRotationTorque1CheckResult = notok
        AshokLeylandFrontEnd.currentRotationTorque1Datetime = getPostgresDatetimeFromString(getCurrentTime())

    def __del__(self):
        self.timer.stop()
        self.timer.deleteLater()

# ****************************************************

class FrontendNoticeWorker(QObject):
    """Reads the notices the IOServer and the camera server leave for the frontend (Redis, readFrontendNotices) once
    a second, in its own thread, and passes them to the window when they change."""
    gotNotices = Signal(list)

    def __init__(self):
        super().__init__()
        self.timer = None
        self.connection = None
        self.last = None

    def start(self):
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check)
        self.timer.start(1000)

    def check(self):
        if AshokLeylandFrontEnd.shutdownThreads:
            self.timer.stop()
            return
        try:
            if self.connection is None:
                self.connection = Redis(CosThetaConfigurator.getInstance().getRedisHost(),
                                        CosThetaConfigurator.getInstance().getRedisPort(),
                                        socket_timeout=0.5, socket_connect_timeout=0.5)
            notices = readFrontendNotices(self.connection)
        except Exception:
            notices = None
        if notices is None:
            self.connection = None
            return
        if notices != self.last:
            self.last = notices
            self.gotNotices.emit(notices)


class StateSyncWorker(QObject):
    """State sync phase A (CHANGES 108). The IOServer publishes every state change (io2festate.q, io:state); this
    worker reads them 4 times a second, in its own thread, and compares them with the frontend's own state
    (utils/StateSync.StateSyncMonitor). It changes nothing: a difference lasting longer than
    frontend.state.sync.grace.sec (5 s) is logged as "STATE-SYNC MISMATCH", its end as "STATE-SYNC RESOLVED"; missing
    messages, an IOServer restart and a per-cycle summary are logged too (all lines start with "STATE-SYNC")."""
    INTERVAL_MS = CosThetaConfigurator.getTuning("frontend.state.sync.poll.interval.msec", 250, minimum=50)

    def __init__(self):
        super().__init__()
        self.timer = None
        self.connection = None
        self.startedFromKey = False
        self.monitor = StateSyncMonitor(log=StateSyncWorker._log,
                                        graceSecs=CosThetaConfigurator.getFrontEndStateSyncGraceSecs,
                                        endOfCycleDisplaySecs=CosThetaConfigurator.getFrontEndFreeRotationTorqueDisplaySecs)

    @staticmethod
    def _log(level: str, text: str) -> None:
        try:
            logBoth(level, "AshokLeylandFrontEnd.StateSync", text)
        except Exception:
            noteSuppressed()

    def start(self):
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check)
        self.timer.start(StateSyncWorker.INTERVAL_MS)

    def check(self):
        if AshokLeylandFrontEnd.shutdownThreads:
            self.timer.stop()
            return
        try:
            if self.connection is None:
                self.connection = Redis(CosThetaConfigurator.getInstance().getRedisHost(),
                                        CosThetaConfigurator.getInstance().getRedisPort(),
                                        socket_timeout=0.5, socket_connect_timeout=0.5)
            if not self.startedFromKey:
                snapshot = readIOServerStateSnapshot(self.connection)
                if snapshot is not None:
                    self.monitor.onIOServerStates([snapshot], fromKey=True)
                self.startedFromKey = True
            snapshots = readIOServerStates(self.connection)
            if snapshots is None:
                self.connection = None
                return
            self.monitor.onIOServerStates(snapshots)
            self.monitor.check(AshokLeylandFrontEnd.machineState.getCurrentState())
        except Exception:
            self.connection = None
            noteSuppressed()


class HeartbeatSenderWorker(QObject):
    def __init__(self):
        super().__init__()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.send_heartbeat)

    def start(self):
        self.timer.start(self._getIntervalMs())   # frontend.status.sleepinterval.sec (was a fixed 500 ms)

    @staticmethod
    def _getIntervalMs() -> int:
        try:
            return max(100, int(CosThetaConfigurator.getInstance().getFrontEndHeartbeatSleepInterval() * 1000))
        except Exception:
            return 500

    def stop(self):
        self.timer.stop()

    def send_heartbeat(self):
        from utils.WorkerHealth import maxLoopSilenceSecs
        frozen = time.monotonic() - AshokLeylandFrontEnd.lastGuiTickAt
        if frozen > maxLoopSilenceSecs() and AshokLeylandFrontEnd.dialogsOpen == 0:
            if not getattr(self, "_reportedFrozen", False):
                self._reportedFrozen = True
                printBoldRed(f"Frontend: the GUI thread has not run for {frozen:.0f} s - heartbeat DEAD")
            sendHeartbeatFromFEServerToHeartbeatServer(AshokLeylandFrontEnd.heartbeatRedisConnection, DEAD)
        else:
            if getattr(self, "_reportedFrozen", False):
                self._reportedFrozen = False
                printBoldGreen("Frontend: the GUI thread runs again")
            sendHeartbeatFromFEServerToHeartbeatServer(AshokLeylandFrontEnd.heartbeatRedisConnection, ALIVE)
        _applyLiveInterval(self.timer, self._getIntervalMs())

    def __del__(self):
        self.timer.stop()
        self.timer.deleteLater()

# ****************************************************

class LargeImageWindow(QWidget):
    """
    This "window" is a QWidget. If it has no parent, it
    will appear as a free-floating window as we want.
    """

    def __init__(self, parent = None):
        super().__init__(parent)
        bind(self, "Large Image Display", "setWindowTitle")
        self.setWindowFlags(Qt.WindowType.WindowCloseButtonHint | Qt.WindowType.Dialog)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.width = int(AshokLeylandFrontEnd.CURRENT_WIDTH * 0.5)
        self.height = int(AshokLeylandFrontEnd.CURRENT_HEIGHT * 0.5)
        self.left = int((AshokLeylandFrontEnd.CURRENT_WIDTH - self.width) / 2)
        self.top =  int(AshokLeylandFrontEnd.RESULTS_CONTAINER_TOP + self.height / 10)
        self.setFixedWidth(self.width)
        self.setFixedHeight(self.height)
        self.setGeometry(self.left, self.top, self.width, self.height)
        layout = QVBoxLayout()
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setScaledContents(False)
        anImage = createImage(text="Sample Image", imageDimensions=(self.width - 35, self.height - 35),
                                        fontColor=QColor(15, 15, 15), replaceChar=[' ',' '], backgroundColor = QColor(125, 210, 185))
        self.image = getPixmapImage(anImage)
        layout.addWidget(self.label)
        self.setLayout(layout)
        self.label.setPixmap(self.image)
        self.successImage = createImage(text="Component Successfully Done", imageDimensions=(self.width - 35, self.height - 35),
                                            fontColor=QColor(15, 15, 15), replaceChar=[' ',' '], backgroundColor = QColor(125, 210, 185))
        self.failureImage = createImage(text="Component Not Successfully Done", imageDimensions=(self.width - 35, self.height - 35),
                                        fontColor=QColor(15, 15, 15), replaceChar=[' ',' '], backgroundColor = QColor(210, 90, 90))
        self.emergencyImage = createImage(text="Component Processing Aborted", imageDimensions=(self.width - 35, self.height - 35),
                                        fontColor=QColor(15, 15, 15), replaceChar=[' ',' '], backgroundColor = QColor(210, 90, 90))


    def setPixmap(self, anyTypeOfImage:  QImage | QPixmap | ndarray | str, result : str = "no result"):
        pixMapImage = getPixmapImage(anyTypeOfImage)
        if result == ok:
            self.label.setStyleSheet("background-color: green;border: 0px;border-radius: 5px;QLabel { margin: auto; }")
        elif result == notok:
            self.label.setStyleSheet("background-color: red;border: 0px;border-radius: 5px;QLabel { margin: auto; }")
        else:
            self.label.setStyleSheet("background-color: white;border: 0px;border-radius: 5px;QLabel { margin: auto; }")
        # Scaled with its aspect ratio kept: as large as fits, touching one pair of opposite sides; the label's
        # green / red (white) background shows in the rest of the box. (It was stretched to fill the box.)
        pixMapImage = resize_pixmap(pixmap=pixMapImage, width=self.width-35, height=self.height-35, keepAspectRatio=True)
        self.label.setPixmap(pixMapImage)

    def _resultImage(self, text: str, backgroundColor):
        return createImage(text=tr(text), imageDimensions=(self.width - 35, self.height - 35),
                           fontColor=QColor(15, 15, 15), replaceChar=[' ', ' '], backgroundColor=backgroundColor)

    def showSuccess(self):
        self.setPixmap(self._resultImage("Component Successfully Done", QColor(125, 210, 185)))
        self.show()
        self.raise_()
        self.activateWindow()

    def showFailure(self):
        self.setPixmap(self._resultImage("Component Not Successfully Done", QColor(210, 90, 90)))
        self.show()
        self.raise_()
        self.activateWindow()

    def showEmergencyAbort(self):
        self.setPixmap(self._resultImage("Component Processing Aborted", QColor(210, 90, 90)))
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        self.hide()
        if self.parent():
            self.parent().setFocus()
        event.accept()

    def close(self):
        self.deleteLater()

    def dispose(self):
        self.deleteLater()
# ****************************************************


def startFrontEnd(mode : str, username : str, role : str):
    gfe = AshokLeylandFrontEnd(mode=mode, username=username, role=role)   # noqa: F841 - the name keeps the window alive
    app.exec()
    app.quit()


# startFrontEnd("Test", "admin", "admin")
# printBoldBlue("****************")
# printBoldBlue("Started FrontEnd Server")
# printBoldBlue("****************")