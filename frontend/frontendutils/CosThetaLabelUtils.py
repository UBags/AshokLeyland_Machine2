from PySide6.QtWidgets import QLabel

from Configuration import *
from frontend.CosThetaStylesheets import okLabelStylesheet, notokLabelStylesheet, noResultLabelStylesheet
from frontend.frontendutils.CosThetaImageUtils import *

def createResultLabel(result = "No Result", width = (1366  - 100) / 4, labelHeight = 50, forceLabelFontSizeTo : int = 0):
    # logger.debug(f"Entered createResultLabel with result={result}, width={width}, labelHeight={labelHeight}")
    fontFace = CosThetaConfigurator.getInstance().getFontFace()
    labelFontSize = CosThetaConfigurator.getInstance().getInitialFontsize()
    labelFont = QFont(fontFace, labelFontSize)
    labelFont.setWeight(QFont.Weight.Bold)
    if forceLabelFontSizeTo > 0:
        labelFont.setPointSize(forceLabelFontSizeTo)
    else:
        metrics = QFontMetrics(labelFont)
        labelTextRect = metrics.boundingRect(0, 0, 0, 0,
                                             Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextExpandTabs,
                                             result)
        textWidth = labelTextRect.width()
        textHeight = labelTextRect.height()
        reductionFactor = min(width * 1.0 / textWidth,
                              labelHeight * 1.0 / textHeight) * 0.8
        fontSize = int(reductionFactor * labelFontSize)
        labelFont.setPointSize(fontSize)
    # logger.debug(f"Resized font {fontFace} to {fontSize}")
    result_label = QLabel(result)  # Label's title
    result_label.setContentsMargins(0,0,0,0)
    result_label.setFont(labelFont)
    result_label.resize(int(width), labelHeight)
    # self.outcome_container_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    result_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    result_label.setFixedHeight(labelHeight)
    result_label.setFixedWidth(int(width))
    # printPlain(f"About to do comparison of result with value {result.lower()}")
    if result.lower() == 'ok':
        # logger.debug(f"Setting stylesheet as okLabelStylesheet")
        result_label.setStyleSheet(okLabelStylesheet.format(
            QColor(Qt.GlobalColor.white).name()))  # Set the background and foreground color of the container's label
    elif result.lower() == 'not ok':
        # logger.debug(f"Setting stylesheet as notokLabelStylesheet")
        result_label.setStyleSheet(notokLabelStylesheet.format(
            QColor(Qt.GlobalColor.white).name()))  # Set the background and foreground color of the container's label
    else:
        # logger.debug(f"Setting stylesheet as noResultStylesheet")
        result_label.setStyleSheet(noResultLabelStylesheet.format(
            QColor(Qt.GlobalColor.white).name()))  # Set the background and foreground color of the container's label
    # result_label.setFixedHeight(labelHeight)
    # result_label.setFixedWidth(width)
    # result_label.setScaledContents(True)
    # logger.debug(f"Exiting createResultLabel()")
    return result_label


def fittingPointSize(widget, texts, font=None, widthShare: float = 1.0, heightShare: float = 1.0, hMargin: int = 0,
                     vMargin: int = 0, minPointSize: int = 4, maxPointSize: int = 200) -> int:
    """The largest point size at which EVERY one of texts fits the widget as it is laid out now (CHANGES 157): each text
    measured with QFontMetrics (QFontMetrics.size - lines and the drawn width, so a Kannada, Tamil or Hindi text is
    measured as drawn, not by its number of characters) against widthShare x the widget's width less hMargin and
    heightShare x its height less vMargin. font: the face / weight to measure with (default the widget's). Call after
    the window has been shown and the layout has run (the widget has its real size)."""
    from PySide6.QtGui import QFont, QFontMetrics
    font = QFont(font if font is not None else widget.font())
    width = widget.width() * widthShare - hMargin
    height = widget.height() * heightShare - vMargin
    texts = [str(t) for t in texts if t] or [" "]

    def fits(size: int) -> bool:
        font.setPointSize(size)
        metrics = QFontMetrics(font)
        for text in texts:
            needed = metrics.size(0, text)
            if needed.width() > width or needed.height() > height:
                return False
        return True

    low, high = minPointSize, maxPointSize
    if not fits(low):
        return low
    while low < high:                       # the largest size that fits (fitting is monotonic in the size)
        middle = (low + high + 1) // 2
        if fits(middle):
            low = middle
        else:
            high = middle - 1
    return low


def largestFittingPointSize(label, text: str, hMargin: int = 16, vMargin: int = 8,
                            minPointSize: int = 6, maxPointSize: int = 200) -> int:
    """The largest point size (label's font face and weight) at which text fits the label as it is laid out now:
    its width less hMargin and its height less vMargin (the stylesheet's border and padding). Call after the window
    has been shown and the layout has run (the label has its real size). One text: fittingPointSize for several."""
    return fittingPointSize(label, [text], hMargin=hMargin, vMargin=vMargin, minPointSize=minPointSize,
                            maxPointSize=maxPointSize)
