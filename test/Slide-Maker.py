import cv2
import pytesseract
from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt
import numpy as np
import os
import glob

# Directory containing scanned slide images
input_dir = "C:/Users/bagch/Downloads"  # Replace with your folder path
output_file = "output.pptx"

# Create a newwidgets PowerPoint presentation
prs = Presentation()
slide_layout = prs.slide_layouts[6]  # Blank layout

# Slide dimensions (default 10x7.5 inches in PowerPoint)
slide_width = Inches(10)
slide_height = Inches(7.5)  

# Get all image files from the directory
image_files = glob.glob(os.path.join(input_dir, "Slide *.png")) + glob.glob(os.path.join(input_dir, "Slide *.jpg"))
image_files.sort()

output_file = f"{input_dir}/{output_file}"

for image_path in image_files:
    print(f"\nProcessing {image_path}...")

    # Load the scanned slide image
    img = cv2.imread(image_path)
    if img is None:
        print(f"Failed to load {image_path}. Skipping.")
        continue

    # Convert image to grayscale and binary for processing
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    thresh = cv2.threshold(gray, 240, 255, cv2.THRESH_BINARY_INV)[1]

    # Step 1: Detect Images (non-text regions)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    image_regions = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = w * h
        if area > 5000:  # Adjust threshold as needed
            image_regions.append((x, y, w, h))
            img_crop = img[y:y + h, x:x + w]
            temp_img_path = f"temp_img_{os.path.basename(image_path)}_{x}_{y}.png"
            cv2.imwrite(temp_img_path, img_crop)

    # Step 2: Detect and OCR Text Regions
    text_data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    text_boxes = []
    for i in range(len(text_data["text"])):
        if text_data["text"][i].strip():  # Non-empty text
            x = text_data["left"][i]
            y = text_data["top"][i]
            w = text_data["width"][i]
            h = text_data["height"][i]
            text = text_data["text"][i]
            text_boxes.append((x, y, w, h, text))
            print(f"OCR’d Text: '{text}' at (x={x}, y={y}, w={w}, h={h})")

    # Step 3: Add a newwidgets slide to the presentation
    slide = prs.slides.add_slide(slide_layout)

    # Scale factors to map image coordinates to PowerPoint inches
    img_height, img_width = img.shape[:2]
    scale_x = slide_width / img_width
    scale_y = slide_height / img_height
    print(f"Image size: {img_width}x{img_height}, Scale factors: x={scale_x}, y={scale_y}")

    # Insert Images
    for (x, y, w, h) in image_regions:
        img_file = f"temp_img_{os.path.basename(image_path)}_{x}_{y}.png"
        left = x * scale_x
        top = y * scale_y
        width = w * scale_x
        height = h * scale_y
        print(f"Adding image at left={left}, top={top}, width={width}, height={height}")
        slide.shapes.add_picture(img_file, left, top, width, height)
        os.remove(img_file)

    # Insert OCR’d Text
    for (x, y, w, h, text) in text_boxes:
        left = x * scale_x
        top = y * scale_y
        width = max(w * scale_x, Inches(1))  # Ensure minimum width
        height = max(h * scale_y, Inches(0.5))  # Ensure minimum height
        # Clamp coordinates to stay within slide bounds
        left = max(Inches(0), min(left, slide_width - width))
        top = max(Inches(0), min(top, slide_height - height))
        print(f"Adding text '{text}' at left={left}, top={top}, width={width}, height={height}")

        textbox = slide.shapes.add_textbox(left, top, width, height)
        tf = textbox.text_frame
        tf.text = text
        tf.word_wrap = True  # Enable word wrap to fit text
        p = tf.paragraphs[0]
        p.font.size = Pt(14)  # Increase font size for visibility
        p.font.name = "Arial"
        p.font.color.rgb = RGBColor(0, 0, 0)  # Black text for visibility

# Save the PowerPoint file
prs.save(output_file)
print(f"Editable presentation saved as '{output_file}' with {len(image_files)} slides.")