#####################
# main_ui.py file
#####################

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtGui import QAction, QFont


class Ui_MainWindow(object):
    def setupUi(self, MainWindow):
        MainWindow.setObjectName("MainWindow")
        MainWindow.resize(997, 554)
        self.centralwidget = QtWidgets.QWidget(MainWindow)
        self.centralwidget.setObjectName("centralwidget")
        MainWindow.setCentralWidget(self.centralwidget)
        self.menubar = QtWidgets.QMenuBar(MainWindow)
        self.menubar.setGeometry(QtCore.QRect(0, 0, 997, 21))
        self.menubar.setObjectName("menubar")
        self.menuWindows = QtWidgets.QMenu(self.menubar)
        self.menuWindows.setObjectName("menuWindows")
        MainWindow.setMenuBar(self.menubar)
        self.statusbar = QtWidgets.QStatusBar(MainWindow)
        font = QtGui.QFont()
        font.setFamily("Arial")
        font.setPointSize(10)
        font.setWeight(QFont.Weight(50))
        font.setBold(False)
        self.statusbar.setFont(font)
        self.statusbar.setObjectName("statusbar")
        MainWindow.setStatusBar(self.statusbar)
        self.actionTest2 = QAction(MainWindow)
        self.actionTest2.setObjectName("actionTest2")
        self.actionTest1 = QAction(MainWindow)
        self.actionTest1.setObjectName("actionTest1")
        self.menuWindows.addAction(self.actionTest1)
        self.menuWindows.addSeparator()
        self.menuWindows.addAction(self.actionTest2)
        self.menubar.addAction(self.menuWindows.menuAction())

        self.retranslateUi(MainWindow)
        QtCore.QMetaObject.connectSlotsByName(MainWindow)

    def retranslateUi(self, MainWindow):
        MainWindow.setWindowTitle(QtWidgets.QApplication.translate("MainWindow", "Multiwindows tests", None, -1))
        self.menuWindows.setTitle(QtWidgets.QApplication.translate("MainWindow", "Windows", None, -1))
        self.actionTest2.setText(QtWidgets.QApplication.translate("MainWindow", "Test2", None, -1))
        self.actionTest1.setText(QtWidgets.QApplication.translate("MainWindow", "Test1", None, -1))






#####################
# main_logic.py file
#####################

from PySide6 import QtWidgets
from PySide6.QtCore import QPoint, QSize, QEvent, Qt

class MenuForm(QtWidgets.QMainWindow, Ui_MainWindow):
    """Main window of the program."""

    def __init__(self, windows_manager, parent=None):
        """Set the initial state of the window"""
        QtWidgets.QMainWindow.__init__(self, parent)
        self.setupUi(self)
        # Initial window size/pos.
        self.setMaximumSize(QSize(400, 300))
        self.resize(self.maximumSize())
        self.move(QPoint(300, 300))
        # Pointer to windows manager
        self.WINDOWS_HANDLER = windows_manager
        # Buttons: QMenubar/Windows
        self.actionTest1.triggered.connect(self.WINDOWS_HANDLER.open_window_test1)
        self.actionTest2.triggered.connect(self.WINDOWS_HANDLER.open_window_test2)

    def closeEvent(self, event):
        """Say to the windows handler that we closed this window"""
        self.WINDOWS_HANDLER.window_is_closed(self)
        event.accept()
        # Special: Force a close of the whole program if other windows are opened
        self.WINDOWS_HANDLER.kill_program()





#####################
# test1_ui.py file
#####################

from PySide6 import QtCore, QtGui, QtWidgets

class Ui_Test1(object):
    def setupUi(self, Test1):
        Test1.setObjectName("Test1")
        Test1.setWindowModality(Qt.NonModal)
        Test1.resize(450, 259)
        self.centralwidget = QtWidgets.QWidget(Test1)
        self.centralwidget.setObjectName("centralwidget")
        self.labelNota = QtWidgets.QLabel(self.centralwidget)
        self.labelNota.setGeometry(QtCore.QRect(30, 50, 231, 111))
        self.labelNota.setStyleSheet("color: rgb(100, 100, 100);")
        self.labelNota.setWordWrap(True)
        self.labelNota.setObjectName("labelNota")
        Test1.setCentralWidget(self.centralwidget)
        self.statusbar = QtWidgets.QStatusBar(Test1)
        self.statusbar.setStyleSheet("")
        self.statusbar.setObjectName("statusbar")
        Test1.setStatusBar(self.statusbar)

        self.retranslateUi(Test1)
        QtCore.QMetaObject.connectSlotsByName(Test1)

    def retranslateUi(self, Test1):
        Test1.setWindowTitle(QtWidgets.QApplication.translate("Test1", "Test1 window", None, -1))
        self.labelNota.setText(QtWidgets.QApplication.translate("Test1", "<html><head/><body><p><span style=\" font-weight:600;\">This is the test1 window.</span></p></body></html>", None, -1))





#####################
# test1_logic.py file
#####################

from PySide6 import QtWidgets
from PySide6.QtCore import QPoint, QSize

