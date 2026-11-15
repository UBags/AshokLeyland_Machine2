from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QLabel
from PySide6.QtGui import QColor, Qt
from frontend.frontendutils.CosThetaImageUtils import createImage, getPixmapImage

from frontend.CosThetaMonitorDimensions import getMonitorHeight, getMonitorWidth


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