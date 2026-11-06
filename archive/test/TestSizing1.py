import sys
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QGridLayout,
                              QLabel, QFrame, QHBoxLayout, QGroupBox, QSpacerItem, QSizePolicy)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetrics, Qt, QImage, QPixmap, QColor, QAction

from frontend.SimplePopups import changePasswordForSelf, createAccount, changePassword, changeRole, activateUser, \
    inactivateUser, createProductionReportByModelName, createProductionReportByModelNameAndDateLimits, \
    createProductionReportOfTodayByModelName, createProductionReportByDateLimits, createProductionReportOfToday


class MainWindow(QMainWindow):
    def __init__(self, username : str, role : str):
        super().__init__()

        self.username = username
        self.role = role

        self.setWindowTitle("GridLayout with Different Row Heights")

        # Disable minimize and maximize buttons
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowMinMaxButtonsHint)
        self.setupUI()
        self.setupMenu()
        self.showMaximized()  # Open window fully maximized

    def setupUI(self):
        # Central widget and grid layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        grid_layout = QGridLayout(central_widget)

        # Add widgets with varying content to enforce different heights
        row_contents = [
            ("Row 1", "Short text"),
            ("Row 2", ["Part 1", "Part 2", "Part 3", "Part 4"]),
            ("Row 3", "This is a very long text to make this the tallest row with significant content."),
            ("Row 4", "Single label spanning the whole row"),
            ("Row 5", "Left text", "Right text")
        ]

        for i, (title, *text) in enumerate(row_contents):
            if i == 0:  # First row with vertical line
                # Left cell (title)
                cell_frame_left = QFrame()
                cell_frame_left.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Raised)
                cell_frame_left.setLineWidth(2)
                layout_left = QHBoxLayout(cell_frame_left)
                title_label = QLabel(title)
                title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                layout_left.addWidget(title_label)

                # Vertical line
                v_line = QFrame()
                v_line.setFrameShape(QFrame.Shape.VLine)
                v_line.setFrameShadow(QFrame.Shadow.Sunken)
                v_line.setFixedWidth(2)  # Minimum width for vertical line
                v_line.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

                # Right cell (content)
                cell_frame_right = QFrame()
                cell_frame_right.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Raised)
                cell_frame_right.setLineWidth(2)
                layout_right = QHBoxLayout(cell_frame_right)
                content_label = QLabel(text[0])
                content_label.setWordWrap(True)
                content_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                layout_right.addWidget(content_label)

                # Combine in a group box
                group_box = QGroupBox("First")
                group_box.setLayout(QHBoxLayout())
                group_box.layout().addWidget(cell_frame_left)
                group_box.layout().addWidget(v_line)
                group_box.layout().addWidget(cell_frame_right)
                grid_layout.addWidget(group_box, i, 0, 1, 2)

            elif i == 1:  # Second row with 4 labels and vertical lines
                group_box = QGroupBox("Second")
                group_box.setLayout(QHBoxLayout())
                layout = group_box.layout()

                for j, part in enumerate(text[0]):
                    cell_frame = QFrame()
                    cell_frame.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Raised)
                    cell_frame.setLineWidth(2)
                    cell_layout = QHBoxLayout(cell_frame)
                    label = QLabel(part)
                    label.setWordWrap(True)
                    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    cell_layout.addWidget(label)

                    layout.addWidget(cell_frame)

                    # Add vertical line after each label except the last one
                    if j < 3:
                        v_line = QFrame()
                        v_line.setFrameShape(QFrame.Shape.VLine)
                        v_line.setFrameShadow(QFrame.Shadow.Sunken)
                        v_line.setFixedWidth(2)  # Minimum width for vertical line
                        v_line.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
                        layout.addWidget(v_line)

                grid_layout.addWidget(group_box, i, 0, 1, 2)

            elif i == 2:  # Third row

                small_container = SmallContainer()
                grid_layout.addWidget(small_container, i, 0, 1, 2)

            elif i == 3:  # Fourth row with single label
                cell_frame = QFrame()
                cell_frame.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Raised)
                cell_frame.setLineWidth(2)
                cell_layout = QHBoxLayout(cell_frame)
                content_label = QLabel(text[0])
                content_label.setWordWrap(True)
                content_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                cell_layout.addWidget(content_label)
                grid_layout.addWidget(cell_frame, i, 0, 1, 2)  # Span the whole row

            elif i == 4:  # Fifth row with left label twice the size of right and vertical line
                group_box = QGroupBox("Fifth")
                group_box.setLayout(QHBoxLayout())
                layout = group_box.layout()

                # Left cell (twice the size)
                cell_frame_left = QFrame()
                cell_frame_left.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Raised)
                cell_frame_left.setLineWidth(2)
                layout_left = QHBoxLayout(cell_frame_left)
                left_label = QLabel(text[0])
                left_label.setWordWrap(True)
                left_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                layout_left.addWidget(left_label)
                layout.addWidget(cell_frame_left, 2)  # Stretch factor 2

                # Vertical line
                v_line = QFrame()
                v_line.setFrameShape(QFrame.Shape.VLine)
                v_line.setFrameShadow(QFrame.Shadow.Sunken)
                v_line.setFixedWidth(2)  # Minimum width for vertical line
                v_line.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
                layout.addWidget(v_line)

                # Right cell
                cell_frame_right = QFrame()
                cell_frame_right.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Raised)
                cell_frame_right.setLineWidth(2)
                layout_right = QHBoxLayout(cell_frame_right)
                right_label = QLabel(text[1])
                right_label.setWordWrap(True)
                right_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                layout_right.addWidget(right_label)
                layout.addWidget(cell_frame_right, 1)  # Stretch factor 1

                grid_layout.addWidget(group_box, i, 0, 1, 2)

        # Set different row stretch factors based on ratio 1 : 1 : 6 : 1 : 1
        grid_layout.setRowStretch(0, 1)  # Row 1
        grid_layout.setRowStretch(1, 1)  # Row 2
        grid_layout.setRowStretch(2, 6)  # Row 3 (6 times the stretch of others)
        grid_layout.setRowStretch(3, 1)  # Row 4
        grid_layout.setRowStretch(4, 1)  # Row 5

        # Add a vertical spacer to push rows and show stretch effect
        grid_layout.addItem(QSpacerItem(0, 0, QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding), 5, 0, 1, 2)


    def goToClose(self):
        return

    def goToInfo(self):
        return

    def goToHelp(self):
        return

    def manualAbort(self):
        return

    def setupMenu(self):
        """Set up menubar."""
        # Create actions for mode menu
        changePassword_act = QAction(' Change Own Password ', self)
        changePassword_act.setShortcut(' Ctrl+Alt+P ')
        changePassword_act.triggered.connect(lambda : changePasswordForSelf(currentUser=self.username, currentRole=self.role))

        exit_act = QAction(' Exit ', self)
        exit_act.setShortcut(' Ctrl+Alt+E ')
        exit_act.triggered.connect(self.goToClose)

        addnewuser_act = QAction(' Add New User ', self)
        addnewuser_act.setShortcut(' Ctrl+Alt+N ')
        addnewuser_act.triggered.connect(lambda : createAccount(currentUser=self.username, currentRole=self.role))

        manageuserpasswords_act = QAction(' Manage Other Users Passwords ', self)
        manageuserpasswords_act.setShortcut(' Ctrl+Alt+M ')
        manageuserpasswords_act.triggered.connect(lambda : changePassword(currentUser=self.username, currentRole=self.role))

        changerole_act = QAction(' Change User Role ', self)
        changerole_act.setShortcut(' Ctrl+Alt+R ')
        changerole_act.triggered.connect(lambda : changeRole(currentUser=self.username, currentRole=self.role))

        activateuser_act = QAction(' Activate an Inactive User ', self)
        activateuser_act.setShortcut(' Ctrl+Alt+C ')
        activateuser_act.triggered.connect(lambda : activateUser(currentUser=self.username, currentRole=self.role))

        inactivateuser_act = QAction(' Inactivate User ', self)
        inactivateuser_act.setShortcut(' Ctrl+Alt+I ')
        inactivateuser_act.triggered.connect(lambda : inactivateUser(currentUser=self.username, currentRole=self.role))

        createreportbymodelname_act = QAction(' Create Production Report By Model Name', self)
        createreportbymodelname_act.setShortcut(' Ctrl+Alt+S ')
        createreportbymodelname_act.triggered.connect(lambda : createProductionReportByModelName())

        createreportbymodelnameforperiod_act = QAction(' Create Production Report By Model Name For a Period', self)
        createreportbymodelnameforperiod_act.setShortcut(' Ctrl+Alt+F ')
        createreportbymodelnameforperiod_act.triggered.connect(lambda : createProductionReportByModelNameAndDateLimits())

        createreportbymodelnamefortoday_act = QAction(' Create Production Report By Model Name For Today', self)
        createreportbymodelnamefortoday_act.setShortcut(' Ctrl+Alt+A ')
        createreportbymodelnamefortoday_act.triggered.connect(lambda : createProductionReportOfTodayByModelName())

        createreportforaperiodforallmodels_act = QAction(' Create Production Report for a Period for all Models', self)
        createreportforaperiodforallmodels_act.setShortcut(' Ctrl+Alt+D ')
        createreportforaperiodforallmodels_act.triggered.connect(lambda : createProductionReportByDateLimits())

        createreportfortodayforallmodels_act = QAction(' Create Full Production Report for Today for all Models', self)
        createreportfortodayforallmodels_act.setShortcut(' Ctrl+Alt+D ')
        createreportfortodayforallmodels_act.triggered.connect(lambda : createProductionReportOfToday())

        info_act = QAction(' About ', self)
        info_act.setShortcut(' Ctrl+Alt+A ')
        info_act.triggered.connect(self.goToInfo)

        help_act = QAction(' Help ', self)
        help_act.setShortcut(' Ctrl+Alt+H ')
        help_act.triggered.connect(self.goToHelp)

        # Add Manual Abort action
        manual_abort_act = QAction(' (Z)Manual Abort ', self)
        manual_abort_act.setShortcut('Ctrl+Alt+Z')
        manual_abort_act.triggered.connect(self.manualAbort)

        # Create menubar
        menu_bar = self.menuBar()
        # # For MacOS users, places menu bar in main window
        menu_bar.setNativeMenuBar(False)
        #
        # # Create Actions menu and add actions
        action_menu = menu_bar.addMenu(' Actions ')
        action_menu.addAction(exit_act)
        action_menu.addAction(changePassword_act)
        action_menu.addSeparator()
        action_menu.addAction(manual_abort_act)

        # Create Admin menu and add actions
        admin_menu = menu_bar.addMenu(' Administration ')
        admin_menu.addAction(addnewuser_act)
        admin_menu.addAction(manageuserpasswords_act)
        admin_menu.addAction(changerole_act)

        # Create Activate/Inactivate menu and add actions
        activate_inactivate_menu = menu_bar.addMenu(' Activate / Inactivate ')
        activate_inactivate_menu.addAction(activateuser_act)
        activate_inactivate_menu.addAction(inactivateuser_act)

        # Create Report menu and add actions
        send_report_menu = menu_bar.addMenu(' Report ')
        send_report_menu.addAction(createreportbymodelname_act)
        send_report_menu.addAction(createreportbymodelnameforperiod_act)
        send_report_menu.addAction(createreportbymodelnamefortoday_act)
        send_report_menu.addSeparator()
        send_report_menu.addAction(createreportforaperiodforallmodels_act)
        send_report_menu.addAction(createreportfortodayforallmodels_act)
        send_report_menu.addSeparator()

        # Create Information menu and add actions
        info_menu = menu_bar.addMenu(' Information ')
        info_menu.addAction(help_act)
        info_menu.addAction(info_act)

        if self.role == "Operator":
            addnewuser_act.setDisabled(True)
            manageuserpasswords_act.setDisabled(True)
            changerole_act.setDisabled(True)
            activateuser_act.setDisabled(True)
            inactivateuser_act.setDisabled(True)
        # elif self.role == "Supervisor" or self.role == "Manager":
        #     addnewuser_act.setDisabled(True)
        #     manageuserpasswords_act.setDisabled(True)
        #     changerole_act.setDisabled(True)
        #     inactivateuser_act.setDisabled(True)
        #     activateuser_act.setDisabled(True)
        self.update()


class SmallContainer(QWidget):
    def __init__(self):
        super().__init__()

        layout = QGridLayout(self)
        layout.setSpacing(10)  # Set spacing between cells
        layout.setContentsMargins(10, 10, 10, 10)  # Set margins around the grid

        # Create 14 labels in 2 rows
        for i in range(2):
            for j in range(7):
                label = QLabel(f"Label {i * 7 + j + 1}")
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                label.setStyleSheet("border: 1px solid black;")  # Add border for visibility
                label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
                layout.addWidget(label, i, j)

        # Set equal row heights
        layout.setRowStretch(0, 1)
        layout.setRowStretch(1, 1)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow(username="ABCD", role="ABCD")
    sys.exit(app.exec())