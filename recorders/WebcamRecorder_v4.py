import sys
from pathlib import Path
import os
import cv2

from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtGui import QIcon, QPixmap, QImage
from PySide6.QtWidgets import QFileDialog, QApplication
from PySide6.QtCore import Qt, QThread, Signal, Slot, QObject, QSize
from PySide6.QtQuick import QQuickPaintedItem, QQuickView, QQuickImageProvider


class ThreadCamera(QThread):
    updateFrame = Signal(QImage)

    def __init__(self, parent=None):
        QThread.__init__(self, parent)

    def run(self):
        self.cap = cv2.VideoCapture(0)
        while self.cap.isOpened():
            ret, frame = self.cap.read()
            if not ret:
                img = QImage("./images/network.png")
                self.updateFrame.emit(img)
            color_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = QImage(color_frame.data, color_frame.shape[1], color_frame.shape[0], QImage.Format_RGB888)
            self.updateFrame.emit(img)


class ImageProvider(QQuickImageProvider):
    imageChanged = Signal(QImage)

    def __init__(self):
        super(ImageProvider, self).__init__(QQuickImageProvider.Image)

        self.cam = ThreadCamera()
        self.cam.updateFrame.connect(self.update_image)
        self.image = None

    def requestImage(self, id, size, requestedSize):
        if self.image:
            img = self.image
        else:
            img = QImage(600, 500, QImage.Format_RGBA8888)
            img.fill(Qt.GlobalColor.black)

        return img

    @Slot()
    def update_image(self, img):
        self.imageChanged.emit(img)
        self.image = img

    @Slot()
    def start(self):
        print("Starting...")
        self.cam.start()

    @Slot()
    def killThread(self):
        print("Finishing...")
        try:
            self.cam.cap.release()
            cv2.destroyAllWindows()
        except:
            pass


class MainWindow(QObject):
    def __init__(self):
        QObject.__init__(self)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon("./images/network.png"));
    engine = QQmlApplicationEngine()

    # Get Context
    main = MainWindow()
    myImageProvider = ImageProvider()
    engine.rootContext().setContextProperty("backend", main)
    engine.rootContext().setContextProperty("myImageProvider", myImageProvider)

    engine.addImageProvider("MyImageProvider", myImageProvider)

    # Load QML File
    engine.load(os.fspath(Path(__file__).resolve().parent / "test2.qml"))

    if not engine.rootObjects():
        sys.exit(-1)
    sys.exit(app.exec())