class Test1Form(QtWidgets.QMainWindow, Ui_Test1):
    """Main window of the program."""

    def __init__(self, windows_manager, parent=None):
        """Set the initial state of the window"""
        QtWidgets.QMainWindow.__init__(self)
        self.setupUi(self)
        # Initial window size/pos
        self.setMaximumSize(QSize(300, 300))
        self.resize(self.maximumSize())
        self.move(QPoint(400, 100))
        # Pointer to windows manager
        self.WINDOWS_HANDLER = windows_manager

    def closeEvent(self, event):
        """Say to the windows handler that we closed this window"""
        self.WINDOWS_HANDLER.window_is_closed(self)
        event.accept()





#####################
# test2_ui.py file
#####################

from PySide6 import QtCore, QtGui, QtWidgets

class Ui_Test2(object):
    def setupUi(self, Test2):
        Test2.setObjectName("Test2")
        Test2.resize(1312, 1005)
        self.centralwidget = QtWidgets.QWidget(Test2)
        self.centralwidget.setObjectName("centralwidget")
        self.labelNota = QtWidgets.QLabel(self.centralwidget)
        self.labelNota.setGeometry(QtCore.QRect(30, 60, 201, 101))
        self.labelNota.setStyleSheet("color: rgb(100, 100, 100);")
        self.labelNota.setAlignment(Qt.AlignLeading|Qt.AlignmentFlag.AlignLeft|Qt.AlignVCenter)
        self.labelNota.setWordWrap(True)
        self.labelNota.setObjectName("labelNota")
        Test2.setCentralWidget(self.centralwidget)
        self.statusbar = QtWidgets.QStatusBar(Test2)
        self.statusbar.setStyleSheet("")
        self.statusbar.setObjectName("statusbar")
        Test2.setStatusBar(self.statusbar)

        self.retranslateUi(Test2)
        QtCore.QMetaObject.connectSlotsByName(Test2)

    def retranslateUi(self, Test2):
        Test2.setWindowTitle(QtWidgets.QApplication.translate("Test2", "Test2 window", None, -1))
        self.labelNota.setText(QtWidgets.QApplication.translate("Test2", "<html><head/><body><p>This is the test2 window</p></body></html>", None, -1))





#####################
# test1_logic.py file
#####################

from PySide6 import QtWidgets
from PySide6.QtCore import QPoint, QSize

class Test2Form(QtWidgets.QMainWindow, Ui_Test2):
    """Main window of the program."""

    def __init__(self, windows_manager, parent=None):
        """Set the initial state of the window"""
        QtWidgets.QMainWindow.__init__(self, parent)
        self.setupUi(self)
        # Initial window size/pos
        self.setMaximumSize(QSize(300, 300))
        self.resize(self.maximumSize())
        self.move(QPoint(450, 100))
        # Pointer to windows manager
        self.WINDOWS_HANDLER = windows_manager

    def closeEvent(self, event):
        """Say to the windows handler that we closed this window"""
        self.WINDOWS_HANDLER.window_is_closed(self)
        event.accept()










##########################
# windows_manager.py file
##########################

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt


class WindowsHandler(object):
    """Main handler of the windows system"""

    def __init__(self):
        """Set the initial instances of the windows"""
        self.active_windows = []
        # Set first window - Menu one
        self.menu_instance = MenuForm(self)
        self.active_windows.append(self.menu_instance)
        self.menu_instance.show()

    def window_is_closed(self, window):
        """Choose what to do when a window is closed"""
        if window in self.active_windows:
            self.active_windows.remove(window)

    def handle_windows(self, window_base, app=None):
        """Check if we should add or remove an item, also log it"""
        for window in self.active_windows:
            if isinstance(window, window_base):
                self.menu_instance.statusbar.setStyleSheet("background-color: rgb(200, 0, 0);color: rgb(255, 255, 255);")
                self.menu_instance.statusbar.showMessage("That window is already opened")
                break
        else:
            if app is None:
                window_instance = window_base(self, self.menu_instance)
            else:
                window_instance = window_base(self, self.menu_instance, app)
            self.active_windows.append(window_instance)
            self.menu_instance.statusbar.setStyleSheet("background-color: rgb(0, 200, 0);color: rgb(255, 255, 255);")
            self.menu_instance.statusbar.showMessage("A newwidgets window has been opened")
            return window_instance
        return None

    # def set_window_top(self, top_window):
    #     """Set the window as top window"""
    #     print("first attempt")
    #     top_window.setWindowState(Qt.WindowMinimized | Qt.WindowActive)
    #     top_window.raise_()
    #     top_window.activateWindow()
    #     top_window.showNormal()
    #     print("second attempt")
    #     top_window.setWindowFlags(Qt.WindowStaysOnTopHint)
    #     APPLICATION.setActiveWindow(top_window)
    #     top_window.raise_()

    def open_window_test1(self):
        """Open the assigned window and add a pointer"""
        instance = self.handle_windows(Test1Form)
        if instance is not None:
            instance.show()

    def open_window_test2(self):
        """Open the assigned window and add a pointer"""
        instance = self.handle_windows(Test2Form)
        if instance is not None:
            instance.show()

    @staticmethod
    def kill_program():
        """Close all windows to trigger an exit of the program"""
        APPLICATION.closeAllWindows()

if __name__ == '__main__':
    import sys
    # App
    APPLICATION = QApplication(sys.argv)
    # APPLICATION.setAttribute(Qt.AA_DisableWindowContextHelpButton)
    # Project data
    WINDOWS_HANDLER = WindowsHandler()
    sys.exit(APPLICATION.exec_())