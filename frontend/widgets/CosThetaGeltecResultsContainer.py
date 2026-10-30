# from multidispatch import dispatch
from PySide6.QtWidgets import QHBoxLayout

import frontend
from frontend import CommonData
from frontend.CommonData import *
from frontend.CosThetaMonitorDimensions import getMonitorWidth, getMonitorHeight
from frontend.widgets.CosThetaOutcomeContainerWithOKNotokTotal import *
from frontend.frontendutils.CosThetaLabelUtils import *
from frontend.frontendutils.CosThetaImageUtils import createImage
from typing import Union, get_origin, get_args
from persistence.GetTodaysCountsFromDB import getTodaysOKRecordsCount, getTodaysTotalRecordsCount, getTodaysNotOKRecordsCount, getTodaysInvalidResultsRecordsCount, getOKRecordsForBatchNumber, getNotOKRecordsForBatchNumber, getInvalidResultRecordsForBatchNumber
# from logutils.SlaveLoggers import *
from persistence.Persistence import *

app = getAppInstance()
populateMonitorDimensions() # needs to be called to ensure that a QtGui context is there for some calls to work

class CosThetaGeltecResultsContainer(QWidget):

    logSource = getFullyQualifiedName(__file__)
    labelHeight = CosThetaConfigurator.getInstance().getLabelInitialHeight()
    averageLabelHeight = labelHeight + 3
    spacingBetweenWidgets = 5
    width_of_each_container = int(getMonitorWidth() / 2) - 10
    # height_of_each_container = width_of_each_container + 2 * labelHeight + 2 * CosThetaOutcomeContainer.spacingBetweenWidgets
    height_of_each_container = int (getMonitorHeight()- 4 * labelHeight - 2 * spacingBetweenWidgets)
    imageHeight = int(height_of_each_container - 3 * averageLabelHeight - 5 * spacingBetweenWidgets)
    # printPlain(width_of_each_container, height_of_each_container)
    # defaultQRCodeImage = createImage("7204838 B5C47102 088 07.11.2022 2.8T", imageDimensions=(width_of_each_container,
    #                                                                               height_of_each_container - 2 * labelHeight - 2 * CosThetaOutcomeContainer.spacingBetweenWidgets - 6 * CosThetaOutcomeContainer.spacingBetweenWidgets),
    #                                  fontColor=Qt.GlobalColor.darkBlue, replaceChar=['=',' '])


    # defaultLeftDiscImage = createImage("Left Disc Image", imageDimensions=(int(width_of_each_container * 0.98), int(imageHeight * 0.88)),
    #                                    fontColor=Qt.GlobalColor.darkBlue, replaceChar=['=',' '])
    # defaultRightDiscImage = createImage("Right Disc Image", imageDimensions=(int(width_of_each_container * 0.98), int(imageHeight * 0.88)),
    #                                     fontColor=Qt.GlobalColor.darkBlue, replaceChar=['=',' '])

    def __init__(self, cust_name : str, med_name : str):
        super().__init__()
        self.cust_name = cust_name
        self.med_name = med_name
        CosThetaGeltecResultsContainer.logSource = getFullyQualifiedName(__file__, __class__)
        self.initializeUI()

    def initializeUI(self):
        """Initialize the window and display its contents to the screen."""
        self.setMinimumSize(int(CosThetaGeltecResultsContainer.width_of_each_container * 0.9), int(CosThetaGeltecResultsContainer.height_of_each_container * 0.9))
        self.setupWidgets()


    def setupWidgets(self):
        """Set up the containers and main layout for the window."""
        leftDiscStartingOKCount = 0
        leftDiscStartingTotalCount = 0
        rightDiscStartingOKCount = 0
        rightDiscStartingTotalCount = 0
        db_name = getDatabaseName(frontend.CommonData.mode)
        # printBoldRed(f"In setupWidgets() of CosThetaGeltecResultsContainer, the db_name is {db_name = }")
        try:
            leftDiscStartingOKCount = 0
            if frontend.CommonData.mode.upper().startswith("PROD") and (frontend.CommonData.batchNumber is not None) and (frontend.CommonData.batchNumber != 'None'):
                leftDiscStartingOKCount = getOKRecordsForBatchNumber(cust_name=self.cust_name, med_name=self.med_name, db_name=db_name, batchNumber=frontend.CommonData.batchNumber, discId=1)*CosThetaConfigurator.getInstance().getNoOfBottlesPerImage(cust_name=self.cust_name, med_name=self.med_name)
                leftDiscStartingNotOKCount = getNotOKRecordsForBatchNumber(cust_name=self.cust_name, med_name=self.med_name, db_name=db_name, batchNumber=frontend.CommonData.batchNumber, discId=1)*CosThetaConfigurator.getInstance().getNoOfBottlesPerImage(cust_name=self.cust_name, med_name=self.med_name)
                # leftDiscStartingTotalCount = getTodaysTotalRecords(1)*CosThetaConfigurator.getInstance().getNoOfBottlesPerImage()
                leftDiscStartingTotalCount = leftDiscStartingOKCount + leftDiscStartingNotOKCount
            else:
                leftDiscStartingOKCount = getTodaysOKRecordsCount(cust_name=self.cust_name, med_name=self.med_name, db_name=db_name, discId=1) * CosThetaConfigurator.getInstance().getNoOfBottlesPerImage(cust_name=self.cust_name, med_name=self.med_name)
                leftDiscStartingNotOKCount = getTodaysNotOKRecordsCount(cust_name=self.cust_name, med_name=self.med_name, db_name=db_name, discId=1) * CosThetaConfigurator.getInstance().getNoOfBottlesPerImage(cust_name=self.cust_name, med_name=self.med_name)
                # leftDiscStartingTotalCount = getTodaysTotalRecords(1)*CosThetaConfigurator.getInstance().getNoOfBottlesPerImage()
                leftDiscStartingTotalCount = leftDiscStartingOKCount + leftDiscStartingNotOKCount

            rightDiscStartingOKCount = 0
            if frontend.CommonData.mode.upper().startswith("PROD") and (frontend.CommonData.batchNumber is not None) and (frontend.CommonData.batchNumber != 'None'):
                rightDiscStartingOKCount = getOKRecordsForBatchNumber(cust_name=self.cust_name, med_name=self.med_name, db_name=db_name, batchNumber=frontend.CommonData.batchNumber, discId=2)*CosThetaConfigurator.getInstance().getNoOfBottlesPerImage(cust_name=self.cust_name, med_name=self.med_name)
                rightDiscStartingNotOKCount = getNotOKRecordsForBatchNumber(cust_name=self.cust_name, med_name=self.med_name, db_name=db_name, batchNumber=frontend.CommonData.batchNumber, discId=2)*CosThetaConfigurator.getInstance().getNoOfBottlesPerImage(cust_name=self.cust_name, med_name=self.med_name)
                # rightDiscStartingTotalCount = getTodaysTotalRecords(2)*CosThetaConfigurator.getInstance().getNoOfBottlesPerImage()
                rightDiscStartingTotalCount = rightDiscStartingOKCount + rightDiscStartingNotOKCount
            else:
                rightDiscStartingOKCount = getTodaysOKRecordsCount(cust_name=self.cust_name, med_name=self.med_name, db_name=db_name, discId=2) * CosThetaConfigurator.getInstance().getNoOfBottlesPerImage(cust_name=self.cust_name, med_name=self.med_name)
                rightDiscStartingNotOKCount = getTodaysNotOKRecordsCount(cust_name=self.cust_name, med_name=self.med_name, db_name=db_name, discId=2) * CosThetaConfigurator.getInstance().getNoOfBottlesPerImage(cust_name=self.cust_name, med_name=self.med_name)
                # rightDiscStartingTotalCount = getTodaysTotalRecords(2)*CosThetaConfigurator.getInstance().getNoOfBottlesPerImage()
                rightDiscStartingTotalCount = rightDiscStartingOKCount + rightDiscStartingNotOKCount

        except Exception as e:
            printBoldRed(f'Could not fetch todays total and OK counts from database due to {e}')
            # SlaveConsoleLogger.getInstance().logDebug(CosThetaGeltecResultsContainer.logSource,
            #                                           f'Could not fetch todays total and OK counts from database due to {e}',
            #                                           Logger.SUCCESS)
            # SlaveFileLogger.getInstance().logDebug(CosThetaGeltecResultsContainer.logSource,
            #                                        f'Could not fetch todays total and OK counts from database due to {e}',
            #                                        Logger.SUCCESS)

        self.left_disc_container = CosThetaOutcomeContainerWithOKNotokTotal(cust_name=self.cust_name, med_name=self.med_name, title="Left Disc", width=CosThetaGeltecResultsContainer.width_of_each_container,
                                                                            height=CosThetaGeltecResultsContainer.height_of_each_container, startingImage=CosThetaGeltecResultsContainer.defaultLeftDiscImage,
                                                                            labelHeight=CosThetaGeltecResultsContainer.averageLabelHeight, todaysOKCount=leftDiscStartingOKCount,
                                                                            todaysTotalCount=leftDiscStartingTotalCount, fg_color = Qt.GlobalColor.white) # =QColor(16, 201, 78))  # Green
        self.right_disc_container = CosThetaOutcomeContainerWithOKNotokTotal(cust_name=self.cust_name, med_name=self.med_name, title="Right Disc", width=CosThetaGeltecResultsContainer.width_of_each_container,
                                                                             height=CosThetaGeltecResultsContainer.height_of_each_container, startingImage=CosThetaGeltecResultsContainer.defaultRightDiscImage,
                                                                             labelHeight=CosThetaGeltecResultsContainer.averageLabelHeight, todaysOKCount=rightDiscStartingOKCount,
                                                                             todaysTotalCount=rightDiscStartingTotalCount, fg_color = Qt.GlobalColor.white) # QColor(10, 194, 228))  # Blue

        main_h_box = QHBoxLayout()
        main_h_box.addSpacing(5)
        main_h_box.addWidget(self.left_disc_container)
        main_h_box.addSpacing(5)
        main_h_box.addWidget(self.right_disc_container)
        main_h_box.addSpacing(5)
        self.setLayout(main_h_box)
        self.update()

    # ****************************************

    def setLeftDiscData(self, image : Union[np.ndarray | str], result : str):
        if isinstance(image, str):
            if result == INVALID_RESULT:
                result = CosThetaConfigurator.getInstance().getNotOKCommand()
            self.left_disc_container.setTextAndResult(image, result)
        elif isinstance(image, get_args(Union[QImage, QPixmap, ndarray])):
            if result == INVALID_RESULT:
                result = CosThetaConfigurator.getInstance().getNotOKCommand()
            self.left_disc_container.setImageAndResult(image, result)
        else:
            self.left_disc_container.setDefault()
        self.update()

    def setRightDiscData(self, image : Union[np.ndarray | str], result : str):
        if isinstance(image, str):
            if result == INVALID_RESULT:
                result = CosThetaConfigurator.getInstance().getNotOKCommand()
            self.right_disc_container.setTextAndResult(image, result)
        elif isinstance(image, get_args(Union[QImage, QPixmap, ndarray])):
            if result == INVALID_RESULT:
                result = CosThetaConfigurator.getInstance().getNotOKCommand()
            self.right_disc_container.setImageAndResult(image, result)
        else:
            self.right_disc_container.setDefault()
        self.update()
    # ****************************************

    def setLeftDiscResult(self, result):
        if result == INVALID_RESULT:
            result = CosThetaConfigurator.getInstance().getNotOKCommand()
        self.left_disc_container.setImageAndResult(self.left_disc_container.starting_pixmap, result)
        self.update()

    def setRightDiscResult(self, result):
        if result == INVALID_RESULT:
            result = CosThetaConfigurator.getInstance().getNotOKCommand()
        self.right_disc_container.setImageAndResult(self.right_disc_container.starting_pixmap, result)
        self.update()
    # ****************************************

    def setLeftDiscImage(self, image):
        self.left_disc_container.setImage(image)
        self.update()

    def setRightDiscImage(self, image):
        self.right_disc_container.setImage(image)
        self.update()

    # ****************************************

    def setLeftDiscDefault(self):
        self.left_disc_container.setDefault()
        self.update()

    def setRightDiscDefault(self):
        self.right_disc_container.setDefault()
        self.update()
    # ****************************************

    def displayLeftDiscDefault(self):
        self.setLeftDiscDefault()

    def displayRightDiscDefault(self):
        self.setRightDiscDefault()

    # ****************************************

    def setDefault(self):
        self.setLeftDiscDefault()
        self.setRightDiscDefault()

    def displayDefault(self):
        self.setDefault()

