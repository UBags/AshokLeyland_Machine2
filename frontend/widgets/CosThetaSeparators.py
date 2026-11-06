from PySide6.QtWidgets import QSizePolicy, QFrame
from frontend.frontendutils.CosThetaImageUtils import *

class CosThetaQHLine(QFrame):
    def __init__(self):
        super(CosThetaQHLine, self).__init__()
        self.setFixedHeight(5)
        self.setFrameShape(QFrame.Shape.HLine)
        self.setFrameShadow(QFrame.Shadow.Sunken)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

class CosThetaQVLine(QFrame):
    def __init__(self):
        super(CosThetaQVLine, self).__init__()
        self.setFixedWidth(5)
        self.setFrameShape(QFrame.Shape.VLine)
        self.setFrameShadow(QFrame.Shadow.Sunken)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
