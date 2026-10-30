import sys

from PySide6.QtGui import QImage, QPixmap
import cv2
import numpy as np
import re

from PySide6.QtWidgets import QApplication, QMainWindow, QLabel


def createImageAlternate(
        text : str ="Sample\bImage\b and Display",
        width : int = 400,
        height : int = 200,
        rgbFontColor : tuple = (0, 0, 128),
        rgbBackgroundColor : tuple = (206, 206, 206),
        forcedFontSize : int =0,
        replaceChar : list =('-', ' '),  # Default replacement for '-' to ' '
        margin : int = 20,
        returnPixMapImage : bool = False
):
    # Create background image with margins
    # rgbBackgroundColor = (206, 206, 206)
    # rgbBackgroundColor = (225, 220, 245),
    if width < 50:
        width = 50
    if height < 50:
        height = 50
    inner_width = width - 2 * margin
    inner_height = height - 2 * margin
    image = np.ones((height, width, 3), dtype=np.uint8)
    image[:] = rgbBackgroundColor[::-1]  # Convert RGB to BGR

    # Handle empty text
    text = text.strip()
    if len(text) == 0:
        if not returnPixMapImage:
            return image
        else:
            cv2_image_to_qpixmap(image)

    # Split text into lines using the new logic
    textSplit = re.split('[ -]', text)  # Split at spaces and hyphens
    for i in range(len(textSplit)):
        aWord = textSplit[i]
        newWord = aWord.replace('\b', ' ')
        newWord = newWord.replace(replaceChar[0], replaceChar[1])
        textSplit[i] = newWord
    lines = [' '.join(textSplit)]  # Join all split parts into a single line for now; adjust based on intent

    # If multi-line intent is desired, assume textSplit represents lines directly
    # For now, we'll treat textSplit as potential lines and filter out empty strings
    lines = [line for line in textSplit if line.strip()]
    n_lines = len(lines)

    font = cv2.FONT_HERSHEY_DUPLEX

    def get_text_size(text_str, font_scale):
        (text_width, text_height), baseline = cv2.getTextSize(text_str, font, font_scale, thickness=2)
        return text_width, text_height, baseline

    # Determine font scale and positioning
    if forcedFontSize == 0:
        # Optimize font scale for forcedFontSize > 0
        test_scale = 0.5
        maxWidth = 0
        totalHeight = 0
        for i, line in enumerate(textSplit):
            w, h, _ = get_text_size(line, test_scale)
            maxWidth = max(maxWidth, w)
            totalHeight += h
            if i > 0:
                totalHeight += 10

        widthRatio = inner_width * 1.0 / maxWidth
        heightRatio = inner_height * 1.0 / totalHeight
        minRatio = min(widthRatio, heightRatio)
        best_scale = test_scale * minRatio * 0.95
        font_scale = best_scale
    else:
        # Use a default reasonable font scale without optimization
        font_scale = forcedFontSize / 20.0  # Default scale, can be adjusted manually

    # Calculate dimensions for centering
    line_heights = []
    line_widths = []
    for line in lines:
        w, h, _ = get_text_size(line, font_scale)
        line_widths.append(w)
        line_heights.append(h)
    total_text_height = sum(line_heights) + (n_lines - 1) * 10
    max_line_width = max(line_widths)

    # Starting positions with margins
    start_y = margin + (inner_height - total_text_height) // 2
    y_current = start_y

    for i, line in enumerate(lines):
        w, h, baseline = get_text_size(line, font_scale)
        x = margin + (inner_width - w) // 2  # Center each line individually within available width
        y = int(y_current + h)
        cv2.putText(
            image,
            line,
            (x, y),
            font,
            font_scale,
            rgbFontColor[::-1],  # BGR
            thickness=2,
            lineType=cv2.LINE_AA
        )
        y_current += h + 10  # Move to next line

    if not returnPixMapImage:
        return image
    else:
        return cv2_image_to_qpixmap(image)

def cv2_image_to_qpixmap(cv2_image):
    """
    Convert an OpenCV image (NumPy array) to a QPixmap.

    Args:
        cv2_image (np.ndarray): OpenCV image in BGR format (height, width, 3).

    Returns:
        QPixmap: Converted QPixmap object.
    """
    # Convert BGR to RGB
    rgb_image = cv2.cvtColor(cv2_image, cv2.COLOR_BGR2RGB)

    # Get image dimensions
    height, width, channel = rgb_image.shape
    bytes_per_line = 3 * width  # 3 channels (RGB)

    # Create QImage from NumPy array
    q_image = QImage(rgb_image.data, width, height, bytes_per_line, QImage.Format.Format_RGB888)

    # Convert QImage to QPixmap
    pixmap = QPixmap.fromImage(q_image)

    return pixmap

img = createImageAlternate(
    text="Split\bPin And Washer\bCheck",
    width=500
)
cv2.imshow("Image", img)
cv2.waitKey(0)
cv2.destroyAllWindows()  # Ensure window closes properly

app = QApplication(sys.argv)
window = QMainWindow()
img = createImageAlternate(
    text="Split\bPin And Washer\bCheck",
    width=500
)
pixmap = cv2_image_to_qpixmap(img)
label = QLabel(window)
label.setPixmap(pixmap)
window.setCentralWidget(label)
window.showMaximized()
sys.exit(app.exec())