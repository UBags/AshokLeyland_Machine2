# from multidispatch import dispatch
import sys

from PySide6.QtWidgets import QDialog
from PySide6.QtGui import Qt

from Configuration import *
from frontend.CosThetaMonitorDimensions import getAppInstance, populateMonitorDimensions
from frontend.CosThetaStylesheets import *

app = getAppInstance()
populateMonitorDimensions() # needs to be called to ensure that a QtGui context is there for some calls to work

class CosThetaLoginDialog(QDialog):

    logger = None

    def __init__(self):
        super().__init__()

    def closeEvent(self, event):
        if event.spontaneous():
            # CosThetaLoginDialog.logger.critical("Invalid login - No uid / password provided. Shutting down the application")
            sys.exit(1)

    def keyPressEvent(self, event):
       if not event.key() == Qt.Key_Escape:
           super(CosThetaLoginDialog, self).keyPressEvent(event)

